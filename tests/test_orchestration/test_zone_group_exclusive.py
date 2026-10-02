"""존 그룹 상호배타 테스트 (D-143 후속3).

은행존(b0)과 공동존(gp/yd)은 동시 조회 불가 — 담당 조직 분리(존 조합 실수요 없음,
사용자 확정 2026-08-05) + b0+gp 조합의 FabriX PII 필터 차단(미종결) 회피.
혼합 selected_db_ids(프론트 우회 포함)·혼합 텍스트 지정 모두 결정적으로 감지해
에러 대신 존 선택 UI 재요청으로 전환한다. 전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.api.routes.query import (
    _file_zone_clarification_or_none,
    _substitute_zone_placeholder,
    _zone_clarification_or_none,
    _zone_group_exclusive_or_none,
)
from src.api.schemas import QueryRequest
from src.utils.query_gen_common import (
    ZONE_GROUP_EXCLUSIVE_QUESTION,
    build_zone_clarification,
    has_mixed_zone_group_terms,
    mixed_zone_groups,
    rewrite_zone_mentions_for_selection,
)

_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _config(active=_ACTIVE, exclusive=True):
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: active,
            zone_group_exclusive=exclusive,
        )
    )


class TestGroupHelpers:
    def test_mixed_zone_groups(self):
        assert mixed_zone_groups(["polestar_b0", "polestar_cm_gp"]) is True
        assert mixed_zone_groups(["polestar_b0", "polestar_cm_yd"]) is True
        assert mixed_zone_groups(["polestar_cm_gp", "polestar_cm_yd"]) is False
        assert mixed_zone_groups(["polestar_b0"]) is False
        assert mixed_zone_groups([]) is False
        assert mixed_zone_groups(None) is False
        # 미등재 id는 판정에서 무시
        assert mixed_zone_groups(["polestar_b0", "unknown_db"]) is False

    def test_has_mixed_zone_group_terms(self):
        assert has_mixed_zone_group_terms("은행존과 공동존 김포의 서버들 OS 조회") is True
        assert has_mixed_zone_group_terms("레거시 서버와 여의도 서버 비교") is True
        assert has_mixed_zone_group_terms("공동존 김포와 여의도 전체 서버") is False
        assert has_mixed_zone_group_terms("은행존 전체 서버 OS 확인") is False
        assert has_mixed_zone_group_terms("OS 종류 확인") is False
        assert has_mixed_zone_group_terms("") is False

    def test_payload_carries_group_and_exclusive(self):
        payload = build_zone_clarification(_ACTIVE, "질의", group_exclusive=True)
        assert payload["group_exclusive"] is True
        groups = {o["db_id"]: o["group"] for o in payload["options"]}
        assert groups["polestar_b0"] == "bank"
        assert groups["polestar_cm_gp"] == "common"
        assert groups["polestar_cm_yd"] == "common"
        # 상호배타 시 기본 안내문에서 "전체는 모두 선택" 문구가 빠진다
        assert "모두 선택" not in payload["question"]

    def test_payload_without_exclusive_keeps_legacy_shape(self):
        payload = build_zone_clarification(_ACTIVE, "질의")
        assert "group_exclusive" not in payload
        assert "모두 선택" in payload["question"]


class TestRouteGate:
    """텍스트 라우트 — 혼합 선택·혼합 텍스트의 결정적 전환."""

    def test_mixed_selected_db_ids_reasks(self):
        """혼합 selected_db_ids(UI 우회 포함)는 재선택 요청 — 파이프라인 미진입."""
        body = QueryRequest(
            query="OS 종류 확인",
            selected_db_ids=["polestar_b0", "polestar_cm_gp"],
        )
        clar = _zone_clarification_or_none(body, {}, _config())
        assert clar is not None
        assert clar["question"] == ZONE_GROUP_EXCLUSIVE_QUESTION
        assert clar["group_exclusive"] is True

    def test_single_group_selected_proceeds(self):
        body = QueryRequest(
            query="OS 종류 확인",
            selected_db_ids=["polestar_cm_gp", "polestar_cm_yd"],
        )
        assert _zone_clarification_or_none(body, {}, _config()) is None

    def test_mixed_text_reasks_any_turn(self):
        """혼합 텍스트 지정은 첫 턴·후속 턴 무관 존 선택창으로 전환."""
        body = QueryRequest(query="은행존과 공동존 김포의 서버들에 대해 OS 조회")
        for checkpoint in (None, {"prev": True}):
            clar = _zone_clarification_or_none(body, checkpoint, _config())
            assert clar is not None
            assert clar["question"] == ZONE_GROUP_EXCLUSIVE_QUESTION

    def test_single_group_text_not_intercepted(self):
        """단일 그룹 지정 질의는 기존 흐름 유지(위치어 해소 — 비발동)."""
        body = QueryRequest(query="공동존 김포와 여의도 전체 서버들의 OS 종류 확인")
        assert _zone_clarification_or_none(body, None, _config()) is None

    def test_flag_off_restores_legacy_behavior(self):
        cfg = _config(exclusive=False)
        body = QueryRequest(
            query="OS 종류 확인",
            selected_db_ids=["polestar_b0", "polestar_cm_gp"],
        )
        assert _zone_clarification_or_none(body, {}, cfg) is None
        body2 = QueryRequest(query="은행존과 공동존 김포의 서버들에 대해 OS 조회")
        assert _zone_clarification_or_none(body2, None, cfg) is None

    def test_normal_popup_carries_exclusive_ui_rule(self):
        """일반(존 미지정) 역질문 페이로드에도 그룹 상호배타 UI 규칙이 실린다."""
        body = QueryRequest(query="모든 서버들의 OS 종류를 확인해줘")
        clar = _zone_clarification_or_none(body, None, _config())
        assert clar is not None
        assert clar["group_exclusive"] is True


class TestFileRouteGate:
    """파일(폼필) 경로 대칭."""

    def test_mixed_selected_reasks_with_file_flag(self):
        clar = _file_zone_clarification_or_none(
            "양식 채워줘", ["polestar_b0", "polestar_cm_yd"], _config()
        )
        assert clar is not None
        assert clar["question"] == ZONE_GROUP_EXCLUSIVE_QUESTION
        assert clar["has_file"] is True

    def test_mixed_text_reasks(self):
        clar = _file_zone_clarification_or_none(
            "은행존과 공동존 김포 서버로 양식 채워줘", None, _config()
        )
        assert clar is not None
        assert clar["question"] == ZONE_GROUP_EXCLUSIVE_QUESTION

    def test_single_group_selected_proceeds(self):
        assert _file_zone_clarification_or_none(
            "양식 채워줘", ["polestar_cm_gp"], _config()
        ) is None


class TestZoneMentionRewrite:
    """존 재선택 재개 턴 원문 재작성 (D-154) — 미선택 존 표기·위치어 누출 차단."""

    _MIXED_Q = "은행존 및 공동존 여의도 센터의 모든 서버들에 대해 OS종류, OS버전 및 OS패치버전 확인"

    def test_bank_selected_drops_common_terms(self):
        """은행존만 선택 시 공동존·여의도 표기가 제거되고 은행존 라벨로 치환된다."""
        out = rewrite_zone_mentions_for_selection(self._MIXED_Q, ["polestar_b0"])
        assert "공동존" not in out
        assert "여의도" not in out
        assert out.startswith("은행존")
        # 존 열거 이후의 본문(조회 대상)은 보존된다
        assert "모든 서버들에 대해 OS종류, OS버전 및 OS패치버전 확인" in out

    def test_common_selected_drops_bank_terms(self):
        out = rewrite_zone_mentions_for_selection(
            self._MIXED_Q, ["polestar_cm_yd"]
        )
        assert "은행존" not in out
        assert out.startswith("공동존 여의도")

    def test_multiple_selected_labels_joined(self):
        out = rewrite_zone_mentions_for_selection(
            "은행존과 공동존 김포 서버 조회", ["polestar_cm_gp", "polestar_cm_yd"]
        )
        assert "은행존" not in out
        assert "공동존 김포, 공동존 여의도" in out

    def test_no_selection_returns_original(self):
        assert rewrite_zone_mentions_for_selection(self._MIXED_Q, None) == self._MIXED_Q
        assert rewrite_zone_mentions_for_selection(self._MIXED_Q, []) == self._MIXED_Q

    def test_single_group_text_untouched(self):
        """혼합 표면어가 없으면(단일 그룹 지정) 원문 그대로 — 오치환 면적 최소화."""
        q = "공동존 김포와 여의도 전체 서버들의 OS 종류 확인"
        assert rewrite_zone_mentions_for_selection(
            q, ["polestar_cm_gp", "polestar_cm_yd"]
        ) == q

    def test_route_helper_applies_rewrite_on_resume_turn(self):
        """라우트 치환 헬퍼가 재개 턴에서 혼합 존 열거 재작성을 적용한다."""
        out = _substitute_zone_placeholder(self._MIXED_Q, ["polestar_b0"])
        assert "공동존" not in out
        assert "여의도" not in out

    def test_route_helper_placeholder_still_works(self):
        """기존 'ㅇㅇ존' 플레이스홀더 치환 동작은 유지된다."""
        out = _substitute_zone_placeholder(
            "ㅇㅇ존 서버 전체 OS 확인", ["polestar_b0"]
        )
        assert out == "은행존 서버 전체 OS 확인"
