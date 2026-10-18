"""결정적 답 줄 렌더 · 신호 정리 · 표시용 행.

plans/180 W1 — `src/orchestration/apm_query.py`에서 순수 이동(본문·주석·평가 순서 불변).
옛 경로 `src.orchestration.apm_query`가 같은 이름으로 재노출한다(D-337 ① shim).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.orchestration.apm.common import DISPLAY_CELL_MAX


def display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """채팅 표·LLM 입력용 행 — 긴 문자열 셀만 `DISPLAY_CELL_MAX`자로 줄인다(전문은 CSV·결과 파일).

    줄일 셀이 없는 행은 같은 객체 그대로다(종전 바이트).
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if not any(isinstance(v, str) and len(v) > DISPLAY_CELL_MAX for v in row.values()):
            out.append(row)
            continue
        out.append({k: (f"{v[:DISPLAY_CELL_MAX]}…(총 {len(v):,}자 — 전체는 CSV)"
                        if isinstance(v, str) and len(v) > DISPLAY_CELL_MAX else v)
                    for k, v in row.items()})
    return out


_LEVEL_RANK = {"CRITICAL": 2, "WARNING": 1}
_CATEGORY_RANK = {"strong": 2, "medium": 1}


def _dedupe_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 `(kind, source_id, instance_id)`는 가장 강한 판정 1건만 남긴다.

    게이트웨이 계약(SPEC-apm-gateway §4)과 같은 규칙이다 — 여러 대상·보기의 판정을 모은 뒤
    다시 건다.
    """
    best: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
    for sig in signals:
        key = (sig.get("kind"), sig.get("source_id"), sig.get("instance_id"))
        rank = (_LEVEL_RANK.get(str(sig.get("level")), 0),
                _CATEGORY_RANK.get(str(sig.get("category")), 0))
        cur = best.get(key)
        if cur is None or rank > (_LEVEL_RANK.get(str(cur.get("level")), 0),
                                  _CATEGORY_RANK.get(str(cur.get("category")), 0)):
            best[key] = sig
    return list(best.values())


#: 게이트웨이 GUID 추적이 기간 없이 기본 창(앞 결과 시각 ±N분 · 최근 N분)을 쓸 때 `[한계]`에 싣는
#: 표지(SPEC-apm-gateway trace 창 규칙 · 계약 §4.2 「기본값이 쓰이면 고지가 답에 실린다」).
_TRACE_DEFAULT_WINDOW_MARK = "기간 미지정"


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _stats_text(stats: dict[str, Any]) -> str:
    """창 집계 한 줄 — 호출 수 · 오류 · 오류율 · p50 · p95(있는 값만)."""
    parts: list[str] = []
    calls, errors, rate = (_num(stats.get(k)) for k in ("calls", "errors", "error_rate"))
    if calls is not None:
        parts.append(f"호출 {calls:,.0f}건")
    if errors is not None:
        parts.append(f"오류 {errors:,.0f}건")
    if rate is not None:
        parts.append(f"오류율 {rate:.1%}")
    for key, name in (("response_time_p50_ms", "p50"), ("response_time_p95_ms", "p95")):
        value = _num(stats.get(key))
        if value is not None:
            parts.append(f"{name} {value:,.0f}ms")
    return " · ".join(parts)


def _hourly_text(hourly: dict[str, Any]) -> str:
    parts: list[str] = []
    calls, failures, rate = (_num(hourly.get(k)) for k in ("calls", "failures", "failure_rate"))
    avg, peak = _num(hourly.get("response_time_avg_ms")), _num(hourly.get("max_response_time_ms"))
    if calls is not None:
        parts.append(f"호출 {calls:,.0f}건")
    if failures is not None:
        parts.append(f"실패 {failures:,.0f}건")
    if rate is not None:
        parts.append(f"실패율 {rate:.1%}")
    if avg is not None:
        parts.append(f"평균 {avg:,.0f}ms")
    if peak is not None:
        parts.append(f"최대 {peak:,.0f}ms")
    span = ""
    if hourly.get("hour_start") and hourly.get("hour_end"):
        span = f"({hourly['hour_start']}~{hourly['hour_end']})"
    return f"시 단위 합계{span} " + " · ".join(parts) if parts else ""


def _answer_lines(meta: dict[str, Any]) -> list[str]:
    """결정적 판정·집계 줄(plans/134 M-1) — LLM 산문에 맡기지 않고 최종 답에 그대로 싣는다.

    판정(label·level·evidence) · 창 집계(호출 수·오류율·p50·p95) · 시 단위 합계 · 오류 유형별 건수 ·
    실행 중 서비스 수. 값은 게이트웨이가 계산한 그대로이고 상한 없이 모두 싣는다.
    """
    lines: list[str] = []
    for sig in meta.get("was_signals") or []:
        where = " ".join(str(x) for x in (sig.get("hostname"), (
            f"#{sig['instance_id']}" if sig.get("instance_id") is not None else None)) if x)
        head = f"판정 {sig.get('label') or sig.get('kind')}({sig.get('level')})"
        evidence = f": {sig['evidence']}" if sig.get("evidence") else ""
        lines.append(f"{head} — {where}{evidence}" if where else f"{head}{evidence}")
    for agg in meta.get("aggregates") or []:
        scope = agg.get("scope") or agg.get("view")
        fleet = _fleet_lines(agg, scope)
        if fleet is not None:  # 전 대상 순위·이벤트(plans/134 W3) — 다른 집계 줄과 섞지 않는다
            lines += fleet
            continue
        compare = _compare_line(agg, scope)
        if compare:  # 두 기간 비교(plans/134 W6 A-1) — 게이트웨이 값 그대로
            lines.append(compare)
            continue
        for win in agg.get("row_windows") or []:
            text = _stats_text(win)
            name = win.get("instance_name") or win.get("instance_id")
            if text:
                lines.append(f"{scope}{f' · {name}' if name else ''}: 구간 {text}")
        summary = agg.get("summary") if isinstance(agg.get("summary"), dict) else {}
        if _num(summary.get("failures")) is not None:
            # 시 단위 통계 합계(`apm_status_stats` — 실패·평균·최대 · 정시 경계로 넓힌 구간)
            text = _hourly_text({**summary, "hour_start": agg.get("hour_start"),
                                 "hour_end": agg.get("hour_end")})
            rows = _num(summary.get("row_count"))
            if text:
                lines.append(f"{scope}: {text}"
                             + (f" — 받은 {rows:,.0f}행의 합계" if rows is not None else ""))
        else:
            text = _stats_text(summary)
            if text:
                lines.append(f"{scope}: 구간 {text}")
        if _num(summary.get("total")) is not None:
            modes = summary.get("by_running_mode") or {}
            detail = " · ".join(f"{k} {v}건" for k, v in modes.items()) if isinstance(
                modes, dict) else ""
            lines.append(f"{scope}: 실행 중 {summary['total']}건"
                         + (f"(실행 모드별 {detail})" if detail else ""))
        hourly = agg.get("hourly") if isinstance(agg.get("hourly"), dict) else {}
        text = _hourly_text(hourly)
        if text:
            lines.append(f"{scope}: {text}")
        errors = [e for e in agg.get("errors_by_type") or []
                  if isinstance(e, dict) and e.get("error_type")]
        if errors:
            lines.append(f"{scope}: 오류 유형별 — " + " · ".join(
                f"{e['error_type']} {e.get('count', 0):,}건" for e in errors))
        if summary.get("guid") and _num(summary.get("transactions")) is not None:
            lines.append(_trace_line(summary, agg.get("window"), agg.get("window_notes") or []))
        lines += [_change_line(row) for row in agg.get("changes") or []]
    return lines


#: 순위 정렬 순서 표기(게이트웨이 `apm_fleet` 인자 값 → 결정적 줄 문구).
_ORDER_TEXT = {"desc": "내림차순", "asc": "오름차순"}


def _fleet_lines(agg: dict[str, Any], scope: Any) -> list[str] | None:
    """전 대상 순위·이벤트 집계 줄(plans/134 W3 · 계약 B-3) — 해당 집계가 아니면 None.

    - 순위(`summary.instances_ranked`): 「{지표} {순서} 순위 — 전체 인스턴스 N개 중 상위 n」 ·
      값 없는 인스턴스 수 · `provisional`이면 「잠정 순위 — 조회 실패 도메인 k곳 제외」(LLM
      산문에 맡기지 않는다).
    - 이벤트(`coverage`): 「이벤트 M건(표시 n건) · 도메인 D곳(버퍼 b · API a · 실패 f)」 — M은
      게이트웨이 `summary.events_total`(V34-5) · 실패 도메인이 있으면 「실패 도메인은 0건이 아니라
      확인하지 못함」.
    값은 게이트웨이가 계산한 그대로다.
    """
    raw_summary, coverage = agg.get("summary"), agg.get("coverage")
    summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
    count = _num(agg.get("row_count"))
    if isinstance(coverage, dict):
        parts = [f"버퍼 {_num(coverage.get('from_buffer')) or 0:,.0f}",
                 f"API {_num(coverage.get('from_api')) or 0:,.0f}"]
        mixed, failed = _num(coverage.get("mixed")) or 0, _num(coverage.get("failed")) or 0
        if mixed:
            parts.append(f"혼합 {mixed:,.0f}")
        parts.append(f"실패 {failed:,.0f}")
        # 건수는 조건에 맞은 전체(`summary.events_total`) — `n`으로 자른 표시 행 수와 구별(V34-5)
        found = _num(summary.get("events_total"))
        shown = f"(표시 {count:,.0f}건)" if found is not None and count is not None \
            and count != found else ""
        line = (f"{scope}: 이벤트 {found if found is not None else count or 0:,.0f}건{shown}"
                f" · 도메인 {_num(coverage.get('domains_total')) or 0:,.0f}곳"
                f"({' · '.join(parts)})")
        if failed:
            line += " — 실패 도메인은 0건이 아니라 확인하지 못함"
        return [line]
    if "instances_ranked" not in summary:
        return None
    order = str(summary.get("order") or "")
    period = summary.get("window_mode") == "period"
    kind = "순위"
    if period:  # 기간 순위(plans/134 W6 A-4 — 게이트웨이가 시 단위 통계를 모두 모은 뒤 정렬)
        span = _span_text(agg.get("window"), summary)
        kind = "기간 순위(시 단위 통계" + (f" · 조회 구간 {span}" if span else "") + ")"
    metric = summary.get("metric")
    if metric == "failure_rate":  # 게이트웨이 값은 0~1 비율 — 표의 값을 %로 읽게 단위를 밝힌다
        metric = "failure_rate(실패율 · 표의 값은 0~1 비율 — 0.05 = 5%)"
    head = (f"{scope}: {metric} {_ORDER_TEXT.get(order, order)} {kind} — 전체"
            f" 인스턴스 {_num(summary.get('instances_total')) or 0:,.0f}개 중 상위"
            f" {count or 0:,.0f}")
    unranked = _num(summary.get("instances_unranked")) or 0
    if unranked:
        head += f" · 값 없는 인스턴스 {unranked:,.0f}개는 순위에서 뺐습니다"
    lines = [head]
    if agg.get("period_requested") and not period:
        # 기간을 말했는데 기간으로 계산할 수 없는 지표(또는 기간 순위를 모르는 게이트웨이) — 현재값
        lines.append(f"{scope}: 요청 기간의 순위가 아니라 현재값 순위입니다 — 이 지표는 기간으로"
                     " 계산하지 못했습니다")
    if agg.get("provisional"):
        lines.append(f"{scope}: 잠정 순위 — 조회 실패 도메인"
                     f" {_num(summary.get('domains_failed')) or 0:,.0f}곳 제외")
    return lines


def _span_text(window: Any, summary: dict[str, Any]) -> str:
    """기간 순위의 조회 구간 — 게이트웨이가 시 경계로 넓혀 실제 조회한 `summary.queried` → 봉투
    `window`(start·end) → 요약 `hour_start`·`hour_end` 순(없으면 빈 값)."""
    for source, (a, b) in ((summary.get("queried"), ("start", "end")),
                           (window, ("start", "end")), (summary, ("hour_start", "hour_end")),
                           (summary.get("window"), ("start", "end"))):
        if isinstance(source, dict) and source.get(a) and source.get(b):
            return f"{source[a]} ~ {source[b]}"
    return ""


def _compare_line(agg: dict[str, Any], scope: Any) -> str:
    """두 기간 비교 한 줄(plans/134 W6 A-1 · `apm_period_compare` 요약) — 비교 요약이 아니면 빈 값.

    값·증감은 게이트웨이가 계산한 그대로다(가중 평균 · 실패율 차 = %p · 기준 0 = N/A · 한쪽 없음 =
    N/A — 0으로 채우지 않는다). 조회 구간은 게이트웨이가 시 경계로 맞춘 구간이다.
    """
    raw = agg.get("summary")
    summary: dict[str, Any] = raw if isinstance(raw, dict) else {}
    base, cur, delta = (summary.get(k) for k in ("baseline", "current", "delta"))
    if not (isinstance(base, dict) and isinstance(cur, dict) and isinstance(delta, dict)):
        return ""

    def span(part: dict[str, Any]) -> str:
        a, b = part.get("hour_start") or part.get("start"), part.get("hour_end") or part.get("end")
        return f"{a} ~ {b}" if a and b else "?"

    def value(part: dict[str, Any], key: str) -> str:
        num = _num(part.get(key))
        if num is None:
            return "N/A"
        if key == "failure_rate":
            return f"{num:.1%}"
        return f"{num:,.0f}ms" if key.endswith("_ms") else f"{num:,.0f}"

    def change(key: str) -> str:
        item = delta.get(key)
        if key == "failure_rate":
            num = _num(item.get("abs")) if isinstance(item, dict) else None
            return f"{num:+.1f}%p" if num is not None else "N/A"
        num = _num(item.get("pct")) if isinstance(item, dict) else None
        return f"{num:+.1f}%" if num is not None else "N/A"

    parts = [f"{label} {value(base, key)} → {value(cur, key)}({change(key)})"
             for key, label in (("calls", "호출"), ("failure_rate", "실패율"),
                                ("avg_response_ms", "평균 응답(호출 수 가중)"),
                                ("max_response_ms", "최대 응답"))]
    return (f"{scope}: 기준 {span(base)} → 비교 {span(cur)} — " + " · ".join(parts))


def _clock_text(ms: Any) -> str:
    """epoch ms → 이 서버 시간대의 ISO 시각(오프셋 포함 — 원천 시간대를 추측하지 않는다)."""
    value = _num(ms)
    if value is None or value <= 0:
        return ""
    return datetime.fromtimestamp(value / 1000).astimezone().isoformat(timespec="seconds")


def _trace_line(summary: dict[str, Any], window: Any = None,
                window_notes: list[str] | tuple[str, ...] = ()) -> str:
    """GUID 연계 거래 한 줄(plans/134 W5 · 계약 §4.3) — 게이트웨이 `summary` 값 그대로.

    「도메인 M곳」을 거래가 나온 도메인 수로 쓰고 조회·실패 도메인 수를 괄호에 둔다(조회 도메인 수만
    쓰면 「거쳐 간 도메인」으로 읽힌다). 조회 구간(봉투 `window`)과 기간을 말하지 않아 쓴 기본 창
    고지(`window_notes` — 게이트웨이 문구), 「같은 GUID일 뿐 호출 관계가 아님」은 0건이어도 싣는다
    (V-3 — 0건 답은 LLM을 거치지 않아 요약 입력의 `[한계]`가 답에 닿지 않는다).
    """
    count = _num(summary.get("transactions")) or 0
    queried, failed = _num(summary.get("domains_queried")), _num(summary.get("domains_failed"))
    hits = _num(summary.get("domains_with_hits"))
    head = f"GUID {summary.get('guid')}: 거래 {count:,.0f}건"
    if hits is not None:
        inner = " · ".join(p for p in (
            f"조회 {queried:,.0f}곳" if queried is not None else "",
            f"실패 {failed:,.0f}곳" if failed is not None else "") if p)
        head += f" · 도메인 {hits:,.0f}곳" + (f"({inner})" if inner else "")
    elif queried is not None:
        head += f" · 도메인 {queried:,.0f}곳" + (f"(실패 {failed:,.0f})" if failed is not None
                                              else "")
    first = _clock_text(summary.get("first_start_ms"))
    last = _clock_text(summary.get("last_end_ms"))
    if first or last:
        head += f" · {first or '?'} ~ {last or '?'}"
    span = (f"조회 구간 {window['start']} ~ {window['end']}"
            if isinstance(window, dict) and window.get("start") and window.get("end") else "")
    notes = "; ".join(window_notes)
    if span or notes:
        head += " · " + (f"{span}({notes})" if span and notes else span or f"조회 구간({notes})")
    head += " · 같은 GUID일 뿐 호출 관계가 아님"
    names = [str(n) for n in summary.get("instances") or [] if n not in (None, "")]
    if names:
        head += " · 인스턴스 " + ", ".join(names)
    return head


def _change_line(row: dict[str, Any]) -> str:
    """변경 감지 1건의 전후 비교 한 줄(plans/134 W6 A-2 · 계약 §4.3) — 값이 없으면 N/A.

    차이(`delta`)는 게이트웨이가 계산한 값을 그대로 쓴다(오류율 차이 = %p · 평균 응답 = %).
    """
    def part_of(key: str) -> dict[str, Any]:
        value = row.get(key)
        return value if isinstance(value, dict) else {}

    before, after, delta = part_of("before"), part_of("after"), part_of("delta")

    def diff(key: str, part: str) -> float | None:
        item = delta.get(key)
        return _num(item.get(part)) if isinstance(item, dict) else None

    def rate(value: Any) -> str:
        num = _num(value)
        return f"{num:.1%}" if num is not None else "N/A"

    def millis(value: Any) -> str:
        num = _num(value)
        return f"{num:,.0f}ms" if num is not None else "N/A"

    def count(value: Any) -> str:
        num = _num(value)
        return f"{num:,.0f}" if num is not None else "N/A"

    point, pct = diff("error_rate", "abs"), diff("avg_response_ms", "pct")
    point_text = f"{point:+.1f}%p" if point is not None else "N/A"
    pct_text = f"{pct:+.1f}%" if pct is not None else "N/A"
    name = row.get("instance_name") or row.get("instance_id") or "인스턴스"
    when = row.get("change_detected_at")
    when = when if isinstance(when, str) and when else _clock_text(row.get("change_detected_ms"))
    return (f"{name} 변경 감지 {when} — "
            f"오류율 {rate(before.get('error_rate'))} → {rate(after.get('error_rate'))}"
            f"({point_text}) · "
            f"평균 응답 {millis(before.get('avg_response_ms'))} → "
            f"{millis(after.get('avg_response_ms'))}({pct_text})"
            f" · 오류 기록 {count(before.get('error_records'))} →"
            f" {count(after.get('error_records'))}"
            f" · 호출 {count(before.get('calls'))} → {count(after.get('calls'))}")
