# Plan 31 검증 보고서: 필드 매핑 실패 원인 해결

> 검증일: 2026-03-26
> 검증 대상: Plan 31 (3개 해결 방안 구현)
> 검증자: verifier agent

---

## 1. 테스트 결과 요약

| 항목 | 결과 |
|------|------|
| Plan 31 전용 테스트 | **29/29 통과** |
| 기존 테스트 회귀 | **17건 실패** (2개 파일) |
| Python 구문 검사 | **4/4 통과** |
| 아키텍처 정합성 | **위반 0건** |

### Plan 31 전용 테스트 (29건 전체 통과)

```
tests/test_plan31_field_mapping_fix.py ..............................  29 passed
```

| 테스트 클래스 | 테스트 수 | 결과 |
|-------------|---------|------|
| TestGlobalSynonymFallback | 1 | 통과 |
| TestLlmSynonymDiscoveryPrompts | 5 | 통과 |
| TestApplyLlmSynonymDiscovery | 9 | 통과 |
| TestRegisterLlmSynonymDiscoveriesToRedis | 5 | 통과 |
| TestStep28InPerform3StepMapping | 2 | 통과 |
| TestMultiDbExecutorEavSeparation | 7 | 통과 |

### 기존 테스트 회귀 (17건 실패)

Plan 31 변경으로 인해 기존 테스트 2개 파일에서 회귀 발생:

**파일 1: `tests/test_nodes/test_field_mapper_node.py`** (8건 실패)

| 테스트 | 실패 원인 |
|--------|---------|
| TestPerform3StepMapping::test_hint_mapping_priority | `result.column_mapping` -- tuple 반환 미대응 |
| TestPerform3StepMapping::test_synonym_mapping | 동일 |
| TestPerform3StepMapping::test_llm_fallback | 동일 |
| TestPerform3StepMapping::test_priority_db | 동일 |
| TestPerform3StepMapping::test_multi_db_mapping | 동일 |
| TestPerform3StepMapping::test_unmapped_fields_are_none | 동일 |
| TestPerform3StepMapping::test_no_redis_graceful_fallback | 동일 |
| TestFieldMapperNode::test_produces_mapping | `_load_db_cache_data` mock이 3값 반환 (5값 필요) |

**파일 2: `tests/test_xls_plan_integration.py`** (9건 실패)

| 테스트 | 실패 원인 |
|--------|---------|
| TestThreeStepMappingIntegration (5건) | `result.column_mapping` -- tuple 반환 미대응 |
| TestMultiDBMapping::test_fields_mapped_to_different_dbs | 동일 |
| TestMultiDBMapping::test_field_mapper_node_produces_mapped_db_ids | `_load_db_cache_data` mock 3값 반환 |
| TestEndToEndExcelPipeline (2건) | `result.mapping_sources` -- tuple 반환 미대응 |

**원인**: `perform_3step_mapping()` 반환 타입이 `MappingResult`에서 `tuple[MappingResult, list[dict]]`로 변경되었고, `_load_db_cache_data()` 반환 값이 3개에서 5개로 확장되었으나, 기존 테스트가 업데이트되지 않음.

**수정 방법**: 기존 테스트에서 `result = await perform_3step_mapping(...)` 호출을 `result, details = await perform_3step_mapping(...)` 으로 변경하고, `_load_db_cache_data` mock의 반환값을 5-tuple로 수정.

---

## 2. 아키텍처 정합성 (arch-check)

```
python scripts/arch_check.py --verbose
```

| 항목 | 결과 |
|------|------|
| 검사 파일 | 66개 |
| 총 import | 194개 |
| 허용 import | 194개 |
| 위반 (error) | **0개** |
| 경고 (warning) | **0개** |

모든 의존성이 Clean Architecture 규칙을 준수한다. Plan 31에서 추가된 코드는 기존 계층 경계를 벗어나지 않는다:

- `src/nodes/field_mapper.py` (application) -> `src/schema_cache/cache_manager.py` (infrastructure): 허용
- `src/document/field_mapper.py` (infrastructure) -> `src/prompts/field_mapper.py` (prompts): 허용
- `src/nodes/multi_db_executor.py` (application) -> `src/nodes/query_generator.py` (application): 동일 계층

---

## 3. 코드 리뷰

### 해결 1: 글로벌 유사어 통합 로드

**파일**: `src/nodes/field_mapper.py:206`

```python
synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
```

**검증 결과**: 통과

- `load_synonyms_with_global_fallback(db_id)` 호출이 `cache_manager.py:943`의 시그니처 `(self, db_id: str, schema_dict: Optional[dict] = None)`와 일치
- `schema_dict`를 생략하면 내부에서 `await self.get_schema(db_id)`로 자동 조회
- 기존 `get_synonyms(db_id)` 대비 글로벌 유사어가 추가로 병합됨

### 해결 2+4: LLM 유사어 발견 단계 (Step 2.8)

#### 프롬프트 (`src/prompts/field_mapper.py:188-222`)

**검증 결과**: 통과

- `FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT`: 복합 필드명(슬래시/괄호) 처리 지침, DB 컬럼 우선 선택, matched_key 출력 형식 포함
- `FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT`: `{unmapped_fields}`, `{db_columns}`, `{eav_attributes}` 3개 placeholder 정의

#### Step 2.8 함수 (`src/document/field_mapper.py:484-593`)

**검증 결과**: 통과

| 계획 요구사항 | 구현 확인 |
|-------------|---------|
| DB 컬럼명만 전달 (synonym words 미전달) | line 510-522: 키만 나열, `for col_key in sorted(synonyms.keys())` |
| EAV 속성명만 전달 (synonym words 미전달) | line 527-531: `for eav_name in eav_name_synonyms` |
| LLM 호출 1회로 전체 처리 | line 548: 단일 `await llm.ainvoke(messages)` |
| mapping_sources에 "llm_synonym" 기록 | line 572, 581: `result.mapping_sources[field] = "llm_synonym"` |
| 글로벌 synonym 자동 등록 (컬럼: add_global_synonym) | line 653: `await cache_manager.add_global_synonym(bare_column_name, [field])` |
| 글로벌 synonym 자동 등록 (EAV: save_eav_name_synonyms) | line 637: `await redis_cache.save_eav_name_synonyms(current_eav)` |

#### Step 2.8 삽입 위치 (`src/document/field_mapper.py:216-226`)

**검증 결과**: 통과

```
Step 1:   힌트 매핑 (line 198)
Step 2:   Redis synonyms (line 203)
Step 2.5: EAV name synonyms (line 208)
Step 2.8: LLM 유사어 발견 (line 216) <-- NEW
Step 3:   LLM 통합 추론 (line 228)
```

올바른 위치에 삽입되었다.

#### Redis 등록 함수 (`src/document/field_mapper.py:596-676`)

**검증 결과**: 통과

- `_register_llm_synonym_discoveries_to_redis()`가 Step 2.8 발견 결과를 Redis에 등록
- 컬럼 매핑: `cache_manager.add_global_synonym(bare_column_name, [field])` -- `cache_manager.py:613`의 시그니처와 일치
- EAV 매핑: `redis_cache.save_eav_name_synonyms(current_eav)` -- `redis_cache.py:1188`의 시그니처와 일치
- `cache_manager=None` 또는 `redis_available=False` 시 graceful 스킵

### 해결 3: EAV 피벗 쿼리 분리

**파일**: `src/nodes/multi_db_executor.py:32-73, 339-398`

**검증 결과**: 통과

| 검증 항목 | 결과 |
|----------|------|
| `_get_eav_pattern()` 함수 존재 | line 32-49, `query_generator.py:207-224`와 동일 로직 |
| `_extract_eav_tables()` 함수 존재 | line 52-73, `query_generator.py:227-249`와 동일 로직 |
| regular_entries / eav_entries 분리 | line 340-348, `query_generator.py:325-333`와 동일 패턴 |
| EAV 테이블 일관성 검증 | line 351-366, `query_generator.py:336-351`와 동일 패턴 |
| CASE WHEN 피벗 가이드 생성 | line 379-398, `query_generator.py:364-383`와 동일 패턴 |

`query_generator.py`와 `multi_db_executor.py`의 EAV 분리 로직이 구조적으로 일치한다.

---

## 4. Python 구문 검사

```
OK: src/document/field_mapper.py
OK: src/prompts/field_mapper.py
OK: src/nodes/field_mapper.py
OK: src/nodes/multi_db_executor.py
```

4개 파일 모두 AST 파싱 통과.

---

## 5. 발견 이슈 목록

### [Major] 기존 테스트 회귀 - 17건 실패

**심각도**: Major
**위치**: `tests/test_nodes/test_field_mapper_node.py`, `tests/test_xls_plan_integration.py`
**설명**: Plan 31 구현으로 `perform_3step_mapping()` 반환 타입과 `_load_db_cache_data()` 반환 값 개수가 변경되었으나, 기존 테스트가 업데이트되지 않아 17건의 테스트 실패가 발생한다.

**구체적 원인**:
1. `perform_3step_mapping()` 반환: `MappingResult` -> `tuple[MappingResult, list[dict]]`
   - 영향: 기존 테스트의 `result.column_mapping` 호출이 `tuple` 객체에 대해 AttributeError 발생
2. `_load_db_cache_data()` 반환: 3-tuple -> 5-tuple (`eav_name_synonyms`, `cache_mgr` 추가)
   - 영향: mock 반환값이 3개인 테스트에서 `ValueError: not enough values to unpack` 발생

**수정 방법**:
- `result = await perform_3step_mapping(...)` -> `result, details = await perform_3step_mapping(...)`
- `mock_load.return_value = ({}, {}, [])` -> `mock_load.return_value = ({}, {}, [], {}, None)`

### [Minor] `_get_eav_pattern()`, `_extract_eav_tables()` 코드 중복

**심각도**: Minor
**위치**: `src/nodes/multi_db_executor.py:32-73`, `src/nodes/query_generator.py:207-249`
**설명**: 두 모듈에 동일한 로직의 헬퍼 함수가 중복 정의되어 있다. 향후 한 쪽만 수정 시 불일치가 발생할 수 있다.
**권장**: 공통 유틸리티 모듈(예: `src/utils/eav_helpers.py`)로 추출하거나, 한 모듈에서 임포트하는 방식으로 통합.

### [Minor] 로그 메시지 내 "llm_synonym" 카운트 추가

**심각도**: Minor (이미 구현됨, 확인 사항)
**위치**: `src/document/field_mapper.py:277`
**설명**: `perform_3step_mapping()` 완료 로그에 `LLM유사어=%d` 카운트가 추가되어 "llm_synonym" 소스를 별도로 집계한다. 올바르게 구현됨.

---

## 6. 검증 결론

### 해결 방안별 구현 상태

| 해결 방안 | 구현 완료 | 코드 정합성 | 테스트 통과 |
|----------|---------|-----------|-----------|
| 해결 1: 글로벌 유사어 통합 로드 | O | O | O |
| 해결 2+4: LLM 유사어 발견 (Step 2.8) | O | O | O |
| 해결 3: EAV 피벗 쿼리 분리 | O | O | O |

### 종합 판정

**구현 자체는 계획대로 올바르게 완료**되었다. 3개 해결 방안 모두 Plan 31의 설계 의도를 정확히 반영하고 있으며, 아키텍처 규칙도 준수한다.

다만 **기존 테스트 17건이 회귀 실패**하므로, 기존 테스트 파일의 업데이트가 필요하다. 이는 구현 코드의 문제가 아니라 테스트 코드가 변경된 반환 타입/시그니처에 맞게 갱신되지 않은 문제이다.

### 조치 필요 사항

1. **[필수]** `tests/test_nodes/test_field_mapper_node.py`의 `TestPerform3StepMapping` 클래스 7건 + `TestFieldMapperNode::test_produces_mapping` 1건 수정
2. **[필수]** `tests/test_xls_plan_integration.py`의 9건 수정
3. **[권장]** `_get_eav_pattern()`, `_extract_eav_tables()` 중복 코드 통합
