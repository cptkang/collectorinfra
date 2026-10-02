"""트랙 A 다중 후보 생성·선택 테스트 (Plan 61 / E2·E3·E4 / D-073·D-074).

- E3 complexity 분류(결정적 규칙)
- E2 generate_candidates(multi_prompt N개·중복 제거·전략 태깅)
- E4 select_candidate(규칙필터→실행→결과일관성 투표→hybrid LLM 폴백→전패 all_failed)
- run_candidate_pipeline 합성
모두 mock LLM/executor로 구동(DB·실 LLM 불요).
"""

from __future__ import annotations

import pytest

from src.nodes.candidate_generator import classify_complexity, generate_candidates
from src.nodes.candidate_selector import (
    run_candidate_pipeline,
    select_candidate,
)
from src.prompts.candidate_strategies import apply_strategy, strategy_names


class FakeResp:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """user 프롬프트의 전략 suffix에 따라 서로 다른 SQL을 반환하는 목 LLM."""

    def __init__(self, mapping: dict[str, str], default: str = "SELECT 1"):
        self.mapping = mapping
        self.default = default
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        user = messages[-1].content
        for key, sql in self.mapping.items():
            if key in user:
                return FakeResp(sql)
        return FakeResp(self.default)


def _extract(content: str) -> str:
    return (content or "").strip()


# ── E3 복잡도 ──────────────────────────────────────

class TestClassifyComplexity:
    def test_simple_direct_lookup(self):
        assert classify_complexity("DB-ORA-023 서버의 IP를 알려줘", {}, {}) == "simple"

    def test_complex_aggregation_keyword(self):
        assert classify_complexity("서버별 CPU 평균 사용률", {}, {}) == "complex"

    def test_complex_multiple_targets(self):
        reqs = {"query_targets": ["cpu", "memory"]}
        assert classify_complexity("현황", reqs, {}) == "complex"

    def test_complex_eav_multi_attr(self):
        meta = {"_structure_meta": {"patterns": [{"type": "eav"}]}}
        assert classify_complexity("논리코어 수와 메모리 용량", {}, meta) == "complex"


# ── E2 생성 ────────────────────────────────────────

class TestGenerateCandidates:
    @pytest.mark.asyncio
    async def test_multi_prompt_produces_distinct(self):
        llm = FakeLLM({
            "분할 정복": "SELECT 2",
            "실행계획": "SELECT 3",
        }, default="SELECT 1")
        cands = await generate_candidates(
            llm, "SYS", "질의", count=3, strategies="multi_prompt",
            is_kbgenai=False, extract_sql=_extract,
        )
        sqls = {c["sql"] for c in cands}
        assert sqls == {"SELECT 1", "SELECT 2", "SELECT 3"}
        strategies = {c["strategy"] for c in cands}
        assert strategies == {"base", "divide_conquer", "exec_plan_cot"}

    @pytest.mark.asyncio
    async def test_dedup_identical(self):
        llm = FakeLLM({}, default="SELECT 1")  # 모든 전략이 동일 SQL
        cands = await generate_candidates(
            llm, "SYS", "질의", count=3, strategies="multi_prompt",
            is_kbgenai=False, extract_sql=_extract,
        )
        assert len(cands) == 1  # 중복 제거

    @pytest.mark.asyncio
    async def test_empty_sql_skipped(self):
        llm = FakeLLM({"분할 정복": "", "실행계획": ""}, default="SELECT 1")
        cands = await generate_candidates(
            llm, "SYS", "질의", count=3, strategies="multi_prompt",
            is_kbgenai=False, extract_sql=_extract,
        )
        assert [c["sql"] for c in cands] == ["SELECT 1"]


# ── E4 선택 ────────────────────────────────────────

def _exec_map(mapping):
    async def _execute(sql):
        return mapping.get(sql, {"rows": None, "error": "unknown"})
    return _execute


async def _pass(_sql):
    return None


class TestSelectCandidate:
    @pytest.mark.asyncio
    async def test_single_candidate_short_circuit(self):
        res = await select_candidate(
            [{"sql": "SELECT 1", "strategy": "base", "confidence": 1.0}],
            validate=_pass, execute=_exec_map({}),
        )
        assert res["method"] == "single"
        assert res["sql"] == "SELECT 1"
        assert res["all_failed"] is False

    @pytest.mark.asyncio
    async def test_consistency_majority(self):
        cands = [
            {"sql": "A", "strategy": "base", "confidence": 1.0},
            {"sql": "B", "strategy": "divide_conquer", "confidence": 0.95},
            {"sql": "C", "strategy": "exec_plan_cot", "confidence": 0.95},
        ]
        # A·C 동일 결과, B 다른 결과 → 다수(A,C) 승리
        execute = _exec_map({
            "A": {"rows": [{"x": 1}], "error": None},
            "B": {"rows": [{"x": 2}], "error": None},
            "C": {"rows": [{"x": 1}], "error": None},
        })
        res = await select_candidate(
            cands, validate=_pass, execute=execute, selection="consistency",
        )
        assert res["method"] == "consistency"
        assert res["sql"] in ("A", "C")
        assert res["confidence"] == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_all_exec_failed(self):
        cands = [
            {"sql": "A", "strategy": "base", "confidence": 1.0},
            {"sql": "B", "strategy": "divide_conquer", "confidence": 0.95},
        ]
        execute = _exec_map({
            "A": {"rows": None, "error": "boom"},
            "B": {"rows": None, "error": "boom"},
        })
        res = await select_candidate(cands, validate=_pass, execute=execute)
        assert res["all_failed"] is True
        assert res["method"] == "all_exec_failed"
        assert res["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_rule_filter_drops_invalid(self):
        cands = [
            {"sql": "BAD", "strategy": "base", "confidence": 1.0},
            {"sql": "GOOD", "strategy": "divide_conquer", "confidence": 0.95},
        ]

        async def _validate(sql):
            return "금지" if sql == "BAD" else None

        execute = _exec_map({"GOOD": {"rows": [{"x": 1}], "error": None}})
        res = await select_candidate(cands, validate=_validate, execute=execute)
        assert res["sql"] == "GOOD"
        assert res["audit"]["rule_passed"] == 1

    @pytest.mark.asyncio
    async def test_hybrid_llm_tiebreak(self):
        # 결과 그룹 2개로 갈림 → LLM 심판이 후보 1 선택
        cands = [
            {"sql": "A", "strategy": "base", "confidence": 1.0},
            {"sql": "B", "strategy": "divide_conquer", "confidence": 1.0},
        ]
        execute = _exec_map({
            "A": {"rows": [{"x": 1}], "error": None},
            "B": {"rows": [{"x": 2}], "error": None},
        })

        class JudgeLLM:
            async def ainvoke(self, messages):
                return FakeResp('{"choice": 1, "reason": "B가 정확"}')

        res = await select_candidate(
            cands, validate=_pass, execute=execute, selection="hybrid",
            llm=JudgeLLM(), user_query="질의",
        )
        assert res["method"] == "llm_pairwise"
        assert res["sql"] == "B"


class TestRunCandidatePipeline:
    @pytest.mark.asyncio
    async def test_pipeline_end_to_end(self):
        llm = FakeLLM({"분할 정복": "SELECT 2", "실행계획": "SELECT 3"}, default="SELECT 1")
        execute = _exec_map({
            "SELECT 1": {"rows": [{"n": 1}], "error": None},
            "SELECT 2": {"rows": [{"n": 1}], "error": None},  # 1과 동일 결과 → 다수
            "SELECT 3": {"rows": [{"n": 9}], "error": None},
        })
        res = await run_candidate_pipeline(
            llm, "SYS", "질의", count=3, strategies="multi_prompt",
            selection="consistency", is_kbgenai=False, extract_sql=_extract,
            validate=_pass, execute=execute, user_query="질의",
        )
        assert res["sql"] in ("SELECT 1", "SELECT 2")
        assert len(res["sql_candidates"]) == 3


class TestStrategyHelpers:
    def test_strategy_names_multi_prompt_cycle(self):
        assert strategy_names(3, "multi_prompt") == ["base", "divide_conquer", "exec_plan_cot"]
        assert strategy_names(4, "multi_prompt")[3] == "base#1"

    def test_strategy_names_temperature_all_base(self):
        assert strategy_names(3, "temperature") == ["base", "base", "base"]

    def test_apply_strategy_base_unchanged(self):
        assert apply_strategy("PROMPT", "base") == "PROMPT"

    def test_apply_strategy_appends_suffix(self):
        out = apply_strategy("PROMPT", "divide_conquer")
        assert out.startswith("PROMPT")
        assert "분할 정복" in out
