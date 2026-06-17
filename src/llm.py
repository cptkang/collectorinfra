"""LLM 인스턴스 생성 모듈.

설정에 따라 적절한 LLM 백엔드를 생성하는 팩토리 함수를 제공한다.
지원 프로바이더: ollama, fabrix, gemini
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
    elif provider == "gemini":
        return _create_gemini(config)
    else:
        raise ValueError(f"지원하지 않는 LLM 프로바이더: {provider}")


def create_orchestrator_llm(config: AppConfig) -> BaseChatModel:
    """deepagents 구동용 tool-calling LLM을 생성한다 (Plan 49 / D-037, 트랙 B).

    provider로 오케스트레이터를 선택한다:
    - "vllm"(기본·운영): vLLM의 OpenAI 호환 `/v1`에 `ChatOpenAI`로 연결, 네이티브 bind_tools 사용.
    - "gemini"(테스트/PoC 전용 — §4.7): `ChatGoogleGenerativeAI`. 외부 egress 필요, 폐쇄망 운영 부적합.

    (FabriX 워커는 create_llm으로 별도 생성 — 실질 응답처리 담당. provider와 무관하게 동일.)

    Args:
        config: 애플리케이션 설정

    Returns:
        오케스트레이터 LLM 인스턴스

    Raises:
        ValueError: vLLM base_url 또는 Gemini api_key 미설정
    """
    if config.orchestrator.provider == "gemini":
        return _create_orchestrator_gemini(config)
    return _create_orchestrator_vllm(config)


def _create_orchestrator_vllm(config: AppConfig) -> BaseChatModel:
    """vLLM(OpenAI 호환) 오케스트레이터 LLM을 생성한다."""
    if not config.orchestrator.base_url:
        raise ValueError(
            "ORCHESTRATOR_BASE_URL이 설정되지 않았습니다. "
            ".env에 ORCHESTRATOR_BASE_URL(vLLM /v1 엔드포인트)을 추가하세요."
        )

    from langchain_openai import ChatOpenAI

    logger.info(
        "오케스트레이터 LLM(vLLM) 초기화: base_url=%s, model=%s",
        config.orchestrator.base_url,
        config.orchestrator.model,
    )
    return ChatOpenAI(
        base_url=config.orchestrator.base_url,
        api_key=config.orchestrator.api_key or "EMPTY",
        model=config.orchestrator.model,
        temperature=0.0,
        timeout=config.orchestrator.timeout,
    )


def _create_orchestrator_gemini(config: AppConfig) -> BaseChatModel:
    """Gemini 오케스트레이터 LLM을 생성한다 (테스트/PoC 전용 — Plan 49 §4.7).

    api_key 검증을 패키지 import보다 먼저 수행한다(미설치 환경에서도 명확한 오류).
    오케스트레이터 모델이 미설정/비-gemini면 LLM_GEMINI_MODEL 또는 gemini-2.5-pro로 폴백한다.
    """
    api_key = config.orchestrator.api_key or config.llm.gemini_api_key
    if not api_key:
        raise ValueError(
            "Gemini 오케스트레이터(테스트 모드) API 키가 없습니다. "
            ".env(.encenv)에 ORCHESTRATOR_API_KEY 또는 LLM_GEMINI_API_KEY(GOOGLE_API_KEY)를 추가하세요."
        )

    model = config.orchestrator.model
    if not model or not model.startswith("gemini"):
        model = config.llm.gemini_model or "gemini-2.5-pro"

    from langchain_google_genai import ChatGoogleGenerativeAI

    logger.info("오케스트레이터 LLM(Gemini, 테스트 모드) 초기화: model=%s", model)
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
        temperature=0.0,
    )


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


def _create_gemini(config: AppConfig) -> BaseChatModel:
    """Google Gemini LLM 클라이언트를 생성한다."""
    from langchain_google_genai import ChatGoogleGenerativeAI

    api_key = config.llm.gemini_api_key
    if not api_key:
        raise ValueError(
            "Gemini API 키가 설정되지 않았습니다. "
            ".env에 LLM_GEMINI_API_KEY 또는 GOOGLE_API_KEY를 추가하세요."
        )

    model = config.llm.gemini_model or config.llm.model
    logger.info("Gemini LLM 초기화: model=%s", model)

    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=api_key,
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
