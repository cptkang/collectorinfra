"""입력 파서 노드.

사용자 입력을 분석하여 구조화된 요구사항을 추출한다.
Phase 1에서는 자연어 파싱만 구현하고, Phase 2에서 양식 파싱을 추가한다.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

from src.utils.llm_compat import is_kbgenai
from src.config import AppConfig, load_config
from src.domain.query_target_surfaces import surfaces_of
from src.llm import create_llm
from src.prompts.input_parser import (
    INPUT_PARSER_CSV_CONTEXT_PROMPT,
    INPUT_PARSER_SYSTEM_PROMPT,
)
from src.schema_cache.cache_manager import get_cache_manager
from src.state import AgentState
from src.clients.instructor_adapter import StructuredOutputError, try_structured_call
from src.nodes.schemas import ParsedRequirements
from src.utils.json_extract import extract_json_from_response

logger = logging.getLogger(__name__)

# 위치/환경 표면어 — target_db_hints 결정적 보강용(D-065).
# LLM(규칙 10)이 "공동존"처럼 예시에 없는 위치어를 target_db_hints로 안 뽑는 경우가 있어,
# 원문에 이 표면어가 있으면 결정적으로 target_db_hints에 보강한다. DB 해소는 하지 않고
# 표면어만 넘겨(field_mapper._resolve_priority_db_ids / semantic_router가 alias로 해소),
# "공동존 김포"처럼 더 구체적 표현이 이미 힌트에 있으면 중복 추가하지 않는다.
# 선언 정본은 `config/db_registry.yaml`의 locations(Plan 67 R2/D-131). 다만 존 역질문
# 후단 게이트(D-153)가 routing(infrastructure) 계층에서도 이 값을 쓰는데 utils는
# registry를 임포트할 수 없어(계층 규칙), 값은 utils.query_gen_common에 두고 registry와의
# 동기를 테스트로 강제한다(tests/test_routing/test_location_terms_sync.py).
# D-004 경계: 이 표면어는 라우팅 의도 분류에 쓰지 않는다(사용자 명시 힌트 보강 전용).
from src.utils.query_gen_common import (
    ELLIPTICAL_SUCCESSION_KEY,
    LOCATION_HINT_TERMS,
    elliptical_succession_filter,
    term_in_text,
)

_LOCATION_HINT_TERMS = LOCATION_HINT_TERMS


def _ensure_source_hints(parsed: dict[str, Any], user_query: str) -> dict[str, Any]:
    """원문의 데이터 소스 이름·유사어를 target_db_hints에 결정적으로 보강한다(plans/132 N-1 ·
    D-293).

    위치어 보강(D-065)과 같은 방식이다 — 「제니퍼」·「APM」·「자산관리」처럼 레지스트리에 등록된
    소스 이름이 원문에 있는데 LLM이 힌트로 뽑지 않았으면 더한다. 정본은 `config/db_registry.yaml`
    (`solutions[].aliases` · 단독 DB `aliases`)이고, 위치·환경·제품 표면어는 제외된다(위 함수 몫).
    D-004 경계: 사용자가 이름으로 지목한 소스의 인식이지 의도 분류가 아니다(D-004 부기).
    """
    if not isinstance(parsed, dict) or not user_query:
        return parsed
    from src.routing.registry import get_registry  # 지연 — 레지스트리 로드는 첫 호출에서

    hints = parsed.get("target_db_hints")
    if not isinstance(hints, list):
        hints = [] if hints in (None, "") else [hints]
    existing_text = " ".join(str(h) for h in hints)
    added: list[str] = []
    for term in get_registry().source_alias_terms():
        if term_in_text(term, user_query) and not term_in_text(term, existing_text):
            hints.append(term)
            added.append(term)
            existing_text += f" {term}"
    if added:
        logger.info("데이터 소스 유사어 힌트 보강(plans/132 N-1): %s", added)
    parsed["target_db_hints"] = hints
    return parsed


def _ensure_location_hints(parsed: dict, user_query: str) -> dict:
    """원문의 위치/환경 표면어를 target_db_hints에 결정적으로 보강한다(D-065).

    LLM 프롬프트(규칙 10)만으로는 "공동존" 등 예시에 없는 위치어 추출이 비결정적이라,
    표면어가 원문에 있고 기존 힌트에 (부분 문자열로도) 없으면 추가한다.

    Args:
        parsed: 파싱 결과 dict
        user_query: 사용자 원문

    Returns:
        target_db_hints가 보강된 parsed (동일 객체)
    """
    if not isinstance(parsed, dict) or not user_query:
        return parsed
    hints = parsed.get("target_db_hints")
    if not isinstance(hints, list):
        hints = [] if hints in (None, "") else [hints]
    existing_text = " ".join(str(h) for h in hints)
    for term in _LOCATION_HINT_TERMS:
        # 라틴 표면어(DR)는 단어 경계로 — "DRM"이 여의도 힌트가 되지 않게(D-271)
        if term_in_text(term, user_query) and not term_in_text(term, existing_text):
            hints.append(term)
            existing_text += f" {term}"
    parsed["target_db_hints"] = hints
    return parsed


def _apply_elliptical_succession(parsed: dict[str, Any], state: AgentState) -> dict[str, Any]:
    """생략형 후속 턴이면 직전 서버 식별 필터를 결정적으로 주입한다 (plans/120 PL-1 · G-4 (a)).

    공통 전단에서 한 번 한다 — 2단 서브에이전트와 3단 노드가 모두 이 `filter_conditions`로 SQL을
    만들므로 두 단이 대칭이다. 주입 사실은 `ELLIPTICAL_SUCCESSION_KEY`로 남겨 응답 고지의 근거로
    쓴다(침묵 승계 금지). 양식 턴은 전량 채움이 기본이라 대상이 아니다. 이미 서버 식별 필터가 있으면
    발동하지 않으므로(조건 ⓑ) 같은 파싱본에 다시 적용해도 결과가 같다(존 답변 재사용 턴).
    """
    if state.get("uploaded_file"):
        return parsed
    cond = elliptical_succession_filter(
        state.get("user_query", ""), parsed, state.get("conversation_context")
    )
    if cond is None:
        return parsed
    logger.info(
        "생략형 후속 승계(plans/120 PL-1): %s=%s 를 filter_conditions에 주입(직전 턴 단일 서버)",
        cond["field"], cond["value"],
    )
    return {
        **parsed,
        "filter_conditions": [*(parsed.get("filter_conditions") or []), cond],
        ELLIPTICAL_SUCCESSION_KEY: {"field": cond["field"], "value": cond["value"]},
    }


async def input_parser(
    state: AgentState,
    *,
    llm: BaseChatModel | None = None,
    app_config: AppConfig | None = None,
) -> dict:
    """사용자 입력을 파싱하여 구조화된 요구사항을 추출한다.

    Phase 1: 자연어 질의 분석
    Phase 2: 양식 파일 구조 분석 추가

    Args:
        state: 현재 에이전트 상태
        llm: LLM 인스턴스 (외부 주입, 없으면 내부 생성)
        app_config: 앱 설정 (외부 주입, 없으면 내부 로드)

    Returns:
        업데이트할 State 필드:
        - parsed_requirements: 구조화된 요구사항 딕셔너리
        - template_structure: 양식 구조 (파일 업로드 시, Phase 2)
        - current_node: "input_parser"
        - error_message: None (정상 완료 시)
    """
    if app_config is None:
        app_config = load_config()
    if llm is None:
        llm = create_llm(app_config)

    # 존 역질문 답변 턴(plans/119 Q-2 · D-267 ③): 라우트가 직전 턴 파싱본을 실었으면 LLM 파싱을
    # 건너뛴다. 원 질의가 같고 이번 턴 입력은 존 선택뿐이라 다시 파싱할 내용이 없다(4~7초 절감).
    # 직전 파싱본은 동의어 치환·위치 힌트 보강까지 끝난 값이라 후처리도 다시 하지 않는다.
    # 대상 DB는 `selected_db_ids`가 정한다 — 위치 힌트는 라우팅에 쓰이지 않는다.
    reused = state.get("reuse_parsed_requirements")
    if isinstance(reused, dict) and reused and not state.get("uploaded_file"):
        parsed = {**reused, "original_query": state["user_query"]}
        logger.info(
            "입력 파싱 재사용(plans/119 Q-2): 존 선택 답변 턴 — LLM 파싱 생략, targets=%s",
            parsed.get("query_targets", []),
        )
        # 생략형 승계는 재사용 턴에도 같은 규칙으로 판정한다(plans/120 PL-1 — 멱등: 필터가 이미
        # 있으면 발동하지 않는다).
        parsed = _apply_elliptical_succession(parsed, state)
        # 시트명 추출은 아래 종전 경로와 같다(업로드 없음 → 직전 턴 양식 구조의 시트명).
        _prior_sheets = [
            s.get("name", "")
            for s in ((state.get("template_structure") or {}).get("sheets") or [])
            if s.get("name")
        ]
        return {
            "parsed_requirements": parsed,
            "template_structure": None,
            "target_sheets": _extract_target_sheets(parsed, state["user_query"], _prior_sheets),
            "unanchored_query_targets": _unanchored_query_targets(parsed, state, None),
            "current_node": "input_parser",
            "error_message": None,
        }

    try:
        context = state.get("conversation_context")
        csv_sheet_data = state.get("csv_sheet_data")

        if csv_sheet_data:
            # 시트별 순환 LLM 호출
            all_sheet_results = []
            for sheet_name, sheet_data in csv_sheet_data.items():
                csv_context = _format_single_sheet_csv(sheet_data)
                sheet_parsed = await _parse_natural_language_with_csv(
                    llm, state["user_query"], csv_context,
                    sheet_name=sheet_name, conversation_context=context,
                )
                all_sheet_results.append(sheet_parsed)
            parsed = _merge_sheet_parse_results(all_sheet_results)
        else:
            parsed = await _parse_natural_language(
                llm, state["user_query"], conversation_context=context
            )
    except Exception as e:
        logger.error(f"입력 파싱 실패: {e}")
        # 최소한의 파싱 결과로 진행 (그래프가 중단되지 않도록)
        parsed = {
            "original_query": state["user_query"],
            "query_targets": [],
            "filter_conditions": [],
            "output_format": "text",
        }

    # filter_conditions 자연어 값 → DB 조건 치환
    parsed = await _apply_column_value_synonyms(parsed)

    # 위치/환경 표면어(공동존 등) target_db_hints 결정적 보강(D-065)
    parsed = _ensure_location_hints(parsed, state.get("user_query", ""))
    # 데이터 소스 이름·유사어(제니퍼·자산관리 등) target_db_hints 결정적 보강(plans/132 N-1)
    parsed = _ensure_source_hints(parsed, state.get("user_query", ""))

    # 생략형 후속 턴 직전 서버 승계(plans/120 PL-1) — 위치 힌트 보강 뒤에 판정한다(조건 ⓒ)
    parsed = _apply_elliptical_succession(parsed, state)

    # 2. 파일 업로드 처리 — 서식 보존용 template_structure 병행 생성
    template: Optional[dict] = None
    if state.get("uploaded_file") and state.get("file_type"):
        template = _parse_uploaded_file(
            state["uploaded_file"],
            state["file_type"],
        )
        if template:
            parsed["output_format"] = state["file_type"]

    # 3. 시트명 추출 — 이번 턴 파싱본이 없으면 직전 턴 양식 구조를 쓴다(후속 턴 대칭)
    _template_for_sheets = template or state.get("template_structure")
    target_sheets = _extract_target_sheets(
        parsed,
        state["user_query"],
        [
            s.get("name", "")
            for s in ((_template_for_sheets or {}).get("sheets") or [])
            if s.get("name")
        ],
    )

    # time_range를 함께 남긴다(D-185) — 기간 2단 폴백(R3-(i))의 입력값이 로그에 없어
    # 폐쇄망에서 "LLM이 기간을 뽑았는지" 확인이 불가능했다(2026-08-25 실측).
    logger.info(
        "입력 파싱 완료: targets=%s, target_sheets=%s, time_range=%s",
        parsed.get("query_targets", []),
        target_sheets,
        parsed.get("time_range"),
    )
    _shadow_condition_conflicts(parsed)

    # 감사 기록은 여기서 하지 않는다(D-183) — 요청 수신 지점(API 라우트 · CLI 진입부)이
    # 주체다. 노드는 app.state에 닿지 못해 client_ip·session_id를 채울 수 없고,
    # 여기서 파일에 쓰면 라우트의 AuditService 기록과 겹쳐 파일에 같은 질의가 두 번 남는다.

    return {
        "parsed_requirements": parsed,
        "template_structure": template,
        "target_sheets": target_sheets,
        "unanchored_query_targets": _unanchored_query_targets(parsed, state, template),
        "current_node": "input_parser",
        "error_message": None,
    }


def _attachment_texts(state: AgentState, template: dict[str, Any] | None) -> list[str]:
    """첨부 양식의 헤더·플레이스홀더 문자열(엑셀 CSV 시트 · 양식 구조)."""
    texts: list[str] = []
    for sheet in (state.get("csv_sheet_data") or {}).values():
        if isinstance(sheet, dict):
            texts += [str(h) for h in sheet.get("headers") or []]
    for sheet in (template or {}).get("sheets") or []:
        if isinstance(sheet, dict):
            texts += [str(h) for h in sheet.get("headers") or []]
    for table in (template or {}).get("tables") or []:
        if isinstance(table, dict):
            texts += [str(h) for h in table.get("headers") or []]
    texts += [str(p) for p in (template or {}).get("placeholders") or []]
    return texts


def _unanchored_query_targets(
    parsed: dict[str, Any], state: AgentState, template: dict[str, Any] | None
) -> list[str]:
    """파서가 낸 조회 대상 중 원문에 표면어가 없는 것 — 해석 고지 트리거(plans/123 S-7(a)).

    사용자 확정(2026-09-30): 「`query_targets` 항목이 원문·유사어 사전에 없음」의 사전은
    `config/query_target_surfaces.yaml`(도메인 ↔ 표면어)이다. **트리거 키와 로그만** 남기고 응답은
    바꾸지 않는다 — 해석 한 줄 표시는 121·TP-4.7 소유다(123·G-15). 첨부 양식이 있으면 헤더·
    플레이스홀더도 원문으로 본다 — 파서 규칙 12가 헤더에서 도출한 도메인은 사용자 말의 해석이
    아니다(S-7(a)의 대상은 「CP」 → CPU 같은 말의 해석 — `plans/123` SW08).
    """
    query = str(parsed.get("original_query") or state.get("user_query") or "")
    anchor = " ".join([query, *_attachment_texts(state, template)])
    unanchored: list[str] = []
    for target in parsed.get("query_targets") or []:
        name = str(target).strip()
        if not name or name in unanchored:
            continue
        if not any(term_in_text(surface, anchor) for surface in surfaces_of(name)):
            unanchored.append(name)
    if unanchored:
        logger.info(
            "S-7(a) 트리거(plans/123): 원문에 표면어가 없는 조회 대상 %s — %r"
            "(해석 한 줄 표시는 121 TP-4.7)",
            unanchored, query[:80],
        )
    return unanchored


def _shadow_condition_conflicts(parsed: dict[str, Any]) -> None:
    """조건 충돌 사전 판정 섀도(plans/123 S-6 · 123·G-8 (c)) — **응답은 바꾸지 않고 로그만** 남긴다.

    같은 열의 수치 구간이 공집합(「90% 초과이고 10% 미만」)이면 조회해도 0건이거나, 생성기가 조건을
    「또는」으로 바꿔 전혀 다른 답을 낸다(run `20260923-103638` R3-07 — 1,535건). on 전환(조회 없이
    안내)은 대조군 과잉 판정 0을 run으로 확인한 뒤다(run R5′). 모든 사다리 단이 이 노드를 지난다.
    """
    try:
        from src.domain.input_guard import condition_conflicts

        conflicts = condition_conflicts(parsed.get("filter_conditions"))
    except Exception as e:  # noqa: BLE001 — 섀도 판정은 질의 경로를 막지 않는다
        logger.warning("S-6 섀도 판정 실패(plans/123): %s", e)
        return
    for conflict in conflicts:
        logger.info(
            "S-6 섀도(plans/123): 조건 충돌 field=%s conditions=%s — 응답 불변",
            conflict.field, list(conflict.conditions),
        )


async def _try_structured_requirements(
    llm, messages: list, *, config=None
) -> Optional[dict]:
    """구조화 출력으로 요구사항을 받는다. 비활성·미설치·실패면 None(기존 경로로 강등).

    **두 파서 함수가 이 헬퍼를 공유**한다 — 이것이 종전 비대칭(`synonym_registration`
    기본값이 CSV 경로에만 없던 문제)을 구조적으로 해소한다(Plan 79 E-3c).
    """
    try:
        cfg = config if config is not None else load_config()
    except Exception:  # noqa: BLE001 — 설정 부재가 파싱을 막으면 안 된다
        return None
    try:
        model = await try_structured_call(
            llm, messages, ParsedRequirements,
            backend=getattr(cfg, "structured_output_backend", "none"),
            max_retries=getattr(cfg, "structured_output_max_retries", 1),
        )
    except StructuredOutputError as e:
        logger.warning(
            "요구사항 구조화 추출 실패(%d회 시도) — 기존 파싱으로 강등: %s", e.attempts, e
        )
        return None
    return model.model_dump() if model is not None else None


async def _parse_natural_language(
    llm: BaseChatModel,
    user_query: str,
    *,
    conversation_context: dict | None = None,
) -> dict:
    """LLM을 사용하여 자연어 질의에서 요구사항을 추출한다.

    JSON 파싱 실패 시 1회 재시도한다.
    멀티턴 대화 시 이전 맥락을 프롬프트에 포함한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 자연어 질의 (한국어)
        conversation_context: 이전 대화 맥락 (멀티턴 시)

    Returns:
        구조화된 요구사항 딕셔너리
    """
    system_prompt = INPUT_PARSER_SYSTEM_PROMPT

    # 멀티턴: 이전 맥락 주입
    if conversation_context and conversation_context.get("turn_count", 0) > 1:
        context_section = (
            "\n\n## 이전 대화 맥락\n"
            f"- 이전 SQL: {conversation_context.get('previous_sql', '없음')}\n"
            f"- 이전 결과: {conversation_context.get('previous_results_summary', '없음')}\n"
            f"- 사용된 테이블: {', '.join(conversation_context.get('previous_tables', []))}\n"
            f"- 대화 턴: {conversation_context['turn_count']}번째\n\n"
            "사용자가 '그것', '아까', '위의', '그 중에서' 등 이전 대화를 참조하는 "
            "표현을 사용하면, 이전 맥락을 활용하여 요구사항을 해석하세요.\n"
            "'아까 결과를 Excel로' 같은 요청은 output_format을 'xlsx'로 설정하고, "
            "이전 SQL/테이블 정보를 활용하세요.\n"
        )
        system_prompt = system_prompt + context_section

    messages = [
        SystemMessage(content=system_prompt),
        # Insert dummy AIMessage when using KBGenAIChat to satisfy required order
        AIMessage(content="") if is_kbgenai(llm) else None,
        HumanMessage(content=user_query),
    ]
    # Remove any None entries (no effect for other LLMs)
    messages = [m for m in messages if m is not None]

    parsed: dict = await _try_structured_requirements(llm, messages) or {}
    for attempt in range(2 if not parsed else 0):  # 최대 2회 시도 (구조화 성공 시 생략)
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content) or {}
        if parsed and parsed.get("query_targets"):
            break
        # 재시도 시 힌트 추가
        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. query_targets는 필수입니다."
        ))

    # 원본 질의 보존
    parsed["original_query"] = user_query

    # 기본값 설정
    parsed.setdefault("output_format", "text")
    parsed.setdefault("query_targets", [])
    parsed.setdefault("filter_conditions", [])
    parsed.setdefault("time_range", None)
    parsed.setdefault("aggregation", None)
    parsed.setdefault("limit", None)
    parsed.setdefault("field_mapping_hints", [])
    parsed.setdefault("target_db_hints", [])
    parsed.setdefault("synonym_registration", None)

    return parsed


def _format_single_sheet_csv(sheet_data: dict) -> str:
    """CsvSheetData dict를 LLM 컨텍스트 문자열로 포맷한다.

    Args:
        sheet_data: CsvSheetData를 dict로 직렬화한 형태

    Returns:
        포맷된 텍스트 (헤더 + 예시 데이터)
    """
    headers = sheet_data.get("headers", [])
    example_rows = sheet_data.get("example_rows", [])

    parts = [f"#### 헤더\n{', '.join(headers)}"]

    if example_rows:
        csv_text = sheet_data.get("csv_text", "")
        parts.append(
            f"\n#### 예시 데이터 ({len(example_rows)}행)\n```csv\n{csv_text}\n```"
        )

    return "\n".join(parts)


async def _parse_natural_language_with_csv(
    llm: BaseChatModel,
    user_query: str,
    csv_context: str,
    *,
    sheet_name: str = "",
    conversation_context: dict | None = None,
) -> dict:
    """CSV 컨텍스트를 포함하여 자연어 질의를 파싱한다.

    기존 _parse_natural_language와 동일하되, 시스템 프롬프트에 CSV 컨텍스트를 추가한다.

    Args:
        llm: LLM 인스턴스
        user_query: 사용자 자연어 질의
        csv_context: 시트별 헤더+예시 데이터 텍스트
        sheet_name: 현재 분석 중인 시트명
        conversation_context: 이전 대화 맥락 (멀티턴 시)

    Returns:
        구조화된 요구사항 딕셔너리
    """
    system_prompt = INPUT_PARSER_SYSTEM_PROMPT

    # CSV 컨텍스트 추가
    csv_section = INPUT_PARSER_CSV_CONTEXT_PROMPT.format(
        sheet_name=sheet_name, csv_context=csv_context
    )
    system_prompt = system_prompt + csv_section

    # 멀티턴 맥락 (기존 로직 재활용)
    if conversation_context and conversation_context.get("turn_count", 0) > 1:
        context_section = (
            "\n\n## 이전 대화 맥락\n"
            f"- 이전 SQL: {conversation_context.get('previous_sql', '없음')}\n"
            f"- 이전 결과: {conversation_context.get('previous_results_summary', '없음')}\n"
            f"- 사용된 테이블: {', '.join(conversation_context.get('previous_tables', []))}\n"
            f"- 대화 턴: {conversation_context['turn_count']}번째\n\n"
            "사용자가 이전 대화를 참조하는 표현을 사용하면, "
            "이전 맥락을 활용하여 요구사항을 해석하세요.\n"
        )
        system_prompt = system_prompt + context_section

    messages = [
        SystemMessage(content=system_prompt),
        AIMessage(content="") if is_kbgenai(llm) else None,
        HumanMessage(content=user_query),
    ]
    # Remove any None entries — KBGenAI 외 provider는 None이 섞이면 "Unsupported message type"으로
    # 파싱 전체가 실패한다(2026-09-17 로컬 MLX H-10 실측 · _parse_natural_language와 대칭).
    messages = [m for m in messages if m is not None]

    parsed: dict = await _try_structured_requirements(llm, messages) or {}
    for attempt in range(2 if not parsed else 0):  # 구조화 성공 시 생략(대칭)
        response = await llm.ainvoke(messages)
        parsed = extract_json_from_response(response.content) or {}
        if parsed and parsed.get("query_targets"):
            break
        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. query_targets는 필수입니다."
        ))

    parsed["original_query"] = user_query
    parsed["_sheet_name"] = sheet_name  # 시트 출처 추적용
    parsed.setdefault("output_format", "text")
    parsed.setdefault("query_targets", [])
    parsed.setdefault("filter_conditions", [])
    parsed.setdefault("time_range", None)
    parsed.setdefault("aggregation", None)
    parsed.setdefault("limit", None)
    parsed.setdefault("field_mapping_hints", [])
    parsed.setdefault("target_db_hints", [])
    # 비대칭 해소(E-3c) — 종전 이 경로에만 이 기본값이 없어 CSV 질의에서 키가 사라졌다.
    parsed.setdefault("synonym_registration", None)

    return parsed


def _merge_sheet_parse_results(results: list[dict]) -> dict:
    """여러 시트의 파싱 결과를 병합한다.

    Args:
        results: 시트별 파싱 결과 리스트

    Returns:
        병합된 구조화 요구사항 딕셔너리
    """
    if not results:
        return {"query_targets": [], "filter_conditions": [], "output_format": "text"}
    if len(results) == 1:
        return results[0]

    merged = dict(results[0])  # 첫 번째 결과를 기반으로

    # query_targets 합집합
    all_targets: set[str] = set()
    for r in results:
        all_targets.update(r.get("query_targets", []))
    merged["query_targets"] = sorted(all_targets)

    # filter_conditions 합산
    all_filters: list[dict] = []
    for r in results:
        all_filters.extend(r.get("filter_conditions", []))
    merged["filter_conditions"] = all_filters

    # field_mapping_hints 합산
    all_hints: list[dict] = []
    for r in results:
        all_hints.extend(r.get("field_mapping_hints", []))
    merged["field_mapping_hints"] = all_hints

    # target_db_hints 합집합
    all_db_hints: set[str] = set()
    for r in results:
        all_db_hints.update(r.get("target_db_hints", []))
    merged["target_db_hints"] = sorted(all_db_hints)

    return merged


def _parse_uploaded_file(
    file_data: bytes,
    file_type: str,
) -> Optional[dict]:
    """업로드된 양식 파일의 구조를 분석한다. (Phase 2)

    Args:
        file_data: 파일 바이너리 데이터
        file_type: "xlsx" | "docx"

    Returns:
        양식 구조 딕셔너리 또는 None
    """
    if file_type == "xlsx":
        try:
            from src.document.excel_parser import parse_excel_template

            return parse_excel_template(file_data)
        except ImportError:
            logger.warning("Excel 파서 미구현 (Phase 2)")
            return None
        except (ValueError, Exception) as e:
            logger.error("Excel 파일 파싱 실패: %s", e)
            return None
    elif file_type == "docx":
        try:
            from src.document.word_parser import parse_word_template

            return parse_word_template(file_data)
        except ImportError:
            logger.warning("Word 파서 미구현 (Phase 2)")
            return None
        except (ValueError, Exception) as e:
            logger.error("Word 파일 파싱 실패: %s", e)
            return None
    elif file_type == "doc":
        # .doc -> .docx 변환 후 처리
        converted = _convert_doc_to_docx(file_data)
        if converted is not None:
            try:
                from src.document.word_parser import parse_word_template

                return parse_word_template(converted)
            except (ImportError, ValueError, Exception) as e:
                logger.error(".doc 변환 후 파싱 실패: %s", e)
                return None
        else:
            logger.warning(
                ".doc 파일을 .docx로 변환할 수 없습니다. "
                ".docx 형식으로 변환하여 업로드해 주세요."
            )
            return None
    else:
        logger.warning(f"지원하지 않는 파일 형식: {file_type}")
        return None


def _drop_unmatched_sheets(
    names: list[str],
    available: Optional[list[str]],
) -> Optional[list[str]]:
    """양식에 실재하는 시트명만 남기고, 하나도 못 맞히면 지목 자체를 버린다.

    시트 지목은 LLM 산출물이라 질의의 존 이름·요약 표현이 그대로 들어온다 —
    run `20260918-182507` 실측: 비-None 4턴 중 3턴이 양식과 무관한 이름
    (`['은행존']`·`['리소스 현황']`, 실제 시트는 `서버정보`·`리소스상태`)이었다.
    하류 소비처 3곳(`field_mapper`·`result_organizer`·`excel_writer`)이 모두
    **정확 일치**로 거르므로 이름이 어긋나면 대상 시트가 0개가 되고, 조회는 성공했는데
    **헤더만 있는 빈 양식**이 산출된다(H-03 759행·H-04 2338행 조회 후 0행 기입).
    부분 불일치는 하류 동작과 동일하게 그 이름만 떨구고, 전건 불일치는 "지목이 없었던 것"
    으로 되돌린다(침묵 스킵 금지 — WARNING).

    Args:
        names: 추출된 시트명 목록(비어 있지 않음)
        available: 업로드 양식이 실제로 가진 시트명. 미상(None·빈 목록)이면 판정하지 않는다.

    Returns:
        실재 시트명 목록, 또는 None(전체 시트 대상)
    """
    if not names:
        return None
    if not available:
        return names
    matched = [n for n in names if n in available]
    if matched:
        return matched
    logger.warning(
        "target_sheets %s가 양식 시트 %s와 전건 불일치 — 시트 지목을 무시하고 "
        "전체 시트를 대상으로 한다",
        names, available,
    )
    return None


def _extract_target_sheets(
    parsed: dict,
    user_query: str,
    available_sheets: Optional[list[str]] = None,
) -> Optional[list[str]]:
    """파싱 결과 또는 사용자 질의에서 대상 시트명을 추출한다.

    **LLM 파싱 결과(`target_sheets`)가 1순위**다. 시트 지목 표현은 변형이 무한하고(따옴표 없는
    "요약 시트만" 등) 산출물이 SQL 직접 입력이 아니라 재질의로 회복 가능하므로 해석은 LLM에 맡기고,
    정규식은 **최후 폴백**으로만 둔다(Plan 67 R3-(ii) · `docs/regex_llm_conversion_review.md` A10).
    폴백 정규식은 "시트" 키워드가 따옴표에 인접할 때만 인정한다 — 종전 두 번째 패턴은 `시트`가
    선택이라 조사가 붙은 따옴표 표현("'서울'의 서버")까지 시트명으로 오탐했다.

    산출물은 업로드 양식의 실제 시트명과 대조한다(`_drop_unmatched_sheets`).

    Args:
        parsed: LLM 파싱 결과
        user_query: 사용자 원본 질의
        available_sheets: 업로드 양식의 실제 시트명(미상이면 None — 대조하지 않는다)

    Returns:
        시트명 목록 또는 None (전체 시트 대상)
    """
    # 1. LLM 파싱 결과 우선 — 문자열만 남기고 공백 정리·중복 제거(형식 오류분은 폴백으로 내려간다)
    llm_sheets = parsed.get("target_sheets")
    if isinstance(llm_sheets, list):
        cleaned: list[str] = []
        for item in llm_sheets:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if name and name not in cleaned:
                cleaned.append(name)
        if cleaned:
            return _drop_unmatched_sheets(cleaned, available_sheets)

    # 2. 정규식 최후 폴백: 따옴표로 감싼 시트명 — "시트" 키워드가 따옴표 밖/안에 있어야 인정
    patterns = [
        # '시트명' 시트, "시트명" 시트
        r"""['"\u2018\u2019\u201c\u201d]([^'"\u2018\u2019\u201c\u201d]+)['"\u2018\u2019\u201c\u201d]\s*시트""",
        # '시트명시트'만, '시트명시트'에 — 따옴표 **안**에 '시트'가 포함된 형태만 인정한다
        # (종전에는 `(?:시트)?`가 선택이어서 "'서울'의 …" 같은 지역명+조사를 시트로 오탐).
        r"""['"\u2018\u2019\u201c\u201d]([^'"\u2018\u2019\u201c\u201d]*시트)['"\u2018\u2019\u201c\u201d]\s*(?:만|에|를|의)""",
    ]
    sheets: list[str] = []
    for pattern in patterns:
        matches = re.findall(pattern, user_query)
        for match in matches:
            name = match.strip()
            if name and name not in sheets:
                sheets.append(name)

    return _drop_unmatched_sheets(sheets, available_sheets)


async def _apply_column_value_synonyms(parsed: dict) -> dict:
    """filter_conditions의 자연어 value를 column_value_synonyms에 따라 DB 조건으로 치환한다.

    예: {"field": "avail_status", "op": "=", "value": "비정상"}
      → {"field": "avail_status", "op": "!=", "value": 0}

    Args:
        parsed: LLM이 파싱한 요구사항 딕셔너리

    Returns:
        치환이 적용된 요구사항 딕셔너리
    """
    filter_conditions = parsed.get("filter_conditions", [])
    if not filter_conditions:
        return parsed

    try:
        cache = get_cache_manager()
        column_value_synonyms = await cache.get_column_value_synonyms()
    except Exception:
        return parsed

    if not column_value_synonyms:
        return parsed

    new_conditions = []
    for cond in filter_conditions:
        field = str(cond.get("field", "")).upper()
        value = cond.get("value")

        # 문자열 값만 치환 대상
        if not isinstance(value, str):
            new_conditions.append(cond)
            continue

        matched = False
        for col_name, value_map in column_value_synonyms.items():
            if col_name.upper() == field or field.endswith(col_name.upper()):
                # 대소문자 무관 비교
                value_lower = value.strip().lower()
                for term, mapping in value_map.items():
                    if term.lower() == value_lower:
                        new_cond = dict(cond)
                        new_cond["op"] = mapping["op"]
                        new_cond["value"] = mapping["value"]
                        new_conditions.append(new_cond)
                        matched = True
                        break
                if matched:
                    break

        if not matched:
            new_conditions.append(cond)

    parsed["filter_conditions"] = new_conditions
    return parsed


def _convert_doc_to_docx(file_data: bytes) -> Optional[bytes]:
    """`.doc` 파일을 `.docx`로 변환한다.

    libreoffice를 사용하여 변환한다.
    libreoffice가 설치되어 있지 않으면 None을 반환한다.

    Args:
        file_data: .doc 파일 바이너리

    Returns:
        .docx 바이너리 또는 None (변환 실패 시)
    """
    import subprocess
    import tempfile
    from pathlib import Path

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "input.doc"
            doc_path.write_bytes(file_data)

            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    tmpdir,
                    str(doc_path),
                ],
                capture_output=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(
                    ".doc -> .docx 변환 실패 (returncode=%d): %s",
                    result.returncode,
                    result.stderr.decode(errors="replace")[:200],
                )
                return None

            docx_path = Path(tmpdir) / "input.docx"
            if docx_path.exists():
                return docx_path.read_bytes()
            else:
                logger.warning(".doc -> .docx 변환 결과 파일을 찾을 수 없습니다.")
                return None

    except FileNotFoundError:
        logger.warning("libreoffice가 설치되어 있지 않아 .doc 변환을 수행할 수 없습니다.")
        return None
    except subprocess.TimeoutExpired:
        logger.warning(".doc -> .docx 변환 타임아웃 (30초)")
        return None
    except Exception as e:
        logger.warning(".doc -> .docx 변환 중 오류: %s", e)
        return None


