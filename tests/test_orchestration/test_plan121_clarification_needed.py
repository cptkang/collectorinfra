"""plans/121 TP-1.4 — `clarification_needed` 선언·로그만(N-3 · G-5 · D-270 ⑤).

고정하는 계약:
① `AgentState`에 선언돼 LangGraph가 버리지 않는다(종전: 미선언이라 노드 반환에서 사라졌다).
② 두 상태 생성 함수가 None으로 초기화한다 — 선언하면 체크포인터에 남아 턴 간 누수된다(§12.5).
③ 2단 계획 출구가 매 턴 쓴다(있으면 값 · 없으면 None).
④ 로그에는 내용 없이 질문 길이·선택지 수만 남는다.
⑤ 소비처는 없다(plans/106 H1과 함께) — 응답·재계획 판정 불변.
"""

from __future__ import annotations

import importlib
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.graph import END, START, StateGraph

from src.state import AgentState, create_followup_input, create_initial_state

# 패키지 `src.orchestration`이 같은 이름의 노드 함수를 재노출해 `import … as`가 함수를 가리킨다.
ip = importlib.import_module("src.orchestration.intent_planner")

_CLAR = {
    "question": "어느 존의 서버를 볼까요?", "options": ["김포", "여의도"], "reason": "위치 모호",
}


def test_declared_in_agent_state():
    assert "clarification_needed" in AgentState.__annotations__


def test_both_state_constructors_reset_to_none():
    assert create_initial_state("질의")["clarification_needed"] is None
    delta = create_followup_input("후속 질의")
    assert "clarification_needed" in delta and delta["clarification_needed"] is None


def test_langgraph_keeps_declared_key():
    """노드가 쓴 값이 그래프 상태에 남는다(미선언 키였다면 드롭됐다)."""
    def _node(_state):
        return {"clarification_needed": _CLAR}

    graph = StateGraph(AgentState)
    graph.add_node("n", _node)
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    out = graph.compile().invoke(create_initial_state("질의"))
    assert out["clarification_needed"] == _CLAR


def test_second_turn_delta_clears_previous_value():
    """1턴 값이 남은 상태에 2턴 델타를 병합하면 None — 턴 간 누수가 없다."""
    turn1 = {**create_initial_state("질의"), "clarification_needed": _CLAR}
    merged = {**turn1, **create_followup_input("다음 질의")}
    assert merged["clarification_needed"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("planned", "expected"),
    [({"clarification_needed": _CLAR}, _CLAR), ({}, None),
     ({"clarification_needed": {}}, None)],
)
async def test_plan_exit_always_writes(monkeypatch, planned, expected):
    async def _fake_plan_turn(state, *, llm=None, app_config=None):
        return {"task_plan": [{"task_id": "t1", "agent": "general_inference",
                               "sub_query": "q", "depends_on": [], "input_from": []}],
                **planned}

    monkeypatch.setattr(ip, "_plan_turn", _fake_plan_turn)
    out = await ip.intent_planner(create_initial_state("질의"), llm=AsyncMock(),
                                  app_config=MagicMock())
    assert "clarification_needed" in out
    assert out["clarification_needed"] == expected


@pytest.mark.asyncio
async def test_log_has_counts_not_content(monkeypatch, caplog):
    async def _fake_plan_turn(state, *, llm=None, app_config=None):
        return {"task_plan": [], "clarification_needed": _CLAR}

    monkeypatch.setattr(ip, "_plan_turn", _fake_plan_turn)
    with caplog.at_level(logging.INFO, logger=ip.logger.name):
        await ip.intent_planner(create_initial_state("질의"), llm=AsyncMock(),
                                app_config=MagicMock())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert f"질문 {len(_CLAR['question'])}자 · 선택지 2개" in text
    assert _CLAR["question"] not in text and "김포" not in text
