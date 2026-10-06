"""plans/134 W1 본체 — 집계 운반(M-1) · 창 의미(M-2) · 보기 선택 조건(M-3) · 고지(M-9).

고정하는 계약(SPEC-apm-question-coverage §6.1~§6.3 · §7.1~§7.3 · §7.5):
  1. M-3: 분해 `view_args`를 보기 선언(`ViewArgSpec`)으로 검증해 도구 인자(SPEC 이름 그대로)로
     싣는다 · 모르는 이름·값은 버리고 `apm_unresolved_condition` · 선택 조건이 모두 무효여도 보기는
     조회한다(W1 검증 H-2) · `null`은 미지정 · 평면 조건(보기 id 없음)은 보기가 하나면 그 보기
     것(W1 검증 L-1) · 파서 `limit` → `n`(조건에 `n`·`full`이 없을 때) · 「전체」 단어만으로는
     `full`을 만들지 않는다.
  2. M-2: range 보기는 기간을 자르지 않고 넘긴다 · current 보기 + 기간 = `apm_current_only` ·
     시 단위 봉투 = `apm_hourly_resolution`.
  3. M-1: (보기, 대상)별 `meta["aggregates"]`(덮어쓰기 없음) · `meta["was_signals"]`(가장 강한
     판정 1건) · 결정적 줄(판정 · 창 집계 · 시 단위 합계 · 오류 유형별) · **2단 최종 답에 그 줄이
     실린다**.
  4. 분해: 활성 배포에서만 `view_args` 슬롯·프롬프트 렌더(비활성 바이트 불변) · 형태 정제.
  5. W0-B 잔여: 스레드 이력이 응답 고지를 남겨 대화를 다시 불러도 작업 카드가 복원된다.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이·LLM 0).
"""

from __future__ import annotations

import asyncio
import importlib
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration import subagents
from src.orchestration.conditional_agents import sanitize_task_views
from src.orchestration.schemas import DecomposedPlan, views_plan_model
from tests.test_orchestration import apm_batch_mock

ip = importlib.import_module("src.orchestration.intent_planner")

NOW = datetime(2026, 10, 2, 10, 0, 0)
ROOT = Path(__file__).resolve().parents[2]


def _cfg(*, active: bool = True) -> SimpleNamespace:
    dbhub = DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if active else {},
                        source_tokens='{"apm": "gw-token"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-02T10:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    def __init__(self, replies: dict) -> None:
        self.replies = replies
        self.calls: list[tuple[str, dict]] = []

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = self.replies[name]
        # plans/134 M-5 — 다건 대상은 `targets` 배치 1호출(계약 A-2 봉투로 흉내)
        reply = apm_batch_mock.reply_for(name, arguments,
                                         reply if callable(reply) else (lambda a: reply))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]


@pytest.fixture
def gateway(monkeypatch):
    def install(replies: dict) -> _Gateway:
        gw = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return install


def _isolated(*hosts: str, time_range: dict | None = None, limit: int | None = None) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": time_range, "limit": limit},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}


async def _run(views: list[str], *hosts: str, view_args: dict | None = None,
               time_range: dict | None = None, limit: int | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views}
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(task, _isolated(*hosts, time_range=time_range, limit=limit),
                                  llm=None, app_config=_cfg(), now=NOW)


def _view(vid: str):
    return {v.id: v for v in aq.apm_views()}[vid]


# ── 1. M-3 선택 조건 ─────────────────────────────────────────────────────────

def test_validate_view_args_types_and_rejections() -> None:
    events = _view("apm.events")
    args, rejected = aq.validate_view_args(events, {
        "level": "fatal", "level_mode": "EXACT", "error_type": "OUTOFMEMORY", "record": "error",
        "n": "5", "full": "true"})
    assert args == {"level": "fatal", "level_mode": "exact", "error_type": "OUTOFMEMORY",
                    "record": "error", "n": 5, "full": True}
    assert rejected == []
    args, rejected = aq.validate_view_args(events, {
        "n": 0, "level_mode": "below", "level": "fa tal; DROP", "sort": "x", "full": 1})
    assert args == {}
    assert [r.split("=")[0].split("(")[0] for r in rejected] == [
        "n", "level_mode", "level", "sort", "full"]
    assert aq.validate_view_args(events, "level=fatal") == ({}, ["조건 형식이 아님(str)"])
    assert aq.validate_view_args(_view("apm.pool"), {"n": 3})[1] == ["n(이 보기에 없는 조건)"]


@pytest.mark.asyncio
async def test_view_args_reach_the_tool_with_spec_names(gateway) -> None:
    gw = gateway({"apm_events": _env("apm_events", [{"level": "FATAL"}])})
    res = await _run(["apm.events"], "web01", view_args={"apm.events": {
        "level": "fatal", "level_mode": "exact", "error_type": "OUTOFMEMORY", "record": "error",
        "full": True}})
    (args,) = gw.named("apm_events")
    for key, value in {"hostname": "web01", "level": "fatal", "level_mode": "exact",
                       "error_type": "OUTOFMEMORY", "record": "error", "full": True,
                       "owner": "user:alice"}.items():
        assert args[key] == value, key
    assert "n" not in args, "full이면 파서 limit도 n으로 옮기지 않는다"
    assert "disclosures" not in res


@pytest.mark.asyncio
async def test_unknown_condition_is_dropped_and_disclosed(gateway) -> None:
    gw = gateway({"apm_events": _env("apm_events", [{"level": "FATAL"}])})
    res = await _run(["apm.events"], "web01", view_args={"apm.events": {
        "level": "fatal", "colour": "red"}})
    (args,) = gw.named("apm_events")
    assert args["level"] == "fatal" and "colour" not in args
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and "colour" in item["text"]
    assert "빼고 조회했습니다" in item["text"]
    spec = disc.KIND_TABLE[item["kind"]]
    assert spec.grade == "guide" and spec.mandatory
    assert res["apm_query"]["unresolved"] == [
        {"view": "apm.events", "conditions": ["colour(이 보기에 없는 조건)"], "queried": True}]


@pytest.mark.asyncio
async def test_invalid_optional_conditions_never_block_the_view(gateway) -> None:
    """선택 조건이 모두 무효여도 보기는 조회하고 버린 조건만 알린다(W1 검증 H-2 · SPEC §6.3)."""
    gw = gateway({"apm_events": _env("apm_events", [{"level": "FATAL"}])})
    res = await _run(["apm.events"], "web01", view_args={"apm.events": {
        "level_mode": "below", "period": "3시간", "n": None}})
    (args,) = gw.named("apm_events")
    assert not {"level_mode", "period", "n"} & set(args)
    assert "degraded_reason" not in res and res["query_results"]
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and "빼고 조회했습니다" in item["text"]
    assert "period" in item["text"] and "'None'" not in item["text"], "null 은 미지정(무고지)"
    assert res["apm_query"]["unresolved"] == [{
        "view": "apm.events", "queried": True,
        "conditions": ["level_mode='below'", "period(이 보기에 없는 조건)"]}]
    assert not res["apm_query"]["failures"]


def test_level_is_the_gateway_enum_and_unicode_digits_are_rejected() -> None:
    """`level`은 게이트웨이 계약 값(대소문자 무시 — W1 검증 L-4) · 「²」·전각 숫자 거부(L-2)."""
    events = _view("apm.events")
    assert aq.validate_view_args(events, {"level": "WARNING"}) == ({"level": "warning"}, [])
    for bad in ("error", "Jennifer", "critical"):
        assert aq.validate_view_args(events, {"level": bad})[0] == {}, bad
    for digits in ("²", "７", "٣"):
        args, rejected = aq.validate_view_args(events, {"n": digits})
        assert args == {} and rejected, digits


@pytest.mark.asyncio
@pytest.mark.parametrize(("views", "applied"), [
    (["apm.events"], True),                       # 보기 하나 → 그 보기 조건
    (["apm.events", "apm.slow_tx"], False),       # 여럿 → 버리고 알림
])
async def test_flat_view_args(gateway, views, applied) -> None:
    gw = gateway({"apm_events": _env("apm_events", [{"level": "FATAL"}]),
                  "apm_slow_transactions": _env("apm_slow_transactions", [{"tx": 1}])})
    plan = {"tasks": [{"task_id": "t1", "agent": "apm_query", "views": views,
                       "view_args": {"level": "fatal", "apm.events": {"level_mode": "exact"}}}]}
    sanitize_task_views(plan, _cfg())
    (task,) = plan["tasks"]
    assert task["view_args"] == {"apm.events": {"level_mode": "exact"},
                                 aq.FLAT_VIEW_ARGS_KEY: {"level": "fatal"}}
    res = await aq.run_apm_query(task, _isolated("web01"), llm=None, app_config=_cfg(), now=NOW)
    (args,) = gw.named("apm_events")
    assert args["level_mode"] == "exact", "보기 묶음은 그대로"
    kinds = [d for d in res.get("disclosures") or []
             if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    if applied:
        assert args["level"] == "fatal" and not kinds
    else:
        assert "level" not in args and "level" not in gw.named("apm_slow_transactions")[0]
        (item,) = kinds
        assert "level(보기 미지정)" in item["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("view_args", "expected"), [
    (None, 5),                                   # 파서 limit → n
    ({"apm.slow_tx": {"n": 3}}, 3),              # 계획 LLM 이 낸 n 이 이긴다
    ({"apm.slow_tx": {"full": True}}, None),     # 전체면 n 을 만들지 않는다
])
async def test_parser_limit_becomes_n(gateway, view_args, expected) -> None:
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions", [{"tx": 1}])})
    await _run(["apm.slow_tx"], "web01", view_args=view_args, limit=5)
    assert gw.named("apm_slow_transactions")[0].get("n") == expected


@pytest.mark.asyncio
async def test_parser_limit_is_not_used_in_composite_plans(gateway) -> None:
    """파서 limit 은 질의 전체 값이라 복합 계획에서는 다른 task 몫일 수 있다 — 계획 LLM 의 n 만."""
    gw = gateway({"apm_slow_transactions": _env("apm_slow_transactions", [{"tx": 1}])})
    isolated = {**_isolated("web01", limit=5), "is_composite": True}
    await aq.run_apm_query({"task_id": "t2", "agent": "apm_query", "views": ["apm.slow_tx"]},
                           isolated, llm=None, app_config=_cfg(), now=NOW)
    assert "n" not in gw.named("apm_slow_transactions")[0]


@pytest.mark.asyncio
async def test_full_is_never_inferred_from_words(gateway) -> None:
    """「전부」 같은 단어는 코드가 보지 않는다 — 계획 LLM 이 낸 `full`만 쓴다(단어 매칭 금지)."""
    gw = gateway({"apm_active_services": _env("apm_active_services", [{"tx": 1}])})
    task = {"task_id": "t1", "agent": "apm_query", "views": ["apm.active"],
            "sub_query": "web01 실행 중 서비스 전부 · 모두 · 전체"}
    isolated = _isolated("web01")
    isolated["user_query"] = task["sub_query"]
    await aq.run_apm_query(task, isolated, llm=None, app_config=_cfg(), now=NOW)
    args = gw.named("apm_active_services")[0]
    assert "full" not in args and "n" not in args


def test_sanitize_view_args_keeps_selected_views_only() -> None:
    result = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "views": ["apm.events", "bogus"],
         "view_args": {"apm.events": {"level": "fatal"}, "apm.pool": {"n": 1},
                       "apm.slow_tx": "x"}},
        {"task_id": "t2", "agent": "data_query", "view_args": {"apm.events": {"level": "x"}}},
    ]}
    sanitize_task_views(result, _cfg())
    t1, t2 = result["tasks"]
    assert t1["views"] == ["apm.events"]
    assert t1["view_args"] == {"apm.events": {"level": "fatal"}}
    assert "view_args" not in t2, "다른 담당의 조건은 떼어 낸다"
    assert aq.sanitize_view_args({"apm.events": "level=fatal"}, ["apm.events"]) == {
        "apm.events": "level=fatal"}, "형식이 틀린 묶음도 처리기까지 가서 고지된다"


# ── 2. M-2 창 · 고지 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_range_view_gets_the_whole_period(gateway) -> None:
    gw = gateway({"apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1}])})
    res = await _run(["apm.runtime"], "web01",
                     time_range={"start": "2026-10-02 06:00", "end": "2026-10-02 09:30"})
    args = gw.named("apm_runtime_health")[0]
    assert args["lookback_minutes"] == 210 and args["reference_time"] == "2026-10-02T09:30:00"
    assert "현재값 기준" not in res["organized_data"]["summary"], "C-2 오고지 제거"


@pytest.mark.asyncio
async def test_current_view_with_period_is_disclosed(gateway) -> None:
    gw = gateway({"apm_resource_pool": _env("apm_resource_pool", [{"db_pool_active": 3}])})
    res = await _run(["apm.pool"], "web01",
                     time_range={"start": "2026-10-02 06:00", "end": "2026-10-02 09:30"})
    args = gw.named("apm_resource_pool")[0]
    assert "lookback_minutes" not in args
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_CURRENT_ONLY and "현재값" in item["text"]


@pytest.mark.asyncio
async def test_hourly_block_is_disclosed(gateway) -> None:
    hourly = {"calls": 1200, "failures": 12, "failure_rate": 0.01, "response_time_avg_ms": 210.4,
              "max_response_time_ms": 9100, "top_applications": [],
              "hour_start": "2026-10-02T06:00:00+09:00", "hour_end": "2026-10-02T10:00:00+09:00"}
    gateway({"apm_app_health": _env("apm_app_health", [{"tps": 2}], hourly=hourly)})
    res = await _run(["apm.app_health"], "web01",
                     time_range={"start": "2026-10-02 06:10", "end": "2026-10-02 09:40"})
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds == [disc.APM_HOURLY_RESOLUTION]
    assert any("시 단위 합계(2026-10-02T06:00:00+09:00~2026-10-02T10:00:00+09:00) 호출 1,200건"
               in line for line in res["answer_lines"])


# ── 3. M-1 집계 운반 · 결정적 줄 ─────────────────────────────────────────────

_SIG_WARN = {"kind": "was_heap_pressure", "level": "WARNING", "category": "medium",
             "label": "힙 메모리 압박", "evidence": "heap_usage_ratio=0.91",
             "instance_id": 11, "source_id": "bank"}
_SIG_CRIT = {**_SIG_WARN, "level": "CRITICAL", "category": "strong",
             "evidence": "OUTOFMEMORY 이벤트"}


def _slow_env(args: dict) -> dict:
    host = args["hostname"]
    return _env("apm_slow_transactions", [{"instance_id": 11, "response_time_ms": 900}],
                summary={"calls": 1234, "errors": 26, "error_rate": 0.0211,
                         "response_time_p50_ms": 120.0, "response_time_p95_ms": 980.0,
                         "sql_fetch_share": 0.7, "external_share": 0.1},
                was_signals=[_SIG_WARN, _SIG_CRIT] if host == "web01" else [],
                window={"start": "x", "end": "y", "minutes": 10})


@pytest.mark.asyncio
async def test_aggregates_per_view_and_target_and_signal_dedupe(gateway) -> None:
    gateway({"apm_slow_transactions": _slow_env,
             "apm_events": _env("apm_events", [], errors_by_type=[
                 {"error_type": "OUTOFMEMORY", "count": 3}, {"error_type": "SQL_EXCEPTION",
                                                             "count": 1}])})
    res = await _run(["apm.slow_tx", "apm.events"], "web01", "web02")
    meta = res["apm_query"]
    keys = [(a["view"], a["hostname"]) for a in meta["aggregates"]]
    assert sorted(keys) == sorted([("apm.slow_tx", "web01"), ("apm.slow_tx", "web02"),
                                   ("apm.events", "web01"), ("apm.events", "web02")])
    assert all(a["summary"]["calls"] == 1234 for a in meta["aggregates"]
               if a["view"] == "apm.slow_tx"), "대상마다 한 항목(덮어쓰기 없음)"
    (sig,) = meta["was_signals"]
    assert sig["level"] == "CRITICAL" and sig["hostname"] == "web01", "가장 강한 판정 1건"
    lines = res["answer_lines"]
    assert "판정 힙 메모리 압박(CRITICAL) — web01 #11: OUTOFMEMORY 이벤트" in lines
    assert ("느린 트랜잭션(시간 분해) · web01: 구간 호출 1,234건 · 오류 26건 · 오류율 2.1% · "
            "p50 120ms · p95 980ms") in lines
    assert ("WAS 이벤트(fatal·warning 등) · web02: 오류 유형별 — "
            "OUTOFMEMORY 3건 · SQL_EXCEPTION 1건") in lines
    assert "판정·집계:" in res["organized_data"]["summary"]


@pytest.mark.asyncio
async def test_row_windows_and_active_summary_lines(gateway) -> None:
    gateway({
        "apm_app_health": _env("apm_app_health", [{"instance_id": 11, "instance_name": "was1",
                                                   "window": {"calls": 50, "errors": 1,
                                                              "error_rate": 0.02,
                                                              "response_time_p50_ms": 80,
                                                              "response_time_p95_ms": 400}}]),
        "apm_active_services": _env("apm_active_services", [{"tx": 1}], summary={
            "total": 7, "by_running_mode": {"SQL": 5, "SOCKET": 2}, "by_status": {}}),
    })
    res = await _run(["apm.app_health", "apm.active"], "web01")
    lines = res["answer_lines"]
    assert any(line.endswith("· web01 · was1: 구간 호출 50건 · 오류 1건 · 오류율 2.0% · p50 80ms · "
                             "p95 400ms") for line in lines)
    assert "지금 실행 중인 서비스 · web01: 실행 중 7건(실행 모드별 SQL 5건 · SOCKET 2건)" in lines


def _app_config() -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


async def _polestar_handler(task, isolated, *, llm, app_config):
    rows = [{"hostname": "web01", "cpu_usage_pct": 42.5}]
    return {"organized_data": {"summary": "1건", "rows": rows, "column_mapping": None,
                               "resolved_mapping": None, "is_sufficient": True,
                               "sheet_mappings": None},
            "query_results": rows, "target_db_ids": ["polestar_cm_gp"]}


async def _two_tier_turn(plan: list[dict], monkeypatch) -> dict:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.replanner import replanner
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=_polestar_handler))
    config = _app_config()
    query = "web01 서버 CPU와 느린 트랜잭션"
    state = create_initial_state(user_query=query, thread_id="th-1", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({
        "parsed_requirements": {"filter_conditions": [
            {"field": "hostname", "op": "=", "value": "web01"}], "original_query": query},
        "task_plan": plan, "task_results": {}, "replan_count": 0,
    })
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"]),
                                          app_config=config))
    no_followup = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state.update(await replanner(state, llm=FakeListChatModel(responses=[no_followup]),
                                 app_config=config))
    return await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 4),
                                   app_config=config, synthesize=True,
                                   composite_answer="steps")


def _task(tid: str, agent: str, sub: str, **extra: Any) -> dict:
    return {"task_id": tid, "agent": agent, "sub_query": sub, "order": int(tid[1:]),
            "depends_on": [], "input_from": [], "status": "pending", **extra}


@pytest.mark.asyncio
@pytest.mark.parametrize("composite", [False, True], ids=["single_task", "with_polestar_task"])
async def test_two_tier_final_answer_carries_deterministic_lines(gateway, monkeypatch,
                                                                 composite) -> None:
    gateway({"apm_slow_transactions": _slow_env})
    plan = [_task("t1", "apm_query", "web01 느린 트랜잭션", views=["apm.slow_tx"])]
    if composite:
        plan.insert(0, _task("t0", "data_query", "web01 CPU 사용률"))
    out = await _two_tier_turn(plan, monkeypatch)
    answer = out["final_response"]
    assert "**판정·집계**" in answer
    assert "- 판정 힙 메모리 압박(CRITICAL) — web01 #11: OUTOFMEMORY 이벤트" in answer
    assert "구간 호출 1,234건 · 오류 26건 · 오류율 2.1% · p50 120ms · p95 980ms" in answer


# ── 4. 분해 — 활성일 때만 · 형태 ─────────────────────────────────────────────

def test_prompt_renders_view_args_only_when_active() -> None:
    active = ip._planner_system_prompt(_cfg(active=True))
    assert "조건(view_args)" in active and "`level_mode` = min|exact" in active
    assert "「web01 fatal 이벤트만」 → level=fatal · level_mode=exact" in active
    for stale in ("전체 200개 상한", "구간 10분 상한", "상위 20", "최대 24시간"):
        assert stale not in active, stale
    inactive = ip._planner_system_prompt(_cfg(active=False))
    assert "view_args" not in inactive and "apm." not in inactive


def test_view_args_slot_only_with_apm() -> None:
    with_apm = views_plan_model(DecomposedPlan, ("apm_query",), True)
    plan = with_apm.model_validate({"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "x", "views": ["apm.events"],
         "view_args": {"apm.events": {"level": "fatal"}}}]})
    assert plan.tasks[0].view_args == {"apm.events": {"level": "fatal"}}
    doc_only = views_plan_model(DecomposedPlan, ("doc_query",), False)
    task_schema = next(v for k, v in doc_only.model_json_schema()["$defs"].items()
                       if k.startswith("Views"))
    assert "view_args" not in task_schema["properties"], "APM 비활성이면 슬롯이 없다"


class _JsonLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def ainvoke(self, messages):
        return SimpleNamespace(content=json.dumps(self.payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_json_decompose_keeps_view_args_for_apm_tasks() -> None:
    payload = {"tasks": [
        {"task_id": "t1", "agent": "apm_query", "sub_query": "web01 fatal 이벤트만",
         "views": ["apm.events"], "view_args": {"apm.events": {"level": "fatal",
                                                                "level_mode": "exact"}}},
        {"task_id": "t2", "agent": "data_query", "sub_query": "web01 CPU",
         "view_args": {"apm.events": {"level": "x"}}},
    ]}
    active = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=True))
    t1, t2 = active["tasks"]
    assert t1["view_args"] == {"apm.events": {"level": "fatal", "level_mode": "exact"}}
    assert "view_args" not in t2
    inactive = await ip._llm_decompose(_JsonLLM(payload), "q", _cfg(active=False))
    assert all("view_args" not in t for t in inactive["tasks"])


# ── 5. W0-B 잔여 — 대화를 다시 불러도 작업 카드 ───────────────────────────────

def test_thread_turn_keeps_disclosures_for_job_cards() -> None:
    from src.api.thread_history import TurnRecorder

    class _Repo:
        def __init__(self) -> None:
            self.turns: list[dict] = []

        async def add_turn(self, owner_key: str, turn: dict) -> None:
            self.turns.append(turn)

    repo = _Repo()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(thread_repo=repo)),
                              headers={})
    item = disc.make(disc.APM_JOB_ACCEPTED, "접수", source="task:t1",
                     ref={"apm_job_id": "0123456789abcdef0123456789abcdef"})
    recorder = TurnRecorder(request, {"sub": "alice"}, user_query="q", has_upload=False)
    asyncio.run(recorder.record({"thread_id": "th-1", "response": "접수", "disclosures": [item]}))
    assert repo.turns[0]["disclosures"] == [item]
    ddl = (ROOT / "src/infrastructure/thread_repository.py").read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS disclosures JSONB" in ddl, "기존 테이블에 열만 더한다"
    js = (ROOT / "src/static/js/app.js").read_text(encoding="utf-8")
    body = js[js.index("function showThreadTurns"):]
    body = body[:body.index("// 이어서 질의하면")]
    assert "disclosures: t.disclosures" in body, "다시 그린 답에도 작업 카드를 붙인다"
