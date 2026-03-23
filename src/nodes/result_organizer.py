"""결과 정리 노드.

쿼리 결과를 사용자 요구에 맞게 정리하고 데이터 충분성을 판단한다.
Phase 1에서는 기본 정리와 텍스트 응답용 가공을 수행하고,
Phase 2에서 양식 매핑 기능을 추가한다.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel

from src.config import AppConfig, load_config
from src.llm import create_llm
from src.security.data_masker import DataMasker
from src.state import AgentState, OrganizedData, SheetMappingResult

logger = logging.getLogger(__name__)


async def result_organizer(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """쿼리 결과를 정리하고 데이터 충분성을 판단한다.

    처리 단계:
    1. 민감 데이터 마스킹
    2. 데이터 충분성 판단
    3. 숫자 포맷팅 (단위 부여)
    4. 양식 매핑 (Phase 2, template_structure가 있을 때)
    5. 요약 생성

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - organized_data: 정리된 데이터
        - error_message: 데이터 부족 시 메시지, 정상 시 None
        - current_node: "result_organizer"
    """
    if app_config is None:
        app_config = load_config()
    query_results = state["query_results"]
    parsed = state["parsed_requirements"]
    template = state.get("template_structure")

    # 1. 민감 데이터 마스킹
    masker = DataMasker(app_config.security)
    masked_results = masker.mask_rows(query_results)

    # 2. 데이터 충분성 판단
    state_column_mapping = state.get("column_mapping")
    is_sufficient = _check_data_sufficiency(
        masked_results, parsed, template, column_mapping=state_column_mapping
    )

    if not is_sufficient and state.get("retry_count", 0) < 3:
        logger.info("데이터 부족으로 재시도 요청")
        return {
            "organized_data": OrganizedData(
                summary="데이터가 부족합니다.",
                rows=masked_results,
                column_mapping=None,
                is_sufficient=False,
            ),
            "error_message": "data_insufficient",
            "current_node": "result_organizer",
        }

    # 3. 숫자 포맷팅 (텍스트 출력 시만)
    output_format = parsed.get("output_format", "text")
    if output_format == "text":
        formatted_results = _format_numbers(masked_results, parsed)
    else:
        formatted_results = masked_results

    # 4. 양식 매핑
    # field_mapper 노드에서 이미 column_mapping이 생성된 경우 그대로 사용
    # (Single Source of Truth 원칙)
    column_mapping: Optional[dict[str, str]] = state.get("column_mapping")
    sheet_mappings: Optional[list[SheetMappingResult]] = None

    if template and output_format in ("xlsx", "docx"):
        if column_mapping:
            # field_mapper에서 이미 매핑 완료 - 중복 LLM 호출 없음
            logger.info("field_mapper 매핑 사용 (LLM 재호출 스킵)")
        else:
            # field_mapper가 스킵된 경우 (레거시 폴백)
            target_sheets = state.get("target_sheets")
            has_multiple_sheets = len(template.get("sheets", [])) > 1

            if output_format == "xlsx" and has_multiple_sheets:
                sheet_mappings = await _perform_per_sheet_field_mapping(
                    llm=llm,
                    app_config=app_config,
                    template=template,
                    schema_info=state.get("schema_info", {}),
                    target_sheets=target_sheets,
                    rows=formatted_results,
                )
                if sheet_mappings:
                    column_mapping = sheet_mappings[0].get("column_mapping")
            else:
                column_mapping = await _perform_field_mapping(
                    llm=llm,
                    app_config=app_config,
                    template=template,
                    schema_info=state.get("schema_info", {}),
                )

    # 5. 요약 생성
    summary = _generate_summary(formatted_results, parsed)

    logger.info(f"결과 정리 완료: {len(formatted_results)}건")

    return {
        "organized_data": OrganizedData(
            summary=summary,
            rows=formatted_results,
            column_mapping=column_mapping,
            is_sufficient=True,
            sheet_mappings=sheet_mappings,
        ),
        "error_message": None,
        "current_node": "result_organizer",
    }


def _check_data_sufficiency(
    results: list[dict[str, Any]],
    parsed: dict,
    template: Optional[dict],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
) -> bool:
    """결과 데이터의 충분성을 판단한다.

    column_mapping이 제공되면 매핑된 컬럼 기준으로 충분성을 판단한다.

    Args:
        results: 쿼리 결과
        parsed: 파싱된 요구사항
        template: 양식 구조 (있을 때)
        column_mapping: 필드-컬럼 매핑 (field_mapper 결과, 선택)

    Returns:
        데이터가 충분하면 True
    """
    # 결과가 0건이면 부족하지 않음 (empty는 정상 응답)
    if len(results) == 0:
        return True  # "해당 데이터 없음"으로 응답

    # column_mapping 기반 충분성 검사 (개선된 방식)
    if column_mapping and results:
        result_keys = set(results[0].keys())
        mapped_columns = [v for v in column_mapping.values() if v is not None]
        if not mapped_columns:
            return True

        matched = 0
        for mc in mapped_columns:
            if mc in result_keys:
                matched += 1
            elif "." in mc and mc.split(".", 1)[-1] in result_keys:
                matched += 1

        return matched >= len(mapped_columns) * 0.5

    # 레거시: 양식 헤더 대비 컬럼 수 비교
    if template:
        sheets = template.get("sheets", [{}])
        required_headers = sheets[0].get("headers", []) if sheets else []
        if required_headers:
            available_cols = set(results[0].keys()) if results else set()
            if len(available_cols) < len(required_headers) * 0.5:
                return False

    return True


def _format_numbers(
    results: list[dict[str, Any]],
    parsed: dict,
) -> list[dict[str, Any]]:
    """숫자 데이터에 적절한 포맷을 적용한다.

    예: usage_pct -> "85.2%", total_gb -> "128 GB"

    Args:
        results: 원본 결과
        parsed: 파싱된 요구사항

    Returns:
        포맷팅된 결과
    """
    # 컬럼명 기반 단위 추론 규칙
    unit_rules: dict[str, str] = {
        "pct": "%",
        "percent": "%",
        "usage_rate": "%",
        "_gb": " GB",
        "_mb": " MB",
        "_kb": " KB",
        "_bytes": " bytes",
        "mbps": " Mbps",
        "gbps": " Gbps",
    }

    formatted: list[dict[str, Any]] = []
    for row in results:
        new_row: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, (int, float)):
                unit = _get_unit_for_column(key, unit_rules)
                if unit == "%":
                    new_row[key] = f"{value:.1f}{unit}"
                elif unit:
                    new_row[key] = f"{value:,.1f}{unit}"
                else:
                    new_row[key] = value
            else:
                new_row[key] = value
        formatted.append(new_row)

    return formatted


def _get_unit_for_column(
    column_name: str,
    rules: dict[str, str],
) -> str:
    """컬럼명에서 단위를 추론한다.

    Args:
        column_name: 컬럼명
        rules: 패턴 -> 단위 매핑 규칙

    Returns:
        추론된 단위 문자열 (없으면 빈 문자열)
    """
    lower = column_name.lower()
    for pattern, unit in rules.items():
        if pattern in lower:
            return unit
    return ""


def _generate_summary(
    results: list[dict[str, Any]],
    parsed: dict,
) -> str:
    """결과 데이터의 요약을 생성한다.

    Args:
        results: 포맷팅된 결과
        parsed: 파싱된 요구사항

    Returns:
        요약 문자열
    """
    if not results:
        return "조건에 해당하는 데이터가 없습니다."

    targets = ", ".join(parsed.get("query_targets", []))
    return f"총 {len(results)}건의 데이터를 조회했습니다. 조회 대상: {targets}."


async def _perform_field_mapping(
    llm: BaseChatModel | None,
    app_config: AppConfig | None,
    template: dict,
    schema_info: dict,
) -> Optional[dict[str, str]]:
    """LLM을 사용하여 양식 필드와 DB 컬럼 간의 매핑을 수행한다.

    Args:
        llm: LLM 인스턴스 (없으면 내부 생성)
        app_config: 앱 설정
        template: 양식 구조 정보
        schema_info: DB 스키마 정보

    Returns:
        매핑 딕셔너리 또는 None (매핑 실패 시)
    """
    try:
        from src.document.field_mapper import map_fields
    except ImportError:
        logger.warning("field_mapper 모듈을 찾을 수 없습니다.")
        return None

    if not schema_info or not schema_info.get("tables"):
        logger.warning("스키마 정보가 없어 필드 매핑을 수행할 수 없습니다.")
        return None

    if llm is None:
        if app_config is None:
            app_config = load_config()
        llm = create_llm(app_config)

    try:
        mapping = await map_fields(llm, template, schema_info)
        if mapping:
            logger.info("필드 매핑 완료: %s", mapping)
            return mapping
    except Exception as e:
        logger.error("필드 매핑 실패: %s", e)

    return None


async def _perform_per_sheet_field_mapping(
    llm: BaseChatModel | None,
    app_config: AppConfig | None,
    template: dict,
    schema_info: dict,
    target_sheets: list[str] | None,
    rows: list[dict[str, Any]],
) -> Optional[list[SheetMappingResult]]:
    """시트별로 독립적인 필드 매핑을 수행한다.

    Args:
        llm: LLM 인스턴스 (없으면 내부 생성)
        app_config: 앱 설정
        template: 양식 구조 정보
        schema_info: DB 스키마 정보
        target_sheets: 대상 시트명 목록 (None이면 전체)
        rows: 조회 결과 행 목록

    Returns:
        시트별 매핑 결과 목록 또는 None
    """
    try:
        from src.document.field_mapper import map_fields_per_sheet
    except ImportError:
        logger.warning("field_mapper 모듈의 map_fields_per_sheet를 찾을 수 없습니다.")
        return None

    if not schema_info or not schema_info.get("tables"):
        logger.warning("스키마 정보가 없어 시트별 필드 매핑을 수행할 수 없습니다.")
        return None

    if llm is None:
        if app_config is None:
            app_config = load_config()
        llm = create_llm(app_config)

    try:
        sheet_mapping_dict = await map_fields_per_sheet(
            llm, template, schema_info, target_sheets
        )
        if not sheet_mapping_dict:
            return None

        results: list[SheetMappingResult] = []
        for sheet_name, col_mapping in sheet_mapping_dict.items():
            results.append(SheetMappingResult(
                sheet_name=sheet_name,
                column_mapping=col_mapping,
                rows=rows,
            ))

        logger.info("시트별 필드 매핑 완료: %d개 시트", len(results))
        return results

    except Exception as e:
        logger.error("시트별 필드 매핑 실패: %s", e)

    return None
