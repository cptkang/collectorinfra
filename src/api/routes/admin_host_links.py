"""관리자 「호스트 연결」 API — 확정·대기·부정 연결과 패턴 · 승인 · 삭제 (plans/196 §1.1 ③ · §5.5).

`NOISE_HOST_LINK_ENABLED`가 켜져 있을 때만 등록한다(꺼져 있으면 라우트가 없어 404). 관리자
의존성(`require_admin_user`) 뒤에서 role 클레임을 한 번 더 명시 검증한다(`admin_investigation`과
같은 관례). 승인·삭제는 감사 로그 대상이다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from src.api.dependencies import is_admin, require_admin_user

logger = logging.getLogger(__name__)

router = APIRouter()

#: 감사 진입점 값(알람 쪽 선택 라우트와 같다).
_ENTRY_POINT = "host_link"
#: 저장소 실패로 다루는 예외 — WATCH 충돌 재시도 소진(`WatchError`)·Redis 연결·시간 초과.
_STORE_ERRORS = (RedisError, OSError)


def _store_failed(action: str) -> HTTPException:
    """저장소 실패 503 — 사용자 연결 라우트와 같은 코드 `link_store_failed`."""
    return HTTPException(
        status_code=503,
        detail={
            "code": "link_store_failed",
            "reason": f"연결 저장소(Redis) {action}에 실패했습니다. 잠시 뒤 다시 시도하세요.",
        },
    )


class HostLinkKey(BaseModel):
    """연결 키 — 제니퍼 소스 id + hostname(저장 시 정규화된다)."""

    source_id: str = Field(..., min_length=1, max_length=128)
    hostname: str = Field(..., min_length=1, max_length=255)


class HostLinkApproval(HostLinkKey):
    """승인 본문 — 키 + 관리자가 본 연결(`db_id`·`server_name`·`at`). 현재 레코드와 다르면 409."""

    db_id: str = Field("", max_length=64, description="목록에서 본 db_id(부정 연결은 빈 값)")
    server_name: str = Field(
        "", max_length=255, description="목록에서 본 서버명(부정 연결은 빈 값)"
    )
    at: str = Field(..., min_length=1, max_length=64, description="목록에서 본 선택 시각 `at`")


def _assert_key(key: HostLinkKey) -> None:
    """키 검증 — `:`가 들었거나 정규화 hostname이 비면 400."""
    from noise_gate.domain.host_link import valid_link_key

    if not valid_link_key(key.source_id, key.hostname):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_link_key",
                "reason": "연결 키로 쓸 수 없는 source_id·hostname입니다.",
            },
        )


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _store(request: Request) -> Any:
    """연결 저장소(알람 라우트와 같은 앱 수명 인스턴스) — 없으면 503."""
    from src.api.routes.alarm import _host_link_store

    store = _host_link_store(request)
    if store is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "link_store_unavailable",
                "reason": "연결 저장소(Redis)를 쓸 수 없습니다.",
            },
        )
    return store


async def _audit(
    admin: dict[str, Any],
    action: str,
    key: HostLinkKey,
    outcome: str,
    rec: dict[str, Any] | None = None,
    **extra: Any,
) -> None:
    """승인·삭제 감사 1건 — 대상 연결의 db_id·서버명을 함께 남긴다(실패해도 응답을 막지 않는다)."""
    from src.security import audit_logger

    try:
        await audit_logger.log_investigation(
            entry_point=_ENTRY_POINT,
            outcome=outcome,
            user_id=admin.get("sub"),
            backend="noise_gate",
            targets=[{"hostname": key.hostname}],
            action=action,
            source_id=key.source_id,
            link_db_id=str((rec or {}).get("db_id") or getattr(key, "db_id", "") or ""),
            link_server_name=str(
                (rec or {}).get("server_name") or getattr(key, "server_name", "") or ""
            ),
            **extra,
        )
    except Exception:  # noqa: BLE001
        logger.warning("호스트 연결 관리 감사 기록 실패", exc_info=True)


@router.get(
    "/admin/host-links",
    summary="호스트 연결 목록(관리자)",
    description="확정·확정 대기·부정 연결과 이름 변환 패턴(적용 건수)을 반환합니다.",
    tags=["admin"],
)
async def list_host_links(
    request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{links[], patterns[{key, kind, from, to, label, count}]}`."""
    from noise_gate.domain.host_link import pattern_label

    _assert_admin(request, admin)
    store = _store(request)
    try:
        links = await store.list_links()
        patterns = await store.patterns()
    except _STORE_ERRORS:
        logger.warning("호스트 연결 목록 조회 실패", exc_info=True)
        raise _store_failed("조회") from None
    return {
        "links": links,
        "patterns": [
            {
                "key": k, "kind": k.split("|")[0], "from": k.split("|")[1:2][0] if "|" in k else "",
                "to": "|".join(k.split("|")[2:]), "label": pattern_label(k), "count": v,
            }
            for k, v in sorted(patterns.items(), key=lambda kv: (-kv[1], kv[0]))
        ],
    }


@router.post(
    "/admin/host-links/approve",
    summary="확정 대기 연결 승인(관리자)",
    description=(
        "확정 대기 연결을 확정(전역)으로 올립니다. 본문에 목록에서 본 `db_id`·`server_name`·`at`을 "
        "실어야 하며, 현재 레코드와 다르면 409 `link_changed`입니다. 감사 로그 대상입니다."
    ),
    tags=["admin"],
)
async def approve_host_link(
    body: HostLinkApproval, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{link}` — 없으면 404 · 본 것과 현재 레코드가 다르면 409 `link_changed`."""
    from noise_gate.infrastructure.host_link_store import HostLinkChangedError
    from src.security.audit_logger import (
        INVESTIGATION_DENIED,
        INVESTIGATION_FAILED,
        INVESTIGATION_OK,
    )

    _assert_admin(request, admin)
    _assert_key(body)
    try:
        rec = await _store(request).approve(
            body.source_id, body.hostname, by=str(admin.get("sub") or ""),
            expect={"db_id": body.db_id, "server_name": body.server_name, "at": body.at},
        )
    except HostLinkChangedError:
        await _audit(admin, "approve", body, INVESTIGATION_DENIED, degraded=[
            {"reason": "link_changed", "detail": "목록을 본 뒤 연결이 바뀜"}])
        raise HTTPException(
            status_code=409,
            detail={
                "code": "link_changed",
                "reason": "목록을 본 뒤 연결이 바뀌었습니다. 목록을 새로 고친 뒤 다시 승인하세요.",
            },
        ) from None
    except _STORE_ERRORS as exc:
        logger.warning("호스트 연결 승인 실패: %s", body.hostname, exc_info=True)
        await _audit(admin, "approve", body, INVESTIGATION_FAILED, degraded=[
            {"reason": "link_store_failed", "detail": type(exc).__name__}])
        raise _store_failed("저장") from None
    await _audit(admin, "approve", body, INVESTIGATION_OK if rec else INVESTIGATION_DENIED, rec)
    if rec is None:
        raise HTTPException(status_code=404, detail="연결을 찾지 못했습니다.")
    return {"link": rec}


@router.delete(
    "/admin/host-links",
    summary="호스트 연결 삭제(관리자)",
    description="연결 1건을 지웁니다(확정 연결이면 패턴 건수도 내립니다). 감사 로그 대상입니다.",
    tags=["admin"],
)
async def delete_host_link(
    body: HostLinkKey, request: Request, admin: dict[str, Any] = Depends(require_admin_user)
) -> dict[str, Any]:
    """`{deleted: 레코드}` — 없으면 404."""
    from src.security.audit_logger import (
        INVESTIGATION_DENIED,
        INVESTIGATION_FAILED,
        INVESTIGATION_OK,
    )

    _assert_admin(request, admin)
    _assert_key(body)
    store = _store(request)
    try:
        rec = await store.delete(body.source_id, body.hostname)
    except _STORE_ERRORS as exc:
        logger.warning("호스트 연결 삭제 실패: %s", body.hostname, exc_info=True)
        await _audit(admin, "delete", body, INVESTIGATION_FAILED, degraded=[
            {"reason": "link_store_failed", "detail": type(exc).__name__}])
        raise _store_failed("삭제") from None
    await _audit(admin, "delete", body, INVESTIGATION_OK if rec else INVESTIGATION_DENIED, rec)
    if rec is None:
        raise HTTPException(status_code=404, detail="연결을 찾지 못했습니다.")
    return {"deleted": rec}


__all__ = ["router"]
