"""스키마 정본에서 dimension/measure 카탈로그를 생성한다 (Plan 67 R1).

지식 4중 복제(프롬프트·조립기·db_profiles·semantic_models) 중 **semantic_models 카탈로그**를
정본에서 파생시키는 모듈이다. 정본은 두 갈래다:

    구조 정본  : 스키마 캐시 ``structure_meta``(= ``config/db_profiles/{db_id}.yaml``)
                 — EAV 패턴 구성, ``known_attributes``(속성·설명·유사어), ``column_synonyms``,
                   ``alarm_allowed_tables``
    큐레이션   : ``config/knowledge/{db_id}/catalog.yaml``
                 — 구조에서 파생 불가능한 운영 판단만(수록 대상 선별, 별칭 가감 delta,
                   패턴 B measure, 패턴 C 조인, 계층 taxonomy). 구조 정본과 **겹치는 데이터는
                   두지 않는다.**

계층 taxonomy(Plan 67 N4 / D-133): 큐레이션 ``taxonomy``에 상위어→하위 항목을 선언하면
생성기가 각 항목에 ``parent``를 스탬프하고 정규화된 ``taxonomy`` 블록을 카탈로그에 싣는다.
평면 동의어의 precision 붕괴(상위어가 하위어 하나로 정확 매칭되는 사고) 완화용이며,
**핵심 용어부터 단계화**한다 — 전면 조화는 ontology drift 부채가 된다
(``docs/standardization_literature_review.md`` §3.6-⑤·§3.7).

별칭은 프로필 유사어에서 파생하고 큐레이션은 ``alias_deny``/``alias_extra`` **delta로만** 적용한다
(전체 치환이면 "프로필에 별칭 1건 추가 → 카탈로그 자동 반영"이 깨져 R1 목표가 무효가 된다).

계층: infrastructure. **DB-agnostic 공용 계층이므로 특정 제품 리터럴을 코드에 두지 않는다**
(D-088) — 테이블·resource_type·한국어 별칭은 전부 structure_meta/오버라이드 데이터에서 온다.
Redis 미가용 환경을 고려해 ``build_catalog``는 입력을 주입받는 순수 함수이며 I/O가 없다.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# EAV 속성의 resource_type 표기 — 구조화 키(`resource_type:`) 미이관 프로필용 폴백.
# 신규/이관 프로필은 명시 키를 쓰며, 이 정규식은 Redis에 남은 구프로필 캐시 호환용으로만 남는다.
_LEGACY_RESOURCE_TYPE_RE = re.compile(r"\[resource_type:\s*([^\]/\s]+)")

_DEFAULT_KNOWLEDGE_DIR = os.path.join("config", "knowledge")
_DEFAULT_PROFILE_DIR = os.path.join("config", "db_profiles")


# ──────────────────────────────────────────────
# 오버라이드 로딩 (오버레이 체인)
# ──────────────────────────────────────────────

def _read_yaml(path: str) -> Optional[dict]:
    """YAML 파일을 dict로 읽는다(없거나 실패하면 None)."""
    if not os.path.exists(path):
        return None
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:  # noqa: BLE001 — 로드 실패는 오버라이드 없음으로 강등
        logger.warning("카탈로그 오버라이드 로드 실패 (%s): %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def load_structure_profile(
    db_id: str, *, profiles_dir: str = _DEFAULT_PROFILE_DIR
) -> Optional[dict]:
    """``config/db_profiles/{db_id}.yaml`` 구조 선언을 읽는다(없으면 None).

    스키마 캐시(Redis)의 ``structure_meta``와 동일한 내용의 파일 원천이다 — 캐시 미가용 환경과
    오프라인 검증(diff 리포트·테스트)에서 카탈로그를 만들 때 쓴다. ``known_attributes``는 원본
    객체 리스트 그대로 두며(평탄화하지 않는다), 카탈로그 생성기가 두 형태를 모두 읽는다.

    Args:
        db_id: DB 식별자
        profiles_dir: 프로필 디렉터리 경로

    Returns:
        구조 선언 dict 또는 None
    """
    return _read_yaml(os.path.join(profiles_dir, f"{db_id}.yaml"))


def deep_merge(base: Any, overlay: Any) -> Any:
    """dict를 재귀 병합한다(overlay 우선). dict가 아닌 값은 overlay가 통째로 대체한다.

    카탈로그 오버라이드 상속(``extends``)에 쓰며, db_profiles 공통 베이스 + db별 diff 오버레이
    병합도 같은 규칙으로 처리할 수 있다(Plan 67 R1-5 — YAML 분할은 실익 부족으로 보류).
    """
    if isinstance(base, dict) and isinstance(overlay, dict):
        merged = dict(base)
        for key, value in overlay.items():
            merged[key] = deep_merge(merged.get(key), value) if key in merged else value
        return merged
    return overlay


def load_knowledge_overrides(
    db_id: str,
    *,
    knowledge_dir: str = _DEFAULT_KNOWLEDGE_DIR,
    _seen: Optional[set[str]] = None,
) -> dict:
    """``config/knowledge/{db_id}/catalog.yaml``을 오버레이 체인까지 병합해 로드한다.

    ``extends: [_base]``로 상위 오버라이드를 상속한다(뒤에 오는 항목이 우선, 자기 자신이 최우선).
    파일이 없으면 빈 dict — 오버라이드는 선택 사항이다.

    Args:
        db_id: DB 식별자(디렉터리명)
        knowledge_dir: 큐레이션 루트 경로
        _seen: 순환 상속 방지용 내부 인자

    Returns:
        병합된 오버라이드 dict
    """
    seen = _seen if _seen is not None else set()
    if db_id in seen:
        logger.warning("카탈로그 오버라이드 순환 상속 감지: %s", db_id)
        return {}
    seen.add(db_id)

    own = _read_yaml(os.path.join(knowledge_dir, db_id, "catalog.yaml")) or {}
    merged: dict = {}
    parents = own.get("extends") or []
    if isinstance(parents, str):
        parents = [parents]
    for parent in parents:
        merged = deep_merge(merged, load_knowledge_overrides(
            str(parent), knowledge_dir=knowledge_dir, _seen=seen))
    merged = deep_merge(merged, {k: v for k, v in own.items() if k != "extends"})
    return merged


# ──────────────────────────────────────────────
# 구조 정본 파싱
# ──────────────────────────────────────────────

def _eav_pattern(structure_meta: dict) -> dict:
    """structure_meta에서 EAV 패턴 dict를 찾는다(없으면 빈 dict)."""
    for pattern in (structure_meta or {}).get("patterns", []) or []:
        if isinstance(pattern, dict) and pattern.get("type") == "eav":
            return pattern
    return {}


def _attribute_entries(eav_pattern: dict) -> list[dict]:
    """EAV 속성 객체 리스트를 반환한다.

    수동 프로필 로더는 ``known_attributes``를 문자열 리스트로 평탄화하고 원본 객체를
    ``known_attributes_detail``에 보존하므로 detail을 우선 읽는다. 이 우선순위가 없으면 실 런타임
    (수동 프로필 로드)에서 속성 메타가 항상 비어 폼필 결정적 피벗이 발동하지 않고 LLM 폴백으로
    떨어진다(2026-07-13 실측, 단일·멀티 공통).
    """
    attrs = eav_pattern.get("known_attributes_detail") or eav_pattern.get("known_attributes") or []
    return [a for a in attrs if isinstance(a, dict)]


def attribute_resource_types(structure_meta: Optional[dict]) -> dict[str, str]:
    """EAV 속성명(대문자) → resource_type 맵을 구조 정본에서 만든다.

    구조화 키 ``resource_type``을 우선 읽고, 없으면 설명 문자열의 ``[resource_type: X]`` 표기를
    파싱한다(미이관 프로필·구캐시 호환 폴백). 설명에 복수 표기가 있으면 **첫 번째**만 쓴다.

    Args:
        structure_meta: 구조 메타(=수동 프로필) dict. None/빈 값이면 빈 맵.

    Returns:
        {속성명 대문자: resource_type}
    """
    out: dict[str, str] = {}
    if not structure_meta:
        return out
    for pattern in structure_meta.get("patterns", []) or []:
        if not isinstance(pattern, dict) or pattern.get("type") != "eav":
            continue
        for attr in _attribute_entries(pattern):
            name = (attr.get("name") or "").strip()
            if not name:
                continue
            rt = (attr.get("resource_type") or "").strip()
            if not rt:
                m = _LEGACY_RESOURCE_TYPE_RE.search(attr.get("description") or "")
                rt = m.group(1).strip() if m else ""
            if rt:
                out[name.upper()] = rt
    return out


# ──────────────────────────────────────────────
# 카탈로그 생성
# ──────────────────────────────────────────────

def _curate_aliases(
    derived: list[str], deny: list[str], extra: list[str]
) -> list[str]:
    """파생 별칭에 큐레이션 delta를 적용한다(중복 제거, 순서 보존)."""
    denied = set(deny or [])
    out: list[str] = []
    for alias in list(derived or []) + list(extra or []):
        if alias in denied or alias in out:
            continue
        out.append(alias)
    return out


def _direct_dimensions(
    eav_pattern: dict,
    column_synonyms: dict,
    pattern_a_over: dict,
) -> list[dict]:
    """엔티티 직접 컬럼 dimension을 만든다(별칭 원천은 column_synonyms)."""
    entity_table = eav_pattern.get("entity_table") or ""
    entity_rt = pattern_a_over.get("entity_resource_type")
    deny_map = pattern_a_over.get("alias_deny") or {}
    extra_map = pattern_a_over.get("alias_extra") or {}

    dims: list[dict] = []
    for spec in pattern_a_over.get("direct_dimensions") or []:
        if not isinstance(spec, dict):
            continue
        column = spec.get("column") or spec.get("name")
        name = spec.get("name") or column
        if not column:
            continue
        syn_entry = column_synonyms.get(f"{entity_table}.{column}") or {}
        derived = list(syn_entry.get("words") or [])
        aliases = _curate_aliases(derived, deny_map.get(name), extra_map.get(name))
        dim: dict[str, Any] = {"name": name, "source": "direct", "column": column}
        if entity_rt:
            dim["resource_type"] = entity_rt
        if spec.get("lob"):
            dim["lob"] = True
        dim["aliases"] = aliases
        dims.append(dim)
    return dims


def _eav_dimensions(eav_pattern: dict, pattern_a_over: dict) -> list[dict]:
    """EAV 속성 dimension을 만든다(별칭 원천은 프로필 synonyms, 순서는 프로필 순서)."""
    exclude = set((pattern_a_over.get("attributes") or {}).get("exclude") or [])
    deny_map = pattern_a_over.get("alias_deny") or {}
    extra_map = pattern_a_over.get("alias_extra") or {}
    rt_map = {a.get("name"): a.get("resource_type") for a in _attribute_entries(eav_pattern)}

    dims: list[dict] = []
    for attr in _attribute_entries(eav_pattern):
        name = (attr.get("name") or "").strip()
        if not name or name in exclude:
            continue
        rt = (rt_map.get(name) or "").strip()
        if not rt:
            m = _LEGACY_RESOURCE_TYPE_RE.search(attr.get("description") or "")
            rt = m.group(1).strip() if m else ""
        dim: dict[str, Any] = {"name": name, "source": "eav", "attribute": name}
        if rt:
            dim["resource_type"] = rt
        if attr.get("lob"):
            dim["lob"] = True
        dim["aliases"] = _curate_aliases(
            list(attr.get("synonyms") or []), deny_map.get(name), extra_map.get(name))
        dims.append(dim)
    return dims


def _build_pattern_a(structure_meta: dict, column_synonyms: dict, overrides: dict) -> dict:
    """패턴 A(엔티티 설정 EAV 피벗) 카탈로그를 만든다."""
    eav = _eav_pattern(structure_meta)
    over = overrides.get("pattern_a") or {}
    if not eav:
        return {}

    eav_block: dict[str, Any] = {}
    for key in ("entity_table", "config_table", "attribute_column", "value_column",
                "lob_value_column"):
        if eav.get(key):
            eav_block[key] = eav[key]
    direct_join = eav.get("direct_join") or {}
    join_block = {k: direct_join[k] for k in ("entity_column", "config_column")
                  if direct_join.get(k)}
    if join_block:
        eav_block["direct_join"] = join_block

    pattern_a: dict[str, Any] = {"eav": eav_block}
    pattern_a["dimensions"] = (
        _direct_dimensions(eav, column_synonyms, over) + _eav_dimensions(eav, over)
    )
    if over.get("filterable"):
        pattern_a["filterable"] = list(over["filterable"])
    return pattern_a


def _build_pattern_c(structure_meta: dict, overrides: dict) -> dict:
    """패턴 C(관계형 조인) 카탈로그를 만든다 — 허용 테이블만 구조 정본에서 파생한다."""
    over = overrides.get("pattern_c")
    if not over:
        return {}
    pattern_c = {k: v for k, v in over.items() if k != "allowed_tables_deny"}
    declared = structure_meta.get(over.get("allowed_tables_source") or "alarm_allowed_tables")
    if declared:
        deny = set(over.get("allowed_tables_deny") or [])
        pattern_c["allowed_tables"] = [t for t in declared if t not in deny]
    pattern_c.pop("allowed_tables_source", None)
    return pattern_c


# ──────────────────────────────────────────────
# 계층 taxonomy (Plan 67 N4 / D-133)
# ──────────────────────────────────────────────

def measure_key(resource_type: Any, definition_name: Any) -> str:
    """measure 참조 키 ``resource_type/definition_name``를 만든다(taxonomy 선언 표기와 동일)."""
    return f"{resource_type}/{definition_name}"


def _taxonomy_children_dims(
    names: list, term: str, dim_by_name: dict[str, dict]
) -> list[str]:
    """선언된 하위 dimension을 카탈로그 실재분으로 걸러 parent를 스탬프한다."""
    out: list[str] = []
    for raw in names or []:
        entry = dim_by_name.get(str(raw))
        if entry is None:
            # db별 exclude로 수록되지 않은 속성 — 상위어에서 조용히 뺀다(선언은 공통 정본).
            logger.debug("taxonomy 하위 dimension 미수록으로 제외: %s ⊃ %s", term, raw)
            continue
        existing = entry.get("parent")
        if existing and existing != term:
            # 부모는 하나만 둔다(첫 선언 우선) — 다중 부모는 계층이 아니라 그래프가 된다.
            logger.warning(
                "taxonomy 하위 항목 중복 부모 — 첫 선언 유지: %s (기존 %s, 신규 %s)",
                raw, existing, term,
            )
            continue
        entry["parent"] = term
        out.append(str(entry.get("name")))
    return out


def _taxonomy_children_measures(
    refs: list, term: str, measure_by_key: dict[str, dict]
) -> list[dict]:
    """선언된 하위 measure를 카탈로그 실재분으로 걸러 parent를 스탬프한다."""
    out: list[dict] = []
    for raw in refs or []:
        entry = measure_by_key.get(str(raw))
        if entry is None:
            logger.debug("taxonomy 하위 measure 미수록으로 제외: %s ⊃ %s", term, raw)
            continue
        existing = entry.get("parent")
        if existing and existing != term:
            logger.warning(
                "taxonomy 하위 항목 중복 부모 — 첫 선언 유지: %s (기존 %s, 신규 %s)",
                raw, existing, term,
            )
            continue
        entry["parent"] = term
        out.append({
            "resource_type": entry.get("resource_type"),
            "definition_name": entry.get("definition_name"),
        })
    return out


def _apply_taxonomy(catalog: dict, overrides: dict) -> None:
    """상위어 선언을 카탈로그에 반영한다 — 항목별 ``parent`` 스탬프 + ``taxonomy`` 블록 생성.

    상위어 자체는 선택지가 아니다(카탈로그 항목이 아니라 어휘) — 하위 항목을 가리키는
    이름일 뿐이다. 하위가 하나도 수록되지 않은 상위어는 블록에서 제외한다(빈 상위어로
    모호성 판정이 헛돌지 않게).

    Args:
        catalog: 생성 중인 카탈로그(제자리 수정)
        overrides: 큐레이션 오버라이드(``taxonomy`` 선언 원천)
    """
    declared = overrides.get("taxonomy")
    if not isinstance(declared, dict) or not declared:
        return

    dims = (catalog.get("pattern_a") or {}).get("dimensions") or []
    dim_by_name = {str(d.get("name")): d for d in dims if d.get("name")}
    measures = (catalog.get("pattern_b") or {}).get("measures") or []
    measure_by_key = {
        measure_key(m.get("resource_type"), m.get("definition_name")): m
        for m in measures if isinstance(m, dict)
    }

    taxonomy: dict[str, dict] = {}
    for term, node in declared.items():
        if not isinstance(node, dict):
            continue
        child_dims = _taxonomy_children_dims(
            node.get("dimensions") or [], str(term), dim_by_name)
        child_measures = _taxonomy_children_measures(
            node.get("measures") or [], str(term), measure_by_key)
        if not child_dims and not child_measures:
            continue
        block: dict[str, Any] = {}
        aliases = [str(a) for a in (node.get("aliases") or [])]
        if aliases:
            block["aliases"] = aliases
        if child_dims:
            block["dimensions"] = child_dims
        if child_measures:
            block["measures"] = child_measures
        taxonomy[str(term)] = block

    if taxonomy:
        catalog["taxonomy"] = taxonomy


def build_catalog(
    structure_meta: Optional[dict],
    *,
    db_id: str = "",
    column_synonyms: Optional[dict] = None,
    overrides: Optional[dict] = None,
) -> dict:
    """구조 정본 + 큐레이션 오버라이드에서 dimension/measure 카탈로그를 생성한다.

    I/O 없는 순수 함수다 — 호출자가 structure_meta(스키마 캐시 또는 프로필)와 오버라이드를
    주입한다(Redis 미가용 환경·테스트에서 그대로 사용 가능).

    Args:
        structure_meta: 구조 메타(EAV 패턴·known_attributes·column_synonyms·alarm_allowed_tables)
        db_id: 카탈로그에 기록할 DB 식별자
        column_synonyms: 컬럼 유사어 맵. None이면 structure_meta의 것을 쓴다.
        overrides: ``load_knowledge_overrides`` 결과(큐레이션 delta·패턴 B/C 정의)

    Returns:
        ``{db_id, pattern_a, pattern_b, pattern_c, taxonomy}`` 카탈로그. 원천이 없으면 해당
        패턴은 생략된다(``taxonomy``는 선언이 있을 때만).
    """
    meta = structure_meta or {}
    over = overrides or {}
    synonyms = column_synonyms if column_synonyms is not None else (
        meta.get("column_synonyms") or {})

    catalog: dict[str, Any] = {"db_id": db_id}
    pattern_a = _build_pattern_a(meta, synonyms, over)
    if pattern_a:
        catalog["pattern_a"] = pattern_a
    if over.get("pattern_b"):
        pattern_b = dict(over["pattern_b"])
        # measure dict는 사본으로 싣는다 — taxonomy parent 스탬프가 오버라이드 원본을
        # 오염시키지 않게(오버라이드는 호출자가 재사용할 수 있는 입력이다).
        if pattern_b.get("measures"):
            pattern_b["measures"] = [
                dict(m) if isinstance(m, dict) else m for m in pattern_b["measures"]
            ]
        catalog["pattern_b"] = pattern_b
    pattern_c = _build_pattern_c(meta, over)
    if pattern_c:
        catalog["pattern_c"] = pattern_c
    _apply_taxonomy(catalog, over)
    return catalog
