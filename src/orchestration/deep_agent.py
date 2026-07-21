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
from langchain_core.messages import AIMessage

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
    - **조기 종료 재개·안내(D-093/D-092)**: 오케스트레이터가 빈 응답(무내용·무도구호출)으로
      루프를 끝내면 대화 이력 + 재개 지시로 재호출해 남은 하위 작업을 이어간다(진전이
      있는 동안 최대 3회). 그래도 미완이면 수행된 조회와 미실행 작업(미완료 todo)을 최종
      응답 말미에 명시하여 부분 결과가 완전한 답처럼 보이는 것을 차단한다(침묵적 강등 금지).

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

    # 조기 종료 감지(D-092): 오케스트레이터가 빈 응답(무내용·무도구호출)으로 루프를 끝내면
    # 남은 하위 작업이 실행되지 않은 것 — 진전이 있는 동안 재개를 반복 시도하고(D-093),
    # 그래도 미완이면 부분 결과임을 사용자 응답에 명시한다.
    # 실측(2026-07-20 라이브): flash-lite는 이 질의 형태에서 **매 도구 결과 후마다** 빈
    # 응답을 반환 → 1회 재개로는 3단계 체인이 끝나지 않아, 재개마다 도구 실행이 늘어나는
    # 동안(진전 게이트) 상한 내 반복한다. 진전 없는 빈 응답 반복은 즉시 중단(무한루프 방지).
    incomplete = _ended_prematurely(result)
    attempts = 0
    while incomplete and attempts < _MAX_RESUME_ATTEMPTS:
        attempts += 1
        before = len(collector)
        logger.warning(
            "deep_agent: 오케스트레이터 빈 응답 조기 종료 감지 → 재개 %d/%d (D-093)",
            attempts, _MAX_RESUME_ATTEMPTS,
        )
        result = await _resume_after_empty_response(agent, result)
        incomplete = _ended_prematurely(result)
        logger.info(
            "deep_agent: 재개 %d회 후 조기 종료=%s (도구 실행 누적 %d건)",
            attempts, incomplete, len(collector),
        )
        if incomplete and len(collector) <= before:
            logger.warning("deep_agent: 재개가 진전 없이 빈 응답 반복 → 재개 중단")
            break

    # step6: 수집된 원본 도구 결과를 FabriX result_aggregator로 재정리한다.
    if collector:
        notice = (
            _build_incomplete_notice(collector, _pending_todos(result))
            if incomplete
            else None
        )
        out = await _aggregate_with_fabrix(
            collector, state, app_config, worker_llm, incomplete_notice=notice
        )
        out["current_node"] = "deep_agent"
        return out

    if incomplete:
        # 도구 0회 + 빈 응답: _extract_final_response는 비어 있지 않은 마지막 메시지를
        # 역순 탐색하므로 사용자 질의(HumanMessage)를 그대로 에코하게 된다 — 대신
        # 아무 조회도 수행되지 않았음을 명시한다(D-092, 침묵적 강등 금지).
        return {
            "final_response": (
                "요청 처리가 시작되지 못하고 중단되었습니다. 수행된 조회가 없습니다. "
                "같은 질문을 다시 시도해 주세요."
            ),
            "current_node": "deep_agent",
        }

    # 도구 미호출(오케스트레이터 직접 응답) → 마지막 메시지 폴백.
    return {"final_response": _extract_final_response(result), "current_node": "deep_agent"}


def _message_text(msg: Any) -> str:
    """메시지 객체/dict에서 텍스트 content를 추출한다 (content blocks 포함)."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _is_empty_ai_message(msg: Any) -> bool:
    """AI 메시지이면서 텍스트도 도구 호출도 없는지 판정한다 (D-092/D-093 공용)."""
    is_ai = isinstance(msg, AIMessage) or (
        isinstance(msg, dict) and msg.get("role") == "assistant"
    )
    if not is_ai:
        return False
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls is None and isinstance(msg, dict):
        tool_calls = msg.get("tool_calls")
    if tool_calls:
        return False
    return not _message_text(msg).strip()


def _ended_prematurely(result: Any) -> bool:
    """오케스트레이터 루프가 빈 AI 응답(무내용·무도구호출)으로 끝났는지 판정한다 (D-092).

    실측(2026-07-20): 오케스트레이터 LLM이 도구 결과 수신 후 output_tokens=0의 빈
    AIMessage를 간헐 반환 → deepagents 루프가 "도구 호출 없음 = 완료"로 종료해 남은
    하위 작업(예: 알람 서버 목록 조회 후의 CPU 평균·제조사/일련번호 조회)이 실행되지
    않는다. 마지막 AI 메시지가 텍스트도 도구 호출도 없으면 조기 종료로 판정한다.

    Args:
        result: agent.ainvoke 반환값

    Returns:
        조기 종료(빈 AI 응답 종결)이면 True
    """
    if not isinstance(result, dict):
        return False
    for msg in reversed(result.get("messages") or []):
        is_ai = isinstance(msg, AIMessage) or (
            isinstance(msg, dict) and msg.get("role") == "assistant"
        )
        if not is_ai:
            continue
        return _is_empty_ai_message(msg)
    return False


# 빈 응답 재개 상한(D-093): 진전(도구 실행 증가)이 있어도 이 횟수를 넘겨 재개하지 않는다.
# 실측 3단계 체인(알람→CPU 평균→제조사/일련번호) 기준 재개 3회면 최종 답변까지 도달 가능.
_MAX_RESUME_ATTEMPTS = 3

# 빈 응답 재개 지시(D-093): 오케스트레이터가 0토큰 응답으로 멈췄을 때 이어서 진행시키는
# user 턴. 모델 교체 없이(사용자 결정) 같은 대화 이력으로 재호출한다.
_RESUME_NUDGE = (
    "직전 응답이 비어 있었습니다. 사용자 요청 처리를 계속하세요. "
    "아직 수행하지 않은 조회가 있으면 필요한 도구를 순서대로 호출하고, "
    "모든 조회가 끝났으면 도구 결과에 근거해 최종 답변을 작성하세요."
)


async def _resume_after_empty_response(agent: Any, result: dict) -> Any:
    """빈 응답으로 끝난 오케스트레이터 루프를 재개한다 (D-093, 호출부가 횟수 제어).

    직전 실행의 메시지 이력에서 말미의 빈 AI 메시지를 제거하고(모델이 빈 assistant 턴을
    거부/오해하지 않도록) 재개 지시(user 턴)를 덧붙여 같은 에이전트를 다시 호출한다.
    기존 도구 결과는 collector(클로저)에 이미 누적되어 유지되며, 재개 호출이 실패하면
    직전 결과로 안전하게 되돌아간다(그 경우 기존 조기 종료 안내 경로가 동작 — D-092).

    Args:
        agent: build_deep_agent가 조립한 컴파일된 에이전트
        result: 직전 agent.ainvoke 반환값 (조기 종료 상태)

    Returns:
        재개 호출의 반환값 (실패 시 직전 result 그대로)
    """
    messages = list((result.get("messages") if isinstance(result, dict) else None) or [])
    while messages and _is_empty_ai_message(messages[-1]):
        messages.pop()
    messages.append({"role": "user", "content": _RESUME_NUDGE})
    try:
        return await agent.ainvoke({"messages": messages})
    except Exception as e:  # noqa: BLE001 — 재개 실패는 1차 결과 + 안내문(D-092)으로 강등
        logger.error("deep_agent 재개 호출 실패 → 1차 결과로 진행: %s", e)
        return result


def _pending_todos(result: Any) -> list[str]:
    """deepagents TodoListMiddleware 상태에서 미완료 todo 내용을 추출한다 (D-092).

    오케스트레이터가 write_todos로 계획을 남긴 경우, status가 completed가 아닌 항목이
    곧 "수행되지 않은 하위 작업"의 명시적 목록이다(계획 미작성 시 빈 목록).

    Args:
        result: agent.ainvoke 반환값

    Returns:
        미완료 todo content 목록
    """
    if not isinstance(result, dict):
        return []
    pending: list[str] = []
    for todo in result.get("todos") or []:
        if not isinstance(todo, dict) or todo.get("status") == "completed":
            continue
        content = str(todo.get("content", "")).strip()
        if content:
            pending.append(content)
    return pending


def _build_incomplete_notice(collector: list, pending_todos: list[str]) -> str:
    """조기 종료 시 최종 응답 말미에 붙일 미실행 작업 안내문을 만든다 (D-092).

    수행된 조회는 collector의 task sub_query(오케스트레이터가 쓴 자연어 지시)로,
    수행되지 않은 작업은 미완료 todo(있을 때)로 명시한다. todo가 없으면 남은 요청
    항목이 실행되지 않았음을 일반 문구로 알린다. LLM에 의존하지 않는 결정적 안내다.

    Args:
        collector: build_tools가 적재한 [(task, result), ...]
        pending_todos: 미완료 todo content 목록

    Returns:
        사용자 응답 말미에 덧붙일 안내문
    """
    lines = ["⚠️ 내부 처리가 중간에 중단되어 요청의 일부만 수행되었습니다."]
    executed: list[str] = []
    for task, _ in collector:
        sub = str(task.get("sub_query", "")).strip()
        if sub and sub not in executed:
            executed.append(sub)
    if executed:
        lines.append("수행된 조회:")
        lines.extend(f"- {s}" for s in executed)
    if pending_todos:
        lines.append("수행되지 않은 작업:")
        lines.extend(f"- {t}" for t in pending_todos)
    else:
        lines.append("질문의 나머지 항목은 수행되지 않았습니다.")
    lines.append("위 답변은 수행된 조회에 한정된 부분 결과입니다. 같은 질문을 다시 시도해 주세요.")
    return "\n".join(lines)


async def _aggregate_with_fabrix(
    collector: list,
    state: dict,
    app_config: AppConfig,
    worker_llm: Optional[BaseChatModel],
    *,
    incomplete_notice: Optional[str] = None,
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
        incomplete_notice: 조기 종료(D-092) 시 최종 응답 말미에 덧붙일 미실행 작업
            안내문 (없으면 None — 정상 완료)

    Returns:
        result_aggregator 반환 dict (final_response[, output_file/...])
    """
    from src.orchestration.result_aggregator import result_aggregator

    task_plan = [task for task, _ in collector]
    task_results = {task["task_id"]: res for task, res in collector}
    agg_state = dict(state)
    agg_state["task_plan"] = task_plan
    agg_state["task_results"] = task_results
    if incomplete_notice:
        # 조기 종료 안내(D-092): result_aggregator가 최종 응답 말미에 결정적으로 덧붙인다.
        agg_state["orchestration_incomplete_notice"] = incomplete_notice

    # synthesize=True: 오케스트레이터가 동일 질문을 재시도하면 collector에 1·2차 결과가
    # 모두 쌓인다. deterministic 이어붙이기는 "없음→있음" 모순 이중 답변을 한 말풍선에
    # 남기므로, LLM 1회로 단일 일관 답변을 합성한다(D-062).
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
