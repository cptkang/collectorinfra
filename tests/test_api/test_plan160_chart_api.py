"""plans/160 W2 — 그래프 보기 응답 신호(A-2)와 데이터 API(A-3).

- 신호 `chart`는 네 진입점(동기·스트림 × 텍스트·파일 · 스트림 폴백 · 시간 상한 부분 결과)에 같은
  모양으로 실리고, 대화 기록 턴에도 남는다.
- `GET /query/{id}/chart` — 소유자 인가(D-262) · 만료 문구 404 · 플래그 off 404 · 화면·CSV와 같은
  마스킹 · 복합 턴 표 경계 분할 · 감사 1행.
- 신호와 API는 같은 판정 함수를 쓴다(`src/api/result_chart_view.chart_tables`).

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import asyncio
import calendar
import io
import json
import os
import time
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "plan160-test-user-secret-" + "x" * 40
_T0 = 1_760_000_000_000  # epoch ms
_MASK = "***MASKED***"


def _wall_ms(epoch_ms: int) -> int:
    """epoch ms → 서버 시간대 벽시계를 UTC로 옮긴 ms(그래프 x 규약 — datetime 칼럼과 같다)."""
    return calendar.timegm(datetime.fromtimestamp(epoch_ms / 1000).timetuple()) * 1000


def _wall_iso(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).isoformat(timespec="seconds")


# ── 픽스처 ─────────────────────────────────────────────────────────────


def heap_rows() -> list[dict[str, Any]]:
    """힙 추세 모양 — 인스턴스 2개 × 지표별 시계열 6점(apm_gateway/tests/conftest.py 값)."""
    used = [700.0, 910.0, 920.0, 950.0, 960.0, 990.0]
    times = [_T0 + i * 60_000 for i in range(6)]
    return [
        {
            "source_id": "jennifer-1",
            "instance_id": 10 + k,
            "instance_name": name,
            "heap_used_mb": used[-1],
            "heap_committed_mb": 1000.0,
            "trend": {
                "heap_used_mb": [
                    {"time_ms": t, "value": u} for t, u in zip(times, used, strict=True)
                ],
                "heap_committed_mb": [{"time_ms": t, "value": 1000.0} for t in times],
            },
        }
        for k, name in enumerate(("inst-a", "inst-b"))
    ]


def list_rows() -> list[dict[str, Any]]:
    """목록형(텍스트 위주) — 그릴 수 없다."""
    return [
        {"hostname": f"web-0{i}", "os": "linux", "owner": f"team-{i}", "note": "운영"}
        for i in range(1, 5)
    ]


def line_rows(n: int = 6) -> list[dict[str, Any]]:
    return [{"ts": f"2026-10-13T0{i}:00:00", "cpu": 10.0 + i} for i in range(n)]


def bar_rows() -> list[dict[str, Any]]:
    return [{"name": f"svc-{c}", "calls": v} for c, v in zip("abc", (300, 120, 45), strict=True)]


def _final_output(rows: list[dict[str, Any]], tables: Any = None) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    out: dict[str, Any] = {
        "final_response": "결과입니다",
        "generated_sql": "SELECT 1",
        "query_results": [dict(r) for r in rows],
        "messages": [HumanMessage(content="q")],
    }
    if tables is not None:
        out["conversation_context"] = {"result_tables": tables}
    return out


class _Graph:
    """정상 종료 대역 — 스트림은 `LangGraph` 종료 이벤트 하나로 끝난다."""

    def __init__(self, rows: list[dict[str, Any]], tables: Any = None) -> None:
        self._rows = rows
        self._tables = tables

    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return _final_output(self._rows, self._tables)

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        output = _final_output(self._rows, self._tables)
        if self._tables is not None:
            # 집계기 델타가 표 경계를 누적 상태에 싣는다(스트림은 `_scope_state`에서 읽는다)
            yield {
                "event": "on_chain_end", "name": "result_aggregator", "parent_ids": [],
                "data": {"output": {"conversation_context": output["conversation_context"]}},
            }
        yield {"event": "on_chain_start", "name": "input_parser", "data": {}}
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": output}}


class _FallbackGraph(_Graph):
    """`astream_events`가 없는 그래프 — 스트림 라우트가 `ainvoke` 폴백으로 간다."""

    def __getattribute__(self, name: str) -> Any:
        if name == "astream_events":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _TimeoutGraph:
    """상한을 넘기는 대역 — 체크포인트에 이미 조회한 행이 있다(부분 결과 경로)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._state = {"query_results": rows}

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self._state)

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        await asyncio.sleep(3)
        raise AssertionError("상한 안에 끝나면 안 된다")  # pragma: no cover

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        yield {"event": "on_chain_start", "name": "context_resolver", "parent_ids": []}
        await asyncio.sleep(1.2)
        yield {"event": "on_chain_start", "name": "query_generator", "parent_ids": []}


class _Audit:
    def __init__(self) -> None:
        self.downloads: list[dict[str, Any]] = []

    async def log_user_request(self, **kwargs: Any) -> None:
        return None

    async def log_file_download(
        self, user_id, file_name, file_type, file_size, client_ip=None, request_id=None
    ) -> None:
        self.downloads.append(
            {"user_id": user_id, "file_name": file_name, "file_type": file_type,
             "file_size": file_size}
        )


class _FakeRepo:
    def __init__(self) -> None:
        self.turns: list[tuple[str, dict]] = []

    async def add_turn(self, owner_key: str, turn: dict) -> None:
        self.turns.append((owner_key, dict(turn)))

    async def get_turns(self, owner_key: str, thread_id: str) -> list[dict]:
        return [t for o, t in self.turns if o == owner_key and t["thread_id"] == thread_id]


def _config(*, chart: bool = True, explain: bool = False, auth: bool = True, timeout: int = 60):
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, AuthConfig, ResultVizConfig, SecurityConfig, ServerConfig

    return AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        server=ServerConfig(query_timeout=timeout, file_query_timeout=timeout),
        auth=AuthConfig(enabled=auth, jwt_secret=SECRET),
        # 검증 대상 필드는 명시한다(.env 누수 차단)
        security=SecurityConfig(
            sensitive_columns=["password", "token"],
            mask_pattern=_MASK,
            mask_ip=False,
            mask_email=False,
        ),
        result_viz=ResultVizConfig(chart_enabled=chart, explain_enabled=explain),
    )


def _client(graph: Any, *, audit: _Audit | None = None, repo: _FakeRepo | None = None,
            **cfg: Any) -> TestClient:
    from src.api.routes import conversation, query, result_viz

    app = FastAPI()
    app.state.config = _config(**cfg)
    app.state.graph = graph
    if audit is not None:
        app.state.audit_service = audit
    if repo is not None:
        app.state.thread_repo = repo
    app.include_router(query.router, prefix="/api/v1")
    app.include_router(result_viz.router, prefix="/api/v1")
    app.include_router(conversation.router, prefix="/api/v1")
    return TestClient(app)


def _auth(sub: str, role: str = "user") -> dict[str, str]:
    token = jwt.encode(
        {"sub": sub, "name": sub, "role": role, "type": "user", "exp": int(time.time()) + 3600},
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _sse(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def _post(client: TestClient, route: str, headers: dict | None = None) -> dict[str, Any]:
    """질의를 실제 라우트로 보내 최종 본문(동기 응답 또는 `done`)을 돌려준다."""
    if "file" in route:
        r = client.post(
            "/api/v1" + route,
            data={"query": "힙 추세", "thread_id": "th-1", "selected_db_ids": "polestar"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
            headers=headers or {},
        )
    else:
        r = client.post("/api/v1" + route, json={"query": "힙 추세", "thread_id": "th-1"},
                        headers=headers or {})
    assert r.status_code == 200, r.text
    if not route.endswith("/stream"):
        return r.json()
    done = [e for e in _sse(r.text) if e.get("type") == "done"]
    assert len(done) == 1, r.text[:500]
    return done[0]


_ROUTES = ("/query", "/query/stream", "/query/file", "/query/file/stream")
_HEAP_SIGNAL = {"kind": "line", "tables": [{"index": 0, "label": "", "kind": "line"}]}


@pytest.fixture(autouse=True)
def _clean_store():
    from src.api.routes import query

    query._results_store.clear()
    yield
    query._results_store.clear()


# ── 응답 신호(A-2) — 네 진입점 대칭 ─────────────────────────────────────


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("graph_cls", (_Graph, _FallbackGraph))
def test_signal_is_symmetric_across_entry_points(route: str, graph_cls: type) -> None:
    from src.api.routes import query

    client = _client(graph_cls(heap_rows()), auth=False)
    body = _post(client, route)

    assert body["chart"] == _HEAP_SIGNAL
    stored = query._results_store[body["query_id"]]
    assert stored["chart"] == _HEAP_SIGNAL
    assert stored["user_query"] == "힙 추세"
    assert stored["result_tables"] is None


@pytest.mark.parametrize("route", _ROUTES)
def test_flag_off_keeps_response_unchanged(route: str) -> None:
    from src.api.routes import query

    client = _client(_Graph(heap_rows()), chart=False, auth=False)
    body = _post(client, route)

    if route.endswith("/stream"):
        assert "chart" not in body, "플래그 off면 done에 키를 싣지 않는다(바이트 불변)"
    else:
        assert body["chart"] is None
    stored = query._results_store[body["query_id"]]
    assert stored["chart"] is None
    assert client.get(f"/api/v1/query/{body['query_id']}/chart").status_code == 404


@pytest.mark.parametrize("route", _ROUTES)
def test_partial_timeout_carries_signal(route: str) -> None:
    client = _client(_TimeoutGraph(line_rows()), auth=False, timeout=1)
    body = _post(client, route)

    assert body["status"] == "partial"
    assert body["chart"] == {"kind": "line", "tables": [{"index": 0, "label": "", "kind": "line"}]}


def test_list_rows_have_no_signal_and_chart_404() -> None:
    client = _client(_Graph(list_rows()), auth=False)
    body = _post(client, "/query")

    assert body["chart"] is None
    r = client.get(f"/api/v1/query/{body['query_id']}/chart")
    assert r.status_code == 404
    assert r.json()["detail"] == "그래프로 그릴 수 있는 표가 없습니다."


def test_signal_failure_is_logged_and_does_not_break_answer(monkeypatch, caplog) -> None:
    from src.api import result_chart_view

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("판정 폭발")

    monkeypatch.setattr(result_chart_view, "chart_tables", boom)
    client = _client(_Graph(heap_rows()), auth=False)
    with caplog.at_level("WARNING", logger="src.api.result_chart_view"):
        body = _post(client, "/query")

    assert body["chart"] is None
    assert body["response"] == "결과입니다"
    assert any("chart 신호 생략" in rec.message for rec in caplog.records)


def test_thread_turn_keeps_chart_and_restores_it() -> None:
    repo = _FakeRepo()
    client = _client(_Graph(heap_rows()), repo=repo, auth=False)
    client.headers["X-Client-Id"] = "0123456789abcdef0123456789abcdef"
    for route in ("/query", "/query/stream"):
        _post(client, route)

    assert [t["chart"] for _, t in repo.turns] == [_HEAP_SIGNAL, _HEAP_SIGNAL]
    restored = client.get("/api/v1/threads/th-1").json()["turns"]
    assert [t["chart"] for t in restored] == [_HEAP_SIGNAL, _HEAP_SIGNAL]


# ── 데이터 API(A-3) ──────────────────────────────────────────────────


def test_heap_trend_chart_is_line_split_by_instance() -> None:
    audit = _Audit()
    client = _client(_Graph(heap_rows()), audit=audit)
    qid = _post(client, "/query", _auth("u1"))["query_id"]

    r = client.get(f"/api/v1/query/{qid}/chart", headers=_auth("u1"))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query_id"] == qid and body["explain_enabled"] is False
    (table,) = body["tables"]
    assert (table["index"], table["label"], table["kind"]) == (0, "", "line")
    assert table["spec"] == {
        "kind": "line", "x": "time_ms", "y": ["heap_used_mb", "heap_committed_mb"],
        "series_by": "instance_name", "x_type": "time", "series_total": 2,
    }
    names = [s["name"] for s in table["data"]["series"]]
    assert names == [
        "inst-a · heap_used_mb", "inst-a · heap_committed_mb",
        "inst-b · heap_used_mb", "inst-b · heap_committed_mb",
    ]
    # epoch x는 서버 벽시계로 그린다(문장·표와 같은 시각 — datetime 칼럼과 같은 규약)
    assert table["data"]["series"][0]["points"][0] == [_wall_ms(_T0), 700.0]
    assert table["data"]["series"][0]["points"][-1] == [_wall_ms(_T0 + 5 * 60_000), 990.0]
    assert len(table["rows"]) == 12, "격자 행은 시간 점마다 1행으로 편 행이다"
    assert table["rows_total"] == 12
    assert table["rows"][0]["time_ms"] == _wall_iso(_T0), "격자도 읽을 수 있는 벽시계 문자열"
    assert table["columns"] == ["source_id", "instance_name", "time_ms", "heap_used_mb",
                                "heap_committed_mb"]
    assert audit.downloads == [
        {"user_id": "u1", "file_name": f"{qid}.chart", "file_type": "chart",
         "file_size": len(json.dumps(body, ensure_ascii=False).encode("utf-8"))}
    ]


@pytest.mark.parametrize("tz", ["Asia/Seoul", "UTC"])
def test_epoch_and_wall_clock_datetime_share_x(monkeypatch, tz: str) -> None:
    """같은 순간을 epoch ms와 naive 벽시계 datetime으로 받으면 그래프 x·격자 시각이 같다."""
    if not hasattr(time, "tzset"):
        pytest.skip("tzset 없음")
    from src.api.result_chart_view import chart_tables

    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        walls = [datetime(2026, 10, 13, 9, i) for i in range(3)]
        epoch_rows = [{"t": int(w.timestamp() * 1000), "v": float(i)} for i, w in enumerate(walls)]
        wall_rows = [{"t": w, "v": float(i)} for i, w in enumerate(walls)]
        security = _config().security
        (ep,) = chart_tables(epoch_rows, None, security)
        (wa,) = chart_tables(wall_rows, None, security)
        assert ep.spec == wa.spec
        from src.domain.result_chart import build_chart_data

        assert build_chart_data(ep.rows, ep.spec)["series"] == (
            build_chart_data(wa.rows, wa.spec)["series"]
        )
        assert ep.rows[0]["t"] == "2026-10-13T09:00:00"
    finally:
        monkeypatch.delenv("TZ", raising=False)
        time.tzset()


def test_grid_rows_are_capped_but_chart_uses_all_rows(monkeypatch) -> None:
    from src.api.routes import result_viz

    monkeypatch.setattr(result_viz, "GRID_MAX_ROWS", 4)
    client = _client(_Graph(line_rows(6)), auth=False)
    qid = _post(client, "/query")["query_id"]

    (table,) = client.get(f"/api/v1/query/{qid}/chart").json()["tables"]

    assert len(table["rows"]) == 4 and table["rows_total"] == 6
    assert len(table["data"]["series"][0]["points"]) == 6


def test_explain_flag_is_reported() -> None:
    client = _client(_Graph(heap_rows()), explain=True, auth=False)
    qid = _post(client, "/query")["query_id"]
    assert client.get(f"/api/v1/query/{qid}/chart").json()["explain_enabled"] is True


def test_other_user_is_denied_admin_and_anonymous_pass() -> None:
    client = _client(_Graph(heap_rows()))
    qid = _post(client, "/query", _auth("owner"))["query_id"]

    denied = client.get(f"/api/v1/query/{qid}/chart", headers=_auth("intruder"))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "이 결과는 질의한 사용자만 받을 수 있습니다."
    admin = client.get(f"/api/v1/query/{qid}/chart", headers=_auth("boss", "admin"))
    assert admin.status_code == 200
    assert client.get(f"/api/v1/query/{qid}/chart").status_code == 401


def test_auth_off_passes() -> None:
    client = _client(_Graph(heap_rows()), auth=False)
    qid = _post(client, "/query")["query_id"]
    assert client.get(f"/api/v1/query/{qid}/chart").status_code == 200


def test_missing_result_is_expired_404() -> None:
    client = _client(_Graph(heap_rows()), auth=False)
    r = client.get("/api/v1/query/no-such-id/chart")
    assert r.status_code == 404
    assert r.json()["detail"] == "결과가 만료되었습니다 — 같은 질문을 다시 실행해 주세요"


def test_zero_rows_is_404() -> None:
    from src.api.routes import query

    client = _client(_Graph([]), auth=False)
    query._store_result("q-empty", {"query_results": []}, owner=None)
    r = client.get("/api/v1/query/q-empty/chart")
    assert r.status_code == 404
    assert r.json()["detail"] == "그래프로 볼 조회 결과가 없습니다."


def test_masking_matches_screen_and_csv() -> None:
    rows = [
        {"ts": f"2026-10-13T0{i}:00:00", "host": f"web-0{i % 2}", "cpu": 10.0 + i,
         "db_password": f"secret-raw-{i}"}
        for i in range(6)
    ]
    client = _client(_Graph(rows), auth=False)
    qid = _post(client, "/query")["query_id"]

    r = client.get(f"/api/v1/query/{qid}/chart")

    assert r.status_code == 200
    assert "secret-raw" not in r.text
    (table,) = r.json()["tables"]
    assert {row["db_password"] for row in table["rows"]} == {_MASK}


def test_nested_metric_keys_are_masked_after_flattening() -> None:
    """마스커는 중첩 값 안을 보지 않는다 — 편 뒤 생긴 칼럼에도 같은 규칙을 건다."""
    rows = heap_rows()
    for row in rows:
        row["trend"]["token_count"] = [
            {"time_ms": _T0 + i * 60_000, "value": 4242.0 + i} for i in range(6)
        ]
    client = _client(_Graph(rows), auth=False)
    qid = _post(client, "/query")["query_id"]

    (table,) = client.get(f"/api/v1/query/{qid}/chart").json()["tables"]

    assert {row["token_count"] for row in table["rows"]} == {_MASK}
    assert "token_count" not in table["spec"]["y"]
    assert all(s["column"] != "token_count" for s in table["data"]["series"])


def _composite(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> tuple[list, list]:
    tables = [{"label": "첫 표", "rows": len(first)}, {"label": "둘째 표", "rows": len(second)}]
    return first + second, tables


@pytest.mark.parametrize("route", ("/query", "/query/stream"))
def test_composite_turn_splits_tables(route: str) -> None:
    from src.api.routes import query

    rows, tables = _composite(line_rows(), bar_rows())
    client = _client(_Graph(rows, tables), auth=False)
    body = _post(client, route)

    assert body["chart"] == {
        "kind": "line",
        "tables": [
            {"index": 0, "label": "첫 표", "kind": "line"},
            {"index": 1, "label": "둘째 표", "kind": "bar"},
        ],
    }
    assert query._results_store[body["query_id"]]["result_tables"] == tables
    data = client.get(f"/api/v1/query/{body['query_id']}/chart").json()
    assert [(t["index"], t["kind"], len(t["rows"])) for t in data["tables"]] == [
        (0, "line", 6), (1, "bar", 3),
    ]


def test_composite_keeps_original_table_index() -> None:
    rows, tables = _composite(list_rows(), bar_rows())
    client = _client(_Graph(rows, tables), auth=False)
    body = _post(client, "/query")

    assert body["chart"] == {
        "kind": "bar", "tables": [{"index": 1, "label": "둘째 표", "kind": "bar"}],
    }
    (table,) = client.get(f"/api/v1/query/{body['query_id']}/chart").json()["tables"]
    assert table["index"] == 1
    assert table["data"]["series"][0]["points"] == [
        ["svc-a", 300.0], ["svc-b", 120.0], ["svc-c", 45.0],
    ]


# ── 대화 기록 저장소 ──────────────────────────────────────────────────


class _Conn:
    def __init__(self, fetch_rows: list[dict[str, Any]] | None = None) -> None:
        self.executed: list[tuple] = []
        self._fetch_rows = fetch_rows or []

    async def execute(self, sql: str, *args: Any) -> str:
        self.executed.append((sql, args))
        return "DELETE 0"

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.executed.append((sql, args))
        return self._fetch_rows

    def transaction(self) -> Any:
        conn = self

        class _Tx:
            async def __aenter__(self) -> Any:
                return conn

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Tx()


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self._conn = conn

    def acquire(self) -> Any:
        conn = self._conn

        class _Acq:
            async def __aenter__(self) -> _Conn:
                return conn

            async def __aexit__(self, *exc: Any) -> None:
                return None

        return _Acq()


def test_repository_adds_chart_column_and_round_trips() -> None:
    from src.infrastructure.thread_repository import THREAD_DDL, PostgresThreadRepository

    assert "ADD COLUMN IF NOT EXISTS chart JSONB" in THREAD_DDL
    conn = _Conn()
    repo = PostgresThreadRepository(_Pool(conn))  # type: ignore[arg-type]
    asyncio.run(repo.add_turn("user:u1", {"thread_id": "th-1", "user_query": "q",
                                          "chart": _HEAP_SIGNAL}))
    sql, args = conn.executed[0]
    assert "chart" in sql and len(args) == 13
    assert json.loads(args[-1]) == _HEAP_SIGNAL

    asyncio.run(repo.add_turn("user:u1", {"thread_id": "th-1", "user_query": "q"}))
    assert conn.executed[2][1][-1] is None, "신호가 없으면 NULL"

    row = {
        "query_id": "q1", "user_query": "q", "response": "r", "status": "completed",
        "executed_sql": None, "row_count": 12, "processing_time_ms": 1.0, "has_upload": False,
        "db_scope": None, "disclosures": None, "chart": json.dumps(_HEAP_SIGNAL),
        "created_at": None,
    }
    turns = asyncio.run(PostgresThreadRepository(_Pool(_Conn([row]))).get_turns("user:u1", "th-1"))  # type: ignore[arg-type]
    assert turns[0]["chart"] == _HEAP_SIGNAL


# ── 플래그 off = 라우트 부재(W2 교정) ─────────────────────────────────


@pytest.mark.parametrize("chart", [False, True])
def test_create_app_registers_viz_routes_only_when_chart_enabled(chart: bool) -> None:
    from src.api.server import create_app

    app = create_app(_config(chart=chart, explain=True))
    paths = {getattr(r, "path", "") for r in app.routes}
    viz = {"/api/v1/query/{query_id}/chart", "/api/v1/query/{query_id}/glossary",
           "/api/v1/query/{query_id}/interpret"}
    assert (viz & paths) == (viz if chart else set())


def test_explain_alone_without_chart_is_404() -> None:
    """격자는 그래프 패널 안에만 있다 — 그래프 보기가 꺼지면 해설도 404(인증보다 먼저)."""
    client = _client(_Graph(heap_rows()), chart=False, explain=True)
    headers = _auth("u1")
    qid = _post(client, "/query", headers)["query_id"]

    assert client.get(f"/api/v1/query/{qid}/glossary?table=0&column=heap_used_mb",
                      headers=headers).status_code == 404
    assert client.post(f"/api/v1/query/{qid}/interpret",
                       json={"table": 0, "column": "heap_used_mb", "row": 0},
                       headers=headers).status_code == 404
    assert client.get(f"/api/v1/query/{qid}/glossary?table=0&column=c").status_code == 404
