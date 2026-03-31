"""SQL 생성 노드.

LLM을 사용하여 사용자 요구사항과 스키마 정보를 기반으로
SQL SELECT 쿼리를 자동 생성한다.
재시도 시 이전 에러 메시지를 반영하여 수정된 SQL을 생성한다.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.query_generator import (
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
    QUERY_GENERATOR_SYSTEM_TEMPLATE,
)
from src.state import AgentState
from src.utils.schema_utils import build_excluded_join_map

logger = logging.getLogger(__name__)


def _format_structure_guide(
    structure_meta: dict,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """구조 분석 메타데이터에서 쿼리 가이드를 포맷한다.

    structure_meta의 query_guide 문자열을 기반으로,
    EAV 패턴이 감지된 경우 resource_type/eav_name 유사단어 정보를 추가한다.

    Args:
        structure_meta: schema_info["_structure_meta"] 딕셔너리
        resource_type_synonyms: RESOURCE_TYPE 유사단어 매핑 (선택)
        eav_name_synonyms: EAV NAME 유사단어 매핑 (선택)

    Returns:
        포맷된 가이드 텍스트
    """
    guide = structure_meta.get("query_guide", "")

    # EAV 패턴 존재 여부 확인
    eav_patterns = [
        p for p in structure_meta.get("patterns", [])
        if p.get("type") == "eav"
    ]

    # EAV 패턴이 있고 query_guide가 존재하면, 조인 규칙 지침을 앞에 삽입
    if eav_patterns and guide:
        eav_join_rule = (
            "## EAV 테이블 조인 규칙\n"
            "EAV 구조의 entity 테이블과 config 테이블을 조인할 때 "
            "id 컬럼으로 직접 조인하지 마세요.\n"
            "두 테이블의 ID 체계가 다릅니다. "
            "반드시 아래 지침의 JOIN SQL 패턴을 그대로 사용하세요.\n\n"
        )
        guide = eav_join_rule + guide

    # RESOURCE_TYPE 유사단어 추가
    if resource_type_synonyms:
        guide += "\n\n### RESOURCE_TYPE 유사 단어\n"
        for rt_value, syns in resource_type_synonyms.items():
            guide += f"  - {rt_value} (유사: {', '.join(syns)})\n"

    # EAV 속성명 유사단어 추가 (EAV 패턴이 있을 때만)
    if eav_name_synonyms and eav_patterns:
        guide += "\n\n### EAV 속성명 유사 단어\n"
        for attr, syns in eav_name_synonyms.items():
            guide += f"  - {attr} (유사: {', '.join(syns)})\n"

    # EAV 패턴의 value_joins 정보를 쿼리 가이드에 추가
    for eav_p in eav_patterns:
        value_joins = eav_p.get("value_joins", [])
        if value_joins:
            entity_table = eav_p.get("entity_table", "entity_table")
            config_table = eav_p.get("config_table", "config_table")
            attr_col = eav_p.get("attribute_column", "NAME")
            guide += "\n\n[값 기반 조인 (value-based join)]"
            guide += (
                f"\n{config_table}과 {entity_table} 간 FK가 없습니다. "
                "다음 값 대응 관계를 조인에 활용하세요:"
            )
            for vj in value_joins:
                guide += (
                    f"\n- {config_table}.{attr_col}='{vj['eav_attribute']}'인 행의 "
                    f"{vj['eav_value_column']} 값은 "
                    f"{entity_table}.{vj['entity_column']}과 동일한 값입니다."
                )

    # 샘플 데이터 정보
    samples = structure_meta.get("samples", {})
    if samples:
        for purpose, rows in samples.items():
            if isinstance(rows, list) and rows:
                guide += f"\n\n### {purpose}\n"
                for row in rows[:10]:
                    guide += f"  - {row}\n"

    # 금지 JOIN 컬럼 경고
    for pattern in structure_meta.get("patterns", []):
        excluded = pattern.get("excluded_join_columns", [])
        if excluded:
            guide += "\n\n[금지 JOIN 컬럼]"
            guide += "\n다음 컬럼은 JOIN ON 절에서 절대 사용하지 마세요:"
            for excl in excluded:
                guide += (
                    f"\n- {excl.get('table', '?')}.{excl.get('column', '?')}: "
                    f"{excl.get('reason', 'JOIN 불가')}"
                )

    # 쿼리 예시 (few-shot) — 질문→SQL 쌍을 직접 제시하여 LLM 환각 방지
    query_examples = structure_meta.get("query_examples", [])
    if query_examples:
        guide += "\n\n## 쿼리 예시 (반드시 이 패턴을 따르세요)"
        guide += "\n아래 예시의 JOIN 패턴을 그대로 따라하세요. 임의로 다른 조인 조건을 만들지 마세요.\n"
        for i, ex in enumerate(query_examples, 1):
            question = ex.get("question", "")
            sql_example = ex.get("sql", "").rstrip()
            explanation = ex.get("explanation", "")
            guide += f"\n### 예시 {i}: \"{question}\""
            guide += f"\n```sql\n{sql_example}\n```"
            if explanation:
                guide += f"\n설명: {explanation}"
            guide += "\n"

    return guide


async def query_generator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 요구사항과 스키마를 기반으로 SQL 쿼리를 생성한다.

    재시도(회귀) 시에는 이전 에러 메시지를 컨텍스트에 포함하여
    수정된 SQL을 생성한다.

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - generated_sql: 생성된 SQL 쿼리 문자열
        - retry_count: 재시도 횟수 (증가)
        - error_message: None (초기화)
        - current_node: "query_generator"
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # 재시도 횟수 관리
    retry_count = state.get("retry_count", 0)
    is_retry = bool(state.get("error_message"))
    if is_retry:
        retry_count += 1

    # 멀티턴 맥락에서 이전 SQL 참조
    conversation_context = state.get("conversation_context")

    # 프롬프트 구성
    system_prompt = _build_system_prompt(
        schema_info=state["schema_info"],
        default_limit=app_config.query.default_limit,
        column_descriptions=state.get("column_descriptions", {}),
        column_synonyms=state.get("column_synonyms", {}),
        resource_type_synonyms=state.get("resource_type_synonyms"),
        eav_name_synonyms=state.get("eav_name_synonyms"),
        active_db_id=state.get("active_db_id"),
        polestar_db_id=app_config.polestar_db_id or None,
        active_db_engine=state.get("active_db_engine"),
    )

    user_prompt = _build_user_prompt(
        parsed_requirements=state["parsed_requirements"],
        template_structure=state.get("template_structure"),
        error_message=state.get("error_message") if is_retry else None,
        previous_sql=state.get("generated_sql") if is_retry else None,
        column_mapping=state.get("column_mapping"),
        conversation_context=conversation_context,
        schema_info=state["schema_info"],
    )

    # LLM 호출
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    response = await llm.ainvoke(messages)

    # SQL 추출
    sql = _extract_sql_from_response(response.content)

    logger.info(f"SQL 생성 완료 (retry={retry_count}): {sql[:1000]}...")

    return {
        "generated_sql": sql,
        "retry_count": retry_count,
        "error_message": None,  # 에러 메시지 초기화
        "current_node": "query_generator",
    }


def _build_system_prompt(
    schema_info: dict,
    default_limit: int,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
    active_db_id: str | None = None,
    polestar_db_id: str | None = None,
    active_db_engine: str | None = None,
) -> str:
    """시스템 프롬프트를 구성한다.

    Args:
        schema_info: DB 스키마 정보
        default_limit: 기본 LIMIT 값
        column_descriptions: 컬럼 설명 매핑 (선택)
        column_synonyms: 유사 단어 매핑 (선택)
        resource_type_synonyms: RESOURCE_TYPE 값-한국어 매핑 (선택)
        eav_name_synonyms: EAV NAME 값-한국어 매핑 (선택)
        active_db_id: 현재 활성 DB 식별자 (선택)
        polestar_db_id: Polestar 전용 프롬프트 적용 DB ID (선택, .env 설정)
        active_db_engine: 대상 DB 엔진 타입 (선택, 예: "db2", "postgresql")

    Returns:
        시스템 프롬프트 문자열
    """
    schema_text = _format_schema_for_prompt(
        schema_info,
        column_descriptions=column_descriptions,
        column_synonyms=column_synonyms,
        resource_type_synonyms=resource_type_synonyms,
        eav_name_synonyms=eav_name_synonyms,
    )

    # 구조 분석 가이드 (있으면 삽입)
    structure_meta = schema_info.get("_structure_meta")
    structure_guide = ""
    if structure_meta:
        structure_guide = _format_structure_guide(
            structure_meta,
            resource_type_synonyms=resource_type_synonyms,
            eav_name_synonyms=eav_name_synonyms,
        )

    # DB 엔진 힌트
    db_engine = active_db_engine or "postgresql"
    db_engine_hint = f"현재 대상 DB 엔진: **{db_engine.upper()}** — 이 엔진의 SQL 문법을 사용하세요."

    # Polestar 전용 프롬프트 선택: .env의 POLESTAR_DB_ID와 active_db_id가 일치하면 전용 템플릿 사용
    if polestar_db_id and active_db_id == polestar_db_id:
        template = POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
    else:
        template = QUERY_GENERATOR_SYSTEM_TEMPLATE

    return template.format(
        schema=schema_text,
        default_limit=default_limit,
        structure_guide=structure_guide,
        db_engine_hint=db_engine_hint,
    )


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


def _build_user_prompt(
    parsed_requirements: dict,
    template_structure: Optional[dict],
    error_message: Optional[str],
    previous_sql: Optional[str],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    conversation_context: Optional[dict] = None,
    schema_info: Optional[dict] = None,
) -> str:
    """사용자 프롬프트를 구성한다.

    column_mapping이 제공되면 (field_mapper에서 생성) 매핑된 컬럼을
    명시적으로 SELECT에 포함하도록 지시한다.
    schema_info가 제공되면 column_mapping의 테이블이 현재 스키마에
    존재하는지 검증하여 필터링한다.

    Args:
        parsed_requirements: 구조화된 요구사항
        template_structure: 양식 구조 (있으면)
        error_message: 이전 에러 메시지 (재시도 시)
        previous_sql: 이전 생성 SQL (재시도 시)
        column_mapping: 필드-컬럼 매핑 (field_mapper 결과, 선택)
        conversation_context: 멀티턴 대화 맥락 (선택)
        schema_info: DB 스키마 정보 (column_mapping 검증용, 선택)

    Returns:
        사용자 프롬프트 문자열
    """
    parts: list[str] = []

    # 멀티턴 맥락 (이전 SQL 참조)
    if (
        conversation_context
        and conversation_context.get("turn_count", 0) > 1
        and conversation_context.get("previous_sql")
        and not error_message  # 재시도가 아닌 경우에만
    ):
        parts.append(
            f"## 이전 대화의 SQL (참조용)\n"
            f"```sql\n{conversation_context['previous_sql']}\n```\n"
            f"이전 결과: {conversation_context.get('previous_results_summary', '없음')}\n\n"
            f"사용자가 이전 결과를 참조하는 경우, 이전 SQL을 기반으로 조건을 추가/수정하세요."
        )

    # 원본 질의
    original = parsed_requirements.get("original_query", "")
    parts.append(f"## 사용자 질의\n{original}")

    # 구조화된 요구사항
    req_json = json.dumps(parsed_requirements, ensure_ascii=False, indent=2)
    parts.append(f"## 파싱된 요구사항\n```json\n{req_json}\n```")

    # 양식-DB 매핑 (field_mapper에서 생성된 column_mapping 우선)
    if column_mapping:
        # 수정 A: schema_info 기반 column_mapping 필터링
        if schema_info:
            tables_in_schema = set(schema_info.get("tables", {}).keys())
            # schema_info 키가 "schema.table" 형식일 수 있으므로 마지막 부분도 매칭 대상에 추가
            # 예: "polestar.cmm_resource" → "cmm_resource"도 매칭
            tables_lower = set()
            for t in tables_in_schema:
                tables_lower.add(t.lower())
                # "schema.table" → "table" 부분도 추가
                if "." in t:
                    tables_lower.add(t.rsplit(".", 1)[-1].lower())

            filtered_mapping: dict[str, Optional[str]] = {}
            for field, col in column_mapping.items():
                if col and not col.startswith("EAV:"):
                    # "db_id:table.column" → "table.column" (콜론 접두사 제거)
                    effective_col = col.split(":", 1)[-1] if ":" in col else col
                    parts = effective_col.split(".")
                    # "db_id.table.column" (3단계) → table = parts[-2]
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
                            "column_mapping 필터링: '%s' -> '%s' (테이블 '%s' 미존재, schema_tables=%s)",
                            field, col, table_part, list(tables_in_schema)[:5],
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
            parts.append(
                f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
                "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
                'SELECT 시 "테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
                '예: SELECT s.hostname AS "servers.hostname", ...'
            )

        if eav_entries:
            # _structure_meta에서 EAV 패턴 정보를 동적 추출
            eav_pattern = _get_eav_pattern(schema_info)
            config_table = eav_pattern.get("config_table", "config_table") if eav_pattern else "config_table"
            attr_col = eav_pattern.get("attribute_column", "NAME") if eav_pattern else "NAME"
            val_col = eav_pattern.get("value_column", "VALUE") if eav_pattern else "VALUE"
            eav_lines = "\n".join(
                f'- "{field}" → EAV 속성 "{attr}" ({config_table}.{attr_col} = \'{attr}\' → {val_col})'
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
                    + "\n예: LEFT JOIN {ct} p_host ON p_host.{ac}='Hostname' AND p_host.{vc} = r.hostname"
                    "\n     LEFT JOIN {ct} p_attr ON p_attr.configuration_id = p_host.configuration_id AND p_attr.{ac} = '속성명'"
                ).format(ct=config_table, ac=attr_col, vc=val_col)
            else:
                join_cond = eav_pattern.get("join_condition", "") if eav_pattern else ""
                if join_cond:
                    join_hint = f"\n조인 조건: {join_cond}"
            parts.append(
                f"## EAV 피벗 매핑 (반드시 CASE WHEN 피벗으로 변환)\n{eav_lines}\n\n"
                f"위 EAV 속성은 {config_table} 테이블에서 피벗 쿼리로 추출해야 합니다:\n"
                f"  MAX(CASE WHEN p.{attr_col} = '속성명' THEN p.{val_col} END) AS alias"
                f"{join_hint}\n"
                "반드시 GROUP BY를 포함하세요."
            )
    elif template_structure:
        # column_mapping이 없으면 기존 방식 (하위 호환)
        tmpl_json = json.dumps(template_structure, ensure_ascii=False, indent=2)
        parts.append(f"## 양식 구조\n```json\n{tmpl_json}\n```")
        parts.append(
            "양식의 헤더/플레이스홀더에 해당하는 컬럼을 반드시 SELECT에 포함하세요."
        )

    # 재시도 컨텍스트
    if error_message and previous_sql:
        parts.append(
            f"## 이전 시도 (실패)\n"
            f"이전 SQL:\n```sql\n{previous_sql}\n```\n"
            f"에러: {error_message}"
        )
        parts.append("위 에러를 수정한 새로운 SQL을 생성하세요.")

    return "\n\n".join(parts)


def _format_schema_for_prompt(
    schema_info: dict,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """스키마 정보를 프롬프트에 적합한 텍스트로 변환한다.

    컬럼 설명과 유사 단어가 있으면 함께 포함하여
    LLM의 컬럼 선택 정확도를 높인다.
    resource_type/eav_name 유사단어가 있으면 참조 정보로 추가한다.

    Args:
        schema_info: 스키마 딕셔너리
        column_descriptions: {table.column: description} 매핑 (선택)
        column_synonyms: {table.column: [synonym, ...]} 매핑 (선택)
        resource_type_synonyms: {resource_type값: [한국어 표현, ...]} 매핑 (선택)
        eav_name_synonyms: {eav_name값: [한국어 표현, ...]} 매핑 (선택)

    Returns:
        사람이 읽기 쉬운 스키마 텍스트
    """
    descriptions = column_descriptions or {}
    synonyms = column_synonyms or {}

    # excluded_join_columns 추출: {(table_lower, column_lower): reason}
    excluded_join_map = build_excluded_join_map(schema_info)

    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_data in tables.items():
        # table_name에서 스키마 접두사 제거한 bare name 추출
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        columns_desc: list[str] = []
        for col in table_data.get("columns", []):
            col_key = f"{table_name}.{col['name']}"
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            if not col.get("nullable", True):
                col_str += " NOT NULL"
            # JOIN 금지 컬럼 주석 추가
            col_lower = col["name"].lower()
            excluded_reason = excluded_join_map.get((bare_table, col_lower))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            # 컬럼 설명 추가
            desc = descriptions.get(col_key)
            if desc:
                col_str += f" -- {desc}"
            # 유사 단어 추가
            syns = synonyms.get(col_key)
            if syns:
                col_str += f" [유사: {', '.join(syns[:5])}]"
            columns_desc.append(col_str)
        lines.append(f"### {table_name}")
        lines.extend(columns_desc)

        # 샘플 데이터 (있으면, 3건까지 표시)
        samples = table_data.get("sample_data", [])
        if samples:
            preview = json.dumps(samples[:3], ensure_ascii=False, indent=2)
            lines.append(f"  샘플 데이터 ({len(samples)}건):\n{preview}")
        lines.append("")

    # resource_type_values 참조 정보
    if resource_type_synonyms:
        lines.append("")
        lines.append("### 참조: RESOURCE_TYPE 값과 한국어 표현")
        lines.append("아래 값들은 RESOURCE_TYPE 컬럼에 저장되는 값입니다. 사용자가 한국어로 질의할 때 아래 매핑을 참고하세요.")
        for rt_value, words in sorted(resource_type_synonyms.items()):
            lines.append(f"  - {rt_value} = {', '.join(words)}")

    # eav_name_values 참조 정보
    if eav_name_synonyms:
        lines.append("")
        lines.append("### 참조: EAV 설정 항목명과 한국어 표현")
        lines.append("아래는 EAV 테이블의 속성명 컬럼에 저장되는 설정 항목명입니다. 사용자가 한국어로 질의할 때 아래 매핑을 참고하세요.")
        for eav_name, words in sorted(eav_name_synonyms.items()):
            lines.append(f"  - {eav_name} = {', '.join(words)}")

    # FK 관계
    rels = schema_info.get("relationships", [])
    if rels:
        lines.append("### 테이블 관계 (FK)")
        for rel in rels:
            lines.append(f"  {rel['from']} -> {rel['to']}")

    return "\n".join(lines)


def _extract_sql_from_response(content: str) -> str:
    """LLM 응답에서 SQL 쿼리를 추출한다.

    Args:
        content: LLM 응답 텍스트

    Returns:
        추출된 SQL 문자열
    """
    # ```sql ... ``` 패턴
    sql_match = re.search(r"```sql\s*(.*?)\s*```", content, re.DOTALL)
    if sql_match:
        return sql_match.group(1).strip()

    # ``` ... ``` 패턴 (SELECT로 시작)
    code_match = re.search(
        r"```\s*(SELECT.*?)\s*```", content, re.DOTALL | re.IGNORECASE
    )
    if code_match:
        return code_match.group(1).strip()

    # SELECT로 시작하는 텍스트 직접 추출
    select_match = re.search(r"(SELECT\s+.*?;)", content, re.DOTALL | re.IGNORECASE)
    if select_match:
        return select_match.group(1).strip()

    # 전체 내용 반환 (최후 수단)
    return content.strip()


