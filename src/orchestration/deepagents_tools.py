"""deepagents 도구 래퍼 (Plan 49, 트랙 B — vLLM 오케스트레이터 + FabriX 워커).

기존 `SUBAGENT_REGISTRY` handler를 LangChain `@tool`로 노출한다. vLLM 오케스트레이터는
이 도구들을 tool-calling으로 호출하고, 도구 **내부**에서는 FabriX(KBGenAIChat) 파이프라인이
실제 작업(자연어→SQL→DB 조회→결과 정리)을 수행한다 → FabriX는 tool-calling을 강요받지 않는다.

신규 비즈니스 로직은 추가하지 않으며, handler 호출은 `agent_orchestrator._run_agent`와 동형이다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from src.config import AppConfig
from src.orchestration.intent_planner import has_alarm_signal
from src.orchestration.subagents import (
    SUBAGENT_REGISTRY,
    _extract_identity_rows,
    _make_isolated_input,
)

logger = logging.getLogger(__name__)

# ── 선행 결과 스코프 주입 게이트 (D-095 — 결정적) ──
# deepagents 경로는 planner의 input_from이 없어 선행 결과 전파가 오케스트레이터의
# sub_query 작성 품질에 의존했다(D-094 잔여). 아래 게이트로 D-086 prior_rows 주입을 배선한다.
# 전역 범위 명시 어휘 — 있으면 선행 스코프를 주입하지 않는다(독립 전역 조회 보호).
_GLOBAL_SCOPE_MARKERS = ("전체", "모든", "전 서버")
# 선행 결과 참조/선별 어휘 — 있으면 주입.
_PRIOR_REF_MARKERS = (
    "해당", "그 서버", "이 서버", "위 서버", "이들", "앞서", "선행", "선별", "조회된",
    "중에서", "these", "among",
)
# 순위/최상급 어휘 — 복합 질의의 후속 조회에서 "선별 집합 내 순위" 신호
# (2026-07-20 라이브 오답 실측: "6월 CPU 최고 서버 조회"가 전 서버 기준 SQL로 생성).
_RANKING_MARKERS = (
    "가장", "최고", "최대", "최소", "최상위", "상위", "하위",
    "highest", "lowest", "top",
)

# 도구 반환 시 포함할 최대 행 수 (토큰 폭증 방지 — R-B6/R-12와 동일 취지)
_MAX_TOOL_ROWS = 50
# organized_data/query_results가 없는 결과의 직렬화 길이 상한(폴백 — config 미전달 시)
_MAX_RAW_CHARS = 4000
# 토큰 → 문자수 보수 근사 계수 (Plan 50 §3.5(3): 정밀 토크나이저 대신 chars≈tokens*4)
_CHARS_PER_TOKEN = 4

# agent 식별자 → 도구 이름 (vLLM에 노출되는 함수명)
_TOOL_NAMES: dict[str, str] = {
    "data_query": "query_infra_db",
    "process_query": "query_live_processes",
    "alarm_query": "query_alarm",
    "cache_management": "manage_cache",
    "synonym_registration": "register_synonym",
    "general_inference": "general_answer",
    # Plan 78 W3-4 — 동사+목적어. 이름 정렬만으로 호출 오류가 크게 준다(arXiv 2510.07248).
    "host_inspect": "inspect_host",
}


def _max_chars(app_config: Optional[AppConfig]) -> int:
    """제어 평면 도구 결과 1건의 최대 문자수를 산정한다 (Plan 50 B1/B2).

    OrchestratorConfig.max_tool_result_tokens(토큰) × _CHARS_PER_TOKEN(보수 근사)로
    문자 상한을 구한다. config 미전달 시 정적 폴백(_MAX_RAW_CHARS)을 사용한다.

    Args:
        app_config: 앱 설정 (None이면 폴백 상한)

    Returns:
        직렬화 결과 텍스트의 최대 문자수
    """
    if app_config is None:
        return _MAX_RAW_CHARS
    return max(1, app_config.orchestrator.max_tool_result_tokens * _CHARS_PER_TOKEN)


def _truncate(text: str, limit: int) -> str:
    """텍스트를 상한 내로 자르고 초과 시 축소 표시를 붙인다."""
    if len(text) <= limit:
        return text
    return text[:limit] + "…(축소됨)"


def _serialize_for_tool(result: Any, app_config: Optional[AppConfig] = None) -> str:
    """handler 반환 dict를 도구 반환용 텍스트(JSON)로 직렬화한다.

    organized_data.summary + 행수 + 상한 적용 행을 담는다. 실패 시 오류 텍스트,
    조회형이 아닌 결과는 dict 전체를 길이 상한 내에서 직렬화한다.

    이 함수의 출력은 **제어 평면(vLLM)에 ToolMessage로 노출되는 요약본**이며,
    OrchestratorConfig.max_tool_result_tokens(B1)를 chars 근사로 환산한 상한을 넘으면
    truncate + "…(축소됨)" 표시한다. 행 상한(_MAX_TOOL_ROWS)도 함께 적용한다.
    원본 결과는 collector에만 적재되며(축소 미적용) 최종 응답 생성에 사용된다.

    Args:
        result: subagent handler 반환값
        app_config: 앱 설정 (제어 평면 토큰 예산 산정용 — 없으면 정적 폴백)

    Returns:
        vLLM 오케스트레이터에 ToolMessage로 전달할 텍스트
    """
    limit = _max_chars(app_config)
    if not isinstance(result, dict):
        return _truncate(str(result), limit)
    if result.get("error"):
        return _truncate(f"[실패] {result['error']}", limit)

    organized = result.get("organized_data") or {}
    rows = organized.get("rows")
    if rows is None:
        rows = result.get("query_results")

    # 조회형(행 데이터)이 아니면 결과 dict 전체를 상한 내에서 직렬화
    if not organized and rows is None:
        return _truncate(json.dumps(result, ensure_ascii=False, default=str), limit)

    rows = rows or []
    total = len(rows) if isinstance(rows, list) else 0
    capped = rows[:_MAX_TOOL_ROWS] if isinstance(rows, list) else rows
    payload = {
        "summary": organized.get("summary") or "",
        "row_count": total,
        "rows": capped,
        "truncated": isinstance(rows, list) and total > _MAX_TOOL_ROWS,
    }
    return _truncate(json.dumps(payload, ensure_ascii=False, default=str), limit)


# 선행 결과를 후속 task의 스코프로 넘길 수 있는 agent (Plan 78 W1-1·W1-2).
# 생산자·소비자를 같은 집합으로 두는 이유: 조회형 결과는 어느 쪽으로도 흐를 수 있다
# (프로세스 조회 결과 → 원인 분석 대상 등). 실제 소비 형태 분기는 _make_isolated_input이 한다.
_SCOPE_PRODUCER_AGENTS: tuple[str, ...] = (
    "data_query", "alarm_query", "process_query", "fault_diagnosis",
)
_SCOPE_CONSUMER_AGENTS: tuple[str, ...] = _SCOPE_PRODUCER_AGENTS


def _dependency_scope(sub_query: str, collector: list) -> tuple[list[str], dict]:
    """후속 조회 task에 주입할 선행 결과 의존(input_from/prior)을 결정한다 (D-095).

    결정적 게이트 중 하나라도 충족하면 선행 조회 결과를 D-086 prior_rows 경로로 주입해
    SQL 대상 스코프를 강제한다:
    - G1 값 일치: sub_query에 선행 결과의 식별 값(서버명 등)이 등장 — SQL 재생성 시
      필터 탈락 방지(강화 주입).
    - G2 참조/선별 어휘: "해당/그 서버/앞서/선별…" — 선행 결과 참조 신호.
    - G3 순위/최상급 어휘: "가장/최고/상위…" — 복합 질의에서 선행 선별 집합 내 순위
      산정 신호.
    단, sub_query가 전역 범위를 명시(_GLOBAL_SCOPE_MARKERS)하면 주입하지 않는다.

    Args:
        sub_query: 현재 도구의 자연어 지시
        collector: 지금까지 실행된 [(task, result), ...]

    Returns:
        (input_from task_id 목록, {task_id: 원본 결과}) — 게이트 미충족 시 ([], {})
    """
    low = (sub_query or "").lower()
    if any(m in low for m in _GLOBAL_SCOPE_MARKERS):
        return [], {}

    # 선행 후보: 조회형 agent의 성공 결과 중 행이 있는 것만
    candidates: list[tuple[dict, dict, list]] = []
    for t, res in collector:
        # Plan 78 W1-1: 생산자 후보에 process_query·fault_diagnosis를 추가한다 —
        # 조회형 결과면 후속 task의 스코프가 될 수 있다. 프로세스 행(pid 보유)이
        # 서버 대상으로 오인되는 것은 build_prior_targets의 결정적 가드가 막는다.
        if t.get("agent") not in _SCOPE_PRODUCER_AGENTS:
            continue
        if not isinstance(res, dict) or res.get("error"):
            continue
        organized = res.get("organized_data") or {}
        rows = organized.get("rows") or res.get("query_results") or []
        if rows:
            candidates.append((t, res, rows))
    if not candidates:
        return [], {}

    ref_hit = any(m in low for m in _PRIOR_REF_MARKERS)
    rank_hit = any(m in low for m in _RANKING_MARKERS)
    value_hit = False
    if not (ref_hit or rank_hit):
        for _, _, rows in candidates:
            for row in _extract_identity_rows(rows):
                if not isinstance(row, dict):
                    continue
                if any(
                    isinstance(v, str) and len(v) >= 3 and v.lower() in low
                    for v in row.values()
                ):
                    value_hit = True
                    break
            if value_hit:
                break
    if not (ref_hit or rank_hit or value_hit):
        return [], {}

    input_from: list[str] = []
    prior: dict = {}
    for t, res, _ in candidates:
        tid = t.get("task_id")
        if tid and tid not in prior:
            input_from.append(tid)
            prior[tid] = res
    return input_from, prior


async def _run_subagent_tool(
    agent_name: str,
    sub_query: str,
    *,
    worker_llm: BaseChatModel,
    app_config: AppConfig,
    ambient_state: dict,
    collector: Optional[list] = None,
) -> str:
    """단일 subagent handler를 도구로 실행하고 결과를 직렬화한다.

    `agent_orchestrator._run_agent`와 동일한 호출 규약(task 구성 → isolated 입력 →
    handler 호출)을 따른다. FabriX는 handler 내부에서만 호출된다.

    `collector`가 주어지면 **truncate 전 원본 handler 결과**를 (task, result) 튜플로
    적재한다(Plan §4.4 — 최종 FabriX 응답 생성용). 도구가 vLLM에 반환하는 텍스트는
    토큰 폭증 방지를 위해 요약/상한이 적용되지만, 최종 응답은 원본 결과로 생성한다.

    Args:
        agent_name: SUBAGENT_REGISTRY 키 (없으면 fallback)
        sub_query: vLLM이 전달한 자연어 지시
        worker_llm: FabriX 워커 LLM
        app_config: 앱 설정
        ambient_state: thread_id/user_id/allowed_db_ids 등 주변 컨텍스트
        collector: (선택) 원본 결과 수집기 — [(task, result), ...]

    Returns:
        직렬화된 결과 텍스트
    """
    # LLM 오케스트레이터가 알람/이벤트(event) 질의를 data_query 도구로 보내는 오분류를
    # 결정적으로 교정한다(D-076 후속3 — D-047 프로세스 교정과 동일 패턴. 도구 설명·지시문
    # 어휘 보강만으로는 bare "event" 질의에서 재발). alarm_query여야 알람 템플릿·테이블이 열린다.
    if agent_name == "data_query" and has_alarm_signal(sub_query):
        logger.info(
            "deepagents 도구 결정적 교정 — data_query→alarm_query (sub_query=%r)", sub_query
        )
        agent_name = "alarm_query"
    spec = SUBAGENT_REGISTRY.get(agent_name) or _fallback_spec()
    order = (len(collector) + 1) if collector is not None else 1
    # 선행 결과 스코프 결정적 주입(D-095): 게이트 충족 시 D-086 prior_rows 경로 배선.
    input_from: list[str] = []
    prior: dict[str, Any] = {}
    # Plan 78 W1-2: 소비 방식이 agent별로 다르다 —
    #   data_query/alarm_query   → prior_rows(SQL 스코프, D-086)
    #   process_query/fault_diagnosis → prior_targets(대상 집합) — _make_isolated_input이 조립
    if collector and agent_name in _SCOPE_CONSUMER_AGENTS:
        input_from, prior = _dependency_scope(sub_query, collector)
        if input_from:
            logger.info(
                "deepagents 도구 선행 스코프 주입(D-095): input_from=%s (sub_query=%r)",
                input_from, sub_query,
            )
    task: dict[str, Any] = {
        "task_id": f"tool_{agent_name}_{order}",
        "agent": agent_name,
        "sub_query": sub_query,
        "depends_on": [],
        "input_from": input_from,
        "order": order,
        "status": "pending",
    }
    isolated = _make_isolated_input(task, ambient_state, prior=prior)
    isolated["user_query"] = sub_query
    result = await spec.handler(
        task, isolated, llm=spec.model or worker_llm, app_config=app_config
    )
    if collector is not None:
        task["status"] = "failed" if isinstance(result, dict) and result.get("error") else "completed"
        # collector에는 truncate 전 원본 결과를 적재한다(B1 — 최종 응답은 원본 기반).
        collector.append((task, result))
    # vLLM에 반환하는 텍스트만 제어 평면 토큰 예산(B1)으로 축소한다.
    return _serialize_for_tool(result, app_config)


def _fallback_spec():
    """fallback(general-purpose) subagent 스펙을 반환한다."""
    for spec in SUBAGENT_REGISTRY.values():
        if spec.fallback:
            return spec
    return SUBAGENT_REGISTRY["general_inference"]


def build_tools(
    worker_llm: BaseChatModel,
    app_config: AppConfig,
    ambient_state: Optional[dict] = None,
    collector: Optional[list] = None,
) -> list[StructuredTool]:
    """SUBAGENT_REGISTRY를 vLLM 오케스트레이터용 도구 목록으로 변환한다.

    Args:
        worker_llm: FabriX 워커 LLM (도구 내부 실행에 사용)
        app_config: 앱 설정
        ambient_state: 주변 컨텍스트(thread_id/user_id 등). 없으면 빈 dict.
        collector: (선택) 원본 결과 수집기 — 도구 실행마다 (task, result)를 적재하여
            최종 FabriX 응답 생성(result_aggregator)에 사용한다(Plan §4.4/§4.3 step6).

    Returns:
        LangChain StructuredTool 목록 (deepagents create_deep_agent의 tools 인자)
    """
    ambient = ambient_state or {}
    tools: list[StructuredTool] = []
    # **목록은 고정한다**(78 P14): 조사 경로가 비활성이어도 도구를 빼지 않는다 —
    # 도구 정의는 컨텍스트 접두부라 목록이 흔들리면 이후 전 턴의 KV 캐시가 무효화된다.
    # 가용성 제어는 handler 진입부 게이트가 담당한다(`run_host_inspect` — 구조화 거부).
    for agent_name, spec in SUBAGENT_REGISTRY.items():
        tool_name = _TOOL_NAMES.get(agent_name, agent_name)

        def _make_coroutine(name: str):
            async def _run(sub_query: str) -> str:
                return await _run_subagent_tool(
                    name,
                    sub_query,
                    worker_llm=worker_llm,
                    app_config=app_config,
                    ambient_state=ambient,
                    collector=collector,
                )
            return _run

        tools.append(
            StructuredTool.from_function(
                coroutine=_make_coroutine(agent_name),
                name=tool_name,
                description=spec.description,
            )
        )
    return tools
