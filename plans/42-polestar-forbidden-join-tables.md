# Plan 42: Polestar 불필요 테이블 JOIN 차단

> 작성일: 2026-04-02
> 상태: 계획
> 관련 결정: D-022 (RESOURCE_CONF_ID JOIN 금지), Plan 33 (3중 방어 체계)

---

## 1. 문제 정의

### 1.1 현상

Polestar DB 질의 시 LLM이 다음과 같이 불필요한 lookup 테이블에 대한 JOIN을 생성한다:

```sql
LEFT JOIN polestar.cmm_vendor cv ON cr.vendor_id = cv.vendor_id
LEFT JOIN polestar.cmm_os co ON cr.os_id = co.os_id
LEFT JOIN polestar.cmm_os_param cop ON cr.os_param_id = cop.os_param_id
```

이 테이블들(`cmm_vendor`, `cmm_os`, `cmm_os_param`)은 Polestar DB에 물리적으로 존재하지만, 실제 운영 데이터 조회에는 사용하지 않는다. 벤더, OS, OS파라미터 정보는 모두 `core_config_prop` EAV 테이블에 속성(`Vendor`, `OSType`, `OSVerson`, `OSParameter`)으로 저장되어 있다.

### 1.2 근본 원인 (3가지 복합 요인)

#### 원인 1: schema_analyzer가 불필요한 테이블을 LLM에 노출

`_llm_select_relevant_tables()` (`src/nodes/schema_analyzer.py:929-1001`)에서:
- DB의 **전체 테이블 목록**, 컬럼, FK 관계를 LLM에 제공한다.
- 프롬프트 규칙 2번: *"JOIN에 필요한 테이블(FK 관계로 연결된 테이블)도 반드시 포함하세요"*
- `cmm_resource`에 `vendor_id`, `os_id`, `os_param_id` 컬럼이 존재하고, 이름이 `cmm_vendor`, `cmm_os`, `cmm_os_param` 테이블과 FK 관계처럼 보인다.
- LLM이 이를 FK 관계로 판단하여 `relevant_tables`에 포함시킨다.

#### 원인 2: query_guide의 테이블 제한 지시가 불충분

`polestar.yaml`의 `query_guide`에 *"2개 테이블로 구성"*이라고 명시되어 있지만:
- `schema_analyzer`가 이미 `cmm_vendor`, `cmm_os`, `cmm_os_param`을 `relevant_tables`로 선택
- `query_generator`에 이 테이블들의 스키마(컬럼 목록 포함)가 전달됨
- LLM은 query_guide보다 구체적인 스키마 정보를 우선시하여 JOIN을 생성

Polestar 전용 프롬프트(`POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`)에 규칙 10번 *"사용할 수 있는 테이블은 cmm_resource, core_config_prop만 사용한다"*가 있지만, 스키마에 다른 테이블이 포함되어 있으면 LLM이 이 규칙을 무시하는 경우가 발생한다.

#### 원인 3: excluded_join_columns에 미등록

`config/db_profiles/polestar.yaml`과 `polestar_pg.yaml`에 `excluded_join_columns` 필드가 **아예 없다**:
- 현재 금지 컬럼은 `resource_conf_id ↔ configuration_id` 패턴만 코드 레벨에서 감지 (D-022)
- `vendor_id`, `os_id`, `os_param_id`는 금지 목록에 등록되어 있지 않음
- `query_validator`의 `_check_excluded_join_columns()`와 `_validate_forbidden_joins()`가 이 JOIN을 **통과시킴**

### 1.3 영향 범위

- **잘못된 결과**: lookup 테이블의 데이터가 불완전하거나 EAV 데이터와 불일치할 수 있음
- **성능 저하**: 불필요한 JOIN으로 쿼리 실행 시간 증가
- **재시도 낭비**: 실행 에러 발생 시 의미 없는 재시도

---

## 2. 현재 JOIN 관련 정책 체계

### 2.1 정책 정의 위치 (전체 맵)

```
┌─────────────────────────────────────────────────────────────────────┐
│                  JOIN 정책 정의 위치 및 역할                          │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 1. YAML 프로필 (config/db_profiles/*.yaml)                  │   │
│  │    - 데이터 계층: 어떤 JOIN이 허용/금지되는지 정의             │   │
│  │    - query_guide: 자연어로 JOIN 규칙 설명                    │   │
│  │    - excluded_join_columns: 금지 컬럼 선언                   │   │
│  │    - value_joins: 올바른 조인 패턴 정의                      │   │
│  │    - query_examples: few-shot SQL 예시                      │   │
│  │    ⚠ 현재 문제: excluded_join_columns 미설정                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                         ↓ 읽어서 프롬프트 구성                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 2. 프롬프트 계층 (src/prompts/query_generator.py)           │   │
│  │    - QUERY_GENERATOR_SYSTEM_TEMPLATE 규칙 11:                │   │
│  │      "-- JOIN 금지 주석이 붙은 컬럼은 절대 ON 절에 사용 금지"    │   │
│  │    - POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE:               │   │
│  │      규칙 2: "CMM_RESOURCE.ID와 CONFIGURATION_ID 직접 조인 금지"│   │
│  │      규칙 10: "cmm_resource, core_config_prop만 사용"         │   │
│  │    ⚠ 현재 문제: 규칙 10이 있지만 스키마에 다른 테이블 포함 시    │   │
│  │      LLM이 규칙 무시                                         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                         ↓ 스키마/가이드를 조합                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 3. 노드 계층 — 스키마 가공                                    │   │
│  │                                                             │   │
│  │  schema_analyzer (src/nodes/schema_analyzer.py)             │   │
│  │    - _llm_select_relevant_tables(): 관련 테이블 선택          │   │
│  │    - _supplement_eav_tables(): EAV 동반 테이블 보충           │   │
│  │    ⚠ 현재 문제: cmm_vendor 등을 필터링하지 않음               │   │
│  │                                                             │   │
│  │  query_generator (src/nodes/query_generator.py)             │   │
│  │    - _format_schema_for_prompt(): 스키마를 텍스트로 변환       │   │
│  │      → excluded_join_map에서 "-- JOIN 금지" 주석 추가        │   │
│  │    - _format_structure_guide(): 구조 가이드 포맷              │   │
│  │      → excluded_join_columns의 금지 컬럼 경고 추가           │   │
│  │    ⚠ 현재 문제: excluded_join_columns가 비어있어 작동 안 함    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                         ↓ 생성된 SQL 검증                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 4. 검증 계층 (src/nodes/query_validator.py)                  │   │
│  │                                                             │   │
│  │  _check_excluded_join_columns():                            │   │
│  │    - excluded_join_map에서 금지 컬럼 추출                    │   │
│  │    - ON 절에서 금지 컬럼 사용 감지 → warning                 │   │
│  │    ⚠ 현재 문제: excluded_join_columns가 비어있어 작동 안 함    │   │
│  │                                                             │   │
│  │  _validate_forbidden_joins():                               │   │
│  │    - EAV 패턴의 entity_table.id = config_table.config_id    │   │
│  │      직접 조인 감지 → error                                  │   │
│  │    - excluded_join_columns 컬럼이 config_table과의 조인에     │   │
│  │      사용되는 패턴 감지 → error                              │   │
│  │    ⚠ 현재 문제: cmm_vendor/cmm_os와의 조인은 검사 대상 아님   │   │
│  │      (config_table=core_config_prop와의 조인만 검사)          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 5. 유틸리티 (src/utils/schema_utils.py)                     │   │
│  │    - build_excluded_join_map(): YAML의 excluded_join_columns │   │
│  │      에서 {(table, column): reason} 매핑 구축                │   │
│  │    - query_generator, query_validator, multi_db_executor에서  │   │
│  │      공용으로 사용                                           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ 6. 의사결정 기록 (docs/02_decision.md)                          │   │
│  │    - D-022: resource_conf_id JOIN 금지 + hostname 브릿지     │   │
│  │    - Plan 33 보강: 3중 방어 체계 (YAML + 프롬프트 + 검증)     │   │
│  │    ⚠ vendor_id/os_id/os_param_id에 대한 결정 없음            │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 기존 3중 방어 체계 (D-022 / Plan 33)

| 층 | 위치 | 역할 | 현재 커버 범위 |
|---|------|------|-------------|
| **1층 YAML** | `config/db_profiles/polestar*.yaml` | 금지 컬럼 선언, 올바른 패턴 제시 | `resource_conf_id` ↔ `configuration_id`만 |
| **2층 프롬프트** | `src/prompts/query_generator.py` | LLM에 금지 규칙 전달 | `-- JOIN 금지` 주석 (excluded_join_columns 기반) |
| **3층 검증** | `src/nodes/query_validator.py` | 생성된 SQL 사후 검증 | `entity.id = config.config_id` + `excluded_join_columns` (config_table 대상만) |

### 2.3 현재 체계의 한계

1. **excluded_join_columns 미설정**: YAML에 필드 자체가 없어 2층·3층이 작동하지 않음
2. **검증 범위 제한**: `_validate_forbidden_joins()`는 `config_table`(core_config_prop)과의 조인만 검사. `cmm_vendor`, `cmm_os` 등 **다른 테이블과의 조인은 검사하지 않음**
3. **테이블 필터링 부재**: `schema_analyzer`가 불필요한 테이블을 `relevant_tables`에 포함하는 것을 방지하는 메커니즘이 없음

---

## 3. 수정 계획

### 3.1 수정 범위 요약

| # | 수정 대상 | 변경 내용 | 방어 층 |
|---|----------|----------|---------|
| A | `config/db_profiles/polestar.yaml` | `excluded_join_columns` 추가 + `allowed_tables` 추가 | 1층 YAML |
| B | `config/db_profiles/polestar_pg.yaml` | 동일 | 1층 YAML |
| C | `src/nodes/schema_analyzer.py` | `allowed_tables` 기반 테이블 필터링 | 스키마 단계 |
| D | `src/nodes/query_validator.py` | `_validate_forbidden_joins()` 확장: 금지 테이블 JOIN 검사 | 3층 검증 |
| E | `docs/02_decision.md` | D-028 추가 | 의사결정 기록 |

### 3.2 상세 수정 내용

#### A/B. YAML 프로필 수정 (polestar.yaml, polestar_pg.yaml)

EAV 패턴에 `excluded_join_columns`와 `allowed_tables`를 추가한다.

```yaml
patterns:
  - type: eav
    entity_table: cmm_resource
    config_table: core_config_prop
    # ... (기존 설정 유지)

    # [신규] JOIN 금지 컬럼 (기존 3중 방어 체계에 연동)
    excluded_join_columns:
      - table: cmm_resource
        column: vendor_id
        reason: "벤더 정보는 core_config_prop EAV에서 name='Vendor'로 조회. cmm_vendor 테이블 JOIN 불필요"
      - table: cmm_resource
        column: os_id
        reason: "OS 정보는 core_config_prop EAV에서 name='OSType'/'OSVerson'으로 조회. cmm_os 테이블 JOIN 불필요"
      - table: cmm_resource
        column: os_param_id
        reason: "OS 파라미터는 core_config_prop EAV에서 name='OSParameter'로 조회. cmm_os_param 테이블 JOIN 불필요"

# [신규] 허용 테이블 목록 — schema_analyzer가 이 목록에 없는 테이블을 relevant_tables에서 제외
allowed_tables:
  - cmm_resource
  - core_config_prop
```

**효과:**
- `excluded_join_columns` → 기존 3중 방어 체계(프롬프트 주석 + 구조 가이드 경고 + validator 감지)가 자동으로 작동
- `allowed_tables` → schema_analyzer에서 불필요한 테이블을 근본적으로 차단

#### C. schema_analyzer 테이블 필터링

`_llm_select_relevant_tables()` 이후, YAML 프로필의 `allowed_tables`가 설정되어 있으면 허용된 테이블만 남긴다.

**수정 위치:** `src/nodes/schema_analyzer.py`의 `schema_analyzer()` 함수, `relevant` 변수 생성 직후

```python
# 2-2. allowed_tables 필터링 (수동 프로필에 허용 테이블이 정의된 경우)
if manual_profile and "allowed_tables" in manual_profile:
    allowed = {t.lower() for t in manual_profile["allowed_tables"]}
    filtered = [t for t in relevant if t.rsplit(".", 1)[-1].lower() in allowed]
    if filtered:
        removed = set(relevant) - set(filtered)
        if removed:
            logger.info(
                "allowed_tables 필터링: 제거=%s, 유지=%s",
                removed, filtered,
            )
        relevant = filtered
```

**효과:**
- `cmm_vendor`, `cmm_os`, `cmm_os_param` 등이 `relevant_tables`에서 제거됨
- `query_generator`에 이 테이블들의 스키마가 전달되지 않음
- LLM이 존재하지 않는 테이블에 대한 JOIN을 생성할 수 없음

#### D. query_validator 확장

현재 `_validate_forbidden_joins()`는 `config_table`(core_config_prop)과의 조인만 검사한다. `excluded_join_columns`에 등록된 컬럼이 **어떤 테이블과의 조인에서든** 사용되면 에러로 감지하도록 확장한다.

**수정 위치:** `src/nodes/query_validator.py`

`_check_excluded_join_columns()` 함수의 warning을 error로 승격하는 옵션을 추가하거나, `_validate_forbidden_joins()` 패턴 2를 확장:

```python
# 패턴 3 (신규): excluded_join_columns 컬럼이 임의 테이블과의 조인에 사용
# config_table 대상이 아니더라도 차단 (cmm_vendor, cmm_os 등)
for exc in excluded_join_columns:
    exc_table = exc.get("table", "").lower()
    exc_column = exc.get("column", "").lower()
    exc_reason = exc.get("reason", "")
    if not exc_table or not exc_column:
        continue

    # 왼쪽이 excluded 컬럼 (어떤 상대 테이블이든)
    if actual_left == exc_table and col_left_lower == exc_column:
        errors.append(
            f"금지된 조인 감지: {exc_table}.{exc_column}이 JOIN ON 절에 사용되었습니다. "
            f"사유: {exc_reason}"
        )
    # 역방향
    if actual_right == exc_table and col_right_lower == exc_column:
        errors.append(
            f"금지된 조인 감지: {exc_table}.{exc_column}이 JOIN ON 절에 사용되었습니다. "
            f"사유: {exc_reason}"
        )
```

**효과:**
- `cmm_resource.vendor_id = cmm_vendor.vendor_id` 형태의 JOIN도 에러로 감지
- `query_generator`로 재시도 시 에러 메시지에 올바른 조회 방법(EAV 속성) 안내

#### E. decision.md에 D-028 추가

```markdown
## D-028. Polestar 불필요 lookup 테이블 JOIN 차단

| 항목 | 내용 |
|------|------|
| **결정일** | 2026-04-02 |
| **상태** | 확정 |
| **이전 결정** | D-022 보강 (3중 방어 체계 확장) |

### 결정

Polestar DB의 `cmm_vendor`, `cmm_os`, `cmm_os_param` 테이블은 쿼리 대상에서 제외한다.
해당 테이블의 데이터는 `core_config_prop` EAV에 속성으로 존재하므로 직접 JOIN이 불필요하다.

### 조치

1. YAML 프로필에 `excluded_join_columns` 추가 (vendor_id, os_id, os_param_id)
2. YAML 프로필에 `allowed_tables` 추가 (cmm_resource, core_config_prop만 허용)
3. schema_analyzer에 allowed_tables 기반 필터링 추가
4. query_validator의 금지 JOIN 검사 범위를 config_table 외 테이블로 확장

### 근거

- cmm_vendor, cmm_os, cmm_os_param은 레거시 lookup 테이블
- 실제 운영 데이터는 core_config_prop EAV의 Vendor, OSType, OSParameter 속성으로 관리
- LLM이 FK-like 컬럼명을 보고 불필요한 JOIN을 생성하여 잘못된 쿼리 발생
```

---

## 4. 수정 파일 목록

| # | 파일 | 변경 유형 | 내용 |
|---|------|----------|------|
| 1 | `config/db_profiles/polestar.yaml` | 수정 | `excluded_join_columns` + `allowed_tables` 추가 |
| 2 | `config/db_profiles/polestar_pg.yaml` | 수정 | 동일 |
| 3 | `src/nodes/schema_analyzer.py` | 수정 | `allowed_tables` 필터링 로직 추가 |
| 4 | `src/nodes/query_validator.py` | 수정 | `_validate_forbidden_joins()` 패턴 3 추가 |
| 5 | `docs/02_decision.md` | 수정 | D-028 추가 |

---

## 5. 수정 후 방어 체계

```
수정 전:                              수정 후:

schema_analyzer                       schema_analyzer
  LLM이 cmm_vendor 선택                 allowed_tables로 필터링 ← [신규]
  ↓                                     cmm_vendor 제거됨
query_generator                       query_generator
  스키마에 cmm_vendor 포함               cmm_vendor 스키마 없음 (근본 차단)
  LLM이 vendor_id JOIN 생성             + excluded_join_columns "-- JOIN 금지" 주석
  ↓                                     ↓
query_validator                       query_validator
  excluded 비어있음 → 통과               패턴 3: vendor_id JOIN 감지 → error ← [신규]
  ↓                                     → query_generator 재시도 (올바른 EAV 패턴 안내)
잘못된 SQL 실행됨                       올바른 EAV 쿼리 생성됨
```

---

## 6. 테스트 계획

| # | 테스트 | 검증 내용 |
|---|--------|----------|
| 1 | schema_analyzer 테이블 필터링 | allowed_tables 설정 시 cmm_vendor 등이 relevant_tables에서 제외되는지 |
| 2 | query_validator 금지 JOIN 감지 | `cmm_resource.vendor_id = cmm_vendor.vendor_id` JOIN이 에러로 감지되는지 |
| 3 | query_generator 프롬프트 주석 | vendor_id 컬럼에 "-- JOIN 금지" 주석이 추가되는지 |
| 4 | E2E: "서버 벤더 조회" | Vendor 조회 시 cmm_vendor JOIN 대신 core_config_prop EAV 패턴 사용하는지 |
| 5 | E2E: "서버 OS 종류 조회" | OSType 조회 시 cmm_os JOIN 대신 core_config_prop EAV 패턴 사용하는지 |
| 6 | allowed_tables 미설정 DB | polestar 외 DB에서는 필터링 없이 기존 동작 유지되는지 |

---

## 7. 위험 및 고려사항

- **하위 호환성**: `allowed_tables`는 선택적 필드이므로 미설정 시 기존 동작 유지
- **다른 DB 영향 없음**: polestar YAML에만 설정하므로 cloud_portal, itsm, itam은 영향 없음
- **향후 테이블 추가**: Polestar에 새 테이블이 추가되면 `allowed_tables`에 등록 필요 (명시적 관리)
- **excluded_join_columns 확장**: 향후 다른 불필요 컬럼 발견 시 YAML에 항목만 추가하면 코드 변경 불필요 (기존 D-022 설계 유지)
