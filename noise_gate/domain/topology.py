"""토폴로지 의존성 그래프 (Plan 60 E4 · D-080 — 결정적 순수 자료구조).

가용성 의존(`cmm_resource.AVAIL_DEPEND_RESOURCE_ID`) 엣지로 구성한 **불변 스냅샷**
그래프다. 노드=리소스 ID, 엣지=자식→부모(가용성 의존). 다홉 조상 순회(BFS)로
연쇄 노이즈(자식 알람이 조상 장애의 파생인지)와 근본원인 후보(최상위 비정상 조상)를
결정적으로 산출한다.

정적/동적 분리(§6.2 실측 보완):
    - **정적**(엣지·이름): 이 자료구조가 보관. 변경이 드물어 장기 캐시 대상.
    - **동적**(비정상 상태 `abnormal` 집합): 매 이벤트 신선 조회값을 *인자로* 받는다.
      그래프는 상태를 보관하지 않는다(24h 캐시 대상 아님).

이 모듈은 domain 계층에 위치하므로 **표준 라이브러리만** 의존한다(infra·config import 금지).
동일 자료구조를 Plan 50 결정적 BFS RCA(correlation_engine)가 공용 선행자산으로 소비한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Optional

# 홉 상한 기본값(생성자 미지정 시) — 순환은 방문 집합으로 방어하되, 비용/폭주 이중 가드.
_DEFAULT_MAX_HOPS = 64


def _norm_id(value) -> str:  # noqa: ANN001
    """리소스 ID를 안정 문자열로 정규화한다(DB 드라이버별 int/str 케이스 흡수)."""
    return str(value)


class DependencyGraph:
    """가용성 의존 그래프의 불변 스냅샷(정적 엣지만 — 동적 상태는 미포함).

    생성자는 엣지 매핑(child_id → parent_ids)과 id→name 맵을 받는다. 부모/자식 관계는
    생성 시점에 정규화(문자열화·None/빈값 제거)하여 조회 시 결정성을 보장한다.
    """

    def __init__(
        self,
        edges: Mapping[str, Iterable] | Iterable[tuple],
        names: Optional[Mapping] = None,
        *,
        max_hops: int = _DEFAULT_MAX_HOPS,
    ) -> None:
        """그래프를 구성한다.

        Args:
            edges: child_id → parent_ids 매핑(dict) 또는 (child_id, [parent_ids]) 튜플 반복자.
            names: 리소스 id → NAME 매핑(name_of 역조회용). None이면 빈 맵.
            max_hops: is_cascaded/find_root의 기본 홉 상한(BFS 비용·순환 가드).
        """
        # 정방향(자식→부모)·역방향(부모→자식) 인접을 함께 구성한다(descendants용).
        parents: dict[str, set[str]] = {}
        children: dict[str, set[str]] = {}

        items: Iterable[tuple]
        if isinstance(edges, Mapping):
            items = edges.items()
        else:
            items = edges  # (child_id, parent_ids) 튜플 반복자

        for child_raw, parent_list in items:
            child = _norm_id(child_raw)
            for parent_raw in parent_list or ():
                if parent_raw is None or parent_raw == "":
                    continue
                parent = _norm_id(parent_raw)
                if parent == child:
                    continue  # 자기참조 엣지 제거(순환 씨앗 방지)
                parents.setdefault(child, set()).add(parent)
                children.setdefault(parent, set()).add(child)

        # 불변 스냅샷으로 동결(조회는 정렬 순회로 결정성 보장).
        self._parents: dict[str, frozenset[str]] = {
            k: frozenset(v) for k, v in parents.items()
        }
        self._children: dict[str, frozenset[str]] = {
            k: frozenset(v) for k, v in children.items()
        }
        self._names: dict[str, str] = {
            _norm_id(k): str(v) for k, v in (names or {}).items() if v is not None
        }
        # 이름→id 역맵(id_of용). 동일 이름 충돌 시 정렬 최소 id를 채택해 결정성을 보장한다.
        # (엣지 미보유 root는 엣지 로더 name 맵에 부재 → 역맵에도 없음 → id_of None·graceful.)
        self._ids_by_name: dict[str, str] = {}
        for rid, nm in sorted(self._names.items()):
            self._ids_by_name.setdefault(nm, rid)
        self._max_hops = int(max_hops)

    def ancestors(self, node_id, *, max_hops: int) -> list[str]:  # noqa: ANN001
        """node_id의 조상(부모→조부모…)을 BFS 순서로 반환한다.

        방문 집합으로 순환을 방어하고, max_hops 레벨까지만 탐색한다(비용/폭주 가드).
        각 레벨은 정렬 순회하여 동일 입력→동일 출력(결정성)을 보장한다.
        """
        start = _norm_id(node_id)
        visited: set[str] = {start}
        result: list[str] = []
        frontier: list[str] = [start]
        hops = 0
        while frontier and hops < max_hops:
            nxt: list[str] = []
            for n in frontier:
                for p in sorted(self._parents.get(n, frozenset())):
                    if p not in visited:
                        visited.add(p)
                        result.append(p)
                        nxt.append(p)
            frontier = nxt
            hops += 1
        return result

    def is_cascaded(self, node_id, abnormal: set) -> bool:  # noqa: ANN001
        """조상 중 비정상(abnormal 집합 소속)이 하나라도 있으면 True.

        abnormal은 동적 상태(AVAIL_STATUS≠0 조상)이며 호출부가 신선 조회해 넘긴다.
        빈 집합이면 즉시 False(연쇄 아님).
        """
        if not abnormal:
            return False
        abn = {_norm_id(a) for a in abnormal}
        for anc in self.ancestors(node_id, max_hops=self._max_hops):
            if anc in abn:
                return True
        return False

    def find_root(self, node_id, abnormal: set):  # noqa: ANN001, ANN201
        """최상위 비정상 조상(근본원인 후보) 리소스 ID를 반환한다(없으면 None).

        비정상 조상 중 **자신의 조상에는 비정상이 없는** 노드가 연쇄의 최상위(root)다.
        후보가 복수면 정렬 최소 ID로 결정적 선택한다.
        """
        if not abnormal:
            return None
        abn = {_norm_id(a) for a in abnormal}
        anc = self.ancestors(node_id, max_hops=self._max_hops)
        abnormal_anc = [a for a in anc if a in abn]
        if not abnormal_anc:
            return None
        roots: list[str] = []
        for a in abnormal_anc:
            higher = self.ancestors(a, max_hops=self._max_hops)
            if not any(h in abn for h in higher):
                roots.append(a)
        candidates = roots or abnormal_anc
        return sorted(candidates)[0]

    def descendants(self, node_id) -> set:  # noqa: ANN001
        """node_id의 후손(자식→손자…) 집합을 반환한다(Plan 50 RCA 연쇄 후보용).

        방문 집합으로 순환을 방어한다. 홉 상한은 두지 않고 전 후손을 수집한다.
        """
        start = _norm_id(node_id)
        visited: set[str] = {start}
        result: set[str] = set()
        frontier: list[str] = [start]
        while frontier:
            nxt: list[str] = []
            for n in frontier:
                for c in sorted(self._children.get(n, frozenset())):
                    if c not in visited:
                        visited.add(c)
                        result.add(c)
                        nxt.append(c)
            frontier = nxt
        return result

    def name_of(self, node_id):  # noqa: ANN001, ANN201
        """리소스 ID → NAME 역조회(root_notified 산출용). 미등록이면 None."""
        return self._names.get(_norm_id(node_id))

    def id_of(self, name):  # noqa: ANN001, ANN201
        """NAME → 리소스 ID 역조회(E2 위상 가중 노드 해소용). 미등록이면 None.

        `name_of`의 역방향이다. **엣지 로더는 엣지(부모 의존) 보유 행만 담으므로**, 부모
        의존이 없는 root 서버는 name 맵(따라서 역맵)에 부재하여 None을 반환한다 — 이는
        정확성 버그가 아니라 **우아한 열화**다(root는 위상 보너스를 못 받되 필드 Jaccard로
        정상 군집). 동일 이름 충돌은 생성 시 정렬 최소 id로 결정적 해소한다.

        Args:
            name: 조회할 리소스 이름(예: 폴스타 등록 서버명 = event.server_name).

        Returns:
            매칭 리소스 ID(문자열), 미등록이면 None.
        """
        if name is None:
            return None
        return self._ids_by_name.get(str(name))

    def is_related(self, a, b, *, max_hops: int) -> bool:  # noqa: ANN001
        """a·b가 같은 가용성 의존 서브트리에 속하는지 판정한다(E2 위상 인접성).

        판정 기준(둘 중 하나라도 충족하면 True):
            - 한쪽이 다른쪽의 조상(max_hops 내) — 직접 의존 계보.
            - max_hops 내 **공통 조상**을 보유 — 같은 상위 자원에 함께 의존(형제/사촌).

        `ancestors`(방문 집합·홉 상한)로 순환/폭주를 방어한다. 자기 자신(a==b)은 False —
        상관 대표는 다른 호스트이므로 자기 인접은 무의미하다(§ 위상 가중).

        Args:
            a: 리소스 ID(신규 이벤트 노드).
            b: 리소스 ID(클러스터 대표 노드).
            max_hops: 조상 BFS 홉 상한(순환/비용 가드).

        Returns:
            같은 의존 서브트리면 True, 아니면 False.
        """
        na = _norm_id(a)
        nb = _norm_id(b)
        if na == nb:
            return False
        anc_a = set(self.ancestors(na, max_hops=max_hops))
        anc_b = set(self.ancestors(nb, max_hops=max_hops))
        # 한쪽이 다른쪽의 조상(직접 계보).
        if nb in anc_a or na in anc_b:
            return True
        # max_hops 내 공통 조상 보유(형제/사촌 — 같은 상위 자원 의존).
        return bool(anc_a & anc_b)
