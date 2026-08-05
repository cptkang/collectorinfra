"""Plan 66 Wave 3-E — 즉시통보 + 후속 브리핑 계약 테스트 (D-124 설계 노트 정련).

인라인 첨부(CW-A)는 poll 완주까지 통보를 붙들어 실 LLM 조사(수십~수백 초)에서 PAGE 통보가
그만큼 늦는다. 후속 모드는 submit까지만 하고 통보를 즉시 내보낸 뒤, 브리핑을 별도 메시지로
보낸다(Plan 64 §6.2가 허용한 "후속 메시지").

검증 범위(sre_agent 패키지 미import — MCP 계약을 모사한 페이크 사용):
  1. 트리거 submit-only: poll 0회·investigation_pending 반환·브리핑 미첨부·감사 submitted.
  2. 트리거 graceful: rejected/서비스 다운 시 pending 미생성·사유 감사·통보 무영향.
  3. 통보 순서: 후속 태스크는 즉시 통보가 **끝난 뒤**에 생성된다.
  4. 후속 발송: poll 종결 → 브리핑 후속 메시지 1건 + 종결 상태 감사.
  5. 후속 graceful: 타임아웃·발송 실패·브리핑 없음 시 감사만·빈 메시지 미발송.
  6. escalate-only: fault_escalation_enabled + verdict.escalate 시 상향 안내 동반.
  7. 스폰 게이트: 플래그 off·pending 없음·workb 미발송·동시 상한 초과 시 미스폰.
  8. 렌더: build_followup_body가 식별 정보 + 브리핑 블록을 담는다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

import noise_gate.application.nodes.alarm_notifier as notifier_mod
from noise_gate.application.nodes.alarm_notifier import (
    alarm_notifier_node,
    build_followup_body,
)
from noise_gate.application.nodes.investigation_trigger import (
    investigation_trigger_node,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import NotificationDecision, TIER_PAGE

REF = datetime(2026, 8, 5, 10, 0, 0)


# ── 헬퍼 ────────────────────────────────────────────────────────────────
def _event() -> AlarmEvent:
    return AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=REF,
        conditions=">90%", condition_log="cpu 95%",
    )


def _result(channels=("workb",)) -> AlarmAnalysisResult:
    return AlarmAnalysisResult(
        alarm_event=_event(), severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a",
        notification_channels=list(channels),
    )


def _decision(fp: str = "fp-1") -> NotificationDecision:
    return NotificationDecision(
        tier=TIER_PAGE, reason="심각도2", priority=3,
        signals={"root_resource": None}, fingerprint=fp,
    )


def _ng(**over) -> SimpleNamespace:
    base = dict(
        investigation_trigger_enabled=True,
        investigation_trigger_min_tier="PAGE",
        investigation_followup_enabled=True,
        investigation_followup_timeout_seconds=5.0,
        investigation_followup_max_inflight=8,
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
        fault_escalation_enabled=False,
    )
    base.update(over)
    return SimpleNamespace(**base)


class FakeSubmitClient:
    """submit만 검증하는 페이크 — poll이 불리면 즉시 실패시켜 submit-only를 고정한다."""

    def __init__(self, status="accepted", reason=None, connect_error=None) -> None:
        self.status = status
        self.reason = reason
        self.connect_error = connect_error
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.submit_calls: list[dict] = []
        self.poll_calls: list[str] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def disconnect(self) -> None:
        self.disconnect_calls += 1

    async def submit(self, payload: dict) -> dict:
        self.submit_calls.append(payload)
        if self.status == "rejected":
            return {"investigation_id": None, "status": "rejected", "reason": self.reason}
        return {"investigation_id": "inv-1", "status": self.status}

    async def poll(self, investigation_id: str) -> dict:  # pragma: no cover - 불리면 실패
        self.poll_calls.append(investigation_id)
        raise AssertionError("후속 모드의 트리거 노드는 poll하지 않아야 한다")


class RecordingStore:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def record_investigation(self, **kw) -> None:
        self.records.append(kw)


def _trigger_config(ng, client=None, store=None) -> dict:
    return {
        "configurable": {
            "app_config": SimpleNamespace(noise_gate=ng),
            "sre_agent_client": client,
            "decision_store": store,
        }
    }


def _trigger_state(decision) -> dict:
    return {
        "alarm_event": _event(),
        "notification_decision": decision,
        "recurrence": None,
        "correlation_meta": None,
    }


# ── 1. 트리거 submit-only ────────────────────────────────────────────────
class TestTriggerSubmitOnly:
    async def test_submits_without_polling_and_returns_pending(self):
        client = FakeSubmitClient()
        store = RecordingStore()
        out = await investigation_trigger_node(
            _trigger_state(_decision()), _trigger_config(_ng(), client, store)
        )

        assert client.submit_calls and client.poll_calls == []
        assert out["investigation_pending"] == {
            "investigation_id": "inv-1", "alarm_id": "A-1", "fingerprint": "fp-1",
        }
        # 브리핑은 아직 없다 — 통보는 지연 없이 즉시 나간다.
        assert "investigation_briefing" not in out
        assert store.records[0]["status"] == "submitted"
        assert client.disconnect_calls == 1

    async def test_duplicate_submit_also_yields_pending(self):
        # dedup으로 기존 조사에 붙어도 후속 발송 대상이다(같은 investigation_id를 poll).
        client = FakeSubmitClient(status="duplicate")
        out = await investigation_trigger_node(
            _trigger_state(_decision()), _trigger_config(_ng(), client, None)
        )
        assert out["investigation_pending"]["investigation_id"] == "inv-1"

    async def test_rejected_yields_no_pending_and_audits_reason(self):
        client = FakeSubmitClient(status="rejected", reason="필수 event 필드 결측")
        store = RecordingStore()
        out = await investigation_trigger_node(
            _trigger_state(_decision()), _trigger_config(_ng(), client, store)
        )
        assert out == {}
        assert store.records[0]["status"] == "rejected"
        assert store.records[0]["verdict"] == "필수 event 필드 결측"

    async def test_service_down_is_graceful_and_audited(self):
        client = FakeSubmitClient(connect_error=RuntimeError("연결 실패"))
        store = RecordingStore()
        out = await investigation_trigger_node(
            _trigger_state(_decision()), _trigger_config(_ng(), client, store)
        )
        assert out == {}
        assert store.records[0]["status"] == "down"

    async def test_followup_off_keeps_inline_poll_path(self):
        # off면 기존 CW-A 인라인 경로 — poll이 불린다(FakeSubmitClient는 poll에서 실패).
        client = FakeSubmitClient()
        out = await investigation_trigger_node(
            _trigger_state(_decision()),
            _trigger_config(_ng(investigation_followup_enabled=False), client, None),
        )
        # poll 예외는 노드가 graceful로 삼키므로 pending·briefing 모두 없다.
        assert out == {}
        assert client.poll_calls == ["inv-1"]


# ── 2~5. notifier 후속 발송 ──────────────────────────────────────────────
def _patch_senders(monkeypatch) -> list[str]:
    calls: list[str] = []

    async def fake_workb(cfg, result, snap=None, **kwargs):
        calls.append("workb")

    async def fake_webhook(cfg, result, snap=None):
        calls.append("webhook")

    monkeypatch.setattr(notifier_mod, "_send_workb", fake_workb)
    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    return calls


def _notifier_config(ng, store=None) -> dict:
    return {
        "configurable": {
            "app_config": SimpleNamespace(
                workb=SimpleNamespace(), alarm=SimpleNamespace(), noise_gate=ng
            ),
            "decision_store": store,
        }
    }


async def _drain_followups() -> None:
    """스폰된 후속 태스크가 끝날 때까지 기다린다(테스트 전용 — 프로덕션은 fire-and-forget)."""
    for _ in range(50):
        pending = [t for t in notifier_mod._FOLLOWUP_TASKS if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


@pytest.fixture(autouse=True)
def _clear_followup_tasks():
    notifier_mod._FOLLOWUP_TASKS.clear()
    yield
    notifier_mod._FOLLOWUP_TASKS.clear()


class TestNotifierFollowupDelivery:
    async def test_sends_followup_after_immediate_notification(self, monkeypatch):
        sent = _patch_senders(monkeypatch)
        order: list[str] = []
        followups: list[tuple] = []

        async def fake_poll(inv_id, gate_cfg):
            order.append(f"poll:{inv_id}")
            return {"cause": "OOM", "evidence": "dmesg L.882"}, "done", None

        async def fake_send_followup(workb_cfg, result, briefing, escalation):
            order.append("followup_send")
            followups.append((briefing, escalation))

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_send_followup)

        store = RecordingStore()
        state = {
            "analysis_result": _result(),
            "notification_decision": _decision(),
            "investigation_pending": {
                "investigation_id": "inv-1", "alarm_id": "A-1", "fingerprint": "fp-1",
            },
        }
        await alarm_notifier_node(state, _notifier_config(_ng(), store))
        # 노드 반환 시점에는 즉시 통보만 끝나 있다(후속은 백그라운드).
        assert sent == ["workb"]
        await _drain_followups()

        assert order == ["poll:inv-1", "followup_send"]
        assert followups[0][0] == {"cause": "OOM", "evidence": "dmesg L.882"}
        assert store.records[-1]["status"] == "done"
        assert store.records[-1]["investigation_id"] == "inv-1"

    async def test_timeout_audits_without_sending(self, monkeypatch):
        _patch_senders(monkeypatch)
        sent_followups: list = []

        async def never_terminal(inv_id, gate_cfg):
            await asyncio.sleep(10)

        async def fake_send_followup(*a):
            sent_followups.append(a)

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", never_terminal)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_send_followup)

        store = RecordingStore()
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(
            state, _notifier_config(_ng(investigation_followup_timeout_seconds=0.05), store)
        )
        await _drain_followups()

        assert sent_followups == []  # 빈 후속 메시지 금지
        assert store.records[-1]["status"] == "followup_timeout"

    async def test_no_briefing_no_message_but_audited(self, monkeypatch):
        _patch_senders(monkeypatch)
        sent_followups: list = []

        async def fake_poll(inv_id, gate_cfg):
            return None, "failed", None

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
        monkeypatch.setattr(
            notifier_mod, "_send_workb_followup",
            lambda *a: sent_followups.append(a),
        )

        store = RecordingStore()
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(state, _notifier_config(_ng(), store))
        await _drain_followups()

        assert sent_followups == []
        assert store.records[-1]["status"] == "failed"

    async def test_send_failure_is_audited(self, monkeypatch):
        _patch_senders(monkeypatch)

        async def fake_poll(inv_id, gate_cfg):
            return {"cause": "OOM"}, "done", None

        async def failing_send(*a):
            raise RuntimeError("workb 500")

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", failing_send)

        store = RecordingStore()
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(state, _notifier_config(_ng(), store))
        await _drain_followups()

        assert "followup_send_failed" in store.records[-1]["status"]

    async def test_escalation_attached_when_enabled(self, monkeypatch):
        _patch_senders(monkeypatch)
        captured: list[tuple] = []

        async def fake_poll(inv_id, gate_cfg):
            return {"cause": "OOM"}, "done", {"escalate": True, "level": "critical"}

        async def fake_send_followup(workb_cfg, result, briefing, escalation):
            captured.append((briefing, escalation))

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_send_followup)

        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(
            state, _notifier_config(_ng(fault_escalation_enabled=True), None)
        )
        await _drain_followups()

        assert captured[0][1] == {"escalate": True, "level": "critical"}

    async def test_escalation_omitted_when_flag_off(self, monkeypatch):
        _patch_senders(monkeypatch)
        captured: list[tuple] = []

        async def fake_poll(inv_id, gate_cfg):
            return {"cause": "OOM"}, "done", {"escalate": True, "level": "critical"}

        async def fake_send_followup(workb_cfg, result, briefing, escalation):
            captured.append((briefing, escalation))

        monkeypatch.setattr(notifier_mod, "_poll_until_terminal", fake_poll)
        monkeypatch.setattr(notifier_mod, "_send_workb_followup", fake_send_followup)

        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(state, _notifier_config(_ng(), None))
        await _drain_followups()

        assert captured[0][1] is None


# ── 7. 스폰 게이트 ───────────────────────────────────────────────────────
class TestSpawnGates:
    async def test_no_spawn_when_flag_off(self, monkeypatch):
        _patch_senders(monkeypatch)
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(
            state, _notifier_config(_ng(investigation_followup_enabled=False))
        )
        assert not notifier_mod._FOLLOWUP_TASKS

    async def test_no_spawn_without_pending(self, monkeypatch):
        _patch_senders(monkeypatch)
        await alarm_notifier_node(
            {"analysis_result": _result()}, _notifier_config(_ng())
        )
        assert not notifier_mod._FOLLOWUP_TASKS

    async def test_no_spawn_when_workb_send_failed(self, monkeypatch):
        # 원 통보가 안 나갔으면 브리핑만 가는 고아 메시지를 만들지 않는다.
        async def failing_workb(cfg, result, snap=None, **kwargs):
            raise RuntimeError("workb down")

        monkeypatch.setattr(notifier_mod, "_send_workb", failing_workb)
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(state, _notifier_config(_ng()))
        assert not notifier_mod._FOLLOWUP_TASKS

    async def test_inflight_cap_blocks_spawn(self, monkeypatch):
        _patch_senders(monkeypatch)

        async def idle():
            await asyncio.sleep(0.2)

        # 상한(1)을 이미 채운 상태 — 신규 스폰은 차단된다.
        filler = asyncio.create_task(idle())
        notifier_mod._FOLLOWUP_TASKS.add(filler)
        state = {
            "analysis_result": _result(),
            "investigation_pending": {"investigation_id": "inv-1", "alarm_id": "A-1"},
        }
        await alarm_notifier_node(
            state, _notifier_config(_ng(investigation_followup_max_inflight=1))
        )
        assert notifier_mod._FOLLOWUP_TASKS == {filler}
        filler.cancel()


# ── 8. 렌더 ─────────────────────────────────────────────────────────────
class TestFollowupBody:
    def test_body_has_identity_and_briefing(self):
        body = build_followup_body(_result(), {"cause": "OOM", "evidence": "dmesg"})
        assert "자동 조사 결과 (후속)" in body
        assert "CPU High" in body and "srv-1" in body and "A-1" in body
        assert "OOM" in body and "dmesg" in body

    def test_body_renders_escalation_block(self):
        body = build_followup_body(
            _result(), {"cause": "OOM"}, {"escalate": True, "level": "critical"}
        )
        assert "중요도 상향" in body and "critical" in body

    def test_body_escapes_html(self):
        result = _result()
        result.alarm_event.alarm_name = "<script>x</script>"
        body = build_followup_body(result, {"cause": "OOM"})
        assert "<script>" not in body and "&lt;script&gt;" in body
