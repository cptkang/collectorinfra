"""비밀번호 해싱 유틸리티.

bcrypt를 사용한 비밀번호 해싱 및 검증 기능을 제공한다.
src/security/는 arch_check.py에서 infrastructure 계층이므로
범용 유틸리티인 비밀번호 해싱은 utils에 배치한다.
"""

from __future__ import annotations

import bcrypt


def hash_password(plain: str) -> str:
    """비밀번호를 bcrypt로 해시한다.

    Args:
        plain: 평문 비밀번호

    Returns:
        bcrypt 해시 문자열
    """
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """비밀번호를 검증한다.

    Args:
        plain: 평문 비밀번호
        hashed: bcrypt 해시 문자열

    Returns:
        비밀번호 일치 여부
    """
    return bcrypt.checkpw(plain.encode(), hashed.encode())
