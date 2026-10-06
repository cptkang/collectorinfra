"""모듈 단위 회귀 러너(scripts/regress.py · D-303 · plans/136) 단위 테스트.

가짜 저장소(tmp · git init)로 선택 규칙 ①~⑥ · --wide · 권고 조건 1~5 · 시그니처 비교 ·
실패 귀속 3갈래와 종료 코드 · `.env` 심링크 · worktree 정리를 고정하고, 과거 누락 2건(A1)은
실제 저장소로 고정한다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts import regress as rg  # noqa: E402

GIT_ID = ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *GIT_ID, "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body), encoding="utf-8")


def make_repo(root: Path, files: dict[str, str]) -> Path:
    """파일을 쓰고 첫 커밋을 만든다."""
    root.mkdir(parents=True, exist_ok=True)
    _write(root, files)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    return root


BASE_FILES = {
    "src/__init__.py": "",
    "src/pkg/__init__.py": "from src.pkg.mod import f\n",
    "src/pkg/mod.py": "def f(a, b=1):\n    return a + b\n",
    "src/pkg/other.py": "def g():\n    return 0\n",
    "src/user.py": "from src.pkg import mod\n\ndef h():\n    return mod.f(1)\n",
    "src/nodes/__init__.py": "",
    "src/nodes/lonely.py": "X = 1\n",
    "config/a/settings.yaml": "k: 1\n",
    "config/b/settings.yaml": "k: 2\n",
    "config/unique_name.yaml": "k: 3\n",
    "tests/__init__.py": "",
    "tests/test_from_pkg.py": "from src.pkg import f\n\ndef test_a():\n    assert f(1)\n",
    "tests/test_func_local.py": """\
        def test_local():
            from src.pkg.mod import f
            assert f(1) == 2
        """,
    "tests/test_patch_str.py": """\
        from unittest.mock import patch

        def test_p():
            with patch("src.pkg.mod.f", return_value=0):
                pass
        """,
    "tests/test_user.py": "from src.user import h\n\ndef test_h():\n    assert h() == 2\n",
    "tests/test_other.py": "from src.pkg.other import g\n\ndef test_g():\n    assert g() == 0\n",
    "tests/test_cfg.py": """\
        from pathlib import Path
        ROOT = Path(__file__).parents[1]

        def test_a():
            assert (ROOT / "config" / "a" / "settings.yaml").exists()

        def test_u():
            assert (ROOT / "config" / "unique_name.yaml").exists()
        """,
    "tests/test_guard.py": """\
        import pytest

        @pytest.mark.repo_guard
        def test_scan():
            pass

        def test_plain():
            pass

        @pytest.mark.repo_guard
        class TestWide:
            def test_one(self):
                pass
        """,
    "tests/sub/__init__.py": "",
    "tests/sub/helper.py": "VALUE = 1\n",
    "tests/sub/conftest.py": "",
    "tests/sub/test_rel.py": "from .helper import VALUE\n\ndef test_v():\n    assert VALUE\n",
    "tests/test_nodes/__init__.py": "",
    "tests/test_nodes/test_nothing.py": "def test_n():\n    pass\n",
    "apm_gateway/apm_gateway/__init__.py": "",
    "apm_gateway/apm_gateway/core.py": "Y = 1\n",
    "apm_gateway/tests/test_core.py": "def test_c():\n    pass\n",
}


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return make_repo(tmp_path_factory.mktemp("regress") / "repo", BASE_FILES)


@pytest.fixture(scope="module")
def index(repo: Path) -> rg.Index:
    return rg.Index(repo)


def _pick(index: rg.Index, *changes: rg.Change, wide: bool = False) -> set[str]:
    return set(rg.select(index, list(changes), wide=wide).body)


GUARDS = {"tests/test_guard.py::test_scan", "tests/test_guard.py::TestWide"}


# ── 선택 규칙 ───────────────────────────────────────────────────────────────


def test_rule1_changed_test_file(index: rg.Index) -> None:
    assert _pick(index, rg.Change("tests/test_other.py", "M")) == {"tests/test_other.py"} | GUARDS


def test_rule2_direct_import_forms(index: rg.Index) -> None:
    picked = _pick(index, rg.Change("src/pkg/mod.py", "M"))
    # from-import 서브모듈 · 함수 안 import · patch 문자열
    assert {"tests/test_func_local.py", "tests/test_patch_str.py"} <= picked
    # 중간 모듈(src.user)을 거쳐서만 닿는 테스트는 직접 import가 아니다
    assert "tests/test_user.py" not in picked
    assert "tests/test_other.py" not in picked


def test_rule2_package_init_selects_package_level_importers_only(index: rg.Index) -> None:
    picked = _pick(index, rg.Change("src/pkg/__init__.py", "M"))
    assert "tests/test_from_pkg.py" in picked
    assert "tests/test_func_local.py" not in picked  # from src.pkg.mod import — 서브모듈 직접


def test_rule2_relative_import_of_test_helper(index: rg.Index) -> None:
    assert "tests/sub/test_rel.py" in _pick(index, rg.Change("tests/sub/helper.py", "M"))


def test_rule3_file_reference_disambiguates_common_names(index: rg.Index) -> None:
    assert "tests/test_cfg.py" in _pick(index, rg.Change("config/a/settings.yaml", "M"))
    # 같은 파일명이 둘이면 부모 폴더 이름까지 맞아야 한다
    assert "tests/test_cfg.py" not in _pick(index, rg.Change("config/b/settings.yaml", "M"))
    assert "tests/test_cfg.py" in _pick(index, rg.Change("config/unique_name.yaml", "M"))


def test_rule4_package_runs_whole_package(index: rg.Index) -> None:
    sel = rg.select(index, [rg.Change("apm_gateway/apm_gateway/core.py", "M")])
    assert sel.packages == ["apm_gateway"]
    assert set(sel.body) == GUARDS


def test_rule5_guards_always_included(index: rg.Index) -> None:
    assert rg.Index.guard_nodes(index) == {
        "tests/test_guard.py::test_scan": 1, "tests/test_guard.py::TestWide": 1,
    }
    assert GUARDS <= _pick(index, rg.Change("config/unique_name.yaml", "M"))


def test_rule6_empty_falls_back_to_test_folder(index: rg.Index) -> None:
    sel = rg.select(index, [rg.Change("src/nodes/lonely.py", "M")])
    assert sel.empty
    assert "tests/test_nodes" in sel.body


def test_conftest_subdir_selects_tests_under_it(index: rg.Index) -> None:
    assert "tests/sub/test_rel.py" in _pick(index, rg.Change("tests/sub/conftest.py", "M"))


def test_wide_adds_one_hop(index: rg.Index) -> None:
    narrow = _pick(index, rg.Change("src/pkg/other.py", "M"))
    assert "tests/test_user.py" not in _pick(index, rg.Change("src/pkg/mod.py", "M"))
    assert "tests/test_user.py" in _pick(index, rg.Change("src/pkg/mod.py", "M"), wide=True)
    assert narrow == {"tests/test_other.py"} | GUARDS


# ── 권고 조건 ───────────────────────────────────────────────────────────────


def _reasons(index: rg.Index, changes: list[rg.Change], sig: list[str] | None = None,
             **kw: float) -> list[str]:
    return rg.recommend(index, changes, rg.select(index, changes), sig or [], **kw)


def test_recommend_none_for_small_change(index: rg.Index) -> None:
    assert _reasons(index, [rg.Change("src/pkg/other.py", "M")]) == []


def test_recommend_cond1_test_infra(index: rg.Index) -> None:
    for path in ("pyproject.toml", "uv.lock", "tests/conftest.py"):
        assert any("테스트 기반" in r for r in _reasons(index, [rg.Change(path, "M")]))
    # 독립 패키지 pyproject는 ④가 패키지 전체를 돌리므로 권고하지 않는다
    assert _reasons(index, [rg.Change("apm_gateway/pyproject.toml", "M")]) == []


def test_recommend_cond2_hub_by_transitive_share(index: rg.Index) -> None:
    # src.pkg.mod에는 src.pkg(__init__ — 형제 모듈 import 때도 실행)·src.user를 거쳐 닿는다
    ratio = index.transitive_ratio("src.pkg.mod")
    assert ratio > index.transitive_ratio("src.pkg.other")
    changes = [rg.Change("src/pkg/mod.py", "M")]
    assert any("허브" in r for r in _reasons(index, changes, hub=ratio, direct=1.1))
    assert not any("허브" in r for r in _reasons(index, changes, hub=ratio + 0.01, direct=1.1))


def test_recommend_cond3_signature_and_cond4_moved(index: rg.Index) -> None:
    reasons = _reasons(index, [rg.Change("src/pkg/other.py", "M")], sig=["src.pkg.other.g 삭제"])
    assert any("src.pkg.other.g 삭제" in r for r in reasons)
    moved = _reasons(index, [rg.Change("src/pkg/new.py", "R", "src/pkg/other.py")])
    assert any("이동·삭제 — src/pkg/other.py" in r for r in moved)


def test_recommend_cond5_direct_ratio(index: rg.Index) -> None:
    changes = [rg.Change("src/pkg/mod.py", "M")]
    ratio = rg.direct_ratio(index, rg.select(index, changes))
    assert any("직접 선택 비율" in r for r in _reasons(index, changes, hub=2, direct=ratio))
    assert not _reasons(index, changes, hub=2, direct=ratio + 0.01)


# ── 공개 시그니처 비교 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("old,new,broken", [
    ("def f(a, b=1): pass", "def f(a, b=1, c=2): pass", False),        # 기본값 있는 추가
    ("def f(a): pass", "def f(a): pass\ndef g(): pass", False),         # 새 공개 심볼
    ("def _p(a): pass", "def _p(): pass", False),                       # 비공개
    ("def f(a): pass", "from x import f", False),                       # 다시 내보냄
    ("def f(a, b): pass", "def f(a): pass", True),                      # 매개변수 삭제
    ("def f(a, b): pass", "def f(a, c): pass", True),                   # 이름 변경
    ("def f(a, b): pass", "def f(b, a): pass", True),                   # 순서 변경
    ("def f(a, b): pass", "def f(a, *, b): pass", True),                # 종류 변경
    ("def f(a, b=1): pass", "def f(a, b): pass", True),                 # 기본값 제거
    ("def f(a): pass", "def f(a, b): pass", True),                      # 기본값 없는 새 매개변수
    ("def f(a) -> int: pass", "def f(a) -> str: pass", True),           # 반환 주석 변경
    ("def f(a): pass", "", True),                                       # 공개 심볼 삭제
    ("class C:\n def m(self, x): pass", "class C:\n def m(self): pass", True),
    ("class C:\n def __init__(self, x): pass", "class C:\n def __init__(self): pass", True),
    ("class C:\n def m(self, x): pass", "class C:\n def m(self, x, y=0): pass", False),
])
def test_signature_breaks(old: str, new: str, broken: bool) -> None:
    assert bool(rg.api_breaks(old, new)) is broken


def test_signature_break_auto_applies_wide(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r", BASE_FILES)
    _write(root, {"src/pkg/mod.py": "def f(a):\n    return a\n"})
    sig = rg.signature_breaks(root, "HEAD", [rg.Change("src/pkg/mod.py", "M")])
    assert sig == ["src.pkg.mod.f(매개변수 삭제 b)"]
    out = _main_plan(root, ["--files", "src/pkg/mod.py"])
    assert "--wide" in out and "tests/test_user.py" in out
    assert "[전체 회귀 권고] 사유:" in out and "공개 시그니처" in out
    assert out.rstrip().endswith("범위: 계획만 — 실행 안 함")


def _main_plan(root: Path, extra: list[str]) -> str:
    proc = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "regress.py"), "--root", str(root),
         "--base", "HEAD", "--plan", *extra],
        capture_output=True, text=True, cwd=root,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def test_plan_full_has_no_recommendation_and_full_range(repo: Path) -> None:
    out = _main_plan(repo, ["--full", "--files", "pyproject.toml"])
    assert "[전체 회귀 권고]" not in out
    assert out.rstrip().endswith("범위: 계획만(전체) — 실행 안 함")


def test_plan_reports_empty_selection(repo: Path) -> None:
    out = _main_plan(repo, ["--files", "src/nodes/lonely.py"])
    assert "선택된 테스트 없음" in out and "tests/test_nodes" in out


def test_usage_errors_exit_2(repo: Path) -> None:
    assert rg.main(["--root", str(repo), "--base", "no-such-ref", "--plan"]) == 2
    assert rg.main(["--root", str(repo), "--base", "HEAD", "--plan", "--files",
                    str(repo / "src")]) == 2
    assert rg.main(["--root", str(repo), "--base", "HEAD", "--plan", "--files",
                    str(_ROOT / "pyproject.toml")]) == 2  # 저장소 밖 경로


# ── 보조 함수 ───────────────────────────────────────────────────────────────


def test_collect_changes_includes_untracked_and_files_mode(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r", {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    _write(root, {"a.py": "x = 2\n", "c.py": "z = 1\n"})
    (root / "b.py").unlink()
    got = {(c.path, c.status) for c in rg.collect_changes(root, "HEAD")}
    assert got == {("a.py", "M"), ("b.py", "D"), ("c.py", "A")}
    files = rg.collect_changes(root, "HEAD", [str(root / "a.py"), str(root / "gone.py")])
    assert [(c.path, c.status) for c in files] == [("a.py", "M"), ("gone.py", "D")]


def test_diff_lines_marks_changed_and_new_files(tmp_path: Path) -> None:
    root = make_repo(tmp_path / "r", {"a.py": "1\n2\n3\n"})
    _write(root, {"a.py": "1\nX\n3\nY\n", "new.py": "n\n"})
    assert rg.diff_lines(root, "HEAD", ["a.py", "new.py"]) == {"a.py": {2, 4}, "new.py": None}


def test_parse_junit_node_ids(tmp_path: Path) -> None:
    xml = tmp_path / "j.xml"
    xml.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="" name="tests.test_b" file="tests/test_b.py"><error/></testcase>'
        '<testcase classname="tests.test_a.TestX.TestIn" name="test_n" file="tests/test_a.py">'
        '<failure/></testcase>'
        '<testcase classname="tests.test_a" name="test_p[a::b]" file="tests/test_a.py"/>'
        '<testcase classname="tests.test_a" name="test_s" file="tests/test_a.py"><skipped/>'
        '</testcase></testsuite></testsuites>', encoding="utf-8")
    got = {c.nodeid: c.outcome for c in rg.parse_junit(xml)}
    assert got == {
        "tests/test_b.py": "error",
        "tests/test_a.py::TestX::TestIn::test_n": "failed",
        "tests/test_a.py::test_p[a::b]": "passed",
        "tests/test_a.py::test_s": "skipped",
    }


def test_split_workers_caps_total() -> None:
    small = rg.SERIAL_THRESHOLD - 1
    assert rg.split_workers(small, 0) == (0, 0)
    assert rg.split_workers(small, 10_000, total=8)[0] == 0
    if (os.cpu_count() or 1) >= 8:
        assert rg.split_workers(10_000, 1_000, total=8) == (6, 2)
    body, apm = rg.split_workers(10_000, 1_000)
    assert body + apm <= rg.MAX_WORKERS and body >= apm


def test_build_jobs_splits_serial_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = make_repo(tmp_path / "r", {
        "tests/__init__.py": "",
        "tests/test_s.py": "import pytest\n\n@pytest.mark.serial\ndef test_s():\n    pass\n",
    })
    monkeypatch.setattr(rg, "SERIAL_THRESHOLD", 1)
    monkeypatch.setattr(rg, "_find_tool", lambda name: None)
    index = rg.Index(root)
    sel = rg.select(index, [rg.Change("tests/test_s.py", "M")])
    lanes, _, _ = rg.build_jobs(root, index, sel, tmp_path, False, [])
    parallel, serial = lanes[0]
    assert parallel.cmd[-4:] == ["-m", "not serial", "-n", str(rg.split_workers(1, 0)[0])]
    assert serial.cmd[-2:] == ["-m", "serial"] and "-n" not in serial.cmd


# ── 실패 귀속(W3) ───────────────────────────────────────────────────────────


ATTR_FILES = {
    ".gitignore": ".env\n",
    "src/__init__.py": "",
    "src/calc.py": "VERSION = 1\n\ndef add(a, b):\n    return a + b\n",
    "tests/__init__.py": "",
    "tests/test_orig.py": "def test_always_broken():\n    assert False\n",
    "tests/test_calc.py": (
        "from src.calc import add\n\ndef test_add():\n    assert add(1, 1) == 2\n"
    ),
    "tests/test_envdep.py": """\
        from pathlib import Path

        from src import calc

        def test_needs_env():
            env = Path(__file__).resolve().parents[1] / ".env"
            assert env.read_text().strip() == "FLAG=1"
            assert calc.VERSION == 1
        """,
}


def _worktrees(root: Path) -> list[str]:
    return [line for line in _git(root, "worktree", "list", "--porcelain").splitlines()
            if line.startswith("worktree ")]


def test_attribution_three_ways_env_symlink_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = make_repo(tmp_path / "r", ATTR_FILES)
    _write(root, {
        ".env": "FLAG=1\n",
        "src/calc.py": "VERSION = 2\n\ndef add(a, b):\n    return a - b\n",
        "tests/test_brand_new.py": "def test_new():\n    assert False\n",
    })
    assert rg.env_files(root) == [".env"]
    monkeypatch.setattr(rg, "_find_tool", lambda name: None)
    monkeypatch.chdir(root)
    code = rg.main(["--root", str(root), "--base", "HEAD", "--files", "src/calc.py",
                    "tests/test_orig.py", "tests/test_brand_new.py", "tests/test_envdep.py"])
    out = capsys.readouterr().out
    assert code == 1
    assert f"[{rg.ORIGINAL}] 본체 tests/test_orig.py::test_always_broken" in out
    assert f"[{rg.CAUSED}] 본체 tests/test_calc.py::test_add" in out
    # A5 — `.env`가 사본에 심링크돼 있어야 「원래 실패」로 오판하지 않는다
    assert f"[{rg.CAUSED}] 본체 tests/test_envdep.py::test_needs_env" in out
    assert f"[{rg.NEW_TEST}] 본체 tests/test_brand_new.py::test_new" in out
    assert out.rstrip().endswith("범위: 모듈 단위 — 전체 미실행")
    assert "ruff: 미실행 — 도구 없음" in out
    # 자기 worktree만 지우고 남기지 않는다
    assert len(_worktrees(root)) == 1
    assert not (Path(tempfile.gettempdir()) / f"regress-{os.getpid()}").exists()
    failed_ids = sorted((root / "logs" / "regress").glob("*/failed_ids.txt"))
    assert failed_ids and "test_calc.py::test_add" in failed_ids[-1].read_text(encoding="utf-8")


def test_attribution_original_only_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    root = make_repo(tmp_path / "r", ATTR_FILES)
    _write(root, {".env": "FLAG=1\n"})
    monkeypatch.setattr(rg, "_find_tool", lambda name: None)
    monkeypatch.chdir(root)
    code = rg.main(["--root", str(root), "--base", "HEAD", "--files", "tests/test_orig.py"])
    out = capsys.readouterr().out
    assert code == 0
    assert f"[{rg.ORIGINAL}] 본체 tests/test_orig.py::test_always_broken" in out
    assert len(_worktrees(root)) == 1


def test_attribution_refuses_copy_resolving_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """사본의 패키지가 사본 밖으로 해소되면 귀속을 중단한다(docs/18:284 · :312)."""
    root = make_repo(tmp_path / "r", ATTR_FILES)
    groups = {"본체": {"subdir": "", "python": sys.executable, "imports": ["src"]}}
    real_git = rg._git

    def add_then_redirect(root_: Path, *args: str, check: bool = True) -> str:
        out = real_git(root_, *args, check=check)
        if args[:2] == ("worktree", "add"):  # 사본의 src가 메인 트리로 새는 상황을 흉내 낸다
            wt = Path(args[3])
            shutil.rmtree(wt / "src")
            (wt / "src").symlink_to(root_ / "src")
        return out

    monkeypatch.setattr(rg, "_git", add_then_redirect)
    with pytest.raises(rg.AttributionError):
        rg.attribute(root, "HEAD", {"본체": ["tests/test_calc.py::test_add"]}, groups, tmp_path)
    assert len(_worktrees(root)) == 1


# ── 과거 누락 2건(A1) — 실제 저장소 ─────────────────────────────────────────


@pytest.fixture(scope="module")
def real_index() -> rg.Index:
    return rg.Index(_ROOT)


def test_a1_d176_multi_db_executor(real_index: rg.Index) -> None:
    picked = _pick(real_index, rg.Change("src/nodes/multi_db_executor.py", "M"))
    assert {"tests/test_nodes/test_multi_db_executor_body.py",
            "tests/test_nodes/test_multi_db_failure_audit.py"} <= picked


def test_a1_time_spec_function_local_import(real_index: rg.Index) -> None:
    picked = _pick(real_index, rg.Change("src/domain/time_spec.py", "M"))
    assert "tests/test_orchestration/test_plan123_disclosures.py" in picked
