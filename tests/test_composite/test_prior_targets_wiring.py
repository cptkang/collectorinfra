"""조사 대상 배선 — 1단·2단 대칭 · 3경로 공통화 (Plan 78 W1-4·대칭성 / WU-11).

**왜 대칭을 따로 단언하는가**: 이 저장소의 반복 실수 1위가 *"단일/멀티 경로 중 한쪽만 고치는
비대칭"* 이다(Known Mistakes). `plans/80` §5.4-⑤도 **양쪽 주입을 실측**하라고 못 박는다.

3-A 조건: 라우팅 결과·relevance_score·의도 분류는 단언하지 않는다.
"""

from __future__ import annotations

import pytest

from src.config import load_config
from src.orchestration.deepagents_tools import (
    _SCOPE_CONSUMER_AGENTS,
    _SCOPE_PRODUCER_AGENTS,
    _dependency_scope,
)
from src.orchestration.subagents import _make_isolated_input

SERVER_ROWS = [
    {"hostname": "svweb001"},
    {"hostname": "svweb002"},
    {"hostname": "svbatch009"},
]


@pytest.fixture
def targets_on(monkeypatch):
    """`COMPOSITE_PRIOR_TARGETS_ENABLED=true`로 W1 경로를 켠다(기본은 off)."""
    monkeypatch.setenv("COMPOSITE_PRIOR_TARGETS_ENABLED", "true")
    load_config.cache_clear()
    yield
    load_config.cache_clear()


def _prior_result(rows, db_id="polestar_gimpo") -> dict:
    return {
        "organized_data": {"summary": "", "rows": rows},
        "query_results": rows,
        "target_db_ids": [db_id],
    }


def _task(agent: str, *, input_from=None) -> dict:
    return {
        "task_id": f"t_{agent}",
        "agent": agent,
        "sub_query": "해당 서버들의 프로세스를 보여줘",
        "depends_on": [],
        "input_from": list(input_from or []),
        "order": 2,
        "status": "pending",
    }


# ──────────────────────────────────────────────
# 1단 — 도구 경로 게이트 (deepagents_tools)
# ──────────────────────────────────────────────

def test_producer_set_includes_investigation_agents():
    """W1-1 — 생산자 후보에 `process_query`·`fault_diagnosis`가 있다."""
    assert "process_query" in _SCOPE_PRODUCER_AGENTS
    assert "fault_diagnosis" in _SCOPE_PRODUCER_AGENTS


def test_consumer_set_includes_investigation_agents():
    """W1-2 — 소비자 게이트에도 둘이 있다(생산만 되고 소비가 안 되면 의미 없다)."""
    assert "process_query" in _SCOPE_CONSUMER_AGENTS
    assert "fault_diagnosis" in _SCOPE_CONSUMER_AGENTS


def test_dependency_scope_picks_up_process_query_result():
    """도구 경로에서 `process_query` 결과가 후속 task의 선행으로 잡힌다."""
    collector = [(_task("process_query"), _prior_result(SERVER_ROWS))]
    input_from, prior = _dependency_scope("해당 서버들 원인 분석", collector)
    assert input_from == ["t_process_query"]
    assert prior["t_process_query"]["query_results"] == SERVER_ROWS


# ──────────────────────────────────────────────
# 2단 — 격리 입력 조립 (subagents) · 1단과 같은 합류점
# ──────────────────────────────────────────────

def test_isolated_input_injects_prior_targets_with_input_from(targets_on):
    """계획 경로: `input_from`이 지정되면 그 결과에서 대상을 해소해 싣는다."""
    prior = {"t1": _prior_result(SERVER_ROWS)}
    iso = _make_isolated_input(_task("process_query", input_from=["t1"]), {}, prior)
    assert [t["hostname"] for t in iso["prior_targets"]] == [
        "svweb001", "svweb002", "svbatch009",
    ]
    assert all(t["db_id"] == "polestar_gimpo" for t in iso["prior_targets"])


def test_isolated_input_injects_prior_targets_without_input_from(targets_on):
    """도구 경로: `input_from`이 없어도 완료된 선행 결과에서 대상을 해소한다.

    `intent_planner`를 수정하지 않고 대상 전달을 성립시키기 위한 폴백이다(78 R-13 경계 유지).
    """
    prior = {"t1": _prior_result(SERVER_ROWS)}
    iso = _make_isolated_input(_task("process_query"), {}, prior)
    assert len(iso["prior_targets"]) == 3


def test_isolated_input_symmetric_for_both_consumer_agents(targets_on):
    """★ 대칭 — `process_query`와 `fault_diagnosis`가 **같은 형태**로 받는다.

    한쪽만 배선되는 것이 이 저장소의 반복 실수다.
    """
    prior = {"t1": _prior_result(SERVER_ROWS)}
    a = _make_isolated_input(_task("process_query", input_from=["t1"]), {}, prior)
    b = _make_isolated_input(_task("fault_diagnosis", input_from=["t1"]), {}, prior)
    assert a["prior_targets"] == b["prior_targets"]


def test_data_query_still_gets_prior_rows_not_targets(targets_on):
    """소비 방식 분기 — `data_query`는 종전대로 `prior_rows`(SQL 스코프, D-086)를 받는다."""
    prior = {"t1": _prior_result(SERVER_ROWS)}
    iso = _make_isolated_input(_task("data_query", input_from=["t1"]), {}, prior)
    assert "prior_rows" in iso
    assert "prior_targets" not in iso


def test_flag_off_means_no_injection():
    """★ 회귀 0 — 플래그 미설정(기본)이면 `prior_targets`를 싣지 않는다(비트동일)."""
    load_config.cache_clear()
    prior = {"t1": _prior_result(SERVER_ROWS)}
    iso = _make_isolated_input(_task("process_query", input_from=["t1"]), {}, prior)
    assert "prior_targets" not in iso


def test_process_rows_do_not_become_targets(targets_on):
    """선행이 프로세스 결과면 대상이 만들어지지 않는다(`pid` 보유 행 — 서버가 아니다)."""
    rows = [{"name": "java", "pid": 1234, "cpu_pct": 91.2}]
    prior = {"t1": _prior_result(rows)}
    iso = _make_isolated_input(_task("fault_diagnosis", input_from=["t1"]), {}, prior)
    assert "prior_targets" not in iso


# ──────────────────────────────────────────────
# 3경로 공통화 (G5)
# ──────────────────────────────────────────────

def test_all_three_paths_use_the_common_module():
    """★ G5 — 세 소비 경로가 **같은 모듈**을 import한다(각자 구현 금지).

    문자열 grep이 아니라 실제 import 심볼로 확인한다(주석·docstring 오탐 방지).
    """
    import ast
    import pathlib

    paths = {
        "process_query": "src/orchestration/process_query.py",
        "fault_diagnosis": "src/nodes/fault_diagnosis.py",
        "investigation_trigger": "noise_gate/application/nodes/investigation_trigger.py",
    }
    for name, rel in paths.items():
        tree = ast.parse(pathlib.Path(rel).read_text())
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert "src.utils.prior_targets" in modules, f"{name}이 공통 모듈을 쓰지 않는다"


def test_fault_diagnosis_reads_prior_targets():
    """`fault_diagnosis`가 `prior_targets`를 실제로 소비한다 — G2가 RCA 경로에서 해소된다."""
    from src.nodes.fault_diagnosis import _extract_targets

    server_name, hostname, db_id = _extract_targets(
        {"prior_targets": [{"hostname": "svweb001", "db_id": "polestar_gimpo"}]}
    )
    assert hostname == "svweb001"
    assert db_id == "polestar_gimpo"


def test_this_turn_filter_still_wins_in_fault_diagnosis():
    """우선순위 ①>② 가 RCA 경로에서도 지켜진다(승계값이 이번 턴을 덮어쓰지 않는다)."""
    from src.nodes.fault_diagnosis import _extract_targets

    _, hostname, _ = _extract_targets(
        {
            "parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "value": "svdb001"}]
            },
            "prior_targets": [{"hostname": "svweb001"}],
        }
    )
    assert hostname == "svdb001"


def test_r13_boundary_untouched():
    """★ R-13 — W1이 `intent_planner`의 대상·계획 구조를 건드리지 않았다.

    `TaskSpec` 타입화·`_llm_decompose` 재시도는 79 소유다(80 §6 소유권 계약).
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/orchestration/intent_planner.py").read_text())
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "src.utils.prior_targets" not in modules
