"""plans/187 W5b — 조사 대화 첫 턴의 `disclosures[].ref.investigation_id` 기록·보존.

D-341 ⑪ · D-300.

브라우저는 새 조사 대화의 첫 질의에만 `X-Investigation-Ref` 헤더를 싣는다. 기록기는 그 턴의
`disclosures`에 ref 항목을 덧붙여 사이드바 재열기 때 고정 카드를 복원하게 한다. 헤더가 없으면
기록은 종전과 같고, 형식이 틀린 값은 버린다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.api.thread_history import (
    INVESTIGATION_REF_HEADER,
    INVESTIGATION_REF_KIND,
    TurnRecorder,
    investigation_ref,
)


class _Repo:
    def __init__(self) -> None:
        self.turns: list[dict] = []

    async def add_turn(self, owner_key: str, turn: dict) -> None:
        self.turns.append(dict(turn))


def _request(repo: Any, headers: dict | None = None) -> Any:
    return SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(thread_repo=repo)), headers=headers or {}
    )


def _record(headers: dict | None, payload: dict) -> dict:
    repo = _Repo()
    rec = TurnRecorder(_request(repo, headers), {"sub": "u1"}, user_query="q", has_upload=False)
    asyncio.run(rec.record(payload))
    return repo.turns[0]


_APM = {"kind": "apm_job", "text": "제니퍼 조회 작업", "ref": {"apm_job_id": "job-1"}}


def test_without_header_disclosures_are_unchanged() -> None:
    assert _record(None, {"thread_id": "t", "response": "a"})["disclosures"] is None
    assert _record(None, {"thread_id": "t", "disclosures": [_APM]})["disclosures"] == [_APM]


def test_header_appends_ref_and_keeps_existing_items() -> None:
    turn = _record(
        {INVESTIGATION_REF_HEADER: "a1b2c3d4"}, {"thread_id": "t", "disclosures": [_APM]}
    )
    assert turn["disclosures"][0] == _APM  # 기존 작업 카드 ref 보존
    added = turn["disclosures"][1]
    assert added["kind"] == INVESTIGATION_REF_KIND
    assert added["ref"] == {"investigation_id": "a1b2c3d4"}
    json.dumps(turn)  # 저장소(JSONB)로 그대로 직렬화된다


def test_header_without_prior_disclosures_and_no_duplicate() -> None:
    turn = _record({INVESTIGATION_REF_HEADER: "inv-7f3a"}, {"thread_id": "t"})
    assert [d["ref"] for d in turn["disclosures"]] == [{"investigation_id": "inv-7f3a"}]
    again = _record(
        {INVESTIGATION_REF_HEADER: "inv-7f3a"},
        {"thread_id": "t", "disclosures": turn["disclosures"]},
    )
    assert len(again["disclosures"]) == 1


@pytest.mark.parametrize(
    "value", ["", "x" * 129, "a b", "<script>", "../etc", "inv/1", "한글"]
)
def test_invalid_ref_is_dropped(value: str) -> None:
    assert investigation_ref(_request(None, {INVESTIGATION_REF_HEADER: value})) is None
    assert _record({INVESTIGATION_REF_HEADER: value}, {"thread_id": "t"})["disclosures"] is None


def test_valid_ref_shapes() -> None:
    for value in ("x" * 128, "0123456789abcdef0123456789abcdef", "inv-manual.cpu:1"):
        assert investigation_ref(_request(None, {INVESTIGATION_REF_HEADER: value})) == value
