"""plans/179 독립 검증(verifier) — 가드 전환(W1a)·순수 이동(W2) 뒤 남은 보호 공백을 메운다.

- 진입이 공유 사전 게이트가 **인가한 선택**을 받아 상태 조립에 쓴다(정적 + 네 진입 실행).
  종전 가드는 `selected_list, _selection_denied` 대입문 위치를 봤다 — W1a 전환 뒤에는 대입 대상을
  보지 않아, 파일 진입이 인가 결과를 버리고 요청 원문 선택으로 상태를 조립해도 통과했다.
- 선택 거부·역질문 조기 반환의 헤더(`X-Accel-Buffering`)·done 키 순서 · 파일 진입 저장 dict의
  업로드 원본·소유자·질문 원문 · 파일 스트림 done의 승인 대기·소스 전환 칸(plans/163 D6).
- W2로 옮긴 모듈에도 `query.py` 부정 단언(라우트 개별 flush · 문서 접두 · 델타 조립)을 적용한다.

실 LLM 0 · 네트워크 0 · DB 0.
"""

from __future__ import annotations

import ast
import asyncio
import io
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage

from src.api.dependencies import require_user
from src.llm import USER_RESPONSE_TAG

_ROOT = Path(__file__).resolve().parents[2]
_QUERY_PY = _ROOT / "src" / "api" / "routes" / "query.py"
_MOVED = sorted([*(_ROOT / "src" / "api" / "query").glob("*.py"),
                 *(_ROOT / "src" / "pipeline" / "turn").glob("*.py")])
_ENTRIES = ("process_query", "process_query_stream", "process_file_query",
            "process_file_query_stream")
ROWS = [{"hostname": "kpo-web-01", "cpu": 91.5}]
ZONE_DBS = "polestar_b0,polestar_cm_gp,polestar_cm_yd,polestar,itam"
ZONE_CLAR = {"kind": "zone_select", "question": "어느 존을 볼까요?",
             "options": [{"id": "bankjon", "label": "은행존"}], "original_query": "서버 목록"}


# ── 정적: 인가한 선택이 상태 조립까지 이어진다 ─────────────────────────────


def _func(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _calls(fn: ast.AST, callee: str) -> list[ast.Call]:
    return [n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee]


@pytest.mark.parametrize(("entry", "selection"), [
    ("process_query", "body.selected_db_ids"),
    ("process_query_stream", "body.selected_db_ids"),
    ("process_file_query", "selected_list"),
    ("process_file_query_stream", "selected_list"),
])
def test_entry_reassigns_authorized_selection(entry: str, selection: str) -> None:
    """`<선택>, gate = await _run_pre_gates(<선택>, …)` — 인가 결과를 같은 이름에 되쓴다.

    텍스트 진입은 게이트가 본문에 직접 적으므로 반환값을 버린다(`_, gate = …` · plans/184 K7).
    """
    fn = _func(ast.parse(_QUERY_PY.read_text(encoding="utf-8")), entry)
    assigns = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and getattr(n.value.value.func, "id", None) == "_run_pre_gates"
    ]
    assert len(assigns) == 1, f"{entry}: _run_pre_gates 대입문 {len(assigns)}개"
    (target,) = assigns[0].targets
    assert isinstance(target, ast.Tuple) and len(target.elts) == 2
    expected = "_" if selection.startswith("body.") else selection
    assert ast.unparse(target.elts[0]) == expected, ast.unparse(assigns[0])
    assert ast.unparse(target.elts[1]) == "gate"
    assert ast.unparse(assigns[0].value.value.args[0]) == selection
    if selection == "selected_list":
        # 파일 진입: 상태 조립·턴 문맥이 인가한 목록을 쓴다
        (init,) = _calls(fn, "create_initial_state")
        kw = {k.arg: ast.unparse(k.value) for k in init.keywords}
        assert kw["selected_db_ids"] == "selected_list"
        assert all(a.lineno > assigns[0].lineno for a in _calls(fn, "create_initial_state"))


def test_pre_gate_writes_authorized_selection_before_text_gates() -> None:
    """텍스트 진입: 게이트가 인가한 선택을 본문에 먼저 적고 나서 존·범위 게이트가 본문을 읽는다."""
    fn = _func(ast.parse(_QUERY_PY.read_text(encoding="utf-8")), "_run_pre_gates")
    writes = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
              and ast.unparse(n.targets[0]) == "body.selected_db_ids"]
    assert len(writes) == 1 and ast.unparse(writes[0].value) == "selected_db_ids"
    for gate in ("_zone_clarification_or_none", "_scope_select_or_none",
                 "_form_memory_delete_or_none"):
        (call,) = _calls(fn, gate)
        assert writes[0].lineno < call.lineno, gate


# ── 정적: W2로 옮긴 모듈에도 query.py 부정 단언을 건다 ─────────────────────


@pytest.mark.parametrize("needle", [
    "flush_if_failed",               # test_wiring_parity — 라우트 개별 트레이스 배선 금지
    "chat_prefix", "/문서",          # test_doc_qa — 질의 라우트 문서 접두 진입 제거(plans/127 W5)
    "build_db_scope(output,",        # test_db_scope_contract — 종료 노드 델타로 db_scope 금지
    "_dependency_notes_field(output)",  # test_plan121_stream_dependency_notes — 같은 이유
])
def test_moved_route_modules_keep_query_py_negative_guards(needle: str) -> None:
    assert _MOVED, "옮긴 모듈을 찾지 못했다 — 경로가 바뀌면 이 가드를 갱신할 것"
    names = {p.name for p in _MOVED}
    assert {"sse.py", "fields.py", "gates.py", "selection.py", "zone.py"} <= names, names
    offenders = [p.relative_to(_ROOT).as_posix() for p in _MOVED
                 if needle in p.read_text(encoding="utf-8")]
    assert not offenders, f"{needle!r}: {offenders}"


# ── 실행: 네 진입 ───────────────────────────────────────────────────────────


def _end(name: str, output: dict | None = None) -> dict:
    return {"event": "on_chain_end", "name": name, "data": {"output": output or {}},
            "parent_ids": ["r"]}


def _start(name: str) -> dict:
    return {"event": "on_chain_start", "name": name, "data": {}, "parent_ids": ["r"]}


def _token(text: str) -> dict:
    return {"event": "on_chat_model_stream", "name": "llm", "tags": [USER_RESPONSE_TAG],
            "metadata": {"langgraph_node": "output_generator"},
            "data": {"chunk": type("C", (), {"content": text})()},
            "parent_ids": ["r", "n", "m"]}


def _final_output(response: str = "최종 응답", **extra: Any) -> dict:
    return {"final_response": response, "query_results": list(ROWS),
            "generated_sql": "SELECT 1", "messages": [HumanMessage(content="서버 목록")],
            **extra}


class _InvokeOnly:
    """`astream_events`가 없는 대역 — 스트림 진입은 ainvoke 폴백을 탄다."""

    def __init__(self, *, result: dict | None = None, events: list[dict] | None = None) -> None:
        self.result = result if result is not None else _final_output()
        self.events = events or []
        self.inputs: list[dict] = []
        self.consumed: list[int] = []

    def get_state(self, config: dict) -> Any:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        self.inputs.append(dict(input_state))
        return dict(self.result)


class _Streaming(_InvokeOnly):
    """`slow_after` 이후 이벤트는 잠시 늦게 낸다 — 생산자 태스크가 미리 당겨 읽으므로, drain
    하지 않는 진입은 그 사이 생산자가 취소돼 뒤 이벤트를 받지 않는다(drain 판독용)."""

    slow_after: int | None = None

    async def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        self.inputs.append(dict(input_state))
        for i, ev in enumerate(self.events):
            if self.slow_after is not None and i > self.slow_after:
                await asyncio.sleep(0.3)
            self.consumed.append(i)
            yield ev


def _normal_events(**final_extra: Any) -> list[dict]:
    return [_start("query_executor"), _end("query_executor", {"query_results": list(ROWS)}),
            _start("output_generator"), _token("답"),
            {"event": "on_chain_end", "name": "LangGraph",
             "data": {"output": _final_output("답", **final_extra)}}]


@pytest.fixture(scope="module")
def _base_config():
    from src.config import AppConfig, ServerConfig

    return AppConfig(db_backend="direct", db_connection_string="", log_level="WARNING",
                     server=ServerConfig(query_timeout=60, file_query_timeout=60))


@pytest.fixture
def cfg(_base_config):
    """검증 대상 필드를 매 테스트 명시한다 — `.env` 누수 차단."""
    c = _base_config
    s = c.server
    s.query_timeout = 60
    s.file_query_timeout = 60
    s.sse_progress_events = True
    s.sse_heartbeat_interval_sec = 0
    s.answer_reserve_sec = 15
    s.stream_idle_timeout_sec = 30
    s.stream_delivery_grace_sec = 60
    c.result_viz.chart_enabled = False
    c.result_viz.explain_enabled = False
    c.schema_cache.enabled = False
    c.multi_db.active_db_ids_csv = ZONE_DBS
    c.multi_db.zone_group_exclusive = True
    c.composite.scope_select_enabled = True
    c.query.intent_llm_assist = False
    c.enable_deepagents_package = True
    c.enable_intent_orchestration = True
    c.enable_semantic_routing = True
    c.enable_sql_approval = False
    return c


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from src.api.routes import query as q
    from src.observability import ladder

    monkeypatch.setattr(ladder, "_resolution", None)
    q._results_store.clear()
    yield
    q._results_store.clear()


_USER = {"sub": "u1", "role": "user"}
_LIMITED = {"sub": "u1", "role": "user", "allowed_db_ids": ["polestar_cm_gp"]}


def _client(graph: Any, cfg: Any, user: dict | None = None) -> TestClient:
    from src.api.routes import query as q

    app = FastAPI()
    app.state.config = cfg
    app.state.graph = graph
    app.include_router(q.router, prefix="/api/v1")
    u = dict(user or _USER)
    app.dependency_overrides[require_user] = lambda: u
    return TestClient(app)


def _post(client: TestClient, entry: str, *, query: str = "서버 목록",
          selected: list[str] | None = None, upload: bytes = b"PK\x03\x04dummy") -> Any:
    files = {"file": ("양식.xlsx", io.BytesIO(upload), "application/octet-stream")}
    if entry.startswith("text"):
        body: dict[str, Any] = {"query": query}
        if selected is not None:
            body["selected_db_ids"] = selected
        path = "/api/v1/query/stream" if entry == "text_stream" else "/api/v1/query"
        return client.post(path, json=body)
    form = {"query": query}
    if selected is not None:
        form["selected_db_ids"] = ",".join(selected)
    path = "/api/v1/query/file/stream" if entry == "file_stream" else "/api/v1/query/file"
    return client.post(path, data=form, files=files)


def _last_event(r: Any) -> dict:
    events = [json.loads(x[6:]) for x in r.text.splitlines() if x.startswith("data: ")]
    assert events, r.text[:200]
    return events[-1]


def _payload(r: Any, entry: str) -> dict:
    return _last_event(r) if entry.endswith("stream") else r.json()


_FOUR = ("text_post", "text_stream", "file_post", "file_stream")


@pytest.mark.parametrize("entry", _FOUR)
def test_partially_denied_selection_reaches_state_authorized_only(entry, cfg) -> None:
    """은행존+공동존(김포) 선택 중 김포만 인가 → 상태에는 김포만 · 존 그룹 배타 역질문 없음."""
    graph = _InvokeOnly()
    r = _post(_client(graph, cfg, _LIMITED), entry, selected=["polestar_b0", "polestar_cm_gp"])
    assert r.status_code == 200, r.text[:300]
    payload = _payload(r, entry)
    assert payload.get("clarification") is None, payload.get("clarification")
    assert len(graph.inputs) == 1, "그래프가 돌지 않았다"
    assert graph.inputs[0]["selected_db_ids"] == ["polestar_cm_gp"]


@pytest.mark.parametrize("entry", _FOUR)
def test_fully_denied_selection_stops_with_reason(entry, cfg) -> None:
    from src.routing.db_authz import SELECTION_DENIED_MESSAGE

    graph = _InvokeOnly()
    r = _post(_client(graph, cfg, _LIMITED), entry, selected=["polestar_b0"])
    assert r.status_code == 200
    assert _payload(r, entry)["response"] == SELECTION_DENIED_MESSAGE
    assert graph.inputs == []
    if entry.endswith("stream"):
        # 텍스트 스트림 선택 거부만 X-Accel-Buffering이 없다(현행 바이트 보존 · plans/168 A1)
        assert ("x-accel-buffering" in r.headers) is (entry == "file_stream")
        done = _last_event(r)
        assert list(done)[:4] == ["type", "response", "query_id", "thread_id"]


@pytest.mark.parametrize("entry", ("text_stream", "file_stream"))
def test_pre_gate_clarification_done_shape(entry, cfg, monkeypatch) -> None:
    from src.api.routes import query as q

    monkeypatch.setattr(q, "_zone_clarification_or_none", lambda *a, **k: dict(ZONE_CLAR))
    monkeypatch.setattr(q, "_file_zone_clarification_or_none", lambda *a, **k: dict(ZONE_CLAR))
    r = _post(_client(_InvokeOnly(), cfg), entry)
    assert r.headers.get("x-accel-buffering") == "no"
    done = _last_event(r)
    assert list(done)[:5] == ["type", "response", "query_id", "thread_id", "clarification"]


@pytest.mark.parametrize("entry", _FOUR)
@pytest.mark.parametrize("streaming", [True, False], ids=["astream", "invoke"])
def test_store_carries_owner_question_and_upload(entry, streaming, cfg) -> None:
    """저장 dict — 소유자(`owner_sub`) · 질문 원문 · 파일 진입은 업로드 원본(§14)."""
    from src.api.routes import query as q

    graph = _Streaming(events=_normal_events()) if streaming else _InvokeOnly()
    upload = b"PK\x03\x04upload-original"
    r = _post(_client(graph, cfg), entry, query="김포 서버 목록", upload=upload,
              selected=["polestar_cm_gp"])
    assert r.status_code == 200, r.text[:300]
    assert len(graph.inputs) == 1, "사전 게이트에서 멈췄다 — 본 경로 저장을 보지 못한다"
    qid = _payload(r, entry)["query_id"]
    stored = q._results_store[qid]
    assert stored["owner_sub"] == "u1"
    assert stored["user_query"] == "김포 서버 목록"
    if entry.startswith("file"):
        assert stored["uploaded_file"] == upload
        assert stored["uploaded_file_name"] == "양식.xlsx"
    else:
        assert "uploaded_file" not in stored
    # /query만 clarification 키를 늘 싣는다(값 없음) · /query/file은 값이 있을 때만
    if entry == "text_post":
        assert "clarification" in stored and stored["clarification"] is None
    if entry == "file_post":
        assert "clarification" not in stored


@pytest.mark.parametrize("streaming", [True, False], ids=["astream", "fallback"])
def test_file_stream_done_never_carries_text_only_fields(streaming, cfg) -> None:
    """파일 스트림 done: 승인 대기는 항상 False · 소스 전환은 null(plans/163 D6) — 출력에 있어도."""
    extra = {"awaiting_approval": True, "source_switch": {"to": "apm"},
             "apm_source_pending": {"query": "q"}}
    graph = (_Streaming(events=[*_normal_events(**extra), _end("after1"), _end("after2")])
             if streaming else _InvokeOnly(result=_final_output("답", **extra)))
    graph.slow_after = 4
    r = _post(_client(graph, cfg), "file_stream", selected=["polestar_cm_gp"])
    assert len(graph.inputs) == 1
    done = _last_event(r)
    assert done["type"] == "done"
    assert done["awaiting_approval"] is False
    assert done["source_switch"] is None
    if streaming:
        # 제니퍼 소스 대기 drain은 텍스트 진입만 — 파일 스트림은 done 뒤 이벤트를 받지 않는다
        assert graph.consumed == [0, 1, 2, 3, 4]


def test_text_stream_drains_after_apm_pending(cfg) -> None:
    """대조군 — 텍스트 스트림은 같은 출력이면 그래프 끝까지 받는다(plans/147)."""
    graph = _Streaming(events=[*_normal_events(apm_source_pending={"query": "q"}),
                               _end("after1"), _end("after2")])
    graph.slow_after = 4
    _post(_client(graph, cfg), "text_stream", selected=["polestar_cm_gp"])
    assert graph.consumed == [0, 1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("entry", ("text_post", "file_post"))
def test_non_stream_pre_gate_status(entry, cfg, monkeypatch) -> None:
    from src.api.routes import query as q

    monkeypatch.setattr(q, "_zone_clarification_or_none", lambda *a, **k: dict(ZONE_CLAR))
    monkeypatch.setattr(q, "_file_zone_clarification_or_none", lambda *a, **k: dict(ZONE_CLAR))
    body = _post(_client(_InvokeOnly(), cfg), entry).json()
    assert body["status"] == "clarification"
    assert body["clarification"]["kind"] == "zone_select"


def test_shim_reexports_are_the_moved_objects() -> None:
    """재노출은 같은 객체다 — 결과 저장소·patch seam이 한 벌이어야 한다."""
    import src.api.query.gates as gates
    import src.api.query.store as store
    import src.api.query.turn_input as turn_input
    import src.pipeline.turn.selection as selection
    from src.api.routes import query as q

    assert q._results_store is store._results_store
    assert q._store_result is store._store_result
    assert q._file_zone_clarification_or_none is gates._file_zone_clarification_or_none
    assert q._zone_clarification_or_none is gates._zone_clarification_or_none
    assert q.apply_selection_authorization is selection.apply_selection_authorization
    assert q._get_checkpoint_state is turn_input._get_checkpoint_state


def test_patch_seams_are_only_called_from_query_py() -> None:
    """테스트가 query 모듈에서 바꿔 끼우는 seam은 query.py 안에서만 불린다(옮긴 모듈에서 직접
    부르면 patch가 그 호출 지점에 닿지 않는다 — D-337 seam)."""
    seams = {"_zone_clarification_or_none", "_file_zone_clarification_or_none",
             "_audit_user_request", "apply_selection_authorization", "_get_checkpoint_state"}
    offenders = []
    for p in _MOVED:
        for fn in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call) and getattr(n.func, "id", None) in seams:
                        offenders.append(f"{p.name}:{fn.name}→{n.func.id}")
    assert not offenders, offenders
    tree = ast.parse(_QUERY_PY.read_text(encoding="utf-8"))
    callers = {fn.name for fn in tree.body
               if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
               and any(isinstance(n, ast.Call) and getattr(n.func, "id", None) in seams
                       for n in ast.walk(fn))}
    assert {"_run_pre_gates", "_partial_on_timeout", *_ENTRIES} <= callers, callers



@pytest.mark.parametrize("entry", ("file_post", "file_stream"))
def test_file_entry_passes_question_text_to_zone_gate(entry, cfg) -> None:
    """파일 진입도 질의 원문을 존 게이트에 넘긴다 — 위치어(「김포」)가 있으면 역질문 없이 진행."""
    graph = _InvokeOnly()
    r = _post(_client(graph, cfg), entry, query="김포 서버 목록")
    assert _payload(r, entry).get("clarification") is None
    assert len(graph.inputs) == 1
    # 대조군 — 위치어가 없으면 존 역질문에서 멈춘다(게이트가 실제로 켜져 있다)
    graph2 = _InvokeOnly()
    r2 = _post(_client(graph2, cfg), entry, query="서버 목록")
    assert (_payload(r2, entry).get("clarification") or {}).get("kind") == "zone_select"
    assert graph2.inputs == []
