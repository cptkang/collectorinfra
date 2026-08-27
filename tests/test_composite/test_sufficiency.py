"""충족도 검증·1회 재계획 (Plan 78 W5 / Plan 80 WU-16 · SPEC M6 · P7).

VMAO의 Verify를 **최소 형태로만** 도입한다 — LLM 미사용, 재계획 **1회로 못 박음**(ADaPT 교훈).
가장 중요한 수용 기준은 **무한 루프 부재**다.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

# `from src.orchestration import agent_orchestrator`는 **함수**를 돌려준다 —
# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다(같은 함정을 라우터 테스트에서
# 이미 겪었다). 모듈 객체가 필요하므로 importlib로 가져온다.
ao = importlib.import_module("src.orchestration.agent_orchestrator")
from src.orchestration.sufficiency import (
    MAX_SUFFICIENCY_RETRIES,
    REASON_EMPTY_HANDED,
    REASON_TARGET_MISMATCH,
    REASON_TARGET_SHORTFALL,
    check_investigation_readiness,
    check_sufficiency,
    reconcile_targets,
    summarize_shortfalls,
)

TASKS = [{"task_id": "t2", "agent": "process_query", "sub_query": "프로세스"}]
THREE = [{"hostname": "h1"}, {"hostname": "h2"}, {"hostname": "h3"}]


def _result(succeeded, hostnames):
    return {
        "process_query": {"succeeded_count": succeeded},
        "query_results": [{"hostname": h} for h in hostnames],
    }


# ──────────────────────────────────────────────
# 충족도 판정 (W5-1 · 결정적)
# ──────────────────────────────────────────────

def test_full_coverage_is_sufficient():
    r = check_sufficiency(TASKS, {"t2": _result(3, ["h1", "h2", "h3"])}, {"t2": THREE})
    assert r.sufficient is True


def test_shortfall_is_detected():
    """선행이 3개를 냈는데 1개만 조사됐다 — 부분 결과가 전체로 오인되는 것을 막는다."""
    r = check_sufficiency(TASKS, {"t2": _result(1, ["h1"])}, {"t2": THREE})
    assert r.shortfalls[0].reason == REASON_TARGET_SHORTFALL
    assert (r.shortfalls[0].expected, r.shortfalls[0].actual) == (3, 1)


def test_empty_handed_is_detected():
    """후속이 대상 미확정으로 빈손 반환한 것을 잡는다(조용한 실패 차단)."""
    r = check_sufficiency(TASKS, {"t2": _result(0, [])}, {"t2": THREE})
    assert r.shortfalls[0].reason == REASON_EMPTY_HANDED


def test_no_llm_is_used():
    """★ 판정은 결정적이다 — LLM을 부르지 않는다(78 W5-1 명시)."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/orchestration/sufficiency.py").read_text())
    modules = {
        n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
    }
    assert not any(m.startswith("src.llm") or m.startswith("src.prompts") for m in modules)


def test_error_results_are_not_double_counted():
    """실패는 이미 사유가 있다 — 충족도로 이중 계상하지 않는다."""
    r = check_sufficiency(TASKS, {"t2": {"error": "API 실패"}}, {"t2": THREE})
    assert r.sufficient is True


def test_without_injected_targets_there_is_no_yardstick():
    """대상 주입이 없으면 "몇 개를 조사했어야 하는가"의 기준이 없다 — 판정하지 않는다."""
    assert check_sufficiency(TASKS, {"t2": _result(0, [])}, {}).sufficient is True


def test_non_investigation_agents_are_ignored():
    tasks = [{"task_id": "t1", "agent": "data_query"}]
    assert check_sufficiency(tasks, {"t1": _result(0, [])}, {"t1": THREE}).sufficient is True


# ──────────────────────────────────────────────
# 대상 정합 사후 대조 (W5-5 · 갭 ② 부분 해소)
# ──────────────────────────────────────────────

def test_out_of_scope_host_is_flagged():
    """★ `prior_targets`에 없는 hostname이 결과에 실리면 **오류로 잡힌다**.

    "엉뚱한 호스트를 조사했는데 형태가 정상이라 통과"를 막는 최소 방어다.
    """
    r = check_sufficiency(TASKS, {"t2": _result(3, ["h1", "h2", "zzz"])}, {"t2": THREE})
    assert any(s.reason == REASON_TARGET_MISMATCH for s in r.shortfalls)


def test_reconcile_accepts_server_name_form():
    """대상이 서버명으로 잡혔고 결과가 그 이름으로 오면 정합이다(D-046 두 표기)."""
    assert reconcile_targets([{"server_name": "웹01"}], _result(1, ["웹01"])) == set()


def test_reconcile_is_silent_when_no_expectation():
    assert reconcile_targets([], _result(1, ["anything"])) == set()


# ──────────────────────────────────────────────
# 준비 검증 (W5-4 · 원인 귀책)
# ──────────────────────────────────────────────

def test_readiness_blocks_before_starting():
    """★ 조사 경로 미가용을 **조사 실패로 기록하지 않는다** — 착수 전에 사유를 돌려준다.

    문서의 지적: *"평가 인프라 잡음이 모델 실패로 위장한다."*
    """
    ok, reason = check_investigation_readiness(SimpleNamespace(noise_gate=None))
    assert ok is False and reason

    off = SimpleNamespace(noise_gate=SimpleNamespace(fault_diagnosis_enabled=False))
    ok, reason = check_investigation_readiness(off)
    assert ok is False and "비활성" in reason

    no_url = SimpleNamespace(noise_gate=SimpleNamespace(
        fault_diagnosis_enabled=True, investigation_service_url="  "))
    ok, reason = check_investigation_readiness(no_url)
    assert ok is False and "URL" in reason


def test_readiness_passes_when_configured():
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(
        fault_diagnosis_enabled=True, investigation_service_url="http://svc/sse"))
    assert check_investigation_readiness(cfg) == (True, "")


# ──────────────────────────────────────────────
# 재계획 1회 상한 · 무한 루프 부재 (W5-2)
# ──────────────────────────────────────────────

def test_retry_limit_is_a_constant_not_a_setting():
    """★ 재계획은 1회뿐이다. 설정으로 늘릴 수 있으면 무한 루프 방지의 의미가 없다."""
    assert MAX_SUFFICIENCY_RETRIES == 1
    import pathlib

    cfg = pathlib.Path("src/config.py").read_text()
    assert "sufficiency_retries" not in cfg and "SUFFICIENCY_RETRIES" not in cfg


@pytest.mark.asyncio
async def test_orchestrator_retries_exactly_once(monkeypatch):
    """★ 미충족이어도 재실행은 **정확히 1회**다 — 개선되지 않아도 다시 돌지 않는다."""
    runs = {"n": 0}

    async def _fake_run(task, state, llm, app_config, *, prior, injected=None):
        runs["n"] += 1
        if injected is not None:
            injected[task["task_id"]] = list(THREE)
        return _result(1, ["h1"])          # 항상 미충족

    monkeypatch.setattr(ao, "_run_agent", _fake_run)
    out = await ao.agent_orchestrator(
        {"task_plan": [dict(TASKS[0], status="pending")], "task_results": {}},
        llm=object(), app_config=SimpleNamespace(),
    )
    assert runs["n"] == 2, "최초 1회 + 재시도 1회 = 2회여야 한다"
    assert out["sufficiency_shortfalls"][0]["reason"] == REASON_TARGET_SHORTFALL


@pytest.mark.asyncio
async def test_orchestrator_stops_when_retry_fixes_it(monkeypatch):
    """재시도로 충족되면 사유를 노출하지 않는다."""
    runs = {"n": 0}

    async def _fake_run(task, state, llm, app_config, *, prior, injected=None):
        runs["n"] += 1
        if injected is not None:
            injected[task["task_id"]] = list(THREE)
        return _result(1, ["h1"]) if runs["n"] == 1 else _result(3, ["h1", "h2", "h3"])

    monkeypatch.setattr(ao, "_run_agent", _fake_run)
    out = await ao.agent_orchestrator(
        {"task_plan": [dict(TASKS[0], status="pending")], "task_results": {}},
        llm=object(), app_config=SimpleNamespace(),
    )
    assert runs["n"] == 2
    assert "sufficiency_shortfalls" not in out


@pytest.mark.asyncio
async def test_no_injected_targets_means_no_verification(monkeypatch):
    """★ 회귀 0 — 대상 주입이 없으면 검증도 재시도도 일어나지 않는다."""
    runs = {"n": 0}

    async def _fake_run(task, state, llm, app_config, *, prior, injected=None):
        runs["n"] += 1
        return _result(0, [])

    monkeypatch.setattr(ao, "_run_agent", _fake_run)
    out = await ao.agent_orchestrator(
        {"task_plan": [dict(TASKS[0], status="pending")], "task_results": {}},
        llm=object(), app_config=SimpleNamespace(),
    )
    assert runs["n"] == 1
    assert "sufficiency_shortfalls" not in out


@pytest.mark.asyncio
async def test_retry_failure_keeps_original_result(monkeypatch):
    """재시도가 터져도 원 결과를 지우지 않는다(부분 결과 보존)."""
    runs = {"n": 0}

    async def _fake_run(task, state, llm, app_config, *, prior, injected=None):
        runs["n"] += 1
        if runs["n"] == 1:
            if injected is not None:
                injected[task["task_id"]] = list(THREE)
            return _result(1, ["h1"])
        raise RuntimeError("재시도 중 예외")

    monkeypatch.setattr(ao, "_run_agent", _fake_run)
    out = await ao.agent_orchestrator(
        {"task_plan": [dict(TASKS[0], status="pending")], "task_results": {}},
        llm=object(), app_config=SimpleNamespace(),
    )
    assert out["task_results"]["t2"]["process_query"]["succeeded_count"] == 1


# ──────────────────────────────────────────────
# 사유 노출 (W5-3)
# ──────────────────────────────────────────────

def test_shortfall_summary_is_user_facing():
    """재시도 후에도 미충족이면 **사유를 노출**한다 — 조용히 부분 결과만 보이지 않는다."""
    r = check_sufficiency(TASKS, {"t2": _result(1, ["h1"])}, {"t2": THREE})
    note = summarize_shortfalls(r, retried=True)
    assert "3건 중 1건" in note
    assert "재시도" in note


def test_sufficient_report_has_no_note():
    r = check_sufficiency(TASKS, {"t2": _result(3, ["h1", "h2", "h3"])}, {"t2": THREE})
    assert summarize_shortfalls(r, retried=False) is None
