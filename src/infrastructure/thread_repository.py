"""질의응답 스레드 저장소 (D-248).

사용자 화면의 대화를 턴 단위로 앱 DB(감사 로그와 같은 PostgreSQL 풀)에 남긴다.
LangGraph 체크포인트는 노드 중간 메시지까지 섞인 실행 상태라 "무엇을 묻고 무엇을
받았나"를 다시 보여 주는 용도로는 쓸 수 없어서 따로 둔다.

소유 키(`owner_key`)가 조회 경계다 — 모든 조회·삭제는 소유 키를 조건으로 건다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

# 소유자당 보관 스레드 상한. 상한 없는 누적은 이 저장소의 금기다(D-183 HISTORY_MAX 전례).
MAX_THREADS_PER_OWNER = 200

THREAD_DDL = """
CREATE TABLE IF NOT EXISTS query_thread_turns (
    id                  BIGSERIAL PRIMARY KEY,
    owner_key           VARCHAR(120) NOT NULL,
    thread_id           VARCHAR(100) NOT NULL,
    query_id            VARCHAR(64),
    user_query          TEXT NOT NULL,
    response            TEXT,
    status              VARCHAR(30),
    executed_sql        TEXT,
    row_count           INTEGER,
    processing_time_ms  DOUBLE PRECISION,
    has_upload          BOOLEAN NOT NULL DEFAULT FALSE,
    db_scope            JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_query_thread_turns_owner
    ON query_thread_turns(owner_key, thread_id, id);
"""


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _deleted_count(status: str) -> int:
    """asyncpg execute 결과("DELETE 3")에서 행 수를 꺼낸다."""
    try:
        return int(status.rsplit(" ", 1)[-1])
    except (ValueError, AttributeError):
        return 0


class PostgresThreadRepository:
    """PostgreSQL 기반 질의응답 스레드 저장소."""

    def __init__(
        self, pool: asyncpg.Pool, *, max_threads_per_owner: int = MAX_THREADS_PER_OWNER
    ) -> None:
        self._pool = pool
        self._max_threads = max_threads_per_owner

    async def ensure_tables(self) -> None:
        """테이블이 없으면 만든다. 실패는 기동을 막지 않고 경고로 남긴다."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(THREAD_DDL)
        except Exception as e:
            logger.warning("질의응답 스레드 테이블 DDL 실행 실패: %s", e)

    async def add_turn(self, owner_key: str, turn: dict[str, Any]) -> None:
        """턴 1건을 기록하고, 상한을 넘은 오래된 스레드를 지운다."""
        db_scope = turn.get("db_scope")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO query_thread_turns
                        (owner_key, thread_id, query_id, user_query, response, status,
                         executed_sql, row_count, processing_time_ms, has_upload, db_scope)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    owner_key,
                    turn["thread_id"],
                    turn.get("query_id"),
                    turn.get("user_query") or "",
                    turn.get("response"),
                    turn.get("status"),
                    turn.get("executed_sql"),
                    turn.get("row_count"),
                    turn.get("processing_time_ms"),
                    bool(turn.get("has_upload")),
                    json.dumps(db_scope, ensure_ascii=False) if db_scope is not None else None,
                )
                await conn.execute(
                    """
                    DELETE FROM query_thread_turns
                    WHERE owner_key = $1 AND thread_id IN (
                        SELECT thread_id FROM query_thread_turns
                        WHERE owner_key = $1
                        GROUP BY thread_id
                        ORDER BY MAX(id) DESC
                        OFFSET $2
                    )
                    """,
                    owner_key,
                    self._max_threads,
                )

    async def list_threads(
        self, owner_key: str, limit: int = MAX_THREADS_PER_OWNER
    ) -> list[dict[str, Any]]:
        """소유자의 스레드 목록(최근 대화 순)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT thread_id,
                       COUNT(*) AS turn_count,
                       MIN(created_at) AS started_at,
                       MAX(created_at) AS updated_at,
                       (ARRAY_AGG(user_query ORDER BY id))[1] AS title,
                       STRING_AGG(user_query, E'\\n' ORDER BY id) AS queries
                FROM query_thread_turns
                WHERE owner_key = $1
                GROUP BY thread_id
                ORDER BY MAX(id) DESC
                LIMIT $2
                """,
                owner_key,
                limit,
            )
        return [
            {
                "thread_id": r["thread_id"],
                "title": r["title"],
                "queries": r["queries"],
                "turn_count": r["turn_count"],
                "started_at": _iso(r["started_at"]),
                "updated_at": _iso(r["updated_at"]),
            }
            for r in rows
        ]

    async def get_turns(self, owner_key: str, thread_id: str) -> list[dict[str, Any]]:
        """스레드의 턴을 기록 순으로 돌려준다. 남의 스레드는 빈 목록이다."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT query_id, user_query, response, status, executed_sql, row_count,
                       processing_time_ms, has_upload, db_scope, created_at
                FROM query_thread_turns
                WHERE owner_key = $1 AND thread_id = $2
                ORDER BY id
                """,
                owner_key,
                thread_id,
            )
        return [
            {
                "query_id": r["query_id"],
                "user_query": r["user_query"],
                "response": r["response"],
                "status": r["status"],
                "executed_sql": r["executed_sql"],
                "row_count": r["row_count"],
                "processing_time_ms": r["processing_time_ms"],
                "has_upload": r["has_upload"],
                "db_scope": json.loads(r["db_scope"]) if r["db_scope"] else None,
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ]

    async def delete_thread(self, owner_key: str, thread_id: str) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM query_thread_turns WHERE owner_key = $1 AND thread_id = $2",
                owner_key,
                thread_id,
            )
        return _deleted_count(status)

    async def delete_all(self, owner_key: str) -> int:
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "DELETE FROM query_thread_turns WHERE owner_key = $1", owner_key
            )
        return _deleted_count(status)
