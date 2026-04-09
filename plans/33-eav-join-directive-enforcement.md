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


---

# Verification Report

# Plan 33 검증 보고서: EAV 조인 지침의 LLM 프롬프트 강제 적용

> 검증일: 2026-03-26
> 검증자: verifier agent
> 대상 Plan: `plans/33-eav-join-directive-enforcement.md`, `plans/33-resource-conf-id-join-prevention.md`

---

## 1. 테스트 결과 요약

| 테스트 범위 | 통과 | 실패 | 비고 |
|------------|------|------|------|
| `tests/test_structure_analysis.py` (Plan 33 Phase 2 포함) | 74 | 0 | 금지 조인 테스트 16건 포함 |
| `tests/test_plan31_field_mapping_fix.py` | 29 | 0 | |
| `tests/test_plan32_manual_profile.py` | 14 | 0 | |
| `tests/test_plan33_join_prevention.py` | 5 | 0 | YAML -> 프롬프트 통합 검증 |
| `tests/test_nodes/test_query_generator_excluded_join.py` | 7 | 0 | 스키마/가이드 주석 삽입 |
| `tests/test_nodes/test_query_validator_excluded_join.py` | 7 | 0 | ON 절 경고 감지 |
| `tests/test_utils/test_schema_utils.py` | 7 | 0 | `build_excluded_join_map()` |
| **전체 테스트 스위트** (e2e/live 제외) | **1027** | **6** | 실패 6건 모두 기존 결함 |

### 기존 결함 (Plan 33 무관)

| 파일 | 실패 테스트 | 원인 |
|------|-----------|------|
| `test_field_mapper_node.py` | `test_llm_fallback`, `test_no_redis_graceful_fallback` | Plan 31 Step 2.8 도입 후 mock 형식 불일치 (기존 결함) |
| `test_pipeline.py` | `test_step2_schema_analyzer`, `test_full_pipeline_end_to_end`, `test_empty_result_full_flow` | LLM mock side_effect 부족, 테이블 선택 로직 변경 (기존 결함) |
| `test_cache_manager.py` | `test_file_mode_get_schema_from_file`, `test_file_mode_save_schema` | Python 3.13 event loop 비호환 (기존 결함) |

---

## 2. 구문 검사

| 파일 | 결과 |
|------|------|
| `src/nodes/query_generator.py` | OK |
| `src/nodes/multi_db_executor.py` | OK |
| `src/nodes/query_validator.py` | OK |

---

## 3. 아키텍처 정합성 (arch-check)

```
검사 파일: 67개
총 import: 197개
허용 import: 197개
위반 (error): 0개
경고 (warning): 0개

모든 의존성이 Clean Architecture 규칙을 준수합니다.
```

### 계층 의존성 매트릭스 (요약)

| From / To | domain | config | utils | prompts | infra | app | orch | interface | entry |
|-----------|--------|--------|-------|---------|-------|-----|------|-----------|-------|
| application | 15 | 13 | 4 | 7 | 40 | - | - | - | - |
| orchestration | 1 | 1 | - | - | 2 | 15 | - | - | - |

의존 방향이 안쪽(domain)에서 바깥쪽(entry)으로만 향하며, 역방향 위반이 없다.

---

## 4. 코드 리뷰: Phase 간 연동 확인

### 4.1 Phase 1 시스템 프롬프트 지침이 LLM에 전달되는 경로

**query_generator.py 경로**:
1. `_format_structure_guide(structure_meta)` (line 27-116): `excluded_join_columns` -> "[금지 JOIN 컬럼]" 텍스트 생성, EAV 패턴 감지 시 "## EAV 테이블 조인 규칙" 앞부분 삽입
2. `_format_schema_for_prompt(schema_info)` (line 465-557): `build_excluded_join_map()` -> 컬럼 옆에 `-- JOIN 금지(reason)` 주석 추가
3. `_build_system_prompt()` (line 197-247): `QUERY_GENERATOR_SYSTEM_TEMPLATE.format(structure_guide=...)` -> 규칙 10과 함께 시스템 프롬프트에 삽입
4. `query_generator()` (line 178-182): `SystemMessage(content=system_prompt)` -> LLM에 전달

**multi_db_executor.py 경로**:
1. `_generate_sql()` (line 299-342): `structure_meta["query_guide"]` 취득, EAV 패턴이면 "## EAV 테이블 조인 규칙" 삽입 + `excluded_join_columns` "[금지]" 경고 추가
2. `_format_schema()` (line 507-548): `build_excluded_join_map()` -> `-- JOIN 금지(reason)` 주석 추가
3. `QUERY_GENERATOR_SYSTEM_TEMPLATE.format()` (line 346-351) -> 동일 프롬프트 템플릿 사용

**검증 결과**: 두 모듈 모두 3층 방어가 구현됨:
- 1층: 스키마 컬럼 옆 `-- JOIN 금지(...)` 주석 (LLM이 컬럼 목록을 읽을 때 경고)
- 2층: 시스템 프롬프트 규칙 10 + structure_guide 내 "[금지 JOIN 컬럼]" 섹션 + "## EAV 테이블 조인 규칙"
- 3층: Phase 2의 validator 검증

### 4.2 Phase 2 validator의 `excluded_join_columns` 참조 검증

`_validate_forbidden_joins()` (query_validator.py line 384-531):
- `schema_info.get("_structure_meta")` -> `patterns` -> EAV 패턴 필터링
- 각 EAV 패턴에서 `entity_table`, `config_table`, `excluded_join_columns` 추출
- 스키마 접두사 제거 (`.rsplit(".", 1)[1]`) 처리
- `_extract_alias_map(sql)` 로 SQL 별칭 해석
- ON 절에서 `X.col = Y.col` 패턴 추출
- 패턴 1: `entity.id = config.configuration_id` 정방향/역방향 감지
- 패턴 2: `excluded_join_columns` 정의 컬럼이 `config_table`과 조인 시 감지 (양방향)
- 에러 메시지에 hostname 기반 브릿지 조인 안내 포함

`query_validator()` (line 114-117): `_validate_forbidden_joins()` 호출 결과를 `errors` 리스트에 추가 -> 검증 실패 -> `_build_failure_result()` -> `error_message` 설정 -> `query_generator`로 루프백 재시도

**검증 결과**: `_structure_meta.patterns[*].excluded_join_columns`를 올바르게 참조하며, 양방향 감지 + 별칭 해석이 정상 동작한다.

### 4.3 Phase 간 충돌 여부

| Phase | 수정 파일 | 수정 내용 |
|-------|----------|----------|
| Phase 1 | `query_generator.py` | `_format_structure_guide()`: EAV 조인 규칙 삽입, `_format_schema_for_prompt()`: JOIN 금지 주석 |
| Phase 1 | `multi_db_executor.py` | `_generate_sql()`: EAV 조인 규칙 삽입, `_format_schema()`: JOIN 금지 주석 |
| Phase 2 | `query_validator.py` | `_validate_forbidden_joins()` 신규 함수 + `query_validator()`에서 호출 |

**충돌 없음**: Phase 1은 프롬프트 생성 (query_generator, multi_db_executor), Phase 2는 검증 (query_validator). 수정 파일이 겹치지 않고, 데이터 흐름이 `프롬프트 생성 -> LLM SQL 생성 -> 검증`으로 순차적이다.

### 4.4 polestar_pg.yaml의 excluded_join_columns 처리 확인

**YAML 설정** (config/db_profiles/polestar_pg.yaml line 37-40):
```yaml
excluded_join_columns:
  - table: cmm_resource
    column: resource_conf_id
    reason: "운영 DB에서 NULL. core_config_prop.configuration_id와 매핑되지 않음"
```

**처리 경로**:
1. `_load_manual_profile("polestar_pg")` -> YAML dict 로드 -> `structure_meta["patterns"][0]["excluded_join_columns"]`에 포함
2. `schema_dict["_structure_meta"] = structure_meta` (schema_analyzer.py line 782)
3. `build_excluded_join_map(schema_info)` -> `{("cmm_resource", "resource_conf_id"): "운영 DB에서 NULL..."}`
4. `_format_schema_for_prompt()`: `resource_conf_id: BIGINT -- JOIN 금지(운영 DB에서 NULL...)`
5. `_format_structure_guide()`: `[금지 JOIN 컬럼] cmm_resource.resource_conf_id: 운영 DB에서 NULL...`
6. `_validate_forbidden_joins()`: resource_conf_id가 config_table과의 조인에 사용되면 에러 반환

**test_plan33_join_prevention.py**에서 실제 YAML 파일 기반으로 통합 검증 통과 확인됨.

---

## 5. 보안 및 품질 리뷰

### 보안

| 항목 | 결과 | 비고 |
|------|------|------|
| SELECT 외 SQL 차단 | 유지 | `_validate_sql_simple()` + `query_validator()` 기존 로직 |
| 금지 조인 차단 | 신규 추가 | `_validate_forbidden_joins()` 에러 → 재시도 유도 |
| SQL 인젝션 방지 | 유지 | `SQLGuard` 기존 로직 |

### 코드 품질

| 항목 | 결과 |
|------|------|
| 타입 힌트 | 모든 함수에 타입 힌트 사용 (`-> list[str]`, `Optional[dict]` 등) |
| Docstring | 모든 함수에 Google-style docstring 작성 |
| 에러 처리 | `_validate_forbidden_joins()`: `_structure_meta` 없으면 빈 리스트 반환 (graceful) |
| 코드 중복 | `_get_eav_pattern()`, `_extract_eav_tables()`가 query_generator와 multi_db_executor에 중복 존재 (Minor) |

---

## 6. 발견 이슈 목록

| 심각도 | 이슈 | 위치 | 설명 |
|--------|------|------|------|
| **Minor** | 함수 중복 | `query_generator.py`, `multi_db_executor.py` | `_get_eav_pattern()`, `_extract_eav_tables()`가 두 모듈에 동일하게 정의됨. `src/utils/schema_utils.py`로 통합 권장. |
| **Minor** | 기존 테스트 실패 6건 | `test_field_mapper_node.py`, `test_pipeline.py`, `test_cache_manager.py` | Plan 31/Python 3.13 관련 기존 결함. Plan 33과 무관하나 수정 필요. |
| **Minor** | multi_db_executor의 EAV 조인 규칙 삽입이 query_generator와 미세 차이 | `multi_db_executor.py:298-342` vs `query_generator.py:27-116` | query_generator는 `_format_structure_guide()` 함수를 사용하지만, multi_db_executor는 인라인으로 동일 로직을 구현. 향후 `_format_structure_guide()` 재사용으로 통합 권장. |

---

## 7. 최종 판정

**Plan 33 구현은 검증을 통과하였다.**

- 구문 검사: 3개 파일 모두 OK
- 아키텍처: 위반 0건, 경고 0건
- 테스트: Plan 33 관련 테스트 143건 (직접 관련 16건 포함) 전체 통과
- Phase 간 충돌: 없음 (수정 파일 겹치지 않음)
- 데이터 흐름: polestar_pg.yaml -> schema_info -> 프롬프트 3층 방어 + validator 검증까지 정상 연결
- 기존 테스트 회귀: Plan 33 변경으로 인한 신규 회귀 없음 (실패 6건 모두 기존 결함)
