"""Plan 52 Phase E2 §3.6 step4 — 의존성(연쇄) 억제 통합 테스트.

`decide_notification`의 의존성 단계(parent_avail_status 기반)를 검증한다.
  - dependency_suppression=True + 부모 비정상(≠0) → SUPPRESS(의존성 연쇄 노이즈).
  - 부모 정상(0) / 미수집(None) → 보수적 미억제(매트릭스 진행, R-3).
  - 심각도3은 부모 비정상이어도 단락하여 PAGE.
  - dependency_suppression=False(기본) → 미억제(E1 회귀 0).

event/config/noise_ctx는 정책 모듈이 덕 타이핑으로 소비하므로 SimpleNamespace/dict로 구성한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)


def make_event(severity: int = 2, *, is_clear=None) -> SimpleNamespace:
    if is_clear is None:
        is_clear = severity == 0
    return SimpleNamespace(
        severity=severity,
        is_clear=is_clear,
        db_id="db1",
        server_name="srv-1",
        alarm_name="CPU 임계",
        resource_name="srv-1-CPU",
        hostname="h1",
    )


def make_config(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2,
        importance_value_map={},
        resolved_to_dashboard=False,
        dependency_suppression=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx(parent_avail_status, *, source="polestar_db") -> dict:
    # importance_id=None → '보통' → sev2 매트릭스 TICKET (미억제 케이스의 기준 티어)
    return {
        "importance_id": None,
        "maintenance": False,
        "noti_policy": None,
        "parent_avail_status": parent_avail_status,
        "source": source,
    }


class TestDependencySuppress:
    def test_parent_abnormal_suppresses(self):
        # 부모 AVAIL_STATUS=1(비정상) + 플래그 on + sev2 → SUPPRESS
        d = decide_notification(
            make_event(2), None, None, make_ctx(1),
            make_config(dependency_suppression=True),
        )
        assert d.tier == TIER_SUPPRESS
        assert "의존성" in d.reason
        assert d.signals["parent_avail_status"] == 1

    def test_parent_abnormal_negative_value_suppresses(self):
        # ≠0 은 모두 비정상 — 음수/2 등도 억제 대상
        for val in (2, -1, 99):
            d = decide_notification(
                make_event(2), None, None, make_ctx(val),
                make_config(dependency_suppression=True),
            )
            assert d.tier == TIER_SUPPRESS, f"parent={val} 억제 실패"

    def test_parent_normal_zero_not_suppressed(self):
        # 부모 정상(0) → 미억제, 매트릭스 진행(sev2 보통 → TICKET)
        d = decide_notification(
            make_event(2), None, None, make_ctx(0),
            make_config(dependency_suppression=True),
        )
        assert d.tier == TIER_TICKET
        assert "의존성" not in d.reason

    def test_parent_none_not_suppressed_conservative(self):
        # parent_avail_status=None(미수집/매핑없음/stale) → 보수적 미억제(매트릭스)
        d = decide_notification(
            make_event(2), None, None, make_ctx(None),
            make_config(dependency_suppression=True),
        )
        assert d.tier == TIER_TICKET

    def test_severity3_pages_despite_abnormal_parent(self):
        # 심각도3은 의존성 단계 이전에 단락 → 부모 비정상이어도 PAGE
        d = decide_notification(
            make_event(3), None, None, make_ctx(1),
            make_config(dependency_suppression=True),
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_flag_off_no_suppression_regression(self):
        # dependency_suppression=False(기본) → 부모 비정상이어도 미억제(E1 무변경)
        d = decide_notification(
            make_event(2), None, None, make_ctx(1),
            make_config(dependency_suppression=False),
        )
        assert d.tier == TIER_TICKET
        assert "의존성" not in d.reason

    def test_flag_off_matches_e1_baseline(self):
        # 플래그 off 시 parent 신호 유무와 무관하게 결과(tier/reason/priority) 동일
        cfg = make_config(dependency_suppression=False)
        ev = make_event(2)
        with_parent = decide_notification(ev, None, None, make_ctx(1), cfg)
        no_parent = decide_notification(ev, None, None, make_ctx(None), cfg)
        assert with_parent.tier == no_parent.tier
        assert with_parent.reason == no_parent.reason
        assert with_parent.priority == no_parent.priority
