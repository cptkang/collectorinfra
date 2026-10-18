"""조회 창 — 보기별 구간·현재값·기간 비교 창 계획과 추세 간격 자동 선택.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from src.domain import disclosure as disc
from src.domain.query_time import QueryTime
from src.domain.time_expr import recognize
from src.domain.time_spec import (
    KST,
    NOTE_MULTIPLE_PERIODS,
    UNITS,
    TimeSpec,
    TimeSpecError,
    floor_to_unit,
    resolve,
    shift_unit,
)
from src.orchestration.apm.common import (
    COMPARE_ASK_NOTE,
    COMPARE_ELAPSED_NOTE,
    COMPARE_OVERLAP_NOTE,
    CURRENT_ONLY_NOTE,
)
from src.routing.registry import ViewSpec
from src.utils.prior_dependency import NOTE_TRACE

#: 추세 간격 자동 선택(plans/134 W6 M-7) — 이보다 긴 구간에서만 고른다(짧은 구간은 종전 기본 간격).
_AUTO_INTERVAL_AFTER_MINUTES = 1440
#: 자동 선택한 간격의 점 수 상한(하루 · 5분 간격과 같은 점 수).
_AUTO_INTERVAL_POINTS = 288
#: 추세 간격 조건 이름(레지스트리 보기 `args`).
_INTERVAL_ARG = "interval_minute"


# ── 창 ────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WindowPlan:
    """보기 1개의 조회 창 — mode: current(현재·도구 기본) · window(구간) · ask(조회 안 함 · 되묻기).

    `kind`는 그 창에 붙는 고지 kind(예 `apm_current_only`)다 — 없으면 빈 문자열.
    """

    mode: str
    args: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    kind: str = ""


def _parse_bound(value: Any, *, end: bool) -> datetime | None:
    """파서 ISO 경계 → naive 시각(옛 경로). 날짜만이면 끝은 **다음 날 0시**(반개구간 — 해석값과 같은
    규칙 · 종전 23:59:59 포함을 바꿨다 · plans/134 W6 M-7)."""
    text = str(value or "").strip().replace("T", " ")
    if not text:
        return None
    try:
        if len(text) == 10:
            day = datetime.fromisoformat(text)
            return day + timedelta(days=1) if end else day
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _resolved_bounds(query_time: QueryTime) -> tuple[datetime | None, datetime] | None:
    """요청 시간 해석(D-309)의 사건 주체 구간 — 기간이 없거나 「현재·지금」이면 None(현재값).

    사건 주체(`event`)는 기간 미지정 = 기간 조건 없음 · 진행 중 기간 = 기준 시각까지다(D-291) —
    현재값 조회와 같은 뜻이다. 반개구간 `[start, end)` 그대로 쓴다. 단 시 단위 롤링(「최근 3시간」·
    「3시간 전」)은 사건 해석이 기준 시각 분 경계에서 끝나므로(plans/156 G-3) 그 기간만 시 단위 통계
    구간(직전 정시 끝)으로 되돌린다 — 제니퍼 창은 종전 그대로다(range 보기 롤링 보정 V6-2 · hourly
    보기 정시 끝).
    """
    event = query_time.event
    if event is None or event.unbounded or event.end is None:
        return None
    if query_time.present and not query_time.explicit:
        return None
    periods = _period_list(query_time)
    if periods and any(_is_hour_rolling(p) for p in periods):
        periods = [_hour_stats_period(p, query_time.anchor_at) if _is_hour_rolling(p) else p
                   for p in periods]
        return min(p.start for p in periods), max(p.end for p in periods)
    return event.start, event.end


def _legacy_bounds(time_range: Any, now: datetime) -> tuple[datetime | None, datetime] | None:
    """파서 기간(`time_range` {start, end} ISO) — 해석이 없을 때(옛 체크포인트)의 종전 경로.

    파서 경계는 naive(KST 벽시계)다. `now`가 aware면 경계에 KST를 붙여 같은 정책으로 맞춘다
    (naive·aware 비교 TypeError 방지 · V6-6). `now`가 naive면 종전대로 naive다.
    """
    if not isinstance(time_range, dict):
        return None
    start = _parse_bound(time_range.get("start"), end=False)
    end = _parse_bound(time_range.get("end"), end=True)
    if start is None and end is None:
        return None
    if now.tzinfo is not None:
        start, end = (None if b is None else b.replace(tzinfo=KST) for b in (start, end))
    return start, end or now


def plan_window(view: ViewSpec, time_range: Any, now: datetime, *,
                query_time: QueryTime | None = None) -> WindowPlan:
    """요청 기간을 보기 창 의미(`ViewSpec.window`)대로 넘긴다.

    기간은 요청 시간 해석(`query_time` — state `time_resolution` · D-309)이 있으면 그것, 없으면 파서
    `time_range`(종전)다. 기준 시각은 해석의 `anchor_at`(KST aware) · 없으면 `now`.

    - range·hourly: 요청 기간을 **자르지 않고** `reference_time`(구간 끝 · 기준 시각과 1분 안이면
      null)·`lookback_minutes`(구간 길이 분)로 넘긴다(plans/134 M-2). 오래된 기간도 넘긴다 — 종전
      「하루 넘게 지난 기간」 거부는 폐지했다(W6 M-7). range 보기의 롤링 기간(「최근 3시간」 —
      해석은 직전 정시에서 끝난다)은 같은 길이를 기준 시각까지 민다(`reference_time` null ·
      V6-2).
    - current: 현재값만 있는 보기 — 기간을 말했으면 `apm_current_only`로 알린다.
    - none: 시간과 무관한 목록 — 기간을 쓰지 않는다.
    - compare: 두 기간 비교 — `plan_compare_window`.
    """
    if view.window == "compare":
        return plan_compare_window(query_time)
    if view.window == "none":
        return WindowPlan("current")
    anchor = query_time.anchor_at if query_time is not None else now
    bounds = (_resolved_bounds(query_time) if query_time is not None
              else _legacy_bounds(time_range, now))
    if bounds is None:
        return WindowPlan("current")
    if view.window == "current":
        return WindowPlan("current", note=CURRENT_ONLY_NOTE, kind=disc.APM_CURRENT_ONLY)
    start, end = bounds
    end = min(end, anchor)
    if (view.window == "range" and query_time is not None and query_time.event is not None
            and query_time.event.completeness == "rolling" and start is not None
            and end == floor_to_unit(anchor, "hour")):
        # 롤링(「최근 3시간」)은 해석상 직전 정시에서 끝난다(시 단위 통계 기준). 실시간 구간 보기는
        # 같은 길이를 기준 시각까지 민다 — 최근 최대 59분이 빠지지 않게(V6-2). hourly 보기는 그대로
        start, end = anchor - (end - start), anchor
    span = max(1, int(((end - (start or end)).total_seconds()) // 60))
    reference = None if anchor - end <= timedelta(minutes=1) else end.isoformat(timespec="seconds")
    args = {"reference_time": reference, "lookback_minutes": span}
    # hourly 보기의 시 단위 고지는 조회한 대상마다 붙인다(`_job_disclosures`) — 기간이 없어도
    # 시 경계로 모인다
    return WindowPlan("window", args)


#: 비교 기간의 성격(plans/134 W6 A-1 · 교정 2) — 진행 중(기준 시각까지) · 롤링(「최근 N일」) ·
#: 지난 기간.
_IN_PROGRESS, _ROLLING, _PAST = "in_progress", "rolling", "past"


@dataclass(frozen=True)
class _Period:
    """비교 질의의 개별 기간 — 반개구간 `[start, end)` · 단위(모르면 None) · 성격."""

    start: datetime
    end: datetime
    unit: str | None
    kind: str


def _period_kind(res: Any, spec: Any, anchor: datetime) -> str:
    """기간 성격 — 진행 중 = 해석 완결성 `to_date` 또는 끝이 기준 시각 **뒤**(끝 = 기준 시각인
    기간은 지난 기간이다 · V6-1). 롤링 = 해석 완결성 `rolling`(「최근 3시간」) 또는 「최근·지난
    N단위」(N ≥ 2 — 「최근 7일」 · V6-4). 「지난주」·「어제」(N = 1)는 지난 기간이다."""
    if res.completeness == "to_date" or (res.end is not None and res.end > anchor):
        return _IN_PROGRESS
    if res.completeness == "rolling" or (
            spec is not None and spec.relation == "last" and (spec.n or 1) > 1):
        return _ROLLING
    return _PAST


def _period_list(query_time: QueryTime) -> list[_Period] | None:
    """해석된 기간 목록 — 비교 질의의 개별 기간(plans/134 W6 A-1).

    해석기는 여러 기간을 덮는 구간 하나(`multiple_periods`)만 싣는다. 개별 기간은 그 원문 스팬을
    공용 인식기(`recognize`)·해석기(`resolve`)로 같은 기준 시각에서 다시 계산한다(사본 금지 —
    D-131). 다시 계산한 기간들이 덮는 구간이 요청 해석과 다르면 쓰지 않는다(None — 되묻기).
    단위는 인식한 명세의 단위(「이번 주」 = week)이고 모르면 None이다.
    """
    event = query_time.event
    if event is None or event.unbounded or event.start is None or event.end is None:
        return None
    anchor = query_time.anchor_at
    found: list[tuple[_Period, Any]] = []
    for spec in (s for s in recognize(event.span or "") if s.relation != "none"):
        try:
            res = resolve(spec, anchor, subject="event")
        except TimeSpecError:
            found = []
            break
        if res.start is None or res.end is None or res.unbounded:
            found = []
            break
        found.append((_Period(res.start, res.end, spec.unit, _period_kind(res, spec, anchor)),
                      spec))
    if NOTE_MULTIPLE_PERIODS not in event.notes:
        same = len(found) == 1 and (found[0][0].start, found[0][0].end) == (event.start,
                                                                            event.end)
        matched = found[0][1] if same else None
        return [_Period(event.start, event.end, matched.unit if matched is not None else None,
                        _period_kind(event, matched, anchor))]
    periods = [p for p, _ in found]
    if len(periods) < 2 or (min(p.start for p in periods), max(p.end for p in periods)) != (
            event.start, event.end):
        return None
    return periods


def _stamp(at: datetime) -> str:
    return f"{at:%Y-%m-%d %H:%M}"


#: 분 단위 기간을 넓혔을 때 비교 안내에 붙이는 문구(plans/159 · D-328 ③).
_MINUTE_WIDENED_RULE = "시 단위 통계라 분 단위 기간을 정시 구간으로 넓혀"


def _is_hour_rolling(period: _Period) -> bool:
    """시 단위 롤링(「최근 3시간」·「3시간 전」)인가 — 사건 해석이 기준 시각 분 경계에서 끝난다
    (plans/156 G-3 · D-333). 제니퍼 창은 종전처럼 통계 해석(직전 정시 끝)을 쓴다."""
    return period.unit == "hour" and period.kind == _ROLLING


def _hour_stats_period(period: _Period, anchor: datetime) -> _Period:
    """분 단위·시 단위 롤링 기간 → 시 단위 통계 구간 — 통계 해석(직전 정시에서 끝나는 ⌈N/60⌉시간 ·
    D-328 ③)을 공개 해석기로 다시 계산한다(사본 금지 — D-131). 비교 도구가 시 단위 통계라 분 끝을
    정시로 깎으면 구간이 뒤집히거나(10:07 ~ 10:00 → 되묻기) 게이트웨이의 시 경계 확장에서 구간이
    겹친다. 시 단위 롤링(N시간 = 60N분)은 통계 해석 「최근 N시간」과 같은 구간이 된다
    (plans/156 G-3).
    """
    minutes = int((period.end - period.start).total_seconds() // 60)
    res = resolve(TimeSpec(relation="last", n=minutes, unit="minute"), anchor, subject="metric")
    assert res.start is not None and res.end is not None  # last는 양 끝이 있다
    return _Period(res.start, res.end, period.unit, period.kind)


def plan_compare_window(query_time: QueryTime | None) -> WindowPlan:
    """두 기간 비교 창 — 기준(baseline)·비교(current) 구간을 해석에서 결정적으로 정한다(A-1).

    - **기간 2개**: 이른 구간 = 기준, 늦은 구간 = 비교. 셋 이상이면 되묻는다.
    - **기간 1개 · 진행 중**(「이번 주」 — 끝이 기준 시각 뒤이거나 기준 시각까지인 기간): 그
      기간이 비교이고 기준은 한 단위 앞(지난주) 시작부터 **같은 경과 시간**이다. 앞 단위가 더
      짧아 같은 경과 시간이 이번 기간 시작을 넘으면 기준 끝을 이번 기간 시작에서 자른다(앞 기간
      전체 · 겹침 없음 — V6-1).
    - **기간 1개 · 롤링**(「최근 7일」·「최근 3시간」): 그 기간이 비교이고 기준은 **바로 앞 같은
      길이** 구간이다(`start − 길이 ~ start` · V6-4). 분 단위(「최근 30분」)는 비교 도구가 시 단위
      통계라 통계 해석대로 정시 구간으로 넓힌다(D-328 ③ — 「최근 30분」 = 직전 정시 1시간). 기간
      2개 중 분 단위가 있어도 같다 — 넓힌 뒤 겹치면 그 사유로 되묻는다(`COMPARE_OVERLAP_NOTE`).
    - **기간 1개 · 지난 기간**(「지난주 대비」 — 끝이 기준 시각과 같아도 지난 기간이다): 그 기간이
      기준이고 비교는 바로 뒤 같은 길이 구간을 기준 시각까지(이번 주 지금까지)다.
    - **길이 맞춤**: 진행 중인 구간의 끝은 기준 시각의 정시다(시 단위 통계라 진행 중인 시간대는
      뺀다). 비교 구간이 기준보다 짧으면 기준을 **같은 경과 시간**으로 자른다(「이번 주 지금까지 vs
      지난주 같은 경과 시간」 — 합계를 같은 길이로 비교한다). 둘 다 지난 기간이면 자르지 않는다
      (길이가 다르면 게이트웨이가 고지한다).
    - 기간이 없거나 해석이 없으면 되묻는다(`COMPARE_ASK_NOTE`). 비교 구간의 경과 시간이 0이면(정시
      직후 「지난주 대비」) 사유를 따로 말하고 되묻는다(`COMPARE_ELAPSED_NOTE` · V6-5).
    계산(가중 평균·증감·기준 0 = N/A)은 게이트웨이가 한다 — 본체는 구간만 정한다.
    """
    periods = _period_list(query_time) if query_time is not None else None
    if not periods or len(periods) > 2 or query_time is None:
        return WindowPlan("ask", note=COMPARE_ASK_NOTE)
    hour = floor_to_unit(query_time.anchor_at, "hour")
    rule = ""
    rolling = False
    if len(periods) == 2:
        widened = any(p.unit == "minute" for p in periods)
        if widened:
            periods = [_hour_stats_period(p, query_time.anchor_at) if p.unit == "minute" else p
                       for p in periods]
        # 시 단위 롤링도 통계 구간으로(plans/156 G-3 — 종전 구간 · 넓힘 고지 없음)
        periods = [_hour_stats_period(p, query_time.anchor_at) if _is_hour_rolling(p) else p
                   for p in periods]
        first, last = sorted(periods, key=lambda p: (p.start, p.end))
        if widened and first.end > last.start:  # 넓힌 뒤 겹침(같은 시작 포함) — 사유를 말한다
            return WindowPlan("ask", note=(
                f"{COMPARE_OVERLAP_NOTE}({_stamp(first.start)} ~ {_stamp(first.end)} · "
                f"{_stamp(last.start)} ~ {_stamp(last.end)})"))
        if first.start == last.start:
            return WindowPlan("ask", note=COMPARE_ASK_NOTE)
        base, cur = (first.start, first.end), (last.start, min(last.end, hour))
        in_progress = last.kind == _IN_PROGRESS
        if widened:
            rule = f"{_MINUTE_WIDENED_RULE} 비교"
    else:
        (period,) = periods
        start, end = period.start, period.end
        in_progress = period.kind == _IN_PROGRESS
        if in_progress:  # 그 기간이 비교, 기준은 한 단위 앞 같은 경과 시간(이번 기간 시작에서 자름)
            cur = (start, hour)
            elapsed = cur[1] - cur[0]
            back = (shift_unit(start, period.unit, -1)
                    if period.unit is not None and period.unit in UNITS else start - elapsed)
            base = (back, min(back + elapsed, start))
            rule = "앞 기간의 같은 경과 시간과 비교"
            if back + elapsed > start:
                rule = "앞 기간이 더 짧아 앞 기간 전체와 비교"
        elif period.kind == _ROLLING:  # 그 기간이 비교, 기준은 바로 앞 같은 길이
            rule = "요청 기간과 바로 앞 같은 길이 기간을 비교"
            if period.unit == "minute":  # 분 구간은 시 단위 통계 구간으로(`_hour_stats_period`)
                hourly = _hour_stats_period(period, query_time.anchor_at)
                start, end = hourly.start, hourly.end
                rule = f"{_MINUTE_WIDENED_RULE} 바로 앞 같은 길이 기간과 비교"
            elif _is_hour_rolling(period):  # 시 단위 롤링도 통계 구간으로(plans/156 G-3)
                hourly = _hour_stats_period(period, query_time.anchor_at)
                start, end = hourly.start, hourly.end
            cur = (start, min(end, hour))
            base = (start - (cur[1] - cur[0]), start)
            rolling = True
        else:  # 지난 기간 — 그 기간이 기준, 비교는 바로 뒤 같은 길이를 기준 시각까지
            base = (start, end)
            cur = (end, min(end + (end - start), hour))
            in_progress = cur[1] < end + (end - start)
            rule = "바로 뒤 기간(지금까지)과 비교"
    elapsed = cur[1] - cur[0]
    if elapsed <= timedelta(0):
        return WindowPlan("ask", note=COMPARE_ELAPSED_NOTE if in_progress else COMPARE_ASK_NOTE)
    if in_progress and base[1] - base[0] > elapsed:
        base = (base[0], base[0] + elapsed)
        rule = (f"{rule} · " if rule else "") + "기준 구간을 같은 경과 시간으로 맞춤"
    args = {"current_start": cur[0].isoformat(timespec="seconds"),
            "current_end": cur[1].isoformat(timespec="seconds"),
            "baseline_start": base[0].isoformat(timespec="seconds"),
            "baseline_end": base[1].isoformat(timespec="seconds")}
    note = (f"기준 {_stamp(base[0])} ~ {_stamp(base[1])} · 비교 {_stamp(cur[0])} ~ {_stamp(cur[1])}"
            + (f"({rule})" if rule else "")
            + (" · 진행 중인 시간대는 시 단위 통계라 뺐습니다"
               if (in_progress or rolling) and cur[1] == hour and query_time.anchor_at > hour
               else ""))
    return WindowPlan("window", args, note=note, kind=NOTE_TRACE)


def auto_interval(view: ViewSpec, plan: WindowPlan,
                  given: dict[str, Any] | None) -> tuple[WindowPlan, str]:
    """추세 간격 자동 선택(plans/134 W6 M-7) — (창, 고지 문구 · 없으면 빈 값).

    사용자가 간격을 말했으면(검증된 조건에 있으면) 그대로 둔다 — 긴 구간이라고 바꾸지 않는다.
    말하지 않았고 구간이 하루보다 길고 보기가 간격 조건을 받으면, 검증된 허용값
    (`ViewArgSpec.verified_values`) 중 점 수가 하루치(288) 이하가 되는 가장 작은 값을 창 인자에
    둔다(보기 단위 — 배치 1호출 유지). 허용값이 선언되지 않았으면 고르지 않고 그 사실을 알린다.
    """
    spec = next((a for a in view.args if a.name == _INTERVAL_ARG), None)
    minutes = plan.args.get("lookback_minutes")
    if (spec is None or plan.mode != "window" or not isinstance(minutes, int)
            or minutes <= _AUTO_INTERVAL_AFTER_MINUTES
            or (given or {}).get(spec.tool_arg or spec.name) is not None):
        return plan, ""
    if not spec.verified_values:
        return plan, ("추세 간격 미지정 — 허용 간격 값이 확인되지 않아(W10) 자동으로 고르지 않고"
                      " 기본 간격으로 조회했습니다 · 긴 구간이면 「1시간 간격」처럼 간격을"
                      " 말해 주세요")
    fits = [v for v in spec.verified_values if minutes / v <= _AUTO_INTERVAL_POINTS]
    value = fits[0] if fits else spec.verified_values[-1]
    args = {**plan.args, spec.tool_arg or spec.name: value}
    return (WindowPlan(plan.mode, args, plan.note, plan.kind),
            f"추세 간격 미지정 — 조회 구간 {minutes:,}분에 맞춰 {value}분 간격으로 조회했습니다")
