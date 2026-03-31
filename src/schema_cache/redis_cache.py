"""Redis 기반 스키마 캐시.

DB별 스키마 정보, 컬럼 설명(description), 유사 단어(synonym)를
Redis에 영구 저장하며, fingerprint 기반 변경 감지를 수행한다.

키 네이밍:
  schema:db_descriptions        -> Hash (db_id -> DB 설명, 한국어)
  schema:{db_id}:meta          -> Hash (fingerprint, cached_at, ...)
  schema:{db_id}:tables        -> Hash (table_name -> JSON)
  schema:{db_id}:relationships -> String (JSON array)
  schema:{db_id}:descriptions  -> Hash (table.column -> description)
  schema:{db_id}:synonyms      -> Hash (table.column -> JSON array)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 캐시 포맷 버전 (Redis 구조 변경 시 증가)
CACHE_FORMAT_VERSION = 1


class RedisSchemaCache:
    """Redis 기반 스키마 캐시.

    스키마 정보, 컬럼 설명, 유사 단어를 Redis Hash에 저장하며
    fingerprint 비교로 변경 감지를 수행한다.
    """

    def __init__(
        self,
        redis_config: Any,
        schema_cache_config: Any | None = None,
    ) -> None:
        """Redis 캐시를 초기화한다.

        Args:
            redis_config: RedisConfig 인스턴스
            schema_cache_config: SchemaCacheConfig 인스턴스 (선택)
        """
        self._redis_config = redis_config
        self._schema_cache_config = schema_cache_config
        self._redis: Any = None
        self._connected = False

    # === 연결 관리 ===

    async def connect(self) -> None:
        """Redis에 연결한다."""
        if self._connected and self._redis is not None:
            return

        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.Redis(
                host=self._redis_config.host,
                port=self._redis_config.port,
                db=self._redis_config.db,
                password=self._redis_config.password or None,
                ssl=self._redis_config.ssl,
                socket_timeout=self._redis_config.socket_timeout,
                decode_responses=True,
            )
            # 연결 테스트
            await self._redis.ping()
            self._connected = True
            logger.info(
                "Redis 연결 성공: %s:%d/%d",
                self._redis_config.host,
                self._redis_config.port,
                self._redis_config.db,
            )
        except Exception as e:
            self._connected = False
            self._redis = None
            logger.warning("Redis 연결 실패: %s", e)
            raise

    async def disconnect(self) -> None:
        """Redis 연결을 종료한다."""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
            self._connected = False

    async def health_check(self) -> bool:
        """Redis 연결 상태를 확인한다.

        Returns:
            연결이 정상이면 True
        """
        if not self._connected or self._redis is None:
            return False
        try:
            await self._redis.ping()
            return True
        except Exception:
            self._connected = False
            return False

    def _key(self, db_id: str, suffix: str) -> str:
        """Redis 키를 생성한다."""
        return f"schema:{db_id}:{suffix}"

    # === 기본 CRUD ===

    async def save_schema(
        self,
        db_id: str,
        schema_dict: dict,
        fingerprint: str,
    ) -> bool:
        """스키마 정보를 Redis에 저장한다.

        Args:
            db_id: DB 식별자
            schema_dict: 스키마 딕셔너리 (tables, relationships 키)
            fingerprint: 스키마 fingerprint

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            pipe = self._redis.pipeline()

            # meta
            tables = schema_dict.get("tables", {})
            total_cols = sum(
                len(t.get("columns", []))
                for t in tables.values()
            )
            meta = {
                "fingerprint": fingerprint,
                "cached_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "cache_version": str(CACHE_FORMAT_VERSION),
                "table_count": str(len(tables)),
                "total_column_count": str(total_cols),
                "description_status": "pending",
            }
            meta_key = self._key(db_id, "meta")
            pipe.delete(meta_key)
            if meta:
                pipe.hset(meta_key, mapping=meta)

            # tables (각 테이블을 Hash 필드로)
            tables_key = self._key(db_id, "tables")
            pipe.delete(tables_key)
            for table_name, table_data in tables.items():
                pipe.hset(
                    tables_key,
                    table_name,
                    json.dumps(table_data, ensure_ascii=False),
                )

            # relationships
            rels_key = self._key(db_id, "relationships")
            rels = schema_dict.get("relationships", [])
            pipe.set(rels_key, json.dumps(rels, ensure_ascii=False))

            # fingerprint 검증 타임스탬프 기록
            fp_ts_key = self._key(db_id, "fingerprint_checked_at")
            pipe.set(fp_ts_key, str(time.time()))

            await pipe.execute()
            logger.info(
                "Redis 스키마 캐시 저장: db_id=%s, fingerprint=%s, tables=%d",
                db_id,
                fingerprint,
                len(tables),
            )
            return True

        except Exception as e:
            logger.error("Redis 스키마 저장 실패: %s", e)
            return False

    async def load_schema(self, db_id: str) -> Optional[dict]:
        """Redis에서 스키마 정보를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            스키마 딕셔너리 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            meta_key = self._key(db_id, "meta")
            meta = await self._redis.hgetall(meta_key)
            if not meta:
                return None

            # 버전 확인
            cache_version = int(meta.get("cache_version", "0"))
            if cache_version != CACHE_FORMAT_VERSION:
                logger.info(
                    "Redis 캐시 버전 불일치: db_id=%s (기대: %d, 실제: %d)",
                    db_id,
                    CACHE_FORMAT_VERSION,
                    cache_version,
                )
                return None

            # tables 로드
            tables_key = self._key(db_id, "tables")
            raw_tables = await self._redis.hgetall(tables_key)
            tables = {}
            for table_name, raw_data in raw_tables.items():
                tables[table_name] = json.loads(raw_data)

            # relationships 로드
            rels_key = self._key(db_id, "relationships")
            raw_rels = await self._redis.get(rels_key)
            relationships = json.loads(raw_rels) if raw_rels else []

            return {
                "tables": tables,
                "relationships": relationships,
            }

        except Exception as e:
            logger.error("Redis 스키마 로드 실패 (db_id=%s): %s", db_id, e)
            return None

    # === 구조 분석 메타 (structure_meta) ===

    async def save_structure_meta(
        self,
        db_id: str,
        structure_meta: dict,
    ) -> bool:
        """구조 분석 결과(structure_meta)를 Redis에 저장한다.

        structure_meta는 스키마(tables/relationships)와 다른 구조이므로
        별도 키에 JSON 문자열로 저장한다.

        Args:
            db_id: DB 식별자
            structure_meta: 구조 분석 결과 (patterns, query_guide 등)

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            key = self._key(db_id, "structure_meta")
            await self._redis.set(
                key, json.dumps(structure_meta, ensure_ascii=False)
            )
            logger.info("Redis structure_meta 저장: db_id=%s", db_id)
            return True
        except Exception as e:
            logger.error("Redis structure_meta 저장 실패 (db_id=%s): %s", db_id, e)
            return False

    async def load_structure_meta(self, db_id: str) -> Optional[dict]:
        """Redis에서 구조 분석 결과(structure_meta)를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            구조 분석 딕셔너리 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            key = self._key(db_id, "structure_meta")
            raw = await self._redis.get(key)
            if raw:
                return json.loads(raw)
            return None
        except Exception as e:
            logger.error(
                "Redis structure_meta 로드 실패 (db_id=%s): %s", db_id, e
            )
            return None

    async def get_fingerprint(self, db_id: str) -> Optional[str]:
        """캐시된 fingerprint를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            fingerprint 해시 문자열 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            return await self._redis.hget(self._key(db_id, "meta"), "fingerprint")
        except Exception as e:
            logger.warning("Redis fingerprint 조회 실패: %s", e)
            return None

    async def is_changed(self, db_id: str, current_fingerprint: str) -> bool:
        """현재 fingerprint와 캐시된 fingerprint를 비교한다.

        Args:
            db_id: DB 식별자
            current_fingerprint: DB에서 조회한 현재 fingerprint

        Returns:
            True이면 스키마가 변경됨
        """
        cached = await self.get_fingerprint(db_id)
        if cached is None:
            return True
        return cached != current_fingerprint

    async def is_fingerprint_fresh(self, db_id: str, ttl_seconds: int) -> bool:
        """Redis에 저장된 fingerprint 검증 타임스탬프가 TTL 내인지 확인한다.

        Args:
            db_id: DB 식별자
            ttl_seconds: TTL 초 단위

        Returns:
            TTL 내이면 True
        """
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
        """fingerprint 검증 타임스탬프를 현재 시각으로 갱신한다.

        Args:
            db_id: DB 식별자
        """
        if not self._connected or self._redis is None:
            return
        try:
            key = self._key(db_id, "fingerprint_checked_at")
            await self._redis.set(key, str(time.time()))
        except Exception as e:
            logger.warning("fingerprint 타임스탬프 갱신 실패: %s", e)

    # === 컬럼 설명 ===

    async def save_descriptions(
        self,
        db_id: str,
        descriptions: dict[str, str],
    ) -> bool:
        """컬럼 설명을 Redis에 저장한다.

        Args:
            db_id: DB 식별자
            descriptions: {table.column: description} 매핑

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            key = self._key(db_id, "descriptions")
            if descriptions:
                await self._redis.hset(key, mapping=descriptions)

            # meta 업데이트
            await self._redis.hset(
                self._key(db_id, "meta"),
                "description_status",
                "complete",
            )
            logger.info(
                "Redis 컬럼 설명 저장: db_id=%s, count=%d",
                db_id,
                len(descriptions),
            )
            return True
        except Exception as e:
            logger.error("Redis 컬럼 설명 저장 실패: %s", e)
            return False

    async def load_descriptions(self, db_id: str) -> dict[str, str]:
        """Redis에서 컬럼 설명을 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: description} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            return await self._redis.hgetall(self._key(db_id, "descriptions"))
        except Exception as e:
            logger.warning("Redis 컬럼 설명 로드 실패: %s", e)
            return {}

    async def get_description(
        self,
        db_id: str,
        table_column: str,
    ) -> Optional[str]:
        """특정 컬럼의 설명을 반환한다.

        Args:
            db_id: DB 식별자
            table_column: "table.column" 형식

        Returns:
            설명 문자열 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            return await self._redis.hget(
                self._key(db_id, "descriptions"), table_column
            )
        except Exception:
            return None

    # === DB 설명 ===

    # DB 설명은 전역 Hash 키 "schema:db_descriptions"에 저장된다.
    # 키: db_id, 값: 한국어 DB 설명 문자열

    DB_DESCRIPTIONS_KEY = "schema:db_descriptions"

    async def save_db_description(
        self,
        db_id: str,
        description: str,
    ) -> bool:
        """특정 DB의 설명을 Redis에 저장한다.

        Args:
            db_id: DB 식별자
            description: DB 설명 (한국어)

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            await self._redis.hset(self.DB_DESCRIPTIONS_KEY, db_id, description)
            logger.info(
                "Redis DB 설명 저장: db_id=%s, description=%s",
                db_id,
                description[:50],
            )
            return True
        except Exception as e:
            logger.error("Redis DB 설명 저장 실패: %s", e)
            return False

    async def load_db_descriptions(self) -> dict[str, str]:
        """모든 DB 설명을 Redis에서 로드한다.

        Returns:
            {db_id: description} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            return await self._redis.hgetall(self.DB_DESCRIPTIONS_KEY)
        except Exception as e:
            logger.warning("Redis DB 설명 로드 실패: %s", e)
            return {}

    async def get_db_description(self, db_id: str) -> Optional[str]:
        """특정 DB의 설명을 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            DB 설명 문자열 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            return await self._redis.hget(self.DB_DESCRIPTIONS_KEY, db_id)
        except Exception:
            return None

    async def delete_db_description(self, db_id: str) -> bool:
        """특정 DB의 설명을 삭제한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            await self._redis.hdel(self.DB_DESCRIPTIONS_KEY, db_id)
            return True
        except Exception as e:
            logger.error("Redis DB 설명 삭제 실패: %s", e)
            return False

    # === 유사 단어 (DB별) ===

    async def save_synonyms(
        self,
        db_id: str,
        synonyms: dict[str, dict | list[str]],
        source: str = "llm",
    ) -> bool:
        """유사 단어를 Redis에 저장한다.

        synonyms 값은 두 가지 형태를 허용한다:
        - list[str]: 하위 호환용 (source 파라미터로 태깅)
        - dict {"words": [...], "sources": {...}}: source 태깅 포함

        Args:
            db_id: DB 식별자
            synonyms: {table.column: words_or_dict} 매핑
            source: 기본 source 태그 ("llm" | "operator")

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            key = self._key(db_id, "synonyms")
            mapping = {}
            for col, value in synonyms.items():
                if isinstance(value, dict) and "words" in value:
                    # 이미 source 태깅된 형태
                    mapping[col] = json.dumps(value, ensure_ascii=False)
                else:
                    # list[str] 형태 -> source 태깅 변환
                    words = value if isinstance(value, list) else list(value)
                    tagged = {
                        "words": words,
                        "sources": {w: source for w in words},
                    }
                    mapping[col] = json.dumps(tagged, ensure_ascii=False)
            if mapping:
                await self._redis.hset(key, mapping=mapping)
            logger.info(
                "Redis 유사 단어 저장: db_id=%s, columns=%d",
                db_id,
                len(synonyms),
            )
            return True
        except Exception as e:
            logger.error("Redis 유사 단어 저장 실패: %s", e)
            return False

    async def load_synonyms(self, db_id: str) -> dict[str, list[str]]:
        """Redis에서 유사 단어를 로드한다 (단어 목록만 반환).

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: [synonym1, ...]} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self._key(db_id, "synonyms"))
            result: dict[str, list[str]] = {}
            for col, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "words" in parsed:
                    result[col] = parsed["words"]
                elif isinstance(parsed, list):
                    result[col] = parsed
                else:
                    result[col] = []
            return result
        except Exception as e:
            logger.warning("Redis 유사 단어 로드 실패: %s", e)
            return {}

    async def load_synonyms_with_sources(
        self, db_id: str
    ) -> dict[str, dict]:
        """Redis에서 유사 단어를 source 태그 포함하여 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: {"words": [...], "sources": {...}}} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self._key(db_id, "synonyms"))
            result: dict[str, dict] = {}
            for col, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "words" in parsed:
                    result[col] = parsed
                elif isinstance(parsed, list):
                    # 레거시 형태 -> 변환
                    result[col] = {
                        "words": parsed,
                        "sources": {w: "llm" for w in parsed},
                    }
                else:
                    result[col] = {"words": [], "sources": {}}
            return result
        except Exception as e:
            logger.warning("Redis 유사 단어 (with sources) 로드 실패: %s", e)
            return {}

    async def add_synonyms(
        self,
        db_id: str,
        column: str,
        words: list[str],
        source: str = "llm",
    ) -> bool:
        """특정 컬럼에 유사 단어를 추가한다.

        기존 단어와 병합하며, source 태그를 함께 저장한다.
        operator source 단어는 LLM 재생성 시에도 보존된다.

        Args:
            db_id: DB 식별자
            column: "table.column" 형식
            words: 추가할 유사 단어 목록
            source: source 태그 ("llm" | "operator")

        Returns:
            성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            key = self._key(db_id, "synonyms")
            existing_raw = await self._redis.hget(key, column)

            if existing_raw:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict) and "words" in parsed:
                    existing_words = parsed["words"]
                    existing_sources = parsed.get("sources", {})
                elif isinstance(parsed, list):
                    existing_words = parsed
                    existing_sources = {w: "llm" for w in parsed}
                else:
                    existing_words = []
                    existing_sources = {}
            else:
                existing_words = []
                existing_sources = {}

            # 중복 제거하며 병합
            merged_words = list(dict.fromkeys(existing_words + words))
            # 새 단어의 source 추가 (기존 source는 보존)
            for w in words:
                if w not in existing_sources:
                    existing_sources[w] = source

            tagged = {
                "words": merged_words,
                "sources": existing_sources,
            }
            await self._redis.hset(
                key, column, json.dumps(tagged, ensure_ascii=False)
            )
            return True
        except Exception as e:
            logger.error("Redis 유사 단어 추가 실패: %s", e)
            return False

    async def remove_synonyms(
        self,
        db_id: str,
        column: str,
        words: list[str],
    ) -> bool:
        """특정 컬럼에서 유사 단어를 삭제한다.

        Args:
            db_id: DB 식별자
            column: "table.column" 형식
            words: 삭제할 유사 단어 목록

        Returns:
            성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            key = self._key(db_id, "synonyms")
            existing_raw = await self._redis.hget(key, column)
            if not existing_raw:
                return True

            parsed = json.loads(existing_raw)
            if isinstance(parsed, dict) and "words" in parsed:
                existing_words = parsed["words"]
                existing_sources = parsed.get("sources", {})
            elif isinstance(parsed, list):
                existing_words = parsed
                existing_sources = {}
            else:
                return True

            words_set = set(words)
            updated_words = [w for w in existing_words if w not in words_set]
            updated_sources = {
                k: v for k, v in existing_sources.items() if k not in words_set
            }

            if updated_words:
                tagged = {"words": updated_words, "sources": updated_sources}
                await self._redis.hset(
                    key, column, json.dumps(tagged, ensure_ascii=False)
                )
            else:
                await self._redis.hdel(key, column)
            return True
        except Exception as e:
            logger.error("Redis 유사 단어 삭제 실패: %s", e)
            return False

    # === 글로벌 유사단어 사전 ===

    GLOBAL_SYNONYMS_KEY = "synonyms:global"
    RESOURCE_TYPE_SYNONYMS_KEY = "synonyms:resource_types"
    EAV_NAME_SYNONYMS_KEY = "synonyms:eav_names"

    async def save_global_synonyms(
        self,
        synonyms: dict[str, list[str] | dict],
    ) -> bool:
        """글로벌 유사단어 사전을 Redis에 저장한다.

        컬럼명(bare name, 테이블 무관)을 키로 하는 범용 사전이다.
        새로운 DB에 동일 컬럼명이 있으면 자동으로 이 사전에서 유사 단어를 로드한다.

        값은 두 가지 형태를 허용한다:
        - list[str]: 하위 호환용 (words만 저장)
        - dict {"words": [...], "description": "..."}: description 포함

        Args:
            synonyms: {column_name: words_or_dict} 매핑

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            mapping = {}
            for col, value in synonyms.items():
                if isinstance(value, dict) and "words" in value:
                    # dict 형태 -> 그대로 저장
                    mapping[col] = json.dumps(value, ensure_ascii=False)
                else:
                    # list 형태 -> 하위 호환: words만 있는 dict로 변환
                    words = value if isinstance(value, list) else list(value)
                    mapping[col] = json.dumps(
                        {"words": words}, ensure_ascii=False
                    )
            if mapping:
                await self._redis.hset(self.GLOBAL_SYNONYMS_KEY, mapping=mapping)
            logger.info("Redis 글로벌 유사단어 저장: columns=%d", len(synonyms))
            return True
        except Exception as e:
            logger.error("Redis 글로벌 유사단어 저장 실패: %s", e)
            return False

    async def load_global_synonyms(self) -> dict[str, list[str]]:
        """글로벌 유사단어 사전을 로드한다 (단어 목록만 반환).

        하위 호환: dict 형태와 list 형태 모두 처리한다.

        Returns:
            {column_name: [synonym1, ...]} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self.GLOBAL_SYNONYMS_KEY)
            result: dict[str, list[str]] = {}
            for col, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "words" in parsed:
                    result[col] = parsed["words"]
                elif isinstance(parsed, list):
                    result[col] = parsed
                else:
                    result[col] = []
            return result
        except Exception as e:
            logger.warning("Redis 글로벌 유사단어 로드 실패: %s", e)
            return {}

    async def load_global_synonyms_full(self) -> dict[str, dict]:
        """글로벌 유사단어 사전을 description 포함하여 로드한다.

        Returns:
            {column_name: {"words": [...], "description": "..."}} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self.GLOBAL_SYNONYMS_KEY)
            result: dict[str, dict] = {}
            for col, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, dict) and "words" in parsed:
                    result[col] = parsed
                elif isinstance(parsed, list):
                    # 레거시 list 형태 -> dict 변환
                    result[col] = {"words": parsed}
                else:
                    result[col] = {"words": []}
            return result
        except Exception as e:
            logger.warning("Redis 글로벌 유사단어 (full) 로드 실패: %s", e)
            return {}

    async def add_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """글로벌 유사단어 사전에 단어를 추가한다.

        기존 description은 보존한다.

        Args:
            column_name: 컬럼명 (bare name, 테이블 무관)
            words: 추가할 유사 단어 목록

        Returns:
            성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            existing_raw = await self._redis.hget(
                self.GLOBAL_SYNONYMS_KEY, column_name
            )
            if existing_raw:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict) and "words" in parsed:
                    existing_words = parsed["words"]
                    description = parsed.get("description", "")
                elif isinstance(parsed, list):
                    existing_words = parsed
                    description = ""
                else:
                    existing_words = []
                    description = ""
            else:
                existing_words = []
                description = ""

            merged = list(dict.fromkeys(existing_words + words))
            entry: dict = {"words": merged}
            if description:
                entry["description"] = description
            await self._redis.hset(
                self.GLOBAL_SYNONYMS_KEY,
                column_name,
                json.dumps(entry, ensure_ascii=False),
            )
            return True
        except Exception as e:
            logger.error("Redis 글로벌 유사단어 추가 실패: %s", e)
            return False

    async def update_global_description(
        self,
        column_name: str,
        description: str,
    ) -> bool:
        """글로벌 사전의 컬럼 설명을 수정한다.

        기존 words는 보존하고 description만 업데이트한다.
        항목이 없으면 description만 가진 새 항목을 생성한다.

        Args:
            column_name: 컬럼명 (bare name)
            description: 새 설명 텍스트

        Returns:
            성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            existing_raw = await self._redis.hget(
                self.GLOBAL_SYNONYMS_KEY, column_name
            )
            if existing_raw:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict) and "words" in parsed:
                    entry = parsed
                elif isinstance(parsed, list):
                    entry = {"words": parsed}
                else:
                    entry = {"words": []}
            else:
                entry = {"words": []}

            entry["description"] = description
            await self._redis.hset(
                self.GLOBAL_SYNONYMS_KEY,
                column_name,
                json.dumps(entry, ensure_ascii=False),
            )
            logger.info(
                "Redis 글로벌 컬럼 설명 업데이트: column=%s, description=%s",
                column_name,
                description[:50],
            )
            return True
        except Exception as e:
            logger.error("Redis 글로벌 컬럼 설명 업데이트 실패: %s", e)
            return False

    async def get_global_description(
        self,
        column_name: str,
    ) -> Optional[str]:
        """글로벌 사전에서 컬럼 설명을 조회한다.

        Args:
            column_name: 컬럼명 (bare name)

        Returns:
            설명 문자열 또는 None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            raw = await self._redis.hget(
                self.GLOBAL_SYNONYMS_KEY, column_name
            )
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get("description")
            return None
        except Exception:
            return None

    async def list_global_column_names(self) -> list[str]:
        """글로벌 사전에 등록된 전체 컬럼명 목록을 반환한다.

        Returns:
            컬럼명 목록 (정렬)
        """
        if not self._connected or self._redis is None:
            return []

        try:
            keys = await self._redis.hkeys(self.GLOBAL_SYNONYMS_KEY)
            return sorted(keys)
        except Exception:
            return []

    async def remove_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """글로벌 유사단어 사전에서 단어를 삭제한다.

        description은 보존한다.

        Args:
            column_name: 컬럼명
            words: 삭제할 유사 단어 목록

        Returns:
            성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            existing_raw = await self._redis.hget(
                self.GLOBAL_SYNONYMS_KEY, column_name
            )
            if not existing_raw:
                return True

            parsed = json.loads(existing_raw)
            if isinstance(parsed, dict) and "words" in parsed:
                existing_words = parsed["words"]
                description = parsed.get("description", "")
            elif isinstance(parsed, list):
                existing_words = parsed
                description = ""
            else:
                return True

            words_set = set(words)
            updated = [w for w in existing_words if w not in words_set]

            if updated or description:
                entry: dict = {"words": updated}
                if description:
                    entry["description"] = description
                await self._redis.hset(
                    self.GLOBAL_SYNONYMS_KEY,
                    column_name,
                    json.dumps(entry, ensure_ascii=False),
                )
            else:
                await self._redis.hdel(self.GLOBAL_SYNONYMS_KEY, column_name)
            return True
        except Exception as e:
            logger.error("Redis 글로벌 유사단어 삭제 실패: %s", e)
            return False

    # === CSV 캐시 ===

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

    # === 관리 ===

    async def invalidate(self, db_id: str) -> bool:
        """특정 DB의 캐시를 삭제한다.

        DB별 데이터를 전체 삭제하며, 글로벌 사전(synonyms:global 등)만 보존한다.
        글로벌 사전을 삭제하려면 delete_global_synonyms()를 사용한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            # 글로벌 사전(synonyms:global 등)만 보존, DB별 데이터는 전체 삭제
            keys = [
                self._key(db_id, suffix)
                for suffix in (
                    "meta",
                    "tables",
                    "relationships",
                    "descriptions",
                    "synonyms",
                    "fingerprint_checked_at",
                    "structure_meta",
                )
            ]
            await self._redis.delete(*keys)
            logger.info(
                "Redis 캐시 삭제 (글로벌 사전만 보존): db_id=%s", db_id
            )
            return True
        except Exception as e:
            logger.error("Redis 캐시 삭제 실패: %s", e)
            return False

    async def invalidate_all(self) -> int:
        """모든 스키마 캐시를 삭제한다.

        글로벌 사전만 보존한다:
        - synonyms:global (GLOBAL_SYNONYMS_KEY)
        - synonyms:resource_types (RESOURCE_TYPE_SYNONYMS_KEY)
        - synonyms:eav_names (EAV_NAME_SYNONYMS_KEY)
        - schema:db_descriptions (DB_DESCRIPTIONS_KEY)

        DB별 synonyms도 삭제 대상에 포함된다.

        Returns:
            삭제된 키 수
        """
        if not self._connected or self._redis is None:
            return 0

        try:
            # 보존 대상 글로벌 키
            preserved_keys = {
                self.GLOBAL_SYNONYMS_KEY,
                self.RESOURCE_TYPE_SYNONYMS_KEY,
                self.EAV_NAME_SYNONYMS_KEY,
                self.DB_DESCRIPTIONS_KEY,
            }
            count = 0
            async for key in self._redis.scan_iter(match="schema:*"):
                if key in preserved_keys:
                    continue
                await self._redis.delete(key)
                count += 1
            # synonyms:global 등은 schema: 접두사가 아니므로 스캔에 잡히지 않음
            logger.info(
                "Redis 전체 캐시 삭제 (글로벌 사전만 보존): %d keys", count
            )
            return count
        except Exception as e:
            logger.error("Redis 전체 캐시 삭제 실패: %s", e)
            return 0

    async def delete_synonyms(self, db_id: str) -> bool:
        """특정 DB의 유사단어를 명시적으로 삭제한다.

        운영자가 명시적으로 삭제 명령을 실행한 경우에만 사용.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            await self._redis.delete(self._key(db_id, "synonyms"))
            logger.info("Redis 유사단어 명시 삭제: db_id=%s", db_id)
            return True
        except Exception as e:
            logger.error("Redis 유사단어 삭제 실패: %s", e)
            return False

    async def delete_global_synonyms(self) -> bool:
        """글로벌 유사단어 사전을 명시적으로 삭제한다.

        운영자가 명시적으로 삭제 명령을 실행한 경우에만 사용.

        Returns:
            삭제 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            await self._redis.delete(self.GLOBAL_SYNONYMS_KEY)
            logger.info("Redis 글로벌 유사단어 명시 삭제")
            return True
        except Exception as e:
            logger.error("Redis 글로벌 유사단어 삭제 실패: %s", e)
            return False

    # === RESOURCE_TYPE / EAV NAME 유사단어 ===

    async def save_resource_type_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """RESOURCE_TYPE 값의 유사단어를 Redis에 저장한다.

        Redis Hash synonyms:resource_types에 field=값, value=JSON array로 저장한다.

        Args:
            synonyms: {resource_type_value: [유사단어, ...]} 매핑
                      예: {"server.Cpu": ["CPU", "씨피유", ...]}

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            mapping = {}
            for rt_value, words in synonyms.items():
                mapping[rt_value] = json.dumps(words, ensure_ascii=False)
            if mapping:
                await self._redis.hset(
                    self.RESOURCE_TYPE_SYNONYMS_KEY, mapping=mapping
                )
            logger.info(
                "Redis RESOURCE_TYPE 유사단어 저장: count=%d", len(synonyms)
            )
            return True
        except Exception as e:
            logger.error("Redis RESOURCE_TYPE 유사단어 저장 실패: %s", e)
            return False

    async def load_resource_type_synonyms(self) -> dict[str, list[str]]:
        """RESOURCE_TYPE 값의 유사단어를 Redis에서 로드한다.

        Returns:
            {resource_type_value: [유사단어, ...]} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self.RESOURCE_TYPE_SYNONYMS_KEY)
            result: dict[str, list[str]] = {}
            for rt_value, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, list):
                    result[rt_value] = parsed
                else:
                    result[rt_value] = []
            return result
        except Exception as e:
            logger.warning("Redis RESOURCE_TYPE 유사단어 로드 실패: %s", e)
            return {}

    async def save_eav_name_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """EAV NAME 값의 유사단어를 Redis에 저장한다.

        Redis Hash synonyms:eav_names에 field=이름, value=JSON array로 저장한다.

        Args:
            synonyms: {eav_name: [유사단어, ...]} 매핑
                      예: {"OSType": ["운영체제", "OS 종류", ...]}

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            mapping = {}
            for eav_name, words in synonyms.items():
                mapping[eav_name] = json.dumps(words, ensure_ascii=False)
            if mapping:
                await self._redis.hset(
                    self.EAV_NAME_SYNONYMS_KEY, mapping=mapping
                )
            logger.info(
                "Redis EAV NAME 유사단어 저장: count=%d", len(synonyms)
            )
            return True
        except Exception as e:
            logger.error("Redis EAV NAME 유사단어 저장 실패: %s", e)
            return False

    async def load_eav_name_synonyms(self) -> dict[str, list[str]]:
        """EAV NAME 값의 유사단어를 Redis에서 로드한다.

        Returns:
            {eav_name: [유사단어, ...]} 매핑
        """
        if not self._connected or self._redis is None:
            return {}

        try:
            raw = await self._redis.hgetall(self.EAV_NAME_SYNONYMS_KEY)
            result: dict[str, list[str]] = {}
            for eav_name, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, list):
                    result[eav_name] = parsed
                else:
                    result[eav_name] = []
            return result
        except Exception as e:
            logger.warning("Redis EAV NAME 유사단어 로드 실패: %s", e)
            return {}

    async def sync_known_attributes_to_eav_synonyms(
        self,
        known_attributes_detail: list[dict],
    ) -> int:
        """수동 프로필의 known_attributes를 Redis eav_name_synonyms에 동기화한다.

        기존 Redis에 저장된 synonyms는 보존하고, 프로필의 synonyms를 추가만 한다.
        description 필드는 Redis에 저장하지 않는다 (eav_name_synonyms는
        {name: [words]} 형식만 지원).

        Args:
            known_attributes_detail: [{name: str, description: str, synonyms: [str]}, ...]

        Returns:
            동기화된 속성 수
        """
        if not self._connected or self._redis is None:
            return 0

        if not known_attributes_detail:
            return 0

        try:
            existing = await self.load_eav_name_synonyms()

            synced_count = 0
            for attr in known_attributes_detail:
                attr_name: str = attr.get("name", "")
                attr_synonyms: list[str] = attr.get("synonyms", [])
                if not attr_name or not attr_synonyms:
                    continue

                current = existing.get(attr_name, [])
                merged = list(dict.fromkeys(current + attr_synonyms))
                existing[attr_name] = merged
                synced_count += 1

            if synced_count > 0:
                await self.save_eav_name_synonyms(existing)
                logger.info(
                    "known_attributes → eav_name_synonyms 동기화 완료: %d개 속성",
                    synced_count,
                )

            return synced_count
        except Exception as e:
            logger.error(
                "known_attributes → eav_name_synonyms 동기화 실패: %s", e
            )
            return 0

    async def list_cached_dbs(self) -> list[dict]:
        """캐시된 DB 목록과 메타정보를 반환한다.

        Returns:
            캐시 정보 목록
        """
        if not self._connected or self._redis is None:
            return []

        try:
            result: list[dict] = []
            seen_db_ids: set[str] = set()
            async for key in self._redis.scan_iter(match="schema:*:meta"):
                # key 형식: schema:{db_id}:meta
                parts = key.split(":")
                if len(parts) == 3:
                    db_id = parts[1]
                    if db_id in seen_db_ids:
                        continue
                    seen_db_ids.add(db_id)
                    meta = await self._redis.hgetall(key)
                    result.append({
                        "db_id": db_id,
                        "fingerprint": meta.get("fingerprint", ""),
                        "cached_at": meta.get("cached_at", ""),
                        "cache_version": int(meta.get("cache_version", "0")),
                        "table_count": int(meta.get("table_count", "0")),
                        "total_column_count": int(
                            meta.get("total_column_count", "0")
                        ),
                        "description_status": meta.get(
                            "description_status", "pending"
                        ),
                    })
            return sorted(result, key=lambda x: x["db_id"])
        except Exception as e:
            logger.error("Redis 캐시 목록 조회 실패: %s", e)
            return []

    async def get_status(self, db_id: str) -> dict:
        """특정 DB의 캐시 상태를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            캐시 상태 딕셔너리
        """
        if not self._connected or self._redis is None:
            return {"exists": False, "error": "Redis 연결 없음"}

        try:
            meta = await self._redis.hgetall(self._key(db_id, "meta"))
            if not meta:
                return {"exists": False}

            desc_count = await self._redis.hlen(self._key(db_id, "descriptions"))
            syn_count = await self._redis.hlen(self._key(db_id, "synonyms"))

            return {
                "exists": True,
                "fingerprint": meta.get("fingerprint", ""),
                "cached_at": meta.get("cached_at", ""),
                "table_count": int(meta.get("table_count", "0")),
                "total_column_count": int(meta.get("total_column_count", "0")),
                "description_status": meta.get("description_status", "pending"),
                "description_count": desc_count,
                "synonym_count": syn_count,
            }
        except Exception as e:
            logger.error("Redis 캐시 상태 조회 실패: %s", e)
            return {"exists": False, "error": str(e)}
