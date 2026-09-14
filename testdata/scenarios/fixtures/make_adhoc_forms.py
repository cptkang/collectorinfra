"""H군 '자작 양식' 픽스처 생성기 (plans/94 H-04·H-05·H-06).

H군 시나리오 17건이 전부 `form_sample.xlsx` 하나를 올리고 있었다(실측 2026-09-14).
원문이 서술하는 양식은 시나리오마다 다른데 같은 파일을 보내면 **양식 해석을 시험하지
못한다** - 무엇을 채워야 하는지가 파일에 없으니 채움 결과도 판정할 수 없다.

여기서 만드는 셋은 저장소 어디에도 없던 것이다. 나머지는 이미 있는 것을 쓴다:
  · `testdata/templates/`      server_list / resource_status
  · `tests/fixtures/forms/`    CPU_양식 / 메모리_양식 / 서버_목록_리스트_양식
  · `tests/e2e/fixtures/`      sample_template.docx

    python testdata/scenarios/fixtures/make_adhoc_forms.py
"""

from __future__ import annotations

from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

HERE = Path(__file__).resolve().parent

#: (파일명, 시트명, 헤더). 헤더는 시나리오 원문이 서술한 열 그대로다.
FORMS: list[tuple[str, str, list[str]]] = [
    # H-04 - '서버명'은 EAV Hostname 이 아니라 `cmm_resource.name` 이다(D-148).
    ("adhoc_server_info.xlsx", "서버정보",
     ["서버명", "호스트명", "IP", "OS버전", "메모리용량", "비고"]),
    # H-05 - classify_metric_field 가 (Cpus,AVG)·(Cpus,MAX)·(Memory,AVG) 로 갈라야 한다.
    ("adhoc_cpu_memory.xlsx", "성능요약",
     ["호스트명", "CPU 평균", "CPU 최고", "메모리 평균"]),
    # H-06 - 뒤 3열은 관측 도메인 밖이다. 공란 + 사유 노출이 정답이다(침묵 공란 금지).
    ("adhoc_out_of_domain.xlsx", "장비대장",
     ["호스트명", "TPMC", "도입일자", "용도"]),
    # I군 - '담당자'는 어느 컬럼에도 매핑되지 않아 폼필 역질문을 유발한다.
    # 나머지 열은 전부 매핑되므로 "모든 열이 0이면 재질문 아님" 경로와 구별된다.
    ("adhoc_owner_column.xlsx", "서버정보",
     ["서버명", "호스트명", "IP", "OS버전", "메모리용량", "비고", "담당자"]),
]


def build(path: Path, sheet: str, headers: list[str]) -> None:
    book = openpyxl.Workbook()
    sheet_obj = book.active
    sheet_obj.title = sheet
    fill = PatternFill("solid", fgColor="DAEEF3")
    for index, name in enumerate(headers, start=1):
        cell = sheet_obj.cell(row=1, column=index, value=name)
        cell.font = Font(bold=True, size=11)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        sheet_obj.column_dimensions[cell.column_letter].width = max(12, len(name) * 2)
    # 데이터 행은 비워 둔다 - 채우는 것이 시험 대상이다.
    book.save(path)


def main() -> None:
    for name, sheet, headers in FORMS:
        target = HERE / name
        build(target, sheet, headers)
        print(f"생성: {target.relative_to(HERE.parents[2])} ({len(headers)}열)")


if __name__ == "__main__":
    main()
