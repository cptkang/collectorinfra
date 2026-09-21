"""스레드 DB 스코프 — 다음 턴이 승계할 DB 집합의 **단일 출처** + 축 구조 보고 (plans/90 · D-205).

**왜 이 모듈인가.** "이 창은 지금 어느 폴스타를 보는가"의 실체는 선택값 보존이 아니라 직전 실행 DB의
암묵 승계다(`context_resolver`의 sticky 추출). 칩이 보여주는 값과 다음 턴이 실제로 승계하는 값이 어긋나면
침묵 강등의 새 형태가 되므로, 두 소비자(`context_resolver` · API 라우트)가 **같은 함수**를 쓴다.

**축 구조.** 레지스트리는 이미 2축이다 — `solutions`(1차) · `zone_groups`(폴스타 내부 2차). 응답은 db_id
평면 목록이 아니라 축별로 보고한다(plans/90 §9 — APM·DPM 편입 시 칩을 다시 설계하지 않기 위해).
판정은 레지스트리 접근자만 쓴다(리터럴 0 — `overfit_check`).

계층: infrastructure(`src/routing/`) — `nodes`(application)·`api`(interface)가 소비한다.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Optional, Sequence

from src.routing.registry import get_registry

#: `db_scope.source` 값 — 문자열 필드이며 닫힌 enum이 아니다(`dependent`는 plans/82 파이프라인용 예약).
SOURCE_SELECTED = "selected"      # 이번 턴 selected_db_ids(역질문 답·스코프 칩)
SOURCE_INHERITED = "inherited"    # 직전 턴 DB 승계(이번 턴 대상 없음 → sticky만 남음, 또는 승계 신호)
SOURCE_HINT = "hint"              # 이번 턴 원문 위치/DB 힌트 결정적 핀
SOURCE_PLANNED = "planned"        # task.db_ids(폼필 mapped_db_ids 등 계획 단계 고정)
SOURCE_CLASSIFIED = "classified"  # LLM 분류 팬아웃(신호 없음)
SOURCE_NONE = "none"              # 승계할 대상 없음


def extract_state_db_ids(state: Mapping[str, Any]) -> list[str]:
    """이번 턴 state의 대상 DB 식별자를 통합해 반환한다 (구 `context_resolver._extract_previous_db_ids`).

    `target_databases`(관련도 순 보존) ∪ `active_db_id` ∪ `mapped_db_ids`. 동작은 이관 전과 동일하다.
    """
    db_ids: list[str] = []
    for t in state.get("target_databases", []) or []:
        did = t.get("db_id") if isinstance(t, dict) else t
        if did and did != "default" and did not in db_ids:
            db_ids.append(did)
    active = state.get("active_db_id")
    if active and active != "default" and active not in db_ids:
        db_ids.append(active)
    for did in state.get("mapped_db_ids", []) or []:
        if did and did not in db_ids:
            db_ids.append(did)
    return db_ids


def zone_selection_db_ids(
    selected_db_ids: Sequence[str] | None,
    target_databases: Sequence[Any] | None,
) -> list[str]:
    """존 선택 재개 턴의 대상 — 선택한 존 + **이번 턴 라우터가 남긴 존 미배정 DB** (plans/95 W-10).

    존 선택·범위 선택의 선택지는 존 그룹으로 만들어지므로 존 그룹이 없는 DB는 선택에 담길 수
    없다. 라우터가 그 DB를 대상에 남겼는데 task 고정이 선택값만 보면 같은 침묵 탈락이 순차 러너
    경로에서 되살아난다 — 3단도 2단 부품을 재사용한다(`docs/21_orchestration_ladder.md` §7).

    선택이 비었으면 빈 목록이다(호출부가 종전대로 분류로 넘어간다).

    Args:
        selected_db_ids: 사용자가 고른 DB 목록
        target_databases: 이번 턴 라우터 산출물(`state["target_databases"]`)

    Returns:
        선택 순서를 유지한 대상 db_id 목록(존 미배정 DB는 뒤에 붙는다)
    """
    selected = [d for d in (selected_db_ids or []) if d]
    if not selected:
        return []
    merged = list(dict.fromkeys(selected))
    reg = get_registry()
    for target in target_databases or []:
        db_id = target.get("db_id") if isinstance(target, Mapping) else target
        if db_id and db_id not in merged and reg.zone_group_of(db_id) is None:
            merged.append(db_id)
    return merged


def resolve_thread_db_ids(state: Mapping[str, Any]) -> list[str]:
    """다음 턴 `context_resolver`가 `previous_db_ids`로 읽을 값 — 단일 출처(D-205).

    이번 턴 추출이 비면 직전 `conversation_context`를 sticky 승계한다(D-056 후속과 동일 규칙).
    """
    ids = extract_state_db_ids(state)
    if ids:
        return ids
    ctx = state.get("conversation_context") or {}
    return [d for d in (ctx.get("previous_db_ids") or []) if d]


def _zone_groups_axis(db_ids: Sequence[str]) -> list[dict]:
    """db_ids가 속한 존 그룹들(폴스타 2차 축) — `query_order` 순. 존 동시 조회(D-206)면 2개가 된다."""
    reg = get_registry()
    declared = {d: i for i, d in enumerate(reg.db_ids())}
    by_group: dict[str, list[str]] = {}
    for did in db_ids:
        code = reg.zone_group_of(did)
        if code:
            by_group.setdefault(code, []).append(did)
    for members in by_group.values():  # 그룹 내부는 레지스트리 선언 순(partition_execution_groups와 동일)
        members.sort(key=lambda d: declared.get(d, len(declared)))
    out: list[dict] = []
    for spec in reg.zone_groups():  # query_order 순
        if spec.code in by_group:
            out.append({"code": spec.code, "label": spec.label or spec.code, "db_ids": by_group.pop(spec.code)})
    for code, ids in by_group.items():  # 레지스트리에 zone_groups 선언이 없는 잔여(방어)
        out.append({"code": code, "label": code, "db_ids": ids})
    return out


def _solutions_axis(db_ids: Sequence[str]) -> list[dict]:
    """db_ids가 속한 관측 솔루션(1차 축) — `DBEntry.family` ↔ `SolutionSpec.family`로 판정, `order` 순."""
    reg = get_registry()
    by_family: dict[str, list[str]] = {}
    for did in db_ids:
        entry = reg.get(did)
        if entry and entry.family:
            by_family.setdefault(entry.family, []).append(did)
    out: list[dict] = []
    for spec in reg.solutions():
        ids = by_family.get(spec.family) if spec.family else None
        if ids:
            out.append({"code": spec.code, "label": spec.label or spec.code, "db_ids": ids})
    return out


def build_db_scope(
    state: Mapping[str, Any], *, selected_db_ids: Optional[Sequence[str]] = None
) -> dict:
    """최종 state에서 응답 메타 `db_scope`를 만든다.

    Returns:
        {"zone_group": {...}|None, "solutions": [...], "db_ids": [...], "source": str}
    """
    db_ids = resolve_thread_db_ids(state)
    if not db_ids:
        return {"zone_group": None, "zone_groups": [], "solutions": [], "db_ids": [], "source": SOURCE_NONE}
    if selected_db_ids or state.get("selected_db_ids"):
        source = SOURCE_SELECTED
    elif state.get("db_scope_source"):
        source = str(state["db_scope_source"])
    elif extract_state_db_ids(state):
        source = SOURCE_CLASSIFIED
    else:
        source = SOURCE_INHERITED  # 이번 턴 대상 없음 — sticky 승계값만 남았다
    zone_groups = _zone_groups_axis(db_ids)
    return {
        "zone_group": zone_groups[0] if zone_groups else None,   # 첫 그룹(호환) — 복수는 zone_groups
        "zone_groups": zone_groups,                              # D-206: 은행존+공동존 동시 스코프
        "solutions": _solutions_axis(db_ids),
        "db_ids": list(db_ids),
        "source": source,
    }


def scope_axes_options(
    active_db_ids: Sequence[str],
    *,
    allowed_db_ids: Optional[Sequence[str]] = None,
    group_exclusive: bool = True,
) -> dict:
    """`GET /scope/options` 페이로드 — 축 배열. 오늘은 `zone_group` 축 하나.

    옵션 입도는 존 선택 역질문(D-143)과 같은 **DB 단위**다(공동존 김포/여의도를 따로 고를 수 있어야
    한다). 짧은 라벨의 정본은 `ZONE_CLARIFY_OPTIONS`(레지스트리 `display_name`은 서술형이라 칩에
    맞지 않는다)이며, 그룹 값의 레지스트리 동등성은 `tests/test_orchestration/test_execution_groups.py`가
    지킨다. 솔루션 축은 plans/82 Wave 7이 `axes`에 항목을 추가한다 — 엔드포인트·프론트 분기는 늘지 않는다.
    """
    from src.utils.query_gen_common import build_zone_clarification

    payload = build_zone_clarification(list(active_db_ids), "", group_exclusive=group_exclusive)
    options: list[dict] = []
    for o in (payload or {}).get("options") or []:
        did = o.get("db_id")
        if not did or (allowed_db_ids is not None and did not in allowed_db_ids):
            continue
        options.append({
            "key": did,
            "label": o.get("label") or did,
            "group": o.get("group"),
            "db_ids": [did],
        })
    return {
        "axes": [
            {"axis": "zone_group", "exclusive": bool(group_exclusive), "options": options}
        ]
    }


# ── 미등록 존 탐지 (plans/108 CU-B2 · G-3 사용자 확정 2026-09-21) ──────────────────
#
# run `20260918-182507` R3-03(3회 전건 동일): "판교존 서버 목록" 에 대해 생성 SQL 이
# `-- 판교존(지역 힌트는 스키마에 없으므로 무시)` 주석을 달고 전 서버 1,690건을 반환했다.
# 등록되지 않은 존을 **침묵 무시**한 것이라 「침묵적 폴백 금지」 위반이다.
#
# 탐지는 **좁게** 못 박는다 — 오탐(정상 질의를 역질문으로 가로채기)의 대가가 미탐보다 크다.
#   ① `[가-힣]{2,}존` — 존 앞에 음절이 2개 이상. `기존·보존·공존·의존·잔존` 같은 2음절
#      낱말(존 앞 1음절)은 이 조건만으로 전부 빠진다.
#   ② `_NON_ZONE_SUFFIXES` 접미 배제 — `상호의존`·`적자생존` 처럼 ①을 통과하는 합성어를 막는다.
#   ③ 레지스트리 어휘(위치 표면어·존 라벨·존 그룹 라벨·DB alias)에 등장하는 `…존` 은 제외.
#      존 이름을 코드에 적지 않는다(리터럴 0 — `overfit_check`).
# `ㅇㅇ존` 플레이스홀더(D-143)는 자모(ㅇ)라 ①에 걸리지 않는다.
_ZONE_TOKEN_RE = re.compile(r"[가-힣]{2,}존")

#: `…존` 으로 끝나지만 존(zone)이 아닌 일반 낱말. 접미 일치로 배제한다.
_NON_ZONE_SUFFIXES: tuple[str, ...] = (
    "기존", "보존", "공존", "의존", "잔존", "상존", "병존", "현존",
    "생존", "실존", "자존", "부존", "온존", "엄존",
)


def _registry_zone_vocabulary() -> frozenset[str]:
    """레지스트리 어휘에 등장하는 `…존` 토큰 집합 (등록된 존 = 탐지 제외 대상).

    캐시하지 않는다 — `get_registry()` 자체가 프로세스 캐시라 비용은 짧은 문자열 ~50개의
    정규식 스캔뿐이고, 여기서 따로 캐시하면 `reload_registry()`(캐시 무효화) 뒤에 이 집합만
    낡은 값으로 남는다.
    """
    reg = get_registry()
    sources: list[str] = list(reg.location_terms())
    for spec in reg.zones:
        sources.extend(filter(None, (spec.code, spec.label)))
    for grp in reg.zone_groups():
        sources.extend(filter(None, (grp.code, grp.label)))
    for entry in reg.databases:
        sources.append(entry.display_name or "")
        sources.extend(entry.aliases or ())
    known: set[str] = set()
    for text in sources:
        known.update(_ZONE_TOKEN_RE.findall(text or ""))
    return frozenset(known)


def find_unregistered_zone_terms(query: str | None) -> list[str]:
    """질의에서 **레지스트리에 없는** 존 표면어를 등장 순서로 반환한다 (plans/108 CU-B2).

    호출부는 이 결과가 비지 않으면 조용히 진행하지 말고 존 선택 역질문으로 되물어야 한다
    (G-3 사용자 확정 — "0건 응답"이 아니라 "그런 존이 없다"를 말한다).

    Args:
        query: 사용자 원문 질의

    Returns:
        미등록 존 토큰 목록(중복 제거·등장 순서 유지). 없으면 빈 목록.
    """
    if not query:
        return []
    known = _registry_zone_vocabulary()
    found: list[str] = []
    for token in _ZONE_TOKEN_RE.findall(query):
        if token in known or token in found:
            continue
        if any(token.endswith(suffix) for suffix in _NON_ZONE_SUFFIXES):
            continue
        found.append(token)
    return found
