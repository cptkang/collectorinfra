"""plans/160 W1 C-1 — 결과 값 위치 계산(`src/domain/result_value_context.py`) 단위 테스트.

칼럼 이름은 판정에 쓰이지 않으므로 중립 이름(`col_a`·`value` 등)만 쓴다.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from src.domain.result_chart import ChartSpec, chart_rows
from src.domain.result_value_context import value_context
from tests.test_domain.test_plan160_result_chart import nested_rows

_BASE = datetime(2026, 10, 13, 9, 0, 0)


def _facts(result: dict[str, Any]) -> dict[str, Any]:
    return {f["key"]: f["value"] for f in result["facts"]}


def test_rank_ties_share_rank() -> None:
    rows = [{"name": n, "value": v} for n, v in (("a", 10), ("b", 30), ("c", 30), ("d", 5))]
    assert _facts(value_context(rows, "value", 1))["rank"] == "1/4"
    assert _facts(value_context(rows, "value", 2))["rank"] == "1/4"
    assert _facts(value_context(rows, "value", 0))["rank"] == "3/4"


def test_is_max_and_is_min() -> None:
    rows = [{"name": n, "value": v} for n, v in (("a", 10.5), ("b", 30.5), ("c", 5.5))]
    top = _facts(value_context(rows, "value", 1))
    bottom = _facts(value_context(rows, "value", 2))
    middle = _facts(value_context(rows, "value", 0))
    assert top.get("is_max") is True and "is_min" not in top
    assert bottom.get("is_min") is True and "is_max" not in bottom
    assert "is_max" not in middle and "is_min" not in middle


def test_vs_mean_and_median_pct() -> None:
    rows = [{"name": n, "value": v} for n, v in (("a", 10.5), ("b", 20.5), ("c", 60.5))]
    facts = _facts(value_context(rows, "value", 2))
    # 평균 30.5 · 중앙값 20.5
    assert facts["vs_mean_pct"] == round((60.5 - 30.5) / 30.5 * 100, 1)
    assert facts["vs_median_pct"] == round((60.5 - 20.5) / 20.5 * 100, 1)


def test_decimal_and_value_display() -> None:
    rows = [{"name": "a", "value": Decimal("1.5")}, {"name": "b", "value": Decimal("2.5")}]
    result = value_context(rows, "value", 1)
    assert result["value"] == 2.5 and result["kind"] == "numeric"
    assert _facts(result)["rank"] == "1/2"


def test_series_rank_within_category() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=m), "inst": inst, "value": v}
        for m, (inst, v) in enumerate([("i1", 5.5), ("i2", 100.5), ("i1", 7.5), ("i2", 50.5)])
    ]
    spec = ChartSpec(
        kind="line", x="ts", y=("value",), series_by="inst", x_type="time", series_total=2
    )
    facts = _facts(value_context(rows, "value", 2, spec=spec))
    assert facts["series_rank"] == "1/2"
    assert facts["rank"] == "3/4"


def test_series_rank_detected_without_spec() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=m), "inst": inst, "value": v}
        for m, (inst, v) in enumerate([("i1", 5.5), ("i2", 100.5), ("i1", 7.5), ("i2", 50.5)])
    ]
    assert _facts(value_context(rows, "value", 2))["series_rank"] == "1/2"


def _trend_rows(last: float) -> list[dict[str, Any]]:
    values = [10.5, 10.0, 10.0, 9.5, last]
    return [{"ts": _BASE + timedelta(minutes=i), "value": v} for i, v in enumerate(values)]


@pytest.mark.parametrize(("last", "expected"), [(12.0, "상승"), (8.0, "하락"), (9.9, "평탄")])
def test_trend_against_previous_three(last: float, expected: str) -> None:
    # 직전 3개 평균 = (10.0 + 10.0 + 9.5) / 3 ≈ 9.83
    assert _facts(value_context(_trend_rows(last), "value", 4))["trend"] == expected


def test_trend_uses_time_order_not_row_order_and_same_series() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=3), "inst": "i1", "value": 20.5},
        {"ts": _BASE + timedelta(minutes=1), "inst": "i1", "value": 10.5},
        {"ts": _BASE + timedelta(minutes=2), "inst": "i2", "value": 900.5},
        {"ts": _BASE + timedelta(minutes=2), "inst": "i1", "value": 10.5},
        {"ts": _BASE + timedelta(minutes=3), "inst": "i2", "value": 1.5},
    ]
    assert _facts(value_context(rows, "value", 0))["trend"] == "상승"


def test_trend_absent_for_first_point_and_without_time() -> None:
    assert "trend" not in _facts(value_context(_trend_rows(12.0), "value", 0))
    rows = [{"name": n, "value": v} for n, v in (("a", 1.5), ("b", 2.5))]
    assert "trend" not in _facts(value_context(rows, "value", 1))


def test_related_ratio_same_unit_token() -> None:
    rows = [
        {"name": f"h{i}", "col_a_mb": used, "col_b_mb": total}
        for i, (used, total) in enumerate([(300.0, 512.0), (400.0, 512.0), (450.0, 1024.0)])
    ]
    facts = _facts(value_context(rows, "col_a_mb", 1))
    assert facts["related_ratio"] == round(400 / 512 * 100, 1)


def test_related_ratio_picks_closest_to_one() -> None:
    rows = [
        {"name": f"h{i}", "value": v, "other_a": v * 1.1, "other_b": v * 3}
        for i, v in enumerate([100.0, 200.0, 300.0])
    ]
    facts = _facts(value_context(rows, "value", 0))
    assert facts["related_ratio"] == round(100 / 110 * 100, 1)


def test_related_ratio_skipped_on_unit_token_mismatch() -> None:
    rows = [
        {"name": f"h{i}", "col_a_mb": used, "col_b_ms": total}
        for i, (used, total) in enumerate([(300.0, 512.0), (400.0, 512.0)])
    ]
    assert "related_ratio" not in _facts(value_context(rows, "col_a_mb", 0))


def test_related_ratio_skipped_on_magnitude_gap() -> None:
    rows = [
        {"name": f"h{i}", "col_a_mb": small, "col_b_mb": big}
        for i, (small, big) in enumerate([(3.0, 5120.0), (4.0, 6000.0)])
    ]
    assert "related_ratio" not in _facts(value_context(rows, "col_a_mb", 0))


def test_related_ratio_skipped_without_consistent_order_or_with_negatives() -> None:
    crossing = [
        {"name": f"h{i}", "col_a": a, "col_b": b}
        for i, (a, b) in enumerate([(10.0, 20.0), (30.0, 15.0), (12.0, 25.0)])
    ]
    assert "related_ratio" not in _facts(value_context(crossing, "col_a", 0))
    negative = [
        {"name": f"h{i}", "col_a": a, "col_b": b}
        for i, (a, b) in enumerate([(10.0, 20.0), (-1.0, 15.0), (12.0, 25.0)])
    ]
    assert "related_ratio" not in _facts(value_context(negative, "col_a", 0))


def test_non_numeric_frequency_time_and_ident() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=i), "name": f"h{i % 2}", "status": s, "value": 1.5}
        for i, s in enumerate(["up", "down", "up", "up"])
    ]
    result = value_context(rows, "status", 2)
    facts = _facts(result)
    assert result["kind"] == "category"
    assert facts["frequency"] == "3/4"
    assert facts["row_time"] == (_BASE + timedelta(minutes=2)).isoformat()
    assert facts["row_ident"] == "h0"


def test_row_ident_omitted_for_its_own_column() -> None:
    rows = [{"name": "a", "value": 1.5}, {"name": "b", "value": 2.5}]
    facts = _facts(value_context(rows, "name", 0))
    assert facts["frequency"] == "1/2" and "row_ident" not in facts


def test_time_column_cell_has_no_row_time() -> None:
    rows = [{"ts": _BASE + timedelta(minutes=i), "name": "a", "value": 1.5} for i in range(2)]
    result = value_context(rows, "ts", 0)
    assert result["kind"] == "time" and "row_time" not in _facts(result)


def test_invalid_column_and_row_raise_value_error() -> None:
    rows = [{"name": "a", "value": 1.5}, {"name": "b", "value": 2.5}]
    with pytest.raises(ValueError):
        value_context(rows, "missing", 0)
    with pytest.raises(ValueError):
        value_context(rows, "value", 2)
    with pytest.raises(ValueError):
        value_context(rows, "value", -1)


def test_output_json_serializable() -> None:
    rows = [
        {
            "ts": _BASE + timedelta(minutes=i),
            "inst": f"i{i % 2}",
            "col_a_mb": Decimal((i + 1) ** 2),
            "col_b_mb": Decimal((i + 1) ** 2 + 3),
            "status": "up",
        }
        for i in range(6)
    ]
    for col in ("ts", "inst", "col_a_mb", "status"):
        json.dumps(value_context(rows, col, 5))
    facts = _facts(value_context(rows, "col_a_mb", 5))
    assert {"rank", "vs_mean_pct", "series_rank", "trend", "related_ratio"} <= set(facts)


def test_related_ratio_on_flattened_nested_series() -> None:
    flat = chart_rows(nested_rows())
    facts = _facts(value_context(flat, "col_used_mb", 1))
    assert facts["related_ratio"] == 91.0
    assert facts["series_rank"] == "5/6"
    assert facts["trend"] == "상승"
