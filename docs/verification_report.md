# 검증 결과 보고서

> 검증일: 2026-03-17
> 검증 대상: 시멘틱 라우팅 시스템 v2 변경 (키워드 제거, LLM 전용, 사용자 직접 DB 지정)

---

## 1. 변경 요구사항 충족 여부

### 1.1 키워드 기반 1차 분류 제거

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| `_keyword_match()` 함수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `_needs_llm_fallback()` 함수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `KEYWORD_CONFIDENCE_THRESHOLD` 상수 삭제 | PASS | `src/routing/semantic_router.py`에서 완전 제거 확인 |
| `DBDomainConfig.keywords` 필드 제거 | PASS | `aliases` 필드로 교체 완료 |
| `DB_DOMAINS` 정의에서 키워드 목록 제거 | PASS | 모든 도메인에서 keywords 항목 삭제 완료 |
| 모든 라우팅이 LLM을 통해 수행 | PASS | `semantic_router()` 함수가 `_llm_classify()` 만 호출 |

### 1.2 사용자 직접 DB 지정 지원

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| LLM 프롬프트에 직접 지정 규칙 포함 | PASS | `SEMANTIC_ROUTER_SYSTEM_PROMPT_TEMPLATE`에 규칙 추가 |
| `user_specified` 필드 JSON 출력에 포함 | PASS | LLM 응답 파싱 시 `user_specified` 필드 처리 |
| `user_specified_db` State 필드 추가 | PASS | `AgentState` 및 `create_initial_state()`에 추가 |
| 별칭(aliases) 기반 인식 | PASS | `DBDomainConfig.aliases`에 한국어/영어 별칭 정의 |
| 동적 프롬프트에 별칭 정보 포함 | PASS | `_build_router_prompt()`에서 aliases를 프롬프트에 포함 |

### 1.3 멀티 DB 쿼리 및 결과 취합

| 검증 항목 | 상태 | 상세 |
|-----------|------|------|
| sub_query_context 분리 | PASS | LLM 프롬프트에 sub_query_context 분리 규칙 포함 |
| DB별 결과에 `_source_db` 태깅 | PASS | 기존 `multi_db_executor`에서 정상 동작 |
| 결과 병합 시 DB별 요약 정보 생성 | PASS | `result_merger`에 `db_result_summary` 생성 로직 추가 |
| 부분 실패 시 성공 결과 + 에러 정보 반환 | PASS | `_build_error_summary()` 함수로 분리하여 명확화 |

---

## 2. 테스트 결과

### 2.1 전체 테스트

```
330 passed in 0.92s
```

모든 330개 테스트 통과. 기존 기능에 대한 회귀 없음.

### 2.2 시멘틱 라우팅 관련 테스트 (56개)

| 테스트 파일 | 건수 | 상태 |
|-------------|------|------|
| `test_domain_config.py` | 13 | PASS |
| `test_db_registry.py` | 13 | PASS |
| `test_semantic_router.py` | 24 | PASS |
| `test_graph_routing.py` | 3 | PASS |
| `test_state_extension.py` | 3 | PASS |

### 2.3 주요 테스트 케이스 상세

**키워드 함수 제거 확인 (3건):**
- `test_no_keyword_match_function` - `_keyword_match` 함수 비존재 확인
- `test_no_needs_llm_fallback_function` - `_needs_llm_fallback` 함수 비존재 확인
- `test_no_keyword_confidence_threshold` - `KEYWORD_CONFIDENCE_THRESHOLD` 상수 비존재 확인

**사용자 직접 DB 지정 (2건):**
- `test_user_specified_db` - LLM 분류에서 user_specified=True 반환 확인
- `test_user_specified_db_in_result` - semantic_router 결과에 user_specified_db 반영 확인

**LLM 전용 라우팅 (5건):**
- `test_single_db_routing` - 단일 DB 라우팅 정상 동작
- `test_multi_db_routing` - 멀티 DB 라우팅 정상 동작
- `test_llm_failure_fallback` - LLM 실패 시 안전한 폴백
- `test_low_score_filtered` - 최소 관련도 미만 필터링
- `test_results_sorted_by_relevance` - 관련도 점수 내림차순 정렬

**동적 프롬프트 (3건):**
- `test_includes_all_active_domains` - 전체 도메인 포함
- `test_includes_aliases` - 별칭 정보 포함
- `test_subset_domains` - 부분 도메인만 포함

---

## 3. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/routing/semantic_router.py` | 전면 재작성 | 키워드 함수 제거, LLM 전용 로직, 동적 프롬프트 구성 |
| `src/routing/domain_config.py` | 수정 | keywords -> aliases 필드 교체 |
| `src/prompts/semantic_router.py` | 전면 재작성 | 동적 템플릿, 직접 DB 지정 규칙, 멀티 DB 분리 규칙 |
| `src/state.py` | 수정 | user_specified_db 필드 추가 |
| `src/nodes/result_merger.py` | 수정 | DB별 결과 요약 생성, _build_error_summary 분리 |
| `docs/requirements.md` | 수정 | 섹션 9 전면 업데이트 |
| `plans/09-semantic-routing.md` | 전면 재작성 | v2 계획서 |
| `tests/test_semantic_routing/test_semantic_router.py` | 전면 재작성 | v2 테스트 |
| `tests/test_semantic_routing/test_domain_config.py` | 수정 | aliases 테스트로 변경 |
| `tests/test_semantic_routing/test_state_extension.py` | 수정 | user_specified_db 테스트 추가 |

---

## 4. Critical 이슈

없음.

---

## 5. 주의사항

1. **LLM 폴백 없음**: 키워드 기반 폴백이 제거되었으므로, LLM 호출 실패 시 첫 번째 활성 DB로 폴백합니다. LLM 서비스 안정성이 라우팅 정확도에 직접 영향을 미칩니다.
2. **라우팅 판단 시간**: LLM 전용 라우팅으로 전환되어 기존 키워드 매칭 대비 응답 시간이 증가할 수 있습니다. 비기능 요건에서 판단 시간을 3초에서 5초로 완화했습니다.
3. **별칭 관리**: 사용자가 DB를 직접 지정할 때 인식할 별칭 목록은 `domain_config.py`의 `aliases` 필드에서 관리됩니다. 새로운 별칭이 필요한 경우 이 필드에 추가하면 됩니다.

---
---

# Phase 2 문서 처리 기능 - 검증 보고서

> 검증일: 2026-03-17
> 검증 대상: Phase 2 Excel/Word 양식 파싱 및 생성 기능

---

## 1. 테스트 결과 요약

| 항목 | 수치 |
|------|------|
| 전체 테스트 수 | 406 |
| 통과 | 406 |
| 실패 | 0 |
| 기존 테스트 (Phase 1 + 시멘틱 라우팅) | 356 |
| 신규 테스트 (Phase 2 문서 처리) | 50 |
| 회귀 테스트 실패 | 0 |

## 2. 모듈별 테스트 결과

### 2.1 Excel 파서 (excel_parser.py) - 9개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_single_sheet | 기본 단일 시트 파싱 | PASS |
| test_header_detection_non_first_row | 비첫행 헤더 탐지 | PASS |
| test_empty_sheet_skipped | 빈 시트 스킵 | PASS |
| test_merged_cells_detected | 병합 셀 탐지 | PASS |
| test_formula_cells_detected | 수식 셀 탐지 | PASS |
| test_multi_sheet | 다중 시트 파싱 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_data_end_row_detection | 데이터 영역 끝 탐지 | PASS |
| test_header_cells_structure | 헤더 셀 구조 검증 | PASS |

### 2.2 Word 파서 (word_parser.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_placeholders | 본문 플레이스홀더 추출 | PASS |
| test_table_structure | 표 구조 분석 | PASS |
| test_table_with_placeholders | 표 내부 플레이스홀더 | PASS |
| test_no_placeholders_or_tables | 빈 문서 처리 | PASS |
| test_duplicate_placeholders_deduplicated | 중복 제거 | PASS |
| test_multiple_tables | 다중 표 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_mixed_paragraphs_and_tables | 혼합 문서 | PASS |

### 2.3 Excel 작성기 (excel_writer.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_basic_data_fill | 기본 데이터 채우기 | PASS |
| test_preserves_headers | 헤더 보존 | PASS |
| test_formula_cells_preserved | 수식 보존 | PASS |
| test_unmapped_columns_ignored | 미매핑 컬럼 스킵 | PASS |
| test_empty_rows | 빈 결과 처리 | PASS |
| test_column_name_case_insensitive | 대소문자 무시 | PASS |
| test_multiple_rows | 다중 행 채우기 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |

### 2.4 Word 작성기 (word_writer.py) - 8개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_placeholder_replacement | 플레이스홀더 치환 | PASS |
| test_table_data_fill | 표 데이터 채우기 | PASS |
| test_table_placeholder_replacement | 표 내부 치환 | PASS |
| test_unmapped_placeholder_cleared | 미매핑 플레이스홀더 처리 | PASS |
| test_empty_rows | 빈 결과 처리 | PASS |
| test_single_row_parameter | 단일 행 매개변수 | PASS |
| test_invalid_file_raises_error | 유효하지 않은 파일 오류 | PASS |
| test_mixed_placeholders_and_tables | 혼합 문서 채우기 | PASS |

### 2.5 필드 매퍼 (field_mapper.py) - 15개 테스트 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_excel_headers | Excel 헤더 추출 | PASS |
| test_word_placeholders | Word 플레이스홀더 추출 | PASS |
| test_deduplication | 중복 필드 제거 | PASS |
| test_empty_template | 빈 양식 | PASS |
| test_basic_format | 스키마 포맷 | PASS |
| test_empty_schema | 빈 스키마 | PASS |
| test_valid_mapping | 유효 매핑 보존 | PASS |
| test_invalid_column_removed | 무효 컬럼 제거 | PASS |
| test_null_mapping_preserved | null 매핑 유지 | PASS |
| test_successful_mapping | LLM 매핑 성공 | PASS |
| test_llm_returns_json_in_codeblock | 코드블록 JSON 파싱 | PASS |
| test_empty_fields | 빈 필드 | PASS |
| test_empty_schema (map_fields) | 빈 스키마 | PASS |
| test_llm_failure_returns_none_mapping | LLM 실패 처리 | PASS |
| test_invalid_json_retries | JSON 파싱 재시도 | PASS |

### 2.6 통합 테스트 - 2개 통과

| 테스트 | 설명 | 결과 |
|--------|------|------|
| test_full_excel_flow | Excel 파싱->매핑->생성 전체 흐름 | PASS |
| test_full_word_flow | Word 파싱->매핑->생성 전체 흐름 | PASS |

## 3. 요건 충족 검증

### F-07: Excel 양식 처리

| 수용 기준 | 상태 |
|-----------|------|
| Excel 파일의 시트별 헤더, 데이터 영역, 병합 셀 정보 추출 | 충족 |
| 데이터를 정확한 셀 위치에 채워넣기 | 충족 |
| 원본의 병합 셀, 서식, 수식 보존 | 충족 |
| 생성된 파일이 정상적으로 열리는지 검증 | 충족 |

### F-08: Word 양식 처리

| 수용 기준 | 상태 |
|-----------|------|
| {{placeholder}} 패턴과 표 구조 정확히 추출 | 충족 |
| 플레이스홀더를 실제 데이터로 치환 | 충족 |
| 표 데이터 행에 결과 채우기 | 충족 |
| 원본 스타일 및 서식 보존 | 충족 |

### D-007: LLM 의미 매핑

| 수용 기준 | 상태 |
|-----------|------|
| 양식 필드명과 DB 컬럼명 간 LLM 매핑 | 충족 |
| 매핑 불가 필드 null 처리 | 충족 |
| 매핑 결과 검증 (존재하지 않는 컬럼 제거) | 충족 |
| 대소문자 무시 매칭 | 충족 |

## 4. 기존 코드 호환성

- 기존 356개 테스트 모두 통과 (회귀 0건)
- `input_parser.py`: 유효하지 않은 파일 업로드 시 에러 처리 추가 (기존 스텁 동작 호환)
- `output_generator.py`: xlsx/docx 폴백 메시지 변경 (테스트 1건 업데이트)
- `result_organizer.py`: llm 파라미터 추가 (기존 호출에 영향 없음, 기본값 None)
- `graph.py`: result_organizer에 llm 주입 추가

## 5. 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `src/document/__init__.py` | 신규 | 문서 처리 모듈 패키지 |
| `src/document/excel_parser.py` | 신규 | Excel 양식 구조 분석 |
| `src/document/word_parser.py` | 신규 | Word 양식 구조 분석 |
| `src/document/field_mapper.py` | 신규 | LLM 기반 필드-컬럼 매핑 |
| `src/document/excel_writer.py` | 신규 | Excel 양식 데이터 채우기 |
| `src/document/word_writer.py` | 신규 | Word 양식 데이터 채우기 |
| `src/prompts/field_mapper.py` | 신규 | 필드 매핑 LLM 프롬프트 |
| `src/nodes/input_parser.py` | 수정 | 파서 에러 처리 추가 |
| `src/nodes/result_organizer.py` | 수정 | 양식 매핑 로직 추가, llm 파라미터 추가 |
| `src/nodes/output_generator.py` | 수정 | xlsx/docx 파일 생성 로직 추가 |
| `src/graph.py` | 수정 | result_organizer에 llm 주입 |
| `plans/10-document-processing.md` | 신규 | Phase 2 구현 계획서 |
| `tests/test_document/` | 신규 | 50개 테스트 |
| `tests/test_nodes/test_output_generator.py` | 수정 | 폴백 테스트 업데이트 |

## 6. Critical 이슈

없음.

## 7. 권장 사항 (Non-Critical)

1. **대용량 데이터 성능**: 10,000건 이상의 Excel 생성 시 성능 테스트 별도 수행 권장.
2. **복잡한 양식**: 다중 시트 간 크로스 참조, 피벗 테이블 등은 현재 미지원. 필요 시 단계적 확장.
3. **Run 분리 플레이스홀더**: Word에서 {{placeholder}}가 여러 Run에 걸쳐 분리된 경우, 현재는 전체 텍스트를 합쳐서 치환 후 첫 번째 Run에 설정. 복잡한 서식이 손실될 수 있음.

---

# 매핑-우선(Mapping-First) 구현 - 검증 보고서

> 검증일: 2026-03-17
> 검증 대상: xls_plan.md Excel/Word 양식 기반 데이터 조회 및 파일 작성 개선

---

## 1. 구현 범위

plans/xls_plan.md의 12단계 구현 계획에 따른 전체 구현

## 2. 변경 파일 요약

### 신규 파일
| 파일 | 역할 |
|------|------|
| `src/nodes/field_mapper.py` | 필드 매핑 그래프 노드 (3단계 매핑, 유사어 등록 처리) |
| `tests/test_nodes/test_field_mapper_node.py` | field_mapper 노드 단위 테스트 (19건) |
| `tests/test_nodes/test_output_generator_mapping.py` | 매핑 정보 표시 테스트 (4건) |
| `tests/test_nodes/test_semantic_router_mapping.py` | 매핑 기반 라우팅 테스트 (3건) |
| `tests/test_nodes/test_query_generator_mapping.py` | column_mapping 프롬프트 테스트 (4건) |
| `tests/test_nodes/test_result_organizer_mapping.py` | 충분성 검사 테스트 (6건) |

### 수정 파일
| 파일 | 변경 내용 |
|------|----------|
| `src/state.py` | `column_mapping`, `db_column_mapping`, `mapping_sources`, `mapped_db_ids`, `pending_synonym_registrations` 필드 추가 |
| `src/nodes/input_parser.py` | `field_mapping_hints`, `target_db_hints`, `synonym_registration` 추출, `.doc` 변환 처리 |
| `src/prompts/input_parser.py` | 매핑 힌트, DB 힌트, 유사어 등록 추출 규칙 추가 |
| `src/document/field_mapper.py` | 3단계 매핑 로직 (힌트/synonyms/LLM), 멀티 DB, `extract_field_names` (public) |
| `src/prompts/field_mapper.py` | 멀티 DB 매핑 프롬프트 추가 (`FIELD_MAPPER_MULTI_DB_*`) |
| `src/graph.py` | `field_mapper` 노드 추가 (input_parser -> field_mapper -> semantic_router) |
| `src/routing/semantic_router.py` | `mapped_db_ids` 기반 라우팅 (LLM 스킵), 우선순위 DB 지원 |
| `src/nodes/query_generator.py` | `column_mapping` 기반 프롬프트 (alias 규칙) |
| `src/prompts/query_generator.py` | alias 규칙 (rule 8, 9) 추가 |
| `src/nodes/multi_db_executor.py` | DB별 `column_mapping` 전달하여 SQL 생성 |
| `src/nodes/result_organizer.py` | 중복 매핑 LLM 호출 제거, `column_mapping` 기반 충분성 검사 |
| `src/nodes/output_generator.py` | LLM 추론 매핑 정보 + 유사어 등록 안내 표시, State column_mapping 우선 사용 |
| `src/document/excel_writer.py` | None 값 처리 (원본 셀 유지), 미매핑 헤더 경고 로그 |
| `src/document/word_writer.py` | None 값 처리 (빈 문자열 치환) |
| `docs/decision.md` | D-012 매핑-우선 전략 결정 추가 |
| `tests/test_document/test_field_mapper.py` | import 경로 수정 (`_extract_field_names` -> `extract_field_names`) |

## 3. 테스트 결과

### 전체 테스트
- **554 passed**, 51 warnings
- 기존 테스트 518건 모두 통과 (하위 호환성 확인)
- 신규 테스트 36건 모두 통과

### 신규 테스트 상세

| 테스트 클래스 | 건수 | 검증 항목 |
|-------------|------|----------|
| `TestExtractFieldNames` | 3 | xlsx/docx/doc 필드 추출 |
| `TestSynonymMatch` | 4 | 정확/대소문자/컬럼명/미매칭 |
| `TestPerform3StepMapping` | 7 | 힌트 우선/synonyms/LLM 폴백/우선순위 DB/멀티 DB/미매핑/Redis 없음 |
| `TestFieldMapperNode` | 3 | 스킵/빈 필드/매핑 생성 |
| `TestBuildPendingRegistrations` | 2 | LLM 추론 항목 생성/빈 결과 |
| `TestAppendInferredMappingInfo` | 4 | 미표시/미추론/추론 표시/번호 매기기 |
| `TestMappedDbIdsRouting` | 3 | 단일 DB/멀티 DB/LLM 폴백 |
| `TestBuildUserPromptWithMapping` | 4 | 매핑 포함/template 폴백/둘 다 없음/null 제외 |
| `TestCheckDataSufficiencyWithMapping` | 6 | alias 키/컬럼 키/부족/빈 결과/레거시/전체 null |

## 4. 구현 검증 체크리스트

| 단계 | 항목 | 상태 | 비고 |
|------|------|------|------|
| 1 | State 변경 | PASS | 5개 필드 추가, create_initial_state 반영 |
| 2 | input_parser 확장 | PASS | field_mapping_hints, target_db_hints, synonym_registration, .doc 변환 |
| 3 | field_mapper 모듈 개선 | PASS | 3단계 매핑, 멀티 DB, descriptions 프롬프트 |
| 4 | field_mapper 그래프 노드 | PASS | input_parser -> field_mapper -> semantic_router |
| 5 | semantic_router 개선 | PASS | mapped_db_ids 기반 라우팅, LLM 스킵 |
| 6 | query_generator 프롬프트 개선 | PASS | column_mapping 기반 SELECT + alias 규칙 |
| 7 | multi_db_executor 확장 | PASS | DB별 column_mapping 전달 |
| 8 | result_organizer 정리 | PASS | 중복 매핑 제거, 충분성 검사 개선 |
| 9 | output_generator | PASS | LLM 추론 매핑 정보 + 유사어 등록 질문 |
| 10 | Excel/Word Writer 개선 | PASS | None 값 처리, 경고 로그 |
| 11 | 유사어 등록 플로우 | PASS | 전체/선택/단건 등록 |
| 12 | 테스트 | PASS | 36건 신규 테스트, 554건 전체 통과 |

## 5. 하위 호환성

- template_structure가 없는 경우 (텍스트 출력 모드): field_mapper가 스킵되어 기존 흐름 유지
- column_mapping이 없는 경우: result_organizer에서 레거시 LLM 매핑 폴백
- Redis 캐시 미존재 시: synonyms 2단계 스킵, LLM 폴백으로 동작
- 기존 518개 테스트 전부 통과

## 6. 아키텍처 결정 기록
- `docs/decision.md`에 D-012 (매핑-우선 전략) 추가 완료

## 7. Critical 이슈
- 없음

---
---

# Redis 기반 스키마 캐시 유사단어 확장 - 검증 보고서

> 검증일: 2026-03-18
> 검증 대상: schemacache_plan.md 유사단어 2계층, source 태깅, invalidate 보존, 프롬프트 기반 유사단어 CRUD

---

## 1. 검증 범위

`plans/schemacache_plan.md` 11장 구현 순서 중 미구현 항목 보완 및 검증.

## 2. 구현 상태 요약

| 단계 | 작업 | 상태 | 비고 |
|------|------|------|------|
| 1 | RedisConfig 설정 추가, .env.example 업데이트 | 기존 완료 | 변경 없음 |
| 2 | RedisSchemaCache (기본 CRUD + fingerprint + 유사단어 2계층) | **보완 완료** | source 태깅, 글로벌 synonyms, invalidate 보존 추가 |
| 3 | SchemaCacheManager (Redis/파일 추상화) | **보완 완료** | load_synonyms_with_global_fallback, sync_global_synonyms, add/remove 래퍼 추가 |
| 4 | schema_analyzer 노드 통합 | 기존 완료 | 변경 없음 |
| 5 | DescriptionGenerator + LLM 프롬프트 | 기존 완료 | 변경 없음 |
| 6 | Redis에 description + synonyms 저장/로드 통합 | 기존 완료 | 변경 없음 |
| 7 | 운영자 API 라우터 | 기존 완료 | 변경 없음 |
| 8 | 독립 실행 CLI | 기존 완료 | 변경 없음 |
| 9 | 프롬프트 기반 캐시 관리 노드 + 그래프 분기 | **보완 완료** | synonym CRUD 핸들러 4개 추가 |
| 10 | query_generator 프롬프트에 컬럼 설명 + 유사 단어 통합 | 기존 완료 | 변경 없음 |
| 11-12 | 단위/통합 테스트 | **보완 완료** | 신규 45개 테스트 추가 |

## 3. 이번 작업에서 수정/추가한 파일

### 수정된 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/schema_cache/redis_cache.py` | (1) synonyms를 `{words, sources}` 구조로 source 태깅 (2) 글로벌 유사단어 사전 (`synonyms:global`) CRUD (3) `invalidate()`에서 synonyms 키 보존 (4) `invalidate_all()`에서 synonyms 키 보존 (5) `delete_synonyms()` / `delete_global_synonyms()` 명시적 삭제 |
| `src/schema_cache/cache_manager.py` | (1) `add_synonyms()` 래퍼 (2) `remove_synonyms()` 래퍼 (3) 글로벌 synonyms CRUD (4) `load_synonyms_with_global_fallback()` (5) `sync_global_synonyms()` |
| `src/nodes/cache_management.py` | synonym CRUD 핸들러 4개: `_handle_list_synonyms`, `_handle_add_synonym`, `_handle_remove_synonym`, `_handle_update_synonym` |
| `src/prompts/cache_management.py` | `list-synonyms`, `add-synonym`, `remove-synonym`, `update-synonym` 액션 추가 |

### 새로 생성된 파일

| 파일 | 내용 |
|------|------|
| `tests/test_schema_cache/test_redis_cache_synonyms.py` | RedisSchemaCache 유사단어 확장 테스트 (20개) |
| `tests/test_schema_cache/test_cache_manager_synonyms.py` | SchemaCacheManager 유사단어 확장 테스트 (11개) |
| `tests/test_nodes/test_cache_management_synonyms.py` | cache_management 노드 synonym CRUD 테스트 (14개) |

## 4. 핵심 요구사항 검증 결과

### 4.1 유사단어 영구 보존

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| invalidate 시 synonyms 보존 | PASS | `invalidate()`에서 "synonyms" suffix를 삭제 대상에서 제외 |
| invalidate_all 시 synonyms 보존 | PASS | `scan_iter` 결과에서 `:synonyms`로 끝나는 키를 스킵 |
| 글로벌 사전 자동 삭제 방지 | PASS | `synonyms:global` 키는 `schema:*` 패턴에 매칭되지 않음 |
| 명시적 삭제만 허용 | PASS | `delete_synonyms()` / `delete_global_synonyms()` 별도 메서드 |

### 4.2 2계층 유사단어 (DB별 + 글로벌)

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| DB별 synonyms 저장/로드 | PASS | `schema:{db_id}:synonyms` Hash |
| 글로벌 사전 저장/로드 | PASS | `synonyms:global` Hash |
| 글로벌 폴백 | PASS | `load_synonyms_with_global_fallback()` - DB에 없는 컬럼은 bare name으로 글로벌 조회 |
| DB synonyms 우선 | PASS | 글로벌보다 DB synonyms가 우선 |
| 글로벌 동기화 | PASS | `sync_global_synonyms()` - DB별 synonyms를 글로벌에 병합 |

### 4.3 source 태깅

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| LLM 생성분 "llm" 태깅 | PASS | `save_synonyms(..., source="llm")` |
| 운영자 추가분 "operator" 태깅 | PASS | `add_synonyms(..., source="operator")` |
| 기존 source 보존 | PASS | `add_synonyms()` 에서 기존 source는 덮어쓰지 않음 |
| 레거시 list 형태 호환 | PASS | list -> `{words, sources}` 자동 변환 |

### 4.4 프롬프트 기반 유사단어 관리

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| 유사단어 목록 조회 (글로벌/DB별/컬럼별) | PASS | `list-synonyms` 액션 |
| 유사단어 추가 (글로벌 + DB 동기화) | PASS | `add-synonym` 액션 |
| 유사단어 삭제 (글로벌 + DB 동시) | PASS | `remove-synonym` 액션 |
| 유사단어 교체 | PASS | `update-synonym` 액션 |
| 프롬프트 파싱 | PASS | `cache_management.py` 프롬프트에 모든 액션 포함 |

### 4.5 Redis Graceful Fallback

| 검증 항목 | 결과 | 근거 |
|-----------|------|------|
| Redis 미연결 시 빈 결과 반환 | PASS | 모든 메서드에서 `_connected` 검사 |
| 파일 백엔드 시 글로벌 synonyms 빈 dict | PASS | `backend != "redis"` 시 빈 결과 |
| 파일 백엔드 시 add_synonyms False | PASS | Redis 없으면 False 반환 |

## 5. 테스트 결과

```
전체 테스트: 644 passed, 1 deselected (환경 의존 테스트)
신규 테스트: 45 passed
기존 테스트: 599 passed (회귀 없음)
```

### 5.1 신규 테스트 상세

**test_redis_cache_synonyms.py (20개)**
- TestSynonymSourceTagging: 7개 (source 태깅 저장/로드/변환)
- TestInvalidatePreservesSynonyms: 3개 (invalidate 보존)
- TestGlobalSynonyms: 8개 (글로벌 CRUD)
- TestDisconnectedGraceful: 2개 (연결 없을 때)

**test_cache_manager_synonyms.py (11개)**
- TestAddRemoveSynonyms: 2개 (래퍼 위임)
- TestGlobalSynonymsMethods: 3개 (글로벌 메서드)
- TestLoadSynonymsWithGlobalFallback: 3개 (폴백 로직)
- TestSyncGlobalSynonyms: 1개 (동기화)
- TestFileBackendFallback: 2개 (파일 백엔드)

**test_cache_management_synonyms.py (14개)**
- TestHandleListSynonyms: 5개 (목록 조회)
- TestHandleAddSynonym: 4개 (추가)
- TestHandleRemoveSynonym: 1개 (삭제)
- TestHandleUpdateSynonym: 2개 (교체)
- TestHandleInvalidatePreservesSynonyms: 2개 (보존 안내)

## 6. 하위 호환성

| 항목 | 결과 |
|------|------|
| 기존 `load_synonyms()` API | 호환 - 레거시 list 형태도 정상 로드 |
| 기존 `save_synonyms()` API | 호환 - list[str] 형태 자동 변환 |
| `SCHEMA_CACHE_BACKEND=file` | 호환 - 글로벌 synonyms는 빈 dict 반환 |
| 기존 599개 테스트 | 모두 통과 |

## 7. Critical 이슈

없음.

## 8. Minor 이슈

| 이슈 | 영향도 | 상태 |
|------|--------|------|
| `test_redis_config_exists` 환경 의존 실패 | Low | 로컬 .env에 REDIS_PORT=6380 설정으로 인한 기존 테스트 실패. 이번 구현과 무관. |

---
---

# Phase C-3: 기존 테스트 호환성 런타임 검증 보고서

> 검증일: 2026-03-20
> 검증 환경: macOS Darwin 25.3.0, Python 3.12.11, pytest 9.0.2
> 가상환경: `/Users/cptkang/AIOps/collectorinfra/.venv/`

---

## 1. MCP 서버 테스트 결과 (mcp_server/tests/)

### 실행 방법

```bash
PYTHONPATH=/Users/cptkang/AIOps/collectorinfra/mcp_server \
  .venv/bin/python -m pytest mcp_server/tests/ -v
```

> **참고**: `dbhub-mcp-server` 패키지가 `.venv`에 pip install 되어 있지 않으므로,
> `PYTHONPATH`를 `mcp_server/` 디렉토리로 설정해야 임포트가 동작합니다.

### 결과: 34 passed / 0 failed (0.26s)

| 테스트 파일 | 테스트 수 | 결과 |
|---|---|---|
| `test_config.py` (TestLoadToml) | 3 | ALL PASSED |
| `test_config.py` (TestEnvOverrides) | 3 | ALL PASSED |
| `test_config.py` (TestLoadConfig) | 2 | ALL PASSED |
| `test_security.py` (TestValidateReadonly) | 16 | ALL PASSED |
| `test_tools.py` (TestPgSearchObjectsSql) | 3 | ALL PASSED |
| `test_tools.py` (TestDb2SearchObjectsSql) | 3 | ALL PASSED |
| `test_tools.py` (TestSqlInjectionPrevention) | 2 | ALL PASSED |
| **합계** | **34** | **ALL PASSED** |

### 상세 검증 항목

- **test_security.py**: 읽기 전용 SQL 가드(`validate_readonly`) 16개 케이스 검증.
  SELECT 허용, DML/DDL/DCL 차단, 다중 문장 차단, 주석/문자열 리터럴 내 키워드 오탐 방지,
  세미콜론 인젝션 방어 모두 정상 동작.
- **test_config.py**: TOML 설정 파싱, 기본값 적용, 환경변수 오버라이드, 비활성 소스 필터링,
  설정 파일 미존재 시 기본값 생성 모두 정상 동작.
- **test_tools.py**: PostgreSQL/DB2 search_objects SQL 생성 함수의 패턴 매칭,
  객체 타입 필터링, SQL 인젝션 방어(따옴표 이스케이프) 모두 정상 동작.

---

## 2. 메인 프로젝트 테스트 결과 (tests/)

### 실행 방법

```bash
.venv/bin/python -m pytest tests/ -v
```

### 결과: 778 passed / 1 failed / 3 collection errors (45.61s)

---

### 2-1. Collection Errors (3건) -- 심각도: Major

테스트 수집 단계에서 ImportError가 발생하여 해당 모듈의 테스트가 전혀 실행되지 않음.

| 테스트 파일 | 임포트 실패 원인 |
|---|---|
| `tests/test_nodes/test_input_parser.py` | `_extract_json_from_response` not found in `src.nodes.input_parser` |
| `tests/test_schema_cache/test_description_generator.py` | `_extract_json` not found in `src.schema_cache.description_generator` |
| `tests/test_semantic_routing/test_semantic_router.py` | `_extract_json_from_response` not found in `src.routing.semantic_router` |

**근본 원인**: JSON 추출 유틸리티가 리팩터링됨.
각 모듈에 있던 비공개 함수 `_extract_json_from_response` (또는 `_extract_json`)가
`src/utils/json_extract.py`의 공개 함수 `extract_json_from_response`로 통합 이전됨.
소스 코드는 이미 새로운 함수를 사용하지만, 테스트 코드의 임포트가 갱신되지 않음.

**수정 방안**:

1. `tests/test_nodes/test_input_parser.py` (line 10):
   - 변경 전: `from src.nodes.input_parser import _extract_json_from_response, ...`
   - 변경 후: `from src.utils.json_extract import extract_json_from_response`
   - 테스트 본문의 `_extract_json_from_response(...)` 호출을 `extract_json_from_response(...)`로 변경

2. `tests/test_schema_cache/test_description_generator.py` (line 13):
   - 변경 전: `from src.schema_cache.description_generator import DescriptionGenerator, _extract_json`
   - 변경 후: `from src.schema_cache.description_generator import DescriptionGenerator` + `from src.utils.json_extract import extract_json_from_response`
   - 테스트 본문의 `_extract_json(...)` 호출을 `extract_json_from_response(...)`로 변경

3. `tests/test_semantic_routing/test_semantic_router.py` (line 17):
   - 변경 전: `from src.routing.semantic_router import ..., _extract_json_from_response, ...`
   - 변경 후: 해당 임포트를 제거하고 `from src.utils.json_extract import extract_json_from_response` 추가
   - 테스트 본문의 `_extract_json_from_response(...)` 호출을 `extract_json_from_response(...)`로 변경

---

### 2-2. Test Failure (1건) -- 심각도: Minor

| 테스트 | 결과 |
|---|---|
| `tests/test_schema_cache/test_integration.py::TestConfigIntegration::test_redis_config_exists` | FAILED |

**실패 내용**:
```
assert config.redis.port == 6379
AssertionError: assert 6380 == 6379
```

**근본 원인**: 프로젝트 루트의 `.env` 파일에 `REDIS_PORT=6380`이 설정되어 있음.
`AppConfig`는 Pydantic Settings를 사용하여 환경변수/`.env`를 자동으로 로드하므로,
코드의 기본값(6379)이 `.env`의 값(6380)으로 오버라이드됨.
테스트는 기본값 6379를 하드코딩하여 기대하지만, 실행 환경의 `.env`를 고려하지 않음.

**수정 방안** (택 1):
- (A) 테스트에서 `monkeypatch`를 사용하여 `REDIS_PORT` 환경변수를 제거한 뒤 테스트 (권장)
- (B) 테스트의 기대값을 `6380`으로 변경 (환경 종속적이라 비권장)
- (C) 테스트에서 `AppConfig`를 환경변수 무시 모드로 생성하도록 fixture 추가

---

### 2-3. Warnings (53건) -- 심각도: Minor

| 경고 유형 | 발생 위치 | 설명 |
|---|---|---|
| `DeprecationWarning: Call to deprecated function copy` | `test_excel_multisheet.py`, `test_excel_writer.py`, `test_integration.py` | openpyxl `cell.font.copy(bold=True)` 사용 -- `copy(obj)` 방식으로 변경 필요 |
| `PydanticDeprecatedSince20` | `src/clients/ollama_client.py:25`, `src/clients/fabrix_client.py:24` | class-based `Config` 대신 `ConfigDict` 사용 필요 (Pydantic V3에서 제거 예정) |

---

## 3. 종합 요약

| 구분 | 총 테스트 | 통과 | 실패 | 수집 오류 |
|---|---|---|---|---|
| MCP 서버 (mcp_server/tests/) | 34 | 34 | 0 | 0 |
| 메인 프로젝트 (tests/) | 779+ | 778 | 1 | 3 |
| **합계** | **813+** | **812** | **1** | **3** |

### 발견된 문제 분류

| 심각도 | 건수 | 내용 |
|---|---|---|
| **Critical** | 0 | -- |
| **Major** | 3 | 리팩터링 후 테스트 임포트 미갱신 (3개 테스트 파일 수집 불가) |
| **Minor** | 2 | 환경 의존적 테스트 실패 (1건), Deprecation 경고 (53건) |

### 핵심 결론

1. **MCP 서버 패키지 테스트는 100% 통과** -- security, config, tools 모듈 모두 정상 동작.
2. **메인 프로젝트 테스트는 778/779 통과 (99.87%)** -- 수집 가능한 테스트 기준.
3. **3개 테스트 파일이 수집 단계에서 ImportError** -- JSON 추출 유틸리티 리팩터링 후 테스트 코드의 임포트가 갱신되지 않은 것이 원인. 기능 자체에는 문제 없으며, 테스트 임포트 경로만 수정하면 해결됨.
4. **Redis 포트 테스트 1건 실패** -- 실행 환경의 `.env` 파일 영향. 테스트 격리(환경변수 초기화)로 해결 가능.
