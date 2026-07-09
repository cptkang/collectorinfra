"""Plan 52 Phase E2 — 플래핑 탐지(Nagios 알고리즘) 단위 테스트.

`src/alarm/domain/flapping.py`의 `flap_percent`/`update_flap_state`를 검증한다.
- 전이 없음/빈/단일 입력 → 0.0%
- 완전 교대 → 100.0%
- 혼합 시퀀스의 가중 % 손계산(최신 전이가 더 큰 영향)
- 히스테리시스(시작 20% / 종료 5%, 양방향)
- 21개 초과 입력 방어(뒤 21개 사용)
"""

import pytest

from src.alarm.domain.flapping import (
    DEFAULT_FLAP_HIGH,
    DEFAULT_FLAP_LOW,
    MAX_STATES,
    flap_percent,
    update_flap_state,
)


# ── flap_percent: 전이 없음 / 경계 입력 ─────────────────────────
def test_empty_list_returns_zero():
    assert flap_percent([]) == pytest.approx(0.0)


def test_single_state_returns_zero():
    assert flap_percent([True]) == pytest.approx(0.0)
    assert flap_percent([False]) == pytest.approx(0.0)


def test_all_same_states_returns_zero():
    """전부 동일 상태 → 전이 0개 → 0.0%."""
    assert flap_percent([True, True, True, True]) == pytest.approx(0.0)
    assert flap_percent([False] * 21) == pytest.approx(0.0)


# ── flap_percent: 완전 교대 ────────────────────────────────────
def test_full_alternation_two_states():
    """두 상태가 다름 → 단일 전이(가중 1.5) 전부 변화 → 100%."""
    assert flap_percent([True, False]) == pytest.approx(100.0)


def test_full_alternation_returns_100():
    """완전 교대(True,False,True,...) → 모든 전이 1 → 100.0%."""
    assert flap_percent([True, False, True, False]) == pytest.approx(100.0)
    assert flap_percent([False, True, False, True, False]) == pytest.approx(100.0)


def test_full_alternation_21_states():
    states = [bool(i % 2 == 0) for i in range(MAX_STATES)]  # 21개 완전 교대
    assert flap_percent(states) == pytest.approx(100.0)


# ── flap_percent: 혼합 시퀀스 가중 % 손계산 ────────────────────
def test_recent_transition_weighted_more():
    """[True, True, False]: 전이 [0, 1], 가중치 [1.0, 1.5].

    (1.0·0 + 1.5·1) / (1.0 + 1.5) × 100 = 1.5/2.5 × 100 = 60.0%.
    """
    assert flap_percent([True, True, False]) == pytest.approx(60.0)


def test_older_transition_weighted_less():
    """[False, True, True]: 전이 [1, 0], 가중치 [1.0, 1.5].

    (1.0·1 + 1.5·0) / (1.0 + 1.5) × 100 = 1.0/2.5 × 100 = 40.0%.
    동일한 1회 변화여도 오래된 전이라 최신 전이(60%)보다 영향이 작다.
    """
    assert flap_percent([False, True, True]) == pytest.approx(40.0)


def test_recent_vs_old_transition_asymmetry():
    """같은 1회 변화: 최신 전이(60%) > 오래된 전이(40%)."""
    recent = flap_percent([True, True, False])
    older = flap_percent([False, True, True])
    assert recent > older
    assert recent == pytest.approx(60.0)
    assert older == pytest.approx(40.0)


def test_mixed_four_states_handcalc():
    """[True, False, False, True]: 전이 [1, 0, 1], 가중치 [1.0, 1.25, 1.5].

    (1.0·1 + 1.25·0 + 1.5·1) / (1.0 + 1.25 + 1.5) × 100
      = 2.5 / 3.75 × 100 = 66.666...%.
    """
    assert flap_percent([True, False, False, True]) == pytest.approx(2.5 / 3.75 * 100.0)


# ── flap_percent: 21개 초과 방어 ───────────────────────────────
def test_truncates_to_last_21_calm_tail():
    """앞쪽은 요동치나 최신 21개가 전부 동일 → 0.0%(뒤 21개만 사용)."""
    noisy_head = [True, False, True, False]  # 포함되면 전이 발생
    calm_tail = [True] * MAX_STATES          # 최신 21개 동일
    assert flap_percent(noisy_head + calm_tail) == pytest.approx(0.0)


def test_truncates_to_last_21_flapping_tail():
    """앞쪽은 잠잠하나 최신 21개가 완전 교대 → 100.0%(뒤 21개만 사용)."""
    calm_head = [False] * 4
    flapping_tail = [bool(i % 2 == 0) for i in range(MAX_STATES)]
    assert flap_percent(calm_head + flapping_tail) == pytest.approx(100.0)


# ── update_flap_state: 히스테리시스(기본 임계) ─────────────────
def test_default_thresholds():
    assert DEFAULT_FLAP_HIGH == pytest.approx(20.0)
    assert DEFAULT_FLAP_LOW == pytest.approx(5.0)


def test_start_flapping_when_percent_at_or_above_high():
    """비플래핑 + percent >= high(20%) → True(시작)."""
    assert update_flap_state(False, 20.0) is True
    assert update_flap_state(False, 25.0) is True


def test_not_flapping_when_below_high():
    """비플래핑 + percent < high → False 유지."""
    assert update_flap_state(False, 19.999) is False
    assert update_flap_state(False, 10.0) is False


def test_stop_flapping_when_percent_at_or_below_low():
    """플래핑 + percent <= low(5%) → False(종료)."""
    assert update_flap_state(True, 5.0) is False
    assert update_flap_state(True, 0.0) is False


def test_stay_flapping_when_above_low():
    """플래핑 + percent > low → True 유지."""
    assert update_flap_state(True, 5.001) is True
    assert update_flap_state(True, 15.0) is True


def test_hysteresis_middle_band_keeps_prev_both_directions():
    """중간 대역(low < percent < high, 예 10%)은 prev를 양방향으로 유지."""
    mid = 10.0
    assert update_flap_state(False, mid) is False  # 비플래핑 유지
    assert update_flap_state(True, mid) is True     # 플래핑 유지


def test_custom_thresholds():
    """사용자 임계도 동일 규칙으로 동작."""
    assert update_flap_state(False, 30.0, high=30.0, low=10.0) is True
    assert update_flap_state(True, 10.0, high=30.0, low=10.0) is False
    assert update_flap_state(False, 20.0, high=30.0, low=10.0) is False  # 중간 → 유지


# ── 통합: flap_percent → update_flap_state ─────────────────────
def test_pipeline_alternating_starts_flapping():
    """완전 교대(100%)는 플래핑을 시작시킨다."""
    pct = flap_percent([True, False, True, False])
    assert update_flap_state(False, pct) is True


def test_pipeline_calm_stops_flapping():
    """잠잠(0%)하면 진행 중 플래핑을 종료시킨다."""
    pct = flap_percent([True, True, True, True])
    assert update_flap_state(True, pct) is False
