"""plans/187 잔여 ⑥ — 사용자 경로 2차 가림(D-325 ③)이 한 줄 안에 섞인 비밀값도 가린다.

값 전체가 비밀값 꼴이면 종전처럼 통째로 가리고(설정 표지), 아니면 그 부분만 가린다. 비밀 꼴이
없는 문장(서버명·수치·시각 · 따옴표 없는 「token: expired」)은 그대로 둔다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.alarm import _investigation_mask_fn

MASK = "***MASKED***"
_mask = _investigation_mask_fn(SimpleNamespace(security=SimpleNamespace(mask_pattern=MASK)))


def test_whole_value_still_masked_entirely() -> None:
    assert _mask("sk-abcdefghijklmnopqrstuvwx") == MASK


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("curl -H 'X-Api-Key: abc123' http://h", "abc123"),
        ('{"password": "hunter2", "host": "web01"}', "hunter2"),
        ("export TOKEN=sk-abcdefghijklmnopqrstuvwxyz && run", "sk-abcdefghij"),
        ("auth Bearer eyJhbGciOi.eyJzdWIi.sig 끝", "eyJhbGciOi"),
        ("psql postgresql://u:pw0rd@db:5432/x", "pw0rd"),
        ("key AKIAABCDEFGHIJKLMNOP end", "AKIAABCDEFGHIJKLMNOP"),
        ("주민 900101-1234567 끝", "900101-1234567"),
    ],
)
def test_inline_secret_masked_rest_kept(text: str, secret: str) -> None:
    out = _mask(text)
    assert secret not in out
    assert out.split()[0] == text.split()[0]  # 앞 낱말(명령·설명)은 남는다


@pytest.mark.parametrize(
    "text",
    [
        "WEB-01 CPU 사용률 92.5% · 2026-10-22 10:00",
        "token: expired 문장",
        "inv-3f2a9c1e-7b4d-4e8a-9f00-12ab34cd56ef 조회",
    ],
)
def test_plain_text_unchanged(text: str) -> None:
    assert _mask(text) == text
