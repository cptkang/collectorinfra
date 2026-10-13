"""결과 값 위치 계산(plans/160 C-1 · D-327) — 표의 셀 하나가 표 안에서 어디쯤인지 코드가 계산한다.

**무엇인가.** 사용자가 결과 표의 셀을 누르면 그 값의 위치 사실(fact)을 결정적으로 계산한다. 숫자는
전부 여기서 나온다 — 뒤 단계 LLM은 이 사실을 문장으로 옮기기만 하고 계산하지 않는다(비결정성
가드). 정상/위험 판정·원인 추정은 하지 않는다.

**사실 어휘(고정 key).**

- 수치 칼럼: `rank`(큰 값 순 n/N · 동률은 같은 순위) · `is_max`/`is_min`(해당 시만) ·
  `vs_mean_pct` · `vs_median_pct` · `series_rank`(시리즈 분할 칼럼이 있을 때 그 행 범주 안 순위) ·
  `trend`(시간 축이 있으면 같은 시리즈 안 시간순 직전 3개 점 평균 대비 상승/하락/평탄 — 평탄 ±1%) ·
  `related_ratio`(같은 행의 단위가 같아 보이는 다른 수치 칼럼 대비 비율).
- 비수치 칼럼: `frequency`(표 안 같은 값 건수/N) · `row_time`(그 행의 시간 칼럼 값) ·
  `row_ident`(그 행의 첫 범주 칼럼 값 — 그 칼럼 자신이면 생략).

**관련 칼럼 고르기는 이름을 보지 않는다.** 값 모양만 본다 — 두 칼럼 모두 음수 없음 · 한쪽이 다른
쪽 이하인 행이 95% 이상 · 두 중앙값의 자릿수 차 ≤ 1. 이름은 「마지막 `_` 뒤 토큰이 둘 다 있으면
같아야 한다」만 비교한다(예: `col_a_x`와 `col_b_x`는 통과, `col_a_x`와 `col_b_y`는 생략 — 토큰
목록을 두지 않는다). 후보가 여럿이면 비율이 1에 가장 가까운 하나, 확신이 없으면 생략한다.

계층: domain — 순수 · 표준 라이브러리만 · I/O·LLM·설정 0.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from src.domain.result_chart import (
    ChartSpec,
    ColumnKind,
    category_key,
    classify_columns,
    detect_chart,
    is_blank,
    parse_number,
    time_value_to_ms,
)

#: 추세 비교에 쓰는 직전 점 수.
TREND_WINDOW = 3
#: 평탄 판정 폭(%).
TREND_FLAT_PCT = 1.0
#: 관련 칼럼 판정 — 한쪽이 다른 쪽 이하인 행 비율 하한.
_RELATED_ORDER_RATIO = 0.95
#: 관련 칼럼 판정 — 두 중앙값의 자릿수(log10) 차 상한.
_RELATED_MAGNITUDE = 1.0


def _fmt(num: float) -> str:
    """표시용 숫자 — 정수면 쉼표, 아니면 소수 1자리."""
    if float(num).is_integer():
        return f"{int(num):,}"
    return f"{num:,.1f}"


def _json_value(value: Any) -> Any:
    """원값을 JSON 안전한 표시용 값으로."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        num = parse_number(value)
        return num if num is not None else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _rank(values: list[float], target: float) -> int:
    """큰 값 순 순위(동률은 같은 순위)."""
    return 1 + sum(1 for v in values if v > target)


def _pct(target: float, base: float) -> float | None:
    if base == 0:
        return None
    return round((target - base) / abs(base) * 100, 1)


def _suffix(name: str) -> str | None:
    return name.rsplit("_", 1)[1].lower() if "_" in name else None


def _column_numbers(rows: list[dict[str, Any]], col: str) -> list[float]:
    return [n for row in rows if (n := parse_number(row.get(col))) is not None]


def _related_ratio(
    rows: list[dict[str, Any]],
    kinds: dict[str, ColumnKind],
    column: str,
    row_index: int,
    target: float,
) -> dict[str, Any] | None:
    """같은 행의 단위가 같아 보이는 다른 수치 칼럼 대비 비율 — 확신 없으면 None."""
    own = _column_numbers(rows, column)
    if not own or min(own) < 0:
        return None
    own_median = statistics.median(own)
    own_suffix = _suffix(column)
    best: tuple[float, str, float] | None = None
    for other, kind in kinds.items():
        if other == column or kind != "numeric":
            continue
        other_suffix = _suffix(other)
        if own_suffix and other_suffix and own_suffix != other_suffix:
            continue
        other_vals = _column_numbers(rows, other)
        if not other_vals or min(other_vals) < 0:
            continue
        other_median = statistics.median(other_vals)
        if own_median <= 0 or other_median <= 0:
            continue
        if abs(math.log10(own_median) - math.log10(other_median)) > _RELATED_MAGNITUDE:
            continue
        pairs = [
            (a, b)
            for row in rows
            if (a := parse_number(row.get(column))) is not None
            and (b := parse_number(row.get(other))) is not None
        ]
        if len(pairs) < 2:
            continue
        le = sum(1 for a, b in pairs if a <= b) / len(pairs)
        ge = sum(1 for a, b in pairs if a >= b) / len(pairs)
        if le < _RELATED_ORDER_RATIO and ge < _RELATED_ORDER_RATIO:
            continue
        other_value = parse_number(rows[row_index].get(other))
        if other_value is None or other_value == 0:
            continue
        ratio = target / other_value
        if best is None or abs(ratio - 1) < abs(best[0] - 1):
            best = (ratio, other, other_value)
    if best is None:
        return None
    ratio, other, other_value = best
    pct = round(ratio * 100, 1)
    return {
        "key": "related_ratio",
        "label": f"같은 행 {other}({_fmt(other_value)}) 대비 {_fmt(pct)}%",
        "value": pct,
    }


def _series_key(row: dict[str, Any], spec: ChartSpec | None) -> str | None:
    if spec is None or spec.series_by is None:
        return None
    raw = row.get(spec.series_by)
    return None if is_blank(raw) else category_key(raw)


def _time_column(kinds: dict[str, ColumnKind], spec: ChartSpec | None) -> str | None:
    if spec is not None and spec.x_type == "time":
        return spec.x
    return next((c for c, k in kinds.items() if k == "time"), None)


def _trend(
    rows: list[dict[str, Any]], column: str, row_index: int, time_col: str, spec: ChartSpec | None
) -> dict[str, Any] | None:
    """같은 시리즈 안 시간순 직전 TREND_WINDOW개 점 평균 대비 방향."""
    target_row = rows[row_index]
    target_ms = time_value_to_ms(target_row.get(time_col))
    target = parse_number(target_row.get(column))
    if target_ms is None or target is None:
        return None
    series = _series_key(target_row, spec)
    points: list[tuple[int, int, float]] = []
    for idx, row in enumerate(rows):
        if idx == row_index or _series_key(row, spec) != series:
            continue
        ms = time_value_to_ms(row.get(time_col))
        num = parse_number(row.get(column))
        if ms is None or num is None:
            continue
        if (ms, idx) < (target_ms, row_index):
            points.append((ms, idx, num))
    if not points:
        return None
    previous = [p[2] for p in sorted(points)[-TREND_WINDOW:]]
    base = statistics.fmean(previous)
    pct = _pct(target, base)
    if pct is None:
        direction = "평탄" if target == base else ("상승" if target > base else "하락")
        detail = ""
    else:
        direction = "평탄" if abs(pct) <= TREND_FLAT_PCT else ("상승" if pct > 0 else "하락")
        detail = f" {pct:+.1f}%"
    return {
        "key": "trend",
        "label": f"직전 {len(previous)}개 점 평균({_fmt(base)}) 대비{detail} {direction}",
        "value": direction,
    }


def _numeric_facts(
    rows: list[dict[str, Any]],
    kinds: dict[str, ColumnKind],
    column: str,
    row_index: int,
    target: float,
    spec: ChartSpec | None,
) -> list[dict[str, Any]]:
    values = _column_numbers(rows, column)
    total = len(values)
    rank = _rank(values, target)
    facts: list[dict[str, Any]] = [
        {
            "key": "rank",
            "label": f"전체 {total}건 중 큰 값 순 {rank}위",
            "value": f"{rank}/{total}",
        },
    ]
    high, low = max(values), min(values)
    if total >= 2 and high != low:
        if target == high:
            facts.append({"key": "is_max", "label": "표 안 최고값", "value": True})
        if target == low:
            facts.append({"key": "is_min", "label": "표 안 최저값", "value": True})
    mean = statistics.fmean(values)
    if (pct := _pct(target, mean)) is not None:
        facts.append(
            {
                "key": "vs_mean_pct",
                "label": f"평균({_fmt(mean)}) 대비 {pct:+.1f}%",
                "value": pct,
            }
        )
    median = statistics.median(values)
    if (pct := _pct(target, median)) is not None:
        facts.append(
            {
                "key": "vs_median_pct",
                "label": f"중앙값({_fmt(median)}) 대비 {pct:+.1f}%",
                "value": pct,
            }
        )
    series = _series_key(rows[row_index], spec)
    if spec is not None and spec.series_by not in (None, column) and series is not None:
        in_series = [
            n
            for row in rows
            if _series_key(row, spec) == series and (n := parse_number(row.get(column))) is not None
        ]
        s_rank = _rank(in_series, target)
        facts.append(
            {
                "key": "series_rank",
                "label": f"{series} 안 {len(in_series)}건 중 큰 값 순 {s_rank}위",
                "value": f"{s_rank}/{len(in_series)}",
            }
        )
    time_col = _time_column(kinds, spec)
    if time_col is not None and (trend := _trend(rows, column, row_index, time_col, spec)):
        facts.append(trend)
    if related := _related_ratio(rows, kinds, column, row_index, target):
        facts.append(related)
    return facts


def _other_facts(
    rows: list[dict[str, Any]],
    kinds: dict[str, ColumnKind],
    column: str,
    row_index: int,
    spec: ChartSpec | None,
) -> list[dict[str, Any]]:
    row = rows[row_index]
    value = row.get(column)
    facts: list[dict[str, Any]] = []
    total = len(rows)
    if not is_blank(value):
        key = category_key(value)
        count = sum(
            1 for r in rows if not is_blank(r.get(column)) and category_key(r.get(column)) == key
        )
        facts.append(
            {
                "key": "frequency",
                "label": f"표 {total}건 중 같은 값 {count}건",
                "value": f"{count}/{total}",
            }
        )
    time_col = _time_column(kinds, spec)
    if time_col is not None and time_col != column and not is_blank(row.get(time_col)):
        shown = _json_value(row.get(time_col))
        facts.append(
            {"key": "row_time", "label": f"이 행의 시각 {time_col}={shown}", "value": shown}
        )
    ident_col = next((c for c, k in kinds.items() if k == "category"), None)
    if ident_col is not None and ident_col != column and not is_blank(row.get(ident_col)):
        shown = _json_value(row.get(ident_col))
        facts.append({"key": "row_ident", "label": f"이 행의 {ident_col}={shown}", "value": shown})
    return facts


def value_context(
    rows: list[dict[str, Any]], column: str, row_index: int, *, spec: ChartSpec | None = None
) -> dict[str, Any]:
    """셀(column, row_index) 하나의 위치 사실을 계산한다(JSON 안전).

    spec을 주지 않으면 같은 행으로 `detect_chart`를 돌려 시리즈 분할·시간 축을 정한다 — 시리즈를
    섞은 순위·추세가 나오지 않게 하기 위해서다. column이 행 키에 없거나 row_index가 범위 밖이면
    ValueError.
    """
    if not 0 <= row_index < len(rows):
        raise ValueError(f"row_index 범위 밖: {row_index} (행 {len(rows)}개)")
    kinds = classify_columns(rows)
    if column not in kinds:
        raise ValueError(f"결과에 없는 칼럼: {column}")
    if spec is None:
        spec = detect_chart(rows)
    kind = kinds[column]
    raw = rows[row_index].get(column)
    target = parse_number(raw) if kind == "numeric" else None
    if target is not None:
        facts = _numeric_facts(rows, kinds, column, row_index, target, spec)
    else:
        facts = _other_facts(rows, kinds, column, row_index, spec)
    return {
        "column": column,
        "row_index": row_index,
        "value": _json_value(raw),
        "kind": kind,
        "facts": facts,
    }
