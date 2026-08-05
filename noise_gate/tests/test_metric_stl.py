"""STL 분해 이상탐지 헬퍼 단위 테스트 (Plan 60 E3 2차 강화 · D-113 · §5.3/§11).

statsmodels 필요 — 파일 상단 importorskip으로 미설치 CI는 자동 스킵된다(statsmodels 부재
graceful 경로는 importorskip에 걸리지 않는 별도 파일 test_metric_stl_absence.py에서 검증).

검증:
    ① 합성 계절 시계열(희소 피크)의 정상 마지막 관측 → score 낮음(비이상)·계절 피크 비오탐
       (정적 z-score는 오탐하지만 robust STL은 계절 성분으로 흡수).
    ② 마지막점 큰 스파이크 → score 높음 → severity_from_anomaly 2/3.
    ③ 데이터 부족(<max(min_periods,2)*period) → None.
    ④ sigma 0(상수열) → None(부동소수 σ≈0도 임계로 차단).
"""

from __future__ import annotations

import statistics

import pytest

pytest.importorskip("statsmodels")

from noise_gate.domain.anomaly import severity_from_anomaly  # noqa: E402
from noise_gate.infrastructure.metric_stl import stl_anomaly_score  # noqa: E402

PERIOD = 24


def _seasonal_series(total_hours: int) -> list[float]:
    """일간 계절성 합성 시계열 — hour 3에만 90(야간 배치), 나머지 20 + 결정적 소음(±0.6).

    정적 z-score는 이 희소 피크(90)를 이상으로 오탐하지만 robust STL은 계절 성분으로 흡수한다.
    """
    pts: list[float] = []
    for i in range(total_hours):
        h = i % PERIOD
        val = 90.0 if h == 3 else 20.0
        val += (i % 5 - 2) * 0.3
        pts.append(val)
    return pts


def _series_ending_on_peak() -> list[float]:
    """마지막 관측이 정상 계절 피크(hour 3)가 되는 5일+4시간(124점) 시계열."""
    return _seasonal_series(5 * PERIOD + 4)  # 마지막 index 123, 123 % 24 == 3


class TestSeasonalPeakNotFalsePositive:
    def test_normal_seasonal_peak_low_score_vs_static(self):
        series = _series_ending_on_peak()
        z_stl = stl_anomaly_score(series, PERIOD, min_periods=3)
        assert z_stl is not None

        # 정적 z-score(전체 평균·표준편차)는 동일 피크를 이상으로 오탐(z>3).
        mean = statistics.fmean(series)
        sd = statistics.pstdev(series)
        z_static = (series[-1] - mean) / sd

        assert z_static > 3.0                     # 정적 z-score는 계절 피크 오탐
        assert abs(z_stl) < z_static              # STL 잔차는 훨씬 작음
        assert severity_from_anomaly(z_stl, 3.0) is None  # STL은 상향 없음(오탐 회피)


class TestSpikeFlagged:
    def test_true_spike_high_score_escalates(self):
        series = _series_ending_on_peak()
        series = series[:-1] + [150.0]  # 계절 예측(≈90)을 크게 초과하는 실제 스파이크
        z_stl = stl_anomaly_score(series, PERIOD, min_periods=3)
        assert z_stl is not None
        assert z_stl > 3.0
        assert severity_from_anomaly(z_stl, 3.0) in (2, 3)


class TestGuards:
    def test_insufficient_history_returns_none(self):
        # len < max(min_periods,2)*period(=72) → None(계산 skip).
        assert stl_anomaly_score(_seasonal_series(40), PERIOD, min_periods=3) is None

    def test_bad_period_returns_none(self):
        assert stl_anomaly_score(_seasonal_series(120), 0, min_periods=3) is None

    def test_constant_series_sigma_zero_returns_none(self):
        # 상수열 → STL 잔차 σ≈0(부동소수) → 임계로 차단 → None(0으로 나누기 방지).
        assert stl_anomaly_score([50.0] * (5 * PERIOD), PERIOD, min_periods=3) is None


class TestDeterminism:
    def test_same_input_same_score(self):
        series = _series_ending_on_peak()
        a = stl_anomaly_score(series, PERIOD, min_periods=3)
        b = stl_anomaly_score(series, PERIOD, min_periods=3)
        assert a == b
