"""PostgreSQL 직접 연결 클라이언트.

DBHub(MCP 서버) 없이 PostgreSQL에 직접 연결하여
스키마 조회 및 SQL 실행을 수행한다.
DBHubClient와 동일한 인터페이스를 제공하여 교체 가능하다.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, AsyncGenerator, Optional

import asyncpg

from src.config import AppConfig
from src.dbhub.models import (
    ColumnInfo,
    DBConnectionError,
    DBHubError,
    QueryExecutionError,
    QueryResult,
    QueryTimeoutError,
    SchemaInfo,
    TableInfo,
)

logger = logging.getLogger(__name__)

_VALID_TABLE_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


class PostgresClient:
    """PostgreSQL 직접 연결 클라이언트.

    DBHubClient와 동일한 퍼블릭 메서드를 제공하여
    설정에 따라 교체 가능하다.
    """

    def __init__(self, dsn: str, query_timeout: int = 30, max_rows: int = 10000) -> None:
        self._dsn = dsn
        self._query_timeout = query_timeout
        self._max_rows = max_rows
        self._pool: Optional[asyncpg.Pool] = None
        self._connected: bool = False

    async def connect(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(
                self._dsn,
                min_size=1,
                max_size=5,
                command_timeout=self._query_timeout,
            )
            self._connected = True
            logger.info("PostgreSQL 직접 연결 성공")
        except Exception as e:
            raise DBConnectionError(f"PostgreSQL 연결 실패: {e}") from e

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
        self._connected = False
        logger.info("PostgreSQL 연결 종료")

    HEALTH_CHECK_TIMEOUT: int = 5  # 초

    async def health_check(self) -> bool:
        """연결 상태를 확인한다. 5초 이내 응답하지 않으면 실패로 판단한다.

        Returns:
            연결 정상 여부
        """
        try:
            await asyncio.wait_for(
                self.execute_sql("SELECT 1"),
                timeout=self.HEALTH_CHECK_TIMEOUT,
            )
            return True
        except Exception:
            return False

    async def search_objects(
        self,
        pattern: str = "*",
        object_type: str = "table",
    ) -> list[TableInfo]:
        self._ensure_connected()
        sql = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """
        result = await self.execute_sql(sql)
        return [TableInfo(name=row["table_name"]) for row in result.rows]

    async def get_table_schema(self, table_name: str) -> TableInfo:
        self._ensure_connected()
        import re
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            raise DBHubError(f"유효하지 않은 테이블명: {table_name}")

        col_sql = """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
        """
        pk_sql = """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = 'public'
              AND tc.table_name = $1
              AND tc.constraint_type = 'PRIMARY KEY'
        """

        async with self._pool.acquire() as conn:
            col_rows = await conn.fetch(col_sql, table_name)
            pk_rows = await conn.fetch(pk_sql, table_name)

        pk_columns = {row["column_name"] for row in pk_rows}

        columns = [
            ColumnInfo(
                name=row["column_name"],
                data_type=row["data_type"],
                nullable=(row["is_nullable"] == "YES"),
                is_primary_key=(row["column_name"] in pk_columns),
            )
            for row in col_rows
        ]

        return TableInfo(name=table_name, columns=columns)

    async def get_full_schema(self) -> SchemaInfo:
        tables_list = await self.search_objects()
        schema = SchemaInfo()

        for table_brief in tables_list:
            table_detail = await self.get_table_schema(table_brief.name)
            schema.tables[table_detail.name] = table_detail

        schema.relationships = await self._get_foreign_keys()
        return schema

    async def get_sample_data(self, table_name: str, limit: int = 5) -> list[dict[str, Any]]:
        """테이블 샘플 데이터를 안전하게 조회한다.

        Args:
            table_name: 테이블명
            limit: 조회 행 수 (기본 5건)

        Returns:
            샘플 데이터 행 목록

        Raises:
            DBHubError: 유효하지 않은 테이블명일 때
        """
        # 테이블명 검증 추가 (SQL 인젝션 방어)
        if not _VALID_TABLE_NAME.match(table_name):
            raise DBHubError(f"유효하지 않은 테이블명: {table_name}")

        result = await self.execute_sql(
            f"SELECT * FROM {table_name} LIMIT {limit}"
        )
        return result.rows

    async def execute_sql(self, sql: str) -> QueryResult:
        self._ensure_connected()
        start_time = time.time()

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(sql)

            elapsed_ms = (time.time() - start_time) * 1000

            if not rows:
                return QueryResult(
                    columns=[],
                    rows=[],
                    row_count=0,
                    execution_time_ms=elapsed_ms,
                )

            columns = list(rows[0].keys())
            result_rows = [dict(row) for row in rows]

            # 타입 변환 (Decimal -> float, datetime -> ISO string 등)
            for row in result_rows:
                for key, val in row.items():
                    if isinstance(val, datetime):
                        row[key] = val.isoformat()
                    elif isinstance(val, date) and not isinstance(val, datetime):
                        row[key] = val.isoformat()
                    elif hasattr(val, '__float__'):
                        try:
                            row[key] = float(val)
                        except (ValueError, TypeError):
                            pass

            truncated = len(result_rows) >= self._max_rows

            return QueryResult(
                columns=columns,
                rows=result_rows,
                row_count=len(result_rows),
                execution_time_ms=elapsed_ms,
                truncated=truncated,
            )

        except asyncpg.exceptions.QueryCanceledError as e:
            raise QueryTimeoutError(
                f"쿼리 타임아웃 ({self._query_timeout}초 초과)"
            ) from e
        except asyncpg.exceptions.PostgresSyntaxError as e:
            raise QueryExecutionError(str(e), sql) from e
        except (QueryTimeoutError, QueryExecutionError):
            raise
        except Exception as e:
            raise QueryExecutionError(str(e), sql) from e

    def _ensure_connected(self) -> None:
        if not self._connected or not self._pool:
            raise DBConnectionError(
                "PostgreSQL에 연결되지 않았습니다. connect()를 먼저 호출하세요."
            )

    async def _get_foreign_keys(self) -> list[dict[str, str]]:
        fk_sql = """
            SELECT
                tc.table_name AS from_table,
                kcu.column_name AS from_column,
                ccu.table_name AS to_table,
                ccu.column_name AS to_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = 'public'
        """
        try:
            result = await self.execute_sql(fk_sql)
            return [
                {
                    "from": f"{row['from_table']}.{row['from_column']}",
                    "to": f"{row['to_table']}.{row['to_column']}",
                }
                for row in result.rows
            ]
        except Exception:
            logger.warning("FK 관계 조회 실패, 빈 목록 반환")
            return []


@asynccontextmanager
async def get_postgres_client(
    config: AppConfig,
) -> AsyncGenerator[PostgresClient, None]:
    """PostgreSQL 클라이언트를 생성하고 연결을 관리한다.

    Args:
        config: 애플리케이션 설정

    Yields:
        연결된 PostgresClient 인스턴스
    """
    client = PostgresClient(
        dsn=config.db_connection_string,
    )
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
