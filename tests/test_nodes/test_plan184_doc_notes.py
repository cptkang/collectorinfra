"""문서(xlsx/docx) 응답 노트 4종 편입 단언 (plans/184 K8 · 2026-10-17 사용자 결정).

종전에는 문서 경로 노트 체인(`DOC_NOTES`)에 텍스트 경로의 `current_month_partial`·
`limit_truncation`·`generator_notes`·`condition_changes` 4종이 없어, 같은 조회라도 양식으로 받으면
당월 부분 집계·LIMIT 절단·생성기 메모·조건 변경 고지가 빠졌다(U-8). 이제 문서 답에도 싣는다.

구동은 양식 파일 생성(`_generate_document_file`)만 성공 결과로 대체하고 가짜 스트리밍 LLM으로
노드를 1회 돌린다 — 노트 함수는 원본 그대로 실행된다.
"""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from src.state import create_initial_state
from tests.test_nodes.test_output_generator import _make_streaming_llm

og = importlib.import_module("src.nodes.output_generator")

_QUERY = "이번 달 서버별 CPU 사용률"
#: 생성기 자기 고백(「생략」) 주석이 있고 파서 조건(cpu >= 80)이 WHERE에 없는 실행 SQL
_SQL = "SELECT hostname, cpu FROM t WHERE 1=1 -- cpu >= 80 조건 생략"


def _doc_state(output_format: str) -> dict:
    """4종 노트가 모두 발동하는 문서 경로 입력 state."""
    rows = [{"hostname": "web-01", "cpu": 12.5}, {"hostname": "web-02", "cpu": 30.1}]
    state = create_initial_state(user_query=_QUERY)
    state["file_type"] = output_format  # 양식 첨부 턴 표지(plans/116 §10.3)
    state["routing_intent"] = "data_query"
    state["query_results"] = rows
    state["organized_data"] = {
        "summary": "2건",
        "rows": rows,
        "column_mapping": None,
        "is_sufficient": True,
    }
    state["parsed_requirements"] = {
        "query_targets": ["서버", "CPU"],
        "output_format": output_format,
        "original_query": _QUERY,
        "filter_conditions": [{"field": "cpu", "op": ">=", "value": 80}],
    }
    state["resolved_limit"] = 2  # 행 수 = 상한 → 절단 고지
    state["query_attempts"] = [{"sql": _SQL, "success": True}]
    return state


async def _run_doc(monkeypatch: pytest.MonkeyPatch, output_format: str) -> dict:
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {
            "file_bytes": b"stub",
            "file_name": f"out.{fmt}",
            "total_filled": 2,
            "fill_stats": None,
        },
    )
    return await og.output_generator(
        _doc_state(output_format), llm=_make_streaming_llm("문서 요약"), app_config=MagicMock()
    )


def test_doc_notes_match_text_notes() -> None:
    """문서 체인은 텍스트 체인과 같은 구성·순서다(4종이 텍스트 상대 순서 자리에 들어간다)."""
    assert og.DOC_NOTES == og.TEXT_NOTES
    for fn in (
        og._append_current_month_partial_note,
        og._append_limit_truncation_note,
        og._append_generator_notes,
        og._append_condition_changes,
    ):
        assert fn in og.DOC_NOTES


@pytest.mark.parametrize("output_format", ["xlsx", "docx"])
async def test_document_response_carries_four_notes(
    monkeypatch: pytest.MonkeyPatch, output_format: str
) -> None:
    """문서 응답 본문에 당월 부분·LIMIT 절단·생성기 메모·조건 변경 고지가 실린다."""
    result = await _run_doc(monkeypatch, output_format)
    assert result["output_file"] == b"stub"
    body = result["final_response"]
    assert "진행 중인 달" in body  # current_month_partial
    assert "조회 상한(LIMIT 2)" in body  # limit_truncation
    assert "생성기가 남긴 메모" in body and "조건 생략" in body  # generator_notes
    assert "cpu >= 80" in body  # condition_changes(누락)


async def test_document_notes_noop_without_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    """발동 신호가 없으면 4종 모두 붙지 않는다(평범한 양식 응답은 종전과 같다)."""
    state = _doc_state("xlsx")
    state["user_query"] = "서버 목록"
    state["parsed_requirements"] = {
        **state["parsed_requirements"],
        "original_query": "서버 목록",
        "filter_conditions": [],
    }
    state["resolved_limit"] = None
    state["query_attempts"] = [{"sql": "SELECT hostname FROM t", "success": True}]
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {"file_bytes": b"stub", "file_name": "out.xlsx", "total_filled": 2},
    )
    result = await og.output_generator(
        state, llm=_make_streaming_llm("문서 요약"), app_config=MagicMock()
    )
    body = result["final_response"]
    for marker in ("진행 중인 달", "조회 상한", "생성기가 남긴 메모", "cpu >= 80"):
        assert marker not in body
