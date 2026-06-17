"""deepagents 실제 패키지 조립 + 백엔드 선택 (Plan 49, 트랙 B).

- `vllm_healthy` / `select_orchestration_backend`: vLLM 가용성으로 백엔드를 선택한다
  (vLLM 서빙 시 deepagents, 미서빙/off 시 기존 semantic_router — §4.6).
- `build_deep_agent`: vLLM 오케스트레이터 + FabriX 워커로 deepagents 에이전트를 조립한다.
  deepagents 패키지는 **lazy import**(폐쇄망 wheel 반입 후 동작) — 미설치 시 본 모듈 import는
  안전하며, `build_deep_agent` 호출 시에만 명확한 오류를 발생시킨다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig
from src.orchestration.deepagents_tools import build_tools
from src.prompts.orchestrator import ORCHESTRATOR_INSTRUCTIONS

logger = logging.getLogger(__name__)


def vllm_healthy(base_url: str, timeout: int = 3) -> bool:
    """vLLM(OpenAI 호환) 엔드포인트의 가용성을 health check한다.

    `{base_url}/models`에 GET하여 200이면 가용으로 판정한다(비용 0에 가까움).

    Args:
        base_url: vLLM /v1 엔드포인트
        timeout: 요청 타임아웃(초)

    Returns:
        가용 시 True, 미설정/미응답/오류 시 False
    """
    if not base_url:
        return False
    import requests

    url = base_url.rstrip("/") + "/models"
    try:
        resp = requests.get(url, timeout=timeout)
        return resp.status_code == 200
    except Exception as e:  # noqa: BLE001 — 가용성 판정이므로 모든 예외를 미가용으로 처리
        logger.info("vLLM health check 실패(%s) → semantic_router 폴백: %s", url, e)
        return False


def orchestrator_available(config: AppConfig) -> bool:
    """오케스트레이터 가용성을 판정한다 (provider별 — Plan 49 §4.6/§4.7).

    - vllm: `/v1/models` health check.
    - gemini(테스트): api_key 유무(별도 health 엔드포인트 없음).

    Args:
        config: 앱 설정

    Returns:
        가용 시 True
    """
    if config.orchestrator.provider == "gemini":
        return bool(config.orchestrator.api_key or config.llm.gemini_api_key)
    return vllm_healthy(config.orchestrator.base_url, config.orchestrator.health_timeout)


def select_orchestration_backend(config: AppConfig) -> str:
    """오케스트레이터 가용성으로 백엔드를 선택한다 (Plan 49 §4.6).

    `enable_deepagents_package`가 활성이고 오케스트레이터(vLLM 또는 Gemini)가 가용하면
    트랙 B(deepagents), 그 외(플래그 off 또는 미가용)는 기존 semantic_router로 회귀한다.

    Args:
        config: 앱 설정

    Returns:
        "deep_agent" | "semantic_router"
    """
    if config.enable_deepagents_package and orchestrator_available(config):
        return "deep_agent"
    return "semantic_router"


def build_deep_agent(
    config: AppConfig,
    *,
    worker_llm: Optional[BaseChatModel] = None,
    ambient_state: Optional[dict] = None,
) -> Any:
    """deepagents 실제 패키지로 에이전트를 조립한다 (vLLM 오케스트레이터 + FabriX 워커).

    Args:
        config: 앱 설정
        worker_llm: FabriX 워커 LLM (없으면 create_llm으로 생성)
        ambient_state: 도구에 주입할 주변 컨텍스트

    Returns:
        deepagents `create_deep_agent`가 반환한 컴파일된 LangGraph 에이전트

    Raises:
        RuntimeError: deepagents 패키지 미설치(폐쇄망 wheel 미반입)
    """
    try:
        from deepagents import create_deep_agent
    except ImportError as e:
        raise RuntimeError(
            "deepagents 패키지가 설치되지 않았습니다. 폐쇄망 wheel 반입 후 사용하세요 "
            "(Plan 49 §3.1 버전·wheel 요구 / §7-1 설치 검증)."
        ) from e

    from src.llm import create_llm, create_orchestrator_llm

    orchestrator = create_orchestrator_llm(config)
    worker = worker_llm or create_llm(config)
    tools = build_tools(worker, config, ambient_state)

    logger.info(
        "deepagents 에이전트 조립: 오케스트레이터=vLLM(%s), 도구 %d개, 워커=%s",
        config.orchestrator.model, len(tools), config.llm.provider,
    )
    return create_deep_agent(
        tools=tools,
        model=orchestrator,
        instructions=ORCHESTRATOR_INSTRUCTIONS,
    )
