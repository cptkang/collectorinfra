"""업로드 파일 확장자·DRM 해제.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException

from src.api.error_detail import current_request_id

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


def _get_file_extension(filename: str | None) -> str:
    """파일 확장자를 추출한다."""
    if not filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def _resolve_uploaded_bytes(
    file_bytes: bytes,
    file_ext: str,
    filename: str | None,
    config: Any,
    user_id: str | None = None,
) -> bytes:
    """업로드 바이트를 평문으로 정규화한다 (Plan 74 DRM 해제 / D-156).

    평문(ZIP)은 그대로 반환하고, Softcamp DRM 암호문(SCDS)은 복호화해 반환한다.
    실패는 침묵 폴백 없이 명확한 HTTP 에러로 노출한다 — /query/file과
    /query/file/stream 양쪽에 동일하게 배선할 것(대칭 유지).
    """
    from src.infrastructure.drm import (
        DrmDecryptError,
        detect_file_kind,
        get_decryptor,
        header_hex,
    )
    from src.security.audit_logger import log_drm_decrypt

    kind = detect_file_kind(file_bytes)
    if kind == "plain":
        return file_bytes

    if kind == "unknown":
        logger.warning(
            "업로드 파일 형식 불명: file=%s ext=%s head=%s",
            filename, file_ext, header_hex(file_bytes),
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f".{file_ext} 파일이 손상되었거나 지원하지 않는 형식입니다. "
                "파일을 다시 저장한 뒤 업로드해 주세요."
            ),
        )

    # kind == "drm"
    drm_cfg = config.drm
    if not drm_cfg.enabled:
        await log_drm_decrypt(
            file_name=filename,
            file_size_bytes=len(file_bytes),
            success=False,
            error="drm_disabled",
            user_id=user_id,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                "DRM 암호화 파일입니다. 이 서버는 DRM 해제가 비활성화되어 있어 "
                "처리할 수 없습니다. 평문 파일로 다시 업로드하거나 관리자에게 "
                "문의하세요."
            ),
        )

    decryptor = get_decryptor(drm_cfg)
    start = time.time()
    try:
        plain_bytes = await decryptor.decrypt(
            file_bytes, filename or f"upload.{file_ext}"
        )
    except DrmDecryptError as e:
        await log_drm_decrypt(
            file_name=filename,
            file_size_bytes=len(file_bytes),
            success=False,
            error=str(e),
            ret_code=e.ret_code,
            elapsed_ms=(time.time() - start) * 1000,
            user_id=user_id,
        )
        # 실패 로그는 이 1줄뿐이다 — 원문(파일·사유·ret·상세)과 요청 번호(plans/184 S1 교정)
        rid = current_request_id()
        logger.error(
            "DRM 복호화 실패 (요청 %s): file=%s reason=%s ret=%s detail=%s",
            rid, filename, e.reason, e.ret_code, e.detail,
        )
        if e.user_visible:
            detail = f"DRM 암호화 파일로 확인되나 복호화에 실패했습니다: {e.reason}"
        else:
            # 설정 키 이름·경로가 담긴 사유는 화면에 싣지 않는다 — 원문은 위 로그(plans/184 S1)
            detail = (
                "DRM 암호화 파일로 확인되나 복호화에 실패했습니다: "
                f"서버 DRM 설정 오류 — 관리자에게 문의하세요 (요청 {rid})"
            )
        raise HTTPException(status_code=502, detail=detail)

    await log_drm_decrypt(
        file_name=filename,
        file_size_bytes=len(file_bytes),
        success=True,
        elapsed_ms=(time.time() - start) * 1000,
        user_id=user_id,
    )
    return plain_bytes
