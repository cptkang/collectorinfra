"""Softcamp ServiceLinker DRM 해제 계층 (Plan 69 / D-123).

업로드 파일의 DRM 암호문 감지(detector)와 복호화 어댑터(decryptor, scsl)를 제공한다.
`DRM_ENABLED=false`(기본)에서는 Passthrough로 동작하여 기존 경로에 영향이 없다.
"""

from src.infrastructure.drm.decryptor import (
    DrmDecryptError,
    DrmDecryptor,
    PassthroughDecryptor,
    get_decryptor,
)
from src.infrastructure.drm.detector import FileKind, detect_file_kind, header_hex

__all__ = [
    "DrmDecryptError",
    "DrmDecryptor",
    "FileKind",
    "PassthroughDecryptor",
    "detect_file_kind",
    "get_decryptor",
    "header_hex",
]
