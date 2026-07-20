#!/usr/bin/env python3
"""공용 계층 DB 스키마 리터럴(과적합) 탐지 스크립트 (Plan 63 트랙 P4-1, D-088).

공용(DB-agnostic) 계층 — `src/utils`·`src/nodes`·`src/orchestration`·공용 `src/prompts` —
에 특정 DB(폴스타)의 테이블/컬럼/리소스타입 리터럴이 누수되면, 등록된 비폴스타 DB를
활성화하는 순간 오지시 주입·기능 무력화로 드러난다(Plan 63 §1). 이 스크립트는 그런
스키마 리터럴을 스캔해 **기준선(화이트리스트) 대비 신규 유입을 CI 실패**로 막는다.

DB 어댑터 계층(`src/db_adapters/`)은 특화 로직을 격리 보관하는 곳이라 스캔에서 제외한다.

카테고리:
  - schema-literal : "무엇이 어디에 있는가"(테이블/컬럼/리소스타입). **게이트 대상**.
  - routing-vocab  : "어느 DB로 보낼 것인가"(위치·별칭 어휘, Plan 63 §1.3). 본 계획
                     스코프 아웃 — 분리 집계·가시화만 하고 CI 게이트에 포함하지 않는다.

기준선(`scripts/overfit_baseline.json`)은 P1 시점 잔존분을 담으며, P2(어댑터 이동)·
P3(선언 전환)가 리터럴을 소거하며 재생성해 **감소**시킨다(감소량이 트랙 완료 지표).

사용법:
    python scripts/overfit_check.py                  # 전체 스캔 리포트
    python scripts/overfit_check.py --verbose        # 파일·라인별 상세
    python scripts/overfit_check.py --json           # JSON 출력
    python scripts/overfit_check.py --ci             # CI: 화이트리스트 외 schema-literal 유입 시 exit 1
    python scripts/overfit_check.py --update-baseline # 현재 잔존분으로 기준선 재생성(리뷰 대상)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = Path(__file__).resolve().parent / "overfit_baseline.json"

# ──────────────────────────────────────────────
# 1. 스캔 대상/제외
# ──────────────────────────────────────────────

# 공용(DB-agnostic) 계층. 어댑터로 이동해야 할 특화 로직이 여기 있으면 누수.
PUBLIC_LAYER_DIRS: tuple[str, ...] = (
    "src/utils",
    "src/nodes",
    "src/orchestration",
    "src/prompts",
)
# DB 어댑터(격리 계층)는 특화 리터럴 허용 — 스캔 제외.
EXCLUDE_DIRS: tuple[str, ...] = ("src/db_adapters",)

# ──────────────────────────────────────────────
# 2. 리터럴 패턴 (카테고리별)
# ──────────────────────────────────────────────

Category = Literal["schema-literal", "routing-vocab"]

# 스키마 리터럴 — 폴스타 특화 테이블/컬럼/리소스타입. 게이트 대상.
SCHEMA_LITERAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"cmm_[a-z][a-z0-9_]*"), "폴스타 테이블(cmm_ 접두)"),
    (re.compile(r"core_config_prop"), "폴스타 EAV config 테이블"),
    (re.compile(r"server\.[A-Za-z][A-Za-z0-9]*"), "폴스타 resource_type(server.*)"),
    (re.compile(r"stat_date"), "폴스타 통계 기간 컬럼"),
    (re.compile(r"stringvalue(?:_short|_long)?"), "폴스타 EAV 값 컬럼"),
    (re.compile(r"resource_conf_id"), "폴스타 EAV 조인 컬럼"),
    (re.compile(r"platform_resource_id"), "폴스타 피벗 그룹 컬럼"),
    (re.compile(r"configuration_id"), "폴스타 EAV 조인 컬럼"),
    (re.compile(r"polestar\.[a-z_]+"), "폴스타 스키마 접두사 사용"),
)

# 라우팅 어휘 — 위치·별칭(어느 DB로). 분리 집계만(게이트 제외, §1.3).
ROUTING_VOCAB_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"_LOCATION_DB_HINTS|_GENERIC_DB_TOKENS|_DB_FOREIGN_REGION_TOKENS|_DB_EXCLUDING_REGIONS"),
     "라우팅 보조표"),
    (re.compile(r"여의도|김포|은행 폴스타|공동존"), "위치/존 어휘"),
    (re.compile(r"(?<![.\w])폴스타|(?<![.\w])polestar(?!\.)"), "제품/인스턴스 별칭"),
)


@dataclass
class Hit:
    file: str          # project-root 상대 경로
    line: int
    category: Category
    token: str         # 매치된 리터럴(원형)
    reason: str


@dataclass
class ScanResult:
    hits: list[Hit] = field(default_factory=list)
    scanned_files: int = 0


# ──────────────────────────────────────────────
# 3. 스캔 (tokenize — 주석 제외)
# ──────────────────────────────────────────────

def _iter_target_files() -> list[Path]:
    files: list[Path] = []
    for d in PUBLIC_LAYER_DIRS:
        base = PROJECT_ROOT / d
        if not base.exists():
            continue
        for py in sorted(base.rglob("*.py")):
            rel = py.relative_to(PROJECT_ROOT).as_posix()
            if any(rel.startswith(ex) for ex in EXCLUDE_DIRS):
                continue
            files.append(py)
    return files


def _scan_file(path: Path) -> list[Hit]:
    """파일의 비주석 토큰(문자열 리터럴·식별자)에서 리터럴 패턴을 찾는다.

    주석(COMMENT)은 제외 — 프롬프트/SQL로 주입되는 문자열·코드 식별자만 대상으로
    삼아 설명 주석의 리터럴 언급에 패널티를 주지 않는다.
    """
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    hits: list[Hit] = []
    seen: set[tuple[int, Category, str]] = set()
    try:
        with path.open("r", encoding="utf-8") as fh:
            tokens = list(tokenize.generate_tokens(fh.readline))
    except (SyntaxError, UnicodeDecodeError, tokenize.TokenError):
        return hits

    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        text = tok.string
        if not text:
            continue
        line = tok.start[0]
        for pat, reason in SCHEMA_LITERAL_PATTERNS:
            for m in pat.finditer(text):
                key = (line, "schema-literal", m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(Hit(rel, line, "schema-literal", m.group(0), reason))
        for pat, reason in ROUTING_VOCAB_PATTERNS:
            for m in pat.finditer(text):
                key = (line, "routing-vocab", m.group(0))
                if key in seen:
                    continue
                seen.add(key)
                hits.append(Hit(rel, line, "routing-vocab", m.group(0), reason))
    return hits


def scan_project() -> ScanResult:
    result = ScanResult()
    for py in _iter_target_files():
        result.scanned_files += 1
        result.hits.extend(_scan_file(py))
    return result


# ──────────────────────────────────────────────
# 4. 기준선(화이트리스트) — schema-literal만 게이트
# ──────────────────────────────────────────────

def load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {"schema_literal": {}}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _baseline_tokens(baseline: dict) -> dict[str, set[str]]:
    """{relpath: {token_lower, ...}} 형태로 정규화."""
    out: dict[str, set[str]] = {}
    for rel, tokens in (baseline.get("schema_literal") or {}).items():
        out[rel] = {t.lower() for t in tokens}
    return out


def new_schema_literals(result: ScanResult, baseline: dict) -> list[Hit]:
    """기준선에 없는 schema-literal 히트(신규 유입)를 반환한다.

    (파일, 토큰) 단위 비교 — 라인 이동에는 둔감, 신규 토큰/신규 파일만 잡는다.
    """
    allowed = _baseline_tokens(baseline)
    new: list[Hit] = []
    for h in result.hits:
        if h.category != "schema-literal":
            continue
        if h.token.lower() in allowed.get(h.file, set()):
            continue
        new.append(h)
    return new


def build_baseline(result: ScanResult) -> dict:
    per_file: dict[str, set[str]] = {}
    for h in result.hits:
        if h.category != "schema-literal":
            continue
        per_file.setdefault(h.file, set()).add(h.token)
    return {
        "_note": (
            "Plan 63 P4-1 기준선(schema-literal 잔존분). 신규 (파일,토큰) 유입은 "
            "overfit_check --ci 실패. P2(어댑터 이동)·P3(선언 전환)가 소거하며 "
            "--update-baseline으로 재생성(감소가 트랙 완료 지표). routing-vocab(§1.3)은 "
            "본 계획 스코프 아웃이라 게이트/기준선 비대상."
        ),
        "schema_literal": {f: sorted(toks) for f, toks in sorted(per_file.items())},
    }


# ──────────────────────────────────────────────
# 5. 출력
# ──────────────────────────────────────────────

COLORS = {
    "red": "\033[91m", "yellow": "\033[93m", "green": "\033[92m",
    "cyan": "\033[96m", "bold": "\033[1m", "reset": "\033[0m",
}


def _counts_by_file(hits: list[Hit], category: Category) -> list[tuple[str, int]]:
    per: dict[str, int] = {}
    for h in hits:
        if h.category == category:
            per[h.file] = per.get(h.file, 0) + 1
    return sorted(per.items(), key=lambda kv: (-kv[1], kv[0]))


def print_report(result: ScanResult, baseline: dict, verbose: bool = False) -> None:
    c = COLORS
    schema = [h for h in result.hits if h.category == "schema-literal"]
    routing = [h for h in result.hits if h.category == "routing-vocab"]
    new = new_schema_literals(result, baseline)

    print(f"\n{c['bold']}=== 공용 계층 과적합(DB 스키마 리터럴) 검사 ==={c['reset']}\n")
    print(f"  스캔 파일: {result.scanned_files}개 (공용: {', '.join(PUBLIC_LAYER_DIRS)})")
    print(f"  {c['cyan']}schema-literal (게이트 대상){c['reset']}: {len(schema)}건")
    print(f"  routing-vocab (§1.3 스코프 아웃, 가시화만): {len(routing)}건")
    base_total = sum(len(v) for v in (baseline.get("schema_literal") or {}).values())
    print(f"  기준선 화이트리스트 토큰: {base_total}개")
    print(f"  {c['red'] if new else c['green']}신규 유입(화이트리스트 외): {len(new)}건{c['reset']}")
    print()

    print(f"{c['bold']}--- schema-literal 파일별 ---{c['reset']}")
    for f, n in _counts_by_file(result.hits, "schema-literal"):
        print(f"  {n:>4}  {f}")
    print()
    print(f"{c['bold']}--- routing-vocab 파일별 (스코프 아웃) ---{c['reset']}")
    for f, n in _counts_by_file(result.hits, "routing-vocab"):
        print(f"  {n:>4}  {f}")
    print()

    if new:
        print(f"{c['red']}{c['bold']}--- 신규 schema-literal 유입 (CI 실패) ---{c['reset']}")
        for h in new:
            print(f"  {c['red']}[NEW]{c['reset']} {h.file}:{h.line}  "
                  f"{c['cyan']}{h.token}{c['reset']}  ({h.reason})")
        print(f"\n  → 공용 계층에 DB 특화 리터럴을 두지 말고 어댑터/프로필로 이동하세요.")
        print(f"    의도된 잔존이면 `--update-baseline`으로 기준선 갱신(리뷰 필수).")
        print()
    else:
        print(f"  {c['green']}신규 schema-literal 유입 없음 — 기준선 준수.{c['reset']}\n")

    if verbose:
        print(f"{c['bold']}--- schema-literal 상세 ---{c['reset']}")
        for h in schema:
            print(f"  {h.file}:{h.line}  {h.token}  ({h.reason})")
        print()


def print_json(result: ScanResult, baseline: dict) -> None:
    new = new_schema_literals(result, baseline)
    out = {
        "scanned_files": result.scanned_files,
        "schema_literal_count": len([h for h in result.hits if h.category == "schema-literal"]),
        "routing_vocab_count": len([h for h in result.hits if h.category == "routing-vocab"]),
        "new_schema_literals": [
            {"file": h.file, "line": h.line, "token": h.token, "reason": h.reason}
            for h in new
        ],
        "schema_literal_by_file": dict(_counts_by_file(result.hits, "schema-literal")),
        "routing_vocab_by_file": dict(_counts_by_file(result.hits, "routing-vocab")),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────
# 6. CLI
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="공용 계층 DB 스키마 리터럴(과적합) 탐지")
    parser.add_argument("--verbose", "-v", action="store_true", help="파일·라인별 상세")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--ci", action="store_true", help="CI 모드 (신규 schema-literal 유입 시 exit 1)")
    parser.add_argument("--update-baseline", action="store_true", help="현재 잔존분으로 기준선 재생성")
    args = parser.parse_args()

    result = scan_project()

    if args.update_baseline:
        baseline = build_baseline(result)
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        total = sum(len(v) for v in baseline["schema_literal"].values())
        print(f"기준선 갱신: {BASELINE_PATH.name} — schema-literal 토큰 {total}개 "
              f"({len(baseline['schema_literal'])}개 파일)")
        return

    baseline = load_baseline()
    if args.json:
        print_json(result, baseline)
    else:
        print_report(result, baseline, verbose=args.verbose)

    if args.ci:
        sys.exit(1 if new_schema_literals(result, baseline) else 0)


if __name__ == "__main__":
    main()
