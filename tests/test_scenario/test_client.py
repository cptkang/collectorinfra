"""SSE 클라이언트 — 측정 정본 경로 (plans/94 §0.3-3 · §4.3).

성공 건의 노드별 지연을 주는 유일한 경로가 `/query/stream` 이므로, 이 파서가 조용히
틀리면 **모든 측정치가 조용히 틀린다.** 네트워크 없이 합성 이벤트로 검증한다.
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import pytest

from scripts.scenario.assertions import Observation
from scripts.scenario.client import ClientConfig, ScenarioClient, _apply_done, _derive_status


class FakeResponse:
    """httpx.Response 의 iter_lines() 만 흉내낸다."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def iter_lines(self) -> Iterator[str]:
        for event in self._events:
            yield f"data: {json.dumps(event, ensure_ascii=False)}"
            yield ""


@pytest.fixture()
def client() -> Iterator[ScenarioClient]:
    instance = ScenarioClient(ClientConfig(port=1))
    yield instance
    instance.close()


def _consume(client: ScenarioClient, events: list[dict[str, Any]]) -> Observation:
    obs = Observation()
    # started 는 호출자의 perf_counter 값이다(_post_stream 과 같은 기준).
    # 0.0 을 넘기면 첫 간격이 프로세스 기동 이래 전체 시간이 되어 거짓 hang 이 된다.
    client._consume_sse(FakeResponse(events), obs, started=time.perf_counter())
    return obs


# --- 기준 URL (W2) -------------------------------------------------------

def test_W2_기준_URL은_127_0_0_1_고정이다() -> None:
    """Windows 에서 localhost 는 ::1 을 먼저 시도할 수 있는데 API_HOST=0.0.0.0 은
    IPv4 전용 바인딩이라 간헐 실패한다(부록 A.1-5)."""
    url = ClientConfig(port=8050).base_url
    assert url == "http://127.0.0.1:8050/api/v1"
    assert "localhost" not in url


def test_토큰이_있으면_Authorization_헤더가_붙는다() -> None:
    assert ClientConfig(port=1).headers == {}
    assert ClientConfig(port=1, token="t").headers == {"Authorization": "Bearer t"}


# --- 상태 유도 -----------------------------------------------------------

def test_done_이벤트에는_status_키가_없어_존재하는_키로_유도한다() -> None:
    """역질문은 clarification 키의 존재로만 판별된다(query.py:1311 pre-gate)."""
    assert _derive_status({"clarification": {"kind": "zone_select"}}) == "clarification"
    assert _derive_status({"awaiting_approval": True}) == "awaiting_approval"
    assert _derive_status({}) == "completed"
    assert _derive_status({"status": "error"}) == "error"


def test_done_페이로드가_관측치로_옮겨진다() -> None:
    obs = Observation()
    _apply_done(obs, {
        "query_id": "q1", "response": "5건", "executed_sql": "SELECT 1", "row_count": 5,
        "has_file": True, "file_name": "a.xlsx", "processing_time_ms": 1234.5,
        "db_scope": {"db_ids": ["polestar_cm_gp"]},
    })
    assert obs.query_id == "q1"
    assert obs.executed_sql == "SELECT 1"
    assert obs.row_count == 5
    assert obs.has_file is True
    assert obs.processing_time_ms == 1234.5
    assert obs.db_ids == ["polestar_cm_gp"]
    assert obs.status == "completed"


# --- 노드별 지연 분해 ----------------------------------------------------

def test_노드별_지연을_timestamp_ms_차로_계산한다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "node_start", "node": "field_mapper", "timestamp_ms": 100.0},
        {"type": "node_complete", "node": "field_mapper", "timestamp_ms": 250.0},
        {"type": "node_start", "node": "deep_agent", "timestamp_ms": 250.0},
        {"type": "node_complete", "node": "deep_agent", "timestamp_ms": 8850.0},
        {"type": "done", "response": "끝", "processing_time_ms": 9000},
    ])
    assert obs.node_elapsed_ms == {"field_mapper": 150.0, "deep_agent": 8600.0}
    assert obs.node_path == ["field_mapper", "deep_agent"]
    assert obs.processing_time_ms == 9000


def test_완료_이벤트가_없는_노드는_지연을_만들지_않는다(client: ScenarioClient) -> None:
    """없는 측정치를 0 으로 채우면 병목 귀속이 거짓이 된다."""
    obs = _consume(client, [
        {"type": "node_start", "node": "a", "timestamp_ms": 0.0},
        {"type": "done", "response": "끝"},
    ])
    assert obs.node_elapsed_ms == {}
    assert obs.node_path == ["a"]


def test_ttfb는_첫_node_start_에서_잡힌다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "node_start", "node": "a", "timestamp_ms": 10.0},
        {"type": "node_start", "node": "b", "timestamp_ms": 20.0},
        {"type": "done", "response": "끝"},
    ])
    assert obs.ttfb_ms is not None and obs.ttfb_ms >= 0


def test_progress_와_token_을_구분해_수집한다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "progress", "phase": "planning"},
        {"type": "token", "content": "안"},
        {"type": "token", "content": "녕"},
        {"type": "done", "response": ""},
    ])
    assert len(obs.progress_events) == 1
    assert obs.response == "안녕"   # done 에 response 가 비면 토큰을 이어붙인다


def test_done_의_response_가_토큰보다_우선한다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "token", "content": "부분"},
        {"type": "done", "response": "최종 응답"},
    ])
    assert obs.response == "최종 응답"


# --- hang 탐지 (금지 등급) ------------------------------------------------

def test_done_없이_끊기면_hang_이고_조용히_성공으로_세지_않는다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "node_start", "node": "a", "timestamp_ms": 0.0},
    ])
    assert obs.hang is True
    assert obs.status == "error"
    assert obs.error and "done" in obs.error


def test_무이벤트_간격이_상한을_넘으면_hang_이다() -> None:
    instance = ScenarioClient(ClientConfig(port=1, hang_gap_ms=0.0))
    try:
        obs = Observation()
        instance._consume_sse(
            FakeResponse([{"type": "done", "response": "끝"}]), obs,
            started=time.perf_counter() - 10.0,
        )
    finally:
        instance.close()
    assert obs.hang is True
    assert obs.max_event_gap_ms is not None


def test_첫_이벤트까지의_무응답도_hang_판정_대상이다() -> None:
    """요청 후 첫 node_start 까지의 침묵도 사용자에게는 무응답이다.

    D-198(무한대기)의 증상이 정확히 이것이었다 - 커서만 깜박이고 이벤트가 0건.
    이 구간을 판정에서 빼면 그 결함을 다시 놓친다.
    """
    instance = ScenarioClient(ClientConfig(port=1, hang_gap_ms=1000.0))
    try:
        obs = Observation()
        instance._consume_sse(
            FakeResponse([{"type": "done", "response": "끝"}]), obs,
            started=time.perf_counter() - 5.0,   # 5초간 무이벤트
        )
    finally:
        instance.close()
    assert obs.hang is True


def test_정상_완료는_hang_이_아니다(client: ScenarioClient) -> None:
    obs = _consume(client, [{"type": "done", "response": "끝", "row_count": 3}])
    assert obs.hang is False
    assert obs.status == "completed"


# --- 에러 이벤트 ---------------------------------------------------------

def test_error_이벤트를_삼키지_않는다(client: ScenarioClient) -> None:
    obs = _consume(client, [
        {"type": "error", "message": "SQL 검증 실패"},
        {"type": "done", "response": ""},
    ])
    assert obs.error == "SQL 검증 실패"


def test_깨진_JSON_라인은_건너뛰되_스트림을_죽이지_않는다(client: ScenarioClient) -> None:
    class Broken(FakeResponse):
        def iter_lines(self):
            yield "data: {깨진 json"
            yield "data: " + json.dumps({"type": "done", "response": "끝"})

    obs = Observation()
    client._consume_sse(Broken([]), obs, started=time.perf_counter())
    assert obs.response == "끝"
    assert obs.hang is False


def test_역질문_done_은_clarification_상태가_된다(client: ScenarioClient) -> None:
    obs = _consume(client, [{
        "type": "done", "response": "어느 존을?",
        "clarification": {"kind": "zone_select", "options": [1, 2, 3]},
    }])
    assert obs.status == "clarification"
    assert obs.clarification is not None
    assert "done" in obs.sse_events


# --- 비스트리밍 대기 상한은 서버 상한을 넘는다 (2026-09-17 plans/100 점검) ------------------

def _capturing_client(config: ClientConfig) -> tuple[ScenarioClient, list[tuple[str, dict]]]:
    """요청마다 (경로, httpx timeout 확장값)을 기록한다(네트워크 0)."""
    import httpx

    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.extensions.get("timeout") or {}))
        return httpx.Response(200, json={"response": "ok"})

    instance = ScenarioClient(config)
    instance._client.close()
    instance._client = httpx.Client(timeout=config.timeout_sec, transport=httpx.MockTransport(handler))
    return instance, seen


def test_비스트리밍_질의는_서버_상한보다_오래_기다린다() -> None:
    """360초에서 끊으면 서버는 정상 처리 중인데 러너가 hang(무조건 불합격)으로 판정한다."""
    instance, seen = _capturing_client(ClientConfig(
        port=1, server_timeouts={"API_QUERY_TIMEOUT": 900.0, "API_FILE_QUERY_TIMEOUT": 1200.0}))
    try:
        instance.send("plain", {"query": "q"})
        instance.send("plain", {"query": "q", "form_fill_answers": {"a": "b"}})
    finally:
        instance.close()
    assert [(path, t["read"]) for path, t in seen] == [
        ("/api/v1/query", 930.0), ("/api/v1/query", 1230.0)]


def test_파일_질의는_파일_질의_상한을_따른다(tmp_path) -> None:
    upload = tmp_path / "form.xlsx"
    upload.write_bytes(b"x")
    instance, seen = _capturing_client(ClientConfig(
        port=1, server_timeouts={"API_FILE_QUERY_TIMEOUT": 1200.0}))
    try:
        instance.send("file", {"query": "q"}, upload=upload)
    finally:
        instance.close()
    assert [(path, t["read"]) for path, t in seen] == [("/api/v1/query/file", 1230.0)]


def test_서버_상한을_모르거나_더_짧으면_timeout_sec_그대로다() -> None:
    instance, seen = _capturing_client(ClientConfig(port=1))
    short, seen_short = _capturing_client(ClientConfig(
        port=1, server_timeouts={"API_QUERY_TIMEOUT": 60.0}))
    try:
        instance.send("plain", {"query": "q"})
        short.send("plain", {"query": "q"})
    finally:
        instance.close()
        short.close()
    assert seen[0][1]["read"] == 360.0
    assert seen_short[0][1]["read"] == 360.0
