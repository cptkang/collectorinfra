"""폴스타 다중 리소스 피벗 SQL 결정적 조립기 (Plan 63 P2, D-089).

query_gen_common.py에서 분리 이동한 폴스타 EAV/피벗 특화 조립 로직(동작 불변, D-068 계열).
공용 코어는 어댑터 모듈을 직접 임포트한다(application→application). 호출부: query_generator·
multi_db_executor·semantic_compiler(모두 application).
"""

from __future__ import annotations

# 기간 범위/값 타당성 게이트는 공용 코어(utils)에서 가져온다(application→config/utils 허용).
from src.utils.sql_dialect import is_db2, row_limit_clause
from src.utils.sql_dialect import sql_literal as _sql_literal  # 이동(Plan 69 P2) — 동작 불변
from src.utils.query_gen_common import (
    StatMonth,
    normalize_stat_month as _normalize_stat_month,
    utilization_guard as _utilization_guard,
)
# EAV 속성 메타 추출은 카탈로그 계층에 위임한다(application→infrastructure 허용).
from src.schema_cache.catalog_builder import attribute_resource_types


def decimal_cast_example(db_engine: str | None) -> str:
    """엔진별 '소수 보존 사용률 집계' 예시 SQL 스니펫을 반환한다(미매핑 alias 안내용).

    PostgreSQL은 `AVG(...)::numeric`으로 소수를 보존하지만, DB2는 `AVG()`가 정수 컬럼을 정수로
    집계하므로 **집계 전** 캐스트가 필요하다(`::numeric`은 DB2 문법 오류). 캐스트는 DOUBLE —
    고정 정밀도 DECIMAL(15,4)는 범위 밖 쓰레기 값(실측 5.5e13)에서 SQL0413N 변환 오버플로로
    쿼리 전체가 죽는다(D-103). 값 타당성 게이트(BETWEEN)도 예시에 포함해 LLM 경로도 오염을 거른다.
    """
    guard = _utilization_guard("avg_val", "Utilization")
    if is_db2(db_engine):
        return (
            "CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
            f"AND s.definition_name = 'Utilization'{guard} "
            'THEN CAST(s.avg_val AS DOUBLE) END), 2) AS DECIMAL(31,2)) AS "CPU 평균"'
        )
    return (
        "ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
        f"AND s.definition_name = 'Utilization'{guard} "
        'THEN s.avg_val END)::numeric, 2) AS "CPU 평균"'
    )


_SERVER_RESOURCE_TYPE = "server.Server"
# 통계 기간 컬럼(YYYYMM/YYYYMMDD 문자열) — 기간 필터와 시계열 행 분해가 같은 컬럼을 쓴다.
_STAT_COLUMN = "stat_date"
# 시계열 행 분해에서 식별 컬럼을 가져오는 부모 서버 조인 alias.
_PARENT_ALIAS = "svr"

#: 월별 통계 테이블 기본값 — 진입 함수 2개와 조립 코어가 공유한다(기본값 드리프트 차단).
_DEFAULT_METRIC_TABLE = "cmm_metric_stat_m"

#: 미매핑 필드 안내(`build_unmapped_fields_block`)에 실을 사용률 피벗 지시 재료.
#: 공용 계층(nodes)이 이 스키마 리터럴을 직접 들고 있지 않도록 어댑터가 제공한다 —
#: `decimal_cast_example`과 같은 성격의 프롬프트 문구 재료다(D-088/D-089).
METRIC_PIVOT_TABLE = _DEFAULT_METRIC_TABLE
METRIC_PIVOT_KEYS = "resource_type + definition_name='Utilization', avg_val/max_val"



# 사용률 통계(metric) 필드 분류 — 명사→resource_type, 집계어→(집계함수, 값컬럼).
# 폴스타 resource_type(server.*) 리터럴을 담으므로 어댑터 계층에 둔다(공용 계층 과적합 가드
# D-088 준수 — 문서 계층은 스키마-무관 `is_metric_field_name`을 쓴다, 2026-07-22 머지 정리).
_METRIC_NOUN_RT: tuple[tuple[str, str], ...] = (
    ("cpu", "server.Cpus"),
    ("메모리", "server.Memory"),
    ("mem", "server.Memory"),
    ("디스크", "server.Disks"),
    ("disk", "server.Disks"),
)
_METRIC_AGG: tuple[tuple[str, str, str], ...] = (
    ("평균", "AVG", "avg_val"),
    ("최고", "MAX", "max_val"),
    ("최대", "MAX", "max_val"),
    ("최소", "MIN", "min_val"),
    ("avg", "AVG", "avg_val"),
    ("max", "MAX", "max_val"),
    ("min", "MIN", "min_val"),
)


def classify_metric_field(field: str) -> tuple[str, str, str] | None:
    """사용률 필드를 (resource_type, 집계함수, 값컬럼)으로 분류한다(아니면 None).

    예: "CPU 평균" → ("server.Cpus", "AVG", "avg_val"),
        "메모리 최고" → ("server.Memory", "MAX", "max_val").
    metric 명사와 집계어가 **둘 다** 있어야 metric으로 인정한다('메모리 용량'은 집계어 없어 제외).
    """
    low = (field or "").lower()
    rt = next((r for noun, r in _METRIC_NOUN_RT if noun in low), None)
    if rt is None:
        return None
    agg = next(((fn, col) for term, fn, col in _METRIC_AGG if term in low), None)
    if agg is None:
        return None
    return rt, agg[0], agg[1]


def eav_attr_resource_types(schema_info: dict | None) -> dict[str, str]:
    """EAV 속성의 `속성명(대문자) → resource_type` 맵을 구조 정본에서 얻는다.

    CPU 코어 수·메모리 용량 같은 자식 리소스 속성은 server.Server 행이 아니라 자식 행
    (platform_resource_id로 연결)에 있으므로, 강제 SELECT 블록이 올바른 resource_type 구분
    피벗을 생성하도록 이 맵을 사용한다(예: LOGICALCORE→server.Cpus, TotalSize→server.Memory).

    추출은 카탈로그 계층(`schema_cache.catalog_builder`)에 위임한다 — 프로필의 구조화 키
    `resource_type`을 읽고, 미이관 프로필·구캐시에서만 description의 `[resource_type: X]`
    표기를 폴백 파싱한다(Plan 67 R1-4: 주석 파싱 → 구조화 필드).

    Args:
        schema_info: `_structure_meta`를 포함할 수 있는 스키마 정보 딕셔너리

    Returns:
        {속성명 대문자: resource_type} 맵. 정보가 없으면 빈 딕셔너리.
    """
    if not schema_info:
        return {}
    return attribute_resource_types(schema_info.get("_structure_meta"))

def _metric_select_line(
    field: str,
    rt: str,
    agg_fn: str,
    val_col: str,
    db_engine: str | None,
    definition_name: str = "Utilization",
) -> str:
    """단일 사용률/지표 필드의 SELECT 라인(엔진별 소수 보존 캐스트 포함).

    definition_name 기본값은 'Utilization'(사용률)이며, 폼필 경로는 이 값만 쓴다. 시맨틱
    컴파일러(트랙 C 패턴 B)는 'MaxIORate'(디스크 IO) 등 다른 지표도 지정할 수 있어 인자로 노출한다.
    """
    return f'  {_metric_agg_expr(rt, agg_fn, val_col, db_engine, definition_name)} AS "{field}"'


def _metric_agg_expr(
    rt: str,
    agg_fn: str,
    val_col: str,
    db_engine: str | None,
    definition_name: str = "Utilization",
) -> str:
    """단일 지표의 집계 표현식(alias 없음)을 만든다 — SELECT와 HAVING이 같은 식을 공유한다.

    HAVING은 SELECT alias를 참조할 수 없어(PostgreSQL·DB2 공통) 임계 조건도 같은 집계식을
    다시 써야 한다(Plan 67 S-IR4 측정치 임계). 두 곳이 어긋나면 임계가 다른 값에 걸리므로
    표현식 조립은 이 함수 하나로 일원화한다.

    Utilization에는 값 타당성 게이트(BETWEEN 0 AND 1000)를 CASE 조건에 넣어, 범위 밖 쓰레기
    행(실측 avg=1.2e9/max=5.5e13, 음수)을 필드 단위로 집계에서 제외한다(D-103). 게이트는
    definition_name='Utilization'일 때만 — MaxIORate 등엔 0~1000 의미가 없다.
    """
    guard = _utilization_guard(val_col, definition_name)
    if is_db2(db_engine):
        # DB2: 집계 함수 내부에서 캐스트(정수 truncate 방지). ::numeric은 문법 오류.
        # 캐스트는 DOUBLE — 고정 정밀도 DECIMAL(15,4)는 범위 밖 값(실측 5.5e13 ≥ 1e11)에서
        # SQL0413N 변환 오버플로로 쿼리 전체가 죽는다(D-103; DOUBLE은 ~1e308이라 변환 오버플로
        # 원리적 불가 — 게이트 없는 지표(MaxIORate)까지 덮는 심층 방어).
        # 또한 DB2 집계는 스케일을 크게 확장(예: scale 18)하여 ROUND(x,2)로 값은 2자리로
        # 반올림돼도 **타입 스케일이 남아** 결과가 6.51000000000000000000처럼 trailing zero로 직렬화된다
        # (엑셀 제로필). 최종을 `CAST(... AS DECIMAL(31,2))`로 감싸 스케일을 2로 고정한다(D-068 후속;
        # 정밀도는 15→31로 확장해 대형 정상값(IO rate 등)의 최종 캐스트 오버플로 여지 제거 — D-103).
        inner = f"CAST(s.{val_col} AS DOUBLE)"
        return (
            f"CAST(ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
            f"AND s.definition_name='{definition_name}'{guard} THEN {inner} END), 2) AS DECIMAL(31,2))"
        )
    return (
        f"ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
        f"AND s.definition_name='{definition_name}'{guard} THEN s.{val_col} END)::numeric, 2)"
    )


def _pivot_select_parts(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    metric_fields: list[str] | None,
    attr_col: str,
    val_col: str,
    db_engine: str | None,
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
    *,
    parent_alias: str | None = None,
) -> tuple[list[str], set[str], bool]:
    """피벗 SELECT 라인 목록·필요 resource_type 집합·metric 유무를 계산한다(블록/SQL 공용).

    metric_fields는 폼필 경로가 쓰는 한글 라벨(예: "CPU 평균")로, `classify_metric_field`로
    (resource_type, 집계함수, 값컬럼)을 추론한다. explicit_measures는 시맨틱 컴파일러(트랙 C)가
    쓰는 명시 지정으로, 라벨 분류에 의존하지 않고 (alias, resource_type, agg_fn, val_col,
    definition_name)을 직접 전달한다(MaxIORate 등 Utilization 외 지표 지원). 둘 다 주면 합쳐 넣는다.

    parent_alias가 주어지면 엔티티 직접 컬럼을 `MAX(<alias>.<컬럼>)`로 뽑는다 — 시계열 행
    분해(Plan 67 S-IR2)는 GROUP BY에 통계 기간이 들어가 서버 행(server.Server)과 통계 행이
    다른 그룹으로 갈리므로, 식별 컬럼을 부모 서버 조인에서 가져와야 NULL이 되지 않는다.
    """
    lines: list[str] = []
    rtset: set[str] = {_SERVER_RESOURCE_TYPE}
    for field, col in regular_entries:
        bare = col.split(".")[-1]
        if parent_alias:
            lines.append(f'  MAX({parent_alias}.{bare}) AS "{field}"')
            continue
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f'THEN c.{bare} END) AS "{field}"'
        )
    for field, attr in server_eav:
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f"AND cc.{attr_col}='{attr}' THEN cc.{val_col} END) AS \"{field}\""
        )
    for field, attr, rt in child_eav:
        rtset.add(rt)
        lines.append(
            f"  MAX(CASE WHEN c.resource_type='{rt}' "
            f"AND cc.{attr_col}='{attr}' THEN cc.{val_col} END) AS \"{field}\""
        )
    has_metric = False
    for field in metric_fields or []:
        cls = classify_metric_field(field)
        if not cls:
            continue
        rt, agg_fn, mval = cls
        rtset.add(rt)
        has_metric = True
        lines.append(_metric_select_line(field, rt, agg_fn, mval, db_engine))
    for alias, rt, agg_fn, mval, defn in explicit_measures or []:
        rtset.add(rt)
        has_metric = True
        lines.append(_metric_select_line(alias, rt, agg_fn, mval, db_engine, defn))
    return lines, rtset, has_metric


def _eav_pattern_parts(eav_pattern: dict) -> tuple[str, str, str, str, str, str]:
    """eav_pattern에서 (entity, config, attr_col, val_col, ent_join, cfg_join)을 뽑는다."""
    entity = eav_pattern.get("entity_table", "cmm_resource")
    config = eav_pattern.get("config_table", "core_config_prop")
    attr_col = eav_pattern.get("attribute_column", "name")
    val_col = eav_pattern.get("value_column", "stringvalue_short")
    direct_join = eav_pattern.get("direct_join", {}) or {}
    ent_join = direct_join.get("entity_column", "resource_conf_id")
    cfg_join = direct_join.get("config_column", "configuration_id")
    return entity, config, attr_col, val_col, ent_join, cfg_join


def _build_pivot_sql(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    db_schema: str | None = None,
    limit: int | None = None,
    stat_month: StatMonth = None,
    metric_table: str = _DEFAULT_METRIC_TABLE,
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
    server_scope: tuple[str, list[str]] | None = None,
    order_by: tuple[str, str] | None = None,
    time_breakdown: bool = False,
    global_aggregate: bool = False,
    entity_count_alias: str | None = None,
    direct_having: list[tuple[str, str, object]] | None = None,
    measure_having: list[tuple[str, str, object]] | None = None,
) -> str:
    """폼필/시맨틱 다중 리소스 피벗을 **runnable SQL로 결정적 조립**하는 공유 코어다.

    두 경로가 쓰는 파라미터의 합집합을 받는 **private 코어**로, 호출은 경로별 진입 함수
    (``build_form_fill_pivot_sql``·``build_semantic_pivot_sql``)를 통한다 — 경로마다 무의미한
    파라미터가 시그니처에 섞이는 것을 막으면서 조립 엔진은 하나로 유지한다(D-067 단일 출처).

    프롬프트로 스켈레톤을 "제안"하면 LLM이 프로필 few-shot 예시(월별 GROUP BY 등)와 경쟁해
    무시·변형(서버 중복·config 누락)한다. 이 well-defined 쿼리는 코드가 직접 조립하여 LLM
    변동성을 제거한다. 조인 패턴은 프로필의 검증된 예시와 동일하되, 사용률까지 **단일 GROUP BY
    스코프**에 합친다(config·metric 이중 조인은 집계값에 불변). 시맨틱 컴파일러(트랙 C, D-076)가
    이 함수를 패턴 A(서버설정)+B(성능지표) 조립 엔진으로 **재사용**한다(이중 조립 엔진 금지 — D-067).

    Args:
        regular_entries/server_eav/child_eav/eav_pattern/metric_fields/db_engine: 피벗 구성요소
        db_schema: 스키마 한정자(polestar 등, DB2는 대문자 POLESTAR — D-057). 비면 무한정.
        limit: 결과 상한(엔진별 LIMIT/FETCH FIRST). None이면 미적용.
        stat_month: 사용률 기간 필터 — 단일 월 YYYYMM(예: '202506') 또는 (시작, 끝) 범위
            (예: ('202504', '202506') → BETWEEN, D-102). None이면 전체 월 평균.
        metric_table: 월별 통계 테이블명(폴스타 기본 cmm_metric_stat_m).
        explicit_measures: 시맨틱 컴파일러용 명시 measure (alias, resource_type, agg_fn,
            val_col, definition_name). metric_fields의 한글라벨 분류 대신 직접 지정(패턴 B).
        server_scope: 선행 결과 서버 한정 (식별컬럼, 값목록) — HAVING의 집계 CASE WHEN으로
            적용한다(WHERE에 두면 자식 리소스 행이 탈락해 0건 — D-096). None이면 미적용.
        order_by: 순위 정렬 (SELECT alias, "DESC"|"ASC"). NULL이 1위를 차지하지 않도록
            NULLS LAST를 항상 부여한다(D-098 — PostgreSQL DESC 기본은 NULLS FIRST).
        time_breakdown: 통계 기간(월/일)별 행 분해(Plan 67 S-IR2). 통계 기간 컬럼을 SELECT·
            GROUP BY에 추가하고, 식별 컬럼은 부모 서버 조인에서 가져온다.
        global_aggregate: 전역 단일 행 집계(Plan 67 S-IR1) — GROUP BY를 생략한다. EAV 속성이
            없으면 config 조인도 빼는데, 전역 집계에서는 config 행 증식 배수가 서버마다 달라
            가중 평균이 왜곡되기 때문이다(서버별 GROUP BY에서는 배수가 그룹 내 상수라 불변).
        entity_count_alias: 엔티티(서버) 수 집계 컬럼 alias. 주면 COUNT(DISTINCT 그룹키)를 SELECT에
            추가한다(Plan 67 S-IR1).
        direct_having: 엔티티 직접 컬럼 조건 [(컬럼, SQL 연산자, 값)] — 서버 식별 필터를 집계 후
            HAVING으로 적용한다(WHERE는 자식 행을 탈락시킴 — D-096).
        measure_having: 측정치 임계 조건 [(measure alias, SQL 연산자, 값)] — SELECT와 동일한
            집계식을 HAVING에 재사용한다(Plan 67 S-IR4).

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결).
    """
    entity, config, attr_col, val_col, ent_join, cfg_join = _eav_pattern_parts(eav_pattern)
    lines, rtset, has_metric = _pivot_select_parts(
        regular_entries, server_eav, child_eav, metric_fields, attr_col, val_col, db_engine,
        explicit_measures=explicit_measures,
        parent_alias=_PARENT_ALIAS if time_breakdown else None,
    )
    if time_breakdown:
        # 기간 컬럼은 dimension 뒤·measure 앞(시계열 표의 통상 배치).
        lines.insert(
            len(regular_entries) + len(server_eav) + len(child_eav),
            f'  s.{_STAT_COLUMN} AS "{_STAT_COLUMN}"',
        )
    if entity_count_alias:
        lines.append(
            f"  COUNT(DISTINCT COALESCE(c.platform_resource_id, c.id)) "
            f'AS "{entity_count_alias}"'
        )

    def q(table: str) -> str:
        return f"{db_schema}.{table}" if db_schema else table

    metric_join = ""
    if has_metric:
        month_rng = _normalize_stat_month(stat_month)
        if not month_rng:
            month_cond = ""
        elif month_rng[0] == month_rng[1]:
            month_cond = f" AND s.stat_date = '{month_rng[0]}'"
        else:
            month_cond = f" AND s.stat_date BETWEEN '{month_rng[0]}' AND '{month_rng[1]}'"
        # 폼필 경로(metric_fields)는 Utilization만 쓰므로 단일 동등 필터를 유지(기존 출력 보존).
        # 시맨틱 패턴 B가 여러 definition_name(Utilization+MaxIORate)을 쓰면 IN 필터로 확장한다.
        defs = sorted({m[4] for m in (explicit_measures or [])}) or ["Utilization"]
        if len(defs) == 1:
            def_cond = f"s.definition_name = '{defs[0]}'"
        else:
            def_cond = "s.definition_name IN (" + ", ".join(f"'{d}'" for d in defs) + ")"
        metric_join = (
            f"\nLEFT JOIN {q(metric_table)} s ON s.resource_id = c.id "
            f"AND {def_cond}{month_cond}"
        )

    rt_in = ", ".join(f"'{r}'" for r in sorted(rtset))
    select_block = ",\n".join(lines)
    config_join = f"\nLEFT JOIN {q(config)} cc ON cc.{cfg_join} = c.{ent_join}"
    if (global_aggregate or time_breakdown) and not (server_eav or child_eav):
        # EAV 속성을 안 뽑는 전역/시계열 집계에서는 config 조인이 행만 증식시킨다 — 증식 배수가
        # 그룹마다 달라 평균을 왜곡하므로 조인을 빼는 것이 정확하다.
        config_join = ""
    parent_join = ""
    if time_breakdown:
        parent_join = (
            f"\nLEFT JOIN {q(entity)} {_PARENT_ALIAS} "
            f"ON {_PARENT_ALIAS}.id = COALESCE(c.platform_resource_id, c.id)"
            f" AND {_PARENT_ALIAS}.resource_type = '{_SERVER_RESOURCE_TYPE}'"
            f" AND {_PARENT_ALIAS}.dtime IS NULL"
        )
    sql = (
        "SELECT\n"
        f"{select_block}\n"
        f"FROM {q(entity)} c"
        f"{config_join}"
        f"{metric_join}"
        f"{parent_join}\n"
        f"WHERE c.resource_type IN ({rt_in})\n"
        "  AND c.dtime IS NULL"
    )
    if time_breakdown and has_metric:
        # 통계가 없는 서버 행(server.Server)은 기간이 NULL인 잉여 그룹을 만든다 — 시계열에서 제외.
        sql += f"\n  AND s.{_STAT_COLUMN} IS NOT NULL"
    if not global_aggregate:
        sql += "\nGROUP BY COALESCE(c.platform_resource_id, c.id)"
        if time_breakdown:
            sql += f", s.{_STAT_COLUMN}"
    having_parts: list[str] = []
    if server_scope:
        scope_col, scope_values = server_scope
        if scope_values:
            quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in scope_values)
            having_parts.append(
                f"MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
                f"THEN c.{scope_col} END) IN ({quoted})"
            )
    for col, op, value in direct_having or []:
        having_parts.append(
            f"MAX(CASE WHEN c.resource_type='{_SERVER_RESOURCE_TYPE}' "
            f"THEN c.{col} END) {op} {_sql_literal(value)}"
        )
    measure_exprs = {
        m[0]: _metric_agg_expr(m[1], m[2], m[3], db_engine, m[4])
        for m in (explicit_measures or [])
    }
    for alias, op, value in measure_having or []:
        expr = measure_exprs.get(alias)
        if expr:
            having_parts.append(f"{expr} {op} {_sql_literal(value)}")
    if having_parts:
        sql += "\nHAVING " + "\n  AND ".join(having_parts)
    if order_by:
        alias, direction = order_by
        dir_kw = "DESC" if str(direction).upper() != "ASC" else "ASC"
        # NULLS LAST 필수: 값이 없는 서버가 정렬 선두를 차지해 임의 서버가 1위로 뽑히는 것을 방지(D-098).
        sql += f'\nORDER BY "{alias}" {dir_kw} NULLS LAST'
    if limit:
        sql += "\n" + row_limit_clause(db_engine, limit)
    return sql + ";"


def build_form_fill_pivot_sql(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    *,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    db_schema: str | None = None,
    limit: int | None = None,
    stat_month: StatMonth = None,
    metric_table: str = _DEFAULT_METRIC_TABLE,
) -> str:
    """폼필(양식 채우기) 경로의 다중 리소스 피벗 SQL을 조립한다.

    측정치는 양식 헤더의 한글 라벨(``metric_fields``)을 ``classify_metric_field``로 분류해
    도출한다 — 시맨틱 경로의 명시 measure·정렬·HAVING 계열 파라미터는 이 경로에 해당하지
    않으므로 시그니처에서 뺐다.

    Args:
        regular_entries/server_eav/child_eav/eav_pattern: 피벗 구성요소
        metric_fields: 사용률 지표로 분류된 양식 헤더 라벨 목록
        db_engine/db_schema/limit/stat_month/metric_table: ``_build_pivot_sql``과 동일

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결).
    """
    return _build_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        metric_fields=metric_fields,
        db_engine=db_engine,
        db_schema=db_schema,
        limit=limit,
        stat_month=stat_month,
        metric_table=metric_table,
    )


def build_semantic_pivot_sql(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    *,
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
    db_engine: str | None = None,
    db_schema: str | None = None,
    limit: int | None = None,
    stat_month: StatMonth = None,
    metric_table: str = _DEFAULT_METRIC_TABLE,
    server_scope: tuple[str, list[str]] | None = None,
    order_by: tuple[str, str] | None = None,
    time_breakdown: bool = False,
    global_aggregate: bool = False,
    entity_count_alias: str | None = None,
    direct_having: list[tuple[str, str, object]] | None = None,
    measure_having: list[tuple[str, str, object]] | None = None,
) -> str:
    """시맨틱 컴파일러(트랙 C, D-076) 경로의 다중 리소스 피벗 SQL을 조립한다.

    측정치는 시맨틱 모델이 검증한 명시 measure(``explicit_measures``)로 받는다 — 한글 라벨
    분류(``metric_fields``)는 이 경로에 해당하지 않으므로 시그니처에서 뺐다. 정렬·상한·형태
    확장(S-IR1~5)과 HAVING 계열은 이 경로 전용이다.

    Args:
        regular_entries/server_eav/child_eav/eav_pattern: 피벗 구성요소
        explicit_measures: (alias, resource_type, agg_fn, val_col, definition_name) 목록
        나머지: ``_build_pivot_sql``과 동일

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결).
    """
    return _build_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        db_engine=db_engine,
        db_schema=db_schema,
        limit=limit,
        stat_month=stat_month,
        metric_table=metric_table,
        explicit_measures=explicit_measures,
        server_scope=server_scope,
        order_by=order_by,
        time_breakdown=time_breakdown,
        global_aggregate=global_aggregate,
        entity_count_alias=entity_count_alias,
        direct_having=direct_having,
        measure_having=measure_having,
    )




def build_multi_resource_pivot_block(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    metric_table: str = _DEFAULT_METRIC_TABLE,
) -> str:
    """서버 + 자식 리소스(server.Cpus/Memory) 속성 + 사용률 통계를 **한 쿼리**로 피벗하는 결정적 지침.

    LLM 프롬프트용 텍스트 버전(결정적 SQL 조립이 불가한 경로의 폴백). 실제 폼필 멀티 경로는
    `build_form_fill_pivot_sql`로 SQL을 직접 조립한다(D-068 2차). 자식 리소스 속성이 하나라도
    있을 때만 호출한다.
    """
    entity, config, attr_col, val_col, ent_join, cfg_join = _eav_pattern_parts(eav_pattern)
    lines, rtset, has_metric = _pivot_select_parts(
        regular_entries, server_eav, child_eav, metric_fields, attr_col, val_col, db_engine
    )
    rt_in = ", ".join(f"'{r}'" for r in sorted(rtset))
    select_block = ",\n".join(lines)

    metric_join = ""
    metric_note = ""
    if has_metric:
        metric_join = (
            f"\nLEFT JOIN {metric_table} s ON s.resource_id = c.id "
            "AND s.definition_name = 'Utilization'"
        )
        metric_note = (
            "\n- 사용률(CPU/메모리 평균·최고)은 위 `s` 조인으로 같은 GROUP BY에서 집계했습니다. "
            "기간 조건(예: '지난달 1개월', '지난 3개월')은 **s.stat_date**(YYYYMM 문자열)에 적용하세요"
            "(단일 월: AND s.stat_date = '지난달YYYYMM' / N개월: AND s.stat_date BETWEEN '시작YYYYMM' "
            f"AND '직전월YYYYMM' — 진행 중인 달 제외). 통계 테이블은 반드시 월별 {metric_table}만 "
            "사용하고 _h/_d는 쓰지 마세요."
        )

    return (
        "## 서버 종합 정보 + 사용률 통합 피벗 (반드시 이 하나의 쿼리 형식 그대로)\n"
        "양식의 서버/CPU/메모리/OS 속성은 한 서버 안에서 **여러 resource_type 행**"
        "(server.Server, server.Cpus, server.Memory 등)에 분산돼 있고, 사용률 통계도 자식 리소스에 "
        f"붙습니다. 같은 서버의 자식 리소스는 {entity}.platform_resource_id로 묶입니다. 서버 행에만 "
        "조인하면 CPU 코어 수·메모리 용량·사용률이 전부 NULL이 되므로, 반드시 아래처럼 resource_type "
        "구분 CASE WHEN 피벗 + 단일 GROUP BY로 **하나의 쿼리**로 작성하세요(별도 블록으로 쪼개지 마세요):\n\n"
        "```sql\n"
        "SELECT\n"
        f"{select_block}\n"
        f"FROM {entity} c\n"
        f"LEFT JOIN {config} cc ON cc.{cfg_join} = c.{ent_join}"
        f"{metric_join}\n"
        f"WHERE c.resource_type IN ({rt_in})\n"
        f"GROUP BY COALESCE(c.platform_resource_id, c.id)\n"
        "```\n"
        "- 결과 alias는 반드시 위 양식 필드명(한글, 따옴표 포함) 그대로 — 임의 영문 alias 금지.\n"
        "- 모든 비집계 컬럼은 위처럼 MAX(CASE ...)로 감싸세요. **집계 밖의 맨 컬럼(r.name 등) 금지** "
        "— GROUP BY 위반이 됩니다.\n"
        "- WHERE에 c.name='...' 등 서버 필터를 직접 두지 마세요(자식 행이 GROUP BY 전에 걸러져 "
        "NULL). 특정 서버 한정은 HAVING을 사용하세요.\n"
        f"- {val_col}를 다른 서버 행 config에 브릿지 조인하지 마세요."
        f"{metric_note}"
    )
