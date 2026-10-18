"""양식 필드 매핑의 입력·결정적 단계 — 구조 선언 판정 · 결과 객체 · 필드 추출 · 1단계 힌트.

`src.document.field_mapper`(3단계 매핑 Facade)에서 옮겼다(plans/166 I2). 종전 경로는 Facade가
옛 이름(`_` 접두 포함)으로 재노출한다(D-337 ①·③).
"""

from __future__ import annotations

import logging
from typing import Any

from src.document.synonym_write_guard import eav_declared_tables
from src.utils.month_structure import is_month_structure_field

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I2)
logger = logging.getLogger("src.document.field_mapper")

# 사용률 통계 필드 판별용 (D-066 후속/RC2). 이런 필드는 cmm_metric_stat_[h,d,m]에서
# resource_type + definition_name 피벗으로만 얻을 수 있어 단일 field→컬럼 매핑이 불가능하다.
# 매핑을 시도하면 LLM이 존재하지 않는 컬럼(cpu_avg_val 등)을 지어내고, step 3 LLM 호출이
# 응답 없이 멈춰(closed-net) 요청 전체가 hang될 수 있다. 이런 필드는 매핑을 건너뛰고
# 미매핑(None)으로 두어 SQL 생성 단계의 쿼리 예시(cmm_metric_stat_m 피벗)에 위임한다.
_METRIC_USAGE_NOUNS = ("cpu", "메모리", "mem", "디스크", "disk", "파일시스템", "filesystem")
_METRIC_USAGE_AGG_TERMS = (
    "평균", "최고", "최대", "최소", "사용률", "avg", "max", "min", "util", "usage",
)


def is_metric_usage_field(field: str) -> bool:
    """사용률 통계(평균/최고 등) 필드인지 판정한다.

    metric 명사(CPU/메모리/디스크 등) + 집계·사용률 표현(평균/최고/사용률 등)이 함께 있을 때 True.
    '메모리 용량'·'CPU 코어 수' 같은 사양(EAV) 필드는 집계어가 없어 False(정상 매핑 대상 유지).
    명사가 없어도 월 구조 필드("월중평균사용률(최근 6개월간)|M+2")면 True다 — 리소스 명사는
    양식 제목·질의에 있고 월 시리즈 인식기가 거기서 찾는다(plans/120 F-1: 명사 없는 월 필드가
    스킵을 빠져나가 오염 유사어에 정확 매칭되면서 인식기가 16회 모두 미발동했다).
    """
    low = (field or "").lower()
    if not any(noun in low for noun in _METRIC_USAGE_NOUNS):
        return is_month_structure_field(field)
    return any(term in low for term in _METRIC_USAGE_AGG_TERMS)


async def load_structure_declarations(
    cache_manager: Any,
    db_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """대상 DB의 구조 선언(structure_meta)을 조회한다(실패는 DB별로 격리).

    구조 선언은 `config/db_profiles/{db_id}.yaml`(수동 프로필)이 정본이고 캐시가 사본이라
    캐시 매니저가 캐시→프로필 순으로 해소한다. 조회 실패·미선언 DB는 결과에서 빠진다.

    Args:
        cache_manager: SchemaCacheManager 인스턴스(없으면 빈 결과)
        db_ids: 조회할 DB 식별자 목록

    Returns:
        {db_id: structure_meta} — 선언이 있는 DB만 포함
    """
    metas: dict[str, dict[str, Any]] = {}
    if cache_manager is None:
        return metas
    getter = getattr(cache_manager, "get_structure_meta_or_profile", None)
    if getter is None:
        return metas
    for db_id in db_ids:
        try:
            meta = await getter(db_id)
        except Exception as e:  # noqa: BLE001
            logger.debug("구조 선언 조회 실패 (db=%s): %s", db_id, e)
            continue
        if isinstance(meta, dict) and meta:
            metas[db_id] = meta
    return metas


def _structure_patterns(structure_metas: dict[str, dict[str, Any]] | None) -> list[dict[str, Any]]:
    """구조 선언들에서 patterns 항목을 평탄화해 반환한다."""
    patterns: list[dict[str, Any]] = []
    for meta in (structure_metas or {}).values():
        raw = meta.get("patterns") if isinstance(meta, dict) else None
        if not isinstance(raw, (list, tuple)):
            continue
        patterns.extend(p for p in raw if isinstance(p, dict))
    return patterns


def core_entity_tables(structure_metas: dict[str, dict[str, Any]] | None) -> set[str]:
    """구조 선언의 EAV 패턴에서 핵심 엔터티 테이블명을 도출한다.

    엔터티 테이블(속성이 매달리는 주 테이블)의 컬럼을 서브 테이블보다 먼저 매칭하기
    위한 우선순위 집합이다. 선언이 없으면 빈 집합 — 이 경우 우선순위 구분 없이 전체
    테이블을 한 번에 매칭한다(DB-agnostic, D-088).
    """
    return {
        str(p["entity_table"]).lower()
        for p in _structure_patterns(structure_metas)
        if p.get("entity_table")
    }


def schema_uses_eav_metric_pivot(structure_metas: dict[str, dict[str, Any]] | None) -> bool:
    """스키마가 EAV 피벗 구조인지 판정한다(사용률 필드 스킵 게이트).

    사용률이 **직접 컬럼**(예: cpu_metrics.usage_pct)인 스키마에서는 사용률 필드가 정상
    매핑 대상이므로 스킵하면 안 된다. 지표를 속성 행으로 저장하는 EAV 피벗 스키마일 때만
    스킵한다. 판정 근거는 구조 선언의 `patterns[].type == "eav"`이며, 특정 DB의 테이블명
    리터럴에 의존하지 않는다(value_index.derive_value_specs와 동일 판정 방식).
    """
    return any(p.get("type") == "eav" for p in _structure_patterns(structure_metas))


def sub_table_scope(
    structure_metas: dict[str, dict[str, Any]] | None,
    db_ids: list[str],
) -> dict[str, frozenset[str]]:
    """EAV 피벗 DB별 서브 테이블 매칭 허용 테이블(구조 선언 테이블)을 만든다(plans/120 F-2).

    선언 밖 테이블의 컬럼 매핑은 결정적 피벗이 스키마 검증에서 어차피 버린다. 그런 매핑을 유사어
    정확 매칭으로 먼저 확정하지 않도록 EAV 피벗 DB의 서브 테이블 후보를 선언 안으로 좁힌다.
    EAV 선언이 없는 DB는 제한하지 않는다. 선언을 얻지 못했거나 EAV 패턴은 있는데 테이블 선언이
    없는 DB도 제한하지 않고(종전 동작) 그 사실을 로그로 남긴다.

    Args:
        structure_metas: {db_id: 구조 선언} — 선언이 있는 DB만 담긴다
        db_ids: 매칭 대상 DB 목록

    Returns:
        {db_id: 선언 테이블 집합} — 제한을 적용할 DB만 담긴다
    """
    scope: dict[str, frozenset[str]] = {}
    undeclared: list[str] = []
    eav_without_tables: list[str] = []
    for db_id in db_ids:
        meta = (structure_metas or {}).get(db_id)
        if meta is None:
            undeclared.append(db_id)
            continue
        tables = eav_declared_tables(meta)
        if tables is not None:
            scope[db_id] = tables
        elif schema_uses_eav_metric_pivot({db_id: meta}):
            eav_without_tables.append(db_id)
    if undeclared:
        logger.info(
            "서브 테이블 후보 제한 미적용 — 구조 선언을 얻지 못함(종전 동작 · plans/120 F-2): %s",
            undeclared,
        )
    if eav_without_tables:
        logger.info(
            "서브 테이블 후보 제한 미적용 — EAV 패턴은 있으나 테이블 선언(allowed_tables)이 없음"
            "(종전 동작 · plans/120 F-2): %s",
            eav_without_tables,
        )
    return scope


# === 3-Step Mapping Results ===


class MappingResult:
    """3단계 매핑의 통합 결과."""

    def __init__(self) -> None:
        self.column_mapping: dict[str, str | None] = {}
        self.db_column_mapping: dict[str, dict[str, str]] = {}
        self.mapping_sources: dict[str, str] = {}
        self.mapped_db_ids: list[str] = []


def resolve_fallback_db_id(
    priority_db_ids: list[str],
    active_db_ids: list[str] | None,
    all_db_synonyms: dict[str, dict[str, list[str]]] | None = None,
) -> str:
    """EAV 매핑 등에서 사용할 폴백 DB ID를 결정한다.

    우선순위: priority_db_ids[0] > active_db_ids[0] > all_db_synonyms 첫 번째 키 > "_default"

    Args:
        priority_db_ids: 우선순위 DB 목록
        active_db_ids: 활성 DB ID 목록
        all_db_synonyms: DB별 synonyms (키가 db_id)

    Returns:
        유효한 DB 식별자 문자열
    """
    if priority_db_ids:
        return priority_db_ids[0]
    if active_db_ids:
        return active_db_ids[0]
    if all_db_synonyms:
        first_key = next(iter(all_db_synonyms), None)
        if first_key:
            return first_key
    return "_default"


# === Helper: Extract field names from template ===


def extract_field_names(template_structure: dict[str, Any]) -> list[str]:
    """양식 구조에서 필드명을 추출한다.

    Args:
        template_structure: 양식 구조 정보

    Returns:
        필드명 목록 (중복 제거)
    """
    names: list[str] = []
    seen: set[str] = set()

    file_type = template_structure.get("file_type", "")

    # Excel: sheets[*].headers
    if file_type == "xlsx":
        for sheet in template_structure.get("sheets", []):
            for header in sheet.get("headers", []):
                if header and header not in seen:
                    names.append(header)
                    seen.add(header)

    # Word: placeholders + tables[*].headers
    elif file_type in ("docx", "doc"):
        for ph in template_structure.get("placeholders", []):
            if ph and ph not in seen:
                names.append(ph)
                seen.add(ph)
        for table in template_structure.get("tables", []):
            for header in table.get("headers", []):
                if header and header not in seen:
                    names.append(header)
                    seen.add(header)

    return names


# === Step 1: Hint Mapping ===


def apply_hint_mapping(
    remaining: set[str],
    hints: list[dict[str, Any]],
    all_db_synonyms: dict[str, dict[str, list[str]]],
    result: MappingResult,
    priority_db_ids: list[str] | None = None,
) -> None:
    """프롬프트 힌트로 매핑을 수행한다.

    Args:
        remaining: 아직 매핑되지 않은 필드 set
        hints: field_mapping_hints 목록
        all_db_synonyms: DB별 synonyms (db_id 결정용)
        result: 매핑 결과 객체
    """
    for hint in hints:
        field = hint.get("field", "")
        if field not in remaining:
            continue

        column = hint.get("column")
        if not column:
            continue

        db_id = hint.get("db_id")
        if not db_id:
            # If user supplied priority DB list, use its first element as default DB
            if priority_db_ids:
                db_id = priority_db_ids[0]
            else:
                db_id = _find_db_for_column(column, all_db_synonyms)

        if db_id:
            result.db_column_mapping.setdefault(db_id, {})[field] = column
            result.mapping_sources[field] = "hint"
            remaining.discard(field)


def _find_db_for_column(
    column: str,
    all_db_synonyms: dict[str, dict[str, list[str]]],
) -> str | None:
    """컬럼명이 속한 DB를 synonyms에서 찾는다.

    Args:
        column: table.column 또는 column 형식
        all_db_synonyms: DB별 synonyms

    Returns:
        db_id 또는 None
    """
    for db_id, synonyms in all_db_synonyms.items():
        for col_key in synonyms:
            if col_key == column or col_key.endswith(f".{column}"):
                return db_id
    return None
