# Plan 19: Excel → CSV → LLM → Excel 파이프라인 전환

> 작성일: 2026-03-23
> 관련 엔드포인트: `/query/file` (`src/api/routes/query.py:526`)
> 관련 모듈: `src/nodes/input_parser.py`, `src/nodes/output_generator.py`, `src/document/`

---

## 1. 개요

### 1.1 현재 방식 (AS-IS)

```
Excel(bytes) ──→ openpyxl 파싱 ──→ template_structure(headers, 구조)
                                         │
                                         ▼
                                   field_mapper(LLM)  ← LLM은 헤더명만 봄
                                         │
                                         ▼
                               SQL 생성/실행 → 결과 rows
                                         │
                                         ▼
                               openpyxl로 원본 Excel에 데이터 채움 → Excel(bytes) 반환
```

- LLM은 Excel 원본 데이터를 직접 보지 못하고, 파싱된 헤더 목록만 전달받음
- `uploaded_file`(bytes)이 State 전체 파이프라인을 통해 전달됨
- 양식 구조 분석(excel_parser) → 필드 매핑(field_mapper) → 데이터 채우기(excel_writer)의 3단계 문서 처리

### 1.2 변경 방식 (TO-BE)

```
Excel(bytes) ──→ CSV 변환 시도 (헤더 + 예시 데이터 추출)
                      │
                      ├─ 성공 → CsvSheetData (헤더 + 예시)
                      │
                      └─ 실패 (복잡한 구조) → template_structure 폴백
                              │                 (기존 excel_parser로 구조 분석 → 헤더 추출)
                              ▼
           input_parser(LLM) ← 시트별 헤더 + 예시 데이터로 질의 분석
                      │
                      ▼
           field_mapper(LLM) ← 헤더 + 예시 데이터로 DB 컬럼 매핑
                      │
                      ▼
           query_generator → SQL 생성 / query_executor → DB 쿼리 실행
                      │
                      ▼
           output_generator → 원본 Excel에 결과 데이터 채움 → Excel(bytes) 반환
```

- CSV 변환을 통해 헤더뿐 아니라 **예시 데이터**도 추출하여 LLM에 전달
- LLM이 예시 데이터 패턴을 참고하여 더 정확한 필드 매핑 수행 (예: "서버명" → `hostname` vs `server_id` 판별)
- **CSV로 단순 변환이 어려운 Excel 구조**(병합셀 중첩, 비정형 레이아웃 등)의 경우, 기존 `excel_parser`의 `template_structure` 기반으로 구조를 분석하여 헤더를 추출하는 폴백 경로 포함
- 기존 파이프라인(field_mapper → SQL → DB 쿼리 → Excel 채우기) 유지, CSV는 LLM 컨텍스트 보강 수단
- 멀티시트 시 시트별로 순환하며 개별 LLM 요청

---

## 2. 상세 설계

### 2.1 Excel → CSV 변환 모듈

**새 파일:** `src/document/excel_csv_converter.py`

#### 반환 구조: `CsvSheetData`

시트별 헤더와 예시 데이터를 구조화하여 LLM에 전달하기 위해 전용 데이터클래스를 도입한다.

```python
@dataclass
class CsvSheetData:
    sheet_name: str
    headers: list[str]              # 헤더 행
    example_rows: list[list[str]]   # 기존 데이터 행 (예시, 최대 50행)
    csv_text: str                   # 전체 CSV 텍스트 (헤더 + 예시)
    header_row_index: int           # 원본 헤더 행 번호 (1-based)
    data_start_row: int             # 데이터 시작 행 (1-based)
```

#### `excel_to_csv` 함수

```python
def excel_to_csv(
    file_data: bytes, sheet_name: str | None = None
) -> dict[str, CsvSheetData]:
    """Excel 파일을 시트별 CsvSheetData로 변환한다.

    Args:
        file_data: Excel 파일 바이너리
        sheet_name: 특정 시트만 변환 (None이면 전체)

    Returns:
        {"시트명": CsvSheetData, ...}
    """
```

**예시 데이터 추출 방식 (기존 `excel_parser` 함수 재활용):**
- `excel_parser._detect_header_row()` 재활용 → 헤더 행 탐지 → `header_row_index`
- `excel_parser._detect_data_end_row()` 재활용 → 데이터 영역 끝 탐지
- `data_start_row` = `header_row_index + 1`
- `data_start_row` ~ `data_end_row` 사이의 실제 데이터 행을 `example_rows`로 추출
- **최대 50행 제한** (LLM 토큰 절약)
- 예시가 없는 시트(빈 양식): `example_rows=[]`, `csv_text`에 헤더만 포함
- `csv_text`는 `csv.writer`로 생성: 헤더 행 + 예시 데이터 행을 포함한 CSV 문자열

**기타 구현 방식:**
- `openpyxl.load_workbook(data_only=True)`로 수식 결과값까지 포함
- 각 시트를 순회하며 `CsvSheetData` 인스턴스 생성
- 빈 행/열 트리밍
- 대용량 방지: 예시 데이터는 최대 50행, 전체 CSV 텍스트는 최대 10,000행

**CSV 변환 시 고려사항:**
- 날짜/시간 값: ISO 형식 문자열로 변환
- 숫자 포맷: 원본 값 유지 (서식 제거)
- 병합 셀: 병합된 첫 번째 셀의 값만 포함
- None/빈 셀: 빈 문자열("")로 처리
- 멀티시트: 시트별 `CsvSheetData`를 dict로 관리

#### CSV 변환 실패 시 폴백: `template_structure` 기반 헤더 추출

CSV로 단순 변환이 어려운 Excel 구조(병합셀 중첩, 비정형 레이아웃, 다단 헤더 등)의 경우, 기존 `excel_parser`의 `template_structure`를 기반으로 구조를 분석하여 헤더를 추출한다.

```python
def excel_to_csv(
    file_data: bytes, sheet_name: str | None = None
) -> dict[str, CsvSheetData]:
    # ...
    for ws in workbook.worksheets:
        try:
            sheet_data = _extract_csv_sheet_data(ws)
        except CsvConversionError:
            # CSV 변환 실패 → template_structure 폴백
            sheet_data = _extract_from_template_structure(ws, file_data)
        result[ws.title] = sheet_data
    return result
```

**폴백 판단 기준:**
- 헤더 행 탐지 실패 (`_detect_header_row()` 결과 없음)
- 병합 셀이 데이터 영역 전체를 덮어 정형 CSV 추출 불가
- 시트 내 데이터 영역이 비연속적 (여러 테이블이 한 시트에 분산)

**폴백 시 동작:**
- 기존 `excel_parser.parse_excel()`로 `template_structure` 생성
- `template_structure`의 `headers` 정보를 `CsvSheetData.headers`에 채움
- `example_rows`는 빈 리스트, `csv_text`는 헤더만 포함

**참고:** `csv_to_excel()` 역변환 함수는 이 계획의 범위에 포함하지 않는다. 출력 단계는 기존 `excel_writer`가 DB 쿼리 결과를 원본 Excel에 직접 채우는 방식을 유지한다.

### 2.2 State 변경

`src/state.py`의 `AgentState`에 추가:

```python
# === CSV 변환 데이터 ===
csv_sheet_data: Optional[dict[str, Any]]  # 시트별 CsvSheetData (dict 형태)
```

- `original_excel_file` 별도 필드는 **불필요** — 기존 `uploaded_file`이 원본 바이너리 보존 역할을 이미 수행
- `csv_sheet_data`에는 `CsvSheetData`를 dict로 직렬화한 형태 저장 (헤더, 예시 데이터, CSV 텍스트 포함)
- `create_initial_state()`에도 해당 필드 초기값(`None`) 추가

### 2.3 `/query/file` 라우트 변경

**파일:** `src/api/routes/query.py` `process_file_query()`

```
변경 전:
  file_bytes → create_initial_state(uploaded_file=file_bytes, file_type="xlsx")
  → 그래프가 input_parser에서 openpyxl로 구조 분석

변경 후:
  file_bytes → excel_to_csv(file_bytes) → csv_sheet_dict (시트별 CsvSheetData)
  → create_initial_state(
      uploaded_file=file_bytes,        # 원본 보존 (서식 복원용)
      csv_sheet_data=csv_sheet_dict,   # 시트별 헤더+예시 (LLM 전달용)
      file_type="xlsx",
    )
```

### 2.4 input_parser 노드 변경 — 멀티시트 순환 LLM 호출

**파일:** `src/nodes/input_parser.py`

현재 `_parse_uploaded_file()`은 openpyxl로 Excel 구조를 파싱하여 `template_structure`를 생성한다.

**변경:**
- `csv_sheet_data`가 State에 존재하면, **시트별로 순환하며 개별 LLM 요청**
- 각 시트의 헤더 + 예시 데이터를 LLM에 전달하여 시트 컨텍스트에 맞는 파싱 수행
- 기존 `_parse_uploaded_file()`은 서식 보존이 필요한 경우에만 호출 (원본 Excel → template_structure)
- 기존 패턴 참고: `field_mapper.map_fields_per_sheet()` (`src/document/field_mapper.py:85-141`) — 시트 목록을 순회하며 각 시트의 헤더로 개별 LLM 호출하는 동일한 패턴

```python
# 시트별 순환 LLM 호출
if state.get("csv_sheet_data"):
    all_sheet_results = []
    for sheet_name, sheet_data in state["csv_sheet_data"].items():
        # 시트별 헤더 + 예시 데이터를 LLM 컨텍스트로 포맷팅
        csv_context = _format_single_sheet_csv(sheet_data)
        sheet_parsed = await _parse_natural_language_with_csv(
            llm, state["user_query"], csv_context, sheet_name=sheet_name
        )
        all_sheet_results.append(sheet_parsed)

    # 시트별 파싱 결과 병합
    parsed = _merge_sheet_parse_results(all_sheet_results)

    # 서식 보존을 위해 template_structure도 병행 생성
    if state.get("uploaded_file"):
        template = _parse_uploaded_file(state["uploaded_file"], state["file_type"])
```

**시트별 개별 호출의 이점:**
- 각 시트가 서로 다른 도메인(서버 목록, CPU 지표, 네트워크 트래픽 등)을 담당할 수 있으므로, 시트 컨텍스트에 특화된 파싱이 가능
- 토큰 사용량 분산: 전체 시트를 한 번에 보내는 것보다 시트별로 나누어 보내는 것이 컨텍스트 윈도우 관리에 유리
- `map_fields_per_sheet()`에서 검증된 패턴 재활용

### 2.5 output_generator 노드 — 변경 없음 (기존 방식 유지)

**파일:** `src/nodes/output_generator.py`

기존 `_generate_document_file()`은 openpyxl로 원본 Excel 템플릿에 DB 쿼리 결과를 채운다. **이 방식을 그대로 유지한다.**

- DB 쿼리 실행 결과(`query_results`)를 기존 `excel_writer`로 원본 Excel에 채움
- CSV 변환은 input_parser/field_mapper의 LLM 컨텍스트 보강 목적이며, output 단계에서는 관여하지 않음
- 원본 Excel 서식/병합셀/수식 보존은 기존 `excel_writer`가 담당

### 2.6 LLM 프롬프트 변경

**파일:** `src/prompts/input_parser.py` (수정), `src/prompts/field_mapper.py` (수정)

#### input_parser 프롬프트 — 헤더 + 예시 데이터 포함 형식

시트별 순환 호출 시 각 시트의 헤더와 예시 데이터를 아래 형식으로 LLM에 전달:

```
### 시트: {sheet_name}

#### 헤더
서버명, IP주소, CPU사용률, 메모리사용률

#### 예시 데이터 ({n}행)
```csv
서버명,IP주소,CPU사용률,메모리사용률
web-server-01,192.168.1.10,45.2,67.8
db-server-02,10.0.0.5,82.1,91.3
```

이 데이터의 패턴을 참고하여 사용자의 질의를 분석하세요.
```

- 예시 데이터가 없는 시트(빈 양식)에서는 "예시 데이터" 섹션을 생략하고 헤더만 전달
- `_format_single_sheet_csv(sheet_data: CsvSheetData) -> str` 헬퍼가 위 형식을 생성

#### field_mapper 프롬프트 — 예시 데이터 포함

`src/prompts/field_mapper.py`에 `FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES` 추가. `perform_3step_mapping()`의 LLM 단계(3단계)에서 예시 데이터를 프롬프트에 포함하여 매핑 정확도를 향상:

```
## 양식 필드 목록 (예시 데이터 포함)
- 서버명 (예시: "web-server-01", "db-server-02")
- IP주소 (예시: "192.168.1.10", "10.0.0.5")
- CPU사용률 (예시: "45.2", "82.1")
- 메모리사용률 (예시: "67.8", "91.3")
```

예시 데이터가 있으면 LLM이 필드의 실제 데이터 패턴을 파악하여 DB 컬럼과의 매핑 정확도가 높아진다 (예: "서버명"이 hostname인지 server_id인지 예시 값으로 판별 가능).

---

## 3. 영향 범위 분석

### 3.1 새로 생성하는 파일

| 파일 | 역할 |
|------|------|
| `src/document/excel_csv_converter.py` | Excel→CSV 변환 (`CsvSheetData` 생성) |

### 3.2 수정하는 파일

| 파일 | 변경 내용 |
|------|-----------|
| `src/state.py` | `csv_sheet_data` 필드 추가 |
| `src/api/routes/query.py` | `/query/file`에서 Excel→CSV 변환 후 State 생성 |
| `src/nodes/input_parser.py` | 시트별 순환 LLM 호출, CSV 헤더+예시를 LLM 컨텍스트에 포함 |
| `src/prompts/input_parser.py` | CSV 컨텍스트 프롬프트 추가 (헤더 + 예시 데이터 형식) |
| `src/prompts/field_mapper.py` | `FIELD_MAPPER_USER_PROMPT_WITH_EXAMPLES` 추가 (예시 데이터 포함 매핑 프롬프트) |

### 3.3 기존 모듈과의 관계

| 기존 모듈 | 영향 |
|-----------|------|
| `excel_parser.py` | `_detect_header_row()`, `_detect_data_end_row()` 재활용. CSV 변환기가 헤더+예시 추출에 활용 |
| `excel_writer.py` | **변경 없음** — 기존대로 DB 쿼리 결과를 원본 Excel에 채우는 역할 유지 |
| `field_mapper.py` | 예시 데이터가 프롬프트에 추가되어 매핑 정확도 향상. `map_fields_per_sheet()` 패턴 재활용 |

---

## 4. 엣지 케이스 및 제약사항

### 4.1 CSV 변환 시 데이터 손실 위험

| 항목 | 위험 | 대응 |
|------|------|------|
| 병합 셀 | CSV로 표현 불가 | 병합 범위 첫 셀값만 유지, 원본 Excel 보존으로 복원 |
| 수식 | CSV에는 결과값만 포함 | `data_only=True`로 결과값 추출, 수식 보존은 원본에서 |
| 서식 (폰트, 색상 등) | CSV로 표현 불가 | 원본 Excel을 `uploaded_file`로 보존, 기존 `excel_writer`가 서식 유지하며 데이터 채움 |
| 차트/이미지 | CSV로 표현 불가 | 원본 Excel에서 보존 |
| 멀티시트 | CSV는 단일 시트 | 시트별 CSV를 dict로 관리, LLM에 구분하여 전달 |

### 4.2 대용량 데이터

- CSV 텍스트가 LLM 컨텍스트 윈도우를 초과할 수 있음
- **대응:** 최대 행 수 제한 (기본 500행), 초과 시 요약 + 샘플 행 전달
- 토큰 수 기반 트리밍 (대략 1행 ≈ 50~100 토큰 추정, 500행 ≈ 25K~50K 토큰)

---

## 5. 서식 보존 전략

기존 `excel_writer`의 서식 보존 방식을 그대로 유지:

```
1. 원본 Excel → CSV (헤더 + 예시 추출, LLM 컨텍스트용)
2. 헤더 + 예시 → field_mapper(LLM) → SQL → DB 쿼리 → 결과 rows
3. 결과 rows + 원본 Excel → excel_writer → 최종 Excel
   ├─ 원본 Excel을 openpyxl로 로드 (서식 유지)
   └─ 원본 Excel의 데이터 영역에 DB 쿼리 결과를 채움
```

서식/병합셀/수식 구조는 원본에서 유지하고, 데이터만 DB 쿼리 결과로 채운다.

---

## 6. 구현 순서

| 단계 | 작업 | 파일 |
|------|------|------|
| 1 | `CsvSheetData` 및 Excel→CSV 변환 모듈 구현 | `src/document/excel_csv_converter.py` |
| 2 | State에 `csv_sheet_data` 필드 추가 | `src/state.py` |
| 3 | `/query/file` 라우트에서 CSV 변환 적용 | `src/api/routes/query.py` |
| 4 | input_parser에서 시트별 순환 LLM 호출 | `src/nodes/input_parser.py`, `src/prompts/input_parser.py` |
| 5 | field_mapper 프롬프트에 예시 데이터 포함 | `src/prompts/field_mapper.py` |
| 6 | 단위 테스트 작성 | `tests/test_document/test_excel_csv_converter.py` |
| 7 | 통합 테스트 | `tests/test_e2e_file_query.py` |

---

## 7. 테스트 계획

### 7.1 단위 테스트

```
tests/test_document/test_excel_csv_converter.py
  - test_excel_to_csv_single_sheet         # 단일 시트 → CsvSheetData
  - test_excel_to_csv_multi_sheet          # 멀티 시트 → 시트별 CsvSheetData
  - test_excel_to_csv_with_example_rows    # 예시 데이터 행 추출 (최대 50행)
  - test_excel_to_csv_empty_template       # 빈 양식 (헤더만, example_rows=[])
  - test_excel_to_csv_merged_cells         # 병합 셀 처리
  - test_excel_to_csv_formula_cells        # 수식 셀 (결과값 추출)
  - test_excel_to_csv_date_values          # 날짜/시간 변환
  - test_excel_to_csv_large_file           # 대용량 행 제한 (50행 예시 제한)
  - test_excel_to_csv_fallback_complex     # 복잡 구조 → template_structure 폴백
  - test_excel_to_csv_fallback_no_header   # 헤더 탐지 실패 → 폴백
  - test_csv_sheet_data_structure          # CsvSheetData 필드 정합성 확인
```

### 7.2 통합 테스트

```
tests/test_e2e_file_query.py
  - test_file_query_csv_context_pipeline   # CSV 헤더+예시 → field_mapper → SQL → DB → Excel
  - test_file_query_multi_sheet_loop       # 멀티시트 시트별 순환 LLM 호출
  - test_file_query_format_preservation    # 서식 보존 확인
  - test_file_query_empty_template         # 빈 양식 처리 (헤더만 있는 경우)
```

---

## 8. 기존 기능과의 호환성

- **Word(.docx) 파일**: 이번 변경 대상 아님, 기존 방식 유지
- **텍스트 질의 (`/query`)**: 영향 없음
- **멀티턴 대화**: CSV 데이터는 첫 턴에서만 State에 저장, 후속 턴에서는 체크포인트에서 복원
- **다운로드 엔드포인트 (`/query/{id}/download`)**: 변경 없음 (output_file bytes 반환)

---

## 9. 미결 사항 (구현 전 확인 필요)

1. **예시 데이터 최대 행 수**: 50행 기본값이 적절한지, 도메인별 조정 필요 여부
2. **시트별 LLM 호출 병렬화**: 시트 수가 많을 경우 `asyncio.gather`로 병렬 호출할지 순차 호출 유지할지
3. **예시 데이터 없는 시트**: 빈 양식에서 field_mapper 매핑 정확도가 충분한지 검증 필요


---

# Verification Report

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
