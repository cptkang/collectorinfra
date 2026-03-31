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
