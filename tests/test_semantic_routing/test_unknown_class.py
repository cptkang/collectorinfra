"""`unknown` 클래스 도입 + 역질문 연결 (Plan 79 A-6 / Plan 80 WU-21).

**`general_inference`와 의미가 다르다**: general_inference는 *DB에 접근하지 않는 일반 응답*
(개념 설명·인사)이고 `unknown`은 **분류 불가**다. 후자는 답을 지어내지 않고 사용자에게 되묻는다.

여기서 고정하는 것은 **구조**다 — 플래그가 꺼져 있으면 프롬프트가 바이트 동일하고, 켜져 있으면
정의 줄과 예시가 **둘 다** 들어가며(계약 C-A), 산출 시 되묻기로 연결된다.
분류 *정확도*(모호한 질의를 실제로 unknown으로 잡는가)는 실 LLM 평가 소관이다.
"""

from __future__ import annotations

import importlib

import pytest

from src.config import load_config

# 패키지 `__init__`이 동명 함수를 리바인딩하므로 `import ... as sr`는 **모듈이 아니라 함수**를
# 준다(기존 테스트도 같은 이유로 importlib을 쓴다 — test_two_stage.py:37).
sr = importlib.import_module("src.routing.semantic_router")
from src.prompts.semantic_router import (
    SEMANTIC_ROUTER_UNKNOWN_CLASS_LINE,
    SEMANTIC_ROUTER_UNKNOWN_EXAMPLE,
)
from src.routing.domain_config import DB_DOMAINS


def _domains():
    doms = DB_DOMAINS if isinstance(DB_DOMAINS, list) else list(DB_DOMAINS.values())
    return doms[:2]


@pytest.fixture
def unknown_off(monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg.router, "unknown_enabled", False, raising=False)
    monkeypatch.setattr(sr, "load_config", lambda: cfg)
    return cfg


@pytest.fixture
def unknown_on(monkeypatch):
    cfg = load_config()
    monkeypatch.setattr(cfg.router, "unknown_enabled", True, raising=False)
    monkeypatch.setattr(sr, "load_config", lambda: cfg)
    return cfg


# ──────────────────────────────────────────────
# U1·U2 — 프롬프트 주입 (계약 C-A: 두 자리 모두 조건부)
# ──────────────────────────────────────────────

def test_u1_prompt_is_byte_identical_when_off(unknown_off):
    """플래그 off면 `unknown`이 프롬프트에 **한 글자도** 없다.

    라우터 프롬프트는 **모든 질의**가 통과하는 단일 지점이라, 여기서 새면 전 경로가 영향을 받는다.
    """
    prompt = sr._build_router_prompt(_domains())
    assert "unknown" not in prompt


def test_u2_both_slots_appear_when_on(unknown_on):
    """★ 계약 C-A — 켜면 **정의 줄과 예시가 둘 다** 들어간다.

    정의만 넣으면 모델이 클래스를 알면서 형식은 모르고, 예시만 넣으면 그 반대다.
    논문이 unknown recall 개선의 직접 원인으로 지목한 것이 **예시**다.
    """
    prompt = sr._build_router_prompt(_domains())
    assert SEMANTIC_ROUTER_UNKNOWN_CLASS_LINE.strip() in prompt
    assert SEMANTIC_ROUTER_UNKNOWN_EXAMPLE.strip() in prompt


def test_u2_definition_separates_unknown_from_general_inference(unknown_on):
    """정의가 `general_inference`와의 차이를 명시한다 — 둘이 섞이면 라벨만 늘고 처리가 없다."""
    assert "분류 불가" in SEMANTIC_ROUTER_UNKNOWN_CLASS_LINE
    prompt = sr._build_router_prompt(_domains())
    # 두 클래스가 **함께** 정의돼 있어야 모델이 구분할 수 있다.
    assert "general_inference" in prompt and "unknown" in prompt


# ──────────────────────────────────────────────
# U3·U4 — 역질문 연결 · fail-closed 강등
# ──────────────────────────────────────────────

def _state(query: str = "그거 좀 확인해줘") -> dict:
    return {"user_query": query, "messages": []}


async def _route(monkeypatch, *, intent: str):
    """`_llm_classify`가 주어진 intent를 낸 상황을 만든다."""
    async def _fake(*a, **k):
        return {"intent": intent, "databases": []}

    monkeypatch.setattr(sr, "_llm_classify", _fake)
    return await sr.semantic_router(_state(), llm=object())


@pytest.mark.asyncio
async def test_u3_unknown_returns_clarification(monkeypatch, unknown_on):
    """★ A-6 ③ — `unknown`이면 **되묻는다**. 답을 지어내지 않는다."""
    result = await _route(monkeypatch, intent="unknown")

    assert result["routing_intent"] == "unknown"
    assert result["final_response"], "되묻기 질문이 비어 있다"
    # 조회로 진행하지 않는다 — 대상 DB가 없어야 한다.
    assert result["target_databases"] == []
    assert result["active_db_id"] is None


@pytest.mark.asyncio
async def test_u3_clarification_reuses_existing_contract(monkeypatch, unknown_on):
    """신규 UI를 만들지 않는다 — 존 역질문과 **같은 키**로 응답한다(79 §3.3)."""
    result = await _route(monkeypatch, intent="unknown")
    for key in ("target_databases", "is_multi_db", "active_db_id",
                "user_specified_db", "routing_intent", "final_response", "current_node"):
        assert key in result, f"{key} 누락 — 기존 응답 규약과 어긋난다"


@pytest.mark.asyncio
async def test_u4_unknown_when_off_degrades_not_clarifies(monkeypatch, unknown_off):
    """★ fail-closed — off인데 모델이 `unknown`을 내면 **강등**한다(되묻지 않는다).

    off면 프롬프트에 정의가 없으므로 정상적으로는 나올 수 없다. 그래도 규정 밖 라벨을
    내는 것은 관측된 실패 유형이라(트랙 E-1), 처리 분기 없이 통과시키지 않는다.
    """
    result = await _route(monkeypatch, intent="unknown")
    assert result["routing_intent"] != "unknown"
    assert result["routing_intent"] == "general_inference"
    assert "final_response" not in result


@pytest.mark.asyncio
async def test_u4_normal_intent_untouched(monkeypatch, unknown_on):
    """`unknown`이 아닌 의도는 이 분기를 지나가기만 한다(회귀 0)."""
    result = await _route(monkeypatch, intent="general_inference")
    assert result["routing_intent"] == "general_inference"
