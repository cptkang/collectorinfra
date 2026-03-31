"""통합 스키마 캐시 매니저.

Redis 캐시와 파일 캐시를 추상화하여 통합 관리한다.
backend 설정에 따라 Redis 또는 파일 캐시를 선택하며,
Redis 장애 시 파일 캐시로 graceful fallback한다.

조회 우선순위:
  1차: 메모리 캐시 (SchemaMemoryCache, TTL 5분)
  2차: Redis 캐시 (fingerprint 기반)
  2차-fallback: 파일 캐시 (Redis 장애 시)
  3차: DB 전체 조회
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config import AppConfig
from src.schema_cache.fingerprint import (
    FINGERPRINT_SQL,
    compute_fingerprint,
    compute_fingerprint_from_schema_dict,
)
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.schema_cache.redis_cache import RedisSchemaCache

logger = logging.getLogger(__name__)


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


def _validate_column_keys(data: dict, label: str) -> dict:
    """table.column 형식이 아닌 키를 필터링하고 유효한 항목만 반환한다."""
    valid = {}
    for key, value in data.items():
        if "." in key:
            valid[key] = value
        else:
            logger.warning("%s 키 형식 오류 무시: %s", label, key)
    return valid


class SchemaMemoryCache:
    """스키마 정보를 TTL 기반으로 메모리에 캐시한다 (1차 캐시).

    SchemaCacheManager 내부에서 사용되며, 프로세스 내 동일 db_id에 대한
    반복 조회를 빠르게 처리한다. dict 형태의 스키마를 저장한다.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        """캐시를 초기화한다.

        Args:
            ttl_seconds: 캐시 유효 시간 (기본 5분)
        """
        self._cache: dict[str, dict] = {}
        self._timestamps: dict[str, float] = {}
        self._ttl = ttl_seconds

    def get(self, db_id: str = "_default") -> Optional[dict]:
        """캐시된 스키마 딕셔너리를 반환한다. 만료 시 None.

        Args:
            db_id: DB 식별자 (단일 DB는 "_default")

        Returns:
            캐시된 스키마 딕셔너리 또는 None
        """
        if db_id in self._cache and (
            time.time() - self._timestamps.get(db_id, 0)
        ) < self._ttl:
            return self._cache[db_id]
        return None

    def set(self, schema_dict: dict, db_id: str = "_default") -> None:
        """스키마 딕셔너리를 캐시에 저장한다.

        Args:
            schema_dict: 저장할 스키마 딕셔너리
            db_id: DB 식별자
        """
        self._cache[db_id] = schema_dict
        self._timestamps[db_id] = time.time()

    def invalidate(self, db_id: Optional[str] = None) -> None:
        """캐시를 무효화한다.

        Args:
            db_id: 특정 DB만 무효화 (None이면 전체)
        """
        if db_id is None:
            self._cache.clear()
            self._timestamps.clear()
        else:
            self._cache.pop(db_id, None)
            self._timestamps.pop(db_id, None)


@dataclass
class CacheRefreshResult:
    """캐시 갱신 결과."""

    db_id: str
    status: str  # "created" | "updated" | "unchanged" | "error"
    table_count: int = 0
    fingerprint: str = ""
    description_status: str = "pending"
    message: str = ""


@dataclass
class CacheStatus:
    """캐시 상태 정보."""

    db_id: str
    fingerprint: str = ""
    cached_at: str = ""
    table_count: int = 0
    description_status: str = "pending"
    description_count: int = 0
    synonym_count: int = 0
    backend: str = "none"  # "redis" | "file" | "none"


class SchemaCacheManager:
    """Redis/파일 캐시를 추상화하는 통합 매니저.

    backend 설정에 따라:
    - "redis": Redis 우선, 실패 시 파일 폴백
    - "file": 파일 캐시만 사용 (기존 동작)
    """

    def __init__(self, app_config: AppConfig) -> None:
        """캐시 매니저를 초기화한다.

        Args:
            app_config: 애플리케이션 설정
        """
        self._config = app_config
        self._backend = app_config.schema_cache.backend
        self._redis_cache: Optional[RedisSchemaCache] = None
        self._file_cache = PersistentSchemaCache(
            cache_dir=app_config.schema_cache.cache_dir,
            enabled=app_config.schema_cache.enabled,
        )
        self._redis_available = False
        self._memory_cache = SchemaMemoryCache(ttl_seconds=300)

        if self._backend == "redis":
            self._redis_cache = RedisSchemaCache(
                redis_config=app_config.redis,
                schema_cache_config=app_config.schema_cache,
            )

    async def ensure_redis_connected(self) -> bool:
        """Redis 연결을 보장한다. 실패 시 False를 반환한다.

        Returns:
            Redis 연결 성공 여부
        """
        if self._redis_cache is None:
            return False

        try:
            if not await self._redis_cache.health_check():
                await self._redis_cache.connect()
            self._redis_available = True
            return True
        except Exception as e:
            logger.warning("Redis 연결 실패, 파일 캐시로 폴백: %s", e)
            self._redis_available = False
            return False

    async def disconnect(self) -> None:
        """Redis 연결을 종료한다."""
        if self._redis_cache is not None:
            await self._redis_cache.disconnect()
            self._redis_available = False

    # === 스키마 조회 ===

    async def get_schema(
        self,
        db_id: str,
    ) -> Optional[dict]:
        """캐시에서 스키마를 로드한다.

        Redis 우선, 실패 시 파일 캐시 폴백.

        Args:
            db_id: DB 식별자

        Returns:
            스키마 딕셔너리 또는 None
        """
        # Redis 시도
        if self._backend == "redis" and await self.ensure_redis_connected():
            schema_dict = await self._redis_cache.load_schema(db_id)
            if schema_dict is not None:
                logger.debug("Redis 캐시 히트: db_id=%s", db_id)
                return schema_dict

        # 파일 캐시 폴백
        cached = self._file_cache.get_schema(db_id)
        if cached is not None:
            logger.debug("파일 캐시 히트 (폴백): db_id=%s", db_id)
            return cached

        return None

    async def get_fingerprint(self, db_id: str) -> Optional[str]:
        """캐시된 fingerprint를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            fingerprint 문자열 또는 None
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            fp = await self._redis_cache.get_fingerprint(db_id)
            if fp is not None:
                return fp

        return self._file_cache.get_cached_fingerprint(db_id)

    async def is_changed(self, db_id: str, current_fingerprint: str) -> bool:
        """스키마 변경 여부를 확인한다.

        Args:
            db_id: DB 식별자
            current_fingerprint: 현재 DB fingerprint

        Returns:
            변경되었으면 True
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.is_changed(db_id, current_fingerprint)

        return self._file_cache.is_changed(db_id, current_fingerprint)

    async def is_fingerprint_fresh(self, db_id: str) -> bool:
        """fingerprint의 TTL이 아직 유효한지 확인한다.

        Redis 백엔드에서만 동작하며, 파일 백엔드는 항상 False 반환.

        Args:
            db_id: DB 식별자

        Returns:
            TTL 유효 시 True
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            ttl = self._config.schema_cache.fingerprint_ttl_seconds
            return await self._redis_cache.is_fingerprint_fresh(db_id, ttl)
        return False

    async def refresh_fingerprint_ttl(self, db_id: str) -> None:
        """fingerprint 검증 타임스탬프를 현재 시각으로 갱신한다.

        Args:
            db_id: DB 식별자
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            await self._redis_cache.refresh_fingerprint_checked_at(db_id)

    async def save_schema(
        self,
        db_id: str,
        schema_dict: dict,
        fingerprint: Optional[str] = None,
    ) -> bool:
        """스키마를 캐시에 저장한다.

        Redis와 파일 캐시 모두에 저장한다 (이중 저장으로 폴백 보장).

        Args:
            db_id: DB 식별자
            schema_dict: 스키마 딕셔너리
            fingerprint: fingerprint (None이면 자동 계산)

        Returns:
            저장 성공 여부 (하나라도 성공하면 True)
        """
        # 유효성 검증: fingerprint가 빈 문자열이면 저장 거부
        if fingerprint is not None and fingerprint == "":
            logger.warning(
                "스키마 저장 거부: fingerprint가 빈 문자열 (db_id=%s)", db_id
            )
            return False

        # 유효성 검증: 스키마 딕셔너리 검증
        valid, reason = _validate_schema_dict(schema_dict)
        if not valid:
            logger.warning(
                "스키마 저장 거부: %s (db_id=%s)", reason, db_id
            )
            return False

        if fingerprint is None:
            fingerprint = compute_fingerprint_from_schema_dict(schema_dict)

        saved = False

        # Redis 저장
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.save_schema(db_id, schema_dict, fingerprint):
                saved = True

        # 파일 캐시에도 저장 (폴백 보장)
        if self._file_cache.save(db_id, schema_dict, fingerprint):
            saved = True

        return saved

    # === 구조 분석 메타 (structure_meta) ===

    async def save_structure_meta(
        self,
        db_id: str,
        structure_meta: dict,
    ) -> bool:
        """구조 분석 결과(structure_meta)를 캐시에 저장한다.

        structure_meta는 EAV 패턴 등 DB 구조 분석 결과로,
        스키마(tables/relationships)와 다른 구조이므로 별도 검증·저장 경로를 사용한다.

        Args:
            db_id: DB 식별자
            structure_meta: 구조 분석 결과 (patterns, query_guide 등)

        Returns:
            저장 성공 여부
        """
        if not structure_meta:
            logger.warning(
                "structure_meta 저장 거부: 빈 데이터 (db_id=%s)", db_id
            )
            return False

        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.save_structure_meta(
                db_id, structure_meta
            )

        logger.debug(
            "structure_meta 저장 스킵: Redis 미연결 (db_id=%s)", db_id
        )
        return False

    async def get_structure_meta(self, db_id: str) -> Optional[dict]:
        """캐시에서 구조 분석 결과(structure_meta)를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            구조 분석 딕셔너리 또는 None
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_structure_meta(db_id)
        return None

    # === DB 설명 ===

    async def get_db_descriptions(self) -> dict[str, str]:
        """모든 DB 설명을 로드한다.

        Returns:
            {db_id: description} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_db_descriptions()

        # 파일 캐시 폴백: 각 DB 캐시 파일에서 db_description 필드 조회
        result: dict[str, str] = {}
        for file_info in self._file_cache.list_cached_dbs():
            db_id = file_info["db_id"]
            data = self._file_cache.load(db_id)
            if data and data.get("_db_description"):
                result[db_id] = data["_db_description"]
        return result

    async def get_db_description(self, db_id: str) -> Optional[str]:
        """특정 DB의 설명을 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            DB 설명 문자열 또는 None
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.get_db_description(db_id)

        # 파일 캐시 폴백
        data = self._file_cache.load(db_id)
        if data:
            return data.get("_db_description")
        return None

    async def save_db_description(
        self,
        db_id: str,
        description: str,
    ) -> bool:
        """DB 설명을 저장한다.

        Redis와 파일 캐시 모두에 저장한다 (이중 저장으로 폴백 보장).

        Args:
            db_id: DB 식별자
            description: DB 설명 (한국어)

        Returns:
            저장 성공 여부
        """
        # 유효성 검증: 빈 문자열이면 저장 거부
        if not description:
            logger.warning(
                "DB 설명 저장 거부: 빈 문자열 (db_id=%s)", db_id
            )
            return False

        saved = False

        # Redis 저장
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.save_db_description(db_id, description):
                saved = True

        # 파일 캐시에도 저장 (기존 캐시 파일의 _db_description 필드 업데이트)
        if self._file_cache.update_field(db_id, "_db_description", description):
            saved = True

        return saved

    async def delete_db_description(self, db_id: str) -> bool:
        """DB 설명을 삭제한다.

        Redis와 파일 캐시 모두에서 삭제한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        success = False
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.delete_db_description(db_id):
                success = True
        # 파일 캐시의 _db_description 필드도 삭제
        if self._file_cache.delete_field(db_id, "_db_description"):
            success = True
        return success

    # === 컬럼 설명 ===

    async def get_descriptions(self, db_id: str) -> dict[str, str]:
        """컬럼 설명을 로드한다.

        Redis 우선, 실패 시 파일 캐시 폴백.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: description} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            result = await self._redis_cache.load_descriptions(db_id)
            if result:
                return result
        # 파일 캐시 폴백
        return self._file_cache.load_descriptions(db_id)

    async def save_descriptions(
        self,
        db_id: str,
        descriptions: dict[str, str],
    ) -> bool:
        """컬럼 설명을 저장한다.

        Redis와 파일 캐시 모두에 저장한다 (이중 저장으로 폴백 보장).

        Args:
            db_id: DB 식별자
            descriptions: {table.column: description} 매핑

        Returns:
            저장 성공 여부 (하나라도 성공하면 True)
        """
        # 유효성 검증: table.column 형식이 아닌 키 필터링
        descriptions = _validate_column_keys(descriptions, "descriptions")
        if not descriptions:
            logger.warning(
                "descriptions 저장 거부: 유효한 키가 없음 (db_id=%s)", db_id
            )
            return False

        saved = False
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.save_descriptions(db_id, descriptions):
                saved = True
        # 파일 캐시에도 저장 (폴백 보장)
        if self._file_cache.save_descriptions(db_id, descriptions):
            saved = True
        return saved

    # === 유사 단어 ===

    async def get_synonyms(self, db_id: str) -> dict[str, list[str]]:
        """유사 단어를 로드한다.

        Redis 우선, 실패 시 파일 캐시 폴백.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: [synonym, ...]} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            result = await self._redis_cache.load_synonyms(db_id)
            if result:
                return result
        # 파일 캐시 폴백
        return self._file_cache.load_synonyms(db_id)

    async def save_synonyms(
        self,
        db_id: str,
        synonyms: dict[str, list[str]],
        source: str = "llm",
    ) -> bool:
        """유사 단어를 저장한다.

        Redis와 파일 캐시 모두에 저장한다 (이중 저장으로 폴백 보장).

        Args:
            db_id: DB 식별자
            synonyms: {table.column: [synonym, ...]} 매핑
            source: source 태그 ("llm" | "operator")

        Returns:
            저장 성공 여부 (하나라도 성공하면 True)
        """
        # 유효성 검증: table.column 형식이 아닌 키 필터링
        synonyms = _validate_column_keys(synonyms, "synonyms")
        # 빈 리스트 값 제거
        synonyms = {k: v for k, v in synonyms.items() if v}
        if not synonyms:
            logger.warning(
                "synonyms 저장 거부: 유효한 항목이 없음 (db_id=%s)", db_id
            )
            return False

        saved = False
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.save_synonyms(
                db_id, synonyms, source=source
            ):
                saved = True
        # 파일 캐시에도 저장 (폴백 보장)
        if self._file_cache.save_synonyms(db_id, synonyms):
            saved = True
        return saved

    async def add_synonyms(
        self,
        db_id: str,
        column: str,
        words: list[str],
        source: str = "operator",
    ) -> bool:
        """특정 컬럼에 유사 단어를 추가한다.

        Args:
            db_id: DB 식별자
            column: "table.column" 형식
            words: 추가할 유사 단어 목록
            source: source 태그 ("llm" | "operator")

        Returns:
            성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.add_synonyms(
                db_id, column, words, source=source
            )
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
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.remove_synonyms(db_id, column, words)
        return False

    # === 글로벌 유사단어 ===

    async def get_global_synonyms(self) -> dict[str, list[str]]:
        """글로벌 유사단어 사전을 로드한다.

        Returns:
            {column_name: [synonym, ...]} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_global_synonyms()
        return {}

    async def save_global_synonyms(
        self,
        synonyms: dict[str, list[str]],
    ) -> bool:
        """글로벌 유사단어 사전을 저장한다.

        Args:
            synonyms: {column_name: [synonym, ...]} 매핑

        Returns:
            저장 성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.save_global_synonyms(synonyms)
        return False

    async def add_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """글로벌 유사단어 사전에 단어를 추가한다.

        Args:
            column_name: 컬럼명 (bare name)
            words: 추가할 유사 단어 목록

        Returns:
            성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.add_global_synonym(column_name, words)
        return False

    async def remove_global_synonym(
        self,
        column_name: str,
        words: list[str],
    ) -> bool:
        """글로벌 유사단어 사전에서 단어를 삭제한다.

        Args:
            column_name: 컬럼명
            words: 삭제할 유사 단어 목록

        Returns:
            성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.remove_global_synonym(column_name, words)
        return False

    async def get_global_synonyms_full(self) -> dict[str, dict]:
        """글로벌 유사단어 사전을 description 포함하여 로드한다.

        Returns:
            {column_name: {"words": [...], "description": "..."}} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_global_synonyms_full()
        return {}

    async def update_global_description(
        self,
        column_name: str,
        description: str,
    ) -> bool:
        """글로벌 사전의 컬럼 설명을 수정한다.

        Args:
            column_name: 컬럼명 (bare name)
            description: 새 설명 텍스트

        Returns:
            성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.update_global_description(
                column_name, description
            )
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
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.get_global_description(column_name)
        return None

    async def list_global_column_names(self) -> list[str]:
        """글로벌 사전에 등록된 전체 컬럼명 목록을 반환한다.

        Returns:
            컬럼명 목록 (정렬)
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.list_global_column_names()
        return []

    async def generate_global_synonyms(
        self,
        column_name: str,
        llm: Any,
        seed_words: list[str] | None = None,
    ) -> dict:
        """LLM으로 글로벌 유사 단어 + description을 생성한다.

        기존 항목이 있으면 merge (중복 제거).

        Args:
            column_name: 컬럼명 (bare name)
            llm: LLM 인스턴스
            seed_words: 사용자 제공 예시 (참고용, 선택)

        Returns:
            {"words": [...], "description": "..."} 딕셔너리
        """
        from langchain_core.messages import HumanMessage, SystemMessage
        from src.prompts.cache_management import GENERATE_GLOBAL_SYNONYMS_PROMPT

        seed_text = ""
        if seed_words:
            seed_text = f"\n참고 예시: {', '.join(seed_words)}"

        prompt = GENERATE_GLOBAL_SYNONYMS_PROMPT.format(
            column_name=column_name,
            seed_words_text=seed_text,
        )

        try:
            import json as json_mod
            import re

            response = await llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content

            # JSON 추출
            json_match = re.search(
                r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL
            )
            if json_match:
                parsed = json_mod.loads(json_match.group(1))
            else:
                brace_match = re.search(r"\{.*\}", content, re.DOTALL)
                if brace_match:
                    parsed = json_mod.loads(brace_match.group())
                else:
                    parsed = json_mod.loads(content)

            words = parsed.get("words", [])
            description = parsed.get("description", "")

            # seed_words가 있으면 포함 보장
            if seed_words:
                words = list(dict.fromkeys(words + seed_words))

            # 기존 항목과 merge
            if self._backend == "redis" and await self.ensure_redis_connected():
                existing = await self._redis_cache.load_global_synonyms_full()
                existing_entry = existing.get(column_name, {})
                existing_words = existing_entry.get("words", [])

                merged_words = list(dict.fromkeys(existing_words + words))
                # description은 새로 생성된 것을 우선 사용 (기존이 있어도 덮어씀)
                final_desc = description or existing_entry.get("description", "")

                entry = {"words": merged_words, "description": final_desc}
                await self._redis_cache.save_global_synonyms(
                    {column_name: entry}
                )
                return entry

            return {"words": words, "description": description}

        except Exception as e:
            logger.error(
                "글로벌 유사 단어 LLM 생성 실패 (column=%s): %s",
                column_name, e,
            )
            # seed_words만이라도 저장
            if seed_words and self._backend == "redis":
                if await self.ensure_redis_connected():
                    entry = {"words": seed_words}
                    await self._redis_cache.save_global_synonyms(
                        {column_name: entry}
                    )
                    return entry
            return {"words": seed_words or [], "description": ""}

    async def find_similar_global_columns(
        self,
        column_name: str,
        llm: Any,
    ) -> list[dict]:
        """LLM으로 글로벌 사전에서 의미적으로 유사한 컬럼을 탐색한다.

        Args:
            column_name: 찾을 컬럼명 (bare name)
            llm: LLM 인스턴스

        Returns:
            유사 컬럼 목록 [{"column": "hostname", "words": [...], "description": "..."}]
        """
        from langchain_core.messages import HumanMessage
        from src.prompts.cache_management import FIND_SIMILAR_COLUMNS_PROMPT

        global_full = await self.get_global_synonyms_full()
        if not global_full:
            return []

        existing_columns = list(global_full.keys())
        if not existing_columns:
            return []

        # 컬럼 목록 + 설명 포맷
        col_info_lines = []
        for col, entry in global_full.items():
            desc = entry.get("description", "")
            words = entry.get("words", [])
            line = f"- {col}"
            if desc:
                line += f": {desc}"
            if words:
                line += f" (유사단어: {', '.join(words[:5])})"
            col_info_lines.append(line)
        col_info_text = "\n".join(col_info_lines)

        prompt = FIND_SIMILAR_COLUMNS_PROMPT.format(
            target_column=column_name,
            existing_columns_info=col_info_text,
        )

        try:
            import json as json_mod
            import re

            response = await llm.ainvoke([HumanMessage(content=prompt)])
            content = response.content

            json_match = re.search(
                r"```(?:json)?\s*(\[.*?\])\s*```", content, re.DOTALL
            )
            if json_match:
                similar_list = json_mod.loads(json_match.group(1))
            else:
                bracket_match = re.search(r"\[.*\]", content, re.DOTALL)
                if bracket_match:
                    similar_list = json_mod.loads(bracket_match.group())
                else:
                    similar_list = json_mod.loads(content)

            # 결과에 글로벌 사전 데이터 보강
            results = []
            for item in similar_list:
                col = item.get("column", "")
                if col in global_full:
                    entry = global_full[col]
                    results.append({
                        "column": col,
                        "words": entry.get("words", []),
                        "description": entry.get("description", ""),
                    })
            return results

        except Exception as e:
            logger.warning(
                "유사 컬럼 탐색 실패 (column=%s): %s", column_name, e
            )
            return []

    async def reuse_synonyms(
        self,
        source_column: str,
        target_column: str,
        db_id: Optional[str] = None,
        mode: str = "copy",
        llm: Any = None,
    ) -> dict:
        """기존 글로벌 컬럼의 유사 단어를 새 컬럼에 복사/병합한다.

        Args:
            source_column: 소스 컬럼명 (글로벌 사전에 존재)
            target_column: 대상 컬럼명
            db_id: DB 식별자 (DB별 synonyms에도 적용, 선택)
            mode: "copy" (그대로 복사) | "merge" (기존 + LLM 신규 병합)
            llm: LLM 인스턴스 (merge 모드에서 필요)

        Returns:
            결과 딕셔너리 {"words": [...], "description": "..."}
        """
        global_full = await self.get_global_synonyms_full()
        source_entry = global_full.get(source_column, {})
        source_words = source_entry.get("words", [])
        source_desc = source_entry.get("description", "")

        if mode == "copy":
            # 소스의 유사 단어를 타겟에 복사
            entry = {
                "words": list(source_words),
                "description": source_desc,
            }
            if self._backend == "redis" and await self.ensure_redis_connected():
                await self._redis_cache.save_global_synonyms(
                    {target_column: entry}
                )
            return entry

        elif mode == "merge" and llm is not None:
            # LLM으로 새 유사 단어 생성 후 기존과 병합
            new_result = await self.generate_global_synonyms(
                target_column, llm, seed_words=source_words
            )
            merged_words = list(
                dict.fromkeys(source_words + new_result.get("words", []))
            )
            merged_desc = (
                new_result.get("description") or source_desc
            )
            entry = {"words": merged_words, "description": merged_desc}
            if self._backend == "redis" and await self.ensure_redis_connected():
                await self._redis_cache.save_global_synonyms(
                    {target_column: entry}
                )
            return entry

        else:
            # 기본: copy
            entry = {
                "words": list(source_words),
                "description": source_desc,
            }
            if self._backend == "redis" and await self.ensure_redis_connected():
                await self._redis_cache.save_global_synonyms(
                    {target_column: entry}
                )
            return entry

    async def load_synonyms_with_global_fallback(
        self,
        db_id: str,
        schema_dict: Optional[dict] = None,
    ) -> dict[str, list[str]]:
        """DB별 synonyms를 조회하고, 없는 컬럼은 글로벌 사전에서 폴백한다.

        Args:
            db_id: DB 식별자
            schema_dict: 스키마 딕셔너리 (글로벌 폴백 시 컬럼명 매칭에 사용)

        Returns:
            {table.column: [synonym, ...]} 매핑 (DB별 + 글로벌 폴백 병합)
        """
        db_synonyms = await self.get_synonyms(db_id)
        global_synonyms = await self.get_global_synonyms()

        if not global_synonyms:
            return db_synonyms

        # DB 스키마에서 컬럼명 목록 추출
        if schema_dict is None:
            schema_dict = await self.get_schema(db_id)
        if schema_dict is None:
            return db_synonyms

        result = dict(db_synonyms)
        tables = schema_dict.get("tables", {})
        for table_name, table_data in tables.items():
            for col in table_data.get("columns", []):
                col_key = f"{table_name}.{col['name']}"
                if col_key not in result:
                    # 글로벌 사전에서 bare column name으로 폴백
                    bare_name = col["name"]
                    if bare_name in global_synonyms:
                        result[col_key] = global_synonyms[bare_name]

        return result

    async def sync_global_synonyms(self, db_id: str) -> int:
        """DB별 synonyms를 글로벌 사전에 병합한다.

        새 DB 추가 시 자동 호출하여 글로벌 사전을 풍부하게 한다.

        Args:
            db_id: DB 식별자

        Returns:
            병합된 컬럼 수
        """
        db_synonyms = await self.get_synonyms(db_id)
        if not db_synonyms:
            return 0

        global_synonyms = await self.get_global_synonyms()
        merged_count = 0

        for col_key, words in db_synonyms.items():
            # bare column name 추출 (table.column -> column)
            bare_name = col_key.split(".", 1)[-1] if "." in col_key else col_key
            existing = global_synonyms.get(bare_name, [])
            merged = list(dict.fromkeys(existing + words))
            if merged != existing:
                await self.add_global_synonym(bare_name, words)
                merged_count += 1

        logger.info(
            "글로벌 유사단어 동기화: db_id=%s, merged=%d columns",
            db_id,
            merged_count,
        )
        return merged_count

    # === 통합 스키마 조회 (3단계 캐시 + DB 폴백) ===

    async def get_schema_or_fetch(
        self,
        client: Any,
        db_id: str,
    ) -> tuple[dict, bool, dict[str, str], dict[str, list[str]]]:
        """3단계 캐시를 거쳐 스키마를 조회한다. 캐시 미스 시 DB에서 직접 조회.

        조회 순서:
          1차: 메모리 캐시 (TTL 기반)
          2차-A: Redis/파일 캐시 (fingerprint TTL 유효 시)
          2차-B: fingerprint TTL 만료 시 DB fingerprint 재검증
          3차: DB 전체 스키마 조회 (캐시 미스)

        Args:
            client: DB 클라이언트 (execute_sql, get_full_schema 메서드 필요)
            db_id: DB 식별자

        Returns:
            (schema_dict, cache_hit, descriptions, synonyms) 튜플
            - schema_dict: 스키마 딕셔너리
            - cache_hit: 캐시에서 로드했으면 True (save 불필요)
            - descriptions: {table.column: description}
            - synonyms: {table.column: [synonym, ...]}
        """
        descriptions: dict[str, str] = {}
        synonyms: dict[str, list[str]] = {}

        # 1차: 메모리 캐시
        cached_mem = self._memory_cache.get(db_id)
        if cached_mem is not None:
            logger.debug("메모리 캐시 히트: db_id=%s", db_id)
            descriptions = await self.get_descriptions(db_id)
            synonyms = await self.load_synonyms_with_global_fallback(
                db_id, cached_mem
            )
            return cached_mem, True, descriptions, synonyms

        # 2차-A: Redis/파일 캐시 + fingerprint TTL 유효
        try:
            fingerprint_fresh = await self.is_fingerprint_fresh(db_id)
            if fingerprint_fresh:
                cached_schema = await self.get_schema(db_id)
                if cached_schema is not None:
                    self._memory_cache.set(cached_schema, db_id)
                    descriptions = await self.get_descriptions(db_id)
                    synonyms = await self.load_synonyms_with_global_fallback(
                        db_id, cached_schema
                    )
                    logger.info(
                        "Redis/파일 캐시 히트 (fingerprint TTL 유효): db_id=%s",
                        db_id,
                    )
                    return cached_schema, True, descriptions, synonyms
        except Exception as e:
            logger.warning("fingerprint TTL 확인 실패 (%s): %s", db_id, e)

        # 2차-B: fingerprint TTL 만료 -- DB에서 fingerprint 재검증
        try:
            result = await client.execute_sql(FINGERPRINT_SQL)
            if result.rows:
                current_fp = compute_fingerprint(result.rows)
                changed = await self.is_changed(db_id, current_fp)
                if not changed:
                    await self.refresh_fingerprint_ttl(db_id)
                    cached_schema = await self.get_schema(db_id)
                    if cached_schema is not None:
                        self._memory_cache.set(cached_schema, db_id)
                        descriptions = await self.get_descriptions(db_id)
                        synonyms = await self.load_synonyms_with_global_fallback(
                            db_id, cached_schema
                        )
                        logger.info(
                            "캐시 히트 (fingerprint 재검증): db_id=%s, fingerprint=%s",
                            db_id,
                            current_fp,
                        )
                        return cached_schema, True, descriptions, synonyms
        except Exception as e:
            logger.warning("fingerprint 조회 실패 (%s): %s", db_id, e)

        # 3차: DB 전체 조회
        logger.info("캐시 미스, DB 전체 스키마 조회: db_id=%s", db_id)
        full_schema = await client.get_full_schema()

        # SchemaInfo -> dict 변환
        tables_dict: dict[str, Any] = {}
        for table_name, table_info in full_schema.tables.items():
            columns = [
                {
                    "name": col.name,
                    "type": col.data_type,
                    "nullable": col.nullable,
                    "primary_key": col.is_primary_key,
                    "foreign_key": col.is_foreign_key,
                    "references": col.references,
                }
                for col in table_info.columns
            ]
            tables_dict[table_name] = {
                "columns": columns,
                "row_count_estimate": table_info.row_count_estimate,
                "sample_data": [],
            }

        schema_dict: dict[str, Any] = {
            "tables": tables_dict,
            "relationships": full_schema.relationships,
        }

        # Redis/파일 캐시에 저장
        await self.save_schema(db_id, schema_dict)
        self._memory_cache.set(schema_dict, db_id)

        # stale entry 정리
        await self.cleanup_stale_entries(db_id, schema_dict)

        # descriptions/synonyms 자동 생성 (캐시 미스 시)
        descriptions = await self.get_descriptions(db_id)
        if not descriptions:
            try:
                from src.schema_cache.description_generator import (
                    DescriptionGenerator,
                )
                from src.llm import create_llm
                from src.config import load_config

                config = load_config()
                if config.schema_cache.auto_generate_descriptions:
                    llm = create_llm(config)
                    generator = DescriptionGenerator(llm)
                    descriptions, gen_synonyms = (
                        await generator.generate_for_db(schema_dict)
                    )
                    await self.save_descriptions(db_id, descriptions)
                    await self.save_synonyms(db_id, gen_synonyms)
                    await self.sync_global_synonyms(db_id)
                    logger.info(
                        "스키마 최초 조회 시 descriptions/synonyms 자동 생성: "
                        "db_id=%s, descriptions=%d, synonyms=%d",
                        db_id,
                        len(descriptions),
                        len(gen_synonyms),
                    )
            except Exception as e:
                logger.warning(
                    "descriptions/synonyms 자동 생성 실패 (%s): %s", db_id, e
                )

        synonyms = await self.load_synonyms_with_global_fallback(
            db_id, schema_dict
        )

        logger.info(
            "스키마 수집 완료: db_id=%s, %d개 테이블",
            db_id,
            len(tables_dict),
        )
        return schema_dict, False, descriptions, synonyms

    async def cleanup_stale_entries(
        self,
        db_id: str,
        schema_dict: dict,
    ) -> dict:
        """스키마 갱신 후 descriptions/synonyms에서 stale 항목을 정리한다.

        새 스키마의 table.column 집합과 비교하여 존재하지 않는 키를 제거한다.

        Args:
            db_id: DB 식별자
            schema_dict: 새로 갱신된 스키마 딕셔너리

        Returns:
            정리 결과 {"removed_descriptions": [...], "removed_synonyms": [...]}
        """
        result: dict[str, list[str]] = {
            "removed_descriptions": [],
            "removed_synonyms": [],
        }

        try:
            # 새 스키마에서 유효한 table.column 키 집합 추출
            valid_keys: set[str] = set()
            for table_name, table_data in schema_dict.get("tables", {}).items():
                for col in table_data.get("columns", []):
                    valid_keys.add(f"{table_name}.{col['name']}")

            if not valid_keys:
                logger.debug(
                    "cleanup_stale_entries: 유효 키가 없음, 건너뜀 (db_id=%s)",
                    db_id,
                )
                return result

            # descriptions 정리
            descriptions = await self.get_descriptions(db_id)
            if descriptions:
                stale_desc_keys = [
                    k for k in descriptions if k not in valid_keys
                ]
                if stale_desc_keys:
                    cleaned_descriptions = {
                        k: v
                        for k, v in descriptions.items()
                        if k in valid_keys
                    }
                    await self.save_descriptions(db_id, cleaned_descriptions)
                    result["removed_descriptions"] = stale_desc_keys
                    logger.debug(
                        "stale descriptions 제거: db_id=%s, keys=%s",
                        db_id,
                        stale_desc_keys,
                    )

            # synonyms 정리
            synonyms = await self.get_synonyms(db_id)
            if synonyms:
                stale_syn_keys = [
                    k for k in synonyms if k not in valid_keys
                ]
                if stale_syn_keys:
                    cleaned_synonyms = {
                        k: v
                        for k, v in synonyms.items()
                        if k in valid_keys
                    }
                    await self.save_synonyms(db_id, cleaned_synonyms)
                    result["removed_synonyms"] = stale_syn_keys
                    logger.debug(
                        "stale synonyms 제거: db_id=%s, keys=%s",
                        db_id,
                        stale_syn_keys,
                    )

        except Exception as e:
            logger.warning(
                "stale entry 정리 중 오류 발생 (db_id=%s): %s", db_id, e
            )

        return result

    def invalidate_memory_cache(self, db_id: Optional[str] = None) -> None:
        """메모리 캐시를 무효화한다.

        Args:
            db_id: 특정 DB만 무효화 (None이면 전체)
        """
        self._memory_cache.invalidate(db_id)

    # === 캐시 갱신 ===

    async def refresh_cache(
        self,
        db_id: str,
        client: Any,
        force: bool = False,
    ) -> CacheRefreshResult:
        """캐시를 갱신한다.

        fingerprint 비교 후 변경 시에만 갱신한다.
        force=True이면 무조건 갱신한다.

        Args:
            db_id: DB 식별자
            client: DB 클라이언트
            force: 강제 갱신 여부

        Returns:
            갱신 결과
        """
        try:
            # 현재 fingerprint 조회
            result = await client.execute_sql(FINGERPRINT_SQL)
            current_fp = compute_fingerprint(result.rows) if result.rows else None

            if current_fp is None:
                return CacheRefreshResult(
                    db_id=db_id,
                    status="error",
                    message="fingerprint 조회 실패",
                )

            # 변경 여부 확인
            if not force:
                changed = await self.is_changed(db_id, current_fp)
                if not changed:
                    status = await self.get_status(db_id)
                    return CacheRefreshResult(
                        db_id=db_id,
                        status="unchanged",
                        table_count=status.table_count,
                        fingerprint=current_fp,
                        description_status=status.description_status,
                        message="변경 없음, 기존 캐시 유지",
                    )

            # DB 전체 스키마 조회
            full_schema = await client.get_full_schema()

            # 스키마를 dict로 변환
            from src.dbhub.models import schema_to_dict
            schema_dict = schema_to_dict(
                full_schema, list(full_schema.tables.keys())
            )

            # 캐시 저장
            await self.save_schema(db_id, schema_dict, current_fp)

            # stale entry 정리
            cleanup_result = await self.cleanup_stale_entries(db_id, schema_dict)
            if cleanup_result.get("removed_descriptions") or cleanup_result.get(
                "removed_synonyms"
            ):
                logger.info(
                    "stale entry 정리: db_id=%s, descriptions=%d, synonyms=%d",
                    db_id,
                    len(cleanup_result.get("removed_descriptions", [])),
                    len(cleanup_result.get("removed_synonyms", [])),
                )

            table_count = len(schema_dict.get("tables", {}))
            return CacheRefreshResult(
                db_id=db_id,
                status="updated" if not force else "created",
                table_count=table_count,
                fingerprint=current_fp,
                description_status="pending",
                message="캐시 갱신 완료",
            )

        except Exception as e:
            logger.error("캐시 갱신 실패 (db_id=%s): %s", db_id, e)
            return CacheRefreshResult(
                db_id=db_id,
                status="error",
                message=str(e),
            )

    # === 관리 ===

    async def invalidate(self, db_id: str) -> bool:
        """특정 DB 캐시를 삭제한다 (메모리 + Redis + 파일 + db_profile).

        DB별 데이터를 전체 삭제하며, 글로벌 사전만 보존한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        self._memory_cache.invalidate(db_id)
        success = False
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.invalidate(db_id):
                success = True
        if self._file_cache.invalidate(db_id):
            success = True
        self._delete_db_profile(db_id)
        return success

    async def invalidate_all(self) -> int:
        """모든 캐시를 삭제한다 (메모리 + Redis + 파일).

        글로벌 사전만 보존한다.

        Returns:
            삭제된 항목 수
        """
        self._memory_cache.invalidate()
        count = 0
        if self._backend == "redis" and await self.ensure_redis_connected():
            count += await self._redis_cache.invalidate_all()
        count += self._file_cache.invalidate_all()
        return count

    def _delete_db_profile(self, db_id: str) -> None:
        """config/db_profiles/{db_id} 파일을 삭제한다.

        Args:
            db_id: DB 식별자
        """
        for ext in (".yaml", ".json"):
            safe_id = "".join(
                c if c.isalnum() or c in ("_", "-") else "_" for c in db_id
            )
            profile_path = os.path.join("config", "db_profiles", f"{safe_id}{ext}")
            try:
                if os.path.exists(profile_path):
                    os.unlink(profile_path)
                    logger.info("db_profile 삭제: %s", profile_path)
            except OSError as e:
                logger.warning("db_profile 삭제 실패 (%s): %s", profile_path, e)

    async def get_status(self, db_id: str) -> CacheStatus:
        """특정 DB의 캐시 상태를 반환한다.

        Args:
            db_id: DB 식별자

        Returns:
            캐시 상태 정보
        """
        # Redis 확인
        if self._backend == "redis" and await self.ensure_redis_connected():
            status = await self._redis_cache.get_status(db_id)
            if status.get("exists"):
                return CacheStatus(
                    db_id=db_id,
                    fingerprint=status.get("fingerprint", ""),
                    cached_at=status.get("cached_at", ""),
                    table_count=status.get("table_count", 0),
                    description_status=status.get("description_status", "pending"),
                    description_count=status.get("description_count", 0),
                    synonym_count=status.get("synonym_count", 0),
                    backend="redis",
                )

        # 파일 캐시 확인
        cached = self._file_cache.load(db_id)
        if cached is not None:
            schema = cached.get("schema", {})
            return CacheStatus(
                db_id=db_id,
                fingerprint=cached.get("_fingerprint", ""),
                cached_at=cached.get("_cached_at_iso", ""),
                table_count=len(schema.get("tables", {})),
                backend="file",
            )

        return CacheStatus(db_id=db_id, backend="none")

    async def get_all_status(self) -> list[CacheStatus]:
        """모든 캐시 상태를 반환한다.

        Returns:
            캐시 상태 목록
        """
        statuses: list[CacheStatus] = []
        seen_db_ids: set[str] = set()

        # Redis 캐시 목록
        if self._backend == "redis" and await self.ensure_redis_connected():
            redis_dbs = await self._redis_cache.list_cached_dbs()
            for db_info in redis_dbs:
                db_id = db_info["db_id"]
                seen_db_ids.add(db_id)
                statuses.append(CacheStatus(
                    db_id=db_id,
                    fingerprint=db_info.get("fingerprint", ""),
                    cached_at=db_info.get("cached_at", ""),
                    table_count=db_info.get("table_count", 0),
                    description_status=db_info.get("description_status", "pending"),
                    backend="redis",
                ))

        # 파일 캐시에만 있는 항목 추가
        for file_info in self._file_cache.list_cached_dbs():
            db_id = file_info["db_id"]
            if db_id not in seen_db_ids:
                statuses.append(CacheStatus(
                    db_id=db_id,
                    fingerprint=file_info.get("fingerprint", ""),
                    cached_at=file_info.get("cached_at", ""),
                    backend="file",
                ))

        return sorted(statuses, key=lambda s: s.db_id)

    @property
    def redis_available(self) -> bool:
        """Redis 연결 가능 여부."""
        return self._redis_available

    async def sync_known_attributes_to_eav_synonyms(
        self, known_attributes_detail: list[dict]
    ) -> int:
        """수동 프로필 known_attributes를 Redis eav_name_synonyms에 동기화한다.

        Redis가 없는 환경에서는 0을 반환하고 graceful하게 스킵한다.

        Args:
            known_attributes_detail: [{name: str, description: str, synonyms: [str]}, ...]

        Returns:
            동기화된 속성 수
        """
        if self._redis_cache and self._redis_available:
            return await self._redis_cache.sync_known_attributes_to_eav_synonyms(
                known_attributes_detail
            )
        return 0

    @property
    def backend(self) -> str:
        """현재 캐시 백엔드."""
        return self._backend


# 모듈 레벨 싱글톤
_cache_manager: Optional[SchemaCacheManager] = None


def get_cache_manager(app_config: Optional[AppConfig] = None) -> SchemaCacheManager:
    """캐시 매니저 싱글톤을 반환한다.

    Args:
        app_config: 앱 설정 (None이면 로드)

    Returns:
        SchemaCacheManager 인스턴스
    """
    global _cache_manager
    if _cache_manager is None:
        if app_config is None:
            from src.config import load_config
            app_config = load_config()
        _cache_manager = SchemaCacheManager(app_config)
    return _cache_manager


def reset_cache_manager() -> None:
    """캐시 매니저 싱글톤을 리셋한다. 테스트용."""
    global _cache_manager
    _cache_manager = None
