"""plans/104 B-1·B-3~B-5 — `DBRegistrationService` (등록 잡 · 설명 초안 · 준비도 · 설정 조각).

계획서 verify: MCP `get_table_schema` 호출 수 = 테이블 수(수집 1회 공유) · 미활성 소스 등록 성공
· 지문 SQL 미호출(DB2 목 소스) · 등록 뒤 `get_schema_or_fetch` 캐시 히트 · 예상 호출 수 = 실제
호출 수 · 실패 테이블만 재실행 · 제외 테이블 미적용 · 적용 뒤 operator 유사어·manual DB 설명 유지
· 레지스트리 조각 파싱·가상 DB 편입 리허설 · 로컬 산출물 머리말 · 파일 쓰기 0
(레지스트리·.env·mcp_server).

MCP는 실제 `DBHubClient` + 가짜 세션, LLM은 목, Redis는 페이크, 저장소는 tmp 경로의 실제 저장소.
"""

from __future__ import annotations

import asyncio
import builtins
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.dbhub.client import DBHubClient
from src.routing.domain_config import build_domains
from src.routing.registry import load_registry, parse_registry
from src.schema_cache.db_registration_service import (
    SERVER_VARIABLE_SQL,
    DBRegistrationService,
    assert_constant_select,
)
from src.schema_cache.db_structure_service import (
    LOCAL_SANDBOX_HEADER,
    DBStructureService,
    DraftNotApprovable,
)
from src.schema_cache.fingerprint import FINGERPRINT_SQL
from tests.test_schema_cache.test_plan104_service_fixtures import (
    LOCAL_ENV,
    REMOTE_ENV,
    Env,
    FakeTable,
    MockLLM,
    default_describe,
    make_env,
    make_registry,
    table_in_prompt,
)

REPO = Path(__file__).resolve().parents[2]
SRC = "app_beta"


def _tables(schema: str = "public") -> dict[str, FakeTable]:
    tables = {
        "t_node": FakeTable([
            ("id", "integer", False, True),
            ("parent_id", "integer", True, False),
            ("node_type", "character varying", True, False),
        ]),
        "t_asset": FakeTable(
            [("id", "integer", False, True), ("node_id", "integer", True, False),
             ("status_cd", "character varying", True, False)],
            fks=[("node_id", "t_node", "id")],
        ),
        "attr_store": FakeTable(
            [("entity_id", "integer", False, False),
             ("attr_name", "character varying", False, False),
             ("attr_value", "character varying", True, False)],
            fks=[("entity_id", "t_asset", "id")],
        ),
    }
    for table in tables.values():
        table.schema = schema
    return tables


EAV_PROFILE: dict[str, Any] = {
    "source": "manual",
    "patterns": [{
        "type": "eav", "entity_table": "t_asset", "config_table": "attr_store",
        "attribute_column": "attr_name", "value_column": "attr_value",
    }],
    "query_guide": "수동 안내",
}


def _sql_rows(source: str, sql: str) -> list[dict[str, Any]]:
    if sql == SERVER_VARIABLE_SQL["postgresql"]:
        return [{"version": "PostgreSQL 16.0", "server_encoding": "UTF8", "search_path": "public"}]
    if sql == SERVER_VARIABLE_SQL["db2"]:
        return [{"current_server": "APPDB", "current_schema": "APPSCHEMA"}]
    if sql == SERVER_VARIABLE_SQL["mariadb"]:
        return [{"version": "11.4.0-MariaDB", "sql_mode": "STRICT_TRANS_TABLES",
                 "lower_case_table_names": 0, "collation_server": "utf8mb4_general_ci"}]
    if sql.startswith("SELECT DISTINCT attr_name "):
        return [{"attr_name": "Alpha"}, {"attr_name": "Beta"}]
    raise RuntimeError(f"예상하지 못한 SQL: {sql}")


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "postgresql", _tables())
    e.session.sql_rows = _sql_rows
    e.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상 DB"})
    return e


def _service(env: Env) -> DBRegistrationService:
    return DBRegistrationService(env.config, env.mgr, **env.service_kwargs())


async def _description_draft_id(env: Env) -> str:
    result = await _register(env, ["descriptions"])
    return str(result["steps"]["descriptions"]["detail"]["draft_id"])


async def _register(env: Env, steps: list[str], tables: list[str] | None = None) -> dict[str, Any]:
    return await _service(env).run_register(
        SRC, steps=steps, tables=tables, by="admin", ctx=env.ctx
    )


# ──────────────────────────────────────────────
# B-3 스키마 수집·캐시 등록
# ──────────────────────────────────────────────


class TestSchemaStep:
    async def test_single_collection_shared_by_cache_and_snapshot(self, env):
        """`get_table_schema` 호출 수 = 테이블 수 — 캐시와 스냅샷이 수집 1회를 공유한다."""
        result = await _register(env, ["schema"])

        assert result["steps"]["schema"]["status"] == "ok"
        assert result["steps"]["schema"]["count"] == 3
        assert len(env.session.tool_calls("search_objects")) == 1
        assert len(env.session.tool_calls("get_table_schema")) == 3
        cached = await env.mgr.get_schema(SRC)
        assert sorted(cached["tables"]) == ["attr_store", "t_asset", "t_node"]
        assert {"from": "t_asset.node_id", "to": "t_node.id"} in cached["relationships"]
        snapshot = await env.store.load_snapshot(SRC)
        assert snapshot["snapshot"]["table_count"] == 3 and snapshot["env"] == REMOTE_ENV
        registration = await env.store.load_registration(SRC)
        assert registration["schema"]["env"] == REMOTE_ENV
        assert registration["schema"]["provider"] is None  # LLM 단계가 아니다

    async def test_cache_hit_after_registration(self, env):
        """등록 뒤 질의 경로 `get_schema_or_fetch`가 수집·지문 SQL 없이 캐시 히트한다."""
        await _register(env, ["schema"])
        query_client = MagicMock()
        query_client.get_full_schema = AsyncMock()
        query_client.execute_sql = AsyncMock()

        schema, hit, source, _descriptions, _synonyms = await env.mgr.get_schema_or_fetch(
            query_client, SRC
        )

        assert hit is True and source in ("Redis", "메모리")
        assert sorted(schema["tables"]) == ["attr_store", "t_asset", "t_node"]
        query_client.get_full_schema.assert_not_awaited()
        query_client.execute_sql.assert_not_awaited()

    async def test_db2_source_registers_without_fingerprint_sql(self, tmp_path, monkeypatch):
        """지문 SQL(information_schema 전용)을 부르지 않아 DB2 목 소스도 등록된다."""
        env = make_env(tmp_path, monkeypatch)
        env.session.add_source("db2_src", "db2", _tables(schema="APPSCHEMA"))
        env.session.sql_rows = _sql_rows
        env.registry = make_registry(
            {"db_id": "db2_src", "engine": "db2", "db_schema": "APPSCHEMA", "description": "d"}
        )

        service = DBRegistrationService(env.config, env.mgr, **env.service_kwargs())
        result = await service.run_register(
            "db2_src", steps=["probe", "schema"], tables=None, by="admin", ctx=env.ctx
        )

        assert result["steps"]["schema"]["status"] == "ok"
        assert result["steps"]["probe"]["status"] == "ok"
        executed = env.session.executed_sql()
        assert FINGERPRINT_SQL not in executed
        assert executed == [SERVER_VARIABLE_SQL["db2"]]
        variables = result["steps"]["probe"]["detail"]["server_variables"]
        assert variables["values"]["current_schema"] == "APPSCHEMA"

    async def test_inactive_unregistered_source_via_default_client(self, tmp_path, monkeypatch):
        """기본 클라이언트(`get_db_client`)로 미등록·미활성 소스를 등록한다(G-6 (a))."""
        env = make_env(tmp_path, monkeypatch, active=("other_src",))
        env.session.add_source("new_src", "mariadb", _tables(schema="APPDB"))
        env.session.sql_rows = _sql_rows
        session = env.session

        async def fake_connect(self: DBHubClient) -> None:
            self._mcp_session = session
            self._connected = True

        monkeypatch.setattr(DBHubClient, "connect", fake_connect)
        monkeypatch.setattr(DBHubClient, "disconnect", AsyncMock())
        service = DBRegistrationService(
            env.config, env.mgr, llm_factory=lambda: env.llm,
            registry_getter=lambda: make_registry(), store=env.store,
        )

        result = await service.run_register(
            "new_src", steps=["schema", "probe"], tables=None, by="admin", ctx=env.ctx
        )

        assert list(result["steps"]) == ["probe", "schema"]  # 고정 순서
        assert result["steps"]["schema"]["status"] == "ok"
        probe = result["steps"]["probe"]
        assert probe["status"] == "ok"
        assert probe["detail"]["engine_match"] is None
        assert "레지스트리 미등록" in probe["detail"]["registry_note"]
        assert probe["detail"]["server_variables"]["values"]["lower_case_table_names"] == 0
        assert {args["source"] for args in session.tool_calls("get_table_schema")} == {"new_src"}

    async def test_step_failure_is_recorded_and_next_step_runs(self, env):
        env.session.tables[SRC] = {}  # 테이블 0개 → 캐시 저장 거부
        result = await _register(env, ["schema", "seeds"])
        assert result["steps"]["schema"]["status"] == "failed"
        assert "거부" in result["steps"]["schema"]["detail"]["error"]
        assert result["steps"]["seeds"]["status"] == "skipped"

    async def test_rejects_unknown_steps(self, env):
        with pytest.raises(ValueError, match="알 수 없는 단계"):
            await _register(env, ["schema", "warmup"])
        with pytest.raises(ValueError, match="비어 있음"):
            await _register(env, [])


class TestProbe:
    async def test_unhealthy_and_engine_mismatch(self, env):
        env.session.health[SRC] = False
        result = await _register(env, ["probe"])
        assert result["steps"]["probe"]["status"] == "failed"

        env.session.health[SRC] = True
        env.registry = make_registry({"db_id": SRC, "engine": "db2", "description": "d"})
        result = await _register(env, ["probe"])
        probe = result["steps"]["probe"]
        assert probe["status"] == "warning" and probe["detail"]["engine_match"] is False

    def test_server_variable_sql_constants_are_safe(self):
        for sql in SERVER_VARIABLE_SQL.values():
            assert_constant_select(sql)
        for bad in ("SELECT 1; DROP TABLE t", "DELETE FROM t", "SELECT SLEEP(5)",
                    "SELECT * FROM t UNION SELECT * FROM u"):
            with pytest.raises(ValueError):
                assert_constant_select(bad)


# ──────────────────────────────────────────────
# B-4 설명 초안 · 적용
# ──────────────────────────────────────────────


class ConcurrencyLLM(MockLLM):
    """설명 호출의 동시 실행 수를 잰다."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.in_flight = 0
        self.max_in_flight = 0

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return await super().ainvoke(messages)
        finally:
            self.in_flight -= 1


class TestDescriptions:
    async def test_estimate_equals_actual_calls(self, env):
        """예상 LLM 호출 수(get_registration) = 실제 설명 생성 호출 수."""
        await _register(env, ["schema"])
        registration = await _service(env).get_registration(SRC)
        estimate = registration["estimate"]
        assert estimate["description_calls"] == 3 and estimate["scope"] == "all"
        assert estimate["structure_calls"] == 1  # FK로 모두 이어진 3테이블 · 상한 40

        await _register(env, ["descriptions"])

        assert len(env.llm.kinds("describe")) == estimate["description_calls"]

    async def test_profile_allowed_tables_limit_scope(self, env):
        env.write_profile(SRC, {**EAV_PROFILE, "allowed_tables": ["T_ASSET", "t_node", "t_gone"]})
        await _register(env, ["schema"])
        estimate = (await _service(env).get_registration(SRC))["estimate"]

        result = await _register(env, ["descriptions"])

        assert estimate["description_calls"] == 2 and estimate["scope"] == "profile_allowed_tables"
        assert len(env.llm.kinds("describe")) == 2
        scope_names = result["steps"]["descriptions"]["detail"]["scope_table_names"]
        assert scope_names == ["t_asset", "t_node"]

    async def test_concurrency_limit_read_at_job_start(self, env):
        """동시성 상한은 잡 시작 시점 설정을 쓴다(서비스 생성 뒤 바꾼 값이 반영된다)."""
        env.llm = ConcurrencyLLM()
        await _register(env, ["schema"])
        service = _service(env)
        env.config.schema_cache.admin_llm_concurrency = 1

        await service.run_register(SRC, steps=["descriptions"], tables=None, by="a", ctx=env.ctx)
        assert env.llm.max_in_flight == 1

        env.config.schema_cache.admin_llm_concurrency = 3
        env.llm.max_in_flight = 0
        await service.run_register(SRC, steps=["descriptions"], tables=None, by="a", ctx=env.ctx)
        assert env.llm.max_in_flight == 3

    async def test_rerun_only_failed_tables(self, env):
        """실패 테이블 목록을 남기고, 그 테이블만 다시 돌리면 LLM 호출도 그 수만큼이다."""
        broken = {"t_node"}

        def describe(prompt: str) -> str:
            return "JSON 아님" if table_in_prompt(prompt) in broken else default_describe(prompt)

        env.llm = MockLLM(describe=describe)
        await _register(env, ["schema"])
        first = await _register(env, ["descriptions"])
        detail = first["steps"]["descriptions"]["detail"]
        assert first["steps"]["descriptions"]["status"] == "draft"
        assert detail["failed_tables"] == ["t_node"] and detail["succeeded_tables"] == 2
        first_calls = len(env.llm.kinds("describe"))

        broken.clear()
        second = await _register(env, ["descriptions"], tables=detail["failed_tables"])

        assert len(env.llm.kinds("describe")) - first_calls == 1
        second_detail = second["steps"]["descriptions"]["detail"]
        assert second_detail["failed_tables"] == []
        assert sorted(second_detail["scope_table_names"]) == ["attr_store", "t_asset", "t_node"]
        drafts = await env.store.list_description_drafts(SRC)
        assert list(drafts[0]["descriptions"]) == ["t_node"]

    async def test_excluded_tables_are_not_applied(self, env):
        await _register(env, ["schema"])
        result = await _register(env, ["descriptions"])
        draft_id = result["steps"]["descriptions"]["detail"]["draft_id"]

        applied = await _service(env).apply_description_draft(
            SRC, draft_id, exclude_tables=["t_asset"], by="admin"
        )

        descriptions = await env.mgr.get_descriptions(SRC)
        assert descriptions and not any(k.startswith("t_asset.") for k in descriptions)
        assert "t_node.parent_id" in descriptions
        synonyms = await env.mgr.get_synonyms(SRC)
        assert not any(k.startswith("t_asset.") for k in synonyms)
        assert applied["coverage"] == {"scope": 3, "applied": 2, "excluded": 1}
        report = await _service(env).readiness(SRC)
        c8 = next(i for i in report.items if i.code == "C8")
        assert c8.ok is True, c8.detail
        draft = await env.store.get_description_draft(SRC, draft_id)
        assert draft["status"] == "applied" and draft["excluded_tables"] == ["t_asset"]
        with pytest.raises(DraftNotApprovable) as exc:
            await _service(env).apply_description_draft(SRC, draft_id, exclude_tables=None, by="a")
        assert exc.value.code == "not_pending"

    async def test_unknown_exclude_and_env_mismatch(self, env):
        await _register(env, ["schema"])
        draft_id = await _description_draft_id(env)
        with pytest.raises(ValueError, match="초안에 없는 제외 테이블"):
            await _service(env).apply_description_draft(
                SRC, draft_id, exclude_tables=["t_x"], by="a"
            )
        env.config.dbhub.server_url = LOCAL_ENV
        with pytest.raises(DraftNotApprovable) as exc:
            await _service(env).apply_description_draft(SRC, draft_id, exclude_tables=None, by="a")
        assert exc.value.code == "env_mismatch"
        assert await env.mgr.get_descriptions(SRC) == {}

    async def test_operator_synonyms_and_manual_db_description_survive(self, env):
        """적용 뒤에도 operator 유사어와 manual DB 설명이 남는다(S5 · R10)."""
        await _register(env, ["schema"])
        await env.mgr.add_synonyms(SRC, "t_asset.status_cd", ["운영자어"], source="operator")
        await env.mgr.save_db_description(SRC, "사람이 쓴 설명", origin="manual")

        result = await _register(env, ["descriptions", "db_description"])
        draft_id = result["steps"]["descriptions"]["detail"]["draft_id"]
        db_draft = result["steps"]["db_description"]
        assert db_draft["status"] == "draft" and db_draft["provider"] == "mlx/fake-local-model"
        assert db_draft["detail"]["current_origin"] == "manual"

        applied = await _service(env).apply_description_draft(
            SRC, draft_id, exclude_tables=None, by="admin"
        )
        set_result = await _service(env).set_db_description(
            SRC, text=db_draft["detail"]["draft_text"], origin="llm", by="admin"
        )

        entry = (await env.store.redis_cache.load_synonyms_with_sources(SRC))["t_asset.status_cd"]
        assert set(entry["words"]) == {"운영자어", "status_cd_별칭"}
        assert entry["sources"] == {"운영자어": "operator", "status_cd_별칭": "llm"}
        assert set_result["saved"] is False and set_result["preserved_manual"] is True
        assert await env.mgr.get_db_description(SRC) == "사람이 쓴 설명"
        assert (await env.store.load_registration(SRC))["db_description"]["status"] == "skipped"
        # 전역 동기화 · 설명 백업(B-6)
        assert "status_cd" in await env.mgr.get_global_synonyms()
        backup = env.store.load_descriptions_backup(SRC)
        assert backup["synonyms"]["t_asset.status_cd"]["sources"]["운영자어"] == "operator"
        assert Path(applied["backup_path"]).is_file()

    async def test_manual_db_description_set(self, env):
        result = await _service(env).set_db_description(
            SRC, text="  관리자 설명  ", origin="manual", by="admin"
        )
        assert result["saved"] is True
        assert await env.mgr.get_db_description(SRC) == "관리자 설명"
        assert await env.mgr.get_db_description_origin(SRC) == "manual"
        with pytest.raises(ValueError):
            await _service(env).set_db_description(SRC, text="x", origin="guess", by="admin")
        with pytest.raises(ValueError):
            await _service(env).set_db_description(SRC, text="  ", origin="manual", by="admin")

    async def test_discard_draft(self, env):
        await _register(env, ["schema"])
        draft_id = await _description_draft_id(env)
        result = await _service(env).discard_description_draft(SRC, draft_id, by="admin")
        assert result["status"] == "discarded"
        assert (await env.store.load_registration(SRC))["descriptions"]["status"] == "discarded"
        assert await env.mgr.get_descriptions(SRC) == {}


# ──────────────────────────────────────────────
# O-7 시드 · 값 인덱스
# ──────────────────────────────────────────────


class TestSeedsAndValueIndex:
    async def test_seed_file_loaded_with_operator_tag(self, env):
        seeds = env.tmp / "config" / "synonym_seeds"
        seeds.mkdir(parents=True)
        (seeds / f"{SRC}.yaml").write_text(yaml.safe_dump({
            "db_id": SRC, "source_tag": "operator",
            "column_synonyms": {"t_asset.status_cd": ["상태코드"]},
        }, allow_unicode=True), encoding="utf-8")

        result = await _register(env, ["seeds"])

        assert result["steps"]["seeds"]["status"] == "ok" and result["steps"]["seeds"]["count"] == 1
        entry = (await env.store.redis_cache.load_synonyms_with_sources(SRC))["t_asset.status_cd"]
        assert entry["sources"]["상태코드"] == "operator"
        report = await _service(env).readiness(SRC)
        assert next(i for i in report.items if i.code == "C9").ok is True

    async def test_seed_db_id_mismatch_fails(self, env):
        seeds = env.tmp / "config" / "synonym_seeds"
        seeds.mkdir(parents=True)
        (seeds / f"{SRC}.yaml").write_text("db_id: other_src\n", encoding="utf-8")
        result = await _register(env, ["seeds"])
        assert result["steps"]["seeds"]["status"] == "failed"

    async def test_value_index_for_eav_profile(self, env):
        env.write_profile(SRC, EAV_PROFILE)
        await _register(env, ["schema"])
        result = await _register(env, ["value_index"])

        step = result["steps"]["value_index"]
        assert step["status"] == "ok" and step["count"] == 1
        assert "쓰이지 않습니다" in step["detail"]["note"]  # SYNONYM_VALUE_RETRIEVAL off
        index = await env.store.redis_cache.load_column_value_index(SRC)
        assert index == {"attr_store.attr_name": ["Alpha", "Beta"]}

    async def test_value_index_skipped_without_eav(self, env):
        result = await _register(env, ["value_index"])
        assert result["steps"]["value_index"]["status"] == "skipped"


# ──────────────────────────────────────────────
# B-1 준비도
# ──────────────────────────────────────────────


class TestReadiness:
    async def test_full_flow_meets_required_items(self, tmp_path, monkeypatch):
        """스키마 등록 → 구조 분석 승인 → 설명 적용 뒤 필수 7/7 · 권장 2/2."""
        env = make_env(tmp_path, monkeypatch, active=(SRC,))
        env.session.add_source(SRC, "postgresql", _tables())
        env.session.sql_rows = _sql_rows
        env.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상"})
        registration = DBRegistrationService(env.config, env.mgr, **env.service_kwargs())
        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())

        before = await registration.readiness(SRC)
        assert before.ready is False
        assert {i.code for i in before.unmet_required()} == {"C5", "C6", "C7"}

        result = await registration.run_register(
            SRC, steps=["probe", "schema", "descriptions"], tables=None, by="admin", ctx=env.ctx
        )
        analyzed = await structure.run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )
        await structure.approve_draft(SRC, analyzed["draft_id"], by="admin", reason="승인")
        await registration.apply_description_draft(
            SRC, result["steps"]["descriptions"]["detail"]["draft_id"], exclude_tables=None,
            by="admin",
        )

        after = await registration.readiness(SRC)
        assert after.ready is True, [(i.code, i.detail) for i in after.unmet_required()]
        assert after.recommended_met == 2
        assert next(i for i in after.items if i.code == "C6").detail.startswith("승인본")
        many = await registration.readiness_many([SRC, "bad.id", SRC])
        assert list(many) == [SRC, "bad.id"]
        assert many["bad.id"].ready is False


# ──────────────────────────────────────────────
# B-5 설정 조각 · R11 파일 쓰기 0
# ──────────────────────────────────────────────


class TestConfigSnippets:
    async def test_snippet_parses_and_onboards_virtual_db(self, tmp_path, monkeypatch):
        """레지스트리 조각이 `parse_registry`·`load_registry`로 읽히고 편입 리허설을 통과한다."""
        env = make_env(tmp_path, monkeypatch)
        env.session.add_source("acme_new_src", "mariadb", _tables(schema="APPDB"))
        env.session.sql_rows = _sql_rows
        env.llm = MockLLM(db_description="가상 자산 계약 데이터를 관리하는 DB")
        service = DBRegistrationService(
            env.config, env.mgr, **{**env.service_kwargs(), "registry_getter": make_registry}
        )
        await service.run_register(
            "acme_new_src", steps=["probe", "schema", "db_description"], tables=None, by="admin",
            ctx=env.ctx,
        )

        snippet = await service.config_snippets("acme_new_src")

        assert snippet["local_sandbox"] is False
        assert LOCAL_SANDBOX_HEADER not in snippet["registry_yaml"]
        parsed = parse_registry(yaml.safe_load(snippet["registry_yaml"]))
        entry = parsed.get("acme_new_src")
        assert entry.engine == "mariadb" and entry.db_schema == "APPDB"
        assert entry.description == "가상 자산 계약 데이터를 관리하는 DB"
        assert "# 사람 검토 필요" in snippet["registry_yaml"]
        assert "env_connection_key" not in snippet["registry_yaml"]

        data = yaml.safe_load((REPO / "config" / "db_registry.yaml").read_text(encoding="utf-8"))
        data["databases"].append(yaml.safe_load(snippet["registry_yaml"])["databases"][0])
        rehearsal = tmp_path / "db_registry.yaml"
        rehearsal.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        new_registry = load_registry(rehearsal)
        domain = next(d for d in build_domains(new_registry) if d.db_id == "acme_new_src")
        assert domain.db_engine == "mariadb" and domain.db_schema == "APPDB"

    async def test_local_sandbox_header(self, tmp_path, monkeypatch):
        env = make_env(tmp_path, monkeypatch, env=LOCAL_ENV)
        env.session.add_source(SRC, "postgresql", _tables())
        env.registry = make_registry({"db_id": SRC, "engine": "postgresql", "description": "가상"})

        snippet = await _service(env).config_snippets(SRC)

        assert snippet["registry_yaml"].splitlines()[0] == LOCAL_SANDBOX_HEADER
        assert snippet["local_sandbox"] is True
        assert any("로컬 샌드박스" in note for note in snippet["notes"])


class _WriteRecorder:
    """파일 쓰기 경로를 기록한다(builtins.open 쓰기 모드 · Path.write_* · os.replace/rename)."""

    def __init__(self, monkeypatch: Any) -> None:
        self.paths: list[Path] = []
        real_open = builtins.open
        real_write_text = Path.write_text
        real_write_bytes = Path.write_bytes
        real_replace = os.replace
        real_rename = os.rename
        recorder = self

        def _record(target: Any) -> None:
            try:
                recorder.paths.append(Path(os.fspath(target)).resolve())
            except TypeError:  # 파일 디스크립터 등
                pass

        def spy_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
            if any(flag in mode for flag in "wax+"):
                _record(file)
            return real_open(file, mode, *args, **kwargs)

        def spy_write_text(self: Path, *args: Any, **kwargs: Any) -> Any:
            _record(self)
            return real_write_text(self, *args, **kwargs)

        def spy_write_bytes(self: Path, *args: Any, **kwargs: Any) -> Any:
            _record(self)
            return real_write_bytes(self, *args, **kwargs)

        def spy_replace(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
            _record(dst)
            return real_replace(src, dst, *args, **kwargs)

        def spy_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> Any:
            _record(dst)
            return real_rename(src, dst, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", spy_open)
        monkeypatch.setattr(Path, "write_text", spy_write_text)
        monkeypatch.setattr(Path, "write_bytes", spy_write_bytes)
        monkeypatch.setattr(os, "replace", spy_replace)
        monkeypatch.setattr(os, "rename", spy_rename)

    def under(self, root: Path) -> list[Path]:
        root = root.resolve()
        return [p for p in self.paths if p == root or root in p.parents]


def _digest(paths: list[Path]) -> dict[str, str | None]:
    return {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None for p in paths
    }


class TestNoConfigWrites:
    async def test_full_admin_flow_never_writes_registry_env_or_mcp_server(self, env, monkeypatch):
        """R11 — 레지스트리·.env·mcp_server에는 쓰지 않는다. 프로필 쓰기는 승인·되돌리기에서만."""
        guarded = [
            REPO / "config" / "db_registry.yaml", REPO / ".env", REPO / ".encenv",
            REPO / "mcp_server" / "config.toml", REPO / "mcp_server" / ".env",
        ]
        before = _digest(guarded)
        env.write_profile(SRC, EAV_PROFILE, header="# 사람 주석\n")
        seeds = env.tmp / "config" / "synonym_seeds"
        seeds.mkdir(parents=True)
        (seeds / f"{SRC}.yaml").write_text(
            f"db_id: {SRC}\ncolumn_synonyms:\n  t_asset.status_cd: [상태코드]\n", encoding="utf-8"
        )
        recorder = _WriteRecorder(monkeypatch)
        registration = _service(env)
        structure = DBStructureService(env.config, env.mgr, **env.service_kwargs())

        await structure.list_sources()
        await structure.run_check(SRC, by="admin", code_columns=["t_asset.status_cd"], ctx=env.ctx)
        env.session.sql_rows = lambda source, sql: (
            [{"id": 1}] if "LIMIT 5" in sql else _sql_rows_lenient(sql)
        )
        env.llm = MockLLM(
            structure=lambda p: json.dumps({"patterns": [], "query_guide": "안내"}),
        )
        analyzed = await structure.run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )
        steps = await registration.run_register(
            SRC,
            steps=["probe", "schema", "descriptions", "db_description", "seeds", "value_index"],
            tables=None, by="admin", ctx=env.ctx,
        )
        await registration.apply_description_draft(
            SRC, steps["steps"]["descriptions"]["detail"]["draft_id"], exclude_tables=None,
            by="admin",
        )
        await registration.set_db_description(SRC, text="설명", origin="manual", by="admin")
        await registration.config_snippets(SRC)
        await structure.get_detail(SRC)
        await structure.diff_report(SRC, analyzed["draft_id"])
        profile_writes_before_approval = recorder.under(env.profiles_dir)

        approved = await structure.approve_draft(SRC, analyzed["draft_id"], by="admin", reason="ok")
        await structure.rollback(SRC, 0, by="admin", reason="되돌림")

        assert profile_writes_before_approval == []
        assert recorder.under(env.profiles_dir)  # 승인·되돌리기만 프로필을 쓴다
        assert approved["version"]["ver"] == 1
        forbidden_roots = [
            REPO / "config" / "db_registry.yaml", REPO / ".env", REPO / ".encenv",
            REPO / "mcp_server", env.tmp / "config" / "db_registry.yaml", env.tmp / ".env",
            env.tmp / "mcp_server",
        ]
        for root in forbidden_roots:
            assert recorder.under(root) == [], root
        assert all(p.is_relative_to(env.tmp.resolve()) for p in recorder.paths), recorder.paths
        assert _digest(guarded) == before


def _sql_rows_lenient(sql: str) -> list[dict[str, Any]]:
    """전 흐름 테스트용 — 코드값·서버 변수·값 인덱스 조회에 값을 준다."""
    if sql.startswith("SELECT DISTINCT status_cd "):
        return [{"status_cd": "OK"}]
    return _sql_rows("", sql)
