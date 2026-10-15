"""plans/162 W5 · D-336 — 제니퍼 정책 경로 심각도3은 유형 정책 표를 따른다.

- 플래그 off(기본): 정책 경로 sev3도 단락 PAGE · 현행 사유 · evidence 무가산(비트 동일)
- 플래그 on: 정책 경로 sev3 — noti_policy "notify"면 PAGE, 그 밖은 TICKET(단계 severity3 그대로)
- 바뀌지 않는 것: 폴스타 sev3 · 수집 실패(None·unavailable) sev3 · 정책 경로 sev2 · 비운영 알람 선행
- 종단: 실제 정책 표(config/apm_noise_policy.yaml)로 표 밖 유형 TICKET · page 유형 PAGE
"""

from __future__ import annotations

import pytest

from noise_gate.domain.notification_policy import (
    EVIDENCE_APM_SEV3_POLICY,
    NOISE_SOURCE_APM_POLICY,
    STAGE_NON_ALARM,
    STAGE_SEVERITY3,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    NotificationDecision,
    decide_notification,
    stage_from_reason,
)
from noise_gate.infrastructure.apm_noise_context import ApmNoiseContext
from noise_gate.infrastructure.polestar_noise_context import _unavailable
from noise_gate.tests.test_notification_policy import (
    IMP_MAP,
    make_config,
    make_ctx,
    make_event,
)
from noise_gate.tests.test_plan144_apm_policy import _apm_event, _apm_policy

_LEGACY_REASON = "심각도3 — 항상 통보(억제 단계 미경유)"


def _cfg(**kwargs):
    return make_config(importance_value_map=IMP_MAP, **kwargs)


def _apm_ctx(noti_policy: str | None) -> dict:
    """정책 경로 노이즈 컨텍스트(ApmNoiseContext.fetch 모양)."""
    return make_ctx(importance_id="보통", noti_policy=noti_policy, source=NOISE_SOURCE_APM_POLICY)


def _key(d: NotificationDecision) -> tuple:
    return (d.tier, d.reason, d.priority, d.signals, d.fingerprint, d.stage, d.evidence)


class TestFlagOff:
    @pytest.mark.parametrize("noti", ["notify", None])
    def test_policy_path_sev3_is_legacy_page(self, noti):
        d = decide_notification(make_event(severity=3), None, None, _apm_ctx(noti), _cfg())
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_SEVERITY3
        assert d.reason == _LEGACY_REASON
        assert EVIDENCE_APM_SEV3_POLICY not in d.evidence

    def test_explicit_false_equals_default(self):
        event = make_event(severity=3)
        base = decide_notification(event, None, None, _apm_ctx(None), _cfg())
        explicit = decide_notification(
            event, None, None, _apm_ctx(None), _cfg(apm_sev3_policy_enabled=False)
        )
        assert _key(base) == _key(explicit)


class TestFlagOn:
    def test_page_type_stays_page(self):
        d = decide_notification(
            make_event(severity=3), None, None, _apm_ctx("notify"),
            _cfg(apm_sev3_policy_enabled=True),
        )
        assert d.tier == TIER_PAGE
        assert d.stage == STAGE_SEVERITY3
        assert d.reason.startswith("심각도3")
        assert "제니퍼 유형 정책" in d.reason
        assert d.evidence[EVIDENCE_APM_SEV3_POLICY] == {"notify": "page", "policy_row": None}

    def test_non_page_type_is_ticket(self):
        policy = {"event_type_norm": "FAKE_TYPE", "policy_row": "default", "persistence": None}
        d = decide_notification(
            make_event(severity=3), None, None, _apm_ctx(None),
            _cfg(apm_sev3_policy_enabled=True), apm_policy=policy,
        )
        assert d.tier == TIER_TICKET
        assert d.stage == STAGE_SEVERITY3
        assert d.reason.startswith("심각도3")
        assert d.evidence[EVIDENCE_APM_SEV3_POLICY] == {"notify": "ticket", "policy_row": "default"}
        assert d.evidence["apm_policy"] == policy

    def test_maintenance_does_not_suppress_ticket(self):
        # step 3에서 끝난다 — 뒤 억제 단계(유지보수 등)로 흘러가지 않는다.
        ctx = {**_apm_ctx(None), "maintenance": True}
        d = decide_notification(
            make_event(severity=3), None, None, ctx, _cfg(apm_sev3_policy_enabled=True)
        )
        assert d.tier == TIER_TICKET and d.stage == STAGE_SEVERITY3

    @pytest.mark.parametrize("reason_tier", [TIER_PAGE, TIER_TICKET])
    def test_reason_prefix_maps_to_severity3(self, reason_tier):
        noti = "notify" if reason_tier == TIER_PAGE else None
        d = decide_notification(
            make_event(severity=3), None, None, _apm_ctx(noti),
            _cfg(apm_sev3_policy_enabled=True),
        )
        assert d.tier == reason_tier
        assert stage_from_reason(d.reason) == STAGE_SEVERITY3

    def test_polestar_sev3_unchanged(self):
        ctx = make_ctx(noti_policy=None)
        on = decide_notification(
            make_event(severity=3), None, None, ctx, _cfg(apm_sev3_policy_enabled=True)
        )
        off = decide_notification(make_event(severity=3), None, None, ctx, _cfg())
        assert on.tier == TIER_PAGE and on.reason == _LEGACY_REASON
        assert _key(on) == _key(off)

    @pytest.mark.parametrize("ctx", [None, make_ctx(source="unavailable")])
    def test_collection_failed_sev3_unchanged(self, ctx):
        on = decide_notification(
            make_event(severity=3), None, None, ctx, _cfg(apm_sev3_policy_enabled=True)
        )
        off = decide_notification(make_event(severity=3), None, None, ctx, _cfg())
        assert on.tier == TIER_PAGE and on.reason == _LEGACY_REASON
        assert _key(on) == _key(off)

    @pytest.mark.parametrize("noti", ["notify", None])
    def test_policy_path_sev2_same_as_off(self, noti):
        event = make_event(severity=2)
        on = decide_notification(
            event, None, None, _apm_ctx(noti), _cfg(apm_sev3_policy_enabled=True)
        )
        off = decide_notification(event, None, None, _apm_ctx(noti), _cfg())
        assert _key(on) == _key(off)

    def test_non_alarm_filter_still_first(self):
        d = decide_notification(
            make_event(severity=3, alarm_name="작업 승인 요청 바랍니다"), None, None,
            _apm_ctx(None),
            _cfg(apm_sev3_policy_enabled=True, non_alarm_filter_enabled=True),
        )
        assert d.tier == TIER_SUPPRESS and d.stage == STAGE_NON_ALARM


class TestEndToEndWithRealPolicyTable:
    """실제 정책 표(config/apm_noise_policy.yaml) → ApmNoiseContext.fetch → 판정."""

    @pytest.fixture(scope="class")
    def provider(self) -> ApmNoiseContext:
        p = ApmNoiseContext.load()
        assert p.available, p.error
        return p

    def _decide(self, provider: ApmNoiseContext, alarm_name: str, *, on: bool):
        ev = _apm_event(alarm_name, severity=3)
        return decide_notification(
            ev, None, None, provider.fetch(ev), _cfg(apm_sev3_policy_enabled=on),
            apm_policy=_apm_policy(ev, provider),
        )

    def test_unlisted_type_is_ticket(self, provider):
        d = self._decide(provider, "FAKE_UNLISTED_EVENT_TYPE", on=True)
        assert d.tier == TIER_TICKET and d.stage == STAGE_SEVERITY3
        assert d.evidence[EVIDENCE_APM_SEV3_POLICY] == {"notify": "ticket", "policy_row": "default"}

    def test_page_type_is_page(self, provider):
        d = self._decide(provider, "ERROR_OUTOFMEMORY", on=True)
        assert d.tier == TIER_PAGE and d.stage == STAGE_SEVERITY3
        assert d.evidence[EVIDENCE_APM_SEV3_POLICY] == {
            "notify": "page", "policy_row": "OUTOFMEMORY",
        }

    def test_off_unlisted_type_is_legacy_page(self, provider):
        d = self._decide(provider, "FAKE_UNLISTED_EVENT_TYPE", on=False)
        assert d.tier == TIER_PAGE and d.reason == _LEGACY_REASON

    def test_unavailable_provider_stays_page(self):
        ev = _apm_event("FAKE_UNLISTED_EVENT_TYPE", severity=3)
        d = decide_notification(
            ev, None, None, _unavailable(), _cfg(apm_sev3_policy_enabled=True)
        )
        assert d.tier == TIER_PAGE and d.reason == _LEGACY_REASON


def test_config_default_off_and_env_key():
    from src.config import NoiseGateConfig

    assert NoiseGateConfig.model_fields["apm_sev3_policy_enabled"].default is False
    on = NoiseGateConfig.model_validate({"apm_sev3_policy_enabled": "true"})
    assert on.apm_sev3_policy_enabled is True
