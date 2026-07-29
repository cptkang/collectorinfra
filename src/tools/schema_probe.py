"""실 스키마·샘플 데이터 확인 도구 (Plan 67 Phase S1 §4.2·4.3).

컬럼을 고르기 전에 "그 테이블에 그 컬럼이 실제로 있는지, 값이 어떤 모양인지"를
확인하기 위한 탐색 도구다. DB 접근은 공통 Protocol(`DBClient`)로만 하고 클라이언트를
주입받는다 — 특정 DB 구현에 묶이지 않는다.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from src.db.interface import DBClient

# 샘플 조회 기본 행 수 — 값의 모양만 보면 충분하고 토큰을 아낀다.
DEFAULT_SAMPLE_LIMIT = 5


class RowMasker(Protocol):
    """결과 행의 민감 데이터를 마스킹하는 객체(주입 — 보안 계층 구현체를 받는다)."""

    def mask_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """행 목록의 민감 값을 마스킹해 반환한다."""
        ...


async def get_table_schema(table_name: str, client: DBClient) -> dict:
    """테이블의 컬럼 구조를 조회한다.

    Args:
        table_name: 조회할 테이블명(스키마 접두 포함 가능)
        client: DB 클라이언트(공통 Protocol)

    Returns:
        {"table", "schema", "comment", "row_count_estimate", "columns": [...]}
    """
    info = await client.get_table_schema(table_name)
    return {
        "table": info.name,
        "schema": info.schema_name,
        "comment": info.comment,
        "row_count_estimate": info.row_count_estimate,
        "columns": [
            {
                "name": col.name,
                "data_type": col.data_type,
                "nullable": col.nullable,
                "is_primary_key": col.is_primary_key,
                "comment": col.comment,
            }
            for col in info.columns
        ],
    }


async def get_sample_data(
    table_name: str,
    client: DBClient,
    *,
    limit: int = DEFAULT_SAMPLE_LIMIT,
    masker: Optional[RowMasker] = None,
) -> list[dict[str, Any]]:
    """테이블의 샘플 행을 조회한다(민감 값은 마스커 주입 시 마스킹).

    Args:
        table_name: 조회할 테이블명
        client: DB 클라이언트(공통 Protocol)
        limit: 조회 행 수
        masker: 민감 데이터 마스커(없으면 원본 그대로)

    Returns:
        샘플 행 목록
    """
    rows = await client.get_sample_data(table_name, limit)
    if masker is not None:
        return masker.mask_rows(rows)
    return rows
