# Plan 10: Phase 2 - 문서 처리 (Excel/Word 양식 파싱 및 생성)

> 작성일: 2026-03-17
> 관련 요건: spec.md 섹션 5, docs/02_decision.md D-007, docs/01_requirements.md F-07, F-08
> 선행 조건: Phase 1 완료 (LangGraph 파이프라인, 330개 테스트 통과)

---

## 1. 개요

사용자가 Excel(.xlsx) 또는 Word(.docx) 양식 파일을 업로드하면:
1. 양식 구조를 분석하여 헤더/플레이스홀더를 추출
2. LLM이 양식 필드명과 DB 컬럼명 간 의미적 매핑 수행
3. 매핑된 컬럼으로 SQL 생성/실행 (Phase 1 파이프라인 재사용)
4. 조회 결과를 양식에 채워 완성된 파일을 반환

## 2. 파일 구조

```
src/document/
  __init__.py
  excel_parser.py      # Excel 양식 구조 분석
  word_parser.py       # Word 양식 구조 분석
  excel_writer.py      # Excel 양식에 데이터 채우기
  word_writer.py       # Word 양식에 데이터 채우기
  field_mapper.py      # LLM 기반 필드-컬럼 매핑

src/prompts/
  field_mapper.py      # 필드 매핑 LLM 프롬프트

# 기존 파일 수정
src/nodes/input_parser.py      # _parse_uploaded_file 연동 (이미 스텁 존재)
src/nodes/result_organizer.py  # column_mapping 생성 로직 추가
src/nodes/output_generator.py  # xlsx/docx 파일 생성 로직 추가
```

## 3. 모듈별 상세 설계

### 3.1 excel_parser.py

**함수:** `parse_excel_template(file_data: bytes) -> dict`

**처리 흐름:**
1. `io.BytesIO`로 바이너리 -> openpyxl Workbook 로드
2. 각 시트를 순회하며:
   - 헤더 행 자동 탐지: 첫 번째 비어있지 않은 행 탐색 (연속으로 2개 이상 셀에 값이 있는 행)
   - 데이터 시작 행: 헤더 행 + 1
   - 데이터 종료 행: 연속 빈 행이 나올 때까지 (또는 시트 끝)
   - 병합 셀 정보 수집: `ws.merged_cells.ranges`
   - 수식 셀 위치 기록: 값이 `=`로 시작하는 셀
3. 반환 구조:
```python
{
    "file_type": "xlsx",
    "sheets": [
        {
            "name": str,               # 시트명
            "headers": list[str],       # 헤더 텍스트 목록
            "header_row": int,          # 헤더 행 번호 (1-based)
            "data_start_row": int,      # 데이터 시작 행 (1-based)
            "data_end_row": int | None, # 데이터 영역 끝 (None=자동확장)
            "header_cells": list[dict], # [{"col": int, "value": str}]
            "merged_cells": list[str],  # ["A1:C1", ...]
            "formula_cells": list[str], # ["D2", "E2", ...]
            "max_column": int,          # 데이터 영역 최대 열
        }
    ],
    "placeholders": [],  # Excel에는 해당 없음
    "tables": [],        # Excel에는 해당 없음
}
```

**엣지 케이스:**
- 빈 시트: headers=[], 스킵
- 모든 셀이 비어있는 경우: 빈 결과 반환
- 병합 셀이 헤더에 있는 경우: 병합 범위의 첫 번째 셀 값을 사용

### 3.2 word_parser.py

**함수:** `parse_word_template(file_data: bytes) -> dict`

**처리 흐름:**
1. `io.BytesIO`로 바이너리 -> python-docx Document 로드
2. 본문 단락에서 `{{placeholder}}` 패턴 추출: `re.findall(r'\{\{(.+?)\}\}', text)`
3. 표(Table) 구조 분석:
   - 각 표의 첫 번째 행을 헤더로 인식
   - 헤더 셀의 텍스트 추출
   - 데이터 행 수 카운트
   - 표 내부의 `{{placeholder}}` 패턴도 수집
4. 반환 구조:
```python
{
    "file_type": "docx",
    "sheets": [],  # Word에는 해당 없음
    "placeholders": ["서버명", "IP주소", ...],  # {{ }} 제거한 이름
    "tables": [
        {
            "index": int,               # 표 인덱스 (0-based)
            "headers": list[str],        # 첫 번째 행 텍스트
            "row_count": int,            # 데이터 행 수 (헤더 제외)
            "has_placeholder_cells": bool, # 셀에 placeholder 존재 여부
        }
    ],
}
```

### 3.3 field_mapper.py

**함수:** `async map_fields(llm, template_structure: dict, schema_info: dict) -> dict[str, str]`

**처리 흐름:**
1. template_structure에서 필드명 추출:
   - Excel: sheets[*].headers
   - Word: placeholders + tables[*].headers
2. schema_info에서 사용 가능한 테이블.컬럼 목록 생성
3. LLM에 프롬프트 전송: "양식 필드명 -> DB 테이블.컬럼 매핑"
4. LLM 응답을 JSON으로 파싱
5. 매핑되지 않은 필드는 None으로 처리

**반환 구조:**
```python
{
    "서버명": "servers.hostname",
    "IP주소": "servers.ip_address",
    "CPU 사용률": "cpu_metrics.usage_pct",
    "비고": None,  # 매핑 불가
}
```

### 3.4 src/prompts/field_mapper.py

```python
FIELD_MAPPER_SYSTEM_PROMPT = """당신은 양식 필드와 DB 컬럼 간의 매핑 전문가입니다.
...
출력: JSON 형식으로만 응답
"""

FIELD_MAPPER_USER_PROMPT = """
## 양식 필드 목록
{field_names}

## DB 스키마 (테이블.컬럼)
{schema_columns}

각 양식 필드에 대해 가장 적합한 DB 컬럼을 매핑하세요.
매핑할 수 없는 필드는 null로 표시하세요.

JSON 형식:
{{"필드명1": "테이블.컬럼", "필드명2": null, ...}}
"""
```

### 3.5 excel_writer.py

**함수:** `fill_excel_template(file_data: bytes, template_structure: dict, column_mapping: dict, rows: list[dict]) -> bytes`

**처리 흐름:**
1. 원본 Workbook 로드 (data_only=False로 수식 보존)
2. 각 시트의 매핑된 헤더에 대해:
   - 헤더 열 인덱스와 DB 컬럼 매핑 확인
   - data_start_row부터 rows를 순서대로 기입
   - 수식 셀은 건너뛰기 (formula_cells)
   - 병합 셀은 보존 (원본 구조 유지)
3. BytesIO에 저장하여 바이너리 반환

**서식 보존 전략:**
- openpyxl은 기본적으로 기존 서식을 유지함
- 새로 기입하는 셀에는 헤더 행 아래 첫 데이터 행의 서식을 복사
- 숫자/날짜 포맷이 설정된 셀은 해당 포맷 유지

### 3.6 word_writer.py

**함수:** `fill_word_template(file_data: bytes, template_structure: dict, column_mapping: dict, rows: list[dict], single_row: dict | None = None) -> bytes`

**처리 흐름:**
1. 원본 Document 로드
2. **단일값 치환** (플레이스홀더):
   - 단락에서 `{{field_name}}` 패턴 찾기
   - column_mapping에서 해당 field_name의 DB 컬럼 확인
   - single_row 또는 rows[0]에서 값 추출하여 치환
   - Run 레벨에서 치환하여 스타일 보존
3. **표 데이터 채우기**:
   - 표 헤더와 column_mapping 매칭
   - 데이터 행 추가 (기존 행의 스타일 복사)
   - 기존 빈 행에 먼저 채우고, 초과 시 행 추가
4. BytesIO에 저장하여 바이너리 반환

**스타일 보존 전략:**
- Run 레벨 치환: paragraph.runs를 순회하며 placeholder가 포함된 run의 텍스트만 교체
- 표 셀: 기존 행의 `_element`를 deepcopy하여 새 행 생성, 텍스트만 교체

### 3.7 기존 노드 수정

#### input_parser.py
- 현재: `_parse_uploaded_file`이 ImportError 시 None 반환 (스텁)
- 변경 없음: `src.document.excel_parser`와 `src.document.word_parser`가 구현되면 자동 연동

#### result_organizer.py
- "Phase 2 - 현재는 스킵" 주석 위치에 field_mapper 호출 로직 추가
- `template_structure`가 있고 `schema_info`가 있을 때:
  1. `field_mapper.map_fields(llm, template, schema_info)` 호출
  2. 결과를 `column_mapping`에 저장
- llm 파라미터 추가 필요 (현재 result_organizer에는 llm이 없음)

#### output_generator.py
- `output_format == "xlsx"` 분기에서:
  1. `excel_writer.fill_excel_template()` 호출
  2. 파일명 생성: `result_{timestamp}.xlsx`
  3. `output_file`, `output_file_name` 반환
- `output_format == "docx"` 분기에서:
  1. `word_writer.fill_word_template()` 호출
  2. 파일명 생성: `result_{timestamp}.docx`
  3. `output_file`, `output_file_name` 반환
- 파일 생성 실패 시: 텍스트 응답으로 폴백 + error_message 로깅

## 4. graph.py 수정

- `result_organizer` 노드에 `llm` 파라미터 주입 추가:
  ```python
  graph.add_node(
      "result_organizer",
      partial(result_organizer, llm=llm, app_config=config),
  )
  ```

## 5. 의존성 변경

- `pyproject.toml`의 `[project.optional-dependencies] document`에 이미 openpyxl, python-docx 선언됨
- 추가 필요 없음 (단, 개발 시 `pip install -e ".[document]"` 필요)

## 6. 테스트 계획

```
tests/test_document/
  __init__.py
  test_excel_parser.py     # Excel 파싱 단위 테스트
  test_word_parser.py      # Word 파싱 단위 테스트
  test_excel_writer.py     # Excel 생성 단위 테스트
  test_word_writer.py      # Word 생성 단위 테스트
  test_field_mapper.py     # 필드 매핑 단위 테스트 (LLM mock)
  test_integration.py      # 통합 테스트 (파싱 -> 매핑 -> 생성)
```

**주요 테스트 시나리오:**
- 단일 시트 Excel 파싱/생성
- 다중 시트 Excel 파싱/생성
- 병합 셀이 있는 Excel 보존
- 수식 셀 보존
- Word 플레이스홀더 치환
- Word 표 데이터 채우기
- Word 스타일 보존
- 빈 양식 파일 처리
- 매핑 불가 필드 처리
- 대량 데이터 (10,000건) Excel 생성 성능

## 7. 구현 순서

1. `src/document/__init__.py` (빈 파일)
2. `src/document/excel_parser.py`
3. `src/document/word_parser.py`
4. `src/prompts/field_mapper.py`
5. `src/document/field_mapper.py`
6. `src/document/excel_writer.py`
7. `src/document/word_writer.py`
8. `src/nodes/result_organizer.py` 수정
9. `src/nodes/output_generator.py` 수정
10. `src/graph.py` 수정


---

# Verification Report

# Phase 2 문서 처리 기능 - 검증 보고서

> 검증일: 2026-03-17
> 검증 대상: Phase 2 Excel/Word 양식 파싱 및 생성 기능

---

## 1. 테스트 결과 요약

| 항목 | 수치 |
|------|------|
| 전체 테스트 수 | 406 |
| 통과 | 406 |
| 실패 | 0 |
| 기존 테스트 (Phase 1 + 시멘틱 라우팅) | 356 |
| 신규 테스트 (Phase 2 문서 처리) | 50 |
| 회귀 테스트 실패 | 0 |

## 2. 모듈별 테스트 결과

### 2.1 Excel 파서 (excel_parser.py) - 9개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_single_sheet | 기본 단일 시트 파싱 | PASS |
| test_header_detection_non_first_row | 비첫행 헤더 탐지 | PASS |
| test_empty_sheet_skipped | 빈 시트 스킵 | PASS |
| test_merged_cells_detected | 병합 셀 탐지 | PASS |
| test_formula_cells_detected | 수식 셀 탐지 | PASS |
| test_multi_sheet | 다중 시트 파싱 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_data_end_row_detection | 데이터 영역 끝 탐지 | PASS |
| test_header_cells_structure | 헤더 셀 구조 검증 | PASS |

### 2.2 Word 파서 (word_parser.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_placeholders | 본문 플레이스홀더 추출 | PASS |
| test_table_structure | 표 구조 분석 | PASS |
| test_table_with_placeholders | 표 내부 플레이스홀더 | PASS |
| test_no_placeholders_or_tables | 빈 문서 처리 | PASS |
| test_duplicate_placeholders_deduplicated | 중복 제거 | PASS |
| test_multiple_tables | 다중 표 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_mixed_paragraphs_and_tables | 혼합 문서 | PASS |

### 2.3 Excel 작성기 (excel_writer.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_data_fill | 기본 데이터 채우기 | PASS |
| test_preserves_headers | 헤더 보존 | PASS |
| test_formula_cells_preserved | 수식 보존 | PASS |
| test_unmapped_columns_ignored | 미매핑 컬럼 스킵 | PASS |
| test_empty_rows | 빈 결과 처리 | PASS |
| test_column_name_case_insensitive | 대소문자 무시 | PASS |
| test_multiple_rows | 다중 행 채우기 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |

### 2.4 Word 작성기 (word_writer.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_placeholder_replacement | 플레이스홀더 치환 | PASS |
| test_table_data_fill | 표 데이터 채우기 | PASS |
| test_table_placeholder_replacement | 표 내부 치환 | PASS |
| test_unmapped_placeholder_cleared | 미매핑 플레이스홀더 처리 | PASS |
| test_empty_rows | 빈 결과 처리 | PASS |
| test_single_row_parameter | 단일 행 매개변수 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_mixed_placeholders_and_tables | 혼합 문서 채우기 | PASS |

### 2.5 필드 매퍼 (field_mapper.py) - 15개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_excel_headers | Excel 헤더 추출 | PASS |
| test_word_placeholders | Word 플레이스홀더 추출 | PASS |
| test_deduplication | 중복 필드 제거 | PASS |
| test_empty_template | 빈 양식 | PASS |
| test_basic_format | 스키마 포맷 | PASS |
| test_empty_schema | 빈 스키마 | PASS |
| test_valid_mapping | 유효 매핑 보존 | PASS |
| test_invalid_column_removed | 무효 컬럼 제거 | PASS |
| test_null_mapping_preserved | null 매핑 유지 | PASS |
| test_successful_mapping | LLM 매핑 성공 | PASS |
| test_llm_returns_json_in_codeblock | 코드블록 JSON 파싱 | PASS |
| test_empty_fields | 빈 필드 | PASS |
| test_empty_schema (map_fields) | 빈 스키마 | PASS |
| test_llm_failure_returns_none_mapping | LLM 실패 처리 | PASS |
| test_invalid_json_retries | JSON 파싱 재시도 | PASS |

### 2.6 통합 테스트 - 2개 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_full_excel_flow | Excel 파싱->매핑->생성 전체 흐름 | PASS |
| test_full_word_flow | Word 파싱->매핑->생성 전체 흐름 | PASS |

## 3. 요건 충족 검증

### F-07: Excel 양식 처리

| 수용 기준 | 상태 |
|-----------|------|
| Excel 파일의 시트별 헤더, 데이터 영역, 병합 셀 정보 추출 | 충족 |
| 데이터를 정확한 셀 위치에 채워넣기 | 충족 |
| 원본의 병합 셀, 서식, 수식 보존 | 충족 |
| 생성된 파일이 정상적으로 열리는지 검증 | 충족 |

### F-08: Word 양식 처리

| 수용 기준 | 상태 |
|-----------|------|
| {{placeholder}} 패턴과 표 구조 정확히 추출 | 충족 |
| 플레이스홀더를 실제 데이터로 치환 | 충족 |
| 표 데이터 행에 결과 채우기 | 충족 |
| 원본 스타일 및 서식 보존 | 충족 |

### D-007: LLM 의미 매핑

| 수용 기준 | 상태 |
|-----------|------|
| 양식 필드명과 DB 컬럼명 간 LLM 매핑 | 충족 |
| 매핑 불가 필드 null 처리 | 충족 |
| 매핑 결과 검증 (존재하지 않는 컬럼 제거) | 충족 |
| 대소문자 무시 매칭 | 충족 |

## 4. 기존 코드 호환성

- 기존 356개 테스트 모두 통과 (회귀 0건)
- `input_parser.py`: 유효하지 않은 파일 업로드 시 에러 처리 추가 (기존 스텁 동작 호환)
- `output_generator.py`: xlsx/docx 폴백 메시지 변경 (테스트 1건 업데이트)
- `result_organizer.py`: llm 파라미터 추가 (기존 호출에 영향 없음, 기본값 None)
- `graph.py`: result_organizer에 llm 주입 추가

## 5. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/document/__init__.py` | 신규 | 문서 처리 모듈 패키지 |
| `src/document/excel_parser.py` | 신규 | Excel 양식 구조 분석 |
| `src/document/word_parser.py` | 신규 | Word 양식 구조 분석 |
| `src/document/field_mapper.py` | 신규 | LLM 기반 필드-컬럼 매핑 |
| `src/document/excel_writer.py` | 신규 | Excel 양식 데이터 채우기 |
| `src/document/word_writer.py` | 신규 | Word 양식 데이터 채우기 |
| `src/prompts/field_mapper.py` | 신규 | 필드 매핑 LLM 프롬프트 |
| `src/nodes/input_parser.py` | 수정 | 파서 에러 처리 추가 |
| `src/nodes/result_organizer.py` | 수정 | 양식 매핑 로직 추가, llm 파라미터 추가 |
| `src/nodes/output_generator.py` | 수정 | xlsx/docx 파일 생성 로직 추가 |
| `src/graph.py` | 수정 | result_organizer에 llm 주입 |
| `plans/10-document-processing.md` | 신규 | Phase 2 구현 계획서 |
| `tests/test_document/` | 신규 | 50개 테스트 |
| `tests/test_nodes/test_output_generator.py` | 수정 | 폴백 테스트 업데이트 |

## 6. Critical 이슈

없음.

## 7. 권장 사항 (Non-Critical)

1. **대용량 데이터 성능**: 10,000건 이상의 Excel 생성 시 성능 테스트 별도 수행 권장.
2. **복잡한 양식**: 다중 시트 간 크로스 참조, 피벗 테이블 등은 현재 미지원. 필요 시 단계적 확장.
3. **Run 분리 플레이스홀더**: Word에서 {{placeholder}}가 여러 Run에 걸쳐 분리된 경우, 현재는 전체 텍스트를 합쳐서 치환 후 첫 번째 Run에 설정. 복잡한 서식이 손실될 수 있음.

---
