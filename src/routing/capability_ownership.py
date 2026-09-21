"""답변 영역 소유 판정 — LLM 구조화 출력의 답변 영역만 보고 정본 시스템을 정한다.

plans/102 §3.1 · D-224 ①.

## 입력은 LLM 구조화 출력뿐이다 (D-004)
라우터·분해 LLM이 낸 **답변 영역 코드**(`databases[].capabilities` · `chain` ·
task `capability`)만 본다.
이 모듈의 어떤 함수도 질의 원문을 받지 않는다 — 키워드로 질의를 분류하는 경로가 생길 자리가 없다.

## 판정 단위는 "시스템"이다
레지스트리가 정한다(`DBRegistry.system_of` · `capability_owners`):
- **다중 존 시스템**(솔루션 하나에 DB 여러 개): 소유 교정은 **이미 고른 그 시스템 DB만**
  남기고, 없을 때만 그 시스템의 활성 DB 전체로 넓힌다. 한 존으로 좁히는 일은 이후 기존
  존 게이트(위치 힌트·역질문)가 한다 — 여기서 고정하면 원문의 위치 한정이 사라진다(X-T13).
- **단일 DB 시스템**(존 없음 · DB 하나): 교정은 그 DB로 바꾸는 것이고, 분해 task는 그 DB로 고정한다.

레지스트리에 답변 영역 선언이 없는 DB(`system_of`가 None)는 판정 밖이다 — 교정하지 않는다.
사용자가 DB를 직접 지정한 항목(`user_specified`)도 교정하지 않는다
(라우터 프롬프트의 직접 지정 규칙 유지). 다만 지정 DB가 그 답변 영역의 정본이 아니면
**사유 노트 1건**을 남긴다 — 자산 DB에도 사용률 컬럼이 있어 지정이 빗나가도 결과는 나오므로
오답이 조용하다(권고 C · plans/102 §0.2 R2).

## 켜짐 조건
`ROUTER_CAPABILITY_OWNERSHIP_ENABLED`가 켜졌을 때만 호출된다. 판정은 호출부가 한다 —
이 모듈은 설정을 읽지 않는다(기동 시 1회 해석 원칙).

계층: infrastructure (`src.routing`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from src.routing.registry import DBRegistry, get_registry
from src.utils.prior_dependency import NOTE_OWNERSHIP, NOTE_ROUTING_FALLBACK

logger = logging.getLogger(__name__)

# 노트 사유 코드 — 응답 경과·API(`dependency_notes`)에 그대로 실린다.
REASON_OWNER_CORRECTED = "owner_corrected"   # 선택 DB를 정본 시스템으로 교정
REASON_OWNER_INACTIVE = "owner_inactive"     # 정본 시스템이 비활성이라 교정 불가
#: 사용자가 DB를 직접 지정했고 그 DB가 정본이 아니다 — **교정하지 않고** 사실만 알린다(권고 C).
REASON_OWNER_USER_SPECIFIED = "owner_user_specified"
REASON_LLM_ERROR = "llm_error"               # 분류 LLM 호출 실패 → 첫 활성 DB 폴백
REASON_NO_CLASSIFICATION = "no_classification"  # 유효한 분류 결과 없음 → 첫 활성 DB 폴백


# ──────────────────────────────────────────────
# 카탈로그 · 출력 정제
# ──────────────────────────────────────────────

def known_capability_codes(registry: DBRegistry | None = None) -> frozenset[str]:
    """레지스트리 답변 영역 카탈로그의 코드 집합(LLM 출력 대조 기준)."""
    reg = registry or get_registry()
    return frozenset(spec.code for spec in reg.capability_specs())


def sanitize_capability_list(raw: Any, known: frozenset[str]) -> tuple[list[str], list[str]]:
    """LLM이 낸 답변 영역 목록에서 카탈로그 코드만 남긴다(순서 유지 · 중복 제거).

    Args:
        raw: LLM 출력 값(목록이 정상 · 문자열 하나도 받는다)
        known: 카탈로그 코드 집합

    Returns:
        (남긴 코드 목록, 버린 원값 목록) — 버린 값은 호출부가 탈락 사유로 보고한다.
    """
    if raw is None:
        return [], []
    items = [raw] if isinstance(raw, str) else raw
    if not isinstance(items, (list, tuple)):
        return [], [repr(raw)]
    kept: list[str] = []
    unknown: list[str] = []
    for item in items:
        if not isinstance(item, str):
            unknown.append(repr(item))
            continue
        code = item.strip()
        if not code:
            continue
        if code not in known:
            unknown.append(item)
        elif code not in kept:
            kept.append(code)
    return kept, unknown


def sanitize_capability_code(raw: Any, known: frozenset[str]) -> str:
    """분해 task의 답변 영역 하나를 정제한다.

    카탈로그 밖·형식 오류는 빈 문자열(= 소유 적용 없음)이다.
    """
    if isinstance(raw, str) and raw.strip() in known:
        return raw.strip()
    return ""


# ──────────────────────────────────────────────
# 소유표 렌더 (프롬프트 가이드 재료 — 레지스트리 파생, 사본 금지 D-053)
# ──────────────────────────────────────────────

def _active_system_db_ids(
    reg: DBRegistry, system: str, active_db_ids: Sequence[str]
) -> tuple[str, ...]:
    active = set(active_db_ids)
    return tuple(d for d in reg.system_db_ids(system) if d in active)


def owned_capability_rows(
    active_db_ids: Sequence[str], *, registry: DBRegistry | None = None
) -> list[tuple[str, str, str, tuple[str, ...]]]:
    """소유표 행 — (코드, 설명, 정본 시스템 라벨, 그 시스템의 활성 db_id).

    **활성 DB가 있는 시스템이 소유한 영역만** 카탈로그 선언 순서로 낸다. 활성 DB가 없는 시스템의
    영역을 보이면 LLM이 고를 수 없는 DB를 가리키게 된다.
    """
    reg = registry or get_registry()
    rows: list[tuple[str, str, str, tuple[str, ...]]] = []
    for spec in reg.capability_specs():
        owners = reg.capability_owners(spec.code)
        if not owners:
            continue
        dbs = _active_system_db_ids(reg, owners[0], active_db_ids)
        if not dbs:
            continue
        rows.append((spec.code, spec.label, reg.system_label(owners[0]), dbs))
    return rows


def active_owner_system_count(
    active_db_ids: Sequence[str], *, registry: DBRegistry | None = None
) -> int:
    """활성 DB가 있는 소유 시스템 수 — 2 이상이어야 교차 시스템 질의(체인)가 성립한다."""
    reg = registry or get_registry()
    active_systems = {reg.system_of(db_id) for db_id in active_db_ids}
    owners = {o for spec in reg.capability_specs() for o in reg.capability_owners(spec.code)}
    return len(owners & {s for s in active_systems if s is not None})


def render_ownership_rows(
    active_db_ids: Sequence[str],
    *,
    with_db_ids: bool,
    registry: DBRegistry | None = None,
) -> str:
    """소유표를 마크다운 표 행으로 렌더한다(머리글은 프롬프트 모듈 소유).

    Args:
        active_db_ids: 활성 DB 목록(라우터는 프롬프트에 실리는 도메인 목록)
        with_db_ids: 정본 시스템 칸에 활성 db_id를 병기할지 — DB를 고르는 라우터는 True,
            DB를 고르지 않는 분해 프롬프트는 False
        registry: 레지스트리(미지정 시 정본)
    """
    lines: list[str] = []
    for code, label, owner_label, dbs in owned_capability_rows(active_db_ids, registry=registry):
        owner_cell = f"{owner_label} — {', '.join(dbs)}" if with_db_ids else owner_label
        lines.append(f"| {code} | {label or code} | {owner_cell} |")
    return "\n".join(lines)


def ownership_guidance_rows(
    db_id: str, *, registry: DBRegistry | None = None
) -> tuple[str, str, str] | None:
    """DB 설명 생성용 소유 안내 재료.

    반환: (이 DB 시스템 라벨, 이 시스템 소유 영역 행, 다른 시스템 소유 영역 행).

    설명 생성은 운영자가 비활성 DB에도 돌릴 수 있어 활성 여부로 거르지 않는다. 답변 영역 선언이 없는
    DB는 None(안내 없음 = 현행 프롬프트).
    """
    reg = registry or get_registry()
    system = reg.system_of(db_id)
    if system is None:
        return None
    own: list[str] = []
    other: list[str] = []
    for spec in reg.capability_specs():
        owners = reg.capability_owners(spec.code)
        if not owners:
            continue
        line = f"- {spec.code}: {spec.label or spec.code}"
        if system in owners:
            own.append(line)
        else:
            other.append(f"{line} (정본: {reg.system_label(owners[0])})")
    return reg.system_label(system), "\n".join(own), "\n".join(other)


# ──────────────────────────────────────────────
# 소유 검증 ① — 라우터 결과(대상 DB 목록)
# ──────────────────────────────────────────────

def _capability_label(reg: DBRegistry, code: str) -> str:
    for spec in reg.capability_specs():
        if spec.code == code:
            return f"{code}({spec.label})" if spec.label else code
    return code


def _db_label(reg: DBRegistry, db_id: str) -> str:
    entry = reg.get(db_id)
    return (entry.display_name or db_id) if entry else db_id


def _corrected_note(
    reg: DBRegistry,
    capability: str,
    owner_system: str,
    from_db_ids: Sequence[str],
    to_db_ids: Sequence[str],
    task_id: str | None,
) -> dict[str, Any]:
    from_label = ", ".join(_db_label(reg, d) for d in from_db_ids)
    return {
        "kind": NOTE_OWNERSHIP,
        "task_id": task_id,
        "reason": REASON_OWNER_CORRECTED,
        "detail": (
            f"답변 영역 {_capability_label(reg, capability)}의 정본은 "
            f"{reg.system_label(owner_system)}입니다 "
            f"— {from_label} 선택을 교정했습니다(조회: {', '.join(to_db_ids)})."
        ),
        "capability": capability,
        "from_db_ids": list(from_db_ids),
        "to_db_ids": list(to_db_ids),
    }


def _inactive_note(
    reg: DBRegistry,
    capability: str,
    owner_system: str,
    kept_db_ids: Sequence[str],
    task_id: str | None,
) -> dict[str, Any]:
    kept = ", ".join(_db_label(reg, d) for d in kept_db_ids)
    tail = f" — {kept} 조회를 유지합니다." if kept else "."
    return {
        "kind": NOTE_OWNERSHIP,
        "task_id": task_id,
        "reason": REASON_OWNER_INACTIVE,
        "detail": (
            f"답변 영역 {_capability_label(reg, capability)}의 정본인 "
            f"{reg.system_label(owner_system)}이(가) "
            f"활성화되어 있지 않아 교정하지 못했습니다{tail}"
        ),
        "capability": capability,
        "from_db_ids": list(kept_db_ids),
    }


def _user_specified_note(
    reg: DBRegistry,
    capability: str,
    owner_system: str,
    db_id: str,
    task_id: str | None,
) -> dict[str, Any]:
    """직접 지정 DB가 정본이 아닐 때의 사유 노트 — 교정하지 않았다는 사실을 문구에 담는다."""
    return {
        "kind": NOTE_OWNERSHIP,
        "task_id": task_id,
        "reason": REASON_OWNER_USER_SPECIFIED,
        "detail": (
            f"직접 지정하신 {_db_label(reg, db_id)}은(는) 답변 영역 "
            f"{_capability_label(reg, capability)}의 정본이 아닙니다"
            f"(정본: {reg.system_label(owner_system)}). "
            "지정을 존중해 **교정하지 않고 그대로 조회**했으니 결과 해석에 주의해 주세요."
        ),
        "capability": capability,
        "from_db_ids": [db_id],
        "owner_system": owner_system,
    }


def user_specified_ownership_notes(
    targets: Sequence[dict[str, Any]],
    *,
    registry: DBRegistry | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """직접 지정 대상 중 **정본이 아닌** 것을 사유 노트로 낸다(교정은 하지 않는다 · 권고 C).

    자산 DB에도 사용률 컬럼이 있어 *"ITAM DB에서 CPU 사용률"* 같은 지정은 **결과가 나오므로**
    오답이 조용하다(plans/102 §0.2 R2 · 95 함정 T5). 지정을 뒤집지 않되 침묵하지도 않는다.

    (대상, 답변 영역) 쌍마다 1건이다. 답변 영역이 없거나 선언 없는 DB는 판정 밖이다.
    """
    reg = registry or get_registry()
    notes: list[dict[str, Any]] = []
    for entry in targets:
        if not isinstance(entry, dict) or not entry.get("user_specified"):
            continue
        db_id = str(entry.get("db_id") or "")
        system = reg.system_of(db_id)
        if system is None:
            continue
        for cap in entry.get("capabilities") or []:
            if not isinstance(cap, str):
                continue
            owners = reg.capability_owners(cap)
            if not owners or system in owners:
                continue
            notes.append(_user_specified_note(reg, cap, owners[0], db_id, task_id))
    if notes:
        logger.info(
            "직접 지정 DB가 정본 아님(교정 없음 · task=%s): %s",
            task_id, [(n["from_db_ids"][0], n["capability"]) for n in notes],
        )
    return notes


def enforce_target_ownership(
    targets: list[dict[str, Any]],
    *,
    active_db_ids: Sequence[str],
    registry: DBRegistry | None = None,
    task_id: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """대상 DB마다 LLM이 붙인 답변 영역의 정본 시스템을 확인하고, 어긋나면 정본으로 교정한다.

    - 답변 영역이 빈 항목·직접 지정 항목·선언 없는 DB는 그대로 둔다. 직접 지정 항목이 정본이
      아니면 **교정하지 않고 사유 노트만** 남긴다(권고 C — 침묵 금지).
    - 어긋난 영역은 정본 시스템 DB로 옮긴다 — 이미 고른 그 시스템 DB가 있으면 거기에 붙이고, 없으면
      그 시스템의 활성 DB 전체를 원래 항목 자리에 넣는다(단일 DB 시스템이면 그 DB 하나).
    - 옮길 영역만 가진 항목은 뺀다. 정본 시스템이 비활성이면 옮기지 않고 사유 노트만 남긴다.

    Args:
        targets: 관련도 필터·정렬이 끝난 대상 목록(각 항목 `capabilities` 포함 가능)
        active_db_ids: 활성 DB 목록 — 여기에 없는 소유자는 추가하지 않는다
        registry: 레지스트리(미지정 시 정본)
        task_id: 노트에 실을 task 식별자(라우터 노드는 None)

    Returns:
        (교정된 대상 목록 — 입력은 변경하지 않는다, NOTE_OWNERSHIP 노트 목록)
    """
    reg = registry or get_registry()
    entries = [dict(t) for t in targets if isinstance(t, dict)]
    # 직접 지정은 교정하지 않는다 — 사실만 먼저 알린다(권고 C). 판정 입력은 **입력 목록**이다:
    # 아래 교정 루프가 직접 지정 항목에 영역을 덧붙이는 경우는 그 DB가 정본일 때뿐이다.
    notes: list[dict[str, Any]] = user_specified_ownership_notes(
        entries, registry=reg, task_id=task_id
    )

    i = 0
    while i < len(entries):
        entry = entries[i]
        caps = [c for c in (entry.get("capabilities") or []) if isinstance(c, str)]
        system = reg.system_of(str(entry.get("db_id") or ""))
        if not caps or entry.get("user_specified") or system is None:
            i += 1
            continue

        moved: list[str] = []
        insert_at = i + 1
        for cap in caps:
            owners = reg.capability_owners(cap)
            if not owners or system in owners:
                continue
            owner = owners[0]
            active_owner_dbs = _active_system_db_ids(reg, owner, active_db_ids)
            if not active_owner_dbs:
                notes.append(_inactive_note(reg, cap, owner, [entry["db_id"]], task_id))
                continue
            chosen = [
                e["db_id"] for e in entries
                if reg.system_of(str(e.get("db_id") or "")) == owner
            ]
            to_ids = chosen or list(active_owner_dbs)
            for db_id in to_ids:
                existing = next((e for e in entries if e.get("db_id") == db_id), None)
                if existing is not None:
                    existing_caps = list(existing.get("capabilities") or [])
                    if cap not in existing_caps:
                        existing["capabilities"] = existing_caps + [cap]
                    continue
                entries.insert(insert_at, {
                    "db_id": db_id,
                    "relevance_score": entry.get("relevance_score", 0.5),
                    "sub_query_context": entry.get("sub_query_context", ""),
                    "user_specified": False,
                    "reason": f"답변 영역 소유 교정({cap}): {_db_label(reg, entry['db_id'])} → "
                              f"{reg.system_label(owner)}",
                    "capabilities": [cap],
                })
                insert_at += 1
            moved.append(cap)
            notes.append(_corrected_note(reg, cap, owner, [entry["db_id"]], to_ids, task_id))

        if moved:
            remaining = [c for c in caps if c not in moved]
            if remaining:
                entry["capabilities"] = remaining
            else:
                entries.pop(i)
                continue
        i += 1

    if notes:
        logger.info(
            "답변 영역 소유 교정(task=%s): %s → %s",
            task_id, [t.get("db_id") for t in targets], [e.get("db_id") for e in entries],
        )
    return entries, notes


# ──────────────────────────────────────────────
# 소유 검증 ② — 분해 task
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class CapabilityOwner:
    """분해 task 답변 영역의 정본 시스템."""

    capability: str
    system: str
    label: str
    registered_db_ids: tuple[str, ...]
    active_db_ids: tuple[str, ...]

    @property
    def single_db(self) -> bool:
        """존 없는 단일 DB 시스템인가(고정 대상)."""
        return len(self.registered_db_ids) == 1

    @property
    def pinned_db_id(self) -> str | None:
        """단일 DB 시스템이고 그 DB가 활성이면 고정할 db_id."""
        return self.active_db_ids[0] if self.single_db and self.active_db_ids else None


def resolve_capability_owner(
    capability: str,
    *,
    active_db_ids: Sequence[str],
    registry: DBRegistry | None = None,
) -> CapabilityOwner | None:
    """답변 영역의 정본 시스템을 찾는다. 소유 선언이 없으면 None(소유 적용 없음)."""
    reg = registry or get_registry()
    owners = reg.capability_owners(capability)
    if not owners:
        return None
    system = owners[0]
    return CapabilityOwner(
        capability=capability,
        system=system,
        label=reg.system_label(system),
        registered_db_ids=reg.system_db_ids(system),
        active_db_ids=_active_system_db_ids(reg, system, active_db_ids),
    )


def owner_inactive_note(
    owner: CapabilityOwner, *, task_id: str | None, registry: DBRegistry | None = None
) -> dict[str, Any]:
    """task 답변 영역의 정본 시스템이 비활성이라 대상 교정을 적용하지 못했다는 노트."""
    reg = registry or get_registry()
    return _inactive_note(reg, owner.capability, owner.system, [], task_id)


def restrict_targets_to_owner(
    targets: list[dict[str, Any]],
    owner: CapabilityOwner,
    *,
    sub_query: str,
    task_id: str | None = None,
    registry: DBRegistry | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """다중 존 시스템 소유 task의 대상을 그 시스템 DB 집합으로 **제한만** 한다(X-T13).

    분류·위치 힌트 고정·승계가 끝난 대상에서 소유 시스템 밖 DB를 뺀다(직접 지정 항목은 유지).
    남는 것이 없으면 소유 시스템의 활성 DB 전체로 넓힌다 — 존 한정은 이후 존 게이트 몫이다.

    Args:
        targets: 분류·위치 힌트 고정·승계가 끝난 대상 목록
        owner: task 답변 영역(LLM 구조화 출력)의 정본 시스템
        sub_query: 넓힐 때 새로 만드는 대상의 `sub_query_context` **채움값**일 뿐이다 —
            제한 판정에는 쓰지 않는다(D-004 · 판정 입력은 `owner`와 `targets`의 db_id뿐)
        task_id: 노트에 실을 task 식별자
        registry: 레지스트리(미지정 시 정본)

    Returns:
        (제한된 대상 목록, NOTE_OWNERSHIP 노트 목록 — 뺀 DB도 직접 지정 위반도 없으면 빈 목록)
    """
    reg = registry or get_registry()
    allowed = set(owner.active_db_ids)
    kept = [t for t in targets if t.get("db_id") in allowed or t.get("user_specified")]
    removed = [t for t in targets if t not in kept]
    # 소유 시스템 밖인데 직접 지정이라 남긴 대상 — 교정하지 않았다는 사실을 알린다(권고 C).
    notes = [
        _user_specified_note(
            reg, owner.capability, owner.system, str(t.get("db_id") or ""), task_id
        )
        for t in kept
        if t.get("user_specified")
        and t.get("db_id") not in allowed
        and reg.system_of(str(t.get("db_id") or "")) is not None
    ]
    if not removed:
        return list(targets), notes
    if not kept:
        template = removed[0]
        kept = [{
            "db_id": db_id,
            "relevance_score": template.get("relevance_score", 0.5),
            "sub_query_context": template.get("sub_query_context") or sub_query,
            "user_specified": False,
            "reason": f"답변 영역 소유 교정({owner.capability}): {owner.label} 활성 DB",
        } for db_id in owner.active_db_ids]
    note = _corrected_note(
        reg, owner.capability, owner.system,
        [str(t.get("db_id") or "") for t in removed],
        [str(t.get("db_id") or "") for t in kept],
        task_id,
    )
    logger.info(
        "분해 task 답변 영역 소유 제한(task=%s, %s): %s → %s",
        task_id, owner.capability,
        [t.get("db_id") for t in targets], [t.get("db_id") for t in kept],
    )
    return kept, notes + [note]


# ──────────────────────────────────────────────
# 빈 분류·LLM 실패 폴백 표기 (X-T3)
# ──────────────────────────────────────────────

def routing_fallback_note(
    reason: str,
    *,
    db_id: str,
    cause: str = "",
    task_id: str | None = None,
) -> dict[str, Any]:
    """첫 활성 DB 폴백을 사용자에게 알리는 노트(침묵 강등 금지).

    Args:
        reason: REASON_LLM_ERROR | REASON_NO_CLASSIFICATION
        db_id: 폴백 대상 DB
        cause: 사유 요약(예외 클래스명 등 — 원문 메시지는 로그에만 둔다)
        task_id: 분해 task 식별자(라우터 노드는 None)
    """
    if reason == REASON_LLM_ERROR:
        head = f"조회 대상 시스템 분류에 실패해({cause or '분류 LLM 호출 오류'})"
    else:
        head = "조회 대상 시스템을 분류하지 못해(유효한 분류 결과 없음)"
    return {
        "kind": NOTE_ROUTING_FALLBACK,
        "task_id": task_id,
        "reason": reason,
        "detail": (
            f"{head} 첫 활성 DB({db_id})로 조회했습니다 — 질의에 맞는 시스템이 아닐 수 있습니다."
        ),
        "db_id": db_id,
        "cause": cause,
    }


def required_capabilities(targets: Sequence[dict[str, Any]], chain: Sequence[str]) -> list[str]:
    """대상 DB 답변 영역과 `chain`의 합집합(처음 나온 순서)."""
    out: list[str] = []
    for t in targets:
        for c in t.get("capabilities") or []:
            if isinstance(c, str) and c not in out:
                out.append(c)
    for c in chain:
        if c not in out:
            out.append(c)
    return out
