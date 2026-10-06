"""폴스타 시간 조건의 리터럴 경계 — `TimeResolution` → 통계·알람 SQL 리터럴.

plans/122 T-4~T-6 · D-309.

해석기(`src/domain/time_spec.py`)는 반개구간 `[start, end)`·입도만 정하고 테이블을 고르지 않는다
(테이블명·`stat_date` 형식은 DB 특화라 어댑터 몫 — D-089). 이 모듈이 그 투영의 단일 출처다 —
프롬프트 기간 블록(T-5)·생성 후 검증기(T-5b)·결정적 조립(알람 T-6 · 폼필·시맨틱 T-7)이
같은 값을 쓴다.

- 통계: 입도 → `cmm_metric_stat_{h,d,m}` · `stat_date` 문자열 형식
  `YYYYMMDDHH`·`YYYYMMDD`·`YYYYMM`(`config/db_profiles/polestar_cm_gp.yaml` 날짜·통계 테이블 절)
- 알람: `ctime` TIMESTAMP 리터럴 — PG·DB2 공통 ISO 형식
  (`assembler._month_range_to_ts_bounds`와 같다)

SQL에는 리터럴 경계만 들어간다(plans/122 §10.3 ⑤). 컬럼에 함수를 씌우지 않고 DB 현재시각 함수
(`CURRENT_DATE`·`NOW()`·`INTERVAL`)를 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from src.domain.query_time import QueryTime
from src.domain.time_spec import (
    SPAN_MAX_CHARS,
    Grain,
    TimeResolution,
    ceil_to_unit,
    display_span,
    floor_to_unit,
)

#: 입도 → 통계 테이블(스키마 접두 없음 — 호출부가 붙인다).
STAT_TABLES: dict[str, str] = {
    "hour": "cmm_metric_stat_h",
    "day": "cmm_metric_stat_d",
    "month": "cmm_metric_stat_m",
}
#: 입도 → `stat_date` 형식(strftime).
STAT_DATE_FORMATS: dict[str, str] = {"hour": "%Y%m%d%H", "day": "%Y%m%d", "month": "%Y%m"}
#: 알람 `ctime` TIMESTAMP 리터럴 형식.
TS_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class StatBounds:
    """통계 테이블 조건 — `[lo, hi)` 리터럴(`stat_date` 문자열 비교 · 같은 길이라 사전순 = 시간순).

    - `lo`가 None이면 왼쪽이 열린 구간(「~까지」)
    - `lo == hi`면 빈 구간(매월 1일의 「이번 달」 등 — 0행이 정답)
    - `last`는 끝을 **포함**하는 마지막 칸(BETWEEN·등호 표기용 · 빈 구간이면 None)
    """

    table: str
    grain: Grain
    lo: str | None
    hi: str
    last: str | None

    @property
    def is_empty(self) -> bool:
        return self.lo is not None and self.lo == self.hi

    def where(self, column: str = "s.stat_date") -> str:
        """반개구간 조건식 — `s.stat_date >= 'lo' AND s.stat_date < 'hi'`."""
        parts = [f"{column} >= '{self.lo}'"] if self.lo is not None else []
        parts.append(f"{column} < '{self.hi}'")
        return " AND ".join(parts)


def stat_bounds(res: TimeResolution) -> StatBounds | None:
    """해석 결과 → 통계 테이블·`stat_date` 리터럴 경계. 기간 조건 없음(unbounded)이면 None.

    경계는 입도 칸에 **바깥쪽**으로 맞춘다(시작 내림 · 끝 올림). 해석기가 입도를 경계 정렬로
    고르므로 보통은 그대로다.
    """
    if res.unbounded or res.end is None:
        return None
    grain = res.grain
    fmt = STAT_DATE_FORMATS[grain]
    start = floor_to_unit(res.start, grain) if res.start is not None else None
    end = ceil_to_unit(res.end, grain)
    if start is not None and end < start:
        end = start
    lo = start.strftime(fmt) if start is not None else None
    hi = end.strftime(fmt)
    last = None
    if start is None or start < end:
        prev = floor_to_unit(end - timedelta(microseconds=1), grain)
        last = prev.strftime(fmt)
    return StatBounds(table=STAT_TABLES[grain], grain=grain, lo=lo, hi=hi, last=last)


def alarm_ts_bounds(res: TimeResolution) -> tuple[str | None, str] | None:
    """해석 결과(사건 주체 권장 — `QueryTime.event`) → 알람 `ctime` TIMESTAMP 리터럴 `[시작, 끝)`.

    기간 조건 없음이면 None. 시작이 없으면(「~까지」) `(None, 끝)`.
    """
    if res.unbounded or res.end is None:
        return None
    start = res.start.strftime(TS_FORMAT) if res.start is not None else None
    return start, res.end.strftime(TS_FORMAT)


def sql_applies_period(sql: str, res: TimeResolution) -> bool:
    """SQL이 이 해석 결과의 리터럴 경계를 실제로 쓰는가(응답 `[조회 기간]` 고지 판정 — T-8).

    통계 경계(`lo`와 `hi` 또는 `last`) 또는 알람 경계(시작·끝 TIMESTAMP)가 따옴표 리터럴로 함께
    들어 있으면 참이다. 기간 조건 없음(unbounded)·빈 SQL이면 거짓.
    """
    if not sql:
        return False
    sb = stat_bounds(res)
    if sb is not None:
        head = f"'{sb.lo}'" in sql if sb.lo is not None else True
        tail = f"'{sb.hi}'" in sql or (sb.last is not None and f"'{sb.last}'" in sql)
        if head and tail and (sb.lo is not None or f"'{sb.hi}'" in sql):
            return True
    ab = alarm_ts_bounds(res)
    if ab is not None:
        start, end = ab
        if (start is None or f"'{start}'" in sql) and f"'{end}'" in sql:
            return True
    return False


# ──────────────────────────────────────────────
# 프롬프트 기간 블록(T-5) — 단일·멀티 LLM 경로 공용(D-066 단일 출처)
# ──────────────────────────────────────────────

PERIOD_BLOCK_HEADER = "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)"
_GRAIN_LABEL: dict[str, str] = {"hour": "시간 통계", "day": "일간 통계", "month": "월간 통계"}
_STAT_DATE_LABEL: dict[str, str] = {"hour": "YYYYMMDDHH", "day": "YYYYMMDD", "month": "YYYYMM"}
#: 모든 변형에 붙는 우선순위·재계산 금지 문장 — 프로필·템플릿의 「하드코딩 날짜 금지·
#: CURRENT_DATE 동적 계산」 일반 규칙과 경쟁하지 않게 한다(plans/122 §10.2 ⑤).
_PRIORITY_LINES = (
    "- 이 지시는 '하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**합니다"
    "(이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다).",
    "- CURRENT_DATE·CURRENT DATE·NOW()·CURRENT_TIMESTAMP·INTERVAL로 기간을 다시 계산하지 "
    "마세요(위 값 그대로 사용).",
)
_ALARM_NO_PERIOD = (
    "- 알람 이력·활성 알람을 조회하면 기간 조건을 넣지 마세요(최신순으로 조회)."
)
#: 블록에 싣는 질의 표현(span) 정규화·상한은 `time_spec.display_span`(응답 고지와 공용 · 리뷰 m-7).
_span_label = display_span


def _stat_line(sb: StatBounds, *, prefix: str) -> str:
    line = (
        f"{prefix} `{sb.table}`({_GRAIN_LABEL[sb.grain]}) 테이블을 쓰고 기간 조건 "
        f"`{sb.where()}`를 그대로 넣으세요(stat_date 형식 {_STAT_DATE_LABEL[sb.grain]})."
    )
    if sb.grain == "month" and sb.lo is not None and sb.last is not None:
        alt = (
            f"s.stat_date = '{sb.lo}'" if sb.lo == sb.last
            else f"s.stat_date BETWEEN '{sb.lo}' AND '{sb.last}'"
        )
        line += f" 같은 범위를 `{alt}`로 적어도 됩니다."
    return line


def _alarm_line(event: TimeResolution | None, *, present: bool = False) -> str:
    bounds = alarm_ts_bounds(event) if event is not None else None
    if bounds is None:
        return _ALARM_NO_PERIOD
    start, end = bounds
    cond = f"a.ctime < TIMESTAMP '{end}'"
    if start is not None:
        cond = f"a.ctime >= TIMESTAMP '{start}' AND {cond}"
    line = (
        f"- 알람 이력·활성 알람을 조회하면 발생 시각 조건 `{cond}`를 그대로 넣으세요"
        "(a는 알람 테이블 별칭 — 쿼리의 실제 별칭을 쓰세요)."
    )
    if present:
        # 검증기와 같은 규칙(리뷰 M-1): 「현재」 활성 알람 스냅샷은 발생 시각 기간과 뜻이 다르다
        line += (
            " 단, 「현재」 활성 알람(cmm_alarm_active) 스냅샷만 묻는 부분에는 기간 조건을 넣지"
            " 마세요."
        )
    return line


def build_period_block(qt: QueryTime | None) -> str:
    """요청 시간 해석(`QueryTime`) → LLM 프롬프트 기간 블록(plans/122 T-5 · D-309).

    단일 DB(`query_generator`)·멀티 DB(`multi_db_executor`) LLM 경로가 **같은 함수**를 부른다.
    리터럴은 `stat_bounds`·`alarm_ts_bounds`(검증기·결정적 조립과 같은 투영)에서만 나온다.
    입도·테이블은 해석기가 정했으므로 「_h/_d로 대체하지 마세요」 같은 테이블 금지 문장은
    쓰지 않는다(§10.2 ③ — 프로필 입도 규칙을 덮던 원인).

    빈 문자열인 경우:
        - `qt`가 없거나 되묻기 대상(`metric` None)
        - 기간 미지정인데 원문이 「현재·지금·실시간」(`present`) — 기본값(지난달)을 강제하지 않고
          종전 프로필 규칙(최근 1시간 통계)·실시간 API 경로에 맡긴다(D-291)
    """
    if qt is None:
        return ""
    # 적용할 해석 — 명시 기간이거나 기본값을 강제해도 될 때만(`QueryTime.metric_for_sql` 단일 판정)
    metric = qt.metric_for_sql
    if metric is None:
        return ""

    lines = [PERIOD_BLOCK_HEADER]
    if qt.uses_default_period:
        sb = stat_bounds(metric)
        lines.append(f"질의에 기간이 없습니다(기간 미지정 기본값 — 지난달 {metric.label()}).")
        if sb is not None:
            lines.append(_stat_line(sb, prefix="- 성능 통계를 조인할 때만 지난달로 한정하세요:"))
        lines.append(
            "- 통계·알람이 필요 없는 질의(구성·목록 등)에는 기간 조건을 넣지 마세요."
        )
        lines.append(_alarm_line(qt.event, present=qt.present))
    elif metric.unbounded:
        lines.append(f"조회 기간: {metric.label()}")
        lines.append(
            "- 성능 통계를 조회하면 기간 조건 없이 전 보관 기간을 검색하세요 — 보관 기간이 "
            f"가장 긴 `{STAT_TABLES['month']}`(월간 통계)를 사용합니다."
        )
        lines.append(_alarm_line(qt.event, present=qt.present))
    else:
        span = _span_label(metric.span)
        origin = f" (질의 표현 「{span}」 · 시스템 해석)" if span else ""
        lines.append(f"조회 기간: {metric.label()}{origin}")
        sb = stat_bounds(metric)
        if sb is not None:
            lines.append(_stat_line(sb, prefix="- 성능 통계를 조회하면"))
            if sb.is_empty:
                lines.append(
                    "- 완결된 구간이 아직 없어 0행이 정답입니다 — 위 조건을 그대로 넣고 다른 "
                    "기간으로 바꾸지 마세요."
                )
            elif metric.completeness == "to_date" and sb.grain == "day":
                # D-201 「이번 달」 — 진행 중 기간의 일간 통계 근사(종전 일간 블록 문구 승계)
                lines.append(
                    "- 진행 중인 기간이라 어제까지의 일간 통계로 집계합니다 — 서버별 GROUP BY로 "
                    "AVG(s.avg_val)=기간 평균, MAX(s.max_val)=기간 최대를 집계하세요."
                )
        lines.append(_alarm_line(qt.event, present=qt.present))
    lines.extend(_PRIORITY_LINES)
    return "\n".join(lines)


__all__ = [
    "PERIOD_BLOCK_HEADER", "SPAN_MAX_CHARS", "STAT_DATE_FORMATS", "STAT_TABLES", "TS_FORMAT",
    "StatBounds",
    "alarm_ts_bounds", "build_period_block", "sql_applies_period", "stat_bounds",
]
