"""응답 본문 결정적 노트 — `output_generator`가 응답 끝에 덧붙이는 각주·고지 함수.

plans/166 W2 N7(이동만) — `src/nodes/output_generator.py`에서 본문 그대로 옮겼다. 노트 호출 나열
(텍스트 경로·문서 경로의 구성·순서)은 `output_generator._run_output_generator`에 그대로 있다
(U-8 — 체인 선언화는 W4). `output_generator`가 옮긴 이름을 모두 재노출하고 자기 전역 이름으로
부르므로 `monkeypatch.setattr(og, "_append_x_note", …)` seam은 그대로다(D-337 ①②).

옮기지 않은 것(D-337 ② · 원 모듈 잔류): `_append_turn_notices`·`collect_disclosures`(테스트가
`og.get_registry`를 patch하는 `scope_partial_text`·`_is_zoned_db`를 부름) ·
`_append_zone_coverage_notes`와 그 보조 3종(응답 표 렌더 보조 `_SOURCE_KEY`·
`_aggregate_summary_lines`·`_db_display_name`을 공유) · `_prepend_alarm_headline`(`_append_*`
계열이 아님 · 인자 3개).

계층: application(`src.pipeline`) — LLM 0 · 로그 0.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from src.domain import disclosure as disc
from src.domain.query_time import QueryTime
from src.domain.time_spec import Subject, TimeResolution
from src.state import AgentState
from src.utils.month_structure import is_month_structure_field
from src.utils.prior_dependency import ADMIN_ASSET_NOTE_KINDS, CROSS_SYSTEM_NOTE_KINDS
from src.utils.query_gen_common import (
    ELLIPTICAL_SUCCESSION_KEY,
    explicit_row_count,
    has_full_scope_request,
    render_elliptical_succession_note,
    resolve_stat_month_range,
)


def _format_ym(yyyymm: str) -> str:
    """YYYYMM을 'YYYY년 M월'로 표기한다."""
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        return yyyymm
    return f"{yyyymm[:4]}년 {int(yyyymm[4:6])}월"


def _form_month_fields(state: AgentState, fill_stats: dict[str, int] | None) -> set[str]:
    """월 시리즈 필드 = 인식기 앵커 필드 ∪ 채움 통계의 월 구조 필드(plans/120 F-6).

    인식기가 발동하지 않아(리소스 명사 부재 등) 앵커가 없어도 월 구조 열은 매핑 대상이 아니다
    — 역질문 후보·[미작성 항목]에서 빼고 전부 0건이면 [확인 필요]로 알린다. 판정은 필드 매퍼의
    매핑 스킵과 같은 규칙(`src.utils.month_structure`)이다. 채움 통계가 없으면(docx) 앵커만 본다.
    """
    anchor = state.get("form_month_anchor") or {}
    fields = set(anchor.get("fields") or [])
    fields.update(f for f in (fill_stats or {}) if is_month_structure_field(f))
    return fields


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
      (D-050 — null/공란은 데이터 부재가 아니라 SQL 문제일 수 있음). 인식기가 발동하지 않아
      앵커가 없어도 월 구조 열은 같은 규칙으로 센다(plans/120 F-6 · `_form_month_fields`).
    """
    anchor = state.get("form_month_anchor") or {}
    month_fields = _form_month_fields(state, fill_stats)

    parts: list[str] = []
    if anchor.get("start") and anchor.get("end"):
        requested = anchor.get("requested") or None
        source = anchor.get("source") or ("query" if requested else "default")
        if source == "query" and requested:
            basis = f"질의에서 지정한 기간({_format_ym(requested[0])}~{_format_ym(requested[1])})의 끝 월을 마지막 칼럼(M+N)으로"  # noqa: E501
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
                f"{_format_ym(requested[0])}~{_format_ym(requested[1])}이지만 양식의 월 칼럼 수에 맞춰 "  # noqa: E501
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
            if anchor.get("fields"):
                parts.append(
                    "**[확인 필요]** 월별 사용률 칼럼이 전부 채워지지 않았습니다. 데이터 부재가 "
                    "아니라 조회 SQL 문제일 수 있으니 처리현황의 생성 SQL을 확인해주세요."
                )
            else:
                # 앵커 없음 = 월 시리즈 인식기 미발동 — 월 열이 조회 SQL에 없다(plans/120 F-6).
                parts.append(
                    "**[확인 필요]** 월별 사용률 칼럼이 전부 채워지지 않았습니다. "
                    "월별 사용률 양식으로 인식하지 못해 기준월을 정하지 못했습니다. "
                    "질의에 대상 자원(CPU·메모리 등)과 기간을 함께 적어 다시 요청하거나 "
                    "처리현황의 생성 SQL을 확인해주세요."
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
                act = _act_label.get(o.get("action"), str(o.get("action")))  # type: ignore[arg-type]
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
                "**[확인 이력 적용]** (이전에 기억한 답 — 변경하려면 \"필드명 기억 삭제\"라고 요청)\n"  # noqa: E501
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

    note = render_narrowed_note(
        state.get("scope_narrowed"),
        # 「전체」를 요청했는데 좁혔으면 그 점을 문구 머리에 밝힌다(plans/123 W-5 강화 규칙)
        full_scope_requested=has_full_scope_request(_turn_query(state)),
    )
    return f"{response}\n\n{note}" if note else response


def _append_elliptical_succession_note(response: str, state: AgentState) -> str:
    """생략형 후속 턴이 직전 서버를 승계했으면 그 사실을 덧붙인다(plans/120 PL-1 — 침묵 승계 금지).

    승계는 `input_parser`가 parsed_requirements에 남긴다 — 2단은 `_build_output_state`가 같은
    parsed_requirements를 넘기므로 두 단이 같은 키로 닿는다. 범위 축소 고지와 같은 원칙으로 코드가
    결정적으로 붙인다.
    """
    marker = (state.get("parsed_requirements") or {}).get(ELLIPTICAL_SUCCESSION_KEY)
    note = render_elliptical_succession_note(marker)
    return f"{response}\n\n{note}" if note else response


# ── 결정적 각주: 진행 중인 달 집계 기준 (C-06 후속 — "이번 달"=stat_d 집계 결정) ──

#: 성능 통계 계열 질의 판별 표면어 — 알람·목록 조회에 각주가 오부착되지 않도록 좁힌다.
_CURRENT_MONTH_NOTE_METRIC_TERMS = (
    "사용률", "이용률", "통계", "cpu", "씨피유", "메모리", "파일시스템",
)


def _month_to_date_period(qt: QueryTime) -> TimeResolution | None:
    """성능 통계 해석이 「이번 달」(당월 1일부터 어제까지의 일 누적)이면 그 해석 — 아니면 None.

    진행 중인 달 안내(「당월 1일부터 어제까지의 일간 통계」 각주 · 전 행 null 안내)의 발동 조건이다
    (plans/122 T-8 · 검증 D4). 입도 일 · 시작 = 당월 1일 · 완결성 to_date일 때만 참이다 — 「최근
    3시간」(시간 통계)·「어제」·「지난주」·「3일 전」은 당월에 걸쳐도 그 각주의 사실이 아니다. 매월
    1일(빈 구간)은 집계된 날이 없어 None이다.
    """
    res = qt.metric
    if res is None or res.start is None or res.end is None or res.is_empty:
        return None
    month_start = qt.anchor_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if res.grain == "day" and res.start == month_start and res.completeness == "to_date":
        return res
    return None


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
    qt = QueryTime.from_state(state.get("time_resolution"))
    if qt is not None:
        # 시간 해석(plans/122 T-4) — 성능 통계 해석이 「이번 달」(당월 1일부터의 일 누적)일 때만
        res = _month_to_date_period(qt)
        if res is None:
            return response
        current_ym = f"{qt.anchor_at:%Y%m}"
    else:
        today = date.today()
        parsed = state.get("parsed_requirements") or {}
        period = resolve_stat_month_range(
            query, today, parsed_time_range=parsed.get("time_range")
        )
        if not period or str(period[1]) != today.strftime("%Y%m"):
            return response
        current_ym = str(period[1])
    return (
        response
        + f"\n\n[안내] 조회 기간에 진행 중인 달({_format_ym(current_ym)})이 포함되어 "
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


def _applied_row_limit(state: AgentState) -> int | None:
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
    found: int | None = None
    for attempt in state.get("query_attempts") or []:
        sql = attempt.get("sql") if isinstance(attempt, dict) else getattr(attempt, "sql", None)
        if not sql:
            continue
        for pattern in _ROW_LIMIT_PATTERNS:
            hit = pattern.search(str(sql))
            if hit:
                found = int(hit.group(1))
    return found


#: 결과 건수를 직접 지정한 표현 중 `explicit_row_count`(LIMIT 결정 규칙) 밖의 것 — 상한 절단
#: 고지의 **비발동 판정에만** 쓴다(LIMIT 결정은 바꾸지 않는다 · plans/123 W-1 ②). 「CPU 높은 서버
#: 3대」·「TOP 5」는 LLM이 LIMIT 3·5를 스스로 붙인다 — 그 수만큼 온 것은 절단이 아니다.
_REQUESTED_COUNT_EXTRA_RES = (
    re.compile(r"(?i)\btop\s*(\d{1,6})"),
    re.compile(r"(\d{1,6})\s*대(?!\s*(?:이상|이하|초과|미만|넘))"),
    re.compile(r"(\d{1,6})\s*위(?:까지)?(?![가-힣])"),
)


def _turn_query(state: Mapping[str, Any]) -> str:
    """턴 원문 — 2단 task 마감은 `original_user_query`(집계기가 싣는다), 3단은 `user_query`."""
    return str(
        state.get("original_user_query")
        or state.get("user_query")
        or (state.get("parsed_requirements") or {}).get("original_query")
        or ""
    )


def _requested_row_counts(query: str) -> set[int]:
    """질의가 지정한 결과 건수들 — 명시 건수(W-0) · 상위 N · TOP N · N대 · N위."""
    counts: set[int] = set()
    explicit = explicit_row_count(query)
    if explicit is not None:
        counts.add(explicit)
    for pattern in _REQUESTED_COUNT_EXTRA_RES:
        for m in pattern.finditer(query or ""):
            n = int(m.group(1))
            if n > 0:
                counts.add(n)
    return counts


def _limit_truncation_text_one(state: Mapping[str, Any]) -> str | None:
    """조회 한 건(단일 task 또는 병합 원천 하나)의 상한 도달 고지 문장 — 없으면 None."""
    limit = _applied_row_limit(state)  # type: ignore[arg-type]
    if not limit:
        return None
    # 명시 건수·상위 N과 같은 상한이면 「요청한 만큼」이다 — 절단이 아니다
    # (W-1 ② · 3단 top-N 오탐 제거).
    if limit in _requested_row_counts(_turn_query(state)):
        return None

    summary = state.get("db_result_summary") or {}
    if summary:
        truncated = [
            str((info or {}).get("display_name") or db_id)
            for db_id, info in summary.items()
            if int((info or {}).get("row_count") or 0) >= limit
        ]
        if not truncated:
            return None
        return (
            f"{' · '.join(truncated)} 조회가 상한(LIMIT {limit:,})에 도달해 "
            "이후 행이 절단되었을 수 있습니다. 기간이나 조건을 좁혀 다시 조회하면 전체를 "
            "확인할 수 있습니다."
        )

    rows = state.get("query_results") or []
    if len(rows) < limit:
        return None
    return (
        f"결과가 조회 상한(LIMIT {limit:,})에 도달해 이후 행이 절단되었을 수 "
        "있습니다. 기간이나 조건을 좁혀 다시 조회하면 전체를 확인할 수 있습니다."
    )


def _limit_truncation_texts(state: Mapping[str, Any]) -> list[str]:
    """상한 도달 고지 문장들.

    병합 경로(plans/123 W-1 ③)는 `limit_sources`로 **원천 task별** 실행 SQL·행 수를 받는다 — 병합
    표의 행 수·존별 건수(`_merged_zone_state`)는 표시용이라 상한 판정 입력이 아니다
    (3,000행 병합 표가 원천 10,000행 도달을 가리면 안 된다). 원천이 없으면 이 상태 하나로 판정한다.
    """
    sources = state.get("limit_sources")
    if isinstance(sources, list) and sources:
        texts: list[str] = []
        for src in sources:
            if not isinstance(src, Mapping):
                continue
            text = _limit_truncation_text_one({**src, "original_user_query": _turn_query(state)})
            if text and text not in texts:
                texts.append(text)
        return texts
    text = _limit_truncation_text_one(state)
    return [text] if text else []


def _append_limit_truncation_note(response: str, state: AgentState) -> str:
    """LIMIT 도달 절단을 결정적으로 명시한다(K-09 · CU-8 — 절단 사실 응답 명시).

    행 수가 적용 상한에 도달했다는 것은 그 이후가 잘렸을 개연성이 높다는 뜻인데,
    2차 실측(C-02 B0: 10,000행 도달)에서 응답이 이를 말하지 않았다. LLM 지시는
    누락되므로 코드가 붙인다.

    **멀티 DB는 존별로 본다.** 종전에는 병합 총행수를 DB 하나치 상한과 비교해 두 방향으로
    틀렸다 — 3-DB × 4,000행이면 절단이 없는데도 12,000 ≥ 10,000으로 경고했고, 반대로
    어느 존이 잘렸는지는 말하지 못했다(P-4 실측: 10,000 / 2,813 / 10,000 — 잘린 것은 둘뿐).
    상한을 판독하지 못하면 no-op(오탐 없음).

    **질의가 지정한 건수와 같은 상한은 절단이 아니다**(plans/123 W-1 ② — 「상위 10대」 LIMIT 10 ·
    10행에 붙던 오탐). 2단은 집계기가 task 결과의 실행 SQL(`query_attempts`)을 넘겨 준다(W-1 ①).
    """
    texts = _limit_truncation_texts(state)
    if not texts:
        return response
    return response + "\n\n" + "\n".join(f"[안내] {t}" for t in texts)


# ── 결정적 고지: 생성기 자기 고백 · 조건 반영 대조 (plans/123 S-8 · S-11) ──────────


def executed_sql_list(state: Mapping[str, Any]) -> list[str]:
    """이번 조회에서 **실제로 실행한** SQL 목록(DB별 마지막 1건).

    - 2단 task 마감: 집계기가 task 결과의 `executed_sqls`를 싣는다(W-1 ①).
    - 3단 멀티 DB: `db_executed_sqls`(DB별 실행 SQL — plans/113 S-1).
    - 3단 단일 DB: `query_attempts`의 마지막 성공 시도(없으면 마지막 시도) — 재시도 SQL은 뺀다.
    """
    executed = state.get("executed_sqls")
    if isinstance(executed, list) and executed:
        return [str(e.get("sql")) for e in executed if isinstance(e, Mapping) and e.get("sql")]
    by_db = state.get("db_executed_sqls")
    if isinstance(by_db, Mapping) and by_db:
        return [str(v) for v in by_db.values() if v]
    attempts = [a for a in (state.get("query_attempts") or []) if isinstance(a, Mapping)]
    if not attempts:
        return []
    with_sql = [a for a in attempts if a.get("sql")]
    ok = [a for a in with_sql if a.get("success")]
    chosen = ok or with_sql
    return [str(chosen[-1]["sql"])] if chosen else []


# ── 결정적 고지: 조회 기간 (plans/122 T-8 · §10.3 「고지」 · D-309) ──────────────────────


def applied_period(state: Mapping[str, Any]) -> tuple[TimeResolution, Subject] | None:
    """이번 조회의 실행 SQL이 **실제로 쓴** 기간 해석과 그 주체 — 없으면 None.

    기간과 무관한 질의(「서버 목록」)에 「지난달 기준」을 붙이면 거짓 고지다. 그래서 해석 결과의
    리터럴 경계가 실행 SQL(`executed_sql_list`)에 들어 있을 때만 그 해석을 돌려준다
    (`time_period.sql_applies_period`). 성능 통계(`metric`)·사건(`event`) 둘 다 맞으면 통계가
    우선이고, 알람 질의(`routing_intent == "alarm_query"`)면 사건이 우선이다. 시간 해석이 없으면
    (플래그 off) None — 종전 응답과 같다. 비SQL 소스(APM·REST·PromQL)는 실행 SQL이 없어 None이다.

    폼필 월 시리즈 앵커(`form_month_anchor`)가 있으면 None이다(검증 D10). 양식의 월 칼럼 수가 SQL
    기간을 정해(예: 6칸 → 6개월) 해석과 다르고, 실제로 채운 월은 폼필 「[기준월 안내]」가
    사람이 읽는 형식으로 이미 적는다 — 같은 사실을 두 줄로 싣지 않는다.
    """
    if _has_month_series_anchor(state):
        return None
    qt = QueryTime.from_state(state.get("time_resolution"))
    if qt is None:
        return None
    sqls = executed_sql_list(state)
    if not sqls:
        return None
    from src.db_adapters.polestar.time_period import sql_applies_period

    def applies(res: TimeResolution | None) -> bool:
        return res is not None and any(sql_applies_period(sql, res) for sql in sqls)

    metric_hit = applies(qt.metric)
    event_hit = applies(qt.event)
    if event_hit and qt.event is not None and (
        state.get("routing_intent") == "alarm_query" or not metric_hit
    ):
        return qt.event, "event"
    if metric_hit and qt.metric is not None:
        return qt.metric, "metric"
    return None


def _has_month_series_anchor(state: Mapping[str, Any]) -> bool:
    """월 시리즈(폼필) 앵커가 SQL 기간을 정했는가 — `_append_form_fill_notes`와 같은 판정."""
    anchor = state.get("form_month_anchor") or {}
    return isinstance(anchor, Mapping) and bool(anchor.get("start") and anchor.get("end"))


def _query_period_text_one(state: Mapping[str, Any]) -> str | None:
    applied = applied_period(state)
    if applied is None:
        return None
    res, subject = applied
    return disc.query_period_text(res, subject=subject)


def query_period_texts(state: Mapping[str, Any]) -> list[str]:
    """조회 기간 고지 문장들(`[조회 기간] …`) — 실행 SQL이 쓴 해석이 없으면 빈 목록.

    병합 경로(2단 공통 서버 키 병합)는 `period_sources`로 **원천 task별** 시간 해석·실행 SQL·의도를
    받는다 — task 문장에 명시 기간이 있으면 task마다 해석이 다를 수 있다(plans/122 §10.3 「2단
    task」). 원천에 해석이 없으면 이 상태의 해석을 쓴다. 같은 문장은 한 번만 싣는다.
    """
    if _has_month_series_anchor(state):
        return []  # 기간은 폼필 [기준월 안내]가 말한다(검증 D10 — `applied_period`)
    sources = state.get("period_sources")
    if isinstance(sources, list) and sources:
        texts: list[str] = []
        for src in sources:
            if not isinstance(src, Mapping):
                continue
            text = _query_period_text_one({
                **src,
                "time_resolution": src.get("time_resolution") or state.get("time_resolution"),
            })
            if text and text not in texts:
                texts.append(text)
        return texts
    text = _query_period_text_one(state)
    return [text] if text else []


def _append_query_period_note(response: str, state: AgentState) -> str:
    """이번 조회가 쓴 기간을 응답 본문 끝에 결정적으로 싣는다(plans/122 T-8).

    해석 출처가 기본값(기간 미지정 = 지난달)·모델 해석이어도 조용히 넘어가지 않는다(침묵 폴백
    금지). 고지 kind는 `disclosure.QUERY_PERIOD`(의무)이고 문구는 `disclosure.query_period_text`가
    정한다. 실행 SQL이 그 해석의 경계를 쓰지 않았으면(기간과 무관한 질의 · 플래그 off) no-op.
    """
    texts = query_period_texts(state)
    if not texts:
        return response
    return response + "\n\n" + disc.render_lines(disc.make(disc.QUERY_PERIOD, t) for t in texts)


def _generator_note_texts(state: Mapping[str, Any]) -> list[str]:
    """SQL 주석의 자기 고백(「무시」·「생략」·「상충」 …)을 원문 인용한 고지 문장들(S-8)."""
    from src.domain.sql_disclosure import generator_confessions

    return [
        f"조회 SQL에 생성기가 남긴 메모: 「{quote}」"
        for quote in generator_confessions(executed_sql_list(state))
    ]


def _append_generator_notes(response: str, state: AgentState) -> str:
    """생성기가 조건을 무시·생략했다고 스스로 적은 주석을 응답에 드러낸다(plans/123 S-8).

    SQL 주석은 사용자가 「실행된 SQL 보기」를 열지 않으면 보이지 않는다 — 조건을 버렸다는 고백이
    거기에만 있으면 침묵 폴백이다(R3-03 「지역 힌트는 스키마에 없으므로 무시」). 요약하지 않고
    원문을 인용한다. 없으면 no-op.
    """
    texts = _generator_note_texts(state)
    if not texts:
        return response
    return response + "\n\n" + "\n".join(f"[안내] {t}" for t in texts)


def _condition_change_texts(state: Mapping[str, Any]) -> list[str]:
    """파서 조건이 실행 SQL에 같은 뜻으로 들어갔는지 대조한 고지 문장들(S-11 1차).

    1차 범위는 **단일 task 계획**이다 — 2단 복합은 전역 조건이 모든 task에 들어가(D-094 ③)
    「다른 task 몫 조건 누락」을 오탐한다(121 TP-3.2 단계 슬롯 뒤 확장). 집계기는 단일 task일
    때만 `condition_check`를 켠다. 3단 그래프(집계기 밖)는 단일 경로라 늘 본다.
    """
    if state.get("per_task_finalize") and not state.get("condition_check"):
        return []
    parsed = state.get("parsed_requirements") or {}
    conditions = parsed.get("filter_conditions") if isinstance(parsed, Mapping) else None
    if not conditions:
        return []
    from src.domain.sql_disclosure import condition_changes

    texts: list[str] = []
    for sql in executed_sql_list(state):
        for change in condition_changes(list(conditions), sql, _turn_query(state)):
            if change.detail and change.detail not in texts:
                texts.append(change.detail)
    return texts


def _append_condition_changes(response: str, state: AgentState) -> str:
    """조건 누락·반전·결합 변경·무력화를 응답에 결정적으로 드러낸다(plans/123 S-11 1차)."""
    texts = _condition_change_texts(state)
    if not texts:
        return response
    return response + "\n\n" + "\n".join(f"[안내] {t}" for t in texts)


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
