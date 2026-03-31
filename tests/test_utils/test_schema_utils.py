"""build_excluded_join_map() 유틸리티 함수 단위 테스트."""

from src.utils.schema_utils import build_excluded_join_map


def test_build_excluded_join_map_returns_mapping():
    """excluded_join_columns가 있으면 (table, column) -> reason 매핑을 반환한다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {
                        "table": "cmm_resource",
                        "column": "resource_conf_id",
                        "reason": "NULL",
                    },
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert ("cmm_resource", "resource_conf_id") in result
    assert result[("cmm_resource", "resource_conf_id")] == "NULL"


def test_build_excluded_join_map_empty_when_no_meta():
    """_structure_meta가 없으면 빈 딕셔너리를 반환한다."""
    assert build_excluded_join_map({}) == {}
    assert build_excluded_join_map({"_structure_meta": None}) == {}


def test_build_excluded_join_map_empty_when_no_patterns():
    """patterns가 비어있으면 빈 딕셔너리를 반환한다."""
    schema_info = {
        "_structure_meta": {
            "patterns": []
        }
    }
    assert build_excluded_join_map(schema_info) == {}


def test_build_excluded_join_map_empty_when_no_excluded():
    """excluded_join_columns 필드가 없는 패턴은 무시한다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "entity_table": "cmm_resource",
            }]
        }
    }
    assert build_excluded_join_map(schema_info) == {}


def test_build_excluded_join_map_case_insensitive():
    """테이블/컬럼명은 소문자로 정규화된다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {
                        "table": "CMM_RESOURCE",
                        "column": "RESOURCE_CONF_ID",
                        "reason": "NULL",
                    },
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert ("cmm_resource", "resource_conf_id") in result


def test_build_excluded_join_map_multiple_entries():
    """여러 금지 컬럼이 모두 매핑된다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "t1", "column": "c1", "reason": "reason1"},
                    {"table": "t2", "column": "c2", "reason": "reason2"},
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert len(result) == 2
    assert result[("t1", "c1")] == "reason1"
    assert result[("t2", "c2")] == "reason2"


def test_build_excluded_join_map_default_reason():
    """reason 필드가 없으면 'NULL'이 기본값이다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "t1", "column": "c1"},
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert result[("t1", "c1")] == "NULL"


def test_build_excluded_join_map_skips_empty_table_or_column():
    """table 또는 column이 빈 문자열이면 무시한다."""
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "", "column": "c1", "reason": "x"},
                    {"table": "t1", "column": "", "reason": "y"},
                    {"table": "t2", "column": "c2", "reason": "z"},
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert len(result) == 1
    assert ("t2", "c2") in result
