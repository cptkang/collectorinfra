"""post-gate L3 보강 배선 (plans/91 1-6 · Plan 60 §18.6 ⑤ 플래그 off 비트동일 · ① 후속 첨부 · ② 재발 dedup)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node, build_followup_body
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import TIER_PAGE, NotificationDecision
from noise_gate.infrastructure.decision_store import DecisionStore
from noise_gate.infrastructure.host_diagnostic_collector import LOAD_GUARD_PREFIX

REF = datetime(2026, 8, 5, 10, 0, 0)
FREE = "              total        used        free\nMem:          64000       60000        4000\nSwap:          8000        1200        6800\n"


def _event(kind_rt="server.Memory", alarm_name="Memory High") -> AlarmEvent:
    return AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1", resource_ancestry="/root/srv-1",
        alarm_id="A-1", severity=2, alarm_status="NOT_ACK", resource_type=kind_rt, resource_name="r1",
        alarm_name=alarm_name, alarm_time=REF, conditions=">90%", condition_log="mem 95%",
    )


def _result() -> AlarmAnalysisResult:
    return AlarmAnalysisResult(alarm_event=_event(), severity_label="경고", summary="s", probable_cause="c",
                               recommended_action="a", notification_channels=["workb"])


def _decision(fp="fp-l3") -> NotificationDecision:
    return NotificationDecision(tier=TIER_PAGE, reason="심각도2", priority=3, signals={"root_resource": None}, fingerprint=fp)


def _ng(**over) -> SimpleNamespace:
    base = dict(investigation_trigger_enabled=False, investigation_followup_enabled=False, fault_escalation_enabled=False,
                l3_enrichment_enabled=False, l3_audit_enabled=False, l3_profile_map_csv="", l3_command_timeout_seconds=5.0,
                l3_ssh_user="", l3_max_inflight=4)
    base.update(over)
    return SimpleNamespace(**base)


def _config(ng, store=None, runner=None) -> dict:
    return {"configurable": {"app_config": SimpleNamespace(workb=SimpleNamespace(), alarm=SimpleNamespace(), noise_gate=ng),
                             "decision_store": store, "l3_runner": runner}}


def _patch(monkeypatch):
    sent: list[str] = []
    followups: list[dict] = []

    async def fake_workb(cfg, result, snap=None, **kw):
        sent.append("workb")

    async def fake_webhook(cfg, result, snap=None):
        sent.append("webhook")

    async def fake_followup(workb_cfg, result, briefing, escalation, l3=None):
        followups.append({"briefing": briefing, "escalation": escalation, "l3": l3})

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_followup)
    return sent, followups


def _runner(outputs):
    seen = []

    async def _run(host, command):
        seen.append((host, command))
        return outputs.get(command[len(LOAD_GUARD_PREFIX):], (0, ""))

    _run.seen = seen  # type: ignore[attr-defined]
    return _run


async def _drain():
    for _ in range(50):
        pending = [t for t in notifier_mod._L3_TASKS if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_tasks():
    notifier_mod._L3_TASKS.clear()
    yield
    notifier_mod._L3_TASKS.clear()


def _state():
    return {"analysis_result": _result(), "notification_decision": _decision()}


async def test_flag_off_is_bit_identical(monkeypatch):
    sent, followups = _patch(monkeypatch)
    run = _runner({})
    await alarm_notifier_node(_state(), _config(_ng(), runner=run))
    await _drain()
    assert sent == ["workb"] and followups == [] and run.seen == [] and not notifier_mod._L3_TASKS


async def test_first_collection_sends_l3_followup_after_notification(monkeypatch, tmp_path):
    sent, followups = _patch(monkeypatch)
    run = _runner({"free -m": (0, FREE)})
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    await alarm_notifier_node(_state(), _config(_ng(l3_enrichment_enabled=True, l3_audit_enabled=True), store, run))
    assert sent == ["workb"]           # 노드 반환 시점에는 즉시 통보만(비차단)
    await _drain()
    assert all(h == "h1" and c.startswith(LOAD_GUARD_PREFIX) for h, c in run.seen)
    assert len(followups) == 1 and followups[0]["l3"]["kind"] == "memory" and followups[0]["l3"]["transition"] == "first"
    assert store.last_l3_state("fp-l3") == {"top_rss_pid": None, "oom_flag": False, "swap_active": True, "sat_bucket": "unknown"}


async def test_recurrence_same_state_is_not_resent_but_worse_is(monkeypatch, tmp_path):
    sent, followups = _patch(monkeypatch)
    store = DecisionStore(str(tmp_path / "d.jsonl"))
    ng = _ng(l3_enrichment_enabled=True, l3_audit_enabled=True)
    await alarm_notifier_node(_state(), _config(ng, store, _runner({"free -m": (0, FREE)})))
    await _drain()
    await alarm_notifier_node(_state(), _config(ng, store, _runner({"free -m": (0, FREE)})))   # 동일 → 재통보 0
    await _drain()
    assert len(followups) == 1
    worse = _runner({"free -m": (0, FREE), "dmesg": (0, "Out of memory: Killed process 1 (x)")})
    await alarm_notifier_node(_state(), _config(ng, store, worse))                              # 악화 → escalate
    await _drain()
    assert len(followups) == 2 and followups[-1]["l3"]["transition"] == "worse"
    recs = [l for l in (tmp_path / "d.jsonl").read_text().splitlines() if '"l3_state"' in l]
    assert len(recs) == 3 and '"sent": false' in recs[1] and '"transition": "worse"' in recs[2]


async def test_unclassified_kind_and_all_failed_collection_do_not_send(monkeypatch):
    sent, followups = _patch(monkeypatch)
    state = _state()
    state["analysis_result"] = AlarmAnalysisResult(alarm_event=_event(kind_rt="server.Server", alarm_name="Host unreachable"), severity_label="경고", summary="s",
                                                   probable_cause="c", recommended_action="a", notification_channels=["workb"])
    run = _runner({})
    await alarm_notifier_node(state, _config(_ng(l3_enrichment_enabled=True), runner=run))
    await _drain()
    assert run.seen == [] and followups == []

    async def failing(host, command):
        raise OSError("ssh down")

    await alarm_notifier_node(_state(), _config(_ng(l3_enrichment_enabled=True), runner=failing))
    await _drain()
    assert followups == []   # ok_count 0 → 발송 없음(빈 후속 금지)


def test_followup_body_without_l3_is_unchanged_and_with_l3_appends_block():
    r = _result()
    assert build_followup_body(r) == build_followup_body(r, None, None, None)
    body = build_followup_body(r, l3={"kind": "memory", "transition": "first", "summary_lines": ["포화 구간: critical", "swap 사용 중"]})
    assert "L3 진단 요지" in body and "포화 구간: critical" in body and body.startswith(build_followup_body(r))
