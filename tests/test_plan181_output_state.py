"""plans/181 W1-A — `OutputState` 선언 · `create_initial_state` 초기값 표 정합.

- `OutputState` 키 = `result_aggregator._build_output_state`가 실제로 만드는 키(허용목록 —
  docs/18 2026-10-06: 여기 없는 키는 2단·1단 출구의 output_generator에 닿지 않는다).
- `OutputState` 중 `AgentState` 밖 키는 고정된 8종이다 — 새 키가 생기면 여기서 의식적으로 고친다.
- `create_initial_state` 키 = `AgentState` 키(요청 스코프 명시 초기화 — D-064·D-162) ·
  가변 기본값은 호출마다 새 객체다.
"""

from __future__ import annotations

from typing import Any

from src.orchestration.result_aggregator import _build_output_state
from src.state import AgentState, OutputState, create_initial_state


def _keys(td: Any) -> set[str]:
    """TypedDict의 선언 키 전부(상속 구간 포함)."""
    return set(td.__required_keys__ | td.__optional_keys__)


_NON_AGENT_STATE_KEYS = {
    "executed_sqls",
    "period_sources",
    "limit_sources",
    "condition_check",
    "task_id",
    "per_task_finalize",
    "all_null_downgrade",
    "original_user_query",
}


def test_output_state_annotations_include_inherited_sections() -> None:
    """구간 TypedDict를 상속한 `OutputState`의 `__annotations__`가 상속 키까지 담는다."""
    assert set(OutputState.__annotations__) == _keys(OutputState)


def test_build_output_state_keys_match_declaration_minimal() -> None:
    """최소 입력에서도 반환 키 집합 = `OutputState` 선언 키 집합."""
    state = create_initial_state("전체 질의")
    out = _build_output_state(state, {"task_id": "t1"}, {})
    assert set(out.keys()) == _keys(OutputState)


def test_build_output_state_keys_match_declaration_filled() -> None:
    """선택 인자·task 결과를 채워도 키 집합은 같다(값만 바뀐다)."""
    state = create_initial_state("전체 질의", selected_db_ids=["polestar"])
    task = {"task_id": "t2", "sub_query": "하위 질의", "agent": "alarm_query"}
    res = {
        "organized_data": {"summary": "", "rows": [{"a": 1}]},
        "query_results": [{"a": 1}],
        "target_db_ids": ["polestar"],
        "executed_sqls": [{"db_id": "polestar", "sql": "SELECT 1"}],
        "spike_notes": ["한계"],
        "time_resolution": {"kind": "range"},
        "form_month_anchor": {"start": "202601"},
    }
    out = _build_output_state(
        state, task, res,
        condition_check=True,
        limit_sources=[{"task_id": "t2"}],
        period_sources=[{"task_id": "t2"}],
    )
    assert set(out.keys()) == _keys(OutputState)
    assert out["per_task_finalize"] is True
    assert out["query_attempts"] == [{"sql": "SELECT 1"}]


def test_output_state_non_agent_state_keys_are_fixed() -> None:
    """`OutputState` - `AgentState` = 이 경로 전용 입력 8종."""
    assert _keys(OutputState) - _keys(AgentState) == _NON_AGENT_STATE_KEYS


def test_create_initial_state_keys_match_agent_state() -> None:
    """초기 state는 `AgentState` 키를 빠짐없이(그리고 그것만) 초기화한다."""
    assert set(create_initial_state("q").keys()) == _keys(AgentState)


def test_create_initial_state_mutable_defaults_not_shared() -> None:
    """가변 기본값(목록 · dict · 중첩 dict)은 호출마다 새 객체다."""
    a = create_initial_state("q")
    b = create_initial_state("q")
    mutable = [k for k, v in a.items() if isinstance(v, (list, dict))]
    assert "organized_data" in mutable and "validation_result" in mutable
    for key in mutable:
        assert a[key] is not b[key], key
    assert a["organized_data"]["rows"] is not b["organized_data"]["rows"]
    # 반환값을 고쳐도 다음 호출의 기본값은 오염되지 않는다.
    a["organized_data"]["rows"].append({"x": 1})
    a["query_results"].append({"x": 1})
    c = create_initial_state("q")
    assert c["organized_data"]["rows"] == []
    assert c["query_results"] == []
