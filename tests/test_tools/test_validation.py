"""SQL 초안 사전 검증 도구 + 추출된 검증 코어 검증 (Plan 67 Phase S1 §4.3).

노드(query_validator)의 동작 불변은 기존 validator 테스트가 담보하고, 여기서는 상태
결합 없이 코어를 직접 부를 수 있는지와 어댑터 훅 주입 구조를 확인한다.
"""

from __future__ import annotations

import src.tools.validation as validation_module
from src.nodes.query_validator import validate_sql
from src.tools.validation import validate_sql_draft


class FakeAdapter:
    """전용 검증 훅을 노출하는 어댑터 목."""

    name = "fake"

    def validator_checks(self):
        return [lambda sql: ["어댑터 전용 위반"] if "host" in sql else []]


class TestValidateSqlCore:
    def test_select_passes(self, schema_info):
        outcome = validate_sql("SELECT hostname FROM host LIMIT 10;", schema_info)
        assert outcome.passed
        assert outcome.errors == []

    def test_non_select_rejected(self, schema_info):
        outcome = validate_sql("DELETE FROM host;", schema_info)
        assert not outcome.passed
        assert any("SELECT 문만 허용" in e for e in outcome.errors)
        assert "DELETE" in outcome.forbidden_keywords

    def test_unknown_column_rejected(self, schema_info):
        outcome = validate_sql("SELECT host.없는컬럼 FROM host LIMIT 1;", schema_info)
        assert not outcome.passed

    def test_limit_auto_added(self, schema_info):
        outcome = validate_sql("SELECT hostname FROM host;", schema_info, default_limit=7)
        assert outcome.auto_fixed_sql is not None
        assert "LIMIT 7" in outcome.auto_fixed_sql

    def test_db2_uses_fetch_first(self, schema_info):
        outcome = validate_sql(
            "SELECT hostname FROM host;", schema_info, db_engine="db2", default_limit=7
        )
        assert "FETCH FIRST 7 ROWS ONLY" in outcome.auto_fixed_sql

    def test_all_query_skips_auto_limit(self, schema_info):
        outcome = validate_sql(
            "SELECT hostname FROM host;", schema_info, user_query="모든 서버 조회"
        )
        assert outcome.auto_fixed_sql is None

    def test_adapter_checks_injected(self, schema_info):
        outcome = validate_sql(
            "SELECT hostname FROM host LIMIT 1;",
            schema_info,
            adapter_checks=[lambda sql: ["주입된 위반"]],
        )
        assert outcome.errors == ["주입된 위반"]

    def test_no_adapter_checks_by_default(self, schema_info):
        """공용 코어는 주입이 없으면 DB 특화 검증을 하지 않는다(DB-agnostic)."""
        assert validate_sql("SELECT hostname FROM host LIMIT 1;", schema_info).passed


class TestValidateSqlDraftTool:
    def test_valid_draft(self, schema_info, monkeypatch):
        monkeypatch.setattr(validation_module, "get_adapter", lambda *a, **k: None)
        result = validate_sql_draft("SELECT hostname FROM host LIMIT 5;", schema_info)
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["fixed_sql"] == "SELECT hostname FROM host LIMIT 5;"

    def test_invalid_draft_reports_errors(self, schema_info, monkeypatch):
        monkeypatch.setattr(validation_module, "get_adapter", lambda *a, **k: None)
        result = validate_sql_draft("DROP TABLE host;", schema_info)
        assert result["valid"] is False
        assert result["errors"]

    def test_fixed_sql_carries_auto_limit(self, schema_info, monkeypatch):
        monkeypatch.setattr(validation_module, "get_adapter", lambda *a, **k: None)
        result = validate_sql_draft(
            "SELECT hostname FROM host;", schema_info, default_limit=3
        )
        assert "LIMIT 3" in result["fixed_sql"]

    def test_adapter_hook_runs_via_registry(self, schema_info, monkeypatch):
        monkeypatch.setattr(validation_module, "get_adapter", lambda *a, **k: FakeAdapter())
        result = validate_sql_draft(
            "SELECT hostname FROM host LIMIT 1;", schema_info,
            db_id="x", adapter_db_ids={"x"},
        )
        assert result["errors"] == ["어댑터 전용 위반"]
