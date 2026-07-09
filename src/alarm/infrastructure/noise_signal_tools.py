"""노이즈 신호 수집 도구 + 트랙 B 백엔드 조립 (Plan 52 Phase E5, D-048.7).

deepagents Advisory Enricher(§3.12·§8.7)가 사용하는 **읽기전용 신호 수집 도구**와
트랙 B(vLLM `bind_tools`) 경량 ReAct 백엔드를 infrastructure 계층에 배치한다.

계층 배치 근거(치명적 — arch_check error 회피):
    - enricher 노드는 application 계층이라 orchestration(`src/orchestration/deep_agent.py`·
      `deepagents_tools.py`)을 import할 수 없다(application→orchestration = error). 따라서
      도구 빌더·vLLM health check·ReAct 루프를 **infrastructure(본 모듈)** 에 두고, enricher
      (application)가 이를 호출한다(application→infrastructure = 정방향 OK).
    - 본 모듈은 domain(`severity_signatures`)·infrastructure(`polestar_noise_context`,
      `src.llm`)·config·외부 패키지(langchain_*, requests)만 참조한다.

안전 제약:
    - 도구는 **읽기전용**만 감싼다 — 내부는 폴스타 고정 SQL(SELECT)·결정적 시그니처 스캔뿐.
      DML/DDL 절대 금지(도구가 감싸는 `PolestarNoiseContextRepository`·`scan_signature_severity`
      모두 읽기전용).
    - collector 패턴(Known Mistakes 2026-06-17): 도구는 **truncate 전 원본 결과**를 collector에
      적재한다. enricher는 ToolMessage(요약·상한본)를 재파싱하지 않고 collector 원본을 소비한다.
    - vLLM 미서빙 환경(현 운영)에서도 import·조립은 안전하며, 라이브 호출은 강요하지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool

from src.alarm.domain.severity_signatures import scan_signature_severity
from src.config import AppConfig

logger = logging.getLogger(__name__)

# collector 항목 signal 키 — enricher가 원본 결과를 분류·소비하는 식별자.
SIGNAL_MESSAGE_SIGNATURE = "message_signature"
SIGNAL_IMPORTANCE_MAINTENANCE = "importance_maintenance"
SIGNAL_DEPENDENCY = "dependency"

# 도구 함수명(vLLM에 노출) — semantic-routing 트랙 A의 needed_signals 라벨과 동일 어휘.
TOOL_NAME_BY_SIGNAL: dict[str, str] = {
    SIGNAL_MESSAGE_SIGNATURE: "scan_message_signature",
    SIGNAL_IMPORTANCE_MAINTENANCE: "collect_importance_maintenance",
    SIGNAL_DEPENDENCY: "collect_dependency_status",
}


def vllm_healthy(base_url: str, timeout: int = 3) -> bool:
    """vLLM(OpenAI 호환) 엔드포인트의 가용성을 health check한다 (deep_agent 패턴 복제).

    `{base_url}/models`에 GET하여 200이면 가용으로 판정한다(비용 0에 가까움). base_url이
    비어 있거나 미응답·오류면 False → enricher가 트랙 A/결정적으로 자동 강등한다.

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
        logger.info("vLLM health check 실패(%s) → 트랙 A/결정적 폴백: %s", url, e)
        return False


def scan_message_signature(condition_log: str) -> dict[str, Any]:
    """condition_log를 결정적 OS 장애 시그니처 스캐너로 조사한다(읽기전용·LLM 없음).

    Plan 51 부록 A.1 시그니처(도메인 `scan_signature_severity`)를 그대로 사용한다.
    매칭 시 상향 심각도 후보, 없으면 severity=None을 담은 신호 dict를 반환한다.
    """
    hit = scan_signature_severity(condition_log or "")
    if hit is None:
        return {"signal": SIGNAL_MESSAGE_SIGNATURE, "severity": None, "label": ""}
    return {"signal": SIGNAL_MESSAGE_SIGNATURE, "severity": hit[0], "label": hit[1]}


async def collect_importance_maintenance(noise_repo, event) -> dict[str, Any]:  # noqa: ANN001
    """자산 중요도/유지보수/알림정책을 폴스타 고정 SQL로 수집한다(읽기전용).

    `PolestarNoiseContextRepository.fetch(event)`를 재사용한다(§8.3). noise_repo가 없으면
    수집 불가(unavailable) 신호를 반환한다(graceful).
    """
    if noise_repo is None:
        return {"signal": SIGNAL_IMPORTANCE_MAINTENANCE, "context": None}
    ctx = await noise_repo.fetch(event)
    return {"signal": SIGNAL_IMPORTANCE_MAINTENANCE, "context": ctx}


async def collect_dependency_status(noise_repo, event) -> dict[str, Any]:  # noqa: ANN001
    """부모 리소스 가용상태(의존성/연쇄)를 폴스타 고정 SQL로 수집한다(읽기전용, §3.6).

    `fetch(event, collect_dependency=True)`로 parent_avail_status를 조회한다. noise_repo가
    없으면 수집 불가 신호를 반환한다(graceful).
    """
    if noise_repo is None:
        return {"signal": SIGNAL_DEPENDENCY, "context": None}
    ctx = await noise_repo.fetch(event, collect_dependency=True)
    return {"signal": SIGNAL_DEPENDENCY, "context": ctx}


# collector 원본 수집을 위한 결정적 컬렉터 매핑(트랙 A·B 공용).
# needed_signal 라벨 → (수집 코루틴, noise_repo 필요 여부).
async def collect_signal(
    signal: str, *, noise_repo, event  # noqa: ANN001
) -> Optional[dict[str, Any]]:
    """단일 needed_signal을 결정적으로 수집한다(트랙 A 컬렉터·트랙 B 도구 내부 공용).

    지원하지 않는 라벨이면 None을 반환한다(무시). 예외는 잡아 None으로 강등한다(graceful).
    """
    try:
        if signal == SIGNAL_MESSAGE_SIGNATURE:
            return scan_message_signature(getattr(event, "condition_log", "") or "")
        if signal == SIGNAL_IMPORTANCE_MAINTENANCE:
            return await collect_importance_maintenance(noise_repo, event)
        if signal == SIGNAL_DEPENDENCY:
            return await collect_dependency_status(noise_repo, event)
    except Exception:  # noqa: BLE001 — 신호 수집 실패는 해당 신호만 손실(부분 반환)
        logger.warning("노이즈 신호 수집 실패 — signal=%s", signal, exc_info=True)
    return None


def build_noise_signal_tools(
    noise_repo,  # noqa: ANN001
    event,  # noqa: ANN001
    *,
    collect_dependency: bool = False,
    collector: Optional[list] = None,
) -> list[StructuredTool]:
    """읽기전용 노이즈 신호 수집 LangChain StructuredTool 목록을 조립한다(트랙 B용).

    도구는 인자 없이 호출되며(event는 클로저로 바인딩), 실행 시 **원본 결과**를 collector에
    적재한다(Known Mistakes 2026-06-17 — ToolMessage 재파싱 금지). vLLM 오케스트레이터는
    이 도구들을 tool-calling으로 동적 호출한다.

    Args:
        noise_repo: PolestarNoiseContextRepository (없으면 폴스타 신호는 unavailable).
        event: AlarmEvent (condition_log/server_name/alarm_name 등).
        collect_dependency: True면 의존성 상태 수집 도구도 포함한다(§3.6, 기본 off).
        collector: 원본 결과 수집 리스트 — 도구 실행마다 신호 dict를 append한다.

    Returns:
        StructuredTool 목록 (create_deep_agent/bind_tools의 tools 인자).
    """

    def _make_signature_tool() -> StructuredTool:
        async def _run() -> str:
            result = scan_message_signature(getattr(event, "condition_log", "") or "")
            if collector is not None:
                collector.append(result)
            return _summarize(result)

        return StructuredTool.from_function(
            coroutine=_run,
            name=TOOL_NAME_BY_SIGNAL[SIGNAL_MESSAGE_SIGNATURE],
            description=(
                "알람 메시지(condition_log)를 OS 장애 시그니처로 스캔하여 상향 심각도 후보를"
                " 반환한다. 읽기전용·결정적(LLM 없음). 인자 없음."
            ),
        )

    def _make_importance_tool() -> StructuredTool:
        async def _run() -> str:
            result = await collect_importance_maintenance(noise_repo, event)
            if collector is not None:
                collector.append(result)
            return _summarize(result)

        return StructuredTool.from_function(
            coroutine=_run,
            name=TOOL_NAME_BY_SIGNAL[SIGNAL_IMPORTANCE_MAINTENANCE],
            description=(
                "이 서버의 자산 중요도·유지보수 모드·폴스타 알림정책을 폴스타 DB에서 읽기전용"
                " 고정 SQL로 조회한다. 인자 없음."
            ),
        )

    def _make_dependency_tool() -> StructuredTool:
        async def _run() -> str:
            result = await collect_dependency_status(noise_repo, event)
            if collector is not None:
                collector.append(result)
            return _summarize(result)

        return StructuredTool.from_function(
            coroutine=_run,
            name=TOOL_NAME_BY_SIGNAL[SIGNAL_DEPENDENCY],
            description=(
                "이 서버의 가용성 의존 부모 리소스 상태(정상/비정상)를 폴스타 DB에서 읽기전용"
                " 고정 SQL로 조회한다(연쇄 노이즈 판단용). 인자 없음."
            ),
        )

    tools = [_make_signature_tool(), _make_importance_tool()]
    if collect_dependency:
        tools.append(_make_dependency_tool())
    return tools


def _summarize(result: dict[str, Any]) -> str:
    """도구가 vLLM에 반환하는 요약 텍스트(원본은 collector에만 적재된다)."""
    signal = result.get("signal", "")
    if signal == SIGNAL_MESSAGE_SIGNATURE:
        sev = result.get("severity")
        if sev is None:
            return "메시지 시그니처: 장애 시그니처 없음"
        return f"메시지 시그니처: 상향 후보 심각도={sev} ({result.get('label') or ''})"
    ctx = result.get("context")
    if ctx is None:
        return f"{signal}: 수집 불가"
    return f"{signal}: {ctx}"


def build_noise_enricher_backend(config: AppConfig, tools: list[StructuredTool]):
    """트랙 B 백엔드: 오케스트레이터 LLM(vLLM)에 신호 수집 도구를 bind한다.

    vLLM 오케스트레이터는 네이티브 `bind_tools`(tool-calling)를 지원한다(FabriX는 미지원 —
    그래서 트랙 B는 vLLM 전제). 워커(FabriX)는 도구 *내부*에서만 쓰이며, 여기서는 오케스트레이터
    LLM만 조립한다.

    Args:
        config: 앱 설정 (orchestrator provider/base_url 참조).
        tools: build_noise_signal_tools가 만든 읽기전용 도구 목록.

    Returns:
        bind_tools가 적용된 Runnable(오케스트레이터 LLM).
    """
    from src.llm import create_orchestrator_llm

    llm = create_orchestrator_llm(config)
    return llm.bind_tools(tools)


async def run_signal_react_loop(
    bound_llm,  # noqa: ANN001 — bind_tools 결과(Runnable)
    tools: list[StructuredTool],
    *,
    system_prompt: str,
    user_prompt: str,
    max_tool_calls: int,
) -> int:
    """트랙 B 경량 ReAct 루프 — 도구 호출 상한(max_tool_calls)을 직접 제어한다.

    오케스트레이터가 tool_calls를 낼 때마다 해당 도구를 실행하고 ToolMessage로 회신하며,
    상한에 도달하거나 오케스트레이터가 더 이상 도구를 호출하지 않으면 종료한다. 도구 실행
    결과의 **원본**은 tools에 바인딩된 collector에 적재되므로(build_noise_signal_tools),
    호출측(enricher)은 ToolMessage가 아닌 collector 원본을 소비한다(Known Mistakes 2026-06-17).

    Args:
        bound_llm: build_noise_enricher_backend가 반환한 bind_tools LLM.
        tools: 실행 대상 도구 목록(이름으로 매칭).
        system_prompt: 신호 수집 보조자 역할 한정 system 프롬프트(판단 금지).
        user_prompt: 알람 메시지·컨텍스트 요약.
        max_tool_calls: 도구 호출 총 상한(ReAct 루프 폭주 방지).

    Returns:
        실제 실행한 도구 호출 수(관측·테스트용).
    """
    tools_by_name = {t.name: t for t in tools}
    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    calls = 0
    # 상한 + 1회의 최종 종료 판단 여유. calls 상한을 초과하지 않도록 이중 가드한다.
    for _ in range(max_tool_calls + 1):
        ai = await bound_llm.ainvoke(messages)
        messages.append(ai if isinstance(ai, BaseMessage) else AIMessage(content=str(ai)))
        tool_calls = list(getattr(ai, "tool_calls", None) or [])
        if not tool_calls:
            break
        for tc in tool_calls:
            if calls >= max_tool_calls:
                break
            tool = tools_by_name.get(tc.get("name"))
            if tool is None:
                messages.append(
                    ToolMessage(content="알 수 없는 도구", tool_call_id=tc.get("id", ""))
                )
                continue
            output = await tool.ainvoke(tc.get("args", {}) or {})
            calls += 1
            messages.append(
                ToolMessage(content=str(output), tool_call_id=tc.get("id", ""))
            )
        if calls >= max_tool_calls:
            break
    return calls


def extract_escalation_candidate(collector: Optional[list]) -> Optional[tuple[int, str]]:
    """collector 원본에서 최고 상향 심각도 후보(message_signature)를 추출한다.

    트랙 A/B가 적재한 신호 dict 중 `signal == "message_signature"` 항목의 severity를 모아
    **최고값**을 (심각도, 라벨)로 반환한다. 없으면 None. (승격 전용 — 판단은 하지 않는다.)
    """
    best: Optional[tuple[int, str]] = None
    for item in collector or []:
        if not isinstance(item, dict) or item.get("signal") != SIGNAL_MESSAGE_SIGNATURE:
            continue
        sev = item.get("severity")
        if isinstance(sev, int) and (best is None or sev > best[0]):
            best = (sev, str(item.get("label") or "메시지 시그니처"))
    return best
