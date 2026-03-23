"""DB 클라이언트 공통 인터페이스.

DBHubClient와 PostgresClient가 모두 이 프로토콜을 만족해야 한다.
설정에 따라 적절한 구현체를 선택하여 사용한다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.dbhub.models import QueryResult, SchemaInfo, TableInfo


@runtime_checkable
class DBClient(Protocol):
    """DB 클라이언트 공통 인터페이스.

    DBHubClient와 PostgresClient가 모두 이 프로토콜을 만족해야 한다.
    """

    async def connect(self) -> None:
        """DB 연결을 수립한다."""
        ...

    async def disconnect(self) -> None:
        """DB 연결을 종료한다."""
        ...

    async def health_check(self) -> bool:
        """연결 상태를 확인한다."""
        ...

    async def search_objects(
        self,
        pattern: str = "*",
        object_type: str = "table",
    ) -> list[TableInfo]:
        """DB 객체를 검색한다."""
        ...

    async def get_table_schema(self, table_name: str) -> TableInfo:
        """테이블 상세 스키마를 조회한다."""
        ...

    async def get_full_schema(self) -> SchemaInfo:
        """전체 DB 스키마를 수집한다."""
        ...

    async def get_sample_data(
        self, table_name: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """테이블 샘플 데이터를 조회한다."""
        ...

    async def execute_sql(self, sql: str) -> QueryResult:
        """SQL을 실행하고 결과를 반환한다."""
        ...
