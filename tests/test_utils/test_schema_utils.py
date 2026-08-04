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


# ── safe_sample_preview (2026-08-04: b0 CLOB 샘플 → scrub_pii 동결 방호) ──

from src.utils.schema_utils import (  # noqa: E402
    SAMPLE_PREVIEW_MAX_CHARS,
    SAMPLE_VALUE_MAX_CHARS,
    safe_sample_preview,
)


def test_safe_sample_preview_passes_small_samples_unchanged():
    """상한 이하 샘플은 값 손실 없이 JSON으로 직렬화된다."""
    rows = [{"hostname": "web01", "cpu": 4}, {"hostname": "web02", "cpu": 8}]
    preview = safe_sample_preview(rows)
    assert "web01" in preview and "web02" in preview
    assert "절단" not in preview


def test_safe_sample_preview_caps_long_values():
    """CLOB성 긴 값은 SAMPLE_VALUE_MAX_CHARS로 절단되고 표식이 남는다."""
    rows = [{"stringvalue_long": "x" * 100_000}]
    preview = safe_sample_preview(rows)
    assert "…(절단)" in preview
    assert len(preview) <= SAMPLE_PREVIEW_MAX_CHARS + 50


def test_safe_sample_preview_caps_total_size():
    """칼럼이 많아 총량이 넘치면 전체 프리뷰도 상한으로 절단된다."""
    rows = [{f"col{i}": "v" * SAMPLE_VALUE_MAX_CHARS for i in range(100)}]
    preview = safe_sample_preview(rows)
    assert len(preview) <= SAMPLE_PREVIEW_MAX_CHARS + 50
    assert "…(샘플 절단)" in preview


def test_safe_sample_preview_limits_rows_and_tolerates_non_dict():
    """max_rows 초과 행은 버리고, dict가 아닌 행도 직렬화를 깨지 않는다."""
    rows = [{"a": 1}, {"a": 2}, {"a": 3}, {"a": 4}, "raw-string", None]
    preview = safe_sample_preview(rows, max_rows=3)
    assert '"a": 3' in preview
    assert '"a": 4' not in preview
