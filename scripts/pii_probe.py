"""FabriX PII 필터 차단 트리거 이등분 특정 도구 (D-122 후속1).

서버측 필터 정책이 로컬 규칙(PII_RULES)보다 넓으면 로컬 스캔은 "일치 없음"만
반환한다. 이 도구는 차단 덤프(logs/pii_block/*.log) 또는 임의 텍스트 파일을
FabriX에 재전송하며 **이등분 탐색**으로 차단을 유발하는 최소 라인 구간을 특정한다.

사용법 (폐쇄망 서버에서, .env의 FabriX 설정 사용):
    python scripts/pii_probe.py logs/pii_block/20260805_..._agenerate.log
    python scripts/pii_probe.py <텍스트파일> --max-calls 30 --delay 0.5

동작:
    1. 파일 전체(덤프 파일이면 프롬프트 구간만)를 전송해 차단 재현 확인
    2. 차단되면 라인 이등분 반복 — 절반 단독 차단 시 창 축소, 양쪽 모두 통과면
       조합 의존으로 판단하고 현재 창 보고
    3. 최종 창이 1라인이면 문자 단위 이등분(최소 40자)까지 축소
    4. 최소 차단 구간 원문 + 로컬 규칙 스캔 결과 출력 → PII_RULES 갱신 재료

주의: FabriX 호출이 발생한다(기본 최대 30회, --max-calls로 조정). DB 접근 없음.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402

from src.config import load_config  # noqa: E402
from src.llm import create_llm  # noqa: E402
from src.security.pii_filter import (  # noqa: E402
    is_filter_blocked,
    scan_account_suspects,
    scan_pii,
)

_PROBE_SYSTEM = "전송 텍스트의 수신 확인 요청입니다. 내용과 무관하게 'OK'라고만 답하십시오."
_DUMP_PROMPT_MARKERS = ("===== 프롬프트 섹션:", "===== 프롬프트 전문 =====")


def _extract_probe_text(raw: str) -> str:
    """덤프 파일이면 프롬프트 구간만 추출한다(응답·헤더 제외). 일반 파일은 그대로."""
    if "# FabriX PII 필터 차단 덤프" not in raw:
        return raw
    lines = raw.splitlines()
    out: list[str] = []
    in_prompt = False
    for line in lines:
        if any(line.startswith(m) for m in _DUMP_PROMPT_MARKERS):
            in_prompt = True
            continue
        if line.startswith("===== 응답"):
            in_prompt = False
            continue
        if in_prompt:
            out.append(line)
    return "\n".join(out) if out else raw


class Prober:
    def __init__(self, max_calls: int, delay: float):
        config = load_config()
        self.llm = create_llm(config)
        self.max_calls = max_calls
        self.delay = delay
        self.calls = 0

    def is_blocked(self, text: str) -> bool:
        """텍스트를 프롬프트로 전송해 필터 차단 여부를 판정한다."""
        if self.calls >= self.max_calls:
            raise RuntimeError(f"호출 상한({self.max_calls}) 도달 — --max-calls로 상향 가능")
        self.calls += 1
        time.sleep(self.delay)
        messages = [SystemMessage(content=_PROBE_SYSTEM)]
        # KBGenAIChat는 System→AI→Human 순서를 요구한다(query_generator와 동형)
        if type(self.llm).__name__ == "KBGenAIChat":
            messages.append(AIMessage(content=""))
        messages.append(HumanMessage(content=text))
        try:
            response = self.llm.invoke(messages)
        except Exception as exc:
            # FILTER_INVALID 등 비-SUCCESS status는 클라이언트가 예외로 승격한다
            blocked = "FILTER" in str(exc).upper() or "status" in str(exc)
            print(f"  [call {self.calls}] 예외({'차단 판정' if blocked else '비차단 오류'}): {exc}")
            return blocked
        blocked = is_filter_blocked(raw_text=str(response.content))
        print(f"  [call {self.calls}] {len(text)}자 → {'차단' if blocked else '통과'}")
        return blocked


def bisect_lines(prober: Prober, lines: list[str]) -> tuple[list[str], bool]:
    """차단을 유지하는 최소 라인 창을 이등분 탐색으로 찾는다.

    Returns:
        (창 라인 목록, 조합_의존 여부) — 양쪽 절반이 모두 단독 통과하면 조합 의존.
    """
    lo, hi = 0, len(lines)
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if prober.is_blocked("\n".join(lines[lo:mid])):
            hi = mid
            continue
        if prober.is_blocked("\n".join(lines[mid:hi])):
            lo = mid
            continue
        print("  → 양쪽 절반 모두 단독 통과: 조합/총량 의존 차단 — ddmin 축소로 전환")
        return lines[lo:hi], True
    return lines[lo:hi], False


def ddmin_lines(prober: Prober, lines: list[str]) -> list[str]:
    """조합 의존 차단의 최소 라인 부분집합을 델타 디버깅(ddmin)으로 축소한다.

    단일 구간이 아니라 **여러 라인의 조합**(예: 자릿수 창을 함께 채우는 두 값)이
    차단을 유발할 때, 연속 청크를 하나씩 제거해 보며 차단이 유지되는 최소 집합으로
    줄인다. 전제: 입력 전체는 차단 상태. 호출 상한 도달 시 현재 집합을 반환한다.
    """
    cur = lines
    n = 2
    while len(cur) >= 2:
        chunk = max(1, (len(cur) + n - 1) // n)
        reduced = False
        for i in range(0, len(cur), chunk):
            candidate = cur[:i] + cur[i + chunk:]
            if candidate and prober.is_blocked("\n".join(candidate)):
                cur = candidate          # 제거해도 차단 유지 → 그 청크는 불필요
                n = max(n - 1, 2)
                reduced = True
                break
        if not reduced:
            if n >= len(cur):
                break                    # 더 못 줄임 — 현재가 (근사) 최소 집합
            n = min(n * 2, len(cur))     # 입도 세분화
    return cur


def bisect_chars(prober: Prober, line: str, min_len: int = 40) -> str:
    """단일 라인 안에서 차단 유지 최소 구간을 문자 이등분으로 좁힌다."""
    seg = line
    while len(seg) > min_len:
        mid = len(seg) // 2
        if prober.is_blocked(seg[:mid]):
            seg = seg[:mid]
            continue
        if prober.is_blocked(seg[mid:]):
            seg = seg[mid:]
            continue
        break
    return seg


def main() -> int:
    parser = argparse.ArgumentParser(description="FabriX PII 필터 차단 트리거 이등분 특정")
    parser.add_argument("file", help="차단 덤프(logs/pii_block/*.log) 또는 텍스트 파일")
    parser.add_argument("--max-calls", type=int, default=30, help="FabriX 호출 상한(기본 30)")
    parser.add_argument("--delay", type=float, default=0.5, help="호출 간 대기 초(기본 0.5)")
    parser.add_argument(
        "--scan-only", action="store_true",
        help="FabriX 호출 없이 로컬 규칙(9종)+계좌 의심 형태(날짜·타임스탬프)로만 스캔",
    )
    args = parser.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8", errors="replace")
    text = _extract_probe_text(raw)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        print("프롬프트 구간이 비어 있습니다.")
        return 1
    print(f"대상: {args.file} — {len(lines)}라인 {len(text)}자")

    if args.scan_only:
        strict = scan_pii(text, max_per_rule=10, unmask=True)
        suspects = scan_account_suspects(text, max_hits=20, unmask=True)
        print(f"\n[정식 규칙 일치: {len(strict)}건]")
        for m in strict:
            print(f"  {m.name}(룰{m.rule_id}) — {m.context}")
        print(f"\n[계좌(851) 광폭 매칭 의심 — 숫자 많은 라인의 날짜·타임스탬프: {len(suspects)}건]")
        for m in suspects:
            print(f"  {m.context}")
        if suspects and not strict:
            print(
                "\n→ 의심 형태만 검출: 서버 계좌 룰의 날짜형 광폭 매칭 가능성 높음."
                "\n   확정하려면 --scan-only 없이 이등분 재현, 또는 의심 라인 하나만 담은"
                "\n   파일로 이 스크립트를 1회 실행(호출 1회로 판정)."
                "\n   확정 시 .env에 SECURITY_PII_SCRUB_SUSPECT_DATES=true (재배포 불요)."
            )
        elif not strict and not suspects:
            print("\n→ 로컬 규칙·의심 형태 모두 무매칭 — 이등분 재현으로 특정 필요.")
        return 0

    prober = Prober(args.max_calls, args.delay)
    window = lines
    combo = False
    try:
        if not prober.is_blocked("\n".join(lines)):
            print("\n결과: 전체 텍스트가 차단되지 않음 — 재현 실패."
                  " (정책 변동/문맥 의존 가능성. 원 요청과 system 프롬프트 차이도 참고)")
            return 1
        window, combo = bisect_lines(prober, lines)
        if combo:
            # 여러 라인의 조합(자릿수 창 등)이 차단을 만들 때 — 최소 부분집합 축소
            window = ddmin_lines(prober, window)
        segment = (
            bisect_chars(prober, window[0]) if len(window) == 1 else "\n".join(window)
        )
    except RuntimeError as exc:
        print(f"\n중단: {exc} — 지금까지의 축소 결과({len(window)}라인)를 보고합니다.")
        segment = "\n".join(window)

    print("\n" + "=" * 60)
    header = "최소 차단 구간"
    if combo and len(window) > 1:
        header += f" (조합 의존 — {len(window)}개 라인이 함께 있어야 차단)"
    print(f"{header}:")
    print("-" * 60)
    print(segment)
    print("-" * 60)
    matches = scan_pii(segment, unmask=True)
    if matches:
        for m in matches:
            print(f"로컬 규칙 일치: {m.name}(룰{m.rule_id}) — {m.context}")
    else:
        print("로컬 규칙(9종) 무매칭 — 위 구간을 근거로 PII_RULES·docs/pii_filtering_rules.md에"
              " 신규 유형을 추가하거나, 해당 재료(샘플/유사어)의 주입 지점을 스크럽 대상에 포함할 것")
    print(f"\n총 FabriX 호출: {prober.calls}회")
    return 0


if __name__ == "__main__":
    sys.exit(main())
