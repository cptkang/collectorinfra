"""plans/122 C-1·C-2·C-4b — 커버리지 분모.

분모 plans/1~122 · 미분류 건수 줄 · 요구 소스 비활성 = 실행 환경 부재.

`docs/30` 은 사람이 확정한 분류(부록 A · G-7)의 정본이다. 여기서는 ①분모가 122까지 닿는지
②집계 표가 행과 맞는지 ③`coverage_gap` 이 미분류 건수·요구 소스 제외를 드러내는지를 못 박는다.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from scripts.scenario import analyze
from scripts.scenario.analyze import COVERAGE_DOC, coverage_gap, parse_coverage_doc
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn


def test_c1_denominator_reaches_plan_122() -> None:
    plans = {int(row["plan"]) for row in parse_coverage_doc()}
    assert set(range(95, 123)) <= plans, sorted(set(range(95, 123)) - plans)


def test_c2_no_unclassified_rows() -> None:
    assert not [row["plan"] for row in parse_coverage_doc() if row["trigger"] == "미분류"]


def test_c1_summary_table_matches_matrix() -> None:
    """집계 표를 손으로 고치고 행을 안 고치면(또는 반대) 분모가 두 개가 된다."""
    rows = parse_coverage_doc()
    counts = Counter(row["trigger"] for row in rows)
    text = COVERAGE_DOC.read_text(encoding="utf-8")
    table = dict(re.findall(r"^\| (가능|불가|미분류) \| (\d+) \|$", text, re.MULTILINE))
    assert {key: int(value) for key, value in table.items()} == {
        "가능": counts["가능"], "불가": counts["불가"], "미분류": counts["미분류"]}
    total = re.search(r"^\| \*\*계\*\* \| \*\*(\d+)\*\* \|$", text, re.MULTILINE)
    assert total and int(total.group(1)) == len(rows)


def _catalog() -> Catalog:
    group = Group(id="M", name="m", latency_target_ms=10000)
    return Catalog(
        groups={"M": group},
        scenarios=[Scenario(id="M-01", group="M", plans=[95], title="t", env="sandbox",
                            turns=[Turn({"query": "q"}, {})])],
        profiles={"baseline": {}},
    )


def _matrix(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, str]]) -> None:
    monkeypatch.setattr(analyze, "parse_coverage_doc", lambda path=COVERAGE_DOC: rows)


def test_c2_head_line_has_denominator_and_unclassified(monkeypatch: pytest.MonkeyPatch) -> None:
    _matrix(monkeypatch, [
        {"plan": "1", "feature": "a", "trigger": "미분류", "status": "미분류", "note": ""},
        {"plan": "95", "feature": "b", "trigger": "가능", "status": "부분", "note": ""},
    ])
    text = coverage_gap({"plans_coverage": {}}, _catalog())
    head = text.split("| 계획서 |")[0]
    assert "**분모: `docs/30` 2행 · plans/1~95 (2건) · 미분류 1건**" in head


def test_c4b_requires_sources_skip_is_environment_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _matrix(monkeypatch, [{"plan": "95", "feature": "ITAM", "trigger": "가능", "status": "부분",
                           "note": "M군"}])
    summary = {"plans_coverage": {"95": {"scenarios": 1, "executed": 0, "pass": 0}},
               "skipped": [{"scenario_id": "M-01", "reason_code": "requires_sources",
                            "reason": "요구 소스 [itam] 이 run 서버에서 비활성"}]}
    text = coverage_gap(summary, _catalog())
    row = next(line for line in text.splitlines() if line.startswith("| plans/95 |"))
    assert "| 실행 환경 부재 |" in row
    assert "요구 소스 비활성으로 선택 제외 1건(C-4b)" in row
    assert "## 요구 소스 비활성으로 선택하지 않은 시나리오" in text
    assert "| M-01 | 요구 소스 [itam] 이 run 서버에서 비활성 |" in text


def test_c1_note_prefix_marks_environment_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """부록 A plans/74(DRM · 폐쇄망 전용) - 시나리오 0건이어도 카탈로그 미작성이 아니다."""
    _matrix(monkeypatch, [
        {"plan": "74", "feature": "DRM", "trigger": "가능", "status": "부분",
         "note": "실행 환경 부재 - 폐쇄망 전용"},
        {"plan": "22", "feature": "매핑", "trigger": "가능", "status": "구현", "note": "H군"},
    ])
    text = coverage_gap({"plans_coverage": {}}, Catalog(groups={}, scenarios=[], profiles={}))
    rows = {line.split("|")[1].strip(): line for line in text.splitlines()
            if line.startswith("| plans/")}
    assert "| 실행 환경 부재 |" in rows["plans/74"]
    assert "| 카탈로그 미작성 |" in rows["plans/22"]


def test_c1_real_docs30_shows_plans_after_95() -> None:
    """95~122 는 분모에 행이 없어 `coverage_gap` 에 아예 나타나지 않았다(§2.5)."""
    text = coverage_gap({"plans_coverage": {}}, Catalog(groups={}, scenarios=[], profiles={}))
    shown = {int(m) for m in re.findall(r"^\| plans/(\d+) \|", text, re.MULTILINE)}
    assert {96, 101, 104, 122} <= shown


def test_docs30_path_is_in_repo() -> None:
    assert COVERAGE_DOC == Path(analyze.REPO_ROOT) / "docs" / "30_scenario_coverage.md"
