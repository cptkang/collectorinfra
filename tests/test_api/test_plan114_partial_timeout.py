"""시간 상한에 걸린 턴의 부분 결과 응답 — plans/114 P-2 (게이트 G-E).

조회까지 끝내고 **서술에서** 상한을 넘긴 턴은 행을 버리지 않는다. 직전 폐쇄망 run 에서
55턴이 SQL 실행 뒤 서술 중에 죽어 결과를 전부 잃었다(§2.3). 여기서 고정하는 것은 넷이다.

1. 행이 있으면 오류 대신 **결정적 표**(LLM 0)로 답하고 상태는 `partial` 이다.
2. 행이 없으면 **종전 오류 그대로**다 — 빈 표를 답이라고 내보내지 않는다.
3. 네 진입점이 대칭이다(비스트림·스트림 × 텍스트·파일).
4. 사다리 단 대칭 — 3단은 `query_results`/`organized_data`, 2단은 `task_results` 에서 건진다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.domain.partial_result import (
    PARTIAL_STATUS,
    PartialAnswer,
    extract_partial_answer,
    render_partial_text,
)

ROWS = [{"hostname": "kpo-web-01", "cpu": 91.5}, {"hostname": "kpo-web-02", "cpu": 88.0}]


# ── 상태에서 행 건지기 (사다리 단 대칭) ─────────────────────────────────────


def test_3단_단일_경로는_query_results에서_건진다() -> None:
    answer = extract_partial_answer({"query_results": ROWS})

    assert answer is not None
    assert answer.rows == ROWS and answer.source == "query_results"


def test_원시_행이_없으면_organized_data의_행을_쓴다() -> None:
    answer = extract_partial_answer({"query_results": [], "organized_data": {"rows": ROWS}})

    assert answer is not None
    assert answer.rows == ROWS and answer.source == "organized_data"


def test_2단은_task_results에서_마지막으로_행을_낸_작업을_쓴다() -> None:
    """작업마다 컬럼이 달라 이어 붙이면 표가 깨진다 — 마지막 것을 쓰고 몇 건 중 몇인지 말한다."""
    state = {
        "task_results": {
            "t1": {"query_results": [{"a": 1}]},
            "t2": {"organized_data": {"rows": ROWS}},
        }
    }

    answer = extract_partial_answer(state)

    assert answer is not None
    assert answer.rows == ROWS
    assert (answer.source, answer.task_id, answer.tasks_with_rows) == ("task", "t2", 2)


def test_행이_없으면_None이라_종전_오류로_간다() -> None:
    assert extract_partial_answer({"query_results": [], "task_results": {}}) is None
    assert extract_partial_answer(None) is None


def test_행이_아닌_값이_섞여도_표가_깨지지_않는다() -> None:
    answer = extract_partial_answer({"query_results": ["문자열", None, {"a": 1}]})

    assert answer is not None and answer.rows == [{"a": 1}]


# ── 표 렌더 (LLM 0) ────────────────────────────────────────────────────────


def test_사유를_먼저_말하고_표를_싣는다() -> None:
    text = render_partial_text(PartialAnswer(rows=ROWS, source="query_results"), limit_sec=120)

    assert text.startswith("서술 생성이 시간 상한(설정 상한 120초)을 넘어 표로 대신합니다.")
    assert "| hostname | cpu |" in text
    assert "| kpo-web-01 | 91.5 |" in text
    assert "총 2건입니다." in text


def test_상한을_넘는_행은_잘라_싣고_남은_건수를_말한다() -> None:
    rows = [{"n": i} for i in range(50)]

    text = render_partial_text(PartialAnswer(rows=rows, source="query_results"), max_rows=20)

    assert text.count("\n| ") == 21          # 헤더 1 + 본문 20
    assert "전체 50건 중 상위 20건입니다." in text
    assert "CSV 내려받기" in text


def test_컬럼이_다른_행은_키_합집합으로_싣는다() -> None:
    text = render_partial_text(
        PartialAnswer(rows=[{"a": 1}, {"b": 2}], source="query_results")
    )

    assert "| a | b |" in text
    assert "| 1 |  |" in text and "|  | 2 |" in text


def test_파이프_문자는_탈출해_표를_깨뜨리지_않는다() -> None:
    text = render_partial_text(
        PartialAnswer(rows=[{"expr": "a|b"}], source="query_results")
    )

    assert r"| a\|b |" in text


def test_하위_작업이_여럿이면_어느_작업의_결과인지_말한다() -> None:
    text = render_partial_text(
        PartialAnswer(rows=ROWS, source="task", task_id="t2", tasks_with_rows=2)
    )

    assert "하위 작업 2건이 결과를 냈고" in text and "`t2`" in text


# ── 라우트 4곳 ─────────────────────────────────────────────────────────────


class _TimeoutGraph:
    """상한을 넘기는 대역 그래프. 체크포인트에는 이미 조회한 행이 들어 있다."""

    def __init__(self, state: dict | None) -> None:
        self._state = state

    def get_state(self, config: dict) -> Any:
        return SimpleNamespace(values=self._state) if self._state is not None else None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        await asyncio.sleep(3)          # 상한(1초)을 넘긴다
        raise AssertionError("상한 안에 끝나면 안 된다")  # pragma: no cover

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        yield {"event": "on_chain_start", "name": "context_resolver", "parent_ids": []}
        await asyncio.sleep(1.2)        # 전체 경과 상한을 넘긴다
        yield {"event": "on_chain_start", "name": "query_generator", "parent_ids": []}


class _FakeRepo:
    def __init__(self) -> None:
        self.turns: list[tuple[str, dict]] = []

    async def add_turn(self, owner: str, turn: dict) -> None:
        self.turns.append((owner, turn))


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=1, file_query_timeout=1),
    )


def _client(graph: _TimeoutGraph, app_config, repo: _FakeRepo | None = None) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = graph
    if repo is not None:
        app.state.thread_repo = repo
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


def _post(client: TestClient, route: str):
    if route == "text":
        return client.post("/api/v1/query", json={"query": "서버 목록"})
    return client.post(
        "/api/v1/query/file",
        data={"query": "서버 목록"},
        files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
    )


def _stream(client: TestClient, route: str) -> list[dict]:
    if route == "text":
        r = client.post("/api/v1/query/stream", json={"query": "서버 목록"})
    else:
        r = client.post(
            "/api/v1/query/file/stream",
            data={"query": "서버 목록"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


@pytest.mark.parametrize("route", ("text", "file"))
def test_비스트림_상한에서_조회한_행을_표로_돌려준다(route, app_config) -> None:
    client = _client(_TimeoutGraph({"query_results": ROWS}), app_config)

    r = _post(client, route)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == PARTIAL_STATUS
    assert body["row_count"] == 2
    assert "서술 생성이 시간 상한" in body["response"]
    assert "| hostname | cpu |" in body["response"]


@pytest.mark.parametrize("route", ("text", "file"))
def test_비스트림_행이_없으면_종전대로_504다(route, app_config) -> None:
    r = _post(_client(_TimeoutGraph({"query_results": []}), app_config), route)

    assert r.status_code == 504
    assert "처리 시간이 초과" in r.json()["detail"]


@pytest.mark.parametrize("route", ("text", "file"))
def test_스트림_상한은_오류가_아니라_done_partial로_끝난다(route, app_config) -> None:
    events = _stream(_client(_TimeoutGraph({"query_results": ROWS}), app_config), route)

    done = [e for e in events if e.get("type") == "done"]
    assert len(done) == 1, events
    assert done[0]["status"] == PARTIAL_STATUS
    assert "| hostname | cpu |" in done[0]["response"]
    assert not [e for e in events if e.get("type") == "error"]


@pytest.mark.parametrize("route", ("text", "file"))
def test_스트림_행이_없으면_종전_오류_이벤트다(route, app_config) -> None:
    events = _stream(_client(_TimeoutGraph(None), app_config), route)

    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1, events
    assert errors[0].get("code") == "timeout"
    assert not [e for e in events if e.get("type") == "done"]


def test_부분_결과는_CSV로_내려받을_수_있다(app_config) -> None:
    """서술을 못 만든 턴일수록 전체 행이 필요하다 — 표는 상위 N행뿐이다."""
    client = _client(_TimeoutGraph({"query_results": ROWS}), app_config)

    body = _post(client, "text").json()
    csv = client.get(f"/api/v1/query/{body['query_id']}/download-csv")

    assert csv.status_code == 200
    assert "hostname" in csv.text and "kpo-web-01" in csv.text


def test_스레드_이력에_partial로_남는다(app_config) -> None:
    """`completed`로 남기면 서술이 빠진 턴을 나중에 구별할 수 없다(D-248 `_turn_status`)."""
    repo = _FakeRepo()
    client = _client(_TimeoutGraph({"query_results": ROWS}), app_config, repo)

    _post(client, "text")

    assert [turn["status"] for _owner, turn in repo.turns] == [PARTIAL_STATUS]


def test_스트림_부분_결과도_스레드_이력에_남는다(app_config) -> None:
    """오류 이벤트는 기록되지 않는다 — `done`으로 내보내야 턴이 남는다."""
    repo = _FakeRepo()
    client = _client(_TimeoutGraph({"query_results": ROWS}), app_config, repo)

    _stream(client, "text")

    assert [turn["status"] for _owner, turn in repo.turns] == [PARTIAL_STATUS]


def test_2단_상태에서도_부분_결과가_나간다(app_config) -> None:
    """2단은 행이 `task_results` 에 있다 — 한쪽만 보면 그 단에서만 오류가 난다."""
    state = {"task_results": {"t1": {"query_results": ROWS}}}

    body = _post(_client(_TimeoutGraph(state), app_config), "text").json()

    assert body["status"] == PARTIAL_STATUS and body["row_count"] == 2
