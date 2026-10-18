"""폴스타 고수준 도구의 순수 헬퍼 — 방언 유틸 · 사건 구간 앵커 · 프로세스 마스킹 (plans/166 M5).

`polestar_tools.py`(고정 SQL 빌더 + 도구 등록)에서 빌더가 아닌 순수 함수 구간을 옮겨 왔다 — 본문은
그대로이며 원 모듈이 같은 이름으로 재노출한다. 값 보간 이스케이프 계약(§6)·방언 규칙은
`polestar_tools` 모듈 독스트링이 정본이다.

`build_*_sql` 빌더는 옮기지 않는다 — 읽기 전용 계약 테스트가 빌더의 정의 모듈을 `polestar_tools`로
고정해 새 빌더 누락을 잡는다(plans/163).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

# 스키마 한정자 (Known Mistakes 방언 규칙 — DB2는 대문자 POLESTAR).
_PG_SCHEMA = "polestar"
_DB2_SCHEMA = "POLESTAR"

# 프로세스 args 마스킹 패턴 (noise_gate/domain/process_rank.py 이식 — 값만 *** 치환).
_SENSITIVE_KEY = (
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|credential)"
)
_SENSITIVE_RE = re.compile(rf"({_SENSITIVE_KEY})(\s*[=:]\s*|\s+)(\S+)", re.IGNORECASE)
# scheme://user:<password>@host 형태의 비밀번호만 치환한다.
_CONN_STRING_RE = re.compile(r"(\b[a-zA-Z][\w+.-]*://[^\s:/@]+:)([^\s@]+)(@)")
_MASK = "***"


# =====================================================================
# 이스케이프 · 방언 유틸
# =====================================================================


def _sql_literal(value: str) -> str:
    """문자열을 안전한 SQL 리터럴로 변환한다(널바이트 제거 + 작은따옴표 이중화).

    MCP는 파라미터 바인딩이 없으므로 모든 외부 입력 문자열은 이 함수를 거쳐 보간한다
    (§6-2 이스케이프 계약 — 단위 테스트로 고정).

    백슬래시가 든 값은 거부한다 — MariaDB 소스도 PostgreSQL 분기 SQL을 받는데(`_resolve_engine`),
    MariaDB 기본 sql_mode에서는 백슬래시가 이스케이프 문자라 `\\'`로 리터럴을 탈출할 수 있다.
    PostgreSQL·DB2에서는 백슬래시가 일반 문자라 이중화하면 값이 바뀌므로, 엔진 공통으로 맞는
    표현이 없다.

    Raises:
        ValueError: 값에 백슬래시가 있을 때
    """
    text = str(value)
    if "\\" in text:
        raise ValueError("값에 백슬래시(\\)를 쓸 수 없습니다")
    cleaned = text.replace("\x00", "").replace("'", "''")
    return f"'{cleaned}'"


def _id_literal(value: Any) -> str:
    """식별자(alarm_id 등)를 안전하게 보간한다.

    ASCII 십진수만이면 바인딩 없이도 인젝션이 불가능하므로 정수 리터럴로, 그 외에는
    ``_sql_literal``로 이스케이프한다(엔진 무관 — DB2의 문자열↔숫자 캐스트 회피).
    """
    s = str(value).strip()
    if s.isascii() and s.isdecimal():
        return s
    return _sql_literal(s)


def _q(is_db2: bool, table: str) -> str:
    """엔진별 스키마로 한정된 테이블 참조를 반환한다."""
    schema = _DB2_SCHEMA if is_db2 else _PG_SCHEMA
    return f"{schema}.{table}"


def _row_limit(is_db2: bool, n: int) -> str:
    """엔진별 행 제한 절을 반환한다(DB2는 LIMIT 미지원 → FETCH FIRST)."""
    n = int(n)
    return f"FETCH FIRST {n} ROWS ONLY" if is_db2 else f"LIMIT {n}"


def _time_floor(is_db2: bool, hours: int) -> str:
    """엔진별 '지금으로부터 hours시간 전' 시각 표현을 반환한다."""
    h = int(hours)
    return f"CURRENT TIMESTAMP - {h} HOURS" if is_db2 else f"NOW() - INTERVAL '{h} hours'"



# ---------------------------------------------------------------------
# 사건 구간 앵커 (plans/50 G1·G2·G3 · SPEC-incident-window-tools · D-197)
#
# 조회 도구가 전부 now 앵커라 "어제 14시" 구간의 증거를 가져올 수 없었다(§3.4 "now() 금지").
# 기준시각·구간 인자를 받아 경계를 **파이썬이 계산해 리터럴로** 보간한다 — 엔진별 interval
# 산술을 SQL에 두지 않으며, 인자를 지정하지 않으면 종전 SQL과 문자열이 같다(회귀 0).
# ---------------------------------------------------------------------

# granularity → stat_date(varchar) 포맷·1단위 분
# (실측 db_profiles: h=YYYYMMDDHH · d=YYYYMMDD · m=YYYYMM).
_STAT_DATE_FMT: dict[str, str] = {"h": "%Y%m%d%H", "d": "%Y%m%d", "m": "%Y%m"}
_GRANULARITY_MINUTES: dict[str, int] = {"h": 60, "d": 1440, "m": 43200}
# lookback 상한(분) — 30일. 구간 조회는 사건 전후이지 장기 추세가 아니다.
_MAX_LOOKBACK_MINUTES = 60 * 24 * 30


def parse_reference_time(value: Any) -> datetime:
    """ISO 8601 기준시각을 파싱한다. 리터럴로 보간되므로 자유 문자열은 거부한다(ValueError).

    반환은 파싱된 그대로다(오프셋이 있으면 tz-aware). 폴스타 SQL 경로는 벽시계 값만 쓴다
    (`_wall_clock`), PromQL 경로는 epoch가 필요해 tz를 살린다.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"reference_time은 ISO 8601 문자열이어야 함: {value!r}")
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as e:
        raise ValueError(f"reference_time은 ISO 8601 문자열이어야 함: {value!r}") from e


def _wall_clock(dt: datetime) -> datetime:
    """오프셋을 떼고 벽시계 값만 남긴다(변환하지 않는다 — 폴스타 DB tz는 서버가 모른다)."""
    return dt.replace(tzinfo=None)


def _ts_literal(is_db2: bool, dt: datetime) -> str:
    """엔진별 timestamp 리터럴(초 해상도)."""
    s = dt.strftime("%Y-%m-%d %H:%M:%S")
    return f"TIMESTAMP('{s}')" if is_db2 else f"TIMESTAMP '{s}'"


def _shift_months(dt: datetime, months: int) -> datetime:
    """월 단위 이동(일자는 1일로 정규화 — YYYYMM 비교에만 쓴다)."""
    idx = dt.year * 12 + (dt.month - 1) - int(months)
    return dt.replace(year=idx // 12, month=idx % 12 + 1, day=1)


def _shift_periods(dt: datetime, granularity: str, periods: int) -> datetime:
    if granularity == "m":
        return _shift_months(dt, periods)
    return dt - timedelta(minutes=_GRANULARITY_MINUTES[granularity] * int(periods))


def _ctime_window_clause(is_db2: bool, reference_time: str, lookback_minutes: int) -> str:
    """`CA.CTIME`를 [기준시각 − lookback, 기준시각]으로 한정하는 WHERE 조각."""
    ref = _wall_clock(parse_reference_time(reference_time))
    lo = ref - timedelta(minutes=_clamp(lookback_minutes, 1, _MAX_LOOKBACK_MINUTES))
    return (
        f"  AND CA.CTIME >= {_ts_literal(is_db2, lo)}\n"
        f"  AND CA.CTIME <= {_ts_literal(is_db2, ref)}\n"
    )


def incident_window(
    reference_time: str,
    lookback_minutes: int | None,
    granularity: str,
    baseline_periods: int | None,
) -> dict[str, Any]:
    """metric_trend 앵커 구간 메타 — SQL 경계와 응답 `window`가 같은 값을 쓴다.

    - incident: [reference − lookback, reference]. lookback 미지정 시 granularity 1단위.
    - baseline(G2): incident 직전 `baseline_periods` 단위.
      소비자가 `stat_date_incident_from`으로 쪼갠다.
    """
    if granularity not in _STAT_DATE_FMT:
        raise ValueError(
            f"지원하지 않는 granularity: {granularity} (허용: {sorted(_STAT_DATE_FMT)})"
        )
    ref = _wall_clock(parse_reference_time(reference_time))
    minutes = _GRANULARITY_MINUTES[granularity] if lookback_minutes is None else lookback_minutes
    incident_from = ref - timedelta(minutes=_clamp(minutes, 1, _MAX_LOOKBACK_MINUTES))
    baseline_from = (
        _shift_periods(incident_from, granularity, baseline_periods)
        if baseline_periods else None
    )
    fmt = _STAT_DATE_FMT[granularity]
    lower = baseline_from or incident_from
    return {
        "reference_time": ref.isoformat(),
        "incident_from": incident_from.isoformat(),
        "baseline_from": baseline_from.isoformat() if baseline_from else None,
        "stat_date_to": ref.strftime(fmt),
        "stat_date_incident_from": incident_from.strftime(fmt),
        "stat_date_from": lower.strftime(fmt),
    }


def _clamp(value: int, lo: int, hi: int) -> int:
    """value를 [lo, hi] 범위로 제한한다."""
    return max(lo, min(int(value), hi))


# =====================================================================
# 프로세스 마스킹 · 랭킹 (순수 함수 — 단위 테스트 대상)
# =====================================================================


def _mask_process_args(args: str, max_len: int = 120) -> str:
    """프로세스 실행 인자에서 민감정보를 마스킹하고 길이를 제한한다.

    마스킹 대상(대소문자 무시, 키는 보존하고 값만 *** 치환):
        - password|passwd|pwd|secret|token|api_key|access_key|credential 의 값
        - DB 접속 문자열(scheme://user:<password>@host)의 비밀번호 부분
    """
    if not args:
        return ""
    masked = _CONN_STRING_RE.sub(rf"\1{_MASK}\3", args)
    masked = _SENSITIVE_RE.sub(rf"\1\2{_MASK}", masked)
    if len(masked) > max_len:
        masked = masked[:max_len].rstrip() + "…"
    return masked


def _to_float(value: Any) -> float:
    """값을 float로 변환한다(실패 시 0.0)."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _sort_key(item: dict[str, Any], sort: str) -> float:
    """정렬 지표를 반환한다(mem=pmem, cpu=p100cpu→pcpu 폴백)."""
    if sort == "mem":
        return _to_float(item.get("pmem"))
    if item.get("p100cpu") is not None:
        return _to_float(item.get("p100cpu"))
    return _to_float(item.get("pcpu"))


def _rank_processes(
    raw_list: list[dict[str, Any]], sort: str, top_n: int
) -> tuple[list[dict[str, Any]], int]:
    """원시 프로세스 목록을 지표 내림차순 상위 N으로 선별하고 args를 마스킹한다.

    Returns:
        (마스킹된 상위 N 프로세스 목록, 전체 프로세스 건수)
    """
    items = [it for it in (raw_list or []) if isinstance(it, dict)]
    total = len(items)
    ranked = sorted(items, key=lambda it: _sort_key(it, sort), reverse=True)
    top: list[dict[str, Any]] = []
    for it in ranked[: max(0, int(top_n))]:
        top.append(
            {
                "name": str(it.get("name", "") or ""),
                "pid": it.get("pid"),
                "ppid": it.get("ppid"),
                "user": str(it.get("user", "") or ""),
                "p100cpu": it.get("p100cpu"),
                "pcpu": it.get("pcpu"),
                "pmem": it.get("pmem"),
                "rss": it.get("rss"),
                "args": _mask_process_args(str(it.get("args", "") or "")),
            }
        )
    return top, total
