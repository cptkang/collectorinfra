"""유사어 메타데이터 추적·감쇠 정리 테스트 (Plan 61 트랙 B / E5-3 / D-075).

increment_synonym_usage(사용횟수·최종사용일·신뢰도)와 prune_stale_synonyms(장기
미사용 llm-발견 유사어 정리)를 governance ON/OFF 경계·operator 보존·레거시 보존
관점에서 검증한다. governance=OFF일 때 완전 no-op(기존 저장 구조 무변경)임을 단언한다.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.schema_cache.redis_cache import RedisSchemaCache


@pytest.fixture
def redis_config():
    config = MagicMock()
    config.host = "localhost"
    config.port = 6379
    config.db = 0
    config.password = ""
    config.ssl = False
    config.socket_timeout = 5
    return config


@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.ping = AsyncMock(return_value=True)
    return redis


@pytest.fixture
def cache(redis_config, mock_redis):
    c = RedisSchemaCache(redis_config)
    c._redis = mock_redis
    c._connected = True
    return c


def _governance(enabled: bool):
    """load_config().synonym.governance 를 강제하는 패치 컨텍스트."""
    cfg = MagicMock()
    cfg.synonym.governance = enabled
    return patch("src.config.load_config", return_value=cfg)


class TestIncrementSynonymUsage:
    @pytest.mark.asyncio
    async def test_off_is_noop(self, cache, mock_redis):
        """governance=OFF이면 완전 no-op(hset 미호출·False)."""
        with _governance(False):
            ok = await cache.increment_synonym_usage(
                "polestar", "servers.hostname", ["서버명"]
            )
        assert ok is False
        mock_redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_increments_and_stamps(self, cache, mock_redis):
        """governance=ON이면 등록 단어의 usage_count 증가 + last_used_ts 갱신."""
        entry = {"words": ["서버명"], "sources": {"서버명": "llm"}}
        mock_redis.hget = AsyncMock(return_value=json.dumps(entry))
        with _governance(True):
            ok = await cache.increment_synonym_usage(
                "polestar", "servers.hostname", ["서버명"],
                now=1_000_000.0, confidence=0.9,
            )
        assert ok is True
        saved = json.loads(mock_redis.hset.call_args[0][2])
        meta = saved["meta"]["서버명"]
        assert meta["usage_count"] == 1
        assert meta["last_used_ts"] == 1_000_000.0
        assert meta["confidence"] == 0.9
        # sources 하위호환 보존
        assert saved["sources"]["서버명"] == "llm"

    @pytest.mark.asyncio
    async def test_unregistered_word_not_tracked(self, cache, mock_redis):
        """컬럼에 등록되지 않은 단어는 추적하지 않는다(미변경 → False)."""
        entry = {"words": ["서버명"], "sources": {"서버명": "llm"}}
        mock_redis.hget = AsyncMock(return_value=json.dumps(entry))
        with _governance(True):
            ok = await cache.increment_synonym_usage(
                "polestar", "servers.hostname", ["미등록어"], now=1_000_000.0
            )
        assert ok is False
        mock_redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_increment_accumulates(self, cache, mock_redis):
        """기존 메타가 있으면 usage_count가 누적된다."""
        entry = {
            "words": ["서버명"],
            "sources": {"서버명": "llm"},
            "meta": {"서버명": {"usage_count": 4, "last_used_ts": 1.0, "confidence": 0.5}},
        }
        mock_redis.hget = AsyncMock(return_value=json.dumps(entry))
        with _governance(True):
            await cache.increment_synonym_usage(
                "polestar", "servers.hostname", ["서버명"], now=2_000_000.0
            )
        saved = json.loads(mock_redis.hset.call_args[0][2])
        assert saved["meta"]["서버명"]["usage_count"] == 5
        assert saved["meta"]["서버명"]["last_used_ts"] == 2_000_000.0
        # confidence 미지정 시 기존값 보존
        assert saved["meta"]["서버명"]["confidence"] == 0.5


class TestPruneStaleSynonyms:
    @pytest.mark.asyncio
    async def test_off_is_noop(self, cache, mock_redis):
        with _governance(False):
            report = await cache.prune_stale_synonyms(decay_days=30)
        assert report["removed_count"] == 0
        mock_redis.hset.assert_not_called()
        mock_redis.hdel.assert_not_called()

    @pytest.mark.asyncio
    async def test_removes_only_stale_llm_words(self, cache, mock_redis):
        """임계보다 오래된 llm 유사어만 제거; operator·경계값·레거시는 보존."""
        now = 10_000_000.0
        old_ts = now - 40 * 86400        # 40일 전 → 제거 대상(임계 30일)
        boundary_ts = now - 30 * 86400    # 정확히 경계 → 보존(strictly older만)
        entry = {
            "words": ["오래된llm", "운영자어", "경계어", "레거시어"],
            "sources": {
                "오래된llm": "llm",
                "운영자어": "operator",
                "경계어": "llm",
                # 레거시어: sources 없음
            },
            "meta": {
                "오래된llm": {"usage_count": 1, "last_used_ts": old_ts, "confidence": 0.5},
                "운영자어": {"usage_count": 1, "last_used_ts": old_ts, "confidence": 0.5},
                "경계어": {"usage_count": 1, "last_used_ts": boundary_ts, "confidence": 0.5},
                # 레거시어: meta 없음 → 보존
            },
        }
        mock_redis.hgetall = AsyncMock(return_value={"servers.hostname": json.dumps(entry)})
        with _governance(True):
            report = await cache.prune_stale_synonyms(
                decay_days=30, now=now, db_id="polestar"
            )
        assert report["removed_count"] == 1
        assert report["removed"][0]["words"] == ["오래된llm"]
        # hset로 갱신된 entry에서 오래된llm만 제거되고 나머지는 보존
        saved = json.loads(mock_redis.hset.call_args[0][2])
        assert set(saved["words"]) == {"운영자어", "경계어", "레거시어"}

    @pytest.mark.asyncio
    async def test_legacy_entry_without_meta_preserved(self, cache, mock_redis):
        """메타가 전혀 없는 레거시 컬럼은 정리 대상이 아니다(무변경)."""
        entry = {"words": ["서버명"], "sources": {"서버명": "llm"}}
        mock_redis.hgetall = AsyncMock(return_value={"servers.hostname": json.dumps(entry)})
        with _governance(True):
            report = await cache.prune_stale_synonyms(
                decay_days=1, now=10_000_000.0, db_id="polestar"
            )
        assert report["removed_count"] == 0
        mock_redis.hset.assert_not_called()
        mock_redis.hdel.assert_not_called()
