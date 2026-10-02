"""실행 그룹 분할기 — db_id 목록을 순서 있는 그룹으로 나눈다 (D-176 · plans/82 §4.2·§4.8).

종전 `mixed_zone_groups`(utils)가 "혼합이면 **거부**"였다면 이 모듈은 "혼합이면 **나눈다**"이다.
사용자 요구(2026-08-28): 은행존을 먼저 조회하고 완료되면 공동존을 조회한다.

**계층 배치 근거(D-171 — 소비자의 들어오는 의존이 배치를 결정한다)**: 이 함수는 레지스트리
(`src/routing/registry.py`, infrastructure)를 읽어야 하는데 `utils`는 어디에도 의존할 수 없다
(`arch_check` ALLOWED_DEPS["utils"] = set()). 소비 예정 지점은 전부 infrastructure를 참조할 수
있는 계층이다 — `api/routes/query.py`(interface) · `routing/semantic_router.py`(infrastructure) ·
`orchestration/subagents.py`(orchestration) · `nodes/multi_db_executor.py`(application).
따라서 routing(infrastructure)에 둔다.

**이 모듈은 아직 소비처가 없다** — 축을 노출만 하므로 런타임 동작 변화가 0이다
(plans/82 Wave 2 · 소비 배선은 group-runner 이후 모듈 소관).

계층: infrastructure (routing).
"""

from __future__ import annotations

from src.routing.registry import get_registry

__all__ = ["partition_execution_groups"]


def partition_execution_groups(db_ids: list[str] | None) -> list[dict]:
    """db_id 목록을 **순서 있는 실행 그룹**으로 나눈다 (D-176 · plans/82 §4.2·§4.8).

    종전 `mixed_zone_groups`가 "혼합이면 거부"였다면 이 함수는 **"혼합이면 나눈다"** 이다.
    순서 정본은 레지스트리 `query_order`(은행존 10 → 공동존 20)이며 **입력 순서·LLM
    relevance_score에 의존하지 않는다**(D-035 — 결정적 게이트가 판단한다).

    미등재 db_id는 무시한다(기존 `mixed_zone_groups` 규약과 동일 — 판정에서 제외).

    Args:
        db_ids: 대상 DB 목록

    Returns:
        `plans/82` §4.1 형태의 그룹 dict 목록(그룹 간 order 오름차순).
        소비처가 아직 없으므로 이 함수 자체는 런타임 동작을 바꾸지 않는다.
    """
    if not db_ids:
        return []
    reg = get_registry()
    sol_by_code = {s.code: s for s in reg.solutions()}

    # 그룹 **내부** 순서도 레지스트리 선언 순서를 따른다 — 입력 배열 순서(라우터의
    # relevance_score 정렬 결과 등)에 의존하면 같은 대상 집합이 호출마다 다른 순서로
    # 실행돼 재현·비교가 불가능해진다.
    declared = {db_id: i for i, db_id in enumerate(reg.db_ids())}

    buckets: dict[str, list[str]] = {}
    seen: set[str] = set()
    for db_id in db_ids:
        if db_id in seen:
            continue
        group_code = reg.zone_group_of(db_id)
        if not group_code:
            continue  # 미등재·존 미배정 — 판정에서 제외
        seen.add(db_id)
        buckets.setdefault(group_code, []).append(db_id)
    for members in buckets.values():
        members.sort(key=lambda d: declared.get(d, len(declared)))

    groups: list[dict] = []
    for spec in reg.zone_groups():          # query_order 순
        members = buckets.get(spec.code)
        if not members:
            continue
        solution = sol_by_code.get(spec.solution)
        groups.append({
            "group_key": f"{spec.solution}:{spec.code}",
            "solution": spec.solution,
            "zone_group": spec.code,
            "label": spec.label,
            "db_ids": members,
            "backend": solution.backend if solution else "sql",
            "order": spec.query_order,
            "kind": "peer",
        })
    return groups
