"""동적 baseline 이상탐지 — 순수 Python Holt-Winters (Plan 60 E3 · D-079).

메트릭 시계열의 계절성(일간 주기)을 additive 삼중 지수평활(Holt-Winters)로 모델링하고,
최신 관측값의 잔차 z-score로 baseline 이탈을 판정한다. 정적 z-score가 계절 피크(정상)를
이상으로 오탐하는 문제를 계절 항으로 흡수해 오탐을 줄인다(§5.1).

**B-3 확정(§5.2)**: 1차 구현은 **외부 패키지 불요·stdlib only**(math/statistics)로 additive
Holt-Winters를 직접 구현한다(statsmodels/STL 반입 금지). STL은 정확도 요구 확인 시 2차 강화.

이 모듈은 domain 계층이므로 **표준 라이브러리만** 의존한다(infra·config import 금지). 시계열
조회·적합 오케스트레이션·캐시는 인프라 어댑터(polestar_metric_baseline.py)가 담당하고,
여기서는 값만 소비하는 결정적 순수 함수만 제공한다(현행 flapping/correlation 패턴과 동일).

**벤더 중립화(Plan 67 R3-(v) 인접 · 편향 검토 §2-9)**: 알람 kind → (resource_type,
definition_name) 매핑표(구 `METRIC_SOURCE_BY_KIND`)는 특정 벤더(폴스타) 스키마 상수라
domain에서 제거하고 **어댑터 기본값 + 설정(`anomaly_metric_source_map_csv`) 주입**으로
옮겼다 — `noise_gate/infrastructure/polestar_metric_baseline.py` 참조. 이 모듈의 함수는
어떤 메트릭에서 왔는지 모르는 순수 수치 계산만 수행한다.

핵심 계약:
    - `severity_from_anomaly`는 **상향 전용**(z>hi → 상향 후보 심각도, 그 외 None). 게이트는
      analyzer 후처리가 `max()` 의미(후보>기존)로만 반영하므로 폴스타 심각도를 낮추지 않는다.
    - 히스토리 부족(<min_periods*period) → `holt_winters_fit` None(계산 skip → 상향 없음).
    - 결정성: 동일 입력 → 동일 출력(고정 평활 계수, 부동소수 재현).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# 고정 평활 계수(결정적) — level/trend/seasonal 각 지수평활 가중.
_ALPHA = 0.3   # level
_BETA = 0.05   # trend
_GAMMA = 0.3   # seasonal


@dataclass
class HWState:
    """Holt-Winters 적합 결과 상태(마지막 관측 이후의 level/trend/seasonal + 잔차).

    forecast_next()가 다음 스텝 예측값을 산출하며, anomaly_score가 이를 최신 관측과 비교한다.
    residuals는 적합 중 산출한 in-sample 1스텝-선행 잔차로, residual_sigma가 σ를 계산한다
    (캐시 시에는 σ만 저장하므로 residuals는 직렬화하지 않는다 — 어댑터 책임).
    """

    level: float
    trend: float
    seasonals: list[float]           # 길이 = period (계절 성분)
    next_idx: int                    # 다음 스텝의 계절 인덱스(= n % period)
    period: int
    residuals: list[float] = field(default_factory=list)

    def forecast_next(self) -> float:
        """다음 스텝(최신 관측 위치)의 1스텝-선행 예측값 = level + trend + 계절 성분."""
        return self.level + self.trend + self.seasonals[self.next_idx % self.period]


def _mean(values: list[float]) -> float:
    """빈 리스트 방어를 포함한 평균(빈 리스트는 0.0)."""
    return sum(values) / len(values) if values else 0.0


def holt_winters_fit(
    series: list[float],
    period: int,
    *,
    min_periods: int = 3,
    alpha: float = _ALPHA,
    beta: float = _BETA,
    gamma: float = _GAMMA,
) -> HWState | None:
    """additive 삼중 지수평활(Holt-Winters)로 시계열을 적합한다 (§5.2).

    계절 초기값 = 첫 주기의 편차, 추세 초기값 = 두 주기 평균차/period(2주기 미만이면 0),
    이후 t=period..n-1을 순회하며 1스텝-선행 예측→갱신을 반복해 잔차를 모은다.

    Args:
        series: 시간 오름차순 관측값(오래된→최신).
        period: 계절 주기 길이(시간별 데이터의 일간 주기 = 24).
        min_periods: 적합에 요구하는 최소 주기 수. len(series) < min_periods*period면 None.
        alpha/beta/gamma: level/trend/seasonal 지수평활 계수(결정적 고정 기본값).

    Returns:
        HWState(마지막 상태 + 잔차) 또는 히스토리 부족·period 비정상 시 None(계산 skip).
    """
    if period <= 0:
        return None
    n = len(series)
    if n < max(min_periods, 1) * period or n < 2 * period:
        # 계절+추세 초기화(2주기)와 최소 주기 요건을 모두 충족해야 적합(보수적 None).
        return None

    level = _mean(series[:period])
    trend = (_mean(series[period : 2 * period]) - _mean(series[:period])) / period
    seasonals = [series[i] - level for i in range(period)]
    residuals: list[float] = []

    for t in range(period, n):
        s_idx = t % period
        forecast = level + trend + seasonals[s_idx]
        residuals.append(series[t] - forecast)
        prev_level = level
        level = alpha * (series[t] - seasonals[s_idx]) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonals[s_idx] = gamma * (series[t] - level) + (1 - gamma) * seasonals[s_idx]

    return HWState(
        level=level,
        trend=trend,
        seasonals=seasonals,
        next_idx=n % period,
        period=period,
        residuals=residuals,
    )


def residual_sigma(series: list[float], state: HWState) -> float:
    """적합 잔차의 표준편차(σ)를 반환한다 (§5.2).

    σ는 적합 중 수집된 in-sample 1스텝-선행 잔차(state.residuals)의 모표준편차다. `series`는
    적합 대상 시계열로, 인터페이스 일관성·향후 검증용으로 받되 σ는 잔차에서 산출한다(재계산
    없이 결정적). 잔차가 2개 미만이면 0.0(분산 미정의 → 이상탐지 불가 신호).
    """
    resid = state.residuals
    if len(resid) < 2:
        return 0.0
    return statistics.pstdev(resid)


def anomaly_score(state: HWState, sigma: float, value: float) -> float:
    """최신 관측값의 잔차 z-score를 산출한다(결정적) (§5.2).

    z = (value - 다음스텝 예측) / σ. σ<=0(잔차 없음/상수 시계열)이면 이상탐지 불가로 0.0.
    양수 z = 관측값이 baseline+계절 예측을 초과(상향 후보), 음수 z = 하회(상향 대상 아님).
    """
    if sigma <= 0:
        return 0.0
    return (value - state.forecast_next()) / sigma


def severity_from_anomaly(score: float, z_high: float) -> int | None:
    """잔차 z-score를 상향 후보 심각도로 변환한다 — **상향 전용** (§5.2).

    z > z_high면 상향 후보를 반환한다(z > z_high*1.5 → 3=심각, 그 외 → 2=경고). 임계 이하·
    음수(하회)는 None(상향 없음). 반환 심각도는 analyzer 후처리가 `max()` 의미로만 반영하므로
    폴스타 심각도·기존 ai를 낮추지 않는다(SSOT 보존).
    """
    if score > z_high:
        return 3 if score > z_high * 1.5 else 2
    return None
