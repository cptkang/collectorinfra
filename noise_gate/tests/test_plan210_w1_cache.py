"""plans/210 W1 — 연계 매핑 Redis 사본·SMS 색인·되적재 스트림·inflight.

Redis는 `tests.mocks.async_redis.FakeAsyncRedis`에 이 테스트가 쓰는 `hmget`·`xadd`만 덧붙인
하위 클래스다(공용 페이크는 고치지 않는다).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.domain import apm_host_map as m
from noise_gate.infrastructure import apm_host_map_cache as c

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6-x7k2p"
WORKLOAD = "bfm-kbland-admin-price"


class _Redis(FakeAsyncRedis):
    def __init__(self) -> None:
        super().__init__()
        self.streams: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}

    async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
        h = self._hash(name) or {}
        return [h.get(k) for k in [*keys, *args]]

    async def xadd(self, name: str, fields: dict[str, Any], **kw: Any) -> str:
        entries = self.streams.setdefault(name, [])
        entries.append((dict(fields), kw))
        return f"{len(entries)}-0"


def _row(scope: str, key: str, *, rid: int, origin: str = "auto", state: str = "active",
         source: str = "jen", servers: tuple[str, ...] = ("node01",)) -> dict[str, Any]:
    return {
        "id": rid, "source_id": source, "domain_id": "7", "scope": scope, "match_key": key,
        "origin": origin, "basis": "node_address", "state": state, "verified": False,
        "targets": [m.make_target(db_id="pg_a", server_name=s) for s in servers],
        "evidence": {"stage": "x"}, "version": 2,
    }


async def test_put_then_lookup_reads_three_fields_once() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    assert await cache.put(_row("instance", POD, rid=1))
    assert await cache.put(_row("workload", WORKLOAD, rid=2, servers=("n1", "n2")))
    assert await cache.put(_row("domain", "", rid=3, origin="manual"))
    calls: list[Any] = []
    original = redis.hmget

    async def _spy(name: str, keys: list[str], *args: str) -> list[str | None]:
        calls.append((name, list(keys)))
        return await original(name, keys, *args)

    redis.hmget = _spy  # type: ignore[method-assign]
    got = await cache.lookup("jen", "7", POD)
    assert got.error is None
    assert calls == [("apmhostmap:jen", [f"instance|7|{POD}", f"workload|7|{WORKLOAD}",
                                         "domain|7|"])]
    assert set(got.records) == {"instance", "workload", "domain"}
    assert got.records["instance"] == m.cache_value(_row("instance", POD, rid=1))
    d = m.decide_from_map(got.records)
    assert d.stage == m.STAGE_MANUAL and d.pick is not None and d.pick.scope == "domain"


async def test_lookup_vm_and_missing_values() -> None:
    cache = c.RedisApmHostMapCache(_Redis())
    got = await cache.lookup("jen", "7", "web01")
    assert got.records == {} and got.error is None


async def test_lookup_redis_error_is_reported_not_swallowed(caplog: pytest.LogCaptureFixture
                                                            ) -> None:
    redis = _Redis()
    redis.hmget = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    cache = c.RedisApmHostMapCache(redis)
    with caplog.at_level(logging.WARNING):
        got = await cache.lookup("jen", "7", POD)
    assert got.error == c.ERROR_REDIS and got.records == {}
    assert "사본 조회 실패" in caplog.text


async def test_lookup_invalid_key_and_corrupt_value() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    assert (await cache.lookup("smsip", "7", POD)).error == c.ERROR_INVALID_KEY
    assert (await cache.lookup("a:b", "7", POD)).error == c.ERROR_INVALID_KEY
    await redis.hset("apmhostmap:jen", f"instance|7|{POD}", "{not json")
    got = await cache.lookup("jen", "7", POD)
    assert got.records == {} and got.error is None


async def test_remove_and_put_failure_returns_false() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    row = _row("instance", POD, rid=1)
    await cache.put(row)
    assert await cache.remove(row)
    assert (await cache.lookup("jen", "7", POD)).records == {}
    assert not await cache.put({**row, "source_id": "bad:id"})


async def test_rebuild_replaces_fields_and_drops_stale_sources() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    await cache.put(_row("instance", "old-pod", rid=9))
    await cache.put(_row("instance", "x", rid=8, source="gone"))
    await redis.hset(c.SMS_IP_KEY, "192.0.2.1", "[]")
    await redis.set(c.inflight_key("jen", "7"), "1")
    n = await cache.rebuild([
        _row("instance", POD, rid=1),
        _row("domain", "", rid=2, source="jen2"),
        {**_row("instance", "y", rid=3), "source_id": "bad:id"},  # 규칙 밖 → 건너뜀
    ])
    assert n == 2
    dump = redis.dump()
    assert set(dump["apmhostmap:jen"]) == {f"instance|7|{POD}"}
    assert "apmhostmap:gone" not in dump and "apmhostmap:jen2" in dump
    assert c.SMS_IP_KEY in dump and c.inflight_key("jen", "7") in dump


async def test_sms_index_rebuild_and_lookup() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    by_ip, by_host = m.build_sms_index([
        {"db_id": "pg_a", "server_name": "web01", "hostname": "web01.corp", "ip": "192.0.2.10",
         "nic_ips": ["192.0.2.11"], "resource_id": "r1"},
        {"db_id": "pg_b", "server_name": "web02", "hostname": "web02", "ip": "192.0.2.10"},
    ])
    assert await cache.rebuild_sms_index(by_ip, by_host) == 4
    two = await cache.sms_by_ip("192.0.2.10")
    assert [s["server_name"] for s in two.servers] == ["web01", "web02"]
    one = await cache.sms_by_host("WEB01.corp")
    assert one.servers[0]["resource_id"] == "r1"
    assert (await cache.sms_by_ip("not-an-ip")).error == c.ERROR_INVALID_KEY
    assert (await cache.sms_by_ip("192.0.2.99")).servers == ()
    await cache.rebuild_sms_index({}, {})
    assert (await cache.sms_by_ip("192.0.2.10")).servers == ()
    redis.hget = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    assert (await cache.sms_by_host("web01")).error == c.ERROR_REDIS


class _FakeRepo:
    def __init__(self, rows: list[dict[str, Any]], sms: list[dict[str, Any]]) -> None:
        self.rows, self.sms = rows, sms
        self.cleanups = 0

    async def all_rows(self) -> list[dict[str, Any]]:
        return self.rows

    async def snapshot_rows(self, table: str, **_: Any) -> list[dict[str, Any]]:
        assert table == "sms_inventory"
        return self.sms

    async def cleanup(self, **_: Any) -> dict[str, int]:
        self.cleanups += 1
        return {}


async def test_sync_from_repo_rebuilds_copy_and_index() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    repo = _FakeRepo([_row("instance", POD, rid=1)],
                     [{"db_id": "pg_a", "server_name": "web01", "ip": "192.0.2.10"}])
    out = await cache.sync_from_repo(repo)  # type: ignore[arg-type]
    assert out == {"map_fields": 1, "index_keys": 2}
    assert (await cache.sms_by_ip("192.0.2.10")).servers[0]["server_name"] == "web01"


async def test_run_sync_loop_cleans_then_syncs_until_cancelled() -> None:
    redis = _Redis()
    cache = c.RedisApmHostMapCache(redis)
    repo = _FakeRepo([_row("instance", POD, rid=1)], [])
    task = asyncio.create_task(c.run_sync_loop(repo, cache, interval_seconds=0))  # type: ignore[arg-type]
    for _ in range(20):
        await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert repo.cleanups >= 1
    assert "apmhostmap:jen" in redis.dump()


# ── 스트림 · inflight ───────────────────────────────────────────────────
async def test_publish_and_decode_request_roundtrip() -> None:
    redis = _Redis()
    msg = m.learn_message(source_id="jen", domain_id="7", instance_name=POD,
                          basis=m.BASIS_NODE_ADDRESS,
                          targets=[m.make_target(db_id="pg_a", server_name="n1")])
    msg_id = await c.publish_request(redis, msg)
    assert msg_id == "1-0"
    fields, kw = redis.streams[c.REQUEST_STREAM][0]
    assert fields["type"] == "learn" and json.loads(fields["data"])["instance_name"] == POD
    assert kw == {"maxlen": c.REQUEST_STREAM_MAXLEN, "approximate": True}
    raw = {k.encode(): v.encode() for k, v in fields.items()}  # bytes 응답도 읽는다
    assert c.decode_request(raw) == msg
    assert c.decode_request({"type": "bogus", "data": "{}"}) is None
    assert c.decode_request({"type": "touch", "data": "[]"}) is None


async def test_publish_rejects_unknown_type_and_reports_redis_error() -> None:
    redis = _Redis()
    assert await c.publish_request(redis, {"type": "write_pg"}) is None
    assert c.REQUEST_STREAM not in redis.streams
    redis.xadd = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    assert await c.publish_request(redis, m.touch_message(source_id="jen", map_ids=[1])) is None


async def test_claim_inflight_nx_ex() -> None:
    redis = _Redis()
    assert await c.claim_inflight(redis, "jen", "7") is True
    assert await c.claim_inflight(redis, "jen", "7") is False
    assert 0 < await redis.ttl("apmhostmap:inflight:jen:7") <= c.INFLIGHT_TTL_SECONDS
    redis.advance(c.INFLIGHT_TTL_SECONDS + 1)
    assert await c.claim_inflight(redis, "jen", "7") is True
    redis.set = AsyncMock(side_effect=ConnectionError("down"))  # type: ignore[method-assign]
    assert await c.claim_inflight(redis, "jen", "8") is None


def test_map_key_rejects_reserved_and_separator() -> None:
    assert c.map_key("jen") == "apmhostmap:jen"
    for bad in ("", "smsip", "smshost", "req", "inflight", "a:b"):
        with pytest.raises(ValueError):
            c.map_key(bad)
