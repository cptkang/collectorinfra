# Plan 29: 캐시 정책 위반 수정

## 배경

프로젝트의 캐시 정책:
```
1차: 메모리 캐시 (TTL 5분) → 2차: Redis 캐시 (fingerprint 기반) → 2차-fallback: 파일 캐시 → 3차: DB 전체 조회
```

**원칙**: 캐시를 1차 → 2차 → 3차 순으로 조회하고, 캐시 미스/만료/원본 변경 시에만 원본 조회. Redis 장애 시 파일 캐시로 graceful fallback.

코드 전체 분석 결과 아래 8개의 캐시 정책 위반을 발견하였다.

---

## 위반 1: `SchemaCacheManager.get_schema()` — 캐시 미스 시 DB 원본 조회 없음

**파일**: `src/schema_cache/cache_manager.py:110-137`

**현상**:
```python
async def get_schema(self, db_id: str) -> Optional[dict]:
    # Redis 시도
    if self._backend == "redis" and await self.ensure_redis_connected():
        schema_dict = await self._redis_cache.load_schema(db_id)
        if schema_dict is not None:
            return schema_dict

    # 파일 캐시 폴백
    cached = self._file_cache.get_schema(db_id)
    if cached is not None:
        return cached

    return None  # ← Redis/파일 둘 다 미스이면 None 반환, DB 조회 없음
```

**문제점**: `SchemaCacheManager`가 "캐시 조회 전용" 함수로 설계되어 DB 클라이언트를 받지 않는다. 이 때문에 캐시 미스 시 3차 DB 폴백을 할 수 없고, 호출측(`schema_analyzer._get_schema_with_cache()`, `multi_db_executor._analyze_schema()`)에서 각각 DB 폴백을 독립 구현해야 한다. 이것이 위반 7(캐시 로직 중복)의 근본 원인이다.

또한 `cache_management.py`, `api/routes/schema_cache.py` 등에서 `get_schema()`를 호출할 때 캐시 미스이면 "캐시 없음" 에러를 반환하는데, 이 중 descriptions 생성(`_handle_generate_descriptions`), synonyms 생성(`_handle_generate_synonyms`), DB 설명 생성(`_handle_generate_db_description`) 등은 운영자가 명시적으로 "생성"을 요청한 것이므로 캐시가 없어도 DB에서 스키마를 조회하여 작업을 수행하는 것이 자연스럽다.

**수정 방안**:
1. `SchemaCacheManager`에 DB 클라이언트를 받는 통합 조회 메서드 `get_schema_or_fetch(client, db_id)` 추가
2. 내부에서 Redis → 파일 → DB 전체 3단계 폴백을 일관되게 처리
3. 기존 `get_schema()`는 캐시 전용 조회로 유지 (운영자 상태 확인용)
4. 쿼리 파이프라인(`schema_analyzer`, `multi_db_executor`)과 캐시 관리 노드의 생성 작업에서 통합 메서드 사용

---

## 위반 2: `_get_schema_with_cache()` — 3차 DB 전체 조회 후 캐시 저장 누락

**파일**: `src/nodes/schema_analyzer.py:541-551`

**현상**: 3차 DB 전체 조회(`client.get_full_schema()`) 실행 후 `_schema_cache.set()`(메모리 캐시)에만 저장하고, `cache_mgr.save_schema()`(Redis/파일 캐시)를 호출하지 않는다. 이후 `schema_analyzer()` 함수의 731-735번 라인에서 별도로 `save_schema`를 호출하지만, 이는 `_get_schema_with_cache` 반환 값이 아닌 새로 변환한 `full_schema_dict`를 저장한다.

**문제점**:
- `_get_schema_with_cache`가 descriptions=`{}`, synonyms=`{}`를 반환하므로 3차 조회 시 descriptions/synonyms 로드를 하지 않는다 (캐시에 이미 있을 수 있는데 건너뜀).
- `_get_schema_with_cache`의 반환 후 호출측(`schema_analyzer`)에서 저장 책임을 지는 구조라 캐시 저장이 2중으로 분산되어 있고, `multi_db_executor`의 `_analyze_schema`는 자체적으로 저장하지만 `_get_schema_with_cache`를 사용하지 않아 로직이 중복된다.

**수정 방안**:
1. `_get_schema_with_cache()` 내 3차 조회 경로에서 `cache_mgr.save_schema()` 호출 추가
2. 3차 조회 시에도 `descriptions`, `synonyms`를 캐시에서 로드하여 반환 (스키마만 갱신되었을 뿐 설명/유사어는 유효할 수 있음)

---

## 위반 3: `get_descriptions()` / `get_synonyms()` — Redis 실패 시 파일 캐시 폴백 없음

**파일**: `src/schema_cache/cache_manager.py:311-322`, `344-355`

**현상**:
```python
async def get_descriptions(self, db_id: str) -> dict[str, str]:
    if self._backend == "redis" and await self.ensure_redis_connected():
        return await self._redis_cache.load_descriptions(db_id)
    return {}  # ← 파일 캐시 폴백 없이 빈 딕셔너리 반환

async def get_synonyms(self, db_id: str) -> dict[str, list[str]]:
    if self._backend == "redis" and await self.ensure_redis_connected():
        return await self._redis_cache.load_synonyms(db_id)
    return {}  # ← 파일 캐시 폴백 없이 빈 딕셔너리 반환
```

**문제점**: `get_schema()`는 Redis 실패 시 파일 캐시 폴백이 구현되어 있지만, `get_descriptions()`와 `get_synonyms()`는 Redis 불가 시 빈 딕셔너리를 반환한다. Redis 장애 시 descriptions/synonyms 데이터가 완전히 소실되어 field_mapper의 2단계(synonym 규칙 매핑)가 무력화된다.

**수정 방안**:
1. `save_descriptions()`에서 파일 캐시에도 이중 저장 (기존 캐시 파일의 `_descriptions` 필드로 저장)
2. `get_descriptions()`에서 Redis 실패 시 파일 캐시 폴백 조회
3. `save_synonyms()`, `get_synonyms()`에도 동일한 파일 캐시 폴백 적용

---

## 위반 4: `save_descriptions()` / `save_synonyms()` — 파일 캐시 이중 저장 누락

**파일**: `src/schema_cache/cache_manager.py:324-340`, `357-377`

**현상**: `save_schema()`는 Redis + 파일 캐시 이중 저장을 구현하지만, `save_descriptions()`와 `save_synonyms()`는 Redis에만 저장한다. `save_db_description()`은 Redis + 파일 이중 저장이 구현되어 있어 설계 불일치가 있다.

**문제점**: Redis 장애 복구 후 descriptions/synonyms가 소실될 수 있다. 또한 Redis 백엔드가 아닌 파일 백엔드로 설정된 경우 descriptions/synonyms를 아예 저장할 수 없다.

**수정 방안**:
1. `save_descriptions()`: 파일 캐시에도 `_descriptions` 필드로 이중 저장
2. `save_synonyms()`: 파일 캐시에도 `_synonyms` 필드로 이중 저장
3. 패턴을 `save_db_description()`과 일치시킴

---

## 위반 5: `delete_db_description()` — 파일 캐시 삭제 누락

**파일**: `src/schema_cache/cache_manager.py:296-307`

**현상**:
```python
async def delete_db_description(self, db_id: str) -> bool:
    if self._backend == "redis" and await self.ensure_redis_connected():
        return await self._redis_cache.delete_db_description(db_id)
    return False  # ← 파일 캐시의 _db_description 필드 삭제 누락
```

**문제점**: `save_db_description()`은 Redis + 파일 이중 저장을 하지만 `delete_db_description()`은 Redis에서만 삭제한다. 삭제 후에도 파일 캐시에 이전 DB 설명이 남아있어 `get_db_description()` 파일 폴백 시 삭제한 설명이 다시 반환될 수 있다.

**수정 방안**:
1. `delete_db_description()`에서 파일 캐시의 `_db_description` 필드도 함께 삭제 (`update_field(db_id, "_db_description", None)` 또는 전용 메서드)

---

## 위반 6: `schema_analyzer()` — 캐시 히트 경로에서도 무조건 `save_schema()` 호출

**파일**: `src/nodes/schema_analyzer.py:731-735`

**현상**:
```python
# 5. 캐시 매니저를 통해 저장 (Redis + 파일 이중 저장)
full_schema_dict = schema_to_dict(
    full_schema, list(full_schema.tables.keys()),
)
await cache_mgr.save_schema(db_id, full_schema_dict)
```

이 코드는 `_get_schema_with_cache()`의 결과와 무관하게 **항상** 실행된다. 캐시 히트(2차-A, 2차-B)로 스키마를 로드했더라도 다시 전체 스키마를 변환하여 저장한다.

**문제점**:
- 캐시 히트 시에도 불필요한 `save_schema()` 호출이 발생하여 Redis/파일 쓰기 I/O 낭비
- `_get_schema_with_cache`에서 반환한 `cached_schema_dict`와 여기서 새로 만든 `full_schema_dict`가 동일한 내용인데 중복 저장

**수정 방안**:
1. 3차 DB 조회가 실제로 발생한 경우에만 `save_schema()`를 호출하도록 조건 분기 추가
2. `_get_schema_with_cache`의 반환값에 캐시 히트 여부(cache_hit: bool)를 포함하여 호출측에서 판단

---

## 위반 7: `multi_db_executor._analyze_schema()` — `_get_schema_with_cache()`와 캐시 로직 중복

**파일**: `src/nodes/multi_db_executor.py:187-283`

**현상**: `schema_analyzer`의 `_get_schema_with_cache()`와 거의 동일한 3단계 캐시 조회 로직을 `multi_db_executor._analyze_schema()`에서 독립적으로 구현하고 있다. 차이점:
- 메모리 캐시(1차)를 사용하지 않음
- descriptions/synonyms를 로드하지 않음
- 캐시 히트 시 dict를 직접 반환 (SchemaInfo 복원 안 함)

**문제점**:
- 캐시 정책 변경 시 두 곳을 동시에 수정해야 함 (DRY 위반)
- `multi_db_executor`는 메모리 캐시를 건너뛰어 불필요한 Redis 조회 발생 가능
- fingerprint TTL 로직의 미세한 차이가 발생할 수 있음

**수정 방안**:
1. 공통 캐시 조회 로직을 `SchemaCacheManager`에 통합 메서드로 추출
2. `schema_analyzer`와 `multi_db_executor` 모두 통합 메서드를 호출하도록 리팩토링
3. 메모리 캐시도 `SchemaCacheManager` 내부로 이동하여 일관된 3단계 캐시 적용

---

## 위반 8: `schema_analyzer._get_schema_with_cache()` — 메모리 캐시 히트 시 `schema_dict` 빈 딕셔너리 반환

**파일**: `src/nodes/schema_analyzer.py:494-500`

**현상**:
```python
# 1차: 메모리 캐시 확인
full_schema = _schema_cache.get(db_id)
if full_schema is not None:
    logger.debug("메모리 캐시 히트: db_id=%s", db_id)
    descriptions = await cache_mgr.get_descriptions(db_id)
    synonyms = await cache_mgr.load_synonyms_with_global_fallback(db_id)
    return full_schema, {}, descriptions, synonyms
    #                   ^^ 빈 딕셔너리
```

**문제점**: 메모리 캐시 히트 시 `cached_schema_dict`를 `{}`로 반환한다. 호출측(`schema_analyzer:643-647`)에서 `cached_schema_dict`로 샘플 데이터를 복원하는데, 빈 딕셔너리라 캐시된 샘플 데이터가 활용되지 못한다. 결과적으로 메모리 캐시 히트 시에도 매번 DB에서 샘플 데이터를 재조회한다.

**수정 방안**:
1. 메모리 캐시 히트 시에도 Redis/파일 캐시에서 `schema_dict`를 조회하여 반환
2. 또는 메모리 캐시에 `schema_dict`도 함께 저장하도록 `SchemaCache` 확장

---

## 수정 우선순위

| 순위 | 위반 | 영향도 | 난이도 |
|------|------|--------|--------|
| 1 | **위반 1** (get_schema DB 폴백 없음) | 높음 — 캐시 미스 시 조회 불가, 로직 중복의 근본 원인 | 중 |
| 2 | **위반 3** (descriptions/synonyms 파일 폴백 없음) | 높음 — Redis 장애 시 매핑 품질 급락 | 중 |
| 3 | **위반 4** (descriptions/synonyms 이중 저장 누락) | 높음 — 위반 3의 전제조건 | 중 |
| 4 | **위반 2** (3차 조회 후 캐시 저장 누락) | 중 — 호출측에서 보완하고 있으나 descriptions 미로드 | 낮 |
| 5 | **위반 8** (메모리 히트 시 샘플 데이터 미활용) | 중 — 불필요한 DB 샘플 조회 | 낮 |
| 6 | **위반 6** (캐시 히트에도 무조건 save) | 낮 — 성능 낭비 | 낮 |
| 7 | **위반 5** (delete_db_description 파일 캐시 누락) | 낮 — 삭제 기능 불완전 | 낮 |
| 8 | **위반 7** (캐시 로직 중복) | 중 — 유지보수성, 위반 1 해결 시 자연 해소 | 높 |

## 구현 단계

### Phase 1: `SchemaCacheManager`에 DB 폴백 통합 메서드 추가 (위반 1, 7)

위반 1이 위반 7(캐시 로직 중복)의 근본 원인이므로 함께 해결한다.

1. `SchemaCacheManager`에 `get_schema_or_fetch(client, db_id)` 통합 메서드 추가
   - 내부에서 메모리(1차) → Redis(2차) → 파일(2차-fallback) → DB 전체 조회(3차) 를 일관 처리
   - fingerprint TTL 검증, DB fingerprint 조회, 캐시 저장까지 모두 포함
   - 반환값에 `cache_hit` 플래그 포함하여 호출측이 불필요한 저장을 방지할 수 있도록 함
2. 메모리 캐시(`SchemaCache`)를 `SchemaCacheManager` 내부로 이동
3. `schema_analyzer._get_schema_with_cache()` → 통합 메서드 호출로 교체
4. `multi_db_executor._analyze_schema()` → 통합 메서드 호출로 교체
5. 기존 `get_schema()`는 캐시 전용 조회로 유지 (운영자 API, 캐시 관리 노드용)

### Phase 2: 파일 캐시 폴백 보장 (위반 3, 4, 5)

1. `PersistentSchemaCache`에 descriptions/synonyms 필드 저장/로드 메서드 추가
2. `SchemaCacheManager.save_descriptions()` / `save_synonyms()`에 파일 이중 저장 추가
3. `SchemaCacheManager.get_descriptions()` / `get_synonyms()`에 파일 폴백 추가
4. `SchemaCacheManager.delete_db_description()`에 파일 캐시 삭제 추가

### Phase 3: 캐시 저장/조회 일관성 수정 (위반 2, 6, 8)

Phase 1의 통합 메서드가 3차 조회 후 캐시 저장과 cache_hit 플래그를 이미 처리하므로, 위반 2, 6은 자연 해소된다.

1. `schema_analyzer()`에서 `cache_hit` 시 `save_schema()` 스킵 (위반 6 잔여)
2. 메모리 캐시 히트 시에도 `cached_schema_dict`를 반환하도록 수정 (위반 8)
3. 3차 DB 조회 시에도 캐시에 이미 있는 descriptions/synonyms를 로드하여 반환 (위반 2 잔여)

### Phase 4: 테스트

1. Redis 장애 시나리오 테스트 (descriptions/synonyms 파일 폴백 검증)
2. 캐시 전체 미스 → DB 폴백 → 캐시 저장 검증
3. 캐시 히트/미스 경로별 저장/미저장 검증
4. `multi_db_executor`와 `schema_analyzer`의 캐시 동작 일관성 검증
