"""도구 계층의 DB-agnostic 원칙 검증 (Plan 67 Phase S1, D-088).

`src/tools`는 공용 계층이므로 특정 DB의 스키마 리터럴을 담아선 안 된다. 과적합 가드
스크립트(scripts/overfit_check.py)의 패턴을 그대로 적용해 신규 유입을 차단한다.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "overfit_check", _PROJECT_ROOT / "scripts" / "overfit_check.py"
)
overfit_check = importlib.util.module_from_spec(_SPEC)
# dataclass 타입 해석이 sys.modules 등록을 요구하므로 exec 전에 등록한다.
sys.modules["overfit_check"] = overfit_check
_SPEC.loader.exec_module(overfit_check)

_TOOLS_DIR = _PROJECT_ROOT / "src" / "tools"


@pytest.mark.parametrize("py_file", sorted(_TOOLS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_no_db_specific_literals(py_file):
    gated = set(overfit_check.GATED_CATEGORIES)
    hits = [h for h in overfit_check._scan_file(py_file) if h.category in gated]
    assert hits == [], f"{py_file.name}에 DB 특화 리터럴 유입: {hits}"


def test_scan_covers_the_package():
    """가드가 실제로 파일을 읽었는지(형해화 방지) — 대상 파일이 존재해야 한다."""
    assert len(list(_TOOLS_DIR.glob("*.py"))) >= 7
