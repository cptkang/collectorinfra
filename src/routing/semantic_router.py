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
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.utils.synonym_set_parser import parse_synonym_set
from src.config import AppConfig, load_config
from src.clients.fabrix_kbgenai import KBGenAIChat
from src.llm import create_llm
from src.prompts.semantic_router import (
    INTENTS_WITHOUT_DATABASES,
    SEMANTIC_ROUTER_FAULT_DIAGNOSIS_CLASS_LINE,
    SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION,
    SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE,
    SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE,
    STAGE2_INTENT_SECTIONS,
    allowed_intents,
    SEMANTIC_ROUTER_FAULT_DIAGNOSIS_CLASS_LINE,
    SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION,
    SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE,
)
from src.routing.domain_config import DB_DOMAINS, DBDomainConfig
from src.routing.registry import get_registry
from src.state import AgentState
from src.clients.instructor_adapter import StructuredOutputError, try_structured_call
from src.routing.schemas import DatabaseSelection, IntentDecision, RouterDecision
from src.utils.json_extract import extract_json_from_response
from src.utils.query_gen_common import (
    ZONE_SKIP_SIGNAL_TERMS,
    build_zone_clarification,
    has_host_identifier_filter,
)

logger = logging.getLogger(__name__)

# 라우팅 결과에 포함할 최소 관련도 점수.
#
# ⚠ **잠정값이다** (plans/79 §8 ⑧ · plans/80 S-3). 이 0.3은 근거를 갖고 도출된 값이 아니라
# **LLM 자기보고 스케일 기준의 관성값**이며, 트랙 A(A-1 규칙 5 제거)로 저신뢰 후보가 실제로
# 출력되기 시작하면서 **이 게이트가 처음으로 실동작**하게 됐다.
#
# 정산은 트랙 C-4에서 한다 — logprob 신뢰도로 스케일이 바뀌면 이 임계도 함께 재설계해야 한다.
# 트랙 C는 현재 라우터 평면 이동 후로 이월돼 있다(FabriX KBGenAI = logprobs 원천 불가).
# 그때까지 **값을 임의로 조정하지 않는다**. 조정하려면 relevance_score 분포 실측(WU-06)이 먼저다.
#
# 동일 임계를 orchestration/subagents.py:166도 쓴다 — 변경 시 **양쪽 대칭 적용** 필수.
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

    # [우선순위 2.5] 존 역질문에서 사용자가 체크박스로 확정한 DB 목록 (Plan 75 §4).
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


def _structured_backend() -> str:
    """구조화 출력 백엔드 설정을 읽는다(기동 시 로드된 설정 · P14)."""
    try:
        return getattr(load_config(), "structured_output_backend", "none")
    except Exception:  # noqa: BLE001 — 설정 부재가 라우팅을 막으면 안 된다
        return "none"


def _structured_max_retries() -> int:
    try:
        return int(getattr(load_config(), "structured_output_max_retries", 1))
    except Exception:  # noqa: BLE001
        return 1


def _validate_intent(raw: Any, *, fault_diagnosis_enabled: bool) -> str:
    """LLM이 산출한 intent를 허용 집합과 대조하고, 미상이면 data_query로 강등한다.

    허용 집합은 프롬프트 클래스 정의와 **단일 출처를 공유**한다(D-053 사본 금지).
    옵트인 클래스(fault_diagnosis)는 플래그에 종속된다 — off일 때 통과시키면 그래프에
    해당 노드가 없는 상태로 라우팅된다(Plan 64 CW-B · plans/80 계약 C-A).

    주의: 검증 대상은 **LLM이 산출한 intent**뿐이다. 노드가 반환하는 routing_intent에는
    코드가 만드는 값(zone_clarification 등)이 있고 그것은 대조 대상이 아니다.

    Args:
        raw: LLM 응답의 intent 값
        fault_diagnosis_enabled: 장애 진단 옵트인 여부

    Returns:
        허용 집합에 속하는 intent 문자열 (미상이면 "data_query")
    """
    allowed = allowed_intents(fault_diagnosis_enabled=fault_diagnosis_enabled)
    if isinstance(raw, str) and raw in allowed:
        return raw
    logger.warning(
        "라우터 intent 미상 — data_query로 강등: intent=%r (허용=%s)",
        raw,
        sorted(allowed),
    )
    return "data_query"


def _coerce_relevance_score(raw: Any) -> Optional[float]:
    """relevance_score를 float로 강제한다. 실패는 None(=판정 불가).

    **임의 기본값을 부여하지 않는다.** 형식 오류는 "관련도 0.5"가 아니라 판정 불가이고,
    기본값을 주면 MIN_RELEVANCE_SCORE 게이트를 그냥 통과해 버린다(plans/79 트랙 E-2).
    값 누락도 같다 — 프롬프트가 필수로 요구하는 필드이므로 부재는 형식 오류다.

    Args:
        raw: LLM이 준 relevance_score 값

    Returns:
        float 값, 또는 판정 불가 시 None
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


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
    # 2단 분리 위임 (Plan 79 트랙 B / WU-D2). **기본 off** — off면 아래 단일 호출 경로가
    # 종전과 비트동일하게 실행된다. 켜는 판정은 S-1·S-2 이후다(SPEC 「미검증으로 남는 것」).
    if load_config().router.two_stage_enabled:
        return await _llm_classify_two_stage(
            llm, query, domains,
            db_descriptions=db_descriptions,
            fault_diagnosis_enabled=fault_diagnosis_enabled,
        )

    system_prompt = _build_router_prompt(
        domains,
        db_descriptions=db_descriptions,
        fault_diagnosis_enabled=fault_diagnosis_enabled,
    )

    messages: list[BaseMessage] = [SystemMessage(content=system_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=query))

    # 구조화 출력 경로 (E-3b · D-169). off면 None → 기존 파싱으로 강등.
    # E-1·E-2 코드 가드를 **대체하지 않는다** — off 경로가 상시 존재하고 거기엔 F1·F2가 그대로다.
    parsed: Optional[dict] = None
    try:
        model = await try_structured_call(
            llm, messages, RouterDecision,
            backend=_structured_backend(),
            max_retries=_structured_max_retries(),
        )
        if model is not None:
            parsed = model.model_dump()
    except StructuredOutputError as e:
        logger.warning(
            "라우터 구조화 분류 실패(%d회 시도) — 기존 파싱으로 강등: %s", e.attempts, e
        )

    if parsed is None:
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content)

    if not parsed:
        return {"intent": "data_query", "databases": []}

    # 의도 추출 — 허용 집합과 대조한다 (E-1 · Plan 79 §3.6 발견 ⑦).
    # 종전에는 parsed["intent"]가 그대로 흘러, 오타·환각 intent가 하류의 동등 비교
    # (`intent == "cache_management"` 등)에 걸리지 않고 **조용히 DB 조회 경로로 낙하**했다.
    intent = _validate_intent(
        parsed.get("intent", "data_query"),
        fault_diagnosis_enabled=fault_diagnosis_enabled,
    )

    if "databases" not in parsed:
        return {"intent": intent, "databases": []}

    results, dropped = _validate_db_entries(parsed["databases"], domains, query)
    return {"intent": intent, "databases": results, "dropped": dropped}


def _validate_db_entries(
    entries: Any, domains: list[DBDomainConfig], query: str
) -> tuple[list[dict], list[dict]]:
    """DB 후보 항목을 활성 도메인 기준으로 검증한다 (E-2 · Plan 79 §3.6 발견 ⑧).

    한 항목의 형식 오류로 분류 전체를 버리지 않는다 — 종전에는 float("높음")이
    ValueError를 내고 호출부 except가 삼켜 단일 DB 폴백으로 갔고, 이는 임계와 무관하게
    멀티 DB 선택(plans/79 §1.1 불변식)이 축소되는 경로였다.

    **단일 호출 경로와 2단 분리 경로가 이 함수 하나를 공유한다**(WU-D2) — 갈라 두면 E-2가
    한쪽에만 적용된다(Known Mistakes: 단일/멀티 경로 비대칭이 반복 원인).

    Args:
        entries: LLM이 낸 databases 목록(형태 미검증)
        domains: 활성 DB 도메인 목록
        query: 사용자 질의(sub_query_context 기본값)

    Returns:
        (검증 통과 항목, 탈락 항목 `{db_id, reason, ...}`)
    """
    valid_db_ids = {d.db_id for d in domains}
    results: list[dict] = []
    dropped: list[dict] = []

    for db_entry in entries or []:
        if not isinstance(db_entry, dict):
            dropped.append({"db_id": "", "reason": "not_a_mapping", "raw": repr(db_entry)})
            continue
        db_id = db_entry.get("db_id", "")
        if db_id not in valid_db_ids:
            dropped.append({"db_id": db_id, "reason": "unknown_db_id"})
            continue

        score = _coerce_relevance_score(db_entry.get("relevance_score"))
        if score is None:
            dropped.append({
                "db_id": db_id,
                "reason": "invalid_relevance_score",
                "raw": repr(db_entry.get("relevance_score")),
            })
            continue

        results.append({
            "db_id": db_id,
            "relevance_score": score,
            "sub_query_context": db_entry.get("sub_query_context", query),
            "user_specified": bool(db_entry.get("user_specified", False)),
            "reason": db_entry.get("reason", ""),
        })

    if dropped:
        # 침묵 탈락 금지 — "모델이 못 골랐다"와 "모델이 환각했다"를 구분할 수 있어야 한다.
        logger.warning("라우터 후보 탈락 %d건: %s", len(dropped), dropped)

    return results, dropped


# ══════════════════════════════════════════════════════════════════════════
# 2단 분리 경로 (Plan 79 트랙 B / WU-D2 · `ROUTER_TWO_STAGE_ENABLED`)
# ══════════════════════════════════════════════════════════════════════════
#
# ⚠ **구조를 세운 것이지 켜도 된다고 판정한 것이 아니다**(SPEC 「미검증으로 남는 것」).
# 플래그 off가 기본이며, 켜는 판정은 S-1·S-2(plans/80 WU-05·06) 이후에 속한다.


def _intent_confidence(raw: Any, *, source: str) -> Optional[float]:
    """1단계 의도 신뢰도를 얻는다 (Plan 79 B-1-5 · 트랙 C 교체점).

    **여기가 유일한 교체점이다.** 라우터 평면 이동 후 `source="logprob"`이 되면 첫 토큰(=라벨)의
    logprob을 읽도록 이 함수만 바꾼다 — 호출부는 그대로다. 교체점을 한 곳에 모아 두는 것이
    트랙 C 재개 비용을 줄인다.

    `self_report`는 **잠정**이다: 모델이 스스로 매긴 값이라 교정 기반이 없다(SPEC M-3).

    Args:
        raw: 1단계가 낸 확신도 원값
        source: "self_report" | "logprob"

    Returns:
        0.0~1.0 신뢰도, 판정 불가면 None
    """
    if source == "logprob":
        # 트랙 C(vLLM) 이후 발효. 현행 워커 평면(FabriX KBGenAI)은 logprobs 원천 불가라
        # 여기 도달하면 설정 오류다 — 조용히 자기보고로 강등하지 않고 사유를 남긴다.
        logger.warning(
            "ROUTER_CONFIDENCE_SOURCE=logprob이나 현행 평면은 logprobs를 제공하지 않는다 "
            "— 신뢰도 미산출(트랙 C는 라우터 평면 이동 후 · plans/79 §8 ⑪)"
        )
        return None
    return _coerce_relevance_score(raw)


def _parse_stage1(text: str) -> tuple[str, Optional[float]]:
    """1단계 응답에서 (라벨, 자기보고 확신도)를 뽑는다.

    출력 계약은 **첫 줄 라벨 + 둘째 줄 JSON**이다. 첫 줄만 보면 되므로 JSON 파싱 실패가
    라벨을 버리지 않는다 — E-2(항목 단위 격리)와 같은 원칙이다.

    Args:
        text: 1단계 LLM 응답 원문

    Returns:
        (라벨 원문, 확신도 원값 또는 None)
    """
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return "", None
    # 모델이 코드펜스를 붙이는 경우를 결정적으로 벗긴다(형식 강제가 완벽하지 않다는 전제).
    label = lines[0].strip("`").strip().strip('"').strip("'")
    confidence: Optional[float] = None
    if len(lines) > 1:
        parsed = extract_json_from_response("\n".join(lines[1:]))
        if isinstance(parsed, dict):
            confidence = parsed.get("confidence")
    return label, confidence


def _stage2_intent_section(intent: str, *, fault_diagnosis_enabled: bool) -> str:
    """확정된 intent에 해당하는 판단 근거 절을 고른다 (B-2 완화).

    2단계는 1단계의 내부 표현을 볼 수 없다 — 그 손실을 줄이려고 **해당 intent의 절만**
    넘긴다. 전량을 넘기면 프롬프트 축소 이득(B-0 ③)이 사라진다.
    이 완화의 효과는 **미측정**이다(SPEC M-2).
    """
    if intent == "fault_diagnosis":
        # 옵트인 클래스는 플래그가 켜졌을 때만 노출한다(계약 C-A).
        return SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION if fault_diagnosis_enabled else ""
    return STAGE2_INTENT_SECTIONS.get(intent, "")


async def _llm_classify_two_stage(
    llm: BaseChatModel,
    query: str,
    domains: list[DBDomainConfig],
    *,
    db_descriptions: dict[str, str] | None = None,
    fault_diagnosis_enabled: bool = False,
) -> dict:
    """intent와 DB를 **두 번의 호출**로 분리해 분류한다 (Plan 79 트랙 B).

    ```
    1단계 intent 분류 (라벨 하나 · 첫 토큰이 라벨 → C-0)
       ├─ DB가 필요 없는 의도        → 2단계 호출 없음 (Q3)
       ├─ 조기 차단(저신뢰 · 기본 off) → 2단계 호출 없음 (B-2-1)
       └─ 그 외                      → 2단계 DB 선택 (LLM · D-004)
    ```

    **2단계도 LLM이다**(D-004 · B-0-1). 위치 힌트로 DB를 결정적으로 고르는 것은
    `config/db_registry.yaml:22-25`가 금지한다 — 힌트는 폴백·보강뿐이다.

    Args:
        llm: LLM 인스턴스
        query: 사용자 질의
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시 DB 설명 (선택)
        fault_diagnosis_enabled: 장애 진단 옵트인 (계약 C-A)

    Returns:
        `_llm_classify`와 **동일 형태** — {intent, databases, dropped, (+ two_stage 메타)}.
        호출부가 단일/2단을 구분하지 않아도 되게 한다.
    """
    cfg = load_config().router

    # ── 1단계: intent ────────────────────────────────────────────────
    # 옵트인 클래스는 **두 자리 모두** 조건부다(계약 C-A · A-5) — 정의 줄만 남겨도 LLM이
    # 그 클래스를 알게 되는데 off 상태에는 그래프에 해당 노드가 없다.
    stage1_prompt = SEMANTIC_ROUTER_STAGE1_INTENT_TEMPLATE.format(
        fault_diagnosis_class_line=(
            SEMANTIC_ROUTER_FAULT_DIAGNOSIS_CLASS_LINE if fault_diagnosis_enabled else ""
        ),
        fault_diagnosis_section=(
            SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION if fault_diagnosis_enabled else ""
        ),
        location_db_examples=_render_location_db_examples(),
    )
    messages: list[BaseMessage] = [SystemMessage(content=stage1_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=query))

    raw_label, raw_confidence = "", None
    model = None
    try:
        model = await try_structured_call(
            llm, messages, IntentDecision,
            backend=_structured_backend(),
            max_retries=_structured_max_retries(),
        )
    except StructuredOutputError as e:
        logger.warning(
            "라우터 1단계 구조화 분류 실패(%d회 시도) — 라벨 파싱으로 강등: %s", e.attempts, e
        )
    if model is not None:
        raw_label, raw_confidence = model.intent, model.confidence
    else:
        response = await llm.ainvoke(messages)
        raw_label, raw_confidence = _parse_stage1(response.content)

    intent = _validate_intent(
        raw_label or "data_query", fault_diagnosis_enabled=fault_diagnosis_enabled
    )
    confidence = _intent_confidence(raw_confidence, source=cfg.confidence_source)

    meta: dict[str, Any] = {
        "two_stage": True,
        "intent_confidence": confidence,
        "confidence_source": cfg.confidence_source,
        "stage2_called": False,
    }

    # ── 2단계 진입 판정 ──────────────────────────────────────────────
    # ① DB를 고를 대상이 아예 없는 의도 — 신뢰도와 무관하게 확실한 근거다(Q3).
    if intent in INTENTS_WITHOUT_DATABASES:
        meta["stage2_skipped_reason"] = "intent_without_databases"
        logger.info("라우터 2단: intent=%s → DB 선택 불필요(호출 1회)", intent)
        return {"intent": intent, "databases": [], "dropped": [], **meta}

    # ② 조기 차단(B-2-1) — **기본 off**. 임계 미설정이면 차단하지 않는다.
    # 자기보고 확신도에는 교정 기반이 없어(SPEC M-3) 근거 없는 임계를 상시 동작시키지 않는다.
    if (
        cfg.early_stop_enabled
        and cfg.min_confidence is not None
        and confidence is not None
        and confidence < cfg.min_confidence
    ):
        meta["stage2_skipped_reason"] = "low_intent_confidence"
        logger.info(
            "라우터 2단 조기 차단: intent=%s confidence=%.3f < %.3f (2단계 호출 없음)",
            intent, confidence, cfg.min_confidence,
        )
        return {"intent": intent, "databases": [], "dropped": [], **meta}

    # ── 2단계: DB 선택 ───────────────────────────────────────────────
    stage2_prompt = SEMANTIC_ROUTER_STAGE2_DATABASE_TEMPLATE.format(
        db_list=_render_db_list(domains, db_descriptions=db_descriptions),
        location_vocab=_render_location_vocab(),
        confirmed_intent=intent,
        intent_section=_stage2_intent_section(
            intent, fault_diagnosis_enabled=fault_diagnosis_enabled
        ),
    )
    messages2: list[BaseMessage] = [SystemMessage(content=stage2_prompt)]
    if isinstance(llm, KBGenAIChat):
        messages2.append(AIMessage(content=""))
    messages2.append(HumanMessage(content=query))

    meta["stage2_called"] = True
    parsed2: Optional[dict] = None
    try:
        selection = await try_structured_call(
            llm, messages2, DatabaseSelection,
            backend=_structured_backend(),
            max_retries=_structured_max_retries(),
        )
        if selection is not None:
            parsed2 = selection.model_dump()
    except StructuredOutputError as e:
        logger.warning(
            "라우터 2단계 구조화 분류 실패(%d회 시도) — 기존 파싱으로 강등: %s", e.attempts, e
        )
    if parsed2 is None:
        response2 = await llm.ainvoke(messages2)
        parsed2 = extract_json_from_response(response2.content) or {}

    # 항목 단위 검증은 단일 경로와 **같은 함수**를 쓴다 — 두 경로가 갈라지면 E-2가 한쪽에만
    # 적용된다(Known Mistakes: 단일/멀티 경로 비대칭이 반복 원인).
    databases, dropped = _validate_db_entries(parsed2.get("databases"), domains, query)
    return {"intent": intent, "databases": databases, "dropped": dropped, **meta}


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


def _render_db_list(
    domains: list[DBDomainConfig], *, db_descriptions: dict[str, str] | None = None
) -> str:
    """활성 DB 목록 블록을 렌더한다.

    단일 호출 프롬프트와 **2단 분리의 2단계 프롬프트가 같은 블록을 쓴다**(D-053) —
    사본을 두면 신규 DB 편입 시 한쪽만 반영된다.

    Args:
        domains: 활성 DB 도메인 목록
        db_descriptions: Redis 캐시에서 로드한 DB 상세 설명 (선택 · **덧붙이기만** 한다)

    Returns:
        프롬프트에 넣을 DB 목록 문자열
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
    return "\n\n".join(db_desc_list)


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
    db_list = _render_db_list(domains, db_descriptions=db_descriptions)
    # (Plan 64 CW-B · Plan 79 A-5) fault_diagnosis는 옵트인이므로 **클래스 정의 줄과 절 본문
    # 모두** 조건부로 주입한다. 종전에는 절을 프롬프트 맨 뒤에 append했으나, 그 절이 스스로
    # "최우선 검토"라고 선언하면서 「intent 판단 우선순위」보다 뒤에 놓이는 모순이 있었다.
    # 플레이스홀더 방식으로 바꿔 선언과 배치를 일치시킨다(off면 두 자리 모두 빈 문자열).
    fault_class_line = (
        SEMANTIC_ROUTER_FAULT_DIAGNOSIS_CLASS_LINE if fault_diagnosis_enabled else ""
    )
    fault_section = (
        SEMANTIC_ROUTER_FAULT_DIAGNOSIS_SECTION if fault_diagnosis_enabled else ""
    )
    return SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE.format(
        db_list=db_list,
        location_vocab=_render_location_vocab(),
        location_db_examples=_render_location_db_examples(),
        fault_diagnosis_class_line=fault_class_line,
        fault_diagnosis_section=fault_section,
    )


