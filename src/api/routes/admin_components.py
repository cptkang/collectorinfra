"""관리자 「분리 컴포넌트 설정」 프록시 라우트 (plans/191 W2 · D-345).

계약: `spec/SPEC-component-config-admin.md` §5.

설정 탭에서 sre_agent·apm_gateway 등 별도 프로세스의 설정을 조회·저장하도록 각 컴포넌트의
`/admin/config/*`를 그대로 중계한다. 본체는 컴포넌트 패키지를 import하지 않는다(D-139).

- 전부 `require_admin_user`(role·`type` 클레임 명시 검증).
- `{name}`은 허용 상수 ∩ `COMPONENT_ADMIN_ENDPOINTS` 키만 받는다(그 밖은 404 — 임의 URL 금지).
- 응답의 시크릿 항목 값(`file_value`·`effective_value`)은 한 번 더 지운다(방어 심화).
- 저장은 본체 감사 `SETTINGS_UPDATE`에 컴포넌트 이름·키 이름만 남긴다(값은 컴포넌트 감사가 보관).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.admin_audit import log_admin_event
from src.api.dependencies import require_admin_user
from src.infrastructure.component_admin_client import ComponentAdminClient, ComponentResult

logger = logging.getLogger(__name__)
router = APIRouter()


def _client(request: Request) -> ComponentAdminClient:
    """실행 중 설정으로 클라이언트를 만든다(`component_admin_transport`는 테스트 주입 지점)."""
    config = request.app.state.config
    transport = getattr(request.app.state, "component_admin_transport", None)
    return ComponentAdminClient(config.component_admin, transport=transport)


def _require_component(client: ComponentAdminClient, name: str) -> None:
    """허용 상수 ∩ 설정 키가 아니면 404."""
    if not client.has(name):
        raise HTTPException(status_code=404, detail=f"설정되지 않은 컴포넌트입니다: {name}")


def _failure_response(result: ComponentResult) -> JSONResponse:
    """컴포넌트 호출 실패를 502로 돌려준다(사유 어휘 + 문구)."""
    return JSONResponse(
        status_code=502, content={"reason": result.reason, "message": result.message},
    )


def _strip_secret_values(schema: dict[str, Any]) -> dict[str, Any]:
    """`is_secret` 항목의 값을 지운다 — 컴포넌트가 실수로 실어도 본체 밖으로 나가지 않는다."""
    groups = schema.get("groups")
    if not isinstance(groups, list):
        return schema
    for group in groups:
        settings = group.get("settings") if isinstance(group, dict) else None
        if not isinstance(settings, list):
            continue
        for item in settings:
            if isinstance(item, dict) and item.get("is_secret"):
                item["file_value"] = None
                item["effective_value"] = None
    return schema


def _key_names(values: Any) -> list[str]:
    """`saved_keys`(문자열 목록)·`ignored_keys`(`{key, reason}` 목록)에서 키 이름만 뽑는다."""
    if not isinstance(values, list):
        return []
    names: list[str] = []
    for value in values:
        if isinstance(value, str):
            names.append(value)
        elif isinstance(value, dict) and isinstance(value.get("key"), str):
            names.append(value["key"])
    return names


@router.get("/admin/components")
async def list_components(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """설정된 컴포넌트의 연결 상태를 병렬로 모은다(개별 실패 격리).

    `COMPONENT_ADMIN_ENDPOINTS`가 비면 빈 목록 — 화면은 대상 선택을 그리지 않는다.
    """
    client = _client(request)
    for warning in client.warnings:
        logger.warning("컴포넌트 설정 관리: %s", warning)
    names = client.names()
    results = await asyncio.gather(*(client.get_status(name) for name in names))
    components = [
        {
            "name": name,
            "endpoint_configured": True,
            "reachable": result.ok,
            "reason": result.reason,
            "message": result.message,
            "status": result.body if result.ok else None,
        }
        for name, result in zip(names, results)
    ]
    return {"components": components, "warnings": client.warnings}


@router.get("/admin/components/{name}/config")
async def get_component_config(
    name: str,
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> Any:
    """컴포넌트 설정 스키마(§2.1)를 중계한다. 실패는 502 `{reason, message}`."""
    client = _client(request)
    _require_component(client, name)
    result = await client.get_schema(name)
    if not result.ok or result.body is None:
        return _failure_response(result)
    schema = _strip_secret_values(result.body)
    schema["reachable"] = True
    return schema


@router.put("/admin/components/{name}/config")
async def update_component_config(
    name: str,
    request: Request,
    body: dict[str, Any] = Body(...),
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> Any:
    """컴포넌트에 저장 요청(§2.2)을 그대로 전달하고 결과 상태·본문을 돌려준다.

    `X-Request-ID`는 감사 미들웨어의 요청 번호를 쓰고(없으면 생성) 컴포넌트로 전파해
    본체·컴포넌트 감사를 대조할 수 있게 한다.
    """
    client = _client(request)
    _require_component(client, name)
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    result = await client.put_config(name, body, request_id)
    if not result.ok or result.body is None:
        return _failure_response(result)

    payload = result.body
    saved_keys = _key_names(payload.get("saved_keys"))
    ignored_keys = _key_names(payload.get("ignored_keys"))
    if result.status_code == 200 and not payload.get("dry_run"):
        from src.domain.audit import AuditEvent

        # 컴포넌트 자기 감사 결과(`audit_logged`)는 그대로 두고 본체 감사 결과를 따로 싣는다.
        payload["proxy_audit_logged"] = await log_admin_event(
            request,
            _admin.get("sub"),
            AuditEvent.SETTINGS_UPDATE.value,
            {
                "component": name,
                "component_request_id": request_id,
                "saved_keys": saved_keys,
                "ignored_keys": ignored_keys,
            },
        )
    logger.info(
        "컴포넌트 설정 저장 요청: %s status=%s saved=%s (by %s)",
        name, result.status_code, saved_keys, _admin.get("sub"),
    )
    return JSONResponse(status_code=result.status_code or 200, content=payload)
