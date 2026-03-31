# Plan 19: Excel -> CSV -> LLM 파이프라인 검증 보고서

> 검증일: 2026-03-23
> 검증 대상: Plan 19 — Excel -> CSV -> LLM -> Excel 파이프라인 전환
> 검증자: verifier agent

---

## 1. 테스트 결과 요약

| 항목 | 결과 |
|------|------|
| 총 테스트 수 | 22 |
| 통과 | 22 |
| 실패 | 0 |
| 테스트 파일 | `tests/test_document/test_excel_csv_converter.py` |
| 실행 시간 | 0.21초 |

### 테스트 목록

| 테스트 | 상태 | 검증 항목 |
|--------|------|-----------|
| `test_excel_to_csv_single_sheet` | PASS | 단일 시트 변환, 헤더/example_rows/csv_text/header_row_index/data_start_row 검증 |
| `test_excel_to_csv_multi_sheet` | PASS | 2개 시트 변환, 시트별 CsvSheetData dict 반환 |
| `test_excel_to_csv_specific_sheet` | PASS | sheet_name 지정 시 해당 시트만 변환 |
| `test_excel_to_csv_with_example_rows` | PASS | 데이터 행 추출 정확성 |
| `test_example_rows_max_50` | PASS | 100행 입력 시 50행 제한 동작 |
| `test_excel_to_csv_empty_template` | PASS | 헤더만 있는 Excel -> example_rows=[], csv_text에 헤더만 |
| `test_excel_to_csv_merged_cells` | PASS | 병합 셀에서 첫 셀 값 추출 |
| `test_excel_to_csv_date_values` | PASS | datetime/date -> ISO 형식 문자열 변환 |
| `test_excel_to_csv_fallback_no_header` | PASS | 헤더 탐지 실패 시 template_structure 폴백 |
| `test_excel_to_csv_fallback_complex` | PASS | 복잡 구조 (정상 시트 + 비정상 시트 혼합) 폴백 |
| `test_csv_sheet_data_structure` | PASS | CsvSheetData 필드 정합성 (Plan 19 스펙 일치) |
| `test_csv_sheet_data_instantiation` | PASS | CsvSheetData 직접 생성 및 필드 접근 |
| `test_none_returns_empty_string` | PASS | _format_cell_value(None) -> "" |
| `test_datetime_returns_iso` | PASS | datetime -> ISO format |
| `test_date_returns_iso` | PASS | date -> ISO format |
| `test_time_returns_iso` | PASS | time -> ISO format |
| `test_number_returns_string` | PASS | 숫자 -> 문자열 |
| `test_string_passthrough` | PASS | 문자열 패스스루 |
| `test_headers_only` | PASS | _build_csv_text 헤더만 |
| `test_headers_with_rows` | PASS | _build_csv_text 헤더+데이터 |
| `test_empty_headers` | PASS | _build_csv_text 빈 입력 |
| `test_invalid_bytes_raises_value_error` | PASS | 잘못된 파일 -> ValueError |

---

## 2. 아키텍처 정합성 (arch-check)

```
검사 파일: 62개
총 import: 184개
허용 import: 184개
위반 (error): 0개
경고 (warning): 0개
```

**결과: 모든 의존성이 Clean Architecture 규칙을 준수합니다.**

`src/document/excel_csv_converter.py`는 infrastructure 계층에 위치하며, 동일 계층의 `src/document/excel_parser.py`만 참조한다. 상위 계층(application/orchestration)으로의 역방향 의존 없음.

### 계층 의존성 매트릭스 (발췌)

| From \ To | domain | config | utils | prompts | infrastructure | application |
|-----------|--------|--------|-------|---------|----------------|-------------|
| infrastructure | 1 | 10 | 3 | 5 | - | - |
| application | 14 | 13 | 1 | 5 | 41 | - |

---

## 3. 코드 리뷰

### 3.1 검토 파일 목록 (7개)

| 파일 | 변경 유형 | 계층 |
|------|-----------|------|
| `src/document/excel_csv_converter.py` | 신규 | infrastructure |
| `src/state.py` | 수정 | domain |
| `src/api/routes/query.py` | 수정 | interface |
| `src/nodes/input_parser.py` | 수정 | application |
| `src/prompts/input_parser.py` | 수정 | prompts |
| `src/prompts/field_mapper.py` | 수정 | prompts |
| `src/document/field_mapper.py` | 수정 | infrastructure |

### 3.2 타입 힌트

모든 변경 파일에서 타입 힌트가 적절히 사용됨:
- `excel_csv_converter.py`: `dict[str, CsvSheetData]`, `list[str]`, `list[list[str]]` 등
- `state.py`: `Optional[dict[str, Any]]`
- `input_parser.py`: `dict | None`, `str`, `list[dict]`
- `field_mapper.py`: `Optional[list[list[str]]]`

### 3.3 Docstring

모든 신규/수정 함수에 한국어 docstring이 작성됨. Args, Returns, Raises 섹션 포함.

### 3.4 에러 핸들링

- **CSV 변환 실패 시 폴백**: `CsvConversionError` 예외로 `template_structure` 기반 폴백 (`excel_csv_converter.py:75-79`)
- **라우트 레벨 폴백**: `query.py:557-563`에서 `excel_to_csv()` 실패 시 `csv_sheet_data=None`으로 기존 파이프라인 유지
- **input_parser 폴백**: 예외 발생 시 최소 파싱 결과로 그래프 진행 (`input_parser.py:76-83`)

### 3.5 보안

- SELECT 외 SQL 차단 관련 변경 없음 (이번 변경은 파서/프롬프트만 해당)
- 민감 데이터 관련 변경 없음

### 3.6 발견된 주의 사항

| 분류 | 내용 | 파일:줄 |
|------|------|---------|
| Minor | `query.py`에서 `from dataclasses import asdict`를 try 블록 내 로컬 임포트로 사용. 표준 라이브러리이므로 ImportError 위험은 없으나, 함수 호출마다 import가 실행됨 | `src/api/routes/query.py:559` |
| Minor | openpyxl이 `date` 객체를 `datetime`으로 변환하는 동작(`data_only=True`)으로 인해 CSV에 `2026-03-23T00:00:00` 형태로 기록됨. 순수 date 값 구분이 불가. `_format_cell_value`에서 `date` 체크가 `datetime` 뒤에 있으나, openpyxl이 이미 datetime으로 변환하므로 `date` 분기는 사실상 도달 불가 | `src/document/excel_csv_converter.py:188-193` |

---

## 4. 기능 완성도 (Plan 19 요구사항 대비)

| 요구사항 | 구현 여부 | 비고 |
|----------|-----------|------|
| CsvSheetData 데이터클래스 | O | 6개 필드 모두 구현 |
| excel_to_csv() 함수 | O | 단일/멀티시트, sheet_name 필터 |
| 예시 데이터 최대 50행 제한 | O | `_MAX_EXAMPLE_ROWS = 50` |
| 날짜/시간 ISO 변환 | O | datetime/date/time 처리 |
| 병합 셀 처리 | O | 첫 셀 값만 추출 |
| CSV 변환 실패 시 template_structure 폴백 | O | CsvConversionError -> parse_excel_template |
| State에 csv_sheet_data 필드 추가 | O | AgentState, create_initial_state 모두 반영 |
| /query/file 라우트에서 CSV 변환 | O | xlsx 조건부 변환, 실패 시 기존 방식 |
| input_parser 시트별 순환 LLM 호출 | O | csv_sheet_data 존재 시 시트별 호출 |
| input_parser CSV 컨텍스트 프롬프트 | O | INPUT_PARSER_CSV_CONTEXT_PROMPT |
| field_mapper 예시 데이터 포함 프롬프트 | O | FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES |
| field_mapper example_rows 파라미터 | O | map_fields_per_sheet, perform_3step_mapping |
| output_generator 변경 없음 유지 | O | 기존 excel_writer 방식 그대로 |

---

## 5. 발견 이슈 목록

| 심각도 | 이슈 | 파일 | 설명 |
|--------|------|------|------|
| Minor | 로컬 import 반복 실행 | `src/api/routes/query.py:558-559` | `dataclasses.asdict`와 `excel_to_csv`를 try 블록 내에서 로컬 임포트. 성능 영향은 미미하나 파일 상단 임포트로 이동하는 것이 관례적 |
| Minor | date vs datetime 구분 불가 | `src/document/excel_csv_converter.py:188-193` | openpyxl의 `data_only=True`로 로드 시 date가 datetime으로 변환되어 `_format_cell_value`의 date 분기에 도달하지 않음. 기능적 문제는 아니나 dead code 발생 |

**Critical 이슈: 0건**
**Major 이슈: 0건**
**Minor 이슈: 2건**

---

## 6. 결론

Plan 19 구현이 요구사항을 충족하며, 아키텍처 위반 없이 안전하게 통합되었다. 21개 단위 테스트 전부 통과. Critical/Major 이슈 없음. Minor 이슈 2건은 기능에 영향을 주지 않는 코드 스타일/dead code 수준이다.
