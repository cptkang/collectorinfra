"""코드 스멜 정량 측정 — 표준 라이브러리 ast만 쓴다(radon·lizard가 오프라인 캐시에 없을 때).

측정값은 근사치다. 순환 복잡도는 분기 노드 수(If·For·While·except·comprehension·
bool 연산·match case)로 세며 radon과 정확히 같지 않다. 판단 근거로 쓸 때 「근사 cc」라고 적는다.

사용:
    python .claude/skills/refactoring/scripts/smell_metrics.py [경로 …] \
        [--top 20] [--getattr] [--silent]

    경로를 생략하면 src noise_gate mcp_server/mcp_server apm_gateway/apm_gateway sre_agent를 잰다.
    --getattr  src/config.py 필드 기본값과 다른 getattr(cfg, "필드", 기본) 폴백을 나열한다.
               D-331 ②의 CI 린트가 생기면 그 스크립트를 정본으로 쓴다.
    --silent   본문이 pass·상수 반환·continue뿐인 `except Exception` 핸들러를 나열한다.
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from collections import Counter

DEFAULT_PATHS = [
    "src",
    "noise_gate",
    "mcp_server/mcp_server",
    "apm_gateway/apm_gateway",
    "sre_agent",
]
SKIP_DIRS = {
    "tests",
    "scripts",
    "testdata",
    ".venv",
    "__pycache__",
    "node_modules",
    "site-packages",
}
BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.IfExp,
    ast.ExceptHandler,
    ast.Assert,
    ast.comprehension,
)


def iter_py(paths: list[str]):
    for base in paths:
        if os.path.isfile(base):
            yield base
            continue
        for d, ds, fs in os.walk(base):
            ds[:] = [x for x in ds if x not in SKIP_DIRS]
            for f in fs:
                if f.endswith(".py") and not f.startswith("test_"):
                    yield os.path.join(d, f)


def approx_cc(node: ast.AST) -> int:
    c = 1
    for n in ast.walk(node):
        if isinstance(n, BRANCH_NODES) or isinstance(n, ast.match_case):
            c += 1
        elif isinstance(n, ast.BoolOp):
            c += len(n.values) - 1
    return c


def config_defaults(path: str = "src/config.py") -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    if not os.path.exists(path):
        return out
    for n in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if not isinstance(n, ast.ClassDef):
            continue
        for b in n.body:
            if not (
                isinstance(b, ast.AnnAssign)
                and isinstance(b.target, ast.Name)
                and b.value is not None
            ):
                continue
            v, d = b.value, None
            if isinstance(v, ast.Call) and getattr(v.func, "id", "") == "Field":
                d = next((ast.unparse(k.value) for k in v.keywords if k.arg == "default"), None)
                if d is None and v.args:
                    d = ast.unparse(v.args[0])
            else:
                d = ast.unparse(v)
            if d is not None:
                out.setdefault(b.target.id, set()).add(d)
    return out


def is_silent(handler: ast.ExceptHandler) -> bool:
    def quiet(b: ast.stmt) -> bool:
        if isinstance(b, (ast.Pass, ast.Continue)):
            return True
        if isinstance(b, ast.Return):
            v = b.value
            return (
                v is None
                or isinstance(v, ast.Constant)
                or (
                    isinstance(v, (ast.List, ast.Tuple, ast.Dict))
                    and not (getattr(v, "elts", None) or getattr(v, "keys", None))
                )
            )
        return False

    return all(quiet(b) for b in handler.body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=DEFAULT_PATHS)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--getattr", action="store_true")
    ap.add_argument("--silent", action="store_true")
    a = ap.parse_args()

    files, funcs, classes, fallbacks, silent = [], [], [], [], []
    defaults = config_defaults() if a.getattr else {}
    for path in iter_py(a.paths):
        text = open(path, encoding="utf-8").read()
        try:
            tree = ast.parse(text)
        except SyntaxError as e:
            print(f"PARSE FAIL {path}: {e}", file=sys.stderr)
            continue
        files.append((path, text.count("\n") + 1))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                funcs.append((path, n.lineno, n.name, n.end_lineno - n.lineno + 1, approx_cc(n)))
            elif isinstance(n, ast.ClassDef):
                methods = sum(
                    isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) for m in n.body
                )
                classes.append((path, n.lineno, n.name, n.end_lineno - n.lineno + 1, methods))
            elif (
                a.getattr
                and isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "getattr"
                and len(n.args) == 3
            ):
                key = n.args[1]
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and key.value in defaults
                ):
                    fb = ast.unparse(n.args[2])
                    if fb not in defaults[key.value]:
                        fallbacks.append(
                            (path, n.lineno, key.value, fb, sorted(defaults[key.value]))
                        )
            elif a.silent and isinstance(n, ast.ExceptHandler) and n.type is not None:
                if ast.unparse(n.type) in ("Exception", "BaseException") and is_silent(n):
                    silent.append((path, n.lineno))

    t = a.top
    print(f"== 파일 {len(files)} · 함수 {len(funcs)} · 클래스 {len(classes)}")
    print(
        f"   파일 >1000줄 {sum(f[1] > 1000 for f in files)}"
        f" · >2000줄 {sum(f[1] > 2000 for f in files)}"
    )
    print(
        f"   함수 >100줄 {sum(f[3] > 100 for f in funcs)} · >200줄 {sum(f[3] > 200 for f in funcs)}"
        f" · 근사cc>20 {sum(f[4] > 20 for f in funcs)} · >50 {sum(f[4] > 50 for f in funcs)}"
    )
    print(f"== 파일 줄 수 상위 {t}")
    for p, n in sorted(files, key=lambda x: -x[1])[:t]:
        print(f"{n:6d}  {p}")
    print(f"== 함수 근사cc 상위 {t}")
    for p, ln, nm, n, c in sorted(funcs, key=lambda x: -x[4])[:t]:
        print(f"cc{c:4d} {n:5d}줄  {p}:{ln} {nm}")
    print(f"== 함수 길이 상위 {t}")
    for p, ln, nm, n, c in sorted(funcs, key=lambda x: -x[3])[:t]:
        print(f"{n:5d}줄 cc{c:4d}  {p}:{ln} {nm}")
    print(f"== 클래스 길이 상위 {t} (줄 · 메서드)")
    for p, ln, nm, n, m in sorted(classes, key=lambda x: -x[3])[:t]:
        print(f"{n:5d}줄 {m:3d}m  {p}:{ln} {nm}")
    if a.getattr:
        print(f"== 필드 기본값과 다른 getattr 폴백 {len(fallbacks)}건")
        for p, ln, fld, fb, ds in fallbacks:
            print(f"{p}:{ln}  {fld}  폴백={fb}  필드={ds}")
    if a.silent:
        print(f"== 침묵형 except Exception {len(silent)}건 — 파일별 상위")
        for p, n in Counter(p for p, _ in silent).most_common(t):
            print(f"{n:4d}  {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
