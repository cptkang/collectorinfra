"""output_generator 노트 덧붙임 호출 순서 단언 (plans/166 W0 · 스멜 N7 · U-8).

plans/166 W0 특성화 스냅샷 — 단계 2(plans/163)만 갱신 가능, 단계 3 이후 갱신 금지(갱신이
필요하면 리팩터가 아니다). 기대값은 「맞는 값」이 아니라 「지금 값」이다(D-337 ⑤).

`output_generator` 노드의 텍스트 경로와 문서(xlsx/docx) 경로가 응답 본문에 노트를 덧붙이는
`_append_*`·`append_structure_missing_note` **호출 목록과 순서**를 현행 그대로 고정한다.
문서 경로에도 텍스트 경로의 4종(`current_month_partial`·`limit_truncation`·`generator_notes`·
`condition_changes`)이 같은 순서로 붙는다(plans/184 K8 행동 변경으로 갱신 — 종전 U-8 「없음」
단언 교체).

방법(실행 기반): `sys.setprofile`로 노드 1회 구동 중의 파이썬 함수 호출을 관찰해, 아래 두 목록에
든 **노트 함수 이름**(`NOTE_FUNCTIONS`)의 호출을 기록한다. 기록 대상 호출마다 중첩 깊이 카운터를
올리고 반환 때 내리며, **깊이 0일 때만** 기록한다 — 노트 함수 안에서 다시 부른 노트 함수는 노드 본문
순서가 아니므로 세지 않는다. 목록 밖 함수는 이름이 무엇이든(`_append_*` 이름의 체인 래퍼 포함)
깊이에 관여하지 않고 투명하게 지나간다. 모듈 속성 교체(monkeypatch)가 아니라 호출 관찰이라 N7 이동
(W2 · `output_notes.py`)·체인 튜플화(W4 · 함수 참조를 튜플에 담음)·래퍼 도입 뒤에도 같은 하네스로
판정된다 — 노트 함수 이름이 바뀌면 그건 이 단언의 변경이다.

구동: 텍스트 경로는 가짜 스트리밍 LLM(`test_output_generator._make_streaming_llm` 재사용)으로,
문서 경로는 양식 파일 생성(`_generate_document_file`)만 성공 결과로 대체하고 같은 가짜 LLM으로 돈다.
노트 함수 자체는 원본 그대로 실행된다(본문 내용은 단언하지 않는다).
"""

from __future__ import annotations

import importlib
import sys
from types import FrameType
from typing import Any
from unittest.mock import MagicMock

import pytest

from src.state import create_initial_state
from tests.test_nodes.test_output_generator import _make_streaming_llm

# `src.nodes` 패키지가 같은 이름의 노드 함수를 재노출해 `import src.nodes.output_generator as og`는
# 함수를 돌려준다 — 모듈 객체는 importlib로 받는다.
og = importlib.import_module("src.nodes.output_generator")

#: 텍스트 경로 — `_run_output_generator`의 `output_format == "text"` 분기(현행 순서)
TEXT_PATH_NOTES: list[str] = [
    "_append_query_period_note",
    "_append_inferred_mapping_info",
    "_append_spike_notes",
    "_append_scope_note",
    "_append_elliptical_succession_note",
    "_append_zone_coverage_notes",
    "_append_current_month_partial_note",
    "_append_unavailable_metric_notes",
    "_append_limit_truncation_note",
    "_append_generator_notes",
    "_append_condition_changes",
    "append_structure_missing_note",
    "_append_cross_system_notes",
    "_append_turn_notices",
]

#: 문서 경로 — `output_format in ("xlsx", "docx")` 분기 · 파일 생성 성공 시(plans/184 K8 이후 순서)
DOC_PATH_NOTES: list[str] = [
    "_append_query_period_note",
    "_append_inferred_mapping_info",
    "_append_spike_notes",
    "_append_scope_note",
    "_append_elliptical_succession_note",
    "_append_zone_coverage_notes",
    "_append_current_month_partial_note",
    "_append_unavailable_metric_notes",
    "_append_limit_truncation_note",
    "_append_generator_notes",
    "_append_condition_changes",
    "append_structure_missing_note",
    "_append_cross_system_notes",
    "_append_form_fill_notes",
    "_append_turn_notices",
]

#: U-8 — 종전 텍스트 경로에만 있던 4종(plans/184 K8부터 문서 경로에도 붙는다)
TEXT_ONLY_NOTES: frozenset[str] = frozenset(
    {
        "_append_current_month_partial_note",
        "_append_limit_truncation_note",
        "_append_generator_notes",
        "_append_condition_changes",
    }
)


#: 기록 대상 노트 함수 이름 — 두 경로 목록의 합집합(이 밖의 함수는 기록·깊이 계산에서 투명)
NOTE_FUNCTIONS: frozenset[str] = frozenset(TEXT_PATH_NOTES) | frozenset(DOC_PATH_NOTES)


async def _record_note_calls(coro_factory: Any) -> list[str]:
    """코루틴 1회 구동 중 깊이 0의 노트 함수 호출 이름을 순서대로 기록한다."""
    calls: list[str] = []
    open_frames: list[FrameType] = []  # 열린 기록 대상 호출 — len()이 중첩 깊이다

    def _profile(frame: FrameType, event: str, arg: Any) -> None:
        if event == "call" and frame.f_code.co_name in NOTE_FUNCTIONS:
            if len(open_frames) == 0:
                calls.append(frame.f_code.co_name)
            open_frames.append(frame)
        elif event == "return" and open_frames and open_frames[-1] is frame:
            open_frames.pop()

    previous = sys.getprofile()
    sys.setprofile(_profile)
    try:
        await coro_factory()
    finally:
        sys.setprofile(previous)
    return calls


def _base_state(output_format: str) -> dict:
    state = create_initial_state(user_query="서버 목록")
    state["organized_data"] = {
        "summary": "1건",
        "rows": [{"hostname": "web-01"}],
        "column_mapping": None,
        "is_sufficient": True,
    }
    state["parsed_requirements"] = {
        "query_targets": ["서버"],
        "output_format": output_format,
        "original_query": "서버 목록",
        "filter_conditions": [],
    }
    return state


async def _text_path_calls() -> list[str]:
    """텍스트 경로를 1회 구동해 노트 호출 기록을 돌려준다."""
    state = _base_state("text")
    llm = _make_streaming_llm("텍스트 응답")

    async def _run() -> None:
        result = await og.output_generator(state, llm=llm, app_config=MagicMock())
        assert result["output_file"] is None

    return await _record_note_calls(_run)


async def _document_path_calls(monkeypatch: pytest.MonkeyPatch, output_format: str) -> list[str]:
    """문서 경로(파일 생성 성공)를 1회 구동해 노트 호출 기록을 돌려준다."""
    state = _base_state(output_format)
    # 양식 첨부 턴 표지 — 없으면 양식 없는 파일 요청으로 보고 텍스트 경로로 돌린다(plans/116 §10.3).
    state["file_type"] = output_format
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {
            "file_bytes": b"stub",
            "file_name": f"out.{fmt}",
            "total_filled": 1,
            "fill_stats": None,
        },
    )
    llm = _make_streaming_llm("문서 요약")

    async def _run() -> None:
        result = await og.output_generator(state, llm=llm, app_config=MagicMock())
        assert result["output_file"] == b"stub"

    return await _record_note_calls(_run)


async def test_text_path_note_order() -> None:
    """텍스트 경로의 노트 호출 목록·순서가 현행과 같다."""
    assert await _text_path_calls() == TEXT_PATH_NOTES


@pytest.mark.parametrize("output_format", ["xlsx", "docx"])
async def test_document_path_note_order(
    monkeypatch: pytest.MonkeyPatch, output_format: str
) -> None:
    """문서 경로의 노트 호출 목록·순서가 현행과 같다(4종 포함 — plans/184 K8)."""
    assert await _document_path_calls(monkeypatch, output_format) == DOC_PATH_NOTES


async def test_text_only_notes_absent_from_document_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """plans/184 K8(실행 기록 대조): 종전 텍스트 전용 4종이 문서 경로에서도 호출되고, 두 경로의
    노트는 같은 순서다(문서 경로는 `_append_form_fill_notes`가 더해질 뿐)."""
    text_calls = await _text_path_calls()
    doc_calls = await _document_path_calls(monkeypatch, "xlsx")
    assert TEXT_ONLY_NOTES <= set(text_calls)
    assert TEXT_ONLY_NOTES <= set(doc_calls)
    assert [n for n in doc_calls if n != "_append_form_fill_notes"] == text_calls
