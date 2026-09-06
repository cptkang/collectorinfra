"""사건 기준시각·구간 (plans/50 A′-5 · SPEC-incident-scope · D-194).

조사에 시간 좌표계를 부여하는 최소 계약이다. push는 알람 시각(yyyyMMddHHmmss), pull은 호출자가
파싱한 ISO 8601을 넘기며, 잡은 `reference_time`(ISO) · `lookback_minutes`를 보유한다.
도구 창은 `[reference_time − lookback, reference_time]`이다(mcp_server `incident_window`와 동일 해석).

domain 계층 — 표준 라이브러리만 의존한다.
"""

from __future__ import annotations

from datetime import datetime

# 기본 사건 구간(분) — plans/50 §3.4 `default_lookback_minutes`.
DEFAULT_LOOKBACK_MINUTES = 120
# lookback 상한(분) — 30일. mcp_server `_MAX_LOOKBACK_MINUTES`와 같은 값(구간 조회 ≠ 장기 추세).
MAX_LOOKBACK_MINUTES = 60 * 24 * 30

_ALARM_TIME_FMT = "%Y%m%d%H%M%S"


def alarm_time_to_iso(value: object) -> str | None:
    """폴스타 원 이벤트 시각(yyyyMMddHHmmss)을 ISO 8601로 바꾼다. 형식이 다르면 None."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if len(s) != 14 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, _ALARM_TIME_FMT).isoformat()
    except ValueError:
        return None


def normalize_reference_time(value: object) -> str:
    """ISO 8601 문자열을 검증·정규화한다(초 해상도 ISO). 형식 오류는 ValueError."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("reference_time은 ISO 8601 문자열이어야 함")
    try:
        return datetime.fromisoformat(value.strip()).isoformat()
    except ValueError as e:
        raise ValueError(f"reference_time 형식 오류(ISO 8601): {value!r}") from e


def normalize_lookback(value: object) -> int:
    """lookback 분을 [1, MAX]로 정규화한다. None·비정수는 기본값."""
    try:
        minutes = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_MINUTES
    return max(1, min(minutes, MAX_LOOKBACK_MINUTES))


__all__ = [
    "DEFAULT_LOOKBACK_MINUTES",
    "MAX_LOOKBACK_MINUTES",
    "alarm_time_to_iso",
    "normalize_reference_time",
    "normalize_lookback",
]
