"""`tests/mocks/async_redis.py` 인메모리 페이크 자체 검증.

다른 plans/104 테스트가 기대는 의미론을 고정한다.
"""

from __future__ import annotations

import pytest

from tests.mocks.async_redis import FakeAsyncRedis, FakeRedisError


@pytest.fixture
def fake():
    return FakeAsyncRedis()


async def test_string_set_get_nx_and_expiry(fake):
    assert await fake.set("k", "v1", ex=10) is True
    assert await fake.set("k", "v2", nx=True) is None
    assert await fake.get("k") == "v1"
    assert 0 < await fake.ttl("k") <= 10
    fake.advance(11)
    assert await fake.get("k") is None
    assert await fake.set("k", 3, nx=True) is True
    assert await fake.get("k") == "3"  # decode_responses=True처럼 str
    assert await fake.ttl("k") == -1
    assert await fake.ttl("없음") == -2


async def test_hash_commands(fake):
    assert await fake.hset("h", mapping={"a": "1", "b": 2}) == 2
    assert await fake.hset("h", "a", "x") == 0
    assert await fake.hget("h", "a") == "x"
    assert await fake.hgetall("h") == {"a": "x", "b": "2"}
    assert await fake.hlen("h") == 2
    assert await fake.hdel("h", "a", "없음") == 1
    assert await fake.hdel("h", "b") == 1
    assert await fake.exists("h") == 0  # 빈 Hash는 키째 사라진다
    assert await fake.hgetall("h") == {}


async def test_wrongtype(fake):
    await fake.set("s", "v")
    with pytest.raises(FakeRedisError):
        await fake.hget("s", "f")
    await fake.hset("h", "f", "v")
    with pytest.raises(FakeRedisError):
        await fake.get("h")


async def test_delete_exists_expire_scan(fake):
    await fake.set("schema:a:meta", "1")
    await fake.set("schema:a:tables", "1")
    await fake.set("other", "1")
    assert sorted([k async for k in fake.scan_iter(match="schema:*")]) == [
        "schema:a:meta", "schema:a:tables",
    ]
    assert await fake.expire("other", 5) is True
    assert await fake.expire("없음", 5) is False
    assert await fake.exists("schema:a:meta", "other", "없음") == 2
    assert await fake.delete("schema:a:meta", "없음") == 1


async def test_pipeline_executes_in_order(fake):
    pipe = fake.pipeline()
    assert pipe.hset("h", "f", "v") is pipe
    pipe.set("k", "v").get("k")
    assert await pipe.execute() == [1, True, "v"]
    assert await pipe.execute() == []


async def test_flushdb_and_ping(fake):
    await fake.set("k", "v")
    assert await fake.ping() is True
    await fake.flushdb()
    assert fake.dump() == {}
