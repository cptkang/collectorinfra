"""이 저장소용 보안 패턴 스캐너 — 표준 라이브러리 ast만 쓴다.

bandit·semgrep이 오프라인 캐시에 없을 때 쓴다. 탐지는 「검토 후보」다.
결과 하나하나를 코드로 읽고 판정한다 — 오탐을 고치려고 코드를 바꾸지 않는다.
사내망 구간의 verify=False(FabriX·폴스타 REST)는 의도된 것이다(D-332 부기).

사용:
    python .claude/skills/secure-coding/scripts/secure_scan.py [경로 …] [--only R1,R3]

    경로를 생략하면 src noise_gate mcp_server/mcp_server apm_gateway/apm_gateway
    sre_agent를 본다.

규칙(근거는 references/standards.md):
    R1 예외 문자열 응답 노출 (CWE-209)
       except 블록 안 HTTPException·JSONResponse·*error_payload 인자가 예외 변수를 참조
    R2 JWT 디코드 후 type 클레임 미검증 (RFC 8725 §3.11 · CWE-345)
       jwt.decode를 부르는 함수 본문에 "type" 비교가 없음
    R3 JWT 서명·알고리즘 검증 약화 (CWE-347)
       verify_signature False · algorithms 인자 없음
    R4 동적 SQL 실행 (CWE-89 — 검증 경로를 거치는지 확인)
       execute/fetch*/exec_immediate/text 첫 인자가 f-string·+·.format·%
    R5 설정 판단용 프로세스 env 직접 조회 (CWE-1188 · 「.env는 os.environ에 없다」)
       os.getenv / os.environ.get / os.environ[...] (src/config.py 제외)
    R6 위험 API
       eval·exec·pickle.loads·yaml.load(SafeLoader 없음)·shell=True·verify=False·mktemp
"""

from __future__ import annotations

import argparse
import ast
import os
import sys

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
RESPONSE_CALLS = ("HTTPException", "JSONResponse", "PlainTextResponse")
SQL_CALLS = {"execute", "executemany", "fetch", "fetchrow", "fetchval", "exec_immediate", "text"}


def iter_py(paths):
    for base in paths:
        if os.path.isfile(base):
            yield base
            continue
        for d, ds, fs in os.walk(base):
            ds[:] = [x for x in ds if x not in SKIP_DIRS]
            for f in fs:
                if f.endswith(".py") and not f.startswith("test_"):
                    yield os.path.join(d, f)


def call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def dotted(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def refers(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def is_dynamic_str(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


def scan(path: str, tree: ast.AST, only: set[str]):
    out = []

    def add(rule, node, msg):
        if not only or rule in only:
            out.append((rule, path, getattr(node, "lineno", 0), msg))

    # R1 — 예외 변수가 응답으로
    for h in ast.walk(tree):
        if isinstance(h, ast.ExceptHandler) and h.name:
            for n in ast.walk(h):
                if isinstance(n, ast.Call):
                    nm = call_name(n)
                    if nm in RESPONSE_CALLS or nm.endswith("error_payload"):
                        args = list(n.args) + [k.value for k in n.keywords]
                        if any(refers(a, h.name) for a in args):
                            add("R1", n, f"{nm}에 예외 `{h.name}` 문자열 포함")

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decodes = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and dotted(n.func).endswith("jwt.decode")
        ]
        if decodes:
            src = ast.unparse(fn)
            if '"type"' not in src and "'type'" not in src:
                add("R2", decodes[0], f"{fn.name}: jwt.decode 뒤 type 클레임 비교 없음")
            for d in decodes:
                kws = {k.arg: k.value for k in d.keywords}
                if "algorithms" not in kws:
                    add("R3", d, "jwt.decode에 algorithms 인자 없음")
                opt = kws.get("options")
                if opt is not None and "verify_signature" in dotted(opt) and "False" in dotted(opt):
                    add("R3", d, "verify_signature=False")

    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        nm, full = call_name(n), dotted(n.func)
        if nm in SQL_CALLS and n.args and is_dynamic_str(n.args[0]):
            add("R4", n, f"{full}(동적 문자열)")
        if not path.endswith(os.path.join("src", "config.py")) and full in (
            "os.getenv",
            "os.environ.get",
            "getenv",
        ):
            key = dotted(n.args[0]) if n.args else "?"
            add("R5", n, f"{full}({key})")
        if nm in ("eval", "exec") and isinstance(n.func, ast.Name):
            add("R6", n, f"{nm}()")
        elif full in ("pickle.loads", "pickle.load", "tempfile.mktemp"):
            add("R6", n, full)
        elif full == "yaml.load" and not any(
            k.arg == "Loader" and "Safe" in dotted(k.value) for k in n.keywords
        ):
            add("R6", n, "yaml.load(SafeLoader 없음)")
        for k in n.keywords:
            if k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True:
                add("R6", n, f"{full}(shell=True)")
            if k.arg == "verify" and isinstance(k.value, ast.Constant) and k.value.value is False:
                add("R6", n, f"{full}(verify=False)")
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Subscript)
            and dotted(n.value) == "os.environ"
            and isinstance(n.ctx, ast.Load)
        ):
            if not path.endswith(os.path.join("src", "config.py")):
                add("R5", n, dotted(n))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*", default=DEFAULT_PATHS)
    ap.add_argument("--only", default="", help="쉼표로 구분한 규칙 ID (예: R1,R2)")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    found = []
    for p in iter_py(a.paths):
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except SyntaxError as e:
            print(f"PARSE FAIL {p}: {e}", file=sys.stderr)
            continue
        found.extend(scan(p, tree, only))
    for rule in sorted({f[0] for f in found}):
        rows = sorted(f for f in found if f[0] == rule)
        print(f"== {rule} · {len(rows)}건")
        for _, p, ln, msg in rows:
            print(f"{p}:{ln}  {msg}")
    print(f"== 합계 {len(found)}건 (검토 후보 — 코드로 판정할 것)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
