# Plan 30: 캐시 값 유효성 검증 및 무효화 정합성 점검

## 배경

Plan 29에서 **스키마 캐시의 정책 위반** (폴백 누락, 이중 저장 누락 등)을 식별하고 일부 수정하였다.
그러나 스키마 캐시(`schema`, `descriptions`, `synonyms`) 외에도 프로젝트에는 **여러 종류의 캐시 데이터**가 존재하며, 이들에 대한 값 유효성 검증과 무효화 처리가 일관되게 적용되어 있는지 확인이 필요하다.

본 계획은 **모든 캐시 저장소에 저장된 값이 유효한지 검증하는 로직의 유무를 점검**하고, 누락된 검증/무효화 처리를 보완하는 것이 목표이다.

---

## 1. 캐시 데이터 전체 목록

프로젝트에서 사용하는 캐시 데이터를 저장소별로 정리한다.

### 1.1 메모리 캐시 (SchemaMemoryCache)

| 데이터 | 키 형태 | TTL | 유효성 검증 | 현재 상태 |
|--------|---------|-----|------------|-----------|
| 스키마 딕셔너리 | `_cache[db_id]` | 300초 (5분) | TTL 만료로만 검증 | **부분 검증** — TTL 만료 시 자동 미스 처리되나, TTL 내 스키마 변경(DDL) 감지 불가 |

### 1.2 Redis 캐시

| 데이터 | Redis 키 | TTL/검증 | 유효성 검증 | 현재 상태 |
|--------|----------|----------|------------|-----------|
| 스키마 meta | `schema:{db_id}:meta` | fingerprint 기반 | fingerprint_checked_at TTL(30분) + DB fingerprint 비교 | **정상** |
| 스키마 tables | `schema:{db_id}:tables` | 없음 (영구) | meta의 fingerprint에 의존 | **간접 검증** — meta 삭제 시 tables 미삭제 위험 |
| 스키마 relationships | `schema:{db_id}:relationships` | 없음 (영구) | meta의 fingerprint에 의존 | **간접 검증** |
| fingerprint_checked_at | `schema:{db_id}:fingerprint_checked_at` | 없음 (영구) | 저장된 timestamp와 현재 시각 비교 | **정상** |
| 컬럼 descriptions | `schema:{db_id}:descriptions` | 없음 (영구) | **검증 없음** | **미검증** — 스키마 변경 시 컬럼 삭제/추가되어도 descriptions는 갱신되지 않음 |
| DB별 synonyms | `schema:{db_id}:synonyms` | 없음 (영구, invalidate에서도 보존) | **검증 없음** | **미검증** — 스키마 변경으로 컬럼이 삭제되어도 해당 컬럼의 synonyms가 남음 |
| DB 설명 | `schema:db_descriptions` | 없음 (영구) | **검증 없음** | **미검증** — DB 삭제/이름 변경 시 오래된 설명 잔존 |
| 글로벌 synonyms | `synonyms:global` | 없음 (영구) | **검증 없음** | **미검증** — 전역 사전이므로 참조 무결성 개념 약함 (허용 가능) |
| resource_type synonyms | `synonyms:resource_types` | 없음 (영구) | **검증 없음** | **미검증** — EAV 구조의 RESOURCE_TYPE 값이 변경되어도 갱신 없음 |
| eav_name synonyms | `synonyms:eav_names` | 없음 (영구) | **검증 없음** | **미검증** — EAV NAME 값 변경 시 갱신 없음 |
| structure_meta | `schema:{db_id}:structure_meta` (get_schema/save_schema로 간접 저장) | 없음 (영구) | **검증 없음** | **미검증** — 스키마 변경 시 구조 분석 결과가 무효화되지 않음 |

### 1.3 파일 캐시 (PersistentSchemaCache)

| 데이터 | 파일 경로 | TTL/검증 | 유효성 검증 | 현재 상태 |
|--------|----------|----------|------------|-----------|
| 스키마 + fingerprint | `.cache/schema/{db_id}_schema.json` | fingerprint 비교 | `is_changed()` — 새 fingerprint와 비교 | **정상** |
| _descriptions 필드 | 위 파일 내 `_descriptions` | 없음 | **검증 없음** | **미검증** — Plan 29에서 이중 저장 추가 예정이나 유효성 검증은 없음 |
| _synonyms 필드 | 위 파일 내 `_synonyms` | 없음 | **검증 없음** | **미검증** |
| _db_description 필드 | 위 파일 내 `_db_description` | 없음 | **검증 없음** | **미검증** |
| cache_version | 위 파일 내 `_cache_version` | 포맷 변경 시 | `load()` 시 버전 비교 | **정상** |

### 1.4 기타 캐시

| 데이터 | 위치 | TTL/검증 | 현재 상태 |
|--------|------|----------|-----------|
| `load_config()` LRU 캐시 | `src/config.py` (`@lru_cache`) | 없음 (프로세스 수명) | **정상** — admin API에서 `cache_clear()` 호출 |
| `config/db_profiles/{db_id}.yaml` | 파일 | 없음 | **미검증** — `schema_analyzer`에서 자동 생성되나, 스키마 변경 시 갱신 로직 없음 |

---

## 2. 캐시 설계 원칙 (신규)

본 계획에서 적용할 캐시 설계 원칙을 먼저 정의한다.

### 원칙 1: 파일 캐시는 1회 읽기 후 갱신 시에만 재읽기

파일 캐시(`PersistentSchemaCache`)는 디스크 I/O 비용이 높으므로:
- 프로세스 시작 시 또는 최초 조회 시 **1회만** 읽는다.
- 이후에는 메모리 캐시/Redis 캐시를 우선 사용한다.
- 파일 캐시 재읽기는 **fingerprint 변경이 감지되어 스키마가 갱신된 경우**에만 수행한다.
- 현재 `PersistentSchemaCache.load()`가 매번 파일 I/O를 수행하는 구조이므로, 파일 캐시 로드 결과를 메모리에 보관하고 갱신 시에만 파일을 재읽기하도록 개선한다.

### 원칙 2: 캐시 저장 전 유효성 검증 필수

캐시에 잘못된 데이터가 저장되면 후속 파이프라인 전체에 영향을 미친다.
따라서 **캐시에 저장하기 전에 데이터 유효성을 검증**하고, 검증 실패 시 캐시에 등록하지 않는다.

| 캐시 항목 | 필수 검증 조건 | 검증 실패 시 동작 |
|-----------|---------------|------------------|
| 스키마 (tables) | `tables`가 비어있지 않아야 함 (`len(tables) > 0`) | 캐시 저장 거부, 기존 캐시 유지, 에러 로그 |
| 스키마 (columns) | 각 테이블에 `columns` 배열이 1개 이상이어야 함 | 해당 테이블 제외 후 저장, 경고 로그 |
| fingerprint | 빈 문자열이 아니어야 함 | 캐시 저장 거부 |
| descriptions | 키가 `table.column` 형식이어야 함 | 잘못된 키 무시, 유효한 항목만 저장 |
| synonyms | 키가 `table.column` 형식, 값이 비어있지 않은 리스트 | 빈 리스트 항목 제거 후 저장 |
| DB 설명 | 빈 문자열이 아니어야 함 | 저장 거부 |
| structure_meta | `patterns` 키가 존재해야 함 | 저장 거부 |

### 원칙 3: invalidate 시 글로벌 synonyms만 보존

- `invalidate(db_id)`: DB별 스키마, descriptions, **DB별 synonyms** 모두 삭제
- 글로벌 synonyms (`synonyms:global`, `synonyms:resource_types`, `synonyms:eav_names`)만 보존
- 이는 DB별 데이터의 정합성을 보장하기 위함이다 (stale entry 잔존 방지)

---

## 3. 식별된 문제점

### 문제 A: 캐시 저장 시 유효성 검증 없음 (신규)

**현상**: `save_schema()`, `save_descriptions()`, `save_synonyms()` 등에서 입력 데이터의 유효성을 검증하지 않고 그대로 저장한다. 예를 들어:
- `tables`가 빈 딕셔너리인 스키마가 저장될 수 있음
- 컬럼이 0개인 테이블 정보가 저장될 수 있음
- 빈 문자열 fingerprint로 저장될 수 있음

**영향**:
- 잘못된 스키마 캐시가 저장되면 후속 쿼리 생성 노드에서 "테이블 없음" 에러 발생
- LLM에 빈 스키마가 전달되어 무의미한 SQL 생성
- fingerprint가 빈 문자열이면 모든 비교에서 "변경됨"으로 판단 → 매번 DB 조회

**수정 방안**:
1. `SchemaCacheManager.save_schema()`에 유효성 검증 게이트 추가
2. `RedisSchemaCache.save_schema()`, `PersistentSchemaCache.save()`에도 동일 검증
3. 검증 실패 시 `False` 반환 + 경고 로그, 기존 캐시를 유지

### 문제 B: 스키마 변경 시 descriptions/synonyms 정합성 깨짐

**현상**: `SchemaCacheManager.refresh_cache()` 또는 `get_schema_or_fetch()` 3차 DB 조회 경로에서 스키마가 갱신되면:
- **schema 키** (meta, tables, relationships)는 새 데이터로 교체됨
- **descriptions 키**는 그대로 유지 → 삭제된 컬럼에 대한 설명이 남고, 새 컬럼에 대한 설명은 없음
- **synonyms 키**는 `invalidate()` 시에도 의도적으로 보존 → 삭제된 컬럼에 대한 유사단어가 남음

**영향**:
- field_mapper가 이미 존재하지 않는 컬럼의 synonym으로 매핑 시도 → 쿼리 생성 오류
- descriptions에 존재하지 않는 컬럼 설명이 LLM 프롬프트에 포함 → 혼란

**수정 방안**:
1. `refresh_cache()` 또는 스키마 갱신 시, 새 스키마의 컬럼 목록과 기존 descriptions/synonyms를 비교
2. 새 스키마에 없는 `table.column` 키를 descriptions/synonyms에서 제거 (stale entry 정리)
3. 새로 추가된 컬럼은 `description_status`를 `"pending"`으로 마킹하여 자동 생성 유도

### 문제 C: `invalidate()` 시 DB별 synonyms 보존으로 stale 데이터 잔존

**현상**: `RedisSchemaCache.invalidate(db_id)`는 `meta`, `tables`, `relationships`, `descriptions` 키를 삭제하지만 `synonyms` 키는 보존한다.

**이전 의도**: synonyms는 운영자가 수동 등록한 것이므로 보존

**변경 결정**: **글로벌 synonyms만 유지하고, DB별 synonyms와 descriptions는 invalidate 시 함께 삭제한다.**

**근거**:
- DB별 synonyms는 특정 스키마의 `table.column`에 종속되므로, 스키마가 무효화되면 synonyms도 무효
- 운영자가 등록한 유사단어는 글로벌 사전(`synonyms:global`)에 보존되어 있으므로 DB별 삭제해도 손실 없음
- 스키마 재생성 시 글로벌 사전에서 자동으로 DB별 synonyms를 재구축 (`load_synonyms_with_global_fallback`)
- descriptions/synonyms를 보존하면 stale entry가 잔존하여 쿼리 품질 저하

**수정 방안**:
1. `RedisSchemaCache.invalidate()` 삭제 대상에 `synonyms`, `descriptions` 모두 포함
2. 글로벌 사전 (`synonyms:global`, `synonyms:resource_types`, `synonyms:eav_names`, `schema:db_descriptions`)은 절대 삭제하지 않음
3. 기존 "synonyms 영구 보존" 정책을 "글로벌 synonyms만 영구 보존"으로 변경

### 문제 D: 파일 캐시 매번 디스크 I/O 발생 (신규)

**현상**: `PersistentSchemaCache.load()`는 호출될 때마다 파일을 열어 JSON을 파싱한다. `SchemaCacheManager`의 Redis 폴백 경로 (`get_schema()`, `get_descriptions()`, `get_synonyms()` 등)에서 파일 캐시를 조회할 때마다 디스크 I/O가 발생한다.

**영향**: Redis가 정상이면 문제 없으나, Redis 장애 시 매 요청마다 파일 I/O가 반복되어 성능 저하.

**수정 방안**:
1. `PersistentSchemaCache`에 인메모리 버퍼 추가 — 최초 `load()` 시 파일 내용을 메모리에 보관
2. `save()` 호출 시 인메모리 버퍼도 갱신
3. `invalidate()` 호출 시 인메모리 버퍼 삭제
4. 파일 재읽기는 `save()` 후 또는 `invalidate()` 후 다음 `load()` 시에만 수행

### 문제 E: resource_type_synonyms / eav_name_synonyms — 값 변경 감지 없음

**현상**: EAV 구조 DB에서 RESOURCE_TYPE이나 NAME 컬럼의 실제 값이 변경(추가/삭제)되어도 Redis의 `synonyms:resource_types`, `synonyms:eav_names`는 갱신되지 않는다.

**영향**: 삭제된 RESOURCE_TYPE에 대한 유사단어가 남아 쿼리 생성에 혼란을 줄 수 있다.

**수정 방안**:
1. `refresh_cache()` 시 EAV 값 목록을 DB에서 재조회하여 stale 항목 정리
2. 또는 resource_type/eav_name synonyms에도 연결된 `db_id` + `fingerprint` 메타를 기록하여 스키마 변경 시 재검증

### 문제 F: structure_meta 캐시 — 스키마 변경 시 무효화 안됨

**현상**: `schema_analyzer`에서 `{db_id}:structure_meta` 키로 LLM 구조 분석 결과를 Redis에 저장한다. 이 키는 `schema:{db_id}:structure_meta`로 `save_schema()`를 통해 저장되므로, `invalidate()`에서 `schema:{db_id}:*` 패턴 삭제 시 **일부만 삭제**된다.

실제로 `invalidate()`는 `meta`, `tables`, `relationships`, `descriptions` 4개 suffix만 명시적으로 삭제하므로 `structure_meta`는 남는다.

**영향**: 스키마 구조가 변경되었는데 이전 구조 분석 결과가 사용됨 → 잘못된 EAV/계층 패턴 감지

**수정 방안**:
1. `invalidate()` 삭제 대상에 `fingerprint_checked_at`, `structure_meta` 추가
2. 또는 `invalidate()` 시 `schema:{db_id}:*` 패턴으로 synonyms 제외 전체 삭제

### 문제 G: 파일 캐시와 Redis 캐시 간 정합성 보장 없음

**현상**: `save_schema()`는 Redis + 파일 이중 저장을 수행하지만, 한쪽만 성공한 경우 정합성이 깨진다. 또한 `invalidate()`도 한쪽 실패 시 불일치 발생.

**영향**:
- Redis에만 저장 성공 → Redis 장애 시 파일 폴백에서 이전 스키마 반환
- 파일에만 저장 성공 → Redis 복구 후 이전 스키마 반환

**수정 방안**:
1. `get_schema_or_fetch()` 2차-B 경로에서 fingerprint 비교로 정합성 자동 교정 (현재 구현됨)
2. Redis 폴백 시 파일 캐시의 fingerprint와 현재 DB fingerprint를 비교하여 stale 여부 확인 추가
3. Redis 복구 후 파일 캐시에서 Redis로 동기화하는 로직 (선택적)

### 문제 H: 메모리 캐시 TTL 내 스키마 변경 감지 불가

**현상**: `SchemaMemoryCache`는 순수 TTL 기반이다. TTL(300초) 내에 DB 스키마가 변경되어도 감지하지 못하고 이전 스키마를 반환한다.

**영향**: 인프라 DB에서는 스키마 변경이 드물어 실질적 영향은 낮으나, 개발/테스트 환경에서는 문제가 될 수 있다.

**수정 방안**:
1. 현재 구조 유지 (인프라 DB 특성상 스키마 변경 빈도 낮음, TTL 5분은 합리적)
2. 필요 시 `invalidate_memory_cache()` 호출로 즉시 무효화 가능 (이미 구현됨)
3. 메모리 캐시에도 fingerprint를 저장하고, Redis/파일 캐시 조회 시 fingerprint 비교 추가 (과잉 가능성)

### 문제 I: `config/db_profiles/` YAML 파일 — 갱신/무효화 없음

**현상**: `_save_structure_profile()`이 `config/db_profiles/{db_id}.yaml`에 구조 분석 결과를 저장하지만, 스키마 변경 시 이 파일을 갱신/삭제하는 로직이 없다.

**영향**: 수동으로 db_profiles을 참조하는 경우 오래된 구조 정보 사용

**수정 방안**:
1. `invalidate()` 시 해당 db_profiles 파일도 삭제
2. 또는 db_profiles 파일에 fingerprint를 기록하여 로드 시 유효성 검증

### 문제 J: 글로벌 유사단어 파일 자동 로드 — 중복 로드 방지 미흡

**현상**: `schema_analyzer`에서 매 호출마다 글로벌 유사단어가 비어있으면 파일에서 로드한다. 그런데 `SynonymLoader`의 `_last_file_mtime`은 인스턴스 변수여서, `schema_analyzer`가 매번 새 `SynonymLoader` 인스턴스를 생성하면 변경 감지가 작동하지 않는다.

**영향**: 이미 Redis에 로드되어 있으면 `existing_global`이 비어있지 않으므로 실질적 중복은 없으나, Redis가 비어있을 때 매번 파일 I/O 발생

**수정 방안**:
1. Redis에 글로벌 synonyms 로드 여부 플래그(`synonyms:global:loaded_at`) 저장
2. 또는 현행 "비어있으면 로드" 방식 유지 (실질적 문제 낮음)

---

## 4. 수정 우선순위

| 순위 | 문제 | 영향도 | 난이도 | 비고 |
|------|------|--------|--------|------|
| 1 | **A** (캐시 저장 시 유효성 검증 없음) | 높음 — 잘못된 캐시가 파이프라인 전체에 전파 | 중 | 저장 전 필수 검증 게이트 |
| 2 | **C** (invalidate 시 DB별 synonyms/descriptions 보존) | 높음 — stale entry 잔존, 쿼리 품질 저하 | 낮 | 글로벌 synonyms만 보존으로 정책 변경 |
| 3 | **B** (descriptions/synonyms stale entry 정리) | 높음 — 쿼리 생성 품질에 직접 영향 | 중 | 스키마 갱신 시 자동 정리 |
| 4 | **F** (structure_meta 무효화 누락) | 중 — 잘못된 구조 분석 | 낮 | invalidate()에 suffix 추가 |
| 5 | **D** (파일 캐시 매번 디스크 I/O) | 중 — Redis 장애 시 성능 저하 | 중 | 인메모리 버퍼 추가 |
| 6 | **E** (resource_type/eav_name 값 변경 감지) | 중 — EAV 쿼리 정확도 | 중 | force refresh 시 재검증 |
| 7 | **I** (db_profiles 갱신 없음) | 낮 — 수동 참조 시에만 영향 | 낮 | invalidate() 시 삭제 추가 |
| 8 | **G** (Redis↔파일 정합성) | 낮 — fingerprint 비교로 자동 교정 | 중 | 현행 유지 |
| 9 | **H** (메모리 캐시 TTL 내 변경) | 낮 — 인프라 DB 특성상 드묾 | 낮 | 현행 유지 |
| 10 | **J** (글로벌 synonyms 중복 로드) | 낮 — 실질적 중복 없음 | 낮 | 현행 유지 |

---

## 5. 구현 계획

### Phase 1: 캐시 저장 전 유효성 검증 게이트 (문제 A)

**목표**: 잘못된 데이터가 캐시에 저장되지 않도록 저장 전 검증 로직을 추가한다.

#### 1-1. 스키마 유효성 검증 함수 추가

```python
# src/schema_cache/cache_manager.py

def _validate_schema_dict(schema_dict: dict) -> tuple[bool, str]:
    """스키마 딕셔너리의 유효성을 검증한다.

    Returns:
        (유효 여부, 실패 사유)
    """
    tables = schema_dict.get("tables", {})
    if not tables:
        return False, "tables가 비어있음"

    empty_tables = [t for t, d in tables.items() if not d.get("columns")]
    if empty_tables:
        return False, f"컬럼 없는 테이블: {empty_tables}"

    return True, ""
```

#### 1-2. `save_schema()`에 검증 게이트 적용

```python
async def save_schema(self, db_id, schema_dict, fingerprint=None):
    # 유효성 검증
    valid, reason = _validate_schema_dict(schema_dict)
    if not valid:
        logger.warning("스키마 캐시 저장 거부 (db_id=%s): %s", db_id, reason)
        return False

    if fingerprint is not None and not fingerprint.strip():
        logger.warning("빈 fingerprint로 캐시 저장 시도 거부: db_id=%s", db_id)
        return False

    # ... 기존 저장 로직
```

#### 1-3. descriptions/synonyms 저장 시 키 형식 검증

```python
def _validate_column_keys(data: dict, label: str) -> dict:
    """table.column 형식이 아닌 키를 필터링한다."""
    valid = {}
    for key, value in data.items():
        if "." in key:
            valid[key] = value
        else:
            logger.warning("%s 키 형식 오류 무시: %s", label, key)
    return valid
```

#### 1-4. synonyms 저장 시 빈 리스트 항목 제거

```python
# save_synonyms() 내부
synonyms = {k: v for k, v in synonyms.items() if v}  # 빈 리스트 제거
```

### Phase 2: invalidate 정책 변경 — DB별 synonyms/descriptions도 삭제 (문제 C, F)

**목표**: `invalidate()` 시 글로벌 사전만 보존하고 DB별 데이터는 모두 삭제한다.

#### 2-1. `RedisSchemaCache.invalidate()` 수정

```python
async def invalidate(self, db_id: str) -> bool:
    """특정 DB의 캐시를 삭제한다.

    글로벌 사전(synonyms:global, synonyms:resource_types, synonyms:eav_names,
    schema:db_descriptions)만 보존하고, DB별 데이터는 모두 삭제한다.
    """
    keys = [
        self._key(db_id, suffix)
        for suffix in (
            "meta", "tables", "relationships",
            "descriptions", "synonyms",               # DB별 descriptions/synonyms 삭제
            "fingerprint_checked_at", "structure_meta", # 기존 누락분 추가
        )
    ]
    await self._redis.delete(*keys)
```

#### 2-2. `SchemaCacheManager.invalidate()` 수정

```python
async def invalidate(self, db_id: str) -> bool:
    self._memory_cache.invalidate(db_id)

    # Redis: DB별 전체 삭제 (글로벌 사전 보존)
    if self._backend == "redis" and await self.ensure_redis_connected():
        await self._redis_cache.invalidate(db_id)

    # 파일 캐시: 파일 자체 삭제
    self._file_cache.invalidate(db_id)

    # db_profiles 파일 삭제 (문제 I)
    self._delete_db_profile(db_id)

    return True
```

#### 2-3. 기존 `delete_synonyms()` 메서드 정리

`invalidate()`가 synonyms를 삭제하게 되므로, 별도 `delete_synonyms()` 메서드는 유지하되 "글로벌 사전에서 특정 항목 삭제" 등 명시적 삭제용으로만 사용.

### Phase 3: 스키마 갱신 시 stale entry 자동 정리 (문제 B)

**목표**: 스키마가 갱신될 때 descriptions/synonyms에서 더 이상 존재하지 않는 컬럼의 항목을 자동 제거한다.

> Phase 2에서 `invalidate()` 시 DB별 synonyms/descriptions가 삭제되므로, 이 Phase는 **스키마가 갱신되지만 invalidate 없이 캐시가 업데이트되는 경로** (fingerprint 변경 감지 후 `refresh_cache()`)에서만 필요하다.

#### 3-1. `SchemaCacheManager`에 stale entry 정리 메서드 추가

```python
async def cleanup_stale_entries(self, db_id: str, schema_dict: dict) -> dict:
    """스키마 갱신 후 descriptions/synonyms에서 stale 항목을 정리한다.

    Args:
        db_id: DB 식별자
        schema_dict: 새로 갱신된 스키마 딕셔너리

    Returns:
        정리 결과 {"removed_descriptions": [...], "removed_synonyms": [...]}
    """
```

구현 로직:
1. `schema_dict["tables"]`에서 모든 `table.column` 키 집합을 추출
2. Redis/파일 캐시에서 `descriptions`의 모든 키를 조회
3. 현재 스키마에 없는 키를 제거
4. `synonyms`에 대해서도 동일 처리
5. 새로 추가된 컬럼은 `description_status`를 `"pending"`으로 마킹
6. 제거된 항목을 로그로 남김

#### 3-2. `refresh_cache()` 스키마 저장 직후 자동 호출

```python
# refresh_cache() 내부, 스키마 저장 직후
await self.cleanup_stale_entries(db_id, schema_dict)
```

### Phase 4: 파일 캐시 인메모리 버퍼 (문제 D)

**목표**: 파일 캐시를 1회 읽은 후 메모리에 보관하고, 갱신 시에만 재읽기한다.

#### 4-1. `PersistentSchemaCache`에 인메모리 버퍼 추가

```python
class PersistentSchemaCache:
    def __init__(self, ...):
        ...
        self._mem_buffer: dict[str, dict] = {}  # db_id -> 파일 내용
        self._mem_loaded: set[str] = set()       # 로드 완료된 db_id 집합

    def load(self, db_id: str) -> Optional[dict]:
        # 이미 메모리에 있으면 파일 I/O 스킵
        if db_id in self._mem_loaded:
            return self._mem_buffer.get(db_id)

        # 파일에서 읽기 (최초 1회)
        data = self._load_from_file(db_id)
        self._mem_loaded.add(db_id)
        if data is not None:
            self._mem_buffer[db_id] = data
        return data

    def save(self, db_id, schema_dict, fingerprint=None):
        # 파일 저장 + 메모리 버퍼 갱신
        success = self._save_to_file(db_id, schema_dict, fingerprint)
        if success:
            self._mem_buffer[db_id] = ...  # 저장한 전체 데이터
            self._mem_loaded.add(db_id)
        return success

    def invalidate(self, db_id):
        # 파일 삭제 + 메모리 버퍼 삭제
        self._mem_buffer.pop(db_id, None)
        self._mem_loaded.discard(db_id)
        return self._safe_delete(self._cache_file_path(db_id))
```

### Phase 5: EAV 값 변경 감지 (문제 E)

#### 5-1. `validate_eav_values()` 별도 메서드로 분리

EAV 값은 DML 변경이므로 fingerprint에 감지되지 않는다. `refresh_cache(force=True)` 시에만 수행한다.

```python
async def validate_eav_values(self, db_id: str, client: Any) -> dict:
    """EAV RESOURCE_TYPE/NAME 값의 유효성을 검증하고 stale 항목을 정리한다."""
    # DB에서 DISTINCT RESOURCE_TYPE, NAME 값 조회
    # 기존 synonyms:resource_types, synonyms:eav_names와 비교
    # 삭제된 값의 항목 제거
```

### Phase 6: 테스트

1. **캐시 저장 유효성 검증 테스트** (문제 A)
   - `tables`가 빈 스키마 저장 시도 → 거부 확인
   - 컬럼 없는 테이블 포함 스키마 → 해당 테이블 제외 확인
   - 빈 fingerprint 저장 시도 → 거부 확인
   - `table.column` 형식이 아닌 descriptions 키 → 필터링 확인

2. **invalidate 정책 변경 테스트** (문제 C)
   - `invalidate()` 후 DB별 synonyms/descriptions 삭제 확인
   - `invalidate()` 후 글로벌 사전 보존 확인
   - `invalidate_all()` 후 글로벌 사전 보존 확인

3. **stale entry 정리 테스트** (문제 B)
   - 컬럼 삭제 후 `refresh_cache()` → descriptions/synonyms에서 해당 컬럼 항목 제거 확인
   - 새 컬럼 추가 후 → `description_status`가 `pending`으로 설정 확인

4. **파일 캐시 인메모리 버퍼 테스트** (문제 D)
   - 동일 db_id에 대해 `load()` 2회 호출 → 파일 I/O 1회만 발생 확인
   - `save()` 후 `load()` → 파일이 아닌 메모리에서 반환 확인
   - `invalidate()` 후 `load()` → 파일에서 재읽기 확인

5. **structure_meta 무효화 테스트** (문제 F)
   - `invalidate()` 후 `{db_id}:structure_meta` 키 삭제 확인

6. **EAV 값 재검증 테스트** (문제 E)
   - RESOURCE_TYPE 값 변경 후 stale 항목 정리 확인

---

## 6. 현행 유지 항목 (수정 불필요)

| 항목 | 이유 |
|------|------|
| 메모리 캐시 TTL 내 변경 감지 (문제 H) | 인프라 DB 특성상 스키마 변경이 드물고, TTL 5분은 합리적. `invalidate_memory_cache()` 수동 호출 가능 |
| Redis↔파일 정합성 (문제 G) | fingerprint 비교로 자동 교정되므로 추가 구현 불필요 |
| 글로벌 synonyms 중복 로드 (문제 J) | Redis에 이미 데이터가 있으면 로드하지 않으므로 실질적 중복 없음 |
| 글로벌 synonyms 유효성 (1.2 표) | 전역 사전이므로 특정 DB/컬럼에 종속되지 않음. 수동 관리 대상 |

---

## 7. 관련 파일

| 파일 | 수정 내용 |
|------|----------|
| `src/schema_cache/cache_manager.py` | `_validate_schema_dict()`, `cleanup_stale_entries()` 추가, `invalidate()` 정책 변경, `save_schema()`/`save_descriptions()`/`save_synonyms()`에 유효성 검증 게이트 |
| `src/schema_cache/redis_cache.py` | `invalidate()` 삭제 대상에 synonyms/descriptions/structure_meta/fingerprint_checked_at 추가 |
| `src/schema_cache/persistent_cache.py` | 인메모리 버퍼(`_mem_buffer`) 추가, `load()` 1회 읽기 최적화 |
| `src/nodes/schema_analyzer.py` | db_profiles 파일 정리 연동 |
| `src/nodes/cache_management.py` | invalidate 정책 변경에 따른 안내 메시지 수정 |
| `src/api/routes/schema_cache.py` | invalidate 응답 메시지 수정 |
| `tests/test_schema_cache/` | 유효성 검증, invalidate 정책 변경, stale entry 정리, 파일 캐시 버퍼 테스트 |
