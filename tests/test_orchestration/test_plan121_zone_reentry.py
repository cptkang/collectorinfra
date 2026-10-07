"""plans/121 TP-1.2 — 존 재진입 계획 보존(후단 복합만 · G-30 · D-272 ⑪ · D-270 ⑤).

고정하는 계약:
① 2단 집계기의 존 역질문 단락은 **복합 계획**(task 2개 이상)일 때만 요청 스코프 스냅샷
   `zone_reentry_plan`을 쓴다 — 계획 필드만(상태·결과 없음) + 게이트에 걸린 task id.
   단일 task 계획과 계획 경로 코드가 없는 단(1단·3단)은 키를 싣지 않는다(바이트 불변).
② 라우트는 Q-2 재사용 조건(후단 게이트 역질문 뒤 · 존 선택 · 같은 원 질의 · 파싱본)에서만 체크포인트
   스냅샷을 `reuse_task_plan`으로 옮기고, 입력 델타가 원 키를 None으로 덮는다.
③ `intent_planner` ②.5는 `reuse_task_plan`이 있으면 계획을 복원한다 — 게이트 task만 존 고정 ·
   상태 pending · 간선·순서 보존 · `plan_path=zone_reentry_restored`. 없으면 종전과 바이트 동일.
④ 양식 턴·모양이 어긋난 스냅샷은 복원하지 않는다(종전 단일 task 경로).
⑤ 두 상태 생성 함수가 두 키를 None으로 초기화한다 — 다음 턴에 새지 않는다.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.graph import END, START, StateGraph

from src.api.routes.query import _build_turn_input_state, _zone_answer_plan_reuse
from src.api.schemas import QueryRequest
from src.orchestration.result_aggregator import result_aggregator
from src.state import AgentState, create_followup_input, create_initial_state

# 패키지 `src.orchestration`이 같은 이름의 노드 함수를 재노출해 `import … as`가 함수를 가리킨다.
ip = importlib.import_module("src.orchestration.intent_planner")

_QUERY = "알람 발생 서버 목록과 서버별 CPU 사용률"
_ZONE = "polestar_cm_gp"
_PAYLOAD = {
    "kind": "zone_select", "original_query": _QUERY, "question": "어느 존의 데이터를 조회할까요?",
    "options": [{"db_id": "polestar_cm_gp"}, {"db_id": "polestar_cm_yd"}],
}
_PARSED = {
    "original_query": _QUERY, "query_targets": ["서버", "CPU 사용률"], "filter_conditions": [],
    "target_db_hints": [],
}
_USER = {"sub": "u1", "role": "user", "allowed_db_ids": None}

#: 1턴 계획 — 실행 뒤 모습(상태·실행 중 표지 포함). t1이 후단 게이트에 걸렸다.
_TURN1_PLAN = [
    {"task_id": "t1", "agent": "alarm_query", "sub_query": "알람 발생 서버 목록",
     "depends_on": [], "input_from": [], "order": 1, "status": "completed"},
    {"task_id": "t2", "agent": "data_query", "sub_query": "서버별 CPU 사용률",
     "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "completed"},
]
_TURN1_RESULTS = {
    "t1": {"final_response": _PAYLOAD["question"], "zone_clarification": _PAYLOAD, "source": []},
    "t2": {"organized_data": {"summary": "2대", "rows": [{"hostname": "web-01"}]},
           "target_db_ids": ["polestar_b0"], "db_origin": "classified"},
}
_SNAPSHOT = {
    "tasks": [
        {"task_id": "t1", "agent": "alarm_query", "sub_query": "알람 발생 서버 목록",
         "depends_on": [], "input_from": [], "order": 1},
        {"task_id": "t2", "agent": "data_query", "sub_query": "서버별 CPU 사용률",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2},
    ],
    "gated_task_ids": ["t1"],
}


def _turn1_state(plan: list[dict[str, Any]] | None = None, plan_path: str | None = "llm_decompose",
                 results: dict[str, Any] | None = None) -> dict[str, Any]:
    state: dict[str, Any] = dict(create_initial_state(user_query=_QUERY))
    state["task_plan"] = json.loads(json.dumps(plan if plan is not None else _TURN1_PLAN))
    state["task_results"] = dict(results if results is not None else _TURN1_RESULTS)
    state["plan_path"] = plan_path
    state["parsed_requirements"] = dict(_PARSED)
    return state


def _body(query: str = _QUERY, selected: tuple[str, ...] = (_ZONE,)) -> QueryRequest:
    return QueryRequest(query=query, selected_db_ids=list(selected) if selected else None)


def _checkpoint(**over: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "zone_clarification": dict(_PAYLOAD),
        "parsed_requirements": dict(_PARSED),
        "zone_reentry_plan": json.loads(json.dumps(_SNAPSHOT)),
        "messages": [],
    }
    state.update(over)
    return state


def _answer_state(reuse: dict[str, Any] | None = None, **over: Any) -> dict[str, Any]:
    state: dict[str, Any] = dict(create_initial_state(user_query=_QUERY, selected_db_ids=[_ZONE]))
    state["reuse_task_plan"] = json.loads(json.dumps(reuse if reuse is not None else _SNAPSHOT))
    state.update(over)
    return state


async def _plan(state: dict[str, Any]) -> tuple[dict[str, Any], MagicMock]:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=AssertionError("LLM 호출 금지"))
    out = await ip.intent_planner(state, llm=llm, app_config=MagicMock())
    return out, llm


# ── ⑤ 상태 선언 · 두 생성 함수 초기화 ─────────────────────────────


def test_keys_declared_in_agent_state():
    assert "zone_reentry_plan" in AgentState.__annotations__
    assert "reuse_task_plan" in AgentState.__annotations__


def test_both_state_constructors_reset_to_none():
    for key in ("zone_reentry_plan", "reuse_task_plan"):
        assert create_initial_state("질의")[key] is None
        delta = create_followup_input("후속 질의")
        assert key in delta and delta[key] is None


def test_langgraph_keeps_declared_keys():
    """선언된 키라 노드가 쓴 값이 그래프 상태에 남는다(미선언이면 드롭돼 복원 자체가 불가능하다)."""
    def _node(_state):
        return {"zone_reentry_plan": _SNAPSHOT, "reuse_task_plan": _SNAPSHOT}

    graph = StateGraph(AgentState)
    graph.add_node("n", _node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    out = graph.compile().invoke(create_initial_state("질의"))
    assert out["zone_reentry_plan"] == _SNAPSHOT
    assert out["reuse_task_plan"] == _SNAPSHOT


# ── ① 집계기 존 역질문 단락 ────────────────────────────────────


@pytest.mark.asyncio
async def test_composite_zone_question_writes_plan_snapshot(mock_config):
    out = await result_aggregator(_turn1_state(), llm=AsyncMock(), app_config=mock_config)

    assert out["zone_clarification"] == _PAYLOAD
    assert out["final_response"] == _PAYLOAD["question"]
    assert out["query_results"] == []
    assert out["zone_reentry_plan"] == _SNAPSHOT
    # 계획 필드만 — 상태·실행 중 노트·결과는 싣지 않는다
    for task in out["zone_reentry_plan"]["tasks"]:
        assert "status" not in task and "dependency_note" not in task
    # 존 역질문 턴은 DB 승격을 붙이지 않는다(종전 불변)
    assert "db_scope_source" not in out and "active_db_id" not in out


@pytest.mark.asyncio
async def test_snapshot_keeps_plan_markers_and_copies_lists(mock_config):
    plan = json.loads(json.dumps(_TURN1_PLAN))
    plan[0].update({"capability": "alarm", "spans": ["알람 발생 서버"], "direct_response": "x"})
    note = {"kind": "gate", "task_id": "t2", "detail": "선행 결과 없음"}
    plan[1].update({"supersedes": [], "agent_fallback": True, "db_ids": ["itam"],
                    "dependency_note": note})
    state = _turn1_state(plan=plan)

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    snap = out["zone_reentry_plan"]
    assert snap["tasks"][0]["capability"] == "alarm"
    assert snap["tasks"][0]["spans"] == ["알람 발생 서버"]
    assert "direct_response" not in snap["tasks"][0]
    assert "dependency_note" not in snap["tasks"][1] and "status" not in snap["tasks"][1]
    assert snap["tasks"][1]["db_ids"] == ["itam"]
    assert snap["tasks"][1]["agent_fallback"] is True
    state["task_plan"][1]["depends_on"].append("t9")  # 원 계획을 고쳐도 스냅샷은 그대로(사본)
    assert snap["tasks"][1]["depends_on"] == ["t1"]


@pytest.mark.asyncio
async def test_single_task_zone_question_output_unchanged(mock_config):
    """단일 의도 역질문 턴은 종전과 같은 반환(키 집합·값) — 스냅샷을 남기지 않는다."""
    plan = [{"task_id": "t1", "agent": "data_query", "sub_query": _QUERY, "depends_on": [],
             "input_from": [], "order": 1, "status": "completed"}]
    results = {"t1": _TURN1_RESULTS["t1"]}
    out = await result_aggregator(
        _turn1_state(plan=plan, results=results, plan_path="llm_decompose"),
        llm=AsyncMock(), app_config=mock_config,
    )
    assert set(out) == {
        "final_response", "zone_clarification", "current_node", "query_results", "messages",
    }
    assert out["final_response"] == _PAYLOAD["question"]


@pytest.mark.asyncio
async def test_no_plan_path_means_no_snapshot(mock_config):
    """계획 경로 코드가 없는 단은 키를 싣지 않는다 — 1단 `deep_agent` · 3단 `finalize`·순차 러너."""
    out = await result_aggregator(
        _turn1_state(plan_path=None), llm=AsyncMock(), app_config=mock_config,
    )
    assert out["zone_clarification"] == _PAYLOAD
    assert "zone_reentry_plan" not in out


@pytest.mark.asyncio
async def test_tier1_aggregate_path_writes_no_snapshot(mock_config):
    """1단은 수집기 결과로 같은 집계기를 부른다 — 복합 존 역질문이어도 스냅샷을 쓰지 않는다."""
    from src.orchestration.deep_agent import _aggregate_with_fabrix

    collector = [(t, _TURN1_RESULTS[t["task_id"]]) for t in json.loads(json.dumps(_TURN1_PLAN))]
    state = dict(create_initial_state(user_query=_QUERY))
    out = await _aggregate_with_fabrix(collector, state, mock_config, AsyncMock())
    assert out["zone_clarification"] == _PAYLOAD
    assert "zone_reentry_plan" not in out


def test_composite_without_gated_task_has_no_snapshot():
    """게이트에 걸린 task가 없으면 스냅샷도 없다(존 역질문 단락 밖 — 방어)."""
    ra = importlib.import_module("src.orchestration.result_aggregator")
    results = {
        "t1": {"organized_data": {"summary": "1건", "rows": [{"hostname": "a"}]}},
        "t2": _TURN1_RESULTS["t2"],
    }
    plan = json.loads(json.dumps(_TURN1_PLAN))
    assert ra._zone_reentry_snapshot(plan, results) is None
    assert ra._zone_reentry_snapshot(plan, _TURN1_RESULTS) == _SNAPSHOT


# ── ② 라우트 입력 조립(Q-2 조건) ────────────────────────────────


def test_answer_turn_moves_snapshot_to_reuse_key():
    delta = _build_turn_input_state(_body(), "th", _checkpoint(), _USER)
    assert delta["reuse_task_plan"] == _SNAPSHOT
    assert delta["reuse_parsed_requirements"] == _PARSED
    assert delta["zone_reentry_plan"] is None  # 원 키는 입력 델타가 덮는다


@pytest.mark.parametrize(
    "body,cp",
    [
        (_body(selected=()), _checkpoint()),                                   # 존 선택 없음
        (_body(query="다른 질의"), _checkpoint()),                               # 원 질의 불일치
        (_body(), _checkpoint(zone_clarification=None)),                        # 역질문 없던 턴
        (_body(), _checkpoint(parsed_requirements={})),                         # 파싱본 없음
        (_body(), _checkpoint(zone_reentry_plan=None)),                         # 단일 task 역질문
        (_body(), _checkpoint(zone_reentry_plan={"tasks": []})),                # 빈 스냅샷
    ],
)
def test_no_reuse_outside_condition(body, cp):
    assert _zone_answer_plan_reuse(body, cp) is None
    delta = _build_turn_input_state(body, "th", cp, _USER)
    assert delta["reuse_task_plan"] is None
    assert delta["zone_reentry_plan"] is None


def test_first_turn_never_reuses():
    """앞단 게이트 답변 턴(체크포인트 없음 — 그래프 미실행)은 복원할 계획이 없다(G-30 (b))."""
    state = _build_turn_input_state(_body(), "th", None, _USER)
    assert state["reuse_task_plan"] is None
    assert state["zone_reentry_plan"] is None


# ── ③ 계획 복원(②.5) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_restores_plan_pinning_only_gated_tasks():
    out, llm = await _plan(_answer_state())

    assert out["plan_path"] == "zone_reentry_restored"
    assert out["is_composite"] is True
    tasks = out["task_plan"]
    assert [t["task_id"] for t in tasks] == ["t1", "t2"]
    assert tasks[0]["db_ids"] == [_ZONE]            # 게이트 task만 선택 존 고정
    assert "db_ids" not in tasks[1]                 # 나머지는 고정하지 않는다
    assert all(t["status"] == "pending" for t in tasks)
    assert tasks[1]["depends_on"] == ["t1"] and tasks[1]["input_from"] == ["t1"]
    assert [t["order"] for t in tasks] == [1, 2]
    assert [t["agent"] for t in tasks] == ["alarm_query", "data_query"]
    assert out["clarification_needed"] is None
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_non_gated_task_keeps_its_own_plan_db_ids():
    snap = json.loads(json.dumps(_SNAPSHOT))
    snap["tasks"][1]["db_ids"] = ["itam"]            # 앞 턴 계획이 고정했던 값은 계획 필드로 보존
    out, _ = await _plan(_answer_state(reuse=snap))
    assert out["task_plan"][0]["db_ids"] == [_ZONE]
    assert out["task_plan"][1]["db_ids"] == ["itam"]


@pytest.mark.asyncio
async def test_no_reuse_is_byte_identical_to_single_zone_reentry():
    """`reuse_task_plan`이 없으면 ②.5는 종전 단일 task 경로 — 반환 dict가 바이트 동일.

    원 질의는 담당 교정(알람·프로세스 — 출구 정규화 D-250 ⑤)이 닿지 않는 문장이다. 교정이 닿는
    문장도 종전과 같다는 것은 기준선 사본 대조로 확인했다(구현 보고).
    """
    query = "서버별 CPU 사용률 상위 10건"
    state = dict(create_initial_state(user_query=query, selected_db_ids=[_ZONE]))
    out, _ = await _plan(state)
    expected = {
        "task_plan": [{
            "task_id": "t1", "agent": "data_query", "sub_query": query, "depends_on": [],
            "input_from": [], "order": 1, "status": "pending", "db_ids": [_ZONE],
        }],
        "is_composite": False,
        "current_node": "intent_planner",
        "plan_path": "zone_reentry",
        "clarification_needed": None,
    }
    assert json.dumps(out, ensure_ascii=False) == json.dumps(expected, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "over",
    [
        {"uploaded_file": b"xlsx"},                                            # 양식 업로드 턴
        {"template_structure": {"sheets": [{"headers": ["호스트명"]}]}},         # 양식 구조 턴
    ],
)
async def test_form_turn_is_not_restored(over):
    out, _ = await _plan(_answer_state(**over))
    assert out["plan_path"] == "zone_reentry"
    assert len(out["task_plan"]) == 1
    assert out["task_plan"][0]["db_ids"] == [_ZONE]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snap",
    [
        {"tasks": _SNAPSHOT["tasks"][:1], "gated_task_ids": ["t1"]},          # 단일 task
        {"tasks": _SNAPSHOT["tasks"], "gated_task_ids": []},                  # 게이트 task 없음
        {"tasks": _SNAPSHOT["tasks"], "gated_task_ids": ["t9"]},              # 계획 밖 id
        {"tasks": [{"task_id": "t1"}, {"task_id": "t2"}], "gated_task_ids": ["t1"]},  # 담당 없음
        {"tasks": "t1", "gated_task_ids": ["t1"]},                             # 모양 불일치
    ],
)
async def test_malformed_snapshot_falls_back_to_single(snap):
    out, _ = await _plan(_answer_state(reuse=snap))
    assert out["plan_path"] == "zone_reentry"
    assert len(out["task_plan"]) == 1


# ── 1턴 → 2턴 → 3턴 왕복(체크포인터 델타 병합 모사) ─────────────────


@pytest.mark.asyncio
async def test_round_trip_restores_then_does_not_leak(mock_config):
    # 1턴: 복합 계획 · t1 후단 게이트 역질문 → 스냅샷
    turn1 = _turn1_state()
    out1 = await result_aggregator(turn1, llm=AsyncMock(), app_config=mock_config)
    checkpoint1 = {**turn1, **out1}

    # 2턴: 같은 원 질의 + 존 선택 → 라우트가 스냅샷을 복원 입력으로 옮기고 원 키는 비운다
    delta2 = _build_turn_input_state(_body(), "th", checkpoint1, _USER)
    state2 = {**checkpoint1, **delta2}
    assert state2["zone_reentry_plan"] is None
    assert state2["reuse_task_plan"] == out1["zone_reentry_plan"]
    plan2, _ = await _plan(state2)
    state2.update(plan2)
    assert state2["plan_path"] == "zone_reentry_restored"
    assert [t.get("db_ids") for t in state2["task_plan"]] == [[_ZONE], None]

    # 3턴: 일반 후속 — 스냅샷·복원 입력이 새지 않는다
    delta3 = _build_turn_input_state(_body(query="다른 질의", selected=()), "th", state2, _USER)
    state3 = {**state2, **delta3}
    assert state3["reuse_task_plan"] is None
    assert state3["zone_reentry_plan"] is None


# ── 실행 배관 — 비게이트 task는 존 그룹 대상만 선택 존으로 바꾼다(교차 시스템 보호) ─────────


@pytest.mark.asyncio
async def test_non_gated_restored_task_is_not_pinned_at_execution():
    """복원 계획(실제 `intent_planner` 출력) → 격리 입력(실제 `_make_isolated_input`) → 배관.

    게이트에 걸리지 않았던 t2는 분류 결과(존 없는 다른 시스템)로 간다 — 선택 존으로 끌려가지 않는다.
    t2 질의는 사용률이 아닌 것으로 바꾼다(D-308 가드 — 사용률 task는 비소유 DB 제외).
    """
    from unittest.mock import patch

    from src.orchestration import subagents

    captured: dict[str, Any] = {}

    async def _capture(state: dict[str, Any], *_a: Any, **_kw: Any) -> dict[str, Any]:
        captured["state"] = state
        return {}

    state = _answer_state()
    plan_out, _ = await _plan(state)
    state.update(plan_out)
    t2 = next(t for t in state["task_plan"] if t["task_id"] == "t2")
    assert "db_ids" not in t2                                   # 계획 단은 비고정이다
    t2 = {**t2, "sub_query": "서버별 OS 버전"}
    isolated = subagents._make_isolated_input(t2, state, {})

    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["polestar_b0", _ZONE, "itam"]
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    classified = [{"db_id": "itam", "relevance_score": 1.0, "sub_query_context": t2["sub_query"],
                   "user_specified": False, "reason": "분류"}]
    with patch.object(subagents, "classify_dbs", AsyncMock(return_value=classified)), \
         patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})):
        await subagents.run_data_query_pipeline(t2, isolated, llm=AsyncMock(), app_config=config)
    assert [t["db_id"] for t in captured["state"]["target_databases"]] == ["itam"]


async def _run_t2(active: list[str], classified_ids: list[str]) -> list[str]:
    from unittest.mock import patch

    from src.orchestration import subagents

    captured: dict[str, Any] = {}

    async def _capture(state: dict[str, Any], *_a: Any, **_kw: Any) -> dict[str, Any]:
        captured["state"] = state
        return {}

    state = _answer_state()
    plan_out, _ = await _plan(state)
    state.update(plan_out)
    t2 = next(t for t in state["task_plan"] if t["task_id"] == "t2")
    # 사용률이 아닌 질의로 바꾼다(D-308 가드 — 사용률 task는 비소유 DB 제외 · 여기 의도는 존 배관)
    t2 = {**t2, "sub_query": "서버별 OS 버전"}
    isolated = subagents._make_isolated_input(t2, state, {})
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    classify = AsyncMock(return_value=[
        {"db_id": d, "relevance_score": 1.0, "sub_query_context": t2["sub_query"],
         "user_specified": False, "reason": "분류"} for d in classified_ids
    ])
    with patch.object(subagents, "classify_dbs", classify), \
         patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})):
        await subagents.run_data_query_pipeline(t2, isolated, llm=AsyncMock(), app_config=config)
    captured["classified"] = classify.await_count
    return [t["db_id"] for t in captured["state"]["target_databases"]], captured["classified"]


@pytest.mark.asyncio
async def test_non_gated_task_zone_group_targets_follow_selection():
    """분류가 존 그룹 DB를 고르면 사용자가 고른 존으로 바뀐다(존 없는 DB는 유지).

    t2 질의는 `_run_t2`가 사용률이 아닌 것으로 바꾼다(D-308 가드 — 사용률 task는 비소유 DB 제외).
    """
    ids, _ = await _run_t2(["polestar_b0", _ZONE, "itam"], ["polestar_b0", "itam"])
    assert ids == [_ZONE, "itam"]


@pytest.mark.asyncio
async def test_single_system_config_keeps_prior_pin_without_classify():
    """활성 시스템이 하나(운영 폴스타 전용)면 종전처럼 선택 존에 바로 고정 — 분류 LLM 0(§12.6 ①)."""
    ids, classified = await _run_t2(["polestar_b0", _ZONE], ["polestar_b0"])
    assert ids == [_ZONE] and classified == 0
