"""기간·건수 해석 도구 (Plan 67 Phase S1 §4.2).

질의의 기간 표현("지난 3개월", "2026년 6월")과 건수 표현("상위 10", "100건")을 해석한다.
해석 로직 자체는 기존 결정적 함수를 그대로 재사용한다 — LLM이 도구를 호출해도 해석은
코드가 하므로 루프 진입 여부와 무관하게 같은 값이 나온다(비결정 유입 차단).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.domain.query_time import QueryTime
from src.utils.query_gen_common import resolve_query_limit, resolve_stat_month_range


def resolve_time_range(
    user_query: str,
    *,
    today: date | None = None,
    query_time: QueryTime | None = None,
) -> dict[str, Any]:
    """질의의 기간 표현을 통계 월 범위로 해석한다.

    `query_time`(요청 시간 해석 · plans/122 T-7 · D-309)이 있으면 그 성능 통계 해석을 돌려준다
    (도구 호출 문장을 다시 해석하지 않는다 — 요청 단위 단일 출처). 종전 키(`resolved`·`start`·
    `end` = YYYYMM 월 투영)는 형식 그대로 두고 키를 더한다:
    `time_range`(SMQ `time_range`에 그대로 쓰는 YYYYMM 목록 · 월로 표현되지 않는 기간이면 None) ·
    `grain`(hour·day·month) · `label`(사람이 읽는 기간) · `source`(rule·llm·default) ·
    `period_start`·`period_end`(반개구간 ISO 시각). 기간 미지정이면 `resolved=False`이고 기본값
    (지난달 · `source=default`)이 함께 실린다. 「현재·지금」 질의의 기본값처럼 기간을 강제하지
    않는 경우(`QueryTime.uses_default_period` 거짓)는 종전 해석으로 돌아간다.

    Args:
        user_query: 사용자 원문 질의
        today: 기준일(테스트 주입용, 기본은 오늘) — query_time이 있으면 쓰지 않는다
        query_time: 요청 시간 해석(None이면 종전 월 해석)

    Returns:
        {"resolved": bool, "start": "YYYYMM"|None, "end": "YYYYMM"|None}(+ query_time 경로 추가 키)
        — resolved=False면 질의에 기간 표현이 없다(종전: 전 기간 대상).
    """
    # 판정은 `QueryTime.metric_for_sql` 단일 출처(리뷰 m-2)
    res = query_time.metric_for_sql if query_time is not None else None
    if res is not None and query_time is not None:
        mr = res.month_range()
        return {
            "resolved": query_time.explicit,
            "start": mr[0] if mr else None,
            "end": mr[1] if mr else None,
            "time_range": ([mr[0]] if mr[0] == mr[1] else list(mr)) if mr else None,
            "grain": res.grain,
            "label": res.label(),
            "source": res.source,
            "period_start": res.start.isoformat() if res.start is not None else None,
            "period_end": res.end.isoformat() if res.end is not None else None,
        }
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
