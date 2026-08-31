"""웹 UI 테마 설정 — 공개 엔드포인트 · 카탈로그 등재 · 도움말 정제 (D-180).

정제(`sanitize_description`)는 정규식이라 회귀에 취약하다. 개별 패턴 단위 검증과
카탈로그 전수 검증을 함께 두어, 새 설정이 개발 참조를 달고 들어와도 화면에는
새어 나가지 않도록 고정한다.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.api.server import create_app
from src.api.settings_catalog import (
    _DEV_REF,
    GROUP_TITLES,
    SECTION_BY_KEY,
    field_index,
    sanitize_description,
)
from src.config import AppConfig


@pytest.fixture(scope="module")
def client() -> TestClient:
    # lifespan을 띄우지 않는다 — /ui/theme은 app.state가 아니라 load_config()만 쓴다.
    return TestClient(create_app())


# ─── 공개 엔드포인트 ───


def test_theme_endpoint_is_public_and_returns_valid_theme(client: TestClient) -> None:
    """로그인 화면도 같은 테마로 그려야 하므로 무인증으로 열려 있어야 한다."""
    response = client.get("/api/v1/ui/theme")
    assert response.status_code == 200
    assert response.json()["default_theme"] in {"light", "dark"}


def test_default_theme_is_light() -> None:
    """설정하지 않으면 밝은 테마가 기본이다."""
    assert AppConfig.model_fields["ui_default_theme"].default == "light"


# ─── 카탈로그 등재 ───


def test_theme_key_is_editable_enum_applied_immediately() -> None:
    """운영자 화면에서 고를 수 있어야 하고, 저장 즉시 반영되어야 한다."""
    spec = field_index()["UI_DEFAULT_THEME"]
    assert spec.type == "enum"
    assert spec.enum_choices == ["light", "dark"]
    assert spec.apply_mode == "immediate"   # 재시작·리로드 없이 반영
    assert not spec.is_secret
    assert spec.description


# ─── 도움말 정제 ───


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("E5-3 유사어 보완 테이블 상한(D-051, 기본 15)", "유사어 보완 테이블 상한(기본 15)"),
        ("(E3 후속) SSE 브리지 on/off", "SSE 브리지 on/off"),
        ("(P60 E3 2차·D-113) STL 분해 이상탐지", "STL 분해 이상탐지"),
        ("(P67 R3-v) 캐시 항목 상한(0=비활성)", "캐시 항목 상한(0=비활성)"),
        ("E2/E4 다중 후보 경로 스위치", "다중 후보 경로 스위치"),
        ("커버리지 밖 라우팅. 트랙 A 착수로 기본값 전환", "커버리지 밖 라우팅. 착수로 기본값 전환"),
    ],
)
def test_sanitize_strips_dev_references(raw: str, expected: str) -> None:
    assert sanitize_description(raw) == expected


@pytest.mark.parametrize(
    "text",
    [
        "3차 시도까지 재시도한다",              # 참조에 붙지 않은 '차'는 정상 문구
        "쿼리 타임아웃(초). 0이면 무제한",
        "활성 DB 식별자 목록",
    ],
)
def test_sanitize_preserves_ordinary_text(text: str) -> None:
    assert sanitize_description(text) == text


def test_sanitize_handles_empty() -> None:
    assert sanitize_description("") == ""
    assert sanitize_description(None) is None


# ─── 전수 회귀: 화면에 개발 참조가 남지 않는다 ───


def test_no_dev_reference_reaches_the_screen() -> None:
    """설정 도움말·구획명·그룹명 어디에도 계획/결정 번호가 남지 않아야 한다."""
    pattern = re.compile(_DEV_REF)

    leaked_descriptions = {
        key: spec.description
        for key, spec in field_index().items()
        if spec.description and pattern.search(spec.description)
    }
    assert not leaked_descriptions, f"도움말에 개발 참조 잔존: {leaked_descriptions}"

    leaked_sections = [s for s in set(SECTION_BY_KEY.values()) if pattern.search(s)]
    assert not leaked_sections, f"구획명에 개발 참조 잔존: {leaked_sections}"

    leaked_groups = [t for t in GROUP_TITLES.values() if pattern.search(t)]
    assert not leaked_groups, f"그룹명에 개발 참조 잔존: {leaked_groups}"


def test_no_section_title_bleeds_into_field_help() -> None:
    """`── 구획 소제목 ──` 라인이 개별 설정 도움말로 흘러들지 않는다."""
    bled = {
        key: spec.description
        for key, spec in field_index().items()
        if spec.description and ("──" in spec.description or "═══" in spec.description)
    }
    assert not bled, f"구획 소제목이 도움말에 혼입: {bled}"
