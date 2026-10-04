"""plans/134 W0-B 본체 — APM 장기 작업의 수명(접수·처리 마감 안 재확인·결과 파일·부분 결과)과 고지.

고정하는 계약(SPEC-apm-question-coverage §3.8 · §7.5):
  1. 데이터 도구 인자에 `owner = "user:<sub>"`·`wait_seconds`가 실린다 — `wait_seconds`는 호출 상한
     (`source_call_timeout`)보다 항상 짧다. 묶이지 않은 컨텍스트는 상한 − 2이고, 조회 마감이
     가까우면 남은 시간이다.
  2. 작업 핸들(승격) → 장부 등록 → 조회 마감 안에 끝나면 미리보기 행으로 종전처럼 답한다.
  3. 마감까지 못 끝나면 **접수 답**: 데이터 답이 아니다(`source_status.status = "accepted"` ·
     행 없음 · 고지 `apm_job_accepted` + `ref.apm_job_id`) · 재계획이 같은 조회를 다시 접수하지
     않는다.
  4. 동기 봉투의 `artifact`(인라인 < 전체) → 장부 + `apm_full_result_file` · `partial` →
     `apm_partial_sources` · 실패 작업은 실패로 · 첫 홉 작업이 못 끝나면 취소.
  5. `ref`가 처리기 → 집계기 → 라우트(스트림·비스트림)까지 보존된다 · 고지 kind 표(SPEC §7.5).
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0).
"""

from __future__ import annotations

import importlib
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs as jobs
from src.orchestration import apm_query as aq
from src.orchestration import subagents
from src.orchestration.investigation_audit import _apm_query_fields
from src.orchestration.replanner import _terminal_source_task_ids
from src.utils.deadline import bind_request_deadline, unbind_request_deadline

ra = importlib.import_module("src.orchestration.result_aggregator")

JOB = "0123456789abcdef0123456789abcdef"
JOB2 = "fedcba9876543210fedcba9876543210"


def _cfg(*, max_targets: int = 10, timeout: float = 10.0) -> SimpleNamespace:
    dbhub = DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=timeout)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=max_targets, fanout_concurrency=2,
                                  audit_enabled=False, task_frame_enabled=False,
                                  plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _handle(state: str, job_id: str = JOB, *, done: int = 2, total: int | None = 61,
            seconds: float | None = 12.2, error: dict | None = None) -> dict:
    handle = {"job_id": job_id, "state": state,
              "progress": {"done": done, "total": total, "unit": "api_calls",
                           "label": "API 호출"},
              "estimate": {"api_calls": total, "seconds": seconds},
              "created_at": "2026-10-02T10:00:00+09:00",
              "updated_at": "2026-10-02T10:00:07+09:00", "expires_at": None}
    if error:
        handle["error"] = error
    return handle


def _accepted(tool: str, job_id: str = JOB) -> dict:
    """접수 봉투(SPEC-apm-gateway §3.1) — 행 없음 · 핸들만."""
    return _env(tool, [], job=_handle("running", job_id))


def _done_status(tool: str, rows: list[dict], *, total: int | None = None, job_id: str = JOB,
                 state: str = "completed", **meta: Any) -> dict:
    """끝난 작업의 `apm_job_status` 봉투 — 미리보기 행 + `result_meta` + `artifact`."""
    total = len(rows) if total is None else total
    result_meta = {"tool": tool, "queried_at": "2026-10-02T10:01:00+09:00",
                   "source_kind": "apm_api", "source": "jennifer", "limits": [],
                   "total_row_count": total, "window": {"minutes": 60}, **meta}
    return _env("apm_job_status", rows, job=_handle(state, job_id, done=61),
                result_meta=result_meta, total_row_count=total,
                artifact={"job_id": job_id, "total_rows": total, "chunk_rows": 2000,
                          "chunks": [], "columns": ["instance_id"], "text_parts": []},
                partial=state == "partial")


class _Gateway:
    """모의 게이트웨이 세션 — 데이터 도구 응답 · 작업 상태 순서(마지막 값 반복) · 호출 기록."""

    def __init__(self, replies: dict, statuses: dict[str, list[dict]] | None = None) -> None:
        self.replies = replies
        self.statuses = {k: list(v) for k, v in (statuses or {}).items()}
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == "apm_job_status":
            queue = self.statuses[arguments["job_id"]]
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
        elif name == "apm_job_cancel":
            reply = _env("apm_job_cancel", [], job=_handle("cancelled", arguments["job_id"]))
        else:
            reply = self.replies[name]
            if callable(reply):
                reply = reply(arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [args for name, args in self.calls if name == tool]


@pytest.fixture
def store(monkeypatch) -> ApmJobStore:
    ledger = ApmJobStore(None)
    monkeypatch.setattr(store_mod, "_STORE", ledger)
    return ledger


@pytest.fixture
def gateway(monkeypatch, store):
    monkeypatch.setattr(jobs, "POLL_INTERVAL_SEC", 0.01)

    def install(replies: dict, statuses: dict | None = None) -> _Gateway:
        gw = _Gateway(replies, statuses)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


@pytest.fixture
def deadline():
    """요청 처리 마감을 묶는다(라우트와 같은 ContextVar) — 조회 마감 = 마감 − 예약."""
    tokens = []

    def bind(seconds: float, reserve: float = 0.0) -> None:
        tokens.append(bind_request_deadline(time.monotonic() + seconds, reserve))

    yield bind
    for token in reversed(tokens):
        try:
            unbind_request_deadline(token)
        except ValueError:  # 비동기 테스트 태스크의 컨텍스트에서 묶였다 — 이미 버려진 컨텍스트
            pass


def _isolated(*hosts: str, user: str | None = "alice") -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": user}


async def _run(views: list[str], *hosts: str, cfg: SimpleNamespace | None = None,
               user: str | None = "alice") -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": views},
                                  _isolated(*hosts, user=user), llm=None,
                                  app_config=cfg or _cfg())


# ── 1. owner · wait_seconds ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_tool_carries_owner_and_wait_shorter_than_call_timeout(gateway) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    await _run(["apm.app_health"], "web01")
    (args,) = gw.named("apm_app_health")
    assert args["owner"] == "user:alice"
    assert args["wait_seconds"] == 8.0, "묶이지 않은 컨텍스트 = 호출 상한 − 2"
    assert args["wait_seconds"] < 10.0, "MCP 호출 상한보다 항상 짧다"


@pytest.mark.asyncio
async def test_anonymous_owner_when_no_subject(gateway) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    await _run(["apm.app_health"], "web01", user=None)
    assert gw.named("apm_app_health")[0]["owner"] == "user:anonymous"


def test_wait_seconds_follows_retrieval_deadline(deadline) -> None:
    assert jobs.wait_seconds(10.0) == 8.0
    deadline(5.0, reserve=1.0)
    assert 3.5 <= jobs.wait_seconds(10.0) <= 4.0, "조회 마감(마감 − 예약)까지 남은 시간"
    deadline(0.2)
    assert jobs.wait_seconds(10.0) == 1.0, "하한 1초"
    for timeout in (3.5, 10.0, 30.0):
        assert jobs.wait_seconds(timeout) < timeout


# ── 2. 승격 → 마감 안 완료 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_promoted_job_finished_in_time_answers_with_preview(gateway, store, deadline) -> None:
    deadline(5.0)
    rows = [{"instance_id": i, "response_time_ms": 900 + i} for i in range(3)]
    gw = gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
                 {JOB: [_env("apm_job_status", [], job=_handle("running")),
                        _done_status("apm_slow_transactions", rows)]})
    res = await _run(["apm.slow_tx"], "web01")

    assert [r["instance_id"] for r in res["organized_data"]["rows"]] == [0, 1, 2]
    assert all(r["hostname"] == "web01" for r in res["query_results"])
    assert res["source_status"][0]["status"] == "ok"
    assert "accepted_jobs" not in res and "disclosures" not in res, "작은 결과는 종전 답 그대로"
    assert all(a["owner"] == "user:alice" for a in gw.named("apm_job_status"))
    record = await store.get(JOB)
    assert record["owner_sub"] == "alice" and record["state"] == "completed"
    assert record["view"] == "apm.slow_tx" and record["hostname"] == "web01"


@pytest.mark.asyncio
async def test_finished_large_result_discloses_full_result_file(gateway, deadline) -> None:
    deadline(5.0)
    preview = [{"instance_id": i} for i in range(500)]
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_done_status("apm_slow_transactions", preview, total=1200)]})
    res = await _run(["apm.slow_tx"], "web01")

    assert len(res["organized_data"]["rows"]) == 500
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_FULL_RESULT_FILE and item["ref"] == {"apm_job_id": JOB}
    assert "앞 500행" in item["text"] and "전체 1,200행" in item["text"]
    assert "전체 1200행" in res["organized_data"]["summary"], "요약에도 결정적으로 싣는다"


# ── 3. 마감 초과 → 접수 답 ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unfinished_job_becomes_accepted_answer(gateway, store, deadline) -> None:
    deadline(0.15)
    gw = gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
                 {JOB: [_env("apm_job_status", [], job=_handle("running", done=5))]})
    res = await _run(["apm.slow_tx"], "web01")

    assert gw.named("apm_job_status"), "처리 마감 안에서 상태를 다시 봤다"
    assert "organized_data" not in res and "error" not in res, "데이터 답도 실패도 아니다"
    status = res["source_status"][0]
    assert status["status"] == "accepted" and status["rows"] == 0
    assert res["accepted_jobs"] == [JOB]
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_JOB_ACCEPTED and item["ref"] == {"apm_job_id": JOB}
    assert item["source"] == "task:t1"
    for part in ("web01", "예상 약 12.2초", "진행 5/61 API 호출", "작업 카드"):
        assert part in item["text"]
    assert item["text"] in res["final_response"], "본문에 같은 문장(집계기가 중복으로 붙이지 않게)"
    assert "아직 조회 결과가 아닙니다" in res["final_response"]
    assert "서버 메모리" in res["final_response"], "메모리 장부는 재기동 시 사라짐을 알린다"
    record = await store.get(JOB)
    assert record["owner_sub"] == "alice" and record["state"] == "running"
    assert record["ledger"] == "memory" and record["thread_id"] == "th-1"


@pytest.mark.asyncio
async def test_accepted_is_terminal_for_replanning_and_partial_in_audit(gateway, deadline) -> None:
    deadline(0.05)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    res = await _run(["apm.slow_tx"], "web01")
    tasks = [{"task_id": "t1", "agent": "apm_query"}]
    assert _terminal_source_task_ids(tasks, {"t1": res}) == {"t1"}, \
        "다시 계획하면 같은 조회를 또 접수한다"
    fields = _apm_query_fields(res)
    assert fields["outcome"] == "partial", "접수는 완료(ok)로 세지 않는다"


@pytest.mark.asyncio
async def test_mixed_rows_and_accepted_keep_data_and_notice(gateway, deadline) -> None:
    deadline(0.15)

    def health(args):
        if args["hostname"] == "web02":
            return _accepted("apm_app_health", JOB2)
        return _env("apm_app_health", [{"tps": 2}])

    gateway({"apm_app_health": health},
            {JOB2: [_env("apm_job_status", [], job=_handle("running", JOB2))]})
    res = await _run(["apm.app_health"], "web01", "web02")

    assert [r["hostname"] for r in res["organized_data"]["rows"]] == ["web01"]
    assert res["source_status"][0]["status"] == "partial"
    assert "작업 접수 1건" in res["source_status"][0]["reason"]
    assert res["accepted_jobs"] == [JOB2]
    assert [d["kind"] for d in res["disclosures"]] == [disc.APM_JOB_ACCEPTED]


# ── 4. 동기 artifact · partial · 실패 · 첫 홉 ─────────────────────────────────

@pytest.mark.asyncio
async def test_sync_artifact_registers_and_discloses_without_polling(gateway, store) -> None:
    rows = [{"instance_id": i} for i in range(500)]
    env = _env("apm_events", rows, total_row_count=5000, job=_handle("completed", done=40),
               artifact={"job_id": JOB, "total_rows": 5000, "chunks": [], "chunk_rows": 2000,
                         "columns": ["instance_id"], "text_parts": []})
    gw = gateway({"apm_events": env})
    res = await _run(["apm.events"], "web01")

    assert not gw.named("apm_job_status"), "끝난 결과는 다시 보지 않는다"
    assert res["source_status"][0]["status"] == "ok"
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_FULL_RESULT_FILE and item["ref"]["apm_job_id"] == JOB
    record = await store.get(JOB)
    assert record["state"] == "completed" and record["total_row_count"] == 5000


@pytest.mark.asyncio
async def test_partial_envelope_discloses_partial_sources(gateway) -> None:
    env = _env("apm_app_health", [{"tps": 1}], partial=True,
               limits=["[한계] APM 소스 common 조회 불가(source_unavailable) — 그 소스의 결과는"
                       " 빠졌다"])
    gateway({"apm_app_health": env})
    res = await _run(["apm.app_health"], "web01")
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_PARTIAL_SOURCES and "ref" not in item
    assert "부분 결과" in item["text"] and "common 조회 불가" in item["text"]
    assert disc.KIND_TABLE[item["kind"]].mandatory


@pytest.mark.asyncio
async def test_failed_job_is_a_failure_not_data(gateway, deadline) -> None:
    deadline(5.0)
    stalled = {"code": "stalled", "reason": "정체 — 300초 넘게 진행이 없어 끝냈다"}
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("failed", error=stalled))]})
    res = await _run(["apm.slow_tx"], "web01")
    assert res["degraded_reason"] == "apm_calls_failed"
    assert "stalled" in res["final_response"]


@pytest.mark.asyncio
async def test_unfinished_first_hop_job_is_cancelled(gateway, store, deadline) -> None:
    deadline(0.1)
    gw = gateway({"apm_instance_map": _accepted("apm_instance_map")},
                 {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    res = await _run(["apm.app_health"])

    assert gw.named("apm_job_cancel") == [{"job_id": JOB, "owner": "user:alice"}]
    assert res["degraded_reason"] == "apm_not_queried"
    assert "처리 시간 안에 끝나지 않아" in res["final_response"]
    assert await store.get(JOB) is None, "대상 선정 단계 작업은 장부에 올리지 않는다"


@pytest.mark.asyncio
async def test_first_hop_job_finished_in_time_selects_hosts(gateway, deadline) -> None:
    deadline(5.0)
    inventory = [{"instance_id": 1, "hostname": "was01", "match_confidence": "high"}]
    gw = gateway({"apm_instance_map": _accepted("apm_instance_map"),
                  "apm_app_health": _env("apm_app_health", [{"tps": 1}])},
                 {JOB: [_done_status("apm_instance_map", inventory)]})
    res = await _run(["apm.app_health"])
    assert gw.named("apm_app_health")[0]["hostname"] == "was01"
    assert res["source_status"][0]["status"] == "ok"


# ── 5. 고지 kind · ref 보존 ─────────────────────────────────────────────────────

def test_spec_kind_table_rows() -> None:
    """SPEC §7.5 표 — 등급 · 의무 · 범위."""
    expected = {
        "apm_job_accepted": ("partial", True), "apm_full_result_file": ("neutral", True),
        "apm_partial_sources": ("partial", True), "apm_current_only": ("neutral", False),
        "apm_hourly_resolution": ("neutral", False), "apm_change_detection": ("neutral", False),
        "apm_masked_fields": ("neutral", False), "apm_unresolved_condition": ("guide", True),
    }
    for kind, (grade, mandatory) in expected.items():
        spec = disc.KIND_TABLE[kind]
        assert (spec.grade, spec.mandatory, spec.scope) == (grade, mandatory, "task"), kind


def test_make_and_dedupe_keep_ref() -> None:
    a = disc.make(disc.APM_JOB_ACCEPTED, "접수", source="task:t1", ref={"apm_job_id": JOB})
    b = disc.make(disc.APM_JOB_ACCEPTED, "접수", source="task:t1", ref={"apm_job_id": JOB2})
    plain = disc.make(disc.ROW_LIMIT_REACHED, "상한")
    assert "ref" not in plain, "ref 없는 고지는 종전 세 칸 그대로"
    out = disc.dedupe([a, dict(a), b, plain, {"kind": "x"}])
    assert out == [a, b, plain], "같은 문구여도 작업이 다르면 둘 다 남는다"
    assert out[0]["ref"] == {"apm_job_id": JOB}


def test_aggregator_keeps_handler_disclosures_with_ref() -> None:
    item = disc.make(disc.APM_JOB_ACCEPTED, "web01: 작업으로 실행 중입니다.", source="task:t1",
                     ref={"apm_job_id": JOB})
    assert ra._handler_disclosures({"disclosures": [item]}) == [item]
    out = ra._apply_disclosures({"final_response": "접수\n- web01: 작업으로 실행 중입니다.",
                                 "disclosures": [item]}, {"user_query": "q"})
    assert out["disclosures"] == [item]
    assert out["final_response"].count("작업으로 실행 중입니다") == 1


# ── 2단 끝까지(분해 → 실행 → 재계획 → 집계) ───────────────────────────────────

def _app_config() -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


class _NoLLM:
    """재계획 평가 LLM — 접수 task는 결정적으로 끝나야 하므로 부르면 실패한다."""

    called = False

    async def ainvoke(self, *_a, **_k):
        _NoLLM.called = True
        raise AssertionError("접수 답 뒤 재계획 평가 LLM을 부르면 안 된다")

    def with_structured_output(self, *_a, **_k):
        return self


@pytest.mark.asyncio
async def test_two_tier_turn_carries_accepted_notice_with_ref(gateway, deadline) -> None:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.replanner import replanner
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    deadline(0.3)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    config = _app_config()
    query = "web01 느린 트랜잭션 전체"
    state = create_initial_state(user_query=query, thread_id="th-1", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({
        "parsed_requirements": {"filter_conditions": [
            {"field": "hostname", "op": "=", "value": "web01"}], "original_query": query},
        "task_plan": [{"task_id": "t1", "agent": "apm_query", "sub_query": query,
                       "views": ["apm.slow_tx"], "order": 1, "depends_on": [],
                       "input_from": [], "status": "pending"}],
        "task_results": {}, "replan_count": 0,
    })
    assert subagents.resolve_subagent("apm_query", config) is aq.APM_QUERY_SPEC, "조건부 등록"
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                          app_config=config))
    _NoLLM.called = False
    decision = await replanner(state, llm=_NoLLM(), app_config=config)
    assert decision["needs_replan"] is False and _NoLLM.called is False
    state.update(decision)
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약"]),
                                  app_config=config, synthesize=True, composite_answer="steps")

    assert "작업으로 실행 중입니다" in out["final_response"]
    accepted = [d for d in out["disclosures"] if d["kind"] == disc.APM_JOB_ACCEPTED]
    assert accepted and accepted[0]["ref"] == {"apm_job_id": JOB}


# ── 라우트(비스트림 · 스트림)가 ref를 그대로 싣는다 ─────────────────────────────

def _route_client(stream: bool, item: dict):
    import os
    from typing import TypedDict

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes
    from src.config import ServerConfig

    class _S(TypedDict, total=False):
        user_query: str
        final_response: str
        query_results: list
        messages: list
        disclosures: list

    async def result_aggregator(state: _S) -> dict:
        return {"final_response": "접수\n\n" + item["text"], "disclosures": [item],
                "messages": [HumanMessage(content=state.get("user_query", ""))]}

    g = StateGraph(_S)
    g.add_node("result_aggregator", result_aggregator)
    g.add_edge(START, "result_aggregator")
    g.add_edge("result_aggregator", END)
    compiled = g.compile()

    class _Graph:
        def __init__(self) -> None:
            if stream:
                self.astream_events = lambda s, c, version="v2": compiled.astream_events(
                    s, c, version=version)

        def get_state(self, config: dict) -> None:
            return None

        async def ainvoke(self, input_state: dict, config: dict) -> dict:
            return await compiled.ainvoke(input_state, config)

    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    app = FastAPI()
    app.state.config = AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )
    app.state.graph = _Graph()
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "alice", "role": "user"}
    return TestClient(app)


_ITEM = disc.make(disc.APM_JOB_ACCEPTED, "web01: 작업으로 실행 중입니다.", source="task:t1",
                  ref={"apm_job_id": JOB})


@pytest.mark.parametrize("stream", (True, False), ids=("astream_events", "ainvoke_fallback"))
def test_stream_done_keeps_ref(stream: bool) -> None:
    client = _route_client(stream, _ITEM)
    r = client.post("/api/v1/query/stream", json={"query": "web01 느린 트랜잭션"})
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"]
    assert done and done[0]["disclosures"] == [_ITEM]


def test_non_stream_response_keeps_ref() -> None:
    r = _route_client(False, _ITEM).post("/api/v1/query", json={"query": "web01 느린 트랜잭션"})
    assert r.status_code == 200, r.text
    assert r.json()["disclosures"] == [_ITEM]
