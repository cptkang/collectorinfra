"""`RedisSchemaCache`의 유사어 키 공간 저장소 (plans/170 I1-c — Composition).

`RedisSchemaCache`가 이 객체를 ``_synonyms`` 속성으로 보유하고, 같은 이름의 공개 메서드는
여기로 위임만 한다(호출부·테스트 무변경).

키 공간:
  schema:{db_id}:synonyms   -> Hash (table.column -> JSON)
  synonyms:global           -> Hash (column_name -> JSON)  ``GLOBAL_SYNONYMS_KEY``
  synonyms:resource_types   -> Hash  ``RESOURCE_TYPE_SYNONYMS_KEY``
  synonyms:eav_names        -> Hash  ``EAV_NAME_SYNONYMS_KEY``
  synonyms:column_values    -> Hash  ``COLUMN_VALUE_SYNONYMS_KEY``

seam 보존: 연결 객체(``_redis``)·연결 상태·키 상수·``_key``·``_governance_enabled``와 유사어
메서드끼리의 호출은 **호출 시점에 소유자를 거쳐** 읽는다 — 테스트가 소유자 인스턴스에 대입한
fakeredis·목과 ``patch.object(RedisSchemaCache, ...)``가 그대로 가로챈다. 순환 import를 피하려고
소유자 타입은 import하지 않는다.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

# 원 모듈 로거 이름 고정 — 로그 레코드 name·caplog 필터 불변(166 W2 선례).
logger = logging.getLogger("src.schema_cache.redis_cache")


class RedisSynonymStore:
    """유사어 키 공간(DB별·전역·RESOURCE_TYPE·EAV NAME·컬럼값) 저장소."""

    def __init__(self, owner: Any) -> None:
        """소유자(`RedisSchemaCache`) 참조를 보관한다. 소유자 상태는 생성 시 캡처하지 않는다."""
        self._owner = owner

    # === 유사어 거버넌스 메타 ===

    def _ensure_meta(self, entry: dict[str, Any], now: float) -> None:
        """유사어 항목(dict)에 word별 메타를 병기한다 (governance ON 전용).

        ``entry["words"]``의 각 단어에 메타가 없으면 생성하고, 현재 단어에
        해당하는 메타만 유지하여 삭제된 단어의 고아 메타 누적을 방지한다.
        메타 구조: ``{word: {usage_count:int, last_used_ts:float, confidence:float}}``.
        """
        words = entry.get("words", []) or []
        meta = entry.get("meta")
        if not isinstance(meta, dict):
            meta = {}
        for w in words:
            if not isinstance(meta.get(w), dict):
                meta[w] = {
                    "usage_count": 0,
                    "last_used_ts": float(now),
                    "confidence": 0.0,
                }
        # 현재 단어에 해당하는 메타만 유지 (고아 메타 제거 — 키 만료 sweep 일관성)
        entry["meta"] = {w: meta[w] for w in words if w in meta}

    # === 유사 단어 (DB별) ===

    async def save_synonyms(
        self,
        db_id: str,
        synonyms: dict[str, dict[str, Any] | list[str]],
        source: str = "llm",
    ) -> bool:
        """유사 단어를 Redis에 저장한다.

        synonyms 값은 두 가지 형태를 허용한다:
        - list[str]: 하위 호환용 (source 파라미터로 태깅)
        - dict {"words": [...], "sources": {...}}: source 태깅 포함

        list[str] 값을 ``source="llm"``으로 저장하면 컬럼별로 **병합**한다(plans/104 S5 · R10):
        기존 항목에서 출처가 ``llm``이 아닌 단어(``operator`` 등)와 그 태그를 보존하고,
        ``llm`` 단어만 새 목록으로 교체한다. 같은 단어가 양쪽에 있으면 기존 비-llm 태그가 이긴다.
        dict 값이거나 ``source``가 ``llm``이 아니면 종전대로 호출자가 준 항목으로 치환한다.

        Args:
            db_id: DB 식별자
            synonyms: {table.column: words_or_dict} 매핑
            source: 기본 source 태그 ("llm" | "operator")

        Returns:
            저장 성공 여부
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            key = owner._key(db_id, "synonyms")
            # governance ON일 때만 word별 메타를 병기한다 (OFF 시 바이트 무변경).
            governance = owner._governance_enabled()
            now = time.time() if governance else None
            merge_llm = source == "llm" and any(
                not (isinstance(v, dict) and "words" in v) for v in synonyms.values()
            )
            existing_raw: Any = (
                await owner._redis.hgetall(key) if merge_llm else {}
            )
            if not isinstance(existing_raw, dict):
                existing_raw = {}
            mapping = {}
            for col, value in synonyms.items():
                if isinstance(value, dict) and "words" in value:
                    # 이미 source 태깅된 형태
                    entry = value
                    if governance:
                        entry = dict(value)
                        owner._ensure_meta(entry, now)
                    mapping[col] = json.dumps(entry, ensure_ascii=False)
                else:
                    # list[str] 형태 -> source 태깅 변환
                    words = value if isinstance(value, list) else list(value)
                    if merge_llm:
                        tagged = owner._merge_llm_words(
                            existing_raw.get(col), words, keep_meta=governance
                        )
                    else:
                        tagged = {
                            "words": words,
                            "sources": {w: source for w in words},
                        }
                    if governance:
                        owner._ensure_meta(tagged, now)
                    mapping[col] = json.dumps(tagged, ensure_ascii=False)
            if mapping:
                await owner._redis.hset(key, mapping=mapping)
            logger.info(
                "Redis 유사 단어 저장: db_id=%s, columns=%d",
                db_id,
                len(synonyms),
            )
            return True
        except Exception as e:
            logger.error("Redis 유사 단어 저장 실패: %s", e)
            return False

    @staticmethod
    def _merge_llm_words(
        existing_raw: Any, words: list[str], *, keep_meta: bool
    ) -> dict[str, Any]:
        """기존 컬럼 항목의 비-llm 단어를 보존하고 llm 단어만 `words`로 교체한 항목을 만든다.

        기존 항목이 없거나 레거시 list·파싱 불가 형태면(전부 llm 취급) 종전 치환과 같은
        ``{"words": words, "sources": {w: "llm"}}``를 돌려준다. 보존 단어의 governance 메타는
        ``keep_meta``(governance ON)일 때만 이어 붙인다(OFF 시 메타 키를 쓰지 않는 종전 동작 유지).
        """
        preserved_words: list[str] = []
        preserved_sources: dict[str, str] = {}
        preserved_meta: dict[str, Any] = {}
        if isinstance(existing_raw, str):
            try:
                parsed = json.loads(existing_raw)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and isinstance(parsed.get("words"), list):
                sources = parsed.get("sources")
                sources = sources if isinstance(sources, dict) else {}
                meta = parsed.get("meta")
                meta = meta if isinstance(meta, dict) else {}
                for w in parsed["words"]:
                    tag = sources.get(w, "llm")
                    if tag != "llm" and w not in preserved_sources:
                        preserved_words.append(w)
                        preserved_sources[w] = tag
                        if w in meta:
                            preserved_meta[w] = meta[w]

        if not preserved_words:
            return {"words": words, "sources": {w: "llm" for w in words}}

        new_words = [w for w in words if w not in preserved_sources]
        merged: dict[str, Any] = {
            "words": preserved_words + new_words,
            "sources": {**preserved_sources, **{w: "llm" for w in new_words}},
        }
        if preserved_meta and keep_meta:
            merged["meta"] = preserved_meta
        return merged

    async def load_synonyms(self, db_id: str) -> dict[str, list[str]]:
        """Redis에서 유사 단어를 로드한다 (단어 목록만 반환).

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: [synonym1, ...]} 매핑
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner._key(db_id, "synonyms"))
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
    ) -> dict[str, dict[str, Any]]:
        """Redis에서 유사 단어를 source 태그 포함하여 로드한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: {"words": [...], "sources": {...}}} 매핑
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner._key(db_id, "synonyms"))
            result: dict[str, dict[str, Any]] = {}
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            key = owner._key(db_id, "synonyms")
            existing_raw = await owner._redis.hget(key, column)

            existing_meta: dict[str, Any] = {}
            if existing_raw:
                parsed = json.loads(existing_raw)
                if isinstance(parsed, dict) and "words" in parsed:
                    existing_words = parsed["words"]
                    existing_sources = parsed.get("sources", {})
                    if isinstance(parsed.get("meta"), dict):
                        existing_meta = dict(parsed["meta"])
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
            # governance ON일 때만 메타를 병기한다 (OFF 시 바이트 무변경).
            if owner._governance_enabled():
                tagged["meta"] = existing_meta
                owner._ensure_meta(tagged, time.time())
            await owner._redis.hset(
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            key = owner._key(db_id, "synonyms")
            existing_raw = await owner._redis.hget(key, column)
            if not existing_raw:
                return True

            parsed = json.loads(existing_raw)
            existing_meta: dict[str, Any] | None = None
            if isinstance(parsed, dict) and "words" in parsed:
                existing_words = parsed["words"]
                existing_sources = parsed.get("sources", {})
                if isinstance(parsed.get("meta"), dict):
                    existing_meta = parsed["meta"]
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
                # 메타가 있으면 잔존 단어분만 유지 (고아 메타 제거).
                if existing_meta is not None:
                    tagged["meta"] = {
                        w: existing_meta[w]
                        for w in updated_words
                        if w in existing_meta
                    }
                await owner._redis.hset(
                    key, column, json.dumps(tagged, ensure_ascii=False)
                )
            else:
                await owner._redis.hdel(key, column)
            return True
        except Exception as e:
            logger.error("Redis 유사 단어 삭제 실패: %s", e)
            return False

    # === 유사어 메타데이터 (E5-3 사전 위생·거버넌스 / D-075) ===

    async def load_synonym_meta(
        self, db_id: str
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """DB별 유사어 메타(사용횟수·최종사용일·신뢰도·출처)를 로드한다.

        메타 없는(레거시) 컬럼/단어는 제외된다. source는 sources 태그에서 병합한다.

        Args:
            db_id: DB 식별자

        Returns:
            {table.column: {word: {usage_count, last_used_ts, confidence, source}}}
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner._key(db_id, "synonyms"))
            result: dict[str, dict[str, dict[str, Any]]] = {}
            for col, data in raw.items():
                parsed = json.loads(data)
                if not isinstance(parsed, dict):
                    continue
                meta = parsed.get("meta")
                if not isinstance(meta, dict) or not meta:
                    continue
                sources = parsed.get("sources")
                sources = sources if isinstance(sources, dict) else {}
                col_meta: dict[str, dict[str, Any]] = {}
                for word, m in meta.items():
                    if not isinstance(m, dict):
                        continue
                    col_meta[word] = {
                        "usage_count": int(m.get("usage_count") or 0),
                        "last_used_ts": m.get("last_used_ts"),
                        "confidence": float(m.get("confidence") or 0.0),
                        "source": sources.get(word, "llm"),
                    }
                if col_meta:
                    result[col] = col_meta
            return result
        except Exception as e:
            logger.warning("Redis 유사어 메타 로드 실패: %s", e)
            return {}

    async def increment_synonym_usage(
        self,
        db_id: str,
        column: str,
        words: list[str],
        *,
        now: float | None = None,
        confidence: float | None = None,
    ) -> bool:
        """유사어 사용횟수를 증가시키고 최종 사용시각을 갱신한다.

        governance=False이면 완전 no-op(False 반환). 컬럼에 등록된 단어에 한해
        메타를 생성/증가하며, confidence가 주어지면 해당 값으로 갱신한다.

        Args:
            db_id: DB 식별자
            column: "table.column" 형식
            words: 사용된 유사어 단어 목록
            now: 기준 시각(epoch초). 미지정 시 현재 시각.
            confidence: 갱신할 신뢰도(0.0~1.0). 미지정 시 기존값 보존.

        Returns:
            갱신 성공 여부 (no-op·미변경 시 False)
        """
        owner = self._owner
        if not owner._governance_enabled():
            return False
        if not owner._connected or owner._redis is None:
            return False
        if not words:
            return False

        try:
            key = owner._key(db_id, "synonyms")
            existing_raw = await owner._redis.hget(key, column)
            if not existing_raw:
                return False

            parsed = json.loads(existing_raw)
            if isinstance(parsed, dict) and "words" in parsed:
                entry = parsed
            elif isinstance(parsed, list):
                entry = {"words": parsed, "sources": {w: "llm" for w in parsed}}
            else:
                return False

            entry_words = entry.get("words", []) or []
            meta = entry.get("meta")
            if not isinstance(meta, dict):
                meta = {}
            ts = float(now) if now is not None else time.time()

            touched = False
            for w in words:
                if w not in entry_words:
                    continue  # 등록되지 않은 단어는 추적하지 않음
                m = meta.get(w)
                if not isinstance(m, dict):
                    m = {"usage_count": 0, "last_used_ts": ts, "confidence": 0.0}
                m["usage_count"] = int(m.get("usage_count") or 0) + 1
                m["last_used_ts"] = ts
                if confidence is not None:
                    m["confidence"] = float(confidence)
                meta[w] = m
                touched = True

            if not touched:
                return False

            # 현재 단어분 메타만 유지 (고아 메타 제거)
            entry["meta"] = {w: meta[w] for w in entry_words if w in meta}
            await owner._redis.hset(
                key, column, json.dumps(entry, ensure_ascii=False)
            )
            return True
        except Exception as e:
            logger.error("Redis 유사어 사용 추적 실패: %s", e)
            return False

    async def prune_stale_synonyms(
        self,
        *,
        decay_days: int,
        now: float | None = None,
        db_id: str | None = None,
    ) -> dict[str, Any]:
        """장기 미사용 llm-발견 유사어를 정리한다 (E5-3 감쇠/정리).

        규칙:
        - last_used_ts가 (now - decay_days*86400)보다 **오래된**(strictly older)
          유사어만 제거 대상 (정확히 임계 경계값은 보존).
        - source가 "operator"인 유사어는 보존(운영자 등록).
        - 메타가 없는(레거시) 컬럼/단어는 보존(정리 대상 아님).

        governance=False이면 완전 no-op. 명시적 호출 시에만 동작한다
        (자동 스케줄 배선은 범위 밖). db_id 미지정 시 전체 DB synonyms를 스캔한다.

        Args:
            decay_days: 감쇠 임계(일)
            now: 기준 시각(epoch초). 미지정 시 현재 시각.
            db_id: 특정 DB만 정리. None이면 schema:*:synonyms 전체 스캔.

        Returns:
            {"removed": [{"db_id", "column", "words"}], "removed_count": int,
             "columns_pruned": int}
        """
        owner = self._owner
        empty = {"removed": [], "removed_count": 0, "columns_pruned": 0}
        if not owner._governance_enabled():
            return empty
        if not owner._connected or owner._redis is None:
            return empty

        ts = float(now) if now is not None else time.time()
        threshold = ts - float(decay_days) * 86400.0

        try:
            if db_id is not None:
                keys = [owner._key(db_id, "synonyms")]
            else:
                keys = []
                async for k in owner._redis.scan_iter(match="schema:*:synonyms"):
                    keys.append(k)

            removed: list[dict[str, Any]] = []
            removed_count = 0
            columns_pruned = 0

            for key in keys:
                key_db_id = owner._db_id_from_key(key)
                raw = await owner._redis.hgetall(key)
                for col, data in (raw or {}).items():
                    parsed = json.loads(data)
                    if not isinstance(parsed, dict) or "words" not in parsed:
                        continue
                    meta = parsed.get("meta")
                    if not isinstance(meta, dict) or not meta:
                        continue  # 메타 없는 레거시 컬럼 전체 보존
                    sources = parsed.get("sources")
                    sources = sources if isinstance(sources, dict) else {}
                    words = parsed.get("words", []) or []

                    to_remove: list[str] = []
                    for w in words:
                        m = meta.get(w)
                        if not isinstance(m, dict):
                            continue  # 메타 없는 단어 보존(레거시)
                        last = m.get("last_used_ts")
                        if last is None:
                            continue  # 타임스탬프 없으면 보존
                        if sources.get(w, "llm") == "operator":
                            continue  # 운영자 등록 보존
                        if float(last) < threshold:
                            to_remove.append(w)

                    if not to_remove:
                        continue

                    remove_set = set(to_remove)
                    updated_words = [w for w in words if w not in remove_set]
                    updated_sources = {
                        k: v for k, v in sources.items() if k not in remove_set
                    }
                    updated_meta = {
                        w: meta[w] for w in updated_words if w in meta
                    }

                    if updated_words:
                        entry = {
                            "words": updated_words,
                            "sources": updated_sources,
                            "meta": updated_meta,
                        }
                        await owner._redis.hset(
                            key, col, json.dumps(entry, ensure_ascii=False)
                        )
                    else:
                        await owner._redis.hdel(key, col)

                    removed.append(
                        {"db_id": key_db_id, "column": col, "words": to_remove}
                    )
                    removed_count += len(to_remove)
                    columns_pruned += 1

            return {
                "removed": removed,
                "removed_count": removed_count,
                "columns_pruned": columns_pruned,
            }
        except Exception as e:
            logger.error("Redis 유사어 정리 실패: %s", e)
            return empty

    # === 글로벌 유사단어 사전 ===

    async def save_global_synonyms(
        self,
        synonyms: dict[str, list[str] | dict[str, Any]],
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
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
                await owner._redis.hset(owner.GLOBAL_SYNONYMS_KEY, mapping=mapping)
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner.GLOBAL_SYNONYMS_KEY)
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

    async def load_global_synonyms_full(self) -> dict[str, dict[str, Any]]:
        """글로벌 유사단어 사전을 description 포함하여 로드한다.

        Returns:
            {column_name: {"words": [...], "description": "..."}} 매핑
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner.GLOBAL_SYNONYMS_KEY)
            result: dict[str, dict[str, Any]] = {}
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            existing_raw = await owner._redis.hget(
                owner.GLOBAL_SYNONYMS_KEY, column_name
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
            entry: dict[str, Any] = {"words": merged}
            if description:
                entry["description"] = description
            await owner._redis.hset(
                owner.GLOBAL_SYNONYMS_KEY,
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            existing_raw = await owner._redis.hget(
                owner.GLOBAL_SYNONYMS_KEY, column_name
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
            await owner._redis.hset(
                owner.GLOBAL_SYNONYMS_KEY,
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
    ) -> str | None:
        """글로벌 사전에서 컬럼 설명을 조회한다.

        Args:
            column_name: 컬럼명 (bare name)

        Returns:
            설명 문자열 또는 None
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return None

        try:
            raw = await owner._redis.hget(
                owner.GLOBAL_SYNONYMS_KEY, column_name
            )
            if not raw:
                return None
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed.get("description")
            return None
        except Exception:
            logger.warning(
                "글로벌 사전 컬럼 설명 조회 실패 (column=%s)", column_name, exc_info=True
            )
            return None

    async def list_global_column_names(self) -> list[str]:
        """글로벌 사전에 등록된 전체 컬럼명 목록을 반환한다.

        Returns:
            컬럼명 목록 (정렬)
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return []

        try:
            keys = await owner._redis.hkeys(owner.GLOBAL_SYNONYMS_KEY)
            return sorted(keys)
        except Exception:
            logger.warning("글로벌 사전 컬럼명 목록 조회 실패", exc_info=True)
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            existing_raw = await owner._redis.hget(
                owner.GLOBAL_SYNONYMS_KEY, column_name
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
                entry: dict[str, Any] = {"words": updated}
                if description:
                    entry["description"] = description
                await owner._redis.hset(
                    owner.GLOBAL_SYNONYMS_KEY,
                    column_name,
                    json.dumps(entry, ensure_ascii=False),
                )
            else:
                await owner._redis.hdel(owner.GLOBAL_SYNONYMS_KEY, column_name)
            return True
        except Exception as e:
            logger.error("Redis 글로벌 유사단어 삭제 실패: %s", e)
            return False

    # === 명시 삭제 ===

    async def delete_synonyms(self, db_id: str) -> bool:
        """특정 DB의 유사단어를 명시적으로 삭제한다.

        운영자가 명시적으로 삭제 명령을 실행한 경우에만 사용.

        Args:
            db_id: DB 식별자

        Returns:
            삭제 성공 여부
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            await owner._redis.delete(owner._key(db_id, "synonyms"))
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            await owner._redis.delete(owner.GLOBAL_SYNONYMS_KEY)
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            mapping = {}
            for rt_value, words in synonyms.items():
                mapping[rt_value] = json.dumps(words, ensure_ascii=False)
            if mapping:
                await owner._redis.hset(
                    owner.RESOURCE_TYPE_SYNONYMS_KEY, mapping=mapping
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner.RESOURCE_TYPE_SYNONYMS_KEY)
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            mapping = {}
            for eav_name, words in synonyms.items():
                mapping[eav_name] = json.dumps(words, ensure_ascii=False)
            if mapping:
                await owner._redis.hset(
                    owner.EAV_NAME_SYNONYMS_KEY, mapping=mapping
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner.EAV_NAME_SYNONYMS_KEY)
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

    async def save_column_value_synonyms(
        self,
        synonyms: dict[str, dict[str, dict[str, Any]]],
    ) -> bool:
        """컬럼 값 유사단어를 Redis에 저장한다.

        Redis Hash synonyms:column_values에 field=컬럼명(대문자), value=JSON object로 저장.

        Args:
            synonyms: {column_name: {term: {op, value}}} 매핑
                      예: {"AVAIL_STATUS": {"비정상": {"op": "!=", "value": 0}}}

        Returns:
            저장 성공 여부
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return False

        try:
            mapping = {}
            for col_name, value_map in synonyms.items():
                mapping[col_name.upper()] = json.dumps(value_map, ensure_ascii=False)
            if mapping:
                await owner._redis.hset(owner.COLUMN_VALUE_SYNONYMS_KEY, mapping=mapping)
            logger.info(
                "Redis 컬럼값 유사단어 저장: count=%d", len(synonyms)
            )
            return True
        except Exception as e:
            logger.error("Redis 컬럼값 유사단어 저장 실패: %s", e)
            return False

    async def load_column_value_synonyms(self) -> dict[str, dict[str, dict[str, Any]]]:
        """컬럼 값 유사단어를 Redis에서 로드한다.

        Returns:
            {column_name: {term: {op, value}}} 매핑
        """
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return {}

        try:
            raw = await owner._redis.hgetall(owner.COLUMN_VALUE_SYNONYMS_KEY)
            result: dict[str, dict[str, dict[str, Any]]] = {}
            for col_name, data in raw.items():
                parsed = json.loads(data)
                if isinstance(parsed, dict):
                    result[col_name] = parsed
            return result
        except Exception as e:
            logger.warning("Redis 컬럼값 유사단어 로드 실패: %s", e)
            return {}

    # === known_attributes → EAV NAME 유사단어 동기화 ===

    async def sync_known_attributes_to_eav_synonyms(
        self,
        known_attributes_detail: list[dict[str, Any]],
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
        owner = self._owner
        if not owner._connected or owner._redis is None:
            return 0

        if not known_attributes_detail:
            return 0

        try:
            existing = await owner.load_eav_name_synonyms()

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
                await owner.save_eav_name_synonyms(existing)
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
