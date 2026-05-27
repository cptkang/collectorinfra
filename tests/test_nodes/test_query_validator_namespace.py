"""query_validator 네임스페이스 폴백 및 격리 테스트.

여의도 폴스타(polestar_cm_yd)와 김포 폴스타(polestar_cm_gp)는 동일한 스키마 구조를 가집니다.
SQL에서 polestar.cmm_resource를 참조할 때, 실제 스키마 상에는 polestar_cm_gp.cmm_resource 또는
polestar_cm_yd.cmm_resource로 정의되어 있을 수 있습니다.
이 테스트는 스키마 접두사 차이와 무관하게 bare name fallback 매핑이 올바르게 동작하는지 검증합니다.
또한 한쪽 변경사항이 다른 쪽 질의 검증에 영향을 주지 않음을 보장합니다.
"""

import pytest
from unittest.mock import patch

from src.nodes.query_validator import query_validator
from src.state import create_initial_state


@pytest.fixture
def gp_schema_info() -> dict:
    """김포 폴스타(polestar_cm_gp)를 가정한 스키마 정보."""
    return {
        "tables": {
            "polestar_cm_gp.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "platform_resource_id", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "resource_type", "type": "varchar(100)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
            "polestar_cm_gp.core_config_prop": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "configuration_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "stringvalue_short", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
        },
    }


@pytest.fixture
def yd_schema_info() -> dict:
    """여의도 폴스타(polestar_cm_yd)를 가정한 스키마 정보 (동일한 구조)."""
    return {
        "tables": {
            "polestar_cm_yd.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "platform_resource_id", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "resource_type", "type": "varchar(100)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
            "polestar_cm_yd.core_config_prop": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "configuration_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "stringvalue_short", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_gp_schema_validator_fallback(gp_schema_info):
    """김포 폴스타 스키마 환경에서 'polestar.테이블명' 쿼리가 정상적으로 검증 통과하는지 테스트."""
    state = create_initial_state(user_query="김포 폴스타 질의")
    state["schema_info"] = gp_schema_info
    # SQL은 'polestar' 네임스페이스를 사용하지만, 실제 DB는 'polestar_cm_gp' 접두사를 가진 상태
    state["generated_sql"] = (
        "SELECT "
        "  COALESCE(c.platform_resource_id, c.id) AS id, "
        "  MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Hostname' THEN cc.stringvalue_short END) AS hostname "
        "FROM polestar.cmm_resource c "
        "JOIN polestar.core_config_prop cc ON c.id = cc.configuration_id "
        "WHERE c.resource_type = 'server.Server' "
        "GROUP BY COALESCE(c.platform_resource_id, c.id) "
        "LIMIT 100;"
    )

    with patch("src.nodes.query_validator.load_config") as mock_config:
        mock_config.return_value.query.default_limit = 1000
        result = await query_validator(state)

    assert result["validation_result"]["passed"] is True, f"검증 실패 사유: {result.get('error_message')}"
    assert result["error_message"] is None


@pytest.mark.asyncio
async def test_yd_schema_validator_fallback(yd_schema_info):
    """여의도 폴스타 스키마 환경에서 'polestar.테이블명' 쿼리가 정상적으로 검증 통과하는지 테스트 (여의도 독립성 보장)."""
    state = create_initial_state(user_query="여의도 폴스타 질의")
    state["schema_info"] = yd_schema_info
    # SQL은 'polestar' 네임스페이스를 사용하지만, 실제 DB는 'polestar_cm_yd' 접두사를 가진 상태
    state["generated_sql"] = (
        "SELECT "
        "  COALESCE(c.platform_resource_id, c.id) AS id, "
        "  MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Hostname' THEN cc.stringvalue_short END) AS hostname "
        "FROM polestar.cmm_resource c "
        "JOIN polestar.core_config_prop cc ON c.id = cc.configuration_id "
        "WHERE c.resource_type = 'server.Server' "
        "GROUP BY COALESCE(c.platform_resource_id, c.id) "
        "LIMIT 100;"
    )

    with patch("src.nodes.query_validator.load_config") as mock_config:
        mock_config.return_value.query.default_limit = 1000
        result = await query_validator(state)

    assert result["validation_result"]["passed"] is True, f"검증 실패 사유: {result.get('error_message')}"
    assert result["error_message"] is None


@pytest.mark.asyncio
async def test_invalid_table_rejected_even_with_fallback(gp_schema_info):
    """잘못된 테이블 이름에 대해서는 여전히 차단되는지 테스트."""
    state = create_initial_state(user_query="잘못된 질의")
    state["schema_info"] = gp_schema_info
    # 존재하지 않는 테이블 polestar.nonexistent_table을 사용
    state["generated_sql"] = (
        "SELECT c.id FROM polestar.nonexistent_table c LIMIT 10;"
    )

    with patch("src.nodes.query_validator.load_config") as mock_config:
        mock_config.return_value.query.default_limit = 1000
        result = await query_validator(state)

    assert result["validation_result"]["passed"] is False
    assert "nonexistent_table" in result["validation_result"]["reason"]


@pytest.fixture
def dotless_schema_info() -> dict:
    """스키마 접두사(.)가 없는 테이블 정보를 가정한 스키마 정보 (예: public 스키마)."""
    return {
        "tables": {
            "cmm_resource": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "platform_resource_id", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "resource_type", "type": "varchar(100)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
            "core_config_prop": {
                "columns": [
                    {"name": "id", "type": "integer", "nullable": False, "primary_key": True, "foreign_key": False, "references": None},
                    {"name": "configuration_id", "type": "integer", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "name", "type": "varchar(255)", "nullable": False, "primary_key": False, "foreign_key": False, "references": None},
                    {"name": "stringvalue_short", "type": "varchar(255)", "nullable": True, "primary_key": False, "foreign_key": False, "references": None},
                ],
            },
        },
    }


@pytest.mark.asyncio
async def test_dotless_schema_fallback(dotless_schema_info):
    """실제 DB에 테이블명이 'polestar.' 접두사나 '.' 없이 'core_config_prop'로 존재할 때도, SQL에서 'polestar.core_config_prop' 조회가 통과하는지 검증."""
    state = create_initial_state(user_query="접두사 없는 스키마 질의")
    state["schema_info"] = dotless_schema_info
    state["generated_sql"] = (
        "SELECT "
        "  COALESCE(c.platform_resource_id, c.id) AS id, "
        "  MAX(CASE WHEN c.resource_type = 'server.Server' AND cc.name = 'Hostname' THEN cc.stringvalue_short END) AS hostname "
        "FROM polestar.cmm_resource c "
        "JOIN polestar.core_config_prop cc ON c.id = cc.configuration_id "
        "WHERE c.resource_type = 'server.Server' "
        "GROUP BY COALESCE(c.platform_resource_id, c.id) "
        "LIMIT 100;"
    )

    with patch("src.nodes.query_validator.load_config") as mock_config:
        mock_config.return_value.query.default_limit = 1000
        result = await query_validator(state)

    assert result["validation_result"]["passed"] is True, f"검증 실패 사유: {result.get('error_message')}"
    assert result["error_message"] is None


@pytest.mark.asyncio
async def test_all_query_skips_limit_addition(dotless_schema_info):
    """사용자 질의에 '모든'이 들어간 경우 LIMIT 자동 추가가 생략되는지 테스트."""
    state = create_initial_state(user_query="모든 서버 조회")
    state["schema_info"] = dotless_schema_info
    # LIMIT 절이 없는 쿼리
    state["generated_sql"] = (
        "SELECT c.id FROM cmm_resource c"
    )

    with patch("src.nodes.query_validator.load_config") as mock_config:
        mock_config.return_value.query.default_limit = 1000
        result = await query_validator(state)

    assert result["validation_result"]["passed"] is True
    # LIMIT 1000이 생성된 SQL에 자동으로 덧붙지 않아야 함
    assert "LIMIT" not in result["generated_sql"]


