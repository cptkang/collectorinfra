# 테스트 환경 및 PostgreSQL 직접 연결 요건 정의서

## 1. 개요

### 1.1 목적

서버 모니터링 시스템의 PostgreSQL DB를 구성하고, DBHub(MCP 서버) 없이도 LangGraph 에이전트가 PostgreSQL에 직접 연결하여 자연어 질의를 처리할 수 있는 테스트 환경을 구축한다.

### 1.2 배경

기존 구현은 DBHub(MCP 서버)를 통해 DB에 접근하지만, 로컬 테스트 환경에서는 MCP 서버 설정이 복잡하다. PostgreSQL 직접 연결 모드를 추가하여 개발/테스트 편의성을 높인다.

---

## 2. 요건 목록

### R-01: PostgreSQL Docker 환경 구성

| 항목 | 내용 |
|------|------|
| ID | R-01 |
| 우선순위 | 필수 |
| 설명 | Docker Compose로 PostgreSQL 16 컨테이너를 구성한다 |

**상세:**
- `db/docker-compose.yml`: PostgreSQL 16 Alpine 이미지
- DB명: `infra_db`, 사용자: `infra_user`
- 포트: 5433 (호스트 → 컨테이너)
- 헬스체크 설정으로 DB 준비 상태 확인
- 볼륨으로 데이터 영속화

**수용 기준:**
- `docker compose up -d`로 DB가 정상 시작된다
- `pg_isready`로 연결 가능 여부가 확인된다

---

### R-02: DB 스키마 구성 (5개 도메인)

| 항목 | 내용 |
|------|------|
| ID | R-02 |
| 우선순위 | 필수 |
| 설명 | 서버 모니터링 5개 도메인의 테이블을 생성한다 |

**테이블 구성:**

| 테이블 | 설명 | 주요 컬럼 |
|--------|------|----------|
| `servers` | 서버 기본 정보 | id, hostname, ip_address, os, location, purpose, cpu_cores, memory_total_gb, status |
| `cpu_metrics` | CPU 지표 | server_id(FK), collected_at, core_count, usage_pct, system_pct, user_pct, idle_pct, load_avg |
| `memory_metrics` | 메모리 지표 | server_id(FK), collected_at, total_gb, used_gb, free_gb, usage_pct, swap, cached, buffers |
| `disk_metrics` | 디스크 지표 | server_id(FK), collected_at, mount_point, filesystem, total_gb, used_gb, free_gb, usage_pct |
| `network_metrics` | 네트워크 지표 | server_id(FK), collected_at, interface, in_bytes, out_bytes, in/out_packets, errors, bandwidth |

**제약 조건:**
- 모든 메트릭 테이블은 `servers.id`에 FK 참조 (CASCADE DELETE)
- `(server_id, collected_at DESC)` 복합 인덱스로 시계열 조회 최적화
- `servers.hostname` UNIQUE 제약

**수용 기준:**
- DDL 실행 후 5개 테이블이 모두 생성된다
- FK 관계가 올바르게 설정된다
- 인덱스가 생성된다

---

### R-03: 테스트 데이터 생성

| 항목 | 내용 |
|------|------|
| ID | R-03 |
| 우선순위 | 필수 |
| 설명 | 현실적인 서버 모니터링 샘플 데이터를 생성한다 |

**데이터 규모:**
- 서버 10대 (다양한 용도: 웹, API, DB, 배치, 모니터링, 캐시, 백업)
- 최근 7일간 6시간 간격 메트릭 (서버당 ~28개 포인트)
- 서버 용도별 차별화된 사용 패턴:
  - DB 서버: 높은 메모리/CPU 사용률
  - 웹/API 서버: 시간대별 변동 패턴
  - 배치 서버: 주기적 spike 패턴

**수용 기준:**
- 각 테이블에 의미있는 양의 데이터가 삽입된다
- "메모리 사용률 80% 이상인 서버" 같은 질의에 결과가 반환된다

---

### R-04: PostgreSQL 직접 연결 클라이언트

| 항목 | 내용 |
|------|------|
| ID | R-04 |
| 우선순위 | 필수 |
| 설명 | asyncpg 기반 PostgreSQL 직접 연결 클라이언트를 구현한다 |

**구현 위치:** `src/db/client.py`

**인터페이스:** DBHubClient와 동일한 퍼블릭 메서드:
- `connect()` / `disconnect()` — 연결 풀 관리
- `health_check()` — 연결 상태 확인
- `search_objects()` — 테이블 목록 조회 (information_schema)
- `get_table_schema(table_name)` — 테이블 컬럼/PK 정보 조회
- `get_full_schema()` — 전체 스키마 + FK 관계 수집
- `get_sample_data(table_name)` — 샘플 데이터 조회
- `execute_sql(sql)` — SQL 실행, QueryResult 반환

**기술 스택:** asyncpg (비동기 PostgreSQL 드라이버)
- 커넥션 풀 (min=1, max=5)
- command_timeout으로 쿼리 타임아웃 제어
- Decimal → float 자동 변환

**수용 기준:**
- DBHubClient 없이 PostgreSQL에 직접 쿼리 실행이 가능하다
- 동일한 인터페이스로 기존 노드 코드 변경 없이 교체 가능하다

---

### R-05: 설정 기반 DB 백엔드 전환

| 항목 | 내용 |
|------|------|
| ID | R-05 |
| 우선순위 | 필수 |
| 설명 | 환경변수로 DB 접근 방식을 전환할 수 있다 |

**설정 항목:**
- `DB_BACKEND`: `direct` (PostgreSQL 직접) 또는 `dbhub` (MCP 서버)
- `DB_CONNECTION_STRING`: PostgreSQL DSN (direct 모드)

**적용 노드:**
- `schema_analyzer`: 스키마 조회 시 _get_client()로 분기
- `query_executor`: SQL 실행 시 _get_client()로 분기

**수용 기준:**
- `DB_BACKEND=direct` 시 asyncpg 클라이언트가 사용된다
- `DB_BACKEND=dbhub` 시 기존 DBHub 클라이언트가 사용된다
- 기존 기능에 영향 없다

---

### R-06: LangGraph 파이프라인 동작 검증

| 항목 | 내용 |
|------|------|
| ID | R-06 |
| 우선순위 | 필수 |
| 설명 | 전체 파이프라인이 PostgreSQL 직접 연결로 정상 동작한다 |

**검증 시나리오:**

| 질의 | 예상 동작 |
|------|----------|
| "전체 서버 목록을 알려줘" | servers 테이블 전체 조회 |
| "CPU 사용률이 70% 이상인 서버" | cpu_metrics + servers JOIN, 필터링 |
| "메모리 사용률이 80% 이상인 서버 목록" | memory_metrics + servers JOIN |
| "디스크 사용률 90% 이상인 서버의 상세 정보" | disk_metrics + servers JOIN |
| "지난 일주일간 네트워크 트래픽 Top 5 서버" | network_metrics 집계 + ORDER BY |

**파이프라인 흐름:**
```
input_parser → schema_analyzer → query_generator → query_validator → query_executor → result_organizer → output_generator
```

**수용 기준:**
- CLI 모드에서 한국어 질의 입력 시 자연어 응답이 반환된다
- 생성된 SQL이 PostgreSQL에서 정상 실행된다

---

## 3. 파일 구조

```
db/
├── docker-compose.yml        # PostgreSQL Docker 구성
├── setup.sh                  # 원클릭 환경 구성 스크립트
└── init/
    ├── 01_schema.sql         # DDL (테이블, 인덱스)
    └── 02_seed_data.sql      # DML (샘플 데이터)

src/db/
├── __init__.py
└── client.py                 # PostgreSQL 직접 연결 클라이언트

# 수정된 파일:
src/config.py                 # DB_BACKEND, DB_CONNECTION_STRING 추가
src/nodes/schema_analyzer.py  # _get_client() 분기 추가
src/nodes/query_executor.py   # _get_client() 분기 추가
pyproject.toml                # asyncpg 의존성 추가
.env.example                  # DB 연결 설정 추가
```

---

## 4. 실행 방법

### 4.1 환경 구성

```bash
# 1. PostgreSQL 컨테이너 시작 + 스키마/데이터 초기화
cd db && bash setup.sh

# 2. Python 의존성 설치
pip install -e ".[dev]"

# 3. .env에 LLM API 키 설정
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

### 4.2 에이전트 실행

```bash
# CLI 모드 (단일 질의)
python -m src.main --query "전체 서버의 CPU 사용률 현황을 알려줘"

# 대화형 모드
python -m src.main

# API 서버 모드
python -m src.main --server
```

---

## 5. 보안 고려사항

- 테스트 환경의 DB 비밀번호는 개발용이며 프로덕션에서 변경 필수
- `.env` 파일은 `.gitignore`에 포함되어 커밋되지 않음
- DB 직접 연결에서도 SQL 검증(query_validator)은 동일하게 적용됨
- 읽기 전용 쿼리만 허용 (SELECT 문만 통과)
