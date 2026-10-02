"""plans/104 A-2~A-6 — `DBStructureService`(목록 · 변경 점검 · 신규 코드값 · 분석 · 승인 가드).

MCP는 실제 `DBHubClient`에 붙인 가짜 세션, LLM은 목, Redis는 인메모리 페이크, 저장소는 tmp 경로의
실제 `StructureStore`다. 가드 테스트는 저장소 적용 함수를 스파이로 바꿔 "호출되지 않음"을 단언한다.
네트워크·실 LLM·실 MCP 0 · 가상 이름만 쓴다.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
import yaml

from src.schema_cache import db_structure_service as svc_module
from src.schema_cache.db_structure_service import (
    CODE_VALUE_LIMIT,
    LOCAL_SANDBOX_HEADER,
    DBStructureService,
    DraftNotApprovable,
    admin_code_targets,
    build_code_value_sql,
    schema_dict_from_snapshot,
)
from src.security.sql_guard import SQLGuard
from tests.test_schema_cache.test_plan104_service_fixtures import (
    LOCAL_ENV,
    REMOTE_ENV,
    Env,
    FakeTable,
    MockLLM,
    failing_client_factory,
    make_env,
    make_registry,
)

SRC = "app_alpha"


def _tables(*, renamed: bool = False, type_changed: bool = False) -> dict[str, FakeTable]:
    """가상 3테이블 — 계층(t_node) · 엔티티(t_asset) · EAV 속성(attr_store)."""
    return {
        "t_node": FakeTable([
            ("id", "integer", False, True),
            ("parent_id", "integer", True, False),
            ("node_type", "integer" if type_changed else "character varying", True, False),
            ("name", "character varying", True, False),
        ]),
        "t_asset": FakeTable(
            [
                ("id", "integer", False, True),
                ("node_id", "integer", True, False),
                ("state_cd" if renamed else "status_cd", "character varying", True, False),
            ],
            fks=[("node_id", "t_node", "id")],
        ),
        "attr_store": FakeTable(
            [
                ("entity_id", "integer", False, False),
                ("attr_name", "character varying", False, False),
                ("attr_value", "character varying", True, False),
            ],
            fks=[("entity_id", "t_asset", "id")],
        ),
    }


MANUAL_PROFILE: dict[str, Any] = {
    "source": "manual",
    "patterns": [
        {
            "type": "eav",
            "entity_table": "t_asset",
            "config_table": "attr_store",
            "attribute_column": "attr_name",
            "value_column": "attr_value",
            "join_condition": "t_asset.id = attr_store.entity_id",
            "known_attributes": [{"name": "Alpha"}, "Beta"],
        },
        {
            "type": "hierarchy",
            "table": "t_node",
            "id_column": "id",
            "parent_column": "parent_id",
            "type_column": "node_type",
            "name_column": "name",
        },
    ],
    "query_guide": "수동 안내",
    "allowed_tables": ["t_asset", "t_node"],
}


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    e = make_env(tmp_path, monkeypatch)
    e.session.add_source(SRC, "postgresql", _tables())
    e.registry = make_registry({
        "db_id": SRC, "engine": "postgresql", "description": "가상 업무 DB", "zone": "",
    })
    return e


def _service(env: Env) -> DBStructureService:
    return DBStructureService(env.config, env.mgr, **env.service_kwargs())


# ──────────────────────────────────────────────
# A-2 목록
# ──────────────────────────────────────────────


class TestListSources:
    async def test_mcp_only_app_only_and_active_without_structure(self, tmp_path, monkeypatch):
        """MCP에만 있음 · 앱에만 있음 · 활성인데 구조 없음 3종을 표시한다."""
        env = make_env(tmp_path, monkeypatch, active=("beta_src", "delta_src"))
        env.session.add_source("alpha_src", "mariadb", _tables())
        env.session.add_source("beta_src", "postgresql", _tables())
        env.session.add_source("delta_src", "postgresql", _tables(), healthy=False)
        env.registry = make_registry(
            {"db_id": "beta_src", "engine": "postgresql", "description": "베타"},
            {"db_id": "gamma_src", "engine": "db2", "description": "감마"},
            {"db_id": "delta_src", "engine": "db2", "description": ""},
        )
        env.write_profile("beta_src", MANUAL_PROFILE)

        result = await _service(env).list_sources()

        assert result["mcp_available"] is True
        assert result["env"] == REMOTE_ENV and result["local_sandbox"] is False
        rows = {r["source"]: r for r in result["sources"]}
        assert list(rows) == ["alpha_src", "beta_src", "delta_src", "gamma_src"]

        assert rows["alpha_src"]["mismatch"] == "mcp_only"
        assert rows["alpha_src"]["registered"] is False
        assert rows["gamma_src"]["mismatch"] == "app_only"
        assert rows["gamma_src"]["in_mcp"] is False and rows["gamma_src"]["health"] is None
        assert rows["delta_src"]["structure"]["warning"] == "active_without_structure"
        assert rows["delta_src"]["engine_mismatch"] is True  # MCP postgresql ≠ 레지스트리 db2
        assert rows["delta_src"]["health"]["healthy"] is False

        beta = rows["beta_src"]
        assert beta["mismatch"] is None and beta["active"] is True
        assert beta["structure"]["status"] == "manual" and beta["structure"]["warning"] is None
        assert beta["health"]["healthy"] is True
        assert beta["readiness"]["summary"].startswith("필수 ")
        assert "items" not in beta["readiness"]
        # 헬스 체크는 MCP 목록 소스마다 1회
        assert len(env.session.tool_calls("health_check")) == 3

    async def test_mcp_unavailable_marks_unknown(self, env):
        """MCP 미가용이면 연결·불일치는 "확인 못 함"(None)이고 C1도 확인 못 함이다."""
        env.session = None  # type: ignore[assignment]
        service = DBStructureService(
            env.config, env.mgr,
            client_factory=failing_client_factory(ConnectionError("MCP 서버 응답 없음")),
            llm_factory=lambda: env.llm, registry_getter=lambda: env.registry, store=env.store,
        )

        result = await service.list_sources()

        assert result["mcp_available"] is False
        assert "MCP 서버 응답 없음" in result["mcp_error"]
        row = result["sources"][0]
        assert row["source"] == SRC
        assert row["mismatch"] is None and row["health"] is None and row["in_mcp"] is False
        unmet = {i["code"] for i in row["readiness"]["unmet_required"]}
        assert "C1" in unmet

        report = await service._readiness_for(SRC, await service._mcp_view())
        c1 = next(i for i in report.items if i.code == "C1")
        assert c1.ok is None and "확인하지 못했습니다" in c1.detail


# ──────────────────────────────────────────────
# A-3·A-4 변경 점검
# ──────────────────────────────────────────────


def _code_rows(values: dict[str, list[str]]):
    """`SELECT DISTINCT <col> ...` 에 컬럼별 값을 돌려주는 가짜 실행기."""

    def _rows(source: str, sql: str) -> list[dict[str, Any]]:
        for column, column_values in values.items():
            if sql.startswith(f"SELECT DISTINCT {column} "):
                return [{column: v} for v in column_values]
        return []

    return _rows


class TestRunCheck:
    async def test_detects_same_count_rename_and_type_change(self, env):
        """개수가 같은 컬럼 이름 교체와 타입 변경을 감지한다(LLM 0)."""
        service = _service(env)
        first = await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)
        assert first["diff"]["baseline"] is True and first["diff"]["has_changes"] is False

        env.session.tables[SRC] = _tables(renamed=True, type_changed=True)
        second = await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)

        diff = second["diff"]
        assert diff["baseline"] is False
        assert diff["columns_added"] == [
            {"table": "t_asset", "column": "state_cd", "type": "character varying"}
        ]
        assert diff["columns_removed"] == [
            {"table": "t_asset", "column": "status_cd", "type": "character varying"}
        ]
        assert diff["type_changed"] == [{
            "table": "t_node", "column": "node_type", "old": "character varying", "new": "integer",
        }]
        assert second["previous_snapshot_hash"] == first["snapshot_hash"]
        assert env.llm.calls == []
        # 스키마 캐시는 건드리지 않는다
        assert await env.mgr.get_schema(SRC) is None

    async def test_structure_impact_badge(self, env):
        """구조 정보가 참조하는 컬럼이 바뀌면 재분석 필요 배지가 목록에 뜬다."""
        env.write_profile(SRC, MANUAL_PROFILE)
        service = _service(env)
        await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)
        env.session.tables[SRC] = _tables(type_changed=True)

        result = await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)

        assert result["reanalysis_required"] is True
        assert {"table": "t_node", "column": "node_type", "path": "patterns[1].type_column",
                "change": "type_changed"} in result["impact"]["hits"]
        listed = await service.list_sources()
        row = next(r for r in listed["sources"] if r["source"] == SRC)
        assert row["last_check"]["reanalysis_required"] is True
        assert row["last_check"]["summary"]["type_changed"] == 1

    async def test_eav_new_attribute_values(self, env):
        """EAV 속성명 신규 값을 known_attributes·이전 점검 값과 대조한다(조립 SQL은 가드 통과)."""
        env.write_profile(SRC, MANUAL_PROFILE)
        env.session.sql_rows = _code_rows(
            {"attr_name": ["Alpha", "Beta", "Gamma"], "node_type": ["rack"]}
        )
        service = _service(env)

        first = await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)

        items = {i["origin"]: i for i in first["code_values"]}
        eav = items["eav_attribute"]
        assert eav["new_values"] == ["Gamma"] and eav["baseline"] is False
        assert eav["sql"] == (
            "SELECT DISTINCT attr_name FROM attr_store WHERE attr_name IS NOT NULL "
            f"LIMIT {CODE_VALUE_LIMIT}"
        )
        assert items["hierarchy_type"]["baseline"] is True
        assert items["hierarchy_type"]["new_values"] == []
        assert first["new_code_value_count"] == 1
        for sql in env.session.executed_sql():
            assert SQLGuard().is_safe_select(sql)[0], sql

        env.session.sql_rows = _code_rows(
            {"attr_name": ["Alpha", "Beta", "Gamma", "Delta"], "node_type": ["rack", "room"]}
        )
        second = await service.run_check(SRC, by="admin", code_columns=None, ctx=env.ctx)
        items = {i["origin"]: i for i in second["code_values"]}
        assert items["eav_attribute"]["new_values"] == ["Delta"]
        assert items["hierarchy_type"]["new_values"] == ["room"]
        listed = await service.list_sources()
        row = next(r for r in listed["sources"] if r["source"] == SRC)
        assert row["last_check"]["new_code_values"] == 2

    async def test_admin_code_columns_db2_schema_qualified(self, tmp_path, monkeypatch):
        """G-10 (a) 관리자 지정 컬럼 — DB2는 레지스트리 db_schema로 한정하고 FETCH FIRST를 쓴다."""
        env = make_env(tmp_path, monkeypatch)
        tables = _tables()
        for table in tables.values():
            table.schema = "APPSCHEMA"
        env.session.add_source("db2_src", "db2", tables)
        env.session.sql_rows = _code_rows({"status_cd": ["A", "B"]})
        env.registry = make_registry(
            {"db_id": "db2_src", "engine": "db2", "db_schema": "APPSCHEMA", "description": "d"}
        )

        result = await _service(env).run_check(
            "db2_src", by="admin", code_columns=["t_asset.status_cd"], ctx=env.ctx
        )

        item = result["code_values"][0]
        assert item["origin"] == "admin" and item["values"] == ["A", "B"]
        assert item["sql"] == (
            "SELECT DISTINCT status_cd FROM APPSCHEMA.t_asset WHERE status_cd IS NOT NULL "
            f"FETCH FIRST {CODE_VALUE_LIMIT} ROWS ONLY"
        )
        assert env.session.executed_sql() == [item["sql"]]

    async def test_invalid_code_columns_never_execute(self, env):
        """형식 오류·스냅샷에 없는 식별자는 사유만 남기고 SQL을 실행하지 않는다."""
        result = await _service(env).run_check(
            SRC, by="admin",
            code_columns=["no_dot", "t_missing.col", "t_asset.missing_col", "t_asset.x;DROP"],
            ctx=env.ctx,
        )

        errors = [i["error"] for i in result["code_values"]]
        assert all(errors), errors
        assert "형식 오류" in errors[0]
        assert "스냅샷에 없습니다" in errors[1] and "스냅샷에 없습니다" in errors[2]
        assert env.session.executed_sql() == []


class TestCodeValueSql:
    def test_rejects_non_identifier(self):
        with pytest.raises(ValueError, match="식별자"):
            build_code_value_sql("t_asset", "a b", engine="postgresql", db_schema=None,
                                 table_schema=None, limit=10)
        with pytest.raises(ValueError, match="식별자"):
            build_code_value_sql("t_asset--", "id", engine="postgresql", db_schema=None,
                                 table_schema=None, limit=10)

    def test_guard_failure_blocks_sql(self):
        """조립 결과가 `SQLGuard.is_safe_select`를 통과하지 못하면 ValueError(실행 전 차단)."""
        with pytest.raises(ValueError, match="안전성 검사"):
            build_code_value_sql("sys.t_asset", "id", engine="postgresql", db_schema=None,
                                 table_schema=None, limit=10)

    def test_admin_targets_keep_malformed_items(self):
        targets = admin_code_targets(["a.b", "", "schema_x.t.c"])
        assert targets[0]["table"] == "a" and targets[0]["column"] == "b"
        assert "형식 오류" in targets[1]["error"]
        assert targets[2]["table"] == "schema_x.t" and targets[2]["column"] == "c"


# ──────────────────────────────────────────────
# A-5 구조 분석
# ──────────────────────────────────────────────


def _structure_llm() -> MockLLM:
    """묶음에 들어온 테이블에 맞는 패턴을 돌려주는 목 LLM."""

    def structure(prompt: str) -> str:
        patterns = []
        if "### attr_store" in prompt:
            patterns.append({
                "type": "eav", "entity_table": "t_asset", "config_table": "attr_store",
                "attribute_column": "attr_name", "value_column": "attr_value",
                "join_condition": "t_asset.id = attr_store.entity_id",
            })
        if "### t_node" in prompt:
            patterns.append({
                "type": "hierarchy", "table": "t_node", "id_column": "id",
                "parent_column": "parent_id", "type_column": "node_type", "name_column": "name",
            })
        return json.dumps({"patterns": patterns, "query_guide": "초안 안내"}, ensure_ascii=False)

    def samples(prompt: str) -> str:
        return json.dumps([{"purpose": "노드 샘플", "sql": "SELECT id FROM t_node LIMIT 5"}])

    return MockLLM(structure=structure, samples=samples)


class TestRunAnalyze:
    async def test_groups_calls_validation_and_merge(self, tmp_path, monkeypatch):
        """묶음 수만큼 분석 호출 + 샘플 1회 · 검증 통과 · 수동 필드 보존 병합 초안."""
        env = make_env(tmp_path, monkeypatch, group_max_tables=2)
        env.session.add_source(SRC, "postgresql", _tables())
        env.session.sql_rows = lambda source, sql: [{"id": 1}]
        env.llm = _structure_llm()
        env.write_profile(SRC, {**MANUAL_PROFILE, "patterns": []}, header="# 사람 주석\n")

        result = await _service(env).run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )

        assert result["groups"] == 2  # attr_store·t_asset | t_node (FK 성분 3 > 상한 2)
        assert len(env.llm.kinds("structure")) == 2 and len(env.llm.kinds("samples")) == 1
        assert result["llm_calls"] == {"structure": 2, "samples": 1}
        assert result["analysis_status"] == "ok" and result["validation_passed"] is True
        assert result["provider"] == {"provider": "mlx", "model": "fake-local-model"}

        draft = await env.store.get_draft(SRC, result["draft_id"])
        assert draft["validation"]["passed"] is True
        assert draft["env"] == REMOTE_ENV and draft["created_by"] == "admin"
        assert draft["comment_lines_in_current"] == 1
        merged = draft["merged_profile"]
        assert merged["source"] == "manual" and "environment" not in merged
        assert merged["query_guide"] == "수동 안내"  # 수동 값 보존
        assert merged["allowed_tables"] == ["t_asset", "t_node"]
        assert [p["type"] for p in merged["patterns"]] == ["eav", "hierarchy"]
        assert "samples" not in merged
        assert any(d["path"].startswith("patterns[") for d in draft["field_diff"])
        # 분석은 스냅샷을 먼저 만든다(없었으므로 수집 1회)
        assert len(env.session.tool_calls("search_objects")) == 1

    async def test_config_read_at_job_start(self, tmp_path, monkeypatch):
        """묶음 상한은 서비스 생성 시점이 아니라 잡 시작 시점 설정을 쓴다(리로드 반영)."""
        env = make_env(tmp_path, monkeypatch, group_max_tables=40)
        env.session.add_source(SRC, "postgresql", _tables())
        service = _service(env)
        env.config.schema_cache.structure_group_max_tables = 1

        result = await service.run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )

        assert result["groups"] == 3

    async def test_no_patterns_draft_is_approvable_without_samples(self, env):
        env.llm = MockLLM()  # 패턴 0건
        result = await _service(env).run_analyze(
            SRC, scope="tables", tables=["T_ASSET"], code_columns=None, by="admin", ctx=env.ctx
        )

        assert result["analysis_status"] == "no_patterns"
        assert result["tables"] == ["t_asset"]  # 대소문자 무시 해석
        assert env.llm.kinds("samples") == []
        draft = await env.store.get_draft(SRC, result["draft_id"])
        assert draft["validation"]["passed"] is True

    async def test_all_groups_failed_creates_no_draft(self, env):
        env.llm = MockLLM(structure=lambda prompt: "JSON 아님")
        result = await _service(env).run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )

        assert result["analysis_status"] == "failed" and result["draft_id"] is None
        assert result["errors"]
        assert await env.store.list_drafts(SRC) == []

    async def test_admin_code_columns_go_to_code_values(self, env):
        env.llm = MockLLM()
        env.session.sql_rows = _code_rows({"status_cd": ["OK", "NG"]})
        result = await _service(env).run_analyze(
            SRC, scope="all", tables=None, code_columns=["t_asset.status_cd"], by="admin",
            ctx=env.ctx,
        )

        draft = await env.store.get_draft(SRC, result["draft_id"])
        assert draft["meta"]["code_values"] == {"t_asset.status_cd": ["OK", "NG"]}
        assert draft["merged_profile"]["code_values"] == {"t_asset.status_cd": ["OK", "NG"]}

    async def test_scope_errors(self, env):
        service = _service(env)
        with pytest.raises(ValueError, match="변경 점검 결과가 없습니다"):
            await service.run_analyze(SRC, scope="changed", tables=None, code_columns=None,
                                      by="admin", ctx=env.ctx)
        with pytest.raises(ValueError, match="스냅샷에 없는 테이블"):
            await service.run_analyze(SRC, scope="tables", tables=["t_nope"], code_columns=None,
                                      by="admin", ctx=env.ctx)
        with pytest.raises(ValueError, match="scope"):
            await service.run_analyze(SRC, scope="everything", tables=None, code_columns=None,
                                      by="admin", ctx=env.ctx)

    def test_schema_dict_from_snapshot_keeps_in_scope_relationships(self):
        snapshot = {
            "tables": {
                "a": {"columns": {"id": {"type": "integer"}, "b_id": {"type": "integer"}},
                      "foreign_keys": ["b_id->b.id", "c_id->c.id"]},
                "b": {"columns": {"id": {"type": "integer"}}, "foreign_keys": []},
            }
        }
        result = schema_dict_from_snapshot(snapshot, ["a", "b"])
        assert result["relationships"] == [{"from": "a.b_id", "to": "b.id"}]
        assert [c["name"] for c in result["tables"]["a"]["columns"]] == ["id", "b_id"]


# ──────────────────────────────────────────────
# A-6 승인 가드 · 재계산
# ──────────────────────────────────────────────


async def _add_draft(target: Env, **overrides: Any) -> str:
    draft = {
        "meta": {"patterns": [], "query_guide": "초안 안내"},
        "validation": {"passed": True, "checks": [], "analysis_errors": []},
        "env": REMOTE_ENV,
        "field_diff": [],
        **overrides,
    }
    return str((await target.store.add_draft(SRC, draft))["draft_id"])


class TestApproveGuards:
    @pytest.fixture
    def spied(self, env):
        env.store.apply_profile = AsyncMock(return_value={"ver": 1})  # type: ignore[method-assign]
        return env

    async def test_validation_failed_draft_cannot_be_approved(self, spied):
        draft_id = await _add_draft(spied, validation={"passed": False, "checks": []})
        with pytest.raises(DraftNotApprovable) as exc:
            await _service(spied).approve_draft(SRC, draft_id, by="admin", reason="확인")
        assert exc.value.code == "validation_failed"
        assert isinstance(exc.value, ValueError)
        spied.store.apply_profile.assert_not_awaited()

    async def test_other_env_draft_cannot_be_approved(self, spied):
        draft_id = await _add_draft(spied, env=LOCAL_ENV)
        with pytest.raises(DraftNotApprovable) as exc:
            await _service(spied).approve_draft(SRC, draft_id, by="admin", reason="확인")
        assert exc.value.code == "env_mismatch"
        spied.store.apply_profile.assert_not_awaited()

    async def test_missing_and_not_pending(self, spied):
        service = _service(spied)
        with pytest.raises(DraftNotApprovable) as exc:
            await service.approve_draft(SRC, "nope", by="admin", reason="확인")
        assert exc.value.code == "not_found"

        draft_id = await _add_draft(spied)
        rejected = await service.reject_draft(SRC, draft_id, by="admin", reason="틀림")
        assert rejected["status"] == "rejected"
        with pytest.raises(DraftNotApprovable) as exc:
            await service.approve_draft(SRC, draft_id, by="admin", reason="확인")
        assert exc.value.code == "not_pending"
        spied.store.apply_profile.assert_not_awaited()

    async def test_approval_recomputes_merge_against_current_file(self, env, monkeypatch):
        """초안 생성 뒤 현행 파일이 바뀌면 승인 시점 파일로 병합을 다시 계산한다."""
        env.write_profile(SRC, {"source": "manual", "patterns": [], "query_guide": ""})
        env.llm = MockLLM(
            structure=lambda p: json.dumps({"patterns": [], "query_guide": "LLM 안내"})
        )
        service = _service(env)
        created = await service.run_analyze(
            SRC, scope="all", tables=None, code_columns=None, by="admin", ctx=env.ctx
        )
        env.write_profile(SRC, {
            "source": "manual", "patterns": [], "query_guide": "사람 안내",
            "allowed_tables": ["t_node"],
        })
        bases: list[Any] = []
        real_merge = svc_module.merge_profile

        def spy_merge(base, draft_meta, *, local_sandbox):
            bases.append(base)
            return real_merge(base, draft_meta, local_sandbox=local_sandbox)

        monkeypatch.setattr(svc_module, "merge_profile", spy_merge)

        result = await service.approve_draft(SRC, created["draft_id"], by="admin", reason="확인")

        assert bases[-1]["allowed_tables"] == ["t_node"]
        assert result["recomputed"] is True
        written = yaml.safe_load((env.profiles_dir / f"{SRC}.yaml").read_text(encoding="utf-8"))
        assert written["allowed_tables"] == ["t_node"]  # 초안 뒤에 생긴 수동 키 보존
        assert written["query_guide"] == "사람 안내"  # 초안 때는 비어 있었으나 지금은 수동 값
        draft = await env.store.get_draft(SRC, created["draft_id"])
        assert draft["status"] == "approved" and draft["approved_ver"] == result["version"]["ver"]
        assert "content" not in result["version"]

    async def test_rollback_unknown_version(self, env):
        with pytest.raises(DraftNotApprovable) as exc:
            await _service(env).rollback(SRC, 7, by="admin", reason="되돌림")
        assert exc.value.code == "version_not_found"


class TestDiffReport:
    async def test_local_sandbox_header_and_comment_warning(self, tmp_path, monkeypatch):
        env = make_env(tmp_path, monkeypatch, env=LOCAL_ENV)
        env.session.add_source(SRC, "postgresql", _tables())
        env.write_profile(SRC, MANUAL_PROFILE, header="# 주석 1\n# 주석 2\n")
        draft_id = await _add_draft(env, env=LOCAL_ENV)

        report = await _service(env).diff_report(SRC, draft_id)

        lines = report.splitlines()
        assert lines[0] == LOCAL_SANDBOX_HEADER
        assert any("주석 2줄" in line for line in lines)
        body = yaml.safe_load(report)
        assert body["draft_id"] == draft_id and body["local_sandbox"] is True
        assert body["comment_lines_dropped"] == 2
        assert body["base_changed_since_draft"] is True  # 초안에 base_sha256 없음
        assert isinstance(body["field_diff"], list)

    async def test_remote_env_has_no_sandbox_header(self, env):
        draft_id = await _add_draft(env)
        report = await _service(env).diff_report(SRC, draft_id)
        assert LOCAL_SANDBOX_HEADER not in report
        with pytest.raises(DraftNotApprovable):
            await _service(env).diff_report(SRC, "nope")
