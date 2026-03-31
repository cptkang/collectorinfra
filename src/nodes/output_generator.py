"""출력 생성 노드.

최종 자연어 응답 또는 문서 파일을 생성한다.
Phase 1에서는 자연어 응답만 지원하고, Phase 2에서 Excel/Word 생성을 추가한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.prompts.output_generator import OUTPUT_GENERATOR_SYSTEM_PROMPT
from src.state import AgentState

logger = logging.getLogger(__name__)


async def output_generator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """최종 응답을 생성한다.

    output_format에 따라 분기:
    - "text": LLM으로 자연어 응답 생성
    - "xlsx": Excel 파일 생성 (Phase 2)
    - "docx": Word 파일 생성 (Phase 2)

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - final_response: 자연어 응답 텍스트
        - output_file: 생성된 파일 바이너리 (파일 출력 시)
        - output_file_name: 출력 파일명 (파일 출력 시)
        - current_node: "output_generator"
        - error_message: None (정상 완료 시)
    """
    if app_config is None:
        app_config = load_config()
    organized = state["organized_data"]
    parsed = state["parsed_requirements"]
    output_format = parsed.get("output_format", "text")

    if output_format == "text":
        response = await _generate_text_response(app_config, state, llm=llm)
        response = _append_inferred_mapping_info(response, state)
        return {
            "final_response": response,
            "output_file": None,
            "output_file_name": None,
            "current_node": "output_generator",
            "error_message": None,
        }

    elif output_format in ("xlsx", "docx"):
        # Phase 2: 파일 생성
        file_result = _generate_document_file(state, output_format)
        if file_result:
            text_response = await _generate_text_response(app_config, state, llm=llm)
            text_response = _append_inferred_mapping_info(text_response, state)

            # Excel 데이터 0건 채움 경고 메시지 추가
            total_filled = file_result.get("total_filled")
            organized = state["organized_data"]
            rows = organized.get("rows", [])
            if total_filled == 0 and rows:
                text_response = (
                    f"조회된 데이터 {len(rows)}건을 Excel 양식에 매핑하지 못했습니다.\n"
                    f"양식의 헤더와 DB 컬럼 간 매핑이 일치하지 않습니다.\n"
                    f"매핑 보고서를 확인하고 유사어를 등록해주세요.\n\n"
                    + text_response
                )

            return {
                "final_response": text_response,
                "output_file": file_result["file_bytes"],
                "output_file_name": file_result["file_name"],
                "current_node": "output_generator",
                "error_message": None,
            }
        else:
            # 파일 생성 실패: 텍스트 응답으로 폴백
            logger.warning("파일 생성 실패, 텍스트 응답으로 대체")
            text_response = await _generate_text_response(app_config, state, llm=llm)
            text_response = _append_inferred_mapping_info(text_response, state)
            return {
                "final_response": (
                    f"{output_format.upper()} 파일 생성에 실패하여 "
                    f"텍스트 응답으로 대체합니다.\n\n{text_response}"
                ),
                "output_file": None,
                "output_file_name": None,
                "current_node": "output_generator",
                "error_message": None,
            }

    else:
        return {
            "final_response": f"지원하지 않는 출력 형식입니다: {output_format}",
            "output_file": None,
            "output_file_name": None,
            "current_node": "output_generator",
            "error_message": None,
        }


async def _generate_text_response(
    config: AppConfig,
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
) -> str:
    """LLM을 사용하여 자연어 응답을 생성한다.

    Args:
        config: 앱 설정
        state: 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)

    Returns:
        자연어 응답 텍스트
    """
    organized = state["organized_data"]
    parsed = state["parsed_requirements"]

    # 결과가 없는 경우
    if not organized["rows"]:
        return _generate_empty_result_response(parsed)

    if llm is None:
        llm = create_llm(config)

    user_prompt = _build_response_prompt(
        original_query=parsed.get("original_query", ""),
        summary=organized["summary"],
        rows=organized["rows"],
        sql=state["generated_sql"],
    )

    messages = [
        SystemMessage(content=OUTPUT_GENERATOR_SYSTEM_PROMPT),
        HumanMessage(content=user_prompt),
    ]

    response = await llm.ainvoke(messages)
    return response.content


def _generate_empty_result_response(parsed: dict) -> str:
    """결과가 0건일 때의 응답을 생성한다.

    Args:
        parsed: 파싱된 요구사항

    Returns:
        빈 결과 안내 텍스트
    """
    targets = ", ".join(parsed.get("query_targets", ["데이터"]))
    filters = parsed.get("filter_conditions", [])

    response = f"조건에 해당하는 {targets} 데이터가 없습니다."

    if filters:
        response += "\n\n다음과 같은 방법을 시도해보세요:"
        response += "\n- 필터 조건을 완화해보세요 (예: 임계값 낮추기)"
        if parsed.get("time_range"):
            response += "\n- 시간 범위를 넓혀보세요"

    return response


def _build_response_prompt(
    original_query: str,
    summary: str,
    rows: list[dict],
    sql: str,
) -> str:
    """응답 생성 프롬프트를 구성한다.

    Args:
        original_query: 원본 사용자 질의
        summary: 데이터 요약
        rows: 결과 데이터 행
        sql: 실행된 SQL

    Returns:
        구성된 프롬프트 문자열
    """
    # 결과가 많으면 상위 20건만 프롬프트에 포함
    display_rows = rows[:20]
    truncated = len(rows) > 20

    parts = [
        f"## 사용자 질의\n{original_query}",
        f"## 데이터 요약\n{summary}",
        f"## 실행된 SQL\n```sql\n{sql}\n```",
        (
            f"## 조회 결과 ({len(rows)}건"
            f"{', 상위 20건 표시' if truncated else ''})\n"
            f"```json\n"
            f"{json.dumps(display_rows, ensure_ascii=False, indent=2)}\n"
            f"```"
        ),
    ]

    return "\n\n".join(parts)


def _append_inferred_mapping_info(response: str, state: AgentState) -> str:
    """LLM 추론 매핑 정보와 유사어 등록 안내를 응답에 추가한다.

    mapping_sources에서 "llm_inferred" 항목이 있으면,
    최종 응답에 매핑 내역을 표시하고 유사어 등록 여부를 질문한다.

    Args:
        response: 기존 응답 텍스트
        state: 에이전트 상태

    Returns:
        매핑 정보가 추가된 응답 텍스트
    """
    mapping_sources = state.get("mapping_sources")
    if not mapping_sources:
        return response

    column_mapping = state.get("column_mapping", {})
    db_column_mapping = state.get("db_column_mapping", {})

    # LLM 추론 매핑만 수집
    inferred_items: list[tuple[str, str, str]] = []  # (field, column, db_id)
    for field, source in mapping_sources.items():
        if source != "llm_inferred":
            continue
        col = column_mapping.get(field)
        if not col:
            continue
        # db_id 찾기
        db_id = "unknown"
        if db_column_mapping:
            for d_id, d_map in db_column_mapping.items():
                if field in d_map:
                    db_id = d_id
                    break
        inferred_items.append((field, col, db_id))

    if not inferred_items:
        return response

    mapping_text = "\n".join(
        f'  {i}. "{field}" -> {col} ({db_id})'
        for i, (field, col, db_id) in enumerate(inferred_items, 1)
    )

    response += (
        f"\n\n---\n"
        f"**[자동 매핑 안내]** 다음 필드는 LLM이 추론하여 매핑했습니다:\n{mapping_text}\n\n"
        f"이 매핑이 정확하다면 **유사어로 등록**하여 다음부터 자동 매핑할 수 있습니다.\n"
        f'- 전체 등록: "전체 등록" 또는 "모두 등록"\n'
        f'- 선택 등록: "1, 3 등록" (번호 지정)\n'
        f'- 매핑 변경: "서버명은 hostname 컬럼으로 조회" 형태로 지정'
    )

    return response


def _validate_mapping_against_csv(
    csv_sheet_data: dict[str, Any],
    column_mapping: dict[str, Optional[str]],
) -> None:
    """CSV 헤더와 column_mapping 키의 정합성을 검증하고 경고 로깅한다.

    Args:
        csv_sheet_data: {시트명: {"headers": [...], ...}} 형태
        column_mapping: {필드명: DB 컬럼명 또는 None} 매핑
    """
    # csv_sheet_data에서 전체 헤더 수집
    csv_headers: set[str] = set()
    for sheet_data in csv_sheet_data.values():
        if isinstance(sheet_data, dict):
            csv_headers.update(sheet_data.get("headers", []))

    mapping_keys = set(column_mapping.keys())
    mapped_keys = {k for k, v in column_mapping.items() if v is not None}

    # 1. CSV 헤더 중 column_mapping에 없는 것
    unmapped_headers = csv_headers - mapping_keys
    if unmapped_headers:
        logger.info("CSV 헤더 중 매핑 미존재: %s", unmapped_headers)

    # 2. column_mapping 키 중 CSV 헤더에 없는 것 (불일치)
    orphan_keys = mapping_keys - csv_headers
    if orphan_keys:
        logger.warning("column_mapping 키가 CSV 헤더와 불일치: %s", orphan_keys)

    # 3. 매핑된 필드 비율 검증
    if csv_headers:
        mapped_ratio = len(mapped_keys & csv_headers) / len(csv_headers)
        logger.info(
            "매핑 정합성: CSV 헤더 %d개 중 %d개 매핑됨 (%.0f%%)",
            len(csv_headers),
            len(mapped_keys & csv_headers),
            mapped_ratio * 100,
        )
        if mapped_ratio == 0:
            logger.warning(
                "매핑률 0%%: column_mapping 값이 모두 None이거나 "
                "CSV 헤더와 키가 완전히 불일치합니다. "
                "Excel 데이터 채우기가 실패할 가능성이 높습니다."
            )


def _generate_document_file(
    state: AgentState,
    output_format: str,
) -> dict | None:
    """Excel 또는 Word 파일을 생성한다.

    Args:
        state: 에이전트 상태
        output_format: "xlsx" 또는 "docx"

    Returns:
        {"file_bytes": bytes, "file_name": str} 또는 None (실패 시)
    """
    organized = state["organized_data"]
    rows = organized.get("rows", [])
    # field_mapper State의 column_mapping을 우선 사용, 없으면 organized_data에서 가져옴
    column_mapping = state.get("column_mapping") or organized.get("column_mapping")
    # resolved_mapping 우선: result_organizer가 생성한 해석된 매핑
    resolved_mapping = organized.get("resolved_mapping")
    effective_mapping = resolved_mapping or column_mapping
    template = state.get("template_structure")
    uploaded_file = state.get("uploaded_file")

    if not template or not uploaded_file:
        logger.warning("양식 구조 또는 원본 파일이 없어 파일 생성 불가")
        return None

    if not effective_mapping:
        logger.warning("컬럼 매핑이 없어 파일 생성 불가")
        return None

    # 매핑 검증: csv_sheet_data 헤더와 column_mapping 비교
    csv_sheet_data = state.get("csv_sheet_data")
    if csv_sheet_data and effective_mapping:
        _validate_mapping_against_csv(csv_sheet_data, effective_mapping)

    import datetime

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    try:
        if output_format == "xlsx":
            from src.document.excel_writer import fill_excel_template

            # 멀티시트: sheet_mappings와 target_sheets 전달
            sheet_mappings = organized.get("sheet_mappings")
            target_sheets = state.get("target_sheets")

            file_bytes, total_filled = fill_excel_template(
                file_data=uploaded_file,
                template_structure=template,
                column_mapping=effective_mapping,
                rows=rows,
                sheet_mappings=sheet_mappings,
                target_sheets=target_sheets,
            )

            if total_filled == 0 and rows:
                logger.warning(
                    "데이터 %d건이 조회되었으나 Excel에 0건 채워짐. "
                    "column_mapping=%s, row_keys=%s",
                    len(rows),
                    {k: v for k, v in list(effective_mapping.items())[:5]},
                    list(rows[0].keys())[:10] if rows else [],
                )

            return {
                "file_bytes": file_bytes,
                "file_name": f"result_{timestamp}.xlsx",
                "total_filled": total_filled,
            }

        elif output_format == "docx":
            from src.document.word_writer import fill_word_template

            file_bytes = fill_word_template(
                file_data=uploaded_file,
                template_structure=template,
                column_mapping=effective_mapping,
                rows=rows,
            )
            return {
                "file_bytes": file_bytes,
                "file_name": f"result_{timestamp}.docx",
            }

    except Exception as e:
        logger.error("파일 생성 실패 (%s): %s", output_format, e)

    return None
