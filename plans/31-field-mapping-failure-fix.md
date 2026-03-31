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
