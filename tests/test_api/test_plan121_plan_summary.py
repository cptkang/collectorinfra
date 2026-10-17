"""plans/121 TP-0.1 — 2단 계획 요약(`done.plan_summary` · `QueryResponse.plan_summary` · raw.jsonl).

고정하는 계약:
① 계획 경로 코드 — 2단 계획 본체가 어느 사전 처리 단락·LLM 분해로 계획을 냈는지
   (존 재진입 = `zone_reentry`).
② 개수·코드만 싣는다(sub_query 등 문장·값 없음 · D-219 반출).
③ 계획이 없는 단(1·3단)은 키를 싣지 않는다(바이트 불변) · 사전 게이트 턴은 2단일 때만 `pre_gate`.
④ 그래프 경로 응답 조립 6경로(비스트림 2 · 스트림 폴백 2 · astream 2)와 `done` 4경로가 같은 필드를
   싣는다 — 네 진입이 공유 빌더 한 벌을 부른다(plans/179 W1a).
⑤ 하네스가 `done.plan_summary`를 raw 행으로 옮긴다.
LLM·DB 0.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from scripts.scenario.assertions import Observation
from scripts.scenario.client import _apply_done
from src.api.routes.query import _plan_summary_field, _pre_gate_plan_summary
from src.api.schemas import QueryResponse
from src.state import create_initial_state

_ip = importlib.import_module("src.orchestration.intent_planner")
_QUERY_PY = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"


def _state(**over):
    state = create_initial_state("심각 알람 서버의 CPU")
    state.update({
        "plan_path": "llm_decompose",
        "task_plan": [
            {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버 CPU", "order": 2,
             "status": "completed", "depends_on": ["t1"], "input_from": ["t1"]},
            {"task_id": "t1", "agent": "alarm_query", "sub_query": "심각 알람 서버", "order": 1,
             "status": "completed", "depends_on": [], "input_from": []},
            {"task_id": "t3", "agent": "general_inference", "sub_query": "x", "order": 3,
             "status": "completed", "agent_fallback": True},
        ],
        "task_results": {
            "t1": {"target_db_ids": ["polestar_cm_gp"], "db_origin": "hint",
                   "dependency_notes": [{"kind": "trace", "task_id": "t2", "detail": "2대 주입"}]},
            "t2": {"target_db_ids": ["polestar_cm_gp"], "db_origin": "planned",
                   "dependency_notes": [{"kind": "bridge", "task_id": "t2", "detail": "d",
                                         "counts": {"total": 2, "link": 2}}]},
        },
        "replan_count": 1,
        "dependency_notes": [{"kind": "trace", "task_id": "t2", "detail": "2대 주입"}],
    })
    state.update(over)
    return state


def test_summary_is_codes_and_counts_only():
    summary = _plan_summary_field(_state())["plan_summary"]
    assert summary["plan_path"] == "llm_decompose" and summary["task_count"] == 3
    assert [t["id"] for t in summary["tasks"]] == ["t1", "t2", "t3"]
    assert summary["tasks"][1] == {
        "id": "t2", "agent": "data_query", "status": "completed", "depends_on": ["t1"],
        "input_from": ["t1"], "db_ids": ["polestar_cm_gp"], "db_origin": "planned",
    }
    assert summary["replan_count"] == 1 and summary["agent_fallback"] == 1
    assert summary["note_kinds"] == {"trace": 1, "bridge": 1}   # 같은 노트는 한 번
    assert summary["bridge"] == [{"task_id": "t2", "total": 2, "link": 2}]
    assert "그 서버 CPU" not in str(summary) and "2대 주입" not in str(summary)


@pytest.mark.parametrize("over", [{"plan_path": None}, {"task_plan": []}])
def test_no_plan_no_key(over):
    assert _plan_summary_field(_state(**over)) == {}


def test_pre_gate_summary_only_on_tier2(monkeypatch):
    import src.observability.ladder as ladder

    monkeypatch.setattr(ladder, "current_ladder", lambda: {"tier": "intent_orchestration"})
    assert _pre_gate_plan_summary() == {"plan_summary": {"plan_path": "pre_gate", "task_count": 0}}
    monkeypatch.setattr(ladder, "current_ladder", lambda: {"tier": "deep_agent"})
    assert _pre_gate_plan_summary() == {}


def test_query_response_declares_field():
    r = QueryResponse(query_id="1", status="completed", response="", plan_summary={"a": 1})
    assert r.plan_summary == {"a": 1}
    assert QueryResponse(query_id="1", status="completed", response="").plan_summary is None


def _func(tree: ast.Module, name: str) -> ast.AST:
    return next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _calls(tree: ast.Module, caller: str, callee: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(_func(tree, caller))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
    ]


#: 진입 → 공유 헬퍼 직접 호출(진입마다 1회) — 사전 게이트 판정·렌더 + 그래프 턴 응답 조립
_ENTRY_CALLS = {
    "process_query": ("_run_pre_gates", "_pre_gate_response", "_build_turn_response"),
    "process_file_query": ("_run_pre_gates", "_pre_gate_response", "_build_turn_response"),
    "process_query_stream": ("_run_pre_gates", "_pre_gate_done", "_stream_graph_turn"),
    "process_file_query_stream": ("_run_pre_gates", "_pre_gate_done", "_stream_graph_turn"),
}


def test_route_wiring_through_shared_builders():
    """네 진입이 공유 헬퍼를 부르고 공유 헬퍼가 계획 요약 칸을 싣는다(plans/179 W1a).

    종전 소스 계수(응답 조립 6곳 · done 4곳 · 사전 게이트 4곳)의 전환 — 리터럴이 한 벌이 됐다.
    ① 진입마다: 비스트림은 `_build_turn_response`, 스트림은 `_stream_graph_turn`(astream
    `_build_stream_turn_response` + 폴백 `_build_turn_response` → done `_turn_done_payload` 2곳)을
    부른다. 사전 게이트는 `_run_pre_gates` → 렌더(`_pre_gate_response`/`_pre_gate_done`).
    ② 공유 헬퍼: 응답 dict는 `_plan_summary_field(state)`(astream은 누적 상태) · done은
    `_plan_summary_carry(response_data)` · 사전 게이트 역질문은 `_pre_gate_plan_summary()`.
    """
    tree = ast.parse(_QUERY_PY.read_text(encoding="utf-8"))
    for entry, callees in _ENTRY_CALLS.items():
        for callee in callees:
            assert len(_calls(tree, entry, callee)) == 1, f"{entry} → {callee}"
    assert len(_calls(tree, "_stream_graph_turn", "_build_stream_turn_response")) == 1
    assert len(_calls(tree, "_stream_graph_turn", "_build_turn_response")) == 1
    assert len(_calls(tree, "_stream_graph_turn", "_turn_done_payload")) == 2
    for builder in ("_build_turn_response", "_build_stream_turn_response"):
        assert len(_calls(tree, builder, "_turn_response_data")) == 1, builder
    # astream 응답은 종료 노드 델타가 아니라 누적 상태(`_scope_state`)로 만든다
    (astream,) = _calls(tree, "_stream_graph_turn", "_build_stream_turn_response")
    assert ast.unparse(astream.args[3]) == "_scope_state"
    (data_call,) = _calls(tree, "_build_stream_turn_response", "_turn_response_data")
    assert ast.unparse(data_call.args[2]) == "scope_state"

    src = {
        name: ast.unparse(_func(tree, name))
        for name in ("_turn_response_data", "_turn_done_payload", "_run_pre_gates",
                     "_pre_gate_response", "_pre_gate_done")
    }
    assert src["_turn_response_data"].count("**_plan_summary_field(state)") == 1
    assert src["_turn_done_payload"].count("**_plan_summary_carry(response_data)") == 1
    assert src["_run_pre_gates"].count("**_pre_gate_plan_summary()") == 1
    assert "**gate.extra" in src["_pre_gate_response"]
    assert "**gate.extra" in src["_pre_gate_done"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state_over", "code"),
    [({"selected_db_ids": ["polestar_cm_gp"]}, "zone_reentry"),
     ({"mapped_db_ids": ["polestar_cm_gp"]}, "mapped_db_ids")],
)
async def test_plan_path_codes_for_pre_checks(state_over, code):
    state = {**create_initial_state("서버 목록"), **state_over}
    out = await _ip.intent_planner(state, llm=AsyncMock(), app_config=MagicMock())
    assert out["plan_path"] == code


@pytest.mark.asyncio
async def test_plan_path_for_llm_decompose(monkeypatch, mock_config):
    async def fake(llm, q, cfg, conversation_context=None):
        return {"tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": q,
                           "depends_on": [], "input_from": [], "order": 1, "status": "pending"}],
                "clarification_needed": None}

    monkeypatch.setattr(_ip, "_llm_decompose", fake)
    mock_config.composite.investigation_enabled = False
    out = await _ip.intent_planner(create_initial_state("서버 목록"), llm=AsyncMock(),
                                   app_config=mock_config)
    assert out["plan_path"] == "llm_decompose"


def test_harness_moves_summary_to_observation():
    obs = Observation()
    _apply_done(obs, {"response": "r", "plan_summary": {"plan_path": "zone_reentry"}})
    assert obs.plan_summary == {"plan_path": "zone_reentry"}
    obs2 = Observation()
    _apply_done(obs2, {"response": "r"})
    assert obs2.plan_summary is None
