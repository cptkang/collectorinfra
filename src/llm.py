"""LLM 인스턴스 생성 모듈.

설정에 따라 적절한 LLM 백엔드를 생성하는 팩토리 함수를 제공한다.
지원 프로바이더: ollama, fabrix, gemini, mlx(로컬 테스트 전용 — plans/100)
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from pydantic import SecretStr

from src.config import AppConfig
from src.utils.json_extract import coerce_content_text

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
            # 일부 모델(Gemini 3.x thinking 계열 등)은 content를 블록 리스트로 반환한다.
            # str()로 감싸면 파이썬 repr이 그대로 사용자 응답에 실려 마크다운 표가
            # 한 줄 텍스트로 깨진다 — 블록의 text만 뽑아 이어 붙인다.
            parts.append(coerce_content_text(content))
    return "".join(parts)


def create_llm(
    config: AppConfig,
    *,
    provider_override: str | None = None,
    purpose: str = "deterministic",
) -> BaseChatModel:
    """설정에 따라 워커(데이터 평면) LLM 인스턴스를 생성한다.

    Args:
        config: 애플리케이션 설정
        provider_override: 워커 provider 강제 지정 (테스트 전용). 지정 시
            `config.llm.provider` 대신 사용한다. 미지정(None)이면 설정값을 그대로
            따른다 — 운영 동작 무변. (deepagent 경로 전체를 gemini로 테스트하기 위한
            `worker_provider_override` 주입 경로 — Plan 49 §4.7 / D-037)
        purpose: 하이퍼파라미터 프로파일 선택(D-194). "deterministic"(기본 — SQL 생성·
            분류 등 중간 호출)이면 `fabrix_llm_config`, "answer"(최종 사용자 응답 합성
            — USER_RESPONSE_TAG 부여 3개 지점)면 `fabrix_answer_llm_config`를 적용한다.
            현재 fabrix provider에만 반영되며 ollama/gemini는 무시한다(temperature 0.0 고정 유지).

    Returns:
        LLM 인스턴스

    Raises:
        ValueError: 필수 설정이 누락된 경우
    """
    provider = provider_override or config.llm.provider

    if provider == "ollama":
        return _create_ollama(config)
    elif provider == "fabrix":
        return _create_fabrix(config, purpose=purpose)
    elif provider == "gemini":
        return _create_gemini(config)
    elif provider == "mlx":
        return _create_mlx(config)
    else:
        raise ValueError(f"지원하지 않는 LLM 프로바이더: {provider}")


def create_orchestrator_llm(config: AppConfig) -> BaseChatModel:
    """deepagents 구동용 tool-calling LLM을 생성한다 (Plan 49 / D-037, 트랙 B).

    provider로 오케스트레이터를 선택한다:
    - "vllm"(기본·운영): vLLM의 OpenAI 호환 `/v1`에 `ChatOpenAI`로 연결, 네이티브 bind_tools 사용.
    - "gemini"(테스트/PoC 전용 — §4.7): `ChatGoogleGenerativeAI`. 외부 egress 필요, 폐쇄망 운영 부적합.
    - "mlx"(로컬 테스트 전용 — plans/100): vLLM 경로를 재사용한다(`mlx_lm.server`도 OpenAI 호환).

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
    #
    # provider=mlx(plans/100)는 모델명과 무관하게 부착한다 — 미전송이면 Qwen3.5가 생성 예산
    # 전부를 reasoning에 쓰고 content가 빈다. `default_model` 별칭이면 위 qwen 가드가 빗나가
    # 정확히 그 상태가 된다(J-1 ②). 템플릿이 이 변수를 쓰지 않는 모델에 보내도 무해함을
    # 실측했다(J-1 ⑥).
    is_mlx = config.orchestrator.provider == "mlx"
    extra_body: dict | None = None
    if is_mlx or "qwen" in config.orchestrator.model.lower():
        extra_body = {
            "chat_template_kwargs": {
                "enable_thinking": config.orchestrator.enable_thinking
            }
        }

    logger.info(
        "오케스트레이터 LLM(%s) 초기화: base_url=%s, model=%s, enable_thinking=%s, extra_body=%s",
        "MLX" if is_mlx else "vLLM",
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
    if is_mlx:
        # mlx_lm.server는 미전송 시 512토큰에서 절단한다(J-1 ①). vllm 경로는 미전송을 유지한다.
        kwargs["max_tokens"] = config.llm.mlx_max_tokens

    # D-060: 목적지 vLLM이 443을 listen하되 유효 인증서를 쓰지 않는 폐쇄망에서는
    # SSL 검증을 끈다. health check(vllm_healthy)만 끄면 실제 tool-calling 요청이 SSL로
    # 실패하므로, ChatOpenAI가 쓰는 httpx 클라이언트(sync/async 모두)에 verify=False를 주입한다.
    if not config.orchestrator.verify_ssl:
        import httpx

        logger.warning(
            "오케스트레이터 vLLM SSL 검증 비활성화(ORCHESTRATOR_VERIFY_SSL=false, D-060): base_url=%s",
            config.orchestrator.base_url,
        )
        kwargs["http_client"] = httpx.Client(verify=False)
        kwargs["http_async_client"] = httpx.AsyncClient(verify=False)

    if is_mlx:
        # 워커와 같은 서브클래스 — max_tokens 절단(finish_reason=length)을 경고로 남긴다.
        # 오케스트레이터 LLM은 구조화 출력(instructor)에 들어가지 않으므로 클래스명 기반
        # 모드 선택(MD_JSON)의 영향이 없다(호출부는 전부 워커 llm).
        from src.clients.mlx_client import MLXChatOpenAI, stream_chunk_timeout_kwargs

        kwargs.update(stream_chunk_timeout_kwargs(config.orchestrator.timeout))
        return MLXChatOpenAI(**kwargs)
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


def _create_mlx(config: AppConfig) -> BaseChatModel:
    """MLX 로컬 서버(`mlx_lm.server`) 워커 LLM을 생성한다 (plans/100 — 로컬 테스트 전용).

    `ChatOpenAI` 대신 서브클래스 `MLXChatOpenAI`를 쓴다 — 구조화 출력 어댑터가 클래스명으로
    모드를 고르기 때문이다(`src/clients/mlx_client.py` 참조).
    """
    try:
        from src.clients.mlx_client import MLXChatOpenAI, stream_chunk_timeout_kwargs
    except ImportError as e:
        raise ValueError(
            "LLM_PROVIDER=mlx에는 langchain-openai가 필요합니다. "
            'pip install -e ".[deepagents]"로 설치하세요.'
        ) from e

    model = config.llm.mlx_model or "default_model"
    logger.info(
        "MLX LLM 초기화: base_url=%s, model=%s, max_tokens=%s, enable_thinking=%s",
        config.llm.mlx_base_url,
        model,
        config.llm.mlx_max_tokens,
        config.llm.mlx_enable_thinking,
    )
    return MLXChatOpenAI(
        base_url=config.llm.mlx_base_url,
        api_key=SecretStr("EMPTY"),
        model=model,
        temperature=0.0,
        # 서버 기본 512토큰 절단 방지(J-1 ①). ChatOpenAI `max_tokens` 필드의 alias로 넘긴다
        max_completion_tokens=config.llm.mlx_max_tokens,
        timeout=config.llm.mlx_timeout,
        # prefill이 끝나야 첫 청크가 온다 — 청크 대기 상한 120초 기본값에 끊기지 않게 한다
        **stream_chunk_timeout_kwargs(config.llm.mlx_timeout),
        # 모델명과 무관하게 부착한다 — 미전송이면 content가 빈다(J-1 ②)
        extra_body={
            "chat_template_kwargs": {"enable_thinking": config.llm.mlx_enable_thinking}
        },
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


def _resolve_fabrix_llm_profile(config: AppConfig, purpose: str) -> dict:
    """purpose에 해당하는 FabriX 하이퍼파라미터 프로파일을 dict로 해석한다(D-194).

    config 필드는 JSON 문자열이며 유효성은 LLMConfig field_validator가 기동 시점에
    보장한다. 여기서의 파싱 실패는 방어적 폴백이되, 침묵하지 않고 ERROR로 남긴다.
    """
    import json

    raw = config.llm.fabrix_llm_config
    if purpose == "answer" and config.llm.fabrix_answer_llm_config.strip():
        raw = config.llm.fabrix_answer_llm_config
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(
            "FabriX llm_config 파싱 실패 — 프로파일 없이 진행(서버 기본값): %s | raw=%r",
            e, raw,
        )
        return {}
    if not isinstance(parsed, dict):
        logger.error("FabriX llm_config가 JSON 객체가 아님 — 무시: raw=%r", raw)
        return {}
    return parsed


def _create_fabrix(config: AppConfig, purpose: str = "deterministic") -> BaseChatModel:
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
    profile = _resolve_fabrix_llm_profile(config, purpose)

    # KBGenAI 모드 (client_key가 있는 경우)
    if config.llm.fabrix_client_key:
        from src.clients.fabrix_kbgenai import KBGenAIChat

        logger.info(
            "FabriX KBGenAI 초기화: endpoint=%s, purpose=%s, llm_config=%s",
            config.llm.fabrix_base_url, purpose, profile or "(미설정)",
        )
        return KBGenAIChat(
            endpoint_url=config.llm.fabrix_base_url,
            x_openapi_token=config.llm.fabrix_api_key,
            x_generative_ai_client=config.llm.fabrix_client_key,
            asset_id=model,
            kb_id="User",
            system_prompt="",
            llm_config=profile or None,
            total_timeout=config.llm.fabrix_total_timeout,
        )

    # OpenAI 호환 모드 — llmConfig는 KBGenAI 전용 규약이라 temperature만 매핑한다
    from src.clients.fabrix_client import FabriXAPIClient

    logger.info("FabriX API 초기화: base_url=%s, model=%s", config.llm.fabrix_base_url, model)
    return FabriXAPIClient(
        base_url=config.llm.fabrix_base_url,
        chat_model=model,
        api_key=config.llm.fabrix_api_key,
        temperature=float(profile.get("temperature", 0.0)),
    )
