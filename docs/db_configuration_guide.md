# DB 연결 및 설정 가이드

이 문서는 collectorinfra 프로젝트의 DB 연결, MCP 서버, Redis, DB 프로필 등 데이터베이스 관련 모든 설정을 정리한다.

---

## 목차

1. [전체 아키텍처](#1-전체-아키텍처)
2. [DB 연결 모드](#2-db-연결-모드)
3. [Docker 컨테이너 구성](#3-docker-컨테이너-구성)
4. [MCP 서버 설정](#4-mcp-서버-설정)
5. [클라이언트(메인 앱) 환경변수](#5-클라이언트메인-앱-환경변수)
6. [민감 키 관리 (.encenv)](#6-민감-키-관리-encenv)
7. [Redis 설정](#7-redis-설정)
8. [스키마 캐시 설정](#8-스키마-캐시-설정)
9. [멀티 DB 시멘틱 라우팅](#9-멀티-db-시멘틱-라우팅)
10. [DB 프로필 (config/db_profiles/)](#10-db-프로필-configdb_profiles)
11. [도메인 설정 (domain_config.py)](#11-도메인-설정-domain_configpy)
12. [설정 동기화 체크리스트](#12-설정-동기화-체크리스트)
13. [개발환경 빠른 시작](#13-개발환경-빠른-시작)
14. [운영환경 설정 요령](#14-운영환경-설정-요령)
15. [트러블슈팅](#15-트러블슈팅)

---

## 1. 전체 아키텍처

```
┌─────────────────────────────┐
│  클라이언트 (메인 앱)          │
│  src/config.py → AppConfig  │
│  .env + .encenv             │
└──────┬──────────────────────┘
       │
       │  DB_BACKEND 설정에 따라 분기
       │
  ┌────┴────┐
  │         │
  ▼         ▼
┌──────┐  ┌───────────────────────────────┐
│direct│  │ dbhub (MCP 서버 경유)          │
│ mode │  │                               │
│      │  │  클라이언트 ──SSE──▶ MCP 서버  │
│      │  │                    (9099)      │
│      │  │  mcp_server/config.toml       │
│      │  │  mcp_server/.env              │
└──┬───┘  └──────────┬────────────────────┘
   │                 │
   ▼                 ▼
┌──────────────────────────────────────────┐
│          데이터베이스 (Docker 등)          │
│  PostgreSQL :5433  │  DB2 :50000         │
│  Polestar  :5434   │  운영 DB            │
└──────────────────────────────────────────┘

┌──────────────────────┐
│  Redis (:6380)       │
│  스키마 캐시/세션     │
│  redis/redis.conf    │
└──────────────────────┘
```

---

## 2. DB 연결 모드

메인 앱은 두 가지 DB 연결 모드를 지원한다.

### 2.1. Direct 모드 (DB_BACKEND=direct)

PostgreSQL에 직접 연결한다. 개발/테스트 시 간편하다.

| 환경변수 | 설명 | 예시 |
|----------|------|------|
| `DB_BACKEND` | `direct` 설정 | `direct` |
| `DB_CONNECTION_STRING` | PostgreSQL DSN | `postgresql://infra_user:password@localhost:5433/infra_db` |

- 클라이언트: `src/db/client.py` → `PostgresClient` (asyncpg 사용)
- MCP 서버 불필요, 단일 DB만 연결 가능
- 커넥션 풀: min=1, max=5, 쿼리 타임아웃 30초, 최대 행 10,000

### 2.2. DBHub 모드 (DB_BACKEND=dbhub)

MCP 서버를 경유하여 DB에 접근한다. 멀티 DB, 운영 환경에 적합하다.

| 환경변수 | 설명 | 예시 |
|----------|------|------|
| `DB_BACKEND` | `dbhub` 설정 | `dbhub` |
| `DBHUB_SERVER_URL` | MCP 서버 SSE 엔드포인트 | `http://localhost:9099/sse` |
| `DBHUB_SOURCE_NAME` | 기본 쿼리 대상 소스명 | `infra_db` |
| `DBHUB_MCP_CALL_TIMEOUT` | MCP 호출 전체 대기시간 (초) | `60` |

- 클라이언트: `src/dbhub/client.py` → `DBHubClient` (MCP SDK SSE transport)
- MCP 서버가 별도 프로세스/VM에서 실행 필요
- 자동 재연결: 최대 3회, 지수 백오프 (2초, 4초, 6초)
- `DBHUB_SOURCE_NAME`은 반드시 `mcp_server/config.toml`의 `[[sources]] name`과 일치해야 함

---

## 3. Docker 컨테이너 구성

### 3.1. PostgreSQL (infra_db)

파일: `db/docker-compose.yml`

```yaml
services:
  postgres:
    image: postgres:16-alpine
    container_name: infra_monitoring_db
    ports: "5433:5432"
    environment:
      POSTGRES_DB: infra_db
      POSTGRES_USER: infra_user
      POSTGRES_PASSWORD: infra_pass_2024
```

```bash
cd db && docker compose up -d
```

- 초기화 스크립트: `db/init/` 디렉토리 (Docker entrypoint가 자동 실행)
- 스키마: servers, cpu_metrics, memory_metrics, disk_metrics, network_metrics
- 연결 문자열: `postgresql://infra_user:infra_pass_2024@localhost:5433/infra_db`

### 3.2. DB2 (infra_db2)

파일: `db2/docker-compose.yml`

```yaml
services:
  db2:
    image: icr.io/db2_community/db2
    platform: linux/amd64
    container_name: infra_db2
    privileged: true
    ports: "50000:50000"
    environment:
      LICENSE: accept
      DB2INST1_PASSWORD: db2pass2024
      DBNAME: infradb
      DB2INSTANCE: db2inst1
    deploy:
      resources:
        limits:
          memory: 4g
        reservations:
          memory: 2g
```

```bash
cd db2 && docker compose up -d
```

- **주의**: DB2 컨테이너 초기 기동에 최대 10분 소요 (healthcheck start_period: 600s)
- **주의**: `linux/amd64` 플랫폼 전용 (macOS Apple Silicon에서는 Rosetta 필요)
- 초기화 스크립트: `db2/init/`, 후처리: `db2/scripts/post-start.sh`
- 연결 문자열: `DATABASE=infradb;HOSTNAME=localhost;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=db2pass2024;`

### 3.3. Polestar PostgreSQL (개발용)

파일: `testdata/pg/docker-compose.yml`

```yaml
services:
  polestar-pg:
    image: postgres:16-alpine
    container_name: polestar_pg
    ports: "5434:5432"
    environment:
      POSTGRES_DB: infradb
      POSTGRES_USER: polestar_user
      POSTGRES_PASSWORD: polestar_pass_2024
```

```bash
cd testdata/pg && docker compose up -d
```

- 운영 Polestar DB2 대신 macOS에서 개발/테스트할 때 사용
- EAV 패턴 테이블 (cmm_resource, core_config_prop)
- 연결 문자열: `postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb`

### 3.4. Redis

파일: `redis/docker-compose.yml`

```yaml
services:
  redis:
    image: redis:7-alpine
    container_name: collectorinfra-redis
    ports: "6380:6379"
    command: redis-server /usr/local/etc/redis/redis.conf
```

```bash
cd redis && docker compose up -d
```

- **주의**: 호스트 포트가 `6380`임 (기본 6379가 아님)
- 설정 파일: `redis/redis.conf`

---

## 4. MCP 서버 설정

MCP 서버는 별도 프로세스로 동작하며, DB 연결을 대행한다. 두 개의 파일로 설정한다.

### 4.1. config.toml (소스 정의 + 서버 설정)

파일: `mcp_server/config.toml`

```toml
[server]
name = "dbhub-server"
host = "0.0.0.0"
port = 9099
transport = "sse"
log_level = "info"

[[sources]]
name = "infra_db"        # ← 이 name이 식별자
type = "postgresql"
readonly = true
query_timeout = 30       # DB 쿼리 타임아웃 (초)
max_rows = 10000         # 최대 반환 행 수
pool_min_size = 1
pool_max_size = 5

[[sources]]
name = "infra_db2"
type = "db2"
readonly = true
query_timeout = 30
max_rows = 10000

[[sources]]
name = "polestar"
type = "postgresql"
readonly = true
query_timeout = 30
max_rows = 10000
pool_min_size = 1
pool_max_size = 3

[[sources]]
name = "cloud_portal"
type = "postgresql"
readonly = true
query_timeout = 30
max_rows = 10000

[[sources]]
name = "itsm"
type = "postgresql"
readonly = true
query_timeout = 30
max_rows = 10000
```

**각 소스 설정 항목:**

| 항목 | 설명 | 기본값 |
|------|------|--------|
| `name` | 소스 식별자 (클라이언트에서 이 이름으로 참조) | 필수 |
| `type` | DB 종류 (`postgresql`, `db2`) | `postgresql` |
| `readonly` | 읽기 전용 모드 (SELECT만 허용) | `true` |
| `query_timeout` | DB 쿼리 타임아웃 (초) | `30` |
| `max_rows` | 최대 반환 행 수 | `10000` |
| `pool_min_size` | 커넥션 풀 최소 크기 (PostgreSQL만) | `1` |
| `pool_max_size` | 커넥션 풀 최대 크기 (PostgreSQL만) | `5` |

### 4.2. .env (DB 연결 문자열)

파일: `mcp_server/.env`

DB 연결 문자열은 보안상 TOML에 기재하지 않고 환경변수로 관리한다.

**환경변수명 규칙**: `{config.toml의 name을 대문자로}_CONNECTION`

| config.toml name | 환경변수 | 형식 | 개발 기본값 |
|------------------|---------|------|------------|
| `infra_db` | `INFRA_DB_CONNECTION` | PostgreSQL DSN | `postgresql://infra_user:password@localhost:5433/infra_db` |
| `infra_db2` | `INFRA_DB2_CONNECTION` | ibm_db 형식 | `DATABASE=infradb;HOSTNAME=localhost;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=db2pass2024;` |
| `polestar` | `POLESTAR_CONNECTION` | PostgreSQL DSN | `postgresql://polestar_user:password@localhost:5434/infradb` |
| `cloud_portal` | `CLOUD_PORTAL_CONNECTION` | PostgreSQL DSN | (운영 시 설정) |
| `itsm` | `ITSM_CONNECTION` | PostgreSQL DSN | (운영 시 설정) |

**핵심 규칙:**
- 연결 문자열이 비어있거나 주석 처리된 소스는 자동으로 비활성 처리됨
- 이미 시스템 환경변수로 설정된 값은 `.env` 파일보다 우선

**서버 설정 오버라이드 환경변수:**

| 환경변수 | 설명 | 기본값 |
|----------|------|--------|
| `SERVER_NAME` | 서버 식별 이름 | `dbhub-server` |
| `SERVER_HOST` | 바인딩 호스트 | `0.0.0.0` |
| `SERVER_PORT` | 서버 포트 | `9099` |
| `SERVER_TRANSPORT` | 전송 방식 | `sse` |
| `SERVER_LOG_LEVEL` | 로그 레벨 | `info` |

### 4.3. MCP 서버 제공 도구

MCP 서버는 5개의 도구를 클라이언트에 제공한다:

| 도구 | 설명 |
|------|------|
| `search_objects` | 테이블/뷰 검색 (패턴 매칭) |
| `get_table_schema` | 테이블 상세 스키마 조회 (컬럼, PK, FK) |
| `execute_sql` | SELECT 쿼리 실행 (readonly 검증 적용) |
| `health_check` | 소스 연결 상태 확인 |
| `list_sources` | 활성 소스 목록 조회 |

### 4.4. MCP 서버 실행

```bash
cd mcp_server
pip install -e .
python -m mcp_server
```

---

## 5. 클라이언트(메인 앱) 환경변수

파일: 프로젝트 루트 `.env` (`.env.example`에서 복사)

### 5.1. DB 연결 관련

```bash
# DB 연결 모드: dbhub (MCP 경유) | direct (PostgreSQL 직접)
DB_BACKEND=direct

# direct 모드 시 PostgreSQL 연결 문자열
DB_CONNECTION_STRING=postgresql://infra_user:password@localhost:5433/infra_db

# dbhub 모드 시 MCP 서버 설정
DBHUB_SERVER_URL=http://localhost:9099/sse
DBHUB_SOURCE_NAME=infra_db
DBHUB_MCP_CALL_TIMEOUT=60
```

### 5.2. 쿼리 정책

```bash
QUERY_MAX_RETRY_COUNT=3      # SQL 생성 실패 시 최대 재시도
QUERY_DEFAULT_LIMIT=1000     # SQL 생성 시 기본 LIMIT
```

### 5.3. 체크포인트 (대화 상태 저장)

```bash
CHECKPOINT_BACKEND=sqlite         # sqlite | postgres
CHECKPOINT_DB_URL=checkpoints.db  # SQLite 파일 경로 또는 PostgreSQL DSN
```

### 5.4. 설정 로딩 구조 (src/config.py)

모든 환경변수는 `pydantic-settings`를 통해 타입 안전하게 로딩된다.

```
AppConfig
├── llm: LLMConfig                 (env_prefix: LLM_)
├── dbhub: DBHubConfig             (env_prefix: DBHUB_)
├── query: QueryConfig             (env_prefix: QUERY_)
├── security: SecurityConfig       (env_prefix: SECURITY_)
├── server: ServerConfig           (env_prefix: API_)
├── admin: AdminConfig             (env_prefix: ADMIN_)
├── multi_db: MultiDBConfig        (env_prefix: MULTI_DB_)
├── redis: RedisConfig             (env_prefix: REDIS_)
├── schema_cache: SchemaCacheConfig (env_prefix: SCHEMA_CACHE_)
├── db_backend: "dbhub" | "direct"
├── db_connection_string: str
├── checkpoint_backend: "sqlite" | "postgres"
├── checkpoint_db_url: str
├── enable_semantic_routing: bool
├── enable_sql_approval: bool
├── conversation_max_turns: int
└── conversation_ttl_hours: int
```

**pydantic-settings 주의사항:**
- `list[str]` 필드는 반드시 JSON 배열 형식으로 작성: `["a","b"]` (쉼표 구분 문자열 불가)
- `bool` 필드는 `true` / `false` (소문자)
- 환경변수 파일: `.env`(일반), `.encenv`(민감 키)

---

## 6. 민감 키 관리 (.encenv)

파일: 프로젝트 루트 `.encenv` (`.encenv.example`에서 복사)

API 키, 비밀번호 등 민감 정보는 `.env`가 아닌 `.encenv` 파일에 별도 관리한다. `.encenv`는 `.gitignore`에 등록되어 git에 업로드되지 않는다.

```bash
# Gemini API 키
LLM_GEMINI_API_KEY=

# Ollama Gateway API 키 (게이트웨이 사용 시)
LLM_API_KEY=

# FabriX API 키
FABRIX_API_KEY=
FABRIX_CLIENT_KEY=

# 운영자 비밀번호 (운영 시 반드시 변경)
ADMIN_PASSWORD=admin123

# JWT 시크릿 (비어있으면 서버 시작 시 자동 생성)
# 운영 시 고정값 권장: openssl rand -hex 32
ADMIN_JWT_SECRET=

# Redis 비밀번호
REDIS_PASSWORD=
```

`.encenv`를 읽는 설정 클래스: `LLMConfig`, `AdminConfig`, `RedisConfig`
(각 클래스의 `env_file`에 `[".env", ".encenv"]` 지정)

---

## 7. Redis 설정

### 7.1. 클라이언트 환경변수 (루트 .env)

```bash
REDIS_HOST=localhost
REDIS_PORT=6379          # Docker 내부 포트 (호스트에서는 6380으로 매핑됨에 주의)
REDIS_DB=0
REDIS_SSL=false
REDIS_SOCKET_TIMEOUT=5
# REDIS_PASSWORD → .encenv에서 관리
```

> **주의**: Docker 컨테이너는 호스트 포트 `6380`에 매핑되어 있다.
> `REDIS_PORT`를 `6380`으로 설정하거나, Docker 포트 매핑을 `6379:6379`로 변경해야 한다.

### 7.2. Redis 서버 설정 (redis/redis.conf)

```
# RDB 스냅샷
save 3600 1          # 1시간 내 1건 변경 시
save 300 100         # 5분 내 100건 변경 시
save 60 10000        # 1분 내 10000건 변경 시

# AOF
appendonly yes
appendfsync everysec  # 매초 fsync (최대 1초 데이터 유실)

# 메모리
maxmemory 256mb
maxmemory-policy noeviction   # 메모리 초과 시 쓰기 거부 (캐시 데이터 보존)
```

### 7.3. 용도

- 스키마 캐시 저장 (테이블 스키마, 컬럼 설명, fingerprint)
- EAV 속성 동의어 매핑 저장
- 세션 관리 (대화 컨텍스트)

---

## 8. 스키마 캐시 설정

```bash
# 캐시 백엔드: redis | file
SCHEMA_CACHE_BACKEND=redis

# 파일 캐시 디렉토리 (backend=file 시)
SCHEMA_CACHE_CACHE_DIR=.cache/schema

# 캐시 활성화 여부
SCHEMA_CACHE_ENABLED=true

# LLM으로 컬럼 설명 자동 생성 여부
SCHEMA_CACHE_AUTO_GENERATE_DESCRIPTIONS=true

# fingerprint 검증 주기 (초, 기본 1800 = 30분)
# DB 스키마 변경 감지: 이 주기마다 실제 DB 스키마와 캐시를 비교
SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS=1800
```

**동작 원리:**
1. 첫 쿼리 시 DB 스키마를 조회하여 Redis/파일에 캐시
2. fingerprint TTL 내에는 캐시된 스키마를 사용 (DB 재조회 없음)
3. TTL 만료 시 DB 스키마의 fingerprint를 비교하여 변경이 있으면 캐시 갱신
4. `AUTO_GENERATE_DESCRIPTIONS=true`이면 LLM이 각 컬럼의 한글 설명을 생성하여 캐시

---

## 9. 멀티 DB 시멘틱 라우팅

여러 DB를 동시에 연결하고, 사용자 질문을 LLM이 분석하여 적절한 DB로 라우팅하는 기능.

### 9.1. 클라이언트 설정 (루트 .env)

```bash
# 시멘틱 라우팅 명시적 활성화
ENABLE_SEMANTIC_ROUTING=false

# 활성 DB ID 목록 (쉼표 구분)
# MCP 서버의 config.toml [[sources]] name과 정확히 일치해야 함
ACTIVE_DB_IDS=polestar,cloud_portal,itsm,itam
```

**자동 활성화**: `ACTIVE_DB_IDS`가 설정되어 있으면 `ENABLE_SEMANTIC_ROUTING`을 명시하지 않아도 자동 활성화된다.

### 9.2. 단일 DB vs 멀티 DB

| 항목 | 단일 DB | 멀티 DB |
|------|---------|---------|
| 설정 | `DBHUB_SOURCE_NAME=소스명` | `ACTIVE_DB_IDS=소스1,소스2,...` |
| 라우팅 | 없음 (고정 소스) | LLM 기반 시멘틱 라우팅 |
| MCP 소스 | 1개만 활성 | 여러 개 활성 |
| 용도 | 개발/테스트 | 운영 환경 |

---

## 10. DB 프로필 (config/db_profiles/)

DB의 특수한 구조 패턴(EAV, 계층형 등)을 YAML 파일로 기술한다. LLM이 SQL 생성 시 이 정보를 프롬프트에 활용한다.

### 10.1. 파일 위치 및 명명 규칙

```
config/db_profiles/
├── polestar_pg.yaml     # Polestar DB (수동 작성)
├── test_db.yaml         # 테스트용 (자동 생성)
└── unknown.yaml         # 기타 (자동 생성)
```

### 10.2. 프로필 구조 (polestar_pg.yaml 예시)

```yaml
# source: manual → 운영자가 직접 작성, LLM 자동 분석으로 덮어쓰지 않음
source: manual

patterns:
  # EAV (Entity-Attribute-Value) 패턴
  - type: eav
    entity_table: cmm_resource          # 엔티티 테이블
    config_table: core_config_prop      # EAV 설정 테이블
    attribute_column: name              # 속성 타입 컬럼
    value_column: stringvalue_short     # 값 컬럼
    lob_value_column: stringvalue       # LOB 값 컬럼
    lob_flag_column: is_lob            # LOB 여부 플래그

    # 값 기반 조인 (FK 없이 값으로 테이블 간 관계 정의)
    value_joins:
      - eav_attribute: Hostname
        eav_value_column: stringvalue_short
        entity_column: hostname
        description: "EAV Hostname 속성값 = cmm_resource.hostname"
      - eav_attribute: IPaddress
        eav_value_column: stringvalue_short
        entity_column: ipaddress

    # 알려진 EAV 속성 + 한글 동의어
    known_attributes:
      - name: OSType
        description: "운영체제 종류"
        synonyms: ["운영체제", "OS종류", "OS 타입", "OS"]
      - name: Vendor
        description: "서버 제조사"
        synonyms: ["벤더", "제조사", "제조업체"]
      # ...

  # 계층형 패턴
  - type: hierarchy
    table: cmm_resource
    id_column: id
    parent_column: parent_resource_id
    type_column: resource_type          # 리소스 종류 구분
    name_column: hostname

# LLM에 제공되는 쿼리 가이드
query_guide: |
  Polestar DB는 cmm_resource(리소스 계층)와 core_config_prop(EAV 설정) 2개 테이블로 구성됩니다.
  ...
```

### 10.3. source 필드

| 값 | 의미 |
|----|------|
| `manual` | 운영자가 직접 작성/검증. LLM 자동 분석으로 덮어쓰지 않음 |
| `auto` | LLM이 자동 생성. 새로운 분석 결과로 갱신 가능 |

---

## 11. 도메인 설정 (domain_config.py)

파일: `src/routing/domain_config.py`

시멘틱 라우팅에서 사용하는 DB 도메인 정의. 각 DB의 담당 데이터 영역, 별칭, DB 엔진 종류를 기술한다.

| db_id | 표시명 | 담당 데이터 | DB 엔진 |
|-------|--------|------------|---------|
| `polestar` | Polestar DB | 서버 물리 사양, 사용량, 프로세스 정보 | DB2 (운영) / PostgreSQL (개발) |
| `cloud_portal` | Cloud Portal DB | VM, 데이터스토어, 영역별 VM 대수 | PostgreSQL |
| `itsm` | ITSM DB | 서비스 요청, 인시던트, 변경관리, SLA | PostgreSQL |
| `itam` | ITAM DB | IT 자산, 라이프사이클, 계약, 라이선스 | PostgreSQL |

**별칭(aliases)**: 사용자가 한글로 "폴스타 서버 목록 보여줘" 등으로 입력하면, aliases 매칭으로 해당 DB를 직접 지정할 수 있다.

새 DB를 추가하려면:
1. `domain_config.py`의 `DB_DOMAINS` 리스트에 `DBDomainConfig` 추가
2. MCP 서버 설정에 소스 추가 (섹션 12 체크리스트 참조)

---

## 12. 설정 동기화 체크리스트

**소스를 추가/변경할 때 아래 3곳을 반드시 맞춰야 한다.**

### 체크리스트

```
① mcp_server/config.toml  ← 기준점
   → [[sources]] name = "소스명" 으로 소스 정의
   → type, readonly, query_timeout, max_rows, pool 설정

② mcp_server/.env
   → {소스명을 대문자로}_CONNECTION 으로 DB 연결 문자열 설정
   → 예: name="polestar" → POLESTAR_CONNECTION=postgresql://...
   → 연결 문자열이 비어있거나 주석 처리 → 자동 비활성

③ 프로젝트 루트 .env (클라이언트)
   → 단일 DB: DBHUB_SOURCE_NAME=소스명
   → 멀티 DB: ACTIVE_DB_IDS=소스명1,소스명2
```

### 새 DB 추가 전체 절차

1. **Docker 컨테이너 준비** (또는 운영 DB 접속 확인)
2. **MCP 서버 config.toml**: `[[sources]]` 블록 추가
3. **MCP 서버 .env**: `{NAME}_CONNECTION` 환경변수 추가
4. **MCP 서버 재시작**
5. **루트 .env**: `ACTIVE_DB_IDS`에 소스명 추가 (멀티 DB 시)
6. **domain_config.py**: `DB_DOMAINS`에 도메인 설정 추가 (시멘틱 라우팅 시)
7. **(선택)** `config/db_profiles/`에 프로필 YAML 작성 (EAV 등 특수 구조가 있을 때)

---

## 13. 개발환경 빠른 시작

### 최소 구성 (Direct 모드, 단일 PostgreSQL)

```bash
# 1. PostgreSQL Docker 기동
cd db && docker compose up -d && cd ..

# 2. Redis 기동
cd redis && docker compose up -d && cd ..

# 3. 환경변수 설정
cp .env.example .env
cp .encenv.example .encenv

# .env 수정:
#   DB_BACKEND=direct
#   DB_CONNECTION_STRING=postgresql://infra_user:infra_pass_2024@localhost:5433/infra_db
#   REDIS_PORT=6380     ← Docker 포트 매핑에 맞춤

# 4. 앱 실행
python -m src.main
```

### Polestar 개발 구성 (DBHub 모드)

```bash
# 1. Polestar PostgreSQL Docker 기동
cd testdata/pg && docker compose up -d && cd ../..

# 2. Redis 기동
cd redis && docker compose up -d && cd ..

# 3. MCP 서버 설정
cd mcp_server
cp .env.example .env
# .env 수정: POLESTAR_CONNECTION 주석 해제

# 4. MCP 서버 실행
python -m mcp_server &

# 5. 클라이언트 환경변수
cd ..
cp .env.example .env
# .env 수정:
#   DB_BACKEND=dbhub
#   DBHUB_SERVER_URL=http://localhost:9099/sse
#   DBHUB_SOURCE_NAME=polestar

# 6. 앱 실행
python -m src.main
```

---

## 14. 운영환경 설정 요령

### 보안

- `.encenv`에 모든 민감 키 집중 관리 (git 미추적)
- `ADMIN_PASSWORD` 반드시 변경
- `ADMIN_JWT_SECRET` 고정값 설정 (`openssl rand -hex 32`)
- `REDIS_PASSWORD` 설정
- DB 연결 문자열에 운영 비밀번호 사용
- MCP 서버의 `readonly = true` 유지 (SQL 인젝션 방어 레이어)

### 성능

- Redis `maxmemory`를 운영 트래픽에 맞게 조정 (기본 256MB)
- PostgreSQL `pool_max_size` 조정 (기본 5, 동시 접속에 따라 증가)
- `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS` 조정 (스키마 변경이 적으면 늘림)
- `QUERY_DEFAULT_LIMIT` 운영 환경에 맞게 조정

### 멀티 DB 운영

```bash
# 루트 .env
ENABLE_SEMANTIC_ROUTING=true
ACTIVE_DB_IDS=polestar,cloud_portal,itsm,itam

# MCP 서버 .env
POLESTAR_CONNECTION=postgresql://user:pass@prod-host:5432/polestar_db
CLOUD_PORTAL_CONNECTION=postgresql://user:pass@prod-host:5432/cloud_portal_db
ITSM_CONNECTION=postgresql://user:pass@prod-host:5432/itsm_db
ITAM_CONNECTION=postgresql://user:pass@prod-host:5432/itam_db
```

---

## 15. 트러블슈팅

### "알 수 없는 소스" 에러

**원인**: 클라이언트의 `DBHUB_SOURCE_NAME` 또는 `ACTIVE_DB_IDS`와 MCP 서버 `config.toml`의 `[[sources]] name`이 불일치.

**해결**: 3곳의 소스명을 정확히 일치시킨다 (섹션 12 참조).

### MCP 서버 연결 실패

**확인 사항:**
1. MCP 서버가 실행 중인가? (`python -m mcp_server`)
2. 서버 포트(기본 9099)가 열려있는가?
3. `DBHUB_SERVER_URL`의 포트가 MCP 서버 포트와 일치하는가?
4. URL 끝에 `/sse` 경로가 포함되어 있는가?

### DB 연결 실패 (MCP 서버 측)

**확인 사항:**
1. Docker 컨테이너가 정상 기동되었는가? (`docker ps`, healthcheck 상태 확인)
2. `mcp_server/.env`의 연결 문자열이 올바른가?
3. DB2의 경우 초기 기동에 최대 10분 소요 (start_period: 600s)

### Redis 연결 실패

**확인 사항:**
1. Redis Docker 컨테이너가 실행 중인가?
2. 호스트 포트 매핑 확인: Docker는 `6380:6379`이므로 `REDIS_PORT=6380`인지 확인
3. `REDIS_PASSWORD`가 설정되어 있으면 Redis 서버에도 비밀번호가 설정되어 있는지 확인

### pydantic-settings 파싱 에러

**원인**: `list[str]` 필드에 쉼표 구분 문자열을 사용함.

**해결**: JSON 배열 형식으로 작성.
```bash
# 잘못된 예
SECURITY_SENSITIVE_COLUMNS=password,secret,token

# 올바른 예
SECURITY_SENSITIVE_COLUMNS=["password","secret","token"]
```
