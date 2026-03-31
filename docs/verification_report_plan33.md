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
