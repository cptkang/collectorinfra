"""pii_probe 축소 알고리즘(이등분·ddmin) 단위 테스트 (D-152 후속4).

FabriX 미호출 — 가짜 판정자(fake prober)로 알고리즘 수렴만 검증한다.
조합 의존 차단(여러 라인이 함께 있어야 차단)이 이등분에서 멈추지 않고
ddmin으로 최소 부분집합까지 축소되는지가 핵심.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "pii_probe",
    Path(__file__).resolve().parents[2] / "scripts" / "pii_probe.py",
)
pii_probe = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pii_probe)


class FakeProber:
    """지정 술어로 차단을 판정하는 가짜 — 호출 수만 기록."""

    def __init__(self, predicate):
        self.predicate = predicate
        self.calls = 0

    def is_blocked(self, text: str) -> bool:
        self.calls += 1
        return self.predicate(text)


def test_bisect_converges_to_single_trigger_line():
    """단일 라인 트리거는 이등분만으로 수렴한다."""
    lines = [f"line{i}" for i in range(16)]
    lines[11] = "TRIGGER"
    prober = FakeProber(lambda t: "TRIGGER" in t)
    window, combo = pii_probe.bisect_lines(prober, lines)
    assert combo is False
    assert window == ["TRIGGER"]


def test_bisect_detects_combination_and_ddmin_reduces():
    """두 라인 조합 차단: 이등분은 조합 의존을 감지하고 ddmin이 2라인으로 축소한다."""
    lines = [f"line{i}" for i in range(12)]
    lines[2] = "PART_A"
    lines[9] = "PART_B"
    prober = FakeProber(lambda t: "PART_A" in t and "PART_B" in t)
    window, combo = pii_probe.bisect_lines(prober, lines)
    assert combo is True  # 절반씩은 각각 통과
    reduced = pii_probe.ddmin_lines(prober, window)
    assert sorted(reduced) == ["PART_A", "PART_B"]


def test_ddmin_digit_budget_style_trigger():
    """자릿수 총량형 차단(창 가설과 동형): 임계 이상 숫자를 남기는 최소 집합으로 축소."""
    lines = ["2026-07-14", "abc", "12345", "def", "678", "ghi"]

    def digit_budget_blocked(text: str) -> bool:
        return sum(c.isdigit() for c in text) >= 10  # 날짜 8자리 + 2자리 이상

    prober = FakeProber(digit_budget_blocked)
    assert prober.is_blocked("\n".join(lines))
    reduced = pii_probe.ddmin_lines(prober, lines)
    # 최소 집합도 여전히 차단을 만족하고, 원본보다 줄었다
    assert digit_budget_blocked("\n".join(reduced))
    assert len(reduced) < len(lines)
