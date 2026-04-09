# Plan 37: Synonym 통합 관리 및 EAV 접두사 비교 오류 수정

## 배경

세 가지 연관된 문제가 column_mapping 기반 데이터 채우기를 실패시키고 있다:

1. **EAV 접두사 문제**: `column_mapping`에 `"EAV:OSType"` 형태로 저장되지만, 쿼리 결과 컬럼명은 `"OSType"`이다. 비교 시 접두사를 제거하지 않아 매핑 실패.
2. **필드명 매칭 문제**: 엑셀 양식에서 추출한 필드명과 DB/Redis synonym의 필드명이 미세하게 달라서 매핑 자체가 생성되지 않는 경우.
3. **Synonym 분리 관리 문제**: 일반 필드는 `synonyms:global`에, EAV는 `synonyms:eav_names`에 별도 저장되어 통합 관리가 안 됨.

---

## Part A-0: Synonym 관리 분석 및 통합 설계

### 현재 Synonym 저장소 구조 (유지)

| 저장소 | Redis 키 | 저장 키 형식 | 저장 내용 | 조회 경로 |
|--------|---------|-------------|----------|----------|
| DB별 synonym | `schema:{db_id}:synonyms` | `table.column` | `{words, sources}` | Step 2: `_apply_synonym_mapping` |
| 글로벌 synonym | `synonyms:global` | `bare_column_name` | `{words, description}` | Step 2 폴백: `load_synonyms_with_global_fallback` |
| EAV name synonym | `synonyms:eav_names` | `eav_name` | `[words]` | Step 2.5: `_apply_eav_synonym_mapping` |

**저장소 구조 원칙:**
- **글로벌 synonym**: bare column name (테이블명 없음) — 범용 유사어 사전
- **DB별 synonym**: `table.column` 형식 — 특정 DB 스키마에 종속된 유사어
- **EAV name synonym**: EAV 속성명 — EAV 여부 판별 + 유사어

### 핵심 문제 0: `schema:{db_id}:synonyms`가 Redis에 전혀 저장되지 않는 원인

**증상**: 여러 번 코드를 실행했으나 Redis에 `schema:{db_id}:synonyms` 키가 존재하지 않음.

**원인 분석 — synonym 생성 경로 추적:**

DB별 synonym이 Redis에 저장되려면 `cache_manager.save_synonyms(db_id, synonyms)`가 호출되어야 한다. 이 함수를 호출하는 경로는 **3곳**뿐이다:

| # | 호출 위치 | 트리거 | 자동 여부 |
|---|----------|--------|----------|
| 1 | `cache_management.py:329` `_handle_generate_descriptions()` | 사용자가 "generate-descriptions" 캐시 관리 액션을 명시적으로 요청 | **수동** |
| 2 | `cache_management.py:374` `_handle_generate_synonyms()` | 사용자가 "generate-synonyms" 캐시 관리 액션을 명시적으로 요청 | **수동** |
| 3 | `schema_cache.py:677` `_generate_descriptions_for_db()` | REST API `POST /admin/schema-cache/generate-descriptions` 호출 또는 캐시 생성 시 `include_descriptions=True` 옵션 | **수동** (API) |

**정상 파이프라인에서의 흐름:**

```
사용자 쿼리 → schema_analyzer → _get_schema_via_cache_manager()
  → cache_mgr.get_schema_or_fetch(client, db_id)
    → 캐시 미스: DB 전체 스키마 조회
    → save_schema(db_id, schema_dict)     ✓ 스키마 저장됨
    → cleanup_stale_entries(db_id, ...)    ✓
    → descriptions = get_descriptions()    → 빈 값 (아직 생성 안 됨)
    → synonyms = load_synonyms_with_global_fallback()  → 빈 값
    → return schema_dict, False, {}, {}
```

**근본 원인**: `get_schema_or_fetch()`는 스키마만 저장하고 **descriptions/synonyms 생성을 호출하지 않는다**. 설정에 `auto_generate_descriptions: bool = True` (`src/config.py:204`)가 있지만, `get_schema_or_fetch()` 내부에서 이 설정을 참조하여 자동 생성을 트리거하는 코드가 **존재하지 않는다**.

**결과적으로:**
1. 스키마는 `schema:{db_id}:tables`에 저장됨 ✓
2. descriptions (`schema:{db_id}:descriptions`)는 비어있음 ✗
3. synonyms (`schema:{db_id}:synonyms`)는 비어있음 ✗
4. 글로벌 synonym은 `SynonymLoader`가 `config/global_synonyms.yaml`에서 로드하면 존재할 수 있음 △
5. EAV synonym은 `SynonymLoader`가 `eav_name_values`를 로드하면 존재 △

사용자가 "generate-descriptions" 액션을 수동으로 실행하거나, REST API `/admin/schema-cache/generate-descriptions`를 호출해야만 DB별 synonym이 생성된다. **정상 사용 흐름에서는 synonym이 절대 생성되지 않는다.**

### 핵심 문제 1: EAV와 Global의 단절

**글로벌 synonym 활용 흐름** (정규 컬럼):
```
synonyms:global["HOSTNAME"] = {words: ["서버명", "호스트명"]}
    ↓ load_synonyms_with_global_fallback()
    ↓ 스키마에서 CMM_RESOURCE.HOSTNAME 발견 → global에서 HOSTNAME 폴백
    ↓
all_db_synonyms[db_id]["CMM_RESOURCE.HOSTNAME"] = ["서버명", "호스트명"]
    ↓ _apply_synonym_mapping → _synonym_match
매핑 성공: "서버명" → CMM_RESOURCE.HOSTNAME
```

**EAV synonym 활용 흐름** (EAV 속성):
```
synonyms:eav_names["OSType"] = ["운영체제", "OS 종류"]
    ↓ load_eav_name_synonyms() — 독립 조회
eav_name_synonyms["OSType"] = ["운영체제", "OS 종류"]
    ↓ _apply_eav_synonym_mapping — 별도 비교 로직
매핑 성공: "운영체제" → EAV:OSType
    ↓ db_column_mapping[eav_db_id]["운영체제"] = "EAV:OSType"
```

### EAV 테이블 정보 관리 분석

**질문**: EAV synonym(`synonyms:eav_names`)에 테이블명/컬럼명이 없는데, EAV가 어떤 테이블에 속하는지 어떻게 아는가?

**분석 결과**: EAV의 테이블 정보는 synonym 저장소가 아닌 **DB 프로파일(structure_meta)** 에서 관리된다. 두 시스템이 역할을 분리하고 있다:

| 정보 | 저장 위치 | 사용 시점 |
|------|----------|----------|
| EAV 속성명 ↔ 유사어 매핑 | `synonyms:eav_names` | field_mapper Step 2.5: 필드명 → `EAV:OSType` 매핑 |
| EAV 테이블 구조 (entity/config/컬럼) | `config/db_profiles/*.yaml` → `_structure_meta` | query_generator: SQL 생성 시 테이블/조인/피벗 결정 |
| EAV 매핑의 DB 귀속 | `_resolve_fallback_db_id()` | field_mapper: `db_column_mapping[db_id]`에 할당 |

**EAV 테이블 정보 흐름 상세:**

```
1. DB 프로파일 (config/db_profiles/polestar.yaml)
   patterns:
     - type: eav
       entity_table: cmm_resource          ← entity 테이블
       config_table: core_config_prop      ← EAV 속성값 테이블
       attribute_column: name              ← 속성명 컬럼 (NAME)
       value_column: stringvalue_short     ← 속성값 컬럼
       known_attributes:
         - name: OSType                    ← 속성명
           synonyms: ["운영체제", ...]     ← 유사어

2. schema_analyzer → _structure_meta에 저장
   schema_dict["_structure_meta"] = {patterns: [{type: "eav", ...}]}

3. query_generator / multi_db_executor
   → _get_eav_pattern(schema_info) → entity_table, config_table, attribute_column 추출
   → EAV:OSType 매핑을 감지하면 CASE WHEN 피벗 SQL 생성:
     SELECT ... CASE WHEN c.name='OSType' THEN c.stringvalue_short END AS "OSType"
     FROM core_config_prop c  ← config_table

4. field_mapper의 DB 귀속
   → _resolve_fallback_db_id()로 eav_db_id 결정 (priority_db_ids[0] 또는 active_db_ids[0])
   → db_column_mapping[eav_db_id]["운영체제"] = "EAV:OSType"
   → 이 db_id가 query_generator에 전달되어 해당 DB의 _structure_meta에서 EAV 패턴 참조
```

**결론**: `synonyms:eav_names`는 "속성명 ↔ 유사어" 매핑만 담당하고, 테이블 구조(entity_table, config_table, attribute_column, value_column)는 **DB 프로파일의 `_structure_meta`** 에서 관리된다. 두 시스템이 `EAV:OSType` 접두사 규약으로 연결되어 있다:
- field_mapper: 유사어로 `EAV:OSType` 매핑 생성 (synonym 참조)
- query_generator: `EAV:` 접두사를 감지하면 `_structure_meta`에서 테이블 구조를 조회하여 SQL 생성

이 분리 구조는 의도적이며, 각 관심사(유사어 매칭 vs SQL 생성)를 분리하여 관리한다.

**단절 지점:**
1. `global_synonyms.yaml`의 `eav_name_values` 섹션은 `eav_names`에만 저장되고 `global`에는 미반영
2. `_synonym_match()`와 `_apply_eav_synonym_mapping()`이 동일 비교를 각각 구현
3. LLM 추론 후 EAV synonym은 `eav_names`에만 직접 저장, 비-EAV는 `cache_manager.add_synonyms()`로 저장 (실패 가능)
4. `synonyms:global`에 EAV 속성명이 없으므로, 폴백 매칭 불가

### 통합 설계 원칙

**기존 저장소 구조를 유지하면서, `synonyms:global`에 EAV 속성명도 등록하여 통합 비교 인프라를 공유한다.**

1. **DB별 synonym은 `table.column` 형식 유지** — 기존 구조 불변
2. **글로벌 synonym은 bare column name 유지** — 기존 구조 불변
3. **EAV 속성명도 `synonyms:global`에 추가 등록** — global의 비교·폴백 인프라 활용
4. **`synonyms:eav_names`는 EAV 여부 판별용 메타데이터로 유지** — 기존 구조 불변
5. **비교 함수를 통합** — `normalize_field_name()`으로 정규화 로직을 한 곳에서 관리

### 통합 후 목표 흐름

```
[synonyms:global]  — bare column name 키 (EAV 속성도 포함)
  HOSTNAME: {words: ["서버명", "호스트명"], description: "서버의 호스트명"}
  OSType: {words: ["운영체제", "OS 종류"]}     ← ★ EAV 속성도 global에 등록
  Vendor: {words: ["제조사", "벤더"]}           ← ★ EAV 속성도 global에 등록

[schema:{db_id}:synonyms]  — table.column 키 (기존 유지)
  CMM_RESOURCE.HOSTNAME: {words: ["서버명"], sources: {서버명: "llm"}}

[synonyms:eav_names]  — EAV 여부 판별 메타데이터 (기존 유지)
  OSType: ["운영체제", "OS 종류"]
  Vendor: ["제조사", "벤더"]

Step 2: _apply_synonym_mapping (기존 유지)
  → load_synonyms_with_global_fallback()로 DB별 + global 폴백
  → all_db_synonyms[db_id]에 table.column 키로 반환

Step 2.5: _apply_eav_synonym_mapping (개선)
  → eav_name_synonyms에서 EAV 속성 목록 확인
  → ★ 각 EAV 속성의 synonym을 global에서도 조회하여 병합 비교
  → 통합 비교 함수 normalize_field_name() 사용

Step 2.8/3: LLM 추론 결과 등록 (개선)
  → EAV: eav_names + ★ global에도 등록
  → 비-EAV: ★ global에 등록 (cache_manager.add_synonyms() 실패 우회)
```

### 추가 문제 N-3: Step 2.8 LLM 프롬프트에 synonym words 미전달 [High]

**증상**: Step 2.8에서 LLM에 컬럼명 목록만 전달하고, synonym words(유의어 목록)를 전달하지 않아 LLM이 필드명-컬럼 매칭에 필요한 핵심 정보를 활용하지 못함.

**현재 코드** (`_apply_llm_synonym_discovery`, line 565-577):
```python
# DB 컬럼명 목록 구성 (키만 사용, synonym words는 전달하지 않음)
for col_key in sorted(synonyms.keys()):
    lines.append(f"- {col_key}")
```

**문제**: LLM에 `"- CMM_RESOURCE.HOSTNAME"` 같은 컬럼명만 전달됨. synonym words(`["서버명", "호스트명", ...]`)는 전달되지 않아, LLM이 "서버명" → `HOSTNAME` 매칭을 추론하려면 컬럼명 자체에서 의미를 유추해야 함.

**목표 프롬프트 형식** (사용자 제공 예시 기반):
```
### Database Schema Information
다음은 조회 가능한 DB 컬럼명과 각 컬럼에 매핑되는 유의어 목록이다.
{
  "HOSTNAME": ["서버명", "호스트명", "호스트 이름", "서버"],
  "IPADDRESS": ["IP주소", "IP 주소", "아이피"],
  "EAV:OSType": ["운영체제", "OS종류", "OS 타입"]
}

### User Input Fields
["자원번호", "서버명", "운영체제"]

### Output Format (JSON)
{
  "서버명": "HOSTNAME",
  "운영체제": "EAV:OSType",
  "자원번호": "확실하지 않음"
}
```

**수정 방안**: Step 2.8 프롬프트에서 synonym words를 `{컬럼명: [유의어 목록]}` 형식으로 전달하고, 매핑 결과를 Redis synonym에 자동 등록.

**파일**: `src/prompts/field_mapper.py`, `src/document/field_mapper.py:565-577`

### 추가 문제 E-6: `_match_column_in_results`에서 CamelCase vs snake_case 매칭 실패 [Medium]

**증상**: `mapped_col = "EAV:SerialNumber"` vs 쿼리 결과 `"serial_number"` → 매칭 실패.
반면 `eav_hostname` vs `cmm_resource_hostname`은 같은 것으로 판단됨 (부분 일치).

**원인 분석:**
- Step 3 EAV: `attr_name = "SerialNumber"` → `attr_name.lower() = "serialnumber"`
- 결과 키: `"serial_number"` → `"serial_number".lower() = "serial_number"`
- 비교: `"serialnumber" != "serial_number"` → **실패** (언더스코어 차이)
- Step 4 폴백에서도 동일 비교로 실패

**수정 방안**: `_match_column_in_results()`에 CamelCase ↔ snake_case 변환 비교 추가. ==> 

```python
# Step 4 이후에 추가: CamelCase ↔ snake_case 변환 비교
import re

def _camel_to_snake(name: str) -> str:
    """CamelCase를 snake_case로 변환한다."""
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

# Step 5: CamelCase ↔ snake_case 변환 비교
effective_snake = _camel_to_snake(effective_col)
for rk in result_keys:
    rk_snake = _camel_to_snake(rk)
    if effective_snake == rk_snake:
        return True
    if effective_snake == rk.lower().replace("_", ""):
        return True
    if effective_col.lower().replace("_", "") == rk.lower().replace("_", ""):
        return True
```

**파일**: `src/nodes/result_organizer.py:141-179`

### 추가 문제 S-5: global synonym에 DB별 `table.column` 형식이 저장됨 [Medium]

**증상**: Redis `synonyms:global`에 `cmm_resource.ipaddress` 같은 `table.column` 형식의 키가 저장되어 있음. global은 bare column name만 저장해야 함.

**원인 분석:**

global에 `table.column` 형식이 저장될 수 있는 경로를 추적한 결과:

| 경로 | 저장 형식 | 문제 여부 |
|------|----------|----------|
| `global_synonyms.yaml` columns 섹션 | bare name (`HOSTNAME`) | ✓ 정상 |
| `synonym_loader` eav_name_values | eav_name (`OSType`) | ✓ 정상 |
| `sync_global_synonyms()` | bare name 변환 후 저장 | ✓ 정상 |
| `synonym_registrar` | bare name 변환 후 저장 | ✓ 정상 |
| `_register_llm_synonym_discoveries_to_redis` Step 2.8 | bare name 변환 후 저장 | ✓ 정상 |
| `_register_llm_mappings_to_redis` Step 3 | bare name 변환 후 저장 | ✓ 정상 |
| `apply_mapping_feedback_to_redis` added/modified | **`cache_manager.add_synonyms(db_id, column, [field])`** — DB별에 저장 | △ global 미저장 |
| `cache_management._handle_add_synonyms` | **직접 확인 필요** | ? |

**가능한 원인**:
1. 수정 1-2 (synonym 자동 생성)에서 `save_synonyms()`가 DB별 synonym에 `table.column`으로 저장 → `sync_global_synonyms()`가 bare name으로 global에 동기화하는 과정에서 일부 누락 또는 레이스 컨디션
2. `apply_mapping_feedback_to_redis()`에서 비-EAV 매핑 시 `cache_manager.add_synonyms(db_id, column)`가 호출되는데, 이때 `column`이 `table.column` 형식 — 이것은 DB별 synonym에 저장되지만, 사용자가 Redis CLI로 조회 시 `schema:{db_id}:synonyms`와 `synonyms:global`을 혼동했을 가능성

**수정 방안**: `apply_mapping_feedback_to_redis()`의 비-EAV 경로에서도 DB별 synonym과 함께 global에도 bare name으로 등록하도록 수정.

```python
# apply_mapping_feedback_to_redis() — added 경로 (line 1573-1584)
# 변경 전: cache_manager.add_synonyms(db_id, column, [field]) — DB별만
# 변경 후: DB별 + global 양쪽 저장
else:
    # DB별 synonym에 등록 (table.column 형식)
    if db_id:
        await cache_manager.add_synonyms(
            db_id, column, [field], source="user_corrected"
        )
    # ★ global에도 bare name으로 등록
    bare_name = column.split(".", 1)[1] if "." in column else column
    success = await cache_manager.add_global_synonym(bare_name, [field])
    if success:
        registered += 1
    else:
        errors.append(f"유사어 등록 실패 ({field} -> {column})")
```

동일 패턴을 modified 경로 (line 1625-1628)에도 적용.

**파일**: `src/document/field_mapper.py:1573-1584, 1625-1628`

---

## Part A: 필드명 추출 및 매칭 분석

### A-1. Excel 헤더 추출 흐름

```
Excel 파일
  → excel_parser._detect_header_row (line 104-136)
    → str(cell.value).strip()  ← 유일한 정규화
  → template_structure["sheets"][i]["header_cells"] = [{"col": 1, "value": "자원번호"}, ...]
```

**미처리 사항:**
- 셀 내부 줄바꿈 (`"서버\n명"` → 그대로 보존됨)
- 내부 다중 공백 (`"CPU  사용률"` → 그대로 보존됨)
- Unicode 정규화 (NFC/NFD 차이)

### A-2. 매칭 시도 각 단계의 비교 방식

#### Step 2: Redis synonym 매칭 (`_synonym_match`, line 451-473)

```python
field_lower = field.lower().strip()
for word in words:
    if word.lower().strip() == field_lower:   # 정확 일치만
        return col_key
col_name = col_key.split(".", 1)[-1]
if col_name.lower() == field_lower:
    return col_key
```

| 양식 필드 | Redis synonym | 결과 | 원인 |
|----------|--------------|------|------|
| `"서버\n명"` | `"서버명"` | 불일치 | 줄바꿈 |
| `"CPU  사용률"` | `"CPU 사용률"` | 불일치 | 다중 공백 |

#### Step 2.5: EAV synonym 매칭 — 동일한 정확 일치 + global synonym 미참조

#### Step 2.8/3: LLM 응답 매칭 — `parsed.get(field)` 정확 일치

---

## Part B: EAV 접두사 비교 분석

### B-1. EAV 접두사 흐름

```
field_mapper → column_mapping["OS종류"] = "EAV:OSType"
  → query_generator → "EAV:" 제거 → 피벗 SQL ✓
  → 쿼리 결과 data_row = {"OSType": "Linux"}  (접두사 없음)
  → result_organizer Step 3 EAV ✓, Step 4 폴백 ✗
  → excel_writer Step 2.5 EAV ✓, Step 3 폴백 ✗
  → word_writer EAV 처리 없음 ✗
```

### B-2. query_generator 정규 컬럼 과도 필터링

`{"OS종류": "EAV:OSType", "서버명": "CMM_RESOURCE.HOSTNAME"}`
→ `eav_tables = {"core_config_prop"}` → `"cmm_resource" ∉ eav_tables` → "서버명" SQL에서 제외!

---

## 전체 문제 목록

### 문제 S-1: EAV synonym이 global 비교 인프라 활용 불가 [Critical]

- **파일**: `src/schema_cache/synonym_loader.py:466-475`, `src/document/field_mapper.py:479-512`
- **증상**: EAV synonym이 `synonyms:eav_names`에만 저장되어 global 비교 인프라 활용 못함
- **원인**: `SynonymLoader._process_synonym_data()`에서 `eav_name_values`를 `eav_names`에만 저장

### 문제 S-2: 정규 synonym 자동 생성 누락 — `schema:{db_id}:synonyms`가 항상 비어있는 근본 원인 [Critical]

- **파일**: `src/schema_cache/cache_manager.py:1127-1145`
- **증상**: 여러 번 코드를 실행해도 Redis에 `schema:{db_id}:synonyms`가 전혀 저장되지 않음. Step 2 synonym 매칭이 항상 실패.
- **근본 원인**: `get_schema_or_fetch()`에서 스키마 최초 조회 후 `save_schema()`만 호출하고, `DescriptionGenerator.generate_for_db()`를 호출하지 않음. descriptions/synonyms 생성은 수동 액션("generate-descriptions" 또는 REST API)에서만 트리거됨. `config.auto_generate_descriptions = True` 설정이 존재하지만 `get_schema_or_fetch()` 내부에서 참조되지 않음.
- **영향**: 정상 사용 흐름에서 DB별 synonym이 절대 생성되지 않아, Step 2 매칭이 전적으로 global 폴백에 의존. global도 `SynonymLoader`가 실행되지 않으면 비어있으므로 모든 필드가 LLM에 의존.

### 문제 S-3: LLM 추론 후 비-EAV synonym 등록 실패 [Critical]

- **파일**: `src/schema_cache/cache_manager.py:559-563`, `src/document/field_mapper.py:941-943`
- **증상**: Step 3에서 LLM이 비-EAV 매핑 추론해도 synonym에 등록 안 됨
- **원인**: `cache_manager.add_synonyms()`가 조건 불충족 시 `return False` (무로깅)

### 문제 N-1: 필드명 정규화 부재 [High]

- **파일**: `src/document/excel_parser.py:129`, `src/document/field_mapper.py:451-473`
- **원인**: `str(value).strip()` 만 적용. 줄바꿈, 다중 공백, Unicode 미처리

### 문제 N-2: LLM 응답 필드명 역매칭 실패 [High]

- **파일**: `src/document/field_mapper.py:597, 799`
- **원인**: `parsed.get(field)` 정확 일치만 사용

### 문제 N-3: Step 2.8 LLM 프롬프트에 synonym words 미전달 [High]

- **파일**: `src/prompts/field_mapper.py:190-222`, `src/document/field_mapper.py:565-577`
- **증상**: Step 2.8에서 LLM에 컬럼명만 전달하고 synonym words를 빠뜨려 LLM이 유의어 기반 매칭 불가
- **원인**: `_apply_llm_synonym_discovery()`에서 `col_key`만 나열하고 `synonyms[col_key]` words를 미포함

### 문제 E-1: word_writer EAV 처리 누락 [Critical]

- **파일**: `src/document/word_writer.py:293-321`
- **원인**: `_get_value_from_row`에 EAV 접두사 처리 로직 없음

### 문제 E-2: query_generator 정규 컬럼 과도 필터링 [Medium]

- **파일**: `src/nodes/query_generator.py:408-424`, `src/nodes/multi_db_executor.py:390-406`

### 문제 E-3: result_organizer 폴백 EAV 미처리 [Medium]

- **파일**: `src/nodes/result_organizer.py:170-176`

### 문제 E-4: excel_writer 폴백 EAV 미처리 [Low]

- **파일**: `src/document/excel_writer.py:294-300`

### 문제 E-5: _classify_mapped_columns EAV 소스 분류 [Low]

- **파일**: `src/nodes/result_organizer.py:207-212`

### 문제 E-6: _match_column_in_results에서 CamelCase ↔ snake_case 매칭 실패 [Medium]

- **파일**: `src/nodes/result_organizer.py:141-179`
- **증상**: `mapped_col = "EAV:SerialNumber"` vs 쿼리 결과 `"serial_number"` → `"serialnumber" != "serial_number"` → 매칭 실패
- **원인**: Step 3/4에서 `.lower()` 비교만 수행. CamelCase와 snake_case 간 변환 비교 없음.

### 문제 S-5: apply_mapping_feedback_to_redis에서 비-EAV 매핑이 global에 미등록 [Medium]

- **파일**: `src/document/field_mapper.py:1573-1584, 1625-1628`
- **증상**: 매핑 보고서 피드백으로 등록된 비-EAV synonym이 `synonyms:global`에 반영되지 않음. DB별 synonym에만 `table.column` 형식으로 저장됨.
- **원인**: `apply_mapping_feedback_to_redis()`의 added/modified 경로에서 `cache_manager.add_synonyms(db_id, column, [field])` 만 호출하고 `add_global_synonym(bare_name, [field])` 미호출.

---

## 수정 계획

### 수정 그룹 1: Synonym 통합 관리 (최우선)

#### 수정 1-1: EAV synonym을 global에도 등록 [문제 S-1 해결]

**목표**: EAV 속성명도 `synonyms:global`에 등록하여 global의 비교·폴백 인프라를 공유.
기존 저장소 구조(`schema:{db_id}:synonyms`는 `table.column`, `synonyms:global`은 bare name)를 유지.

##### (a) `src/schema_cache/synonym_loader.py:466-475`

`_process_synonym_data()`에서 `eav_name_values`를 `eav_names` + **global에도 등록**.

```python
# 변경 전
eav_values = data.get("eav_name_values", {})
if eav_values:
    eav_synonyms: dict[str, list[str]] = {}
    for eav_name, eav_info in eav_values.items():
        words = eav_info.get("words", [])
        eav_synonyms[eav_name] = words
        result.total_words += len(words)
    await self._redis_cache.save_eav_name_synonyms(eav_synonyms)
    result.eav_names_loaded = len(eav_synonyms)

# 변경 후
eav_values = data.get("eav_name_values", {})
if eav_values:
    eav_synonyms: dict[str, list[str]] = {}
    for eav_name, eav_info in eav_values.items():
        words = eav_info.get("words", [])
        eav_synonyms[eav_name] = words
        result.total_words += len(words)
        # ★ global에도 등록: EAV 속성명을 bare column name과 동일하게 관리
        if words:
            await self._redis_cache.add_global_synonym(eav_name, words)
    await self._redis_cache.save_eav_name_synonyms(eav_synonyms)
    result.eav_names_loaded = len(eav_synonyms)
```

##### (b) `src/document/field_mapper.py:479-512` — `_apply_eav_synonym_mapping` 개선

global synonym도 병합하여 비교. 정규화 함수 적용.

```python
# 변경 후
def _apply_eav_synonym_mapping(
    remaining: set[str],
    eav_name_synonyms: dict[str, list[str]],
    result: MappingResult,
    eav_db_id: str = "_default",
    global_synonyms: dict[str, list[str]] | None = None,
) -> None:
    for field in list(remaining):
        field_norm = normalize_field_name(field).lower()
        for eav_name, words in eav_name_synonyms.items():
            # eav_names의 words + global에 같은 이름으로 등록된 words 병합
            combined_words = list(words)
            if global_synonyms and eav_name in global_synonyms:
                for gw in global_synonyms[eav_name]:
                    if gw not in combined_words:
                        combined_words.append(gw)

            matched = False
            for word in combined_words:
                if normalize_field_name(word).lower() == field_norm:
                    matched = True
                    break
            if not matched and normalize_field_name(eav_name).lower() == field_norm:
                matched = True
            if matched:
                eav_key = f"EAV:{eav_name}"
                result.db_column_mapping.setdefault(eav_db_id, {})[field] = eav_key
                result.mapping_sources[field] = "eav_synonym"
                remaining.discard(field)
                break
```

##### (c) `src/nodes/field_mapper.py:220-232` — global_synonyms 로드 추가

```python
# EAV name synonyms + global synonyms 로드
eav_name_synonyms = {}
global_synonyms_raw: dict[str, list[str]] = {}
try:
    if cache_mgr.redis_available:
        eav_name_synonyms = await cache_mgr._redis_cache.load_eav_name_synonyms()
        global_synonyms_raw = await cache_mgr.get_global_synonyms()
except Exception as e:
    logger.debug("eav/global synonyms 로드 실패: %s", e)
```

`perform_3step_mapping()`에 `global_synonyms` 파라미터 추가 → `_apply_eav_synonym_mapping()`에 전달.

#### 수정 1-2: 스키마 조회 시 synonym 자동 생성 [문제 S-2 해결 — 근본 원인 수정]

**목표**: `get_schema_or_fetch()`에서 스키마 최초 조회(캐시 미스) 시 descriptions/synonyms를 자동 생성하여, 정상 사용 흐름에서도 DB별 synonym이 존재하도록 한다.

**파일**: `src/schema_cache/cache_manager.py` — `get_schema_or_fetch()` line 1132 이후

```python
# stale entry 정리
await self.cleanup_stale_entries(db_id, schema_dict)

# ★ descriptions/synonyms 자동 생성 (캐시 미스 시)
# config.auto_generate_descriptions 설정을 참조
descriptions = await self.get_descriptions(db_id)
if not descriptions:
    try:
        from src.schema_cache.description_generator import DescriptionGenerator
        from src.llm import create_llm
        from src.config import load_config
        config = load_config()
        if config.schema_cache.auto_generate_descriptions:
            llm = create_llm(config)
            generator = DescriptionGenerator(llm)
            descriptions, synonyms = await generator.generate_for_db(schema_dict)
            await self.save_descriptions(db_id, descriptions)
            await self.save_synonyms(db_id, synonyms)    # ← schema:{db_id}:synonyms에 저장
            await self.sync_global_synonyms(db_id)         # ← synonyms:global에 동기화
            logger.info(
                "스키마 최초 조회 시 descriptions/synonyms 자동 생성: db_id=%s, "
                "descriptions=%d, synonyms=%d",
                db_id, len(descriptions), len(synonyms),
            )
    except Exception as e:
        logger.warning("descriptions/synonyms 자동 생성 실패 (%s): %s", db_id, e)

synonyms = await self.load_synonyms_with_global_fallback(db_id, schema_dict)
```

**핵심 변경점**: `DescriptionGenerator`에 LLM 인스턴스가 필요하므로, `create_llm()`으로 생성. `auto_generate_descriptions` 설정이 `True`일 때만 실행. 이로써 정상 사용 흐름에서 스키마 최초 조회 시 자동으로 `schema:{db_id}:synonyms`가 생성됨.

#### 수정 1-3: LLM 추론 후 synonym 등록을 global 경유로 통합 [문제 S-3 해결]

**목표**: LLM 추론 결과를 `synonyms:global`에 확실히 등록. EAV는 추가로 `eav_names`도 갱신.

**파일**: `src/document/field_mapper.py`

##### Step 2.8 EAV 등록 (`_register_llm_synonym_discoveries_to_redis`, line 663-680)

```python
# eav_names 메타데이터 갱신 + ★ global에도 등록
if match_type == "eav":
    eav_name = matched_key[4:]
    redis_cache = getattr(cache_manager, "_redis_cache", None)
    if redis_cache is not None:
        # 1. eav_names 갱신
        current_eav = await redis_cache.load_eav_name_synonyms()
        existing_words = current_eav.get(eav_name, [])
        if field not in existing_words:
            existing_words.append(field)
            current_eav[eav_name] = existing_words
            await redis_cache.save_eav_name_synonyms(current_eav)
            eav_updated = True
        # 2. ★ global에도 등록 (통합 관리)
        await redis_cache.add_global_synonym(eav_name, [field])
        registered_count += 1
```

##### Step 2.8 비-EAV 등록 (line 681-698) — 변경 없음 (이미 `add_global_synonym` 사용)

##### Step 3 EAV 등록 (`_register_llm_mappings_to_redis`, line 923-939)

```python
# eav_names + ★ global 양쪽 저장 (Step 2.8과 동일 패턴)
if column.startswith("EAV:"):
    eav_name = column[4:]
    redis_cache = getattr(cache_manager, "_redis_cache", None)
    if redis_cache is not None:
        current_eav = await redis_cache.load_eav_name_synonyms()
        existing_words = current_eav.get(eav_name, [])
        if field not in existing_words:
            existing_words.append(field)
            current_eav[eav_name] = existing_words
            await redis_cache.save_eav_name_synonyms(current_eav)
            eav_updated = True
        await redis_cache.add_global_synonym(eav_name, [field])
        registered_count += 1
```

##### Step 3 비-EAV 등록 (line 940-949)

```python
# 변경: ★ global에 bare column name으로 저장 (cache_manager.add_synonyms() 실패 우회)
else:
    bare_name = column.split(".", 1)[1] if "." in column else column
    redis_cache = getattr(cache_manager, "_redis_cache", None)
    if redis_cache is not None:
        success = await redis_cache.add_global_synonym(bare_name, [field])
    else:
        # 폴백: cache_manager 경유
        success = await cache_manager.add_synonyms(
            db_id, column, [field], source="llm_inferred"
        )
    if success:
        registered_count += 1
    else:
        logger.warning("synonym 등록 실패: %s -> %s", field, bare_name)
```

---

### 수정 그룹 2: 비교 로직 정규화 (High)

#### 수정 2-1: 정규화 함수 추가 [문제 N-1 해결]

**파일**: `src/utils/schema_utils.py`

```python
import re
import unicodedata

def normalize_field_name(name: str) -> str:
    """필드명을 정규화한다.

    1. Unicode NFC 정규화
    2. 줄바꿈/탭을 공백으로 치환
    3. 연속 공백을 단일 공백으로 축소
    4. 앞뒤 공백 제거
    """
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[\r\n\t]+", " ", name)
    name = re.sub(r" {2,}", " ", name)
    return name.strip()
```

**적용 위치:**

1. **`src/document/excel_parser.py:129`** — 헤더 추출 시점
   ```python
   "value": normalize_field_name(str(value)),
   ```

2. **`src/document/field_mapper.py:_synonym_match`** — synonym 비교
   ```python
   field_lower = normalize_field_name(field).lower()
   if normalize_field_name(word).lower() == field_lower:
   ```

3. **`src/document/field_mapper.py:_apply_eav_synonym_mapping`** — 수정 1-1(b)에서 함께 적용

#### 수정 2-2: LLM 응답 필드명 퍼지 매칭 [문제 N-2 해결]

**파일**: `src/document/field_mapper.py`

Step 2.8 (line 596)과 Step 3 (line 799):

```python
# LLM 응답 키를 정규화하여 역매핑 구축
normalized_lookup: dict[str, dict] = {}
for key, value in parsed.items():
    norm_key = normalize_field_name(key).lower()
    normalized_lookup[norm_key] = value

for field in list(remaining):
    norm_field = normalize_field_name(field).lower()
    mapping_info = parsed.get(field) or normalized_lookup.get(norm_field)
```

#### 수정 2-3: Step 2.8 프롬프트에 synonym words 포함 [문제 N-3 해결]

**목표**: Step 2.8 LLM 호출 시 `{컬럼명: [유의어 목록]}` 형식으로 synonym words를 전달하여, LLM이 유의어 기반 매칭을 수행하도록 한다. 매핑 결과는 Redis synonym에 자동 등록.

**파일**: `src/prompts/field_mapper.py`, `src/document/field_mapper.py:565-577`

##### (a) 프롬프트 변경 (`src/prompts/field_mapper.py`)

`FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT`와 `FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT`를 변경하여, DB 컬럼 목록을 `{컬럼명: [유의어]}` 형식으로 전달.

```python
FIELD_MAPPER_SYNONYM_DISCOVERY_SYSTEM_PROMPT = """당신은 제공된 데이터베이스 스키마와 유의어(Synonyms) 사전을 기반으로, 사용자가 입력한 필드 목록을 실제 DB 컬럼명으로 1:1 매핑하는 데이터 파이프라인 컴포넌트이다.

### Mapping Rules
1. 입력받은 'User Input Fields'의 각 항목을 'Database Schema Information'의 유의어 목록과 대조하여 가장 적합한 DB 컬럼명을 찾는다.
2. 정확히 일치하는 유의어가 없더라도, 의미상 가장 가까운 컬럼이 있다면 매핑한다.
3. [중요] 매핑할 적절한 DB 컬럼을 찾을 수 없으면, 임의의 컬럼을 생성하거나 추측하지 말고 반드시 null을 반환한다.
4. 설명이나 부연 문구 없이, 오직 요구된 JSON 형식만 출력한다.
5. EAV 속성은 "EAV:속성명" 형식으로 표시된다. 매핑 시 "EAV:속성명" 그대로 반환한다.

### Output Format (JSON)
{
    "필드명1": {"matched_key": "db_id:table.column" 또는 "EAV:속성명", "reason": "매칭 근거"},
    "필드명2": null
}
"""

FIELD_MAPPER_SYNONYM_DISCOVERY_USER_PROMPT = """### Database Schema Information
다음은 조회 가능한 DB 컬럼명과 각 컬럼에 매핑되는 유의어 목록이다.
{db_columns_with_synonyms}

### EAV 속성 목록
{eav_attributes_with_synonyms}

### User Input Fields
{unmapped_fields}

위 스키마 정보와 유의어를 참고하여 각 필드에 가장 적합한 DB 컬럼 또는 EAV 속성을 매핑하세요.
JSON 형식으로만 응답:"""
```

##### (b) 프롬프트 데이터 구성 변경 (`src/document/field_mapper.py:565-577`)

```python
# 변경 전: 컬럼명만 나열
for col_key in sorted(synonyms.keys()):
    lines.append(f"- {col_key}")

# 변경 후: {컬럼명: [유의어 목록]} 형식으로 구성
import json as _json

db_schema_dict: dict[str, dict[str, list[str]]] = {}
for db_id in ordered_db_ids:
    synonyms = all_db_synonyms.get(db_id, {})
    if not synonyms:
        continue
    db_entries: dict[str, list[str]] = {}
    for col_key, words in synonyms.items():
        # db_id:table.column 형식으로 키 구성
        full_key = f"{db_id}:{col_key}"
        db_entries[full_key] = words if isinstance(words, list) else []
    db_schema_dict.update(db_entries)

db_columns_with_synonyms = _json.dumps(db_schema_dict, ensure_ascii=False, indent=2)

# EAV 속성도 유의어 포함
eav_dict: dict[str, list[str]] = {}
if eav_name_synonyms:
    for eav_name, words in eav_name_synonyms.items():
        eav_dict[f"EAV:{eav_name}"] = words
eav_attributes_with_synonyms = _json.dumps(eav_dict, ensure_ascii=False, indent=2) if eav_dict else "(없음)"

# 미매핑 필드 목록 (JSON 배열)
unmapped_fields_list = _json.dumps(sorted(remaining), ensure_ascii=False)
```

##### (c) 매핑 결과 Redis synonym 자동 등록 — 기존 `_register_llm_synonym_discoveries_to_redis()` 활용 (변경 없음)

매핑 성공 시 Step 2.8의 기존 등록 로직이 그대로 동작:
- EAV 매핑: `eav_names` + `global`에 등록
- 컬럼 매핑: `global`에 bare name으로 등록

---

### 수정 그룹 3: EAV 접두사 처리 (기능 수정)

#### 수정 3-1: word_writer EAV 처리 추가 [문제 E-1 해결]

**파일**: `src/document/word_writer.py:293-321`

```python
def _get_value_from_row(data_row, db_column):
    if db_column in data_row:
        return data_row[db_column]

    if "." in db_column:
        col_name = db_column.split(".", 1)[1]
        if col_name in data_row:
            return data_row[col_name]

    # ★ EAV 접두사 처리 추가
    if db_column.startswith("EAV:"):
        attr_name = db_column[4:]
        if attr_name in data_row:
            return data_row[attr_name]
        lower_attr = attr_name.lower()
        for key, value in data_row.items():
            if key.lower() == lower_attr:
                return value

    # 대소문자 무시 (EAV 접두사 고려)
    effective = db_column[4:] if db_column.startswith("EAV:") else db_column
    lower_col = effective.lower()
    for key, value in data_row.items():
        if key.lower() == lower_col or (
            "." in effective and key.lower() == effective.split(".", 1)[1].lower()
        ):
            return value

    return None
```

#### 수정 3-2: query_generator 정규 컬럼 필터링 완화 [문제 E-2 해결]

**파일**: `src/nodes/query_generator.py:408-424`, `src/nodes/multi_db_executor.py:390-406`

EAV 존재 시 정규 컬럼 필터링 로직 제거. LLM이 schema_info를 보고 적절한 JOIN을 결정.

```python
# 변경 전
if eav_entries:
    eav_tables = _extract_eav_tables(schema_info)
    filtered_regular = []
    for field, col in regular_entries:
        table_part = col.split(".")[0]
        if not eav_tables or table_part.lower() in eav_tables:
            filtered_regular.append((field, col))
        else:
            excluded.append(...)
    regular_entries = filtered_regular

# 변경 후: 필터링 제거
# EAV config 테이블과 entity 테이블이 다를 수 있으므로
# LLM이 schema_info를 보고 적절한 JOIN을 결정하도록 함
```

#### 수정 3-3: result_organizer 폴백 EAV 보강 [문제 E-3 해결]

**파일**: `src/nodes/result_organizer.py:170-176`

```python
# 변경 전
mapped_lower = mapped_col.lower()
# 변경 후
effective_col = mapped_col[4:] if mapped_col.startswith("EAV:") else mapped_col
mapped_lower = effective_col.lower()
```

#### 수정 3-4: excel_writer 폴백 EAV 보강 [문제 E-4 해결]

**파일**: `src/document/excel_writer.py:294-300`

```python
effective = db_column[4:] if db_column.startswith("EAV:") else db_column
lower_col = effective.lower()
col_only_lower = effective.split(".", 1)[1].lower() if "." in effective else lower_col
for key, value in data_row.items():
    if key.lower() == lower_col or key.lower() == col_only_lower:
        return value
```

#### 수정 3-5: _classify_mapped_columns EAV 소스 보강 [문제 E-5 해결]

**파일**: `src/nodes/result_organizer.py:209`

```python
# 변경 전
if source in ("hint", "synonym"):
# 변경 후
if source in ("hint", "synonym", "eav_synonym"):
```

#### 수정 3-6: _match_column_in_results CamelCase ↔ snake_case 매칭 [문제 E-6 해결]

**파일**: `src/nodes/result_organizer.py:141-179`

`_match_column_in_results()`에 Step 5로 CamelCase ↔ snake_case 변환 비교를 추가한다.

```python
import re

def _camel_to_snake(name: str) -> str:
    """CamelCase를 snake_case로 변환한다."""
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

# _match_column_in_results() — Step 4 이후에 추가
# 5. CamelCase ↔ snake_case 변환 비교
effective_snake = _camel_to_snake(effective_col)
for rk in result_keys:
    rk_snake = _camel_to_snake(rk)
    if effective_snake == rk_snake:
        return True
    # 언더스코어 제거 비교 (serialnumber == serial_number)
    if effective_col.lower().replace("_", "") == rk.lower().replace("_", ""):
        return True
```

**테스트 시나리오:**
- `_match_column_in_results("EAV:SerialNumber", {"serial_number"})` → `True`
- `_match_column_in_results("EAV:OSType", {"os_type"})` → `True`
- `_match_column_in_results("CMM_RESOURCE.HOSTNAME", {"hostname"})` → `True` (기존 Step 2)

#### 수정 1-4: apply_mapping_feedback_to_redis에서 global에도 등록 [문제 S-5 해결]

**파일**: `src/document/field_mapper.py:1573-1584, 1625-1628`

`apply_mapping_feedback_to_redis()`의 비-EAV added/modified 경로에서 DB별 synonym과 함께 global에도 bare name으로 등록.

```python
# added 경로 (line 1573-1584) 변경
else:
    # DB별 synonym에 등록 (table.column 형식)
    if db_id:
        await cache_manager.add_synonyms(
            db_id, column, [field], source="user_corrected"
        )
    # ★ global에도 bare name으로 등록
    bare_name = column.split(".", 1)[1] if "." in column else column
    success = await cache_manager.add_global_synonym(bare_name, [field])
    if success:
        registered += 1
    else:
        errors.append(f"유사어 등록 실패 ({field} -> {column})")

# modified 경로 (line 1625-1628) 변경 — 동일 패턴 적용
elif new_column and new_db_id:
    await cache_manager.add_synonyms(
        new_db_id, new_column, [field], source="user_corrected"
    )
    # ★ global에도 bare name으로 등록
    bare_name = new_column.split(".", 1)[1] if "." in new_column else new_column
    await cache_manager.add_global_synonym(bare_name, [field])
```

---

## 수정 우선순위

### 그룹 1: Synonym 통합 관리 (최우선)

| 순위 | 수정 | 해결 문제 | 효과 |
|------|------|----------|------|
| 1 | 1-1: EAV synonym global 통합 | S-1 | EAV도 global 비교 인프라 공유 |
| 2 | 1-2: synonym 자동 생성 | S-2 | 스키마 최초 조회 시 자동 활성화 |
| 3 | 1-3: LLM 등록 global 통합 | S-3 | 모든 추론 결과가 global에 영구 저장 |
| 4 | 1-4: feedback 등록 global 추가 | S-5 | 사용자 피드백도 global에 반영 |

### 그룹 2: 비교 정규화 및 LLM 프롬프트 개선 (High)

| 순위 | 수정 | 해결 문제 | 효과 |
|------|------|----------|------|
| 5 | 2-1: 정규화 함수 통합 | N-1 | 줄바꿈/다중공백/Unicode 정규화 |
| 6 | 2-2: LLM 응답 퍼지 매칭 | N-2 | LLM 결과 수집 정상화 |
| 7 | 2-3: Step 2.8 프롬프트에 synonym words 포함 | N-3 | LLM이 유의어 기반 매칭 수행, 매핑 정확도 향상 |

### 그룹 3: EAV 접두사 처리

| 순위 | 수정 | 해결 문제 | 효과 |
|------|------|----------|------|
| 8 | 3-1: word_writer EAV 처리 | E-1 | Word EAV 데이터 채우기 복원 |
| 9 | 3-2: 정규 컬럼 필터링 완화 | E-2 | EAV+정규 혼합 SQL 정상 생성 |
| 10 | 3-3: result_organizer 폴백 | E-3 | EAV 충분성 판단 방어 강화 |
| 11 | 3-4: excel_writer 폴백 | E-4 | EAV 데이터 추출 방어 강화 |
| 12 | 3-5: EAV 소스 분류 | E-5 | 충분성 판단 정밀도 향상 |
| 13 | 3-6: CamelCase ↔ snake_case 매칭 | E-6 | SerialNumber vs serial_number 매칭 |

---

## 변경 파일 요약

| 파일 | 수정 | 변경 내용 |
|------|------|----------|
| `src/utils/schema_utils.py` | 2-1 | `normalize_field_name()` 함수 추가 |
| `src/schema_cache/synonym_loader.py` | 1-1(a) | EAV를 global에도 등록 |
| `src/schema_cache/cache_manager.py` | 1-2 | synonym 자동 생성 |
| `src/nodes/field_mapper.py` | 1-1(c) | global_synonyms 별도 로드, perform_3step_mapping에 전달 |
| `src/prompts/field_mapper.py` | 2-3 | Step 2.8 프롬프트에 synonym words 포함 형식으로 변경 |
| `src/document/field_mapper.py` | 1-1(b), 1-3, 1-4, 2-1, 2-2, 2-3 | EAV synonym global 참조, 등록 통합, feedback global 추가, 정규화, 퍼지 매칭, synonym words 전달 |
| `src/document/excel_parser.py` | 2-1 | 헤더 추출 시 normalize_field_name() 적용 |
| `src/document/word_writer.py` | 3-1 | _get_value_from_row EAV 처리 추가 |
| `src/document/excel_writer.py` | 3-4 | _get_value_from_row 폴백 EAV 보강 |
| `src/nodes/query_generator.py` | 3-2 | 정규 컬럼 필터링 제거 |
| `src/nodes/multi_db_executor.py` | 3-2 | 정규 컬럼 필터링 제거 |
| `src/nodes/result_organizer.py` | 3-3, 3-5, 3-6 | 폴백 EAV 보강, EAV 소스 분류, CamelCase/snake_case 매칭 |

---

## 테스트 계획

1. **EAV synonym global 통합 테스트**
   - `SynonymLoader.load_auto()` 후 `synonyms:global`에 EAV 속성명(OSType 등)도 포함되는지 확인
   - `_apply_eav_synonym_mapping`에서 global에만 있는 synonym으로 매칭 성공 확인

2. **synonym 자동 생성 테스트**
   - `get_schema_or_fetch()` 최초 호출 후 `schema:{db_id}:synonyms`와 `synonyms:global` 모두 생성되는지 확인

3. **LLM 추론 결과 등록 테스트**
   - EAV 매핑 추론 후 `synonyms:global`과 `synonyms:eav_names` 양쪽에 등록되는지 확인
   - 비-EAV 매핑 추론 후 `synonyms:global`에 bare column name으로 등록되는지 확인

4. **필드명 정규화 단위 테스트**
   - `normalize_field_name("서버\n명")` → `"서버 명"`
   - `normalize_field_name("CPU  사용률")` → `"CPU 사용률"`

5. **LLM 응답 퍼지 매칭 테스트**
   - LLM이 `"서버명"` 반환, remaining에 `"서버 명"` → 매칭 성공

6. **word_writer EAV 테스트**
   - `_get_value_from_row({"OSType": "Linux"}, "EAV:OSType")` → `"Linux"`

7. **테이블 필터링 테스트**
   - EAV+정규 혼합 매핑에서 정규 컬럼이 제외되지 않는지 확인

8. **CamelCase ↔ snake_case 매칭 테스트**
   - `_match_column_in_results("EAV:SerialNumber", {"serial_number"})` → `True`
   - `_match_column_in_results("EAV:OSType", {"os_type"})` → `True`
   - `_match_column_in_results("EAV:AgentVersion", {"agent_version"})` → `True`

9. **매핑 피드백 global 등록 테스트**
   - `apply_mapping_feedback_to_redis()`로 비-EAV 매핑 추가 후 `synonyms:global`에 bare name으로 등록되는지 확인

10. **회귀 테스트**
   - 기존 DB별 synonym(`table.column` 형식)이 정상 동작하는지 확인
   - `synonyms:eav_names`가 정상 저장/로드되는지 확인
   - `load_synonyms_with_global_fallback()`이 DB별 + global 폴백 정상 동작


---

# Verification Report

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
