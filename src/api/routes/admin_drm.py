"""DRM 연동 진단 라우트 — `/admin/drm/{status,verify}` (plans/166 A7 — `admin.py`에서 이동).

`admin.router`가 이 라우터를 원래 선언 위치에 포함한다(경로·이름·OpenAPI 불변). 옛 경로
재노출은 사용처 0이라 제거했다(plans/166 단계 8).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from src.api.dependencies import require_admin_user

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 A7)
logger = logging.getLogger("src.api.routes.admin")
router = APIRouter()


# --- 엔드포인트: DRM 연동 진단 (Plan 74 §4.2) ---
#
# 실기 환경이 운영계뿐이므로 셸 없이 연동 상태를 점검할 수 있게 한다.
# 복호화 결과 파일은 반환하지 않는다 — 진단 정보만 돌려주므로 반복 호출로도
# 문서 내용이 복원되지 않는다(복호화 오라클 방지, 계획서 §7 비범위).


@router.get("/admin/drm/status")
async def get_drm_status(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict:  # type: ignore[type-arg]
    """DRM 연동 환경 상태를 점검한다 (파일 업로드 없음).

    설정 경로 4종·키 파일 갱신 시각·java 런타임·작업 디렉터리를 확인한다.
    키 파일 mtime은 KeyManager 생존 신호다(24시간 주기 갱신).
    """
    from src.infrastructure.drm.diagnostics import check_environment

    return check_environment(request.app.state.config.drm)


@router.post("/admin/drm/verify")
async def verify_drm_sample(
    request: Request,
    file: UploadFile = File(...),
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict:  # type: ignore[type-arg]
    """암호화 샘플을 복호화해 **진단 결과만** 반환한다 (평문 파일 미반환).

    실패도 200 + 구조화된 결과로 반환한다 — 실패가 곧 진단 데이터이며,
    화면이 에러로 깨지면 정작 필요한 ret 값을 볼 수 없다.
    """
    from src.infrastructure.drm.diagnostics import verify_sample
    from src.security.audit_logger import log_drm_decrypt

    filename = file.filename or "sample"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("xlsx", "docx"):
        return {
            "file_name": filename,
            "detected": None,
            "success": False,
            "message": (
                f"지원하지 않는 형식입니다: .{ext or '(없음)'}. "
                "폼필 대상과 동일하게 .xlsx 또는 .docx만 진단합니다."
            ),
        }

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        return {
            "file_name": filename,
            "detected": None,
            "success": False,
            "message": "파일 크기가 10MB를 초과합니다.",
        }

    result = await verify_sample(request.app.state.config.drm, file_bytes, filename)

    await log_drm_decrypt(
        file_name=filename,
        file_size_bytes=len(file_bytes),
        success=bool(result.get("success")),
        error=None if result.get("success") else result.get("message"),
        ret_code=result.get("ret"),
        elapsed_ms=result.get("elapsed_ms"),
        user_id=_admin.get("sub"),
        mode="admin_verify",
    )
    logger.info(
        "DRM 진단 실행: file=%s detected=%s ret=%s success=%s (by %s)",
        filename, result.get("detected"), result.get("ret"),
        result.get("success"), _admin.get("sub"),
    )
    return result
