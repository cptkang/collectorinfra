"""임의 정규식을 파일에 적용해 매칭 값을 출력하는 단독 도구 (프로젝트 무관).

FabriX 개인정보 필터의 공개/최신 정규식이 실제로 어떤 값을 잡는지 로컬에서 검증한다.
표준 라이브러리만 사용 — 이 파일 하나만 복사하면 어디서든 동작한다(폐쇄망 포함).

사용법:
    python pii_regex_check.py "<정규식>" <대상파일> [<대상파일>...]
    python pii_regex_check.py --pattern-file rules.txt <대상파일>

예시:
    python pii_regex_check.py "(?<!\\d)\\d{4}([-\\s])\\d{4}\\1\\d{4}\\1\\d{4}(?!\\d)" logs/pii_block/xxx.log
    → 필터링 1 : 1234-1231-5432-4234
      필터링 2 : 1234 2342 4352 6345

옵션:
    --pattern-file F  정규식을 파일에서 읽는다(줄당 1개, # 시작 줄은 주석).
                      셸 이스케이프가 번거로운 복잡한 정규식·여러 룰 일괄 대조용.
                      "이름 ::: 정규식" 형식이면 이름이 출력에 표시된다.
    --whole           파일 전체를 한 텍스트로 매칭(기본: 라인 단위 — ^/$ 앵커 정규식 안전)
    --line-no         매칭된 라인 번호 표시
    --context N       매칭 앞뒤 N자 문맥 표시(기본 0 = 매칭 문자열만)
    --max N           패턴당 최대 출력 건수(기본 100)
    --unique          동일 매칭 문자열 중복 제거

종료 코드: 매칭 있으면 0, 없으면 1, 오류(정규식 컴파일 실패 등) 2 — grep 관례.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


def load_patterns(args) -> list[tuple[str, "re.Pattern[str]"]]:
    """(이름, 컴파일된 패턴) 목록을 만든다. 컴파일 실패는 즉시 오류로 알린다."""
    raw: list[tuple[str, str]] = []
    if args.pattern_file:
        # utf-8-sig: PowerShell Out-File 등이 남기는 BOM을 제거하고 읽는다
        for i, line in enumerate(
            Path(args.pattern_file).read_text(encoding="utf-8-sig", errors="replace").splitlines(), 1
        ):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":::" in line:
                name, _, expr = line.partition(":::")
                raw.append((name.strip() or f"패턴{i}", expr.strip()))
            else:
                raw.append((f"패턴{i}", line))
    if args.pattern:
        raw.append(("패턴", args.pattern))
    if not raw:
        print("정규식이 없습니다 — 인자 또는 --pattern-file로 지정하세요.", file=sys.stderr)
        raise SystemExit(2)

    compiled: list[tuple[str, "re.Pattern[str]"]] = []
    for name, expr in raw:
        try:
            compiled.append((name, re.compile(expr)))
        except re.error as exc:
            print(f"정규식 컴파일 실패 [{name}]: {exc}\n  {expr}", file=sys.stderr)
            raise SystemExit(2)
    return compiled


def find_matches(
    pattern: "re.Pattern[str]",
    text: str,
    *,
    whole: bool = False,
    max_hits: int = 100,
    unique: bool = False,
) -> list[tuple[str, int, int, str]]:
    """(매칭 문자열, 라인 번호, 라인 내 시작 위치, 라인 전문) 목록을 반환한다.

    기본은 라인 단위 매칭 — ^/$ 앵커나 라인 전제 정규식(구형 계좌 룰 등)이 의도대로
    동작한다. --whole이면 파일 전체를 한 텍스트로 취급한다(개행 관통 패턴용).
    """
    results: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()

    def _collect(m: "re.Match[str]", line_no: int, line: str) -> bool:
        value = m.group(0)
        if unique:
            if value in seen:
                return True
            seen.add(value)
        results.append((value, line_no, m.start(), line))
        return len(results) < max_hits

    if whole:
        # 전체 텍스트 모드 — 라인 번호는 매칭 시작 위치 기준으로 환산
        for m in pattern.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if not _collect(m, line_no, line):
                break
    else:
        for line_no, line in enumerate(text.split("\n"), 1):
            stop = False
            for m in pattern.finditer(line):
                if not _collect(m, line_no, line):
                    stop = True
                    break
            if stop:
                break
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="임의 정규식을 파일에 적용해 매칭 값을 출력 (PII 필터 정규식 검증용)",
    )
    parser.add_argument("pattern", nargs="?", help="적용할 정규식 (--pattern-file과 병용 가능)")
    parser.add_argument("files", nargs="+", help="대상 파일 (logs/pii_block/*.log 등)")
    parser.add_argument("--pattern-file", help="정규식 목록 파일 (줄당 1개, '이름 ::: 정규식' 지원)")
    parser.add_argument("--whole", action="store_true", help="파일 전체를 한 텍스트로 매칭 (기본: 라인 단위)")
    parser.add_argument("--line-no", action="store_true", help="매칭 라인 번호 표시")
    parser.add_argument("--context", type=int, default=0, metavar="N", help="매칭 앞뒤 N자 문맥 표시")
    parser.add_argument("--max", type=int, default=100, dest="max_hits", help="패턴당 최대 출력(기본 100)")
    parser.add_argument("--unique", action="store_true", help="동일 매칭 문자열 중복 제거")
    args = parser.parse_args()

    try:  # Windows 콘솔 인코딩 방어
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    patterns = load_patterns(args)
    total = 0
    for path in args.files:
        try:
            text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            print(f"파일 읽기 실패: {path} — {exc}", file=sys.stderr)
            return 2
        multi_file = len(args.files) > 1
        multi_pattern = len(patterns) > 1
        for name, pattern in patterns:
            matches = find_matches(
                pattern, text,
                whole=args.whole, max_hits=args.max_hits, unique=args.unique,
            )
            prefix_parts = []
            if multi_file:
                prefix_parts.append(path)
            if multi_pattern:
                prefix_parts.append(name)
            prefix = f"[{' | '.join(prefix_parts)}] " if prefix_parts else ""
            for i, (value, line_no, col, line) in enumerate(matches, 1):
                out = f"{prefix}필터링 {i} : {value}"
                if args.line_no:
                    out += f"  (라인 {line_no})"
                if args.context > 0:
                    pre = line[max(0, col - args.context):col]
                    post = line[col + len(value):col + len(value) + args.context]
                    out += f"  [{pre}>>{value}<<{post}]"
                print(out)
            total += len(matches)
            if not matches and (multi_pattern or multi_file):
                print(f"{prefix}매칭 없음")
    if total == 0:
        print("매칭 없음")
        return 1
    print(f"\n총 {total}건 매칭")
    return 0


if __name__ == "__main__":
    sys.exit(main())
