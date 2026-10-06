"""LLM 백엔드 오류 응답 감지 — 단일 경로 대칭 (plans/139 W1 · D-308 ⑦).

내부망 ITAM run `20261006-152938`: 단일 경로 프롬프트가 FabriX 한도를 넘자 FabriX가
**응답 본문으로** `An exception occurred in GptOssAdapter.llm_call: Input tokens must be
<= 95232. Given: 96858. … Error occurred from orchestrator`를 돌려줬다. 단일 검증에는 감지가
없어 `from orchestrator`를 FROM 절로 읽고(「존재하지 않는 테이블 참조: orchestrator」),
산문 판정으로 「조회 엔진이 남긴 설명」 안내 +
같은 크기 프롬프트 재호출 1회가 났다.

- 공용 판정 `detect_llm_backend_error` — 멀티 간이 검증 시절 문구와 바이트 동일 · Given/한도 파싱
- 단일 검증 코어는 SELECT 검사 **전에** 판정해 사유 하나만 낸다
- 그래프(실제 LangGraph 조립)·2단 단일 DB 루프·멀티 재생성 루프 모두 재생성 0회
- 사용자 화면에 「orchestrator」·「조회 엔진이 남긴 설명」이 나오지 않는다

LLM·DB는 전부 가짜다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

import importlib
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.graph import END, START, StateGraph

from src.security.pii_filter import scrub_pii
from src.sql_validation import (
    BACKEND_ERROR_OTHER,
    BACKEND_ERROR_TOKEN_LIMIT,
    TOKEN_LIMIT_ERROR_PREFIX,
    detect_llm_backend_error,
    validate_sql,
)
from src.state import AgentState, create_followup_input, create_initial_state

graph = importlib.import_module("src.graph")
mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")
qv = importlib.import_module("src.nodes.query_validator")
sub = importlib.import_module("src.orchestration.subagents")
replanner = importlib.import_module("src.orchestration.replanner")

#: 내부망 실보고 형태(plans/139 §2.3)
FABRIX_TEXT = (
    "An exception occurred in GptOssAdapter.llm_call: Input tokens must be <= 95232. "
    "Given: 96858. Please reduce the length of the messages. Error occurred from orchestrator"
)
ORCHESTRATOR_ONLY_TEXT = "Error occurred from orchestrator. reason: exception 발생"
FORBIDDEN_ON_SCREEN = ("orchestrator", "조회 엔진이 남긴 설명")


def _legacy_token_limit_message(text: str) -> str:
    """멀티 간이 검증의 종전 문구(이관 전 `_validate_sql_simple` 본문을 그대로 옮긴 기대값)."""
    excerpt = scrub_pii(" ".join(text.split())[:200])
    return (
        f"LLM 백엔드 입력 토큰 한도 초과 응답(비-SQL) — 프롬프트가 데이터 평면 "
        f"한도를 초과함(스키마 스코프·재료 축소 필요) | 응답 원문: {excerpt!r}"
    )


def _schema() -> dict:
    return {"tables": {"assets": {"columns": [
        {"name": "asset_id", "type": "integer"},
        {"name": "asset_name", "type": "varchar(100)"},
    ]}}}


def _cfg(*, budget: int = 0):
    """검증 대상 필드만 명시한 설정 대역(.env 누수 차단)."""
    cfg = MagicMock()
    cfg.query.default_limit = 1000
    cfg.query.max_retry_count = 3
    cfg.text2sql.multi_candidate = False
    cfg.text2sql.generic_llm_mapping = False
    cfg.text2sql.prompt_token_budget = budget
    cfg.get_polestar_db_ids.return_value = set()
    cfg.server.answer_reserve_sec = 0
    return cfg


def _fake_llm(content: str = FABRIX_TEXT) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return llm


def _graph_state() -> AgentState:
    state = create_initial_state(user_query="자산 목록 보여줘")
    state["schema_info"] = _schema()
    state["relevant_tables"] = ["assets"]
    state["parsed_requirements"] = {
        "query_targets": ["자산"], "filter_conditions": [], "time_range": None,
        "output_format": "text", "aggregation": None, "limit": None,
        "original_query": "자산 목록 보여줘",
    }
    return state


def _assert_clean(text: str | None) -> None:
    for word in FORBIDDEN_ON_SCREEN:
        assert word not in (text or ""), f"화면 문구에 {word!r}가 남았다: {text!r}"


# ──────────────────────────────────────────────
# 공용 판정
# ──────────────────────────────────────────────

class TestDetectLLMBackendError:
    def test_token_limit_kind_and_counts(self):
        err = detect_llm_backend_error(FABRIX_TEXT)
        assert err is not None
        assert err.kind == BACKEND_ERROR_TOKEN_LIMIT
        assert (err.given, err.limit) == (96858, 95232)
        assert err.as_marker() == {"kind": "token_limit", "given": 96858, "limit": 95232}

    def test_message_byte_identical_to_legacy_multi_text(self):
        err = detect_llm_backend_error(FABRIX_TEXT)
        assert err is not None and err.message == _legacy_token_limit_message(FABRIX_TEXT)

    def test_summary_has_no_raw_text(self):
        err = detect_llm_backend_error(FABRIX_TEXT)
        assert err is not None
        assert err.summary == f"{TOKEN_LIMIT_ERROR_PREFIX}(보고 96858 > 한도 95232)"
        _assert_clean(err.summary)

    def test_counts_missing_are_none(self):
        err = detect_llm_backend_error("Input tokens must be small")
        assert err is not None and err.kind == BACKEND_ERROR_TOKEN_LIMIT
        assert (err.given, err.limit) == (None, None)
        assert err.summary == TOKEN_LIMIT_ERROR_PREFIX

    def test_other_backend_error(self):
        err = detect_llm_backend_error(ORCHESTRATOR_ONLY_TEXT)
        assert err is not None and err.kind == BACKEND_ERROR_OTHER
        assert err.message.startswith("LLM 백엔드 예외 응답(비-SQL) | 응답 원문: ")
        _assert_clean(err.summary)

    @pytest.mark.parametrize("text", [
        "", "SELECT asset_id FROM assets", "요청하신 데이터는 스키마에 없습니다.",
    ])
    def test_not_backend_error(self, text):
        assert detect_llm_backend_error(text) is None


# ──────────────────────────────────────────────
# 단일 검증 코어 · 멀티 간이 검증 대칭
# ──────────────────────────────────────────────

class TestSingleValidateSql:
    def test_one_reason_before_select_and_table_checks(self):
        outcome = validate_sql(FABRIX_TEXT, _schema())
        assert outcome.errors == [_legacy_token_limit_message(FABRIX_TEXT)], (
            "사유 하나만 — 「존재하지 않는 테이블 참조: orchestrator」·SELECT 사유가 섞이면 안 된다"
        )
        assert outcome.backend_error is not None
        assert outcome.backend_error.kind == BACKEND_ERROR_TOKEN_LIMIT

    def test_same_reason_as_multi_simple_validation(self):
        """D-066 — 같은 응답에 단일·멀티가 같은 사유를 낸다."""
        single = validate_sql(FABRIX_TEXT, _schema()).errors
        multi = mdb._validate_sql_simple(FABRIX_TEXT, _schema())
        assert single == [multi]

    def test_normal_sql_unaffected(self):
        outcome = validate_sql("SELECT asset_id FROM assets LIMIT 10", _schema())
        assert outcome.passed and outcome.backend_error is None


class TestQueryValidatorNode:
    async def test_marker_and_clean_error_message(self):
        state = _graph_state()
        state["generated_sql"] = FABRIX_TEXT
        out = await qv.query_validator(state, app_config=_cfg())
        result = out["validation_result"]
        assert result["passed"] is False
        assert result["non_sql"] is False, "산문 판정 대상이 아니다"
        assert result["backend_error"] == {"kind": "token_limit", "given": 96858, "limit": 95232}
        assert result["reason"].startswith(TOKEN_LIMIT_ERROR_PREFIX)
        assert "존재하지 않는 테이블" not in result["reason"]
        assert out["error_message"] == f"{TOKEN_LIMIT_ERROR_PREFIX}(보고 96858 > 한도 95232)"
        _assert_clean(out["error_message"])

    async def test_token_budget_log_carries_given_limit_and_estimate(self, caplog):
        state = _graph_state()
        state["generated_sql"] = FABRIX_TEXT
        state["prompt_budget"] = {
            "estimated_tokens": 82215, "budget": 90000, "stage": "materials",
            "table_count": 99, "samples": True,
        }
        with caplog.at_level("ERROR", logger="src.nodes.query_validator"):
            await qv.query_validator(state, app_config=_cfg())
        line = next(r.getMessage() for r in caplog.records if "[토큰예산]" in r.getMessage())
        assert "96858" in line and "95232" in line and "82215" in line

    async def test_ordinary_prose_still_non_sql(self):
        """백엔드 오류가 아닌 산문은 종전 산문 판정 그대로다."""
        state = _graph_state()
        state["generated_sql"] = "요청하신 데이터는 스키마에 없습니다."
        out = await qv.query_validator(state, app_config=_cfg())
        assert out["validation_result"]["non_sql"] is True
        assert "backend_error" not in out["validation_result"]


# ──────────────────────────────────────────────
# 그래프 경로 — 실제 LangGraph 조립(생성 → 검증 → 분기)
# ──────────────────────────────────────────────

def _mini_graph(llm, cfg):
    """3단 직결 체인의 생성·검증·재시도 분기를 실제 노드·실제 분기 함수로 조립한다."""
    executed = []

    async def _executor(state):
        executed.append(state.get("generated_sql"))
        return {"error_message": None, "query_results": []}

    g = StateGraph(AgentState)
    g.add_node("query_generator", partial(qg.query_generator, llm=llm, app_config=cfg))
    g.add_node("query_validator", partial(qv.query_validator, app_config=cfg))
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
    return g.compile(), executed


class TestGraphPath:
    async def test_backend_limit_ends_without_regeneration(self):
        llm = _fake_llm()
        app, executed = _mini_graph(llm, _cfg())
        out = await app.ainvoke(_graph_state())
        assert llm.ainvoke.await_count == 1, "재생성 0회"
        assert executed == []
        assert out["validation_result"]["backend_error"]["given"] == 96858
        assert out["final_response"].startswith(qv.backend_limit_response("token_limit"))
        _assert_clean(out["final_response"])
        _assert_clean(out["error_message"])

    def test_route_functions_stop_immediately(self):
        state = _graph_state()
        state["validation_result"] = {
            "passed": False, "reason": "x", "auto_fixed_sql": None, "non_sql": False,
            "backend_error": {"kind": "token_limit", "given": 1, "limit": 0},
        }
        state["retry_count"] = 0
        assert graph.route_after_validation(state) == "error_response"
        assert graph.route_after_validation_with_approval(state) == "error_response"

    def test_other_backend_error_response_wording(self):
        text = qv.backend_limit_response(BACKEND_ERROR_OTHER)
        assert "오류 응답" in text and "입력 한도" not in text
        _assert_clean(text)
        assert "대신" not in text and "대신" not in qv.backend_limit_response("token_limit")


# ──────────────────────────────────────────────
# 2단 단일 DB 루프
# ──────────────────────────────────────────────

def _sub_cfg():
    return SimpleNamespace(
        query=SimpleNamespace(max_retry_count=3, default_limit=1000),
        get_polestar_db_ids=lambda: set(),
        server=SimpleNamespace(answer_reserve_sec=0.0),
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: ["polestar_b0"], zone_group_exclusive=True,
        ),
        polestar_rest=SimpleNamespace(realtime_usage_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        text2sql=SimpleNamespace(multi_full_validation=False, alarm_deterministic=False),
    )


def _patch_sub(monkeypatch, gen_output: dict):
    calls = {"gen": 0, "exec": 0}

    async def _schema_node(state, llm=None, app_config=None):
        return {"schema_info": _schema()}

    async def _gen(state, llm=None, app_config=None):
        calls["gen"] += 1
        rc = state.get("retry_count", 0) + (1 if state.get("error_message") else 0)
        return {"retry_count": rc, "error_message": None, **gen_output}

    async def _exec(state, app_config=None):
        calls["exec"] += 1
        return {"error_message": None, "query_results": []}

    async def _organize(state, llm=None, app_config=None):
        return {"organized_data": {"rows": [], "summary": ""}, "error_message": None}

    monkeypatch.setattr(sub, "schema_analyzer", _schema_node)
    monkeypatch.setattr(sub, "query_generator", _gen)
    monkeypatch.setattr(sub, "query_executor", _exec)
    monkeypatch.setattr(sub, "result_organizer", _organize)
    return calls


def _isolated() -> dict:
    return {
        "user_query": "자산 목록 보여줘",
        "original_user_query": "자산 목록 보여줘",
        "parsed_requirements": {"original_query": "자산 목록 보여줘", "query_targets": ["x"]},
        "conversation_context": None, "allowed_db_ids": None, "user_role": None,
        "selected_db_ids": None, "zone_selection_db_ids": None,
        "realtime_usage_intent": False, "zone_clarification_allowed": False,
        "retry_count": 0, "error_message": None,
        "validation_result": {"passed": False, "reason": "", "auto_fixed_sql": None},
        "organized_data": {"rows": [], "summary": ""}, "query_results": [],
        "generated_sql": "", "request_deadline": None,
    }


_TASK = {"task_id": "t1", "agent": "data_query", "sub_query": "자산 목록",
         "db_ids": ["polestar_b0"]}


class TestTier2SingleLoop:
    async def test_backend_limit_stops_after_one_generation(self, monkeypatch):
        calls = _patch_sub(monkeypatch, {"generated_sql": FABRIX_TEXT})
        out = await sub._run_single_db_pipeline(_isolated(), MagicMock(), _sub_cfg())
        assert calls == {"gen": 1, "exec": 0}, "재생성 0회 · 실행 없음"
        assert out["regen_stop"]["reason"] == qv.REGEN_STOP_BACKEND_LIMIT == "backend_limit"
        assert out["regen_stop"]["detail"].startswith(TOKEN_LIMIT_ERROR_PREFIX)
        assert out["regen_stop_response"] == qv.backend_limit_response("token_limit")
        _assert_clean(out["regen_stop"]["detail"])

    async def test_task_result_is_clean(self, monkeypatch):
        _patch_sub(monkeypatch, {"generated_sql": FABRIX_TEXT})
        res = await sub.run_data_query_pipeline(
            dict(_TASK), _isolated(), llm=MagicMock(), app_config=_sub_cfg(),
        )
        assert res["final_response"] == qv.backend_limit_response("token_limit")
        assert res["regen_stop"]["reason"] == "backend_limit"
        _assert_clean(res["final_response"])
        _assert_clean(res.get("error"))
        _assert_clean(res["regen_stop"]["detail"])

    def test_replanner_notice_wording(self):
        notice = replanner._regen_stop_notice([
            ({"sub_query": "자산 목록"},
             {"reason": "backend_limit", "detail": f"{TOKEN_LIMIT_ERROR_PREFIX}(보고 2 > 한도 1)"}),
        ])
        assert replanner._REGEN_STOP_REASON_TEXT["backend_limit"] in notice
        assert "대신" not in notice
        _assert_clean(notice)


# ──────────────────────────────────────────────
# 멀티 DB 재생성 루프 — 사유·재시도 중단 비트 동일
# ──────────────────────────────────────────────

class TestMultiLoop:
    async def test_reason_and_single_generation(self, monkeypatch):
        calls = []

        async def fake_generate(llm, parsed, schema_info, sub_context, limit, **kwargs):
            calls.append(1)
            return FABRIX_TEXT

        monkeypatch.setattr(mdb, "_generate_sql", fake_generate)
        run = SimpleNamespace(
            llm=AsyncMock(), parsed_requirements={}, effective_limit=100,
            unmapped_fields=None,
            app_config=SimpleNamespace(
                query=SimpleNamespace(default_limit=100),
                text2sql=SimpleNamespace(multi_full_validation=False, prompt_token_budget=0),
            ),
            mc_candidates=[], mc_derivations=[], prior_block=None, prior_scope=None,
            value_index=None, form_context="", form_fill_out=None, form_intent=False,
            mapping_sources=None, form_fill_answers=None,
            state={"user_query": "자산 목록"},
        )
        _sql, validation_error = await mdb._generate_validated_sql(
            run, AsyncMock(), {"tables": {}}, "ctx", {}, db_engine="postgresql", db_id="gp",
        )
        assert len(calls) == 1, "재생성 0회"
        assert validation_error == _legacy_token_limit_message(FABRIX_TEXT)
        assert mdb._TOKEN_LIMIT_ERROR_PREFIX == TOKEN_LIMIT_ERROR_PREFIX


# ──────────────────────────────────────────────
# 요청 스코프 상태
# ──────────────────────────────────────────────

class TestRequestScope:
    def test_prompt_budget_initialized_in_both_state_builders(self):
        assert create_initial_state(user_query="q")["prompt_budget"] is None
        assert create_followup_input("q")["prompt_budget"] is None
