"""사전 스캔 단독 실행 Redis 락 (plans/210 §3.5 · D-362).

`SET apmhostmap:scan:lock <run_id> NX EX 3600` — 잡은 실행만 30초마다 연장하고 끝나면 지운다(자기
run_id일 때만). 연장·해제는 「값 비교 → expire/del」을 Lua 스크립트 1회로 원자적으로 한다(비교와
쓰기 사이에 만료·재획득이 끼어 남의 락을 연장·삭제하지 않게 — `worker_lock`과 같은 방식).
키 이름에 `:`를 넣어 사본 재구성의 「없어진 소스 키」 정리 대상에서 빠지게 한다
(`apm_host_map_cache.rebuild`는 접두 뒤에 `:`가 없는 키를 소스 해시로 본다).

계층: infrastructure.
"""

from __future__ import annotations

from typing import Any

SCAN_LOCK_KEY = "apmhostmap:scan:lock"
#: 락 수명(초) — 연장이 끊기면(프로세스 종료) 이 시간 뒤 풀린다.
SCAN_LOCK_TTL_SECONDS = 3600

# 값이 자기 것일 때만 만료를 연장한다(1 = 연장 · 0 = 값 불일치·키 없음).
_EXTEND_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
# 값이 자기 것일 때만 지운다(1 = 삭제 · 0 = 남의 락이거나 이미 없음).
_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw)


class RedisScanLock:
    """스캔 단독 실행 락(redis.asyncio 클라이언트 주입)."""

    def __init__(self, redis: Any, *, key: str = SCAN_LOCK_KEY) -> None:
        """Redis 클라이언트와 키를 받는다."""
        self._redis = redis
        self._key = key

    async def acquire(self, run_id: str, ttl_seconds: int = SCAN_LOCK_TTL_SECONDS) -> bool:
        """잡았으면 True · 다른 실행이 잡고 있으면 False."""
        return bool(await self._redis.set(self._key, run_id, nx=True, ex=int(ttl_seconds)))

    async def holder(self) -> str | None:
        """지금 락을 잡은 run_id(없으면 None)."""
        return _text(await self._redis.get(self._key))

    async def extend(self, run_id: str, ttl_seconds: int = SCAN_LOCK_TTL_SECONDS) -> bool:
        """자기 락이면 수명을 다시 채운다 → 연장 여부(남의 락·만료면 False)."""
        return bool(await self._redis.eval(_EXTEND_SCRIPT, 1, self._key, run_id,
                                           int(ttl_seconds)))

    async def release(self, run_id: str) -> None:
        """자기 락이면 지운다."""
        await self._redis.eval(_RELEASE_SCRIPT, 1, self._key, run_id)


__all__ = ["SCAN_LOCK_KEY", "SCAN_LOCK_TTL_SECONDS", "RedisScanLock"]
