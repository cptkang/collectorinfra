"""D-361 — 조사 답변 언어 지침(항상 · 지침 맨 끝 · 중복 없음)."""

from __future__ import annotations

from sre_agent.diagnosis import (
    ANSWER_LANGUAGE_NOTE,
    _with_answer_language_note,
    _with_load_guard_note,
)
from sre_agent.toolset_profiles import LOAD_GUARD_NOTE


def test_note_appended_last_after_load_guard() -> None:
    out = _with_answer_language_note(_with_load_guard_note(None))
    assert out.startswith(LOAD_GUARD_NOTE)
    assert out.endswith(ANSWER_LANGUAGE_NOTE)


def test_caller_guidance_preserved_and_note_last() -> None:
    out = _with_answer_language_note(_with_load_guard_note("호출자 고유 지침"))
    assert out.startswith("호출자 고유 지침")
    assert out.index(LOAD_GUARD_NOTE) < out.index(ANSWER_LANGUAGE_NOTE)
    assert out.endswith(ANSWER_LANGUAGE_NOTE)


def test_not_duplicated() -> None:
    once = _with_answer_language_note("지침")
    assert _with_answer_language_note(once) == once
    assert once.count(ANSWER_LANGUAGE_NOTE) == 1


def test_note_keeps_identifiers_untranslated() -> None:
    assert "한국어" in ANSWER_LANGUAGE_NOTE and "원문 그대로" in ANSWER_LANGUAGE_NOTE
