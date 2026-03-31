"""직접 PostgreSQL 연결 클라이언트 및 DB 클라이언트 팩토리.

설정에 따라 DBHub 또는 직접 연결 클라이언트를 반환하는 통합 팩토리를 제공한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from src.config import AppConfig, DBHubConfig
from src.db.client import PostgresClient, get_postgres_client
from src.db.interface import DBClient


@asynccontextmanager
async def get_db_client(
    config: AppConfig,
    *,
    db_id: str | None = None,
) -> AsyncGenerator[DBClient, None]:
    """설정에 따라 적절한 DB 클라이언트를 생성하고 관리한다.

    Args:
        config: 애플리케이션 설정
        db_id: 대상 DB 식별자 (멀티 DB 모드에서 source_name 오버라이드용, 선택)

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

        dbhub_config = config.dbhub
        # db_id가 지정되면 해당 source_name으로 오버라이드
        if db_id and db_id != dbhub_config.source_name:
            dbhub_config = DBHubConfig(
                server_url=dbhub_config.server_url,
                source_name=db_id,
                mcp_call_timeout=dbhub_config.mcp_call_timeout,
            )
        client = DBHubClient(dbhub_config, config.query)

    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()


__all__ = ["PostgresClient", "get_postgres_client", "get_db_client", "DBClient"]
