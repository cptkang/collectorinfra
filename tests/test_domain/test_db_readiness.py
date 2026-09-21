"""DB 활성화 준비도 C1~C10 순수 함수 테스트 (plans/104 B-1)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.domain.db_readiness import (
    ReadinessInputs,
    ReadinessReport,
    evaluate_readiness,
    is_local_sandbox_env,
)

ENV = "http://mcp.example.internal:9099/sse"


def _inputs(**overrides: Any) -> ReadinessInputs:
    """필수·권장 전부 충족하는 PostgreSQL 기본 입력."""
    base = ReadinessInputs(
        db_id="sample_db",
        mcp_available=True,
        mcp_listed=True,
        mcp_type="postgresql",
        mcp_health=True,
        registry_entry={
            "db_id": "sample_db",
            "enabled": True,
            "engine": "postgresql",
            "description": "샘플 업무 DB",
            "db_schema": "",
            "zone": "zone_a",
        },
        cache_exists=True,
        cache_table_count=12,
        snapshot_table_count=12,
        cache_env=ENV,
        structure_source="approved",
        approved_env=ENV,
        current_env=ENV,
        description_scope_tables=12,
        description_applied_tables=10,
        description_excluded_tables=2,
        seed_loaded_count=0,
        llm_synonym_applied_count=5,
        default_allowed_db_ids=(),
    )
    return replace(base, **overrides)


def _item(report: ReadinessReport, code: str) -> Any:
    return next(i for i in report.items if i.code == code)


def _registry(**overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "db_id": "sample_db",
        "enabled": True,
        "engine": "postgresql",
        "description": "샘플 업무 DB",
        "db_schema": "",
        "zone": "zone_a",
    }
    entry.update(overrides)
    return entry


class TestAllMet:
    def test_ready_counts_and_dict(self) -> None:
        report = evaluate_readiness(_inputs())
        assert [i.code for i in report.items] == [f"C{n}" for n in range(1, 11)]
        assert report.required_total == 7 and report.required_met == 7
        assert report.recommended_total == 2 and report.recommended_met == 2
        assert report.ready is True
        assert report.unmet_required() == []
        data = report.to_dict()
        assert data["summary"] == "필수 7/7 · 권장 2/2"
        assert data["ready"] is True
        assert data["items"][0]["code"] == "C1"


class TestC1Mcp:
    def test_mcp_unavailable_is_unknown_and_unmet(self) -> None:
        report = evaluate_readiness(_inputs(mcp_available=False))
        assert _item(report, "C1").ok is None
        assert report.ready is False
        assert [i.code for i in report.unmet_required()] == ["C1"]

    def test_not_listed(self) -> None:
        item = _item(evaluate_readiness(_inputs(mcp_listed=False)), "C1")
        assert item.ok is False
        assert "list_sources" in item.detail

    def test_health_failed_or_unchecked(self) -> None:
        assert _item(evaluate_readiness(_inputs(mcp_health=False)), "C1").ok is False
        assert _item(evaluate_readiness(_inputs(mcp_health=None)), "C1").ok is None


class TestC2Registry:
    def test_not_in_running_registry_needs_restart(self) -> None:
        item = _item(evaluate_readiness(_inputs(registry_entry=None)), "C2")
        assert item.ok is False
        assert "재기동" in item.detail

    def test_disabled(self) -> None:
        assert (
            _item(evaluate_readiness(_inputs(registry_entry=_registry(enabled=False))), "C2").ok
            is False
        )

    def test_engine_mismatch(self) -> None:
        item = _item(evaluate_readiness(_inputs(mcp_type="mariadb")), "C2")
        assert item.ok is False
        assert "불일치" in item.detail

    def test_engine_case_insensitive(self) -> None:
        report = evaluate_readiness(_inputs(registry_entry=_registry(engine="PostgreSQL")))
        assert _item(report, "C2").ok is True

    def test_mcp_type_unknown(self) -> None:
        assert _item(evaluate_readiness(_inputs(mcp_type=None)), "C2").ok is None


class TestC3Description:
    def test_empty_description(self) -> None:
        item = _item(evaluate_readiness(_inputs(registry_entry=_registry(description="  "))), "C3")
        assert item.ok is False

    def test_met(self) -> None:
        assert _item(evaluate_readiness(_inputs()), "C3").ok is True


class TestC4SchemaQualification:
    def test_db2_requires_db_schema(self) -> None:
        inputs = _inputs(mcp_type="db2", registry_entry=_registry(engine="db2", db_schema=""))
        item = _item(evaluate_readiness(inputs), "C4")
        assert item.ok is False
        assert item.grade == "required"

    def test_db2_with_schema_met(self) -> None:
        inputs = _inputs(
            mcp_type="db2", registry_entry=_registry(engine="db2", db_schema="APPSCHEMA")
        )
        assert _item(evaluate_readiness(inputs), "C4").ok is True

    @pytest.mark.parametrize("engine", ["postgresql", "mariadb"])
    def test_non_db2_not_applicable(self, engine: str) -> None:
        inputs = _inputs(mcp_type=engine, registry_entry=_registry(engine=engine, db_schema=""))
        item = _item(evaluate_readiness(inputs), "C4")
        assert item.ok is True
        assert "해당 없음" in item.detail

    def test_engine_from_mcp_when_unregistered(self) -> None:
        item = _item(evaluate_readiness(_inputs(registry_entry=None, mcp_type="db2")), "C4")
        assert item.ok is False


class TestC5Cache:
    def test_missing_cache(self) -> None:
        assert _item(evaluate_readiness(_inputs(cache_exists=False)), "C5").ok is False

    def test_count_mismatch(self) -> None:
        item = _item(evaluate_readiness(_inputs(snapshot_table_count=13)), "C5")
        assert item.ok is False
        assert "≠" in item.detail

    def test_no_snapshot(self) -> None:
        assert _item(evaluate_readiness(_inputs(snapshot_table_count=None)), "C5").ok is False


class TestC6Structure:
    @pytest.mark.parametrize("source", ["manual", "approved"])
    def test_met(self, source: str) -> None:
        assert _item(evaluate_readiness(_inputs(structure_source=source)), "C6").ok is True

    def test_missing(self) -> None:
        assert _item(evaluate_readiness(_inputs(structure_source=None)), "C6").ok is False


class TestC7Env:
    def test_cache_env_mismatch(self) -> None:
        item = _item(evaluate_readiness(_inputs(cache_env="http://localhost:9099/sse")), "C7")
        assert item.ok is False
        assert "로컬 샌드박스" in item.detail

    def test_cache_env_missing(self) -> None:
        assert _item(evaluate_readiness(_inputs(cache_env=None)), "C7").ok is False

    def test_approved_env_mismatch(self) -> None:
        assert (
            _item(evaluate_readiness(_inputs(approved_env="http://other:9099/sse")), "C7").ok
            is False
        )

    def test_manual_profile_excludes_approved_env(self) -> None:
        report = evaluate_readiness(_inputs(structure_source="manual", approved_env=None))
        item = _item(report, "C7")
        assert item.ok is True
        assert "대조 제외" in item.detail

    def test_trailing_slash_equal(self) -> None:
        assert _item(evaluate_readiness(_inputs(current_env=ENV + "/")), "C7").ok is True

    def test_no_structure_does_not_double_count(self) -> None:
        report = evaluate_readiness(_inputs(structure_source=None, approved_env=None))
        assert _item(report, "C7").ok is True
        assert [i.code for i in report.unmet_required()] == ["C6"]


class TestRecommended:
    def test_c8_full_or_excluded(self) -> None:
        assert _item(evaluate_readiness(_inputs()), "C8").ok is True
        item = _item(evaluate_readiness(_inputs(description_excluded_tables=0)), "C8")
        assert item.ok is False
        assert item.grade == "recommended"
        assert "미적용 2개" in item.detail

    def test_c8_scope_not_selected(self) -> None:
        assert _item(evaluate_readiness(_inputs(description_scope_tables=0)), "C8").ok is False

    def test_c9_seed_or_llm(self) -> None:
        assert (
            _item(
                evaluate_readiness(_inputs(seed_loaded_count=3, llm_synonym_applied_count=0)), "C9"
            ).ok
            is True
        )
        assert _item(evaluate_readiness(_inputs(llm_synonym_applied_count=0)), "C9").ok is False

    def test_recommended_unmet_does_not_block_ready(self) -> None:
        report = evaluate_readiness(
            _inputs(llm_synonym_applied_count=0, description_scope_tables=0)
        )
        assert report.ready is True
        assert report.recommended_met == 0


class TestC10Info:
    def test_info_grade_never_counts(self) -> None:
        item = _item(evaluate_readiness(_inputs()), "C10")
        assert item.grade == "info"
        assert item.ok is None

    def test_empty_default_allowed_explains_new_signup_effect(self) -> None:
        """빈 설정의 뜻은 "신규 가입자는 조회 가능 DB 없음"이다(plans/104 C-4 · D-232)."""
        detail = _item(evaluate_readiness(_inputs(default_allowed_db_ids=())), "C10").detail
        assert "비어 있음" in detail
        assert "신규 가입자" in detail
        assert "조회 가능 DB 없음" in detail
        assert "미소비" not in detail          # 이제 실제로 소비된다

    def test_included_and_unzoned(self) -> None:
        inputs = _inputs(default_allowed_db_ids=("sample_db",), registry_entry=_registry(zone=""))
        detail = _item(evaluate_readiness(inputs), "C10").detail
        assert "포함" in detail
        assert "존 미배정" in detail


class TestLocalSandboxEnv:
    @pytest.mark.parametrize(
        "env",
        [
            "http://localhost:9099/sse",
            "http://127.0.0.1:9099/sse",
            "http://[::1]:9099/sse",
            "http://0.0.0.0:9099",
            "LOCALHOST:9099",
        ],
    )
    def test_local(self, env: str) -> None:
        assert is_local_sandbox_env(env) is True

    @pytest.mark.parametrize(
        "env", ["http://mcp.example.internal:9099/sse", "", "http://localhost.example:1"]
    )
    def test_not_local(self, env: str) -> None:
        assert is_local_sandbox_env(env) is False
