"""Plan 33: resource_conf_id JOIN 방지 통합 테스트.

polestar_pg.yaml -> schema_info -> 스키마/가이드 프롬프트까지의
전체 파이프라인을 검증한다.
"""

import pytest
import yaml
from pathlib import Path

from src.utils.schema_utils import build_excluded_join_map
from src.nodes.query_generator import _format_schema_for_prompt, _format_structure_guide


YAML_PATH = Path(__file__).resolve().parent.parent / "config" / "db_profiles" / "polestar_pg.yaml"


@pytest.fixture
def polestar_profile():
    """polestar_pg.yaml을 로드한다."""
    with open(YAML_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _build_test_schema_info(profile: dict) -> dict:
    """프로필에서 테스트용 schema_info를 구성한다."""
    return {
        "tables": {
            "polestar.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "BIGINT", "primary_key": True},
                    {"name": "hostname", "type": "VARCHAR(255)"},
                    {"name": "ipaddress", "type": "VARCHAR(255)"},
                    {"name": "resource_type", "type": "VARCHAR(255)"},
                    {"name": "resource_conf_id", "type": "BIGINT"},
                    {"name": "parent_resource_id", "type": "BIGINT"},
                ],
            },
            "polestar.core_config_prop": {
                "columns": [
                    {"name": "id", "type": "BIGINT", "primary_key": True},
                    {"name": "configuration_id", "type": "BIGINT"},
                    {"name": "name", "type": "VARCHAR(255)"},
                    {"name": "stringvalue_short", "type": "VARCHAR(4000)"},
                    {"name": "stringvalue", "type": "TEXT"},
                    {"name": "is_lob", "type": "BOOLEAN"},
                ],
            },
        },
        "_structure_meta": profile,
    }


class TestYamlConfiguration:
    """polestar_pg.yaml 설정 검증."""

    def test_excluded_join_columns_present(self, polestar_profile):
        """EAV 패턴에 excluded_join_columns가 존재한다."""
        eav_pattern = polestar_profile["patterns"][0]
        assert eav_pattern["type"] == "eav"
        assert "excluded_join_columns" in eav_pattern
        excl = eav_pattern["excluded_join_columns"][0]
        assert excl["column"] == "resource_conf_id"
        assert excl["table"] == "cmm_resource"

    def test_query_guide_prohibits_resource_conf_id(self, polestar_profile):
        """query_guide에 resource_conf_id 금지 문구가 포함되어 있다."""
        guide = polestar_profile["query_guide"]
        assert "resource_conf_id" in guide
        assert "금지" in guide


class TestBuildExcludedJoinMap:
    """build_excluded_join_map() 통합 테스트."""

    def test_with_real_profile(self, polestar_profile):
        """실제 프로필로부터 excluded_join_map이 올바르게 구축된다."""
        schema_info = _build_test_schema_info(polestar_profile)
        result = build_excluded_join_map(schema_info)
        assert ("cmm_resource", "resource_conf_id") in result


class TestSchemaPromptIntegration:
    """스키마 프롬프트 통합 테스트."""

    def test_schema_prompt_has_join_warning(self, polestar_profile):
        """스키마 프롬프트에 JOIN 금지 주석이 포함된다."""
        schema_info = _build_test_schema_info(polestar_profile)
        schema_text = _format_schema_for_prompt(schema_info)
        assert "JOIN 금지" in schema_text
        # resource_conf_id 라인에만 주석이 있어야 함
        lines = schema_text.split("\n")
        for line in lines:
            if "hostname:" in line:
                assert "JOIN 금지" not in line


class TestStructureGuideIntegration:
    """구조 가이드 통합 테스트."""

    def test_structure_guide_has_excluded_join_warning(self, polestar_profile):
        """_format_structure_guide()에 금지 컬럼 경고가 포함된다."""
        guide = _format_structure_guide(polestar_profile)
        assert "resource_conf_id" in guide
        assert "금지" in guide
