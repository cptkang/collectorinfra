"""경계 불변식 테스트 (D-118) — sre_agent ↔ collectorinfra src 양방향 import 0.

- 정방향: sre_agent 패키지·scripts가 `src.*` / `collectorinfra.*`를 import하지 않음.
- 역방향: (co-located 시) collectorinfra `src/`가 `sre_agent`를 import하지 않음.
"""

import ast
from pathlib import Path

import pytest

_TOP = Path(__file__).resolve().parents[1]          # collectorinfra/sre_agent/ (최상위)
_PACKAGE = _TOP / "sre_agent"                        # 파이썬 패키지
_SCRIPTS = _TOP / "scripts"
_COLLECTORINFRA_SRC = _TOP.parent / "src"            # collectorinfra/src (co-located 시)

# sre_agent 안에서 금지되는 최상위 모듈 (collectorinfra 본체)
_FORBIDDEN_TOP = frozenset({"src", "collectorinfra"})


def _absolute_imported_tops(py_file: Path) -> set[str]:
    """파일에서 절대 import된 최상위 모듈명 집합을 반환한다(상대 import 제외)."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    tops: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                tops.add(node.module.split(".", 1)[0])
    return tops


def test_sre_agent_does_not_import_collectorinfra():
    offenders: list[str] = []
    for py_file in list(_PACKAGE.rglob("*.py")) + list(_SCRIPTS.rglob("*.py")):
        for top in _absolute_imported_tops(py_file):
            if top in _FORBIDDEN_TOP:
                offenders.append(f"{py_file.relative_to(_TOP)}: import {top}")
    assert offenders == [], f"sre_agent가 collectorinfra 모듈을 import함: {offenders}"


def test_collectorinfra_src_does_not_import_sre_agent():
    if not _COLLECTORINFRA_SRC.is_dir():
        pytest.skip("collectorinfra src 부재 — 분리 완료 상태(역방향 경계 대상 없음)")
    offenders: list[str] = []
    for py_file in _COLLECTORINFRA_SRC.rglob("*.py"):
        try:
            tops = _absolute_imported_tops(py_file)
        except (SyntaxError, UnicodeDecodeError):
            continue
        if "sre_agent" in tops:
            offenders.append(str(py_file.relative_to(_COLLECTORINFRA_SRC)))
    assert offenders == [], f"collectorinfra src가 sre_agent를 import함: {offenders}"
