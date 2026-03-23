"""DB 연결 관리 모듈.

PostgreSQL(asyncpg 풀)과 DB2(ibm_db, asyncio.to_thread 래핑)를 통합 관리한다.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from mcp_server.config import SourceConfig

logger = logging.getLogger(__name__)


class DBPoolManager:
    """데이터소스별 DB 연결 관리.

    PostgreSQL은 asyncpg 풀, DB2는 ibm_db 요청별 연결을 사용한다.
    """

    def __init__(self, sources: list[SourceConfig]) -> None:
        self._sources: dict[str, SourceConfig] = {
            s.name: s for s in sources if s.connection
        }
        self._pg_pools: dict[str, Any] = {}  # asyncpg.Pool
        self._db2_configs: dict[str, SourceConfig] = {}

    async def initialize(self) -> None:
        """활성 소스에 대해 연결을 초기화한다."""
        for name, src in self._sources.items():
            if src.type == "postgresql":
                try:
                    import asyncpg

                    pool = await asyncpg.create_pool(
                        dsn=src.connection,
                        min_size=src.pool_min_size,
                        max_size=src.pool_max_size,
                        command_timeout=src.query_timeout,
                    )
                    self._pg_pools[name] = pool
                    logger.info(
                        "PostgreSQL 풀 초기화 성공: %s (풀 %d-%d)",
                        name,
                        src.pool_min_size,
                        src.pool_max_size,
                    )
                except Exception as e:
                    logger.error("PostgreSQL 풀 초기화 실패 (%s): %s", name, e)
            elif src.type == "db2":
                self._db2_configs[name] = src
                logger.info("DB2 소스 등록: %s (요청별 연결)", name)
            else:
                logger.warning("지원하지 않는 DB 타입: %s (%s)", src.type, name)

    async def execute(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """소스 타입에 따라 적절한 드라이버로 쿼리를 실행한다.

        Args:
            source_name: 데이터소스 이름
            sql: 실행할 SQL

        Returns:
            결과 행 목록

        Raises:
            ValueError: 알 수 없는 소스명
        """
        if source_name in self._pg_pools:
            return await self._execute_pg(source_name, sql)
        elif source_name in self._db2_configs:
            return await self._execute_db2(source_name, sql)
        else:
            raise ValueError(
                f"알 수 없는 소스: {source_name}. "
                f"사용 가능: {list(self._sources.keys())}"
            )

    async def _execute_pg(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """PostgreSQL 쿼리를 실행한다."""
        pool = self._pg_pools[source_name]
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql)
            return [_normalize_row(dict(r)) for r in rows]

    async def _execute_db2(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """DB2 쿼리를 실행한다 (ibm_db를 asyncio.to_thread로 래핑)."""
        src = self._db2_configs[source_name]
        return await asyncio.to_thread(
            self._execute_db2_sync, src.connection, sql
        )

    @staticmethod
    def _execute_db2_sync(conn_str: str, sql: str) -> list[dict[str, Any]]:
        """DB2 동기 쿼리를 실행한다."""
        import ibm_db

        conn = ibm_db.connect(conn_str, "", "")
        try:
            stmt = ibm_db.exec_immediate(conn, sql)
            rows: list[dict[str, Any]] = []
            row = ibm_db.fetch_assoc(stmt)
            while row:
                # DB2는 컬럼명을 대문자로 반환하므로 소문자로 정규화
                normalized = {k.lower(): v for k, v in row.items()}
                rows.append(_normalize_row(normalized))
                row = ibm_db.fetch_assoc(stmt)
            return rows
        finally:
            ibm_db.close(conn)

    def get_source_config(self, source_name: str) -> SourceConfig:
        """소스 설정을 반환한다."""
        if source_name not in self._sources:
            raise ValueError(f"알 수 없는 소스: {source_name}")
        return self._sources[source_name]

    def get_source_type(self, source_name: str) -> str:
        """소스의 DB 타입을 반환한다."""
        return self._sources[source_name].type

    def get_active_sources(self) -> list[str]:
        """활성 소스 이름 목록을 반환한다."""
        return list(self._sources.keys())

    def is_source_active(self, source_name: str) -> bool:
        """소스가 활성 상태인지 확인한다."""
        return source_name in self._sources

    async def health_check(self, source_name: str) -> bool:
        """특정 소스의 연결 상태를 확인한다."""
        try:
            if source_name in self._pg_pools:
                rows = await self._execute_pg(source_name, "SELECT 1 AS ok")
                return len(rows) > 0
            elif source_name in self._db2_configs:
                rows = await self._execute_db2(
                    source_name, "SELECT 1 AS ok FROM SYSIBM.SYSDUMMY1"
                )
                return len(rows) > 0
            return False
        except Exception as e:
            logger.warning("헬스체크 실패 (%s): %s", source_name, e)
            return False

    async def close_all(self) -> None:
        """모든 PostgreSQL 풀을 종료한다.

        DB2는 요청별 연결이므로 별도 종료가 불필요하다.
        """
        for name, pool in self._pg_pools.items():
            try:
                await pool.close()
                logger.info("PostgreSQL 풀 종료: %s", name)
            except Exception as e:
                logger.warning("PostgreSQL 풀 종료 실패 (%s): %s", name, e)
        self._pg_pools.clear()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """행 데이터의 타입을 JSON 직렬화 가능한 형태로 변환한다."""
    for key, val in row.items():
        if isinstance(val, datetime):
            row[key] = val.isoformat()
        elif isinstance(val, date) and not isinstance(val, datetime):
            row[key] = val.isoformat()
        elif isinstance(val, Decimal):
            row[key] = float(val)
        elif isinstance(val, bytes):
            row[key] = val.hex()
    return row
