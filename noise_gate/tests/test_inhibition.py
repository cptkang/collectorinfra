"""Plan 52 Phase E2 §3.4 step5 — 인히비션(상위 심각도 음소거) 통합 테스트.

A. 정책 단계(`decide_notification`): inhibition_enabled + inhibited 인자 → SUPPRESS,
   심각도3은 단락하여 PAGE, 플래그 off는 미억제(회귀).
B. 워커 탐지(`AlarmWorker._detect_inhibition`) 직접 단위:
   - 동일 스코프(db_id|server) 상위 심각도 활성 → 하위 inhibited=True.
   - 자기억제(동일 alarm_key) 금지 → False.
   - inhibition_window_seconds 만료 후 → False.
   - 동급/하위 심각도 인히비터·해소 이벤트 → False.
"""

from __future__ import annotations

from types import SimpleNamespace

from noise_gate.application.alarm_worker import AlarmWorker
from noise_gate.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)


# ─────────────────────────────────────────────────────────────
# A. 정책 단계 — decide_notification step5
# ─────────────────────────────────────────────────────────────
def make_event(severity: int = 2, *, is_clear=None) -> SimpleNamespace:
    if is_clear is None:
        is_clear = severity == 0
    return SimpleNamespace(
        severity=severity, is_clear=is_clear, db_id="db1", server_name="srv-1",
        alarm_name="CPU 임계", resource_name="r1", hostname="h1",
    )


def make_config(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2, importance_value_map={},
        resolved_to_dashboard=False, inhibition_enabled=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx() -> dict:
    return {
        "importance_id": None, "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }


class TestInhibitionPolicy:
    def test_inhibited_suppresses(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(inhibition_enabled=True), inhibited=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert "인히비션" in d.reason

    def test_not_inhibited_proceeds_to_matrix(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(inhibition_enabled=True), inhibited=False,
        )
        assert d.tier == TIER_TICKET
        assert "인히비션" not in d.reason

    def test_severity3_pages_despite_inhibited(self):
        d = decide_notification(
            make_event(3), None, None, make_ctx(),
            make_config(inhibition_enabled=True), inhibited=True,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_flag_off_no_suppression_regression(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(inhibition_enabled=False), inhibited=True,
        )
        assert d.tier == TIER_TICKET


# ─────────────────────────────────────────────────────────────
# B. 워커 탐지 — AlarmWorker._detect_inhibition (test_min_severity 패턴)
# ─────────────────────────────────────────────────────────────
def _inhibition_worker(window: int = 300) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(inhibition_window_seconds=window)
    )
    return AlarmWorker(cfg)


def _wev(severity: int, alarm_name: str, *, is_clear=False,
         db_id="db1", server="srv-1") -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=is_clear, db_id=db_id, server_name=server,
        alarm_name=alarm_name, resource_name="r1", hostname="h1",
    )


class TestDetectInhibition:
    def test_higher_severity_active_inhibits_lower(self):
        w = _inhibition_worker()
        # 상위(sev2) 발생 등록 → 자기 자신은 미억제
        assert w._detect_inhibition(_wev(2, "CRIT_CPU"), now=1000.0) is False
        # 하위(sev1) 다른 알람 → 상위 활성 중이므로 inhibited=True
        assert w._detect_inhibition(_wev(1, "MINOR_DISK"), now=1010.0) is True

    def test_self_inhibition_forbidden_same_alarm_key(self):
        w = _inhibition_worker()
        assert w._detect_inhibition(_wev(2, "CPU"), now=1000.0) is False
        # 동일 alarm_name(=alarm_key) → 자기억제 금지 → False
        assert w._detect_inhibition(_wev(1, "CPU"), now=1010.0) is False

    def test_window_expiry_no_inhibition(self):
        w = _inhibition_worker(window=300)
        assert w._detect_inhibition(_wev(2, "CRIT"), now=1000.0) is False
        # 301s 후 → 상위 기록 만료 → inhibited 아님
        assert w._detect_inhibition(_wev(1, "MINOR"), now=1301.0) is False

    def test_equal_severity_not_inhibited(self):
        w = _inhibition_worker()
        assert w._detect_inhibition(_wev(2, "A"), now=1000.0) is False
        # 동급(sev2)은 '상위'가 아니므로 억제 대상 아님(rec_sev > severity 엄격)
        assert w._detect_inhibition(_wev(2, "B"), now=1005.0) is False

    def test_lower_then_higher_not_inhibited(self):
        w = _inhibition_worker()
        # 하위 먼저 활성 → 이후 상위 도착 시 상위는 억제되지 않음
        assert w._detect_inhibition(_wev(1, "MINOR"), now=1000.0) is False
        assert w._detect_inhibition(_wev(2, "CRIT"), now=1005.0) is False

    def test_clear_event_not_inhibited(self):
        w = _inhibition_worker()
        w._detect_inhibition(_wev(2, "CRIT"), now=1000.0)
        # 해소 이벤트는 인히비션 탐지 비대상 → False
        assert w._detect_inhibition(
            _wev(0, "MINOR", is_clear=True), now=1010.0
        ) is False

    def test_different_scope_independent(self):
        w = _inhibition_worker()
        assert w._detect_inhibition(
            _wev(2, "CRIT", server="srv-A"), now=1000.0
        ) is False
        # 다른 서버(scope) → 상위 활성과 무관 → 억제 아님
        assert w._detect_inhibition(
            _wev(1, "MINOR", server="srv-B"), now=1010.0
        ) is False
