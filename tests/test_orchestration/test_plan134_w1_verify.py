"""plans/134 W1 본체(M-1·M-2·M-3·M-9) 검증자 적대적 테스트.

구현 테스트(`test_plan134_w1_apm.py`)와 별개다.

확인하는 것(SPEC-apm-question-coverage §6.1~§6.3 · §7.1~§7.3 · §7.5):
  - view_args 형 위반 · 모르는 이름 · enum 밖 · 여러 보기 묶음 섞임 · 평면(보기 id 없는) 묶음 ·
    null 값.
  - 파서 `limit` → `n`(보기에 `n`이 있을 때만) · 「전체」 단어 매칭 부재.
  - 창: range 보기(app_health·runtime·slow_tx·events)는 기간을 자르지 않는다 · current 보기는
    `apm_current_only` · none 보기는 기간 무시 · 하루 넘게 지난 기간 문구.
  - 집계: 같은 보기를 여러 대상에 불러도 (보기, 대상)마다 한 항목(값이 서로 다름) · 판정 중복 제거 ·
    오류 유형 전부.
  - 결정적 줄(판정·집계)이 2단 최종 답에 실린다 — 단일 · 병합(공통 hostname) ·
    **병합 불성립(단계별)**
    · APM task 2개.

결함 재현은 `xfail(strict=True)`. 게이트웨이는 모의 MCP 세션(실 게이트웨이·LLM 0).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import AppConfig, DBHubConfig, LLMConfig
from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.orchestration import subagents
from src.orchestration.conditional_agents import sanitize_task_views
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 10, 2, 10, 0, 0)


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"},
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
def gw(monkeypatch):
    def install(replies: dict) -> _Gateway:
        g = _Gateway(replies)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", g.factory())
        return g

    return install


def _isolated(*hosts: str, time_range: dict | None = None, limit: Any = None,
              query: str = "") -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": time_range, "limit": limit},
            "conversation_context": {}, "thread_id": "th-v", "user_id": "alice",
            "user_query": query}


async def _run(views: list[str], *hosts: str, view_args: Any = None,
               time_range: dict | None = None, limit: Any = None, sub_query: str = "") -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": sub_query}
    if view_args is not None:
        task["view_args"] = view_args
    return await aq.run_apm_query(
        task, _isolated(*hosts, time_range=time_range, limit=limit, query=sub_query),
        llm=None, app_config=_cfg(), now=NOW)


def _view(vid: str):
    return {v.id: v for v in aq.apm_views()}[vid]


_ALL = {"apm_events": _env("apm_events", [{"level": "fatal"}]),
        "apm_slow_transactions": _env("apm_slow_transactions", [{"tx": 1}]),
        "apm_active_services": _env("apm_active_services", [{"tx": 1}]),
        "apm_app_health": _env("apm_app_health", [{"tps": 1}]),
        "apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1}]),
        "apm_resource_pool": _env("apm_resource_pool", [{"db_pool_active": 1}]),
        "apm_instance_map": _env("apm_instance_map", [{"hostname": "web01",
                                                       "match_confidence": "high"}])}
_CONDITION_KEYS = ("n", "full", "level", "level_mode", "error_type", "record")


# ── M-3 view_args 검증 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "ok", "value"), [
    ({"n": "abc"}, False, None), ({"n": 2.5}, False, None), ({"n": 2.0}, True, 2),
    ({"n": True}, False, None), ({"n": -1}, False, None), ({"n": " 7 "}, True, 7),
    ({"n": 10**12}, True, 10**12), ({"n": [5]}, False, None),
    ({"full": "yes"}, False, None), ({"full": 1}, False, None), ({"full": "TRUE"}, True, True),
    ({"full": False}, True, False),
    ({"level_mode": "Exact"}, True, "exact"), ({"level_mode": "max"}, False, None),
    ({"record": "errors"}, False, None), ({"record": "ERROR"}, True, "error"),
    ({"error_type": "out of memory"}, False, None),
    ({"error_type": "ERROR_OOM"}, True, "ERROR_OOM"),
    ({"level": "fatal; DROP"}, False, None), ({"level": ["fatal"]}, False, None),
    # W1 검증 L-4(팀 리드 결정) — `level`은 게이트웨이 계약 enum(fatal·warning·normal)이라
    # 선언 값으로 정규화한다(종전 식별자 형식 검증의 기대값 "Fatal"에서 바뀜 · 게이트웨이는
    # 대소문자 무시)
    ({"level": "Fatal"}, True, "fatal"),
])
def test_view_arg_type_violations(raw, ok, value) -> None:
    args, rejected = aq.validate_view_args(_view("apm.events"), raw)
    (name,) = raw
    if ok:
        assert args == {name: value} and rejected == []
    else:
        assert args == {} and len(rejected) == 1 and rejected[0].startswith(name)


def test_superscript_digit_is_rejected_not_raised() -> None:
    args, rejected = aq.validate_view_args(_view("apm.events"), {"n": "²"})
    assert args == {} and rejected


@pytest.mark.asyncio
async def test_mixed_bundles_stay_with_their_own_view(gw) -> None:
    g = gw(_ALL)
    res = await _run(["apm.events", "apm.slow_tx"], "web01", view_args={
        "apm.events": {"level": "fatal", "level_mode": "exact", "n": 3},
        "apm.slow_tx": {"n": 5, "level": "fatal", "record": "error"}})
    (ev,) = g.named("apm_events")
    (tx,) = g.named("apm_slow_transactions")
    assert (ev["level"], ev["level_mode"], ev["n"]) == ("fatal", "exact", 3)
    assert tx["n"] == 5 and "level" not in tx and "record" not in tx, "남의 보기 조건이 새지 않는다"
    kinds = [d for d in res["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert len(kinds) == 1 and "level" in kinds[0]["text"] and "record" in kinds[0]["text"]
    assert res["apm_query"]["unresolved"] == [{"view": "apm.slow_tx", "queried": True,
                                               "conditions": ["level(이 보기에 없는 조건)",
                                                              "record(이 보기에 없는 조건)"]}]


@pytest.mark.asyncio
async def test_unknown_enum_value_on_one_view_does_not_stop_other_view(gw) -> None:
    """한 보기의 무효 조건이 다른 보기 조회·조건을 막지 않는다.

    무효 보기 자체를 조회하는지는 H-2(팀 리드 판단)라 단언하지 않는다.
    """
    g = gw(_ALL)
    res = await _run(["apm.events", "apm.active"], "web01", view_args={
        "apm.events": {"level_mode": "below"}, "apm.active": {"n": 2}})
    assert g.named("apm_active_services")[0]["n"] == 2
    assert all("level_mode" not in a for a in g.named("apm_events"))
    assert any(d["kind"] == disc.APM_UNRESOLVED_CONDITION and "level_mode" in d["text"]
               for d in res["disclosures"])


@pytest.mark.asyncio
async def test_unknown_condition_only_still_queries_the_view(gw) -> None:
    g = gw(_ALL)
    res = await _run(["apm.app_health"], "web01",
                     view_args={"apm.app_health": {"duration": "3시간"}})
    assert g.named("apm_app_health"), "모르는 조건만 냈다고 보기를 버리지 않는다"
    assert any(d["kind"] == disc.APM_UNRESOLVED_CONDITION for d in res["disclosures"])


def test_active_output_skeleton_carries_views_and_view_args() -> None:
    import importlib

    ip = importlib.import_module("src.orchestration.intent_planner")
    prompt = ip._planner_system_prompt(_cfg())
    section = prompt[prompt.index("## 출력 형식"):]
    skeleton = section[section.index("```json"):section.index("```", section.index("```json") + 7)]
    assert '"views"' in skeleton and '"view_args"' in skeleton


@pytest.mark.asyncio
async def test_flat_view_args_are_not_silently_dropped(gw) -> None:
    g = gw(_ALL)
    plan = {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": "web01 fatal만",
                       "views": ["apm.events"],
                       "view_args": {"level": "fatal", "level_mode": "exact"}}]}
    sanitize_task_views(plan, _cfg())
    (task,) = plan["tasks"]
    res = await aq.run_apm_query(task, _isolated("web01"), llm=None, app_config=_cfg(), now=NOW)
    (args,) = g.named("apm_events")
    applied = args.get("level") == "fatal"
    disclosed = any(d["kind"] == disc.APM_UNRESOLVED_CONDITION
                    for d in res.get("disclosures") or [])
    assert applied or disclosed


@pytest.mark.asyncio
async def test_null_conditions_do_not_block_the_view(gw) -> None:
    g = gw(_ALL)
    res = await _run(["apm.slow_tx"], "web01",
                     view_args={"apm.slow_tx": {"n": None, "full": None}})
    assert g.named("apm_slow_transactions"), "미지정(null) 조건 때문에 조회를 막지 않는다"
    assert not any(d["kind"] == disc.APM_UNRESOLVED_CONDITION
                   for d in res.get("disclosures") or [])


@pytest.mark.asyncio
@pytest.mark.parametrize(("view", "tool", "limit", "expected"), [
    ("apm.events", "apm_events", 5, 5),
    ("apm.active", "apm_active_services", 3, 3),
    ("apm.app_health", "apm_app_health", 5, None),   # n 조건이 없는 보기
    ("apm.slow_tx", "apm_slow_transactions", True, None),  # bool 은 개수가 아니다
    ("apm.slow_tx", "apm_slow_transactions", 0, None),
    ("apm.slow_tx", "apm_slow_transactions", "5", None),
])
async def test_parser_limit_to_n_only_where_declared(gw, view, tool, limit, expected) -> None:
    g = gw(_ALL)
    await _run([view], "web01", limit=limit)
    assert g.named(tool)[0].get("n") == expected


@pytest.mark.asyncio
async def test_no_word_matching_for_conditions(gw) -> None:
    g = gw(_ALL)
    words = "web01 fatal만 정확히 · 상위 5개 · 전부 모두 전체 · OutOfMemory 오류 기록만 · error"
    await _run(["apm.events", "apm.slow_tx", "apm.active"], "web01", sub_query=words)
    for tool in ("apm_events", "apm_slow_transactions", "apm_active_services"):
        args = g.named(tool)[0]
        assert not set(args) & set(_CONDITION_KEYS), (tool, args)


# ── M-2 창 ─────────────────────────────────────────────────────────────────


_SIX_HOURS = {"start": "2026-10-02 03:30", "end": "2026-10-02 09:30"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("view", "tool"), [
    ("apm.app_health", "apm_app_health"), ("apm.runtime", "apm_runtime_health"),
    ("apm.slow_tx", "apm_slow_transactions"), ("apm.events", "apm_events")])
async def test_range_views_pass_the_whole_period(gw, view, tool) -> None:
    g = gw(_ALL)
    res = await _run([view], "web01", time_range=_SIX_HOURS)
    args = g.named(tool)[0]
    assert args["lookback_minutes"] == 360 and args["reference_time"] == "2026-10-02T09:30:00"
    blob = json.dumps(res, ensure_ascii=False)
    assert "현재값 기준" not in blob and "상한" not in blob, "C-2 오고지·상한 문구 없음"
    assert not any(d["kind"] == disc.APM_CURRENT_ONLY for d in res.get("disclosures") or [])


@pytest.mark.asyncio
async def test_twenty_hour_event_window_is_not_cut(gw) -> None:
    g = gw(_ALL)
    await _run(["apm.events"], "web01",
               time_range={"start": "2026-10-01 14:00", "end": "2026-10-02 10:00"})
    args = g.named("apm_events")[0]
    assert args["lookback_minutes"] == 20 * 60 and args.get("reference_time") is None


@pytest.mark.asyncio
async def test_current_and_none_views_with_period(gw) -> None:
    g = gw(_ALL)
    res = await _run(["apm.active", "apm.pool"], "web01", time_range=_SIX_HOURS)
    for tool in ("apm_active_services", "apm_resource_pool"):
        args = g.named(tool)[0]
        assert "lookback_minutes" not in args and "reference_time" not in args
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds.count(disc.APM_CURRENT_ONLY) == 2
    g2 = gw(_ALL)
    res2 = await _run(["apm.instances"], time_range=_SIX_HOURS)
    (args,) = g2.named("apm_instance_map")
    assert "lookback_minutes" not in args
    assert not res2.get("disclosures"), "목록 보기는 기간을 무시하고 고지하지 않는다"


@pytest.mark.asyncio
async def test_more_than_a_day_ago_is_not_queried_with_fact_wording(gw) -> None:
    g = gw(_ALL)
    res = await _run(["apm.slow_tx"], "web01",
                     time_range={"start": "2026-09-29 09:00", "end": "2026-09-29 10:00"})
    assert g.named("apm_slow_transactions") == []
    assert aq.OUT_OF_WINDOW_NOTE in res["final_response"]
    assert "W6" not in res["final_response"]


# ── M-1 집계 운반 · 결정적 줄 ────────────────────────────────────────────


def _slow_by_host(args: dict) -> dict:
    host = args["hostname"]
    calls = {"web01": 111, "web02": 222, "web03": 333}[host]
    sigs = [{"kind": "was_slow_sql", "level": "WARNING", "category": "medium",
             "label": "SQL 지연(SQL·Fetch 비중 과다)", "evidence": f"share {host}",
             "instance_id": calls, "source_id": "s1"}]
    return _env("apm_slow_transactions", [{"instance_id": calls, "response_time_ms": calls}],
                summary={"calls": calls, "errors": 1, "error_rate": 1 / calls,
                         "response_time_p50_ms": 10.0, "response_time_p95_ms": 20.0},
                was_signals=sigs)


@pytest.mark.asyncio
async def test_same_view_many_targets_keeps_each_aggregate(gw) -> None:
    gw({"apm_slow_transactions": _slow_by_host})
    res = await _run(["apm.slow_tx"], "web01", "web02", "web03")
    aggs = {a["hostname"]: a["summary"]["calls"] for a in res["apm_query"]["aggregates"]}
    assert aggs == {"web01": 111, "web02": 222, "web03": 333}
    lines = res["answer_lines"]
    for host, calls in (("web01", 111), ("web02", 222), ("web03", 333)):
        assert any(f"· {host}: 구간 호출 {calls}건" in line for line in lines), host
        assert any(f"— {host} #{calls}: share {host}" in line for line in lines), host
    assert len(res["apm_query"]["was_signals"]) == 3, "인스턴스가 다르면 판정도 각각"


@pytest.mark.asyncio
async def test_all_error_types_are_listed_without_cut(gw) -> None:
    types = [{"error_type": f"TYPE_{i:02d}", "count": 30 - i} for i in range(25)]
    gw({"apm_events": _env("apm_events", [], errors_by_type=types)})
    res = await _run(["apm.events"], "web01")
    (line,) = [x for x in res["answer_lines"] if "오류 유형별" in x]
    assert all(f"TYPE_{i:02d} {30 - i}건" in line for i in range(25))


# ── 2단 최종 답 ───────────────────────────────────────────────────────────


def _app_config() -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db",
                          source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
        checkpoint_backend="sqlite", checkpoint_db_url=":memory:",
    )


def _data_handler(rows: list[dict]):
    async def handler(task, isolated, *, llm, app_config):
        return {"organized_data": {"summary": f"{len(rows)}건", "rows": rows,
                                   "column_mapping": None, "resolved_mapping": None,
                                   "is_sufficient": True, "sheet_mappings": None},
                "query_results": rows, "target_db_ids": ["polestar_cm_gp"]}
    return handler


async def _two_tier(plan: list[dict], monkeypatch, data_rows: list[dict]) -> dict:
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.replanner import replanner
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=_data_handler(data_rows)))
    config = _app_config()
    query = "web01 web02 WAS 상태"
    state = create_initial_state(user_query=query, thread_id="th-v", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({
        "parsed_requirements": {"filter_conditions": [
            {"field": "hostname", "op": "=", "value": "web01"}], "original_query": query},
        "task_plan": plan, "task_results": {}, "replan_count": 0,
    })
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"] * 8),
                                          app_config=config))
    no_followup = json.dumps({"needs_followup": False, "reason": "충분", "new_tasks": []})
    state.update(await replanner(state, llm=FakeListChatModel(responses=[no_followup]),
                                 app_config=config))
    return await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 8),
                                   app_config=config, synthesize=True, composite_answer="steps")


def _task(tid: str, agent: str, sub: str, **extra: Any) -> dict:
    return {"task_id": tid, "agent": agent, "sub_query": sub, "order": int(tid[1:]),
            "depends_on": [], "input_from": [], "status": "pending", **extra}


@pytest.mark.asyncio
async def test_two_tier_lines_survive_non_mergeable_composite(gw, monkeypatch) -> None:
    """공통 서버 키가 없는 복합(단계별 이어붙이기 `_finalize_steps`)에서도 결정적 줄이 실린다."""
    gw({"apm_slow_transactions": _slow_by_host})
    plan = [_task("t1", "data_query", "알람 건수 집계"),
            _task("t2", "apm_query", "web01 느린 트랜잭션", views=["apm.slow_tx"])]
    out = await _two_tier(plan, monkeypatch, data_rows=[{"severity": "major", "cnt": 3}])
    answer = out["final_response"]
    assert "**판정·집계**" in answer
    assert "구간 호출 111건" in answer and "share web01" in answer


@pytest.mark.asyncio
async def test_two_tier_lines_from_two_apm_tasks(gw, monkeypatch) -> None:
    gw({"apm_slow_transactions": _slow_by_host,
        "apm_events": _env("apm_events", [{"level": "fatal", "instance_id": 7}],
                           errors_by_type=[{"error_type": "OUTOFMEMORY", "count": 4}])})
    plan = [_task("t1", "apm_query", "web01 느린 트랜잭션", views=["apm.slow_tx"]),
            _task("t2", "apm_query", "web01 이벤트", views=["apm.events"])]
    out = await _two_tier(plan, monkeypatch, data_rows=[])
    answer = out["final_response"]
    assert "구간 호출 111건" in answer and "OUTOFMEMORY 4건" in answer


@pytest.mark.asyncio
async def test_no_lines_means_byte_identical_text(gw, monkeypatch) -> None:
    """결정적 줄이 없는 APM 결과(행만)는 블록을 붙이지 않는다(종전 본문 그대로)."""
    gw({"apm_resource_pool": _env("apm_resource_pool", [{"db_pool_active": 1}])})
    plan = [_task("t1", "apm_query", "web01 커넥션 풀", views=["apm.pool"])]
    out = await _two_tier(plan, monkeypatch, data_rows=[])
    assert "**판정·집계**" not in out["final_response"]
