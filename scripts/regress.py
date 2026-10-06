#!/usr/bin/env python3
"""모듈 단위 회귀 러너 (D-303 · plans/136).

바꾼 파일에서 관련 테스트만 골라 병렬로 돌리고, 실패한 테스트만 기준 커밋의 격리 사본에서
다시 돌려 「원래 실패 / 이번 변경 탓 / 새 테스트 실패」로 가른다. 표준 라이브러리만 쓴다.

    python scripts/regress.py                     # 모듈 단위(기본) — 병렬 · 정적 게이트 · 실패 귀속
    python scripts/regress.py --files src/a.py …   # 이 파일들만 변경으로 본다
    python scripts/regress.py --base <sha>        # 세션 시작 커밋(변경 목록 · 실패 귀속 기준)
    python scripts/regress.py --wide              # 공개 시그니처를 바꿨을 때 — 1단계 확장
    python scripts/regress.py --plan              # 무엇을 돌릴지 목록만(실행 안 함)
    python scripts/regress.py --full              # 사용자가 요청할 때만 — 전 패키지 전체

선택 규칙(plans/136 §3): ① 바뀐 테스트 파일 ② 바뀐 Python 모듈을 직접 import하는 테스트
(함수 안 import · 상대 import · 문자열 모듈 경로 포함 — 경로로 읽는 스크립트는 ③과 같은 파일 참조로)
③ 바뀐 비Python 파일의 경로·파일명을 문자열로 적은 테스트 ④ 독립 패키지 안 파일 → 그 패키지 전체
⑤ `repo_guard` 마커 테스트(항상) ⑥ 위가 비면 바뀐 파일의 테스트 폴더.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

BODY_TEST_ROOTS = ("tests", "noise_gate/tests")
BODY_TEST_PREFIXES = tuple(f"{r}/" for r in BODY_TEST_ROOTS)
MODULE_ROOTS = ("src", "noise_gate", "scripts", "tests")
PACKAGES = ("apm_gateway", "mcp_server", "sre_agent")
ROOT_CONFTESTS = frozenset({"conftest.py", "tests/conftest.py", "noise_gate/tests/conftest.py"})
# 권고 조건 1 — 독립 패키지의 pyproject·conftest는 ④가 패키지 전체를 돌리므로 넣지 않는다
TEST_INFRA_FILES = frozenset({"pyproject.toml", "uv.lock", "pytest.ini", "setup.cfg", "tox.ini"})
TEST_INFRA_FILES = TEST_INFRA_FILES | ROOT_CONFTESTS
DOTTED = re.compile(r"^(src|noise_gate|scripts|tests)(\.[A-Za-z_]\w*)+$")

MAX_WORKERS = 8
# 직렬 임계(정적 테스트 함수 수) — 실측(2026-10-06 · 부하 6~9): 72건 직렬 3.1초 < -n4 3.4초,
# 104~217건 7묶음 중 6묶음에서 -n8이 1.4~2.1배 빠름(1묶음은 0.3초 느림) · 1,208건 직렬 369초 vs
# -n4 76초
SERIAL_THRESHOLD = 100
# 권고 임계 — 척도는 정적 테스트 함수 수 비율. da3ea4f까지 최근 커밋 24건 재생(2026-10-06):
# 전이 영향 0.5~0.7은 그래프 조립 경유 고원(0.74)에 걸려 권고 67% → 0.85(src.config 등 기반
# 모듈만)에서 권고 5/24(21%)
HUB_THRESHOLD = 0.85
DIRECT_THRESHOLD = 0.4
GROUP_TIMEOUT = 1200
FULL_TIMEOUT = 2400

ORIGINAL = "원래 실패"
CAUSED = "이번 변경 탓"
NEW_TEST = "새 테스트 실패"
UNKNOWN = "판정 불가"


Groups = dict[str, dict[str, Any]]  # 귀속 그룹 → 사본 하위 폴더·인터프리터·해소 확인 패키지


class UsageError(Exception):
    """사용법 오류 — 종료 코드 2."""


class AttributionError(Exception):
    """실패 귀속 중단 — 기준 사본을 믿을 수 없다."""


# ── git ─────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str, check: bool = True) -> str:
    """git 명령을 돌려 표준 출력을 돌려준다."""
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=off", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and proc.returncode != 0:
        raise UsageError(f"git {' '.join(args)} 실패: {proc.stderr.strip()}")
    return proc.stdout


def _show(root: Path, ref: str, path: str) -> str | None:
    """커밋의 파일 내용(없으면 None)."""
    proc = subprocess.run(
        ["git", "-C", str(root), "show", f"{ref}:{path}"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout if proc.returncode == 0 else None


# ── 변경 목록 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Change:
    """바뀐 파일 하나. status는 A·M·D·R(R이면 old가 옛 경로)."""

    path: str
    status: str
    old: str | None = None


def collect_changes(root: Path, base: str, files: list[str] | None = None) -> list[Change]:
    """기준 커밋 대비 변경 목록(커밋분 + 작업 트리 + 추적 안 된 새 파일) 또는 --files 목록."""
    out: list[Change] = []
    if files is not None:
        for item in files:
            given = Path(item) if Path(item).is_absolute() else Path.cwd() / item
            try:
                rel = given.resolve().relative_to(root.resolve()).as_posix()
            except ValueError as exc:
                raise UsageError(f"저장소 밖 경로: {item}") from exc
            if (root / rel).is_dir():
                raise UsageError(f"디렉터리는 받지 않는다(파일을 나열하라): {item}")
            if not (root / rel).exists():
                out.append(Change(rel, "D"))
            else:
                tracked = _show(root, base, rel) is not None
                out.append(Change(rel, "M" if tracked else "A"))
        return list(dict.fromkeys(out))
    raw = _git(root, "diff", "--name-status", "-z", "-M", base).split("\0")
    i = 0
    while i < len(raw) and raw[i]:
        status = raw[i]
        if status[0] in "RC":
            out.append(Change(raw[i + 2], "R" if status[0] == "R" else "A", raw[i + 1]))
            i += 3
        else:
            out.append(Change(raw[i + 1], status[0] if status[0] in "ADM" else "M"))
            i += 2
    for new in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
        if new:
            out.append(Change(new, "A"))
    return list(dict.fromkeys(out))


# ── 파일 분류 ────────────────────────────────────────────────────────────────


def is_test_file(path: str) -> bool:
    """본체 테스트 파일(tests/ · noise_gate/tests/의 test_*.py · *_test.py)."""
    name = path.rsplit("/", 1)[-1]
    return (
        path.startswith(BODY_TEST_PREFIXES) and name.endswith(".py")
        and (name.startswith("test_") or name.endswith("_test.py"))
    )


def package_of(path: str) -> str | None:
    """독립 패키지(apm_gateway·mcp_server·sre_agent) 소속이면 그 이름."""
    head, _, rest = path.partition("/")
    return head if head in PACKAGES and rest else None


def module_name(path: str) -> str | None:
    """저장소 상대 경로 → 점 모듈 이름(src·noise_gate·scripts·tests 밖이면 None)."""
    if not path.endswith(".py") or package_of(path):
        return None
    parts = path[:-3].split("/")
    if parts[0] not in MODULE_ROOTS:
        return None
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def kind_of(path: str) -> str:
    """선택 규칙 종류 — test · conftest · package · module · file."""
    if package_of(path):
        return "package"
    if is_test_file(path):
        return "test"
    name = path.rsplit("/", 1)[-1]
    if name == "conftest.py" and (path == "conftest.py" or path.startswith(BODY_TEST_PREFIXES)):
        return "conftest"
    if module_name(path):
        return "module"
    return "file"


def is_source_module(path: str) -> bool:
    """src·noise_gate·scripts의 비테스트 모듈(권고 조건 2·3·4 대상)."""
    return kind_of(path) == "module" and not path.startswith(BODY_TEST_PREFIXES)


# ── AST 색인 ─────────────────────────────────────────────────────────────────


@dataclass
class PyInfo:
    """파이썬 파일 하나의 정적 정보."""

    refs: set[str] = field(default_factory=set)
    strings: set[str] = field(default_factory=set)
    tests: int = 0
    markers: set[str] = field(default_factory=set)
    guards: dict[str, int] = field(default_factory=dict)  # 노드 접미 → 정적 건수("" = 파일)


def _has_mark(expr: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Attribute) and n.attr == name
        and isinstance(n.value, ast.Attribute) and n.value.attr == "mark"
        for n in ast.walk(expr)
    )


def _count_tests(body: list[ast.stmt]) -> int:
    total = 0
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name.startswith("test"):
            total += 1
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            total += _count_tests(node.body)
    return total


def _guard_nodes(tree: ast.Module) -> dict[str, int]:
    """repo_guard 마커가 붙은 노드 접미(파일 전체는 "")와 그 정적 건수."""
    out: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ) and _has_mark(node.value, "repo_guard"):
            return {"": _count_tests(tree.body)}

    def visit(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test") and any(
                    _has_mark(d, "repo_guard") for d in node.decorator_list
                ):
                    out[prefix + node.name] = 1
            elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                if any(_has_mark(d, "repo_guard") for d in node.decorator_list):
                    out[prefix + node.name] = _count_tests(node.body)
                else:
                    visit(node.body, f"{prefix}{node.name}::")

    visit(tree.body, "")
    return out


def parse_py(text: str, modname: str, is_init: bool) -> PyInfo:
    """import(함수 안 · 상대 · 서브모듈) · 문자열 상수 · 테스트 수 · 마커를 모은다."""
    info = PyInfo()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return info
    package = modname.split(".") if is_init else modname.split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            info.refs.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                keep = len(package) - (node.level - 1)
                base = ".".join(package[: max(keep, 0)] + ([base] if base else []))
            if base:
                info.refs.add(base)
                info.refs.update(f"{base}.{a.name}" for a in node.names if a.name != "*")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            info.strings.add(node.value)
            if DOTTED.match(node.value):  # patch("src.x.y.f") · import_module("src.x")
                info.refs.add(node.value)
        elif isinstance(node, ast.Attribute) and node.attr in ("serial", "repo_guard"):
            if isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
                info.markers.add(node.attr)
    info.tests = _count_tests(tree.body)
    if "repo_guard" in info.markers:
        info.guards = _guard_nodes(tree)
    return info


def _parents(mod: str) -> list[str]:
    parts = mod.split(".")
    return [".".join(parts[:i]) for i in range(1, len(parts))]


class Index:
    """저장소의 파이썬 파일 import 그래프와 테스트 정적 건수."""

    def __init__(self, root: Path, extra_modules: Iterable[str] = ()) -> None:
        self.root = root
        listed = _git(root, "ls-files", "-co", "--exclude-standard", "-z").split("\0")
        self.files = [p for p in listed if p and (root / p).is_file()]
        self.name_count = Counter(p.rsplit("/", 1)[-1] for p in self.files)
        self.info: dict[str, PyInfo] = {}
        self.mod_of: dict[str, str] = {}
        for path in self.files:
            mod = module_name(path)
            if mod is None:
                continue
            text = (root / path).read_text(encoding="utf-8", errors="replace")
            self.info[path] = parse_py(text, mod, path.endswith("__init__.py"))
            self.mod_of[path] = mod
        self.path_of = {m: p for p, m in self.mod_of.items()}
        self.known = set(self.path_of) | set(extra_modules)
        self.tests = sorted(p for p in self.info if is_test_file(p))
        self.count = {t: self.info[t].tests for t in self.tests}
        self.total = sum(self.count.values()) or 1
        self.deps = {p: self._resolve(i.refs, self.mod_of[p]) for p, i in self.info.items()}
        self.pkg_count = {pkg: self._package_count(pkg) for pkg in PACKAGES}
        self._tails: dict[str, set[str]] = {}
        self._blob: dict[str, str] = {}
        self._rev: dict[str, set[str]] | None = None

    def _resolve(self, refs: set[str], own: str) -> set[str]:
        out: set[str] = set()
        for ref in refs:
            parts = ref.split(".")
            for i in range(len(parts), 0, -1):
                cand = ".".join(parts[:i])
                if cand in self.known:
                    if cand != own:
                        out.add(cand)
                    break
        return out

    def _package_count(self, pkg: str) -> int:
        total = 0
        for path in self.files:
            name = path.rsplit("/", 1)[-1]
            if path.startswith(f"{pkg}/tests/") and name.startswith("test_") \
                    and name.endswith(".py"):
                text = (self.root / path).read_text(encoding="utf-8", errors="replace")
                try:
                    total += _count_tests(ast.parse(text).body)
                except (SyntaxError, ValueError):
                    continue
        return total

    # ② 직접 import
    def importers(self, mods: set[str], tests_only: bool = True) -> set[str]:
        """mods를 직접 참조하는 파일(기본은 테스트 파일만)."""
        pool = self.tests if tests_only else self.info
        return {p for p in pool if self.deps[p] & mods}

    # ③ 파일 참조
    def file_refs(self, path: str) -> set[str]:
        """경로 또는 파일명을 문자열 상수로 적은 테스트 파일.

        파일명이 저장소에 여럿이면(`__init__.py`·`index.html` 등) 부모 폴더 이름도 함께 있어야
        한다 — `"admin/dashboard.html"` 또는 `ROOT / "routes" / "query.py"`처럼.
        """
        name = path.rsplit("/", 1)[-1]
        parent = path.rsplit("/", 2)[-2] if path.count("/") else ""
        unique = self.name_count.get(name, 0) <= 1
        suffix = f"{parent}/{name}" if parent else name
        hits: set[str] = set()
        for test in self.tests:
            strings = self.info[test].strings
            if path in self._blob_of(test):
                hits.add(test)
            elif unique and (name in strings or name in self._tails_of(test)):
                hits.add(test)
            elif not unique and (
                suffix in strings or suffix in self._tails_of(test)
                or (name in strings and parent in strings)
            ):
                hits.add(test)
        return hits

    def _blob_of(self, test: str) -> str:
        if test not in self._blob:
            self._blob[test] = "\0".join(self.info[test].strings)
        return self._blob[test]

    def _tails_of(self, test: str) -> set[str]:
        if test not in self._tails:
            tails: set[str] = set()
            for s in self.info[test].strings:
                if "/" in s and len(s) < 300:
                    parts = s.split("/")
                    tails.update("/".join(parts[i:]) for i in range(1, len(parts)))
            self._tails[test] = tails
        return self._tails[test]

    # 권고 조건 2 — 전이 import 영향
    def transitive_ratio(self, mod: str) -> float:
        """mod에 전이적으로 닿는 테스트의 정적 건수 비율(패키지 __init__ 실행 포함)."""
        if self._rev is None:
            rev: dict[str, set[str]] = defaultdict(set)
            for path, deps in self.deps.items():
                me = self.mod_of[path]
                for dep in deps | {p for p in _parents(me) if p in self.known}:
                    rev[dep].add(me)
                    for parent in _parents(dep):
                        if parent in self.known:
                            rev[parent].add(me)
            self._rev = rev
        seen, stack = {mod}, [mod]
        while stack:
            for up in self._rev.get(stack.pop(), ()):
                if up not in seen:
                    seen.add(up)
                    stack.append(up)
        return sum(self.count[t] for t in self.tests if self.mod_of[t] in seen) / self.total

    def guard_nodes(self) -> dict[str, int]:
        """⑤ repo_guard 노드 ID → 정적 건수."""
        out: dict[str, int] = {}
        for test in self.tests:
            for suffix, n in self.info[test].guards.items():
                out[f"{test}::{suffix}" if suffix else test] = n
        return out

    def static_count(self, item: str) -> int:
        """파일·폴더·노드 ID의 정적 건수."""
        if item in self.count:
            return self.count[item]
        if "::" in item:
            return self.guard_nodes().get(item, 1)
        prefix = item.rstrip("/") + "/"
        return sum(n for t, n in self.count.items() if t.startswith(prefix))


# ── 선택 ─────────────────────────────────────────────────────────────────────


@dataclass
class Row:
    """출력 표의 한 줄 — 바뀐 것 하나와 그것이 고른 테스트."""

    label: str
    rule: str
    files: set[str] = field(default_factory=set)
    nodes: set[str] = field(default_factory=set)
    package: str | None = None
    note: str = ""


@dataclass
class Selection:
    """선택 결과."""

    rows: list[Row]
    body: list[str]
    packages: list[str]
    direct: set[str]
    empty: bool
    wide: bool


def _fallback_dir(root: Path, path: str) -> str | None:
    """⑥ 바뀐 파일이 속한 테스트 폴더(testpaths 뿌리는 제외)."""
    parts = path.split("/")
    cands: list[str] = []
    if path.startswith(BODY_TEST_PREFIXES):
        cands.append("/".join(parts[:-1]))
    elif parts[0] in ("src", "scripts") and len(parts) >= 3:
        cands.append(f"tests/test_{parts[1]}")
    elif parts[0] == "scripts":
        cands.append("tests/test_scripts")
    elif parts[0] == "src":
        cands.append(f"tests/test_{Path(parts[-1]).stem}")
    elif parts[0] == "noise_gate" and len(parts) >= 3:
        cands.append(f"noise_gate/tests/test_{parts[1]}")
    for cand in cands:
        if cand not in BODY_TEST_ROOTS and (root / cand).is_dir():
            return cand
    return None


def select(index: Index, changes: list[Change], wide: bool = False) -> Selection:
    """선택 규칙 ①~⑥(+ --wide)을 적용한다."""
    rows: list[Row] = []
    packages: dict[str, list[str]] = defaultdict(list)
    changed_mods: dict[str, str] = {}
    for ch in changes:
        for path in [ch.path] + ([ch.old] if ch.old else []):
            gone = path == ch.old or not (index.root / path).is_file()
            kind = kind_of(path)
            if kind == "package":
                packages[package_of(path) or ""].append(path)
                refs = index.file_refs(path)
                if refs:
                    rows.append(Row(path, "③ 파일 참조", refs))
            elif kind == "test":
                note = "삭제됨 — 실행 대상 없음" if gone else ""
                rows.append(Row(path, "① 테스트", set() if gone else {path}, note=note))
            elif kind == "conftest":
                folder = path.rsplit("/", 1)[0] if "/" in path else ""
                if path in ROOT_CONFTESTS:
                    rows.append(Row(path, "루트 conftest(권고 조건 1)", set(),
                                    note="전체를 자동 실행하지 않는다"))
                else:
                    under = {t for t in index.tests if t.startswith(folder + "/")}
                    rows.append(Row(path, "conftest 하위", under))
            elif kind == "module":
                mod = module_name(path) or ""
                changed_mods[mod] = path
                files = index.importers({mod}) | index.file_refs(path)
                rows.append(Row(path, "② 직접 import", files - {path},
                                note="삭제·이동" if gone else ""))
            else:
                rows.append(Row(path, "③ 파일 참조", index.file_refs(path)))
    direct = set().union(*(r.files for r in rows)) if rows else set()
    if wide and changed_mods:
        for mod, path in changed_mods.items():
            via = {index.mod_of[p] for p in index.importers({mod}, tests_only=False)
                   if not is_test_file(p)} - set(changed_mods)
            extra = index.importers(via) - direct if via else set()
            rows.append(Row(f"{path} ↳ 1단계 확장", "--wide", extra,
                            note=f"경유 모듈 {len(via)}"))
    picked = set().union(*(r.files for r in rows)) if rows else set()
    empty = not picked and not packages
    if empty:
        for ch in changes:
            fallback = _fallback_dir(index.root, ch.path)
            if fallback:
                rows.append(Row(ch.path, "⑥ 폴더 대체", {fallback}))
        picked = set().union(*(r.files for r in rows)) if rows else set()
    guards = index.guard_nodes()
    rows.append(Row("저장소 전역 가드", "⑤ repo_guard", nodes=set(guards)))
    for pkg in PACKAGES:
        if pkg in packages:
            rows.append(Row(f"{pkg}/ (변경 {len(packages[pkg])})", "④ 패키지 전체",
                            package=pkg))
    folders = tuple(p.rstrip("/") + "/" for p in picked if not p.endswith(".py"))
    body = sorted(picked) + sorted(
        n for n in guards
        if n.split("::", 1)[0] not in picked and not n.startswith(folders or ("\0",))
    )
    return Selection(rows, body, [p for p in PACKAGES if p in packages], direct, empty, wide)


def direct_ratio(index: Index, sel: Selection) -> float:
    """직접 선택(①②③ · 하위 conftest) 정적 건수 비율."""
    return sum(index.static_count(f) for f in sel.direct) / index.total


# ── 공개 시그니처 비교(권고 조건 3) ──────────────────────────────────────────


def _public(name: str) -> bool:
    return not name.startswith("_") or name in ("__init__", "__call__")


Param = tuple[str, str, bool]  # (이름, 종류, 기본값 유무)
Sig = tuple[tuple[Param, ...], str | None]  # (매개변수, 반환 주석)


def _signature(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> Sig:
    a = fn.args
    params: list[Param] = []
    positional = a.posonlyargs + a.args
    first_default = len(positional) - len(a.defaults)
    for i, arg in enumerate(positional):
        kind = "posonly" if i < len(a.posonlyargs) else "pos"
        params.append((arg.arg, kind, i >= first_default))
    if a.vararg:
        params.append((a.vararg.arg, "var", False))
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        params.append((arg.arg, "kwonly", default is not None))
    if a.kwarg:
        params.append((a.kwarg.arg, "varkw", False))
    return tuple(params), ast.unparse(fn.returns) if fn.returns else None


def public_api(src: str) -> tuple[dict[str, Sig | str], set[str]] | None:
    """공개 최상위 함수·클래스·클래스 공개 메서드의 시그니처와 최상위에 묶인 이름."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return None
    api: dict[str, Sig | str] = {}
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bound.add(node.name)
            if not node.name.startswith("_"):
                api[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef):
            bound.add(node.name)
            if not node.name.startswith("_"):
                api[node.name] = "class"
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and _public(sub.name):
                        api[f"{node.name}.{sub.name}"] = _signature(sub)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            bound.update(n.id for t in targets for n in ast.walk(t) if isinstance(n, ast.Name))
    return api, bound


def _param_breaks(old: Sig, new: Sig) -> list[str]:
    (o_params, o_ret), (n_params, n_ret) = old, new
    out: list[str] = []
    o_pos = [p for p in o_params if p[1] in ("posonly", "pos")]
    n_pos = [p for p in n_params if p[1] in ("posonly", "pos")]
    n_by_name = {p[0]: p for p in n_params if p[1] in ("pos", "kwonly")}
    for i, (name, kind, dflt) in enumerate(o_pos):
        if i >= len(n_pos):
            out.append(f"매개변수 삭제 {name}")
            continue
        n_name, n_kind, n_dflt = n_pos[i]
        if kind == "pos" and n_name != name:
            out.append(f"매개변수 이름·순서 변경 {name}→{n_name}")
            break  # 뒤 위치는 연쇄로 어긋나므로 첫 지점만 적는다
        if kind == "pos" and n_kind == "posonly":
            out.append(f"매개변수 종류 변경 {name}")
        if dflt and not n_dflt:
            out.append(f"기본값 제거 {name}")
    out.extend(f"기본값 없는 새 매개변수 {n}" for n, _, d in n_pos[len(o_pos):] if not d)
    old_names = {p[0] for p in o_params if p[1] in ("posonly", "pos", "kwonly")}
    for name, kind, dflt in o_params:
        if kind != "kwonly":
            continue
        cur = n_by_name.get(name)
        if cur is None:
            out.append(f"매개변수 삭제 {name}")
        elif dflt and not cur[2]:
            out.append(f"기본값 제거 {name}")
    out.extend(
        f"기본값 없는 새 매개변수 {n}"
        for n, k, d in n_params if k == "kwonly" and not d and n not in old_names
    )
    for kind, label in (("var", "*args 제거"), ("varkw", "**kwargs 제거")):
        if any(p[1] == kind for p in o_params) and not any(p[1] == kind for p in n_params):
            out.append(label)
    if o_ret is not None and o_ret != n_ret:
        out.append(f"반환 주석 변경 {o_ret}→{n_ret}")
    return out


def api_breaks(old_src: str, new_src: str) -> list[str]:
    """두 버전의 공개 API를 비교해 깨지는 변경만 돌려준다."""
    old, new = public_api(old_src), public_api(new_src)
    if old is None or new is None:
        return []
    (o_api, _), (n_api, n_bound) = old, new
    out: list[str] = []
    for name, sig in o_api.items():
        owner = name.split(".")[0]
        if name not in n_api:
            if "." in name and n_api.get(owner) != "class":
                continue  # 클래스 자체가 사라진 경우는 클래스 한 줄로 센다
            if "." not in name and name in n_bound:
                continue  # 다른 모듈에서 다시 내보낸다
            out.append(f"{name} 삭제")
            continue
        new_sig = n_api[name]
        if isinstance(sig, str) or isinstance(new_sig, str):
            if sig != new_sig:
                out.append(f"{name} 종류 변경")
            continue
        reasons = _param_breaks(sig, new_sig)
        if reasons:
            out.append(f"{name}({' · '.join(reasons)})")
    return out


def signature_breaks(root: Path, base: str, changes: list[Change],
                     head: str | None = None) -> list[str]:
    """바뀐 소스 모듈의 공개 시그니처 깨짐(head가 None이면 작업 트리와 비교)."""
    out: list[str] = []
    for ch in changes:
        if ch.status != "M" or not is_source_module(ch.path):
            continue
        old = _show(root, base, ch.path)
        if head is None:
            target = root / ch.path
            new = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
        else:
            new = _show(root, head, ch.path)
        if old is None or new is None:
            continue
        out.extend(f"{module_name(ch.path)}.{b}" for b in api_breaks(old, new))
    return out


# ── 전체 회귀 권고 ───────────────────────────────────────────────────────────


def recommend(index: Index, changes: list[Change], sel: Selection, sig: list[str],
              hub: float = HUB_THRESHOLD, direct: float = DIRECT_THRESHOLD) -> list[str]:
    """결정적 판정 — 권고 사유 목록(비면 권고 없음)."""
    reasons: list[str] = []
    paths = [p for c in changes for p in (c.path, c.old) if p]
    infra = sorted({p for p in paths if p in TEST_INFRA_FILES})
    if infra:
        reasons.append(f"테스트 기반 파일 변경 — {', '.join(infra)}")
    hubs: list[str] = []
    for path in sorted({c.path for c in changes if is_source_module(c.path)}):
        mod = module_name(path) or ""
        ratio = index.transitive_ratio(mod)
        if ratio >= hub:
            hubs.append(f"{mod} {ratio:.0%}")
    if hubs:
        reasons.append(f"허브 모듈 변경(전이 import 영향 ≥ {hub:.0%}) — {', '.join(hubs)}")
    if sig:
        shown = "; ".join(sig[:6]) + (f" 외 {len(sig) - 6}" if len(sig) > 6 else "")
        reasons.append(f"공개 시그니처·반환 형태 변경(--wide 자동 적용) — {shown}")
    moved = sorted({
        p for c in changes for p in ([c.old] if c.status == "R" else [c.path] if c.status == "D"
                                     else []) if p and is_source_module(p)
    })
    if moved:
        reasons.append(f"Python 모듈 이동·삭제 — {', '.join(moved)}")
    ratio = direct_ratio(index, sel)
    if ratio >= direct:
        reasons.append(f"직접 선택 비율 {ratio:.0%} ≥ {direct:.0%} — 전체와 차이가 작다")
    return reasons


# ── 실행 ─────────────────────────────────────────────────────────────────────


@dataclass
class Job:
    """별도 프로세스 하나."""

    name: str
    cmd: list[str]
    cwd: Path
    log: Path
    junit: Path | None = None
    env: dict[str, str] | None = None
    timeout: int = GROUP_TIMEOUT
    rc: int | None = None
    timed_out: bool = False
    elapsed: float = 0.0


_RUNNING: set[subprocess.Popen[bytes]] = set()
_LOCK = threading.Lock()


def _kill(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def run_job(job: Job) -> None:
    """출력을 파일로 받으며 실행한다(파이프 절단 없음 · 타임아웃이면 프로세스 그룹 종료)."""
    start = time.monotonic()
    with open(job.log, "wb") as fh:
        proc = subprocess.Popen(job.cmd, cwd=job.cwd, env=job.env, stdout=fh,
                                stderr=subprocess.STDOUT, start_new_session=True)
        with _LOCK:
            _RUNNING.add(proc)
        try:
            job.rc = proc.wait(timeout=job.timeout)
        except subprocess.TimeoutExpired:
            job.timed_out = True
            _kill(proc)
            proc.wait()
        except BaseException:
            _kill(proc)
            raise
        finally:
            with _LOCK:
                _RUNNING.discard(proc)
    job.elapsed = time.monotonic() - start


def _run_lane(jobs: list[Job]) -> None:
    for job in jobs:
        run_job(job)


def run_lanes(lanes: list[list[Job]]) -> None:
    """레인끼리는 동시에, 레인 안은 차례로 돈다."""
    threads = [threading.Thread(target=_run_lane, args=(lane,), daemon=True)
               for lane in lanes if lane]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
    except BaseException:
        with _LOCK:
            for proc in list(_RUNNING):
                _kill(proc)
        raise


@dataclass
class Case:
    """junit 테스트 케이스 하나."""

    nodeid: str
    file: str
    outcome: str  # passed · failed · error · skipped


def parse_junit(path: Path) -> list[Case]:
    """xunit1 junit(file 속성) → 노드 ID와 결과."""
    if not path.is_file():
        return []
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return []
    out: dict[str, Case] = {}
    rank = {"passed": 0, "skipped": 1, "failed": 2, "error": 3}
    for tc in tree.iter("testcase"):
        file = tc.get("file") or ""
        name, cls = tc.get("name") or "", tc.get("classname") or ""
        if not file:
            file = name.replace(".", "/") + ".py"
        if not cls:  # 수집 에러 — 파일 단위
            nodeid = file
        else:
            mod = file[:-3].replace("/", ".")
            inner = cls[len(mod) + 1:].split(".") if cls.startswith(mod + ".") else []
            nodeid = "::".join([file, *inner, name])
        tags = {child.tag for child in tc}
        outcome = ("error" if "error" in tags else "failed" if "failure" in tags
                   else "skipped" if "skipped" in tags else "passed")
        prev = out.get(nodeid)
        if prev is None or rank[outcome] > rank[prev.outcome]:
            out[nodeid] = Case(nodeid, file, outcome)
    return list(out.values())


def _find_tool(name: str) -> list[str] | None:
    """ruff·mypy 탐색 — PATH → uvx --offline → uv 캐시 바이너리(mypy)."""
    found = shutil.which(name)
    if found:
        return [found]
    uvx = shutil.which("uvx") or str(Path.home() / ".local" / "bin" / "uvx")
    if Path(uvx).is_file():
        probe = subprocess.run([uvx, "--offline", name, "--version"], capture_output=True)
        if probe.returncode == 0:
            return [uvx, "--offline", name]
    if name == "mypy":
        for cand in sorted((Path.home() / ".cache" / "uv" / "archive-v0").glob("*/bin/mypy")):
            if subprocess.run([str(cand), "--version"], capture_output=True).returncode == 0:
                return [str(cand)]
    return None


def diff_lines(root: Path, base: str, files: list[str]) -> dict[str, set[int] | None]:
    """파일별 이번 diff 줄 번호(기준 커밋에 없던 파일은 None = 전 줄)."""
    out: dict[str, set[int] | None] = {}
    if not files:
        return out
    text = _git(root, "diff", "-U0", "--no-color", "--no-ext-diff", base, "--", *files)
    current = None
    for line in text.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else None
            if current:
                out.setdefault(current, set())
        elif line.startswith("@@") and current:
            m = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if m:
                start, n = int(m.group(1)), int(m.group(2) or 1)
                lines = out[current]
                if lines is not None:
                    lines.update(range(start, start + n))
    for path in files:
        if path not in out and _show(root, base, path) is None:
            out[path] = None
    return out


@dataclass
class Gate:
    """정적 게이트 하나의 결과."""

    name: str
    status: str  # 통과 · 실패 · 미실행 · TIMEOUT
    detail: str = ""
    lines: list[str] = field(default_factory=list)


def _new_findings(log: Path, pattern: str,
                  lines: dict[str, set[int] | None]) -> tuple[int, list[str]]:
    total, new = 0, []
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    for raw in text.splitlines():
        m = re.match(pattern, raw)
        if not m:
            continue
        total += 1
        path, line_no = m.group(1), int(m.group(2))
        changed = lines.get(path, set())
        if changed is None or line_no in changed:
            new.append(raw)
    return total, new


# ── 실패 귀속 ────────────────────────────────────────────────────────────────


def env_files(root: Path) -> list[str]:
    """gitignore된 `.env` 계열 런타임 파일(저장소 뿌리와 최상위 폴더 한 단계까지)."""
    listed = _git(root, "ls-files", "-o", "-i", "--exclude-standard", "-z", "--",
                  ":(glob).env", ":(glob).encenv", ":(glob)*.env",
                  ":(glob)*/.env", ":(glob)*/.encenv", ":(glob)*/*.env", check=False)
    return sorted(p for p in listed.split("\0") if p and (root / p).is_file())


def _collected(cmd_py: str, cwd: Path, env: dict[str, str], files: list[str],
               log: Path) -> set[str]:
    proc = subprocess.run(
        [cmd_py, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         "--continue-on-collection-errors", *files],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=GROUP_TIMEOUT,
    )
    log.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    return {line.strip() for line in proc.stdout.splitlines() if "::" in line}


@dataclass
class Verdict:
    """실패 하나의 귀속 판정."""

    group: str
    nodeid: str
    verdict: str
    note: str = ""


def attribute(root: Path, base: str, failed: dict[str, list[str]], groups: Groups,
              run_dir: Path) -> list[Verdict]:
    """실패 ID만 기준 커밋의 격리 사본에서 다시 돌려 판정한다."""
    out: list[Verdict] = []
    wt = Path(tempfile.gettempdir()) / f"regress-{os.getpid()}"
    added = False
    try:
        try:
            _git(root, "worktree", "add", "--detach", str(wt), base)
        except UsageError as exc:
            raise AttributionError(f"기준 사본을 만들지 못했다 — {exc}") from exc
        added = True
        for rel in env_files(root):
            dst = wt / rel
            if dst.parent.is_dir() and not dst.exists():
                dst.symlink_to(root / rel)
        for group, ids in failed.items():
            meta = groups[group]
            cwd = wt / meta["subdir"] if meta["subdir"] else wt
            if not cwd.is_dir():
                out.extend(Verdict(group, i, NEW_TEST, "사본에 패키지 없음") for i in ids)
                continue
            env = dict(os.environ, PYTHONPATH=str(cwd))
            for pkg in meta["imports"]:
                if not (cwd / pkg / "__init__.py").is_file():
                    continue
                probe = subprocess.run(
                    [meta["python"], "-c", f"import {pkg}; print({pkg}.__file__)"],
                    cwd=cwd, env=env, capture_output=True, text=True,
                )
                where = Path(probe.stdout.strip() or "/").resolve()
                if not where.is_relative_to(cwd.resolve()):
                    raise AttributionError(
                        f"기준 사본의 `{pkg}`가 사본이 아니라 {where}로 해소됐다 — 귀속 중단"
                    )
            present = [i for i in ids if (cwd / i.split("::", 1)[0]).is_file()]
            out.extend(Verdict(group, i, NEW_TEST, "사본에 파일 없음")
                       for i in ids if i not in present)
            files = sorted({i.split("::", 1)[0] for i in present})
            stem = f"attrib-{group}"
            collected = _collected(meta["python"], cwd, env, files, run_dir / f"{stem}-collect.log")
            runnable = [i for i in present if "::" not in i or i in collected]
            out.extend(Verdict(group, i, NEW_TEST, "사본에서 수집 안 됨")
                       for i in present if i not in runnable)
            if not runnable:
                continue
            junit = run_dir / f"{stem}.xml"
            job = Job(stem, [meta["python"], "-m", "pytest", *runnable, "-q", "-p",
                             "no:cacheprovider", "--tb=short", "--continue-on-collection-errors",
                             "--junitxml", str(junit), "-o", "junit_family=xunit1"],
                      cwd, run_dir / f"{stem}.log", junit, env)
            run_job(job)
            cases = {c.nodeid: c for c in parse_junit(junit)}
            for nodeid in runnable:
                if job.timed_out:
                    out.append(Verdict(group, nodeid, UNKNOWN, "사본 재실행 TIMEOUT"))
                    continue
                if "::" not in nodeid:  # 수집 에러였던 파일
                    broken = cases.get(nodeid)
                    out.append(Verdict(group, nodeid, ORIGINAL if broken else CAUSED,
                                       "" if broken else "사본에서는 수집됨"))
                    continue
                case = cases.get(nodeid)
                if case is None:
                    out.append(Verdict(group, nodeid, UNKNOWN, "사본 결과 없음"))
                elif case.outcome in ("failed", "error"):
                    out.append(Verdict(group, nodeid, ORIGINAL))
                elif case.outcome == "skipped":
                    out.append(Verdict(group, nodeid, CAUSED, "사본에서 skip — 보수적 판정"))
                else:
                    out.append(Verdict(group, nodeid, CAUSED))
    finally:
        if added:
            subprocess.run(["git", "-C", str(root), "worktree", "remove", "--force", str(wt)],
                           capture_output=True)
    return out


# ── 출력 ─────────────────────────────────────────────────────────────────────


class Printer:
    """화면과 요약 파일에 함께 쓴다."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)


def _tally(cases: Iterable[Case]) -> str:
    c = Counter(x.outcome for x in cases)
    parts = [f"통과 {c['passed']:,}", f"실패 {c['failed']:,}", f"에러 {c['error']:,}",
             f"건너뜀 {c['skipped']:,}"]
    return " · ".join(parts)


def _row_cases(row: Row, cases: list[Case]) -> list[Case]:
    folders = tuple(f.rstrip("/") + "/" for f in row.files if not f.endswith(".py"))
    return [
        c for c in cases
        if c.file in row.files or (folders and c.file.startswith(folders))
        or any(c.nodeid == n or c.nodeid.startswith((n + "::", n + "[")) for n in row.nodes)
    ]


def print_table(out: Printer, index: Index, sel: Selection, results: dict[str, list[Case]] | None,
                plan: bool) -> None:
    """① 모듈별 표."""
    out("① 모듈별 결과" if results is not None else "① 모듈별 선택")
    unreferenced = [r.label for r in sel.rows if r.rule.startswith("③") and not r.files]
    if unreferenced:
        out(f"  (파일 참조 0 — 비Python 등 {len(unreferenced)}개 파일은 고른 테스트가 없다)")
        if plan:
            for label in unreferenced:
                out(f"      {label}")
    for row in sel.rows:
        if row.rule.startswith("③") and not row.files:
            continue
        if row.package:
            n = index.pkg_count.get(row.package, 0)
            line = f"  {row.label}  [{row.rule}]  정적 {n:,}건"
            if results is not None:
                line += " → " + (_tally(results.get(row.package, [])) if row.package in results
                                 else "미실행")
            out(line)
            continue
        count = sum(index.static_count(f) for f in row.files) + sum(
            index.static_count(n) for n in row.nodes)
        what = f"노드 {len(row.nodes)}" if row.nodes else f"파일 {len(row.files)}"
        line = f"  {row.label}  [{row.rule}]  {what} · 정적 {count:,}건"
        if row.note:
            line += f" ({row.note})"
        if results is not None and (row.files or row.nodes):
            line += " → " + _tally(_row_cases(row, results.get("본체", [])))
        out(line)
        if plan:
            items = sorted(row.files | row.nodes)
            for item in items[:20]:
                out(f"      {item}")
            if len(items) > 20:
                out(f"      … 외 {len(items) - 20}")
    body_files = [b for b in sel.body if "::" not in b]
    total = sum(index.static_count(b) for b in sel.body)
    nodes = len(sel.body) - len(body_files)
    line = (f"  고유 합계: 본체 파일·폴더 {len(body_files)} · 노드 {nodes}"
            f" · 정적 {total:,}건 · 패키지 {len(sel.packages)}")
    out(line)
    if results is not None:
        for group, cases in results.items():
            out(f"    {group}: {_tally(cases)}")


def print_recommend(out: Printer, reasons: list[str]) -> None:
    """④ 전체 회귀 권고 블록."""
    if not reasons:
        return
    out(f"[전체 회귀 권고] 사유: {reasons[0]}")
    for reason in reasons[1:]:
        out(f"                        {reason}")
    out("  → 사용자가 요청하면: python scripts/regress.py --full")


# ── 조립 ─────────────────────────────────────────────────────────────────────


def split_workers(body: int, apm: int, total: int = MAX_WORKERS) -> tuple[int, int]:
    """본체·apm_gateway 워커 수(0 = 직렬). 합계 total 이하, 선택 규모에 비례."""
    total = max(1, min(total, (os.cpu_count() or 2)))
    want_body, want_apm = body >= SERIAL_THRESHOLD, apm >= SERIAL_THRESHOLD
    if want_body and want_apm and total < 4:
        return total, 0
    if want_body and want_apm:
        wb = min(total - 2, max(2, round(total * body / (body + apm))))
        return wb, total - wb
    return (total if want_body else 0), (total if want_apm else 0)


def _pytest(py: str, args: list[str], junit: Path, workers: int, marker: str | None) -> list[str]:
    cmd = [py, "-m", "pytest", *args, "-q", "-p", "no:cacheprovider", "--tb=short", "-rfE",
           "--continue-on-collection-errors", "--junitxml", str(junit), "-o", "junit_family=xunit1"]
    if marker:
        cmd += ["-m", marker]
    if workers >= 2:
        cmd += ["-n", str(workers)]
    return cmd


def build_jobs(root: Path, index: Index, sel: Selection, run_dir: Path, full: bool,
               changed_py: list[str]) -> tuple[list[list[Job]], Groups, list[str]]:
    """실행 레인(본체 · 패키지 · 정적 게이트)과 귀속 메타, 계획 설명."""
    py = str(root / ".venv" / "bin" / "python") if (root / ".venv" / "bin" / "python").is_file() \
        else sys.executable
    sre_py = root / "sre_agent" / ".venv" / "bin" / "python"
    timeout = FULL_TIMEOUT if full else GROUP_TIMEOUT
    lanes: list[list[Job]] = []
    plan: list[str] = []
    groups: Groups = {
        "본체": {"subdir": "", "python": py, "imports": ["src", "noise_gate"]},
        "apm_gateway": {"subdir": "apm_gateway", "python": py, "imports": ["apm_gateway"]},
        "mcp_server": {"subdir": "mcp_server", "python": py, "imports": ["mcp_server"]},
        "sre_agent": {"subdir": "sre_agent", "python": str(sre_py), "imports": ["sre_agent"]},
    }
    body_n = sum(index.static_count(b) for b in sel.body)
    apm_n = index.pkg_count.get("apm_gateway", 0) if "apm_gateway" in sel.packages else 0
    wb, wa = split_workers(body_n, apm_n)
    if sel.body:
        serial = any("serial" in index.info[t].markers for t in index.tests
                     if any(t == b.split("::", 1)[0] or t.startswith(b.rstrip("/") + "/")
                            for b in sel.body))
        lane = [Job("본체", _pytest(py, sel.body, run_dir / "body.xml", wb,
                                    "not serial" if wb and serial else None),
                    root, run_dir / "body.log", run_dir / "body.xml", timeout=timeout)]
        mode = f"-n {wb}" if wb else f"직렬(정적 {body_n:,}건 < 임계 {SERIAL_THRESHOLD})"
        plan.append(f"  본체: 대상 {len(sel.body)} · 정적 {body_n:,}건 · {mode}")
        if wb and serial:
            lane.append(Job("본체(serial)", _pytest(py, sel.body, run_dir / "body_serial.xml", 0,
                                                     "serial"),
                            root, run_dir / "body_serial.log", run_dir / "body_serial.xml",
                            timeout=timeout))
            plan.append("  본체(serial): serial 마커만 직렬")
        lanes.append(lane)
    for pkg in sel.packages:
        meta = groups[pkg]
        if not Path(meta["python"]).is_file():
            plan.append(f"  {pkg}: 미실행(인터프리터 없음 {meta['python']})")
            continue
        workers = wa if pkg == "apm_gateway" else 0
        args = ["tests"] if pkg == "sre_agent" else []
        junit = run_dir / f"{pkg}.xml"
        lanes.append([Job(pkg, _pytest(meta["python"], args, junit, workers, None), root / pkg,
                          run_dir / f"{pkg}.log", junit, timeout=timeout)])
        plan.append(f"  {pkg}: 패키지 전체 · " + (f"-n {workers}" if workers else "직렬"))
    for script in ("arch_check", "overfit_check"):
        if (root / "scripts" / f"{script}.py").is_file():
            lanes.append([Job(script, [py, f"scripts/{script}.py", "--ci"], root,
                              run_dir / f"{script}.log", timeout=300)])
            plan.append(f"  {script} --ci")
    existing = [p for p in changed_py if (root / p).is_file()]
    ruff = _find_tool("ruff") if existing else None
    if ruff:
        lanes.append([Job("ruff", [*ruff, "check", "--output-format", "concise", "--no-fix",
                                   *existing], root, run_dir / "ruff.log", timeout=300)])
        plan.append(f"  ruff: 바꾼 .py {len(existing)}")
    targets = [p for p in existing if is_source_module(p) and p.startswith(("src/", "noise_gate/"))]
    mypy = _find_tool("mypy") if targets else None
    if mypy:
        lanes.append([Job("mypy", [*mypy, "--python-executable", py, "--follow-imports=silent",
                                   "--python-version", "3.12", "--no-color-output",
                                   "--no-error-summary", *targets],
                          root, run_dir / "mypy.log", timeout=600)])
        plan.append(f"  mypy: 바꾼 src·noise_gate 모듈 {len(targets)}")
    return lanes, groups, plan


def judge_gates(root: Path, base: str, jobs: dict[str, Job], changed_py: list[str]) -> list[Gate]:
    """③ 정적 게이트 판정 — ruff·mypy는 이번 diff 줄에 걸린 것만 신규로 센다."""
    gates: list[Gate] = []
    for name in ("arch_check", "overfit_check"):
        job = jobs.get(name)
        if job is None:
            gates.append(Gate(name, "미실행", "스크립트 없음"))
        elif job.timed_out:
            gates.append(Gate(name, "TIMEOUT"))
        else:
            gates.append(Gate(name, "통과" if job.rc == 0 else "실패", f"rc={job.rc}"))
    existing = [p for p in changed_py if (root / p).is_file()]
    lines = diff_lines(root, base, existing)
    specs = (("ruff", r"^(.+?):(\d+):\d+: ", existing),
             ("mypy", r"^(.+?):(\d+): error: ", [p for p in existing if is_source_module(p)
                                                  and p.startswith(("src/", "noise_gate/"))]))
    for name, pattern, files in specs:
        job = jobs.get(name)
        if not files:
            gates.append(Gate(name, "미실행", "대상 파일 없음"))
            continue
        if job is None:
            gates.append(Gate(name, "미실행", "도구 없음"))
            continue
        if job.timed_out:
            gates.append(Gate(name, "TIMEOUT"))
            continue
        total, new = _new_findings(job.log, pattern, lines)
        crashed = job.rc not in (0, 1)
        status = "실패" if new or crashed else "통과"
        detail = f"파일 {len(files)} · 위반 {total}(이번 diff 줄 {len(new)})"
        if crashed:
            detail += f" · 도구 오류 rc={job.rc}"
        gates.append(Gate(name, status, detail, new))
    return gates


def main(argv: list[str] | None = None) -> int:
    """진입점 — 종료 코드 0(통과·원래 실패뿐) · 1(회귀·게이트 실패·TIMEOUT) · 2(사용법)."""
    parser = argparse.ArgumentParser(description="모듈 단위 회귀 러너 (D-303 · plans/136)")
    parser.add_argument("--base", help="세션 시작 커밋(생략 시 HEAD)")
    parser.add_argument("--files", nargs="+", help="이 파일들만 변경으로 본다")
    parser.add_argument("--wide", action="store_true", help="1단계 확장")
    parser.add_argument("--plan", action="store_true", help="계획만 출력(실행 안 함)")
    parser.add_argument("--full", action="store_true", help="전 패키지 전체(사용자 요청 시만)")
    parser.add_argument("--root", default=str(REPO), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    out = Printer()
    try:
        return _main(args, root, out)
    except UsageError as exc:
        out(f"사용법 오류: {exc}")
        return 2


def _main(args: argparse.Namespace, root: Path, out: Printer) -> int:
    base = args.base or "HEAD"
    if not args.base:
        out("경고: --base 생략 — HEAD 기준이다. 병행 세션이 세션 중에 커밋했으면 변경 목록·"
            "실패 귀속 기준이 틀릴 수 있다(세션 시작 커밋을 --base로 주라).")
    if subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", "-q",
                       f"{base}^{{commit}}"], capture_output=True).returncode != 0:
        raise UsageError(f"기준 커밋을 찾지 못했다: {base}")
    changes = collect_changes(root, base, args.files)
    gone = [module_name(p) for c in changes for p in (c.path, c.old)
            if p and not (root / p).is_file() and module_name(p)]
    index = Index(root, [g for g in gone if g])
    changed_py = sorted({c.path for c in changes if c.path.endswith(".py")})
    out(f"기준 {base} · 변경 {len(changes)}" + (" · 지정 파일(--files)" if args.files else ""))
    sig: list[str] = []
    reasons: list[str] = []
    if args.full:
        roots = [r for r in BODY_TEST_ROOTS if (root / r).is_dir()]
        pkgs = [p for p in PACKAGES if (root / p / "tests").is_dir()]
        rows = [Row("본체 전체", "--full", set(roots))]
        rows += [Row(f"{p}/", "--full", package=p) for p in pkgs]
        sel = Selection(rows, roots, pkgs, set(), False, False)
    else:
        sig = signature_breaks(root, base, changes)
        sel = select(index, changes, wide=args.wide or bool(sig))
        reasons = recommend(index, changes, sel, sig)
        if sel.empty:
            out("선택된 테스트 없음" + (" — 바뀐 파일의 테스트 폴더로 대체한다"
                                       if any(r.rule.startswith("⑥") for r in sel.rows) else ""))
    if args.plan:
        print_table(out, index, sel, None, plan=True)
        _, _, plan = build_jobs(root, index, sel, root / "logs" / "regress" / "plan", args.full,
                                changed_py)
        out("실행 계획")
        for line in plan:
            out(line)
        print_recommend(out, reasons)
        out("범위: 계획만(전체) — 실행 안 함" if args.full else "범위: 계획만 — 실행 안 함")
        return 0
    run_dir = root / "logs" / "regress" / f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}"
    run_dir.mkdir(parents=True, exist_ok=True)
    lanes, groups, _ = build_jobs(root, index, sel, run_dir, args.full, changed_py)
    previous = signal.signal(signal.SIGTERM, _raise_interrupt)
    try:
        return _execute(args, root, base, out, index, sel, lanes, groups, run_dir, changed_py,
                        reasons)
    finally:
        signal.signal(signal.SIGTERM, previous)
        (run_dir / "summary.txt").write_text("\n".join(out.lines) + "\n", encoding="utf-8")


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _execute(args: argparse.Namespace, root: Path, base: str, out: Printer, index: Index,
             sel: Selection, lanes: list[list[Job]], groups: Groups, run_dir: Path,
             changed_py: list[str], reasons: list[str]) -> int:
    start = time.monotonic()
    run_lanes(lanes)
    jobs = {j.name: j for lane in lanes for j in lane}
    results: dict[str, list[Case]] = {}
    failed: dict[str, list[str]] = {}
    problems: list[str] = []
    for name, job in jobs.items():
        if job.junit is None:
            continue
        group = "본체" if name.startswith("본체") else name
        cases = parse_junit(job.junit)
        results.setdefault(group, []).extend(cases)
        if job.timed_out:
            problems.append(f"{name} TIMEOUT({job.timeout}초) — 로그 {job.log}")
        elif job.rc not in (0, 1, 5) and not cases:
            problems.append(f"{name} pytest 오류 rc={job.rc} — 로그 {job.log}")
        bad = [c.nodeid for c in cases if c.outcome in ("failed", "error")]
        if bad:
            failed.setdefault(group, []).extend(bad)
    print_table(out, index, sel, results, plan=False)
    out(f"  소요 {time.monotonic() - start:.0f}초 · 산출물 {run_dir}")
    for line in problems:
        out(f"  ! {line}")
    verdicts: list[Verdict] = []
    if failed:
        ids_file = run_dir / "failed_ids.txt"
        ids_file.write_text("".join(f"{g}\t{i}\n" for g, ids in failed.items() for i in ids),
                            encoding="utf-8")
        out(f"② 실패 귀속 (기준 {base} · 실패 ID {sum(map(len, failed.values()))}건 → {ids_file})")
        try:
            verdicts = attribute(root, base, failed, groups, run_dir)
        except AttributionError as exc:
            out(f"  ! 귀속 중단: {exc}")
            problems.append(str(exc))
        for v in sorted(verdicts, key=lambda v: (v.verdict != CAUSED, v.verdict, v.nodeid)):
            out(f"  [{v.verdict}] {v.group} {v.nodeid}" + (f" — {v.note}" if v.note else ""))
        tally = Counter(v.verdict for v in verdicts)
        out("  합계: " + " · ".join(f"{k} {tally[k]}" for k in (ORIGINAL, CAUSED, NEW_TEST, UNKNOWN)
                                    if tally[k]))
    else:
        out("② 실패 귀속: 실패 없음")
    gates = judge_gates(root, base, jobs, changed_py)
    out("③ 정적 게이트")
    for gate in gates:
        out(f"  {gate.name}: {gate.status}" + (f" — {gate.detail}" if gate.detail else ""))
        for line in gate.lines[:30]:
            out(f"      {line}")
    print_recommend(out, reasons)
    out("범위: 전체(사용자 요청)" if args.full else "범위: 모듈 단위 — 전체 미실행")
    bad_verdict = any(v.verdict in (CAUSED, NEW_TEST, UNKNOWN) for v in verdicts)
    bad_gate = any(g.status in ("실패", "TIMEOUT") for g in gates)
    return 1 if bad_verdict or bad_gate or problems else 0


if __name__ == "__main__":
    sys.exit(main())
