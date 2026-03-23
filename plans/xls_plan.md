# Excel 양식 기반 데이터 조회 및 파일 작성 — 개선 계획

## 1. 현재 문제 분석

### 1.1 핵심 문제: SQL 생성이 Excel 양식 구조를 반영하지 못함

현재 흐름에서 **query_generator**는 `template_structure`(Excel 헤더 정보)를 프롬프트에 JSON으로 전달하고 "양식의 헤더/플레이스홀더에 해당하는 컬럼을 반드시 SELECT에 포함하세요"라는 지시만 추가한다. 하지만:

1. **LLM이 Excel 헤더와 DB 컬럼 간의 매핑을 추론해야 하는데**, 이 매핑은 별도의 `field_mapper`가 result_organizer 단계에서 수행한다. 즉 **같은 매핑 작업이 두 곳에서 독립적으로 수행**되며 결과가 일치하지 않을 수 있다.
2. **query_generator의 SQL SELECT 컬럼**과 **result_organizer의 column_mapping**이 일치하지 않으면, Excel에 값을 채울 수 없다.
3. **query_generator 프롬프트**에는 Excel 헤더를 DB 컬럼에 매핑하라는 구체적 지시가 없고, 단순히 template_structure JSON 전체를 포함하므로 LLM이 핵심 정보를 놓칠 수 있다.

### 1.2 구체적 실패 시나리오

| 시나리오 | 원인 | 결과 |
|----------|------|------|
| SQL이 Excel 헤더와 무관한 컬럼만 SELECT | query_generator가 템플릿 헤더를 무시 | 빈 Excel 파일 생성 |
| SQL 컬럼 alias와 column_mapping key 불일치 | SQL: `s.hostname`, mapping: `servers.hostname`, result key: `hostname` | _get_value_from_row 폴백에 의존 |
| 양식에 여러 도메인(CPU+Memory+Disk) 헤더 존재 | 단일 SQL로 모든 헤더를 커버 불가 | 일부 컬럼 누락 |
| 양식 헤더가 여러 DB에 걸쳐 있음 | 현재 단일 DB 기준으로 SQL 생성 | 다른 DB의 컬럼은 조회 불가 |
| 데이터 충분성 검사 오판 | _check_data_sufficiency가 result 컬럼 수와 header 수를 비교하는데, 컬럼명이 매핑 전이라 의미 없음 | 불필요한 재시도 또는 불충분 데이터 통과 |

### 1.3 근본 원인

```
현재 흐름:
  input_parser → [template_structure 추출]
  query_generator → [LLM이 template 보고 알아서 SQL 생성] ← 매핑 없이 추론
  result_organizer → [field_mapper로 매핑 수행] ← 사후 매핑
  output_generator → [매핑 기반으로 Excel 채우기]

문제: SQL 생성 시점에는 정확한 field-column 매핑이 없고,
      매핑 수행 시점에는 이미 SQL이 실행된 후라 SELECT 컬럼 변경 불가
```

---

## 2. 개선 방향

### 2.1 핵심 전략: **필드 매핑을 최우선 수행 → 매핑 결과로 대상 DB 결정 → DB별 SQL 생성**

```
개선 흐름:
  [1] input_parser
      → template_structure 추출 (Excel/Word 공통 — xlsx, doc, docx 모두 동일 처리)
      → 사용자 프롬프트에서 매핑 힌트 + 대상 DB/서비스명 추출

  [2] ★ field_mapper (신규 노드 — input_parser 직후)
      → 프롬프트에 특정 DB/서비스명이 있으면 해당 DB를 우선 조회,
        없으면 전체 활성 DB의 Redis 캐시(synonyms/descriptions)를 조회
      → 3단계 매핑 수행: 프롬프트 힌트 → synonyms 규칙 → LLM 추론
      → 매핑 결과에서 각 필드가 어떤 DB의 컬럼에 매핑되었는지 확인
      → column_mapping + db_column_mapping을 State에 저장
      → LLM 추론으로 매핑된 필드는 mapping_confidence를 "inferred"로 표시
      → template_structure와 매핑정보를 함께 저장 (최종 파일 생성 시 활용)

  ...

  [*] LLM 추론 매핑의 유사어 등록 플로우 (응답 이후)
      → 최종 응답에 LLM 추론 매핑 내역을 번호와 함께 표시
      → 사용자가 선택적으로 등록 가능:
        - "전체 등록" → 모든 추론 매핑을 한꺼번에 Redis synonyms에 등록
        - "1, 3 등록" → 번호로 지정한 항목만 개별 등록
        - "1번 등록" → 특정 1건만 등록
        - 무응답/거부 → 등록하지 않음

  [3] semantic_router
      → field_mapper의 매핑 결과(db_column_mapping)를 기반으로 대상 DB 결정
      → 매핑된 DB가 곧 target_databases

  [4] schema_analyzer → [각 DB별 스키마 조회 (검증용)]

  [5] query_generator
      → column_mapping 기반으로 DB별 정확한 SELECT 생성
      → 여러 DB가 선택되었다면 각 DB별로 독립 SQL을 생성

  [6] query_validator → query_executor (DB별 실행)
      → result_merger → result_organizer → output_generator

  [7] output_generator
      → 저장된 template_structure + column_mapping을 사용하여
         Excel 양식의 정확한 컬럼 위치에 데이터를 채움

  [*] 응답에 매핑 정보 포함
      → LLM 추론 매핑의 경우, 최종 응답에 매핑 내역을 표시하여
         사용자가 확인할 수 있도록 함
```

### 2.2 설계 원칙

1. **매핑 우선 (Mapping-First)**: 필드 매핑을 가장 먼저 수행하고, 그 결과가 대상 DB 선택, SQL 생성, 파일 생성 전체를 주도한다.
2. **Excel/Word 통합 처리**: xlsx, doc, docx 모두 동일한 파이프라인으로 처리한다. template_structure는 파일 형식에 무관하게 headers/placeholders를 추출하며, field_mapper와 query_generator는 파일 형식을 구분하지 않고 매핑 정보를 사용한다.
3. **Redis 기반 매핑 우선, LLM은 폴백**: 기본적으로 Redis에 저장된 synonyms/descriptions 정보를 기반으로 매핑한다. Redis에 정보가 없는 경우에만 LLM을 통해 매핑한다.
4. **프롬프트 내 DB/서비스명 우선 조회**: 사용자 프롬프트에 특정 DB명이나 서비스명이 언급되면, 해당 DB의 synonyms를 우선 조회하여 매핑 효율을 높인다.
5. **LLM 추론 매핑은 사용자에게 공개 + 유사어 등록 제안**: LLM 추론으로 매핑된 경우 최종 응답에 매핑 내역을 보여주고, 사용자에게 유사어 등록 여부를 질문한다. 사용자가 승인하면 Redis synonyms에 자동 등록하여 이후 동일 필드는 LLM 호출 없이 매핑된다.
6. **Single Source of Truth**: 필드 매핑은 field_mapper에서 한 번만 수행하고, query_generator와 output_generator가 동일한 매핑을 참조한다.
7. **template_structure + 매핑정보 보존**: input_parser에서 추출한 template_structure와 field_mapper의 매핑정보를 State에 저장하여, 최종 파일 생성 시 양식의 정확한 컬럼 위치에 데이터를 채울 수 있도록 한다.
8. **매핑 결과가 DB 선택을 주도**: semantic_router는 사용자 질의뿐만 아니라 field_mapper의 매핑 결과(어떤 필드가 어떤 DB에 매핑되었는지)를 참조하여 대상 DB를 결정한다.
9. **멀티 DB 지원**: 매핑 결과에서 여러 DB가 식별되면, 각 DB별로 독립 SQL을 생성·실행하고 결과를 병합한다.

---

## 3. 상세 구현 계획

### 3.1 Phase A: input_parser 확장 — 매핑 힌트 + 대상 DB 추출 + Excel/Word 통합

**변경 대상**: `src/nodes/input_parser.py`, `src/prompts/input_parser.py`

#### 3.1.1 Excel/Word 통합 처리

현재 `_parse_uploaded_file()`은 xlsx와 docx를 분기 처리하지만, 이후 field_mapper와 query_generator는 **파일 형식에 무관하게 동일한 매핑 파이프라인**을 사용한다.

- **Excel (.xlsx)**: `parse_excel_template()` → `template_structure.sheets[*].headers`
- **Word (.docx)**: `parse_word_template()` → `template_structure.placeholders` + `template_structure.tables[*].headers`
- **Word (.doc)**: doc 형식은 python-docx로 직접 읽을 수 없으므로, `libreoffice --convert-to docx` 등으로 변환 후 처리하거나 미지원 안내

field_mapper는 `_extract_field_names(template_structure)`로 파일 형식에 무관하게 필드명을 추출하므로, 이후 파이프라인은 동일하다.

#### 3.1.2 매핑 힌트 + 대상 DB/서비스명 추출

사용자가 프롬프트에 직접 매핑 정보나 대상 DB/서비스명을 제공하는 경우를 감지한다.

사용자 프롬프트 예시:
- "서버명은 hostname, IP는 ip_address 컬럼으로 조회해줘"
- "CPU 사용률은 polestar DB의 cpu_metrics.usage_pct에서 가져와"
- "메모리는 cloud_portal DB에서 조회"
- "폴스타에서 서버 현황 조회해줘" (서비스명으로 DB 지정)

`_parse_natural_language()`의 LLM 프롬프트를 확장하여 `field_mapping_hints`와 `target_db_hints`를 추출:

```python
# parsed_requirements에 추가되는 필드
{
    "field_mapping_hints": [
        {"field": "서버명", "column": "hostname", "db_id": null},
        {"field": "IP", "column": "ip_address", "db_id": null},
        {"field": "CPU 사용률", "column": "cpu_metrics.usage_pct", "db_id": "polestar"},
        {"field": "메모리", "column": null, "db_id": "cloud_portal"},
    ],
    "target_db_hints": ["polestar", "cloud_portal"]  # 프롬프트에서 감지된 DB/서비스명
}
```

#### 3.1.3 INPUT_PARSER_SYSTEM_PROMPT 확장

```
추가 추출 항목:
- field_mapping_hints: 사용자가 명시적으로 지정한 양식 필드 → DB 컬럼 매핑 정보.
  각 항목은 {field, column, db_id} 형태.
  - field: 양식 필드명 (한국어)
  - column: DB 컬럼명 또는 테이블.컬럼명 (없으면 null)
  - db_id: 특정 DB 지정 (없으면 null, 자동 결정)
- target_db_hints: 사용자가 프롬프트에서 언급한 DB명, 서비스명, 시스템명 목록.
  DB 도메인 설정(DB_DOMAINS)의 db_id 또는 aliases와 매칭하여 추출한다.
  예: "폴스타" → "polestar", "클라우드 포탈" → "cloud_portal"
```

### 3.2 Phase B: field_mapper 노드 신설 — input_parser 직후 매핑 수행

**신규 파일**: `src/nodes/field_mapper.py`
**변경 대상**: `src/document/field_mapper.py`, `src/state.py`, `src/graph.py`

#### 3.2.1 핵심 변경: field_mapper를 독립 노드로 신설

기존에는 result_organizer 내부에서 호출되던 field_mapper를 **input_parser 직후에 실행되는 독립 그래프 노드**로 승격한다. 이 노드가 모든 매핑을 담당하며, 매핑 결과가 이후 모든 노드의 동작을 주도한다.

```
그래프 변경:
  기존: input_parser → semantic_router → schema_analyzer → query_generator → ...
  개선: input_parser → ★field_mapper → semantic_router → schema_analyzer → query_generator → ...
```

field_mapper 노드가 input_parser 직후에 위치하는 이유:
- **매핑 결과로 대상 DB를 결정**해야 하므로 semantic_router보다 앞에 와야 한다
- **Redis 캐시의 전체 DB synonyms**를 조회하여 필드가 어느 DB에 속하는지 판단한다

#### 3.2.2 field_mapper 노드 동작

```python
async def field_mapper(state: AgentState, ...) -> dict:
    """Excel 양식 필드와 DB 컬럼 간 매핑을 수행한다.

    template_structure가 없으면 (텍스트 출력 모드) 스킵한다.

    3단계 매핑:
    1단계: 사용자 프롬프트 매핑 힌트 (field_mapping_hints)
    2단계: Redis synonyms 기반 규칙 매핑 (전체 DB 대상)
    3단계: LLM 의미 매핑 (Redis descriptions 포함 프롬프트)

    Returns:
        - column_mapping: 통합 매핑 {field: "db_id:table.column"}
        - db_column_mapping: DB별 매핑 {db_id: {field: "table.column"}}
        - mapping_sources: 매핑 출처 추적 {field: "hint"|"synonym"|"llm_inferred"}
        - mapped_db_ids: 매핑에서 식별된 DB 목록
    """
```

#### 3.2.3 DB 우선순위 기반 synonyms 조회

field_mapper는 Redis 캐시에서 synonyms를 조회하되, **프롬프트에 언급된 DB를 우선 조회**한다.

```python
async def _load_db_synonyms(cache_manager, active_db_ids, target_db_hints=None):
    """DB 우선순위를 적용하여 synonyms를 로드한다.

    target_db_hints가 있으면 해당 DB를 먼저 조회하여 매핑 우선권을 부여하고,
    나머지 DB는 이후에 조회한다.
    """
    all_synonyms = {}  # {db_id: {table.column: [synonyms]}}

    # 우선순위 DB 먼저 조회
    priority_db_ids = []
    remaining_db_ids = []
    if target_db_hints:
        for db_id in active_db_ids:
            if db_id in target_db_hints:
                priority_db_ids.append(db_id)
            else:
                remaining_db_ids.append(db_id)
    else:
        remaining_db_ids = list(active_db_ids)

    # 우선순위 DB → 나머지 DB 순으로 로드
    for db_id in priority_db_ids + remaining_db_ids:
        synonyms = await cache_manager.get_synonyms(db_id)
        if synonyms:
            all_synonyms[db_id] = synonyms

    return all_synonyms, priority_db_ids
```

이렇게 하면:
- 사용자가 "폴스타에서 조회해줘"라고 하면 polestar DB의 synonyms를 먼저 검색
- 동일 필드명이 여러 DB에 매핑 가능한 경우, **우선순위 DB를 선택**
- target_db_hints가 없으면 전체 DB를 동등하게 조회 (기존 동작)

#### 3.2.4 3단계 매핑 상세

```python
async def _perform_3step_mapping(
    field_names, field_mapping_hints, all_db_synonyms, all_db_descriptions, llm
):
    remaining = set(field_names)
    db_column_mapping = {}     # {db_id: {field: "table.column"}}
    mapping_sources = {}       # {field: "hint"|"synonym"|"llm_inferred"}

    # --- 1단계: 프롬프트 힌트 ---
    for hint in field_mapping_hints:
        field = hint["field"]
        if field not in remaining:
            continue
        if hint.get("column"):
            db_id = hint.get("db_id") or _find_db_for_column(hint["column"], all_db_synonyms)
            if db_id:
                db_column_mapping.setdefault(db_id, {})[field] = hint["column"]
                mapping_sources[field] = "hint"
                remaining.discard(field)

    # --- 2단계: Redis synonyms 규칙 매핑 (우선순위 DB 먼저) ---
    # priority_db_ids를 앞에 배치하여 우선순위 DB의 synonyms를 먼저 검색
    ordered_db_ids = priority_db_ids + [d for d in all_db_synonyms if d not in priority_db_ids]
    for field in list(remaining):
        for db_id in ordered_db_ids:
            synonyms = all_db_synonyms.get(db_id, {})
            matched_column = _synonym_match(field, synonyms)
            if matched_column:
                db_column_mapping.setdefault(db_id, {})[field] = matched_column
                mapping_sources[field] = "synonym"
                remaining.discard(field)
                break

    # --- 3단계: LLM 추론 매핑 ---
    if remaining:
        # 전체 DB의 descriptions를 합쳐서 LLM 프롬프트 구성
        llm_mapping = await _invoke_llm_mapping_multi_db(
            llm, list(remaining), all_db_descriptions
        )
        for field, (db_id, column) in llm_mapping.items():
            if column:
                db_column_mapping.setdefault(db_id, {})[field] = column
                mapping_sources[field] = "llm_inferred"

    return db_column_mapping, mapping_sources
```

#### 3.2.5 매핑 출처 추적 (mapping_sources)

각 필드의 매핑이 어디서 왔는지 추적한다:

| mapping_source 값 | 의미 | 사용자에게 표시 |
|-------------------|------|----------------|
| `"hint"` | 사용자가 프롬프트에서 직접 지정 | 표시하지 않음 (사용자가 알고 있음) |
| `"synonym"` | Redis 캐시 synonyms 정확 일치 | 표시하지 않음 (신뢰도 높음) |
| `"llm_inferred"` | LLM이 추론으로 매핑 | **반드시 표시** (사용자 확인 필요) |

#### 3.2.6 State 변경

```python
class AgentState(TypedDict):
    ...
    # === 필드 매핑 (신규) ===
    column_mapping: Optional[dict[str, Optional[str]]]           # 통합 매핑 {field: "table.column"}
    db_column_mapping: Optional[dict[str, dict[str, str]]]       # DB별 매핑 {db_id: {field: "table.column"}}
    mapping_sources: Optional[dict[str, str]]                    # 매핑 출처 {field: "hint"|"synonym"|"llm_inferred"}
    mapped_db_ids: Optional[list[str]]                           # 매핑에서 식별된 DB 목록
    column_descriptions: Optional[dict[str, dict[str, str]]]     # DB별 descriptions {db_id: {table.column: desc}}
    column_synonyms: Optional[dict[str, dict[str, list[str]]]]   # DB별 synonyms {db_id: {table.column: [words]}}
    pending_synonym_registrations: Optional[list[dict]]           # 유사어 등록 대기 [{field, column, db_id}, ...]
```

### 3.3 Phase C: semantic_router 개선 — 매핑 결과 기반 DB 선택

**변경 대상**: `src/routing/semantic_router.py`

#### 3.3.1 매핑 결과 기반 라우팅

현재 semantic_router는 사용자 질의 텍스트만으로 LLM이 대상 DB를 판단한다. 개선 후에는 **field_mapper가 이미 결정한 `mapped_db_ids`를 우선 참조**한다.

```python
async def semantic_router(state, ...):
    mapped_db_ids = state.get("mapped_db_ids")

    if mapped_db_ids:
        # field_mapper가 이미 대상 DB를 결정함 → LLM 라우팅 스킵
        targets = [
            {
                "db_id": db_id,
                "relevance_score": 1.0,
                "sub_query_context": state["user_query"],
                "user_specified": False,
                "reason": f"필드 매핑 결과에서 식별된 DB",
            }
            for db_id in mapped_db_ids
        ]
    else:
        # template 없는 경우 (텍스트 출력 모드): 기존 LLM 라우팅
        targets = await _llm_classify(llm, user_query, active_domains)

    ...
```

이렇게 하면:
- Excel 양식 업로드 시: field_mapper가 결정한 DB를 그대로 사용 (LLM 라우팅 스킵, 비용 절감)
- 텍스트 질의 시: 기존 LLM 라우팅 유지 (하위 호환)

### 3.4 Phase D: query_generator 개선 — column_mapping 기반 SQL 생성

**변경 대상**: `src/nodes/query_generator.py`, `src/prompts/query_generator.py`, `src/nodes/multi_db_executor.py`

#### 3.4.1 단일 DB 모드: _build_user_prompt 변경

현재: template_structure JSON 전체를 프롬프트에 포함
개선: **column_mapping을 직접 전달**하여 SELECT 절에 포함할 컬럼을 명시적으로 지정

```
## 양식-DB 매핑 (반드시 SELECT에 포함할 컬럼)
- "서버명" → servers.hostname
- "IP주소" → servers.ip_address
- "CPU 사용률" → cpu_metrics.usage_pct
- "메모리(GB)" → memory_metrics.total_gb

위 매핑에 포함된 모든 DB 컬럼을 반드시 SELECT에 포함하고,
SELECT 시 "테이블명.컬럼명" 형식의 alias를 사용하세요.
예: SELECT s.hostname AS "servers.hostname", ...
```

#### 3.4.2 멀티 DB 모드: DB별 독립 SQL 생성

여러 DB가 선택된 경우, `multi_db_executor`에서 각 DB의 `db_column_mapping[db_id]`를 전달하여 **각 DB별로 필요한 컬럼만 SELECT하는 독립 SQL**을 생성한다.

```python
# multi_db_executor 내부
for target in targets:
    db_id = target["db_id"]
    db_mapping = state.get("db_column_mapping", {}).get(db_id, {})

    sql = await _generate_sql(
        llm, parsed_requirements, schema_info,
        sub_context, default_limit,
        column_mapping=db_mapping,  # ← 해당 DB의 매핑 전달
    )
```

#### 3.4.3 시스템 프롬프트 규칙 추가

`QUERY_GENERATOR_SYSTEM_TEMPLATE`에 다음 규칙 추가:

```
8. 양식-DB 매핑이 제공된 경우, 매핑된 모든 컬럼을 SELECT에 포함하고
   "테이블명.컬럼명" 형태의 alias를 부여하세요.
   예: SELECT s.hostname AS "servers.hostname"
9. 여러 테이블의 컬럼이 매핑된 경우, 적절한 JOIN을 사용하세요.
```

#### 3.4.4 SQL alias 규칙

query_generator가 생성하는 SQL에서 alias를 `"table.column"` 형태로 통일한다:

```sql
SELECT
    s.hostname AS "servers.hostname",
    s.ip_address AS "servers.ip_address",
    c.usage_pct AS "cpu_metrics.usage_pct"
FROM servers s
JOIN cpu_metrics c ON s.id = c.server_id
LIMIT 1000;
```

### 3.5 Phase E: result_organizer 중복 매핑 제거

**변경 대상**: `src/nodes/result_organizer.py`

기존 result_organizer에서 수행하던 field_mapping LLM 호출을 **완전 제거**하고, State에 이미 있는 `column_mapping`을 그대로 `organized_data`에 전달한다.

```python
# 변경 전 (현재)
if template and output_format in ("xlsx", "docx"):
    column_mapping = await _perform_field_mapping(llm, ...)

# 변경 후
if template and output_format in ("xlsx", "docx"):
    column_mapping = state.get("column_mapping")  # field_mapper 노드에서 이미 생성됨
```

데이터 충분성 검사도 column_mapping 기반으로 개선:

```python
def _check_data_sufficiency(results, parsed, template, column_mapping):
    if not column_mapping or not results:
        return True
    result_keys = set(results[0].keys())
    mapped_columns = [v for v in column_mapping.values() if v is not None]
    matched = sum(1 for mc in mapped_columns
                  if mc in result_keys or mc.split(".", 1)[-1] in result_keys)
    return matched >= len(mapped_columns) * 0.5
```

### 3.6 Phase F: output_generator — 매핑 정보 응답 포함 + 유사어 등록 제안 + 파일 생성

**변경 대상**: `src/nodes/output_generator.py`, `src/document/excel_writer.py`, `src/document/word_writer.py`

#### 3.6.1 LLM 추론 매핑 정보를 사용자 응답에 포함 + 유사어 등록 질문

`mapping_sources`에서 `"llm_inferred"` 항목이 있으면, 최종 응답에 매핑 내역을 표시하고 **유사어 등록 여부를 질문**한다:

```python
async def _generate_text_response(config, state, llm=None):
    ...
    # LLM 추론 매핑 정보 추가
    mapping_sources = state.get("mapping_sources", {})
    column_mapping = state.get("column_mapping", {})
    db_column_mapping = state.get("db_column_mapping", {})
    inferred_mappings = {}
    for field, source in mapping_sources.items():
        if source == "llm_inferred" and column_mapping.get(field):
            # DB 정보도 함께 수집
            for db_id, db_map in db_column_mapping.items():
                if field in db_map:
                    inferred_mappings[field] = {
                        "column": db_map[field],
                        "db_id": db_id,
                    }
                    break

    if inferred_mappings:
        items = list(inferred_mappings.items())
        mapping_text = "\n".join(
            f"  {i}. \"{field}\" → {info['column']} ({info['db_id']})"
            for i, (field, info) in enumerate(items, 1)
        )
        response += (
            f"\n\n---\n"
            f"**[자동 매핑 안내]** 다음 필드는 LLM이 추론하여 매핑했습니다:\n{mapping_text}\n\n"
            f"이 매핑이 정확하다면 **유사어로 등록**하여 다음부터 자동 매핑할 수 있습니다.\n"
            f"- 전체 등록: \"전체 등록\" 또는 \"모두 등록\"\n"
            f"- 선택 등록: \"1, 3 등록\" (번호 지정)\n"
            f"- 매핑 변경: \"서버명은 hostname 컬럼으로 조회\" 형태로 지정"
        )
    return response
```

사용자에게 보이는 응답 예시:
```
총 50건의 데이터를 조회하여 Excel 파일을 생성했습니다.
[다운로드: result_20260317_143000.xlsx]

---
**[자동 매핑 안내]** 다음 필드는 LLM이 추론하여 매핑했습니다:
  1. "CPU 사용률" → cpu_metrics.usage_pct (polestar)
  2. "디스크 잔여" → disk_metrics.free_gb (polestar)
  3. "네트워크 대역폭" → network_metrics.bandwidth_mbps (polestar)

이 매핑이 정확하다면 **유사어로 등록**하여 다음부터 자동 매핑할 수 있습니다.
- 전체 등록: "전체 등록" 또는 "모두 등록"
- 선택 등록: "1, 3 등록" (번호 지정)
- 매핑 변경: "서버명은 hostname 컬럼으로 조회" 형태로 지정
```

#### 3.6.2 유사어 등록 플로우

사용자가 "유사어 등록" 또는 "매핑 승인"이라고 응답하면, **이전 대화의 inferred_mappings를 Redis synonyms에 등록**한다.

```
예시 1: 전체 등록
[사용자] "전체 등록"
    ↓
[input_parser] 의도 감지: synonym_registration = {mode: "all"}
    ↓
[synonym_registrar 핸들러]
    ├─ State에서 pending_synonym_registrations 전체 조회
    ├─ Redis에 일괄 등록:
    │   cache_manager.add_synonyms("polestar", "cpu_metrics.usage_pct", ["CPU 사용률"])
    │   cache_manager.add_synonyms("polestar", "disk_metrics.free_gb", ["디스크 잔여"])
    │   cache_manager.add_synonyms("polestar", "network_metrics.bandwidth_mbps", ["네트워크 대역폭"])
    └─ 응답: "3건의 유사어가 모두 등록되었습니다. 다음부터 자동 매핑됩니다."

예시 2: 선택 등록
[사용자] "1, 3 등록"
    ↓
[input_parser] 의도 감지: synonym_registration = {mode: "selective", indices: [1, 3]}
    ↓
[synonym_registrar 핸들러]
    ├─ pending_synonym_registrations에서 1번, 3번 항목만 선택
    ├─ Redis에 선택 등록:
    │   cache_manager.add_synonyms("polestar", "cpu_metrics.usage_pct", ["CPU 사용률"])
    │   cache_manager.add_synonyms("polestar", "network_metrics.bandwidth_mbps", ["네트워크 대역폭"])
    └─ 응답: "2건의 유사어가 등록되었습니다. (1. CPU 사용률, 3. 네트워크 대역폭)"

예시 3: 단건 등록
[사용자] "1번 등록"
    ↓
[input_parser] 의도 감지: synonym_registration = {mode: "selective", indices: [1]}
    ↓
[synonym_registrar 핸들러]
    ├─ 1번 항목만 Redis에 등록
    └─ 응답: "1건의 유사어가 등록되었습니다. (1. CPU 사용률 → cpu_metrics.usage_pct)"
```

**State에 inferred_mappings 보존**: 유사어 등록 승인은 멀티턴 대화에서 이전 상태를 참조해야 하므로, `pending_synonym_registrations`를 State에 저장한다. 각 항목에 번호(index)를 부여하여 사용자가 번호로 선택할 수 있도록 한다.

```python
class AgentState(TypedDict):
    ...
    # === 유사어 등록 대기 (신규) ===
    pending_synonym_registrations: Optional[list[dict]]
    # [{index: 1, field: "CPU 사용률", column: "cpu_metrics.usage_pct", db_id: "polestar"}, ...]
```

**구현 옵션**:
- **옵션 A (Phase 3 연계)**: 멀티턴 대화 체크포인트를 활용하여 이전 State의 `pending_synonym_registrations`를 참조
- **옵션 B (독립 구현)**: 세션/쿠키 기반으로 pending 등록 정보를 임시 저장하고, 다음 요청에서 처리

**input_parser에서 유사어 등록 의도 감지**:

```python
# 정규식으로 등록 의도 + 번호 파싱
patterns = [
    r"전체\s*등록|모두\s*등록",                      # 전체 등록
    r"([\d,\s]+)\s*번?\s*등록",                      # "1, 3 등록", "1번 등록"
    r"유사어\s*등록|매핑\s*승인|매핑\s*등록",           # 전체 등록 (레거시)
]
```

#### 3.6.2 Excel 생성 시 template_structure + column_mapping 활용

output_generator는 State에 저장된 `template_structure`(헤더 위치, 데이터 시작 행 등)와 `column_mapping`을 사용하여 Excel 양식의 **정확한 컬럼 위치에 데이터를 채운다**.

이 부분은 기존 `fill_excel_template()` 로직과 동일하지만, column_mapping이 field_mapper에서 이미 확정되었으므로 키 불일치 문제가 해소된다.

#### 3.6.4 Excel/Word Writer 개선

**공통 개선** (excel_writer, word_writer 모두):
- alias 규칙으로 query_results key가 `"servers.hostname"` 형태로 통일되므로 `_get_value_from_row` 폴백 의존도 감소
- None 값 처리: 매핑된 컬럼에 값이 없으면 원본 파일의 기존 값을 유지
- 매칭 실패 시 경고 로그 추가

**Excel (.xlsx)**:
- `fill_excel_template()`: 기존 로직 유지, column_mapping 키 매칭 신뢰도 향상

**Word (.docx)**:
- `fill_word_template()`: `{{placeholder}}` 및 테이블 헤더에 동일한 column_mapping 적용
- field_mapper의 매핑이 placeholder명과 DB 컬럼을 연결하므로, 기존 `_replace_paragraph_placeholders()`가 정확한 값을 삽입

**Word (.doc)**:
- .doc 형식은 python-docx로 직접 읽을 수 없음
- `libreoffice --headless --convert-to docx` 명령으로 .docx로 변환 후 처리
- 변환 실패 시 사용자에게 ".docx 형식으로 변환하여 업로드해 주세요" 안내

---

## 4. 변경 파일 요약

| 파일 | 변경 내용 | 우선순위 |
|------|----------|---------|
| `src/state.py` | `column_mapping`, `db_column_mapping`, `mapping_sources`, `mapped_db_ids`, `column_descriptions`, `column_synonyms` 추가 | P0 |
| `src/nodes/input_parser.py` | `field_mapping_hints` 추출 로직 추가 | P0 |
| `src/prompts/input_parser.py` | `field_mapping_hints` 추출 지시 추가 | P0 |
| `src/nodes/field_mapper.py` | **신규** — 독립 그래프 노드, 3단계 매핑 로직 | P0 |
| `src/document/field_mapper.py` | 3단계 매핑 지원 (synonyms 규칙 + LLM), descriptions 프롬프트 포함, 멀티 DB 매핑 | P0 |
| `src/prompts/field_mapper.py` | descriptions/synonyms 포함 프롬프트 개선 | P0 |
| `src/graph.py` | field_mapper 노드 추가 (input_parser → field_mapper → semantic_router) | P0 |
| `src/routing/semantic_router.py` | `mapped_db_ids` 참조하여 매핑 기반 라우팅 추가 | P0 |
| `src/nodes/query_generator.py` | `_build_user_prompt`에서 column_mapping 기반 프롬프트 생성 | P0 |
| `src/prompts/query_generator.py` | alias 규칙, 매핑 컬럼 필수 포함 규칙 추가 | P0 |
| `src/nodes/multi_db_executor.py` | DB별 column_mapping 전달하여 SQL 생성 | P1 |
| `src/nodes/result_organizer.py` | 중복 field_mapping 호출 완전 제거, 충분성 검사 개선 | P1 |
| `src/nodes/output_generator.py` | LLM 추론 매핑 정보 + 유사어 등록 질문을 응답에 포함 | P1 |
| `src/nodes/result_merger.py` | 멀티 DB 결과 병합 시 column_mapping 기반 키 통일 | P1 |
| `src/document/excel_writer.py` | None 값 처리, 경고 로그 추가 | P1 |
| `src/document/word_writer.py` | column_mapping 기반 placeholder/테이블 채우기 개선 | P1 |
| `src/nodes/input_parser.py` | .doc → .docx 변환 처리, `target_db_hints` 추출 추가 | P1 |

---

## 5. 수정 후 예상 흐름

### 5.1 단일 DB 흐름

```
[1] input_parser
    ├─ Excel 파싱 → template_structure (headers: ["서버명", "IP주소", "CPU 사용률"])
    └─ 프롬프트 분석 → field_mapping_hints:
       [{"field": "서버명", "column": "hostname", "db_id": null}]  (사용자가 힌트를 준 경우)

[2] ★ field_mapper (신규 노드)
    ├─ Redis 캐시에서 전체 DB의 synonyms/descriptions 로드
    │   polestar synonyms: {"servers.hostname": ["서버명", "호스트명"], "servers.ip_address": ["IP주소", "아이피"]}
    ├─ 3단계 매핑:
    │   1단계 (프롬프트 힌트): "서버명" → "servers.hostname" (사용자 지정) → DB: polestar
    │   2단계 (synonyms 규칙): "IP주소" → synonyms 검색 → "servers.ip_address" → DB: polestar ✅
    │   3단계 (LLM 추론): "CPU 사용률" → LLM 호출 → "cpu_metrics.usage_pct" → DB: polestar
    ├─ 결과 저장:
    │   column_mapping: {"서버명": "servers.hostname", "IP주소": "servers.ip_address", "CPU 사용률": "cpu_metrics.usage_pct"}
    │   db_column_mapping: {"polestar": {"서버명": "servers.hostname", ...}}
    │   mapping_sources: {"서버명": "hint", "IP주소": "synonym", "CPU 사용률": "llm_inferred"}
    │   mapped_db_ids: ["polestar"]
    └─ template_structure + column_mapping을 State에 보존

[3] semantic_router
    └─ mapped_db_ids=["polestar"] → target_databases: [{db_id: "polestar", ...}]
       (LLM 라우팅 스킵)

[4] schema_analyzer → 스키마 조회 (검증용)

[5] query_generator
    ├─ 프롬프트에 column_mapping 포함:
    │   "반드시 SELECT에 포함: servers.hostname, servers.ip_address, cpu_metrics.usage_pct"
    └─ SQL 생성:
       SELECT s.hostname AS "servers.hostname",
              s.ip_address AS "servers.ip_address",
              c.usage_pct AS "cpu_metrics.usage_pct"
       FROM servers s
       JOIN cpu_metrics c ON s.id = c.server_id
       LIMIT 1000;

[6] query_validator → query_executor
    └─ query_results: [
         {"servers.hostname": "web-01", "servers.ip_address": "10.0.0.1", "cpu_metrics.usage_pct": 85.2},
         ...
       ]

[7] result_organizer
    └─ column_mapping = state["column_mapping"]  # field_mapper에서 이미 확정됨

[8] output_generator
    ├─ fill_excel_template()
    │   ├─ header "서버명" → mapping "servers.hostname" → result key "servers.hostname" → ✅ 직접 매칭
    │   ├─ header "IP주소" → mapping "servers.ip_address" → result key "servers.ip_address" → ✅ 직접 매칭
    │   └─ header "CPU 사용률" → mapping "cpu_metrics.usage_pct" → ✅ 직접 매칭
    └─ 응답에 매핑 정보 포함:
       "**[자동 매핑 안내]** CPU 사용률 → cpu_metrics.usage_pct (LLM 추론)"
```

### 5.2 멀티 DB 흐름

```
[1] input_parser
    ├─ Excel 파싱 → headers: ["서버명", "IP주소", "CPU 사용률", "클라우드 인스턴스"]
    └─ 프롬프트: "서버 정보는 polestar, 클라우드는 cloud_portal에서 조회"
       → field_mapping_hints:
         [{"field": "클라우드 인스턴스", "column": null, "db_id": "cloud_portal"}]

[2] ★ field_mapper
    ├─ 전체 DB synonyms 로드:
    │   polestar: {"servers.hostname": ["서버명", ...], "servers.ip_address": ["IP주소", ...]}
    │   cloud_portal: {"cloud_instances.instance_type": ["클라우드 인스턴스", "인스턴스 유형", ...]}
    ├─ 3단계 매핑:
    │   1단계 (힌트): "클라우드 인스턴스" → db_id: "cloud_portal" (사용자 지정)
    │   2단계 (synonyms): "서버명" → polestar, "IP주소" → polestar, "CPU 사용률" → polestar
    │   3단계 (LLM): "클라우드 인스턴스" → cloud_instances.instance_type (cloud_portal)
    └─ 결과:
       db_column_mapping: {
         "polestar": {"서버명": "servers.hostname", "IP주소": "servers.ip_address", "CPU 사용률": "cpu_metrics.usage_pct"},
         "cloud_portal": {"클라우드 인스턴스": "cloud_instances.instance_type"}
       }
       mapped_db_ids: ["polestar", "cloud_portal"]

[3] semantic_router
    └─ mapped_db_ids 기반 → target_databases: [polestar, cloud_portal]

[4] schema_analyzer (각 DB 검증)

[5] multi_db_executor (DB별 독립 SQL 생성·실행)
    ├─ polestar SQL:
    │   SELECT s.hostname AS "servers.hostname", s.ip_address AS "servers.ip_address",
    │          c.usage_pct AS "cpu_metrics.usage_pct"
    │   FROM servers s JOIN cpu_metrics c ON s.id = c.server_id LIMIT 1000;
    └─ cloud_portal SQL:
        SELECT ci.instance_type AS "cloud_instances.instance_type"
        FROM cloud_instances ci LIMIT 1000;

[6] result_merger → 두 DB 결과 병합

[7] result_organizer → [8] output_generator → Excel 파일 생성
```

---

## 6. 리스크 및 완화 방안

| 리스크 | 영향 | 완화 |
|--------|------|------|
| Redis 캐시에 synonyms가 없음 (초기 상태) | 2단계 매핑 실패, LLM 폴백 | 최초 캐시 생성 시 `DescriptionGenerator`로 자동 생성; LLM 폴백으로 기능은 동작; 추론 매핑은 사용자에게 표시 |
| LLM이 alias 규칙을 따르지 않음 | query_results key 불일치 | query_validator에서 alias 형식 검증 추가; _get_value_from_row 폴백 유지 |
| 멀티 DB 결과 병합 시 행 매칭 불일치 | 서로 다른 DB의 행을 잘못 결합 | 공통 키(hostname, server_id 등)가 있으면 키 기반 조인, 없으면 별도 시트로 분리 |
| 사용자 프롬프트 매핑 힌트 파싱 실패 | 힌트가 무시됨 | 파싱 실패 시 경고 로그, synonyms/LLM 폴백으로 정상 동작 |
| field_mapper 노드 추가로 그래프 복잡도 증가 | 유지보수 부담 | 노드 역할이 명확하고, template 없으면 스킵하여 기존 흐름에 영향 없음 |
| 전체 DB synonyms 로드 시 Redis I/O 부하 | field_mapper 응답 시간 증가 | synonyms Hash는 소량 데이터 (DB당 수백 항목), 전체 로드해도 수 ms; 메모리 캐시 적용 가능 |
| LLM 추론 매핑이 잘못될 경우 | 잘못된 데이터로 Excel 채움 | 추론 매핑을 사용자에게 명시적으로 표시하여 확인 기회 제공; 사용자가 힌트로 수정 가능 |
| schemacache_plan.md Redis 구현이 선행 필요 | 캐시 없으면 synonyms 사용 불가 | Redis 미구현 시 2단계(synonyms)를 스킵, LLM 폴백으로 동작하도록 graceful fallback |

---

## 7. 구현 순서 (권장)

### 선행 의존성: schemacache_plan.md의 Redis 캐시 + descriptions/synonyms 구현

이 계획은 `schemacache_plan.md`의 다음 항목이 구현된 상태를 전제한다:
- `SchemaCacheManager` (Redis 캐시 통합 매니저)
- `RedisSchemaCache.load_descriptions()`, `load_synonyms()`
- `DescriptionGenerator` (LLM 기반 컬럼 설명 + 유사 단어 생성)

Redis 캐시가 미구현인 경우에도 **2단계(synonyms 매핑)를 스킵**하고 LLM 매핑으로 동작하도록 graceful fallback을 구현한다.

### 구현 단계

| 단계 | 작업 | 변경 파일 | 의존성 |
|------|------|----------|--------|
| **1** | State 변경 — 매핑 관련 필드 추가 | `src/state.py` | 없음 |
| **2** | input_parser 확장 — `field_mapping_hints` 추출 | `src/nodes/input_parser.py`, `src/prompts/input_parser.py` | 없음 |
| **3** | field_mapper 모듈 개선 — 3단계 매핑 로직, 멀티 DB, descriptions 프롬프트 | `src/document/field_mapper.py`, `src/prompts/field_mapper.py` | 단계 1 |
| **4** | field_mapper 그래프 노드 신설 | `src/nodes/field_mapper.py`, `src/graph.py` | 단계 2, 3 |
| **5** | semantic_router 개선 — mapped_db_ids 기반 라우팅 | `src/routing/semantic_router.py` | 단계 4 |
| **6** | query_generator 프롬프트 개선 — column_mapping 기반 SELECT + alias | `src/nodes/query_generator.py`, `src/prompts/query_generator.py` | 단계 4 |
| **7** | multi_db_executor 확장 — DB별 column_mapping 전달 | `src/nodes/multi_db_executor.py` | 단계 6 |
| **8** | result_organizer 정리 — 중복 매핑 제거, 충분성 검사 개선 | `src/nodes/result_organizer.py` | 단계 4 |
| **9** | output_generator — LLM 추론 매핑 정보 응답 + 유사어 등록 질문 포함 | `src/nodes/output_generator.py` | 단계 4 |
| **10** | Excel/Word Writer 개선 — None 값 처리, .doc 변환 지원 | `src/document/excel_writer.py`, `src/document/word_writer.py`, `src/nodes/input_parser.py` | 단계 8 |
| **11** | 유사어 등록 플로우 — 사용자 승인 시 Redis synonyms 등록 | `src/nodes/field_mapper.py`, `src/nodes/input_parser.py` | 단계 4, 9 |
| **12** | 테스트 — end-to-end 검증 | `tests/` | 전체 |

---

## 8. 테스트 계획

| 테스트 케이스 | 검증 항목 |
|-------------|----------|
| 단일 시트, 단일 DB, 서버 정보만 요청 | column_mapping이 SQL SELECT에 반영되는지 |
| 단일 시트, 단일 DB, 서버+CPU+메모리 혼합 | JOIN SQL 생성 및 전체 컬럼 채우기 |
| 단일 시트, 멀티 DB | DB별 독립 SQL 생성·실행, 결과 병합 후 Excel 채우기 |
| 사용자 프롬프트에 매핑 힌트 포함 | 힌트가 1단계로 우선 적용되는지 |
| synonyms 캐시로 LLM 호출 없이 매핑 | LLM 호출 횟수 0인지 확인 |
| Redis 캐시 미존재 시 LLM 폴백 | LLM 매핑으로 정상 동작하는지 |
| LLM 추론 매핑 시 응답에 매핑 정보 표시 | mapping_sources가 "llm_inferred"인 필드가 응답에 포함되는지 |
| field_mapper 결과로 대상 DB가 결정되는지 | mapped_db_ids가 semantic_router에 전달되어 target_databases가 결정되는지 |
| template 없는 텍스트 질의 | field_mapper 스킵, 기존 LLM 라우팅 동작 확인 |
| 멀티시트 (시트별 다른 도메인) | 시트별 독립 매핑 및 채우기 |
| 매핑 불가 헤더 포함 (예: "비고") | null 매핑 처리, 해당 열 스킵 |
| 수식 셀 보존 | 수식 셀이 덮어쓰기되지 않는지 |
| 병합 셀 보존 | 병합 영역이 유지되는지 |
| 빈 결과 (0건) | "해당 데이터 없음" 응답, 빈 Excel 반환 |
| SQL alias가 `table.column` 형식인지 | query_validator에서 검증 |
| template_structure + column_mapping이 output_generator까지 보존되는지 | State 전달 검증 |
| **Word (.docx) 양식 처리** | placeholder/테이블 헤더에 column_mapping 적용되는지 |
| **Word (.doc) 변환 처리** | .doc → .docx 변환 후 정상 파싱되는지 |
| **프롬프트에 DB명 지정 시 우선 조회** | target_db_hints가 synonyms 조회 우선순위에 반영되는지 |
| **LLM 추론 후 유사어 등록 질문** | inferred 매핑이 응답에 표시되고 등록 질문이 포함되는지 |
| **전체 유사어 등록** | "전체 등록" 입력 → 모든 추론 매핑이 Redis synonyms에 등록되는지 |
| **선택 유사어 등록** | "1, 3 등록" 입력 → 지정 번호 항목만 Redis에 등록, 나머지는 미등록 상태 유지 |
| **단건 유사어 등록** | "1번 등록" 입력 → 1건만 Redis에 등록 |
| **등록 후 자동 매핑 전환** | synonyms 등록 후 동일 필드명으로 재요청 시 LLM 호출 없이 synonym 매핑 되는지 |
