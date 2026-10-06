"""PII 규칙 탐색 시간 상한 · 검출 결과 비트 동일.

plans/135 v1.4 · 사용자 「지금 135에서 같이 수정」(2026-10-06 인터뷰).

`scan_pii`·`scrub_pii`는 이메일 규칙(853)이 긴 영숫자 연속열에서 시작 위치마다 끝까지 다시 훑어
제곱 시간이 걸렸다(20KB 한 줄 5.7초 실측 2026-10-06). 고친 뒤에도 **어떤 입력이든 일치 위치·범위가
원 정규식과 같아야 한다** — FabriX 필터와 맞춘 규칙이라 매칭 의미를 바꾸지 않는다.

비교 기준은 수정 전 정규식 원문(`docs/pii_filtering_rules.md` §9 이메일)을 그대로 컴파일한 것이다.
"""

from __future__ import annotations

import random
import re
import time

import pytest

from src.security import pii_filter
from src.security.pii_filter import PII_RULES, scan_pii, scrub_pii

#: 수정 전 이메일 규칙 원문(비교 기준 — 이 문자열은 바꾸지 않는다).
ORIGINAL_EMAIL = re.compile(
    r"[A-Za-z0-9]+(?:[._%+-][A-Za-z0-9]+)*(?<!\\n)@"
    r"(?!(?:kbonecloud\.com|kbfg\.com)\b)"
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*(?:\.[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)+"
)

PATHOLOGICAL = {
    "alnum_run": "a" * 20_000,
    "digit_run": "1" * 20_000,
    "dotted_run": ("a.b" * 7_000)[:20_000],
    "dashed_run": ("a-b" * 7_000)[:20_000],
    "digit_dash": ("12-34-" * 4_000)[:20_000],
    "run_then_at": "a" * 19_990 + "@x",
    "run_at_run": "a" * 9_990 + "@" + "b" * 10_000,
    "many_ats": ("a@" * 10_000),
    "underscore_chain": ("a_" * 10_000),
}


def _email_rule():
    return next(rule for rule in PII_RULES if rule.rule_id == "853")


@pytest.mark.parametrize("name", sorted(PATHOLOGICAL))
def test_scan_and_scrub_are_bounded_on_20kb_line(name: str) -> None:
    text = PATHOLOGICAL[name]
    scan_pii(text[:100])  # 적재 비용 제외
    started = time.perf_counter()
    scan_pii(text, max_per_rule=5, unmask=False)
    scrub_pii(text)
    assert time.perf_counter() - started < 0.2, name


def _spans(pattern, text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)]


_ALPHABET = "ab1Z9._%+-@\\n kbfgonecloud.com$#가"
_FRAGMENTS = ["kbfg.com", "kbonecloud.com", "\\n@", "@x.co", "a.b@c.d", "_x@", "-", ".", "@",
              "user.name+tag@mail.example.co.kr", "x@kbfg.comX", "x@kbfg.com.kr", "a@b", "  "]


def _random_texts(seed: int, count: int) -> list[str]:
    rnd = random.Random(seed)
    texts = []
    for _ in range(count):
        parts = []
        for _ in range(rnd.randint(1, 12)):
            if rnd.random() < 0.3:
                parts.append(rnd.choice(_FRAGMENTS))
            else:
                parts.append("".join(rnd.choice(_ALPHABET) for _ in range(rnd.randint(0, 8))))
        texts.append("".join(parts))
    return texts


DOC_EXAMPLES = [
    "abcd123@google.co.kr", "edd123@naver.com", "메일 a.b-c_d%e+f@sub-domain.example.com 로",
    "x@kbonecloud.com", "x@kbfg.com", "x@kbfg.comm", "abc\\n@x.com", "ab\\nxy@c.com",
    "a@b.com_x@c.com", "a@b.com.x@c.com", "user@localhost", "a..b@c.com", "-a@b.com", "a-@b.com",
]


def test_email_matches_are_bit_identical_to_original_regex() -> None:
    rule = _email_rule()
    for text in DOC_EXAMPLES + _random_texts(135, 20_000):
        assert _spans(rule.pattern, text) == _spans(ORIGINAL_EMAIL, text), repr(text)


def test_email_sub_is_bit_identical_to_original_regex() -> None:
    rule = _email_rule()
    for text in DOC_EXAMPLES + _random_texts(7, 5_000):
        mark = lambda m: f"<{m.group(0)}>"  # noqa: E731
        assert rule.pattern.sub(mark, text) == ORIGINAL_EMAIL.sub(mark, text), repr(text)


def test_scan_and_scrub_outputs_unchanged_against_original_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """함수 출력 전체(유형·가린 값·문맥 · 스크럽 문장)가 원 정규식 규칙일 때와 같다."""
    texts = DOC_EXAMPLES + _random_texts(2026, 3_000) + [
        "연락처 010-1234-5678 · 메일 hong.gildong@example.com · 카드 1234-5678-9012-3456",
        "계좌 110-123-456789 주민 900101-1234567",
    ]
    current = [(scan_pii(t, unmask=True), scrub_pii(t)) for t in texts]
    original_rules = [
        pii_filter.PiiRule(r.name, r.rule_id, ORIGINAL_EMAIL, r.whole_line) if r.rule_id == "853"
        else r
        for r in PII_RULES
    ]
    monkeypatch.setattr(pii_filter, "PII_RULES", original_rules)
    reference = [(scan_pii(t, unmask=True), scrub_pii(t)) for t in texts]
    assert current == reference


def test_rule_keeps_documented_regex_text() -> None:
    """규칙 정의(문서 대조용 정규식 문자열)는 그대로다 — 바뀐 것은 탐색 방법뿐이다."""
    assert _email_rule().pattern.pattern == ORIGINAL_EMAIL.pattern
