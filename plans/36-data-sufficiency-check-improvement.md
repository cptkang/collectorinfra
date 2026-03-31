# Plan 36: 데이터 충분성 검사 로직 개선

> 작성일: 2026-03-27
> 대상 파일: `src/nodes/result_organizer.py` — `_check_data_sufficiency()`
> 관련 노드: `result_organizer`, `field_mapper`, `query_generator`

---

## 1. 문제 현상

`result_organizer` 노드의 `_check_data_sufficiency()` 함수가 **임의의 50% 하드코딩 임계값**으로 데이터 충분성을 판단하고 있어, 불완전한 쿼리 결과가 사용자에게 전달되거나 불필요한 재시도가 발생한다.

```python
# 현재 코드 (line 178)
return matched >= len(mapped_columns) * 0.5
```

---

## 2. 근본 원인 분석

### 2.1 데이터 흐름 추적

`_check_data_sufficiency`에 도달하기까지의 파이프라인:

```
input_parser → field_mapper → schema_analyzer → query_generator → query_executor → result_organizer
```

| 단계 | 산출물 | 의미 |
|------|--------|------|
| `input_parser` | `parsed_requirements.query_targets` | 사용자가 요청한 도메인 (예: `["서버", "CPU"]`) |
| `input_parser` | `parsed_requirements.field_mapping_hints` | 사용자가 명시한 필드→컬럼 매핑 |
| `input_parser` (CSV) | `csv_sheet_data.headers` | CSV/Excel에서 추출한 헤더 목록 |
| `field_mapper` | `column_mapping` | 양식 필드 → DB 컬럼 매핑 (`{field: "table.col" \| "EAV:attr" \| None}`) |
| `field_mapper` | `mapping_sources` | 각 매핑의 출처 (`"hint"` \| `"synonym"` \| `"llm_inferred"`) |
| `query_generator` | `generated_sql` | column_mapping의 non-None 컬럼을 SELECT에 포함하도록 지시받은 SQL |
| `query_executor` | `query_results` | 실제 DB 결과 rows |

### 2.2 핵심 인사이트: `column_mapping`은 이미 "사용자 요청 vs DB 가용"의 교집합

`field_mapper`의 3단계 매핑 결과:

```python
column_mapping = {
    "서버명": "servers.hostname",     # DB에 존재 → 쿼리 결과에 반드시 있어야 함
    "IP주소": "servers.ip_address",   # DB에 존재 → 쿼리 결과에 반드시 있어야 함
    "OS종류": "EAV:OSType",           # EAV 조회 가능 → 피벗 결과에 있어야 함
    "비고": None,                     # DB에 없음 → 조회 불가 (검증 대상 아님)
    "담당자": None,                   # DB에 없음 → 조회 불가 (검증 대상 아님)
}
```

- **non-None 값**: 사용자가 요청했고 DB에서 조회 가능한 필드 → **쿼리 결과에 반드시 존재해야 함**
- **None 값**: DB에 없어서 채울 수 없는 필드 → 검증 대상 아님

`query_generator`는 non-None 매핑을 받아 "반드시 SELECT에 포함하라"고 지시받음 (`query_generator.py:426-435`).

### 2.3 현재 로직의 5가지 구체적 문제

| # | 문제 | 위치 | 영향 |
|---|------|------|------|
| P1 | **50% 하드코딩 임계값** | line 178 | 매핑된 10개 컬럼 중 5개만 있어도 "충분"으로 판정 → 불완전한 Excel 전달 |
| P2 | **mapping_sources 미참조** | line 136-189 전체 | hint/synonym(확실한 매핑)과 llm_inferred(불확실한 매핑)를 동일 취급 |
| P3 | **빈 결과 = 무조건 True** | line 155-157 | 쿼리 실패(WHERE 오류)와 정상 빈 결과 구분 불가 |
| P4 | **text 모드 검사 없음** | line 189 | column_mapping/template 없으면 무조건 True → 텍스트 응답도 검증 없이 통과 |
| P5 | **레거시 경로: 이름이 아닌 수량만 비교** | line 181-188 | 결과 컬럼이 완전히 다른 이름이어도 수가 50% 이상이면 통과 |

---

## 3. 설계 원칙

### 3.1 임계값 결정 기준

임계값은 하드코딩이 아니라 **실제 매핑 데이터로부터 도출**되어야 한다:

1. **사용자가 요청한 필드** (CSV 헤더, 템플릿 필드, `query_targets`)
2. **DB에서 조회 가능한 필드** (`column_mapping`의 non-None 값)
3. **매핑 확신도** (`mapping_sources`의 출처별 차등)

### 3.2 확신도별 차등 기준

100% 달성은 alias 불일치, EAV 피벗 변환 등으로 현실적으로 어려우므로 **70%를 목표 기준**으로 설정한다.

| 매핑 출처 | 의미 | 결과 내 존재 요구 | `.env` 키 |
|-----------|------|-------------------|-----------|
| `"hint"` | 사용자가 직접 지정 | **70% 이상** (기본값) | `QUERY_SUFFICIENCY_REQUIRED_THRESHOLD` |
| `"synonym"` | Redis 유사어 정확 매칭 | **70% 이상** (기본값) | (hint와 동일 키 공유) |
| `"llm_inferred"` | LLM 추론 (매핑 자체가 틀릴 수 있음) | **50% 이상** (기본값) | `QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD` |

> hint/synonym 매핑도 alias 불일치(`servers.hostname` vs `hostname` 등)나 EAV 피벗 결과의 컬럼명 변환으로 매칭 실패할 수 있으므로 100%를 강제하지 않는다.
> 두 임계값 모두 `.env`에서 운영 환경에 맞게 조정 가능하다.

---

## 4. 구현 계획

### 4.1 변경 대상 파일

| 파일 | 변경 내용 |
|------|----------|
| `src/config.py` | `QueryConfig`에 `sufficiency_required_threshold`, `sufficiency_optional_threshold` 필드 추가 |
| `.env.example` | `QUERY_SUFFICIENCY_REQUIRED_THRESHOLD`, `QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD` 항목 추가 |
| `src/nodes/result_organizer.py` | `_check_data_sufficiency()` 시그니처 확장 및 로직 전면 개편 |
| `src/state.py` | 변경 없음 (`mapping_sources`는 이미 State에 존재) |
| `tests/` | 신규 테스트 파일 작성 |

### 4.2 `QueryConfig` 확장 (`.env` 설정 가능화)

**파일**: `src/config.py`

```python
class QueryConfig(BaseSettings):
    """클라이언트 측 쿼리 정책."""

    max_retry_count: int = 3
    default_limit: int = 1000

    # 데이터 충분성 검사 임계값 (0.0 ~ 1.0)
    sufficiency_required_threshold: float = 0.7   # hint/synonym 매핑
    sufficiency_optional_threshold: float = 0.5   # llm_inferred 매핑

    model_config = {"env_prefix": "QUERY_", "env_file": ".env", "extra": "ignore"}
```

**파일**: `.env.example` — `# === 클라이언트 쿼리 정책 ===` 섹션에 추가

```bash
# 데이터 충분성 검사: hint/synonym 매핑의 최소 매칭 비율 (0.0~1.0)
QUERY_SUFFICIENCY_REQUIRED_THRESHOLD=0.7
# 데이터 충분성 검사: LLM 추론 매핑의 최소 매칭 비율 (0.0~1.0)
QUERY_SUFFICIENCY_OPTIONAL_THRESHOLD=0.5
```

### 4.3 `_check_data_sufficiency` 개편안

#### 4.3.1 함수 시그니처 변경

```python
# 변경 전
def _check_data_sufficiency(
    results: list[dict[str, Any]],
    parsed: dict,
    template: Optional[dict],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
) -> bool:

# 변경 후
def _check_data_sufficiency(
    results: list[dict[str, Any]],
    parsed: dict,
    template: Optional[dict],
    column_mapping: Optional[dict[str, Optional[str]]] = None,
    mapping_sources: Optional[dict[str, str]] = None,
    app_config: Optional[AppConfig] = None,
) -> bool:
```

#### 4.3.2 신규 보조 함수

```python
def _match_column_in_results(mapped_col: str, result_keys: set[str]) -> bool:
    """매핑된 컬럼이 쿼리 결과 키에 존재하는지 확인한다.

    정확 매칭, 컬럼명만 매칭 (table.col → col), EAV 매칭을 순서대로 시도한다.
    """

def _classify_mapped_columns(
    column_mapping: dict[str, Optional[str]],
    mapping_sources: Optional[dict[str, str]],
) -> tuple[list[str], list[str]]:
    """매핑된 컬럼을 확신도 기준으로 필수/선택으로 분류한다.

    Returns:
        (required_columns, optional_columns)
        - required: hint/synonym 출처 매핑의 DB 컬럼
        - optional: llm_inferred 출처 매핑의 DB 컬럼
    """
```

#### 4.3.3 메인 로직 개편

```python
def _check_data_sufficiency(
    results, parsed, template,
    column_mapping=None, mapping_sources=None,
    app_config=None,
):
    # --- Case 1: 빈 결과 ---
    if not results:
        # 집계 쿼리(aggregation)인데 0건이면 비정상일 가능성
        if parsed.get("aggregation"):
            return False
        return True  # 일반 조회 0건은 정상

    result_keys = set(results[0].keys())

    # --- Case 2: column_mapping 기반 (Excel/문서 모드) ---
    if column_mapping:
        required, optional = _classify_mapped_columns(column_mapping, mapping_sources)

        if not required and not optional:
            return True  # 모든 필드가 None 매핑

        # .env에서 설정 가능한 임계값 (QueryConfig 참조)
        if app_config is None:
            app_config = load_config()
        required_threshold = app_config.query.sufficiency_required_threshold  # 기본 0.7
        optional_threshold = app_config.query.sufficiency_optional_threshold  # 기본 0.5

        # 필수 컬럼 (hint/synonym): required_threshold 이상 존재해야 함
        if required:
            required_matched = sum(1 for c in required if _match_column_in_results(c, result_keys))
            if required_matched < len(required) * required_threshold:
                required_missing = [c for c in required if not _match_column_in_results(c, result_keys)]
                logger.warning(
                    "필수 매핑 컬럼 부족 (hint/synonym): %d/%d (기준 %.0f%%), 누락: %s",
                    required_matched, len(required), required_threshold * 100, required_missing,
                )
                return False

        # 선택 컬럼 (LLM 추론): optional_threshold 이상 존재
        if optional:
            optional_matched = sum(1 for c in optional if _match_column_in_results(c, result_keys))
            if optional_matched < len(optional) * optional_threshold:
                logger.warning(
                    "LLM 추론 매핑 컬럼 부족: %d/%d (기준 %.0f%%)",
                    optional_matched, len(optional), optional_threshold * 100,
                )
                return False

        return True

    # --- Case 3: 레거시 template 기반 ---
    if template:
        sheets = template.get("sheets", [{}])
        required_headers = sheets[0].get("headers", []) if sheets else []
        if required_headers:
            result_keys_lower = {k.lower() for k in result_keys}
            matched = sum(1 for h in required_headers if h.lower() in result_keys_lower)
            if matched < len(required_headers) * 0.5:
                return False

    # --- Case 4: text 모드 (기본) ---
    # query_targets 기반 최소 검증: 결과에 최소 1개 컬럼 존재 확인
    if not template and not column_mapping:
        if len(result_keys) == 0:
            return False

    return True
```

### 4.4 호출부 변경

**파일**: `src/nodes/result_organizer.py` — `result_organizer()` 함수 (line 59-62)

```python
# 변경 전
is_sufficient = _check_data_sufficiency(
    masked_results, parsed, template, column_mapping=state_column_mapping
)

# 변경 후
is_sufficient = _check_data_sufficiency(
    masked_results,
    parsed,
    template,
    column_mapping=state_column_mapping,
    mapping_sources=state.get("mapping_sources"),
    app_config=app_config,
)
```

### 4.5 `_match_column_in_results` 매칭 규칙

현재 인라인된 매칭 로직을 별도 함수로 추출하고 일관성을 높인다:

```python
def _match_column_in_results(mapped_col: str, result_keys: set[str]) -> bool:
    # 1. 정확 매칭: "servers.hostname" in result_keys
    if mapped_col in result_keys:
        return True

    # 2. 컬럼명만 매칭: "servers.hostname" → "hostname"
    if "." in mapped_col:
        col_only = mapped_col.split(".", 1)[-1]
        if col_only in result_keys:
            return True

    # 3. EAV 매칭: "EAV:OSType" → "OSType" 또는 "ostype"
    if mapped_col.startswith("EAV:"):
        attr_name = mapped_col[4:]
        result_keys_lower = {k.lower() for k in result_keys}
        if attr_name in result_keys or attr_name.lower() in result_keys_lower:
            return True

    # 4. 대소문자 무시 매칭 (폴백)
    mapped_lower = mapped_col.lower()
    for rk in result_keys:
        if rk.lower() == mapped_lower:
            return True
        if "." in mapped_col and rk.lower() == mapped_col.split(".", 1)[-1].lower():
            return True

    return False
```

---

## 5. 테스트 계획

### 5.1 단위 테스트

**파일**: `tests/test_nodes/test_result_organizer_sufficiency.py`

| # | 테스트 케이스 | 입력 | 기대 결과 |
|---|-------------|------|----------|
| T1 | hint/synonym 매핑 전부 존재 | required 3개 전부 결과에 존재 (100% ≥ 70%) | `True` |
| T2 | hint/synonym 매핑 70% 이상 존재 | required 10개 중 7개 존재 (70% ≥ 70%) | `True` |
| T3 | hint/synonym 매핑 70% 미만 | required 10개 중 6개 존재 (60% < 70%) | `False` |
| T4 | llm_inferred만 있고 절반 이상 존재 | optional 4개 중 3개 존재 | `True` |
| T5 | llm_inferred만 있고 절반 미만 존재 | optional 4개 중 1개 존재 | `False` |
| T6 | 혼합: required 70%+ & optional 50%+ | required 7/10 OK + optional 2/4 | `True` |
| T7 | 혼합: required 70% 미만 | required 6/10 + optional 전부 OK | `False` |
| T8 | 빈 결과 + 일반 쿼리 | `results=[], aggregation=None` | `True` |
| T9 | 빈 결과 + 집계 쿼리 | `results=[], aggregation="top_n"` | `False` |
| T10 | column_mapping 전부 None | 모든 필드 매핑 불가 | `True` |
| T11 | EAV 컬럼 alias 매칭 | `"EAV:OSType"` vs result key `"ostype"` | `True` |
| T12 | table.column → column 매칭 | `"servers.hostname"` vs result key `"hostname"` | `True` |
| T13 | text 모드 (column_mapping 없음) | 결과에 컬럼 1개 이상 | `True` |
| T14 | mapping_sources 미제공 (하위 호환) | column_mapping만 제공, mapping_sources=None | 기존 동작 유지 |

### 5.2 `_match_column_in_results` 단위 테스트

| # | 입력 | 기대 |
|---|------|------|
| M1 | `"servers.hostname"`, `{"servers.hostname"}` | `True` (정확 매칭) |
| M2 | `"servers.hostname"`, `{"hostname"}` | `True` (컬럼명만 매칭) |
| M3 | `"EAV:OSType"`, `{"OSType"}` | `True` (EAV 매칭) |
| M4 | `"EAV:OSType"`, `{"ostype"}` | `True` (EAV 대소문자 무시) |
| M5 | `"servers.hostname"`, `{"ip_address"}` | `False` |
| M6 | `"SERVERS.HOSTNAME"`, `{"hostname"}` | `True` (대소문자 폴백) |

### 5.3 `_classify_mapped_columns` 단위 테스트

| # | 입력 | 기대 required | 기대 optional |
|---|------|--------------|--------------|
| C1 | sources=`{"서버명":"hint","IP":"synonym","OS":"llm_inferred"}` | `["servers.hostname","servers.ip"]` | `["EAV:OSType"]` |
| C2 | mapping_sources=None (하위 호환) | 전부 required | `[]` |
| C3 | 모든 필드 None 매핑 | `[]` | `[]` |

---

## 6. 하위 호환성

| 시나리오 | 현재 동작 | 변경 후 동작 | 호환성 |
|---------|----------|-------------|--------|
| `mapping_sources=None` (레거시) | 50% 임계값 | 모든 non-None 매핑을 required(70%)로 취급 → **더 엄격** | 의도적 강화 |
| text 모드 (`column_mapping=None`) | 무조건 True | 결과 컬럼 0개일 때만 False | 거의 동일 |
| 빈 결과 + 집계 쿼리 | True | **False** (재시도) | 의도적 변경 |
| 빈 결과 + 일반 조회 | True | True | 동일 |

---

## 7. 계층 규칙 확인

변경은 `src/nodes/result_organizer.py` 내부에 한정되며, 외부 의존은 `src/state.py`(같은 계층 이하)만 참조.
`mapping_sources`는 이미 `AgentState`에 정의되어 있으므로 새 import 없음.

```
application(nodes/) → domain(state) ✅ 정방향 의존
```

`scripts/arch_check.py` 위반 없음 예상.

---

## 8. 구현 순서

| 단계 | 작업 | 예상 영향 범위 |
|------|------|---------------|
| 1 | `QueryConfig`에 `sufficiency_required_threshold`, `sufficiency_optional_threshold` 필드 추가 | `src/config.py` 1개 클래스 |
| 2 | `.env.example`에 `QUERY_SUFFICIENCY_*` 항목 추가 | `.env.example` 2줄 |
| 3 | `_match_column_in_results()` 함수 추출 | 신규 함수 (기존 인라인 로직 이동) |
| 4 | `_classify_mapped_columns()` 함수 추가 | 신규 함수 |
| 5 | `_check_data_sufficiency()` 시그니처 확장 + 로직 개편 | 기존 함수 수정 |
| 6 | `result_organizer()` 호출부에 `mapping_sources`, `app_config` 전달 | 1줄 변경 |
| 7 | 단위 테스트 작성 (T1-T14, M1-M6, C1-C3) | 신규 파일 |
| 8 | `scripts/arch_check.py` 실행하여 계층 위반 확인 | 검증 |

---

## 9. 리스크 및 대안

| 리스크 | 완화 방안 |
|--------|----------|
| `mapping_sources`가 None인 기존 흐름에서 모든 매핑이 required(70%)로 분류 → 기존 50%보다 엄격해짐 | T14 테스트로 하위 호환 검증. 필요 시 `mapping_sources=None`일 때 기존 50% 유지 옵션 |
| EAV 피벗 alias가 예측 불가한 형태로 반환될 경우 매칭 실패 | `_match_column_in_results`에 fuzzy 매칭 확장 가능 (현 단계에서는 대소문자 무시까지만) |
| 집계 쿼리 0건을 False로 변경 시 무한 재시도 | `retry_count < 3` 상한이 이미 존재 (line 64) |
