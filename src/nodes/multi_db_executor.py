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
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

import sqlparse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import create_llm
from src.nodes.candidate_generator import classify_complexity
from src.nodes.query_validator import _check_left_join_where_demotion
from src.nodes.semantic_compiler import compile_from_nl
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.routing.db_registry import DBRegistry
from src.routing.domain_config import get_domain_by_id
from src.security.audit_logger import log_query_execution
from src.state import AgentState, QueryAttempt
from src.utils.query_gen_common import (
    build_generic_period_hint,
    build_prior_rows_block,
    build_query_examples_block,
    build_stat_month_block,
    correct_servername_hostname_mapping,
    extract_sql_from_response,
    resolve_query_limit,
    resolve_stat_month_range,
)
# 폴스타 EAV/피벗 결정적 조립기는 어댑터로 이동(Plan 63 P2, D-089) — application 직접 임포트.
from src.db_adapters.polestar.assembler import (
    build_multi_resource_pivot_sql,
    classify_metric_field,
    decimal_cast_example,
    eav_attr_resource_types,
)
from src.utils.schema_utils import build_excluded_join_map

if TYPE_CHECKING:  # 타입 표기 전용 — 런타임 임포트는 플래그 ON 경로에서만 수행한다.
    from src.nodes.column_deriver import StepwiseDeps

logger = logging.getLogger(__name__)


def _get_eav_pattern(schema_info: Optional[dict]) -> Optional[dict]:
    """_structure_meta에서 첫 번째 EAV 패턴을 반환한다.

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴 딕셔너리 또는 None
    """
    if not schema_info:
        return None
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return None
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") == "eav":
            return pattern
    return None


def _extract_eav_tables(schema_info: Optional[dict]) -> set[str]:
    """_structure_meta에서 EAV 패턴의 관련 테이블명을 추출한다.

    Args:
        schema_info: 스키마 정보 딕셔너리 (선택)

    Returns:
        EAV 패턴과 관련된 테이블명 집합 (소문자)
    """
    if not schema_info:
        return set()
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return set()
    tables: set[str] = set()
    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") == "eav":
            for key in ("entity_table", "config_table", "table"):
                val = pattern.get(key)
                if val:
                    tables.add(val.lower())
    return tables


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
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    registry = DBRegistry(app_config)
    targets = state.get("target_databases", [])
    parsed_requirements = state.get("parsed_requirements", {})

    # "전체/모든" 조회는 LIMIT를 상향해 1000건 절단을 방지한다 — 단일 DB 경로와 동등화(RC4/D-066).
    effective_limit = resolve_query_limit(
        state.get("user_query", ""), app_config.query.default_limit
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

    # 선행 task 결과 서버 스코프 — 단일 경로(query_generator)와 대칭 배선(D-086/D-066)
    prior_block = build_prior_rows_block(state.get("prior_rows"))

    for target in targets:
        db_id = target["db_id"]
        sub_context = target.get("sub_query_context", state["user_query"])

        if not registry.is_registered(db_id):
            db_errors[db_id] = f"DB '{db_id}'이(가) 레지스트리에 등록되지 않았습니다."
            logger.warning("미등록 DB 스킵: %s", db_id)
            continue

        try:
            async with registry.get_client(db_id) as client:
                # 1. 스키마 분석
                schema_info = await _analyze_schema(
                    client, parsed_requirements,
                    db_id=db_id, app_config=app_config,
                )
                db_schemas[db_id] = schema_info

                if not schema_info.get("tables"):
                    db_errors[db_id] = f"DB '{db_id}'에서 테이블을 찾을 수 없습니다."
                    continue

                # 2. SQL 생성 (DB별 column_mapping 전달)
                db_mapping = state.get("db_column_mapping", {}).get(db_id, {}) if state.get("db_column_mapping") else {}
                # DB 엔진 정보 조회
                domain_cfg = get_domain_by_id(db_id)
                db_engine = domain_cfg.db_engine if domain_cfg else "postgresql"
                db_schema = domain_cfg.db_schema if domain_cfg else ""
                schema_key = (db_engine, db_schema)

                cached_sql = _sql_by_schema.get(schema_key)
                if cached_sql:
                    # 동일 스키마 DB: 검증된 SQL 재사용 → 컬럼 alias 일관성 보장(폼필 병합 정합)
                    sql = cached_sql
                    logger.info(
                        "DB '%s': 동일 스키마%s SQL 재사용 (alias 일관성)", db_id, schema_key
                    )
                else:
                    async def _mc_execute(_sql: str) -> dict:
                        try:
                            _r = await client.execute_sql(_sql)
                            return {"rows": _r.rows, "error": None}
                        except Exception as _e:  # noqa: BLE001
                            return {"rows": None, "error": str(_e)}

                    sql = await _generate_sql(
                        llm, parsed_requirements, schema_info,
                        sub_context, effective_limit,
                        column_mapping=db_mapping,
                        db_engine=db_engine,
                        db_id=db_id,
                        unmapped_fields=unmapped_fields,
                        app_config=app_config,
                        execute=_mc_execute,
                        candidate_sink=mc_candidates,
                        prior_block=prior_block,
                        derivation_sink=mc_derivations,
                    )

                    # 3. SQL 검증 (간이)
                    validation_error = _validate_sql_simple(sql, schema_info)
                    if validation_error:
                        # 1회 재시도
                        logger.warning(
                            "DB '%s' SQL 검증 실패, 재생성 시도: %s",
                            db_id, validation_error,
                        )
                        sql = await _generate_sql(
                            llm, parsed_requirements, schema_info,
                            sub_context, effective_limit,
                            error_context=validation_error,
                            column_mapping=db_mapping,
                            db_engine=db_engine,
                            db_id=db_id,
                            unmapped_fields=unmapped_fields,
                            app_config=app_config,
                            prior_block=prior_block,
                        )
                        validation_error = _validate_sql_simple(sql, schema_info)
                        if validation_error:
                            db_errors[db_id] = f"SQL 검증 실패: {validation_error}"
                            continue
                    # 검증 통과한 SQL을 스키마 키로 캐시 (다음 동일 스키마 DB가 재사용)
                    _sql_by_schema[schema_key] = sql

                # 4. SQL 실행
                start_time = time.time()
                result = await client.execute_sql(sql)
                elapsed_ms = (time.time() - start_time) * 1000

                db_results[db_id] = result.rows
                all_attempts.append(QueryAttempt(
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
                    retry_attempt=0,
                )

                logger.info(
                    "DB '%s' 쿼리 완료: %d건, %.0fms",
                    db_id, result.row_count, elapsed_ms,
                )

        except Exception as e:
            error_msg = f"DB '{db_id}' 실행 에러: {str(e)}"
            db_errors[db_id] = error_msg
            logger.error(error_msg)

            all_attempts.append(QueryAttempt(
                sql="",
                success=False,
                error=str(e),
                row_count=0,
                execution_time_ms=0,
            ))

    # 전체 병합 결과 생성 — 엔진별 칼럼명 차이(DB2 소문자화 등)를 양식 필드 기준으로 통일
    _canonical_fields = list((state.get("column_mapping") or {}).keys())
    merged_results = _merge_results(db_results, canonical_fields=_canonical_fields)

    return {
        "db_results": db_results,
        "db_schemas": db_schemas,
        "db_errors": db_errors,
        "query_results": merged_results,
        "query_attempts": all_attempts,
        "sql_candidates": mc_candidates or None,
        "smq_derivation": mc_derivations or None,
        "current_node": "multi_db_executor",
        "error_message": None if db_results else "모든 DB 쿼리가 실패했습니다.",
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
    from src.nodes.column_deriver import StepwiseDeps

    synonyms: dict[str, list[str]] = {}
    try:
        from src.schema_cache.cache_manager import get_cache_manager

        synonyms = await get_cache_manager(app_config).get_synonyms(db_id) or {}
    except Exception as e:  # noqa: BLE001 — 사전 부재는 도구 1종 제외로 강등(사유 로그)
        logger.warning(
            "DB '%s': 단계적 도출 유사어 로드 실패(lookup_synonym 도구 제외): %s", db_id, e
        )

    return StepwiseDeps(
        path="multi_db",
        synonyms=synonyms,
        schema_info=schema_info or {},
        db_engine=db_engine or "postgresql",
        adapter_db_ids=app_config.get_polestar_db_ids() or None,
        default_limit=default_limit,
        synonym_min_score=app_config.synonym.match_confidence_min,
        value_fuzzy=app_config.synonym.fuzzy_match,
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
    if app_config is None:
        return None
    from src.schema_cache.query_history import select_fewshot_examples

    t2 = app_config.text2sql
    return await select_fewshot_examples(
        db_id, user_query,
        enabled=t2.query_history_fewshot,
        top_k=t2.query_history_top_k,
        min_score=t2.query_history_min_score,
    )


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

    Returns:
        생성된 SQL 문자열
    """
    # 트랙 C(D-076): 경로 C(멀티 DB) 명시 이식 — 커버리지 내 정형 NL 질의는 시맨틱 결정적 컴파일.
    # 폼필(column_mapping)·재시도(error_context)가 아닐 때만 진입. 커버리지 밖이면 아래 LLM 경로(회귀 0).
    if app_config is None:
        app_config = load_config()
    # prior_block(선행 task 결과 스코프)이 있으면 결정적 컴파일 우회 — SMQ는 선행 결과
    # 서버 한정을 표현할 수 없다(D-086, 단일 경로와 동일 조건).
    if (not error_context and not column_mapping and not prior_block
            and app_config.text2sql.semantic_compose):
        _uq = parsed_requirements.get("original_query", "") or ""
        semantic_sql, _smq, _cov = await compile_from_nl(
            llm, _uq, db_id,
            default_limit=default_limit,
            stat_month=resolve_stat_month_range(_uq),
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

    schema_text = _format_schema(schema_info)

    # 구조 분석 메타 기반 쿼리 가이드 (있으면 삽입)
    structure_meta = schema_info.get("_structure_meta")
    structure_guide = ""
    if structure_meta:
        structure_guide = structure_meta.get("query_guide", "")
        # EAV 패턴의 value_joins 정보를 구조 가이드에 추가
        eav_patterns = [
            p for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav"
        ]
        # EAV 패턴이 있고 query_guide가 존재하면, 조인 규칙 지침을 앞에 삽입
        if eav_patterns and structure_guide:
            eav_join_rule = (
                "## EAV 테이블 조인 규칙\n"
                "EAV 구조의 entity 테이블과 config 테이블을 조인할 때 "
                "id 컬럼으로 직접 조인하지 마세요.\n"
                "두 테이블의 ID 체계가 다릅니다. "
                "반드시 아래 지침의 JOIN SQL 패턴을 그대로 사용하세요.\n\n"
            )
            structure_guide = eav_join_rule + structure_guide
        for eav_p in eav_patterns:
            value_joins = eav_p.get("value_joins", [])
            if value_joins:
                entity_table = eav_p.get("entity_table", "entity_table")
                config_table = eav_p.get("config_table", "config_table")
                attr_col = eav_p.get("attribute_column", "NAME")
                structure_guide += "\n\n[값 기반 조인 (value-based join)]"
                structure_guide += (
                    f"\n{config_table}과 {entity_table} 간 FK가 없습니다. "
                    "다음 값 대응 관계를 조인에 활용하세요:"
                )
                for vj in value_joins:
                    structure_guide += (
                        f"\n- {config_table}.{attr_col}='{vj['eav_attribute']}'인 행의 "
                        f"{vj['eav_value_column']} 값은 "
                        f"{entity_table}.{vj['entity_column']}과 동일한 값입니다."
                    )

            # 금지 JOIN 컬럼 경고 추가
            for excl in eav_p.get("excluded_join_columns", []):
                structure_guide += (
                    f"\n[금지] {excl.get('table', '?')}.{excl.get('column', '?')}는 "
                    f"JOIN ON 절에서 사용할 수 없습니다: {excl.get('reason', 'JOIN 불가')}"
                )

    # 프로필 few-shot 쿼리 예시 주입 — 단일 DB 경로(query_generator)와 동등화(RC1/D-066).
    # 예시 부재로 멀티 DB 폼필이 조인 환각(존재하지 않는 컬럼)을 내던 문제 차단.
    # N2(D-133): 이력 검색이 유사 예시를 골라오면 고정 예시 대신 그것을 쓴다 — 단일 경로와
    # 같은 공용 헬퍼·같은 블록 포맷을 통과한다. 플래그 OFF·무적중이면 고정 경로 그대로(회귀 0).
    _history_examples = await _select_query_history_examples(
        db_id,
        parsed_requirements.get("original_query", "") or sub_query_context,
        app_config,
    )
    if _history_examples:
        structure_guide += build_query_examples_block(
            {"query_examples": _history_examples}
        )
    else:
        structure_guide += build_query_examples_block(structure_meta)

    db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

    # D-057: 스키마 한정 규칙을 결정적으로 주입한다.
    # LLM이 임의로 스키마(예: PostgreSQL식 `polestar.`)를 붙이거나, DB2에서 무스키마로 두어
    # 연결 계정 CURRENT SCHEMA(예: SDQ000)로 잘못 해소되는 것을 방지한다.
    from src.routing.db_schema import get_schema_prefix

    schema_prefix = get_schema_prefix(db_id) if db_id else ""
    if schema_prefix:
        db_engine_hint += (
            f"\n[스키마 한정 규칙] 이 DB의 모든 테이블은 반드시 접두사 `{schema_prefix}`를 붙여 "
            f"`{schema_prefix}테이블명` 형식으로 참조하세요 (예: {schema_prefix}cmm_resource). "
            f"다른 스키마명을 임의로 붙이지 마세요."
        )
    else:
        db_engine_hint += (
            "\n[스키마 한정 규칙] 이 DB의 테이블은 **스키마 접두사 없이(무스키마)** 참조하세요 "
            "(예: cmm_resource). `polestar.` 등 임의의 스키마 접두사를 붙이지 마세요."
        )
    if db_engine == "db2":
        db_engine_hint += (
            "\n[DB2 방언] 행 수 제한은 `LIMIT` 대신 `FETCH FIRST n ROWS ONLY`를 사용하세요."
        )

    system_prompt = QUERY_GENERATOR_SYSTEM_TEMPLATE.format(
        schema=schema_text,
        default_limit=default_limit,
        structure_guide=structure_guide,
        db_engine_hint=db_engine_hint,
    )

    user_parts = [
        f"## 사용자 질의\n{sub_query_context}",
        f"## 파싱된 요구사항\n```json\n{json.dumps(parsed_requirements, ensure_ascii=False, indent=2)}\n```",
    ]

    # 기간 표현의 결정적 해석 주입 — 단일 DB 경로(query_generator)와 동일 규칙(D-076 후속4,
    # D-066 단일 출처). 원문 질의 우선, 라우터가 만든 sub_query_context에만 표현이 남은 경우 폴백.
    # 폴스타 월 통계 테이블 규약 특화 블록이라 폴스타 DB에만 주입한다(L2 일반화, 단일 경로와 대칭
    # P1-3/D-088). 프로필 부재 DB는 미주입 — 일반 기간 규칙만 남는다. 프로필 선언 전환은 P3(D-090).
    _stat_block_db = db_id in ((app_config.get_polestar_db_ids() if app_config else None) or set())
    _stat_month = (
        resolve_stat_month_range(parsed_requirements.get("original_query", "") or "")
        or resolve_stat_month_range(sub_query_context)
    )
    _sm_block = build_stat_month_block(_stat_month) if _stat_block_db else ""
    if _sm_block:
        user_parts.append(_sm_block)
    # 무선언 DB: GENERIC_LLM_MAPPING 옵트인 시 범용 기간 힌트(폴스타 리터럴 없음, 단일 경로와 대칭 P3/D-090).
    elif app_config and app_config.text2sql.generic_llm_mapping:
        _gp_block = build_generic_period_hint(_stat_month)
        if _gp_block:
            user_parts.append(_gp_block)

    # 선행 task 결과 서버 스코프 강제 — 단일 DB 경로(query_generator)와 동일 블록(D-086/D-066)
    if prior_block:
        user_parts.append(prior_block)

    # column_mapping이 있으면 schema_info 기반 필터링 후 매핑 컬럼을 명시
    if column_mapping:
        # 수정 A 적용: schema_info에 존재하지 않는 테이블의 매핑을 필터링
        if schema_info:
            tables_in_schema = set(schema_info.get("tables", {}).keys())
            tables_lower = set()
            for t in tables_in_schema:
                tables_lower.add(t.lower())
                # "schema.table" → "table" 부분도 매칭 대상에 추가
                if "." in t:
                    tables_lower.add(t.rsplit(".", 1)[-1].lower())
            filtered_mapping: dict[str, str | None] = {}
            for field, col in column_mapping.items():
                if col and not col.startswith("EAV:"):
                    parts = col.split(".")
                    # "db_id.table.column" (3단계) → table = parts[-2], 값을 table.column으로 정규화
                    # "table.column" (2단계) → table = parts[0]
                    if len(parts) >= 3:
                        table_part = parts[-2]
                        col = f"{parts[-2]}.{parts[-1]}"
                    elif len(parts) == 2:
                        table_part = parts[0]
                    else:
                        table_part = ""
                    if table_part.lower() in tables_lower:
                        filtered_mapping[field] = col
                    else:
                        logger.warning(
                            "multi_db column_mapping 필터링: '%s' -> '%s' (테이블 '%s' 미존재)",
                            field, col, table_part,
                        )
                else:
                    filtered_mapping[field] = col
            column_mapping = filtered_mapping

        # 서버명/서버이름류가 EAV Hostname으로 오매핑되면 등록명 컬럼으로 결정적 교정
        # (프로필 확정 규칙, 단일 경로 _try_build_form_fill_pivot_sql와 동등). db_mapping을
        # 직접 변형하지 않도록 사본에 적용.
        column_mapping = dict(column_mapping)
        _sn_eav = _get_eav_pattern(schema_info)
        if _sn_eav:
            correct_servername_hostname_mapping(column_mapping, _sn_eav.get("entity_table", ""))

        # 정규 매핑과 EAV 매핑 분리
        regular_entries = [
            (field, col) for field, col in column_mapping.items()
            if col and not col.startswith("EAV:")
        ]
        eav_entries = [
            (field, col[4:])  # "EAV:" 접두사 제거
            for field, col in column_mapping.items()
            if col and col.startswith("EAV:")
        ]

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

        # EAV config 테이블과 entity 테이블이 다를 수 있으므로
        # 정규 컬럼 필터링을 제거하고 LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함.
        # (Plan 37: 수정 3-2)

        # 자식 리소스(server.Cpus/Memory 등) EAV 속성이 섞이면 서버 행 브릿지 조인으로는
        # NULL이 되므로, resource_type 구분 다중 리소스 피벗 블록으로 대체한다(D-068).
        # 단일 DB 경로(query_generator)와 동일 로직 — 공유 헬퍼 사용.
        attr_rt = eav_attr_resource_types(schema_info)
        child_eav = [
            (field, attr, attr_rt[attr.upper()])
            for field, attr in eav_entries
            if attr.upper() in attr_rt and attr_rt[attr.upper()] != "server.Server"
        ]
        use_multi_resource_pivot = bool(child_eav)
        if use_multi_resource_pivot:
            server_eav = [
                (field, attr)
                for field, attr in eav_entries
                if attr.upper() not in attr_rt or attr_rt[attr.upper()] == "server.Server"
            ]
            # 사용률 통계 필드는 통합 피벗에 접어 넣는다(미매핑 경로 + metric 컬럼 매핑 경로).
            _um = list(unmapped_fields or [])
            pivot_metric_fields = [f for f in _um if classify_metric_field(f)]
            pivot_metric_fields += [
                field for field, _ in metric_entries if classify_metric_field(field)
            ]
            eav_pattern_mr = _get_eav_pattern(schema_info) or {}
            # 프롬프트로 "제안"하면 LLM이 프로필 few-shot(월별 GROUP BY 등)과 경쟁해 무시·변형
            # (서버 중복·config 누락)한다. 이 well-defined 폼필 쿼리는 코드가 직접 조립해 LLM을
            # 우회한다(D-068 2차 정정) — LLM 변동성 원천 제거.
            domain_cfg = get_domain_by_id(db_id)
            db_schema = domain_cfg.db_schema if domain_cfg else ""
            stat_month = resolve_stat_month_range(parsed_requirements.get("original_query", ""))
            deterministic_sql = build_multi_resource_pivot_sql(
                regular_entries, server_eav, child_eav, eav_pattern_mr,
                metric_fields=pivot_metric_fields, db_engine=db_engine,
                db_schema=db_schema, limit=default_limit, stat_month=stat_month,
            )
            logger.info(
                "DB '%s': 폼필 다중 리소스 피벗 SQL 결정적 조립(LLM 우회) — child=%d, metric=%d, month=%s",
                db_id, len(child_eav), len(pivot_metric_fields),
                "~".join(stat_month) if stat_month else "전체",
            )
            return deterministic_sql

        if regular_entries and not use_multi_resource_pivot:
            mapping_lines = "\n".join(
                f'- "{field}" ← {col}' for field, col in regular_entries
            )
            user_parts.append(
                f"## 양식 필드 매핑 (반드시 SELECT에 포함)\n{mapping_lines}\n\n"
                "각 양식 필드를 지정된 DB 컬럼에서 조회하되, **결과 alias는 반드시 왼쪽 양식 필드명"
                "(한글, 따옴표 포함) 그대로** 사용하세요. 결과 컬럼명이 양식 헤더와 정확히 일치해야 "
                "채워지며, DB마다 다른 임의 영문 alias(server_name 등)는 절대 쓰지 마세요.\n"
                '예: SELECT r.name AS "서버 이름", r.hostname AS "호스트네임"'
            )

        if metric_entries:
            metric_fields = ", ".join(f'"{field}"' for field, _ in metric_entries)
            user_parts.append(
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

        if eav_entries and not use_multi_resource_pivot:
            # _structure_meta에서 EAV 패턴 정보를 동적 추출
            eav_pattern = _get_eav_pattern(schema_info)
            config_table = eav_pattern.get("config_table", "config_table") if eav_pattern else "config_table"
            attr_col = eav_pattern.get("attribute_column", "NAME") if eav_pattern else "NAME"
            val_col = eav_pattern.get("value_column", "VALUE") if eav_pattern else "VALUE"
            eav_lines = "\n".join(
                f'- "{field}" \u2192 EAV 속성 "{attr}" ({config_table}.{attr_col} = \'{attr}\' \u2192 {val_col})'
                for field, attr in eav_entries
            )
            # value_joins를 우선 사용하고, 없을 때만 join_condition 폴백
            join_hint = ""
            if eav_pattern and eav_pattern.get("value_joins"):
                vjs = eav_pattern["value_joins"]
                entity_table = eav_pattern.get("entity_table", "entity_table")
                vj_lines = []
                for vj in vjs:
                    vj_lines.append(
                        f"  {config_table}.{attr_col}='{vj['eav_attribute']}' -> "
                        f"{vj['eav_value_column']} = {entity_table}.{vj['entity_column']}"
                    )
                join_hint = (
                    "\n주의: 두 테이블 간 FK가 없으므로 값 기반 브릿지 조인을 사용하세요:\n"
                    + "\n".join(vj_lines)
                    + f"\n예: LEFT JOIN {config_table} p_host ON p_host.{attr_col}='Hostname' AND p_host.{val_col} = r.hostname"
                    f"\n     LEFT JOIN {config_table} p_attr ON p_attr.configuration_id = p_host.configuration_id AND p_attr.{attr_col} = '속성명'"
                )
            else:
                join_cond = eav_pattern.get("join_condition", "") if eav_pattern else ""
                if join_cond:
                    join_hint = f"\n조인 조건: {join_cond}"
            user_parts.append(
                f"## EAV 피벗 매핑 (반드시 CASE WHEN 피벗으로 변환)\n{eav_lines}\n\n"
                f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다.\n"
                "**결과 alias는 반드시 양식 필드명(왼쪽 한글, 따옴표 포함) 그대로** 하세요"
                "(임의 영문 alias 금지 — 결과 컬럼명이 양식 헤더와 일치해야 채워집니다):\n"
                f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS \"양식필드명\""
                f"{join_hint}\n"
                "반드시 GROUP BY를 포함하세요."
            )

    # 미매핑 필드(column_mapping=None) — 반드시 한글 필드명 그대로 alias (D-066 후속3/폼필 채우기).
    # 사용률 지표는 field_mapper가 의도적으로 미매핑하므로, 결과 컬럼명이 양식 헤더와 일치해야
    # excel_writer가 채운다. 단일 DB(query_generator)에 있는 "자동 매핑 실패 필드" 블록을 이식.
    if unmapped_fields:
        field_lines = "\n".join(f'- "{f}"' for f in unmapped_fields)
        user_parts.append(
            "## 자동 매핑 실패 필드 (스키마에서 직접 조회 필요)\n"
            "아래 양식 필드들은 자동 매핑에 실패했습니다. 스키마와 **위 쿼리 예시**를 참고하여 "
            "적절한 DB 컬럼 또는 계산식으로 반드시 SELECT에 포함하세요.\n"
            "**중요**: 각 필드명을 그대로(따옴표 포함) SQL alias로 사용하세요 — 결과 컬럼명이 "
            "양식 헤더와 정확히 일치해야 값이 채워집니다.\n"
            f"예: {decimal_cast_example(db_engine)}\n"
            "CPU/메모리 사용률(평균/최고) 등 성능 지표는 위 쿼리 예시의 cmm_metric_stat_m 피벗"
            "(resource_type + definition_name='Utilization', avg_val/max_val)을 그대로 따르되, "
            "**결과 alias는 아래 한글 필드명으로** 하세요(임의 영문명 금지):\n"
            f"{field_lines}"
        )

    if error_context:
        user_parts.append(
            f"## 이전 에러\n{error_context}\n위 에러를 수정한 새로운 SQL을 생성하세요."
        )

    user_prompt = "\n\n".join(user_parts)

    # 트랙 A(E2~E4): 멀티 DB 경로(C) 명시 이식 — NL 질의(폼필·재시도 아님)에만 다중 후보.
    # execute(읽기전용 실행 클로저)·_validate_sql_simple을 주입해 경로 비대칭 차단(§2.1 / D-066).
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
            return _validate_sql_simple(sql, schema_info)

        selection = await run_candidate_pipeline(
            llm, system_prompt, user_prompt,
            count=t2.candidate_count, strategies=t2.candidate_strategies,
            selection=t2.selection, is_kbgenai=isinstance(llm, KBGenAIChat),
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
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    response = await llm.ainvoke(messages)
    return extract_sql_from_response(response.content)


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
    from src.nodes.query_validator import _find_bare_hangul_tokens

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

    # LIMIT 없으면 추가
    if not re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        # 자동 추가하지는 않고 경고만
        pass

    return None


def _format_schema(schema_info: dict) -> str:
    """스키마 정보를 프롬프트용 텍스트로 변환한다.

    Args:
        schema_info: 스키마 딕셔너리

    Returns:
        스키마 텍스트
    """
    # excluded_join_columns 추출
    excluded_join_map = build_excluded_join_map(schema_info)

    lines: list[str] = []
    for table_name, table_data in schema_info.get("tables", {}).items():
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        lines.append(f"### {table_name}")
        for col in table_data.get("columns", []):
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            # JOIN 금지 컬럼 주석 추가
            col_lower = col["name"].lower()
            excluded_reason = excluded_join_map.get((bare_table, col_lower))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            lines.append(col_str)

        samples = table_data.get("sample_data", [])
        if samples:
            preview = json.dumps(samples[:3], ensure_ascii=False, indent=2)
            lines.append(f"  sample: {preview}")
        lines.append("")

    rels = schema_info.get("relationships", [])
    if rels:
        lines.append("### FK Relationships")
        for rel in rels:
            lines.append(f"  {rel['from']} -> {rel['to']}")

    return "\n".join(lines)


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
