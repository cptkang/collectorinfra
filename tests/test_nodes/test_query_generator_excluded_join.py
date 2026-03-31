"""_format_schema_for_prompt() 및 _format_structure_guide()의
excluded_join_columns 처리를 검증하는 단위 테스트.
"""

from src.nodes.query_generator import _format_schema_for_prompt, _format_structure_guide


def _make_schema_info_with_excluded():
    """테스트용 schema_info를 생성한다 (excluded_join_columns 포함)."""
    return {
        "tables": {
            "polestar.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "BIGINT", "primary_key": True},
                    {"name": "hostname", "type": "VARCHAR(255)"},
                    {"name": "resource_conf_id", "type": "BIGINT"},
                ],
            },
        },
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                "excluded_join_columns": [
                    {
                        "table": "cmm_resource",
                        "column": "resource_conf_id",
                        "reason": "운영 DB에서 NULL",
                    },
                ]
            }]
        },
    }


class TestFormatSchemaForPrompt:
    """_format_schema_for_prompt()의 excluded_join 주석 테스트."""

    def test_schema_prompt_contains_join_warning(self):
        """resource_conf_id에 JOIN 금지 주석이 포함되어야 한다."""
        schema_info = _make_schema_info_with_excluded()
        result = _format_schema_for_prompt(schema_info)
        assert "resource_conf_id" in result
        assert "JOIN 금지" in result
        assert "운영 DB에서 NULL" in result

    def test_non_excluded_columns_have_no_warning(self):
        """금지 대상이 아닌 컬럼에는 JOIN 금지 주석이 없어야 한다."""
        schema_info = _make_schema_info_with_excluded()
        result = _format_schema_for_prompt(schema_info)
        lines = result.split("\n")
        for line in lines:
            if "id:" in line and "resource_conf_id" not in line:
                assert "JOIN 금지" not in line
            if "hostname:" in line:
                assert "JOIN 금지" not in line

    def test_no_warning_without_excluded_join_columns(self):
        """excluded_join_columns가 없으면 JOIN 금지 주석이 없어야 한다."""
        schema_info = {
            "tables": {
                "polestar.cmm_resource": {
                    "columns": [
                        {"name": "id", "type": "BIGINT", "primary_key": True},
                        {"name": "resource_conf_id", "type": "BIGINT"},
                    ],
                },
            },
        }
        result = _format_schema_for_prompt(schema_info)
        assert "JOIN 금지" not in result

    def test_schema_prefix_stripped_for_matching(self):
        """schema.table 형식에서 bare table name이 올바르게 추출되어 매칭된다."""
        schema_info = {
            "tables": {
                "myschema.cmm_resource": {
                    "columns": [
                        {"name": "resource_conf_id", "type": "BIGINT"},
                    ],
                },
            },
            "_structure_meta": {
                "patterns": [{
                    "type": "eav",
                    "excluded_join_columns": [
                        {"table": "cmm_resource", "column": "resource_conf_id", "reason": "NULL"},
                    ]
                }]
            },
        }
        result = _format_schema_for_prompt(schema_info)
        assert "JOIN 금지" in result


class TestFormatStructureGuide:
    """_format_structure_guide()의 금지 JOIN 컬럼 경고 테스트."""

    def test_structure_guide_contains_excluded_join_warning(self):
        """금지 JOIN 컬럼 섹션이 가이드에 포함되어야 한다."""
        structure_meta = {
            "query_guide": "some guide text",
            "patterns": [{
                "type": "eav",
                "entity_table": "cmm_resource",
                "config_table": "core_config_prop",
                "attribute_column": "name",
                "value_column": "stringvalue_short",
                "excluded_join_columns": [
                    {
                        "table": "cmm_resource",
                        "column": "resource_conf_id",
                        "reason": "운영 DB에서 NULL",
                    },
                ],
            }],
        }
        result = _format_structure_guide(structure_meta)
        assert "금지 JOIN 컬럼" in result
        assert "cmm_resource.resource_conf_id" in result
        assert "운영 DB에서 NULL" in result

    def test_no_warning_without_excluded(self):
        """excluded_join_columns가 없으면 금지 JOIN 컬럼 섹션이 없어야 한다."""
        structure_meta = {
            "query_guide": "some guide text",
            "patterns": [{
                "type": "eav",
                "entity_table": "cmm_resource",
            }],
        }
        result = _format_structure_guide(structure_meta)
        assert "금지 JOIN 컬럼" not in result
