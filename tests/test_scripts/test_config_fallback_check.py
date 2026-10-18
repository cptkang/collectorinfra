"""설정 getattr 폴백 린트 규칙 단언 (D-331 ② · plans/166 W1)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "config_fallback_check.py"


@pytest.fixture(scope="module")
def lint():
    spec = importlib.util.spec_from_file_location("config_fallback_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["config_fallback_check"] = module
    spec.loader.exec_module(module)
    return module


def _scan(lint, tmp_path: Path, rel: str, body: str):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return lint.check(tmp_path)


def test_fallback_differs_from_field_default_is_violation(lint, tmp_path):
    report = _scan(lint, tmp_path, "src/a.py",
                   'x = getattr(cfg.multi_db, "zone_group_exclusive", True)\n')
    assert [(f.field, f.fallback, f.default) for f in report.violations] == [
        ("zone_group_exclusive", "True", "False"),
    ]


def test_fallback_equal_to_field_default_passes(lint, tmp_path):
    report = _scan(lint, tmp_path, "noise_gate/a.py",
                   'x = getattr(cfg.multi_db, "zone_group_exclusive", False)\n')
    assert report.violations == []
    assert report.getattr_calls == 1


@pytest.mark.parametrize(("body", "reason"), [
    ('x = getattr(cfg, "zone_group_exclusive", None)\n', "None 폴백"),
    ('x = getattr(cfg, "zone_group_exclusive", DEFAULT)\n', "리터럴이 아닌 폴백"),
])
def test_undecidable_fallback_is_skipped_and_counted(lint, tmp_path, body, reason):
    report = _scan(lint, tmp_path, "src/a.py", body)
    assert report.violations == []
    assert report.skipped == {reason: 1}


@pytest.mark.parametrize("rel", ["src/tests/a.py", "noise_gate/scripts/a.py", "src/config.py"])
def test_excluded_paths_are_not_scanned(lint, tmp_path, rel):
    report = _scan(lint, tmp_path, rel, 'x = getattr(cfg, "zone_group_exclusive", True)\n')
    assert report.violations == []


def test_non_config_name_is_ignored(lint, tmp_path):
    report = _scan(lint, tmp_path, "src/a.py", 'x = getattr(obj, "no_such_config_field_xyz", 1)\n')
    assert report.getattr_calls == 0 and report.violations == []


@pytest.mark.parametrize(("a", "b", "same"), [
    (0, 0.0, True), (True, 1, False), (False, 0, False),
    ([], (), True), ("a", "a", True), (15, 0, False),
])
def test_value_equivalence(lint, a, b, same):
    assert lint._same(a, b) is same


def test_allowlisted_site_is_skipped_not_violation(lint, tmp_path):
    report = _scan(lint, tmp_path, "src/nodes/prompt_blocks.py",
                   'x = getattr(t2s, "prompt_token_budget", 0)\n')
    assert report.violations == []
    assert report.skipped == {"허용 목록": 1}


def test_repository_has_no_violation(lint):
    """D-331 ② — X1 치환 뒤 저장소 위반 0(재유입 차단)."""
    assert lint.check().violations == []
