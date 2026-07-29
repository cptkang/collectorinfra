"""Plan 67 Phase 0 ⑨ — 접속 문자열 비밀번호 평문 노출 차단 회귀 테스트.

검증 대상:
- `_parse_connection_string`이 비밀번호를 평문으로 돌려주지 않는다.
- `GET /admin/settings`가 키 이름에 민감 키워드가 없어도 값에 자격증명이 들어 있으면 마스킹한다.
- 접속 기능 자체(파싱된 host/port/db/user, 연결 문자열 생성)는 그대로 동작한다.
"""

from __future__ import annotations

import pytest

from src.api.routes import admin as admin_routes
from src.api.routes.admin import (
    DbConfigUpdateRequest,
    _build_connection_string,
    _has_url_credentials,
    _MASK_VALUE,
    _parse_connection_string,
    get_settings,
)

_ADMIN = {"sub": "admin", "role": "admin"}
_CONN = "postgresql://polestar:s3cr3t-pw@10.0.0.5:5432/infra"


class TestParseConnectionString:
    def test_password_is_masked(self):
        parsed = _parse_connection_string(_CONN)
        assert parsed["password"] == _MASK_VALUE
        assert "s3cr3t-pw" not in str(parsed)

    def test_other_fields_preserved(self):
        """마스킹은 비밀번호에만 적용되고 접속 정보 파싱은 동작이 같아야 한다."""
        parsed = _parse_connection_string(_CONN)
        assert parsed["db_type"] == "postgresql"
        assert parsed["username"] == "polestar"
        assert parsed["host"] == "10.0.0.5"
        assert parsed["port"] == "5432"
        assert parsed["database"] == "infra"

    def test_empty_password_stays_empty(self):
        parsed = _parse_connection_string("postgresql://user:@db:5432/infra")
        assert parsed["password"] == ""

    def test_empty_connection_string(self):
        assert _parse_connection_string("") == {
            "db_type": "",
            "host": "",
            "port": "5432",
            "database": "",
            "username": "",
            "password": "",
        }

    def test_build_connection_string_unchanged(self):
        """저장 경로는 평문 비밀번호로 연결 문자열을 만들어야 한다(동작 불변)."""
        body = DbConfigUpdateRequest(
            db_type="postgresql",
            host="10.0.0.5",
            port=5432,
            database="infra",
            username="polestar",
            password="s3cr3t-pw",
        )
        assert _build_connection_string(body) == _CONN


class TestUrlCredentialDetection:
    @pytest.mark.parametrize(
        "value",
        [
            _CONN,
            "mysql://u:p@host/db",
            "redis://:pw@redis-host:6379/0",
        ],
    )
    def test_detects_credentials(self, value):
        assert _has_url_credentials(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "http://prometheus:9090",
            "http://host:8080/path@fragment",  # authority 밖의 '@'는 자격증명이 아니다
            "plain-value",
        ],
    )
    def test_ignores_non_credential_values(self, value):
        assert _has_url_credentials(value) is False


class TestGetSettingsMasking:
    async def test_connection_string_password_not_returned(self, monkeypatch, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            f"DB_CONNECTION_STRING={_CONN}\n"
            "LLM_MODEL=llama3.1:8b\n"
            "ADMIN_PASSWORD=plain-admin-pw\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(admin_routes, "_ENV_FILE", env_file)

        response = await get_settings(_ADMIN)
        items = {s.key: s for s in response.settings}

        assert "s3cr3t-pw" not in items["DB_CONNECTION_STRING"].value
        assert items["DB_CONNECTION_STRING"].value == (
            f"postgresql://polestar:{_MASK_VALUE}@10.0.0.5:5432/infra"
        )
        assert items["DB_CONNECTION_STRING"].is_sensitive is True
        # 키 이름 기반 민감 판정과 비민감 값 노출은 기존과 동일해야 한다.
        assert items["ADMIN_PASSWORD"].value == _MASK_VALUE
        assert items["LLM_MODEL"].value == "llama3.1:8b"
        assert items["LLM_MODEL"].is_sensitive is False
