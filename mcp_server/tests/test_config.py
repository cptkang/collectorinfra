"""config.py 테스트.

TOML 설정 로딩 및 환경변수 오버라이드를 검증한다.
"""

import os
import tempfile
from pathlib import Path

import pytest

from mcp_server.config import (
    AppServerConfig,
    ServerConfig,
    SourceConfig,
    load_config,
    _load_toml,
    _apply_env_overrides,
)


class TestLoadToml:
    """TOML 파일 로딩 테스트."""

    def test_load_valid_toml(self, tmp_path):
        """유효한 TOML 파일을 올바르게 파싱한다."""
        toml_content = """
[server]
name = "test-server"
host = "127.0.0.1"
port = 8080
transport = "sse"
log_level = "debug"

[[sources]]
name = "test_db"
type = "postgresql"
connection = "postgresql://user:pass@localhost/test"
readonly = true
query_timeout = 15
max_rows = 5000
pool_min_size = 2
pool_max_size = 10
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)

        config = _load_toml(config_file)

        assert config.server.name == "test-server"
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 8080
        assert config.server.transport == "sse"
        assert config.server.log_level == "debug"

        assert len(config.sources) == 1
        src = config.sources[0]
        assert src.name == "test_db"
        assert src.type == "postgresql"
        assert src.connection == "postgresql://user:pass@localhost/test"
        assert src.readonly is True
        assert src.query_timeout == 15
        assert src.max_rows == 5000
        assert src.pool_min_size == 2
        assert src.pool_max_size == 10

    def test_load_multiple_sources(self, tmp_path):
        """여러 소스가 정의된 TOML을 파싱한다."""
        toml_content = """
[server]
name = "multi"

[[sources]]
name = "pg_db"
type = "postgresql"

[[sources]]
name = "db2_db"
type = "db2"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)

        config = _load_toml(config_file)
        assert len(config.sources) == 2
        assert config.sources[0].name == "pg_db"
        assert config.sources[0].type == "postgresql"
        assert config.sources[1].name == "db2_db"
        assert config.sources[1].type == "db2"

    def test_defaults_for_missing_fields(self, tmp_path):
        """누락된 필드는 기본값으로 채워진다."""
        toml_content = """
[[sources]]
name = "minimal"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)

        config = _load_toml(config_file)
        src = config.sources[0]
        assert src.type == "postgresql"
        assert src.readonly is True
        assert src.query_timeout == 30
        assert src.max_rows == 10000
        assert src.pool_min_size == 1
        assert src.pool_max_size == 5


class TestEnvOverrides:
    """환경변수 오버라이드 테스트."""

    def test_env_override_connection(self, monkeypatch):
        """환경변수로 연결 문자열을 오버라이드한다."""
        monkeypatch.setenv(
            "INFRA_DB_CONNECTION", "postgresql://env_user:pass@env_host/db"
        )

        config = AppServerConfig(
            sources=[
                SourceConfig(name="infra_db", type="postgresql", connection=""),
            ]
        )
        _apply_env_overrides(config)

        assert config.sources[0].connection == "postgresql://env_user:pass@env_host/db"

    def test_env_does_not_override_existing(self, monkeypatch):
        """TOML에 값이 있더라도 환경변수가 있으면 오버라이드한다."""
        monkeypatch.setenv(
            "TEST_DB_CONNECTION", "postgresql://from_env/db"
        )

        config = AppServerConfig(
            sources=[
                SourceConfig(
                    name="test_db",
                    type="postgresql",
                    connection="postgresql://from_toml/db",
                ),
            ]
        )
        _apply_env_overrides(config)

        # 환경변수가 설정되어 있으면 오버라이드
        assert config.sources[0].connection == "postgresql://from_env/db"

    def test_no_env_keeps_toml_value(self):
        """환경변수가 없으면 TOML 값을 유지한다."""
        config = AppServerConfig(
            sources=[
                SourceConfig(
                    name="keep_db",
                    type="postgresql",
                    connection="postgresql://toml_value/db",
                ),
            ]
        )
        _apply_env_overrides(config)

        assert config.sources[0].connection == "postgresql://toml_value/db"


class TestLoadConfig:
    """load_config 통합 테스트."""

    def test_inactive_sources_filtered(self, tmp_path):
        """연결 문자열이 없는 소스는 필터링된다."""
        toml_content = """
[server]
name = "filter-test"

[[sources]]
name = "active_db"
type = "postgresql"
connection = "postgresql://user:pass@host/db"

[[sources]]
name = "inactive_db"
type = "postgresql"
"""
        config_file = tmp_path / "config.toml"
        config_file.write_text(toml_content)

        config = load_config(config_file)

        assert len(config.sources) == 1
        assert config.sources[0].name == "active_db"

    def test_missing_config_file(self, tmp_path):
        """설정 파일이 없으면 기본값으로 생성된다."""
        config = load_config(tmp_path / "nonexistent.toml")
        assert config.server.name == "dbhub-server"
        assert len(config.sources) == 0
