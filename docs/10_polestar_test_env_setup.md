# Polestar DB 테스트 환경 초기 데이터 설정 가이드

## 목차

1. [개요](#1-개요)
2. [사전 요구사항](#2-사전-요구사항)
3. [아키텍처 개요](#3-아키텍처-개요)
4. [PostgreSQL 환경 구성 (권장)](#4-postgresql-환경-구성-권장)
   - [4-A. Docker 방식](#4-a-docker-방식)
   - [4-B. 서버 직접 설치 방식](#4-b-서버-직접-설치-방식)
5. [DB2 환경 구성](#5-db2-환경-구성)
   - [5-A. Docker 방식](#5-a-docker-방식)
   - [5-B. 서버 직접 설치 방식](#5-b-서버-직접-설치-방식)
6. [테스트 데이터 생성기](#6-테스트-데이터-생성기)
7. [데이터 검증](#7-데이터-검증)
8. [Redis 스키마 캐시 설정](#8-redis-스키마-캐시-설정)
   - [8-A. Docker 방식](#8-a-docker-방식)
   - [8-B. 서버 직접 설치 방식](#8-b-서버-직접-설치-방식)
9. [MCP 서버 연동 설정](#9-mcp-서버-연동-설정)
10. [애플리케이션 환경변수 설정](#10-애플리케이션-환경변수-설정)
11. [전체 통합 실행 순서](#11-전체-통합-실행-순서)
    - [11-A. Docker 기반 통합 실행](#11-a-docker-기반-통합-실행)
    - [11-B. 서버 직접 설치 기반 통합 실행](#11-b-서버-직접-설치-기반-통합-실행)
12. [데이터 정리 및 초기화](#12-데이터-정리-및-초기화)
13. [트러블슈팅](#13-트러블슈팅)

---

## 1. 개요

Polestar DB는 인프라 자원(서버, CPU, 메모리, 디스크, 네트워크)을 **EAV(Entity-Attribute-Value) 패턴**과 **계층 구조**로 관리하는 데이터베이스입니다.

테스트 환경에서는 2개 테이블에 대한 초기 데이터를 설정합니다:

| 테이블 | 역할 | 테스트 데이터 행 수 |
|--------|------|-------------------|
| `polestar.cmm_resource` | 인프라 리소스 계층 구조 | 약 1,115행 |
| `polestar.core_config_prop` | 리소스별 EAV 설정 속성 | 360행 |

테스트 데이터는 **서버 30대**(WEB 10, WAS 10, DB 10)와 각 서버의 하위 리소스(CPU, 메모리, 디스크, 파일시스템, 네트워크 인터페이스 등)로 구성됩니다.

### DB 엔진 선택지

| 환경 | DB 엔진 | 구성 방식 | 디렉토리 / 포트 | 비고 |
|------|---------|----------|----------------|------|
| **개발 (macOS)** | PostgreSQL 16 | Docker | `testdata/pg/` / 5434 | Apple Silicon 호환, 권장 |
| **개발 (Linux)** | PostgreSQL 14+ | Docker 또는 서버 직접 설치 | 5432 또는 지정 포트 | 범용 |
| **개발 (Linux)** | IBM DB2 11.5+ | Docker 또는 서버 직접 설치 | `db2/` / 50000 | x86_64 전용 |
| **운영** | 실제 Polestar DB | 기존 인프라 | - | 읽기 전용 접속 |

> macOS (특히 Apple Silicon)에서는 DB2 이미지가 정상 동작하지 않으므로 **PostgreSQL 버전을 사용**하세요.

### 구성 방식 비교: Docker vs 서버 직접 설치

| 항목 | Docker | 서버 직접 설치 |
|------|--------|---------------|
| 설치 편의성 | `docker compose up -d` 한 줄 | 패키지 설치 + 설정 필요 |
| 초기 데이터 | `init/` 디렉토리 자동 실행 (PostgreSQL) | SQL 수동 실행 |
| 환경 격리 | 완전 격리 (컨테이너) | 호스트 시스템에 직접 설치 |
| 포트 충돌 | 매핑으로 회피 가능 | 기존 서비스와 충돌 주의 |
| 운영 환경 유사성 | 낮음 | 높음 |
| 권장 대상 | 로컬 개발, CI/CD | 스테이징, 운영 유사 테스트 |

---

## 2. 사전 요구사항

### Docker 방식

| 소프트웨어 | 최소 버전 | 용도 |
|-----------|----------|------|
| Docker Desktop 또는 OrbStack | Docker Engine 20+ | DB/Redis 컨테이너 실행 |
| Docker Compose | v2.0+ (Docker Desktop 포함) | 멀티 컨테이너 관리 |
| Python | 3.10+ | 데이터 생성기, 애플리케이션 실행 |

### 서버 직접 설치 방식

| 소프트웨어 | 최소 버전 | 용도 |
|-----------|----------|------|
| PostgreSQL | 14+ (권장 16) | Polestar DB |
| 또는 IBM DB2 | 11.5+ | Polestar DB (x86_64 전용) |
| Redis | 6+ (권장 7) | 스키마 캐시 |
| Python | 3.10+ | 데이터 생성기, 애플리케이션 실행 |
| psql (PostgreSQL 클라이언트) | - | SQL 실행, 접속 테스트 |

### 리소스 요구사항

| 항목 | PostgreSQL | DB2 |
|------|-----------|-----|
| 메모리 | 256MB | 4GB (최소 2GB) |
| 디스크 | 100MB | 2GB |
| CPU | 제한 없음 | x86_64 필수 |

### 포트 사용 현황

아래 포트가 다른 서비스와 충돌하지 않는지 확인하세요:

| 포트 | 서비스 | Docker 디렉토리 | 서버 직접 설치 시 |
|------|--------|-----------------|-----------------|
| 5432 | PostgreSQL 기본 포트 | - | 직접 설치 시 기본값 |
| 5433 | 인프라 모니터링 DB (PostgreSQL) | `db/` | - |
| 5434 | Polestar DB (PostgreSQL) | `testdata/pg/` | - |
| 50000 | Polestar DB (DB2) | `db2/` | DB2 기본 포트 |
| 6379 | Redis 기본 포트 | - | 직접 설치 시 기본값 |
| 6380 | Redis (Docker) | `redis/` | - |
| 8000 | 애플리케이션 API 서버 | - | - |
| 9099 | MCP 서버 (DBHub) | `mcp_server/` | - |

---

## 3. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────┐
│ 애플리케이션 (FastAPI, port 8000)                         │
│   ├── DB_BACKEND=direct → PostgreSQL/DB2 직접 연결        │
│   └── DB_BACKEND=dbhub  → MCP 서버(DBHub) 경유           │
└───────────────┬──────────────────┬──────────────────────┘
                │                  │
        ┌───────▼───────┐  ┌──────▼──────────┐
        │ PostgreSQL    │  │ MCP 서버 (DBHub) │
        │ (direct 모드) │  │ :9099            │
        └───────────────┘  └──────┬───────────┘
                                  │
                           ┌──────▼──────────┐
                           │ PostgreSQL / DB2 │
                           └─────────────────┘

  DB는 Docker 컨테이너 또는 서버에 직접 설치된 인스턴스 모두 가능
```

### Polestar 데이터 모델

```
cmm_resource (계층 구조)
├── platform.server (서버) ──── resource_conf_id ──→ core_config_prop.configuration_id
│   ├── server.Cpus (CPU 컨테이너)
│   │   └── server.Cpu (개별 CPU 코어)
│   ├── server.Memory (메모리)
│   │   ├── server.OtherMemory
│   │   └── server.VirtualMemory
│   ├── server.Disks (디스크)
│   ├── server.FileSystems (파일시스템 컨테이너)
│   │   └── server.FileSystem (개별 파일시스템)
│   ├── server.NetworkInterfaces (NIC 컨테이너)
│   │   └── server.NetworkInterface (개별 NIC)
│   ├── server.Process (프로세스)
│   ├── server.Netstat (네트워크 세션)
│   ├── server.Other (기타정보)
│   └── management.MonitorGroup (모니터 그룹)
│       ├── server.LogMonitor
│       └── server.ProcessMonitor
└── (DB 서버만) server.Hbas → server.Hba → server.HbaPort
```

### EAV 조인 관계

```
cmm_resource.resource_conf_id = core_config_prop.configuration_id
```

> **주의**: `cmm_resource.id = core_config_prop.configuration_id` 직접 조인은 금지입니다.

---

## 4. PostgreSQL 환경 구성 (권장)

### 4-A. Docker 방식

#### 4-A-1. 컨테이너 실행

```bash
cd testdata/pg
docker compose up -d
```

컨테이너가 시작되면 `init/` 디렉토리의 SQL 파일이 **알파벳 순서대로 자동 실행**됩니다:

| 순서 | 파일 | 내용 |
|------|------|------|
| 1 | `init/01_create_tables.sql` | `polestar` 스키마 + `cmm_resource`, `core_config_prop` 테이블 생성 |
| 2 | `init/02_insert_cmm_resource.sql` | 서버 30대의 리소스 계층 데이터 INSERT (약 1,115행) |
| 3 | `init/03_insert_core_config_prop.sql` | 서버 30대의 EAV 설정 데이터 INSERT (360행) |

#### 4-A-2. 컨테이너 상태 확인

```bash
# 컨테이너 상태 확인
docker ps --filter name=polestar_pg

# 헬스체크 확인
docker inspect --format='{{.State.Health.Status}}' polestar_pg
# 기대값: healthy
```

#### 4-A-3. 접속 정보

| 항목 | 값 |
|------|-----|
| Host | `localhost` |
| Port | `5434` |
| Database | `infradb` |
| Schema | `polestar` |
| User | `polestar_user` |
| Password | `polestar_pass_2024` |
| Connection URI | `postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb` |

#### 4-A-4. 접속 테스트

```bash
# 컨테이너 내부 psql로 접속
docker exec -it polestar_pg psql -U polestar_user -d infradb

# 또는 호스트에서 접속 (psql 설치 필요)
psql postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb
```

#### 4-A-5. 추가 데이터 적용

`05_insert_excel_data.sql`은 `resource_conf_id` 매핑과 추가 리소스 데이터를 포함합니다. 자동 실행되지 않으므로 수동으로 적용합니다:

```bash
docker exec -i polestar_pg psql -U polestar_user -d infradb < 05_insert_excel_data.sql
```

이 스크립트가 수행하는 작업:
- 기존 30대 서버의 `platform.server` 리소스에 `resource_conf_id` 값 설정 (UPDATE 30건)
- 추가 `cmm_resource` INSERT (390행)
- 추가 `core_config_prop` INSERT (362행)

> `resource_conf_id`가 설정되어야 `cmm_resource`와 `core_config_prop` 간의 EAV 조인이 정상 동작합니다.

---

### 4-B. 서버 직접 설치 방식

Docker 없이 서버에 PostgreSQL을 직접 설치하여 Polestar 테스트 DB를 구성하는 방법입니다.

#### 4-B-1. PostgreSQL 설치

**Ubuntu/Debian:**

```bash
# PostgreSQL 16 설치
sudo apt update
sudo apt install -y postgresql-16 postgresql-client-16

# 서비스 시작 및 자동 시작 등록
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

**RHEL/CentOS/Rocky Linux:**

```bash
# PostgreSQL 공식 저장소 추가
sudo dnf install -y https://download.postgresql.org/pub/repos/yum/reporpms/EL-$(rpm -E %{rhel})-x86_64/pgdg-redhat-repo-latest.noarch.rpm

# PostgreSQL 16 설치
sudo dnf install -y postgresql16-server postgresql16

# DB 클러스터 초기화 및 시작
sudo /usr/pgsql-16/bin/postgresql-16-setup initdb
sudo systemctl start postgresql-16
sudo systemctl enable postgresql-16
```

**macOS (Homebrew):**

```bash
brew install postgresql@16
brew services start postgresql@16
```

#### 4-B-2. 데이터베이스 및 사용자 생성

```bash
# postgres 시스템 사용자로 전환 (Linux)
sudo -u postgres psql

# macOS (Homebrew)는 현재 사용자로 바로 접속
# psql postgres
```

psql 프롬프트에서:

```sql
-- 사용자 생성
CREATE USER polestar_user WITH PASSWORD 'polestar_pass_2024';

-- 데이터베이스 생성
CREATE DATABASE infradb OWNER polestar_user ENCODING 'UTF8';

-- 사용자 권한 부여
GRANT ALL PRIVILEGES ON DATABASE infradb TO polestar_user;

\q
```

#### 4-B-3. 원격 접속 허용 (필요 시)

애플리케이션이 다른 서버에서 실행되는 경우 원격 접속을 허용해야 합니다.

**`postgresql.conf` 수정:**

```bash
# 설정 파일 위치 확인
sudo -u postgres psql -c "SHOW config_file;"

# listen_addresses 수정
# listen_addresses = '*'    ← 모든 IP에서 접속 허용
# 또는 특정 IP만: listen_addresses = 'localhost,10.0.1.100'
sudo vi /etc/postgresql/16/main/postgresql.conf   # Ubuntu
# 또는
sudo vi /var/lib/pgsql/16/data/postgresql.conf    # RHEL
```

**`pg_hba.conf` 수정:**

```bash
# 클라이언트 인증 설정 파일
sudo vi /etc/postgresql/16/main/pg_hba.conf   # Ubuntu
# 또는
sudo vi /var/lib/pgsql/16/data/pg_hba.conf    # RHEL
```

파일 끝에 추가:

```
# Polestar 테스트 접속 허용
# TYPE  DATABASE    USER            ADDRESS         METHOD
host    infradb     polestar_user   0.0.0.0/0       md5
```

> 운영 환경에서는 `0.0.0.0/0` 대신 애플리케이션 서버 IP 대역을 지정하세요.

설정 반영:

```bash
sudo systemctl restart postgresql       # Ubuntu
sudo systemctl restart postgresql-16    # RHEL
```

#### 4-B-4. 포트 변경 (선택)

기본 포트(5432)가 이미 사용 중이면 변경할 수 있습니다:

```bash
# postgresql.conf에서 port 변경
# port = 5434

sudo systemctl restart postgresql
```

#### 4-B-5. 스키마 및 테이블 생성

```bash
# polestar_user로 infradb에 접속하여 DDL 실행
psql -U polestar_user -d infradb -h localhost -f testdata/pg/init/01_create_tables.sql
```

또는 호스트가 다른 경우:

```bash
psql postgresql://polestar_user:polestar_pass_2024@<서버IP>:<포트>/infradb \
  -f testdata/pg/init/01_create_tables.sql
```

#### 4-B-6. 테스트 데이터 투입

SQL 파일을 **순서대로** 실행합니다:

```bash
# 접속 URI (서버 환경에 맞게 수정)
POLESTAR_URI="postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb"

# 1. DDL (이미 실행했으면 생략)
psql "$POLESTAR_URI" -f testdata/pg/init/01_create_tables.sql

# 2. CMM_RESOURCE INSERT
psql "$POLESTAR_URI" -f testdata/pg/init/02_insert_cmm_resource.sql

# 3. CORE_CONFIG_PROP INSERT
psql "$POLESTAR_URI" -f testdata/pg/init/03_insert_core_config_prop.sql

# 4. resource_conf_id 매핑 + 추가 데이터
psql "$POLESTAR_URI" -f testdata/pg/05_insert_excel_data.sql
```

#### 4-B-7. 접속 정보 (서버 직접 설치)

| 항목 | 값 (기본값, 환경에 따라 변경) |
|------|-----|
| Host | `localhost` 또는 서버 IP |
| Port | `5432` (기본값) 또는 지정 포트 |
| Database | `infradb` |
| Schema | `polestar` |
| User | `polestar_user` |
| Password | `polestar_pass_2024` |
| Connection URI | `postgresql://polestar_user:polestar_pass_2024@<host>:<port>/infradb` |

#### 4-B-8. 접속 테스트

```bash
psql postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb
```

```sql
-- 스키마 확인
\dn

-- 테이블 확인
\dt polestar.*

-- 서버 수 확인
SELECT COUNT(*) FROM polestar.cmm_resource WHERE resource_type LIKE 'platform.server%';
-- 기대값: 30
```

---

## 5. DB2 환경 구성

> DB2는 x86_64(amd64) 아키텍처에서만 안정적으로 실행됩니다.
> macOS Apple Silicon에서는 에뮬레이션으로 실행 가능하나 불안정합니다. PostgreSQL 사용을 권장합니다.

### 5-A. Docker 방식

#### 5-A-1. 컨테이너 실행

```bash
cd db2
docker compose up -d
```

DB2 컨테이너는 초기화에 시간이 오래 걸립니다 (약 5~10분). `start_period: 600s`로 설정되어 있습니다.

#### 5-A-2. 초기화 확인

```bash
# 헬스체크 상태 확인 (healthy가 될 때까지 대기)
docker inspect --format='{{.State.Health.Status}}' infra_db2

# 로그 확인
docker logs -f infra_db2
```

#### 5-A-3. 접속 정보

| 항목 | 값 |
|------|-----|
| Host | `localhost` |
| Port | `50000` |
| Database | `infradb` |
| Schema | `POLESTAR` (대문자) |
| User | `db2inst1` |
| Password | `db2pass2024` |

#### 5-A-4. 테스트 데이터 투입

DB2 컨테이너는 PostgreSQL처럼 `init/` 자동 실행을 지원하지 않으므로, SQL 파일을 **수동으로** 실행합니다:

```bash
# SQL 파일을 컨테이너에 복사
docker cp testdata/01_create_tables.sql infra_db2:/tmp/
docker cp testdata/02_insert_cmm_resource.sql infra_db2:/tmp/
docker cp testdata/03_insert_core_config_prop.sql infra_db2:/tmp/

# 순서대로 실행
docker exec -i infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 -tvf /tmp/01_create_tables.sql"

docker exec -i infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 -tvf /tmp/02_insert_cmm_resource.sql"

docker exec -i infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 -tvf /tmp/03_insert_core_config_prop.sql"
```

#### 5-A-5. 접속 테스트

```bash
docker exec -it infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 'SELECT COUNT(*) FROM POLESTAR.CMM_RESOURCE'"
```

---

### 5-B. 서버 직접 설치 방식

서버에 DB2를 직접 설치하여 Polestar 테스트 DB를 구성하는 방법입니다.

#### 5-B-1. DB2 설치

> DB2 Community Edition은 무료 사용 가능하나, IBM 계정 등록이 필요합니다.

**RHEL/CentOS/Rocky Linux (x86_64):**

```bash
# 1. 사전 패키지 설치
sudo yum install -y libaio libstdc++ pam numactl

# 2. DB2 설치 파일 다운로드 후 압축 해제
tar -xzf v11.5.9_linuxx64_server_dec.tar.gz
cd server_dec

# 3. 설치 실행
sudo ./db2_install -b /opt/ibm/db2/V11.5

# 4. DB2 인스턴스 생성
sudo /opt/ibm/db2/V11.5/instance/db2icrt -u db2fenc1 db2inst1

# 5. 인스턴스 시작
su - db2inst1 -c "db2start"
```

#### 5-B-2. 데이터베이스 생성

```bash
# db2inst1 사용자로 전환
su - db2inst1

# 데이터베이스 생성
db2 "CREATE DATABASE infradb USING CODESET UTF-8 TERRITORY KR PAGESIZE 32768"

# 데이터베이스 접속
db2 connect to infradb
```

#### 5-B-3. 원격 접속 허용 (필요 시)

```bash
su - db2inst1

# TCP/IP 통신 활성화
db2set DB2COMM=tcpip

# 서비스 포트 설정
db2 update dbm cfg using SVCENAME 50000

# 인스턴스 재시작
db2stop force
db2start
```

방화벽에서 50000 포트를 열어야 합니다:

```bash
sudo firewall-cmd --permanent --add-port=50000/tcp
sudo firewall-cmd --reload
```

#### 5-B-4. 테스트 데이터 투입

```bash
su - db2inst1

# DB 접속
db2 connect to infradb

# SQL 파일 순서대로 실행
db2 -tvf /path/to/testdata/01_create_tables.sql
db2 -tvf /path/to/testdata/02_insert_cmm_resource.sql
db2 -tvf /path/to/testdata/03_insert_core_config_prop.sql
```

#### 5-B-5. DDL 수정 참고 사항

`testdata/01_create_tables.sql`의 원본 DDL에 포함된 운영 전용 옵션이 테스트 환경에서 오류를 발생시킬 수 있습니다:

| 옵션 | 설명 | 조치 |
|------|------|------|
| `COMPRESS YES ADAPTIVE` | 적응형 압축 | DB2 버전에 따라 미지원 — 해당 행 제거 |
| `IN "SSNISND01R" INDEX IN "SSNISND01X"` | 테이블스페이스 지정 | 해당 테이블스페이스가 없으면 제거 |
| `ORGANIZE BY ROW` | 행 기반 구성 | 필요 시 제거 |

#### 5-B-6. 접속 정보 (서버 직접 설치)

| 항목 | 값 (기본값, 환경에 따라 변경) |
|------|-----|
| Host | `localhost` 또는 서버 IP |
| Port | `50000` (기본값) |
| Database | `infradb` |
| Schema | `POLESTAR` (대문자) |
| User | `db2inst1` |
| Password | 설치 시 지정한 비밀번호 |
| ibm_db 연결 문자열 | `DATABASE=infradb;HOSTNAME=<host>;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=<password>;` |

#### 5-B-7. 접속 테스트

```bash
su - db2inst1
db2 connect to infradb
db2 "SELECT COUNT(*) FROM POLESTAR.CMM_RESOURCE"
# 기대값: 약 780 (기본) 또는 1,115 (05 데이터 포함)
```

---

## 6. 테스트 데이터 생성기

### 6.1 SQL 생성기 (`testdata/_generate_sql.py`)

테스트 데이터 SQL 파일(`02_insert_cmm_resource.sql`, `03_insert_core_config_prop.sql`)은 Python 스크립트로 자동 생성됩니다:

```bash
cd testdata
python3 _generate_sql.py
```

**출력 파일**:
- `testdata/02_insert_cmm_resource.sql` — CMM_RESOURCE INSERT (DB2용, PostgreSQL에서도 호환)
- `testdata/03_insert_core_config_prop.sql` — CORE_CONFIG_PROP INSERT (DB2용, PostgreSQL에서도 호환)

### 6.2 생성기 설정

`_generate_sql.py` 상단의 설정값:

| 변수 | 값 | 설명 |
|------|-----|------|
| `CMM_ID_START` | 300001 | CMM_RESOURCE ID 시작 값 |
| `CONFIG_ID_START` | 500001 | CORE_CONFIG_PROP ID 시작 값 |
| `SERVERS` | 30대 | WEB 10, WAS 10, DB 10 |

### 6.3 서버 구성

| 그룹 | 호스트명 | IP 범위 | CPU 코어 | 플랫폼 |
|------|---------|---------|---------|--------|
| WEB (Profile A) | svr-web-01 ~ 10 | 10.0.1.1 ~ 10 | 2~4 | VMware, HPE |
| WAS (Profile B) | svr-was-01 ~ 10 | 10.0.2.1 ~ 10 | 4~8 | VMware, HPE, Dell |
| DB (Profile C) | svr-db-01 ~ 10 | 10.0.3.1 ~ 10 | 8~16 | HPE, Dell |

### 6.4 CORE_CONFIG_PROP 속성 (서버당 12건)

각 서버의 `platform.server` 리소스에 대해 아래 12개 EAV 속성이 생성됩니다:

| 속성명 (name) | 예시 값 | 설명 |
|---------------|---------|------|
| Vendor | VMware, Inc. / HPE / Dell Inc. | 서버 제조사 |
| Model | VMware Virtual Platform / ProLiant DL380 Gen10 Plus | 서버 모델 |
| OSType | LINUX | 운영체제 종류 |
| OSVerson | 3.10.0-957.el7.x86_64 | OS 버전 (Polestar 원본의 오탈자 반영) |
| GMT | GMT+09:00 | 시간대 |
| SerialNumber | VMware-SVRWEB01 | 시리얼 번호 |
| Hostname | svr-web-01 | 호스트명 |
| IPaddress | 10.0.1.1 | IP 주소 |
| AgentVersion | 7.6.26_6 | 에이전트 버전 |
| InstallPath | /fsutil/polestar/agent/NNPAgent/MAgent/ | 설치 경로 |
| AgentID | MA_svr-web-01_20220315091000 | 에이전트 ID |
| OSParameter | kernel.shmmax = 68719476736 | OS 커널 파라미터 |

### 6.5 PostgreSQL용 호환성

DB2용으로 생성된 INSERT SQL은 PostgreSQL에서도 그대로 호환됩니다. PostgreSQL은 unquoted identifier (`POLESTAR.CMM_RESOURCE`)를 소문자 (`polestar.cmm_resource`)로 fold하므로 별도 수정 없이 동작합니다.

다만 **DDL(테이블 생성)은 DB2/PostgreSQL 전용 파일이 별도로 존재**합니다:
- DB2: `testdata/01_create_tables.sql`
- PostgreSQL: `testdata/pg/init/01_create_tables.sql`

---

## 7. 데이터 검증

### 7.1 검증 쿼리 실행

검증 스크립트는 DB 엔진별로 별도 파일이 있습니다:

**PostgreSQL (Docker):**
```bash
docker exec -i polestar_pg psql -U polestar_user -d infradb < testdata/pg/04_verify_data.sql
```

**PostgreSQL (서버 직접 설치):**
```bash
psql postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb \
  -f testdata/pg/04_verify_data.sql
```

**DB2 (Docker):**
```bash
docker cp testdata/04_verify_data.sql infra_db2:/tmp/
docker exec -i infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 -tvf /tmp/04_verify_data.sql"
```

**DB2 (서버 직접 설치):**
```bash
su - db2inst1
db2 connect to infradb
db2 -tvf /path/to/testdata/04_verify_data.sql
```

### 7.2 검증 항목 및 기대값

| # | 검증 항목 | 기대값 |
|---|----------|--------|
| 1 | `cmm_resource` 전체 행 수 | 약 1,115행 (05 데이터 포함 시) |
| 2 | 서버별 행 수 (HOSTNAME 기준) | 30건 (svr-web-01 ~ svr-db-10) |
| 3 | 용도별 서버 수 | WEB 10, WAS 10, DB 10 |
| 4 | `resource_type`별 분포 | 15종 이상 |
| 5 | `avail_status` 분포 | 0(정상) 약 92~95%, 1(비정상) 약 5~8% |
| 6 | `core_config_prop` 전체 행 수 | 360행 |
| 7 | `configuration_id`별 설정 항목 수 | 301~330 각각 12건 |
| 8 | svr-web-01 설정 값 확인 | 12개 속성 출력 |
| 9 | 제조사(Vendor)별 서버 수 | VMware, HPE, Dell 3종 |
| 10 | OS 버전 분포 | 커널 2~3종 |

### 7.3 핵심 EAV 조인 검증 쿼리

데이터가 올바르게 설정되었는지 확인하는 실제 사용 패턴 쿼리입니다.
Docker/서버 관계없이 SQL은 동일하며, 접속 방식만 다릅니다:

```sql
-- 서버 목록 조회 (호스트명 + OS + 벤더)
SELECT
  r.hostname,
  cc_os.stringvalue_short AS os_type,
  cc_vendor.stringvalue_short AS vendor
FROM polestar.cmm_resource r
LEFT JOIN polestar.core_config_prop cc_os
  ON r.resource_conf_id = cc_os.configuration_id AND cc_os.name = 'OSType'
LEFT JOIN polestar.core_config_prop cc_vendor
  ON r.resource_conf_id = cc_vendor.configuration_id AND cc_vendor.name = 'Vendor'
WHERE r.resource_type LIKE 'platform.server%'
  AND r.dtime IS NULL
ORDER BY r.hostname
LIMIT 10;
```

이 쿼리가 30개 서버의 OS 종류와 벤더를 정상 반환하면 EAV 조인이 올바르게 동작하는 것입니다.

> **주의**: `resource_conf_id`가 NULL이면 EAV 조인 결과도 NULL입니다. `05_insert_excel_data.sql`을 적용하여 `resource_conf_id`를 설정해야 합니다.

---

## 8. Redis 스키마 캐시 설정

스키마 캐시는 DB 스키마 정보를 Redis에 저장하여 반복 조회를 방지합니다.

### 8-A. Docker 방식

#### 8-A-1. Redis 컨테이너 실행

```bash
cd redis
docker compose up -d
```

#### 8-A-2. 접속 확인

```bash
docker exec -it collectorinfra-redis redis-cli ping
# 기대값: PONG
```

#### 8-A-3. 애플리케이션 환경변수

```bash
# 프로젝트 루트 .env
REDIS_HOST=localhost
REDIS_PORT=6380          # Docker 매핑 포트
REDIS_DB=0
SCHEMA_CACHE_BACKEND=redis
SCHEMA_CACHE_ENABLED=true
```

---

### 8-B. 서버 직접 설치 방식

#### 8-B-1. Redis 설치

**Ubuntu/Debian:**

```bash
sudo apt update
sudo apt install -y redis-server

# 서비스 시작 및 자동 시작 등록
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

**RHEL/CentOS/Rocky Linux:**

```bash
sudo dnf install -y redis

sudo systemctl start redis
sudo systemctl enable redis
```

**macOS (Homebrew):**

```bash
brew install redis
brew services start redis
```

#### 8-B-2. Redis 설정 (선택)

비밀번호 설정이 필요한 경우:

```bash
# redis.conf 위치 확인
redis-cli CONFIG GET dir

# redis.conf 수정
sudo vi /etc/redis/redis.conf    # Ubuntu
# 또는
sudo vi /etc/redis.conf          # RHEL
```

```conf
# 비밀번호 설정 (선택)
requirepass your_redis_password

# 원격 접속 허용 (필요 시)
bind 0.0.0.0
# 또는 특정 IP만: bind 127.0.0.1 10.0.1.100
```

설정 반영:

```bash
sudo systemctl restart redis-server   # Ubuntu
sudo systemctl restart redis          # RHEL
```

#### 8-B-3. 접속 확인

```bash
redis-cli ping
# 기대값: PONG

# 비밀번호 설정 시
redis-cli -a your_redis_password ping
```

#### 8-B-4. 애플리케이션 환경변수

```bash
# 프로젝트 루트 .env
REDIS_HOST=localhost           # 또는 Redis 서버 IP
REDIS_PORT=6379                # 서버 직접 설치 시 기본 포트
REDIS_DB=0
REDIS_PASSWORD=                # 비밀번호 설정 시 입력 (보안을 위해 .encenv 권장)
SCHEMA_CACHE_BACKEND=redis
SCHEMA_CACHE_ENABLED=true
```

---

## 9. MCP 서버 연동 설정

MCP 서버(DBHub)를 통해 Polestar DB에 접속하려면 아래 설정이 필요합니다. DB가 Docker이든 서버 직접 설치이든 MCP 서버 설정 방법은 동일하며, **연결 문자열만 환경에 맞게 변경**합니다.

### 9.1 MCP 서버 설정 (`mcp_server/config.toml`)

이미 `polestar` 소스가 정의되어 있습니다:

```toml
[[sources]]
name = "polestar"   # 이 이름이 모든 설정의 기준점
type = "postgresql"
readonly = true
query_timeout = 30
max_rows = 10000
pool_min_size = 1
pool_max_size = 3
```

### 9.2 MCP 서버 환경변수 (`mcp_server/.env`)

DB 연결 문자열을 환경에 맞게 설정합니다:

```bash
# === PostgreSQL Docker (testdata/pg, port 5434) ===
POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb

# === PostgreSQL 서버 직접 설치 (기본 port 5432) ===
# POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@<서버IP>:5432/infradb

# === DB2 Docker (db2, port 50000) ===
# POLESTAR_CONNECTION=DATABASE=infradb;HOSTNAME=localhost;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=db2pass2024;

# === DB2 서버 직접 설치 ===
# POLESTAR_CONNECTION=DATABASE=infradb;HOSTNAME=<서버IP>;PORT=50000;PROTOCOL=TCPIP;UID=db2inst1;PWD=<password>;
```

> 환경변수명 규칙: `config.toml`의 `name`을 대문자로 변환 + `_CONNECTION` 접미사

### 9.3 설정 일치 확인 체크리스트

3곳의 이름이 **정확히 일치**해야 합니다:

| 위치 | 설정 항목 | 값 |
|------|----------|-----|
| `mcp_server/config.toml` | `[[sources]] name` | `polestar` |
| `mcp_server/.env` | 환경변수 접두사 | `POLESTAR_CONNECTION` |
| 프로젝트 루트 `.env` | `DBHUB_SOURCE_NAME` 또는 `ACTIVE_DB_IDS` | `polestar` |

> 하나라도 불일치하면 "알 수 없는 소스" 에러가 발생합니다.

### 9.4 MCP 서버 실행

```bash
cd mcp_server
pip install -e .
python -m mcp_server
# 또는
uvicorn mcp_server.server:app --host 0.0.0.0 --port 9099
```

---

## 10. 애플리케이션 환경변수 설정

프로젝트 루트의 `.env` 파일에서 DB 접속 방식을 설정합니다. Docker와 서버 직접 설치의 차이는 **연결 문자열(호스트/포트)** 뿐입니다.

### 10.1 Direct 모드 (MCP 서버 없이 직접 연결)

```bash
DB_BACKEND=direct

# Docker PostgreSQL (port 5434)
DB_CONNECTION_STRING=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb

# 서버 직접 설치 PostgreSQL (기본 port 5432)
# DB_CONNECTION_STRING=postgresql://polestar_user:polestar_pass_2024@<서버IP>:5432/infradb

# Polestar 전용 프롬프트 활성화
POLESTAR_DB_ID=polestar
```

### 10.2 DBHub 모드 (MCP 서버 경유)

```bash
DB_BACKEND=dbhub
DBHUB_SERVER_URL=http://localhost:9099/sse
DBHUB_SOURCE_NAME=polestar

# Polestar 전용 프롬프트 활성화
POLESTAR_DB_ID=polestar

# 멀티 DB 모드 (여러 DB 동시 사용 시)
# ENABLE_SEMANTIC_ROUTING=true
# ACTIVE_DB_IDS=polestar,infra_db
```

### 10.3 DB 프로필 설정

Polestar DB 구조 프로필은 `config/db_profiles/` 디렉토리에 정의되어 있습니다:

| 파일 | 용도 |
|------|------|
| `config/db_profiles/polestar.yaml` | DB2 연결용 프로필 |
| `config/db_profiles/polestar_pg.yaml` | PostgreSQL 연결용 프로필 |

두 프로필은 동일한 EAV 패턴, 계층 구조, 쿼리 가이드, 예시 쿼리를 정의합니다. DB 엔진에 따라 적합한 프로필이 자동 선택됩니다.

---

## 11. 전체 통합 실행 순서

### 11-A. Docker 기반 통합 실행

#### 최소 구성 (PostgreSQL Direct 모드)

```bash
# 1. Polestar DB 컨테이너 실행
cd testdata/pg
docker compose up -d

# 2. 컨테이너 healthy 상태 확인 (약 10초)
docker inspect --format='{{.State.Health.Status}}' polestar_pg

# 3. 추가 데이터 적용 (resource_conf_id 매핑 포함)
docker exec -i polestar_pg psql -U polestar_user -d infradb < 05_insert_excel_data.sql

# 4. 데이터 검증
docker exec -i polestar_pg psql -U polestar_user -d infradb < 04_verify_data.sql

# 5. 프로젝트 루트로 이동
cd ../..

# 6. .env 설정
# DB_BACKEND=direct
# DB_CONNECTION_STRING=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb
# POLESTAR_DB_ID=polestar

# 7. 애플리케이션 실행
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

#### 전체 구성 (MCP 서버 + Redis 포함)

```bash
# 1. 모든 컨테이너 실행
cd testdata/pg && docker compose up -d && cd ../..
cd redis && docker compose up -d && cd ..

# 2. Polestar 추가 데이터 적용
cd testdata/pg
docker exec -i polestar_pg psql -U polestar_user -d infradb < 05_insert_excel_data.sql
cd ../..

# 3. MCP 서버 환경변수 설정 확인
# mcp_server/.env: POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@localhost:5434/infradb

# 4. MCP 서버 실행
cd mcp_server && python -m mcp_server &
cd ..

# 5. 프로젝트 루트 .env 설정
# DB_BACKEND=dbhub
# DBHUB_SERVER_URL=http://localhost:9099/sse
# DBHUB_SOURCE_NAME=polestar
# POLESTAR_DB_ID=polestar
# REDIS_HOST=localhost
# REDIS_PORT=6380

# 6. 애플리케이션 실행
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

---

### 11-B. 서버 직접 설치 기반 통합 실행

#### 최소 구성 (PostgreSQL Direct 모드)

```bash
# 사전 조건: PostgreSQL이 설치되어 있고, 4-B 섹션의 설정이 완료된 상태

# 접속 URI 설정 (환경에 맞게 수정)
POLESTAR_URI="postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb"

# 1. PostgreSQL 서비스 확인
sudo systemctl status postgresql       # Ubuntu
# 또는
sudo systemctl status postgresql-16    # RHEL

# 2. 데이터 투입 (최초 1회)
psql "$POLESTAR_URI" -f testdata/pg/init/01_create_tables.sql
psql "$POLESTAR_URI" -f testdata/pg/init/02_insert_cmm_resource.sql
psql "$POLESTAR_URI" -f testdata/pg/init/03_insert_core_config_prop.sql
psql "$POLESTAR_URI" -f testdata/pg/05_insert_excel_data.sql

# 3. 데이터 검증
psql "$POLESTAR_URI" -f testdata/pg/04_verify_data.sql

# 4. .env 설정
# DB_BACKEND=direct
# DB_CONNECTION_STRING=postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb
# POLESTAR_DB_ID=polestar

# 5. 애플리케이션 실행
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

#### 전체 구성 (MCP 서버 + Redis 포함)

```bash
# 사전 조건: PostgreSQL, Redis가 설치되어 있고, 데이터 투입 완료

# 1. 서비스 상태 확인
sudo systemctl status postgresql
sudo systemctl status redis-server     # Ubuntu
# 또는
sudo systemctl status redis            # RHEL

# 2. Redis 접속 확인
redis-cli ping   # 기대값: PONG

# 3. MCP 서버 환경변수 설정
# mcp_server/.env:
# POLESTAR_CONNECTION=postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb

# 4. MCP 서버 실행
cd mcp_server && python -m mcp_server &
cd ..

# 5. 프로젝트 루트 .env 설정
# DB_BACKEND=dbhub
# DBHUB_SERVER_URL=http://localhost:9099/sse
# DBHUB_SOURCE_NAME=polestar
# POLESTAR_DB_ID=polestar
# REDIS_HOST=localhost
# REDIS_PORT=6379              ← 서버 직접 설치 시 기본 포트

# 6. 애플리케이션 실행
python -m uvicorn src.api.server:app --host 0.0.0.0 --port 8000
```

---

## 12. 데이터 정리 및 초기화

### 테스트 데이터만 삭제 (테이블 유지)

**PostgreSQL (Docker):**
```bash
docker exec -i polestar_pg psql -U polestar_user -d infradb < testdata/pg/99_cleanup.sql
```

**PostgreSQL (서버 직접 설치):**
```bash
psql postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb \
  -f testdata/pg/99_cleanup.sql
```

**DB2 (Docker):**
```bash
docker cp testdata/99_cleanup.sql infra_db2:/tmp/
docker exec -i infra_db2 su - db2inst1 -c \
  "db2 connect to infradb && db2 -tvf /tmp/99_cleanup.sql"
```

**DB2 (서버 직접 설치):**
```bash
su - db2inst1
db2 connect to infradb
db2 -tvf /path/to/testdata/99_cleanup.sql
```

삭제 범위:
- `cmm_resource`: ID >= 300001
- `core_config_prop`: ID >= 500001

### 전체 환경 제거

**Docker (컨테이너 + 볼륨 완전 삭제):**

```bash
# PostgreSQL 환경
cd testdata/pg && docker compose down -v

# DB2 환경
cd db2 && docker compose down -v

# Redis 환경
cd redis && docker compose down -v
```

**서버 직접 설치 (데이터베이스 삭제):**

```bash
# PostgreSQL: DB 삭제
sudo -u postgres psql -c "DROP DATABASE IF EXISTS infradb;"
# 사용자도 제거하려면:
sudo -u postgres psql -c "DROP USER IF EXISTS polestar_user;"

# DB2: DB 삭제
su - db2inst1
db2 drop database infradb
```

### 데이터 재투입

**Docker PostgreSQL (볼륨 삭제 후 재시작):**

```bash
cd testdata/pg
docker compose down -v
docker compose up -d

# healthy 대기 후 추가 데이터 적용
sleep 10
docker exec -i polestar_pg psql -U polestar_user -d infradb < 05_insert_excel_data.sql
```

**서버 직접 설치 PostgreSQL (정리 후 재투입):**

```bash
POLESTAR_URI="postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb"

# 기존 데이터 삭제
psql "$POLESTAR_URI" -f testdata/pg/99_cleanup.sql

# 재투입
psql "$POLESTAR_URI" -f testdata/pg/init/02_insert_cmm_resource.sql
psql "$POLESTAR_URI" -f testdata/pg/init/03_insert_core_config_prop.sql
psql "$POLESTAR_URI" -f testdata/pg/05_insert_excel_data.sql
```

---

## 13. 트러블슈팅

### 13.1 "알 수 없는 소스" 에러

**원인**: `mcp_server/config.toml`의 소스명과 클라이언트 `.env`의 `DBHUB_SOURCE_NAME` 값이 불일치.

**해결**:
```bash
# config.toml 확인
grep 'name = ' mcp_server/config.toml

# .env 확인
grep DBHUB_SOURCE_NAME .env
grep ACTIVE_DB_IDS .env
```

3곳(config.toml, mcp_server/.env, 루트 .env)의 소스명이 정확히 일치하는지 확인합니다.

### 13.2 EAV 조인 결과가 NULL

**원인**: `cmm_resource.resource_conf_id`가 설정되지 않음.

**해결**: `05_insert_excel_data.sql`을 적용합니다.

Docker:
```bash
docker exec -i polestar_pg psql -U polestar_user -d infradb < testdata/pg/05_insert_excel_data.sql
```

서버 직접 설치:
```bash
psql postgresql://polestar_user:polestar_pass_2024@localhost:5432/infradb \
  -f testdata/pg/05_insert_excel_data.sql
```

확인:
```sql
SELECT hostname, resource_conf_id
FROM polestar.cmm_resource
WHERE resource_type LIKE 'platform.server%'
ORDER BY hostname;
-- resource_conf_id가 301~330으로 설정되어 있어야 함
```

### 13.3 Docker 포트 충돌

```bash
# 사용 중인 포트 확인
lsof -i :5434    # PostgreSQL Docker
lsof -i :50000   # DB2
lsof -i :6380    # Redis Docker
```

충돌 시 해당 `docker-compose.yml`의 포트 매핑을 변경하고, 관련 `.env` 연결 문자열도 함께 수정합니다.

### 13.4 서버 직접 설치 — PostgreSQL 접속 거부

**증상**: `psql: error: connection refused` 또는 `no pg_hba.conf entry`

**해결**:

```bash
# 1. PostgreSQL 서비스가 실행 중인지 확인
sudo systemctl status postgresql

# 2. listen_addresses 확인
sudo -u postgres psql -c "SHOW listen_addresses;"
# 원격 접속 시 '*' 또는 해당 IP가 포함되어야 함

# 3. pg_hba.conf에 접속 규칙 확인
sudo grep polestar /etc/postgresql/16/main/pg_hba.conf    # Ubuntu
sudo grep polestar /var/lib/pgsql/16/data/pg_hba.conf     # RHEL

# 4. 방화벽 확인
sudo ufw status                           # Ubuntu
sudo firewall-cmd --list-ports            # RHEL
```

### 13.5 서버 직접 설치 — DB2 접속 거부

**증상**: `SQL30081N` 통신 오류

**해결**:

```bash
# 1. DB2 인스턴스 상태 확인
su - db2inst1 -c "db2 get instance"

# 2. DB2COMM 설정 확인
su - db2inst1 -c "db2set DB2COMM"
# tcpip가 설정되어 있어야 함

# 3. 서비스 포트 확인
su - db2inst1 -c "db2 get dbm cfg | grep SVCENAME"

# 4. 방화벽 확인
sudo firewall-cmd --list-ports    # 50000/tcp가 열려 있어야 함
```

### 13.6 DB2 컨테이너 시작 실패 (Apple Silicon)

**증상**: 컨테이너가 시작은 되지만 `healthy` 상태에 도달하지 못하고 계속 `starting` 상태.

**원인**: DB2 이미지(`icr.io/db2_community/db2`)는 x86_64 전용이며, Apple Silicon에서 Rosetta 에뮬레이션 시 불안정.

**해결**: PostgreSQL 버전(`testdata/pg/`)을 사용하세요.

### 13.7 pydantic-settings list[str] 파싱 에러

`.env`에서 `list[str]` 필드 설정 시 반드시 **JSON 배열 형식**을 사용해야 합니다:

```bash
# 올바른 형식
SECURITY_SENSITIVE_COLUMNS=["password","secret","token"]

# 잘못된 형식 (쉼표 구분 문자열 — 에러 발생)
SECURITY_SENSITIVE_COLUMNS=password,secret,token
```

### 13.8 PostgreSQL Docker — init 스크립트 미실행

`docker-entrypoint-initdb.d`의 SQL은 **최초 컨테이너 생성 시에만 실행**됩니다. 이미 볼륨(`polestar_pgdata`)이 존재하면 init 스크립트가 건너뛰어집니다.

**해결**: 볼륨을 삭제하고 재생성합니다.

```bash
cd testdata/pg
docker compose down -v   # -v 플래그가 볼륨도 삭제
docker compose up -d
```

### 13.9 서버 직접 설치 — 스키마 이미 존재

DDL을 재실행할 때 `schema "polestar" already exists` 오류가 발생할 수 있습니다.

**해결**: PostgreSQL DDL은 `CREATE SCHEMA IF NOT EXISTS`를 사용하므로 오류가 발생하지 않습니다. 테이블이 이미 존재하면 DROP 후 재생성하거나, 데이터만 삭제(`99_cleanup.sql`) 후 재투입합니다.

```bash
# 방법 1: 데이터만 삭제 후 재투입
psql "$POLESTAR_URI" -f testdata/pg/99_cleanup.sql
psql "$POLESTAR_URI" -f testdata/pg/init/02_insert_cmm_resource.sql
psql "$POLESTAR_URI" -f testdata/pg/init/03_insert_core_config_prop.sql

# 방법 2: 테이블 전체 삭제 후 재생성
psql "$POLESTAR_URI" -c "DROP TABLE IF EXISTS polestar.core_config_prop;"
psql "$POLESTAR_URI" -c "DROP TABLE IF EXISTS polestar.cmm_resource;"
psql "$POLESTAR_URI" -f testdata/pg/init/01_create_tables.sql
psql "$POLESTAR_URI" -f testdata/pg/init/02_insert_cmm_resource.sql
psql "$POLESTAR_URI" -f testdata/pg/init/03_insert_core_config_prop.sql
```
