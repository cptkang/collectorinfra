"""plans/121 TP-11.8 — 스트림 `done`·astream `response_data`에도 순차 처리 경과 노트(4경로 대칭).

종전에는 비스트림 두 경로와 스트림 폴백(`ainvoke`)의 응답 조립에만 `dependency_notes`가 있어,
측정 정본인 스트림 하네스가 게이트·브리지 경과를 구조로 받지 못했다(D-205·D-066 비대칭).

고정하는 계약:
① 그래프 경로 SSE done 4곳(텍스트 astream·폴백 · 파일 astream·폴백)이 같은 필드를 싣는다.
② astream `response_data` 2곳은 종료 노드 델타가 아니라 누적 상태(`_scope_state`)에서 싣는다.
③ 노트가 없으면 키를 싣지 않는다 — done 이벤트 바이트 불변.
LLM·DB 0.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from src.api.routes.query import _dependency_notes_field

_QUERY_PY = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"


@pytest.fixture(scope="module")
def query_py() -> str:
    return _QUERY_PY.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tree(query_py) -> ast.Module:
    return ast.parse(query_py)


def _func(tree: ast.Module, name: str) -> ast.AST:
    return next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _calls(tree: ast.Module, caller: str, callee: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(_func(tree, caller))
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
    ]


def test_done_events_all_carry_notes_field(tree):
    """done 4경로(텍스트·파일 × astream·폴백) = 두 스트림 진입 → `_stream_graph_turn` →
    done 빌더 2곳.

    종전 소스 계수(done 리터럴 4벌)의 전환(plans/179 W1a) — done 페이로드는 `_turn_done_payload`
    한 벌이고, 노트 칸은 db_scope 칸 바로 뒤에 펼친다.
    """
    for entry in ("process_query_stream", "process_file_query_stream"):
        assert len(_calls(tree, entry, "_stream_graph_turn")) == 1, entry
    assert len(_calls(tree, "_stream_graph_turn", "_turn_done_payload")) == 2
    (ret,) = [n for n in ast.walk(_func(tree, "_turn_done_payload")) if isinstance(n, ast.Return)]
    done = ret.value
    assert isinstance(done, ast.Dict)
    entries = [(k and ast.unparse(k), ast.unparse(v)) for k, v in zip(done.keys, done.values,
                                                                       strict=True)]
    at = entries.index(("'db_scope'", "response_data.get('db_scope')"))
    assert entries[at + 1] == (None, "_dependency_notes_field(response_data)")


def test_astream_response_data_uses_accumulated_state(tree, query_py):
    """astream 응답 조립은 누적 상태(`_scope_state`)에서 노트를 싣는다(있을 때만 · TP-11.8)."""
    (astream,) = _calls(tree, "_stream_graph_turn", "_build_stream_turn_response")
    assert ast.unparse(astream.args[3]) == "_scope_state"
    (data_call,) = _calls(tree, "_build_stream_turn_response", "_turn_response_data")
    assert ast.unparse(data_call.args[2]) == "scope_state"
    assert {kw.arg: ast.unparse(kw.value) for kw in data_call.keywords}["mode"] == "'astream'"
    assert "_dependency_notes_field(state) if mode == 'astream'" in ast.unparse(
        _func(tree, "_turn_response_data")
    )
    assert "_dependency_notes_field(output)" not in query_py


@pytest.mark.parametrize("state", [{}, {"dependency_notes": None}, {"dependency_notes": []}])
def test_absent_notes_add_no_key(state):
    assert _dependency_notes_field(state) == {}
    base = {"type": "done", "response": "r"}
    assert json.dumps({**base, **_dependency_notes_field(state)}) == json.dumps(base)


def test_present_notes_are_carried_as_is():
    notes = [{"kind": "gate", "task_id": "t2", "detail": "선행 0건"}]
    assert _dependency_notes_field({"dependency_notes": notes}) == {"dependency_notes": notes}
