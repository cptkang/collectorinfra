"""유사어 등록 처리 노드.

pending_synonym_registrations에서 사용자가 선택한 항목을
Redis synonyms에 등록한다.
"""

from __future__ import annotations

import logging
import re

from langchain_core.messages import AIMessage

from src.config import AppConfig, load_config
from src.state import AgentState

logger = logging.getLogger(__name__)


async def synonym_registrar(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """유사어 등록 요청을 처리한다.

    사용자의 자연어 입력("전체 등록", "1, 3 등록", "건너뛰기")을
    파싱하여 pending_synonym_registrations에서 선택된 항목만
    Redis synonyms에 등록한다.

    Args:
        state: 현재 에이전트 상태
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

    if syn_reg:
        mode = syn_reg.get("mode", "skip")
        indices = syn_reg.get("indices", [])
    else:
        # 직접 파싱 폴백
        mode, indices = _parse_registration_intent(user_query)

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


def _parse_registration_intent(user_query: str) -> tuple[str, list[int]]:
    """사용자 입력에서 유사어 등록 의도를 파싱한다.

    Args:
        user_query: 사용자 입력

    Returns:
        (mode, indices) 튜플
        - mode: "all" | "selective" | "skip"
        - indices: selective 모드일 때 등록할 항목 번호 목록
    """
    query = user_query.strip().lower()

    # 건너뛰기 패턴
    skip_patterns = [
        "건너뛰기", "스킵", "skip", "등록 안", "안 해", "필요 없",
        "괜찮", "아니", "no", "pass",
    ]
    for pattern in skip_patterns:
        if pattern in query:
            return ("skip", [])

    # 전체 등록 패턴
    all_patterns = [
        "전체 등록", "모두 등록", "전부 등록", "다 등록", "all",
    ]
    for pattern in all_patterns:
        if pattern in query:
            return ("all", [])

    # 선택 등록 패턴: "1, 3 등록", "1번 등록", "1,3번 등록"
    selective_match = re.search(
        r"(\d+(?:\s*[,\s]\s*\d+)*)\s*번?\s*등록",
        query,
    )
    if selective_match:
        nums_str = selective_match.group(1)
        nums = [int(n.strip()) for n in re.findall(r"\d+", nums_str)]
        return ("selective", nums)

    # 단순 숫자만: "1, 3" (등록 맥락에서)
    nums_only = re.findall(r"\d+", query)
    if nums_only and ("등록" in query or len(query) < 20):
        return ("selective", [int(n) for n in nums_only])

    # 기본: 건너뛰기 (모호한 경우)
    return ("skip", [])
