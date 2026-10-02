"""plans/87 J8 · D-287 — `noise_gate` 다중 제니퍼 소스 소비측.

검증 범위:
    A. 배지 상세 — APM 알람 dbId `jennifer_<소스 id>`의 존을 레지스트리 소스로 풀어 툴팁에 싣는다
       (「제니퍼 — 은행존; jennifer_bank」). 존 없는 `jennifer`(단일 설정)는 종전과 바이트 동일.
    B. `is_apm_source` — 원문 `source`가 없어도 dbId `jennifer_<id>`면 APM 소스다.
    C. `apm_source_ids_for` — 폴스타 알람 존 → 그 존의 제니퍼 소스 id(선언 순서) · 없으면
       None(전 소스).
    D. `app_impact` — 알람 존의 소스만 `source_ids`로 넘긴다 · 소스가 없으면 키 자체가 없다
       (종전 호출) · 게이트웨이 `invalid_argument`는 판정 유지 + 사유 기록이고 전 소스로
       재시도하지 않는다(호출 1회).
    E. 조사 힌트 `source_id` · 게이트웨이 클라이언트 인자.
"""

from __future__ import annotations

import logging

import pytest

import noise_gate.application.nodes.notification_gate as gate_mod
from noise_gate.application.nodes.notification_gate import (
    apm_source_ids_for,
    notification_gate_node,
)
from noise_gate.application.server_identity import (
    apm_source_labels,
    attach_server_identity,
    is_apm_source,
    zone_labels_for,
)
from noise_gate.domain.investigation_payload import build_trigger_payload
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    NotificationDecision,
    decide_notification,
)
from noise_gate.infrastructure.apm_gateway_client import APM_EVENTS_TOOL, ApmGatewayClient
from noise_gate.tests.test_plan87_apm_consumer import (
    _analysis,
    _ctx,
    _FakeApmClient,
    _fatal_rows,
    _gate_config,
    _gate_state,
    _policy_cfg,
    _SpyStore,
    apm_event,
    gateway_record,
    polestar_event,
)

BANK_ZONE_LABEL = "은행존(K리전 은행/레거시)"


def _source_event(db_id: str, *, domain: str | None = "운영도메인", source_id: str | None = None,
                  raw_source: bool = True):
    """게이트웨이 다중 소스 알람(dbId `jennifer_<id>` · `apm.source_id`) — 값은 합성."""
    rec = gateway_record(dbId=db_id)
    apm = dict(rec["apm"])
    if domain is None:
        apm.pop("domain_name")
    else:
        apm["domain_name"] = domain
    if source_id is not None:
        apm["source_id"] = source_id
    rec["apm"] = apm
    if not raw_source:
        rec.pop("source")
    return apm_event(db_id=db_id, raw_payload=rec)


# ═════════════════════════════════════════════════════════════════════════════
# A. 배지 상세
# ═════════════════════════════════════════════════════════════════════════════


class TestSourceBadgeDetail:
    async def test_bank_source_without_domain(self):
        ident = await attach_server_identity(_source_event("jennifer_bank", domain=None), None)
        assert ident.source_label == "제니퍼"
        assert ident.source_detail == "제니퍼 — 은행존; jennifer_bank"
        assert (ident.zone, ident.zone_label, ident.site_label) == ("bankjon", BANK_ZONE_LABEL, "")

    async def test_bank_source_with_domain(self):
        ident = await attach_server_identity(_source_event("jennifer_bank"), None)
        assert ident.source_detail == "제니퍼 — 은행존 운영도메인; jennifer_bank"

    async def test_common_and_legacy_sources(self):
        common = await attach_server_identity(_source_event("jennifer_common"), None)
        legacy = await attach_server_identity(_source_event("jennifer_legacy", domain=None), None)
        assert common.source_detail == "제니퍼 — 공동존 운영도메인; jennifer_common"
        assert common.zone == "gongjon"
        assert legacy.source_detail == "제니퍼 — 은행존; jennifer_legacy"
        assert legacy.zone == "bankjon"

    async def test_default_single_setting_is_byte_identical(self):
        ident = await attach_server_identity(apm_event(), None)
        assert ident.source_detail == "제니퍼 — 운영도메인; jennifer"
        assert (ident.zone, ident.zone_label, ident.site_label) == ("", "", "")

    async def test_unknown_source_has_no_zone(self):
        ident = await attach_server_identity(_source_event("jennifer_zzz"), None)
        assert ident.source_label == "제니퍼"
        assert ident.source_detail == "제니퍼 — 운영도메인; jennifer_zzz" and ident.zone == ""

    def test_labels_function_signature_keeps_default(self):
        """zone_label 기본값(빈 값)이면 종전 모양 그대로다."""
        assert apm_source_labels(apm_event()) == ("제니퍼", "제니퍼 — 운영도메인; jennifer")

    def test_zone_labels_for_polestar_db_unchanged(self):
        assert zone_labels_for("polestar_b0") == ("bankjon", BANK_ZONE_LABEL, "은행존")
        assert zone_labels_for("polestar_cm_gp")[0] == "gongjon"
        assert zone_labels_for("jennifer") == ("", "", "")

    def test_registry_failure_is_warning_and_empty(self, monkeypatch, caplog):
        import src.routing.registry as reg_mod

        def _boom():
            raise RuntimeError("registry down")

        monkeypatch.setattr(reg_mod, "get_registry", _boom)
        with caplog.at_level(logging.WARNING):
            assert zone_labels_for("jennifer_bank") == ("", "", "")
        assert any("존/사이트 라벨 파생 실패" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# B. is_apm_source
# ═════════════════════════════════════════════════════════════════════════════


class TestIsApmSource:
    def test_prefixed_db_id_without_raw_source_is_apm(self):
        assert is_apm_source(_source_event("jennifer_bank", raw_source=False)) is True

    def test_polestar_event_is_not_apm(self):
        assert is_apm_source(polestar_event()) is False
        assert is_apm_source(polestar_event(db_id="jenniferbank")) is False


# ═════════════════════════════════════════════════════════════════════════════
# C. apm_source_ids_for
# ═════════════════════════════════════════════════════════════════════════════


class TestApmSourceIdsFor:
    @pytest.mark.parametrize(
        "db_id,expected",
        [
            ("polestar_b0", ["bank", "legacy"]),
            ("polestar_cm_gp", ["common"]),
            ("polestar_cm_yd", ["common"]),
            ("polestar", None),        # 존 없는 DB(로컬 샌드박스)
            ("cloud_portal", None),
            ("", None),
            ("jennifer_bank", None),   # APM 알람 자신은 대상이 아니다(DB 항목 존만 본다)
        ],
    )
    def test_zone_to_source_ids(self, db_id, expected):
        assert apm_source_ids_for(db_id) == expected

    def test_registry_failure_warns_and_returns_none(self, monkeypatch, caplog):
        import src.routing.registry as reg_mod

        def _boom():
            raise RuntimeError("registry down")

        monkeypatch.setattr(reg_mod, "get_registry", _boom)
        with caplog.at_level(logging.WARNING):
            assert apm_source_ids_for("polestar_b0") is None
        assert any("APM 소스 좁히기 실패" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# D. app_impact 소스 좁히기
# ═════════════════════════════════════════════════════════════════════════════


class TestAppImpactSourceNarrowing:
    async def test_bank_alarm_asks_only_bank_zone_sources(self):
        client = _FakeApmClient(_fatal_rows(1))
        out = await notification_gate_node(
            _gate_state(polestar_event(db_id="polestar_b0", severity=1)), _gate_config(client)
        )
        assert out["notification_decision"].tier == TIER_PAGE
        assert client.calls[0]["source_ids"] == ["bank", "legacy"]
        assert client.calls[0]["hostname"] == "was-app-01"

    async def test_common_alarm_asks_only_common_source(self):
        client = _FakeApmClient(_fatal_rows(1))
        await notification_gate_node(
            _gate_state(polestar_event(db_id="polestar_cm_yd", severity=1)), _gate_config(client)
        )
        assert client.calls[0]["source_ids"] == ["common"]

    async def test_zoneless_alarm_omits_source_ids_key(self):
        client = _FakeApmClient(_fatal_rows(1))
        await notification_gate_node(
            _gate_state(polestar_event(db_id="polestar", severity=1)), _gate_config(client)
        )
        assert client.calls == [{
            "hostname": "was-app-01",
            "reference_time": "2026-09-29T10:15:00",
            "lookback_minutes": 10,
            "level": "fatal",
            "investigation_id": "P-1",
        }]

    async def test_zone_without_sources_omits_source_ids_key(self, monkeypatch):
        monkeypatch.setattr(gate_mod, "_APM_SYSTEM", "no_such_system")
        client = _FakeApmClient(_fatal_rows(1))
        await notification_gate_node(
            _gate_state(polestar_event(db_id="polestar_b0", severity=1)), _gate_config(client)
        )
        assert "source_ids" not in client.calls[0]

    async def test_invalid_argument_keeps_decision_without_retry(self, caplog):
        resp = {
            "error": "invalid_argument",
            "reason": "모르는 source_ids: bank — 설정된 소스: default",
            "source": "jennifer",
            "tool": "apm_events",
        }
        client, store = _FakeApmClient(resp), _SpyStore()
        ev = polestar_event(db_id="polestar_b0", severity=1)
        expected = decide_notification(ev, None, _analysis(), _ctx("2"), _policy_cfg())
        with caplog.at_level(logging.WARNING):
            out = await notification_gate_node(_gate_state(ev), _gate_config(client, store))
        d = out["notification_decision"]
        assert (d.tier, d.reason, d.priority) == (expected.tier, expected.reason, expected.priority)
        assert d.tier == TIER_DASHBOARD and "noise_context" not in out
        assert len(client.calls) == 1, "전 소스로 재시도하지 않는다(다른 존 승격 방지)"
        assert client.calls[0]["source_ids"] == ["bank", "legacy"]
        assert "invalid_argument" in store.records[0]["stage_evidence"]["app_impact_error"]
        assert any("app_impact" in r.getMessage() for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# E. 조사 힌트 · 게이트웨이 클라이언트 인자
# ═════════════════════════════════════════════════════════════════════════════


class TestHintsAndClient:
    def test_hints_carry_source_id(self):
        ev = _source_event("jennifer_bank", source_id="bank")
        payload = build_trigger_payload(ev, NotificationDecision(TIER_PAGE, "r", 1, {}))
        hints = payload["meta"]["hints"]
        assert hints == {
            "solution": "apm",
            "source_id": "bank",
            "instance_id": 1001,
            "domain_id": 1000,
            "event_type": "ERROR_OUTOFMEMORY",
            "txid": "7142093822391",
        }

    async def _captured_arguments(self, monkeypatch, **extra) -> dict:
        client = ApmGatewayClient(server_url="http://127.0.0.1:9096/sse")
        seen: list[tuple[str, dict]] = []

        async def _fake_call(tool, arguments):
            seen.append((tool, arguments))
            return {"rows": [], "source": "jennifer"}

        monkeypatch.setattr(client, "_call", _fake_call)
        await client.apm_events(hostname="h", reference_time="2026-09-29T10:15:00",
                                lookback_minutes=10, level="fatal", investigation_id="P-1", **extra)
        assert len(seen) == 1 and seen[0][0] == APM_EVENTS_TOOL
        return seen[0][1]

    async def test_client_sends_source_ids_when_given(self, monkeypatch):
        ids = ["bank", "legacy"]
        args = await self._captured_arguments(monkeypatch, source_ids=ids)
        assert args["source_ids"] == ["bank", "legacy"] and args["source_ids"] is not ids

    @pytest.mark.parametrize("extra", [{}, {"source_ids": None}, {"source_ids": []}])
    async def test_client_omits_source_ids_when_absent(self, monkeypatch, extra):
        args = await self._captured_arguments(monkeypatch, **extra)
        assert args == {
            "hostname": "h",
            "reference_time": "2026-09-29T10:15:00",
            "lookback_minutes": 10,
            "level": "fatal",
            "investigation_id": "P-1",
        }
