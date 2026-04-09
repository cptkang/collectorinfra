# Plan 34: Polestar 도메인별 쿼리 생성 시스템 프롬프트 적용

## 목표

Polestar DB 도메인에 특화된 시스템 프롬프트를 정의하고, `.env`에 설정된 Polestar 전용 DB명과 `active_db_id`가 일치할 때 이 전용 프롬프트로 전환되도록 한다.

## 배경

현재 `QUERY_GENERATOR_SYSTEM_TEMPLATE`은 범용 프롬프트로, 모든 도메인에 동일하게 적용된다.
Polestar DB는 EAV 패턴, FK 부재 값 기반 조인, LOB 분기 등 특수한 규칙이 많아
범용 프롬프트 + `structure_guide` 주입만으로는 LLM이 규칙을 위반하는 쿼리를 생성할 확률이 높다.

사용자가 제공한 Polestar 전용 프롬프트는 다음을 명시적으로 금지/강제한다:
1. **Hallucination 금지**: 제공되지 않은 테이블/컬럼/내장함수 임의 생성 금지
2. **Core Tables**: CMM_RESOURCE, CORE_CONFIG_PROP 2개 테이블과 주요 컬럼 명시
3. **Join Relation**: `HOSTNAME = STRINGVALUE_SHORT` (WHERE `NAME='Hostname'`) 조인 필수
4. **EAV Pivot & LOB**: `MAX(CASE WHEN ... END)` 피벗 + IS_LOB 분기 강제
5. **Output Format**: db_engine_hint 참고하여 해당 DB 호환 SQL만 출력

### 설정 기반 전용 프롬프트 적용

코드에 `"polestar"`를 하드코딩하는 대신, `.env`의 `POLESTAR_DB_ID` 환경변수로 전용 프롬프트를 적용할 DB를 지정한다.
이를 통해 운영 환경에서 DB 소스명이 변경되더라도(예: `polestar_pg`, `polestar_prod`) 코드 수정 없이 `.env`만 변경하면 된다.

## 현재 구조 분석

### 프롬프트 흐름

```
src/prompts/query_generator.py
  └─ QUERY_GENERATOR_SYSTEM_TEMPLATE  (범용 템플릿)

src/nodes/query_generator.py
  └─ _build_system_prompt()
       ├─ schema_text = _format_schema_for_prompt(schema_info, ...)
       ├─ structure_guide = _format_structure_guide(structure_meta, ...)
       ├─ db_engine_hint = ...
       └─ QUERY_GENERATOR_SYSTEM_TEMPLATE.format(schema=..., structure_guide=..., ...)
```

### 도메인 정보 접근 경로

- `state["active_db_id"]` → `"polestar"` (라우팅 결과)
- `state["active_db_engine"]` → `"db2"` (domain_config에서)
- `schema_info["_structure_meta"]` → 구조 분석 결과 (EAV 패턴, query_guide 등)

## 구현 계획

### Phase 1: `.env` 설정 추가 — Polestar 전용 DB 식별자

**파일**: `src/config.py` — `AppConfig`에 필드 추가

```python
class AppConfig(BaseSettings):
    ...
    # Polestar 전용 프롬프트를 적용할 DB ID
    # .env에서 POLESTAR_DB_ID=polestar 로 설정하면
    # active_db_id가 이 값과 일치할 때 Polestar 전용 시스템 프롬프트를 사용한다.
    # 비어있으면 전용 프롬프트를 사용하지 않음 (범용 프롬프트 적용).
    polestar_db_id: str = ""
```

**파일**: `.env.example` — 시멘틱 라우팅 섹션에 추가

```env
# === Polestar 전용 프롬프트 ===
# Polestar EAV 구조 전용 시스템 프롬프트를 적용할 DB ID
# ACTIVE_DB_IDS에 포함된 DB 중 하나를 지정 (예: polestar, polestar_pg)
# 비어있으면 모든 DB에 범용 프롬프트 적용
POLESTAR_DB_ID=polestar
```

### Phase 2: Polestar 전용 시스템 프롬프트 정의

**파일**: `src/prompts/query_generator.py`

기존 `QUERY_GENERATOR_SYSTEM_TEMPLATE` 아래에 `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`을 추가한다.

```python
POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE = """Role: 당신은 POLESTAR 인프라 모니터링 DB 쿼리 생성 전문가이다.
지시사항: 주어진 스키마 규칙을 엄격히 준수하여 SQL을 작성하라. 제공되지 않은 테이블, 컬럼, 내장 함수를 임의로 추측하거나 생성(Hallucination)하는 것을 엄격히 금지한다. 사용자의 요청이 모호하거나 스키마 범위를 벗어나는 경우, 쿼리를 생성하지 말고 추가 맥락을 요청하라.

[Database Context & Rules]

1. Core Tables:
- CMM_RESOURCE: 서버 및 리소스의 계층 구조 정보를 담고 있다. (주요 컬럼: ID, HOSTNAME, DTYPE, RESOURCE_TYPE, PARENT_RESOURCE_ID)
- CORE_CONFIG_PROP: 리소스의 설정 정보를 EAV(Entity-Attribute-Value) 형태로 저장한다. (주요 컬럼: CONFIGURATION_ID, NAME, STRINGVALUE_SHORT, STRINGVALUE, IS_LOB)

2. Join Relation (CRITICAL):
- 명시적인 FK는 존재하지 않는다.
- 서버 리소스와 설정 정보를 조인할 때는 반드시 다음 조건을 따른다:
  `CMM_RESOURCE.HOSTNAME = CORE_CONFIG_PROP.STRINGVALUE_SHORT`
  (단, `CORE_CONFIG_PROP.NAME = 'Hostname'` 조건이 필수적으로 동반되어야 함)

3. EAV Pivot & LOB Handling Rules:
- 여러 설정값(예: IPaddress, OSType, OSParameter 등)을 단일 행으로 조회하려면 조인된 `CONFIGURATION_ID`를 기준으로 `MAX(CASE WHEN ... END)` 방식의 피벗 연산을 수행한다.
- 데이터 값 추출 시, `IS_LOB = 1`이면 대용량 텍스트 컬럼인 `STRINGVALUE`를, `IS_LOB = 0`이면 `STRINGVALUE_SHORT`(VARCHAR)를 사용하도록 `CASE`문 분기 처리를 포함해야 한다.

4. Output Format:
- {db_engine_hint}
- 실행 가능한 표준 해당 DB 호환 SQL만 코드 블록으로 출력한다.

## DB 스키마

{schema}

{structure_guide}

## 행 제한

- PostgreSQL/MySQL: `LIMIT {default_limit}`
- DB2: `FETCH FIRST {default_limit} ROWS ONLY`
사용자가 특정 개수를 지정하면 그 값을 사용한다.

## 추가 규칙

1. **SELECT 문만 생성한다.** INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE 등은 절대 금지이다.
2. **테이블/컬럼명은 위 스키마에 존재하는 것만 사용한다.** 스키마에 표시된 테이블명을 그대로 사용하라. (스키마 접두사 포함)
3. 필요 시 JOIN, GROUP BY, ORDER BY, 집계 함수(COUNT, AVG, SUM, MAX, MIN)를 활용한다.
4. 시간 범위 필터가 있으면 timestamp 컬럼에 WHERE 조건을 적용한다.
5. 쿼리에 주석(-- 설명)을 포함하여 쿼리의 목적을 설명한다.
6. 테이블 별칭(alias)을 사용하여 가독성을 높인다.
7. 양식-DB 매핑이 제공된 경우, 매핑된 모든 컬럼을 SELECT에 포함하고 "테이블명.컬럼명" 형태의 alias를 부여한다.
8. **스키마에 "-- JOIN 금지" 주석이 붙은 컬럼은 절대 JOIN 조건(ON 절)에 사용하지 않는다.**

## 출력 형식

SQL 쿼리만 ```sql 코드블록으로 출력하라. 추가 설명은 불필요하다.

```sql
-- 쿼리 설명
SELECT ...
FROM ...
LIMIT ... ;  -- 또는 FETCH FIRST ... ROWS ONLY (DB2)
```
"""
```

**설계 포인트**:
- 범용 프롬프트와 동일한 포맷 변수(`{schema}`, `{structure_guide}`, `{default_limit}`, `{db_engine_hint}`)를 유지하여 기존 `_build_system_prompt()` 호환
- Polestar 특유의 규칙(FK 부재 조인, EAV 피벗, LOB 분기)을 시스템 프롬프트 최상단에 배치하여 LLM 주의력 극대화
- Hallucination 금지 지시를 Role 설명에 포함

### Phase 3: 프롬프트 선택 로직 추가 (설정 기반)

**파일**: `src/nodes/query_generator.py`

`_build_system_prompt()`에 `active_db_id`와 `polestar_db_id` 파라미터를 추가하고, `.env` 설정값과 비교하여 템플릿을 선택한다.

```python
# 변경 1: import 추가
from src.prompts.query_generator import (
    QUERY_GENERATOR_SYSTEM_TEMPLATE,
    POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE,
)

# 변경 2: _build_system_prompt() 시그니처 확장
def _build_system_prompt(
    schema_info: dict,
    default_limit: int,
    ...,
    active_db_id: str | None = None,       # ← 추가
    polestar_db_id: str | None = None,     # ← 추가 (.env의 POLESTAR_DB_ID)
    active_db_engine: str | None = None,
) -> str:
    ...
    # .env에서 설정된 polestar_db_id와 active_db_id가 일치하면 전용 프롬프트 사용
    if polestar_db_id and active_db_id == polestar_db_id:
        template = POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
    else:
        template = QUERY_GENERATOR_SYSTEM_TEMPLATE

    return template.format(
        schema=schema_text,
        default_limit=default_limit,
        structure_guide=structure_guide,
        db_engine_hint=db_engine_hint,
    )
```

```python
# 변경 3: query_generator() 함수에서 active_db_id, polestar_db_id 전달
system_prompt = _build_system_prompt(
    schema_info=state["schema_info"],
    default_limit=app_config.query.default_limit,
    ...,
    active_db_id=state.get("active_db_id"),            # ← 추가
    polestar_db_id=app_config.polestar_db_id or None,  # ← 추가
    active_db_engine=state.get("active_db_engine"),
)
```

**동작 흐름**:
```
.env:  POLESTAR_DB_ID=polestar
                  ↓
AppConfig.polestar_db_id = "polestar"
                  ↓
query_generator() → _build_system_prompt(polestar_db_id="polestar")
                  ↓
state["active_db_id"] == "polestar"?
  → Yes: POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE
  → No:  QUERY_GENERATOR_SYSTEM_TEMPLATE (범용)
```

### Phase 4: 테스트

**파일**: `tests/test_nodes/test_query_generator_polestar_prompt.py`

1. `POLESTAR_DB_ID=polestar` + `active_db_id="polestar"` → Polestar 전용 프롬프트 사용 확인
2. `POLESTAR_DB_ID=polestar` + `active_db_id="cloud_portal"` → 범용 프롬프트 사용 확인
3. `POLESTAR_DB_ID=""` (미설정) + `active_db_id="polestar"` → 범용 프롬프트 사용 확인
4. `POLESTAR_DB_ID=polestar_prod` + `active_db_id="polestar_prod"` → Polestar 전용 프롬프트 사용 확인 (DB명 변경 대응)
5. Polestar 프롬프트에 핵심 규칙 키워드("Hallucination", "HOSTNAME = CORE_CONFIG_PROP.STRINGVALUE_SHORT", "IS_LOB", "MAX(CASE WHEN") 포함 여부 검증

## 변경 파일 목록

| 파일 | 변경 내용 |
|------|----------|
| `src/config.py` | `AppConfig`에 `polestar_db_id: str` 필드 추가 |
| `.env.example` | `POLESTAR_DB_ID` 환경변수 문서화 |
| `src/prompts/query_generator.py` | `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` 상수 추가 |
| `src/nodes/query_generator.py` | `_build_system_prompt()`에 설정 기반 템플릿 선택 로직 추가 |
| `tests/test_nodes/test_query_generator_polestar_prompt.py` | 설정 기반 프롬프트 선택 단위 테스트 |

## 확장성 고려

- 다른 도메인에 전용 프롬프트가 필요해지면 동일 패턴으로 `ITSM_DB_ID`, `CLOUD_PORTAL_DB_ID` 등을 `.env`에 추가하고 해당 템플릿을 정의하면 됨
- 향후 `config/db_profiles/*.yaml`에 `system_prompt_template` 필드를 추가하여 YAML에서 프롬프트 자체를 관리하는 방안도 고려 가능 (현 단계에서는 코드 내 상수 + `.env` 설정이면 충분)

## 기존 동작과의 호환성

- `POLESTAR_DB_ID`가 비어있으면(기본값) 모든 DB에 범용 프롬프트가 그대로 사용됨 → 기존 동작 변화 없음
- `_build_system_prompt()`의 기존 파라미터와 반환 형식은 변경 없음
- `{schema}`, `{structure_guide}`, `{default_limit}`, `{db_engine_hint}` 포맷 변수가 양쪽 템플릿에 동일하게 존재하므로 호출부 변경 최소화


---

# Verification Report

# Plan 34 검증 보고서: Polestar 도메인별 쿼리 생성 시스템 프롬프트

- 검증일: 2026-03-27
- 검증 대상: Plan 34 (Phase 4 테스트)
- 검증자: verifier agent

---

## 1. 테스트 결과 요약

| 항목 | 결과 |
|------|------|
| Plan 34 전용 테스트 | **13 passed / 0 failed** |
| 기존 query_generator 테스트 (회귀 검증) | **23 passed / 0 failed** |
| 총 query_generator 관련 테스트 | **36 passed / 0 failed** |
| 테스트 실행 시간 | 0.15s |

### 테스트 파일

- **신규**: `tests/test_nodes/test_query_generator_polestar_prompt.py` (13개 테스트)
- **기존**: `tests/test_nodes/test_query_generator.py` (13개 테스트)
- **기존**: `tests/test_nodes/test_query_generator_mapping.py` (4개 테스트)
- **기존**: `tests/test_nodes/test_query_generator_excluded_join.py` (6개 테스트)

### 테스트 케이스 상세

| # | 클래스 | 테스트명 | 검증 항목 | 결과 |
|---|--------|----------|-----------|------|
| 1 | TestPolestarPromptSelection | test_polestar_db_id_matches_active_db_id | polestar_db_id=polestar + active_db_id=polestar -> Polestar 전용 프롬프트 | PASSED |
| 2 | TestPolestarPromptSelection | test_polestar_db_id_does_not_match_active_db_id | polestar_db_id=polestar + active_db_id=cloud_portal -> 범용 프롬프트 | PASSED |
| 3 | TestPolestarPromptSelection | test_polestar_db_id_empty_uses_generic | polestar_db_id=None (미설정) + active_db_id=polestar -> 범용 프롬프트 | PASSED |
| 4 | TestPolestarPromptSelection | test_polestar_db_id_renamed_matches | polestar_db_id=polestar_prod + active_db_id=polestar_prod -> Polestar 전용 | PASSED |
| 5 | TestPolestarPromptContent | test_contains_hallucination_prohibition | "Hallucination" 키워드 포함 | PASSED |
| 6 | TestPolestarPromptContent | test_contains_join_relation | "CMM_RESOURCE.HOSTNAME = CORE_CONFIG_PROP.STRINGVALUE_SHORT" 포함 | PASSED |
| 7 | TestPolestarPromptContent | test_contains_is_lob_handling | "IS_LOB" 키워드 포함 | PASSED |
| 8 | TestPolestarPromptContent | test_contains_eav_pivot_pattern | "MAX(CASE WHEN" 키워드 포함 | PASSED |
| 9 | TestPolestarPromptContent | test_contains_format_variables | {schema}, {structure_guide}, {default_limit}, {db_engine_hint} 포맷 변수 존재 | PASSED |
| 10 | TestPolestarPromptFormatting | test_format_with_schema_and_limit | 테이블 포함 schema_info + DB 엔진 힌트 정상 포맷 | PASSED |
| 11 | TestPolestarPromptFormatting | test_format_with_structure_guide | _structure_meta -> structure_guide 삽입 검증 | PASSED |
| 12 | TestPolestarPromptFormatting | test_polestar_prompt_none_active_db_id | active_db_id=None -> 범용 프롬프트 | PASSED |
| 13 | TestPolestarPromptFormatting | test_both_none_uses_generic | 양쪽 None -> 범용 프롬프트 | PASSED |

---

## 2. 아키텍처 정합성 (arch-check)

```
검사 파일: 67개
총 import: 197개
허용 import: 197개
위반 (error): 0개
경고 (warning): 0개
```

**결론**: 모든 의존성이 Clean Architecture 규칙을 준수한다.

### 의존성 매트릭스 (발췌)

| From \ To | domain | config | utils | prompts | infrastructure | application |
|-----------|--------|--------|-------|---------|---------------|-------------|
| application | 15 | 13 | 4 | 7 | 40 | - |

- `application -> prompts` (7건): `_build_system_prompt()`에서 `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE` import 포함. 허용된 의존성 방향 (application -> prompts).
- 신규 import `POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE`는 기존 `QUERY_GENERATOR_SYSTEM_TEMPLATE`과 동일 계층 (prompts)에 위치하므로 아키텍처 위반 없음.

---

## 3. 코드 리뷰

### 변경 파일별 검토

#### `src/config.py` (AppConfig.polestar_db_id 필드 추가)

- **타입 안전성**: `str` 타입, 기본값 `""` -- 정상
- **호환성**: 기존 `.env` 파일에 `POLESTAR_DB_ID`가 없어도 빈 문자열로 동작하므로 기존 환경에 영향 없음
- **pydantic-settings 파싱**: `str` 필드는 단순 문자열이므로 JSON 형식 불필요 (Known Mistakes의 list[str] 이슈와 무관)

#### `.env.example` (POLESTAR_DB_ID 환경변수 추가)

- 시멘틱 라우팅 섹션 이후에 적절히 배치됨
- 주석으로 용도와 설정 방법이 명확히 문서화됨

#### `src/prompts/query_generator.py` (POLESTAR_QUERY_GENERATOR_SYSTEM_TEMPLATE 상수)

- **포맷 변수 호환**: `{schema}`, `{structure_guide}`, `{default_limit}`, `{db_engine_hint}` 4개 변수가 범용 템플릿과 동일하게 포함됨
- **핵심 규칙 포함**: Hallucination 금지, 조인 조건, EAV 피벗, IS_LOB 분기 -- 모두 포함 확인
- **SELECT 전용**: "INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE 등은 절대 금지" 명시

#### `src/nodes/query_generator.py` (_build_system_prompt 시그니처 확장)

- **파라미터 추가**: `active_db_id: str | None = None`, `polestar_db_id: str | None = None` -- 선택 파라미터로 하위 호환성 유지
- **선택 로직**: `if polestar_db_id and active_db_id == polestar_db_id:` -- 빈 문자열/None 모두 정상 처리
- **호출부**: `query_generator()` 함수에서 `app_config.polestar_db_id or None`으로 변환하여 빈 문자열 -> None 전환

### 보안 검토

- SQL 인젝션 위험 없음: 프롬프트 선택 로직은 `.env`의 정적 문자열 비교만 수행
- 민감 데이터 노출 없음: 프롬프트 텍스트에 자격 증명 미포함

---

## 4. 발견 이슈 목록

**이슈 없음** -- Plan 34의 구현이 계획서 사양을 정확히 충족하며, 아키텍처 위반도 없다.

| 심각도 | 이슈 수 |
|--------|---------|
| Critical | 0 |
| Major | 0 |
| Minor | 0 |

---

## 5. 검증 결론

Plan 34의 구현은 계획서의 모든 요구사항을 충족한다:

1. **설정 기반 프롬프트 선택**: `.env`의 `POLESTAR_DB_ID`와 `active_db_id` 비교를 통해 전용/범용 프롬프트를 정확히 선택한다.
2. **하위 호환성**: `POLESTAR_DB_ID`가 미설정이면 기존 동작(범용 프롬프트)이 유지된다.
3. **DB명 변경 대응**: DB 식별자가 변경되어도 `.env`만 수정하면 코드 변경 없이 대응 가능하다.
4. **핵심 규칙 포함**: Hallucination 금지, 조인 조건, EAV 피벗, IS_LOB 분기가 Polestar 프롬프트에 명시되어 있다.
5. **아키텍처 준수**: Clean Architecture 의존성 규칙 위반 0건.
6. **기존 테스트 회귀 없음**: 기존 23개 query_generator 테스트 전체 통과.
