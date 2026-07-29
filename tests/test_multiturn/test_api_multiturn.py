"""멀티턴 API 테스트."""

import pytest

from src.api.routes.query import _parse_approval


class TestParseApproval:
    """승인 의도 파싱 검증."""

    def test_approve_korean(self):
        action, _ = _parse_approval("실행")
        assert action == "approve"

    def test_approve_english(self):
        action, _ = _parse_approval("approve")
        assert action == "approve"

    def test_approve_yes(self):
        action, _ = _parse_approval("네")
        assert action == "approve"

    def test_reject_cancel(self):
        action, _ = _parse_approval("취소")
        assert action == "reject"

    def test_reject_english(self):
        action, _ = _parse_approval("reject")
        assert action == "reject"

    def test_modify_with_sql(self):
        action, sql = _parse_approval("SELECT * FROM servers WHERE id > 5")
        assert action == "modify"
        assert "SELECT" in sql

    def test_ok_approves(self):
        action, _ = _parse_approval("ok")
        assert action == "approve"

    def test_no_rejects(self):
        action, _ = _parse_approval("no")
        assert action == "reject"


class TestParseApprovalFailClosed:
    """승인 오탐 차단 검증 (Plan 67 Phase 0 ⑧).

    기본값이 approve이고 승인어를 prefix로 매칭하던 시절에는 "확인해보고 알려줘"처럼
    승인이 아닌 입력이 승인으로 해석되어 미승인 SQL이 실행됐다.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "확인해보고 알려줘",       # 승인어 prefix 오탐 (실제 사고 사례)
            "네 그럼 메모리도 보여줘",  # 승인어로 시작하지만 새 질의
            "실행 중인 프로세스 목록",   # "실행" prefix 오탐
            "노드 목록 알려줘",         # 승인과 무관한 후속 질의
            "그럼 CPU 사용률은?",
            "",
        ],
    )
    def test_ambiguous_input_is_not_approved(self, query):
        action, _ = _parse_approval(query)
        assert action != "approve"

    @pytest.mark.parametrize(
        "query, expected",
        [
            ("실행해줘", "approve"),
            ("실행해 주세요", "approve"),
            ("승인합니다", "approve"),
            ("네!", "approve"),
            ("ok.", "approve"),
            ("ㅇㅇ", "approve"),
            ("취소해줘", "reject"),
            ("아니요", "reject"),
            ("거부합니다", "reject"),
        ],
    )
    def test_explicit_intent_still_recognized(self, query, expected):
        """어미·문장부호가 붙은 명시적 승인·거부는 그대로 인식한다."""
        action, _ = _parse_approval(query)
        assert action == expected

    def test_unrecognized_input_defaults_to_reject(self):
        """의도 불명이면 실행하지 않는다(fail-closed)."""
        action, sql = _parse_approval("이 쿼리 결과를 엑셀로 정리해줘")
        assert action == "reject"
        assert sql == ""
