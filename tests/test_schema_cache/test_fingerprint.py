"""fingerprint 모듈 테스트."""

from __future__ import annotations

import pytest

from src.schema_cache.fingerprint import (
    compute_fingerprint,
    compute_fingerprint_from_schema_dict,
)


class TestComputeFingerprint:
    """compute_fingerprint 함수 테스트."""

    def test_empty_rows_returns_consistent_hash(self) -> None:
        """빈 행 목록에 대해 일관된 해시를 반환한다."""
        fp1 = compute_fingerprint([])
        fp2 = compute_fingerprint([])
        assert fp1 == fp2
        assert len(fp1) == 64  # SHA-256 hex

    def test_same_data_produces_same_hash(self) -> None:
        """동일한 데이터는 동일한 해시를 생성한다."""
        rows = [
            {"table_name": "servers", "column_count": 4},
            {"table_name": "cpu_metrics", "column_count": 5},
        ]
        assert compute_fingerprint(rows) == compute_fingerprint(rows)

    def test_different_data_produces_different_hash(self) -> None:
        """다른 데이터는 다른 해시를 생성한다."""
        rows1 = [{"table_name": "servers", "column_count": 4}]
        rows2 = [{"table_name": "servers", "column_count": 5}]
        assert compute_fingerprint(rows1) != compute_fingerprint(rows2)

    def test_order_independent(self) -> None:
        """행 순서에 관계없이 동일한 해시를 생성한다."""
        rows_a = [
            {"table_name": "cpu_metrics", "column_count": 5},
            {"table_name": "servers", "column_count": 4},
        ]
        rows_b = [
            {"table_name": "servers", "column_count": 4},
            {"table_name": "cpu_metrics", "column_count": 5},
        ]
        assert compute_fingerprint(rows_a) == compute_fingerprint(rows_b)

    def test_extra_fields_ignored(self) -> None:
        """예상 외 필드는 무시되고 결과에 영향 없다."""
        rows_base = [{"table_name": "servers", "column_count": 4}]
        rows_extra = [{"table_name": "servers", "column_count": 4, "extra": "value"}]
        assert compute_fingerprint(rows_base) == compute_fingerprint(rows_extra)

    def test_new_table_changes_hash(self) -> None:
        """테이블 추가 시 해시가 변경된다."""
        rows_before = [{"table_name": "servers", "column_count": 4}]
        rows_after = [
            {"table_name": "servers", "column_count": 4},
            {"table_name": "new_table", "column_count": 3},
        ]
        assert compute_fingerprint(rows_before) != compute_fingerprint(rows_after)


class TestComputeFingerprintFromSchemaDict:
    """compute_fingerprint_from_schema_dict 함수 테스트."""

    def test_basic_schema_dict(self) -> None:
        """기본 스키마 딕셔너리에서 fingerprint를 생성한다."""
        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [
                        {"name": "id", "type": "integer"},
                        {"name": "hostname", "type": "varchar"},
                    ],
                },
                "cpu_metrics": {
                    "columns": [
                        {"name": "id", "type": "integer"},
                        {"name": "server_id", "type": "integer"},
                        {"name": "usage_pct", "type": "double"},
                    ],
                },
            },
        }
        fp = compute_fingerprint_from_schema_dict(schema_dict)
        assert len(fp) == 64

    def test_consistent_with_compute_fingerprint(self) -> None:
        """DB 쿼리 결과 기반 fingerprint와 동일한 결과를 생성한다."""
        schema_dict = {
            "tables": {
                "servers": {
                    "columns": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
                },
            },
        }
        rows = [{"table_name": "servers", "column_count": 3}]
        assert compute_fingerprint_from_schema_dict(schema_dict) == compute_fingerprint(rows)

    def test_empty_schema(self) -> None:
        """빈 스키마에서도 유효한 해시를 반환한다."""
        fp = compute_fingerprint_from_schema_dict({"tables": {}})
        assert len(fp) == 64

    def test_missing_tables_key(self) -> None:
        """tables 키가 없으면 빈 스키마로 처리한다."""
        fp = compute_fingerprint_from_schema_dict({})
        assert len(fp) == 64
