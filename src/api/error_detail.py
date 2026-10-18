"""오류 응답 문구 — 예외 원문은 서버 로그에만 두고 응답에는 요청 번호를 싣는다(plans/163 S3).

예외 문자열에는 DSN·SQL·경로·외부 API 응답이 섞일 수 있어 사용자·관리자 응답에 그대로
싣지 않는다. 요청 번호는 감사 미들웨어(`audit_middleware.py`)가 만든 `request_id`이며
같은 값이 structlog 컨텍스트와 응답 헤더 `X-Request-ID`에 실린다 — 운영자는 이 번호로
서버 로그의 원문을 찾는다.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

_UNKNOWN_REQUEST_ID = "-"


def current_request_id(request: Any = None) -> str:
    """요청 번호를 돌려준다 — `request.state` 우선, 없으면 structlog 컨텍스트, 둘 다 없으면 "-"."""
    if request is not None:
        rid = getattr(getattr(request, "state", None), "request_id", None)
        if rid:
            return str(rid)
    rid = structlog.contextvars.get_contextvars().get("request_id")
    return str(rid) if rid else _UNKNOWN_REQUEST_ID


def user_error_detail(
    message: str,
    exc: BaseException,
    logger: logging.Logger,
    request: Any = None,
) -> str:
    """사용자 응답 문구 — 고정 문구 + 요청 번호. 예외 원문은 `logger`에 스택과 함께 남긴다."""
    rid = current_request_id(request)
    logger.error("%s (요청 %s)", message, rid, exc_info=exc)
    return f"{message} (요청 {rid})"


def admin_error_detail(
    action: str,
    exc: BaseException,
    logger: logging.Logger,
    request: Any = None,
    *,
    input_error: bool = False,
) -> str:
    """관리자 응답 문구(plans/163 §2.1a #2 A안) — 작업명 + 예외 종류 + 요청 번호.

    예) 「설정 저장 실패했습니다 (ValueError · 요청 1a2b3c4d) — 상세는 서버 로그」
    `input_error=True`(요청 값 해석 실패 등 클라이언트 입력 오류)면 스택 없이 WARNING으로 남긴다.
    """
    rid = current_request_id(request)
    if input_error:
        logger.warning("%s 실패 (요청 %s): %r", action, rid, exc)
    else:
        logger.error("%s 실패 (요청 %s)", action, rid, exc_info=exc)
    return f"{action} 실패했습니다 ({type(exc).__name__} · 요청 {rid}) — 상세는 서버 로그"
