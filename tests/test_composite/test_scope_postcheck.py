"""사후 대조 — `scope-postcheck` (D-203 · plans/88 §4.3 · SPEC-scope-postcheck).

LLM 폴백 SQL이 선행 스코프를 어겨 목록 밖 서버를 돌려줘도 사용자에게 도달하지 않게 하고,
스코프 안 서버 중 결과가 없는 서버를 드러낸다. 1단·2단이 같은 `apply_scope_postcheck`를 쓴다.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.deepagents_tools import _run_subagent_tool
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.state import create_initial_state
from src.utils.prior_dependency import (
    DependencyVerdict,
    apply_scope_postcheck,
    assess_scope_conformance,
    filter_outside_rows,
)


def _verdict(col="hostname", values=("a", "b", "c"), truncated=False):
    return DependencyVerdict(ok=True, scope_col=col, scope_values=list(values), scope_size=len(values),
                             truncated=truncated, source_task_ids=["t1"])


# ──────────────────────────────────────────────
# 판정 (순수)
# ──────────────────────────────────────────────

def test_outside_and_missing_detected():
    conf = assess_scope_conformance(_verdict(), [{"hostname": "a"}, {"hostname": "b"}, {"hostname": "z"}])
    assert conf.checked and conf.result_col == "hostname"
    assert conf.outside == ["z"] and conf.missing == ["c"]


def test_case_and_whitespace_insensitive():
    conf = assess_scope_conformance(_verdict(), [{"HOSTNAME": "A "}, {"HOSTNAME": "b"}, {"HOSTNAME": "C"}])
    assert conf.outside == [] and conf.missing == []


def test_no_identity_column_is_unchecked():
    conf = assess_scope_conformance(_verdict(), [{"cpu": 1}, {"cpu": 2}])
    assert conf.checked is False


def test_column_kind_mismatch_is_unchecked():
    """스코프는 hostname인데 결과에 name만 있으면 대조하지 않는다(D-061 혼합 금지 — 오제거 방지)."""
    conf = assess_scope_conformance(_verdict("hostname"), [{"server_name": "x"}])
    assert conf.checked is False
    conf2 = assess_scope_conformance(_verdict("name", ("x",)), [{"server_name": "x"}, {"server_name": "y"}])
    assert conf2.checked and conf2.outside == ["y"]


def test_truncated_scope_skips_missing():
    conf = assess_scope_conformance(_verdict(truncated=True), [{"hostname": "a"}])
    assert conf.missing == [] and conf.outside == []


def test_filter_outside_rows_keeps_input():
    rows = [{"hostname": "a"}, {"hostname": "z"}]
    out = filter_outside_rows(rows, "hostname", ["z"])
    assert out == [{"hostname": "a"}] and len(rows) == 2


def test_apply_removes_from_all_result_shapes_and_adds_note():
    res = {"query_results": [{"hostname": "a"}, {"hostname": "z"}],
           "organized_data": {"summary": "2건", "rows": [{"hostname": "a"}, {"hostname": "z"}]}}
    out = apply_scope_postcheck(_verdict(), res, "t2")
    assert out["query_results"] == [{"hostname": "a"}]
    assert out["organized_data"]["rows"] == [{"hostname": "a"}] and out["organized_data"]["summary"] == "2건"
    note = out["dependency_notes"][0]
    assert note["kind"] == "postcheck" and note["outside"] == ["z"] and note["missing"] == ["b", "c"]
    assert res["query_results"][1] == {"hostname": "z"}  # 입력 불변


def test_apply_noop_when_conformant_or_not_applicable():
    res = {"query_results": [{"hostname": "a"}, {"hostname": "b"}, {"hostname": "c"}]}
    assert apply_scope_postcheck(_verdict(), res, "t2") is res  # 이상 없음 → 그대로
    assert apply_scope_postcheck(None, res, "t2") is res
    err = {"error": "x"}
    assert apply_scope_postcheck(_verdict(), err, "t2") is err


def test_apply_all_outside_leaves_zero_rows_with_note():
    res = {"query_results": [{"hostname": "z"}]}
    out = apply_scope_postcheck(_verdict(), res, "t2")
    assert out["query_results"] == [] and "1대" in out["dependency_notes"][0]["detail"]


# ──────────────────────────────────────────────
# 2단 배선
# ──────────────────────────────────────────────

def _chain():
    return [
        {"task_id": "t1", "agent": "data_query", "sub_query": "높은 서버", "depends_on": [], "input_from": [],
         "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버들의 CPU", "depends_on": ["t1"],
         "input_from": ["t1"], "order": 2, "status": "pending"},
    ]


def _registry(handler):
    base = SUBAGENT_REGISTRY["data_query"]
    return {"data_query": SubAgentSpec(base.name, base.description, handler, fallback=base.fallback)}


async def _handler(task, isolated, **kw):
    if task["task_id"] == "t1":
        return {"query_results": [{"hostname": "a"}, {"hostname": "b"}]}
    return {"query_results": [{"hostname": "a", "cpu": 1}, {"hostname": "z", "cpu": 9}]}


@pytest.mark.asyncio
async def test_orchestrator_flag_off_keeps_outside_rows(mock_config):
    state = create_initial_state(user_query="q")
    state["task_plan"] = _chain()
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=_handler))):
        out = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)
    assert len(out["task_results"]["t2"]["query_results"]) == 2
    assert "dependency_notes" not in out["task_results"]["t2"]


@pytest.mark.asyncio
async def test_orchestrator_flag_on_removes_outside_rows(mock_config):
    """게이트 off·대조 on — 대조는 게이트와 독립으로 켤 수 있다(verdict는 항상 계산된다)."""
    mock_config.composite.scope_postcheck_enabled = True
    state = create_initial_state(user_query="q")
    state["task_plan"] = _chain()
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=_handler))):
        out = await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)
    t2 = out["task_results"]["t2"]
    assert t2["query_results"] == [{"hostname": "a", "cpu": 1}]
    assert t2["dependency_notes"][0]["outside"] == ["z"] and t2["dependency_notes"][0]["missing"] == ["b"]
    assert out["task_plan"][1]["status"] == "completed"


# ──────────────────────────────────────────────
# 1단 배선
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tool_flag_on_removes_outside_rows(mock_config, monkeypatch):
    async def handler(task, isolated, *, llm, app_config):
        return {"organized_data": {"summary": "2건", "rows": [{"hostname": "a"}, {"hostname": "z"}]}}

    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    mock_config.composite.scope_postcheck_enabled = True
    rows = [{"hostname": "a"}, {"hostname": "b"}]
    t1 = {"task_id": "tool_data_query_1", "agent": "data_query", "sub_query": "높은 서버", "depends_on": [],
          "input_from": [], "order": 1, "status": "completed"}
    collector = [(t1, {"organized_data": {"rows": rows}, "query_results": rows})]
    await _run_subagent_tool("data_query", "그 서버들의 CPU", worker_llm=AsyncMock(), app_config=mock_config,
                             ambient_state={}, collector=collector)
    _, res = collector[1]
    assert res["organized_data"]["rows"] == [{"hostname": "a"}]
    assert res["dependency_notes"][0]["kind"] == "postcheck"
