"""plans/134 W2 본체 — 통계·지표 목록·소스 변경 보기 · 선택 재시도(M-8) · 표시 절단.

고정하는 계약(SPEC-apm-question-coverage §6.2 W2 행 · §6.3 · §6.4 · §7.5):
  1. 보기 5종(`apm.app_stats`·`apm.sql_stats`·`apm.external_stats` = `apm_status_stats` + 고정
     `kind` · hourly | `apm.metrics` = `apm_metrics(mode=catalog)` · 대상 없음 | `apm.changes` =
     `apm_source_changes` · range)과 `apm.runtime`의 `metrics`·`interval_minute` 조건이 SPEC 인자
     이름 그대로 도구에 실린다. 대상 없는 보기는 첫 홉·대상별 호출을 하지 않는다.
  2. 고지: 통계 보기 = `apm_hourly_resolution`(기간이 없어도) · 변경 보기 = `apm_change_detection`.
  3. 새 답변 영역 3종은 APM 이 활성일 때만 분해 영역 카탈로그에 렌더된다(비활성 바이트 불변).
  4. M-8: 분해 보기가 task 영역(APM 소유)을 덮으면 LLM 0 · 일반 현황은 기본 보기 · 못 덮으면
     보기 카탈로그만 담은 선택 프롬프트로 1회 재시도 → 일부 영역만 덮으면 덮은 보기는 조회하고 못
     덮은 영역만 후보 ≤3으로 되묻는다(의무 고지) · 덮은 영역이 없으면 조회하지 않고 되묻는다 ·
     원문 단어로 보기를 고르지 않는다 · 메타 `apm_query.selection`.
  5. 긴 셀은 채팅 표·LLM 입력에서만 줄이고 전문은 `query_results`(CSV)에 남는다 — 폴스타 결과와
     hostname으로 병합한 표의 CSV도 전문이다.
  6. 게이트웨이 W2 계약 대조: 통계 `summary`(실패·평균·최대 · `hour_start`/`hour_end`)가 결정적 줄로
     · 봉투 `disclosures`는 등록 kind만 · 감사 `commands`에 고정 인자·조건.
게이트웨이는 모의 MCP 세션이다(실 게이트웨이 · 실 LLM 0 — 선택 LLM 도 대역). 봉투 모양은 작업 트리의
게이트웨이(`apm_gateway/application/tools.py` `apm_status_stats`·`apm_source_changes`·
`jobs.masked_disclosure`)와 같다.
"""

from __future__ import annotations

import importlib
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import DBHubConfig
from src.domain import disclosure as disc
from src.orchestration import apm_query as aq
from src.routing.registry import get_registry

ip = importlib.import_module("src.orchestration.intent_planner")

NOW = datetime(2026, 10, 2, 10, 0, 0)
NEW_AREAS = ("was_statistics", "apm_metric_catalog", "was_change_detection")


def _cfg(*, active: bool = True) -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None,
                        source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if active else {},
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
        if callable(reply):
            reply = reply(arguments)
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


class _SelectLLM:
    """선택 재시도 LLM 대역 — 부른 횟수·메시지를 남긴다(`reply`가 None 이면 부르면 실패)."""

    def __init__(self, reply: dict | str | Exception | None) -> None:
        self.reply = reply
        self.messages: list[Any] = []

    async def ainvoke(self, messages):
        self.messages.append(messages)
        if self.reply is None:
            raise AssertionError("재시도가 필요 없는 경우에 LLM 을 불렀다")
        if isinstance(self.reply, Exception):
            raise self.reply
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply,
                                                                          ensure_ascii=False)
        return SimpleNamespace(content=text)


def _isolated(*hosts: str, time_range: dict | None = None) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts],
                "time_range": time_range, "limit": None},
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice"}


async def _run(views: list[str], *hosts: str, view_args: dict | None = None,
               areas: list[str] | None = None, sub_query: str = "질의", llm: Any = None,
               time_range: dict | None = None) -> dict:
    task: dict[str, Any] = {"task_id": "t1", "agent": "apm_query", "views": views,
                            "sub_query": sub_query}
    if view_args is not None:
        task["view_args"] = view_args
    if areas is not None:
        task["areas"] = areas
    return await aq.run_apm_query(task, _isolated(*hosts, time_range=time_range), llm=llm,
                                  app_config=_cfg(), now=NOW)


# ── 1. 레지스트리 · 인자 ──────────────────────────────────────────────────────

def test_w2_views_follow_spec_rows() -> None:
    views = {v.id: v for v in aq.apm_views()}
    for vid, kind in (("apm.app_stats", "application"), ("apm.sql_stats", "sql"),
                      ("apm.external_stats", "external_call")):
        view = views[vid]
        assert (view.tool, view.window, view.fixed_args, view.required_input) == (
            "apm_status_stats", "hourly", {"kind": kind}, "hostname"), vid
        assert [a.name for a in view.args][:3] == ["sort_by", "n", "full"]
    assert [a.name for a in views["apm.app_stats"].args][-1] == "application_name"
    metrics = views["apm.metrics"]
    assert (metrics.tool, metrics.window, metrics.fixed_args, metrics.required_input,
            metrics.first_hop) == ("apm_metrics", "none", {"mode": "catalog"}, "", False)
    (scope,) = metrics.args
    assert scope.choices == ("domain", "instance", "business", "application", "sql",
                             "external_call")
    changes = views["apm.changes"]
    assert (changes.tool, changes.window, changes.notices) == (
        "apm_source_changes", "range", ("apm_change_detection",))
    runtime = views["apm.runtime"]
    assert [(a.name, a.type, a.catalog) for a in runtime.args] == [
        ("metrics", "str_list", "instance"), ("interval_minute", "int", None)]
    reg = get_registry()
    for code in NEW_AREAS:
        assert reg.capability_owners(code) == ("apm",)
    assert {"was_statistics", "apm_metric_catalog", "was_change_detection"} <= {
        v.capability for v in views.values()}


@pytest.mark.asyncio
async def test_stats_view_carries_fixed_kind_conditions_and_hourly_notice(gateway) -> None:
    gw = gateway({"apm_status_stats": _env("apm_status_stats", [{"name": "/order", "calls": 3}])})
    res = await _run(["apm.app_stats"], "web01", view_args={"apm.app_stats": {
        "sort_by": "calls", "n": 10, "application_name": "/order/list.jsp"}})
    (args,) = gw.named("apm_status_stats")
    for key, value in {"kind": "application", "sort_by": "calls", "n": 10,
                       "application_name": "/order/list.jsp", "hostname": "web01"}.items():
        assert args[key] == value, key
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_HOURLY_RESOLUTION, "기간이 없어도 시 단위 고지"


@pytest.mark.asyncio
async def test_metrics_view_has_no_first_hop_and_no_per_host_calls(gateway) -> None:
    gw = gateway({"apm_metrics": _env("apm_metrics", [{"scope": "instance", "name": "heap"}]),
                  "apm_instance_map": _env("apm_instance_map", [])})
    await _run(["apm.metrics"], view_args={"apm.metrics": {"scope": "instance"}})
    assert gw.named("apm_instance_map") == [], "대상 없는 보기는 인스턴스 목록을 먼저 부르지 않는다"
    (args,) = gw.named("apm_metrics")
    assert args["mode"] == "catalog" and args["scope"] == "instance" and "hostname" not in args
    gw2 = gateway({"apm_metrics": _env("apm_metrics", [{"name": "heap"}]),
                   "apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    await _run(["apm.metrics", "apm.app_health"], "web01", "web02")
    assert len(gw2.named("apm_metrics")) == 1, "대상이 있어도 전체 보기는 한 번"
    assert len(gw2.named("apm_app_health")) == 2


@pytest.mark.asyncio
async def test_changes_view_passes_period_and_discloses_detection(gateway) -> None:
    gw = gateway({"apm_source_changes": _env("apm_source_changes", [
        {"source_id": "default", "domain_id": 1000, "instance_id": 11, "instance_name": "web01",
         "change_detected_ms": 1759341600000}])})
    res = await _run(["apm.changes"], "web01",
                     time_range={"start": "2026-10-01 10:00", "end": "2026-10-02 09:00"})
    args = gw.named("apm_source_changes")[0]
    assert args["lookback_minutes"] == 23 * 60
    kinds = [d["kind"] for d in res["disclosures"]]
    assert kinds == [disc.APM_CHANGE_DETECTION]
    assert "배포 시각으로 확정할 수 없습니다" in res["disclosures"][0]["text"]


@pytest.mark.asyncio
async def test_runtime_metrics_and_interval_conditions(gateway) -> None:
    gw = gateway({"apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1}])})
    await _run(["apm.runtime"], "web01", view_args={"apm.runtime": {
        "metrics": ["heap_used", "gc_time"], "interval_minute": 10}})
    args = gw.named("apm_runtime_health")[0]
    assert args["metrics"] == ["heap_used", "gc_time"] and args["interval_minute"] == 10
    res = await _run(["apm.runtime"], "web01", view_args={"apm.runtime": {
        "metrics": ["heap used"], "interval_minute": 10}})
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and "metrics" in item["text"]


def test_text_condition_rejects_control_chars_and_overlong() -> None:
    view = {v.id: v for v in aq.apm_views()}["apm.app_stats"]
    assert aq.validate_view_args(view, {"application_name": "/a/b?c=1"})[0] == {
        "application_name": "/a/b?c=1"}
    for bad in ("x\ny", "a" * 201, "", 3):
        assert aq.validate_view_args(view, {"application_name": bad})[0] == {}, bad


# ── 2. 답변 영역 — 활성일 때만 ─────────────────────────────────────────────────

def test_new_areas_render_only_when_apm_is_active() -> None:
    inactive = ip._planner_system_prompt(_cfg(active=False))
    active = ip._planner_system_prompt(_cfg(active=True))
    for code in NEW_AREAS:
        assert f"| {code} |" not in inactive, "비활성 배포 바이트 불변"
        assert f"| {code} |" in active
    for vid in ("`apm.app_stats`", "`apm.metrics`", "`apm.changes`"):
        assert vid in active
    assert "`metrics` = 이름 목록(instance 지표)" in active


# ── 3. M-8 선택 재시도 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_covered_areas_need_no_llm(gateway) -> None:
    gateway({"apm_status_stats": _env("apm_status_stats", [{"calls": 1}])})
    llm = _SelectLLM(None)
    res = await _run(["apm.sql_stats"], "web01", areas=["was_statistics"], llm=llm)
    assert llm.messages == []
    assert res["apm_query"]["selection"] == {"areas": ["was_statistics"],
                                             "planned": ["apm.sql_stats"], "retried": False,
                                             "result": "planned"}


@pytest.mark.asyncio
async def test_general_area_without_views_keeps_default_view(gateway) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}])})
    llm = _SelectLLM(None)
    res = await _run([], "web01", areas=["was_performance"], llm=llm)
    assert llm.messages == [] and gw.named("apm_app_health"), "D-293 기본 보기 유지"
    assert res["apm_query"]["selection"]["result"] == "default"


@pytest.mark.asyncio
async def test_uncovered_area_retries_once_with_catalog_prompt(gateway) -> None:
    gw = gateway({"apm_status_stats": _env("apm_status_stats", [{"name": "select 1"}])})
    llm = _SelectLLM({"views": ["apm.sql_stats"], "view_args": {"apm.sql_stats": {"n": 5}}})
    res = await _run([], "web01", areas=["was_statistics"], llm=llm,
                     sub_query="web01 많이 호출된 SQL 다섯 개")
    assert len(llm.messages) == 1, "재시도는 1회"
    system, human = llm.messages[0]
    assert "`apm.sql_stats`" in system.content and "조건(view_args)" in system.content
    assert "WAS 시 단위 통계" in system.content, "요청 영역 라벨"
    assert human.content == "web01 많이 호출된 SQL 다섯 개"
    (args,) = gw.named("apm_status_stats")
    assert args["kind"] == "sql" and args["n"] == 5
    sel = res["apm_query"]["selection"]
    assert sel["retried"] is True and sel["result"] == "retried"
    assert sel["retried_views"] == ["apm.sql_stats"] and sel["latency_ms"] >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [
    {"views": ["apm.instances"]},           # 여전히 못 덮음(분해와 다른 목록 보기로 대신하려 함)
    {"views": []},                          # 맞는 보기 없음
    "보기를 못 골랐어요",                     # JSON 아님
    RuntimeError("llm down"),               # 호출 실패
], ids=["still_uncovered", "empty", "not_json", "error"])
async def test_still_uncovered_is_asked_back_without_querying(gateway, reply) -> None:
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}]),
                  "apm_status_stats": _env("apm_status_stats", [])})
    res = await _run(["apm.app_health"], "web01", areas=["was_statistics"],
                     llm=_SelectLLM(reply))
    assert gw.calls == [], "명시 기능을 응답시간·목록 보기로 바꿔 조회하지 않는다"
    assert res["degraded_reason"] == "apm_unresolved_selection"
    text = res["final_response"]
    assert "조회하지 않았습니다" in text and "다시 물어 주세요" in text
    for label in ("URL(애플리케이션)별 시 단위 통계", "SQL별 시 단위 통계",
                  "외부 호출(연계 대상)별"):
        assert label in text
    (item,) = res["disclosures"]
    assert item["kind"] == disc.APM_UNRESOLVED_CONDITION and item["text"] == text
    sel = res["apm_query"]["selection"]
    assert sel["result"] == "unresolved" and sel["retried"] is True
    assert sel["candidates"] == ["apm.app_stats", "apm.sql_stats", "apm.external_stats"]


@pytest.mark.asyncio
async def test_views_are_never_picked_by_words(gateway) -> None:
    """132 계약 — 원문 단어(「SQL」·「통계」·「변경」)로 보기를 고르지 않는다."""
    gw = gateway({"apm_app_health": _env("apm_app_health", [{"tps": 1}]),
                  "apm_external_stats": _env("apm_status_stats", []),
                  "apm_status_stats": _env("apm_status_stats", [{"calls": 1}])})
    llm = _SelectLLM(None)
    await _run([], "web01", sub_query="web01 SQL 통계와 소스 변경 지표 목록", llm=llm)
    assert llm.messages == [] and [n for n, _ in gw.calls] == ["apm_app_health"], \
        "영역 신호가 없으면 단어와 무관하게 기본 보기"
    llm2 = _SelectLLM({"views": ["apm.external_stats"]})
    await _run([], "web01", areas=["was_statistics"], sub_query="web01 SQL 통계", llm=llm2)
    assert gw.named("apm_status_stats")[-1]["kind"] == "external_call", \
        "재시도 결과(LLM 선택)를 그대로 쓴다 — 원문 「SQL」로 바꾸지 않는다"


# ── 4. 표시 절단 ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_long_cells_are_cut_for_display_but_kept_in_csv_rows(gateway) -> None:
    long_sql = "select " + "x, " * 300
    gateway({"apm_status_stats": _env("apm_status_stats", [{"name": long_sql, "calls": 2}])})
    res = await _run(["apm.sql_stats"], "web01")
    shown = res["organized_data"]["rows"][0]["name"]
    assert len(shown) < len(long_sql) and shown.startswith(long_sql[:aq.DISPLAY_CELL_MAX])
    assert f"총 {len(long_sql):,}자" in shown
    assert res["query_results"][0]["name"] == long_sql, "CSV 원천에는 전문"
    gateway({"apm_status_stats": _env("apm_status_stats", [{"name": "select 1", "calls": 2}])})
    short = await _run(["apm.sql_stats"], "web01")
    assert short["organized_data"]["rows"][0] is short["query_results"][0], \
        "줄일 셀이 없으면 같은 행(종전 바이트)"


@pytest.mark.asyncio
async def test_two_tier_retry_uses_orchestrator_llm_and_planner_areas(gateway) -> None:
    """분해(`areas` 정제) → 오케스트레이터(같은 LLM 전달) → 처리기 재시도까지 배선."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.config import AppConfig, LLMConfig
    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.state import create_initial_state

    gw = gateway({"apm_status_stats": _env("apm_status_stats", [{"name": "select 1"}])})
    config = AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
                       dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                                         source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
                       checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    plan = {"tasks": [{"task_id": "t1", "agent": "apm_query", "sub_query": "web01 SQL 통계",
                       "views": [], "areas": ["was_statistics"]}]}
    decomposed = await ip._llm_decompose(_SelectLLM(plan), "web01 SQL 통계", config)
    assert decomposed["tasks"][0]["areas"] == ["was_statistics"]
    state = create_initial_state(user_query="web01 SQL 통계", thread_id="th-1", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "original_query": "q"},
        "task_plan": decomposed["tasks"], "task_results": {}, "replan_count": 0})
    selection = json.dumps({"views": ["apm.sql_stats"]})
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=[selection]),
                                          app_config=config))
    assert gw.named("apm_status_stats")[0]["kind"] == "sql"
    assert state["task_results"]["t1"]["apm_query"]["selection"]["result"] == "retried"


# ── 5. 결정 3 — 일부 영역만 덮으면 덮은 보기는 조회 ─────────────────────────────

_PARTIAL_REPLIES = {"apm_app_health": _env("apm_app_health", [{"tps": 1}]),
                    "apm_status_stats": _env("apm_status_stats", [{"name": "select 1"}]),
                    "apm_source_changes": _env("apm_source_changes", [])}


@pytest.mark.asyncio
async def test_retry_covering_some_areas_queries_them_and_asks_the_rest(gateway) -> None:
    gw = gateway(_PARTIAL_REPLIES)
    llm = _SelectLLM({"views": ["apm.sql_stats"], "view_args": {"apm.sql_stats": {"n": 3}}})
    res = await _run(["apm.app_health"], "web01", llm=llm,
                     areas=["was_performance", "was_statistics", "was_change_detection"])
    assert len(llm.messages) == 1
    assert [n for n, _ in gw.calls] == ["apm_app_health", "apm_status_stats"], "덮은 보기만"
    assert gw.named("apm_status_stats")[0]["n"] == 3
    assert "degraded_reason" not in res and res["query_results"], "정상 답을 버리지 않는다"
    (item,) = [d for d in res["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert disc.KIND_TABLE[item["kind"]].mandatory
    assert "그 부분은 조회하지 않았습니다" in item["text"]
    assert "WAS 소스(리소스) 변경 감지 시각" in item["text"] and "다시 물어 주세요" in item["text"]
    sel = res["apm_query"]["selection"]
    # plans/134 W6 — 변경 전후 비교(`apm.change_impact`)가 같은 영역을 재사용한다(의도된 갱신)
    assert (sel["result"], sel["uncovered"], sel["candidates"]) == (
        "partial", ["was_change_detection"], ["apm.changes", "apm.change_impact"])
    assert res["apm_query"]["views"] == ["apm.app_health", "apm.sql_stats"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("planned", "reply"), [
    (["apm.app_health"], {"views": []}),                     # 재시도도 못 고름
    (["apm.app_health"], {"views": ["apm.metrics"]}),        # 요청 밖 보기 — 대신 조회하지 않는다
    (["apm.app_health"], RuntimeError("llm down")),          # 재시도 실패
    ([], {"views": []}),                                     # 분해 보기 없음 → 기본 보기가 덮는다
], ids=["empty", "off_area", "error", "default_view"])
async def test_planned_cover_survives_failed_retry(gateway, planned, reply) -> None:
    gw = gateway({**_PARTIAL_REPLIES, "apm_metrics": _env("apm_metrics", [])})
    res = await _run(planned, "web01", llm=_SelectLLM(reply),
                     areas=["was_performance", "was_statistics"])
    assert [n for n, _ in gw.calls] == ["apm_app_health"]
    assert res["query_results"] and res["apm_query"]["selection"]["result"] == "partial"
    (item,) = [d for d in res["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert "SQL별 시 단위 통계" in item["text"]


@pytest.mark.asyncio
async def test_planner_and_retry_agreeing_off_label_view_is_queried(gateway) -> None:
    """분해·재시도가 영역 라벨 밖의 같은 보기를 내면 라벨보다 보기를 믿는다(W2 검증 B7)."""
    gw = gateway({**_PARTIAL_REPLIES,
                  "apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1}])})
    args = {"apm.runtime": {"interval_minute": 10}}
    res = await _run(["apm.runtime"], "web01", areas=["apm_metric_catalog"], view_args=args,
                     llm=_SelectLLM({"views": ["apm.runtime"], "view_args": args}))
    assert [n for n, _ in gw.calls] == ["apm_runtime_health"]
    assert gw.named("apm_runtime_health")[0]["interval_minute"] == 10
    sel = res["apm_query"]["selection"]
    assert (sel["result"], sel["agreed"]) == ("agreed", ["apm.runtime"])
    assert not [d for d in res.get("disclosures") or []
                if d["kind"] == disc.APM_UNRESOLVED_CONDITION], "틀린 라벨로 되묻지 않는다"


def test_unresolved_candidates_cover_each_area() -> None:
    """미해결 영역이 여럿이면 영역마다 후보 1개 이상(합계 ≤3 · W2 검증 B3)."""
    two = [v.id for v in aq._candidates(["was_statistics", "was_change_detection"])]
    assert two == ["apm.app_stats", "apm.changes", "apm.sql_stats"]
    four = aq._candidates(["was_change_detection", "apm_metric_catalog", "was_statistics",
                           "was_runtime"])
    assert [v.capability for v in four] == [
        "was_change_detection", "apm_metric_catalog", "was_statistics"]
    assert [v.id for v in aq._candidates(["was_statistics"])] == [
        "apm.app_stats", "apm.sql_stats", "apm.external_stats"], "한 영역이면 종전 그대로"


# ── 6. 게이트웨이 W2 계약 대조 ──────────────────────────────────────────────────

def _stats_envelope(rows: list[dict]) -> dict:
    """`apm_status_stats` 봉투 — 게이트웨이 `_status_summary`·`hour_start`/`hour_end` 모양."""
    calls = sum(r["calls"] for r in rows)
    failures = sum(r["failures"] for r in rows)
    return _env("apm_status_stats", rows, kind="sql",
                hour_start="2026-10-02T07:00:00+09:00", hour_end="2026-10-02T10:00:00+09:00",
                summary={"row_count": len(rows), "calls": calls, "failures": failures,
                         "failure_rate": failures / calls, "bad_responses": 0,
                         "response_time_avg_ms": sum(r["total_response_ms"] for r in rows) / calls,
                         "max_response_time_ms": max(r["max_response_time_ms"] for r in rows)})


@pytest.mark.asyncio
async def test_stats_summary_becomes_an_hourly_answer_line(gateway) -> None:
    rows = [{"source_id": "default", "domain_id": 1000, "name": "select ?", "calls": 300,
             "failures": 3, "bad_responses": 0, "response_time_avg_ms": 20.0,
             "max_response_time_ms": 900, "total_response_ms": 6000},
            {"source_id": "default", "domain_id": 1000, "name": "update ?", "calls": 100,
             "failures": 1, "bad_responses": 0, "response_time_avg_ms": 40.0,
             "max_response_time_ms": 400, "total_response_ms": 4000}]
    gateway({"apm_status_stats": _stats_envelope(rows)})
    res = await _run(["apm.sql_stats"], "web01")
    (line,) = [x for x in res["answer_lines"] if "시 단위 합계" in x]
    assert ("(2026-10-02T07:00:00+09:00~2026-10-02T10:00:00+09:00)" in line
            and "호출 400건" in line and "실패 4건" in line and "실패율 1.0%" in line
            and "평균 25ms" in line and "최대 900ms" in line and "받은 2행의 합계" in line), line
    assert not any("구간 호출" in x for x in res["answer_lines"]), "창 집계 문구로 잘못 읽지 않는다"


@pytest.mark.asyncio
async def test_gateway_disclosures_pass_only_registered_kinds(gateway) -> None:
    gateway({"apm_events": _env("apm_events", [{"level": "fatal"}], disclosures=[
        {"kind": "apm_masked_fields",
         "text": "개인정보·자격증명 보호를 위해 값을 가린 칸: user_id"},
        {"kind": "made_up_kind", "text": "x"}, {"kind": "apm_masked_fields"}, "junk"])})
    res = await _run(["apm.events"], "web01", "web02")
    items = [d for d in res["disclosures"] if d["kind"] == disc.APM_MASKED_FIELDS]
    assert len(items) == 1, "대상 둘 — 같은 문구는 한 번"
    assert items[0]["text"].endswith("user_id") and items[0]["source"] == "task:t1"
    assert not any(d["kind"] == "made_up_kind" for d in res["disclosures"])


@pytest.mark.asyncio
async def test_audit_commands_carry_fixed_args_and_conditions(gateway) -> None:
    from src.orchestration.investigation_audit import _apm_query_fields

    gateway({"apm_status_stats": _env("apm_status_stats", [{"name": "select 1"}]),
             "apm_runtime_health": _env("apm_runtime_health", [{"heap_used_mb": 1}])})
    res = await _run(["apm.sql_stats", "apm.runtime"], "web01", view_args={
        "apm.sql_stats": {"n": 5, "sort_by": "calls"},
        "apm.runtime": {"metrics": ["heap_used"]}})
    assert _apm_query_fields(res)["commands"] == [
        "apm_status_stats(hostname=web01, kind=sql, n=5, sort_by=calls)",
        'apm_runtime_health(hostname=web01, metrics=["heap_used"])']


# ── 7. 병합 경로 CSV 원천 = 전문 ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_merged_table_csv_keeps_full_cells(gateway, monkeypatch) -> None:
    """폴스타 결과와 hostname으로 병합한 표 — 표시는 줄인 셀, CSV(`query_results`)는 전문."""
    from dataclasses import replace

    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.config import AppConfig, LLMConfig
    from src.orchestration import subagents
    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    long_sql = "select " + "x, " * 300
    gateway({"apm_status_stats": _env("apm_status_stats", [{"name": long_sql, "calls": 2}])})
    db_rows = [{"hostname": "web01", "cpu_usage": 51.0}]

    async def data_handler(task, isolated, *, llm, app_config):
        return {"organized_data": {"summary": "1건", "rows": db_rows, "column_mapping": None,
                                   "resolved_mapping": None, "is_sufficient": True,
                                   "sheet_mappings": None},
                "query_results": db_rows, "target_db_ids": ["polestar_cm_gp"]}

    spec = subagents.SUBAGENT_REGISTRY["data_query"]
    monkeypatch.setitem(subagents.SUBAGENT_REGISTRY, "data_query",
                        replace(spec, handler=data_handler))
    config = AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
                       dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                                         source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
                       checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    state = create_initial_state(user_query="web01 CPU 와 SQL 통계", thread_id="th-1",
                                 user_id="alice", user_role="user", allowed_sources=None)
    plan = [{"task_id": "t1", "agent": "data_query", "sub_query": "web01 CPU", "order": 1,
             "depends_on": [], "input_from": [], "status": "pending"},
            {"task_id": "t2", "agent": "apm_query", "sub_query": "web01 SQL 통계", "order": 2,
             "depends_on": [], "input_from": [], "status": "pending",
             "views": ["apm.sql_stats"]}]
    state.update({"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "original_query": "q"},
        "task_plan": plan, "task_results": {}, "replan_count": 0})
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"] * 4),
                                          app_config=config))
    assert state["task_results"]["t2"]["display_rows_cut"] is True
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 8),
                                  app_config=config, synthesize=True, composite_answer="steps")
    (row,) = out["query_results"]
    assert row["name"] == long_sql and row["cpu_usage"] == 51.0, "병합 CSV 원천은 전문"


# ── 8. 비의무 처리기 고지 — 2단 최종 답 본문(W2 검증 B1 · plans/123 W-9) ─────────────

async def _two_tier_answer(task: dict[str, Any], *, time_range: dict | None = None) -> tuple[
        dict, dict]:
    """2단 오케스트레이터 → 집계기(단계별) — (최종 결과, task 결과). LLM 은 대역."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from src.config import AppConfig, LLMConfig
    from src.orchestration.agent_orchestrator import agent_orchestrator
    from src.orchestration.result_aggregator import result_aggregator
    from src.state import create_initial_state

    config = AppConfig(_env_file=None, llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
                       dbhub=DBHubConfig(_env_file=None, server_url="http://localhost:9099/sse",
                                         source_endpoints={"apm": "http://127.0.0.1:9096/sse"}),
                       checkpoint_backend="sqlite", checkpoint_db_url=":memory:")
    state = create_initial_state(user_query="web01 조회", thread_id="th-1", user_id="alice",
                                 user_role="user", allowed_sources=None)
    state.update({"parsed_requirements": {"filter_conditions": [
        {"field": "hostname", "op": "=", "value": "web01"}], "time_range": time_range,
        "original_query": "q"},
        "task_plan": [{"task_id": "t1", "agent": "apm_query", "sub_query": "web01 조회",
                       "order": 1, "depends_on": [], "input_from": [], "status": "pending",
                       **task}],
        "task_results": {}, "replan_count": 0})
    state.update(await agent_orchestrator(state, llm=FakeListChatModel(responses=["x"] * 4),
                                          app_config=config))
    out = await result_aggregator(state, llm=FakeListChatModel(responses=["요약입니다."] * 8),
                                  app_config=config, synthesize=True, composite_answer="steps")
    return out, state["task_results"]["t1"]


@pytest.mark.asyncio
async def test_two_tier_body_carries_optional_handler_notices_up_to_three(gateway) -> None:
    """의무 아닌 처리기 고지는 우선순위 순 최대 3줄이 본문에 · 나머지는 구조 필드에만."""
    from datetime import timedelta

    masked = "개인정보·자격증명 보호를 위해 값을 가린 칸: name"
    gateway({"apm_source_changes": _env("apm_source_changes", [
                 {"instance_id": 11, "change_detected_ms": 1759341600000}],
                 disclosures=[{"kind": "apm_masked_fields", "text": masked}]),
             "apm_resource_pool": _env("apm_resource_pool", [{"db_pool_active": 1}]),
             "apm_status_stats": _env("apm_status_stats", [{"name": "select 1", "calls": 1}])})
    now = datetime.now()
    period = {"start": (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"),
              "end": now.strftime("%Y-%m-%d %H:%M")}
    out, _ = await _two_tier_answer(
        {"views": ["apm.changes", "apm.pool", "apm.sql_stats"]}, time_range=period)
    body = out["final_response"]
    for text in ("배포 시각으로 확정할 수 없습니다", "현재값 기준입니다", "시 단위 통계라"):
        assert "[안내] " in body and text in body, text
    assert masked not in body, "넷째 줄(우선순위 40)은 본문에 싣지 않는다"
    kinds = {d["kind"] for d in out["disclosures"]}
    assert {disc.APM_CHANGE_DETECTION, disc.APM_CURRENT_ONLY, disc.APM_HOURLY_RESOLUTION,
            disc.APM_MASKED_FIELDS} <= kinds, "구조 필드에는 모두 남는다"


@pytest.mark.asyncio
async def test_gateway_unresolved_condition_reaches_the_two_tier_body(gateway) -> None:
    """게이트웨이 봉투의 `apm_unresolved_condition`이 2단 최종 답 본문에 실린다.

    거부된 정렬 기준·모르는 지표를 빼고 조회했다는 고지다(W2 검증 B2 새 계약). 등록 kind라 L-5
    통로로 task 고지가 되고, 의무 고지라 본문에 나온다. 그 조건만 빠지고 보기는 정상 답이다.
    """
    # 문구는 실 게이트웨이 프로세스 종단(2026-10-02)에서 받은 모양 그대로다
    notice = "원천이 정렬 기준 responseTime을(를) 받지 않아 그 조건 없이 받아 직접 정렬했습니다"
    gw = gateway({"apm_status_stats": _env(
        "apm_status_stats", [{"name": "select 1", "calls": 2}],
        limits=["[한계] 원천이 정렬 기준 responseTime를 받지 않아 전체를 받아 정렬했다(그 조건·행"
                " 수 없이 다시 받음 — 행 수는 서버 기본값 · 미공개 W10 · 원천 사유 도메인 1000:"
                " Unknown sort_by_metrics: responseTime)"],
        disclosures=[{"kind": "apm_unresolved_condition", "text": notice}])})
    out, result = await _two_tier_answer(
        {"views": ["apm.sql_stats"], "view_args": {"apm.sql_stats": {"sort_by": "responseTime"}}})
    assert gw.named("apm_status_stats")[0]["sort_by"] == "responseTime"
    assert result["query_results"] and "degraded_reason" not in result
    (item,) = [d for d in result["disclosures"] if d["kind"] == disc.APM_UNRESOLVED_CONDITION]
    assert (item["text"], item["source"]) == (notice, "task:t1")
    assert f"[안내] {notice}" in out["final_response"], "의무 고지 — 본문에 실린다"
    assert any(d["kind"] == disc.APM_UNRESOLVED_CONDITION and d["text"] == notice
               for d in out["disclosures"])
