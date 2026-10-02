"""사유 채널 — `dependency-notes` (D-203 · plans/88 §4.4·§4.10 · SPEC-dependency-notes).

현행 결함(plans/88 §2.2): `sufficiency_shortfalls`는 AgentState에 선언돼 있지 않고 읽는 곳도 없어
78 W5-3의 "미충족 사유 노출"이 응답에 닿지 않는다. 이 파일은 노트 채널이 생산자 → 집계기 →
API 응답까지 실제로 이어지는지를 고정한다.
"""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.api.schemas import QueryResponse
from src.orchestration.result_aggregator import _apply_incomplete_notice, result_aggregator
from src.orchestration.sufficiency import REASON_TARGET_SHORTFALL
from src.state import create_followup_input, create_initial_state
from src.utils.prior_dependency import NOTE_SUFFICIENCY, render_dependency_notes

ao = importlib.import_module("src.orchestration.agent_orchestrator")
agg_mod = sys.modules["src.orchestration.result_aggregator"]

GATE_NOTE = {"kind": "gate", "task_id": "t2", "reason": "prior_empty",
             "detail": "선행 작업(t1)의 결과가 0건이라 이 단계를 실행하지 않았습니다."}


# ──────────────────────────────────────────────
# 렌더 · 상태
# ──────────────────────────────────────────────

def test_render_empty_is_empty_string():
    assert render_dependency_notes(None) == "" and render_dependency_notes([]) == ""
    assert render_dependency_notes([{"kind": "gate"}]) == ""  # detail 없는 노트는 무시


def test_render_block_lists_notes_with_task_prefix():
    block = render_dependency_notes([GATE_NOTE, {"kind": "sufficiency", "detail": "대상 3건 중 1건만 조사됐습니다"}])
    assert block.startswith("## 순차 처리 경과")
    assert "- [t2] 선행 작업(t1)의 결과가 0건" in block
    assert "- 대상 3건 중 1건만" in block


def test_state_declares_and_initializes_request_scope():
    s = create_initial_state(user_query="q")
    assert "dependency_notes" in s and s["dependency_notes"] is None
    assert create_followup_input("다음 턴")["dependency_notes"] is None


# ──────────────────────────────────────────────
# 집계기 — 응답 말미 블록 (2단 · 1단 shape)
# ──────────────────────────────────────────────

def test_apply_notes_no_notes_is_byte_identical():
    state = create_initial_state(user_query="q")
    result = {"final_response": "본문"}
    assert _apply_incomplete_notice(result, state) == {"final_response": "본문"}


def test_apply_notes_from_state_appends_block_and_returns_notes():
    state = create_initial_state(user_query="q")
    state["dependency_notes"] = [GATE_NOTE]
    out = _apply_incomplete_notice({"final_response": "본문"}, state)
    assert out["final_response"].startswith("본문\n\n---\n## 순차 처리 경과")
    assert "[t2]" in out["final_response"]
    assert out["dependency_notes"] == [GATE_NOTE]


def test_apply_notes_from_task_plan_note_deep_agent_shape():
    """1단은 노트를 task dict(`dependency_note`)에 싣는다 — 집계기가 task_plan에서 모은다."""
    state = create_initial_state(user_query="q")
    state["task_plan"] = [{"task_id": "tool_data_query_2", "agent": "data_query",
                           "dependency_note": dict(GATE_NOTE, task_id="tool_data_query_2")}]
    out = _apply_incomplete_notice({"final_response": "본문"}, state)
    assert "[tool_data_query_2]" in out["final_response"]


def test_apply_notes_dedups_and_keeps_incomplete_notice_first():
    state = create_initial_state(user_query="q")
    state["dependency_notes"] = [GATE_NOTE, dict(GATE_NOTE)]
    state["orchestration_incomplete_notice"] = "미실행 작업이 있습니다"
    out = _apply_incomplete_notice({"final_response": "본문"}, state)
    assert out["final_response"].count("선행 작업(t1)") == 1
    assert out["final_response"].index("미실행 작업") < out["final_response"].index("순차 처리 경과")


@pytest.mark.asyncio
async def test_result_aggregator_single_task_carries_block(mock_config):
    state = create_initial_state(user_query="q")
    state["task_plan"] = [{"task_id": "t1", "agent": "general_inference", "sub_query": "q", "order": 1,
                           "status": "completed"}]
    state["task_results"] = {"t1": {"final_response": "답변"}}
    state["dependency_notes"] = [GATE_NOTE]
    with patch.object(agg_mod, "output_generator", new=AsyncMock()):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)
    assert "## 순차 처리 경과" in out["final_response"] and out["dependency_notes"] == [GATE_NOTE]
    # 대화 이력에도 블록이 포함된 최종 텍스트가 실린다(다음 턴 근거)
    assert "순차 처리 경과" in out["messages"][0].content


# ──────────────────────────────────────────────
# 현행 결함 재현 — 충족도 미달 사유가 채널에 실린다
# ──────────────────────────────────────────────

THREE = [{"hostname": "h1"}, {"hostname": "h2"}, {"hostname": "h3"}]


def _result(succeeded, hostnames):
    return {"process_query": {"succeeded_count": succeeded},
            "query_results": [{"hostname": h} for h in hostnames]}


@pytest.mark.asyncio
async def test_sufficiency_shortfall_reaches_dependency_notes(monkeypatch):
    """`sufficiency_shortfalls`(병기 유지)와 함께 `dependency_notes`에 같은 사유가 실린다."""
    async def _fake_run(task, state, llm, app_config, *, prior, injected=None):
        if injected is not None:
            injected[task["task_id"]] = list(THREE)
        return _result(1, ["h1"])

    monkeypatch.setattr(ao, "_run_agent", _fake_run)
    out = await ao.agent_orchestrator(
        {"task_plan": [{"task_id": "t2", "agent": "process_query", "sub_query": "프로세스", "status": "pending"}],
         "task_results": {}},
        llm=object(), app_config=SimpleNamespace(),
    )
    assert out["sufficiency_shortfalls"][0]["reason"] == REASON_TARGET_SHORTFALL  # 병기 유지
    notes = out["dependency_notes"]
    assert len(notes) == 1 and notes[0]["kind"] == NOTE_SUFFICIENCY and "3건 중 1건" in notes[0]["detail"]


@pytest.mark.asyncio
async def test_shortfall_note_appears_in_final_response(mock_config):
    """★ 현행 0건 — 미충족 사유가 최종 응답 본문에 실제로 나타난다."""
    state = create_initial_state(user_query="q")
    state["task_plan"] = [{"task_id": "t2", "agent": "general_inference", "sub_query": "q", "order": 1,
                           "status": "completed"}]
    state["task_results"] = {"t2": {"final_response": "프로세스 1건"}}
    state["dependency_notes"] = [{"kind": NOTE_SUFFICIENCY, "task_id": "t2", "reason": "sufficiency_shortfall",
                                  "detail": "대상 3건 중 1건만 조사됐습니다"}]
    with patch.object(agg_mod, "output_generator", new=AsyncMock()):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)
    assert "대상 3건 중 1건만 조사됐습니다" in out["final_response"]


# ──────────────────────────────────────────────
# API 스키마
# ──────────────────────────────────────────────

def test_query_response_exposes_dependency_notes():
    r = QueryResponse(query_id="q", status="completed", response="x", dependency_notes=[GATE_NOTE])
    assert r.dependency_notes == [GATE_NOTE]
    assert QueryResponse(query_id="q", status="completed", response="x").dependency_notes is None
