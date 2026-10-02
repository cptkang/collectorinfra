"""역질문 자동 응답 규칙 (D-216 · scripts/scenario/clarify.py).

전부 순수 함수다 - 서버·네트워크 없이 검증한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import CatalogError, load_catalog
from scripts.scenario.clarify import (
    APPROVAL_REPLY,
    FORM_FILL_REPLY_QUERY,
    Question,
    answer_endpoint,
    build_answer,
    choose_db_ids,
    expects_question,
    complete_payload,
    load_zone_preference,
    pending_question,
)

from .conftest import write

ZONE = {
    "kind": "zone_select",
    "question": "조회할 존이 지정되지 않았습니다.",
    "original_query": "전체 서버 수 알려줘",
    "options": [
        {"db_id": "polestar_b0", "label": "은행존"},
        {"db_id": "polestar_cm_gp", "label": "공동존(김포)"},
        {"db_id": "polestar_cm_yd", "label": "공동존(여의도)"},
    ],
}
SCOPE = {
    "kind": "scope_select",
    "original_query": "전체 서버 OS",
    "options": [
        {"key": "__all__", "db_ids": ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"], "default": True},
        {"key": "bank", "db_ids": ["polestar_b0"], "default": False},
    ],
}
FORM_FILL = {"fields": [{"name": "담당자", "label": "담당자"}, {"name": "비고", "label": "비고"}],
             "candidates": [{"value": "column:hostname", "label": "호스트명"}]}


# --- 역질문 탐지 ---------------------------------------------------------

def test_존_선택과_범위_선택은_존_질문으로_본다() -> None:
    assert pending_question(Observation(status="clarification", clarification=ZONE)).kind == "zone"
    assert pending_question(Observation(status="clarification", clarification=SCOPE)).kind == "zone"


def test_폼필_역질문과_승인_대기를_구분한다() -> None:
    assert pending_question(
        Observation(status="completed", form_fill_clarification=FORM_FILL)
    ).kind == "form_fill"
    assert pending_question(Observation(status="awaiting_approval")).kind == "approval"


def test_역질문이_없거나_모르는_종류면_답하지_않는다() -> None:
    assert pending_question(Observation(status="completed")) is None
    unknown = Observation(status="clarification", clarification={"kind": "새종류"})
    assert pending_question(unknown) is None


@pytest.mark.parametrize("expect, expected", [
    ({"status": "clarification"}, True),
    ({"status": "awaiting_approval"}, True),
    ({"clarification": {"options_len": 1}}, True),
    ({"status": "completed"}, False),
    ({}, False),
])
def test_역질문을_기대하는_턴은_자동_응답하지_않는다(expect: dict, expected: bool) -> None:
    assert expects_question(expect) is expected


# --- 존 선택 -------------------------------------------------------------

def test_우선_존이_선택지에_있으면_그_존_하나만_고른다() -> None:
    assert choose_db_ids(ZONE, ["polestar_cm_gp"]) == ["polestar_cm_gp"]


def test_우선_존이_없으면_첫_선택지를_고른다() -> None:
    assert choose_db_ids(ZONE, ["없는존"]) == ["polestar_b0"]
    assert choose_db_ids(ZONE, []) == ["polestar_b0"]


def test_YAML_명시값이_우선순위를_이긴다() -> None:
    assert choose_db_ids(ZONE, ["polestar_cm_gp"], ["polestar_cm_yd"]) == ["polestar_cm_yd"]


def test_범위_선택은_묶음_선택지의_db_ids에서_고른다() -> None:
    assert choose_db_ids(SCOPE, ["polestar_cm_gp"]) == ["polestar_cm_gp"]


def test_존_답은_원문을_selected_db_ids와_함께_재전송한다() -> None:
    body = build_answer(Question("zone", ZONE), override={}, preference=["polestar_cm_gp"],
                        last_query="직전 질의")
    assert body == {"query": "전체 서버 수 알려줘", "selected_db_ids": ["polestar_cm_gp"]}


def test_원문이_없으면_직전_질의를_보낸다() -> None:
    zone = {**ZONE, "original_query": ""}
    body = build_answer(Question("zone", zone), override={}, preference=[], last_query="직전 질의")
    assert body["query"] == "직전 질의"


def test_선택지가_비면_답을_만들지_않는다() -> None:
    zone = {**ZONE, "options": []}
    assert build_answer(Question("zone", zone), override={}, preference=[], last_query="q") is None


# --- 폼필 · 승인 ---------------------------------------------------------

def test_폼필은_후보를_임의로_고르지_않고_전_필드를_공란으로_답한다() -> None:
    body = build_answer(Question("form_fill", FORM_FILL), override={}, preference=[], last_query="q")
    assert body == {
        "query": FORM_FILL_REPLY_QUERY,
        "form_fill_answers": {"담당자": {"action": "blank", "value": None},
                              "비고": {"action": "blank", "value": None}},
    }


def test_폼필_명시_답변이_있으면_그대로_보낸다() -> None:
    answers = {"담당자": {"action": "literal", "value": "인프라팀"}}
    body = build_answer(Question("form_fill", FORM_FILL), override={"form_fill_answers": answers},
                        preference=[], last_query="q")
    assert body["form_fill_answers"] == answers


def test_승인_대기는_결정적_승인_어휘로_답한다() -> None:
    body = build_answer(Question("approval", {}), override={}, preference=[], last_query="q")
    assert body == {"query": APPROVAL_REPLY}


# --- 엔드포인트 ----------------------------------------------------------

def test_파일_경로의_존_선택은_파일을_다시_올린다() -> None:
    assert answer_endpoint("file_stream", Question("zone", {**ZONE, "has_file": True})) == ("file_stream", True)


def test_폼필_답변은_JSON_경로로_보낸다() -> None:
    assert answer_endpoint("file_stream", Question("form_fill", FORM_FILL)) == ("stream", False)
    assert answer_endpoint("file", Question("form_fill", FORM_FILL)) == ("plain", False)
    assert answer_endpoint("stream", Question("zone", ZONE)) == ("stream", False)


# --- 구조화 턴 질의 채움 ------------------------------------------------

def test_존_답변_턴은_직전_역질문의_원문으로_질의를_채운다() -> None:
    last = Observation(status="clarification", clarification=ZONE)
    filled = complete_payload({"selected_db_ids": ["polestar_cm_gp"]}, last, "다른 질의")
    assert filled["query"] == "전체 서버 수 알려줘"


def test_폼필_답변_턴은_UI와_같은_문구로_채운다() -> None:
    filled = complete_payload({"form_fill_answers": {"a": {}}}, None, "원 질의")
    assert filled["query"] == FORM_FILL_REPLY_QUERY


def test_질의가_있으면_건드리지_않는다() -> None:
    payload = {"query": "그대로", "selected_db_ids": ["x"]}
    assert complete_payload(payload, None, "직전") is payload


def test_기타_구조화_턴은_직전_질의를_쓴다() -> None:
    assert complete_payload({"form_memory_delete": {"fields": ["a"]}}, None, "직전")["query"] == "직전"


def test_기억_삭제_턴은_직전_패널의_시그니처를_채운다() -> None:
    """서버는 세션이 조회한 양식 시그니처와 같아야 지운다(D-187) - I-06 3턴이 이것 없이 거부됐다."""
    last = Observation(status="completed", form_memory_panel={"signature": "sig-1", "entries": []})
    filled = complete_payload({"form_memory_delete": {"fields": ["담당자"]}}, last, "이 양식에 저장된 내용은?")
    assert filled["form_memory_delete"] == {"fields": ["담당자"], "signature": "sig-1"}
    assert filled["query"] == "이 양식에 저장된 내용은?"


def test_시그니처가_이미_있으면_덮어쓰지_않는다() -> None:
    last = Observation(form_memory_panel={"signature": "새것"})
    payload = {"query": "q", "form_memory_delete": {"fields": ["a"], "signature": "원래"}}
    assert complete_payload(payload, last, "q") is payload


# --- 설정 · 카탈로그 -----------------------------------------------------

def test_저장소_기본_우선_존은_김포다() -> None:
    assert load_zone_preference()[0] == "polestar_cm_gp"


def test_우선순위_파일이_없으면_빈_목록이다(tmp_path: Path) -> None:
    assert load_zone_preference(tmp_path / "없음.yaml") == []


def test_턴_auto_answer_false_와_시나리오_명시값을_읽는다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", """version: 1
group: {id: T, name: "테스트군", latency_target_ms: 10000}
scenarios:
  - id: T-01
    plans: [94]
    title: "자동 응답"
    auto_answer: {selected_db_ids: [polestar_cm_yd]}
    turns:
      - send: {query: "서버 목록"}
        auto_answer: false
        expect: {status: completed}
""")
    scenario = load_catalog(scenario_dir, profiles_path).by_id("T-01")
    assert scenario.auto_answer == {"selected_db_ids": ["polestar_cm_yd"]}
    assert scenario.turns[0].auto_answer is False


def test_auto_answer_형식이_틀리면_로더가_거부한다(scenario_dir: Path, profiles_path: Path) -> None:
    write(scenario_dir / "t.yaml", """version: 1
group: {id: T, name: "테스트군", latency_target_ms: 10000}
scenarios:
  - id: T-01
    plans: [94]
    title: "형식 오류"
    auto_answer: {zone: gp}
    turns:
      - send: {query: "서버 목록"}
        auto_answer: "아니오"
        expect: {status: completed}
""")
    with pytest.raises(CatalogError) as exc:
        load_catalog(scenario_dir, profiles_path)
    text = "\n".join(exc.value.errors)
    assert "auto_answer" in text and "zone" in text and "true|false" in text
