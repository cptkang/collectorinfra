"""plans/163 X6/X6+ — LLM 클라이언트·LLMConfig의 접두 없는 셸 env 폴백 제거 회귀 테스트.

검증 대상:
- `FabriXAPIClient`·`LLMAPIClient`는 받은 인자만 쓴다 — 셸 env(`FABRIX_*`·`LLM_OLLAMA_BASE_URL`·
  `LLM_API_KEY`)를 줘도 인자가 쓰인다.
- 필수 인자가 비면 생성 시점에 `ValueError`이고, 메시지에 비어 있는 설정 키가 나온다
  (조용히 `localhost` 기본 엔드포인트로 가지 않는다 · CWE-1188).
- `LLMConfig`가 접두 없는 셸 env(`FABRIX_*`·`GOOGLE_API_KEY`·`LLM_API_KEY`)를 더 이상 읽지 않는다.
- `bind_tools` 복제본이 원본 값을 그대로 잇는다.
"""

from __future__ import annotations

import pytest

from src.clients.fabrix_client import FabriXAPIClient
from src.clients.ollama_client import LLMAPIClient
from src.config import LLMConfig

_SHELL_ENV = {
    "FABRIX_BASE_URL": "http://shell-fabrix:1/v1",
    "FABRIX_API_KEY": "shell-fabrix-key",
    "FABRIX_CLIENT_KEY": "shell-fabrix-client",
    "FABRIX_CHAT_MODEL": "shell-fabrix-model",
    "GOOGLE_API_KEY": "shell-google-key",
    "LLM_API_KEY": "shell-llm-key",
    "LLM_OLLAMA_BASE_URL": "http://shell-ollama:2",
}


@pytest.fixture
def shell_env(monkeypatch):
    for key, value in _SHELL_ENV.items():
        monkeypatch.setenv(key, value)


# --- FabriXAPIClient ---


def test_fabrix_empty_base_url_raises_with_key_name(shell_env):
    with pytest.raises(ValueError, match="LLM_FABRIX_BASE_URL"):
        FabriXAPIClient(base_url="", chat_model="m", api_key="k")


def test_fabrix_empty_chat_model_raises_with_key_name(shell_env):
    with pytest.raises(ValueError, match="LLM_FABRIX_CHAT_MODEL"):
        FabriXAPIClient(base_url="http://arg:1/v1", chat_model="", api_key="k")


def test_fabrix_omitted_base_url_raises():
    """인자를 생략해도 하드코딩 `localhost:8002`로 가지 않는다."""
    with pytest.raises(ValueError, match="LLM_FABRIX_BASE_URL"):
        FabriXAPIClient(chat_model="m")


def test_fabrix_uses_args_not_shell_env(shell_env):
    client = FabriXAPIClient(base_url="http://arg:1/v1", chat_model="arg-model", api_key="arg-key")
    assert client.base_url == "http://arg:1/v1"
    assert client.chat_model == "arg-model"
    assert client.api_key == "arg-key"


def test_fabrix_no_api_key_does_not_read_shell_env(shell_env):
    client = FabriXAPIClient(base_url="http://arg:1/v1", chat_model="m")
    assert client.api_key is None
    assert "Authorization" not in client._headers


def test_fabrix_bind_tools_keeps_values(shell_env):
    client = FabriXAPIClient(base_url="http://arg:1/v1", chat_model="m", api_key="k")
    bound = client.bind_tools([])
    assert (bound.base_url, bound.chat_model, bound.api_key) == ("http://arg:1/v1", "m", "k")


# --- LLMAPIClient (Ollama) ---


def test_ollama_args_win_over_shell_env(shell_env):
    client = LLMAPIClient(base_url="http://arg-ollama:3", chat_model="m")
    assert client.base_url == "http://arg-ollama:3"
    assert client.api_key is None  # 셸 LLM_API_KEY를 읽지 않는다
    assert "Authorization" not in client._headers


def test_ollama_api_key_arg_used(shell_env):
    client = LLMAPIClient(base_url="http://arg-ollama:3", chat_model="m", api_key="arg-key")
    assert client.api_key == "arg-key"
    assert client._headers["Authorization"] == "Bearer arg-key"


def test_ollama_empty_base_url_raises_with_key_name(shell_env):
    with pytest.raises(ValueError, match="LLM_OLLAMA_BASE_URL"):
        LLMAPIClient(base_url="", chat_model="m")


def test_ollama_empty_chat_model_raises_with_key_name():
    with pytest.raises(ValueError, match="LLM_MODEL"):
        LLMAPIClient(base_url="http://arg-ollama:3", chat_model="")


def test_ollama_bind_tools_keeps_values(shell_env):
    client = LLMAPIClient(base_url="http://arg-ollama:3", chat_model="m", api_key="k")
    bound = client.bind_tools([])
    assert (bound.base_url, bound.chat_model, bound.api_key) == ("http://arg-ollama:3", "m", "k")


# --- LLMConfig (X6+) ---


def test_llm_config_ignores_unprefixed_shell_env(shell_env, monkeypatch):
    for key in (
        "LLM_FABRIX_BASE_URL", "LLM_FABRIX_API_KEY", "LLM_FABRIX_CLIENT_KEY",
        "LLM_FABRIX_CHAT_MODEL", "LLM_GEMINI_API_KEY", "LLM_OLLAMA_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    cfg = LLMConfig(_env_file=None)
    assert cfg.fabrix_base_url == ""
    assert cfg.fabrix_api_key == ""
    assert cfg.fabrix_client_key == ""
    assert cfg.fabrix_chat_model == ""
    assert cfg.gemini_api_key == ""
    assert cfg.ollama_api_key == ""


def test_llm_config_reads_prefixed_shell_env(monkeypatch):
    """접두 키(`LLM_*`)는 pydantic-settings가 그대로 읽는다 — 정상 경로 유지."""
    monkeypatch.setenv("LLM_FABRIX_BASE_URL", "http://prefixed:1/v1")
    monkeypatch.setenv("LLM_OLLAMA_API_KEY", "prefixed-key")
    cfg = LLMConfig(_env_file=None)
    assert cfg.fabrix_base_url == "http://prefixed:1/v1"
    assert cfg.ollama_api_key == "prefixed-key"
