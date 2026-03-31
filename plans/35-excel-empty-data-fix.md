# Plan 35: Excel 데이터 미채움 버그 수정

## 문제 현상

사용자가 `취합 예시2.xlsx` 양식을 업로드하고 데이터 채우기를 요청하면, 텍스트 응답에는 조회 결과가 정상 표시되지만 다운로드한 Excel 파일에는 데이터가 비어있다.

## 근본 원인 분석

### 1차 원인: `fill_excel_template`의 Silent Failure

**위치**: `src/document/excel_writer.py:22-101`

`fill_excel_template()` 함수는 데이터가 **0건 채워져도 예외 없이** 원본 템플릿 바이너리를 그대로 반환한다.

```python
# fill_excel_template 마지막 부분 (line 94-101)
output = io.BytesIO()
wb.save(output)            # 데이터가 0건이어도 저장
wb.close()
output.seek(0)
logger.info("Excel 파일 생성 완료: %d건 데이터 채움", total_filled)
return output.getvalue()   # ← total_filled=0이어도 bytes 반환
```

호출부(`output_generator.py:296-315`)는 반환값이 bytes이면 무조건 성공으로 판단:
```python
file_bytes = fill_excel_template(...)   # 항상 bytes 반환
return {"file_bytes": file_bytes, ...}  # 항상 성공
```

**결과**: 사용자는 원본 템플릿(데이터 없음)을 다운로드하게 됨.

### 2차 원인: `_fill_sheet`에서 데이터 채우기 실패

**위치**: `src/document/excel_writer.py:104-166`

`_fill_sheet`가 데이터를 채우지 못하는 3가지 시나리오:

#### 시나리오 A: `col_assignments` 빈 리스트 (가장 유력)

```python
# _fill_sheet line 122-133
col_assignments = []
for hc in header_cells:
    header_name = hc["value"]             # 예: "서버명"
    mapped = column_mapping.get(header_name)  # None이면 스킵
    if mapped:
        col_assignments.append((col_idx, mapped))

if not col_assignments:
    logger.warning("매핑된 컬럼이 없어 데이터 채우기 스킵")
    return  # ← Silent return, total_filled 0
```

**발생 조건**: `column_mapping`의 모든 값이 `None` → 모든 필드가 매핑 실패.

`field_mapper` 노드가 Redis synonyms도 없고 LLM 추론도 실패하면 column_mapping은:
```python
{"서버명": None, "IP": None, "CPU": None, ...}  # 모든 값이 None
```

#### 시나리오 B: `_get_value_from_row`가 모두 None 반환

```python
# _fill_sheet line 146-161
for data_row in rows:
    for col_idx, db_column in col_assignments:
        value = _get_value_from_row(data_row, db_column)
        if value is None:
            continue  # ← 값을 찾지 못하면 스킵
        cell.value = value
```

**발생 조건**: `column_mapping` 값(예: `"cmm_resource.hostname"`)과 실제 쿼리 결과 dict의 키(예: `"HOSTNAME"`)가 불일치.

`_get_value_from_row`는 4단계 검색(정확→split→EAV→case-insensitive)을 수행하지만, **별칭(alias) SQL 결과**나 **EAV 피벗 결과**에서 키가 예상과 다를 수 있음.

#### 시나리오 C: 헤더 행 오탐지

`_detect_header_row`가 실제 헤더가 아닌 행을 선택하면, `header_cells.value`와 `column_mapping` 키가 불일치.

현재 `취합 예시2.xlsx`의 경우:
```
Row 1: "전체 시스템 자원 현황" (title, merged A1:M1)
Row 2: (empty)
Row 3: "자원현황", "모니터링 Tool", "2월" (category headers, merged)
Row 4: 21개 컬럼 헤더 (실제 헤더) ← 정상 탐지됨
```
Row 4가 정상적으로 탐지되어 이 시나리오는 현재 파일에는 해당 안 됨. 단, 다른 양식에서 발생 가능.

### 현재 헤더 추출 → 필드 매핑 → Excel 채우기 데이터 흐름

현재 파이프라인에서 CSV 헤더, template 헤더, column_mapping, Excel 채우기가 어떻게 연결되는지 정리한다.

#### 전체 흐름도

```
[사용자 업로드: 취합 예시2.xlsx]
            │
            ├──────────────────────────────┐
            ▼                              ▼
   ① API 라우트                    ② input_parser 노드
   query.py:594-603                input_parser.py:87-91
   excel_to_csv(file_bytes)        parse_excel_template(file_bytes)
   data_only=True                  data_only=False
            │                              │
            ▼                              ▼
   CsvSheetData.headers            template_structure.sheets[*].headers
   ["서버명","IP","CPU",...]       ["서버명","IP","CPU",...]
            │                              │
            ▼                              ├────────────────────┐
   ③ input_parser                  ④ field_mapper 노드         │
   LLM에 CSV 헤더+예시 전달       extract_field_names()        │
   → parsed_requirements 생성      field_names 추출             │
     field_mapping_hints 포함              │                    │
            │                              ▼                    │
            │                     ⑤ perform_3step_mapping()     │
            │                        field_names를 키로 사용     │
            │                              │                    │
            │                      ┌───────┼───────┐            │
            │                      ▼       ▼       ▼            │
            │                   1단계    2단계    3단계          │
            │                  프롬프트  Redis   LLM 추론       │
            │                   힌트    synonyms               │
            │                      └───────┼───────┘            │
            │                              ▼                    │
            │                     column_mapping 생성            │
            │                  {"서버명": "cmm_resource.hostname",│
            │                   "IP": "cmm_resource.ip_address", │
            │                   "CPU": None, ...}               │
            │                              │                    │
            │     ┌────────────────────────┘                    │
            │     │                                             │
            ▼     ▼                                             │
   ⑥ query_generator                                           │
   column_mapping의 values를 참조하여 SQL 생성                    │
   SELECT hostname, ip_address FROM cmm_resource                │
            │                                                   │
            ▼                                                   │
   ⑦ query_executor                                            │
   rows = [{"hostname":"svr1", "ip_address":"10.0.0.1"}, ...]  │
            │                                                   │
            ▼                                                   │
   ⑧ result_organizer                                          │
   organized_data.rows = masked_results                        │
   organized_data.column_mapping = state.column_mapping         │
            │                                                   │
            ▼                                                   ▼
   ⑨ output_generator._generate_document_file()
   fill_excel_template(
       file_data      = uploaded_file (원본 Excel bytes),
       template       = template_structure,   ◄── ②에서 생성
       column_mapping = state.column_mapping, ◄── ⑤에서 생성
       rows           = organized_data.rows,  ◄── ⑧에서 생성
   )
            │
            ▼
   ⑩ _fill_sheet()
   header_cells[i].value ──lookup──▶ column_mapping.get("서버명")
                                           = "cmm_resource.hostname"
                                                    │
                                                    ▼
                              _get_value_from_row(data_row, "cmm_resource.hostname")
                              data_row = {"hostname":"svr1", ...}
                                ① "cmm_resource.hostname" in row? → No
                                ② split(".")[1] = "hostname" in row? → Yes ✓
                                         │
                                         ▼
                                  cell.value = "svr1"
```

#### 핵심 매핑 지점 3곳

| # | 위치 | 매핑 내용 | 키 출처 | 값 출처 |
|---|------|----------|---------|---------|
| A | `_fill_sheet` line 127 | `column_mapping.get(header_name)` | `header_cells[i].value` (template) | `column_mapping` 값 (field_mapper) |
| B | `_get_value_from_row` | DB 컬럼명으로 data_row에서 값 검색 | `column_mapping` 값 (예: `cmm_resource.hostname`) | `data_row` 키 (예: `hostname`) |
| C | `field_mapper` 3단계 | 필드명 → DB 컬럼 매핑 | `field_names` (template headers) | Redis synonyms / LLM 추론 |

#### 데이터 미채움 발생 시나리오별 실패 지점

```
헤더 추출 실패 (②)          → A 실패: header_cells 빈 리스트
매핑 전체 실패 (⑤)          → A 실패: column_mapping 값 모두 None
매핑 키-헤더 불일치 (②↔⑤)   → A 실패: header_name이 column_mapping에 없음
DB 결과 키 불일치 (⑦↔⑩)     → B 실패: _get_value_from_row 전부 None
```

#### CSV 헤더와 Template 헤더의 관계

두 헤더는 **동일한 `_detect_header_row()` 함수**로 추출되지만 독립적으로 호출된다:

| 구분 | CSV 헤더 (①) | Template 헤더 (②) |
|------|-------------|-------------------|
| 호출 위치 | `excel_csv_converter.py` | `excel_parser.py` |
| openpyxl 옵션 | `data_only=True` (수식 → 계산값) | `data_only=False` (수식 유지) |
| 용도 | LLM 컨텍스트 (input_parser에서 질의 분석용) | field_mapper 키 + excel_writer 매칭 키 |
| State 필드 | `csv_sheet_data` | `template_structure` |

**주의**: 수식 셀이 헤더에 포함된 경우 두 헤더가 달라질 수 있음:
- CSV: `=CONCATENATE("CPU_AVG", A2)` → 계산값 `"CPU_AVG702"`
- Template: `data_only=False`이므로 **수식 텍스트 자체를 반환할 수 있음**

현재 `취합 예시2.xlsx`에서는 수식 헤더가 없어 양쪽 동일하지만, 수식 헤더를 사용하는 양식에서는 불일치가 발생할 수 있다.

### 3차 원인: CSV 변환 중복 수행 및 검증 부재

#### 중복 변환

`/query/file` 엔드포인트(`src/api/routes/query.py:594-603`)에서 매 요청마다 `excel_to_csv(file_bytes)` 변환을 수행한다. 동일한 양식 파일을 반복 업로드(멀티턴, 재시도 등)할 때마다 동일한 변환 작업이 불필요하게 반복됨.

#### 매핑 검증 부재

CSV 변환으로 추출된 헤더 정보와 `output_generator`에서 사용하는 `column_mapping`의 정합성을 확인하는 로직이 없다. CSV 헤더와 column_mapping 키가 불일치해도 감지할 방법이 없음.

### 진단 불가 원인: 로깅 부족

현재 `_fill_sheet`에서 아래 정보가 로깅되지 않아 원인 특정이 불가:
- `column_mapping`의 실제 키/값
- `col_assignments`에 포함된 매핑 수
- 쿼리 결과 row의 실제 키 목록
- 각 셀에 값이 채워졌는지 여부

## 수정 계획

### Phase 1: CSV 변환 캐시 및 매핑 검증

#### 1-1. Excel→CSV 변환 결과 Redis 캐시

**문제**: 동일한 Excel 양식을 업로드할 때마다 `excel_to_csv()`가 매번 실행되어 불필요한 openpyxl 로딩과 파싱이 발생. 인메모리 캐시로는 서버 재시작/멀티 프로세스 환경에서 캐시가 유실됨.

**해결**: 파일 해시(SHA-256)를 키로 CSV 변환 결과를 **Redis에 저장**하여 프로세스 간/재시작 후에도 재활용. Redis 미사용 또는 조회 실패 시 CSV 변환을 수행하는 fallback 로직 포함.

**Redis 키 네이밍**: `csv_cache:{file_hash}` → JSON 직렬화된 `dict[str, CsvSheetData]`

**변경 파일**: `src/document/excel_csv_converter.py`, `src/schema_cache/redis_cache.py`

##### (A) `RedisSchemaCache`에 CSV 캐시 메서드 추가

**파일**: `src/schema_cache/redis_cache.py`

```python
# Redis 키 접두사
CSV_CACHE_PREFIX = "csv_cache:"
CSV_CACHE_TTL = 86400 * 7  # 7일

async def save_csv_cache(self, file_hash: str, csv_data: dict) -> None:
    """CSV 변환 결과를 Redis에 저장한다.

    Args:
        file_hash: SHA-256 파일 해시
        csv_data: {시트명: CsvSheetData를 dict로 직렬화한 형태}
    """
    if not self._connected:
        return
    try:
        key = f"{self.CSV_CACHE_PREFIX}{file_hash}"
        await self._redis.set(
            key,
            json.dumps(csv_data, ensure_ascii=False),
            ex=self.CSV_CACHE_TTL,
        )
        logger.debug("CSV 캐시 Redis 저장: %s...", file_hash[:12])
    except Exception as e:
        logger.debug("CSV 캐시 Redis 저장 실패: %s", e)

async def load_csv_cache(self, file_hash: str) -> dict | None:
    """Redis에서 CSV 변환 결과를 조회한다.

    Args:
        file_hash: SHA-256 파일 해시

    Returns:
        {시트명: CsvSheetData dict} 또는 None (미스 시)
    """
    if not self._connected:
        return None
    try:
        key = f"{self.CSV_CACHE_PREFIX}{file_hash}"
        raw = await self._redis.get(key)
        if raw:
            logger.debug("CSV 캐시 Redis 히트: %s...", file_hash[:12])
            return json.loads(raw)
    except Exception as e:
        logger.debug("CSV 캐시 Redis 조회 실패: %s", e)
    return None
```

##### (B) `excel_csv_converter.py`에 Redis 캐시 연동 + fallback

**파일**: `src/document/excel_csv_converter.py`

기존 `excel_to_csv` 함수에 `cache_manager` 파라미터를 추가하고, 3단계 캐시 로직 적용:

```python
import hashlib

def _compute_file_hash(file_data: bytes) -> str:
    """파일 바이너리의 SHA-256 해시를 계산한다."""
    return hashlib.sha256(file_data).hexdigest()

async def excel_to_csv_cached(
    file_data: bytes,
    cache_manager: Any | None = None,
    sheet_name: str | None = None,
) -> dict[str, CsvSheetData]:
    """Redis 캐시를 활용하는 Excel→CSV 변환.

    조회 순서:
    1. Redis 캐시 조회 (file_hash 키)
    2. 캐시 미스 시 CSV 변환 수행
    3. 변환 결과를 Redis에 저장

    Redis 미사용/장애 시 → 변환 수행 후 캐시 저장 스킵 (graceful fallback).
    """
    file_hash = _compute_file_hash(file_data)

    # 1. Redis 캐시 조회
    if cache_manager and cache_manager.redis_available:
        try:
            cached = await cache_manager._redis_cache.load_csv_cache(file_hash)
            if cached:
                logger.info("CSV 캐시 히트 (Redis, hash=%s...)", file_hash[:12])
                result = {
                    k: CsvSheetData(**v) for k, v in cached.items()
                }
                if sheet_name:
                    return {k: v for k, v in result.items() if k == sheet_name}
                return result
        except Exception as e:
            logger.debug("Redis CSV 캐시 조회 실패, fallback 변환: %s", e)

    # 2. Fallback: CSV 변환 수행
    result = excel_to_csv(file_data, sheet_name)

    # 3. Redis에 저장 (비동기, 실패 무시)
    if cache_manager and cache_manager.redis_available:
        try:
            from dataclasses import asdict
            serializable = {k: asdict(v) for k, v in result.items()}
            await cache_manager._redis_cache.save_csv_cache(file_hash, serializable)
        except Exception as e:
            logger.debug("Redis CSV 캐시 저장 실패: %s", e)

    return result
```

기존 동기 `excel_to_csv()` 함수는 변경하지 않고 유지 (하위 호환). 새 `excel_to_csv_cached()` async 함수가 Redis 캐시 계층을 감싸며, 캐시 미스/Redis 장애 시 기존 `excel_to_csv()`를 fallback으로 호출.

##### (C) API 라우트에서 캐시 함수 호출

**파일**: `src/api/routes/query.py` `process_file_query()`

```python
# 기존:
# csv_result = excel_to_csv(file_bytes)

# 변경:
from src.document.excel_csv_converter import excel_to_csv_cached
from src.schema_cache.cache_manager import get_cache_manager

cache_mgr = get_cache_manager(request.app.state.config)
csv_result = await excel_to_csv_cached(file_bytes, cache_manager=cache_mgr)
csv_sheet_data = {k: asdict(v) for k, v in csv_result.items()}
```

#### 1-2. CSV 헤더 vs column_mapping 정합성 검증

**문제**: `output_generator`에서 Excel에 데이터를 채울 때, `column_mapping` 키가 실제 양식 헤더와 일치하는지 사전 검증이 없어 데이터가 0건 채워져도 원인을 알 수 없음.

**해결**: `output_generator._generate_document_file()` 내에서 Excel 채우기 전에 CSV 헤더와 column_mapping 키를 비교하여 매핑 정합성을 검증.

**파일**: `src/nodes/output_generator.py` `_generate_document_file()`

```python
def _generate_document_file(state, output_format):
    ...
    # 매핑 검증: csv_sheet_data 헤더와 column_mapping 비교
    csv_sheet_data = state.get("csv_sheet_data")
    if csv_sheet_data and column_mapping:
        _validate_mapping_against_csv(csv_sheet_data, column_mapping)
    ...
```

**검증 함수** (동일 파일에 추가):

```python
def _validate_mapping_against_csv(
    csv_sheet_data: dict[str, Any],
    column_mapping: dict[str, Optional[str]],
) -> None:
    """CSV 헤더와 column_mapping 키의 정합성을 검증하고 경고 로깅한다."""
    # csv_sheet_data에서 전체 헤더 수집
    csv_headers: set[str] = set()
    for sheet_data in csv_sheet_data.values():
        if isinstance(sheet_data, dict):
            csv_headers.update(sheet_data.get("headers", []))

    mapping_keys = set(column_mapping.keys())
    mapped_keys = {k for k, v in column_mapping.items() if v is not None}

    # 1. CSV 헤더 중 column_mapping에 없는 것
    unmapped_headers = csv_headers - mapping_keys
    if unmapped_headers:
        logger.info("CSV 헤더 중 매핑 미존재: %s", unmapped_headers)

    # 2. column_mapping 키 중 CSV 헤더에 없는 것 (불일치)
    orphan_keys = mapping_keys - csv_headers
    if orphan_keys:
        logger.warning("column_mapping 키가 CSV 헤더와 불일치: %s", orphan_keys)

    # 3. 매핑된 필드 비율 검증
    if csv_headers:
        mapped_ratio = len(mapped_keys & csv_headers) / len(csv_headers)
        logger.info(
            "매핑 정합성: CSV 헤더 %d개 중 %d개 매핑됨 (%.0f%%)",
            len(csv_headers),
            len(mapped_keys & csv_headers),
            mapped_ratio * 100,
        )
        if mapped_ratio == 0:
            logger.warning(
                "⚠ 매핑률 0%%: column_mapping 값이 모두 None이거나 "
                "CSV 헤더와 키가 완전히 불일치합니다. "
                "Excel 데이터 채우기가 실패할 가능성이 높습니다."
            )
```

### Phase 2: Silent Failure 제거

#### 2-1. `fill_excel_template` 반환 타입을 `(bytes, int)`로 변경

**파일**: `src/document/excel_writer.py`

```python
def fill_excel_template(...) -> tuple[bytes, int]:
    """Returns: (파일 바이너리, 채워진 데이터 건수)"""
    ...
    return output.getvalue(), total_filled
```

#### 2-2. `_generate_document_file`에서 `total_filled` 검증

**파일**: `src/nodes/output_generator.py` `_generate_document_file()`

```python
file_bytes, total_filled = fill_excel_template(
    file_data=uploaded_file,
    template_structure=template,
    column_mapping=column_mapping,
    rows=rows,
    sheet_mappings=sheet_mappings,
    target_sheets=target_sheets,
)

if total_filled == 0 and rows:
    logger.warning(
        "데이터 %d건이 조회되었으나 Excel에 0건 채워짐. "
        "column_mapping=%s, row_keys=%s",
        len(rows),
        {k: v for k, v in list(column_mapping.items())[:5]},
        list(rows[0].keys())[:10] if rows else [],
    )
```

#### 2-3. 텍스트 응답에 채움 건수 정보 포함

**파일**: `src/nodes/output_generator.py`

`output_generator`의 파일 생성 분기에서 `total_filled`를 텍스트 응답에 포함:

```python
if total_filled == 0:
    text_response = (
        f"⚠ 조회된 데이터 {len(rows)}건을 Excel 양식에 매핑하지 못했습니다.\n"
        f"양식의 헤더와 DB 컬럼 간 매핑이 일치하지 않습니다.\n"
        f"매핑 보고서를 확인하고 유사어를 등록해주세요.\n\n"
        + text_response
    )
```

### Phase 3: 디버깅 로그 강화

#### 3-1. `_fill_sheet`에 상세 로깅 추가

**파일**: `src/document/excel_writer.py` `_fill_sheet()`

```python
# col_assignments 빌드 직후
logger.debug(
    "시트 '%s': col_assignments=%d/%d, 매핑된 헤더=%s, 데이터 rows=%d",
    ws.title,
    len(col_assignments),
    len(header_cells),
    [(hc["value"], column_mapping.get(hc["value"])) for hc in header_cells[:5]],
    len(rows),
)

# rows가 있으면 첫 번째 row의 키 로깅
if rows:
    logger.debug(
        "시트 '%s': 첫 행 키=%s",
        ws.title,
        list(rows[0].keys())[:10],
    )
```

#### 3-2. `fill_excel_template`에 결과 검증 로깅

```python
if total_filled == 0 and any(sheet.get("header_cells") for sheet in sheets_info):
    logger.warning(
        "⚠ 데이터가 0건 채워졌습니다! column_mapping 키와 header_cells 값을 확인하세요."
    )
```

### Phase 4: `_get_value_from_row` 매칭 강화

**파일**: `src/document/excel_writer.py`

#### 4-1. 한글 필드명 직접 매칭 추가

현재 `_get_value_from_row`는 `db_column` (예: `"cmm_resource.hostname"`)으로만 검색한다. 하지만 쿼리 결과가 SQL alias를 사용하여 한글 키를 가질 수 있음 (예: `query_generator`가 `SELECT hostname AS "서버명"` 생성).

현재 함수에 **역방향 매핑** 검색을 추가:
- `column_mapping` 전체를 `_fill_sheet`에서 미리 **역매핑** dict를 생성
- `{db_column: field_name}` 형태로, `_get_value_from_row`에서 field_name으로도 검색

```python
# _fill_sheet 내부에서 역매핑 구축
reverse_mapping = {v: k for k, v in column_mapping.items() if v}

# _get_value_from_row에 reverse_mapping 전달하여 필드명으로도 검색
```

#### 4-2. 부분 매칭(substring) 폴백 추가

DB 결과 키가 `HOSTNAME`이고 매핑값이 `cmm_resource.hostname`인 경우, 현재 case-insensitive 검색(step 4)에서 이미 처리됨. 하지만 키가 `server_hostname`이고 매핑값이 `hostname`인 경우를 위한 contains 매칭 추가:

```python
# 5. 부분 매칭 (substring)
lower_col_base = db_column.split(".", 1)[-1].lower() if "." in db_column else lower_col
for key, value in data_row.items():
    if lower_col_base in key.lower() or key.lower() in lower_col_base:
        logger.debug("부분 매칭 성공: '%s' ↔ '%s'", db_column, key)
        return value
```

### Phase 5: 통합 테스트 추가

**파일**: `tests/test_excel_fill_pipeline.py` (신규)

```python
async def test_csv_redis_cache_hit():
    """동일 파일 업로드 시 Redis CSV 캐시 히트 확인"""
    # 1. excel_to_csv_cached 호출 → Redis 미스 → 변환 수행 → Redis 저장
    # 2. 동일 bytes로 재호출 → Redis 히트 (변환 스킵)

async def test_csv_redis_cache_miss_different_file():
    """다른 파일 업로드 시 Redis 캐시 미스 확인"""

async def test_csv_cache_fallback_redis_unavailable():
    """Redis 미사용 시 fallback으로 직접 변환 수행 확인"""
    # cache_manager=None → excel_to_csv() 직접 호출

def test_validate_mapping_against_csv():
    """CSV 헤더와 column_mapping 정합성 검증"""
    # mapped_ratio가 0이면 경고 로그 출력 확인

async def test_fill_excel_with_actual_template():
    """취합 예시2.xlsx 실제 템플릿으로 데이터 채우기 E2E 테스트"""
    # 1. 템플릿 파싱
    # 2. 컬럼 매핑 생성 (mock)
    # 3. 쿼리 결과 생성 (mock)
    # 4. fill_excel_template 호출
    # 5. 결과 Excel에서 데이터 검증 + total_filled > 0 확인

def test_fill_sheet_empty_mapping():
    """column_mapping이 모두 None일 때 total_filled=0 반환 확인"""

def test_fill_sheet_key_mismatch():
    """DB 결과 키와 매핑 값 불일치 시 case-insensitive 매칭 확인"""

def test_get_value_from_row_various_formats():
    """다양한 키 형식(table.col, col, alias, EAV:attr)에서 값 추출 확인"""
```

## 수정 대상 파일 요약

| 파일 | 변경 내용 |
|------|----------|
| `src/document/excel_csv_converter.py` | `excel_to_csv_cached()` async 함수 추가 (Redis 캐시 + fallback) |
| `src/schema_cache/redis_cache.py` | `save_csv_cache()`, `load_csv_cache()` 메서드 추가 |
| `src/api/routes/query.py` | `process_file_query()`에서 `excel_to_csv_cached` 호출로 변경 |
| `src/document/excel_writer.py` | 반환 타입 `(bytes, int)`, 디버그 로깅, 역매핑, 부분 매칭 |
| `src/nodes/output_generator.py` | CSV 헤더 vs 매핑 정합성 검증, `total_filled` 검증, 경고 메시지 |
| `tests/test_excel_fill_pipeline.py` | Redis CSV 캐시 테스트, fallback 테스트, 매핑 검증 테스트, E2E 테스트 (신규) |

## 수정 순서

1. **Phase 1** (CSV 캐시 + 매핑 검증) → 중복 변환 제거, 매핑 불일치 조기 감지
2. **Phase 2** (Silent Failure 제거) → 사용자가 빈 파일 여부를 즉시 인지 가능
3. **Phase 3** (디버깅 로그) → 잔여 원인 추적용 상세 로깅
4. **Phase 4** (매칭 강화) → Phase 3 로그 분석 결과에 따라 적용 범위 결정
5. **Phase 5** (테스트) → 회귀 방지

## 관련 결정

- D-007: 문서 처리 LLM 의미 매핑
- D-012: 매핑-우선 필드 매핑 + 유사어 등록
- D-015: Excel→CSV 변환으로 LLM 컨텍스트 보강
- D-018: LLM 지능형 필드 매핑 + 매핑 보고서 + 피드백 학습
