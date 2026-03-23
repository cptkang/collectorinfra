"""멀티 DB 연결 레지스트리.

MCP 서버를 통해 여러 DB의 연결 정보를 통합 관리하고,
라우팅 결과에 따라 적절한 DB 클라이언트를 생성/제공한다.

변경 이력:
- MCP 서버 도입: 연결 문자열을 클라이언트에서 관리하지 않음.
  MCP 서버의 list_sources 도구를 통해 활성 소스를 동적 조회하거나,
  환경변수 ACTIVE_DB_IDS로 명시적으로 설정.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from src.config import AppConfig
from src.db.interface import DBClient
from src.routing.domain_config import DB_DOMAINS, get_domain_by_id

logger = logging.getLogger(__name__)


class DBRegistryError(Exception):
    """DB 레지스트리 관련 에러."""


class DBRegistry:
    """멀티 DB 연결 레지스트리.

    MCP 서버를 통해 각 DB 도메인의 연결을 관리한다.
    DB 식별자에 따라 MCP 클라이언트를 생성하여 해당 소스에 쿼리한다.
    """

    def __init__(self, config: AppConfig) -> None:
        """레지스트리를 초기화한다.

        Args:
            config: 애플리케이션 설정
        """
        self._config = config
        self._active_db_ids: list[str] = []
        self._load_active_sources()

    def _load_active_sources(self) -> None:
        """활성 DB 목록을 로드한다.

        MultiDBConfig의 active_db_ids_csv 또는 환경변수 ACTIVE_DB_IDS에서
        활성 DB 목록을 가져온다. 비어있으면 레거시 단일 DB 모드로 동작.
        """
        self._active_db_ids = self._config.multi_db.get_active_db_ids()

        for db_id in self._active_db_ids:
            domain = get_domain_by_id(db_id)
            display_name = domain.display_name if domain else db_id
            logger.info("DB 등록: %s (%s)", db_id, display_name)

        # 레거시 단일 DB 모드 지원
        if not self._active_db_ids and self._config.db_connection_string:
            self._active_db_ids = ["default"]
            logger.info("레거시 단일 DB 모드: default DB 등록")

    def list_databases(self) -> list[str]:
        """등록된 DB 식별자 목록을 반환한다.

        Returns:
            DB 식별자 목록
        """
        return list(self._active_db_ids)

    def is_registered(self, db_id: str) -> bool:
        """DB 식별자가 등록되어 있는지 확인한다.

        Args:
            db_id: DB 식별자

        Returns:
            등록 여부
        """
        return db_id in self._active_db_ids

    def get_db_info(self, db_id: str) -> dict[str, Any]:
        """DB 식별자에 해당하는 정보를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            DB 정보 딕셔너리
        """
        domain = get_domain_by_id(db_id)
        is_registered = db_id in self._active_db_ids
        return {
            "db_id": db_id,
            "display_name": domain.display_name if domain else db_id,
            "description": domain.description if domain else "",
            "is_active": is_registered,
        }

    def get_all_db_info(self) -> list[dict[str, Any]]:
        """모든 등록된 DB 정보를 반환한다.

        Returns:
            DB 정보 목록
        """
        return [self.get_db_info(db_id) for db_id in self._active_db_ids]

    @asynccontextmanager
    async def get_client(self, db_id: str) -> AsyncGenerator[DBClient, None]:
        """DB 식별자에 해당하는 클라이언트를 생성하고 관리한다.

        MCP 서버를 통해 해당 소스에 연결한다.
        dbhub 모드와 direct 모드를 모두 지원한다.

        Args:
            db_id: DB 식별자

        Yields:
            연결된 DB 클라이언트 인스턴스

        Raises:
            DBRegistryError: 등록되지 않은 DB 식별자
        """
        if db_id not in self._active_db_ids:
            raise DBRegistryError(
                f"등록되지 않은 DB: '{db_id}'. "
                f"사용 가능한 DB: {', '.join(self._active_db_ids)}"
            )

        if self._config.db_backend == "dbhub" or db_id != "default":
            # MCP 서버를 통한 연결: source 이름을 해당 db_id로 설정
            from src.config import DBHubConfig
            from src.dbhub.client import DBHubClient

            dbhub_config = DBHubConfig(
                server_url=self._config.dbhub.server_url,
                source_name=db_id,
                mcp_call_timeout=self._config.dbhub.mcp_call_timeout,
            )
            client = DBHubClient(dbhub_config, self._config.query)
        else:
            # 레거시 direct 모드 (default DB)
            from src.db.client import PostgresClient

            client = PostgresClient(
                dsn=self._config.db_connection_string,
            )

        try:
            await client.connect()
            yield client
        finally:
            await client.disconnect()

    async def health_check(self, db_id: str) -> bool:
        """특정 DB의 연결 상태를 확인한다.

        Args:
            db_id: DB 식별자

        Returns:
            연결 정상 여부
        """
        try:
            async with self.get_client(db_id) as client:
                return await client.health_check()
        except Exception as e:
            logger.warning("DB 헬스체크 실패 (%s): %s", db_id, e)
            return False

    async def health_check_all(self) -> dict[str, bool]:
        """모든 등록된 DB의 연결 상태를 확인한다.

        Returns:
            DB별 연결 상태 딕셔너리 {db_id: bool}
        """
        results = {}
        for db_id in self._active_db_ids:
            results[db_id] = await self.health_check(db_id)
        return results
