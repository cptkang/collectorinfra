"""LLM 인스턴스 생성 모듈.

설정에 따라 적절한 LLM 백엔드를 생성하는 팩토리 함수를 제공한다.
지원 프로바이더: ollama, fabrix
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig

logger = logging.getLogger(__name__)


def create_llm(config: AppConfig) -> BaseChatModel:
    """설정에 따라 LLM 인스턴스를 생성한다.

    Args:
        config: 애플리케이션 설정

    Returns:
        LLM 인스턴스

    Raises:
        ValueError: 필수 설정이 누락된 경우
    """
    provider = config.llm.provider

    if provider == "ollama":
        return _create_ollama(config)
    elif provider == "fabrix":
        return _create_fabrix(config)
    else:
        raise ValueError(f"지원하지 않는 LLM 프로바이더: {provider}")


def _create_ollama(config: AppConfig) -> BaseChatModel:
    """Ollama LLM 클라이언트를 생성한다."""
    from src.clients.ollama_client import LLMAPIClient

    logger.info(
        "Ollama LLM 초기화: model=%s, base_url=%s",
        config.llm.model,
        config.llm.ollama_base_url,
    )
    return LLMAPIClient(
        base_url=config.llm.ollama_base_url,
        chat_model=config.llm.model,
        api_key=config.llm.ollama_api_key or None,
        timeout=config.llm.ollama_timeout,
        temperature=0.0,
    )


def _create_fabrix(config: AppConfig) -> BaseChatModel:
    """FabriX LLM 클라이언트를 생성한다.

    fabrix_client_key가 설정된 경우 KBGenAIChat (SDS 전용 API),
    그렇지 않으면 FabriXAPIClient (OpenAI 호환 API)를 사용한다.
    """
    if not config.llm.fabrix_base_url:
        raise ValueError(
            "FABRIX_BASE_URL이 설정되지 않았습니다. "
            ".env 파일에 FABRIX_BASE_URL을 추가하세요."
        )
    if not config.llm.fabrix_api_key:
        raise ValueError(
            "FABRIX_API_KEY가 설정되지 않았습니다. "
            ".env 파일에 FABRIX_API_KEY를 추가하세요."
        )

    model = config.llm.fabrix_chat_model or config.llm.model

    # KBGenAI 모드 (client_key가 있는 경우)
    if config.llm.fabrix_client_key:
        from src.clients.fabrix_kbgenai import KBGenAIChat

        logger.info("FabriX KBGenAI 초기화: endpoint=%s", config.llm.fabrix_base_url)
        return KBGenAIChat(
            endpoint_url=config.llm.fabrix_base_url,
            x_openapi_token=config.llm.fabrix_api_key,
            x_generative_ai_client=config.llm.fabrix_client_key,
            asset_id=model,
            kb_id="User",
            system_prompt="",
        )

    # OpenAI 호환 모드
    from src.clients.fabrix_client import FabriXAPIClient

    logger.info("FabriX API 초기화: base_url=%s, model=%s", config.llm.fabrix_base_url, model)
    return FabriXAPIClient(
        base_url=config.llm.fabrix_base_url,
        chat_model=model,
        api_key=config.llm.fabrix_api_key,
        temperature=0.0,
    )
