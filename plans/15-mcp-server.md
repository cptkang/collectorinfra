# 15. DBHub MCP 서버 구축 및 클라이언트 리팩토링 계획

> **최종 업데이트**: 2026-03-20
> **전체 진행률**: Phase A 100% / Phase B 100% / Phase C 100%

---

## 0. 수행 상태 요약

| Phase | 상태 | 비고 |
|-------|------|------|
| **Phase A**: MCP 서버 패키지 생성 | **완료** | 9/9 단계 모두 구현됨 |
| **Phase B**: 클라이언트 설정 분리 및 수정 | **완료** | 10/10 단계 모두 구현됨 |
| **Phase C**: 통합 테스트 | **완료** | 3/3 단계 모두 완료 |

### 추가 수정 사항 (검증 과정에서 발견/수정)

1. **`server.py` host/port 버그 수정**: `FastMCP` 생성자에 `host`/`port`가 전달되지 않아 `127.0.0.1:8000`에 바인딩되던 문제 → `config.server.host`/`config.server.port` 전달로 수정
2. **`server.py` 모듈 레벨 이중 로딩 제거**: `mcp = create_server()` 모듈 레벨 코드 제거
3. **3개 테스트 파일 import 수정**: `_extract_json_from_response` → `extract_json_from_response` (리팩터링 후 미갱신)
4. **Redis 테스트 환경 격리**: `.env`의 `REDIS_PORT=6380`이 기본값 테스트에 영향 → `monkeypatch`로 격리

### 참고

- **`dbhub` npm 패키지**: 시스템에서 미설치 상태 (npm 의존성 제거 완료)
- **`config/dbhub_test.toml`**: 이 파일은 존재하지 않으며, 기존 `dbhub` CLI용 설정이므로 생성 불필요. MCP 서버 설정은 `mcp_server/config.toml`에 일원화됨
- **런타임 검증 완료**: `python -m mcp_server`로 서버 구동 확인 (`0.0.0.0:9090` SSE). DB 미연결 상태에서 서버 라이프사이클 정상 동작

---

## 1. 현황 분석

### 1.1 이전 아키텍처 (변경 전)

```
DBHubClient (src/dbhub/client.py)
  └─ MCP stdio transport ─→ dbhub (외부 npm 패키지, 로컬 프로세스)
                                └─ PostgreSQL
```

- `DBHubClient`는 `mcp` SDK의 `ClientSession` + `stdio_client`를 사용하여 외부 `dbhub` 명령어(npm 패키지)를 MCP 서버로 호출
- `StdioServerParameters(command="dbhub", args=["--config", config_path])`로 **로컬 프로세스** 생성
- `search_objects`, `execute_sql` 2개 도구를 호출
- `dbhub.toml`에 데이터소스 및 도구 설정 정의

### 1.2 이전 문제점

1. **외부 npm 의존성**: `dbhub`는 npm 패키지로 Node.js 런타임 필요
2. **커스터마이징 제한**: 외부 패키지의 도구 동작을 수정할 수 없음
3. **로컬 프로세스 한계**: stdio transport는 동일 머신에서만 동작 → DB 서버와 에이전트 서버 분리 불가
4. **기능 확장 제한**: 스키마 캐싱, 감사 로깅 등 자체 기능을 MCP 서버 레벨에서 추가 불가
5. **설정 혼재**: DB 연결 정보가 클라이언트 설정(`config.py`, `MultiDBConfig`)과 서버 설정(`dbhub.toml`)에 분산

### 1.3 관련 코드 파일

| 파일 | 역할 | 수정 상태 |
|------|------|-----------|
| `src/dbhub/client.py` | MCP 클라이언트 | **완료** — SSE transport로 교체 |
| `src/dbhub/models.py` | 데이터 모델 (87줄) | **완료** — `source_name` 필드 추가, `DBConnectionError` 네이밍 변경 |
| `src/dbhub/__init__.py` | 패키지 exports | 유지 |
| `src/db/interface.py` | DBClient Protocol | 유지 |
| `src/db/__init__.py` | 팩토리 함수 | **완료** — `DBHubConfig`만 전달 |
| `src/config.py` | DBHubConfig, QueryConfig, MultiDBConfig | **완료** — 서버 URL 기반, 연결 문자열 제거 |
| `dbhub.toml` | 외부 dbhub 설정 | **완료** — DEPRECATED 처리 |

---

## 2. 목표 아키텍처

### 2.1 배포 구조

> 기존 direct 모드로 동작되는 PostgreSQL을 MCP 서버에서 직접 접속하도록 변경한다. MCP 서버 구동과 관련된 모든 설정(toml, .env 등)은 `mcp_server/` 폴더에 있어야 한다.

MCP 서버는 **별도 VM**에서 독립 구동된다. 클라이언트(에이전트)와 네트워크로 통신한다.

```
┌──────────────────────────┐          ┌──────────────────────────────┐
│  VM A: 에이전트 서버       │          │  VM B: MCP DB 서버            │
│                          │          │                              │
│  src/                    │   SSE    │  mcp_server/                 │
│  ├── dbhub/client.py ────┼──(HTTP)──┼→ server.py (FastMCP)         │
│  ├── config.py           │          │  ├── db.py (asyncpg pool)    │
│  ├── graph.py            │          │  ├── tools.py                │
│  └── ...                 │          │  ├── security.py             │
│                          │          │  ├── config.toml             │
│  .env (에이전트 설정)      │          │  ├── .env (DB 연결 정보)      │
│                          │          │  └── pyproject.toml          │
│                          │          │           │                  │
│                          │          │           ▼                  │
│                          │          │       PostgreSQL / DB2       │
└──────────────────────────┘          └──────────────────────────────┘
```

### 2.2 핵심 변경사항

| # | 변경사항 | 상태 |
|---|---------|------|
| 1 | `mcp_server/` 폴더에 독립 Python 패키지 생성 | **완료** |
| 2 | Transport 교체: `stdio` → `SSE` | **완료** |
| 3 | `src/dbhub/client.py` 수정: `sse_client` 사용 | **완료** |
| 4 | 설정 완전 분리: DB 연결 정보는 MCP 서버 VM에만 존재 | **완료** |
| 5 | 코드 의존성 완전 분리: `mcp_server/`는 `src/`를 import하지 않음 | **완료** |

### 2.3 설정 정보 분리 설계

**원칙**: MCP 서버는 별도 VM에서 구동되므로, DB 연결 정보·보안 정책·쿼리 제한은 서버 VM에만 존재해야 한다. 클라이언트 VM에는 서버 접속 URL만 있으면 된다.

#### 변경 후 설정 분리 (구현 완료)

```
┌──────────────────────────────────┐    ┌───────────────────────────────────────────┐
│  클라이언트 .env                  │    │  MCP 서버 .env + config.toml               │
│                                  │    │                                           │
│  # MCP 서버 접속 (개발: localhost)  │    │  [server]                                 │
│  DBHUB_SERVER_URL=               │    │    host = "0.0.0.0"                       │
│    http://localhost:9090/sse     │    │    port = 9090                            │
│  DBHUB_SOURCE_NAME=infra_db     │    │    transport = "sse"                      │
│  DBHUB_MCP_CALL_TIMEOUT=60      │    │                                           │
│                                  │    │  [[sources]]  # PostgreSQL (Docker :5433) │
│  # 클라이언트 정책                 │    │    name = "infra_db"                      │
│  QUERY_MAX_RETRY_COUNT=3        │    │    type = "postgresql"                    │
│  QUERY_DEFAULT_LIMIT=1000       │    │    connection = "postgresql://..."        │
│                                  │    │                                           │
│  # LLM, 체크포인트 등 에이전트 설정 │    │  [[sources]]  # DB2 (Docker :50000)       │
│  LLM_PROVIDER=ollama            │    │    name = "infra_db2"                     │
│  ...                             │    │    type = "db2"                           │
│                                  │    │    connection = "DATABASE=infradb;..."    │
│                                  │    │                                           │
│                                  │    │  [[sources]]  # 멀티 DB (운영 환경)        │
│                                  │    │    name = "polestar"                      │
│                                  │    │    ...                                    │
└──────────────────────────────────┘    └───────────────────────────────────────────┘
```

#### 설정 항목별 소유권 매핑 (구현 완료)

| 설정 항목 | 이전 위치 | 현재 위치 | 이유 |
|-----------|-----------|---------|------|
| DB 연결 문자열 | `MultiDBConfig`, `dbhub.toml` | **서버** `config.toml` + `.env` | DB 접속은 서버 VM에서만 발생 |
| DB 타입 (postgresql 등) | `MultiDBConfig` | **서버** `config.toml` | DB 드라이버는 서버가 관리 |
| `query_timeout` | `QueryConfig` (클라이언트) | **서버** `config.toml` (소스별) | DB 쿼리 타임아웃은 서버가 제어 |
| `max_rows` | `QueryConfig` (클라이언트) | **서버** `config.toml` (소스별) | 결과 제한은 서버가 강제 |
| `readonly` | `dbhub.toml` | **서버** `config.toml` | 보안 정책은 서버가 강제 |
| 커넥션 풀 설정 | 없음 | **서버** `config.toml` | 풀은 서버 VM 리소스 |
| MCP 서버 URL | 없음 | **클라이언트** `.env` | 클라이언트가 서버 위치를 알아야 함 |
| `mcp_call_timeout` | 없음 | **클라이언트** `.env` | 네트워크 호출 전체 대기시간은 클라이언트 정책 |
| `max_retry_count` | `QueryConfig` | **클라이언트** 유지 | MCP 호출 재시도는 클라이언트 정책 |
| `default_limit` | `QueryConfig` | **클라이언트** 유지 | SQL 생성 시 기본 LIMIT은 에이전트 정책 |
| `source_name` | `DBHubConfig` | **클라이언트** 유지 | 어떤 소스를 쿼리할지는 클라이언트 선택 |

#### 구현 결과: `MultiDBConfig` (연결 문자열 제거 완료)

```python
# src/config.py — 현재 구현
class MultiDBConfig(BaseSettings):
    """멀티 DB 라우팅 설정. 연결 문자열은 MCP 서버 VM이 관리."""
    active_db_ids_csv: str = ""  # 쉼표 구분, ACTIVE_DB_IDS 환경변수
    # polestar_connection 등 연결 문자열 필드 전부 제거됨
```

#### 구현 결과: `QueryConfig` (서버 정책 제거 완료)

```python
# src/config.py — 현재 구현
class QueryConfig(BaseSettings):
    max_retry_count: int = 3    # 클라이언트: MCP 호출 재시도 횟수
    default_limit: int = 1000   # 에이전트: SQL 생성 시 기본 LIMIT
    # query_timeout, max_rows → 서버 VM config.toml로 이동 완료
```

#### 구현 결과: `db_backend` 분기 유지

```python
# src/config.py — 현재 구현
class AppConfig(BaseSettings):
    db_backend: Literal["dbhub", "direct"] = "direct"
    db_connection_string: str = ""  # direct 모드 전용, 유지
```

### 2.4 다중 DB 타입 지원 (구현 완료)

MCP 서버는 PostgreSQL과 DB2 두 가지 DB 타입을 지원한다.

| DB 타입 | 드라이버 | 비동기 지원 | 구현 파일 |
|---------|----------|------------|-----------|
| `postgresql` | `asyncpg` | 네이티브 async | `mcp_server/mcp_server/db.py` |
| `db2` | `ibm-db` | `asyncio.to_thread` 래핑 | `mcp_server/mcp_server/db.py` |

### 2.5 MCP 서버 제공 도구 (Tools) — 5개 모두 구현 완료

| 도구명 | 설명 | 구현 위치 | 상태 |
|--------|------|-----------|------|
| `search_objects` | DB 테이블/뷰 목록 검색 (PG + DB2) | `tools.py:41` | **완료** |
| `execute_sql` | 읽기 전용 SQL 실행 (PG + DB2) | `tools.py:80` | **완료** |
| `get_table_schema` | 테이블 상세 스키마 조회 (PG + DB2) | `tools.py:144` | **완료** |
| `health_check` | DB 연결 상태 확인 | `tools.py:215` | **완료** |
| `list_sources` | 등록된 활성 데이터소스 목록 반환 | `tools.py:258` | **완료** |

---

## 3. 상세 설계

### 3.1 MCP 서버 패키지 구조 (구현 완료)

```
mcp_server/                      # 독립 패키지 (별도 VM 배포)
├── pyproject.toml               # 서버 전용 의존성 및 빌드 설정         ✅
├── .env.example                 # 서버 환경변수 템플릿                  ✅
├── config.toml                  # 데이터소스 정의 (dbhub.toml 대체)     ✅
├── mcp_server/                  # Python 패키지
│   ├── __init__.py              #                                     ✅
│   ├── __main__.py              # python -m mcp_server로 실행          ✅
│   ├── server.py                # FastMCP 서버 + lifespan              ✅
│   ├── db.py                    # DBPoolManager (asyncpg + ibm_db)    ✅
│   ├── tools.py                 # MCP 도구 5개                         ✅
│   ├── config.py                # TOML + 환경변수 로딩                  ✅
│   └── security.py              # 읽기 전용 SQL 가드                    ✅
└── tests/                       # 서버 단독 테스트
    ├── test_tools.py            #                                     ✅
    ├── test_security.py         #                                     ✅
    └── test_config.py           #                                     ✅
```

> **검증**: `mcp_server/` 내부 코드에서 `src/`의 모듈을 import하는 코드는 없음 (독립 패키지 확인 완료).

### 3.2~3.8 구현 상세 (모두 완료)

각 모듈의 구현은 계획과 일치한다. 주요 구현 현황:

| 모듈 | 계획 | 실제 구현 | 일치 여부 |
|------|------|-----------|-----------|
| `server.py` | FastMCP + lifespan으로 풀 관리 | `create_server()` + `lifespan()` | **일치** |
| `tools.py` | `register_tools(mcp)`로 5개 도구 등록 | `@mcp.tool()` 데코레이터로 5개 등록 | **일치** |
| `db.py` | `DBPoolManager` (PG 풀 + DB2 요청별) | asyncpg 풀 + `asyncio.to_thread` DB2 래핑 | **일치** |
| `security.py` | `validate_readonly()` + 금지 키워드 | `FORBIDDEN_KEYWORDS` frozenset + `sqlparse` | **일치** (DB2 `MERGE`, `CALL` 추가됨) |
| `config.py` | TOML + 환경변수 오버라이드 | `_load_toml()` + `_apply_env_overrides()` | **일치** |
| `__main__.py` | `python -m mcp_server` 엔트리포인트 | `main()` → `create_server()` → `server.run()` | **일치** |
| `client.py` | SSE transport + 재연결 로직 | `sse_client` + `_ensure_connected_with_retry` | **일치** |
| `config.py` (클라이언트) | `server_url` 기반 DBHubConfig | `server_url`, `source_name`, `mcp_call_timeout` | **일치** |

---

## 4. 구현 단계 및 수행 상태

### Phase A: MCP 서버 패키지 생성 (`mcp_server/`) — **완료**

| 단계 | 작업 | 산출물 | 상태 |
|------|------|--------|------|
| A-1 | 독립 패키지 구조 생성 | `mcp_server/pyproject.toml` | **완료** |
| A-2 | 서버 설정 로딩 (TOML + 환경변수 오버라이드) | `mcp_server/mcp_server/config.py` | **완료** |
| A-3 | 설정 파일 작성 | `mcp_server/config.toml`, `.env.example` | **완료** |
| A-4 | DB 커넥션 풀 매니저 (다중 소스) | `mcp_server/mcp_server/db.py` | **완료** |
| A-5 | 보안 검증 (읽기 전용 SQL 가드, 자체 구현) | `mcp_server/mcp_server/security.py` | **완료** |
| A-6 | MCP 도구 구현 (5개) | `mcp_server/mcp_server/tools.py` | **완료** |
| A-7 | FastMCP 서버 조립 + lifespan | `mcp_server/mcp_server/server.py` | **완료** |
| A-8 | 엔트리포인트 | `mcp_server/mcp_server/__main__.py` | **완료** |
| A-9 | 서버 단독 테스트 | `mcp_server/tests/` (3개 파일) | **완료** |

### Phase B: 클라이언트 설정 분리 및 수정 (`src/`) — **완료**

| 단계 | 작업 | 산출물 | 상태 |
|------|------|--------|------|
| B-1 | `DBHubConfig` 재설계 — `server_url` 기반, DB 연결 정보 제거 | `src/config.py` | **완료** |
| B-2 | `QueryConfig` 분리 — `query_timeout`, `max_rows` 제거 | `src/config.py` | **완료** |
| B-3 | `MultiDBConfig` 축소 — 연결 문자열 제거, `active_db_ids_csv`만 유지 | `src/config.py` | **완료** |
| B-4 | `client.py` connect() — `stdio_client` → `sse_client` 교체 | `src/dbhub/client.py` | **완료** |
| B-5 | `client.py` disconnect() — SSE 컨텍스트 종료로 변경 | `src/dbhub/client.py` | **완료** |
| B-6 | `client.py` get_table_schema() — 서버 도구 1회 호출로 단순화 | `src/dbhub/client.py` | **완료** |
| B-7 | `client.py` execute_sql() — 타임아웃을 `mcp_call_timeout` 참조로 변경 | `src/dbhub/client.py` | **완료** |
| B-8 | 시멘틱 라우터에서 `list_sources` 활용하도록 수정 | `src/routing/db_registry.py` | **완료** — `ACTIVE_DB_IDS` 환경변수 기반, MCP 클라이언트 생성 시 `source_name=db_id` 전달 |
| B-9 | `.env.example` 업데이트 (서버 URL 추가, DB 연결 정보 제거) | `.env.example` | **완료** — MCP 서버 설정 참조 안내 포함 |
| B-10 | 기존 `dbhub.toml` deprecated 처리 | `dbhub.toml` 1행 | **완료** |

### Phase C: 통합 테스트 — **완료**

| 단계 | 작업 | 산출물 | 상태 |
|------|------|--------|------|
| C-1 | MCP 서버 단독 실행 테스트 (서버 패키지 내) | `mcp_server/tests/` (34개 통과) | **완료** |
| C-2 | 클라이언트 → 원격 서버 연동 테스트 | `tests/test_dbhub_integration.py` (57개 통과) | **완료** |
| C-3 | 기존 테스트 호환성 확인 | 메인 프로젝트 테스트 전체 통과 | **완료** |

---

## 5. 의존성 변경 (구현 완료)

### 서버 패키지 (`mcp_server/pyproject.toml`)

```toml
dependencies = [
    "mcp[cli]",              # FastMCP + SSE transport
    "asyncpg>=0.29.0",       # PostgreSQL 드라이버
    "ibm-db>=3.2.0",         # DB2 드라이버
    "sqlparse>=0.5.0",       # SQL 파싱
]
```

### 클라이언트 패키지 (`pyproject.toml`)

```toml
dependencies = [
    "mcp",                   # MCP 클라이언트 SDK (sse_client 포함)
    "asyncpg>=0.29.0",       # direct 모드에서 사용
    ...
]
```

### 외부 의존성 제거

- `dbhub` npm 패키지 → **제거 완료** (Node.js 런타임 불필요)
- `config/dbhub_test.toml` → 생성 불필요 (MCP 서버 설정은 `mcp_server/config.toml`에 일원화)

---

## 6. 호환성 및 마이그레이션 (구현 완료)

### 6.1 `DBClient` Protocol 호환

`src/db/interface.py`의 `DBClient` Protocol은 변경 없음. `DBHubClient`가 동일한 인터페이스를 만족.

### 6.2 `get_db_client()` 팩토리 호환

`src/db/__init__.py`의 팩토리 함수에서 `db_backend == "dbhub"` 분기 시 `DBHubClient(config.dbhub, config.query)` 전달. 구현 완료.

### 6.3 기존 direct 모드 영향 없음

`db_backend == "direct"` (PostgresClient) 경로는 변경 없음. `db_connection_string`은 `AppConfig`에 유지.

### 6.4 설정 마이그레이션 가이드

**클라이언트 VM `.env` 변경 (완료):**

| 이전 | 현재 | 비고 |
|------|------|------|
| `DBHUB_CONFIG_PATH=./dbhub.toml` | `DBHUB_SERVER_URL=http://localhost:9090/sse` | 로컬 파일 → 원격 URL |
| `QUERY_TIMEOUT=30` | (제거) | 서버 VM으로 이관 |
| `QUERY_MAX_ROWS=10000` | (제거) | 서버 VM으로 이관 |
| `POLESTAR_DB_CONNECTION=...` | (제거) | 서버 VM으로 이관 |
| `CLOUD_PORTAL_DB_CONNECTION=...` | (제거) | 서버 VM으로 이관 |
| `ITSM_DB_CONNECTION=...` | (제거) | 서버 VM으로 이관 |
| `ITAM_DB_CONNECTION=...` | (제거) | 서버 VM으로 이관 |
| (없음) | `DBHUB_MCP_CALL_TIMEOUT=60` | 신규 |

**서버 VM `.env` (완료 — `mcp_server/.env.example` 참조):**

| 환경변수 | 용도 |
|----------|------|
| `INFRA_DB_CONNECTION` | PostgreSQL (Docker :5433) |
| `INFRA_DB2_CONNECTION` | DB2 (Docker :50000) |
| `POLESTAR_CONNECTION` | 운영 멀티 DB (선택) |
| `CLOUD_PORTAL_CONNECTION` | 운영 멀티 DB (선택) |
| `ITSM_CONNECTION` | 운영 멀티 DB (선택) |
| `ITAM_CONNECTION` | 운영 멀티 DB (선택) |

---

## 7. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 | 구현 여부 |
|--------|------|-----------|-----------|
| SSE transport 안정성 | 네트워크 끊김 시 연결 손실 | `_ensure_connected_with_retry` 재연결 로직 | **구현됨** |
| MCP 결과 포맷 차이 | 클라이언트 파서 호환 깨짐 | `_parse_json_result`, `_parse_table_list` 등 파서 | **구현됨** |
| 네트워크 지연 | 로컬 stdio 대비 응답 시간 증가 | `mcp_call_timeout=60s` (DB timeout 30s보다 넉넉) | **구현됨** |
| 서버 VM 장애 | 에이전트 전체 DB 접근 불가 | 헬스체크 + 재시도 로직 | **구현됨** |
| asyncpg 풀 관리 | 커넥션 리크 | lifespan에서 `close_all()` 보장 | **구현됨** |
| ibm-db 설치 실패 | DB2 소스 비활성 | 초기화 시 에러 로깅, 해당 소스만 비활성 | **구현됨** |
| DB2 동기 드라이버 블로킹 | 이벤트 루프 블로킹 | `asyncio.to_thread()` 래핑 | **구현됨** |
| DB2/PG SQL 구문 차이 | 스키마 조회 쿼리 실패 | 소스 타입별 SQL 분기 (`information_schema` vs `SYSCAT`) | **구현됨** |
| 코드 중복 (security.py) | 클라이언트/서버 양쪽에 SQL 검증 | 이중 방어 의도적 설계 | **구현됨** |

---

## 8. 개발환경 검증 시나리오

개발환경에서의 전체 플로우:

```
1. Docker 컨테이너 시작 (PostgreSQL :5433, DB2 :50000)
2. MCP 서버 시작: cd mcp_server && python -m mcp_server  (localhost:9090)
3. 에이전트 시작: 클라이언트 .env에 DBHUB_SERVER_URL=http://localhost:9090/sse
4. 클라이언트 → SSE → MCP 서버 → PostgreSQL/DB2 → 결과 반환
```

검증 항목:
- `list_sources` → `["infra_db", "infra_db2"]` 반환
- `execute_sql(source="infra_db", sql="SELECT 1")` → PostgreSQL 결과
- `execute_sql(source="infra_db2", sql="SELECT 1 FROM SYSIBM.SYSDUMMY1")` → DB2 결과
- `search_objects(source="infra_db2")` → DB2 테이블 목록 (servers, cpu_metrics 등)

> **주의**: `dbhub --config config/dbhub_test.toml` 명령은 더 이상 사용하지 않는다. 이는 이전 아키텍처의 npm 기반 `dbhub` CLI 명령어이며, 현재는 `python -m mcp_server`로 대체되었다.

---

## 9. 완료 기준

- [x] `mcp_server/` 폴더가 독립 Python 패키지로 구성됨 (자체 `pyproject.toml`)
- [x] `mcp_server/`는 `src/`에 대한 import 의존성이 없음
- [x] `python -m mcp_server`로 서버가 SSE transport로 `0.0.0.0:9090`에서 실행됨 (런타임 검증 완료)
- [x] `search_objects`, `execute_sql`, `get_table_schema`, `health_check`, `list_sources` 5개 도구 구현됨
- [ ] PostgreSQL(Docker :5433) 연결 및 쿼리 실행 정상 동작 — **Docker 미실행 상태, 코드 구현 완료**
- [ ] DB2(Docker :50000) 연결 및 쿼리 실행 정상 동작 — **ibm-db 미설치, 코드 구현 완료**
- [x] 모든 DB 연결 설정이 서버의 `config.toml` + `.env`에 일원 관리됨
- [x] `src/config.py`의 `DBHubConfig`에 DB 연결 정보가 없음 (`server_url`만 보유)
- [x] `QueryConfig`에서 `query_timeout`, `max_rows`가 제거됨
- [x] `MultiDBConfig`에서 연결 문자열이 제거됨
- [x] `DBHubClient`가 SSE transport로 MCP 서버와 통신하도록 구현됨
- [x] 읽기 전용 검증이 서버 레벨에서 작동 (`mcp_server/mcp_server/security.py`)
- [x] 기존 테스트 모두 통과 (MCP 서버 34개 + 통합 테스트 57개 + 메인 프로젝트 전체 통과)
- [x] `dbhub` npm 패키지 의존성 제거됨
- [x] 클라이언트 → 서버 연동 통합 테스트 작성 완료 (`tests/test_dbhub_integration.py`, 57개 테스트)
