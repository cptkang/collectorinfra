"""사용자 의사 확인(유사어 등록·SQL 승인) LLM 분류기 (Plan 67 R3-(ii)).

표면어 정규식이 변형을 못 따라가 정반대로 해석되던 두 지점을 LLM 분류로 보조한다
(`docs/regex_llm_conversion_review.md` A11·A12). 두 함수 모두:

- **호출부의 결정적 판정이 확정하지 못한 입력만** 넘어온다(정규식·단독 표현 판정이 1순위).
- 실패·타임아웃·형식 오류·`unclear`는 전부 **None**을 반환한다 — 호출부가 재질의(등록)나
  거부(승인)로 처리하도록 강제해, LLM 비결정성이 침묵 오답이 되지 않게 한다.
- 승인(HITL) 판정은 fail-closed를 유지한다(D-130): 고신뢰 `approve`만 승인으로 인정한다.

계층상 infrastructure에 두는 이유: 소비처가 application 노드(`synonym_registrar`)와
interface 라우트(`api/routes/query.py`) 양쪽이고, interface는 `src.prompts`를 직접 참조할 수
없다(`scripts/arch_check.py` ALLOWED_DEPS["interface"]에 prompts 없음).
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.prompts.intent_confirm import (
    APPROVAL_INTENT_SYSTEM_PROMPT,
    REGISTRATION_INTENT_SYSTEM_PROMPT,
)
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

#: 의사 분류 1콜 전체 상한(초). 승인·등록 응답 대기 중 사용자를 붙잡아 두는 경로라 짧게 잡는다.
CLASSIFY_TIMEOUT_SECONDS: float = 15.0

#: 승인으로 인정할 최소 확신도(코드 상수 — 프롬프트·설정으로 낮출 수 없다).
#: 미만은 unclear와 동일하게 취급한다(fail-closed — D-130). 호출부는 이 판정만으로 실행하지
#: 않고 결정적 보강 신호를 한 번 더 요구한다(`api/routes/query.py::_has_approval_token`).
APPROVAL_MIN_CONFIDENCE: float = 0.8

_REGISTRATION_MODES = ("all", "selective", "skip")
_APPROVAL_INTENTS = ("approve", "reject", "modify")


async def _classify(
    llm: BaseChatModel, system_prompt: str, user_query: str
) -> dict | None:
    """분류 프롬프트 1콜을 실행해 JSON dict를 반환한다(실패·타임아웃·형식오류는 None)."""
    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_query))
    try:
        response = await asyncio.wait_for(
            llm.ainvoke(messages), timeout=CLASSIFY_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.warning("의사 분류 LLM 타임아웃(%.1fs) — 결정적 폴백", CLASSIFY_TIMEOUT_SECONDS)
        return None
    except Exception as e:
        logger.warning("의사 분류 LLM 호출 실패 — 결정적 폴백: %s", e)
        return None
    parsed = extract_json_from_response(getattr(response, "content", ""))
    if not isinstance(parsed, dict):
        logger.warning("의사 분류 응답을 JSON으로 해석할 수 없음 — 결정적 폴백")
        return None
    return parsed


def _render_items(pending: list[dict]) -> str:
    """등록 후보 목록을 프롬프트용 번호 목록으로 렌더한다."""
    lines = []
    for item in pending:
        index = item.get("index")
        field = item.get("field", "")
        column = item.get("column", "")
        lines.append(f"- {index}번: {field} -> {column}")
    return "\n".join(lines) if lines else "(없음)"


async def classify_registration_intent(
    llm: BaseChatModel, user_query: str, pending: list[dict]
) -> tuple[str, list[int]] | None:
    """유사어 등록 응답에서 (mode, indices)를 분류한다 (A11).

    "2번만 빼고 전부"처럼 제외 표현이 섞인 응답을 정규식이 정반대로 해석하던 지점을 대체한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 응답 원문
        pending: 등록 후보 목록(`pending_synonym_registrations`)

    Returns:
        ("all" | "selective" | "skip", 등록할 번호 목록) 또는 판정 불가 시 None.
        `selective`인데 유효 번호가 없으면 판정 불가로 본다.
    """
    valid_indices = {
        item.get("index") for item in pending or [] if isinstance(item.get("index"), int)
    }
    prompt = REGISTRATION_INTENT_SYSTEM_PROMPT.format(items=_render_items(pending or []))
    parsed = await _classify(llm, prompt, user_query)
    if parsed is None:
        return None

    mode = parsed.get("mode")
    if mode not in _REGISTRATION_MODES:
        logger.info("등록 의사 분류 불가(mode=%r) — 재질의로 넘긴다", mode)
        return None
    if mode != "selective":
        logger.info("등록 의사 분류: mode=%s (%s)", mode, parsed.get("reason", ""))
        return (mode, [])

    raw = parsed.get("indices")
    indices = [
        i for i in (raw if isinstance(raw, list) else [])
        if isinstance(i, int) and not isinstance(i, bool) and i in valid_indices
    ]
    if not indices:
        logger.info("등록 의사 분류 불가(유효 번호 없음: %r) — 재질의로 넘긴다", raw)
        return None
    logger.info(
        "등록 의사 분류: selective indices=%s (%s)", indices, parsed.get("reason", "")
    )
    return ("selective", sorted(set(indices)))


async def classify_approval_intent(
    llm: BaseChatModel, user_query: str
) -> tuple[str, float] | None:
    """SQL 실행 승인 응답에서 의도를 분류한다 (A12 — fail-closed 유지).

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 응답 원문

    Returns:
        (intent, confidence) — intent는 "approve" | "reject" | "modify", 판정 불가 시 None.
        `approve`는 확신도 `APPROVAL_MIN_CONFIDENCE` 이상일 때만 반환한다 — 미만은 None으로
        내려 호출부의 거부(fail-closed) 판정을 그대로 쓰게 한다(D-130 불변).
        확신도를 함께 돌려주는 것은 호출부가 판정 근거를 감사 로그에 남기기 위한 것이다.
    """
    parsed = await _classify(llm, APPROVAL_INTENT_SYSTEM_PROMPT, user_query)
    if parsed is None:
        return None

    intent = parsed.get("intent")
    if intent not in _APPROVAL_INTENTS:
        logger.info("승인 의사 분류 불가(intent=%r) — 거부 유지", intent)
        return None

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    if intent != "approve":
        logger.info(
            "승인 의사 분류: %s conf=%.2f (%s)", intent, confidence, parsed.get("reason", "")
        )
        return (intent, confidence)

    if confidence < APPROVAL_MIN_CONFIDENCE:
        logger.warning(
            "승인 판정 확신도 미달(%.2f < %.2f) — 거부 유지(fail-closed)",
            confidence, APPROVAL_MIN_CONFIDENCE,
        )
        return None
    logger.info("승인 의사 분류: approve conf=%.2f (%s)", confidence, parsed.get("reason", ""))
    return ("approve", confidence)
