# Plan 33: EAV 조인 지침의 LLM 프롬프트 강제 적용

> 작성일: 2026-03-26
> 상태: **계획 수립**
> 관련: Plan 32 (EAV 수동 프로필), Plan 20 (EAV 피벗 쿼리), Plan 25 (EAV 필드 검증)

---

## 1. 문제 현상

Plan 32에서 `config/db_profiles/polestar_pg.yaml`에 올바른 조인 지침을 설정했음에도, LLM이 여전히 잘못된 조인을 생성한다:

```sql
-- LLM이 생성하는 잘못된 쿼리 (반복 발생)
LEFT JOIN polestar.core_config_prop AS p_os
    ON r.id = p_os.configuration_id AND p_os.name = 'OSType'
```

### 왜 잘못된 조인인가

실제 DB 데이터 확인 결과:

```
cmm_resource (hostname1):       ID = 873
core_config_prop (hostname1):   CONFIGURATION_ID = 110

→ 873 ≠ 110  — ID 체계가 완전히 다름
→ cmm_resource.resource_conf_id = NULL (운영 DB에서 비어있음)
→ cmm_resource와 core_config_prop 사이에 FK 없음 (FK 조회 0건 확인)
```

### 올바른 조인 패턴 (2단계 브릿지)

```sql
-- 1단계: hostname 값으로 core_config_prop의 Hostname 속성을 찾는다
LEFT JOIN polestar.core_config_prop p_host
    ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname

-- 2단계: 같은 configuration_id 그룹 내에서 다른 EAV 속성을 조인
LEFT JOIN polestar.core_config_prop p_ostype
    ON p_ostype.configuration_id = p_host.configuration_id AND p_ostype.name = 'OSType'
```

---

## 2. 근본 원인 분석

프로필의 `query_guide`, `value_joins`, `excluded_join_columns`가 존재하지만, 코드 파이프라인에서 이를 LLM에 **충분히 강하게 전달하지 않는다**:

| 단계 | 현재 상태 | 문제 |
|------|----------|------|
| **스키마 프롬프트** | 테이블 컬럼 목록만 나열 | `id`, `configuration_id`, `resource_conf_id`가 조인 가능해 보임 |
| **query_generator 프롬프트** | query_guide를 텍스트로 삽입 | LLM이 "금지" 지시를 무시하고 자체 판단으로 조인 |
| **query_validator** | 별칭-테이블 매핑, 컬럼 존재 여부만 검증 | 잘못된 조인 패턴을 감지/차단하지 않음 |
| **excluded_join_columns** | 프로필에 정의됨 | 처리하는 코드가 없음 |

---

## 3. 수정 계획

### 3.1 스키마 프롬프트에 조인 금지 주석 삽입 (excluded_join_columns 처리)

**대상 파일**: `src/nodes/schema_analyzer.py` 또는 스키마 포맷팅 함수

프로필의 `excluded_join_columns`를 읽어 스키마 프롬프트의 해당 컬럼에 경고 주석을 삽입한다:

```
현재:
  cmm_resource:
    - id (BIGINT, PK)
    - resource_conf_id (BIGINT)
    - hostname (VARCHAR)

변경 후:
  cmm_resource:
    - id (BIGINT, PK) -- 주의: core_config_prop.configuration_id와 ID 체계가 다름. 직접 조인 금지
    - resource_conf_id (BIGINT) -- JOIN 금지: 운영 DB에서 NULL
    - hostname (VARCHAR)
```

LLM이 컬럼 목록을 볼 때부터 잘못된 조인을 시도하지 않도록 사전 차단한다.

### 3.2 db profile의 구체적 JOIN SQL 가이드를 시스템 프롬프트에 전달

**핵심 원칙**: db profile의 `query_guide`에 구체적인 JOIN SQL을 명시하고, 이를 시스템 프롬프트에 그대로 전달하여 LLM이 참조하도록 한다.

**db profile(`query_guide`)에 작성하는 내용** (운영자가 관리):

```sql
-- query_guide 예시 (polestar_pg.yaml에 이미 작성됨)

[금지] entity 테이블과 config 테이블을 id로 직접 조인 금지:
  - cmm_resource.id = core_config_prop.configuration_id (X)
  - cmm_resource.resource_conf_id = core_config_prop.configuration_id (X)

[필수] 값 기반 브릿지 조인만 사용:
  -- 1단계: hostname으로 브릿지
  LEFT JOIN polestar.core_config_prop p_host
    ON p_host.name = 'Hostname' AND p_host.stringvalue_short = r.hostname

  -- 2단계: 같은 configuration_id 그룹 내 다른 속성 조인
  LEFT JOIN polestar.core_config_prop p_ostype
    ON p_ostype.configuration_id = p_host.configuration_id AND p_ostype.name = 'OSType'
```

**코드가 하는 일**: 프로필의 `query_guide` 텍스트를 시스템 프롬프트에 삽입 + id 조인 금지 지침 추가

**대상 파일**: `src/nodes/query_generator.py` (`_build_system_prompt()` 또는 시스템 프롬프트 템플릿)

```python
# 구현 예시
structure_meta = schema_info.get("_structure_meta")
if structure_meta:
    query_guide = structure_meta.get("query_guide", "")
    has_eav = any(
        p.get("type") == "eav" for p in structure_meta.get("patterns", [])
    )
    if has_eav and query_guide:
        system_prompt += (
            "\n\n## EAV 테이블 조인 규칙\n"
            "EAV 구조의 entity 테이블과 config 테이블을 조인할 때 "
            "id 컬럼으로 직접 조인하지 마세요. 두 테이블의 ID 체계가 다릅니다.\n"
            "반드시 아래 지침의 JOIN SQL 패턴을 그대로 사용하세요.\n\n"
            f"{query_guide}"
        )
```

**구현 방식**:
- 코드에서 조인 SQL을 동적으로 조합하지 않는다
- 프로필의 `query_guide`에 금지 조인과 구체적인 JOIN SQL 예시가 모두 포함되어 있으므로 이를 시스템 프롬프트에 그대로 삽입한다
- 운영자가 프로필의 `query_guide`를 수정하면 시스템 프롬프트에 자동 반영된다
- LLM은 `query_guide`에 작성된 `LEFT JOIN ... ON p_ostype.configuration_id = p_host.configuration_id AND p_ostype.name = 'OSType'` 같은 구체적 SQL을 보고 그대로 따른다

### 3.3 query_validator에 금지 조인 패턴 검증 추가

**대상 파일**: `src/nodes/query_validator.py`

생성된 SQL에서 `excluded_join_columns`에 해당하는 조인 패턴을 감지하면 오류로 반환한다:

```python
def _validate_forbidden_joins(sql: str, schema_info: dict) -> list[str]:
    """프로필에서 금지된 조인 패턴을 검출한다."""
    errors = []
    structure_meta = schema_info.get("_structure_meta", {})

    for pattern in structure_meta.get("patterns", []):
        if pattern.get("type") != "eav":
            continue

        entity_table = pattern.get("entity_table", "")
        config_table = pattern.get("config_table", "")

        # excluded_join_columns 검증
        for exc in pattern.get("excluded_join_columns", []):
            table = exc["table"]
            column = exc["column"]
            # SQL에서 해당 조인 패턴 검출 (별칭 해석 필요)
            # 예: r.resource_conf_id = p.configuration_id
            # 예: r.id = p.configuration_id
            if _detect_forbidden_join(sql, table, column, config_table):
                errors.append(
                    f"금지된 조인 감지: {table}.{column}을 {config_table}과 조인에 사용할 수 없습니다. "
                    f"사유: {exc['reason']}. "
                    f"value_joins에 정의된 값 기반 브릿지 조인을 사용하세요."
                )

        # entity_table.id = config_table.configuration_id 직접 조인 검출
        if _detect_direct_id_join(sql, entity_table, config_table):
            errors.append(
                f"금지된 조인 감지: {entity_table}.id = {config_table}.configuration_id 직접 조인은 "
                f"ID 체계가 달라 잘못된 결과를 반환합니다. "
                f"value_joins에 정의된 hostname 기반 브릿지 조인을 사용하세요."
            )

    return errors
```

검증 실패 시 → query_generator로 루프백 (기존 재시도 메커니즘 활용) + 오류 메시지에 올바른 조인 패턴을 포함하여 LLM이 수정하도록 유도.

### 3.4 multi_db_executor에도 동일 적용

**대상 파일**: `src/nodes/multi_db_executor.py`

query_generator와 동일한 시스템 프롬프트 강화 + 금지 조인 검증 로직을 적용한다.

---

## 4. 수정 대상 파일

| 파일 | 수정 내용 | 난이도 |
|------|----------|--------|
| `src/nodes/query_generator.py` | 시스템 프롬프트에 EAV 금지/필수 조인 지침 삽입 | 중간 |
| `src/nodes/query_validator.py` | `_validate_forbidden_joins()` 신규, 금지 조인 패턴 검출 | 중간 |
| `src/nodes/multi_db_executor.py` | query_generator와 동일한 지침 삽입 | 중간 |
| `src/nodes/schema_analyzer.py` (스키마 포맷팅) | excluded_join_columns → 스키마 컬럼 주석 삽입 | 낮음 |

---

## 5. 구현 단계

### Phase 1: 프롬프트 강화 (즉시 효과)

1. **query_generator 시스템 프롬프트 강화**: 금지 조인 + 필수 조인 패턴을 시스템 프롬프트에 삽입
2. **multi_db_executor 동일 적용**
3. **스키마 프롬프트에 경고 주석 삽입**: excluded_join_columns 처리

### Phase 2: 검증 + 자동 수정 (안전망)

4. **query_validator에 금지 조인 검출 추가**: `_validate_forbidden_joins()`
5. **검증 실패 시 재시도 메시지에 올바른 패턴 포함**: LLM이 재시도 시 올바른 조인을 사용하도록 유도

---

## 6. 검증 방법

| 검증 항목 | 방법 |
|-----------|------|
| 금지 조인이 프롬프트에 반영됨 | "서버 OS정보 조회" 요청 시 LLM 프롬프트에 금지/필수 조인 지침이 포함되는지 로그 확인 |
| 스키마 컬럼에 경고 주석 존재 | LLM에 전달되는 스키마에 `resource_conf_id -- JOIN 금지` 주석 확인 |
| LLM이 올바른 조인 생성 | "호스트명, OSType, OSParameter 조회" 요청 시 hostname 기반 브릿지 조인 SQL 생성 확인 |
| 잘못된 조인이 validator에서 차단됨 | `r.id = p.configuration_id` 패턴이 validator 오류로 반환되는지 확인 |
| 재시도 시 올바른 조인으로 수정됨 | validator 실패 후 재시도에서 hostname 브릿지 패턴으로 변경되는지 확인 |
| 기존 비-EAV DB에 영향 없음 | excluded_join_columns가 없는 DB에서 기존 동작 유지 확인 |

---

## 7. 기존 의사결정과의 관계

| 결정 | 관계 |
|------|------|
| D-020 (LLM 기반 범용 구조 분석) | 확장: 수동 프로필의 조인 지침을 LLM 프롬프트에 강제 적용 |
| D-003 (3중 읽기 전용 방어) | 유사 패턴: 프롬프트 + 코드 검증 + 설정의 다층 방어로 잘못된 조인 방지 |
| Plan 32 (EAV 수동 프로필) | 보완: 프로필의 value_joins/excluded_join_columns를 실제 파이프라인에서 강제 |

---

## 8. 핵심 설계 원칙

**LLM 프롬프트 지침만으로는 불충분하다.** LLM은 프롬프트를 무시할 수 있다.
따라서 D-003(3중 읽기 전용 방어)과 동일한 다층 방어 전략을 적용한다:

```
1층: 스키마 프롬프트 — 조인 금지 컬럼에 경고 주석 (사전 차단)
2층: 시스템 프롬프트 — 금지/필수 조인 패턴 명시 (생성 유도)
3층: query_validator — 금지 조인 패턴 검출 + 재시도 (사후 검증)
```

어느 한 층이 실패해도 다른 층이 잘못된 조인을 방지한다.
