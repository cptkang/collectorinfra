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

IR 모델(``SMQ``)·커버리지 판정·카탈로그 렌더·가드 계측은 ``src.semantic`` 패키지에 있다
(Plan 69 P5-1 — ``src.tools``가 nodes를 거쳐 참조하던 순환을 끊기 위한 분리). 이 모듈은
그 이름들을 전부 재노출하므로 기존 임포트 경로(``from src.nodes.semantic_compiler import
SMQ`` 등)는 무수정으로 동작한다.

계층: application(nodes) — utils.query_gen_common·routing.db_schema/domain_config·config 참조.
활성화: ``cfg.text2sql.semantic_compose`` 플래그(기본 OFF). OFF 시 호출부가 미진입한다.
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any, Optional

from src.utils.llm_compat import is_kbgenai
from src.routing.db_schema import get_schema_prefix
from src.routing.domain_config import get_domain_by_id
from src.utils.json_extract import extract_json_from_response
from src.utils.sql_dialect import row_limit_clause
from src.utils.query_gen_common import (
    StatMonth,
    resolve_query_limit,
    resolve_stat_month_range,
)
# 폴스타 피벗 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트(D-067 재사용).
from src.db_adapters.polestar.assembler import build_multi_resource_pivot_sql
# IR·커버리지 판정·카탈로그 렌더·가드 계측은 `src.semantic`으로 이동했다(Plan 69 P5-1 — nodes↔
# tools 순환 해소). 이 모듈이 쓰는 것과 하위호환 재노출분을 함께 임포트하고 `__all__`로 공표한다.
from src.semantic import (
    CoverageResult,
    GUARD_BREAKDOWN_PROMOTE,
    GUARD_CAPACITY_INJECT,
    GUARD_HYPERNYM_EXPAND,
    GUARD_IR_LIMIT,
    GUARD_IR_ORDER_BY,
    GUARD_IR_TIME_RANGE,
    GUARD_MONTHLY_GATE,
    GUARD_PHYSICALCORE_DROP,
    GUARD_PHYSICALCORE_SWAP,
    GUARD_RANKING_SURFACE,
    GUARD_RESOURCE_TYPE_FILTER_IGNORED,
    GUARD_SCOPE_FILTER_STRIP,
    GUARD_SCOPE_GLOBAL_DROP,
    GUARD_TIME_FILTER_PROMOTE,
    GUARD_TIME_RANGE_OVERRIDE,
    SMQ,
    SMQFilter,
    SMQMeasure,
    SMQOrderBy,
    check_coverage,
    guard_counters,
    note_guard,
    render_catalog,
    reset_guard_counters,
)
# 패키지 공개 API가 아닌 내부 헬퍼는 각 하위 모듈에서 직접 가져온다(어디로 갔는지 그대로 보인다).
from src.semantic.coverage import (
    _ALARM_DIM_ALIAS,
    _IDENTITY_COLUMNS,
    _PATTERN_AB_SAFE_FILTER_FIELDS,
    _PATTERN_C_SAFE_FILTER_FIELDS,
    _classify_filter_ab,
    _contested_parent_aliases,
    _coverage_ab,
    _coverage_c,
    _dimension_index,
    _entity_resource_type,
    _filter_reason_ab,
    _has_identity_dim,
    _ir_common_reason,
    _measure_by_ref,
    _measure_combos,
    _order_direction,
    _resolve_dim,
    _resolve_ir_order_by_ab,
    _resolve_ir_order_by_c,
    _safe_filter_fields_ab,
    _shape_reason_ab,
    _validate_literals,
)
from src.semantic.guards import _GUARD_COUNTERS, _guard_delta
from src.semantic.ir import (
    _AGG_FN,
    _ALARM_COUNT_ALIAS,
    _FILTER_SQL_OPS,
    _MAX_IR_LIMIT,
    _MEASURE_FILTER_OPS,
    _ORDER_DIRECTION_KEYS,
    _ORDER_FIELD_KEYS,
    _YYYYMM_RE,
    _coerce_order_by,
)
from src.semantic.taxonomy import (
    _child_dim_entries,
    _child_discriminators,
    _child_measure_specs,
    _expand_hypernym_ambiguity,
    _hypernym_surfaces,
    _mentions_any,
    _missing_child_dims,
    _missing_child_measures,
    _squash,
    _taxonomy,
)

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 진입 분기 안에서 지연 수행한다.
    from src.config import AppConfig
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)

#: 이 모듈이 계속 노출하는 이름 — 자체 API + `src.semantic` 이동분의 하위호환 재노출이다.
#: (Plan 69 P5-1. 신규 코드는 이동분을 `src.semantic`에서 직접 임포트할 것.)
__all__ = [
    # 이 모듈의 자체 API
    "load_semantic_model", "compile_smq", "compile_from_nl", "normalize_smq",
    "parse_smq_response",
    # src.semantic.ir
    "SMQ", "SMQFilter", "SMQMeasure", "SMQOrderBy", "CoverageResult",
    "_AGG_FN", "_ALARM_COUNT_ALIAS", "_FILTER_SQL_OPS", "_MAX_IR_LIMIT",
    "_MEASURE_FILTER_OPS", "_ORDER_DIRECTION_KEYS", "_ORDER_FIELD_KEYS",
    "_YYYYMM_RE", "_coerce_order_by",
    # src.semantic.guards
    "note_guard", "guard_counters", "reset_guard_counters", "_GUARD_COUNTERS",
    "_guard_delta",
    "GUARD_BREAKDOWN_PROMOTE", "GUARD_CAPACITY_INJECT", "GUARD_HYPERNYM_EXPAND",
    "GUARD_IR_LIMIT", "GUARD_IR_ORDER_BY", "GUARD_IR_TIME_RANGE",
    "GUARD_MONTHLY_GATE", "GUARD_PHYSICALCORE_DROP", "GUARD_PHYSICALCORE_SWAP",
    "GUARD_RANKING_SURFACE", "GUARD_RESOURCE_TYPE_FILTER_IGNORED",
    "GUARD_SCOPE_FILTER_STRIP", "GUARD_SCOPE_GLOBAL_DROP",
    "GUARD_TIME_FILTER_PROMOTE", "GUARD_TIME_RANGE_OVERRIDE",
    # src.semantic.coverage
    "check_coverage", "_ALARM_DIM_ALIAS", "_IDENTITY_COLUMNS",
    "_PATTERN_AB_SAFE_FILTER_FIELDS", "_PATTERN_C_SAFE_FILTER_FIELDS",
    "_classify_filter_ab", "_contested_parent_aliases", "_coverage_ab",
    "_coverage_c", "_dimension_index", "_entity_resource_type",
    "_filter_reason_ab", "_has_identity_dim", "_ir_common_reason",
    "_measure_by_ref", "_measure_combos", "_order_direction", "_resolve_dim",
    "_resolve_ir_order_by_ab", "_resolve_ir_order_by_c",
    "_safe_filter_fields_ab", "_shape_reason_ab", "_validate_literals",
    # src.semantic.catalog_render
    "render_catalog",
    # src.semantic.taxonomy
    "_child_dim_entries", "_child_discriminators", "_child_measure_specs",
    "_expand_hypernym_ambiguity", "_hypernym_surfaces", "_mentions_any",
    "_missing_child_dims", "_missing_child_measures", "_squash", "_taxonomy",
]


# ──────────────────────────────────────────────
# 시맨틱 모델 로더
# ──────────────────────────────────────────────

_MODEL_CACHE: dict[str, Optional[dict]] = {}


def load_semantic_model(db_id: str, *, use_cache: bool = True) -> Optional[dict]:
    """시맨틱 모델(dimension/measure 카탈로그)을 지식 정본에서 만들어 반환한다(없으면 None).

    원천 우선순위(Plan 67 R1 — 지식 정본 일원화):
        1. **정본**: 구조 선언 ``config/db_profiles/{db_id}.yaml`` + 큐레이션
           ``config/knowledge/{db_id}/catalog.yaml`` → ``build_catalog``로 생성.
           큐레이션이 없는 DB는 카탈로그를 만들지 않는다(선별되지 않은 컬럼이 결정적 조립
           대상으로 새어 들어가는 것을 막는다).
        2. **사본 폴백**: 기존 ``config/semantic_models/{db_id}.yaml``. 정본 생성 실패 시에만 쓴다.

    두 원천의 산출물이 동등함은 ``scripts/catalog_diff.py``가 실측한다(전환 시점 차이 0).
    db_engine/db_schema는 모델에 없고 domain_config에서 주입한다(D-066 후속6 단일 출처).
    """
    if use_cache and db_id in _MODEL_CACHE:
        return _MODEL_CACHE[db_id]

    from src.schema_cache.catalog_builder import (
        build_catalog,
        load_knowledge_overrides,
        load_structure_profile,
    )

    model: Optional[dict] = None
    overrides = load_knowledge_overrides(db_id)
    if overrides:
        structure = load_structure_profile(overrides.get("structure_from") or db_id)
        if structure:
            model = build_catalog(structure, db_id=db_id, overrides=overrides)
            logger.debug("시맨틱 모델 원천=지식 정본 (db_id=%s)", db_id)

    if model is None:
        path = os.path.join("config", "semantic_models", f"{db_id}.yaml")
        if os.path.exists(path):
            try:
                import yaml
                with open(path, "r", encoding="utf-8") as f:
                    model = yaml.safe_load(f)
                # 정본에서 생성하지 못하고 사본으로 강등된 상태 — 침묵 폴백 금지(사유 가시화).
                logger.warning(
                    "시맨틱 모델을 지식 정본에서 생성하지 못해 사본으로 강등 (db_id=%s, 사본=%s) "
                    "— config/db_profiles·config/knowledge 확인 필요",
                    db_id, path,
                )
            except Exception as e:  # noqa: BLE001 — 로드 실패는 커버리지 밖으로 graceful 강등
                logger.warning("시맨틱 모델 로드 실패 (%s): %s", path, e)
                model = None

    if use_cache:
        _MODEL_CACHE[db_id] = model
    return model


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
    stat_month: StatMonth = None,
    server_scope: Optional[tuple[str, list[str]]] = None,
) -> str:
    """SMQ를 방언별 SQL로 결정적 컴파일한다(패턴 A/B는 기존 엔진 재사용, C는 알람 조립).

    Args:
        smq: 컴파일 대상 SMQ(커버리지 내로 판정된 것)
        db_id: 대상 DB 식별자(엔진·스키마 결정용)
        model: 시맨틱 모델(없으면 로드)
        user_query: 원문 질의("전체/모든" LIMIT 상향 판단용)
        default_limit: 기본 LIMIT
        stat_month: 성능지표 기간 필터 — 단일 월 YYYYMM 또는 (시작, 끝) 범위(패턴 B, D-102)
        server_scope: 선행 task 결과 서버 한정 (식별컬럼, 값목록) — 패턴 A/B에 HAVING으로
            결정적 적용(D-099). None이면 미적용.

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
    # IR limit(S-IR3)이 있으면 표면어 해석(resolve_query_limit)보다 우선한다 — 표면어 파싱은
    # IR 부재 시의 폴백으로 남긴다(R3 원칙: 정규식 제거가 아니라 강등).
    if smq.limit:
        limit = int(smq.limit)
        note_guard(GUARD_IR_LIMIT, f"limit={limit}")
    else:
        limit = resolve_query_limit(user_query, default_limit)
    # 호출부가 결정적으로 해석한 기간이 우선이고, 없을 때만 IR 기간을 쓴다(D-035 결정적 우선).
    if stat_month is None and smq.time_range:
        stat_month = _stat_month_from_ir(smq.time_range)
        note_guard(GUARD_IR_TIME_RANGE, f"time_range={smq.time_range}")

    if smq.pattern in ("A", "B"):
        return _compile_ab(
            smq, model, db_engine, db_schema, limit, stat_month,
            server_scope=server_scope, user_query=user_query,
        )
    if smq.pattern == "C":
        return _compile_c(smq, model, db_id, limit, db_engine=db_engine)
    raise ValueError(f"미지원 패턴: {smq.pattern}")


def _stat_month_from_ir(time_range: list[str]) -> StatMonth:
    """IR time_range([YYYYMM] 또는 [시작, 끝])를 조립기 stat_month 형식으로 바꾼다."""
    months = [str(m) for m in time_range if _YYYYMM_RE.fullmatch(str(m))]
    if not months:
        return None
    if len(months) == 1:
        return months[0]
    return (min(months), max(months))


def _compile_ab(
    smq: SMQ,
    model: dict,
    db_engine: str,
    db_schema: str,
    limit: int,
    stat_month: StatMonth,
    *,
    server_scope: Optional[tuple[str, list[str]]] = None,
    user_query: str = "",
) -> str:
    """패턴 A(서버설정)+B(성능지표)를 build_multi_resource_pivot_sql로 조립한다(D-067 재사용).

    dimension을 direct(cmm_resource 컬럼)/server_eav/child_eav로 나누고, measure를
    explicit_measures로 넘긴다 — resource_type 구분 CASE WHEN + 단일 GROUP BY(서버당 1행).
    """
    pattern_a = model.get("pattern_a") or {}
    pattern_b = model.get("pattern_b") or {}
    eav_pattern = pattern_a.get("eav") or {}
    dim_index = _dimension_index(pattern_a)

    # measure가 있는데 서버 식별 dimension(name/hostname/ipaddress direct 컬럼)이 하나도 없으면
    # 결과 행을 식별할 수 없다 — 실측상 LLM SMQ가 자주 범하는 선택 누락으로, dimension이 완전히
    # 빈 경우뿐 아니라 속성(용량 등)만 고른 경우에도 발생한다(2026-07-21 yd-004: 서버명 없는
    # 사용률 리스트). 프롬프트 유도 대신 모델 pattern_b.default_dimensions를 결정적으로
    # 앞에 주입한다(D-035, D-076 후속).
    dimensions = list(smq.dimensions)
    # 전역 집계(S-IR1)는 식별 컬럼 자체가 없어야 단일 값이 나오므로 주입 대상이 아니다.
    if smq.measures and not smq.global_aggregate and not _has_identity_dim(dimensions, dim_index):
        chosen = {
            e["name"] for d in dimensions
            if (e := _resolve_dim(str(d), dim_index)) is not None
        }
        defaults = [
            d for d in (pattern_b.get("default_dimensions") or [])
            if (e := _resolve_dim(str(d), dim_index)) is not None and e["name"] not in chosen
        ]
        dimensions = defaults + dimensions
    # 선행 스코프가 있으면 그 식별 컬럼(name/hostname)을 SELECT에 결정적으로 포함한다 —
    # 없으면 결과 행이 어느 서버의 값인지 알 수 없다(D-097).
    if server_scope and server_scope[1]:
        scope_col = server_scope[0]
        if not any(
            (_resolve_dim(str(d), dim_index) or {}).get("column") == scope_col
            for d in dimensions
        ):
            if _resolve_dim(scope_col, dim_index) is not None:
                dimensions = [scope_col] + dimensions

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
        val_col = value_columns.get(m.agg.lower(), "avg_val")
        explicit_measures.append(
            (m.alias, m.resource_type, _AGG_FN[m.agg.lower()], val_col, m.definition_name)
        )

    metric_tables = pattern_b.get("metric_tables") or {}
    grain = smq.time_grain or pattern_b.get("default_time_grain", "month")
    metric_table = metric_tables.get(grain, "cmm_metric_stat_m")

    # 정렬은 IR(S-IR3) 우선, 없으면 표면어("가장 높은/최고") 폴백(NULLS LAST는 조립기 — D-098).
    order_by = _resolve_ir_order_by_ab(smq, dim_index) if smq.order_by else None
    if order_by is not None:
        note_guard(GUARD_IR_ORDER_BY, f"{order_by[0]} {order_by[1]}")
        # 최상급 어휘가 있고 상한을 지정하지 않았으면 상위 1건 유지(D-100).
        if smq.limit is None and _is_superlative(user_query):
            limit = 1
    else:
        order_by = _resolve_ranking(user_query, explicit_measures)
        # 최상급 순위("가장 높은/낮은")는 상위 1건만 — 결정적 조립이 default_limit로 전체를 반환하면
        # 병합 시 "가장 높은 서버"가 아닌 대상 전체가 남는다(D-100 실측: 2건 반환).
        if order_by:
            note_guard(GUARD_RANKING_SURFACE, f"{order_by[0]} {order_by[1]}")
            if smq.limit is None:
                limit = 1

    direct_having, measure_having = _compile_filters_ab(smq, model, dim_index)

    return build_multi_resource_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern,
        db_engine=db_engine, db_schema=db_schema, limit=limit,
        stat_month=stat_month, metric_table=metric_table,
        explicit_measures=explicit_measures or None,
        server_scope=server_scope,
        order_by=order_by,
        time_breakdown=smq.time_breakdown,
        global_aggregate=smq.global_aggregate,
        entity_count_alias=_entity_count_alias(pattern_a) if smq.entity_count else None,
        direct_having=direct_having or None,
        measure_having=measure_having or None,
    )


def _entity_count_alias(pattern_a: dict) -> str:
    """엔티티 수 집계 컬럼 alias를 엔티티 resource_type에서 만든다(예: server.Server → server_count)."""
    short = _entity_resource_type(pattern_a).split(".")[-1].lower()
    return f"{short}_count" if short else "entity_count"


def _compile_filters_ab(
    smq: SMQ, model: dict, dim_index: tuple[dict, dict]
) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """패턴 A/B 필터를 조립기 HAVING 인자로 변환한다 (S-IR4).

    서버 식별·상태 direct 컬럼은 집계 후 HAVING으로(WHERE에 두면 자식 리소스 행이 GROUP BY
    전에 탈락 — D-096), 측정치 임계는 SELECT와 동일한 집계식으로 건다.

    Returns:
        (direct_having, measure_having) — 각각 [(컬럼|alias, SQL 연산자, 값)]
    """
    direct_having: list[tuple[str, str, Any]] = []
    measure_having: list[tuple[str, str, Any]] = []
    for f in smq.filters:
        kind = _classify_filter_ab(f, smq, model, dim_index)
        op = _FILTER_SQL_OPS.get(str(f.op).lower(), "=")
        if kind == "measure":
            measure = _measure_by_ref(smq, f.field)
            if measure is not None:
                measure_having.append((measure.alias, op, f.value))
        elif kind == "direct":
            entry = _resolve_dim(str(f.field), dim_index) or {}
            direct_having.append((entry.get("column") or str(f.field), op, f.value))
        elif kind == "resource_type":
            # 조립 대상 resource_type은 선택(dimension/measure)에서 도출하므로 이 필터는 쓰지
            # 않는다. 무시 사실을 계측해 R4 축소 판단 재료로 남긴다(침묵 무시 금지).
            note_guard(GUARD_RESOURCE_TYPE_FILTER_IGNORED, f"{f.field} {f.op} {f.value}")
    return direct_having, measure_having


# 순위(최상급) 어휘 — 방향별. 결정적 정렬 판단용(D-099).
_RANK_DESC_MARKERS = ("가장 높", "가장 많", "최고", "최대", "제일 높", "highest", "top")
_RANK_ASC_MARKERS = ("가장 낮", "가장 적", "최저", "최소", "제일 낮", "lowest")


def _resolve_ranking(
    user_query: str,
    explicit_measures: list[tuple[str, str, str, str, str]],
) -> Optional[tuple[str, str]]:
    """질의의 최상급 어휘로 정렬 대상(measure alias)과 방향을 결정한다 (D-099).

    measure가 없으면(순위 기준 없음) None. 최상급 어휘가 없어도 None(기존 무정렬 유지).

    Args:
        user_query: 원문 질의
        explicit_measures: (alias, resource_type, agg_fn, val_col, definition_name) 목록

    Returns:
        (alias, "DESC"|"ASC") 또는 None
    """
    if not explicit_measures:
        return None
    low = (user_query or "").lower()
    if any(m in low for m in _RANK_DESC_MARKERS):
        return explicit_measures[0][0], "DESC"
    if any(m in low for m in _RANK_ASC_MARKERS):
        return explicit_measures[0][0], "ASC"
    return None


def _is_superlative(user_query: str) -> bool:
    """질의에 최상급 어휘가 있는지 판정한다(상위 1건 축약 판단 — D-100)."""
    low = (user_query or "").lower()
    return any(m in low for m in _RANK_DESC_MARKERS + _RANK_ASC_MARKERS)


# 알람 조회에 항상 포함할 선별 근거 dimension (카탈로그 키, D-100).
# 서버 식별(병합 키) + 알람명 + 심각도 — "심각 알람이 있는 서버" 선별 조건을 결과에 표시.
_ALARM_CONTEXT_DIMS = ("server_name", "NAME", "ALARMSEVERITY")


def _compile_c(
    smq: SMQ, model: dict, db_id: str, limit: int, *, db_engine: str = ""
) -> str:
    """패턴 C(알람)를 정규화 조인으로 결정적 조립한다.

    CMM_ALARM(CA)↔CMM_ALARM_DEF(D)↔CMM_ALARM_ACTIVE(A)↔CMM_RESOURCE(CR). 활성 알람은 A 조인 +
    severity IN (1,2,3), 이력은 A 미조인 + IN (0,1,2,3). severity 명시 필터가 있으면 그 값 사용.
    조인 조건·컬럼은 시맨틱 모델(검증된 골드 SQL 유래)에서만 가져와 환각 불가.

    ``entity_count``가 켜지면 알람 건수 집계 형태로 조립한다(S-IR5) — 요청 dimension만
    GROUP BY하고 COUNT(*)를 얹으며, 정렬은 IR order_by(없으면 건수 내림차순)로 결정한다.
    """
    pattern_c = model.get("pattern_c") or {}
    prefix = get_schema_prefix(db_id)
    dim_map = {k.upper(): v for k, v in (pattern_c.get("dimensions") or {}).items()}
    engine = db_engine or (
        get_domain_by_id(db_id).db_engine if get_domain_by_id(db_id) else "postgresql"
    )
    counting = bool(smq.entity_count)

    # SELECT — 알람 선별 근거(서버명·알람명·심각도)를 결정적으로 앞에 포함하고, 그 뒤 요청
    # dimension을 잇는다(D-100). "심각 알람이 있는 서버" 같은 선별 조건이 최종 결과 표에
    # 함께 나타나도록 하기 위함 — 오케스트레이터가 sub_query를 "서버 목록 조회"로 좁혀
    # dimensions=[server_name]만 골라도 알람명·심각도가 소실되지 않는다. 카탈로그에 정의된
    # dimension만 사용하므로 환각 불가. alias는 표시·병합 친화적으로 명시 지정한다.
    if counting:
        # 건수 집계에서는 선별 근거를 덧붙이면 그룹이 쪼개져 "서버별 건수"가 아니게 된다 —
        # 요청 dimension만 GROUP BY 키로 쓴다.
        ordered_dims = [str(d) for d in smq.dimensions]
    else:
        base_context = [d for d in _ALARM_CONTEXT_DIMS if d.upper() in dim_map]
        base_keys = {d.upper() for d in base_context}
        ordered_dims = base_context + [
            str(d) for d in smq.dimensions if str(d).upper() not in base_keys
        ]
    select_lines: list[str] = []
    group_exprs: list[str] = []
    seen_dims: set[str] = set()
    for d in ordered_dims:
        key = str(d).upper()
        if key not in dim_map or key in seen_dims:
            continue
        seen_dims.add(key)
        alias = _ALARM_DIM_ALIAS.get(key, str(d).lower())
        select_lines.append(f"  {dim_map[key]} AS {alias}")
        group_exprs.append(dim_map[key])
    if counting:
        select_lines.append(f"  COUNT(*) AS {_ALARM_COUNT_ALIAS}")
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
    # 기간은 IR time_range로 승격된 것만 결정적 창으로 적용한다(S-IR4/5).
    where_parts.extend(_alarm_time_where(smq.time_range, dim_map))

    sql = "SELECT\n" + ",\n".join(select_lines) + "\n" + from_clause
    if join_lines:
        sql += "\n" + "\n".join(join_lines)
    sql += "\nWHERE " + "\n  AND ".join(where_parts)
    if counting and group_exprs:
        sql += "\nGROUP BY " + ", ".join(group_exprs)
    order_by = _order_clause_c(smq, dim_map, counting) or pattern_c.get("order_by")
    if order_by:
        sql += f"\nORDER BY {order_by}"
    if limit:
        sql += "\n" + row_limit_clause(engine, limit)
    return sql + ";"


def _order_clause_c(
    smq: SMQ, dim_map: dict[str, str], counting: bool
) -> Optional[str]:
    """패턴 C의 ORDER BY 절을 IR·집계 형태로 결정한다(없으면 None → 모델 기본 정렬)."""
    # NULLS LAST는 항상 부여한다 — 값 없는 행이 정렬 선두를 차지하는 것을 막고(D-098),
    # 어댑터 검증(집계 내림차순 + 행 제한 시 NULLS LAST 요구)도 이 규칙을 강제한다.
    ir = _resolve_ir_order_by_c(smq, dim_map)
    if ir is not None:
        note_guard(GUARD_IR_ORDER_BY, f"{ir[0]} {ir[1]}")
        return f"{ir[0]} {ir[1]} NULLS LAST"
    if counting:
        # 건수 집계는 모델 기본 정렬(발생시각)이 GROUP BY 밖 컬럼이라 쓸 수 없다 — 건수 내림차순.
        return f"{_ALARM_COUNT_ALIAS} DESC NULLS LAST"
    return None


def _alarm_time_where(
    time_range: Optional[list[str]], dim_map: dict[str, str]
) -> list[str]:
    """IR 기간(YYYYMM)을 알람 발생시각 범위 조건으로 만든다(없으면 빈 목록).

    시각 컬럼 표현은 카탈로그(``CTIME`` dimension)에서 가져오고, 경계는 엔진 공통
    ``DATE('YYYY-MM-DD')`` 리터럴로 쓴다(PostgreSQL·DB2 공통 문법). 끝월의 **다음 달 1일
    미만**으로 닫아 월 경계 누락·중복을 없앤다.
    """
    if not time_range:
        return []
    col = dim_map.get("CTIME")
    if not col:
        return []
    months = sorted(str(m) for m in time_range if _YYYYMM_RE.fullmatch(str(m)))
    if not months:
        return []
    return [
        f"{col} >= DATE('{_month_first_day(months[0])}')",
        f"{col} < DATE('{_month_first_day(_next_month(months[-1]))}')",
    ]


def _month_first_day(ym: str) -> str:
    """YYYYMM을 그 달 1일의 ISO 날짜 문자열로 만든다."""
    return f"{ym[:4]}-{ym[4:6]}-01"


def _next_month(ym: str) -> str:
    """YYYYMM의 다음 달을 YYYYMM으로 만든다(연 경계 처리)."""
    year, month = int(ym[:4]), int(ym[4:6])
    return f"{year + 1}01" if month == 12 else f"{year}{month + 1:02d}"


# ──────────────────────────────────────────────
# 자연어 → SMQ (LLM 선택) + coverage_router 진입점
# ──────────────────────────────────────────────


# "월별/월간" 분해 질의 검출 — "3개월간"의 '월간'은 기간 표현이므로 제외(부정 후방탐색).
_MONTHLY_BREAKDOWN_RE = re.compile(r"월별|(?<!개)월간")

_PHYSICAL_HINTS = ("물리", "physical")

# 질의의 용량 표현 검출 — SMQ가 명시 요청 차원을 누락하는 비결정 보정용(2026-07-21 gp-009).
# "CPU 용량"/"CPU코어수" 직접형 + "CPU, 메모리 용량" 열거형(용량이 CPU에도 걸림)을 커버.
_CPU_CAPACITY_RE = re.compile(r"CPU\s*(?:용량|코어)|CPU\s*[,·와과및]\s*메모리\s*용량", re.IGNORECASE)
_MEM_CAPACITY_RE = re.compile(r"메모리\s*(?:용량|크기)")

#: 기간을 필터로 표현했을 때 LLM이 쓰는 필드명(실측 2026-07-30 라이브: `time` between
#: ['202606','202606']). IR에 기간 필드(time_range)가 생겼으므로 이 표기들은 결정적으로
#: 승격한다 — 프롬프트 지시만으로는 표기가 계속 흔들린다(LLM 비결정성 원칙). DB별 실제
#: 기간 컬럼명은 여기 열거하지 않고(공용 계층 무지 유지 — D-088) 값 형태로 판정한다.
_TIME_FILTER_FIELDS = {
    "time", "times", "period", "date", "dates", "month", "months",
    "stat_month", "time_range", "ctime", "occurred_at",
    "기간", "월", "날짜",
}


def normalize_smq(
    smq: SMQ,
    user_query: str,
    model: Optional[dict] = None,
    *,
    hypernym_ambiguity: bool = False,
) -> SMQ:
    """LLM SMQ 선택의 알려진 비결정 오류를 결정적으로 교정한다(D-076 후속).

    실측(2026-07-21 yd-004): "CPU 용량"에 LOGICALCORE·PHYSICALCORE를 동시 선택. 실측
    (2026-07-21 gp-009 회귀): 같은 질의에서 PHYSICALCORE **단독** 선택으로도 흔들림 —
    운영 관행은 VM 위주라 CPU 용량/코어수=LOGICALCORE. 질의가 '물리'를 명시하지 않으면
    PHYSICALCORE를 제거(동시 선택 시)하거나 LOGICALCORE로 치환(단독 선택 시)한다.
    '물리' 신호가 있으면 선택을 존중해 불변.

    Plan 67 S3에서 두 교정을 추가했다 — 둘 다 표기·형태의 흔들림을 IR로 흡수하는 승격이다:
        - 기간 필터(`time` between …) → ``time_range``(+ 질의의 결정적 해석으로 값 교정)
        - "월별/월간" 분해 질의 → ``time_breakdown``(구 폴백 강제 게이트의 해소)
    모든 교정은 발동 카운터를 남긴다(R4 — 계측 후 축소 판단).

    Args:
        smq: LLM이 선택한 SMQ
        user_query: 원문 질의
        model: 시맨틱 모델(카탈로그) — 없으면 카탈로그 의존 교정을 건너뛴다
        hypernym_ambiguity: 상위어 단독 질의를 하위 전부로 확장할지(N4/D-133, 기본 OFF)
    """
    if hypernym_ambiguity:
        # 실측 관행이 확립된 아래 교정 가드들이 최종 중재자가 되도록 **가장 먼저** 돌린다.
        smq = _expand_hypernym_ambiguity(smq, user_query, model)
    names = {str(d).upper() for d in smq.dimensions}
    if "PHYSICALCORE" in names:
        q = (user_query or "").lower()
        if not any(h in q for h in _PHYSICAL_HINTS):
            if "LOGICALCORE" in names:
                dims = [d for d in smq.dimensions if str(d).upper() != "PHYSICALCORE"]
                note_guard(GUARD_PHYSICALCORE_DROP)
            else:
                dims = [
                    "LOGICALCORE" if str(d).upper() == "PHYSICALCORE" else d
                    for d in smq.dimensions
                ]
                note_guard(GUARD_PHYSICALCORE_SWAP)
            smq = smq.model_copy(update={"dimensions": dims})
            names = {str(d).upper() for d in dims}

    # 명시 요청 용량 차원 누락 보정 — 질의가 "CPU/메모리 용량"을 명시했는데 SMQ가 해당
    # dimension을 통째로 빠뜨리는 비결정(실측 2026-07-21 gp-009 2차 회귀: 같은 질의에서
    # 동시선택→단독선택→미선택으로 흔들림). 패턴 C(알람)는 무관하므로 A/B만.
    if smq.pattern in ("A", "B"):
        added: list[str] = []
        if _CPU_CAPACITY_RE.search(user_query or "") and not names & {"LOGICALCORE", "PHYSICALCORE"}:
            added.append("LOGICALCORE")
        if _MEM_CAPACITY_RE.search(user_query or "") and "TOTALSIZE" not in names:
            added.append("TotalSize")
        if added:
            note_guard(GUARD_CAPACITY_INJECT, ",".join(added))
            smq = smq.model_copy(update={"dimensions": list(smq.dimensions) + added})

    smq = _promote_time_filters(smq, user_query, model)
    smq = _promote_time_breakdown(smq, user_query)
    return smq


def _is_time_filter(f: SMQFilter, model: Optional[dict]) -> bool:
    """필터가 기간 표현인지 판정한다 — 필드명(표기 흔들림) 또는 값 형태(YYYYMM)로 본다.

    DB별 기간 컬럼명(profile 소관)을 공용 계층에 열거하지 않으려고 값 형태 판정을 함께 쓴다.
    카탈로그에 정의된 dimension이면 값이 우연히 6자리여도 기간으로 보지 않는다.
    """
    if str(f.field).lower() in _TIME_FILTER_FIELDS:
        return True
    if model and _resolve_dim(
        str(f.field), _dimension_index(model.get("pattern_a") or {})
    ) is not None:
        return False
    return bool(_months_from_filters([f]))


def _promote_time_filters(
    smq: SMQ, user_query: str, model: Optional[dict] = None
) -> SMQ:
    """기간을 필터로 표현한 SMQ를 ``time_range`` IR로 승격한다 (S-IR4).

    실측(2026-07-30 라이브 스모크): 선택은 정확한데 기간만 `{'field': 'time', 'op':
    'between', 'value': ['202606','202606']}` 필터로 나와 "미지원 필터"로 전량 폴백했다.
    필터를 IR 기간으로 옮기고, 질의에서 기간을 결정적으로 해석할 수 있으면 **그 값으로
    교정**한다(LLM이 계산한 월보다 결정적 파서를 신뢰 — D-035).
    """
    time_filters = [f for f in smq.filters if _is_time_filter(f, model)]
    if not time_filters and not smq.time_range:
        return smq

    resolved = resolve_stat_month_range(user_query)
    deterministic = (
        ([resolved[0]] if resolved[0] == resolved[1] else list(resolved))
        if resolved else []
    )
    llm_months = _months_from_filters(time_filters) or list(smq.time_range or [])
    months = deterministic or llm_months
    if time_filters and not months:
        # 기간 값을 결정적으로 뽑지 못했다("지난주" 등) — 필터를 그대로 남겨 커버리지가
        # 폴백으로 돌리게 한다. 조건을 조용히 버리고 전체 기간으로 집계하면 오답이 된다.
        return smq

    update: dict[str, Any] = {}
    if time_filters:
        update["filters"] = [f for f in smq.filters if f not in time_filters]
        note_guard(
            GUARD_TIME_FILTER_PROMOTE,
            "; ".join(f"{f.field} {f.op} {f.value}" for f in time_filters),
        )
    if deterministic and llm_months and deterministic != llm_months:
        note_guard(GUARD_TIME_RANGE_OVERRIDE, f"{llm_months} → {deterministic}")
    if months != list(smq.time_range or []):
        update["time_range"] = months
    return smq.model_copy(update=update) if update else smq


def _months_from_filters(time_filters: list[SMQFilter]) -> list[str]:
    """기간 필터 값에서 YYYYMM 목록을 뽑는다(YYYY-MM·YYYY년 M월 표기 포함)."""
    months: list[str] = []
    for f in time_filters:
        values = f.value if isinstance(f.value, (list, tuple)) else [f.value]
        for raw in values:
            text = str(raw)
            if _YYYYMM_RE.fullmatch(text):
                months.append(text)
                continue
            m = re.fullmatch(r"(\d{4})\D{0,2}(\d{1,2})\D?", text)
            if m and 1 <= int(m.group(2)) <= 12:
                months.append(f"{m.group(1)}{int(m.group(2)):02d}")
    # 중복 제거 + 정렬(범위 양끝만 의미가 있다).
    ordered = sorted(set(months))
    return [ordered[0], ordered[-1]] if len(ordered) > 1 else ordered


def _promote_time_breakdown(smq: SMQ, user_query: str) -> SMQ:
    """"월별/월간" 분해 질의를 ``time_breakdown`` IR로 승격한다 (S-IR2).

    종전에는 이 표면어를 만나면 컴파일을 포기하고 LLM 폴백을 강제했다(서버당 1행 집계로는
    표현 불가했기 때문). 컴파일러가 기간별 행 분해를 지원하게 됐으므로 **폴백 강제 대신
    승격**한다. 표면어 정규식은 유지하고 발동만 계측한다(R4 — 가드 삭제 금지).
    """
    if smq.pattern != "B" or smq.time_breakdown or not smq.measures:
        return smq
    if not _MONTHLY_BREAKDOWN_RE.search(user_query or ""):
        return smq
    note_guard(GUARD_BREAKDOWN_PROMOTE)
    return smq.model_copy(update={"time_breakdown": True})


def parse_smq_response(content: str) -> Optional[SMQ]:
    """LLM 응답에서 SMQ JSON을 파싱한다(코드펜스 허용). 밖/파싱실패는 None."""
    data = extract_json_from_response((content or "").strip())
    if not isinstance(data, dict):
        return None
    if str(data.get("pattern", "")).upper() not in ("A", "B", "C"):
        return None  # {"pattern": "none"} 등 커버리지 밖 신호
    try:
        return SMQ.from_dict(data)
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 폴백으로 강등
        logger.debug("SMQ 파싱 실패(스키마 불일치): %s", e)
        return None


async def _select_smq_one_shot(
    llm: Any,
    user_query: str,
    model: dict,
    server_scope: Optional[tuple[str, list[str]]],
) -> Optional[SMQ]:
    """현행 1방 SMQ 선택 — 카탈로그를 프롬프트에 실어 LLM 1회 호출로 선택받는다.

    프롬프트·메시지 구성은 무변경이다(플래그 OFF 경로의 바이트 동일성 근거).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원문 질의
        model: 시맨틱 모델(카탈로그)
        server_scope: 선행 task 결과 서버 스코프(있으면 예외 블록 주입, D-099)

    Returns:
        파싱된 SMQ, 또는 None(LLM 실패·커버리지 밖 신호·파싱 실패)
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    from src.prompts.semantic_compiler import (
        SEMANTIC_SMQ_SCOPE_NOTE,
        SEMANTIC_SMQ_SYSTEM_TEMPLATE,
        SEMANTIC_SMQ_USER_TEMPLATE,
    )

    system = SEMANTIC_SMQ_SYSTEM_TEMPLATE.format(catalog=render_catalog(model))
    user = SEMANTIC_SMQ_USER_TEMPLATE.format(user_query=user_query)
    # 선행 스코프가 있으면 "특정 서버 지목·정렬 상위" 커버리지 밖 조건을 해제한다(D-099) —
    # 이 둘은 컴파일러가 결정적으로 처리하므로 그것을 이유로 none을 받으면 조립이 무산된다.
    if server_scope and server_scope[1]:
        user += SEMANTIC_SMQ_SCOPE_NOTE
    messages = [SystemMessage(content=system)]
    # KBGenAIChat 등은 SystemMessage 다음 AIMessage 순서를 요구 — 클래스명으로 감지(선택 의존).
    if is_kbgenai(llm):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user))

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — LLM 실패는 폴백으로 강등(회귀 0)
        logger.warning("NL→SMQ 생성 실패(폴백): %s", e)
        return None

    return parse_smq_response(getattr(response, "content", "") or "")


def _stepwise_enabled(app_config: Optional["AppConfig"]) -> bool:
    """단계적 도출 루프(S2/D-128) 진입 여부를 판정한다.

    ``app_config``가 없으면 **OFF**로 본다 — 설정을 여기서 로드하면 호출마다 ``.env``를
    다시 읽게 되고(load_config는 싱글톤이 아니다) 테스트가 환경에 좌우된다. 실 경로
    (query_generator·multi_db_executor)는 항상 설정을 넘긴다.
    """
    text2sql = getattr(app_config, "text2sql", None)
    return bool(getattr(text2sql, "stepwise_derivation", False))


def _hypernym_ambiguity_enabled(app_config: Optional["AppConfig"]) -> bool:
    """상위어 모호성 처리(N4/D-133) 진입 여부를 판정한다(설정 미주입이면 OFF).

    ``_stepwise_enabled``와 같은 규칙이다 — 여기서 설정을 로드하면 호출마다 ``.env``를 다시
    읽어 테스트가 환경에 좌우된다.
    """
    text2sql = getattr(app_config, "text2sql", None)
    return bool(getattr(text2sql, "hypernym_ambiguity", False))


async def _select_smq_stepwise(
    llm: Any,
    user_query: str,
    db_id: str,
    model: dict,
    app_config: "AppConfig",
    stepwise_deps: Optional["StepwiseDeps"],
    derivation_sink: Optional[list[dict]],
    server_scope: Optional[tuple[str, list[str]]] = None,
) -> tuple[Optional[SMQ], Optional[CoverageResult]]:
    """단계적 컬럼 도출 루프(S2/D-128)로 SMQ를 도출한다.

    루프 산출물은 기존 ``SMQ.from_dict`` 경로로 통과시켜 검증한다(새 검증 경로 신설 금지 —
    D-067). 미해결 필드가 남거나 도출 실패면 **사유를 담은 CoverageResult**를 돌려 호출부가
    3단 폴백으로 넘기게 한다(침묵 폴백 금지).

    Args:
        llm: tool-calling 가능한 LLM
        user_query: 사용자 원문 질의
        db_id: 대상 DB 식별자
        model: 시맨틱 모델(카탈로그)
        app_config: 앱 설정(상한 산출용)
        stepwise_deps: 경로별 도구 주입 재료(StepwiseDeps 또는 None)
        derivation_sink: 관측 레코드 적재 리스트(state 노출용, 선택)

    Returns:
        (smq, cov) — smq가 None이면 cov.reason이 폴백 사유다.
    """
    from src.nodes.column_deriver import StepwiseDeps, StepwiseLimits, derive_smq
    from src.prompts.semantic_compiler import SEMANTIC_SMQ_SCOPE_NOTE

    deps = stepwise_deps if stepwise_deps is not None else StepwiseDeps()
    # 선행 스코프 예외 블록 — 1방 경로의 SCOPE_NOTE 주입과 대칭(D-099 ⑤, Plan 69 P0-⑩).
    # 스코프가 없으면 빈 문자열이라 도출 프롬프트 바이트 불변.
    scope_note = (
        SEMANTIC_SMQ_SCOPE_NOTE.format() if server_scope and server_scope[1] else ""
    )
    record = await derive_smq(
        llm, user_query, db_id, model,
        deps=deps,
        limits=StepwiseLimits.from_config(app_config.text2sql),
        scope_note=scope_note,
    )
    if derivation_sink is not None:
        derivation_sink.append(record)

    unresolved = record.get("unresolved") or []
    raw_smq = record.get("smq")
    if unresolved or not isinstance(raw_smq, dict):
        reason = _derivation_failure_reason(record)
        logger.info("단계적 도출 미완(폴백): %s", reason)
        return None, CoverageResult(covered=False, reason=reason)

    try:
        smq = SMQ.from_dict(raw_smq)
    except Exception as e:  # noqa: BLE001 — 스키마 불일치는 사유와 함께 폴백
        reason = f"단계적 도출 SMQ 형식 오류: {e}"
        logger.info("%s", reason)
        record["stopped_reason"] = "schema_error"
        return None, CoverageResult(covered=False, reason=reason)
    logger.info(
        "단계적 도출 SMQ 확정(패턴 %s, 라운드 %s, tool %s)",
        smq.pattern, record.get("rounds"), record.get("tool_calls"),
    )
    return smq, None


def _stamp_coverage(derivation_sink: Optional[list[dict]], covered: Optional[bool]) -> None:
    """직전 도출 레코드에 커버리지 판정 결과를 기록한다(관측 — 1방 경로는 sink 없음)."""
    if derivation_sink:
        derivation_sink[-1]["covered"] = covered


def _stamp_guards(derivation_sink: Optional[list[dict]], guards: dict[str, int]) -> None:
    """질의 1건에서 발동한 가드를 기록·로그한다 (R4 — stepwise ON/OFF 발동률 비교 재료).

    stepwise ON에서는 도출 레코드(state 노출)에도 남기고, OFF 경로는 로그만 남는다.
    """
    if not guards:
        return
    logger.info("[가드계측] 질의 단위 발동: %s", guards)
    if derivation_sink:
        derivation_sink[-1]["guards"] = guards


def _derivation_failure_reason(record: dict) -> str:
    """도출 실패·미해결을 사용자·감사 노출용 단일 사유 문자열로 만든다."""
    parts = [f"단계적 도출 미완({record.get('stopped_reason')})"]
    unresolved = record.get("unresolved") or []
    if unresolved:
        parts.append(
            "미해결: " + "; ".join(
                f"{u.get('field')}({u.get('reason')})" for u in unresolved[:5]
            )
        )
    elif not record.get("smq"):
        parts.append("SMQ 미도출")
    return " — ".join(parts)


async def compile_from_nl(
    llm: Any,
    user_query: str,
    db_id: str,
    *,
    default_limit: int = 100,
    stat_month: StatMonth = None,
    value_index: Optional[dict[str, list[str]]] = None,
    server_scope: Optional[tuple[str, list[str]]] = None,
    app_config: Optional["AppConfig"] = None,
    stepwise_deps: Optional["StepwiseDeps"] = None,
    derivation_sink: Optional[list[dict]] = None,
) -> tuple[Optional[str], Optional[SMQ], Optional[CoverageResult]]:
    """coverage_router: 자연어 → (LLM)SMQ → 커버리지 판정 → 결정적 컴파일.

    SMQ 선택 방식만 두 갈래다 — 기본은 현행 1방 선택이고, ``TEXT2SQL_STEPWISE_DERIVATION``이
    ON이면 도구 기반 단계적 도출 루프(S2/D-128)가 SMQ를 만든다. **커버리지 판정·컴파일은
    어느 쪽이든 동일한 결정적 경로**를 통과한다(D-076·D-067). SQL 생성 4경로가 모두 이
    함수를 지나므로 여기가 대칭 주입 지점이다(D-066).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 원문 질의
        db_id: 대상 DB 식별자
        default_limit: 기본 LIMIT
        stat_month: 결정적으로 해석된 통계 월(범위)
        value_index: E5-2 값 인덱스(있으면 필터 리터럴 실측 검증)
        server_scope: 선행 task 결과 서버 스코프(D-099)
        app_config: 앱 설정 — 단계적 도출 플래그·상한 판정용(없으면 1방 경로)
        stepwise_deps: 경로별 도구 주입 재료(``column_deriver.StepwiseDeps``)
        derivation_sink: 단계적 도출 관측 레코드 적재 리스트(state 노출용)

    반환:
        (sql, smq, cov) — sql이 있으면 커버리지 내 결정적 조립 성공(LLM SQL 생성 우회).
        sql이 None이면 커버리지 밖/파싱실패 → 호출부가 현행 폴백(LLM 자유생성)으로 진행.
    """
    model = load_semantic_model(db_id)
    if not model:
        return None, None, None
    guards_before = guard_counters()

    if _stepwise_enabled(app_config):
        smq, derive_cov = await _select_smq_stepwise(
            llm, user_query, db_id, model, app_config, stepwise_deps, derivation_sink,
            server_scope=server_scope,
        )
        if smq is None:
            return None, None, derive_cov
    else:
        smq = await _select_smq_one_shot(llm, user_query, model, server_scope)
        if smq is None:
            return None, None, None
    smq = normalize_smq(
        smq, user_query, model,
        hypernym_ambiguity=_hypernym_ambiguity_enabled(app_config),
    )

    # 월별 행 분해("월간/월별 통계·추이") 질의는 normalize가 time_breakdown으로 승격한다
    # (S-IR2). 승격 조건에 걸리지 않는 형태(measure 없는 패턴 B 등)는 여전히 컴파일 불가라
    # 폴백을 강제한다 — 게이트는 남기고 발동만 계측한다(R4).
    if (
        smq.pattern == "B"
        and not smq.time_breakdown
        and _MONTHLY_BREAKDOWN_RE.search(user_query or "")
    ):
        cov = CoverageResult(
            covered=False,
            reason="월별 분해(월간/월별) 질의는 컴파일 미지원 - LLM 폴백",
        )
        note_guard(GUARD_MONTHLY_GATE, cov.reason)
        logger.info("시맨틱 커버리지 밖(폴백): %s", cov.reason)
        _stamp_coverage(derivation_sink, False)
        _stamp_guards(derivation_sink, _guard_delta(guards_before))
        return None, smq, cov

    # 선행 스코프가 결정적으로 주어지면 SMQ의 서버 식별 필터는 중복이다. 그대로 두면
    # 커버리지 밖(패턴 A/B 안전 필터는 resource_type뿐)으로 밀려 결정적 조립이 발동하지
    # 못하고 LLM 폴백으로 떨어진다 — 스코프가 더 신뢰도 높은 출처이므로 제거한다(D-099).
    if server_scope and server_scope[1]:
        identity_fields = {"name", "hostname", str(server_scope[0]).lower()}
        kept = [f for f in smq.filters if str(f.field).lower() not in identity_fields]
        if len(kept) != len(smq.filters):
            note_guard(
                GUARD_SCOPE_FILTER_STRIP, f"{len(smq.filters) - len(kept)}건",
            )
            logger.info(
                "선행 스코프 우선 — SMQ 서버 식별 필터 %d건 제거(결정적 HAVING으로 대체)",
                len(smq.filters) - len(kept),
            )
            smq = smq.model_copy(update={"filters": kept})
        if smq.global_aggregate:
            # 스코프는 "이 서버들"이라는 결정적 한정이므로 전역 단일 값 집계와 양립하지
            # 않는다(HAVING이 전역 집계 1행을 지워 결과가 비어버린다) — 스코프를 우선한다.
            note_guard(GUARD_SCOPE_GLOBAL_DROP)
            smq = smq.model_copy(update={"global_aggregate": False, "entity_count": False})

    cov = check_coverage(smq, model, value_index=value_index)
    _stamp_coverage(derivation_sink, cov.covered)
    if not cov.covered:
        logger.info("시맨틱 커버리지 밖(폴백): %s", cov.reason)
        _stamp_guards(derivation_sink, _guard_delta(guards_before))
        return None, smq, cov
    sql = compile_smq(
        smq, db_id, model, user_query=user_query,
        default_limit=default_limit, stat_month=stat_month,
        server_scope=server_scope,
    )
    logger.info("시맨틱 결정적 컴파일 성공(패턴 %s): %s", smq.pattern, sql[:200])
    _stamp_guards(derivation_sink, _guard_delta(guards_before))
    return sql, smq, cov
