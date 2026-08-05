"""Plan 52 Phase E2 §3.8 step7 — 스톰(동일 서버 다발) 그룹핑 통합 테스트.

A. 워커 탐지(`AlarmWorker._detect_storm`) 직접 단위:
   - 동일 스코프(db_id|server) threshold 초과 발생 → 대표(처음 threshold개)는 False, 이후 True.
   - 해소(is_clear) 이벤트는 카운트 제외.
   - 다른 서버(scope)는 독립.
   - storm_window_seconds 밖 타임스탬프는 만료 제거.
B. 정책 단계(`decide_notification` step7): storm_grouping_enabled + storm → SUPPRESS,
   심각도3 → PAGE, 플래그 off는 미억제(회귀).
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
# A. 워커 탐지 — AlarmWorker._detect_storm
# ─────────────────────────────────────────────────────────────
def _storm_worker(threshold: int = 3, window: int = 60) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(
            storm_threshold=threshold, storm_window_seconds=window
        )
    )
    return AlarmWorker(cfg)


def _sev(is_clear: bool = False, server: str = "srv-1",
         db_id: str = "db1") -> SimpleNamespace:
    # _detect_storm은 is_clear/db_id/server_name만 소비한다.
    return SimpleNamespace(is_clear=is_clear, db_id=db_id, server_name=server)


class TestDetectStorm:
    def test_first_threshold_are_representatives(self):
        # threshold=3 → 1~3번째 발생은 storm=False(대표), 4번째부터 True(억제)
        w = _storm_worker(threshold=3)
        results = [w._detect_storm(_sev(), now=1000.0) for _ in range(5)]
        assert results == [False, False, False, True, True]

    def test_clear_events_excluded_from_count(self):
        w = _storm_worker(threshold=3)
        for _ in range(3):
            assert w._detect_storm(_sev(), now=1000.0) is False
        # 해소 이벤트는 카운트 제외 → False 이며 창 크기를 늘리지 않는다
        assert w._detect_storm(_sev(is_clear=True), now=1000.0) is False
        # 다음 발생이 4번째 발생(>threshold)이 되어 True — clear가 카운트됐다면 이미 4였을 것
        assert w._detect_storm(_sev(), now=1000.0) is True

    def test_different_server_independent(self):
        w = _storm_worker(threshold=3)
        for _ in range(5):
            w._detect_storm(_sev(server="srv-A"), now=1000.0)
        # 다른 서버는 독립 스코프 → 첫 발생은 대표(False)
        assert w._detect_storm(_sev(server="srv-B"), now=1000.0) is False

    def test_window_expiry_resets(self):
        w = _storm_worker(threshold=3, window=60)
        results = [w._detect_storm(_sev(), now=1000.0) for _ in range(4)]
        assert results[-1] is True  # 4번째 → 스톰
        # 창(60s) 밖 → 이전 타임스탬프 만료 → 단독 발생은 대표(False)
        assert w._detect_storm(_sev(), now=1000.0 + 61) is False


# ─────────────────────────────────────────────────────────────
# B. 정책 단계 — decide_notification step7
# ─────────────────────────────────────────────────────────────
def make_event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def make_config(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2, importance_value_map={},
        resolved_to_dashboard=False, storm_grouping_enabled=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx() -> dict:
    return {
        "importance_id": None, "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }


class TestStormPolicy:
    def test_storm_suppressed(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(storm_grouping_enabled=True), storm=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert "스톰" in d.reason
        assert d.signals["storm"] is True

    def test_storm_severity3_pages(self):
        d = decide_notification(
            make_event(3), None, None, make_ctx(),
            make_config(storm_grouping_enabled=True), storm=True,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_flag_off_no_suppression_regression(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(storm_grouping_enabled=False), storm=True,
        )
        assert d.tier == TIER_TICKET
