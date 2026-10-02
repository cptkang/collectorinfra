"""산출 파일 단언 확장 (plans/122 H-3 `.docx` · H-4 `.xlsx`).

xlsx 는 테스트에서 openpyxl 로 만들고, docx 는 저장소 픽스처
`tests/e2e/fixtures/sample_template.docx`(자리 표시 `{{서버명}}`·`{{IP주소}}` + 6행 표)를 원본으로
제품 작성기와 같은 방식(행 XML 복제)으로 채운 사본을 만든다. 하위 키마다 합격·불합격 쌍, 원본 대비
스타일 보존, 새 하위 키가 없으면 종전 판정과 같음을 고정한다. 전부 무과금이다.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import REPO_ROOT
from scripts.scenario.assertions import Observation, Verdict, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn

openpyxl = pytest.importorskip("openpyxl")
docx = pytest.importorskip("docx")

DOCX_TEMPLATE = "tests/e2e/fixtures/sample_template.docx"


def _judge(file_spec: dict[str, Any], artifact: Path, upload: str | None = None) -> Verdict:
    sc = Scenario(id="T-01", group="T", plans=[122], title="t", upload=upload,
                  turns=[Turn(send={"query": "q"}, expect={"file": file_spec})])
    obs = Observation(status="completed", http_status=200, has_file=True, artifacts=[str(artifact)])
    return evaluate_turn(sc, 1, sc.turns[0], obs, Group(id="T", name="t", latency_target_ms=1))


def _xlsx(path: Path, rows: list[list[Any]], *, widths: dict[str, float] | None = None,
          merged: list[str] | None = None, title: str = "서버") -> Path:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = title
    for row in rows:
        sheet.append(row)
    for letter, width in (widths or {}).items():
        sheet.column_dimensions[letter].width = width
    for cells in merged or []:
        sheet.merge_cells(cells)
    book.save(path)
    return path


HEADER = ["서버명", "호스트명", "CPU 평균", "TPMC", "담당자"]


@pytest.fixture()
def good_xlsx(tmp_path: Path) -> Path:
    return _xlsx(tmp_path / "good.xlsx", [
        HEADER,
        ["WEB서버1", "web-01", 12.5, None, "인프라팀"],
        ["DB서버", "db-01", "99", "", "인프라팀"],
        [None, None, None, None, None],                      # 뒤따르는 빈 행은 데이터가 아니다
    ])


# --- H-4 xlsx 값 단언 ---------------------------------------------------------------

def test_xlsx_value_subkeys_pass(good_xlsx: Path) -> None:
    verdict = _judge({
        "columns": ["서버명", "호스트명"], "filled_rows": {"min": 2},
        "value_range": {"CPU 평균": [0, 100]}, "unique_by": ["호스트명"],
        "columns_differ": [["서버명", "호스트명"]], "empty_columns": ["TPMC"],
        "column_equals": {"담당자": "인프라팀"},
    }, good_xlsx)
    assert verdict.func == "pass", verdict.failures


def test_xlsx_value_subkeys_fail(tmp_path: Path) -> None:
    bad = _xlsx(tmp_path / "bad.xlsx", [
        HEADER,
        ["web-01", "web-01", 180, 5000, "인프라팀"],
        ["db-01", "db-01", "n/a", None, "운영팀"],
        ["db-01", "db-01", 50, None, None],
    ])
    verdict = _judge({
        "value_range": {"CPU 평균": [0, 100]}, "unique_by": ["호스트명"],
        "columns_differ": [["서버명", "호스트명"]], "empty_columns": ["TPMC"],
        "column_equals": {"담당자": "인프라팀"},
    }, bad)
    by_key = {f.key: f.actual for f in verdict.failures}
    assert by_key["file.value_range"]["out_of_range"] == 1
    assert by_key["file.value_range"]["non_numeric"] == 1
    assert by_key["file.unique_by"]["duplicate_rows"] == 2
    assert by_key["file.columns_differ"] == {"compared_rows": 3, "equal_rows": 3}
    assert by_key["file.empty_columns"]["filled_rows"] == 1
    assert by_key["file.empty_columns"]["empty_by_column"] == {"TPMC": "2/3"}   # Y-3 진단 형식
    assert by_key["file.column_equals"] == {"rows": 3, "mismatched_rows": 2,
                                            "examples": ["운영팀", ""]}


def test_columns_differ_allows_some_equal_rows(tmp_path: Path) -> None:
    """name=hostname 인 정상 서버가 섞여 있어도 열 전체가 사본이 아니면 합격이다(§9.1 H-04)."""
    path = _xlsx(tmp_path / "mixed.xlsx", [HEADER[:2], ["WEB서버1", "web-01"], ["db-01", "db-01"]])
    assert _judge({"columns_differ": [["서버명", "호스트명"]]}, path).func == "pass"


def test_column_equals_and_differ_need_rows(tmp_path: Path) -> None:
    """데이터 행이 0건이면 내용 단언을 지켰다고 세지 않는다."""
    path = _xlsx(tmp_path / "empty.xlsx", [HEADER])
    verdict = _judge({"column_equals": {"담당자": "인프라팀"},
                      "columns_differ": [["서버명", "호스트명"]]}, path)
    assert sorted(f.key for f in verdict.failures) == ["file.column_equals", "file.columns_differ"]


def test_missing_column_is_reported_with_header(good_xlsx: Path) -> None:
    verdict = _judge({"empty_columns": ["비고"]}, good_xlsx)
    assert verdict.failures[0].actual == {"missing": "헤더에 없음", "header": HEADER}


# --- H-4 style_preserved -----------------------------------------------------------

def test_style_preserved_against_upload(tmp_path: Path) -> None:
    original = _xlsx(tmp_path / "form.xlsx", [HEADER], widths={"A": 20.0, "B": 18.0},
                     merged=["D1:E1"])
    kept = _xlsx(tmp_path / "kept.xlsx", [HEADER, ["a", "b", 1, None, "x"]],
                 widths={"A": 20.0, "B": 18.0}, merged=["D1:E1"])
    assert _judge({"style_preserved": True}, kept, str(original)).func == "pass"

    lost = _xlsx(tmp_path / "lost.xlsx", [HEADER], widths={"A": 8.43})
    verdict = _judge({"style_preserved": True}, lost, str(original))
    assert verdict.failures[0].key == "file.style_preserved"
    assert verdict.failures[0].actual == {
        "sheets_missing": [], "widths_changed": {"서버!A": [20.0, 8.43], "서버!B": [18.0, None]},
        "merged_missing": ["서버!D1:E1"],
    }


def test_style_preserved_without_upload_is_held(good_xlsx: Path) -> None:
    verdict = _judge({"style_preserved": True}, good_xlsx, None)
    assert verdict.func == "manual" and verdict.manual_sources == ["unobservable"]


def test_style_preserved_on_repo_template_roundtrip(tmp_path: Path) -> None:
    """저장소 양식을 openpyxl 로 읽어 채우고 저장한 산출물(제품 작성기 방식)은 보존으로 판정된다."""
    template = "testdata/templates/server_list_template.xlsx"
    book = openpyxl.load_workbook(REPO_ROOT / template)
    book.active.append(["web-01", "10.0.0.1", "LINUX", 8, "HPE", "정상"])
    out = tmp_path / "filled.xlsx"
    book.save(out)
    assert _judge({"style_preserved": True}, out, template).func == "pass"


# --- 종전 판정 불변 -----------------------------------------------------------------

def test_non_xlsx_without_docx_key_keeps_old_hold(tmp_path: Path) -> None:
    """새 하위 키가 없으면 docx 산출물은 종전 문구 그대로 보류다(바이트 동일)."""
    path = tmp_path / "result.docx"
    path.write_bytes((REPO_ROOT / DOCX_TEMPLATE).read_bytes())
    verdict = _judge({"columns": ["a"]}, path)
    assert verdict.manual_notes == ["result.docx: xlsx 가 아니라 자동 칼럼 검증 대상이 아니다"]


def test_docx_key_on_xlsx_artifact_fails(good_xlsx: Path) -> None:
    verdict = _judge({"docx": {"no_placeholders": True}}, good_xlsx)
    assert [(f.key, f.actual) for f in verdict.failures] == [("file.docx", "good.xlsx")]


# --- H-3 docx ----------------------------------------------------------------------

def _fill_docx(out: Path, *, replace: bool = True, extra_rows: int = 2,
               restyle: bool = False) -> Path:
    """픽스처를 제품 작성기처럼 채운다.

    자리 표시 치환 · 기존 빈 행 채움 · 첫 데이터 행 XML 복제.
    """
    doc = docx.Document(str(REPO_ROOT / DOCX_TEMPLATE))
    if replace:
        for paragraph in doc.paragraphs:
            for run in paragraph.runs:
                text = run.text.replace("{{서버명}}", "web-01")
                run.text = text.replace("{{IP주소}}", "10.0.0.1")
    table = doc.tables[0]
    for row in table.rows[1:]:
        row.cells[0].paragraphs[0].text = "web-01"
    for _ in range(extra_rows):
        table._tbl.append(copy.deepcopy(table.rows[1]._tr))
    if restyle:
        doc.paragraphs[0].style = doc.styles["Normal"]            # 제목 스타일이 사라졌다
        table.rows[-1].cells[0].paragraphs[0].style = doc.styles["Heading 1"]
    doc.save(str(out))
    return out


DOCX_SPEC = {
    "no_placeholders": True, "styles_preserved": True,
    "tables": [{"index": 0, "min_rows": 8, "first_row": ["서버명", "IP주소", "CPU사용률"]}],
}


def test_docx_filled_like_product_passes(tmp_path: Path) -> None:
    out = _fill_docx(tmp_path / "out.docx")
    verdict = _judge({"docx": DOCX_SPEC}, out, DOCX_TEMPLATE)
    assert verdict.func == "pass", verdict.failures


def test_docx_leftover_placeholders_and_short_table_fail(tmp_path: Path) -> None:
    out = _fill_docx(tmp_path / "out.docx", replace=False, extra_rows=0)
    verdict = _judge({"docx": DOCX_SPEC}, out, DOCX_TEMPLATE)
    by_key = {f.key: f.actual for f in verdict.failures}
    assert by_key["file.docx.no_placeholders"] == {
        "remaining": 2, "examples": ["{{서버명}}", "{{IP주소}}"],
    }
    assert by_key["file.docx.tables.min_rows"] == {"index": 0, "rows": 6}


def test_docx_first_row_and_missing_table(tmp_path: Path) -> None:
    out = _fill_docx(tmp_path / "out.docx")
    verdict = _judge({"docx": {"tables": [
        {"index": 0, "first_row": ["호스트명", "IP주소", "CPU사용률"]},
        {"index": 3, "min_rows": 1},
    ]}}, out)
    by_key = {f.key: f.actual for f in verdict.failures}
    assert by_key["file.docx.tables.first_row"] == {
        "index": 0, "cells": 3, "mismatched": [{"cell": 0, "actual": "서버명"}],
    }
    assert by_key["file.docx.tables"] == {"tables": 1}


def test_docx_style_drift_fails(tmp_path: Path) -> None:
    out = _fill_docx(tmp_path / "out.docx", restyle=True)
    verdict = _judge({"docx": {"styles_preserved": True}}, out, DOCX_TEMPLATE)
    (failure,) = verdict.failures
    assert failure.key == "file.docx.styles_preserved"
    assert failure.actual["paragraphs"]["changed"] == 1
    assert failure.actual["tables"]["0"]["rows_mismatched"] == 1
    assert failure.actual["tables"]["0"]["first_mismatch_row"] == 7


def test_docx_styles_without_upload_is_held(tmp_path: Path) -> None:
    out = _fill_docx(tmp_path / "out.docx")
    verdict = _judge({"docx": {"styles_preserved": True}}, out, None)
    assert verdict.func == "manual" and verdict.manual_sources == ["unobservable"]


def test_broken_docx_is_held(tmp_path: Path) -> None:
    broken = tmp_path / "out.docx"
    broken.write_bytes(b"not a zip")
    verdict = _judge({"docx": {"no_placeholders": True}}, broken)
    assert verdict.func == "manual" and verdict.manual_notes[0].startswith("docx 열기 실패")


def test_missing_python_docx_is_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """python-docx 가 없으면(document extra 미설치) 불합격이 아니라 보류다."""
    import builtins

    real_import = builtins.__import__

    def no_docx(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "docx":
            raise ImportError("no docx")
        return real_import(name, *args, **kwargs)

    out = _fill_docx(tmp_path / "out.docx")
    monkeypatch.setattr(builtins, "__import__", no_docx)
    verdict = _judge({"docx": {"no_placeholders": True}}, out)
    assert verdict.func == "manual"
    assert "python-docx 미설치" in verdict.manual_notes[0]


def test_missing_sheet_with_filled_rows_fails_without_crash(good_xlsx: Path) -> None:
    """선언한 시트가 없으면 `file.sheets` 불합격으로 끝난다 — `filled_rows` 가 있어도 예외로 죽지 않는다.

    종전에는 `sheets.get(없는 시트)` = None 을 `[1:]` 로 잘라 `evaluate_turn` 전체가 TypeError 로 죽었다
    (h-judge 발견 · HEAD 부터 잠복).
    """
    verdict = _judge({"sheets": ["없는시트"], "columns": ["서버명"], "filled_rows": {"min": 1}},
                     good_xlsx)
    assert verdict.func == "fail"
    assert "file.sheets" in [f.key for f in verdict.failures]
