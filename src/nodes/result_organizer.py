"""결과 정리 노드.

쿼리 결과를 사용자 요구에 맞게 정리하고 데이터 충분성을 판단한다.
Phase 1에서는 기본 정리와 텍스트 응답용 가공을 수행하고,
Phase 2에서 양식 매핑 기능을 추가한다.
"""

from __future__ import annotations

import json as json_module
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
    is_sufficient = await _check_data_sufficiency(
        masked_results,
        parsed,
        template,
        column_mapping=state_column_mapping,
        llm=llm,
        app_config=app_config,
    )

    if not is_sufficient and state.get("retry_count", 0) < 3:
        logger.info("데이터 부족으로 재시도 요청")
        return {
            "organized_data": OrganizedData(
                summary="데이터가 부족합니다.",
                rows=masked_results,
                column_mapping=None,
                resolved_mapping=None,
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

    # 4.5 resolved_mapping 생성 (3계층 하이브리드 Layer 1 + Layer 2)
    resolved_mapping: Optional[dict[str, str]] = None
    if column_mapping and formatted_results:
        result_keys = set(formatted_results[0].keys())

        # Layer 1: 규칙 기반 매칭
        from src.utils.column_matcher import build_resolved_mapping
        resolved_mapping, unresolved_fields = build_resolved_mapping(
            column_mapping, result_keys,
        )
        logger.info(
            "Layer 1 규칙 기반 resolved_mapping: %d건 해석, %d건 미해결",
            len(resolved_mapping) - len(unresolved_fields),
            len(unresolved_fields),
        )

        # Layer 2: 미해결 항목에 대해 LLM 유사성 판단
        if unresolved_fields:
            llm_resolved = await _resolve_unmatched_via_llm(
                llm=llm,
                column_mapping=column_mapping,
                unresolved_fields=unresolved_fields,
                result_keys=result_keys,
                app_config=app_config,
            )
            if llm_resolved:
                for field, resolved_key in llm_resolved.items():
                    resolved_mapping[field] = resolved_key
                logger.info(
                    "Layer 2 LLM 유사성 판단으로 %d건 추가 해석: %s",
                    len(llm_resolved), llm_resolved,
                )

    # 5. 요약 생성
    summary = _generate_summary(formatted_results, parsed)

    logger.info(f"결과 정리 완료: {len(formatted_results)}건")

    return {
        "organized_data": OrganizedData(
            summary=summary,
            rows=formatted_results,
            column_mapping=column_mapping,
            resolved_mapping=resolved_mapping,
            is_sufficient=True,
            sheet_mappings=sheet_mappings,
        ),
        "error_message": None,
        "current_node": "result_organizer",
    }


async def _resolve_unmatched_via_llm(
    llm: BaseChatModel | None,
    column_mapping: dict[str, str | None],
    unresolved_fields: list[str],
    result_keys: set[str],
    app_config: AppConfig | None = None,
) -> dict[str, str] | None:
    """미해결 매핑 항목에 대해 LLM 유사성 판단을 수행한다.

    Args:
        llm: LLM 인스턴스
        column_mapping: 원본 column_mapping
        unresolved_fields: 규칙 기반 매칭 실패 필드명 목록
        result_keys: SQL 결과의 실제 키 집합
        app_config: 앱 설정 (LLM 생성용)

    Returns:
        {field: resolved_result_key} 또는 None (실패/불필요 시)
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    from src.prompts.column_resolver import (
        COLUMN_RESOLVER_SYSTEM_PROMPT,
        COLUMN_RESOLVER_USER_PROMPT,
    )

    # 미해결 필드의 매핑값만 추출
    unresolved_columns: dict[str, str] = {}
    for f in unresolved_fields:
        val = column_mapping.get(f)
        if val is not None:
            unresolved_columns[f] = val

    if not unresolved_columns:
        return None

    # LLM 인스턴스 확보
    if llm is None:
        try:
            if app_config is None:
                app_config = load_config()
            llm = create_llm(app_config)
        except Exception as e:
            logger.warning("Layer 2 LLM 생성 실패, 스킵: %s", e)
            return None

    # 프롬프트 구성
    user_prompt = COLUMN_RESOLVER_USER_PROMPT.format(
        unresolved_columns=json_module.dumps(
            {f: v for f, v in unresolved_columns.items()},
            ensure_ascii=False,
        ),
        result_keys=json_module.dumps(sorted(result_keys), ensure_ascii=False),
    )

    try:
        messages = [
            SystemMessage(content=COLUMN_RESOLVER_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]
        response = await llm.ainvoke(messages)
        content = response.content.strip()

        # JSON 블록 추출
        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()

        llm_mapping: dict[str, str] = json_module.loads(content)
        if not isinstance(llm_mapping, dict):
            logger.warning("Layer 2 LLM 응답이 dict가 아님: %s", type(llm_mapping))
            return None

        # LLM 응답 검증: 매핑값 -> 결과 키 매핑을 field -> 결과 키로 변환
        resolved: dict[str, str] = {}
        for field, db_col_val in unresolved_columns.items():
            matched_key = llm_mapping.get(db_col_val)
            if matched_key and matched_key in result_keys:
                resolved[field] = matched_key

        return resolved if resolved else None

    except Exception as e:
        logger.warning("Layer 2 LLM 유사성 판단 실패 (graceful 스킵): %s", e)
        return None


async def _check_data_sufficiency(
    results: list[dict[str, Any]],
    parsed: dict,
    template: Optional[dict],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    llm: BaseChatModel | None = None,
    app_config: Optional[AppConfig] = None,
) -> bool:
    """결과 데이터의 충분성을 LLM 기반으로 판단한다.

    column_mapping이 제공되면 LLM을 사용해 매핑된 컬럼이
    쿼리 결과 키에 의미적으로 존재하는지 판단한다.

    Args:
        results: 쿼리 결과
        parsed: 파싱된 요구사항
        template: 양식 구조 (있을 때)
        column_mapping: 필드-컬럼 매핑 (field_mapper 결과, 선택)
        llm: LLM 인스턴스 (없으면 내부 생성)
        app_config: 앱 설정 (임계값 참조, 없으면 내부 로드)

    Returns:
        데이터가 충분하면 True
    """
    # --- Case 1: 빈 결과 ---
    if not results:
        if parsed.get("aggregation"):
            return False
        return True

    result_keys = set(results[0].keys())

    # --- Case 2: column_mapping 기반 (Excel/문서 모드) — LLM 매칭 ---
    if column_mapping:
        mapped_cols = [col for col in column_mapping.values() if col is not None]
        if not mapped_cols:
            return True

        if app_config is None:
            app_config = load_config()
        threshold = app_config.query.sufficiency_required_threshold

        matched_count = await _llm_check_column_coverage(
            llm, mapped_cols, result_keys, app_config,
        )

        if matched_count < len(mapped_cols) * threshold:
            logger.warning(
                "LLM 판단 매핑 컬럼 부족: %d/%d (기준 %.0f%%)",
                matched_count, len(mapped_cols), threshold * 100,
            )
            return False

        return True

    # --- Case 3: 레거시 template 기반 ---
    if template:
        sheets = template.get("sheets", [{}])
        required_headers = sheets[0].get("headers", []) if sheets else []
        if required_headers:
            result_keys_lower = {k.lower() for k in result_keys}
            matched = sum(1 for h in required_headers if h.lower() in result_keys_lower)
            if matched < len(required_headers) * 0.5:
                return False

    # --- Case 4: text 모드 ---
    if not template and not column_mapping:
        if len(result_keys) == 0:
            return False

    return True


async def _llm_check_column_coverage(
    llm: BaseChatModel | None,
    mapped_cols: list[str],
    result_keys: set[str],
    app_config: Optional[AppConfig] = None,
) -> int:
    """LLM을 사용하여 매핑된 컬럼이 결과 키에 포함되는 수를 판단한다.

    Args:
        llm: LLM 인스턴스 (없으면 내부 생성)
        mapped_cols: 매핑된 DB 컬럼 목록
        result_keys: SQL 결과의 실제 키 집합
        app_config: 앱 설정

    Returns:
        매칭된 컬럼 수 (LLM 호출 실패 시 전체 수를 반환하여 충분으로 간주)
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    if llm is None:
        try:
            if app_config is None:
                app_config = load_config()
            llm = create_llm(app_config)
        except Exception as e:
            logger.warning("LLM 생성 실패, 충분성 검사 스킵: %s", e)
            return len(mapped_cols)

    system_prompt = (
        "당신은 DB 컬럼명 매칭 전문가입니다.\n"
        "매핑된 컬럼이 SQL 결과 키에 존재하는지 판단하세요.\n"
        "이름이 다르더라도 의미적으로 동일하면 매칭입니다.\n"
        "(예: table.column ↔ table_column, EAV:OSType ↔ os_type, CamelCase ↔ snake_case)\n\n"
        "매칭된 매핑 컬럼만 JSON 배열로 출력하세요. 다른 설명은 불필요합니다.\n"
        '```json\n["matched_col1", "matched_col2"]\n```'
    )
    user_prompt = (
        f"## 매핑된 컬럼\n{json_module.dumps(mapped_cols, ensure_ascii=False)}\n\n"
        f"## SQL 결과 키\n{json_module.dumps(sorted(result_keys), ensure_ascii=False)}"
    )

    try:
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
        response = await llm.ainvoke(messages)
        content = response.content.strip()

        if "```json" in content:
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in content:
            content = content.split("```", 1)[1].split("```", 1)[0].strip()

        matched: list = json_module.loads(content)
        if isinstance(matched, list):
            count = len(matched)
            logger.info(
                "LLM 컬럼 커버리지 판단: %d/%d 매칭", count, len(mapped_cols),
            )
            return count

    except Exception as e:
        logger.warning("LLM 컬럼 커버리지 판단 실패, 충분으로 간주: %s", e)

    return len(mapped_cols)


def _format_numbers(
    results: list[dict[str, Any]],
    parsed: dict,  # noqa: ARG001 - 향후 요구사항 기반 포맷팅 확장용
) -> list[dict[str, Any]]:
    """숫자 데이터에 적절한 포맷을 적용한다.

    예: usage_pct -> "85.2%", total_gb -> "128 GB"

    Args:
        results: 원본 결과
        parsed: 파싱된 요구사항 (향후 확장용)

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
                resolved_mapping=None,
                rows=rows,
            ))

        logger.info("시트별 필드 매핑 완료: %d개 시트", len(results))
        return results

    except Exception as e:
        logger.error("시트별 필드 매핑 실패: %s", e)

    return None
