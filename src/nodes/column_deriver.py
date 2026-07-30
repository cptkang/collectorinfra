"""단계적 컬럼 도출 루프 (Plan 67 Phase S2 / D-128).

트랙 C(D-076)의 "1방 SMQ 선택"을 **도구 기반 다회 탐색**으로 확장한다. 흐름은 계획서
§3.1 그대로다:

    [1] 요구 분해(LLM 1회, 도구 없음) — 질의를 필요 필드 단위로 쪼갠다.
    [2] 필드별 컬럼 도출 루프(LLM + tools 다회) — 필드마다 카탈로그·유사어·값 인덱스를
        **확인한 뒤** dimension/measure/filter를 골라 SMQ에 누적하고, 근거·확신도를 남긴다.

역할 경계(D-035·D-067·D-076 정합):
    - 이 모듈은 **선택만** 만든다. 커버리지 판정(`check_coverage`)과 SQL 조립(`compile_smq`)은
      기존 결정적 경로가 그대로 담당하며 여기서 손대지 않는다.
    - 산출 SMQ는 호출부(`semantic_compiler.compile_from_nl`)가 기존 `SMQ.from_dict` +
      `normalize_smq` 경로로 통과시켜 검증한다(새 검증 경로 신설 금지 — D-067).
    - 해소하지 못한 필드는 **구조화 사유와 함께** 반환한다. 침묵 폴백 금지 — 호출부가 사유를
      로그·state에 남기고 기존 3단 폴백으로 넘긴다.

루프 실행 기반은 deepagents 서브에이전트가 아니라 LangChain ``bind_tools`` + 자체 while
루프다(계획서 §2.3): built-in tool 추가 유입·서브에이전트 경계의 상태 유실을 피하고,
반복 상한·전체 타임아웃·감사 로그를 결정적 가드로 직접 통제하기 위함이다.

계층: application(nodes) — prompts·tools(도구 계층)·utils 참조.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from src.utils.json_extract import extract_json_from_response

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 루프 실행 시점에 지연 수행한다.
    from src.config import Text2SQLConfig
    from src.db.interface import DBClient
    from src.tools.binding import ToolContext
    from src.tools.schema_probe import RowMasker

logger = logging.getLogger(__name__)

#: 루프 종료 사유. completed 외에는 전부 "결론을 못 냈다"는 신호이며 폴백 사유로 노출된다.
STOP_COMPLETED = "completed"
STOP_MAX_ROUNDS = "max_rounds"
STOP_MAX_TOOL_CALLS = "max_tool_calls"
STOP_TIMEOUT = "timeout"
STOP_NO_TOOLS = "no_tools"
STOP_TOOL_BINDING_UNSUPPORTED = "tool_binding_unsupported"
STOP_PARSE_ERROR = "parse_error"
STOP_LLM_ERROR = "llm_error"

#: 미해결 사유 항목의 필드명 자리 표시자(특정 필드가 아니라 루프 전체가 실패한 경우).
_WHOLE_QUERY_FIELD = "*"


@dataclass
class StepwiseDeps:
    """경로별로 가용한 도구 주입 재료 (D-066 대칭 주입 단위).

    SQL 생성 경로마다 손에 있는 재료가 다르다(단일 경로는 state의 유사어·값 인덱스까지,
    멀티 DB 경로는 스키마·엔진까지). 없는 재료는 해당 도구를 목록에서 빼는 신호이며,
    루프는 남은 도구만으로 진행한다 — 어느 경로든 **같은 함수**를 통과하는 것이 요점이다.
    """

    path: str = ""                                    # 발동 경로 라벨("single"|"multi_db")
    synonyms: dict[str, list[str]] = field(default_factory=dict)
    synonym_meta: dict[str, dict] = field(default_factory=dict)
    value_index: Optional[dict[str, list[str]]] = None
    schema_info: dict = field(default_factory=dict)
    db_engine: str = "postgresql"
    adapter_db_ids: Optional[set[str]] = None
    default_limit: int = 100
    db_client: Optional["DBClient"] = None             # 실 스키마·샘플 프로브(가용 시)
    row_masker: Optional["RowMasker"] = None
    synonym_min_score: float = 0.85
    value_fuzzy: bool = False


@dataclass
class StepwiseLimits:
    """루프 상한. 설정(`cfg.text2sql.stepwise_*`)에서 만든다."""

    max_rounds: int = 6
    max_tool_calls: int = 24
    timeout_seconds: float = 60.0

    @classmethod
    def from_config(cls, text2sql: "Text2SQLConfig") -> "StepwiseLimits":
        """Text2SQLConfig에서 상한을 만든다."""
        return cls(
            max_rounds=max(1, int(getattr(text2sql, "stepwise_max_rounds", 6))),
            max_tool_calls=max(1, int(getattr(text2sql, "stepwise_max_tool_calls", 24))),
            timeout_seconds=float(getattr(text2sql, "stepwise_timeout_seconds", 60.0)),
        )


@dataclass
class _Progress:
    """루프 진행 상태 — 타임아웃으로 중단돼도 부분 계측을 보고하기 위한 누적기."""

    rounds: int = 0
    tool_calls: int = 0
    llm_calls: int = 0
    smq: Optional[dict] = None
    fields: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    stopped_reason: str = STOP_MAX_ROUNDS
    tool_names: list[str] = field(default_factory=list)


def build_tool_context(
    db_id: str, model: dict, user_query: str, deps: StepwiseDeps
) -> "ToolContext":
    """S1 도구 계층의 ``ToolContext``를 경로 재료로 조립한다.

    Args:
        db_id: 대상 DB 식별자
        model: 시맨틱 모델(카탈로그)
        user_query: 원문 질의(기간·상한 해석 도구의 기본 입력)
        deps: 경로별 주입 재료

    Returns:
        ``src.tools.binding.ToolContext`` 인스턴스
    """
    from src.tools.binding import ToolContext

    return ToolContext(
        semantic_model=model,
        synonyms=deps.synonyms or {},
        synonym_meta=deps.synonym_meta or {},
        value_index=deps.value_index or {},
        schema_info=deps.schema_info or {},
        db_client=deps.db_client,
        row_masker=deps.row_masker,
        db_id=db_id,
        adapter_db_ids=deps.adapter_db_ids,
        db_engine=deps.db_engine or "postgresql",
        user_query=user_query,
        default_limit=deps.default_limit,
        synonym_min_score=deps.synonym_min_score,
        value_fuzzy=deps.value_fuzzy,
    )


async def derive_smq(
    llm: Any,
    user_query: str,
    db_id: str,
    model: dict,
    *,
    deps: Optional[StepwiseDeps] = None,
    limits: Optional[StepwiseLimits] = None,
    scope_note: str = "",
) -> dict:
    """단계적 컬럼 도출 루프를 실행하고 관측 레코드를 반환한다.

    상한(라운드·tool 호출)과 **전체 타임아웃**을 모두 적용한다 — per-call 타임아웃만으로는
    다회 왕복을 막지 못한다(Known Mistakes). 중단돼도 그 시점까지의 계측·미해결 사유를
    담아 반환하며, 예외를 호출부로 전파하지 않는다(폴백 판단은 호출부 몫).

    Args:
        llm: tool-calling 가능한 LLM(bind_tools 지원)
        user_query: 사용자 원문 질의
        db_id: 대상 DB 식별자
        model: 시맨틱 모델(카탈로그)
        deps: 경로별 도구 주입 재료
        limits: 루프 상한(없으면 기본값)
        scope_note: 선행 스코프 예외 블록(D-099 ⑤) — 있으면 도출 프롬프트에 덧붙인다.
            빈 문자열이면 프롬프트 바이트 불변(1방 경로의 SCOPE_NOTE 주입과 대칭, Plan 69 P0-⑩)

    Returns:
        ``state.SmqDerivation`` 형태 dict — smq가 None이거나 unresolved가 비지 않으면
        호출부가 폴백해야 한다는 뜻이다.
    """
    deps = deps or StepwiseDeps()
    limits = limits or StepwiseLimits()
    progress = _Progress()
    started = time.monotonic()

    try:
        await asyncio.wait_for(
            _run_loop(llm, user_query, db_id, model, deps, limits, progress,
                      scope_note=scope_note),
            timeout=limits.timeout_seconds,
        )
    except asyncio.TimeoutError:
        progress.stopped_reason = STOP_TIMEOUT
        progress.smq = None
        progress.unresolved.append({
            "field": _WHOLE_QUERY_FIELD,
            "reason": f"도출 루프 전체 타임아웃({limits.timeout_seconds}초 초과)",
        })
        logger.warning(
            "[단계적도출] 타임아웃 — path=%s db=%s 라운드=%d tool=%d",
            deps.path, db_id, progress.rounds, progress.tool_calls,
        )
    except Exception as e:  # noqa: BLE001 — 루프 실패는 사유와 함께 폴백으로 강등
        progress.stopped_reason = STOP_LLM_ERROR
        progress.smq = None
        progress.unresolved.append({
            "field": _WHOLE_QUERY_FIELD, "reason": f"도출 루프 실패: {e}",
        })
        logger.warning("[단계적도출] 실패(폴백) — path=%s db=%s: %s", deps.path, db_id, e)

    elapsed_ms = (time.monotonic() - started) * 1000.0
    record: dict = {
        "path": deps.path,
        "db_id": db_id,
        "smq": progress.smq,
        "fields": progress.fields,
        "unresolved": progress.unresolved,
        "rounds": progress.rounds,
        "tool_calls": progress.tool_calls,
        "llm_calls": progress.llm_calls,
        "elapsed_ms": round(elapsed_ms, 1),
        "stopped_reason": progress.stopped_reason,
        "covered": None,
        # 커버리지·컴파일 단계에서 발동한 교정 가드가 여기에 귀속 기록된다(R4 — 호출부가 채운다).
        "guards": {},
    }
    # 관측(S3 평가 재료): 경로·라운드·tool 호출·미해결·소요 시간을 한 줄로 남긴다.
    logger.info(
        "[단계적도출] path=%s db=%s 사유=%s 라운드=%d tool=%d llm=%d %.0fms "
        "필드=%d 미해결=%d 도구=%s",
        deps.path, db_id, progress.stopped_reason, progress.rounds,
        progress.tool_calls, progress.llm_calls, elapsed_ms,
        len(progress.fields), len(progress.unresolved),
        ",".join(progress.tool_names) or "없음",
    )
    if progress.unresolved:
        logger.info(
            "[단계적도출] 미해결 필드 — %s",
            "; ".join(
                f"{u.get('field')}: {u.get('reason')}" for u in progress.unresolved
            ),
        )
    return record


async def _run_loop(
    llm: Any,
    user_query: str,
    db_id: str,
    model: dict,
    deps: StepwiseDeps,
    limits: StepwiseLimits,
    progress: _Progress,
    scope_note: str = "",
) -> None:
    """[1]요구 분해 + [2]도구 루프를 수행하고 결과를 progress에 누적한다."""
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

    from src.prompts.column_deriver import (
        DERIVE_FINALIZE_NOTE,
        DERIVE_SYSTEM_TEMPLATE,
        DERIVE_USER_TEMPLATE,
    )
    from src.tools.binding import build_query_tools

    tools = build_query_tools(build_tool_context(db_id, model, user_query, deps))
    if not tools:
        progress.stopped_reason = STOP_NO_TOOLS
        progress.unresolved.append({
            "field": _WHOLE_QUERY_FIELD,
            "reason": "도출에 쓸 도구가 없다(카탈로그·사전 미주입)",
        })
        return
    progress.tool_names = [t.name for t in tools]

    try:
        bound = llm.bind_tools(tools)
    except Exception as e:  # noqa: BLE001 — tool-calling 미지원 LLM은 폴백으로 강등
        progress.stopped_reason = STOP_TOOL_BINDING_UNSUPPORTED
        progress.unresolved.append({
            "field": _WHOLE_QUERY_FIELD, "reason": f"LLM tool-calling 미지원: {e}",
        })
        return

    # [1] 요구 분해 — 도구 없이 1회. 실패해도 루프는 원문 질의로 진행한다(사유는 남긴다).
    decomposed = await _decompose(llm, user_query, progress)

    tools_by_name = {t.name: t for t in tools}
    deadline = time.monotonic() + limits.timeout_seconds
    messages: list[Any] = [SystemMessage(content=DERIVE_SYSTEM_TEMPLATE)]
    # KBGenAIChat 등은 SystemMessage 다음 AIMessage 순서를 요구(compile_from_nl과 동일 규약).
    if type(llm).__name__ == "KBGenAIChat":
        from langchain_core.messages import AIMessage

        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=DERIVE_USER_TEMPLATE.format(
        user_query=user_query, fields=_render_fields(decomposed),
    ) + scope_note))

    finalize_sent = False
    while progress.rounds < limits.max_rounds:
        if time.monotonic() >= deadline:
            # wait_for가 잡지 못하는 경우(동기 도구가 블로킹)를 위한 내부 마감 확인.
            progress.stopped_reason = STOP_TIMEOUT
            progress.unresolved.append({
                "field": _WHOLE_QUERY_FIELD,
                "reason": f"도출 루프 전체 타임아웃({limits.timeout_seconds}초 초과)",
            })
            return
        progress.rounds += 1
        response = await bound.ainvoke(messages)
        progress.llm_calls += 1
        tool_calls = list(getattr(response, "tool_calls", None) or [])

        if not tool_calls:
            _consume_final(response, progress)
            return

        messages.append(response)
        budget_exhausted = False
        for call in tool_calls:
            if progress.tool_calls >= limits.max_tool_calls:
                budget_exhausted = True
                messages.append(ToolMessage(
                    content=json.dumps(
                        {"error": "tool 호출 한도 도달 — 추가 호출 없이 결론을 내라"},
                        ensure_ascii=False,
                    ),
                    tool_call_id=str(call.get("id") or ""),
                ))
                continue
            progress.tool_calls += 1
            messages.append(ToolMessage(
                content=await _invoke_tool(tools_by_name, call),
                tool_call_id=str(call.get("id") or ""),
            ))

        if budget_exhausted and not finalize_sent:
            # 한도 도달을 명시하고 마지막 1라운드로 결론을 받는다(조용한 중단 금지).
            finalize_sent = True
            progress.stopped_reason = STOP_MAX_TOOL_CALLS
            messages.append(HumanMessage(content=DERIVE_FINALIZE_NOTE))
            logger.info(
                "[단계적도출] tool 호출 한도 도달(%d) — 마감 지시 후 결론 요청",
                limits.max_tool_calls,
            )

    # while 조건 소진 = 라운드 상한. 한도 사유가 이미 잡혀 있으면 그것을 유지한다.
    if progress.stopped_reason not in (STOP_MAX_TOOL_CALLS,):
        progress.stopped_reason = STOP_MAX_ROUNDS
    progress.unresolved.append({
        "field": _WHOLE_QUERY_FIELD,
        "reason": (
            f"도출 상한 도달(라운드 {progress.rounds}/{limits.max_rounds}, "
            f"tool {progress.tool_calls}/{limits.max_tool_calls}) — 결론 미도달"
        ),
    })
    progress.smq = None


async def _decompose(llm: Any, user_query: str, progress: _Progress) -> list[dict]:
    """[1] 요구 분해 — 질의를 필드 단위로 쪼갠다(도구 없이 1회 호출).

    Args:
        llm: LLM 인스턴스(도구 미바인딩)
        user_query: 사용자 원문 질의
        progress: 호출 수·사유 누적기

    Returns:
        [{field, role_hint}] 목록(실패 시 빈 목록 — 루프는 원문으로 진행)
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.prompts.column_deriver import (
        DECOMPOSE_SYSTEM_TEMPLATE,
        DECOMPOSE_USER_TEMPLATE,
    )

    messages: list[Any] = [SystemMessage(content=DECOMPOSE_SYSTEM_TEMPLATE)]
    if type(llm).__name__ == "KBGenAIChat":
        from langchain_core.messages import AIMessage

        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(
        content=DECOMPOSE_USER_TEMPLATE.format(user_query=user_query)
    ))

    try:
        response = await llm.ainvoke(messages)
    except Exception as e:  # noqa: BLE001 — 분해 실패는 루프 진행을 막지 않는다(사유 로그)
        logger.warning("[단계적도출] 요구 분해 실패(원문으로 진행): %s", e)
        return []
    progress.llm_calls += 1

    data = extract_json_from_response(_message_text(response))
    if not isinstance(data, dict):
        logger.info("[단계적도출] 요구 분해 응답에서 JSON을 찾지 못함(원문으로 진행)")
        return []
    fields = [f for f in (data.get("fields") or []) if isinstance(f, dict) and f.get("field")]
    logger.info(
        "[단계적도출] 요구 분해 %d필드: %s",
        len(fields), ", ".join(str(f.get("field")) for f in fields) or "없음",
    )
    return fields


def _render_fields(fields: list[dict]) -> str:
    """분해된 필드를 루프 프롬프트용 목록 텍스트로 렌더한다."""
    if not fields:
        return "- (분해 결과 없음 — 질의 원문에서 직접 판단하라)"
    return "\n".join(
        f"- {f.get('field')} (역할 추정: {f.get('role_hint') or '미정'})" for f in fields
    )


async def _invoke_tool(tools_by_name: dict[str, Any], call: dict) -> str:
    """tool_call 1건을 실행해 결과 문자열을 만든다(실패는 사유를 그대로 노출)."""
    name = str(call.get("name") or "")
    args = call.get("args")
    if not isinstance(args, dict):
        args = {}
    tool = tools_by_name.get(name)
    if tool is None:
        logger.warning("[단계적도출] 미등록 도구 호출: %s", name)
        return json.dumps(
            {"error": f"등록되지 않은 도구: {name}", "available": sorted(tools_by_name)},
            ensure_ascii=False,
        )
    try:
        result = await tool.ainvoke(args)
    except Exception as e:  # noqa: BLE001 — 도구 실패 사유를 LLM에 돌려 재시도 여지를 준다
        logger.warning("[단계적도출] 도구 실패(%s): %s", name, e)
        return json.dumps({"error": f"도구 실패({name}): {e}"}, ensure_ascii=False)
    return result if isinstance(result, str) else json.dumps(
        result, ensure_ascii=False, default=str
    )


def _message_text(response: Any) -> str:
    """AI 메시지 content를 텍스트로 정규화한다.

    실 모델(Gemini 등)은 content를 콘텐츠 블록 리스트로 반환할 수 있다 — str 가정 시
    정규식 파서가 TypeError로 죽는다(2026-07-30 라이브 스모크 실측, 목은 str만 반환해 미검출).
    deep_agent._message_text와 동일 관행(지점별 정규화).
    """
    content = getattr(response, "content", "") or ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def _consume_final(response: Any, progress: _Progress) -> None:
    """도구 호출 없는 응답을 최종 산출물로 해석해 progress에 반영한다."""
    data = extract_json_from_response(_message_text(response))
    if not isinstance(data, dict):
        progress.stopped_reason = STOP_PARSE_ERROR
        progress.unresolved.append({
            "field": _WHOLE_QUERY_FIELD,
            "reason": "최종 응답에서 SMQ JSON을 찾지 못함",
        })
        return

    progress.fields = [f for f in (data.get("fields") or []) if isinstance(f, dict)]
    progress.unresolved.extend(
        u for u in (data.get("unresolved") or []) if isinstance(u, dict)
    )

    smq = data.get("smq")
    if not isinstance(smq, dict):
        # 관용: 래핑 없이 SMQ 본문만 낸 경우도 받아들인다(패턴 키로 판정).
        smq = data if str(data.get("pattern", "")).upper() in ("A", "B", "C") else None
    if smq is None:
        progress.stopped_reason = STOP_COMPLETED
        if not progress.unresolved:
            progress.unresolved.append({
                "field": _WHOLE_QUERY_FIELD,
                "reason": "루프가 조립 가능한 SMQ를 도출하지 못했다(smq=null)",
            })
        return

    progress.smq = smq
    progress.stopped_reason = STOP_COMPLETED
