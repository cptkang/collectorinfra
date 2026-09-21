"""plans/107 W0.5 — 표면어 결정적 판정은 원문 기준으로 읽는다.

오케스트레이션(1·2단)은 단일 DB 파이프라인에 들어가며 ``user_query``를 재작성문으로 바꾼다.
순위·최상급(트랙 C)·급증 계열(급증·비교기간·파일시스템·절대임계·기타지표) 판정이 그 재작성문을
읽으면 표면어가 탈락·추가될 때 원문과 다른 결론이 난다. 이 테스트는
①판정 입력 선택 규칙 ②3단(원문 키 부재)에서 종전과 바이트 동일 ③단일·멀티 경로 대칭을 고정한다.

DB·LLM 0(D-127).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config import AppConfig, QueryConfig, Text2SQLConfig
from src.nodes.query_generator import _GenContext, _try_spike
from src.nodes.semantic_compiler import compile_smq, load_semantic_model
from src.semantic.ir import SMQ
from src.utils.query_gen_common import surface_query_for_judgment

SPIKE_QUERY = "파일시스템 사용률이 갑자기 80% 이상으로 상승한 서버 목록"
# 라우터 정제가 수량·변화 표현을 압축해 떨어뜨린 형태(재작성문)
REWRITTEN_SPIKE = "파일시스템 사용률 서버 목록"


class TestSurfaceQuerySelection:
    """판정 입력 선택 규칙 — 원문 키가 있고 복합 계획이 아니면 원문."""

    def test_graph_path_without_original_key_uses_user_query(self):
        """3단 그래프 경로에는 원문 키가 없다 — user_query가 곧 원문이라 종전과 같다."""
        assert surface_query_for_judgment({"user_query": "원문"}) == "원문"

    def test_orchestration_single_task_uses_original(self):
        state = {"user_query": "재작성문", "original_user_query": "원문", "is_composite": False}
        assert surface_query_for_judgment(state) == "원문"

    def test_composite_plan_keeps_task_scoped_rewrite(self):
        """복합 계획의 원문에는 다른 task 조건이 섞여 있다 — task 스코프 재작성문을 쓴다."""
        state = {"user_query": "재작성문", "original_user_query": "원문", "is_composite": True}
        assert surface_query_for_judgment(state) == "재작성문"

    def test_empty_original_falls_back(self):
        state = {"user_query": "재작성문", "original_user_query": ""}
        assert surface_query_for_judgment(state) == "재작성문"

    def test_missing_everything_is_empty_string(self):
        assert surface_query_for_judgment({}) == ""


def _spike_config() -> AppConfig:
    config = AppConfig()
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(spike_condition_enabled=True)
    return config


def _spike_ctx(config: AppConfig, query: str) -> _GenContext:
    return _GenContext(
        llm=MagicMock(), app_config=config, user_query=query, retry_count=0,
        is_retry=False, limit_value=100, stat_month=None, stat_block_db=True,
        conversation_context=None, prior_scope=None,
        adapter_db_ids={"polestar", "b0", "cm_gp"},
    )


def _spike_state(**overrides) -> dict:
    base = {
        "active_db_id": "polestar",
        "active_db_engine": "postgresql",
        "user_query": SPIKE_QUERY,
        "parsed_requirements": {"query_targets": ["서버"]},
        "template_structure": None,
        "column_mapping": None,
    }
    base.update(overrides)
    return base


class TestSpikeChainReadsOriginal:
    """급증 조립 진입 판정 5종(급증·비교기간·파일시스템·절대임계·기타지표)."""

    def test_graph_path_unchanged(self):
        """원문 키 부재(3단) — 종전과 같은 입력으로 조립에 진입한다."""
        result = _try_spike(_spike_state(), _spike_ctx(_spike_config(), SPIKE_QUERY))
        assert result is not None and "sql" in result

    def test_rewrite_dropping_spike_terms_no_longer_skips_assembly(self):
        """재작성문이 '갑자기 80% 이상으로 상승'을 떨어뜨려도 원문 기준으로 조립에 진입한다.

        종전(재작성문 판정)이면 급증 어휘가 없어 None — 사용자가 요구한 급증 조건이 조용히
        사라지고 LLM 경로로 갔다.
        """
        state = _spike_state(
            user_query=REWRITTEN_SPIKE, original_user_query=SPIKE_QUERY, is_composite=False,
        )
        result = _try_spike(state, _spike_ctx(_spike_config(), REWRITTEN_SPIKE))
        assert result is not None and "sql" in result
        assert any("절대 임계 80%" in n for n in result["spike_notes"])

    def test_before_fix_rewritten_text_alone_would_skip(self):
        """대조군 — 원문이 없으면(재작성문만) 급증 어휘가 없어 조립하지 않는다."""
        state = _spike_state(user_query=REWRITTEN_SPIKE)
        assert _try_spike(state, _spike_ctx(_spike_config(), REWRITTEN_SPIKE)) is None

    def test_composite_plan_does_not_borrow_other_task_terms(self):
        """복합 계획이면 원문의 급증 조건을 이 task로 끌어오지 않는다(스코프 번짐 방지)."""
        state = _spike_state(
            user_query=REWRITTEN_SPIKE, original_user_query=SPIKE_QUERY, is_composite=True,
        )
        assert _try_spike(state, _spike_ctx(_spike_config(), REWRITTEN_SPIKE)) is None


def _pattern_b_smq() -> SMQ:
    return SMQ.from_dict({
        "pattern": "B", "dimensions": ["name"],
        "measures": [
            {"agg": "avg", "definition_name": "Utilization", "resource_type": "server.Cpus"},
        ],
        "time_grain": "month",
    })


class TestRankingReadsSurfaceQuery:
    """트랙 C 순위·최상급 판정 — surface_query가 있으면 그것을, 없으면 user_query를 본다."""

    def test_default_none_is_byte_identical(self):
        """surface_query 미지정 = 종전 호출과 같은 SQL(3단·기존 호출부 회귀 0)."""
        model = load_semantic_model("polestar_cm_gp")
        before = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                             user_query="CPU 평균 사용률이 가장 높은 서버")
        after = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                            user_query="CPU 평균 사용률이 가장 높은 서버", surface_query=None)
        assert before == after

    def test_rewrite_dropping_superlative_still_ranks(self):
        """재작성문이 '가장 높은'을 떨어뜨려도 원문 기준으로 정렬·상위 1건을 유지한다."""
        model = load_semantic_model("polestar_cm_gp")
        sql = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                          user_query="CPU 평균 사용률 서버",
                          surface_query="CPU 평균 사용률이 가장 높은 서버")
        assert "ORDER BY" in sql.upper() and "DESC" in sql.upper()

    def test_rewrite_adding_superlative_is_ignored(self):
        """재작성문이 원문에 없던 '최고'를 얹어도 정렬을 만들지 않는다(추가 방향 변조 차단)."""
        model = load_semantic_model("polestar_cm_gp")
        baseline = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                               user_query="CPU 평균 사용률 서버")
        sql = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                          user_query="CPU 평균 사용률 최고 서버",
                          surface_query="CPU 평균 사용률 서버")
        assert sql == baseline


class TestLimitReadsSurfaceQuery:
    """U-10(v2.6) — IR 부재 시 LIMIT 표면어 해석도 surface_query를 본다(확대 방향 오염 차단)."""

    def test_rewrite_adding_per_server_does_not_lift_limit(self):
        """재작성문이 원문에 없던 '서버별'을 얻어도 LIMIT은 원문 기준(default)이다."""
        model = load_semantic_model("polestar_cm_gp")
        baseline = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                               user_query="CPU 평균 사용률 보여줘", default_limit=1000)
        sql = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                          user_query="각 서버별 CPU 평균 사용률 조회", default_limit=1000,
                          surface_query="CPU 평균 사용률 보여줘")
        assert sql == baseline
        assert "10000" not in sql

    def test_rewrite_dropping_all_scope_keeps_lift(self):
        """재작성문이 '모든'을 떨어뜨려도 원문 기준으로 전체 상한을 유지한다."""
        model = load_semantic_model("polestar_cm_gp")
        sql = compile_smq(_pattern_b_smq(), "polestar_cm_gp", model,
                          user_query="CPU 평균 사용률 조회", default_limit=1000,
                          surface_query="모든 서버의 CPU 평균 사용률 보여줘")
        assert "10000" in sql


class TestMultiPathSymmetry:
    """멀티 DB 경로도 같은 판정 입력을 트랙 C로 넘긴다(단일/멀티 대칭 — Known Mistakes)."""

    @pytest.mark.asyncio
    async def test_try_semantic_compile_forwards_surface_query(self):
        from src.nodes import multi_db_executor as mde

        config = AppConfig()
        config.text2sql = Text2SQLConfig(semantic_compose=True)
        fake = AsyncMock(return_value=(None, None, None))
        with patch.object(mde, "compile_from_nl", fake):
            await mde._try_semantic_compile(
                MagicMock(), {"original_query": "재작성문"}, {}, 100, None, None,
                "postgresql", "polestar_cm_gp", config, None, None, None,
                parity=False, surface_query="원문",
            )
        assert fake.await_args.kwargs["surface_query"] == "원문"
        # SMQ 선택 LLM 입력은 종전대로 R6(parsed_requirements.original_query)
        assert fake.await_args.args[1] == "재작성문"

    @pytest.mark.asyncio
    async def test_try_semantic_compile_default_is_none(self):
        """미지정이면 None — compile_from_nl이 user_query로 폴백해 종전과 같다."""
        from src.nodes import multi_db_executor as mde

        config = AppConfig()
        config.text2sql = Text2SQLConfig(semantic_compose=True)
        fake = AsyncMock(return_value=(None, None, None))
        with patch.object(mde, "compile_from_nl", fake):
            await mde._try_semantic_compile(
                MagicMock(), {"original_query": "q"}, {}, 100, None, None,
                "postgresql", "polestar_cm_gp", config, None, None, None, parity=False,
            )
        assert fake.await_args.kwargs["surface_query"] is None


class TestCurrentArgument:
    """호출부가 쥔 현재 질의(ctx.user_query)를 넘기면 원문이 없을 때 그 값을 그대로 쓴다."""

    def test_current_used_when_no_original(self):
        assert surface_query_for_judgment({"user_query": "state값"}, "ctx값") == "ctx값"

    def test_original_still_wins_over_current(self):
        state = {"user_query": "재작성문", "original_user_query": "원문"}
        assert surface_query_for_judgment(state, "ctx값") == "원문"

    def test_composite_returns_current(self):
        state = {"original_user_query": "원문", "is_composite": True}
        assert surface_query_for_judgment(state, "ctx값") == "ctx값"


class TestAllScopeLimitReadsOriginal:
    """'전체/모든' 행 상한 상향(검증기 안전망) — W-1이 확정한 오염 슬롯(판정 보류 → 오염).

    LLM SQL에 LIMIT이 없을 때 검증기가 붙이는 상한을 표면어로 정한다. 재작성문이 "전체"를
    떨어뜨리면 10,000이 아니라 기본 상한으로 절단된다 — ``resolved_limit``(D-066)이 이
    안전망에는 닿지 않는다(검증기는 승격값을 읽지 않는다).
    """

    def test_validate_sql_uses_original_for_all_scope(self):
        from src.sql_validation import validate_sql

        sql = "SELECT hostname FROM t"
        rewritten = validate_sql(sql, {}, db_engine="postgresql",
                                 user_query="서버 호스트명", default_limit=1000)
        original = validate_sql(sql, {}, db_engine="postgresql",
                                user_query="전체 서버 호스트명", default_limit=1000)
        assert "1000" in (rewritten.auto_fixed_sql or "")
        assert "10000" in (original.auto_fixed_sql or "")

    @pytest.mark.asyncio
    async def test_query_validator_passes_surface_query(self, monkeypatch):
        import importlib

        from src.sql_validation import SQLValidationOutcome

        # `src.nodes`가 노드 함수를 같은 이름으로 재노출하므로 모듈은 importlib로 잡는다.
        qv = importlib.import_module("src.nodes.query_validator")
        captured: dict = {}

        def _fake_validate(sql, schema_info, **kwargs):
            captured.update(kwargs)
            return SQLValidationOutcome()

        monkeypatch.setattr(qv, "validate_sql", _fake_validate)
        config = AppConfig()
        state = {
            "generated_sql": "SELECT 1", "schema_info": {"tables": {}},
            "user_query": "서버 호스트명", "original_user_query": "전체 서버 호스트명",
            "is_composite": False, "active_db_id": "x",
        }
        await qv.query_validator(state, app_config=config)
        assert captured["user_query"] == "전체 서버 호스트명"

    def test_multi_validate_sql_keeps_adapter_input_but_limits_by_surface(self):
        from src.nodes import multi_db_executor as mde

        config = AppConfig()
        _, fixed = mde._validate_sql(
            "SELECT hostname FROM t", {}, db_engine="postgresql",
            user_query="서버 호스트명", app_config=config,
            surface_query="전체 서버 호스트명",
        )
        assert "10000" in (fixed or "")

    def test_multi_validate_sql_default_is_unchanged(self):
        """surface_query 미지정 = 종전(user_query로 판정)."""
        from src.nodes import multi_db_executor as mde

        config = AppConfig()
        _, fixed = mde._validate_sql(
            "SELECT hostname FROM t", {}, db_engine="postgresql",
            user_query="서버 호스트명", app_config=config,
        )
        assert "10000" not in (fixed or "") and "1000" in (fixed or "")
