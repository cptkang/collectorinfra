"""plans/127 — 문서 채팅 라우팅을 켠 배포의 2단 경로 끝까지(D-286).

125 `test_plan125_apm_active_path`와 같은 형식이다.

분해(`_llm_decompose` — 대역 LLM이 JSON 계획을 낸다) → 오케스트레이터(`doc_query` 실제 처리기 ·
폴스타 `data_query`는 대역 처리기) → 재계획기 → 집계기(2단 배선 그대로 `synthesize=True` ·
`composite_answer="steps"`) → 최종 응답. 문서 엔진(`answer_from_documents`)은 대역이다
(실 검색·LLM 0).

네 경우:
  1. 문서만 묻는 턴 — 참고 문서가 응답에 실리고 재계획 평가 LLM을 부르지 않는다(G-12).
  2. 문서 + 폴스타 — 둘 다 답하고, 분해가 건 문서 task 의존은 끊긴다(간선 없음 · G-10).
  3. 소스 권한 없는 사용자 — 엔진을 부르지 않고 소스 이름 없는 거부 문구 · 폴스타 부분은 정상 답.
  4. 라우팅 스위치 off(기본) — 분해 어휘에 `doc_query`가 없다(목록 밖 이름은 종전 폴백).
"""

from __future__ import annotations

import importlib
import json
from dataclasses import replace

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig, RagConfig
from src.doc_qa.service import DocAnswer
from src.orchestration import doc_query as dq
from src.orchestration import subagents
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.routing.db_authz import SOURCE_ACCESS_DENIED_MESSAGE
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")

DOC_ONLY = "계정 신청은 누가 승인하나요?"
MIXED = "계정 신청 승인 규정과 web01 서버 CPU 사용률 알려줘"
POLESTAR_ROWS = [{"hostname": "web01", "cpu_usage_pct": 42.5}]
DOC_ANSWER = "부서장이 승인합니다.\n\n---\n**참고 문서**\n1. 규정 문서 — `계정 신청 절차`"


def _config(*, routing: bool) -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db"),
        rag=RagConfig(_env_file=None, enabled=True, chat_routing_enabled=routing),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


class _Engine:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, query, collection_ids, **kwargs):
        self.calls.append({"query": query, "collections": list(collection_ids), **kwargs})
        return DocAnswer(DOC_ANSWER, citations=[object()], status="ok")


async def _polestar_handler(task, isolated, *, llm, app_config):
    """폴스타 SQL 조회 대역 — 이 테스트의 관심은 문서 경로와 집계다."""
    return {
        "organized_data": {"summary": f"{len(POLESTAR_ROWS)}건", "rows": POLESTAR_ROWS,
                           "column_mapping": None, "resolved_mapping": None,
                           "is_sufficient": True, "sheet_mappings": None},
        "query_results": POLESTAR_ROWS,
        "target_db_ids": ["polestar_cm_gp"],
    }


@pytest.fixture
def engine(monkeypatch) -> _Engine:
    fake = _Engine()
    monkeypatch.setattr(dq, "answer_from_documents", fake)
    # 서술 LLM(answer 프로파일 · plans/138 W5)은 주입 LLM으로 대역 — 실 LLM 생성 0
    monkeypatch.setattr(dq, "_answer_llm", lambda app_config, *, fallback: fallback)
    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=_polestar_handler))
    return fake


def _llm(*responses: str) -> FakeListChatModel:
    return FakeListChatModel(responses=list(responses))


class _NoCallLLM(FakeListChatModel):
    """부르면 실패하는 LLM — 재계획 평가 생략(G-12)을 단언한다."""

    async def ainvoke(self, *args, **kwargs):  # noqa: D102
        raise AssertionError("문서 전용 계획은 재계획 평가 LLM 을 부르지 않는다")


async def _turn(config: AppConfig, query: str, plan: dict, *, replan_llm=None, **user) -> dict:
    """분해 → 실행 → 재계획 판정 → 집계 한 턴."""
    decomposed = await ip._llm_decompose(_llm(json.dumps(plan, ensure_ascii=False)), query, config)
    state = create_initial_state(user_query=query, thread_id="th-1", **user)
    parsed = {"query_targets": ["서버", "CPU"], "filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "original_query": query}
    state.update({"parsed_requirements": parsed, "task_plan": decomposed["tasks"],
                  "task_results": {}, "replan_count": 0})
    state.update(await agent_orchestrator(state, llm=_llm("unused"), app_config=config))
    no_followup = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state.update(await replanner(state, llm=replan_llm or _llm(no_followup), app_config=config))
    out = await result_aggregator(state, llm=_llm("요약입니다."), app_config=config,
                                  synthesize=True, composite_answer="steps")
    return {"plan": decomposed, "state": state, "out": out}


# ─── 1. 문서만 묻는 턴 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(("allowed", "role"), [(None, "user"), (["doc"], "user"), ([], "admin")])
async def test_doc_only_turn_answers_with_citations_without_replan_llm(engine, allowed, role):
    plan = {"tasks": [{"task_id": "t1", "agent": "doc_query", "sub_query": DOC_ONLY,
                       "views": ["doc.hq_manual"]}]}
    turn = await _turn(_config(routing=True), DOC_ONLY, plan, replan_llm=_NoCallLLM(responses=[]),
                       user_role=role, allowed_sources=allowed)

    assert [t["agent"] for t in turn["plan"]["tasks"]] == ["doc_query"]
    assert turn["plan"]["tasks"][0]["views"] == ["doc.hq_manual"]
    assert engine.calls and engine.calls[0]["collections"] == ["hq_manual"]
    assert engine.calls[0]["audit_context"]["source"] == "chat"
    answer = turn["out"]["final_response"]
    assert "부서장이 승인합니다" in answer and "**참고 문서**" in answer
    assert turn["state"]["needs_replan"] is False


# ─── 2. 문서 + 폴스타 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mixed_turn_answers_both_and_cuts_doc_dependency(engine):
    plan = {"tasks": [
        {"task_id": "t1", "agent": "data_query", "sub_query": "web01 서버 CPU 사용률"},
        {"task_id": "t2", "agent": "doc_query", "sub_query": "계정 신청 승인 규정",
         "views": [], "depends_on": ["t1"], "input_from": ["t1"]},
    ]}
    turn = await _turn(_config(routing=True), MIXED, plan, user_role="user",
                       allowed_sources=None)

    doc_task = turn["plan"]["tasks"][1]
    assert doc_task["depends_on"] == [] and doc_task["input_from"] == []
    assert [d["reason"] for d in turn["plan"]["degraded"]] == ["doc_task_dependency_cut"]
    assert engine.calls[0]["collections"] == ["hq_manual", "arch_docs"], "보기 없음 = 전체(G-6)"
    answer = turn["out"]["final_response"]
    assert "42.5" in answer and "부서장이 승인합니다" in answer
    assert "문서군을 지정하지 않아 등록 문서군 전체" in answer


# ─── 3. 소스 권한 없는 사용자 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unauthorized_user_gets_denial_and_polestar_answer(engine):
    plan = {"tasks": [
        {"task_id": "t1", "agent": "data_query", "sub_query": "web01 서버 CPU 사용률"},
        {"task_id": "t2", "agent": "doc_query", "sub_query": "계정 신청 승인 규정",
         "views": ["doc.hq_manual"]},
    ]}
    turn = await _turn(_config(routing=True), MIXED, plan, user_role="user",
                       allowed_sources=["apm"])

    assert engine.calls == [], "권한 밖이면 문서 엔진을 부르지 않는다"
    answer = turn["out"]["final_response"]
    assert SOURCE_ACCESS_DENIED_MESSAGE in answer
    assert "42.5" in answer, "같은 질의의 폴스타 부분은 정상 답이다"
    for leak in ("사내 문서", "참고 문서", "doc.hq_manual"):
        assert leak not in answer, f"권한 밖 소스를 드러냈다: {leak}"
    assert turn["state"]["task_results"]["t2"].get("routing_intent") == "access_denied"


# ─── 4. 라우팅 스위치 off(기본) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_switch_off_has_no_doc_path(engine):
    config = _config(routing=False)
    assert subagents.resolve_subagent("doc_query", config) is None
    assert "doc_query" not in ip._planner_system_prompt(config)
    plan = {"tasks": [{"task_id": "t1", "agent": "doc_query", "sub_query": DOC_ONLY}]}
    turn = await _turn(config, DOC_ONLY, plan, user_role="user", allowed_sources=None)
    assert engine.calls == []
    assert [t["agent"] for t in turn["plan"]["tasks"]] == ["general_inference"], \
        "스위치 off 배포에서 목록 밖 담당 이름은 종전 폴백 규칙을 따른다"
