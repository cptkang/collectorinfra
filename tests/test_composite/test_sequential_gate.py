"""선행 결과 게이트 — `prior-dependency-gate` (D-203 · plans/88 §4.1 · SPEC-prior-dependency-gate).

현행 결함(plans/88 §3 R-1): 선행 task가 0건이면 후속 task가 **스코프 없이** 실행돼 전체 서버를
조회한다. 이 파일은 그 결함을 "플래그 off = 현행" 케이스로 먼저 고정하고, 플래그 on에서
후속이 실행되지 않음을 1단·2단 양쪽에서 단언한다.
"""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, patch

import pytest

from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.deepagents_tools import _run_subagent_tool
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec
from src.state import create_initial_state
from src.utils.prior_dependency import (
    REASON_PRIOR_EMPTY,
    REASON_PRIOR_FAILED,
    REASON_PRIOR_NO_IDENTITY,
    assess_prior_dependency,
    skip_result,
    verdict_note,
)

# ──────────────────────────────────────────────
# 판정 함수 (순수)
# ──────────────────────────────────────────────

T2 = {"task_id": "t2", "agent": "data_query", "input_from": ["t1"], "depends_on": ["t1"]}


def test_no_input_from_is_not_assessed():
    assert assess_prior_dependency({"task_id": "t1", "input_from": []}, {}) is None


def test_prior_error_is_failed():
    v = assess_prior_dependency(T2, {"t1": {"error": "DB 연결 실패"}})
    assert v is not None and not v.ok and v.reason == REASON_PRIOR_FAILED
    assert "t1" in v.detail and "DB 연결 실패" in v.detail


def test_missing_prior_result_is_failed():
    v = assess_prior_dependency(T2, {})
    assert v is not None and v.reason == REASON_PRIOR_FAILED


@pytest.mark.parametrize("res", [
    {"rows": []},
    {"query_results": []},
    {"organized_data": {"summary": "없음", "rows": []}},
])
def test_prior_zero_rows_is_empty(res):
    v = assess_prior_dependency(T2, {"t1": res})
    assert v is not None and not v.ok and v.reason == REASON_PRIOR_EMPTY
    assert "0건" in v.detail


def test_rows_without_identity_column():
    v = assess_prior_dependency(T2, {"t1": {"query_results": [{"cpu": 91.2}, {"cpu": 88.0}]}})
    assert v is not None and v.reason == REASON_PRIOR_NO_IDENTITY


def test_hostname_rows_ok_with_scope():
    rows = [{"hostname": f"sv{i}", "cpu": 90 + i} for i in range(7)]
    v = assess_prior_dependency(T2, {"t1": {"query_results": rows}})
    assert v is not None and v.ok and v.scope_col == "hostname" and v.scope_size == 7
    assert v.scope_values == [f"sv{i}" for i in range(7)] and not v.truncated
    assert "7대" in v.detail


def test_truncation_reported_not_silent():
    rows = [{"hostname": f"sv{i:03d}"} for i in range(130)]
    v = assess_prior_dependency(T2, {"t1": {"query_results": rows}})
    assert v is not None and v.ok and v.truncated and v.truncated_count == 30 and v.scope_size == 100
    assert "30대" in v.detail


def test_multi_source_one_failed_is_failed():
    """부분 스코프로 조용히 좁히지 않는다(SPEC 가정 3)."""
    task = {"task_id": "t3", "input_from": ["t1", "t2"]}
    prior = {"t1": {"query_results": [{"hostname": "a"}]}, "t2": {"error": "x"}}
    v = assess_prior_dependency(task, prior)
    assert v is not None and v.reason == REASON_PRIOR_FAILED and "t2" in v.detail


def test_verdict_note_and_skip_result_shapes():
    v = assess_prior_dependency(T2, {"t1": {"query_results": []}})
    note = verdict_note(v, "t2")
    assert note["kind"] == "gate" and note["task_id"] == "t2" and note["reason"] == REASON_PRIOR_EMPTY
    res = skip_result(v, guidance="재호출 금지")
    assert res["skipped"] is True and res["skip_reason"] == REASON_PRIOR_EMPTY
    assert res["error"].endswith("재호출 금지")
    ok = assess_prior_dependency(T2, {"t1": {"query_results": [{"hostname": "a"}]}})
    tn = verdict_note(ok, "t2")
    assert tn["kind"] == "trace" and tn["scope_size"] == 1 and tn["sample"] == ["a"]


# ──────────────────────────────────────────────
# 2단 — agent_orchestrator 배선
# ──────────────────────────────────────────────

def _state(tasks):
    s = create_initial_state(user_query="CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률")
    s["task_plan"] = tasks
    return s


def _chain():
    return [
        {"task_id": "t1", "agent": "data_query", "sub_query": "CPU 사용률이 높은 서버",
         "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "선행 결과 서버들의 최근 1개월 CPU",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "pending"},
    ]


def _registry(handler):
    base = SUBAGENT_REGISTRY["data_query"]
    return {"data_query": SubAgentSpec(base.name, base.description, handler, fallback=base.fallback)}


@pytest.mark.asyncio
async def test_orchestrator_flag_off_keeps_current_behavior(mock_config):
    """현행 재현: 플래그 off면 t1이 0건이어도 t2가 실행된다(무스코프 — R-1)."""
    # .env 누수 차단(CLAUDE.md Known Mistakes) — 운영 .env가 2026-09-10부터 1차 3종을 on으로 두므로 off를 명시한다.
    mock_config.composite.sequential_gate_enabled = False
    mock_config.composite.scope_postcheck_enabled = False
    mock_config.composite.prior_scope_by_db_enabled = False
    calls: list[str] = []

    async def handler(task, isolated, **kw):
        calls.append(task["task_id"])
        if task["task_id"] == "t1":
            return {"query_results": []}
        assert isolated.get("prior_rows") == {"t1": []}
        return {"query_results": [{"hostname": "any", "cpu": 1}]}

    assert mock_config.composite.sequential_gate_enabled is False
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(_state(_chain()), llm=AsyncMock(), app_config=mock_config)
    assert calls == ["t1", "t2"]
    assert [t["status"] for t in out["task_plan"]] == ["completed", "completed"]
    assert "dependency_notes" not in out


@pytest.mark.asyncio
async def test_orchestrator_flag_on_skips_dependent_when_prior_empty(mock_config):
    calls: list[str] = []

    async def handler(task, isolated, **kw):
        calls.append(task["task_id"])
        return {"query_results": []}

    mock_config.composite.sequential_gate_enabled = True
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(_state(_chain()), llm=AsyncMock(), app_config=mock_config)
    assert calls == ["t1"]  # t2 handler는 호출되지 않는다
    t2 = out["task_plan"][1]
    assert t2["status"] == "skipped"
    res = out["task_results"]["t2"]
    assert res["skipped"] is True and res["skip_reason"] == REASON_PRIOR_EMPTY and res["error"]
    notes = out["dependency_notes"]
    assert len(notes) == 1 and notes[0]["kind"] == "gate" and notes[0]["task_id"] == "t2"


@pytest.mark.asyncio
async def test_orchestrator_flag_on_skips_when_prior_failed(mock_config):
    async def handler(task, isolated, **kw):
        if task["task_id"] == "t1":
            raise RuntimeError("DB down")
        return {"query_results": [{"hostname": "x"}]}

    mock_config.composite.sequential_gate_enabled = True
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(_state(_chain()), llm=AsyncMock(), app_config=mock_config)
    assert out["task_plan"][0]["status"] == "failed"
    assert out["task_plan"][1]["status"] == "skipped"
    assert out["task_results"]["t2"]["skip_reason"] == REASON_PRIOR_FAILED


@pytest.mark.asyncio
async def test_orchestrator_flag_on_runs_dependent_when_prior_ok_and_records_trace(mock_config):
    captured = {}

    async def handler(task, isolated, **kw):
        if task["task_id"] == "t1":
            return {"query_results": [{"hostname": "sv1"}, {"hostname": "sv2"}]}
        captured["prior_rows"] = isolated.get("prior_rows")
        return {"query_results": [{"hostname": "sv1", "cpu": 1}]}

    mock_config.composite.sequential_gate_enabled = True
    with patch.dict(SUBAGENT_REGISTRY, _registry(AsyncMock(side_effect=handler))):
        out = await agent_orchestrator(_state(_chain()), llm=AsyncMock(), app_config=mock_config)
    assert [t["status"] for t in out["task_plan"]] == ["completed", "completed"]
    assert captured["prior_rows"] == {"t1": [{"hostname": "sv1"}, {"hostname": "sv2"}]}
    notes = out["dependency_notes"]
    assert notes[0]["kind"] == "trace" and notes[0]["scope_size"] == 2 and notes[0]["scope_col"] == "hostname"


# ──────────────────────────────────────────────
# 1단 — deepagents 도구 배선
# ──────────────────────────────────────────────

def _t1(agent="data_query"):
    return {"task_id": "tool_data_query_1", "agent": agent, "sub_query": "CPU 사용률이 높은 서버",
            "depends_on": [], "input_from": [], "order": 1, "status": "completed"}


@pytest.mark.asyncio
async def test_tool_flag_off_runs_unscoped_when_prior_empty(mock_config, monkeypatch):
    """현행 재현: 선행 0건이면 후보가 없어 input_from이 비고, 후속은 무스코프로 실행된다."""
    # .env 누수 차단(CLAUDE.md Known Mistakes) — 운영 .env가 2026-09-10부터 1차 3종을 on으로 두므로 off를 명시한다.
    mock_config.composite.sequential_gate_enabled = False
    mock_config.composite.scope_postcheck_enabled = False
    mock_config.composite.prior_scope_by_db_enabled = False
    captured = {}

    async def handler(task, isolated, *, llm, app_config):
        captured["input_from"] = task.get("input_from")
        captured["prior_rows"] = isolated.get("prior_rows")
        return {"organized_data": {"summary": "전체", "rows": [{"hostname": "any"}]}}

    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    collector = [(_t1(), {"organized_data": {"summary": "0건", "rows": []}, "query_results": []})]
    await _run_subagent_tool(
        "data_query", "그 서버들의 최근 1개월 CPU 사용률", worker_llm=AsyncMock(),
        app_config=mock_config, ambient_state={}, collector=collector,
    )
    assert captured["input_from"] == [] and captured["prior_rows"] is None
    assert len(collector) == 2 and collector[1][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_tool_flag_on_skips_when_prior_empty(mock_config, monkeypatch):
    handler = AsyncMock(return_value={"organized_data": {"rows": [{"hostname": "any"}]}})
    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    mock_config.composite.sequential_gate_enabled = True
    collector = [(_t1(), {"organized_data": {"summary": "0건", "rows": []}, "query_results": []})]
    text = await _run_subagent_tool(
        "data_query", "그 서버들의 최근 1개월 CPU 사용률", worker_llm=AsyncMock(),
        app_config=mock_config, ambient_state={}, collector=collector,
    )
    handler.assert_not_called()
    task, res = collector[1]
    assert task["status"] == "skipped" and res["skip_reason"] == REASON_PRIOR_EMPTY
    assert task["dependency_note"]["kind"] == "gate"
    assert "다시 호출하지" in text and "[실패]" in text


@pytest.mark.asyncio
async def test_tool_flag_on_skips_when_prior_failed(mock_config, monkeypatch):
    handler = AsyncMock(return_value={"organized_data": {"rows": [{"hostname": "any"}]}})
    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    mock_config.composite.sequential_gate_enabled = True
    collector = [(_t1(), {"error": "DB 연결 실패"})]
    await _run_subagent_tool(
        "data_query", "해당 서버들의 CPU", worker_llm=AsyncMock(),
        app_config=mock_config, ambient_state={}, collector=collector,
    )
    handler.assert_not_called()
    assert collector[1][1]["skip_reason"] == REASON_PRIOR_FAILED


@pytest.mark.asyncio
async def test_tool_flag_on_global_scope_marker_bypasses_gate(mock_config, monkeypatch):
    """"전체 서버"처럼 전역 범위를 명시하면 선행이 비어도 게이트가 개입하지 않는다(D-095 차단어 우선)."""
    handler = AsyncMock(return_value={"organized_data": {"rows": [{"hostname": "any"}]}})
    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    mock_config.composite.sequential_gate_enabled = True
    collector = [(_t1(), {"organized_data": {"rows": []}, "query_results": []})]
    await _run_subagent_tool(
        "data_query", "전체 서버의 최근 1개월 CPU 사용률", worker_llm=AsyncMock(),
        app_config=mock_config, ambient_state={}, collector=collector,
    )
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_tool_flag_on_prior_ok_runs_with_scope_and_trace(mock_config, monkeypatch):
    captured = {}

    async def handler(task, isolated, *, llm, app_config):
        captured["prior_rows"] = isolated.get("prior_rows")
        return {"organized_data": {"rows": [{"hostname": "sv1", "cpu": 1}]}}

    monkeypatch.setitem(SUBAGENT_REGISTRY, "data_query", SubAgentSpec("data_query", "DB", handler))
    mock_config.composite.sequential_gate_enabled = True
    rows = [{"hostname": "sv1"}, {"hostname": "sv2"}]
    collector = [(_t1(), {"organized_data": {"rows": rows}, "query_results": rows})]
    await _run_subagent_tool(
        "data_query", "그 서버들의 최근 1개월 CPU", worker_llm=AsyncMock(),
        app_config=mock_config, ambient_state={}, collector=collector,
    )
    assert captured["prior_rows"] == {"tool_data_query_1": rows}
    task = collector[1][0]
    assert task["status"] == "completed" and task["dependency_note"]["kind"] == "trace"


def test_both_tiers_use_the_same_gate_function():
    """1단·2단 대칭 고정 — 한쪽만 고치는 비대칭 방지(Known Mistakes)."""
    orch_mod = importlib.import_module("src.orchestration.agent_orchestrator")
    tools_mod = importlib.import_module("src.orchestration.deepagents_tools")
    assert orch_mod.assess_prior_dependency is assess_prior_dependency
    assert tools_mod.assess_prior_dependency is assess_prior_dependency


# ──────────────────────────────────────────────
# 선별 기준 표기 (G-2 · plans/88 §11 · 2026-09-10 사용자 확정) — 선행 SQL에서 결정적 추출(LLM 0회)
# ──────────────────────────────────────────────

from src.utils.prior_dependency import extract_selection_basis  # noqa: E402


@pytest.mark.parametrize("sql,expected", [
    ("SELECT hostname, cpu_usage FROM t ORDER BY cpu_usage DESC LIMIT 10", "정렬 cpu_usage 내림차순 · 상한 10건"),
    ("SELECT hostname FROM POLESTAR.T ORDER BY cpu_usage DESC FETCH FIRST 5 ROWS ONLY", "정렬 cpu_usage 내림차순 · 상한 5건"),
    ("SELECT hostname FROM t ORDER BY AVG(cpu), hostname LIMIT 3", "정렬 cpu 오름차순 · 상한 3건"),
    # 결정적 컴파일 경로의 인용 별칭 + 기본 상한(2026-09-10 실측 형태) — "상위 10000건"으로 단정하지 않는다
    ('SELECT MAX(c.name) AS "name", ROUND(MAX(s.max_val)::numeric, 2) AS "cpus_max" FROM t '
     'ORDER BY "cpus_max" DESC NULLS LAST LIMIT 10000;', "정렬 cpus_max 내림차순 · 상한 10000건"),
    ("SELECT hostname FROM t WHERE cpu_usage >= 80 LIMIT 1000", "조건 cpu_usage >= 80"),
    ("SELECT hostname FROM t GROUP BY hostname "
     "HAVING MAX(CASE WHEN c.resource_type='server' THEN c.cpu_usage END) >= 80.5", "조건 cpu_usage >= 80.5"),
    ("SELECT hostname FROM t WHERE a = 'x' AND cpu >= 80 ORDER BY cpu DESC LIMIT 10",
     "정렬 cpu 내림차순 · 상한 10건 · 조건 cpu >= 80"),
    # 기간 컬럼 비교는 선별 임계가 아니다
    ("SELECT hostname FROM t WHERE stat_month >= 202608 ORDER BY hostname LIMIT 1000", "정렬 hostname 오름차순 · 상한 1000건"),
    # ORDER BY 없는 LIMIT은 기본 상한이지 선별 기준이 아니다 · `<>`는 임계가 아니다
    ("SELECT hostname FROM t WHERE x <> 5 LIMIT 1000", ""),
    ("", ""),
    (None, ""),
])
def test_extract_selection_basis(sql, expected):
    assert extract_selection_basis(sql) == expected


def test_verdict_carries_selection_basis_from_prior_sql():
    rows = [{"hostname": "a"}, {"hostname": "b"}]
    sql = "SELECT hostname FROM t ORDER BY cpu DESC LIMIT 2"
    v = assess_prior_dependency(T2, {"t1": {"query_results": rows, "generated_sql": sql}})
    assert v is not None and v.ok and v.selection_basis == "정렬 cpu 내림차순 · 상한 2건"
    assert "선별 기준: 정렬 cpu 내림차순 · 상한 2건" in v.detail
    assert verdict_note(v, "t2")["selection_basis"] == v.selection_basis


def test_verdict_without_sql_has_no_basis():
    v = assess_prior_dependency(T2, {"t1": {"query_results": [{"hostname": "a"}]}})
    assert v is not None and v.ok and v.selection_basis == "" and "선별 기준" not in v.detail
    assert "selection_basis" not in verdict_note(v, "t2")


# ──────────────────────────────────────────────
# 1단 차단어 축 한정 (plans/88 §11.6 (d) · 2026-09-10 확정) — 실측 sub_query 고정
# ──────────────────────────────────────────────

from src.orchestration.deepagents_tools import _dependency_scope, _is_global_scope  # noqa: E402

# 2026-09-10 1단 실 실행에서 게이트를 우회시킨 오케스트레이터 재표현 문구(§11.5 단계 2'')
REAL_SUBQUERY = "2026년 7월 전체 일자별 CPU 사용률 상위 서버 상세 통계 (dbora023, cocm-hdkapp01 등)"


@pytest.mark.parametrize("q,expected", [
    ("전체 서버의 최근 1개월 CPU 사용률", True),
    ("모든 장비의 OS 종류", True),
    ("전 서버 목록", True),
    ("전체호스트 상태", True),
    (REAL_SUBQUERY.lower(), False),          # "전체 일자별" — 기간 축
    ("모든 월의 평균 CPU", False),            # "모든 월" — 기간 축
    ("전체 기간 디스크 사용률", False),
    ("그 서버들의 CPU", False),
    ("", False),
])
def test_is_global_scope_axis_bound(q, expected):
    assert _is_global_scope(q) is expected


def test_dependency_scope_not_blocked_by_period_axis_global_word():
    """실측 sub_query: 차단어가 아니므로 G3("상위")로 선행 결과가 주입된다(종전엔 차단 → input_from=[])."""
    t1 = {"task_id": "tool_data_query_1", "agent": "data_query", "sub_query": "CPU 사용률이 높은 서버", "depends_on": [],
          "input_from": [], "order": 1, "status": "completed"}
    rows = [{"name": "DB-ORA-023", "hostname": "dbora023"}, {"name": "cocm-hdkapp01", "hostname": "cocm-hdkapp01"}]
    collector = [(t1, {"organized_data": {"rows": rows}, "query_results": rows})]
    input_from, prior = _dependency_scope(REAL_SUBQUERY, collector)
    assert input_from == ["tool_data_query_1"] and "tool_data_query_1" in prior


def test_dependency_scope_still_blocked_for_server_axis_global():
    t1 = {"task_id": "tool_data_query_1", "agent": "data_query", "sub_query": "x", "depends_on": [],
          "input_from": [], "order": 1, "status": "completed"}
    rows = [{"hostname": "a"}]
    collector = [(t1, {"organized_data": {"rows": rows}, "query_results": rows})]
    assert _dependency_scope("전체 서버의 최근 1개월 CPU 사용률 상위", collector) == ([], {})
