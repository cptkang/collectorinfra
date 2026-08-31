"""장애 진단 경로의 가용성 사전 판정 (Plan 81 T7 · G-2 확정 · D-175).

G-2 확정: 가용성 비정상 대상은 **거부 + 사실 브리핑** — `sre_agent`에 위임하지 않는다.

왜 위임 전인가: 조사는 분 단위로 길다(실측 161s · 전체 타임아웃 300s). 죽은 대상에
그 예산을 태우면 정작 필요한 조사가 시간당 예산에서 밀린다(`docs/25` L-5 취지).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import noise_gate.infrastructure.polestar_hostname_resolver as resolver_mod
import src.nodes.fault_diagnosis as fd
from noise_gate.infrastructure.polestar_hostname_resolver import HostLookup
from src.domain.host_availability import judge_availability
from src.observability import investigation_metrics as metrics


class _FakeSreClient:
    """위임되면 안 되는 것을 단언하기 위한 대역(호출되면 흔적이 남는다)."""

    def __init__(self):
        self.diagnose_calls: list[dict] = []

    async def connect(self):
        return None

    async def disconnect(self):
        return None

    async def diagnose(self, question, server_name=None, hostname=None, db_id=None):
        self.diagnose_calls.append({"hostname": hostname, "db_id": db_id})
        return {"status": "accepted", "investigation_id": "inv-1"}

    async def poll(self, investigation_id):
        return {"status": "done", "answer": "진단 결과"}


def _gate_cfg():
    return SimpleNamespace(
        fault_diagnosis_enabled=True,
        investigation_service_url="http://localhost:9098/sse",
        investigation_service_token="",
        investigation_mcp_call_timeout_seconds=10.0,
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
    )


def _app_cfg(*, precheck=True, block=True):
    return SimpleNamespace(
        noise_gate=_gate_cfg(),
        host_authz=SimpleNamespace(mode="admin_only"),
        composite=SimpleNamespace(
            prior_targets_enabled=False,
            max_targets=10,
            availability_precheck_enabled=precheck,
            availability_block_on_unavailable=block,
        ),
    )


def _state():
    return {
        "user_query": "web-01 서버 장애 원인 분석해줘",
        "active_db_id": "polestar_b0",
        "parsed_requirements": {
            "filter_conditions": [{"field": "hostname", "value": "web-01"}]
        },
        "conversation_context": None,
        "user_role": "admin",
        "user_id": "admin-1",
    }


@pytest.fixture
def wired(monkeypatch):
    client = _FakeSreClient()
    monkeypatch.setattr(fd, "_build_client", lambda gate_cfg: client)
    metrics.reset()

    lookups: dict = {}

    async def _fake_lookup(app_config, db_id, value):
        return lookups.get(value, HostLookup(value, value, judge_availability(avail_status=0)))

    monkeypatch.setattr(resolver_mod, "lookup_host", _fake_lookup)
    yield client, lookups
    metrics.reset()


def _down(lookups, host="web-01"):
    lookups[host] = HostLookup(
        host, host, judge_availability(avail_status=1, as_of="2026-08-28 09:00:00")
    )


class TestBlocksUnavailableTarget:
    async def test_비정상_대상은_sre_agent에_위임하지_않는다(self, wired):
        client, lookups = wired
        _down(lookups)
        out = await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        assert client.diagnose_calls == [], "죽은 대상에 조사 예산을 태우지 않는다"

    async def test_사유와_확인_시각을_응답한다(self, wired):
        _, lookups = wired
        _down(lookups)
        out = await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        text = out["final_response"]
        assert "비정상(중지/통신이상)" in text
        assert "2026-08-28 09:00:00" in text
        assert "조사를 수행하지 않았습니다" in text

    async def test_라우팅_계약은_유지된다(self, wired):
        _, lookups = wired
        _down(lookups)
        out = await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        assert out["routing_intent"] == "fault_diagnosis"
        assert out["current_node"] == "fault_diagnosis"

    async def test_거부_지표가_사유별로_집계된다(self, wired):
        _, lookups = wired
        _down(lookups)
        await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        routing = metrics.snapshot()["routing"]
        assert routing["denied"] == 1
        assert routing["denied_by_reason"]["target_unavailable"] == 1


class TestPassThrough:
    async def test_정상_대상은_그대로_위임한다(self, wired):
        client, _ = wired
        out = await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        assert client.diagnose_calls[0]["hostname"] == "web-01"
        assert out["final_response"] == "진단 결과"

    async def test_판정_불가는_위임을_막지_않는다(self, wired):
        """fail-open — 조회 실패로 정상 조사를 잃으면 안 된다."""
        client, lookups = wired
        lookups["web-01"] = HostLookup(None, None, judge_availability(lookup_failed=True))
        await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        assert client.diagnose_calls

    async def test_점검_상태는_조사를_막지_않는다(self, wired):
        client, lookups = wired
        lookups["web-01"] = HostLookup(
            "web-01", "web-01", judge_availability(avail_status=0, is_maintenance=1)
        )
        await fd.fault_diagnosis(_state(), app_config=_app_cfg())
        assert client.diagnose_calls

    async def test_판정_off면_종전대로_위임한다(self, wired):
        client, lookups = wired
        _down(lookups)
        await fd.fault_diagnosis(_state(), app_config=_app_cfg(precheck=False))
        assert client.diagnose_calls, "판정을 끄면 회귀 0 — 종전 경로 그대로"

    async def test_관찰_모드도_위임한다(self, wired):
        client, lookups = wired
        _down(lookups)
        await fd.fault_diagnosis(_state(), app_config=_app_cfg(block=False))
        assert client.diagnose_calls

    async def test_대상_미식별이면_판정하지_않는다(self, wired, monkeypatch):
        """db_id·식별자가 없으면 조회할 곳이 없다 — 종전 경로(위임 후 거부)로 간다."""
        client, _ = wired
        called = []

        async def _boom(app_config, db_id, value):
            called.append(value)
            raise AssertionError("대상 미식별인데 조회를 시도했다")

        monkeypatch.setattr(resolver_mod, "lookup_host", _boom)
        state = _state()
        state["parsed_requirements"] = {"filter_conditions": []}
        state["active_db_id"] = None
        await fd.fault_diagnosis(state, app_config=_app_cfg())
        assert called == []
