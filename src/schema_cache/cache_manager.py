"""통합 스키마 캐시 매니저.

Redis 캐시와 파일 캐시를 추상화하여 통합 관리한다.
backend 설정에 따라 Redis 또는 파일 캐시를 선택하며,
Redis 장애 시 파일 캐시로 graceful fallback한다.

조회 우선순위:
  1차: 메모리 캐시 (SchemaCache, TTL 5분)
  2차: Redis 캐시 (fingerprint 기반)
  2차-fallback: 파일 캐시 (Redis 장애 시)
  3차: DB 전체 조회
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.config import AppConfig
from src.schema_cache.fingerprint import compute_fingerprint_from_schema_dict
from src.schema_cache.persistent_cache import PersistentSchemaCache
from src.schema_cache.redis_cache import RedisSchemaCache

logger = logging.getLogger(__name__)


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

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.delete_db_description(db_id)
        return False

    # === 컬럼 설명 ===

    async def get_descriptions(self, db_id: str) -> dict[str, str]:
        """컬럼 설명을 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: description} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_descriptions(db_id)
        return {}

    async def save_descriptions(
        self,
        db_id: str,
        descriptions: dict[str, str],
    ) -> bool:
        """컬럼 설명을 저장한다.

        Args:
            db_id: DB 식별자
            descriptions: {table.column: description} 매핑

        Returns:
            저장 성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.save_descriptions(db_id, descriptions)
        return False

    # === 유사 단어 ===

    async def get_synonyms(self, db_id: str) -> dict[str, list[str]]:
        """유사 단어를 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: [synonym, ...]} 매핑
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.load_synonyms(db_id)
        return {}

    async def save_synonyms(
        self,
        db_id: str,
        synonyms: dict[str, list[str]],
        source: str = "llm",
    ) -> bool:
        """유사 단어를 저장한다.

        Args:
            db_id: DB 식별자
            synonyms: {table.column: [synonym, ...]} 매핑
            source: source 태그 ("llm" | "operator")

        Returns:
            저장 성공 여부
        """
        if self._backend == "redis" and await self.ensure_redis_connected():
            return await self._redis_cache.save_synonyms(
                db_id, synonyms, source=source
            )
        return False

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
        from src.schema_cache.fingerprint import FINGERPRINT_SQL, compute_fingerprint

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
            from src.nodes.schema_analyzer import _schema_to_dict
            schema_dict = _schema_to_dict(
                full_schema, list(full_schema.tables.keys())
            )

            # 캐시 저장
            await self.save_schema(db_id, schema_dict, current_fp)

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
        """특정 DB 캐시를 삭제한다.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        success = False
        if self._backend == "redis" and await self.ensure_redis_connected():
            if await self._redis_cache.invalidate(db_id):
                success = True
        if self._file_cache.invalidate(db_id):
            success = True
        return success

    async def invalidate_all(self) -> int:
        """모든 캐시를 삭제한다.

        Returns:
            삭제된 항목 수
        """
        count = 0
        if self._backend == "redis" and await self.ensure_redis_connected():
            count += await self._redis_cache.invalidate_all()
        count += self._file_cache.invalidate_all()
        return count

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
