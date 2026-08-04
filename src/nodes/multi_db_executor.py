"""멀티 DB 실행 노드.

시멘틱 라우팅 결과에 따라 여러 DB에 대해
스키마 분석 -> SQL 생성 -> 검증 -> 실행을 수행한다.
각 DB별로 독립적으로 파이프라인을 실행하며,
부분 실패 시 성공한 결과와 실패 정보를 모두 반환한다.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

import sqlparse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.utils.llm_compat import is_kbgenai
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.nodes.candidate_generator import classify_complexity
from src.nodes.query_validator import check_left_join_where_demotion as _check_left_join_where_demotion
from src.nodes.semantic_compiler import compile_from_nl
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.routing.db_registry import DBRegistry
from src.routing.domain_config import get_domain_by_id
from src.security.audit_logger import log_query_execution
from src.state import AgentState, QueryAttempt
from src.utils.query_gen_common import (
    build_generic_period_hint,
    build_prior_rows_block,
    build_stat_month_block,
    correct_servername_hostname_mapping,
    extract_sql_from_response,
    resolve_query_limit,
    resolve_stat_month_range,
)
# 단일/멀티 경로 공유 프롬프트 블록 빌더(Plan 69 P3-1, D-066). 폴스타 스키마 리터럴은
# 공용 빌더에 두지 않고 이 파일이 인자로 주입한다(D-088 — overfit 기준선은 호출부 기준).
from src.nodes.prompt_blocks import (
    EAV_JOIN_RULE_BLOCK,
    build_eav_pivot_block,
    build_forbidden_join_block,
    build_query_examples,
    build_schema_prefix_rule,
    build_stepwise_deps,
    build_unmapped_fields_block,
    build_value_index_injection,
    build_value_joins_block,
    eav_patterns_of,
    filter_mapping_by_schema,
    first_eav_pattern,
    format_schema_text,
    path_parity_enabled,
    prior_server_scope,
    select_history_fewshot,
    split_eav_by_resource_type,
    split_mapping_entries,
)
# 폴스타 EAV/피벗 결정적 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트.
# 아래 4종은 `DBAdapter` 훅 표면에 대응하는 훅이 없어 직접 임포트로 남긴다. 새 훅 신설은
# 두 번째 어댑터가 생기기 전까지 금지(Plan 63 §9) — 훅이 있는 `classify_metric_field`만
# 레지스트리 경유로 옮겼다(단일 경로 query_generator와 대칭).
from src.db_adapters.polestar.assembler import (
    METRIC_PIVOT_KEYS,
    METRIC_PIVOT_TABLE,
    build_form_fill_pivot_sql,
    build_multi_resource_pivot_block,
    decimal_cast_example,
    eav_attr_resource_types,
)
# 지표 필드 분류는 어댑터 레지스트리 경유 도구를 쓴다(D-089). 검증 코어가 도구 계층으로
# 내려가 tools→nodes 역참조가 사라졌으므로 모듈 수준 임포트가 안전하다(후속 2단계).
from src.tools.metrics import classify_metric_field

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 플래그 ON 경로에서만 수행한다.
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)

# 공유 빌더에 주입하는 DB 특화 리터럴 — 공용 빌더(prompt_blocks)로 옮기지 않고 호출부인
# 이 파일에 남긴다(D-088: 공용 계층 DB-agnostic, overfit_check 기준선은 파일 단위).
_ENTITY_RESOURCE_TYPE = "server.Server"     # 엔티티(서버) 자신의 resource_type
_EAV_HOST_ATTRIBUTE = "Hostname"            # 브릿지 조인 예시의 엔티티 식별 속성
_EAV_LINK_COLUMN = "configuration_id"       # config 행끼리 잇는 컬럼
_SCHEMA_EXAMPLE_TABLE = "cmm_resource"      # 스키마 한정 규칙 예시 테이블
_FOREIGN_SCHEMA_PREFIX = "polestar."        # 붙이지 말아야 할 접두사 예시


def _get_eav_pattern(schema_info: Optional[dict]) -> Optional[dict]:
    """_structure_meta에서 첫 번째 EAV 패턴을 반환한다.

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴 딕셔너리 또는 None
    """
    return first_eav_pattern(schema_info)


@dataclass
class _MultiRun:
    """대상 DB 루프가 공유하는 입력과 누적 수집기 (Plan 69 P5-2).

    per-DB 처리를 ``_run_single_target``으로 떼어내면서, 루프 밖에서 만들어 여러 DB가
    함께 채우는 값(결과·에러·시도 이력·SQL 캐시)을 한 묶음으로 전달한다.
    """

    state: AgentState
    llm: BaseChatModel
    app_config: AppConfig
    registry: DBRegistry
    parsed_requirements: dict
    effective_limit: int
    unmapped_fields: list[str]
    prior_block: str | None
    prior_scope: tuple[str, list[str]] | None
    value_index: dict[str, list[str]] | None
    db_results: dict[str, list[dict]]
    db_schemas: dict[str, dict]
    db_errors: dict[str, str]
    all_attempts: list[QueryAttempt]
    mc_candidates: list[dict]
    mc_derivations: list[dict]
    sql_by_schema: dict[tuple, str]


def _prepare_multi_run(
    state: AgentState,
    llm: BaseChatModel | None,
    app_config: AppConfig | None,
) -> _MultiRun:
    """대상 DB 루프에 필요한 설정·상한·스코프와 누적 수집기를 준비한다."""
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    registry = DBRegistry(app_config)
    targets = state.get("target_databases", [])
    parsed_requirements = state.get("parsed_requirements", {})

    # "전체/모든" 조회는 LIMIT를 상향해 1000건 절단을 방지한다 — 단일 DB 경로와 동등화(RC4/D-066).
    # 표면어가 미매칭이면 input_parser LLM 산출물(limit)로 2단 폴백한다(Plan 67 R3-(i),
    # 단일 경로 query_generator와 동일 규칙 — 한쪽만 폴백하는 비대칭 금지).
    effective_limit = resolve_query_limit(
        state.get("user_query", ""), app_config.query.default_limit,
        parsed_limit=parsed_requirements.get("limit"),
    )
    # 미매핑 필드(사용률 지표 등, column_mapping=None) — SQL이 한글 헤더로 alias하도록 전달한다.
    # db_column_mapping[db_id]에는 미매핑 필드가 없으므로 통합 column_mapping에서 추출한다(D-066 후속3).
    unmapped_fields = [
        f for f, c in (state.get("column_mapping") or {}).items() if c is None
    ]

    db_results: dict[str, list[dict]] = {}
    db_schemas: dict[str, dict] = {}
    db_errors: dict[str, str] = {}
    all_attempts: list[QueryAttempt] = list(state.get("query_attempts", []))
    mc_candidates: list[dict] = []  # 트랙 A 다중 후보(경로 C, DB별 태깅) — 관측/감사용
    # 트랙 S(S2/D-128) 단계적 도출 기록(DB별) — 단일 경로(query_generator)와 동일 형태로
    # state에 실어 감사·평가 재료를 대칭 확보한다(D-066). 루프 미발동이면 빈 리스트 → None.
    mc_derivations: list[dict] = []

    # 동일 스키마(엔진+스키마명) DB는 SQL을 한 번만 생성해 재사용한다(D-066 후속6).
    # 공동존(gp/yd)은 스키마가 동일해 같은 SQL이 양쪽에서 동작하는데, DB별 독립 LLM 호출은
    # alias를 비결정적으로 만들어(서버 이름 vs 서버명) 병합 결과 컬럼이 어긋나고 폼필이 깨진다.
    # 첫 DB의 검증된 SQL을 같은 스키마의 나머지 DB에 재사용해 컬럼명을 일관되게 만든다.
    _sql_by_schema: dict[tuple, str] = {}

    # 선행 task 결과 서버 스코프 — 단일 경로(query_generator)와 대칭 배선(D-086/D-066).
    # prior_scope(결정적 컴파일 전달용)·value_index는 경로 대칭 ON일 때만 소비된다(P3-2).
    prior_block = build_prior_rows_block(state.get("prior_rows"))
    prior_scope = prior_server_scope(state.get("prior_rows"))
    value_index = state.get("column_value_index")

    return _MultiRun(
        state=state, llm=llm, app_config=app_config, registry=registry,
        parsed_requirements=parsed_requirements, effective_limit=effective_limit,
        unmapped_fields=unmapped_fields, prior_block=prior_block,
        prior_scope=prior_scope, value_index=value_index,
        db_results=db_results, db_schemas=db_schemas, db_errors=db_errors,
        all_attempts=all_attempts, mc_candidates=mc_candidates,
        mc_derivations=mc_derivations, sql_by_schema=_sql_by_schema,
    )


async def _record_success(
    run: _MultiRun, db_id: str, sql: str, result: Any, exec_start: float,
) -> None:
    """성공한 실행의 결과·시도 이력·감사 로그를 남긴다."""
    elapsed_ms = (time.time() - exec_start) * 1000

    run.db_results[db_id] = result.rows
    run.all_attempts.append(QueryAttempt(
        sql=sql,
        success=True,
        error=None,
        row_count=result.row_count,
        execution_time_ms=round(elapsed_ms, 2),
    ))

    await log_query_execution(
        sql=sql,
        row_count=result.row_count,
        execution_time_ms=elapsed_ms,
        success=True,
        retry_attempt=run.state.get("retry_count", 0),
        user_id=run.state.get("user_id"),
        thread_id=run.state.get("thread_id"),
        source_name=db_id,
    )

    logger.info(
        "DB '%s' 쿼리 완료: %d건, %.0fms",
        db_id, result.row_count, elapsed_ms,
    )


async def _record_failure(
    run: _MultiRun, db_id: str, sql: str, exc: Exception, exec_start: float | None,
) -> None:
    """실패한 실행의 SQL·경과를 보존하고 감사 로그를 남긴다.

    단일 경로(4경로 전부 감사)와 대칭 (Plan 69 P0-⑤) — 실패도 감사에서 빠지지 않는다.
    """
    error_msg = f"DB '{db_id}' 실행 에러: {str(exc)}"
    run.db_errors[db_id] = error_msg
    logger.error(error_msg)

    failed_elapsed = (time.time() - exec_start) * 1000 if exec_start else 0
    run.all_attempts.append(QueryAttempt(
        sql=sql,
        success=False,
        error=str(exc),
        row_count=0,
        execution_time_ms=round(failed_elapsed, 2),
    ))
    await log_query_execution(
        sql=sql,
        row_count=0,
        execution_time_ms=failed_elapsed,
        success=False,
        error=str(exc),
        retry_attempt=run.state.get("retry_count", 0),
        user_id=run.state.get("user_id"),
        thread_id=run.state.get("thread_id"),
        source_name=db_id,
    )


async def _generate_validated_sql(
    run: _MultiRun,
    client: Any,
    schema_info: dict,
    sub_context: str,
    db_mapping: dict,
    *,
    db_engine: str,
    db_id: str,
) -> tuple[str, str | None]:
    """대상 DB의 SQL을 생성하고 간이 검증한다(실패 시 1회 재생성).

    Returns:
        (sql, 검증 오류) — 오류가 남아 있으면 호출부가 이 DB를 건너뛴다.
    """
    async def _mc_execute(_sql: str) -> dict:
        try:
            _r = await client.execute_sql(_sql)
            return {"rows": _r.rows, "error": None}
        except Exception as _e:  # noqa: BLE001
            return {"rows": None, "error": str(_e)}

    sql = await _generate_sql(
        run.llm, run.parsed_requirements, schema_info,
        sub_context, run.effective_limit,
        column_mapping=db_mapping,
        db_engine=db_engine,
        db_id=db_id,
        unmapped_fields=run.unmapped_fields,
        app_config=run.app_config,
        execute=_mc_execute,
        candidate_sink=run.mc_candidates,
        prior_block=run.prior_block,
        derivation_sink=run.mc_derivations,
        prior_scope=run.prior_scope,
        value_index=run.value_index,
    )

    # 3. SQL 검증 (간이)
    validation_error = _validate_sql(
        sql, schema_info, db_id=db_id, db_engine=db_engine,
        user_query=run.state.get("user_query", ""), app_config=run.app_config,
    )
    if not validation_error:
        return sql, None

    # 1회 재시도
    logger.warning(
        "DB '%s' SQL 검증 실패, 재생성 시도: %s",
        db_id, validation_error,
    )
    sql = await _generate_sql(
        run.llm, run.parsed_requirements, schema_info,
        sub_context, run.effective_limit,
        error_context=validation_error,
        column_mapping=db_mapping,
        db_engine=db_engine,
        db_id=db_id,
        unmapped_fields=run.unmapped_fields,
        app_config=run.app_config,
        prior_block=run.prior_block,
        prior_scope=run.prior_scope,
        value_index=run.value_index,
    )
    return sql, _validate_sql(
        sql, schema_info, db_id=db_id, db_engine=db_engine,
        user_query=run.state.get("user_query", ""), app_config=run.app_config,
    )


async def _run_single_target(target: dict, run: _MultiRun) -> None:
    """대상 DB 한 곳에 대해 스키마 분석 → SQL 생성·검증 → 실행을 수행한다.

    결과·에러·시도 이력은 ``run``의 수집기에 담는다(실패해도 다른 DB 처리는 계속된다).
    """
    db_id = target["db_id"]
    sub_context = target.get("sub_query_context", run.state["user_query"])
    # 실패 경로에서도 attempt·감사에 SQL·경과를 보존하기 위한 루프 스코프 (Plan 69 P0-⑤)
    sql = ""
    exec_start: float | None = None

    if not run.registry.is_registered(db_id):
        run.db_errors[db_id] = f"DB '{db_id}'이(가) 레지스트리에 등록되지 않았습니다."
        logger.warning("미등록 DB 스킵: %s", db_id)
        return

    try:
        async with run.registry.get_client(db_id) as client:
            # 1. 스키마 분석
            schema_info = await _analyze_schema(
                client, run.parsed_requirements,
                db_id=db_id, app_config=run.app_config,
            )
            run.db_schemas[db_id] = schema_info

            if not schema_info.get("tables"):
                run.db_errors[db_id] = f"DB '{db_id}'에서 테이블을 찾을 수 없습니다."
                return

            # 2. SQL 생성 (DB별 column_mapping 전달)
            db_mapping = run.state.get("db_column_mapping", {}).get(db_id, {}) if run.state.get("db_column_mapping") else {}
            # DB 엔진 정보 조회
            domain_cfg = get_domain_by_id(db_id)
            db_engine = domain_cfg.db_engine if domain_cfg else "postgresql"
            db_schema = domain_cfg.db_schema if domain_cfg else ""
            schema_key = (db_engine, db_schema)

            cached_sql = run.sql_by_schema.get(schema_key)
            if cached_sql:
                # 동일 스키마 DB: 검증된 SQL 재사용 → 컬럼 alias 일관성 보장(폼필 병합 정합)
                sql = cached_sql
                logger.info(
                    "DB '%s': 동일 스키마%s SQL 재사용 (alias 일관성)", db_id, schema_key
                )
            else:
                sql, validation_error = await _generate_validated_sql(
                    run, client, schema_info, sub_context, db_mapping,
                    db_engine=db_engine, db_id=db_id,
                )
                if validation_error:
                    run.db_errors[db_id] = f"SQL 검증 실패: {validation_error}"
                    return
                # 검증 통과한 SQL을 스키마 키로 캐시 (다음 동일 스키마 DB가 재사용)
                run.sql_by_schema[schema_key] = sql

            # 4. SQL 실행
            exec_start = time.time()
            result = await client.execute_sql(sql)
            await _record_success(run, db_id, sql, result, exec_start)

    except Exception as e:
        await _record_failure(run, db_id, sql, e, exec_start)



async def multi_db_executor(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """여러 DB에 대해 쿼리 파이프라인을 실행한다.

    각 대상 DB별로:
    1. 스키마 분석
    2. SQL 생성
    3. SQL 검증 (간이)
    4. SQL 실행

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - db_results: DB별 쿼리 결과
        - db_schemas: DB별 스키마 정보
        - db_errors: DB별 에러 메시지
        - query_results: 전체 병합 결과
        - query_attempts: 실행 이력
        - current_node: "multi_db_executor"
    """
    run = _prepare_multi_run(state, llm, app_config)
    targets = state.get("target_databases", [])
    for target in targets:
        await _run_single_target(target, run)

    # 전체 병합 결과 생성 — 엔진별 칼럼명 차이(DB2 소문자화 등)를 양식 필드 기준으로 통일
    _canonical_fields = list((state.get("column_mapping") or {}).keys())
    merged_results = _merge_results(run.db_results, canonical_fields=_canonical_fields)

    return {
        "db_results": run.db_results,
        "db_schemas": run.db_schemas,
        "db_errors": run.db_errors,
        "query_results": merged_results,
        "query_attempts": run.all_attempts,
        "sql_candidates": run.mc_candidates or None,
        "smq_derivation": run.mc_derivations or None,
        "current_node": "multi_db_executor",
        "error_message": None if run.db_results else "모든 DB 쿼리가 실패했습니다.",
    }


async def _analyze_schema(
    client: Any,
    parsed_requirements: dict,
    db_id: str = "_default",
    app_config: Optional[AppConfig] = None,
) -> dict:
    """DB 스키마를 분석하여 관련 테이블 정보를 수집한다.

    SchemaCacheManager.get_schema_or_fetch()를 사용하여
    3단계 캐시(메모리/Redis/파일)를 거친 후 DB 폴백을 수행한다.

    Args:
        client: DB 클라이언트
        parsed_requirements: 파싱된 요구사항
        db_id: DB 식별자 (캐시 키)
        app_config: 앱 설정

    Returns:
        스키마 정보 딕셔너리
    """
    if app_config is None:
        app_config = load_config()

    from src.schema_cache.cache_manager import get_cache_manager

    cache_mgr = get_cache_manager(app_config)

    # 통합 메서드로 3단계 캐시 + DB 폴백 수행
    schema_dict, cache_hit, _cache_source, _descriptions, _synonyms = (
        await cache_mgr.get_schema_or_fetch(client, db_id)
    )

    # 샘플 데이터 수집 (캐시에서 로드한 경우 샘플이 없을 수 있으므로 보충)
    for table_name in list(schema_dict.get("tables", {}).keys()):
        table_data = schema_dict["tables"][table_name]
        if not table_data.get("sample_data"):
            try:
                samples = await client.get_sample_data(table_name, limit=3)
                table_data["sample_data"] = samples
            except Exception:
                pass

    # 구조 메타(query_guide/query_examples/patterns) 부착 — 단일 DB(schema_analyzer 노드)와 동등화(D-066).
    # get_schema_or_fetch는 테이블 스키마만 반환하고 _structure_meta는 별도 캐시 키로 관리돼 여기에
    # 부착되지 않는다. 이게 없으면 query_guide·query_examples·EAV 힌트가 프롬프트에 전혀 들어가지
    # 않아 멀티 DB 폼필이 metric 조인 환각(존재하지 않는 definition_name·잘못된 조인)을 낸다.
    if not schema_dict.get("_structure_meta"):
        structure_meta: Optional[dict] = None
        try:
            from src.nodes.schema_analyzer import _load_manual_profile

            manual = _load_manual_profile(db_id)
        except Exception:
            manual = None
        if manual is not None:
            structure_meta = {k: v for k, v in manual.items() if k != "source"}
        else:
            try:
                structure_meta = await cache_mgr.get_structure_meta(db_id)
            except Exception:
                structure_meta = None
        if structure_meta:
            schema_dict["_structure_meta"] = structure_meta
            logger.info(
                "multi_db _analyze_schema: _structure_meta 부착 (db_id=%s, query_examples=%d)",
                db_id, len(structure_meta.get("query_examples", []) or []),
            )

    return schema_dict


async def _build_stepwise_deps(
    schema_info: dict,
    app_config: AppConfig,
    db_engine: str,
    db_id: str,
    default_limit: int,
) -> Optional["StepwiseDeps"]:
    """멀티 DB 경로(경로 C)의 단계적 도출 도구 재료를 만든다 (S2/D-128).

    플래그 OFF면 None(도구 조립 자체 없음). 단일 경로(`query_generator._build_stepwise_deps`)와
    같은 형태를 만들며, 경로 라벨만 다르다 — 발동 여부를 로그·레코드에서 구분하기 위함이다.
    단일 경로는 state에 실린 유사어를 쓰지만 멀티 DB 경로는 state에 없으므로 캐시에서 읽는다
    — 그래야 두 경로의 **도구 목록**이 같아진다(재료 비대칭 방지, D-066).

    Args:
        schema_info: 해당 DB 스키마 정보
        app_config: 앱 설정
        db_engine: DB 엔진 타입
        db_id: DB 식별자
        default_limit: 기본 행 제한

    Returns:
        ``column_deriver.StepwiseDeps`` 또는 None(플래그 OFF)
    """
    if not app_config.text2sql.stepwise_derivation:
        return None

    synonyms: dict[str, list[str]] = {}
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        synonyms = await get_cache_manager(app_config).get_synonyms(db_id) or {}
    except Exception as e:  # noqa: BLE001 — 사전 부재는 도구 1종 제외로 강등(사유 로그)
        logger.warning(
            "DB '%s': 단계적 도출 유사어 로드 실패(lookup_synonym 도구 제외): %s", db_id, e
        )

    return build_stepwise_deps(
        app_config,
        path="multi_db",
        synonyms=synonyms,
        schema_info=schema_info,
        db_engine=db_engine,
        default_limit=default_limit,
    )


async def _select_query_history_examples(
    db_id: str, user_query: str, app_config: AppConfig | None
) -> list[dict] | None:
    """멀티 DB 경로의 이력 few-shot 선택 — 단일 경로와 동일한 공용 헬퍼를 호출한다 (N2/D-133).

    Args:
        db_id: DB 식별자
        user_query: 검색에 쓸 자연어 질의(원문 우선, 없으면 sub_query_context)
        app_config: 앱 설정(없으면 미적용)

    Returns:
        few-shot 예시 목록 또는 None(고정 예시 유지)
    """
    return await select_history_fewshot(db_id, user_query, app_config)


async def _invoke_llm_for_sql(
    llm: BaseChatModel,
    system_prompt: str,
    user_prompt: str,
    *,
    parsed_requirements: dict,
    schema_info: dict,
    sub_query_context: str,
    db_engine: str,
    db_id: str,
    error_context: str | None,
    column_mapping: dict[str, str] | None,
    app_config: AppConfig | None,
    execute: Callable[[str], Awaitable[dict]] | None,
    candidate_sink: list[dict] | None,
) -> str:
    """조립한 프롬프트로 LLM에서 SQL을 받는다(다중 후보 우선, 없으면 단일 호출).

    트랙 A(E2~E4): 멀티 DB 경로(C) 명시 이식 — NL 질의(폼필·재시도 아님)에만 다중 후보를
    돌린다. execute(읽기전용 실행 클로저)·검증 클로저를 주입해 경로 비대칭을 막는다
    (§2.1 / D-066).
    """
    if app_config is None:
        app_config = load_config()
    t2 = app_config.text2sql
    use_multi = (
        t2.multi_candidate and execute is not None
        and not error_context and not column_mapping
        and (not t2.complexity_gate
             or classify_complexity(
                 user_query=parsed_requirements.get("original_query", "") or sub_query_context,
                 parsed_requirements=parsed_requirements, schema_info=schema_info,
             ) == "complex")
    )
    if use_multi:
        from src.nodes.candidate_selector import run_candidate_pipeline

        async def _validate(sql: str):
            return _validate_sql(
                sql, schema_info, db_id=db_id, db_engine=db_engine,
                user_query=sub_query_context, app_config=app_config,
            )

        selection = await run_candidate_pipeline(
            llm, system_prompt, user_prompt,
            count=t2.candidate_count, strategies=t2.candidate_strategies,
            selection=t2.selection, is_kbgenai=is_kbgenai(llm),
            extract_sql=extract_sql_from_response, validate=_validate, execute=execute,
            user_query=parsed_requirements.get("original_query", "") or sub_query_context,
        )
        if selection.get("sql"):
            if candidate_sink is not None:
                for c in selection.get("sql_candidates") or []:
                    candidate_sink.append({**c, "db_id": db_id})
            logger.info(
                "DB '%s' 다중 후보 선택: method=%s conf=%.2f",
                db_id, selection.get("method"), selection.get("confidence", 0.0),
            )
            return selection["sql"]

    messages: list[BaseMessage] = [
        SystemMessage(content=system_prompt)
    ]
    if is_kbgenai(llm):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    response = await llm.ainvoke(messages)
    return extract_sql_from_response(response.content)


async def _try_semantic_compile(
    llm: BaseChatModel,
    parsed_requirements: dict,
    schema_info: dict,
    default_limit: int,
    error_context: str | None,
    column_mapping: dict[str, str] | None,
    db_engine: str,
    db_id: str,
    app_config: AppConfig,
    prior_block: str | None,
    prior_scope: tuple[str, list[str]] | None,
    derivation_sink: list[dict] | None,
    *,
    parity: bool,
) -> str | None:
    """트랙 C(D-076) — 커버리지 내 정형 NL 질의를 시맨틱 결정적 컴파일한다(경로 C 이식).

    폼필(column_mapping)·재시도(error_context)가 아닐 때만 진입한다. 커버리지 밖이면
    None → 호출부가 LLM 경로로 진행한다(회귀 0).
    """
    # 경로 대칭 (d): 종전에는 prior_block(선행 task 결과 스코프)이 있으면 결정적 컴파일을
    # 통째로 우회했다 — SMQ가 선행 스코프를 표현하지 못하던 시절의 잔재(D-086)다. 이제
    # 조립기가 HAVING으로 강제하므로 단일 경로처럼 server_scope로 결정적 전달한다(D-099 대칭).
    _skip_for_prior = bool(prior_block) and not parity
    if parity and prior_block:
        logger.info(
            "[경로대칭] (d) 선행 스코프 결정적 전달(db=%s, scope=%s)",
            db_id, (prior_scope[0] if prior_scope else "없음"),
        )
    if not (not error_context and not column_mapping and not _skip_for_prior
            and app_config.text2sql.semantic_compose):
        return None

    _uq = parsed_requirements.get("original_query", "") or ""
    semantic_sql, _smq, _cov = await compile_from_nl(
        llm, _uq, db_id,
        default_limit=default_limit,
        # 표면어 미매칭 시 LLM 기간 산출물로 2단 폴백 — 단일 경로와 동일 규칙(R3-(i)/D-066)
        stat_month=resolve_stat_month_range(
            _uq, parsed_time_range=parsed_requirements.get("time_range")
        ),
        server_scope=prior_scope if parity else None,
        app_config=app_config,
        stepwise_deps=await _build_stepwise_deps(
            schema_info, app_config, db_engine, db_id, default_limit,
        ),
        derivation_sink=derivation_sink,
    )
    if semantic_sql:
        logger.info(
            "DB '%s': 시맨틱 결정적 컴파일 SQL(LLM 우회, 경로 C): %s",
            db_id, semantic_sql[:200],
        )
        return semantic_sql
    return None


async def _build_multi_structure_guide(
    schema_info: dict,
    parsed_requirements: dict,
    sub_query_context: str,
    db_id: str,
    app_config: AppConfig | None,
) -> str:
    """구조 분석 메타 기반 쿼리 가이드를 만든다(EAV 조인 규칙 + 값 조인 + 금지 조인 + few-shot).

    단일 경로(``query_generator._format_structure_guide``)와 같은 공용 빌더를 쓰되, 조립
    순서·조건은 현행 그대로 둔다(Plan 69 P3-1 동작 불변).
    """
    structure_meta = schema_info.get("_structure_meta")
    structure_guide = ""
    if structure_meta:
        structure_guide = structure_meta.get("query_guide", "")
        eav_patterns = eav_patterns_of(structure_meta)
        # EAV 패턴이 있으면 조인 규칙을 앞에 삽입 — guide가 빈 프로필에서도 금지 규칙은
        # 유효하다(P0-③의 멀티 대칭, Plan 69 W-8. 폴스타 guide는 비어 있지 않아 바이트 불변).
        if eav_patterns:
            structure_guide = EAV_JOIN_RULE_BLOCK + structure_guide
        for eav_p in eav_patterns:
            # EAV 패턴의 value_joins 정보 + 금지 JOIN 컬럼 경고를 구조 가이드에 추가.
            # 금지 JOIN 경고는 단일 경로와 같은 section 문구를 쓴다(W-1 채택) — 소제목+불릿이
            # 독립 규칙 블록으로 읽혀, 구조 가이드 본문에 섞이던 종전 inline 한 줄보다 지시
            # 준수에 유리하다. 금지 컬럼이 여러 건일수록 차이가 커진다.
            structure_guide += build_value_joins_block(eav_p)
            structure_guide += build_forbidden_join_block([eav_p])

    # 프로필 few-shot 쿼리 예시 주입 — 단일 DB 경로(query_generator)와 동등화(RC1/D-066).
    # 예시 부재로 멀티 DB 폼필이 조인 환각(존재하지 않는 컬럼)을 내던 문제 차단.
    # N2(D-133): 이력 검색이 유사 예시를 골라오면 고정 예시 대신 그것을 쓴다 — 단일 경로와
    # 같은 공용 헬퍼·같은 블록 포맷을 통과한다. 플래그 OFF·무적중이면 고정 경로 그대로(회귀 0).
    _history_examples = await _select_query_history_examples(
        db_id,
        parsed_requirements.get("original_query", "") or sub_query_context,
        app_config,
    )
    return structure_guide + build_query_examples(structure_meta, _history_examples)


def _build_multi_engine_hint(db_engine: str, db_id: str) -> str:
    """대상 엔진 문법 힌트 + 스키마 한정 규칙(D-057) + DB2 방언 주의를 만든다."""
    hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

    # D-057: 스키마 한정 규칙을 결정적으로 주입한다.
    # LLM이 임의로 스키마를 붙이거나, DB2에서 무스키마로 두어 연결 계정
    # CURRENT SCHEMA(예: SDQ000)로 잘못 해소되는 것을 방지한다.
    from src.routing.db_schema import get_schema_prefix

    hint += build_schema_prefix_rule(
        get_schema_prefix(db_id) if db_id else "",
        example_table=_SCHEMA_EXAMPLE_TABLE,
        foreign_prefix_example=_FOREIGN_SCHEMA_PREFIX,
    )
    if db_engine == "db2":
        hint += (
            "\n[DB2 방언] 행 수 제한은 `LIMIT` 대신 `FETCH FIRST n ROWS ONLY`를 사용하세요."
        )
    return hint


async def _build_multi_system_prompt(
    schema_info: dict,
    parsed_requirements: dict,
    sub_query_context: str,
    default_limit: int,
    db_engine: str,
    db_id: str,
    app_config: AppConfig | None,
) -> str:
    """멀티 DB 경로의 시스템 프롬프트를 조립한다(스키마 + 구조 가이드 + 엔진 힌트)."""
    # 경로 대칭 (a): 담당 어댑터(폴스타)가 있으면 단일 경로(`_build_system_prompt`)와 같은
    # 진입점으로 전용 시스템 템플릿을 쓴다 — 종전 멀티는 공통 템플릿 고정이라 폴스타 DB를
    # 멀티로 조회하면 전용 지식이 통째로 빠졌다(D-066 원형 결함과 동형). 렌더 2모드(마커
    # 원문/정본)는 어댑터가 내부 처리하므로 여기서 재구현하지 않는다(Plan 69 P3-2).
    template = QUERY_GENERATOR_SYSTEM_TEMPLATE
    if path_parity_enabled(app_config):
        from src.db_adapters import get_adapter

        _adapter = get_adapter(db_id, app_config.get_polestar_db_ids() or None)
        _adapter_template = (
            _adapter.system_template(routing_intent=None) if _adapter is not None else None
        )
        if _adapter_template is not None:
            template = _adapter_template
            logger.info("[경로대칭] (a) 어댑터 템플릿 적용(db=%s)", db_id)

    return template.format(
        schema=_format_schema(schema_info),
        default_limit=default_limit,
        structure_guide=await _build_multi_structure_guide(
            schema_info, parsed_requirements, sub_query_context, db_id, app_config,
        ),
        db_engine_hint=_build_multi_engine_hint(db_engine, db_id),
    )


def _metric_field_predicate(
    db_id: str, app_config: AppConfig | None
) -> Callable[[str], bool]:
    """지표 필드 판정을 어댑터 레지스트리에 바인딩한 술어로 만든다.

    담당 어댑터의 분류 훅이 없으면 스키마 무관 표면어 판정으로 강등된다(D-088/D-089).
    """
    adapter_db_ids = (app_config.get_polestar_db_ids() if app_config else None) or None

    def _is_metric(field: str) -> bool:
        return bool(classify_metric_field(
            field, db_id=db_id, adapter_db_ids=adapter_db_ids,
        ))

    return _is_metric


def _multi_resource_pivot_result(
    regular_entries: list[tuple[str, str]],
    server_eav: list[tuple[str, str]],
    child_eav: list[tuple[str, str, str]],
    metric_entries: list[tuple[str, str]],
    unmapped_fields: list[str] | None,
    *,
    schema_info: dict,
    db_engine: str,
    db_id: str,
    default_limit: int,
    parsed_requirements: dict,
    error_context: str | None,
    app_config: AppConfig | None,
) -> tuple[str | None, str | None, list[str] | None]:
    """자식 리소스 EAV가 섞인 폼필을 결정적 SQL 또는 프롬프트 지침 블록으로 처리한다(D-068).

    기본은 **코드가 직접 조립**(LLM 우회)이고, 재시도(결정적 SQL이 이미 실패)에서 경로 대칭이
    켜져 있으면 단일 경로처럼 피벗 지침 블록을 주어 LLM이 에러를 반영하게 한다(P3-2 (c)).

    Returns:
        (프롬프트 블록 또는 None, 결정적 SQL 또는 None, 갱신된 unmapped_fields)
    """
    _is_metric = _metric_field_predicate(db_id, app_config)
    # 사용률 통계 필드는 통합 피벗에 접어 넣는다(미매핑 경로 + metric 컬럼 매핑 경로).
    _um = list(unmapped_fields or [])
    pivot_metric_fields = [f for f in _um if _is_metric(f)]
    pivot_metric_fields += [
        field for field, _ in metric_entries if _is_metric(field)
    ]
    eav_pattern_mr = _get_eav_pattern(schema_info) or {}
    if error_context and path_parity_enabled(app_config):
        # 경로 대칭 (c): 재시도(결정적 SQL이 이미 실패)면 단일 경로처럼 LLM 폴백에
        # 피벗 지침 블록을 주고 에러를 반영하게 한다 — 종전 멀티는 재시도에도 같은
        # 결정적 SQL을 되돌려줘 에러 컨텍스트가 무시됐다(Plan 69 P3-2).
        # 사용률 필드는 피벗 블록에 접히므로 미매핑 목록에서 뺀다(블록 충돌 방지).
        unmapped_fields = [f for f in _um if not _is_metric(f)]
        logger.info(
            "[경로대칭] (c) 폼필 피벗 프롬프트 블록 주입(db=%s, 재시도 — 결정적 조립 우회)",
            db_id,
        )
        return (
            build_multi_resource_pivot_block(
                regular_entries, server_eav, child_eav, eav_pattern_mr,
                metric_fields=pivot_metric_fields, db_engine=db_engine,
            ),
            None,
            unmapped_fields,
        )

    # 프롬프트로 "제안"하면 LLM이 프로필 few-shot(월별 GROUP BY 등)과 경쟁해 무시·변형
    # (서버 중복·config 누락)한다. 이 well-defined 폼필 쿼리는 코드가 직접 조립해 LLM을
    # 우회한다(D-068 2차 정정) — LLM 변동성 원천 제거.
    domain_cfg = get_domain_by_id(db_id)
    db_schema = domain_cfg.db_schema if domain_cfg else ""
    # 폼필 피벗도 기간 2단 폴백에 **포함**한다(R3-(i), 2026-07-30 결정 변경) — 표면어
    # 미매칭 시 stat_date 필터가 빠져 전 기간 평균으로 침묵 왜곡되는 것을 막는다.
    # 단일 경로 `query_generator._try_build_form_fill_pivot_sql`와 동형(D-066).
    stat_month = resolve_stat_month_range(
        parsed_requirements.get("original_query", ""),
        parsed_time_range=parsed_requirements.get("time_range"),
    )
    deterministic_sql = build_form_fill_pivot_sql(
        regular_entries, server_eav, child_eav, eav_pattern_mr,
        metric_fields=pivot_metric_fields, db_engine=db_engine,
        db_schema=db_schema, limit=default_limit, stat_month=stat_month,
    )
    logger.info(
        "DB '%s': 폼필 다중 리소스 피벗 SQL 결정적 조립(LLM 우회) — child=%d, metric=%d, month=%s",
        db_id, len(child_eav), len(pivot_metric_fields),
        "~".join(stat_month) if stat_month else "전체",
    )
    return None, deterministic_sql, unmapped_fields


def _form_field_mapping_section(regular_entries: list[tuple[str, str]]) -> str:
    """양식 필드 매핑을 SELECT에 포함시키는 지시 섹션(한글 alias 강제 — 폼필 헤더 매칭)."""
    mapping_lines = "\n".join(
        f'- "{field}" ← {col}' for field, col in regular_entries
    )
    return (
        f"## 양식 필드 매핑 (반드시 SELECT에 포함)\n{mapping_lines}\n\n"
        "각 양식 필드를 지정된 DB 컬럼에서 조회하되, **결과 alias는 반드시 왼쪽 양식 필드명"
        "(한글, 따옴표 포함) 그대로** 사용하세요. 결과 컬럼명이 양식 헤더와 정확히 일치해야 "
        "채워지며, DB마다 다른 임의 영문 alias(server_name 등)는 절대 쓰지 마세요.\n"
        '예: SELECT r.name AS "서버 이름", r.hostname AS "호스트네임"'
    )


def _metric_fields_section(metric_entries: list[tuple[str, str]]) -> str:
    """성능 지표(사용률) 필드를 통계 피벗 예시로 유도하는 섹션.

    단일 field→table.column 매핑이 불가능한 값들이라 강제 SELECT 대신 안내로 넘긴다(RC2).
    """
    metric_fields = ", ".join(f'"{field}"' for field, _ in metric_entries)
    return (
        f"## 성능 지표 필드 (사용률) — 위 쿼리 예시 패턴을 따르세요\n"
        f"{metric_fields} 는 CPU/메모리 등 사용률 지표입니다. 이 값들은 단일 컬럼이 "
        "아니라 cmm_metric_stat 통계 테이블에서 resource_type + definition_name 피벗으로만 "
        "얻을 수 있으므로, 위 '쿼리 예시'의 성능 지표 SQL(cmm_metric_stat_m 3중 조인 + "
        "CASE WHEN resource_type='server.Cpus'/'server.Memory' AND definition_name='Utilization' "
        "피벗)을 그대로 따르세요.\n"
        "- 시간 표현이 '월/개월/월간' 또는 미지정이면 반드시 cmm_metric_stat_m(월별)을 사용하고, "
        "cmm_metric_stat_h(시간)·cmm_metric_stat_d(일)는 사용하지 마세요.\n"
        "- 각 지표 필드는 위 매핑이 지어낸 임의 컬럼명(cpu_avg_val 등)이 아니라 avg_val/max_val을 "
        "resource_type로 구분해 alias 하세요."
    )


def _split_mapping_for_prompt(
    column_mapping: dict[str, str], schema_info: dict,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
    """양식 매핑을 프롬프트 섹션별 묶음(정규/EAV/성능지표)으로 가른다.

    성능 지표 매핑을 왜 갈라내는지는 아래 분리 지점 주석 참조.

    Returns:
        (정규 매핑, EAV 매핑, 성능지표 매핑)
    """
    # 수정 A 적용: schema_info에 존재하지 않는 테이블의 매핑을 필터링.
    # 로그에 대조 테이블 목록을 부기해 0건 원인 추적을 돕고(W-7 — 경로 구분 접두는 유지),
    # `db_id:` 접두 표기도 단일 경로처럼 떼고 판정한다(S-1 — 외부 힌트 유입 시 침묵 폐기 방지).
    column_mapping = filter_mapping_by_schema(
        column_mapping, schema_info,
        log_label="multi_db column_mapping 필터링",
        log_schema_tables=True,
        strip_db_prefix=True,
    )

    # 서버명/서버이름류가 EAV Hostname으로 오매핑되면 등록명 컬럼으로 결정적 교정
    # (프로필 확정 규칙, 단일 경로 _try_build_form_fill_pivot_sql와 동등). db_mapping을
    # 직접 변형하지 않도록 사본에 적용.
    column_mapping = dict(column_mapping)
    _sn_eav = _get_eav_pattern(schema_info)
    if _sn_eav:
        correct_servername_hostname_mapping(column_mapping, _sn_eav.get("entity_table", ""))

    # 정규 매핑과 EAV 매핑 분리
    regular_entries, eav_entries = split_mapping_entries(column_mapping)

    # 성능 지표(사용률) 매핑은 강제 SELECT에서 제외한다(D-066 후속/RC2).
    # CPU/메모리 사용률(평균/최고)은 cmm_metric_stat_[h,d,m]에서 resource_type +
    # definition_name 피벗으로만 얻을 수 있어 단일 field→table.column 매핑이 불가능하다.
    # field_mapper가 지어낸 컬럼(예: cmm_metric_stat_h.cpu_avg_val)을 "반드시 포함"으로
    # 강제하면 LLM이 잘못된 테이블(_h)·조인을 만든다. 이 항목은 강제 대신 쿼리 예시의
    # cmm_metric_stat_m 피벗 패턴을 따르도록 안내로 넘긴다.
    metric_entries = [
        (field, col) for field, col in regular_entries
        if "cmm_metric_stat" in col.lower()
    ]
    regular_entries = [
        (field, col) for field, col in regular_entries
        if "cmm_metric_stat" not in col.lower()
    ]
    # EAV config 테이블과 entity 테이블이 다를 수 있으므로 정규 컬럼 필터링은 하지 않고
    # LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 남긴다(Plan 37: 수정 3-2).
    return regular_entries, eav_entries, metric_entries


def _build_mapping_user_parts(
    column_mapping: dict[str, str],
    schema_info: dict,
    unmapped_fields: list[str] | None,
    *,
    db_engine: str,
    db_id: str,
    default_limit: int,
    parsed_requirements: dict,
    error_context: str | None,
    app_config: AppConfig | None,
) -> tuple[list[str], str | None, list[str] | None]:
    """양식 필드 매핑(column_mapping)에서 파생되는 사용자 프롬프트 섹션들을 만든다.

    자식 리소스 EAV가 섞이면 폼필 피벗을 **코드가 결정적으로 조립**해 LLM을 우회하므로,
    이 함수는 프롬프트 섹션과 함께 그 SQL을 돌려준다(있으면 호출부가 즉시 반환한다).

    Returns:
        (user_parts에 이어 붙일 섹션들, 결정적 SQL 또는 None, 갱신된 unmapped_fields)
    """
    parts: list[str] = []
    regular_entries, eav_entries, metric_entries = _split_mapping_for_prompt(
        column_mapping, schema_info,
    )

    # 자식 리소스(server.Cpus/Memory 등) EAV 속성이 섞이면 서버 행 브릿지 조인으로는
    # NULL이 되므로, resource_type 구분 다중 리소스 피벗 블록으로 대체한다(D-068).
    # 단일 DB 경로(query_generator)와 동일 로직 — 공유 헬퍼 사용.
    attr_rt = eav_attr_resource_types(schema_info)
    child_eav, server_eav = split_eav_by_resource_type(
        eav_entries, attr_rt, entity_resource_type=_ENTITY_RESOURCE_TYPE
    )
    use_multi_resource_pivot = bool(child_eav)
    if use_multi_resource_pivot:
        pivot_part, deterministic_sql, unmapped_fields = _multi_resource_pivot_result(
            regular_entries, server_eav, child_eav, metric_entries, unmapped_fields,
            schema_info=schema_info, db_engine=db_engine, db_id=db_id,
            default_limit=default_limit, parsed_requirements=parsed_requirements,
            error_context=error_context, app_config=app_config,
        )
        if deterministic_sql:
            return parts, deterministic_sql, unmapped_fields
        if pivot_part:
            parts.append(pivot_part)

    if regular_entries and not use_multi_resource_pivot:
        parts.append(_form_field_mapping_section(regular_entries))

    if metric_entries:
        parts.append(_metric_fields_section(metric_entries))

    if eav_entries and not use_multi_resource_pivot:
        # _structure_meta에서 EAV 패턴 정보를 동적 추출. 멀티는 폼필 헤더 매칭 때문에
        # 결과 alias를 한글 양식 필드명으로 강제한다(§0.3-3 (e) 의도된 차이 — 통일 금지).
        parts.append(
            build_eav_pivot_block(
                eav_entries, _get_eav_pattern(schema_info),
                hangul_alias=True,
                host_attribute=_EAV_HOST_ATTRIBUTE,
                link_column=_EAV_LINK_COLUMN,
            )
        )

    return parts, None, unmapped_fields


async def _generate_sql(
    llm: BaseChatModel,
    parsed_requirements: dict,
    schema_info: dict,
    sub_query_context: str,
    default_limit: int,
    error_context: str | None = None,
    column_mapping: dict[str, str] | None = None,
    db_engine: str = "postgresql",
    db_id: str = "",
    unmapped_fields: list[str] | None = None,
    app_config: AppConfig | None = None,
    execute: Callable[[str], Awaitable[dict]] | None = None,
    candidate_sink: list[dict] | None = None,
    prior_block: str | None = None,
    derivation_sink: list[dict] | None = None,
    prior_scope: tuple[str, list[str]] | None = None,
    value_index: dict[str, list[str]] | None = None,
) -> str:
    """LLM을 사용하여 SQL을 생성한다.

    Args:
        llm: LLM 인스턴스
        parsed_requirements: 파싱된 요구사항
        schema_info: DB 스키마 정보
        sub_query_context: 해당 DB에서 조회할 내용 설명
        default_limit: 기본 LIMIT 값
        error_context: 이전 에러 메시지 (재시도 시)
        column_mapping: DB별 필드-컬럼 매핑 (field_mapper 결과, 선택)
        db_engine: DB 엔진 타입 ("postgresql", "db2" 등)
        db_id: DB 식별자 (스키마 한정 규칙 결정용, D-057)
        app_config: 앱 설정 (트랙 C 시맨틱 조합 플래그 판정용, 없으면 로드)
        derivation_sink: 트랙 S 단계적 도출 관측 레코드 적재 리스트 (선택, S2/D-128)
        prior_scope: 선행 task 결과 서버 스코프 (경로 대칭 ON일 때만 소비, P3-2 (d))
        value_index: 컬럼 값 인덱스 (경로 대칭 ON일 때만 소비, P3-2 (c))

    Returns:
        생성된 SQL 문자열
    """
    if app_config is None:
        app_config = load_config()
    _parity = path_parity_enabled(app_config)

    semantic_sql = await _try_semantic_compile(
        llm, parsed_requirements, schema_info, default_limit, error_context,
        column_mapping, db_engine, db_id, app_config, prior_block, prior_scope,
        derivation_sink, parity=_parity,
    )
    if semantic_sql:
        return semantic_sql

    system_prompt = await _build_multi_system_prompt(
        schema_info, parsed_requirements, sub_query_context,
        default_limit, db_engine, db_id, app_config,
    )

    user_prompt, deterministic_sql = _build_multi_user_prompt(
        parsed_requirements, schema_info, sub_query_context, default_limit,
        error_context, column_mapping, db_engine, db_id, unmapped_fields,
        app_config, prior_block, value_index, parity=_parity,
    )
    if deterministic_sql:
        return deterministic_sql

    return await _invoke_llm_for_sql(
        llm, system_prompt, user_prompt,
        parsed_requirements=parsed_requirements, schema_info=schema_info,
        sub_query_context=sub_query_context, db_engine=db_engine, db_id=db_id,
        error_context=error_context, column_mapping=column_mapping,
        app_config=app_config, execute=execute, candidate_sink=candidate_sink,
    )


def _unmapped_fields_section(unmapped_fields: list[str], db_engine: str) -> str:
    """자동 매핑에 실패한 양식 필드의 직접 조회 안내 섹션(한글 alias 강제, U-1 공유 빌더).

    사용률 지표는 field_mapper가 의도적으로 미매핑하므로, 결과 컬럼명이 양식 헤더와
    일치해야 excel_writer가 채운다(D-066 후속3/폼필 채우기).
    """
    return build_unmapped_fields_block(
        unmapped_fields,
        cast_example=decimal_cast_example(db_engine),
        metric_table=METRIC_PIVOT_TABLE,
        metric_pivot_keys=METRIC_PIVOT_KEYS,
        hangul_alias=True,
    )


def _period_prompt_block(
    parsed_requirements: dict,
    sub_query_context: str,
    db_id: str,
    app_config: AppConfig | None,
) -> str:
    """기간 표현의 결정적 해석을 프롬프트 블록으로 만든다(해당 없으면 빈 문자열).

    단일 DB 경로(query_generator)와 동일 규칙(D-076 후속4, D-066 단일 출처). 원문 질의를
    우선하고, 라우터가 만든 sub_query_context에만 표현이 남은 경우로 폴백한다.
    폴스타 월 통계 테이블 규약 특화 블록이라 폴스타 DB에만 주입한다(L2 일반화, 단일 경로와
    대칭 P1-3/D-088). 프로필 부재 DB는 미주입 — 일반 기간 규칙만 남는다(프로필 선언 전환은 P3/D-090).
    """
    _stat_block_db = db_id in ((app_config.get_polestar_db_ids() if app_config else None) or set())
    # 폴백 순서: 원문 표면어 → sub_query_context 표면어 → LLM 기간 산출물(R3-(i)).
    # 폴백 인자는 마지막 호출에만 준다 — 앞 단계가 매칭되면 or 단축으로 폴백이 발동하지 않는다.
    _stat_month = (
        resolve_stat_month_range(parsed_requirements.get("original_query", "") or "")
        or resolve_stat_month_range(
            sub_query_context, parsed_time_range=parsed_requirements.get("time_range")
        )
    )
    if _stat_block_db:
        return build_stat_month_block(_stat_month)
    # 무선언 DB: GENERIC_LLM_MAPPING 옵트인 시 범용 기간 힌트(폴스타 리터럴 없음, 단일 경로와 대칭 P3/D-090).
    if app_config and app_config.text2sql.generic_llm_mapping:
        return build_generic_period_hint(_stat_month)
    return ""


def _build_multi_user_prompt(
    parsed_requirements: dict,
    schema_info: dict,
    sub_query_context: str,
    default_limit: int,
    error_context: str | None,
    column_mapping: dict[str, str] | None,
    db_engine: str,
    db_id: str,
    unmapped_fields: list[str] | None,
    app_config: AppConfig | None,
    prior_block: str | None,
    value_index: dict[str, list[str]] | None,
    *,
    parity: bool,
) -> tuple[str, str | None]:
    """멀티 DB 경로의 사용자 프롬프트를 조립한다(블록 순서가 곧 프롬프트 바이트다).

    양식 필드 매핑 구간에서 폼필 피벗이 **결정적으로 조립**되면 프롬프트 대신 그 SQL이
    나온다 — 그때는 두 번째 반환값으로 돌려 호출부가 LLM 호출 없이 반환하게 한다.

    Returns:
        (user_prompt, 결정적 SQL 또는 None)
    """
    user_parts = [
        f"## 사용자 질의\n{sub_query_context}",
        f"## 파싱된 요구사항\n```json\n{json.dumps(parsed_requirements, ensure_ascii=False, indent=2)}\n```",
    ]

    _period_block = _period_prompt_block(
        parsed_requirements, sub_query_context, db_id, app_config,
    )
    if _period_block:
        user_parts.append(_period_block)

    # 경로 대칭 (c): E5-2 값 검색 리터럴 주입 — 종전 단일 전용이라 멀티는 WHERE 리터럴을
    # 검증 없이 환각했다(D-128이 "멀티 value_index 미전달"로 별건 기록한 건의 수용).
    if parity:
        _vi_block = build_value_index_injection(
            value_index,
            parsed_requirements.get("original_query", "") or sub_query_context,
            app_config,
        )
        if _vi_block:
            user_parts.append(_vi_block)
            logger.info("[경로대칭] (c) 값 인덱스 리터럴 블록 주입(db=%s)", db_id)

    # 선행 task 결과 서버 스코프 강제 — 단일 DB 경로(query_generator)와 동일 블록(D-086/D-066)
    if prior_block:
        user_parts.append(prior_block)

    # 양식 필드 매핑에서 파생되는 섹션들 — 폼필 피벗이 결정적으로 조립되면 여기서 SQL이 나온다.
    if column_mapping:
        _parts, _deterministic, unmapped_fields = _build_mapping_user_parts(
            column_mapping, schema_info, unmapped_fields,
            db_engine=db_engine, db_id=db_id, default_limit=default_limit,
            parsed_requirements=parsed_requirements, error_context=error_context,
            app_config=app_config,
        )
        user_parts.extend(_parts)
        if _deterministic:
            return "", _deterministic

    # 미매핑 필드(column_mapping=None) — 반드시 한글 필드명 그대로 alias (D-066 후속3/폼필 채우기).
    # 사용률 지표는 field_mapper가 의도적으로 미매핑하므로, 결과 컬럼명이 양식 헤더와 일치해야
    # excel_writer가 채운다. 단일 DB(query_generator)에 있는 "자동 매핑 실패 필드" 블록을 이식.
    if unmapped_fields:
        user_parts.append(_unmapped_fields_section(unmapped_fields, db_engine))

    if error_context:
        user_parts.append(
            f"## 이전 에러\n{error_context}\n위 에러를 수정한 새로운 SQL을 생성하세요."
        )

    return "\n\n".join(user_parts), None
def _validate_sql(
    sql: str,
    schema_info: dict,
    *,
    db_id: str = "",
    db_engine: str = "postgresql",
    user_query: str = "",
    app_config: Optional[AppConfig] = None,
) -> Optional[str]:
    """멀티 DB 경로 검증 심 (Plan 69 P4-3).

    기본은 종전 간이 검증(동작 불변). ``TEXT2SQL_MULTI_FULL_VALIDATION`` ON이면 단일
    경로와 같은 full validator(테이블·컬럼 존재, EAV 금지 조인, 어댑터 훅)를 소비한다 —
    같은 폴스타 DB가 단일 조회에선 차단되고 멀티 조회에선 통과하던 방어 비대칭의 해소.
    거부 사유는 로그로 계측한다(위양성 실측 → 기본 전환 별도 판단, §0.3-4).
    """
    if not getattr(
        getattr(app_config, "text2sql", None), "multi_full_validation", False
    ):
        return _validate_sql_simple(sql, schema_info)
    from src.db_adapters import get_adapter
    from src.nodes.query_validator import validate_sql

    adapter = get_adapter(db_id, app_config.get_polestar_db_ids() or None)
    adapter_checks = adapter.validator_checks() if adapter is not None else []
    outcome = validate_sql(
        sql, schema_info,
        db_engine=db_engine, user_query=user_query,
        default_limit=app_config.query.default_limit,
        adapter_checks=adapter_checks,
    )
    if outcome.errors:
        reason = "; ".join(outcome.errors[:5])
        logger.info("[멀티검증강화] 거부(db=%s): %s", db_id, reason)
        return reason
    return None


def _validate_sql_simple(sql: str, schema_info: dict) -> Optional[str]:
    """SQL을 간이 검증한다.

    Args:
        sql: SQL 문자열
        schema_info: 스키마 정보

    Returns:
        에러 메시지 (정상이면 None)
    """
    if not sql or not sql.strip():
        return "빈 SQL"

    # SELECT 문 확인 — CTE(WITH ... SELECT)도 읽기 전용이므로 허용(2026-07-21 gp-014,
    # 단일 경로 _get_statement_type과 동일 규칙). DML은 아래 위험 키워드 검사가 차단.
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith(("SELECT", "WITH")) and not sql_upper.startswith("--"):
        # 주석으로 시작할 수 있으므로 주석 제거 후 확인
        cleaned = re.sub(r"--[^\n]*\n", "", sql).strip().upper()
        if not cleaned.startswith(("SELECT", "WITH")):
            return "SELECT 문이 아닙니다."

    # 위험 키워드 확인
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
    for kw in dangerous:
        if re.search(rf"\b{kw}\b", sql, re.IGNORECASE):
            return f"금지 키워드 포함: {kw}"

    # LEFT JOIN 강등(WHERE 필터) 감지 — 단일 경로(query_validator 6.7)와 대칭 (D-085)
    demotion_errors = _check_left_join_where_demotion(sql)
    if demotion_errors:
        return demotion_errors[0]

    # 따옴표 밖 자연어(한글) 토큰 잔존 검출 — 단일 경로(query_validator)와 동일 가드를
    # 멀티 경로에도 공유(D-066 경로 비대칭 방지, D-104). 검출 시 재시도 루프가 재생성 유도.
    from src.nodes.query_validator import find_bare_hangul_tokens as _find_bare_hangul_tokens

    bare_hangul = _find_bare_hangul_tokens(sql)
    if bare_hangul:
        shown = ", ".join(sorted(set(bare_hangul))[:5])
        return (
            f"SQL 구조에 자연어(한글) 토큰이 남아 있습니다: {shown} - "
            "따옴표 안 별칭/문자열 리터럴 외의 한글은 모두 제거하고 완전한 SQL로 다시 작성하세요."
        )

    # cmm_resource 조회 시 dtime IS NULL 부재 검출 — 단일 경로(query_validator 4.6)와 공유
    # (D-066 경로 비대칭 방지). 폐쇄망 실측 2026-07-21 b0-005: 필터 누락 시 삭제 서버 혼입.
    from src.utils.query_gen_common import MISSING_DTIME_ERROR, missing_dtime_filter

    if missing_dtime_filter(sql):
        return MISSING_DTIME_ERROR

    return None


def _format_schema(schema_info: dict) -> str:
    """스키마 정보를 프롬프트용 텍스트로 변환한다.

    Args:
        schema_info: 스키마 딕셔너리

    Returns:
        스키마 텍스트
    """
    # 단일 경로(_format_schema_for_prompt)와 같은 빌더를 쓴다. NOT NULL 표기(W-3)와 건수
    # 포함 한국어 샘플 표기(W-4)는 단일 문구로 통일했다 — NOT NULL은 LLM이 조인 방향·널
    # 처리를 판단하는 재료이고, 샘플 건수는 대표성 판단 재료라 멀티만 빠질 근거가 없다.
    # 설명·유사어·참조 섹션은 멀티가 재료(state)를 갖고 있지 않아 여전히 미수록이다(W-6 별건).
    # FK 헤더는 현행 영문 유지(W-5 반려 — relationships_header 기본값).
    return format_schema_text(
        schema_info,
        include_not_null=True,
        sample_style="labeled",
    )


def _merge_results(
    db_results: dict[str, list[dict]],
    canonical_fields: list[str] | None = None,
) -> list[dict]:
    """여러 DB의 결과를 하나의 리스트로 병합한다(각 행에 _source_db 태그).

    엔진별 결과 칼럼명 차이(특히 **DB2가 결과 칼럼의 라틴 문자를 소문자로 반환** → gp="IP주소"
    vs b0="ip주소")로 원본 병합·CSV에서 칼럼이 중복 분리되는 것을 방지한다. 칼럼명을 정규화
    (소문자·공백/언더스코어 제거) 기준으로 **canonical 이름(양식 필드 우선, 없으면 첫 등장 키)**으로
    통일한다. Excel writer는 자체 정규화 매칭으로 흡수하지만, 원본 병합(query_results)·그 CSV
    다운로드는 키를 그대로 써서 분리됐다(D-068 후속).

    Args:
        db_results: DB별 쿼리 결과 {db_id: rows}
        canonical_fields: 양식 필드명 등 canonical 칼럼명 후보(정규화 매칭용, 선택)

    Returns:
        병합된 결과 행 리스트(칼럼명 통일)
    """
    def _norm(s: object) -> str:
        return str(s).lower().replace(" ", "").replace("_", "")

    # canonical 후보: 양식 필드명 우선(같은 정규형이면 양식 표기를 대표로)
    canon_by_norm: dict[str, str] = {}
    for f in canonical_fields or []:
        canon_by_norm.setdefault(_norm(f), f)

    merged: list[dict] = []
    for db_id, rows in db_results.items():
        for row in rows:
            tagged_row: dict = {}
            for key, value in row.items():
                nk = _norm(key)
                canon = canon_by_norm.get(nk)
                if canon is None:
                    # 양식 필드에 없으면 첫 등장 키를 대표로 고정 → 이후 동일 정규형은 이 표기로 통일
                    canon = key
                    canon_by_norm[nk] = key
                tagged_row[canon] = value
            tagged_row["_source_db"] = db_id
            merged.append(tagged_row)
    return merged
