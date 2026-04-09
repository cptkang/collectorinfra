"""로컬 인증 프로바이더 구현.

ID/PW 기반 로컬 인증 프로바이더.
UserRepository에서 사용자를 조회하고 bcrypt 비밀번호를 검증한다.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.domain.auth import AuthMethod, AuthProvider
from src.domain.user import UserRepository
from src.utils.password import verify_password

logger = logging.getLogger(__name__)


class LocalAuthProvider(AuthProvider):
    """ID/PW 기반 로컬 인증 프로바이더."""

    def __init__(self, user_repo: UserRepository) -> None:
        self._user_repo = user_repo

    async def authenticate(self, credentials: dict) -> Optional[dict]:
        """ID/PW로 인증을 수행한다.

        Args:
            credentials: {"user_id": "...", "password": "..."}

        Returns:
            인증된 사용자 정보 dict 또는 None (인증 실패)
        """
        user_id = credentials.get("user_id", "")
        password = credentials.get("password", "")

        if not user_id or not password:
            return None

        user = await self._user_repo.get_by_user_id(user_id)
        if not user or not user.is_active:
            return None

        if not verify_password(password, user.hashed_password):
            return None

        return user.to_auth_dict()

    def get_method(self) -> AuthMethod:
        """인증 방식을 반환한다."""
        return AuthMethod.LOCAL
