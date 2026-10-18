"""plans/164 W7 독립 검증(verifier) — 본체 쪽 경계·상태·보안.

- 요청 필드 `apm_xview_choice`는 Literal(trend|full)만 받는다.
- 요청 스코프 상태(`apm_xview_choice`·`apm_xview_confirmation`)가 다음 턴에 누수되지 않는다
  (라우트 `_build_turn_input_state` 후속 턴 경로 포함).
- 되묻기 페이로드에 내부 값(토큰·게이트웨이 URL)이 실리지 않는다.
- 교정 확인: 사전 확인 호출이 작업으로 승격돼도 되묻는다 · 1단(deep_agent) ambient에 답이
  실린다.

실 게이트웨이·실 LLM 0 — 모의 MCP 세션.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from src.api.routes.query import _build_turn_input_state
from src.api.schemas import QueryRequest
from src.config import DBHubConfig
from src.domain.query_time import resolve_query_time
from src.infrastructure import apm_job_store as store_mod
from src.infrastructure.apm_job_store import ApmJobStore
from src.orchestration import apm_jobs as jobs
from src.orchestration import apm_query as aq
from src.orchestration.deep_agent import _extract_ambient_state
from src.orchestration.subagents import _make_isolated_input
from tests.test_orchestration import apm_batch_mock

NOW = datetime(2026, 10, 17, 15, 0, 0)
LONG_Q = "web01 오늘 응답시간 p95"
JOB = "0123456789abcdef0123456789abcdef"
GW_TOKEN = "gw-secret-token-XYZ"
APP_JS = Path(__file__).resolve().parents[2] / "src" / "static" / "js" / "app.js"


def _env(tool: str, rows: list[dict], **extra: Any) -> dict:
    return {"rows": rows, "row_count": len(rows), "queried_at": "2026-10-17T15:00:00+09:00",
            "source_kind": "apm_api", "source": "jennifer", "tool": tool, "limits": [], **extra}


def _confirm(args: dict) -> dict:
    minutes = args.get("lookback_minutes") or 0
    return _env("apm_app_health", [], status="confirm_required", expected_xview_calls=minutes,
                expected_api_calls=minutes + 2, xview_confirm_over=args[aq.XVIEW_CONFIRM_ARG],
                instance_count=1,
                estimate={"api_calls": minutes + 2, "seconds": 120.0, "basis": "rate_only"})


def _handle(state: str) -> dict:
    return {"job_id": JOB, "state": state,
            "progress": {"done": 0, "total": None, "unit": "api_calls", "label": "API 호출"},
            "estimate": {"api_calls": None, "seconds": None, "basis": None},
            "created_at": "2026-10-17T15:00:00+09:00", "updated_at": "2026-10-17T15:00:01+09:00",
            "expires_at": None}


class _Gateway:
    """`promote=True`면 사전 확인 호출을 작업으로 승격(접수 봉투)하고, 상태 조회가 끝난 작업의
    `result_meta`에 견적 봉투를 싣는다 — 실 게이트웨이 동작(검증자 probe로 실측)과 같은 모양."""

    def __init__(self, promote: bool) -> None:
        self.promote = promote
        self.calls: list[tuple[str, dict]] = []
        self.confirm_args: dict | None = None

    def factory(self):
        @asynccontextmanager
        async def open_(url, headers):
            yield self

        return open_

    def _single(self, name: str, args: dict) -> dict:
        if name == "apm_app_health" and aq.XVIEW_CONFIRM_ARG in args:
            if self.promote:
                self.confirm_args = args
                return _env(name, [], job=_handle("running"))
            return _confirm(args)
        return _env(name, [{"tool_row": name, "hostname": args.get("hostname")}])

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, dict(arguments)))
        if name == "apm_job_status":
            meta = {k: v for k, v in _confirm(self.confirm_args or {aq.XVIEW_CONFIRM_ARG: 60,
                                                                   "lookback_minutes": 900}
                                              ).items() if k not in ("rows", "row_count")}
            reply = _env("apm_job_status", [], job=_handle("completed"), result_meta=meta,
                         total_row_count=0,
                         artifact={"job_id": JOB, "total_rows": 0, "chunk_rows": 2000,
                                   "chunks": [], "columns": [], "text_parts": []})
        else:
            reply = apm_batch_mock.reply_for(name, arguments, lambda a: self._single(name, a))
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(reply))], isError=False)

    def data_tools(self) -> list[str]:
        return [n for n, _ in self.calls if not n.startswith("apm_job_")]


@pytest.fixture
def install(monkeypatch):
    monkeypatch.setattr(store_mod, "_STORE", ApmJobStore(None))
    monkeypatch.setattr(jobs, "POLL_INTERVAL_SEC", 0.01)

    def _install(promote: bool) -> _Gateway:
        gw = _Gateway(promote)
        monkeypatch.setattr(aq, "_SESSION_FACTORY", gw.factory())
        return gw

    return _install


def _cfg() -> SimpleNamespace:
    dbhub = DBHubConfig(_env_file=None, source_endpoints={"apm": "http://127.0.0.1:1/sse"},
                        source_tokens=json.dumps({"apm": GW_TOKEN}), source_call_timeout=10.0)
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


async def _run(views: list[str], isolated: dict) -> dict:
    return await aq.run_apm_query({"task_id": "t1", "agent": "apm_query", "sub_query": "q",
                                   "input_from": [], "views": views, "view_args": {}},
                                  isolated, llm=None, app_config=_cfg(), now=NOW)


# ── 요청 필드 검증(보안) ─────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["trend", "full", None])
def test_choice_field_accepts_only_literals(value) -> None:
    assert QueryRequest(query="q", apm_xview_choice=value).apm_xview_choice == value


@pytest.mark.parametrize("value", ["FULL", "all", "", "<script>", 1, ["full"]])
def test_choice_field_rejects_other_values(value) -> None:
    with pytest.raises(ValidationError):
        QueryRequest(query="q", apm_xview_choice=value)


def test_handler_ignores_unknown_choice_even_if_state_is_polluted() -> None:
    assert aq.xview_choice({"apm_xview_choice": "FULL"}) is None
    assert aq.xview_choice({"apm_xview_choice": ["full"]}) is None


# ── 요청 스코프(체크포인터 델타 병합) ─────────────────────────────────────────


def _followup(body: QueryRequest, checkpoint: dict) -> dict:
    return _build_turn_input_state(body, "th-1", checkpoint, {"sub": "alice", "role": "user"})


def test_route_followup_clears_previous_choice_and_confirmation() -> None:
    prev = {"apm_xview_choice": "full",
            "apm_xview_confirmation": {"question": "q", "options": []},
            "messages": []}
    delta = _followup(QueryRequest(query="다음 질문", thread_id="th-1"), prev)
    assert "apm_xview_choice" in delta and delta["apm_xview_choice"] is None
    assert "apm_xview_confirmation" in delta and delta["apm_xview_confirmation"] is None
    merged = {**prev, **delta}  # 체크포인터는 델타만 병합한다
    assert merged["apm_xview_choice"] is None and merged["apm_xview_confirmation"] is None


def test_route_followup_carries_button_choice() -> None:
    delta = _followup(QueryRequest(query=LONG_Q, thread_id="th-1", apm_xview_choice="full"),
                      {"messages": []})
    assert delta["apm_xview_choice"] == "full" and delta["apm_xview_confirmation"] is None


def test_route_first_turn_carries_choice() -> None:
    state = _build_turn_input_state(QueryRequest(query=LONG_Q, apm_xview_choice="trend"),
                                    "th-new", None, {"sub": "alice", "role": "user"})
    assert state["apm_xview_choice"] == "trend" and state["apm_xview_confirmation"] is None


def test_isolated_input_carries_choice_tier2() -> None:
    task = {"task_id": "t1", "agent": "apm_query", "sub_query": "q", "input_from": []}
    iso = _make_isolated_input(task, {"apm_xview_choice": "full"}, prior={})
    assert iso["apm_xview_choice"] == "full"


def test_isolated_input_carries_choice_tier1_deep_agent() -> None:
    ambient = _extract_ambient_state({"apm_xview_choice": "full", "thread_id": "th-1"})
    task = {"task_id": "t1", "agent": "apm_query", "sub_query": "q", "input_from": []}
    assert _make_isolated_input(task, ambient, prior={})["apm_xview_choice"] == "full"


# ── 되묻기 페이로드(보안 · 조회 0) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirmation_payload_has_no_internal_values(install) -> None:
    gw = install(promote=False)
    res = await _run(["apm.app_health", "apm.runtime"], _isolated(LONG_Q, "web01"))
    payload = res[aq.XVIEW_CONFIRMATION_KEY]
    assert set(payload) == {"question", "options", "expected_xview_calls", "estimate",
                            "original_query"}
    assert [o["key"] for o in payload["options"]] == ["trend", "full"]
    dumped = json.dumps(res, ensure_ascii=False, default=str)
    assert GW_TOKEN not in dumped and "127.0.0.1:1" not in dumped
    assert gw.data_tools() == ["apm_app_health"], "다른 보기(runtime)는 부르지 않는다"


@pytest.mark.asyncio
async def test_promoted_preflight_still_asks(install) -> None:
    gw = install(promote=True)
    res = await _run(["apm.app_health", "apm.runtime"], _isolated(LONG_Q, "web01"))
    assert "apm_runtime_health" not in gw.data_tools()
    assert aq.XVIEW_CONFIRMATION_KEY in res


# ── 화면(XSS) ───────────────────────────────────────────────────────────────


def test_screen_escapes_option_key_and_label() -> None:
    js = APP_JS.read_text(encoding="utf-8")
    start = js.index("function renderApmXviewConfirmation")
    body = js[start:js.index("function appendApmXviewConfirmationToLastBubble", start)]
    assert "escapeHtml(o.key" in body and "escapeHtml(o.label" in body
    assert "clar.question" not in body  # 질문 원문은 말풍선(마크다운 경로)이 그린다
    assert "innerHTML" not in body
