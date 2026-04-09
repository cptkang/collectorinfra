"""PostgreSQL 기반 감사 로그 저장소 구현.

기존 파일 기반 감사 로그와 병행 운영한다.
AUTH_ENABLED=true일 때 DB 감사 로그도 기록한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import asyncpg

from src.domain.user import AuditRepository

logger = logging.getLogger(__name__)


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """asyncpg Record를 감사 로그 딕셔너리로 변환한다.

    Args:
        row: asyncpg 조회 결과 행

    Returns:
        변환된 딕셔너리
    """
    return {
        "id": row["id"],
        "event_type": row["event_type"],
        "user_id": row["user_id"],
        "detail": json.loads(row["detail"]) if row["detail"] else {},
        "ip_address": row["ip_address"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


class PostgresAuditRepository(AuditRepository):
    """PostgreSQL 기반 감사 로그 저장소."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log_event(self, event: dict) -> None:
        """감사 이벤트를 기록한다."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs
                        (event_type, user_id, detail, ip_address)
                    VALUES ($1, $2, $3, $4)
                    """,
                    event.get("event_type"),
                    event.get("user_id"),
                    json.dumps(event.get("detail", {}), ensure_ascii=False),
                    event.get("ip_address"),
                )
        except Exception as e:
            logger.error("감사 로그 DB 기록 실패: %s", e)

    async def query_logs(
        self,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """감사 로그를 조회한다."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1

        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT id, event_type, user_id, detail, ip_address, created_at
            FROM audit_logs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${idx}
        """
        params.append(limit)

        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query, *params)
            return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("감사 로그 조회 실패: %s", e)
            return []

    async def query_logs_paginated(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        target_db: Optional[str] = None,
        success: Optional[bool] = None,
        keyword: Optional[str] = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[dict], int]:
        """페이지네이션 감사 로그 조회. (logs, total_count)를 반환한다.

        Args:
            start_date: 시작 날짜 (ISO 8601 형식)
            end_date: 종료 날짜 (ISO 8601 형식)
            user_id: 사용자 ID 필터
            event_type: 이벤트 유형 필터
            target_db: 대상 DB 필터 (detail JSONB에서 조회)
            success: 성공 여부 필터 (detail JSONB에서 조회)
            keyword: 키워드 검색 (user_id, event_type, detail에서 ILIKE)
            page: 페이지 번호 (1부터 시작)
            page_size: 페이지 크기

        Returns:
            (로그 목록, 전체 건수) 튜플
        """
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if start_date:
            conditions.append(f"created_at >= ${idx}::timestamptz")
            params.append(start_date)
            idx += 1

        if end_date:
            conditions.append(f"created_at <= ${idx}::timestamptz")
            params.append(end_date)
            idx += 1

        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1

        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1

        if target_db:
            conditions.append(f"detail->>'target_db' = ${idx}")
            params.append(target_db)
            idx += 1

        if success is not None:
            conditions.append(f"detail->>'success' = ${idx}")
            params.append(str(success).lower())
            idx += 1

        if keyword:
            keyword_pattern = f"%{keyword}%"
            conditions.append(
                f"(user_id ILIKE ${idx} OR event_type ILIKE ${idx} OR detail::text ILIKE ${idx})"
            )
            params.append(keyword_pattern)
            idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # 전체 건수 조회
        count_query = f"SELECT COUNT(*) FROM audit_logs {where_clause}"

        # 데이터 조회 (페이지네이션)
        offset = (page - 1) * page_size
        data_query = f"""
            SELECT id, event_type, user_id, detail, ip_address, created_at
            FROM audit_logs
            {where_clause}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        params_data = [*params, page_size, offset]

        try:
            async with self._pool.acquire() as conn:
                total_count = await conn.fetchval(count_query, *params)
                rows = await conn.fetch(data_query, *params_data)
            logs = [_row_to_dict(row) for row in rows]
            return logs, total_count or 0
        except Exception as e:
            logger.error("페이지네이션 감사 로그 조회 실패: %s", e)
            return [], 0

    async def get_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """감사 통계를 반환한다.

        Args:
            start_date: 시작 날짜 (ISO 8601 형식)
            end_date: 종료 날짜 (ISO 8601 형식)

        Returns:
            통계 딕셔너리 (total_requests, unique_users, success_rate 등)
        """
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if start_date:
            conditions.append(f"created_at >= ${idx}::timestamptz")
            params.append(start_date)
            idx += 1

        if end_date:
            conditions.append(f"created_at <= ${idx}::timestamptz")
            params.append(end_date)
            idx += 1

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        try:
            async with self._pool.acquire() as conn:
                # 기본 통계
                basic_row = await conn.fetchrow(
                    f"""
                    SELECT
                        COUNT(*) AS total_requests,
                        COUNT(DISTINCT user_id) AS unique_users
                    FROM audit_logs
                    {where_clause}
                    """,
                    *params,
                )

                # 쿼리 실행 성공률 및 평균 실행 시간
                qe_conditions = list(conditions)
                qe_conditions.append("event_type = 'query_execution'")
                qe_where = "WHERE " + " AND ".join(qe_conditions)

                qe_row = await conn.fetchrow(
                    f"""
                    SELECT
                        AVG(CASE WHEN detail->>'success' = 'true' THEN 1 ELSE 0 END)
                            AS success_rate,
                        AVG((detail->>'execution_time_ms')::float)
                            AS avg_execution_time_ms
                    FROM audit_logs
                    {qe_where}
                    """,
                    *params,
                )

                # Top 사용자
                top_users_rows = await conn.fetch(
                    f"""
                    SELECT user_id, COUNT(*) AS count
                    FROM audit_logs
                    {where_clause}
                    AND user_id IS NOT NULL
                    GROUP BY user_id
                    ORDER BY count DESC
                    LIMIT 10
                    """,
                    *params,
                ) if where_clause else await conn.fetch(
                    """
                    SELECT user_id, COUNT(*) AS count
                    FROM audit_logs
                    WHERE user_id IS NOT NULL
                    GROUP BY user_id
                    ORDER BY count DESC
                    LIMIT 10
                    """,
                )

                # Top 테이블 (detail->'target_tables' 배열 unnest)
                top_tables_rows: list[asyncpg.Record] = []
                try:
                    top_tables_rows = await conn.fetch(
                        f"""
                        SELECT tbl, COUNT(*) AS count
                        FROM audit_logs,
                             jsonb_array_elements_text(
                                 CASE
                                     WHEN detail ? 'target_tables'
                                          AND jsonb_typeof(detail->'target_tables') = 'array'
                                     THEN detail->'target_tables'
                                     ELSE '[]'::jsonb
                                 END
                             ) AS tbl
                        {where_clause}
                        GROUP BY tbl
                        ORDER BY count DESC
                        LIMIT 10
                        """,
                        *params,
                    ) if where_clause else await conn.fetch(
                        """
                        SELECT tbl, COUNT(*) AS count
                        FROM audit_logs,
                             jsonb_array_elements_text(
                                 CASE
                                     WHEN detail ? 'target_tables'
                                          AND jsonb_typeof(detail->'target_tables') = 'array'
                                     THEN detail->'target_tables'
                                     ELSE '[]'::jsonb
                                 END
                             ) AS tbl
                        GROUP BY tbl
                        ORDER BY count DESC
                        LIMIT 10
                        """,
                    )
                except Exception as e:
                    logger.warning("Top 테이블 집계 실패 (빈 리스트 반환): %s", e)

                # 일별 통계
                daily_rows = await conn.fetch(
                    f"""
                    SELECT
                        DATE(created_at) AS date,
                        COUNT(*) AS count,
                        COUNT(DISTINCT user_id) AS unique_users
                    FROM audit_logs
                    {where_clause}
                    GROUP BY DATE(created_at)
                    ORDER BY date DESC
                    LIMIT 30
                    """,
                    *params,
                )

                # 보안 경고 건수
                sa_conditions = list(conditions)
                sa_conditions.append("event_type = 'security_alert'")
                sa_where = "WHERE " + " AND ".join(sa_conditions)
                security_alerts_count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM audit_logs {sa_where}",
                    *params,
                )

                # 로그인 실패 건수
                lf_conditions = list(conditions)
                lf_conditions.append("event_type = 'login_fail'")
                lf_where = "WHERE " + " AND ".join(lf_conditions)
                failed_login_count = await conn.fetchval(
                    f"SELECT COUNT(*) FROM audit_logs {lf_where}",
                    *params,
                )

            return {
                "total_requests": basic_row["total_requests"] if basic_row else 0,
                "unique_users": basic_row["unique_users"] if basic_row else 0,
                "success_rate": (
                    round(float(qe_row["success_rate"]), 4)
                    if qe_row and qe_row["success_rate"] is not None
                    else None
                ),
                "avg_execution_time_ms": (
                    round(float(qe_row["avg_execution_time_ms"]), 2)
                    if qe_row and qe_row["avg_execution_time_ms"] is not None
                    else None
                ),
                "top_users": [
                    {"user_id": r["user_id"], "count": r["count"]}
                    for r in top_users_rows
                ],
                "top_tables": [
                    {"table": r["tbl"], "count": r["count"]}
                    for r in top_tables_rows
                ],
                "daily_counts": [
                    {
                        "date": r["date"].isoformat() if r["date"] else None,
                        "count": r["count"],
                        "unique_users": r["unique_users"],
                    }
                    for r in daily_rows
                ],
                "security_alerts_count": security_alerts_count or 0,
                "failed_login_count": failed_login_count or 0,
            }

        except Exception as e:
            logger.error("감사 통계 조회 실패: %s", e)
            return {
                "total_requests": 0,
                "unique_users": 0,
                "success_rate": None,
                "avg_execution_time_ms": None,
                "top_users": [],
                "top_tables": [],
                "daily_counts": [],
                "security_alerts_count": 0,
                "failed_login_count": 0,
            }

    async def get_user_activity(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """특정 사용자의 활동 이력을 반환한다.

        Args:
            user_id: 사용자 ID
            limit: 최대 조회 건수

        Returns:
            활동 이력 목록
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, event_type, user_id, detail, ip_address, created_at
                    FROM audit_logs
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id,
                    limit,
                )
            return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("사용자 활동 이력 조회 실패: %s", e)
            return []

    async def get_alerts(
        self,
        limit: int = 100,
    ) -> list[dict]:
        """보안 경고 목록을 반환한다.

        Args:
            limit: 최대 조회 건수

        Returns:
            보안 경고 목록
        """
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, event_type, user_id, detail, ip_address, created_at
                    FROM audit_logs
                    WHERE event_type = 'security_alert'
                    ORDER BY created_at DESC
                    LIMIT $1
                    """,
                    limit,
                )
            return [_row_to_dict(row) for row in rows]
        except Exception as e:
            logger.error("보안 경고 조회 실패: %s", e)
            return []

    async def cleanup_old_logs(self, retention_days: int) -> int:
        """보관 기간이 지난 로그를 삭제한다.

        Args:
            retention_days: 보관 일수

        Returns:
            삭제된 행 수
        """
        try:
            async with self._pool.acquire() as conn:
                result = await conn.execute(
                    """
                    DELETE FROM audit_logs
                    WHERE created_at < NOW() - ($1 || ' days')::interval
                    """,
                    str(retention_days),
                )
                # asyncpg execute returns "DELETE N"
                deleted = int(result.split()[-1]) if result else 0
                logger.info("오래된 감사 로그 %d건 삭제 (보관 기간: %d일)", deleted, retention_days)
                return deleted
        except Exception as e:
            logger.error("오래된 감사 로그 삭제 실패: %s", e)
            return 0
