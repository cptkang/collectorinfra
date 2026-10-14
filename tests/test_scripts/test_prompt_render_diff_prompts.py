"""plans/156 W5 — `prompt_render_diff.py --prompts <base_sha>` (src/prompts 모듈 상수 base 대조).

임시 git 저장소에 `src/prompts/*.py`를 커밋하고 작업 트리를 바꿔 일치·변경·신설·삭제 표를 확인한다.
소스는 AST로만 읽는다(실행 0). 실 LLM·DB 0.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import prompt_render_diff as prd

BASE_A = '''
_HEAD = "머리 "
KEEP = "그대로"
CHANGE = _HEAD + "전"
GONE = "지울 상수"
TAIL = KEEP.removesuffix("로") + "!"
lower_name = "소문자는 보고 대상이 아니다"
'''

HEAD_A = '''
_HEAD = "머리 "
KEEP = "그대로"
CHANGE = _HEAD + "후"
TAIL = KEEP.removesuffix("로") + "!"
ADDED = """새
상수"""
lower_name = "소문자는 보고 대상이 아니다"
'''


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, str]:
    """base 커밋(a.py · gone.py) 뒤 작업 트리를 바꾼 임시 저장소 — (루트, base sha)."""
    prompts = tmp_path / "src" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "a.py").write_text(BASE_A, encoding="utf-8")
    (prompts / "gone.py").write_text('OLD = "파일째 삭제"\n', encoding="utf-8")
    (tmp_path / "other.py").write_text('OUTSIDE = "범위 밖"\n', encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    sha = _git(tmp_path, "rev-parse", "--short", "HEAD")
    (prompts / "a.py").write_text(HEAD_A, encoding="utf-8")
    (prompts / "gone.py").unlink()
    (prompts / "new.py").write_text('FRESH = "새 파일"\n', encoding="utf-8")
    (tmp_path / "other.py").write_text('OUTSIDE = "바뀌어도 대상 아님"\n', encoding="utf-8")
    return tmp_path, sha


class TestModuleConstants:
    def test_evaluates_literals_concat_and_pure_str_methods(self) -> None:
        consts, skipped = prd._module_constants(BASE_A)
        assert consts == {
            "_HEAD": "머리 ", "KEEP": "그대로", "CHANGE": "머리 전", "GONE": "지울 상수",
            "TAIL": "그대!",
        }
        assert skipped == []

    def test_fstring_imported_name_and_bad_format_are_skipped(self) -> None:
        source = (
            "from x import Y\n"
            'F = f"{1}"\n'
            'IMPORTED = Y + "a"\n'
            'BAD = "{a}".format(b="1")\n'
            'OK = "{a}".format(a="1")\n'
            'SET = frozenset({"a", "b"})\n'
            "NUM = 3\n"
        )
        consts, skipped = prd._module_constants(source)
        assert consts == {"OK": "1"}
        assert skipped == ["F", "IMPORTED", "BAD"]  # 비문자열(frozenset·정수)은 평가 불가가 아니다


class TestReportPrompts:
    def test_compare_rows_cover_every_status(self, repo: tuple[Path, str]) -> None:
        root, sha = repo
        rows, skipped = prd._compare_prompt_constants(
            prd._base_prompt_sources(sha, root), prd._worktree_prompt_sources(root)
        )
        by_name = {name: (status, path) for status, path, name, _b, _a in rows}
        assert by_name == {
            "_HEAD": ("일치", "src/prompts/a.py"),
            "KEEP": ("일치", "src/prompts/a.py"),
            "TAIL": ("일치", "src/prompts/a.py"),
            "CHANGE": ("변경", "src/prompts/a.py"),
            "GONE": ("삭제", "src/prompts/a.py"),
            "ADDED": ("신설", "src/prompts/a.py"),
            "OLD": ("삭제", "src/prompts/gone.py"),
            "FRESH": ("신설", "src/prompts/new.py"),
        }
        assert "OUTSIDE" not in by_name  # src/prompts 밖은 대상이 아니다
        assert skipped == []

    def test_report_prints_table_and_returns_zero(
        self, repo: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        root, sha = repo
        assert prd._report_prompts(sha, root) == 0
        out = capsys.readouterr().out
        assert f"base {sha}" in out
        assert "총 8개 / 일치 3 / 변경 1 / 신설 2 / 삭제 2" in out
        assert "  변경  src/prompts/a.py  CHANGE  (4자 → 4자)" in out
        assert "  신설  src/prompts/new.py  FRESH  (-자 → 4자)" in out
        assert "  삭제  src/prompts/gone.py  OLD  (6자 → -자)" in out
        assert "KEEP" not in out  # 일치 행은 수만 센다

    def test_no_change_says_zero(
        self, repo: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        root, _sha = repo
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "head")
        assert prd._report_prompts("HEAD", root) == 0
        assert "변경 0 — 전 상수 바이트 일치" in capsys.readouterr().out

    def test_unknown_sha_returns_2(
        self, repo: tuple[Path, str], capsys: pytest.CaptureFixture[str]
    ) -> None:
        root, _sha = repo
        assert prd._report_prompts("deadbeef", root) == 2
        assert "git 조회 실패(deadbeef)" in capsys.readouterr().out


def test_main_prompts_mode_skips_template_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--prompts`는 상수 대조만 한다 — 기본 모드(폴스타 템플릿 diff)를 돌리지 않는다."""
    calls: list[str] = []
    monkeypatch.setattr(prd, "_report_prompts", lambda sha: calls.append(sha) or 0)
    monkeypatch.setattr(prd, "_report_default", lambda: pytest.fail("기본 모드 호출"))
    monkeypatch.setattr(sys, "argv", ["prompt_render_diff.py", "--prompts", "abc123", "--ci"])
    assert prd.main() == 0
    assert calls == ["abc123"]
