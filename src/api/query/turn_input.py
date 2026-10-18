"""턴 입력 조립 보조 — 체크포인트 상태·승인 해소·존 답변 재사용·소스 선택 메타.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.api.schemas import QueryRequest
from src.pipeline.turn.approval import _is_legacy_structure_hitl_thread, resolve_approval_action

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


async def _get_checkpoint_state(graph: Any, thread_config: dict[str, Any]) -> dict[str, Any] | None:
    """체크포인트에서 이전 State를 조회한다.

    Args:
        graph: 컴파일된 LangGraph 그래프
        thread_config: thread_id가 포함된 설정

    Returns:
        이전 State 딕셔너리 또는 None (체크포인트 없음)
    """
    try:
        state_snapshot = await asyncio.to_thread(
            graph.get_state, thread_config
        )
        if state_snapshot and state_snapshot.values:
            return state_snapshot.values  # type: ignore[no-any-return]
    except Exception as e:
        logger.debug("체크포인트 조회 실패 (첫 턴으로 진행): %s", e)
    return None


async def _resolve_turn_approval(
    body: QueryRequest, checkpoint_state: dict[str, Any] | None, config: Any
) -> tuple[str, str] | None:
    """승인 대기 턴이면 승인 의사를 해소해 반환한다(그 외 턴은 None).

    두 텍스트 라우트가 공유한다 — 한쪽만 LLM 보조를 받는 비대칭을 만들지 않는다(D-066).
    구조 승인 대기 구 스레드는 승인 턴이 아니다(plans/104 — `_is_legacy_structure_hitl_thread`).
    """
    if not checkpoint_state or not checkpoint_state.get("awaiting_approval"):
        return None
    if _is_legacy_structure_hitl_thread(checkpoint_state):
        return None
    return await resolve_approval_action(body.query, config)


def _zone_answer_parse_reuse(
    body: QueryRequest, checkpoint_state: dict[str, Any]
) -> dict[str, Any] | None:
    """존 역질문 답변 턴이면 직전 턴 `parsed_requirements`를, 아니면 None (plans/119 Q-2).

    조건(전부): 직전 턴이 후단 게이트로 존을 되물었고(체크포인트 `zone_clarification`) · 이번 턴이
    존을 골랐고(`selected_db_ids` — 소스 선택 칩이면 `selected_sources` · plans/132 N-10) · 이번 턴
    질의가 역질문의 원 질의와 같고 · 직전 파싱본이 있다.
    이 턴은 **대상 DB만 바꾸는** 턴이라 다시 파싱할 내용이 없다(`plans/111` C-6의 좁은 조각 —
    C-6이 오면 흡수된다). 앞단 게이트(파이프라인 미실행)는 체크포인트가 없어 해당하지 않는다.
    """
    if not (body.selected_db_ids or getattr(body, "selected_sources", None)):
        return None
    zone_q = checkpoint_state.get("zone_clarification")
    parsed = checkpoint_state.get("parsed_requirements")
    if not isinstance(zone_q, dict) or not isinstance(parsed, dict) or not parsed:
        return None
    original = str(zone_q.get("original_query") or "").strip()
    if not original or original != (body.query or "").strip():
        return None
    return dict(parsed)


def _zone_answer_plan_reuse(
    body: QueryRequest, checkpoint_state: dict[str, Any]
) -> dict[str, Any] | None:
    """존 역질문 답변 턴이면 직전 턴 계획 스냅샷을, 아니면 None (plans/121 TP-1.2 · G-30 · D-272 ⑪).

    조건은 Q-2 파싱 재사용과 같다(`_zone_answer_parse_reuse`가 값을 낼 때만). 스냅샷
    (`zone_reentry_plan`)은 2단 집계기가 존 역질문 턴의 **복합 계획**일 때만 남긴다. 앞단 게이트
    (그래프 미실행)는 스냅샷이 없어 해당하지 않는다(G-30 (b) — 범위 밖).
    """
    if _zone_answer_parse_reuse(body, checkpoint_state) is None:
        return None
    snapshot = checkpoint_state.get("zone_reentry_plan")
    if not isinstance(snapshot, dict) or not snapshot.get("tasks"):
        return None
    return dict(snapshot)


def _source_selection_meta(
    body: QueryRequest, checkpoint_state: dict[str, Any]
) -> dict[str, Any] | None:
    """이번 턴 소스 선택이 직전 턴 칩의 응답이면 그 표지, 아니면 None(plans/132 W5 — 쓰기 게이트).

    직전 턴이 소스 선택 칩(`zone_clarification.kind == "source_select"`)을 냈으면 `user_choice`,
    기억을 써서 답하고 「다른 소스로 보기」(`source_switch`)를 냈으면 `feedback`(정정)이다. 그 밖의
    `selected_sources`(API 직접 호출 등)는 확인된 선택으로 보지 않는다 — 기억에 쓰지 않는다.
    """
    if not getattr(body, "selected_sources", None):
        return None
    chip = checkpoint_state.get("zone_clarification")
    if isinstance(chip, dict) and chip.get("kind") == "source_select":
        return {"origin": "user_choice", "areas": list(chip.get("areas") or [])}
    switch = checkpoint_state.get("source_switch")
    if isinstance(switch, dict) and switch.get("correction"):
        return {"origin": "feedback", "areas": list(switch.get("areas") or [])}
    return None
