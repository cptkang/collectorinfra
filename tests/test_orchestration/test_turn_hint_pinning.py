"""이번 턴 원문 위치 힌트의 결정적 DB 고정 + 맥락 오염원 차단 회귀 테스트 (D-084).

버그(2026-07-16): 폼필(은행존+김포) 턴 다음의 텍스트 질의 "지난 하루동안 은행존에서
발생한 모든 알람"이 intent_planner LLM 분해에서 직전 턴 위치와 병합돼 sub_query가
"김포 은행 공동존에서 …"로 오염 → classify_dbs(LLM)가 gp를 선택(은행존 b0 누락).
프롬프트에 "명시 위치 최우선" 규칙이 있었으나 LLM이 미준수 — 비결정적.

수정(2겹):
- 2a: _build_context_block이 이번 턴 원문에 위치 표면어가 있으면 직전 위치/DB 줄을
  주입하지 않는다(입력에 없는 위치는 병합 불가 — 오염원 제거).
- 2b: run_data_query_pipeline의 DB 선택에서 이번 턴 원문 힌트(target_db_hints)를
  폼필과 동일 로직(resolve_priority_db_ids)으로 해소해 DB 집합을 결정적으로 고정
  (_apply_turn_hint_pinning). classify_dbs 결과는 sub_query_context 재사용을 위해 유지.
"""

from __future__ import annotations

import importlib

from src.orchestration.intent_planner import _build_context_block
from src.orchestration.subagents import (
    _apply_turn_hint_pinning,
    _strip_location_terms,
)

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "cloud_portal"]


def _classify_target(db_id: str, ctx: str = "정제 질의") -> dict:
    return {
        "db_id": db_id,
        "relevance_score": 0.9,
        "sub_query_context": ctx,
        "user_specified": False,
        "reason": "LLM 분류",
    }


def _isolated(hints: list[str] | None, *, composite: bool = False) -> dict:
    return {
        "is_composite": composite,
        "parsed_requirements": {"target_db_hints": hints or []},
    }


class TestApplyTurnHintPinning:
    """원문 힌트가 해소되면 classify 결과와 무관하게 DB 집합이 고정되는지."""

    def test_bug_repro_bankzone_hint_overrides_gp_classification(self):
        """실측 버그 재현: 힌트=["은행존"]인데 classify가 gp를 내면 b0로 고정."""
        targets, pinned = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["은행존"]),
            "김포 은행 공동존에서 지난 하루동안 발생한 모든 알람 조회",
            _ACTIVE,
        )
        assert pinned is True
        assert [t["db_id"] for t in targets] == ["polestar_b0"]

    def test_reuses_classify_target_when_db_matches(self):
        """classify가 이미 맞는 DB를 냈으면 그 dict(정제 질의 포함)를 재사용한다."""
        classified = _classify_target("polestar_b0", ctx="지난 하루 알람 조회")
        targets, pinned = _apply_turn_hint_pinning(
            [classified], _isolated(["은행존"]), "은행존 알람", _ACTIVE
        )
        assert pinned is True
        assert targets == [classified]
        assert targets[0]["sub_query_context"] == "지난 하루 알람 조회"

    def test_cross_zone_hints_supplement_missing_db(self):
        """힌트가 두 존을 지목하면 classify가 빠뜨린 DB를 보충한다."""
        targets, pinned = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["은행존", "공동존 김포 폴스타"]),
            "은행존과 공동존 김포 알람",
            _ACTIVE,
        )
        assert pinned is True
        assert [t["db_id"] for t in targets] == ["polestar_b0", "polestar_cm_gp"]
        # 합성 target(b0)에는 위치어가 제거된 정제 질의가 들어간다
        b0 = targets[0]
        assert "은행존" not in b0["sub_query_context"]
        assert "김포" not in b0["sub_query_context"]

    def test_no_hints_keeps_classify_results(self):
        """힌트가 없으면(위치 미명시) 기존 classify 결과 그대로."""
        classified = [_classify_target("polestar_cm_gp")]
        targets, pinned = _apply_turn_hint_pinning(
            classified, _isolated([]), "해당 서버 알람", _ACTIVE
        )
        assert pinned is False
        assert targets == classified

    def test_unresolvable_hints_keep_classify_results(self):
        """활성 DB로 해소되지 않는 힌트는 무시(기존 동작 유지)."""
        classified = [_classify_target("polestar_cm_gp")]
        targets, pinned = _apply_turn_hint_pinning(
            classified, _isolated(["알 수 없는 위치"]), "질의", _ACTIVE
        )
        assert pinned is False
        assert targets == classified

    def test_composite_task_ignores_location_absent_from_original(self):
        """복합 계획도 task 단위로 고정한다(plans/113 F-2 — 종전엔 통째로 해제했다).

        task 질의의 "김포"는 원문 힌트(["은행존"])에 없으므로 무시한다 — LLM이 task 질의에
        다른 위치를 섞는 오염(2026-07-16)에 대한 원문 우선 방어가 복합 계획에서도 유지된다.
        (종전 `test_composite_plan_skips_pinning`은 해제 동작 자체를 단언했다 — A-3 결함.)
        """
        classified = [_classify_target("polestar_cm_gp")]
        targets, pinned = _apply_turn_hint_pinning(
            classified,
            _isolated(["은행존"], composite=True),
            "은행존 알람과 김포 서버 현황",
            _ACTIVE,
        )
        assert pinned is True
        assert [t["db_id"] for t in targets] == ["polestar_b0"]


class TestCompositeTaskPinning:
    """복합 계획의 task 단위 결정적 고정 (plans/113 F-2 · 계획서 A-3 교정)."""

    def test_task_without_location_gets_whole_original_hint(self):
        """"공동존 CPU top10과 메모리 top10" — 위치어 없는 두 task가 모두 [gp, yd]."""
        for sub_query, classified in (
            ("VM 최대 CPU 사용률 상위 10개 서버", "polestar_cm_gp"),
            ("VM 최대 메모리 사용률 상위 10개 서버", "polestar_cm_yd"),
        ):
            targets, pinned = _apply_turn_hint_pinning(
                [_classify_target(classified)],
                _isolated(["공동존"], composite=True), sub_query, _ACTIVE,
            )
            assert pinned is True
            assert [t["db_id"] for t in targets] == ["polestar_cm_gp", "polestar_cm_yd"]

    def test_task_naming_zone_gets_whole_zone(self):
        targets, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["공동존"], composite=True), "공동존 VM 최대 메모리 top 10", _ACTIVE,
        )
        assert [t["db_id"] for t in targets] == ["polestar_cm_gp", "polestar_cm_yd"]

    def test_location_split_tasks_pin_their_own_location(self):
        """"김포 CPU top10과 여의도 메모리 top10" → task별 [gp] / [yd]."""
        hints = ["김포", "여의도"]
        gp, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_yd")],
            _isolated(hints, composite=True), "김포 서버 CPU 상위 10개", _ACTIVE,
        )
        yd, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(hints, composite=True), "여의도 서버 메모리 상위 10개", _ACTIVE,
        )
        assert [t["db_id"] for t in gp] == ["polestar_cm_gp"]
        assert [t["db_id"] for t in yd] == ["polestar_cm_yd"]

    def test_compound_original_hint_split_by_region_term(self):
        """원문 힌트가 한 덩어리("김포와 여의도")여도 task 질의의 지역 표면어로 가른다."""
        targets, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["김포와 여의도"], composite=True), "여의도 메모리 top 5", _ACTIVE,
        )
        assert [t["db_id"] for t in targets] == ["polestar_cm_yd"]

    def test_task_narrowing_never_widens_original(self):
        """원문 "공동존 김포"인데 task 질의가 "공동존"만 담아도 김포 밖으로 넓히지 않는다."""
        targets, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["공동존 김포"], composite=True), "공동존 메모리 top 5", _ACTIVE,
        )
        assert [t["db_id"] for t in targets] == ["polestar_cm_gp"]

    def test_hallucinated_split_inside_zone_is_ignored(self):
        """원문은 "공동존"뿐인데 LLM이 task를 김포/여의도로 나눠 적어도 원문 전체로 고정한다."""
        targets, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["공동존"], composite=True), "김포 서버 CPU 상위 10개", _ACTIVE,
        )
        assert [t["db_id"] for t in targets] == ["polestar_cm_gp", "polestar_cm_yd"]

    def test_single_task_keeps_whole_original_hint(self):
        """단일 task(1단 도구 호출 포함)는 task 질의로 좁히지 않는다 — 재작성 질의가 위치어를
        빠뜨려도 한 존이 조용히 빠지지 않게(과포함 쪽을 택한다)."""
        targets, _ = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp")],
            _isolated(["김포", "여의도"]), "김포 서버 CPU 사용률 비교", _ACTIVE,
        )
        assert [t["db_id"] for t in targets] == ["polestar_cm_gp", "polestar_cm_yd"]


class TestZonelessTargetsPreserved:
    """존 그룹이 없는 DB의 분류 결과는 보존한다 — 3단 라우터와 같은 함수·같은 규칙(F-2)."""

    def test_zoneless_classification_survives_zone_hint(self):
        itam = _classify_target("itam", ctx="유지보수 계약 만료일")
        targets, pinned = _apply_turn_hint_pinning(
            [_classify_target("polestar_cm_gp"), itam],
            _isolated(["공동존"]), "공동존 서버의 유지보수 계약 만료일",
            [*_ACTIVE, "itam"],
        )
        assert pinned is True
        assert [t["db_id"] for t in targets] == ["polestar_cm_gp", "polestar_cm_yd", "itam"]
        assert targets[-1] is itam

    def test_exclusive_does_not_pin_across_zone_groups(self):
        """상호배타(ZONE_GROUP_EXCLUSIVE=true)에서 두 존 그룹 해소(제품명 단독)는 고정 안 함."""
        classified = [_classify_target("polestar_b0")]
        targets, pinned = _apply_turn_hint_pinning(
            classified, _isolated(["폴스타"]), "폴스타 서버 목록", _ACTIVE,
            zone_group_exclusive=True,
        )
        assert pinned is False
        assert targets == classified

    def test_same_function_as_tier3_router(self):
        """1·2단과 3단이 같은 판정 함수를 쓴다(D-053 사본 금지 · D-066 대칭)."""
        import inspect

        from src.orchestration import subagents
        from src.routing import location_hints

        router = importlib.import_module("src.routing.semantic_router")
        assert "pin_targets_to_hints(" in inspect.getsource(subagents._apply_turn_hint_pinning)
        assert "pin_targets_to_hints(" in inspect.getsource(router._pin_turn_location_hints)
        assert subagents.pin_targets_to_hints is location_hints.pin_targets_to_hints
        assert router.pin_targets_to_hints is location_hints.pin_targets_to_hints


class TestStripLocationTerms:
    def test_removes_location_and_product_tokens(self):
        out = _strip_location_terms("은행존 폴스타에서 지난 하루동안 발생한 모든 알람")
        assert "은행존" not in out
        assert "폴스타" not in out
        assert "알람" in out

    def test_longer_token_removed_without_residue(self):
        """"은행존"이 "은행"+잔재("존")로 쪼개지지 않는다."""
        out = _strip_location_terms("은행존 알람")
        assert "존" not in out


class TestContextBlockLocationGate:
    """2a: 이번 턴 원문에 위치가 명시되면 직전 위치/DB 줄을 주입하지 않는다."""

    _CTX = {
        "turn_count": 2,
        "previous_location": "김포 은행 공동존",
        "previous_db_ids": ["polestar_b0", "polestar_cm_gp"],
        "previous_entities": [{"field": "hostname", "value": "###"}],
        "previous_results_summary": "폼필 완료",
    }

    def test_explicit_location_omits_previous_location_and_db(self):
        block = _build_context_block(
            self._CTX, "지난 하루동안 은행존에서 발생한 모든 알람을 출력해줘"
        )
        assert "직전 대상 위치/환경" not in block
        assert "직전 대상 DB 후보" not in block
        # 직전 턴의 실제 위치/DB 값이 입력에 존재하지 않음 → 병합 자체가 불가
        # (지시어 규칙의 정적 예시 문구는 맥락 값이 아니므로 단언 대상이 아님)
        assert "김포 은행 공동존" not in block
        assert "polestar_cm_gp" not in block
        # 이번 질의 위치만 쓰라는 지시가 대신 들어간다
        assert "이번 질의에 적힌 위치만" in block
        # 요약 줄은 유지. 서버 엔티티는 지시어 없는 질의라 미주입(D-153 후속1 —
        # 지시어 턴에만 주입, 샘플 엔티티의 스코프 오염 차단).
        assert "hostname=###" not in block
        assert "폼필 완료" in block

    def test_no_location_keeps_previous_lines(self):
        """위치 미명시 후속 턴은 기존 동작(직전 위치/DB 주입) 유지 — 승계 회귀 방지."""
        block = _build_context_block(self._CTX, "해당 서버의 프로세스 리스트")
        assert "직전 대상 위치/환경" in block
        assert "직전 대상 DB 후보" in block
        assert "polestar_b0" in block

    def test_backward_compatible_without_user_query(self):
        """user_query 미전달(기존 시그니처) 시 종전 동작."""
        block = _build_context_block(self._CTX)
        assert "직전 대상 위치/환경" in block
