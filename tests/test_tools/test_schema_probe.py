"""실 스키마·샘플 조회 도구 검증 (Plan 67 Phase S1 §4.2·4.3).

DB 클라이언트는 공통 Protocol을 만족하는 목으로 주입한다(실 DB 접속 없음).
"""

from __future__ import annotations

from typing import Any

import pytest

from src.dbhub.models import ColumnInfo, TableInfo
from src.tools.schema_probe import get_sample_data, get_table_schema


class FakeClient:
    """DBClient Protocol 중 도구가 쓰는 두 메서드만 구현한 목."""

    def __init__(self, table: TableInfo, rows: list[dict[str, Any]]) -> None:
        self._table = table
        self._rows = rows
        self.sample_calls: list[tuple[str, int]] = []

    async def get_table_schema(self, table_name: str) -> TableInfo:
        if table_name != self._table.name:
            raise ValueError(f"없는 테이블: {table_name}")
        return self._table

    async def get_sample_data(self, table_name: str, limit: int = 5) -> list[dict[str, Any]]:
        self.sample_calls.append((table_name, limit))
        return self._rows[:limit]


@pytest.fixture
def client() -> FakeClient:
    table = TableInfo(
        name="host",
        schema_name="app",
        columns=[
            ColumnInfo(name="hostname", data_type="varchar", nullable=False, is_primary_key=True),
            ColumnInfo(name="passwd", data_type="varchar"),
        ],
        row_count_estimate=42,
    )
    return FakeClient(table, [{"hostname": "web-01", "passwd": "s3cret"}])


class TestGetTableSchema:
    async def test_returns_column_structure(self, client):
        result = await get_table_schema("host", client)
        assert result["table"] == "host"
        assert result["schema"] == "app"
        assert result["row_count_estimate"] == 42
        assert [c["name"] for c in result["columns"]] == ["hostname", "passwd"]
        assert result["columns"][0]["is_primary_key"] is True

    async def test_unknown_table_propagates(self, client):
        with pytest.raises(ValueError):
            await get_table_schema("없는테이블", client)


class TestGetSampleData:
    async def test_returns_rows(self, client):
        rows = await get_sample_data("host", client, limit=1)
        assert rows == [{"hostname": "web-01", "passwd": "s3cret"}]
        assert client.sample_calls == [("host", 1)]

    async def test_masker_applied_when_injected(self, client):
        from src.config import SecurityConfig
        from src.security.data_masker import DataMasker

        # 검증 대상 필드는 명시 지정한다(.env의 SECURITY_* 값 누수 차단).
        config = SecurityConfig(
            sensitive_columns=["passwd"],
            mask_pattern="***MASKED***",
            mask_ip=False,
            mask_email=False,
        )
        rows = await get_sample_data("host", client, masker=DataMasker(config))
        assert rows[0]["hostname"] == "web-01"
        assert rows[0]["passwd"] == "***MASKED***"
