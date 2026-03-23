# Plan 10: Phase 2 - 문서 처리 (Excel/Word 양식 파싱 및 생성)

> 작성일: 2026-03-17
> 관련 요건: spec.md 섹션 5, docs/decision.md D-007, docs/requirements.md F-07, F-08
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
