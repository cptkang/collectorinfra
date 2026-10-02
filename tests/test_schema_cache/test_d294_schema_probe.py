"""D-294 W2 — `src/schema_cache/schema_probe.py` (카탈로그 · 값 표본 · 관계 값 겹침 · 예산).

실제 `DBHubClient` + 가짜 MCP 세션(`execute_sql` 응답 콜백)으로 엔진별 SQL 모양 · 가드 통과 ·
결과 매핑 · 실패 격리 · 예산을 확인한다. 네트워크·LLM 0. 이름은 전부 가상(`t_*`).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.domain.schema_snapshot import build_snapshot
from src.schema_cache import schema_probe as sp
from tests.test_schema_cache.test_plan104_service_fixtures import FakeMCPSession, client_factory


def _snapshot(schema: str | None = "app") -> dict[str, Any]:
    tables = {
        "t_parent": {"columns": [{"name": "Id", "type": "int", "primary_key": True},
                                 {"name": "nm", "type": "varchar"}]},
        "t_child": {"columns": [{"name": "id", "type": "int", "primary_key": True},
                                {"name": "parent_id", "type": "int"}]},
    }
    return build_snapshot({"tables": tables, "relationships": []},
                          {k: schema for k in tables} if schema else None)


async def _client(session: FakeMCPSession):
    return client_factory(session)("app_src")


class TestCatalogSql:
    @pytest.mark.parametrize("engine", ["mariadb", "postgresql", "db2"])
    @pytest.mark.parametrize("kind", ["tables", "columns"])
    def test_constant_sql_passes_guard(self, engine, kind):
        sql = sp.build_catalog_sql(engine, kind, "APP")
        assert "'APP'" in sql and ";" not in sql

    def test_mariadb_default_database_and_rejections(self):
        assert "DATABASE()" in sp.build_catalog_sql("mysql", "columns", None)
        with pytest.raises(ValueError, match="스키마명"):
            sp.build_catalog_sql("postgresql", "tables", None)
        with pytest.raises(ValueError, match="스키마 식별자"):
            sp.build_catalog_sql("db2", "tables", "A'B")
        with pytest.raises(ValueError, match="모르는 엔진"):
            sp.build_catalog_sql("oracle", "tables", "A")

    def test_guard_still_blocks_other_patterns(self):
        with pytest.raises(ValueError):
            sp.assert_catalog_select("SELECT SLEEP(5) FROM information_schema.tables")
        with pytest.raises(ValueError):
            sp.assert_catalog_select("SELECT 1; DROP TABLE t")


class TestReadCatalog:
    async def test_maps_rows_to_snapshot_keys(self):
        session = FakeMCPSession()

        def rows(_source: str, sql: str) -> list[dict[str, Any]]:
            if "SYSCAT.TABLES" in sql:
                return [{"TABLE_NAME": "T_PARENT", "TABLE_COMMENT": "부모", "ROW_ESTIMATE": -1},
                        {"TABLE_NAME": "T_OTHER", "TABLE_COMMENT": "목록 밖", "ROW_ESTIMATE": 5}]
            return [{"TABLE_NAME": "T_CHILD", "COLUMN_NAME": "PARENT_ID",
                     "COLUMN_COMMENT": "부모 ID"}]

        session.sql_rows = rows
        budget = sp.ProbeBudget(limit=10)
        async with (await _client(session)) as client:
            info = await sp.read_catalog(
                client, engine="db2", snapshot=_snapshot("APP"), db_schema=None, budget=budget,
            )

        assert info.table_comments == {"t_parent": "부모"}
        assert info.row_estimates == {"t_parent": None}  # CARD -1 = 통계 없음
        assert info.column_comments == {"t_child.parent_id": "부모 ID"}
        assert budget.used == 2 and info.errors == []

    async def test_failure_is_recorded_and_budget_skips(self):
        session = FakeMCPSession()

        def boom(_source: str, _sql: str) -> list[dict[str, Any]]:
            raise RuntimeError("권한 없음")

        session.sql_rows = boom
        budget = sp.ProbeBudget(limit=1)
        async with (await _client(session)) as client:
            info = await sp.read_catalog(
                client, engine="postgresql", snapshot=_snapshot("app"), db_schema=None,
                budget=budget,
            )

        assert len(info.errors) == 1 and "권한 없음" in info.errors[0]
        assert budget.skipped == ["catalog:columns:app"]


class TestReviewFixes:
    """코드 리뷰 지적(2026-10-02) — DB2 스키마 대문자 · 잘림 사유 · 다른 스키마 같은 이름 테이블."""

    def test_db2_schema_literal_is_uppercased(self):
        assert "TABSCHEMA = 'APP'" in sp.build_catalog_sql("db2", "tables", "app")
        assert "nspname = 'app'" in sp.build_catalog_sql("postgresql", "tables", "app")

    async def test_truncation_and_schema_mismatch(self):
        session = FakeMCPSession()

        def rows(_source: str, sql: str) -> list[dict[str, Any]]:
            if "pg_class" in sql and "obj_description" in sql:
                return [{"table_name": "t_parent", "table_comment": "다른 스키마의 같은 이름",
                         "row_estimate": 3}]
            return []

        session.sql_rows = rows
        snapshot = _snapshot("app")
        async with (await _client(session)) as client:
            # 스냅샷 테이블은 app 스키마 — other 스키마 카탈로그 행은 붙지 않는다
            info = await sp.read_catalog(
                client, engine="postgresql",
                snapshot={**snapshot, "tables": {
                    k: {**v, "schema": "app"} for k, v in snapshot["tables"].items()
                }},
                db_schema=None, budget=sp.ProbeBudget(limit=10),
            )
        assert info.table_comments == {"t_parent": "다른 스키마의 같은 이름"}
        assert sp._table_key({"t_parent": {"schema": "app"}}, "other", "t_parent") is None


class _TruncatingClient:
    async def execute_sql(self, _sql: str) -> SimpleNamespace:
        return SimpleNamespace(rows=[], truncated=True)


async def test_truncated_catalog_is_reported():
    info = await sp.read_catalog(
        _TruncatingClient(), engine="mariadb", snapshot=_snapshot("app"), db_schema=None,
        budget=sp.ProbeBudget(limit=10),
    )
    assert len(info.errors) == 2 and all("잘렸습니다" in e for e in info.errors)


class TestSamplesAndOverlap:
    async def test_sample_distinct_with_budget(self):
        session = FakeMCPSession()
        session.sql_rows = lambda _s, _sql: [{"nm": "a"}, {"nm": "b"}]
        budget = sp.ProbeBudget(limit=1)
        async with (await _client(session)) as client:
            out = await sp.sample_distinct(
                client, ["t_parent.nm", "t_parent.Id"], snapshot=_snapshot(), engine="mariadb",
                db_schema=None, limit=10, budget=budget,
            )

        assert out["t_parent.nm"]["values"] == ["a", "b"]
        assert out["t_parent.nm"]["sql"].endswith("LIMIT 10")
        assert out["t_parent.Id"]["error"] == "예산 초과"

    def test_overlap_sql_db2_qualified(self):
        sql = sp.build_overlap_sql(
            "t_child", ["parent_id"], "t_parent", ["id"], snapshot=_snapshot("APP"),
            engine="db2", db_schema="APP", sample=50,
        )
        assert "FROM APP.t_child c" in sql and "LEFT JOIN APP.t_parent p ON p.Id = s.k0" in sql
        assert "FETCH FIRST 50 ROWS ONLY" in sql

    def test_overlap_sql_rejects_bad_input(self):
        with pytest.raises(ValueError, match="컬럼 수"):
            sp.build_overlap_sql("t_child", ["a", "b"], "t_parent", ["Id"], snapshot=_snapshot(),
                                 engine="mariadb", db_schema=None, sample=10)
        with pytest.raises(ValueError, match="스냅샷에 없습니다"):
            sp.build_overlap_sql("t_child", ["nope"], "t_parent", ["Id"], snapshot=_snapshot(),
                                 engine="mariadb", db_schema=None, sample=10)

    async def test_check_overlap_ratio(self):
        session = FakeMCPSession()
        session.sql_rows = lambda _s, _sql: [{"SAMPLED": 40, "MATCHED": 38}]
        async with (await _client(session)) as client:
            out = await sp.check_overlap(
                client, child="t_child", child_columns=["parent_id"], parent="t_parent",
                parent_columns=["Id"], snapshot=_snapshot(), engine="postgresql",
                db_schema=None, budget=sp.ProbeBudget(limit=5),
            )

        assert out["ratio"] == 0.95 and out["error"] is None
