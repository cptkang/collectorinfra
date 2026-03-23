"""민감 데이터 마스킹 확장 테스트.

추가 패턴, 경계 조건, spec 요구사항 준수를 검증한다.
"""

import pytest

from src.config import SecurityConfig
from src.security.data_masker import DataMasker


@pytest.fixture
def masker():
    config = SecurityConfig(
        sensitive_columns=[
            "password", "passwd", "pwd",
            "secret", "secret_key",
            "token", "access_token", "refresh_token",
            "api_key", "apikey",
            "private_key", "priv_key",
            "credential", "credentials",
            "ssn", "social_security",
            "credit_card", "card_number",
            "pin", "pin_code",
            "auth", "authorization",
        ],
        mask_pattern="***MASKED***",
    )
    return DataMasker(config)


class TestSpecSensitiveColumns:
    """spec에 명시된 민감 데이터(비밀번호, 접근키, 토큰 등) 마스킹."""

    @pytest.mark.parametrize("column_name", [
        "password", "passwd", "pwd",
        "secret", "secret_key",
        "token", "access_token", "refresh_token",
        "api_key", "apikey",
        "private_key", "priv_key",
        "credential", "credentials",
    ])
    def test_sensitive_column_masked(self, masker, column_name):
        """민감 컬럼이 마스킹된다."""
        rows = [{column_name: "some_value"}]
        masked = masker.mask_rows(rows)
        assert masked[0][column_name] == "***MASKED***"


class TestAdditionalValuePatterns:
    """추가 값 패턴 마스킹 검증."""

    def test_aws_access_key(self, masker):
        """AWS Access Key 패턴을 마스킹한다."""
        rows = [{"key_field": "AKIAIOSFODNN7EXAMPLE"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["key_field"] == "***MASKED***"

    def test_github_token(self, masker):
        """GitHub Personal Token 패턴을 마스킹한다."""
        rows = [{"some_col": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["some_col"] == "***MASKED***"

    def test_korean_ssn(self, masker):
        """한국 주민번호 패턴을 마스킹한다."""
        rows = [{"data": "920101-1234567"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["data"] == "***MASKED***"

    def test_visa_card_number(self, masker):
        """Visa 카드번호 패턴을 마스킹한다."""
        rows = [{"field": "4111111111111111"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["field"] == "***MASKED***"


class TestPartialMaskingMethods:
    """부분 마스킹 메서드 검증."""

    def test_ip_partial_mask(self, masker):
        """IP 부분 마스킹이 올바르게 동작한다."""
        result = masker._partial_mask_ip("192.168.1.100")
        assert result == "192.168.1.***"

    def test_ip_partial_mask_invalid(self, masker):
        """유효하지 않은 IP는 전체 마스킹한다."""
        result = masker._partial_mask_ip("not-an-ip")
        assert result == "***MASKED***"

    def test_email_partial_mask(self, masker):
        """이메일 부분 마스킹이 올바르게 동작한다."""
        result = masker._partial_mask_email("admin@company.com")
        assert result == "a***n@company.com"

    def test_email_short_local(self, masker):
        """짧은 이메일 로컬 파트를 완전 마스킹한다."""
        result = masker._partial_mask_email("ab@company.com")
        assert result == "**@company.com"

    def test_email_no_at_sign(self, masker):
        """@ 없는 문자열은 전체 마스킹한다."""
        result = masker._partial_mask_email("not-an-email")
        assert result == "***MASKED***"


class TestEdgeCases:
    """경계 조건 테스트."""

    def test_none_value_in_row(self, masker):
        """None 값이 포함된 행을 안전하게 처리한다."""
        rows = [{"hostname": "web-01", "password": None}]
        masked = masker.mask_rows(rows)
        # password는 민감 컬럼이므로 값에 관계없이 마스킹
        assert masked[0]["password"] == "***MASKED***"

    def test_numeric_sensitive_column(self, masker):
        """민감 컬럼의 숫자 값도 마스킹한다."""
        rows = [{"pin": 1234}]
        masked = masker.mask_rows(rows)
        assert masked[0]["pin"] == "***MASKED***"

    def test_empty_string_value(self, masker):
        """빈 문자열도 민감 컬럼이면 마스킹한다."""
        rows = [{"password": ""}]
        masked = masker.mask_rows(rows)
        assert masked[0]["password"] == "***MASKED***"

    def test_nested_sensitive_column_name(self, masker):
        """user_password_hash 같은 중첩 컬럼명도 마스킹한다."""
        rows = [{"user_password_hash": "bcrypt_hash_value"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["user_password_hash"] == "***MASKED***"

    def test_large_dataset(self, masker):
        """대량 데이터 마스킹이 정상 동작한다."""
        rows = [
            {"hostname": f"server-{i}", "token": f"token-{i}"}
            for i in range(1000)
        ]
        masked = masker.mask_rows(rows)
        assert len(masked) == 1000
        assert all(row["token"] == "***MASKED***" for row in masked)
        assert all(row["hostname"].startswith("server-") for row in masked)
