"""subagent 정의 및 실행 헬퍼 (Plan 48, deepagents SubAgent 미들웨어 대응).

기존 노드/파이프라인을 얇은 래퍼(handler)로 캡슐화하여 SUBAGENT_REGISTRY로 일원화한다.
신규 비즈니스 로직은 추가하지 않으며, 모든 위임은 registry를 통해 분기된다.

구성:
- SubAgentSpec / SUBAGENT_REGISTRY: deepagents SubAgent dict 스키마에 정합하는 정의 테이블.
- classify_dbs: 기존 semantic_router._llm_classify의 DB 분류부만 재사용.
- _run_single_db_pipeline: 단일 DB 풀 검증·재시도 루프(graph.py 라우팅 로직 이식).
- _make_isolated_input: subagent에 전달할 필터된 얇은 컨텍스트(S3 부분 격리).
- run_* handler들: 기존 동명 노드를 sub_query 입력으로 호출.

본 모듈은 tool-calling을 사용하지 않는다(handler 디스패치는 코드 기반).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.config import AppConfig, load_config
from src.nodes.cache_management import cache_management
from src.nodes.field_mapper import resolve_priority_db_ids
from src.nodes.general_inference import general_inference
from src.nodes.input_parser import LOCATION_HINT_TERMS
from src.nodes.multi_db_executor import multi_db_executor
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.realtime_usage import realtime_usage_lookup
from src.nodes.result_merger import result_merger
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import schema_analyzer
from src.nodes.synonym_registrar import synonym_registrar
from src.orchestration.process_query import (
    _DEMONSTRATIVE_NOUNS,
    _DEMONSTRATIVE_PREFIXES,
    _HOST_FIELDS,
    _is_demonstrative_value,
    _resolve_hostname,
    run_process_query,
)
from src.orchestration.host_inspect import HOST_INSPECT_AGENT, run_host_inspect
from src.routing.domain_config import DB_DOMAINS, get_domain_by_id
from src.routing.registry import get_registry
from src.routing.semantic_router import MIN_RELEVANCE_SCORE, _llm_classify
from src.utils.prior_targets import build_prior_targets
from src.utils.query_gen_common import (
    build_zone_clarification,
    has_host_identifier_filter,
    is_realtime_usage_query,
    is_server_identity_col,
    refers_to_demonstrative_server,
    resolve_query_limit,
)

logger = logging.getLogger(__name__)

# 단일 DB 파이프라인 재시도 루프 무한루프 방지용 명시적 반복 상한
_MAX_PIPELINE_STEPS = 10
# input_from 선행 결과 주입 시 행수 상한 (R-12: 토큰·IN 절 폭증 방지)
_MAX_PRIOR_ROWS = 100
# 추론 agent(general_inference)에 전달할 이전 대화 메시지 상한 (①, 토큰 폭증 방지).
# general_inference가 재차 [-10:]로 자르므로 상한은 보수적으로만 둔다.
_MAX_HISTORY_MESSAGES = 10
# 식별 키 컬럼 추출 우선순위 (보수적: 식별성 높은 컬럼 우선)
# 서버 식별 컬럼 판정은 query_gen_common.is_server_identity_col로 공용화(D-100).
# 이번 턴 질의에서 "새 위치/DB 신호"로 인정할 키워드 (M2 — DB 승계 차단 조건).
# 사용자가 이번 턴에 아래 신호를 명시하면 직전 DB를 승계하지 않고 분류 결과를 따른다.
# 정본은 `config/db_registry.yaml`(locations + environment_terms + 제품/DB signal_terms)
# — Plan 67 R2, 사본 금지. D-004 경계: 의도 분류가 아니라 승계 차단 판정에만 쓴다.
_LOCATION_DB_SIGNALS = get_registry().new_db_signal_terms()


# ──────────────────────────────────────────────
# SubAgent 정의
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class SubAgentSpec:
    """subagent 정의 (deepagents SubAgent dict와 1:1 대응).

    Attributes:
        name: task() 호출 시 식별자
        description: planner 위임 분류 근거 (SubAgent S2)
        handler: 실행 진입점 (기존 노드/파이프라인 래퍼)
        model: per-agent 모델 슬롯 — Phase 7 예약 (None=메인 LLM 사용)
        prompt: per-agent 프롬프트 슬롯 — Phase 7 예약
        fallback: general-purpose(미분류 시 기본) 여부
        needs_history: 이전 대화 이력(messages)을 격리 입력에 전달할지 여부(①).
            추론/판단형 응답(general_inference)은 직전 턴 답변을 근거로 삼아야 하므로 True.
            데이터 조회 agent(data_query/process_query)는 이력이 SQL 생성에 불필요·토큰
            부담·오염 위험이 있어 False(격리 유지).
    """

    name: str
    description: str
    handler: Callable
    model: Optional[BaseChatModel] = None
    prompt: Optional[str] = None
    fallback: bool = False
    needs_history: bool = False


# ──────────────────────────────────────────────
# DB 분류 재사용
# ──────────────────────────────────────────────

async def classify_dbs(
    llm: BaseChatModel,
    sub_query: str,
    app_config: AppConfig,
) -> list[dict]:
    """기존 semantic_router._llm_classify의 DB 분류부만 호출하여 대상 DB를 결정한다.

    intent 분류는 무시하고 target_databases 형태 리스트만 반환한다.

    Args:
        llm: LLM 인스턴스
        sub_query: 해당 task의 자연어 지시
        app_config: 앱 설정

    Returns:
        target_databases 형태 dict 리스트
        ({db_id, relevance_score, sub_query_context, user_specified, reason})
    """
    active_db_ids = app_config.multi_db.get_active_db_ids()

    # 활성 DB가 없으면 레거시 단일 DB 폴백
    if not active_db_ids:
        return [
            {
                "db_id": "default",
                "relevance_score": 1.0,
                "sub_query_context": sub_query,
                "user_specified": False,
                "reason": "레거시 단일 DB 모드",
            }
        ]

    active_domains = [d for d in DB_DOMAINS if d.db_id in active_db_ids]

    # DB 설명(Redis 캐시)을 라우팅 프롬프트에 주입하여 분류 정확도를 높인다.
    # (기존 semantic_router와 동일 — §4.9.6 보완. 누락 시 위치/도메인 라우팅 정확도 저하)
    db_descriptions: dict[str, str] = {}
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        cache_mgr = get_cache_manager(app_config)
        db_descriptions = await cache_mgr.get_db_descriptions()
    except Exception as e:
        logger.debug("classify_dbs DB 설명 로드 실패 (분류 계속): %s", e)

    try:
        classified = await _llm_classify(
            llm, sub_query, active_domains, db_descriptions=db_descriptions
        )
    except Exception as e:
        logger.error("classify_dbs LLM 분류 실패, 첫 활성 DB 폴백: %s", e)
        classified = {"intent": "data_query", "databases": []}

    databases = classified.get("databases", []) if isinstance(classified, dict) else []

    targets = [r for r in databases if r.get("relevance_score", 0.0) >= MIN_RELEVANCE_SCORE]
    targets.sort(key=lambda x: x["relevance_score"], reverse=True)

    # 결과가 없으면 첫 번째 활성 DB 사용
    if not targets:
        logger.warning("classify_dbs 결과 없음, 첫 활성 DB 사용: %s", active_db_ids[0])
        targets = [
            {
                "db_id": active_db_ids[0],
                "relevance_score": 0.5,
                "sub_query_context": sub_query,
                "user_specified": False,
                "reason": "DB 분류 결과 없음, 기본 DB 사용",
            }
        ]

    return targets


def _has_new_location_db_signal(text: str) -> bool:
    """이번 턴 질의에 새 위치/DB 신호가 있는지 표면 검사한다 (M2 — 승계 차단 조건).

    사용자가 이번 턴에 위치(김포/여의도 등)·DB명(polestar 등)·환경을 명시하면
    직전 DB를 승계하지 않고 이번 분류 결과를 따라야 한다(리스크 표: 새 의도를 덮어쓰지 않음).

    Args:
        text: 이번 턴 sub_query(또는 user_query)

    Returns:
        새 위치/DB 신호가 있으면 True
    """
    if not text:
        return False
    lowered = text.lower()
    return any(sig.lower() in lowered for sig in _LOCATION_DB_SIGNALS)


def _zone_clarification_or_none_task(
    task: dict,
    isolated: dict,
    targets: list[dict],
    *,
    db_pinned: bool,
    db_succeeded: bool,
    app_config: AppConfig,
) -> Optional[dict]:
    """존 역질문 후단 게이트 판정 (D-143 후속2 — 텍스트 경로 파이프라인 내 결정적 게이트).

    라우트 pre-gate(표면어 "서버"+전량 조회 필수)가 놓치는 형태 — 위치어 없는 존 단위
    조회("OS 버전 확인하시오" 등)가 LLM 임의 팬아웃(전 존/임의 존)으로 흐르는 것을,
    파싱·분류가 끝난 시점의 실제 신호로 판정해 존 선택 역질문으로 전환한다.
    §4.2 비발동 목록을 결정적 조건으로 옮긴 것: 서버명 지목·승계 가능·존 무관 질의는
    비발동, 비대화 채널(zone_clarification_allowed=False)은 항상 비발동.

    Args:
        task: 현재 TaskSpec
        isolated: 격리 입력(zone_clarification_allowed/parsed_requirements 포함)
        targets: 분류·핀·승계가 끝난 대상 DB 목록
        db_pinned: 이번 턴 위치 힌트 결정적 고정 여부
        db_succeeded: 직전 턴 DB 승계 여부
        app_config: 앱 설정

    Returns:
        발동 시 clarification 페이로드, 비발동이면 None
    """
    # 채널 게이트: 대화형 텍스트 라우트만 허용(§4.3-3 — 배치·평가·API 직접 호출 보호)
    if not isolated.get("zone_clarification_allowed"):
        return None
    # data_query 전용 (alarm_query 등은 기존 폴백 유지 — 스코프 최소화)
    if task.get("agent", "data_query") != "data_query":
        return None
    # 복합 계획 제외(중간 task 역질문은 UX 어색 + 전역 판정 부정확 — 핀 게이트와 동일 사유)
    if isolated.get("is_composite"):
        return None
    # 위치 힌트 고정·승계가 발동했으면 존은 이미 결정적(§4.2 승계 우선)
    if db_pinned or db_succeeded:
        return None
    # 첫 턴 한정: 직전 턴 DB가 있으면 승계 계열이 처리(분류가 직전 DB 1개로 수렴해
    # db_succeeded=False로 남는 경우 포함 — previous_db_ids 존재 자체를 비발동 조건으로)
    ctx = isolated.get("conversation_context") or {}
    if ctx.get("previous_db_ids"):
        return None
    # 이번 턴 원문·sub_query에 위치/DB 신호가 있으면 비발동(D-065 결정적 보강이 처리)
    original_query = isolated.get("original_user_query") or isolated.get("user_query", "")
    if _has_new_location_db_signal(original_query) or _has_new_location_db_signal(
        task.get("sub_query", "")
    ):
        return None
    parsed = isolated.get("parsed_requirements") or {}
    # 서버명 지목 질의는 존이 결과에 영향 없음(§4.2 ⓐ) — hostname으로 어차피 특정됨
    if has_host_identifier_filter(parsed):
        return None
    # 조회 대상 필드가 파싱되지 않은 질의(잡담성 오분류 등)는 비발동 — 과잉 역질문 방지
    if not parsed.get("query_targets"):
        return None
    # 대상이 전부 폴스타 존일 때만(비폴스타 대상은 존 선택이 무의미)
    polestar_ids = app_config.get_polestar_db_ids() or set()
    target_ids = [t.get("db_id") for t in targets if t.get("db_id")]
    if not target_ids or not all(d in polestar_ids for d in target_ids):
        return None
    payload = build_zone_clarification(
        app_config.multi_db.get_active_db_ids(), original_query,
        # 존 그룹 상호배타(D-143 후속3) — 라우트 pre-gate와 동일 UI 규칙
        group_exclusive=bool(
            getattr(app_config.multi_db, "zone_group_exclusive", True)
        ),
    )
    if payload:
        logger.info(
            "존 역질문 후단 게이트 발동(D-143 후속2): 위치어·서버 식별·승계 신호 없음 — "
            "LLM 임의 팬아웃(%s) 대신 존 선택 요청", target_ids,
        )
    return payload


def _refers_to_specific_server(text: str) -> bool:
    """질의가 지시어로 특정 서버 하나를 가리키는지 판정한다 (전체 조회 방지 게이트).

    "전체/모든/모두" 같은 전역 조회 신호가 있으면 False(서버 스코프 강제 금지).
    "해당/그/이/저/위/직전 … 서버/장비/노드/…" 같은 지시어 서버 참조가 있으면 True.

    Args:
        text: 판정 대상 질의 텍스트(보통 original_query)

    Returns:
        지시어로 특정 서버를 지목하면 True
    """
    # 구현은 utils.query_gen_common으로 이동(D-153 후속1 단일 출처 — intent_planner
    # 맥락 주입 게이트와 공유) — 동작 동일.
    return refers_to_demonstrative_server(text)


def _inject_demonstrative_hostname(isolated: dict) -> dict:
    """지시어("해당 서버") data/alarm 조회에 직전 서버 hostname을 filter_conditions로 주입한다.

    query_generator는 parsed_requirements(original_query + filter_conditions)로 SQL을
    생성하며 intent_planner가 확장한 sub_query(state.user_query)는 프롬프트에 사용하지 않는다.
    따라서 지시어 후속 질의는 filter_conditions가 비어(D-055) 서버 필터 없이 전체 조회
    (전체 서버/전체 알람)로 빠진다. concrete "webdb01 서버 알람"과 동일하게 filter에 hostname을
    채워, 이미 검증된 filter_conditions.hostname 경로로 서버 필터를 강제한다(D-056 후속).

    주입 조건(모두 충족):
    - 이번 턴 filter_conditions에 서버 식별 필터가 없음(concrete 질의면 그대로 둠).
    - original_query가 지시어로 특정 서버를 가리킴(전체 조회 신호 없음).
    - 직전 턴 서버(previous_entities)에서 지시어가 아닌 실제 hostname이 해소됨.

    Args:
        isolated: subagent 격리 입력(parsed_requirements/conversation_context 포함)

    Returns:
        (필요 시) hostname 필터가 추가된 parsed_requirements 사본, 아니면 원본 참조
    """
    parsed = isolated.get("parsed_requirements") or {}
    filters = parsed.get("filter_conditions") or []
    if any(
        isinstance(c, dict) and str(c.get("field", "")).lower() in _HOST_FIELDS
        for c in filters
    ):
        return parsed  # 이미 서버 식별 필터 있음(concrete 질의)
    if not _refers_to_specific_server(str(parsed.get("original_query", ""))):
        return parsed  # 특정 서버 지목 아님(전체 조회 등) — 스코프 강제 금지
    hostname = _resolve_hostname(isolated)
    if not hostname or _is_demonstrative_value(hostname):
        return parsed  # 직전 실제 서버 미해소
    new_parsed = dict(parsed)
    new_parsed["filter_conditions"] = [
        *filters,
        {"field": "hostname", "op": "=", "value": hostname},
    ]
    logger.info(
        "data_query 지시어 서버 해소: hostname=%s 를 filter_conditions에 주입(전체 조회 방지)",
        hostname,
    )
    return new_parsed


def _strip_location_terms(text: str) -> str:
    """질의 텍스트에서 위치/제품명 토큰을 제거한다(핀 대체 target의 정제 질의용).

    classify_dbs의 sub_query_context(위치 제거 정제 질의)가 없는 합성 target에 원문을
    그대로 넣으면 위치어가 SQL WHERE로 누출될 수 있어(과거 GROUP_PATH ILIKE '김포' 사례),
    위치·제품명 토큰만 결정적으로 걷어낸다. 긴 토큰부터 제거해 부분 잔재("은행존"→"존")를 막는다.
    """
    stripped = text or ""
    tokens = sorted(
        (*LOCATION_HINT_TERMS, "폴스타", "polestar"), key=len, reverse=True
    )
    for token in tokens:
        stripped = stripped.replace(token, " ")
    return " ".join(stripped.split())


def _apply_turn_hint_pinning(
    targets: list[dict],
    isolated: dict,
    sub_query: str,
    active_db_ids: list[str],
) -> tuple[list[dict], bool]:
    """이번 턴 원문 위치 힌트(target_db_hints)로 대상 DB 집합을 결정적으로 고정한다.

    DB 선택 우선순위 "① 이번 턴 명시 위치 > ② db_ids 고정 > ③ 승계 > ④ fan-out"에서
    ①의 판정이 종래 LLM 산출물(sub_query→classify_dbs)에 의존해, LLM이 직전 턴 위치를
    병합해 sub_query를 오염시키면("은행존 알람"→"김포 은행 공동존…") 오라우팅됐다
    (2026-07-16 실측). input_parser가 이번 턴 **원문**에서 결정적으로 추출한
    parsed_requirements.target_db_hints를 폼필과 동일한 해소 로직(resolve_priority_db_ids,
    D-065 계열)으로 DB 집합에 고정한다. classify_dbs 결과는 sub_query_context(위치 제거
    정제 질의) 재사용을 위해 유지하고 **DB 집합 결정권만** 이 함수가 갖는다.

    적용 게이트(모두 충족):
    - 단일 task 계획(is_composite 아님) — 전역 힌트는 task별 위치가 다른 복합 계획에 부정확.
    - 힌트가 활성 DB로 1개 이상 해소됨.

    Args:
        targets: classify_dbs가 반환한 후보 목록
        isolated: subagent 격리 입력(parsed_requirements/is_composite 포함)
        sub_query: 이번 task 질의(합성 target의 정제 질의 원료)
        active_db_ids: 활성 DB 목록

    Returns:
        (적용된 targets, 핀 적용 여부)
    """
    if isolated.get("is_composite"):
        return targets, False
    parsed = isolated.get("parsed_requirements") or {}
    hints = parsed.get("target_db_hints") or []
    if not isinstance(hints, list) or not hints:
        return targets, False
    pinned = resolve_priority_db_ids([str(h) for h in hints], active_db_ids)
    if not pinned:
        return targets, False

    by_id = {t.get("db_id"): t for t in targets if isinstance(t, dict)}
    final: list[dict] = []
    for db_id in pinned:
        existing = by_id.get(db_id)
        if existing:
            final.append(existing)  # classify의 sub_query_context 재사용
        else:
            final.append({
                "db_id": db_id,
                "relevance_score": 1.0,
                "sub_query_context": _strip_location_terms(sub_query),
                "user_specified": True,
                "reason": "이번 턴 원문 위치/DB 힌트 결정적 해소(target_db_hints)",
            })
    dropped = [i for i in by_id if i not in set(pinned)]
    logger.info(
        "이번 턴 위치 힌트 결정적 DB 고정: hints=%s → %s (classify 제외분=%s)",
        hints, pinned, dropped or "없음",
    )
    return final, True


def _apply_db_succession(
    targets: list[dict],
    sub_query: str,
    conversation_context: Optional[dict],
    active_db_ids: list[str],
) -> tuple[list[dict], bool]:
    """이전 턴 DB를 우선 후보로 승계한다 (M2, Plan 50 §3.2).

    우선순위: ① 이번 턴 명시 위치/DB > ② mapped_db_ids(호출 전 db_ids 고정) >
    ③ previous_db_ids(멀티턴 승계) > ④ 전체 후보 fan-out.

    본 함수는 ①(이번 턴 신호)이 없고 직전 DB가 있을 때만 승계를 적용한다.
    승계 조건:
      - 이번 턴 sub_query에 새 위치/DB 신호가 **없음**.
      - conversation_context.previous_db_ids 가 **활성 DB 중 1개 이상** 존재.
    승계 시 직전 DB를 단일 우선 후보로 좁혀 fan-out(다중 폴스타 오선택)을 방지한다.

    Args:
        targets: classify_dbs가 반환한 target_databases 목록
        sub_query: 이번 턴 SQL 생성 입력 질의
        conversation_context: context_resolver 보존 맥락(없으면 첫 턴)
        active_db_ids: 활성 DB 목록(승계 후보 유효성 검사용)

    Returns:
        (적용된 targets, 승계 적용 여부)
    """
    if not conversation_context:
        return targets, False
    prev_db_ids = conversation_context.get("previous_db_ids") or []
    if not prev_db_ids:
        return targets, False

    # ① 이번 턴에 새 위치/DB 신호가 있으면 승계하지 않는다(사용자 새 의도 최우선).
    if _has_new_location_db_signal(sub_query):
        return targets, False

    # 직전 DB 중 현재 활성인 것만 후보로 인정.
    valid_prev = [d for d in prev_db_ids if not active_db_ids or d in active_db_ids]
    if not valid_prev:
        return targets, False

    # 이미 분류 결과가 직전 DB 1개로 수렴했으면 승계 불필요.
    current_ids = [t.get("db_id") for t in targets]
    if len(targets) == 1 and current_ids[0] in valid_prev:
        return targets, False

    # 승계: 직전 DB(우선순위 1개)를 단일 우선 후보로 사용.
    succeeded_id = valid_prev[0]
    succeeded = [{
        "db_id": succeeded_id,
        "relevance_score": 1.0,
        "sub_query_context": sub_query,
        "user_specified": False,
        "reason": f"이전 턴 DB 승계(멀티턴, previous_db_ids={valid_prev})",
    }]
    logger.info(
        "data_query DB 승계 적용: %s (분류 결과 %s → 직전 DB 우선)",
        succeeded_id, current_ids,
    )
    return succeeded, True


def _normalize_targets(targets: list, sub_query: str) -> list[dict]:
    """db_ids(문자열 리스트) 또는 target dict 리스트를 target dict 형태로 정규화한다.

    Args:
        targets: 문자열 db_id 리스트 또는 target_databases dict 리스트
        sub_query: sub_query_context 기본값

    Returns:
        target_databases 형태 dict 리스트
    """
    normalized: list[dict] = []
    for t in targets:
        if isinstance(t, str):
            normalized.append({
                "db_id": t,
                "relevance_score": 1.0,
                "sub_query_context": sub_query,
                "user_specified": False,
                "reason": "필드 매핑 결과에서 식별된 DB",
            })
        elif isinstance(t, dict):
            normalized.append(t)
    return normalized


# ──────────────────────────────────────────────
# 단일 DB 파이프라인 (풀 검증·재시도 보존)
# ──────────────────────────────────────────────

async def _run_single_db_pipeline(
    s: dict,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """단일 DB 파이프라인을 재현한다 (graph.py 라우팅 로직을 함수 루프로 이식).

    흐름:
    schema_analyzer → query_generator → query_validator
      → (passed면 query_executor, 아니면 retry<3이면 query_generator 재시도 / >=3이면 에러 종료)
      → query_executor
      → (error 있고 retry<3이면 query_generator 회귀 / >=3이면 에러)
      → result_organizer (호출자가 별도 수행하므로 여기서는 executor까지)

    주의: result_organizer는 호출자(run_data_query_pipeline)에서 일괄 수행한다.
    여기서는 schema→generate→validate→execute 까지의 재시도 루프만 담당한다.

    Args:
        s: 입력 state dict (복사하여 사용)
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        파이프라인 실행으로 갱신된 state 필드 dict
    """
    state = dict(s)
    # SQL 재생성 재시도 예산 — config 단일 출처 (D-099, Plan 69 P0-⑧)
    _max_retry = app_config.query.max_retry_count if app_config else 3

    # 1) 스키마 분석 (1회)
    state.update(await schema_analyzer(state, llm=llm, app_config=app_config))

    steps = 0
    while steps < _MAX_PIPELINE_STEPS:
        steps += 1

        # 2) SQL 생성
        state.update(await query_generator(state, llm=llm, app_config=app_config))

        # 3) 검증
        state.update(await query_validator(state, app_config=app_config))
        if not state["validation_result"]["passed"]:
            if state.get("retry_count", 0) >= _max_retry:
                # 검증 실패 + 재시도 초과 → 에러 종료
                if not state.get("error_message"):
                    state["error_message"] = state["validation_result"].get(
                        "reason", "SQL 검증 실패"
                    )
                break
            # 재시도 가능 → query_generator 재진입
            continue

        # 4) 실행
        state.update(await query_executor(state, app_config=app_config))
        if state.get("error_message"):
            if state.get("retry_count", 0) >= _max_retry:
                break
            # 실행 에러 + 재시도 가능 → query_generator 회귀
            continue

        # 정상 실행 완료
        break

    return state


# ──────────────────────────────────────────────
# 격리 입력 컨텍스트 (S3 부분 격리)
# ──────────────────────────────────────────────

def _extract_identity_rows(rows: list[dict]) -> list[dict]:
    """선행 결과 행에서 식별 키 컬럼만·행수 상한을 적용해 추린다 (R-12).

    식별 키 컬럼(hostname/name/id류)이 있으면 해당 컬럼만, 없으면 전체 컬럼을
    유지하되 행수 상한을 적용한다.

    Args:
        rows: 선행 task 결과 행 목록

    Returns:
        식별 키 위주로 추린 행 목록 (최대 _MAX_PRIOR_ROWS)
    """
    if not rows:
        return []

    limited = rows[:_MAX_PRIOR_ROWS]
    first = limited[0]
    if not isinstance(first, dict):
        return limited

    # 식별 키 컬럼 식별 — 서버 식별 컬럼만 엄격 선택(alarm_name 등 비서버 컬럼 배제 — D-100).
    # 선행 조회가 서버명 외 컬럼(알람명·심각도 등)도 반환하면서 "name" 부분매칭이 alarm_name을
    # 서버 식별로 오수집해 후속 스코프 HAVING이 오염됐다(실측).
    key_cols = [col for col in first.keys() if is_server_identity_col(col)]

    if not key_cols:
        # 식별 키 없으면 전체 컬럼 유지 (행수 상한만)
        return limited

    return [{col: row.get(col) for col in key_cols} for row in limited if isinstance(row, dict)]


def _history_for_agent(task: dict, state: dict) -> list:
    """추론 agent(needs_history=True)에 한해 이전 대화 이력을 소량 반환한다 (①).

    데이터 조회 agent는 빈 리스트를 유지(격리)하고, general_inference처럼 직전 턴 답변을
    근거로 판단해야 하는 agent에만 트리밍된 messages를 전달한다. 호출 노드가 user_query를
    다시 HumanMessage로 덧붙이므로, 말미의 현재 턴 질의(HumanMessage)는 중복 방지를 위해
    제외한다. 상한(_MAX_HISTORY_MESSAGES)으로 토큰 폭증을 막는다.

    Args:
        task: 현재 TaskSpec (agent 명으로 needs_history 판정)
        state: 전체 에이전트 상태 (messages 원본)

    Returns:
        전달할 이전 대화 메시지 목록 (needs_history가 아니면 빈 리스트)
    """
    spec = SUBAGENT_REGISTRY.get(task.get("agent"))
    if not spec or not spec.needs_history:
        return []
    msgs = state.get("messages") or []
    if msgs and isinstance(msgs[-1], HumanMessage):
        # 현재 턴 질의는 노드가 user_query로 재부착하므로 제외(near-duplicate 방지)
        msgs = msgs[:-1]
    return list(msgs[-_MAX_HISTORY_MESSAGES:])


def _scope_parsed_requirements(state: dict, task: dict) -> dict:
    """parsed_requirements를 sub-task 스코프로 좁힌 사본을 만든다 (D-094).

    original_query만 task의 sub_query로 교체한다(사본 — 원본 state 비오염).
    time_range/query_targets 등 구조화 필드는 전체 질의 파싱값을 그대로 유지한다
    (SQL 생성 프롬프트의 보조 맥락 — 예: 기간 202606). 단일 task(sub_query==전체
    질의)면 실질 변화가 없다.

    Args:
        state: 전체 에이전트 상태
        task: 현재 TaskSpec

    Returns:
        스코프 적용된 parsed_requirements 사본
    """
    parsed = dict(state.get("parsed_requirements", {}) or {})
    sub_query = task.get("sub_query")
    if sub_query:
        parsed["original_query"] = sub_query
    return parsed


def _make_isolated_input(task: dict, state: dict, prior: dict) -> dict:
    """subagent에 전달할 필터된 얇은 컨텍스트를 만든다 (SubAgent S3 부분 격리).

    전체 AgentState를 넘기지 않고 실행에 필요한 필드 + 노드 KeyError 방지용 기본값만 포함한다.
    대형·히스토리 필드(원본 query_results/db_results/messages 누적분)는 제외한다.
    단, 추론 agent(needs_history=True)에는 직전 턴 답변 근거를 위해 트리밍된 messages를
    선별 전달한다(①). 선행 결과(input_from)는 식별 키 컬럼만·행수 상한으로 추려 prior_rows에 주입한다.

    Args:
        task: 현재 TaskSpec
        state: 전체 에이전트 상태
        prior: 지금까지 완료된 task 결과 {task_id: norm_result}

    Returns:
        필터된 isolated state dict
    """
    # 실행에 필요한 컨텍스트 필드 (얕은 복사)
    base: dict[str, Any] = {
        "user_query": state.get("user_query", ""),
        # 원문 기준 LIMIT 확정값 승격(Plan 75 §3 / D-066 후속): 호출부가 user_query를
        # sub_query(planner 재작성)로, 단일 DB 파이프라인이 다시 sub_query_context
        # (semantic_router 정제 — "모든" 등 수량 한정어까지 압축 탈락)로 교체해도,
        # 이 시점의 state.user_query(원문)로 계산한 limit이 state 필드로 살아남는다.
        # 실측(2026-07-24): 이 승격 부재로 은행존 "모든" 질의가 LIMIT 1000 절단(2,328→1,000).
        "resolved_limit": state.get("resolved_limit") or resolve_query_limit(
            state.get("user_query", ""), load_config().query.default_limit
        ),
        # orchestration 경로는 semantic_router를 타지 않아 routing_intent가 항상 None이었고,
        # alarm_query task도 일반 템플릿 + allowed_tables(알람 테이블 제외 필터)로 실행되는
        # 결함이 있었다(D-076 후속3). task agent에서 결정적으로 매핑해 그래프 경로와 대칭화한다
        # — alarm_query여야 schema_analyzer가 alarm_allowed_tables를 노출하고
        # query_generator가 알람 전용 템플릿을 쓴다.
        "routing_intent": "alarm_query" if task.get("agent") == "alarm_query" else None,
        # sub-task 스코프(D-094, D-092 ③의 생성측 대칭): query_generator._build_user_prompt는
        # "## 사용자 질의"로 parsed_requirements["original_query"]를 사용한다. 전체 질의를
        # 그대로 두면 sub_query에 담긴 제약(선행 결과로 해석된 서버명 IN (...) 등)이 아니라
        # 전체 질문에 대한 SQL을 생성하고, data_query는 알람 테이블 접근이 없어 "알람 서버 중"
        # 같은 조건을 침묵 탈락시킨다(2026-07-20 라이브 실측 — 전 서버 기준 오답).
        "parsed_requirements": _scope_parsed_requirements(state, task),
        "conversation_context": state.get("conversation_context"),
        "thread_id": state.get("thread_id"),
        "user_id": state.get("user_id"),
        "user_department": state.get("user_department"),
        # 조사 인가는 **실행 경계**(handler 내부)에서 판정하므로 여기를 통과해야 한다.
        # 빠뜨리면 role이 None이 되어 전 조사가 차단된다(fail-closed — 78 W3-5).
        "user_role": state.get("user_role"),
        "allowed_db_ids": state.get("allowed_db_ids"),
        "request_id": state.get("request_id"),
        "client_ip": state.get("client_ip"),
        "template_structure": state.get("template_structure"),
        # uploaded_file(원본 파일 바이너리)은 output_generator가 Excel/Word를 실제로 채우는 데
        # 필수다. template_structure만 전파하고 이 값을 빠뜨리면 "원본 파일 정보가 없어 채울 수
        # 없습니다"로 CSV 강등된다(비대칭 전파, D-053 계열).
        "uploaded_file": state.get("uploaded_file"),
        "target_sheets": state.get("target_sheets"),
        "file_type": state.get("file_type"),
        "csv_sheet_data": state.get("csv_sheet_data"),
        # 존 선택 고정(Plan 75 §4): intent_planner pre-check가 못 덮는 복합 계획의
        # 개별 task까지 run_data_query_pipeline에서 결정적 고정하도록 전파.
        "selected_db_ids": state.get("selected_db_ids"),
        # 실시간 사용률 의도(Plan 71, B안 게이트)는 **원문 기준**으로 판정해 승격 —
        # sub_query/sub_query_context 재작성으로 "실시간" 표면어가 탈락해도 유지
        # (resolved_limit과 동일 원리, D-066 후속7).
        "realtime_usage_intent": is_realtime_usage_query(state.get("user_query", "")),
        "mapped_db_ids": state.get("mapped_db_ids"),
        "db_column_mapping": state.get("db_column_mapping"),
        "column_mapping": state.get("column_mapping"),
        "mapping_sources": state.get("mapping_sources"),
        # HITL 폼필 답변(D-151, FIX-19): 라우트가 복원한 답변이 이 격리 경계를 통과해야
        # multi_db_executor/query_generator의 오버라이드 적용에 도달한다(라이브 실측
        # 2026-07-31: 누락 시 답변이 유실돼 동일 미해결 → 역질문 패널 무한 반복).
        "form_fill_answers": state.get("form_fill_answers"),
        "llm_inference_details": state.get("llm_inference_details"),
        "pending_synonym_registrations": state.get("pending_synonym_registrations"),
        "pending_synonym_reuse": state.get("pending_synonym_reuse"),
        # 이번 턴 원문 위치 힌트의 결정적 DB 고정(_apply_turn_hint_pinning)은 전역 힌트를
        # 쓰므로 단일 task 계획에서만 안전 — 복합 여부를 게이트 신호로 전달한다.
        "is_composite": bool(state.get("is_composite")),
        # 존 역질문 후단 게이트(D-143 후속2): 채널 플래그(대화형 텍스트 라우트만 True)와
        # 원문 질의(호출부가 user_query를 sub_query로 덮어써도 게이트 판정·재전송 페이로드는
        # 원문 기준이어야 함 — resolved_limit 승격과 동일 원리).
        "zone_clarification_allowed": bool(state.get("zone_clarification_allowed")),
        "original_user_query": state.get("user_query", ""),
    }

    # 노드 KeyError 방지용 기본값 (대형 누적분은 빈 값으로 초기화)
    base.update({
        "retry_count": 0,
        "query_attempts": [],
        "schema_info": {},
        "schema_cache_source": None,
        "query_results": [],
        "db_results": {},
        "db_schemas": {},
        "db_errors": {},
        "validation_result": {"passed": False, "reason": "", "auto_fixed_sql": None},
        "organized_data": {
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "resolved_mapping": None,
            "is_sufficient": False,
            "sheet_mappings": None,
        },
        "generated_sql": "",
        "synonym_usage": None,
        "error_message": None,
        "relevant_tables": [],
        "column_descriptions": {},
        "column_synonyms": {},
        "resource_type_synonyms": {},
        "eav_name_synonyms": {},
        "active_db_engine": None,
        "accessed_tables": [],
        # 추론 agent(general_inference)에만 트리밍된 이전 대화 이력을 전달한다(①).
        # 데이터 조회 agent는 [] 유지(격리) — 이력이 SQL 생성에 불필요·오염 위험.
        "messages": _history_for_agent(task, state),
        "is_multi_db": False,
        "target_databases": [],
        "active_db_id": None,
        "user_specified_db": None,
    })

    # 데이터 의존(패턴 ②): 선행 task 결과 행을 식별 키 위주로 주입 (R-12)
    input_from = task.get("input_from") or []
    if input_from:
        prior_rows: dict[str, list[dict]] = {}
        for tid in input_from:
            res = prior.get(tid) or {}
            rows = res.get("rows")
            if rows is None:
                # data_query 결과는 query_results / organized_data.rows에 담길 수 있음
                rows = res.get("query_results")
            if rows is None:
                organized = res.get("organized_data") or {}
                rows = organized.get("rows", [])
            prior_rows[tid] = _extract_identity_rows(rows or [])
        base["prior_rows"] = prior_rows

    # 조사 대상 전달 (Plan 78 W1 · G2). 소비 방식이 agent별로 다르다 —
    # data_query/alarm_query는 위의 prior_rows(SQL 스코프, D-086)를 그대로 쓰고,
    # process_query/fault_diagnosis는 **대상 집합**(prior_targets)을 받는다.
    #
    # 이 지점이 1단(deepagents_tools 도구 경로)·2단(agent_orchestrator 계획 경로) **양쪽의
    # 합류점**이다 — 두 경로가 같은 함수를 통과하므로 비대칭이 생기지 않는다
    # (Known Mistakes: "한쪽만 고치는 비대칭이 반복 원인").
    if task.get("agent") in _TARGET_CONSUMER_AGENTS:
        targets = _build_prior_targets_for_task(task, state, prior)
        if targets is not None:
            base["prior_targets"] = targets

    return base


# 선행 결과를 **조사 대상 집합**으로 소비하는 agent (Plan 78 W1-2).
_TARGET_CONSUMER_AGENTS: tuple[str, ...] = ("process_query", "fault_diagnosis")


def _prior_result_rows(res: dict) -> list[dict]:
    """선행 task 결과에서 행 목록을 꺼낸다(결과 형태 3종 방어 — prior_rows 로직과 동일 규약)."""
    rows = res.get("rows")
    if rows is None:
        rows = res.get("query_results")
    if rows is None:
        organized = res.get("organized_data") or {}
        rows = organized.get("rows", [])
    return rows or []


def _build_prior_targets_for_task(
    task: dict, state: dict, prior: dict
) -> Optional[list[dict]]:
    """선행 결과에서 조사 대상을 해소해 상태에 실을 dict 목록을 만든다 (Plan 78 W1).

    플래그(`COMPOSITE_PRIOR_TARGETS_ENABLED`)가 off면 **아무것도 하지 않는다** —
    미설정 시 현행 동작과 비트 동일해야 한다(Plan 80 §5.4-③).

    `input_from`이 지정돼 있으면 그 task 결과만, 없으면 완료된 선행 결과 전체를 본다.
    후자는 계획 경로(2단)에서 planner가 `input_from`을 채우지 않는 경우를 위한 것이며,
    `intent_planner`를 수정하지 않고 대상 전달을 성립시킨다(78 R-13 경계 유지).

    Args:
        task: 현재 TaskSpec
        state: 전체 에이전트 상태
        prior: 완료된 task 결과 {task_id: 결과}

    Returns:
        `TargetRef.model_dump()` 목록, 또는 해소 불가·플래그 off 시 None
    """
    cfg = load_config().composite
    if not cfg.prior_targets_enabled:
        return None

    input_from = task.get("input_from") or []
    sources = (
        [prior.get(tid) or {} for tid in input_from]
        if input_from
        else [r for r in (prior or {}).values() if isinstance(r, dict)]
    )

    for res in sources:
        if not isinstance(res, dict) or res.get("error"):
            continue
        rows = _prior_result_rows(res)
        if not rows:
            continue
        # 행별 `_source_db`가 있으면 `build_prior_targets`가 그것을 db_id 정본으로 쓴다
        # (D-176). 여기서 주는 값은 **태그가 없는 행의 폴백**일 뿐이다 — 종전에는 이 값
        # 하나가 전 대상에 찍혀, 팬아웃 결과의 대상들이 전부 첫 DB(b0)로 표기됐다.
        db_ids = res.get("target_db_ids") or []
        resolution = build_prior_targets(
            rows,
            db_id=db_ids[0] if db_ids else state.get("active_db_id"),
            max_targets=cfg.max_targets,
        )
        if resolution.resolved:
            if resolution.truncated:
                logger.info(
                    "prior_targets 절단(Plan 78 W1): %d건 초과 — 상한 %d",
                    resolution.truncated_count, cfg.max_targets,
                )
            return resolution.as_state_value()
        if resolution.dropped:
            logger.info(
                "prior_targets 미해소(task=%s): %s",
                task.get("task_id"), [d.get("reason") for d in resolution.dropped],
            )
    return None


# ──────────────────────────────────────────────
# handler 정의 (registry 정의보다 먼저 — NameError 방지)
# ──────────────────────────────────────────────

async def run_cache_management(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """캐시 관리 작업을 수행한다 (cache_management 노드 래퍼).

    Args:
        task: 현재 TaskSpec
        isolated: 필터된 입력 컨텍스트 (user_query=task["sub_query"] 주입됨)
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        cache_management 노드의 반환 dict
    """
    return await cache_management(isolated, llm=llm, app_config=app_config)


async def run_synonym_registration(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """유사어 등록 작업을 수행한다 (synonym_registrar 노드 래퍼).

    Args:
        task: 현재 TaskSpec
        isolated: 필터된 입력 컨텍스트
        llm: LLM 인스턴스. `QUERY_INTENT_LLM_ASSIST`가 켜져 있고 등록 의사가 결정적으로
            확정되지 않은 경우에만 노드가 사용한다(미전달 시 노드가 자체 생성 — Plan 67 R3-(ii)).
        app_config: 앱 설정

    Returns:
        synonym_registrar 노드의 반환 dict
    """
    return await synonym_registrar(isolated, llm=llm, app_config=app_config)


async def run_general_inference(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """DB 미접근 일반 응답을 생성한다 (general_inference 노드 래퍼).

    Args:
        task: 현재 TaskSpec
        isolated: 필터된 입력 컨텍스트
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        general_inference 노드의 반환 dict
    """
    # 결정적 고정 안내(D-150 — 파일 없는 폼필 등)는 LLM을 통과시키지 않는다.
    # 라이브 실측(2026-07-30): 고정 안내를 LLM 재서술시키다 호출 실패 → 일반 오류
    # 문구("죄송합니다…")로 강등. 고정문은 그대로 반환한다(침묵 강등 금지).
    direct = task.get("direct_response")
    if direct:
        logger.info("general_inference: direct_response 고정 안내 반환(D-150, LLM 미호출)")
        return {
            "final_response": direct,
            "routing_intent": "general_inference",
            "current_node": "general_inference",
        }
    return await general_inference(isolated, llm=llm, app_config=app_config)


async def run_data_query_pipeline(
    task: dict,
    isolated: dict,
    *,
    llm: BaseChatModel,
    app_config: AppConfig,
) -> dict:
    """인프라/알람 DB 조회 파이프라인을 수행한다 (단일/멀티 DB 통합).

    DB 선택(classify_dbs) → 단일/멀티 분기 실행 → result_organizer 정리 순으로 수행한다.
    단일 분기는 _run_single_db_pipeline으로 풀 검증·재시도를 보존한다(R-09).

    Args:
        task: 현재 TaskSpec (db_ids가 있으면 DB 고정)
        isolated: 필터된 입력 컨텍스트 (user_query=task["sub_query"] 주입됨)
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        {organized_data, query_results, source, (error)} 형태 dict
    """
    sub_query = task.get("sub_query", isolated.get("user_query", ""))

    # 1) DB 선택 — db_ids 고정(②mapped_db_ids)이 있으면 우선, 없으면 classify_dbs.
    #    classify_dbs 후, 이번 턴에 새 위치/DB 신호가 없으면 직전 턴 DB를 승계한다(③, M2).
    #    selected_db_ids(존 선택 역질문, Plan 75 §4)는 복합 계획의 개별 task에도 적용되도록
    #    task.db_ids 다음 순위로 결합 — LLM 분류(classify_dbs)를 우회한다.
    raw_targets = task.get("db_ids") or isolated.get("selected_db_ids")
    db_succeeded = False
    db_pinned = False
    if raw_targets:
        targets = _normalize_targets(raw_targets, sub_query)
    else:
        targets = await classify_dbs(llm, sub_query, app_config)
        # ① 이번 턴 원문 위치 힌트가 해소되면 DB 집합을 결정적으로 고정한다
        #    (LLM 분해/분류가 직전 턴 위치를 병합해도 원문 힌트가 이긴다 — 2026-07-16).
        targets, db_pinned = _apply_turn_hint_pinning(
            targets, isolated, sub_query, app_config.multi_db.get_active_db_ids()
        )
        if not db_pinned:
            targets, db_succeeded = _apply_db_succession(
                targets,
                sub_query,
                isolated.get("conversation_context"),
                app_config.multi_db.get_active_db_ids(),
            )

    if not targets:
        # 방어적 폴백 (classify_dbs가 항상 1개 이상 반환하지만 안전 차원)
        targets = [{
            "db_id": "default",
            "relevance_score": 1.0,
            "sub_query_context": sub_query,
            "user_specified": False,
            "reason": "대상 DB 미식별 폴백",
        }]

    # 존 역질문 후단 게이트 (D-143 후속2): 첫 턴 + 위치어·서버 식별·승계·핀 신호가 전부
    # 없는 data_query가 폴스타 존으로 팬아웃되면, LLM 임의 라우팅(전 존/임의 존 — 종전
    # "기존 폴백"의 실체) 대신 존 선택을 역질문한다. 재개 턴은 selected_db_ids가
    # raw_targets로 고정되므로 비발동.
    if not raw_targets:
        _zone_q = _zone_clarification_or_none_task(
            task, isolated, targets,
            db_pinned=db_pinned, db_succeeded=db_succeeded, app_config=app_config,
        )
        if _zone_q:
            return {
                "final_response": _zone_q["question"],
                "zone_clarification": _zone_q,
                "source": [],
                # target_db_ids를 의도적으로 남기지 않는다 — result_aggregator의 DB 승격
                # (_collect_db_promotion)이 임의 분류 결과를 previous_db_ids로 체크포인터에
                # 남겨 재개·후속 턴 승계를 오염시키는 것을 차단(요청 스코프 원칙).
            }

    # Plan 71: 실시간 사용률 분기 (옵트인 기본 OFF, B안 게이트 — 원문 기준 승격 신호).
    # 대상이 전부 폴스타이고 measurement 조회가 성공하면 SQL 파이프라인을 건너뛴다.
    # 실패(None)면 아래 기존 경로로 폴백(침묵 금지 — 사유는 노드가 로그·summary에 남김).
    if (app_config.polestar_rest.realtime_usage_enabled
            and isolated.get("realtime_usage_intent")):
        _polestar_ids = app_config.get_polestar_db_ids() or set()
        _target_ids = [t["db_id"] for t in targets]
        if _target_ids and all(d in _polestar_ids for d in _target_ids):
            rt = await realtime_usage_lookup(
                _target_ids,
                isolated.get("user_query", "") or sub_query,
                app_config,
                user_id=isolated.get("user_id"),
                thread_id=isolated.get("thread_id"),
            )
            if rt is not None:
                return {
                    **rt,
                    "target_databases": targets,
                    "is_multi_db": len(targets) > 1,
                    "active_db_id": targets[0]["db_id"],
                }
            logger.info("realtime_usage 폴백 — 기존 SQL 파이프라인으로 진행")
        else:
            logger.info(
                "realtime_usage 스킵: 대상에 비폴스타 DB 포함 — SQL 경로 (targets=%s)",
                [t["db_id"] for t in targets],
            )
    elif isolated.get("realtime_usage_intent"):
        # 의도는 감지됐는데 플래그 OFF — 침묵 스킵이면 폐쇄망에서 "왜 SQL로 갔는지"
        # 진단 불가(2026-07-24 실측: 활성화 누락과 게이트 미발동을 구분 못 함).
        logger.info(
            "realtime_usage 의도 감지 — 플래그 OFF(POLESTAR_REST_REALTIME_USAGE_ENABLED=false), SQL 경로로 처리"
        )

    is_multi_db = len(targets) > 1
    # 단일 DB: 라우팅 신호(위치/DB명)가 제거된 정제 질의(sub_query_context)를 SQL 생성 입력으로 사용한다.
    #   → 위치가 SQL WHERE 절로 누출되는 것을 방지 (§4.9.6 디멘전 7).
    #   멀티 DB는 multi_db_executor가 target별 sub_query_context를 사용하므로 user_query는 원본 유지.
    sql_query = sub_query if is_multi_db else (targets[0].get("sub_query_context") or sub_query)
    # 지시어("해당 서버") 후속 조회는 filter_conditions가 비어 전체 조회로 빠진다. 직전 서버
    # hostname을 filter에 결정적으로 주입해 서버 스코프를 강제한다(D-056 후속, query_generator는
    # sub_query 확장이 아니라 parsed_requirements로 SQL을 생성하므로 filter 주입이 필요).
    parsed_for_gen = _inject_demonstrative_hostname(isolated)
    # 활성 DB 엔진을 설정한다 — query_generator/query_validator가 엔진별 방언(DB2 FETCH FIRST·
    # DECIMAL 캐스트 vs PostgreSQL LIMIT·::numeric)을 올바로 쓰도록. 미설정 시 항상 PostgreSQL로
    # 취급돼 b0(DB2)가 정수 표시·문법 오류(data_insufficient)를 냈다(D-066 후속6).
    _active_domain = get_domain_by_id(targets[0]["db_id"])
    _active_engine = _active_domain.db_engine if _active_domain else None
    s: dict[str, Any] = {
        **isolated,
        "parsed_requirements": parsed_for_gen,
        "user_query": sql_query,
        "target_databases": targets,
        "is_multi_db": is_multi_db,
        "active_db_id": targets[0]["db_id"],
        "active_db_engine": _active_engine,
    }

    # 2) 실행 — 단일/멀티 분기
    if is_multi_db:
        s.update(await multi_db_executor(s, llm=llm, app_config=app_config))
        s.update(await result_merger(s, app_config=app_config))
    else:
        s.update(await _run_single_db_pipeline(s, llm, app_config))

    # 3) 결과 정리
    s.update(await result_organizer(s, llm=llm, app_config=app_config))

    result: dict = {
        "organized_data": s.get("organized_data"),
        "query_results": s.get("query_results"),
        "source": targets,
    }
    if s.get("error_message"):
        result["error"] = s["error_message"]

    # 폼필 산출물 승격(D-146/D-151): orchestration에서 output_generator는 파이프라인
    # 내부 state(s)가 아니라 result_aggregator의 _build_output_state 입력을 받으므로,
    # 기준월 앵커·역질문 후보·답변 적용 내역·직접입력 상수를 task 결과로 실어 전달한다
    # (미승격 시 기준월 안내·역질문이 그래프 경로에서만 동작하는 비대칭 — Known Mistakes).
    for _fk in (
        "form_month_anchor", "form_fill_candidates",
        "form_fill_overrides", "form_fill_literals",
    ):
        if s.get(_fk):
            result[_fk] = s[_fk]

    # 관찰성(처리 현황): orchestration에서는 query_generator가 그래프 노드가 아니라
    # 함수 호출이어서 생성 SQL이 SSE node_complete로 노출되지 않는다. task 결과에
    # 생성 SQL·대상 DB·DB별 에러를 담아 _extract_node_progress(agent_orchestrator)가
    # 처리 현황에 표시하도록 한다(어떤 SQL이 어느 DB로 실행됐는지·b0 오선택 가시화).
    result["target_db_ids"] = [t.get("db_id") for t in targets if t.get("db_id")]
    if db_succeeded:
        # 처리현황 UI 투명성: 이전 턴 DB 승계 사실을 노출한다(Plan 50 §3.2).
        result["db_succession"] = {
            "succeeded": True,
            "db_ids": result["target_db_ids"],
            "note": "이전 턴 DB 승계(멀티턴)",
        }
    if db_pinned:
        # 관찰성: 원문 위치 힌트로 DB가 결정적으로 고정됐음을 task 결과에 남긴다(오라우팅 진단용).
        result["db_hint_pinning"] = {
            "pinned": True,
            "db_ids": result["target_db_ids"],
            "note": "이번 턴 위치 힌트 결정적 DB 고정",
        }
    gen_sql = s.get("generated_sql")
    if not gen_sql:
        # 멀티 DB 경로: multi_db_executor는 generated_sql을 두지 않으므로
        # query_attempts에서 시도된 SQL을 수집한다(대표 1건).
        sqls: list[str] = []
        for a in (s.get("query_attempts") or []):
            q = getattr(a, "sql", None)
            if q is None and isinstance(a, dict):
                q = a.get("sql")
            if q:
                sqls.append(q)
        gen_sql = sqls[-1] if sqls else None
    if gen_sql:
        result["generated_sql"] = gen_sql
    db_errors = s.get("db_errors")
    if db_errors:
        result["db_errors"] = db_errors
    return result


# ──────────────────────────────────────────────
# registry (handler 정의 이후)
# ──────────────────────────────────────────────

SUBAGENT_REGISTRY: dict[str, SubAgentSpec] = {
    "data_query": SubAgentSpec(
        "data_query",
        "인프라 DB(서버 사양·사용량·성능 통계) 조회 — 알람/이벤트(event) 조회는 alarm_query 담당",
        run_data_query_pipeline,
    ),
    "process_query": SubAgentSpec(
        "process_query",
        "특정 서버의 현재/실시간 프로세스 리스트 조회 (DB 이력이 아닌 실시간 폴스타 프로세스 API)",
        run_process_query,
    ),
    "alarm_query": SubAgentSpec(
        "alarm_query",
        "알람/모니터링 이벤트(event) 조회 — 알람 현황·이력, event 발생 서버, alert, 경보",
        run_data_query_pipeline,
    ),
    "cache_management": SubAgentSpec(
        "cache_management", "스키마 캐시·유사어 관리", run_cache_management
    ),
    "synonym_registration": SubAgentSpec(
        "synonym_registration", "유사어 등록", run_synonym_registration
    ),
    "general_inference": SubAgentSpec(
        "general_inference", "DB 미접근 일반 응답", run_general_inference,
        fallback=True, needs_history=True,
    ),
    # Plan 78 W3-1·W3-2 (WU-18) — 중간 비용대. `data_query`(DB SQL)와 `fault_diagnosis`
    # (sre_agent 위임) 사이의 공백을 메운다. **도구 수를 늘리지 않는다**(W3-4): 프로파일
    # 4종을 `profile` 인자로 흡수한다. 목록 노출은 플래그 종속 — `active_subagents()` 참조.
    HOST_INSPECT_AGENT: SubAgentSpec(
        HOST_INSPECT_AGENT,
        "특정 서버의 OS 구성·자원 현황·메트릭 추세 단건 조회 "
        "(DB SQL도 장애 진단 위임도 아닌 mcp_server 고수준 도구 경로)",
        run_host_inspect,
    ),
}
