"""plans/147 W2 · D-322 — 질의 라우트 · 체크포인터 멀티턴 · 응답 칸 운반.

그래프: `StateGraph(AgentState)` + `MemorySaver` — 분해 대역 노드(결정적 · 질의 원문을 APM
task 1개로) → 실제 `agent_orchestrator`(실제 `apm_query` 처리기 · 모의 게이트웨이 세션) → 실제
`result_aggregator`.
체크포인터 병합(델타만)·요청 스코프 초기화·스레드 칸 승계를 실물로 잰다. 실 LLM·네트워크 0.

고정하는 계약:
  1. 3턴 — 지목 없음 → 되묻기(`apm_source_clarification` · 대기 `apm_source_pending`) → 글 답
     「공동존」
     (질의를 원 질문으로 바꿔 공동존만 · 대화 기록에는 사용자 원문) → 후속 「힙 사용률은?」(승계).
  2. 버튼 답(`selected_apm_source_ids`) — 근거 `answered` · 여러 개 · 「전체」(`*`).
  3. 글 답 「은행존」(두 소스) → 그 두 소스로 다시 되묻기 · 새 질문 글은 답으로 보지 않는다.
  4. × 해제(`reset_db_scope`) — 승계가 지워져 다시 되묻는다.
  5. 운반 — JSON(`/query`) · SSE `done` · 스트림 폴백(`ainvoke`) 모두 같은 칸 · 값 없으면 SSE에
     키 없음. SSE 되묻기·승계 턴의 스레드 칸이 체크포인트에 남는다(종료 노드 쓰기).
  6. 요청 경계 — 화면 선택 id는 레지스트리 ∩ 사용 가능 소스 · `*` 허용 · `apm` 권한 없으면 버린다.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.api.dependencies import require_user
from src.orchestration import apm_query as aq
from src.routing import apm_source_select as sel

pytestmark = pytest.mark.apm_source_ladder

ALL3 = ["bank", "common", "legacy"]
USER: dict[str, Any] = {"sub": "u1", "role": "user"}


class _Gateway:
    """모의 게이트웨이 — 실은 `source_ids`(없으면 전 소스)마다 행 1개."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        picked = arguments.get("source_ids") or ALL3
        env = {"rows": [{"source_id": s, "instance_id": 11, "instance_name": f"{s}-was1",
                         "tps": 3.5} for s in picked],
               "row_count": len(picked), "queried_at": "2026-10-08T10:00:00+09:00",
               "source_kind": "apm_api", "source": "apm", "tool": name, "limits": [],
               "sources": [{"source_id": s, "status": "ok", "reason": ""} for s in picked]}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(env))], isError=False)

    def take(self) -> list[Any]:
        sent = [a.get("source_ids") for _, a in self.calls]
        self.calls.clear()
        return sent


class _NoAstream:
    """실제 그래프의 `astream_events`만 가린다 — 스트림 라우트가 `ainvoke` 폴백으로 간다."""

    def __init__(self, compiled: Any) -> None:
        self._compiled = compiled

    def __getattr__(self, name: str) -> Any:
        if name == "astream_events":
            raise AttributeError(name)
        return getattr(self._compiled, name)


def _config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, DBHubConfig, ServerConfig

    cfg = AppConfig(
        _env_file=None, db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
        dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
    )
    cfg.server.sse_progress_events = True
    cfg.server.sse_heartbeat_interval_sec = 5
    return cfg


def _graph(cfg):
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import AgentState

    async def planner(state):
        """분해 대역 — 이번 턴 질의를 APM task 1개로(서버 web01)."""
        query = state["user_query"]
        return {"task_plan": [{"task_id": "t1", "agent": "apm_query", "sub_query": query,
                               "views": ["apm.app_health"], "status": "pending",
                               "depends_on": []}],
                "task_results": {}, "replan_count": 0,
                "parsed_requirements": {"original_query": query, "filter_conditions": [
                    {"field": "hostname", "op": "=", "value": "web01"}]}}

    async def orchestrate(state):
        return await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                        app_config=cfg)

    async def aggregate(state):
        return await result_aggregator(state, llm=FakeListChatModel(responses=["요약"]),
                                       app_config=cfg)

    builder = StateGraph(AgentState)
    builder.add_node("intent_planner", planner)
    builder.add_node("agent_orchestrator", orchestrate)
    builder.add_node("result_aggregator", aggregate)
    builder.set_entry_point("intent_planner")
    builder.add_edge("intent_planner", "agent_orchestrator")
    builder.add_edge("agent_orchestrator", "result_aggregator")
    builder.add_edge("result_aggregator", END)
    return builder.compile(checkpointer=MemorySaver())


@pytest.fixture
def env(monkeypatch):
    from src.api.routes import query as query_routes
    from src.infrastructure import apm_job_store as store_mod
    from src.infrastructure.apm_job_store import ApmJobStore

    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    cfg = _config()
    compiled = _graph(cfg)

    def client(*, fallback: bool = False, user: dict | None = None) -> TestClient:
        app = FastAPI()
        app.state.config = cfg
        app.state.graph = _NoAstream(compiled) if fallback else compiled
        app.include_router(query_routes.router, prefix="/api/v1")
        app.dependency_overrides[require_user] = lambda: dict(user or USER)
        return TestClient(app)

    return SimpleNamespace(gw=gw, graph=compiled, client=client)


def _post(client: TestClient, route: str, query: str, thread: str, **extra: Any) -> dict:
    body = {"query": query, "thread_id": thread, **extra}
    if route == "json":
        r = client.post("/api/v1/query", json=body)
        assert r.status_code == 200, r.text
        return r.json()
    r = client.post("/api/v1/query/stream", json=body)
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    (done,) = [e for e in events if e["type"] == "done"]
    return done


def _thread_values(env, thread: str) -> dict:
    return env.graph.get_state({"configurable": {"thread_id": thread}}).values


ROUTES = ("json", "sse")


# ── 1. 3턴 ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("route", ROUTES)
def test_three_turns_ask_text_answer_followup(env, route) -> None:
    client, thread = env.client(), f"p147-3t-{route}"
    q1 = "WAS 응답시간 알려줘"
    out = _post(client, route, q1, thread)
    assert env.gw.take() == [], "1턴 — 조회 0"
    ask = out["apm_source_clarification"]
    assert [o["key"] for o in ask["options"]] == [*ALL3, "*"] and ask["multi"] is True
    assert ask["question"] in out["response"]
    assert not out.get("apm_source_scope")
    assert _thread_values(env, thread)["apm_source_pending"] == {
        "query": q1, "choices": [*ALL3, "*"]}

    out = _post(client, route, "공동존", thread)
    assert env.gw.take() == [["common"]], "2턴 글 답 — 원 질문을 공동존만"
    assert not out.get("apm_source_clarification")
    assert out["apm_source_scope"] == {"ids": ["common"], "basis": "answered",
                                       "last_target_sources": ["common"]}
    assert "공동존 제니퍼만 조회했습니다(되묻기 답)" in out["response"]
    values = _thread_values(env, thread)
    assert values["apm_source_pending"] is None, "대기는 답 턴에 소비된다"
    assert values["user_query"] == q1
    humans = [m.content for m in values["messages"] if m.type == "human"]
    assert humans[-1] == "공동존", "대화 기록에는 사용자 원문"

    out = _post(client, route, "힙 사용률은?", thread)
    assert env.gw.take() == [["common"]], "3턴 — 승계"
    assert "공동존 제니퍼만 조회했습니다(이전 선택 승계)" in out["response"]
    assert out["apm_source_scope"]["ids"] == ["common"]


def test_button_answer_and_all(env) -> None:
    client, thread = env.client(), "p147-btn"
    q1 = "WAS 응답시간 알려줘"
    _post(client, "json", q1, thread)
    out = _post(client, "json", q1, thread, selected_apm_source_ids=["bank", "legacy"])
    assert env.gw.take() == [["bank", "legacy"]]
    assert out["apm_source_scope"]["basis"] == "answered"
    out = _post(client, "json", "레거시는?", thread)
    assert env.gw.take() == [["legacy"]], "승계 중 새 단어가 이긴다"
    assert out["apm_source_scope"] == {"ids": ["legacy"], "basis": "named",
                                       "last_target_sources": ["legacy"]}
    out = _post(client, "json", "WAS 응답시간 알려줘", thread, selected_apm_source_ids=["*"])
    assert env.gw.take() == [ALL3]
    assert out["apm_source_scope"] == {"ids": ["*"], "basis": "all",
                                       "last_target_sources": ALL3}, "「전체」는 근거 all"


def test_first_turn_screen_selection(env) -> None:
    out = _post(env.client(), "json", "WAS 응답시간 알려줘", "p147-first",
                selected_apm_source_ids=["common"])
    assert env.gw.take() == [["common"]]
    assert out["apm_source_scope"]["basis"] == "selected"


# ── 3. 글 답 판정 ────────────────────────────────────────────────────────────

def test_shared_word_text_answer_reasks_with_matching_sources(env) -> None:
    client, thread = env.client(), "p147-reask"
    q1 = "WAS 응답시간 알려줘"
    _post(client, "json", q1, thread)
    out = _post(client, "json", "은행존이요", thread)
    assert env.gw.take() == []
    ask = out["apm_source_clarification"]
    assert [o["key"] for o in ask["options"]] == ["bank", "legacy"]
    assert ask["original_query"] == q1
    assert _thread_values(env, thread)["apm_source_pending"]["query"] == q1, "원 질문 유지"
    out = _post(client, "json", "레거시로 해 주세요", thread)
    assert env.gw.take() == [["legacy"]]
    assert _thread_values(env, thread)["user_query"] == q1


def test_new_question_is_not_an_answer(env) -> None:
    client, thread = env.client(), "p147-newq"
    _post(client, "json", "WAS 응답시간 알려줘", thread)
    out = _post(client, "json", "공동존 web01 힙 사용률 추이 보여줘", thread)
    assert env.gw.take() == [["common"]], "새 질문 — 단어 지목(named)으로 처리"
    assert out["apm_source_scope"]["basis"] == "named"
    assert _thread_values(env, thread)["user_query"] == "공동존 web01 힙 사용률 추이 보여줘"


# ── 4. × 해제 ────────────────────────────────────────────────────────────────

def test_reset_db_scope_clears_inherited_choice(env) -> None:
    client, thread = env.client(), "p147-reset"
    _post(client, "json", "김포 WAS 응답시간", thread)
    assert env.gw.take() == [["common"]]
    out = _post(client, "json", "힙 사용률은?", thread, reset_db_scope=True)
    assert env.gw.take() == [], "승계가 지워져 다시 되묻는다"
    assert out["apm_source_clarification"]["options"][-1]["key"] == "*"
    assert _thread_values(env, thread)["apm_source_scope"] is None


# ── 5. 운반 ──────────────────────────────────────────────────────────────────

def test_stream_fallback_carries_fields(env) -> None:
    client, thread = env.client(fallback=True), "p147-fb"
    out = _post(client, "sse", "WAS 응답시간 알려줘", thread)
    assert [o["key"] for o in out["apm_source_clarification"]["options"]] == [*ALL3, "*"]
    out = _post(client, "sse", "전체", thread)
    assert env.gw.take() == [ALL3]
    assert out["apm_source_scope"] == {"ids": ["*"], "basis": "all",
                                       "last_target_sources": ALL3}
    assert "apm_source_clarification" not in out, "값이 없으면 SSE에 키를 싣지 않는다"


def test_response_model_declares_fields() -> None:
    from src.api.schemas import QueryRequest, QueryResponse

    assert "selected_apm_source_ids" in QueryRequest.model_fields
    for name in ("apm_source_clarification", "apm_source_scope"):
        assert name in QueryResponse.model_fields


# ── 6. 요청 경계 ─────────────────────────────────────────────────────────────

def test_selection_is_filtered_by_registry_available_and_permission() -> None:
    from src.api.routes.query import apply_apm_source_selection_authorization as authz

    assert authz(["common", "zzz", "*", "common"], USER) == ["common", "*"]
    assert authz(["zzz"], USER) is None
    assert authz(["common"], {"sub": "u", "role": "user", "allowed_sources": []}) is None
    sel.set_available_apm_sources({"bank"})
    assert authz(["common", "bank"], USER) == ["bank"], "미연결 소스는 고를 수 없다"


@pytest.mark.parametrize(("text", "expected"), [
    ("공동존", (["common"], None)), ("김포로요", (["common"], None)),
    ("레거시 제니퍼로 조회해 주세요", (["legacy"], None)), ("전체", (["*"], None)),
    ("전체 조회해 줘", (["*"], None)), ("은행존", (None, "은행존")),
    ("공동존과 레거시", (None, "공동존과 레거시")),
    ("공동존 CPU 상위 10대", None), ("운영체제", None), ("응답시간은?", None), ("", None),
])
def test_text_answer_rule(text, expected) -> None:
    from src.api.routes.query import _apm_source_text_answer

    pending = {"query": "WAS 응답시간", "choices": [*ALL3, "*"]}
    assert _apm_source_text_answer(text, pending, USER) == expected
    assert _apm_source_text_answer(text, None, USER) is None, "대기가 없으면 답이 아니다"


def test_selection_caps_count_and_item_length_and_logs_preview(caplog) -> None:
    """심층 방어 — 상한을 넘는 개수·긴 항목은 거부(422)가 아니라 버린다 · 로그는 개수와 앞 몇
    개(잘린 값)만."""
    import logging

    from src.api.routes.query import apply_apm_source_selection_authorization as authz

    flood = [f"x{i}" for i in range(500)] + ["common"]
    assert authz(flood, USER) is None, "상한(32) 밖의 유효 id는 보지 않는다"
    assert authz(["common", "z" * 65 + "common"], USER) == ["common"]
    with caplog.at_level(logging.INFO, logger="src.api.routes.query"):
        authz(["A" * 5000, *flood], USER)
    (line,) = [r.getMessage() for r in caplog.records if "제니퍼 소스 선택" in r.getMessage()]
    assert "502건" in line and "A" * 21 not in line and "x5" not in line
