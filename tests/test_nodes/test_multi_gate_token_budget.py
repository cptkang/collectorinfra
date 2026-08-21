"""멀티 경로 관련 테이블 게이트·토큰 예산·백엔드 예외 감지 테스트 (D-159).

공동존(cm_gp+cm_yd) 폐쇄망 실측(2026-08-21): 멀티 경로가 미스코프 캐시 스키마 전량
× W-6 재료를 프롬프트에 실어 FabriX 한도(95,232tok)를 초과(136,707tok)했고, 백엔드
예외 텍스트가 응답 content로 유입돼 "SELECT 문이 아닙니다"로 오표면화됐다.

- FIX-A: `_gate_schema_tables` — 프로필 allowed_tables + 질의 매칭 유사어만 유지
- FIX-B: `_build_multi_system_prompt` 토큰 예산 절단 계단(재료→샘플→명시 실패)
- FIX-C: `_validate_sql_simple` 백엔드 예외 마커 감지 + 재시도 중단

LLM·DB 호출은 전부 결정적 목이다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.nodes import multi_db_executor as mdb
from src.nodes.prompt_blocks import (
    PromptBudgetExceeded,
    estimate_prompt_tokens,
    resolve_prompt_token_budget,
)


def _config(*, gate=True, budget=0, full_validation=False):
    """검증 대상 필드만 명시한 설정 대역(.env 누수 차단)."""
    return SimpleNamespace(
        query=SimpleNamespace(default_limit=100),
        text2sql=SimpleNamespace(
            multi_relevant_gate=gate,
            prompt_token_budget=budget,
            multi_full_validation=full_validation,
            path_parity=False,
        ),
        synonym=SimpleNamespace(
            max_synonym_supplement_tables=15,
            fuzzy_match=False,
            match_confidence_min=0.85,
            semantic_match=False,
            semantic_confidence_min=0.65,
        ),
    )


def _schema(tables: dict | None = None) -> dict:
    if tables is None:
        tables = {
            "polestar.cmm_resource": {"columns": [{"name": "name", "type": "text"}]},
            "polestar.core_config_prop": {"columns": []},
            "polestar.cmm_metric_stat_m": {"columns": []},
            "polestar.acc_user": {"columns": []},
            "polestar.acc_role": {"columns": []},
        }
    return {"tables": tables}


_PROFILE = {"allowed_tables": ["cmm_resource", "core_config_prop"]}


class TestGateSchemaTables:
    """FIX-A: 프로필 화이트리스트 + 질의 매칭 유사어 게이트 (b0 재발 경로 차단)."""

    def test_filters_to_allowed_tables(self):
        schema = _schema()
        out = mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "서버 목록"}, "",
            db_id="gp", app_config=_config(),
        )
        assert set(out["tables"]) == {"polestar.cmm_resource", "polestar.core_config_prop"}

    def test_original_schema_dict_not_mutated(self):
        """캐시 공유 객체 보호 — 게이트는 얕은 사본을 반환한다."""
        schema = _schema()
        before = set(schema["tables"])
        mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "서버 목록"}, "",
            db_id="gp", app_config=_config(),
        )
        assert set(schema["tables"]) == before

    def test_query_matched_synonym_table_supplemented(self):
        """질의에 등장한 유사어(vcore)의 테이블은 화이트리스트 밖이어도 유지."""
        syns = {"cmm_metric_stat_m.value": ["vcore", "코어수"]}
        out = mdb._gate_schema_tables(
            _schema(), _PROFILE, syns,
            {"original_query": "공동존 서버들의 vcore 수를 확인해줘"}, "",
            db_id="gp", app_config=_config(),
        )
        assert "polestar.cmm_metric_stat_m" in out["tables"]

    def test_unmatched_synonym_table_excluded(self):
        """질의에 없는 유사어 테이블은 유입되지 않는다 — 전량 추가가 b0 폭증 원인."""
        syns = {
            "acc_user.name": ["담당자"],
            "acc_role.name": ["권한"],
        }
        out = mdb._gate_schema_tables(
            _schema(), _PROFILE, syns, {"original_query": "서버 목록 조회"}, "",
            db_id="gp", app_config=_config(),
        )
        assert "polestar.acc_user" not in out["tables"]
        assert "polestar.acc_role" not in out["tables"]

    def test_empty_filter_result_keeps_all(self):
        """필터 결과 공집합이면 전량 유지(질의를 죽이지 않음) — 방호 로그."""
        schema = _schema({"polestar.other_table": {"columns": []}})
        out = mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "서버"}, "",
            db_id="gp", app_config=_config(),
        )
        assert out is schema

    def test_no_profile_keeps_all(self):
        schema = _schema()
        assert mdb._gate_schema_tables(
            schema, None, {}, {"original_query": "서버"}, "",
            db_id="gp", app_config=_config(),
        ) is schema

    def test_flag_off_keeps_all(self):
        schema = _schema()
        assert mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "서버"}, "",
            db_id="gp", app_config=_config(gate=False),
        ) is schema

    def test_mock_config_defaults_to_off(self):
        """MagicMock 설정 대역의 truthy 속성으로 게이트가 오발동하지 않는다(`is True`)."""
        schema = _schema()
        assert mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "서버"}, "",
            db_id="gp", app_config=MagicMock(),
        ) is schema

    def test_alarm_intent_skips_gate(self):
        schema = _schema()
        assert mdb._gate_schema_tables(
            schema, _PROFILE, {}, {"original_query": "알람"}, "",
            db_id="gp", app_config=_config(), routing_intent="alarm_query",
        ) is schema

    def test_sub_query_context_used_for_matching(self):
        """원질의에 없어도 라우터 sub_query_context의 용어로 유사어 매칭된다."""
        syns = {"cmm_metric_stat_m.value": ["vcore"]}
        out = mdb._gate_schema_tables(
            _schema(), _PROFILE, syns, {"original_query": "확인해줘"},
            "김포 폴스타에서 vcore 수 조회",
            db_id="gp", app_config=_config(),
        )
        assert "polestar.cmm_metric_stat_m" in out["tables"]


class TestTokenEstimate:
    """FIX-B 프리미티브: 보수 토큰 추정기·예산 해석."""

    def test_ascii_quarter(self):
        assert estimate_prompt_tokens("a" * 400) == 100

    def test_korean_weighted_heavier(self):
        assert estimate_prompt_tokens("가" * 300) == 200

    def test_empty_zero(self):
        assert estimate_prompt_tokens("") == 0

    def test_budget_from_config(self):
        assert resolve_prompt_token_budget(_config(budget=90_000)) == 90_000

    def test_budget_absent_or_invalid_disables(self):
        assert resolve_prompt_token_budget(None) == 0
        assert resolve_prompt_token_budget(SimpleNamespace(text2sql=SimpleNamespace())) == 0
        assert resolve_prompt_token_budget(MagicMock()) == 0
        assert resolve_prompt_token_budget(_config(budget=0)) == 0
        assert resolve_prompt_token_budget(_config(budget=-1)) == 0
        assert resolve_prompt_token_budget(_config(budget=True)) == 0


class TestMultiPromptBudget:
    """FIX-B: 멀티 시스템 프롬프트 절단 계단 — 재료 → 샘플 → 명시 실패."""

    def _patch(self, monkeypatch):
        """렌더 크기를 결정적으로 만든다: 본문 100tok, 재료 +1000tok, 샘플 +1000tok."""

        def fake_format(schema, materials=None):
            text = "S" * 400
            if materials:
                text += "M" * 4000
            if any(
                (data or {}).get("sample_data")
                for data in (schema.get("tables") or {}).values()
            ):
                text += "D" * 4000
            return text

        async def fake_materials(db_id, app_config):
            return {"column_synonyms": {"t.c": ["단어"]}}

        async def fake_guide(*args, **kwargs):
            return ""

        monkeypatch.setattr(mdb, "QUERY_GENERATOR_SYSTEM_TEMPLATE", "{schema}")
        monkeypatch.setattr(mdb, "_format_schema", fake_format)
        monkeypatch.setattr(mdb, "_load_schema_prompt_materials", fake_materials)
        monkeypatch.setattr(mdb, "_build_multi_structure_guide", fake_guide)
        monkeypatch.setattr(mdb, "_build_multi_engine_hint", lambda *a, **k: "")

    def _schema_with_samples(self):
        return {"tables": {
            "polestar.cmm_resource": {
                "columns": [{"name": "name", "type": "text"}],
                "sample_data": [{"name": "srv-01"}],
            },
        }}

    async def _build(self, budget, schema=None):
        return await mdb._build_multi_system_prompt(
            schema if schema is not None else self._schema_with_samples(),
            {"original_query": "q"}, "", 100, "db2", "gp", _config(budget=budget),
        )

    async def test_within_budget_unchanged(self, monkeypatch):
        self._patch(monkeypatch)
        prompt = await self._build(budget=5000)
        assert "M" in prompt and "D" in prompt, "예산 내면 재료·샘플 온전(바이트 무변경)"

    async def test_budget_disabled_unchanged(self, monkeypatch):
        self._patch(monkeypatch)
        prompt = await self._build(budget=0)
        assert "M" in prompt and "D" in prompt

    async def test_stage1_drops_materials(self, monkeypatch):
        self._patch(monkeypatch)
        prompt = await self._build(budget=1500)
        assert "M" not in prompt and "D" in prompt, "1단: 재료만 제거"

    async def test_stage2_drops_samples(self, monkeypatch):
        self._patch(monkeypatch)
        schema = self._schema_with_samples()
        prompt = await self._build(budget=500, schema=schema)
        assert "M" not in prompt and "D" not in prompt, "2단: 샘플까지 제거"
        assert schema["tables"]["polestar.cmm_resource"]["sample_data"], (
            "원본 스키마(캐시 공유 객체)의 샘플은 보존"
        )

    async def test_final_overflow_raises(self, monkeypatch):
        self._patch(monkeypatch)
        with pytest.raises(PromptBudgetExceeded) as exc:
            await self._build(budget=50)
        assert "프롬프트 토큰 예산 초과" in str(exc.value)


class TestBackendErrorDetection:
    """FIX-C: 백엔드 예외 텍스트가 SQL 검증으로 흘러온 변형의 정확한 원인 노출."""

    _TOKEN_LIMIT_TEXT = (
        "An exception occurred in GptOssAdapter.llm_call: "
        "Input tokens must be <= 95232. Given: 136707"
    )

    def test_token_limit_text_detected(self):
        err = mdb._validate_sql_simple(self._TOKEN_LIMIT_TEXT, {"tables": {}})
        assert err is not None and mdb._TOKEN_LIMIT_ERROR_PREFIX in err
        assert "SELECT 문이 아닙니다" not in err, "증상이 아니라 원인을 노출한다"

    def test_orchestrator_error_text_detected(self):
        err = mdb._validate_sql_simple(
            "Error occurred from orchestrator. reason: exception 발생",
            {"tables": {}},
        )
        assert err is not None and "LLM 백엔드 예외 응답" in err

    def test_normal_select_unaffected(self):
        assert mdb._validate_sql_simple("SELECT hostname FROM servers", {"tables": {}}) is None

    def test_prose_still_reported_as_non_select(self):
        err = mdb._validate_sql_simple("The requested data is unavailable.", {"tables": {}})
        assert err == "SELECT 문이 아닙니다."


class TestTokenLimitStopsRetry:
    """FIX-C: 토큰 한도 초과는 동일 프롬프트 재생성이 무의미 — 재시도 즉시 중단."""

    async def test_no_regeneration_on_token_limit(self, monkeypatch):
        calls = []

        async def fake_generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            calls.append(kwargs.get("error_context"))
            return TestBackendErrorDetection._TOKEN_LIMIT_TEXT

        monkeypatch.setattr(mdb, "_generate_sql", fake_generate)
        run = SimpleNamespace(
            llm=AsyncMock(), parsed_requirements={}, effective_limit=100,
            unmapped_fields=None, app_config=_config(), mc_candidates=[],
            mc_derivations=[], prior_block=None, prior_scope=None, value_index=None,
            form_context="", form_fill_out=None, form_intent=False,
            mapping_sources=None, form_fill_answers=None,
            state={"user_query": "공동존 서버들의 vcore 수"},
        )

        sql, validation_error = await mdb._generate_validated_sql(
            run, AsyncMock(), {"tables": {}}, "ctx", {},
            db_engine="db2", db_id="gp",
        )

        assert len(calls) == 1, "재생성 없이 1회로 중단(재시도 2회 절약)"
        assert validation_error and mdb._TOKEN_LIMIT_ERROR_PREFIX in validation_error

    async def test_ordinary_failure_still_retries(self, monkeypatch):
        """일반 검증 실패의 기존 재생성 루프(최대 3회 시도)는 유지된다."""
        calls = []

        async def fake_generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            calls.append(1)
            return "not sql at all"

        monkeypatch.setattr(mdb, "_generate_sql", fake_generate)
        run = SimpleNamespace(
            llm=AsyncMock(), parsed_requirements={}, effective_limit=100,
            unmapped_fields=None, app_config=_config(), mc_candidates=[],
            mc_derivations=[], prior_block=None, prior_scope=None, value_index=None,
            form_context="", form_fill_out=None, form_intent=False,
            mapping_sources=None, form_fill_answers=None,
            state={"user_query": "q"},
        )

        _sql, validation_error = await mdb._generate_validated_sql(
            run, AsyncMock(), {"tables": {}}, "ctx", {},
            db_engine="db2", db_id="gp",
        )

        assert len(calls) == 3, "기존 동작: 초기 1회 + 재생성 2회"
        assert validation_error
