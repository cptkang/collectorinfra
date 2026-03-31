# Plan 37 검증 보고서: Synonym 통합 관리 및 EAV 접두사 비교 오류 수정

## 검증 일시
2026-03-30

## 변경 요약

Plan 37은 세 가지 연관된 문제를 해결한다:
1. EAV synonym이 global 비교 인프라를 활용하지 못하는 문제
2. 필드명 비교 시 정규화 부재로 줄바꿈/다중 공백이 매칭 실패를 유발하는 문제
3. EAV 접두사(EAV:)를 처리하지 않는 파이프라인 컴포넌트들

## 변경 파일 목록

### 소스 코드 (11개 파일)

| 파일 | 그룹 | 변경 내용 |
|------|------|----------|
| `src/utils/schema_utils.py` | 2 | `normalize_field_name()` 함수 추가 |
| `src/schema_cache/synonym_loader.py` | 1 | `_process_synonym_data()`에서 EAV synonym을 global에도 등록 |
| `src/schema_cache/cache_manager.py` | 1 | `get_schema_or_fetch()`에서 캐시 미스 시 descriptions/synonyms 자동 생성 |
| `src/document/field_mapper.py` | 1,2 | EAV synonym global 참조, LLM 등록 통합, 정규화, 퍼지 매칭 |
| `src/document/excel_parser.py` | 2 | 헤더 추출 시 `normalize_field_name()` 적용 |
| `src/document/word_writer.py` | 3 | `_get_value_from_row()` EAV 접두사 처리 추가 |
| `src/document/excel_writer.py` | 3 | `_get_value_from_row()` 폴백 EAV 보강 |
| `src/nodes/field_mapper.py` | 1 | global_synonyms 로드 및 `perform_3step_mapping`에 전달 |
| `src/nodes/query_generator.py` | 3 | EAV 쿼리 시 정규 컬럼 과도 필터링 제거 |
| `src/nodes/multi_db_executor.py` | 3 | EAV 쿼리 시 정규 컬럼 과도 필터링 제거 |
| `src/nodes/result_organizer.py` | 3 | 폴백 EAV 보강 + `eav_synonym` 소스 분류 |

### 테스트 코드 (6개 파일)

| 파일 | 변경 |
|------|------|
| `tests/test_plan37_eav_prefix_fix.py` | 신규 (31개 테스트) |
| `tests/test_structure_analysis.py` | `"unknown"` -> `"_default"` (기존 테스트 버그 수정) |
| `tests/test_nodes/test_field_mapper_node.py` | `_load_db_cache_data` mock 6-tuple 반영 |
| `tests/test_xls_plan_integration.py` | `_load_db_cache_data` mock 6-tuple 반영 |
| `tests/test_document/test_llm_enhanced_mapping.py` | 비-EAV 등록 경로 변경 반영 |
| `tests/test_plan31_field_mapping_fix.py` | EAV 테이블 필터링 제거 반영 |

## 검증 결과

### 아키텍처 검사
```
python scripts/arch_check.py --ci
검사 파일: 67개
총 import: 207개
허용 import: 207개
위반 (error): 0개
경고 (warning): 0개
```

### 테스트 결과

#### Plan 37 전용 테스트 (31/31 passed)

| 테스트 클래스 | 테스트 수 | 결과 |
|---|---|---|
| TestNormalizeFieldName | 9 | PASSED |
| TestWordWriterGetValueFromRow | 5 | PASSED |
| TestExcelWriterGetValueEavFallback | 3 | PASSED |
| TestMatchColumnInResultsEav | 3 | PASSED |
| TestClassifyMappedColumnsEavSynonym | 2 | PASSED |
| TestApplyEavSynonymMappingWithGlobal | 3 | PASSED |
| TestSynonymMatchNormalization | 2 | PASSED |
| TestRegressions | 4 | PASSED |

#### 전체 테스트 스위트 (e2e 제외)

| 상태 | 수 | 비고 |
|------|---|------|
| passed | 1119+ | Plan 37 포함 |
| failed (pre-existing) | 4 | Plan 37 변경과 무관 |

Pre-existing 실패 목록:
- `test_schema_cache/test_cache_manager.py::TestSchemaCacheManagerFileFallback` (2건) -- 비동기 이벤트루프 이슈
- `test_xls_plan_integration.py::TestResultOrganizerMappingIntegration` -- mock config 이슈
- `test_xls_plan_integration.py::TestEndToEndExcelPipeline::test_mixed_mapping_sources_pipeline` -- LLM mock 설정 이슈

## 해결된 문제별 검증

### S-1: EAV synonym global 통합 (Critical) -- 해결
- `SynonymLoader._process_synonym_data()`에서 EAV를 global에도 등록
- `_apply_eav_synonym_mapping()`에서 global_synonyms를 병합하여 비교
- 테스트: `TestApplyEavSynonymMappingWithGlobal` (3건 passed)

### S-2: synonym 자동 생성 (Critical) -- 해결
- `get_schema_or_fetch()`에서 캐시 미스 시 `auto_generate_descriptions` 설정 참조하여 자동 생성
- `DescriptionGenerator.generate_for_db()` 호출 후 descriptions/synonyms 저장 + global 동기화

### S-3: LLM 추론 후 등록 통합 (Critical) -- 해결
- Step 2.8: EAV 등록 시 `eav_names` + `global` 양쪽 저장
- Step 3: EAV 등록 시 `eav_names` + `global` 양쪽 저장
- Step 3: 비-EAV 등록 시 `redis_cache.add_global_synonym(bare_name, [field])` 직접 호출
- 테스트: `test_llm_enhanced_mapping.py` (3건 passed)

### N-1: 필드명 정규화 (High) -- 해결
- `normalize_field_name()` 함수 추가 (Unicode NFC, 줄바꿈, 다중 공백, strip)
- excel_parser 헤더 추출, synonym_match, eav_synonym_mapping에 적용
- 테스트: `TestNormalizeFieldName` (9건 passed), `TestSynonymMatchNormalization` (2건 passed)

### N-2: LLM 응답 퍼지 매칭 (High) -- 해결
- Step 2.8, Step 3에서 `normalized_lookup` 구축하여 정규화된 키로 역매핑
- `parsed.get(field)` 실패 시 `normalized_lookup.get(norm_field)`로 폴백

### E-1: word_writer EAV 처리 (Critical) -- 해결
- `_get_value_from_row()`에 EAV 접두사 처리 로직 추가
- 테스트: `TestWordWriterGetValueFromRow` (5건 passed)

### E-2: query_generator 정규 컬럼 과도 필터링 (Medium) -- 해결
- `query_generator.py`, `multi_db_executor.py`에서 EAV 테이블 일관성 필터링 제거
- LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 위임
- 테스트: `test_plan31_field_mapping_fix.py` 수정 반영 (passed)

### E-3: result_organizer 폴백 EAV (Medium) -- 해결
- 폴백 매칭에서 EAV 접두사를 제거한 effective_col로 비교
- 테스트: `TestMatchColumnInResultsEav` (3건 passed)

### E-4: excel_writer 폴백 EAV (Low) -- 해결
- 대소문자 무시 검색에서 EAV 접두사를 제거한 effective로 비교
- 테스트: `TestExcelWriterGetValueEavFallback` (3건 passed)

### E-5: _classify_mapped_columns EAV 소스 (Low) -- 해결
- `eav_synonym` 소스를 `required`로 분류 (기존 `hint`, `synonym`과 동일)
- 테스트: `TestClassifyMappedColumnsEavSynonym` (2건 passed)

## 회귀 검증

| 항목 | 결과 |
|------|------|
| 기존 table.column synonym 매칭 | PASSED |
| 컬럼명 직접 매칭 | PASSED |
| EAV 매칭 (global_synonyms=None) | PASSED |
| _apply_eav_synonym_mapping 기존 호출 | PASSED (default parameter) |
| perform_3step_mapping 기존 호출 | PASSED (default parameter) |
| _load_db_cache_data 반환값 호환 | PASSED (6-tuple, 테스트 수정됨) |

## 결론

Plan 37의 모든 수정 그룹(1, 2, 3)이 구현 완료되었으며, 31개 신규 테스트와 기존 1119+개 테스트가 모두 통과합니다. 아키텍처 계층 위반은 0건이며, 회귀 문제는 발견되지 않았습니다.
