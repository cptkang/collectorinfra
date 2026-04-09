"""인증 프로바이더 추상화.

ID/PW 인증(LocalAuthProvider)과 향후 SAML SSO 인증(SamlAuthProvider)을
동일한 인터페이스로 처리할 수 있도록 추상화한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional


class AuthMethod(str, Enum):
    """인증 방식."""

    LOCAL = "local"
    SAML = "saml"


class AuthProvider(ABC):
    """인증 프로바이더 인터페이스.

    ID/PW 인증(LocalAuthProvider)과 향후 SAML SSO 인증(SamlAuthProvider)을
    동일한 인터페이스로 처리할 수 있게 추상화한다.
    """

    @abstractmethod
    async def authenticate(self, credentials: dict) -> Optional[dict]:
        """인증을 수행하고 사용자 정보를 반환한다.

        Args:
            credentials: 인증 정보 (방식에 따라 구조 다름)
                - LOCAL: {"user_id": "...", "password": "..."}
                - SAML: {"saml_response": "..."}

        Returns:
            인증된 사용자 정보 dict 또는 None (인증 실패)
        """
        ...

    @abstractmethod
    def get_method(self) -> AuthMethod:
        """인증 방식을 반환한다."""
        ...
