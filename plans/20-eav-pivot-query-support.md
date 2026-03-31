# Plan 20: EAV 비정규화 테이블 쿼리 지원

> 작성일: 2026-03-24
> 상태: **구현 완료** (2026-03-24)

---

## 1. 배경 및 문제 정의

### 1.1 현재 상황

현재 에이전트는 일반적인 정규화 테이블 구조(서버 테이블에 hostname, ip_address, os_type 등 컬럼이 존재)를 전제로 SQL을 생성한다. 그러나 Polestar DB의 실제 구조는 이와 전혀 다르다.

### 1.2 Polestar DB 실제 구조

**CMM_RESOURCE** (리소스 계층 테이블):
- 서버, CPU, 메모리, 디스크, 파일시스템, 네트워크 등 모든 인프라 리소스를 하나의 테이블에 저장
- `RESOURCE_TYPE` 컬럼으로 리소스 종류를 구분 (예: `server.Cpu`, `server.FileSystem`)
- `PARENT_RESOURCE_ID`를 통한 계층 구조 (Server → CPU/Memory/Disk/Network → 개별 Core/FileSystem/Interface)
- 서버명은 `HOSTNAME` 컬럼이나, CPU/디스크/네트워크 등은 `NAME` 컬럼에 이름이 들어감

**CORE_CONFIG_PROP** (EAV 설정 테이블):
- Entity-Attribute-Value 패턴으로 서버 설정 정보를 저장
- `CONFIGURATION_ID` = Entity (서버에 매핑)
- `NAME` = Attribute (AgentID, Hostname, IPaddress, OSType, Model, Vendor 등)
- `STRINGVALUE_SHORT` / `STRINGVALUE` = Value (IS_LOB 플래그로 어느 컬럼을 사용할지 결정)

### 1.3 핵심 문제

| 문제 | 상세 |
|------|------|
| **단순 SELECT 불가** | "서버별 IP주소를 조회해줘" → 일반 테이블이면 `SELECT hostname, ip_address FROM servers`지만, Polestar에서는 EAV 피벗 쿼리가 필요 |
| **계층 탐색 필요** | "hostname1 서버의 파일시스템 목록" → `CMM_RESOURCE`에서 해당 서버 ID를 찾고, 그 하위에서 `RESOURCE_TYPE='server.FileSystem'`인 행을 조회해야 함 |
| **2테이블 조인 필요** | 서버 기본정보(CMM_RESOURCE)와 설정정보(CORE_CONFIG_PROP)를 조합해야 완전한 서버 정보가 됨 |
| **LLM이 구조를 모름** | 현재 프롬프트는 스키마 컬럼 목록만 제공하므로 LLM이 EAV 피벗 패턴을 이해하지 못함 |

### 1.4 필드 매핑(Field Mapper) + Redis 유사어의 EAV 지원 현황 (코드 검토 결과)

> 검토일: 2026-03-24. Redis 유사어 저장/로드 구조와 field_mapper의 EAV 비정규화 대응을 분석.
>
> **개선 방향**: Redis에 EAV 구조를 명시적으로 등록하여 LLM이 EAV 피벗 패턴을 이해하도록 지원한다.

#### Redis 3계층 유사어 — 저장/로드는 정상, field_mapper 연결 미흡

| 계층 | Redis 키 | 저장 | 로드 | query_generator 사용 | field_mapper 사용 |
|------|---------|------|------|---------------------|-------------------|
| 컬럼 유사어 | `synonyms:{db_id}`, `synonyms:global` | ✅ | ✅ | ✅ | ✅ |
| RESOURCE_TYPE 유사어 | `synonyms:resource_types` | ✅ | ✅ | ✅ | ❌ 미전달 |
| EAV NAME 유사어 | `synonyms:eav_names` | ✅ | ✅ | ✅ | ❌ 미전달 |

- `schema_analyzer`는 `resource_type_synonyms`, `eav_name_synonyms`를 State에 저장하지만, `field_mapper` 노드는 이를 사용하지 않음
- `perform_3step_mapping()`의 `all_db_synonyms` 파라미터는 `{table.column: [words]}` 형식만 수용

#### 핵심 갭: EAV 속성을 field_mapper가 표현할 수 없음

**시나리오**: 양식에 "OS종류" 필드 → `CORE_CONFIG_PROP.NAME = 'OSType'`으로 매핑되어야 함

```
현재 흐름:
  1단계 (힌트): 없음 → SKIP
  2단계 (synonym): synonyms = {table.column: [words]} 형식만 → EAV 속성 불일치 → SKIP
  3단계 (LLM): descriptions에 EAV 설명 없음 → 매핑 불확실 → 실패 가능
```

- `_synonym_match()`: `{table.column: [words]}` 구조만 처리. EAV 속성은 컬럼이 아니라 행의 NAME 값이므로 표현 불가
- `_format_schema_columns()`: 정규 테이블 컬럼만 포맷. `_polestar_meta` 활용 없음
- `_validate_mapping()`: `table.column` 존재 여부만 확인. EAV 속성 검증 로직 없음
- field_mapper 프롬프트(`src/prompts/field_mapper.py`): Polestar EAV 구조 설명/예시 없음

#### 개선사항 목록 → **Plan 21로 분리 완료** (`plans/21-eav-field-mapper-support.md`)

**방침**: Redis에 EAV 구조(known_attributes, resource_types)를 명시적으로 등록하고, field_mapper와 LLM 프롬프트가 이를 활용하여 EAV 피벗 매핑을 수행하도록 개선한다.

| 우선순위 | 개선 항목 | 대상 파일 | 설명 |
|---------|----------|----------|------|
| **P0** | Redis에 EAV 구조 명시적 등록 | `config/global_synonyms.yaml`, `src/schema_cache/redis_cache.py` | EAV known_attributes를 Redis에 `eav:schema:{db_id}` 키로 저장하여 LLM과 field_mapper가 EAV 구조를 인식하도록 함 |
| **P0** | field_mapper에 eav_name_synonyms 전달 | `src/nodes/field_mapper.py` | `perform_3step_mapping()` 호출 시 eav_name_synonyms를 추가 파라미터로 전달 |
| **P0** | `_synonym_match()` EAV 확장 | `src/document/field_mapper.py` | eav_name_synonyms에서 필드명을 매칭하여 `EAV:속성명` 형식으로 반환하는 분기 추가 |
| **P0** | field_mapper 프롬프트에 EAV 가이드 | `src/prompts/field_mapper.py` | Polestar EAV 구조 설명과 매핑 예시 추가 (query_generator와 동일 수준) |
| **P1** | `_format_schema_columns()` Polestar 보강 | `src/document/field_mapper.py` | `_polestar_meta` 감지 시 EAV known_attributes를 가상 컬럼으로 스키마에 포함 |
| **P1** | `load_synonyms_with_global_fallback()` EAV 폴백 | `src/schema_cache/cache_manager.py` | EAV 속성에 대한 글로벌 유사어 폴백 메커니즘 추가 |
| **P1** | description_generator EAV 확장 | `src/schema_cache/description_generator.py` | EAV 속성별 설명 생성 (예: `OSType: "서버의 운영체제 종류"`) |
| **P2** | EAV 매핑 결과 표현 | `src/document/field_mapper.py` | 매핑 결과에 `is_eav` 플래그 포함하여 query_generator가 피벗 쿼리 생성 시 활용 |
| **P2** | `_validate_mapping()` EAV 검증 | `src/document/field_mapper.py` | EAV 속성명이 known_attributes에 포함되는지 검증 |

---

## 2. 목표

사용자가 한국어 자연어로 질의하면, EAV/계층 구조를 고려한 올바른 SQL을 자동 생성하여 결과를 반환한다.

### 예시 질의 → 기대 SQL

**질의 1**: "전체 서버 목록과 IP 주소를 조회해줘"
```sql
-- 서버 목록 + IP 주소 (EAV 피벗)
SELECT
    r.ID,
    r.HOSTNAME,
    r.IPADDRESS,
    MAX(CASE WHEN p.NAME = 'OSType' THEN p.STRINGVALUE_SHORT END) AS OS_TYPE,
    MAX(CASE WHEN p.NAME = 'Model' THEN p.STRINGVALUE_SHORT END) AS MODEL,
    MAX(CASE WHEN p.NAME = 'Vendor' THEN p.STRINGVALUE_SHORT END) AS VENDOR
FROM CMM_RESOURCE r
LEFT JOIN CORE_CONFIG_PROP p ON p.CONFIGURATION_ID = r.RESOURCE_CONF_ID
WHERE r.RESOURCE_TYPE = 'server.Cpus'  -- 서버 최상위는 PARENT가 NULL인 행 또는 특정 조건
  AND r.DTYPE = 'Resource'
GROUP BY r.ID, r.HOSTNAME, r.IPADDRESS
LIMIT 100;
```

**질의 2**: "hostname1 서버의 파일시스템 목록"
```sql
-- hostname1의 파일시스템 목록 (계층 탐색)
SELECT child.ID, child.NAME, child.DESCRIPTION, child.AVAIL_STATUS
FROM CMM_RESOURCE child
JOIN CMM_RESOURCE parent ON child.PARENT_RESOURCE_ID = parent.ID
WHERE parent.HOSTNAME = 'hostname1'
  AND child.RESOURCE_TYPE = 'server.FileSystem'
LIMIT 100;
```

**질의 3**: "CPU 코어 수가 가장 많은 서버 Top 5"
```sql
-- CPU 코어 수 기준 Top 5 서버 (계층 집계)
SELECT
    server.HOSTNAME,
    COUNT(cpu.ID) AS CPU_CORE_COUNT
FROM CMM_RESOURCE server
JOIN CMM_RESOURCE cpus ON cpus.PARENT_RESOURCE_ID = server.ID
    AND cpus.RESOURCE_TYPE = 'server.Cpus'
JOIN CMM_RESOURCE cpu ON cpu.PARENT_RESOURCE_ID = cpus.ID
    AND cpu.RESOURCE_TYPE = 'server.Cpu'
WHERE server.RESOURCE_TYPE IS NOT NULL
  AND server.HOSTNAME IS NOT NULL
GROUP BY server.HOSTNAME
ORDER BY CPU_CORE_COUNT DESC
LIMIT 5;
```

---

## 3. 수정 계획

### Phase A: 메타데이터 보강 (프롬프트 계층)

#### A-1. EAV 구조 설명 프롬프트 추가

**파일**: `src/prompts/query_generator.py`

`QUERY_GENERATOR_SYSTEM_TEMPLATE`에 Polestar DB 전용 EAV/계층 쿼리 패턴 가이드를 추가한다.

```
## Polestar DB 특수 구조 가이드

### CMM_RESOURCE: 계층형 리소스 테이블
- 모든 인프라 리소스(서버, CPU, 메모리, 디스크, 파일시스템, 네트워크)가 하나의 테이블에 저장됨
- RESOURCE_TYPE으로 리소스 종류를 구분
- PARENT_RESOURCE_ID로 부모-자식 관계를 표현 (self-join 필요)
- 서버 최상위 행: HOSTNAME IS NOT NULL AND PARENT_RESOURCE_ID가 그룹 리소스를 가리킴

### CORE_CONFIG_PROP: EAV(Entity-Attribute-Value) 테이블
- 서버 설정 정보가 행 단위로 저장됨 (컬럼이 아닌 행으로 속성 구분)
- CONFIGURATION_ID → CMM_RESOURCE.RESOURCE_CONF_ID에 매핑
- NAME 컬럼 = 속성명 (AgentID, Hostname, IPaddress, OSType, Model, Vendor 등)
- STRINGVALUE_SHORT 컬럼 = 속성값 (IS_LOB=0일 때), STRINGVALUE = LOB값 (IS_LOB=1일 때)

### EAV 피벗 쿼리 패턴
여러 속성을 컬럼으로 변환할 때 CASE WHEN + GROUP BY를 사용:
  MAX(CASE WHEN p.NAME = '속성명' THEN p.STRINGVALUE_SHORT END) AS alias

### 계층 탐색 패턴
서버의 하위 리소스를 조회할 때 self-join 사용:
  FROM CMM_RESOURCE child
  JOIN CMM_RESOURCE parent ON child.PARENT_RESOURCE_ID = parent.ID

### RESOURCE_TYPE 값 참조
{resource_type_reference}

### EAV NAME 값 참조
{eav_name_reference}
```

**변경 방식**: 동적으로 Polestar DB가 대상일 때만 이 섹션을 삽입한다.

#### A-2. 도메인별 쿼리 패턴 템플릿 정의

**신규 파일**: `src/prompts/polestar_patterns.py`

Polestar DB 전용 쿼리 패턴 사전을 정의한다:

```python
POLESTAR_QUERY_PATTERNS = {
    "서버목록": {
        "description": "전체 서버 목록 조회 (기본 정보)",
        "pattern": """
SELECT r.ID, r.HOSTNAME, r.IPADDRESS, r.AVAIL_STATUS, r.DESCRIPTION
FROM CMM_RESOURCE r
WHERE r.HOSTNAME IS NOT NULL
  AND r.DTIME IS NULL
""",
    },
    "서버상세_EAV": {
        "description": "서버 상세정보 (EAV 피벗 포함)",
        "pattern": """
SELECT
    r.ID, r.HOSTNAME, r.IPADDRESS,
    MAX(CASE WHEN p.NAME = 'OSType' THEN p.STRINGVALUE_SHORT END) AS OS_TYPE,
    MAX(CASE WHEN p.NAME = 'OSVerson' THEN p.STRINGVALUE_SHORT END) AS OS_VERSION,
    MAX(CASE WHEN p.NAME = 'Model' THEN p.STRINGVALUE_SHORT END) AS MODEL,
    MAX(CASE WHEN p.NAME = 'Vendor' THEN p.STRINGVALUE_SHORT END) AS VENDOR,
    MAX(CASE WHEN p.NAME = 'SerialNumber' THEN p.STRINGVALUE_SHORT END) AS SERIAL_NUMBER,
    MAX(CASE WHEN p.NAME = 'IPaddress' THEN p.STRINGVALUE_SHORT END) AS CONFIG_IP
FROM CMM_RESOURCE r
LEFT JOIN CORE_CONFIG_PROP p ON p.CONFIGURATION_ID = r.RESOURCE_CONF_ID
WHERE r.HOSTNAME IS NOT NULL AND r.DTIME IS NULL
GROUP BY r.ID, r.HOSTNAME, r.IPADDRESS
""",
    },
    "하위리소스": {
        "description": "특정 서버의 하위 리소스 조회",
        "pattern": """
SELECT child.ID, child.NAME, child.RESOURCE_TYPE, child.AVAIL_STATUS, child.DESCRIPTION
FROM CMM_RESOURCE child
JOIN CMM_RESOURCE parent ON child.PARENT_RESOURCE_ID = parent.ID
WHERE parent.HOSTNAME = :hostname
  AND child.RESOURCE_TYPE = :resource_type
""",
    },
    "CPU코어수": {
        "description": "서버별 CPU 코어 수 집계",
        "pattern": """
SELECT server.HOSTNAME, COUNT(cpu.ID) AS CPU_CORE_COUNT
FROM CMM_RESOURCE server
JOIN CMM_RESOURCE cpus ON cpus.PARENT_RESOURCE_ID = server.ID
    AND cpus.RESOURCE_TYPE = 'server.Cpus'
JOIN CMM_RESOURCE cpu ON cpu.PARENT_RESOURCE_ID = cpus.ID
    AND cpu.RESOURCE_TYPE = 'server.Cpu'
WHERE server.HOSTNAME IS NOT NULL
GROUP BY server.HOSTNAME
""",
    },
    "파일시스템": {
        "description": "서버별 파일시스템 목록",
        "pattern": """
SELECT server.HOSTNAME, fs.NAME AS MOUNT_POINT, fs.AVAIL_STATUS
FROM CMM_RESOURCE fs
JOIN CMM_RESOURCE fsc ON fs.PARENT_RESOURCE_ID = fsc.ID
    AND fsc.RESOURCE_TYPE = 'server.FileSystems'
JOIN CMM_RESOURCE server ON fsc.PARENT_RESOURCE_ID = server.ID
WHERE server.HOSTNAME IS NOT NULL
""",
    },
    "네트워크인터페이스": {
        "description": "서버별 네트워크 인터페이스 목록",
        "pattern": """
SELECT server.HOSTNAME, ni.NAME AS INTERFACE_NAME, ni.AVAIL_STATUS
FROM CMM_RESOURCE ni
JOIN CMM_RESOURCE nic ON ni.PARENT_RESOURCE_ID = nic.ID
    AND nic.RESOURCE_TYPE = 'server.NetworkInterfaces'
JOIN CMM_RESOURCE server ON nic.PARENT_RESOURCE_ID = server.ID
WHERE server.HOSTNAME IS NOT NULL
""",
    },
}
```

#### A-3. input_parser 프롬프트 보강

**파일**: `src/prompts/input_parser.py`

`INPUT_PARSER_SYSTEM_PROMPT`의 query_targets에 Polestar 전용 세부 도메인을 추가:

```
- **query_targets** 가능한 값:
  - 기존: "서버", "CPU", "메모리", "디스크", "네트워크"
  - 추가: "파일시스템", "프로세스", "HBA", "에이전트", "서버설정"
```

`filter_conditions`에서 EAV 속성도 필터 가능하도록 가이드:

```
- Polestar DB의 경우, 필터 대상이 EAV 속성일 수 있습니다.
  예: "OS가 LINUX인 서버" → filter_conditions: [{"field": "OSType", "op": "=", "value": "LINUX", "is_eav": true}]
```

---

### Phase B: 스키마 분석 보강

#### B-1. Polestar 전용 스키마 보강 로직

**파일**: `src/nodes/schema_analyzer.py`

스키마 분석 시 CMM_RESOURCE / CORE_CONFIG_PROP 테이블이 감지되면, 자동으로 다음 메타데이터를 `schema_info`에 추가한다:

```python
# schema_info에 추가할 Polestar 전용 메타데이터
schema_info["_polestar_meta"] = {
    "is_eav_structure": True,
    "resource_table": "CMM_RESOURCE",
    "config_table": "CORE_CONFIG_PROP",
    "join_condition": "CORE_CONFIG_PROP.CONFIGURATION_ID = CMM_RESOURCE.RESOURCE_CONF_ID",
    "hierarchy": {
        "id_column": "ID",
        "parent_column": "PARENT_RESOURCE_ID",
        "type_column": "RESOURCE_TYPE",
        "name_column": "NAME",
        "hostname_column": "HOSTNAME",
    },
    "eav": {
        "entity_column": "CONFIGURATION_ID",
        "attribute_column": "NAME",
        "value_column": "STRINGVALUE_SHORT",
        "lob_value_column": "STRINGVALUE",
        "lob_flag_column": "IS_LOB",
        "known_attributes": [
            "AgentID", "AgentVersion", "GMT", "Hostname",
            "IPaddress", "InstallPath", "Model", "OSParameter",
            "OSType", "OSVerson", "SerialNumber", "Vendor"
        ],
    },
    "resource_types": {
        "server.Cpu": "개별 CPU 코어",
        "server.Cpus": "CPU 관리 (컨테이너)",
        "server.Memory": "물리적 메모리",
        "server.VirtualMemory": "가상 메모리",
        "server.OtherMemory": "기타 메모리",
        "server.Disks": "디스크 관리 (컨테이너)",
        "server.FileSystems": "파일시스템 관리 (컨테이너)",
        "server.FileSystem": "개별 파일시스템 마운트포인트",
        "server.NetworkInterfaces": "네트워크 인터페이스 (컨테이너)",
        "server.NetworkInterface": "개별 네트워크 인터페이스",
        "server.Netstat": "네트워크 세션/연결 정보",
        "server.Process": "프로세스 관제",
        "server.ProcessMonitor": "프로세스 모니터",
        "server.LogMonitor": "로그 모니터",
        "server.Other": "기타 정보",
        "server.Hbas": "HBA 관리 (컨테이너)",
        "server.Hba": "개별 HBA 어댑터",
        "server.HbaPort": "HBA 포트",
        "management.MonitorGroup": "모니터 그룹",
    },
}
```

**감지 기준**: `schema_info["tables"]`에 `CMM_RESOURCE`와 `CORE_CONFIG_PROP`이 모두 존재하면 Polestar 구조로 판단.

#### B-2. 샘플 데이터 강화

현재 `get_sample_data(table_name, limit=5)`로 무작위 5건을 가져오는데, EAV 테이블에서는 이것만으로 구조를 이해하기 어렵다.

Polestar 구조가 감지되면 추가 샘플을 수집:

```python
# CORE_CONFIG_PROP에서 NAME별 그룹 샘플 (EAV 패턴 이해용)
eav_sample_sql = """
SELECT NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
FROM CORE_CONFIG_PROP
WHERE IS_LOB = 0
GROUP BY NAME, STRINGVALUE_SHORT, CONFIGURATION_ID
FETCH FIRST 30 ROWS ONLY
"""

# CMM_RESOURCE에서 RESOURCE_TYPE별 분포
resource_type_sql = """
SELECT RESOURCE_TYPE, COUNT(*) AS CNT
FROM CMM_RESOURCE
WHERE DTIME IS NULL
GROUP BY RESOURCE_TYPE
ORDER BY CNT DESC
FETCH FIRST 20 ROWS ONLY
"""

# 계층 구조 샘플 (1개 서버의 하위 트리)
hierarchy_sample_sql = """
SELECT r.ID, r.NAME, r.RESOURCE_TYPE, r.PARENT_RESOURCE_ID, r.HOSTNAME
FROM CMM_RESOURCE r
WHERE r.HOSTNAME IS NOT NULL
FETCH FIRST 1 ROWS ONLY
"""
```

이 결과를 `schema_info["_polestar_meta"]["samples"]`에 저장하여 프롬프트에 포함.

---

### Phase C: 쿼리 생성 보강

#### C-1. query_generator에 Polestar 전용 프롬프트 분기

**파일**: `src/nodes/query_generator.py`

`_build_system_prompt()`에서 `schema_info`에 `_polestar_meta`가 존재하면 Polestar 전용 가이드를 삽입:

```python
def _build_system_prompt(self, ...):
    ...
    polestar_meta = schema_info.get("_polestar_meta")
    if polestar_meta:
        schema_text += _format_polestar_guide(polestar_meta)
    ...
```

`_format_polestar_guide()` 함수가 다음을 포함하는 텍스트를 생성:
1. EAV 피벗 쿼리 패턴 (CASE WHEN + GROUP BY)
2. 계층 탐색 패턴 (self-join)
3. CMM_RESOURCE ↔ CORE_CONFIG_PROP 조인 조건
4. RESOURCE_TYPE 값 참조 테이블
5. EAV NAME 값 참조 테이블
6. 예시 쿼리 2~3개

#### C-2. DB 엔진별 SQL 문법 대응

Polestar DB는 DB2를 사용할 수도 있고 PostgreSQL을 사용할 수도 있다. 따라서 `db_engine` 설정값을 확인하여 엔진에 맞는 SQL 문법을 생성해야 한다.

**DB2일 때 차이점**:
- `LIMIT N` → `FETCH FIRST N ROWS ONLY`
- `IFNULL()` → `COALESCE()`
- 문자열 비교 시 대소문자 주의 (DB2 기본 = 대소문자 구분)
- 테이블/컬럼명이 대문자 기본

**PostgreSQL일 때**:
- 표준 `LIMIT N` 사용
- 대소문자 구분 시 `"컬럼명"` 더블쿼트 필요 여부 확인

**파일**: `src/prompts/query_generator.py` 및 `src/nodes/query_validator.py`

query_validator의 LIMIT 검사 로직에 DB2의 `FETCH FIRST ... ROWS ONLY` 패턴도 인식하도록 수정:

```python
def _has_limit_clause(sql: str) -> bool:
    return bool(
        re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE)
        or re.search(r"\bFETCH\s+FIRST\s+\d+\s+ROWS?\s+ONLY\b", sql, re.IGNORECASE)
    )
```

---

### Phase D: State 및 설정 확장

#### D-1. DB 타입 정보 전달

**파일**: `src/routing/domain_config.py`

DBDomainConfig에 `db_engine` 필드를 추가:

```python
@dataclass(frozen=True)
class DBDomainConfig:
    ...
    db_engine: str = "postgresql"  # "postgresql", "mysql", "db2", etc.
```

Polestar DB 정의 (실제 환경에 따라 `db_engine` 값을 설정):
```python
DBDomainConfig(
    db_id="polestar",
    ...
    db_engine="db2",  # 또는 "postgresql" — 실제 운영 환경에 따라 결정
)
```

이 정보가 query_generator까지 전달되어, 해당 엔진 문법에 맞는 SQL을 생성하도록 유도.

#### D-2. AgentState 확장

**파일**: `src/state.py`

```python
class AgentState(TypedDict):
    ...
    # === DB 엔진 정보 ===
    active_db_engine: Optional[str]  # 현재 DB의 엔진 타입 ("db2", "postgresql", etc.)
```

---

### Phase E: 쿼리 검증 보강

#### E-1. query_validator DB 엔진별 대응

**파일**: `src/nodes/query_validator.py`

1. **LIMIT 검사**: `FETCH FIRST ... ROWS ONLY` 패턴(DB2)도 유효한 LIMIT으로 인식
2. **LIMIT 자동 추가**: `active_db_engine`에 따라 적절한 형식으로 추가 (DB2: `FETCH FIRST N ROWS ONLY`, PostgreSQL: `LIMIT N`)
3. **테이블명 대소문자**: DB2는 기본적으로 대문자 스키마를 사용하므로, DB2일 때 테이블명 비교 시 대소문자 무시

```python
def _add_limit_clause(sql: str, limit: int, db_engine: str = "postgresql") -> str:
    sql = sql.rstrip().rstrip(";")
    if db_engine == "db2":
        return f"{sql}\nFETCH FIRST {limit} ROWS ONLY;"
    return f"{sql}\nLIMIT {limit};"
```

---

## 4. 수정 대상 파일 요약

| 파일 | 변경 내용 | 우선순위 | 상태 |
|------|----------|---------|------|
| `src/prompts/polestar_patterns.py` | **신규** - Polestar 전용 쿼리 패턴 사전 (6개 패턴, POLESTAR_META, POLESTAR_QUERY_GUIDE) | P0 | ✅ 완료 |
| `src/prompts/query_generator.py` | EAV/계층 구조 가이드 추가, DB 엔진별 문법 규칙 추가, `{polestar_guide}`/`{db_engine_hint}` 플레이스홀더 | P0 | ✅ 완료 |
| `src/nodes/query_generator.py` | `_format_polestar_guide()` 함수, `_build_system_prompt()`에 active_db_engine/polestar 분기 | P0 | ✅ 완료 |
| `src/nodes/schema_analyzer.py` | Polestar 구조 자동 감지 (`_detect_polestar_structure`), 메타데이터 보강 (`_enrich_polestar_metadata`), EAV/RESOURCE_TYPE 샘플 수집 (`_collect_polestar_samples`), DOMAIN_TABLE_HINTS 10개 확장 | P0 | ✅ 완료 |
| `src/nodes/query_validator.py` | DB 엔진별 LIMIT 문법 대응 (DB2 FETCH FIRST), 대소문자 비교 완화, 엔진별 경고 메시지 | P1 | ✅ 완료 |
| `src/prompts/input_parser.py` | query_targets 10개 확장, EAV 필터 `is_eav` 가이드 추가 | P1 | ✅ 완료 |
| `src/routing/domain_config.py` | `db_engine` 필드 추가 (polestar=db2, 나머지 postgresql) | P1 | ✅ 완료 |
| `src/state.py` | `active_db_engine: Optional[str]` 필드 추가 | P1 | ✅ 완료 |
| `tests/test_polestar_eav.py` | **신규** - 단위 테스트 19개 (구조 감지, LIMIT 검사, 가이드 포맷, 회귀, db_engine) | P2 | ✅ 완료 |
| `docs/decision.md` | D-016 의사결정 기록 추가 | - | ✅ 완료 |

---

## 5. 구현 순서

```
Step 1 (P0): 프롬프트 계층                              ✅ 완료
  ├── src/prompts/polestar_patterns.py 신규 작성
  ├── src/prompts/query_generator.py 보강
  └── src/prompts/input_parser.py 보강

Step 2 (P0): 스키마 분석 보강                            ✅ 완료
  └── src/nodes/schema_analyzer.py 수정
      (Polestar 구조 감지 + _polestar_meta 생성 + 샘플 강화)

Step 3 (P0): 쿼리 생성 보강                              ✅ 완료
  └── src/nodes/query_generator.py 수정
      (polestar_meta 기반 프롬프트 분기)

Step 4 (P1): 인프라 계층                                 ✅ 완료
  ├── src/routing/domain_config.py (db_engine 추가)
  ├── src/state.py (active_db_engine 추가)
  └── src/nodes/query_validator.py (DB 엔진별 대응)

Step 5 (P2): 테스트                                      ✅ 완료
  └── tests/test_polestar_eav.py (19개 단위 테스트)
```

### 구현 결과
- **arch-check**: 위반 0건, 경고 0건 (63개 파일, 186개 import 검사 통과)
- **테스트**: 19개 전체 통과 (0.13초)
- **병렬 실행**: Step 1+4 병렬, Step 2+3 병렬로 진행하여 효율 극대화
- **의사결정 기록**: `docs/decision.md`에 D-016 추가

---

## 6. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 |
|--------|------|----------|
| LLM이 EAV 피벗 패턴을 잘못 생성 | 쿼리 실패 | 예시 쿼리를 프롬프트에 충분히 포함 + retry 시 에러 메시지에 패턴 가이드 재삽입 |
| CMM_RESOURCE ↔ CORE_CONFIG_PROP 조인 조건 부정확 | 잘못된 결과 | RESOURCE_CONF_ID ↔ CONFIGURATION_ID 매핑 검증 필요 (운영 DB에서 확인) |
| DB 엔진별 문법 차이 (DB2/PostgreSQL) | SQL 실행 에러 | query_validator에서 db_engine 기반 문법 검증 + 자동 변환 로직 |
| 프롬프트 길이 증가 | 토큰 비용 증가 | Polestar 전용 가이드는 해당 DB 질의 시에만 삽입 (조건부) |
| 다른 DB에 영향 | 기존 기능 퇴행 | _polestar_meta가 없으면 기존 로직 그대로 동작 (하위 호환) |

---

## 7. 검증 기준

### 7.1 단위 테스트 (`tests/test_polestar_eav.py` — 19개 전체 통과)

- [x] Polestar 구조 감지 로직: CMM_RESOURCE + CORE_CONFIG_PROP 존재 시 `_polestar_meta` 생성 확인 (4개 테스트)
- [x] Polestar 메타데이터 보강: `_enrich_polestar_metadata` 정확성 확인 (1개 테스트)
- [x] LIMIT 검사: DB2(`FETCH FIRST N ROWS ONLY`)와 PostgreSQL(`LIMIT N`) 모두 인식 확인 (4개 테스트)
- [x] LIMIT 자동 추가: `active_db_engine`에 따라 올바른 형식 추가 확인 (4개 테스트)
- [x] Polestar 가이드 포맷: 기본 및 유사단어 포함 시 (2개 테스트)
- [x] DBDomainConfig db_engine 필드: polestar=db2, 나머지=postgresql (2개 테스트)

### 7.2 통합 테스트 (운영 DB 연동 후 검증 예정)

- [ ] "전체 서버 목록 조회" → EAV 피벗이 포함된 SQL 생성
- [ ] "hostname1 서버의 파일시스템 목록" → 계층 self-join SQL 생성
- [ ] "OS가 LINUX인 서버" → EAV 필터 조건이 포함된 SQL 생성
- [ ] "CPU 코어 수 Top 5 서버" → 계층 집계 + ORDER BY 포함 SQL 생성
- [ ] 생성된 SQL이 `active_db_engine`에 맞는 문법인지 검증 (DB2: FETCH FIRST, PostgreSQL: LIMIT 등)

### 7.3 회귀 테스트

- [x] Polestar가 아닌 DB 질의 시 기존 동작에 변화 없음 (2개 테스트)
- [ ] 멀티 DB 질의 시 Polestar와 다른 DB가 혼합되어도 정상 동작 (운영 연동 후 검증)
