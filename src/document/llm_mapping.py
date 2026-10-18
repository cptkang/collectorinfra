"""양식 필드 LLM 매핑(레거시 단일·멀티 DB 호출) · 프롬프트용 포맷 · 매핑 검증.

`src.document.field_mapper`(3단계 매핑 Facade)에서 옮겼다(plans/166 I2). 종전 경로는 Facade가
옛 이름(`_` 접두 포함)으로 재노출한다(D-337 ①·③). 3단계 강화 추론
(`_apply_llm_mapping_with_synonyms`)은 테스트가 Facade 경로에서 프롬프트 상수를 patch하므로
Facade에 남는다(D-337 ②).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.document.mapping_steps import MappingResult
from src.prompts.field_mapper import (
    FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT,
    FIELD_MAPPER_MULTI_DB_USER_PROMPT,
    FIELD_MAPPER_SYSTEM_PROMPT,
    FIELD_MAPPER_USER_PROMPT,
    FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES,
)
from src.utils.json_extract import extract_json_from_response

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I2)
logger = logging.getLogger("src.document.field_mapper")


# === Step 3 (Legacy): LLM Mapping ===


async def _apply_llm_mapping(
    llm: BaseChatModel,
    remaining_fields: list[str],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    result: MappingResult,
    example_rows: list[list[str]] | None = None,
) -> None:
    """LLM을 통해 남은 필드의 매핑을 수행한다.

    여러 DB의 descriptions를 포함한 프롬프트로 LLM에 매핑을 요청하며,
    LLM은 각 필드에 대해 db_id와 table.column을 반환한다.

    Args:
        llm: LLM 인스턴스
        remaining_fields: 매핑되지 않은 필드 목록
        all_db_descriptions: DB별 descriptions {db_id: {table.column: desc}}
        priority_db_ids: 우선순위 DB 목록
        result: 매핑 결과 객체
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 remaining_fields와 동일 순서의 값 리스트.
    """
    if not remaining_fields:
        return

    if not all_db_descriptions:
        logger.warning("DB descriptions가 없어 LLM 매핑을 수행할 수 없습니다.")
        return

    # 멀티 DB descriptions를 하나의 프롬프트로 구성
    llm_mapping = await _invoke_llm_mapping_multi_db(
        llm,
        remaining_fields,
        all_db_descriptions,
        priority_db_ids,
        example_rows=example_rows,
    )

    for field, mapping_info in llm_mapping.items():
        if mapping_info:
            db_id = mapping_info.get("db_id")
            column = mapping_info.get("column")
            if db_id and column:
                if priority_db_ids and db_id not in priority_db_ids:
                    continue
                result.db_column_mapping.setdefault(db_id, {})[field] = column
                result.mapping_sources[field] = "llm_inferred"


async def _invoke_llm_mapping_multi_db(
    llm: BaseChatModel,
    field_names: list[str],
    all_db_descriptions: dict[str, dict[str, str]],
    priority_db_ids: list[str],
    example_rows: list[list[str]] | None = None,
) -> dict[str, dict[str, str] | None]:
    """멀티 DB 환경에서 LLM 매핑을 수행한다.

    Args:
        llm: LLM 인스턴스
        field_names: 매핑할 필드 목록
        all_db_descriptions: DB별 descriptions
        priority_db_ids: 우선순위 DB
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 field_names와 동일 순서의 값 리스트.

    Returns:
        {field: {"db_id": str, "column": str} | None}
    """
    # DB별 스키마 정보를 프롬프트용으로 포맷
    db_schema_parts: list[str] = []
    if priority_db_ids:
        ordered_db_ids = priority_db_ids
    else:
        ordered_db_ids = list(all_db_descriptions.keys())
    for db_id in ordered_db_ids:
        descs = all_db_descriptions.get(db_id, {})
        if not descs:
            continue
        lines = [f"## DB: {db_id}"]
        for col_key, desc in descs.items():
            lines.append(f"- {col_key}: {desc}")
        db_schema_parts.append("\n".join(lines))

    schema_text = "\n\n".join(db_schema_parts)

    if example_rows:
        field_names_text = format_field_names_with_examples(
            field_names, example_rows
        )
    else:
        field_names_text = "\n".join(f"- {name}" for name in field_names)

    user_prompt = FIELD_MAPPER_MULTI_DB_USER_PROMPT.format(
        field_names=field_names_text,
        db_schema_columns=schema_text,
    )

    messages: list[BaseMessage] = [
        SystemMessage(content=FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT)
    ]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    for attempt in range(2):
        try:
            response = await llm.ainvoke(messages)
            parsed = extract_json_from_response(response.content)
            if parsed:
                # {"field": {"db_id": "x", "column": "y"}} 형식 기대
                result: dict[str, dict[str, str] | None] = {}
                for field in field_names:
                    mapping = parsed.get(field)
                    if isinstance(mapping, dict):
                        result[field] = mapping
                    elif isinstance(mapping, str) and mapping:
                        # 단순 "table.column" 형식인 경우 첫 번째 DB에 할당
                        result[field] = {
                            "db_id": ordered_db_ids[0] if ordered_db_ids else "_default",
                            "column": mapping,
                        }
                    else:
                        result[field] = None
                return result
        except Exception as e:
            logger.warning("LLM 멀티DB 매핑 호출 실패 (시도 %d): %s", attempt + 1, e)

        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. 각 필드에 대해 {\"db_id\": \"...\", \"column\": \"...\"} 형식으로 응답합니다."  # noqa: E501
        ))

    logger.error("LLM 멀티DB 매핑 실패: 모든 시도 소진")
    return {name: None for name in field_names}


# === Legacy LLM Mapping (Single DB) ===


async def invoke_llm_mapping(
    llm: BaseChatModel,
    field_names: list[str],
    schema_columns: str,
    example_rows: list[list[str]] | None = None,
) -> dict[str, str | None]:
    """LLM을 호출하여 필드 매핑을 수행한다 (단일 DB).

    JSON 파싱 실패 시 1회 재시도한다.

    Args:
        llm: LLM 인스턴스
        field_names: 양식 필드명 목록
        schema_columns: 포맷된 스키마 컬럼 문자열
        example_rows: 예시 데이터 행 목록 (선택). 각 행은 field_names와 동일한 순서의 값 리스트.

    Returns:
        매핑 딕셔너리
    """
    if example_rows:
        field_names_with_examples = format_field_names_with_examples(
            field_names, example_rows
        )
        user_prompt = FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES.format(
            field_names_with_examples=field_names_with_examples,
            schema_columns=schema_columns,
        )
    else:
        user_prompt = FIELD_MAPPER_USER_PROMPT.format(
            field_names="\n".join(f"- {name}" for name in field_names),
            schema_columns=schema_columns,
        )

    messages: list[BaseMessage] = [
        SystemMessage(content=FIELD_MAPPER_SYSTEM_PROMPT)
    ]
    if isinstance(llm, KBGenAIChat):
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    for attempt in range(2):
        try:
            response = await llm.ainvoke(messages)
            mapping = extract_json_from_response(response.content)
            if mapping:
                return mapping
        except Exception as e:
            logger.warning("LLM 매핑 호출 실패 (시도 %d): %s", attempt + 1, e)

        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. 설명 텍스트 없이 JSON만 응답합니다."
        ))

    logger.error("LLM 필드 매핑 실패: 모든 시도 소진")
    return {name: None for name in field_names}


# === Utility ===


def build_eav_mapping_guide(
    eav_name_synonyms: dict[str, list[str]],
) -> str:
    """EAV 속성 유사어 정보에서 범용 매핑 가이드를 동적으로 생성한다.

    EAV 패턴이 감지된 DB에 대해, 속성명과 유사어 정보를 기반으로
    LLM이 매핑에 활용할 수 있는 가이드 텍스트를 생성한다.

    Args:
        eav_name_synonyms: {eav_name: [유사어 목록]}

    Returns:
        EAV 매핑 가이드 문자열
    """
    lines = ["## EAV 매핑 가이드"]
    lines.append("")
    lines.append("이 DB는 EAV(Entity-Attribute-Value) 패턴을 사용합니다.")
    lines.append("엔티티 속성이 별도 설정 테이블의 행으로 저장되어 있습니다.")
    lines.append("매핑 시 'EAV:속성명' 형식을 사용하세요.")
    lines.append("")
    lines.append("### EAV 속성 목록")
    for eav_name, words in eav_name_synonyms.items():
        if words:
            synonyms_text = ", ".join(words[:5])
            lines.append(f"- EAV:{eav_name} (유사어: {synonyms_text})")
        else:
            lines.append(f"- EAV:{eav_name}")
    return "\n".join(lines)


def format_field_names_with_examples(
    field_names: list[str],
    example_rows: list[list[str]],
) -> str:
    """필드명과 예시 데이터를 결합하여 프롬프트용 문자열로 포맷한다.

    Args:
        field_names: 양식 필드명 목록
        example_rows: 예시 데이터 행 목록. 각 행은 field_names와 동일 순서의 값 리스트.

    Returns:
        포맷된 문자열. 예: '- 서버명 (예시: "web-server-01", "db-server-02")'
    """
    lines: list[str] = []
    for i, name in enumerate(field_names):
        examples: list[str] = []
        for row in example_rows:
            if i < len(row):
                val = str(row[i]).strip()
                if val:
                    examples.append(val)
            if len(examples) >= 3:
                break
        if examples:
            quoted = ", ".join(f'"{ex}"' for ex in examples)
            lines.append(f"- {name} (예시: {quoted})")
        else:
            lines.append(f"- {name}")
    return "\n".join(lines)


def format_schema_columns(
    schema_info: dict[str, Any],
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
) -> str:
    """스키마 정보를 LLM 프롬프트용 문자열로 포맷한다.

    Args:
        schema_info: DB 스키마 정보
        column_descriptions: 컬럼 설명 (선택)
        column_synonyms: 유사 단어 (선택)

    Returns:
        포맷된 스키마 문자열
    """
    descs = column_descriptions or {}
    syns = column_synonyms or {}
    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_info in tables.items():
        columns = table_info.get("columns", [])
        for col in columns:
            col_name = col.get("name", "")
            col_type = col.get("type", "")
            if col_name:
                col_key = f"{table_name}.{col_name}"
                entry = f"- {col_key} ({col_type})"
                desc = descs.get(col_key)
                if desc:
                    entry += f" -- {desc}"
                syn_list = syns.get(col_key)
                if syn_list:
                    entry += f" [유사: {', '.join(syn_list[:5])}]"
                lines.append(entry)

    # EAV 가상 컬럼 추가 (_structure_meta에서 동적 추출)
    structure_meta = schema_info.get("_structure_meta")
    if structure_meta:
        eav_patterns = [
            p for p in structure_meta.get("patterns", [])
            if p.get("type") == "eav"
        ]
        if eav_patterns:
            lines.append("")
            lines.append("# EAV 속성 (피벗 쿼리로 추출)")
            lines.append("# 매핑 시 'EAV:속성명' 형식을 사용하세요.")
            for p in eav_patterns:
                for attr in p.get("known_attributes", []):
                    lines.append(f"- EAV:{attr} -- EAV 피벗 속성")

    return "\n".join(lines)


def validate_mapping(
    mapping: dict[str, str | None],
    schema_info: dict[str, Any],
    field_names: list[str],
) -> dict[str, str | None]:
    """매핑 결과를 검증한다.

    존재하지 않는 테이블.컬럼을 참조하는 매핑은 None으로 변경한다.

    Args:
        mapping: LLM이 반환한 매핑
        schema_info: DB 스키마 정보
        field_names: 원본 필드명 목록

    Returns:
        검증된 매핑 딕셔너리
    """
    valid_columns: set[str] = set()
    lower_to_original: dict[str, str] = {}
    tables = schema_info.get("tables", {})
    for table_name, table_info in tables.items():
        for col in table_info.get("columns", []):
            col_name = col.get("name", "")
            if col_name:
                full_name = f"{table_name}.{col_name}"
                valid_columns.add(full_name)
                lower_to_original[full_name.lower()] = full_name

    # EAV known attributes (_structure_meta에서 동적 추출)
    structure_meta = schema_info.get("_structure_meta")
    known_eav_attrs: set[str] = set()
    if structure_meta:
        for p in structure_meta.get("patterns", []):
            if p.get("type") == "eav":
                known_eav_attrs.update(p.get("known_attributes", []))

    validated: dict[str, str | None] = {}
    for name in field_names:
        mapped_col = mapping.get(name)
        if mapped_col:
            # EAV 속성 검증
            if mapped_col.startswith("EAV:"):
                attr_name = mapped_col[4:]
                if attr_name in known_eav_attrs:
                    validated[name] = mapped_col
                else:
                    logger.warning(
                        "EAV 매핑 검증 실패: '%s' -> '%s' (알 수 없는 EAV 속성)",
                        name,
                        mapped_col,
                    )
                    validated[name] = None
            # 정규 컬럼 검증
            elif mapped_col in valid_columns:
                validated[name] = mapped_col
            elif mapped_col.lower() in lower_to_original:
                validated[name] = lower_to_original[mapped_col.lower()]
            else:
                logger.warning(
                    "매핑 검증 실패: '%s' -> '%s' (존재하지 않는 컬럼)",
                    name,
                    mapped_col,
                )
                validated[name] = None
        else:
            validated[name] = None

    return validated
