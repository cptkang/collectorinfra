"""plans/190 독립 검증(verifier) — 디스패처 배선 · 침묵 강등 하류 · 경계 사례.

가짜 배치 콜러블만 쓴다(실 게이트웨이·LLM 호출 0).
"""

from __future__ import annotations

import pytest
from sre_agent.application.playbook_steps import APM_SOURCE_UNRESOLVED_LIMIT
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import alarm_of, build, make_settings, run_job
from test_plan190_apm_scope import ALARM, COMPARE, CONFIRM, Fake, _health, _inst, _run

# ── 디스패처 배선 — settings.apm_xview_confirm_over → run_playbook ───────────


def _dispatch_calls(**overrides) -> list[tuple[str, dict]]:
    settings = make_settings(sre_deep_dive_mode="off", **overrides)
    caller = FakeCaller(_load("latency_sql"), strict=False)
    job = run_job(build(settings, caller), alarm_of("latency_sql"))
    assert job.status == "done"
    return caller.calls


def _period_health(calls: list[tuple[str, dict]]) -> list[dict]:
    return [a for t, a in calls if t == "apm_app_health" and a.get("reference_time")]


def test_dispatcher_sends_xview_confirm_over_from_settings():
    health = _period_health(_dispatch_calls(apm_xview_confirm_over=10))
    assert health, "기간 모드 app_health 호출이 없다 — 픽스처 확인"
    assert all(a.get("xview_confirm_over") == 10 for a in health)


def test_dispatcher_default_setting_is_10():
    health = _period_health(_dispatch_calls())
    assert health and all(a.get("xview_confirm_over") == 10 for a in health)


@pytest.mark.parametrize("value", [None, 0])
def test_dispatcher_setting_off_sends_no_arg(value):
    calls = _dispatch_calls(apm_xview_confirm_over=value)
    assert _period_health(calls)
    assert all("xview_confirm_over" not in a for _, a in calls)


# ── 침묵 강등 — confirm 봉투가 하류 어디서도 「normal」이 되지 않는다 ───────


def test_confirm_only_on_baselines_keeps_alarm_unjudged():
    """사건창은 정상 응답(신호 없음)이지만 기준선이 모두 사전 확인 초과 → 기준선 p95 없음 → 판정 불가."""

    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0", search_confidence="high")]}
        if tool == "apm_app_health":
            return _health(p95=1000.0) if args["reference_time"] == "2026-10-19T10:10:00" \
                else CONFIRM
        if tool == "apm_period_compare":
            return COMPARE
        return None

    run = _run(ALARM, "was.latency", Fake(rule), xview_confirm_over=10)
    s0, s1 = run.section("S0"), run.section("S1")
    assert s0.data["instances"][0]["verdict"] is None
    assert s1.verdict is None and s1.data["targets"]["A1"]["verdict"] is None
    assert any("X-View 사전 확인 초과" in n for n in run.notes)


def test_confirm_readings_scope_is_unjudged_not_normal():
    apm = Fake(lambda t, a: {"rows": [_inst("A1", "b0", search_confidence="high")]}
               if t == "apm_instance_map" else (CONFIRM if t == "apm_app_health" else COMPARE
                                                if t == "apm_period_compare" else None))
    run = _run(ALARM, "was.latency", apm, xview_confirm_over=10)
    assert run.readings is not None
    assert run.readings.values.get("scope") is None
    ev = run.readings.evidence.get("scope")
    assert ev is not None and ev.reason and "판정 불가" in ev.reason
    # S0 targets(dispatcher·_alarm_degraded가 읽는 명단)의 알람 판정도 None
    assert run.scope["targets"][0]["verdict"] is None


def test_confirm_with_degraded_signals_still_degraded():
    """게이트웨이가 confirm 봉투에 신호를 싣지 않으므로 별개 — 사건창 정상 응답 신호는 그대로 인용."""

    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0", search_confidence="high")]}
        if tool == "apm_app_health":
            if args["reference_time"] == "2026-10-19T10:10:00":
                return _health(signals=[{"kind": "was_slow_sql"}])
            return CONFIRM
        return COMPARE if tool == "apm_period_compare" else None

    run = _run(ALARM, "was.latency", Fake(rule), xview_confirm_over=10)
    assert run.section("S0").data["instances"][0]["verdict"] == "degraded"


# ── 경계 — 업무 확장에서 알람 인스턴스가 source_id 없이 다시 나올 때 ─────────


def test_business_row_without_source_merges_into_alarm_instance():
    def rule(tool, args):
        if tool == "apm_instance_map":
            if "query" in args:
                return {"rows": [_inst("A1", "b0", search_confidence="high",
                                       business_names=["계좌조회"])]}
            if "hostname" in args:
                return {"rows": [_inst("A1", "b0")]}
            return {"rows": [_inst("A1", None)]}  # 업무 확장 — source_id 누락 행
        if tool == "apm_app_health":
            return _health()
        return COMPARE if tool == "apm_period_compare" else None

    run = _run(ALARM, "was.latency", Fake(rule), for_each_max=None)
    names = [(i["instance"], i["source_id"]) for i in run.section("S0").data["instances"]]
    assert names == [("A1", "b0")]
    assert APM_SOURCE_UNRESOLVED_LIMIT not in run.notes
