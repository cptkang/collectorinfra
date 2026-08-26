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
from src.llm import USER_RESPONSE_TAG, astream_text, create_llm
from src.prompts.output_generator import OUTPUT_GENERATOR_SYSTEM_PROMPT
from src.schema_cache.form_memory import save_form_memory_entries
from src.state import AgentState
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

    # 기준 정보(D-165): 프롬프트에 연도가 전혀 없어("1월~6월" 질의 + M/M+1 칼럼) LLM이 학습
    # prior(2023년)를 적었다(라이브 실측 2026-08-25). 결정적 값(앵커·조회 기간·오늘)을 주입.
    reference_info = _build_reference_info(state)
    user_prompt = _build_response_prompt(
        original_query=parsed.get("original_query", ""),
        summary=organized["summary"],
        rows=organized["rows"],
        reference_info=reference_info,
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
    # 사후 가드(D-165): 요약 문단의 "YYYY년 M월" 연도가 기준 연도 밖이면 침묵하지 않는다.
    # 스트리밍은 이미 화면에 나간 뒤라 회수가 아닌 후행 경고이며, 자동 치환은 하지 않는다.
    warn = _check_response_years(response, reference_info)
    if warn:
        response = f"{response}\n\n{warn}"
    return response


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
    reference_info: dict | None = None,
) -> str:
    """응답 생성 프롬프트를 구성한다.

    Args:
        original_query: 원본 사용자 질의
        summary: 데이터 요약
        rows: 결과 데이터 행
        reference_info: `_build_reference_info` 산출(오늘·기준월·조회 기간) — 기간·연도 표기의
            결정적 근거(D-165). None이면 블록 생략(종전 프롬프트와 동일).

    Returns:
        구성된 프롬프트 문자열
    """
    # 결과가 많으면 상위 20건만 프롬프트에 포함.
    # 복합 필드명(그룹|서브, D-145)의 '|'는 Markdown 표 구분자와 충돌해 응답 표가
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
        rule = (
            "## 표시 규칙\n"
            f"아래 {len(columns)}개 컬럼을 **모두** 표에 포함하세요(하나도 생략 금지): "
            + ", ".join(columns)
        )
        if truncated:
            # "N건 중 상위 20건(대표 서버)" 오서술 차단(D-165) — 절단은 표시 제한이지 데이터 특성이 아니다
            rule += (
                f"\n전체 결과는 {len(rows)}건이며 위 JSON은 표시용으로 상위 20건만 실은 것입니다. "
                "요약에는 전체 건수만 쓰고, '상위 20건'·'대표 서버'처럼 절단을 데이터 특성으로 "
                "서술하지 마세요."
            )
        parts.append(rule)

    # 기준 정보(D-165): 기간·연도 표기의 결정적 근거. 프롬프트에 연도가 없으면 LLM이 학습
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


def _build_reference_info(state: AgentState) -> dict:
    """응답 프롬프트용 기준 정보(오늘·월 시리즈 앵커·조회 기간)를 결정적으로 산출한다(D-165).

    앵커는 `form_month_anchor`(D-146/D-164), 조회 기간은 `resolve_stat_month_range`
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
    """요약 문단의 "YYYY년 M월" 연도가 기준 연도 밖이면 경고 문구를 돌려준다(D-165 사후 가드).

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
        "응답 요약의 연도가 기준 연도 밖(D-165 가드): 발견=%s, 기준=%s", bad, sorted(years)
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
        # 요청 기간과 실제 채운 월이 다르면 침묵하지 않는다(D-164) — 요청 개월 수 ≠ 양식 칸 수
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
