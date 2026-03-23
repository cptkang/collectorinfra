"""db_registry 모듈 테스트.

MCP 서버 도입 후 변경:
- MultiDBConfig에서 연결 문자열 제거
- active_db_ids_csv로 활성 DB 관리
- DBRegistry가 MCP 서버를 통해 DB에 연결
"""

import pytest

from src.config import AppConfig, MultiDBConfig
from src.routing.db_registry import DBRegistry, DBRegistryError


def _make_config(**overrides) -> AppConfig:
    """테스트용 AppConfig를 생성한다."""
    active_ids = overrides.get("active_db_ids", [])
    multi_db = MultiDBConfig(
        active_db_ids_csv=",".join(active_ids) if active_ids else "",
    )
    return AppConfig(
        multi_db=multi_db,
        db_connection_string=overrides.get("db_connection_string", ""),
    )


class TestDBRegistry:
    """DBRegistry 테스트."""

    def test_no_connections(self):
        """연결 설정이 없으면 빈 레지스트리가 된다."""
        config = _make_config()
        registry = DBRegistry(config)
        assert registry.list_databases() == []

    def test_single_db_registered(self):
        """단일 DB가 활성화되면 해당 DB만 등록된다."""
        config = _make_config(active_db_ids=["polestar"])
        registry = DBRegistry(config)
        assert "polestar" in registry.list_databases()
        assert len(registry.list_databases()) == 1

    def test_multiple_dbs_registered(self):
        """여러 DB가 활성화되면 모두 등록된다."""
        config = _make_config(
            active_db_ids=["polestar", "cloud_portal", "itsm"]
        )
        registry = DBRegistry(config)
        dbs = registry.list_databases()
        assert "polestar" in dbs
        assert "cloud_portal" in dbs
        assert "itsm" in dbs
        assert len(dbs) == 3

    def test_legacy_fallback(self):
        """멀티 DB 미설정 시 레거시 단일 DB로 폴백한다."""
        config = _make_config(
            db_connection_string="postgresql://localhost/infra_db"
        )
        registry = DBRegistry(config)
        assert "default" in registry.list_databases()

    def test_is_registered(self):
        """등록 여부를 정확히 판별한다."""
        config = _make_config(active_db_ids=["polestar"])
        registry = DBRegistry(config)
        assert registry.is_registered("polestar") is True
        assert registry.is_registered("cloud_portal") is False
        assert registry.is_registered("nonexistent") is False

    def test_get_db_info(self):
        """DB 정보를 올바르게 반환한다."""
        config = _make_config(active_db_ids=["polestar"])
        registry = DBRegistry(config)
        info = registry.get_db_info("polestar")
        assert info["db_id"] == "polestar"
        assert info["display_name"] == "Polestar DB"
        assert info["is_active"] is True

    def test_get_all_db_info(self):
        """모든 등록된 DB 정보를 반환한다."""
        config = _make_config(
            active_db_ids=["polestar", "itsm"]
        )
        registry = DBRegistry(config)
        all_info = registry.get_all_db_info()
        assert len(all_info) == 2
        db_ids = [info["db_id"] for info in all_info]
        assert "polestar" in db_ids
        assert "itsm" in db_ids


class TestMultiDBConfig:
    """MultiDBConfig 테스트."""

    def test_get_active_db_ids(self):
        """활성 DB 식별자만 반환한다."""
        config = MultiDBConfig(
            active_db_ids_csv="polestar,itsm",
        )
        active = config.get_active_db_ids()
        assert "polestar" in active
        assert "itsm" in active
        assert "cloud_portal" not in active
        assert "itam" not in active

    def test_no_active_dbs(self):
        """연결이 없으면 빈 목록을 반환한다."""
        config = MultiDBConfig()
        assert config.get_active_db_ids() == []

    def test_csv_parsing(self):
        """쉼표 구분 문자열을 올바르게 파싱한다."""
        config = MultiDBConfig(
            active_db_ids_csv="polestar, cloud_portal , itsm",
        )
        active = config.get_active_db_ids()
        assert active == ["polestar", "cloud_portal", "itsm"]

    def test_empty_csv(self):
        """빈 문자열이면 빈 목록을 반환한다."""
        config = MultiDBConfig(active_db_ids_csv="")
        assert config.get_active_db_ids() == []
