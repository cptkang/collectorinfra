"""plans/104 §3.2 잡 러너 — 소스당 동시 1개 락 · 성공/실패 기록 · 재기동 뒤 interrupted.

Redis는 인메모리 페이크. 잡 본문은 테스트 코루틴(LLM·DB 0).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.schema_cache import admin_jobs
from src.schema_cache.admin_jobs import (
    BOOT_ID,
    JOB_TTL_SECONDS,
    LOCK_TTL_SECONDS,
    AdminJobRunner,
    JobConflictError,
    JobContext,
    get_job_runner,
)
from src.schema_cache.cache_manager import SchemaCacheManager
from src.schema_cache.redis_cache import RedisSchemaCache
from src.schema_cache.structure_store import StructureStoreUnavailable
from tests.mocks.async_redis import attach_fake_redis


@pytest.fixture
def runner():
    cache = RedisSchemaCache(MagicMock())
    fake = attach_fake_redis(cache)
    return AdminJobRunner(cache), fake


async def _drain() -> None:
    """러너가 띄운 태스크가 모두 끝날 때까지 기다린다."""
    tasks = list(admin_jobs._RUNNING_TASKS)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _lock_key(db_id: str) -> str:
    return f"admin:db_structure:lock:{db_id}"


class TestLifecycle:
    async def test_success_records_result_and_releases_lock(self, runner):
        jr, fake = runner

        async def work(ctx: JobContext) -> dict:
            await ctx.progress(2, 2, "완료")
            return {"tables": 3}

        started = await jr.start(
            kind="check", db_id="db1", by="admin", params={"scope": "all"}, work=work
        )

        assert started["status"] == "running"
        assert started["boot_id"] == BOOT_ID
        assert started["progress"] == {"done": 0, "total": 0, "label": ""}
        await _drain()

        record = await jr.get(started["job_id"])
        assert record is not None
        assert record["status"] == "succeeded"
        assert record["result"] == {"tables": 3}
        assert record["error"] is None and record["finished_at"]
        assert record["progress"] == {"done": 2, "total": 2, "label": "완료"}
        assert record["params"] == {"scope": "all"} and record["by"] == "admin"
        assert await fake.exists(_lock_key("db1")) == 0
        assert 0 < await fake.ttl(f"admin:db_structure:job:{started['job_id']}") <= JOB_TTL_SECONDS

    async def test_failure_records_error_and_releases_lock(self, runner):
        jr, fake = runner

        async def work(ctx: JobContext) -> dict:
            raise RuntimeError("MCP 연결 실패")

        started = await jr.start(kind="analyze", db_id="db1", by=None, params={}, work=work)
        await _drain()

        record = await jr.get(started["job_id"])
        assert record["status"] == "failed"
        assert "MCP 연결 실패" in record["error"]
        assert record["result"] is None
        assert await fake.exists(_lock_key("db1")) == 0

    async def test_unserializable_result_is_failure_not_silent(self, runner):
        jr, _ = runner

        async def work(ctx: JobContext) -> dict:
            return {"bad": object()}

        started = await jr.start(kind="check", db_id="db1", by=None, params={}, work=work)
        await _drain()
        record = await jr.get(started["job_id"])
        assert record["status"] == "failed"
        assert "직렬화" in record["error"]

    async def test_progress_visible_while_running(self, runner):
        jr, _ = runner
        gate = asyncio.Event()

        async def work(ctx: JobContext) -> dict:
            await ctx.progress(1, 4, "테이블 1/4")
            await gate.wait()
            return {}

        started = await jr.start(kind="register", db_id="db1", by=None, params={}, work=work)
        for _ in range(50):
            record = await jr.get(started["job_id"])
            if record["progress"]["done"]:
                break
            await asyncio.sleep(0)
        assert record["status"] == "running"
        assert record["progress"] == {"done": 1, "total": 4, "label": "테이블 1/4"}
        gate.set()
        await _drain()

    async def test_unknown_job_is_none(self, runner):
        jr, _ = runner
        assert await jr.get("없는잡") is None


class TestLock:
    async def test_same_db_conflicts_other_db_runs(self, runner):
        jr, fake = runner
        gate = asyncio.Event()

        async def blocked(ctx: JobContext) -> dict:
            await gate.wait()
            return {}

        first = await jr.start(kind="check", db_id="db1", by=None, params={}, work=blocked)
        with pytest.raises(JobConflictError) as exc:
            await jr.start(kind="analyze", db_id="db1", by=None, params={}, work=blocked)
        assert first["job_id"] in str(exc.value)
        assert 0 < await fake.ttl(_lock_key("db1")) <= LOCK_TTL_SECONDS

        other = await jr.start(kind="check", db_id="db2", by=None, params={}, work=blocked)
        assert other["status"] == "running"

        gate.set()
        await _drain()
        again = await jr.start(
            kind="check", db_id="db1", by=None, params={}, work=AsyncMock(return_value={})
        )
        assert again["status"] == "running"
        await _drain()

    async def test_stale_lock_from_previous_process_is_reclaimed(self, runner):
        jr, fake = runner
        stale = {"job_id": "oldjob", "kind": "check", "db_id": "db1", "status": "running",
                 "boot_id": "old-boot", "started_at": "t", "finished_at": None, "params": {},
                 "by": None, "progress": {"done": 0, "total": 0, "label": ""},
                 "result": None, "error": None}
        await jr.redis_cache.set_json("admin:db_structure:job:oldjob", stale)
        await jr.redis_cache.set_json(
            _lock_key("db1"), {"job_id": "oldjob", "boot_id": "old-boot"}, ex=LOCK_TTL_SECONDS
        )

        started = await jr.start(
            kind="check", db_id="db1", by=None, params={}, work=AsyncMock(return_value={})
        )

        assert started["status"] == "running"
        old = await jr.get("oldjob")
        assert old["status"] == "interrupted"
        await _drain()


class TestInterrupted:
    async def test_running_record_from_other_boot_is_interrupted(self, runner):
        jr, fake = runner
        record = {"job_id": "j1", "kind": "analyze", "db_id": "db1", "status": "running",
                  "boot_id": "old-boot", "started_at": "t", "finished_at": None, "params": {},
                  "by": "admin", "progress": {"done": 1, "total": 3, "label": ""}, "result": None,
                  "error": None}
        await jr.redis_cache.set_json("admin:db_structure:job:j1", record)
        await jr.redis_cache.set_json(_lock_key("db1"), {"job_id": "j1", "boot_id": "old-boot"})

        got = await jr.get("j1")

        assert got["status"] == "interrupted"
        assert got["error"] and got["finished_at"]
        stored = await jr.redis_cache.get_json("admin:db_structure:job:j1")
        assert stored["status"] == "interrupted"
        assert await fake.exists(_lock_key("db1")) == 0

    async def test_same_boot_running_stays_running(self, runner):
        jr, _ = runner
        record = {"job_id": "j2", "db_id": "db1", "status": "running", "boot_id": BOOT_ID}
        await jr.redis_cache.set_json("admin:db_structure:job:j2", record)
        assert (await jr.get("j2"))["status"] == "running"


class TestGuards:
    async def test_no_redis_raises_unavailable(self):
        jr = AdminJobRunner(None)
        with pytest.raises(StructureStoreUnavailable):
            await jr.start(kind="check", db_id="db1", by=None, params={}, work=AsyncMock())
        with pytest.raises(StructureStoreUnavailable):
            await jr.get("j")

    async def test_invalid_db_id_rejected(self, runner):
        jr, fake = runner
        with pytest.raises(ValueError):
            await jr.start(kind="check", db_id="../x", by=None, params={}, work=AsyncMock())
        assert fake.dump() == {}

    def test_get_job_runner_singleton_follows_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(admin_jobs, "_runner", None)
        config = MagicMock()
        config.schema_cache.backend = "redis"
        config.schema_cache.cache_dir = str(tmp_path / ".cache" / "schema")
        mgr = SchemaCacheManager(config)

        first = get_job_runner(mgr)
        assert get_job_runner(mgr) is first
        assert first.redis_cache is mgr._redis_cache

        mgr._redis_cache = RedisSchemaCache(MagicMock())
        assert get_job_runner(mgr) is not first
