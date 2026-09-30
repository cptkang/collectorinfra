"""FabriX Retrieval Connector 클라이언트 (plans/126 §4.2 · W1).

사내 GenAI 플랫폼의 리트리벌 엔드포인트를 호출한다. **읽기 전용 HTTP POST**이고 요청 바디는
`retrieval_id`·`query` 둘뿐이다 — 검색 파라미터(HyDE·top_k·threshold·rerank)는 플랫폼 전속이라
우리가 보낼 것도, 되돌아온 결과를 다시 거를 것도 없다(plans/126 §3.3).

이 모듈이 지키는 계약:
  1. **자격증명은 호출 인자다.** 엔드포인트·토큰·클라이언트 키가 리트리벌 발급마다 다르므로
     (§3.1) 전역 단일 자격증명 가정이 성립하지 않는다. URL은 **받은 문자열 그대로** 쓴다
     (실측 2건이 호스트 `trnn`/`trrn`·세그먼트 `bk0`/`kb0`에서 이미 다르다 — 조립 금지).
  2. **판정 순서 고정**: ①오류 봉투(`error`·`errorCode` · HTTP 상태와 무관) → ②`results` 파싱
     → ③빈 배열이면 `empty`. `results`를 먼저 보면 오류 봉투가 "0건"으로 오독되는데, 그 오독이
     가장 비싸다(관리자 조치가 필요한 상황을 "문서에 없음"으로 사용자에게 확정 통보한다).
  3. **자산 폐기(`stale_id`)는 1급 처분**이다 — 재시도해도 회복되지 않으므로 재시도 금지.
  4. **이중 타임아웃**: httpx read 상한 + `asyncio.wait_for` 벽시계 총상한(D-198 교훈 —
     read 상한만으로는 무한대기를 막지 못했다).
  5. **본문을 로그에 싣지 않는다.** 토큰은 마스킹, 결과는 식별자·점수·길이만.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple, Sequence

import httpx

from src.infrastructure.doc_sources import DocCollection
from src.security.pii_filter import is_filter_blocked

logger = logging.getLogger(__name__)

# ── status 어휘 (plans/126 §4.2 · docs/32 §6 판독표와 1:1) ────────────────
STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_STALE_ID = "stale_id"
STATUS_BLOCKED_PII = "blocked_pii"
STATUS_ERROR = "error"
STATUS_TIMEOUT = "timeout"
STATUS_DISABLED = "disabled"

#: 자산 폐기 판정 — `errorCode`만으로 단정하지 않는다(`NoContent`는 다른 사유에도 쓰일 수 있다).
#: 메시지 패턴과 **함께** 충족해야 `stale_id`이고, 어긋나면 generic error로 낮춘다(보수적).
_STALE_ERROR_CODES = frozenset({"nocontent"})
_STALE_MESSAGE_MARK = "retrieval does not exist"


class DocHit(NamedTuple):
    """검색 결과 1건 — 답변 근거의 단위."""

    collection_id: str
    doc_id: str
    title: str
    filename: str
    subtitle: str
    content: str
    rank: int
    rank_score: float
    url: str
    content_type: str
    catalog_id: str
    result_id: str

    @property
    def display_title(self) -> str:
        return self.title or self.filename or self.doc_id or "(제목 없음)"


@dataclass
class RetrievalOutcome:
    """컬렉션 1개에 대한 호출 결과. 실패도 사유를 들고 돌아온다(침묵 폴백 금지)."""

    collection_id: str
    status: str
    reason: str = ""
    hits: list[DocHit] = field(default_factory=list)
    executed_query: str = ""
    hyde_query: str = ""
    elapsed_ms: int = 0
    raw: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _as_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _as_int(raw: Any, default: int = 0) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _error_envelope(body: Mapping[str, Any]) -> tuple[str, str] | None:
    """오류 봉투면 `(status, reason)`, 아니면 None. **HTTP 상태를 보지 않는다.**

    게이트웨이가 200 + 오류 본문으로 답할 수 있어(M-10 미확정) 본문만으로 판정한다.
    """
    if not ({"error", "errorCode"} & set(body)):
        return None
    code = str(body.get("errorCode") or "").strip()
    message = str(body.get("message") or "").strip()
    err = str(body.get("error") or "").strip()
    if code.lower() in _STALE_ERROR_CODES and _STALE_MESSAGE_MARK in message.lower():
        return STATUS_STALE_ID, (
            "문서 색인이 갱신되어 현재 설정으로는 조회할 수 없습니다"
            "(자산 ID 폐기 — 관리자 교체 필요 · docs/32 §3)"
        )
    detail = " / ".join(x for x in (code, err, message) if x) or "사유 미상"
    return STATUS_ERROR, f"검색 서비스가 오류를 반환했습니다: {detail}"


def parse_results(collection_id: str, body: Mapping[str, Any]) -> list[DocHit]:
    """`results`를 방어적으로 파싱한다.

    `results`가 없거나 항목이 dict가 아니면 건너뛰고 건수를 로그에 남긴다 — FabriX SSE에서
    `data:null`이 JSON 파싱을 통과해 `None`이 흘러 터진 실측과 같은 형태의 결함을 막는다.
    `has_permission=false`는 **무조건 제외**한다(권한 없는 내용이 답변에 섞이면 사고다).
    """
    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        if raw_results is not None:
            logger.warning(
                "[%s] results가 배열이 아님(type=%s) — 0건 처리",
                collection_id, type(raw_results).__name__,
            )
        return []

    hits: list[DocHit] = []
    skipped_shape = 0
    skipped_permission = 0
    for item in raw_results:
        if not isinstance(item, Mapping):
            skipped_shape += 1
            continue
        if item.get("has_permission") is False:
            skipped_permission += 1
            continue
        info = item.get("content_info")
        subtitle = ""
        if isinstance(info, Mapping):
            subtitle = str(info.get("subtitle") or "").strip()
        hits.append(DocHit(
            collection_id=collection_id,
            doc_id=str(item.get("doc_id") or "").strip(),
            title=str(item.get("title") or "").strip(),
            filename=str(item.get("filename") or "").strip(),
            subtitle=subtitle,
            content=str(item.get("content") or ""),
            rank=_as_int(item.get("rank")),
            rank_score=_as_float(item.get("rank_score")),
            url=str(item.get("url") or "").strip(),
            content_type=str(item.get("content_type") or "").strip(),
            catalog_id=str(item.get("catalog_id") or "").strip(),
            result_id=str(item.get("id") or "").strip(),
        ))
    if skipped_shape or skipped_permission:
        logger.info(
            "[%s] 결과 제외 — shape 불일치 %d건 · 권한 없음 %d건",
            collection_id, skipped_shape, skipped_permission,
        )
    return hits


def interpret_body(collection_id: str, body: Any) -> RetrievalOutcome:
    """응답 본문 하나를 `RetrievalOutcome`으로 해석한다(판정 순서 계약 §4.2 4).

    순수 함수다 — 네트워크를 타지 않으므로 단위 테스트가 이 함수로 계약을 고정한다.
    """
    if not isinstance(body, Mapping):
        return RetrievalOutcome(
            collection_id, STATUS_ERROR,
            reason=f"응답이 JSON 객체가 아닙니다(type={type(body).__name__})",
        )

    # ① 오류 봉투 우선 — HTTP 상태와 무관하게 본문으로 판정한다.
    envelope = _error_envelope(body)
    if envelope is not None:
        status, reason = envelope
        return RetrievalOutcome(collection_id, status, reason=reason)

    # ①-b PII 필터 차단(게이트웨이 공통 형태) — 사유를 구조화해 노출한다.
    if is_filter_blocked(dict(body)):
        return RetrievalOutcome(
            collection_id, STATUS_BLOCKED_PII,
            reason="질의가 개인정보 필터에 차단되었습니다(질문 표현을 바꿔 주세요)",
        )

    # ② results 파싱 → ③ 빈 배열이면 empty
    hits = parse_results(collection_id, body)
    # `executed_query`는 결과 항목에 실린다(HyDE on이면 가상 답변이 실제 검색 질의다).
    executed = ""
    raw_results = body.get("results")
    if isinstance(raw_results, list):
        for item in raw_results:
            if isinstance(item, Mapping) and item.get("executed_query"):
                executed = str(item["executed_query"])
                break
    outcome = RetrievalOutcome(
        collection_id,
        STATUS_OK if hits else STATUS_EMPTY,
        reason="" if hits else "검색 결과가 0건입니다(플랫폼 임계 기준 관련 근거 없음)",
        hits=hits,
        executed_query=executed,
        hyde_query=str(body.get("hyde_query") or ""),
    )
    return outcome


def timeouts_from_config(rag_config: Any) -> tuple[float, float]:
    """설정에서 `(read 상한, 벽시계 총상한)`을 읽는다 — 둘을 함께 쓰는 것이 계약이다(§4.2 4).

    호출부(엔진·CLI·API)가 같은 값을 쓰도록 한 곳에서 읽는다.
    """
    read = float(getattr(rag_config, "timeout", 12) or 12)
    total = float(getattr(rag_config, "total_timeout", 20) or 20)
    if total < read:
        logger.warning(
            "RAG_TOTAL_TIMEOUT(%.0f)이 RAG_TIMEOUT(%.0f)보다 작습니다 — 총상한을 읽기 상한으로 올립니다",
            total, read,
        )
        total = read
    return read, total


async def retrieve_one(
    collection: DocCollection,
    query: str,
    *,
    timeout_sec: float = 12.0,
    total_timeout_sec: float = 20.0,
    include_raw: bool = False,
    client: httpx.AsyncClient | None = None,
) -> RetrievalOutcome:
    """컬렉션 1개를 조회한다. **재시도 없음**(§4.2 1).

    Args:
        collection: 접속 4종 세트를 담은 컬렉션(정본 의미 + 설정 접속).
        query: 보낼 질의 문장 — 호출부가 확정한 그대로 보낸다(용어 보존 · §4.5 질의 규칙).
        timeout_sec: httpx read 상한.
        total_timeout_sec: 벽시계 총상한(하트비트로 read 상한이 무력화되는 경우 대비 · D-198).
        include_raw: True면 원시 응답을 `raw`에 담는다(관리자 진단용).
        client: 재사용할 AsyncClient(없으면 호출마다 생성).
    """
    if not collection.usable:
        return RetrievalOutcome(
            collection.id, STATUS_DISABLED,
            reason=collection.disabled_reason or "컬렉션이 비활성입니다",
        )

    payload = {"retrieval_id": collection.retrieval_id, "query": query}
    headers = {
        "x-openapi-token": collection.token,
        "x-generative-ai-client": collection.client_key,
        "Content-Type": "application/json",
    }
    started = time.monotonic()

    async def _call() -> RetrievalOutcome:
        own_client = client is None
        http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_sec, connect=min(5.0, timeout_sec)),
            verify=False,  # 사내망 http/자체 서명 — 폴스타 REST 경로와 같은 규약
        )
        try:
            resp = await http.post(collection.endpoint, json=payload, headers=headers)
            try:
                body = resp.json()
            except ValueError:
                return RetrievalOutcome(
                    collection.id, STATUS_ERROR,
                    reason=f"응답을 JSON으로 읽지 못했습니다(HTTP {resp.status_code})",
                )
            out = interpret_body(collection.id, body)
            # HTTP 상태가 실패인데 본문이 정상 형태면 사유에 상태를 병기한다(진단용).
            if resp.status_code >= 400 and out.status in (STATUS_OK, STATUS_EMPTY):
                return RetrievalOutcome(
                    collection.id, STATUS_ERROR,
                    reason=f"HTTP {resp.status_code} 응답입니다(본문은 오류 형태가 아님)",
                    raw=dict(body) if include_raw else None,
                )
            if include_raw and isinstance(body, Mapping):
                out.raw = dict(body)
            return out
        finally:
            if own_client:
                await http.aclose()

    try:
        outcome = await asyncio.wait_for(_call(), timeout=total_timeout_sec)
    except asyncio.TimeoutError:
        outcome = RetrievalOutcome(
            collection.id, STATUS_TIMEOUT,
            reason=f"검색 서비스 응답이 없습니다(총상한 {total_timeout_sec:.0f}초 초과)",
        )
    except httpx.TimeoutException:
        outcome = RetrievalOutcome(
            collection.id, STATUS_TIMEOUT,
            reason=f"검색 서비스 응답이 없습니다(읽기 상한 {timeout_sec:.0f}초 초과)",
        )
    except httpx.HTTPError as exc:
        outcome = RetrievalOutcome(
            collection.id, STATUS_ERROR,
            reason=f"검색 서비스에 연결하지 못했습니다: {type(exc).__name__}",
        )

    outcome.elapsed_ms = int((time.monotonic() - started) * 1000)
    scores = [h.rank_score for h in outcome.hits]
    logger.info(
        "문서 검색 [%s] status=%s 건수=%d 점수=%s~%s hyde=%s %dms token=%s",
        collection.id, outcome.status, len(outcome.hits),
        f"{min(scores):.3f}" if scores else "-",
        f"{max(scores):.3f}" if scores else "-",
        "y" if outcome.executed_query and outcome.executed_query != query else "n",
        outcome.elapsed_ms, collection.masked_connection()["token"] or "-",
    )
    if outcome.status not in (STATUS_OK, STATUS_EMPTY):
        logger.warning("문서 검색 [%s] 실패 사유: %s", collection.id, outcome.reason)
    return outcome


async def retrieve_many(
    collections: Sequence[DocCollection],
    query: str,
    *,
    timeout_sec: float = 12.0,
    total_timeout_sec: float = 20.0,
    include_raw: bool = False,
) -> list[RetrievalOutcome]:
    """여러 컬렉션을 병렬 조회한다 — 컬렉션마다 독립 처리해 **부분 반환을 보장**한다.

    한 try 블록에 묶으면 하나의 실패가 전부를 날린다(Known Mistakes: 독립 신호 수집은
    개별 try/except).
    """
    if not collections:
        return []

    async def _safe(col: DocCollection) -> RetrievalOutcome:
        try:
            return await retrieve_one(
                col, query, timeout_sec=timeout_sec,
                total_timeout_sec=total_timeout_sec, include_raw=include_raw,
            )
        except Exception as exc:  # noqa: BLE001 — 한 컬렉션 실패가 전체를 막지 않게
            logger.exception("문서 검색 [%s] 예기치 않은 실패", col.id)
            return RetrievalOutcome(
                col.id, STATUS_ERROR,
                reason=f"예기치 않은 오류: {type(exc).__name__}",
            )

    return list(await asyncio.gather(*(_safe(c) for c in collections)))
