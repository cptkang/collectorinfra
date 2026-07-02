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


def vllm_healthy(base_url: str, timeout: int = 3, *, verify_ssl: bool = True) -> bool:
    """vLLM(OpenAI 호환) 엔드포인트의 가용성을 health check한다.

    `{base_url}/models`에 GET하여 200이면 가용으로 판정한다(비용 0에 가까움).

    Args:
        base_url: vLLM /v1 엔드포인트
        timeout: 요청 타임아웃(초)
        verify_ssl: SSL 인증서 검증 여부(D-060). 목적지가 443을 listen하되 유효 인증서를
            쓰지 않는 폐쇄망에서 False로 두면 SSL 검증을 건너뛴다. False면 SSL 검증 실패로
            인한 잘못된 semantic_router 폴백을 방지한다.

    Returns:
        가용 시 True, 미설정/미응답/오류 시 False
    """
    if not base_url:
        return False
    import requests

    url = base_url.rstrip("/") + "/models"
    try:
        resp = requests.get(url, timeout=timeout, verify=verify_ssl)
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
    return vllm_healthy(
        config.orchestrator.base_url,
        config.orchestrator.health_timeout,
        verify_ssl=config.orchestrator.verify_ssl,
    )


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
    collector: Optional[list] = None,
) -> Any:
    """deepagents 실제 패키지로 에이전트를 조립한다 (vLLM 오케스트레이터 + FabriX 워커).

    Args:
        config: 앱 설정
        worker_llm: FabriX 워커 LLM (없으면 create_llm으로 생성)
        ambient_state: 도구에 주입할 주변 컨텍스트
        collector: (선택) 원본 도구 결과 수집기 — 최종 FabriX 응답 생성용(§4.3 step6)

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
    tools = build_tools(worker, config, ambient_state, collector=collector)

    logger.info(
        "deepagents 에이전트 조립: 오케스트레이터=vLLM(%s), 도구 %d개, 워커=%s",
        config.orchestrator.model, len(tools), config.llm.provider,
    )
    # create_deep_agent의 실측 시그니처는 system_prompt(=instructions 아님, 0.6.10).
    return create_deep_agent(
        tools=tools,
        model=orchestrator,
        system_prompt=ORCHESTRATOR_INSTRUCTIONS,
    )


# 에이전트 상태(AgentState) → 도구로 전달할 ambient 컨텍스트 키.
# (subagents._make_isolated_input가 읽는 식별/권한 필드와 동일 집합)
_AMBIENT_KEYS = (
    "thread_id",
    "user_id",
    "user_department",
    "allowed_db_ids",
    "request_id",
    "client_ip",
    "parsed_requirements",
    "conversation_context",
    "template_structure",
    "target_sheets",
    "file_type",
    "mapped_db_ids",
    "db_column_mapping",
    "column_mapping",
    "mapping_sources",
    "csv_sheet_data",
)


def _extract_ambient_state(state: dict) -> dict:
    """AgentState에서 도구에 주입할 주변 컨텍스트만 추린다.

    deepagents 도구는 `sub_query`(문자열)만 시그니처로 노출하므로, 식별/권한/양식
    등 주변 컨텍스트는 클로저(`build_tools(ambient_state=...)`)로 주입한다(§4.4).

    Args:
        state: 전체 에이전트 상태

    Returns:
        ambient 컨텍스트 dict
    """
    return {k: state.get(k) for k in _AMBIENT_KEYS if state.get(k) is not None}


async def run_deep_agent(
    state: dict,
    *,
    app_config: AppConfig,
    worker_llm: Optional[BaseChatModel] = None,
) -> dict:
    """deepagents 에이전트를 실행하여 최종 응답을 생성하는 그래프 노드 (Plan 49 §4.3/§4.6).

    - vLLM(또는 Gemini) 오케스트레이터가 tool-calling으로 도구를 호출·재계획한다.
    - 도구 내부는 FabriX 워커 파이프라인이 실제 작업을 수행한다(실질 응답처리).
    - **최종 응답(§4.3 step6)**: 오케스트레이터의 자유 서술을 그대로 노출하지 않고,
      도구가 수집기(collector)에 남긴 **원본 결과**를 FabriX `result_aggregator`로
      재정리하여 생성한다(성공기준 5). 도구가 한 번도 호출되지 않은 경우(예: 일반 질의를
      오케스트레이터가 직접 답)에만 마지막 메시지를 폴백으로 사용한다.
    - deepagents 패키지 미설치(폐쇄망 wheel 미반입) 등 조립 실패 시 RuntimeError를
      잡아 명확한 안내 메시지로 안전 종료한다(그래프 크래시 방지 — 검증 기준).

    Args:
        state: 현재 에이전트 상태 (user_query + ambient 컨텍스트 포함)
        app_config: 앱 설정 (다른 노드와 동일한 파라미터명 — LangGraph가 'config'를
            RunnableConfig로 가로채는 것을 피한다)
        worker_llm: FabriX 워커 LLM (없으면 build_deep_agent가 create_llm으로 생성)

    Returns:
        업데이트할 State 필드 (final_response[, output_file/output_file_name], current_node)
    """
    ambient = _extract_ambient_state(state)
    user_query = state.get("user_query", "")
    collector: list[tuple[dict, dict]] = []

    try:
        agent = build_deep_agent(
            app_config, worker_llm=worker_llm, ambient_state=ambient, collector=collector
        )
    except RuntimeError as e:
        # 폐쇄망 wheel 미반입 등 — 그래프를 죽이지 않고 명확히 안내한다.
        logger.error("deepagents 에이전트 조립 실패 → 안전 종료: %s", e)
        return {
            "final_response": (
                "deepagents 오케스트레이션 백엔드를 사용할 수 없습니다. "
                "관리자에게 문의하세요(폐쇄망 패키지 반입 필요)."
            ),
            "current_node": "deep_agent",
        }

    result = await agent.ainvoke({"messages": [{"role": "user", "content": user_query}]})

    # step6: 수집된 원본 도구 결과를 FabriX result_aggregator로 재정리한다.
    if collector:
        out = await _aggregate_with_fabrix(collector, state, app_config, worker_llm)
        out["current_node"] = "deep_agent"
        return out

    # 도구 미호출(오케스트레이터 직접 응답) → 마지막 메시지 폴백.
    return {"final_response": _extract_final_response(result), "current_node": "deep_agent"}


async def _aggregate_with_fabrix(
    collector: list,
    state: dict,
    app_config: AppConfig,
    worker_llm: Optional[BaseChatModel],
) -> dict:
    """수집된 도구 결과(원본)를 FabriX result_aggregator로 최종 응답으로 재정리한다.

    deepagents 런타임의 도구 결과는 `messages` 안의 ToolMessage(요약·상한 적용)로만
    남으므로, 토큰 폭증을 피하면서도 정밀한 최종 응답을 위해 수집기의 **원본 결과**를
    `task_plan`/`task_results`로 재구성하여 기존 result_aggregator(FabriX)에 전달한다.

    Args:
        collector: build_tools가 적재한 [(task, result), ...]
        state: 전체 에이전트 상태 (output_generator 입력 보강용)
        app_config: 앱 설정
        worker_llm: FabriX 워커 LLM (없으면 result_aggregator가 내부 생성)

    Returns:
        result_aggregator 반환 dict (final_response[, output_file/...])
    """
    from src.orchestration.result_aggregator import result_aggregator

    task_plan = [task for task, _ in collector]
    task_results = {task["task_id"]: res for task, res in collector}
    agg_state = dict(state)
    agg_state["task_plan"] = task_plan
    agg_state["task_results"] = task_results

    # synthesize=True: 오케스트레이터가 동일 질문을 재시도하면 collector에 1·2차 결과가
    # 모두 쌓인다. deterministic 이어붙이기는 "없음→있음" 모순 이중 답변을 한 말풍선에
    # 남기므로, LLM 1회로 단일 일관 답변을 합성한다(D-048).
    out = await result_aggregator(
        agg_state, llm=worker_llm, app_config=app_config, synthesize=True
    )
    # current_node는 호출부에서 deep_agent로 통일한다.
    out.pop("current_node", None)
    return out


def _extract_final_response(result: Any) -> str:
    """deepagents 실행 결과에서 최종 자연어 응답 텍스트를 추출한다.

    deepagents는 LangGraph 메시지 상태(`{"messages": [...]}`)를 반환한다. 마지막
    AI 메시지의 content를 최종 응답으로 사용한다(도구 결과는 이미 FabriX가 생성한
    응답에 근거 — §2/§4.3).

    Args:
        result: agent.ainvoke 반환값

    Returns:
        최종 응답 텍스트
    """
    if isinstance(result, dict):
        messages = result.get("messages") or []
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content is None and isinstance(msg, dict):
                content = msg.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):  # content blocks
                text = "".join(
                    b.get("text", "") for b in content if isinstance(b, dict)
                ).strip()
                if text:
                    return text
    return "응답을 생성할 수 없습니다."
