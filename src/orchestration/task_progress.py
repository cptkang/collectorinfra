"""복합 질의 단계 진행 이벤트 — 1단·2단 공통 발행 헬퍼 (plans/89 §3.4 · D-204).

`agent_orchestrator`(2단 레벨 루프)와 `deepagents_tools._run_subagent_tool`(1단 도구 러너)이
**같은 함수**로 task 단계의 시작·종료를 LangChain custom event(`name="task"`)로 낸다.
바깥 그래프의 `astream_events(v2)`가 이를 `on_custom_event`로 올리고, SSE 라우트가
`progress{kind:"task"}`로 변환한다(`src/api/routes/query.py::_progress_sse_payload`).

- 판정 로직은 없다 — plans/88의 `DependencyVerdict`/`skip_result` 값을 **같은 필드명**으로 실어
  나를 뿐이다(실행 중 표시와 사후 경과 블록이 같은 값에서 나오게 하는 불변식).
- 발행 공통부(`dispatch_progress_event`·`emit_step`)는 `src/utils/progress_events.py`에 있다 —
  nodes(application)도 같은 함수를 쓰기 위해 utils 계층으로 내렸다(plans/89 T4). 여기서는 재노출한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.utils.progress_events import dispatch_progress_event, emit_step  # noqa: F401 — 재노출

logger = logging.getLogger(__name__)

TASK_EVENT = "task"
_MAX_ERROR_CHARS = 200


def _rows_of(result: Any) -> Optional[list]:
    if not isinstance(result, dict):
        return None
    for key in ("rows", "query_results"):
        v = result.get(key)
        if isinstance(v, list):
            return v
    od = result.get("organized_data")
    if isinstance(od, dict) and isinstance(od.get("rows"), list):
        return od["rows"]
    return None


def build_task_payload(
    task: dict,
    phase: str,
    *,
    result: Any = None,
    verdict: Any = None,
    total: Optional[int] = None,
) -> dict:
    """task 단계 이벤트 페이로드(순수 함수 — 테스트 대상).

    필드명은 plans/88 `DependencyVerdict`·`skip_result`와 동일하게 둔다(변환 계층 없음).
    """
    payload: dict[str, Any] = {
        "task_id": task.get("task_id", ""),
        "order": task.get("order"),
        "total": total,
        "agent": task.get("agent", ""),
        "sub_query": task.get("sub_query", ""),
        "input_from": list(task.get("input_from") or []),
        "phase": phase,
        "status": task.get("status", "pending"),
    }
    if isinstance(result, dict):
        rows = _rows_of(result)
        if rows is not None:
            payload["row_count"] = len(rows)
        if result.get("error"):
            payload["error"] = str(result["error"])[:_MAX_ERROR_CHARS]
        if result.get("skipped"):
            payload["status"] = "skipped"
            if result.get("skip_reason"):
                payload["reason"] = result["skip_reason"]
    # 1단은 verdict 노트를 task에 싣는다(deepagents_tools) — 같은 필드명으로 승격
    note = task.get("dependency_note")
    if isinstance(note, dict):
        for key in ("reason", "scope_col", "scope_size", "truncated_count"):
            if note.get(key) not in (None, "", "ok"):
                payload.setdefault(key, note[key])
    if verdict is not None:
        for key in ("scope_col", "scope_size", "truncated", "truncated_count"):
            val = getattr(verdict, key, None)
            if val not in (None, "", 0, False):
                payload[key] = val
        if not getattr(verdict, "ok", True) and getattr(verdict, "reason", None):
            payload["reason"] = verdict.reason
    return payload


async def emit_task_progress(
    task: dict,
    phase: str,
    *,
    result: Any = None,
    verdict: Any = None,
    total: Optional[int] = None,
) -> None:
    """task 단계 진행을 custom event로 낸다. 부모 run이 없으면 조용히 건너뛴다."""
    payload = build_task_payload(task, phase, result=result, verdict=verdict, total=total)
    await _dispatch(TASK_EVENT, payload)


async def _dispatch(name: str, data: dict) -> None:
    await dispatch_progress_event(name, data)
