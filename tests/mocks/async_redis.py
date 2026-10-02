"""인메모리 async Redis 페이크 (plans/104 — fakeredis 미설치 환경용).

`redis.asyncio.Redis(decode_responses=True)`가 쓰이는 범위만 흉내 낸다 — 값은 항상 `str`로
돌려준다. 네트워크·프로세스 0, 테스트 간 상태 공유 0(인스턴스마다 독립).

지원 명령
    ping · get · set(ex, nx) · delete · exists · expire · ttl · hget · hset(key/value · mapping) ·
    hgetall · hdel · hlen · scan_iter(match) · pipeline(execute) · flushdb · aclose

사용법 — `RedisSchemaCache`에 붙이기::

    from tests.mocks.async_redis import FakeAsyncRedis, attach_fake_redis

    cache = RedisSchemaCache(redis_config)      # redis_config는 MagicMock이어도 된다
    fake = attach_fake_redis(cache)             # cache._redis = fake · cache._connected = True
    await cache.save_structure_meta("db1", {"patterns": []})
    assert "schema:db1:structure_meta" in fake.dump()

`SchemaCacheManager`에 붙이기(Redis 백엔드 설정 필요)::

    mgr = SchemaCacheManager(config)            # config.schema_cache.backend = "redis"
    fake = attach_fake_redis(mgr._redis_cache)
    # 이후 mgr.ensure_redis_connected()는 fake.ping()으로 True가 된다

"Redis를 비운 뒤" 시나리오는 `await fake.flushdb()`. 만료는 `time.monotonic()` 기준이며
`fake.advance(seconds)`로 시계를 앞당길 수 있다.
실패 주입이 필요하면 해당 메서드를 `AsyncMock(side_effect=...)`로 덮어쓴다.
"""

from __future__ import annotations

import fnmatch
import time
from collections.abc import AsyncIterator
from typing import Any


class FakeRedisError(RuntimeError):
    """자료형 불일치(WRONGTYPE) 등 Redis가 오류로 응답할 상황."""


_WRONGTYPE = "WRONGTYPE Operation against a key holding the wrong kind of value"


def _to_str(value: Any) -> str:
    """redis-py가 받아들이는 값(str·bytes·int·float)을 decode_responses 저장 형태로 바꾼다."""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, (str, int, float)):
        return str(value)
    raise FakeRedisError(
        f"Invalid input of type: '{type(value).__name__}'. "
        "Convert to a bytes, string, int or float first."
    )


class FakeAsyncRedis:
    """`redis.asyncio.Redis(decode_responses=True)`의 인메모리 대역."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}  # str 또는 dict[str, str](Hash)
        self._expire_at: dict[str, float] = {}
        self._clock_offset = 0.0
        self.closed = False

    # --- 내부 ---

    def _now(self) -> float:
        return time.monotonic() + self._clock_offset

    def _purge(self, key: str) -> None:
        exp = self._expire_at.get(key)
        if exp is not None and exp <= self._now():
            self._data.pop(key, None)
            self._expire_at.pop(key, None)

    def _get_entry(self, key: str) -> Any:
        self._purge(key)
        return self._data.get(key)

    def _hash(self, key: str, *, create: bool = False) -> dict[str, str] | None:
        entry = self._get_entry(key)
        if entry is None:
            if not create:
                return None
            entry = {}
            self._data[key] = entry
        if not isinstance(entry, dict):
            raise FakeRedisError(_WRONGTYPE)
        return entry

    # --- 테스트 보조 ---

    def advance(self, seconds: float) -> None:
        """만료 판정용 시계를 앞당긴다."""
        self._clock_offset += seconds

    def dump(self) -> dict[str, Any]:
        """만료되지 않은 전체 키·값 사본(Hash는 dict 사본)."""
        for key in list(self._data):
            self._purge(key)
        return {k: (dict(v) if isinstance(v, dict) else v) for k, v in self._data.items()}

    # --- 연결 ---

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True

    async def flushdb(self) -> bool:
        self._data.clear()
        self._expire_at.clear()
        return True

    # --- String ---

    async def get(self, name: str) -> str | None:
        entry = self._get_entry(name)
        if entry is None:
            return None
        if isinstance(entry, dict):
            raise FakeRedisError(_WRONGTYPE)
        return entry

    async def set(
        self,
        name: str,
        value: Any,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None:
        exists = self._get_entry(name) is not None
        if (nx and exists) or (xx and not exists):
            return None
        self._data[name] = _to_str(value)
        self._expire_at.pop(name, None)
        if ex is not None:
            self._expire_at[name] = self._now() + float(ex)
        elif px is not None:
            self._expire_at[name] = self._now() + float(px) / 1000.0
        return True

    # --- 키 ---

    async def delete(self, *names: str) -> int:
        count = 0
        for name in names:
            if self._get_entry(name) is not None:
                count += 1
            self._data.pop(name, None)
            self._expire_at.pop(name, None)
        return count

    async def exists(self, *names: str) -> int:
        return sum(1 for name in names if self._get_entry(name) is not None)

    async def expire(self, name: str, time: int) -> bool:  # noqa: A002 - redis-py 인자명
        if self._get_entry(name) is None:
            return False
        self._expire_at[name] = self._now() + float(time)
        return True

    async def ttl(self, name: str) -> int:
        if self._get_entry(name) is None:
            return -2
        exp = self._expire_at.get(name)
        if exp is None:
            return -1
        return max(0, int(round(exp - self._now())))

    async def scan_iter(
        self,
        match: str | None = None,
        count: int | None = None,
        _type: str | None = None,
    ) -> AsyncIterator[str]:
        for key in list(self._data):
            self._purge(key)
            if key not in self._data:
                continue
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

    # --- Hash ---

    async def hget(self, name: str, key: str) -> str | None:
        h = self._hash(name)
        return None if h is None else h.get(key)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: Any = None,
        mapping: dict | None = None,
        items: list | None = None,
    ) -> int:
        pairs: list[tuple[Any, Any]] = []
        if key is not None:
            pairs.append((key, value))
        if mapping:
            pairs.extend(mapping.items())
        if items:
            pairs.extend(zip(items[::2], items[1::2]))
        if not pairs:
            raise FakeRedisError("'hset' with no key value pairs")
        h = self._hash(name, create=True)
        assert h is not None
        added = 0
        for k, v in pairs:
            field = _to_str(k)
            if field not in h:
                added += 1
            h[field] = _to_str(v)
        return added

    async def hgetall(self, name: str) -> dict[str, str]:
        h = self._hash(name)
        return {} if h is None else dict(h)

    async def hdel(self, name: str, *keys: str) -> int:
        h = self._hash(name)
        if h is None:
            return 0
        count = 0
        for k in keys:
            if k in h:
                del h[k]
                count += 1
        if not h:
            self._data.pop(name, None)
            self._expire_at.pop(name, None)
        return count

    async def hlen(self, name: str) -> int:
        h = self._hash(name)
        return 0 if h is None else len(h)

    # --- pipeline ---

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        return FakePipeline(self)


class FakePipeline:
    """명령을 모았다가 `execute()`에서 순서대로 실행한다(redis-py처럼 큐잉 메서드는 self 반환)."""

    _QUEUEABLE = frozenset({
        "get", "set", "delete", "exists", "expire", "ttl",
        "hget", "hset", "hgetall", "hdel", "hlen",
    })

    def __init__(self, redis: FakeAsyncRedis) -> None:
        self._redis = redis
        self._commands: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str) -> Any:
        if name not in self._QUEUEABLE:
            raise AttributeError(name)

        def _queue(*args: Any, **kwargs: Any) -> FakePipeline:
            self._commands.append((name, args, kwargs))
            return self

        return _queue

    async def execute(self) -> list[Any]:
        results = []
        commands, self._commands = self._commands, []
        for name, args, kwargs in commands:
            results.append(await getattr(self._redis, name)(*args, **kwargs))
        return results

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self._commands = []


def attach_fake_redis(redis_cache: Any, fake: FakeAsyncRedis | None = None) -> FakeAsyncRedis:
    """`RedisSchemaCache` 인스턴스에 페이크를 연결 상태로 붙이고 페이크를 돌려준다."""
    fake = fake or FakeAsyncRedis()
    redis_cache._redis = fake
    redis_cache._connected = True
    return fake
