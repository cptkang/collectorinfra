"""알람 자동 조사의 가용성 판정 전달 (Plan 81 T10 · G-3 확정 · D-175).

두 가지를 고정한다:
    ① 대상이 가용하지 않으면 **판정을 페이로드에 실어** 보낸다 — `sre_agent`가 조사 예산을
       쓰기 전에 거부한다(본체 게이트는 본체 경유 조사만 막는다 · `docs/25` L-4와 같은 구조).
    ② **가용성/다운 계열 알람은 예외** — 대상이 다운인 것이 당연하므로 판정하면 "왜 내려갔나"
       조사가 통째로 막힌다(G-3).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import noise_gate.infrastructure.polestar_hostname_resolver as resolver_mod
from noise_gate.application.nodes.investigation_trigger import investigation_trigger_node
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.investigation_payload import is_availability_alarm
from noise_gate.infrastructure.polestar_hostname_resolver import HostLookup
from src.domain.host_availability import judge_availability


def _event(**over) -> AlarmEvent:
    base = dict(
        db_id="polestar_gimpo", server_name="web-01", hostname="web-01",
        ip_address="10.0.0.1", resource_ancestry="", alarm_id="a-1", severity=3,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="CPU",
        alarm_name="CPU 사용률 임계 초과", alarm_time="20260828090000",
        conditions="cpu > 90", condition_log="cpu=95",
    )
    base.update(over)
    return AlarmEvent(**base)


def _decision(tier="PAGE"):
    return SimpleNamespace(tier=tier, reason="", fingerprint="fp-1", signals={})


class _Client:
    """submit 페이로드를 붙잡아 두는 대역(계약만 모사 — sre_agent import 0)."""

    def __init__(self):
        self.payloads: list[dict] = []

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def submit(self, payload):
        self.payloads.append(payload)
        return {"status": "accepted", "investigation_id": "inv-1"}

    async def poll(self, investigation_id):
        return {"status": "stub", "briefing": {"stub": True, "message": "m"}}


def _cfg(*, precheck=True):
    return SimpleNamespace(
        noise_gate=SimpleNamespace(
            investigation_trigger_enabled=True,
            investigation_trigger_min_tier="PAGE",
            investigation_poll_interval_seconds=0.0,
            investigation_total_timeout_seconds=5.0,
        ),
        host_authz=SimpleNamespace(mode="admin_only"),
        composite=SimpleNamespace(
            prior_targets_enabled=False, availability_precheck_enabled=precheck
        ),
    )


@pytest.fixture
def wired(monkeypatch):
    client = _Client()
    lookups: dict = {}

    async def _fake_lookup(app_config, db_id, value):
        return lookups.get(value, HostLookup(value, value, judge_availability(avail_status=0)))

    monkeypatch.setattr(resolver_mod, "lookup_host", _fake_lookup)
    yield client, lookups


async def _run(client, event, *, precheck=True):
    state = {
        "alarm_event": event,
        "notification_decision": _decision(),
        "recurrence": None,
        "correlation_meta": None,
    }
    config = {"configurable": {
        "app_config": _cfg(precheck=precheck),
        "sre_agent_client": client,
        "decision_store": None,
    }}
    return await investigation_trigger_node(state, config)


def _down(lookups, host="web-01"):
    lookups[host] = HostLookup(
        host, host, judge_availability(avail_status=1, as_of="2026-08-28 09:00:00")
    )


class TestTargetStateDelivery:
    async def test_비정상_판정이_페이로드에_실린다(self, wired):
        client, lookups = wired
        _down(lookups)
        await _run(client, _event())
        state = client.payloads[0]["meta"]["target_state"]
        assert state["state"] == "unavailable"
        assert state["as_of"] == "2026-08-28 09:00:00"

    async def test_정상_판정도_함께_실어_감사에_남긴다(self, wired):
        client, _ = wired
        await _run(client, _event())
        assert client.payloads[0]["meta"]["target_state"]["state"] == "available"

    async def test_트리거_자체는_취소하지_않는다(self, wired):
        """침묵보다 '왜 조사하지 않았는지'가 담긴 브리핑이 낫다."""
        client, lookups = wired
        _down(lookups)
        await _run(client, _event())
        assert len(client.payloads) == 1

    async def test_판정_off면_키_자체가_없어_페이로드가_종전과_동일하다(self, wired):
        client, lookups = wired
        _down(lookups)
        await _run(client, _event(), precheck=False)
        assert "target_state" not in client.payloads[0]["meta"]


class TestAvailabilityAlarmException:
    """G-3 — 다운 알람의 원인 조사를 막지 않는다."""

    @pytest.mark.parametrize("alarm_name", [
        "서버 다운", "Server DOWN", "가용성 이상", "Ping 실패",
        "에이전트 통신 이상", "Power off 감지",
    ])
    def test_가용성_계열_알람을_알아본다(self, alarm_name):
        assert is_availability_alarm(_event(alarm_name=alarm_name)) is True

    @pytest.mark.parametrize("alarm_name", [
        "CPU 사용률 임계 초과", "메모리 사용률 경고", "디스크 사용량 초과",
    ])
    def test_자원_알람은_가용성_계열이_아니다(self, alarm_name):
        assert is_availability_alarm(_event(alarm_name=alarm_name)) is False

    async def test_다운_알람은_판정을_건너뛰고_조사를_진행한다(self, wired):
        client, lookups = wired
        _down(lookups)
        await _run(client, _event(alarm_name="서버 다운"))
        assert "target_state" not in client.payloads[0]["meta"], (
            "다운 알람에 판정을 실으면 '왜 내려갔나' 조사가 막힌다"
        )

    async def test_조건문에_있는_표면어도_인정한다(self, wired):
        client, lookups = wired
        _down(lookups)
        await _run(client, _event(alarm_name="상태 이상", conditions="ping unreachable"))
        assert "target_state" not in client.payloads[0]["meta"]


class TestFailOpen:
    async def test_판정_불가는_그대로_전달하되_막지_않는다(self, wired):
        client, lookups = wired
        lookups["web-01"] = HostLookup(None, None, judge_availability(lookup_failed=True))
        await _run(client, _event())
        assert client.payloads[0]["meta"]["target_state"]["state"] == "unknown"

    async def test_조회_예외가_트리거를_막지_않는다(self, wired, monkeypatch):
        client, _ = wired

        async def _boom(app_config, db_id, value):
            raise RuntimeError("DB 다운")

        monkeypatch.setattr(resolver_mod, "lookup_host", _boom)
        with pytest.raises(RuntimeError):
            # lookup_host 자체는 예외를 삼키지만, 여기서는 대역이 직접 던져
            # 상위가 이를 삼키지 않음을 드러낸다(진단 가시성).
            await _run(client, _event())
