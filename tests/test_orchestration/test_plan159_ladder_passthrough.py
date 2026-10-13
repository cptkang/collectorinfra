"""plans/159 W4 — 분 단위 시간 해석이 1단·3단에도 그대로 전달된다 (D-328 · D-309).

plans/122 T-4(`test_plan122_t4_orchestration.py`)가 2단·3단 배선을 고정했다. 여기서는 분 단위
값(통계 = 직전 정시 ⌈N/60⌉시간 + `minute_widened_to_hour` · 사건 = 분 그대로)이 같은 길로 지나
가는지만 본다.

① 1단 — `_extract_ambient_state`가 `time_resolution`을 그대로 싣고, ambient로 만든 격리 입력이
   2단과 같은 task 범위 값을 만든다(1단 ambient에는 `user_query`가 없다 — 원문은
   `parsed_requirements.original_query`).
② 3단 — `task_payload`는 원문 해석 그대로, `task_prompt`가 2단과 같은 값으로 좁히고 그 값이
   `TaskRunState` 서브그래프 채널을 지나 하류까지 닿는다.

기준 시각 2026-09-29(화) 10:07 KST(정시가 아니어야 통계 확장과 사건 분 구간이 갈린다). LLM·DB 0.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from langgraph.graph import END, START, StateGraph

from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST, NOTE_MINUTE_WIDENED
from src.orchestration.deep_agent import _extract_ambient_state
from src.orchestration.subagents import _make_isolated_input
from src.orchestration.tier3_plan import TaskRunState, task_payload, task_prompt
from src.state import create_initial_state

NOW = datetime(2026, 9, 29, 10, 7, tzinfo=KST)

#: 원문 = 시간 기간 + 분 기간(복합) · task 2 = 분 기간만
COMPOSITE = "지난 3시간 CPU 상위 서버의 최근 30분 메모리"
T_MINUTE = "앞 단계 서버들의 최근 30분 메모리"


def _t(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=KST)


#: task 2 기대값 — 통계 09:00~10:00(직전 정시 1시간) · 사건 09:37~10:07(분 그대로)
EXPECTED_MINUTE = (_t(9), _t(10), "hour", _t(9, 37), _t(10, 7))


def _graph_state() -> dict[str, Any]:
    state: dict[str, Any] = dict(create_initial_state(user_query=COMPOSITE, thread_id="th-153"))
    state["time_resolution"] = resolve_query_time(COMPOSITE, NOW).to_state()
    state["parsed_requirements"] = {"original_query": COMPOSITE}
    return state


def _task() -> dict[str, Any]:
    return {
        "task_id": "t2", "agent": "data_query", "sub_query": T_MINUTE,
        "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "in_progress",
    }


def _bounds(value: Any) -> tuple[datetime, datetime, str, datetime | None, datetime]:
    qt = QueryTime.from_state(value)
    assert qt is not None and qt.metric is not None and qt.event is not None, value
    return qt.metric.start, qt.metric.end, qt.metric.grain, qt.event.start, qt.event.end


def _notes(value: Any) -> tuple[str, ...]:
    qt = QueryTime.from_state(value)
    assert qt is not None and qt.metric is not None
    return tuple(qt.metric.notes)


def test_tier2_reference_scopes_minute_task():
    """기준 — 2단 격리 입력이 분 task를 통계 정시 확장 · 사건 분 구간으로 좁힌다."""
    scoped = _make_isolated_input(_task(), _graph_state(), {})["time_resolution"]
    assert _bounds(scoped) == EXPECTED_MINUTE
    assert NOTE_MINUTE_WIDENED in _notes(scoped)


def test_tier1_ambient_carries_minute_value_as_is():
    """① 1단 ambient — 요청 값을 바꾸지 않고 싣고, 격리 입력이 2단과 같은 값을 만든다."""
    state = _graph_state()
    ambient = _extract_ambient_state(state)
    assert ambient["time_resolution"] is state["time_resolution"]
    assert "user_query" not in ambient
    scoped = _make_isolated_input(_task(), ambient, {})["time_resolution"]
    assert _bounds(scoped) == EXPECTED_MINUTE
    assert scoped == _make_isolated_input(_task(), state, {})["time_resolution"]


@pytest.mark.asyncio
async def test_tier3_task_prompt_scopes_minute_like_tier2():
    """② 3단 — payload는 원문 값 그대로, `task_prompt`가 2단과 같은 값으로 좁힌다."""
    state = _graph_state()
    payload = task_payload(_task(), state, {}, total=2)
    assert payload["time_resolution"] is state["time_resolution"]
    out = await task_prompt(payload)  # type: ignore[arg-type]
    assert _bounds(out["time_resolution"]) == EXPECTED_MINUTE
    assert NOTE_MINUTE_WIDENED in _notes(out["time_resolution"])


@pytest.mark.asyncio
async def test_tier3_minute_value_survives_task_subgraph_channels():
    """② 3단 — 좁힌 분 값이 `TaskRunState` 채널을 지나 하류 노드에 닿는다."""
    seen: dict[str, Any] = {}

    def downstream(state: TaskRunState) -> dict[str, Any]:
        seen["time_resolution"] = state.get("time_resolution")
        return {}

    sub = StateGraph(TaskRunState)
    sub.add_node("task_prompt", task_prompt)
    sub.add_node("downstream", downstream)
    sub.add_edge(START, "task_prompt")
    sub.add_edge("task_prompt", "downstream")
    sub.add_edge("downstream", END)
    await sub.compile().ainvoke(task_payload(_task(), _graph_state(), {}, total=2))
    assert _bounds(seen["time_resolution"]) == EXPECTED_MINUTE
