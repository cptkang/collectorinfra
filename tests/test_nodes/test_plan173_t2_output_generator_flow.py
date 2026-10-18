"""`_run_output_generator` 분기 흐름 특성화 (plans/173 W1 T2 · 스멜 N5·N7).

동작 불변 리팩터(D-337 ⑤)의 안전망 — 기대값은 「맞는 값」이 아니라 「지금 값」이다.
노트 호출 순서는 `test_plan166_w0_output_note_order.py`가 고정하므로 여기서는 그 밖의 흐름을 본다.

- 텍스트 경로: 앞 턴 0행 지시어·넓힘·표본 승계 판정과 앞머리 안내 순서, 양식 없는 파일 요청의
  텍스트 강등, 반환 dict 키 순서, 로그 문구·순서.
- 문서 경로: HITL 역질문 판정 → 요약 생성 호출 순서, 0건 채움 경고, 폼필 기억 저장 안내,
  역질문 페이로드 싣기, 반환 dict 키 순서, 로그 문구·순서.

판정 함수(`_graph_sql_outcome`·`widened_beyond_antecedent`·…)는 모듈 전역 교체로 고정 입력을
준다 — 단계 함수로 뽑은 뒤에도 같은 이름을 호출 시점에 찾는지 함께 확인된다.
"""

from __future__ import annotations

import importlib
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

og = importlib.import_module("src.nodes.output_generator")

_LOGGER = "src.nodes.output_generator"


def _messages(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == _LOGGER]


def _text_state(output_format: str = "text", rows: int = 1) -> dict[str, Any]:
    return {
        "user_query": "서버 목록",
        "organized_data": {
            "summary": "",
            "rows": [{"hostname": f"web-{i}"} for i in range(rows)],
            "column_mapping": None,
            "is_sufficient": True,
        },
        "parsed_requirements": {
            "query_targets": ["서버"],
            "output_format": output_format,
            "original_query": "서버 목록",
            "filter_conditions": [],
        },
        "conversation_context": {"empty_antecedent": {"x": 1}, "sampled_antecedent": {"y": 2}},
    }


def _patch_text_deps(
    monkeypatch: pytest.MonkeyPatch,
    *,
    outcome: tuple[list[str], int] | None,
    widened: bool,
    sampled: str | None,
    seen: list[str],
) -> None:
    async def _fake_text(app_config: Any, state: Any, **kw: Any) -> str:
        seen.append(f"text:{state['parsed_requirements']['output_format']}")
        return "본문"

    monkeypatch.setattr(og, "_generate_text_response", _fake_text)
    monkeypatch.setattr(og, "_graph_sql_outcome", lambda state: outcome)
    monkeypatch.setattr(og, "widened_beyond_antecedent", lambda a, e: widened)
    monkeypatch.setattr(og, "sampled_antecedent_outcome", lambda a, e, n: sampled)
    monkeypatch.setattr(
        og, "empty_antecedent_note", lambda a, *, widened: f"[앞턴 widened={widened}]"
    )
    monkeypatch.setattr(og, "_prepend_alarm_headline", lambda r, s, c: f"[헤드]{r}")
    monkeypatch.setattr(og, "carried_zero_row_turn", lambda a, q, s: {"zero": q})
    monkeypatch.setattr(og, "sampled_turn", lambda q, s, n: {"sampled": n} if n > 2 else None)
    monkeypatch.setattr(og, "collect_disclosures", lambda state, response: ["D"])


@pytest.mark.parametrize(
    ("case", "fmt", "outcome", "widened", "sampled", "expected"),
    [
        (
            "plain",
            "text",
            None,
            False,
            None,
            {
                "keys": [
                    "final_response", "output_file", "output_file_name", "current_node",
                    "error_message", "disclosures",
                ],
                "final_response": "[헤드]본문",
                "logs": [],
                "seen": ["text:text"],
            },
        ),
        (
            "zero_rows",
            "text",
            (["SELECT 1"], 0),
            True,
            None,
            {
                "keys": [
                    "final_response", "output_file", "output_file_name", "current_node",
                    "error_message", "disclosures", "conversation_context",
                ],
                "final_response": "[헤드]본문\n\n[앞턴 widened=False]",
                "logs": [],
                "seen": ["text:text"],
                "context": {"zero": "서버 목록"},
            },
        ),
        (
            "widened_sampled_no_template",
            "xlsx",
            (["SELECT 1"], 5),
            True,
            "[표본]",
            {
                "keys": [
                    "final_response", "output_file", "output_file_name", "current_node",
                    "error_message", "disclosures", "conversation_context",
                ],
                "final_response": (
                    "[표본]\n\n[앞턴 widened=True]\n\n"
                    + og._no_template_file_notice("xlsx", True)
                    + "\n\n[헤드]본문"
                ),
                "logs": [
                    "양식 없는 xlsx 요청 — 파일 없이 텍스트 응답으로 처리",
                    "앞 턴 0행 지시어 — 이번 조회 5행이 앞 턴 조건을 잇지 않아 맨 앞에 안내"
                    "(plans/146 W3)",
                    "표본 앞 턴 지시어 — 이번 조회 5행에 맨 앞 안내(plans/150 W1)",
                ],
                "seen": ["text:text"],
                "context": {"sampled": 5},
            },
        ),
    ],
)
async def test_text_path_flow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    fmt: str,
    outcome: tuple[list[str], int] | None,
    widened: bool,
    sampled: str | None,
    expected: dict[str, Any],
) -> None:
    """텍스트 경로: 앞머리 안내 순서·강등·키 순서·로그가 현행과 같다."""
    seen: list[str] = []
    _patch_text_deps(monkeypatch, outcome=outcome, widened=widened, sampled=sampled, seen=seen)
    state = _text_state(fmt, rows=1)
    caplog.set_level(logging.INFO, logger=_LOGGER)
    result = await og._run_output_generator(state, llm=MagicMock(), app_config=MagicMock())
    assert list(result) == expected["keys"]
    assert result["final_response"] == expected["final_response"]
    assert result["output_file"] is None and result["output_file_name"] is None
    assert result["current_node"] == "output_generator" and result["error_message"] is None
    assert result["disclosures"] == ["D"]
    assert _messages(caplog) == expected["logs"]
    assert seen == expected["seen"]
    if "context" in expected:
        ctx = result["conversation_context"]
        extra = {k: v for k, v in ctx.items() if k not in state["conversation_context"]}
        assert list(extra.values()) == [expected["context"]]
    # 원 state는 바꾸지 않는다(강등은 새 dict)
    assert state["parsed_requirements"]["output_format"] == fmt


def _doc_state(*, remember: bool, template: bool = True, rows: int = 1) -> dict[str, Any]:
    state: dict[str, Any] = {
        "user_query": "양식 채워줘(uq)",
        "organized_data": {
            "rows": [{"서버명": f"w{i}"} for i in range(rows)],
            "column_mapping": None,
            "is_sufficient": True,
            "summary": "",
        },
        "parsed_requirements": {"output_format": "xlsx", "original_query": "양식 채워줘"},
        "uploaded_file": b"xlsx",
        "file_type": "xlsx",
        "form_fill_overrides": {
            "도입일자": {"action": "column", "value": "ctime", "applied": True, "origin": "answer"},
            "용도": {"action": "literal", "value": "웹", "applied": True, "origin": "memory"},
            "구분": {"action": "column", "value": "x", "applied": False, "origin": "answer"},
        },
    }
    if template:
        state["template_structure"] = {"file_type": "xlsx", "sheets": []}
    if remember:
        state["form_fill_remember"] = True
    return state


_DOC_KEYS = [
    "final_response", "output_file", "output_file_name", "current_node", "error_message",
    "disclosures",
]


@pytest.mark.parametrize(
    ("case", "remember", "saved", "clarify", "total_filled", "expected"),
    [
        (
            "saved_no_clarify",
            True,
            "서버 목록",
            False,
            3,
            {
                "keys": _DOC_KEYS + ["pending_form_fill"],
                "final_response": (
                    "본문[턴]\n\n**[기억 저장]** '서버 목록'에 1개 항목을 기억했습니다 "
                    "(유효 7일, 사용할 때마다 연장). 다음부터 이 양식에 자동 반영됩니다."
                ),
                "calls": [
                    "hitl",
                    "text:skip=None:stats={'서버명': 3}",
                    "save:['도입일자']:양식 채워줘",
                ],
                "logs": [],
            },
        ),
        (
            "not_saved_clarify_zero_filled",
            True,
            None,
            True,
            0,
            {
                "keys": _DOC_KEYS + ["pending_form_fill", "form_fill_clarification"],
                "final_response": (
                    "조회된 데이터 1건을 Excel 양식에 매핑하지 못했습니다.\n"
                    "양식의 헤더와 DB 컬럼 간 매핑이 일치하지 않습니다.\n"
                    "매핑 보고서를 확인하고 유사어를 등록해주세요.\n\n본문[턴]"
                    "\n\n**[기억 저장 안 됨]** 저장소를 사용할 수 없거나 기억 기능이 "
                    "꺼져 있어(form_memory_ttl_days=0) 이번 답변은 이번 실행에만 "
                    "적용되었습니다."
                ),
                "calls": [
                    "hitl",
                    "text:skip=" + og._form_fill_pending_notice(2) + ":stats=None",
                    "save:['도입일자']:양식 채워줘",
                ],
                "logs": ["폼필 역질문 발행(D-151): 미해결 2건 — ['A', 'B']"],
            },
        ),
        (
            "no_remember",
            False,
            "x",
            False,
            3,
            {
                "keys": _DOC_KEYS + ["pending_form_fill"],
                "final_response": "본문[턴]",
                "calls": ["hitl", "text:skip=None:stats={'서버명': 3}"],
                "logs": [],
            },
        ),
    ],
)
async def test_document_path_flow(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: str,
    remember: bool,
    saved: str | None,
    clarify: bool,
    total_filled: int,
    expected: dict[str, Any],
) -> None:
    """문서 경로: 역질문→요약 순서·경고·기억 저장 안내·페이로드·키 순서·로그가 현행과 같다."""
    calls: list[str] = []
    pending = {"fields": ["P"]} if clarify else None
    clarification = {"fields": [{"name": "A"}, {"name": "B"}]} if clarify else None

    def _fake_hitl(state: Any, fill_stats: Any) -> tuple[Any, Any]:
        calls.append("hitl")
        return clarification, pending

    async def _fake_text(app_config: Any, state: Any, **kw: Any) -> str:
        calls.append(f"text:skip={kw.get('summary_skip_notice')}:stats={kw.get('form_fill_stats')}")
        return "본문"

    async def _fake_save(template: Any, entries: Any, query: str, cfg: Any) -> str | None:
        calls.append(f"save:{sorted(entries)}:{query}")
        return saved

    monkeypatch.setattr(og, "_build_form_fill_hitl", _fake_hitl)
    monkeypatch.setattr(og, "_generate_text_response", _fake_text)
    monkeypatch.setattr(og, "save_form_memory_entries", _fake_save)
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {
            "file_bytes": b"stub",
            "file_name": f"out.{fmt}",
            "total_filled": total_filled,
            "fill_stats": {"서버명": 3},
        },
    )
    monkeypatch.setattr(og, "_append_form_fill_notes", lambda r, s, *, fill_stats: r)
    monkeypatch.setattr(og, "_append_turn_notices", lambda r, s: f"{r}[턴]")
    monkeypatch.setattr(og, "collect_disclosures", lambda state, response: [response])
    state = _doc_state(remember=remember)
    cfg = SimpleNamespace(query=SimpleNamespace(form_memory_ttl_days=7))
    caplog.set_level(logging.INFO, logger=_LOGGER)
    result = await og._run_output_generator(state, llm=MagicMock(), app_config=cfg)  # type: ignore[arg-type]
    assert list(result) == expected["keys"]
    assert result["final_response"] == expected["final_response"]
    # 고지 수집은 기억 저장 안내를 붙이기 전 본문으로 한다
    assert result["disclosures"] == [expected["final_response"].split("\n\n**[기억 저장")[0]]
    assert result["output_file"] == b"stub" and result["output_file_name"] == "out.xlsx"
    assert result["pending_form_fill"] == pending
    if clarify:
        assert result["form_fill_clarification"] == clarification
    assert calls == expected["calls"]
    assert _messages(caplog) == expected["logs"]


async def test_document_path_without_template_skips_hitl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """양식 구조 없이 파일 생성에 성공한 턴은 역질문 판정·페이로드가 없다(현행)."""
    calls: list[str] = []

    def _fake_hitl(state: Any, fill_stats: Any) -> tuple[Any, Any]:
        calls.append("hitl")
        return None, None

    async def _fake_text(app_config: Any, state: Any, **kw: Any) -> str:
        calls.append("text")
        return "본문"

    monkeypatch.setattr(og, "_build_form_fill_hitl", _fake_hitl)
    monkeypatch.setattr(og, "_generate_text_response", _fake_text)
    monkeypatch.setattr(
        og,
        "_generate_document_file",
        lambda st, fmt: {"file_bytes": b"s", "file_name": "o.xlsx", "total_filled": 1},
    )
    monkeypatch.setattr(og, "_append_form_fill_notes", lambda r, s, *, fill_stats: r)
    monkeypatch.setattr(og, "_append_turn_notices", lambda r, s: r)
    monkeypatch.setattr(og, "collect_disclosures", lambda state, response: [])
    state = _doc_state(remember=False, template=False)
    result = await og._run_output_generator(state, llm=MagicMock(), app_config=MagicMock())
    assert list(result) == _DOC_KEYS
    assert result["final_response"] == "본문"
    assert calls == ["text"]


async def test_document_failure_and_unsupported_format(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """파일 생성 실패 폴백과 미지원 형식 반환(현행)."""

    async def _fake_text(app_config: Any, state: Any, **kw: Any) -> str:
        return "본문"

    monkeypatch.setattr(og, "_generate_text_response", _fake_text)
    monkeypatch.setattr(og, "_append_inferred_mapping_info", lambda r, s: f"{r}[매핑]")
    monkeypatch.setattr(og, "_generate_document_file", lambda st, fmt: {"reason": "사유X"})
    caplog.set_level(logging.INFO, logger=_LOGGER)
    result = await og._run_output_generator(
        _doc_state(remember=False), llm=MagicMock(), app_config=MagicMock()
    )
    assert list(result) == [
        "final_response", "output_file", "output_file_name", "current_node", "error_message",
    ]
    assert result["final_response"] == (
        "업로드하신 XLSX 양식을 채우지 못했습니다.\n사유: 사유X\n"
        "아래 원본 조회 데이터(CSV 다운로드)로 결과를 확인하실 수 있습니다.\n\n본문[매핑]"
    )
    assert _messages(caplog) == ["파일 생성 실패, 텍스트 응답으로 대체 — 사유: 사유X"]

    state = _text_state("pdf")
    result = await og._run_output_generator(state, llm=MagicMock(), app_config=MagicMock())
    assert result == {
        "final_response": "지원하지 않는 출력 형식입니다: pdf",
        "output_file": None,
        "output_file_name": None,
        "current_node": "output_generator",
        "error_message": None,
    }
