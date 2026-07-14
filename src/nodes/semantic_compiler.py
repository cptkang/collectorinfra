"""시맨틱 모델 기반 결정적 SQL 컴파일러 (Plan 61 트랙 C / E6 / D-076).

LLM이 SQL을 직접 쓰지 않고 검증된 dimension/measure/entity를 **선택(SMQ)**하면, 이 모듈이
결정적으로 SQL을 조립한다 — 잘못된 조인·집계·미존재 컬럼(구조적 환각)이 원천 불가능해진다
(D-035 결정적=판단 정합). 커버리지 내 정형 질의(서버 설정·성능지표·알람)의 1차 방어선.

구성:
    - ``SMQ`` (Pydantic): 폴스타판 Semantic Model Query 중간표현. gold_smq 계약과 일치
      (pattern/resource_types/dimensions/measures/filters/time_grain/active_only/entities).
    - ``load_semantic_model(db_id)``: ``config/semantic_models/{db_id}.yaml`` 로드
      (db_profiles 불변 — 입력 소스로만 참조). db_engine/db_schema는 여기 저장하지 않고
      ``get_domain_by_id``에서 결정적으로 주입한다(D-066 후속6 단일 출처).
    - ``check_coverage(smq, model, value_index)``: 질의가 시맨틱 모델로 결정적 처리 가능한지 판정.
    - ``compile_smq(smq, db_id)``: SMQ → 방언별 SQL. 패턴 A(서버설정)+B(성능지표)는 기존 결정적
      조립 엔진 ``build_multi_resource_pivot_sql``(D-068)을 **재사용**한다(이중 조립 엔진 금지 —
      D-067 단일 출처). 패턴 C(알람)는 정규화 조인 전용 조립.

리터럴 정확성(E5-2 연결): 컴파일러가 emit하는 리터럴(resource_type·EAV 속성명·severity)은 전부
**시맨틱 모델이 정의한 검증된 값**이라 구조적으로 환각이 불가능하다. SMQ 필터가 모델 밖의 실측
값을 참조하면 ``check_coverage``가 값 인덱스(E5-2)로 검증하고, 미검증이면 커버리지 밖으로 돌린다.

계층: application(nodes) — utils.query_gen_common·routing.db_schema/domain_config·config 참조.
활성화: ``cfg.text2sql.semantic_compose`` 플래그(기본 OFF). OFF 시 호출부가 미진입한다.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from src.routing.db_schema import get_schema_prefix
from src.routing.domain_config import get_domain_by_id
from src.utils.query_gen_common import build_multi_resource_pivot_sql, resolve_query_limit

logger = logging.getLogger(__name__)

_AGG_FN = {"avg": "AVG", "max": "MAX", "min": "MIN"}


# ──────────────────────────────────────────────
# SMQ 중간표현 (gold_smq 계약과 일치)
# ──────────────────────────────────────────────

class SMQMeasure(BaseModel):
    """성능지표 measure (패턴 B). gold_smq measure dict와 동일 필드."""

    agg: str                        # avg | max | min
    definition_name: str            # Utilization | MaxIORate
    resource_type: str              # server.Cpus 등

    def as_dict(self) -> dict:
        return {"agg": self.agg, "definition_name": self.definition_name,
                "resource_type": self.resource_type}


class SMQFilter(BaseModel):
    """WHERE/HAVING 필터 (field, op, value). gold_smq filter dict와 동일 필드."""

    field: str
    op: str                         # eq | ne | in | like | gte | lte
    value: Any

    def as_dict(self) -> dict:
        return {"field": self.field, "op": self.op, "value": self.value}


class SMQ(BaseModel):
    """폴스타판 Semantic Model Query — LLM이 선택하고 컴파일러가 결정적으로 조립한다."""

    pattern: Literal["A", "B", "C"]
    resource_types: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)          # 패턴 C
    dimensions: list[str] = Field(default_factory=list)
    measures: list[SMQMeasure] = Field(default_factory=list)
    filters: list[SMQFilter] = Field(default_factory=list)
    time_grain: Optional[str] = None                            # hour | day | month | None
    active_only: bool = False                                   # 패턴 C

    @classmethod
    def from_dict(cls, data: dict) -> "SMQ":
        """gold_smq/LLM 산출 dict에서 SMQ를 만든다(measures/filters는 dict 리스트)."""
        d = dict(data or {})
        d["measures"] = [SMQMeasure(**m) if isinstance(m, dict) else m
                         for m in d.get("measures", []) or []]
        d["filters"] = [SMQFilter(**f) if isinstance(f, dict) else f
                        for f in d.get("filters", []) or []]
        return cls(**d)

    def to_match_dict(self) -> dict:
        """E1 하네스 ``smq_match``가 채점하는 dict 표현(순서 무관 비교 대상)."""
        return {
            "pattern": self.pattern,
            "resource_types": list(self.resource_types),
            "entities": list(self.entities),
            "dimensions": list(self.dimensions),
            "measures": [m.as_dict() for m in self.measures],
            "filters": [f.as_dict() for f in self.filters],
            "time_grain": self.time_grain,
            "active_only": self.active_only,
        }


class CoverageResult(BaseModel):
    """커버리지 판정 결과 — 내부(covered=True)면 컴파일, 밖이면 reason과 함께 폴백."""

    covered: bool
    reason: str = ""


# ──────────────────────────────────────────────
# 시맨틱 모델 로더
# ──────────────────────────────────────────────

_MODEL_CACHE: dict[str, Optional[dict]] = {}


def load_semantic_model(db_id: str, *, use_cache: bool = True) -> Optional[dict]:
    """``config/semantic_models/{db_id}.yaml``을 로드한다(없으면 None).

    db_profiles와 동일한 로딩 패턴. db_engine/db_schema는 모델에 없고 domain_config에서 주입한다.
    """
    if use_cache and db_id in _MODEL_CACHE:
        return _MODEL_CACHE[db_id]
    path = os.path.join("config", "semantic_models", f"{db_id}.yaml")
    model: Optional[dict] = None
    if os.path.exists(path):
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                model = yaml.safe_load(f)
        except Exception as e:  # noqa: BLE001 — 로드 실패는 커버리지 밖으로 graceful 강등
            logger.warning("시맨틱 모델 로드 실패 (%s): %s", path, e)
            model = None
    if use_cache:
        _MODEL_CACHE[db_id] = model
    return model


def _dimension_index(pattern_a: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    """패턴 A dimension 인덱스를 (정확이름, 소문자별칭) 두 맵으로 만든다.

    ``Model``(server.Server)과 ``MODEL``(server.Cpus)처럼 대소문자만 다른 속성이 공존하므로,
    소문자 단일 인덱스로 합치면 충돌한다. SMQ dimension 토큰은 카탈로그의 정확한 이름을 쓰므로
    **정확이름(대소문자 구분) 우선** 매칭하고, 소문자 별칭 맵은 관용 폴백으로만 쓴다.
    """
    by_name: dict[str, dict] = {}
    by_alias: dict[str, dict] = {}
    for dim in pattern_a.get("dimensions", []) or []:
        name = dim.get("name")
        if not name:
            continue
        by_name[name] = dim
        by_alias.setdefault(name.lower(), dim)
        for alias in dim.get("aliases", []) or []:
            by_alias.setdefault(alias.lower(), dim)
    return by_name, by_alias


def _resolve_dim(token: str, index: tuple[dict[str, dict], dict[str, dict]]) -> Optional[dict]:
    """dimension 토큰을 정확이름(대소문자 구분) → 소문자 별칭 순으로 해소한다."""
    by_name, by_alias = index
    if token in by_name:
        return by_name[token]
    return by_alias.get(str(token).lower())


def _measure_combos(pattern_b: dict) -> set[tuple[str, str]]:
    """패턴 B에서 허용된 (resource_type, definition_name) 조합 집합."""
    return {
        (m.get("resource_type"), m.get("definition_name"))
        for m in pattern_b.get("measures", []) or []
    }


# ──────────────────────────────────────────────
# 커버리지 판정 (E6-3 결정적 판정 + E5-2 리터럴 검증)
# ──────────────────────────────────────────────

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
        if f.field not in _PATTERN_AB_SAFE_FILTER_FIELDS:
            return CoverageResult(
                covered=False,
                reason=f"미지원 필터(서버필터/동적조건은 폴백): {f.field}",
            )
    lit = _validate_literals(smq, model, value_index)
    if lit:
        return CoverageResult(covered=False, reason=lit)
    if not smq.dimensions and not smq.measures:
        return CoverageResult(covered=False, reason="dimension/measure 없음")
    return CoverageResult(covered=True)


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
            # CTIME 범위·GROUP BY 집계(상위 N)는 동적/집계라 폴백에 위임.
            return CoverageResult(
                covered=False,
                reason=f"미지원 알람 필터(기간/집계는 폴백): {f.field}",
            )
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
        if f.field != "resource_type":
            continue
        val = f.value
        if f.op in ("like",) or not isinstance(val, str):
            continue
        candidates = value_index.get("resource_type") or []
        if candidates and val not in candidates:
            return f"미검증 리터럴(value_index 부재): resource_type={val}"
    return None


# ──────────────────────────────────────────────
# 결정적 컴파일 (SMQ → SQL)
# ──────────────────────────────────────────────

def compile_smq(
    smq: SMQ,
    db_id: str,
    model: Optional[dict] = None,
    *,
    user_query: str = "",
    default_limit: int = 100,
    stat_month: Optional[str] = None,
) -> str:
    """SMQ를 방언별 SQL로 결정적 컴파일한다(패턴 A/B는 기존 엔진 재사용, C는 알람 조립).

    Args:
        smq: 컴파일 대상 SMQ(커버리지 내로 판정된 것)
        db_id: 대상 DB 식별자(엔진·스키마 결정용)
        model: 시맨틱 모델(없으면 로드)
        user_query: 원문 질의("전체/모든" LIMIT 상향 판단용)
        default_limit: 기본 LIMIT
        stat_month: 성능지표 기간 필터 YYYYMM(패턴 B)

    Returns:
        실행 가능한 SQL 문자열(세미콜론 종결)
    """
    if model is None:
        model = load_semantic_model(db_id)
    if not model:
        raise ValueError(f"시맨틱 모델 없음: {db_id}")

    domain = get_domain_by_id(db_id)
    db_engine = domain.db_engine if domain else "postgresql"
    db_schema = domain.db_schema if domain else ""
    limit = resolve_query_limit(user_query, default_limit)

    if smq.pattern in ("A", "B"):
        return _compile_ab(smq, model, db_engine, db_schema, limit, stat_month)
    if smq.pattern == "C":
        return _compile_c(smq, model, db_id, limit)
    raise ValueError(f"미지원 패턴: {smq.pattern}")


def _compile_ab(
    smq: SMQ,
    model: dict,
    db_engine: str,
    db_schema: str,
    limit: int,
    stat_month: Optional[str],
) -> str:
    """패턴 A(서버설정)+B(성능지표)를 build_multi_resource_pivot_sql로 조립한다(D-067 재사용).

    dimension을 direct(cmm_resource 컬럼)/server_eav/child_eav로 나누고, measure를
    explicit_measures로 넘긴다 — resource_type 구분 CASE WHEN + 단일 GROUP BY(서버당 1행).
    """
    pattern_a = model.get("pattern_a") or {}
    pattern_b = model.get("pattern_b") or {}
    eav_pattern = pattern_a.get("eav") or {}
    dim_index = _dimension_index(pattern_a)

    # measure만 선택되고 dimension이 비면 결과 행을 식별할 컬럼(서버명 등)이 없어 값만 나열된다
    # — 실측상 LLM SMQ가 자주 범하는 선택 누락(Plan 61 §7 SMQ 정확도 축). 프롬프트 유도 대신
    # 모델 pattern_b.default_dimensions를 결정적으로 주입한다(D-035, D-076 후속).
    dimensions = list(smq.dimensions)
    if smq.measures and not dimensions:
        dimensions = [
            d for d in (pattern_b.get("default_dimensions") or [])
            if _resolve_dim(str(d), dim_index) is not None
        ]

    regular_entries: list[tuple[str, str]] = []
    server_eav: list[tuple[str, str]] = []
    child_eav: list[tuple[str, str, str]] = []
    entity_table = eav_pattern.get("entity_table", "cmm_resource")
    for dim in dimensions:
        entry = _resolve_dim(str(dim), dim_index)
        name = entry["name"]
        if entry.get("source") == "direct":
            regular_entries.append((name, f"{entity_table}.{entry['column']}"))
        elif (entry.get("resource_type") or "server.Server") == "server.Server":
            server_eav.append((name, entry["attribute"]))
        else:
            child_eav.append((name, entry["attribute"], entry["resource_type"]))

    value_columns = pattern_b.get("value_columns") or {"avg": "avg_val", "max": "max_val", "min": "min_val"}
    explicit_measures: list[tuple[str, str, str, str, str]] = []
    for m in smq.measures:
        rt_short = m.resource_type.split(".")[-1].lower()
        alias = f"{rt_short}_{m.agg.lower()}"
        val_col = value_columns.get(m.agg.lower(), "avg_val")
        explicit_measures.append(
            (alias, m.resource_type, _AGG_FN[m.agg.lower()], val_col, m.definition_name)
        )

    metric_tables = pattern_b.get("metric_tables") or {}
    grain = smq.time_grain or pattern_b.get("default_time_grain", "month")
    metric_table = metric_tables.get(grain, "cmm_metric_stat_m")

    return build_multi_resource_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        db_engine=db_engine, db_schema=db_schema, limit=limit,
        stat_month=stat_month, metric_table=metric_table,
        explicit_measures=explicit_measures or None,
    )


def _compile_c(smq: SMQ, model: dict, db_id: str, limit: int) -> str:
    """패턴 C(알람)를 정규화 조인으로 결정적 조립한다.

    CMM_ALARM(CA)↔CMM_ALARM_DEF(D)↔CMM_ALARM_ACTIVE(A)↔CMM_RESOURCE(CR). 활성 알람은 A 조인 +
    severity IN (1,2,3), 이력은 A 미조인 + IN (0,1,2,3). severity 명시 필터가 있으면 그 값 사용.
    조인 조건·컬럼은 시맨틱 모델(검증된 골드 SQL 유래)에서만 가져와 환각 불가.
    """
    pattern_c = model.get("pattern_c") or {}
    prefix = get_schema_prefix(db_id)
    dim_map = {k.upper(): v for k, v in (pattern_c.get("dimensions") or {}).items()}
    engine = (get_domain_by_id(db_id).db_engine if get_domain_by_id(db_id) else "postgresql")

    # SELECT — 요청 dimension만 조립(요청 항목만, Template 전체 복사 회피)
    select_lines = [f"  {dim_map[str(d).upper()]} AS {str(d).lower()}" for d in smq.dimensions]
    if not select_lines:
        select_lines = ["  CA.ALARMSEVERITY AS severity", "  CA.CTIME AS ctime"]

    base = pattern_c.get("base_table") or {"table": "cmm_resource", "alias": "CR"}
    from_clause = f"FROM {prefix}{base['table']} {base['alias']}"

    join_lines: list[str] = []
    joins = pattern_c.get("joins") or {}
    for jsql in joins.values():
        join_lines.append(jsql.format(p=prefix))
    if smq.active_only and pattern_c.get("active_join"):
        join_lines.append(pattern_c["active_join"].format(p=prefix))

    where_parts = list(pattern_c.get("base_where") or [])
    sev_filter = next((f for f in smq.filters if f.field == "ALARMSEVERITY"), None)
    if sev_filter is not None:
        if sev_filter.op == "in" and isinstance(sev_filter.value, (list, tuple)):
            vals = ", ".join(str(int(v)) for v in sev_filter.value)
            where_parts.append(f"CA.ALARMSEVERITY IN ({vals})")
        else:
            where_parts.append(f"CA.ALARMSEVERITY = {int(sev_filter.value)}")
    elif smq.active_only:
        where_parts.append("CA.ALARMSEVERITY IN (1, 2, 3)")
    else:
        where_parts.append("CA.ALARMSEVERITY IN (0, 1, 2, 3)")

    sql = "SELECT\n" + ",\n".join(select_lines) + "\n" + from_clause
    if join_lines:
        sql += "\n" + "\n".join(join_lines)
    sql += "\nWHERE " + "\n  AND ".join(where_parts)
    order_by = pattern_c.get("order_by")
    if order_by:
        sql += f"\nORDER BY {order_by}"
    if limit:
        if (engine or "").lower() == "db2":
            sql += f"\nFETCH FIRST {limit} ROWS ONLY"
        else:
            sql += f"\nLIMIT {limit}"
    return sql + ";"


# ──────────────────────────────────────────────
# 진입 헬퍼 (coverage_router에서 호출)
# ──────────────────────────────────────────────

def try_semantic_compile(
    smq: SMQ,
    db_id: str,
    *,
    user_query: str = "",
    default_limit: int = 100,
    stat_month: Optional[str] = None,
    value_index: Optional[dict[str, list[str]]] = None,
) -> tuple[Optional[str], CoverageResult]:
    """커버리지 판정 후 내부면 컴파일 SQL을, 밖이면 None과 사유를 반환한다.

    coverage_router가 이 헬퍼를 호출해 (SQL 또는 None, CoverageResult)을 받는다.
    커버리지 밖(None)이면 호출부가 현행 폴백(LLM 자유생성)으로 진행한다.
    """
    model = load_semantic_model(db_id)
    cov = check_coverage(smq, model or {}, value_index=value_index)
    if not cov.covered:
        return None, cov
    sql = compile_smq(
        smq, db_id, model, user_query=user_query,
        default_limit=default_limit, stat_month=stat_month,
    )
    return sql, cov


# ──────────────────────────────────────────────
# 자연어 → SMQ (LLM 선택) + coverage_router 진입점
# ──────────────────────────────────────────────

def render_catalog(model: dict) -> str:
    """시맨틱 모델을 NL→SMQ 프롬프트용 카탈로그 텍스트로 렌더한다(선택 가능 항목만 제시)."""
    lines: list[str] = []
    pattern_a = model.get("pattern_a") or {}
    dims = pattern_a.get("dimensions") or []
    if dims:
        lines.append("■ 패턴 A 서버설정 dimensions (name — resource_type — 별칭):")
        for d in dims:
            aliases = ", ".join(d.get("aliases", []) or [])
            lob = " (LOB — 미지원)" if d.get("lob") else ""
            lines.append(f"  - {d.get('name')} [{d.get('resource_type')}]{lob}"
                         + (f" ← {aliases}" if aliases else ""))
    pattern_b = model.get("pattern_b") or {}
    measures = pattern_b.get("measures") or []
    if measures:
        lines.append("■ 패턴 B 성능지표 measures (resource_type/definition_name — 별칭):")
        for m in measures:
            aliases = ", ".join(m.get("aliases", []) or [])
            lines.append(f"  - {m.get('resource_type')} / {m.get('definition_name')}"
                         + (f" ← {aliases}" if aliases else ""))
        grains = ", ".join((pattern_b.get("metric_tables") or {}).keys())
        lines.append(f"  time_grain 옵션: {grains} (기본 month)")
        lines.append("  집계(agg): avg, max, min")
    pattern_c = model.get("pattern_c") or {}
    ents = pattern_c.get("entities") or {}
    if ents:
        lines.append("■ 패턴 C 알람 엔터티: " + ", ".join(ents.keys()))
        cdims = pattern_c.get("dimensions") or {}
        lines.append("  알람 dimensions: " + ", ".join(cdims.keys()))
        sev = pattern_c.get("severity_map") or {}
        lines.append("  severity: " + ", ".join(f"{k}={v}" for k, v in sev.items()))
    return "\n".join(lines)


def parse_smq_response(content: str) -> Optional[SMQ]:
    """LLM 응답에서 SMQ JSON을 파싱한다(코드펜스 허용). 밖/파싱실패는 None."""
    import json
    import re

    text = (content or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        m2 = re.search(r"\{.*\}", text, re.S)
        if m2:
            text = m2.group(0)
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("pattern", "")).upper() not in ("A", "B", "C"):
        return None  # {"pattern": "none"} 등 커버리지 밖 신호
    try:
        return SMQ.from_dict(data)
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 폴백으로 강등
        logger.debug("SMQ 파싱 실패(스키마 불일치): %s", e)
        return None


async def compile_from_nl(
    llm: Any,
    user_query: str,
    db_id: str,
    *,
    default_limit: int = 100,
    stat_month: Optional[str] = None,
    value_index: Optional[dict[str, list[str]]] = None,
) -> tuple[Optional[str], Optional[SMQ], Optional[CoverageResult]]:
    """coverage_router: 자연어 → (LLM)SMQ → 커버리지 판정 → 결정적 컴파일.

    반환:
        (sql, smq, cov) — sql이 있으면 커버리지 내 결정적 조립 성공(LLM SQL 생성 우회).
        sql이 None이면 커버리지 밖/파싱실패 → 호출부가 현행 폴백(LLM 자유생성)으로 진행.
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from src.prompts.semantic_compiler import (
        SEMANTIC_SMQ_SYSTEM_TEMPLATE,
        SEMANTIC_SMQ_USER_TEMPLATE,
    )

    model = load_semantic_model(db_id)
    if not model:
        return None, None, None

    system = SEMANTIC_SMQ_SYSTEM_TEMPLATE.format(catalog=render_catalog(model))
    user = SEMANTIC_SMQ_USER_TEMPLATE.format(user_query=user_query)
    messages = [SystemMessage(content=system)]
    # KBGenAIChat 등은 SystemMessage 다음 AIMessage 순서를 요구 — 클래스명으로 감지(선택 의존).
    if type(llm).__name__ == "KBGenAIChat":
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user))

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — LLM 실패는 폴백으로 강등(회귀 0)
        logger.warning("NL→SMQ 생성 실패(폴백): %s", e)
        return None, None, None

    smq = parse_smq_response(getattr(response, "content", "") or "")
    if smq is None:
        return None, None, None

    cov = check_coverage(smq, model, value_index=value_index)
    if not cov.covered:
        logger.info("시맨틱 커버리지 밖(폴백): %s", cov.reason)
        return None, smq, cov
    sql = compile_smq(
        smq, db_id, model, user_query=user_query,
        default_limit=default_limit, stat_month=stat_month,
    )
    logger.info("시맨틱 결정적 컴파일 성공(패턴 %s): %s", smq.pattern, sql[:200])
    return sql, smq, cov
