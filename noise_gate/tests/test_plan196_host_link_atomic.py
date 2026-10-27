"""plans/196 잔여 1·5 — 연결 저장소 WATCH 트랜잭션 · 공유 호출 상한 · 사건 종합 조사 연결 힌트.

Redis는 `tests.mocks.async_redis.FakeAsyncRedis`(키 버전으로 WATCH를 흉내 낸다). 경쟁은 즉시 모드
`get` 직후 다른 쓰기를 끼워 넣어 만든다.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from redis.exceptions import WatchError
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application.nodes import investigation_trigger as trig
from noise_gate.infrastructure.host_link_store import (
    PATTERNS_KEY,
    RATE_KEY_PREFIX,
    HostLinkChangedError,
    HostLinkStore,
    link_key,
)
from noise_gate.tests.test_investigation_followup import FakeSubmitClient, _authz_ns

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6"


def _run(coro):  # noqa: ANN001, ANN202
    return asyncio.run(coro)


def _race_after_get(redis: FakeAsyncRedis, key: str, write: Any, *, times: int = 1) -> list[int]:
    """`key`의 get 직후 `write()`(코루틴 함수)를 `times`번까지 끼워 넣는다 → 끼운 횟수 목록."""
    original = redis.get
    fired: list[int] = []

    async def _get(name: str) -> Any:
        value = await original(name)
        if name == key and len(fired) < times:
            fired.append(1)
            await write()
        return value

    redis.get = _get  # type: ignore[method-assign]
    return fired


# ── FakePipeline WATCH ──────────────────────────────────────────────────
def test_fake_pipeline_watch_aborts_on_concurrent_write_and_plain_pipeline_unchanged():
    redis = FakeAsyncRedis()

    async def _scenario():  # noqa: ANN202
        await redis.set("k", "1")
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.watch("k")
            assert await pipe.get("k") == "1"  # 즉시 모드
            await redis.set("k", "2")
            pipe.multi()
            pipe.set("k", "3")
            with pytest.raises(WatchError):
                await pipe.execute()
        assert await redis.get("k") == "2"
        # WATCH 없는 파이프라인은 예전처럼 큐잉 → 일괄 실행
        pipe = redis.pipeline()
        pipe.set("a", "x").incr("n").incr("n").hincrby("h", "f", 2)
        assert await pipe.execute() == [True, 1, 2, 2]

    _run(_scenario())


# ── approve ─────────────────────────────────────────────────────────────
def test_approve_raises_changed_when_record_overwritten_after_compare():
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        rec, _ = await store.save("s", "web01", kind="link", confirmed=False, by="u1",
                                  by_role="user", db_id="d", server_name="web01")
        seen = {"db_id": "d", "server_name": "web01", "at": rec["at"]}
        key = link_key("s", "web01")

        async def _other_selection() -> None:
            await redis.set(key, json.dumps({**json.loads(await redis.get(key)),
                                             "server_name": "web02"}))

        fired = _race_after_get(redis, key, _other_selection)
        with pytest.raises(HostLinkChangedError):
            await store.approve("s", "web01", by="op", expect=seen)
        assert fired == [1]
        cur = await store.get("s", "web01")
        assert cur["state"] == "pending" and cur["server_name"] == "web02"
        assert await store.patterns() == {}

    _run(_scenario())


# ── save ────────────────────────────────────────────────────────────────
def test_pending_save_does_not_overwrite_confirmed_that_lands_during_race():
    """대기 저장이 「확정 아님」을 읽은 뒤 확정이 들어오면 재시도에서 확정을 보고 저장 안 한다."""
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)
    other = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        key = link_key("cm_prd", POD)

        async def _operator_confirms() -> None:
            await other.save("cm_prd", POD, kind="link", confirmed=True, by="op",
                             by_role="operator", db_id="polestar_cm_gp",
                             server_name="kbl-kbland-admin-price",
                             polestar_hostname="kbl-kbland-admin-price")

        _race_after_get(redis, key, _operator_confirms)
        _, stored = await store.save("cm_prd", POD, kind="link", confirmed=False, by="u1",
                                     by_role="user", db_id="polestar_cm_gp", server_name="x")
        assert stored is False
        cur = await store.get("cm_prd", POD)
        assert cur["state"] == "confirmed" and cur["server_name"] == "kbl-kbland-admin-price"
        assert await store.patterns() == {"prefix|bfm|kbl": 1}

    _run(_scenario())


def test_save_gives_up_after_retries_with_watch_error(caplog):
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        key = link_key("s", "web01")
        n = 0

        async def _bump() -> None:
            nonlocal n
            n += 1
            await redis.set(key, f'{{"state": "pending", "kind": "link", "n": {n}}}')

        fired = _race_after_get(redis, key, _bump, times=10)
        with pytest.raises(WatchError):
            await store.save("s", "web01", kind="link", confirmed=True, by="op",
                             by_role="operator", db_id="d", server_name="web01")
        assert len(fired) == 3
        assert await redis.hgetall(PATTERNS_KEY) == {}

    _run(_scenario())
    assert "동시 쓰기 충돌 3회" in caplog.text


def test_confirmed_replace_moves_pattern_counts_in_one_transaction():
    redis = FakeAsyncRedis()
    store = HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        await store.save("s", "WEB01.kb.local", kind="link", confirmed=True, by="op",
                         by_role="operator", db_id="d", server_name="web01")
        assert await store.patterns() == {"normalize||": 1}
        # 같은 연결 재확정 — 건수 변화 없음
        await store.save("s", "WEB01.kb.local", kind="link", confirmed=True, by="op",
                         by_role="operator", db_id="d", server_name="web01")
        assert await store.patterns() == {"normalize||": 1}
        # 다른 서버로 교체 — 옛 패턴 필드는 0이 되어 지워진다
        await store.save("s", "WEB01.kb.local", kind="absent", confirmed=True, by="op",
                         by_role="operator")
        assert await redis.hgetall(PATTERNS_KEY) == {}

    _run(_scenario())


# ── 호출 상한 ───────────────────────────────────────────────────────────
def test_rate_hit_fixed_window_shared_across_stores():
    redis = FakeAsyncRedis()
    a, b = HostLinkStore(redis), HostLinkStore(redis)

    async def _scenario():  # noqa: ANN202
        t = 1_000_040.0  # 창 번호 16667(60초) — 창 끝까지 40초
        hits = [await (a if i % 2 else b).rate_hit("link", "u1", limit=5, window_seconds=60,
                                                    now=t) for i in range(6)]
        assert hits[:5] == [None] * 5 and hits[5] == 40
        assert await a.rate_hit("link", "u2", limit=5, window_seconds=60, now=t) is None
        assert await a.rate_hit("link", "u1", limit=5, window_seconds=60, now=t + 40) is None
        keys = list(redis.dump())
        assert all(k.startswith(RATE_KEY_PREFIX) for k in keys)
        assert await redis.ttl(f"{RATE_KEY_PREFIX}link:u1:16667") > 0
        assert await a.list_links() == []

    _run(_scenario())


# ── 사건 종합 조사 연결 힌트(항목 1) ────────────────────────────────────
def _apm_event() -> SimpleNamespace:
    return SimpleNamespace(
        db_id="jennifer_cm_prd", server_name=POD, hostname=POD, alarm_id="a1", severity=3,
        alarm_name="SQL_TOOMANY_FETCH", resource_type="apm.Instance", resource_name="",
        alarm_time=None, conditions="", condition_log="",
        raw_payload={"apm": {"source_id": "cm_prd"}},
    )


def _episode_payload(enabled: bool, redis: Any) -> dict:
    client = FakeSubmitClient()
    cfg = SimpleNamespace(noise_gate=SimpleNamespace(host_link_enabled=enabled), **_authz_ns())
    decision = SimpleNamespace(tier="page", reason="r", fingerprint="fp", signals={})
    episode = {"episode_id": "ep-1", "members": [{"alarm_id": "a1", "db_id": "jennifer_cm_prd"}]}
    out = _run(trig.submit_episode_investigation(
        client, _apm_event(), decision, episode, ["a1"], cfg=cfg, redis=redis
    ))
    assert out["status"] == "submitted"
    return client.submit_calls[0]


def test_episode_payload_carries_confirmed_link_when_flag_on():
    redis = FakeAsyncRedis()
    _run(HostLinkStore(redis).save(
        "cm_prd", POD, kind="link", confirmed=True, by="op", by_role="operator",
        db_id="polestar_cm_gp", server_name="KBL-01", polestar_hostname="kbl-01",
    ))
    payload = _episode_payload(True, redis)
    assert payload["meta"]["investigation_kind"] == "episode"
    link = payload["meta"]["hints"]["polestar_link"]
    assert link["status"] == "linked" and link["db_id"] == "polestar_cm_gp"
    assert link["server_name"] == "KBL-01"


def test_episode_payload_unchanged_when_flag_off_or_no_redis():
    redis = FakeAsyncRedis()
    _run(HostLinkStore(redis).save(
        "cm_prd", POD, kind="link", confirmed=True, by="op", by_role="operator",
        db_id="polestar_cm_gp", server_name="KBL-01",
    ))
    off = _episode_payload(False, redis)
    assert "polestar_link" not in off["meta"]["hints"]
    assert off == _episode_payload(False, None)
    assert _episode_payload(True, None)["meta"]["hints"]["polestar_link"] == {
        "status": "unresolved"
    }


def test_worker_episode_host_exposes_redis():
    from noise_gate.application.alarm_worker import AlarmWorker, _WorkerEpisodeHost

    worker = object.__new__(AlarmWorker)
    assert _WorkerEpisodeHost(worker).redis is None
    worker._redis = sentinel = object()
    assert _WorkerEpisodeHost(worker).redis is sentinel
