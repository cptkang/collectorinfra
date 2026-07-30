"""출력 생성 노드.

최종 자연어 응답 또는 문서 파일을 생성한다.
Phase 1에서는 자연어 응답만 지원하고, Phase 2에서 Excel/Word 생성을 추가한다.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.prompts.output_generator import OUTPUT_GENERATOR_SYSTEM_PROMPT
from src.state import AgentState

logger = logging.getLogger(__name__)


async def output_generator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
    stream_user_response: bool = True,
) -> dict:
    """최종 응답을 생성하고 직접 경로에서는 답변을 대화 이력에 누적한다.

    실제 생성은 `_run_output_generator`가 담당하고, 본 래퍼는 반환에 `messages`
    (AIMessage 1건)를 덧붙인다(②, 멀티턴 후속 판단용). orchestration 경로에서는
    result_aggregator._finalize_task가 이 함수를 호출하되 반환 messages를 사용하지 않고
    자체적으로 top-level 누적하므로 이중 누적이 없다(단일 append 원칙).

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)
        stream_user_response: 자연어 응답 LLM 호출에 USER_RESPONSE_TAG를 부여해
            토큰 단위 SSE 스트리밍(D-009)을 활성화할지 여부.

    Returns:
        _run_output_generator 반환 dict + (final_response가 비지 않으면) messages.
    """
    result = await _run_output_generator(
        state, llm=llm, app_config=app_config, stream_user_response=stream_user_response
    )
    text = result.get("final_response")
    if text and text.strip():
        result = {**result, "messages": [AIMessage(content=text)]}
    return result


async def _run_output_generator(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
    stream_user_response: bool = True,
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
        stream_user_response: 자연어 응답 LLM 호출에 USER_RESPONSE_TAG를 부여해
            토큰 단위 SSE 스트리밍(D-009)을 활성화할지 여부. 딥 에이전트 경로에서
            여러 하위 결과를 별도 LLM으로 단일 합성하는 경우(D-062), 중간 per-task
            토큰이 새지 않도록 False로 호출하고 최종 합성에만 태그를 부여한다.

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
        response = await _generate_text_response(
            app_config, state, llm=llm, stream_user_response=stream_user_response
        )
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
        if file_result and file_result.get("file_bytes"):
            text_response = await _generate_text_response(
                app_config, state, llm=llm, stream_user_response=stream_user_response
            )
            text_response = _append_inferred_mapping_info(text_response, state)
            # 폼필 기준월 명시(§2.4) + 미작성 항목 사유(D-114) — 감사자료 오기재·침묵 공란 방지.
            # 판정은 매핑 유무가 아니라 writer의 실제 채움 통계(fill_stats) 기반(라이브 실측 교정).
            text_response = _append_form_fill_notes(
                text_response, state, fill_stats=file_result.get("fill_stats")
            )

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
            # 파일 생성 실패: 사유를 사용자에게 노출하고 텍스트/CSV로 폴백 (D-059 — 침묵적 강등 금지)
            reason = (file_result or {}).get("reason") or "알 수 없는 이유로 양식을 채우지 못했습니다."
            logger.warning("파일 생성 실패, 텍스트 응답으로 대체 — 사유: %s", reason)
            text_response = await _generate_text_response(
                app_config, state, llm=llm, stream_user_response=stream_user_response
            )
            text_response = _append_inferred_mapping_info(text_response, state)
            return {
                "final_response": (
                    f"업로드하신 {output_format.upper()} 양식을 채우지 못했습니다.\n"
                    f"사유: {reason}\n"
                    f"아래 원본 조회 데이터(CSV 다운로드)로 결과를 확인하실 수 있습니다.\n\n"
                    f"{text_response}"
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
    stream_user_response: bool = True,
) -> str:
    """LLM을 사용하여 자연어 응답을 생성한다.

    Args:
        config: 앱 설정
        state: 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        stream_user_response: USER_RESPONSE_TAG 부여 여부 (D-062 합성 시 False).

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
    )

    messages: list[BaseMessage] = [
        SystemMessage(content=OUTPUT_GENERATOR_SYSTEM_PROMPT)
    ]
    if type(llm) is KBGenAIChat:
        messages.append(AIMessage(content=""))
    messages.append(HumanMessage(content=user_prompt))

    # 토큰 단위 SSE 스트리밍(D-009)을 위해 .astream()으로 호출하고,
    # 최종 사용자 응답임을 USER_RESPONSE_TAG로 표시한다. 단, 딥 에이전트 단일
    # 합성(D-062)에서는 중간 per-task 토큰이 새지 않도록 태그를 생략한다.
    tags = [USER_RESPONSE_TAG] if stream_user_response else None
    return await astream_text(llm, messages, tags=tags)


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
) -> str:
    """응답 생성 프롬프트를 구성한다.

    Args:
        original_query: 원본 사용자 질의
        summary: 데이터 요약
        rows: 결과 데이터 행

    Returns:
        구성된 프롬프트 문자열
    """
    # 결과가 많으면 상위 20건만 프롬프트에 포함.
    # 복합 필드명(그룹|서브, D-112)의 '|'는 Markdown 표 구분자와 충돌해 응답 표가
    # 깨진다(라이브 실측: 칼럼 분해·순서 뒤죽박죽) — 표시용 키로 결정적 치환.
    display_rows = [
        {_display_field_name(str(k)): v for k, v in r.items()} if isinstance(r, dict) else r
        for r in rows[:20]
    ]
    truncated = len(rows) > 20

    parts = [
        f"## 사용자 질의\n{original_query}",
        f"## 데이터 요약\n{summary}",
        (
            f"## 조회 결과 ({len(rows)}건"
            f"{', 상위 20건 표시' if truncated else ''})\n"
            f"```json\n"
            f"{json.dumps(display_rows, ensure_ascii=False, indent=2)}\n"
            f"```"
        ),
    ]

    # 표에 모든 컬럼을 빠짐없이 포함하도록 컬럼 목록을 명시한다(D-100) — LLM이 질의 문구에
    # 이끌려 일부 컬럼만 표시하는 것을 방지(실측: "제조사와 일련번호" 질의에서 서버명·알람명 누락).
    columns = list(display_rows[0].keys()) if display_rows and isinstance(display_rows[0], dict) else []
    if columns:
        parts.append(
            "## 표시 규칙\n"
            f"아래 {len(columns)}개 컬럼을 **모두** 표에 포함하세요(하나도 생략 금지): "
            + ", ".join(columns)
        )

    return "\n\n".join(parts)


def _format_ym(yyyymm: str) -> str:
    """YYYYMM을 'YYYY년 M월'로 표기한다."""
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        return yyyymm
    return f"{yyyymm[:4]}년 {int(yyyymm[4:6])}월"


def _append_form_fill_notes(
    response: str,
    state: AgentState,
    fill_stats: dict[str, int] | None = None,
) -> str:
    """폼필 응답에 기준월 매핑(§2.4)과 미작성 항목 사유(D-114)를 덧붙인다.

    - 기준월: 월 시리즈(M~M+5) 양식은 M이 어느 달인지 양식에 없으므로 실제 사용 월을
      응답에 반드시 명시한다(감사자료 오기재 방지 — plans/67 R4). 양식 원본(비고 열 등)에는
      기재하지 않는다(Q3 확정).
    - 미작성 사유: **writer의 실제 채움 통계(fill_stats)** 기준으로 0건 채워진 칼럼만
      나열한다(침묵 공란 금지 — D-059의 필드 단위 확장). 라이브 실측(2026-07-28): 매핑
      None이어도 행 키=필드명 폴백으로 채워지는 칼럼이 있어 매핑 기준 판정은 오보를 냈다.
      fill_stats가 없으면(docx 등) 종전 매핑 기준으로 폴백한다.
    - 월 시리즈 필드가 전부 0건이면 사유 목록 대신 "생성 SQL 확인" 안내를 낸다
      (D-050 — null/공란은 데이터 부재가 아니라 SQL 문제일 수 있음).
    """
    anchor = state.get("form_month_anchor") or {}
    month_fields = set(anchor.get("fields") or [])

    parts: list[str] = []
    if anchor.get("start") and anchor.get("end"):
        parts.append(
            "**[기준월 안내]** 월별 사용률 칼럼은 "
            f"{_format_ym(anchor['start'])}부터 {_format_ym(anchor['end'])}까지 "
            "(M=첫 달, 별도 지정이 없으면 실행일 기준 지난달이 마지막 칼럼) 데이터로 채웠습니다. "
            "다른 기준월이 필요하면 \"기준월 3월\"처럼 지정해 다시 요청해주세요."
        )

    if fill_stats:
        unfilled = [
            f for f, cnt in fill_stats.items()
            if cnt == 0 and f not in month_fields
        ]
        observed_month = [f for f in month_fields if f in fill_stats]
        if observed_month and all(fill_stats[f] == 0 for f in observed_month):
            parts.append(
                "**[확인 필요]** 월별 사용률 칼럼이 전부 채워지지 않았습니다. 데이터 부재가 "
                "아니라 조회 SQL 문제일 수 있으니 처리현황의 생성 SQL을 확인해주세요."
            )
    else:
        column_mapping = state.get("column_mapping") or {}
        unfilled = [
            f for f, c in column_mapping.items()
            if c is None and f not in month_fields
        ]
    if unfilled:
        shown = "\n".join(f"  - {_display_field_name(f)}" for f in unfilled)
        parts.append(
            "**[미작성 항목]** 다음 칼럼은 수집 데이터에 해당 항목이 없어 "
            f"비워두었습니다(임의 기재 금지):\n{shown}"
        )

    if not parts:
        return response
    return response + "\n\n---\n" + "\n\n".join(parts)


def _display_field_name(field: str) -> str:
    """복합 필드명(그룹|서브)을 사용자 표시용 'A > B'로 변환한다(내부 키는 '|' 유지)."""
    return field.replace("|", " > ")


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
        성공 시 {"file_bytes": bytes, "file_name": str, "total_filled": int},
        실패 시 {"reason": str} (D-059 — 사유를 호출부가 사용자에게 노출한다).
        성공 여부는 "file_bytes" 키 존재로 판별한다.
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
        missing = []
        if not template:
            missing.append("template_structure(양식 구조)")
        if not uploaded_file:
            missing.append("uploaded_file(원본 파일 바이너리)")
        missing_str = ", ".join(missing)
        logger.warning("양식 파일 생성 불가 — state 누락: %s", missing_str)
        return {"reason": (
            f"양식을 채우는 데 필요한 정보가 state에서 누락되었습니다: {missing_str}. "
            "(오케스트레이션 경로의 상태 전파 누락 가능성 — 원본 파일이면 D-053 계열)"
        )}

    if not effective_mapping:
        logger.warning("컬럼 매핑이 없어 파일 생성 불가")
        return {"reason": (
            "양식 헤더와 조회 데이터 간 매핑을 만들지 못해 양식을 채울 수 없습니다 "
            "(조회 데이터 부족 또는 서버 식별 컬럼 누락일 수 있습니다)."
        )}

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

            fill_stats: dict[str, int] = {}
            file_bytes, total_filled = fill_excel_template(
                file_data=uploaded_file,
                template_structure=template,
                column_mapping=effective_mapping,
                rows=rows,
                sheet_mappings=sheet_mappings,
                target_sheets=target_sheets,
                fill_stats=fill_stats,
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
                "fill_stats": fill_stats,
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
        return {"reason": f"양식 채우기 중 오류가 발생했습니다: {e}"}

    return {"reason": "알 수 없는 이유로 양식을 생성하지 못했습니다."}
