"""statsmodels 부재 시 STL 헬퍼 graceful 폴백 검증 (Plan 60 E3 2차 · D-113 · §5.2/§10).

**importorskip 없음** — 이 파일은 statsmodels 설치 여부와 무관하게 항상 실행되어, 미설치
환경에서 STL 헬퍼가 앱을 죽이지 않고 None + 경고 로그로 graceful 폴백함을 고정한다. statsmodels가
설치된 dev/CI에서도 sys.modules 주입으로 ImportError를 시뮬레이션해 동일 경로를 검증한다.
"""

from __future__ import annotations

import logging

from noise_gate.infrastructure.metric_stl import stl_anomaly_score

PERIOD = 24


def _sufficient_series() -> list[float]:
    """STL 길이 가드(max(min_periods,2)*period=72)를 통과하는 충분한 시계열(120점)."""
    return [20.0 + (i % 5 - 2) * 0.3 for i in range(5 * PERIOD)]


def test_statsmodels_missing_returns_none_and_warns(monkeypatch, caplog):
    # sys.modules에 None을 주입하면 `from statsmodels.tsa.seasonal import STL`이 ImportError.
    monkeypatch.setitem(__import__("sys").modules, "statsmodels.tsa.seasonal", None)
    with caplog.at_level(logging.WARNING):
        result = stl_anomaly_score(_sufficient_series(), PERIOD, min_periods=3)
    assert result is None  # 미설치 → graceful None(앱 미충돌)
    assert any("statsmodels 미설치" in rec.message for rec in caplog.records)


def test_missing_statsmodels_does_not_raise_on_short_series(monkeypatch):
    # 길이 가드가 import보다 먼저이므로 부족 시계열은 statsmodels 없이도 None(예외 없음).
    monkeypatch.setitem(__import__("sys").modules, "statsmodels.tsa.seasonal", None)
    assert stl_anomaly_score([1.0] * 10, PERIOD, min_periods=3) is None
