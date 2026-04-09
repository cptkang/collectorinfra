# Plan 33: resource_conf_id JOIN 방지 -- LLM 잘못된 조인 근본 원인 제거

## 개요

| 항목 | 내용 |
|------|------|
| **의사결정 연결** | D-022 보강 (RESOURCE_CONF_ID JOIN 금지 + hostname 브릿지 조인 필수화) |
| **문제** | D-022에서 hostname 브릿지 조인을 필수화했으나, LLM이 여전히 `cmm_resource.resource_conf_id = core_config_prop.configuration_id` 기반 JOIN을 생성함 |
| **근본 원인** | 3가지: (1) 스키마에 resource_conf_id 컬럼이 무주석으로 노출, (2) query_guide 금지 문구가 resource_conf_id를 명시하지 않음, (3) 시스템 프롬프트에 금지 규칙 부재 |
| **수정 파일** | 3개 설정/소스 + 1개 검증 소스 |
| **아키텍처 계층** | config (YAML) / prompts / application (nodes) |

## 올바른 조인 패턴 (참고)

```sql
-- 1단계: hostname 값으로 core_config_prop의 Hostname 속성 행을 찾는다
LEFT JOIN polestar.core_config_prop p_host
  ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname
-- 2단계: 동일 configuration_id를 공유하는 다른 EAV 속성을 조인한다
LEFT JOIN polestar.core_config_prop p_ostype
  ON p_ostype.configuration_id = p_host.configuration_id AND p_ostype.name = 'OSType'
```

---

## 수정 1: `config/db_profiles/polestar_pg.yaml`

### 현재 문제점

**파일**: `config/db_profiles/polestar_pg.yaml`
**라인**: 91

```yaml
query_guide: |
  ...
  [EAV 피벗 쿼리 패턴]
  주의: cmm_resource.id ≠ core_config_prop.configuration_id 이므로 id로 직접 조인할 수 없습니다.
  반드시 hostname 값 기반 조인을 거쳐야 합니다.
```

문제:
- `cmm_resource.id`만 언급하고 `cmm_resource.resource_conf_id`는 명시적으로 금지하지 않음
- LLM은 `resource_conf_id`가 `id`와 별개의 컬럼이라 판단하고, 이름에서 "conf_id"를 보고 `configuration_id`와의 JOIN에 사용함

### 수정 내용

#### 수정 1-A: `query_guide` 금지 문구 강화

**Before** (line 91):
```yaml
  [EAV 피벗 쿼리 패턴]
  주의: cmm_resource.id ≠ core_config_prop.configuration_id 이므로 id로 직접 조인할 수 없습니다.
  반드시 hostname 값 기반 조인을 거쳐야 합니다.
```

**After**:
```yaml
  [EAV 피벗 쿼리 패턴]
  [금지] cmm_resource와 core_config_prop를 직접 조인하는 것은 금지입니다:
    - cmm_resource.id = core_config_prop.configuration_id (X) -- 서로 다른 ID 체계
    - cmm_resource.resource_conf_id = core_config_prop.configuration_id (X) -- 운영 DB에서 resource_conf_id는 NULL
  반드시 아래 hostname 값 기반 브릿지 조인 패턴만 사용하세요.
```

#### 수정 1-B: `excluded_join_columns` 신규 필드 추가

EAV 패턴 블록에 `excluded_join_columns` 필드를 추가한다. 이 필드는 코드에서 읽어 스키마 출력 시 해당 컬럼에 경고 주석을 붙이는 데 사용된다.

**위치**: `patterns[0]` (EAV 패턴) 안, `value_joins` 뒤

**추가할 내용**:
```yaml
    # JOIN에 사용하면 안 되는 컬럼 목록
    # 해당 컬럼이 스키마 프롬프트에 출력될 때 "-- JOIN 금지(NULL)" 주석이 추가된다.
    excluded_join_columns:
      - table: cmm_resource
        column: resource_conf_id
        reason: "운영 DB에서 NULL. core_config_prop.configuration_id와 매핑되지 않음"
```

#### 전체 수정 후 polestar_pg.yaml EAV 패턴 영역

```yaml
  - type: eav
    entity_table: cmm_resource
    config_table: core_config_prop
    attribute_column: name
    value_column: stringvalue_short
    lob_value_column: stringvalue
    lob_flag_column: is_lob

    value_joins:
      - eav_attribute: Hostname
        eav_value_column: stringvalue_short
        entity_column: hostname
        description: "EAV Hostname 속성값 = cmm_resource.hostname"
      - eav_attribute: IPaddress
        eav_value_column: stringvalue_short
        entity_column: ipaddress
        description: "EAV IPaddress 속성값 = cmm_resource.ipaddress"

    # JOIN에 사용하면 안 되는 컬럼 목록
    excluded_join_columns:
      - table: cmm_resource
        column: resource_conf_id
        reason: "운영 DB에서 NULL. core_config_prop.configuration_id와 매핑되지 않음"

    known_attributes:
      # ... (기존과 동일)
```

### 영향 범위

- `_load_manual_profile()` (`src/nodes/schema_analyzer.py` line 392): YAML을 dict로 로드만 하므로 새 필드는 자동으로 `structure_meta`에 포함됨. 코드 변경 불필요.
- `_format_structure_guide()` (`src/nodes/query_generator.py` line 26): `query_guide` 문자열이 변경되므로 LLM이 더 명확한 금지 지시를 받음. 코드 변경 불필요.
- `excluded_join_columns` 필드는 수정 3에서 코드가 읽어서 활용함.

---

## 수정 2: `src/prompts/query_generator.py` — 시스템 프롬프트 규칙 10 추가

> **검색 확인 결과 (2026-03-26):**
> EAV 쿼리 생성을 위한 기존 코드를 검색한 결과, 추가 시스템 프롬프트/코드는 불필요하다.
> - `_build_user_prompt()` (query_generator.py:383-420): EAV 피벗 매핑 + 브릿지 조인 힌트 이미 구현
> - `_format_structure_guide()` (query_generator.py:26-92): value_joins, EAV 속성명 유사단어 가이드 포함
> - `multi_db_executor.py:298-324`: structure_guide에 value_joins를 직접 조합 (금지 컬럼 경고만 누락 → 수정 5에서 해결)
>
> **결론**: 기존 EAV 처리가 충분하므로 이 수정은 규칙 10 (JOIN 금지 컬럼 규칙) 추가만으로 완결된다.

### 현재 문제점

**파일**: `src/prompts/query_generator.py`
**라인**: 7-43 (`QUERY_GENERATOR_SYSTEM_TEMPLATE`)

```python
## 규칙 (반드시 준수)

1. **SELECT 문만 생성합니다.**
2. **테이블/컬럼명은 위 스키마에 존재하는 것만 사용합니다.**
   ...
```

문제:
- "스키마에 존재하는 컬럼만 사용하라"는 규칙은 있지만, `resource_conf_id`는 실제 스키마에 존재하는 컬럼이므로 이 규칙으로 차단할 수 없음
- EAV 구조에서 특정 컬럼을 JOIN에 사용하지 말라는 금지 규칙이 없음
- `{structure_guide}` 플레이스홀더로 query_guide가 삽입되지만, 이는 규칙 섹션과 분리되어 있어 LLM이 규칙보다 스키마를 우선할 수 있음

### 수정 내용

`QUERY_GENERATOR_SYSTEM_TEMPLATE`의 규칙 섹션에 항목 10을 추가한다. 이 규칙은 `{structure_guide}` 내 금지 컬럼 정보가 있을 때 LLM의 주의를 환기시키는 역할을 한다.

**Before** (line 31-35):
```python
8. 양식-DB 매핑이 제공된 경우, 매핑된 모든 컬럼을 SELECT에 포함하고 "테이블명.컬럼명" 형태의 alias를 부여하세요. 예: SELECT s.hostname AS "servers.hostname"
9. 여러 테이블의 컬럼이 매핑된 경우, 적절한 JOIN을 사용하세요.

## 출력 형식
```

**After**:
```python
8. 양식-DB 매핑이 제공된 경우, 매핑된 모든 컬럼을 SELECT에 포함하고 "테이블명.컬럼명" 형태의 alias를 부여하세요. 예: SELECT s.hostname AS "servers.hostname"
9. 여러 테이블의 컬럼이 매핑된 경우, 적절한 JOIN을 사용하세요.
10. **스키마에 "-- JOIN 금지" 주석이 붙은 컬럼은 절대 JOIN 조건(ON 절)에 사용하지 마세요.** 해당 컬럼은 운영 DB에서 NULL이거나 의미가 다른 ID입니다. 구조 가이드에 명시된 값 기반 조인 패턴만 사용하세요.

## 출력 형식
```

### 전체 수정 후 파일

```python
QUERY_GENERATOR_SYSTEM_TEMPLATE = """당신은 인프라 DB에 대한 SQL 쿼리를 생성하는 전문가입니다.
아래 스키마 정보를 참고하여 사용자의 요구사항에 맞는 SQL을 생성하세요.

## DB 스키마

{schema}

{structure_guide}

## 규칙 (반드시 준수)

1. **SELECT 문만 생성합니다.** INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE 등은 절대 금지입니다.
2. **테이블/컬럼명은 위 스키마에 존재하는 것만 사용합니다.** 존재하지 않는 이름을 임의로 사용하지 마세요.
   - 스키마에 표시된 테이블명을 그대로 사용하세요. 예를 들어 스키마에 `polestar.cmm_resource`로 표시되어 있으면 FROM 절에 `polestar.cmm_resource`를 사용해야 합니다. 스키마 접두사를 생략하지 마세요.
3. **행 제한 절을 포함합니다.**
   - PostgreSQL/MySQL: `LIMIT {default_limit}`
   - DB2: `FETCH FIRST {default_limit} ROWS ONLY`
   사용자가 특정 개수를 지정하면 그 값을 사용합니다.
   {db_engine_hint}
4. 필요 시 JOIN, GROUP BY, ORDER BY, 집계 함수(COUNT, AVG, SUM, MAX, MIN)를 활용합니다.
5. 시간 범위 필터가 있으면 timestamp 컬럼에 WHERE 조건을 적용합니다.
6. 쿼리에 주석(-- 설명)을 포함하여 쿼리의 목적을 설명합니다.
7. 테이블 별칭(alias)을 사용하여 가독성을 높입니다.
8. 양식-DB 매핑이 제공된 경우, 매핑된 모든 컬럼을 SELECT에 포함하고 "테이블명.컬럼명" 형태의 alias를 부여하세요. 예: SELECT s.hostname AS "servers.hostname"
9. 여러 테이블의 컬럼이 매핑된 경우, 적절한 JOIN을 사용하세요.
10. **스키마에 "-- JOIN 금지" 주석이 붙은 컬럼은 절대 JOIN 조건(ON 절)에 사용하지 마세요.** 해당 컬럼은 운영 DB에서 NULL이거나 의미가 다른 ID입니다. 구조 가이드에 명시된 값 기반 조인 패턴만 사용하세요.

## 출력 형식

SQL 쿼리만 ```sql 코드블록으로 출력하세요. 추가 설명은 불필요합니다.

```sql
-- 쿼리 설명
SELECT ...
FROM ...
LIMIT ... ;  -- 또는 FETCH FIRST ... ROWS ONLY (DB2)
```
"""
```

### 영향 범위

- `src/nodes/query_generator.py`의 `_build_system_prompt()`: `QUERY_GENERATOR_SYSTEM_TEMPLATE`을 사용하여 시스템 프롬프트를 생성. 추가 코드 변경 불필요.
- `src/nodes/multi_db_executor.py`의 `_generate_sql()`: 동일한 `QUERY_GENERATOR_SYSTEM_TEMPLATE`을 import하여 사용. 추가 코드 변경 불필요.
- 규칙 10은 수정 3에서 스키마에 "-- JOIN 금지" 주석이 추가되어야 효과를 발휘함.

---

## 수정 3: `src/nodes/query_generator.py`의 `_format_schema_for_prompt()`

### 현재 문제점

**파일**: `src/nodes/query_generator.py`
**라인**: 441-523 (`_format_schema_for_prompt()`)

```python
for col in table_data.get("columns", []):
    col_key = f"{table_name}.{col['name']}"
    col_str = f"  - {col['name']}: {col['type']}"
    if col.get("primary_key"):
        col_str += " [PK]"
    if col.get("foreign_key"):
        col_str += f" [FK -> {col.get('references', '?')}]"
    if not col.get("nullable", True):
        col_str += " NOT NULL"
    # 컬럼 설명 추가
    desc = descriptions.get(col_key)
    if desc:
        col_str += f" -- {desc}"
    # 유사 단어 추가
    syns = synonyms.get(col_key)
    if syns:
        col_str += f" [유사: {', '.join(syns[:5])}]"
    columns_desc.append(col_str)
```

문제:
- 모든 컬럼을 동일하게 출력하므로 `resource_conf_id`가 아무 경고 없이 LLM에 노출됨
- LLM은 `resource_conf_id` (BIGINT)와 `configuration_id` (BIGINT)의 이름 유사성을 보고 JOIN 후보로 인식함

### 수정 내용

#### 수정 3-A: `_format_schema_for_prompt()`에 `excluded_join_columns` 처리 추가

`schema_info`의 `_structure_meta` > `patterns` > `excluded_join_columns`에서 금지 컬럼 목록을 추출하고, 해당 컬럼 출력 시 `-- JOIN 금지(NULL)` 주석을 추가한다.

**Before** (line 441-523 중 핵심부):
```python
def _format_schema_for_prompt(
    schema_info: dict,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    descriptions = column_descriptions or {}
    synonyms = column_synonyms or {}

    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_data in tables.items():
        columns_desc: list[str] = []
        for col in table_data.get("columns", []):
            col_key = f"{table_name}.{col['name']}"
            col_str = f"  - {col['name']}: {col['type']}"
            ...
```

**After**:
```python
def _format_schema_for_prompt(
    schema_info: dict,
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
    resource_type_synonyms: dict[str, list[str]] | None = None,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> str:
    descriptions = column_descriptions or {}
    synonyms = column_synonyms or {}

    # excluded_join_columns 추출: {(table_lower, column_lower): reason}
    excluded_join_map = _build_excluded_join_map(schema_info)

    lines: list[str] = []
    tables = schema_info.get("tables", {})

    for table_name, table_data in tables.items():
        # table_name에서 스키마 접두사 제거한 bare name 추출
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        columns_desc: list[str] = []
        for col in table_data.get("columns", []):
            col_key = f"{table_name}.{col['name']}"
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            if not col.get("nullable", True):
                col_str += " NOT NULL"
            # JOIN 금지 컬럼 주석 추가
            col_lower = col["name"].lower()
            excluded_reason = excluded_join_map.get((bare_table, col_lower))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            # 컬럼 설명 추가
            desc = descriptions.get(col_key)
            if desc:
                col_str += f" -- {desc}"
            # 유사 단어 추가
            syns = synonyms.get(col_key)
            if syns:
                col_str += f" [유사: {', '.join(syns[:5])}]"
            columns_desc.append(col_str)
        ...
```

#### 수정 3-B: `_build_excluded_join_map()` 헬퍼 함수 신규 작성

`_format_schema_for_prompt()` 직전에 추가한다.

```python
def _build_excluded_join_map(schema_info: dict) -> dict[tuple[str, str], str]:
    """_structure_meta의 excluded_join_columns에서 금지 컬럼 매핑을 구축한다.

    Args:
        schema_info: 스키마 정보 딕셔너리

    Returns:
        {(table_lower, column_lower): reason} 매핑
    """
    result: dict[tuple[str, str], str] = {}
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return result
    for pattern in structure_meta.get("patterns", []):
        for excl in pattern.get("excluded_join_columns", []):
            table = excl.get("table", "").lower()
            column = excl.get("column", "").lower()
            reason = excl.get("reason", "NULL")
            if table and column:
                result[(table, column)] = reason
    return result
```

### 수정 3-C: `src/nodes/multi_db_executor.py`의 `_format_schema()`에도 동일 적용

**파일**: `src/nodes/multi_db_executor.py`
**라인**: 489-521 (`_format_schema()`)

현재 `_format_schema()`는 `query_generator.py`의 `_format_schema_for_prompt()`보다 간소한 버전이다. `column_descriptions`, `column_synonyms` 등을 받지 않지만, `schema_info`에서 `_structure_meta`는 접근 가능하다.

**Before** (line 489-521):
```python
def _format_schema(schema_info: dict) -> str:
    lines: list[str] = []
    for table_name, table_data in schema_info.get("tables", {}).items():
        lines.append(f"### {table_name}")
        for col in table_data.get("columns", []):
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            lines.append(col_str)
        ...
```

**After**:
```python
def _format_schema(schema_info: dict) -> str:
    # excluded_join_columns 추출
    excluded_join_map = _build_excluded_join_map(schema_info)

    lines: list[str] = []
    for table_name, table_data in schema_info.get("tables", {}).items():
        bare_table = table_name.rsplit(".", 1)[-1].lower()
        lines.append(f"### {table_name}")
        for col in table_data.get("columns", []):
            col_str = f"  - {col['name']}: {col['type']}"
            if col.get("primary_key"):
                col_str += " [PK]"
            if col.get("foreign_key"):
                col_str += f" [FK -> {col.get('references', '?')}]"
            # JOIN 금지 컬럼 주석 추가
            col_lower = col["name"].lower()
            excluded_reason = excluded_join_map.get((bare_table, col_lower))
            if excluded_reason:
                col_str += f" -- JOIN 금지({excluded_reason})"
            lines.append(col_str)
        ...
```

`_build_excluded_join_map()` 함수는 `multi_db_executor.py`에도 필요하다. 중복을 피하기 위해 두 가지 방안이 있다:

**방안 A (권장)**: `src/utils/schema_utils.py`에 공용 함수로 배치하고 양쪽에서 import.
- `utils` 계층은 `application` 계층(nodes/)에서 참조 가능 (계층 규칙 충족)
- `MODULE_LAYER_MAP`에 `src.utils.schema_utils` 추가 필요 (layer: `utils`)

**방안 B**: 각 모듈에 동일 함수를 중복 정의.
- 코드 중복이지만 의존 관계가 단순

**결정**: 방안 A를 채택한다. 함수가 `_structure_meta`(infrastructure 계층 데이터 모델)를 읽지만, dict 자체를 순회하는 순수 유틸이므로 `utils` 계층에 배치하는 것이 적합하다.

### 수정 3-D: `src/utils/schema_utils.py` 신규 파일

```python
"""스키마 관련 유틸리티 함수.

_structure_meta 딕셔너리에서 메타데이터를 추출하는 순수 함수들.
application 계층(nodes/)에서 공용으로 사용한다.
"""

from __future__ import annotations


def build_excluded_join_map(schema_info: dict) -> dict[tuple[str, str], str]:
    """_structure_meta의 excluded_join_columns에서 금지 컬럼 매핑을 구축한다.

    Args:
        schema_info: 스키마 정보 딕셔너리

    Returns:
        {(table_lower, column_lower): reason} 매핑.
        예: {("cmm_resource", "resource_conf_id"): "NULL"}
    """
    result: dict[tuple[str, str], str] = {}
    structure_meta = schema_info.get("_structure_meta")
    if not structure_meta:
        return result
    for pattern in structure_meta.get("patterns", []):
        for excl in pattern.get("excluded_join_columns", []):
            table = excl.get("table", "").lower()
            column = excl.get("column", "").lower()
            reason = excl.get("reason", "NULL")
            if table and column:
                result[(table, column)] = reason
    return result
```

**아키텍처 계층**: `config/utils` (기존 `src/utils/` 디렉토리의 계층)
**`scripts/arch_check.py` 등록**: `MODULE_LAYER_MAP`에 `"src.utils.schema_utils": "utils"` 추가

### 스키마 출력 결과 예시

수정 전:
```
### polestar.cmm_resource
  - id: BIGINT [PK]
  - hostname: VARCHAR(255)
  - resource_conf_id: BIGINT
  ...
```

수정 후:
```
### polestar.cmm_resource
  - id: BIGINT [PK]
  - hostname: VARCHAR(255)
  - resource_conf_id: BIGINT -- JOIN 금지(운영 DB에서 NULL. core_config_prop.configuration_id와 매핑되지 않음)
  ...
```

### 영향 범위

- `_format_schema_for_prompt()` 호출 경로: `query_generator()` -> `_build_system_prompt()` -> `_format_schema_for_prompt()`
- `_format_schema()` 호출 경로: `multi_db_executor()` -> `_generate_sql()` -> `_format_schema()`
- 두 경로 모두 `schema_info["_structure_meta"]`를 참조하므로, `polestar_pg.yaml`에 `excluded_join_columns`가 추가되면 자동으로 동작함

---

## 수정 4 (부가): `src/nodes/query_validator.py`에 금지 JOIN 감지 경고 추가

### 현재 상태

`query_validator.py`의 `_validate_columns()` (line 279-337)은 컬럼 존재 여부만 검증하고, 특정 컬럼이 JOIN 조건에 사용되었는지는 확인하지 않음.

### 수정 내용

검증 항목 6 (`_validate_columns`) 뒤에 새 검증 항목 6.5를 추가하여, `excluded_join_columns`에 지정된 컬럼이 ON 절에 사용되었는지 감지하고 **경고**(에러가 아닌 warning)를 발생시킨다. 이는 재생성을 유도하는 에러가 아니라, 감사 로그에 기록되는 경고이다.

단, 이 검증이 에러로 분류되면 재시도 루프가 동작하여 성능 저하가 발생할 수 있으므로, 첫 번째 구현에서는 **warning 레벨**로 처리한다. 실 운영에서 반복 발생하면 에러로 승격하는 것을 검토한다.

**추가 위치**: `query_validator()` 함수의 "6. 참조 컬럼 존재 여부" 뒤 (line 108 이후)

```python
    # 6.5. 금지 JOIN 컬럼 사용 감지
    excluded_join_warnings = _check_excluded_join_columns(sql, schema_info)
    warnings.extend(excluded_join_warnings)
```

**새 함수**:
```python
def _check_excluded_join_columns(sql: str, schema_info: dict) -> list[str]:
    """금지된 컬럼이 JOIN ON 절에 사용되었는지 감지한다.

    Args:
        sql: SQL 쿼리
        schema_info: 스키마 정보

    Returns:
        경고 메시지 목록
    """
    from src.utils.schema_utils import build_excluded_join_map

    excluded_map = build_excluded_join_map(schema_info)
    if not excluded_map:
        return []

    warnings: list[str] = []
    # ON 절 추출 (간이)
    on_clauses = re.findall(r"\bON\s+(.+?)(?:\bWHERE\b|\bGROUP\b|\bORDER\b|\bLIMIT\b|\bLEFT\b|\bRIGHT\b|\bINNER\b|\bFULL\b|\bCROSS\b|\bJOIN\b|\bFETCH\b|;|$)", sql, re.IGNORECASE | re.DOTALL)

    for clause in on_clauses:
        for (table_lower, col_lower), reason in excluded_map.items():
            # alias.column 또는 table.column 패턴에서 컬럼명 매칭
            if re.search(rf"\b\w+\.{re.escape(col_lower)}\b", clause, re.IGNORECASE):
                warnings.append(
                    f"JOIN 금지 컬럼 '{table_lower}.{col_lower}'이 ON 절에 사용되었습니다. "
                    f"사유: {reason}. hostname 값 기반 브릿지 조인을 사용하세요."
                )
    return warnings
```

### 향후 에러 승격 기준

운영 로그에서 이 경고가 3회 이상 반복 발생하면, `warnings` 대신 `errors`에 추가하여 재생성 루프를 유도하는 것을 D-022 업데이트로 결정한다.

---

## 수정 5: `_format_structure_guide()`에 금지 컬럼 경고 자동 삽입

### 현재 상태

**파일**: `src/nodes/query_generator.py`
**라인**: 26-92 (`_format_structure_guide()`)

이 함수는 `structure_meta`의 `query_guide`, `value_joins`, `resource_type_synonyms`, `eav_name_synonyms`를 조합하여 가이드 텍스트를 생성한다. 그러나 `excluded_join_columns` 정보는 가이드에 포함되지 않는다.

### 수정 내용

`_format_structure_guide()` 끝부분에 `excluded_join_columns` 경고를 자동 삽입한다.

**추가 위치**: `_format_structure_guide()` 함수의 `samples` 처리 이후, `return guide` 직전

```python
    # 금지 JOIN 컬럼 경고
    for pattern in structure_meta.get("patterns", []):
        excluded = pattern.get("excluded_join_columns", [])
        if excluded:
            guide += "\n\n[금지 JOIN 컬럼]"
            guide += "\n다음 컬럼은 JOIN ON 절에서 절대 사용하지 마세요:"
            for excl in excluded:
                guide += (
                    f"\n- {excl.get('table', '?')}.{excl.get('column', '?')}: "
                    f"{excl.get('reason', 'JOIN 불가')}"
                )

    return guide
```

### 수정 범위

- `_format_structure_guide()`는 `query_generator.py`와 `multi_db_executor.py` 양쪽에서 호출되지 않음.
  - `query_generator.py`: `_build_system_prompt()` -> `_format_structure_guide()` 호출 (line 208)
  - `multi_db_executor.py`: `_generate_sql()` 내에서 `structure_meta.get("query_guide")`를 직접 읽고 value_joins를 수동으로 추가 (line 298-324). `_format_structure_guide()`를 호출하지 않음.

따라서 `multi_db_executor.py`의 `_generate_sql()` (line 298-324)에도 동일한 금지 컬럼 경고 로직을 추가해야 한다.

**`multi_db_executor.py` 추가 위치**: line 324 이후 (`for vj in value_joins:` 루프 이후)

```python
        # 금지 JOIN 컬럼 경고 추가
        for excl in eav_p.get("excluded_join_columns", []):
            structure_guide += (
                f"\n[금지] {excl.get('table', '?')}.{excl.get('column', '?')}는 "
                f"JOIN ON 절에서 사용할 수 없습니다: {excl.get('reason', 'JOIN 불가')}"
            )
```

---

## 아키텍처 계층 매핑

| 수정 대상 | 계층 | 비고 |
|-----------|------|------|
| `config/db_profiles/polestar_pg.yaml` | config | 설정 파일, 계층 규칙 외 |
| `src/prompts/query_generator.py` | prompts | 프롬프트 템플릿 |
| `src/utils/schema_utils.py` (신규) | utils | 순수 유틸 함수 |
| `src/nodes/query_generator.py` | application | `_format_schema_for_prompt()`, `_format_structure_guide()` |
| `src/nodes/multi_db_executor.py` | application | `_format_schema()`, `_generate_sql()` |
| `src/nodes/query_validator.py` | application | `_check_excluded_join_columns()` |

의존 방향: `config/db_profiles/` <- `utils` <- `application(nodes/)` <- `prompts`
계층 규칙 위반 없음.

### `scripts/arch_check.py` MODULE_LAYER_MAP 추가

```python
"src.utils.schema_utils": "utils",
```

---

## 구현 순서

```
1. config/db_profiles/polestar_pg.yaml 수정 (수정 1)
   ├── query_guide 금지 문구 강화 (1-A)
   └── excluded_join_columns 필드 추가 (1-B)
2. src/utils/schema_utils.py 신규 작성 (수정 3-D)
   └── build_excluded_join_map() 함수
3. src/prompts/query_generator.py 수정 (수정 2)
   └── 규칙 10 추가
4. src/nodes/query_generator.py 수정 (수정 3-A, 수정 5)
   ├── _format_schema_for_prompt()에 excluded_join 주석 추가
   └── _format_structure_guide()에 금지 컬럼 경고 추가
5. src/nodes/multi_db_executor.py 수정 (수정 3-C, 수정 5)
   ├── _format_schema()에 excluded_join 주석 추가
   └── _generate_sql()에 금지 컬럼 경고 추가
6. src/nodes/query_validator.py 수정 (수정 4)
   └── _check_excluded_join_columns() 경고 추가
7. scripts/arch_check.py MODULE_LAYER_MAP 업데이트
```

---

## 검증 방법

### 단위 테스트

#### 테스트 1: `build_excluded_join_map()` 함수 검증

```python
# tests/test_utils/test_schema_utils.py

def test_build_excluded_join_map_returns_mapping():
    schema_info = {
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "cmm_resource", "column": "resource_conf_id", "reason": "NULL"},
                ]
            }]
        }
    }
    result = build_excluded_join_map(schema_info)
    assert ("cmm_resource", "resource_conf_id") in result
    assert result[("cmm_resource", "resource_conf_id")] == "NULL"


def test_build_excluded_join_map_empty_when_no_meta():
    assert build_excluded_join_map({}) == {}
    assert build_excluded_join_map({"_structure_meta": None}) == {}
```

#### 테스트 2: `_format_schema_for_prompt()` 출력에 JOIN 금지 주석 포함 확인

```python
# tests/test_nodes/test_query_generator_excluded_join.py

def test_schema_prompt_contains_join_warning():
    schema_info = {
        "tables": {
            "polestar.cmm_resource": {
                "columns": [
                    {"name": "id", "type": "BIGINT", "primary_key": True},
                    {"name": "hostname", "type": "VARCHAR(255)"},
                    {"name": "resource_conf_id", "type": "BIGINT"},
                ],
            },
        },
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "cmm_resource", "column": "resource_conf_id",
                     "reason": "운영 DB에서 NULL"},
                ]
            }]
        },
    }
    from src.nodes.query_generator import _format_schema_for_prompt
    result = _format_schema_for_prompt(schema_info)
    assert "resource_conf_id" in result
    assert "JOIN 금지" in result
    assert "운영 DB에서 NULL" in result
    # id, hostname에는 JOIN 금지가 없어야 함
    lines = result.split("\n")
    for line in lines:
        if "id:" in line and "resource_conf_id" not in line:
            assert "JOIN 금지" not in line
```

#### 테스트 3: `_check_excluded_join_columns()` ON 절 감지

```python
# tests/test_nodes/test_query_validator_excluded_join.py

def test_detects_excluded_column_in_on_clause():
    sql = """
    SELECT r.hostname, p.stringvalue_short
    FROM polestar.cmm_resource r
    JOIN polestar.core_config_prop p
      ON p.configuration_id = r.resource_conf_id
    LIMIT 100;
    """
    schema_info = {
        "tables": {},
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "cmm_resource", "column": "resource_conf_id",
                     "reason": "NULL"},
                ]
            }]
        },
    }
    from src.nodes.query_validator import _check_excluded_join_columns
    warnings = _check_excluded_join_columns(sql, schema_info)
    assert len(warnings) == 1
    assert "resource_conf_id" in warnings[0]


def test_no_warning_for_legitimate_join():
    sql = """
    SELECT r.hostname
    FROM polestar.cmm_resource r
    LEFT JOIN polestar.core_config_prop p_host
      ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname
    LIMIT 100;
    """
    schema_info = {
        "tables": {},
        "_structure_meta": {
            "patterns": [{
                "type": "eav",
                "excluded_join_columns": [
                    {"table": "cmm_resource", "column": "resource_conf_id",
                     "reason": "NULL"},
                ]
            }]
        },
    }
    from src.nodes.query_validator import _check_excluded_join_columns
    warnings = _check_excluded_join_columns(sql, schema_info)
    assert len(warnings) == 0
```

### 통합 테스트 (E2E)

```python
# tests/test_plan33_join_prevention.py

import pytest

@pytest.mark.asyncio
async def test_llm_generates_hostname_bridge_join():
    """LLM이 cmm_resource와 core_config_prop 조인 시
    resource_conf_id 대신 hostname 브릿지 조인을 사용하는지 확인한다.
    """
    # 1. polestar_pg.yaml의 excluded_join_columns가 로드되는지 확인
    from src.nodes.schema_analyzer import _load_manual_profile
    profile = _load_manual_profile("polestar_pg")
    assert profile is not None
    eav_pattern = profile["patterns"][0]
    assert "excluded_join_columns" in eav_pattern
    assert eav_pattern["excluded_join_columns"][0]["column"] == "resource_conf_id"

    # 2. 스키마 프롬프트에 JOIN 금지 주석이 포함되는지 확인
    from src.nodes.query_generator import _format_schema_for_prompt
    schema_info = _build_test_schema_info(profile)
    schema_text = _format_schema_for_prompt(schema_info)
    assert "JOIN 금지" in schema_text

    # 3. structure_guide에 금지 컬럼 경고가 포함되는지 확인
    from src.nodes.query_generator import _format_structure_guide
    guide = _format_structure_guide(profile)
    assert "resource_conf_id" in guide
    assert "JOIN" in guide and "금지" in guide

    # 4. (선택) 실제 LLM 호출로 생성된 SQL에 resource_conf_id JOIN이 없는지 확인
    # 이 테스트는 LLM API 키가 있는 환경에서만 실행
    # sql = await _call_query_generator_with_test_state(...)
    # assert "resource_conf_id" not in sql.lower() or "join 금지" in sql.lower()
```

### 수동 검증 절차

1. `python -c "from src.nodes.schema_analyzer import _load_manual_profile; p = _load_manual_profile('polestar_pg'); print(p['patterns'][0].get('excluded_join_columns'))"` -- `excluded_join_columns`가 로드되는지 확인
2. 실제 쿼리 실행: "서버별 OS 종류를 알려줘" -> 생성된 SQL에서 `resource_conf_id`가 JOIN ON 절에 없는지 확인
3. `scripts/arch_check.py --verbose` 실행하여 계층 위반 없음 확인

---

## `docs/02_decision.md` D-022 업데이트 내용

D-022의 "수정된 파일" 섹션에 아래 항목을 추가한다:

```markdown
### 수정된 파일 (Plan 33 추가)

6. **`config/db_profiles/polestar_pg.yaml`**: `query_guide`의 금지 문구 강화 + `excluded_join_columns` 필드 추가
7. **`src/prompts/query_generator.py`**: 시스템 프롬프트 규칙 10 추가 (JOIN 금지 컬럼 규칙)
8. **`src/utils/schema_utils.py`** (신규): `build_excluded_join_map()` 공용 함수
9. **`src/nodes/query_generator.py`**: `_format_schema_for_prompt()`에 JOIN 금지 주석 추가, `_format_structure_guide()`에 금지 컬럼 경고 삽입
10. **`src/nodes/multi_db_executor.py`**: `_format_schema()`에 JOIN 금지 주석 추가, `_generate_sql()`에 금지 컬럼 경고 삽입
11. **`src/nodes/query_validator.py`**: `_check_excluded_join_columns()` 경고 감지 추가
```

변경 이력 테이블에 추가:
```
| 2026-03-26 | D-022 | Plan 33: resource_conf_id JOIN 방지 3중 보강 -- YAML 금지 필드, 시스템 프롬프트 규칙, 스키마 출력 주석, 검증기 경고 |
```

---

## 리스크 및 대안

| 리스크 | 대응 |
|--------|------|
| `excluded_join_columns`가 YAML에 추가되었지만 캐시된 `_structure_meta`에 반영되지 않음 | `_load_manual_profile()`이 Redis 캐시보다 우선하므로 (schema_analyzer.py line 676-682) 캐시 무효화 없이도 다음 쿼리부터 반영됨 |
| LLM이 "-- JOIN 금지" 주석을 무시하고 여전히 resource_conf_id를 JOIN에 사용 | 3중 방어 (스키마 주석 + 시스템 규칙 + 구조 가이드 경고)로 확률 최소화. query_validator 경고로 사후 감지. 반복 발생 시 validator에서 에러로 승격하여 재생성 유도 |
| `_check_excluded_join_columns()`의 ON 절 추출 정규식이 복잡한 SQL에서 오탐/누락 | 간이 검증이므로 false negative 허용. 핵심 방어는 LLM 프롬프트 수준 |
| `multi_db_executor.py`와 `query_generator.py`의 `_format_structure_guide` 로직 분산 | 장기적으로 `multi_db_executor.py`에서도 `_format_structure_guide()`를 공유하도록 리팩토링 고려 (이번 scope 외) |

---

## 요약

| 수정 | 파일 | 방어 계층 | 역할 |
|------|------|-----------|------|
| 수정 1-A | `polestar_pg.yaml` (query_guide) | 구조 가이드 텍스트 | LLM에 금지 컬럼을 명시적으로 안내 |
| 수정 1-B | `polestar_pg.yaml` (excluded_join_columns) | 설정 데이터 | 코드가 읽어서 스키마 주석/경고 자동 생성 |
| 수정 2 | `query_generator.py` (프롬프트) | 시스템 규칙 | LLM의 규칙 준수 계층에서 금지 |
| 수정 3 | `query_generator.py` + `multi_db_executor.py` (스키마 출력) | 스키마 인지 | LLM이 컬럼을 볼 때 즉시 경고 인지 |
| 수정 4 | `query_validator.py` (검증) | 사후 감지 | 잘못된 JOIN이 생성되었을 때 경고 |
| 수정 5 | `query_generator.py` + `multi_db_executor.py` (구조 가이드) | 가이드 강화 | 구조 가이드에서 한 번 더 금지 명시 |

3중 방어 (프롬프트 규칙 + 스키마 주석 + 구조 가이드 경고) + 사후 감지 (validator 경고)로 LLM의 잘못된 resource_conf_id JOIN 생성을 근본적으로 차단한다.
