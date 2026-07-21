"""토폴로지 로더 인프라 단위 테스트 (Plan 60 E4 · D-080).

정적 엣지 SQL·동적 상태 SQL 문자열 단언(읽기전용·스키마 한정·이스케이프)과,
로드→DependencyGraph 구성·비정상 집합 산출·캐시·graceful degradation(조회 실패→None)을
검증한다. DBHub 클라이언트는 polestar_noise_context 테스트 패턴(SimpleNamespace(rows=...))을
재사용한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.alarm.infrastructure.topology_loader import (
    TopologyLoader,
    build_ancestor_status_sql,
    build_edges_sql,
)


class TestBuildEdgesSql:
    def test_basic_structure(self):
        sql = build_edges_sql("polestar_cm_gp")
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql  # 단일문
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "RESOURCE_CONF_ID" not in upper  # D-022
        assert "AVAIL_DEPEND_RESOURCE_ID" in upper
        assert "AVAIL_DEPEND_RESOURCE_ID_2" in upper
        assert "NAME" in upper
        # 엣지 보유 행만(전량 스캔 아님)
        assert "AVAIL_DEPEND_RESOURCE_ID IS NOT NULL" in sql
        assert "DTIME IS NULL" in sql

    def test_schema_qualified(self):
        sql = build_edges_sql("polestar_cm_yd")
        assert "polestar.cmm_resource" in sql
        assert "FROM cmm_resource" not in sql


class TestBuildAncestorStatusSql:
    def test_basic_structure(self):
        sql = build_ancestor_status_sql("polestar_cm_gp", ["10", "20", "30"])
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "AVAIL_STATUS" in upper
        assert "NAME" in upper  # root 이름 역조회 보강용
        assert "DTIME IS NULL" in sql
        # IN 절에 이스케이프된 리터럴
        assert "ID IN ('10', '20', '30')" in sql

    def test_empty_ids_returns_empty(self):
        assert build_ancestor_status_sql("polestar_cm_gp", []) == ""

    def test_literal_escaping(self):
        sql = build_ancestor_status_sql("polestar", ["x'; DROP TABLE t--"])
        assert "x''; DROP TABLE t--" in sql

    def test_schema_qualified(self):
        sql = build_ancestor_status_sql("polestar_cm_yd", ["1"])
        assert "polestar.cmm_resource" in sql


class FakeClient:
    """SQL 내용으로 분기하는 가짜 DB 클라이언트(엣지/상태 행 반환·예외 모사)."""

    def __init__(self, edge_rows=None, status_rows=None, raise_on=None):
        self._edge_rows = edge_rows if edge_rows is not None else []
        self._status_rows = status_rows if status_rows is not None else []
        self._raise_on = raise_on  # "edges" | "status" | None
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        is_status = "AVAIL_STATUS" in sql
        if self._raise_on == "status" and is_status:
            raise RuntimeError("status boom")
        if self._raise_on == "edges" and not is_status:
            raise RuntimeError("edges boom")
        rows = self._status_rows if is_status else self._edge_rows
        return SimpleNamespace(rows=rows, row_count=len(rows))


class TestLoadGraph:
    async def test_builds_graph_from_edges(self):
        client = FakeClient(
            edge_rows=[
                {"id": "leaf", "name": "web-01", "avail_depend_resource_id": "m1",
                 "avail_depend_resource_id_2": None},
                {"id": "m1", "name": "app-01", "avail_depend_resource_id": "root",
                 "avail_depend_resource_id_2": None},
            ]
        )
        loader = TopologyLoader()
        graph = await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=86400, max_hops=5
        )
        assert graph is not None
        assert graph.ancestors("leaf", max_hops=5) == ["m1", "root"]
        assert graph.name_of("leaf") == "web-01"

    async def test_secondary_depend_column_as_parent(self):
        client = FakeClient(
            edge_rows=[
                {"id": "leaf", "name": None, "avail_depend_resource_id": "p1",
                 "avail_depend_resource_id_2": "p2"},
            ]
        )
        loader = TopologyLoader()
        graph = await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=86400, max_hops=5
        )
        assert set(graph.ancestors("leaf", max_hops=5)) == {"p1", "p2"}

    async def test_cache_hit_skips_requery(self):
        client = FakeClient(
            edge_rows=[{"id": "leaf", "name": "n", "avail_depend_resource_id": "root",
                        "avail_depend_resource_id_2": None}]
        )
        loader = TopologyLoader()
        g1 = await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=86400, max_hops=5, now=1000.0
        )
        n_after_first = len(client.executed_sqls)
        g2 = await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=86400, max_hops=5, now=1500.0
        )
        assert g1 is g2  # 동일 캐시 인스턴스
        assert len(client.executed_sqls) == n_after_first  # 재조회 없음

    async def test_cache_expiry_requeries(self):
        client = FakeClient(
            edge_rows=[{"id": "leaf", "name": "n", "avail_depend_resource_id": "root",
                        "avail_depend_resource_id_2": None}]
        )
        loader = TopologyLoader()
        await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=100, max_hops=5, now=1000.0
        )
        n1 = len(client.executed_sqls)
        # TTL(100s) 만료 후 → 재조회
        await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=100, max_hops=5, now=1200.0
        )
        assert len(client.executed_sqls) > n1

    async def test_query_failure_returns_none(self):
        client = FakeClient(raise_on="edges")
        loader = TopologyLoader()
        graph = await loader.load_graph(
            client, "polestar_cm_gp", cache_ttl_seconds=86400, max_hops=5
        )
        assert graph is None  # 조회 실패 → None(1홉 폴백)


class TestFetchAncestorState:
    async def test_returns_abnormal_and_names(self):
        client = FakeClient(
            status_rows=[
                {"id": "a", "name": "srv-a", "avail_status": 0},   # 정상
                {"id": "b", "name": "srv-b", "avail_status": 1},   # 비정상
                {"id": "c", "name": "srv-c", "avail_status": 2},   # 비정상
            ]
        )
        loader = TopologyLoader()
        state = await loader.fetch_ancestor_state(client, "polestar_cm_gp", ["a", "b", "c"])
        assert state is not None
        abnormal, names = state
        assert abnormal == {"b", "c"}
        assert names == {"a": "srv-a", "b": "srv-b", "c": "srv-c"}

    async def test_empty_ancestors_returns_empty(self):
        client = FakeClient()
        loader = TopologyLoader()
        state = await loader.fetch_ancestor_state(client, "polestar_cm_gp", [])
        assert state == (set(), {})
        assert client.executed_sqls == []  # 조회 스킵

    async def test_query_failure_returns_none(self):
        client = FakeClient(raise_on="status")
        loader = TopologyLoader()
        state = await loader.fetch_ancestor_state(client, "polestar_cm_gp", ["a"])
        assert state is None  # 실패 → None(1홉 폴백)

    async def test_non_numeric_status_skipped(self):
        client = FakeClient(
            status_rows=[
                {"id": "a", "name": "srv-a", "avail_status": "not-a-number"},
                {"id": "b", "name": "srv-b", "avail_status": None},
                {"id": "c", "name": "srv-c", "avail_status": 3},
            ]
        )
        loader = TopologyLoader()
        state = await loader.fetch_ancestor_state(client, "polestar_cm_gp", ["a", "b", "c"])
        abnormal, names = state
        assert abnormal == {"c"}
