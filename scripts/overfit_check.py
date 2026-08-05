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
# Plan 67 R2에서 사각지대 4곳을 편입했다(`docs/polestar_bias_review.md` §5):
#   src/document      — 공용 문서 매핑인데 리터럴이 매핑 우선순위·스킵 게이트를 좌우
#   src/schema_cache  — 캐시 계층의 DB-agnostic 전제 위반 폴백 검출
#   mcp_server        — 범용 도구 경로(단 전용 모듈 polestar_tools.py는 EXCLUDE 대칭)
#   noise_gate/domain  — Clean Architecture domain 계층의 벤더 스키마 누수 검출
#                       (infrastructure는 어댑터 논리라 제외)
#   src/tools         — Plan 67 S1 fine-grained tools 계층(공용 — 어댑터 지식은 주입/레지스트리 경유만)
PUBLIC_LAYER_DIRS: tuple[str, ...] = (
    "src/utils",
    "src/nodes",
    "src/orchestration",
    "src/prompts",
    "src/document",
    "src/schema_cache",
    "noise_gate/domain",
    "mcp_server/mcp_server",
    "src/tools",
    # P5-1(Plan 69)로 semantic_compiler에서 분리된 IR 계층 — 이동 전과 동일하게 감시
    "src/semantic",
    # Plan 69 후속 2단계로 query_validator에서 분리된 SQL 검증 코어(단일 파일) —
    # 이동 전과 동일하게 감시(리터럴의 어댑터 이관은 별건 D-088 작업)
    "src/sql_validation.py",
)
# DB 어댑터·전용 도구(격리 계층)는 특화 리터럴 허용 — 스캔 제외.
EXCLUDE_DIRS: tuple[str, ...] = (
    "src/db_adapters",
    "mcp_server/mcp_server/polestar_tools.py",
)

# ──────────────────────────────────────────────
# 2. 리터럴 패턴 (카테고리별)
# ──────────────────────────────────────────────

Category = Literal["schema-literal", "routing-vocab", "ops-literal"]

# 게이트 대상 카테고리 → 기준선 JSON의 섹션 키
GATED_CATEGORIES: dict[str, str] = {
    "schema-literal": "schema_literal",
    "ops-literal": "ops_literal",
}

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

# 운영 리터럴 — 특정 고객사/운영 인스턴스의 도메인·엔드포인트 하드코딩. **게이트 대상**.
# 현행 schema/routing 패턴군으로는 `polestar.kbonecloud.com`이 routing-vocab(스코프 아웃)으로
# 분류돼 게이트되지 않았다(편향 검토 §5-5). 운영 주소는 코드가 아니라 `.env`에 둔다.
OPS_LITERAL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"kbonecloud", re.IGNORECASE), "고객사 운영 도메인"),
    (re.compile(r"sotori", re.IGNORECASE), "고객사 이벤트 도메인"),
    # 사설망 IP(운영 엔드포인트) 하드코딩. 표준 스키마 URI(OOXML 네임스페이스 등) 오탐을
    # 피하려고 일반 URL은 잡지 않고, 운영 주소로만 쓰이는 사설 대역만 대상으로 한다.
    (re.compile(r"(?<![\w.])(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\.\d{1,3}(?![\w.])"),
     "사설망 IP 하드코딩"),
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
        if base.is_file():
            files.append(base)
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

    categorized: tuple[tuple[Category, tuple[tuple[re.Pattern[str], str], ...]], ...] = (
        ("schema-literal", SCHEMA_LITERAL_PATTERNS),
        ("ops-literal", OPS_LITERAL_PATTERNS),
        ("routing-vocab", ROUTING_VOCAB_PATTERNS),
    )
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            continue
        text = tok.string
        if not text:
            continue
        line = tok.start[0]
        for category, patterns in categorized:
            for pat, reason in patterns:
                for m in pat.finditer(text):
                    key = (line, category, m.group(0))
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(Hit(rel, line, category, m.group(0), reason))
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
        return {key: {} for key in GATED_CATEGORIES.values()}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _baseline_tokens(baseline: dict, section: str) -> dict[str, set[str]]:
    """{relpath: {token_lower, ...}} 형태로 정규화."""
    out: dict[str, set[str]] = {}
    for rel, tokens in (baseline.get(section) or {}).items():
        out[rel] = {t.lower() for t in tokens}
    return out


def new_hits(result: ScanResult, baseline: dict, category: Category) -> list[Hit]:
    """기준선에 없는 해당 카테고리 히트(신규 유입)를 반환한다.

    (파일, 토큰) 단위 비교 — 라인 이동에는 둔감, 신규 토큰/신규 파일만 잡는다.
    """
    section = GATED_CATEGORIES.get(category)
    if section is None:
        return []
    allowed = _baseline_tokens(baseline, section)
    new: list[Hit] = []
    for h in result.hits:
        if h.category != category:
            continue
        if h.token.lower() in allowed.get(h.file, set()):
            continue
        new.append(h)
    return new


def new_schema_literals(result: ScanResult, baseline: dict) -> list[Hit]:
    """기준선에 없는 schema-literal 히트(신규 유입)를 반환한다."""
    return new_hits(result, baseline, "schema-literal")


def new_gated_hits(result: ScanResult, baseline: dict) -> list[Hit]:
    """게이트 대상 전 카테고리(schema-literal·ops-literal)의 신규 유입."""
    out: list[Hit] = []
    for category in GATED_CATEGORIES:
        out.extend(new_hits(result, baseline, category))  # type: ignore[arg-type]
    return out


def build_baseline(result: ScanResult) -> dict:
    baseline: dict = {
        "_note": (
            "과적합 가드 기준선(게이트 대상 잔존분). 신규 (파일,토큰) 유입은 "
            "overfit_check --ci 실패. 리터럴을 어댑터/프로필/레지스트리로 소거하며 "
            "--update-baseline으로 재생성(감소가 트랙 지표). routing-vocab(§1.3)은 "
            "스코프 아웃이라 게이트/기준선 비대상. "
            "카테고리: schema_literal(무엇이 어디에) / ops_literal(운영 도메인·엔드포인트)."
        ),
    }
    for category, section in GATED_CATEGORIES.items():
        per_file: dict[str, set[str]] = {}
        for h in result.hits:
            if h.category != category:
                continue
            per_file.setdefault(h.file, set()).add(h.token)
        baseline[section] = {f: sorted(toks) for f, toks in sorted(per_file.items())}
    return baseline


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
    ops = [h for h in result.hits if h.category == "ops-literal"]
    routing = [h for h in result.hits if h.category == "routing-vocab"]
    new = new_gated_hits(result, baseline)

    print(f"\n{c['bold']}=== 공용 계층 과적합(DB 스키마·운영 리터럴) 검사 ==={c['reset']}\n")
    print(f"  스캔 파일: {result.scanned_files}개 (공용: {', '.join(PUBLIC_LAYER_DIRS)})")
    print(f"  {c['cyan']}schema-literal (게이트 대상){c['reset']}: {len(schema)}건")
    print(f"  {c['cyan']}ops-literal (게이트 대상){c['reset']}: {len(ops)}건")
    print(f"  routing-vocab (§1.3 스코프 아웃, 가시화만): {len(routing)}건")
    base_total = sum(
        len(v)
        for section in GATED_CATEGORIES.values()
        for v in (baseline.get(section) or {}).values()
    )
    print(f"  기준선 화이트리스트 토큰: {base_total}개")
    print(f"  {c['red'] if new else c['green']}신규 유입(화이트리스트 외): {len(new)}건{c['reset']}")
    print()

    for category in ("schema-literal", "ops-literal", "routing-vocab"):
        suffix = " (스코프 아웃)" if category == "routing-vocab" else ""
        counts = _counts_by_file(result.hits, category)  # type: ignore[arg-type]
        if not counts:
            continue
        print(f"{c['bold']}--- {category} 파일별{suffix} ---{c['reset']}")
        for f, n in counts:
            print(f"  {n:>4}  {f}")
        print()

    if new:
        print(f"{c['red']}{c['bold']}--- 신규 유입 (CI 실패) ---{c['reset']}")
        for h in new:
            print(f"  {c['red']}[NEW]{c['reset']} {h.file}:{h.line}  "
                  f"{c['cyan']}{h.token}{c['reset']}  ({h.category} / {h.reason})")
        print(f"\n  → 공용 계층에 DB 특화 리터럴을 두지 말고 어댑터/프로필/레지스트리로,")
        print(f"    운영 도메인·엔드포인트는 `.env`로 옮기세요.")
        print(f"    의도된 잔존이면 `--update-baseline`으로 기준선 갱신(리뷰 필수).")
        print()
    else:
        print(f"  {c['green']}신규 유입 없음 — 기준선 준수.{c['reset']}\n")

    if verbose:
        print(f"{c['bold']}--- schema-literal 상세 ---{c['reset']}")
        for h in schema:
            print(f"  {h.file}:{h.line}  {h.token}  ({h.reason})")
        print()
        if ops:
            print(f"{c['bold']}--- ops-literal 상세 ---{c['reset']}")
            for h in ops:
                print(f"  {h.file}:{h.line}  {h.token}  ({h.reason})")
            print()


def print_json(result: ScanResult, baseline: dict) -> None:
    new = new_gated_hits(result, baseline)
    out = {
        "scanned_files": result.scanned_files,
        "schema_literal_count": len([h for h in result.hits if h.category == "schema-literal"]),
        "ops_literal_count": len([h for h in result.hits if h.category == "ops-literal"]),
        "routing_vocab_count": len([h for h in result.hits if h.category == "routing-vocab"]),
        "new_gated_hits": [
            {"file": h.file, "line": h.line, "category": h.category,
             "token": h.token, "reason": h.reason}
            for h in new
        ],
        "schema_literal_by_file": dict(_counts_by_file(result.hits, "schema-literal")),
        "ops_literal_by_file": dict(_counts_by_file(result.hits, "ops-literal")),
        "routing_vocab_by_file": dict(_counts_by_file(result.hits, "routing-vocab")),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


# ──────────────────────────────────────────────
# 6. CLI
# ──────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="공용 계층 DB 스키마·운영 리터럴(과적합) 탐지")
    parser.add_argument("--verbose", "-v", action="store_true", help="파일·라인별 상세")
    parser.add_argument("--json", action="store_true", help="JSON 출력")
    parser.add_argument("--ci", action="store_true", help="CI 모드 (게이트 대상 신규 유입 시 exit 1)")
    parser.add_argument("--update-baseline", action="store_true", help="현재 잔존분으로 기준선 재생성")
    args = parser.parse_args()

    result = scan_project()

    if args.update_baseline:
        baseline = build_baseline(result)
        BASELINE_PATH.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        parts = []
        for category, section in GATED_CATEGORIES.items():
            total = sum(len(v) for v in baseline[section].values())
            parts.append(f"{category} 토큰 {total}개({len(baseline[section])}개 파일)")
        print(f"기준선 갱신: {BASELINE_PATH.name} — " + ", ".join(parts))
        return

    baseline = load_baseline()
    if args.json:
        print_json(result, baseline)
    else:
        print_report(result, baseline, verbose=args.verbose)

    if args.ci:
        sys.exit(1 if new_gated_hits(result, baseline) else 0)


if __name__ == "__main__":
    main()
