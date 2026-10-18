"""질의 결과 저장소 — 저장·소유 확인·첨부 파일명·다운로드 감사.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException, Request

from src.api.dependencies import is_admin

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


# 비동기 결과 저장소
_MAX_RESULTS_STORE_SIZE = 1000
_results_store: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _store_result(query_id: str, data: dict[str, Any], *, owner: str | None) -> None:
    """결과를 저장하고, 최대 크기를 초과하면 오래된 항목을 제거한다.

    ``owner``는 질의한 사용자의 ``sub``다 — 결과·파일 조회 라우트가 소유자 확인에 쓴다
    (`_owned_result`). 키워드 필수로 둬 저장 지점이 소유자를 빠뜨리지 못하게 한다.
    """
    _results_store[query_id] = {**data, "owner_sub": owner}
    while len(_results_store) > _MAX_RESULTS_STORE_SIZE:
        _results_store.popitem(last=False)


def owned_result(
    request: Request,
    query_id: str,
    current_user: dict[str, Any],
    *,
    not_found: str = "결과를 찾을 수 없습니다.",
    allow_admin: bool = True,
    denied: str = "이 결과는 질의한 사용자만 받을 수 있습니다.",
) -> dict[str, Any]:
    """저장된 결과를 돌려주되, 요청자가 질의한 사람(또는 관리자)이 아니면 403.

    - 없는 query_id는 404(종전 문구 유지).
    - 인증 비활성(`AUTH_ENABLED=false`)은 종전 동작 그대로 통과한다 — 전원이 `anonymous`
      한 명이라 소유자를 가를 수 없다.
    - 관리자(`role == admin`, DB 실시간 값)는 통과한다(D-069 통합 RBAC). 단 ``allow_admin=False``
      (첨부 원본)는 관리자도 소유자여야 한다 — DRM 해제된 평문 원본이라 관리자 복호화
      다운로드(D-156 후속1 「승인 전 범위 밖」)와 같은 효과가 되기 때문이다.
    - 소유자가 없는 결과는 일반 사용자에게 내주지 않는다(fail-closed).
    """
    stored = _results_store.get(query_id)
    if stored is None:
        raise HTTPException(status_code=404, detail=not_found)
    if not request.app.state.config.auth.enabled:
        return stored
    if allow_admin and is_admin(current_user):
        return stored
    owner = stored.get("owner_sub")
    if not owner or owner != current_user.get("sub"):
        logger.warning(
            "질의 결과 접근 거부: query_id=%s 요청자=%s (소유자 아님)",
            query_id[:8], current_user.get("sub"),
        )
        raise HTTPException(status_code=403, detail=denied)
    return stored


def attachment_disposition(file_name: str) -> str:
    """다운로드 `Content-Disposition` 값을 만든다(RFC 6266 · RFC 5987).

    헤더는 latin-1로만 나가므로 한글 파일명을 그대로 넣으면 응답 생성이 500으로 끝난다.
    원래 이름은 `filename*=UTF-8''<퍼센트 인코딩>`으로 싣고, `filename=`에는 인쇄 가능한
    ASCII만 남긴 대체 이름을 둔다(따옴표·역슬래시·제어 문자도 `_`). 대체할 것이 없는 ASCII
    이름은 종전 헤더 그대로다.
    """
    fallback = "".join(
        c if " " <= c <= "~" and c not in '"\\' else "_" for c in file_name
    )
    if fallback == file_name:
        return f'attachment; filename="{file_name}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name, safe='')}"


async def audit_file_download(
    request: Request,
    current_user: dict[str, Any],
    *,
    file_name: str,
    file_type: str,
    file_size: int,
) -> None:
    """파일 다운로드를 감사에 기록한다(`AuditService.log_file_download`).

    감사 실패는 다운로드를 막지 않는다 — 다만 삼키지 않고 경고로 남긴다
    (`_audit_user_request`와 같다).
    """
    audit_service = getattr(request.app.state, "audit_service", None)
    if not audit_service:
        return
    try:
        await audit_service.log_file_download(
            user_id=current_user.get("sub") if current_user else None,
            file_name=file_name,
            file_type=file_type,
            file_size=file_size,
            client_ip=getattr(request.state, "client_ip", None)
            or (request.client.host if request.client else None),
            request_id=getattr(request.state, "request_id", None),
        )
    except Exception as e:
        logger.warning("파일 다운로드 감사 기록 실패: %s", e)


# D-337 ③ 옛 `_` 이름 별칭(plans/179 W2) — 다음 계획에서 grep 0 확인 후 제거
_owned_result = owned_result


# D-337 ③ 옛 `_` 이름 별칭(plans/179 W2) — 다음 계획에서 grep 0 확인 후 제거
_attachment_disposition = attachment_disposition


# D-337 ③ 옛 `_` 이름 별칭(plans/179 W2) — 다음 계획에서 grep 0 확인 후 제거
_audit_file_download = audit_file_download
