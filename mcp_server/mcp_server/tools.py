"""MCP 도구 구현.

5개 도구를 FastMCP 서버에 등록한다:
- search_objects: DB 테이블/뷰 목록 검색
- execute_sql: 읽기 전용 SQL 실행
- get_table_schema: 테이블 상세 스키마 조회
- health_check: DB 연결 상태 확인
- list_sources: 등록된 활성 데이터소스 목록 반환
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from mcp_server.db import DBPoolManager
from mcp_server.security import ReadOnlyViolationError, validate_readonly

logger = logging.getLogger(__name__)


def _get_pool_manager(ctx: Context) -> DBPoolManager:
    """컨텍스트에서 DBPoolManager를 가져온다."""
    return ctx.request_context.lifespan_context["pool_manager"]


def _get_source_config(ctx: Context, source: str) -> Any:
    """컨텍스트에서 소스 설정을 가져온다."""
    pool_manager = _get_pool_manager(ctx)
    return pool_manager.get_source_config(source)


def register_tools(mcp: FastMCP) -> None:
    """MCP 서버에 도구를 등록한다."""

    @mcp.tool()
    async def search_objects(
        source: str,
        pattern: str = "*",
        type: str = "table",
        ctx: Context | None = None,
    ) -> str:
        """DB 테이블/뷰 객체를 검색한다.

        Args:
            source: 데이터소스 이름
            pattern: 검색 패턴 (기본: 전체)
            type: 객체 유형 (table, view)
            ctx: MCP 컨텍스트

        Returns:
            JSON 문자열 - 객체 목록
        """
        pool_manager = _get_pool_manager(ctx)
        source_type = pool_manager.get_source_type(source)

        try:
            if source_type == "postgresql":
                sql = _pg_search_objects_sql(pattern, type)
            elif source_type == "db2":
                sql = _db2_search_objects_sql(pattern, type)
            else:
                return json.dumps({"error": f"지원하지 않는 DB 타입: {source_type}"})

            rows = await pool_manager.execute(source, sql)
            logger.info(
                "search_objects: source=%s, pattern=%s, type=%s, 결과=%d건",
                source, pattern, type, len(rows),
            )
            return json.dumps(rows, ensure_ascii=False, default=str)

        except Exception as e:
            logger.error("search_objects 실패 (%s): %s", source, e)
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def execute_sql(
        source: str,
        sql: str,
        ctx: Context | None = None,
    ) -> str:
        """읽기 전용 SQL을 실행한다.

        Args:
            source: 데이터소스 이름
            sql: 실행할 SQL (SELECT만 허용)
            ctx: MCP 컨텍스트

        Returns:
            JSON 문자열 - 쿼리 결과
        """
        pool_manager = _get_pool_manager(ctx)
        src_config = pool_manager.get_source_config(source)

        # 읽기 전용 검증 (readonly 소스인 경우)
        if src_config.readonly:
            try:
                validate_readonly(sql)
            except ReadOnlyViolationError as e:
                logger.warning("읽기 전용 위반 (%s): %s", source, e.reason)
                return json.dumps(
                    {"error": f"읽기 전용 위반: {e.reason}"},
                    ensure_ascii=False,
                )

        start_time = time.time()
        try:
            rows = await pool_manager.execute(source, sql)

            # max_rows 제한 적용
            truncated = False
            if len(rows) > src_config.max_rows:
                rows = rows[:src_config.max_rows]
                truncated = True

            elapsed_ms = (time.time() - start_time) * 1000
            columns = list(rows[0].keys()) if rows else []

            result = {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "execution_time_ms": round(elapsed_ms, 2),
            }

            logger.info(
                "execute_sql: source=%s, rows=%d, time=%.0fms, truncated=%s",
                source, len(rows), elapsed_ms, truncated,
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            logger.error(
                "execute_sql 실패 (%s, %.0fms): %s", source, elapsed_ms, e
            )
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def get_table_schema(
        source: str,
        table_name: str,
        ctx: Context | None = None,
    ) -> str:
        """테이블의 상세 스키마(컬럼, PK, FK)를 반환한다.

        Args:
            source: 데이터소스 이름
            table_name: 테이블명
            ctx: MCP 컨텍스트

        Returns:
            JSON 문자열 - 테이블 스키마 정보
        """
        pool_manager = _get_pool_manager(ctx)
        source_type = pool_manager.get_source_type(source)

        try:
            if source_type == "postgresql":
                columns = await _pg_get_columns(pool_manager, source, table_name)
                pk_columns = await _pg_get_primary_keys(
                    pool_manager, source, table_name
                )
                fk_relations = await _pg_get_foreign_keys(
                    pool_manager, source, table_name
                )
            elif source_type == "db2":
                columns = await _db2_get_columns(pool_manager, source, table_name)
                pk_columns = await _db2_get_primary_keys(
                    pool_manager, source, table_name
                )
                fk_relations = await _db2_get_foreign_keys(
                    pool_manager, source, table_name
                )
            else:
                return json.dumps(
                    {"error": f"지원하지 않는 DB 타입: {source_type}"},
                    ensure_ascii=False,
                )

            pk_set = {pk["column_name"] for pk in pk_columns}

            schema = {
                "table_name": table_name,
                "source": source,
                "source_type": source_type,
                "columns": [
                    {
                        **col,
                        "is_primary_key": col["column_name"] in pk_set,
                    }
                    for col in columns
                ],
                "primary_keys": [pk["column_name"] for pk in pk_columns],
                "foreign_keys": fk_relations,
            }

            logger.info(
                "get_table_schema: source=%s, table=%s, columns=%d",
                source, table_name, len(columns),
            )
            return json.dumps(schema, ensure_ascii=False, default=str)

        except Exception as e:
            logger.error(
                "get_table_schema 실패 (%s.%s): %s", source, table_name, e
            )
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    @mcp.tool()
    async def health_check(
        source: str,
        ctx: Context | None = None,
    ) -> str:
        """데이터소스 연결 상태를 확인한다.

        Args:
            source: 데이터소스 이름
            ctx: MCP 컨텍스트

        Returns:
            JSON 문자열 - 연결 상태 정보
        """
        pool_manager = _get_pool_manager(ctx)

        if not pool_manager.is_source_active(source):
            return json.dumps(
                {
                    "source": source,
                    "status": "not_found",
                    "message": f"소스 '{source}'가 등록되지 않음",
                },
                ensure_ascii=False,
            )

        start_time = time.time()
        is_healthy = await pool_manager.health_check(source)
        elapsed_ms = (time.time() - start_time) * 1000

        result = {
            "source": source,
            "status": "healthy" if is_healthy else "unhealthy",
            "response_time_ms": round(elapsed_ms, 2),
            "source_type": pool_manager.get_source_type(source),
        }

        logger.info(
            "health_check: source=%s, status=%s, time=%.0fms",
            source, result["status"], elapsed_ms,
        )
        return json.dumps(result, ensure_ascii=False)

    @mcp.tool()
    async def list_sources(
        ctx: Context | None = None,
    ) -> str:
        """등록된 활성 데이터소스 목록을 반환한다.

        Returns:
            JSON 문자열 - 소스 목록
        """
        pool_manager = _get_pool_manager(ctx)
        sources_list = []

        for name in pool_manager.get_active_sources():
            src_config = pool_manager.get_source_config(name)
            sources_list.append({
                "name": name,
                "type": src_config.type,
                "readonly": src_config.readonly,
                "query_timeout": src_config.query_timeout,
                "max_rows": src_config.max_rows,
            })

        logger.info("list_sources: %d개 활성 소스", len(sources_list))
        return json.dumps(sources_list, ensure_ascii=False)


# --- PostgreSQL 스키마 조회 SQL ---


def _pg_split_table_name(table_name: str) -> tuple[str, str]:
    """테이블명에서 스키마와 테이블을 분리한다.

    'schema.table' 형태면 분리하고, bare name이면 'public'을 기본으로 한다.
    """
    if "." in table_name:
        schema, bare = table_name.split(".", 1)
        return schema, bare
    return "public", table_name


def _pg_search_objects_sql(pattern: str, obj_type: str) -> str:
    """PostgreSQL 객체 검색 SQL을 생성한다.

    public 스키마의 테이블은 bare name, 그 외 스키마는 schema.table 형태로 반환한다.
    """
    type_filter = "BASE TABLE" if obj_type == "table" else "VIEW"
    schema_exclude = (
        "table_schema NOT IN ('information_schema', 'pg_catalog', 'pg_toast')"
    )
    name_expr = (
        "CASE WHEN table_schema = 'public' THEN table_name "
        "ELSE table_schema || '.' || table_name END AS name"
    )
    if pattern == "*":
        return (
            f"SELECT {name_expr}, table_schema AS schema "
            "FROM information_schema.tables "
            f"WHERE {schema_exclude} AND table_type = '{type_filter}' "
            "ORDER BY table_schema, table_name"
        )
    else:
        safe_pattern = pattern.replace("'", "''").replace("*", "%")
        return (
            f"SELECT {name_expr}, table_schema AS schema "
            "FROM information_schema.tables "
            f"WHERE {schema_exclude} AND table_type = '{type_filter}' "
            f"AND table_name LIKE '{safe_pattern}' "
            "ORDER BY table_schema, table_name"
        )


async def _pg_get_columns(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """PostgreSQL 테이블의 컬럼 정보를 조회한다."""
    schema, bare = _pg_split_table_name(table_name)
    sql = (
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{bare}' "
        "ORDER BY ordinal_position"
    )
    return await pm.execute(source, sql)


async def _pg_get_primary_keys(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """PostgreSQL 테이블의 PK 컬럼을 조회한다."""
    schema, bare = _pg_split_table_name(table_name)
    sql = (
        "SELECT kcu.column_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema "
        f"WHERE tc.table_schema = '{schema}' AND tc.table_name = '{bare}' "
        "AND tc.constraint_type = 'PRIMARY KEY'"
    )
    return await pm.execute(source, sql)


async def _pg_get_foreign_keys(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """PostgreSQL 테이블의 FK 관계를 조회한다."""
    schema, bare = _pg_split_table_name(table_name)
    sql = (
        "SELECT "
        "kcu.column_name AS from_column, "
        "ccu.table_name AS to_table, "
        "ccu.column_name AS to_column "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema "
        "JOIN information_schema.constraint_column_usage ccu "
        "ON tc.constraint_name = ccu.constraint_name "
        "AND tc.table_schema = ccu.table_schema "
        f"WHERE tc.table_schema = '{schema}' AND tc.table_name = '{bare}' "
        "AND tc.constraint_type = 'FOREIGN KEY'"
    )
    try:
        return await pm.execute(source, sql)
    except Exception:
        return []


# --- DB2 스키마 조회 SQL ---


def _db2_search_objects_sql(pattern: str, obj_type: str) -> str:
    """DB2 객체 검색 SQL을 생성한다."""
    type_filter = "T" if obj_type == "table" else "V"
    if pattern == "*":
        return (
            "SELECT TRIM(TABNAME) AS name, TRIM(TABSCHEMA) AS schema "
            "FROM SYSCAT.TABLES "
            f"WHERE TYPE = '{type_filter}' "
            "AND TABSCHEMA NOT LIKE 'SYS%' "
            "ORDER BY TABNAME"
        )
    else:
        safe_pattern = pattern.replace("'", "''").replace("*", "%")
        return (
            "SELECT TRIM(TABNAME) AS name, TRIM(TABSCHEMA) AS schema "
            "FROM SYSCAT.TABLES "
            f"WHERE TYPE = '{type_filter}' "
            "AND TABSCHEMA NOT LIKE 'SYS%' "
            f"AND TABNAME LIKE '{safe_pattern}' "
            "ORDER BY TABNAME"
        )


async def _db2_get_columns(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """DB2 테이블의 컬럼 정보를 조회한다."""
    sql = (
        "SELECT TRIM(COLNAME) AS column_name, "
        "TRIM(TYPENAME) AS data_type, "
        "CASE WHEN NULLS = 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable, "
        "DEFAULT AS column_default "
        "FROM SYSCAT.COLUMNS "
        f"WHERE TABNAME = '{table_name.upper()}' "
        "AND TABSCHEMA NOT LIKE 'SYS%' "
        "ORDER BY COLNO"
    )
    return await pm.execute(source, sql)


async def _db2_get_primary_keys(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """DB2 테이블의 PK 컬럼을 조회한다."""
    sql = (
        "SELECT TRIM(kcu.COLNAME) AS column_name "
        "FROM SYSCAT.KEYCOLUSE kcu "
        "JOIN SYSCAT.TABCONST tc "
        "ON kcu.CONSTNAME = tc.CONSTNAME AND kcu.TABSCHEMA = tc.TABSCHEMA "
        f"WHERE tc.TABNAME = '{table_name.upper()}' "
        "AND tc.TYPE = 'P' "
        "AND tc.TABSCHEMA NOT LIKE 'SYS%' "
        "ORDER BY kcu.COLSEQ"
    )
    return await pm.execute(source, sql)


async def _db2_get_foreign_keys(
    pm: DBPoolManager, source: str, table_name: str
) -> list[dict[str, Any]]:
    """DB2 테이블의 FK 관계를 조회한다."""
    sql = (
        "SELECT TRIM(fk.COLNAME) AS from_column, "
        "TRIM(ref.REFTABNAME) AS to_table, "
        "TRIM(pk.COLNAME) AS to_column "
        "FROM SYSCAT.REFERENCES ref "
        "JOIN SYSCAT.KEYCOLUSE fk "
        "ON ref.CONSTNAME = fk.CONSTNAME AND ref.TABSCHEMA = fk.TABSCHEMA "
        "JOIN SYSCAT.KEYCOLUSE pk "
        "ON ref.REFKEYNAME = pk.CONSTNAME AND ref.REFTABSCHEMA = pk.TABSCHEMA "
        "AND fk.COLSEQ = pk.COLSEQ "
        f"WHERE ref.TABNAME = '{table_name.upper()}' "
        "AND ref.TABSCHEMA NOT LIKE 'SYS%'"
    )
    try:
        return await pm.execute(source, sql)
    except Exception:
        return []
