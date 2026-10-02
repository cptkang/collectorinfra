"""질의응답 스레드 이력 — D-248.

- 소유 키: 로그인 사용자는 user_id, 익명은 브라우저 식별자(X-Client-Id), 둘 다 없으면 기록·조회 없음
- 기록: 완결된 턴만(done · QueryResponse), done을 내보내기 **전에** 기록,
  기록 실패는 응답을 막지 않음
- 진입점 4곳 대칭(`/query`·`/query/stream`·`/query/file`·`/query/file/stream`)
- 조회 API는 소유 키로만 거른다(남의 대화는 404)
- 사용자 화면: Admin·노이즈 관제는 새 탭, 스레드 ID 갱신은 setCurrentThread 한 곳

실 LLM 0 · 네트워크 0 · DB 0 (저장소는 메모리 대역).
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import ANONYMOUS_USER, require_user
from src.api.thread_history import TurnRecorder, history_owner

ROOT = Path(__file__).resolve().parents[2]
CID = "0123456789abcdef0123456789abcdef"


class _FakeRepo:
    def __init__(self, *, fail: bool = False) -> None:
        self.turns: list[tuple[str, dict]] = []
        self.fail = fail

    async def add_turn(self, owner_key: str, turn: dict) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.turns.append((owner_key, dict(turn)))

    async def list_threads(self, owner_key: str, limit: int = 200) -> list[dict]:
        seen: dict[str, list[dict]] = {}
        for owner, t in self.turns:
            if owner == owner_key:
                seen.setdefault(t["thread_id"], []).append(t)
        return [
            {
                "thread_id": tid,
                "title": ts[0]["user_query"],
                "queries": "\n".join(x["user_query"] for x in ts),
                "turn_count": len(ts),
                "started_at": None,
                "updated_at": None,
            }
            for tid, ts in reversed(list(seen.items()))
        ][:limit]

    async def get_turns(self, owner_key: str, thread_id: str) -> list[dict]:
        return [t for o, t in self.turns if o == owner_key and t["thread_id"] == thread_id]

    async def delete_thread(self, owner_key: str, thread_id: str) -> int:
        before = len(self.turns)
        self.turns = [
            (o, t) for o, t in self.turns if not (o == owner_key and t["thread_id"] == thread_id)
        ]
        return before - len(self.turns)

    async def delete_all(self, owner_key: str) -> int:
        before = len(self.turns)
        self.turns = [(o, t) for o, t in self.turns if o != owner_key]
        return before - len(self.turns)


def _request(repo: Any, headers: dict | None = None) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(thread_repo=repo)), headers=headers or {}
    )


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 소유 키
# ---------------------------------------------------------------------------


def test_owner_is_user_id_for_logged_in_user() -> None:
    # 로그인 사용자는 브라우저 식별자를 보지 않는다 — 기기를 바꿔도 같은 목록이다
    assert history_owner(_request(None, {"X-Client-Id": CID}), {"sub": "kim"}) == "user:kim"


def test_owner_is_browser_id_for_anonymous() -> None:
    assert history_owner(_request(None, {"X-Client-Id": CID}), ANONYMOUS_USER) == f"anon:{CID}"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Client-Id": "short"},
        {"X-Client-Id": "a b c d e f g h"},
        {"X-Client-Id": "x" * 65},
        {"X-Client-Id": "../../etc/passwd"},
    ],
)
def test_anonymous_without_valid_browser_id_has_no_owner(headers: dict) -> None:
    # 전원이 anonymous 하나로 뭉치면 남의 대화가 섞인다(D-183 G-1) — 차라리 기록하지 않는다
    assert history_owner(_request(None, headers), ANONYMOUS_USER) is None


# ---------------------------------------------------------------------------
# TurnRecorder
# ---------------------------------------------------------------------------


def _drain(recorder: TurnRecorder, chunks: list[str], on_done=None) -> list[str]:
    async def gen() -> AsyncGenerator[str, None]:
        for c in chunks:
            yield c

    async def run() -> list[str]:
        out = []
        async for c in recorder.stream(gen()):
            if on_done and '"done"' in c:
                on_done()
            out.append(c)
        return out

    return asyncio.run(run())


def test_stream_records_done_before_client_sees_it() -> None:
    repo = _FakeRepo()
    rec = TurnRecorder(_request(repo), {"sub": "u1"}, user_query="CPU 상위 10", has_upload=False)
    chunks = [
        _sse({"type": "token", "content": "답"}),
        _sse(
            {
                "type": "done",
                "response": "답",
                "thread_id": "t1",
                "query_id": "q1",
                "executed_sql": "SELECT 1",
                "row_count": 3,
                "processing_time_ms": 10.0,
                "db_scope": {"db_ids": ["polestar"]},
            }
        ),
    ]
    seen_at_done: list[int] = []
    out = _drain(rec, chunks, on_done=lambda: seen_at_done.append(len(repo.turns)))
    assert out == chunks, "기록기는 청크를 바꾸지 않고 그대로 흘려야 한다"
    assert seen_at_done == [1], "done을 받는 시점에 기록이 끝나 있어야 목록 재조회에 들어온다"
    owner, turn = repo.turns[0]
    assert owner == "user:u1"
    assert turn["thread_id"] == "t1" and turn["user_query"] == "CPU 상위 10"
    assert turn["status"] == "completed" and turn["row_count"] == 3
    assert turn["db_scope"] == {"db_ids": ["polestar"]}


def test_stream_status_follows_clarification_and_approval() -> None:
    repo = _FakeRepo()
    rec = TurnRecorder(_request(repo), {"sub": "u1"}, user_query="q", has_upload=False)
    _drain(
        rec,
        [
            _sse(
                {
                    "type": "done",
                    "response": "어느 존?",
                    "thread_id": "t",
                    "clarification": {"options": []},
                }
            )
        ],
    )
    _drain(
        rec,
        [_sse({"type": "done", "response": "승인?", "thread_id": "t", "awaiting_approval": True})],
    )
    assert [t["status"] for _, t in repo.turns] == ["clarification", "awaiting_approval"]


def test_failed_or_threadless_turns_are_not_recorded() -> None:
    repo = _FakeRepo()
    rec = TurnRecorder(_request(repo), {"sub": "u1"}, user_query="q", has_upload=True)
    _drain(rec, [_sse({"type": "error", "message": "시간 초과"})])
    # 파일 경로의 첫 턴 역질문은 thread_id가 없다 — 이어 붙일 대화가 없다
    _drain(rec, [_sse({"type": "done", "response": "어느 존?", "thread_id": None})])
    assert repo.turns == []


def test_record_failure_does_not_break_stream() -> None:
    rec = TurnRecorder(
        _request(_FakeRepo(fail=True)), {"sub": "u1"}, user_query="q", has_upload=False
    )
    done = _sse({"type": "done", "response": "답", "thread_id": "t1"})
    assert _drain(rec, [done]) == [done]


def test_no_store_or_no_owner_is_a_no_op() -> None:
    done = _sse({"type": "done", "response": "답", "thread_id": "t1"})
    assert _drain(
        TurnRecorder(_request(None), {"sub": "u1"}, user_query="q", has_upload=False), [done]
    ) == [done]
    repo = _FakeRepo()
    _drain(TurnRecorder(_request(repo), ANONYMOUS_USER, user_query="q", has_upload=False), [done])
    assert repo.turns == []


# ---------------------------------------------------------------------------
# 진입점 4곳 — 실제 라우트를 Mock 그래프로 통과시킨다
# ---------------------------------------------------------------------------


def _final_output(response: str = "김포 서버 3대입니다") -> dict:
    from langchain_core.messages import HumanMessage

    return {
        "final_response": response,
        "generated_sql": "SELECT 1",
        "query_results": [{"hostname": "a"}],
        "messages": [HumanMessage(content="q")],
    }


class _Graph:
    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return _final_output()

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        yield {"event": "on_chain_start", "name": "input_parser", "data": {}}
        yield {"event": "on_chain_end", "name": "LangGraph", "data": {"output": _final_output()}}


@pytest.fixture(scope="module")
def app_config():
    os.environ.setdefault("SCHEMA_CACHE_ENABLED", "false")
    from src.config import AppConfig, ServerConfig

    return AppConfig(
        db_backend="direct",
        db_connection_string="",
        log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )


def _query_client(repo: _FakeRepo, app_config) -> TestClient:
    from src.api.routes import query as query_routes

    app = FastAPI()
    app.state.config = app_config
    app.state.graph = _Graph()
    app.state.thread_repo = repo
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    return TestClient(app)


@pytest.mark.parametrize("route", ["/query", "/query/stream", "/query/file", "/query/file/stream"])
def test_every_entry_point_records_the_turn(route: str, app_config) -> None:
    repo = _FakeRepo()
    client = _query_client(repo, app_config)
    if "file" in route:
        r = client.post(
            "/api/v1" + route,
            data={"query": "양식 채워줘", "thread_id": "th-file"},
            files={"file": ("f.xlsx", io.BytesIO(b"PK\x03\x04dummy"), "application/octet-stream")},
        )
    else:
        r = client.post("/api/v1" + route, json={"query": "양식 채워줘", "thread_id": "th-text"})
    assert r.status_code == 200, r.text
    assert len(repo.turns) == 1, f"{route}: 완결된 턴이 기록되지 않았다"
    owner, turn = repo.turns[0]
    assert owner == "user:u1"
    assert turn["thread_id"] == ("th-file" if "file" in route else "th-text")
    assert turn["user_query"] == "양식 채워줘"
    assert turn["response"] == "김포 서버 3대입니다"
    assert turn["executed_sql"] == "SELECT 1"
    assert turn["has_upload"] is ("file" in route)


def test_entry_points_wrap_every_exit() -> None:
    """네 진입점의 모든 정상 종료가 기록기를 지난다 — 한쪽만 기록하는 비대칭 방지."""
    tree = ast.parse((ROOT / "src/api/routes/query.py").read_text(encoding="utf-8"))
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    for name in (
        "process_query",
        "process_query_stream",
        "process_file_query",
        "process_file_query_stream",
    ):
        fn = funcs[name]
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "QueryResponse"
            ):
                pytest.fail(f"{name}:{node.lineno} — QueryResponse를 turn.response() 없이 반환")
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "StreamingResponse":
                first = node.args[0]
                ok = (
                    isinstance(first, ast.Call)
                    and isinstance(first.func, ast.Attribute)
                    and first.func.attr == "stream"
                    and getattr(first.func.value, "id", None) == "turn"
                )
                assert ok, (
                    f"{name}:{node.lineno} — StreamingResponse가 turn.stream()으로 감싸지지 않았다"
                )


# ---------------------------------------------------------------------------
# 조회 API
# ---------------------------------------------------------------------------


def _threads_client(repo: Any, user: dict) -> TestClient:
    from src.api.routes import conversation

    app = FastAPI()
    app.state.thread_repo = repo
    app.include_router(conversation.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: user
    return TestClient(app)


def _seed(repo: _FakeRepo) -> None:
    for owner, tid, q in [
        (f"anon:{CID}", "t1", "첫 질문"),
        (f"anon:{CID}", "t1", "후속"),
        (f"anon:{CID}", "t2", "둘째 대화"),
        ("anon:someoneelse0000", "t9", "남의 질문"),
    ]:
        repo.turns.append((owner, {"thread_id": tid, "user_query": q, "response": "답"}))


def test_list_and_get_are_scoped_to_owner() -> None:
    repo = _FakeRepo()
    _seed(repo)
    client = _threads_client(repo, ANONYMOUS_USER)
    h = {"X-Client-Id": CID}
    body = client.get("/api/v1/threads", headers=h).json()
    assert body["available"] is True
    assert [t["thread_id"] for t in body["threads"]] == ["t2", "t1"]
    turns = client.get("/api/v1/threads/t1", headers=h).json()["turns"]
    assert [t["user_query"] for t in turns] == ["첫 질문", "후속"]
    # 남의 대화는 없는 대화와 같다
    assert client.get("/api/v1/threads/t9", headers=h).status_code == 404


def test_list_explains_why_it_is_empty() -> None:
    assert _threads_client(None, ANONYMOUS_USER).get("/api/v1/threads").json() == {
        "available": False,
        "reason": "no_store",
        "threads": [],
    }
    assert (
        _threads_client(_FakeRepo(), ANONYMOUS_USER).get("/api/v1/threads").json()["reason"]
        == "no_owner"
    )


def test_delete_only_touches_own_threads() -> None:
    repo = _FakeRepo()
    _seed(repo)
    client = _threads_client(repo, ANONYMOUS_USER)
    h = {"X-Client-Id": CID}
    assert client.delete("/api/v1/threads/t9", headers=h).json() == {"deleted": 0}
    assert client.delete("/api/v1/threads/t1", headers=h).json() == {"deleted": 2}
    assert client.delete("/api/v1/threads", headers=h).json() == {"deleted": 1}
    assert [o for o, _ in repo.turns] == ["anon:someoneelse0000"]


# ---------------------------------------------------------------------------
# 사용자 화면
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def index_html() -> str:
    return (ROOT / "src/static/index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return (ROOT / "src/static/js/app.js").read_text(encoding="utf-8")


@pytest.mark.parametrize("link_id", ["adminEntryLink", "noiseConsoleLink"])
def test_other_screens_open_in_new_tab(index_html: str, link_id: str) -> None:
    # 같은 탭으로 이동하면 이 페이지가 내려가 질의응답 화면이 사라진다
    tag = re.search(r'<a id="' + link_id + r'"[^>]*>', index_html).group(0)
    assert 'target="_blank"' in tag and 'rel="noopener"' in tag


def test_history_mode_buttons(index_html: str) -> None:
    assert 'data-mode="threads"' in index_html and 'data-mode="queries"' in index_html


def test_requests_carry_browser_id(app_js: str) -> None:
    body = app_js.split("function getAuthHeaders()", 1)[1].split("\n    }\n", 1)[0]
    assert 'headers["X-Client-Id"]' in body


def test_thread_id_updates_go_through_one_helper(app_js: str) -> None:
    # 턴이 끝날 때 목록을 갱신하려면 스레드 ID 갱신이 한 곳을 지나야 한다
    assert not re.search(r"currentThreadId = \w+\.thread_id", app_js)
    assert app_js.count("setCurrentThread(") >= 6  # 정의 1 + 완료 지점 5


def test_history_mode_state_is_declared_before_init(app_js: str) -> None:
    # setupViewTabs()가 이력 코드보다 먼저 돈다 — 상태가 그 뒤에 선언되면 undefined로 그린다
    assert app_js.index('var historyMode = "threads"') < app_js.index("\n    setupViewTabs();")


def test_new_chat_button_starts_fresh_thread(index_html: str, app_js: str) -> None:
    # 불러온 대화를 본 뒤 새 대화를 열 수 있어야 한다 — 다음 전송이 thread_id 없이 나가야 새 스레드다
    assert 'id="newChatBtn"' in index_html
    body = app_js.split("function startNewChat()", 1)[1].split("\n    }\n", 1)[0]
    assert "currentThreadId = null" in body
    assert 'querySelectorAll(".message")' in body
    assert 'newChatBtn.addEventListener("click", startNewChat)' in app_js
    # 패널을 접어도 보여야 한다 — 접히면 숨는 본문(historyPanelBody) 밖에 둔다
    assert index_html.index('id="newChatBtn"') < index_html.index('id="historyPanelBody"')
