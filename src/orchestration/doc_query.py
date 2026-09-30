"""사내 문서(규정·설계 근거) 조건부 처리기 `doc_query` — plans/127 W2 · `plans/125` 계약 위.

`plans/126` 문서 엔진(`answer_from_documents` — 입력 `(문서군, 질의)`)을 2단 기준 경로가 부르게 하는
**호출부**다. 엔진은 바꾸지 않는다(126 §4.5 3 · 부록 D-0).

- **활성일 때만 등록**(125 D-283 ② 방식): `RAG_ENABLED` ∧ `RAG_CHAT_ROUTING_ENABLED` ∧ 보기로 오를
  문서군(정본 `enabled` ∧ 비민감) ≥ 1(`routing_active`). 비활성 배포는 처리기도 분해 프롬프트 줄도
  없다 — 바이트 불변. 고정 처리기 목록(`SUBAGENT_REGISTRY`)에는 넣지 않는다.
- **보기 = 문서군**: 분해 LLM 이 `views`(`doc.<문서군 id>` 닫힌 어휘)를 고른다 — 추가 LLM 0
  (D-004 — 구조화 출력). 보기는 `config/rag_collections.yaml`에서 파생한다(사본 금지 · D-131).
  비면 보기에 오른 문서군 전부(상한 `RAG_MAX_COLLECTIONS_PER_TURN`)를 찾고 그 사실을 고지한다
  (plans/127 G-6 (a)).
- **인가 2층**: 소스 축 `allowed_sources`(125 A-7 · plans/127 G-8 (a)) → 문서군 축(민감 · 126
  `allowed_collection_ids`). 거부 문구에 소스·문서군 이름을 싣지 않는다(D-264 ②).
- **폴백 없음**: 검색 0건·실패를 일반 LLM 답(`general_inference`)으로 넘기지 않는다 — 사내 규정을
  일반지식으로 답하는 것이 가장 피해야 할 동작이다(126 R-1). 엔진이 사유 문구를 들고 돌아온다.
- **간선 없음**: 문서 답은 다른 task 의 입력이 되지 않는다(`key_facets_out=()` — 분해 출구가
  문서 task 의 의존을 끊는다 · plans/127 G-10).

계층: orchestration.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig
from src.doc_qa.authz import allowed_collection_ids
from src.doc_qa.service import answer_from_documents
from src.infrastructure.doc_sources import (
    DocCollection,
    resolve_collections,
    routing_active,
    routing_collections,
)
from src.orchestration.db_access import access_denied_result
from src.orchestration.subagents import SubAgentSpec
from src.routing.db_authz import SOURCE_ACCESS_DENIED_MESSAGE, is_source_allowed
from src.routing.registry import get_registry

logger = logging.getLogger(__name__)

#: 처리기 이름(분해 어휘 — 활성일 때만 렌더) · 시스템 코드(레지스트리 `solutions[doc]`).
DOC_QUERY_AGENT = "doc_query"
DOC_SYSTEM = "doc"
#: 보기 id 접두 — 보기 id = `doc.<문서군 id>`.
VIEW_PREFIX = "doc."
#: 거부·실패 사유 키(apm_query·host_inspect 와 같은 이름).
DEGRADED_KEY = "degraded_reason"
#: 결과 메타(계획 요약·후속 조합이 읽는다).
META_KEY = "doc_query"
#: 엔진 status 중 실패(사용자에게는 엔진의 사유 문구가 그대로 간다).
#: `empty`(근거 없음)는 정상 답이다.
_FAILURE_STATUSES = frozenset({"stale_id", "blocked_pii", "error", "timeout", "disabled"})


# ── 활성 · 어휘 ──────────────────────────────────────────────────────────────

def _rag(app_config: Any) -> Any:
    return getattr(app_config, "rag", None)


def doc_active(app_config: Any) -> bool:
    """문서 채팅 라우팅이 활성인가(설정 대역·설정 없음은 비활성)."""
    try:
        return routing_active(_rag(app_config))
    except Exception as e:  # noqa: BLE001 — 정본 파일 오류는 비활성 + 사유(침묵 금지)
        logger.warning(
            "doc_query 비활성 — 문서 정본을 읽지 못했습니다: %s: %s", type(e).__name__, e,
        )
        return False


def doc_views(app_config: Any) -> tuple[DocCollection, ...]:
    """보기에 오른 문서군(선언 순서) — 비활성이면 빈 튜플."""
    return routing_collections(_rag(app_config)) if doc_active(app_config) else ()


def view_id(collection_id: str) -> str:
    return f"{VIEW_PREFIX}{collection_id}"


def max_views(app_config: Any) -> int:
    """task 하나가 싣는 문서군 상한(`RAG_MAX_COLLECTIONS_PER_TURN` · 설정이 이상하면 2)."""
    raw = getattr(_rag(app_config), "max_collections_per_turn", 2)
    return raw if isinstance(raw, int) and raw >= 1 else 2


def sanitize_views(raw: Any, app_config: Any) -> list[str]:
    """LLM 이 낸 보기 목록 → 닫힌 어휘만(순서 유지 · 중복 제거 · 상한)."""
    items = [raw] if isinstance(raw, str) else (raw if isinstance(raw, (list, tuple)) else [])
    known = {view_id(c.id) for c in doc_views(app_config)}
    kept: list[str] = []
    for item in items:
        code = str(item).strip() if isinstance(item, str) else ""
        if code in known and code not in kept:
            kept.append(code)
    return kept[:max_views(app_config)]


def render_agent_line() -> str:
    """분해 프롬프트의 담당 목록 한 줄(활성일 때만 삽입)."""
    return f"- **{DOC_QUERY_AGENT}**: {DOC_QUERY_SPEC.purpose}"


def render_view_rows(app_config: Any) -> str:
    """분해 프롬프트의 문서군 표 — 정본 YAML 파생(사본 금지 · D-131)."""
    lines = []
    for c in doc_views(app_config):
        desc = " ".join(c.description.split())
        tail = f" ({c.sibling_note})" if c.sibling_note else ""
        lines.append(f"- `{view_id(c.id)}`: {c.title} — {desc}{tail}")
    return "\n".join(lines)


def example_view(app_config: Any) -> str:
    """분해 절 예시에 쓸 보기 id(첫 문서군)."""
    views = doc_views(app_config)
    return view_id(views[0].id) if views else ""


# ── 처리기 ────────────────────────────────────────────────────────────────────

def _status(label: str, status: str, rows: int, reason: str = "") -> dict[str, Any]:
    return {"system": DOC_SYSTEM, "label": label, "status": status, "rows": rows, "reason": reason}


async def run_doc_query(
    task: dict[str, Any],
    isolated: dict[str, Any],
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict[str, Any]:
    """문서 엔진을 부른다(handler 규약 · LLM 은 엔진의 서술 1회뿐 — 근거 0건이면 0회).

    Returns:
        텍스트 결과 `{final_response, doc_query, source_status}` — 실패면
        `error`·`degraded_reason`을 더한다(사유 문구는 엔진이 만든다). 소스 권한 밖이면
        `access_denied` 결과.
    """
    # 소스 축 인가(plans/125 A-7 · plans/127 G-8 (a)) — 실행 경계에서 판정한다(2단 오케스트레이터 ·
    # 3단 계획 루프가 같은 처리기를 부른다). 거부 문구·결과에는 소스 이름을 싣지 않는다(D-264 ②).
    role = isolated.get("user_role")
    if not is_source_allowed(DOC_SYSTEM, isolated.get("allowed_sources"), role):
        logger.info("%s 인가 거부: 문서 소스 권한 없음(역할=%s)", DOC_QUERY_AGENT, role)
        return access_denied_result(SOURCE_ACCESS_DENIED_MESSAGE)

    rag = _rag(app_config)
    label = get_registry().system_label(DOC_SYSTEM)
    candidates = doc_views(app_config)
    views = sanitize_views(task.get("views"), app_config)
    note = ""
    if views:
        chosen = [c for c in candidates if view_id(c.id) in views]
    else:
        # G-6 (a) — 문서군을 고르지 않았으면 보기 전부(상한)를 찾고 그 사실을 코드가 고지한다
        chosen = list(candidates[:max_views(app_config)])
        if chosen:
            note = ("문서군을 지정하지 않아 등록 문서군 전체("
                    + " · ".join(c.title for c in chosen) + ")에서 찾았습니다.")

    # 문서군 축 인가(민감 · 126 G-2) — 격리 입력의 신원 키를 문서 인가가 읽는 키로 옮긴다
    user = {"role": role, "sub": isolated.get("user_id")}
    permitted = allowed_collection_ids(resolve_collections(rag), user=user, rag_config=rag)
    targets = [c.id for c in chosen if c.id in set(permitted)]
    query = str(task.get("sub_query") or isolated.get("user_query") or "")

    result = await answer_from_documents(
        query, targets, llm=llm, app_config=app_config,
        allowed_collection_ids=sorted(permitted),
        audit_context={
            "user_id": isolated.get("user_id"),
            "thread_id": isolated.get("thread_id"),
            "source": "chat",
        },
    )
    logger.info(
        "%s status=%s 문서군=%s 근거=%d건", DOC_QUERY_AGENT, result.status, targets,
        len(result.citations),
    )
    meta = {
        "views": [view_id(t) for t in targets],
        "status": result.status,
        "citations": len(result.citations),
        "reason": result.reason,
        "all_collections": not views,
    }
    out: dict[str, Any] = {
        "final_response": f"_{note}_\n\n{result.answer}" if note else result.answer,
        META_KEY: meta,
        "source_status": [_status(label, result.status, len(result.citations), result.reason)],
    }
    if result.status in _FAILURE_STATUSES:
        out["error"] = result.reason or result.answer
        out[DEGRADED_KEY] = f"doc_{result.status}"
    return out


DOC_QUERY_SPEC = SubAgentSpec(
    DOC_QUERY_AGENT,
    "사내 규정·절차·설계 문서의 내용 질의(문서 근거·출처로 답한다)",
    run_doc_query,
    purpose=(
        "사내 **규정·절차·설계 문서의 내용** 질의 — 문서 근거와 출처로 답한다"
        "(특정 서버의 현재 수치·목록·발생 알람이 아님)"
    ),
    backend="rest",  # FabriX Retrieval Connector(httpx POST — 엔진 클라이언트)
    output_type="text",
    key_facets_out=(),  # 간선 없음 — 다른 task 의 입력이 되지 않는다(plans/127 §4.7)
    # 대상 상한은 설정값(`RAG_MAX_COLLECTIONS_PER_TURN`) · 시간 상한은 엔진 설정(`RAG_TIMEOUT`·
    # `RAG_TOTAL_TIMEOUT`) + 턴 마감(126 §4.2 3) — 네 번째 시계를 만들지 않는다
    prerequisites=("rag_routing_active",),
)


def active_doc_subagents(app_config: Any) -> dict[str, SubAgentSpec]:
    """활성이면 `{doc_query}` · 아니면 빈 dict."""
    return {DOC_QUERY_AGENT: DOC_QUERY_SPEC} if doc_active(app_config) else {}


__all__ = [
    "DOC_QUERY_AGENT",
    "DOC_QUERY_SPEC",
    "DOC_SYSTEM",
    "active_doc_subagents",
    "doc_active",
    "doc_views",
    "run_doc_query",
    "sanitize_views",
]
