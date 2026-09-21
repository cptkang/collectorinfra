"""D-243 — 미연동 환경의 통보·조사 생략 테스트.

  1. worKB 미설정(WORKB_BASE_URL 없음)이면 발송하지 않고 traceback 없이 한 줄 경고만 남긴다.
  2. 조사 서비스 사전 도달성 확인(`SreAgentClient.unreachable_reason`) — 실제 루프백 소켓으로 본다.
  3. 트리거 노드는 서비스 미가용이면 SSE 연결 없이 한 줄 경고로 조사를 생략하고 감사에 남긴다.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from datetime import datetime
from types import SimpleNamespace

import noise_gate.application.nodes.alarm_notifier as notifier_mod
import noise_gate.infrastructure.sre_agent_client as client_mod
from noise_gate.application.nodes.alarm_notifier import alarm_notifier_node
from noise_gate.application.nodes.investigation_trigger import (
    investigation_trigger_node,
)
from noise_gate.domain.alarm import AlarmAnalysisResult, AlarmEvent
from noise_gate.domain.notification_policy import TIER_PAGE, NotificationDecision
from noise_gate.infrastructure.sre_agent_client import SreAgentClient


def _event() -> AlarmEvent:
    return AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=datetime(2026, 9, 21, 17, 0, 0),
        conditions=">90%", condition_log="cpu 95%",
    )


def _decision() -> NotificationDecision:
    return NotificationDecision(
        tier=TIER_PAGE, reason="t", priority=3,
        signals={"root_resource": None}, fingerprint="fp-1",
    )


# ── 1. worKB 미설정 → 발송 생략 ─────────────────────────────────────────


async def test_workb_unconfigured_skips_without_traceback(monkeypatch, caplog):
    webhook_calls: list[str] = []

    async def fake_webhook(cfg, result, snap=None):
        webhook_calls.append("webhook")

    monkeypatch.setattr(notifier_mod, "_send_webhook", fake_webhook)
    result = AlarmAnalysisResult(
        alarm_event=_event(), severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a",
        notification_channels=["workb", "webhook"],
    )
    config = {"configurable": {"app_config": SimpleNamespace(
        workb=SimpleNamespace(base_url=""), alarm=SimpleNamespace())}}

    with caplog.at_level(logging.WARNING, logger=notifier_mod.__name__):
        out = await alarm_notifier_node({"analysis_result": result}, config)

    # workb는 발송하지 않고, 다른 채널은 그대로 진행한다.
    assert out["analysis_result"].notifications_sent == {"workb": False, "webhook": True}
    assert webhook_calls == ["webhook"]
    records = [r for r in caplog.records if r.name == notifier_mod.__name__]
    assert len(records) == 1
    assert "worKB 미설정" in records[0].getMessage()
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None  # traceback 없음


async def test_send_workb_still_raises_value_error_when_called_directly():
    """직접 호출 계약은 유지된다 — 전용 예외는 ValueError 하위다."""
    result = AlarmAnalysisResult(
        alarm_event=_event(), severity_label="경고", summary="s",
        probable_cause="c", recommended_action="a", notification_channels=["workb"],
    )
    try:
        await notifier_mod._send_workb(SimpleNamespace(base_url=""), result)
    except ValueError as e:
        assert isinstance(e, notifier_mod.WorkbNotConfiguredError)
    else:  # pragma: no cover
        raise AssertionError("ValueError가 나야 한다")


# ── 2. 조사 서비스 사전 도달성 확인 ──────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_probe_reachable_returns_none():
    async def _handler(reader, writer):
        writer.close()

    server = await asyncio.start_server(_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SreAgentClient(f"http://127.0.0.1:{port}/sse")
        assert await client.unreachable_reason() is None
    finally:
        server.close()
        await server.wait_closed()


async def test_probe_closed_port_returns_reason_and_caches(monkeypatch):
    port = _free_port()
    client = SreAgentClient(f"http://127.0.0.1:{port}/sse")

    reason = await client.unreachable_reason()
    assert reason and f"127.0.0.1:{port}" in reason

    # 재사용 창 안에서는 재확인(연결 시도) 없이 같은 사유를 즉시 돌려준다.
    attempts: list[tuple] = []

    async def counting_open_connection(*args, **kwargs):
        attempts.append(args)
        raise OSError("should not be called")

    monkeypatch.setattr(client_mod.asyncio, "open_connection", counting_open_connection)
    assert await client.unreachable_reason() == reason
    assert attempts == []


async def test_probe_rechecks_after_cooldown(monkeypatch):
    port = _free_port()
    client = SreAgentClient(f"http://127.0.0.1:{port}/sse")
    assert await client.unreachable_reason()

    client._unreachable_until = 0.0  # 창 만료 모사
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", port)
    try:
        assert await client.unreachable_reason() is None
    finally:
        server.close()
        await server.wait_closed()


# ── 3. 트리거 노드: 미가용이면 연결 없이 생략 ─────────────────────────────


class _Client:
    def __init__(self, reason):
        self.reason = reason
        self.connect_calls = 0
        self.submit_calls = 0

    async def unreachable_reason(self):
        return self.reason

    async def connect(self):
        self.connect_calls += 1

    async def disconnect(self):
        pass

    async def submit(self, payload):
        self.submit_calls += 1
        return {"investigation_id": "inv-1", "status": "accepted"}

    async def poll(self, investigation_id):
        return {"status": "stub", "briefing": {"stub": True}}


class _Store:
    def __init__(self):
        self.calls: list[dict] = []

    def record_investigation(self, **kwargs):
        self.calls.append(kwargs)


def _trigger_config(client, store) -> dict:
    ng = SimpleNamespace(
        investigation_trigger_enabled=True,
        investigation_trigger_min_tier="PAGE",
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
    )
    return {"configurable": {
        "app_config": SimpleNamespace(
            noise_gate=ng,
            host_authz=SimpleNamespace(mode="admin_only"),
            composite=SimpleNamespace(
                prior_targets_enabled=False, availability_precheck_enabled=False
            ),
        ),
        "sre_agent_client": client,
        "decision_store": store,
    }}


def _trigger_state() -> dict:
    return {
        "alarm_event": _event(), "notification_decision": _decision(),
        "recurrence": None, "correlation_meta": None,
    }


async def test_trigger_skips_when_service_unreachable(caplog):
    client = _Client("127.0.0.1:9098 연결 불가(Connection refused)")
    store = _Store()
    with caplog.at_level(logging.WARNING):
        out = await investigation_trigger_node(_trigger_state(), _trigger_config(client, store))

    assert out == {}
    assert client.connect_calls == 0 and client.submit_calls == 0
    assert [c["status"] for c in store.calls] == ["down"]
    warnings = [r for r in caplog.records if "조사 서비스 미가용" in r.getMessage()]
    assert len(warnings) == 1 and warnings[0].exc_info is None


async def test_trigger_proceeds_when_service_reachable():
    client = _Client(None)
    out = await investigation_trigger_node(_trigger_state(), _trigger_config(client, _Store()))
    assert client.submit_calls == 1
    assert out.get("investigation_briefing") == {"stub": True}
