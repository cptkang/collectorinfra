"""경계 불변식 — 양방향 import 0.

plans/148 §3.8 · D-274 원칙 · `apm_gateway/tests/test_boundary.py` 전례.

- `fabrix_proxy/fabrix_proxy`·`fabrix_proxy/scripts`(있으면)·`fabrix_proxy/tests`는
  `src`·`noise_gate`·`sre_agent`·`mcp_server`·`apm_gateway`를 import하지 않는다
  (AST — 이식은 복사로만).
- 역방향으로 그 패키지들이 `fabrix_proxy`를 import하지 않는다.
- 엔트리는 `__main__` 하나다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PROXY = Path(__file__).resolve().parents[1]  # fabrix_proxy/
PACKAGE = PROXY / "fabrix_proxy"
REPO = PROXY.parent

_FORBIDDEN_FROM_PROXY = frozenset(
    {"src", "noise_gate", "sre_agent", "mcp_server", "apm_gateway", "collectorinfra"}
)
_OTHER_PACKAGES = {
    "src": REPO / "src",
    "noise_gate": REPO / "noise_gate",
    "sre_agent": REPO / "sre_agent" / "sre_agent",
    "mcp_server": REPO / "mcp_server" / "mcp_server",
    "apm_gateway": REPO / "apm_gateway" / "apm_gateway",
}


def _imports(py_file: Path) -> list[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.append(node.module)
    return names


def test_proxy_does_not_import_other_packages() -> None:
    offenders = []
    for sub in ("fabrix_proxy", "scripts", "tests"):
        for py_file in sorted((PROXY / sub).rglob("*.py")):
            for name in _imports(py_file):
                if name.split(".", 1)[0] in _FORBIDDEN_FROM_PROXY:
                    offenders.append(f"{py_file.relative_to(PROXY)}: {name}")
    assert offenders == []


@pytest.mark.parametrize("package", sorted(_OTHER_PACKAGES))
def test_other_packages_do_not_import_proxy(package: str) -> None:
    root = _OTHER_PACKAGES[package]
    if not root.is_dir():
        pytest.skip(f"{package} 부재")
    offenders = []
    for py_file in root.rglob("*.py"):
        try:
            names = _imports(py_file)
        except (SyntaxError, UnicodeDecodeError):
            continue
        if any(n.split(".", 1)[0] == "fabrix_proxy" for n in names):
            offenders.append(str(py_file.relative_to(REPO)))
    assert offenders == []


def test_single_entry_point() -> None:
    entries = [
        p.relative_to(PACKAGE).as_posix()
        for p in sorted(PACKAGE.rglob("*.py"))
        if '__name__ == "__main__"' in p.read_text(encoding="utf-8")
    ]
    assert entries == ["__main__.py"]
