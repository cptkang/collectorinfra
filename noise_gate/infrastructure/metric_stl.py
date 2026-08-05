"""STL 분해 기반 이상탐지 헬퍼 (Plan 60 E3 2차 강화 · D-113 — 인프라·statsmodels optional).

robust STL(Seasonal-Trend decomposition using Loess)로 시계열을 계절·추세·잔차로 분해하고,
최신 관측(series[-1])의 잔차 z-score(score)만 산출한다. robust=True가 이상점을 downweight하므로
계절 성분에 흡수되지 않은 실제 이상만 큰 잔차로 남아 계절 피크 오탐이 줄어든다(§5.3). 심각도
매핑은 호출 어댑터가 domain `severity_from_anomaly(score, z_high)`로 수행한다(상향 전용 계약 불변).

폐쇄망 주의(§5.2·§10):
    statsmodels는 운영 폐쇄망에 **미반입**(반입·보안 협의 미완)이므로 **필수 의존성이 아니다**.
    이 모듈은 statsmodels를 **함수 내부에서 lazy import**하며(모듈 최상단 import 없음 — 미설치
    환경에서 import만으로 앱이 죽지 않도록), 미설치·fit 실패·데이터 부족·잔차 σ 0이면 None을
    반환한다. 호출부(polestar_metric_baseline)는 None을 받으면 순수 Python Holt-Winters로 graceful
    폴백한다. 침묵 강등 금지 — 폴백 사유는 logger.warning/debug로 가시화한다.

계층: infrastructure. domain(anomaly.py)을 import하지 않고 **score(float)만** 반환한다 —
심각도 매핑(severity_from_anomaly)은 호출 어댑터의 책임이다(현행 flapping/correlation 패턴과 동일).
"""

from __future__ import annotations

import logging
import statistics
from typing import Optional

logger = logging.getLogger(__name__)

# 잔차 표준편차가 사실상 0인지 판정하는 임계. 순수 HW는 상수열에서 σ=0.0을 정확히 내지만
# STL(Loess)은 상수열에서도 부동소수 잔차(~1e-14 규모)를 남겨 σ가 정확히 0이 아니다. 실 신호의
# σ는 O(1) 이상이므로 이 임계로 상수/무변동열을 안전하게 걸러낸다(0으로 나누기·의미 없는 z 방지).
_SIGMA_EPS = 1e-9


def stl_anomaly_score(
    series: list[float],
    period: int,
    *,
    min_periods: int = 3,
) -> Optional[float]:
    """robust STL 분해 후 최신 관측(series[-1])의 잔차 z-score를 반환한다(없으면 None).

    표준 STL 이상탐지: robust=True가 이상점을 downweight하므로 계절 성분에 흡수되지 않은
    실제 이상만 큰 잔차로 남는다(계절 피크 오탐↓ — §5.3). statsmodels 미설치·fit 실패·
    데이터 부족·잔차 표준편차 0이면 None(호출부 HW 폴백). lazy import — 미설치 시 앱 무영향.

    Args:
        series: 시간 오름차순 관측값(오래된→최신). series[-1]이 판정 대상 최신 관측.
        period: 계절 주기 길이(시간별 데이터의 일간 주기 = 24). <=0이면 None.
        min_periods: 분해에 요구하는 최소 주기 수. len(series) < max(min_periods,2)*period면 None.

    Returns:
        최신 관측 잔차의 z-score(float) 또는 계산 불가 시 None(graceful → HW 폴백).
    """
    if period <= 0:
        return None
    if len(series) < max(min_periods, 2) * period:
        return None

    # lazy import — 모듈 최상단이 아닌 여기서 import해 미설치 환경에서도 앱이 죽지 않게 한다.
    try:
        from statsmodels.tsa.seasonal import STL
    except ImportError:
        logger.warning("statsmodels 미설치 — STL skip, HW 폴백")
        return None

    try:
        res = STL(series, period=period, robust=True).fit()
    except Exception as exc:  # noqa: BLE001 — fit 실패는 삼키지 않고 로그 후 HW 폴백
        logger.warning("STL fit 실패 — HW 폴백: %s", exc)
        return None

    resid = list(map(float, res.resid))
    sigma = statistics.pstdev(resid)  # HW residual_sigma와 동일한 모표준편차 관례
    if sigma <= _SIGMA_EPS:
        logger.debug("STL 잔차 σ≈0(무변동 시계열) — 이상탐지 불가, HW 폴백")
        return None
    return resid[-1] / sigma
