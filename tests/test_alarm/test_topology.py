"""토폴로지 의존성 그래프 domain 단위 테스트 (Plan 60 E4 · D-080).

`DependencyGraph`의 다홉 BFS 순회(ancestors/descendants), 연쇄 판정(is_cascaded),
근본원인 후보(find_root), 이름 역조회(name_of)를 합성 그래프로 결정적 검증한다.
순환 방어(방문 집합)·홉 상한을 고정한다. domain은 stdlib만 의존하므로 인프라·config
없이 순수 테스트 가능하다.

그래프 규약: 엣지는 자식→부모(가용성 의존). 예) node C가 P를 의존하면 edges={"C":["P"]}.
"""

from __future__ import annotations

from src.alarm.domain.topology import DependencyGraph


class TestAncestors:
    def test_multi_hop_bfs_order(self):
        # 체인 leaf → m1 → m2 → root (자식이 부모를 의존)
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        anc = g.ancestors("leaf", max_hops=5)
        assert anc == ["m1", "m2", "root"]  # BFS 레벨 순서

    def test_hop_limit_truncates(self):
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        # 홉 2까지만 → m1(1홉), m2(2홉)만 수집, root(3홉) 제외
        assert g.ancestors("leaf", max_hops=2) == ["m1", "m2"]
        assert g.ancestors("leaf", max_hops=1) == ["m1"]
        assert g.ancestors("leaf", max_hops=0) == []

    def test_multiple_parents_sorted_deterministic(self):
        # 다부모(AVAIL_DEPEND_RESOURCE_ID + _2) — 정렬 순서로 결정성
        g = DependencyGraph({"leaf": ["pb", "pa"]})
        assert g.ancestors("leaf", max_hops=5) == ["pa", "pb"]

    def test_cycle_defense(self):
        # 순환: a→b→a. 방문 집합으로 무한루프 방어, 각 노드 1회만.
        g = DependencyGraph({"a": ["b"], "b": ["a"]})
        anc = g.ancestors("a", max_hops=100)
        assert anc == ["b"]  # a는 시작점(재방문 안 함)

    def test_self_reference_edge_removed(self):
        g = DependencyGraph({"a": ["a"]})
        assert g.ancestors("a", max_hops=5) == []

    def test_unknown_node_empty(self):
        g = DependencyGraph({"leaf": ["root"]})
        assert g.ancestors("ghost", max_hops=5) == []

    def test_int_ids_normalized(self):
        # DB 드라이버가 int ID를 줘도 문자열로 정규화되어 일관 동작.
        g = DependencyGraph({10: [20], 20: [30]})
        assert g.ancestors(10, max_hops=5) == ["20", "30"]
        assert g.ancestors("10", max_hops=5) == ["20", "30"]


class TestIsCascaded:
    def test_abnormal_ancestor_true(self):
        g = DependencyGraph({"leaf": ["m1"], "m1": ["root"]})
        assert g.is_cascaded("leaf", {"root"}) is True
        assert g.is_cascaded("leaf", {"m1"}) is True

    def test_no_abnormal_false(self):
        g = DependencyGraph({"leaf": ["m1"], "m1": ["root"]})
        assert g.is_cascaded("leaf", set()) is False
        assert g.is_cascaded("leaf", {"other"}) is False

    def test_abnormal_descendant_not_cascaded(self):
        # 후손이 비정상이어도 조상 아님 → 연쇄 아님(방향성).
        g = DependencyGraph({"child": ["node"]})
        assert g.is_cascaded("node", {"child"}) is False

    def test_int_abnormal_normalized(self):
        g = DependencyGraph({10: [30]})
        assert g.is_cascaded(10, {30}) is True  # int 집합도 정규화 매칭


class TestFindRoot:
    def test_topmost_abnormal_in_chain(self):
        # leaf → m1 → m2 → root, 전부 비정상이면 최상위(root)가 근본원인.
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        assert g.find_root("leaf", {"m1", "m2", "root"}) == "root"

    def test_partial_abnormal_topmost(self):
        # m1 정상, m2·root 비정상 → 최상위 비정상 root.
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        assert g.find_root("leaf", {"m2", "root"}) == "root"

    def test_middle_abnormal_only(self):
        # root 정상, m2만 비정상 → m2가 최상위 비정상.
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        assert g.find_root("leaf", {"m2"}) == "m2"

    def test_no_abnormal_none(self):
        g = DependencyGraph({"leaf": ["root"]})
        assert g.find_root("leaf", set()) is None
        assert g.find_root("leaf", {"nonexistent"}) is None

    def test_multiple_roots_deterministic(self):
        # leaf가 두 독립 부모(pa, pb) 의존, 둘 다 비정상·상위 없음 → 정렬 최소 선택.
        g = DependencyGraph({"leaf": ["pb", "pa"]})
        assert g.find_root("leaf", {"pa", "pb"}) == "pa"


class TestDescendants:
    def test_multi_hop_descendants(self):
        g = DependencyGraph({"leaf": ["m1"], "m1": ["m2"], "m2": ["root"]})
        assert g.descendants("root") == {"m2", "m1", "leaf"}
        assert g.descendants("m2") == {"m1", "leaf"}
        assert g.descendants("leaf") == set()

    def test_descendants_cycle_defense(self):
        g = DependencyGraph({"a": ["b"], "b": ["a"]})
        assert g.descendants("a") == {"b"}


class TestNameOf:
    def test_name_lookup(self):
        g = DependencyGraph(
            {"r1": ["r2"]}, names={"r1": "web-01", "r2": "db-01"}
        )
        assert g.name_of("r1") == "web-01"
        assert g.name_of("r2") == "db-01"

    def test_missing_name_none(self):
        g = DependencyGraph({"r1": ["r2"]}, names={"r1": "web-01"})
        assert g.name_of("r2") is None
        assert g.name_of("ghost") is None

    def test_int_id_name_lookup(self):
        g = DependencyGraph({10: [20]}, names={10: "web-01"})
        assert g.name_of(10) == "web-01"
        assert g.name_of("10") == "web-01"
