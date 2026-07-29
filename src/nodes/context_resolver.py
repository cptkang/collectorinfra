"""대화 컨텍스트 해석 노드.

멀티턴 대화에서 이전 대화 맥락을 분석하고,
현재 질의에 필요한 컨텍스트를 추출한다.
첫 번째 노드로 실행된다.

Plan 50 (M3) 확장: 후속 턴 분해(intent_planner)·DB 승계(data_query)를 위해
직전 턴의 해소된 신호(`previous_db_ids`/`previous_entities`/`previous_location`)를
`conversation_context`에 구조적으로 보존한다. 식별 키 컬럼은 값까지 소량(상한 N행)
보존하되 대량 보존은 금지한다(Known Mistakes 2026-06-11 사전 순회 상한 원칙).
"""

from __future__ import annotations

import logging

from langchain_core.messages import HumanMessage

from src.config import AppConfig
from src.routing.registry import get_registry
from src.state import AgentState

logger = logging.getLogger(__name__)

# 대화 히스토리 최대 턴 수 (Human + AI 쌍) — 데이터 평면 기준.
# (제어 평면 전용 상한은 OrchestratorConfig.max_history_turns 에서 별도로 읽는다 — 평면 분리, Plan 50 B3)
MAX_HISTORY_TURNS = 10

# 누적 토큰 이중 기준(Plan 50 B3): 메시지 누적 문자수가 이 값을 넘으면 턴 수와 무관하게 트리밍.
# 보수적 근사(문자수 기준 — 폐쇄망 토크나이저 미반입 대응, §3.5(1)). 토큰 ≈ chars/4 가정 시
# 약 24,000 토큰( = MAX_HISTORY_CHARS/4 )에 대응. 단일 턴 내 재계획 누적 방어.
MAX_HISTORY_CHARS = 96_000

# 직전 턴 식별 엔티티(서버/장비) 값을 보존할 때의 행수 상한 (R-12 / 2026-06-11 상한 원칙).
_MAX_ENTITY_ROWS = 20
# 식별 키 컬럼 후보 (보수적: 식별성 높은 컬럼 우선). subagents._IDENTITY_KEY_HINTS와 정합.
_IDENTITY_KEY_HINTS = ("hostname", "host_name", "name", "server_name", "ip", "id")
# filter_conditions 중 식별 키로 인정할 field 명.
_IDENTITY_FILTER_FIELDS = ("hostname", "host_name", "name", "server_name", "ip", "ip_address")
# 위치/환경 키워드 (DB 식별 신호 승계용). 원문/힌트에서 표면 추출(LLM 미사용).
# 정본은 `config/db_registry.yaml`(locations + environment_terms) — Plan 67 R2, 사본 금지.
# D-004 경계: 승계 신호 추출 전용이며 라우팅 의도 분류에 쓰지 않는다.
_LOCATION_KEYWORDS = get_registry().location_signal_terms()


async def context_resolver(
    state: AgentState,
    *,
    app_config: AppConfig | None = None,
) -> dict:
    """이전 대화 맥락을 분석하고 현재 질의에 필요한 컨텍스트를 추출한다.

    첫 턴이면 맥락 없이 통과하고,
    후속 턴이면 이전 SQL, 결과, 테이블, pending 상태를 요약한다.

    Args:
        state: 현재 에이전트 상태
        app_config: 앱 설정

    Returns:
        업데이트할 State 필드:
        - conversation_context: 이전 대화 맥락 딕셔너리 (첫 턴이면 None)
        - messages: 트리밍된 메시지 (MAX_HISTORY_TURNS/MAX_HISTORY_CHARS 초과 시)
        - current_node: "context_resolver"
    """
    messages = state.get("messages", [])
    turn_count = len([m for m in messages if isinstance(m, HumanMessage)])

    # 첫 턴이면 맥락 없음
    if turn_count <= 1:
        result: dict = {
            "conversation_context": None,
            "current_node": "context_resolver",
        }
        # 대화 히스토리 트리밍
        trimmed = _trim_messages(messages)
        if len(trimmed) < len(messages):
            result["messages"] = trimmed
        return result

    # 후속 턴: 이전 대화에서 맥락 추출
    previous_sql = state.get("generated_sql", "")
    previous_results = state.get("query_results", [])
    previous_tables = state.get("relevant_tables", [])
    previous_db_id = state.get("active_db_id")

    # pending 상태 감지
    pending_reuse = state.get("pending_synonym_reuse")
    pending_regs = state.get("pending_synonym_registrations")

    # 이전 결과 요약 (LLM 호출 없이 간단 요약)
    results_summary = ""
    if previous_results:
        results_summary = f"{len(previous_results)}건 조회됨"
        if len(previous_results) > 0:
            cols = list(previous_results[0].keys())
            results_summary += f", 컬럼: {', '.join(cols[:5])}"

    # [M3] 후속 턴 분해·승계를 위한 구조적 엔티티 보존.
    # [D-056 후속] 직전 턴이 DB 조회 없이 분석/판단(general_inference)만 수행하면 top-level
    # query_results/parsed_requirements가 비거나 갱신되어 엔티티(hostname 등)가 소실된다.
    # 이 경우 직전 conversation_context에 보존돼 있던 신호를 승계(sticky)하여, "해당 서버"
    # 지시어가 분석 턴을 건너뛰어도 유지되게 한다. 이번 턴에서 새로 추출된 값이 있으면 그것을
    # 우선하고, 없을 때만 직전 맥락을 물려받는다(사용자가 이번 턴에 새 대상을 지목하면 그 값이
    # 이번 턴 추출로 잡혀 승계보다 우선 — 오염 없음).
    prior_ctx = state.get("conversation_context") or {}
    previous_db_ids = _extract_previous_db_ids(state) or (prior_ctx.get("previous_db_ids") or [])
    previous_entities = _extract_previous_entities(state, previous_results) or (
        prior_ctx.get("previous_entities") or []
    )
    previous_location = _extract_previous_location(state) or (
        prior_ctx.get("previous_location") or ""
    )

    context = {
        "previous_sql": previous_sql,
        "previous_results_summary": results_summary,
        "previous_result_count": len(previous_results),
        "previous_tables": previous_tables,
        "previous_db_id": previous_db_id,
        "turn_count": turn_count,
        "has_pending_synonym_reuse": pending_reuse is not None,
        "has_pending_synonym_registrations": (
            pending_regs is not None and len(pending_regs or []) > 0
        ),
        "pending_synonym_reg_count": len(pending_regs) if pending_regs else 0,
        # [M3] 신규: 후속 턴 DB 승계·지시어 해소용 압축 신호
        "previous_db_ids": previous_db_ids,
        "previous_entities": previous_entities,
        "previous_location": previous_location,
    }

    logger.info(
        "context_resolver: turn=%d, prev_sql=%s, prev_results=%d, prev_db_ids=%s, "
        "prev_entities=%d, prev_location=%s, pending_reuse=%s, pending_regs=%d",
        turn_count,
        bool(previous_sql),
        len(previous_results),
        previous_db_ids,
        len(previous_entities),
        previous_location,
        bool(pending_reuse),
        len(pending_regs) if pending_regs else 0,
    )

    result = {
        "conversation_context": context,
        "current_node": "context_resolver",
    }

    # 대화 히스토리 트리밍
    trimmed = _trim_messages(messages)
    if len(trimmed) < len(messages):
        result["messages"] = trimmed

    return result


def _extract_previous_db_ids(state: AgentState) -> list[str]:
    """직전 턴의 대상 DB 식별자들을 통합하여 반환한다 (M3 — 후속 턴 DB 승계용).

    target_databases / active_db_id / mapped_db_ids 를 통합·중복 제거한다.
    순서는 관련도(target_databases 우선)를 보존한다.

    Args:
        state: 현재 에이전트 상태

    Returns:
        직전 턴 DB 식별자 목록 (중복 제거, 빈 목록 가능)
    """
    db_ids: list[str] = []

    # 1) target_databases (라우팅된 대상 — relevance 순서 보존)
    for t in state.get("target_databases", []) or []:
        if isinstance(t, dict):
            did = t.get("db_id")
        else:
            did = t
        if did and did != "default" and did not in db_ids:
            db_ids.append(did)

    # 2) active_db_id (단일 DB 경로의 실제 처리 DB)
    active = state.get("active_db_id")
    if active and active != "default" and active not in db_ids:
        db_ids.append(active)

    # 3) mapped_db_ids (양식 업로드로 고정된 DB)
    for did in state.get("mapped_db_ids", []) or []:
        if did and did not in db_ids:
            db_ids.append(did)

    return db_ids


def _looks_like_process_rows(rows: list[dict]) -> bool:
    """결과 행이 프로세스 조회 결과인지 판별한다 (엔티티 오수집 방지용).

    프로세스 조회 결과 행은 `pid`를 갖는다(process_query._process_to_dict:
    {name, pid, user, cpu_pct, mem_pct, rss, args}). `name`은 프로세스명, `pid`는 서버가
    아니므로 이런 행에서 서버 식별 엔티티를 수집하면 안 된다. 서버 조회 결과 행에는 `pid`가 없다.

    Args:
        rows: 직전 턴 결과 행 목록

    Returns:
        첫 행이 `pid` 키를 가지면 True(프로세스 행으로 간주)
    """
    if not rows:
        return False
    first = rows[0]
    if not isinstance(first, dict):
        return False
    keys = {str(k).lower() for k in first.keys()}
    return "pid" in keys


def _extract_previous_entities(
    state: AgentState, previous_results: list[dict]
) -> list[dict]:
    """직전 턴의 식별 엔티티(서버/장비)를 소량 보존한다 (M3 — "해당 서버" 지시어 해소용).

    두 출처를 결합한다:
    - parsed_requirements.filter_conditions 중 식별 키(hostname/name/ip 등) 필터 값.
    - 결과 첫 행들의 식별 키 컬럼 값 (행수 상한 _MAX_ENTITY_ROWS).

    대량 보존을 금지하여 토큰 폭증을 막는다(2026-06-11 상한 원칙).

    Args:
        state: 현재 에이전트 상태
        previous_results: 직전 턴 쿼리 결과 행 목록

    Returns:
        [{"field": "hostname", "value": "###"}] 형태 식별 엔티티 목록 (중복 제거, 상한 적용)
    """
    entities: list[dict] = []
    seen: set[tuple] = set()

    def _add(field: str, value) -> None:
        if value is None or value == "":
            return
        key = (str(field).lower(), str(value))
        if key in seen:
            return
        seen.add(key)
        entities.append({"field": field, "value": value})

    # 1) filter_conditions 중 식별 키 필터
    parsed = state.get("parsed_requirements") or {}
    for cond in parsed.get("filter_conditions", []) or []:
        if not isinstance(cond, dict):
            continue
        field = cond.get("field")
        if field and str(field).lower() in _IDENTITY_FILTER_FIELDS:
            _add(field, cond.get("value"))

    # 2) 결과 첫 행들의 식별 키 컬럼 값 (행수 상한)
    #    단, 프로세스 조회 결과 행({name, pid, ...})은 서버 식별자가 아니다. 여기서 harvesting하면
    #    name=프로세스명(mysql 등)·pid(→"id" 부분매칭)가 "서버"로 오수집되어, 이후 턴 "해당 서버"가
    #    프로세스명으로 오해소된다(알람/데이터 조회가 엉뚱한 대상으로 감). 프로세스 조회의 서버
    #    식별자는 filter_conditions(위 1)에서 이미 수집되므로, 프로세스 행은 결과 harvesting에서 제외한다.
    if previous_results and not _looks_like_process_rows(previous_results):
        limited = previous_results[: _MAX_ENTITY_ROWS]
        first = limited[0] if isinstance(limited[0], dict) else None
        if first:
            id_cols = [
                col
                for col in first.keys()
                if any(hint in str(col).lower() for hint in _IDENTITY_KEY_HINTS)
            ]
            for row in limited:
                if not isinstance(row, dict):
                    continue
                for col in id_cols:
                    _add(col, row.get(col))
                if len(entities) >= _MAX_ENTITY_ROWS:
                    break

    return entities[: _MAX_ENTITY_ROWS]


def _extract_previous_location(state: AgentState) -> str:
    """직전 턴의 폴스타 위치/환경 신호를 표면 추출하여 반환한다 (M3 — DB 식별 신호 승계).

    target_db_hints / 직전 user_query 원문에서 위치·환경 키워드를 토큰 매칭으로 추출한다.
    LLM을 사용하지 않는 결정적 표면 추출이다(부작용 없음).

    Args:
        state: 현재 에이전트 상태

    Returns:
        추출된 위치 신호 문자열(예: "김포 운영"), 없으면 빈 문자열
    """
    found: list[str] = []

    # 1) parsed_requirements.target_db_hints (있으면 우선)
    parsed = state.get("parsed_requirements") or {}
    hints = parsed.get("target_db_hints")
    hint_text = ""
    if isinstance(hints, str):
        hint_text = hints
    elif isinstance(hints, (list, tuple)):
        hint_text = " ".join(str(h) for h in hints)

    # 2) 직전 user_query 원문 (state.user_query는 이번 턴 질의이므로 messages 마지막 AI 이전의
    #    Human 메시지가 직전 질의 — 여기서는 보수적으로 hint_text + 현재 parsed 원문만 사용)
    haystack = f"{hint_text} {parsed.get('raw_query', '')}".strip()

    for kw in _LOCATION_KEYWORDS:
        if kw in haystack and kw not in found:
            found.append(kw)

    return " ".join(found)


def _approx_tokens(messages: list) -> int:
    """메시지 누적 문자수를 반환한다 (토큰 근사용 — 보수적, Plan 50 §3.5(1)).

    정확한 토크나이저(폐쇄망 미반입 가능) 대신 content 문자수 합으로 근사한다.
    상한 트리거 용도이므로 정밀도는 불필요하다.

    Args:
        messages: 메시지 목록

    Returns:
        누적 content 문자수
    """
    total = 0
    for m in messages:
        content = getattr(m, "content", "")
        if isinstance(content, str):
            total += len(content)
        elif content:
            total += len(str(content))
    return total


def _trim_messages(messages: list) -> list:
    """대화 히스토리를 턴 수 + 누적 문자수 이중 기준으로 제한한다 (Plan 50 B3).

    1차로 턴 수(MAX_HISTORY_TURNS)로 자르고, 그래도 누적 문자수가
    MAX_HISTORY_CHARS를 넘으면 가장 오래된 쌍부터 추가로 제거한다.
    (단일 턴 내 재계획 누적으로 메시지 1~2개가 비대해진 경우도 방어 — 기존 턴 수만으로는
    못 막던 M5 토큰 폭증 대응.)

    Args:
        messages: 전체 메시지 목록

    Returns:
        트리밍된 메시지 목록
    """
    max_messages = MAX_HISTORY_TURNS * 2
    trimmed = messages[-max_messages:] if len(messages) > max_messages else list(messages)

    # 누적 문자수 이중 기준: 상한 초과 시 가장 오래된 메시지부터 추가 제거.
    # 최소 마지막 2개(현재 턴 질의/응답 쌍)는 보존한다.
    while len(trimmed) > 2 and _approx_tokens(trimmed) > MAX_HISTORY_CHARS:
        trimmed = trimmed[1:]

    return trimmed
