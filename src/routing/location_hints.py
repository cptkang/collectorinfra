"""원문 위치/DB 힌트(`target_db_hints`) → 활성 DB 결정적 해소의 **단일 출처** (D-065 계열).

폼필(`nodes.field_mapper`) · 1·2단 핸들러(`orchestration.subagents._apply_turn_hint_pinning`) ·
3단 라우터(`routing.semantic_router`)가 같은 함수를 쓴다(경로별 사본 금지 · D-053).
종전 정의처는 `src/nodes/field_mapper.py`였으나, 3단 라우터가 infrastructure 계층
(`src/routing/`)이라 application(`src/nodes/`)을 임포트할 수 없어(계층 규칙 — `arch_check`)
본문을 이 모듈로 **옮겼다**(plans/113 F-1). `field_mapper`는 같은 이름으로 재노출한다.
핀 대체 target의 정제 질의(`strip_location_terms`, 종전 `orchestration.subagents`)도 같은
이유로 옮겼다 — 두 함수 모두 동작 불변이다.

D-004 경계: 라우팅 의도 분류가 아니라 **사용자가 명시한** 위치/DB 힌트의 결정적 해소에만 쓴다.
원문을 새로 스캔하지 않는다 — 입력 파서가 만든 `target_db_hints`만 입력으로 받는다.

계층: infrastructure(`src/routing/`).
"""

from __future__ import annotations

import logging
from typing import Any

from src.routing.domain_config import get_domain_by_id
from src.routing.registry import get_registry
from src.utils.query_gen_common import LOCATION_HINT_TERMS

logger = logging.getLogger(__name__)

# 지역/존 변별 토큰 — 이게 hint에 있으면 특정 DB를 가리킨다.
# 아래 3개 표는 전부 `config/db_registry.yaml` 파생이다(Plan 67 R2 — 모듈별 사본 금지).
# D-004 경계: 라우팅 의도 분류가 아니라 사용자 명시 힌트의 결정적 해소에만 쓴다.
_REGION_HINT_TOKENS = get_registry().location_terms()
# 제품명 단독 토큰 — 같은 제품군 DB에 공통이라 지역 변별력이 없다. 지역 토큰과 함께 있으면
# priority 확대(예: "폴스타"가 "은행 폴스타"에 부분매칭돼 b0를 끌어들임)를 유발하므로 제거 대상.
_GENERIC_DB_TOKENS = get_registry().product_terms()


# db_id별로 그 DB를 "배제"하는 경쟁 지역 토큰(같은 제품군의 다른 DB를 배타 지목하는 표면어).
# 어떤 hint가 이 토큰을 포함하면 그 hint는 해당 db_id를 가리키지 않는다(다른 존을 지목).
_DB_EXCLUDING_REGIONS: dict[str, tuple[str, ...]] = get_registry().excluding_region_terms()


def _is_generic_only_hint(hint: str) -> bool:
    """제품명(폴스타 등)만 있고 지역 변별 토큰이 없는 hint인지 판정한다."""
    low = hint.strip().lower()
    if not any(g in low for g in _GENERIC_DB_TOKENS):
        return False
    return not any(r in low for r in _REGION_HINT_TOKENS)


def _hint_excludes_db(hint: str, db_id_lower: str) -> bool:
    """이 hint가 다른 존을 지목해 db_id를 배제하는지 판정한다(hint 단위)."""
    regions = _DB_EXCLUDING_REGIONS.get(db_id_lower)
    if not regions:
        return False
    return any(region in hint for region in regions)


# 상호 배타 지역 그룹 — 한 hint에 서로 다른 그룹이 함께 들어오면(예: "공동존 김포/여의도")
# hint 단위 배제가 모든 DB를 전멸시킨다(gp는 '여의도'에, yd는 '김포'에, b0는 둘 다에 배제
# → 빈 priority → 폴백 오판. 라이브 실측 2026-07-29: 은행존 선택). 지역별로 분해한다.
_EXCLUSIVE_REGION_GROUPS: tuple[tuple[str, ...], ...] = (
    ("김포",),
    ("여의도",),
    ("은행존", "은행", "레거시"),
)


def _split_multi_region_hint(hint: str) -> list[str]:
    """한 hint에 상호 배타 지역이 2개 이상이면 지역 토큰별 hint로 분해한다(결정적).

    예: "공동존 김포/여의도" → ["김포", "여의도"], "김포와 여의도" → ["김포", "여의도"].
    지역이 0~1개면 원본 그대로(기존 동작 불변).
    """
    found: list[str] = []
    for group in _EXCLUSIVE_REGION_GROUPS:
        token = next((t for t in group if t in hint), None)
        if token:
            found.append(token)
    if len(found) < 2:
        return [hint]
    return found


def resolve_priority_db_ids(
    target_db_hints: list[str],
    active_db_ids: list[str],
) -> list[str]:
    """target_db_hints의 DB명/별칭을 active_db_ids에 매핑하여 우선순위 DB ID 목록을 반환한다.

    지역 배제는 **hint 단위**로 평가한다. 여러 hint가 서로 다른 존을 지목하면
    (예: ["은행 폴스타", "공동존 김포 폴스타"] → [b0, gp]) 각 hint가 자신이 지목한
    DB만 선택하도록 하여, 한 hint의 경쟁 지역이 다른 hint가 지목한 DB를 배제하지
    않게 한다(D-065 후속2 회귀). 배제를 전체 hint에 걸쳐 판정하면 양 DB가 모두
    상대 hint의 지역 토큰에 걸려 빈 priority가 됐다.
    """
    if not target_db_hints:
        return []

    priority_set = set()
    normalized_hints = [hint.strip().lower() for hint in target_db_hints if hint.strip()]
    # 복수 지역이 한 hint에 든 경우(예: "공동존 김포/여의도") 지역별로 분해 —
    # hint 단위 배제의 상호 전멸을 방지한다(라이브 실측 2026-07-29 FIX-14).
    normalized_hints = [
        part for hint in normalized_hints for part in _split_multi_region_hint(hint)
    ]

    # 지역(공동존/김포/여의도/은행 등)이 명시된 경우, 제품명 단독 토큰("폴스타")은 변별력이 없어
    # 오히려 b0("은행 폴스타") 등을 부분매칭으로 끌어들인다(D-065 후속). 지역 토큰이 있으면
    # 제품명 단독 hint를 제거해 지역이 우선하도록 한다. 지역 토큰이 전혀 없으면(순수 "폴스타") 유지.
    has_region = any(
        any(r in h for r in _REGION_HINT_TOKENS) for h in normalized_hints
    )
    if has_region:
        filtered = [h for h in normalized_hints if not _is_generic_only_hint(h)]
        if filtered:
            normalized_hints = filtered

    for db_id in active_db_ids:
        db_id_lower = db_id.lower()

        # 이 db_id를 배제하지 않는(=다른 존을 지목하지 않는) hint만 매칭 후보로 사용한다.
        candidate_hints = [
            h for h in normalized_hints if not _hint_excludes_db(h, db_id_lower)
        ]
        if not candidate_hints:
            continue

        # 1. raw db_id와 직접 비교 (대소문자 무시)
        if db_id_lower in candidate_hints:
            priority_set.add(db_id)
            continue

        # 2. 별칭(aliases)과 비교 (부분 일치 포함)
        domain_cfg = get_domain_by_id(db_id)
        if domain_cfg:
            for alias in domain_cfg.aliases:
                alias_lower = alias.strip().lower()
                for hint in candidate_hints:
                    if hint == alias_lower or hint in alias_lower or alias_lower in hint:
                        priority_set.add(db_id)
                        break
                if db_id in priority_set:
                    break

    # 원래 active_db_ids의 순서를 유지하면서 필터링
    return [db_id for db_id in active_db_ids if db_id in priority_set]


def strip_location_terms(text: str) -> str:
    """질의 텍스트에서 위치/제품명 토큰을 제거한다(핀 대체 target의 정제 질의용).

    classify_dbs의 sub_query_context(위치 제거 정제 질의)가 없는 합성 target에 원문을
    그대로 넣으면 위치어가 SQL WHERE로 누출될 수 있어(과거 GROUP_PATH ILIKE '김포' 사례),
    위치·제품명 토큰만 결정적으로 걷어낸다. 긴 토큰부터 제거해 부분 잔재("은행존"→"존")를 막는다.
    1·2단(`subagents._apply_turn_hint_pinning`)·3단(`semantic_router`)이 이 함수 하나를 쓴다
    (종전 정의처 `orchestration.subagents` — plans/113 F-1로 이동, 동작 불변).
    """
    stripped = text or ""
    tokens = sorted(
        (*LOCATION_HINT_TERMS, "폴스타", "polestar"), key=len, reverse=True
    )
    for token in tokens:
        stripped = stripped.replace(token, " ")
    return " ".join(stripped.split())


# ──────────────────────────────────────────────
# 대상 DB 결정적 고정 — 3단 라우터 · 1·2단 핸들러 공용 판정 (plans/113 F-1 · F-2)
# ──────────────────────────────────────────────

def _has_region(hint: str) -> bool:
    """힌트가 지역/존 변별 토큰을 담는지 — 제품명 단독 힌트("폴스타")는 task 범위 근거가 아니다."""
    low = hint.strip().lower()
    return any(r in low for r in _REGION_HINT_TOKENS)


def task_hint_scope(hints: list[str], task_query: str) -> list[str]:
    """복합 계획의 한 task가 원문 위치 힌트 중 **어느 부분**을 가리키는지 결정적으로 고른다 (F-2).

    원문 힌트 우선(2026-07-16 실측 — LLM이 직전 턴 위치를 task 질의에 병합해 오라우팅)을 깨지
    않으려고, task 질의는 **원문 힌트 안에서 고르는 근거**로만 쓴다. 원문에 없는 위치어는
    task 질의에 있어도 무시한다.

    1. 원문 힌트(지역 토큰 포함) 문자열이 task 질의에 그대로 있으면 그 힌트들 ("김포 CPU top10과
       여의도 메모리 top10" → task별 ["김포"] / ["여의도"] · 복합 표현 "공동존 김포"도 그대로)
    2. 없으면 원문 힌트에 들어 있는 레지스트리 위치 표면어 중 task 질의에도 있는 것
       (원문 "김포와 여의도"가 한 힌트로 뽑혔을 때 task "김포 CPU" → ["김포"])
    3. 둘 다 없으면 원문 힌트 전체(위치어가 없는 task — "공동존 CPU와 메모리 top10"의 두 task)

    Args:
        hints: 이번 턴 원문 위치/DB 힌트(`parsed_requirements.target_db_hints`)
        task_query: 이번 task 질의(계획 단계 `sub_query`)

    Returns:
        이 task의 해소 입력 힌트 목록(원문 힌트의 부분집합 또는 그 안의 위치 표면어)
    """
    text = (task_query or "").lower()
    region_hints = [h for h in hints if _has_region(h)]
    literal = [h for h in region_hints if h.strip().lower() in text]
    if literal:
        return literal
    original = " ".join(h.lower() for h in region_hints)
    terms = [t for t in _REGION_HINT_TOKENS if t.lower() in original and t.lower() in text]
    return terms or list(hints)


def pin_targets_to_hints(
    targets: list[dict[str, Any]],
    hints: list[str],
    active_db_ids: list[str],
    *,
    fill_query: str,
    task_query: str | None = None,
    zone_group_exclusive: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """이번 턴 원문 위치/DB 힌트로 대상 DB 집합을 결정적으로 고정한다 — 사다리 전 단 공용 규칙.

    3단 라우터(`semantic_router._pin_turn_location_hints`)와 1·2단 핸들러
    (`subagents._apply_turn_hint_pinning`)가 이 함수 하나를 쓴다(D-053 · D-066).

    규칙:
    1. 해소(`resolve_priority_db_ids`) 집합의 DB는 전부 대상에 넣는다. 분류 결과에 같은 DB가
       있으면 그 항목을 **그대로** 재사용하고(정제 질의·표지 — 소유 교정이 직접 지정 항목을
       건너뛰므로 표지를 새로 달지 않는다), 없으면 위치어를 걷어낸 `fill_query`로 채운 항목
       (`user_specified=True`)을 만든다.
    2. 해소 집합에 존 그룹 DB가 있으면 분류 결과의 다른 존 그룹 DB는 뺀다("공동존 김포" → gp).
    3. **존 그룹이 없는 DB**(존 없이 한 시스템이 전부를 관리하는 DB)는 분류 결과를 그대로 둔다 —
       존 힌트로 좁힌다는 말이 성립하지 않는다(plans/95 W-10 방향 · 종전 1·2단의 전량 탈락 교정).
    4. `task_query`(복합 계획의 task 질의)를 주면 `task_hint_scope`로 그 task가 가리키는 원문 힌트
       부분집합만 해소한다. 결과는 원문 전체 해소 집합 **안에서만** 좁힌다(넓히지 않는다).
    5. `zone_group_exclusive`(D-143 후속3)이고 원문 전체 해소가 두 존 그룹 이상에 걸치면 고정하지
       않는다 — 원문 혼합 지목은 라우트 pre-gate가 먼저 끝내므로 여기 오는 것은 제품명 단독·부분
       별칭 힌트뿐이다.
    6. 힌트 없음·해소 0건이면 입력 그대로다.

    Args:
        targets: 분류(LLM) 결과 대상 목록
        hints: 원문 위치/DB 힌트
        active_db_ids: 활성 DB 목록(해소 결과 순서의 기준)
        fill_query: 새로 채우는 항목의 정제 질의 원료
        task_query: 복합 계획 task 질의(None이면 원문 전체 힌트)
        zone_group_exclusive: 존 그룹 상호배타 설정

    Returns:
        (고정이 적용된 대상 목록 — 미적용이면 입력 그대로, 고정 여부)
    """
    clean = [str(h) for h in hints if str(h).strip()] if isinstance(hints, list) else []
    if not clean:
        return targets, False
    pinned = resolve_priority_db_ids(clean, active_db_ids)
    if not pinned:
        return targets, False

    reg = get_registry()
    groups = {g for g in (reg.zone_group_of(d) for d in pinned) if g is not None}
    if zone_group_exclusive and len(groups) >= 2:
        logger.info(
            "위치 힌트 %s가 존 그룹 %d개(%s)로 해소 — 상호배타 설정이라 고정하지 않는다",
            clean, len(groups), sorted(groups),
        )
        return targets, False
    if task_query is not None:
        scope = task_hint_scope(clean, task_query)
        narrowed = [d for d in resolve_priority_db_ids(scope, active_db_ids) if d in pinned]
        if narrowed and narrowed != pinned:
            logger.info("task 위치 범위로 고정 좁힘: task=%r scope=%s → %s", task_query[:80],
                        scope, narrowed)
            pinned = narrowed
            groups = {g for g in (reg.zone_group_of(d) for d in pinned) if g is not None}

    pinned_set = set(pinned)
    by_id = {t.get("db_id"): t for t in targets if isinstance(t, dict)}
    final: list[dict[str, Any]] = []
    for db_id in pinned:
        existing = by_id.get(db_id)
        if existing:
            final.append(existing)
        else:
            final.append({
                "db_id": db_id,
                "relevance_score": 1.0,
                "sub_query_context": strip_location_terms(fill_query),
                "user_specified": True,
                "reason": "이번 턴 원문 위치/DB 힌트 결정적 해소(target_db_hints)",
            })
    dropped: list[str] = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        target_id = str(t.get("db_id") or "")
        if target_id in pinned_set:
            continue
        if groups and reg.zone_group_of(target_id) is not None:
            dropped.append(target_id)
            continue
        final.append(t)
    logger.info(
        "이번 턴 위치 힌트 결정적 DB 고정: hints=%s → %s (분류 제외분=%s)",
        clean, [t["db_id"] for t in final], dropped or "없음",
    )
    return final, True
