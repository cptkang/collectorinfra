"""커버리지 판정 (E6-3 결정적 판정 + E5-2 리터럴 검증).

``src/nodes/semantic_compiler.py``에서 분리했다(Plan 69 P5-1) — 상태·설정·LLM에 결합하지
않고 SMQ와 시맨틱 모델 dict만 보는 순수 판정이라 nodes 밖에 두어 ``src.tools``가 nodes를
거치지 않고 참조하게 한다(순환 해소).
"""

from __future__ import annotations

from typing import Optional

from src.semantic.ir import (
    CoverageResult,
    SMQ,
    SMQFilter,
    SMQMeasure,
    SMQOrderBy,
    _AGG_FN,
    _ALARM_COUNT_ALIAS,
    _FILTER_SQL_OPS,
    _MAX_IR_LIMIT,
    _MEASURE_FILTER_OPS,
    _YYYYMM_RE,
)


def _dimension_index(pattern_a: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """패턴 A dimension 인덱스를 (정확이름, 소문자별칭) 두 맵으로 만든다.

    ``Model``(server.Server)과 ``MODEL``(server.Cpus)처럼 대소문자만 다른 속성이 공존하므로,
    소문자 단일 인덱스로 합치면 충돌한다. SMQ dimension 토큰은 카탈로그의 정확한 이름을 쓰므로
    **정확이름(대소문자 구분) 우선** 매칭하고, 소문자 별칭 맵은 관용 폴백으로만 쓴다.

    계층 taxonomy(N4/D-133): **형제 둘 이상이 부모(상위어) 이름을 자기 별칭으로 주장하면 그
    별칭을 등록하지 않는다.** 선착순(``setdefault``)으로 하나가 조용히 이기면 평면 동의어
    사전의 precision 붕괴가 그대로 재현되기 때문이다 — 등록하지 않으면 상위어 토큰이 모호한
    채로 남아 모호성 처리(``_expand_hypernym_ambiguity``)나 커버리지 사유로 드러난다.
    주장하는 형제가 하나뿐이면 큐레이션이 그 하나로 결속한 것으로 보고 유지한다(결속 해제는
    기존 ``alias_deny`` 수단 — 현행 카탈로그는 이 경우뿐이라 규칙 발동 0건).
    """
    dims = pattern_a.get("dimensions", []) or []
    contested = _contested_parent_aliases(dims)
    by_name: dict[str, dict] = {}
    by_alias: dict[str, dict] = {}
    for dim in dims:
        name = dim.get("name")
        if not name:
            continue
        parent = str(dim.get("parent") or "").strip().lower()
        by_name[name] = dim
        by_alias.setdefault(name.lower(), dim)
        for alias in dim.get("aliases", []) or []:
            if parent in contested and str(alias).strip().lower() == parent:
                continue
            by_alias.setdefault(alias.lower(), dim)
    return by_name, by_alias


def _contested_parent_aliases(dimensions: list) -> set[str]:
    """둘 이상의 형제가 자기 별칭으로 주장하는 상위어 이름 집합(소문자)."""
    claims: dict[str, int] = {}
    for dim in dimensions:
        parent = str(dim.get("parent") or "").strip().lower()
        if not parent:
            continue
        if any(str(a).strip().lower() == parent for a in dim.get("aliases") or []):
            claims[parent] = claims.get(parent, 0) + 1
    return {parent for parent, count in claims.items() if count > 1}


def _resolve_dim(token: str, index: tuple[dict[str, dict], dict[str, dict]]) -> Optional[dict]:
    """dimension 토큰을 정확이름(대소문자 구분) → 소문자 별칭 순으로 해소한다."""
    by_name, by_alias = index
    if token in by_name:
        return by_name[token]
    return by_alias.get(str(token).lower())


# 서버 식별 dimension으로 인정하는 direct 컬럼 — 이 중 하나가 SELECT에 있어야 리스트 행을
# 사람이 식별할 수 있다(avail_status 등 direct라도 식별자가 아닌 컬럼은 제외).
_IDENTITY_COLUMNS = {"name", "hostname", "ipaddress"}


def _has_identity_dim(
    dimensions: list, index: tuple[dict[str, dict], dict[str, dict]]
) -> bool:
    """선택된 dimension 중 서버 식별 direct 컬럼이 있는지 판정한다."""
    for d in dimensions:
        entry = _resolve_dim(str(d), index)
        if entry and entry.get("source") == "direct" and entry.get("column") in _IDENTITY_COLUMNS:
            return True
    return False


def _measure_combos(pattern_b: dict) -> set[tuple[str, str]]:
    """패턴 B에서 허용된 (resource_type, definition_name) 조합 집합."""
    return {
        (m.get("resource_type"), m.get("definition_name"))
        for m in pattern_b.get("measures", []) or []
    }


# 패턴 A/B에서 컴파일러가 안전하게 처리 가능한 필터(그 외는 커버리지 밖 — HAVING/동적날짜 등 미지원).
_PATTERN_AB_SAFE_FILTER_FIELDS = {"resource_type"}
# 패턴 C에서 처리 가능한 필터.
_PATTERN_C_SAFE_FILTER_FIELDS = {"ALARMSEVERITY"}


def check_coverage(
    smq: SMQ,
    model: dict,
    *,
    value_index: Optional[dict[str, list[str]]] = None,
) -> CoverageResult:
    """SMQ가 시맨틱 모델로 결정적 처리 가능한지 판정한다.

    보수적 판정(과설계 방지) — 커버리지 내는 컴파일러가 **정확히** 조립할 수 있는 형태로만 한정하고,
    나머지(HAVING 서버필터·동적 날짜·집계 over 알람·LOB 속성)는 밖으로 돌려 폴백(현행 LLM)에 맡긴다.
    커버리지는 dimension 카탈로그를 사람 승인 루프(D-012)로 점진 확장한다.

    Args:
        smq: 판정 대상 SMQ
        model: 시맨틱 모델 dict
        value_index: E5-2 값 인덱스(있으면 필터 리터럴을 실측 검증)

    Returns:
        CoverageResult(covered, reason)
    """
    if not model:
        return CoverageResult(covered=False, reason="시맨틱 모델 없음")

    if smq.pattern in ("A", "B"):
        return _coverage_ab(smq, model, value_index)
    if smq.pattern == "C":
        return _coverage_c(smq, model, value_index)
    return CoverageResult(covered=False, reason=f"미지원 패턴: {smq.pattern}")


def _coverage_ab(smq: SMQ, model: dict, value_index: Optional[dict]) -> CoverageResult:
    pattern_a = model.get("pattern_a") or {}
    dim_index = _dimension_index(pattern_a)
    for dim in smq.dimensions:
        entry = _resolve_dim(str(dim), dim_index)
        if entry is None:
            return CoverageResult(covered=False, reason=f"미정의 dimension: {dim}")
        if entry.get("lob"):
            # LOB 속성(OSParameter)은 COALESCE(stringvalue, stringvalue_short)가 필요해
            # 단일 val_col 피벗으로 표현 불가 — 폴백에 위임(Known Mistakes 2026-06-10).
            return CoverageResult(covered=False, reason=f"LOB dimension 미지원: {dim}")
    combos = _measure_combos(model.get("pattern_b") or {})
    for m in smq.measures:
        if (m.resource_type, m.definition_name) not in combos:
            return CoverageResult(
                covered=False,
                reason=f"미정의 measure: {m.resource_type}/{m.definition_name}",
            )
        if m.agg not in _AGG_FN:
            return CoverageResult(covered=False, reason=f"미지원 집계: {m.agg}")
    for f in smq.filters:
        reason = _filter_reason_ab(f, smq, model, dim_index)
        if reason:
            return CoverageResult(covered=False, reason=reason)
    shape = _shape_reason_ab(smq, model, dim_index)
    if shape:
        return CoverageResult(covered=False, reason=shape)
    lit = _validate_literals(smq, model, value_index)
    if lit:
        return CoverageResult(covered=False, reason=lit)
    if not smq.dimensions and not smq.measures and not smq.entity_count:
        return CoverageResult(covered=False, reason="dimension/measure 없음")
    ir = _ir_common_reason(smq, lambda: _resolve_ir_order_by_ab(smq, dim_index))
    if ir:
        return CoverageResult(covered=False, reason=ir)
    return CoverageResult(covered=True)


def _safe_filter_fields_ab(model: dict) -> set[str]:
    """패턴 A/B에서 필터로 허용할 필드 집합 — 카탈로그 ``filterable`` 선언을 정본으로 쓴다.

    S-IR4: 코드 상수 1개(resource_type)와 YAML 선언 5개의 불일치가 "선언 커버리지 76.9% vs
    런타임 판정 34.6%" 격차의 구조적 원인이었다(계획서 §2.5 한계 3). 선언을 정본으로 삼되,
    선언이 없는 모델은 기존 상수로 폴백한다. 선언돼 있어도 **컴파일러가 실제로 조립할 수
    있는 형태**(direct 컬럼 + 지원 op)만 통과한다 — 통과시키고 조립에서 빠뜨리면 조건 없는
    SQL이 나가므로, 조립 불가는 반드시 커버리지 밖이다.
    """
    declared = {str(f) for f in ((model.get("pattern_a") or {}).get("filterable") or [])}
    return (declared | _PATTERN_AB_SAFE_FILTER_FIELDS) if declared else set(
        _PATTERN_AB_SAFE_FILTER_FIELDS
    )


def _filter_reason_ab(
    f: SMQFilter, smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> Optional[str]:
    """패턴 A/B 필터 1건이 조립 불가인 사유를 돌려준다(조립 가능하면 None)."""
    kind = _classify_filter_ab(f, smq, model, dim_index)
    if kind:
        return None
    if _measure_by_ref(smq, f.field) is not None:
        return f"미지원 측정치 임계 op(집계 비교만 가능): {f.field} {f.op}"
    if f.field in _safe_filter_fields_ab(model):
        return f"미지원 필터 형태(direct 컬럼·지원 op만): {f.field} {f.op}"
    return f"미지원 필터(서버필터/동적조건은 폴백): {f.field}"


def _classify_filter_ab(
    f: SMQFilter, smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> str:
    """필터를 컴파일 가능한 종류로 분류한다 — resource_type|measure|direct, 불가하면 빈 문자열."""
    if f.field == "resource_type":
        # 대상 resource_type은 dimension·measure 선택에서 결정적으로 도출되므로 조립에 쓰지
        # 않는다(기존 동작 유지 — 조립 시 무시 사실을 계측·로그로 가시화한다).
        return "resource_type"
    if _measure_by_ref(smq, f.field) is not None:
        return "measure" if str(f.op).lower() in _MEASURE_FILTER_OPS else ""
    if f.field not in _safe_filter_fields_ab(model):
        return ""
    entry = _resolve_dim(str(f.field), dim_index)
    if entry is None or entry.get("source") != "direct":
        # EAV 속성 필터는 피벗 후 HAVING 대상이 아니라 값 컬럼 조건이라 폴백에 위임.
        return ""
    op = str(f.op).lower()
    if op not in _FILTER_SQL_OPS:
        return ""
    if op == "in" and not isinstance(f.value, (list, tuple)):
        return ""
    if op != "in" and isinstance(f.value, (list, tuple, dict)):
        return ""
    return "direct"


def _measure_by_ref(smq: SMQ, ref: str) -> Optional[SMQMeasure]:
    """참조 문자열(measure alias 또는 resource_type)로 선택된 measure를 찾는다."""
    token = str(ref or "").strip().lower()
    if not token:
        return None
    for m in smq.measures:
        if token in (m.alias.lower(), m.resource_type.lower()):
            return m
    return None


def _shape_reason_ab(smq: SMQ, model: dict, dim_index: tuple[dict, dict]) -> Optional[str]:
    """S-IR1/2 형태 확장(전역 집계·기간별 분해)의 조립 가능 조건을 판정한다."""
    if smq.global_aggregate:
        if smq.time_breakdown:
            return "전역 집계와 기간별 분해는 동시 지원 불가"
        if smq.dimensions:
            return "전역 집계는 dimension 불가(단일 값 집계)"
        if not smq.measures and not smq.entity_count:
            return "전역 집계에 집계 대상(measure/entity_count) 없음"
    elif smq.entity_count:
        return "엔티티 수 집계는 전역 집계(global_aggregate)에서만 지원"
    if smq.entity_count:
        # 세는 대상은 엔티티(서버) 행뿐이다 — 자식 리소스 수를 요청했는데 엔티티 수를 돌려주면
        # 조용한 오답이 된다.
        entity_rt = _entity_resource_type(model.get("pattern_a") or {})
        others = [
            rt for rt in smq.resource_types
            if entity_rt and str(rt).lower() != entity_rt.lower()
        ]
        if others:
            return f"엔티티 외 리소스 수 집계 미지원: {', '.join(map(str, others))}"
    if smq.time_range and not smq.measures:
        # 기간을 적용할 통계 조인이 없으면 조건이 사라진다 — 기간 질의는 폴백에 맡긴다.
        return "기간 조건을 적용할 measure 없음(설정 조회에 기간 미지원)"
    if smq.time_breakdown:
        if not smq.measures:
            return "기간별 행 분해는 measure 필요(통계 기간 기준 분해)"
        for dim in smq.dimensions:
            entry = _resolve_dim(str(dim), dim_index)
            if entry is None or entry.get("source") != "direct":
                # 기간이 GROUP BY에 들어가면 EAV 속성 행이 다른 그룹으로 갈려 NULL이 된다.
                return f"기간별 분해는 EAV 속성 dimension 미지원: {dim}"
    return None


def _ir_common_reason(smq: SMQ, resolve_order) -> Optional[str]:
    """패턴 공통 IR 확장(order_by·limit·time_range)의 형식·해소 가능성을 판정한다."""
    if smq.limit is not None and not (0 < int(smq.limit) <= _MAX_IR_LIMIT):
        return f"IR limit 범위 밖(1~{_MAX_IR_LIMIT}): {smq.limit}"
    if smq.time_range is not None:
        if not smq.time_range or len(smq.time_range) > 2:
            return f"IR time_range 형식 오류(YYYYMM 1~2개): {smq.time_range}"
        for ym in smq.time_range:
            if not _YYYYMM_RE.fullmatch(str(ym)):
                return f"IR time_range 형식 오류(YYYYMM 아님): {ym}"
    if smq.order_by is not None:
        if str(smq.order_by.direction).lower() not in ("asc", "desc"):
            return f"IR order_by 방향 미지원: {smq.order_by.direction}"
        if resolve_order() is None:
            return f"IR order_by 대상 미해소(카탈로그·선택 밖): {smq.order_by.field}"
    return None


def _coverage_c(smq: SMQ, model: dict, value_index: Optional[dict]) -> CoverageResult:
    pattern_c = model.get("pattern_c") or {}
    known_entities = {e.upper() for e in (pattern_c.get("entities") or {}).keys()}
    for ent in smq.entities:
        if str(ent).upper() not in known_entities:
            return CoverageResult(covered=False, reason=f"미정의 알람 엔터티: {ent}")
    dim_map = {k.upper(): v for k, v in (pattern_c.get("dimensions") or {}).items()}
    for dim in smq.dimensions:
        if str(dim).upper() not in dim_map:
            return CoverageResult(covered=False, reason=f"미정의 알람 dimension: {dim}")
    for f in smq.filters:
        if f.field not in _PATTERN_C_SAFE_FILTER_FIELDS:
            # 기간은 IR time_range로 승격된 것만 처리한다(원시 CTIME 조건은 폴백).
            return CoverageResult(
                covered=False,
                reason=f"미지원 알람 필터(기간/집계는 폴백): {f.field}",
            )
    if smq.global_aggregate or smq.time_breakdown:
        return CoverageResult(
            covered=False, reason="알람은 전역 집계·기간별 분해 형태 미지원",
        )
    if smq.time_range and "CTIME" not in dim_map:
        # 시각 컬럼 표현이 카탈로그에 없으면 기간 조건을 조립할 수 없다(조건 누락 SQL 금지).
        return CoverageResult(
            covered=False, reason="알람 기간 조건 조립 불가(카탈로그에 CTIME 없음)",
        )
    ir = _ir_common_reason(smq, lambda: _resolve_ir_order_by_c(smq, dim_map))
    if ir:
        return CoverageResult(covered=False, reason=ir)
    return CoverageResult(covered=True)


def _validate_literals(
    smq: SMQ, model: dict, value_index: Optional[dict]
) -> Optional[str]:
    """E5-2 값 검색 연결 — 필터 리터럴이 실측 값집합에 존재하는지 검증한다(있을 때만).

    컴파일러가 emit하는 리터럴은 전부 시맨틱 모델 정의값이라 구조적 환각은 불가능하지만,
    SMQ 필터가 특정 실측 값(예: resource_type='server.Xyz')을 참조하면 value_index로 검증해
    미검증 값은 커버리지 밖으로 돌린다(리터럴 환각(Plan 25 유형)의 컴파일러 우회 방지).
    value_index가 없으면(플래그 OFF) 검증을 건너뛴다.

    Returns:
        검증 실패 사유 문자열, 통과 시 None.
    """
    if not value_index:
        return None
    for f in smq.filters:
        if f.op in ("like",):
            continue
        # 값 인덱스가 그 필드를 실제로 수집했을 때만 검증한다("가용 시" 게이트 — 미수집 필드를
        # 미검증으로 몰면 정상 필터가 전부 폴백으로 새 나간다).
        candidates = value_index.get(f.field) or []
        if not candidates:
            continue
        values = f.value if isinstance(f.value, (list, tuple)) else [f.value]
        for val in values:
            if isinstance(val, str) and val not in candidates:
                return f"미검증 리터럴(value_index 부재): {f.field}={val}"
    return None


def _entity_resource_type(pattern_a: dict) -> str:
    """카탈로그가 말하는 엔티티(서버) resource_type을 얻는다(없으면 빈 문자열).

    선언(``entity_resource_type``)이 우선이고, 없는 카탈로그는 direct dimension에 찍힌
    resource_type에서 읽는다 — 컴파일러에 엔티티 이름을 하드코딩하지 않기 위함(D-088).
    """
    rt = str(pattern_a.get("entity_resource_type") or "")
    if rt:
        return rt
    return next(
        (
            str(d.get("resource_type") or "")
            for d in (pattern_a.get("dimensions") or [])
            if d.get("source") == "direct" and d.get("resource_type")
        ),
        "",
    )


# 표시·병합 친화 alias (기본 lower() 대신 명시). server_name은 병합 canonical 키와 일치.
_ALARM_DIM_ALIAS = {
    "SERVER_NAME": "server_name",
    "NAME": "alarm_name",
    "ALARMSEVERITY": "severity",
}


def _order_direction(order_by: SMQOrderBy) -> str:
    """IR 정렬 방향을 SQL 키워드로 바꾼다(기본 DESC)."""
    return "ASC" if str(order_by.direction).lower() == "asc" else "DESC"


def _resolve_ir_order_by_ab(
    smq: SMQ, dim_index: tuple[dict, dict]
) -> Optional[tuple[str, str]]:
    """패턴 A/B의 IR order_by를 (SELECT alias, 방향)으로 해소한다 (S-IR3).

    해소 순서는 measure(alias·resource_type) → dimension 카탈로그 이름이다. 어느 것과도
    맞지 않으면 None — 커버리지 판정이 이를 밖으로 돌린다(임의 컬럼 정렬 금지).
    엔티티 수 집계는 전역 단일 행이라 정렬 대상이 아니다.
    """
    if smq.order_by is None:
        return None
    direction = _order_direction(smq.order_by)
    field = str(smq.order_by.field).strip()
    measure = _measure_by_ref(smq, field)
    if measure is not None:
        return measure.alias, direction
    entry = _resolve_dim(field, dim_index)
    if entry is not None:
        return entry["name"], direction
    return None


def _resolve_ir_order_by_c(
    smq: SMQ, dim_map: dict[str, str]
) -> Optional[tuple[str, str]]:
    """패턴 C의 IR order_by를 (SELECT alias, 방향)으로 해소한다 (S-IR5).

    건수 집계 alias와 카탈로그 알람 dimension(그 SELECT alias)만 허용한다.
    """
    if smq.order_by is None:
        return None
    direction = _order_direction(smq.order_by)
    field = str(smq.order_by.field).strip()
    if smq.entity_count and field.lower() == _ALARM_COUNT_ALIAS:
        return _ALARM_COUNT_ALIAS, direction
    key = field.upper()
    if key in dim_map:
        return _ALARM_DIM_ALIAS.get(key, field.lower()), direction
    # SELECT alias(예: server_name)로 지정한 경우도 받아들인다.
    for dim_key in dim_map:
        if _ALARM_DIM_ALIAS.get(dim_key, dim_key.lower()) == field.lower():
            return _ALARM_DIM_ALIAS.get(dim_key, dim_key.lower()), direction
    return None
