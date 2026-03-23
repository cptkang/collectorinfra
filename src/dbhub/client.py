"""DBHub MCP 클라이언트.

원격 MCP 서버에 SSE transport로 연결하여 스키마 조회 및 SQL 실행을 수행한다.
MCP 프로토콜의 tool call 인터페이스를 사용한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional

from src.config import DBHubConfig, QueryConfig
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


class DBHubClient:
    """DBHub MCP 서버 클라이언트.

    원격 MCP 서버와 SSE transport로 통신한다.
    search_objects로 스키마를 조회하고, execute_sql로 쿼리를 실행한다.
    get_table_schema로 테이블 상세 스키마를 서버 도구 1회 호출로 조회한다.
    """

    MAX_RECONNECT_ATTEMPTS: int = 3
    RECONNECT_DELAY: float = 2.0  # 초
    HEALTH_CHECK_TIMEOUT: int = 5  # 초

    def __init__(
        self,
        dbhub_config: DBHubConfig,
        query_config: QueryConfig | None = None,
    ) -> None:
        """클라이언트를 초기화한다.

        Args:
            dbhub_config: DBHub 연결 설정 (MCP 서버 URL 포함)
            query_config: 쿼리 제한 설정 (선택, 재시도/기본 LIMIT용)
        """
        self._config = dbhub_config
        self._query_config = query_config or QueryConfig()
        self._mcp_session: Optional[Any] = None
        self._connected: bool = False
        self._sse_context: Optional[Any] = None
        self._session_context: Optional[Any] = None

    async def connect(self) -> None:
        """MCP 서버에 SSE transport로 연결한다.

        Raises:
            DBConnectionError: 연결 실패 시
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            # SSE 클라이언트로 원격 MCP 서버에 연결
            self._sse_context = sse_client(url=self._config.server_url)
            sse_transport = await self._sse_context.__aenter__()
            read_stream, write_stream = sse_transport

            # MCP 세션 생성 및 초기화
            self._session_context = ClientSession(read_stream, write_stream)
            self._mcp_session = await self._session_context.__aenter__()
            await self._mcp_session.initialize()

            self._connected = True
            logger.info(
                "MCP 서버 연결 성공 (SSE): %s", self._config.server_url
            )
        except ImportError:
            # MCP SDK가 설치되지 않은 경우 폴백 모드
            logger.warning(
                "MCP SDK가 설치되지 않았습니다. DBHub 클라이언트가 제한 모드로 동작합니다."
            )
            self._connected = True
        except Exception as e:
            raise DBConnectionError(
                f"MCP 서버 연결 실패 ({self._config.server_url}): {e}"
            ) from e

    async def disconnect(self) -> None:
        """MCP 서버 연결을 종료한다."""
        try:
            if self._session_context:
                await self._session_context.__aexit__(None, None, None)
            if self._sse_context:
                await self._sse_context.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"MCP 서버 연결 종료 중 에러: {e}")
        finally:
            self._mcp_session = None
            self._sse_context = None
            self._session_context = None
            self._connected = False
            logger.info("MCP 서버 연결 종료")

    async def health_check(self) -> bool:
        """연결 상태를 확인한다. 5초 이내 응답하지 않으면 실패로 판단한다.

        Returns:
            연결 정상 여부
        """
        try:
            result = await asyncio.wait_for(
                self._call_tool(
                    "health_check",
                    {"source": self._config.source_name},
                ),
                timeout=self.HEALTH_CHECK_TIMEOUT,
            )
            parsed = self._parse_json_result(result)
            return parsed.get("status") == "healthy"
        except Exception:
            return False

    async def _ensure_connected_with_retry(self) -> None:
        """연결 상태를 확인하고 필요 시 재연결한다.

        Raises:
            DBConnectionError: 최대 재연결 시도 초과 시
        """
        if self._connected and self._mcp_session:
            return

        for attempt in range(self.MAX_RECONNECT_ATTEMPTS):
            try:
                await self.connect()
                return
            except Exception as e:
                if attempt < self.MAX_RECONNECT_ATTEMPTS - 1:
                    delay = self.RECONNECT_DELAY * (attempt + 1)
                    logger.warning(
                        f"MCP 서버 재연결 시도 {attempt + 1}/{self.MAX_RECONNECT_ATTEMPTS}, "
                        f"{delay}초 후 재시도: {e}"
                    )
                    await asyncio.sleep(delay)
                else:
                    raise DBConnectionError(
                        f"MCP 서버 재연결 실패 ({self.MAX_RECONNECT_ATTEMPTS}회 시도): {e}"
                    ) from e

    async def search_objects(
        self,
        pattern: str = "*",
        object_type: str = "table",
    ) -> list[TableInfo]:
        """DB 객체(테이블, 뷰 등)를 검색한다.

        MCP 서버의 search_objects 도구를 호출하여 테이블 목록과
        컬럼 정보를 반환한다.

        Args:
            pattern: 검색 패턴 (기본: 전체)
            object_type: 객체 유형 (table, view 등)

        Returns:
            테이블 정보 목록

        Raises:
            DBConnectionError: 연결이 안 된 상태에서 호출 시
            DBHubError: 검색 실패 시
        """
        self._ensure_connected()
        try:
            result = await self._call_tool(
                "search_objects",
                {
                    "source": self._config.source_name,
                    "pattern": pattern,
                    "type": object_type,
                },
            )
            return self._parse_table_list(result)
        except Exception as e:
            raise DBHubError(f"스키마 검색 실패: {e}") from e

    async def get_table_schema(self, table_name: str) -> TableInfo:
        """특정 테이블의 상세 스키마를 조회한다.

        MCP 서버의 get_table_schema 도구를 1회 호출하여
        컬럼, PK, FK 정보를 모두 반환받는다.

        Args:
            table_name: 테이블명

        Returns:
            테이블 상세 정보 (컬럼, PK, FK 포함)

        Raises:
            DBHubError: 조회 실패 시
        """
        self._ensure_connected()
        # 테이블명 화이트리스트 검증 (SQL 인젝션 방어, 스키마 수식 허용)
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", table_name):
            raise DBHubError(f"유효하지 않은 테이블명: {table_name}")
        try:
            result = await self._call_tool(
                "get_table_schema",
                {
                    "source": self._config.source_name,
                    "table_name": table_name,
                },
            )
            return self._parse_table_schema(result)
        except Exception as e:
            raise DBHubError(f"테이블 스키마 조회 실패 ({table_name}): {e}") from e

    async def get_full_schema(self) -> SchemaInfo:
        """전체 DB 스키마를 수집한다.

        Returns:
            전체 스키마 정보 (테이블, 컬럼, FK 관계)
        """
        tables_list = await self.search_objects()
        schema = SchemaInfo()

        for table_brief in tables_list:
            table_detail = await self.get_table_schema(table_brief.name)
            schema.tables[table_detail.name] = table_detail

        # FK 관계 수집
        schema.relationships = await self._get_foreign_keys()
        return schema

    async def get_sample_data(
        self,
        table_name: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """테이블의 샘플 데이터를 안전하게 조회한다.

        Args:
            table_name: 테이블명
            limit: 조회 행 수 (기본 5건)

        Returns:
            샘플 데이터 행 목록

        Raises:
            DBHubError: 유효하지 않은 테이블명일 때
        """
        # 테이블명 검증 추가 (SQL 인젝션 방어)
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table_name):
            raise DBHubError(f"유효하지 않은 테이블명: {table_name}")

        result = await self.execute_sql(
            f"SELECT * FROM {table_name} LIMIT {limit}"
        )
        return result.rows

    async def execute_sql(self, sql: str) -> QueryResult:
        """SQL 쿼리를 실행한다. 연결 끊김 시 재연결을 시도한다.

        MCP 서버의 execute_sql 도구를 호출한다.
        읽기 전용이므로 SELECT 문만 허용된다.
        타임아웃은 mcp_call_timeout을 사용한다.

        Args:
            sql: 실행할 SQL 쿼리 문자열

        Returns:
            쿼리 실행 결과

        Raises:
            QueryTimeoutError: 타임아웃 초과 시
            QueryExecutionError: SQL 실행 에러 시
            DBConnectionError: 연결 문제 시
        """
        await self._ensure_connected_with_retry()
        start_time = time.time()

        try:
            result = await asyncio.wait_for(
                self._call_tool(
                    "execute_sql",
                    {
                        "source": self._config.source_name,
                        "sql": sql,
                    },
                ),
                timeout=self._config.mcp_call_timeout,
            )
            elapsed_ms = (time.time() - start_time) * 1000
            query_result = self._parse_query_result(result)
            query_result.execution_time_ms = elapsed_ms
            return query_result
        except asyncio.TimeoutError:
            raise QueryTimeoutError(
                f"MCP 호출 타임아웃 ({self._config.mcp_call_timeout}초 초과): "
                f"{sql[:100]}..."
            )
        except (QueryTimeoutError, QueryExecutionError):
            raise
        except Exception as e:
            raise QueryExecutionError(str(e), sql) from e

    # --- 내부 메서드 ---

    def _ensure_connected(self) -> None:
        """연결 상태를 확인한다.

        Raises:
            DBConnectionError: 연결되지 않은 경우
        """
        if not self._connected:
            raise DBConnectionError(
                "MCP 서버에 연결되지 않았습니다. connect()를 먼저 호출하세요."
            )

    async def _call_tool(self, tool_name: str, arguments: dict) -> Any:
        """MCP 도구를 호출한다.

        Args:
            tool_name: 도구명
            arguments: 도구 인자

        Returns:
            도구 실행 결과
        """
        if self._mcp_session is None:
            raise DBConnectionError("MCP 세션이 초기화되지 않았습니다.")

        result = await self._mcp_session.call_tool(tool_name, arguments)
        return result

    async def _get_foreign_keys(self) -> list[dict[str, str]]:
        """전체 FK 관계를 조회한다.

        Returns:
            FK 관계 목록
        """
        fk_sql = """
            SELECT
                tc.table_name AS from_table,
                kcu.column_name AS from_column,
                ccu.table_name AS to_table,
                ccu.column_name AS to_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
            WHERE tc.constraint_type = 'FOREIGN KEY'
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

    def _parse_json_result(self, raw_result: Any) -> dict:
        """MCP 도구 결과를 JSON dict로 파싱한다."""
        if raw_result is None:
            return {}

        try:
            content = raw_result
            if hasattr(raw_result, "content"):
                content = raw_result.content

            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if hasattr(item, "text"):
                        text_parts.append(item.text)
                    else:
                        text_parts.append(str(item))
                text = "\n".join(text_parts)
            elif isinstance(content, str):
                text = content
            else:
                text = str(content)

            return json.loads(text)
        except (json.JSONDecodeError, AttributeError, TypeError):
            return {}

    def _parse_table_list(self, raw_result: Any) -> list[TableInfo]:
        """search_objects 결과를 TableInfo 목록으로 변환한다.

        Args:
            raw_result: MCP tool 호출 결과

        Returns:
            테이블 정보 목록
        """
        tables: list[TableInfo] = []

        if raw_result is None:
            return tables

        # MCP 결과에서 content 추출
        try:
            content = raw_result
            if hasattr(raw_result, "content"):
                content = raw_result.content

            # content가 리스트인 경우 (TextContent 등)
            if isinstance(content, list):
                for item in content:
                    text = item.text if hasattr(item, "text") else str(item)
                    parsed = json.loads(text) if isinstance(text, str) else text
                    if isinstance(parsed, list):
                        for entry in parsed:
                            tables.append(
                                TableInfo(
                                    name=entry.get("name", ""),
                                    schema_name=entry.get("schema", "public"),
                                )
                            )
                    elif isinstance(parsed, dict):
                        if "error" in parsed:
                            logger.warning(
                                "search_objects 에러: %s", parsed["error"]
                            )
                        else:
                            tables.append(
                                TableInfo(
                                    name=parsed.get("name", ""),
                                    schema_name=parsed.get("schema", "public"),
                                )
                            )
            elif isinstance(content, str):
                parsed = json.loads(content)
                if isinstance(parsed, list):
                    for entry in parsed:
                        tables.append(
                            TableInfo(
                                name=entry.get("name", ""),
                                schema_name=entry.get("schema", "public"),
                            )
                        )
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning(f"테이블 목록 파싱 경고: {e}")

        return tables

    def _parse_table_schema(self, raw_result: Any) -> TableInfo:
        """get_table_schema 결과를 TableInfo로 변환한다.

        Args:
            raw_result: MCP tool 호출 결과

        Returns:
            테이블 상세 정보
        """
        parsed = self._parse_json_result(raw_result)

        if "error" in parsed:
            raise DBHubError(f"스키마 조회 에러: {parsed['error']}")

        table_name = parsed.get("table_name", "")
        columns_data = parsed.get("columns", [])
        fk_data = parsed.get("foreign_keys", [])

        # FK 매핑 구성
        fk_map: dict[str, str] = {}
        for fk in fk_data:
            fk_map[fk.get("from_column", "")] = (
                f"{fk.get('to_table', '')}.{fk.get('to_column', '')}"
            )

        columns = [
            ColumnInfo(
                name=col.get("column_name", ""),
                data_type=col.get("data_type", ""),
                nullable=(col.get("is_nullable", "YES") == "YES"),
                is_primary_key=col.get("is_primary_key", False),
                is_foreign_key=(col.get("column_name", "") in fk_map),
                references=fk_map.get(col.get("column_name", "")),
            )
            for col in columns_data
        ]

        return TableInfo(name=table_name, columns=columns)

    def _parse_query_result(self, raw_result: Any) -> QueryResult:
        """execute_sql 결과를 QueryResult로 변환한다.

        Args:
            raw_result: MCP tool 호출 결과

        Returns:
            구조화된 쿼리 결과
        """
        if raw_result is None:
            return QueryResult(columns=[], rows=[], row_count=0)

        try:
            parsed = self._parse_json_result(raw_result)

            if "error" in parsed:
                raise QueryExecutionError(parsed["error"])

            rows = parsed.get("rows", [])
            columns = parsed.get(
                "columns", list(rows[0].keys()) if rows else []
            )
            truncated = parsed.get("truncated", False)

            return QueryResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
            )
        except QueryExecutionError:
            raise
        except (json.JSONDecodeError, AttributeError, TypeError) as e:
            logger.warning(f"쿼리 결과 파싱 경고: {e}")
            return QueryResult(columns=[], rows=[], row_count=0)


@asynccontextmanager
async def get_dbhub_client(
    dbhub_config: DBHubConfig,
    query_config: QueryConfig | None = None,
) -> AsyncGenerator[DBHubClient, None]:
    """DBHub 클라이언트를 생성하고 연결을 관리한다.

    사용 예:
        async with get_dbhub_client(config.dbhub) as client:
            result = await client.execute_sql("SELECT 1")

    Args:
        dbhub_config: DBHub 설정
        query_config: 쿼리 설정 (선택)

    Yields:
        연결된 DBHubClient 인스턴스
    """
    client = DBHubClient(dbhub_config, query_config)
    try:
        await client.connect()
        yield client
    finally:
        await client.disconnect()
