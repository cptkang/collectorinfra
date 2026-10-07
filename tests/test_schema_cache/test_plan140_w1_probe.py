"""plans/140 W1-0 — 한글 식별자 조회문(엔진 인용) · 라틴 조회문 바이트 불변 · 컬럼 유일성 조회.

스냅샷에 있는 테이블·컬럼 조각만 받는다. 라틴 조각은 종전처럼 인용 없이, 한글 음절이 든 조각만
엔진 인용(MariaDB·MySQL 백틱 · PostgreSQL·DB2 큰따옴표)으로 감싼다. 인용 문자·공백·구분자가 든
이름은 여전히 거부한다. 네트워크·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.schema_cache import schema_probe as sp
from src.schema_cache.db_structure_service import (
    build_code_value_sql,
    collect_code_values,
    snapshot_identifier,
)
from src.security.sql_guard import SQLGuard
from tests.test_schema_cache.test_plan140_w1_fixtures import snapshot_from_columns

KO_SNAPSHOT = snapshot_from_columns({
    "t_asset": [("자산번호", "char", False), ("상태코드", "char", False),
                ("hostNm", "varchar", False)],
    "t_use": [("자산번호", "char", False), ("사용구분", "char", False)],
    "자산원장": [("자산번호", "char", False), ("등록일자", "char", False)],
})
LATIN_SNAPSHOT = snapshot_from_columns({
    "t_srv": [("hostNm", "varchar", True), ("statCd", "char", False)],
    "t_hw": [("hostNm", "varchar", True), ("endYmd", "varchar", False)],
})


class TestSnapshotIdentifier:
    @pytest.mark.parametrize("engine", ["mariadb", "postgresql", "db2", "mysql", "", None])
    def test_latin_unchanged(self, engine):
        for part in ("hostNm", "HOST_NM", "_x$#1"):
            assert snapshot_identifier(part, engine) == part

    @pytest.mark.parametrize(("engine", "expected"), [
        ("mariadb", "`상태코드`"), ("mysql", "`상태코드`"), ("MariaDB", "`상태코드`"),
        ("postgresql", '"상태코드"'), ("postgres", '"상태코드"'), ("db2", '"상태코드"'),
    ])
    def test_korean_quoted_per_engine(self, engine, expected):
        assert snapshot_identifier("상태코드", engine) == expected

    def test_mixed_korean_latin_quoted(self):
        assert snapshot_identifier("IP주소_1", "postgresql") == '"IP주소_1"'

    @pytest.mark.parametrize("bad", [
        "a b", "상태 코드", 'a"b', "상태`코드", "a.b", "a;b", "1abc", "상태-코드", "", "상태ㄱ",
        "a\nb",
    ])
    def test_rejects(self, bad):
        with pytest.raises(ValueError, match="식별자"):
            snapshot_identifier(bad, "mariadb")

    def test_unknown_engine_rejects_korean_only(self):
        with pytest.raises(ValueError, match="인용 방식"):
            snapshot_identifier("상태코드", "oracle")
        assert snapshot_identifier("statCd", "oracle") == "statCd"


class TestCodeValueSql:
    def test_latin_bytes_unchanged(self):
        assert build_code_value_sql(
            "t_srv", "statCd", engine="mariadb", db_schema=None, table_schema=None, limit=51,
        ) == "SELECT DISTINCT statCd FROM t_srv WHERE statCd IS NOT NULL LIMIT 51"
        assert build_code_value_sql(
            "T_SRV", "STATCD", engine="db2", db_schema="APP", table_schema=None, limit=51,
        ) == ("SELECT DISTINCT STATCD FROM APP.T_SRV WHERE STATCD IS NOT NULL "
              "FETCH FIRST 51 ROWS ONLY")

    @pytest.mark.parametrize(("engine", "db_schema", "expected"), [
        ("mariadb", None,
         "SELECT DISTINCT `상태코드` FROM `자산원장` WHERE `상태코드` IS NOT NULL LIMIT 10"),
        ("postgresql", None,
         'SELECT DISTINCT "상태코드" FROM "자산원장" WHERE "상태코드" IS NOT NULL LIMIT 10'),
        ("db2", "APP",
         'SELECT DISTINCT "상태코드" FROM APP."자산원장" WHERE "상태코드" IS NOT NULL '
         "FETCH FIRST 10 ROWS ONLY"),
    ])
    def test_korean_quoted(self, engine, db_schema, expected):
        sql = build_code_value_sql(
            "자산원장", "상태코드", engine=engine, db_schema=db_schema, table_schema=None, limit=10,
        )
        assert sql == expected
        assert SQLGuard().is_safe_select(sql)[0]

    def test_korean_schema_still_rejected(self):
        with pytest.raises(ValueError, match="스키마 식별자"):
            build_code_value_sql("t", "상태코드", engine="db2", db_schema="업무", table_schema=None,
                                 limit=10)

    async def test_collect_code_values_runs_korean_column(self):
        seen: list[str] = []

        class Client:
            async def execute_sql(self, sql: str) -> Any:
                seen.append(sql)
                return SimpleNamespace(rows=[{"상태코드": "A"}, {"상태코드": "B"}], truncated=False)

        (item,) = await collect_code_values(
            Client(), [{"key": "t_asset.상태코드", "table": "t_asset", "column": "상태코드",
                        "origin": "asset", "known": []}],
            snapshot=KO_SNAPSHOT, engine="mariadb", db_schema=None, limit=51,
        )
        assert item["error"] is None and item["values"] == ["A", "B"]
        assert seen == [
            "SELECT DISTINCT `상태코드` FROM t_asset WHERE `상태코드` IS NOT NULL LIMIT 51"
        ]


class TestOverlapAndPairs:
    def test_latin_overlap_bytes_unchanged(self):
        sql = sp.build_overlap_sql(
            "t_hw", ["hostNm"], "t_srv", ["hostNm"], snapshot=LATIN_SNAPSHOT, engine="mariadb",
            db_schema=None, sample=200,
        )
        assert sql == (
            "SELECT COUNT(*) AS sampled, COUNT(p.hostNm) AS matched FROM (SELECT DISTINCT "
            "c.hostNm AS k0 FROM t_hw c WHERE c.hostNm IS NOT NULL LIMIT 200) s LEFT JOIN t_srv p "
            "ON p.hostNm = s.k0"
        )

    @pytest.mark.parametrize(("engine", "q", "tail", "parent_ref"), [
        ("mariadb", "`", "LIMIT 50", "`자산원장`"),
        ("postgresql", '"', "LIMIT 50", '"자산원장"'),
        ("db2", '"', "FETCH FIRST 50 ROWS ONLY", 'APP."자산원장"'),
    ])
    def test_korean_overlap_quoted(self, engine, q, tail, parent_ref):
        sql = sp.build_overlap_sql(
            "t_use", ["자산번호"], "자산원장", ["자산번호"], snapshot=KO_SNAPSHOT, engine=engine,
            db_schema="APP" if engine == "db2" else None, sample=50,
        )
        col = f"{q}자산번호{q}"
        child_ref = "APP.t_use" if engine == "db2" else "t_use"
        assert sql == (
            f"SELECT COUNT(*) AS sampled, COUNT(p.{col}) AS matched FROM (SELECT DISTINCT "
            f"c.{col} AS k0 FROM {child_ref} c WHERE c.{col} IS NOT NULL {tail}) s "
            f"LEFT JOIN {parent_ref} p ON p.{col} = s.k0"
        )

    async def test_korean_pairs_quoted(self):
        class Client:
            sql: str = ""

            async def execute_sql(self, sql: str) -> Any:
                Client.sql = sql
                return SimpleNamespace(rows=[{"code_value": "A", "code_label": "가"}],
                                       truncated=False)

        out = await sp.sample_pairs(
            Client(), "t_use", "사용구분", "자산번호", snapshot=KO_SNAPSHOT, engine="postgresql",
            db_schema=None, limit=10, budget=sp.ProbeBudget(limit=5),
        )
        assert out["error"] is None and out["pairs"] == [("A", "가")]
        assert Client.sql == ('SELECT DISTINCT "사용구분" AS code_value, "자산번호" AS code_label '
                              'FROM t_use WHERE "사용구분" IS NOT NULL LIMIT 10')


class TestUnique:
    @pytest.mark.parametrize(("engine", "db_schema", "expected"), [
        ("mariadb", None, "SELECT COUNT(*) AS non_null, COUNT(DISTINCT `자산번호`) AS "
                          "distinct_count FROM t_use WHERE `자산번호` IS NOT NULL"),
        ("postgresql", None, 'SELECT COUNT(*) AS non_null, COUNT(DISTINCT "자산번호") AS '
                             'distinct_count FROM t_use WHERE "자산번호" IS NOT NULL'),
        ("db2", "APP", 'SELECT COUNT(*) AS non_null, COUNT(DISTINCT "자산번호") AS '
                       'distinct_count FROM APP.t_use WHERE "자산번호" IS NOT NULL'),
    ])
    def test_sql(self, engine, db_schema, expected):
        sql = sp.build_unique_sql("t_use", "자산번호", snapshot=KO_SNAPSHOT, engine=engine,
                                  db_schema=db_schema)
        assert sql == expected
        assert SQLGuard().is_safe_select(sql)[0]

    def test_latin_sql(self):
        assert sp.build_unique_sql(
            "t_srv", "HOSTNM", snapshot=LATIN_SNAPSHOT, engine="mariadb", db_schema=None,
        ) == ("SELECT COUNT(*) AS non_null, COUNT(DISTINCT hostNm) AS distinct_count FROM t_srv "
              "WHERE hostNm IS NOT NULL")

    def test_unknown_column_rejected(self):
        with pytest.raises(ValueError, match="스냅샷에 없습니다"):
            sp.build_unique_sql("t_use", "없는컬럼", snapshot=KO_SNAPSHOT, engine="mariadb",
                                db_schema=None)

    @pytest.mark.parametrize(("row", "unique"), [
        ({"NON_NULL": 5, "DISTINCT_COUNT": 5}, True),  # DB2 대문자 열 이름
        ({"non_null": 5, "distinct_count": 4}, False),
        ({"non_null": 0, "distinct_count": 0}, False),  # 빈 컬럼은 부모 아님
        ({}, None),
    ])
    async def test_check_unique(self, row, unique):
        class Client:
            async def execute_sql(self, sql: str) -> Any:
                return SimpleNamespace(rows=[row] if row else [], truncated=False)

        out = await sp.check_unique(
            Client(), table="t_use", column="자산번호", snapshot=KO_SNAPSHOT, engine="mariadb",
            db_schema=None, budget=sp.ProbeBudget(limit=5),
        )
        assert out["unique"] is unique and out["error"] is None

    async def test_check_unique_budget_and_failure(self):
        class Boom:
            async def execute_sql(self, sql: str) -> Any:
                raise RuntimeError("down")

        budget = sp.ProbeBudget(limit=1)
        failed = await sp.check_unique(Boom(), table="t_use", column="자산번호",
                                       snapshot=KO_SNAPSHOT, engine="mariadb", db_schema=None,
                                       budget=budget)
        assert failed["unique"] is None and "RuntimeError" in failed["error"]
        over = await sp.check_unique(Boom(), table="t_use", column="자산번호",
                                     snapshot=KO_SNAPSHOT, engine="mariadb", db_schema=None,
                                     budget=budget)
        assert over["error"] == "예산 초과" and budget.skipped == ["unique:t_use.자산번호"]


def test_catalog_query_count():
    assert sp.catalog_query_count(KO_SNAPSHOT, None) == 2
    assert sp.catalog_query_count({"tables": {"a": {"schema": "x"}, "b": {"schema": "y"}}},
                                  None) == 4
