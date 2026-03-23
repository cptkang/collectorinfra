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
from src.prompts.query_generator import QUERY_GENERATOR_SYSTEM_TEMPLATE
from src.state import AgentState

logger = logging.getLogger(__name__)


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
    )

    user_prompt = _build_user_prompt(
        parsed_requirements=state["parsed_requirements"],
        template_structure=state.get("template_structure"),
        error_message=state.get("error_message") if is_retry else None,
        previous_sql=state.get("generated_sql") if is_retry else None,
        column_mapping=state.get("column_mapping"),
        conversation_context=conversation_context,
    )

    # LLM 호출
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]
    response = await llm.ainvoke(messages)

    # SQL 추출
    sql = _extract_sql_from_response(response.content)

    logger.info(f"SQL 생성 완료 (retry={retry_count}): {sql[:100]}...")

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
) -> str:
    """시스템 프롬프트를 구성한다.

    Args:
        schema_info: DB 스키마 정보
        default_limit: 기본 LIMIT 값
        column_descriptions: 컬럼 설명 매핑 (선택)
        column_synonyms: 유사 단어 매핑 (선택)
        resource_type_synonyms: RESOURCE_TYPE 값-한국어 매핑 (선택)
        eav_name_synonyms: EAV NAME 값-한국어 매핑 (선택)

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
    return QUERY_GENERATOR_SYSTEM_TEMPLATE.format(
        schema=schema_text,
        default_limit=default_limit,
    )


def _build_user_prompt(
    parsed_requirements: dict,
    template_structure: Optional[dict],
    error_message: Optional[str],
    previous_sql: Optional[str],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    conversation_context: Optional[dict] = None,
) -> str:
    """사용자 프롬프트를 구성한다.

    column_mapping이 제공되면 (field_mapper에서 생성) 매핑된 컬럼을
    명시적으로 SELECT에 포함하도록 지시한다.

    Args:
        parsed_requirements: 구조화된 요구사항
        template_structure: 양식 구조 (있으면)
        error_message: 이전 에러 메시지 (재시도 시)
        previous_sql: 이전 생성 SQL (재시도 시)
        column_mapping: 필드-컬럼 매핑 (field_mapper 결과, 선택)
        conversation_context: 멀티턴 대화 맥락 (선택)

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
        mapped_entries = [
            (field, col) for field, col in column_mapping.items() if col
        ]
        if mapped_entries:
            mapping_lines = "\n".join(
                f'- "{field}" -> {col}' for field, col in mapped_entries
            )
            parts.append(
                f"## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)\n{mapping_lines}\n\n"
                "위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,\n"
                'SELECT 시 "테이블명.컬럼명" 형식의 alias를 사용하세요.\n'
                '예: SELECT s.hostname AS "servers.hostname", ...'
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

    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_data in tables.items():
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
        lines.append("아래는 CORE_CONFIG_PROP.NAME 컬럼에 저장되는 설정 항목명입니다. 사용자가 한국어로 질의할 때 아래 매핑을 참고하세요.")
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


