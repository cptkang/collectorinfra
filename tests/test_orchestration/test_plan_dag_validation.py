"""분해 계약 — `plan-dag-validation` + `sequential-decompose` (D-203 · plans/88 §4.2-b·§4.8 · W2·W8).

현행 결함(plans/88 §2.3): backend `none` 경로는 DAG 검증이 없어 순환·미존재 참조가
`topological_levels`의 "한 레벨 안전 처리"로 **조용히 병렬**이 된다. 되먹임은 총 1회.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.orchestration.intent_planner import _llm_decompose, intent_planner
from src.orchestration.schemas import validate_plan_dag
from src.state import create_initial_state
from src.utils.prior_dependency import has_sequential_marker

SEQ_QUERY = "CPU 사용률이 높은 서버를 찾아 그 서버들의 최근 1개월 CPU 사용률을 보여줘"


# ──────────────────────────────────────────────
# validate_plan_dag (순수)
# ──────────────────────────────────────────────

def _t(tid, deps=(), inputs=(), agent="data_query"):
    return {"task_id": tid, "agent": agent, "sub_query": tid, "depends_on": list(deps), "input_from": list(inputs)}


def test_valid_chain_has_no_violations():
    tasks, violations, fixes = validate_plan_dag([_t("t1"), _t("t2", ["t1"], ["t1"])])
    assert violations == [] and fixes == [] and tasks[1]["depends_on"] == ["t1"]


def test_input_from_not_in_depends_on_is_autofixed():
    tasks, violations, fixes = validate_plan_dag([_t("t1"), _t("t2", [], ["t1"])])
    assert violations == [] and tasks[1]["depends_on"] == ["t1"] and fixes and "t2" in fixes[0]


def test_unknown_reference_is_violation():
    _, violations, _ = validate_plan_dag([_t("t1"), _t("t2", ["t9"], ["t9"])])
    assert any("t9" in v for v in violations)


def test_duplicate_ids_and_self_reference_are_violations():
    _, violations, _ = validate_plan_dag([_t("t1"), _t("t1", ["t1"])])
    assert any("중복" in v for v in violations) and any("자기 참조" in v for v in violations)


def test_cycle_is_violation():
    _, violations, _ = validate_plan_dag([_t("t1", ["t2"]), _t("t2", ["t1"])])
    assert any("순환" in v for v in violations)


def test_validate_does_not_mutate_input():
    src = [_t("t1"), _t("t2", [], ["t1"])]
    validate_plan_dag(src)
    assert src[1]["depends_on"] == []


# ──────────────────────────────────────────────
# has_sequential_marker 정오표 (오탐 케이스 포함)
# ──────────────────────────────────────────────

@pytest.mark.parametrize("q", [
    SEQ_QUERY,
    "심각 알람 서버를 조회한 뒤 해당 서버들의 메모리를 보여줘",
    "디스크 상위 5대를 뽑고 그 중 메모리가 가장 높은 서버",
    "김포 서버를 찾아서 그 서버들의 CPU",  # "찾아서" + "그 서버"
])
def test_marker_hits(q):
    assert has_sequential_marker(q)


@pytest.mark.parametrize("q", [
    "김포 서버를 찾아줘",  # "찾아줘"는 표지가 아니다 — 단일 질의는 재분해조차 하지 않는다(좁게 못 박음)
    "전체 서버의 OS 종류와 버전을 보여줘",
    "CPU 사용률이 80% 이상인 서버 목록",
    "여의도 개발 폴스타의 서버 수",
    "",
])
def test_marker_misses(q):
    assert not has_sequential_marker(q)


# ──────────────────────────────────────────────
# _llm_decompose — 되먹임 1회 · 폴백 사유
# ──────────────────────────────────────────────

def _plan(tasks):
    return json.dumps({"tasks": tasks}, ensure_ascii=False)


def _llm(*contents):
    llm = AsyncMock()
    llm.ainvoke.side_effect = [MagicMock(content=c) for c in contents]
    return llm


SINGLE = _plan([{"task_id": "t1", "agent": "data_query", "sub_query": SEQ_QUERY}])
CHAIN = _plan([
    {"task_id": "t1", "agent": "data_query", "sub_query": "CPU 높은 서버"},
    {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버들의 1개월 CPU", "depends_on": ["t1"], "input_from": ["t1"]},
])
BROKEN = _plan([
    {"task_id": "t1", "agent": "data_query", "sub_query": "a"},
    {"task_id": "t2", "agent": "data_query", "sub_query": "b", "depends_on": ["t9"], "input_from": ["t9"]},
])


@pytest.mark.asyncio
async def test_flags_off_single_call_and_byte_identical(mock_config):
    # 기본값이 on(2026-09-10)이므로 off 전제는 명시로 고정한다(.env 누수·기본값 변경 무관)
    mock_config.composite.plan_dag_validation_enabled = False
    mock_config.composite.sequential_replan_enabled = False
    llm = _llm(BROKEN)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 1
    assert out["tasks"][1]["input_from"] == ["t9"] and "degraded" not in out  # 현행 그대로(검증 없음)


@pytest.mark.asyncio
async def test_dag_violation_triggers_one_retry_then_ok(mock_config):
    mock_config.composite.plan_dag_validation_enabled = True
    llm = _llm(BROKEN, CHAIN)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 2
    assert [t["task_id"] for t in out["tasks"]] == ["t1", "t2"] and "degraded" not in out
    # 되먹임은 마지막 HumanMessage 말미에만 붙는다(시스템 접두 불변)
    retry_msgs = llm.ainvoke.await_args_list[1].args[0]
    assert retry_msgs[0].content == llm.ainvoke.await_args_list[0].args[0][0].content
    assert "## 재요청 사유" in retry_msgs[-1].content and "t9" in retry_msgs[-1].content


@pytest.mark.asyncio
async def test_dag_violation_twice_falls_back_with_reason(mock_config):
    mock_config.composite.plan_dag_validation_enabled = True
    llm = _llm(BROKEN, BROKEN)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 2  # 총 호출 ≤ 2
    assert len(out["tasks"]) == 1 and out["tasks"][0]["agent"] == "data_query"
    assert out["degraded"][-1]["reason"] == "plan_dag_invalid" and out["degraded"][-1]["attempts"] == 2


@pytest.mark.asyncio
async def test_autofix_records_note_without_retry(mock_config):
    mock_config.composite.plan_dag_validation_enabled = True
    fixable = _plan([
        {"task_id": "t1", "agent": "data_query", "sub_query": "a"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "b", "input_from": ["t1"]},
    ])
    llm = _llm(fixable)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 1
    assert out["tasks"][1]["depends_on"] == ["t1"]
    assert out["degraded"][0]["reason"] == "plan_dag_autofixed"


@pytest.mark.asyncio
async def test_sequential_marker_single_plan_retries_once(mock_config):
    mock_config.composite.sequential_replan_enabled = True
    llm = _llm(SINGLE, CHAIN)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 2 and len(out["tasks"]) == 2 and "degraded" not in out


@pytest.mark.asyncio
async def test_sequential_marker_still_single_runs_with_note(mock_config):
    mock_config.composite.sequential_replan_enabled = True
    llm = _llm(SINGLE, SINGLE)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 2 and len(out["tasks"]) == 1
    assert out["degraded"][0]["reason"] == "sequential_not_applied"


@pytest.mark.asyncio
async def test_no_marker_never_retries_even_when_single(mock_config):
    mock_config.composite.sequential_replan_enabled = True
    mock_config.composite.plan_dag_validation_enabled = True
    llm = _llm(_plan([{"task_id": "t1", "agent": "data_query", "sub_query": "x"}]))
    out = await _llm_decompose(llm, "전체 서버의 OS 종류", mock_config)
    assert llm.ainvoke.await_count == 1 and "degraded" not in out


@pytest.mark.asyncio
async def test_both_flags_share_one_retry_budget(mock_config):
    """DAG 위반 + 표지 단일이 같이 걸려도 재요청은 1회다."""
    mock_config.composite.sequential_replan_enabled = True
    mock_config.composite.plan_dag_validation_enabled = True
    llm = _llm(BROKEN, SINGLE)
    out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 2
    assert out["degraded"][-1]["reason"] == "sequential_not_applied"


@pytest.mark.asyncio
async def test_intent_planner_node_routes_degraded_to_dependency_notes(mock_config):
    mock_config.composite.sequential_replan_enabled = True
    llm = _llm(SINGLE, SINGLE)
    state = create_initial_state(user_query=SEQ_QUERY)
    out = await intent_planner(state, llm=llm, app_config=mock_config)
    notes = out["dependency_notes"]
    assert notes[0]["kind"] == "decompose" and notes[0]["reason"] == "sequential_not_applied"
    assert len(out["task_plan"]) == 1


# ──────────────────────────────────────────────
# off 관측 로그 — 게이트와 대칭 (plans/88 §11 · 2026-09-10 확정: 카운터 대신 로그)
# ──────────────────────────────────────────────

import logging  # noqa: E402


@pytest.mark.asyncio
async def test_replan_off_logs_observation_and_stays_byte_identical(mock_config, caplog):
    mock_config.composite.sequential_replan_enabled = False  # off 전제 명시(기본값 on)
    llm = _llm(SINGLE)
    with caplog.at_level(logging.INFO, logger="src.orchestration.intent_planner"):
        out = await _llm_decompose(llm, SEQ_QUERY, mock_config)
    assert llm.ainvoke.await_count == 1 and len(out["tasks"]) == 1 and "degraded" not in out
    msgs = [r.getMessage() for r in caplog.records if "순차 재분해 관측(off)" in r.getMessage()]
    assert len(msgs) == 1


@pytest.mark.asyncio
async def test_replan_off_no_log_without_marker_or_when_chained(mock_config, caplog):
    mock_config.composite.sequential_replan_enabled = False  # off 전제 명시(기본값 on)
    with caplog.at_level(logging.INFO, logger="src.orchestration.intent_planner"):
        await _llm_decompose(_llm(SINGLE), "전체 서버의 OS 종류", mock_config)
        await _llm_decompose(_llm(CHAIN), SEQ_QUERY, mock_config)
    assert not [r for r in caplog.records if "순차 재분해 관측(off)" in r.getMessage()]
