"""DB 연결 관리 모듈.

PostgreSQL(asyncpg 풀) · DB2(ibm_db, asyncio.to_thread 래핑) · MariaDB(aiomysql 풀)를
통합 관리한다.

읽기 전용(D-003 · plans/163 S1): readonly 소스는 모든 실행이 `DBPoolManager.execute`의
`validate_readonly`를 거치고(완전 중재 — 도구·스키마 헬퍼·exporter·헬스체크 공통), 연결은 세션
읽기 전용으로 연다(PostgreSQL·MariaDB). 세션 읽기 전용은 보조 수단이다 — 같은 세션에서 되돌릴 수
있으므로 본질은 SELECT 권한만 가진 접속 계정이다.

조회 대상 보호(plans/194 W4a · D-347): 소스별 동시 실행 상한(`asyncio.Semaphore`)과 대기
타임아웃을 `execute`에 건다. 넘치면 무기한 줄 서지 않고 `QueryQueueTimeoutError`로 끊는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from mcp_server import sql_log
from mcp_server.config import SourceConfig
from mcp_server.security import validate_readonly

logger = logging.getLogger(__name__)


class QueryQueueTimeoutError(Exception):
    """소스별 동시 실행 상한에 걸려 대기 타임아웃 안에 실행 슬롯·연결을 얻지 못했다(D-347).

    도구는 기존 관행대로 `{"error": str(e)}`로 돌려준다 — 메시지가 곧 사용자 안내다.
    """


def _queue_timeout_message(src: SourceConfig) -> str:
    """대기 초과 안내 문구 — 소스·동시 상한·대기 시간을 싣는다(SQL·연결 문자열은 싣지 않는다)."""
    return (
        f"대상 DB 조회 대기 초과(소스 {src.name} · "
        f"동시 {src.effective_max_concurrent()}건 진행 중 · "
        f"{src.effective_queue_timeout():g}초 대기) — 잠시 뒤 다시 시도하세요"
    )


class DBPoolManager:
    """데이터소스별 DB 연결 관리.

    PostgreSQL은 asyncpg 풀, DB2는 ibm_db 요청별 연결, MariaDB는 aiomysql 풀을 사용한다.
    """

    def __init__(self, sources: list[SourceConfig]) -> None:
        self._sources: dict[str, SourceConfig] = {
            s.name: s for s in sources if s.connection
        }
        self._pg_pools: dict[str, Any] = {}  # asyncpg.Pool
        self._db2_configs: dict[str, SourceConfig] = {}
        self._mariadb_pools: dict[str, Any] = {}  # aiomysql.Pool
        # 소스별 동시 실행 상한(D-347). D-162(무기한 대기 허용) 예외 — 조사 1건이 폴스타 MCP를
        # 최대 16건 동시 호출하고 조사 수만큼 곱해져, 상한·타임아웃 없이는 프로세스 안 대기열이
        # 끝없이 자라 행(hang)이 된다. 기본 상한 = 풀 크기라 정상 부하(풀 크기 이하)는 무대기.
        self._query_limits: dict[str, asyncio.Semaphore] = {
            name: asyncio.Semaphore(src.effective_max_concurrent())
            for name, src in self._sources.items()
        }

    async def initialize(self) -> None:
        """활성 소스에 대해 연결을 초기화한다."""
        for name, src in self._sources.items():
            if src.type == "postgresql":
                try:
                    import asyncpg

                    pg_kwargs: dict[str, Any] = {}
                    if src.readonly:
                        # 세션 기본 트랜잭션을 읽기 전용으로 — 풀 반납 시 RESET ALL로 원복된다.
                        pg_kwargs["server_settings"] = {
                            "default_transaction_read_only": "on"
                        }
                    pool = await asyncpg.create_pool(
                        dsn=src.connection,
                        min_size=src.pool_min_size,
                        max_size=src.pool_max_size,
                        command_timeout=src.query_timeout,
                        **pg_kwargs,
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
                # 세션 읽기 전용 미적용 — ibm_db가 루트 venv에 없어 연결 속성을 실측하지 못했다
                # (plans/163 W0). DB2 쓰기 차단은 실행 지점 검사와 접속 계정 권한에 의존한다.
                self._db2_configs[name] = src
                logger.info("DB2 소스 등록: %s (요청별 연결)", name)
            elif src.type == "mariadb":
                try:
                    import aiomysql

                    # 문장 타임아웃을 서버가 강제한다(MariaDB 세션 변수 · 초 단위). readonly 소스는
                    # 세션 읽기 전용을 같은 문장에 붙인다(init_command는 한 문장만 실행된다).
                    # aiomysql은 CLIENT.MULTI_STATEMENTS를 항상 켠다(client_flag로 끌 수 없음 —
                    # 0.3.2 실측) — 다중 문장 차단은 실행 지점 검사가 맡는다.
                    init_command = f"SET SESSION max_statement_time={src.query_timeout}"
                    pool_kwargs = dict(
                        minsize=src.pool_min_size,
                        maxsize=src.pool_max_size,
                        connect_timeout=src.query_timeout,
                        # SELECT만 해도 트랜잭션이 열리고, aiomysql은 트랜잭션이 열린 연결을
                        # 풀에 돌려받을 때 닫는다 — autocommit 없이는 풀이 매 요청 재연결로
                        # 퇴화한다.
                        autocommit=True,
                        **_mariadb_connect_kwargs(src.connection),
                    )
                    if src.readonly:
                        pool = await _create_mariadb_readonly_pool(
                            aiomysql, name, init_command, pool_kwargs
                        )
                    else:
                        pool = await aiomysql.create_pool(
                            init_command=init_command, **pool_kwargs
                        )
                    self._mariadb_pools[name] = pool
                    logger.info(
                        "MariaDB 풀 초기화 성공: %s (풀 %d-%d)",
                        name,
                        src.pool_min_size,
                        src.pool_max_size,
                    )
                except Exception as e:
                    logger.error("MariaDB 풀 초기화 실패 (%s): %s", name, e)
            else:
                logger.warning("지원하지 않는 DB 타입: %s (%s)", src.type, name)

    async def execute(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """소스 타입에 따라 적절한 드라이버로 쿼리를 실행한다.

        실행된 SQL은 성공·실패 모두 `logs/sql/`에 기록한다(D-140). 엔진 분기가
        여기 한 곳으로 모이므로 로깅도 여기서 한 번만 건다(경로 대칭).
        readonly 소스면 실행 전에 읽기 전용 검사를 건다(완전 중재 — 거부도 같은 로그에 남는다).

        Args:
            source_name: 데이터소스 이름
            sql: 실행할 SQL

        Returns:
            결과 행 목록

        Raises:
            ValueError: 알 수 없는 소스명
            ReadOnlyViolationError: readonly 소스에 읽기 전용이 아닌 SQL
            QueryQueueTimeoutError: 소스 동시 상한 대기 초과(D-347)
        """
        if source_name in self._pg_pools:
            runner = self._execute_pg
        elif source_name in self._db2_configs:
            runner = self._execute_db2
        elif source_name in self._mariadb_pools:
            runner = self._execute_mariadb
        else:
            raise ValueError(
                f"알 수 없는 소스: {source_name}. "
                f"사용 가능: {list(self._sources.keys())}"
            )

        started = time.perf_counter()
        try:
            self._guard_readonly(source_name, sql)
            async with self._query_slot(source_name):
                rows = await runner(source_name, sql)
        except Exception as e:
            sql_log.log_sql(
                sql,
                source=source_name,
                execution_time_ms=(time.perf_counter() - started) * 1000,
                error=str(e),
            )
            raise
        sql_log.log_sql(
            sql,
            source=source_name,
            execution_time_ms=(time.perf_counter() - started) * 1000,
            row_count=len(rows),
        )
        return rows

    @contextlib.asynccontextmanager
    async def _query_slot(self, source_name: str) -> AsyncIterator[None]:
        """소스 동시 실행 슬롯을 대기 타임아웃 안에 얻는다. 못 얻으면 `QueryQueueTimeoutError`.

        슬롯이 비어 있으면 즉시 통과한다. 타임아웃은 대기에만 걸린다 — 실행 시간은 엔진별
        `query_timeout`이 맡는다.
        """
        src = self._sources[source_name]
        limit = self._query_limits[source_name]
        try:
            await asyncio.wait_for(limit.acquire(), timeout=src.effective_queue_timeout())
        except TimeoutError:
            logger.warning(
                "조회 대기 초과 (%s): 동시 상한 %d · 대기 %gs",
                source_name, src.effective_max_concurrent(), src.effective_queue_timeout(),
            )
            raise QueryQueueTimeoutError(_queue_timeout_message(src)) from None
        try:
            yield
        finally:
            limit.release()

    def _guard_readonly(self, source_name: str, sql: str) -> None:
        """readonly 소스면 `validate_readonly`를 건다. 설정이 없는 소스도 검사한다(실패 시 닫힘).

        소스 `readonly=false` 스위치(`config.py`)는 현행 유지 — 그 소스만 검사를 건너뛴다.
        """
        src = self._sources.get(source_name)
        if src is None or src.readonly:
            validate_readonly(sql)

    async def _execute_pg(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """PostgreSQL 쿼리를 실행한다.

        풀 연결 대기에도 대기 타임아웃을 건다(D-347) — 동시 상한을 풀 크기보다 크게 잡았거나
        헬스체크가 연결을 쥔 경우의 무기한 대기를 막는다. 실행 timeout(`command_timeout`)과
        구분하려고 acquire만 따로 감싼다(둘 다 `TimeoutError`다).
        """
        pool = self._pg_pools[source_name]
        src = self._sources[source_name]
        try:
            conn = await pool.acquire(timeout=src.effective_queue_timeout())
        except TimeoutError:
            raise QueryQueueTimeoutError(_queue_timeout_message(src)) from None
        try:
            rows = await conn.fetch(sql)
            return [_normalize_row(dict(r)) for r in rows]
        finally:
            await pool.release(conn)

    async def _execute_db2(self, source_name: str, sql: str) -> list[dict[str, Any]]:
        """DB2 쿼리를 실행한다 (ibm_db를 asyncio.to_thread로 래핑)."""
        src = self._db2_configs[source_name]
        return await asyncio.to_thread(
            self._execute_db2_sync, src.connection, sql, src.query_timeout
        )

    async def _execute_mariadb(
        self, source_name: str, sql: str
    ) -> list[dict[str, Any]]:
        """MariaDB 쿼리를 실행한다.

        인자 없이 실행하므로 드라이버가 `%`를 포맷 지시자로 해석하지 않는다(`LIKE 'a%'` 안전).
        """
        import aiomysql

        pool = self._mariadb_pools[source_name]
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql)
                rows = await cur.fetchall()
                return [_normalize_row(dict(r)) for r in rows]

    @staticmethod
    def _execute_db2_sync(
        conn_str: str, sql: str, query_timeout: int = 0
    ) -> list[dict[str, Any]]:
        """DB2 동기 쿼리를 실행한다.

        `query_timeout`(초)이 양수면 statement 옵션 `SQL_ATTR_QUERY_TIMEOUT`으로 건다(D-347).
        """
        import ibm_db

        conn = ibm_db.connect(conn_str, "", "")
        try:
            options = _db2_statement_options(ibm_db, query_timeout)
            if options:
                stmt = ibm_db.exec_immediate(conn, sql, options)
            else:
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
        """특정 소스의 연결 상태를 확인한다.

        상수 SQL도 실행 지점과 같은 읽기 전용 검사를 받는다. `execute`를 거치지 않는 것은
        헬스체크를 SQL 로그(`logs/sql/`)에 남기지 않기 위해서다.
        """
        try:
            if source_name in self._pg_pools:
                sql = "SELECT 1 AS ok"
                self._guard_readonly(source_name, sql)
                rows = await self._execute_pg(source_name, sql)
                return len(rows) > 0
            elif source_name in self._db2_configs:
                sql = "SELECT 1 AS ok FROM SYSIBM.SYSDUMMY1"
                self._guard_readonly(source_name, sql)
                rows = await self._execute_db2(source_name, sql)
                return len(rows) > 0
            elif source_name in self._mariadb_pools:
                sql = "SELECT 1 AS ok"
                self._guard_readonly(source_name, sql)
                rows = await self._execute_mariadb(source_name, sql)
                return len(rows) > 0
            return False
        except Exception as e:
            logger.warning("헬스체크 실패 (%s): %s", source_name, e)
            return False

    async def close_all(self) -> None:
        """모든 PostgreSQL·MariaDB 풀을 종료한다.

        DB2는 요청별 연결이므로 별도 종료가 불필요하다.
        """
        for name, pool in self._pg_pools.items():
            try:
                await pool.close()
                logger.info("PostgreSQL 풀 종료: %s", name)
            except Exception as e:
                logger.warning("PostgreSQL 풀 종료 실패 (%s): %s", name, e)
        self._pg_pools.clear()
        for name, pool in self._mariadb_pools.items():
            try:
                pool.close()
                await pool.wait_closed()
                logger.info("MariaDB 풀 종료: %s", name)
            except Exception as e:
                logger.warning("MariaDB 풀 종료 실패 (%s): %s", name, e)
        self._mariadb_pools.clear()


def _db2_statement_options(ibm_db: Any, query_timeout: int) -> dict[int, int]:
    """DB2 statement 옵션 — 쿼리 타임아웃(초)을 `SQL_ATTR_QUERY_TIMEOUT`으로 싣는다(D-347).

    ibm_db가 루트 venv에 없어 상수를 실측하지 못했다(plans/194 W4a) — 상수가 없는 빌드면
    경고를 남기고 옵션 없이 실행한다(종전 동작). 내부망 설치본에서 확인이 필요하다.
    """
    if query_timeout <= 0:
        return {}
    attr = getattr(ibm_db, "SQL_ATTR_QUERY_TIMEOUT", None)
    if attr is None:
        logger.warning("ibm_db에 SQL_ATTR_QUERY_TIMEOUT 없음 — DB2 쿼리 타임아웃 미적용")
        return {}
    return {attr: int(query_timeout)}


# 세션 읽기 전용 변수명 — MariaDB 11.1부터 `transaction_read_only`, 그 전(10.x)은 `tx_read_only`만
# 있다. MySQL 8.0은 반대로 `tx_read_only`가 제거됐다. 앞에서부터 시도해 서버가 아는 이름을 쓴다.
_MARIADB_READONLY_VARS = ("transaction_read_only", "tx_read_only")
_ER_UNKNOWN_SYSTEM_VARIABLE = 1193


async def _create_mariadb_readonly_pool(
    aiomysql: Any, name: str, init_command: str, pool_kwargs: dict[str, Any]
) -> Any:
    """세션 읽기 전용 변수를 붙여 풀을 만든다 — 1193(변수 없음)이면 다음 변수명으로 다시 시도한다.

    다른 오류이거나 마지막 후보까지 1193이면 그대로 올린다(읽기 전용 없이 여는 강등은 하지 않는다).
    """
    for i, var in enumerate(_MARIADB_READONLY_VARS):
        try:
            return await aiomysql.create_pool(
                init_command=f"{init_command}, SESSION {var}=1", **pool_kwargs
            )
        except Exception as e:
            args = getattr(e, "args", ())
            unknown_var = bool(args) and args[0] == _ER_UNKNOWN_SYSTEM_VARIABLE
            if not unknown_var or i == len(_MARIADB_READONLY_VARS) - 1:
                raise
            logger.warning(
                "MariaDB 세션 읽기 전용 변수 %s 미지원 (%s) — %s로 재시도",
                var, name, _MARIADB_READONLY_VARS[i + 1],
            )
    raise AssertionError("unreachable")  # pragma: no cover


def _mariadb_connect_kwargs(dsn: str) -> dict[str, Any]:
    """`mariadb://user:password@host:port/database` 연결 문자열을 aiomysql 접속 인자로 푼다.

    aiomysql은 DSN 문자열을 받지 않고 키워드 인자만 받는다. 사용자·비밀번호의 특수문자는
    퍼센트 인코딩으로 적는다. 쿼리 옵션(`?…`)은 해석하지 않으므로 조용히 버리지 않고 거부한다.
    오류 메시지에는 연결 문자열을 싣지 않는다(비밀번호 노출 방지).

    Raises:
        ValueError: 스킴·호스트·database 누락 또는 쿼리 옵션 포함
    """
    parts = urlsplit(dsn)
    if parts.scheme not in ("mariadb", "mysql"):
        raise ValueError(
            "MariaDB 연결 문자열은 mariadb:// 또는 mysql:// 형식이어야 한다"
        )
    if parts.query:
        raise ValueError("MariaDB 연결 문자열의 쿼리 옵션(?…)은 지원하지 않는다")
    database = unquote(parts.path.lstrip("/"))
    if not parts.hostname or not database:
        raise ValueError("MariaDB 연결 문자열에 host와 database가 필요하다")
    return {
        "host": parts.hostname,
        "port": parts.port or 3306,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "db": database,
    }


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
