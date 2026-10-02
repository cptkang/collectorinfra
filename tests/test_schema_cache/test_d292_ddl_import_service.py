"""D-292 — `DBRegistrationService` DDL 등록(미리보기 · 등록 잡 · 거절).

- 미리보기는 저장 0 · DB 접속 0(`get_table_schema`·`execute_sql` 호출 0)
- 등록은 O-2와 같은 자리(스키마 캐시 · 기준선 스냅샷 · 등록 상태 `schema`)에 쓰고 출처 `ddl`을
  남긴다
- 미리보기와 다른 DDL·엔진(해시 불일치) · 테이블 0개 · 목록 밖 소스 · 미지원 엔진은 거절한다
- MCP로 수집한 기준선과 같은 구조의 DDL은 차이 0으로 보인다(서비스 경로 동등성)
- O-2(MCP) 저장은 종전과 같다 — 스냅샷 기록에 `origin`을 싣지 않는다

MCP는 실제 `DBHubClient` + 가짜 세션, Redis는 페이크, 저장소는 tmp. 네트워크·LLM 0.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.schema_cache.db_registration_service import DBRegistrationService
from src.schema_cache.db_structure_service import DBStructureService
from tests.test_schema_cache.test_plan104_service_fixtures import (
    Env,
    FakeTable,
    make_env,
    make_registry,
)

SRC = "app_beta"

DDL = """
CREATE TABLE public.t_node (id integer NOT NULL, parent_id integer, node_type varchar(20));
CREATE TABLE public.t_asset (id integer NOT NULL, node_id integer, status_cd varchar(10));
ALTER TABLE ONLY public.t_node ADD CONSTRAINT t_node_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.t_asset ADD CONSTRAINT t_asset_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.t_asset
    ADD CONSTRAINT fk FOREIGN KEY (node_id) REFERENCES public.t_node(id);
COMMENT ON COLUMN public.t_asset.status_cd IS '상태 코드';
CREATE INDEX ix ON public.t_asset (node_id);
"""


def _mcp_tables() -> dict[str, FakeTable]:
    """`DDL`과 같은 구조를 MCP 서버 표기로."""
    return {
        "t_node": FakeTable([
            ("id", "integer", False, True), ("parent_id", "integer", True, False),
            ("node_type", "character varying", True, False),
        ]),
        "t_asset": FakeTable(
            [("id", "integer", False, True), ("node_id", "integer", True, False),
             ("status_cd", "character varying", True, False)],
            fks=[("node_id", "t_node", "id")],
        ),
    }


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "postgresql", _mcp_tables())
    e.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상 DB"})
    return e


def _service(env: Env) -> DBRegistrationService:
    return DBRegistrationService(env.config, env.mgr, **env.service_kwargs())


async def _import(env: Env, ddl: str = DDL, engine: str = "postgresql") -> dict[str, Any]:
    service = _service(env)
    preview = await service.preview_ddl(SRC, engine=engine, text=ddl)
    prepared = await service.prepare_ddl_import(
        SRC, engine=engine, text=ddl, expected_hash=preview["snapshot_hash"]
    )
    return await service.run_ddl_import(SRC, prepared, by="admin", ctx=env.ctx)


def _db_access_calls(env: Env) -> list[str]:
    db_tools = ("get_table_schema", "execute_sql")
    return [name for name, _args in env.session.calls if name in db_tools]


class TestPreview:
    async def test_preview_writes_nothing_and_touches_no_db(self, env):
        result = await _service(env).preview_ddl(SRC, engine="postgresql", text=DDL)

        assert result["table_count"] == 2 and result["column_count"] == 6
        assert result["relationship_count"] == 1 and result["primary_key_tables"] == 2
        assert result["source_engine"] == "postgresql"
        assert result["current_snapshot"] is None and result["diff"]["baseline"] is True
        assert result["skipped"] == {"CREATE INDEX": 1}
        asset = next(t for t in result["tables"] if t["name"] == "t_asset")
        status = next(c for c in asset["columns"] if c["name"] == "status_cd")
        assert status == {
            "name": "status_cd", "type": "character varying", "nullable": True,
            "primary_key": False, "references": None, "comment": "상태 코드",
        }

        assert await env.store.load_snapshot(SRC) is None
        assert await env.store.load_registration(SRC) == {}
        assert await env.mgr.get_schema(SRC) is None
        assert _db_access_calls(env) == []

    async def test_same_structure_as_mcp_baseline_shows_no_changes(self, env):
        """MCP로 수집한 기준선과 같은 구조의 DDL은 해시가 같고 차이 0."""
        service = _service(env)
        await service.run_register(SRC, steps=["schema"], tables=None, by="a", ctx=env.ctx)
        baseline = await env.store.load_snapshot(SRC)

        result = await service.preview_ddl(SRC, engine="postgresql", text=DDL)

        assert result["snapshot_hash"] == baseline["hash"]
        assert result["diff"]["has_changes"] is False
        assert result["current_snapshot"]["origin"] == "mcp"
        assert result["warnings"] == []

    async def test_removed_tables_and_engine_mismatch_are_warned(self, env):
        service = _service(env)
        await service.run_register(SRC, steps=["schema"], tables=None, by="a", ctx=env.ctx)

        result = await service.preview_ddl(
            SRC, engine="mariadb", text="CREATE TABLE t_node (id int PRIMARY KEY);"
        )

        assert result["diff"]["tables_removed"] == ["t_asset"]
        assert "t_asset" not in [t["name"] for t in result["tables"]]
        assert "테이블 1개가 이 DDL에 없습니다" in result["warnings"][0]
        assert "선택한 엔진(mariadb)이 소스 엔진(postgresql)과 다릅니다" in result["warnings"][1]


class TestImport:
    async def test_import_saves_cache_baseline_and_step(self, env):
        result = await _import(env)

        step = result["steps"]["schema"]
        assert step["status"] == "ok" and step["count"] == 2 and step["provider"] is None
        assert step["detail"]["origin"] == "ddl" and step["detail"]["engine"] == "postgresql"
        assert step["detail"]["skipped"] == {"CREATE INDEX": 1}
        snapshot = await env.store.load_snapshot(SRC)
        assert snapshot["origin"] == "ddl" and snapshot["by"] == "admin"
        cached = await env.mgr.get_schema(SRC)
        assert sorted(cached["tables"]) == ["t_asset", "t_node"]
        assert {"from": "t_asset.node_id", "to": "t_node.id"} in cached["relationships"]
        assert (await env.store.load_registration(SRC))["schema"]["detail"]["origin"] == "ddl"
        assert _db_access_calls(env) == []

        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())
        detail = await structure.get_detail(SRC)
        assert detail["snapshot"]["origin"] == "ddl" and detail["snapshot"]["table_count"] == 2

    async def test_parse_warnings_mark_step_warning(self, env):
        ddl = DDL + "CREATE TABLE t_x (id int, y_id int REFERENCES t_missing(id));"

        result = await _import(env, ddl)

        assert result["steps"]["schema"]["status"] == "warning"
        assert result["steps"]["schema"]["detail"]["warnings"] == 1
        assert result["steps"]["schema"]["count"] == 3

    async def test_mcp_check_after_ddl_import_sees_no_spurious_diff(self, env):
        """DDL 기준선 뒤 MCP 변경 점검 — 같은 구조면 변경 0(타입·키 표기 동등)."""
        await _import(env)
        service = DBStructureService(env.config, env.mgr, **env.service_kwargs())

        check = await service.run_check(SRC, by="a", code_columns=None, ctx=env.ctx)

        assert check["diff"]["baseline"] is False
        assert check["diff"]["has_changes"] is False, check["diff"]["summary"]

    async def test_mcp_schema_step_keeps_record_shape(self, env):
        """O-2(MCP) 스냅샷 기록에는 `origin`을 싣지 않는다(종전 모양) — 상세는 `mcp`로 보인다."""
        await _service(env).run_register(SRC, steps=["schema"], tables=None, by="a", ctx=env.ctx)

        assert "origin" not in await env.store.load_snapshot(SRC)
        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())
        detail = await structure.get_detail(SRC)
        assert detail["snapshot"]["origin"] == "mcp"


class TestRejections:
    async def test_hash_mismatch(self, env):
        with pytest.raises(ValueError, match="다시 분석"):
            await _service(env).prepare_ddl_import(
                SRC, engine="postgresql", text=DDL, expected_hash="0" * 64
            )

    async def test_zero_tables(self, env):
        service = _service(env)
        preview = await service.preview_ddl(SRC, engine="postgresql", text="SELECT 1;")

        assert preview["table_count"] == 0
        with pytest.raises(ValueError, match="테이블이 없습니다"):
            await service.prepare_ddl_import(
                SRC, engine="postgresql", text="SELECT 1;", expected_hash=preview["snapshot_hash"]
            )

    async def test_source_not_in_list(self, env):
        with pytest.raises(LookupError, match="목록에 없는 소스"):
            await _service(env).preview_ddl("app_nowhere", engine="postgresql", text=DDL)

    async def test_mcp_only_and_active_only_sources_are_listed(self, tmp_path, monkeypatch):
        env = make_env(tmp_path, monkeypatch, active=("app_active",))
        env.session.add_source("app_mcp", "mariadb", {})
        service = _service(env)

        mcp_only = await service.preview_ddl(
            "app_mcp", engine="mariadb", text="CREATE TABLE t (id int);"
        )
        active_only = await service.preview_ddl(
            "app_active", engine="db2", text="CREATE TABLE T (ID INT);"
        )

        assert mcp_only["source_engine"] == "mariadb" and mcp_only["table_count"] == 1
        assert active_only["source_engine"] is None and active_only["table_count"] == 1

    async def test_bad_engine_and_source_name(self, env):
        service = _service(env)
        with pytest.raises(ValueError, match="engine"):
            await service.preview_ddl(SRC, engine="oracle", text=DDL)
        with pytest.raises(ValueError):
            await service.preview_ddl("../x", engine="postgresql", text=DDL)
