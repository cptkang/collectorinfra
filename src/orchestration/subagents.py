"""subagent 정의 및 실행 헬퍼 (Plan 48, deepagents SubAgent 미들웨어 대응).

기존 노드/파이프라인을 얇은 래퍼(handler)로 캡슐화하여 SUBAGENT_REGISTRY로 일원화한다.
신규 비즈니스 로직은 추가하지 않으며, 모든 위임은 registry를 통해 분기된다.

구성:
- SubAgentSpec / SUBAGENT_REGISTRY: deepagents SubAgent dict 스키마에 정합하는 정의 테이블.
- classify_dbs: 기존 semantic_router._llm_classify의 DB 분류부만 재사용.
- _run_single_db_pipeline: 단일 DB 풀 검증·재시도 루프(graph.py 라우팅 로직 이식).
- make_isolated_input: subagent에 전달할 필터된 얇은 컨텍스트(S3 부분 격리).
- run_* handler들: 기존 동명 노드를 sub_query 입력으로 호출.

plans/166 O4로 정의 스키마(`agent_spec.SubAgentSpec`) · DB 대상 정책(`db_targeting`) · 격리 입력
순수 조각(`isolated_input`)을 떼어 냈다. 옮긴 이름은 여기서 재노출한다. 테스트가 이 모듈에서
바꿔 끼우는 이름(`classify_dbs`·`_run_single_db_pipeline`·노드 함수·`load_config` 등)을 부르는
함수는 이 모듈(Facade)에 남겼다(D-337 ②).

본 모듈은 tool-calling을 사용하지 않는다(handler 디스패치는 코드 기반).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.config import AppConfig, load_config
from src.nodes.cache_management import cache_management
from src.nodes.general_inference import general_inference
from src.nodes.multi_db_executor import multi_db_executor
from src.nodes.name_match_retry import (
    NAME_MATCH_CHECKED_KEY,
    loggable_reason,
    name_match_retry_hint,
)
from src.nodes.query_executor import query_executor
from src.nodes.query_generator import query_generator
from src.nodes.query_validator import (
    REGEN_STOP_BACKEND_LIMIT,
    REGEN_STOP_DEADLINE,
    REGEN_STOP_NON_SQL,
    REGEN_STOP_SELECTION_NONE,
    REGEN_STOP_VALIDATION_BUDGET,
    backend_limit_hit,
    backend_limit_response,
    deadline_stop_message,
    non_sql_budget_exhausted,
    non_sql_prose_response,
    query_validator,
    retrieval_reserve_sec,
    selection_none_hit,
)
from src.nodes.realtime_usage import realtime_usage_lookup
from src.nodes.result_merger import result_merger
from src.nodes.result_organizer import result_organizer
from src.nodes.schema_analyzer import schema_analyzer
from src.nodes.synonym_registrar import synonym_registrar
from src.observability import run_capture
from src.orchestration.agent_spec import SubAgentSpec
from src.orchestration.process_query import run_process_query
from src.orchestration.db_access import access_denied_result, authorize_targets, denied_for_all
from src.orchestration.db_targeting import (
    REASON_SOURCE_INACTIVE,  # noqa: F401 — 재노출
    REASON_SOURCE_NOT_CANONICAL,  # noqa: F401 — 재노출
    REASON_SOURCE_UNSUPPORTED,  # noqa: F401 — 재노출
    _apply_db_succession,
    _apply_selection_to_zone_groups,
    _apply_turn_hint_pinning,
    _explicit_source_notice,
    _has_new_location_db_signal,  # noqa: F401 — 재노출
    _inject_demonstrative_hostname,
    _multiple_systems_active,
    _refers_to_specific_server,  # noqa: F401 — 재노출
    _source_unavailable_notes,
    _strip_location_terms,  # noqa: F401 — 재노출
    _utilization_correction_note,
    _utilization_source_guard,
    _zone_clarification_or_none_task,
    _zone_clarification_result,
    normalize_targets,
)
from src.orchestration.host_inspect import HOST_INSPECT_AGENT, run_host_inspect
from src.orchestration.investigation_audit import (
    BACKEND_MCP,
    BACKEND_PROCESS_API,
    audited_investigation,
)
from src.orchestration.isolated_input import (
    _ROW_REF_CONSUMER_AGENTS,
    _TARGET_CONSUMER_AGENTS,
    _prior_result_rows,
    _row_ref_dependencies,
    _scope_parsed_requirements,
    scope_time_resolution,
)
from src.routing.capability_ownership import (
    REASON_LLM_ERROR,
    REASON_NO_CLASSIFICATION,
    capability_ownership_on,
    owner_inactive_note,
    resolve_capability_owner,
    restrict_targets_to_owner,
    routing_fallback_note,
)
from src.routing.db_scope import (
    ZONE_GROUP_ONLY,
    ZONE_SELECTION_SCOPE_KEY,
    zone_selection_db_ids,
)
from src.routing.domain_config import DB_DOMAINS, get_domain_by_id
from src.routing.location_hints import pin_targets_to_hints  # noqa: F401 — 재노출(plans/166 O4)
from src.routing.registry import get_registry
from src.routing.semantic_router import MIN_RELEVANCE_SCORE, _llm_classify
from src.utils.deadline import has_time_for, retrieval_remaining
from src.utils.prior_targets import SOURCE_DB_KEY, build_prior_targets
from src.utils.progress_events import emit_step
from src.utils.query_gen_common import (
    is_realtime_usage_query,
    is_server_identity_col,
    resolve_query_limit,
)

# 옛 `_` 이름 별칭(D-337 ③ · plans/166 O4 — 1사이클) — 모듈 밖 소비처가 쓰던 이름. 이 모듈
# 정의분(`extract_identity_rows`·`make_isolated_input`·`pack_pipeline_result`)은 정의 뒤에 둔다.
_normalize_targets = normalize_targets

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

    llm_error: str | None = None
    try:
        classified = await _llm_classify(
            llm, sub_query, active_domains, db_descriptions=db_descriptions
        )
    except Exception as e:
        logger.error("classify_dbs LLM 분류 실패, 첫 활성 DB 폴백: %s", e)
        classified = {"intent": "data_query", "databases": []}
        llm_error = type(e).__name__

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
        # X-T3(plans/102) — 폴백 사실을 구조화 표지로 싣는다. `run_data_query_pipeline`이
        # 떼어 내 경과 노트로 올린다(대상 dict에 남겨 두지 않는다). 소유 플래그와 무관하다
        # (plans/121 TP-1.6 — 침묵 폴백 금지).
        targets[0][_ROUTING_FALLBACK_KEY] = {
            "reason": REASON_LLM_ERROR if llm_error else REASON_NO_CLASSIFICATION,
            "cause": llm_error or "",
        }

    return targets


#: classify_dbs 폴백 표지 키(plans/102 X-T3) — 소유 플래그와 무관하게 붙는다(plans/121 TP-1.6).
_ROUTING_FALLBACK_KEY = "routing_fallback"


def _zone_clarification_before_classify(
    task: dict[str, Any],
    isolated: dict[str, Any],
    sub_query: str,
    app_config: AppConfig,
) -> dict[str, Any] | None:
    """존 역질문 조기 판정 — DB 분류 LLM **전에** 후단 게이트와 같은 판정을 한다.

    plans/119 Q-1 · D-267 ② (D-143 후속2 개정).

    등가성(분류 결과와 무관하게 후단 게이트가 같은 페이로드로 끝남)이 성립하는 경우에만 판정하고,
    나머지는 None을 돌려 종전 경로(분류 → 핀 → 승계 → 소유 제한 → 후단 게이트)에 맡긴다.

    등가성 근거(코드 추적 — `tests/test_orchestration/test_plan119_zone_early.py`가 고정한다):
    - 분류 결과는 활성 DB의 비지 않은 부분집합이다 — `_validate_db_entries`가 활성 도메인 밖 db_id를
      버리고, 결과가 비거나 LLM이 실패하면 `classify_dbs`가 첫 활성 DB로 폴백한다.
    - 위치 힌트가 없으면 핀은 입력 그대로·미적용이다(`pin_targets_to_hints` 규칙 6). 힌트가 있으면
      핀 판정이 결과를 바꿀 수 있어 여기서 판정하지 않는다.
    - 승계는 `previous_db_ids`가 있어야 일어나는데, 그 경우 후단 게이트가 먼저 비발동한다.
    - 소유 제한은 활성 DB 안에서만 좁히거나 소유 시스템의 활성 DB로 넓힌다 — 결과가 비지 않는다.
    - 후단 게이트가 대상 목록에서 쓰는 것은 "비지 않고 전부 폴스타 DB인가" 하나뿐이고, 페이로드는
      활성·인가 DB와 원문으로만 만든다.
    따라서 **활성 DB가 전부 폴스타 DB이면서 존 그룹 소속**이면 분류가 무엇을 내든 후단 게이트의 답은
    같다. 존 그룹이 없는 DB가 하나라도 활성이면(W-10 경계 — 분류가 그 DB를 고르면 역질문하지 않는다)
    판정하지 않는다. 활성 DB·존 그룹은 설정·레지스트리에서 읽는다.
    """
    active = [d for d in app_config.multi_db.get_active_db_ids() if d]
    if not active:
        return None
    polestar_ids = app_config.get_polestar_db_ids() or set()
    registry = get_registry()
    if not all(d in polestar_ids and registry.zone_group_of(d) is not None for d in active):
        return None
    hints = (isolated.get("parsed_requirements") or {}).get("target_db_hints") or []
    if isinstance(hints, list) and any(str(h).strip() for h in hints):
        return None
    payload = _zone_clarification_or_none_task(
        task, isolated, normalize_targets(active, sub_query),
        db_pinned=False, db_succeeded=False, app_config=app_config,
    )
    if payload:
        logger.info(
            "존 역질문 조기 판정(plans/119 Q-1): 활성 DB %s 전부 존 그룹 소속 · 신호 없음 — "
            "DB 분류 LLM 생략", active,
        )
    return payload


# ──────────────────────────────────────────────
# 단일 DB 파이프라인 (풀 검증·재시도 보존)
# ──────────────────────────────────────────────

def _monotonic() -> float:
    """단조 시계(초) — 조회 마감 판정·생성 소요 계측이 쓴다. 테스트는 이 이름을 바꿔 끼운다."""
    return time.monotonic()


def _stop_regen(
    state: dict[str, Any], reason: str, detail: str, *, response: str | None = None,
) -> None:
    """SQL 재생성 루프 종결 표지를 파이프라인 상태에 싣는다(plans/119 N-5·T-3·T-1ⓑ).

    - `regen_stop`(`{"reason", "detail"}`)은 실패 task 결과로 승격돼 재계획기가 소비한다
      (`pack_pipeline_result` · 계약 plans/119 Q-3).
    - `regen_stop_response`가 있으면 호출부가 결과 정리 대신 그 문구를 응답 본문으로 싣는다.
    - 앞선 실패 사유(`error_message`)가 없으면 detail을 실패 사유로 둔다 — task가 "0건 완료"로
      바뀌지 않게 한다(D-231 ②와 같은 이유).
    """
    state["regen_stop"] = {"reason": reason, "detail": detail}
    if response is not None:
        state["regen_stop_response"] = response
    if not state.get("error_message"):
        state["error_message"] = detail


def _stop_for_deadline(
    state: dict[str, Any], reserve_sec: float, *, stage: str, need_sec: float | None,
    last_reason: str | None,
) -> None:
    """조회 마감으로 다음 단계를 시작하지 않고 끝낸다 — 사유를 사용자 문구로 남긴다(침묵 금지).

    직전 실패가 산문(비-SQL)이면 그 산문(되물음·불가 사유)이 사용자에게 가장 쓸모 있는 답이라
    산문 응답(`non_sql_prose_response`)을 앞에 두고 시간 사유를 덧붙인다 — 검증 사유 문자열
    ("SELECT 문만 허용됩니다 …")은 사용자 언어가 아니다.
    """
    left = retrieval_remaining(state, reserve_sec, now=_monotonic())
    prose_first = bool(last_reason) and bool((state.get("validation_result") or {}).get("non_sql"))
    message = deadline_stop_message(
        stage, remaining_sec=left, need_sec=need_sec,
        last_reason=None if prose_first else last_reason,
    )
    if prose_first:
        message = (
            f"{non_sql_prose_response(state.get('generated_sql') or '')}\n\n{message}"
        )
    logger.info(
        "단일 DB 파이프라인 조회 마감 종결(plans/119): %s 미시작 — 남은 %s초 · 직전 생성 %s초",
        stage, None if left is None else round(left, 1),
        None if need_sec is None else round(need_sec, 1),
    )
    _stop_regen(state, REGEN_STOP_DEADLINE, last_reason or message, response=message)


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

    종결 규칙(plans/119 — 그래프 경로와 같은 상수·같은 판정):
    - **산문(비-SQL) 응답**은 전용 예산 `NON_SQL_RETRY_BUDGET`(1)으로 끝낸다(N-5). 그래프
      `route_after_validation`과 같은 판정(`non_sql_budget_exhausted`)이다. 일반 검증 실패는 종전
      예산(`QUERY_MAX_RETRY_COUNT`) 그대로다.
    - **LLM 입력 한도 초과·백엔드 오류 응답**(백엔드 보고 · 전송 전 예산 초과)은 재생성 0회로
      끝낸다(plans/139 W1·W2 — 그래프와 같은 판정 `backend_limit_hit`).
    - **정의 기반 테이블 선별 0개**는 SQL 생성 없이 안내 문구로 끝낸다(plans/139 W4 —
      `selection_none_hit` · 종결 사유 `selection_none`).
    - **조회 마감**(처리 마감 − 서술 예약): 스키마 분석·첫 생성 **진입 전** 마감이 지났으면
      시작하지 않고(T-1ⓑ), 재생성(검증 실패·실행 오류 모두)은 `남은 시간 < 방금 잰 직전 생성
      소요`면 하지 않는다(T-3). 마감(`request_deadline`)이 없는 상태(CLI·테스트·옛
      체크포인트)는 종전 동작과 같다.
    - 유효 SQL 없이 끝나면 `regen_stop`을 남긴다(`_stop_regen`).
    - **0행 + 이름 칸 등호·IN**(plans/149 W3)이면 부분 일치 힌트로 1회 재생성한다
      (`name_match_retry_hint` — 그래프 경로 `result_organizer`와 같은 판정). 재생성도 0행이면 끝.
      재생성이 실행 성공 없이 끝나면(검증·실행 실패 · 조회 마감 등) 재생성 전 0행 답으로 되돌린다.

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
    # 조회 마감 = 처리 마감(request_deadline) − 서술 예약(plans/119 T-1ⓑ·T-3 · D-267 ⑥)
    _reserve = retrieval_reserve_sec(app_config)

    # T-1ⓑ: 스키마 분석 진입 전 — 조회 마감이 이미 지났으면 시작하지 않는다.
    if not has_time_for(state, _reserve, None, now=_monotonic()):
        _stop_for_deadline(state, _reserve, stage="스키마 분석", need_sec=None, last_reason=None)
        return state

    # 단계 마일스톤(plans/89 T4): 서브에이전트 안에서는 노드가 함수로 불려 바깥 그래프에
    # node 이벤트가 없다 — 각 단계 앞뒤에 `pipeline.<stage>` custom event를 내 상태줄이 따라온다.
    # 1) 스키마 분석 (1회)
    await emit_step("pipeline.schema", "start", label="스키마 분석")
    state.update(await schema_analyzer(state, llm=llm, app_config=app_config))
    await emit_step("pipeline.schema", "end")

    steps = 0
    # 직전 SQL 생성 소요(초) — 방금 잰 값으로 재생성 가능 여부를 판정한다(추정 상수 금지 · T-3).
    last_gen_sec: float | None = None
    # 이름 칸 부분 일치 재생성 직전의 0행 상태(plans/149 W3 · 검증 149 M-1) — 재생성이 실행 성공
    # 없이(검증·실행 실패 예산 소진 · 산문 · 조회 마감 등) 끝나면 이 0행 답으로 되돌린다.
    zero_row_snapshot: dict[str, Any] | None = None
    while steps < _MAX_PIPELINE_STEPS:
        steps += 1

        # T-1ⓑ·T-3: (재)생성 진입 전 시간 게이트 — 첫 생성은 조회 마감 경과만,
        # 재생성은 직전 생성 소요와 비교한다.
        if not has_time_for(state, _reserve, last_gen_sec, now=_monotonic()):
            _stop_for_deadline(
                state, _reserve,
                stage="SQL 생성" if steps == 1 else "SQL 재생성",
                need_sec=last_gen_sec,
                last_reason=state.get("error_message") if steps > 1 else None,
            )
            break

        # 2) SQL 생성
        _gen_label = "SQL 생성" if steps == 1 else f"SQL 재생성 {steps - 1}회차"
        await emit_step("pipeline.generate", "start", label=_gen_label)
        _gen_started = _monotonic()
        state.update(await query_generator(state, llm=llm, app_config=app_config))
        last_gen_sec = _monotonic() - _gen_started
        await emit_step("pipeline.generate", "end")

        # 3) 검증
        await emit_step("pipeline.validate", "start", label="SQL 검증")
        state.update(await query_validator(state, app_config=app_config))
        # 실패 사유를 단계 종료에 싣는다 — 스트림이 실패로 끝나면 화면 경위에 쓰인다(D-242)
        await emit_step("pipeline.validate", "end", detail=(
            None if state["validation_result"]["passed"]
            else state.get("error_message") or state["validation_result"].get("reason")
        ))
        if not state["validation_result"]["passed"]:
            _reason = state["validation_result"].get("reason", "SQL 검증 실패")
            if selection_none_hit(state["validation_result"]):
                # 정의 기반 테이블 선별 0개(plans/139 W4) — SQL 생성 LLM을 부르지 않았다.
                # 재생성 없이 안내 문구(검증 사유 = `selection_none_guidance`)로 끝낸다
                # (그래프와 같은 문구).
                logger.info("단일 DB 파이프라인 테이블 선별 0개 종결(plans/139 W4)")
                _stop_regen(state, REGEN_STOP_SELECTION_NONE, _reason, response=_reason)
                break
            if backend_limit_hit(state["validation_result"]):
                # LLM 입력 한도 초과·백엔드 오류 응답(plans/139 W1·W2) — 같은 프롬프트 재생성은
                # 결정적으로 다시 넘는다. 재생성 0회로 끝내고 그래프 `error_response`와 같은 문구를
                # 싣는다. detail은 응답 원문 없는 요약(검증 노드의 실패 사유)이다.
                logger.info(
                    "단일 DB 파이프라인 입력 한도 종결(plans/139): retry=%s",
                    state.get("retry_count", 0),
                )
                _stop_regen(
                    state, REGEN_STOP_BACKEND_LIMIT, state.get("error_message") or _reason,
                    response=backend_limit_response(
                        state["validation_result"]["backend_error"].get("kind"),
                    ),
                )
                break
            if non_sql_budget_exhausted(state["validation_result"], state.get("retry_count", 0)):
                # N-5: 산문 전용 예산 소진 — 그래프 `error_response`와 같은 문구로 산문을 싣는다
                logger.info(
                    "단일 DB 파이프라인 산문 조기 종결(plans/119 N-5): retry=%s",
                    state.get("retry_count", 0),
                )
                _stop_regen(
                    state, REGEN_STOP_NON_SQL, _reason,
                    response=non_sql_prose_response(state.get("generated_sql") or ""),
                )
                break
            if state.get("retry_count", 0) >= _max_retry:
                # 검증 실패 + 재시도 초과 → 에러 종료
                if not state.get("error_message"):
                    state["error_message"] = _reason
                _stop_regen(state, REGEN_STOP_VALIDATION_BUDGET, _reason)
                break
            # 재시도 가능 → query_generator 재진입
            continue

        # 4) 실행
        await emit_step("pipeline.execute", "start", label="SQL 실행")
        state.update(await query_executor(state, app_config=app_config))
        await emit_step("pipeline.execute", "end", detail=state.get("error_message"))
        if state.get("error_message"):
            if state.get("retry_count", 0) >= _max_retry:
                break
            # 실행 에러 + 재시도 가능 → query_generator 회귀
            continue

        # 0행 + 이름 칸 등호·IN(plans/149 W3 · G-4 (b)) — 부분 일치 힌트로 1회 재생성한다. 그래프
        # 경로(`result_organizer` → `route_after_organization`)와 같은 판정 함수 · 같은 예산이다.
        # 재생성할 시간이 없으면 0행 그대로 끝낸다(조회 마감 실패로 바꾸지 않는다).
        _hint = name_match_retry_hint(state, max_retry=_max_retry)
        if _hint is not None:
            # 판정 표지 — 결과 정리(`result_organizer`)가 같은 판정을 다시 내 재시도 사유가 task
            # 실패(`error`)로 새지 않게 한다(이 루프 뒤에는 재생성 루프가 없다).
            state[NAME_MATCH_CHECKED_KEY] = True
            if has_time_for(state, _reserve, last_gen_sec, now=_monotonic()):
                zero_row_snapshot = dict(state)
                state["error_message"] = _hint
                continue
            logger.info("이름 칸 부분 일치 재생성 생략 — 조회 마감(plans/149 W3) · 0행 그대로")

        # 정상 실행 완료 — 재생성 SQL이 실행됐으면 그 결과가 답이다
        zero_row_snapshot = None
        break

    if zero_row_snapshot is not None:
        logger.info(
            "이름 칸 부분 일치 재생성이 실행 성공 없이 끝남 — 재생성 전 0행 답으로 되돌림"
            "(plans/149 W3): 종결=%s 사유=%s",
            (state.get("regen_stop") or {}).get("reason"),
            loggable_reason(state.get("error_message")),
        )
        state = zero_row_snapshot
    return state


# ──────────────────────────────────────────────
# 격리 입력 컨텍스트 (S3 부분 격리)
# ──────────────────────────────────────────────

def extract_identity_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
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

    # 값 기반 키 브리지(plans/102 §3.3-② · D-224): 키 컬럼을 이름이 아니라
    # 값(호스트명·FQDN·IP)으로도 판정해 남기고, 출처 태그는 대상 DB별 스코프·판정에
    # 쓰이므로 있으면 유지한다. off면 아래 종전 경로.
    from src.nodes.key_bridge import identity_columns, key_bridge_enabled

    if key_bridge_enabled(load_config()):
        bridge_cols = identity_columns(limited)
        if not bridge_cols:
            return limited
        if SOURCE_DB_KEY in first:
            bridge_cols = [*bridge_cols, SOURCE_DB_KEY]
        return [
            {col: row.get(col) for col in bridge_cols} for row in limited if isinstance(row, dict)
        ]

    # 식별 키 컬럼 식별 — 서버 식별 컬럼만 엄격 선택(alarm_name 등 비서버 컬럼 배제 — D-100).
    # 선행 조회가 서버명 외 컬럼(알람명·심각도 등)도 반환하면서 "name" 부분매칭이 alarm_name을
    # 서버 식별로 오수집해 후속 스코프 HAVING이 오염됐다(실측).
    key_cols = [col for col in first.keys() if is_server_identity_col(col)]

    if not key_cols:
        # 식별 키 없으면 전체 컬럼 유지 (행수 상한만)
        return limited

    # DB별 스코프 분할(D-203 · plans/88 §4.9): 출처 태그는 식별 컬럼이 아니지만 후속 멀티 DB
    # 조회가 DB별로 IN 목록을 나누는 데 필요하다. 플래그 off면 종전대로 버린다(행 shape 불변).
    if SOURCE_DB_KEY in first and _prior_scope_by_db_enabled():
        key_cols = [*key_cols, SOURCE_DB_KEY]

    return [{col: row.get(col) for col in key_cols} for row in limited if isinstance(row, dict)]


# 옛 `_` 이름 별칭(D-337 ③ · plans/166 O4 — 1사이클)
_extract_identity_rows = extract_identity_rows


def _prior_scope_by_db_enabled() -> bool:
    """`COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED` — 기동 시 1회 해석(load_config 캐시)."""
    return bool(load_config().composite.prior_scope_by_db_enabled)


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


def make_isolated_input(
    task: dict[str, Any], state: dict[str, Any], prior: dict[str, Any], *, scope_time: bool = True,
) -> dict[str, Any]:
    """subagent에 전달할 필터된 얇은 컨텍스트를 만든다 (SubAgent S3 부분 격리).

    전체 AgentState를 넘기지 않고 실행에 필요한 필드 + 노드 KeyError 방지용 기본값만 포함한다.
    대형·히스토리 필드(원본 query_results/db_results/messages 누적분)는 제외한다.
    단, 추론 agent(needs_history=True)에는 직전 턴 답변 근거를 위해 트리밍된 messages를
    선별 전달한다(①). 선행 결과(input_from)는 식별 키 컬럼만·행수 상한으로 추려 prior_rows에 주입한다.

    Args:
        task: 현재 TaskSpec
        state: 전체 에이전트 상태
        prior: 지금까지 완료된 task 결과 {task_id: norm_result}
        scope_time: 시간 해석을 task 문장으로 좁힐지(plans/122 T-4). 3단 계획 루프는 False로
            원문 해석을 그대로 넘기고 `task_prompt`가 조각으로 찍은 문장으로 좁힌다.

    Returns:
        필터된 isolated state dict
    """
    # 원문 기준 판정(LIMIT 승격·실시간 의도)의 입력. 1단(deep_agent)은 격리 입력에 원문이
    # 실리지 않아(ambient 키에 user_query 없음 — plans/103 N-1) 빈 문자열로 판정하면 LIMIT이
    # 기본값에 고정되고("모든"이어도 1000) 실시간 의도가 항상 꺼진다. 원문이 없을 때만 task
    # 질의로 판정한다 — 2단은 원문이 있어 종전과 같다(plans/107 v2.6 · plans/111).
    judged_query = state.get("user_query") or task.get("sub_query", "")
    # 실행에 필요한 컨텍스트 필드 (얕은 복사)
    base: dict[str, Any] = {
        "user_query": state.get("user_query", ""),
        # 원문 기준 LIMIT 확정값 승격(Plan 75 §3 / D-066 후속): 호출부가 user_query를
        # sub_query(planner 재작성)로, 단일 DB 파이프라인이 다시 sub_query_context
        # (semantic_router 정제 — "모든" 등 수량 한정어까지 압축 탈락)로 교체해도,
        # 이 시점의 state.user_query(원문)로 계산한 limit이 state 필드로 살아남는다.
        # 실측(2026-07-24): 이 승격 부재로 은행존 "모든" 질의가 LIMIT 1000 절단(2,328→1,000).
        "resolved_limit": state.get("resolved_limit") or resolve_query_limit(
            judged_query, load_config().query.default_limit
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
        # 요청 시간 해석(plans/122 T-4 · D-309) — 격리 입력이 키를 직접 고르므로 싣지 않으면 task
        # 안의 모든 소비처(SQL 생성·검증·결정적 조립·APM 창)가 None을 받아 종전 경로로 돈다.
        # task 문장에 명시 기간이 있을 때만 task별 해석이다(§10.3 「2단 task」).
        "time_resolution": (
            scope_time_resolution(state, task) if scope_time else state.get("time_resolution")
        ),
        "conversation_context": state.get("conversation_context"),
        "thread_id": state.get("thread_id"),
        "user_id": state.get("user_id"),
        "user_department": state.get("user_department"),
        # 조사 인가는 **실행 경계**(handler 내부)에서 판정하므로 여기를 통과해야 한다.
        # 빠뜨리면 role이 None이 되어 전 조사가 차단된다(fail-closed — 78 W3-5).
        "user_role": state.get("user_role"),
        "allowed_db_ids": state.get("allowed_db_ids"),
        # 관측 소스 인가(plans/125 A-7) — 조건부 처리기(`apm_query`)가 실행 경계에서 판정한다
        "allowed_sources": state.get("allowed_sources"),
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
        # (plans/95 W-10) 존 선택 고정에 쓸 대상 — 선택 존 + 이번 턴 라우터가 남긴 존 미배정 DB.
        # `target_databases`는 아래에서 task 격리를 위해 비우므로 그 값을 여기서 미리 접는다.
        "zone_selection_db_ids": zone_selection_db_ids(
            state.get("selected_db_ids"), state.get("target_databases")
        ),
        # 제니퍼 소스 선택(plans/147 · D-322) — `apm_query` 소스 사다리 입력(화면 선택·되묻기 답 ·
        # 근거 · 글 답 · 대화 승계). 다른 처리기는 읽지 않는다.
        "selected_apm_source_ids": state.get("selected_apm_source_ids"),
        "apm_source_basis": state.get("apm_source_basis"),
        "apm_source_answer_text": state.get("apm_source_answer_text"),
        "apm_source_scope": state.get("apm_source_scope"),
        # 실시간 사용률 의도(Plan 71, B안 게이트)는 **원문 기준**으로 판정해 승격 —
        # sub_query/sub_query_context 재작성으로 "실시간" 표면어가 탈락해도 유지
        # (resolved_limit과 동일 원리, D-066 후속7).
        "realtime_usage_intent": is_realtime_usage_query(judged_query),
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
        # 이번 턴 원문 위치 힌트의 결정적 DB 고정(_apply_turn_hint_pinning)은 복합 계획이면
        # task 질의가 가리키는 원문 힌트 부분집합으로 좁힌다(plans/113 F-2) — 그 판정 신호.
        "is_composite": bool(state.get("is_composite")),
        # 존 역질문 후단 게이트(D-143 후속2): 채널 플래그(대화형 텍스트 라우트만 True)와
        # 원문 질의(호출부가 user_query를 sub_query로 덮어써도 게이트 판정·재전송 페이로드는
        # 원문 기준이어야 함 — resolved_limit 승격과 동일 원리).
        "zone_clarification_allowed": bool(state.get("zone_clarification_allowed")),
        "original_user_query": state.get("user_query", ""),
        # 요청 처리 마감(monotonic · D-266 ④) — SQL 루프의 조회 마감 게이트 입력
        # (plans/119 T-1ⓑ·T-3).
        # 없으면(CLI·옛 체크포인트·1단 ambient 미전파) 게이트가 항상 통과한다(종전 동작).
        "request_deadline": state.get("request_deadline"),
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
            prior_rows[tid] = extract_identity_rows(rows or [])
        base["prior_rows"] = prior_rows
    # 앞 결과 행 참조(plans/134 M-6) — 식별 키만 남긴 prior_rows에는 행 참조 칸
    # (profile_ref 등)이 없다(실측: APM 행 → {hostname}). 참조 처리기는 LLM 0이라(행이
    # 프롬프트에 들어가지 않는다) 선행 행을 그대로 넘긴다. `depends_on`만 건 선행 APM task도
    # 원천이다(V-5 — 없으면 이번 턴 표가 아니라 직전 턴 목록에서 행을 골랐다).
    if task.get("agent") in _ROW_REF_CONSUMER_AGENTS:
        ref_from = list(dict.fromkeys([*input_from, *_row_ref_dependencies(task, state)]))
        if ref_from:
            base["prior_result_rows"] = {
                tid: _prior_result_rows(prior.get(tid) or {}) for tid in ref_from
            }
            # 선행 task마다 한 표다 — 후보가 표를 넘으면 되묻는 문구의 표 이름 재료(R-1)
            plan = {str(t.get("task_id")): t for t in state.get("task_plan") or []
                    if isinstance(t, dict)}
            base["prior_result_tables"] = {
                tid: {"views": ((prior.get(tid) or {}).get("apm_query") or {}).get("views")
                      or (plan.get(str(tid)) or {}).get("views"),
                      "sub_query": (plan.get(str(tid)) or {}).get("sub_query")}
                for tid in ref_from
            }

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


# 옛 `_` 이름 별칭(D-337 ③ · plans/166 O4 — 1사이클)
_make_isolated_input = make_isolated_input


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
        out = {
            "final_response": direct,
            "routing_intent": "general_inference",
            "current_node": "general_inference",
        }
        # 저장 값 삭제 패널(D-187): 조회 단락의 구조화 컨텍스트를 task 결과로 운반
        if task.get("form_memory_panel"):
            out["form_memory_panel"] = task["form_memory_panel"]
        # 소스 선택 칩(plans/132 N-10) — 존 역질문과 같은 키로 운반한다(집계기 턴당 1회 단락 ·
        # 라우트 `clarification` · 재계획 스킵 · 존 재진입 스냅샷이 그대로 받는다).
        if task.get("source_clarification"):
            out["zone_clarification"] = task["source_clarification"]
        return out
    # 포탈 기능 질문 되돌림(plans/142 G-5 (b) · D-315) — 1·2단 수렴점. 사용법 단락(③.8) 질의·고정
    # 안내는 대상이 아니고, 좁은 결정적 판정(매뉴얼 항목명·의문 표지·검색 1위)이 성립할 때만이다.
    from src.orchestration.portal_help import redirect_general_inference  # 지연 — 순환 방지

    redirected = await redirect_general_inference(task, isolated, llm=llm, app_config=app_config)
    if redirected is not None:
        return redirected
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

    # 사용자별 DB 인가(D-232 · plans/116 §10.3 결함 ②) — 3단은 라우터 노드 경계에서 거르지만
    # 1·2단은 이 핸들러가 대상을 정한다. 조회 가능 DB가 없는 사용자는 분류(LLM)도 부르지 않는다.
    if denied_for_all(isolated):
        logger.info("data_query 인가 거부: 조회 가능 DB 없음 (task=%s)", task.get("task_id"))
        return access_denied_result()

    # 명시 소스 안내 단락(plans/132 G-1 「안내만」 · D-293) — 사용자가 이름으로 지목한 소스를 이
    # SQL 처리기가 조회할 수 없으면(지목 소스 비활성 · 비SQL 시스템) 조회하지 않고 안내만 한다.
    # 계획이 DB를 고정한 task(`db_ids` — 존 선택 재개 등)는 사용자 지목이 이미 반영된 것이라
    # 건너뛴다.
    if not task.get("db_ids"):
        _notice = _explicit_source_notice(sub_query, isolated, app_config)
        if _notice is not None:
            return _notice

    # (plans/102 X-7) 소유 검증 지점 ② — 분해 task의 답변 영역(LLM 구조화 출력 · D-004 원문
    # 스캔 0)으로 대상 시스템을 맞춘다. 플래그 off거나 task에 답변 영역이 없으면 종전 경로 그대로다.
    #   단일 DB 시스템 소유 → `db_ids` 고정(classify_dbs 재분류를 건너뛰는 기존 우선 배관)
    #   다중 존 시스템 소유 → 고정하지 않는다. 분류·위치 힌트 고정·승계 뒤 소유 DB 집합으로
    #                         **제한만**(고정하면 원문의 존 한정이 사라진다 — X-T13)
    # 이미 DB가 정해진 task(`db_ids`)·존 선택 재개 턴(`selected_db_ids`)은 건드리지 않는다.
    ownership_notes: list[dict[str, Any]] = []
    task_owner = None
    if (
        capability_ownership_on(app_config)
        and task.get("capability")
        and not task.get("db_ids")
        and not isolated.get("selected_db_ids")
    ):
        task_owner = resolve_capability_owner(
            str(task["capability"]), active_db_ids=app_config.multi_db.get_active_db_ids()
        )
        if task_owner is not None and not task_owner.active_db_ids:
            ownership_notes.append(owner_inactive_note(task_owner, task_id=task.get("task_id")))
            task_owner = None
        elif task_owner is not None and task_owner.pinned_db_id:
            task = {**task, "db_ids": [task_owner.pinned_db_id]}
            task_owner = None

    # 1) DB 선택 — db_ids 고정(②mapped_db_ids)이 있으면 우선, 없으면 classify_dbs.
    #    classify_dbs 후, 이번 턴에 새 위치/DB 신호가 없으면 직전 턴 DB를 승계한다(③, M2).
    #    selected_db_ids(존 선택 역질문, Plan 75 §4)는 복합 계획의 개별 task에도 적용되도록
    #    task.db_ids 다음 순위로 결합 — LLM 분류(classify_dbs)를 우회한다.
    # (plans/95 W-10) 존 선택 고정에 이번 턴 라우터가 남긴 존 미배정 DB를 합친다 —
    # 라우터에서 살린 대상을 여기서 다시 떨어뜨리면 같은 침묵 탈락이 순차 러너 경로에서
    # 되살아난다(3단도 이 부품을 쓴다 — docs/21 §7). 존 미배정 대상이 없으면 선택값 그대로다.
    # 존 답변 턴에 복원한 계획의 비게이트 task(plans/121 TP-1.2)는 선택 존으로 통째 고정하지 않고
    # 분류 뒤 존 그룹 대상만 바꾼다. 활성 시스템이 하나 이하면(운영 폴스타 전용) 종전 고정과 같은
    # 결과라 분류 LLM을 더 부르지 않도록 종전 경로를 그대로 탄다(§12.6 ①).
    zone_group_only = (
        task.get(ZONE_SELECTION_SCOPE_KEY) == ZONE_GROUP_ONLY
        and bool(isolated.get("selected_db_ids"))
        and _multiple_systems_active(app_config)
    )
    raw_targets = task.get("db_ids") or (
        None if zone_group_only
        else isolated.get("zone_selection_db_ids") or isolated.get("selected_db_ids")
    )
    db_succeeded = False
    db_pinned = False
    # 소유 플래그 off의 분류 폴백 노트(plans/121 TP-1.6) — 대상 확정·인가 뒤에 싣는다(아래).
    pending_fallback_note: dict[str, Any] | None = None
    if raw_targets:
        targets = normalize_targets(raw_targets, sub_query)
    else:
        # 존 역질문 조기 판정(plans/119 Q-1): 분류 결과와 무관하게 후단 게이트가 같은 답을
        # 내는 경우엔 DB 분류 LLM을 부르지 않고 여기서 되묻는다. 그 밖에는 None — 아래 종전
        # 경로가 판정한다.
        _early_zone_q = _zone_clarification_before_classify(task, isolated, sub_query, app_config)
        if _early_zone_q:
            return _zone_clarification_result(_early_zone_q)
        targets = await classify_dbs(llm, sub_query, app_config)
        # X-T3(plans/102) — classify_dbs 폴백 표지를 떼어 경과 노트로 올린다. 소유 플래그 on은
        # 종전 그대로 바로 싣는다. off(plans/121 TP-1.6)는 폴백 DB가 위치 힌트 고정·승계로
        # 바뀌지 않고 인가 필터 뒤에도 남을 때만 싣는다(인가 밖 DB 이름 비노출 — D-264).
        for _target in targets:
            _mark = _target.pop(_ROUTING_FALLBACK_KEY, None)
            if isinstance(_mark, dict):
                _fallback_note = routing_fallback_note(
                    str(_mark.get("reason") or REASON_NO_CLASSIFICATION),
                    db_id=str(_target.get("db_id") or ""),
                    cause=str(_mark.get("cause") or ""),
                    task_id=task.get("task_id"),
                )
                if capability_ownership_on(app_config):
                    ownership_notes.append(_fallback_note)
                else:
                    pending_fallback_note = _fallback_note
        # ① 이번 턴 원문 위치 힌트가 해소되면 DB 집합을 결정적으로 고정한다
        #    (LLM 분해/분류가 직전 턴 위치를 병합해도 원문 힌트가 이긴다 — 2026-07-16).
        targets, db_pinned = _apply_turn_hint_pinning(
            targets, isolated, sub_query, app_config.multi_db.get_active_db_ids(),
            zone_group_exclusive=(
                app_config.multi_db.zone_group_exclusive is True
            ),
        )
        if not db_pinned:
            targets, db_succeeded = _apply_db_succession(
                targets,
                sub_query,
                isolated.get("conversation_context"),
                app_config.multi_db.get_active_db_ids(),
                # 존 동시 조회 개방(D-206)이면 직전 DB 집합 전체를 승계한다
                inherit_all=(
                    app_config.multi_db.zone_group_exclusive is False
                ),
            )
        # 요청 소스 불가 사유(plans/121 TP-1.11a) — 원문 힌트가 등록·비활성 소스만 가리키면
        # 노트만 남긴다.
        from src.orchestration.conditional_agents import (  # 지연 — 순환 방지
            active_conditional_systems,
        )

        ownership_notes.extend(
            _source_unavailable_notes(
                isolated, app_config.multi_db.get_active_db_ids(),
                active_non_db=active_conditional_systems(app_config),
            )
        )
        if task_owner is not None:
            # 다중 존 시스템 소유 — 위치 힌트 고정·승계가 남긴 존 한정 위에서 소유 DB 집합으로
            # 제한만 한다.
            targets, _restricted = restrict_targets_to_owner(
                targets, task_owner, sub_query=sub_query, task_id=task.get("task_id"),
            )
            ownership_notes.extend(_restricted)
        if zone_group_only:
            targets = _apply_selection_to_zone_groups(
                targets, list(isolated.get("selected_db_ids") or []), sub_query,
            )

    # D-205 스코프 출처(구조화 키) — 처리현황 note 문자열이 아니라 이 값을 승격·보고한다.
    if task.get("db_ids"):
        db_origin = "selected" if isolated.get("selected_db_ids") else "planned"
    elif isolated.get("selected_db_ids"):
        db_origin = "selected"
    elif db_pinned:
        db_origin = "hint"
    elif db_succeeded:
        db_origin = "inherited"
    else:
        db_origin = "classified"

    # 사용률 소스 가드(D-308 ⑨ · plans/132) — 대상 출처와 무관하게 확정된 대상에 건다. 존 역질문
    # 앞에 둔다(비소유 DB를 뺀 뒤 관측 DB 존이 남으면 존 질문이 맞다).
    targets, _util_notice, _util_removed = _utilization_source_guard(
        task, sub_query, isolated, targets, app_config,
    )
    if _util_notice is not None:
        return _util_notice

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
    if not raw_targets and not zone_group_only:
        _zone_q = _zone_clarification_or_none_task(
            task, isolated, targets,
            db_pinned=db_pinned, db_succeeded=db_succeeded, app_config=app_config,
        )
        if _zone_q:
            return _zone_clarification_result(_zone_q)

    # 사용자별 DB 인가(D-232) — 대상 출처(계획 고정·존 선택·분류·위치 힌트 고정·승계·소유 제한)와
    # 무관하게 확정된 대상에 3단과 같은 필터를 건다. 존 역질문 뒤에 두는 것도 3단과 같다(라우터 안의
    # 존 게이트 → `authorized_router`). 인가된 대상이 없으면 조회하지 않고 사유를 돌려준다.
    authorized = authorize_targets(targets, isolated)
    if authorized is None:
        return access_denied_result()
    targets = authorized
    # 분류 폴백 노트(plans/121 TP-1.6 · 소유 플래그 off) — 존 역질문·인가 거부는 위에서 이미
    # 반환했다. 폴백 DB가 그대로 조회 대상일 때만 싣는다.
    if (
        pending_fallback_note is not None
        and not (db_pinned or db_succeeded)
        and pending_fallback_note["db_id"] in {t.get("db_id") for t in targets}
    ):
        ownership_notes.append(pending_fallback_note)
    # 사용률 가드 교정 노트(D-308) — 같은 이유로 인가 뒤 확정 대상 기준으로 싣는다(D-264 ②).
    if _util_removed:
        _util_note = _utilization_correction_note(
            task, _util_removed, targets, isolated, app_config,
        )
        if _util_note is not None:
            ownership_notes.append(_util_note)

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
                rt_result = {
                    **rt,
                    "target_databases": targets,
                    "is_multi_db": len(targets) > 1,
                    "active_db_id": targets[0]["db_id"],
                    # D-205 승격 재료 — SQL 경로 반환부와 대칭(plans/120 S-1b: 없으면 이 턴의
                    # `db_scope.db_ids`가 스트림·비스트림 모두 비고 다음 턴 DB 승계가 끊긴다)
                    "target_db_ids": _target_ids,
                    "db_origin": db_origin,
                }
                if ownership_notes:
                    rt_result["dependency_notes"] = (
                        list(rt_result.get("dependency_notes") or []) + ownership_notes
                    )
                return rt_result
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
        await emit_step("pipeline.multi_db", "start", label=f"멀티 DB 조회 {len(targets)}곳")
        s.update(await multi_db_executor(s, llm=llm, app_config=app_config))
        s.update(await result_merger(s, app_config=app_config))
        await emit_step("pipeline.multi_db", "end")
    else:
        s.update(await _run_single_db_pipeline(s, llm, app_config))

    # 3) 결과 정리
    # 실행 단계의 실패 사유(검증·실행 재시도 소진 · 전 DB 실패)는 결과 정리가 덮지 못하게 먼저 잡는다.
    # result_organizer는 0건이어도 `error_message: None`을 돌려줘, 실패가 "0건 완료"로 바뀌고 후속
    # 게이트·집계기가 "데이터가 없습니다"로 답했다(P-13 — 2026-09-17 로컬 C-06 재현, 그래프 경로는
    # 같은 실패를 error_response로 보낸다).
    pipeline_error = s.get("error_message")
    # SQL 루프가 사유 문구를 남기고 끝났으면(산문 조기 종결 N-5 · 조회 마감 T-1ⓑ·T-3) 정리할
    # 결과가 없다 — 결과 정리를 건너뛰고 그 문구를 응답 본문으로 싣는다. 결과 정리·출력 생성을
    # 거치면 0행이라 집계기가 "조건에 해당하는 … 데이터가 없습니다"로 답해 사유가 사라진다(침묵
    # 강등). 집계기는 `organized_data`가 없는 결과의 `final_response`를 그대로 쓴다
    # (`_finalize_task` 텍스트 계열 분기). 산문 문구는 그래프 `error_response`와 같은 함수다
    # (`non_sql_prose_response` · 경로 대칭).
    stop_response = s.get("regen_stop_response") if pipeline_error else None
    if stop_response:
        stopped = pack_pipeline_result(
            s, targets, pipeline_error,
            ownership_notes=ownership_notes, db_origin=db_origin,
            db_succeeded=db_succeeded, db_pinned=db_pinned,
        )
        stopped["organized_data"] = None
        stopped["final_response"] = stop_response
        return stopped
    await emit_step("pipeline.organize", "start", label="결과 정리")
    s.update(await result_organizer(s, llm=llm, app_config=app_config))
    await emit_step("pipeline.organize", "end")

    return pack_pipeline_result(
        s, targets, pipeline_error,
        ownership_notes=ownership_notes, db_origin=db_origin,
        db_succeeded=db_succeeded, db_pinned=db_pinned,
    )


def _task_regen_stop(s: dict[str, Any]) -> dict[str, str] | None:
    """실패 task에 실을 `regen_stop` — 단일 DB는 루프가 남긴 표지, 멀티 DB는 DB별 표지를 합친다.

    멀티 DB는 **실패한 DB 전부**가 SQL 루프에서 멈췄을 때만 싣는다. 연결 오류·미등록·테이블
    없음 등 SQL 루프 밖 실패가 하나라도 섞이면 재위임이 무익하다고 단정할 수 없어 싣지 않는다
    (재계획기 판단에 맡긴다). 사유가 DB마다 다르면 조회 마감(요청 전체의 사유)이 앞서고,
    산문·검증 소진이 섞이면 검증 소진으로 본다. detail은 DB별 마지막 사유를 잇는다.
    """
    stop = s.get("regen_stop")
    if isinstance(stop, dict) and stop.get("reason"):
        return {"reason": str(stop["reason"]), "detail": str(stop.get("detail") or "")}
    stops = s.get("regen_stops")
    if not isinstance(stops, dict) or not stops:
        return None
    failed = [d for d in (s.get("db_errors") or {}) if d]
    if not failed or any(not isinstance(stops.get(d), dict) for d in failed):
        return None
    reasons = {str(stops[d].get("reason") or "") for d in failed}
    if len(reasons) == 1:
        reason = reasons.pop()
    elif REGEN_STOP_DEADLINE in reasons:
        reason = REGEN_STOP_DEADLINE
    else:
        reason = REGEN_STOP_VALIDATION_BUDGET
    detail = " / ".join(f"{d}: {stops[d].get('detail') or ''}" for d in failed)
    return {"reason": reason, "detail": detail}


def _executed_sqls_by_db(
    s: dict[str, Any], targets: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """실행한 SQL을 DB별 마지막 1건으로 모은다(plans/123 W-1 ①) — `[{db_id, sql}]`.

    멀티 DB는 `db_executed_sqls`(DB별 실행 SQL · plans/113 S-1)를, 단일 DB는 `query_attempts`의
    마지막 성공 시도(없으면 마지막 시도)를 쓴다 — 재시도 전 SQL의 상한·주석이 고지 판정에 섞이지
    않게 한다. 실행하지 않았으면 빈 목록이다.
    """
    by_db = s.get("db_executed_sqls")
    if isinstance(by_db, dict) and by_db:
        return [{"db_id": str(d), "sql": str(q)} for d, q in by_db.items() if q]
    attempts = [a for a in (s.get("query_attempts") or []) if isinstance(a, dict) and a.get("sql")]
    if not attempts:
        return []
    ok = [a for a in attempts if a.get("success")]
    last = (ok or attempts)[-1]
    db_id = str(s.get("active_db_id") or (targets[0].get("db_id") if targets else "") or "")
    return [{"db_id": db_id, "sql": str(last["sql"])}]


def pack_pipeline_result(
    s: dict[str, Any],
    targets: list[dict[str, Any]],
    pipeline_error: str | None,
    *,
    ownership_notes: list[dict[str, Any]],
    db_origin: str,
    db_succeeded: bool,
    db_pinned: bool,
) -> dict[str, Any]:
    """조회 파이프라인 상태(``s``)를 task 결과 dict로 접는다.

    2단 핸들러(``run_data_query_pipeline``)와 3단 task 서브그래프의 ``pack_outcome``
    (plans/103 P1-1)이 같은 함수를 쓴다 — 결과 모양이 두 경로에서 갈라지지 않게 한다(D-053).
    """
    # 측정 연결점(D-301 ⑥) — 수신 함수가 설치된 측정 전용 서버에서만 동작한다(기본 no-op).
    run_capture.emit("task_pipeline_state", s)
    result: dict = {
        "organized_data": s.get("organized_data"),
        "query_results": s.get("query_results"),
        "source": targets,
    }
    # 멀티 DB 순위 전역 재정렬(plans/113 S-1)이 적용됐으면 선행 결과 전달(`prior_rows`·
    # `prior_targets`·순차 경과 판정)도 응답에 보인 **전체 기준 상위 N**을 쓴다 — 세 판독처가
    # `rows`를 1순위로 읽는다. 원본 병합(`query_results` — CSV 원천)은 그대로다. 적용 여부는
    # 결과 정리가 이번 행에 대해 확정한 표지(`organized_data.merge_ranking`)로 판정한다.
    _organized = s.get("organized_data")
    _ranking = s.get("merged_ranking")
    if (
        isinstance(_organized, dict)
        and (_organized.get("merge_ranking") or {}).get("applied")
        and isinstance(_ranking, dict)
        and _ranking.get("applied")
    ):
        result["rows"] = list(_ranking.get("rows") or [])
    error = pipeline_error or s.get("error_message")
    if error:
        result["error"] = error
        # SQL 재생성 루프 종결 표지(plans/119 Q-3 계약) — 유효 SQL 없이 끝난 **실패 task에만**
        # 싣는다.
        stop = _task_regen_stop(s)
        if stop is not None:
            result["regen_stop"] = stop
    # DB별 스코프 분할 경과(D-203) — multi_db_executor가 낸 미조회 DB·노트를 task 결과로 승격한다.
    # group_results(D-206 존 순차 실행 경과)도 처리현황에 실린다.
    for _dk in ("dependency_notes", "skipped_dbs", "group_results"):
        if s.get(_dk):
            result[_dk] = s[_dk]
    if ownership_notes:
        # 답변 영역 소유 교정·분류 폴백 경과(plans/102 X-7·X-T3) — 같은 채널로 집계기·API에 닿는다.
        result["dependency_notes"] = list(result.get("dependency_notes") or []) + ownership_notes

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
    result["db_origin"] = db_origin  # D-205: _collect_db_promotion이 db_scope_source로 승격
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
    # 실제로 실행한 SQL(DB별 마지막 1건 · plans/123 W-1 ①) — 2단 결정적 고지(상한 도달 · 생성기
    # 자기 고백 · 조건 반영 대조)의 입력이다. 종전에는 집계기가 출력 생성에 넘기는 입력에 실행 SQL이
    # 없어 10,000행 도달 17턴에 고지가 0이었다(run `20260923-103638`).
    executed = _executed_sqls_by_db(s, targets)
    if executed:
        result["executed_sqls"] = executed
    # 급증 조회 한계 표기(D-176 후속2)도 task 결과로 싣는다(W-3) — 없으면 2단 응답에서 빠졌다.
    if s.get("spike_notes"):
        result["spike_notes"] = list(s["spike_notes"])
    db_errors = s.get("db_errors")
    if db_errors:
        result["db_errors"] = db_errors
    # 존별 건수 요약 — output_generator의 존 커버리지 각주(_append_zone_coverage_notes)가
    # 소비한다. db_errors만 전파하면 "0행 존 생략"이 트랙 A에서 보이지 않는다.
    db_result_summary = s.get("db_result_summary")
    if db_result_summary:
        result["db_result_summary"] = db_result_summary
    # 0건 원인 진단(D-176 후속1) — 결과 정리가 남긴 결정적 신호를 task 결과로 승격한다(plans/121
    # TP-11.5). 없으면 2·1단 0건 응답이 퍼널 사유 없이 종전 문구로만 나갔다(3단 단일 경로와 비대칭).
    empty_diagnosis = s.get("empty_diagnosis")
    if empty_diagnosis:
        result["empty_diagnosis"] = empty_diagnosis
    # task 시간 해석(plans/122 T-4·T-8 · D-309) — 집계기가 task 마감 입력·원천별 기간 고지
    # (`period_sources`)에 쓴다. task 문장에 명시 기간이 있으면 원문 해석과 다르다. 없으면(플래그
    # off) 싣지 않는다 — 집계기는 턴 해석으로 내려간다.
    if isinstance(s.get("time_resolution"), dict):
        result["time_resolution"] = s["time_resolution"]
    return result


# 옛 `_` 이름 별칭(D-337 ③ · plans/166 O4 — 1사이클)
_pack_pipeline_result = pack_pipeline_result


# ──────────────────────────────────────────────
# registry (handler 정의 이후)
# ──────────────────────────────────────────────


def resolve_subagent(name: str | None, app_config: Any) -> SubAgentSpec | None:
    """고정 처리기 목록 → (없으면) 활성인 조건부 처리기(plans/125 A-3 · plans/127 — `apm_query` ·
    `doc_query`).

    조건부 처리기는 그 소스가 활성일 때만 있다 — 비활성이면 종전처럼 None(호출부 폴백).
    """
    spec = SUBAGENT_REGISTRY.get(name or "")
    if spec is not None:
        return spec
    from src.orchestration.conditional_agents import active_conditional_agents  # 지연 — 순환 방지

    return active_conditional_agents(app_config).get(name or "")

SUBAGENT_REGISTRY: dict[str, SubAgentSpec] = {
    # 처리기 계약(plans/121 §4.4 · TP-2.1a) — 값은 현행 코드 실측이고 소비처는 아직 없다.
    # `purpose`는 분해 프롬프트(`prompts/intent_planner.py` 「사용 가능한 agent」)의 현행 줄
    # 그대로다(강조 표기 포함 — TP-2.1b가 이 값으로 렌더해도 분해 목록이 바이트 그대로이도록).
    "data_query": SubAgentSpec(
        "data_query",
        "인프라 DB(서버 사양·사용량·성능 통계) 조회 — 알람/이벤트(event) 조회는 alarm_query 담당",
        run_data_query_pipeline,
        purpose="인프라 DB(서버 사양·사용량·VM·자산·프로세스 **이력/추세** 등) 조회",
        # 실시간 사용률 분기(REST)는 옵트인 기본 off(`POLESTAR_REST_REALTIME_USAGE_ENABLED`)
        backend="sql",
        input_slots=("entity_set", "db_set", "time_window"),
        output_type="rows",
        # 선행 결과 → 조사 대상 해소(`build_prior_targets`)가 행에서 뽑는 패싯(ip는 조건에서만)
        key_facets_out=("hostname", "server_name"),
        self_filters=("zone", "time", "host"),
        max_bind_values=_MAX_PRIOR_ROWS,  # 선행 행 주입 상한 — 넘는 값은 절단(한 IN 목록)
        prerequisites=("structure_info",),  # 구조 정보 없으면 사유 노트(D-227 structure_missing)
        # timeout: 고정 초 없음 — 요청 마감(`retrieval_remaining`)에서 파생
    ),
    "process_query": SubAgentSpec(
        "process_query",
        "특정 서버의 현재/실시간 프로세스 리스트 조회 (DB 이력이 아닌 실시간 폴스타 프로세스 API)",
        # 본체 감사 task당 1건(plans/121 TP-1.13) — 세 단이 모두 이 handler를 부른다
        audited_investigation(run_process_query, BACKEND_PROCESS_API),
        purpose=(
            "특정 서버의 **현재/실시간 프로세스 리스트**(실행 중 프로세스·top 프로세스) 조회 "
            "(DB 이력이 아닌 실시간 API)"
        ),
        backend="rest",  # 존별 프로세스 API(httpx GET)
        input_slots=("entity_set", "db_set"),
        required_inputs=("entity_set",),  # 대상 서버 미식별이면 조회하지 않고 안내
        output_type="rows",
        # 프로세스 행(pid)은 서버 대상으로 해소하지 않는다(`looks_like_process_rows`)
        key_facets_out=(),
        self_filters=("zone", "host"),
        # 대상 상한은 설정값 `composite.max_targets` · 대상별 API 호출 1회 · fan-out 동시 수는
        # `composite.fanout_concurrency` · 시간 상한은 `composite.*_timeout_seconds`(설정값)
        bind_block=1,
        # 존별 프로세스 API 매핑(`get_process_api_base_url`)이 없으면 안내로 끝난다
        prerequisites=("process_api_mapping",),
    ),
    "alarm_query": SubAgentSpec(
        "alarm_query",
        "알람/모니터링 이벤트(event) 조회 — 알람 현황·이력, event 발생 서버, alert, 경보",
        run_data_query_pipeline,
        purpose="알람/모니터링 이벤트(알람 현황·이력·임계값 초과·alert) 조회",
        backend="sql",  # data_query와 같은 handler
        input_slots=("entity_set", "db_set", "time_window"),
        output_type="rows",
        key_facets_out=("hostname", "server_name"),
        self_filters=("zone", "time", "host"),
        max_bind_values=_MAX_PRIOR_ROWS,
        prerequisites=("structure_info",),
    ),
    "cache_management": SubAgentSpec(
        "cache_management", "스키마 캐시·유사어 관리", run_cache_management,
        purpose="스키마 캐시 생성/갱신/삭제, 유사어 관리, 컬럼/DB 설명 변경",
        backend="sql",  # 캐시 생성 동작만 DB 구조를 읽는다(`refresh_cache`) — 나머지는 앱 캐시
        output_type="text",
    ),
    "synonym_registration": SubAgentSpec(
        "synonym_registration", "유사어 등록", run_synonym_registration,
        purpose="유사어 등록",
        backend="none",  # 앱 유사어 저장소만 쓴다 — 관측 데이터 소스에 닿지 않는다
        output_type="text",
    ),
    "general_inference": SubAgentSpec(
        "general_inference", "DB 미접근 일반 응답", run_general_inference,
        fallback=True, needs_history=True,
        purpose=(
            "DB에 접근하지 않는 일반 응답(개념 설명, 인사, 범위 외 요청). **최후 수단(fallback)**"
        ),
        backend="none",
        output_type="text",
    ),
    # Plan 78 W3-1·W3-2 (WU-18) — 중간 비용대. `data_query`(DB SQL)와 `fault_diagnosis`
    # (sre_agent 위임) 사이의 공백을 메운다. **도구 수를 늘리지 않는다**(W3-4): 프로파일
    # 4종을 `profile` 인자로 흡수한다. 목록 노출은 플래그와 무관하다 — 1단 도구 목록은
    # 고정이고(78 P14 · `deepagents_tools.py` 목록 고정 주석), 2단 진입은 분해 뒤 결정적 교정
    # (`intent_planner._coerce_host_inspect_intent` · 플래그 on에서만)뿐이다. 가용성은
    # handler 진입부 게이트(`run_host_inspect` — 플래그 off면 구조화 거부)가 막는다.
    HOST_INSPECT_AGENT: SubAgentSpec(
        HOST_INSPECT_AGENT,
        "특정 서버의 OS 구성·자원 현황·메트릭 추세 단건 조회 "
        "(DB SQL도 장애 진단 위임도 아닌 mcp_server 고수준 도구 경로)",
        audited_investigation(run_host_inspect, BACKEND_MCP),  # 본체 감사(plans/121 TP-1.13)
        # 분해 프롬프트에는 줄이 없다 — `description`·1단 지시문 줄의 공통 앞부분
        purpose="특정 서버의 OS 구성·자원 현황·메트릭 추세 단건 조회",
        backend="mcp",  # `DBHubClient.inspect_host`
        input_slots=("entity_set", "db_set"),
        required_inputs=("entity_set",),  # 대상 미식별이면 구조화 거부(target_unresolved)
        output_type="rows",  # 거부·실패는 텍스트 결과(TP-1.5)
        key_facets_out=(),  # 서버 계약 행의 식별 열 미확인 — 조사 대상은 `target` 키에 따로 싣는다
        self_filters=("zone", "host"),
        max_bind_values=1,  # 단건 — 첫 대상만 조사하고 나머지는 절단 고지
        bind_block=1,
        # 조사 플래그(`composite.investigation_enabled`) off면 거부 ·
        # `inspect_host`는 MCP 백엔드 클라이언트에만 있다
        prerequisites=("investigation_enabled", "mcp_backend"),
    ),
}
