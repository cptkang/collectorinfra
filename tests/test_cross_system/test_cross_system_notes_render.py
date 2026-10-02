"""교차 시스템 경과 노트의 응답 렌더.

plans/102 X-7 · D-224 — `output_generator._append_cross_system_notes`.

단언하는 것:
    N1 노트가 없거나 D-203 순차 경과 노트뿐이면 응답이 바이트 동일(플래그 off 경로)
    N2 `CROSS_SYSTEM_NOTE_KINDS` 4종(소유·분류 폴백·키 브리지·소재 프로브)만 결정적으로
       덧붙는다 · 중복 제거
    N3 텍스트 응답·파일 응답 두 경로 대칭
    N4 오케스트레이션 경로(`result_aggregator`)에서 두 번 렌더되지 않는다 —
       그 경로의 입력에 노트가 없다

LLM 호출 0 — 응답 본문 생성은 대역.
"""

from __future__ import annotations

import importlib

import pytest

from src.orchestration.result_aggregator import _build_output_state
from src.utils.prior_dependency import (
    CROSS_SYSTEM_NOTE_KINDS,
    NOTE_BRIDGE,
    NOTE_DECOMPOSE,
    NOTE_GATE,
    NOTE_OWNERSHIP,
    NOTE_PROBE,
    NOTE_ROUTING_FALLBACK,
    NOTE_TRACE,
)

# 패키지 `__init__`가 동명 함수를 re-export해 모듈을 가린다 — importlib만 모듈을 준다.
og = importlib.import_module("src.nodes.output_generator")

_BODY = "본문 응답"


def _note(kind: str, detail: str, task_id: str | None = None) -> dict:
    return {"kind": kind, "task_id": task_id, "reason": "r", "detail": detail}


class TestAppendFunction:
    def test_no_notes_is_identity(self):
        assert og._append_cross_system_notes(_BODY, {}) == _BODY
        assert og._append_cross_system_notes(_BODY, {"dependency_notes": None}) == _BODY

    def test_d203_notes_are_not_rendered(self):
        """★ D-203 노트는 `result_aggregator`가 렌더한다.

        여기서 렌더하면 off 경로 응답이 바뀐다.
        """
        state = {
            "dependency_notes": [
                _note(NOTE_GATE, "선행 결과 없음"),
                _note(NOTE_TRACE, "선별 3대"),
                _note(NOTE_DECOMPOSE, "재분해"),
                {"kind": "structure_missing", "detail": "구조 정보 없음"},
            ]
        }
        assert og._append_cross_system_notes(_BODY, state) == _BODY

    def test_all_cross_system_kinds_render_in_order_with_task_prefix(self):
        state = {
            "dependency_notes": [
                _note(NOTE_OWNERSHIP, "소유 교정"),
                _note(NOTE_GATE, "순차 게이트"),
                _note(NOTE_ROUTING_FALLBACK, "분류 폴백", "t1"),
                _note(NOTE_BRIDGE, "매칭 보고", "t2"),
                _note(NOTE_PROBE, "소재 판정"),
            ]
        }
        out = og._append_cross_system_notes(_BODY, state)
        assert out == (
            f"{_BODY}\n\n**[조회 시스템 경과]**\n"
            "- 소유 교정\n- [t1] 분류 폴백\n- [t2] 매칭 보고\n- 소재 판정"
        )
        assert set(CROSS_SYSTEM_NOTE_KINDS) == {
            NOTE_OWNERSHIP,
            NOTE_ROUTING_FALLBACK,
            NOTE_BRIDGE,
            NOTE_PROBE,
        }

    def test_duplicates_and_empty_details_are_skipped(self):
        state = {
            "dependency_notes": [
                _note(NOTE_OWNERSHIP, "같은 노트"),
                _note(NOTE_OWNERSHIP, "같은 노트"),
                _note(NOTE_OWNERSHIP, ""),
                "not-a-dict",
            ]
        }
        assert og._append_cross_system_notes(_BODY, state).count("같은 노트") == 1


# ──────────────────────────────────────────────
# N3 — 텍스트·파일 두 경로
# ──────────────────────────────────────────────


def _state(output_format: str, notes: list[dict] | None) -> dict:
    return {
        "user_query": "질의",
        "organized_data": {"rows": [{"a": 1}], "summary": ""},
        "parsed_requirements": {"output_format": output_format, "original_query": "질의"},
        "query_results": [{"a": 1}],
        "dependency_notes": notes,
    }


@pytest.fixture
def stub_body(monkeypatch):
    async def _text(app_config, state, llm=None, stream_user_response=True):
        return _BODY

    monkeypatch.setattr(og, "_generate_text_response", _text)
    monkeypatch.setattr(og, "_prepend_alarm_headline", lambda response, state, app_config: response)


@pytest.mark.parametrize("output_format", ["text", "xlsx"])
async def test_both_output_paths_append_notes(monkeypatch, mock_config, stub_body, output_format):
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda state, fmt: {
            "file_bytes": b"x",
            "file_name": f"out.{fmt}",
            "total_filled": 1,
            "fill_stats": None,
        },
    )
    notes = [_note(NOTE_OWNERSHIP, "답변 영역 교정 경과")]
    out = await og._run_output_generator(
        _state(output_format, notes), llm=object(), app_config=mock_config
    )
    assert "**[조회 시스템 경과]**\n- 답변 영역 교정 경과" in out["final_response"]


@pytest.mark.parametrize("output_format", ["text", "xlsx"])
async def test_both_output_paths_unchanged_without_cross_system_notes(
    monkeypatch,
    mock_config,
    stub_body,
    output_format,
):
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda state, fmt: {
            "file_bytes": b"x",
            "file_name": f"out.{fmt}",
            "total_filled": 1,
            "fill_stats": None,
        },
    )
    without = await og._run_output_generator(
        _state(output_format, None), llm=object(), app_config=mock_config
    )
    with_d203 = await og._run_output_generator(
        _state(output_format, [_note(NOTE_GATE, "게이트")]),
        llm=object(),
        app_config=mock_config,
    )
    assert "조회 시스템 경과" not in without["final_response"]
    assert with_d203["final_response"] == without["final_response"]


# ──────────────────────────────────────────────
# N4 — 오케스트레이션 경로 이중 렌더 없음
# ──────────────────────────────────────────────


def test_orchestration_output_state_carries_no_notes():
    state = {
        "user_query": "q",
        "parsed_requirements": {},
        "dependency_notes": [_note(NOTE_OWNERSHIP, "x")],
    }
    res = {"organized_data": {}, "dependency_notes": [_note(NOTE_OWNERSHIP, "y")]}
    built = _build_output_state(state, {"sub_query": "q"}, res)
    assert "dependency_notes" not in built
