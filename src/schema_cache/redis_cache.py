"""Redis 기반 스키마 캐시.

DB별 스키마 정보, 컬럼 설명(description), 유사 단어(synonym)를
Redis에 영구 저장하며, fingerprint 기반 변경 감지를 수행한다.

키 네이밍:
  schema:db_descriptions        -> Hash (db_id -> DB 설명, 한국어)
  schema:db_description_origin  -> Hash (db_id -> "manual" | "llm", plans/104 S5)
  schema:{db_id}:meta          -> Hash (fingerprint, cached_at, ...)
  schema:{db_id}:tables        -> Hash (table_name -> JSON)
  schema:{db_id}:relationships -> String (JSON array)
  schema:{db_id}:descriptions  -> Hash (table.column -> description)
  schema:{db_id}:synonyms      -> Hash (table.column -> JSON array)

관리자 자산 키(plans/104 — JSON String, `StructureStore`가 쓴다)는
`ADMIN_ASSET_SUFFIXES` 참조. 이 키들은 `invalidate_all()`이 지우지 않는다.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from src.schema_cache.redis_synonyms import RedisSynonymStore

logger = logging.getLogger(__name__)

# 캐시 포맷 버전 (Redis 구조 변경 시 증가)
CACHE_FORMAT_VERSION = 1

# 관리자가 만든 자산 키 접미사(plans/104 §3.3) — 런타임 캐시가 아니므로 전체 무효화에서 보존한다.
# 적용본(`structure_meta`)은 여기에 없다: 버전 목록·파일 백업에서 복원된다.
ADMIN_ASSET_SUFFIXES = frozenset({
    "structure_versions",
    "structure_drafts",
    "schema_snapshot",
    "check_result",
    "registration",
    "description_drafts",
    "asset_drafts",  # D-294 자산 자동 생성 초안
    "ddl_comments",  # D-294 ⑨ DDL 등록 때 읽은 주석(DDL을 다시 등록해야만 되살아난다)
})


def schema_key(db_id: str, suffix: str) -> str:
    """DB별 Redis 키 ``schema:{db_id}:{suffix}``를 만든다(키 규칙 단일 출처 — plans/170 I1-d)."""
    return f"schema:{db_id}:{suffix}"


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
        # 유사어 키 공간 하위 저장소(plans/170 I1-c) — 소유자 상태는 호출 시점에 읽는다.
        self._synonyms = RedisSynonymStore(self)

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
                logger.warning("Redis 연결 종료 실패", exc_info=True)
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
        except Exception as e:
            # 유휴 끊김 감지는 정상 재연결 경로라 스택 없이 한 줄만 남긴다.
            logger.warning("Redis health check 실패 — 연결 끊김으로 표시: %s", type(e).__name__)
            self._connected = False
            return False

    @property
    def connected(self) -> bool:
        """마지막으로 확인된 연결 상태(네트워크 확인 없음)."""
        return self._connected and self._redis is not None

    async def ensure_connected(self) -> bool:
        """연결을 확인하고 끊겨 있으면 다시 연결한다. 실패는 False(예외를 던지지 않는다)."""
        try:
            if not await self.health_check():
                await self.connect()
            return True
        except Exception:
            return False

    # === 범용 JSON 헬퍼 (plans/104 관리자 자산·잡 상태) ===

    async def get_json(self, key: str) -> Any:
        """JSON 문자열 키를 읽어 파싱한다.

        미연결·키 없음·파싱 실패(WARNING 로그)는 None. Redis 명령 오류는 호출자에게 전파한다.
        """
        if not self._connected or self._redis is None:
            return None
        raw = await self._redis.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError) as e:
            logger.warning("Redis JSON 파싱 실패 (key=%s): %s", key, e)
            return None

    async def set_json(
        self,
        key: str,
        value: Any,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        """값을 JSON 문자열로 저장한다.

        Args:
            key: Redis 키
            value: JSON 직렬화 가능한 값
            ex: 만료 초(None이면 만료 없음)
            nx: True면 키가 없을 때만 저장(락 획득용)

        Returns:
            저장했으면 True. 미연결이거나 `nx`로 저장하지 않았으면 False.
            직렬화 오류·Redis 명령 오류는 호출자에게 전파한다.
        """
        if not self._connected or self._redis is None:
            return False
        payload = json.dumps(value, ensure_ascii=False)
        result = await self._redis.set(key, payload, ex=ex, nx=nx)
        return bool(result)

    async def delete_keys(self, *keys: str) -> int:
        """키를 삭제하고 삭제된 수를 반환한다. 미연결이면 0. Redis 명령 오류는 전파한다."""
        if not keys or not self._connected or self._redis is None:
            return 0
        return int(await self._redis.delete(*keys) or 0)

    def _key(self, db_id: str, suffix: str) -> str:
        """Redis 키를 생성한다."""
        return schema_key(db_id, suffix)

    @staticmethod
    def _db_id_from_key(key: str) -> str:
        """``schema:{db_id}:{suffix}`` 형식 키에서 db_id를 추출한다."""
        parts = key.split(":")
        return parts[1] if len(parts) >= 3 else key

    def _governance_enabled(self) -> bool:
        """E5-3 사전 위생·거버넌스 마스터 스위치 상태를 반환한다 (D-075).

        기본 OFF. OFF일 때 유사어 메타(사용횟수·최종사용일·신뢰도) 저장/증가·정리
        로직은 완전 no-op이며, 기존 저장 구조가 바이트 단위로 무변경이어야 한다.
        접근 경로: ``load_config().synonym.governance``.
        """
        try:
            from src.config import load_config

            return bool(load_config().synonym.governance)
        except Exception as e:
            # 설정 검증 오류 본문에 원값이 실릴 수 있어 예외 종류만 남긴다.
            logger.warning("유사어 거버넌스 설정 조회 실패 — OFF로 간주: %s", type(e).__name__)
            return False

    def _ensure_meta(self, entry: dict, now: float) -> None:
        """유사어 거버넌스 메타 — ``RedisSynonymStore``에 위임한다."""
        self._synonyms._ensure_meta(entry, now)

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

    async def add_missing_descriptions(
        self,
        db_id: str,
        descriptions: dict[str, str],
    ) -> list[str]:
        """Redis에 없는 컬럼 설명만 추가한다(HSETNX — 기존 설명은 덮지 않는다 · D-316 ④).

        한 번 적재한 키는 DB별 집합(`schema:{db_id}:knowledge_seeded`)에 남기고 다시 채우지 않는다 —
        운영자가 지운 설명이 재기동 때 되살아나지 않게. 해시에 없고 집합에도 없는 키만 채운다.
        주의: 정본 파일 내용이 바뀌어도 이미 적재한 키는 다시 채우지 않는다(캐시 삭제
        `invalidate(db_id)`·`invalidate_all`은 설명과 함께 집합도 지운다).
        `meta.description_status`는 건드리지 않는다(관리자 등록 상태가 아니다).

        Args:
            db_id: DB 식별자
            descriptions: {table.column: description} 매핑

        Returns:
            실제로 추가한 키 목록(미연결·실패면 빈 목록)
        """
        if not self._connected or self._redis is None or not descriptions:
            return []

        key = self._key(db_id, "descriptions")
        seeded_key = self._key(db_id, "knowledge_seeded")
        added: list[str] = []
        try:
            seeded = await self._redis.smembers(seeded_key)
            for field_name, text in descriptions.items():
                if field_name in seeded:
                    continue
                if await self._redis.hsetnx(key, field_name, text):
                    added.append(field_name)
                    await self._redis.sadd(seeded_key, field_name)
        except Exception as e:
            logger.warning("Redis 컬럼 설명 추가 실패 (db_id=%s): %s", db_id, e)
        return added

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
            logger.warning("Redis 컬럼 설명 조회 실패 (db_id=%s)", db_id, exc_info=True)
            return None

    # === DB 설명 ===

    # DB 설명은 전역 Hash 키 "schema:db_descriptions"에 저장된다.
    # 키: db_id, 값: 한국어 DB 설명 문자열

    DB_DESCRIPTIONS_KEY = "schema:db_descriptions"
    # DB 설명의 출처(plans/104 S5) — field=db_id, value="manual" | "llm"
    DB_DESCRIPTION_ORIGIN_KEY = "schema:db_description_origin"

    async def save_db_description(
        self,
        db_id: str,
        description: str,
        origin: str = "manual",
    ) -> bool:
        """특정 DB의 설명을 출처와 함께 Redis에 저장한다.

        설명과 출처는 한 트랜잭션(pipeline)으로 기록한다. 출처 보존 판정(LLM이 수동
        설명을 덮지 않음)은 호출자인 `SchemaCacheManager.save_db_description`이 한다.

        Args:
            db_id: DB 식별자
            description: DB 설명 (한국어)
            origin: 설명 출처 ("manual" | "llm")

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            pipe = self._redis.pipeline()
            pipe.hset(self.DB_DESCRIPTIONS_KEY, db_id, description)
            pipe.hset(self.DB_DESCRIPTION_ORIGIN_KEY, db_id, origin)
            await pipe.execute()
            logger.info(
                "Redis DB 설명 저장: db_id=%s, origin=%s, description=%s",
                db_id,
                origin,
                description[:50],
            )
            return True
        except Exception as e:
            logger.error("Redis DB 설명 저장 실패: %s", e)
            return False

    async def get_db_description_origin(self, db_id: str) -> str | None:
        """특정 DB 설명의 출처를 반환한다(기록 없음·미연결이면 None).

        Args:
            db_id: DB 식별자

        Returns:
            "manual" | "llm" | None
        """
        if not self._connected or self._redis is None:
            return None

        try:
            origin = await self._redis.hget(self.DB_DESCRIPTION_ORIGIN_KEY, db_id)
            return origin if isinstance(origin, str) else None
        except Exception as e:
            logger.warning("Redis DB 설명 출처 조회 실패 (db_id=%s): %s", db_id, e)
            return None

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
            logger.warning("Redis DB 설명 조회 실패 (db_id=%s)", db_id, exc_info=True)
            return None

    async def delete_db_description(self, db_id: str) -> bool:
        """특정 DB의 설명과 그 출처 기록을 삭제한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if not self._connected or self._redis is None:
            return False

        try:
            pipe = self._redis.pipeline()
            pipe.hdel(self.DB_DESCRIPTIONS_KEY, db_id)
            pipe.hdel(self.DB_DESCRIPTION_ORIGIN_KEY, db_id)
            await pipe.execute()
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
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.save_synonyms(db_id, synonyms, source)

    @staticmethod
    def _merge_llm_words(
        existing_raw: Any, words: list[str], *, keep_meta: bool
    ) -> dict[str, Any]:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return RedisSynonymStore._merge_llm_words(existing_raw, words, keep_meta=keep_meta)

    async def load_synonyms(self, db_id: str) -> dict[str, list[str]]:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_synonyms(db_id)

    async def load_synonyms_with_sources(
        self, db_id: str
    ) -> dict[str, dict]:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_synonyms_with_sources(db_id)

    async def add_synonyms(
        self,
        db_id: str,
        column: str,
        words: list[str],
        source: str = "llm",
    ) -> bool:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.add_synonyms(db_id, column, words, source)

    async def remove_synonyms(
        self,
        db_id: str,
        column: str,
        words: list[str],
    ) -> bool:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.remove_synonyms(db_id, column, words)

    # === 유사어 메타데이터 (E5-3 사전 위생·거버넌스 / D-075) ===

    async def load_synonym_meta(
        self, db_id: str
    ) -> dict[str, dict[str, dict]]:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_synonym_meta(db_id)

    async def increment_synonym_usage(
        self,
        db_id: str,
        column: str,
        words: list[str],
        *,
        now: float | None = None,
        confidence: float | None = None,
    ) -> bool:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.increment_synonym_usage(
            db_id, column, words, now=now, confidence=confidence
        )

    async def prune_stale_synonyms(
        self,
        *,
        decay_days: int,
        now: float | None = None,
        db_id: str | None = None,
    ) -> dict:
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.prune_stale_synonyms(
            decay_days=decay_days, now=now, db_id=db_id
        )

    # === 글로벌 유사단어 사전 ===

    GLOBAL_SYNONYMS_KEY = "synonyms:global"
    RESOURCE_TYPE_SYNONYMS_KEY = "synonyms:resource_types"
    EAV_NAME_SYNONYMS_KEY = "synonyms:eav_names"
    COLUMN_VALUE_SYNONYMS_KEY = "synonyms:column_values"

    async def save_global_synonyms(
        self,
        synonyms: dict[str, list[str] | dict],
    ) -> bool:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.save_global_synonyms(synonyms)

    async def load_global_synonyms(self) -> dict[str, list[str]]:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_global_synonyms()

    async def load_global_synonyms_full(self) -> dict[str, dict]:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_global_synonyms_full()

    async def add_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.add_global_synonym(column_name, words)

    async def update_global_description(
        self,
        column_name: str,
        description: str,
    ) -> bool:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.update_global_description(column_name, description)

    async def get_global_description(
        self,
        column_name: str,
    ) -> Optional[str]:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.get_global_description(column_name)

    async def list_global_column_names(self) -> list[str]:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.list_global_column_names()

    async def remove_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.remove_global_synonym(column_name, words)

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

        DB별 런타임 캐시를 전체 삭제하며, 글로벌 사전(synonyms:global 등)은 보존한다.
        글로벌 사전을 삭제하려면 delete_global_synonyms()를 사용한다.
        관리자 자산 키(`ADMIN_ASSET_SUFFIXES`)는 삭제 목록에 없다 — 적용본 `structure_meta`는
        지워지지만 버전 목록·파일 백업에서 복원된다(plans/104).
        설명 적재 기록 집합(`knowledge_seeded`)은 `descriptions`와 함께 지운다 — 남기면 지운
        설명이 정본 파일에서 다시 채워지지 않는다(D-316 ④).

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
                    "knowledge_seeded",
                    "synonyms",
                    "fingerprint_checked_at",
                    "structure_meta",
                    "column_value_index",
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
        - schema:db_description_origin (DB_DESCRIPTION_ORIGIN_KEY)

        관리자 자산 키 `schema:{db_id}:{ADMIN_ASSET_SUFFIXES}`(버전·초안·스냅샷·점검 결과·
        등록 상태 — plans/104 · 자산 초안·DDL 주석 — D-294)도 보존한다. DB별 synonyms는 삭제
        대상에 포함된다.

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
                self.DB_DESCRIPTION_ORIGIN_KEY,
                self.COLUMN_VALUE_SYNONYMS_KEY,
            }
            count = 0
            async for key in self._redis.scan_iter(match="schema:*"):
                if key in preserved_keys:
                    continue
                parts = key.split(":")
                if len(parts) == 3 and parts[2] in ADMIN_ASSET_SUFFIXES:
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
        """``schema:{db_id}:synonyms`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.delete_synonyms(db_id)

    async def delete_global_synonyms(self) -> bool:
        """``GLOBAL_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.delete_global_synonyms()

    # === RESOURCE_TYPE / EAV NAME 유사단어 ===

    async def save_resource_type_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """``RESOURCE_TYPE_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.save_resource_type_synonyms(synonyms)

    async def load_resource_type_synonyms(self) -> dict[str, list[str]]:
        """``RESOURCE_TYPE_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_resource_type_synonyms()

    async def save_eav_name_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """``EAV_NAME_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.save_eav_name_synonyms(synonyms)

    async def load_eav_name_synonyms(self) -> dict[str, list[str]]:
        """``EAV_NAME_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_eav_name_synonyms()

    async def save_column_value_synonyms(
        self,
        synonyms: dict[str, dict[str, dict]],
    ) -> bool:
        """``COLUMN_VALUE_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.save_column_value_synonyms(synonyms)

    async def load_column_value_synonyms(self) -> dict[str, dict[str, dict]]:
        """``COLUMN_VALUE_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.load_column_value_synonyms()

    # === 값 검색 인덱스 (Plan 61 트랙 B / E5-2) ===
    # distinct resource_type·EAV NAME 등 안정·유한 값집합을 실측 인덱싱해 캐시한다.
    # 기존 ``synonyms:column_values``(연산자-값 맵 — AVAIL_STATUS 등)와 **의미가 달라**
    # 그 키를 덮어쓰지 않고 DB별 전용 키를 신설한다(회귀 0).
    # 유사어 Hash는 원래 TTL이 없으나, 값 인덱스는 CSV 캐시의 ``ex=`` 패턴 TTL을 신규
    # 적용하고 fingerprint freshness(D-019)로 갱신 시점을 판단한다. 갱신은 읽기전용 SELECT
    # DISTINCT로만 수행하므로 D-003(3중 읽기전용 방어)을 준수한다.

    COLUMN_VALUE_INDEX_TTL = 86400  # 1일 (안정·유한 값집합)

    async def save_column_value_index(
        self,
        db_id: str,
        index: dict[str, list[str]],
        ttl: int | None = None,
    ) -> bool:
        """DB별 컬럼 값 검색 인덱스를 TTL과 함께 Redis에 저장한다(E5-2).

        Args:
            db_id: DB 식별자
            index: {인덱스 키: [distinct 값, ...]} 예: {"resource_type": [...], "eav_name": [...]}
            ttl: 만료 초. None이면 COLUMN_VALUE_INDEX_TTL(1일).

        Returns:
            저장 성공 여부
        """
        if not self._connected or self._redis is None:
            return False
        try:
            key = self._key(db_id, "column_value_index")
            await self._redis.set(
                key,
                json.dumps(index, ensure_ascii=False),
                ex=ttl if ttl is not None else self.COLUMN_VALUE_INDEX_TTL,
            )
            logger.info(
                "값 검색 인덱스 저장: db_id=%s, keys=%d", db_id, len(index)
            )
            return True
        except Exception as e:
            logger.warning("값 검색 인덱스 저장 실패: %s", e)
            return False

    async def load_column_value_index(
        self, db_id: str
    ) -> Optional[dict[str, list[str]]]:
        """DB별 컬럼 값 검색 인덱스를 Redis에서 로드한다(E5-2).

        Args:
            db_id: DB 식별자

        Returns:
            {인덱스 키: [distinct 값, ...]} 또는 None(미스/만료 시)
        """
        if not self._connected or self._redis is None:
            return None
        try:
            key = self._key(db_id, "column_value_index")
            raw = await self._redis.get(key)
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
            return None
        except Exception as e:
            logger.warning("값 검색 인덱스 로드 실패: %s", e)
            return None

    async def sync_known_attributes_to_eav_synonyms(
        self,
        known_attributes_detail: list[dict],
    ) -> int:
        """``EAV_NAME_SYNONYMS_KEY`` — ``RedisSynonymStore``에 위임한다."""
        return await self._synonyms.sync_known_attributes_to_eav_synonyms(known_attributes_detail)

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

    # === 폼필 확인 이력 (Plan 73 Phase 3, D-151) ===
    # 키: formfill:memory:{signature} — JSON 문자열 + TTL(sliding).
    # 유사어와 키 공간 분리(양식 시그니처 스코프) — 전역 질의에 영향 없음.

    def _form_memory_key(self, signature: str) -> str:
        return f"formfill:memory:{signature}"

    async def load_form_memory(self, signature: str) -> Optional[dict]:
        """양식 시그니처의 확인 이력을 반환한다 (없으면 None)."""
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(self._form_memory_key(signature))
            if not raw:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("폼필 확인 이력 조회 실패(%s): %s", signature, e)
            return None

    async def save_form_memory(
        self, signature: str, data: dict, ttl_seconds: int
    ) -> bool:
        """확인 이력을 저장한다 (TTL 필수 — 무기한 저장 금지, Plan 73 §2.4 개정)."""
        if self._redis is None or ttl_seconds <= 0:
            return False
        try:
            await self._redis.set(
                self._form_memory_key(signature),
                json.dumps(data, ensure_ascii=False),
                ex=ttl_seconds,
            )
            return True
        except Exception as e:
            logger.warning("폼필 확인 이력 저장 실패(%s): %s", signature, e)
            return False

    async def delete_form_memory(self, signature: str) -> bool:
        """확인 이력 전체를 삭제한다."""
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.delete(self._form_memory_key(signature)))
        except Exception as e:
            logger.warning("폼필 확인 이력 삭제 실패(%s): %s", signature, e)
            return False

    async def touch_form_memory(self, signature: str, ttl_seconds: int) -> None:
        """sliding TTL 갱신 — 적용(사용)이 곧 재확인이므로 만료를 연장한다."""
        if self._redis is None or ttl_seconds <= 0:
            return
        try:
            await self._redis.expire(self._form_memory_key(signature), ttl_seconds)
        except Exception as e:
            logger.debug("폼필 확인 이력 TTL 갱신 실패(%s): %s", signature, e)
