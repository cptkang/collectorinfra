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
