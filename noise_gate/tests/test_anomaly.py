"""동적 baseline 이상탐지 domain 순수 함수 단위 테스트 (Plan 60 E3 · §11).

- holt_winters_fit / residual_sigma / anomaly_score / severity_from_anomaly 결정성.
- 계절 피크(정상)가 이상으로 오탐되지 않음(정적 z-score 대비 오탐 감소).
- 히스토리 부족 → None(계산 skip).
- severity_from_anomaly 상향 전용(음수 z·임계 이하 → None).
"""

from __future__ import annotations

import statistics

from noise_gate.domain.anomaly import (
    anomaly_score,
    holt_winters_fit,
    residual_sigma,
    severity_from_anomaly,
)
from noise_gate.infrastructure.polestar_metric_baseline import (
    DEFAULT_METRIC_SOURCE_BY_KIND,
    parse_metric_source_map,
)

PERIOD = 24


def _seasonal_series(total_hours: int) -> list[float]:
    """일간 계절성 합성 시계열 — 하루 중 hour 3에만 90(야간 배치), 나머지 20.

    작은 결정적 노이즈(±0.6)로 σ>0을 보장한다(순수 사인은 잔차 0 → z 산출 불가). 정적
    z-score는 이 hour-3 피크(90)를 이상으로 오탐하지만 HW는 계절 항으로 흡수한다.
    """
    pts: list[float] = []
    for i in range(total_hours):
        h = i % PERIOD
        val = 90.0 if h == 3 else 20.0
        val += (i % 5 - 2) * 0.3  # 결정적 소음 [-0.6, +0.6]
        pts.append(val)
    return pts


def _baseline_ending_before_hour3() -> list[float]:
    """다음 스텝(next_idx)이 hour 3(계절 피크)가 되도록 5일+3시간(123점) baseline을 만든다."""
    return _seasonal_series(5 * PERIOD + 3)  # 123 % 24 == 3


class TestHoltWintersFit:
    def test_insufficient_history_returns_none(self):
        # min_periods*period(=72) 미만이면 None(계산 skip).
        assert holt_winters_fit([20.0] * 40, PERIOD, min_periods=3) is None

    def test_sufficient_history_fits(self):
        state = holt_winters_fit(_seasonal_series(5 * PERIOD), PERIOD, min_periods=3)
        assert state is not None
        assert len(state.seasonals) == PERIOD
        assert state.period == PERIOD

    def test_deterministic_same_input_same_state(self):
        series = _seasonal_series(5 * PERIOD)
        a = holt_winters_fit(series, PERIOD, min_periods=3)
        b = holt_winters_fit(series, PERIOD, min_periods=3)
        assert a is not None and b is not None
        assert a.level == b.level
        assert a.trend == b.trend
        assert a.seasonals == b.seasonals
        assert a.next_idx == b.next_idx

    def test_bad_period_returns_none(self):
        assert holt_winters_fit([1.0] * 100, 0) is None


class TestSeasonalPeakNotFalsePositive:
    def test_seasonal_peak_not_flagged_vs_static_zscore(self):
        # 계절 피크(hour 3의 90)를 관측해도 HW는 이상이 아니라고 본다(오탐 없음).
        baseline = _baseline_ending_before_hour3()
        state = holt_winters_fit(baseline, PERIOD, min_periods=3)
        sigma = residual_sigma(baseline, state)
        observed_peak = 90.0  # 정상 계절 피크
        z_hw = anomaly_score(state, sigma, observed_peak)

        # 정적 z-score(전체 baseline 평균·표준편차)는 동일 피크를 오탐한다(z>3).
        mean = statistics.fmean(baseline)
        sd = statistics.pstdev(baseline)
        z_static = (observed_peak - mean) / sd

        assert z_static > 3.0  # 정적 z-score는 계절 피크를 이상으로 오탐
        assert abs(z_hw) < z_static  # HW는 훨씬 작은 잔차
        assert severity_from_anomaly(z_hw, 3.0) is None  # HW는 상향 없음(오탐 회피)

    def test_true_spike_is_flagged(self):
        baseline = _baseline_ending_before_hour3()
        state = holt_winters_fit(baseline, PERIOD, min_periods=3)
        sigma = residual_sigma(baseline, state)
        # 계절 예측(≈90)을 크게 초과하는 실제 스파이크 → 상향.
        z_spike = anomaly_score(state, sigma, 150.0)
        assert z_spike > 3.0
        assert severity_from_anomaly(z_spike, 3.0) == 3


class TestAnomalyScore:
    def test_zero_sigma_returns_zero(self):
        # 상수 시계열(잔차 0) → σ=0 → 이상탐지 불가로 0.0(0으로 나누기 방어).
        flat = [50.0] * (5 * PERIOD)
        state = holt_winters_fit(flat, PERIOD, min_periods=3)
        sigma = residual_sigma(flat, state)
        assert sigma == 0.0
        assert anomaly_score(state, sigma, 999.0) == 0.0


class TestSeverityFromAnomaly:
    def test_escalate_only_below_threshold_none(self):
        assert severity_from_anomaly(2.9, 3.0) is None
        assert severity_from_anomaly(3.0, 3.0) is None  # 경계(초과만)

    def test_negative_z_never_escalates(self):
        # 하회(음수 z)는 상향 대상 아님(상향 전용).
        assert severity_from_anomaly(-10.0, 3.0) is None

    def test_graduated_severity(self):
        assert severity_from_anomaly(3.5, 3.0) == 2         # z_high < z <= z_high*1.5
        assert severity_from_anomaly(5.0, 3.0) == 3         # z > z_high*1.5


class TestMetricSourceMap:
    """kind→메트릭 소스 매핑은 어댑터/설정 소관 (Plan 67 R3-(v) 인접 · 편향 검토 §2-9).

    domain(anomaly.py)은 벤더 스키마 매핑을 알지 않는다 — 상수는 어댑터 기본값으로 이동했고
    다른 스키마는 설정 CSV/생성자 주입으로 대체한다.
    """

    def test_domain_has_no_vendor_mapping(self):
        import noise_gate.domain.anomaly as anomaly_module

        assert not hasattr(anomaly_module, "METRIC_SOURCE_BY_KIND")

    def test_only_cpu_memory_mapped_by_default(self):
        # 1차 범위 = CPU·메모리만. disk/network/log는 매핑 부재(어댑터 화이트리스트에서 skip).
        assert DEFAULT_METRIC_SOURCE_BY_KIND == {
            "cpu": ("server.Cpus", "Utilization"),
            "memory": ("server.Memory", "Utilization"),
        }

    def test_csv_override_parsed(self):
        parsed = parse_metric_source_map(
            "cpu=host.CPU:Usage, memory = host.Mem : Usage "
        )
        assert parsed == {
            "cpu": ("host.CPU", "Usage"),
            "memory": ("host.Mem", "Usage"),
        }

    def test_blank_or_invalid_csv_falls_back_to_default(self):
        # 미설정·형식 위반은 None → 호출부가 기본 매핑 사용(설정 오타로 전면 비활성 방지).
        assert parse_metric_source_map("") is None
        assert parse_metric_source_map("   ") is None
        assert parse_metric_source_map("cpu-server.Cpus-Utilization") is None
        assert parse_metric_source_map("cpu=server.Cpus") is None
