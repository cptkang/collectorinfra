"""plans/143 §4.5 · D-316 ④ — 설명 정본 파일(`column_descriptions.yaml`) 적재.

고정하는 계약:
  ① Redis에 없는 컬럼만 채운다 — 기존 설명(내부망 LLM·운영자 편집 등)은 덮지 않는다(HSETNX)
  ② 현재 스키마에 없는 컬럼은 건너뛴다 · `meta.description_status`는 그대로
  ③ 파일 없음(폴스타)·형식 오류 → 무동작(Redis 쓰기 0)
  ④ Redis 불가 → 무동작 + WARNING 1회 · 다시 연결되면 다음 스키마 로드에서 채운다
  ⑤ 프로세스당 DB별 1회 · Redis가 비고 파일 캐시 설명을 쓰는 중이면 가리지 않는다
  ⑥ 한 번 적재한 키는 DB별 집합(`knowledge_seeded`)에 남아 재기동 뒤에도 다시 채우지 않는다
     (운영자가 지운 설명 · 정본 파일 내용 변경 모두)

LLM·DB·네트워크 0 · Redis는 공용 인메모리 페이크 · 파일은 tmp.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.knowledge_descriptions import (
    FILE_NAME,
    load_knowledge_descriptions,
)
from tests.mocks.async_redis import FakeAsyncRedis, attach_fake_redis

DB_ID = "asset_db"
DESC_KEY = f"schema:{DB_ID}:descriptions"
SEEDED_KEY = f"schema:{DB_ID}:knowledge_seeded"
FILE_DESCRIPTIONS = {
    "items.id": "항목 식별자(파일)",
    "owners.name": "소유자 이름",
    "gone.col": "스키마에 없는 컬럼",
}


def _write(root: Path, data: object, db_id: str = DB_ID) -> Path:
    path = root / db_id / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else yaml.safe_dump(data, allow_unicode=True)
    path.write_text("# 설명 정본 파일 — 테스트\n" + text, encoding="utf-8")
    return path


def _file(descriptions: dict[str, str] = FILE_DESCRIPTIONS) -> dict:
    return {"version": 1, "origin": "claude_code", "descriptions": dict(descriptions)}


def _manager(
    tmp_path: Path, backend: str = "redis", fake: FakeAsyncRedis | None = None
) -> tuple[SchemaCacheManager, FakeAsyncRedis]:
    config = MagicMock()
    config.schema_cache.backend = backend
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    mgr._knowledge_root = tmp_path / "knowledge"
    fake = fake or FakeAsyncRedis()
    if backend == "redis":
        attach_fake_redis(mgr._redis_cache, fake)
    return mgr, fake


def _client() -> AsyncMock:
    """캐시 미스 수집 표면만 가진 DB 클라이언트(지문 조회 0행)."""
    client = AsyncMock()
    client.execute_sql = AsyncMock(return_value=SimpleNamespace(rows=[]))
    client.get_full_schema = AsyncMock(return_value=SchemaInfo(
        tables={
            "items": TableInfo(name="items", columns=[ColumnInfo(name="id", data_type="int")]),
            "owners": TableInfo(
                name="owners", columns=[ColumnInfo(name="name", data_type="varchar")]
            ),
        },
        relationships=[],
    ))
    return client


@pytest.fixture(autouse=True)
def _cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 프로필·시드 조회가 저장소 파일을 읽지 않게


async def test_empty_redis_fills_schema_columns_only(tmp_path):
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())

    _, cache_hit, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert cache_hit is False
    expected = {"items.id": "항목 식별자(파일)", "owners.name": "소유자 이름"}
    assert descriptions == expected                           # 스키마에 없는 컬럼 제외
    assert await fake.hgetall(DESC_KEY) == expected
    meta = await fake.hgetall(f"schema:{DB_ID}:meta")
    assert meta["description_status"] == "pending"            # 관리자 등록 상태로 바꾸지 않는다
    assert await mgr.get_descriptions(DB_ID) == expected       # 소비 코드 그대로 읽힌다


async def test_existing_redis_descriptions_are_kept(tmp_path):
    """내부망 기존 설명·운영자 편집(컬럼 설명에는 항목별 출처 칸이 없다)은 모두 기존 값으로 보존."""
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    await fake.hset(DESC_KEY, mapping={"items.id": "운영자가 고친 설명"})

    _, _, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert await fake.hgetall(DESC_KEY) == {
        "items.id": "운영자가 고친 설명", "owners.name": "소유자 이름",
    }
    assert descriptions["items.id"] == "운영자가 고친 설명"


async def test_hsetnx_never_overwrites_even_if_read_missed(tmp_path):
    """읽은 뒤 다른 경로가 먼저 쓴 값도 덮지 않는다(경합 · 읽기 실패 대비)."""
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    await fake.hset(DESC_KEY, mapping={"items.id": "관리자 적용 설명"})
    mgr._redis_cache.load_descriptions = AsyncMock(return_value={})  # 읽기 누락 흉내

    schema = {"tables": {"items": {"columns": [{"name": "id"}]},
                         "owners": {"columns": [{"name": "name"}]}}}
    out = await mgr._seed_knowledge_descriptions(DB_ID, schema, {})

    assert (await fake.hgetall(DESC_KEY))["items.id"] == "관리자 적용 설명"
    assert out == {"owners.name": "소유자 이름"}


async def test_missing_file_is_noop(tmp_path):
    mgr, fake = _manager(tmp_path)
    await fake.hset(DESC_KEY, mapping={"items.id": "기존"})
    calls: list[str] = []
    original = fake.hsetnx

    async def _spy(*args):
        calls.append(args[1])
        return await original(*args)

    fake.hsetnx = _spy  # type: ignore[method-assign]

    _, _, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert calls == []
    assert descriptions == {"items.id": "기존"}
    assert await fake.hgetall(DESC_KEY) == {"items.id": "기존"}
    assert DB_ID in mgr._knowledge_done


@pytest.mark.parametrize("content", [
    "version: 1\norigin: claude_code\ndescriptions: [a, b]\n",      # 매핑 아님
    "version: 2\norigin: claude_code\ndescriptions: {}\n",          # 버전 불일치
    "version: 1\norigin: llm\ndescriptions:\n  items.id: x\n",      # 출처 불일치
    "version: 1\ndescriptions: {items.id: [\n",                     # YAML 오류
])
async def test_bad_format_logs_and_does_nothing(tmp_path, caplog, content):
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, content)

    with caplog.at_level(logging.WARNING):
        _, _, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert descriptions == {}
    assert await fake.hgetall(DESC_KEY) == {}
    assert any("설명 정본 파일" in r.getMessage() for r in caplog.records)


async def test_redis_unavailable_noop_then_fills_after_reconnect(tmp_path, caplog):
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    schema = {"tables": {"items": {"columns": [{"name": "id"}]}}}
    mgr.ensure_redis_connected = AsyncMock(return_value=False)  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        assert await mgr._seed_knowledge_descriptions(DB_ID, schema, {}) == {}
        assert await mgr._seed_knowledge_descriptions(DB_ID, schema, {}) == {}

    assert await fake.hgetall(DESC_KEY) == {}
    warned = [r for r in caplog.records if "Redis 불가" in r.getMessage()]
    assert len(warned) == 1                                     # WARNING 1회
    assert DB_ID not in mgr._knowledge_done                     # 다음 로드에 다시

    mgr.ensure_redis_connected = AsyncMock(return_value=True)  # type: ignore[method-assign]
    out = await mgr._seed_knowledge_descriptions(DB_ID, schema, {})
    assert out == {"items.id": "항목 식별자(파일)"}


async def test_file_backend_is_noop(tmp_path):
    mgr, _ = _manager(tmp_path, backend="file")
    _write(mgr._knowledge_root, _file())
    schema = {"tables": {"items": {"columns": [{"name": "id"}]}}}

    assert await mgr._seed_knowledge_descriptions(DB_ID, schema, {"x.y": "z"}) == {"x.y": "z"}
    assert DB_ID in mgr._knowledge_done


async def test_once_per_process_and_memory_hit_path(tmp_path):
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    # 메모리 캐시 히트 경로에서도 첫 로드에 채운다
    mgr._memory_cache.set(
        {"tables": {"items": {"columns": [{"name": "id"}]}}, "relationships": []}, DB_ID
    )
    _, cache_hit, source, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)
    assert (cache_hit, source) == (True, "메모리")
    assert descriptions == {"items.id": "항목 식별자(파일)"}

    await fake.hdel(DESC_KEY, "items.id")                        # 이후 지워져도
    _, _, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)
    assert descriptions == {} and await fake.hgetall(DESC_KEY) == {}  # 다시 채우지 않는다


async def test_schema_columns_unknown_defers(tmp_path):
    """적용본 구조(`structure_meta`)처럼 컬럼이 없는 스키마면 Redis 스키마 캐시를 보고, 그것도
    없으면 다음 로드로 미룬다."""
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())

    assert await mgr._seed_knowledge_descriptions(DB_ID, {"patterns": []}, {}) == {}
    assert DB_ID not in mgr._knowledge_done and await fake.hgetall(DESC_KEY) == {}

    await mgr._redis_cache.save_schema(
        DB_ID, {"tables": {"owners": {"columns": [{"name": "name"}]}}, "relationships": []}, "fp"
    )
    out = await mgr._seed_knowledge_descriptions(DB_ID, {"patterns": []}, {})
    assert out == {"owners.name": "소유자 이름"}


async def test_file_cache_fallback_is_not_shadowed(tmp_path, caplog):
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    schema = {"tables": {"items": {"columns": [{"name": "id"}]}}}

    with caplog.at_level(logging.WARNING):
        out = await mgr._seed_knowledge_descriptions(DB_ID, schema, {"items.id": "파일 캐시"})

    assert out == {"items.id": "파일 캐시"}
    assert await fake.hgetall(DESC_KEY) == {}
    assert any("가리지 않음" in r.getMessage() for r in caplog.records)


def test_loader_skips_bad_items(tmp_path, caplog):
    _write(tmp_path, _file({"items.id": "좋음", "nodot": "키 형식 오류", "owners.name": " "}))

    with caplog.at_level(logging.WARNING):
        loaded = load_knowledge_descriptions(tmp_path, DB_ID)

    assert loaded == {"items.id": "좋음"}
    assert any("2건 건너뜀" in r.getMessage() for r in caplog.records)
    assert load_knowledge_descriptions(tmp_path, "other_db") is None


async def test_seeded_keys_are_recorded_and_not_refilled_after_restart(tmp_path):
    """⑥ 운영자가 지운 설명은 재기동(새 관리자 · 같은 Redis) 뒤에도 다시 채우지 않는다."""
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    await mgr.get_schema_or_fetch(_client(), DB_ID)
    assert await fake.smembers(SEEDED_KEY) == {"items.id", "owners.name"}

    await fake.hdel(DESC_KEY, "items.id")                        # 운영자 삭제
    restarted, _ = _manager(tmp_path, fake=fake)
    _, _, _, descriptions, _ = await restarted.get_schema_or_fetch(_client(), DB_ID)

    assert "items.id" not in await fake.hgetall(DESC_KEY)
    assert "items.id" not in descriptions
    assert descriptions["owners.name"] == "소유자 이름"


async def test_changed_file_does_not_refill_seeded_key(tmp_path):
    """⑥ 정본 파일 내용이 바뀌어도 이미 적재한 키는 다시 채우지 않는다 — 새 키만 채운다."""
    mgr, fake = _manager(tmp_path)
    schema = {"tables": {"items": {"columns": [{"name": "id"}]},
                         "owners": {"columns": [{"name": "name"}]}}}
    _write(mgr._knowledge_root, _file({"items.id": "처음 설명"}))
    assert await mgr._seed_knowledge_descriptions(DB_ID, schema, {}) == {"items.id": "처음 설명"}
    await fake.hdel(DESC_KEY, "items.id")

    _write(mgr._knowledge_root, _file({"items.id": "고친 설명", "owners.name": "새 설명"}))
    restarted, _ = _manager(tmp_path, fake=fake)
    out = await restarted._seed_knowledge_descriptions(DB_ID, schema, {})

    assert out == {"owners.name": "새 설명"}
    assert await fake.hgetall(DESC_KEY) == {"owners.name": "새 설명"}
    assert await fake.smembers(SEEDED_KEY) == {"items.id", "owners.name"}


async def test_existing_key_not_recorded_as_seeded(tmp_path):
    """기존 설명 때문에 채우지 않은 키는 적재 기록에 넣지 않는다(채운 키만 SADD)."""
    mgr, fake = _manager(tmp_path)
    _write(mgr._knowledge_root, _file())
    await fake.hset(DESC_KEY, mapping={"items.id": "운영자 설명"})

    await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert await fake.smembers(SEEDED_KEY) == {"owners.name"}
