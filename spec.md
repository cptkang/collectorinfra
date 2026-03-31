# 인프라 데이터 조회 에이전트 요건 정의서

## 1. 프로젝트 개요

### 1.1 목적

사용자가 자연어(한국어)로 인프라 정보를 질의하면, LLM이 DB 스키마를 분석하여 SQL을 자동 생성·실행하고, 결과를 정리하여 자연어 응답 또는 문서(Excel/Word) 형태로 제공하는 에이전트를 구축한다.

### 1.2 핵심 워크플로우

```
사용자 자연어 질의 또는 양식 파일 업로드
    ↓
대화 맥락 복원 (멀티턴)
    ↓
입력 파싱 + 필드 매핑 (양식 → DB 컬럼)
    ↓
시멘틱 라우팅 (대상 DB 자동 선택)
    ↓
DB 스키마 분석 (캐시 활용, EAV 구조 감지)
    ↓
SQL 쿼리 자동 생성 및 검증
    ↓
[선택] 사용자 SQL 승인 (Human-in-the-loop)
    ↓
DB 실행 및 데이터 수집 (멀티 DB 병렬 가능)
    ↓
결과 정리 + 데이터 충분성 검사
    ↓
LLM 자연어 응답 생성 또는 양식 파일(Excel/Word) 작성
```

### 1.3 기술 스택

| 구분 | 기술 | 비고 |
|------|------|------|
| 에이전트 프레임워크 | **LangGraph** (≥0.2.0) | 노드/엣지 기반 상태 머신, 체크포인트 |
| LLM | Ollama / FabriX / Google Gemini | 프로바이더 선택 가능 (env 설정) |
| DB 연결 | **자체 MCP 서버** (SSE transport) | Python FastMCP 기반, 다중 DB 지원 |
| DB 직접 연결 | asyncpg (PostgreSQL) | MCP 대안, 동일 인터페이스 |
| 문서 처리 | openpyxl (Excel), python-docx (Word) | 양식 파싱 및 생성 |
| API 서버 | FastAPI + uvicorn | 웹 UI + REST API |
| 캐시 | Redis + 파일 + 메모리 | 3단계 스키마 캐시 |
| 체크포인트 | SQLite (dev) / InMemory | 멀티턴 대화 상태 유지 |
| 설정 | pydantic-settings | 타입 안전 환경변수 관리 |

---

## 2. 시스템 아키텍처

### 2.1 전체 구성도

```
┌─────────────────────────────────────────────────────────┐
│                    사용자 인터페이스                        │
│         (Web UI: 채팅 인터페이스 + SSE 스트리밍)            │
│         (운영자 대시보드: 설정/캐시 관리)                    │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│               FastAPI 서버 (REST + SSE)                   │
│  /api/v1/query | /conversation | /admin | /schema-cache  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                LangGraph 에이전트                          │
│                                                          │
│  context_resolver → input_parser → field_mapper          │
│       ↓                                                  │
│  semantic_router → [단일DB / 멀티DB / 캐시관리 / 유사어등록] │
│       ↓                                                  │
│  schema_analyzer → query_generator → query_validator     │
│       ↓                                                  │
│  [approval_gate] → query_executor → result_organizer     │
│       ↓                                                  │
│  output_generator                                        │
│                                                          │
│  ┌───────────────────────────────────────────┐          │
│  │  State (AgentState) + 체크포인트 관리       │          │
│  └───────────────────────────────────────────┘          │
└──────────────────────┬──────────────────────────────────┘
                       │
              ┌────────┴────────┐
              ▼                 ▼
┌──────────────────┐  ┌──────────────────────┐
│  MCP 서버 (SSE)   │  │   Redis 캐시 서버     │
│  search_objects   │  │   스키마/설명/유사어   │
│  execute_sql      │  │   fingerprint        │
│  get_table_schema │  └──────────────────────┘
│  health_check     │
│  list_sources     │
└────────┬─────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│              인프라 데이터베이스                             │
│     PostgreSQL / DB2 / MySQL / MariaDB                   │
└─────────────────────────────────────────────────────────┘
```

### 2.2 LangGraph 노드 정의

| 노드 | 역할 | 입력 | 출력 |
|------|------|------|------|
| **context_resolver** | 멀티턴 대화 시 이전 맥락(SQL, 결과, 테이블, pending 상태) 추출 | thread_id, 체크포인트 | conversation_context |
| **input_parser** | 사용자 입력(자연어/파일) 분석, 의도 파악, CSV 컨텍스트 생성 | user_query, uploaded_file, csv_sheet_data | parsed_requirements, template_structure |
| **field_mapper** | 양식 필드를 DB 컬럼에 3단계 매핑 (힌트→synonym→LLM 추론) | template_structure, schema_cache | column_mapping, db_column_mapping, mapping_sources |
| **semantic_router** | LLM 기반 대상 DB 라우팅, 캐시관리/유사어등록 의도 분류 | parsed_requirements, mapped_db_ids | target_databases, routing_intent, is_multi_db |
| **schema_analyzer** | DB 스키마 조회, EAV 구조 감지, 캐시 활용 | target DB, relevant_tables | schema_info, column_descriptions, column_synonyms |
| **query_generator** | LLM SQL 생성, EAV 피벗 쿼리, DB 엔진별 문법 분기 | parsed_requirements + schema_info + column_mapping | generated_sql |
| **query_validator** | SQL 문법·안전성·참조 컬럼 검증, LIMIT 강제 | generated_sql, schema_info | validation_result |
| **approval_gate** | SQL 실행 전 사용자 승인 대기 (HITL) | generated_sql | approval_action |
| **query_executor** | MCP/직접연결로 SQL 실행, 감사 로그 기록 | validated SQL | query_results, query_attempts |
| **multi_db_executor** | 여러 DB에 대해 schema_analyzer→...→executor 순차 실행 | target_databases | db_results, db_errors |
| **result_merger** | 멀티 DB 결과 통합, DB별 요약 생성 | db_results | query_results (통합) |
| **result_organizer** | 민감 데이터 마스킹, 데이터 충분성 검사, 매핑 기반 정리 | query_results, column_mapping | organized_data |
| **output_generator** | 자연어 응답 생성(LLM) 또는 Excel/Word 파일 작성 | organized_data, template | final_response, output_file |
| **cache_management** | 스키마 캐시 CRUD 작업 (생성/갱신/삭제) | routing_intent | final_response |
| **synonym_registrar** | LLM 추론 매핑을 Redis 유사어로 등록 | pending_synonym_registrations | final_response |
| **structure_approval_gate** | 구조 분석 결과 승인 (HITL) | schema_info | approval_action |
| **error_response** | 최대 재시도 초과 시 에러 응답 생성 | error_message | final_response |

### 2.3 엣지 (제어 흐름)

```
START → context_resolver → input_parser → field_mapper
    → [시멘틱 라우팅 활성화 시]
        → semantic_router → [조건부]
            ├─ 캐시 관리 의도 → cache_management → END
            ├─ 유사어 등록 의도 → synonym_registrar → END
            ├─ 멀티 DB → multi_db_executor → result_merger → result_organizer → output_generator → END
            └─ 단일 DB → schema_analyzer
    → [시멘틱 라우팅 비활성화 시]
        → schema_analyzer

schema_analyzer → [구조 승인 활성화 시]
    → structure_approval_gate → [approve: schema_analyzer | reject: query_generator]

schema_analyzer → query_generator → query_validator
    → [조건부]
        ├─ 검증 통과 → [SQL 승인 활성화 시: approval_gate] → query_executor
        ├─ 검증 실패 + 재시도 가능 → query_generator (최대 3회)
        └─ 검증 실패 + 재시도 초과 → error_response → END

query_executor → [조건부]
    ├─ 실행 성공 → result_organizer
    ├─ 실행 에러 + 재시도 가능 → query_generator (에러 메시지 포함 재생성)
    └─ 실행 에러 + 재시도 초과 → error_response → END

result_organizer → [조건부]
    ├─ 데이터 충분 → output_generator → END
    ├─ 데이터 부족 + 재시도 가능 → query_generator (추가 쿼리 생성)
    └─ 데이터 부족 + 재시도 초과 → output_generator (있는 데이터로 생성) → END

approval_gate → [조건부]
    ├─ approve → query_executor
    ├─ modify → query_validator (수정된 SQL 재검증)
    └─ reject → END
```

### 2.4 State 스키마 (AgentState)

```python
class AgentState(TypedDict):
    # === 사용자 입력 ===
    user_query: str                          # 자연어 질의
    uploaded_file: Optional[bytes]           # 업로드된 양식 파일 바이너리
    file_type: Optional[str]                 # "xlsx" | "docx" | None

    # === 파싱 결과 ===
    parsed_requirements: dict                # 구조화된 요구사항
    template_structure: Optional[dict]       # 양식 구조 정보
    target_sheets: Optional[list[str]]       # 대상 시트 목록 (None이면 전체)
    csv_sheet_data: Optional[dict]           # 시트별 CsvSheetData (Excel→CSV 변환 결과)

    # === DB 관련 ===
    relevant_tables: list[str]               # 관련 테이블 목록
    schema_info: dict                        # 스키마 상세 (테이블, 컬럼, FK, EAV 메타)
    column_descriptions: dict[str, str]      # 컬럼 설명 {table.column: description}
    column_synonyms: dict[str, list[str]]    # 유사 단어 {table.column: [synonym, ...]}
    resource_type_synonyms: dict[str, list[str]]  # RESOURCE_TYPE 값 유사단어
    eav_name_synonyms: dict[str, list[str]]       # EAV NAME 값 유사단어
    generated_sql: str                       # 현재 SQL 쿼리
    validation_result: ValidationResult      # 검증 결과
    query_results: list[dict]                # 현재 쿼리 실행 결과

    # === 가공 결과 ===
    organized_data: OrganizedData            # 정리된 데이터 (summary, rows, column_mapping, is_sufficient, sheet_mappings)

    # === 필드 매핑 ===
    column_mapping: Optional[dict]           # 통합 매핑 {field: "table.column" | "EAV:속성명"}
    db_column_mapping: Optional[dict]        # DB별 매핑 {db_id: {field: "table.column"}}
    mapping_sources: Optional[dict]          # 매핑 출처 {field: "hint"|"synonym"|"eav_synonym"|"llm_inferred"}
    mapped_db_ids: Optional[list[str]]       # 매핑에서 식별된 DB 목록
    pending_synonym_registrations: Optional[list[dict]]  # 유사어 등록 대기
    llm_inference_details: Optional[list[dict]]          # LLM 추론 매핑 상세
    mapping_report_md: Optional[str]         # 매핑 보고서 Markdown

    # === 유사단어 재활용 대기 ===
    pending_synonym_reuse: Optional[dict]    # 유사어 재활용 제안

    # === DB 엔진 정보 ===
    active_db_engine: Optional[str]          # "db2" | "postgresql" 등

    # === 시멘틱 라우팅 ===
    routing_intent: Optional[str]            # "data_query" | "cache_management" | "synonym_registration"
    target_databases: list[dict]             # 라우팅된 대상 DB 목록
    active_db_id: Optional[str]              # 현재 처리 중인 DB
    db_results: dict[str, list[dict]]        # DB별 쿼리 결과
    db_schemas: dict[str, dict]              # DB별 스키마 정보
    db_errors: dict[str, str]               # DB별 에러 메시지
    is_multi_db: bool                        # 멀티 DB 쿼리 여부
    user_specified_db: Optional[str]         # 사용자 직접 지정 DB

    # === 멀티턴 대화 ===
    messages: Annotated[list[BaseMessage], add_messages]  # 대화 히스토리 (누적)
    thread_id: Optional[str]                 # 세션 식별자
    conversation_context: Optional[dict]     # 이전 대화 맥락

    # === Human-in-the-loop ===
    awaiting_approval: bool                  # 사용자 승인 대기 여부
    approval_context: Optional[dict]         # 승인 요청 컨텍스트
    approval_action: Optional[str]           # "approve" | "reject" | "modify"
    approval_modified_sql: Optional[str]     # 수정된 SQL

    # === 제어 ===
    retry_count: int                         # 재시도 횟수 (최대 3)
    error_message: Optional[str]             # 에러 메시지
    current_node: str                        # 현재 실행 중인 노드
    query_attempts: list[QueryAttempt]       # SQL 시도 이력

    # === 출력 ===
    final_response: str                      # 자연어 응답
    output_file: Optional[bytes]             # 생성된 파일 바이너리
    output_file_name: Optional[str]          # 출력 파일명
```

---

## 3. DB 연결 및 MCP 서버

### 3.1 자체 MCP 서버 (`mcp_server/`)

외부 npm 패키지 `dbhub` 대신 Python FastMCP 기반 자체 MCP 서버를 사용한다.

| 항목 | 내용 |
|------|------|
| Transport | SSE (HTTP, 분산 배포 가능) |
| 패키지 | `mcp_server/` (독립 pyproject.toml) |
| 설정 | `mcp_server/config.toml` |
| 지원 DB | PostgreSQL (asyncpg), DB2 (ibm_db, asyncio.to_thread 래핑) |

### 3.2 MCP 도구

| 도구 | 용도 | 사용 노드 |
|------|------|----------|
| `search_objects` | 테이블 목록 조회, 스키마 탐색 | schema_analyzer |
| `execute_sql` | SQL 쿼리 실행 (readonly 강제) | query_executor |
| `get_table_schema` | 상세 테이블 스키마 조회 | schema_analyzer |
| `health_check` | DB 연결 상태 확인 | health API |
| `list_sources` | 활성 DB 소스 목록 | admin API |

### 3.3 직접 DB 연결 (대안)

MCP 서버 없이 asyncpg로 PostgreSQL에 직접 연결하는 `PostgresClient`도 제공한다. `DBClient` Protocol 인터페이스로 MCP/직접연결을 동일하게 사용.

### 3.4 설정 분리

- DB 연결 정보(호스트, 포트, 비밀번호): MCP 서버 VM에만 존재
- 에이전트 클라이언트: MCP 서버 URL만 보유 (`DBHUB_SERVER_URL`)
- 활성 DB 목록: `MULTI_DB_ACTIVE_DB_IDS_CSV` 환경변수

### 3.5 보안

- DB 연결은 **읽기 전용(readonly)** 모드로 제한
- 쿼리 타임아웃 설정 (서버 측)
- max_rows 제한으로 과도한 데이터 반환 차단
- 이중 SQL 가드: 서버 자체 + 클라이언트 측

---

## 4. 시멘틱 라우팅 (멀티 DB 지원)

### 4.1 LLM 전용 라우팅

DB 라우팅은 **LLM 전용**으로 수행한다. 키워드 기반 사전 분류는 사용하지 않는다.

- 사용자 직접 DB 지정: `aliases` 필드로 DB별 인식 가능 이름 정의
- 멀티 DB 분류: LLM이 각 DB별 `sub_query_context`를 분리하여 반환
- 동적 프롬프트: 활성 도메인만 포함
- confidence 기반 필터링: `relevance_score` 임계값 이하 DB 제외

### 4.2 의도 분류

semantic_router는 3가지 의도를 분류한다:

| 의도 | 설명 | 대상 노드 |
|------|------|----------|
| `data_query` | 데이터 조회 질의 | schema_analyzer (단일) / multi_db_executor (멀티) |
| `cache_management` | 스키마 캐시 관리 요청 | cache_management |
| `synonym_registration` | 유사어 등록 요청 | synonym_registrar |

### 4.3 도메인 구성

| DB ID | 대상 데이터 | 별칭 예시 | DB 엔진 |
|-------|-----------|----------|---------|
| `polestar` | 서버 사양, 사용량, EAV 리소스 | 폴스타, Polestar | DB2 / PostgreSQL |
| `cloud_portal` | VM 정보, 데이터스토어 | 클라우드 포탈, Cloud Portal | - |
| `itsm` | IT 서비스 관리 | ITSM | - |
| `itam` | IT 자산 관리 | ITAM | - |

### 4.4 멀티 DB 실행

- 각 DB를 **순차적으로 독립 실행** (부분 실패 허용)
- `_source_db` 태깅으로 데이터 출처 표시
- `result_merger`가 DB별 결과를 통합
- DB별 성공/실패 현황 보고

---

## 5. 스키마 캐시 시스템

### 5.1 4단계 캐시 구조

```
요청 → 1차 메모리 캐시 (TTL 5분)
  ├─ 히트 → 바로 사용
  └─ 미스 → 2차 Redis 캐시 (fingerprint 비교)
       ├─ fingerprint 일치 → Redis에서 로드 + descriptions/synonyms 로드
       └─ 불일치/Redis 장애 → 3차 파일 캐시 폴백
            └─ 미스 → 4차 DB 전체 조회 → 캐시 갱신
```

### 5.2 Redis 키 구조

| 키 패턴 | 내용 |
|---------|------|
| `schema:{db_id}:meta` | fingerprint, cached_at, table_count |
| `schema:{db_id}:tables` | 테이블별 스키마 JSON |
| `schema:{db_id}:relationships` | FK 관계 JSON 배열 |
| `schema:{db_id}:descriptions` | 컬럼별 한국어 설명 (LLM 생성) |
| `schema:{db_id}:synonyms` | 컬럼별 유사 단어 (LLM 생성) |
| `synonyms:global` | 글로벌 유사단어 사전 (수동 등록분 보존) |

### 5.3 Fingerprint 기반 유효성 검사

- `information_schema.columns`에서 테이블별 컬럼 수 조회 (가벼운 쿼리)
- 테이블명+컬럼수를 정렬된 JSON → SHA-256 해시
- 캐시된 해시와 비교하여 변경 감지
- **Fingerprint TTL**: 30분 (이 시간 동안 DB 재질의 없이 캐시 유효 판정)

### 5.4 LLM 컬럼 설명/유사 단어 생성

- 스키마 캐시 생성 시 LLM이 컬럼별 한국어 설명 + 유사 단어를 자동 생성
- query_generator 프롬프트에 descriptions/synonyms 포함하여 컬럼 선택 정확도 향상
- 운영자 API를 통해 수동 수정 가능
- 글로벌 사전(`synonyms:global`)은 스키마 갱신 시에도 보존

### 5.5 캐시 관리 API

| 엔드포인트 | 용도 |
|-----------|------|
| `POST /admin/schema-cache/generate` | 스키마 캐시 생성/갱신 |
| `GET /admin/schema-cache/status` | DB별 캐시 상태 조회 |
| `POST /admin/schema-cache/refresh-fingerprint` | fingerprint 갱신 |
| `PUT /admin/schema-cache/descriptions` | 컬럼 설명 수정 |
| `DELETE /admin/schema-cache/{db_id}` | 캐시 삭제 |
| `GET /admin/schema-cache/synonyms` | 유사어 목록 조회 |
| `POST /admin/schema-cache/synonyms/register` | 유사어 수동 등록 |
| `DELETE /admin/schema-cache/synonyms/{db_id}/{column}` | 유사어 삭제 |

---

## 6. 양식 기반 문서 생성

### 6.1 지원 파일 형식

| 형식 | 입력(양식 파싱) | 출력(결과 작성) |
|------|----------------|----------------|
| Excel (.xlsx) | O | O |
| Word (.docx) | O | O |

### 6.2 양식 처리 워크플로우

```
1. 사용자가 양식 파일(Excel/Word) 업로드
    ↓
2. input_parser: 양식 구조 분석 + Excel→CSV 변환 (예시 데이터 추출)
   - Excel: 시트별 헤더, 셀 위치, 데이터 영역, 예시 데이터(최대 50행)
   - Word: 표(Table) 구조, {{placeholder}} 패턴
    ↓
3. field_mapper: 3단계 매핑 (양식 필드 → DB 컬럼)
   - 1단계: 프롬프트 힌트 (사용자 입력에서 추출)
   - 2단계: Redis 유사어 매칭
   - 2.5단계: EAV synonym 매칭 (EAV:속성명 규약)
   - 3단계: LLM 추론 (Redis synonyms + descriptions + EAV names 결합 컨텍스트)
    ↓
4. 매핑 기반으로 SQL 쿼리 생성 및 실행
    ↓
5. result_organizer: 쿼리 결과를 양식 구조에 맞게 정리
    ↓
6. output_generator: 양식에 데이터 채우기 + 매핑 보고서 첨부
    ↓
7. 완성된 파일을 사용자에게 전달 (다운로드)
```

### 6.3 Excel 처리 상세

- **헤더 자동 탐지**: 첫 번째 비어있지 않은 행
- **멀티시트 지원**: 모든 시트 독립 처리, `target_sheets`로 특정 시트만 선택 가능
- **병합 셀 보존**: 원본 양식의 병합 셀 구조 유지
- **수식 보존**: 수식이 포함된 셀은 유지, 데이터 셀만 채움
- **서식 보존**: 원본 양식의 서식(글꼴, 색상, 테두리 등) 유지
- **CSV 변환**: LLM 컨텍스트 보강용 (헤더 + 예시 데이터 추출)

### 6.4 Word 처리 상세

- **플레이스홀더 탐지**: `{{placeholder}}` 패턴
- **표(Table) 지원**: 표 헤더/데이터 행 구분, 행 추가
- **스타일 보존**: 원본 문서 스타일(글꼴, 문단 서식) 유지

### 6.5 필드 매핑 규약

- **정규 컬럼**: `"table.column"` 형식 (예: `"servers.hostname"`)
- **EAV 속성**: `"EAV:속성명"` 형식 (예: `"EAV:OSType"`, `"EAV:Vendor"`)
- **매핑 출처 추적**: `mapping_sources` 필드에 `"hint"`, `"synonym"`, `"eav_synonym"`, `"llm_inferred"` 기록
- **LLM 추론 매핑 즉시 Redis 등록**: 새 매핑 발견 시 Redis synonyms에 자동 저장
- **매핑 보고서**: Markdown 형식으로 생성, 사용자 수정/업로드로 교정 가능

---

## 7. EAV (Entity-Attribute-Value) 구조 지원

### 7.1 개요

Polestar DB의 EAV 구조 (CMM_RESOURCE + CORE_CONFIG_PROP)에 대한 자동 감지 및 쿼리 지원.

### 7.2 자동 감지

- schema_analyzer가 CMM_RESOURCE + CORE_CONFIG_PROP 테이블 존재 시 자동 감지
- `_polestar_meta`를 schema_info에 삽입
- EAV 샘플 데이터, RESOURCE_TYPE 분포, known_attributes 수집

### 7.3 쿼리 생성

- **CASE WHEN 피벗**: EAV 속성을 정규 컬럼처럼 표현
- **계층형 self-join**: PARENT_RESOURCE_CONF_ID 기반 트리 탐색
- **DB 엔진 분기**: DB2(`FETCH FIRST N ROWS ONLY`) / PostgreSQL(`LIMIT N`)
- **6개 쿼리 패턴**: 서버 사양 조회, 리소스 사용량, 계층 탐색, 조건부 조합 등

### 7.4 필드 매핑

- 2.5단계 EAV synonym 매칭: Redis `eav_name_synonyms`에서 매칭
- `EAV:속성명` 접두사 규약으로 정규/EAV 매핑 구분
- query_generator가 EAV 매핑 감지 시 CASE WHEN 피벗 힌트 자동 삽입

### 7.5 하위 호환성

- `_polestar_meta`가 없으면 기존 로직 그대로 동작
- `db_engine` 기본값 "postgresql"로 기존 DB 불변
- EAV 관련 데이터가 없으면 2.5단계 스킵

---

## 8. 보안

### 8.1 3중 읽기 전용 방어 (절대 변경 불가)

1. **MCP 서버 설정 레벨**: readonly 강제
2. **SQL 검증 레벨**: `query_validator`에서 DML/DDL 키워드 차단 (INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, CREATE, GRANT, REVOKE)
3. **LLM 프롬프트 레벨**: "SELECT 문만 생성" 명시

### 8.2 SQL 검증 (`query_validator`)

1. SQL 문법 검증 (sqlparse)
2. SELECT 문 여부 확인 (DML/DDL/DCL 차단)
3. 참조 테이블/컬럼 존재 여부 확인
4. LIMIT 절 존재 여부 확인 (대량 조회 방지, DB 엔진별 자동 추가)
5. 잠재적 성능 이슈 확인 (전체 테이블 스캔 등)
6. SQL 인젝션 패턴 탐지 (UNION, 주석, 저장 프로시저, BENCHMARK, SLEEP, @@변수)
7. 다중 문장 차단

### 8.3 민감 데이터 마스킹

- **컬럼 기반**: 민감 컬럼명 (password, token, secret 등) 자동 마스킹
- **패턴 기반**: API 키, JWT 토큰, SSN, 신용카드, bcrypt 해시
- **부분 마스킹**: IP 주소, 이메일 (앞 2자리만 표시)

### 8.4 감사 로그

- 모든 SQL 실행 이력을 JSONL 파일로 기록
- 일별 로그 파일 로테이션
- 기록 항목: SQL, 행 수, 실행 시간, 성공/실패, 재시도 횟수, 검증 경고
- 비동기 로깅

### 8.5 관리자 인증

- JWT 기반 인증 (자동 시크릿 생성)
- 환경변수로 관리자 계정 설정 (ADMIN_USERNAME, ADMIN_PASSWORD)

---

## 9. 멀티턴 대화 및 Human-in-the-loop

### 9.1 멀티턴 대화

- **체크포인트 기반**: LangGraph 체크포인트로 대화 상태 자동 유지
- **통합 코드 경로**: 단일 턴과 멀티턴이 동일 그래프를 통과 (단일 턴은 멀티턴의 특수 경우)
- **context_resolver**: 그래프 첫 노드에서 이전 대화 맥락(SQL, 결과, 테이블, pending 상태) 추출
- **thread_id**: 세션 기반 분리, 다중 사용자 동시 지원
- **대화 이력 API**: `GET /conversation/{thread_id}` (메시지 역할, 턴 수, pending 상태)

### 9.2 SQL 승인 (Human-in-the-loop)

- `ENABLE_SQL_APPROVAL=true` 환경변수로 활성화
- `approval_gate` 노드에서 `interrupt_before` 사용
- 사용자 응답: `approve` (실행) / `reject` (중단) / `modify` (SQL 수정 후 재검증)
- `POST /conversation/{thread_id}/approve` API

### 9.3 구조 분석 승인

- `ENABLE_STRUCTURE_APPROVAL=true` 환경변수로 활성화
- schema_analyzer의 구조 분석 결과를 사용자에게 확인
- `approve` → 캐시 저장 후 진행 / `reject` → 구조 메타 없이 진행

### 9.4 유사어 등록 승인

- field_mapper의 LLM 추론 매핑을 사용자에게 표시
- `pending_synonym_registrations`에 등록 대기 항목 저장
- 사용자 승인 시 `synonym_registrar`가 Redis에 등록
- 체크포인트에서 pending 상태 자동 복원

---

## 10. LLM 프로바이더

### 10.1 지원 프로바이더

| 프로바이더 | 설정 | 비고 |
|-----------|------|------|
| **Ollama** | `LLM_PROVIDER=ollama` | 로컬 모델, HTTP API |
| **FabriX** | `LLM_PROVIDER=fabrix` | SDS 기업용 API (KBGenAI / OpenAI 호환) |
| **Google Gemini** | `LLM_PROVIDER=gemini` | 클라우드, `GOOGLE_API_KEY` 필요 |

### 10.2 프로바이더 전환

- `LLM_PROVIDER` 환경변수로 전환
- 모델명은 프로바이더별 설정 (`LLM_MODEL`, `LLM_GEMINI_MODEL`, `LLM_FABRIX_CHAT_MODEL`)
- 팩토리 패턴으로 LLM 인스턴스 생성, 그래프 빌드 시 1회 생성하여 partial로 노드에 주입

---

## 11. API 서버

### 11.1 엔드포인트 목록

#### 질의 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/api/v1/query` | 자연어 질의 실행 (텍스트 + 파일 업로드) |
| GET | `/api/v1/query/{query_id}` | 쿼리 결과 조회 |
| GET | `/api/v1/query/{query_id}/stream` | SSE 스트리밍 결과 |

#### 대화 API

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/api/v1/conversation/{thread_id}` | 대화 히스토리 조회 |
| POST | `/api/v1/conversation/{thread_id}/approve` | 사용자 승인 응답 |

#### 관리자 API (인증 필요)

| 메서드 | 경로 | 설명 |
|--------|------|------|
| POST | `/admin/login` | 관리자 로그인 (JWT 발급) |
| GET | `/admin/settings` | 환경변수 조회 (민감 값 마스킹) |
| PUT | `/admin/settings` | 환경변수 수정 |
| GET | `/admin/db-config` | DB 연결 설정 조회 |
| PUT | `/admin/db-config` | DB 연결 설정 수정 |
| POST | `/admin/db-config/test` | DB 연결 테스트 |

#### 스키마 캐시 API (인증 필요)

(→ 5.5절 참조)

#### 헬스 체크

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/health` | 시스템 상태 확인 |

### 11.2 SSE 스트리밍

```
data: {"type": "token", "content": "..."}\n\n       # 토큰 단위 텍스트
data: {"type": "progress", "node": "...", ...}\n\n   # 노드 진행 상태
data: {"type": "meta", "executed_sql": "..."}\n\n    # 메타 정보
data: {"type": "done", "query_id": "..."}\n\n        # 완료
data: {"type": "error", "message": "..."}\n\n        # 에러
```

### 11.3 응답 형식

```python
class QueryResponse:
    query_id: str
    response: str                    # 자연어 응답
    executed_sql: Optional[str]      # 실행된 SQL
    row_count: int
    execution_time_ms: float
    has_file: bool                   # 파일 다운로드 가능 여부
    thread_id: Optional[str]         # 대화 세션 ID
    awaiting_approval: bool          # 승인 대기 여부
    approval_context: Optional[dict] # 승인 컨텍스트
    turn_count: int                  # 대화 턴 수
    mapping_report: Optional[str]    # 매핑 보고서
```

---

## 12. Web UI

### 12.1 사용자 화면 (인증 없이 접근)

- **채팅 인터페이스**: 사용자/에이전트 메시지를 대화 형태로 표시
- **SSE 스트리밍**: 토큰 단위 실시간 응답 출력
- **프롬프트 입력**: 자연어 질의 텍스트 입력 (여러 줄)
- **파일 첨부**: Excel(.xlsx)/Word(.docx) 드래그앤드롭 또는 파일 선택
- **진행 상태 표시**: SSE 이벤트 기반 노드별 진행 인디케이터
- **결과 표시**: 자연어 텍스트 + 파일 다운로드 링크
- **폴백**: SSE 미지원 시 기존 POST /query로 자동 폴백

### 12.2 운영자 화면 (JWT 인증)

- **로그인**: ID/PW 입력 → JWT 토큰 발급
- **대시보드**: 환경변수 관리 + DB 연결 설정 + 스키마 캐시 관리
- **환경변수 설정**: .env 파일 설정값 목록 표시, 인라인 편집, 민감 값 마스킹
- **DB 연결 설정**: DB 타입/호스트/포트/DB명/사용자/비밀번호 입력, 연결 테스트
- **스키마 캐시 관리**: 캐시 상태 조회, 생성/갱신/삭제, 유사어 관리

---

## 13. 기능 요건 정리

### 13.1 핵심 기능 (구현 완료)

| ID | 기능 | 설명 | Phase |
|----|------|------|-------|
| F-01 | 자연어 질의 처리 | 한국어 자연어로 인프라 데이터 조회 | 1 |
| F-02 | DB 스키마 자동 분석 | MCP/직접연결로 테이블/컬럼 구조를 동적으로 파악 | 1 |
| F-03 | SQL 자동 생성 | 사용자 질의를 SQL로 변환 (DB 엔진별 문법 분기) | 1 |
| F-04 | SQL 검증 | 생성된 SQL의 안전성·문법·참조 컬럼 검증 | 1 |
| F-05 | 쿼리 실행 및 결과 수집 | MCP/직접연결로 SQL 실행, 결과 반환 | 1 |
| F-06 | 자연어 응답 생성 | 쿼리 결과를 LLM으로 사용자 친화적 자연어 정리 | 1 |
| F-07 | Excel 양식 처리 | .xlsx 양식 파싱 + 데이터 채우기 + 멀티시트 지원 | 2 |
| F-08 | Word 양식 처리 | .docx 양식 파싱 + 플레이스홀더/표 데이터 채우기 | 2 |
| F-09 | 에러 핸들링 및 재시도 | SQL 오류 시 자동 수정 후 재시도 (최대 3회) | 1 |
| F-10 | 시멘틱 라우팅 | LLM 기반 대상 DB 자동 선택, 멀티 DB 분류 | 1 |
| F-11 | 멀티 DB 지원 | 여러 DB에서 데이터를 통합 조회 (부분 실패 허용) | 1 |
| F-12 | 3단계 필드 매핑 | 양식 필드 → DB 컬럼 자동 매핑 (힌트→synonym→LLM) | 2 |
| F-13 | EAV 구조 지원 | EAV 테이블 자동 감지, 피벗 쿼리 생성, 필드 매핑 | 2 |
| F-14 | 스키마 캐시 | 메모리→Redis→파일→DB 4단계 캐시 + fingerprint 검증 | 1 |
| F-15 | 컬럼 설명/유사어 | LLM 생성 한국어 설명 + 유사 단어 → Redis 저장 | 1 |
| F-16 | Excel→CSV 변환 | LLM 컨텍스트 보강용 예시 데이터 추출 | 2 |
| F-17 | 멀티턴 대화 | 체크포인트 기반 대화 상태 유지, 맥락 복원 | 3 |
| F-18 | SQL 승인 (HITL) | 실행 전 사용자 SQL 확인/수정/거부 | 3 |
| F-19 | 유사어 등록 승인 | LLM 추론 매핑을 사용자 확인 후 Redis 등록 | 3 |
| F-20 | 감사 로그 | 모든 쿼리 실행 이력 JSONL 기록 | 1 |
| F-21 | 민감 데이터 마스킹 | 컬럼/패턴 기반 자동 마스킹 | 1 |
| F-22 | 매핑 보고서 | Markdown 형식 필드 매핑 결과 보고서 생성 | 2 |
| F-23 | SSE 스트리밍 | 실시간 응답 + 노드별 진행 상태 표시 | 4 |
| F-24 | 자체 MCP 서버 | Python FastMCP 기반 SSE 서버 (DB2/PostgreSQL 지원) | 1 |
| F-25 | 멀티 LLM 프로바이더 | Ollama / FabriX / Gemini 선택 가능 | 1 |
| F-26 | 캐시 관리 기능 | 대화형 캐시 CRUD + 운영자 API | 3 |

### 13.2 UI 기능

| ID | 기능 | 설명 | Phase |
|----|------|------|-------|
| F-30 | 채팅 인터페이스 | 대화형 질의/응답 UI | 4 |
| F-31 | 파일 첨부/다운로드 | Excel/Word 업로드 + 결과 파일 다운로드 | 4 |
| F-32 | 진행 상태 표시 | SSE 기반 노드별 진행 인디케이터 | 4 |
| F-33 | 운영자 로그인 | JWT 기반 관리자 인증 | 4 |
| F-34 | 환경변수 설정 UI | .env 설정값 조회/수정 (민감 값 마스킹) | 4 |
| F-35 | DB 연결 설정 UI | DB 연결 정보 입력/수정/테스트 | 4 |
| F-36 | 스키마 캐시 관리 UI | 캐시 상태/생성/갱신/삭제/유사어 관리 | 4 |

### 13.3 계획된 기능 (미구현)

| ID | 기능 | 설명 | 관련 계획 |
|----|------|------|----------|
| F-40 | 스키마 구조 분석 범용화 | 하드코딩 의존성 제거, LLM 범용 구조 분석 | Plan 27 |
| F-41 | 캐시 유효성/무효화 일관성 | 스키마 변경 시 descriptions/synonyms 자동 갱신 | Plan 30 |
| F-42 | EAV 수동 프로파일 설정 | attribute_column, value_joins 사용자 보정 | Plan 32 |
| F-43 | EAV 조인 지침 강제 적용 | excluded_join_columns 스키마 프롬프트 주입 | Plan 33 |
| F-44 | Polestar 전용 시스템 프롬프트 | .env 기반 DB별 프롬프트 분기 | Plan 34 |
| F-45 | 데이터 충분성 동적 계산 | mapping_sources 기반 확실도 차등화 | Plan 36 |
| F-46 | EAV 접두사 비교 정규화 | "EAV:" 접두사 정규화 + 부분 문자열 매칭 | Plan 37 |
| F-47 | Playwright E2E 테스트 | UI/API 자동화 테스트 프레임워크 | Plan 24 |
| F-48 | 스케줄 기반 리포트 | 정기적 양식 기반 리포트 자동 생성 | - |
| F-49 | 양식 템플릿 관리 | 자주 사용하는 양식 등록/관리 | - |
| F-50 | 쿼리 히스토리 | 이전 실행 쿼리 이력 관리 및 재실행 | - |

---

## 14. 비기능 요건

### 14.1 성능

| 항목 | 목표 |
|------|------|
| 단순 조회 응답 시간 | 10초 이내 |
| 복합 조회(JOIN, 집계) | 30초 이내 |
| 양식 파일 생성 | 60초 이내 |
| 동시 사용자 지원 | 최소 10명 |
| MCP 호출 타임아웃 | 60초 |
| SQL 최대 재시도 | 3회 |
| 최대 반환 행 수 | 10,000행 |

### 14.2 보안

- DB 접근은 읽기 전용으로 제한 (3중 방어)
- 생성된 SQL에 대한 다층 검증
- JWT 기반 관리자 인증
- 민감 데이터 마스킹 (컬럼/패턴 기반)
- 감사 로그: 모든 쿼리 실행 이력 기록
- MCP 서버 이중 SQL 가드

### 14.3 안정성

- LLM API 호출 실패 시 재시도 로직
- DB 연결 자동 재연결 (3회 시도)
- Redis 장애 시 파일 캐시 폴백
- 체크포인트를 통한 상태 복구
- 헬스 체크 (5초 타임아웃)
- 멀티 DB 부분 실패 허용

### 14.4 확장성

- 새 DB 소스 추가: MCP 서버 설정 + domain_config.py 추가
- 새 LLM 프로바이더: src/llm.py에 팩토리 분기 추가
- 새 DB 엔진: query_validator LIMIT 분기 + MCP 서버 드라이버 추가
- 새 양식 형식: output_generator 노드만 확장

---

## 15. 설정 구조

### 15.1 환경변수 체계

```python
AppConfig
  ├─ llm: LLMConfig              # LLM 프로바이더, 모델, API 키
  ├─ dbhub: DBHubConfig           # MCP 서버 URL, source_name, 타임아웃
  ├─ query: QueryConfig           # max_retries(3), default_limit, 데이터충분성 임계값
  ├─ security: SecurityConfig     # 민감 컬럼, 마스킹 패턴, IP/이메일 마스킹
  ├─ server: ServerConfig         # API 포트, CORS origins
  ├─ admin: AdminConfig           # 관리자 계정, JWT 시크릿
  ├─ multi_db: MultiDBConfig      # 활성 DB 목록 (CSV)
  ├─ redis: RedisConfig           # Redis 호스트/포트/SSL
  ├─ schema_cache: SchemaCacheConfig  # 백엔드(redis/file), TTL, 자동설명
  ├─ checkpoint_backend           # sqlite | memory
  ├─ enable_semantic_routing      # bool (자동 감지)
  ├─ enable_sql_approval          # bool (HITL)
  ├─ enable_structure_approval    # bool (HITL)
  └─ polestar_db_id               # Polestar 전용 프롬프트 대상 DB
```

### 15.2 주요 환경변수

| 변수 | 기본값 | 설명 |
|------|--------|------|
| `LLM_PROVIDER` | `ollama` | LLM 프로바이더 (ollama/fabrix/gemini) |
| `LLM_MODEL` | `llama3.1:8b` | 모델명 |
| `DBHUB_SERVER_URL` | `http://localhost:9090/sse` | MCP 서버 URL |
| `MULTI_DB_ACTIVE_DB_IDS_CSV` | - | 활성 DB 목록 (쉼표 구분) |
| `SCHEMA_CACHE_BACKEND` | `redis` | 캐시 백엔드 (redis/file) |
| `REDIS_HOST` | `localhost` | Redis 호스트 |
| `ENABLE_SEMANTIC_ROUTING` | (자동) | 시멘틱 라우팅 활성화 |
| `ENABLE_SQL_APPROVAL` | `false` | SQL 승인 HITL 활성화 |
| `ADMIN_USERNAME` | `admin` | 관리자 계정 |
| `ADMIN_PASSWORD` | `admin` | 관리자 비밀번호 |
| `SERVER_PORT` | `8000` | API 서버 포트 |

---

## 16. 개발 단계 및 진행 상태

### Phase 1: 자연어 → SQL 파이프라인 — **완료**

- LangGraph 그래프 기본 구조 (7노드 + 조건부 라우팅)
- MCP 서버 연동 + 직접 DB 연결
- SQL 생성 → 검증 → 실행 → 자연어 응답 파이프라인
- 에러 핸들링 (자동 재시도 3회)
- 시멘틱 라우팅 + 멀티 DB 지원
- 스키마 캐시 (메모리→Redis→파일→DB)
- LLM 컬럼 설명/유사어 생성
- 감사 로그 + 민감 데이터 마스킹
- 3중 읽기 전용 방어
- 멀티 LLM 프로바이더 (Ollama/FabriX/Gemini)

### Phase 2: 양식 기반 문서 생성 — **완료**

- Excel 양식 파싱 (헤더 탐지, 멀티시트, 병합 셀, 수식 보존)
- Word 양식 파싱 (플레이스홀더, 표 구조)
- 3단계 필드 매핑 (힌트→synonym→LLM 추론)
- EAV 구조 자동 감지 + 피벗 쿼리 + 필드 매핑
- Excel→CSV 변환으로 LLM 컨텍스트 보강
- 매핑 보고서 생성 (Markdown)
- LLM 추론 매핑 즉시 Redis 등록

### Phase 3: 멀티턴 대화 + 감사 + 승인 — **완료**

- 체크포인트 기반 멀티턴 대화
- context_resolver (대화 맥락 복원)
- SQL 승인 Human-in-the-loop (approve/reject/modify)
- 구조 분석 승인 HITL
- 유사어 등록 승인 워크플로우
- 캐시 관리 대화형 기능

### Phase 4: Web UI — **부분 구현**

- 채팅 인터페이스 + SSE 스트리밍 (**완료**)
- 파일 업로드/다운로드 (**완료**)
- 진행 상태 표시 (**완료**)
- 운영자 로그인 + JWT 인증 (**완료**)
- 환경변수/DB 설정 관리 (**완료**)
- 스키마 캐시 관리 API (**완료**)

---

## 17. 인프라 질의 예시

### 자연어 질의

| 질의 | 예상 동작 |
|------|----------|
| "전체 서버의 CPU 사용률 현황을 알려줘" | servers + cpu_metrics 조인, 서버별 최근 CPU 사용률 |
| "메모리 사용률이 80% 이상인 서버 목록" | memory_metrics 80% 이상 필터링 |
| "지난 일주일간 네트워크 트래픽 Top 10 서버" | network_metrics 최근 7일 집계 상위 10개 |
| "폴스타 DB에서 웹 서버의 OS 정보 조회" | Polestar EAV 피벗 쿼리 (CMM_RESOURCE + CORE_CONFIG_PROP) |
| "서버 A의 최근 한 달 리소스 사용 추이" | 서버명 필터 + CPU/메모리/디스크/NW 시계열 |
| "캐시 상태 확인해줘" | cache_management 라우팅, 캐시 현황 응답 |

### 양식 기반 출력

사용자가 Excel 양식 업로드 시:

```
| 서버명 | IP | CPU 코어 | CPU 사용률 | 메모리(GB) | OS종류 |
|--------|-----|---------|-----------|-----------|--------|
|        |     |         |           |           |        |
```

에이전트가:
1. 필드 매핑: "서버명"→`CMM_RESOURCE.RESOURCE_NAME`, "OS종류"→`EAV:OSType`
2. SQL 생성: SELECT + CASE WHEN 피벗 쿼리
3. 데이터 채우기: 양식에 결과 삽입
4. 매핑 보고서 첨부: 각 필드의 매핑 출처/신뢰도

---

## 18. Clean Architecture 계층 규칙

의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향해야 한다.

```
domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

| 계층 | 디렉토리 | 역할 |
|------|---------|------|
| domain | `src/state.py`, `src/dbhub/models.py` | 타입, 모델 |
| config | `src/config.py` | 설정 관리 |
| utils | `src/utils/` | 유틸리티 |
| prompts | `src/prompts/` | LLM 프롬프트 |
| infrastructure | `src/db/`, `src/dbhub/`, `src/schema_cache/`, `src/security/`, `src/clients/` | 외부 시스템 연동 |
| application | `src/nodes/`, `src/document/`, `src/routing/` | 비즈니스 로직 |
| orchestration | `src/graph.py`, `src/llm.py` | 그래프 빌드 |
| interface | `src/api/` | REST API |
| entry | `src/main.py`, `src/api/server.py` | 진입점 |

위반 검사: `python scripts/arch_check.py --ci`

---

## 19. 참고 자료

- LangGraph 공식 문서: https://langchain-ai.github.io/langgraph/
- FastMCP 문서: https://github.com/jlowin/fastmcp
- 의사결정 기록: `docs/decision.md`
- 구현 계획서: `plans/*.md`
