"""승인 의사 해석 — 결정적 어휘 판정·LLM 보강·레거시 구조 HITL 해제.

plans/179 W2 — `src/api/routes/query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.api.routes.query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

import logging
import re
from typing import Any

# 테스트·운영 로그가 이 이름으로 잡는다(이동 전 로거 이름 유지 — plans/179 W2)
logger = logging.getLogger("src.api.routes.query")


# 승인·거부 표현. 이 표현 하나만으로 이루어진 입력일 때만 의도가 확정된다.
_APPROVE_WORDS = ("실행", "approve", "승인", "확인", "네", "yes", "ㅇㅇ", "ok")
_REJECT_WORDS = ("취소", "reject", "거부", "아니", "no", "cancel")

# 승인·거부 표현 뒤에 붙어도 의미가 바뀌지 않는 어미·문장부호만 허용한다.
# 이 꼬리에 해당하지 않는 말이 이어지면("확인해보고 알려줘") 의도 불명으로 본다.
_INTENT_TAIL = (
    r"(?:\s*(?:해|하)?\s*"
    r"(?:줘|주세요|주십시오|세요|요|합니다|하겠습니다|할게요|할게|please)?)?"
    r"[\s.!?~,]*$"
)


def _matches_intent(text: str, words: tuple[str, ...]) -> bool:
    """입력이 승인·거부 표현 하나로만 이루어졌는지 판정한다(어미·문장부호는 허용)."""
    return any(re.match(re.escape(w) + _INTENT_TAIL, text) for w in words)


# LLM 승인 판정을 인정하기 위해 원문에 최소 1개 있어야 하는 **결정적 보강 신호**(R3-(ii)).
# LLM의 approve 하나만으로는 실행하지 않는다 — 승인 어휘가 전무한 입력(예: "결과를 엑셀로
# 정리해줘")은 LLM이 approve를 환각해도 실행되지 않는다. 승인 의사 표현의 변형("네 실행해주세요
# 감사합니다"·"그대로 진행해줘")은 이 신호를 갖고 있어 회복 대상은 그대로 남는다.
# "해줘"류 범용 어미는 넣지 않는다(비승인 지시문까지 보강 통과시킴).
_APPROVAL_CORROBORATION_TOKENS: tuple[str, ...] = (
    "실행", "진행", "승인", "허가", "확인", "네", "예", "좋아", "맞아", "그대로", "ㅇㅇ",
    "approve", "yes", "ok", "go",
)


def _has_approval_token(query: str) -> bool:
    """원문에 승인 어휘가 하나라도 있는지 판정한다(LLM 승인 판정의 결정적 보강 조건)."""
    text = (query or "").lower()
    return any(token in text for token in _APPROVAL_CORROBORATION_TOKENS)


def _deterministic_approval(query: str) -> tuple[str, str] | None:
    """승인 의도를 결정적으로 확정한다(확정 불가면 None).

    **fail-closed**: 명확한 승인 표현이 아니면 승인하지 않는다. 과거에는 기본값이
    "approve"이고 승인어를 prefix로 매칭해, "확인해보고 알려줘" 같은 입력이 승인으로
    오탐되어 사용자가 승인하지 않은 SQL이 실행됐다(D-130).
    """
    q = query.strip().lower()

    if _matches_intent(q, _APPROVE_WORDS):
        return ("approve", "")

    if _matches_intent(q, _REJECT_WORDS):
        return ("reject", "")

    # SQL이 포함된 경우 modify로 판단
    if re.search(r"\bSELECT\b", query, re.IGNORECASE):
        return ("modify", query.strip())

    return None


def _parse_approval(query: str) -> tuple[str, str]:
    """사용자 입력에서 승인 의도를 파싱한다(결정적 판정 전용 — fail-closed).

    Args:
        query: 사용자 입력

    Returns:
        (action, modified_sql) 튜플
        - action: "approve" | "reject" | "modify"
        - modified_sql: modify 시 수정된 SQL
    """
    resolved = _deterministic_approval(query)
    if resolved is not None:
        return resolved

    # 의도 불명 — 실행하지 않는다(미승인 SQL 실행 방지)
    logger.warning("승인 의도를 확정하지 못해 실행을 취소한다: %r", query.strip()[:80])
    return ("reject", "")


def _log_approval_decision(
    action: str, query: str, *, basis: str, confidence: float | None = None, note: str = ""
) -> None:
    """승인 판정 근거를 감사 가능한 단일 라인으로 남긴다(결정적/LLM·확신도·보강 신호).

    승인은 사용자가 보지 못한 SQL을 실행시키는 게이트라, 어떤 근거로 통과·차단됐는지 사후에
    추적할 수 있어야 한다(D-011·D-130). approve만 INFO, 나머지는 WARNING으로 남긴다.
    """
    conf = "-" if confidence is None else f"{confidence:.2f}"
    line = "[승인판정] action=%s basis=%s confidence=%s%s query=%r"
    args = (action, basis, conf, f" note={note}" if note else "", query.strip()[:80])
    if action == "approve":
        logger.info(line, *args)
    else:
        logger.warning(line, *args)


async def resolve_approval_action(query: str, config: Any) -> tuple[str, str]:
    """승인 의도를 결정적 판정 1순위 + (옵트인) LLM 보조로 해소한다 (Plan 67 R3-(ii) / A12).

    결정적 판정이 확정한 입력은 LLM을 거치지 않는다(동작 불변). 확정 불가일 때만 LLM 분류를
    시도하며, **fail-closed는 불변**이다(D-130). LLM 승인은 두 조건을 **모두** 만족해야 인정한다:
    ①확신도 ≥ `APPROVAL_MIN_CONFIDENCE`(코드 상수) ②원문에 결정적 승인 어휘 존재
    (`_has_approval_token`) — LLM 판정 하나만으로는 실행하지 않는다.
    `QUERY_INTENT_LLM_ASSIST`가 꺼져 있거나 LLM 호출이 실패하면 종전 결정적 판정 결과(거부)를
    그대로 쓴다. 모든 경로가 판정 근거를 로그로 남긴다.

    Args:
        query: 사용자 입력
        config: 앱 설정(`request.app.state.config`)

    Returns:
        (action, modified_sql) 튜플
    """
    resolved = _deterministic_approval(query)
    if resolved is not None:
        _log_approval_decision(resolved[0], query, basis="deterministic")
        return resolved

    if not getattr(getattr(config, "query", None), "intent_llm_assist", False):
        _log_approval_decision(
            "reject", query, basis="deterministic", note="의도불명·LLM보조_OFF"
        )
        return ("reject", "")

    classified: tuple[str, float] | None = None
    try:
        from src.llm import create_llm
        from src.routing.intent_confirm import classify_approval_intent

        classified = await classify_approval_intent(create_llm(config), query)
    except Exception as e:
        logger.warning("승인 의사 LLM 분류 실패 — 거부 유지(fail-closed): %s", e)

    if classified is None:
        _log_approval_decision("reject", query, basis="llm", note="판정불가")
        return ("reject", "")

    intent, confidence = classified
    if intent == "approve":
        # LLM 단독 승인 금지 — 결정적 보강 신호가 없으면 실행하지 않는다.
        if not _has_approval_token(query):
            _log_approval_decision(
                "reject", query, basis="llm", confidence=confidence,
                note="승인어휘_부재(LLM_단독_승인_불허)",
            )
            return ("reject", "")
        _log_approval_decision(
            "approve", query, basis="llm", confidence=confidence, note="승인어휘_확인"
        )
        return ("approve", "")

    if intent == "modify":
        _log_approval_decision("modify", query, basis="llm", confidence=confidence)
        return ("modify", query.strip())

    _log_approval_decision("reject", query, basis="llm", confidence=confidence)
    return ("reject", "")


def _is_legacy_structure_hitl_thread(checkpoint_state: dict[str, Any] | None) -> bool:
    """구조 분석 승인 대기로 멈춘 **구 스레드**인지 판정한다 (plans/104 · D-135 ①).

    질의 경로의 구조 승인 게이트는 plans/104(D-227)에서 노드째 삭제됐다. 그 게이트 앞에서 멈춘
    체크포인트(`awaiting_approval` + `approval_context.type == "structure_analysis"`)는 재개할
    노드가 없으므로 승인 턴으로 보지 않는다 — 다음 턴은 일반 질의로 처리하고 승인 상태를 해제한다.
    해제하지 않으면 체크포인터가 델타만 병합해 `awaiting_approval`이 매 턴 남는다(실측).
    """
    if not checkpoint_state or not checkpoint_state.get("awaiting_approval"):
        return False
    ctx = checkpoint_state.get("approval_context") or {}
    return isinstance(ctx, dict) and ctx.get("type") == "structure_analysis"


def _release_legacy_structure_hitl(
    delta: dict[str, Any], checkpoint_state: dict[str, Any] | None
) -> dict[str, Any]:
    """구 구조 승인 대기 스레드면 이번 턴 델타에 승인 상태 해제를 싣는다(아니면 그대로)."""
    if _is_legacy_structure_hitl_thread(checkpoint_state):
        logger.info("구조 승인 대기 구 스레드 — 일반 질의 턴으로 처리하고 승인 상태 해제")
        delta["awaiting_approval"] = False
        delta["approval_context"] = None
        delta["approval_action"] = None
    return delta
