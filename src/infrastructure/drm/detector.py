"""DRM 암호문 결정적 감지 (Plan 69 §2.3).

Softcamp ServiceLinker 암호문은 선두 4바이트가 `SCDS`로 시작한다
(2026-08-07 사내 실측: 전체 매직 `SCDSA002` — 빈/내용 있는 xlsx·docx 모두 동일,
원 확장자 유지. 에러코드명 `NO_SCDSFILE_ACCESS`와 부합).

평문 xlsx/docx는 ZIP 컨테이너라 항상 `PK\x03\x04`로 시작하므로 업로드 즉시
결정적으로 판별한다. 5~8바이트("A002")는 제품 버전·포맷에 따라 달라질 수 있어
판정에서 제외하고 진단 로그(header_hex)로만 남긴다.
"""

from __future__ import annotations

from typing import Literal

ZIP_MAGIC = b"PK\x03\x04"
SCDS_MAGIC = b"SCDS"

FileKind = Literal["plain", "drm", "unknown"]


def detect_file_kind(file_bytes: bytes) -> FileKind:
    """업로드 바이트의 종류를 판별한다.

    Returns:
        "plain": ZIP 시그니처 — 평문 xlsx/docx
        "drm": SCDS 시그니처 — Softcamp DRM 암호문
        "unknown": 둘 다 아님 — 손상 또는 미지원 형식
    """
    if file_bytes.startswith(ZIP_MAGIC):
        return "plain"
    if file_bytes.startswith(SCDS_MAGIC):
        return "drm"
    return "unknown"


def header_hex(file_bytes: bytes, length: int = 8) -> str:
    """진단 로그용 선두 바이트 hex 문자열을 반환한다."""
    return file_bytes[:length].hex()
