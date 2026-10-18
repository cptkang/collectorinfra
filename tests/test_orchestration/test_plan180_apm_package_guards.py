"""plans/180 W1 — `src/orchestration/apm/` 순수 모듈 가드(LLM 0회 · 소스·AST 정적 검사).

`apm_query.py`에서 순수 이동한 코드가 원 파일에 걸려 있던 가드 밖으로 나가지 않게 덮는다.

1. 유사어 누수 0 — 각 모듈이 `src.schema_cache`를 import하지 않고 `synonym` 문자열이 없다
   (`test_plan132_w6_source_synonyms.py::test_non_db_handlers_do_not_import_db_synonym_dictionaries`와
   같은 단언 · N-12d)
2. 호출 구성 표(`_CALL_BUILDERS`) 키 = 레지스트리 보기 대상 어휘(`VIEW_TARGETS` — plans/172 §5 후속)
3. 순환 금지 — `apm/` 모듈이 Facade(`src.orchestration.apm_query`)를 import하지 않는다
   (지연 import 포함)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.orchestration.apm.builders import _CALL_BUILDERS
from src.routing.registry import VIEW_TARGETS

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "src/orchestration/apm"
MODULES = sorted(p.relative_to(REPO).as_posix() for p in PACKAGE.rglob("*.py"))
FACADE = "src.orchestration.apm_query"


def _imported_modules(tree: ast.AST, module: str) -> set[str]:
    """모든 import(함수 안 지연 import 포함)의 대상 모듈 — `from a import b`는 `a`와 `a.b`.

    상대 import(`from .. import x`)는 모듈 파일 경로 기준 절대 이름으로 풀고,
    `importlib.import_module("…")`·`__import__("…")`의 문자열 인자도 넣는다.
    """
    package = Path(module).parent.as_posix().split("/")
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
            name = ".".join(p for p in (base, node.module or "") if p)
            out.add(name)
            out |= {f"{name}.{alias.name}" for alias in node.names}
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            callee = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            arg = node.args[0]
            if callee in ("import_module", "__import__") and isinstance(arg, ast.Constant) \
                    and isinstance(arg.value, str):
                out.add(arg.value)
    return out


def test_package_has_modules() -> None:
    assert len(MODULES) > 1, MODULES


@pytest.mark.parametrize("module", MODULES)
def test_apm_modules_do_not_import_db_synonym_dictionaries(module: str) -> None:
    text = (REPO / module).read_text(encoding="utf-8")
    imported = _imported_modules(ast.parse(text), module)
    assert not {m for m in imported if m.startswith("src.schema_cache")}
    assert "synonym" not in text.lower(), "소스 밖 유사어 사전 소비 0(N-12d)"


def test_call_builders_cover_view_targets() -> None:
    assert set(_CALL_BUILDERS) == set(VIEW_TARGETS)


@pytest.mark.parametrize("module", MODULES)
def test_apm_modules_do_not_import_facade(module: str) -> None:
    tree = ast.parse((REPO / module).read_text(encoding="utf-8"))
    imported = _imported_modules(tree, module)
    assert FACADE not in imported, f"{module} → {FACADE}(순환)"
