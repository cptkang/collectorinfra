"""plans/139 독립 검증 — 실제 노드 경로 종단 · 경로 대칭 · 멀티턴 요청 스코프.

구현 측 테스트(`test_plan139_table_selection.py` 등)는 2단 단일 루프의 `schema_analyzer`를
가짜로 바꿔 끼우고, 벤치 수신기(`schema_context_record`)는 합성 상태로만 검증한다. 여기서는
**실제 노드**(schema_analyzer → query_generator → query_validator)를 2단 핸들러
`run_data_query_pipeline`으로 돌려 `_pack_pipeline_result`가 내보내는 task 상태
(`run_capture.emit("task_pipeline_state", s)`)를 수신기에 그대로 통과시킨다.

시드: `testdata/itam_bench/closed/{itam_schema.json,table_definitions.yaml}`(G-7 — 실제 모양).
LLM·DB는 전부 가짜다(D-127 — 실 호출 없음). 실행·결과 정리 노드만 대역이다.
"""

from __future__ import annotations

import copy
import importlib
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from scripts.itam_bench._serve import schema_context_record
from src.config import AppConfig, Text2SQLConfig
from src.domain.table_definitions import parse_import_document, validate_table_definitions
from src.nodes import table_selection as ts
from src.observability import run_capture
from src.state import AgentState, create_followup_input, create_initial_state

mdb = importlib.import_module("src.nodes.multi_db_executor")
qg = importlib.import_module("src.nodes.query_generator")
sa = importlib.import_module("src.nodes.schema_analyzer")
sub = importlib.import_module("src.orchestration.subagents")

_ROOT = Path(__file__).resolve().parents[2]
_SEED_DIR = _ROOT / "testdata" / "itam_bench" / "closed"
_DB = "itam"

FABRIX_TEXT = (
    "An exception occurred in GptOssAdapter.llm_call: Input tokens must be <= 95232. "
    "Given: 96858. Please reduce the length of the messages. Error occurred from orchestrator"
)
_SELECT_MARK = "## 테이블 성격 안내"
_PURPOSE_HEADER = "[테이블 용도]"
_SQL = "SELECT 가상화서버명, IP주소내용 FROM tcdmsif92 LIMIT 100"


# ──────────────────────────────────────────────
# 가짜 LLM · 시드 · 설정
# ──────────────────────────────────────────────


class _RouterLLM:
    """프롬프트 내용으로 선별 호출과 SQL 생성 호출을 가른다(응답이 예외면 던진다)."""

    def __init__(self, select: Any, sql: Any = _SQL) -> None:
        self._select = select
        self._sql = sql
        self.select_prompts: list[str] = []
        self.sql_prompts: list[str] = []

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        text = "\n".join(str(getattr(m, "content", m)) for m in messages)
        if _SELECT_MARK in text:
            self.select_prompts.append(text)
            response = self._select
        else:
            self.sql_prompts.append(text)
            response = self._sql
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(content=response)


def _picks(*names: str) -> str:
    return json.dumps({"tables": list(names)}, ensure_ascii=False)


def _seed() -> tuple[dict, dict, str]:
    raw = json.loads((_SEED_DIR / "itam_schema.json").read_text(encoding="utf-8"))
    schema = raw["schema"]
    doc = yaml.safe_load((_SEED_DIR / "table_definitions.yaml").read_text(encoding="utf-8"))
    columns = {t: [c["name"] for c in info["columns"]] for t, info in schema["tables"].items()}
    defs, errors = validate_table_definitions(parse_import_document(doc), columns)
    assert errors == {}
    allowed = [
        t for t, entry in doc["tables"].items()
        if not (isinstance(entry, dict) and entry.get("allowed") is False)
    ]
    profile = {"source": "manual", "allowed_tables": allowed, "table_definitions": defs}
    return schema, profile, raw.get("_db_description") or ""


def _cfg(*, budget: int | None = None) -> AppConfig:
    cfg = AppConfig(checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    cfg.text2sql = Text2SQLConfig(
        schema_table_select_max=8, schema_table_select_skip_enabled=False,
        **({} if budget is None else {"prompt_token_budget": budget}),
    )
    return cfg


def _cache_mgr(schema: dict, description: str) -> AsyncMock:
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.return_value = (schema, True, "메모리", {}, {})
    mgr.get_db_description.return_value = description
    mgr.get_synonyms.return_value = {}
    mgr.redis_available = False
    return mgr


def _isolated(question: str) -> dict:
    return {
        "user_query": question, "original_user_query": question, "active_db_id": _DB,
        "thread_id": "verify-138",
        "parsed_requirements": {"original_query": question, "query_targets": []},
        "conversation_context": None, "allowed_db_ids": None, "user_role": None,
        "selected_db_ids": None, "zone_selection_db_ids": None,
        "realtime_usage_intent": False, "zone_clarification_allowed": False,
        "retry_count": 0, "error_message": None,
        "validation_result": {"passed": False, "reason": "", "auto_fixed_sql": None},
        "organized_data": {"rows": [], "summary": ""}, "query_results": [],
        "generated_sql": "", "request_deadline": None,
    }


class _Captured:
    """수신기 — 받은 순간 벤치 레코드로 접는다(상태 객체는 뒤에 바뀔 수 있다)."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.raw: list[dict] = []

    def __call__(self, kind: str, payload: Any) -> None:
        record = schema_context_record(kind, payload)
        if record is not None:
            self.records.append(record)
            self.raw.append({
                k: copy.deepcopy(payload.get(k))
                for k in ("prompt_budget", "table_selection", "regen_stop", "regen_stops",
                          "validation_result", "active_db_id", "is_multi_db")
            })


@pytest.fixture()
def capture():
    sink = _Captured()
    run_capture.install(sink)
    try:
        yield sink
    finally:
        run_capture.uninstall()


async def _run_tier2_single(llm: _RouterLLM, question: str, *, cfg: AppConfig,
                            samples: list[dict] | None = None) -> tuple[dict, dict]:
    """2단 핸들러를 실제 스키마 분석·생성·검증 노드로 돌린다(실행·결과 정리만 대역)."""
    schema, profile, desc = _seed()
    calls = {"exec": 0}

    @asynccontextmanager
    async def _ctx(client):
        yield client

    client = AsyncMock()
    client.get_sample_data.return_value = list(samples or [])

    async def _exec(state, app_config=None):
        calls["exec"] += 1
        return {"error_message": None, "query_results": [{"가상화서버명": "vm-a"}]}

    async def _organize(state, llm=None, app_config=None):
        return {"organized_data": {"rows": state.get("query_results") or [], "summary": "ok"},
                "error_message": None}

    task = {"task_id": "t1", "agent": "data_query", "sub_query": question, "db_ids": [_DB]}
    with patch.object(sa, "get_db_client", return_value=_ctx(client)), \
         patch.object(sa, "get_cache_manager",
                      return_value=_cache_mgr(copy.deepcopy(schema), desc)), \
         patch.object(sa, "_load_manual_profile", return_value=copy.deepcopy(profile)), \
         patch.object(sub, "query_executor", _exec), \
         patch.object(sub, "result_organizer", _organize):
        result = await sub.run_data_query_pipeline(task, _isolated(question), llm=llm,
                                                   app_config=cfg)
    return result, calls


# ──────────────────────────────────────────────
# W6 잔여 — 실제 노드 경로 → `_pack_pipeline_result` → 벤치 수신기
# ──────────────────────────────────────────────


class TestTier2CaptureEndToEnd:
    async def test_llm_selection_reaches_bench_record(self, capture):
        llm = _RouterLLM(_picks("tcdmsif92", "tcdmsif94"))
        result, calls = await _run_tier2_single(llm, "가상 서버 IP 목록", cfg=_cfg())
        assert calls["exec"] == 1 and "error" not in result
        assert len(llm.select_prompts) == 1 and len(llm.sql_prompts) == 1
        assert _PURPOSE_HEADER in llm.sql_prompts[0], "용도 블록이 2단 SQL 프롬프트에 실린다"

        assert len(capture.records) == 1
        raw = capture.raw[0]
        assert raw["table_selection"][_DB]["source"] == "llm"
        assert raw["prompt_budget"]["stage"] == "within"
        entry = capture.records[0]["dbs"][_DB]
        assert entry["selection_source"] == "llm"
        assert entry["selected_count"] == len(raw["table_selection"][_DB]["selected"])
        assert isinstance(entry["prompt_tokens_est"], int) and entry["prompt_tokens_est"] > 0
        assert entry["budget_stage"] == "within"
        assert entry["stop_reason"] is None and entry["backend_reported_tokens"] is None
        # 스키마 칸은 선별된 테이블만(좁힌 스키마)
        assert set(entry["tables"]) == set(raw["table_selection"][_DB]["selected"])

    async def test_backend_token_limit_reaches_bench_record(self, capture):
        llm = _RouterLLM(_picks("tcdmsif92"), sql=FABRIX_TEXT)
        result, calls = await _run_tier2_single(llm, "가상 서버 IP 목록", cfg=_cfg())
        assert calls["exec"] == 0
        assert len(llm.sql_prompts) == 1, "백엔드 한도 보고 뒤 재생성 0회"
        assert result["regen_stop"]["reason"] == "backend_limit"
        entry = capture.records[-1]["dbs"][_DB]
        assert entry["stop_reason"] == "backend_limit"
        assert entry["backend_reported_tokens"] == 96858
        assert entry["budget_stage"] == "within"
        assert entry["selection_source"] == "llm" and entry["selected_count"] == 1
        assert "GptOssAdapter" not in repr(capture.records), "백엔드 원문은 레코드에 없다"

    async def test_prompt_budget_exceeded_reaches_bench_record(self, capture):
        llm = _RouterLLM(_picks("tcdmsif92"))
        result, calls = await _run_tier2_single(llm, "가상 서버 IP 목록", cfg=_cfg(budget=50))
        assert calls["exec"] == 0
        assert llm.sql_prompts == [], "전송 전 예산 초과 — SQL 생성 LLM 0회"
        assert result["regen_stop"]["reason"] == "backend_limit"
        entry = capture.records[-1]["dbs"][_DB]
        assert entry["budget_stage"] == "exceeded"
        assert entry["stop_reason"] == "backend_limit"
        assert isinstance(entry["prompt_tokens_est"], int) and entry["prompt_tokens_est"] > 50
        assert entry["backend_reported_tokens"] is None

    async def test_selection_none_reaches_bench_record(self, capture):
        _, profile, _ = _seed()
        llm = _RouterLLM(RuntimeError("selection down"))
        result, calls = await _run_tier2_single(llm, "zzqq", cfg=_cfg())
        assert calls["exec"] == 0 and llm.sql_prompts == []
        guidance = ts.selection_none_guidance(profile["table_definitions"])
        assert result["final_response"] == guidance
        entry = capture.records[-1]["dbs"][_DB]
        assert entry["selection_source"] == "none"
        assert entry["selected_count"] == 0
        assert entry["stop_reason"] == "selection_none"
        assert entry["tables"] == {}


# ──────────────────────────────────────────────
# 경로 대칭(D-066) — 단일 그래프 노드 · 2단 단일 루프 · 멀티 스키마 분석
# ──────────────────────────────────────────────


class TestThreeWaySymmetryOnSeed:
    async def test_same_selection_prompt_and_state_across_paths(self):
        schema, profile, desc = _seed()
        question = "가상 서버 IP 목록"
        picks = _picks("tcdmsif94", "tcdmsif89")  # 다리(tcdmsif92)가 붙는 조합

        # ① 단일 그래프 노드
        single_llm = _RouterLLM(picks)

        @asynccontextmanager
        async def _ctx(client):
            yield client

        client = AsyncMock()
        client.get_sample_data.return_value = []
        state = dict(create_initial_state(user_query=question))
        state["active_db_id"] = _DB
        state["parsed_requirements"] = {"original_query": question, "query_targets": []}
        with patch.object(sa, "get_db_client", return_value=_ctx(client)), \
             patch.object(sa, "get_cache_manager",
                          return_value=_cache_mgr(copy.deepcopy(schema), desc)), \
             patch.object(sa, "_load_manual_profile", return_value=copy.deepcopy(profile)):
            single = await sa.schema_analyzer(state, llm=single_llm, app_config=_cfg())

        # ② 2단 단일 루프(실제 schema_analyzer)
        tier2_llm = _RouterLLM(picks)
        await _run_tier2_single(tier2_llm, question, cfg=_cfg())

        # ③ 멀티 스키마 분석
        multi_llm = _RouterLLM(picks)
        sink: dict = {}
        mclient = AsyncMock()
        mclient.get_sample_data.return_value = []
        with patch("src.schema_cache.cache_manager.get_cache_manager",
                   return_value=_cache_mgr(copy.deepcopy(schema), desc)), \
             patch.object(sa, "_load_manual_profile", return_value=copy.deepcopy(profile)):
            multi = await mdb._analyze_schema(
                mclient, {"original_query": question}, db_id=_DB, app_config=_cfg(),
                sub_query_context=question, routing_intent=None, llm=multi_llm,
                selection_sink=sink,
            )

        assert single_llm.select_prompts == tier2_llm.select_prompts == multi_llm.select_prompts
        expected = {
            "mode": "definitions", "source": "llm", "candidates": 99,
            "selected": ["tcdmsif94", "tcdmsif89", "tcdmsif92"], "bridged": ["tcdmsif92"],
        }
        assert single["table_selection"][_DB] == expected
        assert sink[_DB] == expected
        assert list(single["schema_info"]["tables"]) == list(multi["tables"])
        assert single["relevant_tables"] == expected["selected"]


# ──────────────────────────────────────────────
# 멀티턴 요청 스코프 — 체크포인터 델타 병합
# ──────────────────────────────────────────────


class TestMultiTurnRequestScope:
    async def _two_turns(self, second_input: dict) -> list[dict]:
        seen: list[dict] = []

        async def _node(state: AgentState) -> dict:
            seen.append({
                "prompt_budget": state.get("prompt_budget"),
                "table_selection": state.get("table_selection"),
            })
            return {
                "prompt_budget": {"estimated_tokens": 123, "stage": "within"},
                "table_selection": {_DB: {"source": "none", "selected": []}},
            }

        g = StateGraph(AgentState)
        g.add_node("probe", _node)
        g.add_edge(START, "probe")
        g.add_edge("probe", END)
        app = g.compile(checkpointer=MemorySaver())
        conf = {"configurable": {"thread_id": "verify-138-turns"}}
        await app.ainvoke(create_initial_state(user_query="첫 질문"), conf)
        await app.ainvoke(second_input, conf)
        return seen

    async def test_followup_input_clears_previous_turn(self):
        seen = await self._two_turns(dict(create_followup_input("둘째 질문")))
        assert seen[0] == {"prompt_budget": None, "table_selection": None}
        assert seen[1] == {"prompt_budget": None, "table_selection": None}, (
            "직전 턴의 선별 0개 표지가 다음 턴으로 새지 않는다"
        )

    async def test_without_reset_previous_turn_would_leak(self):
        # 대조군 — 두 키를 빼면 체크포인터가 직전 값을 그대로 넘긴다(초기화가 필요한 이유)
        delta = dict(create_followup_input("둘째 질문"))
        delta.pop("prompt_budget")
        delta.pop("table_selection")
        seen = await self._two_turns(delta)
        assert seen[1]["table_selection"] == {_DB: {"source": "none", "selected": []}}


# ──────────────────────────────────────────────
# W6 잔여 — 멀티 DB(2단 핸들러 → multi_db_executor 실제 경로) → 벤치 수신기
# ──────────────────────────────────────────────

_PLAIN_SCHEMA = {
    "tables": {"servers": {"columns": [{"name": "hostname", "type": "varchar"}]}},
    "relationships": [],
}


class _Registry:
    def __init__(self, cfg: Any) -> None:
        pass

    def is_registered(self, db_id: str) -> bool:
        return True

    def get_client(self, db_id: str) -> Any:
        @asynccontextmanager
        async def _ctx():
            client = AsyncMock()
            client.get_sample_data.return_value = []
            client.execute_sql.return_value = SimpleNamespace(rows=[{"x": "a"}], row_count=1)
            yield client

        return _ctx()


class _MultiLLM(_RouterLLM):
    """SQL 생성은 프롬프트에 실린 스키마로 DB를 가른다."""

    async def ainvoke(self, messages: Any, **_kw: Any) -> Any:
        text = "\n".join(str(getattr(m, "content", m)) for m in messages)
        if _SELECT_MARK in text:
            self.select_prompts.append(text)
            if isinstance(self._select, Exception):
                raise self._select
            return SimpleNamespace(content=self._select)
        self.sql_prompts.append(text)
        sql = _SQL if "tcdmsif92" in text else "SELECT hostname FROM servers LIMIT 10"
        return SimpleNamespace(content=sql)


async def _run_tier2_multi(llm: _MultiLLM, question: str) -> dict:
    schema, profile, desc = _seed()
    mgr = AsyncMock()
    mgr.get_schema_or_fetch.side_effect = lambda _c, db_id: (
        copy.deepcopy(schema if db_id == _DB else _PLAIN_SCHEMA), True, "메모리", {}, {},
    )
    mgr.get_db_description.side_effect = lambda db_id: desc if db_id == _DB else None
    mgr.get_synonyms.return_value = {}
    mgr.get_applied_structure_meta.return_value = None
    mgr.get_descriptions.return_value = {}
    mgr.redis_available = False

    async def _organize(state, llm=None, app_config=None):
        return {"organized_data": {"rows": [], "summary": "ok"}, "error_message": None}

    task = {"task_id": "t1", "agent": "data_query", "sub_query": question,
            "db_ids": [_DB, "plain_db"]}
    with patch.object(mdb, "DBRegistry", _Registry), \
         patch("src.schema_cache.cache_manager.get_cache_manager", return_value=mgr), \
         patch.object(sa, "_load_manual_profile",
                      side_effect=lambda db_id: copy.deepcopy(profile) if db_id == _DB else None), \
         patch.object(mdb, "log_query_execution", AsyncMock()), \
         patch.object(sub, "result_organizer", _organize):
        return await sub.run_data_query_pipeline(task, _isolated(question), llm=llm,
                                                 app_config=_cfg())


class TestTier2MultiCaptureEndToEnd:
    async def test_multi_selection_reaches_bench_record(self, capture):
        llm = _MultiLLM(_picks("tcdmsif92"))
        await _run_tier2_multi(llm, "가상 서버 IP 목록")
        assert len(llm.select_prompts) == 1, "정의가 있는 DB만 선별 LLM을 부른다"
        itam_sql = [p for p in llm.sql_prompts if "tcdmsif92" in p]
        assert itam_sql and _PURPOSE_HEADER in itam_sql[0], "멀티 SQL 프롬프트에도 용도 블록"
        dbs = capture.records[-1]["dbs"]
        assert dbs[_DB]["selection_source"] == "llm" and dbs[_DB]["selected_count"] == 1
        assert dbs[_DB]["stop_reason"] is None
        assert set(dbs[_DB]["tables"]) == {"tcdmsif92"}
        assert dbs["plain_db"]["selection_source"] is None

    async def test_multi_selection_none_reaches_bench_record(self, capture):
        llm = _MultiLLM(RuntimeError("selection down"))
        await _run_tier2_multi(llm, "zzqq")
        assert not [p for p in llm.sql_prompts if "tcdmsif" in p], "선별 0개 DB는 SQL 생성 0회"
        dbs = capture.records[-1]["dbs"]
        assert dbs[_DB]["selection_source"] == "none"
        assert dbs[_DB]["selected_count"] == 0
        assert dbs[_DB]["stop_reason"] == "selection_none"
