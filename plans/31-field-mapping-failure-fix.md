# Plan 31: 필드 매핑 실패 원인 분석 및 해결 방안

> 작성일: 2026-03-26
> 대상 파일: `sample/취합 예시1.xlsx`
> 매핑 보고서: `sample/mapping_report.md` (2/33 필드 매핑 성공, 6%)

## 문제 요약

| 필드명 | 기대 매핑 | 실제 결과 | 문제 유형 |
|--------|----------|----------|----------|
| 자산명/호스트명 | hostname (컬럼) 또는 EAV:Hostname | 매핑 불가 | 유사어 매칭 실패 |
| IP | IPADDRESS (컬럼) 또는 EAV:IPaddress | 매핑 불가 | 글로벌 유사어 미참조 |
| S/N(Serial Number) | EAV:SerialNumber | 매핑 불가 | 유사어 매칭 실패 |
| 제조사 | EAV:Vendor | 매핑 성공 (eav_synonym) | 조회 결과 0건 |
| 모델명 | EAV:Model | 매핑 성공 (eav_synonym) | 조회 결과 0건 |

---

## 원인 분석

### 원인 1: field_mapper가 글로벌 유사어 사전을 참조하지 않음

**위치**: `src/nodes/field_mapper.py:204-208` (`_load_db_cache_data`)

```python
# 현재 코드: per-DB synonyms만 로드
synonyms = await cache_mgr.get_synonyms(db_id)
```

- `cache_manager.get_synonyms(db_id)`는 **DB별 유사어**만 반환
- `cache_manager.load_synonyms_with_global_fallback(db_id)`가 글로벌 유사어를 병합하는 메서드이지만, field_mapper에서 호출하지 않음
- `global_synonyms.yaml`에 아래 매핑이 등록되어 있으나 사용되지 않음:
  - IPADDRESS → ["IP주소", "IP 주소", "아이피", **"IP"**, ...]
  - HOSTNAME → ["호스트명", "서버명", ...]

**영향**: "IP"는 글로벌 유사어에 정확히 등록되어 있어 정확 일치로도 매핑 가능하지만, 글로벌 유사어가 로드되지 않아 실패

### 원인 2: synonym 매칭이 정확 일치(exact match)만 지원

**위치**: `src/document/field_mapper.py:413-416` (`_synonym_match`)

```python
for word in words:
    if word.lower().strip() == field_lower:  # 정확 일치만
        return col_key
```

**위치**: `src/document/field_mapper.py:449-451` (`_apply_eav_synonym_mapping`)

```python
for word in words:
    if word.lower().strip() == field_lower:  # 정확 일치만
        matched = True
```

**영향을 받는 필드들**:

| 양식 필드 | 등록된 유사어 | 일치 여부 | 실패 원인 |
|----------|-------------|----------|----------|
| `자산명/호스트명` | `호스트명`, `서버명` | 불일치 | "/"로 구분된 복합 필드명. 부분 문자열 포함이지만 정확 일치 아님 |
| `S/N(Serial Number)` | `S/N`, `시리얼 번호`, `serial number` | 불일치 | 괄호 부연설명 포함. "S/N" 부분 포함이지만 정확 일치 아님 |
| `IP` | `IP` (글로벌에 등록됨) | 정확 일치 가능 | 원인 1에 의해 글로벌 유사어 미참조로 실패 |

### 원인 3: EAV 매핑은 성공했으나 multi_db_executor에 EAV 피벗 쿼리 분리 로직 부재

**위치**: `src/nodes/multi_db_executor.py:275-307` (`_generate_sql`)

- **query_generator.py** (단일 DB 경로)에는 EAV 매핑을 분리하여 CASE WHEN 피벗 쿼리를 명시적으로 지시하는 로직이 있음 (lines 324-383):
  ```python
  # query_generator.py에만 있는 EAV 분리 로직
  eav_entries = [
      (field, col[4:])  # "EAV:" 접두사 제거
      for field, col in column_mapping.items()
      if col and col.startswith("EAV:")
  ]
  # → CASE WHEN 피벗 매핑 가이드 생성
  ```

- **multi_db_executor.py**는 `EAV:Vendor` 형태를 column_mapping에 그대로 전달:
  ```python
  # multi_db_executor.py의 현재 코드 (EAV 분리 없음)
  mapped_entries = [
      (field, col) for field, col in column_mapping.items() if col
  ]
  # → "제조사" -> EAV:Vendor 형태로 LLM에 전달 (CASE WHEN 지시 없음)
  ```

- `structure_guide` (query_guide)만으로는 구체적인 피벗 쿼리 지시가 부족
- LLM이 `EAV:Vendor` 매핑만 보고 올바른 피벗 쿼리를 생성하지 못함

**결과**: SQL은 생성되었으나 잘못된 JOIN 조건이나 EAV 속성명 대소문자 불일치 등으로 0건 반환

---

## 해결 방안

### 해결 1: field_mapper에서 글로벌 유사어 통합 로드

**수정 대상**: `src/nodes/field_mapper.py` - `_load_db_cache_data()`

```python
# 변경 전
synonyms = await cache_mgr.get_synonyms(db_id)

# 변경 후
synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
```

- `load_synonyms_with_global_fallback()`은 per-DB 유사어를 먼저 로드하고, 해당 DB 스키마에 존재하는 컬럼 중 per-DB 유사어가 없는 컬럼에 대해 글로벌 유사어를 폴백으로 적용
- 이미 cache_manager에 구현되어 있으므로 호출만 변경하면 됨
- **즉시 해결되는 필드**: "IP" (글로벌 유사어에 정확 일치로 등록됨)

### 해결 2: LLM 기반 유사어 발견 단계 추가 (Step 2.8)

기존 3단계 매핑에서 정확 일치(Step 2, 2.5) 이후 남은 미매핑 필드를 LLM에게 일괄 전달하여 유사어 매칭을 수행한다. 기존 Step 3(LLM 통합 추론)과 별도로, **유사어 발견에 특화된 경량 LLM 호출**을 1회 수행한다.

> 코드 기반 퍼지 매칭(정규식, 부분 문자열 등) 대신 LLM에게 판단을 위임한다.
> 필드당 LLM 호출 시 속도/비용 이슈가 있으므로 **전체 미매핑 필드를 1회 일괄 호출**한다.

**수정 대상**: `src/document/field_mapper.py` - `perform_3step_mapping()` 내에 새 단계 삽입

#### 위치: Step 2.5 (EAV synonym) 이후, Step 3 (LLM 통합 추론) 이전

```
Step 1: 프롬프트 힌트
Step 2: Redis synonyms 정확 일치
Step 2.5: EAV name synonyms 정확 일치
Step 2.8: LLM 유사어 발견 (NEW) ← 컬럼 synonym + EAV synonym 통합
Step 3: LLM 통합 추론 (기존, 남은 필드 대상)
```

#### 동작 흐름

1. **입력 구성**: 미매핑 필드 목록 + DB 컬럼명 목록(bare name) + EAV 속성명 목록을 하나의 프롬프트로 구성
2. **LLM 1회 호출**: "각 필드명이 어떤 DB 컬럼 또는 EAV 속성과 의미적으로 대응하는지 판별"
3. **매핑 적용**: LLM이 매칭한 결과를 column_mapping / db_column_mapping에 반영
4. **글로벌 synonym 자동 등록**: LLM이 매칭한 필드명을 해당 컬럼/EAV 항목의 글로벌 synonym에 추가 → 동일 필드명이 재입력되면 Step 2에서 정확 일치로 즉시 매핑

#### 프롬프트 설계

synonym 전체를 전달할 필요 없이, **DB 컬럼명(bare name)과 EAV 속성명**만 전달한다. LLM은 이름의 의미를 직접 추론하여 매핑할 수 있다.

```
## 미매핑 양식 필드 목록
- 자산명/호스트명
- IP
- S/N(Serial Number)
- 설명
- 위치
- ...

## DB 컬럼명 목록 (DB별)

### polestar
- CMM_RESOURCE.HOSTNAME
- CMM_RESOURCE.IPADDRESS
- CMM_RESOURCE.DESCRIPTION
- CMM_RESOURCE.LOCATION
- CMM_RESOURCE.RESOURCE_TYPE
- ...

## EAV 속성명 목록
- Hostname
- IPaddress
- SerialNumber
- Vendor
- Model
- OSType
- ...

## 지시사항
각 미매핑 필드가 위 DB 컬럼 또는 EAV 속성 중 어떤 항목과 의미적으로 대응하는지 판별하세요.
복합 필드명("자산명/호스트명"), 괄호 부연("S/N(Serial Number)"), 약어 등을 고려하세요.
하나의 필드에 DB 컬럼과 EAV 속성이 모두 매칭 가능한 경우, DB 컬럼을 우선 선택하세요.
확신이 없는 매핑은 null로 표시하세요.

## 출력 형식 (JSON)
{
    "필드명": {
        "matched_key": "polestar:CMM_RESOURCE.HOSTNAME" 또는 "EAV:SerialNumber",
        "reason": "매칭 근거 (1줄)"
    }
}
매핑 불가: "필드명": null
```

#### 프롬프트 설계 근거

- **synonym 목록 불필요**: LLM은 "자산명/호스트명" → HOSTNAME, "IP" → IPADDRESS를 synonym 없이도 직접 추론 가능
- **프롬프트 크기 최소화**: 컬럼명 + EAV 속성명만 전달하므로 토큰 소비가 적음
- **Step 3과의 차별화**: Step 3은 전체 스키마(테이블 구조, 타입, 샘플 데이터, descriptions)를 전달하는 반면, Step 2.8은 이름 목록만 전달

#### 기대 매칭 결과

| 양식 필드 | LLM 매칭 결과 | 매칭 근거 |
|----------|-------------|----------|
| 자산명/호스트명 | CMM_RESOURCE.HOSTNAME | "호스트명"이 HOSTNAME의 한국어 표현 |
| IP | CMM_RESOURCE.IPADDRESS | "IP"는 IPADDRESS의 약어 |
| S/N(Serial Number) | EAV:SerialNumber | "Serial Number"가 SerialNumber과 동일 |
| 설명 | CMM_RESOURCE.DESCRIPTION | "설명"은 DESCRIPTION의 한국어 표현 |
| 위치 | CMM_RESOURCE.LOCATION | "위치"는 LOCATION의 한국어 표현 |

#### 글로벌 synonym 자동 등록

LLM이 매칭한 필드명을 해당 항목의 글로벌 synonym에 추가 등록:

```
HOSTNAME words에 "자산명/호스트명" 추가
IPADDRESS words에 "IP" 추가 (이미 있으면 스킵)
EAV:SerialNumber words에 "S/N(Serial Number)" 추가
DESCRIPTION words에 "설명" 추가 (이미 있으면 스킵)
```

→ 다음 실행 시 Step 2 정확 일치에서 바로 매핑 성공 (LLM 재호출 불필요)

#### Step 3과의 역할 분담

| | Step 2.8 (LLM 유사어 발견) | Step 3 (LLM 통합 추론) |
|--|--------------------------|----------------------|
| 목적 | 컬럼명/EAV명과 필드명 간 이름 수준 매칭 | DB 스키마 전체를 보고 새로운 매핑 추론 |
| 입력 | 미매핑 필드 + 컬럼명(bare name) + EAV 속성명 | 미매핑 필드 + 전체 스키마 + descriptions + synonyms |
| 결과 | 이름 기반 매핑 + 글로벌 synonym 등록 | 스키마 기반 매핑 + per-DB synonym 등록 |
| 비용 | 낮음 (이름 목록만 전달) | 높음 (전체 스키마 전달) |
| 학습 효과 | 글로벌 synonym에 축적 → 재사용 가능 | per-DB synonym에 저장 → 해당 DB 한정 |

### 해결 3: multi_db_executor에 EAV 피벗 쿼리 분리 로직 추가

**수정 대상**: `src/nodes/multi_db_executor.py` - `_generate_sql()`

query_generator.py의 EAV 분리 로직(lines 324-383)을 multi_db_executor에도 동일하게 적용:

1. column_mapping에서 `EAV:` 접두사가 있는 항목을 분리
2. `_get_eav_pattern(schema_info)`으로 config_table, attribute_column, value_column, join_condition 추출
3. CASE WHEN 피벗 쿼리 가이드를 프롬프트에 명시

```python
# EAV 분리 및 피벗 쿼리 가이드 생성 (query_generator.py와 동일 로직)
regular_entries = [(f, c) for f, c in column_mapping.items() if c and not c.startswith("EAV:")]
eav_entries = [(f, c[4:]) for f, c in column_mapping.items() if c and c.startswith("EAV:")]

if eav_entries:
    eav_pattern = _get_eav_pattern(schema_info)
    # ... CASE WHEN 피벗 쿼리 가이드 생성
```

### 해결 4: EAV도 해결 2와 동일한 방식으로 통합 처리

해결 2의 Step 2.8 프롬프트에 **EAV 속성명(bare name)** 을 함께 전달한다. synonym이 아닌 속성명 자체(Hostname, SerialNumber, Vendor 등)를 전달하고, LLM이 필드명과 직접 매칭한다. 매칭 결과를 사후에 EAV name synonym에 등록하여 다음 실행 시 정확 일치로 재사용한다.

**동작 방식**:
1. Step 2.8 프롬프트의 "EAV 속성명 목록" 섹션에 EAV name을 나열 (synonym 목록 불필요)
2. LLM이 `EAV:SerialNumber`에 매칭하면 `db_column_mapping`에 반영
3. 매칭된 필드명을 Redis의 `eav_name_synonyms`에 사후 등록 (`redis_cache.save_eav_name_synonyms()`)

**기대 결과**:
- "S/N(Serial Number)" → EAV:SerialNumber 매칭 → eav_name_synonyms에 "S/N(Serial Number)" 추가
- 다음 실행 시 Step 2.5에서 정확 일치로 즉시 매핑 (LLM 재호출 불필요)

> `config/global_synonyms.yaml` 수동 편집 불필요. LLM이 발견한 매핑이 Redis synonym으로 자동 축적된다.

---

## 구현 우선순위

| 순서 | 해결 방안 | 난이도 | 영향 범위 | 해결되는 필드 |
|------|---------|-------|----------|-------------|
| 1 | 해결 1: 글로벌 유사어 통합 로드 | 낮음 (1줄 변경) | 전체 필드 매핑 | IP (글로벌에 이미 등록된 필드들) |
| 2 | 해결 2+4: LLM 유사어 발견 단계 (Step 2.8) | 중간~높음 | synonym 매칭 전체 (컬럼+EAV 통합) | 자산명/호스트명, IP, S/N(Serial Number), 설명 등 |
| 3 | 해결 3: EAV 피벗 쿼리 분리 | 중간 | multi_db_executor EAV 쿼리 | 제조사(0건→정상), 모델명(0건→정상) |

> 해결 2와 해결 4는 동일한 메커니즘(LLM 기반 유사어 발견)이므로 하나의 구현으로 통합한다.

---

## 수정 대상 파일 목록

| 파일 | 수정 내용 |
|------|----------|
| `src/nodes/field_mapper.py` | `_load_db_cache_data()`에서 `load_synonyms_with_global_fallback()` 호출 |
| `src/document/field_mapper.py` | `perform_3step_mapping()`에 Step 2.8 LLM 유사어 발견 단계 추가 |
| `src/prompts/field_mapper.py` | Step 2.8용 LLM 유사어 발견 프롬프트 추가 |
| `src/nodes/multi_db_executor.py` | `_generate_sql()`에 EAV 피벗 쿼리 분리 로직 추가 |

## 검증 방법

1. `sample/취합 예시1.xlsx` 입력 후 mapping_report.md 확인
   - "자산명/호스트명", "IP", "S/N(Serial Number)"이 매핑 성공하는지 확인
   - mapping_sources에서 "llm_synonym" (Step 2.8)으로 매핑된 항목 확인
2. 글로벌 synonym 자동 등록 확인
   - Step 2.8에서 매핑된 필드명이 Redis 글로벌 synonym에 등록되었는지 확인
   - 동일 파일 재실행 시 Step 2 정확 일치에서 즉시 매핑되는지 확인 (LLM 재호출 없음)
3. "제조사", "모델명"의 EAV 쿼리 결과가 0건이 아닌지 확인
   - SQL 로그에서 CASE WHEN 피벗 쿼리가 올바르게 생성되는지 확인
4. 기존 매핑 동작에 대한 회귀 테스트
   - 정확 일치로 이미 매핑되던 필드들이 여전히 정상 동작하는지 확인
   - Step 2.8 추가로 인한 전체 매핑 소요시간 측정 (LLM 1회 호출 추가 비용)


---

# Verification Report

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
