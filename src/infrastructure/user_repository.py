"""PostgreSQL 기반 사용자 저장소 구현.

asyncpg를 직접 사용하여 DB2 전환 시 SQL만 교체하면 되도록 한다.
ORM 미사용: DB2 드라이버(ibm_db_sa 등) 호환 문제 방지.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import asyncpg

from src.domain.user import User, UserRepository, UserRole, UserStatus

logger = logging.getLogger(__name__)


class PostgresUserRepository(UserRepository):
    """PostgreSQL 기반 사용자 저장소."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_user_id(self, user_id: str) -> Optional[User]:
        """사용자 ID로 조회한다."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM auth_users WHERE user_id = $1", user_id
            )
        if not row:
            return None
        return self._row_to_user(row)

    async def create(self, user: User) -> None:
        """사용자를 생성한다."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO auth_users
                    (user_id, username, hashed_password, role, status,
                     department, allowed_db_ids, auth_method)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                """,
                user.user_id,
                user.username,
                user.hashed_password,
                user.role.value,
                user.status.value,
                user.department,
                user.allowed_db_ids,
                user.auth_method,
            )

    async def update(self, user: User) -> None:
        """사용자를 수정한다."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE auth_users SET
                    username = $2, role = $3, status = $4,
                    department = $5, allowed_db_ids = $6,
                    login_fail_count = $7, last_login_at = $8,
                    updated_at = NOW()
                WHERE user_id = $1
                """,
                user.user_id,
                user.username,
                user.role.value,
                user.status.value,
                user.department,
                user.allowed_db_ids,
                user.login_fail_count,
                user.last_login_at,
            )

    async def list_all(self) -> list[User]:
        """전체 사용자 목록을 조회한다."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM auth_users ORDER BY created_at DESC"
            )
        return [self._row_to_user(row) for row in rows]

    async def delete(self, user_id: str) -> None:
        """사용자를 삭제한다."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM auth_users WHERE user_id = $1", user_id
            )

    async def exists(self, user_id: str) -> bool:
        """사용자 존재 여부를 확인한다."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM auth_users WHERE user_id = $1", user_id
            )
        return row is not None

    @staticmethod
    def _row_to_user(row: asyncpg.Record) -> User:
        """DB 행을 User 객체로 변환한다."""
        return User(
            user_id=row["user_id"],
            username=row["username"],
            hashed_password=row["hashed_password"],
            role=UserRole(row["role"]),
            status=UserStatus(row["status"]),
            department=row["department"],
            allowed_db_ids=row["allowed_db_ids"],
            auth_method=row["auth_method"],
            login_fail_count=row["login_fail_count"],
            last_login_at=row["last_login_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
