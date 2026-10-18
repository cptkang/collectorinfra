"""plans/164 W7-C · G-10 (a) — 긴 X-View 사전 확인(본체 쪽 · 수용 10).

- 처리기: `apm.app_health` 호출에 기준(`xview_confirm_over=60`)을 싣고, 게이트웨이가 견적
  (`status = confirm_required`)을 돌려주면 조회 결과 대신 선택지 되묻기를 낸다(다른 보기도 부르지
  않는다). 답 「전부」 = 기준 없는 종전 전수 호출 · 「추세」 = `apm_metrics` series·instance
  (같은 대상·기간 · 간격은 질문에서 · 없으면 60분). 짧은 창은 되묻지 않는다.
- 작업 예상 문구는 `estimate.basis`를 반영한다(실측 = 「남은 예상 약」 · 그 밖 = 「호출 지연
  미반영」).
- 집계기·라우트·스키마·화면이 되묻기 페이로드와 답을 나른다.

실 게이트웨이·실 LLM 0 — 모의 게이트웨이 세션.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.api.routes.query import _apm_source_fields
from src.api.schemas import QueryRequest, QueryResponse
from src.config import DBHubConfig
from src.domain.query_time import resolve_query_time
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_query as aq
from src.orchestration.result_aggregator import _apply_apm_source_state
from src.state import create_followup_input, create_initial_state
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 10, 17, 15, 0, 0)
TODAY_MINUTES = 15 * 60
LONG_Q = "web01 오늘 응답시간 p95"
SHORT_Q = "web01 최근 30분 응답시간 p95"
APP_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "app.js"


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-17T15:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


class _Gateway:
    """게이트웨이 흉내 — 기준을 넘는 app_health는 견적만(데이터 0) 돌려준다."""

    def __init__(self, estimate: dict | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.estimate = estimate or {"api_calls": 902, "seconds": 11275.0, "basis": "measured"}

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        over = args.get(aq.XVIEW_CONFIRM_ARG)
        minutes = args.get("lookback_minutes") or 0
        if name == "apm_app_health" and over is not None and minutes > over:
            return _env(name, [], status="confirm_required", expected_xview_calls=minutes,
                        expected_api_calls=minutes + 2, xview_confirm_over=over,
                        instance_count=1, estimate=self.estimate)
        return _env(name, [{"tool_row": name, "hostname": args.get("hostname")}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def named(self, tool: str) -> list[dict]:
        return [a for n, a in self.calls if n == tool]

    def data_tools(self) -> list[str]:
        return [n for n, _ in self.calls if not n.startswith("apm_job_")]


@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    gw = _Gateway()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
    return gw


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens='{"apm": "gw"}', source_call_timeout=10.0)
    return SimpleNamespace(
        dbhub=dbhub,
        composite=SimpleNamespace(max_targets=10, fanout_concurrency=2, audit_enabled=False,
                                  task_frame_enabled=False, plan_dag_validation_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]))


def _isolated(query: str, *hosts: str, choice: str | None = None) -> dict:
    return {"parsed_requirements": {
                "filter_conditions": [{"field": "hostname", "op": "=", "value": h}
                                      for h in hosts], "time_range": None, "limit": None},
            "time_resolution": resolve_query_time(query, NOW).to_state(),
            "conversation_context": {}, "thread_id": "th-1", "user_id": "alice",
            "original_user_query": query, "user_query": query, "apm_xview_choice": choice}


async def _run(views: list[str], isolated: dict, view_args: dict | None = None) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                                   "input_from": [], "views": views,
                                   "view_args": view_args or {}},
                                  isolated, llm=None, app_config=_cfg(), now=NOW)


def _stable(args: dict) -> dict:
    """호출마다 달라지는 칸(호출 직전에 재는 `wait_seconds`)을 뺀 인자."""
    return {k: v for k, v in args.items() if k != "wait_seconds"}


# ── 되묻기 ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_long_window_asks_before_collecting(gateway) -> None:
    res = await _run(["apm.app_health"], _isolated(LONG_Q, "web01"))
    (sent,) = gateway.named("apm_app_health")
    assert sent[aq.XVIEW_CONFIRM_ARG] == aq.XVIEW_CONFIRM_OVER == 60
    assert sent["lookback_minutes"] == TODAY_MINUTES  # 범위를 줄이지 않는다
    assert gateway.data_tools() == ["apm_app_health"], "데이터 조회 0(견적 1회뿐)"
    assert "organized_data" not in res and res["degraded_reason"] == "apm_xview_confirm"
    ask = res[aq.XVIEW_CONFIRMATION_KEY]
    assert [o["key"] for o in ask["options"]] == ["trend", "full"]
    assert ask["original_query"] == LONG_Q and ask["expected_xview_calls"] == TODAY_MINUTES
    question = ask["question"]
    assert "API를 900회 불러야 합니다" in question
    assert "시 단위 추세로 볼까요(수 초), 거래 원본을 전부 모을까요(" in question
    assert "예상 약 3.1시간 · API 호출 902회" in question and "지연 미반영" not in question
    assert ask["options"][1]["label"] == "거래 원본 전부 모으기(예상 약 3.1시간 · API 호출 902회)"
    assert ask["options"][0]["label"] == "시 단위 추세로 보기(수 초)"
    assert res["final_response"] == question and res["error"] == question
    assert res["source_status"][0]["status"] == "not_queried"
    assert res["apm_query"]["xview_confirmation"]["threshold"] == 60


@pytest.mark.asyncio
async def test_rate_only_estimate_is_marked(gateway) -> None:
    gateway.estimate = {"api_calls": 902, "seconds": 180.4, "basis": "rate_only"}
    res = await _run(["apm.app_health"], _isolated(LONG_Q, "web01"))
    assert "예상 약 3분 · 호출 지연 미반영 · API 호출 902회" in res["final_response"]


@pytest.mark.asyncio
async def test_other_views_are_not_called_when_asking(gateway) -> None:
    res = await _run(["apm.app_health", "apm.pool"], _isolated(LONG_Q, "web01"))
    assert aq.XVIEW_CONFIRMATION_KEY in res
    assert gateway.data_tools() == ["apm_app_health"], "되물으면 다른 보기도 부르지 않는다"


@pytest.mark.asyncio
async def test_short_window_runs_as_before(gateway) -> None:
    res = await _run(["apm.app_health", "apm.pool"], _isolated(SHORT_Q, "web01"))
    assert aq.XVIEW_CONFIRMATION_KEY not in res and not res.get("error")
    assert sorted(gateway.data_tools()) == ["apm_app_health", "apm_resource_pool"]
    assert res["query_results"]


# ── 답 ──────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_choice_sends_previous_arguments(gateway) -> None:
    await _run(["apm.app_health"], _isolated(LONG_Q, "web01"))
    (gated,) = gateway.named("apm_app_health")
    gateway.calls.clear()
    res = await _run(["apm.app_health"], _isolated(LONG_Q, "web01", choice="full"))
    (sent,) = gateway.named("apm_app_health")
    assert aq.XVIEW_CONFIRM_ARG not in sent
    expected = {k: v for k, v in gated.items() if k != aq.XVIEW_CONFIRM_ARG}
    assert _stable(sent) == _stable(expected), "종전과 같은 인자(전수 조회)"
    assert aq.XVIEW_CONFIRMATION_KEY not in res and res["query_results"]


@pytest.mark.asyncio
async def test_trend_choice_queries_instance_series(gateway) -> None:
    res = await _run(["apm.app_health"], _isolated(LONG_Q, "web01", choice="trend"))
    assert gateway.data_tools() == ["apm_metrics"]
    (sent,) = gateway.named("apm_metrics")
    assert sent["mode"] == "series" and sent["scope"] == "instance"
    assert sent["hostname"] == "web01" and sent["lookback_minutes"] == TODAY_MINUTES
    assert sent["interval_minute"] == 60 and "metrics" not in sent
    assert res["apm_query"]["views"] == [aq.TREND_VIEW]
    texts = [d["text"] for d in res.get("disclosures") or []]
    assert any("선택에 따라 거래 원본(X-View) 대신" in t and "(60분 간격)로 조회했습니다" in t
               for t in texts)


@pytest.mark.asyncio
async def test_trend_choice_keeps_interval_from_question(gateway) -> None:
    query = "web01 오늘 10분 간격 응답시간"
    await _run(["apm.app_health"], _isolated(query, "web01", choice="trend"))
    (sent,) = gateway.named("apm_metrics")
    assert sent["interval_minute"] == 10


@pytest.mark.asyncio
async def test_trend_choice_without_trend_view_runs_previous_call(gateway, monkeypatch) -> None:
    """추세 보기가 레지스트리에 없으면 바꾸지 못한다 — 기준을 다시 실어 되풀이해 묻지 않고 종전
    전수 조회다(교정 4)."""
    views = [v for v in aq.apm_views() if v.id != aq.TREND_VIEW]
    monkeypatch.setattr(aq, "apm_views", lambda: views)
    res = await _run(["apm.app_health"], _isolated(LONG_Q, "web01", choice="trend"))
    (sent,) = gateway.named("apm_app_health")
    assert aq.XVIEW_CONFIRM_ARG not in sent
    assert aq.XVIEW_CONFIRMATION_KEY not in res and res["query_results"]


def test_unknown_choice_is_ignored() -> None:
    assert aq.xview_choice({"apm_xview_choice": "bogus"}) is None
    assert aq.xview_choice({"apm_xview_choice": "full"}) == "full"


# ── 작업 예상 문구(estimate.basis) ───────────────────────────────────────────────


def test_progress_text_reflects_basis() -> None:
    measured = {"estimate": {"api_calls": 1073, "seconds": 13000.0, "basis": "measured"},
                "progress": {"done": 10, "total": 1073, "label": "API 호출"}}
    assert aq._progress_text(measured) == (
        "남은 예상 약 3.6시간(API 호출 1073회) · 진행 10/1073 API 호출")
    rate_only = {"estimate": {"api_calls": 120, "seconds": 300, "basis": "rate_only"}}
    assert aq._progress_text(rate_only) == "예상 약 5분(API 호출 120회 · 호출 지연 미반영)"
    assert aq._progress_text({"estimate": {"seconds": 12.2}}) == "예상 약 12.2초(호출 지연 미반영)"
    assert aq._progress_text({}) == "예상 시간 미정"


# ── 집계기 · 라우트 · 화면 ───────────────────────────────────────────────────────


def test_aggregator_lifts_confirmation_to_state() -> None:
    ask = {"question": "q?", "options": [{"key": "trend"}, {"key": "full"}],
           "original_query": LONG_Q}
    state = {"task_plan": [{"task_id": "t1", "agent": "apm_query", "order": 1}],
             "task_results": {"t1": {"error": "q?", aq.XVIEW_CONFIRMATION_KEY: ask}}}
    out = _apply_apm_source_state({"final_response": "q?"}, state)
    assert out[aq.XVIEW_CONFIRMATION_KEY] == ask
    assert "apm_source_pending" not in out, "대기 값 없음(답은 버튼이 원 질문과 함께 보낸다)"


def test_request_scope_and_response_fields() -> None:
    assert create_followup_input("q", apm_xview_choice="trend")["apm_xview_choice"] == "trend"
    delta = create_followup_input("q")
    assert delta["apm_xview_choice"] is None and delta["apm_xview_confirmation"] is None
    first = create_initial_state("q", apm_xview_choice="full")
    assert first["apm_xview_choice"] == "full" and first["apm_xview_confirmation"] is None
    assert QueryRequest(query="q", apm_xview_choice="trend").apm_xview_choice == "trend"
    with pytest.raises(ValueError):
        QueryRequest(query="q", apm_xview_choice="all")
    assert "apm_xview_confirmation" in QueryResponse.model_fields
    ask = {"question": "q?", "options": []}
    assert _apm_source_fields({"apm_xview_confirmation": ask}) == {"apm_xview_confirmation": ask}
    assert _apm_source_fields({}) == {}


def test_screen_renders_buttons_and_resends_choice() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    for body in ("streamBody", "queryBody"):
        assert f"{body}.apm_xview_choice = apmScope.xview;" in js
    for src in ("metaData", "data"):
        call = f"appendApmXviewConfirmationToLastBubble({src}.apm_xview_confirmation);"
        assert js.count(call) == 1
    block = js[js.index("function renderApmXviewConfirmation(bubble, clar)"):]
    block = block[:block.index("function appendApmXviewConfirmationToLastBubble(clar)")]
    assert ('executeStreamingQuery(clar.original_query || "", null, null, null, null, false, null,'
            ' { xview: key });') in block
