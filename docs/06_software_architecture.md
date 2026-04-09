# 02. 소프트웨어 아키텍처 (Software Architecture)

> 최종 갱신: 2026-04-02

---

## 1. Clean Architecture 계층 구조

프로젝트는 Clean Architecture 원칙을 따르며, 의존성은 안쪽(domain)에서 바깥쪽(entry)으로만 향한다.

```
┌─────────────────────────────────────────────────────────────────┐
│                      entry (src/main.py)                        │
│  서버 부트스트랩, uvicorn 실행                                    │
├─────────────────────────────────────────────────────────────────┤
│                   interface (src/api/)                           │
│  FastAPI routes, schemas, middleware, dependencies               │
├─────────────────────────────────────────────────────────────────┤
│                orchestration (src/graph.py)                      │
│  LangGraph 그래프 빌드, 노드 연결, 조건부 라우팅                    │
├─────────────────────────────────────────────────────────────────┤
│              application (src/nodes/, src/routing/)              │
│  각 노드 로직 (input_parser, query_generator, ...)              │
│  시멘틱 라우터, 필드 매퍼                                         │
├─────────────────────────────────────────────────────────────────┤
│          infrastructure (src/infrastructure/, src/db/)           │
│  DB 클라이언트, Repository 구현, Auth Provider                    │
│  src/schema_cache/, src/security/, src/clients/                  │
├─────────────────────────────────────────────────────────────────┤
│               prompts (src/prompts/)                             │
│  LLM 프롬프트 템플릿 (한국어 시스템 프롬프트)                       │
├─────────────────────────────────────────────────────────────────┤
│            config / utils (src/config.py, src/utils/)            │
│  설정 관리, 공통 유틸리티                                          │
├─────────────────────────────────────────────────────────────────┤
│                  domain (src/domain/, src/state.py)              │
│  AgentState, 도메인 엔티티 (User, AuditEvent, Auth)              │
│  비즈니스 규칙, 인터페이스 정의 (ABC)                               │
└─────────────────────────────────────────────────────────────────┘

의존 방향: domain → config/utils → prompts → infrastructure → application → orchestration → interface → entry
```

### 계층 위반 검사

```bash
python scripts/arch_check.py              # 위반 검사
python scripts/arch_check.py --verbose    # 의존성 매트릭스 포함
python scripts/arch_check.py --ci         # CI 모드 (위반 시 exit 1)
```

---

## 2. 디렉토리 구조와 책임

```
src/
├── main.py                  # 엔트리포인트 (uvicorn 실행)
├── config.py                # pydantic-settings 기반 설정 (AppConfig)
├── state.py                 # AgentState TypedDict (LangGraph 전역 상태)
├── graph.py                 # LangGraph 그래프 빌드 및 라우팅 로직
├── llm.py                   # LLM 팩토리 (Ollama/FabriX/Gemini)
│
├── api/                     # [interface 계층] FastAPI
│   ├── server.py            #   앱 생성, lifespan, 미들웨어
│   ├── schemas.py           #   Pydantic 요청/응답 모델
│   ├── dependencies.py      #   인증 디펜던시 (JWT 검증)
│   ├── middleware/           #   감사 미들웨어 (request_id)
│   └── routes/              #   API 엔드포인트
│       ├── query.py         #     질의 API (동기/SSE/파일 업로드)
│       ├── admin.py         #     관리자 API (설정/사용자/감사로그)
│       ├── conversation.py  #     대화 이력 API
│       ├── user_auth.py     #     사용자 인증 API
│       ├── admin_auth.py    #     관리자 인증 API
│       ├── health.py        #     헬스체크 API
│       └── schema_cache.py  #     스키마 캐시 API
│
├── nodes/                   # [application 계층] LangGraph 노드
│   ├── context_resolver.py  #   멀티턴 컨텍스트 추출
│   ├── input_parser.py      #   자연어 파싱 (LLM)
│   ├── field_mapper.py      #   3계층 필드 매핑
│   ├── schema_analyzer.py   #   DB 스키마 분석 (캐시 활용)
│   ├── query_generator.py   #   SQL 생성 (LLM)
│   ├── query_validator.py   #   SQL 검증 (규칙 기반)
│   ├── approval_gate.py     #   SQL 승인 게이트 (HITL)
│   ├── structure_approval_gate.py  # 구조분석 승인 (HITL)
│   ├── query_executor.py    #   SQL 실행 (MCP/Direct)
│   ├── multi_db_executor.py #   멀티 DB 병렬 실행
│   ├── result_merger.py     #   멀티 DB 결과 병합
│   ├── result_organizer.py  #   결과 정리/마스킹/매핑
│   ├── output_generator.py  #   응답 생성 (텍스트/문서)
│   ├── cache_management.py  #   캐시 관리 노드
│   └── synonym_registrar.py #   유사어 등록 노드
│
├── routing/                 # [application 계층] 시멘틱 라우팅
│   ├── semantic_router.py   #   LLM 기반 DB 라우팅
│   ├── domain_config.py     #   DB 도메인 정의
│   └── db_registry.py       #   DB 레지스트리
│
├── schema_cache/            # [infrastructure 계층] 스키마 캐시
│   ├── cache_manager.py     #   통합 캐시 매니저 (3-tier)
│   ├── redis_cache.py       #   Redis 캐시 구현
│   ├── persistent_cache.py  #   파일 캐시 구현
│   ├── fingerprint.py       #   스키마 변경 감지
│   ├── description_generator.py  # LLM 컬럼 설명 생성
│   └── synonym_loader.py    #   유사어 사전 로더
│
├── domain/                  # [domain 계층] 도메인 엔티티
│   ├── user.py              #   User 모델, 역할, ABC (UserRepository, AuditRepository)
│   ├── auth.py              #   AuthProvider ABC, AuthMethod 열거형
│   └── audit.py             #   AuditEvent 열거형, AuditLogEntry
│
├── infrastructure/          # [infrastructure 계층] 구현체
│   ├── user_repository.py   #   PostgresUserRepository (asyncpg)
│   ├── auth_provider.py     #   LocalAuthProvider (bcrypt 검증)
│   └── audit_repository.py  #   PostgresAuditRepository (asyncpg)
│
├── db/                      # [infrastructure 계층] DB 클라이언트
│   ├── interface.py         #   DBClient 프로토콜 (Protocol)
│   └── client.py            #   PostgresClient (asyncpg 직접 연결)
│
├── dbhub/                   # [infrastructure 계층] MCP 클라이언트
│   ├── client.py            #   DBHub MCP 클라이언트 (SSE)
│   └── models.py            #   데이터 모델 (QueryResult, SchemaInfo, TableInfo)
│
├── clients/                 # [infrastructure 계층] 외부 LLM 클라이언트
│   ├── ollama_client.py     #   Ollama API (OpenAI 호환)
│   ├── fabrix_client.py     #   FabriX API
│   └── fabrix_kbgenai.py    #   FabriX KBGenAI
│
├── document/                # [infrastructure 계층] 문서 처리
│   ├── excel_parser.py      #   Excel 파싱 (헤더/데이터 영역)
│   ├── excel_writer.py      #   Excel 데이터 채움 (서식 보존)
│   ├── excel_csv_converter.py  # Excel→CSV 변환 (LLM 컨텍스트)
│   ├── word_parser.py       #   Word {{placeholder}} 파싱
│   ├── word_writer.py       #   Word 데이터 채움 (스타일 보존)
│   ├── field_mapper.py      #   필드-컬럼 매핑 로직
│   └── mapping_report.py    #   매핑 보고서 생성
│
├── prompts/                 # [prompts 계층] LLM 프롬프트 템플릿
│   ├── input_parser.py      #   입력 파싱 프롬프트
│   ├── query_generator.py   #   SQL 생성 (범용 / Polestar 전용)
│   ├── output_generator.py  #   응답 생성 프롬프트
│   ├── semantic_router.py   #   시멘틱 라우팅 프롬프트
│   ├── field_mapper.py      #   필드 매핑 프롬프트
│   ├── structure_analyzer.py  # 구조 분석 (EAV 감지)
│   ├── schema_description.py  # 스키마 설명 생성
│   ├── cache_management.py  #   캐시 관리 의도 파싱
│   ├── column_resolver.py   #   컬럼 별칭 해석
│   └── result_organizer.py  #   결과 요약 프롬프트
│
├── security/                # [infrastructure 계층] 보안
│   ├── sql_guard.py         #   SQL 주입/DDL/DML 차단
│   ├── data_masker.py       #   민감 데이터 마스킹
│   ├── audit_logger.py      #   structlog 설정
│   └── audit_service.py     #   감사 서비스 (JSONL+DB 이중기록)
│
├── utils/                   # [config/utils 계층] 유틸리티
│   ├── json_extract.py      #   LLM 응답 JSON 추출
│   ├── column_matcher.py    #   컬럼 매칭 유틸
│   ├── schema_utils.py      #   스키마 변환 유틸
│   ├── retry.py             #   재시도 데코레이터
│   ├── password.py          #   bcrypt 해싱
│   └── sql_file_logger.py   #   SQL 파일 로깅
│
└── static/                  # 프론트엔드 (정적 파일)
    ├── index.html           #   사용자 질의 UI
    ├── login.html           #   로그인 화면
    ├── register.html        #   회원가입 화면
    ├── admin/               #   관리자 UI
    │   ├── dashboard.html   #     관리 대시보드
    │   └── login.html       #     관리자 로그인
    ├── css/style.css
    └── js/
        ├── app.js           #   사용자 UI 로직
        └── admin.js         #   관리자 UI 로직
```

---

## 3. 핵심 데이터 모델: AgentState

LangGraph의 모든 노드가 공유하는 전역 상태이다. `TypedDict`로 정의되며 (`src/state.py`), 각 노드는 자신이 담당하는 필드만 쓴다.

```
AgentState
│
├── 사용자 입력
│   ├── user_query: str                    # 자연어 질의
│   ├── uploaded_file: Optional[bytes]     # 업로드된 양식 파일
│   └── file_type: Optional[str]          # "xlsx" | "docx" | None
│
├── 파싱 결과
│   ├── parsed_requirements: dict          # 구조화된 요구사항
│   ├── template_structure: Optional[dict] # 양식 구조 정보
│   ├── target_sheets: Optional[list]      # 대상 시트 목록
│   └── csv_sheet_data: Optional[dict]     # Excel CSV 변환 데이터
│
├── 필드 매핑 (field_mapper 노드에서 생성)
│   ├── column_mapping: dict               # {필드명: "table.column"} 통합 매핑
│   ├── db_column_mapping: dict            # DB별 매핑 {db_id: {field: col}}
│   ├── mapping_sources: dict              # 매핑 출처 {field: "hint"|"synonym"|"llm_inferred"}
│   ├── mapped_db_ids: list                # 매핑에서 식별된 DB 목록
│   ├── pending_synonym_registrations: list # 유사어 등록 대기 항목
│   ├── llm_inference_details: list        # LLM 추론 상세 (confidence, reason)
│   └── mapping_report_md: str             # 매핑 보고서 Markdown
│
├── DB / 스키마
│   ├── relevant_tables: list[str]         # 관련 테이블
│   ├── schema_info: dict                  # 스키마 상세 (테이블, 컬럼, FK)
│   ├── column_descriptions: dict          # 컬럼 설명 {table.column: desc}
│   ├── column_synonyms: dict              # 유사단어 {table.column: [word, ...]}
│   ├── resource_type_synonyms: dict       # RESOURCE_TYPE 값 유사단어 (EAV)
│   ├── eav_name_synonyms: dict            # EAV NAME 값 유사단어
│   └── active_db_engine: Optional[str]    # DB 엔진 타입 ("db2", "postgresql", ...)
│
├── SQL 처리
│   ├── generated_sql: str                 # 현재 SQL 쿼리
│   ├── validation_result: ValidationResult # {passed, reason, auto_fixed_sql}
│   ├── query_results: list[dict]          # 쿼리 실행 결과
│   └── query_attempts: list[QueryAttempt] # 실행 시도 이력 (디버깅/감사)
│
├── 시멘틱 라우팅
│   ├── routing_intent: Optional[str]      # "data_query" | "cache_management"
│   ├── target_databases: list[dict]       # 라우팅된 대상 DB 목록
│   ├── active_db_id: Optional[str]        # 현재 처리 중인 DB
│   ├── is_multi_db: bool                  # 멀티 DB 쿼리 여부
│   ├── user_specified_db: Optional[str]   # 사용자 직접 지정 DB
│   ├── db_results: dict                   # DB별 결과 {db_id: rows}
│   ├── db_schemas: dict                   # DB별 스키마 {db_id: schema}
│   └── db_errors: dict                    # DB별 에러 {db_id: msg}
│
├── 가공 결과
│   └── organized_data: OrganizedData      # {summary, rows, column_mapping,
│                                          #  resolved_mapping, is_sufficient,
│                                          #  sheet_mappings}
│
├── 대화 상태 (Phase 3)
│   ├── messages: list[BaseMessage]        # 대화 히스토리 (add_messages reducer)
│   ├── thread_id: Optional[str]           # 세션 식별자
│   ├── conversation_context: Optional[dict] # context_resolver 추출 맥락
│   ├── awaiting_approval: bool            # 승인 대기 여부
│   ├── approval_context: Optional[dict]   # 승인 요청 컨텍스트
│   ├── approval_action: Optional[str]     # "approve"|"reject"|"modify"
│   └── approval_modified_sql: Optional[str] # 수정된 SQL
│
├── 사용자 / 보안
│   ├── user_id: Optional[str]             # "anonymous" 또는 실제 user_id
│   ├── user_department: Optional[str]
│   ├── allowed_db_ids: Optional[list]     # None=전체 허용
│   ├── request_id: Optional[str]          # 요청 추적 ID (미들웨어 주입)
│   ├── client_ip: Optional[str]           # 클라이언트 IP
│   └── accessed_tables: list[str]         # 실제 접근한 테이블
│
├── 제어
│   ├── retry_count: int                   # 재시도 횟수 (최대 3)
│   ├── error_message: Optional[str]       # 에러 메시지 (재시도 시 참조)
│   └── current_node: str                  # 현재 실행 중인 노드
│
└── 출력
    ├── final_response: str                # 자연어 응답
    ├── output_file: Optional[bytes]       # 생성된 파일 바이너리
    └── output_file_name: Optional[str]    # 출력 파일명
```

---

## 4. API 아키텍처

### 4.1 엔드포인트 구성

```
/api/v1/
├── /health                          GET    헬스체크
│
├── /query                           POST   동기 질의
├── /query/stream                    POST   SSE 스트리밍 질의
├── /query/file                      POST   파일 업로드 질의
├── /query/{id}/result               GET    결과 조회
├── /query/{id}/download             GET    파일 다운로드
├── /query/{id}/download-csv         GET    CSV 다운로드
├── /query/{id}/mapping-report       GET    매핑 보고서
├── /query/mapping-feedback          POST   매핑 피드백
│
├── /conversation/{thread_id}        GET    대화 이력 조회
│
├── /auth/                                  사용자 인증
│   ├── register                     POST   회원가입
│   ├── login                        POST   로그인
│   ├── me                           GET    내 정보
│   ├── change-password              POST   비밀번호 변경
│   ├── status                       GET    인증 상태
│   └── logout                       POST   로그아웃
│
├── /admin/                                 관리자
│   ├── auth/login                   POST   관리자 로그인
│   ├── settings                     GET/PUT 시스템 설정
│   ├── db-config                    GET/PUT DB 설정
│   ├── db-config/test               POST   DB 연결 테스트
│   ├── users                        GET    사용자 목록
│   ├── users/{id}                   PUT/DEL 사용자 관리
│   ├── users/{id}/permissions       PUT    권한 설정
│   ├── audit-logs                   GET    감사 로그
│   ├── audit/logs                   GET    감사 로그 (페이징)
│   ├── audit/stats                  GET    감사 통계
│   ├── audit/users/{id}/activity    GET    사용자 활동
│   └── audit/alerts                 GET    보안 알림
│
└── /schema-cache/                          캐시 관리
    ├── status                       GET    캐시 상태
    └── invalidate                   POST   캐시 무효화
```

### 4.2 앱 라이프사이클

```
서버 시작 (lifespan)
    │
    ├── 1. 로깅 설정 (structlog)
    ├── 2. SQL 파일 로거 초기화
    ├── 3. 체크포인터 생성 (AsyncSqliteSaver)
    ├── 4. LangGraph 그래프 빌드 (build_graph)
    ├── 5. 인증 DB 풀 초기화 (asyncpg)
    │      ├── UserRepository, AuditRepository 생성
    │      ├── AuditService 생성
    │      ├── AuthProvider 생성
    │      └── DDL 자동 실행 (auth_users, audit_logs 테이블)
    ├── 6. Redis 스키마 캐시 연결
    │
    ▼ (yield — 서버 운영)
    │
    ├── 7. 인증 DB 풀 정리
    ├── 8. 체크포인터 연결 정리
    └── 9. Redis 연결 해제
```

---

## 5. 보안 아키텍처

### 5.1 3계층 읽기 전용 방어

```
┌─────────────────────────────────────────────────────────────┐
│ Layer 1: SQL Guard (src/security/sql_guard.py)              │
│                                                             │
│   - INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE 차단            │
│   - SQL 주입 패턴 감지 (UNION, --, /*, xp_, exec 등)         │
│   - SELECT-only 강제                                        │
├─────────────────────────────────────────────────────────────┤
│ Layer 2: Query Validator (src/nodes/query_validator.py)      │
│                                                             │
│   - sqlparse 기반 구문 분석                                   │
│   - 테이블/컬럼 존재 확인                                     │
│   - 금지 JOIN 컬럼 검사 (EAV resource_conf_id 등)             │
│   - LIMIT 강제 (최대 10,000행)                               │
│   - 성능 위험 경고                                           │
├─────────────────────────────────────────────────────────────┤
│ Layer 3: DB Level (MCP Server / Connection)                 │
│                                                             │
│   - DB 계정 자체가 READ-ONLY 권한                             │
│   - MCP 서버에서 query_timeout, max_rows 강제                 │
│   - 네트워크 레벨 접근 제어                                    │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 인증/인가 아키텍처

```
┌──────────┐     JWT Token        ┌──────────────────────┐
│  Client  │ ────────────────►   │  dependencies.py     │
│          │                      │  get_current_user()  │
│          │                      │  require_user()      │
└──────────┘                      └──────────┬───────────┘
                                             │
                      ┌──────────────────────┼──────────────────────┐
                      │                      │                      │
                      ▼                      ▼                      ▼
            AUTH_ENABLED=true       AUTH_ENABLED=false       Admin Auth
                      │                      │                      │
            ┌─────────▼──────────┐  ┌────────▼───────────┐  ┌──────▼──────┐
            │ JWT 검증           │  │ ANONYMOUS_USER     │  │ admin JWT   │
            │ → UserRepository  │  │ (인증 없이 사용)     │  │ (별도 시크릿) │
            │ → User 객체 반환   │  │                    │  │             │
            └────────────────────┘  └────────────────────┘  └─────────────┘

User 모델 (src/domain/user.py):
  - user_id, username
  - role: "user" | "admin"
  - status: "active" | "inactive" | "locked"
  - department, allowed_db_ids (DB 접근 제어)
  - bcrypt hashed password
  - login_fail_count → max_login_attempts 초과 시 계정 잠금
```

### 5.3 감사 로깅 아키텍처

```
AuditService (src/security/audit_service.py)
    │
    │  이중 기록
    │
    ├──► JSONL File (logs/audit.jsonl)
    │    - 빠른 기록, 로컬 보관
    │    - structlog 기반
    │
    └──► PostgreSQL (audit_logs 테이블)
         - 영구 보관, 검색/통계 지원
         - 자동 보관 정책 (retention_days: 90)

감사 이벤트 15종 (src/domain/audit.py):
  LOGIN_SUCCESS, LOGIN_FAILURE, LOGOUT
  USER_REQUEST, QUERY_EXECUTION, DATA_ACCESS
  FILE_DOWNLOAD, SETTINGS_CHANGE
  USER_CREATE, USER_UPDATE, USER_DELETE, PASSWORD_CHANGE
  PERMISSION_CHANGE, SECURITY_ALERT
  SENSITIVE_DATA_ACCESS

보안 알림 조건:
  - 로그인 실패 반복 (alert_on_failed_login: 5회)
  - 대량 데이터 조회 (alert_on_large_result > 5,000행)
  - 심야 접근 (2:00~6:00)
  - 민감 테이블 접근 (sensitive_tables 설정)
```

### 5.4 데이터 마스킹

```
DataMasker (src/security/data_masker.py)
    │
    │  쿼리 결과에서 민감 컬럼 자동 마스킹
    │
    ├── 대상 컬럼 (SecurityConfig.sensitive_columns):
    │   password, passwd, secret, token, api_key,
    │   private_key, credential, ssn, credit_card, pin ...
    │
    ├── 전체 마스킹: "***MASKED***"
    └── 부분 마스킹: partial_mask_columns (앞 3자 보존)
```

---

## 6. LangGraph 그래프 구조

그래프는 `src/graph.py`의 `build_graph()` 함수에서 빌드된다. 설정에 따라 노드와 엣지가 동적으로 구성된다.

### 6.1 노드 목록 (16개)

| 노드 | 파일 | LLM 사용 | 역할 |
|------|------|---------|------|
| context_resolver | nodes/context_resolver.py | - | 멀티턴 컨텍스트 추출 |
| input_parser | nodes/input_parser.py | O | 자연어 → 구조화 요구사항 |
| field_mapper | nodes/field_mapper.py | O | 3계층 필드-컬럼 매핑 |
| semantic_router | routing/semantic_router.py | O | LLM 기반 DB 라우팅 |
| schema_analyzer | nodes/schema_analyzer.py | O | DB 스키마 분석/캐시 |
| query_generator | nodes/query_generator.py | O | SQL SELECT 생성 |
| query_validator | nodes/query_validator.py | - | 규칙 기반 SQL 검증 |
| approval_gate | nodes/approval_gate.py | - | SQL 승인 (HITL, 선택) |
| structure_approval_gate | nodes/structure_approval_gate.py | - | 구조분석 승인 (HITL, 선택) |
| query_executor | nodes/query_executor.py | - | SQL 실행 (MCP/Direct) |
| multi_db_executor | nodes/multi_db_executor.py | O | 멀티 DB 파이프라인 |
| result_merger | nodes/result_merger.py | - | 멀티 DB 결과 병합 |
| result_organizer | nodes/result_organizer.py | O | 결과 정리/마스킹/충분성 |
| output_generator | nodes/output_generator.py | O | 응답/문서 생성 |
| cache_management | nodes/cache_management.py | O | 캐시/유사어 관리 |
| synonym_registrar | nodes/synonym_registrar.py | - | Redis 유사어 등록 |
| error_response | graph.py (inline) | - | 에러 응답 생성 |

### 6.2 조건부 라우팅 함수

| 함수 | 위치 | 분기 조건 |
|------|------|----------|
| route_after_semantic_router | semantic_router 이후 | intent별 (cache/synonym/multi_db/single) |
| route_after_schema_analyzer | schema_analyzer 이후 | 구조 승인 대기 여부 |
| route_after_structure_approval | structure_approval_gate 이후 | approve → schema_analyzer, reject → query_generator |
| route_after_validation | query_validator 이후 | passed/실패+재시도/실패+초과 |
| route_after_validation_with_approval | query_validator 이후 (승인 활성) | passed → approval_gate |
| route_after_approval | approval_gate 이후 | approve/reject/modify |
| route_after_execution | query_executor 이후 | 정상/에러+재시도/에러+초과 |
| route_after_organization | result_organizer 이후 | 충분/부족+재시도 |

### 6.3 설정별 그래프 변형

| 설정 | 기본값 | 효과 |
|------|--------|------|
| `enable_semantic_routing` | 자동 (멀티DB 있으면 true) | semantic_router, multi_db_executor, cache_management, synonym_registrar 노드 활성화 |
| `enable_sql_approval` | false | approval_gate 노드 및 interrupt_before 활성화 |
| `enable_structure_approval` | true | structure_approval_gate 노드 및 interrupt_before 활성화 |

---

> 관련 문서: [01. 시스템 아키텍처](01_system_architecture.md) | [03. 처리 프로세스](03_processing_flow.md)
