"""AI 메시지 심각도 보강 — 상향 전용(escalate-only) 불변식 단위 테스트 (Plan 52 E3).

decide_notification이 analysis.ai_message_severity를 `max(severity, ai)`로만 결합하여
**하향 경로가 절대 없음**(R-10)을 고정한다. 또한 보강 비활성 시 AI 값이 무시되고(E2 회귀 0),
심각도3은 어떤 AI 값에도 항상 PAGE임을 검증한다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.domain.notification_policy import (
    TIER_DASHBOARD,
    TIER_PAGE,
    TIER_SUPPRESS,
    decide_notification,
)

REF = datetime(2026, 6, 30, 10, 0, 0)
IMP_MAP = {"HIGH": "높음", "MID": "보통", "LOW": "낮음"}


def make_event(**kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent(메시지형 — condition_log 보유). severity로 is_clear 동기화."""
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="srv-log01",
        hostname="hlog01",
        ip_address="10.1.2.9",
        resource_ancestry="/Servers/srv/LogMonitor",
        alarm_id="LOG-001",
        severity=1,
        alarm_status="NOT_ACK",
        resource_type="server.LogMonitor",
        resource_name="syslog",
        alarm_name="로그 패턴 감지",
        alarm_time=REF,
        conditions="",
        condition_log="Out of memory: Killed process 1 (java)",
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_config(*, enable_ai_severity_boost=True, **kwargs) -> SimpleNamespace:
    """게이트 설정(보강 토글 포함)."""
    base = dict(
        suppress_max_severity=2,
        importance_value_map=IMP_MAP,
        resolved_to_dashboard=False,
        enable_ai_severity_boost=enable_ai_severity_boost,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx(*, importance_id="LOW", maintenance=False, noti_policy=None) -> dict:
    return {
        "importance_id": importance_id,
        "maintenance": maintenance,
        "noti_policy": noti_policy,
        "parent_avail_status": None,
        "source": "polestar_db",
    }


def make_analysis(ai_message_severity) -> SimpleNamespace:
    """alarm_analyzer가 채운 AlarmAnalysisResult를 모사한 덕타이핑 객체."""
    return SimpleNamespace(
        pattern_type="",
        is_routine=None,
        ai_message_severity=ai_message_severity,
    )


class TestEscalate:
    def test_ai_boost_severity1_to_3_pages(self):
        # 상향: severity1 + AI 3 → effective 3 → 심각도3 단락 PAGE
        cfg = make_config(enable_ai_severity_boost=True)
        d = decide_notification(
            make_event(severity=1), None, make_analysis(3), make_ctx(), cfg
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason
        assert d.signals["effective_severity"] == 3
        assert d.signals["ai_severity"] == 3

    def test_ai_boost_signal_reflects_applied_value(self):
        # signals['ai_severity']가 적용값을 반영하고, effective는 max로 결합
        cfg = make_config(enable_ai_severity_boost=True)
        d = decide_notification(
            make_event(severity=1), None, make_analysis(2), make_ctx(), cfg
        )
        assert d.signals["ai_severity"] == 2
        assert d.signals["effective_severity"] == 2  # max(1, 2)


class TestNoDowngrade:
    def test_ai_lower_than_severity_is_ignored_no_suppress(self):
        # 하향 불가: severity2 + AI 1 → effective 2 유지(AI 무시), 절대 SUPPRESS 안 됨
        cfg = make_config(enable_ai_severity_boost=True)
        d = decide_notification(
            make_event(severity=2, condition_log="benign log line"),
            None,
            make_analysis(1),
            make_ctx(importance_id="LOW"),
            cfg,
        )
        assert d.tier != TIER_SUPPRESS
        assert d.tier == TIER_DASHBOARD  # sev2×낮음 매트릭스 그대로
        assert d.signals["effective_severity"] == 2
        assert d.signals["ai_severity"] == 1  # 읽힌 값은 1이나 max로 강등 불가

    def test_severity3_with_low_ai_always_pages(self):
        # 심각도3 + AI 1(강등 시도) → max(3,1)=3 → 항상 PAGE
        cfg = make_config(enable_ai_severity_boost=True)
        d = decide_notification(
            make_event(severity=3), None, make_analysis(1), make_ctx(), cfg
        )
        assert d.tier == TIER_PAGE
        assert d.signals["effective_severity"] == 3


class TestBoostDisabled:
    def test_disabled_ignores_ai_severity(self):
        # 보강 비활성 → analysis에 ai_message_severity가 있어도 무시(E2 동작 동일)
        cfg_off = make_config(enable_ai_severity_boost=False)
        d_off = decide_notification(
            make_event(severity=2), None, make_analysis(3), make_ctx(importance_id="LOW"), cfg_off
        )
        # AI=3이 적용됐다면 PAGE가 됐을 테지만, 비활성이라 sev2×낮음=DASHBOARD 유지
        assert d_off.tier == TIER_DASHBOARD
        assert d_off.signals["ai_severity"] is None
        assert d_off.signals["effective_severity"] == 2

    def test_disabled_matches_no_analysis(self):
        # 보강 비활성 결과가 analysis 미주입 결과와 동일(회귀 0)
        cfg_off = make_config(enable_ai_severity_boost=False)
        ctx = make_ctx(importance_id="LOW")
        with_ai = decide_notification(
            make_event(severity=2), None, make_analysis(3), ctx, cfg_off
        )
        without = decide_notification(make_event(severity=2), None, None, ctx, cfg_off)
        assert with_ai.tier == without.tier
        assert with_ai.signals["effective_severity"] == without.signals["effective_severity"]
