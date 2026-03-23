# 04. 각 노드의 상세 구현 계획

> 기존 `src/nodes/*.py` 분석 및 개선/확장 계획

---

## 1. 전체 노드 목록 및 구현 현황

| 노드 | 파일 | Phase 1 | Phase 2 | 개선 필요 |
|------|------|---------|---------|----------|
| input_parser | `src/nodes/input_parser.py` | 구현 완료 | 양식 파싱 스텁 | 에러 처리 보강 |
| schema_analyzer | `src/nodes/schema_analyzer.py` | 구현 완료 | - | LLM 기반 테이블 매칭 추가 |
| query_generator | `src/nodes/query_generator.py` | 구현 완료 | 양식 매핑 SQL | 프롬프트 개선 |
| query_validator | `src/nodes/query_validator.py` | 구현 완료 | - | 주석 내 키워드 처리 |
| query_executor | `src/nodes/query_executor.py` | 구현 완료 | - | query_attempts 이력 기록 |
| result_organizer | `src/nodes/result_organizer.py` | 구현 완료 | 양식 매핑 | LLM 요약 통합 |
| output_generator | `src/nodes/output_generator.py` | 구현 완료 | Excel/Word 생성 | 파일 생성 로직 |
| error_response | `src/graph.py` 내 함수 | 구현 완료 | - | OK |

---

## 2. input_parser 노드

### 2.1 현재 구현 분석

- LLM에 `INPUT_PARSER_SYSTEM_PROMPT`를 전달하여 자연어를 JSON으로 변환
- `_extract_json_from_response()`로 LLM 응답에서 JSON 파싱 (markdown 코드블록 처리)
- Phase 2 양식 파싱은 `ImportError`로 스텁 처리

### 2.2 개선 사항

**a) 에러 처리 보강**

```python
async def input_parser(state: AgentState) -> dict:
    try:
        config = load_config()
        llm = create_llm(config)
        parsed = await _parse_natural_language(llm, state["user_query"])
    except Exception as e:
        logger.error(f"입력 파싱 실패: {e}")
        # 최소한의 파싱 결과로 진행 (그래프가 중단되지 않도록)
        parsed = {
            "original_query": state["user_query"],
            "query_targets": [],
            "filter_conditions": [],
            "output_format": "text",
        }
    # ...
```

**b) JSON 파싱 실패 시 재시도**

현재 `_extract_json_from_response()`가 실패하면 빈 딕셔너리를 반환한다. LLM에 재요청하는 로직을 추가한다:

```python
async def _parse_natural_language(llm, user_query: str) -> dict:
    messages = [
        SystemMessage(content=INPUT_PARSER_SYSTEM_PROMPT),
        HumanMessage(content=user_query),
    ]

    for attempt in range(2):  # 최대 2회 시도
        response = await llm.ainvoke(messages)
        parsed = _extract_json_from_response(response.content)
        if parsed and parsed.get("query_targets"):
            break
        # 재시도 시 힌트 추가
        messages.append(HumanMessage(
            content="반드시 유효한 JSON만 출력하세요. query_targets는 필수입니다."
        ))

    parsed["original_query"] = user_query
    # 기본값 설정 (기존 코드와 동일)
    return parsed
```

### 2.3 Phase 2: 양식 파싱 구현

```python
# src/document/excel_parser.py
import io
from openpyxl import load_workbook


def parse_excel_template(file_data: bytes) -> dict:
    """Excel 양식 파일의 구조를 분석한다."""
    wb = load_workbook(io.BytesIO(file_data), data_only=False)
    sheets = []

    for ws in wb.worksheets:
        # 헤더 행 자동 탐지 (첫 번째 비어있지 않은 행)
        header_row = _find_header_row(ws)
        headers = _extract_headers(ws, header_row)
        data_start_row = header_row + 1

        # 병합 셀 정보
        merged = [str(m) for m in ws.merged_cells.ranges]

        sheets.append({
            "name": ws.title,
            "headers": headers,
            "header_row": header_row,
            "data_start_row": data_start_row,
            "merged_cells": merged,
        })

    return {
        "file_type": "xlsx",
        "sheets": sheets,
        "placeholders": [],
        "tables": [],
    }


def _find_header_row(ws, max_scan: int = 10) -> int:
    """헤더 행을 자동 탐지한다."""
    for row_idx in range(1, max_scan + 1):
        values = [cell.value for cell in ws[row_idx]]
        non_empty = [v for v in values if v is not None]
        if len(non_empty) >= 2:  # 2개 이상의 값이 있으면 헤더로 간주
            return row_idx
    return 1


def _extract_headers(ws, header_row: int) -> list[str]:
    """헤더 행에서 컬럼 헤더를 추출한다."""
    return [
        str(cell.value) if cell.value else ""
        for cell in ws[header_row]
        if cell.value is not None
    ]
```

```python
# src/document/word_parser.py
import io
import re
from docx import Document


def parse_word_template(file_data: bytes) -> dict:
    """Word 양식 파일의 구조를 분석한다."""
    doc = Document(io.BytesIO(file_data))

    # 플레이스홀더 추출
    placeholders = _extract_placeholders(doc)

    # 표 구조 추출
    tables = _extract_tables(doc)

    return {
        "file_type": "docx",
        "sheets": [],
        "placeholders": placeholders,
        "tables": tables,
    }


def _extract_placeholders(doc: Document) -> list[str]:
    """{{placeholder}} 패턴을 추출한다."""
    pattern = re.compile(r"\{\{([^}]+)\}\}")
    found = set()
    for para in doc.paragraphs:
        matches = pattern.findall(para.text)
        found.update(matches)
    return sorted(found)


def _extract_tables(doc: Document) -> list[dict]:
    """표 구조를 추출한다."""
    tables = []
    for idx, table in enumerate(doc.tables):
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        row_count = len(table.rows) - 1  # 헤더 제외
        tables.append({
            "index": idx,
            "headers": headers,
            "data_row_count": row_count,
        })
    return tables
```

---

## 3. schema_analyzer 노드

### 3.1 현재 구현 분석

- `SchemaCache` 클래스로 TTL 기반 캐시 (5분)
- `DOMAIN_TABLE_HINTS`로 도메인 키워드 -> 테이블명 매핑
- `_filter_relevant_tables()`에서 키워드 기반 필터링
- `_schema_to_dict()`로 State에 저장 가능한 형태로 변환

### 3.2 개선 사항

**a) LLM 기반 테이블 매칭 추가 (힌트 매핑 실패 시 폴백)**

키워드 매핑으로 관련 테이블을 찾지 못할 때, LLM에 테이블 목록을 보여주고 관련 테이블을 선택하게 한다:

```python
async def _llm_filter_tables(
    llm: BaseChatModel,
    all_tables: list[str],
    query_targets: list[str],
    user_query: str,
) -> list[str]:
    """LLM을 사용하여 관련 테이블을 필터링한다."""
    prompt = f"""다음 DB 테이블 목록 중에서 사용자 질의와 관련된 테이블만 선택하세요.

테이블 목록: {', '.join(all_tables)}
사용자 질의: {user_query}
조회 대상: {', '.join(query_targets)}

관련 테이블명을 쉼표로 구분하여 응답하세요.
"""
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    selected = [t.strip() for t in response.content.split(",")]
    return [t for t in selected if t in set(all_tables)]
```

**b) 캐시 무효화 API**

외부에서 스키마 캐시를 명시적으로 무효화할 수 있는 함수 노출:

```python
def invalidate_schema_cache() -> None:
    """스키마 캐시를 무효화한다. 스키마 변경 시 호출."""
    _schema_cache.invalidate()
```

---

## 4. query_generator 노드

### 4.1 현재 구현 분석

- `QUERY_GENERATOR_SYSTEM_TEMPLATE`에 스키마와 규칙을 포함
- `_build_user_prompt()`에서 요구사항 + 재시도 컨텍스트 조합
- `_extract_sql_from_response()`로 SQL 추출 (코드블록, SELECT 패턴)

### 4.2 개선 사항

**a) 프롬프트에 샘플 데이터 포함**

현재 `_format_schema_for_prompt()`에서 샘플 데이터를 2건만 표시한다. 복잡한 질의 시 LLM이 데이터 구조를 더 잘 이해할 수 있도록 실제 컬럼명/값 매핑 예시를 강화한다:

```python
def _format_schema_for_prompt(schema_info: dict) -> str:
    # 기존 코드 유지
    # 샘플 데이터 표시를 3건으로 증가하고, 테이블 관계를 더 명시적으로 표현
    for table_name, table_data in tables.items():
        # ...
        samples = table_data.get("sample_data", [])
        if samples:
            preview = json.dumps(samples[:3], ensure_ascii=False, indent=2)
            lines.append(f"  샘플 데이터 ({len(samples)}건):\n{preview}")
```

**b) 양식 기반 SQL 생성 강화 (Phase 2)**

양식 헤더를 DB 컬럼에 매핑하는 로직을 프롬프트에 추가:

```python
def _build_user_prompt(parsed_requirements, template_structure, ...):
    # 기존 코드에 추가
    if template_structure:
        headers = []
        for sheet in template_structure.get("sheets", []):
            headers.extend(sheet.get("headers", []))
        for placeholder in template_structure.get("placeholders", []):
            headers.append(placeholder)

        parts.append(
            f"## 양식 헤더/플레이스홀더\n"
            f"{', '.join(headers)}\n\n"
            f"위 항목들에 해당하는 DB 컬럼을 반드시 SELECT에 포함하세요.\n"
            f"양식 헤더명과 DB 컬럼명의 의미적 매핑을 수행하세요.\n"
            f"예: '서버명' -> servers.hostname, 'IP' -> servers.ip"
        )
```

---

## 5. query_validator 노드

### 5.1 현재 구현 분석

- `sqlparse`로 SQL 파싱 및 문 타입 판별
- `SQLGuard`로 금지 키워드, 인젝션 패턴 탐지
- 참조 테이블/컬럼 존재 검증
- LIMIT 절 자동 추가
- 성능 위험 패턴 탐지 (SELECT *, WHERE 없음, 카테시안 곱)

### 5.2 개선 사항

**a) 주석 내 금지 키워드 처리 개선**

현재 `SQLGuard.detect_forbidden_keywords()`는 주석 내용도 검사한다. query_generator가 SQL 주석(`-- 설명`)을 생성하도록 요구하므로, 주석 안의 키워드는 오탐이 발생할 수 있다.

```python
def detect_forbidden_keywords(self, sql: str, forbidden=None) -> list[str]:
    if forbidden is None:
        forbidden = FORBIDDEN_SQL_KEYWORDS

    # 주석 제거 후 검사
    sql_no_comments = sqlparse.format(sql, strip_comments=True)
    tokens = re.findall(r'\b([A-Z_]+)\b', sql_no_comments.upper())
    return [t for t in tokens if t in forbidden]
```

**b) 서브쿼리 내 테이블 참조 검증**

현재 `_extract_table_names()`는 FROM/JOIN 뒤의 단일 단어만 추출한다. 서브쿼리 내 테이블은 놓칠 수 있다. `sqlparse`의 토큰 분석을 활용한 개선:

```python
def _extract_table_names(sql: str) -> set[str]:
    """sqlparse를 활용한 정밀 테이블 추출."""
    tables = set()
    parsed = sqlparse.parse(sql)
    for statement in parsed:
        tables.update(_extract_tables_from_statement(statement))
    # information_schema 제외
    return {t for t in tables if not t.lower().startswith("information_schema")}

def _extract_tables_from_statement(statement) -> set[str]:
    """재귀적으로 FROM/JOIN 절의 테이블을 추출한다."""
    # 기존 정규식 방식과 sqlparse 토큰 분석을 병행
    tables = set()
    from_seen = False
    for token in statement.tokens:
        if token.ttype is sqlparse.tokens.Keyword and token.normalized in ("FROM", "JOIN"):
            from_seen = True
        elif from_seen:
            if hasattr(token, "get_name"):
                name = token.get_name()
                if name:
                    tables.add(name)
            from_seen = False
    return tables
```

---

## 6. query_executor 노드

### 6.1 현재 구현 분석

- `_get_client(config)`로 DB 클라이언트 선택 (direct/dbhub)
- 감사 로그 기록 (`log_query_execution`)
- 타임아웃/실행 에러/일반 에러 분기 처리

### 6.2 개선 사항

**a) query_attempts 이력 기록**

```python
async def query_executor(state: AgentState) -> dict:
    # ... 기존 코드 ...

    attempt = QueryAttempt(
        sql=sql,
        success=True,  # 또는 False
        error=None,     # 또는 에러 메시지
        row_count=result.row_count,
        execution_time_ms=elapsed_ms,
    )

    # 기존 이력에 추가
    existing_attempts = state.get("query_attempts", [])

    return {
        "query_results": result.rows,
        "error_message": None,
        "current_node": "query_executor",
        "query_attempts": existing_attempts + [attempt],
    }
```

**b) 민감 데이터 마스킹을 executor 레벨로 이동 (검토)**

현재 마스킹은 `result_organizer`에서 수행한다. 보안 관점에서 executor 레벨에서 먼저 마스킹하는 것이 더 안전할 수 있지만, 양식 매핑 시 원본 데이터가 필요할 수 있으므로 현재 구조를 유지한다.

---

## 7. result_organizer 노드

### 7.1 현재 구현 분석

- `DataMasker`로 민감 데이터 마스킹
- `_check_data_sufficiency()`로 데이터 충분성 판단
- `_format_numbers()`로 숫자 포맷팅 (단위 추론)
- `_generate_summary()`로 기본 요약 생성

### 7.2 개선 사항

**a) LLM 기반 요약 생성**

현재 요약은 "총 N건의 데이터를 조회했습니다"로 단순하다. `RESULT_ORGANIZER_SUMMARY_PROMPT`가 이미 정의되어 있으나 사용되지 않고 있다. 이를 활용한다:

```python
async def _generate_llm_summary(
    llm: BaseChatModel,
    results: list[dict],
    parsed: dict,
) -> str:
    """LLM을 사용하여 상세 요약을 생성한다."""
    from src.prompts.result_organizer import RESULT_ORGANIZER_SUMMARY_PROMPT

    preview = json.dumps(results[:5], ensure_ascii=False, indent=2)
    prompt = RESULT_ORGANIZER_SUMMARY_PROMPT.format(
        user_query=parsed.get("original_query", ""),
        query_targets=", ".join(parsed.get("query_targets", [])),
        row_count=len(results),
        preview_data=preview,
    )
    response = await llm.ainvoke([HumanMessage(content=prompt)])
    return response.content
```

**b) Phase 2: 양식 컬럼 매핑**

```python
def _map_columns_to_template(
    results: list[dict],
    template: dict,
) -> Optional[dict[str, str]]:
    """쿼리 결과 컬럼을 양식 헤더에 매핑한다."""
    if not template or not results:
        return None

    # 양식 헤더 수집
    headers = []
    for sheet in template.get("sheets", []):
        headers.extend(sheet.get("headers", []))
    for ph in template.get("placeholders", []):
        headers.append(ph.replace("{{", "").replace("}}", ""))

    # 결과 컬럼명
    result_columns = list(results[0].keys()) if results else []

    # 의미적 매핑 (단순 유사도 기반)
    mapping = {}
    for header in headers:
        best_match = _find_best_column_match(header, result_columns)
        if best_match:
            mapping[header] = best_match

    return mapping if mapping else None


def _find_best_column_match(header: str, columns: list[str]) -> Optional[str]:
    """헤더와 가장 유사한 컬럼을 찾는다."""
    header_lower = header.lower()
    # 정확한 매칭
    for col in columns:
        if col.lower() == header_lower:
            return col
    # 부분 매칭
    SYNONYMS = {
        "서버명": ["hostname", "server_name", "host"],
        "아이피": ["ip", "ip_address"],
        "CPU 코어": ["core_count", "cpu_cores"],
        "CPU 사용률": ["cpu_usage_pct", "usage_pct"],
        "메모리": ["total_gb", "memory_gb"],
        "메모리 사용률": ["memory_usage_pct", "mem_usage_pct"],
        "디스크": ["disk_total_gb", "total_gb"],
        "디스크 사용률": ["disk_usage_pct"],
    }
    for synonym_key, synonym_values in SYNONYMS.items():
        if header_lower in synonym_key.lower() or synonym_key.lower() in header_lower:
            for col in columns:
                if col.lower() in synonym_values:
                    return col
    return None
```

---

## 8. output_generator 노드

### 8.1 현재 구현 분석

- `output_format`에 따라 분기: `text`, `xlsx`(스텁), `docx`(스텁)
- `_generate_text_response()`에서 LLM으로 자연어 응답 생성
- `_generate_empty_result_response()`로 0건 결과 처리
- `OUTPUT_GENERATOR_SYSTEM_PROMPT`로 마크다운 표 형식 안내

### 8.2 Phase 2: Excel 파일 생성

```python
# src/document/excel_writer.py
import io
from openpyxl import load_workbook
from typing import Any


def fill_excel_template(
    template_bytes: bytes,
    rows: list[dict[str, Any]],
    column_mapping: dict[str, str],
    sheet_index: int = 0,
    data_start_row: int = 2,
) -> bytes:
    """Excel 양식에 데이터를 채워넣는다.

    Args:
        template_bytes: 원본 양식 바이너리
        rows: 채울 데이터 행
        column_mapping: 양식 헤더 -> 데이터 컬럼 매핑
        sheet_index: 대상 시트 인덱스
        data_start_row: 데이터 시작 행

    Returns:
        완성된 Excel 파일 바이너리
    """
    wb = load_workbook(io.BytesIO(template_bytes))
    ws = wb.worksheets[sheet_index]

    # 헤더 -> 열 인덱스 매핑
    header_col_map = {}
    for col_idx, cell in enumerate(ws[data_start_row - 1], start=1):
        if cell.value and str(cell.value) in column_mapping:
            header_col_map[column_mapping[str(cell.value)]] = col_idx

    # 데이터 채우기
    for row_idx, data_row in enumerate(rows, start=data_start_row):
        for data_key, col_idx in header_col_map.items():
            if data_key in data_row:
                ws.cell(row=row_idx, column=col_idx, value=data_row[data_key])

    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()
```

### 8.3 Phase 2: Word 파일 생성

```python
# src/document/word_writer.py
import io
import re
from docx import Document
from typing import Any


def fill_word_template(
    template_bytes: bytes,
    data: dict[str, Any],
    table_rows: list[dict[str, Any]] | None = None,
    column_mapping: dict[str, str] | None = None,
) -> bytes:
    """Word 양식에 데이터를 채워넣는다.

    Args:
        template_bytes: 원본 양식 바이너리
        data: 플레이스홀더 -> 값 매핑
        table_rows: 표에 채울 행 데이터
        column_mapping: 표 헤더 -> 데이터 컬럼 매핑

    Returns:
        완성된 Word 파일 바이너리
    """
    doc = Document(io.BytesIO(template_bytes))

    # 1. 플레이스홀더 치환
    for para in doc.paragraphs:
        for key, value in data.items():
            placeholder = f"{{{{{key}}}}}"
            if placeholder in para.text:
                para.text = para.text.replace(placeholder, str(value))

    # 2. 표 데이터 채우기
    if table_rows and column_mapping:
        for table in doc.tables:
            headers = [cell.text.strip() for cell in table.rows[0].cells]
            for row_data in table_rows:
                new_row = table.add_row()
                for col_idx, header in enumerate(headers):
                    data_key = column_mapping.get(header)
                    if data_key and data_key in row_data:
                        new_row.cells[col_idx].text = str(row_data[data_key])

    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()
```

### 8.4 output_generator 통합

```python
# output_generator 내 Phase 2 분기 구현
async def output_generator(state: AgentState) -> dict:
    output_format = state["parsed_requirements"].get("output_format", "text")

    if output_format == "text":
        response = await _generate_text_response(config, state)
        return {"final_response": response, "output_file": None, ...}

    elif output_format == "xlsx":
        from src.document.excel_writer import fill_excel_template
        file_bytes = fill_excel_template(
            template_bytes=state["uploaded_file"],
            rows=state["organized_data"]["rows"],
            column_mapping=state["organized_data"]["column_mapping"] or {},
            data_start_row=state["template_structure"]["sheets"][0]["data_start_row"],
        )
        text_summary = await _generate_text_response(config, state)
        return {
            "final_response": text_summary,
            "output_file": file_bytes,
            "output_file_name": "결과.xlsx",
            ...
        }

    elif output_format == "docx":
        from src.document.word_writer import fill_word_template
        # 유사한 로직
        ...
```

---

## 9. 에러 핸들링 전략 (전 노드 공통)

| 에러 유형 | 발생 노드 | 처리 |
|----------|----------|------|
| LLM API 호출 실패 | input_parser, query_generator, output_generator | exponential backoff (1s, 2s, 4s) 재시도 |
| JSON 파싱 실패 | input_parser, query_generator | 재시도 1회 + 기본값 폴백 |
| DB 연결 실패 | schema_analyzer, query_executor | error_message 설정, 사용자에게 안내 |
| 쿼리 타임아웃 | query_executor | error_message 설정, query_generator로 회귀 |
| SQL 검증 실패 | query_validator | error_message 설정, query_generator로 회귀 |
| 파일 파싱 실패 | input_parser (Phase 2) | 지원하지 않는 형식 안내 |
| 파일 생성 실패 | output_generator (Phase 2) | 텍스트 응답으로 폴백 |

### LLM 재시도 유틸리티

```python
# src/utils/retry.py
import asyncio
from typing import TypeVar, Callable, Awaitable

T = TypeVar("T")


async def retry_with_backoff(
    func: Callable[..., Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    **kwargs,
) -> T:
    """exponential backoff로 비동기 함수를 재시도한다."""
    for attempt in range(max_retries):
        try:
            return await func(**kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning(f"재시도 {attempt + 1}/{max_retries}, {delay}초 후: {e}")
            await asyncio.sleep(delay)
```

---

## 10. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| input_parser | JSON 파싱 실패 시 빈 dict | 재시도 1회 + 기본값 폴백 |
| schema_analyzer | 키워드 매핑만 | LLM 기반 테이블 매칭 폴백 추가 |
| query_validator | 주석 포함하여 금지 키워드 검사 | `sqlparse.format(strip_comments=True)` 적용 |
| query_executor | 이력 미기록 | `query_attempts` 필드에 시도 결과 기록 |
| result_organizer | 단순 요약 | LLM 요약 + 양식 컬럼 매핑 (Phase 2) |
| output_generator | Excel/Word 스텁 | Phase 2에서 실제 파일 생성 구현 |
| 에러 핸들링 | 노드별 개별 처리 | `retry_with_backoff` 공통 유틸리티 도입 |
| document 모듈 | 미존재 | `src/document/` 4개 파일 신규 생성 (Phase 2) |
