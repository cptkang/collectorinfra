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


# ─────────────────────────────────────────────────────────────
# Plan 60 E2 위상 가중 — id_of(name→id 역조회)·is_related(같은 의존 서브트리)
# ─────────────────────────────────────────────────────────────
class TestIdOf:
    def test_reverse_lookup(self):
        g = DependencyGraph(
            {"r1": ["r2"]}, names={"r1": "web-01", "r2": "db-01"}
        )
        assert g.id_of("web-01") == "r1"
        assert g.id_of("db-01") == "r2"

    def test_missing_name_none(self):
        # 이름 부재(엣지 미보유 root 등) → None(우아한 열화·보너스 미발동).
        g = DependencyGraph({"r1": ["r2"]}, names={"r1": "web-01"})
        assert g.id_of("db-01") is None   # r2는 name 맵에 없음
        assert g.id_of("ghost") is None
        assert g.id_of(None) is None

    def test_roundtrip_with_name_of(self):
        g = DependencyGraph({"r1": ["r2"]}, names={"r1": "web-01"})
        rid = g.id_of("web-01")
        assert rid is not None
        assert g.name_of(rid) == "web-01"

    def test_collision_deterministic_min_id(self):
        # 동일 이름 충돌 → 정렬 최소 id를 결정적으로 채택.
        g = DependencyGraph(
            {"r2": ["p"], "r1": ["p"]}, names={"r2": "dup", "r1": "dup"}
        )
        assert g.id_of("dup") == "r1"

    def test_known_mistake_1_root_no_name_graceful(self):
        # Known Mistakes #1: 엣지 로더는 엣지(부모 의존) 보유 행만 name에 담는다 →
        # 부모 없는 root 서버는 name 부재 → id_of None → 위상 보너스 미발동(우아한 열화).
        # child r_child는 부모 dep 보유(name 有), root p는 엣지 미보유(name 無)로 모사.
        g = DependencyGraph({"r_child": ["p"]}, names={"r_child": "child-01"})
        assert g.id_of("child-01") == "r_child"   # 엣지 보유 → 해소됨
        assert g.id_of("root-01") is None          # 엣지 미보유 root → None(graceful)


class TestIsRelated:
    def test_ancestor_relation_true(self):
        # leaf → m1 → root. leaf와 root는 한쪽이 다른쪽 조상 → 관련.
        g = DependencyGraph({"leaf": ["m1"], "m1": ["root"]})
        assert g.is_related("leaf", "root", max_hops=5) is True
        assert g.is_related("root", "leaf", max_hops=5) is True  # 대칭

    def test_common_ancestor_siblings_true(self):
        # a, b가 같은 부모 p에 의존(형제) → 공통 조상 보유 → 관련.
        g = DependencyGraph({"a": ["p"], "b": ["p"]})
        assert g.is_related("a", "b", max_hops=5) is True

    def test_unrelated_false(self):
        # 서로 다른 독립 서브트리 → 관련 없음.
        g = DependencyGraph({"a": ["pa"], "b": ["pb"]})
        assert g.is_related("a", "b", max_hops=5) is False

    def test_self_is_false(self):
        # 자기 자신은 False(대표는 다른 호스트이므로 무의미).
        g = DependencyGraph({"a": ["p"]})
        assert g.is_related("a", "a", max_hops=5) is False

    def test_hop_limit_excludes_distant_common_ancestor(self):
        # 공통 조상이 홉 상한 밖이면 미관련(비용/폭주 가드).
        # a → a1 → a2 → root, b → b1 → b2 → root (공통 조상 root는 3홉).
        g = DependencyGraph({
            "a": ["a1"], "a1": ["a2"], "a2": ["root"],
            "b": ["b1"], "b1": ["b2"], "b2": ["root"],
        })
        assert g.is_related("a", "b", max_hops=3) is True   # root가 3홉 내
        assert g.is_related("a", "b", max_hops=2) is False  # root가 홉 밖

    def test_cycle_defense(self):
        # 순환 그래프도 무한루프 없이 판정(ancestors 방문집합 재사용).
        g = DependencyGraph({"a": ["b"], "b": ["a"]})
        assert g.is_related("a", "b", max_hops=100) is True

    def test_deterministic(self):
        g = DependencyGraph({"a": ["p"], "b": ["p"]})
        first = g.is_related("a", "b", max_hops=5)
        assert first == g.is_related("a", "b", max_hops=5)
