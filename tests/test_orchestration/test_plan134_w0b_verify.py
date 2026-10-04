"""plans/134 W0-B 본체 검증(검증자) — 작업 수명 · 접수 답 · 고지 참조 적대적 시험
(SPEC-apm-question-coverage §3.8 · §7.5 · D-251 ⑥ · D-066).

구현자 테스트(`test_plan134_apm_jobs.py`)가 고정한 대표 경로 밖을 친다.

  1. `wait_seconds`: 호출 **직전** 재계산(앞 호출이 쓴 시간만큼 줄어든다) · 첫 홉 포함 · 격자 불변식
     (`wait_seconds < source_call_timeout`) · 상한 ≤ 1초 설정에서 깨지는 경계(결함 재현).
  2. 접수 답은 데이터 완료가 아니다: `source_status` · 계획 요약 코드 → 하네스 `observed_sources` ·
     실제 감사 래퍼(`audited_investigation`)의 outcome · 재계획 종결 · 의존 단계 게이트 사유
     (결함 재현).
  3. 재확인 견고성: 상태 호출 통신 실패(접수로 남음) · 재확인 중 `job_not_found` · 중단·취소로 끝난
     작업(실패) · 접수 여러 건은 병렬로 기다린다(조회 마감 1회분).
  4. 인가 거부면 게이트웨이·장부 0 · 비활성 배포는 작업 기계를 건드리지 않는다(바이트 불변).
  5. `ref`가 파일 진입점(비스트림 · 스트림 2종)까지 보존된다 — 텍스트 진입점은 구현자 테스트가 고정.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0). 소스 코드는 바꾸지 않는다.
"""

from __future__ import annotations

import asyncio
import io
import json
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs as jobs
from src.orchestration import apm_query as aq
from src.orchestration import investigation_audit as ia
from src.orchestration.replanner import _terminal_source_task_ids
from src.utils.deadline import bind_request_deadline, unbind_request_deadline

JOB = "0123456789abcdef0123456789abcdef"
JOB2 = "fedcba9876543210fedcba9876543210"
JOB3 = "00112233445566778899aabbccddeeff"


def _cfg(*, timeout: float = 10.0, concurrency: int = 3, endpoint: bool = True,
         audit: bool = False) -> SimpleNamespace:
    endpoints = {"apm": "http://127.0.0.1:9096/sse"} if endpoint else {}
    return SimpleNamespace(
        dbhub=DBHubConfig(_env_file=None, source_endpoints=endpoints,
                          source_tokens='{"apm": "gw-token"}', source_call_timeout=timeout),
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=concurrency,
                                  audit_enabled=audit, task_frame_enabled=False,
                                  plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _handle(state: str, job_id: str = JOB, error: dict | None = None) -> dict:
    handle: dict[str, Any] = {
        "job_id": job_id, "state": state,
        "progress": {"done": 2, "total": 61, "unit": "api_calls", "label": "API 호출"},
        "estimate": {"api_calls": 61, "seconds": 12.2},
        "created_at": "2026-10-02T10:00:00+09:00", "updated_at": "2026-10-02T10:00:07+09:00",
        "expires_at": None}
    if error:
        handle["error"] = error
    return handle


class _Gateway:
    """모의 게이트웨이 세션 — 데이터 도구 응답(함수·지연 가능) · 상태 응답 순서 · 호출 기록."""

    def __init__(self, replies: dict, statuses: dict[str, list[Any]] | None = None, *,
                 delay: float = 0.0) -> None:
        self.replies = replies
        self.statuses = {k: list(v) for k, v in (statuses or {}).items()}
        self.delay = delay
        self.calls: list[tuple[str, dict, float]] = []
        self.opened = 0

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            self.opened += 1
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments), time.monotonic()))
        if name == "apm_job_status":
            queue = self.statuses[arguments["job_id"]]
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(reply, BaseException):
                raise reply
        elif name == "apm_job_cancel":
            reply = _env(name, [], job=_handle("cancelled", arguments["job_id"]))
        else:
            if self.delay:
                await asyncio.sleep(self.delay)
            reply = self.replies[name]
            if callable(reply):
                reply = reply(arguments)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [args for name, args, _ in self.calls if name == tool]


@pytest.fixture
def store(monkeypatch) -> ApmJobStore:
    ledger = ApmJobStore(None)
    monkeypatch.setattr(store_mod, "_STORE", ledger)
    return ledger


@pytest.fixture
def gateway(monkeypatch, store):
    monkeypatch.setattr(jobs, "POLL_INTERVAL_SEC", 0.01)

    def install(replies: dict, statuses: dict | None = None, **kw: Any) -> _Gateway:
        gw = _Gateway(replies, statuses, **kw)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


@pytest.fixture
def deadline():
    tokens = []

    def bind(seconds: float, reserve: float = 0.0) -> None:
        tokens.append(bind_request_deadline(time.monotonic() + seconds, reserve))

    yield bind
    for token in reversed(tokens):
        try:
            unbind_request_deadline(token)
        except ValueError:
            pass


def _isolated(*hosts: str, user: str | None = "alice", **extra: Any) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": user, **extra}


TASK = {"task_id": "t1", "agent": "apm_query", "views": ["apm.slow_tx"]}


async def _run(views: list[str], *hosts: str, cfg: SimpleNamespace | None = None,
               **iso: Any) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "views": views},
                                  _isolated(*hosts, **iso), llm=None, app_config=cfg or _cfg())


def _accepted(tool: str, job_id: str = JOB) -> dict:
    return _env(tool, [], job=_handle("running", job_id))


# ── 1. wait_seconds ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_wait_seconds_is_recomputed_right_before_each_call(gateway, deadline) -> None:
    """동시 1 · 호출마다 0.4초 — 뒤 호출의 wait_seconds는 앞 호출이 쓴 시간만큼 줄어든다."""
    deadline(4.0)
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions", [{"x": 1}])},
                 delay=0.4)
    await _run(["apm.slow_tx"], "web01", "web02", "web03", cfg=_cfg(concurrency=1))
    waits = [a["wait_seconds"] for a in gw.named("apm_slow_transactions")]
    assert len(waits) == 3 and all(isinstance(w, float) for w in waits)
    assert waits[0] > waits[1] > waits[2], waits
    assert waits[0] - waits[2] >= 0.6, "두 호출(0.8초)만큼 줄었다(반올림 0.1초)"
    assert all(w < 10.0 for w in waits) and waits[0] <= 4.0


@pytest.mark.asyncio
async def test_first_hop_instance_list_carries_owner_and_wait(gateway, deadline) -> None:
    deadline(30.0)
    inventory = [{"instance_id": 1, "hostname": "was01", "match_confidence": "high"}]
    gw = gateway({"apm_instance_map": _env("apm_instance_map", inventory),
                  "apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    await _run(["apm.app_health"])
    (first,) = gw.named("apm_instance_map")
    assert first["owner"] == "user:alice"
    assert isinstance(first["wait_seconds"], float) and 1.0 <= first["wait_seconds"] < 10.0


@pytest.mark.parametrize("timeout", [3.5, 5.0, 10.0, 30.0, 120.0])
@pytest.mark.parametrize("remaining", [None, -5.0, 0.0, 0.3, 2.0, 50.0, 10_000.0])
def test_wait_seconds_grid_stays_below_call_timeout(deadline, timeout, remaining) -> None:
    if remaining is not None:
        deadline(remaining)
    wait = jobs.wait_seconds(timeout)
    assert 1.0 <= wait < timeout, (timeout, remaining, wait)
    if remaining is not None and remaining > 1.0:
        assert wait <= remaining + 0.05, "조회 마감까지 남은 시간을 넘지 않는다"


def test_wait_seconds_below_call_timeout_even_for_tiny_timeout() -> None:
    cfg = DBHubConfig(_env_file=None, source_call_timeout=1.0)
    assert jobs.wait_seconds(cfg.source_call_timeout) < cfg.source_call_timeout


def test_unbound_poll_deadline_uses_processing_budget_minus_reserve() -> None:
    cfg = SimpleNamespace(server=SimpleNamespace(query_timeout=30, answer_reserve_sec=10))
    assert jobs.poll_deadline(cfg, now=100.0) == 120.0
    assert jobs.poll_deadline(SimpleNamespace(), now=0.0) == 105.0, "설정이 없으면 120 − 15"


# ── 2. 접수 답은 데이터 완료가 아니다 ─────────────────────────────────────────

def _harness_sees_apm(res: dict) -> bool:
    from scripts.scenario.source_metrics import QUERIED_STATUSES, observed_sources
    from src.api.routes.query import _nonsql_task_summary

    summary = _nonsql_task_summary({"views": ["apm.slow_tx"]}, res)
    assert "accepted" not in QUERIED_STATUSES
    return "apm" in observed_sources({"db_ids": [], "plan_summary": {"tasks": [summary]}})


@pytest.mark.asyncio
async def test_accepted_is_not_counted_by_harness_but_finished_job_is(gateway, deadline) -> None:
    deadline(0.15)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    accepted = await _run(["apm.slow_tx"], "web01")
    assert accepted["source_status"][0]["status"] == "accepted"
    assert _harness_sees_apm(accepted) is False, "접수는 「조회한 소스」로 세지 않는다"

    gateway({"apm_slow_transactions": _env("apm_slow_transactions", [{"x": 1}])})
    finished = await _run(["apm.slow_tx"], "web01")
    assert _harness_sees_apm(finished) is True, "대조군 — 끝난 조회는 센다"


@pytest.mark.asyncio
async def test_audit_wrapper_records_accepted_as_partial(gateway, deadline, monkeypatch) -> None:
    """실제 처리기(`APM_QUERY_SPEC.handler` = 감사 래퍼)를 통과시켜 감사 1건의 outcome을 본다."""
    captured: list[dict] = []

    async def fake_log(**kwargs: Any) -> None:
        captured.append(kwargs)

    monkeypatch.setattr(ia, "log_investigation", fake_log)
    deadline(0.15)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    await aq.APM_QUERY_SPEC.handler(TASK, _isolated("web01"), llm=None,
                                    app_config=_cfg(audit=True))
    (entry,) = captured
    assert entry["outcome"] == "partial", "접수는 완료(ok)가 아니다"
    assert {"reason": "apm_job_accepted", "detail": JOB[:8]} in entry["degraded"]
    assert entry["backend"] == "apm_gateway" and entry["user_id"] == "alice"


@pytest.mark.asyncio
async def test_mixed_result_is_terminal_for_replanning(gateway, deadline) -> None:
    deadline(0.15)

    def health(args):
        return (_accepted("apm_app_health", JOB2) if args["hostname"] == "web02"
                else _env("apm_app_health", [{"tps": 2}]))

    gateway({"apm_app_health": health},
            {JOB2: [_env("apm_job_status", [], job=_handle("running", JOB2))]})
    res = await _run(["apm.app_health"], "web01", "web02")
    assert res["organized_data"]["rows"] and res["accepted_jobs"] == [JOB2]
    tasks = [{"task_id": "t1", "agent": "apm_query"}]
    assert _terminal_source_task_ids(tasks, {"t1": res}) == {"t1"}
    assert _harness_sees_apm(res) is True, "행이 있는 부분 결과는 조회로 센다(partial)"


@pytest.mark.asyncio
async def test_full_result_file_without_accept_is_not_terminal(gateway) -> None:
    rows = [{"i": i} for i in range(500)]
    env = _env("apm_events", rows, total_row_count=900, job=_handle("completed"),
               artifact={"job_id": JOB, "total_rows": 900, "chunks": [], "chunk_rows": 2000,
                         "columns": ["i"], "text_parts": []})
    gateway({"apm_events": env})
    res = await _run(["apm.events"], "web01")
    assert "accepted_jobs" not in res
    assert _terminal_source_task_ids([{"task_id": "t1", "agent": "apm_query"}],
                                     {"t1": res}) == set(), "데이터 답은 종전대로 평가 대상"


@pytest.mark.asyncio
async def test_dependent_step_on_accepted_predecessor_is_not_reported_as_empty(gateway,
                                                                             deadline) -> None:
    from src.utils.prior_dependency import REASON_PRIOR_EMPTY, assess_prior_dependency

    deadline(0.15)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    res = await _run(["apm.slow_tx"], "web01")
    verdict = assess_prior_dependency({"task_id": "t2", "input_from": ["t1"]}, {"t1": res})
    assert verdict is not None and not verdict.ok
    assert verdict.reason != REASON_PRIOR_EMPTY and "0건" not in verdict.detail


@pytest.mark.asyncio
async def test_step_progress_does_not_mark_accepted_task_completed(gateway, deadline) -> None:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.config import AppConfig, LLMConfig
    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.task_progress import build_task_payload
    from src.state import create_initial_state

    deadline(0.3)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    config = AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
                       dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                                         source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
                       checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    state = create_initial_state(user_query="web01 느린 트랜잭션", thread_id="th-1",
                                 user_id="alice", user_role="user", allowed_sources=None)
    state.update({
        "parsed_requirements": {"filter_conditions": [
            {"field": "hostname", "op": "=", "value": "web01"}], "original_query": "q"},
        "task_plan": [{"task_id": "t1", "agent": "apm_query", "sub_query": "web01 느린 트랜잭션",
                       "views": ["apm.slow_tx"], "order": 1, "depends_on": [],
                       "input_from": [], "status": "pending"}],
        "task_results": {}, "replan_count": 0,
    })
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                          app_config=config))
    task = state["task_plan"][0]
    res = state["task_results"]["t1"]
    assert res["source_status"][0]["status"] == "accepted"
    payload = build_task_payload(task, "end", result=res)
    assert payload["status"] != "completed", payload


# ── 3. 재확인 견고성 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_transport_failure_keeps_job_accepted(gateway, store, deadline) -> None:
    """상태 호출 시간 초과(통신) — 작업은 이어질 수 있으므로 실패가 아니라 접수로 남는다."""
    deadline(2.0)
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [TimeoutError()]})
    res = await _run(["apm.slow_tx"], "web01")
    assert res["source_status"][0]["status"] == "accepted"
    assert "error" not in res and res["accepted_jobs"] == [JOB]
    assert (await store.get(JOB))["owner_sub"] == "alice", "카드가 다시 볼 수 있게 장부에 남는다"


@pytest.mark.asyncio
async def test_job_not_found_during_poll_is_a_failure_not_accepted(gateway, deadline) -> None:
    deadline(2.0)
    lost = {"error": "job_not_found", "reason": "작업이 없다 — 게이트웨이 재기동", "tool": "s"}
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")}, {JOB: [lost]})
    res = await _run(["apm.slow_tx"], "web01")
    assert res["degraded_reason"] == "apm_calls_failed"
    assert "job_not_found" in res["final_response"]
    assert "accepted_jobs" not in res and "organized_data" not in res


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["interrupted", "cancelled", "failed"])
async def test_jobs_ending_without_result_are_failures(gateway, deadline, state) -> None:
    deadline(2.0)
    error = {"code": state, "reason": f"사유-{state}"}
    gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
            {JOB: [_env("apm_job_status", [], job=_handle(state, error=error))]})
    res = await _run(["apm.slow_tx"], "web01")
    assert res["degraded_reason"] == "apm_calls_failed"
    assert f"사유-{state}" in res["final_response"]
    assert res["source_status"][0]["status"] == "unavailable"


@pytest.mark.asyncio
async def test_status_poll_does_not_overrun_retrieval_deadline(gateway, deadline,
                                                               monkeypatch) -> None:
    deadline(0.3)
    gw = gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")},
                 {JOB: [_env("apm_job_status", [], job=_handle("running"))]})
    original = gw.call_tool

    async def slow_status(name, arguments):
        if name == "apm_job_status":
            await asyncio.sleep(1.5)
        return await original(name, arguments)

    gw.call_tool = slow_status
    started = time.monotonic()
    res = await _run(["apm.slow_tx"], "web01")
    elapsed = time.monotonic() - started
    assert res["source_status"][0]["status"] == "accepted"
    assert elapsed < 0.3 + 0.5, f"조회 마감 0.3초를 {elapsed:.2f}초로 넘겼다"


@pytest.mark.asyncio
async def test_gateway_partial_envelope_is_partial_in_status_and_audit(gateway) -> None:
    env = _env("apm_app_health", [{"tps": 1}], partial=True,
               limits=["[한계] APM 소스 common 조회 불가(source_unavailable)"])
    gateway({"apm_app_health": env})
    res = await _run(["apm.app_health"], "web01")
    assert [d["kind"] for d in res["disclosures"]] == [disc.APM_PARTIAL_SOURCES]
    assert res["source_status"][0]["status"] == "partial"
    assert ia._apm_query_fields(res)["outcome"] == "partial"


@pytest.mark.asyncio
async def test_several_accepted_jobs_wait_in_parallel(gateway, deadline) -> None:
    """접수 3건 — 조회 마감을 건마다 따로 쓰지 않는다(병렬 재확인 · 처리 상한을 늘리지 않음)."""
    deadline(0.5)
    ids = {"web01": JOB, "web02": JOB2, "web03": JOB3}
    gateway({"apm_slow_transactions": lambda a: _accepted("apm_slow_transactions",
                                                          ids[a["hostname"]])},
            {j: [_env("apm_job_status", [], job=_handle("running", j))] for j in ids.values()})
    started = time.monotonic()
    res = await _run(["apm.slow_tx"], *ids)
    elapsed = time.monotonic() - started
    assert sorted(res["accepted_jobs"]) == sorted(ids.values())
    assert elapsed < 1.0, f"마감 0.5초 × 3건이 아니라 한 번분이어야 한다(실측 {elapsed:.2f}초)"
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds == [disc.APM_JOB_ACCEPTED] * 3
    assert {d["ref"]["apm_job_id"] for d in res["disclosures"]} == set(ids.values())


# ── 4. 인가 · 비활성 배포 ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_source_denied_makes_no_gateway_call_and_no_ledger(gateway, store) -> None:
    gw = gateway({"apm_slow_transactions": _accepted("apm_slow_transactions")})
    res = await _run(["apm.slow_tx"], "web01", user_role="user",
                     allowed_sources=["polestar_cm_gp"])
    assert gw.opened == 0 and gw.calls == []
    assert await store.list_for("alice") == [], "장부 등록은 실행 인가 뒤다(SPEC §7.6)"
    assert "accepted_jobs" not in res


@pytest.mark.asyncio
async def test_inactive_deployment_touches_no_job_machinery(monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_STORE", None)
    touched: list[str] = []
    monkeypatch.setattr(jobs, "poll_deadline", lambda *a, **k: touched.append("poll") or 0.0)
    monkeypatch.setattr(jobs, "store_for", lambda *a, **k: touched.append("store"))

    @asynccontextmanager
    async def never(url, headers):
        touched.append("session")
        yield None

    monkeypatch.setattr(aq, "_SESSION_FACTORY", never)
    res = await aq.run_apm_query(TASK, _isolated("web01"), llm=None,
                                 app_config=_cfg(endpoint=False))
    assert touched == [] and store_mod._STORE is None
    assert set(res) == {"error", "degraded_reason", "final_response", "apm_query", "source_status"}
    assert not {"jobs", "accepted_jobs"} & set(res["apm_query"])
    assert aq.active_extra_subagents(_cfg(endpoint=False)) == {}


@pytest.mark.asyncio
async def test_small_sync_result_has_no_job_traces(gateway, store) -> None:
    """활성 배포의 짧은 조회도 종전 모양 그대로 — 고지·접수·메타 `jobs`·장부 0."""
    gateway({"apm_slow_transactions": _env("apm_slow_transactions", [{"x": 1}])})
    res = await _run(["apm.slow_tx"], "web01")
    assert not {"disclosures", "accepted_jobs"} & set(res)
    assert not {"jobs", "accepted_jobs"} & set(res["apm_query"])
    assert await store.list_for("alice") == []


def test_handler_disclosure_pass_is_identity_for_plain_results() -> None:
    import importlib

    ra = importlib.import_module("src.orchestration.result_aggregator")
    assert ra._handler_disclosures({"final_response": "x"}) == []
    legacy = {"kind": disc.ROW_LIMIT_REACHED, "text": "상한", "source": "turn"}
    assert disc.dedupe([legacy]) == [legacy] and set(disc.dedupe([legacy])[0]) == {
        "kind", "text", "source"}, "ref 없는 고지는 세 칸 그대로(바이트 불변)"


# ── 5. ref — 파일 진입점(비스트림 · 스트림) ────────────────────────────────────

_ITEM = disc.make(disc.APM_JOB_ACCEPTED, "web01: 작업으로 실행 중입니다.", source="task:t1",
                  ref={"apm_job_id": JOB})


def _xlsx_bytes() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["hostname", "tps"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _file_client(monkeypatch, stream: bool):
    import os
    from typing import TypedDict

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from langchain_core.messages import HumanMessage
    from langgraph.graph import END, START, StateGraph

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes
    from src.config import AppConfig, ServerConfig

    class _S(TypedDict, total=False):
        user_query: str
        final_response: str
        query_results: list
        messages: list
        disclosures: list

    async def result_aggregator(state: _S) -> dict:
        return {"final_response": "접수\n\n" + _ITEM["text"], "disclosures": [_ITEM],
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

    async def no_csv(*_a, **_k):
        raise RuntimeError("검증용 — 변환 생략")

    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    monkeypatch.setattr(query_routes, "_file_zone_clarification_or_none", lambda *a, **k: None)
    monkeypatch.setattr("src.document.excel_csv_converter.excel_to_csv_cached", no_csv)
    app = FastAPI()
    app.state.config = AppConfig(
        _env_file=None, db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(_env_file=None, query_timeout=60, file_query_timeout=120),
    )
    app.state.graph = _Graph()
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "alice", "role": "user"}
    return TestClient(app)


def _file_form() -> dict:
    return {"files": {"file": ("form.xlsx", _xlsx_bytes(),
                               "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            "data": {"query": "web01 느린 트랜잭션으로 양식 채워줘"}}


@pytest.mark.parametrize("stream", (True, False), ids=("astream_events", "ainvoke_fallback"))
def test_file_stream_done_keeps_ref(monkeypatch, stream: bool) -> None:
    r = _file_client(monkeypatch, stream).post("/api/v1/query/file/stream", **_file_form())
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    done = [e for e in events if e.get("type") == "done"]
    assert done, events
    assert done[0]["disclosures"] == [_ITEM]


def test_file_non_stream_response_keeps_ref(monkeypatch) -> None:
    r = _file_client(monkeypatch, False).post("/api/v1/query/file", **_file_form())
    assert r.status_code == 200, r.text
    assert r.json()["disclosures"] == [_ITEM]
