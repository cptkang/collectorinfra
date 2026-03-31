# Plan 38: 필드 매핑 전파 정합성 수정 (column_mapping → SQL alias → 값 추출)

## 배경

### 문제 현상

Excel 양식 채우기 시 `result_organizer`의 데이터 충분성 검사는 통과하지만, `excel_writer`가 실제 값을 추출하지 못하여 데이터가 0건 채워진다.

### 근본 원인: 매핑 형식과 SQL alias 형식의 불일치

파이프라인의 4개 단계에서 컬럼명 형식이 달라지며, 이를 정규화하는 로직이 단계마다 다르다:

| 단계 | 컴포넌트 | 형식 예시 | 정규화 여부 |
|------|----------|----------|------------|
| Step 1 | `field_mapper` → `column_mapping` | `"cmm_resource.hostname"`, `"EAV:OSType"` | (원본 생성) |
| Step 2 | `query_generator` → SQL alias | LLM이 생성: `cmm_resource_hostname`, `os_type` 등 | **비결정적** |
| Step 3 | `result_organizer._match_column_in_results()` | CamelCase↔snake_case + 언더스코어 제거 비교 포함 | **있음** (5단계 매칭) |
| Step 4 | `excel_writer._get_value_from_row()` | 대소문자 무시 + 부분 매칭만 | **부분적** (CamelCase↔snake_case 누락) |

**핵심 간극:**
- `_match_column_in_results()`는 `_camel_to_snake()` 변환과 언더스코어 제거 비교를 포함하여 `"EAV:OSType"` → `"os_type"` 매칭이 가능하다 (L186-194)
- `_get_value_from_row()`는 이 로직이 **없어서** `"EAV:OSType"` → 결과 키 `"os_type"` 매칭에 **실패**한다
- `"cmm_resource.hostname"` → `"cmm_resource_hostname"` 매칭은 부분 매칭(substring)에 의존하여 사전 순서에 의존하는 불안정한 동작

**LLM 비결정성 문제:**
- 프롬프트에서 `AS "테이블명.컬럼명"` 형태의 alias를 지시하지만, LLM이 항상 따르지 않는다
- `MAX(CASE WHEN p.NAME='OSType' THEN p.VALUE END) AS os_type` 같은 snake_case alias를 생성하는 경우가 빈번
- 규칙 기반 정규화(CamelCase↔snake_case)로도 해결 불가능한 창의적 alias가 생성될 수 있음
  - 예: `"cmm_resource.description"` → SQL alias `"resource_desc"` (축약)
  - 예: `"EAV:AgentVersion"` → SQL alias `"agent_ver"` (축약)

---

## 접근 방식 평가

| 접근 | 장점 | 단점 | 판정 |
|------|------|------|------|
| A. `_get_value_from_row` 정규화 강화 | 최소 변경, 기존 패턴 재사용 | 매번 O(n) 비교, 축약·창의적 alias 불가 | **보조 폴백** |
| B. 후처리 매핑 조정 (rule-based resolved_mapping) | 한 번만 계산, 정확한 매핑 | 규칙 한계, 축약 등 미대응 | **1차 주력** |
| C. query_generator alias 강제화 | 원천 해결 | LLM 비결정성으로 신뢰 불가 | **불가** |
| **E. LLM 유사성 판단 (post-query)** | 축약/창의적 alias 대응 가능, 의미적 매칭 | LLM 호출 추가 (비용/지연) | **2차 주력** |
| **D'. 3계층 하이브리드 (B → E → A)** | 규칙→LLM→폴백으로 가장 견고 | 총 작업량 | **채택** |

### 채택: 접근 D' (3계층 하이브리드)

```
column_mapping + result_keys
    │
    ▼
[Layer 1] 규칙 기반 매칭 (build_resolved_mapping)
    │      → 정확 매칭, table.column 분리, EAV 접두사 제거,
    │        대소문자 무시, CamelCase↔snake_case, 언더스코어 제거
    │
    ▼  (미해결 항목이 있으면)
[Layer 2] LLM 유사성 판단 (resolve_unmatched_via_llm)
    │      → 미해결 매핑값 + 결과 키 목록을 LLM에 전달
    │        의미적 유사성으로 매칭 (축약, 재명명, 오타 대응)
    │
    ▼  (merge)
resolved_mapping 완성
    │
    ▼  (excel_writer에서 값 추출 시)
[Layer 3] _get_value_from_row 폴백 정규화
           → resolved_mapping이 없는 레거시 경로 대비
```

**이유:**
1. Layer 1 (규칙 기반)이 80%+ 케이스를 지연 없이 해결
2. Layer 2 (LLM)는 미해결 항목에만 호출하므로 비용/지연 최소화 (소규모 컨텍스트)
3. Layer 3 (폴백)은 resolved_mapping이 없는 레거시 경로를 커버

---

## 상세 구현 계획

### Phase 1: 공통 정규화 유틸리티 추출

**파일**: `src/utils/column_matcher.py` (신규)

`result_organizer._match_column_in_results()`의 매칭 로직을 독립 유틸로 추출한다.

```python
def camel_to_snake(name: str) -> str:
    """CamelCase를 snake_case로 변환."""

def resolve_column_key(mapped_col: str, result_keys: set[str]) -> str | None:
    """매핑된 컬럼명을 실제 결과 키로 해석한다.

    5단계 매칭 순서:
    1. 정확 매칭
    2. "table.column" → "column" 부분 매칭
    3. "EAV:" 접두사 제거 후 매칭
    4. 대소문자 무시 매칭
    5. CamelCase↔snake_case 변환 + 언더스코어 제거 비교

    Returns:
        매칭된 실제 result key, 또는 None (매칭 실패)
    """

def build_resolved_mapping(
    column_mapping: dict[str, str | None],
    result_keys: set[str],
) -> tuple[dict[str, str | None], list[str]]:
    """column_mapping을 실제 결과 키로 해석한다.

    Returns:
        (resolved_mapping, unresolved_fields)
        - resolved_mapping: {field: 실제_result_key 또는 None}
        - unresolved_fields: 규칙 기반으로 해석 실패한 field명 목록
    """
```

**계층 위치**: `src/utils/` (config/utils 계층) — 순수 규칙 기반, LLM 의존 없음. `arch_check.py` 위반 없음.

### Phase 2: LLM 유사성 판단 프롬프트 추가

**파일**: `src/prompts/column_resolver.py` (신규)

쿼리 실행 후 미해결 매핑을 LLM으로 해석하는 전용 프롬프트를 정의한다.

```python
COLUMN_RESOLVER_SYSTEM_PROMPT = """당신은 데이터베이스 컬럼명 매칭 전문가입니다.

SQL 쿼리의 SELECT 절에서 사용된 alias(결과 키)와,
양식 필드 매핑에서 생성된 DB 컬럼 참조(매핑 값)를 비교하여
의미적으로 동일한 쌍을 찾아주세요.

## 매칭 기준
1. 동일 의미의 축약/확장 (description ↔ desc, version ↔ ver)
2. 접두사/접미사 차이 (cmm_resource.hostname ↔ cmm_resource_hostname)
3. 명명법 차이 (CamelCase ↔ snake_case: OSType ↔ os_type)
4. EAV 접두사 무시 (EAV:OSType ↔ os_type)
5. 오타/유사 철자 (OSVerson ↔ os_version)

## 출력 형식 (JSON)
매핑값을 키로, 매칭된 결과 키를 값으로 하는 JSON 객체만 출력하세요.
매칭 불가한 항목은 포함하지 마세요.

```json
{
    "EAV:OSType": "os_type",
    "cmm_resource.description": "resource_desc"
}
```"""

COLUMN_RESOLVER_USER_PROMPT = """## 미해결 매핑값 (DB 컬럼 참조)
{unresolved_columns}

## SQL 결과 키 (실제 alias)
{result_keys}

위 매핑값과 결과 키 중 의미적으로 동일한 쌍을 JSON으로 매칭하세요.
확신이 없으면 해당 항목을 제외하세요."""
```

**설계 포인트:**
- 컨텍스트가 매우 작음 (매핑값 목록 + 결과 키 목록 = 보통 20~40개 항목)
- 단순 1:1 매칭이므로 LLM 응답이 빠르고 정확
- 기존 field_mapper의 Step 2.8 (synonym discovery) 프롬프트 패턴을 재활용

### Phase 3: State에 resolved_mapping 필드 추가

**파일**: `src/state.py` (수정)

```python
class OrganizedData(TypedDict):
    summary: str
    rows: list[dict[str, Any]]
    column_mapping: Optional[dict[str, str]]
    resolved_mapping: Optional[dict[str, str]]  # 신규: 실제 결과 키로 해석된 매핑
    is_sufficient: bool
    sheet_mappings: Optional[list[SheetMappingResult]]

class SheetMappingResult(TypedDict):
    sheet_name: str
    column_mapping: Optional[dict[str, str]]
    resolved_mapping: Optional[dict[str, str]]  # 신규
    rows: list[dict[str, Any]]
```

### Phase 4: result_organizer에서 3계층 매핑 해석

**파일**: `src/nodes/result_organizer.py` (수정)

Step 4 (양식 매핑) 직후에 resolved_mapping 생성 로직을 추가한다.

```python
# Step 4.5 (신규): resolved_mapping 생성
resolved = None
if column_mapping and formatted_results:
    result_keys = set(formatted_results[0].keys())

    # Layer 1: 규칙 기반 매칭
    from src.utils.column_matcher import build_resolved_mapping
    resolved, unresolved_fields = build_resolved_mapping(column_mapping, result_keys)

    # Layer 2: 미해결 항목에 대해 LLM 유사성 판단
    if unresolved_fields:
        llm_resolved = await _resolve_unmatched_via_llm(
            llm=llm,
            column_mapping=column_mapping,
            unresolved_fields=unresolved_fields,
            result_keys=result_keys,
        )
        if llm_resolved:
            for field, resolved_key in llm_resolved.items():
                resolved[field] = resolved_key
            logger.info(
                "LLM 유사성 판단으로 %d건 추가 해석: %s",
                len(llm_resolved), llm_resolved,
            )
```

**신규 함수**: `_resolve_unmatched_via_llm()` (같은 파일 내)

```python
async def _resolve_unmatched_via_llm(
    llm: BaseChatModel | None,
    column_mapping: dict[str, str | None],
    unresolved_fields: list[str],
    result_keys: set[str],
) -> dict[str, str] | None:
    """미해결 매핑 항목에 대해 LLM 유사성 판단을 수행한다.

    Args:
        llm: LLM 인스턴스
        column_mapping: 원본 column_mapping
        unresolved_fields: 규칙 기반 매칭 실패 필드명 목록
        result_keys: SQL 결과의 실제 키 집합

    Returns:
        {field: resolved_result_key} 또는 None
    """
    # 미해결 필드의 매핑값만 추출
    unresolved_columns = {
        f: column_mapping[f] for f in unresolved_fields
        if column_mapping.get(f) is not None
    }
    if not unresolved_columns:
        return None

    # 이미 해석된 키를 후보에서 제외 (1:1 매핑 보장)
    # ... LLM 호출 후 JSON 파싱 ...
```

**`_match_column_in_results` 리팩터:** 기존 함수는 `resolve_column_key`를 래핑하여 bool을 반환하도록 변경 (하위 호환 유지).

### Phase 5: output_generator에서 resolved_mapping 우선 사용

**파일**: `src/nodes/output_generator.py` (수정)

`_generate_document_file()`에서:
```python
organized = state["organized_data"]
# resolved_mapping 우선, column_mapping 폴백
effective_mapping = organized.get("resolved_mapping") or column_mapping
```

`fill_excel_template` / `fill_word_template` 호출 시 `effective_mapping`을 전달.
Writer 함수의 시그니처 변경 불필요.

### Phase 6: excel_writer._get_value_from_row 정규화 강화 (Layer 3 폴백)

**파일**: `src/document/excel_writer.py` (수정)

기존 Step 3 (대소문자 무시) 이후, Step 5 (부분 매칭) 이전에 추가:

```python
# Step 3.5 (신규): CamelCase↔snake_case 변환 + 언더스코어 제거 비교
from src.utils.column_matcher import camel_to_snake

effective_snake = camel_to_snake(effective)
for key, value in data_row.items():
    if camel_to_snake(key) == effective_snake:
        return value
    if effective.lower().replace("_", "") == key.lower().replace("_", ""):
        return value
```

**파일**: `src/document/word_writer.py` (수정) — 동일 패턴 적용

### Phase 7: 테스트

**파일**: `tests/test_utils/test_column_matcher.py` (신규)

- `resolve_column_key` 단위 테스트
  - `"cmm_resource.hostname"` → `{"cmm_resource_hostname"}` 매칭
  - `"EAV:OSType"` → `{"os_type"}` CamelCase→snake_case 매칭
  - `"EAV:SerialNumber"` → `{"serial_number"}` 매칭
  - `"EAV:OSVerson"` → `{"os_version"}` 오타 케이스
  - 정확 매칭 우선순위 확인
- `build_resolved_mapping` 통합 테스트
  - 반환값 `(resolved, unresolved)` 검증
  - unresolved 목록 정확성 검증

**파일**: `tests/test_nodes/test_result_organizer_llm_resolver.py` (신규)

- `_resolve_unmatched_via_llm` 테스트 (LLM mock)
  - 축약 alias 해석: `"EAV:AgentVersion"` → `"agent_ver"`
  - 접두사 변경: `"cmm_resource.description"` → `"resource_desc"`
  - 매칭 불가 시 None 반환
- result_organizer 통합 테스트
  - Layer 1 (규칙) → Layer 2 (LLM) 순차 실행 확인
  - Layer 1에서 해결된 항목은 Layer 2에 전달되지 않음 확인

**파일**: `tests/test_query_to_excel_mapping.py` (수정)

- Case A EAV→snake_case 매칭 테스트가 통과하도록 갱신

---

## 변경 영향도

| 파일 | 변경 유형 | 비고 |
|------|----------|------|
| `src/utils/column_matcher.py` | **신규** | 규칙 기반 유틸 (LLM 의존 없음) |
| `src/prompts/column_resolver.py` | **신규** | LLM 유사성 판단 프롬프트 |
| `src/state.py` | 수정 (optional 필드 추가) | 하위 호환 |
| `src/nodes/result_organizer.py` | 수정 | Layer 1+2 통합, resolved_mapping 생성 |
| `src/nodes/output_generator.py` | 수정 | resolved_mapping 우선 사용 |
| `src/document/excel_writer.py` | 수정 | Layer 3 폴백 정규화 추가 |
| `src/document/word_writer.py` | 수정 | 동일 폴백 |
| `tests/test_utils/test_column_matcher.py` | **신규** | 규칙 기반 단위 테스트 |
| `tests/test_nodes/test_result_organizer_llm_resolver.py` | **신규** | LLM 해석 테스트 |
| `tests/test_query_to_excel_mapping.py` | 수정 | 케이스 갱신 |

---

## 기존 테스트 영향

- `test_plan37_eav_prefix_fix.py`: `_match_column_in_results` 시그니처 유지 → **영향 없음**
- `test_result_organizer_sufficiency.py`: 내부 구현 변경이지만 동작 동일 → **영향 없음**
- `test_excel_fill_pipeline.py`: 매칭 범위가 넓어지므로 기존 테스트 **통과 유지**
- `test_query_to_excel_mapping.py`: Case A EAV 테스트가 **새로 통과**

---

## 구현 순서 (의존관계 기반)

```
Step 1: src/utils/column_matcher.py 생성 (의존 없음)
Step 2: src/prompts/column_resolver.py 생성 (의존 없음)
Step 3: src/state.py 수정 (의존 없음)
Step 4: src/nodes/result_organizer.py 수정 (Step 1, 2, 3 의존)
Step 5: src/nodes/output_generator.py 수정 (Step 3 의존)
Step 6: src/document/excel_writer.py 수정 (Step 1 의존)
Step 7: src/document/word_writer.py 수정 (Step 1 의존)
Step 8: 테스트 작성/수정 (Step 1~7 의존)
Step 9: scripts/arch_check.py 실행으로 계층 위반 확인
```

---

## 주의사항

1. **부분 매칭 오탐 방지**: CamelCase↔snake_case 매칭을 부분 매칭보다 **앞**에 배치하여 정확한 정규화 매칭을 우선
2. **resolved_mapping None 처리**: 텍스트 출력 모드 등에서는 None → `column_mapping`으로 폴백
3. **multi_db_executor 경로**: `result_organizer`를 거치므로 resolved_mapping 생성으로 자동 커버
4. **계층 규칙 준수**: `column_matcher.py`는 `src/utils/`(순수 규칙), 프롬프트는 `src/prompts/`, LLM 호출은 `src/nodes/`(application) → arch_check 위반 없음
5. **LLM 호출 최소화**: Layer 2는 미해결 항목이 있을 때만 호출. 전체 매핑이 규칙으로 해결되면 LLM 호출 0회
6. **LLM 실패 시 graceful 처리**: Layer 2 LLM 호출 실패/파싱 실패 시 원본 매핑값 유지 → Layer 3 폴백에 위임

---

## 성공 기준

1. `test_query_to_excel_mapping.py` 전체 36개 테스트 통과 (현재 8개 실패 → 0개 실패)
2. `fill_excel_template`에 `MAPPING_TABLE_DOT_COLUMN` + `QUERY_RESULT_ROWS` 전달 시 `total_filled > 0`
3. LLM 축약 alias 케이스 (`"agent_ver"`, `"resource_desc"`) resolved_mapping에서 해석 성공
4. `scripts/arch_check.py` 위반 없음
5. 기존 테스트 전체 통과 (회귀 없음)
