"""인스턴스·서비스·업무 이름 검색 — 정규화 · 검색 단계 · 유사 후보(plans/130 N-1 · plans/178 W1에서
`tools.py`에서 옮김)."""

from __future__ import annotations

import difflib
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, TypeVar

from apm_gateway.application.masking import mask_text
from apm_gateway.application.resolver import Inventory
from apm_gateway.application.sources import JenniferSource, name_key
from apm_gateway.domain.errors import INVALID_ARGUMENT, ApmError

# plans/130 N-1 — 인스턴스 이름 검색(단계 순서 · 단계별 신뢰도 · 검색어 길이 · 유사 후보)
SEARCH_TIERS = ("exact", "normalized", "prefix", "contains")
QUERY_MAX = 200
CONTAINS_MIN = 3
SUGGEST_RATIO = 0.8
SUGGEST_MAX = 3
_NAME_SEPARATORS = frozenset("-_.")


def _is_separator(ch: str) -> bool:
    return ch in _NAME_SEPARATORS or ch.isspace()


def normalize_name(text: str) -> str:
    """이름 정규화 — 소문자(casefold) + 구분자(`-`·`_`·`.`·공백) 제거. 정규식 없이 한 번 훑는다."""
    return "".join(ch for ch in str(text or "").casefold() if not _is_separator(ch))


def search_query(value: Any, name: str = "query") -> str:
    """검색어 인자(`name` = 인자 이름 — query·business) — 앞뒤 공백 제거 뒤 1~`QUERY_MAX`자 문자열.
    그 밖은 `invalid_argument`."""
    if not isinstance(value, str):
        raise ApmError(INVALID_ARGUMENT, f"{name}는 문자열이어야 한다")
    text = value.strip()
    if not text:
        raise ApmError(INVALID_ARGUMENT, f"{name}가 비어 있다")
    if len(text) > QUERY_MAX:
        raise ApmError(INVALID_ARGUMENT, f"{name}는 {QUERY_MAX}자 이하여야 한다({len(text)}자)")
    return text


@dataclass
class InstanceSearch:
    """인스턴스 이름 검색 결과(plans/130 N-1)."""

    tier: str | None  # 채택한 단계(0건이면 None)
    # 채택 단계의 (소스, 인벤토리, 인스턴스) — 정렬: 소스 선언 순서·도메인·이름
    matches: list[tuple[JenniferSource, Inventory, dict[str, Any]]]
    counts: dict[str, int]  # 단계별 후보 수(인스턴스마다 가장 앞 단계 하나로 센다)
    # 0건일 때만 — 정규화 이름 유사도 ≥ SUGGEST_RATIO 상위 SUGGEST_MAX(자동 채택 안 함)
    suggestions: list[dict[str, Any]]


def name_tier(name: Any, q: str, q_norm: str, description: Any = "") -> str | None:
    """이름 하나(+설명)의 가장 앞 검색 단계(plans/130 N-1 규칙 — 인스턴스·서비스(도메인)·업무
    이름 공용 · plans/134 W3·W4). `q`는 앞뒤 공백 제거·casefold한 검색어, `q_norm`은 그 정규화.
    단계: exact(대소문자 무시 동일) → normalized(구분자 제거 동일) → prefix(앞부분 + 바로 뒤가
    끝·구분자) → contains(정규화 검색어 `CONTAINS_MIN`자 이상 · 정규화 이름 또는 설명에 포함).
    설명은 화면에 싣는 마스킹본에서만 찾는다(가린 원문으로 일치 여부를 흘리지 않는다) — 비우면
    이름만 본다."""
    text = str(name or "").strip().casefold()
    if text == q:
        return "exact"
    norm = normalize_name(text)
    if q_norm and norm == q_norm:
        return "normalized"
    if text.startswith(q) and (len(text) == len(q) or _is_separator(text[len(q)])):
        return "prefix"
    if len(q_norm) >= CONTAINS_MIN and (
        q_norm in norm or (bool(description) and q in mask_text(description, limit=None).casefold())
    ):
        return "contains"
    return None


Hit = TypeVar("Hit")


def search_names(
    text: str, candidates: Iterable[tuple[Hit, Any, Any]]
) -> tuple[str | None, list[Hit], dict[str, int]]:
    """후보 `(항목, 이름, 설명)`에서 **가장 앞 단계의 항목만** 고른다(`name_tier` · 후보 순서
    유지) → (채택 단계 · 채택 항목 · 단계별 후보 수(항목마다 가장 앞 단계 하나로 센다)). 0건이면
    (None, [], 단계별 0)."""
    q = text.strip().casefold()
    q_norm = normalize_name(q)
    counts = dict.fromkeys(SEARCH_TIERS, 0)
    by_tier: dict[str, list[Hit]] = {}
    for item, name, description in candidates:
        tier = name_tier(name, q, q_norm, description)
        if tier is not None:
            counts[tier] += 1
            by_tier.setdefault(tier, []).append(item)
    chosen = next((t for t in SEARCH_TIERS if by_tier.get(t)), None)
    return chosen, (by_tier[chosen] if chosen is not None else []), counts


def suggest_names(
    q_norm: str, candidates: Iterable[tuple[tuple[Any, ...], Any, dict[str, Any]]]
) -> list[dict[str, Any]]:
    """유사 이름 후보(자동 채택 안 함) — 후보 `(정렬 키, 이름, 제안 항목)` 중 정규화 이름 유사도
    (`difflib.SequenceMatcher`) ≥ SUGGEST_RATIO 상위 SUGGEST_MAX. 순서는 유사도 → 정렬 키 → 이름 ·
    같은 제안 항목은 한 번."""
    if not q_norm:
        return []
    matcher = difflib.SequenceMatcher()
    matcher.set_seq2(q_norm)  # 검색어 쪽을 캐시한다(difflib.get_close_matches와 같은 방식)
    scored: list[tuple[float, tuple[Any, ...], str, dict[str, Any]]] = []
    for order, name, item in candidates:
        norm = normalize_name(name)
        if not norm:
            continue
        matcher.set_seq1(norm)
        if (
            matcher.real_quick_ratio() >= SUGGEST_RATIO
            and matcher.quick_ratio() >= SUGGEST_RATIO
            and (ratio := matcher.ratio()) >= SUGGEST_RATIO
        ):
            scored.append((-ratio, order, str(name), item))
    out: list[dict[str, Any]] = []
    for *_, item in sorted(scored, key=lambda s: s[:3]):
        if item in out:
            continue
        out.append(item)
        if len(out) == SUGGEST_MAX:
            break
    return out


def domain_order(domain_id: Any) -> tuple[bool, int]:
    return domain_id is None, domain_id if isinstance(domain_id, int) else 0


def search_instances(
    usable: list[tuple[JenniferSource, Inventory]], text: str, domain_id: int | None = None
) -> InstanceSearch:
    """고른 소스 인벤토리(캐시 · 새 HTTP 없음)에서 인스턴스 이름·설명을 찾는다(`search_names` —
    **가장 앞 단계의 결과만** 채택). `domain_id`를 주면 그 도메인 안에서만 찾는다."""
    tier, hits, counts = search_names(
        text,
        (
            ((rank, src, inv, inst), inst.get("instance_name"), inst.get("description", ""))
            for rank, (src, inv) in enumerate(usable)
            for inst in inv.instances
            if domain_id is None or inst["domain_id"] == domain_id
        ),
    )
    if tier is not None:
        ordered = sorted(
            hits, key=lambda h: (h[0], domain_order(h[3]["domain_id"]), h[3]["instance_name"])
        )
        return InstanceSearch(tier, [(s, v, i) for _, s, v, i in ordered], counts, [])
    q_norm = normalize_name(text.strip().casefold())
    return InstanceSearch(None, [], counts, _suggest(usable, q_norm, domain_id))


def _suggest(
    usable: list[tuple[JenniferSource, Inventory]], q_norm: str, domain_id: int | None
) -> list[dict[str, Any]]:
    """인스턴스 이름 유사 후보(`suggest_names` — 소스 선언 순서·도메인·이름으로 동률을 가른다)."""
    return suggest_names(
        q_norm,
        (
            (
                (rank, domain_order(inst["domain_id"])),
                inst["instance_name"],
                {
                    "instance_name": inst["instance_name"],
                    "source_id": src.source_id,
                    "domain_id": inst["domain_id"],
                },
            )
            for rank, (src, inv) in enumerate(usable)
            for inst in inv.instances
            if domain_id is None or inst["domain_id"] == domain_id
        ),
    )


def same_name(a: str, b: str) -> bool:
    """이름이 같은가 — 대소문자 무시 또는 구분자(`-`·`_`·`.`·공백) 무시."""
    norm = normalize_name(b)
    return name_key(a) == name_key(b) or (bool(norm) and normalize_name(a) == norm)
