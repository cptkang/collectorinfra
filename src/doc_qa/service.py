"""문서 질의 응답 엔진 (plans/126 §4.5 · W2).

**그래프 노드가 아니다.** 입력이 `(컬렉션 목록, 질의)` 쌍이면 동작하는 서비스 함수이고, 그 쌍을
누가 만들었는지 엔진은 모른다 — CLI(T-1)가 주든 운영자 API(T-2)가 주든 관리자 화면(T-3)이 주든,
나중에 라우터(`plans/125`)가 주든 같다. 라우팅이 붙을 때 이 함수는 바뀌지 않고 **호출부만 늘어난다.**

세부 단계는 LLM 이 정하지 않는다 — 고정 상태 기계다(`plans/121` P-4 정합):

    1 질의 확정 → 2 컬렉션 확정 → 3 인가 → 4 캐시 → 5 검색(병렬)
      → 6 후처리(문자 예산) → 7-a 서술 1회 / 7-b 근거 없음 결정적 안내
      → 8 인용 조립 → 9 진단·감사

**근거가 0건이면 LLM 을 호출하지 않는다**(7-b). "모르면 모른다고 해라"를 프롬프트로 지시하는
방식은 LLM 비결정성에 정합성을 의존하는 것이라 쓰지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from src.clients.fabrix_retrieval import (
    STATUS_BLOCKED_PII,
    STATUS_DISABLED,
    STATUS_EMPTY,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_STALE_ID,
    STATUS_TIMEOUT,
    DocHit,
    RetrievalOutcome,
    retrieve_many,
    timeouts_from_config,
)
from src.doc_qa.evidence import Citation, EvidenceSet, attach_citations, build_evidence, render_evidence_block
from src.infrastructure.doc_sources import (
    DocCollection,
    resolve_collections,
    strip_surface_prefix,
)
from src.llm import USER_RESPONSE_TAG, astream_text
from src.prompts.doc_answer import render_system, render_user

logger = logging.getLogger(__name__)

#: 실패·공백 상황의 사용자 문구. **어느 경우에도 일반 지식으로 답하지 않는다**(§4.7 · G-11).
MSG_DISABLED = (
    "문서 검색이 설정되지 않았습니다. 관리자에게 문서군 접속 정보 등록을 요청해 주세요."
)
MSG_NO_COLLECTION = (
    "조회할 문서군을 지정해 주세요. 등록된 문서군: {names}"
)
MSG_EMPTY = (
    "질문과 관련한 내용을 {names}에서 찾지 못했습니다.\n"
    "검색 서비스가 관련 근거를 판정하지 못한 결과이므로 같은 질문을 다시 물어도 같은 답입니다 — "
    "문서에 쓰인 용어·조항 번호를 그대로 넣어 질문을 바꿔 보시거나, 다른 문서군을 지정해 보세요."
)
MSG_STALE = (
    "문서 색인이 갱신되어 현재 설정으로는 조회할 수 없습니다. 관리자에게 알려 주세요"
    "(문서군 접속 정보 재등록 필요)."
)
MSG_BLOCKED = (
    "질의가 개인정보 필터에 차단되어 문서를 조회하지 못했습니다. 질문 표현을 바꿔 주세요."
)
MSG_FAILED = "문서 검색 서비스를 호출하지 못했습니다: {reason}"
MSG_PARTIAL_STALE = "문서군 {names}의 색인이 갱신되어 이번 답변에서는 제외했습니다(관리자 조치 필요)."


@dataclass
class DocAnswer:
    """엔진 산출물 — 실패도 사유를 들고 돌아온다(침묵 폴백 금지)."""

    answer: str
    citations: list[Citation] = field(default_factory=list)
    status: str = STATUS_OK
    reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _titles(collections: Sequence[DocCollection]) -> dict[str, str]:
    return {c.id: c.title for c in collections}


def _names(collections: Sequence[DocCollection]) -> str:
    return " · ".join(f"「{c.title}」" for c in collections) or "등록된 문서군"


def cache_key(collection_id: str, retrieval_id: str, query: str) -> str:
    """캐시 키 — **자산 ID를 포함**한다.

    자산 ID 가 회전하면 키도 바뀌므로 문서 갱신 뒤 낡은 본문이 재사용되지 않는다
    (`stale_id` 무효화와 별개의 2차 방어 · plans/126 §4.9).
    """
    digest = hashlib.sha1(query.strip().encode("utf-8")).hexdigest()[:16]
    return f"rag:hit:{collection_id}:{retrieval_id}:{digest}"


def _hits_to_cache(hits: Sequence[DocHit]) -> str:
    return json.dumps([h._asdict() for h in hits], ensure_ascii=False)


def _hits_from_cache(blob: str, collection_id: str) -> list[DocHit]:
    try:
        rows = json.loads(blob)
    except (TypeError, ValueError):
        return []
    out: list[DocHit] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            out.append(DocHit(**row))
        except TypeError:
            # 캐시 스키마가 바뀐 경우 — 조용히 버리고 재검색한다(형태 불일치 로그만).
            logger.info("문서 캐시 형태 불일치 — 무시하고 재검색 [%s]", collection_id)
            return []
    return out


async def _load_cached(cache: Any, collections: Sequence[DocCollection], query: str,
                       ) -> dict[str, list[DocHit]]:
    if cache is None:
        return {}
    found: dict[str, list[DocHit]] = {}
    for col in collections:
        try:
            blob = await cache.get(cache_key(col.id, col.retrieval_id, query))
        except Exception:  # noqa: BLE001 — 캐시 실패가 조회를 막지 않는다
            logger.debug("문서 캐시 조회 실패 [%s]", col.id, exc_info=True)
            continue
        if blob:
            hits = _hits_from_cache(blob, col.id)
            if hits:
                found[col.id] = hits
    return found


async def _store_cached(cache: Any, outcomes: Sequence[RetrievalOutcome],
                        collections: dict[str, DocCollection], query: str, ttl: int) -> None:
    if cache is None or ttl <= 0:
        return
    for out in outcomes:
        if out.status != STATUS_OK or not out.hits:
            continue
        col = collections.get(out.collection_id)
        if col is None:
            continue
        try:
            await cache.set(
                cache_key(col.id, col.retrieval_id, query),
                _hits_to_cache(out.hits), ttl,
            )
        except Exception:  # noqa: BLE001
            logger.debug("문서 캐시 저장 실패 [%s]", out.collection_id, exc_info=True)


def _aggregate_status(outcomes: Sequence[RetrievalOutcome]) -> tuple[str, str]:
    """전 컬렉션이 실패했을 때의 대표 status·사유.

    한쪽만 실패했으면 **살아 있는 쪽으로 답한다**(부분 성공 보존) — 이 함수는 살아 있는
    컬렉션이 없을 때만 쓰인다.
    """
    priority = [STATUS_STALE_ID, STATUS_BLOCKED_PII, STATUS_TIMEOUT, STATUS_ERROR, STATUS_DISABLED]
    for want in priority:
        picked = [o for o in outcomes if o.status == want]
        if picked:
            return want, picked[0].reason
    return STATUS_EMPTY, ""


async def _audit(
    context: dict[str, Any] | None,
    *,
    collection_ids: Sequence[str],
    result: "DocAnswer",
    hit_count: int,
    query: str,
) -> None:
    """감사 1건 — 실패해도 조회 결과를 버리지 않는다(감사 실패가 사용자 경로를 막지 않는다)."""
    if not context:
        return
    try:
        from src.security.audit_logger import log_doc_retrieval
        from src.security.pii_filter import scrub_pii

        await log_doc_retrieval(
            collection_ids=list(collection_ids),
            status=result.status,
            hit_count=hit_count,
            elapsed_ms=float(result.diagnostics.get("total_ms")
                             or result.diagnostics.get("search_ms") or 0),
            query=scrub_pii(query),
            doc_ids=[c.doc_id for c in result.citations if c.doc_id][:5],
            source=str(context.get("source") or "api"),
            reason=result.reason or None,
            user_id=context.get("user_id"),
            thread_id=context.get("thread_id"),
        )
    except Exception:  # noqa: BLE001
        logger.warning("문서 조회 감사 기록 실패", exc_info=True)


async def _answer_impl(
    query: str,
    collection_ids: Sequence[str],
    *,
    llm: Any = None,
    app_config: Any,
    cache: Any = None,
    include_raw: bool = False,
    search_only: bool = False,
    allowed_collection_ids: Sequence[str] | None = None,
    collections_path: str | None = None,
    audit_context: dict[str, Any] | None = None,
) -> DocAnswer:
    """문서군을 검색해 근거 기반 답변을 만든다.

    Args:
        query: 질의 문장. **받은 그대로** 보낸다(용어·조항 번호 보존 · §4.5 질의 규칙 1).
            머리에 붙은 컬렉션 표면어만 결정적으로 떼어낸다(규칙 3).
        collection_ids: 조회할 문서군 id 목록. **비어 있으면 오류**다 — 암묵적 전체 검색은
            암묵적 라우팅이고, 라우팅은 이 계획의 범위가 아니다(§4.6).
        llm: 서술용 LLM. `search_only=True`면 쓰지 않는다.
        app_config: `AppConfig`(`.rag` 그룹을 읽는다).
        cache: `get(key)`/`set(key, value, ttl)` 을 가진 캐시(없으면 캐시 미사용).
        include_raw: 원시 응답을 담을지(관리자 진단용).
        search_only: 검색만 하고 서술하지 않는다(진단 · LLM 호출 0).
        allowed_collection_ids: 호출자 신원의 허용 목록(None이면 인가 판정 생략).
        collections_path: 정본 경로 override(테스트용).
        audit_context: `{"user_id":…, "thread_id":…, "source":…}`. 주면 감사 로그를 남긴다
            (진입점마다 흩어지지 않게 **기록은 이 함수 한 곳**에서 한다 · D-261 정합).
    """
    started = time.monotonic()
    rag = getattr(app_config, "rag", None)
    if rag is None or not getattr(rag, "enabled", False):
        return DocAnswer(MSG_DISABLED, status=STATUS_DISABLED,
                         reason="RAG_ENABLED=false 또는 rag 설정 부재")

    all_cols = resolve_collections(rag, path=collections_path)
    registry = {c.id: c for c in all_cols}

    # 1) 질의 확정 — 표면어 접두만 제거하고 나머지는 건드리지 않는다.
    text = strip_surface_prefix(query, all_cols)
    if not text:
        return DocAnswer("질문 내용이 비어 있습니다.", status=STATUS_ERROR, reason="빈 질의")

    # 2) 컬렉션 확정 — 호출자가 명시한다(미지정은 오류).
    if not collection_ids:
        usable_names = _names([c for c in all_cols if c.usable]) or "없음"
        return DocAnswer(MSG_NO_COLLECTION.format(names=usable_names),
                         status=STATUS_ERROR, reason="컬렉션 미지정")

    unknown = [cid for cid in collection_ids if cid not in registry]
    if unknown:
        logger.warning("모르는 문서군 지정 — 무시: %s", unknown)

    picked: list[DocCollection] = []
    blocked: list[str] = []
    for cid in collection_ids:
        col = registry.get(cid)
        if col is None:
            continue
        # 3) 인가 — 민감 컬렉션은 명시 허용만(비민감은 목록이 있으면 교집합).
        if allowed_collection_ids is not None and cid not in allowed_collection_ids:
            blocked.append(cid)
            continue
        picked.append(col)

    limit = int(getattr(rag, "max_collections_per_turn", 2) or 2)
    if len(picked) > limit:
        logger.info("문서군 지정 %d개 → 상한 %d개로 절단", len(picked), limit)
        picked = picked[:limit]

    if not picked:
        reason = "인가된 문서군 없음" if blocked else "지정한 문서군이 없거나 비활성"
        detail = "; ".join(
            f"{c.id}: {c.disabled_reason}" for c in all_cols if not c.usable
        )
        return DocAnswer(MSG_DISABLED, status=STATUS_DISABLED, reason=f"{reason} ({detail})")

    unusable = [c for c in picked if not c.usable]
    usable = [c for c in picked if c.usable]
    if not usable:
        return DocAnswer(
            MSG_DISABLED, status=STATUS_DISABLED,
            reason="; ".join(f"{c.id}: {c.disabled_reason}" for c in unusable),
        )

    read_to, total_to = timeouts_from_config(rag)
    ttl = int(getattr(rag, "cache_ttl", 0) or 0)

    # 4) 캐시 조회
    cached = await _load_cached(cache if ttl > 0 else None, usable, text)
    to_search = [c for c in usable if c.id not in cached]

    # 5) 검색(병렬 · 컬렉션마다 독립 처리로 부분 반환 보장)
    outcomes: list[RetrievalOutcome] = []
    if to_search:
        outcomes = await retrieve_many(
            to_search, text, timeout_sec=read_to,
            total_timeout_sec=total_to, include_raw=include_raw,
        )
        await _store_cached(cache if ttl > 0 else None, outcomes,
                            {c.id: c for c in usable}, text, ttl)
    for cid, hits in cached.items():
        outcomes.append(RetrievalOutcome(cid, STATUS_OK, hits=list(hits),
                                         reason="캐시 재사용"))

    # 6) 후처리 — 문자 예산만(점수·건수 절단 없음)
    hits: list[DocHit] = [h for o in outcomes for h in o.hits]
    titles = _titles(all_cols)
    evidence = build_evidence(
        hits,
        collection_titles=titles,
        max_doc_chars=int(getattr(rag, "max_doc_chars", 4000) or 0),
        max_context_chars=int(getattr(rag, "max_context_chars", 24000) or 0),
        doc_url_base=str(getattr(rag, "doc_url_base", "") or ""),
    )

    stale = [o.collection_id for o in outcomes if o.status == STATUS_STALE_ID]
    notes: list[str] = []
    if stale:
        notes.append(MSG_PARTIAL_STALE.format(
            names=" · ".join(f"「{titles.get(c, c)}」" for c in stale)))

    diagnostics = {
        "counts": {o.collection_id: len(o.hits) for o in outcomes},
        "statuses": {o.collection_id: o.status for o in outcomes},
        "score_min": min((h.rank_score for h in hits), default=None),
        "score_max": max((h.rank_score for h in hits), default=None),
        "hyde_applied": any(
            o.executed_query and o.executed_query != text for o in outcomes
        ),
        "truncated_docs": evidence.truncated_docs,
        "dropped_docs": evidence.dropped_docs,
        "cached_collections": sorted(cached),
        "search_ms": int((time.monotonic() - started) * 1000),
    }
    raw = None
    if include_raw:
        raw = {o.collection_id: o.raw for o in outcomes if o.raw is not None}

    # 7-b) 근거 없음 — **LLM 호출 없이** 결정적 안내
    if evidence.is_empty:
        status, reason = _aggregate_status(outcomes)
        if status == STATUS_STALE_ID:
            answer = MSG_STALE
        elif status == STATUS_BLOCKED_PII:
            answer = MSG_BLOCKED
        elif status in (STATUS_TIMEOUT, STATUS_ERROR):
            answer = MSG_FAILED.format(reason=reason or "사유 미상")
        elif status == STATUS_DISABLED:
            answer = MSG_DISABLED
        else:
            status = STATUS_EMPTY
            answer = MSG_EMPTY.format(names=_names(usable))
        diagnostics["llm_calls"] = 0
        logger.info("문서 질의 종료(근거 0건) status=%s · LLM 호출 0회", status)
        return DocAnswer(answer, status=status, reason=reason,
                         diagnostics=diagnostics, raw=raw)

    # 7-a) 서술 — LLM 1회(검색 전용 모드면 0회)
    block = render_evidence_block(evidence, collection_titles=titles)
    if search_only or llm is None:
        diagnostics["llm_calls"] = 0
        summary = "\n".join(
            f"- {c.collection_title} — {c.title}"
            + (f" `{c.subtitle}`" if c.subtitle else "")
            for c in evidence.citations
        )
        return DocAnswer(
            f"검색만 수행했습니다(서술 생략). 찾은 근거 {len(evidence.hits)}건:\n{summary}",
            citations=list(evidence.citations), status=STATUS_OK,
            diagnostics=diagnostics, raw=raw,
        )

    max_chars = int(getattr(rag, "answer_max_chars", 1200) or 1200)
    messages = [
        SystemMessage(content=render_system(max_chars=max_chars)),
        HumanMessage(content=render_user(query=text, evidence=block)),
    ]
    try:
        narrative = await astream_text(llm, messages, tags=[USER_RESPONSE_TAG])
        diagnostics["llm_calls"] = 1
    except Exception as exc:  # noqa: BLE001 — 서술 실패도 근거는 전달한다(D-236)
        logger.exception("문서 근거 서술 실패 — 근거 목록만 반환")
        diagnostics["llm_calls"] = 1
        diagnostics["llm_error"] = type(exc).__name__
        listing = "\n".join(
            f"- {c.collection_title} — {c.title}" for c in evidence.citations
        )
        return DocAnswer(
            f"답변 생성에 실패했습니다({type(exc).__name__}). 찾은 근거는 다음과 같습니다:\n{listing}",
            citations=list(evidence.citations), status=STATUS_ERROR,
            reason=f"서술 LLM 실패: {type(exc).__name__}",
            diagnostics=diagnostics, raw=raw,
        )

    # 8) 인용 조립 — 코드가 결정적으로 붙인다
    answer = attach_citations(narrative, evidence, note_lines=notes)
    diagnostics["answer_chars"] = len(narrative)
    diagnostics["total_ms"] = int((time.monotonic() - started) * 1000)
    logger.info(
        "문서 질의 완료 근거=%d건 서술=%d자 절단=%d 제외=%d %dms",
        len(evidence.hits), len(narrative), evidence.truncated_docs,
        evidence.dropped_docs, diagnostics["total_ms"],
    )
    return DocAnswer(answer, citations=list(evidence.citations), status=STATUS_OK,
                     diagnostics=diagnostics, raw=raw)


async def answer_from_documents(
    query: str,
    collection_ids: Sequence[str],
    *,
    llm: Any = None,
    app_config: Any,
    cache: Any = None,
    include_raw: bool = False,
    search_only: bool = False,
    allowed_collection_ids: Sequence[str] | None = None,
    collections_path: str | None = None,
    audit_context: dict[str, Any] | None = None,
) -> DocAnswer:
    """`_answer_impl` 을 감싸 **감사 기록을 한 곳에서** 남긴다(plans/126 W5).

    반환 경로가 여러 개(0건·폐기·타임아웃·검색 전용·정상·서술 실패)라 각 지점에 기록을 흩으면
    한 경로가 빠진다. 래퍼로 모아 두면 «감사에 안 남는 조회»가 구조적으로 생기지 않는다.
    """
    result = await _answer_impl(
        query, collection_ids, llm=llm, app_config=app_config, cache=cache,
        include_raw=include_raw, search_only=search_only,
        allowed_collection_ids=allowed_collection_ids,
        collections_path=collections_path,
    )
    counts = result.diagnostics.get("counts") or {}
    hit_count = sum(int(v) for v in counts.values()) if counts else len(result.citations)
    await _audit(
        audit_context, collection_ids=collection_ids, result=result,
        hit_count=hit_count, query=query,
    )
    return result


__all__ = [
    "DocAnswer",
    "answer_from_documents",
    "cache_key",
    "EvidenceSet",
]
