# Plan 26: 스키마 조회 최적화 — Redis 우선 캐시 전략

> 작성일: 2026-03-25
> 구현 완료일: 2026-03-25
> 상태: **구현 완료**
> 관련 파일: `src/nodes/schema_analyzer.py`, `src/schema_cache/`, `src/config.py`
> 의사결정: `docs/02_decision.md` D-019

---

## 1. 문제 현상

매 사용자 요청마다 DB 스키마를 조회하고 있다. 3단계 캐시(메모리→Redis→파일)가 구현되어 있지만, 메모리 캐시(5분 TTL) 만료 후에는 변경 감지를 위해 **매번 DB에 fingerprint SQL을 실행**한다.

### 변경 전 흐름

```
1차: 메모리 캐시 확인 (TTL 5분) → 히트 시 바로 반환
2차: DB에 fingerprint SQL 실행 (_fetch_fingerprint)  ← 매번 DB 조회!
     → Redis 캐시의 fingerprint와 비교 (is_changed)
     → 불변이면 Redis에서 스키마 복원
3차: DB 전체 스키마 조회 (캐시 미스 또는 fingerprint 변경)
```

### 문제 원인

`_fetch_fingerprint(client)`가 `FINGERPRINT_SQL`을 DB에 실행하여 `information_schema.columns`를 조회한다. 이 DB 조회는 메모리 캐시 5분 만료 후 **매 요청마다** 발생한다.

### Fingerprint SQL

```sql
SELECT table_name, COUNT(*) AS column_count
FROM information_schema.columns
WHERE table_schema NOT IN ('information_schema', 'pg_catalog')
GROUP BY table_name
ORDER BY table_name
```

---

## 2. 수정 방안: Fingerprint TTL 기반 지연 검증

Redis에 fingerprint 검증 타임스탬프를 저장하고, TTL(기본 30분) 내에서는 DB에 fingerprint SQL을 실행하지 않고 Redis 캐시를 그대로 신뢰한다.

### 변경 후 흐름

```
1차: 메모리 캐시 확인 (TTL 5분)
     → 히트: 바로 반환

2차-A: Redis 캐시 확인 + fingerprint TTL(30분) 유효
     → DB 조회 없이 Redis에서 복원 ← 핵심 개선

2차-B: Redis 캐시 확인 + fingerprint TTL 만료
     → DB fingerprint SQL 실행 → 비교 → 불변이면 TTL 갱신 후 Redis에서 복원
     → Redis에 스키마 캐시 없음: 3차로 진행

3차: DB 전체 스키마 조회 (최초 또는 스키마 변경 시)
     → Redis + 파일에 이중 저장
     → fingerprint 타임스탬프 기록
```

---

## 3. 변경 파일 및 구현 상세

### 3.1 `src/config.py` (line 193) — 설정 추가

`SchemaCacheConfig`에 fingerprint 검증 주기 설정 추가:

```python
class SchemaCacheConfig(BaseSettings):
    cache_dir: str = ".cache/schema"
    enabled: bool = True
    backend: str = "redis"
    auto_generate_descriptions: bool = True
    fingerprint_ttl_seconds: int = 1800  # fingerprint 검증 주기 (기본 30분)
```

환경변수: `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS`

### 3.2 `src/schema_cache/redis_cache.py` — Redis 키 확장 + 메서드 추가

**새 Redis 키:**
```
schema:{db_id}:fingerprint_checked_at   # 마지막 fingerprint 검증 시각 (Unix timestamp)
```

**새 메서드 2개** (line 272-306, `is_changed` 뒤에 배치):

```python
async def is_fingerprint_fresh(self, db_id: str, ttl_seconds: int) -> bool:
    """Redis에 저장된 fingerprint 검증 타임스탬프가 TTL 내인지 확인한다."""
    if not self._connected or self._redis is None:
        return False
    try:
        key = self._key(db_id, "fingerprint_checked_at")
        checked_at = await self._redis.get(key)
        if checked_at is None:
            return False
        return (time.time() - float(checked_at)) < ttl_seconds
    except Exception as e:
        logger.warning("fingerprint freshness 확인 실패: %s", e)
        return False

async def refresh_fingerprint_checked_at(self, db_id: str) -> None:
    """fingerprint 검증 타임스탬프를 현재 시각으로 갱신한다."""
    if not self._connected or self._redis is None:
        return
    try:
        key = self._key(db_id, "fingerprint_checked_at")
        await self._redis.set(key, str(time.time()))
    except Exception as e:
        logger.warning("fingerprint 타임스탬프 갱신 실패: %s", e)
```

**`save_schema()` 수정** (line 172-174): 파이프라인에 fingerprint_checked_at 기록 추가:

```python
# fingerprint 검증 타임스탬프 기록
fp_ts_key = self._key(db_id, "fingerprint_checked_at")
pipe.set(fp_ts_key, str(time.time()))
```

### 3.3 `src/schema_cache/cache_manager.py` (line 170-193) — 위임 메서드 추가

`is_changed()` 메서드 뒤에 2개 메서드 추가:

```python
async def is_fingerprint_fresh(self, db_id: str) -> bool:
    """fingerprint의 TTL이 아직 유효한지 확인한다.
    Redis 백엔드에서만 동작하며, 파일 백엔드는 항상 False 반환.
    """
    if self._backend == "redis" and await self.ensure_redis_connected():
        ttl = self._config.schema_cache.fingerprint_ttl_seconds
        return await self._redis_cache.is_fingerprint_fresh(db_id, ttl)
    return False

async def refresh_fingerprint_ttl(self, db_id: str) -> None:
    """fingerprint 검증 타임스탬프를 현재 시각으로 갱신한다."""
    if self._backend == "redis" and await self.ensure_redis_connected():
        await self._redis_cache.refresh_fingerprint_checked_at(db_id)
```

### 3.4 `src/nodes/schema_analyzer.py` (line 276-313) — 캐시 조회 흐름 변경

`_get_schema_with_cache()` 함수의 2차 캐시 부분을 2차-A / 2차-B로 분리:

```python
# 1차: 메모리 캐시 확인 (기존과 동일)
full_schema = _schema_cache.get(db_id)
if full_schema is not None:
    ...
    return full_schema, {}, descriptions, synonyms

# 2차-A: Redis 캐시 + fingerprint TTL 유효 (DB 조회 없이 바로 반환)
fingerprint_fresh = await cache_mgr.is_fingerprint_fresh(db_id)
if fingerprint_fresh:
    cached_schema_dict = await cache_mgr.get_schema(db_id)
    if cached_schema_dict is not None:
        full_schema = _reconstruct_schema_info(cached_schema_dict)
        _schema_cache.set(full_schema, db_id)
        descriptions = await cache_mgr.get_descriptions(db_id)
        synonyms = await cache_mgr.load_synonyms_with_global_fallback(
            db_id, cached_schema_dict
        )
        logger.info(
            "Redis 캐시 히트 (fingerprint TTL 유효): db_id=%s", db_id
        )
        return full_schema, cached_schema_dict, descriptions, synonyms

# 2차-B: fingerprint TTL 만료 -- DB에서 fingerprint 조회
current_fingerprint = await _fetch_fingerprint(client)
if current_fingerprint is not None:
    changed = await cache_mgr.is_changed(db_id, current_fingerprint)
    if not changed:
        await cache_mgr.refresh_fingerprint_ttl(db_id)  # TTL 갱신
        cached_schema_dict = await cache_mgr.get_schema(db_id)
        if cached_schema_dict is not None:
            full_schema = _reconstruct_schema_info(cached_schema_dict)
            _schema_cache.set(full_schema, db_id)
            descriptions = await cache_mgr.get_descriptions(db_id)
            synonyms = await cache_mgr.load_synonyms_with_global_fallback(
                db_id, cached_schema_dict
            )
            logger.info(
                "캐시 히트 (fingerprint 재검증): db_id=%s, fingerprint=%s, backend=%s",
                db_id,
                current_fingerprint,
                cache_mgr.backend,
            )
            return full_schema, cached_schema_dict, descriptions, synonyms

# 3차: DB 전체 조회 (기존과 동일)
```

### 3.5 `src/nodes/multi_db_executor.py` (line 210-281) — SchemaCacheManager 통합 + TTL 로직

`_analyze_schema()` 함수를 `PersistentSchemaCache` 직접 사용에서 `SchemaCacheManager` 통합 사용으로 변경:

```python
from src.schema_cache.cache_manager import get_cache_manager

cache_mgr = get_cache_manager(app_config)

# 2차-A: Redis 캐시 + fingerprint TTL 유효
try:
    fingerprint_fresh = await cache_mgr.is_fingerprint_fresh(db_id)
    if fingerprint_fresh:
        cached = await cache_mgr.get_schema(db_id)
        if cached is not None:
            logger.info(
                "멀티DB Redis 캐시 히트 (fingerprint TTL 유효): db_id=%s",
                db_id,
            )
            return cached
except Exception as e:
    logger.warning("멀티DB fingerprint TTL 확인 실패 (%s): %s", db_id, e)

# 2차-B: fingerprint TTL 만료 -- DB에서 fingerprint 조회
try:
    result = await client.execute_sql(FINGERPRINT_SQL)
    if result.rows:
        current_fp = compute_fingerprint(result.rows)
        changed = await cache_mgr.is_changed(db_id, current_fp)
        if not changed:
            await cache_mgr.refresh_fingerprint_ttl(db_id)
            cached = await cache_mgr.get_schema(db_id)
            if cached is not None:
                logger.info(
                    "멀티DB 캐시 히트 (fingerprint 재검증): db_id=%s, fingerprint=%s",
                    db_id, current_fp,
                )
                return cached
except Exception as e:
    logger.warning("멀티DB fingerprint 조회 실패 (%s): %s", db_id, e)

# 3차: 캐시 미스 — 전체 스키마 조회
...
# 캐시 매니저를 통해 저장 (Redis + 파일 이중 저장)
await cache_mgr.save_schema(db_id, schema_dict)
```

**추가 변경**: `from src.schema_cache.persistent_cache import PersistentSchemaCache` import 삭제, `persistent` 변수 제거.

---

## 4. 변경하지 않은 부분

| 파일 | 이유 |
|------|------|
| `src/schema_cache/fingerprint.py` | fingerprint 생성 로직 자체는 변경 없음 |
| `src/schema_cache/persistent_cache.py` | 파일 캐시는 Redis 장애 시 폴백 용도로 유지 |
| `src/state.py` | State 구조 변경 없음 |
| 기존 Redis 키 구조 | 하위 호환 유지 (새 키만 추가) |

---

## 5. 효과 비교

| 시나리오 | 변경 전 DB 조회 | 변경 후 DB 조회 |
|---------|---------------|---------------|
| 5분 이내 재요청 | 없음 (메모리 캐시) | 없음 (메모리 캐시) |
| 5~30분 이내 재요청 | fingerprint SQL 1회 | **없음 (Redis TTL 유효)** |
| 30분 후 재요청 | fingerprint SQL 1회 | fingerprint SQL 1회 (TTL 갱신) |
| DB 스키마 변경 후 | 전체 스키마 조회 | 전체 스키마 조회 (최대 30분 지연) |
| Redis 장애 시 | 파일 캐시 폴백 | 파일 캐시 폴백 (기존과 동일) |

**트레이드오프**: 스키마 변경 반영이 최대 30분 지연될 수 있다. `SCHEMA_CACHE_FINGERPRINT_TTL_SECONDS` 환경변수로 조절 가능.

---

## 6. 구현 순서 및 완료 상태

| 단계 | 작업 | 파일 | 상태 |
|------|------|------|------|
| 1 | `fingerprint_ttl_seconds` 설정 추가 | `src/config.py` | **완료** |
| 2 | Redis 캐시에 freshness 메서드 추가 + save_schema 파이프라인 수정 | `src/schema_cache/redis_cache.py` | **완료** |
| 3 | CacheManager에 freshness 위임 메서드 추가 | `src/schema_cache/cache_manager.py` | **완료** |
| 4 | schema_analyzer 캐시 흐름 2차-A/2차-B 분리 | `src/nodes/schema_analyzer.py` | **완료** |
| 5 | multi_db_executor SchemaCacheManager 통합 + TTL 로직 | `src/nodes/multi_db_executor.py` | **완료** |
| 6 | 단위 테스트 작성 (19 cases) | `tests/test_schema_cache/test_fingerprint_ttl.py` | **완료** |
| 7 | 기존 테스트 regression 확인 | `pytest tests/test_schema_cache/ -v` | **완료** |

---

## 7. 검증 결과

### 테스트 실행 결과

| 항목 | 결과 |
|------|------|
| 기존 테스트 (regression) | **196/196 PASSED** |
| 새 테스트 (fingerprint TTL) | **19/19 PASSED** |
| 전체 합계 | **215/215 PASSED** |
| 아키텍처 검사 (arch-check) | **위반 0건, 경고 0건** (65파일, 197 import) |

### 새 테스트 커버리지 (`tests/test_schema_cache/test_fingerprint_ttl.py`)

**RedisSchemaCache 레벨 (10 tests)**:
- `is_fingerprint_fresh`: TTL 내 True, 만료 False, 키 없음 False, 미연결 False, 경계값, Redis 에러 시 False
- `refresh_fingerprint_checked_at`: 정상 갱신, 미연결 시 무동작, Redis 에러 시 무동작
- `save_schema`: fingerprint_checked_at 파이프라인 포함 확인

**CacheManager 레벨 (7 tests)**:
- `is_fingerprint_fresh`: Redis 백엔드 True/False, 파일 백엔드 항상 False, Redis 연결 실패 시 False
- `refresh_fingerprint_ttl`: Redis 위임, 파일 백엔드 무동작, Redis 연결 실패 시 무동작

**Config 테스트 (2 tests)**:
- `fingerprint_ttl_seconds` 기본값 1800 확인
- 환경변수로 커스텀 값 설정 확인

### 로그 기반 통합 확인 시나리오

| 시나리오 | 예상 로그 |
|---------|----------|
| 첫 요청 | `"캐시 미스, DB 전체 스키마 조회"` |
| 30분 이내 재요청 | `"Redis 캐시 히트 (fingerprint TTL 유효)"` — DB 조회 없음 |
| 30분 후 재요청 | `"캐시 히트 (fingerprint 재검증)"` — fingerprint SQL만 1회 |
| DB 스키마 변경 후 30분 이내 | 이전 캐시 사용 (최대 30분 지연) |
| DB 스키마 변경 후 30분 이후 | `"fingerprint 변경 감지"` → 전체 재조회 |
| 멀티DB 첫 요청 | `"멀티DB Redis 캐시 히트 (fingerprint TTL 유효)"` 또는 전체 조회 |
