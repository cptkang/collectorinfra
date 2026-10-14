"""plans/163 W1 D3·D4 — 스키마 캐시 무효화의 관리자 자산 보존 · 설명 정본 파일 재적재.

- D3: `invalidate_all()`은 D-294 관리자 자산 키(`asset_drafts`·`ddl_comments`)도 보존한다
  (`ADMIN_ASSET_SUFFIXES` — plans/104 §3.3 원칙 · D-294 ⑨).
- D4: `invalidate(db_id)`는 `descriptions`와 함께 적재 기록 집합 `knowledge_seeded`도 지운다 —
  남기면 지운 설명이 정본 파일에서 다시 채워지지 않는다(D-316 ④ · `invalidate_all`과 대칭).
  같은 프로세스에서도 무효화 뒤 다음 스키마 로드에 다시 채운다.

LLM·DB·네트워크 0 · Redis는 공용 인메모리 페이크 · 파일은 tmp.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.knowledge_descriptions import FILE_NAME
from tests.mocks.async_redis import FakeAsyncRedis, attach_fake_redis

DB_ID = "asset_db"
DESC_KEY = f"schema:{DB_ID}:descriptions"
SEEDED_KEY = f"schema:{DB_ID}:knowledge_seeded"
ASSET_DRAFTS_KEY = f"schema:{DB_ID}:asset_drafts"
DDL_COMMENTS_KEY = f"schema:{DB_ID}:ddl_comments"
FILE_DESCRIPTIONS = {"items.id": "항목 식별자(파일)", "owners.name": "소유자 이름"}


def _manager(
    tmp_path: Path, fake: FakeAsyncRedis | None = None
) -> tuple[SchemaCacheManager, FakeAsyncRedis]:
    config = MagicMock()
    config.schema_cache.backend = "redis"
    config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
    config.schema_cache.enabled = True
    mgr = SchemaCacheManager(config)
    mgr._knowledge_root = tmp_path / "knowledge"
    fake = fake or FakeAsyncRedis()
    attach_fake_redis(mgr._redis_cache, fake)
    return mgr, fake


def _write_knowledge(root: Path) -> None:
    path = root / DB_ID / FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "origin": "claude_code", "descriptions": dict(FILE_DESCRIPTIONS)}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


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


# === D3 관리자 자산 보존 ===


async def test_invalidate_all_preserves_d294_admin_assets(tmp_path):
    mgr, fake = _manager(tmp_path)
    store = mgr.structure_store
    await store.add_asset_draft(DB_ID, {"asset": "relationships", "items": []})
    await store.save_ddl_comments(DB_ID, {"items": "항목 테이블", "items.id": "식별자"})
    await mgr._redis_cache.save_schema(
        DB_ID, {"tables": {"items": {"columns": [{"name": "id"}]}}, "relationships": []}, "fp"
    )

    await mgr.invalidate_all()

    assert await fake.exists(ASSET_DRAFTS_KEY, DDL_COMMENTS_KEY) == 2
    assert [d["asset"] for d in await store.list_asset_drafts(DB_ID)] == ["relationships"]
    assert await store.load_ddl_comments(DB_ID) == {"items": "항목 테이블", "items.id": "식별자"}
    assert await fake.exists(f"schema:{DB_ID}:meta", f"schema:{DB_ID}:tables") == 0


async def test_invalidate_single_db_preserves_d294_admin_assets(tmp_path):
    mgr, fake = _manager(tmp_path)
    await mgr.structure_store.add_asset_draft(DB_ID, {"asset": "query_examples"})
    await mgr.structure_store.save_ddl_comments(DB_ID, {"items": "항목 테이블"})

    await mgr.invalidate(DB_ID)

    assert await fake.exists(ASSET_DRAFTS_KEY, DDL_COMMENTS_KEY) == 2


# === D4 설명 정본 파일 재적재 ===


async def test_invalidate_single_db_drops_seeded_set_with_descriptions(tmp_path):
    mgr, fake = _manager(tmp_path)
    _write_knowledge(mgr._knowledge_root)
    await mgr.get_schema_or_fetch(_client(), DB_ID)
    assert await fake.smembers(SEEDED_KEY) == set(FILE_DESCRIPTIONS)

    assert await mgr.invalidate(DB_ID) is True

    assert await fake.exists(DESC_KEY, SEEDED_KEY) == 0


async def test_descriptions_reseeded_after_invalidate_and_restart(tmp_path):
    """무효화 뒤 재기동(새 관리자 · 같은 Redis)하면 지식 설명이 다시 채워진다."""
    mgr, fake = _manager(tmp_path)
    _write_knowledge(mgr._knowledge_root)
    await mgr.get_schema_or_fetch(_client(), DB_ID)
    await mgr.invalidate(DB_ID)

    restarted, _ = _manager(tmp_path, fake=fake)
    _, cache_hit, _, descriptions, _ = await restarted.get_schema_or_fetch(_client(), DB_ID)

    assert cache_hit is False
    assert descriptions == FILE_DESCRIPTIONS
    assert await fake.hgetall(DESC_KEY) == FILE_DESCRIPTIONS
    assert await fake.smembers(SEEDED_KEY) == set(FILE_DESCRIPTIONS)


@pytest.mark.parametrize("invalidate_all", [False, True])
async def test_descriptions_reseeded_after_invalidate_same_process(tmp_path, invalidate_all):
    """같은 프로세스에서도 무효화 뒤 다음 스키마 로드에 다시 채운다(프로세스당 1회 표지 해제)."""
    mgr, fake = _manager(tmp_path)
    _write_knowledge(mgr._knowledge_root)
    await mgr.get_schema_or_fetch(_client(), DB_ID)
    assert DB_ID in mgr._knowledge_done

    if invalidate_all:
        await mgr.invalidate_all()
    else:
        await mgr.invalidate(DB_ID)
    assert DB_ID not in mgr._knowledge_done

    _, _, _, descriptions, _ = await mgr.get_schema_or_fetch(_client(), DB_ID)

    assert descriptions == FILE_DESCRIPTIONS
    assert await fake.hgetall(DESC_KEY) == FILE_DESCRIPTIONS


async def test_operator_deleted_description_still_not_refilled_without_invalidate(tmp_path):
    """무효화가 없으면 종전 계약 그대로 — 운영자가 지운 설명은 재기동 뒤에도 비어 있다."""
    mgr, fake = _manager(tmp_path)
    _write_knowledge(mgr._knowledge_root)
    await mgr.get_schema_or_fetch(_client(), DB_ID)
    await fake.hdel(DESC_KEY, "items.id")

    restarted, _ = _manager(tmp_path, fake=fake)
    _, _, _, descriptions, _ = await restarted.get_schema_or_fetch(_client(), DB_ID)

    assert "items.id" not in descriptions
    assert "items.id" not in await fake.hgetall(DESC_KEY)
