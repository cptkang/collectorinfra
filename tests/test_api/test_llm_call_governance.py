"""LLM 호출 거버넌스 테스트 (권고 ①③).

- 권고 ③: SSE 스트림 폴백이 1차 실행 완주(END) 후에는 graph.ainvoke로 그래프를
  재실행하지 않고 체크포인터의 최종 상태에서 응답을 회수한다(LLM 0회).
  회수 불가(상태에 final_response 없음·체크포인트 없음·스트림 미완주)면
  종전 ainvoke 폴백을 그대로 수행한다(동작 불변).
- 권고 ①: 요청 진입 시 start_request, 종료 시 finish_request가 배선되어
  요청당 LLM 호출 요약 로그([LLM계측])가 1줄 남는다.

LangGraph 실행·LLM 호출은 전부 mock 처리한다(실 LLM 호출 0회, D-127).
"""

import json
import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# 공용 헬퍼
# ---------------------------------------------------------------------------

_RECOVERED_STATE = {
    "final_response": "체크포인트에서 회수된 응답",
    "generated_sql": "SELECT 1",
    "query_results": [{"id": 1}],
    "messages": [],
    "awaiting_approval": False,
    "output_file": None,
    "output_file_name": None,
    "mapping_report_md": None,
}

_AINVOKE_RESULT = {
    "final_response": "ainvoke 재실행 응답",
    "generated_sql": "SELECT 2",
    "query_results": [],
    "messages": [],
    "awaiting_approval": False,
    "output_file": None,
    "output_file_name": None,
    "mapping_report_md": None,
}


class _EmptyStreamGraph:
    """astream_events가 이벤트 0건으로 완주하는 그래프 목 — SSE 폴백 경로 유도.

    query.py의 event_generator는 스트림이 완주해도 final_response 이벤트가 없으면
    폴백으로 넘어간다. get_state는 주입된 체크포인트 상태를 돌려준다.
    """

    def __init__(self, state_values):
        self._state_values = state_values
        self.ainvoke_calls = 0

    async def astream_events(self, input_state, config, version="v2"):
        return
        yield  # pragma: no cover — 빈 async generator를 만들기 위한 구문

    def get_state(self, config):
        if self._state_values is None:
            return None
        snapshot = MagicMock()
        snapshot.values = self._state_values
        return snapshot

    async def ainvoke(self, input_state, config):
        self.ainvoke_calls += 1
        return dict(_AINVOKE_RESULT)


class _BrokenStreamGraph(_EmptyStreamGraph):
    """astream_events 호출이 즉시 실패하는 그래프 목 — 스트림 미완주(그래프 미실행)."""

    def astream_events(self, input_state, config, version="v2"):
        raise NotImplementedError("astream_events 미지원")


@contextmanager
def _client_with_graph(graph):
    """test_routes.py와 동일한 방식으로 그래프만 갈아끼운 TestClient를 생성한다."""
    with patch("src.api.server.build_graph") as mock_build, \
         patch("src.api.server.setup_logging"), \
         patch("src.api.server.load_config") as mock_config:

        mock_config.return_value = MagicMock()
        mock_config.return_value.checkpoint_backend = "sqlite"
        mock_config.return_value.checkpoint_db_url = ":memory:"
        mock_config.return_value.server.query_timeout = 30.0
        mock_config.return_value.server.file_query_timeout = 60.0
        mock_config.return_value.auth.enabled = False
        mock_build.return_value = graph

        from src.api.server import create_app
        app = create_app()
        app.state.graph = graph
        app.state.config = mock_config.return_value

        with TestClient(app) as client:
            yield client


def _sse_events(text: str) -> list[dict]:
    """SSE 응답 본문을 이벤트 dict 목록으로 파싱한다."""
    events = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def _done_event(text: str) -> dict:
    done = [e for e in _sse_events(text) if e.get("type") == "done"]
    assert len(done) == 1, f"done 이벤트가 정확히 1건이어야 한다: {_sse_events(text)}"
    return done[0]


# ---------------------------------------------------------------------------
# 권고 ③ — SSE 폴백의 체크포인트 상태 회수
# ---------------------------------------------------------------------------


class TestStreamFallbackCheckpointRecovery:
    """POST /api/v1/query/stream 폴백 검증."""

    def test_fallback_recovers_from_checkpoint_without_ainvoke(self, caplog):
        """스트림 완주 + 상태에 final_response 존재 → ainvoke 없이 회수한다."""
        graph = _EmptyStreamGraph(dict(_RECOVERED_STATE))
        with _client_with_graph(graph) as client, caplog.at_level(logging.INFO):
            resp = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 0

        events = _sse_events(resp.text)
        tokens = [e for e in events if e["type"] == "token"]
        assert tokens and tokens[0]["content"] == "체크포인트에서 회수된 응답"

        done = _done_event(resp.text)
        assert done["response"] == "체크포인트에서 회수된 응답"
        assert done["executed_sql"] == "SELECT 1"
        assert done["row_count"] == 1
        assert done["has_file"] is False

        assert any(
            "체크포인트 상태 회수로 재실행 생략" in r.getMessage()
            for r in caplog.records
        )

    def test_fallback_without_final_response_reinvokes(self):
        """상태에 final_response가 없으면 종전대로 ainvoke 폴백을 수행한다."""
        graph = _EmptyStreamGraph({"messages": []})
        with _client_with_graph(graph) as client:
            resp = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 1
        assert _done_event(resp.text)["response"] == "ainvoke 재실행 응답"

    def test_fallback_without_checkpoint_reinvokes(self):
        """체크포인트 자체가 없으면 종전대로 ainvoke 폴백을 수행한다."""
        graph = _EmptyStreamGraph(None)
        with _client_with_graph(graph) as client:
            resp = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 1
        assert _done_event(resp.text)["response"] == "ainvoke 재실행 응답"

    def test_incomplete_stream_reinvokes_even_with_saved_response(self):
        """스트림이 완주하지 못했다면(그래프 미실행) 상태에 final_response가 있어도
        회수하지 않는다 — 직전 턴 응답을 재사용하는 오답을 막는 안전 조건."""
        graph = _BrokenStreamGraph(dict(_RECOVERED_STATE))
        with _client_with_graph(graph) as client:
            resp = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 1
        assert _done_event(resp.text)["response"] == "ainvoke 재실행 응답"


class TestFileStreamFallbackCheckpointRecovery:
    """POST /api/v1/query/file/stream 폴백 검증 (docx — CSV 변환 경로 우회)."""

    def _post(self, client):
        return client.post(
            "/api/v1/query/file/stream",
            data={"query": "양식 채워줘"},
            files={"file": ("양식.docx", b"dummy-docx", "application/octet-stream")},
        )

    def test_fallback_recovers_from_checkpoint_without_ainvoke(self, caplog):
        graph = _EmptyStreamGraph(dict(_RECOVERED_STATE))
        with _client_with_graph(graph) as client, caplog.at_level(logging.INFO):
            resp = self._post(client)

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 0

        done = _done_event(resp.text)
        assert done["response"] == "체크포인트에서 회수된 응답"
        assert done["executed_sql"] == "SELECT 1"
        assert done["row_count"] == 1

        assert any(
            "체크포인트 상태 회수로 재실행 생략" in r.getMessage()
            for r in caplog.records
        )

    def test_fallback_without_final_response_reinvokes(self):
        graph = _EmptyStreamGraph({"messages": []})
        with _client_with_graph(graph) as client:
            resp = self._post(client)

        assert resp.status_code == 200
        assert graph.ainvoke_calls == 1
        assert _done_event(resp.text)["response"] == "ainvoke 재실행 응답"


# ---------------------------------------------------------------------------
# 권고 ① — 요청당 LLM 호출 계측 배선 (start_request / finish_request)
# ---------------------------------------------------------------------------


class TestLLMCallCounterWiring:
    """요청 1건 처리 시 start/finish 배선과 요약 로그를 검증한다."""

    @staticmethod
    def _summary_records(caplog):
        return [
            r for r in caplog.records
            if r.name == "src.observability.llm_call_counter"
            and "[LLM계측] request_id=" in r.getMessage()
        ]

    def test_query_endpoint_emits_counter_summary(self, monkeypatch, caplog):
        """POST /query: 진입 시 start_request, 종료 시 finish_request 요약 로그."""
        import src.api.routes.query as query_module

        started = []
        real_start = query_module.start_request

        def _spy_start(request_id):
            started.append(request_id)
            real_start(request_id)

        monkeypatch.setattr("src.api.routes.query.start_request", _spy_start)

        graph = _EmptyStreamGraph(None)
        with _client_with_graph(graph) as client, caplog.at_level(logging.INFO):
            resp = client.post("/api/v1/query", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert len(started) == 1
        summaries = self._summary_records(caplog)
        assert len(summaries) == 1
        # mock 그래프 실행이므로 실 LLM 호출은 0회여야 한다.
        assert "llm_calls=0" in summaries[0].getMessage()

    def test_stream_endpoint_emits_counter_summary(self, caplog):
        """POST /query/stream: 스트림 종료 시(finally) 요약 로그가 남는다."""
        graph = _EmptyStreamGraph(dict(_RECOVERED_STATE))
        with _client_with_graph(graph) as client, caplog.at_level(logging.INFO):
            resp = client.post("/api/v1/query/stream", json={"query": "서버 목록"})

        assert resp.status_code == 200
        assert len(self._summary_records(caplog)) == 1

    def test_file_stream_endpoint_emits_counter_summary(self, caplog):
        """POST /query/file/stream: 스트림 종료 시 요약 로그가 남는다."""
        graph = _EmptyStreamGraph(dict(_RECOVERED_STATE))
        with _client_with_graph(graph) as client, caplog.at_level(logging.INFO):
            resp = client.post(
                "/api/v1/query/file/stream",
                data={"query": "양식 채워줘"},
                files={"file": ("양식.docx", b"dummy", "application/octet-stream")},
            )

        assert resp.status_code == 200
        assert len(self._summary_records(caplog)) == 1
