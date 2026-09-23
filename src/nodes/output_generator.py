"""출력 생성 노드.

최종 자연어 응답 또는 문서 파일을 생성한다.
Phase 1에서는 자연어 응답만 지원하고, Phase 2에서 Excel/Word 생성을 추가한다.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

from src.clients.fabrix_kbgenai import KBGenAIChat
from src.config import AppConfig, load_config
from src.domain.empty_answer import from_payload as diagnosis_from_payload
from src.domain.empty_answer import render_diagnosis
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.nodes.intent_frame_builder import CONSUMER_OUTPUT_GENERATOR, get_prompt_query
from src.prompts.output_generator import OUTPUT_GENERATOR_SYSTEM_PROMPT
from src.routing.domain_config import get_domain_by_id
from src.schema_cache.form_memory import save_form_memory_entries
from src.state import AgentState
from src.utils.prior_dependency import ADMIN_ASSET_NOTE_KINDS, CROSS_SYSTEM_NOTE_KINDS
from src.utils.query_gen_common import FORM_MEMORY_SHORTCUT_HINT, resolve_stat_month_range

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

    # 양식 없이 파일 형식만 요청("…을 엑셀로 만들어줘", plans/116 §10.3). 파일 산출은 첨부 양식을
    # 채우는 방식뿐이라 만들 파일이 없다 — 첨부하지 않은 양식을 "채우지 못했다"고 하지 않고
    # 실제 동작(텍스트 응답 + CSV)을 알린다. 첨부했는데 양식이 전파되지 않은 경우(file_type 있음)는
    # 아래 파일 분기가 D-059 사유를 그대로 노출한다.
    no_template_notice: str | None = None
    if (
        output_format in ("xlsx", "docx")
        and not state.get("template_structure")
        and not state.get("uploaded_file")
        and not state.get("file_type")
    ):
        no_template_notice = _no_template_file_notice(
            output_format, bool((organized or {}).get("rows"))
        )
        logger.info("양식 없는 %s 요청 — 파일 없이 텍스트 응답으로 처리", output_format)
        output_format = "text"
        parsed = {**parsed, "output_format": "text"}
        state = {**state, "parsed_requirements": parsed}

    if output_format == "text":
        response = await _generate_text_response(
            app_config, state, llm=llm, stream_user_response=stream_user_response
        )
        response = _append_inferred_mapping_info(response, state)
        response = _append_spike_notes(response, state)
        response = _append_scope_note(response, state)
        response = _append_zone_coverage_notes(response, state)
        response = _append_current_month_partial_note(response, state)
        response = _append_unavailable_metric_notes(response, state)
        response = _append_limit_truncation_note(response, state)
        response = append_structure_missing_note(response, state)
        response = _append_cross_system_notes(response, state)
        response = _prepend_alarm_headline(response, state, app_config)
        if no_template_notice:
            response = f"{no_template_notice}\n\n{response}"
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
            text_response = _append_spike_notes(text_response, state)
            text_response = _append_scope_note(text_response, state)
            text_response = _append_zone_coverage_notes(text_response, state)
            text_response = _append_unavailable_metric_notes(text_response, state)
            text_response = append_structure_missing_note(text_response, state)
            text_response = _append_cross_system_notes(text_response, state)
            # 폼필 기준월 명시(§2.4) + 미작성 항목 사유(D-147) — 감사자료 오기재·침묵 공란 방지.
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

            result: dict = {
                "final_response": text_response,
                "output_file": file_result["file_bytes"],
                "output_file_name": file_result["file_name"],
                "current_node": "output_generator",
                "error_message": None,
            }
            # 확인 이력 저장(D-151 Phase 3) — 옵트인(기억 체크) + 검증 통과 + 이번 턴
            # 답변(origin=answer)만. LLM 산출물·이력 재적용분은 저장하지 않는다(C3).
            if state.get("form_fill_remember"):
                _remember = {
                    f: {"action": o.get("action"), "value": o.get("value")}
                    for f, o in (state.get("form_fill_overrides") or {}).items()
                    if o.get("applied") and o.get("origin") != "memory"
                }
                if _remember:
                    _saved = await save_form_memory_entries(
                        state.get("template_structure"), _remember,
                        state.get("parsed_requirements", {}).get("original_query", "")
                        or state.get("user_query", ""),
                        app_config,
                    )
                    _ttl_days = getattr(app_config.query, "form_memory_ttl_days", 0)
                    if _saved:
                        result["final_response"] += (
                            f"\n\n**[기억 저장]** '{_saved}'에 {len(_remember)}개 항목을 "
                            f"기억했습니다 (유효 {_ttl_days}일, 사용할 때마다 연장). "
                            "다음부터 이 양식에 자동 반영됩니다."
                        )
                    else:
                        # 침묵 강등 금지 — 저장 실패(기능 OFF·Redis 불가)를 명시
                        result["final_response"] += (
                            "\n\n**[기억 저장 안 됨]** 저장소를 사용할 수 없거나 기억 기능이 "
                            "꺼져 있어(form_memory_ttl_days=0) 이번 답변은 이번 실행에만 "
                            "적용되었습니다."
                        )

            # HITL 폼필(D-151): 미해결 필드 역질문 페이로드 + 대기 상태.
            # 미해결 0이면 pending=None으로 자기정리(답변 적용 완료 턴 포함).
            if state.get("template_structure"):
                clarification, pending = _build_form_fill_hitl(
                    state, file_result.get("fill_stats")
                )
                result["pending_form_fill"] = pending
                if clarification:
                    result["form_fill_clarification"] = clarification
                    logger.info(
                        "폼필 역질문 발행(D-151): 미해결 %d건 — %s",
                        len(clarification["fields"]),
                        [f["name"] for f in clarification["fields"]][:10],
                    )
            return result
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


def _no_template_file_notice(output_format: str, has_rows: bool) -> str:
    """양식 없이 Excel/Word 파일을 요청했을 때의 안내 (plans/116 §10.3).

    파일 산출은 첨부 양식을 채우는 방식만 지원한다(요구사항 Phase 2 「양식 기반 문서 생성」).
    CSV 안내는 내려받을 행이 있을 때만 붙인다(행이 없으면 CSV 다운로드가 404다).
    """
    kind = "Excel" if output_format == "xlsx" else "Word"
    text = (
        f"양식 파일이 첨부되지 않아 {kind} 파일은 만들지 않았습니다. "
        "파일은 첨부한 양식(Excel/Word)을 채우는 방식으로만 만들 수 있습니다."
    )
    if has_rows:
        text += " 아래 조회 결과는 「CSV 다운로드」로 내려받아 Excel에서 열 수 있습니다."
    return text


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
        return _generate_empty_result_response(parsed, state.get("empty_diagnosis"))

    # 전 행 null 강등(C-06): 값 칼럼이 전부 전 행 null이면 의미 없는 목록 표 대신 결정적
    # 안내로 응답한다(LLM 미호출). CSV 산출 원본(query_results)은 건드리지 않는다.
    # text 전용 — 폼필(xlsx/docx) 동반 텍스트는 의도적 공란(H-06 도메인 밖 열)이 있어 제외.
    if parsed.get("output_format", "text") == "text":
        all_null_cols = _all_null_value_columns(organized["rows"])
        if all_null_cols is not None:
            return _generate_all_null_response(all_null_cols, organized["rows"], state)

    if llm is None:
        # 최종 사용자 응답만 answer 프로파일(D-194). D-062 중간 합성
        # (stream_user_response=False)은 최종 합성의 재료이므로 결정적 프로파일을 유지한다.
        llm = create_llm(
            config, purpose="answer" if stream_user_response else "deterministic"
        )

    # 기준 정보(D-186): 프롬프트에 연도가 전혀 없어("1월~6월" 질의 + M/M+1 칼럼) LLM이 학습
    # prior(2023년)를 적었다(라이브 실측 2026-08-25). 결정적 값(앵커·조회 기간·오늘)을 주입.
    reference_info = _build_reference_info(state)
    user_prompt = _build_response_prompt(
        # 정규 질의 채널(plans/107 W3) — 꺼져 있으면 종전 그대로 R6(task 스코프 질의).
        original_query=get_prompt_query(
            state, config, consumer=CONSUMER_OUTPUT_GENERATOR,
            current=parsed.get("original_query", ""),
        ),
        summary=organized["summary"],
        rows=organized["rows"],
        reference_info=reference_info,
        # 멀티 DB 순위 전역 재정렬(plans/113 S-1)이 적용된 행은 이미 전체 순위다 — 앞 20건 그대로.
        ranked=bool((organized.get("merge_ranking") or {}).get("applied")),
        # 멀티 DB 집계 질의(plans/113 S-3) — 수치 요약을 DB별·전체 코드 계산값으로 바꾼다.
        aggregates=organized.get("merge_aggregates"),
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
    response = await astream_text(llm, messages, tags=tags)
    # 표 정규화(D-187): GFM은 구분선 셀 수 ≠ 헤더 셀 수면 표로 인식하지 않아 원문이 노출된다
    # (19열 금감원 양식에서 LLM이 셀 수를 자주 틀림 — 라이브 실측 2026-08-26). 렌더만 보장.
    response = _normalize_markdown_tables(response)
    # 사후 가드(D-186): 요약 문단의 "YYYY년 M월" 연도가 기준 연도 밖이면 침묵하지 않는다.
    # 스트리밍은 이미 화면에 나간 뒤라 회수가 아닌 후행 경고이며, 자동 치환은 하지 않는다.
    warn = _check_response_years(response, reference_info)
    if warn:
        response = f"{response}\n\n{warn}"
    return response


def _generate_empty_result_response(
    parsed: dict, diagnosis_payload: dict | None = None
) -> str:
    """결과가 0건일 때의 응답을 생성한다.

    진단(D-176 후속1)이 있으면 *"어느 조건에서 끊겼는지"* 를 덧붙인다. 조건이 3개일 때
    "필터 조건을 완화해보세요"는 **어느 것을 완화해야 하는지**를 알려주지 못한다.

    진단이 없으면(플래그 OFF·프로브 미발동) 종전 문구를 **바이트 단위로** 그대로 낸다.

    Args:
        parsed: 파싱된 요구사항
        diagnosis_payload: `state["empty_diagnosis"]`(없으면 None)

    Returns:
        빈 결과 안내 텍스트
    """
    targets = ", ".join(parsed.get("query_targets", ["데이터"]))
    filters = parsed.get("filter_conditions", [])

    response = f"조건에 해당하는 {targets} 데이터가 없습니다."

    diagnosis = diagnosis_from_payload(diagnosis_payload)
    if diagnosis is not None:
        rendered = render_diagnosis(diagnosis)
        if rendered:
            return f"{response}\n\n{rendered}"

    if filters:
        response += "\n\n다음과 같은 방법을 시도해보세요:"
        response += "\n- 필터 조건을 완화해보세요 (예: 임계값 낮추기)"
        if parsed.get("time_range"):
            response += "\n- 시간 범위를 넓혀보세요"

    return response


# 식별자성 칼럼('리소스 ID', 'resource_id', 'id' 등 — 이름이 별도 단어 'id'로 끝남) 판정.
# 식별자의 최소/최대/평균은 무의미하므로 수치 요약에서 제외한다.
_IDENTIFIER_COL_RE = re.compile(r"(?:^|[^0-9A-Za-z가-힣])id$", re.IGNORECASE)


def _numeric_summary_lines(rows: list) -> list[str]:
    """전체 rows에서 숫자 칼럼별 최소·최대·평균·null 건수를 결정적으로 계산한다(C-11·C-12).

    포함 기준(좁게): 비-null 값이 1개 이상이고 전부 int/float(bool 제외)인 칼럼.
    식별자성 칼럼(`_IDENTIFIER_COL_RE`)은 통계가 무의미하므로 제외. 문자열 숫자('4.0')는
    강제 변환하지 않는다 — 형 추정 오류로 잘못된 통계를 싣는 것보다 생략이 안전하다.
    """
    dict_rows = [r for r in rows if isinstance(r, dict)]
    if not dict_rows or len(dict_rows) != len(rows):
        return []
    columns: list[str] = []
    for r in dict_rows:
        for k in r.keys():
            key = str(k)
            if key not in columns:
                columns.append(key)
    lines: list[str] = []
    for col in columns:
        if _IDENTIFIER_COL_RE.search(col.strip()):
            continue
        values = [r.get(col) for r in dict_rows]
        non_null = [v for v in values if v is not None]
        if not non_null:
            continue
        if not all(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null
        ):
            continue
        null_count = len(values) - len(non_null)
        line = (
            f"- {_display_field_name(col)}: 최소 {round(min(non_null), 2)} · "
            f"최대 {round(max(non_null), 2)} · "
            f"평균 {round(sum(non_null) / len(non_null), 2)}"
        )
        if null_count:
            line += f" (null {null_count}건)"
        lines.append(line)
    return lines


def _all_null_value_columns(rows: list) -> list[str] | None:
    """전 행 null 강등(C-06) 판정 — 발동 조건을 좁게 고정한다.

    발동 조건(전부 충족 시 전-행-null 칼럼 목록 반환, 아니면 None):
      ① 행이 1건 이상이고 전부 dict
      ② 결과 전체에 비-null 숫자 값이 하나도 없음 — 지표가 한 값이라도 채워졌으면
         미발동(C-03 부분 null 오폭 방지)
      ③ 전 행 null 칼럼이 **2개 이상**, 또는 1개 이상이면서 전체 칼럼의 절반 이상
         — 절반 기준만 쓰면 멀티 병합 표(존·서버명·호스트명 등 식별 칼럼 다수)에서
         지표 2칼럼 전 행 null인데도 미발동한다(C-06 CM 재실측 결함). ≥2 기준만 쓰면
         [hostname, cpu_avg(null)] 같은 단일 지표 결과를 놓친다 — 둘의 합집합.
         식별자 목록 + 부수적 공란 1칼럼(빈 비고)은 두 기준 모두 미달이라 보호된다.
    호출부는 output_format=text에서만 발동시킨다 — 폼필(xlsx/docx) 동반 텍스트는
    의도적 공란(도메인 밖 열, H-06)이 있어 강등하면 안 된다.
    """
    dict_rows = [r for r in rows if isinstance(r, dict)]
    if not dict_rows or len(dict_rows) != len(rows):
        return None
    columns: list[str] = []
    for r in dict_rows:
        for k in r.keys():
            key = str(k)
            if key not in columns:
                columns.append(key)
    if not columns:
        return None
    all_null: list[str] = []
    has_numeric = False
    for col in columns:
        values = [r.get(col) for r in dict_rows]
        non_null = [v for v in values if v is not None]
        if not non_null:
            all_null.append(col)
            continue
        if any(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in non_null
        ):
            has_numeric = True
    if not all_null or has_numeric:
        return None
    if len(all_null) < 2 and len(all_null) * 2 < len(columns):
        return None
    return all_null


def _generate_all_null_response(
    null_cols: list[str], rows: list, state: AgentState
) -> str:
    """값 칼럼이 전 행 null인 결과의 결정적 안내 응답(C-06 — LLM 미호출).

    조회 기간에 진행 중인 달이 포함되면 "월간 통계는 직전월까지 집계" 안내를 덧붙인다.
    CSV 산출은 라우트가 query_results로 별도 생성하므로 여기서 잃지 않는다.
    """
    cols = ", ".join(_display_field_name(c) for c in null_cols)
    response = (
        f"조회는 {len(rows)}건 수행되었으나, 값 칼럼({cols})이 전 행 null이어서 "
        "목록 표시를 생략합니다.\n"
        "원본 조회 데이터는 CSV 다운로드로 확인할 수 있습니다."
    )
    info = _build_reference_info(state)
    period = info.get("period")
    this_month = date.today().strftime("%Y%m")
    if period and str(period[1]) >= this_month:
        response += (
            f"\n\n[안내] 조회 기간({_format_ym(str(period[0]))}~{_format_ym(str(period[1]))})에 "
            "진행 중인 달이 포함되어 있습니다. 폴스타 월간 통계는 직전월까지 집계되므로, "
            "직전월 기준으로 다시 질의하면 값이 조회될 수 있습니다."
        )
    return response


def _build_response_prompt(
    original_query: str,
    summary: str,
    rows: list[dict],
    reference_info: dict | None = None,
    *,
    ranked: bool = False,
    aggregates: dict[str, Any] | None = None,
) -> str:
    """응답 생성 프롬프트를 구성한다.

    Args:
        original_query: 원본 사용자 질의
        summary: 데이터 요약
        rows: 결과 데이터 행
        reference_info: `_build_reference_info` 산출(오늘·기준월·조회 기간) — 기간·연도 표기의
            결정적 근거(D-186). None이면 블록 생략(종전 프롬프트와 동일).
        ranked: 멀티 DB 순위 전역 재정렬(plans/113 S-1)이 적용된 행인지 — True면 미리보기를
            DB별로 고르게 뽑지 않고 앞 20건(전체 순위 상위)을 그대로 싣는다.
        aggregates: 멀티 DB 집계 종합(plans/113 S-3) — 적용됐으면 「수치 요약」을 DB별 값과
            건수·합계·최대·최소의 전체 값으로 바꾼다(평균 전체 값은 만들지 않는다). 행 위 통계
            (DB별 값끼리의 최소·최대·평균)는 DB를 넘나드는 무의미한 값이라 싣지 않는다.

    Returns:
        구성된 프롬프트 문자열
    """
    # 결과가 많으면 20건만 프롬프트에 포함 — 멀티 DB 이어 붙이기는 DB별로 고르게(S-2).
    # 복합 필드명(그룹|서브, D-145)의 '|'는 Markdown 표 구분자와 충돌해 응답 표가
    # 깨진다(라이브 실측: 칼럼 분해·순서 뒤죽박죽) — 표시용 키로 결정적 치환.
    preview, balanced = _preview_rows(rows, ranked=ranked)
    display_rows = [_display_row(r) if isinstance(r, dict) else r for r in preview]
    truncated = len(rows) > 20
    shown = "DB별로 고르게 20건" if balanced else "상위 20건"

    parts = [
        f"## 사용자 질의\n{original_query}",
        f"## 데이터 요약\n{summary}",
        (
            f"## 조회 결과 ({len(rows)}건"
            f"{f', {shown} 표시' if truncated else ''})\n"
            f"```json\n"
            f"{json.dumps(display_rows, ensure_ascii=False, indent=2)}\n"
            f"```"
        ),
    ]

    # 수치 요약(결정적, C-10~C-12): 응답 LLM은 위 20행 미리보기만 보므로 최대/최소/평균을
    # 직접 계산하면 전체와 어긋난다(라이브 실측: 전체 max 99.58%를 49.02%로 서술).
    # 전체 rows에서 코드가 계산한 값만 인용하도록 강제한다.
    agg_lines = _aggregate_summary_lines(aggregates)
    if agg_lines:
        parts.append(
            "## 수치 요약 (DB별 집계 — 코드 계산값)\n"
            "각 DB의 집계 값과, 건수·합계·최대·최소의 전체 값입니다. 수치를 서술할 때는 "
            "**반드시 아래 값을 그대로** 쓰세요. \"전체 값 없음\" 항목(평균 등)은 "
            "DB별 행 수 정보가 없어 합칠 수 없으니 전체를 계산하거나 추정해 서술하지 마세요. "
            "이 블록 자체를 본문에 복창하지 마세요.\n"
            + "\n".join(agg_lines)
        )
    stats_lines = [] if agg_lines else _numeric_summary_lines(rows)
    if stats_lines:
        parts.append(
            f"## 수치 요약 (전체 {len(rows)}건 전수 기준 — 코드 계산값)\n"
            "최대·최소·평균 등 수치를 서술할 때는 **반드시 아래 값을 그대로** 쓰세요. "
            "위 조회 결과 JSON은 표시용 일부라 직접 계산하면 틀립니다. "
            "칼럼 값에 단위(%, GB 등)를 임의로 붙이지 마세요. 값이 개수·건수·개월 수면 "
            "칼럼명에 pct/percent가 들어 있어도 %를 붙이지 말고(별칭 표기 오류일 수 있음 — "
            "예: months_over_40pct는 '개월 수'), 칼럼명을 '비율'로 바꿔 부르지도 마세요. "
            "%는 값 자체가 비율(0~100 사용률 등)일 때만 씁니다. "
            "이 블록 자체를 본문에 복창하지 마세요.\n"
            + "\n".join(stats_lines)
        )

    # 표에 모든 컬럼을 빠짐없이 포함하도록 컬럼 목록을 명시한다(D-100) — LLM이 질의 문구에
    # 이끌려 일부 컬럼만 표시하는 것을 방지(실측: "제조사와 일련번호" 질의에서 서버명·알람명 누락).
    columns = list(display_rows[0].keys()) if display_rows and isinstance(display_rows[0], dict) else []
    if columns:
        rule = (
            "## 표시 규칙\n"
            f"아래 {len(columns)}개 컬럼을 **모두** 표에 포함하세요(하나도 생략 금지): "
            + ", ".join(columns)
        )
        if truncated:
            # "N건 중 상위 20건(대표 서버)" 오서술 차단(D-186) — 절단은 표시 제한이지 데이터 특성이 아니다
            rule += (
                f"\n전체 결과는 {len(rows)}건이며 위 JSON은 표시용으로 {shown}만 실은 것입니다. "
                "요약에는 전체 건수만 쓰고, '상위 20건'·'대표 서버'처럼 절단을 데이터 특성으로 "
                "서술하지 마세요."
            )
        parts.append(rule)

    # 기준 정보(D-186): 기간·연도 표기의 결정적 근거. 프롬프트에 연도가 없으면 LLM이 학습
    # prior 연도를 적는다 — [기준월 안내]는 LLM 생성 **이후** 덧붙어 LLM이 볼 수 없다.
    if reference_info:
        ref_lines = [f"- 오늘: {reference_info['today']}"]
        anchor = reference_info.get("anchor")
        if anchor:
            ref_lines.append(
                f"- 월별 칼럼 기준월: {_format_ym(anchor[0])} ~ {_format_ym(anchor[1])} "
                "(M=첫 달, 이후 M+1, M+2 … 순)"
            )
        period = reference_info.get("period")
        if period:
            ref_lines.append(f"- 조회 기간: {_format_ym(period[0])} ~ {_format_ym(period[1])}")
        parts.append(
            "## 기준 정보\n"
            "기간·연도를 언급할 때는 **반드시 아래 값을 그대로** 쓰고, 여기에 없는 연도를 "
            "추정해 적지 마세요. 이 블록 자체를 본문에 복창하지 말고 기간 표기에만 참조하세요.\n"
            + "\n".join(ref_lines)
        )

    return "\n\n".join(parts)


#: 멀티 DB 병합 행의 출처 태그(`multi_db_executor._merge_results`) · 응답 표의 출처 칼럼 표시명.
_SOURCE_KEY = "_source_db"
_SOURCE_LABEL = "출처"
_PREVIEW_LIMIT = 20


def _preview_rows(
    rows: list[Any], *, ranked: bool, limit: int = _PREVIEW_LIMIT,
) -> tuple[list[Any], bool]:
    """응답 LLM에 싣는 미리보기 행(최대 20건)과 DB 균형 추출 여부 (plans/113 S-2 · B-2).

    멀티 DB 이어 붙이기 결과는 DB 실행 순서대로 이어져 있어 앞 20건으로 자르면 뒤 DB 행이
    하나도 보이지 않는다("한쪽만 조회한 것처럼" 보이는 두 번째 경로). 행마다 출처 태그가 있고
    출처가 2곳 이상이며 20건을 넘을 때만 DB별로 번갈아 뽑는다 — 대상 DB마다 1행 이상, DB 안
    순서는 유지하고 출력은 원래 순서다. 단일 DB·전역 재정렬(S-1) 행은 종전 그대로 앞 20건이다.
    1단 도구 결과 요약(`deepagents_tools._serialize_for_tool`)도 같은 규칙으로 자른다(`limit`).
    """
    head = rows[:limit]
    if ranked or len(rows) <= limit:
        return head, False
    by_source: dict[Any, list[int]] = {}
    for idx, row in enumerate(rows):
        if not isinstance(row, dict) or _SOURCE_KEY not in row:
            return head, False
        by_source.setdefault(row[_SOURCE_KEY], []).append(idx)
    if len(by_source) < 2:
        return head, False
    picked: list[int] = []
    depth = 0
    while len(picked) < limit:
        took = False
        for indices in by_source.values():
            if depth < len(indices) and len(picked) < limit:
                picked.append(indices[depth])
                took = True
        if not took:
            break
        depth += 1
    return [rows[i] for i in sorted(picked)], True


def _display_row(row: dict[str, Any]) -> dict[str, Any]:
    """표시용 행 — 복합 필드명 치환(D-145) + 출처 태그를 레지스트리 표시명 칼럼으로(G-4).

    출처 태그가 없는 행(단일 DB)은 종전 치환만 한다(프롬프트 바이트 동일).
    """
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k == _SOURCE_KEY and _SOURCE_LABEL not in row:
            out[_SOURCE_LABEL] = _db_display_name(v)
        else:
            out[_display_field_name(str(k))] = v
    return out


#: 집계 종류 표시명(plans/113 S-3).
_AGG_KIND_LABELS = {
    "COUNT": "건수", "SUM": "합계", "MAX": "최대", "MIN": "최소", "AVG": "평균",
    "COUNT_DISTINCT": "중복 제외 건수", "OTHER": "값",
}


def _fmt_number(value: Any) -> str:
    """집계 값 표기 — 정수는 천 단위 구분, 실수는 소수 둘째 자리."""
    if value is None:
        return "NULL"
    if isinstance(value, float):
        return f"{int(value):,}" if value.is_integer() else f"{round(value, 2):,}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _aggregate_summary_lines(aggregates: dict[str, Any] | None) -> list[str]:
    """집계 종합(plans/113 S-3) 줄 — 항목마다 DB별 값 · 전체 값(건수·합계·최대·최소만)."""
    if not isinstance(aggregates, dict) or not aggregates.get("applied"):
        return []
    lines: list[str] = []
    for col in aggregates.get("columns") or []:
        per_db = col.get("per_db") or {}
        rendered = " · ".join(
            f"{_db_display_name(d)} {_fmt_number(v)}" for d, v in per_db.items()
        )
        label = f"{_display_field_name(str(col.get('column')))}"
        kind = _AGG_KIND_LABELS.get(str(col.get("kind")), "값")
        total = col.get("total")
        tail = f" → 전체 {_fmt_number(total)}" if total is not None else " (전체 값 없음)"
        lines.append(f"- {label}({kind}): {rendered}{tail}")
    return lines


def _db_display_name(db_id: Any) -> str:
    """DB 표시명(레지스트리 `display_name`) — 미등록이면 db_id 그대로."""
    domain = get_domain_by_id(str(db_id)) if db_id else None
    return domain.display_name if domain else str(db_id)


_TABLE_SEP_CELL_RE = re.compile(r"^\s*:?-{3,}:?\s*$")


def _split_table_cells(line: str) -> list[str]:
    """`| a | b |` 행을 셀 목록으로 나눈다(양끝 파이프 제거, `\\|` 이스케이프 보존)."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def _normalize_markdown_tables(text: str) -> str:
    """마크다운 표 블록의 구분선·본문 셀 수를 헤더에 맞춘다(D-187 — 렌더 실패 안전망).

    GFM(marked.js gfm=true)은 **구분선 행의 셀 수가 헤더와 다르면** 블록을 표로 인식하지
    않고 원문을 그대로 보여준다. 19열 양식에서 LLM이 셀 수를 틀리는 일이 잦아(라이브
    실측) 구분선을 헤더 셀 수로 재생성하고, 본문 행은 부족분 패딩·초과분 절단(GFM이 초과
    셀을 버리는 동작과 동일)한다. 내용(누락 칼럼·순서)은 고치지 않는다 — 렌더만 보장.
    코드 펜스 내부와 "2행이 구분선"이 아닌 블록은 건드리지 않는다.
    """
    if not text or "|" not in text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    i, n = 0, len(lines)
    in_fence = False

    def _has_pipe(s: str) -> bool:
        # 선행 파이프 없는 GFM 표(`a | b` / `---|---`)도 표다 — LLM이 자주 쓰는 형태
        # (폐쇄망 실측 2026-08-26: 선행 `|` 요구 시 이 변형이 정규화에서 빠져 원문 노출 지속)
        return bool(re.search(r"(?<!\\)\|", s))

    def _is_sep_line(s: str) -> bool:
        if not _has_pipe(s):
            return False  # 파이프 없는 `---`는 수평선(hr)이지 표 구분선이 아니다
        cells = _split_table_cells(s)
        return bool(cells) and all(_TABLE_SEP_CELL_RE.match(c) for c in cells)

    while i < n:
        line = lines[i]
        if line.strip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if (not in_fence) and _has_pipe(line) and i + 1 < n and _is_sep_line(lines[i + 1]):
            header_cells = _split_table_cells(line)
            width = len(header_cells)
            out.append("| " + " | ".join(header_cells) + " |")
            out.append("|" + "|".join(["---"] * width) + "|")
            i += 2
            while i < n and lines[i].strip() and _has_pipe(lines[i]):
                cells = _split_table_cells(lines[i])
                if len(cells) < width:
                    cells = cells + [""] * (width - len(cells))
                elif len(cells) > width:
                    cells = cells[:width]
                out.append("| " + " | ".join(cells) + " |")
                i += 1
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _build_reference_info(state: AgentState) -> dict:
    """응답 프롬프트용 기준 정보(오늘·월 시리즈 앵커·조회 기간)를 결정적으로 산출한다(D-186).

    앵커는 `form_month_anchor`(D-146/D-185), 조회 기간은 `resolve_stat_month_range`
    (정규식 1순위 → LLM time_range 폴백)로 SQL 필터와 같은 값을 쓴다. 기간 표현이 없으면
    오늘만 싣는다(비폼필 "지난달"류 서술의 연도 정확도도 함께 보강).
    """
    today = date.today()
    info: dict = {"today": today.isoformat()}
    anchor = state.get("form_month_anchor") or {}
    if anchor.get("start") and anchor.get("end"):
        info["anchor"] = (str(anchor["start"]), str(anchor["end"]))
    parsed = state.get("parsed_requirements") or {}
    period = resolve_stat_month_range(
        parsed.get("original_query") or state.get("user_query") or "",
        today,
        parsed_time_range=parsed.get("time_range"),
    )
    if period:
        info["period"] = period
    return info


_YEAR_MONTH_RE = re.compile(r"(\d{4})년\s*\d{1,2}월")


def _check_response_years(response: str, reference_info: dict | None) -> str | None:
    """요약 문단의 "YYYY년 M월" 연도가 기준 연도 밖이면 경고 문구를 돌려준다(D-186 사후 가드).

    검사 범위를 **첫 표 이전 문단**의 **연+월 패턴**으로 한정한다 — 표 본문의 도입일자·비고
    등 정당한 연도(서버 양식)를 오탐하지 않기 위함. 기준 연도(앵커·조회 기간)가 없으면
    판정 근거가 없으므로 미발동. 자동 치환은 하지 않는다(LLM 문장 임의 수정 금지).
    """
    if not response or not reference_info:
        return None
    years: set[str] = set()
    for key in ("anchor", "period"):
        rng = reference_info.get(key)
        if rng:
            years.update({str(rng[0])[:4], str(rng[1])[:4]})
    if not years:
        return None
    summary = response.split("\n|", 1)[0]
    found = {m.group(1) for m in _YEAR_MONTH_RE.finditer(summary)}
    bad = sorted(found - years)
    if not bad:
        return None
    logger.warning(
        "응답 요약의 연도가 기준 연도 밖(D-186 가드): 발견=%s, 기준=%s", bad, sorted(years)
    )
    return (
        "**[확인 필요]** 요약 문장의 연도(" + ", ".join(f"{y}년" for y in bad) + ")가 "
        "실제 조회 기준(" + ", ".join(f"{y}년" for y in sorted(years)) + ")과 다릅니다. "
        "기간은 아래 [기준월 안내]와 처리현황의 생성 SQL을 기준으로 확인해주세요."
    )


def _format_ym(yyyymm: str) -> str:
    """YYYYMM을 'YYYY년 M월'로 표기한다."""
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        return yyyymm
    return f"{yyyymm[:4]}년 {int(yyyymm[4:6])}월"


_ALARM_MODE_LABELS = {"active": "활성", "history": "이력"}


def _prepend_alarm_headline(response: str, state: AgentState, app_config) -> str:
    """알람 결정적 조립 대상 질의의 응답 첫 줄에 코드가 만든 헤드라인을 붙인다.

    응답 LLM이 샘플 행의 ack_status 값을 보고 "전부 NOT_ACK"처럼 전체를 일반화하는
    서술 환각(2026-09-02 폐쇄망 실측 — 연도 환각 D-177③과 동형)에 대한 결정적 대응:
    건수는 db_result_summary(실측), 조건 서술은 인식기 재실행(결정적)에서 얻으므로
    LLM 서술과 무관하게 정확한 숫자·조건이 항상 최상단에 보인다.

    플래그 OFF·비알람·미인식·건수 없음이면 no-op(응답 바이트 무변경).
    """
    cfg = getattr(app_config, "text2sql", None)
    if not getattr(cfg, "alarm_deterministic", False):
        return response
    if state.get("routing_intent") != "alarm_query":
        return response
    from src.db_adapters.polestar.assembler import recognize_active_alarm_query

    spec = recognize_active_alarm_query(
        state.get("user_query", ""),
        parsed_time_range=(state.get("parsed_requirements") or {}).get("time_range"),
    )
    if spec is None:
        return response
    if spec.group_by:
        # 서버별 집계(D-202 3차)는 행수=서버 수라 "총 N건" 헤드라인이 오독을 만든다 —
        # 집계 표 자체가 결정적 산출물이므로 헤드라인 없이 그대로 둔다.
        return response

    summary = state.get("db_result_summary") or {}
    if summary:
        counts = {d: (info or {}).get("row_count", 0) for d, info in summary.items()}
        total = sum(counts.values())
        per_zone = " (" + " · ".join(f"{d} {c:,}건" for d, c in counts.items()) + ")"
    else:
        rows = state.get("query_results") or []
        if not rows:
            return response  # 0건은 기존 0건 안내가 담당
        total, per_zone = len(rows), ""

    parts = [_ALARM_MODE_LABELS.get(spec.mode, spec.mode)]
    if spec.mode == "history" and spec.month_range:
        parts.append(f"{spec.month_range[0]}~{spec.month_range[1]}")
    if spec.type_label:
        parts.append(f"{spec.type_label} 유형")
    if spec.severity is not None:
        op = " 이상" if spec.severity_op == ">=" else ""
        parts.append(f"심각도 {spec.severity}{op}")
    if spec.unack_only:
        parts.append("미확인만")
    headline = (
        f"**[알람 조회]** {' · '.join(parts)} — 총 {total:,}건{per_zone}"
    )
    return headline + chr(10) + chr(10) + (response or "")


def _append_zone_coverage_notes(response: str, state: AgentState) -> str:
    """멀티 DB 조회의 존별 커버리지·부분 실패를 응답 말미에 명시한다 (침묵 강등 금지).

    한 존이라도 행을 반환하면 나머지 존의 실패·0행이 무표시로 증발하던 문제의 교정
    (2026-09-02 폐쇄망 실측 — 같은 알람 질의가 턴마다 "B0만"/"GP·YD만"으로 보였고,
    사용자는 "그 존엔 없다"로 오독). db_errors는 result_merger가 전체 실패일 때만
    error_message로 승격하고 부분 실패는 삼켰다(result_merger `not db_results` 조건).
    단일 DB 조회(두 필드 모두 빈 값)는 no-op — 응답 바이트 무변경.

    Args:
        response: 지금까지 조립된 응답 텍스트
        state: 에이전트 상태 (db_errors · db_result_summary 소비)

    Returns:
        각주가 덧붙은 응답 (해당 없으면 원본 그대로)
    """
    db_errors = state.get("db_errors") or {}
    summary = state.get("db_result_summary") or {}
    lines: list[str] = []

    if db_errors:
        lines.append("**[일부 존 조회 실패]**")
        for db_id, err in db_errors.items():
            lines.append(f"- {db_id}: {str(err)[:150]}")

    # 멀티 존 조회의 존별 건수 — "그 존에 없음(0건)"과 "조회 누락"을 사용자가 구별할 수 있게
    # 한다. 전 존 0행은 기존 0건 안내가 담당한다. 정상 조회 턴(전 존 1행 이상)에도 싣는다 —
    # 종합 결과를 존 기준으로 읽는 근거다(plans/113 G-4 (가) 존별 건수 1줄).
    if len(summary) >= 2 and _rows_are_merged(state):
        counts = {d: (info or {}).get("row_count", 0) for d, info in summary.items()}
        if any(c > 0 for c in counts.values()):
            names = {d: str((info or {}).get("display_name") or d) for d, info in summary.items()}
            rendered = " · ".join(f"{names[d]} {c:,}건" for d, c in counts.items())
            lines.append(
                f"**[존별 결과]** {rendered}{_ranking_clause(state, names)}"
                f"{_aggregate_skip_clause(state)}"
            )
            agg_lines = _aggregate_summary_lines(
                (state.get("organized_data") or {}).get("merge_aggregates")
                if isinstance(state.get("organized_data"), dict) else None
            )
            if agg_lines:
                # 집계 질의(S-3)는 표보다 이 값이 답이다 — LLM 서술과 무관하게 결정적으로 싣는다.
                lines.append("**[존별 집계]**")
                lines.extend(agg_lines)

    if not lines:
        return response
    return (response or "") + "\n\n" + "\n".join(lines)


def _aggregate_skip_clause(state: AgentState) -> str:
    """집계 질의인데 DB별 값을 합치지 못한 사유(plans/113 S-3 · 침묵 강등 금지).

    사유가 없으면 빈 문자열이다.
    """
    organized = state.get("organized_data")
    aggregates = organized.get("merge_aggregates") if isinstance(organized, dict) else None
    if not isinstance(aggregates, dict) or aggregates.get("applied"):
        return ""
    if not aggregates.get("reason"):
        return ""
    return f" — {aggregates['reason']} DB별 집계 값을 합치지 않았습니다"


def _rows_are_merged(state: AgentState) -> bool:
    """이번 응답 행이 멀티 DB 병합 행(출처 태그)인지 — 존별 건수 줄의 잔존 방어(plans/113).

    `db_result_summary`는 멀티 DB 경로만 쓰므로, 같은 요청 안에서 단일 경로로 재시도하면 앞선
    병합의 요약이 남는다. 이번 행에 출처 태그가 하나도 없으면 그 요약은 이번 행의 것이 아니다.
    행 정보가 없으면(각주 함수 단독 호출) 종전 판정 그대로다.
    """
    organized = state.get("organized_data")
    if not isinstance(organized, dict):
        return True
    rows = organized.get("rows") or []
    return any(isinstance(r, dict) and _SOURCE_KEY in r for r in rows)


def _ranking_clause(state: AgentState, names: dict[str, str]) -> str:
    """존별 건수 줄 뒤에 붙는 전역 재정렬 경과(plans/113 S-1) — 경과가 없으면 빈 문자열.

    적용: 표가 전체 기준 상위 N이라는 사실과 그 N건의 존별 분포 · 미적용: 순위 질의인데 DB별
    결과를 이어 붙였다는 사실과 사유(침묵 강등 금지).
    """
    organized = state.get("organized_data") or {}
    ranking = organized.get("merge_ranking") if isinstance(organized, dict) else None
    if not isinstance(ranking, dict):
        return ""
    if not ranking.get("applied"):
        reason = ranking.get("reason")
        if not reason:
            return ""
        return f" — {reason} 전체 기준으로 다시 정렬하지 않고 존별 결과를 이어 붙였습니다"
    rows = organized.get("rows") or []
    top: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict) and row.get(_SOURCE_KEY):
            src = str(row[_SOURCE_KEY])
            top[src] = top.get(src, 0) + 1
    # 조회한 존 순서로 싣는다(0건 존도 — 상위 N에 한 건도 들지 못했다는 사실이 정보다).
    dist = " · ".join(f"{name} {top.get(d, 0):,}건" for d, name in names.items())
    return f" → 전체 기준 상위 {len(rows):,}건({dist})"


def _append_form_fill_notes(
    response: str,
    state: AgentState,
    fill_stats: dict[str, int] | None = None,
) -> str:
    """폼필 응답에 기준월 매핑(§2.4)과 미작성 항목 사유(D-147)를 덧붙인다.

    - 기준월: 월 시리즈(M~M+5) 양식은 M이 어느 달인지 양식에 없으므로 실제 사용 월을
      응답에 반드시 명시한다(감사자료 오기재 방지 — plans/72 R4). 양식 원본(비고 열 등)에는
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
        requested = anchor.get("requested") or None
        source = anchor.get("source") or ("query" if requested else "default")
        if source == "query" and requested:
            basis = f"질의에서 지정한 기간({_format_ym(requested[0])}~{_format_ym(requested[1])})의 끝 월을 마지막 칼럼(M+N)으로"
        elif source == "absolute":
            basis = "양식에 표기된 절대 월 기준으로"
        else:
            basis = "기간 지정이 없어 실행일 기준 지난달을 마지막 칼럼(M+N)으로"
        parts.append(
            "**[기준월 안내]** 월별 사용률 칼럼은 "
            f"{_format_ym(anchor['start'])}부터 {_format_ym(anchor['end'])}까지 "
            f"({basis}) 데이터로 채웠습니다. "
            "다른 기준월이 필요하면 \"1월부터 6월까지\"처럼 기간을 지정해 다시 요청해주세요."
        )
        # 요청 기간과 실제 채운 월이 다르면 침묵하지 않는다(D-185) — 요청 개월 수 ≠ 양식 칸 수
        # (예: 1~3월 지정에 6칸 양식)나 상대 양식의 끝 월 정렬로 생기는 차이를 명시.
        if requested and (
            str(requested[0]) != str(anchor["start"]) or str(requested[1]) != str(anchor["end"])
        ):
            parts.append(
                "**[기간 불일치]** 요청 기간은 "
                f"{_format_ym(requested[0])}~{_format_ym(requested[1])}이지만 양식의 월 칼럼 수에 맞춰 "
                f"{_format_ym(anchor['start'])}~{_format_ym(anchor['end'])}로 채웠습니다. "
                "요청 개월 수와 양식 칸 수가 다르면 끝 월을 기준으로 정렬합니다."
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
    # 사용자 답변/확인 이력 적용·거부 내역(D-151) — 침묵 반영·침묵 무시 금지.
    # origin으로 분리 표시: answer=이번 턴 패널 답변, memory=확인 이력(Phase 3).
    overrides = state.get("form_fill_overrides") or {}
    if overrides:
        _act_label = {
            "blank": "공란 유지",
            "column": "DB 항목 지정",
            "eav": "DB 항목 지정",
            "literal": "직접 입력",
        }
        ans_lines: list[str] = []
        mem_lines: list[str] = []
        user_blank_fields: set[str] = set()
        for f, o in overrides.items():
            label = _display_field_name(f)
            if o.get("applied"):
                act = _act_label.get(o.get("action"), str(o.get("action")))
                val = o.get("value")
                suffix = f"('{val}')" if val not in (None, "") else ""
                line = f"  - {label}: {act}{suffix} 적용"
                if o.get("action") == "blank":
                    user_blank_fields.add(f)
            else:
                line = f"  - {label}: 반영 불가 — {o.get('reason')}"
            (mem_lines if o.get("origin") == "memory" else ans_lines).append(line)
        if ans_lines:
            parts.append("**[사용자 답변 적용 내역]**\n" + "\n".join(ans_lines))
        if mem_lines:
            parts.append(
                "**[확인 이력 적용]** (이전에 기억한 답 — 변경하려면 \"필드명 기억 삭제\"라고 요청)\n"
                + "\n".join(mem_lines)
            )
        # 사용자 지정 공란은 미작성 사유 목록에서 제외(중복 안내 방지)
        unfilled = [f for f in unfilled if f not in user_blank_fields]

    if unfilled:
        shown = "\n".join(f"  - {_display_field_name(f)}" for f in unfilled)
        parts.append(
            "**[미작성 항목]** 다음 칼럼은 수집 데이터에 해당 항목이 없어 "
            f"비워두었습니다(임의 기재 금지):\n{shown}"
        )

    if not parts:
        return response
    return response + "\n\n---\n" + "\n\n".join(parts)


def _build_form_fill_hitl(
    state: AgentState,
    fill_stats: dict[str, int] | None,
) -> tuple[Optional[dict], Optional[dict]]:
    """미해결 필드를 역질문 페이로드와 멀티턴 대기 상태로 구성한다(D-151).

    미해결 = 실제 채움 0건 필드 − 월 시리즈 필드 − 직접 입력 상수 − 사용자 지정 공란.
    전 필드 0건(식별 칼럼 포함)이면 매핑이 아니라 데이터·SQL 문제이므로 역질문하지
    않는다(D-050 — 잘못된 질문으로 오진 유도 금지).

    Returns:
        (clarification | None, pending | None) — 미해결 없으면 (None, None)이며
        호출부가 pending_form_fill=None 델타로 자기정리한다.
    """
    if not fill_stats:
        return None, None
    if not any(cnt > 0 for cnt in fill_stats.values()):
        return None, None
    anchor = state.get("form_month_anchor") or {}
    month_fields = set(anchor.get("fields") or [])
    literals = state.get("form_fill_literals") or {}
    overrides = state.get("form_fill_overrides") or {}
    user_blanks = {
        f for f, o in overrides.items()
        if o.get("applied") and o.get("action") == "blank"
    }
    unresolved = [
        f for f, cnt in fill_stats.items()
        if cnt == 0 and f not in month_fields
        and f not in literals and f not in user_blanks
    ]
    if not unresolved:
        return None, None
    candidates = state.get("form_fill_candidates") or []
    clarification = {
        "question": (
            f"채우지 못한 항목이 {len(unresolved)}건 있습니다. "
            "각 항목의 처리 방법(공란 유지 / DB 항목 선택 / 직접 입력)을 지정해 주세요. "
            + FORM_MEMORY_SHORTCUT_HINT
        ),
        "fields": [{"name": f, "label": _display_field_name(f)} for f in unresolved],
        "candidates": candidates,
    }
    # FIX-26(라이브 실측 2026-08-13): 답변 턴 존 유실 방지 — 원 질의 복원(FIX-17)은
    # 존이 텍스트 위치어로 지정된 런에서만 라우팅을 재현한다. 존 체크박스 런
    # ("채워줘" + CM존 선택)은 복원된 원 질의에 위치어가 없어 답변 턴이 기본 DB(b0)로
    # 침묵 오라우팅되고, 존재성 검증도 b0 스키마 기준이 되어 답변이 전량 탈락한다.
    # 이 런의 확정 존을 pending에 보존해 답변 턴 라우트가 selected_db_ids로 복원한다.
    # 우선순위: 사용자 체크박스 선택 > 라우팅 확정(target_databases 등) > 실행 결과 DB.
    from src.nodes.context_resolver import _extract_previous_db_ids

    db_ids = list(state.get("selected_db_ids") or []) or _extract_previous_db_ids(state)
    if not db_ids:
        db_ids = [d for d in (state.get("db_results") or {}) if d]
    pending = {
        "uploaded_file": state.get("uploaded_file"),
        "file_type": state.get("file_type")
        or state.get("parsed_requirements", {}).get("output_format"),
        "original_query": state.get("parsed_requirements", {}).get("original_query")
        or state.get("user_query", ""),
        "unresolved": unresolved,
        "db_ids": db_ids or None,
    }
    return clarification, pending


def _display_field_name(field: str) -> str:
    """복합 필드명(그룹|서브)을 사용자 표시용 'A > B'로 변환한다(내부 키는 '|' 유지)."""
    return field.replace("|", " > ")


def _append_scope_note(response: str, state: AgentState) -> str:
    """좁힌 범위의 **미조회 사실**을 응답에 덧붙인다(D-176 후속4 · §5.3 불변식 6).

    범위 축소는 정보 손실이 복구되지 않는 절단이다 — 좁혔다는 사실을 말하지 않으면
    사용자는 **전체를 본 결과로 읽는다**. LLM 프롬프트에 각주를 지시하면 누락되므로
    코드가 결정적으로 붙인다(급증 한계 표기와 같은 원칙).
    """
    from src.domain.scope_select import render_narrowed_note

    note = render_narrowed_note(state.get("scope_narrowed"))
    return f"{response}\n\n{note}" if note else response


# ── 결정적 각주: 진행 중인 달 집계 기준 (C-06 후속 — "이번 달"=stat_d 집계 결정) ──

#: 성능 통계 계열 질의 판별 표면어 — 알람·목록 조회에 각주가 오부착되지 않도록 좁힌다.
_CURRENT_MONTH_NOTE_METRIC_TERMS = (
    "사용률", "이용률", "통계", "cpu", "씨피유", "메모리", "파일시스템",
)


def _append_current_month_partial_note(response: str, state: AgentState) -> str:
    """진행 중인 달이 조회 기간에 포함된 성능 통계 응답에 집계 기준을 결정적으로 명시한다.

    "이번 달" 질의는 프로필 규칙(2026-09-07)에 따라 stat_d(당월 1일~어제) 집계로 답하므로,
    그 값이 확정 월간 통계로 읽히지 않게 한다(LLM 각주 지시는 누락됨 — spike_notes 원칙).
    미발동 조건: 알람 질의(진행월 실데이터가 정상) · 지표어 없음 · 기간 끝이 당월 아님 ·
    숫자 값 없음(전 행 null 강등·빈 결과는 자체 안내가 담당).
    """
    if state.get("routing_intent") == "alarm_query":
        return response
    rows = state.get("query_results") or []
    has_numeric = any(
        isinstance(v, (int, float)) and not isinstance(v, bool)
        for r in rows if isinstance(r, dict) for v in r.values()
    )
    if not has_numeric:
        return response
    query = (
        state.get("user_query")
        or (state.get("parsed_requirements") or {}).get("original_query")
        or ""
    )
    if not any(t in query.lower() for t in _CURRENT_MONTH_NOTE_METRIC_TERMS):
        return response
    today = date.today()
    parsed = state.get("parsed_requirements") or {}
    period = resolve_stat_month_range(
        query, today, parsed_time_range=parsed.get("time_range")
    )
    if not period or str(period[1]) != today.strftime("%Y%m"):
        return response
    return (
        response
        + f"\n\n[안내] 조회 기간에 진행 중인 달({_format_ym(str(period[1]))})이 포함되어 "
        "있습니다. 진행 중인 달의 값은 확정 월간 통계가 아니라 당월 1일부터 어제까지의 "
        "일간 통계를 집계한 기준입니다."
    )


# ── 결정적 각주: 미수집 지표 · LIMIT 절단 (C-02 2차 실측 2026-09-07) ──────────

#: DB별 미수집 지표 안내 — 질의에 표면어가 있으면 응답에 결정적으로 붙인다.
#: 프로필의 "미수집 안내" 지시는 SQL 생성 프롬프트에만 실려 응답 LLM에게 전달되지
#: 않는다(생성기는 SQL만 반환 — 2차 실측에서 안내 누락 확인) — 코드가 붙인다
#: (_append_spike_notes와 같은 원칙). 근거: scripts/diag_metric_definitions.sql
#: 3존 전수 실측 — 은행존 stat_m에 MaxIORate 행 0건.
_DB_UNAVAILABLE_METRIC_NOTES: dict[str, tuple[tuple[str, ...], str]] = {
    "polestar_b0": (
        ("디스크 io", "디스크io", "disk io", "diskio", "디스크 아이오", "maxiorate"),
        "은행존 폴스타는 디스크 IO 통계를 수집하지 않아 해당 항목을 제공할 수 없습니다"
        "(실측 2026-09-07).",
    ),
}


def _involved_db_ids(state: AgentState) -> set[str]:
    """이번 응답에 관여한 DB id 집합(단일·멀티·오케스트레이션 경로 공통 신호 합집합)."""
    ids: set[str] = set()
    if state.get("active_db_id"):
        ids.add(str(state["active_db_id"]))
    for t in state.get("target_databases") or []:
        if isinstance(t, dict) and t.get("db_id"):
            ids.add(str(t["db_id"]))
    ids.update((state.get("db_result_summary") or {}).keys())
    return ids


def _append_unavailable_metric_notes(response: str, state: AgentState) -> str:
    """미수집 지표 요청에 대한 안내를 결정적으로 덧붙인다(침묵 공란 금지)."""
    query = (
        state.get("user_query")
        or (state.get("parsed_requirements") or {}).get("original_query")
        or ""
    ).lower()
    if not query:
        return response
    notes: list[str] = []
    for db_id in sorted(_involved_db_ids(state)):
        entry = _DB_UNAVAILABLE_METRIC_NOTES.get(db_id)
        if entry and any(term in query for term in entry[0]):
            notes.append(entry[1])
    if not notes:
        return response
    return response + "\n\n" + "\n".join(f"[안내] {n}" for n in notes)


_ROW_LIMIT_PATTERNS = (
    re.compile(r"\blimit\s+(\d+)", re.IGNORECASE),                        # PostgreSQL
    re.compile(r"\bfetch\s+first\s+(\d+)\s+rows?\s+only", re.IGNORECASE),  # DB2
)


def _applied_row_limit(state: AgentState) -> Optional[int]:
    """이 턴에 **실제로 적용된** 행 상한. 승격값이 없으면 실행 SQL에서 읽는다.

    `resolved_limit`은 라우트가 폼필 턴과 존 재선택 턴에만 싣는다(`api/routes/query.py`의
    두 지점). 파이프라인은 그 값을 읽기만 하고 쓰지 않으므로 **평범한 조회에서는 None**이다 —
    이 값에만 기대면 절단이 조용히 지나간다(CU-8: 알람 질의는 존 역질문을 건너뛰어
    승격 기회 자체가 없고, 그 경로가 3-DB 팬아웃이라 절단이 가장 잘 난다).

    실행 SQL은 단일·멀티 양쪽이 `query_attempts`로 남긴다(`query_executor`는 시도마다
    누적, `multi_db_executor`는 반환 dict에 싣는다) — 거기서 읽으면 승격 여부와 무관하다.
    재시도가 있었으면 **마지막** 시도의 상한이 실제 적용값이다.
    """
    promoted = state.get("resolved_limit")
    if isinstance(promoted, int) and promoted > 0:
        return promoted
    found: Optional[int] = None
    for attempt in state.get("query_attempts") or []:
        sql = attempt.get("sql") if isinstance(attempt, dict) else getattr(attempt, "sql", None)
        if not sql:
            continue
        for pattern in _ROW_LIMIT_PATTERNS:
            hit = pattern.search(str(sql))
            if hit:
                found = int(hit.group(1))
    return found


def _append_limit_truncation_note(response: str, state: AgentState) -> str:
    """LIMIT 도달 절단을 결정적으로 명시한다(K-09 · CU-8 — 절단 사실 응답 명시).

    행 수가 적용 상한에 도달했다는 것은 그 이후가 잘렸을 개연성이 높다는 뜻인데,
    2차 실측(C-02 B0: 10,000행 도달)에서 응답이 이를 말하지 않았다. LLM 지시는
    누락되므로 코드가 붙인다.

    **멀티 DB는 존별로 본다.** 종전에는 병합 총행수를 DB 하나치 상한과 비교해 두 방향으로
    틀렸다 — 3-DB × 4,000행이면 절단이 없는데도 12,000 ≥ 10,000으로 경고했고, 반대로
    어느 존이 잘렸는지는 말하지 못했다(P-4 실측: 10,000 / 2,813 / 10,000 — 잘린 것은 둘뿐).
    상한을 판독하지 못하면 no-op(오탐 없음).
    """
    limit = _applied_row_limit(state)
    if not limit:
        return response

    summary = state.get("db_result_summary") or {}
    if summary:
        truncated = [
            str((info or {}).get("display_name") or db_id)
            for db_id, info in summary.items()
            if int((info or {}).get("row_count") or 0) >= limit
        ]
        if not truncated:
            return response
        return (
            response
            + f"\n\n[안내] {' · '.join(truncated)} 조회가 상한(LIMIT {limit:,})에 도달해 "
            "이후 행이 절단되었을 수 있습니다. 기간이나 조건을 좁혀 다시 조회하면 전체를 "
            "확인할 수 있습니다."
        )

    rows = state.get("query_results") or []
    if len(rows) < limit:
        return response
    return (
        response
        + f"\n\n[안내] 결과가 조회 상한(LIMIT {limit:,})에 도달해 이후 행이 절단되었을 수 "
        "있습니다. 기간이나 조건을 좁혀 다시 조회하면 전체를 확인할 수 있습니다."
    )


def append_structure_missing_note(response: str, state: AgentState) -> str:
    """관리자 등록이 필요한 DB 자산 부재 사유를 응답 말미에 **결정적으로** 덧붙인다(plans/104).

    사유는 `dependency_notes`(D-203 채널)의 `ADMIN_ASSET_NOTE_KINDS` 노트다 — 구조 정보 없음
    (G-1 (a))·컬럼 설명 미등록(§3.8.5 · G-9 (a)). 웹 UI는 이 채널을 따로 렌더하지 않으므로
    3단 그래프 경로(단일·멀티 DB)는 본문에 싣는다 — 침묵 강등 금지. 같은 문구는 한 번만 싣는다.
    오케스트레이션 경로(1·2단·순차 러너)는 `result_aggregator`가 같은 노트를 경과 블록으로 붙이고,
    그 경로가 이 함수에 넘기는 입력(`_build_output_state`)에는 노트가 없어 **두 번 나오지 않는다**.
    """
    details: list[str] = []
    for note in state.get("dependency_notes") or []:
        if not isinstance(note, dict) or note.get("kind") not in ADMIN_ASSET_NOTE_KINDS:
            continue
        detail = note.get("detail")
        if detail and detail not in details:
            details.append(detail)
    if not details:
        return response
    return response + "\n\n" + "\n".join(f"[안내] {d}" for d in details)


_CROSS_SYSTEM_NOTES_TITLE = "**[조회 시스템 경과]**"


def _append_cross_system_notes(response: str, state: AgentState) -> str:
    """교차 시스템 경과를 응답 말미에 **결정적으로** 덧붙인다(plans/102 · D-224).

    `dependency_notes` 중 `CROSS_SYSTEM_NOTE_KINDS`(답변 영역 소유 교정 · 분류 폴백 ·
    키 브리지 매칭 · 식별자 소재 프로브)만 렌더한다. LLM 합성에 맡기면 누락된다
    (`_append_spike_notes`와 같은 이유 — "다른 시스템으로 바꿔 조회했다"·"분류에 실패해 기본 DB로
    갔다"는 반드시 보여야 한다).

    - **D-203 순차 경과 노트는 렌더하지 않는다** — 그 노트는 오케스트레이션 경로의
      `result_aggregator`가 「순차 처리 경과」로 붙인다. 그 경로가 이 노드에 넘기는 입력
      (`_build_output_state`)에는 `dependency_notes`가 없어 두 번 나오지 않는다.
    - 각 플래그가 꺼져 있으면 이 종류의 노트가 생기지 않으므로 응답은 종전과 바이트 동일하다.
    - 텍스트 응답·파일 응답 두 경로가 같은 함수를 부른다(대칭).
    """
    lines: list[str] = []
    seen: set[tuple[Any, Any, Any]] = set()
    for note in state.get("dependency_notes") or []:
        if not isinstance(note, dict) or note.get("kind") not in CROSS_SYSTEM_NOTE_KINDS:
            continue
        detail = note.get("detail")
        if not detail:
            continue
        key = (note.get("kind"), note.get("task_id"), detail)
        if key in seen:
            continue
        seen.add(key)
        task_id = note.get("task_id")
        lines.append(f"- [{task_id}] {detail}" if task_id else f"- {detail}")
    if not lines:
        return response
    return (response or "") + "\n\n" + "\n".join([_CROSS_SYSTEM_NOTES_TITLE, *lines])


def _append_spike_notes(response: str, state: AgentState) -> str:
    """급증 조회의 한계를 응답에 **결정적으로** 덧붙인다(D-176 후속2 · §6.12).

    LLM 프롬프트에 "각주를 달아라"라고 지시하면 누락된다(LLM 비결정성). 기본 임계값을
    썼다는 사실·용량 변경 미대조·주 단위 차단은 **반드시** 보여야 하므로 코드가 붙인다 —
    못 하는 것보다 말하지 않는 것이 나쁘다.
    """
    notes = state.get("spike_notes")
    if not notes:
        return response
    return response + "\n\n" + "\n".join(f"- {n}" for n in notes)


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

    # 키가 None 값으로 실려 오면 .get(key, {})는 None을 반환한다 — or-폴백 필수
    # (오케스트레이션 _build_output_state가 state 값을 그대로 복사).
    column_mapping = state.get("column_mapping") or {}
    db_column_mapping = state.get("db_column_mapping") or {}

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
                # 사용자 직접 입력 상수(D-151 역질문 답변) — 전 데이터 행 동일값
                literal_values=state.get("form_fill_literals"),
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
