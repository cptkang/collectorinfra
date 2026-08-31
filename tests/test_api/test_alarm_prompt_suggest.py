"""Plan 86 T4 (D-192) — 알람 조회 질의 추천 라우트 계약.

이 엔드포인트는 **결정적 축 매핑이 비는 알람에서만** 프론트가 부른다. 고정할 계약:
①기본 off → 503 ②다른 존 → 403 ③LLM 실패·형식 불일치는 **200 + null**(카드가 깨지면 안 된다)
④대상 서버명이 빠진 산출은 채택하지 않는다.

실 LLM은 호출하지 않는다(D-127) — `create_llm`을 mock해 계약만 고정한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.dependencies import require_user
from src.api.routes import alarm as alarm_routes
from src.routing.zones import ZONE_BANKJON, ZONE_GONGJON

GONGJON_DB = "polestar_cm_gp"
BANKJON_DB = "polestar_b0"


def _make_config(*, enabled: bool = True, auth_enabled: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(enabled=auth_enabled),
        noise_gate=SimpleNamespace(alarm_prompt_llm_suggest_enabled=enabled),
    )


def _make_client(config, user=None) -> TestClient:  # noqa: ANN001
    app = FastAPI()
    app.include_router(alarm_routes.router, prefix="/api/v1")
    app.state.config = config
    app.dependency_overrides[require_user] = lambda: (user or {})
    return TestClient(app)


def _body(**over):
    body = {
        "target": "fgisidd0",
        "alarm_name": "Cloud PC 사양변경 승인",
        "resource_type": "management.MonitorGroup",
        "recommended_action": "변경 이력을 확인하십시오",
        "pattern_analysis": "이 시간대에 반복 발생",
        "db_id": GONGJON_DB,
    }
    body.update(over)
    return body


class _FakeLLM:
    """`ainvoke`가 고정 응답을 돌려주는 mock. 실 네트워크·과금 없음."""

    def __init__(self, content: str | Exception) -> None:
        self._content = content

    async def ainvoke(self, messages):  # noqa: ANN001
        if isinstance(self._content, Exception):
            raise self._content
        return SimpleNamespace(content=self._content)


@pytest.fixture
def patch_llm(monkeypatch):
    def _apply(content):
        monkeypatch.setattr("src.llm.create_llm", lambda cfg, **kw: _FakeLLM(content))
    return _apply


# ─── 게이트 ───


def test_disabled_by_default_returns_503() -> None:
    """기본 off — 켜지 않은 배포에서 과금 경로가 열려 있으면 안 된다."""
    client = _make_client(_make_config(enabled=False), user={"alarm_zones": [ZONE_GONGJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body())
    assert resp.status_code == 503
    assert "비활성" in resp.json()["detail"]


def test_real_config_default_is_off() -> None:
    """설정 기본값 자체가 False여야 한다(신규 플래그 기본 off = 현행 동작과 비트 동일)."""
    from src.config import NoiseGateConfig

    assert NoiseGateConfig().alarm_prompt_llm_suggest_enabled is False


def test_other_zone_is_forbidden(patch_llm) -> None:
    """쓰기 경로와 같은 존 RBAC — 다른 존 알람 내용을 LLM에 실어 보내지 못하게 한다."""
    patch_llm('{"label": "x", "text": "fgisidd0 조회"}')
    client = _make_client(_make_config(), user={"alarm_zones": [ZONE_BANKJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body(db_id=GONGJON_DB))
    assert resp.status_code == 403


def test_empty_target_is_rejected(patch_llm) -> None:
    """주어 없는 질의는 만들지 않는다(결정적 경로와 같은 규칙)."""
    patch_llm('{"label": "x", "text": "조회"}')
    client = _make_client(_make_config(), user={"alarm_zones": [ZONE_GONGJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body(target="   "))
    assert resp.status_code == 400


# ─── 정상 경로 ───


def test_suggestion_shape_matches_frontend_contract(patch_llm) -> None:
    """응답 형식은 프론트의 결정적 추천과 동일해야 한다(CAPABILITY-MAP-86)."""
    patch_llm(
        '{"label": "변경 이력", "text": "fgisidd0 서버의 최근 3개월 알람 발생 이력을 보여줘"}'
    )
    client = _make_client(_make_config(), user={"alarm_zones": [ZONE_GONGJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body())
    assert resp.status_code == 200
    sug = resp.json()["suggestion"]
    assert sug["label"] == "변경 이력"
    assert sug["text"] == "fgisidd0 서버의 최근 3개월 알람 발생 이력을 보여줘"
    assert sug["axis"] == "management.MonitorGroup"
    assert sug["source"] == "llm"   # 결정적 추천과 구별 가능해야 한다


def test_code_fenced_json_is_parsed(patch_llm) -> None:
    """LLM이 코드펜스를 붙여도 파싱된다(extract_json_from_response 경유)."""
    patch_llm('```json\n{"label": "이력", "text": "fgisidd0 서버의 알람 이력을 보여줘"}\n```')
    client = _make_client(_make_config(), user={"alarm_zones": [ZONE_GONGJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body())
    assert resp.json()["suggestion"]["text"] == "fgisidd0 서버의 알람 이력을 보여줘"


# ─── 실패는 graceful ───


@pytest.mark.parametrize(
    "content",
    [
        RuntimeError("LLM 타임아웃"),          # 호출 자체 실패
        "이건 JSON이 아닙니다",                  # 파싱 실패
        '{"label": "x"}',                      # text 누락
        '{"label": "x", "text": "   "}',       # 빈 질의
        '{"label": "x", "text": "다른서버 조회"}',  # ★ 대상 서버명 누락
    ],
)
def test_failures_return_null_not_5xx(patch_llm, content) -> None:
    """어떤 실패든 200 + null이다 — 추천이 없다고 알람 카드가 깨지면 안 된다."""
    patch_llm(content)
    client = _make_client(_make_config(), user={"alarm_zones": [ZONE_GONGJON]})
    resp = client.post("/api/v1/alarm/suggest-prompt", json=_body())
    assert resp.status_code == 200
    assert resp.json()["suggestion"] is None
