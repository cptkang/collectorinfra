"""plans/119 · D-268 부기: OpenAI 호환 클라이언트(vLLM·MLX)의 SDK 자동 재시도를 끈다.

langchain-openai `ChatOpenAI.max_retries` 미지정(None)이면 openai SDK 기본 2회가 적용돼
호출 상한이 재시도마다 새로 걸린다(벽시계 최대 3배 — 2026-09-28 openai 2.26.0 실측).
단언은 langchain 필드와 실제 SDK 클라이언트 두 곳 모두에서 한다(네트워크 0).
"""

from __future__ import annotations

import pytest

from src.config import LLMConfig, OrchestratorConfig
from src.llm import create_llm, create_orchestrator_llm

pytest.importorskip("langchain_openai")


def _assert_no_sdk_retry(llm) -> None:
    assert llm.max_retries == 0
    assert llm.root_client.max_retries == 0
    assert llm.root_async_client.max_retries == 0


def test_orchestrator_vllm_disables_sdk_retry(mock_config):
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="vllm", base_url="http://vllm:8000/v1", model="gpt-oss-20b"
    )
    _assert_no_sdk_retry(create_orchestrator_llm(mock_config))


def test_orchestrator_mlx_disables_sdk_retry(mock_config):
    mock_config.llm = LLMConfig(_env_file=None, provider="mlx")
    mock_config.orchestrator = OrchestratorConfig(
        _env_file=None, provider="mlx", base_url="http://127.0.0.1:8080/v1", model="default_model"
    )
    _assert_no_sdk_retry(create_orchestrator_llm(mock_config))


def test_worker_mlx_disables_sdk_retry(mock_config):
    mock_config.llm = LLMConfig(_env_file=None, provider="mlx")
    _assert_no_sdk_retry(create_llm(mock_config))


def test_sdk_default_is_two_retries():
    """전제 실측 — 미지정이면 SDK가 2회 재시도한다(이 값이 바뀌면 부기 근거를 다시 본다)."""
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(base_url="http://127.0.0.1:9/v1", api_key="x", model="m")
    assert llm.max_retries is None
    assert llm.root_client.max_retries == 2
