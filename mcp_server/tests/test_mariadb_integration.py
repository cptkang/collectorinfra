"""MariaDB 실연결 통합 테스트 (plans/95 W-S3 · §4.6.6) — `RUN_DOCKER_IT=1` 옵트인.

픽스처: `testdata/itam/setup.sh`(컨테이너 `itam_mariadb` · localhost:3307 · SELECT 전용 `itam_ro`).
연결 정보는 env 주입이며 기본값은 픽스처 문서값이다. **옵트인했는데 미도달이면 skip이 아니라
실패한다**(조용한 skip은 D-181에서 테스트를 전건 통과로 무력화한 경로다).

이 테스트가 증명하는 것은 **엔진 동작**뿐이다 — 픽스처가 전사본에서 파생되므로 운영 식별자(G-4)·
database명(G-2)·서버 변수(G-13)는 증명하지 못한다(plans/95 §4.6.2).
"""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from typing import Any

import pymysql
import pytest
from mcp_server.config import AppServerConfig, SourceConfig
from mcp_server.db import DBPoolManager

_SOURCE = "itam"
_PK = ["groupCoCd", "sevrHostName", "iPCtnt"]


def _conn_parts() -> dict[str, str]:
    return {
        "host": os.environ.get("DOCKER_IT_ITAM_HOST", "localhost"),
        "port": os.environ.get("DOCKER_IT_ITAM_PORT", "3307"),
        "db": os.environ.get("DOCKER_IT_ITAM_DB", "INST1"),
        "user": os.environ.get("DOCKER_IT_ITAM_USER", "itam_ro"),
        "password": os.environ.get("DOCKER_IT_ITAM_PASSWORD", "itam_ro_pass_2024"),
    }


def _dsn() -> str:
    p = _conn_parts()
    return f"mariadb://{p['user']}:{p['password']}@{p['host']}:{p['port']}/{p['db']}"


class _CaptureMCP:
    """@mcp.tool() 등록 함수를 이름→함수로 포획한다(실 도구 클로저를 fake ctx로 호출)."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _run(call):
    async def _do():
        src = SourceConfig(name=_SOURCE, type="mariadb", connection=_dsn())
        pool = DBPoolManager([src])
        await pool.initialize()
        if not await pool.health_check(_SOURCE):
            p = _conn_parts()
            raise RuntimeError(
                f"MariaDB 픽스처 미도달 — {p['user']}@{p['host']}:{p['port']}/{p['db']} 확인 "
                "(RUN_DOCKER_IT=1 · testdata/itam/setup.sh · aiomysql 드라이버)"
            )
        from mcp_server.tools import register_tools

        capture = _CaptureMCP()
        register_tools(capture, expose_execute_sql=True)
        ctx = SimpleNamespace(
            request_context=SimpleNamespace(
                lifespan_context={
                    "pool_manager": pool,
                    "config": AppServerConfig(sources=[src]),
                }
            )
        )
        try:
            return await call(pool, capture.tools, ctx)
        finally:
            await pool.close_all()

    return asyncio.run(_do())


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="MariaDB 샌드박스 미기동 — testdata/itam/setup.sh 실행 후 RUN_DOCKER_IT=1로 옵트인",
)
class TestMariadbDockerIntegration:
    def test_introspection_column_counts_and_pk_order(self):
        """단언 1 — 68/9컬럼 · PK 3열 **순서**."""

        async def _call(pool, tools, ctx):
            out = {}
            for table in ("TCDMSIF80", "TCDMSIF79"):
                out[table] = json.loads(
                    await tools["get_table_schema"](source=_SOURCE, table_name=table, ctx=ctx)
                )
            return out

        schemas = _run(_call)
        assert len(schemas["TCDMSIF80"]["columns"]) == 68
        assert len(schemas["TCDMSIF79"]["columns"]) == 9
        for schema in schemas.values():
            assert schema["source_type"] == "mariadb"
            assert schema["primary_keys"] == _PK
            assert [c["column_name"] for c in schema["columns"][:3]] == _PK
            assert schema["foreign_keys"] == []

    def test_result_keys_match_pg_helper_contract(self):
        """단언 2 — 결과 키 집합이 PG 헬퍼(`_pg_search_objects_sql`·`_pg_get_columns`·
        `_pg_get_primary_keys`)의 별칭과 같다. FK는 픽스처에 없어 키를 관찰할 수 없다."""
        from mcp_server import tools as t

        async def _call(pool, tools, ctx):
            objects = json.loads(await tools["search_objects"](source=_SOURCE, ctx=ctx))
            columns = await t._mariadb_get_columns(pool, _SOURCE, "TCDMSIF79")
            pks = await t._mariadb_get_primary_keys(pool, _SOURCE, "TCDMSIF79")
            return objects, columns, pks

        objects, columns, pks = _run(_call)
        assert {tuple(sorted(r)) for r in objects} == {("name", "schema")}
        assert {r["name"] for r in objects} == {"TCDMSIF79", "TCDMSIF80"}
        assert {tuple(sorted(r)) for r in columns} == {
            ("column_default", "column_name", "data_type", "is_nullable")
        }
        assert {tuple(sorted(r)) for r in pks} == {("column_name",)}

    def test_readonly_account_denied_at_db_layer(self):
        """단언 3 — MCP `readonly` 검증을 거치지 않는 드라이버 직접 경로에서도
        쓰기가 DB 권한으로 막힌다."""

        async def _call(pool, tools, ctx):
            codes = []
            for sql in (
                "INSERT INTO TCDMSIF79 (groupCoCd, sevrHostName, iPCtnt, sysRegiUno, "
                "sysRegiPrcssYMS, sysLastUno, sysLastPrcssYMS) "
                "VALUES ('Z99', 'probe', 'probe', 'T', 'T', 'T', 'T')",
                "CREATE TABLE probe_write (a INT)",
            ):
                with pytest.raises(pymysql.err.OperationalError) as exc:
                    await pool.execute(_SOURCE, sql)
                codes.append(exc.value.args[0])
            return codes

        assert _run(_call) == [1142, 1142]  # ER_TABLEACCESS_DENIED_ERROR

    def test_lowercase_table_name_is_not_found(self):
        """단언 4 — `lower_case_table_names=0`이 실제로 걸려 테이블명 대소문자를 구분한다(§3.4-b).
        인트로스펙션도 같은 규칙이라 describe와 SELECT의 판정이 갈리지 않는다."""

        async def _call(pool, tools, ctx):
            with pytest.raises(pymysql.err.ProgrammingError) as exc:
                await pool.execute(_SOURCE, "SELECT COUNT(*) AS n FROM tcdmsif80")
            schema = json.loads(
                await tools["get_table_schema"](source=_SOURCE, table_name="tcdmsif80", ctx=ctx)
            )
            return exc.value.args[0], schema

        code, schema = _run(_call)
        assert code == 1146  # ER_NO_SUCH_TABLE
        assert schema["columns"] == [] and schema["primary_keys"] == []

    def test_execute_sql_normalizes_types_like_pg_path(self):
        """단언 5 — DECIMAL은 float, CHAR는 str(PG 경로 `_normalize_row`와 같은 파이썬 타입)."""

        async def _call(pool, tools, ctx):
            return json.loads(
                await tools["execute_sql"](
                    source=_SOURCE,
                    sql=(
                        "SELECT sevrCPUUseRt, cPUCnt, groupCoCd, manmenCtrcEndYmd "
                        "FROM TCDMSIF80 WHERE sevrHostName = 'svr-web-01'"
                    ),
                    ctx=ctx,
                )
            )

        result = _run(_call)
        assert result["row_count"] == 1 and result["truncated"] is False
        row = result["rows"][0]
        assert isinstance(row["sevrCPUUseRt"], float) and row["sevrCPUUseRt"] == 10.37
        assert isinstance(row["cPUCnt"], float)
        assert isinstance(row["groupCoCd"], str) and row["groupCoCd"] == "Z99"
        assert isinstance(row["manmenCtrcEndYmd"], str) and len(row["manmenCtrcEndYmd"]) == 8
