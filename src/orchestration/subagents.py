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

from src.config import AppConfig, load_config
from src.nodes.cache_management import cache_management
from src.nodes.general_inference import general_inference
from src.nodes.multi_db_executor import multi_db_executor
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import query_validator
from src.nodes.result_merger import result_merger
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import schema_analyzer
from src.nodes.synonym_registrar import synonym_registrar
from src.routing.domain_config import DB_DOMAINS
from src.routing.semantic_router import MIN_RELEVANCE_SCORE, _llm_classify

logger = logging.getLogger(__name__)

# 단일 DB 파이프라인 재시도 루프 무한루프 방지용 명시적 반복 상한
_MAX_PIPELINE_STEPS = 10
# input_from 선행 결과 주입 시 행수 상한 (R-12: 토큰·IN 절 폭증 방지)
_MAX_PRIOR_ROWS = 100
# 식별 키 컬럼 추출 우선순위 (보수적: 식별성 높은 컬럼 우선)
_IDENTITY_KEY_HINTS = ("hostname", "host_name", "name", "server_name", "id")


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
    """

    name: str
    description: str
    handler: Callable
    model: Optional[BaseChatModel] = None
    prompt: Optional[str] = None
    fallback: bool = False


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
            if state.get("retry_count", 0) >= 3:
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
            if state.get("retry_count", 0) >= 3:
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

    # 식별 키 컬럼 식별 (보수적: 컬럼명에 힌트 포함 여부)
    key_cols = [
        col for col in first.keys()
        if any(hint in str(col).lower() for hint in _IDENTITY_KEY_HINTS)
    ]

    if not key_cols:
        # 식별 키 없으면 전체 컬럼 유지 (행수 상한만)
        return limited

    return [{col: row.get(col) for col in key_cols} for row in limited if isinstance(row, dict)]


def _make_isolated_input(task: dict, state: dict, prior: dict) -> dict:
    """subagent에 전달할 필터된 얇은 컨텍스트를 만든다 (SubAgent S3 부분 격리).

    전체 AgentState를 넘기지 않고 실행에 필요한 필드 + 노드 KeyError 방지용 기본값만 포함한다.
    대형·히스토리 필드(원본 query_results/db_results/messages 누적분)는 제외한다.
    선행 결과(input_from)는 식별 키 컬럼만·행수 상한으로 추려 prior_rows에 주입한다.

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
        "parsed_requirements": state.get("parsed_requirements", {}),
        "conversation_context": state.get("conversation_context"),
        "thread_id": state.get("thread_id"),
        "user_id": state.get("user_id"),
        "user_department": state.get("user_department"),
        "allowed_db_ids": state.get("allowed_db_ids"),
        "request_id": state.get("request_id"),
        "client_ip": state.get("client_ip"),
        "template_structure": state.get("template_structure"),
        "target_sheets": state.get("target_sheets"),
        "file_type": state.get("file_type"),
        "csv_sheet_data": state.get("csv_sheet_data"),
        "mapped_db_ids": state.get("mapped_db_ids"),
        "db_column_mapping": state.get("db_column_mapping"),
        "column_mapping": state.get("column_mapping"),
        "mapping_sources": state.get("mapping_sources"),
        "llm_inference_details": state.get("llm_inference_details"),
        "pending_synonym_registrations": state.get("pending_synonym_registrations"),
        "pending_synonym_reuse": state.get("pending_synonym_reuse"),
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
        "messages": [],
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

    return base


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
        llm: LLM 인스턴스 (synonym_registrar는 사용 안 함)
        app_config: 앱 설정

    Returns:
        synonym_registrar 노드의 반환 dict
    """
    return await synonym_registrar(isolated, app_config=app_config)


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

    # 1) DB 선택 — db_ids 고정이 있으면 우선, 없으면 classify_dbs
    raw_targets = task.get("db_ids")
    if raw_targets:
        targets = _normalize_targets(raw_targets, sub_query)
    else:
        targets = await classify_dbs(llm, sub_query, app_config)

    if not targets:
        # 방어적 폴백 (classify_dbs가 항상 1개 이상 반환하지만 안전 차원)
        targets = [{
            "db_id": "default",
            "relevance_score": 1.0,
            "sub_query_context": sub_query,
            "user_specified": False,
            "reason": "대상 DB 미식별 폴백",
        }]

    is_multi_db = len(targets) > 1
    # 단일 DB: 라우팅 신호(위치/DB명)가 제거된 정제 질의(sub_query_context)를 SQL 생성 입력으로 사용한다.
    #   → 위치가 SQL WHERE 절로 누출되는 것을 방지 (§4.9.6 디멘전 7).
    #   멀티 DB는 multi_db_executor가 target별 sub_query_context를 사용하므로 user_query는 원본 유지.
    sql_query = sub_query if is_multi_db else (targets[0].get("sub_query_context") or sub_query)
    s: dict[str, Any] = {
        **isolated,
        "user_query": sql_query,
        "target_databases": targets,
        "is_multi_db": is_multi_db,
        "active_db_id": targets[0]["db_id"],
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
    return result


# ──────────────────────────────────────────────
# registry (handler 정의 이후)
# ──────────────────────────────────────────────

SUBAGENT_REGISTRY: dict[str, SubAgentSpec] = {
    "data_query": SubAgentSpec(
        "data_query", "인프라 DB(서버 사양·사용량·모니터링) 조회", run_data_query_pipeline
    ),
    "alarm_query": SubAgentSpec(
        "alarm_query", "알람/모니터링 이벤트 조회", run_data_query_pipeline
    ),
    "cache_management": SubAgentSpec(
        "cache_management", "스키마 캐시·유사어 관리", run_cache_management
    ),
    "synonym_registration": SubAgentSpec(
        "synonym_registration", "유사어 등록", run_synonym_registration
    ),
    "general_inference": SubAgentSpec(
        "general_inference", "DB 미접근 일반 응답", run_general_inference, fallback=True
    ),
}
