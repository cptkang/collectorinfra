"""Excel 양식 파서 단위 테스트."""

from __future__ import annotations

import io

import pytest
from openpyxl import Workbook

from src.document.excel_parser import parse_excel_template


def _create_excel_bytes(
    headers: list[str] | None = None,
    data_rows: list[list] | None = None,
    sheet_name: str = "Sheet1",
    header_row: int = 1,
    merged_cells: list[str] | None = None,
    formulas: dict[str, str] | None = None,
    extra_sheets: list[dict] | None = None,
) -> bytes:
    """테스트용 Excel 파일 바이너리를 생성한다."""
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name

    if headers:
        for col_idx, header in enumerate(headers, 1):
            ws.cell(row=header_row, column=col_idx, value=header)

    if data_rows:
        start_row = header_row + 1
        for row_offset, row_data in enumerate(data_rows):
            for col_idx, value in enumerate(row_data, 1):
                ws.cell(row=start_row + row_offset, column=col_idx, value=value)

    if merged_cells:
        for cell_range in merged_cells:
            ws.merge_cells(cell_range)

    if formulas:
        for cell_ref, formula in formulas.items():
            ws[cell_ref] = formula

    if extra_sheets:
        for extra in extra_sheets:
            extra_ws = wb.create_sheet(title=extra.get("name", "Extra"))
            extra_headers = extra.get("headers", [])
            for col_idx, header in enumerate(extra_headers, 1):
                extra_ws.cell(row=1, column=col_idx, value=header)

    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


class TestParseExcelTemplate:
    """parse_excel_template 함수 테스트."""

    def test_basic_single_sheet(self):
        """기본 단일 시트 파싱."""
        data = _create_excel_bytes(
            headers=["서버명", "IP주소", "CPU 사용률"],
            data_rows=[
                ["web-01", "10.0.0.1", 85.2],
                ["web-02", "10.0.0.2", 72.0],
            ],
        )

        result = parse_excel_template(data)

        assert result["file_type"] == "xlsx"
        assert len(result["sheets"]) == 1

        sheet = result["sheets"][0]
        assert sheet["name"] == "Sheet1"
        assert sheet["headers"] == ["서버명", "IP주소", "CPU 사용률"]
        assert sheet["header_row"] == 1
        assert sheet["data_start_row"] == 2
        assert sheet["max_column"] == 3

    def test_header_detection_non_first_row(self):
        """헤더가 첫 번째 행이 아닌 경우 탐지."""
        wb = Workbook()
        ws = wb.active
        # 1행: 빈 행, 2행: 제목 (1셀만), 3행: 헤더
        ws.cell(row=2, column=1, value="보고서 제목")
        ws.cell(row=3, column=1, value="서버명")
        ws.cell(row=3, column=2, value="IP주소")
        ws.cell(row=3, column=3, value="상태")

        buf = io.BytesIO()
        wb.save(buf)
        data = buf.getvalue()

        result = parse_excel_template(data)
        sheet = result["sheets"][0]
        assert sheet["header_row"] == 3
        assert sheet["data_start_row"] == 4
        assert "서버명" in sheet["headers"]

    def test_empty_sheet_skipped(self):
        """빈 시트는 결과에 포함되지 않는다."""
        wb = Workbook()
        wb.active.title = "Empty"
        buf = io.BytesIO()
        wb.save(buf)

        result = parse_excel_template(buf.getvalue())
        assert result["sheets"] == []

    def test_merged_cells_detected(self):
        """병합 셀 정보가 수집된다."""
        data = _create_excel_bytes(
            headers=["서버명", "IP", "상태"],
            merged_cells=["A1:B1"],
        )

        result = parse_excel_template(data)
        sheet = result["sheets"][0]
        assert len(sheet["merged_cells"]) > 0

    def test_formula_cells_detected(self):
        """수식 셀이 탐지된다."""
        data = _create_excel_bytes(
            headers=["항목", "값", "합계"],
            data_rows=[["a", 100, None]],
            formulas={"C2": "=SUM(B2:B100)"},
        )

        result = parse_excel_template(data)
        sheet = result["sheets"][0]
        assert "C2" in sheet["formula_cells"]

    def test_multi_sheet(self):
        """다중 시트 파싱."""
        data = _create_excel_bytes(
            headers=["서버명", "IP"],
            extra_sheets=[
                {"name": "CPU", "headers": ["서버", "사용률"]},
            ],
        )

        result = parse_excel_template(data)
        assert len(result["sheets"]) == 2
        sheet_names = [s["name"] for s in result["sheets"]]
        assert "Sheet1" in sheet_names
        assert "CPU" in sheet_names

    def test_invalid_file_raises_error(self):
        """유효하지 않은 파일은 ValueError를 발생시킨다."""
        with pytest.raises(ValueError, match="Excel 파일을 읽을 수 없습니다"):
            parse_excel_template(b"not a valid excel file")

    def test_data_end_row_detection(self):
        """데이터 영역 끝 행이 올바르게 탐지된다."""
        data = _create_excel_bytes(
            headers=["이름", "값"],
            data_rows=[
                ["a", 1],
                ["b", 2],
                ["c", 3],
            ],
        )

        result = parse_excel_template(data)
        sheet = result["sheets"][0]
        assert sheet["data_end_row"] == 4  # row 2, 3, 4

    def test_header_cells_structure(self):
        """header_cells에 col과 value 정보가 있다."""
        data = _create_excel_bytes(headers=["이름", "나이"])

        result = parse_excel_template(data)
        sheet = result["sheets"][0]
        assert len(sheet["header_cells"]) == 2
        assert sheet["header_cells"][0]["col"] == 1
        assert sheet["header_cells"][0]["value"] == "이름"
