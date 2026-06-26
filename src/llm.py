"""LLM 인스턴스 생성 모듈.

설정에 따라 적절한 LLM 백엔드를 생성하는 팩토리 함수를 제공한다.
지원 프로바이더: ollama, fabrix, gemini
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from src.config import AppConfig

logger = logging.getLogger(__name__)


# SSE 토큰 스트리밍 대상으로 식별할 LLM 실행 태그(D-009).
# 최종 "사용자 응답" 생성 호출에만 부여하여, SQL 생성·DB 분류 등 중간 LLM 호출의
# 토큰이 채팅 응답으로 새어 나오지 않도록 한다. SSE 핸들러는 이 태그가 붙은
# on_chat_model_stream 이벤트만 토큰으로 전달한다.
USER_RESPONSE_TAG = "user_response"


async def astream_text(
    llm: BaseChatModel,
    messages: list[BaseMessage],
    *,
    tags: list[str] | None = None,
) -> str:
    """LLM을 스트리밍 방식으로 호출하고 전체 응답 텍스트를 누적해 반환한다.

    `ainvoke()`는 `_agenerate`(단일 호출) 경로를 타기 때문에 `astream_events`가
    `on_chat_model_stream` 토큰 이벤트를 내보내지 않는다. 토큰 단위 SSE 스트리밍
    (D-009)을 실제로 동작시키려면 노드가 `.astream()`을 호출해 모델의 스트리밍
    경로(`_astream`)를 거쳐야 한다.

    `_astream`을 구현한 클라이언트(KBGenAIChat)는 토큰 단위로 흘러나오고,
    `_generate`만 구현한 클라이언트(FabriX OpenAI 호환/Ollama)는 BaseChatModel의
    기본 동작에 따라 단일 청크로 폴백되므로 회귀가 없다.

    Args:
        llm: LLM 인스턴스
        messages: 입력 메시지 목록
        tags: 이 LLM 실행에 부여할 태그. SSE 핸들러가 토큰 스트리밍 대상을 식별하는
            데 사용한다(예: [USER_RESPONSE_TAG]). 태그는 자식 run에 전파되어
            astream_events 이벤트의 `tags`로 노출된다.

    Returns:
        누적된 전체 응답 텍스트
    """
    config = {"tags": tags} if tags else None
    parts: list[str] = []
    async for chunk in llm.astream(messages, config=config):
        content = getattr(chunk, "content", "")
        if isinstance(content, str):
            if content:
                parts.append(content)
        elif content:
            # 일부 모델은 content를 블록 리스트로 반환할 수 있다.
            parts.append(str(content))
    return "".join(parts)


def create_llm(config: AppConfig, *, provider_override: str | None = None) -> BaseChatModel:
    """설정에 따라 워커(데이터 평면) LLM 인스턴스를 생성한다.

    Args:
        config: 애플리케이션 설정
        provider_override: 워커 provider 강제 지정 (테스트 전용). 지정 시
            `config.llm.provider` 대신 사용한다. 미지정(None)이면 설정값을 그대로
            따른다 — 운영 동작 무변. (deepagent 경로 전체를 gemini로 테스트하기 위한
            `worker_provider_override` 주입 경로 — Plan 49 §4.7 / D-037)

    Returns:
        LLM 인스턴스

    Raises:
        ValueError: 필수 설정이 누락된 경우
    """
    provider = provider_override or config.llm.provider

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

    # Plan 50 / D-040 (B7): Qwen 계열 vLLM은 no-think(enable_thinking=false)를 기본 적용한다.
    # 추론 토큰이 입력/출력 토큰을 키워 제어 평면 한계를 압박하고 tool_call JSON 파싱을
    # 불안정하게 하므로, vLLM 확장 필드 extra_body.chat_template_kwargs.enable_thinking로 토글한다.
    # 계열 가드: 모델이 Qwen 계열일 때만 extra_body를 부착한다(비-Qwen/미지원 서버에서
    # 알 수 없는 chat_template_kwargs로 오류가 나는 것을 회피 — Plan 50 §3.6(2)).
    #
    # 주의(실측): ChatOpenAI는 model_kwargs 안의 extra_body를 전용 extra_body 인자로 끌어내며
    # UserWarning을 낸다 → langchain_openai 0.x 기준 extra_body는 **전용 생성자 인자**로 직접
    # 전달한다(요청 바디에 동일하게 실려 vLLM chat template로 전파됨, 경고 없음).
    extra_body: dict | None = None
    if "qwen" in config.orchestrator.model.lower():
        extra_body = {
            "chat_template_kwargs": {
                "enable_thinking": config.orchestrator.enable_thinking
            }
        }

    logger.info(
        "오케스트레이터 LLM(vLLM) 초기화: base_url=%s, model=%s, enable_thinking=%s, extra_body=%s",
        config.orchestrator.base_url,
        config.orchestrator.model,
        config.orchestrator.enable_thinking,
        extra_body is not None,
    )
    kwargs: dict = {
        "base_url": config.orchestrator.base_url,
        "api_key": config.orchestrator.api_key or "EMPTY",
        "model": config.orchestrator.model,
        "temperature": 0.0,
        "timeout": config.orchestrator.timeout,
    }
    if extra_body is not None:
        kwargs["extra_body"] = extra_body
    return ChatOpenAI(**kwargs)


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
