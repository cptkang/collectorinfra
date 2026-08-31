"""범위 질문 발동 게이트 + 페이로드 (Plan 82 W65-T5 · SPEC-scope-select).

★ 이 파일이 지키는 계약 세 가지:
  ① **묻지 않는 조건이 묻는 조건보다 많다** — 재개·승계·비대화·모호성 대기·좁힐 여지 없음
  ② **"전체 조회"가 항상 첫 선택지이고 기본값**이다(U10 — 답하지 않아도 진행)
  ③ **시간 임계 상수가 없다**(U11) — 그룹 2개 이상이면 항상 묻는다

순수 함수만 다루므로 LLM·DB·네트워크 0(D-127).
"""

from __future__ import annotations

import pytest

from src.domain.scope_select import (
    ALL_KEY,
    KIND,
    MIN_SAMPLES_FOR_ESTIMATE,
    narrowed_record,
    render_narrowed_note,
    scope_question_or_none,
)

BANK = {"group_key": "polestar:bank", "label": "은행존", "db_ids": ["polestar_b0"], "kind": "peer"}
COMMON = {
    "group_key": "polestar:common", "label": "공동존",
    "db_ids": ["polestar_cm_gp", "polestar_cm_yd"], "kind": "peer",
}
DISCOVERY = {"group_key": "polestar:discovery", "label": "탐색", "db_ids": [], "kind": "discovery"}

TWO = [BANK, COMMON]
CTX = {"zone_clarification_allowed": True, "original_query": "abd00 서버의 프로세스"}


def _ask(groups=None, ctx=None, enabled=True, samples=0):
    return scope_question_or_none(
        groups=TWO if groups is None else groups,
        ctx={**CTX, **(ctx or {})},
        enabled=enabled,
        samples=samples,
    )


class TestFires:
    def test_two_groups_ask(self):
        payload = _ask()

        assert payload is not None
        assert payload["kind"] == KIND
        assert payload["axis"] == "zone_group"
        assert "범위를 좁히시겠습니까?" in payload["question"]

    def test_question_states_group_count(self):
        assert "2개" in _ask()["question"]


class TestDoesNotFire:
    def test_disabled(self):
        assert _ask(enabled=False) is None

    def test_single_group(self):
        assert _ask(groups=[BANK]) is None

    def test_no_group(self):
        assert _ask(groups=[]) is None

    def test_non_interactive_channel(self):
        """배치·평가·API 직접 호출은 답할 사람이 없다 — 물으면 그대로 멈춘다."""
        assert _ask(ctx={"zone_clarification_allowed": False}) is None

    def test_resume_turn_with_selected_db_ids(self):
        assert _ask(ctx={"selected_db_ids": ["polestar_b0"]}) is None

    def test_resume_turn_with_selected_scope(self):
        assert _ask(ctx={"selected_scope": {"keys": ["polestar:bank"]}}) is None

    def test_multiturn_inherited_scope(self):
        assert _ask(ctx={"previous_scope": ["polestar:bank"]}) is None

    def test_ambiguity_question_wins(self):
        """★ 모호성 해소가 이긴다 — 2연속 질문 금지."""
        assert _ask(ctx={"ambiguity_pending": True}) is None

    def test_discovery_group_is_not_billable(self):
        """탐색은 존당 ~50ms — 좁혀도 아낄 것이 없어 비용 산정에서 뺀다."""
        assert _ask(groups=[BANK, DISCOVERY]) is None

    def test_duplicate_group_keys_count_once(self):
        assert _ask(groups=[BANK, dict(BANK)]) is None


class TestOptions:
    def test_all_option_is_first_and_default(self):
        """★ 모르면 그냥 진행할 수 있어야 한다(U10)."""
        options = _ask()["options"]

        assert options[0]["key"] == ALL_KEY
        assert options[0]["default"] is True
        assert all(o["default"] is False for o in options[1:])

    def test_all_option_carries_every_db_id(self):
        assert _ask()["options"][0]["db_ids"] == [
            "polestar_b0", "polestar_cm_gp", "polestar_cm_yd",
        ]

    def test_group_options_follow(self):
        options = _ask()["options"]

        assert [o["key"] for o in options[1:]] == ["polestar:bank", "polestar:common"]
        assert [o["label"] for o in options[1:]] == ["은행존", "공동존"]

    def test_payload_is_marked_skippable(self):
        assert _ask()["skippable"] is True

    def test_original_query_is_carried_for_resend(self):
        assert _ask()["original_query"] == "abd00 서버의 프로세스"


class TestNoTimeThreshold:
    def test_no_seconds_constant_in_module(self):
        """★ U11 — 시간 임계 상수를 만들지 않는다(근거 없는 임계의 무기한 실동작 차단)."""
        import inspect

        import src.domain.scope_select as mod

        source = inspect.getsource(mod)
        assert "MIN_SECONDS" not in source
        assert "min_seconds" not in source

    def test_fires_regardless_of_speed(self):
        """빠른 조회에도 그룹이 2개면 묻는다 — 임계가 없으므로."""
        fast = [{**BANK, "p50_ms": 10}, {**COMMON, "p50_ms": 10}]

        assert _ask(groups=fast) is not None


class TestEstimateText:
    def test_no_estimate_below_sample_floor(self):
        """★ 표본이 모자라면 초 표기를 **아예 내지 않는다**(계획서 §5.5 S-C)."""
        groups = [{**BANK, "p50_ms": 20000, "p90_ms": 40000},
                  {**COMMON, "p50_ms": 20000, "p90_ms": 40000}]

        question = _ask(groups=groups, samples=MIN_SAMPLES_FOR_ESTIMATE - 1)["question"]

        assert "초" not in question
        assert "2개" in question

    def test_estimate_appears_with_enough_samples(self):
        groups = [{**BANK, "p50_ms": 20000, "p90_ms": 40000},
                  {**COMMON, "p50_ms": 20000, "p90_ms": 40000}]

        question = _ask(groups=groups, samples=MIN_SAMPLES_FOR_ESTIMATE)["question"]

        assert "예상 40~80초" in question

    def test_missing_metrics_yield_no_estimate(self):
        question = _ask(samples=MIN_SAMPLES_FOR_ESTIMATE)["question"]

        assert "예상" not in question


class TestNarrowedRecord:
    def test_records_skipped_groups(self):
        record = narrowed_record(TWO, ["polestar_b0"])

        assert record["selected"] == ["은행존"]
        assert record["skipped"] == ["공동존"]
        assert record["skipped_db_ids"] == ["polestar_cm_gp", "polestar_cm_yd"]

    def test_full_selection_is_not_a_narrowing(self):
        assert narrowed_record(TWO, ["polestar_b0", "polestar_cm_gp"]) is None

    def test_partial_group_selection_counts_as_selected(self):
        """공동존 2개 중 하나만 골라도 그 그룹은 '조회함'이다(그룹 단위 기록)."""
        record = narrowed_record(TWO, ["polestar_cm_gp"])

        assert record["skipped"] == ["은행존"]

    def test_no_selection_is_not_a_record(self):
        assert narrowed_record(TWO, None) is None

    def test_render_names_what_was_skipped(self):
        note = render_narrowed_note(narrowed_record(TWO, ["polestar_b0"]))

        assert "은행존만 조회했습니다" in note
        assert "공동존은(는) 조회하지 않았습니다" in note

    def test_render_empty_when_nothing_skipped(self):
        assert render_narrowed_note(None) == ""
