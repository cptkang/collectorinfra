# 02. AgentState 상세 스키마 및 노드 간 데이터 흐름

> 기존 `src/state.py` 분석 및 보완 계획

---

## 1. 현재 AgentState 분석

기존 `src/state.py`에 정의된 `AgentState`:

```python
class AgentState(TypedDict):
    # 사용자 입력
    user_query: str
    uploaded_file: Optional[bytes]
    file_type: Optional[str]
    # 파싱 결과
    parsed_requirements: dict
    template_structure: Optional[dict]
    # DB 관련
    relevant_tables: list[str]
    schema_info: dict
    generated_sql: str
    validation_result: ValidationResult
    query_results: list[dict[str, Any]]
    # 가공 결과
    organized_data: OrganizedData
    # 제어
    retry_count: int
    error_message: Optional[str]
    current_node: str
    # 출력
    final_response: str
    output_file: Optional[bytes]
    output_file_name: Optional[str]
```

**이미 구현된 보조 TypedDict:**
- `ValidationResult`: `passed`, `reason`, `auto_fixed_sql`
- `OrganizedData`: `summary`, `rows`, `column_mapping`, `is_sufficient`

---

## 2. 개선이 필요한 항목

### 2.1 멀티턴 대화 지원을 위한 messages 필드 (Phase 3)

현재 State에 이전 대화 히스토리를 추적하는 필드가 없다. Phase 3에서 멀티턴을 구현하려면 `messages` 필드가 필요하다.

### 2.2 다중 쿼리 지원

`result_organizer`에서 데이터 부족 시 추가 쿼리를 생성하지만, 이전 쿼리의 결과를 누적할 수 없다. `generated_sql`이 단일 문자열이므로 이전 SQL이 덮어써진다.

### 2.3 실행 이력 추적

어떤 SQL이 몇 번 실행되었는지, 각 시도의 에러가 무엇이었는지 추적이 불가능하다.

### 2.4 Human-in-the-loop 지원 (Phase 3)

사용자 승인 대기 상태를 표현할 수 없다.

---

## 3. 보강된 AgentState 설계

```python
# src/state.py (보강 버전)

from __future__ import annotations
from typing import Any, Optional, TypedDict


class ValidationResult(TypedDict):
    """SQL 검증 결과."""
    passed: bool
    reason: str
    auto_fixed_sql: Optional[str]


class OrganizedData(TypedDict):
    """정리된 결과 데이터."""
    summary: str
    rows: list[dict[str, Any]]
    column_mapping: Optional[dict[str, str]]  # 양식 헤더 -> DB 컬럼 매핑
    is_sufficient: bool


class QueryAttempt(TypedDict):
    """개별 SQL 실행 시도 기록."""
    sql: str
    success: bool
    error: Optional[str]
    row_count: int
    execution_time_ms: float


class AgentState(TypedDict):
    """LangGraph 에이전트의 전역 상태."""

    # === 사용자 입력 ===
    user_query: str                          # 자연어 질의
    uploaded_file: Optional[bytes]           # 업로드된 양식 파일 바이너리
    file_type: Optional[str]                 # "xlsx" | "docx" | None

    # === 파싱 결과 ===
    parsed_requirements: dict                # 구조화된 요구사항
    template_structure: Optional[dict]       # 양식 구조 정보

    # === DB 관련 ===
    relevant_tables: list[str]               # 관련 테이블 목록
    schema_info: dict                        # 스키마 상세 (테이블, 컬럼, FK)
    generated_sql: str                       # 현재 SQL 쿼리
    validation_result: ValidationResult      # 검증 결과
    query_results: list[dict[str, Any]]      # 현재 쿼리 실행 결과

    # === 가공 결과 ===
    organized_data: OrganizedData            # 정리된 데이터

    # === 제어 ===
    retry_count: int                         # 재시도 횟수 (최대 3)
    error_message: Optional[str]             # 에러 메시지 (재시도 시 참조)
    current_node: str                        # 현재 실행 중인 노드

    # === [신규] 실행 이력 ===
    query_attempts: list[QueryAttempt]       # SQL 시도 이력 (디버깅/감사용)

    # === [Phase 3] 멀티턴 대화 ===
    # messages: list[BaseMessage]            # 대화 히스토리

    # === [Phase 3] Human-in-the-loop ===
    # awaiting_approval: bool                # 사용자 승인 대기 여부
    # approval_context: Optional[dict]       # 승인 요청 컨텍스트

    # === 출력 ===
    final_response: str                      # 자연어 응답
    output_file: Optional[bytes]             # 생성된 파일 바이너리
    output_file_name: Optional[str]          # 출력 파일명
```

---

## 4. 필드별 상세 명세

### 4.1 사용자 입력 필드

| 필드 | 타입 | 작성 노드 | 초기값 | 설명 |
|------|------|----------|--------|------|
| `user_query` | `str` | (외부) | 사용자 입력 | 자연어 질의 원문 |
| `uploaded_file` | `Optional[bytes]` | (외부) | `None` | 양식 파일 바이너리 |
| `file_type` | `Optional[str]` | (외부) | `None` | `"xlsx"`, `"docx"`, `None` |

### 4.2 파싱 결과 필드

| 필드 | 타입 | 작성 노드 | 설명 |
|------|------|----------|------|
| `parsed_requirements` | `dict` | `input_parser` | 구조화된 요구사항 (아래 상세) |
| `template_structure` | `Optional[dict]` | `input_parser` | 양식 구조 (Phase 2) |

**parsed_requirements 내부 구조:**

```python
{
    "original_query": str,           # 원본 질의 보존
    "query_targets": list[str],      # ["서버", "CPU", "메모리", ...]
    "filter_conditions": list[dict], # [{"field": "usage_pct", "op": ">=", "value": 80}]
    "time_range": Optional[dict],    # {"start": "ISO8601", "end": "ISO8601"}
    "output_format": str,            # "text" | "xlsx" | "docx"
    "aggregation": Optional[str],    # "top_n" | "group_by" | "time_series" | None
    "limit": Optional[int],          # 결과 제한 수
}
```

**template_structure 내부 구조 (Phase 2):**

```python
{
    "file_type": "xlsx" | "docx",
    "sheets": [                       # Excel인 경우
        {
            "name": str,
            "headers": list[str],
            "header_row": int,
            "data_start_row": int,
            "merged_cells": list,
        }
    ],
    "placeholders": list[str],        # Word: ["{{서버명}}", "{{IP}}"]
    "tables": list[dict],             # Word 표 구조
}
```

### 4.3 DB 관련 필드

| 필드 | 타입 | 작성 노드 | 설명 |
|------|------|----------|------|
| `relevant_tables` | `list[str]` | `schema_analyzer` | 관련 테이블 이름 목록 |
| `schema_info` | `dict` | `schema_analyzer` | 스키마 상세 (아래 상세) |
| `generated_sql` | `str` | `query_generator` | 현재 SQL 쿼리 문자열 |
| `validation_result` | `ValidationResult` | `query_validator` | 검증 결과 |
| `query_results` | `list[dict]` | `query_executor` | 실행 결과 행 목록 |

**schema_info 내부 구조:**

```python
{
    "tables": {
        "servers": {
            "columns": [
                {
                    "name": "id",
                    "type": "integer",
                    "nullable": False,
                    "primary_key": True,
                    "foreign_key": False,
                    "references": None,
                }
            ],
            "row_count_estimate": int | None,
            "sample_data": list[dict],
        }
    },
    "relationships": [
        {"from": "cpu_metrics.server_id", "to": "servers.id"}
    ]
}
```

### 4.4 제어 필드

| 필드 | 타입 | 작성 노드 | 초기값 | 설명 |
|------|------|----------|--------|------|
| `retry_count` | `int` | `query_generator` | `0` | 누적 재시도 횟수 |
| `error_message` | `Optional[str]` | 여러 노드 | `None` | 직전 에러 메시지. 재시도 시 참조 후 초기화 |
| `current_node` | `str` | 모든 노드 | `""` | 현재 실행 중인 노드명 |
| `query_attempts` | `list[QueryAttempt]` | `query_executor` | `[]` | [신규] 실행 이력 |

### 4.5 출력 필드

| 필드 | 타입 | 작성 노드 | 설명 |
|------|------|----------|------|
| `final_response` | `str` | `output_generator` / `error_response` | 최종 자연어 응답 |
| `output_file` | `Optional[bytes]` | `output_generator` | 생성된 파일 바이너리 (Phase 2) |
| `output_file_name` | `Optional[str]` | `output_generator` | 파일명 (예: "인프라_현황.xlsx") |

---

## 5. 노드 간 데이터 흐름 다이어그램

```
[외부 입력]
  쓰기: user_query, uploaded_file, file_type
       │
       ▼
[input_parser]
  읽기: user_query, uploaded_file, file_type
  쓰기: parsed_requirements, template_structure, current_node
       │
       ▼
[schema_analyzer]
  읽기: parsed_requirements
  쓰기: relevant_tables, schema_info, current_node, error_message
       │
       ▼
[query_generator]
  읽기: parsed_requirements, schema_info, error_message, retry_count,
        generated_sql (재시도 시), template_structure (양식 시)
  쓰기: generated_sql, retry_count, error_message(=None), current_node
       │
       ▼
[query_validator]
  읽기: generated_sql, schema_info
  쓰기: validation_result, generated_sql (자동 보정 시),
        error_message (실패 시), current_node
       │
       ├─ 통과 ─────────────────┐
       │                         ▼
       ├─ 실패 (retry < 3) → [query_generator] (회귀)
       │
       └─ 실패 (retry >= 3) → [error_response]
                                 │
[query_executor]                 │
  읽기: generated_sql            │
  쓰기: query_results, error_message, current_node,
        query_attempts (이력 추가)
       │
       ├─ 성공 ─────────────────┐
       │                         ▼
       ├─ 에러 (retry < 3) → [query_generator] (회귀)
       │
       └─ 에러 (retry >= 3) → [error_response]
                                 │
[result_organizer]               │
  읽기: query_results, parsed_requirements, template_structure,
        retry_count
  쓰기: organized_data, error_message, current_node
       │
       ├─ 데이터 충분 ──────────┐
       │                         ▼
       └─ 데이터 부족 → [query_generator] (회귀, retry < 3일 때)
                                 │
[output_generator]               │
  읽기: organized_data, parsed_requirements, template_structure,
        uploaded_file, generated_sql
  쓰기: final_response, output_file, output_file_name, current_node
       │
       ▼
[END]

[error_response] (최대 재시도 초과 시)
  읽기: error_message, retry_count
  쓰기: final_response, current_node
       │
       ▼
[END]
```

---

## 6. create_initial_state 함수 개선

```python
def create_initial_state(
    user_query: str,
    uploaded_file: Optional[bytes] = None,
    file_type: Optional[str] = None,
) -> AgentState:
    """초기 State를 생성한다."""
    return AgentState(
        user_query=user_query,
        uploaded_file=uploaded_file,
        file_type=file_type,
        parsed_requirements={},
        template_structure=None,
        relevant_tables=[],
        schema_info={},
        generated_sql="",
        validation_result={"passed": False, "reason": "", "auto_fixed_sql": None},
        query_results=[],
        organized_data={
            "summary": "",
            "rows": [],
            "column_mapping": None,
            "is_sufficient": False,
        },
        retry_count=0,
        error_message=None,
        current_node="",
        query_attempts=[],          # [신규]
        final_response="",
        output_file=None,
        output_file_name=None,
    )
```

---

## 7. 기존 코드 대비 변경 사항 요약

| 항목 | 현재 | 변경 |
|------|------|------|
| `QueryAttempt` TypedDict | 미정의 | 신규 추가 (실행 이력 추적용) |
| `query_attempts` 필드 | 미존재 | AgentState에 추가 |
| `messages` 필드 | 미존재 | Phase 3에서 추가 (현재는 주석) |
| `awaiting_approval` 필드 | 미존재 | Phase 3에서 추가 (현재는 주석) |
| `create_initial_state` | `query_attempts` 미포함 | `query_attempts=[]` 추가 |
| `query_executor` 노드 | 이력 미기록 | `query_attempts`에 시도 결과 append |

**기존 코드에 영향을 주는 변경은 최소화되었다.** `query_attempts` 필드 추가는 하위 호환성을 유지하며, 기존 노드들이 이 필드를 무시해도 문제없다. `query_executor`만 수정하여 이력을 기록하면 된다.
