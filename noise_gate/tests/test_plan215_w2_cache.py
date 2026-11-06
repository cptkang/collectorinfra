"""plans/215 W2 — 업무 서비스 Redis 사본·귀속 색인.

Redis는 `tests.mocks.async_redis.FakeAsyncRedis`에 이 테스트가 쓰는 `hmget`·`smembers`·`sadd`
(파이프라인 큐잉 포함)만 덧붙인 하위 클래스다(공용 페이크는 고치지 않는다).
키 계약 · `idx:host` 1대 규칙 · 귀속 순서 전 분기 · 2개 서비스 미상 · Redis 오류 미상을 본다.
"""

from __future__ import annotations

import json
from typing import Any

from tests.mocks.async_redis import FakeAsyncRedis, FakePipeline

from noise_gate.infrastructure import biz_service_cache as c
from noise_gate.infrastructure.biz_service_cache import Attribution, RedisBizServiceCache

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6-x7k2p"
WORKLOAD = "bfm-kbland-admin-price"


class _Pipe(FakePipeline):
    _QUEUEABLE = FakePipeline._QUEUEABLE | {"sadd"}


class _Redis(FakeAsyncRedis):
    async def hmget(self, name: str, keys: list[str], *args: str) -> list[str | None]:
        h = self._hash(name) or {}
        return [h.get(k) for k in [*keys, *args]]

    def pipeline(self, transaction: bool = True, shard_hint: Any = None) -> _Pipe:
        return _Pipe(self)


class _Repo:
    def __init__(self, services: list[dict[str, Any]]) -> None:
        self.services = services

    async def active_services_with_members(self) -> list[dict[str, Any]]:
        return self.services


def _sms(
    mid: int, db: str, rid: str, *, hostname: str = "", server_name: str = ""
) -> dict[str, Any]:
    return {"id": mid, "kind": "sms_server", "member_key": f"{db}|{rid}", "db_id": db,
            "resource_id": rid, "hostname": hostname, "server_name": server_name,
            "evidence": {"big": "x"}}


def _apm(mid: int, key: str, kind: str = "apm_instance") -> dict[str, Any]:
    return {"id": mid, "kind": kind, "member_key": key}


def _services() -> list[dict[str, Any]]:
    return [
        {"id": 1, "service_key": "loan", "display_name": "여신", "tier": 1, "state": "active",
         "evidence": {"x": 1}, "note": "n", "version": 3,
         "members": [
             _sms(10, "pg_a", "101", hostname="web-01"),
             _sms(11, "pg_a", "102", hostname="dup-host"),
             _apm(12, f"jen|7|instance|{POD}"),
             _apm(13, "jen|7", "apm_domain"),
             _apm(14, "jen|7|biz9", "apm_business"),
         ]},
        {"id": 2, "service_key": "card", "display_name": "카드", "state": "active", "version": 1,
         "members": [
             _sms(20, "pg_b", "201", hostname="dup_host", server_name="card-was"),
             _apm(21, f"jen|8|workload|{WORKLOAD}"),
             _apm(22, "jen|8", "apm_domain"),
         ]},
    ]


async def _synced(
    services: list[dict[str, Any]] | None = None,
) -> tuple[RedisBizServiceCache, _Redis]:
    redis = _Redis()
    cache = RedisBizServiceCache(redis)
    await cache.sync_from_repo(_Repo(_services() if services is None else services))
    return cache, redis


# ── 재구성·키 계약 ──────────────────────────────────────────────────────
async def test_sync_writes_key_contract() -> None:
    cache, redis = await _synced()
    data = redis.dump()
    assert data[c.IDX_SMS_KEY] == {"pg_a|101": "1", "pg_a|102": "1", "pg_b|201": "2"}
    assert data[c.IDX_APM_KEY] == {f"jen|7|instance|{POD}": "1", f"jen|8|workload|{WORKLOAD}": "2"}
    assert data[c.IDX_APMDOM_KEY] == {"jen|7": "1", "jen|8": "2"}
    assert data[c.SVCS_KEY] == {"1", "2"}
    copy = json.loads(data["bizsvc:svc:1"])
    assert copy["display_name"] == "여신" and copy["tier"] == 1 and copy["version"] == 3
    assert "evidence" not in copy and "note" not in copy
    assert [m["kind"] for m in copy["members"]] == [
        "sms_server", "sms_server", "apm_instance", "apm_domain", "apm_business",
    ]
    assert "evidence" not in copy["members"][0]
    assert await cache.get_service(1) == copy
    assert await cache.list_service_ids() == [1, 2]


async def test_idx_host_only_when_one_server_per_canonical_key() -> None:
    """`dup-host`·`dup_host`는 정규형이 같다 — 전 DB 기준 서버 2대라 맨 호스트 필드는 없다.

    DB 한정 필드(`{db_id}|{호스트}`)는 그 DB 안에서 1대면 싣는다(pg_a·pg_b 각 1대).
    """
    _, redis = await _synced()
    host = redis.dump()[c.IDX_HOST_KEY]
    assert host == {
        "web01": "1", "cardwas": "2",
        "pg_a|web01": "1", "pg_a|duphost": "1", "pg_b|duphost": "2", "pg_b|cardwas": "2",
    }


async def test_idx_host_db_field_skipped_when_two_servers_in_same_db() -> None:
    """같은 DB 안에서 같은 정규 호스트 키 서버가 2대면 DB 한정 필드도 싣지 않는다(D-224 ④)."""
    services = [
        {"id": 1, "state": "active", "members": [_sms(1, "pg", "1", hostname="app-01")]},
        {"id": 2, "state": "active", "members": [_sms(2, "pg", "2", hostname="app_01")]},
    ]
    cache, redis = await _synced(services)
    assert redis.dump().get(c.IDX_HOST_KEY, {}) == {}
    assert await cache.attribute(hostname="app01", db_id="pg") is None


async def test_same_server_hostname_and_server_name_same_key_counts_once() -> None:
    services = [{"id": 5, "state": "active",
                 "members": [_sms(1, "pg", "1", hostname="app-01", server_name="APP01")]}]
    _, redis = await _synced(services)
    assert redis.dump()[c.IDX_HOST_KEY] == {"app01": "5", "pg|app01": "5"}


async def test_resync_replaces_and_removes_stale_service_keys() -> None:
    cache, redis = await _synced()
    await redis.set("bizsvc:svc:99", "old")
    await cache.sync_from_repo(_Repo([_services()[1]]))
    data = redis.dump()
    assert "bizsvc:svc:1" not in data and "bizsvc:svc:99" not in data
    assert data[c.SVCS_KEY] == {"2"} and data[c.IDX_SMS_KEY] == {"pg_b|201": "2"}
    assert await cache.get_service(1) is None


async def test_resync_empty_clears_everything() -> None:
    cache, redis = await _synced()
    counts = await cache.sync_from_repo(_Repo([]))
    assert counts["services"] == 0
    assert not [k for k in redis.dump() if k.startswith(c.KEY_PREFIX)]
    assert await cache.list_service_ids() == []
    assert await cache.attribute(polestar_link_targets=[("pg_a", "101")]) is None


# ── 귀속 순서 ───────────────────────────────────────────────────────────
async def test_polestar_link_wins_over_apm_and_host() -> None:
    cache, _ = await _synced()
    got = await cache.attribute(
        polestar_link_targets=[("pg_b", "201")], apm=("jen", "7", POD), hostname="web-01",
    )
    assert got == Attribution(2, c.BASIS_POLESTAR_LINK)


async def test_polestar_link_same_service_twice_is_attributed() -> None:
    cache, _ = await _synced()
    got = await cache.attribute(polestar_link_targets=[("pg_a", "101"), ("pg_a", "102")])
    assert got == Attribution(1, c.BASIS_POLESTAR_LINK)


async def test_polestar_link_two_services_is_unknown_and_stops() -> None:
    """D-224 ④ — 고르지 않는다. 뒤 단계(APM·호스트)로 넘어가 다른 답을 내지도 않는다."""
    cache, _ = await _synced()
    got = await cache.attribute(
        polestar_link_targets=[("pg_a", "101"), ("pg_b", "201")],
        apm=("jen", "7", POD), hostname="web-01",
    )
    assert got is None


async def test_polestar_link_miss_falls_to_apm_instance() -> None:
    cache, _ = await _synced()
    got = await cache.attribute(polestar_link_targets=[("pg_x", "1"), ("bad|id", "2")],
                                apm=("jen", "7", POD))
    assert got == Attribution(1, c.BASIS_APM_INSTANCE)


async def test_apm_workload_key_matches() -> None:
    cache, _ = await _synced()
    other_pod = f"{WORKLOAD}-deploy-7f8e9d0c1b-abcde"
    assert await cache.attribute(apm=("jen", "8", other_pod)) == Attribution(
        2, c.BASIS_APM_INSTANCE
    )


async def test_apm_instance_miss_falls_to_domain() -> None:
    cache, _ = await _synced()
    assert await cache.attribute(apm=("jen", "8", "zzz01")) == Attribution(2, c.BASIS_APM_DOMAIN)
    assert await cache.attribute(apm=("jen", "8", None)) == Attribution(2, c.BASIS_APM_DOMAIN)


async def test_apm_miss_falls_to_host() -> None:
    cache, _ = await _synced()
    got = await cache.attribute(apm=("jen", "99", "zzz"), hostname="WEB-01.corp.local")
    assert got is None  # FQDN 정규형은 첫 레이블이 아니다 — 색인 키 `web01`과 다르다
    got = await cache.attribute(apm=("jen", "99", "zzz"), hostname="WEB_01")
    assert got == Attribution(1, c.BASIS_HOST)


async def test_host_with_db_id_only_matches_that_db() -> None:
    """SMS DB 알람(db_id 있음)은 그 DB 구성원으로만 귀속 — 다른 DB 같은 이름은 미상.

    DB 한정 필드에 없으면 전 DB 기준 필드로 물러서지 않는다.
    """
    cache, _ = await _synced()
    assert await cache.attribute(hostname="WEB_01", db_id="pg_a") == Attribution(1, c.BASIS_HOST)
    assert await cache.attribute(hostname="WEB_01", db_id="pg_b") is None
    assert await cache.attribute(hostname="WEB_01", db_id="pg_zz") is None
    # 전 DB 기준으로는 2대라 미상이던 호스트도 DB 안에서 1대면 귀속된다
    assert await cache.attribute(hostname="dup-host", db_id="pg_b") == Attribution(
        2, c.BASIS_HOST)
    assert await cache.attribute(hostname="web01", db_id="bad|db") is None


async def test_host_ambiguous_or_unknown_is_none() -> None:
    cache, _ = await _synced()
    assert await cache.attribute(hostname="dup-host") is None
    assert await cache.attribute(hostname="nobody") is None
    assert await cache.attribute(hostname="") is None
    assert await cache.attribute() is None


async def test_bytes_responses_are_decoded() -> None:
    cache, redis = await _synced()

    class _Bytes:
        def __getattr__(self, name: str) -> Any:
            fn = getattr(redis, name)

            async def call(*a: Any, **kw: Any) -> Any:
                out = await fn(*a, **kw)
                if isinstance(out, list):
                    return [v.encode() if isinstance(v, str) else v for v in out]
                if isinstance(out, set):
                    return {v.encode() for v in out}
                return out.encode() if isinstance(out, str) else out

            return call

    bcache = RedisBizServiceCache(_Bytes())
    assert await bcache.attribute(hostname="web01") == Attribution(1, c.BASIS_HOST)
    assert await bcache.list_service_ids() == [1, 2]
    got = await bcache.get_service(2)
    assert got is not None and got["display_name"] == "카드"


# ── 오류 = 미상 ─────────────────────────────────────────────────────────
class _Broken:
    def __getattr__(self, name: str) -> Any:
        async def boom(*a: Any, **kw: Any) -> Any:
            raise ConnectionError("down")

        return boom


async def test_redis_errors_are_unknown_not_raised() -> None:
    cache = RedisBizServiceCache(_Broken())
    assert await cache.attribute(polestar_link_targets=[("pg", "1")], hostname="web01") is None
    assert await cache.get_service(1) is None
    assert await cache.list_service_ids() == []


async def test_corrupt_values_are_unknown() -> None:
    cache, redis = await _synced()
    await redis.set("bizsvc:svc:1", "{not json")
    await redis.hset(c.IDX_HOST_KEY, "junk", "abc")
    assert await cache.get_service(1) is None
    assert await cache.attribute(hostname="junk") is None
