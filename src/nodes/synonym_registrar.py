"""유사어 등록 처리 노드.

pending_synonym_registrations에서 사용자가 선택한 항목을
Redis synonyms에 등록한다.
"""

from __future__ import annotations

import logging
import re

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.routing.intent_confirm import classify_registration_intent
from src.state import AgentState

logger = logging.getLogger(__name__)

# 등록 의사 확정 실패 시 사용자에게 되묻는 문구. 등록 후보(pending)를 유지한 채 반환하므로
# 다음 턴의 응답으로 이어서 해소된다 — 애매한 입력을 임의로 skip/selective 처리하던 침묵 오답
# ("2번만 빼고 전부"가 2번만 등록으로 뒤집히던 실측)을 대신한다(Plan 67 R3-(ii) / A11).
_REASK_RESPONSE = (
    "유사어 등록 의사를 확정하지 못했습니다. "
    '"전체 등록" / "1, 3번 등록" / "건너뛰기" 중 하나로 다시 알려주세요.'
)


async def synonym_registrar(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """유사어 등록 요청을 처리한다.

    사용자의 자연어 입력("전체 등록", "1, 3 등록", "건너뛰기")을
    파싱하여 pending_synonym_registrations에서 선택된 항목만
    Redis synonyms에 등록한다.

    등록 의사 해석은 3단이다(Plan 67 R3-(ii)):
    ① 상위 파싱 결과(`parsed_requirements["synonym_registration"]`) — 단, 제외 표현이 섞인
      응답은 신뢰하지 않는다(프롬프트에 제외 개념이 없어 "N번만 빼고"를 N번 등록으로 뒤집는다).
    ② 결정적 선처리(`_parse_registration_intent`) — 단독 표현·순수 번호 나열만 인정.
    ③ LLM 분류(옵트인 `QUERY_INTENT_LLM_ASSIST`) — 모호 케이스만. 실패·미가용이면 **재질의**.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 필요 시 내부 생성)
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드
    """
    if app_config is None:
        app_config = load_config()

    pending = state.get("pending_synonym_registrations")
    if not pending:
        return {
            "final_response": "등록할 유사어 항목이 없습니다.",
            "pending_synonym_registrations": None,
            "current_node": "synonym_registrar",
        }

    user_query = state.get("user_query", "")

    # parsed_requirements에서 synonym_registration 의도 확인
    parsed = state.get("parsed_requirements", {})
    syn_reg = parsed.get("synonym_registration")

    resolved: tuple[str, list[int]] | None = None
    if isinstance(syn_reg, dict) and syn_reg and not _has_exclusion_marker(user_query):
        raw_indices = syn_reg.get("indices") or []
        resolved = (
            syn_reg.get("mode", "all"),
            [i for i in raw_indices if isinstance(i, int) and not isinstance(i, bool)],
        )
    else:
        resolved = _parse_registration_intent(user_query)
        if resolved is None:
            resolved = await _classify_intent_with_llm(
                user_query, pending, llm=llm, app_config=app_config
            )

    if resolved is None:
        logger.info(
            "synonym_registrar: 등록 의사 확정 불가 — 재질의(pending 유지): %r", user_query[:80]
        )
        return {
            "final_response": _REASK_RESPONSE,
            "pending_synonym_registrations": pending,
            "current_node": "synonym_registrar",
            "messages": [AIMessage(content=_REASK_RESPONSE)],
        }

    mode, indices = resolved

    if mode == "skip":
        logger.info("synonym_registrar: 등록 건너뛰기")
        return {
            "final_response": "유사어 등록을 건너뛰었습니다.",
            "pending_synonym_registrations": None,
            "current_node": "synonym_registrar",
        }

    # 등록 대상 결정
    if mode == "all":
        items_to_register = pending
    elif mode == "selective" and indices:
        items_to_register = [
            item for item in pending
            if item.get("index") in indices
        ]
    else:
        items_to_register = pending

    if not items_to_register:
        return {
            "final_response": "등록할 항목을 찾을 수 없습니다.",
            "pending_synonym_registrations": None,
            "current_node": "synonym_registrar",
        }

    # Redis에 등록
    registered = []
    try:
        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(app_config)

        for item in items_to_register:
            field = item.get("field", "")
            column = item.get("column", "")
            db_id = item.get("db_id", "")

            if not field or not column:
                continue

            # DB별 synonyms에 등록
            if db_id:
                await cache_mgr.add_synonyms(
                    db_id, column, [field], source="operator"
                )

            # 글로벌 사전에도 등록
            bare_col = column.split(".")[-1] if "." in column else column
            await cache_mgr.add_global_synonym(bare_col, [field])

            registered.append(f"{field} -> {column}")
            logger.info(
                "synonym_registrar: 등록 완료 - %s -> %s (db=%s)",
                field, column, db_id,
            )

    except Exception as e:
        logger.error("synonym_registrar: Redis 등록 실패 - %s", e)
        return {
            "final_response": f"유사어 등록 중 오류가 발생했습니다: {e}",
            "pending_synonym_registrations": None,
            "current_node": "synonym_registrar",
            "error_message": str(e),
        }

    response = f"{len(registered)}건 유사어 등록 완료:\n"
    response += "\n".join(f"- {r}" for r in registered)

    return {
        "final_response": response,
        "pending_synonym_registrations": None,
        "current_node": "synonym_registrar",
        "messages": [AIMessage(content=response)],
    }


# 제외 표현. 이 표현이 있으면 "N번만 빼고 전부"처럼 등록 대상이 **반전**되므로, 상위 파싱
# 결과(제외 개념이 없는 프롬프트 산출물)와 번호 나열 판정을 모두 신뢰하지 않고 LLM 분류로 넘긴다.
_EXCLUSION_MARKERS: tuple[str, ...] = ("빼고", "빼", "제외", "말고", "이외", "외에", "except")

# 결정적 인정 표현 — **입력 전체가** 이 표현 + 허용 어미로만 이루어졌을 때만 확정한다.
# 종전에는 부분 문자열 매칭이라 "괜찮아요, 등록해주세요"의 "괜찮"이 건너뛰기로 뒤집혔다.
_SKIP_FORMS: tuple[str, ...] = (
    "건너뛰", "스킵", "skip", "pass", "no", "아니", "등록안", "등록하지", "필요없", "나중에",
)
_ALL_FORMS: tuple[str, ...] = (
    "전체등록", "모두등록", "전부등록", "다등록", "all", "전체", "모두", "전부", "등록",
)
# 표현 뒤에 붙어도 의미가 바뀌지 않는 어미만 허용한다(그 밖의 말이 이어지면 판정 불가).
_INTENT_TAIL_RE = re.compile(
    r"(?:해|하)?(?:도|겠)?"
    r"(?:줘|주세요|주십시오|세요|요|오|어|어요|자|기|겠어|겠습니다|합니다|할게요|할게|"
    r"돼|됩니다|됨|음|습니다|드립니다|please)*$"
)
# 순수 번호 나열: "1, 3", "1번 등록", "1번과 3번 등록해줘" (번호 외의 뜻이 섞이면 미인정)
_NUM_ONLY_RE = re.compile(
    r"^(?:\d+(?:번|번째)?(?:[,·]|과|와|및|그리고|랑|하고)?)+(?:만)?(?:등록\S*)?$"
)


def _normalize_intent_text(user_query: str) -> str:
    """의사 표현 판정을 위해 공백·문장부호를 제거하고 소문자로 정규화한다.

    쉼표·중점은 번호 나열의 구분자라 남긴다("1, 3"이 "13"으로 뭉치면 13번 등록이 된다).
    """
    return re.sub(r"[\s.!?~'\"]+", "", user_query or "").lower()


def _has_exclusion_marker(user_query: str) -> bool:
    """등록 대상을 반전시키는 제외 표현이 있는지 판정한다."""
    text = (user_query or "").lower()
    return any(marker in text for marker in _EXCLUSION_MARKERS)


def _matches_form(norm: str, forms: tuple[str, ...]) -> bool:
    """정규화된 입력이 표현 하나 + 허용 어미로만 이루어졌는지 판정한다."""
    return any(
        norm.startswith(form) and _INTENT_TAIL_RE.fullmatch(norm[len(form):]) is not None
        for form in forms
    )


def _parse_registration_intent(user_query: str) -> tuple[str, list[int]] | None:
    """사용자 입력에서 유사어 등록 의도를 **결정적으로 확정 가능한 경우만** 파싱한다.

    명확한 표현(단독 건너뛰기/전체 등록, 순수 번호 나열)만 인정하고, 그 밖의 입력은 판정 불가
    (None)로 남겨 호출부가 LLM 분류·재질의로 넘기게 한다. 종전에는 부분 문자열 매칭 + "모호하면
    skip" 기본값이라 "괜찮아요, 등록해주세요"가 건너뛰기로, "2번만 빼고 전부"가 2번 등록으로
    뒤집혔다(`docs/regex_llm_conversion_review.md` A11).

    Args:
        user_query: 사용자 입력

    Returns:
        (mode, indices) 튜플 — mode는 "all" | "selective" | "skip",
        indices는 selective일 때 등록할 항목 번호 목록. 확정 불가면 None.
    """
    if _has_exclusion_marker(user_query):
        return None  # 대상 반전 표현은 결정적으로 다루지 않는다(정반대 해석 방지)

    norm = _normalize_intent_text(user_query)
    if not norm:
        return None

    # 건너뛰기 판정을 먼저 본다("등록 안 해"가 "등록"으로 오인되지 않도록)
    if _matches_form(norm, _SKIP_FORMS):
        return ("skip", [])

    if _NUM_ONLY_RE.fullmatch(norm):
        nums = [int(n) for n in re.findall(r"\d+", norm)]
        if nums:
            return ("selective", nums)

    if _matches_form(norm, _ALL_FORMS):
        return ("all", [])

    return None


async def _classify_intent_with_llm(
    user_query: str,
    pending: list[dict],
    *,
    llm: BaseChatModel | None,
    app_config: AppConfig,
) -> tuple[str, list[int]] | None:
    """결정적 판정이 확정하지 못한 등록 의사를 LLM으로 분류한다(옵트인, 실패 시 None).

    `QUERY_INTENT_LLM_ASSIST`가 꺼져 있으면 호출하지 않는다 — 그 경우 호출부가 재질의하므로
    LLM 없이도 오답이 나가지 않는다(신규 LLM 호출은 명시 옵트인 후에만 — D-127).
    """
    if not getattr(app_config.query, "intent_llm_assist", False):
        return None
    try:
        if llm is None:
            llm = create_llm(app_config)
    except Exception as e:
        logger.warning("synonym_registrar: 의사 분류 LLM 생성 실패 — 재질의로 진행: %s", e)
        return None
    return await classify_registration_intent(llm, user_query, pending)
