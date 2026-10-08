"""plans/147 W2 · D-322 — 2단 경로(분해 → 오케스트레이터 → 재계획기 → 집계기)에서의 소스 사다리.

고정하는 계약(가짜 LLM · 모의 게이트웨이 세션 · 폴스타 처리기 대역):
  1. 폴스타 + 제니퍼(지목 없음) 복합 — 제니퍼 task만 조회 0 · 되묻기, 폴스타 task는 진행해 답에
     실린다.
     되묻기 task는 재계획 대상이 아니다(평가 LLM을 부르지 않는다 · 대체 task 0).
  2. 집계기 — 되묻기 페이로드를 응답 칸 `apm_source_clarification`으로, 대기 `apm_source_pending`
     (`{query, choices}`)을 스레드 칸으로 올린다. 조회한 턴은 `apm_source_scope`
     (`{ids, basis, last_target_sources}`)를 올린다.
  3. AC-8 — 「김포 서버 CPU」는 소스 단어가 있어도 APM task·소스 힌트를 만들지 않는다(D-293).
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.orchestration import subagents
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")
pytestmark = pytest.mark.apm_source_ladder

POLESTAR_ROWS = [{"hostname": "web01", "cpu_usage_pct": 42.5}]


def _config() -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


class _Gateway:
    """모의 게이트웨이 — 실은 `source_ids`마다 행 1개."""

    def __init__(self) -> None:
        self.opened = 0
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        picked = arguments.get("source_ids") or ["bank", "common", "legacy"]
        env = {"rows": [{"source_id": s, "instance_id": 11, "instance_name": f"{s}-was1",
                         "tps": 3.5, "avg_response_ms": 812} for s in picked],
               "row_count": len(picked), "queried_at": "2026-10-08T10:00:00+09:00",
               "source_kind": "apm_api", "source": "apm", "tool": name, "limits": [],
               "sources": [{"source_id": s, "status": "ok", "reason": ""} for s in picked]}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)


async def _polestar_handler(task, isolated, *, llm, app_config):
    return {
        "organized_data": {"summary": "1건", "rows": POLESTAR_ROWS, "column_mapping": None,
                           "resolved_mapping": None, "is_sufficient": True,
                           "sheet_mappings": None},
        "query_results": POLESTAR_ROWS, "target_db_ids": ["polestar_cm_gp"],
    }


@pytest.fixture
def gateway(monkeypatch) -> _Gateway:
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=_polestar_handler))
    return gw


class _MustNotCall:
    """재계획 평가 LLM — 부르면 실패(되묻기 task는 재계획 대상이 아니다)."""

    async def ainvoke(self, *a: Any, **k: Any) -> Any:
        raise AssertionError("재계획 평가 LLM이 불렸다")


async def _turn(query: str, apm_sub: str, **state_extra: Any) -> dict:
    plan_json = {"tasks": [
        {"task_id": "t1", "agent": "data_query", "sub_query": "web01 서버 CPU 사용률"},
        {"task_id": "t2", "agent": "apm_query", "sub_query": apm_sub,
         "views": ["apm.app_health"]},
    ]}
    config = _config()
    plan = await ip._llm_decompose(FakeListChatModel(responses=[json.dumps(plan_json)]), query,
                                   config)
    state = create_initial_state(user_query=query, thread_id="th-147p", user_id="alice")
    state.update({"parsed_requirements": {
        "query_targets": ["서버", "CPU"], "original_query": query,
        "filter_conditions": [{"field": "hostname", "op": "=", "value": "web01"}]},
        "task_plan": plan["tasks"], "task_results": {}, "replan_count": 0, **state_extra})
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                          app_config=config))
    replan = await replanner(state, llm=_MustNotCall(), app_config=config)
    state.update(replan)
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."]),
                                  app_config=config, synthesize=True, composite_answer="steps")
    return {"state": state, "out": out, "replan": replan}


@pytest.mark.asyncio
async def test_composite_asks_apm_source_while_polestar_answers(gateway) -> None:
    query = "web01 서버 CPU 사용률과 WAS 응답시간 알려줘"
    turn = await _turn(query, "web01 WAS 응답시간")
    assert gateway.opened == 0, "제니퍼 task는 조회 0"
    assert turn["replan"]["needs_replan"] is False
    assert [t["task_id"] for t in turn["state"]["task_plan"]] == ["t1", "t2"], "대체 task 0"
    out = turn["out"]
    ask = out["apm_source_clarification"]
    assert [o["key"] for o in ask["options"]] == ["bank", "common", "legacy", "*"]
    assert ask["multi"] is True and ask["allow_all"] is True
    assert out["apm_source_pending"] == {"query": query,
                                         "choices": ["bank", "common", "legacy", "*"]}
    assert "42.5" in out["final_response"], "폴스타 부분은 정상 답"
    assert ask["question"] in out["final_response"], "되묻기 문구가 답에 남는다"
    assert out.get("apm_source_scope") is None
    assert out.get("zone_clarification") is None


@pytest.mark.asyncio
async def test_composite_with_location_word_queries_common_and_records_scope(gateway) -> None:
    query = "김포 web01 서버 CPU 사용률과 WAS 응답시간 알려줘"
    turn = await _turn(query, "web01 WAS 응답시간")
    assert {tuple(a.get("source_ids") or ()) for _, a in gateway.calls} == {("common",)}
    out = turn["out"]
    assert out.get("apm_source_clarification") is None and out.get("apm_source_pending") is None
    assert out["apm_source_scope"] == {"ids": ["common"], "basis": "named",
                                       "last_target_sources": ["common"]}
    assert "common-was1" in out["final_response"] and "42.5" in out["final_response"]
    assert "공동존 제니퍼만 조회했습니다(근거: 「김포」)" in out["final_response"]


@pytest.mark.asyncio
async def test_answer_turn_scope_basis_answered(gateway) -> None:
    query = "web01 서버 CPU 사용률과 WAS 응답시간 알려줘"
    turn = await _turn(query, "web01 WAS 응답시간", selected_apm_source_ids=["bank", "legacy"],
                       apm_source_basis="answered")
    assert {tuple(a["source_ids"]) for _, a in gateway.calls} == {("bank", "legacy")}
    assert turn["out"]["apm_source_scope"] == {
        "ids": ["bank", "legacy"], "basis": "answered", "last_target_sources": ["bank", "legacy"]}


# ── AC-8 ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["김포 서버 CPU 사용률", "레거시 서버 CPU 사용률",
                                   "은행존 서버 CPU 사용률"])
async def test_source_words_do_not_create_apm_task_or_source_hint(query) -> None:
    from src.nodes.input_parser import _ensure_source_hints
    from src.routing.registry import get_registry

    payload = {"tasks": [{"task_id": "t1", "agent": "data_query", "sub_query": query}]}
    plan = await ip._llm_decompose(FakeListChatModel(responses=[json.dumps(payload)]), query,
                                   _config())
    assert [t["agent"] for t in plan["tasks"]] == ["data_query"], "APM task가 생기지 않는다"
    hints = _ensure_source_hints({"target_db_hints": []}, query)["target_db_hints"]
    apm_aliases = set(get_registry().source_alias_terms()) & {
        t for s in get_registry().sources_of("apm") for t in s.terms}
    assert not apm_aliases, "소스 단어는 시스템 유사어가 아니다(D-293)"
    assert not [h for h in hints if h in {"제니퍼", "APM"}]
