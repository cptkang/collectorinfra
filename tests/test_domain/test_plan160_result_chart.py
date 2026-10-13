"""plans/160 W1 A-1 — 결과 그래프 판정(`src/domain/result_chart.py`) 단위 테스트.

칼럼 이름은 판정에 쓰이지 않으므로 중립 이름(`col_a`·`value` 등)만 쓴다.
"""

from __future__ import annotations

import calendar
import json
import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from src.domain.result_chart import (
    ChartSpec,
    build_chart_data,
    chart_rows,
    classify_columns,
    detect_chart,
    split_tables,
)

_BASE = datetime(2026, 10, 13, 9, 0, 0)


def _ms(dt: datetime) -> int:
    return calendar.timegm(dt.timetuple()) * 1000


# ── 칼럼 유형 ───────────────────────────────────────────────────────


def test_classify_iso_datetime_numeric_category_empty() -> None:
    rows = [
        {"ts": f"2026-10-13T0{i}:00:00", "value": i * 1.5, "label": f"n{i % 2}", "blank": None}
        for i in range(5)
    ]
    assert classify_columns(rows) == {
        "ts": "time",
        "value": "numeric",
        "label": "category",
        "blank": "empty",
    }


def test_classify_iso_variants_space_zone_and_date_only() -> None:
    rows = [
        {
            "a": "2026-10-13 09:00",
            "b": "2026-10-13T09:00:00.123Z",
            "c": "2026-10-13",
            "d": "2026-10-13T09:00:00+09:00",
        },
        {
            "a": "2026-10-13 10:00",
            "b": "2026-10-13T10:00:00.123Z",
            "c": "2026-10-14",
            "d": "2026-10-13T10:00:00+09:00",
        },
    ]
    assert set(classify_columns(rows).values()) == {"time"}


def test_classify_datetime_and_date_objects() -> None:
    rows = [{"a": _BASE + timedelta(hours=i), "b": date(2026, 10, 1 + i)} for i in range(3)]
    assert classify_columns(rows) == {"a": "time", "b": "time"}


def test_classify_epoch_seconds_and_ms_monotonic() -> None:
    start = 1_760_000_000
    rows = [{"s": start + i * 60, "ms": (start + i * 60) * 1000, "v": i} for i in range(6)]
    kinds = classify_columns(rows)
    assert kinds["s"] == "time" and kinds["ms"] == "time" and kinds["v"] == "numeric"


def test_classify_epoch_integral_float() -> None:
    rows = [{"t": float(1_760_000_000_000 + i * 1000), "v": i} for i in range(4)]
    assert classify_columns(rows)["t"] == "time"


def test_large_random_integer_id_is_not_time() -> None:
    rng = random.Random(7)
    ids = rng.sample(range(1_000_000_000, 4_000_000_000), 40)
    rows = [{"col_id": i, "value": n} for n, i in enumerate(ids)]
    assert classify_columns(rows)["col_id"] == "numeric"


def test_epoch_monotonic_per_group() -> None:
    start = 1_760_000_000_000
    rows = []
    for i in range(5):
        for grp in ("g1", "g2"):
            rows.append(
                {"t": start + i * 1000 + (0 if grp == "g1" else 10_000_000), "grp": grp, "v": i}
            )
    # 전체 순서는 g1·g2가 교차해 절반이 감소하지만 그룹별로는 단조다.
    assert classify_columns(rows)["t"] == "time"


def test_epoch_mixed_range_is_not_time() -> None:
    rows = [{"t": 1_760_000_000}, {"t": 1_760_000_000_000}, {"t": 1_760_000_100}]
    assert classify_columns(rows)["t"] == "numeric"


def test_numeric_strings_comma_percent_whitespace_and_decimal() -> None:
    rows = [
        {"a": " 1,234 ", "b": "12.5%", "c": Decimal("3.25"), "d": "-0.5"},
        {"a": "56", "b": "7 %", "c": Decimal("1"), "d": "1e3"},
    ]
    assert set(classify_columns(rows).values()) == {"numeric"}


def test_bool_is_not_numeric() -> None:
    rows = [{"flag": True}, {"flag": False}, {"flag": True}]
    assert classify_columns(rows)["flag"] == "category"


def test_mixed_type_ninety_percent_boundary() -> None:
    nine = [{"v": i} for i in range(9)] + [{"v": "x"}]
    assert classify_columns(nine)["v"] == "numeric"
    eight = [{"v": i} for i in range(8)] + [{"v": "x"}, {"v": "y"}]
    assert classify_columns(eight)["v"] == "category"


def test_blank_values_excluded_from_denominator() -> None:
    rows = [{"v": 1}, {"v": None}, {"v": ""}, {"v": "  "}, {"v": 3}]
    assert classify_columns(rows)["v"] == "numeric"


def test_column_order_includes_late_keys() -> None:
    rows = [{"b": 1, "a": 2}, {"b": 1, "a": 2, "c": "x"}]
    assert list(classify_columns(rows)) == ["b", "a", "c"]


# ── 그래프 판정 ──────────────────────────────────────────────────────


def test_line_iso_single_series() -> None:
    rows = [{"ts": f"2026-10-13T{h:02d}:00:00", "value": h * 2.0} for h in range(6)]
    spec = detect_chart(rows)
    assert spec == ChartSpec(
        kind="line", x="ts", y=("value",), series_by=None, x_type="time", series_total=0
    )


def test_line_series_split() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=i), "inst": inst, "value": float(i)}
        for i in range(4)
        for inst in ("i1", "i2", "i3")
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.series_by == "inst" and spec.series_total == 3


def test_single_value_category_not_split() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=i), "inst": "only", "value": i * 1.0} for i in range(4)
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.series_by is None and spec.series_total == 0


def test_bar_top_n() -> None:
    rows = [{"name": f"h{i}", "value": 100.5 - i} for i in range(10)]
    spec = detect_chart(rows)
    assert spec == ChartSpec(kind="bar", x="name", y=("value",), series_by=None, x_type="category")


def test_bar_over_fifty_rows_none() -> None:
    rows = [{"name": f"h{i}", "value": 1.5 * i} for i in range(51)]
    assert detect_chart(rows) is None


def test_fewer_than_two_rows_none() -> None:
    assert detect_chart([{"name": "a", "value": 1.0}]) is None
    assert detect_chart([]) is None


def test_no_numeric_none() -> None:
    rows = [{"name": "a", "other": "x"}, {"name": "b", "other": "y"}]
    assert detect_chart(rows) is None


def test_bar_identifier_excluded() -> None:
    rows = [{"name": f"h{i}", "col_id": 1000 + i * 7, "value": 1.5 * i} for i in range(5)]
    spec = detect_chart(rows)
    assert spec is not None and spec.y == ("value",)


def test_bar_identifier_only_none() -> None:
    rows = [{"name": f"h{i}", "col_id": 1000 + i} for i in range(5)]
    assert detect_chart(rows) is None


def test_bar_top_n_unique_integer_counts_kept_and_serial_excluded() -> None:
    counts = [97, 54, 40, 21, 13, 8]
    rows = [{"no": i + 1, "name": f"h{i}", "cnt": c} for i, c in enumerate(counts)]
    spec = detect_chart(rows)
    assert spec is not None and spec.kind == "bar" and spec.y == ("cnt",)


def test_line_unique_integer_measure_kept() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=i), "value": v} for i, v in enumerate([5, 9, 2, 14, 7])
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.y == ("value",)


def test_line_float_arithmetic_measure_kept() -> None:
    rows = [{"ts": _BASE + timedelta(minutes=i), "value": i * 2.0} for i in range(5)]
    spec = detect_chart(rows)
    assert spec is not None and spec.y == ("value",)


def test_line_sequential_identifier_excluded() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=i), "seq": 10 + i * 2, "value": 1.5 * i} for i in range(5)
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.y == ("value",)


def test_line_epoch_ms_x() -> None:
    start = 1_760_000_000_000
    rows = [{"t": start + i * 60_000, "value": float(i)} for i in range(5)]
    spec = detect_chart(rows)
    assert spec is not None and spec.kind == "line" and spec.x == "t"
    data = build_chart_data(rows, spec)
    assert data["series"][0]["points"][0] == [start, 0.0]


def test_line_epoch_seconds_converted_to_ms() -> None:
    start = 1_760_000_000
    rows = [{"t": start + i * 60, "value": float(i)} for i in range(3)]
    spec = detect_chart(rows)
    assert spec is not None
    data = build_chart_data(rows, spec)
    assert [p[0] for p in data["series"][0]["points"]] == [
        (start + i * 60) * 1000 for i in range(3)
    ]


# ── 시리즈 데이터 ─────────────────────────────────────────────────────


def test_build_series_names_and_sorted_time() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=m), "inst": inst, "used": m * 1.0, "total": 100.0}
        for m in (2, 0, 1)
        for inst in ("i1", "i2")
    ]
    spec = detect_chart(rows)
    assert spec is not None
    data = build_chart_data(rows, spec)
    names = [s["name"] for s in data["series"]]
    assert names == ["i1 · used", "i1 · total", "i2 · used", "i2 · total"]
    xs = [p[0] for p in data["series"][0]["points"]]
    assert xs == sorted(xs) and xs[0] == _ms(_BASE)
    assert data["series"][0]["column"] == "used"
    assert data["hidden_series"] == 0 and data["downsampled"] is False


def test_build_single_y_series_named_by_category() -> None:
    rows = [
        {"ts": _BASE + timedelta(minutes=m), "inst": i, "value": 1.0}
        for m in range(2)
        for i in ("a", "b")
    ]
    spec = detect_chart(rows)
    assert spec is not None
    assert [s["name"] for s in build_chart_data(rows, spec)["series"]] == ["a", "b"]


def test_build_unsplit_named_by_column() -> None:
    rows = [{"ts": _BASE + timedelta(minutes=m), "value": 1.0} for m in range(3)]
    spec = detect_chart(rows)
    assert spec is not None
    assert build_chart_data(rows, spec)["series"][0]["name"] == "value"


def test_build_skips_unparseable_y_and_blank_x() -> None:
    rows = [
        {"ts": _BASE, "value": 1.0},
        {"ts": _BASE + timedelta(minutes=1), "value": None},
        {"ts": None, "value": 3.0},
        {"ts": _BASE + timedelta(minutes=3), "value": 4.0},
    ]
    spec = ChartSpec(kind="line", x="ts", y=("value",), series_by=None, x_type="time")
    points = build_chart_data(rows, spec)["series"][0]["points"]
    assert [p[1] for p in points] == [1.0, 4.0]


def test_build_more_than_ten_series_hidden() -> None:
    rows = []
    for k in range(13):
        count = 14 - k  # k가 클수록 점이 적다
        for m in range(count):
            rows.append({"ts": _BASE + timedelta(minutes=m), "inst": f"s{k:02d}", "value": 1.0})
    spec = detect_chart(rows)
    assert spec is not None and spec.series_total == 13
    data = build_chart_data(rows, spec)
    assert data["hidden_series"] == 3
    assert [s["name"] for s in data["series"]] == [f"s{k:02d}" for k in range(10)]


def test_build_downsample_keeps_first_and_last() -> None:
    rows = [{"ts": _BASE + timedelta(seconds=i), "value": float(i)} for i in range(5000)]
    spec = detect_chart(rows)
    assert spec is not None
    data = build_chart_data(rows, spec, max_points=100)
    points = data["series"][0]["points"]
    assert data["downsampled"] is True and len(points) == 100
    assert points[0][1] == 0.0 and points[-1][1] == 4999.0


def test_build_bar_keeps_row_order_and_decimal() -> None:
    rows = [
        {"name": n, "value": Decimal(v)} for n, v in (("zz", "9.5"), ("aa", "3.25"), ("mm", "1"))
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.kind == "bar"
    points = build_chart_data(rows, spec)["series"][0]["points"]
    assert points == [["zz", 9.5], ["aa", 3.25], ["mm", 1.0]]


def test_aware_datetime_uses_wall_clock() -> None:
    aware = [datetime(2026, 10, 13, 9, i, tzinfo=UTC) for i in range(3)]
    rows = [{"ts": t, "value": 1.0} for t in aware]
    spec = detect_chart(rows)
    assert spec is not None
    assert build_chart_data(rows, spec)["series"][0]["points"][0][0] == _ms(
        datetime(2026, 10, 13, 9, 0)
    )


def test_output_json_serializable() -> None:
    rows = [
        {
            "ts": _BASE + timedelta(minutes=i),
            "d": date(2026, 10, 1),
            "inst": f"i{i % 2}",
            "value": Decimal(i * i),
        }
        for i in range(6)
    ]
    spec = detect_chart(rows)
    assert spec is not None
    json.dumps(build_chart_data(rows, spec))
    json.dumps(spec.to_dict())


# ── 중첩 시계열 평탄화 ────────────────────────────────────────────────

_T0 = 1_760_000_000_000


def nested_rows() -> list[dict[str, Any]]:
    """인스턴스당 1행 + 지표별 시계열 중첩 칼럼(인스턴스 2개 × 6점)."""
    used = [700.0, 910.0, 920.0, 950.0, 960.0, 990.0]
    rows = []
    for k, name in enumerate(("inst-a", "inst-b")):
        rows.append(
            {
                "src": "s1",
                "col_id": 10 + k,
                "col_name": name,
                "grp_id": 3,
                "col_used_mb": used[-1],
                "col_committed_mb": 1000.0,
                "nested": {
                    "col_used_mb": [{"t": _T0 + i * 60_000, "v": u} for i, u in enumerate(used)],
                    "col_committed_mb": [{"t": _T0 + i * 60_000, "v": 1000.0} for i in range(6)],
                    "col_gc": [{"t": _T0 + i * 60_000, "v": i + 2} for i in range(6)],
                },
            }
        )
    return rows


def test_chart_rows_flattens_nested_series() -> None:
    flat = chart_rows(nested_rows())
    assert len(flat) == 12
    assert flat[0] == {
        "src": "s1",
        "col_name": "inst-a",
        "t": _T0,
        "col_used_mb": 700.0,
        "col_committed_mb": 1000.0,
        "col_gc": 2.0,
    }
    assert [r["t"] for r in flat[:6]] == [_T0 + i * 60_000 for i in range(6)]


def test_chart_rows_idempotent_and_flat_passthrough() -> None:
    flat = chart_rows(nested_rows())
    assert chart_rows(flat) == flat
    plain = [{"name": "a", "value": 1.5}, {"name": "b", "value": 2.5}]
    assert chart_rows(plain) is plain


def test_chart_rows_union_of_times_fills_none() -> None:
    rows = [
        {
            "col_name": "a",
            "nested": {
                "m1": [{"t": _T0, "v": 1.0}, {"t": _T0 + 1000, "v": 2.0}],
                "m2": [{"t": _T0 + 1000, "v": 5.0}],
            },
        }
    ]
    flat = chart_rows(rows)
    assert [(r["t"], r["m1"], r["m2"]) for r in flat] == [(_T0, 1.0, None), (_T0 + 1000, 2.0, 5.0)]


def test_chart_rows_ignores_non_series_dict() -> None:
    rows = [{"name": "a", "meta": {"k": [{"x": "y"}]}}, {"name": "b", "meta": {"k": []}}]
    assert chart_rows(rows) is rows


def test_detect_chart_on_flattened_nested_series() -> None:
    flat = chart_rows(nested_rows())
    spec = detect_chart(flat)
    assert spec == ChartSpec(
        kind="line",
        x="t",
        y=("col_used_mb", "col_committed_mb", "col_gc"),
        series_by="col_name",
        x_type="time",
        series_total=2,
    )
    data = build_chart_data(flat, spec)
    assert len(data["series"]) == 6
    assert data["series"][0]["name"] == "inst-a · col_used_mb"
    assert data["series"][0]["points"][0] == [_T0, 700.0]


# ── 표 분할(W2) ─────────────────────────────────────────────────────


def test_split_tables_cuts_by_boundaries_in_order() -> None:
    rows = [{"i": n} for n in range(5)]
    tables = [{"label": "앞 표", "rows": 2}, {"label": "뒤 표", "rows": 3}]
    assert split_tables(rows, tables) == [("앞 표", rows[:2]), ("뒤 표", rows[2:])]


def test_split_tables_single_table_when_boundaries_invalid() -> None:
    rows = [{"i": n} for n in range(3)]
    for tables in (
        None,
        [],
        "x",
        [{"label": "a", "rows": 1}, {"label": "b", "rows": 1}],  # 합 불일치
        [{"label": "a", "rows": "2"}, {"label": "b", "rows": 1}],  # 정수 아님
        [{"label": "a", "rows": True}, {"label": "b", "rows": 2}],  # bool
        [{"label": "a", "rows": -1}, {"label": "b", "rows": 4}],  # 음수
        [["a", 3]],  # dict 아님
    ):
        assert split_tables(rows, tables) == [("", rows)], tables


def test_split_tables_missing_label_is_empty_string() -> None:
    rows = [{"i": 0}, {"i": 1}]
    assert split_tables(rows, [{"rows": 1}, {"label": None, "rows": 1}]) == [
        ("", rows[:1]),
        ("", rows[1:]),
    ]


# ── 한 시리즈 안 x 고유(W2 교정) ─────────────────────────────────────


def _series_x_unique(rows: list[dict[str, Any]], spec: ChartSpec) -> bool:
    for series in build_chart_data(rows, spec)["series"]:
        xs = [p[0] for p in series["points"]]
        if len(xs) != len(set(xs)):
            return False
    return True


def test_bar_with_duplicate_x_splits_by_next_category() -> None:
    rows = [
        {"col_a": "a", "col_b": "p", "n": 10},
        {"col_a": "a", "col_b": "q", "n": 3},
        {"col_a": "b", "col_b": "p", "n": 7},
        {"col_a": "b", "col_b": "q", "n": 1},
    ]
    spec = detect_chart(rows)
    assert spec == ChartSpec(
        kind="bar", x="col_a", y=("n",), series_by="col_b", x_type="category", series_total=2
    )
    assert _series_x_unique(rows, spec)


def test_bar_with_unsplittable_duplicate_x_is_none() -> None:
    rows = [{"col_a": "a", "n": 10}, {"col_a": "a", "n": 3}, {"col_a": "b", "n": 7}]
    assert detect_chart(rows) is None


def test_bar_unique_x_is_not_split() -> None:
    rows = [{"col_a": "a", "col_b": "p", "n": 10}, {"col_a": "b", "col_b": "q", "n": 3}]
    spec = detect_chart(rows)
    assert spec is not None and spec.series_by is None


def test_line_long_table_with_two_keys_is_none() -> None:
    """시각·서버·지표·값의 긴 표 — 한 범주로는 (시리즈, x)가 고유해지지 않는다."""
    rows = [
        {"ts": f"2026-10-13T0{i}:00:00", "host": h, "metric": m, "value": float(i)}
        for i in range(3) for h in ("h1", "h2") for m in ("cpu", "mem")
    ]
    assert detect_chart(rows) is None


def test_line_falls_back_to_other_category_when_first_collides() -> None:
    rows = [
        {"ts": f"2026-10-13T0{i}:00:00", "zone": "z1", "host": h, "value": float(i)}
        for i in range(3) for h in ("h1", "h2")
    ] + [{"ts": "2026-10-13T00:00:00", "zone": "z2", "host": "h3", "value": 1.0}]
    spec = detect_chart(rows)
    assert spec is not None and spec.series_by == "host"
    assert _series_x_unique(rows, spec)


def test_line_without_category_and_duplicate_time_is_none() -> None:
    rows = [
        {"ts": "2026-10-13T00:00:00", "value": 1.0},
        {"ts": "2026-10-13T00:00:00", "value": 2.0},
        {"ts": "2026-10-13T01:00:00", "value": 3.0},
    ]
    assert detect_chart(rows) is None


# ── 교정 2차: 텍스트 위주 목록은 그리지 않는다(W6 replay 오탐) ──────────────


def test_list_of_text_columns_with_one_number_is_none() -> None:
    """호스트·IP·OS 목록 + 등급 숫자 1칸 — 변하는 범주 3개라 막대가 아니다."""
    rows = [
        {
            "col_a": f"srv-{i:02d}",
            "col_b": f"10.0.0.{i}",
            "col_c": ("Linux", "AIX", "Windows")[i % 3],
            "level": (3, 4, 5)[i % 3],
        }
        for i in range(8)
    ]
    assert detect_chart(rows) is None


def test_event_history_list_is_none() -> None:
    """시각·큰 정수 ID·호스트·등급·메시지 이력 20건 — 선 그래프가 아니다."""
    rows = [
        {
            "occurred": (_BASE - timedelta(minutes=7 * i)).isoformat(),
            "event_id": 9_600_000 + 37 * i + (i * i) % 11,
            "col_a": f"srv-{i % 6:02d}",
            "col_b": ("major", "critical")[i % 2],
            "col_c": f"threshold exceeded #{i}",
        }
        for i in range(20)
    ]
    assert detect_chart(rows) is None


def test_name_with_numeric_measures_stays_bar() -> None:
    """서버명 + 정수 id + 코어 수 + 메모리 — 변하는 범주 1개라 막대 유지(id는 y 잔여 허용)."""
    ids = [17, 3, 42, 8, 29, 11]
    mem = [64, 32, 128, 64, 256, 16]
    rows = [
        {"id": ids[i], "col_a": f"srv-{i:02d}", "cores": 8 + 4 * (i % 3), "mem_gb": mem[i]}
        for i in range(6)
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.kind == "bar" and spec.x == "col_a"
    assert {"cores", "mem_gb"} <= set(spec.y)


def test_constant_category_columns_are_not_counted() -> None:
    """고유값 1인 범주 칼럼은 세지 않는다 — 변하는 범주 2개(호스트·IP)면 막대."""
    rows = [
        {"zone": "z1", "kind": "server", "col_a": f"srv-{i}", "col_b": f"10.0.0.{i}",
         "value": float(10 - i)}
        for i in range(5)
    ]
    spec = detect_chart(rows)
    assert spec is not None and spec.kind == "bar" and spec.x == "col_a"
