"""DRM 복호화 어댑터 인터페이스 (Plan 74 §2.4).

- `DrmDecryptor`: 복호화 프로토콜 — 구현체는 평문 바이트를 반환하거나
  `DrmDecryptError`를 던진다 (침묵 폴백 금지).
- `PassthroughDecryptor`: `DRM_ENABLED=false` 환경용 — 입력을 그대로 반환.
- `get_decryptor()`: 설정 기반 팩토리. 프로세스당 싱글턴을 유지한다
  (ScslCliDecryptor의 temp sweep이 요청별 인스턴스에서 반복 실행되어
  동시 요청의 작업 파일을 지우는 사고 방지).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:
    from src.config import DrmConfig


class DrmDecryptError(Exception):
    """DRM 복호화 실패 — 사유를 구조화해 사용자 응답·로그에 노출한다."""

    def __init__(
        self,
        reason: str,
        ret_code: Optional[int] = None,
        detail: Optional[str] = None,
    ) -> None:
        self.reason = reason
        self.ret_code = ret_code
        self.detail = detail
        super().__init__(reason)

    def __str__(self) -> str:
        parts = [self.reason]
        if self.ret_code is not None:
            parts.append(f"(ret={self.ret_code})")
        return " ".join(parts)


class DrmDecryptor(Protocol):
    """복호화 어댑터 프로토콜."""

    async def decrypt(self, file_bytes: bytes, filename: str) -> bytes:
        """암호문 바이트를 평문 바이트로 복호화한다.

        Raises:
            DrmDecryptError: 복호화 실패 시 (사유 포함)
        """
        ...


class PassthroughDecryptor:
    """DRM 미연동 환경용 — 입력을 그대로 반환한다."""

    async def decrypt(self, file_bytes: bytes, filename: str) -> bytes:
        return file_bytes


_decryptor_singleton: Optional[DrmDecryptor] = None


def get_decryptor(config: "DrmConfig") -> DrmDecryptor:
    """설정에 맞는 복호화 어댑터를 반환한다 (프로세스당 싱글턴)."""
    global _decryptor_singleton
    if _decryptor_singleton is None:
        if config.enabled:
            from src.infrastructure.drm.scsl import ScslCliDecryptor

            _decryptor_singleton = ScslCliDecryptor(config)
        else:
            _decryptor_singleton = PassthroughDecryptor()
    return _decryptor_singleton


def reset_decryptor() -> None:
    """싱글턴을 초기화한다 (테스트 전용)."""
    global _decryptor_singleton
    _decryptor_singleton = None
