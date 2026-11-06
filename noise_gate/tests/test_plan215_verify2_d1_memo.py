"""plans/215 2차 독립 검증 — D1 메모 존별 읽기(`_d1_memo`)의 fail-closed 경계.

검증자 작성(제품 코드 수정 없음). 값 형식 `{by_zone:{존:{verdict, anomalous_members}}, computed_at}`
에서 알람 존 항목만 돌려주고, 존 미상·항목 없음·손상·옛 형식·Redis 실패는 None이어야 한다.
다른 존 항목은 어떤 경로로도 돌려주지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from noise_gate.application.biz_investigation_context import D1_MEMO_PREFIX, _d1_memo

_VALUE = {
    "by_zone": {
        "gongjon": {"verdict": "R2", "anomalous_members": [{"kind": "sms_server",
                                                            "member_key": "pg|1"}]},
        "bankjon": {"verdict": "R4", "anomalous_members": [{"kind": "sms_server",
                                                            "member_key": "db2|9"}]},
    },
    "computed_at": "2026-11-06T00:00:00Z",
}


class _Redis:
    def __init__(self, raw: Any = None, exc: BaseException | None = None) -> None:
        self.raw, self.exc = raw, exc
        self.keys: list[str] = []

    async def get(self, key: str) -> Any:
        self.keys.append(key)
        if self.exc is not None:
            raise self.exc
        return self.raw


def _run(redis: Any, zone: str, sid: int = 5) -> Any:
    return asyncio.run(_d1_memo(redis, sid, zone))


def test_returns_only_alarm_zone_entry_from_str_and_bytes() -> None:
    raw = json.dumps(_VALUE, ensure_ascii=False)
    for value in (raw, raw.encode("utf-8")):
        r = _Redis(value)
        got = _run(r, "gongjon")
        assert got == _VALUE["by_zone"]["gongjon"]
        assert "db2|9" not in json.dumps(got) and "R4" not in json.dumps(got)
        assert r.keys == [f"{D1_MEMO_PREFIX}5"]


def test_unknown_zone_makes_no_redis_call() -> None:
    r = _Redis(json.dumps(_VALUE))
    assert _run(r, "") is None and r.keys == []


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"",
        "not-json{",
        json.dumps([1, 2]),
        json.dumps({"verdict": "R2", "anomalous_members": []}),  # 옛 형식(존 축 없음)
        json.dumps({"by_zone": ["gongjon"]}),
        json.dumps({"by_zone": {"gongjon": "R2"}}),  # 항목이 dict가 아님
        json.dumps({"by_zone": {"bankjon": {"verdict": "R4"}}}),  # 알람 존 항목 없음
    ],
)
def test_malformed_or_missing_entry_is_none(raw: Any) -> None:
    assert _run(_Redis(raw), "gongjon") is None


def test_redis_failure_is_none() -> None:
    assert _run(_Redis(exc=ConnectionError("down")), "gongjon") is None


def test_no_redis_is_none() -> None:
    assert _run(None, "gongjon") is None
