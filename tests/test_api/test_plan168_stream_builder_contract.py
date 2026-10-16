"""plans/168 A1 — `_stream_graph_turn` 빌더 호출 계약 고정(verifier Minor).

`stream_done(...)`은 astream 안쪽 `except (AttributeError, TypeError, ...)` 안에서 불린다 —
빌더의 키워드가 호출과 어긋나면 TypeError가 조용히 ainvoke 폴백으로 간다. 두 스트림 진입의
빌더 클로저(`_stream_done`·`_fallback_done`) 시그니처가 공통 함수의 호출과 같은지 정적으로 본다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_QUERY_PY = Path(__file__).resolve().parents[2] / "src" / "api" / "routes" / "query.py"


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(_QUERY_PY.read_text(encoding="utf-8"))


def _func(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    return next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == name
    )


def _call_shape(fn: ast.AST, callee: str) -> tuple[int, set[str]]:
    calls = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == callee
    ]
    assert len(calls) == 1, f"{callee} 호출 {len(calls)}개"
    return len(calls[0].args), {kw.arg for kw in calls[0].keywords if kw.arg}


@pytest.mark.parametrize("handler", ["process_query_stream", "process_file_query_stream"])
@pytest.mark.parametrize(
    ("callee", "builder"), [("stream_done", "_stream_done"), ("fallback_done", "_fallback_done")]
)
def test_builder_signature_matches_call(tree, handler, callee, builder):
    n_pos, keywords = _call_shape(_func(tree, "_stream_graph_turn"), callee)
    defs = [
        n for n in ast.walk(_func(tree, handler))
        if isinstance(n, ast.FunctionDef) and n.name == builder
    ]
    assert len(defs) == 1, f"{handler}.{builder} 정의 {len(defs)}개"
    args = defs[0].args
    assert len(args.posonlyargs) + len(args.args) == n_pos
    assert {a.arg for a in args.kwonlyargs} == keywords
    assert args.vararg is None and args.kwarg is None
    assert all(d is None for d in args.kw_defaults), "기본값이 있으면 누락 키워드를 못 잡는다"
