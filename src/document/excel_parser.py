"""Excel 양식 구조 분석 모듈.

openpyxl을 사용하여 Excel 파일의 헤더, 데이터 영역, 병합 셀, 수식 셀 정보를 추출한다.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell
from openpyxl.worksheet.worksheet import Worksheet

logger = logging.getLogger(__name__)

# 헤더 행으로 판단하기 위한 최소 비어있지 않은 셀 수
_MIN_HEADER_CELLS = 2

# 헤더를 탐색할 최대 행 수
_MAX_HEADER_SEARCH_ROWS = 20

# 연속 빈 행이 이 수를 초과하면 데이터 영역 끝으로 판단
_MAX_CONSECUTIVE_EMPTY_ROWS = 3


def parse_excel_template(file_data: bytes) -> dict[str, Any]:
    """Excel 양식 파일의 구조를 분석한다.

    Args:
        file_data: Excel 파일 바이너리 데이터

    Returns:
        양식 구조 딕셔너리 (template_structure 형식)

    Raises:
        ValueError: 파일을 읽을 수 없는 경우
    """
    try:
        wb = load_workbook(io.BytesIO(file_data), data_only=False)
    except Exception as e:
        raise ValueError(f"Excel 파일을 읽을 수 없습니다: {e}") from e

    sheets: list[dict[str, Any]] = []

    for ws in wb.worksheets:
        sheet_info = _analyze_sheet(ws)
        if sheet_info is not None:
            sheets.append(sheet_info)

    wb.close()

    logger.info("Excel 양식 분석 완료: %d개 시트", len(sheets))

    return {
        "file_type": "xlsx",
        "sheets": sheets,
        "placeholders": [],
        "tables": [],
    }


def _analyze_sheet(ws: Worksheet) -> dict[str, Any] | None:
    """단일 시트의 구조를 분석한다.

    Args:
        ws: openpyxl Worksheet 객체

    Returns:
        시트 구조 딕셔너리 또는 None (빈 시트)
    """
    header_row, header_cells = _detect_header_row(ws)
    if header_row is None:
        logger.debug("시트 '%s': 헤더를 찾을 수 없어 스킵", ws.title)
        return None

    headers = [cell["value"] for cell in header_cells]
    data_start_row = header_row + 1
    max_column = max(cell["col"] for cell in header_cells) if header_cells else 1

    # 데이터 영역 끝 탐지
    data_end_row = _detect_data_end_row(ws, data_start_row, max_column)

    # 병합 셀 정보
    merged_cells = [str(rng) for rng in ws.merged_cells.ranges]

    # 수식 셀 정보
    formula_cells = _detect_formula_cells(ws, data_start_row, data_end_row, max_column)

    return {
        "name": ws.title,
        "headers": headers,
        "header_row": header_row,
        "data_start_row": data_start_row,
        "data_end_row": data_end_row,
        "header_cells": header_cells,
        "merged_cells": merged_cells,
        "formula_cells": formula_cells,
        "max_column": max_column,
    }


def _detect_header_row(
    ws: Worksheet,
) -> tuple[int | None, list[dict[str, Any]]]:
    """헤더 행을 자동 탐지한다.

    첫 번째로 연속 비어있지 않은 셀이 _MIN_HEADER_CELLS개 이상인 행을 헤더로 판단한다.

    Args:
        ws: openpyxl Worksheet 객체

    Returns:
        (헤더 행 번호(1-based), 헤더 셀 목록) 또는 (None, [])
    """
    for row_idx in range(1, min(ws.max_row or 1, _MAX_HEADER_SEARCH_ROWS) + 1):
        cells: list[dict[str, Any]] = []
        for col_idx in range(1, (ws.max_column or 1) + 1):
            cell: Cell = ws.cell(row=row_idx, column=col_idx)
            value = cell.value
            if value is not None and str(value).strip():
                cells.append({
                    "col": col_idx,
                    "value": str(value).strip(),
                })

        if len(cells) >= _MIN_HEADER_CELLS:
            return row_idx, cells

    return None, []


def _detect_data_end_row(
    ws: Worksheet,
    data_start_row: int,
    max_column: int,
) -> int | None:
    """데이터 영역의 끝 행을 탐지한다.

    연속으로 빈 행이 _MAX_CONSECUTIVE_EMPTY_ROWS개 이상이면 데이터 영역 끝으로 판단한다.
    끝을 찾지 못하면 None (자동 확장 가능)을 반환한다.

    Args:
        ws: openpyxl Worksheet 객체
        data_start_row: 데이터 시작 행 (1-based)
        max_column: 데이터 영역 최대 열

    Returns:
        데이터 끝 행 (1-based) 또는 None
    """
    max_row = ws.max_row or data_start_row
    consecutive_empty = 0
    last_data_row = None

    for row_idx in range(data_start_row, max_row + 1):
        is_empty = True
        for col_idx in range(1, max_column + 1):
            cell_val = ws.cell(row=row_idx, column=col_idx).value
            if cell_val is not None and str(cell_val).strip():
                is_empty = False
                break

        if is_empty:
            consecutive_empty += 1
            if consecutive_empty >= _MAX_CONSECUTIVE_EMPTY_ROWS:
                return last_data_row
        else:
            consecutive_empty = 0
            last_data_row = row_idx

    return last_data_row


def _detect_formula_cells(
    ws: Worksheet,
    data_start_row: int,
    data_end_row: int | None,
    max_column: int,
) -> list[str]:
    """수식이 포함된 셀 위치를 탐지한다.

    Args:
        ws: openpyxl Worksheet 객체
        data_start_row: 데이터 시작 행
        data_end_row: 데이터 끝 행
        max_column: 최대 열

    Returns:
        수식 셀 주소 목록 (예: ["D2", "E3"])
    """
    formula_cells: list[str] = []
    end_row = data_end_row or data_start_row + 100  # 최대 100행 탐색

    for row_idx in range(data_start_row, min(end_row + 1, (ws.max_row or 0) + 1)):
        for col_idx in range(1, max_column + 1):
            cell: Cell = ws.cell(row=row_idx, column=col_idx)
            if isinstance(cell.value, str) and cell.value.startswith("="):
                formula_cells.append(cell.coordinate)

    return formula_cells
