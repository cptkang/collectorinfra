"""DB별 스키마 캐시 모듈.

3단계 캐싱:
  1차: 메모리 캐시 (SchemaCache, TTL 5분)
  2차: Redis 캐시 (SchemaCacheManager, fingerprint 기반)
  2차-fallback: 파일 캐시 (PersistentSchemaCache)
  3차: DB 전체 조회
"""

from src.schema_cache.cache_manager import SchemaCacheManager, get_cache_manager
from src.schema_cache.fingerprint import compute_fingerprint
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.schema_cache.redis_cache import RedisSchemaCache

__all__ = [
    "PersistentSchemaCache",
    "RedisSchemaCache",
    "SchemaCacheManager",
    "compute_fingerprint",
    "get_cache_manager",
]
