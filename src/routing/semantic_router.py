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
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.utils.synonym_set_parser import parse_synonym_set
from src.config import AppConfig, load_config
from src.clients.fabrix_kbgenai import KBGenAIChat
from src.llm import create_llm
from src.prompts.semantic_router import (
    SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION,
    SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE,
)
from src.routing.domain_config import DB_DOMAINS, DBDomainConfig
from src.routing.registry import get_registry
from src.state import AgentState
from src.utils.json_extract import extract_json_from_response
from src.utils.query_gen_common import (
    ZONE_SKIP_SIGNAL_TERMS,
    build_zone_clarification,
    has_host_identifier_filter,
)

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

    # [우선순위 2.5] 존 역질문에서 사용자가 체크박스로 확정한 DB 목록 (Plan 70 §4).
    # UI 선택은 어떤 추론보다 우선 — mapped_db_ids 선례와 동형으로 LLM 라우팅을 스킵해
    # 결정적으로 고정한다(자연어 재조합 금지: sub_query_context=원문 유지).
    selected_db_ids = state.get("selected_db_ids")
    if selected_db_ids:
        selected = [d for d in selected_db_ids if not active_db_ids or d in active_db_ids]
        if selected:
            logger.info("시멘틱 라우팅: 사용자 존 선택 고정, LLM 라우팅 스킵. DB=%s", selected)
            targets = [
                {
                    "db_id": db_id,
                    "relevance_score": 1.0,
                    "sub_query_context": user_query,
                    "user_specified": True,
                    "reason": "존 선택 역질문에서 사용자가 확정한 DB",
                }
                for db_id in selected
            ]
            return {
                "target_databases": targets,
                "is_multi_db": len(targets) > 1,
                "active_db_id": targets[0]["db_id"],
                "user_specified_db": targets[0]["db_id"] if len(targets) == 1 else None,
                "routing_intent": "data_query",
                "current_node": "semantic_router",
            }

    # [우선순위 3] 앵커 없는 동의어 집합 등록 → cache_management 강제 라우팅 (D-142)
    # "vcore, cpu, core은 동의어이다. 캐시에 등록하라" 형태를 LLM 라우팅에 맡기면
    # data_query로 새는 경우가 생긴다. 선파서가 확정한 문장은 결정적으로 보낸다.
    # 존 선택 확정(2.5)보다 뒤 — 두 게이트는 트리거가 배타적(state 키 vs 문장 패턴)이나
    # UI 확정은 어떤 텍스트 해석보다 우선한다는 기존 원칙을 유지한다.
    if parse_synonym_set(user_query):
        logger.info("동의어 집합 등록 요청 감지(결정적), cache_management로 라우팅")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "cache_management",
            "current_node": "semantic_router",
        }

    # [우선순위 4] field_mapper에서 이미 대상 DB를 결정한 경우 (양식 업로드 시)
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

    # (Plan 64 CW-B) 장애 진단 pull 위임 옵트인. off면 프롬프트에 fault_diagnosis 미노출 +
    # 아래 강등으로 라우팅 비트동일(회귀 0). noise_gate 속성 부재(경량 config)도 안전 처리.
    fault_dx_on = bool(
        getattr(getattr(app_config, "noise_gate", None), "fault_diagnosis_enabled", False)
    )

    # LLM 기반 분류 (사용자 직접 지정 감지 포함)
    try:
        llm_results = await _llm_classify(
            llm, user_query, active_domains,
            db_descriptions=db_descriptions,
            fault_diagnosis_enabled=fault_dx_on,
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

    # (Plan 64 CW-B) 옵트인 off인데 LLM이 fault_diagnosis를 산출했다면(할루시네이션 방어)
    # data_query로 강등한다 — off 경로에서 fault_diagnosis 노드는 미배선이라 라우팅 파손을 막는다.
    if intent == "fault_diagnosis" and not fault_dx_on:
        logger.debug("fault_diagnosis 비활성 — data_query로 강등(라우팅 비트동일)")
        intent = "data_query"

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

    if intent == "general_inference":
        logger.info("시멘틱 라우팅: 일반 추론 의도 감지")
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "general_inference",
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

    # 존 역질문 후단 게이트 (D-143 후속2) — 레거시(비오케스트레이션) 경로 대칭.
    # 트랙 A(subagents._zone_clarification_or_none_task)와 동일 판정: 대화형 채널 +
    # 첫 턴 + 위치어·서버 식별·사용자 지정 신호 없음 + 폴스타 존 팬아웃이면 역질문.
    zone_q = _zone_clarification_or_none_router(
        state, targets, user_specified_db, app_config
    )
    if zone_q:
        logger.info(
            "존 역질문 후단 게이트 발동(D-143 후속2, 레거시 경로): targets=%s",
            [t["db_id"] for t in targets],
        )
        return {
            "target_databases": [],
            "is_multi_db": False,
            "active_db_id": None,
            "user_specified_db": None,
            "routing_intent": "zone_clarification",
            "zone_clarification": zone_q,
            "final_response": zone_q["question"],
            "current_node": "semantic_router",
        }

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
        "routing_intent": intent,
        "current_node": "semantic_router",
    }


def _zone_clarification_or_none_router(
    state: AgentState,
    targets: list[dict],
    user_specified_db: Optional[str],
    app_config: AppConfig,
) -> Optional[dict]:
    """존 역질문 후단 게이트 판정 (D-143 후속2 — 레거시 semantic_router 경로).

    §4.2 비발동 목록을 결정적 조건으로 판정한다. selected_db_ids(우선순위 2.5)·
    mapped_db_ids(우선순위 4)는 본 함수 도달 전에 조기 반환되므로 재검사하지 않는다.

    Args:
        state: 현재 에이전트 상태 (input_parser/context_resolver 산출 포함)
        targets: 관련도 필터·정렬이 끝난 대상 DB 목록
        user_specified_db: 사용자가 직접 지정한 DB (있으면 비발동)
        app_config: 앱 설정

    Returns:
        발동 시 clarification 페이로드, 비발동이면 None
    """
    # 채널 게이트(§4.3-3): 대화형 텍스트 라우트만 — 배치·평가·API 직접 호출 보호
    if not state.get("zone_clarification_allowed"):
        return None
    if user_specified_db:
        return None
    # 첫 턴 한정 — 직전 턴 DB가 있으면 기존 승계 흐름 유지(§4.2)
    ctx = state.get("conversation_context") or {}
    if ctx.get("previous_db_ids"):
        return None
    # 이번 턴 원문에 위치/DB 신호가 있으면 비발동(D-065 결정적 보강이 처리)
    user_query = state.get("user_query", "") or ""
    lowered = user_query.lower()
    if any(t.lower() in lowered for t in ZONE_SKIP_SIGNAL_TERMS):
        return None
    parsed = state.get("parsed_requirements") or {}
    # 서버명 지목 질의는 존이 결과에 영향 없음(§4.2 ⓐ)
    if has_host_identifier_filter(parsed):
        return None
    # 조회 대상 필드가 파싱되지 않은 질의는 비발동(과잉 역질문 방지)
    if not parsed.get("query_targets"):
        return None
    # 대상이 전부 폴스타 존일 때만
    polestar_ids = app_config.get_polestar_db_ids() or set()
    target_ids = [t.get("db_id") for t in targets if t.get("db_id")]
    if not target_ids or not all(d in polestar_ids for d in target_ids):
        return None
    return build_zone_clarification(
        app_config.multi_db.get_active_db_ids(), user_query,
        # 존 그룹 상호배타(D-143 후속3) — 라우트 pre-gate와 동일 UI 규칙
        group_exclusive=bool(
            getattr(app_config.multi_db, "zone_group_exclusive", True)
        ),
    )


async def _llm_classify(
    llm: BaseChatModel,
    query: str,
    domains: list[DBDomainConfig],
    *,
    db_descriptions: dict[str, str] | None = None,
    fault_diagnosis_enabled: bool = False,
) -> list[dict]:
    """LLM을 사용하여 질의의 대상 DB를 분류한다.

    활성 도메인 목록을 기반으로 동적 프롬프트를 구성하여 LLM에 전달한다.

    Args:
        llm: LLM 인스턴스
        query: 사용자 질의
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시에서 로드한 DB 설명 (선택)
        fault_diagnosis_enabled: 장애 진단 pull 위임 옵트인 (CW-B). True일 때만
            프롬프트에 fault_diagnosis 의도 섹션을 노출한다(off면 비트동일).

    Returns:
        분류 결과 목록
    """
    system_prompt = _build_router_prompt(
        domains,
        db_descriptions=db_descriptions,
        fault_diagnosis_enabled=fault_diagnosis_enabled,
    )

    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=query))

    response = await llm.ainvoke(messages)
    parsed = extract_json_from_response(response.content)

    if not parsed:
        return {"intent": "data_query", "databases": []}

    # 의도 추출 (cache_management, alarm_query, data_query)
    intent = parsed.get("intent", "data_query")

    if "databases" not in parsed:
        return {"intent": intent, "databases": []}

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

    return {"intent": intent, "databases": results}


def _render_location_vocab() -> str:
    """라우팅 프롬프트에 넣을 위치/환경 어휘 나열을 레지스트리에서 렌더한다(Plan 67 R2).

    sub_query_context에 위치어를 넣지 말라는 규칙이 참조하는 어휘 목록이며, 신규 DB
    편입 시 자동 반영되도록 프롬프트에 사본을 두지 않는다.
    """
    return ", ".join(get_registry().location_signal_terms())


def _render_location_db_examples() -> str:
    """"<위치> 알람" → db_id 예시를 레지스트리에서 렌더한다(DB당 대표 표면어 1개)."""
    hints = get_registry().location_db_hints()
    parts = [
        f'"{terms[0]} 알람" → {db_id}'
        for db_id, terms in hints.items()
        if terms
    ]
    return ", ".join(parts)


def _build_router_prompt(
    domains: list[DBDomainConfig],
    *,
    db_descriptions: dict[str, str] | None = None,
    fault_diagnosis_enabled: bool = False,
) -> str:
    """활성 도메인 기반으로 라우팅 프롬프트를 동적 생성한다.

    db_descriptions가 제공되면 각 DB 설명에 캐시된 상세 설명을 추가하여
    LLM의 DB 분류 정확도를 향상시킨다.

    Args:
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시에서 로드한 DB 설명 매핑 (선택)
        fault_diagnosis_enabled: True일 때만 fault_diagnosis 의도 섹션을 덧붙인다 (CW-B).
            off면 프롬프트가 기존과 비트동일하여 LLM이 fault_diagnosis를 산출하지 않는다.

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
    prompt = SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        db_list=db_list,
        location_vocab=_render_location_vocab(),
        location_db_examples=_render_location_db_examples(),
    )
    # (Plan 64 CW-B) 옵트인 on일 때만 fault_diagnosis 의도 섹션을 덧붙인다(off면 비트동일).
    if fault_diagnosis_enabled:
        prompt += SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION.format()
    return prompt


