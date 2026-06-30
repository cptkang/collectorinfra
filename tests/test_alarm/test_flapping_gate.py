"""Plan 52 Phase E2 §3.7 step6 — 플래핑(상태 진동) 억제 통합 테스트.

A. 워커 탐지(`AlarmWorker._detect_flapping`) 직접 단위:
   - 교대(발생↔해소) 시퀀스 주입 → 임계 초과로 flapping=True.
   - 진동 직후 한 건 안정화로는 종료되지 않음(히스테리시스 유지).
   - 충분히 안정되면(연속 발생 다수) flapping 종료(False).
B. 정책 단계(`decide_notification` step6): flapping_enabled + flapping + 저심각도 → SUPPRESS,
   심각도3 → PAGE, suppress_max 미만 심각도는 미억제, 플래그 off는 미억제(회귀).

단위 임계는 도메인 순수함수(flapping.py)가 test_flapping.py에서 별도 검증되므로
여기서는 워커 시퀀스 통합 거동과 정책 단계를 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.alarm.application.alarm_worker import AlarmWorker
from src.alarm.domain.flapping import MAX_STATES
from src.alarm.domain.notification_policy import (
    TIER_PAGE,
    TIER_SUPPRESS,
    TIER_TICKET,
    decide_notification,
)


# ─────────────────────────────────────────────────────────────
# A. 워커 탐지 — AlarmWorker._detect_flapping
# ─────────────────────────────────────────────────────────────
def _flap_worker(high: float = 20.0, low: float = 5.0) -> AlarmWorker:
    cfg = SimpleNamespace(
        noise_gate=SimpleNamespace(flap_high_threshold=high, flap_low_threshold=low)
    )
    return AlarmWorker(cfg)


def _fev(is_clear: bool) -> SimpleNamespace:
    # _detect_flapping은 event.is_clear만 소비한다(firing=not is_clear).
    return SimpleNamespace(is_clear=is_clear)


class TestDetectFlapping:
    def test_alternation_triggers_flapping(self):
        w = _flap_worker()
        fp = "fp-flap"
        # 발생→해소 단일 교대만으로 100% → 시작 임계(20%) 초과 → True
        assert w._detect_flapping(fp, _fev(False), now=1.0) is False  # 첫 상태
        assert w._detect_flapping(fp, _fev(True), now=2.0) is True    # 전이 → 플래핑

    def test_longer_alternation_stays_flapping(self):
        w = _flap_worker()
        fp = "fp-flap2"
        last = False
        for i in range(8):
            last = w._detect_flapping(fp, _fev(i % 2 == 0), now=float(i))
        assert last is True

    def test_single_stable_keeps_flapping_hysteresis(self):
        # 진동으로 플래핑 진입 후 한 건만 안정화 → percent 여전히 높음 → 유지(True)
        w = _flap_worker()
        fp = "fp-hys"
        for i in range(6):
            w._detect_flapping(fp, _fev(i % 2 == 0), now=float(i))
        assert w._flap_flag[fp] is True
        # 안정 발생 1건 추가 → 즉시 종료되지 않음(히스테리시스)
        assert w._detect_flapping(fp, _fev(False), now=10.0) is True

    def test_sustained_stable_ends_flapping(self):
        # 플래핑 진입 후 연속 발생(안정)을 창 길이 이상 주입 → percent≈0 → 종료(False)
        w = _flap_worker()
        fp = "fp-end"
        for i in range(6):
            w._detect_flapping(fp, _fev(i % 2 == 0), now=float(i))
        assert w._flap_flag[fp] is True
        result = True
        for j in range(MAX_STATES + 2):  # deque(maxlen=21)를 전부 동일 상태로 채움
            result = w._detect_flapping(fp, _fev(False), now=100.0 + j)
        assert result is False


# ─────────────────────────────────────────────────────────────
# B. 정책 단계 — decide_notification step6
# ─────────────────────────────────────────────────────────────
def make_event(severity: int = 2) -> SimpleNamespace:
    return SimpleNamespace(
        severity=severity, is_clear=(severity == 0), db_id="db1",
        server_name="srv-1", alarm_name="CPU", resource_name="r1", hostname="h1",
    )


def make_config(**kwargs) -> SimpleNamespace:
    base = dict(
        suppress_max_severity=2, importance_value_map={},
        resolved_to_dashboard=False, flapping_enabled=False,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_ctx() -> dict:
    return {
        "importance_id": None, "maintenance": False, "noti_policy": None,
        "parent_avail_status": None, "source": "polestar_db",
    }


class TestFlappingPolicy:
    def test_flapping_low_severity_suppressed(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(flapping_enabled=True), flapping=True,
        )
        assert d.tier == TIER_SUPPRESS
        assert "플래핑" in d.reason
        assert d.signals["flapping"] is True

    def test_flapping_severity3_pages(self):
        d = decide_notification(
            make_event(3), None, None, make_ctx(),
            make_config(flapping_enabled=True), flapping=True,
        )
        assert d.tier == TIER_PAGE
        assert "심각도3" in d.reason

    def test_flapping_above_suppress_max_not_suppressed(self):
        # suppress_max_severity=1 → sev2는 (2 <= 1) 거짓 → 미억제(매트릭스)
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(flapping_enabled=True, suppress_max_severity=1),
            flapping=True,
        )
        assert d.tier != TIER_SUPPRESS
        assert "플래핑" not in d.reason

    def test_flag_off_no_suppression_regression(self):
        d = decide_notification(
            make_event(2), None, None, make_ctx(),
            make_config(flapping_enabled=False), flapping=True,
        )
        assert d.tier == TIER_TICKET
