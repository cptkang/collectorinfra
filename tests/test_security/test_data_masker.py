"""민감 데이터 마스킹 테스트.

컬럼명 기반 마스킹과 값 패턴 기반 마스킹을 검증한다.
"""

import pytest

from src.config import SecurityConfig
from src.security.data_masker import DataMasker


@pytest.fixture
def masker():
    config = SecurityConfig(
        sensitive_columns=["password", "secret", "token", "api_key"],
        mask_pattern="***MASKED***",
    )
    return DataMasker(config)


class TestColumnBasedMasking:
    """컬럼명 기반 마스킹 검증."""

    def test_password_column_masked(self, masker):
        rows = [{"hostname": "web-01", "password": "mysecret123"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["password"] == "***MASKED***"
        assert masked[0]["hostname"] == "web-01"

    def test_token_column_masked(self, masker):
        rows = [{"token": "abc123"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["token"] == "***MASKED***"

    def test_api_key_column_masked(self, masker):
        rows = [{"api_key": "sk-test123"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["api_key"] == "***MASKED***"

    def test_secret_column_masked(self, masker):
        rows = [{"secret": "top_secret"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["secret"] == "***MASKED***"

    def test_partial_match_masked(self, masker):
        """부분 매칭: password_hash, api_key_value 등도 마스킹한다."""
        rows = [{"password_hash": "bcrypt_hash", "api_key_value": "key123"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["password_hash"] == "***MASKED***"
        assert masked[0]["api_key_value"] == "***MASKED***"

    def test_case_insensitive(self, masker):
        """대소문자 관계없이 마스킹한다."""
        rows = [{"PASSWORD": "secret"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["PASSWORD"] == "***MASKED***"

    def test_non_sensitive_column_preserved(self, masker):
        """비민감 컬럼은 원본을 유지한다."""
        rows = [{"hostname": "web-01", "ip_address": "10.0.0.1", "usage_pct": 85.3}]
        masked = masker.mask_rows(rows)
        assert masked[0]["hostname"] == "web-01"
        assert masked[0]["ip_address"] == "10.0.0.1"
        assert masked[0]["usage_pct"] == 85.3


class TestValuePatternMasking:
    """값 패턴 기반 마스킹 검증."""

    def test_api_key_pattern(self, masker):
        """sk-로 시작하는 API 키 패턴을 마스킹한다."""
        rows = [{"some_field": "sk-abcdefghijklmnopqrstu"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["some_field"] == "***MASKED***"

    def test_jwt_token_pattern(self, masker):
        """JWT 토큰 패턴을 마스킹한다."""
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        rows = [{"data": jwt}]
        masked = masker.mask_rows(rows)
        assert masked[0]["data"] == "***MASKED***"

    def test_bcrypt_hash_pattern(self, masker):
        """bcrypt 해시 패턴을 마스킹한다."""
        bcrypt_hash = "$2b$12$WApznUPhDubN0oeveSXHpOhJ3bFKJQhGnnjqXYEShJDRSQlPzOyDK"
        rows = [{"hash_val": bcrypt_hash}]
        masked = masker.mask_rows(rows)
        assert masked[0]["hash_val"] == "***MASKED***"

    def test_normal_string_preserved(self, masker):
        """일반 문자열은 마스킹하지 않는다."""
        rows = [{"name": "John Doe", "status": "active"}]
        masked = masker.mask_rows(rows)
        assert masked[0]["name"] == "John Doe"

    def test_numeric_value_preserved(self, masker):
        """숫자 값은 값 패턴 검사를 건너뛴다."""
        rows = [{"count": 42, "rate": 3.14}]
        masked = masker.mask_rows(rows)
        assert masked[0]["count"] == 42
        assert masked[0]["rate"] == 3.14


class TestMaskRowsBulk:
    """다건 마스킹 검증."""

    def test_multiple_rows(self, masker):
        rows = [
            {"hostname": "web-01", "password": "pass1"},
            {"hostname": "web-02", "password": "pass2"},
            {"hostname": "db-01", "password": "pass3"},
        ]
        masked = masker.mask_rows(rows)
        assert len(masked) == 3
        for row in masked:
            assert row["password"] == "***MASKED***"
            assert row["hostname"].startswith(("web-", "db-"))

    def test_empty_rows(self, masker):
        masked = masker.mask_rows([])
        assert masked == []

    def test_original_rows_not_modified(self, masker):
        """원본 데이터가 변경되지 않는다 (새 딕셔너리 반환)."""
        rows = [{"hostname": "web-01", "password": "secret"}]
        masked = masker.mask_rows(rows)
        assert rows[0]["password"] == "secret"  # 원본 보존
        assert masked[0]["password"] == "***MASKED***"
