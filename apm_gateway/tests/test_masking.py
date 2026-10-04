"""마스킹 — HTTP query 첫 값 교정 · 식별자 가림 (plans/134 §4.3 · COV E-20).

필드별 적용은 W1이다 — 이번에는 함수 계약만 고정한다(`mask_url` 결함은 지금 고친다).
"""

from __future__ import annotations

import pytest
from apm_gateway.application.masking import mask_identifier, mask_query, mask_text, mask_url


@pytest.mark.parametrize(
    ("text", "masked"),
    [
        ("a=1&b=2", "a=<v>&b=<v>"),  # COV E-20 — 종전에는 'a=1&b=<v>'(첫 값이 남았다)
        ("/order/list?user=kim&page=1", "/order/list?user=<v>&page=<v>"),
        ("/pay?card=1234-5678&i=0", "/pay?card=<v>&i=<v>"),
        ("/plain/path", "/plain/path"),
    ],
)
def test_mask_url_masks_first_pair_too(text, masked):
    assert mask_url(text) == masked


@pytest.mark.parametrize(
    ("query", "masked"),
    [
        ("a=1&b=2", "a=<v>&b=<v>"),
        ("?id=kim&token=x", "?id=<v>&token=<v>"),
        ("x=1;y=&flag&z=3", "x=<v>;y=<v>&flag&z=<v>"),
        ("only=1", "only=<v>"),
        ("", ""),
    ],
)
def test_mask_query_masks_every_value(query, masked):
    assert mask_query(query) == masked


@pytest.mark.parametrize(
    ("value", "masked"),
    [
        ("kim.cs", "k***"),
        ("abc", "a***"),
        ("ab", "***"),
        ("a", "***"),
        ("", ""),
        (None, ""),
        (1234, "1***"),
    ],
)
def test_mask_identifier(value, masked):
    assert mask_identifier(value) == masked


def test_mask_text_keeps_existing_rules():
    out = mask_text("user=kim@example.com from 192.168.10.77 phone 010-1234-5678")
    assert "kim@example.com" not in out and "10.77" not in out and "1234-5678" not in out
