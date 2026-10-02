"""query_generator 다중 후보 통합 배선 테스트 (Plan 61 트랙 A / 경로 A·B).

multi_candidate ON이면 generate→select 파이프라인을 타고 sql_candidates가 실리며,
OFF면 기존 단일 LLM 경로(회귀 0)임을 검증한다. get_db_client·query_validator는 패치.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.query_generator import query_generator


def _cfg(**t2):
    cfg = MagicMock()
    cfg.query.default_limit = 100
    cfg.get_polestar_db_ids.return_value = set()
    cfg.synonym.value_retrieval = False
    cfg.synonym.fuzzy_match = False
    cfg.synonym.match_confidence_min = 0.85
    cfg.text2sql.semantic_compose = False
    cfg.text2sql.semantic_fallback = "candidate_then_human"
    cfg.text2sql.fallback_confidence_min = 0.0
    cfg.text2sql.multi_candidate = t2.get("multi_candidate", False)
    cfg.text2sql.candidate_count = t2.get("candidate_count", 3)
    cfg.text2sql.candidate_strategies = t2.get("candidate_strategies", "multi_prompt")
    cfg.text2sql.complexity_gate = t2.get("complexity_gate", False)
    cfg.text2sql.selection = t2.get("selection", "consistency")
    return cfg


def _state():
    return {
        "user_query": "서버별 CPU 평균 사용률",
        "schema_info": {"tables": {}},
        "parsed_requirements": {"original_query": "서버별 CPU 평균 사용률",
                                "query_targets": ["cpu"]},
        "active_db_id": "polestar_cm_gp",
        "column_descriptions": {},
        "column_synonyms": {},
        "retry_count": 0,
    }


class _Result:
    def __init__(self, rows):
        self.rows = rows
        self.row_count = len(rows)


class _Client:
    def __init__(self, mapping):
        self.mapping = mapping

    async def execute_sql(self, sql):
        return _Result(self.mapping.get(sql.strip(), [{"x": 1}]))


class _MultiLLM:
    """전략 suffix별 상이 SQL 반환."""
    async def ainvoke(self, messages):
        user = messages[-1].content
        if "분할 정복" in user:
            return MagicMock(content="SELECT 2")
        if "실행계획" in user:
            return MagicMock(content="SELECT 1")
        return MagicMock(content="SELECT 1")


@pytest.mark.asyncio
async def test_multi_candidate_on_populates_candidates():
    cfg = _cfg(multi_candidate=True, candidate_count=3, selection="consistency")
    # SELECT 1(base·exec_plan_cot 동일)이 2표, SELECT 2가 1표 → 결과일관성 다수 = SELECT 1
    client = _Client({"SELECT 1": [{"n": 1}], "SELECT 2": [{"n": 2}]})

    @asynccontextmanager
    async def _fake_client(config, *, db_id=None):
        yield client

    vr = {"validation_result": {"passed": True, "reason": "", "auto_fixed_sql": None}}
    with patch("src.db.get_db_client", _fake_client), \
         patch("src.nodes.query_validator.query_validator", AsyncMock(return_value=vr)):
        result = await query_generator(_state(), llm=_MultiLLM(), app_config=cfg)

    assert result["generated_sql"] == "SELECT 1"
    assert result["sql_candidates"] is not None
    assert len(result["sql_candidates"]) == 2  # SELECT 1, SELECT 2 (중복 제거)


@pytest.mark.asyncio
async def test_multi_candidate_off_single_path():
    cfg = _cfg(multi_candidate=False)

    class _Single:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return MagicMock(content="SELECT 42")

    llm = _Single()
    result = await query_generator(_state(), llm=llm, app_config=cfg)
    assert result["generated_sql"] == "SELECT 42"
    assert result["sql_candidates"] is None  # 단일 경로는 후보 미생성
    assert llm.calls == 1  # LLM 1회(회귀 0)


class TestDecideFallbackTier:
    """트랙 C 커버리지 밖 3단 폴백 티어 판정 (E6-3)."""

    def _run(self, coverage_outside, fallback, conf, all_failed=False, min_conf=0.0):
        from src.nodes.query_generator import _decide_fallback_tier
        cfg = _cfg()
        cfg.text2sql.semantic_fallback = fallback
        cfg.text2sql.fallback_confidence_min = min_conf
        sel = {"confidence": conf, "all_failed": all_failed, "method": "consistency"}
        return _decide_fallback_tier(coverage_outside, cfg, sel)

    def test_coverage_inside_returns_none(self):
        assert self._run(False, "candidate_then_human", 1.0) is None

    def test_llm_fallback_returns_none(self):
        assert self._run(True, "llm", 0.0) is None

    def test_candidate_then_human_all_failed_is_human_review(self):
        r = self._run(True, "candidate_then_human", 0.0, all_failed=True)
        assert r["tier"] == "human_review"

    def test_candidate_then_human_confident_is_auto(self):
        r = self._run(True, "candidate_then_human", 0.9, min_conf=0.5)
        assert r["tier"] == "auto"

    def test_candidate_then_human_low_conf_is_human_review(self):
        r = self._run(True, "candidate_then_human", 0.3, min_conf=0.5)
        assert r["tier"] == "human_review"

    def test_human_always_review(self):
        r = self._run(True, "human", 1.0, min_conf=0.0)
        assert r["tier"] == "human_review"


@pytest.mark.asyncio
async def test_complexity_gate_suppresses_simple():
    # complexity_gate ON + 단순 질의 → 단일 경로(후보 억제)
    cfg = _cfg(multi_candidate=True, complexity_gate=True)

    class _Single:
        def __init__(self):
            self.calls = 0

        async def ainvoke(self, messages):
            self.calls += 1
            return MagicMock(content="SELECT 7")

    st = _state()
    st["user_query"] = "DB-ORA-023 서버의 IP"  # 단순
    st["parsed_requirements"] = {"original_query": "DB-ORA-023 서버의 IP"}
    llm = _Single()
    result = await query_generator(st, llm=llm, app_config=cfg)
    assert result["generated_sql"] == "SELECT 7"
    assert result["sql_candidates"] is None
    assert llm.calls == 1


class TestPipelineErrorNotMislabeledAsNoDb:
    """파이프라인 자체 예외의 no_db 위장 방지 (Plan 69 P0-②)."""

    @pytest.mark.asyncio
    async def test_pipeline_exception_returns_pipeline_error(self):
        """run_candidate_pipeline 예외는 'no_db'가 아니라 'pipeline_error'로 반환된다."""
        from src.nodes.query_generator import _run_multi_candidate_single_db

        @asynccontextmanager
        async def _ok_client(*args, **kwargs):
            client = MagicMock()
            client.execute_sql = AsyncMock()
            yield client

        gen_mock = AsyncMock()
        with patch("src.db.get_db_client", _ok_client), \
             patch("src.nodes.candidate_selector.run_candidate_pipeline",
                   AsyncMock(side_effect=RuntimeError("selector boom"))), \
             patch("src.nodes.candidate_generator.generate_candidates", gen_mock):
            result = await _run_multi_candidate_single_db(
                _state(), MagicMock(), _cfg(multi_candidate=True),
                "sys", "user", "질의",
            )

        assert result["method"] == "pipeline_error"
        assert result["all_failed"] is True
        assert "selector boom" in result["audit"]["error"]
        gen_mock.assert_not_awaited()  # no_db 경로(재생성)로 흐르지 않는다

    @pytest.mark.asyncio
    async def test_connection_failure_still_no_db(self):
        """DB 연결 실패는 종전대로 no_db + 생성만 수행 폴백이다(동작 보존)."""
        from src.nodes.query_generator import _run_multi_candidate_single_db

        @asynccontextmanager
        async def _bad_client(*args, **kwargs):
            raise ConnectionError("refused")
            yield  # pragma: no cover

        gen_mock = AsyncMock(return_value=[
            {"sql": "SELECT 1", "strategy": "s", "confidence": 0.5},
        ])
        with patch("src.db.get_db_client", _bad_client), \
             patch("src.nodes.candidate_generator.generate_candidates", gen_mock):
            result = await _run_multi_candidate_single_db(
                _state(), MagicMock(), _cfg(multi_candidate=True),
                "sys", "user", "질의",
            )

        assert result["method"] == "no_db"
        assert result["sql"] == "SELECT 1"
        gen_mock.assert_awaited_once()
