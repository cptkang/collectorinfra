"""DB 무관 시간 해석 소비 헬퍼 — 범용(무선언) DB 기간 힌트 · 프롬프트 재료 정리.

plans/122 T-4·T-5 · D-306.

요청 시간 해석(`QueryTime` — state `time_resolution`)을 SQL 생성 경로가 소비할 때 DB 특화가 아닌
부분만 둔다. 특정 DB의 테이블·컬럼 리터럴은 여기 두지 않는다(D-089 — 폴스타 투영은
`src/db_adapters/polestar/time_period.py`). 단일 DB(`query_generator`)·멀티 DB(`multi_db_executor`)
경로가 같은 함수를 불러 두 경로의 프롬프트 재료가 갈라지지 않게 한다(D-066).

- `build_generic_time_hint`: 프로필 없는 DB(`GENERIC_LLM_MAPPING` 옵트인) 기간 힌트 — 종전
  `build_generic_period_hint`(월 YYYYMM만 알림)의 해석 on 대체. 반개구간·입도를 알리고 스키마에
  실제 있는 시간 컬럼에 적용하게 한다
- `strip_raw_time_keys`: 「파싱된 요구사항」 JSON 덤프에서 원시 기간 키(`time_range`·`time_expr`)를
  뺀다 — 해석 결과 블록과 원시 ISO 값이 동시에 실리던 이중 신호 제거(plans/122 §10.2 ⑦)
- `metric_period`: 결정적 경로(폼필 피벗·월 시리즈)에 넘길 성능 통계 해석 — 도메인 판정
  `QueryTime.metric_for_sql`의 None 안전 래퍼(「현재·지금」+기간 미지정은 None)
- `stat_month_compat`: 종전 `stat_month` 인자(YYYYMM 범위)를 받는 결정적 경로용 호환 값
"""

from __future__ import annotations

from typing import Any

from src.domain.query_time import QueryTime
from src.domain.time_spec import TimeResolution

#: 해석 결과가 있으면 「파싱된 요구사항」 덤프에서 빼는 원시 기간 키(input_parser 산출물).
RAW_TIME_KEYS: frozenset[str] = frozenset({"time_range", "time_expr"})

_GRAIN_LABEL: dict[str, str] = {"hour": "시간", "day": "일", "month": "월"}
_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def build_generic_time_hint(res: TimeResolution | None) -> str:
    """무선언 DB용 기간 힌트(특정 DB 스키마 리터럴 없음). 알릴 기간이 없으면 빈 문자열.

    기간 미지정 기본값(`source="default"` — 지난달)은 통계 테이블 규약을 가진 DB의 관행이라
    무선언 DB에는 강제하지 않는다(종전 힌트도 기간 표현이 있을 때만 실렸다). 기간 조건 없음
    (`unbounded`)도 빈 문자열이다.
    """
    if res is None or res.unbounded or res.end is None or res.source == "default":
        return ""
    end = f"'{res.end:{_TS_FORMAT}}'"
    if res.start is not None:
        interval = f"['{res.start:{_TS_FORMAT}}', {end})"
        cond = "`시간컬럼 >= 시작 AND 시간컬럼 < 끝`"
    else:
        interval = f"(시작 제한 없음, {end})"
        cond = "`시간컬럼 < 끝`"
    lines = [
        "## 기간 조건 해석 (참고)",
        f"질의의 기간은 {res.label()}로 해석되었습니다 — 반개구간 {interval} · "
        f"입도 {_GRAIN_LABEL[res.grain]}(KST).",
        f"- 대상 스키마에 **실제 존재하는** 시간/날짜 컬럼에 {cond} 조건으로 적용하세요"
        "(컬럼 형식에 맞게 리터럴 표기만 바꾸세요).",
        "- 스키마에 없는 테이블/컬럼을 지어내지 마세요(환각 금지).",
        "- CURRENT_DATE·NOW() 같은 현재시각 함수나 INTERVAL로 기간을 다시 계산하지 마세요"
        "(시스템이 계산한 값 그대로 사용).",
    ]
    if res.is_empty:
        lines.append("- 완결된 구간이 아직 없어 0행이 정답입니다.")
    return "\n".join(lines)


def strip_raw_time_keys(parsed_requirements: Any, query_time: QueryTime | None) -> Any:
    """「파싱된 요구사항」 덤프용 값 — 시간 해석이 있으면 원시 기간 키를 뺀 사본.

    해석이 없으면(플래그 off · 옛 체크포인트) 입력을 **그대로**(같은 객체) 돌려준다 — 덤프
    바이트가 종전과 같다.
    """
    if query_time is None or not isinstance(parsed_requirements, dict):
        return parsed_requirements
    return {k: v for k, v in parsed_requirements.items() if k not in RAW_TIME_KEYS}


def metric_period(query_time: QueryTime | None) -> TimeResolution | None:
    """결정적 경로가 쓸 성능 통계 해석 — `QueryTime.metric_for_sql`의 None 안전 래퍼.

    판정(명시 기간 또는 기본값을 강제해도 될 때만 · 「현재·지금」+기간 미지정은 None · D-291)은
    도메인 한 곳(`metric_for_sql`)에만 둔다(리뷰 m-2 — 판정 사본 금지). 알람(사건) 주체
    `QueryTime.event`는 대상이 아니다(기간 미지정 = 조건 없음이라 충돌하지 않는다).
    """
    return query_time.metric_for_sql if query_time is not None else None


def stat_month_compat(query_time: QueryTime | None) -> tuple[str, str] | None:
    """종전 `stat_month`(YYYYMM `(시작, 끝)` 끝 포함) 호환 값 — 결정적 경로의 옛 인자용.

    - 월 경계에 맞는 해석만 값이 있다(`TimeResolution.month_range` — 일·시 입도는 None)
    - `metric_period`가 None이면 None(「현재·지금」+기간 미지정 · 해석 없음 · 되묻기)
    """
    res = metric_period(query_time)
    return res.month_range() if res is not None else None


__all__ = [
    "RAW_TIME_KEYS", "build_generic_time_hint", "metric_period", "stat_month_compat",
    "strip_raw_time_keys",
]
