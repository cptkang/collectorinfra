# 스키마 캐시 테스트 환경 구축 및 글로벌 유사단어 사전 계획

> 작성일: 2026-03-20
> 의존 문서: `plans/schemacache_plan.md`
> 입력 데이터: `schema/polestar-schema.md`, `schema/polestar-data.md`, `sample/*.xlsx`

---

## 목차

1. [개요](#1-개요)
2. [영역 1: DB2 테이블 생성 및 샘플 데이터 생성](#2-영역-1-db2-테이블-생성-및-샘플-데이터-생성)
3. [영역 2: Redis 캐시 데이터 생성 계획](#3-영역-2-redis-캐시-데이터-생성-계획)
4. [영역 3: 글로벌 유사단어 사전 생성](#4-영역-3-글로벌-유사단어-사전-생성)
5. [영역 4: 코드에서 글로벌 유사단어 사전 로드 기능](#5-영역-4-코드에서-글로벌-유사단어-사전-로드-기능)
6. [디렉토리 구조](#6-디렉토리-구조)
7. [구현 순서 및 의존 관계](#7-구현-순서-및-의존-관계)
8. [성공 기준](#8-성공-기준)
9. [리스크 및 완화 방안](#9-리스크-및-완화-방안)

---

## 1. 개요

### 1.1 목적

본 문서는 두 가지 독립적이면서 상호 보완적인 영역을 다룬다.

**A. 스키마 캐시 테스트 환경 구축 (영역 1-2)**
- 실제 DB2 환경에 Polestar 스키마 테이블을 생성하고 현실적인 샘플 데이터를 투입한다.
- DBHub MCP 서버를 통해 DB2에 접속한 후, 기존 `SchemaCacheManager` + `DescriptionGenerator`가 스키마를 읽어 Redis 캐시를 자동 생성하는 전체 파이프라인을 E2E 검증한다.

**B. 글로벌 유사단어 사전 파일 관리 체계 (영역 3-4)**
- 인프라 모니터링 도메인에 특화된 글로벌 유사단어 사전의 초기 데이터를 YAML 파일로 정의한다.
- YAML/JSON/Excel/MD 파일에서 글로벌 유사단어를 로드하여 Redis `synonyms:global`에 저장하는 `synonym_loader.py` 모듈을 설계한다.

### 1.2 테스트 전략

본 문서는 독립된 테스트 계획으로, 아래 4단계 테스트 전략을 수행한다.

| 단계 | 테스트 유형 | 대상 | 검증 목표 |
|------|-----------|------|----------|
| 1단계 | 데이터 준비 검증 | DB2 테이블/데이터 | DDL 실행 + 30대 서버 샘플 데이터 정합성 |
| 2단계 | 캐시 파이프라인 검증 | Redis 캐시 자동 생성 | DB2 → DBHub → SchemaCacheManager → Redis 전체 흐름 |
| 3단계 | 유사단어 사전 검증 | 글로벌 사전 파일 로드 | YAML → Redis 로드/병합/내보내기 라운드트립 |
| 4단계 | E2E 통합 검증 | 자연어 질의 + 문서 생성 + 유사단어 관리 | 캐시 정보 기반 SQL 생성 정확도, 엑셀 양식 채우기, 유사단어 CRUD |

---

## 2. 영역 1: DB2 테이블 생성 및 샘플 데이터 생성

### 2.1 테이블 생성 DDL

`schema/polestar-schema.md`의 원본 DDL을 그대로 사용한다. DDL 파일은 `testdata/` 폴더에 별도 SQL 스크립트로 저장한다.

**생성할 파일**: `testdata/01_create_tables.sql`

```
내용:
- CREATE TABLE "POLESTAR"."CMM_RESOURCE" (...) -- 59컬럼, 원본 DDL 그대로
- CREATE TABLE "POLESTAR"."CORE_CONFIG_PROP" (...) -- 12컬럼, 원본 DDL 그대로
```

**주의사항**:
- POLESTAR 스키마가 이미 존재해야 한다 (필요 시 `CREATE SCHEMA POLESTAR` 선행)
- TABLESPACE `SSNISND01R`, `SSNISND01X`가 없는 테스트 환경에서는 `IN` 절을 제거하거나 기본 테이블스페이스를 사용하도록 DDL을 수정한다
- `COMPRESS YES ADAPTIVE`, `ORGANIZE BY ROW`는 테스트 환경 DB2 버전에 따라 제거가 필요할 수 있다
- DDL 수정 시 원본과 수정본을 모두 보관한다

### 2.2 샘플 데이터 설계 원칙

`schema/polestar-data.md`의 실제 데이터 패턴을 분석하여 현실적인 테스트 데이터를 생성한다.

#### 데이터 카운팅 기준

현재 데이터에서 **hostname 1건 = CMM_RESOURCE 최상위 ServiceResource 1행**으로 판단한다. 기존 데이터에는 hostname1, hotname2로 **2건**이 존재한다. 본 계획에서는 이 기준으로 **30건(30대 서버)**의 샘플 데이터를 생성한다.

```
hostname 1건의 구성:
  CMM_RESOURCE 최상위 ServiceResource 1행 (DTYPE='ServiceResource')
  + 하위 리소스 N행 (DTYPE='Resource') -- CPU, 메모리, 디스크, 파일시스템, 네트워크 등
  + CORE_CONFIG_PROP 12행 -- 에이전트 설정 (AgentID, Hostname, OSType 등)
```

#### 데이터 설계 기준

| 항목 | 기준 |
|------|------|
| 서버 수 | **30대** (svr-web-01 ~ svr-web-10, svr-was-01 ~ svr-was-10, svr-db-01 ~ svr-db-10) |
| 기존 데이터와 구분 | ID 범위를 300000 이상으로 시작하여 기존 데이터와 충돌 방지 |
| RESOURCE_TYPE 다양성 | 기존 19종 중 최소 15종 이상 커버 |
| 서버 용도별 분류 | 웹서버 10대, WAS 서버 10대, DB 서버 10대 |
| 서버 플랫폼 다양화 | VM(VMware), 물리(HPE), 물리(Dell) 혼합 |
| CORE_CONFIG_PROP | 각 서버당 12종 설정 항목 (기존 패턴 동일) |
| 합계 | CMM_RESOURCE **약 700행**, CORE_CONFIG_PROP **360행** |

#### 서버 용도별 리소스 프로파일 (3종 템플릿)

각 서버는 용도에 따라 아래 3종 프로파일 중 하나를 적용한다. 같은 프로파일 내에서도 코어 수, 파일시스템 등에 미세 변화를 두어 데이터 다양성을 확보한다.

**프로파일 A: 웹서버 (svr-web-01 ~ svr-web-10)**
- CPU: 2~4코어
- 메모리: Memory, VirtualMemory, OtherMemory (3행)
- 파일시스템: 6~8개 (/, /boot, /fsutil, /fsapp, /fslog, /fshome 등)
- 네트워크: 2개 (ens192, ens224)
- 모니터: LogMonitor 2개, ProcessMonitor 2개 (httpd/nginx)
- 기타: Disks, FileSystems, NetworkInterfaces, Netstat, Process, Other (컨테이너 6행)
- monitor group (1행)
- **1대당 약 20~24행** (상위 ServiceResource 포함)

**프로파일 B: WAS 서버 (svr-was-01 ~ svr-was-10)**
- CPU: 4~8코어
- 메모리: Memory, VirtualMemory, OtherMemory (3행)
- 파일시스템: 8~10개 (/, /boot, /fsutil, /fsapp, /fslog, /fswas, /fswaslog 등)
- 네트워크: 2~3개 (bond0, ens2f0 등)
- 모니터: LogMonitor 3개, ProcessMonitor 3개 (java, tomcat 등)
- 기타: 컨테이너 6행, monitor group 1행
- **1대당 약 25~30행**

**프로파일 C: DB 서버 (svr-db-01 ~ svr-db-10)**
- CPU: 8~16코어
- 메모리: Memory, VirtualMemory, OtherMemory (3행)
- 파일시스템: 10~14개 (DB 관련: /FSDB2INST, /FSDB2LOG, /FSDB2DAT 등)
- 네트워크: 2~3개
- 모니터: LogMonitor 2개 (DB2 진단 포함), ProcessMonitor 2개 (db2sysc 등)
- HBA: 1~2 어댑터, 2~4 포트 (SAN 스토리지 연결)
- 기타: 컨테이너 6행 + Hbas 컨테이너 1행, monitor group 1행
- **1대당 약 28~40행**

#### 서버 목록 (30대)

| # | Hostname | 용도 | 프로파일 | 플랫폼 | CPU 코어 | IP |
|---|----------|------|---------|--------|---------|-----|
| 1 | svr-web-01 | 웹서버 | A | VMware | 2 | 10.0.1.1 |
| 2 | svr-web-02 | 웹서버 | A | VMware | 2 | 10.0.1.2 |
| 3 | svr-web-03 | 웹서버 | A | VMware | 4 | 10.0.1.3 |
| 4 | svr-web-04 | 웹서버 | A | VMware | 4 | 10.0.1.4 |
| 5 | svr-web-05 | 웹서버 | A | VMware | 2 | 10.0.1.5 |
| 6 | svr-web-06 | 웹서버 | A | VMware | 4 | 10.0.1.6 |
| 7 | svr-web-07 | 웹서버 | A | HPE | 4 | 10.0.1.7 |
| 8 | svr-web-08 | 웹서버 | A | HPE | 2 | 10.0.1.8 |
| 9 | svr-web-09 | 웹서버 | A | VMware | 2 | 10.0.1.9 |
| 10 | svr-web-10 | 웹서버 | A | VMware | 4 | 10.0.1.10 |
| 11 | svr-was-01 | WAS | B | VMware | 4 | 10.0.2.1 |
| 12 | svr-was-02 | WAS | B | VMware | 8 | 10.0.2.2 |
| 13 | svr-was-03 | WAS | B | HPE | 4 | 10.0.2.3 |
| 14 | svr-was-04 | WAS | B | HPE | 8 | 10.0.2.4 |
| 15 | svr-was-05 | WAS | B | VMware | 4 | 10.0.2.5 |
| 16 | svr-was-06 | WAS | B | VMware | 8 | 10.0.2.6 |
| 17 | svr-was-07 | WAS | B | Dell | 4 | 10.0.2.7 |
| 18 | svr-was-08 | WAS | B | Dell | 8 | 10.0.2.8 |
| 19 | svr-was-09 | WAS | B | VMware | 4 | 10.0.2.9 |
| 20 | svr-was-10 | WAS | B | HPE | 8 | 10.0.2.10 |
| 21 | svr-db-01 | DB | C | HPE | 16 | 10.0.3.1 |
| 22 | svr-db-02 | DB | C | HPE | 16 | 10.0.3.2 |
| 23 | svr-db-03 | DB | C | Dell | 8 | 10.0.3.3 |
| 24 | svr-db-04 | DB | C | Dell | 16 | 10.0.3.4 |
| 25 | svr-db-05 | DB | C | HPE | 8 | 10.0.3.5 |
| 26 | svr-db-06 | DB | C | HPE | 16 | 10.0.3.6 |
| 27 | svr-db-07 | DB | C | Dell | 8 | 10.0.3.7 |
| 28 | svr-db-08 | DB | C | Dell | 16 | 10.0.3.8 |
| 29 | svr-db-09 | DB | C | HPE | 8 | 10.0.3.9 |
| 30 | svr-db-10 | DB | C | HPE | 16 | 10.0.3.10 |

#### AVAIL_STATUS 다양성

30대 서버 중 대부분은 정상(0)으로 설정하되, 테스트 다양성을 위해 일부를 비정상(1)으로 설정한다:
- 비정상 서버: svr-web-05(1대), svr-was-07(1대) — 상위 ServiceResource의 AVAIL_STATUS=1
- 비정상 하위 리소스: 각 서버 내 ProcessMonitor 1~2개, HbaPort 1~2개를 AVAIL_STATUS=1로 설정
- 전체 비정상 비율: 약 5~8%

#### CORE_CONFIG_PROP 데이터 설계

| CONFIGURATION_ID 범위 | 서버 그룹 | 특성 |
|----------------------|----------|------|
| 301~310 | svr-web-01 ~ svr-web-10 | VMware/HPE, 2~4코어, LINUX |
| 311~320 | svr-was-01 ~ svr-was-10 | VMware/HPE/Dell, 4~8코어, LINUX |
| 321~330 | svr-db-01 ~ svr-db-10 | HPE/Dell, 8~16코어, LINUX |

각 서버에 12종 설정 항목을 생성한다 (총 30 x 12 = **360행**):

| NAME | 웹서버 예시 (svr-web-01) | WAS 예시 (svr-was-01) | DB 예시 (svr-db-01) |
|------|------------------------|---------------------|---------------------|
| AgentID | MA_svr-web-01_20220315091000 | MA_svr-was-01_20210820143000 | MA_svr-db-01_20200601100000 |
| AgentVersion | 7.6.28_1 | 7.6.26_6 | 7.6.30_2 |
| GMT | GMT+09:00 | GMT+09:00 | GMT+09:00 |
| Hostname | svr-web-01 | svr-was-01 | svr-db-01 |
| IPaddress | 10.0.1.1 | 10.0.2.1 | 10.0.3.1 |
| InstallPath | /fsutil/polestar/agent/... | /fsutil/polestar/agent/... | /fsutil/polestar/agent/... |
| Model | VMware Virtual Platform | VMware Virtual Platform | ProLiant DL380 Gen10 Plus |
| OSParameter | (커널 파라미터 요약) | (커널 파라미터 요약) | (커널 파라미터 요약) |
| OSType | LINUX | LINUX | LINUX |
| OSVerson | 4.18.0-305.el8.x86_64 | 3.10.0-1160.el7.x86_64 | 5.14.0-70.el9.x86_64 |
| SerialNumber | VMware-web01abcd | VMware-was01efgh | HOST-DB01-1234AB |
| Vendor | VMware, Inc. | VMware, Inc. | HPE |

> **참고**: 같은 그룹 내에서도 AgentVersion은 2~3종으로 분산시키고, OSVerson은 CentOS 7/8/9 혼합하여 쿼리 테스트 다양성을 확보한다.

### 2.3 생성할 SQL 스크립트 파일 목록

| 파일 | 내용 | 비고 |
|------|------|------|
| `testdata/01_create_tables.sql` | CREATE TABLE DDL 2개 | 원본 DDL 기반, 테스트 환경용 수정본 포함 |
| `testdata/02_insert_cmm_resource.sql` | CMM_RESOURCE INSERT 문 (약 700행) | 서버 30대 리소스 |
| `testdata/03_insert_core_config_prop.sql` | CORE_CONFIG_PROP INSERT 문 (360행) | 서버 30대 x 12설정 |
| `testdata/04_verify_data.sql` | 데이터 검증 쿼리 | 행 수, 분포 확인용 SELECT |
| `testdata/99_cleanup.sql` | DROP TABLE / DELETE 문 | 테스트 후 정리용 |
| `testdata/README.md` | SQL 스크립트 실행 순서, 환경 요구사항 설명 | |

### 2.4 INSERT 문 생성 규칙

- IDENTITY 컬럼(ID)은 명시적으로 값을 지정한다. DB2에서는 `GENERATED BY DEFAULT AS IDENTITY`이므로 명시적 INSERT가 가능하다.
- CTIME, MTIME 값은 epoch 밀리초 단위로 현실적인 시각을 사용한다 (예: 2022년~2025년 범위).
- RESOURCE_KEY는 UUID 형식의 고유 값을 사용한다.
- ID_ANCESTRY는 부모-자식 관계를 반영한 실제 경로 형식을 사용한다.
- AVAIL_STATUS는 대부분 0(정상), 일부 1(비정상)로 설정하여 테스트 다양성을 확보한다.
- HOSTNAME, IPADDRESS는 ServiceResource와 상위 컨테이너에만 설정 (하위 리소스는 NULL -- 기존 데이터 패턴 준수).
- OSParameter는 IS_LOB=0일 때 STRINGVALUE_SHORT에, IS_LOB=1일 때 STRINGVALUE(CLOB)에 저장한다.

### 2.5 데이터 검증 쿼리 (`testdata/04_verify_data.sql`)

```sql
-- 1. 전체 행 수 확인
SELECT COUNT(*) AS total_rows FROM POLESTAR.CMM_RESOURCE;
-- 기대값: 약 700행 (서버 30대)

-- 2. 서버별 행 수 (HOSTNAME 기준)
SELECT HOSTNAME, COUNT(*) AS cnt
FROM POLESTAR.CMM_RESOURCE
WHERE HOSTNAME IS NOT NULL
GROUP BY HOSTNAME
ORDER BY HOSTNAME;
-- 기대값: svr-web-01 ~ svr-web-10, svr-was-01 ~ svr-was-10, svr-db-01 ~ svr-db-10 (30건)

-- 3. 용도별 서버 수
SELECT
  CASE
    WHEN HOSTNAME LIKE 'svr-web%' THEN 'WEB'
    WHEN HOSTNAME LIKE 'svr-was%' THEN 'WAS'
    WHEN HOSTNAME LIKE 'svr-db%' THEN 'DB'
  END AS server_group,
  COUNT(DISTINCT HOSTNAME) AS server_count
FROM POLESTAR.CMM_RESOURCE
WHERE HOSTNAME IS NOT NULL
GROUP BY
  CASE
    WHEN HOSTNAME LIKE 'svr-web%' THEN 'WEB'
    WHEN HOSTNAME LIKE 'svr-was%' THEN 'WAS'
    WHEN HOSTNAME LIKE 'svr-db%' THEN 'DB'
  END;
-- 기대값: WEB 10, WAS 10, DB 10

-- 4. RESOURCE_TYPE별 분포
SELECT RESOURCE_TYPE, COUNT(*) AS cnt
FROM POLESTAR.CMM_RESOURCE
GROUP BY RESOURCE_TYPE
ORDER BY cnt DESC;
-- 기대값: 15종 이상

-- 5. AVAIL_STATUS 분포 (비정상 리소스 확인)
SELECT AVAIL_STATUS, COUNT(*) AS cnt
FROM POLESTAR.CMM_RESOURCE
GROUP BY AVAIL_STATUS;
-- 기대값: 0(정상) 약 92~95%, 1(비정상) 약 5~8%

-- 6. CORE_CONFIG_PROP 행 수
SELECT COUNT(*) AS total_rows FROM POLESTAR.CORE_CONFIG_PROP;
-- 기대값: 360 (30대 x 12설정)

-- 7. CONFIGURATION_ID별 설정 항목 수
SELECT CONFIGURATION_ID, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
GROUP BY CONFIGURATION_ID
ORDER BY CONFIGURATION_ID;
-- 기대값: 301~330 각각 12건

-- 8. 서버별 설정 값 비교 (웹서버 1대)
SELECT NAME, STRINGVALUE_SHORT
FROM POLESTAR.CORE_CONFIG_PROP
WHERE CONFIGURATION_ID = 301
ORDER BY NAME;

-- 9. 제조사(Vendor)별 서버 수
SELECT STRINGVALUE_SHORT AS vendor, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
WHERE NAME = 'Vendor'
GROUP BY STRINGVALUE_SHORT;
-- 기대값: VMware, Inc. / HPE / Dell Inc. 3종

-- 10. OS 버전 분포
SELECT STRINGVALUE_SHORT AS os_version, COUNT(*) AS cnt
FROM POLESTAR.CORE_CONFIG_PROP
WHERE NAME = 'OSVerson'
GROUP BY STRINGVALUE_SHORT;
-- 기대값: CentOS 7/8/9 커널 2~3종
```

---

## 3. 영역 2: Redis 캐시 데이터 생성 계획

### 3.1 전체 흐름

영역 1에서 생성한 DB2 테이블/데이터를 기반으로, 기존 구현 코드가 Redis 캐시를 자동 생성하는 과정을 검증한다.

```
[Phase 1] DB2 테이블/데이터 준비 (영역 1)
      |
[Phase 2] DBHub MCP 서버 설정 + 연결 확인
      |
[Phase 3] SchemaCacheManager.refresh_cache() 호출
      |  -> DB2에서 스키마 조회 (search_objects)
      |  -> fingerprint 계산
      |  -> Redis에 스키마 저장 (tables, meta, relationships)
      |
[Phase 4] DescriptionGenerator.generate_for_db() 호출
      |  -> LLM이 각 컬럼의 설명 + 유사 단어 생성
      |  -> Redis에 descriptions, synonyms 저장
      |
[Phase 5] DescriptionGenerator.generate_db_description() 호출
      |  -> LLM이 DB 전체 설명 생성
      |  -> Redis db_descriptions에 저장
      |
[Phase 6] SchemaCacheManager.sync_global_synonyms() 호출
      |  -> DB별 synonyms -> 글로벌 사전 병합
      |
[Phase 7] 검증: Redis 키/값 확인
      |
[Phase 8] E2E: 자연어 질의 -> SQL 생성 -> 실행 -> 응답
```

### 3.2 MCP 서버 설정 (테스트용)

MCP 서버의 `mcp_server/config.toml`에 이미 `infra_db2` 소스가 정의되어 있다. 테스트 시 이 소스를 사용한다. DB 연결 문자열은 `mcp_server/.env`에서 환경변수로 오버라이드한다.

> **참고**: 프로젝트 루트의 `dbhub.toml`은 **DEPRECATED**이다. MCP 서버 도입으로 DB 연결 설정은 `mcp_server/config.toml` + `mcp_server/.env`에서 관리한다.

**기존 설정** (`mcp_server/config.toml` 발췌):
```toml
# === DB2 (Docker :50000) ===
[[sources]]
name = "infra_db2"
type = "db2"
# connection은 .env에서 오버라이드
readonly = true
query_timeout = 30
max_rows = 10000
```

**환경변수 설정** (`mcp_server/.env`):
```
INFRA_DB2_CONNECTION=db2://db2inst1:password@localhost:50000/INFRADB
```

MCP 서버 시작:
```bash
cd mcp_server
python -m mcp_server
```

### 3.3 테스트에서 검증해야 할 Redis 키/값 목록

#### Phase 3 후 검증 (스키마 저장)

| Redis 키 | 검증 항목 | 기대값 |
|-----------|---------|--------|
| `schema:infra_db2:meta` | fingerprint 존재 | 비어있지 않은 SHA-256 해시 |
| `schema:infra_db2:meta` | table_count | "2" |
| `schema:infra_db2:meta` | total_column_count | "71" |
| `schema:infra_db2:meta` | description_status | "pending" |
| `schema:infra_db2:tables` | HLEN | 2 (CMM_RESOURCE, CORE_CONFIG_PROP) |
| `schema:infra_db2:tables` | CMM_RESOURCE columns 수 | JSON 파싱 후 59 |
| `schema:infra_db2:tables` | CORE_CONFIG_PROP columns 수 | JSON 파싱 후 12 |
| `schema:infra_db2:tables` | sample_data 존재 | 각 테이블 최소 1건 |
| `schema:infra_db2:relationships` | 존재 | JSON array (빈 배열도 허용) |

#### Phase 4 후 검증 (설명 + 유사 단어)

| Redis 키 | 검증 항목 | 기대값 |
|-----------|---------|--------|
| `schema:infra_db2:descriptions` | HLEN | 60 이상 (71 목표, 커버율 90%) |
| `schema:infra_db2:descriptions` | CMM_RESOURCE.HOSTNAME | 비어있지 않은 한국어 설명 |
| `schema:infra_db2:descriptions` | CORE_CONFIG_PROP.NAME | EAV Attribute 역할 인식 |
| `schema:infra_db2:synonyms` | HLEN | 60 이상 |
| `schema:infra_db2:synonyms` | CMM_RESOURCE.HOSTNAME words | "호스트명", "서버명" 등 3개 이상 |
| `schema:infra_db2:synonyms` | CMM_RESOURCE.IPADDRESS words | "IP주소", "아이피" 등 3개 이상 |
| `schema:infra_db2:meta` | description_status | "complete" |

#### Phase 5 후 검증 (DB 설명)

| Redis 키 | 검증 항목 | 기대값 |
|-----------|---------|--------|
| `schema:db_descriptions` | infra_db2 필드 존재 | 비어있지 않은 한국어 문자열 |
| `schema:db_descriptions` | 설명 길이 | 30~100자 |
| `schema:db_descriptions` | 도메인 키워드 | "인프라", "서버", "리소스", "모니터링" 중 1개 이상 |

#### Phase 6 후 검증 (글로벌 사전 동기화)

| Redis 키 | 검증 항목 | 기대값 |
|-----------|---------|--------|
| `synonyms:global` | HOSTNAME 필드 존재 | "호스트명" 등 유사 단어 목록 |
| `synonyms:global` | IPADDRESS 필드 존재 | "IP주소" 등 유사 단어 목록 |
| `synonyms:global` | RESOURCE_TYPE 필드 존재 | "리소스 유형" 등 유사 단어 목록 |
| `synonyms:global` | 공통 컬럼(DTYPE, ID, NAME) | 양 테이블에서 1회만 등록 (중복 제거) |

### 3.4 E2E 테스트 시나리오 (Phase 8)

캐시 생성 후, 3가지 유형의 E2E 테스트를 수행한다.
엑셀 양식 기반 테스트는 `sample/취합 예시1.xlsx` 파일을 사용한다.

#### 3.4.1 자연어 질의 → SQL 생성 테스트

캐시의 유사단어/설명 정보가 SQL 생성 정확도에 미치는 효과를 검증한다.

| # | 자연어 질의 | 기대 SQL 패턴 | 검증 포인트 |
|---|-----------|-------------|-----------|
| E2E-1 | "서버 목록을 보여줘" | `SELECT ... FROM CMM_RESOURCE WHERE DTYPE='ServiceResource'` 또는 `WHERE HOSTNAME IS NOT NULL` | 서버 식별 정확성 (30대) |
| E2E-2 | "svr-db-01의 CPU 코어 수를 알려줘" | `SELECT COUNT(*) FROM CMM_RESOURCE WHERE ... RESOURCE_TYPE='server.Cpu' AND PARENT_RESOURCE_ID IN (...)` | 계층 구조 인식 |
| E2E-3 | "파일시스템 사용 현황" | `SELECT ... FROM CMM_RESOURCE WHERE RESOURCE_TYPE='server.FileSystem'` | RESOURCE_TYPE 매핑 |
| E2E-4 | "에이전트 버전 정보" | `SELECT NAME, STRINGVALUE_SHORT FROM CORE_CONFIG_PROP WHERE NAME='AgentVersion'` | EAV 패턴 인식 |
| E2E-5 | "운영체제 종류별 서버 수" | `SELECT STRINGVALUE_SHORT, COUNT(DISTINCT CONFIGURATION_ID) FROM CORE_CONFIG_PROP WHERE NAME='OSType' GROUP BY ...` | EAV 집계 |
| E2E-6 | "네트워크 인터페이스 목록" | `SELECT ... FROM CMM_RESOURCE WHERE RESOURCE_TYPE='server.NetworkInterface'` | RESOURCE_TYPE 유사 단어 매핑 |
| E2E-7 | "서버 제조사 정보" | `SELECT ... FROM CORE_CONFIG_PROP WHERE NAME='Vendor'` | 유사 단어("제조사" → Vendor) |
| E2E-8 | "비정상 상태인 서버 목록" | `SELECT ... FROM CMM_RESOURCE WHERE AVAIL_STATUS=1 AND DTYPE='ServiceResource'` | AVAIL_STATUS 유사 단어("비정상" → 1) |
| E2E-9 | "WAS 서버의 프로세스 모니터 현황" | `SELECT ... FROM CMM_RESOURCE WHERE RESOURCE_TYPE='server.ProcessMonitor' AND ...` | 호스트명 패턴 + RESOURCE_TYPE 조합 |
| E2E-10 | "DB 서버의 HBA 포트 상태" | `SELECT ... FROM CMM_RESOURCE WHERE RESOURCE_TYPE='server.HbaPort' AND ...` | 다단계 계층 탐색 |

#### 3.4.2 엑셀 양식 파일 기반 데이터 생성 테스트

`sample/취합 예시1.xlsx` 파일을 첨부하고, 자연어로 요청하면 DB 데이터를 조회하여 양식에 맞춰 데이터를 채운 엑셀 파일을 생성하는 시나리오를 검증한다.

**테스트 대상 파일**: `sample/취합 예시1.xlsx` (3개 시트)

##### 시트 1: 성능관리분석자료

| 행 | 구조 |
|----|------|
| 1행 | 제목: "전체 시스템 자원 현황" (A1:M1 병합) |
| 3행 | 소분류 헤더: "자원현황"(L3:M3 병합), "모니터링 Tool"(N3:P3 병합), "2월"(S3:U3 병합) |
| 4행 | 컬럼 헤더: 일련번호, 업무유형, 성능관리, 비대면주요서버, CF여부, Serial 번호, **서버명**, 업무내용, 대분류, 소분류, **IP**, **CPU**, **MEMORY**, OS, WAS, DB, DMZ 유무, 센터구분, CPU_AVG, CPU_PEAK, MEMORY |
| 5행~ | 데이터 행 |

**DB 컬럼 매핑 (유사단어 사전 기반)**:

| 양식 헤더 | DB 테이블.컬럼 | 매핑 근거 |
|----------|--------------|----------|
| 서버명 (G열) | CMM_RESOURCE.HOSTNAME 또는 CORE_CONFIG_PROP.NAME='Hostname' | 유사단어: "서버명" → HOSTNAME |
| IP (K열) | CORE_CONFIG_PROP.NAME='IPaddress' | 유사단어: "IP" → IPADDRESS |
| Serial 번호 (F열) | CORE_CONFIG_PROP.NAME='SerialNumber' | 유사단어: "시리얼 번호", "S/N" |
| CPU (L열) | CMM_RESOURCE에서 RESOURCE_TYPE='server.Cpu' COUNT | 컬럼 유사단어: "CPU" → server.Cpu |
| MEMORY (M열) | CMM_RESOURCE에서 RESOURCE_TYPE='server.Memory' 관련 | 컬럼 유사단어: "메모리" |
| OS (N열) | 모니터링 Tool 컬럼 — 직접 매핑 불가, "Polestar" 고정 | 도메인 특수 처리 |
| CPU_AVG, CPU_PEAK, MEMORY(S~U열) | 성능 메트릭 — 현재 DB에 없음 | 매핑 불가 시 빈 값 처리 검증 |

##### 시트 2: 자산정보관리대장

| 행 | 구조 |
|----|------|
| 1행 | (빈 행 또는 제목) |
| 2행 | 컬럼 헤더: No, 환경구분, 서버구분, 대분류, 운영기준, 구분, **자산명/호스트명**, **IP**, **설명**, **제조사**, **모델명**, **S/N(Serial Number)**, EOS 일자, EOS 여부, EOS 조치 계획, 상세 조치내용, **위치**, 인프라 담당자, 인프라 담당부서, 비고 |
| 3행~ | 데이터 행 |

**DB 컬럼 매핑 (유사단어 사전 기반)**:

| 양식 헤더 | DB 테이블.컬럼 | 매핑 근거 |
|----------|--------------|----------|
| 자산명/호스트명 (G열) | CMM_RESOURCE.HOSTNAME | 유사단어: "호스트명", "서버명" |
| IP (H열) | CORE_CONFIG_PROP.NAME='IPaddress' | 유사단어: "IP주소", "아이피" |
| 설명 (I열) | CMM_RESOURCE.DESCRIPTION | 유사단어: "설명", "리소스 설명" |
| 제조사 (J열) | CORE_CONFIG_PROP.NAME='Vendor' | 유사단어: "제조사", "벤더" |
| 모델명 (K열) | CORE_CONFIG_PROP.NAME='Model' | 유사단어: "서버 모델", "모델명" |
| S/N (L열) | CORE_CONFIG_PROP.NAME='SerialNumber' | 유사단어: "시리얼 번호", "S/N" |
| 위치 (Q열) | CMM_RESOURCE.LOCATION | 유사단어: "위치", "서버 위치" |
| 환경구분, 서버구분 등 | 직접 매핑 불가 | 매핑 불가 시 빈 값 처리 검증 |

##### 시트 3: 시스템 계획정지 작업 상세현황

| 행 | 구조 |
|----|------|
| 2~3행 | 2단 헤더 (병합 셀 다수): 연번, 작업일자, 작업시간(시작/종료), 업무, 작업내용, 대고객 영향도, 인프라변경 검토 유무, 부서, 대상기기(유형/수량), 참여인원(참여직원/KB/협력업체), 비고 |
| 4행~ | 데이터 행 |

이 시트는 작업 이력 데이터로, CMM_RESOURCE/CORE_CONFIG_PROP과 직접 매핑이 어렵다. **대상기기 → HOSTNAME 매핑** 정도만 가능하며, 나머지는 매핑 불가 항목 처리를 검증하는 데 활용한다.

##### 테스트용 양식 템플릿 (신규 생성)

`취합 예시1.xlsx`에 포함되지 않은 시나리오를 검증하기 위해 단순한 양식 템플릿 3종을 `testdata/templates/`에 추가 생성한다.

| 양식 파일 | 양식 구조 (헤더) | 설명 |
|----------|----------------|------|
| `server_list_template.xlsx` | 호스트명 \| IP주소 \| OS종류 \| CPU코어수 \| 제조사 \| 상태 | 단순 서버 인벤토리 목록 (1단 헤더, 병합 없음) |
| `resource_status_template.xlsx` | 서버명 \| 리소스유형 \| 리소스명 \| 상태 \| 설명 | 리소스별 상태 보고서 (하위 리소스 행 단위) |
| `config_report_template.xlsx` | 서버명 \| 설정항목 \| 설정값 | EAV 원본 형태 서버 설정 보고서 |

**목적**: `취합 예시1.xlsx`는 실무 양식으로 복잡한 구조(병합 셀, 2단 헤더, 매핑 불가 컬럼)를 테스트하는 반면, 이 3종은 단순 1단 헤더로 기본 매핑 정확도를 검증한다.

##### E2E 테스트 시나리오

**A. 실무 양식 테스트 (`sample/취합 예시1.xlsx`)**

| # | 시나리오 | 자연어 요청 | 사용 시트 | 검증 포인트 |
|---|---------|-----------|----------|-----------|
| TPL-1 | 자산정보 양식 채우기 | "이 양식의 자산정보관리대장 시트에 전체 서버 정보를 채워줘" + `취합 예시1.xlsx` 첨부 | 자산정보관리대장 | 호스트명/IP/제조사/모델/S/N/위치 6개 필드 시맨틱 매핑, 30대 서버 데이터 채우기, EAV 피벗(Vendor→제조사) |
| TPL-2 | 성능관리 양식 채우기 | "성능관리분석자료 시트에 서버 현황을 채워줘" + `취합 예시1.xlsx` 첨부 | 성능관리분석자료 | 병합 셀 헤더(4행) 감지, 서버명/IP/CPU/Serial 매핑, 매핑 불가 컬럼(CPU_AVG 등) 빈 값 처리 |
| TPL-3 | 필터 조건부 채우기 | "자산정보관리대장에 DB 서버만 채워줘" + `취합 예시1.xlsx` 첨부 | 자산정보관리대장 | 필터 조건 해석("DB 서버" → svr-db-*) + 양식 매핑, 결과 10행 |
| TPL-4 | 멀티 시트 동시 처리 | "이 파일의 자산정보관리대장과 성능관리분석자료 시트 둘 다 채워줘" + `취합 예시1.xlsx` 첨부 | 양쪽 시트 | 2개 시트 동시 처리, 각 시트별 헤더/매핑 독립 수행 |
| TPL-5 | 계획정지 시트 매핑 한계 | "시스템 계획정지 작업 상세현황 시트에 데이터를 채워줘" + `취합 예시1.xlsx` 첨부 | 시스템 계획정지... | 2단 병합 헤더 파싱, 대부분 매핑 불가 → 사용자에게 매핑 불가 항목 안내 메시지 생성 |

**B. 단순 양식 테스트 (`testdata/templates/*.xlsx`)**

| # | 시나리오 | 자연어 요청 | 사용 양식 | 검증 포인트 |
|---|---------|-----------|----------|-----------|
| TPL-6 | 서버 목록 양식 채우기 | "이 양식에 맞춰 전체 서버 목록을 만들어줘" + `server_list_template.xlsx` 첨부 | server_list | 1단 헤더 기본 매핑, 30대 서버 정확히 채우기, "호스트명"→HOSTNAME / "CPU코어수"→server.Cpu COUNT / "제조사"→EAV Vendor |
| TPL-7 | 리소스 상태 보고서 | "svr-web-01 서버의 리소스 현황을 이 양식으로 만들어줘" + `resource_status_template.xlsx` 첨부 | resource_status | 특정 서버 하위 리소스 조회, 리소스유형/리소스명/상태 매핑, 약 20~24행 결과 |
| TPL-8 | 설정 정보 보고서 | "전체 서버 설정 정보를 이 양식으로 만들어줘" + `config_report_template.xlsx` 첨부 | config_report | EAV 원본 형태 출력 (피벗 없이 NAME/VALUE 행 단위), 30대 x 12설정 = 360행 |

**검증 항목**:
- 양식 파일의 헤더 자동 감지 (행 위치, 병합 셀, 2단 헤더 등)
- 유사단어 사전 기반 필드 매핑 정확도 (양식 한국어 헤더 ↔ DB 영문 컬럼)
- EAV 패턴(CORE_CONFIG_PROP) 피벗: NAME별 값을 양식의 개별 컬럼에 분배
- 매핑 불가 컬럼의 graceful 처리 (빈 값 또는 사용자 안내)
- 출력 엑셀의 서식 보존 (병합 셀, 글꼴, 테두리, 열 너비 등)
- 데이터 건수 정확성 (30대 전체 또는 필터 결과)
- 단순 양식(TPL-6~8) vs 실무 양식(TPL-1~5)의 매핑 성공률 비교

#### 3.4.3 유사단어 추가/수정 시나리오 테스트

대화 중 유사단어를 추가하거나 수정하는 워크플로우를 검증한다.

| # | 시나리오 | 수행 절차 | 검증 포인트 |
|---|---------|----------|-----------|
| SYN-1 | 새 유사단어 자동 제안 | 1. "장비명 목록을 보여줘" 질의<br>2. 시스템이 "장비명"을 HOSTNAME으로 매핑했음을 응답<br>3. `"장비명" → HOSTNAME` 유사어 등록을 제안<br>4. 사용자가 "등록" 선택 | synonym_registrar 노드가 `synonyms:global`에 "장비명" 추가, 이후 "장비명" 질의 시 바로 매핑 |
| SYN-2 | 수동 유사단어 추가 | 1. "HOSTNAME에 '머신명'을 유사단어로 추가해줘" 직접 요청 | CLI 또는 대화를 통한 수동 등록, Redis 즉시 반영 확인 |
| SYN-3 | 유사단어 일괄 추가 (YAML) | 1. `global_synonyms.yaml`에 새 항목 추가<br>2. `load-synonyms` CLI 실행 | 기존 사전 보존 + 새 항목 병합 (중복 제거) |
| SYN-4 | 유사단어 수정 | 1. "HOSTNAME의 유사단어에서 '호스트'를 제거해줘" 요청 | 특정 유사단어 삭제 후 Redis 반영, 이후 "호스트" 단독 질의 시 매핑 변경 확인 |
| SYN-5 | 유사단어 충돌 해결 | 1. "서버"가 HOSTNAME과 NAME 양쪽에 유사단어로 등록된 경우<br>2. "서버 목록 보여줘" 질의 시 어느 컬럼을 선택하는지 확인 | DB별 synonyms > 글로벌 synonyms 우선순위 적용, 모호한 경우 사용자에게 확인 요청 |
| SYN-6 | YAML 수정 후 자동 갱신 | 1. YAML 파일 수정<br>2. `check_and_reload()` 호출<br>3. 변경 감지 → Redis 자동 갱신 확인 | mtime 기반 변경 감지 정확성, 부분 갱신 아닌 전체 리로드 |
| SYN-7 | Redis → YAML 내보내기 | 1. 대화/CLI로 여러 유사단어 추가<br>2. `export-synonyms` 실행<br>3. 내보낸 YAML 재로드 후 데이터 일치 확인 | 라운드트립 무손실 (Redis → YAML → Redis) |

### 3.5 테스트 실행 방법

```bash
# 1. DB2 테이블/데이터 생성 (Docker 컨테이너 사용)

# 1-0. DB2 컨테이너 시작 및 DB 매니저 시작
docker start infra_db2
docker exec infra_db2 bash -c "su - db2inst1 -c 'db2start'"

# 1-1. SQL 파일 컨테이너로 복사
# 참고: db2inst1 홈 디렉토리는 /database/config/db2inst1 (NOT /home/db2inst1)
docker cp testdata/ infra_db2:/database/config/db2inst1/testdata/

# 1-2. DDL 실행
docker exec infra_db2 bash -c "su - db2inst1 -c 'db2 connect to INFRADB && db2 -tvf /database/config/db2inst1/testdata/01_create_tables.sql'"

# 1-3. 데이터 INSERT
docker exec infra_db2 bash -c "su - db2inst1 -c 'db2 connect to INFRADB && db2 -tvf /database/config/db2inst1/testdata/02_insert_cmm_resource.sql'"
docker exec infra_db2 bash -c "su - db2inst1 -c 'db2 connect to INFRADB && db2 -tvf /database/config/db2inst1/testdata/03_insert_core_config_prop.sql'"

# 1-4. 검증
docker exec infra_db2 bash -c "su - db2inst1 -c 'db2 connect to INFRADB && db2 -tvf /database/config/db2inst1/testdata/04_verify_data.sql'"

# 정리 시:
# docker exec infra_db2 bash -c "su - db2inst1 -c 'db2 connect to INFRADB && db2 -tvf /database/config/db2inst1/testdata/99_cleanup.sql'"

# 2. Redis 시작 확인
docker exec -it collectorinfra-redis redis-cli ping  # PONG

# 3. MCP 서버 시작 (별도 터미널)
# 설정 파일: mcp_server/config.toml (infra_db2 소스 사용)
# DB 연결 문자열: mcp_server/.env 에서 환경변수로 관리

# 3-0. 사전 준비 (최초 1회)
pip install "mcp[cli]"                                     # FastMCP 패키지 설치
cp mcp_server/.env.example mcp_server/.env                 # .env 파일 생성 (연결 문자열 설정)

# 3-1. MCP 서버 실행
cd mcp_server
python -m mcp_server
# 활성 소스로 infra_db (postgresql), infra_db2 (db2)가 표시되면 정상

# 4. 캐시 생성 (CLI 스크립트 사용)
python scripts/schema_cache_cli.py generate --db-id infra_db2 --force
python scripts/schema_cache_cli.py generate-descriptions --db-id infra_db2

# 5. 글로벌 유사단어 사전 로드
python scripts/schema_cache_cli.py load-synonyms --file config/global_synonyms.yaml

# 6. 캐시 및 유사단어 상태 확인
python scripts/schema_cache_cli.py status --db-id infra_db2
python scripts/schema_cache_cli.py synonym-status

# 7. E2E 자연어 질의 테스트
python -m pytest tests/test_e2e_polestar.py -v --timeout=120

# 8. 엑셀 양식 기반 테스트 (sample/취합 예시1.xlsx 사용)
python -m pytest tests/test_e2e_polestar.py -k "test_template" -v --timeout=180

# 9. 유사단어 관리 테스트
python -m pytest tests/test_e2e_polestar.py -k "test_synonym" -v --timeout=120
```

---

## 4. 영역 3: 글로벌 유사단어 사전 생성

### 4.1 개요

`synonyms:global` Redis 키에 저장될 글로벌 유사단어 사전의 초기 데이터를 파일로 정의한다. 이 사전은 DB에 독립적이며, 인프라 모니터링 도메인의 공통 용어를 한국어/영어 유사 표현으로 매핑한다.

### 4.2 파일 형식 및 위치

**기본 형식**: YAML
**파일 위치**: `config/global_synonyms.yaml`

YAML을 기본 형식으로 선택한 이유:
- 한국어 문자열이 많아 가독성이 JSON보다 우수하다
- 주석을 지원하여 각 항목의 배경 설명을 기록할 수 있다
- 계층 구조 표현이 간결하다

### 4.3 YAML 구조 설계

```yaml
# config/global_synonyms.yaml
# 글로벌 유사단어 사전 - 인프라 모니터링 도메인
# 각 항목: 컬럼명(bare name) -> words(유사 표현 목록), description(컬럼 의미)
#
# 이 파일은 synonym_loader.py에 의해 Redis synonyms:global에 로드된다.
# 수정 후 로더를 재실행하면 Redis에 반영된다.

version: "1.0"
domain: "infrastructure_monitoring"
updated_at: "2026-03-20"

columns:
  # ===== 서버 기본 정보 =====
  HOSTNAME:
    description: "서버의 호스트명"
    words:
      - "호스트명"
      - "서버명"
      - "호스트 이름"
      - "서버 이름"
      - "host name"
      - "서버"
      - "호스트"

  IPADDRESS:
    description: "서버의 IP 주소"
    words:
      - "IP주소"
      - "IP 주소"
      - "아이피"
      - "아이피 주소"
      - "IP"
      - "ip address"
      - "아이피주소"

  LOCATION:
    description: "서버의 물리적 위치"
    words:
      - "위치"
      - "설치 위치"
      - "물리 위치"
      - "장소"
      - "서버 위치"

  # ===== 리소스 분류 =====
  RESOURCE_TYPE:
    description: "리소스의 유형 분류"
    words:
      - "리소스 유형"
      - "리소스 종류"
      - "자원 유형"
      - "자원 종류"
      - "resource type"
      - "유형"
      - "종류"
      - "타입"

  AVAIL_STATUS:
    description: "리소스의 가용 상태 (0=정상, 1=비정상)"
    words:
      - "가용 상태"
      - "상태"
      - "사용 가능 상태"
      - "가용성"
      - "서버 상태"
      - "리소스 상태"
      - "정상 여부"

  DESCRIPTION:
    description: "리소스에 대한 설명 텍스트"
    words:
      - "설명"
      - "리소스 설명"
      - "서버 설명"
      - "상세 설명"
      - "비고"

  NAME:
    description: "리소스 또는 설정 항목의 이름"
    words:
      - "이름"
      - "리소스명"
      - "항목명"
      - "필드명"
      - "name"

  # ===== 시간 정보 =====
  CTIME:
    description: "리소스 생성 시각 (epoch 밀리초)"
    words:
      - "생성 시각"
      - "생성 시간"
      - "생성일"
      - "등록일"
      - "created time"

  MTIME:
    description: "리소스 수정 시각 (epoch 밀리초)"
    words:
      - "수정 시각"
      - "수정 시간"
      - "수정일"
      - "변경일"
      - "최종 수정일"
      - "modified time"

  # ===== 계층 구조 =====
  PARENT_RESOURCE_ID:
    description: "부모 리소스의 ID (계층 구조)"
    words:
      - "부모 리소스"
      - "상위 리소스"
      - "부모 ID"
      - "상위 ID"

  PLATFORM_RESOURCE_ID:
    description: "플랫폼(서버) 리소스의 ID"
    words:
      - "플랫폼 리소스"
      - "서버 리소스"
      - "플랫폼 ID"

  # ===== EAV 패턴 (CORE_CONFIG_PROP) =====
  CONFIGURATION_ID:
    description: "설정 그룹 식별자 (EAV의 Entity)"
    words:
      - "설정 그룹"
      - "설정 ID"
      - "구성 ID"
      - "configuration"

  STRINGVALUE_SHORT:
    description: "설정 값 (EAV의 Value, 짧은 텍스트)"
    words:
      - "설정 값"
      - "설정값"
      - "속성 값"
      - "속성값"
      - "value"
      - "값"

  STRINGVALUE:
    description: "대용량 설정 값 (CLOB)"
    words:
      - "대용량 설정 값"
      - "CLOB 값"
      - "긴 설정 값"

  IS_LOB:
    description: "LOB 사용 여부 (0=SHORT, 1=CLOB)"
    words:
      - "LOB 여부"
      - "대용량 여부"
      - "CLOB 사용"

  PROPERTYDEFINITION_ID:
    description: "속성 정의 ID"
    words:
      - "속성 정의"
      - "정의 ID"
      - "property definition"

  # ===== 식별자/버전 =====
  UUID:
    description: "범용 고유 식별자"
    words:
      - "UUID"
      - "고유 식별자"
      - "유니크 ID"

  VERSION:
    description: "버전 정보"
    words:
      - "버전"
      - "version"
      - "소프트웨어 버전"

  # ===== 기타 =====
  DTYPE:
    description: "데이터 타입 분류 (리소스 구분 또는 속성 유형)"
    words:
      - "데이터 타입"
      - "구분"
      - "분류"
      - "타입"

  RESOURCE_KEY:
    description: "리소스 고유 키 (UUID 형식)"
    words:
      - "리소스 키"
      - "자원 키"
      - "고유 키"
      - "resource key"

# ===== RESOURCE_TYPE 값에 대한 유사 단어 =====
# (컬럼명이 아닌 값(value)에 대한 매핑)
resource_type_values:
  "server.Cpu":
    words: ["CPU", "씨피유", "프로세서", "CPU 코어", "코어"]
  "server.Cpus":
    words: ["CPU 컨테이너", "CPU 관리", "전체 CPU"]
  "server.Memory":
    words: ["메모리", "물리 메모리", "RAM", "물리적 메모리"]
  "server.VirtualMemory":
    words: ["가상 메모리", "가상메모리", "swap", "스왑"]
  "server.OtherMemory":
    words: ["기타 메모리", "페이지 메모리", "문맥교환"]
  "server.FileSystem":
    words: ["파일시스템", "파일 시스템", "마운트포인트", "디스크 파티션", "파티션"]
  "server.FileSystems":
    words: ["파일시스템 컨테이너", "파일시스템 관리", "전체 파일시스템"]
  "server.Disks":
    words: ["디스크", "디스크 관리", "전체 디스크"]
  "server.NetworkInterface":
    words: ["네트워크 인터페이스", "NIC", "네트워크 카드", "네트워크", "랜카드"]
  "server.NetworkInterfaces":
    words: ["네트워크 인터페이스 컨테이너", "네트워크 관리"]
  "server.Netstat":
    words: ["네트워크 세션", "네트워크 연결", "세션", "연결 정보"]
  "server.Process":
    words: ["프로세스", "프로세스 관제", "실행 프로세스"]
  "server.ProcessMonitor":
    words: ["프로세스 모니터", "프로세스 감시", "프로세스 모니터링"]
  "server.LogMonitor":
    words: ["로그 모니터", "로그 감시", "로그 모니터링"]
  "server.Other":
    words: ["기타 정보", "기타", "IPCS", "OS Table"]
  "server.Hbas":
    words: ["HBA 관리", "HBA 컨테이너"]
  "server.Hba":
    words: ["HBA", "HBA 어댑터", "호스트 버스 어댑터"]
  "server.HbaPort":
    words: ["HBA 포트", "HBA port"]
  "management.MonitorGroup":
    words: ["모니터 그룹", "모니터링 그룹", "감시 그룹"]

# ===== EAV NAME 값에 대한 유사 단어 =====
# (CORE_CONFIG_PROP.NAME 컬럼의 값에 대한 매핑)
eav_name_values:
  "AgentID":
    words: ["에이전트 ID", "에이전트 식별자", "agent ID"]
  "AgentVersion":
    words: ["에이전트 버전", "agent version", "모니터링 에이전트 버전"]
  "GMT":
    words: ["타임존", "시간대", "GMT", "timezone"]
  "Hostname":
    words: ["호스트명", "서버명", "hostname"]
  "IPaddress":
    words: ["IP주소", "IP 주소", "아이피", "ip address"]
  "InstallPath":
    words: ["설치 경로", "에이전트 설치 경로", "install path"]
  "Model":
    words: ["서버 모델", "모델명", "하드웨어 모델", "장비 모델"]
  "OSParameter":
    words: ["OS 파라미터", "커널 파라미터", "시스템 파라미터", "sysctl"]
  "OSType":
    words: ["운영체제", "OS 종류", "운영체제 종류", "OS", "운영체제 타입"]
  "OSVerson":
    words: ["OS 버전", "운영체제 버전", "커널 버전", "OS version"]
  "SerialNumber":
    words: ["시리얼 번호", "일련번호", "serial number", "S/N"]
  "Vendor":
    words: ["제조사", "벤더", "vendor", "서버 제조사", "하드웨어 제조사"]
```

### 4.4 YAML 구조 상세

```yaml
# 최상위 구조
version: str           # 사전 버전
domain: str            # 도메인 식별자
updated_at: str        # 최종 수정일

columns:               # 컬럼명 기반 유사단어 (synonyms:global에 저장)
  {COLUMN_NAME}:
    description: str   # 컬럼 의미 설명
    words: list[str]   # 유사 표현 목록

resource_type_values:  # RESOURCE_TYPE 값 기반 유사단어 (별도 Redis 키 또는 컬럼 synonyms에 부가 정보로 저장)
  {VALUE}:
    words: list[str]

eav_name_values:       # EAV NAME 값 기반 유사단어
  {VALUE}:
    words: list[str]
```

**3가지 계층의 유사단어**:
1. **columns**: 컬럼명(bare name) 기준 -- Redis `synonyms:global`에 직접 저장
2. **resource_type_values**: 특정 컬럼의 값에 대한 유사 표현 -- 쿼리 생성 시 LLM 프롬프트에 참조 정보로 제공
3. **eav_name_values**: EAV 패턴의 Attribute 이름에 대한 유사 표현 -- 쿼리 생성 시 LLM 프롬프트에 참조 정보로 제공

### 4.5 향후 다중 소스 지원 계획

글로벌 유사단어 사전은 YAML 외에 Excel, MD 파일에서도 관리할 수 있도록 확장한다.

#### 4.5.1 Excel 형식 (`config/global_synonyms.xlsx`)

| 시트명 | 내용 |
|--------|------|
| columns | 컬럼명 유사단어 |
| resource_types | RESOURCE_TYPE 값 유사단어 |
| eav_names | EAV NAME 값 유사단어 |

**columns 시트 구조**:

| 컬럼명 | 설명 | 유사어1 | 유사어2 | 유사어3 | ... |
|--------|------|---------|---------|---------|-----|
| HOSTNAME | 서버의 호스트명 | 호스트명 | 서버명 | 호스트 이름 | ... |
| IPADDRESS | 서버의 IP 주소 | IP주소 | 아이피 | IP 주소 | ... |

**파싱 규칙**:
- A열: 컬럼명 (필수)
- B열: 설명 (선택, 빈 셀이면 기존 description 보존)
- C열~: 유사 단어 (빈 셀까지 읽기)
- 빈 행은 무시
- 1행은 헤더로 취급

#### 4.5.2 Markdown 형식 (`config/global_synonyms.md`)

```markdown
## 컬럼 유사단어

| 컬럼명 | 설명 | 유사 단어 |
|--------|------|----------|
| HOSTNAME | 서버의 호스트명 | 호스트명, 서버명, 호스트 이름, host name |
| IPADDRESS | 서버의 IP 주소 | IP주소, 아이피, IP 주소, ip address |
```

**파싱 규칙**:
- Markdown 테이블 형식 (`|` 구분자)
- 1행: 헤더
- 2행: 구분선 (`|---|`)
- 3행~: 데이터
- "유사 단어" 열은 쉼표로 구분된 목록

#### 4.5.3 파일 변경 감지 및 자동 갱신

**감지 방식**: 파일 수정 시각(mtime) 기반

```
synonym_loader 초기화 시:
  1. config/global_synonyms.yaml 의 mtime을 기록
  2. Redis에 synonyms:global:_meta 키로 마지막 로드 시각 저장
  3. 주기적으로 (또는 요청 시) mtime 비교
  4. 변경 감지 시 파일 재로드 -> Redis 갱신
```

**자동 갱신 트리거 옵션** (향후 구현):
- **폴링 방식**: 60초 주기로 파일 mtime 체크
- **이벤트 방식**: `watchdog` 라이브러리로 파일 변경 이벤트 감지
- **수동 방식**: CLI 명령으로 강제 리로드 (`python scripts/schema_cache_cli.py reload-synonyms`)

---

## 5. 영역 4: 코드에서 글로벌 유사단어 사전 로드 기능

### 5.1 모듈 위치 및 구조

**파일**: `src/schema_cache/synonym_loader.py`

### 5.2 클래스/함수 인터페이스 설계

```python
class SynonymLoader:
    """파일에서 글로벌 유사단어 사전을 로드하여 Redis에 저장한다."""

    def __init__(
        self,
        cache_manager: SchemaCacheManager,
        config_dir: str = "config",
    ) -> None:
        """로더를 초기화한다.

        Args:
            cache_manager: SchemaCacheManager 인스턴스
            config_dir: 설정 파일 디렉토리 경로
        """

    async def load_from_yaml(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """YAML 파일에서 글로벌 유사단어를 로드하여 Redis에 저장한다.

        Args:
            file_path: YAML 파일 경로
            merge: True이면 기존 데이터와 병합, False이면 덮어쓰기

        Returns:
            로드 결과 (추가/갱신 항목 수, 오류 등)
        """

    async def load_from_json(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """JSON 파일에서 글로벌 유사단어를 로드한다.

        JSON 구조는 YAML과 동일하다.
        """

    async def load_from_excel(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """Excel 파일에서 글로벌 유사단어를 로드한다. (향후 구현)

        시트별로 columns, resource_types, eav_names를 파싱한다.
        """

    async def load_from_markdown(
        self,
        file_path: str,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """Markdown 파일에서 글로벌 유사단어를 로드한다. (향후 구현)

        Markdown 테이블을 파싱한다.
        """

    async def load_auto(
        self,
        file_path: str | None = None,
        merge: bool = True,
    ) -> SynonymLoadResult:
        """파일 확장자를 자동 감지하여 적절한 로더를 호출한다.

        file_path가 None이면 기본 경로(config/global_synonyms.yaml)를 사용한다.

        지원 확장자: .yaml, .yml, .json, .xlsx, .md
        """

    async def check_and_reload(self) -> SynonymLoadResult | None:
        """파일 변경을 감지하고, 변경되었으면 자동 리로드한다.

        마지막 로드 시각과 파일 mtime을 비교한다.

        Returns:
            변경 감지 시 로드 결과, 변경 없으면 None
        """

    async def export_to_yaml(
        self,
        output_path: str,
    ) -> bool:
        """현재 Redis의 글로벌 유사단어를 YAML 파일로 내보낸다.

        Redis -> YAML 역방향 변환. 백업 또는 편집용.

        Returns:
            성공 여부
        """

    async def export_to_json(
        self,
        output_path: str,
    ) -> bool:
        """현재 Redis의 글로벌 유사단어를 JSON 파일로 내보낸다."""

    def get_last_loaded_at(self) -> str | None:
        """마지막 로드 시각을 반환한다."""

    def get_loaded_stats(self) -> dict:
        """마지막 로드 통계를 반환한다.

        Returns:
            {
                "file_path": str,
                "loaded_at": str,
                "column_count": int,
                "resource_type_count": int,
                "eav_name_count": int,
                "total_words": int,
            }
        """
```

### 5.3 결과 데이터 클래스

```python
@dataclass
class SynonymLoadResult:
    """유사단어 로드 결과."""

    status: str              # "success" | "partial" | "error"
    file_path: str           # 로드한 파일 경로
    columns_loaded: int      # 로드한 컬럼 유사단어 수
    resource_types_loaded: int  # 로드한 RESOURCE_TYPE 값 유사단어 수
    eav_names_loaded: int    # 로드한 EAV NAME 값 유사단어 수
    total_words: int         # 전체 단어 수
    merge_mode: bool         # 병합 모드 여부
    errors: list[str]        # 발생한 오류 목록
    message: str             # 결과 메시지
```

### 5.4 YAML 파싱 상세 흐름

```
1. YAML 파일 로드 (yaml.safe_load)
2. version, domain 메타데이터 확인
3. columns 섹션 파싱:
   a. 각 항목에서 words, description 추출
   b. merge=True이면 기존 글로벌 사전과 병합
      - 기존 words와 새 words를 합집합 (순서 보존, 중복 제거)
      - description은 새 값 우선 (비어있으면 기존 보존)
   c. SchemaCacheManager.save_global_synonyms() 호출
      - 내부적으로 RedisSchemaCache.save_global_synonyms() 사용
4. resource_type_values 섹션 파싱:
   a. 별도 Redis 키 또는 글로벌 사전의 특수 네임스페이스에 저장
      - 키 형식: "RESOURCE_TYPE::{value}" (예: "RESOURCE_TYPE::server.Cpu")
   b. 또는 별도 Redis Hash `synonyms:resource_types`에 저장
5. eav_name_values 섹션 파싱:
   a. 키 형식: "EAV_NAME::{name}" (예: "EAV_NAME::OSType")
   b. 또는 별도 Redis Hash `synonyms:eav_names`에 저장
6. 결과 통계 집계 및 SynonymLoadResult 반환
```

### 5.5 기존 코드와의 연동 포인트

#### 연동 포인트 1: `SchemaCacheManager`

`synonym_loader.py`는 `SchemaCacheManager`의 다음 메서드를 사용한다:

| 메서드 | 용도 |
|--------|------|
| `save_global_synonyms(synonyms)` | columns 섹션의 유사단어를 Redis에 저장 |
| `add_global_synonym(column_name, words)` | 개별 컬럼 유사단어 추가 (merge 모드) |
| `get_global_synonyms_full()` | 기존 글로벌 사전 조회 (merge 전 비교용) |
| `update_global_description(column_name, description)` | 컬럼 설명 저장 |

#### 연동 포인트 2: `RedisSchemaCache`

resource_type_values, eav_name_values는 기존 `synonyms:global` Hash에 네임스페이스 프리픽스를 붙여 저장하거나, 새로운 Redis Hash 키를 추가한다.

**방안 A (프리픽스 방식, 기존 코드 변경 최소화)**:
```
synonyms:global 에 저장:
  "HOSTNAME" -> {"words": [...], "description": "..."}
  "RESOURCE_TYPE::server.Cpu" -> {"words": [...]}
  "EAV_NAME::OSType" -> {"words": [...]}
```

**방안 B (별도 Hash 방식, 관심사 분리)**:
```
synonyms:global           -> 컬럼명 기반 유사단어 (기존 그대로)
synonyms:resource_types   -> RESOURCE_TYPE 값 유사단어 (신규)
synonyms:eav_names        -> EAV NAME 값 유사단어 (신규)
```

**권장**: 방안 B -- 기존 `synonyms:global`의 구조를 변경하지 않고, 새로운 Redis Hash를 추가하여 확장한다. `RedisSchemaCache`에 `save_resource_type_synonyms()`, `load_resource_type_synonyms()` 등의 메서드를 추가한다.

#### 연동 포인트 3: `query_generator.py`

쿼리 생성 시 LLM 프롬프트에 resource_type_values와 eav_name_values를 참조 정보로 포함한다.

```python
# query_generator.py 수정 예상 위치
# 프롬프트에 추가할 컨텍스트:
# "RESOURCE_TYPE 컬럼의 값과 한국어 표현:
#   server.Cpu = CPU, 씨피유, 프로세서
#   server.Memory = 메모리, RAM
#   ..."
```

#### 연동 포인트 4: `synonym_registrar.py`

기존 `synonym_registrar` 노드는 사용자가 대화 중 새 유사어를 등록할 때 `SchemaCacheManager.add_synonyms()` 및 `add_global_synonym()`을 호출한다. `SynonymLoader`는 이와 독립적으로 파일 기반 일괄 로드를 수행하며, 두 경로 모두 동일한 Redis 키에 저장되므로 자동으로 병합된다.

#### 연동 포인트 5: `schema_cache_cli.py`

기존 CLI 스크립트에 유사단어 사전 관리 명령을 추가한다.

```bash
# 유사단어 사전 파일 로드
python scripts/schema_cache_cli.py load-synonyms --file config/global_synonyms.yaml
python scripts/schema_cache_cli.py load-synonyms --file config/global_synonyms.xlsx

# 현재 Redis 글로벌 사전을 파일로 내보내기
python scripts/schema_cache_cli.py export-synonyms --format yaml --output config/global_synonyms_backup.yaml

# 글로벌 사전 상태 조회
python scripts/schema_cache_cli.py synonym-status
```

### 5.6 RedisSchemaCache 확장 메서드 (신규)

resource_type_values, eav_name_values 저장을 위해 `RedisSchemaCache`에 추가할 메서드:

```python
class RedisSchemaCache:
    # ... 기존 코드 ...

    RESOURCE_TYPE_SYNONYMS_KEY = "synonyms:resource_types"
    EAV_NAME_SYNONYMS_KEY = "synonyms:eav_names"

    async def save_resource_type_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """RESOURCE_TYPE 값의 유사단어를 저장한다.

        Args:
            synonyms: {resource_type_value: [synonym, ...]} 매핑

        Returns:
            저장 성공 여부
        """

    async def load_resource_type_synonyms(self) -> dict[str, list[str]]:
        """RESOURCE_TYPE 값의 유사단어를 로드한다."""

    async def save_eav_name_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """EAV NAME 값의 유사단어를 저장한다."""

    async def load_eav_name_synonyms(self) -> dict[str, list[str]]:
        """EAV NAME 값의 유사단어를 로드한다."""
```

---

## 6. 디렉토리 구조

```
collectorinfra/
├── config/
│   └── global_synonyms.yaml         # [영역 3] 글로벌 유사단어 사전 (초기 데이터)
│
├── mcp_server/
│   ├── config.toml                  # [영역 2] MCP 서버 설정 (infra_db2 소스 포함, 기존)
│   └── .env                         # [영역 2] DB 연결 문자열 (환경변수, 기존)
│
├── testdata/
│   ├── README.md                    # [영역 1] SQL 스크립트 설명
│   ├── 01_create_tables.sql         # [영역 1] CREATE TABLE DDL
│   ├── 02_insert_cmm_resource.sql   # [영역 1] CMM_RESOURCE INSERT (약 700행, 30대 서버)
│   ├── 03_insert_core_config_prop.sql # [영역 1] CORE_CONFIG_PROP INSERT (360행)
│   ├── 04_verify_data.sql           # [영역 1] 데이터 검증 쿼리
│   ├── 99_cleanup.sql               # [영역 1] 정리 스크립트
│   └── templates/                   # [영역 2] 단순 양식 템플릿 (신규 생성)
│       ├── server_list_template.xlsx       # 서버 인벤토리 목록 (1단 헤더)
│       ├── resource_status_template.xlsx   # 리소스 상태 보고서 (1단 헤더)
│       └── config_report_template.xlsx     # 서버 설정 정보 보고서 (1단 헤더)
│
├── sample/
│   ├── 취합 예시1.xlsx               # [영역 2] 실무 양식 테스트용 (3시트: 성능관리, 자산정보, 계획정지)
│   ├── Table Schema.xlsx             # DB2 DDL 포함 (기존)
│   ├── CMM_RESOURCE(873.xlsx         # CMM_RESOURCE 실제 데이터 (기존)
│   └── CORE_CONFIG_PROP(110.xlsx     # CORE_CONFIG_PROP 실제 데이터 (기존)
│
├── src/
│   └── schema_cache/
│       ├── synonym_loader.py        # [영역 4] 글로벌 유사단어 파일 로더 (신규)
│       ├── redis_cache.py           # [영역 4] RedisSchemaCache (확장: resource_type/eav_name 메서드)
│       ├── cache_manager.py         # (기존) SchemaCacheManager
│       └── description_generator.py # (기존) DescriptionGenerator
│
├── tests/
│   ├── test_e2e_polestar.py         # [영역 2] E2E 테스트 (DB2 -> 캐시 -> 질의)
│   └── test_synonym_loader.py       # [영역 4] synonym_loader 단위 테스트
│
└── scripts/
    └── schema_cache_cli.py          # [영역 4] CLI 확장 (load-synonyms 등)
```

---

## 7. 구현 순서 및 의존 관계

```
Phase A: 영역 1 - DB2 테스트 데이터 생성
  ├── A-1: DDL 스크립트 작성 (01_create_tables.sql)
  ├── A-2: CMM_RESOURCE INSERT 생성 (02_insert_cmm_resource.sql)
  ├── A-3: CORE_CONFIG_PROP INSERT 생성 (03_insert_core_config_prop.sql)
  ├── A-4: 검증/정리 스크립트 작성 (04_verify_data.sql, 99_cleanup.sql)
  └── A-5: DB2에 실행 + 검증
      |
Phase B: 영역 3 - 글로벌 유사단어 사전 파일 생성
  ├── B-1: config/global_synonyms.yaml 작성
  └── B-2: YAML 구조 검증 (yaml.safe_load로 파싱 확인)
      |
Phase C: 영역 4 - synonym_loader.py 구현
  ├── C-1: SynonymLoader 클래스 기본 구현 (YAML 로더)
  ├── C-2: load_auto() 메서드 구현
  ├── C-3: export_to_yaml() 메서드 구현
  ├── C-4: check_and_reload() 메서드 구현
  ├── C-5: RedisSchemaCache 확장 (resource_type, eav_name 메서드)
  ├── C-6: schema_cache_cli.py 확장 (load-synonyms, export-synonyms 명령)
  └── C-7: 단위 테스트 작성 (test_synonym_loader.py)
      |
Phase D: 영역 2 - Redis 캐시 E2E 테스트
  ├── D-1: MCP 서버 DB2 연결 확인 (mcp_server/config.toml + .env)
  ├── D-2: 캐시 자동 생성 테스트 (refresh_cache)
  ├── D-3: LLM 설명/유사단어 생성 테스트 (generate_for_db)
  ├── D-4: 글로벌 사전 동기화 테스트 (sync_global_synonyms)
  ├── D-5: synonym_loader로 YAML 로드 테스트
  ├── D-6: Redis 키/값 검증
  ├── D-7: E2E 자연어 질의 테스트 (E2E-1 ~ E2E-10)
  ├── D-8: 단순 양식 템플릿 3종 작성 (testdata/templates/)
  ├── D-9: 엑셀 양식 기반 데이터 생성 테스트 (TPL-1~5 취합 예시1.xlsx + TPL-6~8 단순 양식)
  └── D-10: 유사단어 추가/수정/동기화 테스트 (SYN-1 ~ SYN-7)
```

### 의존 관계 요약

```
영역 1 (DB2 데이터) ──────────────────────────┐
                                              ↓
영역 3 (YAML 사전) ──→ 영역 4 (synonym_loader) ──→ 영역 2 (E2E 테스트)
```

- **영역 1**은 독립 실행 가능 (DB2 환경만 필요)
- **영역 3**은 독립 실행 가능 (파일 작성만)
- **영역 4**는 영역 3의 YAML 파일에 의존
- **영역 2**는 영역 1(DB 데이터)과 영역 4(synonym_loader)에 의존

### 예상 소요 시간

| Phase | 작업 | 예상 소요 |
|-------|------|----------|
| A | DB2 테스트 데이터 생성 (30대 서버, 약 1060행) | 2일 |
| B | 글로벌 유사단어 YAML 작성 | 0.5일 |
| C | synonym_loader 구현 + 테스트 | 2일 |
| D-1~D-7 | 캐시 파이프라인 E2E + 자연어 질의 테스트 | 1.5일 |
| D-8~D-9 | 취합 예시1.xlsx 시트별 매핑 분석 + 양식 기반 데이터 생성 테스트 | 1일 |
| D-10 | 유사단어 추가/수정/동기화 테스트 | 0.5일 |
| **합계** | | **7.5일** |

---

## 8. 성공 기준

### 8.1 영역 1 성공 기준

| 기준 | 설명 |
|------|------|
| DDL 실행 성공 | DB2에서 CREATE TABLE이 에러 없이 완료 |
| 데이터 투입 완료 | CMM_RESOURCE 약 700행, CORE_CONFIG_PROP 360행 정상 INSERT |
| 서버 30대 | HOSTNAME이 30종류 존재 (svr-web 10, svr-was 10, svr-db 10) |
| RESOURCE_TYPE 다양성 | 15종 이상의 RESOURCE_TYPE이 존재 |
| 플랫폼 다양성 | VMware, HPE, Dell 3종 제조사 혼합 |
| AVAIL_STATUS 다양성 | 비정상(1) 리소스가 전체의 5~8% |
| 검증 쿼리 통과 | 04_verify_data.sql의 10개 SELECT 결과가 기대값과 일치 |

### 8.2 영역 2 성공 기준

| 기준 | 설명 |
|------|------|
| DBHub 연결 성공 | DBHub MCP 서버가 DB2에 정상 연결 |
| 캐시 자동 생성 | refresh_cache() 후 Redis에 meta, tables, relationships 키 존재 |
| LLM 설명 생성 | descriptions 커버율 90% 이상 (71개 중 64개 이상) |
| 유사단어 생성 | synonyms 커버율 90% 이상 |
| DB 설명 생성 | db_descriptions에 polestar 설명 존재, 도메인 키워드 포함 |
| 글로벌 사전 동기화 | sync_global_synonyms() 후 HOSTNAME, IPADDRESS 등 글로벌 사전에 존재 |
| E2E 질의 정확도 | 10개 자연어 질의 시나리오 중 7개 이상에서 올바른 SQL 생성 |
| 실무 양식 테스트 | 5개 TPL 시나리오(TPL-1~5, 취합 예시1.xlsx) 중 4개 이상에서 양식 매핑 + 데이터 채우기 성공 |
| 단순 양식 테스트 | 3개 TPL 시나리오(TPL-6~8, 단순 템플릿) 전부 성공 (1단 헤더 기본 매핑) |
| 유사단어 관리 | 7개 SYN 시나리오 중 6개 이상에서 추가/수정/동기화 정상 동작 |

### 8.3 영역 3 성공 기준

| 기준 | 설명 |
|------|------|
| YAML 파싱 성공 | yaml.safe_load()로 에러 없이 파싱 |
| 컬럼 유사단어 | 20개 이상 컬럼에 대해 유사 단어 정의 |
| RESOURCE_TYPE 값 유사단어 | 19종 RESOURCE_TYPE에 대해 한국어 표현 정의 |
| EAV NAME 값 유사단어 | 12종 NAME에 대해 한국어 표현 정의 |
| 한국어 품질 | 각 항목에 최소 3개 이상의 한국어 유사 표현 |

### 8.4 영역 4 성공 기준

| 기준 | 설명 |
|------|------|
| YAML 로드 성공 | load_from_yaml() 후 Redis synonyms:global에 데이터 저장 |
| merge 동작 | 기존 데이터 보존하며 새 데이터 병합 (중복 제거) |
| export 동작 | Redis -> YAML 내보내기 후 재로드 시 데이터 일관성 유지 |
| 변경 감지 | YAML 파일 수정 후 check_and_reload()가 변경을 감지하여 리로드 |
| CLI 통합 | load-synonyms, export-synonyms 명령이 정상 동작 |
| resource_type 저장 | resource_type_values가 별도 Redis Hash에 저장 |
| eav_name 저장 | eav_name_values가 별도 Redis Hash에 저장 |

---

## 9. 리스크 및 완화 방안

| # | 리스크 | 영향도 | 발생 가능성 | 완화 방안 |
|---|--------|--------|-----------|----------|
| R-1 | DB2 테스트 환경 미확보 | 높음 | 중간 | SQLite 또는 PostgreSQL로 대체 가능한 범용 SQL로 INSERT 문 작성. DB2 전용 구문(`OCTETS`, `IDENTITY`)은 조건부 포함 |
| R-2 | DBHub의 DB2 지원 미흡 | 높음 | 낮음 | DBHub 대신 직접 DB2 Python 드라이버(ibm_db)로 폴백 테스트 가능하도록 설계 |
| R-3 | LLM 설명 생성 품질 불안정 | 중간 | 중간 | 59컬럼 테이블은 배치 분할(30컬럼 단위), 재시도 1회, 파일 기반 유사단어 사전으로 LLM 의존도 감소 |
| R-4 | Redis 미설치 테스트 환경 | 중간 | 낮음 | 파일 캐시 폴백 테스트로 대체. Docker Compose로 Redis 테스트 환경 일괄 구성 제공 |
| R-5 | YAML 파일 인코딩 문제 | 낮음 | 낮음 | UTF-8 BOM 없는 형식으로 통일. 로더에서 encoding='utf-8' 명시 |
| R-6 | 글로벌 사전과 DB별 사전 충돌 | 중간 | 중간 | 우선순위 규칙 명확화: DB별 synonyms > 글로벌 synonyms. 병합 시 DB별 데이터가 우선 |
| R-7 | RESOURCE_TYPE/EAV_NAME 유사단어의 Redis 키 구조 변경 | 중간 | 낮음 | 방안 B(별도 Hash) 채택으로 기존 synonyms:global 구조 변경 없이 확장. 기존 코드 영향 최소화 |
| R-8 | 테스트 데이터의 현실성 부족 | 낮음 | 중간 | 기존 polestar-data.md의 실제 데이터 패턴을 충실히 따르고, 서버 특성(VM/물리/DB)을 다양화 |

### 리스크 대응 우선순위

1. **R-1 (DB2 환경 미확보)**: 최우선 확인 사항. DB2 환경이 없으면 PostgreSQL 대체 DDL도 함께 준비
2. **R-3 (LLM 품질)**: 파일 기반 글로벌 유사단어 사전(영역 3)이 LLM 품질 리스크를 보완
3. **R-6 (사전 충돌)**: 구현 시 우선순위 규칙을 코드에 명확히 문서화

---

## 부록: 기존 코드 참조 요약

### 사용하는 기존 모듈

| 모듈 | 역할 | 본 계획에서의 활용 |
|------|------|-----------------|
| `src/schema_cache/redis_cache.py::RedisSchemaCache` | Redis 캐시 CRUD | 글로벌 사전 저장/로드, resource_type/eav_name 메서드 확장 |
| `src/schema_cache/cache_manager.py::SchemaCacheManager` | 캐시 통합 관리 | refresh_cache, sync_global_synonyms, 유사단어 CRUD |
| `src/schema_cache/description_generator.py::DescriptionGenerator` | LLM 설명 생성 | DB 설명, 컬럼 설명+유사단어 생성 |
| `src/nodes/synonym_registrar.py` | 대화 중 유사어 등록 | 독립적이나, 동일 Redis 키에 저장하므로 자동 병합 |

### Redis 키 전체 목록 (본 계획 포함)

| Redis 키 | 용도 | 출처 |
|-----------|------|------|
| `schema:db_descriptions` | DB별 설명 (Hash) | 기존 |
| `schema:{db_id}:meta` | 캐시 메타 (Hash) | 기존 |
| `schema:{db_id}:tables` | 테이블 스키마 (Hash) | 기존 |
| `schema:{db_id}:relationships` | FK 관계 (String) | 기존 |
| `schema:{db_id}:descriptions` | 컬럼 설명 (Hash) | 기존 |
| `schema:{db_id}:synonyms` | DB별 유사단어 (Hash) | 기존 |
| `synonyms:global` | 글로벌 컬럼 유사단어 (Hash) | 기존 |
| `synonyms:resource_types` | RESOURCE_TYPE 값 유사단어 (Hash) | **신규** |
| `synonyms:eav_names` | EAV NAME 값 유사단어 (Hash) | **신규** |

---

## 10. 구현 현황 분석 (2026-03-20)

### 10.1 영역별 구현 상태 요약

| 영역 | 계획 | 구현 상태 | 비고 |
|------|------|----------|------|
| **영역 1** | DB2 테이블 + 30대 서버 샘플 데이터 | **구현 완료** | 계획 대비 상회 (700행 → 1,115행) |
| **영역 2** | Redis 캐시 E2E 테스트 | **미구현** | DB2/DBHub/Redis 환경 필요 |
| **영역 3** | 글로벌 유사단어 YAML 파일 | **구현 완료** | 계획과 일치 |
| **영역 4** | synonym_loader.py 모듈 | **부분 구현** | 핵심 모듈 완성, 연동 미완료 |

### 10.2 영역 1: DB2 테스트 데이터 — 상세 분석

| 계획 항목 | 계획값 | 실제값 | 상태 | 비고 |
|----------|--------|--------|------|------|
| `testdata/01_create_tables.sql` | DDL 2개 테이블 | DDL 2개 테이블 | **완료** | CREATE SCHEMA 포함 |
| `testdata/02_insert_cmm_resource.sql` | 약 700행 | **1,115행** | **완료** | 계획 대비 60% 초과 |
| `testdata/03_insert_core_config_prop.sql` | 360행 | **360행** | **완료** | 정확히 일치 |
| `testdata/04_verify_data.sql` | 검증 쿼리 10개 | 검증 쿼리 작성됨 | **완료** | |
| `testdata/99_cleanup.sql` | 정리 스크립트 | 작성됨 | **완료** | |
| `testdata/README.md` | 실행 가이드 | 작성됨 | **완료** | |
| 서버 수 | 30대 | **30대** | **완료** | svr-web 10, svr-was 10, svr-db 10 |
| RESOURCE_TYPE 다양성 | 15종 이상 | **20종** | **완료** | 19종 + platform.server 포함 |
| CONFIGURATION_ID 범위 | 301~330 | **301~330** | **완료** | |
| AVAIL_STATUS 비정상 비율 | 5~8% | **1.1%** (12행) | **차이** | 계획보다 낮음. 필요 시 _generate_sql.py 수정 |
| `testdata/_generate_sql.py` | (계획 외) | 생성됨 | **추가** | SQL 재생성 가능한 Python 스크립트 |

### 10.3 영역 2: Redis 캐시 E2E 테스트 — 미구현 항목

| 계획 항목 | 상태 | 사유 |
|----------|------|------|
| MCP 서버 DB2 연결 확인 (`mcp_server/config.toml` + `.env`) | **미확인** | DB2 Docker 컨테이너 실행 필요 |
| Phase 1~6 (캐시 파이프라인) | **미실행** | DB2 + Redis + DBHub 실행 환경 필요 |
| Phase 7 (Redis 키/값 검증) | **미실행** | 상동 |
| Phase 8 E2E-1~10 (자연어 질의) | **미실행** | 상동 |
| `tests/test_e2e_polestar.py` | **미생성** | E2E 테스트 코드 미작성 |
| TPL-1~5 (취합 예시1.xlsx 양식 테스트) | **미실행** | E2E 환경 필요 |
| TPL-6~8 (단순 양식 테스트) | **미실행** | 상동. 양식 파일 3종은 생성 완료 |
| SYN-1~7 (유사단어 관리 시나리오) | **미실행** | 상동 |

### 10.4 영역 3: 글로벌 유사단어 YAML — 상세 분석

| 계획 항목 | 계획값 | 실제값 | 상태 |
|----------|--------|--------|------|
| `config/global_synonyms.yaml` | YAML 파일 | 생성됨 (8.2KB) | **완료** |
| columns | 20개 이상 | **20개** | **완료** |
| resource_type_values | 19종 | **19종** | **완료** |
| eav_name_values | 12종 | **12종** | **완료** |
| 총 유사단어 수 | 각 항목 3개 이상 | **205개** | **완료** |
| YAML 파싱 검증 | yaml.safe_load() 성공 | 성공 확인 | **완료** |

### 10.5 영역 4: synonym_loader.py — 상세 분석

#### 10.5.1 SynonymLoader 클래스

| 계획 메서드 | 구현 상태 | 비고 |
|------------|----------|------|
| `__init__(cache_manager, config_dir)` | **구현됨 (변경)** | 인자가 `cache_manager` → `redis_cache`로 변경됨. SchemaCacheManager 대신 RedisSchemaCache를 직접 받음 |
| `load_from_yaml(file_path, merge)` | **구현됨** | |
| `load_from_json(file_path, merge)` | **구현됨** | |
| `load_from_excel(file_path, merge)` | **미구현** | 계획: "향후 구현" |
| `load_from_markdown(file_path, merge)` | **미구현** | 계획: "향후 구현" |
| `load_auto(file_path, merge)` | **구현됨** | .yaml/.yml/.json만 지원 (.xlsx/.md 미지원) |
| `check_and_reload()` | **구현됨** | mtime 기반 변경 감지 |
| `export_to_yaml(output_path)` | **구현됨** | |
| `export_to_json(output_path)` | **구현됨** | |
| `get_last_loaded_at()` | **구현됨** | |
| `get_loaded_stats()` | **구현됨** | |

#### 10.5.2 SynonymLoadResult 데이터클래스

| 계획 필드 | 구현 상태 |
|----------|----------|
| status, file_path, columns_loaded, resource_types_loaded, eav_names_loaded, total_words, merge_mode, errors, message | **모두 구현됨** |

#### 10.5.3 RedisSchemaCache 확장

| 계획 항목 | 구현 상태 |
|----------|----------|
| `RESOURCE_TYPE_SYNONYMS_KEY` 상수 | **구현됨** |
| `EAV_NAME_SYNONYMS_KEY` 상수 | **구현됨** |
| `save_resource_type_synonyms()` | **구현됨** |
| `load_resource_type_synonyms()` | **구현됨** |
| `save_eav_name_synonyms()` | **구현됨** |
| `load_eav_name_synonyms()` | **구현됨** |
| 방안 B (별도 Hash) 채택 | **구현됨** | `synonyms:resource_types`, `synonyms:eav_names` |

#### 10.5.4 연동 포인트 구현 현황

| 연동 포인트 | 계획 | 구현 상태 | 영향도 |
|------------|------|----------|--------|
| **1. SchemaCacheManager** | synonym_loader가 SchemaCacheManager를 통해 Redis 접근 | **미연동** — 직접 RedisSchemaCache 사용으로 변경 | 중간: SchemaCacheManager의 파일 폴백 기능을 우회함 |
| **2. RedisSchemaCache** | resource_type/eav_name 메서드 추가 | **구현됨** | - |
| **3. query_generator.py** | LLM 프롬프트에 resource_type/eav_name 유사단어 포함 | **미연동** | 높음: 유사단어 사전이 쿼리 생성에 활용되지 않음 |
| **4. synonym_registrar.py** | 독립 동작 (동일 Redis 키 공유) | **연동 불필요** | - |
| **5. schema_cache_cli.py** | load-synonyms, export-synonyms, synonym-status 명령 추가 | **구현 완료** | CLI에 3개 명령 모두 등록됨 |

#### 10.5.5 단위 테스트

| 계획 항목 | 구현 상태 | 비고 |
|----------|----------|------|
| `tests/test_schema_cache/test_synonym_loader.py` | **구현됨** | 20개 테스트 전부 통과 (0.06s) |
| Redis 확장 메서드 테스트 | **구현됨** | 5개 테스트 (save/load resource_type, eav_name) |
| YAML 로드 테스트 | **구현됨** | 3개 테스트 (기본, merge=false, 파일 없음) |
| JSON 로드 테스트 | **구현됨** | 1개 테스트 |
| 자동 감지 테스트 | **구현됨** | 4개 테스트 (.yaml, .json, .txt, 기본 경로) |
| 변경 감지 테스트 | **구현됨** | 3개 테스트 (이력 없음, 미변경, 변경됨) |
| 내보내기 테스트 | **구현됨** | 2개 테스트 (YAML, JSON) |
| 데이터클래스 테스트 | **구현됨** | 2개 테스트 |
| 기존 테스트 회귀 | **없음** | 기존 180개 테스트 영향 없음 |

### 10.6 추가 생성된 파일 (계획 외)

| 파일 | 설명 |
|------|------|
| `testdata/_generate_sql.py` | SQL INSERT 문 재생성 스크립트 (서버 프로파일 변경 시 재실행 가능) |
| `testdata/templates/create_templates.py` | 엑셀 양식 템플릿 생성 스크립트 (openpyxl) |

### 10.7 미구현 항목 및 후속 작업 우선순위

| 우선순위 | 항목 | 사유 | 선행 조건 |
|---------|------|------|----------|
| **P1** | schema_cache_cli.py에 load-synonyms/export-synonyms 명령 추가 | SynonymLoader가 어디에서도 호출되지 않아 실질적 사용 불가 | 없음 |
| **P1** | query_generator.py에 resource_type/eav_name 유사단어 프롬프트 포함 | 유사단어 사전이 SQL 생성에 활용되지 않으면 효과 없음 | 없음 |
| **P2** | SchemaCacheManager에 SynonymLoader 연동 (초기화 시 자동 로드) | 서버 시작 시 글로벌 사전 자동 로드 | P1 완료 |
| **P2** | AVAIL_STATUS 비정상 비율 조정 (1.1% → 5~8%) | _generate_sql.py 수정 후 재생성 | 없음 |
| **P3** | load_from_excel(), load_from_markdown() 구현 | 계획에 "향후 구현"으로 명시 | P1 완료 |
| **P3** | tests/test_e2e_polestar.py E2E 테스트 코드 작성 | DB2 + Redis + DBHub 환경 확보 필요 | DB2 환경 |
| **P3** | mcp_server/.env에 DB2 연결 문자열 설정 확인 | DB2 Docker 컨테이너 실행 필요 | DB2 환경 |

### 10.8 설계 변경 사항 (계획 대비)

| 항목 | 계획 | 실제 구현 | 변경 사유 |
|------|------|----------|----------|
| SynonymLoader 생성자 인자 | `cache_manager: SchemaCacheManager` | `redis_cache: RedisSchemaCache` | SchemaCacheManager 래퍼 계층 없이 Redis에 직접 접근하여 의존성 단순화 |
| CMM_RESOURCE 행 수 | 약 700행 | 1,115행 | 프로파일별 리소스를 더 풍부하게 생성 |
| RESOURCE_TYPE | 19종 (HBA 포함 15종+) | 20종 (platform.server 추가) | 최상위 서버 리소스에 platform.server 타입 사용 |
| AVAIL_STATUS 비정상 비율 | 5~8% | 1.1% | 생성 스크립트에서 비정상 설정 로직 보수적 적용 |
