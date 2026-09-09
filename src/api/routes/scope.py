"""스레드 DB 스코프 옵션 라우트 (plans/90 · D-205).

`GET /scope/options` — 스코프 칩 팝오버가 그릴 **축 배열**을 돌려준다. 오늘은 `zone_group` 축 하나이고,
솔루션 축(APM·DPM)은 plans/82 Wave 7이 `axes`에 항목을 추가한다(엔드포인트·프론트 분기 불변).
옵션 어휘(`axis`·`key`·`label`·`db_ids`)는 `src/domain/scope_select.py` 페이로드와 같다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from src.api.dependencies import require_user
from src.routing.db_scope import scope_axes_options

router = APIRouter()


@router.get("/scope/options")
async def get_scope_options(
    request: Request,
    current_user: dict = Depends(require_user),
) -> dict:
    """스코프 칩 선택지 — 활성 DB ∩ 사용자 허용 DB(존 RBAC)만 노출한다."""
    config = request.app.state.config
    allowed = (current_user or {}).get("allowed_db_ids")
    return scope_axes_options(
        config.multi_db.get_active_db_ids(),
        allowed_db_ids=allowed,
        group_exclusive=getattr(config.multi_db, "zone_group_exclusive", True),
    )
