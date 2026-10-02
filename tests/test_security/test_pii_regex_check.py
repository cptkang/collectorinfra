"""pii_regex_check(프로젝트 무관 정규식 검증 도구) 단위 테스트.

표준 라이브러리만 쓰는 단독 스크립트 — find_matches의 라인/전체 모드·상한·중복
제거만 검증한다(콘솔 출력·인자 파싱은 수동 검증).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "pii_regex_check",
    Path(__file__).resolve().parents[2] / "scripts" / "pii_regex_check.py",
)
prc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(prc)

_CARD = re.compile(r"(?<!\d)\d{4}([-\s])\d{4}\1\d{4}\1\d{4}(?!\d)")
_TEXT = (
    '"card": "1234-1231-5432-4234", "id": 7\n'
    "결제수단: 1234 2342 4352 6345\n"
    "no match 12-34\n"
    '"dup": "1234-1231-5432-4234"\n'
)


def test_line_mode_finds_matches_with_line_numbers():
    matches = prc.find_matches(_CARD, _TEXT)
    values = [m[0] for m in matches]
    lines = [m[1] for m in matches]
    assert values == [
        "1234-1231-5432-4234", "1234 2342 4352 6345", "1234-1231-5432-4234",
    ]
    assert lines == [1, 2, 4]


def test_unique_dedupes_values():
    matches = prc.find_matches(_CARD, _TEXT, unique=True)
    assert [m[0] for m in matches] == [
        "1234-1231-5432-4234", "1234 2342 4352 6345",
    ]


def test_max_hits_caps_output():
    assert len(prc.find_matches(_CARD, _TEXT, max_hits=1)) == 1


def test_whole_mode_reports_correct_line_and_context_line():
    matches = prc.find_matches(_CARD, _TEXT, whole=True)
    value, line_no, col, line = matches[1]
    assert value == "1234 2342 4352 6345"
    assert line_no == 2
    assert "결제수단" in line


def test_line_anchored_pattern_works_in_line_mode():
    """^/$ 앵커 정규식(구형 계좌 룰류)이 라인 단위 모드에서 의도대로 동작한다."""
    anchored = re.compile(r"^\d{3}-\d{3}$")
    text = "123-456\nabc 123-456\n789-012\n"
    values = [m[0] for m in prc.find_matches(anchored, text)]
    assert values == ["123-456", "789-012"]
