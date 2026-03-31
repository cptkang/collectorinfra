"""DBHub 연동에 사용하는 데이터 모델.

스키마 정보, 쿼리 결과, 에러 타입 등을 정의한다.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    """테이블 컬럼 정보."""

    name: str
    data_type: str
    nullable: bool = True
    is_primary_key: bool = False
    is_foreign_key: bool = False
    references: Optional[str] = None
    comment: Optional[str] = None


class TableInfo(BaseModel):
    """테이블 메타데이터."""

    name: str
    schema_name: str = "public"
    columns: list[ColumnInfo] = Field(default_factory=list)
    row_count_estimate: Optional[int] = None
    comment: Optional[str] = None


class SchemaInfo(BaseModel):
    """DB 스키마 전체 정보."""

    tables: dict[str, TableInfo] = Field(default_factory=dict)
    relationships: list[dict[str, str]] = Field(default_factory=list)


class QueryResult(BaseModel):
    """쿼리 실행 결과."""

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: Optional[float] = None
    truncated: bool = False
    source_name: Optional[str] = None  # 어떤 DB 소스에서 실행되었는지


# --- 예외 클래스 ---


class DBHubError(Exception):
    """DBHub 관련 에러의 기본 클래스."""

    pass


class DBConnectionError(DBHubError):
    """DB 연결 실패."""

    pass



class QueryTimeoutError(DBHubError):
    """쿼리 타임아웃 초과."""

    pass


class QueryExecutionError(DBHubError):
    """쿼리 실행 에러."""

    def __init__(self, message: str, sql: str = "") -> None:
        """쿼리 실행 에러를 생성한다.

        Args:
            message: 에러 메시지
            sql: 실패한 SQL 쿼리
        """
        self.sql = sql
        super().__init__(message)


def schema_to_dict(
    schema: SchemaInfo,
    relevant_tables: list[str],
) -> dict[str, Any]:
    """SchemaInfo를 dict로 변환한다 (State에 저장 가능한 형태).

    Args:
        schema: SchemaInfo 인스턴스
        relevant_tables: 포함할 테이블 목록

    Returns:
        스키마 딕셔너리
    """
    tables_dict: dict[str, Any] = {}
    for table_name in relevant_tables:
        if table_name in schema.tables:
            table = schema.tables[table_name]
            tables_dict[table_name] = {
                "columns": [
                    {
                        "name": col.name,
                        "type": col.data_type,
                        "nullable": col.nullable,
                        "primary_key": col.is_primary_key,
                        "foreign_key": col.is_foreign_key,
                        "references": col.references,
                    }
                    for col in table.columns
                ],
                "row_count_estimate": table.row_count_estimate,
                "sample_data": [],
            }

    relevant_set = set(relevant_tables)
    relationships = [
        rel
        for rel in schema.relationships
        if (
            rel["from"].split(".")[0] in relevant_set
            and rel["to"].split(".")[0] in relevant_set
        )
    ]

    return {
        "tables": tables_dict,
        "relationships": relationships,
    }
