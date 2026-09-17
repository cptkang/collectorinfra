"""DBHubClient plans/104 추가분 테스트 — list_sources · health_check(source) · 관계 파생(B-2).

MCP 응답 형태는 `mcp_server/mcp_server/tools.py`의 list_sources·health_check·search_objects·
get_table_schema 반환 JSON을 따른다. 서버·DB 연결 없이 `_call_tool`만 목으로 바꾼다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.config import DBHubConfig
from src.dbhub.client import DBHubClient
from src.dbhub.models import DBConnectionError, DBHubError


def _text(payload: Any, *, is_error: bool = False) -> SimpleNamespace:
    """MCP CallToolResult 모양(TextContent 1개)."""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=is_error)


@pytest.fixture
def client() -> DBHubClient:
    c = DBHubClient(DBHubConfig(server_url="http://mcp.test/sse", source_name="default_src"))
    c._connected = True
    c._mcp_session = AsyncMock()
    return c


class TestListSources:
    async def test_parses_sources(self, client: DBHubClient) -> None:
        payload = [
            {
                "name": "src_pg",
                "type": "postgresql",
                "readonly": True,
                "query_timeout": 30,
                "max_rows": 10000,
            },
            {
                "name": "src_maria",
                "type": "mariadb",
                "readonly": True,
                "query_timeout": 20,
                "max_rows": 500,
            },
        ]
        client._call_tool = AsyncMock(return_value=_text(payload))  # type: ignore[method-assign]
        result = await client.list_sources()
        assert result == payload
        client._call_tool.assert_awaited_once_with("list_sources", {})

    async def test_empty_list(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(return_value=_text([]))  # type: ignore[method-assign]
        assert await client.list_sources() == []

    @pytest.mark.parametrize(
        "raw",
        [
            _text("not json"),
            _text({"error": "boom"}),
            _text({"name": "x"}),
            _text([{"type": "postgresql"}]),
            _text("알 수 없는 도구", is_error=True),
        ],
    )
    async def test_parse_failures_raise(self, client: DBHubClient, raw: Any) -> None:
        client._call_tool = AsyncMock(return_value=raw)  # type: ignore[method-assign]
        with pytest.raises(DBHubError):
            await client.list_sources()

    async def test_tool_exception_wrapped(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(side_effect=RuntimeError("세션 끊김"))  # type: ignore[method-assign]
        with pytest.raises(DBHubError, match="세션 끊김"):
            await client.list_sources()

    async def test_not_connected(self) -> None:
        c = DBHubClient(DBHubConfig(server_url="http://mcp.test/sse", source_name="default_src"))
        with pytest.raises(DBConnectionError):
            await c.list_sources()


class TestHealthCheck:
    async def test_default_source_unchanged(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(return_value=_text({"status": "healthy"}))  # type: ignore[method-assign]
        assert await client.health_check() is True
        client._call_tool.assert_awaited_once_with("health_check", {"source": "default_src"})

    async def test_explicit_source(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(return_value=_text({"status": "not_found"}))  # type: ignore[method-assign]
        assert await client.health_check("other_src") is False
        client._call_tool.assert_awaited_once_with("health_check", {"source": "other_src"})

    async def test_detail_healthy(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(  # type: ignore[method-assign]
            return_value=_text({"source": "s1", "status": "healthy", "response_time_ms": 1.2})
        )
        detail = await client.health_check_detail("s1")
        assert detail["source"] == "s1"
        assert detail["healthy"] is True
        assert detail["error"] is None
        assert isinstance(detail["latency_ms"], float)

    async def test_detail_not_found_message(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(  # type: ignore[method-assign]
            return_value=_text(
                {"source": "s1", "status": "not_found", "message": "소스 's1'가 등록되지 않음"}
            )
        )
        detail = await client.health_check_detail("s1")
        assert detail["healthy"] is False
        assert detail["error"] == "소스 's1'가 등록되지 않음"

    async def test_detail_unhealthy_without_message(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(return_value=_text({"status": "unhealthy"}))  # type: ignore[method-assign]
        detail = await client.health_check_detail("s1")
        assert detail == {**detail, "healthy": False, "error": "status=unhealthy"}

    async def test_detail_exception_and_unparsable(self, client: DBHubClient) -> None:
        client._call_tool = AsyncMock(side_effect=RuntimeError("연결 거부"))  # type: ignore[method-assign]
        detail = await client.health_check_detail("s1")
        assert detail["healthy"] is False
        assert "연결 거부" in (detail["error"] or "")

        client._call_tool = AsyncMock(return_value=_text("garbage"))  # type: ignore[method-assign]
        assert (await client.health_check_detail("s1"))[
            "error"
        ] == "health_check 응답을 해석하지 못했습니다"

    async def test_detail_timeout(
        self, client: DBHubClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _slow(*_a: Any, **_k: Any) -> Any:
            await asyncio.sleep(1)

        monkeypatch.setattr(DBHubClient, "HEALTH_CHECK_TIMEOUT", 0.01)
        client._call_tool = _slow  # type: ignore[method-assign]
        detail = await client.health_check_detail("s1")
        assert detail["healthy"] is False
        assert "응답 없음" in (detail["error"] or "")


def _fake_mcp(
    tables: list[dict[str, str]],
    table_schemas: dict[str, dict[str, Any]],
) -> tuple[AsyncMock, list[tuple[str, dict[str, Any]]]]:
    """search_objects·get_table_schema만 답하는 가짜 `_call_tool`(호출 기록 포함)."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _call(tool: str, args: dict[str, Any]) -> Any:
        calls.append((tool, args))
        if tool == "search_objects":
            return _text(tables)
        if tool == "get_table_schema":
            return _text(table_schemas[args["table_name"]])
        raise AssertionError(f"예상 밖 도구 호출: {tool}")

    return AsyncMock(side_effect=_call), calls


def _table(
    name: str, columns: list[str], fks: list[tuple[str, str, str]], pk: str = ""
) -> dict[str, Any]:
    return {
        "table_name": name,
        "columns": [
            {
                "column_name": c,
                "data_type": "integer",
                "is_nullable": "YES",
                "is_primary_key": c == pk,
            }
            for c in columns
        ],
        "foreign_keys": [{"from_column": f, "to_table": t, "to_column": tc} for f, t, tc in fks],
    }


class TestFullSchemaRelationships:
    async def test_pg_public_bare_names(self, client: DBHubClient) -> None:
        call, calls = _fake_mcp(
            [{"name": "parent_t", "schema": "public"}, {"name": "child_t", "schema": "public"}],
            {
                "parent_t": _table("parent_t", ["id"], [], pk="id"),
                "child_t": _table(
                    "child_t", ["id", "parent_ref"], [("parent_ref", "parent_t", "id")]
                ),
            },
        )
        client._call_tool = call  # type: ignore[method-assign]
        schema = await client.get_full_schema()
        assert schema.relationships == [{"from": "child_t.parent_ref", "to": "parent_t.id"}]
        # 테이블당 get_table_schema 1회 · FK 전용 SQL(execute_sql) 호출 0
        assert [t for t, _ in calls] == ["search_objects", "get_table_schema", "get_table_schema"]
        assert schema.tables["child_t"].schema_name == "public"

    async def test_pg_non_public_schema_prefix_matches_table_keys(
        self, client: DBHubClient
    ) -> None:
        call, _ = _fake_mcp(
            [{"name": "app.parent_t", "schema": "app"}, {"name": "app.child_t", "schema": "app"}],
            {
                "app.parent_t": _table("app.parent_t", ["id"], [], pk="id"),
                # PG FK 응답의 to_table은 스키마 접두가 없다(ccu.table_name)
                "app.child_t": _table(
                    "app.child_t", ["parent_ref"], [("parent_ref", "parent_t", "id")]
                ),
            },
        )
        client._call_tool = call  # type: ignore[method-assign]
        schema = await client.get_full_schema()
        assert schema.relationships == [{"from": "app.child_t.parent_ref", "to": "app.parent_t.id"}]
        assert set(schema.tables) == {"app.parent_t", "app.child_t"}
        assert schema.tables["app.child_t"].schema_name == "app"

    async def test_db2_uppercase_bare(self, client: DBHubClient) -> None:
        """DB2 목 응답: 대문자 bare 테이블명 · REFTABNAME 대문자 · schema=TABSCHEMA."""
        call, _ = _fake_mcp(
            [
                {"name": "PARENT_T", "schema": "APPSCHEMA"},
                {"name": "CHILD_T", "schema": "APPSCHEMA"},
            ],
            {
                "PARENT_T": _table("PARENT_T", ["ID"], [], pk="ID"),
                "CHILD_T": _table(
                    "CHILD_T", ["ID", "PARENT_REF"], [("PARENT_REF", "PARENT_T", "ID")]
                ),
            },
        )
        client._call_tool = call  # type: ignore[method-assign]
        schema = await client.get_full_schema()
        assert schema.relationships == [{"from": "CHILD_T.PARENT_REF", "to": "PARENT_T.ID"}]
        assert schema.tables["PARENT_T"].schema_name == "APPSCHEMA"
        assert schema.tables["CHILD_T"].columns[1].references == "PARENT_T.ID"

    async def test_mariadb_camelcase_and_multi_fk_same_column(self, client: DBHubClient) -> None:
        call, _ = _fake_mcp(
            [
                {"name": "assetMaster", "schema": "INST0"},
                {"name": "assetLink", "schema": "INST0"},
                {"name": "assetArchive", "schema": "INST0"},
            ],
            {
                "assetMaster": _table("assetMaster", ["assetId"], [], pk="assetId"),
                "assetArchive": _table("assetArchive", ["assetId"], [], pk="assetId"),
                "assetLink": _table(
                    "assetLink",
                    ["assetId"],
                    [
                        ("assetId", "assetMaster", "assetId"),
                        ("assetId", "assetArchive", "assetId"),
                        ("assetId", "assetMaster", "assetId"),  # 중복 행
                    ],
                ),
            },
        )
        client._call_tool = call  # type: ignore[method-assign]
        schema = await client.get_full_schema()
        assert schema.relationships == [
            {"from": "assetLink.assetId", "to": "assetMaster.assetId"},
            {"from": "assetLink.assetId", "to": "assetArchive.assetId"},
        ]

    async def test_no_foreign_keys(self, client: DBHubClient) -> None:
        call, _ = _fake_mcp(
            [{"name": "solo_t", "schema": "public"}],
            {"solo_t": _table("solo_t", ["id"], [])},
        )
        client._call_tool = call  # type: ignore[method-assign]
        schema = await client.get_full_schema()
        assert schema.relationships == []
        assert list(schema.tables) == ["solo_t"]

    async def test_incomplete_fk_rows_skipped(self, client: DBHubClient) -> None:
        call, _ = _fake_mcp(
            [{"name": "a_t", "schema": "public"}],
            {
                "a_t": {
                    **_table("a_t", ["x"], []),
                    "foreign_keys": [
                        {"from_column": "x", "to_table": "", "to_column": "id"},
                        {"from_column": "", "to_table": "b_t", "to_column": "id"},
                    ],
                }
            },
        )
        client._call_tool = call  # type: ignore[method-assign]
        assert (await client.get_full_schema()).relationships == []

    async def test_get_table_schema_signature_unchanged(self, client: DBHubClient) -> None:
        call, _ = _fake_mcp(
            [], {"t1": _table("t1", ["id", "other_ref"], [("other_ref", "t2", "id")])}
        )
        client._call_tool = call  # type: ignore[method-assign]
        table = await client.get_table_schema("t1")
        assert table.name == "t1"
        assert table.columns[1].is_foreign_key is True


class TestResolveFkTableRule:
    """FK 대상 표기를 `schema.tables` 키 표기와 맞추는 규칙 고정."""

    @pytest.mark.parametrize(
        ("keys", "from_table", "to_table", "expected"),
        [
            # 1. 참조 테이블의 접두를 붙인 이름이 키에 있으면 우선
            (["s.a_t", "s.b_t", "b_t"], "s.a_t", "b_t", "s.b_t"),
            # 2. 접두 없는 이름이 키에 있으면 그대로
            (["a_t", "b_t"], "a_t", "b_t", "b_t"),
            (["s.a_t", "b_t"], "s.a_t", "b_t", "b_t"),
            # 3. 대소문자 무시 유일 일치
            (["A_T", "B_T"], "A_T", "b_t", "B_T"),
            (["s.a_t", "S.B_T"], "s.a_t", "b_t", "S.B_T"),
            # 4. 목록 밖 — 접두 표기(없으면 받은 이름)
            (["s.a_t"], "s.a_t", "gone_t", "s.gone_t"),
            (["a_t"], "a_t", "gone_t", "gone_t"),
            # 대소문자 무시로 둘 이상 걸리면 목록 밖으로 본다
            (["a_t", "B_t", "b_T"], "a_t", "b_t", "b_t"),
        ],
    )
    def test_rule(self, keys: list[str], from_table: str, to_table: str, expected: str) -> None:
        assert DBHubClient._resolve_fk_table(keys, from_table, to_table) == expected
