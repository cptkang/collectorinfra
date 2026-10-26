"""관리자 「조사 설정」 요약 API — 조사 모드·상한·조사 서비스 부하 (plans/194 W5 · C5 · D-347).

`GET /admin/investigation/summary` 하나만 둔다(읽기 전용). 관리자 의존성(`require_admin_user` —
운영자 토큰은 `type == "admin"` 서명 검증, 사용자 토큰은 DB role) 뒤에서 role 클레임을 한 번 더 명시
검증한다(`admin_incident_kb`와 같은 관례).

- 모드는 capabilities와 같은 식이다 — 자동 = 게이트 ∧ 트리거 · 수동 = 게이트 ∧ 수동 접수.
- 사용자별 상한은 본체 설정(진행 1 · 시간당 `investigation_additional_user_hourly_limit`)이고,
  조사 서비스 상한·부하는 sre_agent `sre_health`의 부하 게이지(앱 캐시 10초 ·
  `investigation_service_health`)에서 읽는다 — 못 읽으면 그 칸은 null이고 `service_error`에 사유.
- 설정을 바꾸는 경로는 없다(쓰기는 관리자 설정 편집기 · 조사 서비스 `.env`).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.dependencies import is_admin, require_admin_user
from src.api.investigation_service_health import NO_GAUGE_REASON, health_cache
from src.infrastructure.investigation_request_limit import USER_ACTIVE_LIMIT

router = APIRouter()

#: 조사 서비스 부하 칸(`load`) — 게이지 키 중 상한이 아닌 것.
_LOAD_KEYS: tuple[str, ...] = ("running", "queued", "abandoned_slots", "abandoned_threads")


def _assert_admin(request: Request, admin: dict[str, Any]) -> None:
    """role 클레임 명시 검증 — 개발 모드(인증 꺼짐)만 예외."""
    if request.app.state.config.auth.enabled and not is_admin(admin):
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@router.get(
    "/admin/investigation/summary",
    summary="조사 모드·상한·조사 서비스 부하 요약(관리자)",
    description=(
        "알람 조사 모드(자동·수동), 상한(조사 서비스 동시·대기열·전역 도구·시간당 · "
        "사용자별 진행·시간당), 조사 서비스 부하(진행·대기·정리 중)를 반환합니다.<br/>"
        "조사 서비스 값은 10초 캐시이며 못 읽으면 null과 `service_error` 사유를 싣습니다. "
        "읽기 전용입니다."
    ),
    tags=["admin"],
)
async def investigation_summary(
    request: Request,
    admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """`{mode, limits, load, checked_at, service_error}`."""
    _assert_admin(request, admin)
    ng = request.app.state.config.noise_gate
    gate_on = bool(getattr(ng, "enable_noise_gate", False))
    snap = await health_cache(request.app.state).snapshot()
    health = snap.health or {}
    gauge = all(_int_or_none(health.get(key)) is not None for key in ("running", "queued"))
    service = health if gauge else {}  # 게이지가 없으면(구버전) 조사 서비스 칸은 전부 null
    return {
        "mode": {
            "auto_enabled": gate_on and bool(getattr(ng, "investigation_trigger_enabled", False)),
            "manual_enabled": gate_on and bool(getattr(ng, "investigation_manual_enabled", True)),
        },
        "limits": {
            "max_concurrent": _int_or_none(service.get("max_concurrent")),
            "max_queued": _int_or_none(service.get("max_queued")),
            "per_user_active": USER_ACTIVE_LIMIT,
            "per_user_hourly": int(getattr(ng, "investigation_additional_user_hourly_limit", 3)),
            "tool_global_concurrency": _int_or_none(service.get("tool_global_concurrency")),
            "hourly_budget": _int_or_none(service.get("hourly_budget")),
        },
        "load": {key: _int_or_none(service.get(key)) or 0 for key in _LOAD_KEYS} if gauge else None,
        "checked_at": snap.checked_at,
        "service_error": snap.error or (None if gauge else NO_GAUGE_REASON),
    }


__all__ = ["router"]
