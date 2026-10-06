"""요청 단위 시간 해석 — 규칙 1순위 + LLM 슬롯 폴백 + 단일 출처 state 직렬화.

plans/122 T-3·T-4 · D-306.

「LLM은 슬롯, 코드는 계산」(plans/122 §10.3)의 합성 지점이다. `input_parser`가 요청마다 한 번
`resolve_query_time`을 불러 state `time_resolution`(요청 스코프)에 싣고, 기간을 쓰는 모든
경로(단일·멀티·2단·3단·폼필·알람·시맨틱·검증기·도구)는 `QueryTime.from_state`로 **이것만** 읽는다.

- 규칙(`time_expr.interpret_detail`)이 기간을 잡으면 그것을 쓴다(LLM 슬롯은 `unused`)
- 규칙이 기간을 못 잡았으면 LLM 슬롯(`time_expr.slot_to_spec` — 스팬 대조·값 범위 검사)을 쓴다.
  슬롯이 검증에 떨어지면 폐기하고 사유를 남긴다(`rejected` · 환각 기간 차단)
- 해석 불가(존재하지 않는 날짜 · 연도 명시 미래 · 남은 미래 어휘 · 슬롯도 못 잡은 기간 흔적)는
  `clarify` 사유 코드만 싣고 해석 결과를 비운다 — 호출부가 되묻는다(D-275 ⑪ · D-291)
- 해석 주체 둘을 함께 계산한다: `metric`(성능 통계) · `event`(알람 — 기간 없음 = 기간 조건 없음 ·
  진행 중 기간 = 기준 시각까지 · D-291). 같은 기준 시각·같은 명세에서 나온다(단일 출처)
- 「현재·지금·실시간」은 기간이 아니다(D-291). 원문에 있으면 `present=True`만 싣는다 — 기간이 따로
  없을 때 기본값(지난달)을 강제하지 않고 호출부의 종전 경로(실시간 API · 최근 시간 통계)에
  맡기라는 신호다

`from_state`가 None을 돌려주면(플래그 off · 옛 체크포인트) 호출부는 종전 경로를 쓴다.

domain 계층 — 표준 라이브러리와 `src.domain.time_spec`·`src.domain.time_expr`만 의존한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from src.domain.time_expr import (
    CLARIFY_CODES,
    CLARIFY_FUTURE_EXPLICIT,
    CLARIFY_FUTURE_RELATIVE,
    CLARIFY_INVALID_DATE,
    CLARIFY_INVALID_SPEC,
    CLARIFY_UNRESOLVED,
    interpret_detail,
    recognize,
    slot_to_spec,
)
from src.domain.time_spec import (
    KST,
    NOTE_FUTURE_PERIOD,
    DisplayGrain,
    Subject,
    TimeResolution,
    TimeSpecError,
    resolve,
)

#: state 키 — `AgentState.time_resolution`(요청 스코프 · 두 상태 생성 함수가 None으로 초기화).
STATE_KEY = "time_resolution"
STATE_VERSION = 1

#: LLM 슬롯 처리 결과(관측용 — 로그·하네스).
SLOT_ABSENT = "absent"      # 슬롯 없음(null · 「기간 없음」 응답 포함)
SLOT_UNUSED = "unused"      # 규칙이 기간을 잡아 슬롯을 쓰지 않음
SLOT_ACCEPTED = "accepted"  # 규칙 미매칭 → 슬롯 채택(source=llm)
SLOT_REJECTED = "rejected"  # 슬롯 검증 실패 → 폐기(`slot_reason`)
SLOT_STATUSES: frozenset[str] = frozenset({SLOT_ABSENT, SLOT_UNUSED, SLOT_ACCEPTED, SLOT_REJECTED})

# 「현재·지금」은 기간이 아니다(D-291 — 실시간 경로·기본값은 호출부). LLM이 이 낱말만 스팬으로
# 기간 슬롯을 내면 받지 않는다.
_PRESENT_ONLY_RE = re.compile(r"^\s*(?:현재|지금|실시간|최신|현\s*시점|now|current)\s*$", re.I)
_PRESENT_RE = re.compile(r"현재|지금|실시간|현\s*시점")


@dataclass(frozen=True)
class QueryTime:
    """요청 하나의 시간 해석 — `metric`·`event` 두 주체 결과와 되묻기 사유.

    - `clarify`가 있으면 `metric`·`event`는 None이다(해석 불가 — 되묻기 대상)
    - `clarify`가 없으면 둘 다 있다(기간 없음도 기본값 해석이다 — `source="default"`)
    - `present`: 원문에 「현재·지금·실시간」이 있다. `metric.source == "default"`와 함께면 기본값
      기간을 강제하지 말 것(`uses_default_period`)
    """

    anchor_at: datetime
    metric: TimeResolution | None
    event: TimeResolution | None
    clarify: str | None = None
    slot_status: str = SLOT_ABSENT
    slot_reason: str | None = None
    present: bool = False

    def __post_init__(self) -> None:
        if self.anchor_at.tzinfo is None:
            raise TimeSpecError("naive_datetime", str(self.anchor_at))
        if self.clarify is not None:
            if self.clarify not in CLARIFY_CODES:
                raise TimeSpecError("invalid_clarify", self.clarify)
            if self.metric is not None or self.event is not None:
                raise TimeSpecError("clarify_with_resolution")
        elif self.metric is None or self.event is None:
            raise TimeSpecError("resolution_missing")
        if self.slot_status not in SLOT_STATUSES:
            raise TimeSpecError("invalid_slot_status", self.slot_status)

    def resolution(self, subject: Subject = "metric") -> TimeResolution | None:
        """해석 주체별 결과(되묻기 대상이면 None)."""
        return self.event if subject == "event" else self.metric

    @property
    def explicit(self) -> bool:
        """사용자가 기간을 말했고 해석됐다(규칙·슬롯 — 기본값·되묻기 아님)."""
        return self.metric is not None and self.metric.source != "default"

    @property
    def uses_default_period(self) -> bool:
        """기간 미지정 기본값(지난달)을 SQL에 강제해도 되는가 — 「현재·지금」 질의는 아니다."""
        return (
            self.metric is not None and self.metric.source == "default" and not self.present
        )

    @property
    def metric_for_sql(self) -> TimeResolution | None:
        """성능 통계 SQL에 리터럴로 걸 해석 — 명시 기간이거나 기본값을 강제해도 될 때만.

        「현재·지금」 + 기간 없음이면 None(종전 경로 · D-291). 결정적 조립·컴파일러·도구·검증기가
        이 한 판정을 함께 쓴다(리뷰 m-2 — 판정 사본 금지).
        """
        return self.metric if (self.explicit or self.uses_default_period) else None

    def to_state(self) -> dict[str, Any]:
        """state `time_resolution` 값(JSON 직렬화 — 체크포인터·스트림·하네스 공용)."""
        return {
            "version": STATE_VERSION,
            "anchor_at": self.anchor_at.isoformat(),
            "metric": self.metric.to_dict() if self.metric is not None else None,
            "event": self.event.to_dict() if self.event is not None else None,
            "clarify": self.clarify,
            "slot_status": self.slot_status,
            "slot_reason": self.slot_reason,
            "present": self.present,
        }

    @classmethod
    def from_state(cls, value: Any) -> QueryTime | None:
        """state 값 → QueryTime. 값이 없거나 모양이 어긋나면 None(호출부는 종전 경로)."""
        if not isinstance(value, dict) or value.get("version") != STATE_VERSION:
            return None
        try:
            anchor_at = datetime.fromisoformat(str(value["anchor_at"]))
            metric = value.get("metric")
            event = value.get("event")
            return cls(
                anchor_at=anchor_at,
                metric=TimeResolution.from_dict(metric) if metric is not None else None,
                event=TimeResolution.from_dict(event) if event is not None else None,
                clarify=value.get("clarify"),
                slot_status=value.get("slot_status") or SLOT_ABSENT,
                slot_reason=value.get("slot_reason"),
                present=bool(value.get("present")),
            )
        except (KeyError, ValueError, TypeError):  # TimeSpecError는 ValueError
            return None


def _rule_display_grain(text: str) -> DisplayGrain:
    grains = [s.display_grain for s in recognize(text) if s.display_grain != "none"]
    if not grains:
        return "none"
    return min(grains, key=lambda g: {"hour": 0, "day": 1, "month": 2}[g])


def resolve_query_time(text: str, now: datetime, *, slot: Any = None) -> QueryTime:
    """원문 + 기준 시각(+ LLM 슬롯) → QueryTime(plans/122 §10.3 ①~③).

    Args:
        text: 사용자 원문(재작성문이 아니라 원문 — 2단 task 명시 기간은 `resolve_task_time`)
        now: 기준 시각(요청 수신 시각). naive면 KST로 본다
        slot: input_parser LLM 출력의 `time_expr`(enum JSON · 없으면 None)
    """
    now = now.replace(tzinfo=KST) if now.tzinfo is None else now.astimezone(KST)
    qt = _resolve(text, now, slot)
    return replace(qt, present=True) if _PRESENT_RE.search(text) else qt


def _resolve(text: str, now: datetime, slot: Any) -> QueryTime:
    detail = interpret_detail(text, now, subject="metric")
    if detail.clarify is not None and detail.clarify != CLARIFY_UNRESOLVED:
        # 존재하지 않는 날짜 · 연도 명시 미래 · 남은 미래 어휘 — LLM으로 덮지 않는다(D-291)
        return QueryTime(
            now, None, None, clarify=detail.clarify,
            slot_status=SLOT_UNUSED if slot is not None else SLOT_ABSENT,
        )
    rule_res = detail.resolution
    if rule_res is not None and rule_res.source != "default":
        return QueryTime(
            now, rule_res, _event_of(text, now),
            slot_status=SLOT_UNUSED if slot is not None else SLOT_ABSENT,
        )

    # 규칙이 기간을 못 잡았다(기본값 또는 해석 불가 흔적) → LLM 슬롯
    status, reason = SLOT_ABSENT, None
    if slot is not None:
        spec, reason = slot_to_spec(slot, text)
        if spec is not None and _PRESENT_ONLY_RE.match(spec.span):
            spec, reason = None, "present_not_period"
        if reason == "no_time_expression":
            reason = None
        elif spec is None:
            status = SLOT_REJECTED
        else:
            if spec.display_grain == "none":
                spec = replace(spec, display_grain=_rule_display_grain(text))
            try:
                metric = resolve(spec, now, subject="metric")
                event = resolve(spec, now, subject="event")
            except TimeSpecError as exc:
                if exc.code == "future_explicit_period":
                    return QueryTime(
                        now, None, None, clarify="future_explicit",
                        slot_status=SLOT_REJECTED, slot_reason=exc.code,
                    )
                status, reason = SLOT_REJECTED, exc.code
            else:
                if NOTE_FUTURE_PERIOD not in metric.notes:
                    return QueryTime(now, metric, event, slot_status=SLOT_ACCEPTED)
                # 슬롯이 미래 구간(「향후 6개월」 next 등)을 냈다 — 미래는 조회하지 않는다(D-291 ·
                # 검증 D3). 규칙이 기간 흔적을 봤으면 미래 되묻기, 아니면 슬롯만 버린다(필터 표현
                # 「30일 후 만료」를 조회 기간으로 끌어오지 않는다).
                status, reason = SLOT_REJECTED, NOTE_FUTURE_PERIOD
                if rule_res is None:
                    return QueryTime(
                        now, None, None, clarify=CLARIFY_FUTURE_RELATIVE,
                        slot_status=status, slot_reason=reason,
                    )
    if rule_res is None:
        # 기간 흔적이 있는데 규칙도 슬롯도 못 잡았다 — 침묵 기본값 금지(§10.3 「해석 불가」)
        return QueryTime(
            now, None, None, clarify=CLARIFY_UNRESOLVED, slot_status=status, slot_reason=reason
        )
    return QueryTime(now, rule_res, _event_of(text, now), slot_status=status, slot_reason=reason)


def _event_of(text: str, now: datetime) -> TimeResolution:
    """같은 원문의 사건(알람) 해석 — 규칙 명세가 같아 metric이 나왔으면 event도 나온다."""
    event = interpret_detail(text, now, subject="event").resolution
    if event is None:  # 주체만 다르므로 도달하지 않는다 — -O에서도 계약을 지킨다
        raise TimeSpecError("event_resolution_missing", text)
    return event


def resolve_task_time(text: str, base: QueryTime, *, original: str | None = None) -> QueryTime:
    """2단 task 문장의 기간 — 문장에 **명시 기간**이 있을 때만 task별로 해석한다.

    plans/122 §10.3 「2단 task」.

    기준 시각은 요청 해석(`base`)과 같다. 명시 기간이 없거나 문장만으로 해석되지 않으면 `base`.
    `original`(사용자 원문)을 주면 task 문장의 기간 표현이 원문에 실제로 있을 때만 받는다 —
    분해 LLM이 원문에 없는 기간을 task 문장에 지어 넣으면 그것이 명시 기간(엄격 검증)으로
    승격되는 것을 막는다(리뷰 상태 계약 지적 · 환각 기간 차단 §10.3 ②와 같은 원칙).
    """
    periods = [s for s in recognize(text or "") if s.relation != "none"]
    if not periods:
        return base
    if original is not None:
        compact_original = "".join(original.split())
        if any("".join(s.span.split()) not in compact_original for s in periods):
            return base
    own = resolve_query_time(text, base.anchor_at)
    if own.clarify is not None:
        return base
    return own


#: 되묻기 사유 코드 → 사유 문장(plans/122 T-3 · D-291 「해석 불가 = 되묻기」 · D-275 ⑪).
_CLARIFY_REASONS: dict[str, str] = {
    CLARIFY_INVALID_DATE: "요청하신 기간에 달력에 없는 날짜가 있어 조회하지 않았습니다.",
    CLARIFY_FUTURE_EXPLICIT: "요청하신 기간은 아직 오지 않은 기간이라 조회할 데이터가 없습니다.",
    CLARIFY_FUTURE_RELATIVE: "미래 기간(내년·다음 달 등)은 조회할 데이터가 없습니다.",
    CLARIFY_UNRESOLVED: "기간 표현을 해석하지 못해 조회하지 않았습니다.",
    CLARIFY_INVALID_SPEC: "기간 표현을 해석하지 못해 조회하지 않았습니다.",
}
_CLARIFY_EXAMPLES = (
    "「지난달」·「2026년 9월」·「최근 7일」·「어제」처럼 조회할 기간을 다시 알려 주세요."
)


def clarify_message(code: str) -> str:
    """되묻기 사유 코드(`QueryTime.clarify`) → 사용자에게 보일 되묻기 문구(결정적 · LLM 0).

    plans/122 T-3 · D-306.

    input_parser가 데이터를 조회하지 않고 이 문구로 턴을 끝낸다(D-291 · D-275 ⑪). 모르는 코드는
    「해석하지 못했다」 문구로 낸다.
    """
    reason = _CLARIFY_REASONS.get(code, _CLARIFY_REASONS[CLARIFY_UNRESOLVED])
    return f"[조회 기간] {reason} {_CLARIFY_EXAMPLES}"


__all__ = [
    "SLOT_ABSENT", "SLOT_ACCEPTED", "SLOT_REJECTED", "SLOT_STATUSES", "SLOT_UNUSED",
    "STATE_KEY", "STATE_VERSION", "QueryTime", "clarify_message", "resolve_query_time",
    "resolve_task_time",
]
