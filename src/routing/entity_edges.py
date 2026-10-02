"""교차 계층 엔터티 간선 경로 판정 (plans/125 E-2 · E-3 · §4.5 · DM-5).

레지스트리 간선 표(`entity_edges`)만으로 **가진 패싯 → 필요한 패싯** 최단 경로를 고른다. 표 밖
경로는 없다(None) — 계획 검증이 거절한다. 패싯 변환 단계는 **교차 시스템 간선에서만** 삽입한다(같은
시스템 간선은 불변 — 121 G-26 · D-272 ⑦): 간선 소유자가 소비 시스템과 같으면 변환하지 않는다.

LLM 0 · 순수(레지스트리 읽기만). 계층: infrastructure(routing).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

from src.routing.registry import DBRegistry, EdgeSpec, get_registry


def facet_path(
    have: Iterable[str], need: str, *, registry: DBRegistry | None = None,
) -> list[EdgeSpec] | None:
    """가진 패싯들에서 `need` 로 가는 최단 간선 경로(이미 있으면 빈 목록 · 없으면 None)."""
    reg = registry or get_registry()
    start = [f for f in dict.fromkeys(have) if f]
    if need in start:
        return []
    edges = reg.entity_edges()
    queue: deque[tuple[str, list[EdgeSpec]]] = deque((f, []) for f in start)
    seen = set(start)
    while queue:
        facet, path = queue.popleft()
        for edge in edges:
            if edge.from_facet != facet or edge.to_facet in seen:
                continue
            nxt = path + [edge]
            if edge.to_facet == need:
                return nxt
            seen.add(edge.to_facet)
            queue.append((edge.to_facet, nxt))
    return None


def cross_system_only(path: list[EdgeSpec], consumer: str) -> bool:
    """경로의 간선이 전부 소비 시스템 밖 소유자인가 — 같은 시스템 간선은 삽입하지 않는다."""
    return all(edge.owner != consumer for edge in path)


__all__ = ["cross_system_only", "facet_path"]
