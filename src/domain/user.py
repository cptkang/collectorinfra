"""사용자 도메인 모델 및 저장소 인터페이스.

사용자(User) 엔터티와 UserRepository, AuditRepository
추상 인터페이스를 정의한다.
Clean Architecture: 인터페이스는 domain에, 구현체는 infrastructure에 배치.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class UserRole(str, Enum):
    """사용자 역할."""

    USER = "user"
    ADMIN = "admin"


class UserStatus(str, Enum):
    """사용자 상태."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    LOCKED = "locked"


@dataclass
class User:
    """사용자 엔터티."""

    user_id: str
    username: str
    hashed_password: str
    role: UserRole = UserRole.USER
    status: UserStatus = UserStatus.ACTIVE
    department: Optional[str] = None
    allowed_db_ids: Optional[list[str]] = None
    auth_method: str = "local"
    login_fail_count: int = 0
    last_login_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        """사용자가 활성 상태인지 확인한다."""
        return self.status == UserStatus.ACTIVE

    def to_auth_dict(self) -> dict:
        """인증 의존성에서 사용할 사용자 정보 dict를 반환한다."""
        return {
            "sub": self.user_id,
            "name": self.username,
            "role": self.role.value,
            "department": self.department,
            "allowed_db_ids": self.allowed_db_ids,
        }


class UserRepository(ABC):
    """사용자 저장소 인터페이스.

    DB 엔진(PostgreSQL/DB2)에 독립적인 인터페이스.
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: str) -> Optional[User]:
        """사용자 ID로 조회한다."""
        ...

    @abstractmethod
    async def create(self, user: User) -> None:
        """사용자를 생성한다."""
        ...

    @abstractmethod
    async def update(self, user: User) -> None:
        """사용자를 수정한다."""
        ...

    @abstractmethod
    async def list_all(self) -> list[User]:
        """전체 사용자 목록을 조회한다."""
        ...

    @abstractmethod
    async def delete(self, user_id: str) -> None:
        """사용자를 삭제한다."""
        ...

    @abstractmethod
    async def exists(self, user_id: str) -> bool:
        """사용자 존재 여부를 확인한다."""
        ...


class AuditRepository(ABC):
    """감사 로그 저장소 인터페이스.

    기존 파일 기반 감사 로그를 DB로 확장하기 위한 인터페이스.
    """

    @abstractmethod
    async def log_event(self, event: dict) -> None:
        """감사 이벤트를 기록한다."""
        ...

    @abstractmethod
    async def query_logs(
        self,
        user_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[dict]:
        """감사 로그를 조회한다."""
        ...

    @abstractmethod
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
        """페이지네이션 감사 로그 조회. (logs, total_count)를 반환한다."""
        ...

    @abstractmethod
    async def get_stats(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """감사 통계를 반환한다."""
        ...

    @abstractmethod
    async def get_user_activity(
        self,
        user_id: str,
        limit: int = 100,
    ) -> list[dict]:
        """특정 사용자의 활동 이력을 반환한다."""
        ...

    @abstractmethod
    async def get_alerts(
        self,
        limit: int = 100,
    ) -> list[dict]:
        """보안 경고 목록을 반환한다."""
        ...

    @abstractmethod
    async def cleanup_old_logs(self, retention_days: int) -> int:
        """보관 기간이 지난 로그를 삭제한다. 삭제 건수를 반환."""
        ...
