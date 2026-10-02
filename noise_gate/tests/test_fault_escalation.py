"""Plan 64 Wave 3-B (CW-C) — escalate-only 후속 통보 승격 계약 테스트.

검증 범위(sre_agent 패키지 미import — MCP 계약을 모사한 FakeSreClient 재사용):
  1. verdict.escalate 소비: dict(escalate=True)/문자열("escalate") → 상향 안내 첨부.
  2. escalate-only: verdict escalate=False/미escalate/문자열 비-escalate → 미첨부.
  3. 옵트인: fault_escalation_enabled off → verdict.escalate여도 미첨부(비트동일).
  4. 소급 변경 없음: notification_decision(tier/reason/priority)은 불변(상향만·하향 없음).
  5. 렌더: build_workb_body가 상향 안내 블록을 첨부(None이면 비트동일).
  6. verdict 판정 헬퍼: verdict_escalates/build_escalation 단위(domain — 트리거·후속 공용).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.application.nodes.alarm_notifier import build_workb_body
from noise_gate.application.nodes.investigation_trigger import (
    investigation_trigger_node,
)
from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.investigation_payload import (
    build_escalation as _build_escalation,
    verdict_escalates as _verdict_escalates,
)
from noise_gate.domain.notification_policy import NotificationDecision, TIER_PAGE


# ── 헬퍼 ────────────────────────────────────────────────────────────────

def _authz_ns():
    """조사 인가·복합 설정 대역 (Plan 78 W3-5 · W1).

    실제 `AppConfig`는 이 둘을 **항상** 갖는다. 대역에서 빼면 인가가 `unknown_authz_mode`로
    차단된다 — fail-closed의 설계 의도이므로 정책이 아니라 대역을 프로덕션 형태에 맞춘다.
    """
    return {
        "host_authz": SimpleNamespace(mode="admin_only"),
        "composite": SimpleNamespace(prior_targets_enabled=False),
    }

def _event() -> AlarmEvent:
    return AlarmEvent(
        db_id="db1", server_name="srv-1", hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/root/srv-1", alarm_id="A-1", severity=2,
        alarm_status="NOT_ACK", resource_type="server.Cpus", resource_name="r1",
        alarm_name="CPU High", alarm_time=datetime(2026, 7, 27, 12, 0, 0),
        conditions=">90%", condition_log="cpu 95%",
    )


def _decision(fp: str = "fp-1") -> NotificationDecision:
    return NotificationDecision(
        tier=TIER_PAGE, reason="심각도2·중요도높음", priority=3,
        signals={"root_resource": None}, fingerprint=fp,
    )


def _ng_cfg(**over) -> SimpleNamespace:
    base = dict(
        investigation_trigger_enabled=True,
        investigation_trigger_min_tier="PAGE",
        investigation_poll_interval_seconds=0.0,
        investigation_total_timeout_seconds=5.0,
        fault_escalation_enabled=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _state(decision) -> dict:
    return {
        "alarm_event": _event(), "notification_decision": decision,
        "recurrence": None, "correlation_meta": None,
    }


def _config(ng, client) -> dict:
    return {"configurable": {
        "app_config": SimpleNamespace(noise_gate=ng, **_authz_ns()),
        "sre_agent_client": client, "decision_store": None,
    }}


class FakeSreClient:
    """submit(sre_investigate_alarm)/poll 계약을 모사(verdict 반환 제어)."""

    def __init__(self, verdict=None, briefing=None) -> None:
        self.verdict = verdict
        self.briefing = briefing if briefing is not None else {
            "stub": True, "message": "조사 미실행(스텁)"
        }

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def submit(self, payload: dict) -> dict:
        return {"investigation_id": "inv-1", "status": "accepted"}

    async def poll(self, investigation_id: str) -> dict:
        return {"status": "done", "briefing": self.briefing, "verdict": self.verdict}


# ── 1. verdict 판정 헬퍼 ─────────────────────────────────────────────────
class TestVerdictHelpers:
    def test_dict_escalate_true(self):
        assert _verdict_escalates({"escalate": True, "level": "critical"}) is True

    def test_dict_escalate_false(self):
        assert _verdict_escalates({"escalate": False}) is False

    def test_string_escalate(self):
        assert _verdict_escalates("escalate") is True
        assert _verdict_escalates("ESCALATE") is True

    def test_string_non_escalate(self):
        assert _verdict_escalates("hold") is False

    def test_none_and_other(self):
        assert _verdict_escalates(None) is False
        assert _verdict_escalates(123) is False

    def test_build_escalation_carries_signals(self):
        esc = _build_escalation(
            {"escalate": True, "level": "critical", "confidence": "high",
             "signals": ["OOM", "restart-loop"]}
        )
        assert esc["escalate"] is True and esc["level"] == "critical"
        assert esc["confidence"] == "high" and esc["signals"] == ["OOM", "restart-loop"]

    def test_build_escalation_none_when_not_escalating(self):
        assert _build_escalation({"escalate": False}) is None
        assert _build_escalation("hold") is None


# ── 2. 노드: verdict.escalate 소비(escalate-only) ────────────────────────
class TestNodeEscalation:
    async def test_dict_verdict_escalate_attaches(self):
        client = FakeSreClient(verdict={
            "escalate": True, "level": "critical", "signals": ["OOM"]
        })
        out = await investigation_trigger_node(_state(_decision()), _config(_ng_cfg(), client))
        esc = out.get("investigation_escalation")
        assert esc is not None and esc["escalate"] is True
        assert esc["level"] == "critical" and esc["signals"] == ["OOM"]

    async def test_string_verdict_escalate_attaches(self):
        client = FakeSreClient(verdict="escalate")
        out = await investigation_trigger_node(_state(_decision()), _config(_ng_cfg(), client))
        assert out.get("investigation_escalation") == {"escalate": True}

    async def test_non_escalate_verdict_no_attach(self):
        client = FakeSreClient(verdict={"escalate": False, "level": "warning"})
        out = await investigation_trigger_node(_state(_decision()), _config(_ng_cfg(), client))
        assert "investigation_escalation" not in out

    async def test_string_non_escalate_no_attach(self):
        client = FakeSreClient(verdict="hold")
        out = await investigation_trigger_node(_state(_decision()), _config(_ng_cfg(), client))
        assert "investigation_escalation" not in out

    async def test_disabled_flag_no_attach_even_if_escalate(self):
        # fault_escalation off면 verdict.escalate여도 미첨부(옵트인·비트동일).
        client = FakeSreClient(verdict={"escalate": True})
        out = await investigation_trigger_node(
            _state(_decision()), _config(_ng_cfg(fault_escalation_enabled=False), client)
        )
        assert "investigation_escalation" not in out
        # CW-A 브리핑은 여전히 첨부(트리거는 켜져 있으므로).
        assert "investigation_briefing" in out

    async def test_escalate_only_decision_unchanged(self):
        # 소급 변경 없음 — 노드는 notification_decision을 반환/변경하지 않는다(상향만).
        decision = _decision()
        client = FakeSreClient(verdict={"escalate": True})
        out = await investigation_trigger_node(_state(decision), _config(_ng_cfg(), client))
        assert "notification_decision" not in out  # 게이트 판정 소급 변경/하향 없음
        assert decision.tier == TIER_PAGE and decision.priority == 3  # 원 결정 불변


# ── 3. 렌더 ──────────────────────────────────────────────────────────────
def _analysis() -> SimpleNamespace:
    ev = SimpleNamespace(
        severity=2, alarm_name="CPU", server_name="srv-1", hostname="h1",
        ip_address="10.0.0.1", resource_ancestry="root/srv-1",
        resource_type="server.Cpus", resource_name="r1", alarm_status="OPEN",
        conditions=">90%", condition_log="cpu high",
    )
    return SimpleNamespace(
        alarm_event=ev, severity_label="심각", summary="s", probable_cause="c",
        recommended_action="a", pattern_type="", pattern_analysis="",
    )


class TestEscalationRender:
    def test_escalation_block_rendered(self):
        body = build_workb_body(_analysis(), investigation_escalation={
            "escalate": True, "level": "critical", "confidence": "high",
            "signals": ["OOM", "restart-loop"],
        })
        assert "[중요도 상향]" in body
        assert "critical" in body and "OOM, restart-loop" in body
        assert "게이트 통보 판정은 유지" in body  # escalate-only 안내

    def test_minimal_escalation_rendered(self):
        body = build_workb_body(_analysis(), investigation_escalation={"escalate": True})
        assert "[중요도 상향]" in body

    def test_none_escalation_bit_identical(self):
        base = build_workb_body(_analysis())
        explicit = build_workb_body(_analysis(), investigation_escalation=None)
        assert base == explicit
        assert "[중요도 상향]" not in base
