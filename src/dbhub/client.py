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
from src.utils import sql_file_logger
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
        if not dbhub_config.source_name:
            raise ValueError(
                "DBHubConfig.source_name이 설정되지 않았습니다. "
                "환경변수 DBHUB_SOURCE_NAME을 설정하세요."
            )
        self._config = dbhub_config
        self._query_config = query_config or QueryConfig()
        # 전송 인증(Plan 04 §6-4·D-015): 설정 토큰이 있으면 SSE 연결에 Bearer 헤더를
        # 첨부한다. 빈 값이면 None → 무헤더(서버 무인증 통과 전제 → 기존 동작 비트동일).
        self._bearer_token: Optional[str] = (
            getattr(dbhub_config, "bearer_token", "") or None
        )
        self._mcp_session: Optional[Any] = None
        self._connected: bool = False
        self._sse_context: Optional[Any] = None
        self._session_context: Optional[Any] = None

    def _auth_headers(self) -> Optional[dict[str, str]]:
        """Bearer 인증 헤더를 구성한다(토큰 없으면 None — 무헤더).

        Returns:
            토큰이 설정된 경우 `{"Authorization": "Bearer <token>"}`, 없으면 None.
        """
        if not self._bearer_token:
            return None
        return {"Authorization": f"Bearer {self._bearer_token}"}

    async def connect(self) -> None:
        """MCP 서버에 SSE transport로 연결한다.

        Raises:
            DBConnectionError: 연결 실패 시
        """
        try:
            from mcp import ClientSession
            from mcp.client.sse import sse_client

            async def _open() -> None:
                # SSE 클라이언트로 원격 MCP 서버에 연결(Bearer 헤더는 설정 토큰이 있을 때만 첨부).
                self._sse_context = sse_client(
                    url=self._config.server_url, headers=self._auth_headers()
                )
                sse_transport = await self._sse_context.__aenter__()
                read_stream, write_stream = sse_transport

                # MCP 세션 생성 및 초기화
                self._session_context = ClientSession(read_stream, write_stream)
                self._mcp_session = await self._session_context.__aenter__()
                await self._mcp_session.initialize()

            # 연결·핸드셰이크 전체 타임아웃 — 서버가 TCP만 수락하고 SSE 핸드셰이크를
            # 못 끝내면 무한 대기가 가능하다. execute_sql의 per-call 타임아웃만으로는
            # connect 단계 hang을 끊지 못한다(전체 타임아웃 가드 원칙).
            await asyncio.wait_for(
                _open(), timeout=self._config.mcp_call_timeout
            )

            self._connected = True
            logger.debug(
                "MCP 서버 연결 성공 (SSE): %s", self._config.server_url
            )
        except ImportError:
            # MCP SDK가 설치되지 않은 경우 폴백 모드
            logger.warning(
                "MCP SDK가 설치되지 않았습니다. DBHub 클라이언트가 제한 모드로 동작합니다."
            )
            self._connected = True
        except asyncio.TimeoutError:
            await self.disconnect()  # 반쯤 열린 컨텍스트 정리(베스트 에포트)
            raise DBConnectionError(
                f"MCP 서버 연결 타임아웃 ({self._config.mcp_call_timeout}초): "
                f"{self._config.server_url}"
            )
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
            logger.debug("MCP 서버 연결 종료")

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
            status = parsed.get("status")
            if status != "healthy":
                logger.warning("health_check 비정상 (source=%s): %s", self._config.source_name, parsed)
            return status == "healthy"
        except Exception as e:
            logger.warning("health_check 실패 (source=%s): %s: %s", self._config.source_name, type(e).__name__, e)
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

    # ──────────────────────────────────────────────
    # 호스트 조사 고수준 도구 (Plan 78 W3-1·4 · G1 중간 비용대 공백 해소)
    # ──────────────────────────────────────────────
    #
    # `mcp_server`가 이미 고수준 도구를 제공하므로 **신규 커넥터 없이** 기존 `_call_tool`로
    # 배선한다. 방언·금지조인·마스킹은 **서버가 처리하므로 본체는 무지해도 된다**(D-122).
    #
    # **도구 수를 늘리지 않는다**(W3-4 — "적지만 더 나은 도구"): 공개 API는 `inspect_host`
    # 하나이고 어느 도구를 부를지는 `profile` 인자가 정한다. 이름은 기존 `_TOOL_NAMES`의
    # 동사+목적어 관례(`query_infra_db`·`query_live_processes`)를 따른다.
    #
    # **식별 키가 도구마다 다르다**(실측 2026-08-27 — `mcp_server/mcp_server/polestar_tools.py`):
    # `metric_trend`·`resource_status`는 `server_name`을, `os_config`·`process_snapshot`은
    # `hostname`을 받는다. 폴스타는 server_name ≠ hostname이므로(D-046) 섞으면 0건이 된다.
    # `process_snapshot`은 프로세스 API 직결이라 **`source` 인자 자체가 없다.**

    HOST_INSPECT_PROFILES: dict[str, dict[str, Any]] = {
        "processes": {
            "tool": "polestar_process_snapshot",
            "identifier": "hostname",
            "needs_source": False,
        },
        "os_config": {
            "tool": "polestar_os_config",
            "identifier": "hostname",
            "needs_source": True,
        },
        "resource_status": {
            "tool": "polestar_resource_status",
            "identifier": "server_name",
            "needs_source": True,
        },
        "metric_trend": {
            "tool": "polestar_metric_trend",
            "identifier": "server_name",
            "needs_source": True,
        },
    }

    #: 서버가 받는 열거값. **호출 전에** 여기서 거른다 — 왕복 한 번을 아끼는 것보다,
    #: 모델에게 "무엇이 틀렸는지"를 구조화해 돌려주는 것이 중요하다(W3-4).
    _METRIC_KINDS: frozenset[str] = frozenset({"cpu", "memory", "filesystem", "disk_io"})
    _METRIC_GRANULARITIES: frozenset[str] = frozenset({"h", "d", "m"})
    _PROCESS_SORTS: frozenset[str] = frozenset({"cpu", "mem"})

    def _validate_inspect_args(
        self,
        profile: str,
        hostname: Optional[str],
        server_name: Optional[str],
        options: dict[str, Any],
    ) -> Optional[str]:
        """`inspect_host` 인자를 **실행 전에** 검증한다 (W3-4).

        Returns:
            오류 메시지, 문제가 없으면 None
        """
        spec = self.HOST_INSPECT_PROFILES.get(profile)
        if spec is None:
            allowed = ", ".join(sorted(self.HOST_INSPECT_PROFILES))
            return f"지원하지 않는 profile: {profile} (허용: {allowed})"

        identifier = hostname if spec["identifier"] == "hostname" else server_name
        if not identifier or not str(identifier).strip():
            return f"profile '{profile}'에는 {spec['identifier']}이(가) 필요합니다"

        if profile == "metric_trend":
            kind = options.get("kind")
            if kind not in self._METRIC_KINDS:
                return f"지원하지 않는 kind: {kind} (허용: {', '.join(sorted(self._METRIC_KINDS))})"
            gran = options.get("granularity", "h")
            if gran not in self._METRIC_GRANULARITIES:
                return f"지원하지 않는 granularity: {gran} (허용: h, d, m)"
            periods = options.get("periods", 24)
            if not isinstance(periods, int) or periods < 1:
                return f"periods는 1 이상의 정수여야 합니다: {periods!r}"
        elif profile == "processes":
            sort = options.get("sort", "cpu")
            if sort not in self._PROCESS_SORTS:
                return f"지원하지 않는 sort: {sort} (허용: cpu, mem)"
            top_n = options.get("top_n", 10)
            if not isinstance(top_n, int) or top_n < 1:
                return f"top_n은 1 이상의 정수여야 합니다: {top_n!r}"
        return None

    async def inspect_host(
        self,
        *,
        profile: str,
        hostname: Optional[str] = None,
        server_name: Optional[str] = None,
        source: Optional[str] = None,
        **options: Any,
    ) -> dict[str, Any]:
        """`mcp_server` 고수준 도구로 호스트를 조사한다 (Plan 78 W3-1).

        조회 비용의 **중간대 공백**을 메운다 — 프로세스 목록(낮음)과 `sre_agent` 위임(높음)
        사이에 "OS 구성·메트릭·토폴로지 단건 조회"가 없었다(§2.2 G1).

        **읽기 전용 불변**: `execute_sql` 노출 정책(D-122 ④)을 건드리지 않는다. 여기서 부르는
        도구는 전부 서버가 SQL을 조립하는 고수준 도구다.

        Args:
            profile: processes | os_config | resource_status | metric_trend
            hostname: OS 호스트명 (processes · os_config)
            server_name: 폴스타 등록 서버명 (resource_status · metric_trend)
            source: 데이터소스(db_id). 미지정 시 설정된 source_name.
                `processes`는 프로세스 API 직결이라 사용하지 않는다
            **options: 프로파일별 인자 — metric_trend(kind, granularity, periods) ·
                processes(top_n, sort)

        Returns:
            서버 반환 계약 `{rows, row_count, queried_at, source_kind, source, engine}`
            그대로. 실패는 **예외가 아니라** `{error: 사유}`로 돌려준다 —
            모델에게 구조화된 실패를 주어야 다음 행동을 고를 수 있다(W3-4).
        """
        problem = self._validate_inspect_args(profile, hostname, server_name, options)
        if problem:
            logger.info("inspect_host 인자 검증 탈락: %s", problem)
            return {"error": problem}

        spec = self.HOST_INSPECT_PROFILES[profile]
        arguments: dict[str, Any] = {}
        if spec["needs_source"]:
            arguments["source"] = source or self._config.source_name
        if spec["identifier"] == "hostname":
            arguments["hostname"] = str(hostname).strip()
        else:
            arguments["server_name"] = str(server_name).strip()
        arguments.update({k: v for k, v in options.items() if v is not None})

        try:
            self._ensure_connected()
            raw = await self._call_tool(spec["tool"], arguments)
        except Exception as e:  # noqa: BLE001 — 조사 실패는 구조화해 반환한다(침묵 금지)
            logger.warning("inspect_host 실패 (%s/%s): %s", profile, spec["tool"], e)
            return {"error": f"{spec['tool']} 호출 실패: {e}"}

        parsed = self._parse_json_result(raw)
        if not parsed:
            return {"error": f"{spec['tool']} 응답을 해석하지 못했습니다"}
        return parsed

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
        # 테이블명 검증 (SQL 인젝션 방어, schema.table 형태 허용)
        if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_.]*$", table_name):
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
            sql_file_logger.log_sql(
                sql, execution_time_ms=elapsed_ms,
                row_count=query_result.row_count,
                source=self._config.source_name,
            )
            return query_result
        except asyncio.TimeoutError:
            sql_file_logger.log_sql(
                sql, execution_time_ms=(time.time() - start_time) * 1000,
                source=self._config.source_name, error="MCP 호출 타임아웃",
            )
            raise QueryTimeoutError(
                f"MCP 호출 타임아웃 ({self._config.mcp_call_timeout}초 초과): "
                f"{sql[:100]}..."
            )
        except QueryTimeoutError:
            raise
        except QueryExecutionError as e:
            # DB 측 SQL 에러(DBHub isError → _parse_query_result)는 종전 재던지기만 해
            # 실패 SQL이 파일 로그에서 통째로 빠졌다(D-140 커버리지 공백 — 2026-08-21
            # 공동존 bigint 실측: logs/sql에 실패 SQL 부재로 감사 로그 수동 대조가 필요
            # 했음, D-160). 가장 진단 가치가 큰 실패 건이므로 기록 후 재던진다.
            # 타임아웃 핸들러가 만든 예외는 같은 try의 except로 재진입하지 않아
            # 이중 기록은 없다.
            sql_file_logger.log_sql(
                sql, execution_time_ms=(time.time() - start_time) * 1000,
                source=self._config.source_name, error=str(e),
            )
            raise
        except Exception as e:
            sql_file_logger.log_sql(
                sql, execution_time_ms=(time.time() - start_time) * 1000,
                source=self._config.source_name, error=str(e),
            )
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

    @staticmethod
    def _result_text(raw_result: Any) -> str:
        """MCP 도구 결과의 content를 평문 텍스트로 합친다."""
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
            return "\n".join(text_parts)
        if isinstance(content, str):
            return content
        return str(content)

    def _parse_json_result(self, raw_result: Any) -> dict:
        """MCP 도구 결과를 JSON dict로 파싱한다."""
        if raw_result is None:
            return {}

        try:
            return json.loads(self._result_text(raw_result))
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

        # MCP 도구 오류(isError)는 content가 JSON이 아닌 오류 문자열이라, 아래 파싱
        # 폴백을 타면 빈 결과(0행)로 침묵 강등된다 — 실측: 미등록 소스의 "알 수 없는
        # 소스" ValueError가 0행으로 위장돼 골드셋 검증이 오판됨(2026-07-30). 명시 예외로.
        if getattr(raw_result, "isError", False) is True:
            raise QueryExecutionError(
                self._result_text(raw_result) or "MCP 도구 오류(상세 없음)"
            )

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
