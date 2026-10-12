"""테이블 선별 폴백 사유 코드 — `TableSelection.reason` (plans/151 W5 (2) · F11).

여기서 고정하는 것:
- 폴백 분기마다 사유 코드가 붙는다 — 호출 예외 `llm_error` · 백엔드 오류(예외·응답)
  `backend_error` · 유효한 이름 0개 `zero_valid`. LLM 선별 성공·후보 0개는 None
- `as_state()`가 `reason`을 싣는다
- 로그 문구는 그대로다(사유 문장 · 「[테이블선별] 폴백」)

LLM은 전부 가짜다(D-127 — 실 호출 없음).
"""

from __future__ import annotations

import logging

import pytest

from src.nodes import table_selection as ts
from tests.test_nodes.test_plan139_table_selection import (
    FABRIX_TEXT,
    _FakeLLM,
    _picks,
    _profile,
    _select,
)


@pytest.mark.parametrize(
    ("response", "code", "log_reason"),
    [
        (RuntimeError("boom"), ts.REASON_LLM_ERROR, "사유: LLM 호출 예외(RuntimeError: boom)"),
        (RuntimeError(FABRIX_TEXT), ts.REASON_BACKEND_ERROR, "사유: LLM 백엔드 오류("),
        (FABRIX_TEXT, ts.REASON_BACKEND_ERROR, "사유: LLM 백엔드 오류 응답("),
        (_picks("nope", "hidden"), ts.REASON_ZERO_VALID, "사유: 유효한 테이블 0개(응답 이름 2개)"),
        ("모르겠습니다", ts.REASON_ZERO_VALID, "사유: 유효한 테이블 0개(응답 이름 "),
    ],
)
async def test_fallback_reason_code_and_log_unchanged(response, code, log_reason, caplog):
    with caplog.at_level(logging.WARNING, logger="src.nodes.table_selection"):
        sel = await _select(_FakeLLM(response), question="서버 호스트명 목록")
    assert sel.source == ts.SOURCE_LEXICAL
    assert sel.reason == code
    assert sel.as_state()["reason"] == code
    assert f"[테이블선별] 폴백 — db=app_db {log_reason}" in caplog.text


async def test_lexical_zero_keeps_reason():
    sel = await _select(_FakeLLM(RuntimeError("x")), question="zzqq")
    assert (sel.source, sel.reason) == (ts.SOURCE_NONE, ts.REASON_LLM_ERROR)


async def test_llm_selection_has_no_reason():
    sel = await _select(_FakeLLM(_picks("alpha", "beta")))
    assert sel.source == ts.SOURCE_LLM
    assert sel.reason is None and sel.as_state()["reason"] is None


async def test_no_candidates_has_no_reason():
    prof = _profile()
    prof["allowed_tables"] = ["not_there"]
    sel = await _select(_FakeLLM(_picks("alpha")), profile=prof)
    assert (sel.source, sel.candidates, sel.reason) == (ts.SOURCE_NONE, 0, None)


def test_as_state_keys():
    selection = ts.TableSelection("definitions", "lexical", 3, ("a",), (), ts.REASON_ZERO_VALID)
    state = selection.as_state()
    assert state == {
        "mode": "definitions", "source": "lexical", "candidates": 3,
        "selected": ["a"], "bridged": [], "reason": "zero_valid",
    }
