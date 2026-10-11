#!/usr/bin/env python3
"""제니퍼 소스 배포 전 대조 — 레지스트리 ↔ 게이트웨이 `.env`(plans/147 W4 · D-322 ⑥).

레지스트리 `solutions[apm].sources[]`와 게이트웨이 `JENNIFER_SOURCES`를 게이트웨이를 띄우지 않고
**파일로만** 대조한다(기동 뒤 대조는 본체 기동 로그·관리 화면 「제니퍼 소스」).

- 레지스트리: `config/db_registry.yaml`(본체 로더 `load_registry` — 스키마 검증 포함)
- 게이트웨이: `apm_gateway/.env`의 `JENNIFER_SOURCES`와 소스별 필수 키
  `JENNIFER_<ID>_API_URL`·`JENNIFER_<ID>_API_TOKEN`. **토큰·URL 값은 출력·보관하지 않는다** —
  키가 있고 값이 비지 않았는지(게이트웨이 로더의 필수 키 판정과 같다)만 본다.
- `.env` 해석은 게이트웨이 `load_dotenv`와 같다(`KEY=VALUE` · `#` 주석 줄 · 따옴표를 벗기지 않는다).
  프로세스 환경 변수가 `.env`를 덮는 경우는 보지 않는다(파일 대조).
- 게이트웨이 패키지를 import 하지 않는다(D-274 경계).

사용법:
    python scripts/apm_source_check.py
    python scripts/apm_source_check.py --gateway-env /path/apm_gateway/.env \
        --registry config/db_registry.yaml

종료 코드: 0 일치 · 1 불일치(레지스트리만·게이트웨이만·필수 키 누락·설정 방식 충돌) ·
2 파일·형식 오류.
"""

from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.routing.registry import RegistryError, SourceSpec, load_registry  # noqa: E402

DEFAULT_GATEWAY_ENV = REPO_ROOT / "apm_gateway" / ".env"
DEFAULT_REGISTRY = REPO_ROOT / "config" / "db_registry.yaml"
APM_SYSTEM = "apm"
#: 단일 설정(`JENNIFER_API_URL`)의 소스 id — 게이트웨이 `_load_sources` 규칙.
SINGLE_SOURCE_ID = "default"
_REQUIRED_SUFFIXES = ("API_URL", "API_TOKEN")


class CheckError(Exception):
    """파일·형식 오류(종료 코드 2)."""


@dataclass(frozen=True)
class GatewayEnv:
    """게이트웨이 `.env` 대조 입력 — 값은 `JENNIFER_SOURCES`만 보관하고 나머지는 «값 있음»만."""

    sources_raw: str | None
    filled: frozenset[str]   # 값이 비지 않은 키

    def has(self, key: str) -> bool:
        return key in self.filled


def read_gateway_env(path: Path) -> GatewayEnv:
    """`.env`를 읽는다 — `JENNIFER_SOURCES` 원문 외의 값은 버리고 «값 있음»만 남긴다."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise CheckError(f"게이트웨이 .env를 읽을 수 없습니다: {path} ({type(e).__name__})") from e
    sources_raw: str | None = None
    filled: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if key == "JENNIFER_SOURCES":
            sources_raw = value
        if value:
            filled.add(key)
    return GatewayEnv(sources_raw=sources_raw, filled=frozenset(filled))


def gateway_source_ids(env: GatewayEnv) -> tuple[tuple[str, ...], list[str]]:
    """(게이트웨이 소스 id, 설정 문제) — 게이트웨이 로더와 같은 규칙으로 읽는다."""
    problems: list[str] = []
    if env.sources_raw:
        try:
            parsed = json.loads(env.sources_raw)
        except json.JSONDecodeError as e:
            raise CheckError(f"JENNIFER_SOURCES가 JSON 배열이 아닙니다: {e.msg}") from e
        if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
            raise CheckError('JENNIFER_SOURCES는 문자열 JSON 배열이어야 합니다'
                             '(예: ["bank", "common"])')
        ids = tuple(x.strip() for x in parsed)
        if env.has("JENNIFER_API_URL") or env.has("JENNIFER_API_TOKEN"):
            problems.append("JENNIFER_SOURCES와 단일 설정 키(JENNIFER_API_URL·JENNIFER_API_TOKEN)를"
                            " 함께 씀 — 게이트웨이가 기동하지 않는다")
        dup = sorted({sid for sid in ids if ids.count(sid) > 1})
        if dup:
            problems.append(f"JENNIFER_SOURCES 중복 id: {dup}")
        return tuple(dict.fromkeys(ids)), problems
    if env.has("JENNIFER_API_URL"):
        return (SINGLE_SOURCE_ID,), problems
    return (), problems


def _missing_keys(sid: str, env: GatewayEnv) -> list[str]:
    if sid == SINGLE_SOURCE_ID:
        return [k for k in ("JENNIFER_API_URL", "JENNIFER_API_TOKEN") if not env.has(k)]
    prefix = f"JENNIFER_{sid.upper()}_"
    return [prefix + s for s in _REQUIRED_SUFFIXES if not env.has(prefix + s)]


@dataclass(frozen=True)
class Row:
    id: str
    label: str
    zone: str
    term_count: int
    in_registry: bool
    in_gateway: bool
    missing: tuple[str, ...]

    @property
    def verdict(self) -> str:
        if not self.in_gateway:
            return "레지스트리만(미연결 — 선택지 제외)"
        if not self.in_registry:
            return "게이트웨이만(존·단어 미상 — 선택 불가)"
        if self.missing:
            return "필수 키 누락(게이트웨이 기동 실패)"
        return "일치"

    @property
    def ok(self) -> bool:
        return self.verdict == "일치"


def compare(
    registry_sources: tuple[SourceSpec, ...], env: GatewayEnv
) -> tuple[list[Row], list[str]]:
    """대조 결과 (행 목록, 설정 문제). 행은 레지스트리 순서 → 게이트웨이에만 있는 id."""
    gw_ids, problems = gateway_source_ids(env)
    gw = set(gw_ids)
    rows = [Row(id=s.id, label=s.label, zone=s.zone, term_count=len(s.terms), in_registry=True,
                in_gateway=s.id in gw,
                missing=tuple(_missing_keys(s.id, env)) if s.id in gw else ())
            for s in registry_sources]
    known = {s.id for s in registry_sources}
    rows += [Row(id=sid, label="", zone="", term_count=0, in_registry=False, in_gateway=True,
                 missing=tuple(_missing_keys(sid, env)))
             for sid in gw_ids if sid not in known]
    return rows, problems


def _width(text: str) -> int:
    """터미널 표시 폭(한글 등 전각 2칸)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def render(rows: list[Row], problems: list[str]) -> str:
    """사람이 읽는 표."""
    head = ("소스 id", "라벨", "존", "단어 수", "레지스트리", "게이트웨이", "판정")
    body = [(r.id, r.label or "-", r.zone or "-", str(r.term_count),
             "예" if r.in_registry else "아니오", "예" if r.in_gateway else "아니오",
             r.verdict + (f" {list(r.missing)}" if r.missing else "")) for r in rows]
    widths = [max(_width(c) for c in col) for col in zip(head, *body)]
    lines = [" | ".join(_pad(c, w) for c, w in zip(head, widths)).rstrip(),
             "-+-".join("-" * w for w in widths)]
    lines += [" | ".join(_pad(c, w) for c, w in zip(row, widths)).rstrip() for row in body]
    if not rows:
        lines.append("(소스 없음 — 레지스트리 sources·JENNIFER_SOURCES 모두 비어 있음)")
    lines += [f"[문제] {p}" for p in problems]
    bad = [r for r in rows if not r.ok]
    lines.append("결과: " + ("일치" if not bad and not problems else
                             f"불일치 {len(bad)}건 · 설정 문제 {len(problems)}건"))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="제니퍼 소스 레지스트리 ↔ 게이트웨이 .env 대조(배포 전)")
    ap.add_argument("--gateway-env", type=Path, default=DEFAULT_GATEWAY_ENV,
                    help="게이트웨이 .env 경로(기본 apm_gateway/.env)")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                    help="레지스트리 경로(기본 config/db_registry.yaml)")
    args = ap.parse_args(argv)
    try:
        registry = load_registry(args.registry)
        env = read_gateway_env(args.gateway_env)
        rows, problems = compare(registry.sources_of(APM_SYSTEM), env)
    except (CheckError, RegistryError, OSError) as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2
    print(render(rows, problems))
    return 0 if all(r.ok for r in rows) and not problems else 1


if __name__ == "__main__":
    sys.exit(main())
