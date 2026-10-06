"""폴스타 전용 SQL 검증 (Plan 63 P2, D-089).

query_validator.py의 `_check_routing_filter_misuse`를 분리 이동한 것(동작 불변). 공용
validator는 어댑터 `validator_checks()` 훅을 순회 실행하며, 담당 DB(폴스타)에서만 발동한다
(기존엔 전 DB에서 실행됐으나 폴스타 토큰 부재 시 무동작이라 동작 불변 — Plan 63 §1.1 L4).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import sqlparse

from src.db_adapters.polestar.time_period import (
    STAT_DATE_FORMATS,
    STAT_TABLES,
    TS_FORMAT,
    StatBounds,
    alarm_ts_bounds,
    stat_bounds,
)
from src.domain.query_time import QueryTime
from src.domain.time_spec import (
    KST,
    NOTE_EVENT_TO_NOW,
    NOTE_MULTIPLE_PERIODS,
    TimeResolution,
    shift_unit,
)
from src.sql_time_conditions import (
    REASON_COLUMN,
    REASON_OR,
    ColumnCondition,
    extract_column_conditions,
    find_now_functions,
    half_open_bounds,
    table_qualifiers,
)

logger = logging.getLogger(__name__)


def check_routing_filter_misuse(sql: str) -> list[str]:
    """라우팅 정보를 WHERE 조건에 사용한 패턴을 탐지한다.

    GROUP_PATH는 CMM_RESOURCE의 내부 계층 경로로, Polestar/위치 식별에 사용하면
    항상 0건 조회 또는 SQL 에러가 발생한다.
    Polestar 이름·위치명은 DB 라우팅 단계에서 이미 처리되므로 SQL에 포함되어선 안 된다.

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    errors: list[str] = []

    # GROUP_PATH를 WHERE/AND/OR 조건에 사용한 패턴 (SELECT alias이므로 WHERE에서 사용 불가)
    if re.search(r"\bGROUP_PATH\s*(?:I?LIKE|=|!=|<>)", sql, re.IGNORECASE):
        errors.append(
            "GROUP_PATH은 SELECT 절의 계산된 별칭(alias)으로 WHERE 조건에서 사용할 수 없습니다. "
            "Polestar/위치 식별 정보는 DB 라우팅 단계에서 이미 처리되었습니다."
        )

    # Polestar 이름을 LIKE/ILIKE 필터로 사용하는 패턴 탐지
    routing_columns = [
        r"RESOURCE_NAME",
        r"CR\.NAME",
        r"A\.RESOURCE_NAME",
        r"AR\.RESOURCE_NAME",
    ]
    polestar_keywords = ["폴스타", "polestar"]
    for col_pat in routing_columns:
        for keyword in polestar_keywords:
            if re.search(
                rf"\b{col_pat}\s+I?LIKE\s+'%{keyword}%'",
                sql,
                re.IGNORECASE,
            ):
                errors.append(
                    f"라우팅 식별자 '{keyword}'를 WHERE 필터로 사용하면 0건 조회됩니다. "
                    "Polestar 이름은 DB 라우팅 단계에서 처리되므로 SQL 조건에 포함하지 마세요."
                )

    return errors


# 다중 resource_type을 한 alias로 접는 피벗 패턴: alias.resource_type IN ('a','b',...)
_RESOURCE_TYPE_MULTI_RE = re.compile(
    r"\b(\w+)\.resource_type\s+IN\s*\(([^()]*)\)", re.IGNORECASE
)
# cmm_resource 테이블의 alias 수집: FROM/JOIN [schema.]cmm_resource [AS] alias
_CMM_RESOURCE_ALIAS_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+[\w.]*\bcmm_resource\s+(?:AS\s+)?(\w+)\b", re.IGNORECASE
)
# HAVING 절 구간(다음 절 키워드 전까지)
_HAVING_SEGMENT_RE = re.compile(
    r"\bHAVING\b(.*?)(?=\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b|\bUNION\b|$)",
    re.IGNORECASE | re.DOTALL,
)
# 성능 통계 테이블 조인의 alias: JOIN [schema.]cmm_metric_stat_m|_d|… [AS] alias
_METRIC_TABLE_ALIAS_RE = re.compile(
    r"\bJOIN\s+[\w.]*\bcmm_metric_stat\w*\s+(?:AS\s+)?(\w+)\b", re.IGNORECASE
)
# 성능 통계 조인 + 조인 종류 캡처 (LEFT 여부 판정용)
_METRIC_TABLE_JOIN_TYPE_RE = re.compile(
    r"\b(LEFT(?:\s+OUTER)?\s+|RIGHT(?:\s+OUTER)?\s+|FULL(?:\s+OUTER)?\s+|INNER\s+)?"
    r"JOIN\s+[\w.]*\bcmm_metric_stat\w*\s+(?:AS\s+)?(\w+)\b",
    re.IGNORECASE,
)
# WHERE 절 구간(다음 절 키워드 전까지 — HAVING의 정상 집계 필터는 구간 밖)
_WHERE_SEGMENT_RE = re.compile(
    r"\bWHERE\b(.*?)(?=\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bUNION\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def check_scoped_pivot_missing_server_identity(sql: str) -> list[str]:
    """특정 서버로 스코프된 조회의 SELECT에 서버 식별 컬럼이 없는 패턴을 탐지한다 (D-097).

    선행 결과 스코프(D-086/D-095)가 적용된 피벗 조회는 HAVING에 서버 식별 필터
    (`HAVING MAX(CASE WHEN … THEN name/hostname END) IN (...)`)를 갖는다. 이때 SELECT에
    서버 식별 컬럼이 없으면 결과 행이 **어느 서버의 값인지 알 수 없다**(2026-07-20 라이브
    실측 — 제조사/일련번호만 반환되어 서버명과 같은 행에 나오지 않는 오답 형태). HAVING
    스코프가 없는 전체 조회(폼필 피벗 조립기 등)는 검사하지 않는다(오검출 방지).
    주석 제거(sqlparse) 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)

    aliases = set(_CMM_RESOURCE_ALIAS_RE.findall(text))
    if not aliases:
        return []

    # HAVING 절에 cmm_resource alias의 서버 식별(name/hostname) 스코프 필터가 있는가
    scoped_alias: str | None = None
    for seg_m in _HAVING_SEGMENT_RE.finditer(text):
        segment = seg_m.group(1)
        if not re.search(r"\bIN\s*\(|=\s*'", segment, re.IGNORECASE):
            continue
        for alias in aliases:
            a = re.escape(alias)
            if re.search(rf"\b{a}\.(?:name|hostname)\b", segment, re.IGNORECASE):
                scoped_alias = alias
                break
        if scoped_alias:
            break
    if not scoped_alias:
        return []

    # SELECT 목록(본문 FROM 이전 구간)에 어떤 cmm_resource alias든 식별 컬럼이 있으면 정상
    from_m = _CMM_RESOURCE_ALIAS_RE.search(text)
    select_segment = text[: from_m.start()] if from_m else text
    for alias in aliases:
        a = re.escape(alias)
        if re.search(rf"\b{a}\.(?:name|hostname)\b", select_segment, re.IGNORECASE):
            return []

    return [
        "특정 서버로 스코프된 조회(HAVING의 서버 식별 필터)인데 SELECT에 서버 식별 컬럼이 "
        "없습니다. 결과 행이 어느 서버의 값인지 알 수 없으므로 서버 식별 컬럼을 함께 "
        "조회하세요. 예(GROUP BY 피벗): "
        f"MAX(CASE WHEN {scoped_alias}.resource_type = 'server.Server' "
        f"THEN COALESCE({scoped_alias}.name, {scoped_alias}.hostname) END) AS server_name"
    ]


def check_metric_join_on_server_entity(sql: str) -> list[str]:
    """성능 통계를 server.Server 고정 alias의 id에 조인한 패턴을 탐지한다 (D-098).

    cmm_metric_stat_*(성능 통계)는 자식 리소스(server.Cpus/Memory/Disks/FileSystems)에만
    붙는다(실측 — server.Server 행에는 통계가 없음). resource_type이 'server.Server'로만
    고정된 alias의 id에 통계를 조인하면 값이 **항상 NULL**이 되고, `ORDER BY <집계> DESC
    LIMIT 1` 패턴에서는 PostgreSQL의 DESC 기본 NULLS FIRST 때문에 **임의 서버가 1위로
    선택되는 침묵 오답**이 된다(2026-07-20 라이브 실측). 주석 제거 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록 (위반 조인당 1건)
    """
    text = sqlparse.format(sql, strip_comments=True)

    # 제약 판정 구간: FROM ~ (GROUP BY/HAVING/ORDER BY/…) — 조인 ON·WHERE의 실제 제약만
    # 본다. SELECT/HAVING의 CASE WHEN resource_type 비교(조건식 사용)는 조인 제약이 아니다.
    from_m = re.search(r"\bFROM\b", text, re.IGNORECASE)
    if not from_m:
        return []
    stop_m = re.search(
        r"\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b",
        text[from_m.start():], re.IGNORECASE,
    )
    region = (
        text[from_m.start(): from_m.start() + stop_m.start()]
        if stop_m else text[from_m.start():]
    )

    errors: list[str] = []
    for m in _METRIC_TABLE_ALIAS_RE.finditer(region):
        metric_alias = m.group(1)
        ma = re.escape(metric_alias)
        # 통계 조인의 상대 alias: X.id = s.resource_id (양방향)
        bound: set[str] = set()
        for bm in re.finditer(
            rf"\b(\w+)\.id\s*=\s*{ma}\.resource_id\b|\b{ma}\.resource_id\s*=\s*(\w+)\.id\b",
            region, re.IGNORECASE,
        ):
            bound.add((bm.group(1) or bm.group(2)))
        for alias in bound:
            a = re.escape(alias)
            # alias의 resource_type 제약 리터럴 수집 (=/IN, 조인·WHERE 구간 한정)
            types: set[str] = set()
            for tm in re.finditer(
                rf"\b{a}\.resource_type\s*(?:=\s*('[^']*')|IN\s*\(([^()]*)\))",
                region, re.IGNORECASE,
            ):
                literals = tm.group(1) or tm.group(2) or ""
                types.update(v.strip("'") for v in re.findall(r"'[^']*'", literals))
            if types == {"server.Server"}:
                errors.append(
                    f"성능 통계({metric_alias})를 'server.Server'로 고정된 alias "
                    f"'{alias}'의 id에 조인했습니다. 통계는 자식 리소스"
                    "(server.Cpus/Memory/Disks/FileSystems)에만 붙으므로 이 조인은 항상 "
                    "NULL이 되어 정렬·집계가 오답이 됩니다. 자식 리소스 alias의 id에 "
                    "조인하세요 (예: cpu.id = "
                    f"{metric_alias}.resource_id + cpu.resource_type = 'server.Cpus', "
                    "또는 단일 alias 피벗이면 c.resource_type IN "
                    f"('server.Server','server.Cpus') + c.id = {metric_alias}.resource_id)."
                )
    return errors


def check_pivot_metric_inner_join(sql: str) -> list[str]:
    """다중 타입 피벗 alias에 성능 통계를 INNER JOIN한 패턴을 탐지한다 (D-098).

    한 alias로 server.Server + 자식 리소스를 접는 피벗에서 성능 통계를 INNER(비-LEFT)
    JOIN하면, 통계가 없는 server.Server 행이 그룹에서 탈락해 서버 속성 CASE WHEN과
    HAVING의 서버 식별 필터가 전부 NULL → **침묵 0건**이 된다(2026-07-20 라이브 실측).
    이 피벗에서 통계 조인은 반드시 LEFT JOIN + 조건 전부 ON 절이어야 한다(WHERE 조건은
    기존 LEFT JOIN 강등 검출이 담당). 주석 제거 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)

    from_m = re.search(r"\bFROM\b", text, re.IGNORECASE)
    if not from_m:
        return []
    stop_m = re.search(
        r"\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b",
        text[from_m.start():], re.IGNORECASE,
    )
    region = (
        text[from_m.start(): from_m.start() + stop_m.start()]
        if stop_m else text[from_m.start():]
    )

    errors: list[str] = []
    for m in _METRIC_TABLE_JOIN_TYPE_RE.finditer(region):
        join_type = (m.group(1) or "").strip().upper()
        if join_type.startswith("LEFT"):
            continue  # LEFT JOIN은 정상 (WHERE 강등은 별도 검출)
        metric_alias = m.group(2)
        ma = re.escape(metric_alias)
        bound: set[str] = set()
        for bm in re.finditer(
            rf"\b(\w+)\.id\s*=\s*{ma}\.resource_id\b|\b{ma}\.resource_id\s*=\s*(\w+)\.id\b",
            region, re.IGNORECASE,
        ):
            bound.add((bm.group(1) or bm.group(2)))
        for alias in bound:
            a = re.escape(alias)
            types: set[str] = set()
            for tm in re.finditer(
                rf"\b{a}\.resource_type\s*(?:=\s*('[^']*')|IN\s*\(([^()]*)\))",
                region, re.IGNORECASE,
            ):
                literals = tm.group(1) or tm.group(2) or ""
                types.update(v.strip("'") for v in re.findall(r"'[^']*'", literals))
            if len(types) >= 2 and "server.Server" in types:
                errors.append(
                    f"다중 resource_type 피벗 alias '{alias}'에 성능 통계({metric_alias})를 "
                    "INNER JOIN했습니다. 통계가 없는 server.Server 행이 그룹에서 탈락해 "
                    "서버 속성·서버 식별 HAVING이 전부 NULL(0건)이 됩니다. LEFT JOIN으로 "
                    "바꾸고 통계 조건(definition_name, stat_date 등)은 전부 그 LEFT JOIN의 "
                    "ON 절에 두세요."
                )
    return errors


# 집계 CASE WHEN 안의 alias resource_type 비교: AGG(CASE WHEN x.resource_type = 'T' …
_CASE_RT_RE = re.compile(
    r"\b(\w+)\.resource_type\s*=\s*'([^']+)'", re.IGNORECASE
)
# 순위 정렬: ORDER BY <식> DESC/ASC (NULLS 지정 여부 확인용)
_ORDER_BY_RE = re.compile(
    r"\bORDER\s+BY\b(.*?)(?=\bLIMIT\b|\bFETCH\b|\bUNION\b|;|$)",
    re.IGNORECASE | re.DOTALL,
)
_AGG_FN_RE = re.compile(r"\b(?:AVG|SUM|MAX|MIN|COUNT)\s*\(", re.IGNORECASE)


def check_contradictory_alias_resource_type(sql: str) -> list[str]:
    """조인에서 타입이 고정된 alias를 다른 resource_type으로 검사하는 패턴을 탐지한다 (D-099).

    `LEFT JOIN cmm_resource r … AND r.resource_type = 'server.Server'`로 고정한 alias를
    집계에서 `CASE WHEN r.resource_type = 'server.Cpus'`로 검사하면 조건이 **영원히 거짓**이라
    값이 항상 NULL이 된다. 순위 질의에서는 정렬이 무의미해져 임의 서버가 1위로 뽑히는 침묵
    오답이 된다(2026-07-20 라이브 실측 — SV-BATCH-009 오답). 주석 제거 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록 (위반 alias당 1건)
    """
    text = sqlparse.format(sql, strip_comments=True)

    from_m = re.search(r"\bFROM\b", text, re.IGNORECASE)
    if not from_m:
        return []
    stop_m = re.search(
        r"\bGROUP\s+BY\b|\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b",
        text[from_m.start():], re.IGNORECASE,
    )
    join_region = (
        text[from_m.start(): from_m.start() + stop_m.start()]
        if stop_m else text[from_m.start():]
    )
    select_region = text[: from_m.start()]

    # 조인/WHERE에서 단일 resource_type으로 고정된 alias 수집
    fixed: dict[str, set[str]] = {}
    for alias, rt in _CASE_RT_RE.findall(join_region):
        fixed.setdefault(alias, set()).add(rt)
    for m in _RESOURCE_TYPE_MULTI_RE.finditer(join_region):
        fixed.setdefault(m.group(1), set()).update(
            v.strip("'") for v in re.findall(r"'[^']*'", m.group(2))
        )

    errors: list[str] = []
    flagged: set[tuple[str, str]] = set()
    for alias, rt in _CASE_RT_RE.findall(select_region):
        allowed = fixed.get(alias)
        if not allowed or rt in allowed or (alias, rt) in flagged:
            continue
        flagged.add((alias, rt))
        errors.append(
            f"alias '{alias}'는 조인에서 resource_type={sorted(allowed)}로 고정되어 있는데 "
            f"SELECT의 집계 조건이 '{rt}'를 검사합니다 — 조건이 영원히 거짓이라 값이 항상 "
            "NULL이 되고, 순위 정렬이 무의미해져 임의 행이 1위로 뽑힙니다. 자식 리소스 값은 "
            f"'{rt}'를 포함하는 alias(예: 다중 타입 피벗 alias 또는 해당 타입 전용 조인 alias)로 "
            "참조하세요."
        )
    return errors


def check_ranking_order_by_nulls_last(sql: str) -> list[str]:
    """집계 기준 순위 정렬에 NULLS LAST가 없는 패턴을 탐지한다 (D-099).

    PostgreSQL의 `ORDER BY … DESC` 기본은 **NULLS FIRST**라, 값이 없는 행이 선두를 차지해
    `LIMIT 1` 순위 질의가 임의 행을 반환한다(2026-07-20 라이브 실측). 집계식(또는 집계
    alias) 기준 DESC 정렬 + 행 제한이 있으면 NULLS LAST를 요구한다. 주석 제거 후 판정한다.

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)
    if not re.search(r"\bLIMIT\b|\bFETCH\s+FIRST\b", text, re.IGNORECASE):
        return []

    m = _ORDER_BY_RE.search(text)
    if not m:
        return []
    segment = m.group(1)
    if re.search(r"\bNULLS\s+(?:LAST|FIRST)\b", segment, re.IGNORECASE):
        return []
    if not re.search(r"\bDESC\b", segment, re.IGNORECASE):
        return []

    # 집계식 직접 정렬이거나, SELECT에서 집계로 정의된 alias 정렬일 때만 요구
    ordering_is_aggregate = bool(_AGG_FN_RE.search(segment))
    if not ordering_is_aggregate:
        select_region = text[: (re.search(r"\bFROM\b", text, re.IGNORECASE) or m).start()]
        for token in re.findall(r'"([^"]+)"|\b([A-Za-z_]\w*)\b', segment):
            alias = token[0] or token[1]
            if not alias or alias.upper() in ("DESC", "ASC", "NULLS", "LAST", "FIRST"):
                continue
            if re.search(
                rf"(?:AVG|SUM|MAX|MIN|COUNT)\s*\(.*?\)\s*(?:::\w+\s*)?AS\s+\"?{re.escape(alias)}\"?",
                select_region, re.IGNORECASE | re.DOTALL,
            ):
                ordering_is_aggregate = True
                break
    if not ordering_is_aggregate:
        return []

    return [
        "집계 기준 내림차순 정렬 + 행 제한(LIMIT/FETCH FIRST)인데 NULLS LAST가 없습니다. "
        "PostgreSQL의 DESC 기본은 NULLS FIRST라 값이 없는 행이 1위로 뽑힙니다. "
        "`ORDER BY <집계> DESC NULLS LAST`로 작성하세요."
    ]


#: `NULLS LAST` 문법이 없는 엔진 — NULL을 가장 작은 값으로 정렬해 `DESC`에서 이미 맨 뒤다
#: (`src/nodes/result_merger.py` `_NULLS_SMALLEST_ENGINES`와 같은 사실 · plans/137 W9).
_NO_NULLS_ORDERING_ENGINES = frozenset({"mariadb", "mysql"})


def ensure_ranking_nulls_last(sql: str, *, db_engine: str | None = None) -> str:
    """집계 순위 정렬의 NULLS LAST 누락을 반려 대신 결정적으로 교정한다 (D-202 2차).

    D군 2차 폐쇄망 실측(D-04 CM) — LLM이 에러 힌트를 받고도 재시도 전부에서 NULLS LAST를
    반복 누락해 재생성 예산(3회)을 이 한 가지로 소진했다. 요구 수정이 기계적 부가
    (`DESC` → `DESC NULLS LAST`, 의미 변경 없음)이므로 생성 직후 결정적으로 교정한다
    (Known Mistakes: 프롬프트 강제 반복 실패 형태는 결정적 처리 대상).

    check_ranking_order_by_nulls_last가 반려할 SQL만 대상 — 비대상은 바이트 불변.
    NULLS LAST는 PostgreSQL·DB2 공통 문법이지만 **MariaDB·MySQL에는 없다**(D-305 — D-202 2차의
    「방언 분기 불필요」 개정). 그 엔진은 원문 그대로 둔다 — 붙이면 1064 구문 오류가 나고, 이
    교정기가 재시도마다 다시 붙여 예산을 소진했다(폐쇄망 ITAM 실측 2026-10-06). 그 엔진은 NULL을
    가장 작게 정렬해 `DESC`에서 이미 맨 뒤라 교정이 지키려던 의미가 부가 없이 성립한다.
    ``db_engine`` 미전달은 종전 동작(부가)이다.
    """
    if str(db_engine or "").strip().lower() in _NO_NULLS_ORDERING_ENGINES:
        return sql
    if not check_ranking_order_by_nulls_last(sql):
        return sql
    m = _ORDER_BY_RE.search(sql)
    if not m:
        return sql  # 원문에서 ORDER BY 구간 미특정(주석 개입 등) — 교정 포기, 검증기에 맡김
    segment = m.group(1)
    fixed = re.sub(
        r"\bDESC\b(?!\s+NULLS)", "DESC NULLS LAST", segment, flags=re.IGNORECASE
    )
    return sql[: m.start(1)] + fixed + sql[m.end(1):]


# ── plans/116 §10.3: EAV 문자열 값 순위 정렬 교정 ─────────────────────────────
# EAV 값 컬럼은 문자열이다. LLM이 크기·숫자 속성을 캐스트 없이 뽑아 그 별칭으로 정렬하면
# '8.0 GB' > '65536' > '64.0 GB'(문자열 순)가 되어 「메모리 큰 상위 3대」가 8GB 서버를 냈다.
# 결정적 조립(assembler)과 같은 정렬식을 LLM 경로 SQL에도 건다.
_EAV_VALUE_COLUMN = "stringvalue_short"
_ORDER_ITEM_RE = re.compile(
    r'\s*("?)(\w+(?:\.\w+)?)\1(\s+(?:ASC|DESC))?(\s+NULLS\s+(?:FIRST|LAST))?\s*',
    re.IGNORECASE,
)
_THEN_VALUE_RE = re.compile(rf"(\bTHEN\s+)((?:\w+\.)?{_EAV_VALUE_COLUMN})(\s+END\b)", re.IGNORECASE)


def _blank_comments_and_depth(sql: str) -> tuple[str, list[int]]:
    """주석을 같은 길이 공백으로 지운 본문과 글자별 괄호 깊이(문자열 리터럴 안 괄호 제외)."""
    out = list(sql)
    depth: list[int] = [0] * len(sql)
    level, i, n = 0, 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch == "'":
            j = i + 1
            while j < n and not (sql[j] == "'" and not sql.startswith("''", j)):
                j += 2 if sql.startswith("''", j) else 1
            for k in range(i, min(j + 1, n)):
                depth[k] = level
            i = j + 1
            continue
        if sql.startswith("--", i) or sql.startswith("/*", i):
            end = sql.find("\n", i) if ch == "-" else sql.find("*/", i + 2)
            end = n if end < 0 else (end if ch == "-" else end + 2)
            for k in range(i, end):
                out[k] = " " if sql[k] != "\n" else "\n"
                depth[k] = level
            i = end
            continue
        if ch == "(":
            level += 1
        depth[i] = level
        if ch == ")":
            level = max(0, level - 1)
        i += 1
    return "".join(out), depth


def _eav_value_aliases(code: str) -> dict[str, str]:
    """정렬 키(SELECT 별칭·조인 별칭 값 컬럼) → 값 크기 순 정렬식. 숫자·크기 속성만 모은다."""
    from src.db_adapters.polestar.assembler import eav_sort_expr

    found: dict[str, str] = {}
    # 형태 1 — 피벗: MAX(CASE WHEN … <x>.name = '<속성>' THEN <x>.stringvalue_short END) AS 별칭
    for m in re.finditer(r"\b(?:MAX|MIN)\s*\(", code, re.IGNORECASE):
        open_pos, level, close = m.end() - 1, 0, None
        for k in range(open_pos, len(code)):
            if code[k] == "(":
                level += 1
            elif code[k] == ")":
                level -= 1
                if level == 0:
                    close = k
                    break
        if close is None:
            continue
        inner = code[open_pos + 1: close]
        am = re.search(rf"\bTHEN\s+(?:\w+\.)?{_EAV_VALUE_COLUMN}\s+END\b", inner, re.IGNORECASE)
        lm = re.findall(r"\.name\s*=\s*'([^']+)'", inner, re.IGNORECASE)
        alias_m = re.match(r'\s+AS\s+"?(\w+)"?', code[close + 1:], re.IGNORECASE)
        if not (am and len(set(lm)) == 1 and alias_m) or re.search(r"CAST|::", inner, re.I):
            continue
        sort_expr = eav_sort_expr(am.group(0).split()[1], lm[0])
        if sort_expr is not None:
            # 집계 안쪽의 값 컬럼만 정렬식으로 바꾼다(피벗은 서버당 값 1개 — MAX 선택 불변).
            agg = code[m.start(): close + 1]
            found[alias_m.group(1)] = _THEN_VALUE_RE.sub(
                lambda t: f"{t.group(1)}{sort_expr}{t.group(3)}", agg, count=1
            )
    # 형태 2 — 속성별 조인 별칭: JOIN … <a> ON … <a>.name = '<속성>'
    #         + SELECT <a>.stringvalue_short AS 별칭
    per_alias: dict[str, set[str]] = {}
    for m in re.finditer(r"\b(\w+)\.name\s*=\s*'([^']+)'", code, re.IGNORECASE):
        per_alias.setdefault(m.group(1), set()).add(m.group(2))
    for join_alias, attrs in per_alias.items():
        if len(attrs) != 1:
            continue  # 한 별칭이 여러 속성을 거르면 피벗 형태 — 형태 1이 맡는다
        expr = f"{join_alias}.{_EAV_VALUE_COLUMN}"
        sort_expr = eav_sort_expr(expr, next(iter(attrs)))
        if sort_expr is None:
            continue
        found[expr.lower()] = sort_expr
        for am in re.finditer(
            rf'\b{re.escape(expr)}\s+AS\s+"?(\w+)"?', code, re.IGNORECASE
        ):
            found[am.group(1)] = sort_expr
    return found


def ensure_eav_value_order(sql: str) -> str:
    """최상위 ORDER BY가 캐스트 없는 EAV 숫자·크기 값이면 값 크기 순 식으로 바꾼다.

    대상: 정렬 항목이 ①그런 값을 뽑은 SELECT 별칭이거나 ②속성별 조인 별칭의 값 컬럼
    그 자체일 때. 출력 칼럼(표시값 '8.0 GB')은 그대로 두고 정렬 키만 바꾼다. 내림차순에는
    NULLS LAST를 붙인다(PostgreSQL DESC 기본 NULLS FIRST — D-098). SELECT DISTINCT·집합
    연산은 ORDER BY 식이 SELECT 목록에 있어야 하므로 건드리지 않는다. 비대상은 바이트 불변.
    """
    if not sql or _EAV_VALUE_COLUMN not in sql.lower():
        return sql
    code, depth = _blank_comments_and_depth(sql)
    if re.search(r"\bSELECT\s+DISTINCT\b|\bUNION\b|\bINTERSECT\b|\bEXCEPT\b", code, re.I):
        return sql
    tops = [m for m in re.finditer(r"\bORDER\s+BY\b", code, re.I) if depth[m.start()] == 0]
    if not tops:
        return sql
    seg_start = tops[-1].end()
    seg_end = len(code)
    for m in re.finditer(r"\bLIMIT\b|\bFETCH\b|\bOFFSET\b|;", code[seg_start:], re.I):
        if depth[seg_start + m.start()] == 0:
            seg_end = seg_start + m.start()
            break
    aliases = _eav_value_aliases(code)
    if not aliases:
        return sql
    # 최상위 콤마로 정렬 항목 분할
    items: list[tuple[int, int]] = []
    last = seg_start
    for k in range(seg_start, seg_end):
        if code[k] == "," and depth[k] == 0:
            items.append((last, k))
            last = k + 1
    items.append((last, seg_end))
    out, changed = sql, False
    for start, end in reversed(items):  # 뒤에서부터 치환해 앞 오프셋을 보존한다
        m = _ORDER_ITEM_RE.fullmatch(code[start:end])
        key = m and (aliases.get(m.group(2)) or aliases.get(m.group(2).lower()))
        if not key:
            continue
        direction = (m.group(3) or "").strip().upper()
        tail = m.group(3) or ""
        if direction == "DESC" and not m.group(4):
            tail += " NULLS LAST"
        tail += m.group(4) or ""
        last_group = 4 if m.group(4) else 3 if m.group(3) else None
        span_end = start + (m.end(last_group) if last_group else m.end(2) + len(m.group(1)))
        out = out[: start + m.start(1)] + key + tail + out[span_end:]
        changed = True
    return out if changed else sql


def check_alarm_resource_server_type_filter(sql: str) -> list[str]:
    """알람 자원에 대한 WHERE `resource_type = 'server.Server'` 필터를 탐지한다 (D-202 2차).

    알람은 자식 리소스(server.Cpus/Memory/Disks/FileSystems 등)에 붙으므로, 알람 조회에서
    자원을 server.Server로 INNER 한정하면 자식 리소스 알람이 전부 탈락한다 — 0건(D군 2차
    D-05 실측) 또는 침묵 축소(2026-09-02 실측: B0 1174→46건). 서버 식별이 필요하면 부모
    승격 LEFT JOIN을 쓰고, 타입 한정은 그 **ON 절**에 둬야 한다(결정적 조립 골격과 동일).
    WHERE 이후 구간만 판정하므로 ON 절의 타입 한정은 대상이 아니다. 주석 제거 후
    판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)
    if not re.search(r"\bcmm_alarm(?:_active)?\b", text, re.IGNORECASE):
        return []
    where_m = re.search(r"\bWHERE\b", text, re.IGNORECASE)
    if not where_m:
        return []
    if not re.search(
        r"\bresource_type\s*=\s*'server\.Server'", text[where_m.start():], re.IGNORECASE
    ):
        return []
    return [
        "알람 자원에 WHERE resource_type = 'server.Server' 필터가 있습니다. 알람은 자식 "
        "리소스(server.Cpus/Memory/Disks/FileSystems 등)에 붙으므로 이 필터가 자식 리소스 "
        "알람을 전부 탈락시켜 0건 또는 침묵 축소가 됩니다. 알람의 자원(res)에는 "
        "resource_type 필터를 걸지 말고, 서버 이름·IP가 필요하면 `LEFT JOIN cmm_resource srv "
        "ON srv.id = COALESCE(res.platform_resource_id, res.service_resource_id, res.id)`로 "
        "부모 서버를 승격해 srv에서 읽으세요(타입 한정이 필요하면 WHERE가 아니라 그 LEFT "
        "JOIN의 ON 절에 두세요)."
    ]


# 파생 테이블(서브쿼리) 조인: `) alias ON <조건>` — 조건은 다음 절 키워드 전까지
_DERIVED_JOIN_RE = re.compile(
    r"\)\s*(\w+)\s+ON\s+(.*?)(?=\bJOIN\b|\bWHERE\b|\bGROUP\s+BY\b|\bHAVING\b|"
    r"\bORDER\s+BY\b|\bLIMIT\b|\bFETCH\b|$)",
    re.IGNORECASE | re.DOTALL,
)
# 값 컬럼(식별자가 아닌 업무 값) — 조인 키로 쓰면 NULL·중복·다중값에 취약하다.
_VALUE_JOIN_COLUMNS = ("ipaddress", "hostname", "name")


def check_value_column_join(sql: str) -> list[str]:
    """파생 테이블(설정 피벗 서브쿼리)을 값 컬럼으로 조인한 패턴을 탐지한다.

    폴스타 설정 피벗 서브쿼리는 `COALESCE(platform_resource_id, id) AS id`로 **서버 식별자**를
    노출한다. 그런데 이 서브쿼리를 `ON svr.ipaddress = hi.ipaddress`처럼 값 컬럼으로 조인하면
    ①값이 비면 조인이 전부 깨져 CPU 코어수·메모리 용량이 NULL이 되고(D-058/D-061 계열 —
    EAV로 읽던 시절 실측된 경로) ②IP·호스트명이 중복·다중(NIC 복수)이면 행이 뻥튀기된다.
    식별자 조인(`ON svr.id = hi.id`)이 가능한데 값 컬럼을 쓴 경우만 지적한다(서브쿼리가 id를
    노출하지 않으면 값 조인이 유일한 수단이므로 대상 아님). 주석 제거 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록 (위반 조인당 1건)
    """
    text = sqlparse.format(sql, strip_comments=True)

    errors: list[str] = []
    for m in _DERIVED_JOIN_RE.finditer(text):
        alias, condition = m.group(1), m.group(2)
        a = re.escape(alias)
        # 서브쿼리가 식별자 컬럼(AS id)을 노출하는가 — 노출하지 않으면 값 조인이 불가피
        if not re.search(r"\bAS\s+id\b", text[: m.start()], re.IGNORECASE):
            continue
        for column in _VALUE_JOIN_COLUMNS:
            c = re.escape(column)
            if re.search(
                rf"\b\w+\.{c}\s*=\s*{a}\.\w+|\b{a}\.{c}\s*=\s*\w+\.\w+",
                condition, re.IGNORECASE,
            ):
                errors.append(
                    f"파생 테이블 '{alias}'을 값 컬럼({column})으로 조인했습니다. 값 컬럼은 "
                    "비어 있거나 중복될 수 있어(IP 미등록·다중 NIC·호스트명 중복) 조인이 조용히 "
                    f"0건이 되거나 행이 중복됩니다. 서브쿼리가 노출하는 식별자로 조인하세요 "
                    f"(예: ON svr.id = {alias}.id — {alias}.id는 "
                    "COALESCE(platform_resource_id, id)로 서버 식별자입니다)."
                )
                break
    return errors


def check_scope_filter_where_demotion(sql: str) -> list[str]:
    """다중 resource_type 피벗 alias의 서버 식별 필터가 WHERE에 있는 패턴을 탐지한다 (D-096).

    폴스타 부모-자식 피벗은 한 alias로 서버 행(server.Server)과 자식 리소스 행
    (server.Cpus/Memory 등)을 함께 접어 조회한다(`resource_type IN (...)` +
    GROUP BY COALESCE(platform_resource_id, id)). 이 alias에 서버명 필터
    (`name/hostname IN (...)`)를 **WHERE**로 걸면 자식 리소스 행(name='Cpus' 등)이
    전부 탈락해 메트릭 집계가 침묵히 0건이 된다(2026-07-20 라이브 실측 — D-095
    선행 스코프 주입 SQL의 오답 형태). 서버 한정은 HAVING의 집계 CASE WHEN으로
    적용해야 한다. 주석 제거 후 판정한다(D-087과 동일 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록 (위반 alias당 1건)
    """
    text = sqlparse.format(sql, strip_comments=True)

    # 다중 resource_type(2개 이상 리터럴)을 접는 alias 수집
    multi_aliases: set[str] = set()
    for m in _RESOURCE_TYPE_MULTI_RE.finditer(text):
        if len(re.findall(r"'[^']*'", m.group(2))) >= 2:
            multi_aliases.add(m.group(1))
    if not multi_aliases:
        return []

    errors: list[str] = []
    flagged: set[str] = set()
    for seg_m in _WHERE_SEGMENT_RE.finditer(text):
        segment = seg_m.group(1)
        for alias in multi_aliases - flagged:
            a = re.escape(alias)
            identity_filter = (
                rf"(?:\b{a}\.(?:name|hostname)\b|COALESCE\s*\(\s*{a}\.(?:name|hostname)\b[^()]*\))"
                rf"\s*(?:=|!=|<>|NOT\s+IN\b|IN\b|I?LIKE\b)"
            )
            if re.search(identity_filter, segment, re.IGNORECASE):
                flagged.add(alias)
                errors.append(
                    f"다중 resource_type 피벗 alias '{alias}'의 서버 식별 필터"
                    f"({alias}.name/hostname)가 WHERE 절에 있습니다. 이 alias는 서버 행과 "
                    "자식 리소스 행(server.Cpus 등)을 함께 조회하므로 WHERE의 서버명 필터는 "
                    "자식 리소스 행(name='Cpus' 등)을 모두 제거해 메트릭 집계가 0건이 됩니다. "
                    "서버 한정 조건은 HAVING의 집계 CASE WHEN으로 적용하세요 "
                    f"(예: HAVING MAX(CASE WHEN {alias}.resource_type = 'server.Server' "
                    f"THEN {alias}.name END) IN (...)). 또는 서버 엔터티와 자식 리소스를 "
                    "별도 alias로 분리 조인하고 서버 alias에만 필터를 적용하세요."
                )
    return errors


# 알람 계열(cmm_alarm 프리픽스) 허용 테이블 — 프로필 `alarm_allowed_tables`의 cmm_alarm
# 계열과 동일 집합(존 공통). 목록 밖 테이블(예: 심각도 표시 테이블) 조인은 라벨 문자열
# 필터로 이어져 침묵 0건이 된다(2026-09-01 라이브 실측 — displayname ILIKE '%critical%'가
# 한글 라벨과 불일치, 심각 활성 알람 128건이 "데이터 없음"으로 응답됨).
_ALARM_TABLE_ALLOWLIST = frozenset({
    "cmm_alarm",
    "cmm_alarm_active",
    "cmm_alarm_def",
    "cmm_alarm_def_noti",
    "cmm_alarm_def_noti_user",
    "cmm_alarm_def_noti_group",
    "cmm_alarm_def_noti_role",
    "cmm_alarm_def_noti_rmtype",
})
_ALARM_TABLE_RE = re.compile(
    r"\b(?:FROM|JOIN)\s+[\w.]*?\b(cmm_alarm\w*)\b", re.IGNORECASE
)
_SEVERITY_GUIDE = (
    "심각도 조건은 라벨 문자열이 아니라 alarmseverity 정수 비교로 작성하세요 "
    "(심각=3, 경고=2, 주의=1, 해소=0 — 예: a.alarmseverity = 3)."
)
# alarmseverity를 문자열 리터럴과 직접 비교: = '심각', ILIKE 'critical' 등
_SEVERITY_STR_CMP_RE = re.compile(
    r"\balarmseverity\s*(?:=|!=|<>|NOT\s+I?LIKE|I?LIKE)\s*'([^']*)'", re.IGNORECASE
)
# alarmseverity IN (...) 나열 — 리터럴 목록을 캡처해 비숫자 문자열만 위반으로 본다
_SEVERITY_IN_RE = re.compile(
    r"\balarmseverity\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.IGNORECASE
)
# 심각도 표시 라벨 컬럼(displayname)에 대한 문자열 필터
_DISPLAYNAME_CMP_RE = re.compile(
    r"\bdisplayname\s*(?:=|!=|<>|NOT\s+I?LIKE|I?LIKE)\s*'([^']*)'", re.IGNORECASE
)
# 라벨 어휘 — 영문(폴스타 라벨은 한글이라 영문 매칭은 반드시 0건)·한글 심각도 명칭
_SEVERITY_LABEL_TOKENS = (
    "critical", "warning", "major", "minor", "severe", "clear",
    "심각", "경고", "주의", "해소",
)


def check_alarm_table_allowlist(sql: str) -> list[str]:
    """허용 목록 밖 알람 계열 테이블 참조를 탐지한다 (P1-③).

    알람 조회의 허용 테이블은 프로필 `alarm_allowed_tables`로 프롬프트에 안내되지만,
    공용 validator의 테이블 존재 검사는 라이브 스키마(수백 개 전체) 기준이라 목록 밖
    테이블도 실존하면 통과한다. cmm_alarm 프리픽스 계열만 결정적으로 차단해 심각도
    표시 테이블 등으로의 우회를 막는다(2026-09-01 라이브 실측). 주석 제거 후 판정한다
    (D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록 (위반 테이블당 1건)
    """
    text = sqlparse.format(sql, strip_comments=True)
    errors: list[str] = []
    seen: set[str] = set()
    for m in _ALARM_TABLE_RE.finditer(text):
        table = m.group(1).lower()
        if table in _ALARM_TABLE_ALLOWLIST or table in seen:
            continue
        seen.add(table)
        errors.append(
            f"알람 조회에 허용되지 않은 테이블 '{table}'을(를) 참조했습니다. "
            f"알람 계열은 {', '.join(sorted(_ALARM_TABLE_ALLOWLIST))}만 사용하세요. "
            + _SEVERITY_GUIDE
        )
    return errors


def check_severity_label_filter(sql: str) -> list[str]:
    """심각도를 라벨 문자열로 필터한 패턴을 탐지한다 (P1-①).

    폴스타 심각도 정본은 alarmseverity 정수(심각=3, 경고=2, 주의=1, 해소=0)다. LLM이
    ①`alarmseverity = '심각'`처럼 정수 컬럼을 비숫자 문자열과 비교하거나 ②표시 라벨
    컬럼(displayname)을 'critical' 등 라벨 문자열로 필터하면, 라벨 불일치(운영 라벨은
    한글)로 항상 0건이 되는 침묵 오답이 난다(2026-09-01 라이브 실측 — 심각 활성 알람
    128건 존재에도 0건 응답). 숫자 문자열('3')은 암묵 캐스트로 동작하므로 허용한다.
    주석 제거 후 판정한다(D-087 규약).

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)
    errors: list[str] = []

    for m in _SEVERITY_STR_CMP_RE.finditer(text):
        literal = m.group(1)
        if not re.fullmatch(r"\s*\d+\s*", literal):
            errors.append(
                f"alarmseverity(정수 컬럼)를 문자열 '{literal}'와 비교했습니다. "
                + _SEVERITY_GUIDE
            )

    for m in _SEVERITY_IN_RE.finditer(text):
        for lit in re.findall(r"'([^']*)'", m.group(1)):
            if not re.fullmatch(r"\s*\d+\s*", lit):
                errors.append(
                    f"alarmseverity IN 목록에 비숫자 문자열 '{lit}'이(가) 있습니다. "
                    + _SEVERITY_GUIDE
                )
                break

    for m in _DISPLAYNAME_CMP_RE.finditer(text):
        normalized = m.group(1).lower().strip("%_ ")
        if any(token in normalized for token in _SEVERITY_LABEL_TOKENS):
            errors.append(
                f"심각도 표시 라벨(displayname)을 문자열 '{m.group(1)}'로 필터했습니다. "
                "표시 라벨은 환경에 따라 달라 필터 기준이 될 수 없습니다. " + _SEVERITY_GUIDE
            )
    return errors


# 활성 판정 오답: currentalarmstatus를 'ACTIVE'류 리터럴과 비교하는 패턴. LLM이 활성
# 알람을 `currentalarmstatus = 'ACTIVE'`로 추정 생성하지만 이 값은 어느 존에도 없다
# (2026-09-02 폐쇄망 실측 — cmm_alarm 테이블 3존 0건, cmm_alarm_active에서도 0건.
# 이 칼럼의 실제 어휘는 'NOT_ACK' 등 **확인(ACK) 상태**로 실측됨). 따라서 활성 판정은
# cmm_alarm_active 존재로만 하고, ACTIVE류 리터럴 비교만 반려한다 — ACK 어휘 비교
# ("미확인 알람" 질의의 NOT_ACK 등)는 정당하므로 손대지 않는다.
# 칼럼을 LOWER/UPPER/TRIM으로 감싼 형태(`LOWER(a.currentalarmstatus) = 'active'`)도 같은
# 오답이다 — 닫는 괄호를 건너 비교 연산자를 잡는다(plans/116 §10.3).
_ACTIVE_STATUS_CMP_RE = re.compile(
    r"\bcurrentalarmstatus\s*\)*\s*(?:=|!=|<>|NOT\s+I?LIKE|I?LIKE)\s*'([^']*)'",
    re.IGNORECASE,
)
# 좌우가 뒤집힌 비교: 'active' = [함수(]a.currentalarmstatus
_ACTIVE_STATUS_CMP_REVERSED_RE = re.compile(
    r"'([^']*)'\s*(?:=|!=|<>)\s*(?:(?:LOWER|UPPER|TRIM)\s*\(\s*)*(?:\w+\.)?currentalarmstatus\b",
    re.IGNORECASE,
)
_ACTIVE_STATUS_IN_RE = re.compile(
    r"\bcurrentalarmstatus\s+(?:NOT\s+)?IN\s*\(([^)]*)\)", re.IGNORECASE
)
# 활성/열림 의미로 오인되는 리터럴 — 실측 어휘(ACK 계열)에 존재하지 않는 값들
_ACTIVE_LIKE_LITERAL_RE = re.compile(
    r"^\s*%?_?(?:active|activated|open|opened|enabled|on|활성|발생)%?\s*$",
    re.IGNORECASE,
)
_ACTIVE_GUIDE = (
    "활성 알람 여부는 cmm_alarm_active 조인으로 판정하세요 "
    "(예: JOIN cmm_alarm_active ca ON ca.alarm_id = a.id — "
    "cmm_alarm_active에 행이 있으면 활성). currentalarmstatus는 확인(ACK) 상태 "
    "칼럼이며 실제 값은 NOT_ACK(미확인)·ACKED(확인)·FINISHED뿐입니다"
    "(2026-09-02 3존 실측 — 'ACTIVE'는 존재하지 않음). 활성 목록·건수 질의라면 "
    "이 칼럼을 필터에 쓰지 마세요."
)


def check_active_status_literal_filter(sql: str) -> list[str]:
    """currentalarmstatus를 ACTIVE류 리터럴과 비교한 패턴을 탐지한다 (V1-4/V1-5 실측).

    'NOT_ACK' 등 확인 상태 어휘 비교는 정당하므로(미확인 알람 질의) 반려하지 않는다.

    Args:
        sql: SQL 쿼리

    Returns:
        에러 메시지 목록
    """
    text = sqlparse.format(sql, strip_comments=True)
    offending: list[str] = []
    for regex in (_ACTIVE_STATUS_CMP_RE, _ACTIVE_STATUS_CMP_REVERSED_RE):
        for m in regex.finditer(text):
            if _ACTIVE_LIKE_LITERAL_RE.match(m.group(1)):
                offending.append(m.group(1))
    for m in _ACTIVE_STATUS_IN_RE.finditer(text):
        for lit in re.findall(r"'([^']*)'", m.group(1)):
            if _ACTIVE_LIKE_LITERAL_RE.match(lit):
                offending.append(lit)
    if offending:
        return [
            f"currentalarmstatus를 '{offending[0]}'와 비교했습니다 — 이 값은 존재하지 "
            "않아 항상 0건입니다. " + _ACTIVE_GUIDE
        ]
    return []


# ── D-201: "이번 달" 질의의 stat_m 당월 조회 반려 (2026-09-07) ────────────────
# 폴스타 월간 통계(cmm_metric_stat_m)는 직전월까지만 집계된다(C-06 3존 실측) —
# "이번 달" 질의를 stat_m으로 생성하면 전부 null이다. 프로필 query_guide 규칙
# (stat_d 당월 1일~어제 집계)의 LLM 순응은 비결정이므로, 검증기가 결정적으로
# 반려해 재생성 힌트를 준다(프롬프트 규칙 + 검증기 2겹 — D-201 주의① 예고분).

_CURRENT_MONTH_STAT_GUIDE = (
    "'이번 달' 성능 통계는 cmm_metric_stat_m(월간)으로 조회하면 안 됩니다 — 월간 통계는 "
    "직전월까지만 집계되어 현재 월 값이 전부 null입니다. cmm_metric_stat_d(일간)를 "
    "stat_date BETWEEN 당월 1일 AND 어제 범위로 조회하고, 서버별 GROUP BY로 "
    "AVG(avg_val)=월중 평균, MAX(max_val)=월중 최대를 집계하세요."
)


def check_current_month_stat_table(sql: str, user_query: str) -> list[str]:
    """'이번 달' 단일 기간 질의가 stat_m을 참조하면 반려한다 (D-201 결정적 안전망).

    발동을 좁게 고정한다: 질의의 기간 해석(resolve_stat_month_range — 프로필 규칙과
    같은 결정적 해석기)이 **정확히 (당월, 당월)** 일 때만. 범위 질의("5월부터 이번
    달까지")는 stat_m 사용이 부분 정당하므로 미발동. stat_d로 생성된 SQL은 통과.

    Args:
        sql: SQL 쿼리
        user_query: 사용자 원문 질의(기간 해석용)

    Returns:
        에러 메시지 목록
    """
    from datetime import date as _date

    from src.utils.query_gen_common import resolve_stat_month_range

    period = resolve_stat_month_range(user_query or "", _date.today())
    cur = _date.today().strftime("%Y%m")
    if period != (cur, cur):
        return []
    if not re.search(r"\bcmm_metric_stat_m\b", sql, re.IGNORECASE):
        return []
    return [_CURRENT_MONTH_STAT_GUIDE]


# ── plans/122 T-5b: 생성 후 시간 조건 검증 (D-309 · 2026-10-06) ─────────────────
# 실행 전 SQL의 시간 조건(통계 stat_date · 알람 ctime)을 요청 시간 해석(`QueryTime`)과 대조한다.
# 경계 대조의 공용 틀은 `src.sql_validation`(DB 무관)이고, 테이블·stat_date 형식·TIMESTAMP 경계는
# 생산자(프롬프트 기간 블록·결정적 조립)와 같은 투영 `time_period`에서 온다 — 「검증기가 반려하는
# SQL = 생산자가 바꾸는 SQL」(D-231 교훈). 사용자가 기간을 말했을 때(`qt.explicit` · 비교 질의
# 아님)만 반려하고, 기본값(지난달)·비교 질의·파싱 불가는 경고 로그만 남긴다(거짓 거부 방지 —
# LLM이 합당한 이유로 다른 입도를 고를 수 있다). D-201(「이번 달」 stat_m 반려)은 ②③이 덮는다.

_STAT_COLUMN = "stat_date"
_ALARM_TIME_TABLES = ("cmm_alarm", "cmm_alarm_active")
_ALARM_TIME_COLUMN = "ctime"
_STAT_GRAIN_OF_TABLE = {table: grain for grain, table in STAT_TABLES.items()}
_STAT_FORMAT_LABEL = {"hour": "YYYYMMDDHH", "day": "YYYYMMDD", "month": "YYYYMM"}
_GRAIN_LABEL = {"hour": "시간", "day": "일", "month": "월"}
_GRAIN_RANK = {"hour": 0, "day": 1, "month": 2}
_SUPPORTED_OPS = frozenset({"=", "between", "in", ">=", ">", "<", "<="})
#: 알람 경계 대조 허용 오차 — `<= '… 23:59:59'`·`> '… 23:59:59'` 같은 닫힌 끝 표기를
#: 같은 경계로 본다.
_TS_TOLERANCE = timedelta(seconds=1)
_DB2_TS_FORMATS = ("%Y-%m-%d-%H.%M.%S.%f", "%Y-%m-%d-%H.%M.%S")
_ISO_DATE_HEAD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _stat_key(grain: str) -> Callable[[str], str]:
    """입도별 `stat_date` 키 함수 — 형식(자릿수·실재 날짜)이 어긋나면 ValueError."""
    fmt = STAT_DATE_FORMATS[grain]
    width = len(_STAT_FORMAT_LABEL[grain])

    def key(value: str) -> str:
        v = value.strip()
        if len(v) != width or not v.isdigit():
            raise ValueError(value)
        datetime.strptime(v, fmt)  # 존재하지 않는 날짜·시각이면 ValueError
        return v

    return key


def _stat_next(grain: str) -> Callable[[str], str]:
    """입도별 다음 칸 함수(`2026083023` → `2026083100` · `202612` → `202701`)."""
    fmt = STAT_DATE_FORMATS[grain]

    def next_cell(value: str) -> str:
        # 칸 이동은 도메인 단위 연산을 쓴다(리뷰 m-1 — 날짜 산술 사본 금지 D-131)
        return shift_unit(datetime.strptime(value, fmt), grain, 1).strftime(fmt)

    return next_cell


def _parse_ts(value: str) -> datetime:
    """알람 시각 리터럴 → naive KST 시각(`YYYY-MM-DD[ HH:MM[:SS[.f]]]`·`T` 구분·DB2 `-HH.MM.SS`)."""
    v = value.strip()
    if not _ISO_DATE_HEAD_RE.match(v):
        raise ValueError(value)
    for fmt in _DB2_TS_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            pass
    dt = datetime.fromisoformat(v)
    return dt.astimezone(KST).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _same_instant(value: datetime) -> datetime:
    """연속 시각의 「다음 칸」 = 자기 자신(닫힌 끝은 허용 오차로 맞춘다)."""
    return value


def _pick_alias(quals: set[str], tables: set[str]) -> str:
    """메시지에 쓸 접두 — 별칭이 있으면 별칭, 없으면 테이블 이름."""
    aliases = sorted(q for q in quals if q not in tables)
    return aliases[0] if aliases else sorted(quals)[0]


def _condition_groups(
    conds: list[ColumnCondition], quals: set[str]
) -> list[list[ColumnCondition]]:
    """대상 테이블 접두의 조건을 (SELECT 블록, 접두)별로 묶는다.

    대상 접두 조건이 하나도 없으면 전부를 본다(CTE 별칭 경유 등). 한 블록에 접두가 하나뿐이면
    접두 없는 조건을 그 묶음에 합친다.
    """
    picked = [c for c in conds if not c.qualifier or c.qualifier in quals] or conds
    by_scope: dict[tuple[int, int], dict[str, list[ColumnCondition]]] = {}
    for c in picked:
        by_scope.setdefault(c.scope, {}).setdefault(c.qualifier, []).append(c)
    groups: list[list[ColumnCondition]] = []
    for per_qual in by_scope.values():
        named = [q for q in per_qual if q]
        if len(named) == 1 and "" in per_qual:
            per_qual[named[0]].extend(per_qual.pop(""))
        groups.extend(per_qual.values())
    return groups


def _judge_groups(
    groups: list[list[ColumnCondition]],
    *,
    key: Callable[[str], Any],
    next_cell: Callable[[Any], Any],
    equal: Callable[[tuple[Any, Any]], bool],
) -> tuple[str, list[str], bool]:
    """조건 묶음들을 기대 구간과 대조한다 → (상태, 묶음 원문 목록, 리터럴 형식 불일치 여부).

    상태(앞이 우선): 묶음 하나라도 기대 구간과 같으면 "match" · OR로 묶인 시간 조건이 있으면 "or"
    (기간을 보장하지 못한다 — 오류) · 읽지 못한 묶음이 있으면 "unparsable"(경고) · 전부 읽었는데
    다르면 "mismatch"(오류) · 묶음이 없으면 "missing"(오류). 상대가 컬럼 참조인 조건(조인 키·CTE
    컬럼)은 구간 계산에서 빼고, 그것만 있는 묶음은 리터럴 묶음이 하나도 없을 때만 "unparsable"로
    본다(리뷰 m-4 — CTE 경유는 누락이 아니다 · 자기 조인 키가 다른 묶음의 불일치를 덮지 않는다).
    """
    if not groups:
        return "missing", [], False
    texts: list[str] = []
    has_or = unparsable = bad_format = evaluated = False
    for g in groups:
        if any(c.reason == REASON_OR for c in g):
            texts.append(" · ".join(c.text for c in g))  # OR 구조는 원문 그대로 재현하지 않는다
            has_or = True
            continue
        texts.append(" AND ".join(c.text for c in g))
        bounded = [c for c in g if c.reason != REASON_COLUMN]
        if not bounded:
            continue  # 컬럼 참조만 — 리터럴 묶음이 없을 때만 「읽기 불가」(아래)
        if any(not c.literal or c.op not in _SUPPORTED_OPS for c in bounded):
            unparsable = True
            continue
        evaluated = True
        try:
            for c in bounded:
                for v in c.values:
                    key(v)
        except ValueError:
            bad_format = True
            continue
        bounds = half_open_bounds(bounded, key=key, next_cell=next_cell)
        if bounds is not None and equal(bounds):
            return "match", texts, False
    if has_or:
        status = "or"
    elif unparsable or not evaluated:
        status = "unparsable"
    else:
        status = "mismatch"
    return status, texts, bad_format


def _or_error(subject: str, where: str, texts: list[str]) -> str:
    """OR 결합 반려 문구(리뷰 m-5 · D6) — 다른 기간이 섞인 SQL이 실행되지 않게 한다."""
    return (
        f"{subject} 조건이 OR로 다른 조건과 묶여 기간을 보장하지 못합니다 — SQL: "
        f"{' / '.join(texts[:3])} · 기대: {where}. OR 없이 기대 조건을 AND로 그대로 쓰세요."
    )


def _now_note(groups: list[list[ColumnCondition]]) -> list[str]:
    funcs: list[str] = []
    for g in groups:
        for c in g:
            funcs.extend(f for f in c.now_functions if f not in funcs)
    return funcs


def _now_function_error(subject: str, where: str, funcs: list[str]) -> str:
    """① 현재시각 함수 반려 문구 — 재생성 프롬프트에 기대 리터럴을 그대로 싣는다."""
    return (
        f"{subject}은 시스템이 해석한 리터럴({where})을 그대로 쓰세요 — "
        f"CURRENT_DATE 등 현재시각 함수 금지(감지: {', '.join(funcs)})."
    )


def _unparsable_finding(
    sql: str, subject: str, where: str, texts: list[str]
) -> tuple[str, str]:
    """리터럴로 읽지 못한 조건 — 거부하지 않고 경고. 단, 조건이 CTE·서브쿼리를 거쳐 현재시각
    함수로 기간을 계산했으면(`s.stat_date >= p.lo` · p가 `CURRENT_DATE`로 계산) ①로 반려한다."""
    funcs = find_now_functions(sql)
    if funcs:
        return "error", _now_function_error(subject, where, funcs)
    return "warn", (
        f"{subject} 조건을 리터럴로 읽지 못해 대조하지 않았습니다(기대: {where}) — "
        f"SQL: {' / '.join(texts[:3])}"
    )


def _stat_findings(
    sql: str, metric: TimeResolution, sb: StatBounds, used: dict[str, set[str]],
    *, present: bool = False,
) -> list[tuple[str, str]]:
    # 원문에 기간과 「현재·지금」이 함께 있고(「지난달 알람이 난 서버의 현재 CPU」) SQL이 시간
    # 통계만 쓰면, 그 통계는 기간이 아니라 「현재」(프로필 규칙 — 최근 시간 통계) 몫으로 본다.
    # 대조는 하되 경고만 남긴다(거짓 거부 방지). 「이번 달 현재까지」+월 통계는 그대로 반려한다.
    if present and sb.grain != "hour" and set(used) == {STAT_TABLES["hour"]}:
        return [
            ("warn", f"「현재」 표현 — 시간 통계를 현재 값 조회로 보고 대조만 남깁니다: {m}")
            for _kind, m in _stat_findings(sql, metric, sb, used)
        ]
    label = metric.label()
    tables = set(used)
    quals = set().union(*used.values())
    alias = _pick_alias(used.get(sb.table) or quals, tables)
    where = sb.where(f"{alias}.{_STAT_COLUMN}")
    groups = _condition_groups(extract_column_conditions(sql, _STAT_COLUMN) or [], quals)
    out: list[tuple[str, str]] = []
    funcs = _now_note(groups)
    if funcs:
        out.append(("error", _now_function_error(f"기간 「{label}」", where, funcs)))
    others = sorted(t for t in tables if t != sb.table)
    if others:
        msg = (
            f"기간 「{label}」은 {sb.table}({_GRAIN_LABEL[sb.grain]} 통계)로 조회해야 합니다 — "
            f"지금 SQL은 {', '.join(others)}을(를) 씁니다. {sb.table}을(를) 조인하고 기간 조건 "
            f"{where}를 그대로 쓰세요."
        )
        if any(_GRAIN_RANK[_STAT_GRAIN_OF_TABLE[t]] > _GRAIN_RANK[sb.grain] for t in others):
            msg += (
                f" {_GRAIN_LABEL[sb.grain]} 통계는 칸마다 행이 있으니 서버별 GROUP BY로 "
                "AVG(avg_val)·MAX(max_val)를 집계하세요."
            )
        if STAT_TABLES["month"] in others:
            msg += f" {STAT_TABLES['month']}(월간)는 완결된 달만 집계됩니다."
        out.append(("error", msg))
        return out
    status, texts, bad_format = _judge_groups(
        groups,
        key=_stat_key(sb.grain),
        next_cell=_stat_next(sb.grain),
        equal=lambda b: b == (sb.lo, sb.hi) or (
            sb.is_empty and b[0] is not None and b[1] is not None and b[0] >= b[1]
        ),
    )
    if status == "missing":
        out.append(("error", (
            f"{sb.table}에 기간 「{label}」 조건이 없습니다 — {where}를 넣으세요."
        )))
    elif status == "mismatch":
        hint = (
            f" stat_date는 {_STAT_FORMAT_LABEL[sb.grain]} 문자열입니다." if bad_format else ""
        )
        out.append(("error", (
            f"기간 조건이 시스템이 해석한 기간 「{label}」과 다릅니다 — SQL: "
            f"{' / '.join(texts[:3])} · 기대: {where}. 기대 조건을 그대로 쓰세요.{hint}"
        )))
    elif status == "or" and not funcs:
        out.append(("error", _or_error(f"기간 「{label}」", where, texts)))
    elif status == "unparsable" and not funcs:
        out.append(_unparsable_finding(sql, f"기간 「{label}」", where, texts))
    return out


def _alarm_findings(
    sql: str, event: TimeResolution, bounds: tuple[str | None, str], used: dict[str, set[str]],
    *, present: bool = False,
) -> list[tuple[str, str]]:
    # 원문에 기간과 「현재·지금」이 함께 있고(「지난달 서버 CPU와 현재 활성 알람 수」) SQL이 활성
    # 알람 스냅샷만 쓰면, 그 조회는 기간이 아니라 「현재」 몫으로 본다 — 통계의 「현재」 완화와 같은
    # 규칙(리뷰 M-1 · 거짓 반려 방지). 알람 이력(`cmm_alarm`)을 쓰면 그대로 엄격하다.
    if present and set(used) == {"cmm_alarm_active"}:
        return [
            ("warn", f"「현재」 표현 — 활성 알람을 현재 스냅샷 조회로 보고 대조만 남깁니다: {m}")
            for _kind, m in _alarm_findings(sql, event, bounds, used)
        ]
    label = event.label()
    start, end = bounds
    tables = set(used)
    quals = set().union(*used.values())
    col = f"{_pick_alias(quals, tables)}.{_ALARM_TIME_COLUMN}"
    where = (f"{col} >= TIMESTAMP '{start}' AND " if start else "") + (
        f"{col} < TIMESTAMP '{end}'"
    )
    exp_lo = datetime.strptime(start, TS_FORMAT) if start else None
    exp_hi = datetime.strptime(end, TS_FORMAT)
    to_now = NOTE_EVENT_TO_NOW in event.notes

    def equal(b: tuple[datetime | None, datetime | None]) -> bool:
        lo, hi = b
        lo_ok = (lo is None and exp_lo is None) or (
            lo is not None and exp_lo is not None and abs(lo - exp_lo) <= _TS_TOLERANCE
        )
        # 진행 중 기간(기준 시각까지 · D-291)은 끝을 생략하거나 미래로 둬도 같은 행이다
        hi_ok = (hi is not None and abs(hi - exp_hi) <= _TS_TOLERANCE) or (
            to_now and (hi is None or hi >= exp_hi)
        )
        return lo_ok and hi_ok

    groups = _condition_groups(extract_column_conditions(sql, _ALARM_TIME_COLUMN) or [], quals)
    out: list[tuple[str, str]] = []
    funcs = _now_note(groups)
    if funcs:
        out.append(("error", _now_function_error(f"알람 기간 「{label}」", where, funcs)))
    status, texts, _bad = _judge_groups(groups, key=_parse_ts, next_cell=_same_instant, equal=equal)
    if status == "missing":
        out.append(("error", f"알람 기간 「{label}」 조건이 없습니다 — {where}를 넣으세요."))
    elif status == "mismatch":
        out.append(("error", (
            f"알람 기간 조건이 시스템이 해석한 기간 「{label}」과 다릅니다 — SQL: "
            f"{' / '.join(texts[:3])} · 기대: {where}. 기대 조건을 그대로 쓰세요."
        )))
    elif status == "or" and not funcs:
        out.append(("error", _or_error(f"알람 기간 「{label}」", where, texts)))
    elif status == "unparsable" and not funcs:
        out.append(_unparsable_finding(sql, f"알람 기간 「{label}」", where, texts))
    return out


def check_time_conditions(sql: str, qt: QueryTime) -> list[str]:
    """생성 SQL의 시간 조건을 요청 시간 해석과 대조한다(plans/122 T-5b · D-309).

    대상은 SQL이 참조하는 통계 테이블(`time_period.STAT_TABLES` — `stat_date`)과 알람 테이블
    (`cmm_alarm`·`cmm_alarm_active` — `ctime`)이다. 사용자가 기간을 말했고(`qt.explicit`) 비교
    질의(`multiple_periods`)가 아니면 다음을 **오류**로 돌려 재생성 사유로 쓴다.

    ① 기간 조건에 DB 현재시각 함수(`CURRENT_DATE`·`CURRENT DATE`·`NOW()`·`INTERVAL` 등)
    ② 통계 테이블 입도가 `stat_bounds(qt.metric).table`과 다름(D-201 「이번 달」 stat_m 반려 포함)
    ③ 통계 테이블의 `stat_date` 조건 누락·경계 불일치(=·BETWEEN·>=/< 를 입도 칸 집합으로
       정규화해 비교 — 같은 집합이면 통과)
    ④ 알람 `ctime` 조건이 `alarm_ts_bounds(qt.event)`와 다르거나 없음(사건 기간 조건 없음이면
       요구하지 않는다 · TIMESTAMP 리터럴·문자열 리터럴 모두 인정)

    기간 미지정 기본값(`uses_default_period`)·비교 질의는 같은 대조를 **경고 로그로만** 남기고,
    「현재·지금」 질의(기본값을 강제하지 않는다)는 대조하지 않는다. 파싱 불가 조건도 경고만이다.
    기간과 「현재·지금」이 함께 있는 질의에서 SQL이 시간 통계만 쓰면 통계 대조(①~③)는, 알람 활성
    스냅샷(`cmm_alarm_active`)만 쓰면 알람 대조(④)는 경고만 남긴다(「현재」 값 조회로 본다 ·
    리뷰 M-1). 시간 조건이 OR로 묶이면 읽지 못한 경우라도 오류다(다른 기간이 섞인다 · 리뷰 m-5).

    Args:
        sql: 검사할 SQL
        qt: 요청 시간 해석(`QueryTime.from_state(state["time_resolution"])`)

    Returns:
        오류 메시지 목록(재생성 프롬프트에 그대로 들어간다 — 기대 테이블·조건 리터럴을 싣는다)
    """
    # 대조 대상 판정은 `QueryTime.metric_for_sql` 한 곳(리뷰 m-2) — 「현재·지금」(기간 없음)·되묻기
    # 대상이면 None이라 대조하지 않는다(기본값을 강제하지 않는다 — 실시간·최근 시간 경로).
    metric, event = qt.metric_for_sql, qt.event
    if metric is None or event is None:
        return []
    strict = qt.explicit and NOTE_MULTIPLE_PERIODS not in metric.notes
    mode = "explicit" if strict else ("multiple_periods" if qt.explicit else "default_period")

    quals = table_qualifiers(sql, [*STAT_TABLES.values(), *_ALARM_TIME_TABLES])
    found: list[tuple[str, str]] = []
    if quals is None:
        found.append(("warn", "SQL 구조(괄호·따옴표)를 읽지 못해 시간 조건을 대조하지 않았습니다."))
    else:
        stat_used = {t: q for t, q in quals.items() if t in _STAT_GRAIN_OF_TABLE}
        sb = stat_bounds(metric)
        if stat_used and sb is not None:
            found.extend(_stat_findings(sql, metric, sb, stat_used, present=qt.present))
        alarm_used = {t: q for t, q in quals.items() if t in _ALARM_TIME_TABLES}
        ab = alarm_ts_bounds(event)
        if alarm_used and ab is not None:
            found.extend(_alarm_findings(sql, event, ab, alarm_used, present=qt.present))

    errors = [msg for kind, msg in found if kind == "error"]
    for kind, msg in found:
        if kind == "warn" or not strict:
            logger.warning("시간 조건 검증 경고(plans/122 T-5b · %s): %s", mode, msg)
    return errors if strict else []
