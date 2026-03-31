"""시멘틱 라우팅 노드.

사용자의 자연어 질의를 분석하여 어떤 DB를 조회해야 하는지 결정한다.
LLM 기반으로만 DB 라우팅을 수행한다.

v2 변경:
- 키워드 기반 1차 분류 완전 제거
- LLM 전용 라우팅으로 전환
- 사용자 직접 DB 지정 지원 추가
- 동적 프롬프트 구성 (활성 도메인 기반)
"""

from __future__ import annotations

import logging
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.semantic_router import SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE
from src.routing.domain_config import DB_DOMAINS, DBDomainConfig
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

# 라우팅 결과에 포함할 최소 관련도 점수
MIN_RELEVANCE_SCORE = 0.3


async def semantic_router(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 질의를 분석하여 대상 DB를 결정한다.

    LLM 기반으로만 라우팅을 수행한다.
    사용자가 프롬프트에서 직접 DB를 지정한 경우도 LLM이 감지한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - target_databases: 대상 DB 목록
        - is_multi_db: 멀티 DB 쿼리 여부
        - active_db_id: 첫 번째(최고 관련도) DB 식별자
        - user_specified_db: 사용자 직접 지정 DB (없으면 None)
        - current_node: "semantic_router"
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    user_query = state["user_query"]
    active_db_ids = app_config.multi_db.get_active_db_ids()

    # [우선순위 1] pending_synonym_reuse → cache_management 강제 라우팅
    pending_reuse = state.get("pending_synonym_reuse")
    if pending_reuse:
        logger.info("pending_synonym_reuse 감지, cache_management로 강제 라우팅")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "cache_management",
            "current_node": "semantic_router",
        }

    # [우선순위 2] 명시적 유사어 등록 요청 → synonym_registration 라우팅
    # parsed_requirements에 synonym_registration이 있을 때만 (멀티턴 두 번째 요청)
    # 첫 번째 요청에서 field_mapper가 생성한 pending은 쿼리 파이프라인 완료 후 안내만 표시
    parsed = state.get("parsed_requirements", {})
    synonym_reg = parsed.get("synonym_registration")
    if synonym_reg:
        pending_regs = state.get("pending_synonym_registrations")
        if pending_regs and len(pending_regs) > 0:
            logger.info(
                "유사어 등록 요청 감지 (%d건), synonym_registrar로 라우팅",
                len(pending_regs),
            )
            return {
                "target_databases": [],
                "is_multi_db": False,
                "active_db_id": None,
                "user_specified_db": None,
                "routing_intent": "synonym_registration",
                "current_node": "semantic_router",
            }

    # [우선순위 3] field_mapper에서 이미 대상 DB를 결정한 경우 (양식 업로드 시)
    mapped_db_ids = state.get("mapped_db_ids")
    if mapped_db_ids:
        logger.info(
            "시멘틱 라우팅: field_mapper 매핑 결과 사용, LLM 라우팅 스킵. DB=%s",
            mapped_db_ids,
        )
        targets = [
            {
                "db_id": db_id,
                "relevance_score": 1.0,
                "sub_query_context": user_query,
                "user_specified": False,
                "reason": "필드 매핑 결과에서 식별된 DB",
            }
            for db_id in mapped_db_ids
        ]
        is_multi_db = len(targets) > 1
        return {
            "target_databases": targets,
            "is_multi_db": is_multi_db,
            "active_db_id": targets[0]["db_id"],
            "user_specified_db": None,
            "routing_intent": "data_query",
            "current_node": "semantic_router",
        }

    # 활성 DB가 없으면 레거시 모드
    if not active_db_ids:
        logger.info("활성 DB 없음, 레거시 단일 DB 모드로 동작")
        return {
            "target_databases": [
                {
                    "db_id": "default",
                    "relevance_score": 1.0,
                    "sub_query_context": user_query,
                    "user_specified": False,
                    "reason": "레거시 단일 DB 모드",
                }
            ],
            "is_multi_db": False,
            "active_db_id": "default",
            "user_specified_db": None,
            "current_node": "semantic_router",
        }

    # 활성 도메인만 필터링
    active_domains = [d for d in DB_DOMAINS if d.db_id in active_db_ids]

    # Redis 캐시에서 DB 설명 로드 (라우팅 프롬프트 보강용)
    db_descriptions: dict[str, str] = {}
    try:
        from src.schema_cache.cache_manager import get_cache_manager
        cache_mgr = get_cache_manager(app_config)
        db_descriptions = await cache_mgr.get_db_descriptions()
    except Exception as e:
        logger.debug("DB 설명 로드 실패 (라우팅 계속): %s", e)

    # LLM 기반 분류 (사용자 직접 지정 감지 포함)
    try:
        llm_results = await _llm_classify(
            llm, user_query, active_domains, db_descriptions=db_descriptions
        )
    except Exception as e:
        logger.error("LLM 라우팅 분류 실패: %s", e)
        # LLM 실패 시 첫 번째 활성 DB로 폴백
        llm_results = [
            {
                "db_id": active_db_ids[0],
                "relevance_score": 0.5,
                "sub_query_context": user_query,
                "user_specified": False,
                "reason": f"LLM 분류 실패로 기본 DB 사용: {e}",
            }
        ]

    # 캐시 관리 의도 확인
    intent = "data_query"
    if isinstance(llm_results, dict):
        # _llm_classify가 dict를 반환한 경우 (intent 포함)
        intent = llm_results.get("intent", "data_query")
        llm_results = llm_results.get("databases", [])

    if intent == "cache_management":
        logger.info("시멘틱 라우팅: 캐시 관리 의도 감지")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "cache_management",
            "current_node": "semantic_router",
        }

    # 최소 관련도 필터링 및 정렬
    targets = [
        r for r in llm_results
        if r["relevance_score"] >= MIN_RELEVANCE_SCORE
    ]
    targets.sort(key=lambda x: x["relevance_score"], reverse=True)

    # 결과가 없으면 기본 DB 사용
    if not targets:
        logger.warning("라우팅 결과 없음, 첫 번째 활성 DB 사용")
        targets = [
            {
                "db_id": active_db_ids[0],
                "relevance_score": 0.5,
                "sub_query_context": user_query,
                "user_specified": False,
                "reason": "LLM 분류 결과 없음, 기본 DB 사용",
            }
        ]

    # 사용자 직접 지정 DB 확인
    user_specified_db = None
    for t in targets:
        if t.get("user_specified"):
            user_specified_db = t["db_id"]
            break

    is_multi_db = len(targets) > 1
    active_db_id = targets[0]["db_id"]

    logger.info(
        "시멘틱 라우팅 완료: targets=%s, multi_db=%s, user_specified=%s",
        [t["db_id"] for t in targets],
        is_multi_db,
        user_specified_db,
    )

    return {
        "target_databases": targets,
        "is_multi_db": is_multi_db,
        "active_db_id": active_db_id,
        "user_specified_db": user_specified_db,
        "routing_intent": "data_query",
        "current_node": "semantic_router",
    }


async def _llm_classify(
    llm: BaseChatModel,
    query: str,
    domains: list[DBDomainConfig],
    *,
    db_descriptions: dict[str, str] | None = None,
) -> list[dict]:
    """LLM을 사용하여 질의의 대상 DB를 분류한다.

    활성 도메인 목록을 기반으로 동적 프롬프트를 구성하여 LLM에 전달한다.

    Args:
        llm: LLM 인스턴스
        query: 사용자 질의
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시에서 로드한 DB 설명 (선택)

    Returns:
        분류 결과 목록
    """
    system_prompt = _build_router_prompt(domains, db_descriptions=db_descriptions)
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=query),
    ]

    response = await llm.ainvoke(messages)
    parsed = extract_json_from_response(response.content)

    if not parsed:
        return []

    # 캐시 관리 의도 확인
    intent = parsed.get("intent", "data_query")
    if intent == "cache_management":
        return {"intent": "cache_management", "databases": []}

    if "databases" not in parsed:
        return []

    # 활성 도메인 필터링
    valid_db_ids = {d.db_id for d in domains}
    results: list[dict] = []

    for db_entry in parsed["databases"]:
        db_id = db_entry.get("db_id", "")
        if db_id in valid_db_ids:
            results.append({
                "db_id": db_id,
                "relevance_score": float(db_entry.get("relevance_score", 0.5)),
                "sub_query_context": db_entry.get("sub_query_context", query),
                "user_specified": bool(db_entry.get("user_specified", False)),
                "reason": db_entry.get("reason", ""),
            })

    return results


def _build_router_prompt(
    domains: list[DBDomainConfig],
    *,
    db_descriptions: dict[str, str] | None = None,
) -> str:
    """활성 도메인 기반으로 라우팅 프롬프트를 동적 생성한다.

    db_descriptions가 제공되면 각 DB 설명에 캐시된 상세 설명을 추가하여
    LLM의 DB 분류 정확도를 향상시킨다.

    Args:
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시에서 로드한 DB 설명 매핑 (선택)

    Returns:
        완성된 시스템 프롬프트 문자열
    """
    db_desc_list: list[str] = []
    for i, domain in enumerate(domains, 1):
        aliases_str = ", ".join(domain.aliases) if domain.aliases else domain.db_id
        entry = (
            f"{i}. **{domain.display_name}** ({domain.db_id})\n"
            f"   - 별칭: {aliases_str}\n"
            f"   - {domain.description}"
        )
        # Redis 캐시에서 로드한 DB 상세 설명 추가
        if db_descriptions and domain.db_id in db_descriptions:
            cached_desc = db_descriptions[domain.db_id]
            entry += f"\n   - 상세: {cached_desc}"
        db_desc_list.append(entry)
    db_list = "\n\n".join(db_desc_list)
    return SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(db_list=db_list)


