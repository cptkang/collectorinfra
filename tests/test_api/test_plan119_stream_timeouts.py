"""plans/119 T-0·T-1·T-5 — 스트림 상한 세 시계 · 마감 바인딩 · 응답 선행 본문 · 단계 타임라인.

D-267 ⑦(G-7): `API_QUERY_TIMEOUT`·`API_FILE_QUERY_TIMEOUT` 은 **첫 답변(표 또는 첫 토큰)까지**의
처리 상한이다. 첫 답변 뒤에는 토큰 간 idle(`API_STREAM_IDLE_TIMEOUT_SEC`)과 전체 상한(처리 상한 +
`API_STREAM_DELIVERY_GRACE_SEC`)으로만 끊는다. 여기서 고정하는 것:

1. 순수 판정 — `StreamCaps`(세 시계) · `StreamTimeline`(경계·사망 단계) ·
   `StreamWatch`(첫 답변 판정).
2. 라우트 — 두 스트림 라우트 대칭(D-066)으로 ① 첫 답변 전 처리 상한(종전 오류/D-265 부분 결과)
   ② 첫 답변 뒤 토큰이 흐르는 동안은 처리 상한으로 끊지 않음 ③ idle 절단 시 보낸 답변 보존
   ④ 전체 상한(hang 방지 · D-242 CU-11) ⑤ heartbeat off 에서도 제때 판정 ⑥ heartbeat 는 idle 을
   되돌리지 않음.
3. 마감 바인딩 — 네 진입점 모두 그래프 실행 컨텍스트에서 `bound_deadline()` 이 보인다(T-1).
4. `answer_prefix` custom event 는 progress 가 아니라 답변 `token` 이다.
5. 타임라인 계약 키 — `done`·`error` 페이로드와 서버 로그 한 줄.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.stream_failure import (
    CAP_SEMANTIC,
    TIMELINE_KEYS,
    StreamCaps,
    StreamTimeline,
    StreamWatch,
    delivery_cut_notice,
)
from src.llm import USER_RESPONSE_TAG
from src.utils.deadline import bound_deadline

ROUTES = ("text", "file")

# ── 순수 판정: StreamCaps ─────────────────────────────────────────────────


def _caps(limit: float = 10.0, idle: float = 3.0, grace: float = 6.0) -> StreamCaps:
    return StreamCaps(limit_sec=limit, idle_sec=idle, grace_sec=grace, start=100.0)


def test_첫_답변_전에는_처리_상한만_본다() -> None:
    caps = _caps()
    assert caps.next_check_at() == 110.0
    assert caps.exceeded(now=109.9) is None
    assert caps.exceeded(now=110.0) == "processing"


def test_첫_답변_뒤에는_처리_상한을_넘겨도_토큰이_흐르면_끊지_않는다() -> None:
    caps = _caps()
    for t in (109.0, 111.0, 113.0, 115.0):          # 2초 간격 < idle 3초
        caps.mark_answer(now=t)
        assert caps.exceeded(now=t) is None, t
    assert caps.answered


def test_첫_답변_뒤_토큰이_idle_동안_없으면_idle() -> None:
    caps = _caps()
    caps.mark_answer(now=104.0)
    assert caps.next_check_at() == 107.0
    assert caps.exceeded(now=106.9) is None
    assert caps.exceeded(now=107.0) == "idle"


def test_답변_토큰이_idle_시계를_되돌린다() -> None:
    caps = _caps()
    caps.mark_answer(now=104.0)
    caps.mark_answer(now=106.5)
    assert caps.exceeded(now=107.5) is None
    assert caps.next_check_at() == 109.5


def test_토큰이_계속_흘러도_전체_상한에서_끊는다() -> None:
    """hang 방지(D-242 CU-11) — 처리 상한 10 + 전달 연장 6 = 16초."""
    caps = _caps()
    t = 101.0
    while t < 116.0:
        caps.mark_answer(now=t)
        t += 1.0
    assert caps.exceeded(now=115.9) is None
    assert caps.exceeded(now=116.0) == "hard_cap"
    assert caps.hard_cap_sec == 16.0


def test_idle과_전체_상한이_함께_지나면_먼저_닿은_쪽이다() -> None:
    caps = _caps()
    caps.mark_answer(now=112.0)          # idle 마감 115 · 전체 116
    assert caps.exceeded(now=120.0) == "idle"


def test_0_이하는_그_시계를_끈다() -> None:
    caps = StreamCaps(limit_sec=0, idle_sec=0, grace_sec=0, start=0.0)
    assert caps.next_check_at() is None and caps.hard_cap_sec is None
    assert caps.exceeded(now=10_000.0) is None
    caps.mark_answer(now=1.0)
    assert caps.exceeded(now=10_000.0) is None


def test_절단_문구는_사유별로_다르다() -> None:
    idle = delivery_cut_notice("idle", idle_sec=30, limit_sec=120, grace_sec=60)
    cap = delivery_cut_notice("hard_cap", idle_sec=30, limit_sec=120, grace_sec=60)
    assert "30초 동안 멈춰" in idle and "토큰 간 상한 30초" in idle
    assert "전체 상한 180초(처리 상한 120초 + 전달 연장 60초)" in cap
    assert idle != cap


# ── 순수 판정: StreamTimeline ─────────────────────────────────────────────


def _end(name: str, output: Any = None, parents: int = 1) -> dict:
    return {"event": "on_chain_end", "name": name, "data": {"output": output or {}},
            "parent_ids": ["r"] * parents}


def _start(name: str, parents: int = 1) -> dict:
    return {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": ["r"] * parents}


def _custom(name: str, data: dict) -> dict:
    return {"event": "on_custom_event", "name": name, "data": data, "parent_ids": ["r", "n"]}


def _observe(tl: StreamTimeline, event: dict, at: float) -> None:
    tl.observe(event, at, root=len(event.get("parent_ids") or ()) <= 1)


def test_타임라인_경계와_계약_키() -> None:
    tl = StreamTimeline(limit_sec=240)
    _observe(tl, _end("input_parser"), 6900.0)
    _observe(tl, _end("intent_planner"), 12000.0)
    task_rows = {"task_results": {"t1": {"query_results": [{"a": 1}]}}}
    _observe(tl, _end("agent_orchestrator", task_rows), 21000.0)
    _observe(tl, _start("result_aggregator"), 24000.0)
    tl.mark("first_answer_ms", 30000.0)

    payload = tl.payload(end_ms=60000.0)
    assert list(payload) == [
        "cap_semantic", "limit_sec", *TIMELINE_KEYS, "end_ms", "timeout_stage", "timeout_kind",
    ]
    assert payload["cap_semantic"] == CAP_SEMANTIC == "first_answer"
    assert payload["limit_sec"] == 240.0
    assert (payload["parse_end_ms"], payload["plan_end_ms"], payload["first_rows_ms"],
            payload["answer_start_ms"], payload["first_answer_ms"]) == (
        6900.0, 12000.0, 21000.0, 24000.0, 30000.0)
    assert payload["timeout_stage"] is None and payload["timeout_kind"] is None


def test_경계는_처음_도달한_시각만_남긴다() -> None:
    tl = StreamTimeline(limit_sec=60)
    _observe(tl, _end("query_executor", {"query_results": [{"a": 1}]}), 100.0)
    _observe(tl, _end("result_merger", {"query_results": [{"a": 1}]}), 200.0)
    assert tl.get("first_rows_ms") == 100.0


def test_행이_없는_노드_종료는_첫_행이_아니다() -> None:
    tl = StreamTimeline(limit_sec=60)
    _observe(tl, _end("query_executor", {"query_results": []}), 100.0)
    _observe(tl, _custom("task", {"phase": "end", "row_count": 0}), 150.0)
    assert tl.get("first_rows_ms") is None


def test_작업_그룹_진행_이벤트의_행_수로_첫_행을_잡는다() -> None:
    task = StreamTimeline(limit_sec=60)
    _observe(task, _custom("task", {"phase": "end", "row_count": 7}), 90.0)
    group = StreamTimeline(limit_sec=60)
    _observe(group, _custom("group", {"phase": "end", "group": {"row_count": 3}}), 80.0)
    assert task.get("first_rows_ms") == 90.0 and group.get("first_rows_ms") == 80.0


def test_1단_최종_합성_단계가_답변_시작이다() -> None:
    tl = StreamTimeline(limit_sec=60)
    _observe(tl, _custom("agent.aggregate", {"phase": "start", "label": "최종 응답 합성"}), 50.0)
    assert tl.get("answer_start_ms") == 50.0


def test_서브그래프_안의_같은_이름_노드는_루트_경계가_아니다() -> None:
    """3단 task_run 서브그래프·1단 내부 그래프의 노드가 루트 경계를 앞당기지 않는다(K-3)."""
    tl = StreamTimeline(limit_sec=60)
    _observe(tl, _end("input_parser", parents=2), 10.0)
    _observe(tl, _start("output_generator", parents=2), 20.0)
    _observe(tl, _end("query_executor", {"query_results": [{"a": 1}]}, parents=2), 30.0)
    assert tl.get("parse_end_ms") is None and tl.get("answer_start_ms") is None
    # 행은 서브그래프 안에서 확보해도 손에 넣은 시각이다
    assert tl.get("first_rows_ms") == 30.0


@pytest.mark.parametrize(("marks", "stage"), [
    ({}, "parse"),
    ({"parse_end_ms": 1}, "plan"),
    ({"parse_end_ms": 1, "plan_end_ms": 2}, "retrieval"),
    ({"parse_end_ms": 1, "first_rows_ms": 2}, "retrieval"),
    ({"parse_end_ms": 1, "plan_end_ms": 2, "answer_start_ms": 3}, "answer"),
    ({"parse_end_ms": 1, "answer_start_ms": 3, "first_answer_ms": 4}, "delivery"),
])
def test_사망_단계는_가장_늦게_도달한_경계다(marks: dict, stage: str) -> None:
    tl = StreamTimeline(limit_sec=60)
    for key, at in marks.items():
        tl.mark(key, at)
    payload = tl.payload(end_ms=9.0, timeout_kind="processing")
    assert payload["timeout_stage"] == stage and payload["timeout_kind"] == "processing"


def test_답변_단계_밖_토큰은_첫_답변이_아니다() -> None:
    """2단 agent_orchestrator 안 general_inference 하위 작업의 토큰 — 세면 조회 중 idle 로
    끊긴다."""
    watch = StreamWatch(limit_sec=60, idle_sec=1, grace_sec=1)
    watch.answer_sent("중간 답")
    assert not watch.caps.answered and watch.timeline.get("first_answer_ms") is None
    watch.observe(_start("result_aggregator"), root=True)
    watch.answer_sent("최종")
    assert watch.caps.answered and watch.timeline.get("first_answer_ms") is not None
    assert watch.answer_text == "중간 답최종"


def test_그래프_종료_출력을_한_번에_보내면_단계와_무관하게_첫_답변이다() -> None:
    watch = StreamWatch(limit_sec=60, idle_sec=1, grace_sec=1)
    watch.answer_sent("전체 응답", final=True)
    assert watch.caps.answered


# ── 라우트 ────────────────────────────────────────────────────────────────


def _final(response: str = "최종 응답", rows: list[dict] | None = None) -> dict:
    from langchain_core.messages import HumanMessage

    return {
        "event": "on_chain_end", "name": "LangGraph",
        "data": {"output": {
            "final_response": response,
            "query_results": rows if rows is not None else [{"hostname": "a"}],
            "messages": [HumanMessage(content="q")],
        }},
    }


def _token(text: str, node: str = "output_generator") -> dict:
    return {
        "event": "on_chat_model_stream", "name": "llm",
        "tags": [USER_RESPONSE_TAG], "metadata": {"langgraph_node": node},
        "data": {"chunk": type("C", (), {"content": text})()},
        "parent_ids": ["r", "n", "m"],
    }


def _prefix(text: str) -> dict:
    return {"event": "on_custom_event", "name": "answer_prefix", "data": {"text": text},
            "parent_ids": ["r", "n"]}


class _Graph:
    """이벤트 목록 + 이벤트 앞 지연 + 취소·마감 바인딩 관측."""

    def __init__(self, events: list[dict], *, delay_before: dict[int, float] | None = None,
                 state: dict | None = None) -> None:
        self.events = events
        self.delay_before = delay_before or {}
        self.state = state
        self.cancelled = False
        self.bound: Any = "unset"
        self.input_deadline: Any = "unset"

    def get_state(self, config: dict) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(values=self.state) if self.state is not None else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        self.bound = bound_deadline()
        self.input_deadline = input_state.get("request_deadline")
        return _final()["data"]["output"]

    async def astream_events(self, input_state: dict, config: dict,
                             version: str = "v2") -> AsyncGenerator[dict, None]:
        self.bound = bound_deadline()
        self.input_deadline = input_state.get("request_deadline")
        try:
            for i, ev in enumerate(self.events):
                if i in self.delay_before:
                    await asyncio.sleep(self.delay_before[i])
                yield ev
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=60),
    )


@pytest.fixture(autouse=True)
def _reset(app_config):
    s = app_config.server
    s.sse_progress_events = True
    s.sse_heartbeat_interval_sec = 5
    s.query_timeout = 60
    s.file_query_timeout = 60
    s.answer_reserve_sec = 15
    s.stream_idle_timeout_sec = 30
    s.stream_delivery_grace_sec = 60
    yield


def _set(app_config, *, limit: float, idle: float = 30, grace: float = 60, hb: float = 5) -> None:
    s = app_config.server
    s.query_timeout = limit
    s.file_query_timeout = limit
    s.stream_idle_timeout_sec = idle
    s.stream_delivery_grace_sec = grace
    s.sse_heartbeat_interval_sec = hb


def _client(graph: _Graph, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _file() -> dict:
    return {"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")}


def _stream(client: TestClient, route: str) -> list[dict]:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post("/api/v1/query/file/stream", data={"query": "서버 목록"}, files=_file())
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def _post(client: TestClient, route: str):
    if route == "text":
        return client.post("/api/v1/query", json={"query": "서버 목록"})
    return client.post("/api/v1/query/file", data={"query": "서버 목록"}, files=_file())


def _tokens(events: list[dict]) -> str:
    return "".join(e["content"] for e in events if e["type"] == "token")


# ── T-1 마감 바인딩: 네 진입점 ────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_스트림_그래프_실행_컨텍스트에서_마감이_보인다(route, app_config) -> None:
    app_config.server.answer_reserve_sec = 7
    graph = _Graph([_start("output_generator"), _final()])
    got = _stream(_client(graph, app_config), route)
    assert got[-1]["type"] == "done"
    assert isinstance(graph.input_deadline, float)
    assert graph.bound == (graph.input_deadline, 7.0)


@pytest.mark.parametrize("route", ROUTES)
def test_비스트림_그래프_실행_컨텍스트에서_마감이_보인다(route, app_config) -> None:
    app_config.server.answer_reserve_sec = 9
    graph = _Graph([])
    r = _post(_client(graph, app_config), route)
    assert r.status_code == 200, r.text
    assert isinstance(graph.input_deadline, float)
    assert graph.bound == (graph.input_deadline, 9.0)


def test_바인딩_해제는_다른_컨텍스트에서도_예외를_내지_않는다() -> None:
    from src.api.routes.query import _bind_deadline, _unbind_deadline

    class _Cfg:
        class server:  # noqa: N801 — 설정 대역
            answer_reserve_sec = 3

    token = _bind_deadline({"request_deadline": 123.0}, _Cfg, stream=True)
    assert bound_deadline() == (123.0, 3.0)
    contextvars.copy_context().run(_unbind_deadline, token)   # 다른 컨텍스트 — 삼킨다
    _unbind_deadline(token)
    assert bound_deadline() is None


# ── answer_prefix 는 답변 토큰이다 ────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_answer_prefix는_progress가_아니라_token이다(route, app_config) -> None:
    table = "| host |\n|---|\n| a |\n\n"
    graph = _Graph([
        _start("output_generator"), _prefix(table), _token("요약"), _final(table + "요약"),
    ])
    got = _stream(_client(graph, app_config), route)
    types = [e["type"] for e in got]
    assert "progress" not in types
    assert _tokens(got) == table + "요약"            # 최종 응답을 한 번 더 보내지 않는다
    done = got[-1]
    assert done["type"] == "done" and done["response"] == table + "요약"
    tl = done["timeline"]
    assert tl["answer_start_ms"] is not None and tl["first_answer_ms"] is not None
    assert tl["first_answer_ms"] >= tl["answer_start_ms"]


# ── T-5 ① 첫 답변 전: 처리 상한 ───────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("hb", [0.1, 0])
def test_첫_답변_전_처리_상한은_종전_타임아웃이다(route, hb, app_config) -> None:
    """행이 없으면 종전 오류 문구 그대로 — heartbeat 가 꺼져도 무이벤트 hang 없이 끊는다."""
    _set(app_config, limit=0.4, hb=hb)
    events = [_end("input_parser"), _end("intent_planner"), _start("agent_orchestrator"), _final()]
    graph = _Graph(events, delay_before={3: 5.0})
    got = _stream(_client(graph, app_config), route)
    err = got[-1]
    assert err["type"] == "error" and err["message"].startswith("처리 시간이 초과되었습니다")
    assert err["code"] == "timeout" and err["elapsed_ms"] < 2500
    tl = err["timeline"]
    assert (tl["timeout_kind"], tl["timeout_stage"]) == ("processing", "retrieval")
    assert tl["limit_sec"] == 0.4 and tl["first_answer_ms"] is None
    assert graph.cancelled


@pytest.mark.parametrize("route", ROUTES)
def test_heartbeat가_꺼져도_이벤트_도착을_기다리지_않고_상한을_판정한다(route, app_config) -> None:
    """종전에는 heartbeat off 면 이벤트가 올 때만 전체 상한을 쟀다 — 0.45초 간격이면
    0.9초에 끊겼다."""
    _set(app_config, limit=0.5, hb=0)
    events = [_start("input_parser")] + [_end("input_parser")] * 4 + [_final()]
    graph = _Graph(events, delay_before={i: 0.45 for i in range(1, 6)})
    got = _stream(_client(graph, app_config), route)
    err = got[-1]
    assert err["type"] == "error" and err["timeline"]["timeout_kind"] == "processing"
    assert err["elapsed_ms"] < 850, err["elapsed_ms"]


@pytest.mark.parametrize("route", ROUTES)
def test_첫_답변_전_처리_상한에서도_조회한_행은_부분_결과로_준다(route, app_config) -> None:
    """D-265 경로는 그대로 — 타임라인만 덧붙는다."""
    _set(app_config, limit=0.4, hb=0.1)
    rows = [{"hostname": "kpo-web-01", "cpu": 91.5}]
    events = [_start("query_executor"), _end("query_executor", {"query_results": rows}),
              _start("output_generator"), _final()]
    graph = _Graph(events, delay_before={3: 5.0})
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["type"] == "done" and done["status"] == "partial"
    assert done["response"].startswith("서술 생성이 시간 상한")
    assert (done["timeline"]["timeout_kind"], done["timeline"]["timeout_stage"]) == (
        "processing", "answer")


# ── T-5 ② 첫 답변 뒤: 토큰 간 idle ────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_첫_답변_뒤에는_처리_상한을_넘겨도_답변을_끝까지_보낸다(route, app_config) -> None:
    """§2.9 — 답을 읽다가 오류를 받던 경로. 처리 상한 0.4초를 넘겨 1초 동안 토큰이 흘러도
    완주한다."""
    _set(app_config, limit=0.4, idle=0.5, grace=5)
    body = [f"줄{i} " for i in range(10)]
    events = [_start("output_generator")] + [_token(t) for t in body] + [_final("".join(body))]
    graph = _Graph(events, delay_before={i: 0.1 for i in range(1, 12)})
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["type"] == "done" and done.get("status") != "partial"
    assert done["processing_time_ms"] > 400
    assert _tokens(got) == "".join(body)
    assert done["timeline"]["timeout_kind"] is None


@pytest.mark.parametrize("route", ROUTES)
def test_첫_답변_뒤_토큰이_멈추면_idle로_끊고_보낸_답변은_남긴다(route, app_config) -> None:
    # heartbeat 가 흘러도 idle 은 되돌리지 않는다
    _set(app_config, limit=30, idle=0.3, grace=60, hb=0.05)
    table = "| host |\n|---|\n| a |\n\n"
    events = [_start("output_generator"), _prefix(table), _token("요약 앞부분"), _final()]
    graph = _Graph(events, delay_before={3: 5.0},
                   state={"query_results": [{"host": "a"}]})
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["type"] == "done" and done["status"] == "partial"
    assert done["response"].startswith(table + "요약 앞부분")        # 보낸 답을 잃지 않는다
    assert "0.3초 동안 멈춰 여기서 끊었습니다" in done["response"]
    assert "서술 생성이 시간 상한" not in done["response"]           # 표는 이미 나갔다 — 중복 없음
    assert done["row_count"] == 1
    assert [e for e in got if e["type"] == "heartbeat"]
    assert done["processing_time_ms"] < 2500
    tl = done["timeline"]
    assert (tl["timeout_kind"], tl["timeout_stage"]) == ("idle", "delivery")
    assert graph.cancelled


@pytest.mark.parametrize("route", ROUTES)
def test_표가_나가지_않은_채_idle로_끊기면_행을_표로_덧붙인다(route, app_config) -> None:
    _set(app_config, limit=30, idle=0.3, hb=0)
    rows = [{"hostname": "kpo-web-01"}]
    events = [_start("query_executor"), _end("query_executor", {"query_results": rows}),
              _start("output_generator"), _token("다음은 서버"), _final()]
    graph = _Graph(events, delay_before={4: 5.0})
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["status"] == "partial" and done["response"].startswith("다음은 서버")
    assert "| kpo-web-01 |" in done["response"] and done["row_count"] == 1


@pytest.mark.parametrize("route", ROUTES)
def test_답변_단계_전_새는_토큰은_idle을_걸지_않는다(route, app_config) -> None:
    """2단 하위 작업(general_inference)의 USER_RESPONSE_TAG 토큰 뒤 조회가 길어도 끊지 않는다."""
    _set(app_config, limit=30, idle=0.3, hb=0.05)
    events = [
        _end("input_parser"), _end("intent_planner"), _start("agent_orchestrator"),
        _token("일반 답변", node="agent_orchestrator"),
        _end("agent_orchestrator", {"task_results": {"t1": {"query_results": [{"a": 1}]}}}),
        _start("result_aggregator"), _token("종합"), _final("종합"),
    ]
    graph = _Graph(events, delay_before={4: 0.8})                  # idle 0.3 보다 긴 조회
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["type"] == "done" and done.get("status") != "partial"
    tl = done["timeline"]
    assert tl["first_answer_ms"] >= tl["answer_start_ms"] >= tl["first_rows_ms"]


# ── T-5 ③ 전체 상한(hang 방지) ────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_라우트는_토큰이_계속_흘러도_전체_상한에서_끊는다(route, app_config) -> None:
    _set(app_config, limit=0.3, idle=1.0, grace=0.3, hb=0)
    events = [_start("output_generator")] + [_token("x") for _ in range(40)] + [_final()]
    graph = _Graph(events, delay_before={i: 0.1 for i in range(1, 42)})
    got = _stream(_client(graph, app_config), route)
    done = got[-1]
    assert done["type"] == "done" and done["status"] == "partial"
    assert "전체 상한 0.6초(처리 상한 0.3초 + 전달 연장 0.3초)" in done["response"]
    assert done["timeline"]["timeout_kind"] == "hard_cap"
    assert done["processing_time_ms"] < 2500
    assert graph.cancelled


# ── T-0 타임라인 로그 한 줄 ───────────────────────────────────────────────


@pytest.mark.parametrize("route", ROUTES)
def test_타임라인은_done_페이로드와_서버_로그_한_줄에_실린다(route, app_config, caplog) -> None:
    events = [_end("input_parser"), _end("semantic_router"),
              _end("query_executor", {"query_results": [{"a": 1}]}),
              _start("output_generator"), _token("답"), _final("답")]
    with caplog.at_level(logging.INFO, logger="src.api.routes.query"):
        got = _stream(_client(_Graph(events), app_config), route)
    done = got[-1]
    tl = done["timeline"]
    assert set(tl) == {"cap_semantic", "limit_sec", *TIMELINE_KEYS, "end_ms",
                       "timeout_stage", "timeout_kind"}
    assert tl["cap_semantic"] == "first_answer" and tl["limit_sec"] == 60.0
    marks = [tl[k] for k in TIMELINE_KEYS]
    assert all(m is not None for m in marks) and marks == sorted(marks)
    assert tl["end_ms"] >= tl["first_answer_ms"]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[timeline] ")]
    assert len(lines) == 1 and f"query_id={done['query_id']}" in lines[0]
    assert json.loads(lines[0].split(" ", 2)[2]) == tl
