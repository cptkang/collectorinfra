# 인프라 데이터 조회 에이전트 - 구현 요구사항 문서

> 작성일: 2026-03-12
> 원본: `/Users/cptkang/AIOps/collectorinfra/spec.md`
> 목적: 구현팀이 바로 작업할 수 있는 구조화된 요구사항 정의

---

## 1. 시스템 개요

사용자가 자연어로 인프라 정보를 질의하면, LLM이 DB 스키마를 분석하여 SQL을 자동 생성/실행하고, 결과를 자연어 응답 또는 문서(Excel/Word)로 제공하는 에이전트 시스템.

**기술 스택:**
- 에이전트 프레임워크: LangGraph (>= 0.2.0)
- LLM 추상화: langchain-core (>= 0.3.0)
- DB 연결: DBHub (MCP 서버, 읽기 전용)
- 문서 처리: openpyxl (>= 3.1.0), python-docx (>= 1.0.0)
- API 서버: FastAPI (>= 0.110.0) + uvicorn (>= 0.30.0)
- 데이터 검증: pydantic (>= 2.0)

---

## 2. 모듈 분해 및 구현 단위

### 2.1 M-01: LangGraph 그래프 코어 (Graph Core)

| 항목 | 내용 |
|------|------|
| **목적** | LangGraph 기반 상태 머신의 뼈대 구성. 노드 등록, 엣지 정의, 조건부 라우팅, State 관리 |
| **책임** | 그래프 빌드, 노드 간 제어 흐름 관리, 체크포인트 연동, 재시도 로직 (최대 3회) |
| **입력** | 사용자 요청 (자연어 텍스트, 선택적 파일 업로드) |
| **출력** | 최종 응답 (자연어 텍스트 또는 생성된 파일) |
| **의존성** | `langgraph`, `langchain-core`, 모든 하위 노드 모듈 |
| **우선순위** | **Phase 1** |

**수용 기준:**
- [ ] AgentState TypedDict가 spec에 정의된 모든 필드를 포함한다
- [ ] 그래프가 `START -> input_parser -> schema_analyzer -> query_generator -> query_validator -> [조건부] -> query_executor -> result_organizer -> output_generator -> END` 흐름을 정확히 구현한다
- [ ] query_validator 검증 실패 시 query_generator로 회귀하며, retry_count가 3 이상이면 사용자에게 에러를 반환한다
- [ ] query_executor 에러 발생 시 에러 메시지를 포함하여 query_generator로 회귀한다
- [ ] result_organizer에서 데이터 부족 판단 시 query_generator로 회귀하여 추가 쿼리를 생성한다
- [ ] 체크포인트 저장소(SQLite/Postgres)와 연동하여 상태를 저장/복구할 수 있다

---

### 2.2 M-02: 입력 파서 노드 (input_parser)

| 항목 | 내용 |
|------|------|
| **목적** | 사용자 입력(자연어/파일)을 분석하여 구조화된 요구사항을 추출 |
| **책임** | 자연어 의도 파악, 파일 타입 판별, 양식 구조 분석 (Excel 시트/헤더, Word 테이블/플레이스홀더) |
| **입력** | `user_query: str`, `uploaded_file: Optional[bytes]`, `file_type: Optional[str]` |
| **출력** | `parsed_requirements: dict`, `template_structure: Optional[dict]` |
| **의존성** | LLM (langchain-anthropic 또는 langchain-openai), `openpyxl`, `python-docx` |
| **우선순위** | **Phase 1** (자연어 파싱), **Phase 2** (양식 파싱) |

**parsed_requirements 구조:**
```python
{
    "query_targets": list[str],       # 조회 대상: ["서버", "CPU", "메모리", "디스크", "네트워크"]
    "filter_conditions": list[dict],  # 필터: [{"field": "usage_pct", "op": ">=", "value": 80}]
    "time_range": Optional[dict],     # 기간: {"start": "...", "end": "..."}
    "output_format": str,             # "text" | "xlsx" | "docx"
    "aggregation": Optional[str],     # "top_n", "group_by", "time_series" 등
    "limit": Optional[int]            # 결과 제한
}
```

**template_structure 구조 (양식 파일 존재 시):**
```python
{
    "file_type": "xlsx" | "docx",
    "sheets": [                         # Excel인 경우
        {
            "name": str,
            "headers": list[str],       # 헤더 목록
            "header_row": int,          # 헤더 행 번호
            "data_start_row": int,      # 데이터 시작 행
            "merged_cells": list,       # 병합 셀 정보
        }
    ],
    "placeholders": list[str],          # Word인 경우: ["{{서버명}}", "{{IP}}"]
    "tables": list[dict],               # Word 표 구조
}
```

**수용 기준:**
- [ ] 한국어 자연어 질의에서 조회 대상, 필터 조건, 기간, 출력 형식을 정확히 추출한다
- [ ] spec 섹션 11의 5가지 질의 예시를 모두 올바르게 파싱한다
- [ ] Excel 파일 업로드 시 시트별 헤더, 데이터 영역, 병합 셀 정보를 정확히 추출한다
- [ ] Word 파일 업로드 시 `{{placeholder}}` 패턴과 표(Table) 구조를 정확히 추출한다
- [ ] 지원하지 않는 파일 형식 업로드 시 명확한 에러 메시지를 반환한다

---

### 2.3 M-03: 스키마 분석 노드 (schema_analyzer)

| 항목 | 내용 |
|------|------|
| **목적** | DBHub를 통해 DB 스키마를 조회하고, 사용자 요구사항에 관련된 테이블/컬럼을 식별 |
| **책임** | DBHub `search_objects` 호출, 테이블/컬럼 메타데이터 수집, 관련 테이블 필터링, 샘플 데이터 조회 |
| **입력** | `parsed_requirements: dict` |
| **출력** | `relevant_tables: list[str]`, `schema_info: dict` |
| **의존성** | DBHub MCP 클라이언트, LLM |
| **우선순위** | **Phase 1** |

**schema_info 구조:**
```python
{
    "tables": {
        "servers": {
            "columns": [
                {"name": "id", "type": "integer", "nullable": False, "primary_key": True},
                {"name": "hostname", "type": "varchar(255)", "nullable": False},
                # ...
            ],
            "row_count_estimate": int,
            "sample_data": list[dict]    # 상위 5건 샘플
        },
        # ...
    },
    "relationships": [                    # FK 관계
        {"from": "cpu_metrics.server_id", "to": "servers.id"}
    ]
}
```

**수용 기준:**
- [ ] DBHub의 `search_objects` API를 호출하여 전체 테이블 목록을 조회할 수 있다
- [ ] 사용자 요구사항에 따라 관련 테이블만 필터링한다 (예: CPU 질의 시 servers + cpu_metrics)
- [ ] 각 테이블의 컬럼명, 타입, nullable, PK/FK 정보를 정확히 수집한다
- [ ] 테이블 간 FK 관계를 파악하여 JOIN 가능 여부를 판단한다
- [ ] DBHub 연결 실패 시 적절한 에러 메시지를 State에 기록한다

---

### 2.4 M-04: SQL 생성 노드 (query_generator)

| 항목 | 내용 |
|------|------|
| **목적** | LLM을 이용하여 사용자 요구사항과 스키마 정보를 기반으로 SQL 쿼리를 생성 |
| **책임** | SELECT 문 생성, JOIN/GROUP BY/집계 함수 활용, LIMIT 포함, 재시도 시 에러 메시지 반영 |
| **입력** | `parsed_requirements: dict`, `schema_info: dict`, `error_message: Optional[str]` (재시도 시) |
| **출력** | `generated_sql: str` |
| **의존성** | LLM (langchain-anthropic 또는 langchain-openai) |
| **우선순위** | **Phase 1** |

**프롬프트 제약 조건 (필수 반영):**
1. SELECT 문만 생성 (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE 등 금지)
2. 테이블/컬럼명은 반드시 `schema_info`에 존재하는 것만 사용
3. 대량 조회 시 LIMIT 절 포함 (기본 LIMIT 1000)
4. 쿼리에 설명 주석 포함
5. 재시도 시 이전 에러 메시지를 컨텍스트에 포함

**수용 기준:**
- [ ] 생성되는 SQL이 항상 SELECT 문이다
- [ ] schema_info에 없는 테이블/컬럼을 참조하지 않는다
- [ ] spec 섹션 11의 5가지 질의 예시에 대해 유효한 SQL을 생성한다
- [ ] 에러로 인한 재시도 시, 이전 에러 내용을 반영하여 수정된 SQL을 생성한다
- [ ] 양식 기반 요청 시 양식 헤더/플레이스홀더에 매핑되는 컬럼을 SELECT한다

---

### 2.5 M-05: SQL 검증 노드 (query_validator)

| 항목 | 내용 |
|------|------|
| **목적** | 생성된 SQL의 문법, 안전성, 성능을 사전 검증 |
| **책임** | SQL 파싱, DML/DDL 차단, 참조 객체 존재 확인, LIMIT 검사, 성능 위험 탐지 |
| **입력** | `generated_sql: str`, `schema_info: dict` |
| **출력** | 검증 결과 (통과: 다음 노드로 진행 / 실패: error_message와 함께 query_generator로 회귀) |
| **의존성** | `sqlparse` 또는 동등한 SQL 파서 (추가 라이브러리), `schema_info` |
| **우선순위** | **Phase 1** |

**검증 체크리스트:**
1. SQL 문법이 유효한가 (파싱 가능한가)
2. SELECT 문인가 (DML/DDL 완전 차단)
3. 참조하는 모든 테이블이 `schema_info.tables`에 존재하는가
4. 참조하는 모든 컬럼이 해당 테이블의 컬럼 목록에 존재하는가
5. LIMIT 절이 포함되어 있는가 (없으면 자동 추가 또는 경고)
6. 위험 패턴 탐지: `SELECT *` + 대형 테이블, 카테시안 곱 가능성, 전체 테이블 스캔
7. **금지 키워드 차단**: INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE, EXEC, EXECUTE

**수용 기준:**
- [ ] SELECT 이외의 SQL 문(INSERT, UPDATE, DELETE, DROP 등)이 입력되면 100% 차단한다
- [ ] 존재하지 않는 테이블/컬럼 참조를 탐지하여 거부한다
- [ ] LIMIT 절이 없는 쿼리에 대해 경고 또는 자동 보정한다
- [ ] SQL 인젝션 패턴(UNION 주입, 주석 처리를 통한 우회 등)을 탐지한다
- [ ] 검증 실패 시 구체적인 실패 사유를 error_message에 기록한다

---

### 2.6 M-06: 쿼리 실행 노드 (query_executor)

| 항목 | 내용 |
|------|------|
| **목적** | 검증 통과된 SQL을 DBHub를 통해 실행하고 결과를 수집 |
| **책임** | DBHub `execute_sql` 호출, 결과 데이터 수집, 타임아웃/에러 처리 |
| **입력** | `generated_sql: str` (검증 통과된 SQL) |
| **출력** | `query_results: list[dict]` |
| **의존성** | DBHub MCP 클라이언트 |
| **우선순위** | **Phase 1** |

**수용 기준:**
- [ ] DBHub의 `execute_sql` API를 호출하여 SQL을 실행하고 결과를 dict 리스트로 반환한다
- [ ] 쿼리 타임아웃(30초) 초과 시 적절한 에러 메시지를 생성한다
- [ ] max_rows(10,000건) 제한이 적용된다
- [ ] DB 연결 실패 시 사용자에게 연결 실패를 알리는 에러 메시지를 생성한다
- [ ] 실행 에러 발생 시 에러 메시지를 State에 기록하고 query_generator로 회귀한다
- [ ] 결과가 빈 경우(0건) 정상 처리하며, result_organizer에서 "해당 데이터 없음" 안내를 생성한다

---

### 2.7 M-07: 결과 정리 노드 (result_organizer)

| 항목 | 내용 |
|------|------|
| **목적** | 쿼리 결과를 사용자 요구에 맞는 구조로 정리/가공 |
| **책임** | 데이터 포맷팅, 집계/정렬, 데이터 충분성 판단, 양식 매핑 |
| **입력** | `query_results: list[dict]`, `parsed_requirements: dict`, `template_structure: Optional[dict]` |
| **출력** | 정리된 데이터 (자연어 응답용 구조 또는 양식 채우기용 구조) |
| **의존성** | LLM (데이터 해석/정리 시) |
| **우선순위** | **Phase 1** (기본 정리), **Phase 2** (양식 매핑) |

**수용 기준:**
- [ ] 쿼리 결과를 사용자가 요청한 형식(텍스트/표)에 맞게 정리한다
- [ ] 결과가 0건인 경우 "해당 데이터 없음" 또는 조건 완화를 제안한다
- [ ] 데이터 부족으로 판단되면 query_generator로 회귀하여 추가 쿼리를 요청한다
- [ ] 양식 기반 요청 시, 양식 헤더/플레이스홀더와 쿼리 결과 컬럼을 매핑한다
- [ ] 숫자 데이터에 적절한 단위(GB, %, Mbps 등)를 부여한다

---

### 2.8 M-08: 출력 생성 노드 (output_generator)

| 항목 | 내용 |
|------|------|
| **목적** | 최종 사용자 응답 생성 (자연어 텍스트 또는 Excel/Word 파일) |
| **책임** | 자연어 응답 작성, Excel 파일 생성/채우기, Word 파일 생성/채우기 |
| **입력** | 정리된 데이터, `template_structure: Optional[dict]`, `uploaded_file: Optional[bytes]` |
| **출력** | `final_response: str`, `output_file: Optional[bytes]` |
| **의존성** | LLM, `openpyxl`, `python-docx` |
| **우선순위** | **Phase 1** (자연어 응답), **Phase 2** (파일 생성) |

**수용 기준:**
- [ ] 자연어 질의에 대해 한국어로 읽기 쉬운 응답을 생성한다
- [ ] Excel 양식 파일에 데이터를 정확한 셀 위치에 채워넣는다
- [ ] Excel 원본의 병합 셀, 서식, 수식을 보존한다
- [ ] Word 양식의 `{{placeholder}}`를 실제 데이터로 치환한다
- [ ] Word 표의 데이터 행에 쿼리 결과를 채워넣는다
- [ ] Word 원본의 스타일 및 서식을 보존한다
- [ ] 생성된 파일이 정상적으로 열리는지 검증 가능하다

---

### 2.9 M-09: DBHub 연동 레이어 (DBHub Client)

| 항목 | 내용 |
|------|------|
| **목적** | DBHub MCP 서버와의 통신을 추상화하는 클라이언트 레이어 |
| **책임** | DBHub 연결 관리, `search_objects`/`execute_sql` 호출, 연결 풀링, 헬스체크 |
| **입력** | SQL 쿼리 또는 스키마 검색 요청 |
| **출력** | 쿼리 결과 또는 스키마 메타데이터 |
| **의존성** | `dbhub`, DBHub TOML 설정 파일 |
| **우선순위** | **Phase 1** |

**TOML 설정 요구사항:**
```toml
[[sources]]
name = "infra_db"
type = "postgresql"          # mysql, mariadb 등도 지원
connection = "postgresql://user:password@host:5432/infra_db"
readonly = true              # 필수: 읽기 전용
query_timeout = 30           # 쿼리 타임아웃 (초)

[[tools]]
name = "execute_sql"
sources = ["infra_db"]
readonly = true              # 필수: 읽기 전용
max_rows = 10000
```

**수용 기준:**
- [ ] DBHub TOML 설정에서 `readonly = true`가 반드시 설정된다
- [ ] `search_objects`를 통해 테이블 목록, 컬럼 정보를 조회할 수 있다
- [ ] `execute_sql`을 통해 SELECT 쿼리를 실행하고 결과를 반환할 수 있다
- [ ] 쿼리 타임아웃(30초)이 적용된다
- [ ] max_rows(10,000건) 제한이 적용된다
- [ ] 연결 실패 시 재시도 및 명확한 에러 메시지를 제공한다
- [ ] SSH 터널링을 통한 원격 DB 접속을 지원한다 (설정 기반)

---

### 2.10 M-10: FastAPI 서버 (API Layer)

| 항목 | 내용 |
|------|------|
| **목적** | 사용자 인터페이스 제공 (Web UI, API 클라이언트 등) |
| **책임** | REST API 엔드포인트 제공, 파일 업로드/다운로드, 세션 관리, 인증/인가 |
| **입력** | HTTP 요청 (자연어 질의, 파일 업로드) |
| **출력** | HTTP 응답 (자연어 텍스트, 파일 다운로드) |
| **의존성** | `fastapi`, `uvicorn`, LangGraph 에이전트 |
| **우선순위** | **Phase 1** (기본 엔드포인트), **Phase 3** (인증, 세션 관리) |

**API 엔드포인트 설계:**

| 메서드 | 경로 | 설명 | Phase |
|--------|------|------|-------|
| POST | `/api/v1/query` | 자연어 질의 처리 | Phase 1 |
| POST | `/api/v1/query/file` | 양식 파일 업로드 + 질의 | Phase 2 |
| GET | `/api/v1/query/{query_id}/result` | 비동기 결과 조회 | Phase 1 |
| GET | `/api/v1/query/{query_id}/download` | 생성된 파일 다운로드 | Phase 2 |
| GET | `/api/v1/health` | 헬스체크 | Phase 1 |
| GET | `/api/v1/history` | 쿼리 히스토리 조회 | Phase 3 |

**수용 기준:**
- [ ] `/api/v1/query`에 자연어 텍스트를 POST하면 응답을 반환한다
- [ ] `/api/v1/query/file`에 양식 파일과 함께 질의를 POST하면 채워진 파일을 반환한다
- [ ] 스레드 기반 세션 분리로 다중 사용자(최소 10명)를 동시 지원한다
- [ ] 요청/응답에 적절한 HTTP 상태 코드를 반환한다
- [ ] API 문서가 Swagger/OpenAPI로 자동 생성된다

---

## 3. LangGraph State 스키마 정의

### 3.1 AgentState 전체 정의

```python
from typing import TypedDict, Optional

class AgentState(TypedDict):
    # --- 사용자 입력 ---
    user_query: str                          # 자연어 질의
    uploaded_file: Optional[bytes]           # 업로드된 양식 파일 바이너리
    file_type: Optional[str]                 # "xlsx" | "docx" | None

    # --- 파싱 결과 ---
    parsed_requirements: dict                # 구조화된 요구사항
    template_structure: Optional[dict]       # 양식 구조 정보

    # --- DB 관련 ---
    relevant_tables: list[str]               # 관련 테이블 목록
    schema_info: dict                        # 스키마 상세 (테이블, 컬럼, FK)
    generated_sql: str                       # 생성된 SQL 쿼리
    validation_result: dict                  # 검증 결과 {"passed": bool, "reason": str}
    query_results: list[dict]                # 쿼리 실행 결과

    # --- 가공 결과 ---
    organized_data: dict                     # 정리된 데이터

    # --- 제어 ---
    retry_count: int                         # 재시도 횟수 (최대 3)
    error_message: Optional[str]             # 에러 메시지 (재시도 시 참조)
    current_node: str                        # 현재 실행 중인 노드

    # --- 출력 ---
    final_response: str                      # 자연어 응답
    output_file: Optional[bytes]             # 생성된 파일 바이너리
    output_file_name: Optional[str]          # 출력 파일명
```

### 3.2 노드 간 데이터 흐름

```
[input_parser]
  읽기: user_query, uploaded_file, file_type
  쓰기: parsed_requirements, template_structure
       ↓
[schema_analyzer]
  읽기: parsed_requirements
  쓰기: relevant_tables, schema_info
       ↓
[query_generator]
  읽기: parsed_requirements, schema_info, error_message (재시도 시), retry_count
  쓰기: generated_sql, retry_count (증가)
       ↓
[query_validator]
  읽기: generated_sql, schema_info
  쓰기: validation_result, error_message (실패 시)
       ↓ (조건부: validation_result.passed == True)
[query_executor]
  읽기: generated_sql
  쓰기: query_results, error_message (에러 시)
       ↓
[result_organizer]
  읽기: query_results, parsed_requirements, template_structure
  쓰기: organized_data, error_message (데이터 부족 시)
       ↓
[output_generator]
  읽기: organized_data, template_structure, uploaded_file, parsed_requirements
  쓰기: final_response, output_file, output_file_name
```

### 3.3 조건부 엣지 로직

```python
def route_after_validation(state: AgentState) -> str:
    """query_validator 이후 라우팅"""
    if state["validation_result"]["passed"]:
        return "query_executor"
    elif state["retry_count"] >= 3:
        return "error_response"    # 최대 재시도 초과 -> 에러 응답
    else:
        return "query_generator"   # 재생성 요청

def route_after_execution(state: AgentState) -> str:
    """query_executor 이후 라우팅"""
    if state.get("error_message"):
        if state["retry_count"] >= 3:
            return "error_response"
        return "query_generator"   # 에러 메시지 포함하여 재생성
    return "result_organizer"

def route_after_organization(state: AgentState) -> str:
    """result_organizer 이후 라우팅"""
    if state.get("error_message") == "data_insufficient":
        if state["retry_count"] >= 3:
            return "output_generator"  # 있는 데이터로 응답 생성
        return "query_generator"       # 추가 쿼리 생성
    return "output_generator"
```

---

## 4. Phase별 구현 범위

### Phase 1: 기본 자연어 -> SQL 조회 파이프라인

**범위:** 텍스트 입력 -> SQL 생성/검증/실행 -> 텍스트 응답

| 구현 대상 | 세부 항목 |
|----------|----------|
| M-01 Graph Core | 전체 그래프 구조, State 정의, 엣지/조건부 라우팅 |
| M-02 input_parser | 자연어 파싱만 (파일 파싱은 Phase 2) |
| M-03 schema_analyzer | DBHub search_objects 연동, 스키마 수집 |
| M-04 query_generator | LLM 기반 SQL 생성 |
| M-05 query_validator | SQL 검증 전체 (문법, 안전성, 성능) |
| M-06 query_executor | DBHub execute_sql 연동 |
| M-07 result_organizer | 기본 데이터 정리 (자연어 응답용) |
| M-08 output_generator | 자연어 응답 생성만 (파일 생성은 Phase 2) |
| M-09 DBHub Client | 연결 관리, search_objects, execute_sql |
| M-10 FastAPI | `/api/v1/query`, `/api/v1/health`, 기본 에러 핸들링 |

**관련 기능 요건:** F-01, F-02, F-03, F-04, F-05, F-06, F-09

**완료 기준:** 사용자가 자연어로 인프라 데이터를 질의하면 SQL을 자동 생성/실행하여 자연어 응답을 반환한다. SQL 검증 실패/실행 에러 시 최대 3회 재시도한다.

---

### Phase 2: 양식 기반 문서 생성

**범위:** 파일 업로드 -> 양식 파싱 -> DB 매핑 -> 데이터 채우기 -> 파일 반환

| 구현 대상 | 세부 항목 |
|----------|----------|
| M-02 input_parser 확장 | Excel 양식 파싱 (시트/헤더/데이터영역), Word 양식 파싱 (표/플레이스홀더) |
| M-04 query_generator 확장 | 양식 항목 -> DB 컬럼 의미적 매핑 기반 SQL 생성 |
| M-07 result_organizer 확장 | 양식 구조에 맞는 데이터 매핑/정리 |
| M-08 output_generator 확장 | Excel 파일 채우기 (openpyxl), Word 파일 채우기 (python-docx) |
| M-10 FastAPI 확장 | `/api/v1/query/file`, `/api/v1/query/{id}/download` |

**관련 기능 요건:** F-07, F-08

**완료 기준:** 사용자가 Excel/Word 양식 파일을 업로드하면, DB에서 데이터를 조회하여 양식에 맞게 채운 파일을 반환한다. 원본 서식이 보존된다.

---

### Phase 3: 안정화 및 부가 기능

**범위:** 대화 관리, 운영 기능, 부가 기능

| 구현 대상 | 세부 항목 |
|----------|----------|
| 멀티턴 대화 | 체크포인트 연동, 이전 대화 맥락 유지, 후속 질의 지원 |
| Human-in-the-loop | 실행 전 SQL을 사용자에게 보여주고 승인/수정 받기 |
| 양식 템플릿 관리 | 자주 사용하는 양식 등록/목록/조회/삭제 API |
| 감사 로그 | 모든 쿼리 실행 이력 기록 (사용자, 시간, SQL, 결과 건수) |
| 인증/인가 | 사용자 인증 체계, API 키 관리 |
| 쿼리 히스토리 | 이전 실행 쿼리 이력 조회 및 재실행 |
| 모니터링 | 시스템 상태 모니터링, 에러율/응답시간 메트릭 |

**관련 기능 요건:** F-10, F-11, F-12, F-14

**완료 기준:** 멀티턴 대화가 가능하고, 쿼리 실행 전 사용자 승인을 받을 수 있으며, 모든 실행 이력이 기록된다.

---

### Phase 4: UI 화면 (사용자/운영자)

**범위:** 사용자 Web UI + 운영자 관리 Web UI

| 구현 대상 | 세부 항목 |
|----------|----------|
| M-11 사용자 Web UI | 프롬프트 입력, 양식 파일 첨부(Excel/Word), 결과 표시, 파일 다운로드 |
| M-12 운영자 로그인 | ID/PW 기반 인증, JWT 토큰 발급, 세션 관리 |
| M-13 환경변수 설정 UI | .env 설정값 조회/수정, 민감값 마스킹, 실시간 반영 |
| M-14 DB 연결 설정 UI | DB 연결 정보 입력/수정, 연결 테스트, dbhub.toml 업데이트 |
| M-10 FastAPI 확장 | 운영자 API 엔드포인트 추가, 정적 파일 서빙 |

**관련 기능 요건:** F-16, F-17, F-18, F-19, F-20

**완료 기준:** 사용자가 Web UI에서 프롬프트 입력과 양식 첨부로 질의하고, 운영자가 별도 화면에서 환경변수와 DB 연결을 관리할 수 있다.

---

## 2.11 M-11: 사용자 Web UI

| 항목 | 내용 |
|------|------|
| **목적** | 사용자가 웹 브라우저에서 프롬프트 입력 및 양식 파일 첨부를 통해 에이전트를 사용 |
| **책임** | 프롬프트 입력 폼, 파일 업로드(드래그앤드롭), 결과 표시, 파일 다운로드 링크 제공 |
| **기술** | HTML/CSS/JS (FastAPI 정적 파일 서빙, Jinja2 템플릿 또는 순수 프론트엔드) |
| **의존성** | FastAPI 서버, 기존 `/api/v1/query` 및 `/api/v1/query/file` 엔드포인트 |
| **우선순위** | **Phase 4** |

**화면 구성 요소:**
1. 프롬프트 입력 텍스트 영역 (여러 줄 가능, 필수)
2. 파일 첨부 영역 (.xlsx, .docx 지원, 드래그앤드롭 가능, 선택)
3. 실행 버튼
4. 로딩 인디케이터
5. 결과 표시 영역 (자연어 응답 텍스트)
6. 파일 다운로드 버튼 (생성된 파일이 있는 경우)

**수용 기준:**
- [ ] 사용자가 프롬프트를 입력하고 실행 버튼을 클릭하면 자연어 응답이 표시된다
- [ ] 사용자가 Excel/Word 파일을 첨부할 수 있다 (드래그앤드롭 또는 파일 선택)
- [ ] 첨부된 파일과 프롬프트가 함께 서버에 전송된다
- [ ] 처리 중 로딩 상태가 표시된다
- [ ] 생성된 파일이 있으면 다운로드 링크가 제공된다
- [ ] .xlsx, .docx 이외의 파일은 첨부 시 경고 메시지를 표시한다

---

## 2.12 M-12: 운영자 인증 (Admin Auth)

| 항목 | 내용 |
|------|------|
| **목적** | 운영자 전용 화면에 대한 접근 제어 |
| **책임** | ID/PW 인증, JWT 토큰 발급/검증, 운영자 세션 관리 |
| **기술** | FastAPI + python-jose (JWT) 또는 PyJWT |
| **의존성** | 환경변수 ADMIN_USERNAME, ADMIN_PASSWORD |
| **우선순위** | **Phase 4** |

**수용 기준:**
- [ ] 운영자 로그인 페이지가 사용자 화면과 분리되어 존재한다 (/admin/login)
- [ ] ID/PW로 인증하면 JWT 토큰이 발급된다
- [ ] 인증되지 않은 사용자가 운영자 페이지 접근 시 로그인 페이지로 리디렉션된다
- [ ] 운영자 계정 정보는 환경변수(ADMIN_USERNAME, ADMIN_PASSWORD)로 설정된다

---

## 2.13 M-13: 환경변수 설정 UI

| 항목 | 내용 |
|------|------|
| **목적** | 운영자가 .env 파일의 설정값을 Web UI에서 조회/수정 |
| **책임** | .env 파일 읽기/쓰기, 설정값 목록 표시, 인라인 편집, 민감값 마스킹 |
| **기술** | FastAPI 엔드포인트 + 프론트엔드 폼 |
| **의존성** | M-12 운영자 인증 |
| **우선순위** | **Phase 4** |

**수용 기준:**
- [ ] .env 파일의 설정값이 키-값 목록으로 표시된다
- [ ] 각 설정값을 수정하고 저장할 수 있다
- [ ] API 키, 비밀번호 등 민감 설정값은 마스킹 표시된다
- [ ] 저장 시 .env 파일이 업데이트된다
- [ ] 운영자 인증 없이는 접근할 수 없다

---

## 2.14 M-14: DB 연결 설정 UI

| 항목 | 내용 |
|------|------|
| **목적** | 운영자가 DB 연결 정보를 Web UI에서 입력/수정 |
| **책임** | DB 연결 폼 제공 (유형, 호스트, 포트, DB명, 사용자명, 비밀번호), 연결 테스트, dbhub.toml 및 .env 파일 업데이트 |
| **기술** | FastAPI 엔드포인트 + 프론트엔드 폼 |
| **의존성** | M-12 운영자 인증 |
| **우선순위** | **Phase 4** |

**수용 기준:**
- [ ] DB 유형(PostgreSQL, MySQL, MariaDB 등), 호스트, 포트, DB명, 사용자명, 비밀번호를 입력하는 폼이 제공된다
- [ ] "연결 테스트" 버튼으로 입력한 정보로 실제 연결을 시도하고 결과를 표시한다
- [ ] 저장 시 .env 파일과 dbhub.toml이 업데이트된다
- [ ] 운영자 인증 없이는 접근할 수 없다

---

## 5. 비기능 요건 체크리스트

### 5.1 성능

- [ ] 단순 조회(단일 테이블 SELECT) 응답 시간 10초 이내
- [ ] 복합 조회(JOIN, GROUP BY, 집계) 응답 시간 30초 이내
- [ ] 양식 파일 생성 포함 전체 처리 시간 60초 이내
- [ ] 동시 사용자 최소 10명 지원 (스레드 기반 세션 분리)
- [ ] LLM API 호출 시 스트리밍 응답 활용하여 체감 속도 개선 (선택)

### 5.2 보안

- [ ] **DB 접근은 읽기 전용(readonly)으로 제한** (DBHub TOML 설정 및 query_validator 이중 검증)
- [ ] **SQL 검증 레이어 필수 적용** (query_validator 노드가 모든 SQL을 검증 후 통과)
- [ ] SELECT 이외의 SQL 문 완전 차단 (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE)
- [ ] SQL 인젝션 패턴 탐지 및 차단
- [ ] 사용자 인증/인가 체계 적용 (Phase 3)
- [ ] 민감 데이터(비밀번호, 접근키, 토큰 등) 조회 결과에서 마스킹 처리
- [ ] 감사 로그: 모든 쿼리 실행 이력 기록 (사용자, 시간, SQL, 결과 건수) (Phase 3)
- [ ] DBHub 연결 문자열은 환경변수 또는 시크릿 매니저로 관리 (.env 파일에 하드코딩 금지)
- [ ] SSH 터널링을 통한 안전한 원격 DB 접속 지원

### 5.3 안정성

- [ ] LLM API 호출 실패 시 재시도 로직 (exponential backoff: 1s, 2s, 4s)
- [ ] DB 연결 풀링 적용
- [ ] DB 연결 헬스체크 (주기적 ping)
- [ ] 체크포인트를 통한 상태 복구 가능 (중단된 처리 재개)
- [ ] 각 노드에서 발생 가능한 예외를 개별 처리 (전체 시스템 장애 방지)
- [ ] 쿼리 타임아웃(30초) 설정으로 장시간 쿼리 방지
- [ ] max_rows(10,000건) 제한으로 과도한 데이터 반환 차단

### 5.4 확장성

- [ ] 새로운 DB 소스 추가: DBHub TOML 설정만으로 가능
- [ ] 새로운 양식 형식(PDF 등) 추가: output_generator 노드만 확장
- [ ] LLM 모델 교체: 프롬프트 레이어만 수정 (langchain 추상화 활용)
- [ ] 다중 DB 소스 지원 구조 (Phase 3에서 구현, Phase 1에서 구조 대비)

---

## 6. 기술 의존성 목록

### 필수 라이브러리

| 패키지 | 최소 버전 | 용도 | Phase |
|--------|----------|------|-------|
| langgraph | 0.2.0 | 에이전트 프레임워크 | 1 |
| langchain-core | 0.3.0 | LLM 추상화 레이어 | 1 |
| dbhub | latest | DB 연결 MCP 서버 | 1 |
| langchain-anthropic | latest | Claude 연동 | 1 |
| langchain-openai | latest | OpenAI 연동 (대체용) | 1 |
| pydantic | 2.0 | 데이터 검증 | 1 |
| python-dotenv | latest | 환경변수 관리 | 1 |
| fastapi | 0.110.0 | API 서버 | 1 |
| uvicorn | 0.30.0 | ASGI 서버 | 1 |
| openpyxl | 3.1.0 | Excel 처리 | 2 |
| python-docx | 1.0.0 | Word 처리 | 2 |
| langgraph-checkpoint-sqlite | latest | 체크포인트 (개발용) | 1 |
| langgraph-checkpoint-postgres | latest | 체크포인트 (운영용) | 3 |

### 추가 권장 라이브러리

| 패키지 | 용도 | Phase |
|--------|------|-------|
| sqlparse | SQL 파싱/검증 (query_validator) | 1 |
| structlog / loguru | 구조화된 로깅 | 1 |
| httpx | 비동기 HTTP 클라이언트 | 1 |

---

## 7. 용어 정의

| 용어 | 정의 |
|------|------|
| LangGraph | LangChain 기반의 상태 머신 에이전트 프레임워크. 노드와 엣지로 워크플로우를 정의 |
| DBHub | 다중 DB를 지원하는 MCP(Model Context Protocol) 서버. SQL 실행, 스키마 조회 등을 제공 |
| MCP | Model Context Protocol. LLM이 외부 도구/데이터에 접근하기 위한 프로토콜 |
| State | LangGraph의 상태 객체. 노드 간 데이터를 전달하고 체크포인트로 저장 |
| 체크포인트 | LangGraph의 상태 저장 메커니즘. 대화 중단 시 복구 및 멀티턴 대화 지원 |
| 양식(Template) | 사용자가 업로드하는 Excel/Word 파일. 데이터를 채워넣을 빈 구조를 가짐 |

---

## 8. 미결 사항 및 결정 필요 항목

| # | 항목 | 설명 | 결정 주체 |
|---|------|------|----------|
| D-01 | LLM 모델 선택 | Claude vs GPT 중 기본 모델 결정 (또는 둘 다 지원) | 아키텍트/PM |
| D-02 | 체크포인트 저장소 | 개발 환경에서 SQLite, 운영에서 Postgres 사용 여부 확정 | 인프라팀 |
| D-03 | 인증 방식 | API 키 기반 / OAuth / JWT 중 선택 | 보안팀 |
| D-04 | 민감 데이터 정의 | 마스킹 대상 컬럼/패턴의 구체적 목록 | 보안팀/DBA |
| D-05 | 다중 DB 소스 | Phase 1에서 단일 DB만 지원 시, 다중 DB 구조를 미리 설계할 범위 | 아키텍트 |
| D-06 | 스케줄 리포트 | F-13(스케줄 기반 리포트)의 구현 범위 및 우선순위 | PM |
| D-07 | State에 organized_data 추가 | spec의 원본 State에는 없지만 result_organizer 출력을 위해 필요. 추가 승인 | 아키텍트 |
| D-08 | validation_result 필드 추가 | 조건부 라우팅을 위해 검증 결과를 State에 명시적으로 저장 필요. 추가 승인 | 아키텍트 |

---

## 9. 시멘틱 라우팅 및 멀티 DB 요구사항

> 추가일: 2026-03-16
> 수정일: 2026-03-17
> 목적: 사용자의 자연어 프롬프트를 분석하여 적절한 DB를 자동 선택하고 쿼리를 실행하는 시멘틱 라우팅 기능
> 변경 이력: v2 - 키워드 기반 1차 분류 제거, LLM 전용 라우팅으로 전환, 사용자 직접 DB 지정 지원 추가, 멀티 DB 결과 취합 강화

### 9.1 개요

기존 시스템은 단일 DB(infra_db)만 지원했으나, 실제 인프라 운영 환경에서는 도메인별로 분리된 여러 DB에서 데이터를 조회해야 한다. 시멘틱 라우팅은 사용자의 자연어 질의를 **LLM이 분석**하여 어떤 DB를 조회해야 하는지 자동으로 분류하고, 해당 DB에 맞는 SQL을 생성/실행하는 기능이다.

**v2 변경 사항:**
- 키워드 기반 1차 분류를 **완전히 제거**하고, LLM 기반으로만 DB 라우팅을 수행한다
- 사용자가 프롬프트에서 **직접 DB를 지정**할 수 있다 (예: "polestar에서 조회해줘", "ITSM DB에서 찾아줘")
- 하나의 질문에 대해 **여러 DB에서 데이터를 추출하고 취합**하는 워크플로우를 지원한다

### 9.2 DB 도메인 정의

| DB 식별자 | DB명 | 담당 도메인 | 주요 데이터 |
|-----------|------|-------------|-------------|
| `polestar` | Polestar DB | 서버 사양 및 사용량, 프로세스 | CPU/Core/Memory/Disk 크기, 월 평균/최고 CPU 사용률, Disk 사용용량, hostname/IP/gateway, 프로세스 정보 |
| `cloud_portal` | Cloud Portal DB | 가상화 인프라 | VM 정보, 데이터 스토어, 전체/영역별 VM 대수 (김포, 여의도, DMZ, 내부망 등) |
| `itsm` | ITSM DB | IT 서비스 관리 | 서비스 요청, 인시던트, 변경 관리, SLA 등 |
| `itam` | ITAM DB | IT 자산 관리 | IT 자산 목록, 라이프사이클, 계약 정보, 소프트웨어 라이선스 등 |

**DB 도메인 정의에서 `keywords` 필드는 제거된다.** 도메인 정의에는 `db_id`, `display_name`, `description`, `env_connection_key`, `env_type_key`만 유지한다. LLM이 `description`을 참고하여 대상 DB를 판단한다.

### 9.3 M-15: 시멘틱 라우터 (Semantic Router)

| 항목 | 내용 |
|------|------|
| **목적** | 사용자 프롬프트를 분석하여 어떤 DB를 조회해야 하는지 자동 분류. 사용자의 명시적 DB 지정도 지원 |
| **책임** | (1) 사용자 직접 DB 지정 감지 및 처리, (2) LLM을 통한 시멘틱 분석으로 대상 DB 결정, (3) 멀티 DB 조회가 필요한 경우 여러 DB를 선택하고 DB별 sub_query_context 생성, (4) 라우팅 결과를 State에 기록 |
| **입력** | `user_query: str`, `parsed_requirements: dict` |
| **출력** | `target_databases: list[DBRouteTarget]` (대상 DB 목록 및 각 DB별 질의 컨텍스트) |
| **의존성** | LLM (시멘틱 분석), DB 레지스트리 (사용 가능한 DB 목록 및 메타데이터) |
| **위치** | LangGraph 그래프에서 `input_parser` 직후, `schema_analyzer` 직전에 삽입 |

**라우팅 전략 (v2 - LLM 전용):**
1. **사용자 직접 DB 지정 감지**: 프롬프트에서 DB 이름/식별자가 명시적으로 언급되면 해당 DB를 우선 선택
   - LLM 프롬프트에 사용자 직접 지정 감지 규칙을 포함
   - 예: "polestar에서 조회해줘", "ITSM DB에서 장애 건수", "클라우드 포탈 DB에서 VM 현황"
   - 사용자가 DB를 직접 지정한 경우 `user_specified: true` 플래그를 결과에 포함
2. **LLM 기반 시멘틱 분류**: 사용자 질의의 의도와 컨텍스트를 LLM이 분석하여 대상 DB를 결정
   - 각 DB 도메인의 `description`을 LLM 프롬프트에 제공
   - LLM이 질의 내용과 DB 도메인 설명의 의미적 유사도를 판단
   - JSON 형식으로 대상 DB, 관련도 점수, 선택 이유, DB별 sub_query_context를 반환
3. **멀티 DB 판단 및 sub_query 분리**: 하나의 질의가 여러 DB를 필요로 하는 경우 LLM이 각 DB별로 조회할 내용을 분리하여 sub_query_context로 제공
   - 예: "서버 사양과 해당 서버의 VM 정보를 알려줘" -> polestar: "서버 사양 조회", cloud_portal: "VM 정보 조회"

**DBRouteTarget 구조 (v2):**
```python
class DBRouteTarget(TypedDict):
    db_id: str                    # DB 식별자: "polestar" | "cloud_portal" | "itsm" | "itam"
    relevance_score: float        # 관련도 점수 (0.0 ~ 1.0)
    sub_query_context: str        # 해당 DB에서 조회할 내용에 대한 설명/서브쿼리
    user_specified: bool          # 사용자가 직접 이 DB를 지정했는지 여부
    reason: str                   # DB 선택 이유
```

**수용 기준:**
- [ ] 사용자 질의에서 대상 DB를 LLM 분석으로 정확히 분류한다 (각 DB 도메인에 대한 테스트 케이스 통과)
- [ ] 멀티 DB 조회가 필요한 질의를 정확히 판별하고 여러 DB를 선택한다
- [ ] 사용자가 "polestar에서 조회해줘" 등으로 DB를 직접 지정하면 해당 DB로 라우팅한다
- [ ] 사용자가 DB를 직접 지정한 경우 `user_specified: true`가 결과에 포함된다
- [ ] **키워드 기반 분류 로직이 완전히 제거**되어, 모든 라우팅이 LLM을 통해 수행된다
- [ ] DB 레지스트리에 등록되지 않은 DB로 라우팅하지 않는다
- [ ] 라우팅 결과에 관련도 점수와 선택 이유가 포함되어 우선순위를 판단할 수 있다
- [ ] 멀티 DB 질의 시 각 DB별 sub_query_context가 적절히 분리되어 제공된다

---

### 9.4 M-16: 멀티 DB 연결 관리 (Multi-DB Registry)

| 항목 | 내용 |
|------|------|
| **목적** | 여러 DB의 연결 정보를 통합 관리하고, 라우팅 결과에 따라 적절한 DB 클라이언트를 제공 |
| **책임** | (1) DB 연결 설정 레지스트리 관리, (2) DB별 클라이언트 인스턴스 생성 및 풀링, (3) 헬스체크 및 연결 상태 모니터링 |
| **설정 방식** | `.env` 파일 또는 환경변수를 통한 DB별 연결 문자열 관리 |

**DB 연결 설정 구조 (`.env`):**
```
# Polestar DB
POLESTAR_DB_CONNECTION=postgresql://user:pass@host:5432/polestar_db
POLESTAR_DB_TYPE=postgresql

# Cloud Portal DB
CLOUD_PORTAL_DB_CONNECTION=postgresql://user:pass@host:5432/cloud_portal_db
CLOUD_PORTAL_DB_TYPE=postgresql

# ITSM DB
ITSM_DB_CONNECTION=postgresql://user:pass@host:5432/itsm_db
ITSM_DB_TYPE=postgresql

# ITAM DB
ITAM_DB_CONNECTION=postgresql://user:pass@host:5432/itam_db
ITAM_DB_TYPE=postgresql
```

**DBRegistry 클래스:**
```python
class DBRegistry:
    """멀티 DB 연결 레지스트리."""

    def get_client(self, db_id: str) -> DBClient:
        """DB 식별자에 해당하는 클라이언트를 반환한다."""
        ...

    def list_databases(self) -> list[DBInfo]:
        """등록된 모든 DB 목록을 반환한다."""
        ...

    def health_check(self, db_id: str) -> bool:
        """특정 DB의 연결 상태를 확인한다."""
        ...
```

**수용 기준:**
- [ ] 4개 DB(Polestar, Cloud Portal, ITSM, ITAM)의 연결 정보를 `.env`에서 로드한다
- [ ] DB 식별자로 해당 DB 클라이언트를 생성할 수 있다
- [ ] 등록되지 않은 DB 식별자 요청 시 명확한 에러를 반환한다
- [ ] 기존 단일 DB 모드(DB_BACKEND=direct)와의 하위 호환성을 유지한다
- [ ] 각 DB별 개별 헬스체크가 가능하다

---

### 9.5 M-17: 멀티 DB 파이프라인 오케스트레이션

| 항목 | 내용 |
|------|------|
| **목적** | 시멘틱 라우팅 결과에 따라 단일 또는 복수 DB에 대한 쿼리 파이프라인을 오케스트레이션 |
| **책임** | (1) 단일 DB 쿼리: 기존 파이프라인과 동일하게 순차 처리, (2) 멀티 DB 쿼리: 각 DB별로 schema_analyzer -> query_generator -> query_validator -> query_executor를 실행하고 결과를 통합, (3) 결과 병합 및 취합 정리 |

**수정되는 LangGraph 그래프 흐름:**
```
START -> input_parser -> semantic_router -> [조건부]
    |- 단일 DB: schema_analyzer -> query_generator -> query_validator -> query_executor -> result_organizer -> output_generator -> END
    |- 멀티 DB: multi_db_executor (내부에서 DB별 sub-pipeline 순차 실행) -> result_merger -> result_organizer -> output_generator -> END
```

**멀티 DB 결과 취합 전략:**
- 각 DB의 결과에 `_source_db` 필드를 추가하여 출처를 명시한다
- `result_merger`에서 DB별 결과를 하나의 리스트로 병합한다
- `result_organizer`에서 LLM이 병합된 결과를 사용자 질의 의도에 맞게 정리/취합한다
- 멀티 DB 결과 간 연관관계가 있는 경우 (예: hostname 기반 서버-VM 매핑) LLM이 이를 인식하여 통합 응답을 생성한다

**수용 기준:**
- [ ] 단일 DB 질의는 기존 파이프라인과 동일하게 동작한다 (하위 호환)
- [ ] 멀티 DB 질의 시 각 DB별 스키마 분석, SQL 생성, 검증, 실행이 독립적으로 수행된다
- [ ] 멀티 DB 결과가 하나의 통합된 결과로 병합된다
- [ ] 특정 DB 쿼리 실패 시 다른 DB 결과는 정상 반환되고, 실패 DB에 대한 부분 에러가 보고된다
- [ ] 멀티 DB 결과 간 연관관계를 LLM이 인식하여 통합 응답을 생성할 수 있다
- [ ] 각 DB별 sub_query_context가 SQL 생성 시 활용된다

---

### 9.6 AgentState 확장

기존 `AgentState`에 멀티 DB 지원을 위한 필드를 추가한다:

```python
class AgentState(TypedDict):
    # ... 기존 필드 유지 ...

    # === 시멘틱 라우팅 ===
    target_databases: list[dict]             # 라우팅된 대상 DB 목록 (DBRouteTarget)
    active_db_id: Optional[str]              # 현재 처리 중인 DB 식별자
    db_results: dict[str, list[dict]]        # DB별 쿼리 결과 {db_id: rows}
    db_schemas: dict[str, dict]              # DB별 스키마 정보 {db_id: schema_info}
    db_errors: dict[str, str]                # DB별 에러 메시지 {db_id: error_msg}
    is_multi_db: bool                        # 멀티 DB 쿼리 여부
    user_specified_db: Optional[str]         # 사용자가 직접 지정한 DB (없으면 None)
```

---

### 9.7 시멘틱 라우터 프롬프트 설계 (v2 - LLM 전용)

```
역할: 사용자의 인프라 관련 질의를 분석하여 어떤 데이터베이스를 조회해야 하는지 분류합니다.

사용 가능한 데이터베이스:

1. Polestar DB (polestar): 서버 물리 사양 및 사용량 데이터
   - 서버 사양: CPU, Core 수, Memory 크기, Disk 크기
   - 서버 사용량: 월 평균/최고 CPU 사용률, Disk 사용용량
   - 서버 정보: hostname, IP, gateway
   - 프로세스 정보: 서버에서 동작 중인 프로세스 종류

2. Cloud Portal DB (cloud_portal): 가상화 인프라 데이터
   - VM(가상머신) 정보
   - 데이터 스토어 정보
   - 전체 VM 대수
   - 영역별 VM 대수: 김포, 여의도, DMZ, 내부망 등

3. ITSM DB (itsm): IT 서비스 관리 데이터
   - 서비스 요청, 인시던트, 변경 관리, SLA 등

4. ITAM DB (itam): IT 자산 관리 데이터
   - IT 자산 목록, 라이프사이클, 계약, 소프트웨어 라이선스 등

## 사용자 직접 DB 지정 규칙

사용자가 프롬프트에서 특정 DB를 명시적으로 지정할 수 있습니다.
다음과 같은 패턴을 인식하세요:
- DB 식별자 직접 언급: "polestar에서", "cloud_portal에서", "itsm에서", "itam에서"
- DB 표시명 언급: "Polestar DB에서", "Cloud Portal에서", "ITSM DB에서", "ITAM DB에서"
- 한국어 별칭: "클라우드 포탈에서", "자산관리 DB에서" 등

사용자가 DB를 직접 지정한 경우 해당 DB를 반드시 포함하고, user_specified를 true로 설정하세요.

## 멀티 DB 쿼리 판단

하나의 질의가 여러 DB의 데이터를 필요로 할 수 있습니다.
이 경우 각 DB별로 조회해야 할 내용을 sub_query_context에 분리하여 기술하세요.
예: "서버 사양과 해당 서버의 VM 정보를 알려줘"
  -> polestar: "서버 사양(CPU, Memory, Disk) 조회"
  -> cloud_portal: "서버에 연결된 VM 정보 조회"

출력 형식: 반드시 아래 JSON 형식으로만 응답하세요.
{
    "databases": [
        {
            "db_id": "데이터베이스 식별자",
            "relevance_score": 0.9,
            "reason": "선택 이유",
            "sub_query_context": "이 DB에서 조회할 내용",
            "user_specified": false
        }
    ]
}
```

---

### 9.8 관련 기능 요건 매핑

| ID | 기능 | 설명 | 우선순위 |
|----|------|------|----------|
| F-21 | LLM 기반 시멘틱 라우팅 | 사용자 프롬프트를 LLM이 분석하여 대상 DB를 자동 분류 (키워드 분류 제거) | Must-Have |
| F-22 | 멀티 DB 연결 관리 | 4개 DB의 연결 정보를 레지스트리로 통합 관리 | Must-Have |
| F-23 | 멀티 DB 쿼리 실행 및 결과 취합 | 여러 DB에 대한 쿼리를 실행하고 결과를 통합 취합 | Must-Have |
| F-24 | 결과 병합 | 여러 DB의 조회 결과를 하나의 통합 응답으로 병합 | Must-Have |
| F-25 | DB별 스키마 캐싱 | 각 DB의 스키마를 독립적으로 캐싱하여 성능 최적화 | Nice-to-Have |
| F-26 | 라우팅 설명 | 어떤 DB를 왜 선택했는지 사용자에게 설명 제공 | Nice-to-Have |
| F-27 | 사용자 직접 DB 지정 | 사용자가 프롬프트에서 "polestar에서 조회해줘" 등으로 DB를 명시적 지정 | Must-Have |

---

### 9.9 비기능 요건 추가

**성능:**
- [ ] 시멘틱 라우팅 판단 시간: 5초 이내 (LLM 전용으로 변경에 따라 기존 3초에서 완화)
- [ ] 멀티 DB 쿼리 시 총 실행 시간 최적화
- [ ] DB별 스키마 캐시 TTL: 5분 (기존과 동일)

**보안:**
- [ ] 모든 DB 연결은 읽기 전용(readonly) 제한 유지
- [ ] DB별 개별 연결 문자열은 환경변수로 관리 (하드코딩 금지)
- [ ] 라우팅 결과에 DB 연결 정보(비밀번호 등)가 노출되지 않도록 마스킹

**안정성:**
- [ ] 특정 DB 연결 실패 시 해당 DB만 스킵하고 나머지 DB 결과는 정상 반환
- [ ] DB 레지스트리에 등록되지 않은 DB로의 라우팅 시도를 안전하게 차단
- [ ] 멀티 DB 실행 중 부분 실패 시 성공한 결과와 함께 실패 정보도 응답에 포함
- [ ] LLM 라우팅 호출 실패 시 에러를 사용자에게 명확히 전달 (키워드 폴백 없음)

**확장성:**
- [ ] 새 DB 추가 시 `.env`에 연결 정보와 DB 도메인 설정에 description만 추가하면 됨
- [ ] 시멘틱 라우터의 DB 도메인 정의가 설정 파일로 분리되어 코드 변경 없이 확장 가능
