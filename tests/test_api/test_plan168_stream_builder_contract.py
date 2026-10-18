"""plans/168 A1 — `_stream_graph_turn` 빌더 호출 계약 고정(verifier Minor).

astream 최종 출력의 응답·done 조립은 astream 안쪽 `except (AttributeError, TypeError, ...)` 안에서
불린다 — 빌더의 키워드가 호출과 어긋나면 TypeError가 조용히 ainvoke 폴백으로 간다.

plans/179 W1a: 진입별 빌더 클로저(`_stream_done`·`_fallback_done` × 두 스트림 진입)가 모듈 공유
빌더(`_build_stream_turn_response`·`_build_turn_response`·`_turn_done_payload`)로 합쳐졌다. 공통
함수 안의 빌더 호출과 두 스트림 진입의 `_stream_graph_turn` 호출이 각 정의의 시그니처와 같은지
정적으로 본다(호출 인자 수·키워드 집합 · 기본값·`*args`·`**kw` 없음).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_QUERY_PY = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(_QUERY_PY.read_text(encoding="utf-8"))


def _func(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    return next(
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )


def _calls(fn: ast.AST, callee: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
    ]


def _assert_call_matches_def(call: ast.Call, fn: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
    args = fn.args
    assert args.vararg is None and args.kwarg is None
    assert not args.defaults and all(d is None for d in args.kw_defaults), (
        "기본값이 있으면 누락 인자를 못 잡는다"
    )
    assert not any(isinstance(a, ast.Starred) for a in call.args)
    assert all(kw.arg for kw in call.keywords), "**kw 펼침 호출은 계약을 못 본다"
    assert len(call.args) == len(args.posonlyargs) + len(args.args), ast.unparse(call)
    assert {kw.arg for kw in call.keywords} == {a.arg for a in args.kwonlyargs}, ast.unparse(call)


@pytest.mark.parametrize(
    ("callee", "n_calls"),
    [("_build_stream_turn_response", 1), ("_build_turn_response", 1), ("_turn_done_payload", 2)],
)
def test_builder_signature_matches_call(tree, callee, n_calls):
    """공통 스트림 본문의 빌더 호출(astream·폴백)이 공유 빌더 정의와 맞는다."""
    calls = _calls(_func(tree, "_stream_graph_turn"), callee)
    assert len(calls) == n_calls, f"{callee} 호출 {len(calls)}개"
    for call in calls:
        _assert_call_matches_def(call, _func(tree, callee))


@pytest.mark.parametrize("handler", ["process_query_stream", "process_file_query_stream"])
def test_stream_entry_call_matches_common_body(tree, handler):
    """두 스트림 진입이 공통 본문을 정의와 같은 인자로 부르고, 진입 정보는 `ctx`로 넘긴다."""
    (call,) = _calls(_func(tree, handler), "_stream_graph_turn")
    _assert_call_matches_def(call, _func(tree, "_stream_graph_turn"))
    assert ast.unparse({kw.arg: kw.value for kw in call.keywords}["ctx"]) == "ctx"
    assert len(_calls(_func(tree, handler), "TurnContext")) == 1


def test_fallback_and_json_modes(tree):
    """폴백은 `mode='fallback'`, 비스트림 두 진입은 `mode='json'`으로 같은 빌더를 부른다."""
    for caller, mode in (
        ("_stream_graph_turn", "'fallback'"),
        ("process_query", "'json'"),
        ("process_file_query", "'json'"),
    ):
        (call,) = _calls(_func(tree, caller), "_build_turn_response")
        _assert_call_matches_def(call, _func(tree, "_build_turn_response"))
        assert ast.unparse({kw.arg: kw.value for kw in call.keywords}["mode"]) == mode, caller
