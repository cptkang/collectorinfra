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
from src.routing.domain_config import get_domain_by_id
from src.security.audit_logger import log_query_execution
from src.state import AgentState, QueryAttempt
from src.utils.schema_utils import build_excluded_join_map

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
                # DB 엔진 정보 조회
                domain_cfg = get_domain_by_id(db_id)
                db_engine = domain_cfg.db_engine if domain_cfg else "postgresql"
                sql = await _generate_sql(
                    llm, parsed_requirements, schema_info,
                    sub_context, app_config.query.default_limit,
                    column_mapping=db_mapping,
                    db_engine=db_engine,
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
                        db_engine=db_engine,
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
    schema_dict, cache_hit, _descriptions, _synonyms = (
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

    return schema_dict


async def _generate_sql(
    llm: BaseChatModel,
    parsed_requirements: dict,
    schema_info: dict,
    sub_query_context: str,
    default_limit: int,
    error_context: str | None = None,
    column_mapping: dict[str, str] | None = None,
    db_engine: str = "postgresql",
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

    Returns:
        생성된 SQL 문자열
    """
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

    db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

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

        # EAV config 테이블과 entity 테이블이 다를 수 있으므로
        # 정규 컬럼 필터링을 제거하고 LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함.
        # (Plan 37: 수정 3-2)

        if regular_entries:
            mapping_lines = "\n".join(
                f'- "{field}" -> {col}' for field, col in regular_entries
            )
            user_parts.append(
                f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
                "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
                '"테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
                '예: SELECT s.hostname AS "servers.hostname"'
            )

        if eav_entries:
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
                f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다:\n"
                f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS alias"
                f"{join_hint}\n"
                "반드시 GROUP BY를 포함하세요."
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
