"""직접 PostgreSQL 연결 클라이언트 및 DB 클라이언트 팩토리.

설정에 따라 DBHub 또는 직접 연결 클라이언트를 반환하는 통합 팩토리를 제공한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from src.config import AppConfig
from src.db.client import PostgresClient, get_postgres_client
from src.db.interface import DBClient


@asynccontextmanager
async def get_db_client(config: AppConfig) -> AsyncGenerator[DBClient, None]:
    """설정에 따라 적절한 DB 클라이언트를 생성하고 관리한다.

    Args:
        config: 애플리케이션 설정

    Yields:
        연결된 DB 클라이언트 인스턴스
    """
    if config.db_backend == "direct":
        from src.db.client import PostgresClient

        client = PostgresClient(
            dsn=config.db_connection_string,
        )
    else:
        from src.dbhub.client import DBHubClient

        client = DBHubClient(config.dbhub, config.query)

    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()


__all__ = ["PostgresClient", "get_postgres_client", "get_db_client", "DBClient"]
