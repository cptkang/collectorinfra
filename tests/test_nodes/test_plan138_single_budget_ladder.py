"""단일 경로 토큰 예산 사다리 — 멀티와 같게 (plans/138 W2 · D-305 ⑥ · D-159 FIX-B 개정).

종전 단일 가드는 시스템 프롬프트만 추정하고, 넘으면 유사어·설명만 뺀 뒤 그래도 넘으면 로그만 남기고
보냈다(ITAM 99테이블 + 표본 → 설명 제거 뒤 82,215 추정으로 통과 → FabriX 96,858 > 95,232 거절).

- 추정 = 시스템 + **사용자** 프롬프트
- 초과 시 ①유사어·설명 재료 제거 ②표본(`sample_data`) 제거(스키마 얕은 사본) ③LLM 호출 없이
  `PromptBudgetExceeded` → 검증 노드가 재생성 0회로 종결(백엔드 한도 초과와 같은 종결 · 사유만
  「전송 전 예산 초과」)
- 예산 안이면 시스템·사용자 프롬프트 바이트 무변경
- 상태 `prompt_budget`(수치·단계·테이블 수·표본 유무) — 벤치(W6)가 키 이름 그대로 읽는다

LLM은 가짜다(D-127).
"""

from __future__ import annotations

import importlib
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.graph import END, START, StateGraph

from src.nodes.prompt_blocks import PromptBudgetExceeded, estimate_prompt_tokens
from src.state import AgentState, create_initial_state

graph = importlib.import_module("src.graph")
qg = importlib.import_module("src.nodes.query_generator")
qv = importlib.import_module("src.nodes.query_validator")

_BUDGET_KEYS = {"estimated_tokens", "budget", "stage", "table_count", "samples"}


# ──────────────────────────────────────────────
# 사다리 단위 — 결정적 렌더러
# ──────────────────────────────────────────────

def _fake_render(schema_info, *, materials):
    """본문 100tok · 재료 +1000tok · 표본 +1000tok(ASCII 4자/토큰)."""
    text = "S" * 400
    if materials:
        text += "M" * 4000
    if any((t or {}).get("sample_data") for t in (schema_info.get("tables") or {}).values()):
        text += "D" * 4000
    return text


def _ladder_schema() -> dict:
    return {
        "tables": {"assets": {"columns": [{"name": "asset_id"}], "sample_data": [{"a": 1}]}},
        "_structure_meta": {"kept": True},
    }


def _fit(budget, *, schema=None, user="U" * 400):
    schema = schema if schema is not None else _ladder_schema()
    full = _fake_render(schema, materials=True)
    return qg._fit_single_prompt_budget(
        _fake_render, schema, full, user, budget=budget, db_id="itam",
    )


class TestLadderUnit:
    @pytest.mark.parametrize("budget", [0, 5000])
    def test_within_budget_same_bytes(self, budget):
        schema = _ladder_schema()
        full = _fake_render(schema, materials=True)
        prompt, mark = qg._fit_single_prompt_budget(
            _fake_render, schema, full, "U" * 400, budget=budget, db_id="itam",
        )
        assert prompt is full, "예산 안(또는 가드 비활성)이면 같은 문자열 그대로"
        assert mark == {
            "estimated_tokens": 2200, "budget": budget, "stage": "within",
            "table_count": 1, "samples": True,
        }

    def test_user_prompt_counts(self):
        """시스템만 보면 예산 안(2100)이지만 사용자 프롬프트(100)를 더하면 넘는다."""
        prompt, mark = _fit(2150)
        assert mark["stage"] == "materials"
        assert "M" not in prompt and "D" in prompt

    def test_stage1_drops_materials_only(self):
        prompt, mark = _fit(1500)
        assert "M" not in prompt and "D" in prompt
        assert mark["stage"] == "materials" and mark["samples"] is True
        assert mark["estimated_tokens"] == 1200

    def test_stage2_drops_samples_without_mutating_cache(self):
        schema = _ladder_schema()
        prompt, mark = _fit(500, schema=schema)
        assert "M" not in prompt and "D" not in prompt
        assert mark["stage"] == "samples" and mark["samples"] is False
        assert mark["estimated_tokens"] == 200
        assert schema["tables"]["assets"]["sample_data"] == [{"a": 1}], "캐시 공유 객체 보존"

    def test_stage2_keeps_structure_meta(self):
        seen = []

        def render(schema_info, *, materials):
            seen.append(schema_info)
            return _fake_render(schema_info, materials=materials)

        schema = _ladder_schema()
        qg._fit_single_prompt_budget(
            render, schema, _fake_render(schema, materials=True), "U" * 400,
            budget=500, db_id="itam",
        )
        assert seen[-1]["_structure_meta"] == {"kept": True}
        assert "sample_data" not in seen[-1]["tables"]["assets"]

    def test_stage3_raises_with_budget_state(self, caplog):
        with caplog.at_level("WARNING", logger="src.nodes.query_generator"):
            with pytest.raises(PromptBudgetExceeded) as exc:
                _fit(50)
        assert exc.value.budget_state == {
            "estimated_tokens": 200, "budget": 50, "stage": "exceeded",
            "table_count": 1, "samples": False,
        }
        assert "전송 전 예산 초과" in str(exc.value)
        logs = [r.getMessage() for r in caplog.records if "[토큰예산]" in r.getMessage()]
        assert len(logs) == 3, "1단·2단 강등과 중단을 모두 로그로 남긴다(침묵 강등 금지)"
        assert "1단" in logs[0] and "2단" in logs[1] and "전송 전 예산 초과" in logs[2]


# ──────────────────────────────────────────────
# 노드 — 실제 렌더링
# ──────────────────────────────────────────────

_DESC = "자산 관리 번호를 나타내는 컬럼으로 장비 식별에 쓰인다. " * 40
_SAMPLE_VALUE = "sample-asset-value-0001"


def _cfg(budget: int):
    """검증 대상 필드만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.query.max_retry_count = 3
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.prompt_token_budget = budget
    cfg.get_polestar_db_ids.return_value = set()
    return cfg


def _state(*, descriptions: bool, samples: bool) -> AgentState:
    table = {
        "columns": [
            {"name": "asset_id", "type": "integer"},
            {"name": "asset_name", "type": "varchar(100)"},
        ],
    }
    if samples:
        table["sample_data"] = [{"asset_id": i, "asset_name": _SAMPLE_VALUE} for i in range(3)]
    state = create_initial_state(user_query="자산 목록 보여줘")
    state["schema_info"] = {"tables": {"assets": table}}
    state["relevant_tables"] = ["assets"]
    state["parsed_requirements"] = {
        "query_targets": ["자산"], "filter_conditions": [], "time_range": None,
        "output_format": "text", "aggregation": None, "limit": None,
        "original_query": "자산 목록 보여줘",
    }
    if descriptions:
        state["column_descriptions"] = {"assets.asset_id": _DESC}
    return state


def _llm():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(
        content="```sql\nSELECT asset_id FROM assets LIMIT 10;\n```",
    ))
    return llm


def _sent(llm) -> tuple[str, str]:
    messages = llm.ainvoke.await_args.args[0]
    return messages[0].content, messages[-1].content


async def _generate(state, budget):
    llm = _llm()
    out = await qg.query_generator(state, llm=llm, app_config=_cfg(budget))
    return llm, out


class TestNodeLadder:
    async def test_within_budget_bytes_identical(self):
        off_llm, off = await _generate(_state(descriptions=True, samples=True), 0)
        on_llm, on = await _generate(_state(descriptions=True, samples=True), 10**9)
        assert _sent(off_llm) == _sent(on_llm), "예산 안이면 시스템·사용자 프롬프트 바이트 무변경"
        assert on["prompt_budget"]["stage"] == "within"
        assert set(on["prompt_budget"]) == _BUDGET_KEYS
        system, user = _sent(on_llm)
        assert on["prompt_budget"]["estimated_tokens"] == (
            estimate_prompt_tokens(system) + estimate_prompt_tokens(user)
        )
        assert _DESC.strip() in system and _SAMPLE_VALUE in system

    async def test_stage1_materials(self):
        state = _state(descriptions=True, samples=False)
        _llm0, probe = await _generate(state, 1)
        floor = probe["prompt_budget"]["estimated_tokens"]  # 재료·표본 없는 크기
        llm, out = await _generate(_state(descriptions=True, samples=False), floor)
        assert out["prompt_budget"]["stage"] == "materials"
        system, _user = _sent(llm)
        assert _DESC.strip() not in system

    async def test_stage2_samples(self):
        state = _state(descriptions=False, samples=True)
        _llm0, probe = await _generate(state, 1)
        floor = probe["prompt_budget"]["estimated_tokens"]
        state = _state(descriptions=False, samples=True)
        llm, out = await _generate(state, floor)
        assert out["prompt_budget"]["stage"] == "samples"
        assert out["prompt_budget"]["samples"] is False
        system, _user = _sent(llm)
        assert _SAMPLE_VALUE not in system
        assert state["schema_info"]["tables"]["assets"]["sample_data"], "캐시 공유 객체 보존"

    async def test_stage3_no_llm_call(self):
        llm, out = await _generate(_state(descriptions=True, samples=True), 1)
        assert llm.ainvoke.await_count == 0, "보내기 전에 멈춘다"
        assert out["generated_sql"] == ""
        mark = out["prompt_budget"]
        assert mark["stage"] == "exceeded" and mark["budget"] == 1
        assert mark["table_count"] == 1 and mark["samples"] is False
        assert set(mark) == _BUDGET_KEYS


# ──────────────────────────────────────────────
# 전송 전 예산 초과 → 그래프 종결(재생성 0회 · 같은 사용자 문구)
# ──────────────────────────────────────────────

def _mini_graph(llm, cfg):
    g = StateGraph(AgentState)
    g.add_node("query_generator", partial(qg.query_generator, llm=llm, app_config=cfg))
    g.add_node("query_validator", partial(qv.query_validator, app_config=cfg))

    async def _executor(state):
        raise AssertionError("실행 단계에 오면 안 된다")

    g.add_node("query_executor", _executor)
    g.add_node("error_response", graph._error_response_node)
    g.add_edge(START, "query_generator")
    g.add_edge("query_generator", "query_validator")
    g.add_conditional_edges(
        "query_validator", partial(graph.route_after_validation, max_retry=3),
        {
            "query_executor": "query_executor", "query_generator": "query_generator",
            "error_response": "error_response",
        },
    )
    g.add_edge("query_executor", END)
    g.add_edge("error_response", END)
    return g.compile()


class TestPreSendStop:
    async def test_graph_ends_without_llm_call(self):
        llm = _llm()
        app = _mini_graph(llm, _cfg(1))
        out = await app.ainvoke(_state(descriptions=True, samples=True))
        assert llm.ainvoke.await_count == 0
        assert out["retry_count"] == 0, "재생성 0회"
        result = out["validation_result"]
        assert result["backend_error"] == {"kind": "prompt_budget", "given": None, "limit": None}
        assert result["reason"].startswith("전송 전 예산 초과")
        assert out["error_message"].startswith("전송 전 예산 초과")
        assert out["final_response"].startswith(qv.backend_limit_response("token_limit")), (
            "백엔드 보고 한도 초과와 같은 사용자 문구"
        )
        for word in ("orchestrator", "조회 엔진이 남긴 설명"):
            assert word not in out["final_response"]

    async def test_stale_exceeded_mark_ignored_when_sql_present(self):
        """표지가 남아 있어도 이번 산출이 SQL이면 종전 검증이다(빈 산출일 때만 종결)."""
        state = _state(descriptions=False, samples=False)
        state["generated_sql"] = "SELECT asset_id FROM assets LIMIT 10"
        state["prompt_budget"] = {
            "estimated_tokens": 9, "budget": 1, "stage": "exceeded",
            "table_count": 1, "samples": False,
        }
        out = await qv.query_validator(state, app_config=_cfg(1))
        assert out["validation_result"]["passed"] is True
