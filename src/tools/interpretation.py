"""기간·건수 해석 도구 (Plan 67 Phase S1 §4.2).

질의의 기간 표현("지난 3개월", "2026년 6월")과 건수 표현("상위 10", "100건")을 해석한다.
해석 로직 자체는 기존 결정적 함수를 그대로 재사용한다 — LLM이 도구를 호출해도 해석은
코드가 하므로 루프 진입 여부와 무관하게 같은 값이 나온다(비결정 유입 차단).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from src.utils.query_gen_common import resolve_query_limit, resolve_stat_month_range


def resolve_time_range(user_query: str, *, today: Optional[date] = None) -> dict:
    """질의의 기간 표현을 통계 월 범위로 해석한다.

    Args:
        user_query: 사용자 원문 질의
        today: 기준일(테스트 주입용, 기본은 오늘)

    Returns:
        {"resolved": bool, "start": "YYYYMM"|None, "end": "YYYYMM"|None}
        — resolved=False면 질의에 기간 표현이 없다(전 기간 대상).
    """
    resolved = resolve_stat_month_range(user_query, today)
    if resolved is None:
        return {"resolved": False, "start": None, "end": None}
    start, end = resolved
    return {"resolved": True, "start": start, "end": end}


def resolve_limit(user_query: str, *, default_limit: int = 100) -> int:
    """질의의 건수 표현을 반영한 행 제한 값을 반환한다.

    명시 건수("100건"/"상위 10")가 최우선이고, "전체/모든/모두"면 상향, 없으면 기본값이다.

    Args:
        user_query: 사용자 원문 질의
        default_limit: 명시 표현이 없을 때 쓸 기본 행 제한

    Returns:
        적용할 행 제한 값
    """
    return resolve_query_limit(user_query, default_limit)
