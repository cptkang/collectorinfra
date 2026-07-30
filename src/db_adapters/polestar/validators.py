"""폴스타 전용 SQL 검증 (Plan 63 P2, D-089).

query_validator.py의 `_check_routing_filter_misuse`를 분리 이동한 것(동작 불변). 공용
validator는 어댑터 `validator_checks()` 훅을 순회 실행하며, 담당 DB(폴스타)에서만 발동한다
(기존엔 전 DB에서 실행됐으나 폴스타 토큰 부재 시 무동작이라 동작 불변 — Plan 63 §1.1 L4).
"""

from __future__ import annotations

import re

import sqlparse


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
