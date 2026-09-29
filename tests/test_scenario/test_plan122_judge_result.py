"""결과 행 단언 `result` (plans/122 H-1 · G-4).

러너가 `/query/{id}/download-csv` 로 받은 행(`Observation.result`)을 합성 CSV 모양으로 만들어
하위 키
(`columns`·`filled_columns`·`value_range`·`unique_by`·`allow_empty`)마다 합격·불합격 쌍을 고정한다.
관측하지 못하면(미수집 · unavailable) 보류, 불합격 상세에는 행 원문을 싣지 않는다(개수 + 예시 3개).
전부 무과금 순수 함수다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.scenario.assertions import Observation, Verdict, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn


def _judge(spec: dict[str, Any], result: dict[str, Any] | None) -> Verdict:
    sc = Scenario(id="T-01", group="T", plans=[122], title="t",
                  turns=[Turn(send={"query": "q"}, expect={"result": spec})])
    obs = Observation(status="completed", http_status=200, response="결과", result=result)
    return evaluate_turn(sc, 1, sc.turns[0], obs, Group(id="T", name="t", latency_target_ms=1))


def _csv(columns: list[str], rows: list[list[str]], *, truncated: bool = False,
         total: int | None = None) -> dict[str, Any]:
    """러너 계약 모양(값은 문자열 — CSV)."""
    return {
        "status": "ok", "columns": columns,
        "rows": [dict(zip(columns, row)) for row in rows],
        "total_rows": total if total is not None else len(rows), "truncated": truncated,
        "reason": None,
    }


SERVERS = _csv(
    ["hostname", "cpu_avg", "month"],
    [["web-01", "12.5", "202608"], ["web-02", " 99.0 ", "202608"], ["db-01", "0", "202608"]],
)


# --- 하위 키별 쌍 -----------------------------------------------------------------

def test_all_subkeys_pass_on_good_rows() -> None:
    verdict = _judge({
        "columns": ["hostname", ["avg_cpu", "cpu_avg"]], "filled_columns": ["hostname"],
        "value_range": {"cpu_avg": [0, 100]}, "unique_by": ["hostname"],
    }, SERVERS)
    assert verdict.func == "pass", verdict.failures


def test_columns_accept_aliases_and_report_header() -> None:
    verdict = _judge({"columns": [["cpu_max", "max_cpu"], "hostname"]}, SERVERS)
    assert verdict.func == "fail"
    (failure,) = verdict.failures
    assert failure.key == "result.columns" and failure.actual == ["hostname", "cpu_avg", "month"]


def test_filled_columns_counts_empty_cells() -> None:
    rows = _csv(["hostname", "vendor"], [["a", "HPE"], ["b", " "], ["c", ""]])
    verdict = _judge({"filled_columns": ["vendor"]}, rows)
    assert verdict.failures[0].actual == {"column": "vendor", "empty_rows": 2, "rows": 3}


def test_value_range_out_of_range_and_non_numeric() -> None:
    rows = _csv(["cpu"], [["5"], ["101.5"], ["abc"], [""], ["-1"], ["150"]])
    verdict = _judge({"value_range": {"cpu": [0, 100]}}, rows)
    (failure,) = verdict.failures
    assert failure.key == "result.value_range"
    assert failure.actual["checked"] == 4 and failure.actual["non_numeric"] == 1
    assert failure.actual["out_of_range"] == 3
    assert failure.actual["examples"] == ["101.5", "abc", "-1"]       # 예시 최대 3개


def test_value_range_needs_at_least_one_number() -> None:
    """숫자가 하나도 없으면(전부 빈 칸) 범위를 지켰다고 세지 않는다."""
    verdict = _judge({"value_range": {"cpu": [0, 100]}}, _csv(["cpu"], [[""], [" "]]))
    assert verdict.func == "fail" and verdict.failures[0].actual["checked"] == 0


def test_value_range_missing_column() -> None:
    verdict = _judge({"value_range": {"mem": [0, 100]}}, SERVERS)
    assert verdict.failures[0].actual["missing"] == "헤더에 없음"


def test_unique_by_composite_key() -> None:
    rows = _csv(["host", "month"], [["a", "1"], ["a", "2"], ["b", "1"], ["b", "1"], ["b", "1"]])
    assert _judge({"unique_by": ["host", "month"]}, _csv(["host", "month"],
                                                         [["a", "1"], ["a", "2"]])).func == "pass"
    verdict = _judge({"unique_by": ["host", "month"]}, rows)
    failure = verdict.failures[0]
    assert failure.key == "result.unique_by"
    assert failure.actual["duplicate_keys"] == 1 and failure.actual["duplicate_rows"] == 3
    assert failure.actual["examples"] == [["b", "1"]]


# --- 빈 결과 · 절단 · 보류 ---------------------------------------------------------

EMPTY = {"status": "empty", "columns": [], "rows": [], "total_rows": 0, "truncated": False,
         "reason": "404"}


def test_empty_result_fails_each_declared_check() -> None:
    verdict = _judge({"columns": ["hostname"], "unique_by": ["hostname"]}, EMPTY)
    assert [(f.key, f.actual) for f in verdict.failures] == [
        ("result.columns", "결과 행 0건"), ("result.unique_by", "결과 행 0건"),
    ]


def test_empty_result_with_only_allow_empty_false_fails() -> None:
    verdict = _judge({"allow_empty": False}, EMPTY)
    assert [f.key for f in verdict.failures] == ["result.allow_empty"]


def test_empty_result_allowed() -> None:
    assert _judge({"allow_empty": True, "columns": ["hostname"]}, EMPTY).func == "pass"
    # 머리글만 있고 행이 0건인 CSV 도 빈 결과다.
    assert _judge({"allow_empty": True}, _csv(["hostname"], [])).func == "pass"
    assert _judge({"columns": ["hostname"]}, _csv(["hostname"], [])).func == "fail"


def test_truncated_result_judges_received_rows_and_says_so() -> None:
    rows = _csv(["cpu"], [["5"], ["500"]], truncated=True, total=5000)
    verdict = _judge({"value_range": {"cpu": [0, 100]}}, rows)
    actual = verdict.failures[0].actual
    assert actual["truncated"] is True
    assert actual["judged_rows"] == 2 and actual["total_rows"] == 5000
    # 받은 행이 다 맞으면 절단돼도 합격이다(받은 행만 판정).
    assert _judge({"value_range": {"cpu": [0, 100]}},
                  _csv(["cpu"], [["5"]], truncated=True, total=9)).func == "pass"


@pytest.mark.parametrize("result, needle", [
    (None, "결과 행 미수집"),
    ({"status": "unavailable", "reason": "http 500"}, "http 500"),
    ({"status": "weird"}, "status=weird"),
])
def test_unobserved_result_is_held(result: dict[str, Any] | None, needle: str) -> None:
    verdict = _judge({"columns": ["hostname"]}, result)
    assert verdict.func == "manual"
    assert verdict.manual_sources == ["unobservable"]
    assert needle in verdict.manual_notes[0]


def test_failure_detail_never_carries_row_dump() -> None:
    """G-4 — 불합격 상세에 행 원문이 없다: 한 행에만 있는 표식이 3개를 넘게 실리지 않는다."""
    rows = _csv(["ip", "cpu"], [[f"10.0.0.{i}", "500"] for i in range(20)])
    verdict = _judge({"value_range": {"cpu": [0, 100]}, "unique_by": ["cpu"],
                      "filled_columns": ["ip"]}, rows)
    dumped = json.dumps([f.as_dict() for f in verdict.failures], ensure_ascii=False)
    assert "10.0.0." not in dumped                   # 판정 대상이 아닌 열 값은 아예 없다
    assert dumped.count("500") <= 3 + 3             # value_range 예시 3 + unique_by 예시 키 1(≤3)
