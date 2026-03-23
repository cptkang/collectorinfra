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
from typing import Any, Optional

import sqlparse
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.routing.db_registry import DBRegistry
from src.schema_cache.fingerprint import (
    FINGERPRINT_SQL,
    compute_fingerprint,
    compute_fingerprint_from_schema_dict,
)
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.security.audit_logger import log_query_execution
from src.state import AgentState, QueryAttempt

logger = logging.getLogger(__name__)


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

    db_results: dict[str, list[dict]] = {}
    db_schemas: dict[str, dict] = {}
    db_errors: dict[str, str] = {}
    all_attempts: list[QueryAttempt] = list(state.get("query_attempts", []))

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
                sql = await _generate_sql(
                    llm, parsed_requirements, schema_info,
                    sub_context, app_config.query.default_limit,
                    column_mapping=db_mapping,
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
                        sub_context, app_config.query.default_limit,
                        error_context=validation_error,
                        column_mapping=db_mapping,
                    )
                    validation_error = _validate_sql_simple(sql, schema_info)
                    if validation_error:
                        db_errors[db_id] = f"SQL 검증 실패: {validation_error}"
                        continue

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

    # 전체 병합 결과 생성
    merged_results = _merge_results(db_results)

    return {
        "db_results": db_results,
        "db_schemas": db_schemas,
        "db_errors": db_errors,
        "query_results": merged_results,
        "query_attempts": all_attempts,
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

    영구 캐시를 활용하여 fingerprint 비교 후 변경이 없으면
    캐시된 스키마를 사용한다.

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

    persistent = PersistentSchemaCache(
        cache_dir=app_config.schema_cache.cache_dir,
        enabled=app_config.schema_cache.enabled,
    )

    # fingerprint 기반 캐시 확인
    if persistent.enabled:
        try:
            result = await client.execute_sql(FINGERPRINT_SQL)
            if result.rows:
                current_fp = compute_fingerprint(result.rows)
                if not persistent.is_changed(db_id, current_fp):
                    cached = persistent.get_schema(db_id)
                    if cached is not None:
                        logger.info(
                            "멀티DB 파일 캐시 히트: db_id=%s, fingerprint=%s",
                            db_id, current_fp,
                        )
                        return cached
        except Exception as e:
            logger.warning("멀티DB fingerprint 조회 실패 (%s): %s", db_id, e)

    # 캐시 미스: 전체 스키마 조회
    full_schema = await client.get_full_schema()

    tables_dict: dict[str, Any] = {}
    for table_name, table_info in full_schema.tables.items():
        columns = [
            {
                "name": col.name,
                "type": col.data_type,
                "nullable": col.nullable,
                "primary_key": col.is_primary_key,
                "foreign_key": col.is_foreign_key,
                "references": col.references,
            }
            for col in table_info.columns
        ]
        tables_dict[table_name] = {
            "columns": columns,
            "row_count_estimate": table_info.row_count_estimate,
            "sample_data": [],
        }

        # 샘플 데이터 수집
        try:
            samples = await client.get_sample_data(table_name, limit=3)
            tables_dict[table_name]["sample_data"] = samples
        except Exception:
            pass

    schema_dict = {
        "tables": tables_dict,
        "relationships": full_schema.relationships,
    }

    # 영구 캐시에 저장
    if persistent.enabled:
        persistent.save(db_id, schema_dict)

    return schema_dict


async def _generate_sql(
    llm: BaseChatModel,
    parsed_requirements: dict,
    schema_info: dict,
    sub_query_context: str,
    default_limit: int,
    error_context: str | None = None,
    column_mapping: dict[str, str] | None = None,
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

    Returns:
        생성된 SQL 문자열
    """
    schema_text = _format_schema(schema_info)
    system_prompt = QUERY_GENERATOR_SYSTEM_TEMPLATE.format(
        schema=schema_text,
        default_limit=default_limit,
    )

    user_parts = [
        f"## 사용자 질의\n{sub_query_context}",
        f"## 파싱된 요구사항\n```json\n{json.dumps(parsed_requirements, ensure_ascii=False, indent=2)}\n```",
    ]

    # column_mapping이 있으면 매핑 컬럼을 명시
    if column_mapping:
        mapped_entries = [
            (field, col) for field, col in column_mapping.items() if col
        ]
        if mapped_entries:
            mapping_lines = "\n".join(
                f'- "{field}" -> {col}' for field, col in mapped_entries
            )
            user_parts.append(
                f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
                "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
                '"테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
                '예: SELECT s.hostname AS "servers.hostname"'
            )

    if error_context:
        user_parts.append(
            f"## 이전 에러\n{error_context}\n위 에러를 수정한 새로운 SQL을 생성하세요."
        )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n\n".join(user_parts)),
    ]

    response = await llm.ainvoke(messages)
    return _extract_sql(response.content)


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

    # SELECT 문 확인
    sql_upper = sql.strip().upper()
    if not sql_upper.startswith("SELECT") and not sql_upper.startswith("--"):
        # 주석으로 시작할 수 있으므로 주석 제거 후 확인
        cleaned = re.sub(r"--[^\n]*\n", "", sql).strip().upper()
        if not cleaned.startswith("SELECT"):
            return "SELECT 문이 아닙니다."

    # 위험 키워드 확인
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
    for kw in dangerous:
        if re.search(rf"\b{kw}\b", sql, re.IGNORECASE):
            return f"금지 키워드 포함: {kw}"

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
    lines: list[str] = []
    for table_name, table_data in schema_info.get("tables", {}).items():
        lines.append(f"### {table_name}")
        for col in table_data.get("columns", []):
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
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


def _extract_sql(content: str) -> str:
    """LLM 응답에서 SQL을 추출한다.

    Args:
        content: LLM 응답 텍스트

    Returns:
        추출된 SQL 문자열
    """
    sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL)
    if sql_match:
        return sql_match.group(1).strip()

    code_match = re.search(
        r"```\s*(SELECT.*?)\s*```", content, re.DOTALL | re.IGNORECASE
    )
    if code_match:
        return code_match.group(1).strip()

    select_match = re.search(r"(SELECT\s+.*?;)", content, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()

    return content.strip()


def _merge_results(db_results: dict[str, list[dict]]) -> list[dict]:
    """여러 DB의 결과를 하나의 리스트로 병합한다.

    각 행에 _source_db 필드를 추가하여 출처를 표시한다.

    Args:
        db_results: DB별 쿼리 결과 {db_id: rows}

    Returns:
        병합된 결과 행 리스트
    """
    merged: list[dict] = []
    for db_id, rows in db_results.items():
        for row in rows:
            tagged_row = dict(row)
            tagged_row["_source_db"] = db_id
            merged.append(tagged_row)
    return merged
