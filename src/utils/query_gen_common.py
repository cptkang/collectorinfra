"""query_generator / multi_db_executor 공통 SQL 생성 헬퍼 (D-066).

단일 DB 경로(`query_generator` 노드)와 멀티 DB 경로(`multi_db_executor` 노드)가
갈라지면서 (1) few-shot 쿼리 예시 주입과 (2) "전체/모든" 조회 LIMIT 상향이 단일
경로에만 존재해, 멀티 DB 폼필(공동존=gp+yd 등)이 예시 없이·LIMIT 1000으로 열화됐다.
두 로직을 단일 출처로 공유하여 경로 간 SQL 품질 비대칭을 제거한다.
"""

from __future__ import annotations

import re
from datetime import date

_PREV_MONTH_SIGNALS: tuple[str, ...] = (
    "지난달", "지난 달", "전월", "저번달", "저번 달", "지난1개월", "지난 1개월",
    "지난달 1개월", "last month", "previous month",
)
_CUR_MONTH_SIGNALS: tuple[str, ...] = ("이번달", "이번 달", "당월", "금월", "this month", "current month")


def resolve_stat_month(user_query: str | None, today: date | None = None) -> str | None:
    """질의의 기간 표현을 사용률 통계 월(YYYYMM 문자열)로 해석한다(없으면 None).

    "지난달"/"전월"/"지난 1개월" → 직전 월, "이번달"/"당월" → 당월. 그 외에는 None(전체 월 평균).
    폼필 SQL을 코드가 결정적으로 조립할 때 `s.stat_date` 필터에 사용한다.
    """
    text = user_query or ""
    ref = today or date.today()
    if any(sig in text for sig in _PREV_MONTH_SIGNALS):
        year, month = (ref.year - 1, 12) if ref.month == 1 else (ref.year, ref.month - 1)
        return f"{year}{month:02d}"
    if any(sig in text for sig in _CUR_MONTH_SIGNALS):
        return f"{ref.year}{ref.month:02d}"
    return None

def build_stat_month_block(
    stat_month: str | None, metric_table: str = "cmm_metric_stat_m"
) -> str:
    """질의 기간 표현의 결정적 해석(YYYYMM 단일 월)을 LLM 폴백 프롬프트에 강제하는 블록.

    "지난 1개월/지난달" 질의에서 LLM이 시스템 템플릿의 일반 규칙("하드코딩 날짜 금지 —
    CURRENT_DATE 동적 계산")을 따라 `BETWEEN 직전월 AND 이번달`처럼 진행 중인 달까지 포함하는
    기간을 재계산하고 월별 GROUP BY로 서버를 중복 출력하는 실측 사례가 있었다(D-076 후속4).
    코드가 이미 해석한 월(`resolve_stat_month`)을 등호 필터로 강제해 기간 해석을 결정화한다.
    단일 DB(query_generator)·멀티 DB(multi_db_executor) 폴백 경로가 공유한다(D-066 단일 출처).

    Args:
        stat_month: resolve_stat_month 결과 YYYYMM 문자열 (None이면 기간 표현 없음 → 빈 문자열)
        metric_table: 월별 통계 테이블명

    Returns:
        프롬프트에 덧붙일 섹션 텍스트(선행 개행 없음). stat_month가 없으면 "".
    """
    if not stat_month:
        return ""
    return (
        "## 기간 조건 (시스템이 결정적으로 해석 — 최우선 준수)\n"
        f"질의의 기간 표현은 이미 월 통계 기준으로 해석되었습니다. {metric_table} 조인에 반드시 "
        f"`s.stat_date = '{stat_month}'` (단일 월 등호 필터)를 사용하세요.\n"
        "- 이 지시는 '하드코딩 날짜 금지·CURRENT_DATE 동적 계산' 일반 규칙보다 **우선**합니다"
        "(이 값은 시스템이 계산해 주입한 것으로 하드코딩이 아닙니다).\n"
        "- BETWEEN·INTERVAL 재계산으로 진행 중인 달을 포함하지 마세요.\n"
        "- 시간별(_h)/일별(_d) 테이블로 대체하지 마세요."
    )


# "전체/모든/모두" 조회는 기본 LIMIT(default_limit)로 절단하면 안 되므로 상향한다.
_ALL_QUERY_KEYWORDS: tuple[str, ...] = ("모든", "전체", "모두")
_ALL_QUERY_LIMIT: int = 100_000


def resolve_query_limit(user_query: str | None, default_limit: int) -> int:
    """"전체/모든/모두" 조회면 LIMIT를 상향하고, 아니면 기본값을 반환한다.

    단일 DB 경로(query_generator)와 동일한 규칙을 멀티 DB 경로에도 적용하기 위한 공용 함수.

    Args:
        user_query: 사용자 원문 질의
        default_limit: 일반 조회 기본 LIMIT

    Returns:
        적용할 LIMIT 값
    """
    text = user_query or ""
    if any(k in text for k in _ALL_QUERY_KEYWORDS):
        return _ALL_QUERY_LIMIT
    return default_limit


def decimal_cast_example(db_engine: str | None) -> str:
    """엔진별 '소수 보존 사용률 집계' 예시 SQL 스니펫을 반환한다(미매핑 alias 안내용).

    PostgreSQL은 `AVG(...)::numeric`으로 소수를 보존하지만, DB2는 `AVG()`가 정수 컬럼을 정수로
    집계하므로 **집계 전** `CAST(... AS DECIMAL)`이 필요하다(`::numeric`은 DB2 문법 오류). 미매핑
    필드(사용률)를 한글 헤더로 alias하라는 안내에 엔진에 맞는 예시를 넣어 소수점 소실을 막는다.
    """
    if (db_engine or "").lower() == "db2":
        return (
            "CAST(ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
            "AND s.definition_name = 'Utilization' "
            'THEN CAST(s.avg_val AS DECIMAL(15,4)) END), 2) AS DECIMAL(15,2)) AS "CPU 평균"'
        )
    return (
        "ROUND(AVG(CASE WHEN r.resource_type = 'server.Cpus' "
        "AND s.definition_name = 'Utilization' "
        'THEN s.avg_val END)::numeric, 2) AS "CPU 평균"'
    )


_RESOURCE_TYPE_RE = re.compile(r"\[resource_type:\s*([^\]/\s]+)")
_SERVER_RESOURCE_TYPE = "server.Server"

# 사용률 통계(metric) 필드 분류 — 명사→resource_type, 집계어→(집계함수, 값컬럼).
# 폼필 사용률 필드(CPU 평균/최고, 메모리 평균/최고)는 cmm_metric_stat_m 피벗으로만 얻으며
# field_mapper가 미매핑(None)으로 넘긴다(D-066 후속3). 이를 통합 피벗 스켈레톤에 접어 넣는다.
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
    """EAV known_attributes 설명에서 `속성명(대문자) → resource_type` 맵을 추출한다.

    프로필 known_attributes의 description 끝에 `[resource_type: server.Cpus]` 형식으로
    각 속성이 어느 리소스 행에 붙는지 표기돼 있다(예: LOGICALCORE→server.Cpus,
    TotalSize→server.Memory). CPU 코어 수·메모리 용량 같은 자식 리소스 속성은
    server.Server 행이 아니라 자식 행(platform_resource_id로 연결)에 있으므로,
    강제 SELECT 블록이 올바른 resource_type 구분 피벗을 생성하도록 이 맵을 사용한다.

    TotalSize처럼 설명에 복수 resource_type이 있으면 **첫 번째**만 사용한다
    (양식 '메모리 용량'은 server.Memory가 정답).

    Args:
        schema_info: `_structure_meta`를 포함할 수 있는 스키마 정보 딕셔너리

    Returns:
        {속성명 대문자: resource_type} 맵. 정보가 없으면 빈 딕셔너리.
    """
    out: dict[str, str] = {}
    if not schema_info:
        return out
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return out
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") != "eav":
            continue
        # `_load_manual_profile`은 known_attributes를 문자열 리스트로 평탄화하고 원본 객체
        # (name/description/synonyms)를 known_attributes_detail에 보존한다. resource_type 태그는
        # description에 있으므로 detail(객체 리스트)을 우선 읽는다. detail이 없으면(이미 dict인
        # 원시 구조 메타) known_attributes를 사용한다. 이 폴백이 없으면 실 런타임(수동 프로필 로드)에서
        # attr_rt가 항상 비어 폼필 결정적 피벗이 발동하지 않고 LLM 폴백으로 떨어진다(단일·멀티 공통).
        attrs = pattern.get("known_attributes_detail") or pattern.get("known_attributes", [])
        for attr in attrs:
            if not isinstance(attr, dict):
                continue
            name = (attr.get("name") or "").strip()
            desc = attr.get("description") or ""
            m = _RESOURCE_TYPE_RE.search(desc)
            if name and m:
                out[name.upper()] = m.group(1).strip()
    return out


# 서버 등록명(cmm_resource.name) 의미의 폼 필드 표면어(공백 제거·소문자 정규화 후 비교).
# 프로필이 명시하듯 이 표현들은 EAV Hostname(=호스트명)이 아니라 등록명 컬럼이다.
_SERVER_NAME_TERMS = frozenset({
    "서버명", "서버이름", "장비명", "장비이름", "리소스명", "등록명",
    "폴스타등록명", "장비식별명",
})


def _norm_name_term(text: str) -> str:
    """폼 필드명을 공백 제거+소문자로 정규화한다('서버 이름'→'서버이름')."""
    return "".join((text or "").split()).lower()


def is_servername_field(field: str) -> bool:
    """필드명이 서버 등록명(cmm_resource.name) 의미의 표면어인지 판정한다."""
    return _norm_name_term(field) in _SERVER_NAME_TERMS


def is_hostname_target(column: str) -> bool:
    """매핑 대상 컬럼이 hostname(직접 컬럼 `*.hostname` 또는 EAV Hostname 속성)인지 판정한다.

    - EAV 매핑: `EAV:Hostname`
    - 직접 컬럼: `hostname` / `cmm_resource.hostname` / `polestar.cmm_resource.hostname` /
      `db_id:table.hostname` 등 — 표기·스키마 접두사·db_id 접두사 무관하게 bare 컬럼명으로 판정.
    """
    if not isinstance(column, str) or not column:
        return False
    if column.startswith("EAV:"):
        return column[4:].strip().lower() == "hostname"
    return column.rsplit(".", 1)[-1].strip().lower() == "hostname"


def is_servername_to_hostname(field: str, column: str) -> bool:
    """서버명/서버이름류 필드가 hostname(컬럼 or EAV)으로 (오)매핑되는지 판정한다.

    자동 유사어 등록 차단(재오염 방지)과 폼필 매핑 교정에서 공용으로 쓰는 결정적 판정.
    '호스트네임' 등 호스트명 표면어는 name-term이 아니므로 False.
    """
    return is_servername_field(field) and is_hostname_target(column)


def correct_servername_hostname_mapping(
    column_mapping: dict, entity_table: str
) -> None:
    """서버명/서버이름류 폼 필드가 hostname으로 오매핑되면 등록명 컬럼으로 교정한다(in-place).

    프로필(gp/yd/b0)이 명시적으로 규정하듯 '서버명/서버 이름'은 hostname(호스트명 값)이
    아니라 등록명 컬럼(`<entity>.name`, 예: cmm_resource.name)이다. 그러나 전역/EAV 유사어의
    `Hostname: [..., 서버명, ...]` 미끼 + 유사어/LLM 매핑의 비결정성으로 "서버 이름"이
    **EAV Hostname 또는 직접 `*.hostname` 컬럼**에 붙어 두 칼럼(서버 이름·호스트네임)이 모두
    hostname으로 채워지는 문제가 반복됐다. 프로필의 확정 규칙을 결정적 가드로 못박아,
    유사어/Redis 상태·LLM 변동과 무관하게 교정한다. EAV·직접 컬럼 **두 경로 모두** 처리한다.
    ('호스트네임' 등 호스트명 표면어는 name-term이 아니므로 건드리지 않는다.)
    """
    if not entity_table or not column_mapping:
        return
    for field, col in list(column_mapping.items()):
        if is_servername_to_hostname(field, col):
            column_mapping[field] = f"{entity_table}.name"


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
    if (db_engine or "").lower() == "db2":
        # DB2: 집계 함수 내부에서 DECIMAL 캐스트(정수 truncate 방지). ::numeric은 문법 오류.
        # 또한 DB2 `AVG(DECIMAL)`은 스케일을 크게 확장(예: scale 18)하여 ROUND(x,2)로 값은 2자리로
        # 반올림돼도 **타입 스케일이 남아** 결과가 6.51000000000000000000처럼 trailing zero로 직렬화된다
        # (엑셀 제로필). 최종을 `CAST(... AS DECIMAL(15,2))`로 감싸 스케일을 2로 고정한다(D-068 후속).
        inner = f"CAST(s.{val_col} AS DECIMAL(15,4))"
        return (
            f"  CAST(ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
            f"AND s.definition_name='{definition_name}' THEN {inner} END), 2) AS DECIMAL(15,2)) AS \"{field}\""
        )
    return (
        f"  ROUND({agg_fn}(CASE WHEN c.resource_type='{rt}' "
        f"AND s.definition_name='{definition_name}' THEN s.{val_col} END)::numeric, 2) AS \"{field}\""
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
) -> tuple[list[str], set[str], bool]:
    """피벗 SELECT 라인 목록·필요 resource_type 집합·metric 유무를 계산한다(블록/SQL 공용).

    metric_fields는 폼필 경로가 쓰는 한글 라벨(예: "CPU 평균")로, `classify_metric_field`로
    (resource_type, 집계함수, 값컬럼)을 추론한다. explicit_measures는 시맨틱 컴파일러(트랙 C)가
    쓰는 명시 지정으로, 라벨 분류에 의존하지 않고 (alias, resource_type, agg_fn, val_col,
    definition_name)을 직접 전달한다(MaxIORate 등 Utilization 외 지표 지원). 둘 다 주면 합쳐 넣는다.
    """
    lines: list[str] = []
    rtset: set[str] = {_SERVER_RESOURCE_TYPE}
    for field, col in regular_entries:
        bare = col.split(".")[-1]
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


def build_multi_resource_pivot_sql(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    db_schema: str | None = None,
    limit: int | None = None,
    stat_month: str | None = None,
    metric_table: str = "cmm_metric_stat_m",
    explicit_measures: list[tuple[str, str, str, str, str]] | None = None,
) -> str:
    """폼필/시맨틱 다중 리소스 피벗을 **runnable SQL로 결정적 조립**한다(LLM 우회, D-068 2차).

    프롬프트로 스켈레톤을 "제안"하면 LLM이 프로필 few-shot 예시(월별 GROUP BY 등)와 경쟁해
    무시·변형(서버 중복·config 누락)한다. 이 well-defined 쿼리는 코드가 직접 조립하여 LLM
    변동성을 제거한다. 조인 패턴은 프로필의 검증된 예시와 동일하되, 사용률까지 **단일 GROUP BY
    스코프**에 합친다(config·metric 이중 조인은 집계값에 불변). 시맨틱 컴파일러(트랙 C, D-076)가
    이 함수를 패턴 A(서버설정)+B(성능지표) 조립 엔진으로 **재사용**한다(이중 조립 엔진 금지 — D-067).

    Args:
        regular_entries/server_eav/child_eav/eav_pattern/metric_fields/db_engine: 피벗 구성요소
        db_schema: 스키마 한정자(polestar 등, DB2는 대문자 POLESTAR — D-057). 비면 무한정.
        limit: 결과 상한(엔진별 LIMIT/FETCH FIRST). None이면 미적용.
        stat_month: 사용률 기간 필터 YYYYMM(예: '202506'). None이면 전체 월 평균.
        metric_table: 월별 통계 테이블명(폴스타 기본 cmm_metric_stat_m).
        explicit_measures: 시맨틱 컴파일러용 명시 measure (alias, resource_type, agg_fn,
            val_col, definition_name). metric_fields의 한글라벨 분류 대신 직접 지정(패턴 B).

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결).
    """
    entity, config, attr_col, val_col, ent_join, cfg_join = _eav_pattern_parts(eav_pattern)
    lines, rtset, has_metric = _pivot_select_parts(
        regular_entries, server_eav, child_eav, metric_fields, attr_col, val_col, db_engine,
        explicit_measures=explicit_measures,
    )

    def q(table: str) -> str:
        return f"{db_schema}.{table}" if db_schema else table

    metric_join = ""
    if has_metric:
        month_cond = f" AND s.stat_date = '{stat_month}'" if stat_month else ""
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
    sql = (
        "SELECT\n"
        f"{select_block}\n"
        f"FROM {q(entity)} c\n"
        f"LEFT JOIN {q(config)} cc ON cc.{cfg_join} = c.{ent_join}"
        f"{metric_join}\n"
        f"WHERE c.resource_type IN ({rt_in})\n"
        "  AND c.dtime IS NULL\n"
        "GROUP BY COALESCE(c.platform_resource_id, c.id)"
    )
    if limit:
        if (db_engine or "").lower() == "db2":
            sql += f"\nFETCH FIRST {limit} ROWS ONLY"
        else:
            sql += f"\nLIMIT {limit}"
    return sql + ";"


def build_multi_resource_pivot_block(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    eav_pattern: dict,
    metric_fields: list[str] | None = None,
    db_engine: str | None = None,
    metric_table: str = "cmm_metric_stat_m",
) -> str:
    """서버 + 자식 리소스(server.Cpus/Memory) 속성 + 사용률 통계를 **한 쿼리**로 피벗하는 결정적 지침.

    LLM 프롬프트용 텍스트 버전(결정적 SQL 조립이 불가한 경로의 폴백). 실제 폼필 멀티 경로는
    `build_multi_resource_pivot_sql`로 SQL을 직접 조립한다(D-068 2차). 자식 리소스 속성이 하나라도
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
            "기간 조건(예: '지난달 1개월')은 **s.stat_date**(YYYYMM 문자열)에 적용하세요"
            f"(예: AND s.stat_date = '지난달YYYYMM'). 통계 테이블은 반드시 월별 {metric_table}만 "
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


def build_query_examples_block(structure_meta: dict | None) -> str:
    """structure_meta.query_examples를 few-shot 블록 텍스트로 만든다(없으면 빈 문자열).

    프로필의 검증된 질문→SQL 패턴을 LLM에 그대로 제시하여 조인 환각을 줄인다.
    단일/멀티 DB 경로가 동일한 예시 블록을 사용하도록 단일 출처화한다.

    Args:
        structure_meta: 구조 분석 메타(수동 프로필 로드 결과, query_examples 포함 가능)

    Returns:
        시스템 프롬프트에 덧붙일 예시 블록 텍스트(없으면 "")
    """
    if not structure_meta:
        return ""
    query_examples = structure_meta.get("query_examples", [])
    if not query_examples:
        return ""

    block = "\n\n## 쿼리 예시 (반드시 이 패턴을 따르세요)"
    block += "\n아래 예시의 JOIN 패턴을 그대로 따라하세요. 임의로 다른 조인 조건을 만들지 마세요.\n"
    for i, ex in enumerate(query_examples, 1):
        question = ex.get("question", "")
        sql_example = ex.get("sql", "").rstrip()
        explanation = ex.get("explanation", "")
        block += f'\n### 예시 {i}: "{question}"'
        block += f"\n```sql\n{sql_example}\n```"
        if explanation:
            block += f"\n설명: {explanation}"
        block += "\n"
    return block


def build_value_index_block(matched: dict[str, list[str]] | None) -> str:
    """E5-2 값 검색 매칭 리터럴을 프롬프트 주입 블록으로 만든다(없으면 빈 문자열).

    질의 키워드로 검증된 **실측 리터럴**(예: `resource_type='server.Server'`,
    EAV `NAME='Hostname'`)만 제시하여 WHERE 리터럴 환각(Plan 25 유형)을 차단한다.
    단일/멀티 DB 경로가 동일 블록을 사용하도록 단일 출처화한다.

    Args:
        matched: {인덱스 키: [매칭 리터럴, ...]} (search_value_index 결과)

    Returns:
        프롬프트에 덧붙일 검증 리터럴 블록(없으면 "")
    """
    if not matched:
        return ""
    lines = [
        "\n\n## 검증된 값 리터럴 (WHERE 절에 이 실측 값만 사용)",
        "아래는 질의와 관련해 DB에서 실측 확인된 값입니다. WHERE 절 리터럴은 반드시 "
        "이 목록의 값만 사용하고, 목록에 없는 값을 임의로 지어내지 마세요.",
    ]
    for key, values in matched.items():
        if not values:
            continue
        vals = ", ".join(f"'{v}'" for v in values)
        lines.append(f"- {key}: {vals}")
    return "\n".join(lines) if len(lines) > 2 else ""


# 선행 task 결과(prior_rows) 서버 스코프 강제 (orchestration 데이터 의존 패턴 ②, D-086)
# hostname 힌트를 name 힌트보다 먼저 검사한다 — "hostname"은 "name"을 부분 문자열로 포함.
_PRIOR_HOSTNAME_HINTS: tuple[str, ...] = ("hostname", "host_name")
_PRIOR_NAME_HINTS: tuple[str, ...] = ("server_name", "name")
_MAX_PRIOR_SCOPE_VALUES: int = 100


def _collect_prior_identity_values(prior_rows: dict) -> tuple[str, list[str]]:
    """prior_rows에서 서버 식별 컬럼 종류와 값 목록을 추출한다.

    hostname류 컬럼이 하나라도 있으면 hostname을 우선하고, 없으면 name류를 쓴다
    (폴스타는 name≠hostname — D-061 계열, 컬럼 종류를 섞으면 0건 위험).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]}

    Returns:
        ("hostname" | "name" | "", 중복 제거된 값 목록[상한 적용])
    """
    hostnames: list[str] = []
    names: list[str] = []
    seen_h: set[str] = set()
    seen_n: set[str] = set()
    for rows in (prior_rows or {}).values():
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            for col, val in row.items():
                if val is None or str(val).strip() == "":
                    continue
                col_l = str(col).lower()
                text = str(val).strip()
                if any(h in col_l for h in _PRIOR_HOSTNAME_HINTS):
                    if text not in seen_h:
                        seen_h.add(text)
                        hostnames.append(text)
                elif any(n in col_l for n in _PRIOR_NAME_HINTS):
                    if text not in seen_n:
                        seen_n.add(text)
                        names.append(text)
    if hostnames:
        return "hostname", hostnames[:_MAX_PRIOR_SCOPE_VALUES]
    if names:
        return "name", names[:_MAX_PRIOR_SCOPE_VALUES]
    return "", []


def build_prior_rows_block(prior_rows: dict | None) -> str:
    """선행 task 결과 서버 목록을 SQL 스코프 강제 블록으로 렌더링한다(없으면 빈 문자열).

    orchestration 데이터 의존(input_from) 경로에서 선행 task가 선별한 서버들로
    이번 SQL의 대상을 결정적으로 한정한다. 선별 조건(알람 상태·심각도 등)을 LLM이
    재표현하다 환각(존재하지 않는 resource_type='alarm.Alarm' 등)으로 0건이 되는
    것을 차단한다(D-086). 단일/멀티 DB 경로가 동일 블록을 사용한다(D-066).

    Args:
        prior_rows: {task_id: [식별 키 행, ...]} (subagents._make_isolated_input 산출)

    Returns:
        프롬프트에 덧붙일 스코프 강제 블록(유효한 식별 값이 없으면 "")
    """
    if not prior_rows:
        return ""
    col, values = _collect_prior_identity_values(prior_rows)
    if not values:
        return ""
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return (
        "## 선행 작업 결과 서버 스코프 (필수 준수)\n"
        "이번 조회 대상은 선행 작업에서 이미 선별된 아래 서버들로 **한정**합니다.\n"
        f"- 대상 서버 (선행 결과 {col} 컬럼 기준): {quoted}\n"
        "규칙:\n"
        f"1. SQL에 서버 한정 조건 `{col} IN ({quoted})` 을 반드시 포함하세요 "
        f"(위 값은 선행 결과의 식별 컬럼 값 목록입니다 — 대상 DB에서 서버를 식별하는 컬럼(`{col}` "
        "또는 동등한 식별 컬럼)에 적용하세요. 서버 엔터티 행 기준, GROUP BY 피벗 쿼리면 기존 규칙대로 "
        "HAVING의 집계 CASE WHEN으로 적용).\n"
        "2. 서버를 선별했던 조건(알람 발생·심각도·활성 상태 등)은 선행 작업에서 이미 처리 완료되었습니다 — "
        "선별에 사용한 테이블·컬럼·조건(알람/이벤트 등)을 이 SQL에서 다시 표현하지 마세요. "
        "대상 DB에 존재하지 않는 테이블/컬럼/값으로 선별 조건을 지어내지 마세요(환각 금지).\n"
        "3. 위 목록 외의 서버가 결과에 포함되어서는 안 됩니다."
    )
