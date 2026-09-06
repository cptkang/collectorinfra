"""D-194: FabriX 하이퍼파라미터 프로파일(llmConfig) 단위 테스트.

설정(JSON 문자열) → create_llm(purpose) 프로파일 해석 → KBGenAIChat 페이로드
llmConfig 반영까지의 배선을 mock 없이 결정적으로 검증한다. 실제 FabriX 호출은 없다.

테스트 config는 검증 대상 필드를 전부 명시해 `.env` 누수를 차단한다(CLAUDE.md).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import LLMConfig
from src.llm import _resolve_fabrix_llm_profile, create_llm


def _make_config(
    *,
    client_key: str = "test-client-key",
    llm_config: str = "",
    answer_llm_config: str = "",
) -> SimpleNamespace:
    llm = LLMConfig(
        provider="fabrix",
        fabrix_base_url="http://fabrix.test/api",
        fabrix_api_key="test-api-key",
        fabrix_client_key=client_key,
        fabrix_chat_model="test-model",
        fabrix_llm_config=llm_config,
        fabrix_answer_llm_config=answer_llm_config,
    )
    return SimpleNamespace(llm=llm)


# ──────────────────────────────────────────────
# 1. LLMConfig 검증 — 오탈자는 기동 시점에 실패
# ──────────────────────────────────────────────


class TestLLMConfigValidation:
    def test_empty_profile_is_valid(self):
        cfg = LLMConfig(fabrix_llm_config="", fabrix_answer_llm_config="")
        assert cfg.fabrix_llm_config == ""

    def test_valid_json_object_accepted(self):
        cfg = LLMConfig(
            fabrix_llm_config='{"temperature": 0.0}',
            fabrix_answer_llm_config='{"temperature": 0.5, "top_k": 40, "top_p": 0.9}',
        )
        assert '"top_k": 40' in cfg.fabrix_answer_llm_config

    def test_invalid_json_rejected(self):
        with pytest.raises(ValidationError):
            LLMConfig(fabrix_llm_config="{temperature: 0.5}")

    def test_non_object_json_rejected(self):
        with pytest.raises(ValidationError):
            LLMConfig(fabrix_answer_llm_config="[0.5, 0.9]")


# ──────────────────────────────────────────────
# 2. purpose → 프로파일 해석
# ──────────────────────────────────────────────


class TestResolveProfile:
    def test_deterministic_uses_base_profile(self):
        config = _make_config(
            llm_config='{"temperature": 0.0}',
            answer_llm_config='{"temperature": 0.5}',
        )
        assert _resolve_fabrix_llm_profile(config, "deterministic") == {
            "temperature": 0.0
        }

    def test_answer_uses_answer_profile(self):
        config = _make_config(
            llm_config='{"temperature": 0.0}',
            answer_llm_config='{"temperature": 0.5, "top_k": 40}',
        )
        assert _resolve_fabrix_llm_profile(config, "answer") == {
            "temperature": 0.5,
            "top_k": 40,
        }

    def test_answer_falls_back_to_base_when_empty(self):
        config = _make_config(llm_config='{"temperature": 0.1}', answer_llm_config="")
        assert _resolve_fabrix_llm_profile(config, "answer") == {"temperature": 0.1}

    def test_both_empty_returns_empty(self):
        config = _make_config()
        assert _resolve_fabrix_llm_profile(config, "answer") == {}
        assert _resolve_fabrix_llm_profile(config, "deterministic") == {}


# ──────────────────────────────────────────────
# 3. create_llm 배선 — KBGenAI / OpenAI 호환 양쪽
# ──────────────────────────────────────────────


class TestCreateLLMWiring:
    def test_kbgenai_gets_answer_profile(self):
        config = _make_config(
            llm_config='{"temperature": 0.0}',
            answer_llm_config='{"temperature": 0.5, "top_k": 40, "top_p": 0.9}',
        )
        llm = create_llm(config, purpose="answer")
        assert isinstance(llm, KBGenAIChat)
        assert llm.llm_config == {"temperature": 0.5, "top_k": 40, "top_p": 0.9}

    def test_kbgenai_default_purpose_is_deterministic(self):
        config = _make_config(
            llm_config='{"temperature": 0.0}',
            answer_llm_config='{"temperature": 0.5}',
        )
        llm = create_llm(config)
        assert isinstance(llm, KBGenAIChat)
        assert llm.llm_config == {"temperature": 0.0}

    def test_kbgenai_unset_profile_is_none(self):
        """프로파일 미설정이면 llm_config=None — D-194 이전 페이로드와 동일해야 한다."""
        llm = create_llm(_make_config())
        assert isinstance(llm, KBGenAIChat)
        assert llm.llm_config is None

    def test_openai_compat_maps_temperature_only(self):
        from src.clients.fabrix_client import FabriXAPIClient

        config = _make_config(
            client_key="",
            answer_llm_config='{"temperature": 0.7, "top_k": 40}',
        )
        llm = create_llm(config, purpose="answer")
        assert isinstance(llm, FabriXAPIClient)
        assert llm.temperature == 0.7


# ──────────────────────────────────────────────
# 4. KBGenAIChat 페이로드 — llmConfig 필드 반영
# ──────────────────────────────────────────────


def _make_chat(llm_config: dict | None) -> KBGenAIChat:
    return KBGenAIChat(
        endpoint_url="http://fabrix.test/api",
        x_openapi_token="tok",
        x_generative_ai_client="cli",
        asset_id="asset",
        llm_config=llm_config,
    )


class TestPayload:
    MESSAGES = [SystemMessage(content="sys"), HumanMessage(content="hi")]

    def test_payload_includes_llm_config(self):
        chat = _make_chat({"temperature": 0.5, "top_k": 40})
        payload = chat._get_payload(self.MESSAGES)
        assert payload["llmConfig"] == {"temperature": 0.5, "top_k": 40}

    def test_stream_payload_includes_llm_config(self):
        chat = _make_chat({"temperature": 0.5})
        payload = chat._get_payload(self.MESSAGES, is_stream=True)
        assert payload["isStream"] is True
        assert payload["llmConfig"] == {"temperature": 0.5}

    def test_payload_omits_llm_config_when_unset(self):
        """미설정이면 llmConfig 키 자체가 없어야 한다(서버 기본 동작 보존)."""
        assert "llmConfig" not in _make_chat(None)._get_payload(self.MESSAGES)
        assert "llmConfig" not in _make_chat({})._get_payload(self.MESSAGES)

    def test_bind_tools_preserves_llm_config(self):
        chat = _make_chat({"temperature": 0.5})

        class _Tool:
            name = "t"

        bound = chat.bind_tools([_Tool()])
        assert bound.llm_config == {"temperature": 0.5}
