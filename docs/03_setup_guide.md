# 초기 환경 설정 가이드

collectorinfra 프로젝트를 서버 환경에서 실행하기 위한 단계별 설정 가이드.

---

## 목차

1. [사전 요구사항](#1-사전-요구사항)
2. [Python 의존성 설치](#2-python-의존성-설치)
3. [환경변수 설정](#3-환경변수-설정)
4. [데이터베이스 설정](#4-데이터베이스-설정)
5. [Redis 설정](#5-redis-설정)
6. [MCP 서버 (DBHub) 설정](#6-mcp-서버-dbhub-설정)
7. [LLM 설정](#7-llm-설정)
8. [애플리케이션 실행](#8-애플리케이션-실행)
9. [설정 검증](#9-설정-검증)

---

## 1. 사전 요구사항

| 항목 | 버전 | 비고 |
|------|------|------|
| Python | >= 3.11 | `python3 --version` |
| PostgreSQL | >= 16 | `psql --version` |
| Redis | >= 7.0 | `redis-server --version` |
| Git | 최신 | 소스 클론 |
| Ollama (선택) | 최신 | 로컬 LLM 사용 시 |
| DB2 (선택) | >= 11.5 | Polestar 운영 DB 연결 시 |

---

## 2. Python 의존성 설치

### 2.1 가상환경 생성

```bash
cd collectorinfra
python3 -m venv .venv
source .venv/bin/activate   # macOS/Linux
# .venv\Scripts\activate    # Windows
```

### 2.2 메인 애플리케이션 의존성

```bash
pip install -e .
```

설치되는 주요 패키지:

| 패키지 | 용도 |
|--------|------|
| `langgraph>=0.2.0` | 에이전트 프레임워크 (상태 머신) |
| `langchain-core>=0.3.0` | LLM 추상화 레이어 |
| `mcp` | MCP 클라이언트 (DBHub 모드) |
| `asyncpg>=0.29.0` | PostgreSQL async 드라이버 (direct 모드) |
| `fastapi>=0.110.0` | API 서버 |
| `uvicorn>=0.30.0` | ASGI 서버 |
| `redis[hiredis]>=5.0.0` | Redis 클라이언트 (스키마 캐시) |
| `langgraph-checkpoint-sqlite` | 대화 상태 체크포인트 (개발용) |
| `pydantic-settings>=2.0` | 환경변수 기반 설정 관리 |
| `sqlparse>=0.5.0` | SQL 파싱 및 검증 |
| `structlog>=24.0.0` | 구조화 로깅 |
| `PyJWT>=2.8.0` | JWT 인증 토큰 |
| `bcrypt>=4.0.0` | 비밀번호 해싱 |

### 2.3 선택적 의존성

필요에 따라 추가 설치:

```bash
# 문서 생성 기능 (Excel/Word 템플릿 처리)
pip install -e ".[document]"

# Gemini LLM 사용 시
pip install -e ".[gemini]"

# PostgreSQL 체크포인트 (운영 환경)
pip install -e ".[postgres-checkpoint]"

# 개발 도구 (테스트, 린트, 타입체크)
pip install -e ".[dev]"

# E2E 테스트 (Playwright)
pip install -e ".[e2e]"
playwright install
```

### 2.4 MCP 서버 의존성 (별도 설치)

MCP 서버는 별도 패키지로 관리된다. 메인 앱과 같은 가상환경 또는 별도 가상환경에 설치:

```bash
pip install -e ./mcp_server
```

설치되는 패키지:

| 패키지 | 용도 |
|--------|------|
| `mcp[cli]` | FastMCP 서버 프레임워크 + SSE transport |
| `asyncpg>=0.29.0` | PostgreSQL async 드라이버 |
| `ibm-db>=3.2.0` | DB2 드라이버 (macOS에서는 설치 실패할 수 있음) |
| `sqlparse>=0.5.0` | SQL 파싱 (읽기 전용 검증) |

> **참고**: macOS에서 `ibm-db`가 설치되지 않는 경우 DB2 소스는 사용할 수 없다. PostgreSQL 소스만으로 개발 가능.

---

## 3. 환경변수 설정

### 3.1 메인 애플리케이션 (.env)

프로젝트 루트에서 `.env.example`을 복사하여 `.env` 파일을 생성한다:

```bash
cp .env.example .env
```

**최소 필수 설정** (기본값으로 동작하지만 확인 필요):

```dotenv
# DB 연결 모드 — direct(직접 연결) 또는 dbhub(MCP 서버 경유)
DB_BACKEND=direct

# direct 모드: PostgreSQL 연결 문자열 (호스트/포트/비밀번호를 실제 환경에 맞게 수정)
DB_CONNECTION_STRING=postgresql://infra_user:password@localhost:5432/infra_db

# LLM 설정
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
LLM_OLLAMA_BASE_URL=http://localhost:11434

# Redis (서버 직접 설치 시 기본 포트 6379)
REDIS_HOST=localhost
REDIS_PORT=6379

# API 서버
API_HOST=0.0.0.0
API_PORT=8000
```

### 3.2 민감정보 관리 (.encenv)

API 키, 비밀번호 등 민감 정보는 `.encenv` 파일에서 별도 관리한다:

```bash
# .encenv 파일 생성 (gitignore에 포함됨)
cat > .encenv << 'EOF'
# Ollama Gateway API 키 (게이트웨이 사용 시)
LLM_API_KEY=

# Gemini API 키 (LLM_PROVIDER=gemini 시)
LLM_GEMINI_API_KEY=

# FabriX 키 (LLM_PROVIDER=fabrix 시) — 반드시 LLM_ 접두
# 접두 없는 FABRIX_API_KEY 는 OS 환경변수로만 읽혀, 이 파일에 적으면 무시된다
LLM_FABRIX_API_KEY=
LLM_FABRIX_CLIENT_KEY=

# 운영자·사용자 인증 — AUTH_ENABLED=true 면 셋 다 필수 (3.5 참조)
# 기본 크레덴셜은 없다(D-071). 두 시크릿은 서로 다른 값이어야 한다(D-070)
ADMIN_PASSWORD=
ADMIN_JWT_SECRET=
AUTH_JWT_SECRET=

# Redis 비밀번호 (설정 시)
REDIS_PASSWORD=
EOF
```

### 3.3 pydantic-settings 주의사항

`.env`에서 복합 타입 필드를 설정할 때 반드시 JSON 형식을 사용해야 한다:

```dotenv
# 올바른 예 (JSON 배열)
SECURITY_SENSITIVE_COLUMNS=["password","secret","token"]
API_CORS_ORIGINS=["*"]

# 잘못된 예 (쉼표 구분 문자열 — 파싱 에러 발생)
SECURITY_SENSITIVE_COLUMNS=password,secret,token
```

`.env`·`.encenv`에는 **인라인 주석을 쓰지 않는다**(`KEY=value  # 설명` 금지). 주석은 별도 줄에 둔다.

### 3.4 설정 우선순위 — "바꿨는데 안 바뀐다"의 1순위 원인

같은 키가 여러 곳에 있으면 **OS 환경변수가 항상 이긴다.** 파일끼리는 **설정 그룹마다 읽는 파일이 다르다** —
`.encenv`를 읽는 설정 클래스는 24개 중 **6개뿐**이다(`src/config.py`의 클래스별 `env_file`).

| 그룹(접두) | 읽는 파일 | 같은 키가 `.env`·`.encenv` 둘 다에 있으면 |
|---|---|---|
| `ADMIN_`·`AUTH_`·`LLM_`·`ORCHESTRATOR_`·`REDIS_`·`WORKB_` | `.env` + `.encenv` | `.encenv`가 이긴다 |
| 나머지 18개 — `ALARM_`·`API_`·`AUDIT_`·`COMPOSITE_`·`DBHUB_`·`DRM_`·`MULTI_DB_`·`NOISE_` 등 | `.env`만 | **`.encenv`의 값은 무시된다** |

그래서 **시크릿이 아닌 설정은 `.env`에 둔다.** `.encenv`는 위 6개 그룹의 시크릿 전용이다.
실측(2026-09-15): `.env`=false · `.encenv`=true 일 때 `AUTH_ENABLED`는 `true`, `ALARM_ENABLED`·`TEXT2SQL_MULTI_CANDIDATE`는 `false`.

| 상황 | 실제 적용값 |
|---|---|
| `.env`에 `AUTH_ENABLED=false`, `.encenv`에 `AUTH_ENABLED=true` | `true` |
| 셸에 `export AUTH_ENABLED=true`(PowerShell `$env:AUTH_ENABLED="true"`)가 남아 있음 | 파일 값과 무관하게 `true` |

- 설정 파일은 **서버 기동 시 1회** 읽는다. 값을 바꾸면 서버를 재시작한다.
- `.env`·`.encenv`는 **현재 작업 디렉터리 기준**으로 찾는다. 서버·스크립트는 프로젝트 루트에서 실행한다.
- PowerShell의 `$env:` 값은 **그 창을 닫을 때까지 남는다.** 앞서 설정한 값이 다음 실행을 조용히 덮는 일이 잦다.
  지울 때: `Remove-Item Env:AUTH_ENABLED` (POSIX: `unset AUTH_ENABLED`).
- **실제 적용값 확인**: 관리자 화면의 「환경변수 설정」 탭이 키마다 실효값과 출처(OS env·`.encenv` 덮어쓰기)를 표시한다.
- 파일 값은 OS 환경변수에 주입되지 않는다. 그래서 **OS 환경변수로만 읽는 키**는 `.env`·`.encenv`에 적어도
  효과가 없다 — 예: 접두 없는 `FABRIX_API_KEY`, 벤치 스위프의 `BENCH_USER_ID`/`BENCH_USER_PASSWORD`.

### 3.5 인증 설정 (AUTH_ENABLED)

`AUTH_ENABLED` 하나로 **개발 모드와 운영 모드**가 갈린다. 폐쇄망 운영 서버는 운영 모드다.

| | `AUTH_ENABLED=false` (기본 · 개발) | `AUTH_ENABLED=true` (운영) |
|---|---|---|
| 웹·API 접근 | 로그인 없이 전 기능 사용(익명 사용자) | 로그인 필수. 토큰 없는 요청은 401 |
| 시크릿 미설정 | 기동마다 난수 시크릿을 만든다(재시작하면 기존 토큰 무효) | **기동 거부** |
| 사용자 계정 | 불필요 | 인증 DB에 있어야 한다 |

#### 3.5.1 키와 두는 파일

비밀값은 `.encenv`, 나머지는 `.env`에 둔다(두 파일 모두 읽히지만 비밀값을 `.env`에 섞지 않는다).

| 키 | 파일 | 운영 모드 | 설명 |
|---|---|---|---|
| `AUTH_ENABLED` | `.env` | — | `true` = 운영 모드. 미설정이면 `false` |
| `ADMIN_USERNAME` | `.env` | **필수** | 운영자(break-glass) 아이디 |
| `ADMIN_PASSWORD` | `.encenv` | **필수** | 운영자 비밀번호. 기본값 없음 |
| `ADMIN_JWT_SECRET` | `.encenv` | **필수** | 운영자 토큰 서명 키 |
| `AUTH_JWT_SECRET` | `.encenv` | **필수** | 사용자 토큰 서명 키. **`ADMIN_JWT_SECRET`과 다른 값** |
| `AUTH_AUTH_DB_URL` | `.env` | 사실상 필수 | 사용자 계정 DB(PostgreSQL). 비우면 `DB_CONNECTION_STRING`을 쓴다. `DB_BACKEND=dbhub`라 `DB_CONNECTION_STRING`도 비어 있으면 **사용자 로그인이 전부 503** |
| `ADMIN_JWT_EXPIRE_HOURS` / `AUTH_JWT_EXPIRE_HOURS` | `.env` | | 토큰 유효시간(기본 24 / 8시간) |
| `AUTH_MAX_LOGIN_ATTEMPTS` / `AUTH_LOCKOUT_MINUTES` | `.env` | | 연속 실패 잠금(기본 5회 / 30분) |
| `AUTH_PASSWORD_MIN_LENGTH` | `.env` | | 가입 비밀번호 최소 길이(기본 8) |

#### 3.5.2 설정 예 (운영 모드)

```dotenv
# .env
AUTH_ENABLED=true
ADMIN_USERNAME=admin
AUTH_AUTH_DB_URL=postgresql://infra_user:password@localhost:5432/infra_db
```

```dotenv
# .encenv
ADMIN_PASSWORD=<운영자 비밀번호>
ADMIN_JWT_SECRET=<시크릿 A>
AUTH_JWT_SECRET=<시크릿 B — A와 다른 값>
```

시크릿 생성(두 번 실행해 서로 다른 값을 쓴다):

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

#### 3.5.3 기동 시 일어나는 일

1. **필수 키 확인** — 하나라도 없으면 서버가 뜨지 않고 로그에
   `운영 모드(AUTH_ENABLED=true) 기동 거부 — 다음을 .env/.encenv에 설정하세요: …`가 남는다.
2. **인증 DB 연결** — 사용자·감사 테이블이 없으면 자동 생성한다. 연결에 실패하면 서버는 뜨지만
   `인증 DB 초기화 실패 (인증 기능 비활성)` 경고가 남고 사용자 로그인은 503이 된다.
3. **관리자 계정 자동 생성** — DB에 활성 관리자가 한 명도 없으면 `ADMIN_USERNAME`/`ADMIN_PASSWORD`로
   관리자 계정 1개를 만든다(로그 `seed admin 생성 완료`). **최초 1회만** 만들며, 나중에 `ADMIN_PASSWORD`를
   바꿔도 이 DB 계정의 비밀번호는 바뀌지 않는다.

#### 3.5.4 계정 두 종류

| | 운영자(break-glass) | 사용자 |
|---|---|---|
| 로그인 화면 | `/admin/login` | `/login` |
| API | `POST /api/v1/admin/login` · 본문 `{"username", "password"}` | `POST /api/v1/auth/login` · 본문 **`{"user_id", "password"}`** |
| 대조 대상 | 설정의 `ADMIN_USERNAME`/`ADMIN_PASSWORD` 그대로(DB 불필요) | 인증 DB의 계정 |
| 쓰임 | 관리자 화면·설정 조회(DB 장애 시 비상 진입) | 질의(`/api/v1/query/*`) |
| 계정 만들기 | 설정 파일에 적는다 | `/register` 화면 가입(즉시 활성·일반 권한) 또는 `POST /api/v1/auth/register` `{"user_id", "username", "password"}`. 3.5.3-3에서 생성된 관리자 계정(아이디 = `ADMIN_USERNAME`)도 쓸 수 있다 |

두 토큰은 서로 다른 시크릿으로 서명되므로 **운영자 토큰으로는 질의할 수 없다.**

#### 3.5.5 자주 겪는 문제

| 증상 | 원인 | 조치 |
|---|---|---|
| 서버가 뜨지 않고 로그에 `기동 거부 — 다음을 … 설정하세요` | 필수 키 누락 | 메시지에 나열된 키를 3.5.1의 파일에 추가 |
| 로그인 503 `인증 서비스를 사용할 수 없습니다` | 인증 DB 없음·연결 실패 | `AUTH_AUTH_DB_URL` 확인 · 기동 로그의 `인증 DB 초기화 실패` |
| 로그인 401 `ID 또는 비밀번호가 올바르지 않습니다` | 계정 없음·비밀번호 불일치·비활성 계정 | `/register` 가입 또는 비밀번호 확인 |
| 로그인 423 `계정이 잠겼습니다` | 연속 실패(기본 5회) | 잠금 시간(기본 30분)이 지난 뒤 로그인하면 풀린다 |
| 로그인 422 | 본문 키 오류(사용자 로그인에 `username`을 보냄) | 3.5.4의 본문 키 |
| `.env`를 고쳤는데 그대로 | OS 환경변수·`.encenv`가 덮음 / 재시작 안 함 | 3.4 |

#### 3.5.6 테스트 러너의 인증

시나리오 러너(`python -m scripts.scenario`)와 벤치 스위프(`python -m scripts.bench --sweep`)는 프로파일마다
서버를 새로 띄워 **스스로 로그인한다.** 사용자 계정을 넘기는 방법이 둘이 다르므로 각 가이드를 따른다 —
`plans/94-WIP-feature-perf-scenario-suite.md` 「▶ 실행 가이드」 ⑨ · `plans/93-WIP-benchmark-driven-config-simplification.md` 「실행 가이드 ① 퀵」 2절.

---

## 4. 데이터베이스 설정

### 4.1 PostgreSQL 설치

#### RHEL / CentOS / Rocky Linux

```bash
# PostgreSQL 16 리포지터리 추가
sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-$(rpm -E %rhel)-x86_64/pgdg-redhat-repo-latest.noarch.rpm

# 기본 내장 PostgreSQL 모듈 비활성화 (충돌 방지)
sudo dnf -qy module disable postgresql

# PostgreSQL 16 설치
sudo dnf install -y postgresql16-server postgresql16

# DB 클러스터 초기화
sudo /usr/pgsql-16/bin/postgresql-16-setup initdb

# 서비스 시작 및 자동 시작 등록
sudo systemctl start postgresql-16
sudo systemctl enable postgresql-16
```

#### Ubuntu / Debian

```bash
# PostgreSQL 공식 리포지터리 추가
sudo sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
sudo apt-get update

# PostgreSQL 16 설치
sudo apt-get install -y postgresql-16

# 서비스 확인 (Ubuntu에서는 설치 시 자동 시작)
sudo systemctl status postgresql
```

#### macOS (Homebrew)

```bash
brew install postgresql@16
brew services start postgresql@16
```

### 4.2 PostgreSQL 기본 설정

#### 인증 설정 (`pg_hba.conf`)

외부 또는 로컬 접속을 허용하려면 `pg_hba.conf`를 수정한다:

```bash
# 설정 파일 위치 확인
sudo -u postgres psql -c "SHOW hba_file;"
```

파일 하단에 추가 (환경에 맞게 IP 대역 조정):

```
# TYPE  DATABASE        USER            ADDRESS                 METHOD
host    infra_db        infra_user      127.0.0.1/32            scram-sha-256
host    infra_db        infra_user      0.0.0.0/0               scram-sha-256
```

#### 네트워크 리스닝 설정 (`postgresql.conf`)

원격 접속이 필요한 경우:

```bash
# 설정 파일 위치 확인
sudo -u postgres psql -c "SHOW config_file;"
```

```ini
# 모든 인터페이스에서 리스닝 (기본값: localhost)
listen_addresses = '*'

# 포트 (기본값 5432, 변경 시 .env도 함께 수정)
port = 5432
```

설정 변경 후 재시작:

```bash
sudo systemctl restart postgresql-16    # RHEL 계열
sudo systemctl restart postgresql       # Ubuntu/Debian
brew services restart postgresql@16     # macOS
```

### 4.3 메인 인프라 DB 생성

```bash
# postgres 사용자로 전환
sudo -u postgres psql

-- 사용자 생성
CREATE USER infra_user WITH PASSWORD 'password';

-- 데이터베이스 생성
CREATE DATABASE infra_db OWNER infra_user;

-- 권한 부여
GRANT ALL PRIVILEGES ON DATABASE infra_db TO infra_user;

-- psql 종료
\q
```

#### 초기 스키마 및 데이터 적용

프로젝트에 포함된 초기화 스크립트를 순서대로 실행한다:

```bash
# 프로젝트 루트에서 실행
psql -h localhost -U infra_user -d infra_db -f db/init/01_schema.sql
psql -h localhost -U infra_user -d infra_db -f db/init/02_seed_data.sql
psql -h localhost -U infra_user -d infra_db -f db/init/03_auth_tables.sql
```

각 스크립트의 역할:

| 파일 | 내용 |
|------|------|
| `01_schema.sql` | 테이블 스키마 생성 (servers, cpu_metrics, memory_metrics 등) |
| `02_seed_data.sql` | 샘플 데이터 삽입 |
| `03_auth_tables.sql` | 사용자 인증 테이블 |

**접속 및 테이블 확인:**

```bash
psql -h localhost -U infra_user -d infra_db -c "\dt"
```

### 4.4 Polestar DB 생성 (테스트용)

Polestar EAV 구조 테스트를 위한 별도 데이터베이스. 같은 PostgreSQL 인스턴스에 생성하거나 별도 인스턴스를 사용할 수 있다.

#### 같은 인스턴스에 생성하는 경우

```bash
sudo -u postgres psql

-- 사용자 생성
CREATE USER polestar_user WITH PASSWORD 'password';

-- 데이터베이스 생성
CREATE DATABASE infradb OWNER polestar_user;

GRANT ALL PRIVILEGES ON DATABASE infradb TO polestar_user;

\q
```

```bash
# Polestar 초기화 스크립트 적용
psql -h localhost -U polestar_user -d infradb -f testdata/pg/init/01_schema.sql
# (init 디렉토리에 추가 스크립트가 있으면 순서대로 실행)
```

#### 별도 인스턴스(다른 포트)를 사용하는 경우

`postgresql.conf`에서 포트를 변경하거나, 별도 데이터 디렉토리로 인스턴스를 추가 구동한다. `.env`의 연결 문자열에서 포트를 맞춘다.

### 4.5 DB2 설정 (선택)

DB2를 사용하는 경우 IBM DB2 서버가 별도로 설치되어 있어야 한다.

```bash
# DB2 인스턴스에서 데이터베이스 생성
su - db2inst1
db2 CREATE DATABASE infradb AUTOMATIC STORAGE YES USING CODESET UTF-8 TERRITORY KR
db2 CONNECT TO infradb
db2 -tvf /path/to/db2/init/schema.sql
db2 CONNECT RESET
```

| 항목 | 기본 값 |
|------|---------|
| 포트 | **50000** |
| 데이터베이스 | `infradb` |
| 사용자 | `db2inst1` |

### 4.6 연결 문자열 매핑 요약

> 호스트, 포트, 비밀번호는 실제 서버 환경에 맞게 수정한다.

| DB | 연결 문자열 (.env) | 기본 포트 |
|----|-------------------|-----------|
| infra_db (PostgreSQL) | `postgresql://infra_user:password@<DB_HOST>:5432/infra_db` | 5432 |
| polestar (PostgreSQL) | `postgresql://polestar_user:password@<DB_HOST>:5432/infradb` | 5432 |
| infra_db2 (DB2) | `DATABASE=infradb;HOSTNAME=<DB_HOST>;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=<password>;` | 50000 |

---

## 5. Redis 설정

스키마 캐시 저장소로 Redis를 사용한다.

### 5.1 Redis 설치

#### RHEL / CentOS / Rocky Linux

```bash
# EPEL 리포지터리 활성화 (Redis 패키지 제공)
sudo dnf install -y epel-release

# Redis 설치
sudo dnf install -y redis

# 서비스 시작 및 자동 시작 등록
sudo systemctl start redis
sudo systemctl enable redis
```

#### Ubuntu / Debian

```bash
# Redis 공식 리포지터리 추가 (최신 7.x 설치)
curl -fsSL https://packages.redis.io/gpg | sudo gpg --dearmor -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/redis.list
sudo apt-get update

# Redis 설치
sudo apt-get install -y redis-server

# 서비스 시작
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

#### macOS (Homebrew)

```bash
brew install redis
brew services start redis
```

### 5.2 Redis 설정 (`redis.conf`)

설정 파일 위치:
- RHEL 계열: `/etc/redis/redis.conf` 또는 `/etc/redis.conf`
- Ubuntu/Debian: `/etc/redis/redis.conf`
- macOS (Homebrew): `/opt/homebrew/etc/redis.conf`

프로젝트에서 권장하는 설정값 (`redis/redis.conf` 참고):

```ini
# --- 바인딩 ---
# 로컬만 허용 (기본값), 원격 접속 필요 시 0.0.0.0 또는 서버 IP 추가
bind 127.0.0.1

# 포트 (기본 6379)
port 6379

# --- 비밀번호 (운영 환경에서는 반드시 설정) ---
# requirepass your-redis-password

# --- 영속성: RDB 스냅샷 ---
save 3600 1
save 300 100
save 60 10000

dbfilename dump.rdb
dir /var/lib/redis          # 데이터 저장 디렉토리 (OS별 상이)

stop-writes-on-bgsave-error yes
rdbcompression yes
rdbchecksum yes

# --- 영속성: AOF (Append Only File) ---
appendonly yes
appendfilename "appendonly.aof"
appendfsync everysec
no-appendfsync-on-rewrite no
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb

# --- 메모리 ---
maxmemory 256mb
maxmemory-policy noeviction

# --- 로깅 ---
loglevel notice
```

설정 변경 후 재시작:

```bash
sudo systemctl restart redis          # RHEL 계열
sudo systemctl restart redis-server   # Ubuntu/Debian
brew services restart redis           # macOS
```

### 5.3 .env 설정

```dotenv
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_SSL=false
REDIS_SOCKET_TIMEOUT=5
# 비밀번호 설정 시 .encenv에서 관리
# REDIS_PASSWORD=your-redis-password
```

### 5.4 상태 확인

```bash
redis-cli ping
# 출력: PONG

# 비밀번호 설정 시
redis-cli -a your-redis-password ping
```

### 5.5 스키마 캐시 백엔드 선택

Redis가 없는 환경에서는 파일 캐시로 전환 가능:

```dotenv
SCHEMA_CACHE_BACKEND=file
SCHEMA_CACHE_CACHE_DIR=.cache/schema
```

---

## 6. MCP 서버 (DBHub) 설정

> `DB_BACKEND=dbhub`으로 설정한 경우에만 필요. `DB_BACKEND=direct`면 이 섹션을 건너뛰어도 된다.

MCP 서버는 데이터베이스에 대한 읽기 전용 프록시 역할을 한다. 별도 프로세스로 실행하며, 메인 애플리케이션이 SSE(Server-Sent Events)로 통신한다.

### 6.1 MCP 서버 환경변수

```bash
cd mcp_server
cp .env.example .env
```

`mcp_server/.env` 핵심 설정:

```dotenv
# 서버 바인딩
SERVER_HOST=0.0.0.0
SERVER_PORT=9099
SERVER_TRANSPORT=sse

# DB 연결 문자열 (사용할 소스만 주석 해제, 호스트/포트/비밀번호는 실제 환경에 맞게 수정)
INFRA_DB_CONNECTION=postgresql://infra_user:password@<DB_HOST>:5432/infra_db
POLESTAR_CONNECTION=postgresql://polestar_user:password@<DB_HOST>:5432/infradb
# INFRA_DB2_CONNECTION=DATABASE=infradb;HOSTNAME=<DB_HOST>;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=<password>;
```

### 6.2 config.toml 데이터소스 정의

`mcp_server/config.toml`에 데이터소스가 미리 정의되어 있다. 연결 문자열이 `.env`에 설정된 소스만 활성화된다.

**소스명 일치 규칙** (불일치 시 "알 수 없는 소스" 에러):

```
config.toml의 name  →  .env 환경변수명             →  클라이언트 .env 참조명
─────────────────────────────────────────────────────────────────────────
"infra_db"          →  INFRA_DB_CONNECTION          →  DBHUB_SOURCE_NAME=infra_db
"polestar"          →  POLESTAR_CONNECTION           →  ACTIVE_DB_IDS에 포함
"infra_db2"         →  INFRA_DB2_CONNECTION          →  ACTIVE_DB_IDS에 포함
```

### 6.3 MCP 서버 실행

```bash
cd mcp_server
python -m mcp_server
```

서버가 `http://localhost:9099/sse`에서 대기한다.

### 6.4 메인 앱에서 MCP 서버 연결 설정

루트 `.env`에서:

```dotenv
DB_BACKEND=dbhub
DBHUB_SERVER_URL=http://localhost:9099/sse
DBHUB_SOURCE_NAME=infra_db
DBHUB_MCP_CALL_TIMEOUT=60
```

---

## 7. LLM 설정

세 가지 LLM 제공자를 지원한다.

### 7.1 Ollama (로컬 LLM, 기본값)

Ollama를 설치하고 모델을 다운로드한다:

```bash
# Ollama 설치 (macOS)
brew install ollama

# Ollama 서버 시작
ollama serve

# 모델 다운로드 (예시)
ollama pull llama3.1:8b
```

`.env` 설정:

```dotenv
LLM_PROVIDER=ollama
LLM_MODEL=llama3.1:8b
LLM_OLLAMA_BASE_URL=http://localhost:11434
LLM_OLLAMA_TIMEOUT=180
```

### 7.2 Gemini

```dotenv
LLM_PROVIDER=gemini
LLM_GEMINI_MODEL=gemini-2.0-flash
```

`.encenv`에 API 키 설정:

```dotenv
LLM_GEMINI_API_KEY=your-gemini-api-key
```

추가 의존성 설치:

```bash
pip install -e ".[gemini]"
```

### 7.3 FabriX

```dotenv
LLM_PROVIDER=fabrix
FABRIX_BASE_URL=https://your-fabrix-endpoint
FABRIX_CHAT_MODEL=model-name
```

`.encenv`에 키 설정:

```dotenv
FABRIX_API_KEY=your-api-key
FABRIX_CLIENT_KEY=your-client-key
```

---

## 8. 애플리케이션 실행

### 8.1 인프라 서비스 상태 확인

애플리케이션 실행 전에 PostgreSQL과 Redis가 정상 동작하는지 확인한다:

```bash
# 1. PostgreSQL 서비스 확인
sudo systemctl status postgresql-16    # RHEL 계열
sudo systemctl status postgresql       # Ubuntu/Debian

# 2. Redis 서비스 확인
sudo systemctl status redis            # RHEL 계열
sudo systemctl status redis-server     # Ubuntu/Debian

# 3. DB 접속 확인
psql -h localhost -U infra_user -d infra_db -c "SELECT 1;"

# 4. Redis 접속 확인
redis-cli ping

# 5. (선택) MCP 서버 시작 — DB_BACKEND=dbhub 사용 시
cd mcp_server && python -m mcp_server &
cd ..
```

### 8.2 메인 애플리케이션 실행

```bash
# API 서버 모드 (http://localhost:8000)
python -m src.main --server

# CLI 단일 질의 모드
python -m src.main --query "서버 목록을 보여줘"

# 대화형 CLI 모드
python -m src.main
```

### 8.3 실행 확인

API 서버 시작 후 브라우저에서 접속:

- 메인 UI: `http://localhost:8000`
- 관리자 대시보드: `http://localhost:8000/admin`

---

## 9. 설정 검증

### 9.1 인프라 상태 확인 체크리스트

```bash
# PostgreSQL 서비스 상태 확인
sudo systemctl is-active postgresql-16    # RHEL 계열
sudo systemctl is-active postgresql       # Ubuntu/Debian

# PostgreSQL 연결 확인
pg_isready -h localhost -U infra_user -d infra_db
# 출력: localhost:5432 - accepting connections

# Redis 연결 확인
redis-cli ping
# 출력: PONG

# (선택) Polestar DB 확인
pg_isready -h localhost -U polestar_user -d infradb

# 포트 리스닝 상태 확인
ss -tlnp | grep -E '5432|6379|8000|9099'
```

### 9.2 아키텍처 검사

코드 계층 의존성 위반 여부를 확인한다:

```bash
python scripts/arch_check.py
```

### 9.3 Quick Start 요약

최소한의 설정으로 빠르게 시작하는 순서:

```bash
# 1. PostgreSQL, Redis 서비스가 실행 중인지 확인
pg_isready -h localhost
redis-cli ping

# 2. DB 및 사용자 생성 (최초 1회)
sudo -u postgres psql -c "CREATE USER infra_user WITH PASSWORD 'password';"
sudo -u postgres psql -c "CREATE DATABASE infra_db OWNER infra_user;"
psql -h localhost -U infra_user -d infra_db -f db/init/01_schema.sql
psql -h localhost -U infra_user -d infra_db -f db/init/02_seed_data.sql
psql -h localhost -U infra_user -d infra_db -f db/init/03_auth_tables.sql

# 3. 가상환경 & 의존성
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# 4. 환경변수
cp .env.example .env
# .env에서 DB_CONNECTION_STRING, REDIS_PORT 등 실제 환경에 맞게 수정

# 5. Ollama 모델 준비
ollama pull llama3.1:8b

# 6. 서버 실행
python -m src.main --server
```
