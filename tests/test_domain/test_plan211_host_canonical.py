"""plans/211 W1 — 솔루션 사이 호스트 정규형(`host_names`)과 entity_key `canonical` 키워드.

벡터는 plans/211 §5 정본 표 + 팀 리드 추가 벡터를 그대로 단언한다. off(기본)는 현행 비트 동일.
"""

from __future__ import annotations

import pytest

from src.domain import entity_key as ek
from src.domain.host_names import (
    build_canonical_host_condition,
    canonical_host_key,
    host_keys_match,
    split_business_suffix,
)

N = "REPLACE(REPLACE(LOWER(TRIM(t.c)), '-', ''), '_', '')"


# ──────────────────────────────────────────────
# §5 벡터 표
# ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("WEBAP01", "webap01", True),
        ("web-ap01", "web_ap01", True),
        ("webap01.corp.local", "webap01", True),
        ("webap01(인터넷뱅킹)", "webap01", True),
        ("webap01 (인터넷뱅킹)", "WEB-AP01.corp.local", True),
        ("webap01.a.local", "webap01.b.local", False),
        ("webap01", "webap010", False),
        ("(인터넷뱅킹)", "webap01", False),
        ("(인터넷뱅킹)", "(인터넷뱅킹)", False),
        ("10.1.2.3", "10.1.2.3", True),
        ("10.1.2.3", "10.1.2.4", False),
        ("web%01", "web%01", False),
    ],
)
def test_host_keys_match_vectors(a: str, b: str, expected: bool) -> None:
    assert host_keys_match(a, b) is expected
    assert host_keys_match(b, a) is expected


def test_split_business_suffix() -> None:
    assert split_business_suffix("webap01(인터넷뱅킹)") == ("webap01", "인터넷뱅킹")
    assert split_business_suffix(" webap01 ( 인터넷뱅킹 ) ") == ("webap01", "인터넷뱅킹")
    assert split_business_suffix("webap01(인터넷뱅킹") == ("webap01", "인터넷뱅킹")
    assert split_business_suffix("webap01") == ("webap01", "")
    assert split_business_suffix("(인터넷뱅킹)") == ("", "인터넷뱅킹")
    assert split_business_suffix(None) == ("", "")
    # 전각 괄호는 처리하지 않는다(반각만)
    assert split_business_suffix("webap01（업무）") == ("webap01（업무）", "")


@pytest.mark.parametrize(
    ("value", "key"),
    [
        ("WEBAP01", "webap01"),
        ("web-ap01", "webap01"),
        ("web_ap01", "webap01"),
        ("webap01(인터넷뱅킹)", "webap01"),
        ("webap01 (인터넷뱅킹)", "webap01"),
        ("WEB-AP01.corp.local", "webap01.corp.local"),
        ("(인터넷뱅킹)", ""),
        ("web%01", ""),
        ("10.1.2.3", "10.1.2.3"),
        ("2001:DB8:0:0:0:0:0:1", "2001:db8::1"),
        # 팀 리드 추가 벡터
        ("WEBAP01.", "webap01"),
        ("web..ap01", ""),
        ("-", ""),
        ("a.-.b", ""),
        ("", ""),
        (None, ""),
        ("web ap01", ""),
        ("웹서버01", ""),
        ("o'brien", ""),
    ],
)
def test_canonical_host_key(value: str | None, key: str) -> None:
    assert canonical_host_key(value) == key


# ──────────────────────────────────────────────
# SQL 조건
# ──────────────────────────────────────────────

def test_condition_single_label_without_suffix() -> None:
    assert build_canonical_host_condition("t.c", ["Web-AP01"], business_suffix=False) == (
        f"({N} = 'webap01' OR {N} LIKE 'webap01.%')"
    )


def test_condition_single_label_with_suffix() -> None:
    assert build_canonical_host_condition("t.c", ["webap01(인터넷뱅킹)"], business_suffix=True) == (
        f"({N} = 'webap01' OR {N} LIKE 'webap01.%'"
        f" OR {N} LIKE 'webap01(%' OR {N} LIKE 'webap01 (%')"
    )


def test_condition_fqdn() -> None:
    assert build_canonical_host_condition(
        "t.c", ["WEB_AP01.corp.local"], business_suffix=False,
    ) == f"({N} = 'webap01.corp.local' OR {N} = 'webap01')"
    assert build_canonical_host_condition(
        "t.c", ["WEB_AP01.corp.local"], business_suffix=True,
    ) == (
        f"({N} = 'webap01.corp.local' OR {N} = 'webap01'"
        f" OR {N} LIKE 'webap01(%' OR {N} LIKE 'webap01 (%')"
    )


def test_condition_multiple_values_or_and_dedupe() -> None:
    cond = build_canonical_host_condition(
        "t.c", ["webap01", "WEB-AP01", "10.1.2.3", "dbsvr01", "web%01"], business_suffix=False,
    )
    assert cond == (
        f"(({N} = 'webap01' OR {N} LIKE 'webap01.%')"
        f" OR ({N} = 'dbsvr01' OR {N} LIKE 'dbsvr01.%'))"
    )


@pytest.mark.parametrize(
    "values",
    [[], ["web%01"], ["(인터넷뱅킹)"], ["10.1.2.3"], ["2001:db8::1"], ["", "-", "web..ap01"]],
)
def test_condition_none_when_no_valid_key(values: list[str]) -> None:
    assert build_canonical_host_condition("t.c", values, business_suffix=True) is None


# ──────────────────────────────────────────────
# entity_key canonical on/off
# ──────────────────────────────────────────────

@pytest.mark.parametrize("value", ["web01(업무)", "web_ap01", "webap01 (인터넷뱅킹)"])
def test_classify_value_off_is_unknown(value: str) -> None:
    assert ek.classify_value(value) == ek.KEY_UNKNOWN


@pytest.mark.parametrize(
    ("value", "key_type"),
    [
        ("web01(업무)", ek.KEY_HOSTNAME),
        ("web_ap01", ek.KEY_HOSTNAME),
        ("webap01 (인터넷뱅킹)", ek.KEY_HOSTNAME),
        ("web_ap01.corp.local(업무)", ek.KEY_FQDN),
        ("10.1.2.3(관리)", ek.KEY_IPV4),
        ("(인터넷뱅킹)", ek.KEY_UNKNOWN),
        ("x(업무)", ek.KEY_UNKNOWN),
        ("12345(업무)", ek.KEY_UNKNOWN),
        ("_web01", ek.KEY_UNKNOWN),
    ],
)
def test_classify_value_on(value: str, key_type: str) -> None:
    assert ek.classify_value(value, canonical=True) == key_type


def test_normalize_value_on_keeps_dash_underscore() -> None:
    assert ek.normalize_value("WEB-AP01(인터넷뱅킹)", canonical=True) == "web-ap01"
    assert ek.normalize_value("Web_AP01", canonical=True) == "web_ap01"
    fqdn = ek.normalize_value("WEB_AP01.Corp.Local. (업무)", canonical=True)
    assert fqdn == "web_ap01.corp.local"
    assert ek.normalize_value("10.1.2.3 (관리)", canonical=True) == "10.1.2.3"
    # off는 현행 그대로 — 꼬리가 있으면 키가 아니다
    assert ek.normalize_value("WEB-AP01(인터넷뱅킹)") == ""


@pytest.mark.parametrize(
    "value", ["webap01(인터넷뱅킹)", " web_ap01 (업무) ", "(인터넷뱅킹)", "web01", "", "a(b(c)"],
)
def test_host_part_matches_split_business_suffix(value: str) -> None:
    """entity_key의 사본 규칙이 host_names 정본과 같은 호스트 부분을 낸다."""
    assert ek._host_part(value.strip()) == split_business_suffix(value)[0]


def test_cell_paren_span_removed_on_only() -> None:
    assert ek.split_cell("webap01 (인터넷뱅킹)") == ["webap01", "(인터넷뱅킹)"]
    assert ek.split_cell("webap01 (인터넷 뱅킹), dbsvr01(계정계)", canonical=True) == [
        "webap01", "dbsvr01",
    ]
    assert ek.cell_family("webap01 (인터넷뱅킹)") is None
    assert ek.cell_family("webap01 (인터넷뱅킹)", canonical=True) == ek.FAMILY_HOSTNAME
    tokens = ek.typed_tokens("WEB_AP01 (인터넷뱅킹)", canonical=True)
    assert [(t.key_type, t.normalized) for t in tokens] == [(ek.KEY_HOSTNAME, "web_ap01")]
    assert ek.typed_tokens("WEB_AP01 (인터넷뱅킹)") == []


_ROWS = [
    {"name": "webap01(인터넷뱅킹)", "flag": "Y"},
    {"name": "dbsvr01 (계정계)", "flag": "N"},
    {"name": "web_ap02", "flag": "Y"},
    {"name": "WASAP03(대외)", "flag": "N"},
    {"name": "batch01", "flag": "Y"},
]


def test_column_classification_on_off() -> None:
    values = [r["name"] for r in _ROWS]
    assert ek.classify_column(values) is None  # 꼬리·`_` 섞인 4/5 → off는 80% 미달
    assert ek.classify_column(values, canonical=True) == ek.FAMILY_HOSTNAME


def test_detect_and_collect_on_off() -> None:
    assert ek.detect_key_columns(_ROWS) == []
    cols = ek.detect_key_columns(_ROWS, canonical=True)
    assert [(c.column, c.family, c.distinct) for c in cols] == [("name", ek.FAMILY_HOSTNAME, 5)]
    collected = ek.collect_family_values(_ROWS, "name", ek.FAMILY_HOSTNAME, canonical=True)
    assert [t.normalized for t in collected] == [
        "webap01", "dbsvr01", "web_ap02", "wasap03", "batch01",
    ]
    assert [t.normalized for t in ek.collect_family_values(
        _ROWS, "name", ek.FAMILY_HOSTNAME,
    )] == ["dbsvr01", "batch01"]  # off는 공백 분해로 `dbsvr01`만 건진다
