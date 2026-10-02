"""산출물 검증 V7 + 계획서 커버리지 역집계 V3 (plans/94 §10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.scenario.analyze import coverage_gap, parse_coverage_doc
from scripts.scenario.assertions import Observation, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn, load_catalog

openpyxl = pytest.importorskip("openpyxl")


def _xlsx(path: Path, header: list[str], rows: list[list], sheet: str = "Sheet1") -> Path:
    book = openpyxl.Workbook()
    page = book.active
    page.title = sheet
    page.append(header)
    for row in rows:
        page.append(row)
    book.save(path)
    return path


def _eval(expect: dict, artifacts: list[str]):
    scenario = Scenario(id="H-01", group="H", plans=[10], title="t",
                        turns=[Turn({"query": "q"}, expect)], endpoint="file_stream",
                        upload="x.xlsx")
    obs = Observation(status="completed", has_file=True, artifacts=artifacts,
                      executed_sql="SELECT 1")
    return evaluate_turn(scenario, 1, scenario.turns[0], obs,
                         Group(id="H", name="h", latency_target_ms=60000))


# --- V7 전 칼럼 검증 -----------------------------------------------------

def test_V7_전_칼럼이_채워지면_합격이다(tmp_path: Path) -> None:
    path = _xlsx(tmp_path / "full.xlsx", ["호스트명", "OS종류", "벤더"],
                 [[f"srv-{i}", "Linux", "HPE"] for i in range(3)])
    verdict = _eval(
        {"file": {"sheets": ["Sheet1"], "columns": ["호스트명", "OS종류", "벤더"],
                  "filled_rows": {"min": 3}}},
        [str(path)],
    )
    assert verdict.func == "pass", verdict.failures


def test_V7_일부_칼럼만_채운_행은_채워진_것으로_세지_않는다(tmp_path: Path) -> None:
    """미리보기 일부가 아니라 실제 산출 파일의 전 칼럼 확인(Known Mistakes)."""
    path = _xlsx(tmp_path / "partial.xlsx", ["호스트명", "OS종류", "벤더"],
                 [[f"srv-{i}", "Linux", None] for i in range(3)])   # 벤더가 비었다
    verdict = _eval(
        {"file": {"sheets": ["Sheet1"], "columns": ["호스트명", "OS종류", "벤더"],
                  "filled_rows": {"min": 3}}},
        [str(path)],
    )
    assert verdict.func == "fail"
    assert verdict.failures[0].key == "file.filled_rows.min"
    # Y-3: 「기대 3 실제 0」만으로는 *빈 파일*로 읽힌다. **어느 열이 몇 행 비었는지**를 싣는다 -
    # H-04 의 실체는 2338행 중 5열이 2337행 채워지고 '비고' 한 열만 전 행 공란인 정상 산출물이었다.
    actual = verdict.failures[0].actual
    assert actual["filled_rows"] == 0
    assert actual["data_rows"] == 3
    assert actual["empty_by_column"] == {"호스트명": "0/3", "OS종류": "0/3", "벤더": "3/3"}


def test_V7_선언한_칼럼이_없으면_불합격이다(tmp_path: Path) -> None:
    path = _xlsx(tmp_path / "missing.xlsx", ["호스트명", "OS종류"], [["srv-1", "Linux"]])
    verdict = _eval({"file": {"columns": ["호스트명", "벤더"]}}, [str(path)])
    assert verdict.func == "fail"
    assert any(f.key == "file.columns" for f in verdict.failures)


def test_V7_선언한_시트가_없으면_불합격이다(tmp_path: Path) -> None:
    path = _xlsx(tmp_path / "sheet.xlsx", ["a"], [["1"]], sheet="다른이름")
    verdict = _eval({"file": {"sheets": ["서버목록"]}}, [str(path)])
    assert any(f.key == "file.sheets" for f in verdict.failures)


def test_V7_산출물이_없으면_불합격이다() -> None:
    verdict = _eval({"file": {"columns": ["a"]}}, [])
    assert verdict.func == "fail"
    assert verdict.failures[0].actual == "산출물 없음"


def test_V7_xlsx_가_아니면_수동_검토로_넘긴다(tmp_path: Path) -> None:
    """판정할 수 없는 것을 합격으로 세지 않는다."""
    path = tmp_path / "out.docx"
    path.write_bytes(b"stub")
    verdict = _eval({"file": {"columns": ["a"]}}, [str(path)])
    assert verdict.func == "manual"
    assert any("자동 칼럼 검증 대상이 아니다" in note for note in verdict.manual_notes)


def test_V7_깨진_xlsx_는_판독_불가로_남는다(tmp_path: Path) -> None:
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not a zip")
    verdict = _eval({"file": {"columns": ["a"]}}, [str(path)])
    assert verdict.func == "manual"
    assert any("xlsx 열기 실패" in note for note in verdict.manual_notes)


# --- V3 계획서 커버리지 역집계 -------------------------------------------

def test_V3_커버리지_문서가_기계_판독된다() -> None:
    rows = parse_coverage_doc()
    assert len(rows) > 50, "docs/30 매트릭스를 읽지 못했다 - 커버리지 분모가 사라진다"
    assert all(row["plan"].isdigit() for row in rows)
    assert {row["trigger"] for row in rows} <= {"가능", "불가", "미분류"}


def test_V3_시나리오_0건인_구현_기능이_미커버로_전부_나온다() -> None:
    """'plans 폴더의 기능들을 테스트' 요건이 충족됐는지는 여기서만 확인된다(§6.4)."""
    catalog = load_catalog()
    index = catalog.plans_index()
    matrix = parse_coverage_doc()
    text = coverage_gap({"plans_coverage": {}}, catalog)

    expected = [
        item["plan"] for item in matrix
        if item["status"] == "구현" and item["trigger"] == "가능"
        and not index.get(int(item["plan"]))
    ]
    assert expected, "검증 표본이 없다 - 전 기능이 커버됐다면 이 테스트를 갱신할 것"
    for plan in expected:
        assert f"| plans/{plan} |" in text, f"plans/{plan} 이 미커버 목록에 없다"
        assert "카탈로그 미작성" in text


def test_V3_미커버_사유가_비지_않는다() -> None:
    '"그냥 없음"은 허용하지 않는다.'
    catalog = load_catalog()
    text = coverage_gap({"plans_coverage": {}}, catalog)
    allowed = {"프롬프트 트리거 아님", "미구현", "카탈로그 미작성", "실행 환경 부재", "분류 미확정"}
    for line in text.splitlines():
        if not line.startswith("| plans/"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        assert cells[3] in allowed, f"정의 밖 사유: {cells[3]!r}"


def test_V3_역집계는_카탈로그의_plans_필드에서_나온다() -> None:
    catalog = load_catalog()
    index = catalog.plans_index()
    assert index, "역집계가 비었다"
    for plan, scenario_ids in index.items():
        for scenario_id in scenario_ids:
            scenario = catalog.by_id(scenario_id)
            assert scenario is not None and plan in scenario.plans
