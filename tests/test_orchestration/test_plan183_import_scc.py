"""plans/183 가드 — orchestration import 순환(SCC) 0 · `apm_jobs`→`apm_query` 역간선 0 · seam 보존.

측정 정의는 plans/166 §5.8 지표 5와 같다 — 함수 안 지연 import 포함 · `__init__.py` 노드 제외 ·
상대 import 해석 · `from pkg import name`은 `pkg.name`이 모듈이면 그 모듈로도 간선.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.orchestration import apm_jobs
from src.orchestration import apm_query as aq

REPO = Path(__file__).resolve().parents[2]
ORCH = REPO / "src/orchestration"


def _modname(path: Path) -> str:
    return ".".join(path.relative_to(REPO).with_suffix("").parts)


def _import_targets(tree: ast.AST, mod: str) -> set[str]:
    """`mod` 안 모든 import(지연 포함)가 가리킬 수 있는 모듈 이름 후보."""
    pkg = mod.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = pkg.split(".")[: len(pkg.split(".")) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            out.add(base)
            out.update(f"{base}.{a.name}" for a in node.names)
    return out


def _graph() -> dict[str, set[str]]:
    paths = {_modname(p): p for p in ORCH.rglob("*.py") if p.name != "__init__.py"}
    return {
        m: (_import_targets(ast.parse(p.read_text(encoding="utf-8")), m) & paths.keys()) - {m}
        for m, p in paths.items()
    }


def _sccs(g: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan — 크기 2 이상 강연결 요소."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on: set[str] = set()
    out: list[list[str]] = []

    def visit(v: str) -> None:
        index[v] = low[v] = len(index)
        stack.append(v)
        on.add(v)
        for w in g[v]:
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) >= 2:
                out.append(sorted(comp))

    for v in g:
        if v not in index:
            visit(v)
    return out


def test_orchestration_import_graph_has_no_cycle() -> None:
    g = _graph()
    # 측정 자체 점검 — 노드 이름이 실제 모듈 이름과 맞아야 공허하지 않다.
    assert {"src.orchestration.apm_query", "src.orchestration.apm_jobs"} <= g.keys()
    assert _sccs(g) == []


def test_apm_jobs_never_imports_apm_query() -> None:
    mod = "src.orchestration.apm_jobs"
    tree = ast.parse((ORCH / "apm_jobs.py").read_text(encoding="utf-8"))
    assert "src.orchestration.apm_query" not in _import_targets(tree, mod)


def test_session_factory_seam_reaches_apm_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(aq, "_SESSION_FACTORY", sentinel)
    assert apm_jobs.session_factory_provider() is sentinel
    monkeypatch.undo()
    assert apm_jobs.session_factory_provider() is None
