"""폼필 답변 턴(D-151)의 행 상한 — 원 질의의 명시 건수를 따른다.

답변 턴은 원 질의를 복원하면서 상한을 전량(10,000)으로 고정해, 첫 턴에 "10건만"이라고
한 요청이 답변 뒤에 전량으로 채워졌다(run 20260923-103638 H-16 — 자동응답 뒤 1,690행).
첫 턴(파일 라우트)과 같은 규칙(`resolve_query_limit`)을 쓰는지 고정한다.

LLM·DB·Redis 0.
"""

from __future__ import annotations

from src.api.routes.query import _FORM_FILL_DEFAULT_LIMIT, _build_turn_input_state
from src.api.schemas import QueryRequest

USER = {"sub": "u1", "role": "user", "allowed_db_ids": ["db_a"], "department": "인프라"}
ANSWERS = {"용도": {"action": "blank"}}


def _answer_turn_delta(original_query: str | None) -> dict:
    pending = {"uploaded_file": b"xlsx-bytes", "file_type": "xlsx", "db_ids": ["db_a"]}
    if original_query is not None:
        pending["original_query"] = original_query
    checkpoint = {"thread_id": "t1", "pending_form_fill": pending}
    body = QueryRequest(query="[양식 미해결 항목 답변]", form_fill_answers=ANSWERS)
    return _build_turn_input_state(body, "t1", checkpoint, USER)


def test_answer_turn_keeps_explicit_count_from_original_query():
    delta = _answer_turn_delta("김포 서버 목록을 10건만 양식 채워줘")
    assert delta["user_query"] == "김포 서버 목록을 10건만 양식 채워줘"
    assert delta["resolved_limit"] == 10


def test_answer_turn_without_count_stays_full():
    """건수를 말하지 않은 원 질의는 종전대로 전량이다."""
    delta = _answer_turn_delta("김포 서버 목록 양식 채워줘")
    assert delta["resolved_limit"] == _FORM_FILL_DEFAULT_LIMIT


def test_answer_turn_without_original_query_stays_full():
    """원 질의가 없으면 답변 턴 고정 문구로 판정한다 — 건수가 없으니 전량이다."""
    delta = _answer_turn_delta(None)
    assert delta["resolved_limit"] == _FORM_FILL_DEFAULT_LIMIT
