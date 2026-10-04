"""APM 장기 작업 API — 내 작업 목록 · 상태 · 취소 · 전체 결과 받기 (plans/134 W0-B M-10·M-11 ·
SPEC-apm-question-coverage §3.8 · D-262 · D-299 ④).

- 인증 필수(`require_user` — 인증 꺼짐은 `anonymous`). 작업은 **장부 소유자 또는 관리자**만
  (D-262 `_owned_result`와 같은 판정). 장부에 없으면 404, 남의 작업이면 403 — 장부를 먼저 보고
  게이트웨이를 부른다. 게이트웨이에도 장부의 `owner`를 다시 싣는다(이중 확인).
- 다운로드는 청크를 차례로 받아 흘려보낸다(전량을 메모리에 모으지 않는다) · CSV는 화면과 같은
  `DataMasker` · BOM · RFC 5987 파일 이름(`_attachment_disposition`) · 감사(`_audit_file_download` —
  완료·중단 모두 실제로 보낸 바이트로 남긴다 · 중단은 경고 로그).
  `completed`·`partial`만 받는다(그 밖 409) · `partial`은 파일 이름 `_partial` + 헤더
  `X-Apm-Job-State: partial`.
- 판단은 `src/orchestration/apm_jobs.py`(`ApmJobService`)에 있고 이 모듈은 HTTP로 옮기기만 한다.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from src.api.dependencies import require_user
from src.api.routes.query import _attachment_disposition, _audit_file_download
from src.orchestration.apm_jobs import (
    ApmJobService,
    JobConflictError,
    JobForbiddenError,
    JobNotFoundError,
    JobUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: 부분 결과 다운로드임을 알리는 응답 헤더(파일 이름 접미 `_partial`과 함께).
JOB_STATE_HEADER = "X-Apm-Job-State"
#: 응답이 끊겨도 감사 쓰기가 끝나도록 분리한 태스크의 강참조.
_PENDING_AUDITS: set[asyncio.Task[None]] = set()


def _service(request: Request) -> ApmJobService:
    return ApmJobService(request.app.state.config)


def _http_error(e: Exception) -> HTTPException:
    if isinstance(e, JobNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, JobForbiddenError):
        return HTTPException(status_code=403, detail=str(e))
    if isinstance(e, JobConflictError):
        return HTTPException(status_code=409, detail=str(e))
    logger.warning("APM 작업 API 게이트웨이 오류: %s", e)
    return HTTPException(status_code=502, detail=f"작업 정보를 가져오지 못했습니다: {e}")


_ERRORS = (JobNotFoundError, JobForbiddenError, JobConflictError, JobUnavailableError)


@router.get("/apm/jobs")
async def list_apm_jobs(
    request: Request,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """내 작업 목록(장부 — 게이트웨이를 부르지 않는다)."""
    return {"jobs": await _service(request).list_jobs(current_user)}


@router.get("/apm/jobs/{job_id}")
async def get_apm_job(
    request: Request,
    job_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """작업 상태·진행·예상 시간 · 끝났으면 미리보기(가림)·전체 행 수·한계."""
    try:
        return await _service(request).status(job_id, current_user)
    except _ERRORS as e:
        raise _http_error(e) from e


@router.post("/apm/jobs/{job_id}/cancel")
async def cancel_apm_job(
    request: Request,
    job_id: str,
    current_user: dict[str, Any] = Depends(require_user),
) -> dict[str, Any]:
    """진행 중인 작업을 취소한다(끝난 작업은 상태를 바꾸지 않는다)."""
    try:
        return await _service(request).cancel(job_id, current_user)
    except _ERRORS as e:
        raise _http_error(e) from e


@router.get("/apm/jobs/{job_id}/download")
async def download_apm_job(
    request: Request,
    job_id: str,
    format: Literal["csv", "jsonl", "txt"] = Query("csv"),  # noqa: A002 — API 쿼리 이름
    current_user: dict[str, Any] = Depends(require_user),
) -> StreamingResponse:
    """전체 결과 받기 — 청크를 차례로 받아 흘려보낸다(완료·부분 완료만)."""
    try:
        download = await _service(request).open_download(job_id, current_user, format)
    except _ERRORS as e:
        raise _http_error(e) from e

    async def body() -> AsyncIterator[bytes]:
        sent = 0
        finished = False
        try:
            async for part in download.body:
                sent += len(part)
                yield part
            finished = True
        finally:
            # 일부라도 나간 데이터가 감사 밖에 남지 않게 — 완료·중단(클라이언트 끊김 · 청크 읽기
            # 실패) 모두 실제로 보낸 바이트로 남긴다. 응답 태스크가 취소돼도 감사 쓰기는 끝까지
            # 가도록 별도 태스크로 떼고 기다린다.
            if not finished:
                logger.warning(
                    "APM 작업 결과 다운로드 중단: job_id=%s 파일=%s 보낸 바이트=%d",
                    job_id[:8], download.file_name, sent,
                )
            task = asyncio.ensure_future(_audit_file_download(
                request, current_user, file_name=download.file_name,
                file_type=download.file_type, file_size=sent,
            ))
            _PENDING_AUDITS.add(task)
            task.add_done_callback(_PENDING_AUDITS.discard)
            await asyncio.shield(task)

    headers = {"Content-Disposition": _attachment_disposition(download.file_name)}
    if download.state == "partial":
        headers[JOB_STATE_HEADER] = "partial"
    return StreamingResponse(body(), media_type=download.media_type, headers=headers)
