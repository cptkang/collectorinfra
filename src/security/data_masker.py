"""민감 데이터 마스킹 모듈.

쿼리 결과에서 비밀번호, 토큰 등 민감 정보를 마스킹 처리한다.
컬럼명 기반 마스킹과 값 패턴 기반 마스킹을 모두 지원한다.

컬럼명 판정(`is_sensitive_column`)은 결과 행 마스킹과 SQL 생성 프롬프트의 표본 렌더(단일·멀티
경로 — `mask_sensitive_sample_columns`)가 함께 쓴다.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import Any

from src.config import SecurityConfig, load_config

logger = logging.getLogger(__name__)

#: 한글 민감 컬럼 표현 — 설정 목록(`SECURITY_SENSITIVE_COLUMNS`)과 별개로 항상 본다(부분 매칭).
#: 운영 `.env`가 설정 목록을 영문 목록으로 덮어쓰므로 설정 기본값에 두면 운영에 닿지 않는다.
KOREAN_SENSITIVE_COLUMN_TERMS: tuple[str, ...] = (
    "비밀번호", "패스워드", "암호", "비밀키", "인증키",
)


def is_sensitive_column(column_name: Any, sensitive_columns: Iterable[str]) -> bool:
    """컬럼명이 민감 데이터에 해당하는지 판단한다 — 결과 행 마스킹·표본 렌더 공용 규칙.

    설정 목록(소문자 비교 · 정확·부분 매칭 — password_hash, api_key_value 등)과 내장 한글 표현
    (`KOREAN_SENSITIVE_COLUMN_TERMS` · 부분 매칭)을 본다.

    Args:
        column_name: 컬럼명
        sensitive_columns: 설정의 민감 컬럼 목록

    Returns:
        민감 컬럼이면 True
    """
    lower = str(column_name).lower()
    for sensitive in sensitive_columns:
        if sensitive.lower() in lower:
            return True
    return any(term in lower for term in KOREAN_SENSITIVE_COLUMN_TERMS)


def mask_sensitive_sample_columns(samples: list[Any]) -> list[Any]:
    """프롬프트에 싣는 표본 행에서 민감 컬럼 값을 마스크 문자열로 바꾼다(단일·멀티 표본 렌더 공용).

    판정은 결과 행 마스킹과 같은 `is_sensitive_column`이고, 목록·마스크 문자열은 앱 설정
    (`security`)에서 읽는다 — 읽지 못하면 코드 기본값으로 가린다. 민감 컬럼이 없으면 입력 목록을
    그대로 돌려준다(프롬프트 바이트 불변).

    Args:
        samples: 표본 행 목록(dict 행 · 그 외 값은 그대로 둔다)

    Returns:
        민감 컬럼 값을 가린 표본 행 목록
    """
    try:
        security = load_config().security
    except Exception:  # noqa: BLE001 — 설정을 못 읽으면 코드 기본값으로 가린다(가리는 쪽)
        security = SecurityConfig.model_construct()
    columns = list(security.sensitive_columns)
    masked: list[Any] = []
    changed = False
    for row in samples:
        if isinstance(row, dict) and any(is_sensitive_column(k, columns) for k in row):
            masked.append({
                k: security.mask_pattern if is_sensitive_column(k, columns) else v
                for k, v in row.items()
            })
            changed = True
        else:
            masked.append(row)
    return masked if changed else samples


class DataMasker:
    """쿼리 결과의 민감 데이터를 마스킹한다."""

    # IP 주소 및 이메일 패턴
    _IP_PATTERN: re.Pattern[str] = re.compile(
        r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
    )
    _EMAIL_PATTERN: re.Pattern[str] = re.compile(
        r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    )

    # 값 패턴 기반 마스킹 (컬럼명과 관계없이)
    SENSITIVE_VALUE_PATTERNS: list[re.Pattern[str]] = [
        # 기존 패턴
        re.compile(r"^sk-[a-zA-Z0-9]{20,}$"),       # API 키 패턴
        re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$"),   # Base64 인코딩된 시크릿
        re.compile(r"^\$2[aby]\$\d{2}\$.{53}$"),      # bcrypt 해시
        re.compile(r"^eyJ[a-zA-Z0-9_-]+\."),          # JWT 토큰

        # 추가 패턴
        re.compile(r"^AKIA[0-9A-Z]{16}$"),             # AWS Access Key
        re.compile(r"^ghp_[a-zA-Z0-9]{36}$"),          # GitHub Personal Token
        re.compile(r"^glpat-[a-zA-Z0-9_-]{20,}$"),     # GitLab Token
        re.compile(r"^\d{3}-\d{2}-\d{4}$"),            # SSN (미국)
        re.compile(r"^\d{6}-\d{7}$"),                  # 주민번호 (한국)
        re.compile(r"^4[0-9]{12}(?:[0-9]{3})?$"),      # Visa 카드번호
        re.compile(r"^5[1-5][0-9]{14}$"),              # Mastercard
    ]

    def __init__(self, security_config: SecurityConfig) -> None:
        """마스커를 초기화한다.

        Args:
            security_config: 보안 설정 (민감 컬럼 목록, 마스크 패턴)
        """
        self._sensitive_columns: set[str] = {
            col.lower() for col in security_config.sensitive_columns
        }
        self._mask: str = security_config.mask_pattern
        self._mask_ip: bool = security_config.mask_ip
        self._mask_email: bool = security_config.mask_email

    def mask_rows(
        self,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """결과 행 목록에서 민감 데이터를 마스킹한다.

        Args:
            rows: 쿼리 결과 행 목록

        Returns:
            마스킹 처리된 행 목록
        """
        return [self._mask_row(row) for row in rows]

    def _mask_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """단일 행의 민감 데이터를 마스킹한다.

        Args:
            row: 단일 결과 행

        Returns:
            마스킹된 행
        """
        masked: dict[str, Any] = {}
        for key, value in row.items():
            if self._is_sensitive_column(key):
                masked[key] = self._mask
            elif isinstance(value, str) and self._is_sensitive_value(value):
                masked[key] = self._mask
            elif isinstance(value, str) and self._mask_ip and self._IP_PATTERN.match(value):
                masked[key] = self._partial_mask_ip(value)
            elif isinstance(value, str) and self._mask_email and self._EMAIL_PATTERN.match(value):
                masked[key] = self._partial_mask_email(value)
            else:
                masked[key] = value
        return masked

    def _is_sensitive_column(self, column_name: str) -> bool:
        """컬럼명이 민감 데이터에 해당하는지 판단한다(공용 규칙 `is_sensitive_column`).

        Args:
            column_name: 컬럼명

        Returns:
            민감 컬럼이면 True
        """
        return is_sensitive_column(column_name, self._sensitive_columns)

    def _is_sensitive_value(self, value: str) -> bool:
        """값 자체가 민감 데이터 패턴인지 판단한다.

        Args:
            value: 확인할 문자열

        Returns:
            민감 값이면 True
        """
        for pattern in self.SENSITIVE_VALUE_PATTERNS:
            if pattern.match(value):
                return True
        return False

    def _partial_mask_ip(self, ip: str) -> str:
        """IP 주소의 마지막 옥텟을 마스킹한다.

        예: 192.168.1.100 -> 192.168.1.***

        Args:
            ip: IP 주소 문자열

        Returns:
            마스킹된 IP 주소
        """
        parts = ip.split(".")
        if len(parts) == 4:
            parts[-1] = "***"
            return ".".join(parts)
        return self._mask

    def _partial_mask_email(self, email: str) -> str:
        """이메일의 로컬 파트를 부분 마스킹한다.

        예: admin@company.com -> a***n@company.com

        Args:
            email: 이메일 주소

        Returns:
            마스킹된 이메일 주소
        """
        if "@" not in email:
            return self._mask
        local, domain = email.rsplit("@", 1)
        if len(local) <= 2:
            masked_local = "*" * len(local)
        else:
            masked_local = local[0] + "***" + local[-1]
        return f"{masked_local}@{domain}"
