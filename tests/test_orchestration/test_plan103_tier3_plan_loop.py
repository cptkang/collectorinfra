"""3단 계획 루프 — plans/103 P1-1·P2-1~P2-3 · plans/111 C-4·C-5 (`TIER3_PLAN_LOOP_ENABLED`).

핵심 계약:
①플래그 off면 3단 그래프 노드·분기가 현행과 같다(계획 노드 0 · 순차 러너 유지)
②계획 필요(`needs_plan`)·순차 표지 데이터 질의만 루프로 간다 — SQL 승인·양식·비데이터 의도는 제외
③`normalize`는 계획의 단일 출구다 — 사전 처리 조기 반환도 교정을 지난다(111 D-1)
④`task_prompt`는 조각으로 task 질의를 찍고 SQL 입력을 task 범위로 좁힌다(111 D-2)
⑤선행 0건이면 의존 task를 실행하지 않는다(111 D-4 · G-4)
⑥재계획은 행을 돌려준 task만 입력으로 쓰고 재시도·SQL 후속을 버린다(111 D-3 · G-3)
⑦`Send` 팬아웃 → 서브그래프 → `defer` 합류가 `ainvoke`와 `astream_events`(SSE 경로) 모두에서 돈다

실 LLM 0 · DB 0 — 파이프라인 노드·계획 LLM·라우터·합성은 대역이다.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
from src.api.routes.query import _is_subgraph_event
from src.graph import build_graph, route_after_semantic_router_plan
from src.orchestration.tier3_plan import (
    TASK_RUN_NODE,
    dispatch,
    join,
    normalize,
    pack_outcome,
    plan_loop_entry,
    replan,
    route_after_join,
    route_after_replan,
    route_dispatch,
    task_error,
    task_prompt,
)
from src.state import create_initial_state

t3 = importlib.import_module("src.orchestration.tier3_plan")

COMPOSITE_Q = "현재 활성 상태인 심각 알람이 있는 서버들의 2026년 7월 CPU 사용률을 보여줘"


def _cfg(mock_config, *, loop: bool = True, sql_approval: bool = False, tier: int = 3):
    cfg = mock_config
    cfg.tier3_plan_loop_enabled = loop
    cfg.enable_sql_approval = sql_approval
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = tier == 2
    cfg.enable_semantic_routing = tier in (2, 3)
    return cfg


def _task(tid: str, agent: str = "data_query", sub_query: str = "q", **kw: Any) -> dict:
    return {
        "task_id": tid, "agent": agent, "sub_query": sub_query, "depends_on": [], "input_from": [],
        "order": int(tid[1:]), "status": "pending", **kw,
    }


@pytest.fixture
def semantic_backend(monkeypatch):
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")


def _names(compiled) -> set[str]:
    return set(compiled.get_graph().nodes.keys())


def _pairs(compiled) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


LOOP_NODES = {"plan", "normalize", "dispatch", TASK_RUN_NODE, "join", "replan", "finalize"}


# ──────────────────────────────────────────────
# ① 배선 — 3단에만, 플래그 off면 불변
# ──────────────────────────────────────────────

def test_flag_off_keeps_current_tier3_graph(mock_config, semantic_backend):
    off = build_graph(_cfg(mock_config, loop=False))
    assert not (_names(off) & LOOP_NODES)
    assert "sequential_runner" in _names(off)  # D-203 3단 러너(기본 on)는 그대로


def test_flag_on_registers_loop_and_replaces_sequential_runner(mock_config, semantic_backend):
    on = build_graph(_cfg(mock_config))
    off = build_graph(_cfg(mock_config, loop=False))
    assert LOOP_NODES <= _names(on)
    assert "sequential_runner" not in _names(on)
    assert _names(on) - LOOP_NODES == _names(off) - {"sequential_runner"}
    pairs = _pairs(on)
    for edge in [
        ("semantic_router", "plan"), ("plan", "normalize"), ("normalize", "dispatch"),
        ("dispatch", TASK_RUN_NODE), ("dispatch", "join"), (TASK_RUN_NODE, "join"),
        ("join", "dispatch"), ("join", "replan"), ("replan", "dispatch"), ("replan", "finalize"),
        ("finalize", "__end__"), ("semantic_router", "schema_analyzer"),
    ]:
        assert edge in pairs, edge


def test_loop_not_registered_outside_tier3(mock_config, semantic_backend):
    tier2 = build_graph(_cfg(mock_config, tier=2))
    assert not (_names(tier2) & LOOP_NODES)
    legacy = build_graph(_cfg(mock_config, tier=4))
    assert not (_names(legacy) & LOOP_NODES)
    assert "sequential_runner" in _names(legacy)  # 4단 순차 러너는 루프와 무관하게 유지


# ──────────────────────────────────────────────
# ② 진입
# ──────────────────────────────────────────────

def test_entry_conditions(mock_config):
    cfg = _cfg(mock_config)
    st = {"user_query": "서버 목록", "routing_intent": "data_query", "needs_plan": True}
    assert plan_loop_entry(st, cfg)
    marker = {**st, "needs_plan": None, "user_query": "높은 서버를 찾아 그 서버들의 CPU"}
    assert plan_loop_entry(marker, cfg)                                          # 순차 표지 OR
    assert not plan_loop_entry({**st, "needs_plan": False}, cfg)                  # 신호 없음
    assert not plan_loop_entry({**st, "routing_intent": "general_inference"}, cfg)
    assert not plan_loop_entry({**st, "template_structure": {"x": 1}}, cfg)       # 양식
    assert not plan_loop_entry(st, _cfg(mock_config, sql_approval=True))          # 승인 우회 금지
    assert not plan_loop_entry(st, _cfg(mock_config, loop=False))


def test_route_wrapper_delegates_when_not_entering(mock_config):
    cfg = _cfg(mock_config)
    delegate = lambda s: "schema_analyzer"  # noqa: E731
    assert route_after_semantic_router_plan(
        {"user_query": "x", "routing_intent": "data_query", "needs_plan": True},
        app_config=cfg, delegate=delegate,
    ) == "plan"
    assert route_after_semantic_router_plan(
        {"user_query": "x", "routing_intent": "data_query", "needs_plan": False},
        app_config=cfg, delegate=delegate,
    ) == "schema_analyzer"


# ──────────────────────────────────────────────
# 라우터 계획 신호 — off 바이트 동일
# ──────────────────────────────────────────────

class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages = messages
        return AIMessage(content=self.content)


@pytest.mark.asyncio
async def test_router_plan_signal_off_prompt_unchanged_on_adds_section(monkeypatch):
    from src.prompts.semantic_router import SEMANTIC_ROUTER_PLAN_SIGNAL_SECTION
    from src.routing.domain_config import DB_DOMAINS

    sr = importlib.import_module("src.routing.semantic_router")

    monkeypatch.setattr(sr, "_structured_backend", lambda: "none")
    domains = list(DB_DOMAINS)[:1]
    payload = '{"intent": "data_query", "needs_plan": true, "databases": []}'

    off_llm = _FakeLLM(payload)
    off = await sr._llm_classify(off_llm, "q", domains)
    assert "needs_plan" not in off
    assert off_llm.messages[0].content == sr._build_router_prompt(domains)

    on_llm = _FakeLLM(payload)
    on = await sr._llm_classify(on_llm, "q", domains, plan_signal=True)
    assert on["needs_plan"] is True
    expected = sr._build_router_prompt(domains) + SEMANTIC_ROUTER_PLAN_SIGNAL_SECTION
    assert on_llm.messages[0].content == expected


# ──────────────────────────────────────────────
# ③ normalize — 단일 출구(111 D-1)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_normalize_corrects_precheck_branch(mock_config):
    """존 선택 재진입(②.5) 같은 사전 처리 단일 task도 알람 교정을 지난다(111 §2.4)."""
    cfg = _cfg(mock_config)
    precheck = _task("t1", sub_query="심각 알람 목록", db_ids=["polestar"])
    state = {"user_query": "심각 알람 목록", "task_plan": [precheck]}
    out = await normalize(state, app_config=cfg)
    assert out["task_plan"][0]["agent"] == "alarm_query"
    assert out["is_composite"] is False


@pytest.mark.asyncio
async def test_normalize_rejects_invented_spans_with_note(mock_config):
    cfg = _cfg(mock_config)
    cfg.composite.task_frame_enabled = True
    state = {
        "user_query": COMPOSITE_Q,
        "task_plan": [
            _task("t1", "alarm_query", spans=["심각(alarm_severity='critical')"]),
            _task("t2", spans=["2026년 7월 CPU 사용률"], input_from=["t1"], depends_on=["t1"]),
        ],
    }
    out = await normalize(state, app_config=cfg)
    assert len(out["task_plan"]) == 1 and out["task_plan"][0]["sub_query"] == COMPOSITE_Q
    assert out["dependency_notes"][0]["reason"] == "task_frame_contract_violation"


# ──────────────────────────────────────────────
# ⑤ dispatch — 선행 0건 게이트(111 D-4)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dispatch_skips_dependent_of_empty_prior(mock_config):
    cfg = _cfg(mock_config)
    state = {
        "task_plan": [
            {**_task("t1", "alarm_query"), "status": "completed"},
            _task("t2", input_from=["t1"], depends_on=["t1"]),
        ],
        "task_results": {"t1": {"query_results": [], "organized_data": {"rows": []}}},
    }
    out = await dispatch(state, app_config=cfg)
    t2 = out["task_plan"][1]
    assert t2["status"] == "skipped"
    assert out["task_results"]["t2"]["skipped"] is True
    assert out["task_results"]["t2"]["skip_reason"] == "prior_empty"
    assert route_dispatch({**state, **out}) == "join"


@pytest.mark.asyncio
async def test_dispatch_fans_out_level_with_turn_routing(mock_config):
    cfg = _cfg(mock_config)
    routed = [{"db_id": "polestar", "relevance_score": 1.0, "sub_query_context": "전체"}]
    state = {
        "user_query": "a 그리고 b", "is_composite": True, "target_databases": routed,
        "is_multi_db": False, "active_db_id": "polestar", "db_scope_source": "classified",
        "task_plan": [_task("t1", sub_query="a"), _task("t2", "process_query", sub_query="b")],
        "task_results": {},
    }
    out = await dispatch(state, app_config=cfg)
    sends = route_dispatch({**state, **out})
    assert [s.node for s in sends] == [TASK_RUN_NODE, TASK_RUN_NODE]
    data_payload, handler_payload = (s.arg for s in sends)
    assert data_payload["current_task"]["task_id"] == "t1"
    assert data_payload["target_databases"] == routed and data_payload["active_db_id"] == "polestar"
    assert handler_payload["target_databases"] == []  # 데이터 task만 라우팅을 이어 받는다


# ──────────────────────────────────────────────
# ④ task_prompt — task 질의(111 D-2)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_prompt_renders_spans_and_scopes_query():
    routed = [
        {"db_id": "a", "sub_query_context": "전체 질의 A"},
        {"db_id": "b", "sub_query_context": "전체 질의 B"},
    ]
    state = {
        "original_user_query": COMPOSITE_Q, "user_query": "planner text", "is_composite": True,
        "target_databases": routed,
        "parsed_requirements": {"original_query": COMPOSITE_Q, "time_range": "x"},
        "current_task": _task("t2", spans=["2026년 7월 CPU 사용률"], input_from=["t1"]),
    }
    out = await task_prompt(state)
    assert out["user_query"] == "선행 결과 대상 중 2026년 7월 CPU 사용률"
    assert out["parsed_requirements"] == {"original_query": out["user_query"], "time_range": "x"}
    assert out["routing_intent"] == "data_query"
    assert [t["sub_query_context"] for t in out["target_databases"]] == [out["user_query"]] * 2


@pytest.mark.asyncio
async def test_task_prompt_single_task_keeps_router_targets():
    routed = [{"db_id": "a", "sub_query_context": "라우터 설명"}]
    state = {
        "original_user_query": "서버 목록", "is_composite": False, "target_databases": routed,
        "parsed_requirements": {}, "current_task": _task("t1", sub_query="서버 목록"),
    }
    out = await task_prompt(state)
    assert "target_databases" not in out and out["user_query"] == "서버 목록"


def test_task_error_keeps_reason_without_final_response():
    prose = task_error({"validation_result": {"non_sql": True}, "generated_sql": "표가 없음"})
    assert "final_response" not in prose and "표가 없음" in prose["error_message"]
    plain = task_error({"validation_result": {"reason": "검증 실패"}, "error_message": None})
    assert plain["error_message"] == "검증 실패"


def test_pack_outcome_uses_shared_result_shape():
    state = {
        "current_task": _task("t1"), "target_databases": [{"db_id": "polestar"}],
        "organized_data": {"rows": [{"hostname": "a"}]}, "query_results": [{"hostname": "a"}],
        "generated_sql": "SELECT 1", "error_message": None, "db_scope_source": "classified",
    }
    out = pack_outcome(state)["task_outcomes"]
    assert out[0]["task_id"] == "t1"
    res = out[0]["result"]
    assert res["target_db_ids"] == ["polestar"] and res["generated_sql"] == "SELECT 1"
    assert res["db_origin"] == "classified" and "error" not in res


# ──────────────────────────────────────────────
# join
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_join_records_outcomes_once_and_routes(mock_config):
    cfg = _cfg(mock_config)
    state = {
        "task_plan": [
            {**_task("t1"), "status": "in_progress"},
            {**_task("t2"), "status": "in_progress"},
            _task("t3", depends_on=["t1"]),
        ],
        "task_results": {},
        "task_outcomes": [
            {"task_id": "t1", "result": {"query_results": [{"hostname": "a"}]}},
            {"task_id": "t2", "result": {"error": "실행 실패"}},
        ],
    }
    out = await join(state, app_config=cfg)
    status = {t["task_id"]: t["status"] for t in out["task_plan"]}
    assert status == {"t1": "completed", "t2": "failed", "t3": "pending"}
    assert set(out["task_results"]) == {"t1", "t2"}
    assert route_after_join({**state, **out}) == "dispatch"


# ──────────────────────────────────────────────
# ⑥ replan — 입력 범위(111 D-3)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_replan_skips_llm_when_no_task_returned_rows(mock_config, monkeypatch):
    cfg = _cfg(mock_config)
    spy = AsyncMock()
    monkeypatch.setattr(t3, "replanner", spy)
    state = {
        "user_query": "q", "replan_count": 0, "replan_history": [],
        "task_plan": [{**_task("t1"), "status": "failed"}, {**_task("t2"), "status": "completed"}],
        "task_results": {"t1": {"error": "산문"}, "t2": {"query_results": []}},
    }
    out = await replan(state, llm=AsyncMock(), app_config=cfg)
    assert out["needs_replan"] is False and spy.await_count == 0
    assert route_after_replan({**state, **out}) == "finalize"


@pytest.mark.asyncio
async def test_replan_scopes_input_and_drops_retries(mock_config, monkeypatch):
    cfg = _cfg(mock_config)
    seen: dict = {}

    async def fake_replanner(state, *, llm, app_config):
        seen["plan"] = [t["task_id"] for t in state["task_plan"]]
        seen["results"] = sorted(state["task_results"])
        dep = {"depends_on": ["t1"], "input_from": ["t1"], "supersedes": [], "status": "pending"}
        new = [
            {"task_id": "t2", "agent": "data_query", "sub_query": "SELECT * FROM t WHERE a > 1",
             "order": 2, **dep},
            {"task_id": "t3", "agent": "alarm_query", "sub_query": "알람 다시 조회",
             "order": 3, **dep, "depends_on": [], "input_from": []},
            {"task_id": "t4", "agent": "data_query", "sub_query": "선별 CPU", "order": 4, **dep},
        ]
        return {"task_plan": state["task_plan"] + new, "needs_replan": True, "replan_count": 1,
                "replan_history": [{"count": 1, "reason": "r", "added": 3}]}

    monkeypatch.setattr(t3, "replanner", fake_replanner)
    state = {
        "user_query": "q", "replan_count": 0, "replan_history": [],
        "task_plan": [{**_task("t1", "alarm_query"), "status": "completed"},
                      {**_task("t2"), "status": "failed"}],
        "task_results": {"t1": {"query_results": [{"hostname": "a"}]}, "t2": {"error": "산문"}},
    }
    out = await replan(state, llm=AsyncMock(), app_config=cfg)
    assert seen == {"plan": ["t1"], "results": ["t1"]}          # 실패 task는 입력에서 빠진다
    added = out["task_plan"][2:]
    assert [t["sub_query"] for t in added] == ["선별 CPU"]   # SQL 문장·재시도 후속 제거
    # 전체 계획 기준 채번 — 실패 task t2와 id가 겹치지 않는다
    assert added[0]["task_id"] == "t3" and added[0]["input_from"] == ["t1"]
    assert out["needs_replan"] is True and out["replan_count"] == 1
    assert out["replan_history"][-1]["added"] == 1


@pytest.mark.asyncio
async def test_replan_cap_reports_note(mock_config):
    cfg = _cfg(mock_config)
    cfg.max_replan = 1
    state = {
        "user_query": "q", "replan_count": 1, "replan_history": [],
        "task_plan": [], "task_results": {},
    }
    out = await replan(state, llm=AsyncMock(), app_config=cfg)
    assert out["needs_replan"] is False
    assert out["dependency_notes"][0]["reason"] == "replan_cap"


# ──────────────────────────────────────────────
# ⑦ 그래프 실행 — Send 팬아웃 · 서브그래프 · defer 합류 (ainvoke · astream_events)
# ──────────────────────────────────────────────

def _fake_pipeline(monkeypatch, rows_by_intent: dict[str, list[dict]], calls: list):
    async def fake_input_parser(state, **_):
        return {"parsed_requirements": {"original_query": state["user_query"]}}

    async def fake_router(state, **_):
        target = {
            "db_id": "polestar", "relevance_score": 1.0, "sub_query_context": state["user_query"],
        }
        return {
            "target_databases": [target],
            "is_multi_db": False, "active_db_id": "polestar", "user_specified_db": None,
            "routing_intent": "data_query", "db_scope_source": "classified", "needs_plan": True,
        }

    async def fake_schema(state, **_):
        return {"schema_info": {"tables": {}}}

    async def fake_generate(state, **_):
        calls.append((state["routing_intent"], state["parsed_requirements"]["original_query"],
                      bool(state.get("prior_rows"))))
        return {"generated_sql": f"SELECT 1 -- {state['routing_intent']}"}

    async def fake_validate(state, **_):
        return {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None}}

    async def fake_execute(state, **_):
        rows = list(rows_by_intent.get(state["routing_intent"], []))
        return {"query_results": rows, "error_message": None}

    async def fake_organize(state, **_):
        rows = state.get("query_results") or []
        return {"organized_data": {
            "summary": "", "rows": rows, "column_mapping": None, "resolved_mapping": None,
            "is_sufficient": True, "sheet_mappings": None,
        }}

    async def fake_aggregate(state, **_):
        def _tag(r: dict) -> str:
            return "skipped" if r.get("skipped") else ("error" if r.get("error") else "ok")

        parts = [f"{tid}:{len(r.get('query_results') or [])}:{_tag(r)}"
                 for tid, r in sorted((state.get("task_results") or {}).items())]
        return {"final_response": " | ".join(parts), "current_node": "result_aggregator"}

    async def fake_plan_turn(state, **_):
        return {"task_plan": [
            _task("t1", "alarm_query", sub_query="현재 활성 상태인 심각 알람이 있는 서버"),
            _task("t2", sub_query="2026년 7월 CPU 사용률", input_from=["t1"], depends_on=["t1"]),
        ], "is_composite": True, "current_node": "intent_planner"}

    async def fake_replanner(state, **_):
        return {"needs_replan": False, "replan_history": [], "current_node": "replanner"}

    for name, fn in [
        ("input_parser", fake_input_parser), ("semantic_router", fake_router),
        ("schema_analyzer", fake_schema), ("query_generator", fake_generate),
        ("query_validator", fake_validate), ("query_executor", fake_execute),
        ("result_organizer", fake_organize), ("result_aggregator", fake_aggregate),
    ]:
        monkeypatch.setattr(graph_module, name, fn)
    monkeypatch.setattr(t3, "_plan_turn", fake_plan_turn)
    monkeypatch.setattr(t3, "replanner", fake_replanner)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ainvoke", "astream_events"])
async def test_graph_runs_sequential_plan_end_to_end(
    mock_config, semantic_backend, monkeypatch, mode,
):
    calls: list = []
    rows = {"alarm_query": [{"hostname": "sv-1"}], "data_query": [{"hostname": "sv-1", "cpu": 3}]}
    _fake_pipeline(monkeypatch, rows, calls)
    compiled = build_graph(_cfg(mock_config), checkpointer=InMemorySaver())
    state = create_initial_state(user_query=COMPOSITE_Q, thread_id="th-1")
    cfg = {"configurable": {"thread_id": "th-1"}}

    if mode == "ainvoke":
        final = await compiled.ainvoke(state, cfg)
    else:
        done = None
        async for ev in compiled.astream_events(state, cfg, version="v2"):
            out = (ev.get("data") or {}).get("output")
            if ev["event"] == "on_chain_end" and isinstance(out, dict) and "final_response" in out \
                    and not _is_subgraph_event(ev) and done is None:
                done = ev["name"]
        assert done == "finalize"  # SSE 종료 판정은 루트 직속 합성 노드에서 1회
        final = (await compiled.aget_state(cfg)).values

    assert final["final_response"] == "t1:1:ok | t2:1:ok"
    # t1(알람) → t2(지표, 선행 결과 스코프 주입) 순서 · task 질의로 좁혀진 SQL 입력
    assert calls == [
        ("alarm_query", "현재 활성 상태인 심각 알람이 있는 서버", False),
        ("data_query", "2026년 7월 CPU 사용률", True),
    ]
    status = {t["task_id"]: t["status"] for t in final["task_plan"]}
    assert status == {"t1": "completed", "t2": "completed"}


@pytest.mark.asyncio
async def test_graph_gate_skips_dependent_when_prior_empty(
    mock_config, semantic_backend, monkeypatch,
):
    calls: list = []
    _fake_pipeline(monkeypatch, {"alarm_query": [], "data_query": [{"hostname": "x"}]}, calls)
    compiled = build_graph(_cfg(mock_config), checkpointer=InMemorySaver())
    final = await compiled.ainvoke(
        create_initial_state(user_query=COMPOSITE_Q, thread_id="th-2"),
        {"configurable": {"thread_id": "th-2"}},
    )
    assert [c[0] for c in calls] == ["alarm_query"]  # 의존 task는 실행되지 않는다
    assert final["final_response"] == "t1:0:ok | t2:0:skipped"
    assert any(n.get("reason") == "prior_empty" for n in final.get("dependency_notes") or [])


@pytest.mark.asyncio
async def test_second_turn_starts_with_clean_loop_state(mock_config, semantic_backend, monkeypatch):
    """턴 격리 — 앞 턴의 팬인 결과·task 결과가 새 턴 합류에 섞이지 않는다(103 K-2)."""
    from src.state import create_followup_input

    calls: list = []
    rows = {"alarm_query": [{"hostname": "sv-1"}], "data_query": [{"hostname": "sv-1"}]}
    _fake_pipeline(monkeypatch, rows, calls)
    compiled = build_graph(_cfg(mock_config), checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "th-3"}}
    await compiled.ainvoke(create_initial_state(user_query=COMPOSITE_Q, thread_id="th-3"), cfg)
    second = await compiled.ainvoke(create_followup_input(COMPOSITE_Q), cfg)
    assert len(second["task_outcomes"]) == 2 and second["final_response"] == "t1:1:ok | t2:1:ok"
