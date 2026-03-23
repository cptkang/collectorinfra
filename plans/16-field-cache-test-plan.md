# 필드 정보 캐시 생성 테스트 계획

## 1. 개요

### 1.1 목적
`sample/` 폴더의 Excel 파일 3종을 입력 데이터로 사용하여, 기존 스키마 캐시 시스템(`DescriptionGenerator`, `RedisSchemaCache`, `SchemaCacheManager`)이 **DB 접속 없이도** 필드 정보 캐시(컬럼 설명 + 유사 단어)를 올바르게 생성할 수 있는지 검증한다.

### 1.2 입력 데이터

| 파일 | 내용 | 역할 |
|------|------|------|
| `sample/Table Schema.xlsx` | `CMM_RESOURCE`, `CORE_CONFIG_PROP` 두 테이블의 CREATE TABLE DDL (DB2 구문) | **스키마 정의** — 컬럼명, 데이터 타입, NOT NULL, PK 등 구조 정보 |
| `sample/CMM_RESOURCE(873.xlsx` | CMM_RESOURCE 테이블 124행 실제 데이터 (59컬럼) | **샘플 데이터** — LLM이 컬럼 의미를 추론하는 근거 |
| `sample/CORE_CONFIG_PROP(110.xlsx` | CORE_CONFIG_PROP 테이블 24행 실제 데이터 (12컬럼) | **샘플 데이터** — 키-값 구조의 설정 정보 |

### 1.3 데이터 특성 분석

#### CMM_RESOURCE (59컬럼, 124행)
- **도메인**: 인프라 리소스 관리 (서버, CPU, 메모리, 디스크, 파일시스템, 네트워크 등)
- **핵심 컬럼**: `DTYPE`, `HOSTNAME`, `IPADDRESS`, `NAME`, `RESOURCE_TYPE`, `RESOURCE_KEY`, `DESCRIPTION`, `AVAIL_STATUS`
- **계층 구조**: `PARENT_RESOURCE_ID`, `PLATFORM_RESOURCE_ID`, `ID_ANCESTRY`로 리소스 간 부모-자식 관계 표현
- **RESOURCE_TYPE 분포**: `server.FileSystem`(47), `server.Cpu`(26), `server.LogMonitor`(9), `server.ProcessMonitor`(7), `server.NetworkInterface`(6) 등
- **호스트**: hostname1, hotname2 (2대 서버)
- **한국어 데이터 포함**: NAME 컬럼에 "가상메모리", "디스크", "파일시스템" 등 한국어 값 존재

#### CORE_CONFIG_PROP (12컬럼, 24행)
- **도메인**: 에이전트 설정 정보 (키-값 쌍)
- **핵심 컬럼**: `NAME` (설정 키), `STRINGVALUE_SHORT` (설정 값), `CONFIGURATION_ID` (소속 설정 그룹)
- **설정 항목**: AgentID, AgentVersion, Hostname, IPaddress, OSType, OSVersion, Model, Vendor 등
- **EAV 패턴**: Entity-Attribute-Value 구조로, NAME이 속성명, STRINGVALUE_SHORT가 속성값
- **CONFIGURATION_ID 기준 그룹**: 110 (hostname1), 176 (hostname2)

---

## 2. 테스트 전략

### 2.1 테스트 레벨

```
Level 1: Excel 파싱 → 스키마 딕셔너리 변환 (단위)
Level 2: DescriptionGenerator에 스키마 딕셔너리 투입 → 설명/유사단어 생성 (통합)
Level 3: Redis 캐시 저장/로드 라운드트립 (통합)
Level 4: 생성된 캐시로 실제 자연어 질의 → SQL 변환 정확도 검증 (E2E)
```

### 2.2 테스트 흐름

```
[Phase A] 데이터 준비
  Table Schema.xlsx → DDL 파싱 → 컬럼 메타데이터 추출
  CMM_RESOURCE(873.xlsx → 행 데이터 로드 → 샘플 데이터
  CORE_CONFIG_PROP(110.xlsx → 행 데이터 로드 → 샘플 데이터
           ↓
[Phase B] 스키마 딕셔너리 구성
  컬럼 메타데이터 + 샘플 데이터 → schema_dict 형식 조립
           ↓
[Phase C] LLM 기반 설명 생성
  DescriptionGenerator.generate_for_db(schema_dict)
           ↓
[Phase D] 캐시 저장 및 검증
  RedisSchemaCache에 저장 → 로드 → 라운드트립 검증
           ↓
[Phase E] 활용 검증
  생성된 캐시 정보로 자연어 → SQL 변환 정확도 비교
```

---

## 3. 상세 테스트 항목

### Phase A: Excel → 스키마 메타데이터 파싱

#### A-1. DDL 파싱 (Table Schema.xlsx)

**목적**: CREATE TABLE DDL 텍스트에서 컬럼 메타데이터를 정확히 추출한다.

**입력**: Sheet1의 셀 텍스트 (DB2 CREATE TABLE 구문)

**구현 필요 항목** — DDL 파서 유틸리티:
```python
def parse_ddl(ddl_text: str) -> dict:
    """DDL 텍스트를 파싱하여 테이블별 컬럼 정보를 반환한다.

    Returns:
        {
            "CMM_RESOURCE": {
                "columns": [
                    {"name": "DTYPE", "type": "VARCHAR(31)", "nullable": False, "primary_key": False},
                    {"name": "ID", "type": "BIGINT", "nullable": False, "primary_key": True},
                    ...
                ]
            },
            "CORE_CONFIG_PROP": { ... }
        }
    """
```

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| A-1-1 | CMM_RESOURCE 컬럼 수 | 59개 |
| A-1-2 | CORE_CONFIG_PROP 컬럼 수 | 12개 |
| A-1-3 | NOT NULL 컬럼 식별 | DTYPE, ID, NAME, RESOURCE_KEY, RESOURCE_TYPE 등 |
| A-1-4 | 데이터 타입 매핑 | VARCHAR(31 OCTETS) → VARCHAR(31), BIGINT → BIGINT, CLOB → CLOB 등 |
| A-1-5 | IDENTITY(자동증가) 컬럼 감지 | CMM_RESOURCE.ID, CORE_CONFIG_PROP.ID |
| A-1-6 | 스키마명 추출 | "POLESTAR" |

#### A-2. 샘플 데이터 로드 (CMM_RESOURCE, CORE_CONFIG_PROP)

**목적**: Excel 데이터 파일에서 헤더 + 행 데이터를 정확히 로드한다.

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| A-2-1 | CMM_RESOURCE 헤더 수 | 59개 (DDL 컬럼 수와 일치) |
| A-2-2 | CMM_RESOURCE 데이터 행 수 | 124행 |
| A-2-3 | CORE_CONFIG_PROP 헤더 수 | 12개 |
| A-2-4 | CORE_CONFIG_PROP 데이터 행 수 | 24행 |
| A-2-5 | 한국어 데이터 보존 | NAME에 "파일시스템", "가상메모리" 등 |
| A-2-6 | NULL 값 처리 | 빈 셀 → None |

### Phase B: 스키마 딕셔너리 조립

**목적**: DDL 메타데이터 + 샘플 데이터를 기존 `schema_dict` 형식에 맞게 조립한다.

**기존 `schema_dict` 형식** (Redis 캐시 저장 형식과 동일):
```python
schema_dict = {
    "tables": {
        "CMM_RESOURCE": {
            "name": "CMM_RESOURCE",
            "schema_name": "POLESTAR",
            "comment": "",
            "columns": [
                {
                    "name": "DTYPE",
                    "type": "VARCHAR(31)",
                    "nullable": False,
                    "primary_key": False,
                    "foreign_key": False,
                    "references": None,
                    "comment": ""
                },
                ...
            ],
            "sample_data": [
                {"DTYPE": "ServiceResource", "ID": 874, "HOSTNAME": "hostname1", ...},
                ...  # 최대 5행
            ]
        },
        "CORE_CONFIG_PROP": { ... }
    },
    "relationships": []  # DDL에서 FK 정보 추출 가능 시 포함
}
```

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| B-1 | schema_dict 구조 유효성 | `tables` 키 존재, 2개 테이블 포함 |
| B-2 | 컬럼 정보 완전성 | name, type, nullable 필드 모두 존재 |
| B-3 | 샘플 데이터 제한 | 각 테이블 최대 5행 |
| B-4 | 샘플 데이터 키-컬럼 일치 | 샘플 데이터의 키가 columns[].name과 일치 |

### Phase C: LLM 기반 설명 + 유사 단어 생성

**목적**: `DescriptionGenerator`에 조립된 `schema_dict`를 투입하여 컬럼 설명과 유사 단어가 올바르게 생성되는지 검증한다.

#### C-1. 테이블 단위 생성 (generate_for_table)

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| C-1-1 | CMM_RESOURCE 결과 키 형식 | `CMM_RESOURCE.DTYPE`, `CMM_RESOURCE.HOSTNAME` 등 |
| C-1-2 | 각 컬럼에 description 존재 | 빈 문자열이 아닌 한국어 설명 |
| C-1-3 | 각 컬럼에 synonyms 존재 | 3~8개의 유사 단어 리스트 |
| C-1-4 | HOSTNAME 유사 단어 품질 | "호스트명", "서버명", "host name" 등 포함 예상 |
| C-1-5 | IPADDRESS 유사 단어 품질 | "IP주소", "아이피", "IP 주소" 등 포함 예상 |
| C-1-6 | RESOURCE_TYPE 유사 단어 품질 | "리소스 유형", "자원 종류", "resource type" 등 포함 예상 |

#### C-2. DB 전체 생성 (generate_for_db)

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| C-2-1 | descriptions 총 수 | 71개 (59 + 12) 근처 |
| C-2-2 | synonyms 총 수 | descriptions와 동일 수준 |
| C-2-3 | 두 테이블 모두 커버 | CMM_RESOURCE.* 와 CORE_CONFIG_PROP.* 모두 존재 |

#### C-3. DB 설명 생성 (generate_db_description)

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| C-3-1 | DB 설명 생성 여부 | None이 아닌 문자열 반환 |
| C-3-2 | 설명 길이 | 30~80자 이내 |
| C-3-3 | 도메인 키워드 포함 | "인프라", "서버", "리소스", "모니터링" 중 하나 이상 포함 |

#### C-4. EAV 패턴 인식 (CORE_CONFIG_PROP 특수 검증)

CORE_CONFIG_PROP은 EAV(Entity-Attribute-Value) 패턴이므로, LLM이 이 구조를 인식하여 설명을 생성하는지 확인한다.

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| C-4-1 | NAME 컬럼 설명 | "설정 항목의 이름/키" 등 EAV의 Attribute 역할 인식 |
| C-4-2 | STRINGVALUE_SHORT 컬럼 설명 | "설정 값" 등 EAV의 Value 역할 인식 |
| C-4-3 | CONFIGURATION_ID 컬럼 설명 | "설정 그룹/엔티티 식별자" 등 EAV의 Entity 역할 인식 |

### Phase D: 캐시 저장/로드 검증

#### D-1. Redis 라운드트립 (Redis 가용 시)

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| D-1-1 | save_schema → load_schema 일관성 | 저장 전후 tables, relationships 동일 |
| D-1-2 | save_descriptions → load_descriptions 일관성 | 저장 전후 딕셔너리 동일 |
| D-1-3 | save_synonyms → load_synonyms 일관성 | 저장 전후 딕셔너리 동일 |
| D-1-4 | fingerprint 저장/조회 | 저장한 fingerprint와 get_fingerprint 결과 일치 |
| D-1-5 | meta 정보 정확성 | table_count=2, total_column_count=71 |

#### D-2. 파일 캐시 폴백

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| D-2-1 | Redis 미연결 시 파일 캐시 저장 | `.cache/schema/` 디렉토리에 JSON 생성 |
| D-2-2 | 파일 캐시 로드 | 저장된 JSON에서 schema_dict 복원 |

#### D-3. 글로벌 유사 단어 사전

**검증 항목**:
| # | 검증 내용 | 기대값 |
|---|----------|--------|
| D-3-1 | sync_global_synonyms 실행 | DB별 synonyms → 글로벌 사전에 병합 |
| D-3-2 | 공통 컬럼명 글로벌 등록 | DTYPE, ID, NAME 등 양 테이블 공통 컬럼 → 글로벌 사전에 1회만 등록 |
| D-3-3 | load_synonyms_with_global_fallback | 글로벌 폴백 정상 동작 |

### Phase E: 활용 검증 (E2E)

**목적**: 생성된 캐시 정보가 실제 자연어 → SQL 변환의 정확도를 향상시키는지 검증한다.

#### E-1. 자연어 질의 → 컬럼 매핑 정확도

캐시 **유무**에 따른 SQL 생성 비교:

| # | 자연어 질의 | 기대 컬럼 | 캐시 없이 | 캐시 있을 때 |
|---|-----------|----------|----------|------------|
| E-1-1 | "서버명 목록을 보여줘" | CMM_RESOURCE.HOSTNAME 또는 NAME | △ | ○ |
| E-1-2 | "호스트 IP를 알려줘" | CMM_RESOURCE.IPADDRESS | △ | ○ |
| E-1-3 | "리소스 종류별 개수" | CMM_RESOURCE.RESOURCE_TYPE | △ | ○ |
| E-1-4 | "에이전트 버전 확인" | CORE_CONFIG_PROP (NAME='AgentVersion') | × | ○ |
| E-1-5 | "OS 종류별 서버 수" | CORE_CONFIG_PROP (NAME='OSType') | × | ○ |
| E-1-6 | "CPU 리소스 상태" | CMM_RESOURCE (RESOURCE_TYPE='server.Cpu') | △ | ○ |

> △: 가능하나 불확실, ×: 캐시 없이는 EAV 구조 해석 어려움, ○: 캐시의 설명/유사단어로 정확한 매핑 기대

---

## 4. 구현 계획

### 4.1 신규 구현 필요 항목

| # | 항목 | 위치 | 설명 |
|---|------|------|------|
| 1 | DDL 파서 유틸리티 | `src/utils/ddl_parser.py` | CREATE TABLE DDL → 컬럼 메타데이터 추출 |
| 2 | Excel → schema_dict 변환기 | `src/utils/excel_schema_loader.py` | DDL + 샘플 데이터 Excel → schema_dict 조립 |
| 3 | 테스트 스크립트 | `tests/test_field_cache_from_excel.py` | 전체 Phase A~E 테스트 |

### 4.2 DDL 파서 구현 방향

```python
# src/utils/ddl_parser.py

import re
from typing import Any

def parse_db2_ddl(ddl_text: str) -> dict[str, dict[str, Any]]:
    """DB2 CREATE TABLE DDL을 파싱한다.

    처리 항목:
    - 테이블명 추출 (schema.table 형식)
    - 컬럼명, 데이터 타입 추출
    - NOT NULL 제약 조건
    - GENERATED BY DEFAULT AS IDENTITY (자동증가 PK)
    - VARCHAR(N OCTETS) → VARCHAR(N) 정규화
    - CLOB 타입 처리
    """
```

**파싱 주의 사항**:
- DB2 구문의 `OCTETS` 키워드 제거 필요 (예: `VARCHAR(31 OCTETS)` → `VARCHAR(31)`)
- `GENERATED BY DEFAULT AS IDENTITY` → PK 식별
- `CLOB(1073741824 OCTETS) LOGGED NOT COMPACT` → `CLOB` 으로 단순화
- `SMALLINT`, `INTEGER`, `BIGINT` 등 숫자 타입 그대로 유지
- 여러 CREATE TABLE 문이 하나의 텍스트에 연속으로 존재

### 4.3 Excel → schema_dict 변환기 구현 방향

```python
# src/utils/excel_schema_loader.py

import openpyxl

def load_schema_from_excel(
    ddl_path: str,           # Table Schema.xlsx
    data_files: dict[str, str],  # {"CMM_RESOURCE": "CMM_RESOURCE(873.xlsx", ...}
    sample_rows: int = 5,
) -> dict:
    """Excel 파일로부터 schema_dict를 생성한다.

    1. DDL 파일 파싱 → 컬럼 메타데이터
    2. 데이터 파일 로드 → 샘플 데이터 (최대 sample_rows행)
    3. schema_dict 형식으로 조립
    """
```

### 4.4 테스트 실행 방법

```bash
# Phase A~B: 파싱 및 변환 테스트 (LLM 불필요)
python -m pytest tests/test_field_cache_from_excel.py -k "test_phase_a or test_phase_b" -v

# Phase C: LLM 설명 생성 테스트 (LLM 필요, 느림)
python -m pytest tests/test_field_cache_from_excel.py -k "test_phase_c" -v --timeout=120

# Phase D: 캐시 저장/로드 테스트 (Redis 필요)
python -m pytest tests/test_field_cache_from_excel.py -k "test_phase_d" -v

# 전체 실행
python -m pytest tests/test_field_cache_from_excel.py -v --timeout=300
```

---

## 5. 테스트 환경 요구사항

| 항목 | 필수/선택 | 설명 |
|------|----------|------|
| Python 3.11+ | 필수 | |
| openpyxl | 필수 | Excel 파일 읽기 |
| LLM (Claude/GPT) | Phase C 필수 | 설명 생성용. 환경변수에 API 키 필요 |
| Redis | Phase D 선택 | 없으면 파일 캐시로 폴백 테스트 |
| pytest | 필수 | 테스트 러너 |

---

## 6. 성공 기준

| 기준 | 설명 |
|------|------|
| **파싱 정확도 100%** | DDL에서 추출한 컬럼 수가 실제 Excel 헤더 수와 일치 |
| **LLM 설명 커버율 ≥ 90%** | 전체 71개 컬럼 중 64개 이상에 유효한 설명 생성 |
| **유사 단어 품질** | 핵심 컬럼 6개(HOSTNAME, IPADDRESS, NAME, RESOURCE_TYPE, AVAIL_STATUS, STRINGVALUE_SHORT)의 유사 단어에 한국어 표현 최소 2개 포함 |
| **캐시 라운드트립 무손실** | 저장 → 로드 후 데이터 일치 |
| **E2E 질의 정확도 ≥ 4/6** | Phase E의 6개 질의 중 4개 이상에서 올바른 컬럼 매핑 |

---

## 7. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 |
|--------|------|-----------|
| DB2 DDL 구문 변형 | 파싱 실패 | 정규식을 넓게 잡고, 실패 시 에러 메시지에 미파싱 행 표시 |
| LLM 응답 JSON 파싱 실패 | 설명 미생성 | `extract_json_from_response` 기존 유틸 활용, 재시도 1회 |
| CORE_CONFIG_PROP의 EAV 구조 | LLM이 키-값 패턴 미인식 | 샘플 데이터에 다양한 NAME 값 포함하여 LLM 힌트 제공 |
| 59컬럼 테이블의 LLM 토큰 초과 | 일부 컬럼 누락 | 테이블당 30컬럼 이하로 분할 배치 처리 |
| Redis 미설치 환경 | Phase D 불가 | 파일 캐시 폴백 테스트로 대체 |

---

## 8. 일정 (예상)

| Phase | 작업 | 예상 소요 |
|-------|------|----------|
| A | DDL 파서 구현 + 단위 테스트 | 0.5일 |
| B | Excel → schema_dict 변환기 + 테스트 | 0.5일 |
| C | LLM 설명 생성 테스트 + 품질 검증 | 0.5일 |
| D | 캐시 저장/로드 라운드트립 테스트 | 0.5일 |
| E | E2E 질의 정확도 검증 | 0.5일 |
| — | 결과 정리 및 보고서 작성 | 0.5일 |
| **합계** | | **3일** |
