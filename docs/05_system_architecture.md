# 01. 시스템 아키텍처 (System Architecture)

> 최종 갱신: 2026-04-02

---

## 1. 프로젝트 개요

**인프라 데이터 조회 에이전트(Infrastructure Data Query Agent)** 는 사용자의 자연어 질의(한국어)를 SQL로 변환하고, 인프라 DB에서 데이터를 조회한 뒤, 자연어 응답 또는 Excel/Word 문서로 결과를 반환하는 AI 에이전트 시스템이다.

### 핵심 기능

- 자연어(한국어) → SQL 변환 및 실행
- 멀티 DB 시멘틱 라우팅 (Polestar, Cloud Portal, ITSM, ITAM)
- Excel/Word 양식 업로드 → 자동 데이터 채움
- 멀티턴 대화 및 Human-in-the-Loop (SQL/구조 승인)
- 4계층 스키마 캐시 (Memory → Redis → File → DB)
- 3계층 읽기 전용 보안 방어

---

## 2. 전체 시스템 구성도

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           사용자 (Web Browser)                           │
│                                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │  index.html  │  │  login.html  │  │ admin/       │                   │
│  │  (질의 UI)   │  │  (로그인)    │  │ dashboard    │                   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘                   │
│         │ SSE/REST         │ REST            │ REST                      │
└─────────┼──────────────────┼────────────────┼───────────────────────────┘
          │                  │                │
          ▼                  ▼                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    FastAPI 서버 (uvicorn, port 8000)                      │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     API Layer (src/api/)                        │    │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌────────┐ ┌──────────┐  │    │
│  │  │ /query  │ │ /user   │ │ /admin  │ │/health │ │/conversa-│  │    │
│  │  │         │ │  /auth  │ │         │ │        │ │ tion     │  │    │
│  │  └────┬────┘ └────┬────┘ └────┬────┘ └────────┘ └──────────┘  │    │
│  │       │           │           │                                │    │
│  │  ┌────┴───────────┴───────────┴──────────────────────────┐     │    │
│  │  │  Middleware: CORS, AuditMiddleware (request_id, IP)   │     │    │
│  │  └───────────────────────────────────────────────────────┘     │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │              LangGraph State Machine (src/graph.py)             │    │
│  │                                                                 │    │
│  │  context_resolver → input_parser → field_mapper                 │    │
│  │        → semantic_router → schema_analyzer                      │    │
│  │        → query_generator ↔ query_validator                      │    │
│  │        → [approval_gate] → query_executor                       │    │
│  │        → result_organizer → output_generator                    │    │
│  │                                                                 │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                         │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────────────────┐  │
│  │  LLM Client  │  │ Schema Cache  │  │   Security                  │  │
│  │  (Ollama/    │  │ (Memory+Redis │  │   (SQL Guard, Data Masker,  │  │
│  │   FabriX/   │  │  +File)       │  │    Audit Logger)            │  │
│  │   Gemini)   │  │               │  │                             │  │
│  └──────┬───────┘  └──────┬────────┘  └─────────────────────────────┘  │
└─────────┼──────────────────┼────────────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌──────────────────┐  ┌───────────────────────────────────────────────────┐
│  LLM Server      │  │              데이터 계층                           │
│                  │  │                                                   │
│  - Ollama        │  │  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│    (llama3.1)    │  │  │ MCP      │  │ Direct   │  │ PostgreSQL     │  │
│  - FabriX       │  │  │ Server   │  │ asyncpg  │  │ (Auth+Audit)   │  │
│    (KBGenAI)    │  │  │ (DBHub)  │  │          │  │                │  │
│  - Gemini       │  │  └────┬─────┘  └────┬─────┘  └────────────────┘  │
│                  │  │       │             │                             │
│                  │  │       ▼             ▼                             │
│                  │  │  ┌──────────────────────────────────────────┐     │
│                  │  │  │  Infrastructure DBs                     │     │
│                  │  │  │  Polestar(DB2) | CloudPortal(PG)        │     │
│                  │  │  │  ITSM(PG)     | ITAM(PG)               │     │
│                  │  │  └──────────────────────────────────────────┘     │
│                  │  │                                                   │
│                  │  │  ┌──────────┐  ┌──────────────────┐              │
│                  │  │  │  Redis   │  │  SQLite           │              │
│                  │  │  │  (Cache) │  │  (Checkpoints)    │              │
│                  │  │  └──────────┘  └──────────────────┘              │
└──────────────────┘  └───────────────────────────────────────────────────┘
```

---

## 3. 인프라 구성 요소

| 구성 요소 | 기술 | 용도 |
|-----------|------|------|
| **API Server** | FastAPI + uvicorn | REST/SSE API, 정적 파일 서빙 |
| **LLM** | Ollama / FabriX / Gemini | 자연어 처리, SQL 생성, 시멘틱 라우팅 |
| **MCP Server** | 자체 구축 (DBHub 프로토콜) | DB 스키마 조회 및 SQL 실행 (SSE 전송) |
| **Redis** | Redis 6+ | 스키마 캐시, 유사어 사전, DB 설명 |
| **PostgreSQL** | asyncpg | 사용자 인증, 감사 로그 저장 |
| **SQLite** | aiosqlite | LangGraph 체크포인트 (대화 상태 저장) |
| **Infrastructure DBs** | DB2, PostgreSQL 등 | 실제 인프라 데이터 소스 (읽기 전용) |

### 3.1 LLM 프로바이더

```
┌──────────────────────────────────────────────────────────────┐
│                     LLM Factory (src/llm.py)                 │
│                                                              │
│  LLM_PROVIDER 환경변수에 따라 프로바이더 선택                    │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │   Ollama     │  │   FabriX     │  │   Gemini     │        │
│  │              │  │              │  │              │        │
│  │ LLMAPIClient │  │ FabriXAPI /  │  │ ChatGoogle   │        │
│  │ (OpenAI호환) │  │ KBGenAIChat  │  │ GenerativeAI │        │
│  │              │  │              │  │              │        │
│  │ llama3.1:8b  │  │ SDS 전용     │  │ gemini-pro   │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│                                                              │
│  공통 인터페이스: langchain_core.BaseChatModel                 │
│  temperature: 0.0 (결정적 출력)                                │
└──────────────────────────────────────────────────────────────┘
```

### 3.2 DB 접근 경로

시스템은 두 가지 DB 접근 방식을 지원한다. `db_backend` 설정으로 전환한다.

```
┌───────────────────────────────────────────────────────────────┐
│                    DB 접근 아키텍처                             │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ 방식 1: MCP Server (db_backend="dbhub")                  │ │
│  │                                                          │ │
│  │  Agent ──SSE──► MCP Server ──► Infrastructure DB         │ │
│  │                 (mcp_server/)                             │ │
│  │                                                          │ │
│  │  - MCP 프로토콜 기반 (SSE 전송)                            │ │
│  │  - search_objects, get_table_schema, execute_sql          │ │
│  │  - 서버 측에서 query_timeout, max_rows 강제               │ │
│  │  - 멀티 DB 소스 관리 (list_sources)                       │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  ┌──────────────────────────────────────────────────────────┐ │
│  │ 방식 2: Direct Connection (db_backend="direct")          │ │
│  │                                                          │ │
│  │  Agent ──asyncpg──► PostgreSQL                           │ │
│  │         (src/db/client.py)                               │ │
│  │                                                          │ │
│  │  - asyncpg 직접 연결                                      │ │
│  │  - DBClient 프로토콜 (src/db/interface.py) 준수           │ │
│  │  - 단일 DB 환경에서 사용                                   │ │
│  └──────────────────────────────────────────────────────────┘ │
│                                                               │
│  공통 인터페이스: DBClient (Protocol)                           │
│  - connect(), disconnect(), health_check()                    │
│  - search_objects(), get_table_schema(), get_full_schema()    │
│  - get_sample_data(), execute_sql()                           │
└───────────────────────────────────────────────────────────────┘
```

### 3.3 멀티 DB 도메인

시멘틱 라우팅으로 사용자 질의를 적절한 DB로 라우팅한다.

| DB ID | 표시명 | 엔진 | 데이터 도메인 |
|-------|--------|------|-------------|
| `polestar` | Polestar DB | DB2 | 서버 물리 사양, CPU/Memory/Disk 사용량, 프로세스 |
| `cloud_portal` | Cloud Portal DB | PostgreSQL | VM 정보, 데이터스토어, 영역별 VM 대수 |
| `itsm` | ITSM DB | PostgreSQL | 서비스 요청, 인시던트, 변경/문제 관리, SLA |
| `itam` | ITAM DB | PostgreSQL | IT 자산 목록, 라이프사이클, 라이선스, 하드웨어 |

---

## 4. 스키마 캐시 아키텍처

```
┌───────────────────────────────────────────────────────────────┐
│                   SchemaCacheManager                          │
│                                                               │
│  조회 요청                                                     │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────┐                                       │
│  │ 1차: Memory Cache  │ TTL 5분, 프로세스 내 캐시               │
│  │  SchemaMemoryCache │ dict 기반, 즉시 반환                   │
│  └────────┬───────────┘                                       │
│      miss │                                                   │
│      ▼                                                        │
│  ┌────────────────────┐                                       │
│  │ 2차: Redis Cache   │ Fingerprint 기반 유효성 검증            │
│  │  RedisSchemaCache  │ 컬럼 설명, 유사어, DB 설명 포함         │
│  │                    │ TTL 30분 (fingerprint 재검증 주기)      │
│  └────────┬───────────┘                                       │
│   miss/err│                                                   │
│      ▼                                                        │
│  ┌────────────────────┐                                       │
│  │ 2차-fb: File Cache │ Redis 장애 시 폴백                     │
│  │ PersistentSchema   │ .cache/schema/ 디렉토리                │
│  │  Cache (JSON)      │                                       │
│  └────────┬───────────┘                                       │
│      miss │                                                   │
│      ▼                                                        │
│  ┌────────────────────┐                                       │
│  │ 3차: DB Full Scan  │ MCP 또는 Direct 연결                   │
│  │  search_objects    │ → 전체 스키마 조회                      │
│  │  get_table_schema  │ → 모든 캐시에 저장                      │
│  └────────────────────┘                                       │
│                                                               │
│  부가 데이터 (Redis에 저장):                                     │
│  ├── 컬럼 설명 (LLM 생성)                                     │
│  ├── 컬럼 유사어 (LLM 생성 + 사용자 등록)                       │
│  ├── 글로벌 유사어 (전체 DB 공통)                               │
│  ├── DB 설명 (라우팅 보강용)                                    │
│  └── 구조 메타 (EAV/계층 분석 결과)                              │
│                                                               │
│  Fingerprint 방식:                                             │
│  - DB에서 테이블/컬럼 목록의 해시값 계산                          │
│  - 캐시된 fingerprint와 비교                                    │
│  - 불일치 시 캐시 무효화 후 재조회                                │
│  - TTL(30분)마다 자동 재검증                                     │
└───────────────────────────────────────────────────────────────┘
```

---

## 5. 설정 아키텍처

모든 설정은 `pydantic-settings` 기반으로 `.env` 파일과 환경변수에서 로드하며, `AppConfig` 싱글톤으로 관리한다.

```
AppConfig (pydantic-settings, 싱글톤)
│
├── llm: LLMConfig                    # LLM 프로바이더 설정
│   ├── provider: "ollama"|"fabrix"|"gemini"
│   ├── model: str
│   ├── ollama_base_url, ollama_api_key, ollama_timeout
│   ├── gemini_api_key, gemini_model
│   └── fabrix_base_url, fabrix_api_key, fabrix_client_key
│
├── dbhub: DBHubConfig                # MCP 서버 접속
│   ├── server_url (SSE 엔드포인트)
│   ├── source_name (기본 쿼리 대상)
│   └── mcp_call_timeout: 60
│
├── query: QueryConfig                # 쿼리 정책
│   ├── max_retry_count: 3
│   ├── default_limit: 1000
│   └── sufficiency thresholds (0.7 / 0.5)
│
├── security: SecurityConfig          # 보안
│   ├── sensitive_columns (마스킹 대상)
│   └── mask_pattern: "***MASKED***"
│
├── server: ServerConfig              # API 서버
│   ├── host: "0.0.0.0", port: 8000
│   ├── cors_origins
│   └── query_timeout: 60, file_query_timeout: 120
│
├── admin: AdminConfig                # 관리자 인증
│   └── jwt_secret, jwt_expire_hours: 24
│
├── auth: AuthConfig                  # 사용자 인증
│   ├── enabled: bool (기본 false)
│   ├── auth_db_url, jwt_expire_hours: 8
│   ├── max_login_attempts: 5, lockout_minutes: 30
│   └── password_min_length: 8
│
├── multi_db: MultiDBConfig           # 멀티 DB
│   └── active_db_ids_csv (쉼표 구분)
│
├── redis: RedisConfig                # Redis
│   └── host, port, db, password, ssl
│
├── schema_cache: SchemaCacheConfig   # 스키마 캐시
│   ├── backend: "redis"|"file"
│   ├── auto_generate_descriptions: true
│   └── fingerprint_ttl_seconds: 1800
│
├── audit: AuditConfig                # 감사 로그
│   ├── jsonl_enabled, db_enabled
│   ├── retention_days: 90
│   └── alert_on_failed_login: 5, alert_on_large_result: 5000
│
├── checkpoint_backend: "sqlite"|"postgres"
├── db_backend: "dbhub"|"direct"
├── enable_semantic_routing: bool (자동 판단)
├── enable_sql_approval: bool
├── enable_structure_approval: bool
├── polestar_db_id: str
├── conversation_max_turns: 20
└── conversation_ttl_hours: 24
```

---

## 6. 개발 단계 및 현재 상태

| Phase | 내용 | 상태 |
|-------|------|------|
| **Phase 1** | NL→SQL 파이프라인 (LangGraph, MCP, 에러핸들링) | 완료 |
| **Phase 2** | Excel/Word 양식 처리, 필드 매핑, EAV 지원 | 완료 |
| **Phase 3** | 멀티턴 대화, HITL 승인, 유사어 관리 | 완료 |
| **Phase 4** | 사용자 인증, 감사 로깅, 권한 관리 | 진행중 |

### 추가 기능 (Phase 간 확장)

- 시멘틱 라우팅 (멀티 DB) — Plan 09, 완료
- 스키마 캐시 (Redis, Fingerprint) — Plans 26~30, 완료
- Gemini LLM 지원 — Plan 28, 완료
- Polestar 전용 프롬프트 — Plan 34, 완료
- 3계층 필드 매핑 (hint+synonym+LLM) — Plan 38, 완료
- 사용자 인증 (JWT, bcrypt) — Plan 39, 진행중
- 감사 로깅 강화 — Plan 40, 진행중
- 프롬프트 접근 제어 — Plan 41, 계획

---

## 7. 멀티에이전트 빌드 시스템

프로젝트는 Claude Agent SDK 기반의 멀티에이전트 시스템으로 개발한다.

```
┌──────────────────────────────────────────────────────┐
│                    team-lead                          │
│              (오케스트레이터/메인 에이전트)               │
│                                                      │
│  Phase별 산출물 검토·승인 후 다음 Phase로 진행          │
└──────────┬───────┬───────┬───────┬───────────────────┘
           │       │       │       │
     Phase1│ Phase2│ Phase3│ Phase4│
           ▼       ▼       ▼       ▼
┌──────────┐ ┌────────────┐ ┌──────────┐ ┌──────────┐
│require-  │ │research-   │ │implemen- │ │verifier  │
│ments-    │ │planner     │ │ter       │ │          │
│analyst   │ │            │ │          │ │          │
├──────────┤ ├────────────┤ ├──────────┤ ├──────────┤
│산출물:    │ │산출물:      │ │산출물:    │ │산출물:    │
│docs/     │ │plans/*.md  │ │src/      │ │tests/    │
│require-  │ │(영역별     │ │pyproject │ │docs/veri-│
│ments.md  │ │구현 계획서) │ │.toml     │ │fication  │
└──────────┘ └────────────┘ └──────────┘ │_report.md│
                                         └──────────┘
```

---

## 8. 기술 스택 요약

| 영역 | 기술 | 버전/설정 |
|------|------|----------|
| 에이전트 프레임워크 | LangGraph | ≥0.2.0 |
| LLM 통합 | langchain-core | - |
| LLM 프로바이더 | Ollama, FabriX, Gemini | 설정으로 전환 |
| API 서버 | FastAPI + uvicorn | port 8000 |
| DB 접근 (MCP) | 자체 MCP Server (SSE) | - |
| DB 접근 (Direct) | asyncpg | PostgreSQL |
| 캐시 | Redis | 스키마/유사어/설명 |
| 체크포인트 | SQLite (aiosqlite) | 대화 상태 |
| 인증 DB | PostgreSQL (asyncpg) | 사용자/감사 |
| 문서 처리 | openpyxl, python-docx | Excel, Word |
| SQL 파싱 | sqlparse | 검증용 |
| 보안 | bcrypt, PyJWT | 인증/해싱 |
| 로깅 | structlog | JSON 구조화 |
| 설정 관리 | pydantic-settings | .env 기반 |
| 테스트 | pytest, pytest-asyncio | 190+ 테스트 |
| 아키텍처 검증 | scripts/arch_check.py | Clean Architecture |

---

> 관련 문서: [02. 소프트웨어 아키텍처](02_software_architecture.md) | [03. 처리 프로세스](03_processing_flow.md)
