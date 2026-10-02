"""웹 UI 표시 설정 라우트.

화면 렌더 전에 필요한 값(전역 기본 테마)을 무인증으로 제공한다. 로그인 화면도
같은 테마로 그려져야 하므로 인증 의존성을 두지 않으며, 민감정보를 담지 않는다.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from src.config import load_config

router = APIRouter()


class UiThemeResponse(BaseModel):
    """전역 기본 테마 응답."""

    default_theme: str


@router.get("/ui/theme", response_model=UiThemeResponse)
async def get_ui_theme() -> UiThemeResponse:
    """전역 기본 테마를 반환한다.

    운영자가 `UI_DEFAULT_THEME`을 저장하면 `update_settings`가 `load_config` 캐시를
    비우므로(즉시 반영 키), 다음 호출부터 새 값이 나온다. 재시작·리로드는 필요 없다.

    Returns:
        기본 테마("light" | "dark")
    """
    return UiThemeResponse(default_theme=load_config().ui_default_theme)
