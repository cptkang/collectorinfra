"""plans/196 잔여 검증 — 연결 저장소 WATCH 트랜잭션을 **실 redis-py asyncio 클라이언트**로 돈다.

`tests.mocks.async_redis.FakePipeline`은 redis-py 파이프라인을 흉내 낸 것이라, 실 클라이언트의
즉시 모드(watch 뒤 get) · multi 뒤 큐잉 · EXEC 널 응답 → `WatchError` · `async with` 종료 시
UNWATCH가 저장소 코드와 맞는지는 따로 확인해야 한다. 여기서는 127.0.0.1 임의 포트에 테스트용
RESP2 최소 서버(이 파일 안 · 명령 십여 개)를 띄우고 실 `redis.asyncio.Redis`를 붙인다
(실 Redis 서버는 띄우지 않는다). 경쟁은 서버가 WATCH 중인 연결의 GET 직후 다른 쓰기를 끼워 만든다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
import redis.asyncio as aioredis
from redis.exceptions import WatchError

from noise_gate.infrastructure.host_link_store import (
    PATTERNS_KEY,
    HostLinkChangedError,
    HostLinkStore,
    link_key,
)

POD = "bfm-kbland-admin-price-deploy-6dd49d5df6"


class _MiniRedis:
    """RESP2 최소 서버 — 문자열·해시·WATCH/MULTI/EXEC(키 버전)만."""

    def __init__(self) -> None:
        self.strings: dict[bytes, bytes] = {}
        self.hashes: dict[bytes, dict[bytes, bytes]] = {}
        self.ttls: dict[bytes, int] = {}
        self.versions: dict[bytes, int] = {}
        self.commands: list[tuple[bytes, ...]] = []
        #: WATCH 중인 연결이 GET한 직후 부른다(경쟁 주입) — `(key) -> None`.
        self.after_watched_get: Callable[[bytes], None] | None = None

    def _bump(self, key: bytes) -> None:
        self.versions[key] = self.versions.get(key, 0) + 1

    # --- 명령 실행(값 → RESP 인코딩은 _enc) ---
    def run(self, args: list[bytes]) -> Any:  # noqa: C901 — 명령 표
        cmd = args[0].upper()
        a = args[1:]
        if cmd in (b"CLIENT", b"SELECT"):
            return b"+OK"
        if cmd == b"PING":
            return b"+PONG"
        if cmd == b"GET":
            return self.strings.get(a[0])
        if cmd == b"SET":
            self.strings[a[0]] = a[1]
            self.ttls.pop(a[0], None)
            self._bump(a[0])
            return b"+OK"
        if cmd == b"DEL":
            n = 0
            for k in a:
                if k in self.strings or k in self.hashes:
                    n += 1
                    self._bump(k)
                self.strings.pop(k, None)
                self.hashes.pop(k, None)
            return n
        if cmd in (b"INCR", b"INCRBY"):  # redis-py `incr()`는 INCRBY를 보낸다
            v = int(self.strings.get(a[0], b"0")) + (int(a[1]) if cmd == b"INCRBY" else 1)
            self.strings[a[0]] = str(v).encode()
            self._bump(a[0])
            return v
        if cmd == b"EXPIRE":
            if a[0] not in self.strings and a[0] not in self.hashes:
                return 0
            self.ttls[a[0]] = int(a[1])
            self._bump(a[0])
            return 1
        if cmd == b"TTL":
            return self.ttls.get(a[0], -1 if a[0] in self.strings else -2)
        if cmd == b"HINCRBY":
            h = self.hashes.setdefault(a[0], {})
            v = int(h.get(a[1], b"0")) + int(a[2])
            h[a[1]] = str(v).encode()
            self._bump(a[0])
            return v
        if cmd == b"HGET":
            return self.hashes.get(a[0], {}).get(a[1])
        if cmd == b"HDEL":
            h = self.hashes.get(a[0], {})
            n = sum(1 for f in a[1:] if h.pop(f, None) is not None)
            if n:
                self._bump(a[0])
            if not h:
                self.hashes.pop(a[0], None)
            return n
        if cmd == b"HGETALL":
            out: list[bytes] = []
            for k, v in self.hashes.get(a[0], {}).items():
                out += [k, v]
            return out
        return Exception(f"ERR unknown command {cmd!r}")

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        watched: dict[bytes, int] = {}
        queue: list[list[bytes]] | None = None
        try:
            while True:
                args = await _read_command(reader)
                if args is None:
                    break
                self.commands.append(tuple(args))
                cmd = args[0].upper()
                if cmd == b"WATCH":
                    for k in args[1:]:
                        watched[k] = self.versions.get(k, 0)
                    reply: Any = b"+OK"
                elif cmd == b"UNWATCH":
                    watched = {}
                    reply = b"+OK"
                elif cmd == b"MULTI":
                    queue = []
                    reply = b"+OK"
                elif cmd == b"DISCARD":
                    queue, watched = None, {}
                    reply = b"+OK"
                elif cmd == b"EXEC":
                    pending, queue = queue or [], None
                    dirty = any(self.versions.get(k, 0) != v for k, v in watched.items())
                    watched = {}
                    reply = None if dirty else [self.run(c) for c in pending]
                    writer.write(_enc(reply, null_array=True))
                    await writer.drain()
                    continue
                elif queue is not None:
                    queue.append(args)
                    reply = b"+QUEUED"
                else:
                    reply = self.run(args)
                    if cmd == b"GET" and watched and self.after_watched_get is not None:
                        self.after_watched_get(args[1])
                writer.write(_enc(reply))
                await writer.drain()
        finally:
            writer.close()


async def _read_command(reader: asyncio.StreamReader) -> list[bytes] | None:
    line = await reader.readline()
    if not line:
        return None
    assert line.startswith(b"*"), line
    n = int(line[1:].strip())
    out: list[bytes] = []
    for _ in range(n):
        hdr = await reader.readline()
        size = int(hdr[1:].strip())
        data = await reader.readexactly(size + 2)
        out.append(data[:-2])
    return out


def _enc(value: Any, *, null_array: bool = False) -> bytes:
    if value is None:
        return b"*-1\r\n" if null_array else b"$-1\r\n"
    if isinstance(value, Exception):
        return b"-" + str(value).encode() + b"\r\n"
    if isinstance(value, bytes) and value[:1] == b"+":
        return value + b"\r\n"
    if isinstance(value, bool):
        return b":%d\r\n" % int(value)
    if isinstance(value, int):
        return b":%d\r\n" % value
    if isinstance(value, bytes):
        return b"$%d\r\n%s\r\n" % (len(value), value)
    if isinstance(value, list):
        return b"*%d\r\n" % len(value) + b"".join(_enc(v) for v in value)
    raise TypeError(value)


def _with_server(scenario: Callable[[_MiniRedis, Any], Awaitable[None]]) -> _MiniRedis:
    srv = _MiniRedis()

    async def _main() -> None:
        server = await asyncio.start_server(srv.handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        client = aioredis.Redis(host="127.0.0.1", port=port, protocol=2)
        try:
            await scenario(srv, client)
        finally:
            await client.aclose()
            server.close()
            await server.wait_closed()

    asyncio.run(_main())
    return srv


def _overwrite(srv: _MiniRedis, key: bytes, times: int, mutate: Callable[[dict], dict]) -> list:
    """WATCH 중 GET 직후 서버 쪽에서 레코드를 바꾼다(다른 클라이언트의 쓰기) → 주입 횟수 목록."""
    fired: list[int] = []

    def _hook(k: bytes) -> None:
        if k == key and len(fired) < times:
            fired.append(1)
            rec = json.loads(srv.strings[key]) if key in srv.strings else {}
            srv.run([b"SET", key, json.dumps(mutate(rec)).encode()])

    srv.after_watched_get = _hook
    return fired


# ── approve ──────────────────────────────────────────────────────────────
def test_real_client_approve_race_raises_changed_and_unwatches():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        rec, stored = await store.save("s", "web01", kind="link", confirmed=False, by="u1",
                                       by_role="user", db_id="d", server_name="web01")
        assert stored is True
        key = link_key("s", "web01").encode()
        fired = _overwrite(srv, key, 1, lambda r: {**r, "server_name": "web02"})
        with pytest.raises(HostLinkChangedError):
            await store.approve("s", "web01", by="op",
                                expect={"db_id": "d", "server_name": "web01", "at": rec["at"]})
        assert fired == [1]
        cur = await store.get("s", "web01")
        assert cur["state"] == "pending" and cur["server_name"] == "web02"
        assert await store.patterns() == {}
        # 같은 연결 풀로 이어서 승인 — 앞 WATCH가 남아 있지 않다(EXEC 뒤 초기화)
        srv.after_watched_get = None
        ok = await store.approve("s", "web01", by="op",
                                 expect={"db_id": "d", "server_name": "web02", "at": cur["at"]})
        assert ok["state"] == "confirmed"

    srv = _with_server(_scenario)
    names = [c[0].upper() for c in srv.commands]
    assert names.count(b"MULTI") == names.count(b"EXEC")


def test_real_client_approve_early_return_sends_unwatch():
    """대조 불일치(EXEC 전 반환)면 `async with` 종료가 UNWATCH를 보낸다 — 연결이 감시를 들고 풀로
    돌아가지 않는다."""
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        await store.save("s", "web01", kind="link", confirmed=False, by="u1", by_role="user",
                         db_id="d", server_name="web01")
        with pytest.raises(HostLinkChangedError):
            await store.approve("s", "web01", by="op",
                                expect={"db_id": "d", "server_name": "web01", "at": "old"})

    srv = _with_server(_scenario)
    names = [c[0].upper() for c in srv.commands]
    assert names[-1] == b"UNWATCH"


# ── save ─────────────────────────────────────────────────────────────────
def test_real_client_pending_save_never_overwrites_confirmed_landing_mid_race():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        key = link_key("cm_prd", POD).encode()
        confirmed = {
            "state": "confirmed", "kind": "link", "source_id": "cm_prd", "hostname": POD,
            "db_id": "polestar_cm_gp", "server_name": "kbl-kbland-admin-price",
            "polestar_hostname": "kbl-kbland-admin-price", "by": "op", "at": "t0",
        }
        _overwrite(srv, key, 1, lambda _r: confirmed)
        rec, stored = await store.save("cm_prd", POD, kind="link", confirmed=False, by="u1",
                                       by_role="user", db_id="polestar_cm_gp", server_name="x")
        assert stored is False and rec["state"] == "pending"
        cur = await store.get("cm_prd", POD)
        assert cur["state"] == "confirmed" and cur["server_name"] == "kbl-kbland-admin-price"

    _with_server(_scenario)


def test_real_client_save_gives_up_after_three_conflicts_without_partial_writes():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        key = link_key("s", "web01").encode()
        fired = _overwrite(srv, key, 10, lambda r: {"state": "pending", "kind": "link",
                                                    "n": r.get("n", 0) + 1})
        with pytest.raises(WatchError):
            await store.save("s", "web01", kind="link", confirmed=True, by="op",
                             by_role="operator", db_id="d", server_name="web01")
        assert len(fired) == 3
        assert PATTERNS_KEY.encode() not in srv.hashes
        assert json.loads(srv.strings[key])["n"] == 3

    _with_server(_scenario)


def test_real_client_confirmed_replace_and_delete_drop_zero_pattern_fields():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        await store.save("s", "WEB01.kb.local", kind="link", confirmed=True, by="op",
                         by_role="operator", db_id="d", server_name="web01")
        assert await store.patterns() == {"normalize||": 1}
        deleted = await store.delete("s", "WEB01.kb.local")
        assert deleted is not None and deleted["state"] == "confirmed"
        assert PATTERNS_KEY.encode() not in srv.hashes  # 0 필드는 HDEL로 지워졌다
        assert await store.delete("s", "WEB01.kb.local") is None

    _with_server(_scenario)


def test_real_client_delete_persistent_conflict_raises_watch_error():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        await store.save("s", "web01", kind="link", confirmed=True, by="op",
                         by_role="operator", db_id="d", server_name="web01")
        key = link_key("s", "web01").encode()
        fired = _overwrite(srv, key, 10, lambda r: {**r, "at": "x"})
        with pytest.raises(WatchError):
            await store.delete("s", "web01")
        assert len(fired) == 3 and key in srv.strings

    _with_server(_scenario)


# ── 호출 상한 ─────────────────────────────────────────────────────────────
def test_real_client_rate_hit_incr_and_expire_in_one_transaction():
    async def _scenario(srv: _MiniRedis, client: Any) -> None:
        store = HostLinkStore(client)
        t = 1_000_040.0
        hits = [await store.rate_hit("link", "u1", limit=5, window_seconds=60, now=t)
                for _ in range(6)]
        assert hits == [None] * 5 + [40]
        key = b"hostlinkrate:link:u1:16667"
        assert srv.strings[key] == b"6" and srv.ttls[key] == 61
        # 콜론이 든 sub도 다른 사용자의 카운터 키와 겹치지 않는다(세그먼트 수가 달라진다)
        assert await store.rate_hit("link", "u1:16667", limit=5, window_seconds=60, now=t) is None
        assert srv.strings[key] == b"6"

    srv = _with_server(_scenario)
    # INCR·EXPIRE는 매번 MULTI/EXEC 안에서 함께 나간다(EXPIRE 누락 경로 없음)
    seq = [c[0].upper() for c in srv.commands if c[0].upper() in (b"MULTI", b"INCRBY", b"EXPIRE",
                                                                    b"EXEC")]
    assert seq == [b"MULTI", b"INCRBY", b"EXPIRE", b"EXEC"] * 7
