"""notification_policy.decide_notification 결정적 규칙 단위 테스트 (Plan 52 E1).

심각도3 항상 PAGE, 유지보수 SUPPRESS, 자가복구·독립 해소, 보수적 기본값(수집실패),
signals 스키마, 핑거프린트 안정성, 중요도 매핑, 폴스타 알림정책 앵커를 검증한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    NotificationDecision,
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    compute_fingerprint,
    decide_notification,
    map_importance,
)

REF = datetime(2026, 6, 29, 14, 0, 0)


def make_event(**kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent 생성. severity를 주면 is_clear을 자동 동기화한다."""
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="cop0-aisapd02",
        hostname="saisvd01",
        ip_address="10.1.2.3",
        resource_ancestry="/Servers/svr/Cpus",
        alarm_id="CUR-001",
        severity=2,
        alarm_status="NOT_ACK",
        resource_type="server.Cpus",
        resource_name="svr-001-CPU",
        alarm_name="CPU 사용률 임계 초과",
        alarm_time=REF,
        conditions="",
        condition_log="",
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_config(**kwargs) -> SimpleNamespace:
    """기본 게이트 설정(보수적)."""
    base = dict(suppress_max_severity=2, importance_value_map={}, resolved_to_dashboard=False)
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx(
    *,
    importance_id="HIGH",
    maintenance=False,
    noti_policy=None,
    source="polestar_db",
) -> dict:
    """noise_ctx 동결 계약 dict 생성."""
    return {
        "importance_id": importance_id,
        "maintenance": maintenance,
        "noti_policy": noti_policy,
        "parent_avail_status": None,
        "source": source,
    }


# 중요도 코드 매핑(테스트 인스턴스 가정)
IMP_MAP = {"HIGH": "높음", "MID": "보통", "LOW": "낮음"}


class TestPriorityMatrix:
    """심각도×중요도 매트릭스 + 심각도3 항상 PAGE."""

    def test_severity3_always_page_all_importance(self):
        # 심각도3은 중요도·유지보수·routine 무관하게 항상 PAGE
        for imp in ("LOW", "MID", "HIGH"):
            event = make_event(severity=3)
            ctx = make_ctx(importance_id=imp, maintenance=True, noti_policy="suppress")
            cfg = make_config(importance_value_map=IMP_MAP)
            d = decide_notification(event, None, None, ctx, cfg)
            assert d.tier == TIER_PAGE
            assert "심각도3" in d.reason

    def test_severity2_matrix(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        cases = {"HIGH": TIER_PAGE, "MID": TIER_TICKET, "LOW": TIER_DASHBOARD}
        for imp, expected in cases.items():
            event = make_event(severity=2)
            d = decide_notification(event, None, None, make_ctx(importance_id=imp), cfg)
            assert d.tier == expected, f"sev2 {imp} → {d.tier}"

    def test_severity1_matrix(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        # E3 결정: sev1×낮음 SUPPRESS→DASHBOARD (저중요도 주의알람은 묵살 아닌 대시보드 강등)
        cases = {"HIGH": TIER_TICKET, "MID": TIER_DASHBOARD, "LOW": TIER_DASHBOARD}
        for imp, expected in cases.items():
            event = make_event(severity=1)
            d = decide_notification(event, None, None, make_ctx(importance_id=imp), cfg)
            assert d.tier == expected, f"sev1 {imp} → {d.tier}"

    def test_priority_monotonic_page_highest(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        page = decide_notification(make_event(severity=3), None, None, make_ctx(), cfg)
        dash = decide_notification(
            make_event(severity=1), None, None, make_ctx(importance_id="MID"), cfg
        )
        assert page.priority > dash.priority


class TestMaintenanceSuppress:
    def test_maintenance_suppress(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        # 유지보수 모드면 심각도2/높음(원래 PAGE)이라도 SUPPRESS
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="HIGH", maintenance=True)
        d = decide_notification(event, None, None, ctx, cfg)
        assert d.tier == TIER_SUPPRESS
        assert "유지보수" in d.reason

    def test_severity3_ignores_maintenance(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=3)
        ctx = make_ctx(importance_id="HIGH", maintenance=True)
        d = decide_notification(event, None, None, ctx, cfg)
        assert d.tier == TIER_PAGE


class TestSelfHeal:
    def test_self_heal_suppress_low_severity(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=0)  # is_clear 자동 True
        d = decide_notification(event, None, None, make_ctx(), cfg, self_heal=True)
        assert d.tier == TIER_SUPPRESS
        assert "자가복구" in d.reason
        assert d.signals["self_heal"] is True

    def test_self_heal_does_not_apply_to_severity3(self):
        # 원 심각도3은 해소 경로에 들어가지 않고 즉시 PAGE (해소가 와도 발생 PAGE 보존)
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=3, is_clear=False)
        d = decide_notification(event, None, None, make_ctx(), cfg, self_heal=True)
        assert d.tier == TIER_PAGE


class TestIndependentClear:
    def test_independent_clear_suppress(self):
        cfg = make_config(importance_value_map=IMP_MAP, resolved_to_dashboard=False)
        event = make_event(severity=0)
        d = decide_notification(event, None, None, make_ctx(), cfg, self_heal=False)
        assert d.tier == TIER_SUPPRESS
        assert "독립 해소" in d.reason

    def test_independent_clear_to_dashboard_when_configured(self):
        cfg = make_config(importance_value_map=IMP_MAP, resolved_to_dashboard=True)
        event = make_event(severity=0)
        d = decide_notification(event, None, None, make_ctx(), cfg, self_heal=False)
        assert d.tier == TIER_DASHBOARD


class TestConservativeDefault:
    def test_none_noise_ctx_pages(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=1)
        d = decide_notification(event, None, None, None, cfg)
        assert d.tier == TIER_PAGE
        assert "수집 실패" in d.reason

    def test_source_unavailable_pages(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=2)
        d = decide_notification(event, None, None, make_ctx(source="unavailable"), cfg)
        assert d.tier == TIER_PAGE
        assert "수집 실패" in d.reason

    def test_collection_fail_clear_still_follows_clear_rule(self):
        # 수집 실패라도 해소 규칙 우선 — severity0 + self_heal → SUPPRESS (PAGE 강제 아님)
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=0)
        d = decide_notification(event, None, None, None, cfg, self_heal=True)
        assert d.tier == TIER_SUPPRESS

    def test_collection_fail_severity3_still_page_with_sev3_reason(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=3)
        d = decide_notification(event, None, None, None, cfg)
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason


class TestSignalsSchema:
    REQUIRED_KEYS = {
        "severity",
        "ai_severity",
        "effective_severity",
        "importance",
        "maintenance",
        "parent_avail_status",
        "pattern",
        "is_routine",
        "noti_policy",
        "flapping",
        "self_heal",
        "storm",
        "llm_actionability",
        # Plan 60 Wave A 일괄 확장(§10): E4 다홉 연쇄 + E2 상관 자리예약.
        "cascaded",
        "root_resource",
        "correlated",
    }

    def test_all_keys_present(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        analysis = SimpleNamespace(pattern_type="주기적", is_routine=True)
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="MID", noti_policy="notify")
        d = decide_notification(event, None, analysis, ctx, cfg)
        assert set(d.signals.keys()) == self.REQUIRED_KEYS
        assert d.signals["severity"] == 2
        assert d.signals["ai_severity"] is None
        assert d.signals["effective_severity"] == 2
        assert d.signals["importance"] == "보통"
        assert d.signals["parent_avail_status"] is None
        assert d.signals["pattern"] == "주기적"
        assert d.signals["is_routine"] is True
        assert d.signals["noti_policy"] == "notify"
        assert d.signals["flapping"] is False
        assert d.signals["storm"] is False

    def test_pattern_falls_back_to_history_stats(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        history = SimpleNamespace(pre_classification="급증")
        event = make_event(severity=2)
        d = decide_notification(event, history, None, make_ctx(), cfg)
        assert d.signals["pattern"] == "급증"
        assert d.signals["is_routine"] is None


class TestFingerprint:
    def test_fingerprint_stable_same_input(self):
        a = compute_fingerprint(make_event())
        b = compute_fingerprint(make_event())
        assert a == b and a != ""

    def test_fingerprint_changes_with_alarm_name(self):
        a = compute_fingerprint(make_event(alarm_name="CPU"))
        b = compute_fingerprint(make_event(alarm_name="MEM"))
        assert a != b

    def test_fingerprint_prefers_server_name_over_hostname(self):
        # server_name 동일·hostname 상이 → 핑거프린트 동일(서버명 우선)
        a = compute_fingerprint(make_event(server_name="srvA", hostname="h1"))
        b = compute_fingerprint(make_event(server_name="srvA", hostname="h2"))
        assert a == b

    def test_fingerprint_falls_back_to_hostname_when_no_server_name(self):
        a = compute_fingerprint(make_event(server_name="", hostname="h1"))
        b = compute_fingerprint(make_event(server_name="", hostname="h2"))
        assert a != b

    def test_decision_carries_fingerprint(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        d = decide_notification(make_event(severity=2), None, None, make_ctx(), cfg)
        assert d.fingerprint == compute_fingerprint(make_event(severity=2))


class TestMapImportance:
    def test_known_codes(self):
        assert map_importance("HIGH", IMP_MAP) == "높음"
        assert map_importance("MID", IMP_MAP) == "보통"
        assert map_importance("LOW", IMP_MAP) == "낮음"

    def test_none_is_conservative(self):
        assert map_importance(None, IMP_MAP) == "보통"

    def test_unmapped_is_conservative(self):
        assert map_importance("UNKNOWN", IMP_MAP) == "보통"
        assert map_importance(7, {}) == "보통"

    def test_numeric_code_coerced_to_str(self):
        assert map_importance(1, {"1": "높음"}) == "높음"


class TestNotiPolicyAnchor:
    """폴스타 알림정책 앵커(§6.2) — 1단계 승격/강등, 충돌 시 승격 우선."""

    def test_suppress_policy_demotes_one_step(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        # sev2/보통 = TICKET → suppress 앵커 → DASHBOARD
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="MID", noti_policy="suppress")
        d = decide_notification(event, None, None, ctx, cfg)
        assert d.tier == TIER_DASHBOARD

    def test_notify_policy_promotes_one_step(self):
        cfg = make_config(importance_value_map=IMP_MAP)
        # sev2/보통 = TICKET → notify 앵커 → PAGE
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="MID", noti_policy="notify")
        d = decide_notification(event, None, None, ctx, cfg)
        assert d.tier == TIER_PAGE

    def test_promote_wins_on_conflict(self):
        # notify(승격) + is_routine(강등) 충돌 → 승격 우선
        cfg = make_config(importance_value_map=IMP_MAP)
        analysis = SimpleNamespace(pattern_type="주기적", is_routine=True)
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="MID", noti_policy="notify")
        d = decide_notification(event, None, analysis, ctx, cfg)
        assert d.tier == TIER_PAGE

    def test_is_routine_demotes_within_cap(self):
        cfg = make_config(importance_value_map=IMP_MAP, suppress_max_severity=2)
        analysis = SimpleNamespace(pattern_type="주기적", is_routine=True)
        # sev2/보통 = TICKET → routine 강등 → DASHBOARD
        event = make_event(severity=2)
        d = decide_notification(event, None, analysis, make_ctx(importance_id="MID"), cfg)
        assert d.tier == TIER_DASHBOARD

    def test_adjustment_clamped_at_page(self):
        # 이미 PAGE(sev2/높음) + notify → PAGE 유지(상한 클램프)
        cfg = make_config(importance_value_map=IMP_MAP)
        event = make_event(severity=2)
        ctx = make_ctx(importance_id="HIGH", noti_policy="notify")
        d = decide_notification(event, None, None, ctx, cfg)
        assert d.tier == TIER_PAGE


def test_returns_notification_decision_instance():
    cfg = make_config(importance_value_map=IMP_MAP)
    d = decide_notification(make_event(severity=2), None, None, make_ctx(), cfg)
    assert isinstance(d, NotificationDecision)
    assert d.tier in {TIER_PAGE, TIER_TICKET, TIER_DASHBOARD, TIER_SUPPRESS}
    assert isinstance(d.priority, int)
    assert isinstance(d.reason, str) and d.reason
