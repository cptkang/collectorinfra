"""스레드 DB 스코프 옵션 라우트 (plans/90 · D-205).

`GET /scope/options` — 스코프 칩 팝오버가 그릴 **축 배열**을 돌려준다. 첫 축은 늘
`zone_group`이고, 제니퍼 소스 축 `apm_source`(plans/147 · D-322 ⑤)는 APM 활성 ∧ 사용 가능 소스
≥2 ∧ 사용자 `apm` 권한일 때만 뒤에 붙는다(불성립이면 축 자체가 없다 — 응답 불변). 솔루션 축(DPM
등)도 같은 방식으로 `axes`에 항목을 추가한다(엔드포인트·프론트 분기 불변).
옵션 어휘(`axis`·`key`·`label`·`db_ids`)는 `src/domain/scope_select.py` 페이로드와 같다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from src.api.dependencies import require_user
from src.routing.db_scope import apm_source_axis, scope_axes_options

router = APIRouter()


@router.get("/scope/options")
async def get_scope_options(
    request: Request,
    current_user: dict = Depends(require_user),
) -> dict:
    """스코프 칩 선택지 — 활성 DB ∩ 사용자 허용 DB(존 RBAC)만 노출한다."""
    config = request.app.state.config
    allowed = (current_user or {}).get("allowed_db_ids")
    payload = scope_axes_options(
        config.multi_db.get_active_db_ids(),
        allowed_db_ids=allowed,
        group_exclusive=getattr(config.multi_db, "zone_group_exclusive", True),
    )
    apm_axis = _apm_source_axis(config, current_user or {})
    if apm_axis is not None:
        payload["axes"].append(apm_axis)
    return payload


def _apm_source_axis(config: Any, current_user: dict[str, Any]) -> dict[str, Any] | None:
    """제니퍼 소스 축 — APM 활성 ∧ 사용자 `apm` 권한 ∧ 사용 가능 소스 ≥2일 때만(아니면 None).

    소스 단위 권한은 없다(D-287 ⑤ — `apm` 권한이면 전 소스). 사용 가능 소스는 기동 정합 점검 결과
    (`get_available_apm_sources` · None = 레지스트리 전부)라 미연결 소스는 옵션에 뜨지 않는다.
    """
    from src.orchestration.apm_query import APM_SYSTEM, apm_active
    from src.routing.apm_source_select import get_available_apm_sources
    from src.routing.db_authz import is_source_allowed
    from src.routing.registry import get_registry

    if not apm_active(config):
        return None
    if not is_source_allowed(APM_SYSTEM, current_user.get("allowed_sources"),
                             current_user.get("role")):
        return None
    return apm_source_axis(get_registry().sources_of(APM_SYSTEM),
                           available=get_available_apm_sources())
