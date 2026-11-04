"""알람 워커 단일 인스턴스 잠금 (plans/208 W5 · C-7 · D-359 부기 1 ②).

`ALARM_WORKER_SINGLETON_GUARD`가 켜졌을 때만 워커가 쓴다(기본 off = 이 모듈의 Redis 호출 0).
같은 소비자 그룹·같은 소비자 이름(`worker-1`)으로 워커가 둘 이상 돌면 순서 의존 상태(중복 억제·
스톰·상관·사건)가 프로세스마다 반쪽이 된다(G-11) — 잠금으로 한 인스턴스만 소비하게 한다.

- 획득: `SET <키> <host:pid> NX EX <TTL>` — 이미 있으면 실패(다른 인스턴스가 소비 중).
- 갱신: 주기마다 「값이 자기 것일 때만 만료 연장」을 Lua 스크립트 1회로 원자적으로 한다.
  0(값 불일치·만료)이면 바로 잠금을 잃은 것으로 본다. Redis 호출 실패는 마지막 성공 갱신 뒤
  경과 + 갱신 주기가 TTL에 닿기 전까지 WARNING 1줄 뒤 다음 주기에 다시 시도하고, 닿으면 잃은
  것으로 본다(다음 시도 전에 만료돼 다른 인스턴스가 잡을 수 있다).
- 해제: 「값이 자기 것일 때만 삭제」 — 남의 잠금을 지우지 않는다.

키 이름·TTL·갱신 주기는 상수다(설정 신설 금지 — 필요 최소).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

logger = logging.getLogger(__name__)

#: 잠금 키 — 알람 스트림·소비자 그룹과 무관하게 하나다(워커는 Redis 하나에 하나만 돈다).
LOCK_KEY = "alarm:worker:lock"
#: 잠금 만료(초) — 갱신이 멈춘 인스턴스(죽은 프로세스)의 잠금은 이 시간 뒤 풀린다.
LOCK_TTL_SECONDS = 30
#: 갱신 주기(초) — TTL 안에 갱신을 세 번 시도한다.
RENEW_INTERVAL_SECONDS = 10.0

#: 워커 종료 사유(`AlarmWorker.lock_outcome`) — 실행 중 잠금을 잃었다.
OUTCOME_LOST = "lost"
#: 워커 종료 사유(`AlarmWorker.lock_outcome`) — 기동 때 잠금을 잡지 못했다.
OUTCOME_NOT_ACQUIRED = "not_acquired"

# 값이 자기 것일 때만 만료를 연장한다(1 = 연장 · 0 = 값 불일치·키 없음).
_RENEW_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
)
# 값이 자기 것일 때만 지운다(1 = 삭제 · 0 = 남의 잠금이거나 이미 없음).
_RELEASE_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


def default_owner() -> str:
    """잠금 값 — `<호스트명>:<pid>`(로그에서 누가 잡고 있는지 바로 읽히게)."""
    return f"{socket.gethostname()}:{os.getpid()}"


def _text(value: Any) -> str | None:
    """Redis 응답(bytes·str·None)을 문자열로."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


class WorkerSingletonLock:
    """워커 단일 인스턴스 잠금 — 획득·감시(주기 갱신)·해제.

    Args:
        redis: `redis.asyncio` 클라이언트(워커의 연결을 같이 쓴다 — 연결 풀이라 블로킹 읽기와
            겹쳐도 된다).
        owner: 잠금 값. 기본 `<호스트명>:<pid>`.
        clock: 갱신 경과를 재는 단조 시계(테스트 seam). 기본은 이벤트 루프 시계.
    """

    def __init__(
        self,
        redis: Any,
        *,
        owner: str | None = None,
        key: str = LOCK_KEY,
        ttl_seconds: int = LOCK_TTL_SECONDS,
        renew_interval: float = RENEW_INTERVAL_SECONDS,
        clock: Any = None,
    ) -> None:
        self._redis = redis
        self.owner = owner or default_owner()
        self.key = key
        self._ttl = int(ttl_seconds)
        self._renew_interval = float(renew_interval)
        self._clock = clock
        self._held = False
        self._acquired = False
        self._last_renewed = 0.0

    @property
    def held(self) -> bool:
        """이 인스턴스가 잠금을 잡았고 아직 잃지 않았는가."""
        return self._held

    async def acquire(self) -> bool:
        """`SET NX EX`로 잠금을 잡는다. 이미 다른 값이 있으면 False."""
        ok = await self._redis.set(self.key, self.owner, nx=True, ex=self._ttl)
        self._held = bool(ok)
        self._acquired = self._acquired or self._held
        if self._held:
            self._last_renewed = self._now()
        return self._held

    async def holder(self) -> str | None:
        """지금 잠금을 쥔 값(없으면 None) — 획득 실패 로그용. 조회 실패도 None."""
        try:
            return _text(await self._redis.get(self.key))
        except Exception:
            return None

    async def watch(self) -> str:
        """갱신 주기마다 잠금을 연장하다가, 잃으면 그 사유를 돌려준다.

        - 값이 자기 것이 아니면(다른 인스턴스가 잡았거나 만료) 바로 잃은 것으로 본다.
        - Redis 호출이 실패하면 마지막 성공 갱신 뒤 경과 + 갱신 주기가 TTL에 닿기 전까지는
          WARNING 1줄 뒤 다음 주기에 다시 시도한다. 닿으면 잃은 것으로 본다 — 갱신을 확인하지
          못한 채 소비를 계속하면 만료 뒤 다른 인스턴스와 겹칠 수 있다.

        취소하면 CancelledError가 그대로 올라간다.
        """
        while True:
            await asyncio.sleep(self._renew_interval)
            try:
                renewed = await self._redis.eval(
                    _RENEW_SCRIPT, 1, self.key, self.owner, self._ttl
                )
            except Exception as exc:
                elapsed = self._now() - self._last_renewed
                if elapsed + self._renew_interval >= self._ttl:
                    self._held = False
                    return (
                        f"갱신 실패 지속({elapsed:.0f}초 동안 갱신 못 함): "
                        f"{type(exc).__name__}: {exc}"
                    )
                logger.warning(
                    "알람 워커 단일 인스턴스 잠금 갱신 실패 — %.0f초 뒤 다시 시도한다"
                    "(마지막 갱신 %.0f초 전 · 만료 %d초): %s",
                    self._renew_interval, elapsed, self._ttl, exc,
                )
                continue
            if not renewed:
                self._held = False
                return "값 불일치(만료됐거나 다른 인스턴스가 잡았다)"
            self._last_renewed = self._now()

    def _now(self) -> float:
        """갱신 경과용 단조 시각(초)."""
        if self._clock is not None:
            return float(self._clock())
        return asyncio.get_running_loop().time()

    async def release(self) -> None:
        """자기 값일 때만 잠금을 지운다. 잡은 적이 없으면 아무것도 하지 않는다.

        갱신 호출 실패로 잃은 것으로 본 경우에도 값이 아직 자기 것이면 지운다(비교 후 삭제라 남의
        잠금은 건드리지 않는다).
        """
        if not self._acquired:
            return
        self._held = False
        self._acquired = False
        try:
            await self._redis.eval(_RELEASE_SCRIPT, 1, self.key, self.owner)
        except Exception as exc:
            logger.warning(
                "알람 워커 단일 인스턴스 잠금 해제 실패(%s초 뒤 만료로 풀린다): %s",
                self._ttl, exc,
            )
