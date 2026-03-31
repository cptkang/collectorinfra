# Plan 21: EAV 구조 전체 파이프라인 지원 (Field Mapper + Redis 연동)

> 작성일: 2026-03-24
> 상태: **구현 완료** (2026-03-24)
> 선행: Plan 20 (EAV 비정규화 테이블 쿼리 지원) — 구현 완료

---

## 1. 배경 및 문제 정의

### 1.1 Plan 20에서 해결된 것

Plan 20에서 **query_generator**와 **query_validator**가 EAV 구조를 이해하도록 개선했다:
- `schema_analyzer`가 Polestar 구조 자동 감지 → `_polestar_meta` 삽입
- `query_generator`에 EAV 피벗/계층 탐색 가이드 프롬프트 삽입
- `query_validator`에 DB2 `FETCH FIRST` 문법 대응

### 1.2 아직 해결되지 않은 것 — Field Mapper

**양식(Excel/Word) 기반 조회** 시, field_mapper가 EAV 속성을 매핑할 수 없다.

**시나리오**: 양식에 "OS종류" 필드가 있고, 이를 Polestar DB에서 조회해야 할 때

```
기대 동작:
  "OS종류" → EAV 속성 OSType → CORE_CONFIG_PROP.NAME = 'OSType'의 STRINGVALUE_SHORT

현재 동작:
  "OS종류" → _synonym_match()에서 {table.column: [words]} 형식만 처리 → 매칭 실패
           → LLM 폴백 시에도 EAV 구조 설명이 프롬프트에 없어 잘못된 매핑 가능
```

### 1.3 핵심 원인

| 원인 | 상세 |
|------|------|
| **synonyms 구조 한계** | Redis의 컬럼 유사어는 `{table.column: [words]}` 형식만 지원. EAV 속성(행의 NAME 값)을 표현할 수 없음 |
| **eav_name_synonyms 미전달** | `schema_analyzer`가 `eav_name_synonyms`를 State에 저장하지만, `field_mapper` 노드가 이를 사용하지 않음 |
| **프롬프트 부재** | field_mapper 프롬프트에 EAV 구조 설명이 없어 LLM이 EAV 매핑을 이해하지 못함 |
| **검증 로직 부재** | `_validate_mapping()`이 `table.column` 존재 여부만 확인. EAV 속성 검증 불가 |

### 1.4 Redis 3계층 유사어 현황

```
synonyms:{db_id}           → {table.column: [words]}     → field_mapper ✅ 사용
synonyms:global            → {bare_column: {words, desc}} → field_mapper ✅ 사용 (글로벌 폴백)
synonyms:resource_types    → {rt_value: [words]}          → query_generator ✅ / field_mapper ❌
synonyms:eav_names         → {eav_name: [words]}          → query_generator ✅ / field_mapper ❌
```

---

## 2. 목표

EAV 구조의 속성(OSType, Vendor, Model 등)을 field_mapper가 올바르게 매핑하고, query_generator가 이를 바탕으로 EAV 피벗 쿼리를 생성하도록 전체 파이프라인을 보강한다.

### 예시 흐름 (목표 상태)

```
양식 필드: "OS종류"
  ↓
field_mapper 2단계 (synonym):
  eav_name_synonyms = {"OSType": ["운영체제", "OS 종류", ...]}
  "OS종류" 매칭 → "EAV:OSType"
  ↓
field_mapper 결과:
  column_mapping = {"OS종류": "EAV:OSType"}
  mapping_sources = {"OS종류": "eav_synonym"}
  ↓
query_generator:
  "EAV:OSType" 감지 → CASE WHEN + GROUP BY 피벗 쿼리 생성:
    MAX(CASE WHEN p.NAME = 'OSType' THEN p.STRINGVALUE_SHORT END) AS OS_TYPE
```

---

## 3. 수정 계획

### Phase A: Field Mapper에 EAV 유사어 전달 및 매칭

#### A-1. field_mapper 노드에 eav_name_synonyms 전달

**파일**: `src/nodes/field_mapper.py`

`_load_db_cache_data()` 함수에서 `eav_name_synonyms`도 로드하여 반환:

```python
async def _load_db_cache_data(
    app_config, active_db_ids, target_db_hints,
) -> tuple[dict, dict, list[str], dict[str, list[str]]]:
    # ... 기존 로직 ...

    # EAV name synonyms 로드
    eav_name_synonyms: dict[str, list[str]] = {}
    try:
        if cache_mgr and cache_mgr.redis_available:
            eav_name_synonyms = await cache_mgr._redis_cache.load_eav_name_synonyms()
    except Exception as e:
        logger.debug("eav_name_synonyms 로드 실패: %s", e)

    return all_synonyms, all_descriptions, priority_db_ids, eav_name_synonyms
```

`field_mapper()` 함수에서 `perform_3step_mapping()` 호출 시 `eav_name_synonyms` 전달:

```python
mapping_result = await perform_3step_mapping(
    llm=llm,
    field_names=field_names,
    field_mapping_hints=field_mapping_hints,
    all_db_synonyms=all_db_synonyms,
    all_db_descriptions=all_db_descriptions,
    priority_db_ids=priority_db_ids,
    eav_name_synonyms=eav_name_synonyms,  # 추가
)
```

#### A-2. perform_3step_mapping에 EAV 매칭 단계 추가

**파일**: `src/document/field_mapper.py`

`perform_3step_mapping()` 시그니처에 `eav_name_synonyms` 추가:

```python
async def perform_3step_mapping(
    ...,
    eav_name_synonyms: dict[str, list[str]] | None = None,
) -> MappingResult:
```

2단계(synonym) 후, 3단계(LLM) 전에 **EAV synonym 매칭 단계** 삽입:

```python
    # --- 2.5단계: EAV name synonyms 매칭 ---
    if remaining and eav_name_synonyms:
        _apply_eav_synonym_mapping(remaining, eav_name_synonyms, result)
```

#### A-3. EAV synonym 매칭 함수 신규 작성

**파일**: `src/document/field_mapper.py`

```python
def _apply_eav_synonym_mapping(
    remaining: set[str],
    eav_name_synonyms: dict[str, list[str]],
    result: MappingResult,
) -> None:
    """EAV 속성 유사어로 매핑을 수행한다.

    EAV 속성명(OSType, Vendor 등)의 유사어에서 필드명이 매칭되면
    "EAV:속성명" 형식으로 매핑한다.
    """
    for field in list(remaining):
        field_lower = field.lower().strip()
        for eav_name, words in eav_name_synonyms.items():
            for word in words:
                if word.lower().strip() == field_lower:
                    eav_key = f"EAV:{eav_name}"
                    # Polestar DB에 매핑 (EAV는 polestar 전용)
                    result.db_column_mapping.setdefault("polestar", {})[field] = eav_key
                    result.mapping_sources[field] = "eav_synonym"
                    remaining.discard(field)
                    break
            if field not in remaining:
                break
```

#### A-4. MappingResult에 EAV 매핑 추적

**파일**: `src/document/field_mapper.py`

`mapping_sources`의 값에 `"eav_synonym"` 추가. 기존 `"hint"`, `"synonym"`, `"llm_inferred"` 외에 `"eav_synonym"`도 허용.

로그 출력도 수정:

```python
logger.info(
    "3단계 매핑 완료: %d/%d 필드 (힌트=%d, 유사어=%d, EAV유사어=%d, LLM=%d), DB=%s",
    ...,
    sum(1 for s in result.mapping_sources.values() if s == "eav_synonym"),
    ...,
)
```

---

### Phase B: Field Mapper 프롬프트에 EAV 가이드 추가

#### B-1. 단일 DB 프롬프트 보강

**파일**: `src/prompts/field_mapper.py`

`FIELD_MAPPER_SYSTEM_PROMPT`에 EAV 매핑 가이드 추가:

```
## EAV(Entity-Attribute-Value) 구조 매핑

일부 DB(예: Polestar)는 EAV 패턴을 사용합니다.
이 구조에서는 서버 속성(OS종류, 제조사 등)이 별도 테이블의 행으로 저장됩니다.

EAV 구조가 감지된 경우:
- "OS종류", "운영체제" 등 → "EAV:OSType"
- "제조사", "벤더" 등 → "EAV:Vendor"
- "모델", "서버 모델" 등 → "EAV:Model"
- "시리얼 번호" 등 → "EAV:SerialNumber"

"EAV:" 접두사가 붙은 매핑은 피벗 쿼리로 자동 변환됩니다.
```

#### B-2. _format_schema_columns()에 EAV 가상 컬럼 포함

**파일**: `src/document/field_mapper.py`

`_format_schema_columns()` 함수에서 `schema_info`에 `_polestar_meta`가 있으면 EAV known_attributes를 가상 컬럼으로 추가:

```python
def _format_schema_columns(
    schema_info: dict[str, Any],
    column_descriptions: dict[str, str] | None = None,
    column_synonyms: dict[str, list[str]] | None = None,
) -> str:
    # ... 기존 정규 컬럼 포맷 ...

    # Polestar EAV 가상 컬럼 추가
    polestar_meta = schema_info.get("_polestar_meta")
    if polestar_meta:
        lines.append("")
        lines.append("# EAV 속성 (CORE_CONFIG_PROP에서 피벗 쿼리로 추출)")
        lines.append("# 매핑 시 'EAV:속성명' 형식을 사용하세요.")
        for attr in polestar_meta.get("eav", {}).get("known_attributes", []):
            desc = polestar_meta.get("resource_types", {}).get(attr, "")
            lines.append(f"- EAV:{attr} -- EAV 피벗 속성")

    return "\n".join(lines)
```

#### B-3. 멀티 DB 프롬프트 보강

**파일**: `src/prompts/field_mapper.py`

`FIELD_MAPPER_MULTI_DB_SYSTEM_PROMPT`에도 동일한 EAV 가이드 추가.

---

### Phase C: Query Generator의 EAV 매핑 결과 활용

#### C-1. EAV 매핑 결과를 피벗 쿼리로 변환

**파일**: `src/nodes/query_generator.py`

`_build_user_prompt()`에서 `column_mapping`에 `"EAV:"` 접두사가 있는 항목을 감지하여 피벗 쿼리 힌트를 추가:

```python
# column_mapping에서 EAV 매핑 감지
eav_mappings = {
    field: col.replace("EAV:", "")
    for field, col in column_mapping.items()
    if col and col.startswith("EAV:")
}
if eav_mappings:
    eav_lines = "\n".join(
        f'- "{field}" → EAV 속성 "{attr}" (CORE_CONFIG_PROP.NAME = \'{attr}\' → STRINGVALUE_SHORT)'
        for field, attr in eav_mappings.items()
    )
    parts.append(
        f"## EAV 피벗 매핑 (반드시 CASE WHEN 피벗으로 변환)\n{eav_lines}\n\n"
        "위 EAV 속성은 CORE_CONFIG_PROP 테이블에서 피벗 쿼리로 추출해야 합니다:\n"
        "  MAX(CASE WHEN p.NAME = '속성명' THEN p.STRINGVALUE_SHORT END) AS alias\n"
        "CMM_RESOURCE r LEFT JOIN CORE_CONFIG_PROP p ON p.CONFIGURATION_ID = r.RESOURCE_CONF_ID"
    )
```

#### C-2. _validate_mapping()에 EAV 검증 추가

**파일**: `src/document/field_mapper.py`

`_validate_mapping()` 함수에서 `EAV:` 접두사 매핑을 검증:

```python
def _validate_mapping(mapping, schema_info, field_names):
    # ... 기존 로직 ...

    # EAV 속성 검증
    polestar_meta = schema_info.get("_polestar_meta")
    known_eav_attrs = set()
    if polestar_meta:
        known_eav_attrs = set(
            polestar_meta.get("eav", {}).get("known_attributes", [])
        )

    for name in field_names:
        mapped_col = mapping.get(name)
        if mapped_col and mapped_col.startswith("EAV:"):
            attr_name = mapped_col[4:]  # "EAV:" 제거
            if attr_name in known_eav_attrs:
                validated[name] = mapped_col  # 유효한 EAV 속성
            else:
                logger.warning(
                    "EAV 매핑 검증 실패: '%s' -> '%s' (알 수 없는 EAV 속성)",
                    name, mapped_col,
                )
                validated[name] = None
        # ... 기존 정규 컬럼 검증 ...
```

---

### Phase D: Output Generator의 EAV 결과 처리

#### D-1. 쿼리 결과의 EAV alias 매핑

EAV 피벗 쿼리 결과는 `OS_TYPE`, `MODEL` 등의 alias로 반환된다. output_generator(Excel/Word 채우기)에서 이 alias를 양식 필드명에 매핑할 때, `column_mapping`의 `EAV:OSType` → 쿼리 결과의 `OS_TYPE` alias 간 연결이 필요하다.

**파일**: `src/nodes/result_organizer.py`

`organized_data`의 `column_mapping`에 EAV alias 변환을 추가:

```python
# EAV 매핑 결과의 alias 변환
# column_mapping에서 "EAV:OSType" → 쿼리 결과에서 "OS_TYPE" (= 속성명의 대문자 변환)
for field, col in column_mapping.items():
    if col and col.startswith("EAV:"):
        attr = col[4:]
        # 쿼리 결과의 alias는 대문자+언더스코어 (예: OSType → OS_TYPE, SerialNumber → SERIAL_NUMBER)
        alias = _eav_attr_to_alias(attr)
        if alias in result_columns:
            column_mapping[field] = alias
```

---

## 4. 수정 대상 파일 요약

| 파일 | 변경 내용 | 우선순위 |
|------|----------|---------|
| `src/nodes/field_mapper.py` | eav_name_synonyms 로드 및 `perform_3step_mapping()`에 전달 | P0 |
| `src/document/field_mapper.py` | `perform_3step_mapping()` EAV 파라미터 추가, `_apply_eav_synonym_mapping()` 신규, `_format_schema_columns()` EAV 가상 컬럼, `_validate_mapping()` EAV 검증 | P0 |
| `src/prompts/field_mapper.py` | 단일/멀티 DB 프롬프트에 EAV 매핑 가이드 추가 | P0 |
| `src/nodes/query_generator.py` | `_build_user_prompt()`에 EAV 매핑 → 피벗 쿼리 힌트 추가 | P0 |
| `src/nodes/result_organizer.py` | EAV alias 변환 로직 | P1 |
| `tests/test_polestar_eav.py` | EAV field_mapper 테스트 추가 | P1 |

---

## 5. 구현 순서

```
Step 1 (P0): Field Mapper EAV 매칭
  ├── src/document/field_mapper.py — _apply_eav_synonym_mapping(), perform_3step_mapping 확장
  ├── src/nodes/field_mapper.py — eav_name_synonyms 로드/전달
  └── src/prompts/field_mapper.py — EAV 가이드 추가

Step 2 (P0): Query Generator EAV 매핑 활용
  └── src/nodes/query_generator.py — EAV: 접두사 감지 → 피벗 쿼리 힌트

Step 3 (P0): 매핑 검증
  └── src/document/field_mapper.py — _validate_mapping() EAV 검증

Step 4 (P1): Output 처리
  └── src/nodes/result_organizer.py — EAV alias 변환

Step 5 (P1): 테스트
  └── tests/test_polestar_eav.py — EAV field_mapper 테스트 추가
```

---

## 6. EAV 매핑 규약

### 6.1 `EAV:` 접두사 규약

EAV 속성 매핑은 `"EAV:속성명"` 형식으로 표현한다:

| 양식 필드 | 매핑 결과 | 의미 |
|----------|----------|------|
| OS종류 | `EAV:OSType` | CORE_CONFIG_PROP.NAME = 'OSType'의 STRINGVALUE_SHORT |
| 제조사 | `EAV:Vendor` | CORE_CONFIG_PROP.NAME = 'Vendor'의 STRINGVALUE_SHORT |
| 서버 모델 | `EAV:Model` | CORE_CONFIG_PROP.NAME = 'Model'의 STRINGVALUE_SHORT |
| 서버명 | `CMM_RESOURCE.HOSTNAME` | 기존 정규 컬럼 매핑 (EAV 아님) |

### 6.2 mapping_sources 값

| 값 | 의미 |
|----|------|
| `hint` | 사용자 프롬프트 힌트 |
| `synonym` | Redis 컬럼 유사어 매칭 |
| `eav_synonym` | Redis EAV NAME 유사어 매칭 **(신규)** |
| `llm_inferred` | LLM 의미 추론 |

---

## 7. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 방안 |
|--------|------|----------|
| EAV 매핑과 정규 매핑 혼재 시 query_generator 혼란 | 잘못된 SQL | `_build_user_prompt()`에서 EAV/정규 매핑을 명확히 분리하여 프롬프트에 제시 |
| EAV alias 변환 불일치 | 출력 파일에 데이터 누락 | 쿼리 결과의 실제 컬럼명과 alias 매핑 테이블을 유지 |
| Polestar 외 DB에 `EAV:` 매핑 적용 | 매핑 오류 | `_apply_eav_synonym_mapping()`이 "polestar" DB에만 매핑 |
| 기존 양식 조회 기능 퇴행 | 기능 장애 | EAV 매핑은 추가 로직이므로 기존 흐름 변경 없음 (하위 호환) |

---

## 8. 검증 기준

### 8.1 단위 테스트

- [ ] `_apply_eav_synonym_mapping()`: "OS종류" → `EAV:OSType` 매칭 확인
- [ ] `_apply_eav_synonym_mapping()`: 대소문자 무관 매칭 확인
- [ ] `_apply_eav_synonym_mapping()`: EAV 유사어에 없는 필드는 매칭 안 됨 확인
- [ ] `_validate_mapping()`: `EAV:OSType` → known_attributes에 있으면 통과
- [ ] `_validate_mapping()`: `EAV:UnknownAttr` → None 반환
- [ ] `_format_schema_columns()`: `_polestar_meta` 있으면 EAV 가상 컬럼 포함

### 8.2 통합 테스트

- [ ] 양식에 "OS종류", "제조사" 필드 → `EAV:OSType`, `EAV:Vendor` 매핑
- [ ] query_generator가 EAV 매핑을 감지하여 CASE WHEN 피벗 쿼리 생성
- [ ] 정규 필드("서버명")와 EAV 필드("OS종류")가 혼합된 양식에서 올바른 쿼리 생성

### 8.3 회귀 테스트

- [ ] Polestar가 아닌 DB의 양식 매핑에 영향 없음
- [ ] EAV synonyms가 없을 때 기존 3단계 매핑 정상 동작
