"""조사 서비스 상태(부하 게이지) 캐시 (plans/194 W5 · C1·C5 · D-347).

조사 목록·알람 조사 조회·실시간 스트림 목록 프레임·관리자 요약이 sre_agent `sre_health`의 부하
게이지(진행·대기·정리 중 슬롯과 상한)를 함께 보여 준다. 요청마다 조사 서비스를 부르지 않도록 앱 단위
캐시 1개가 `CACHE_SECONDS`(10초)에 한 번만 묻는다. 만료 뒤에는 **직전 값을 바로 돌려주고**
백그라운드 작업 하나가 갱신한다(stale-while-revalidate — 조사 서비스가 TCP는 받고 응답을
붙들어도 조회 화면이 기다리지 않는다). 값이 한 번도 없을 때(첫 조회)만 잠금 하나를 기다려
같은 결과를 받는다 — 이 대기는 `sre_agent_client.HEALTH_TIMEOUT_SECONDS`(3초)로 묶인다
(전부 async).

실패(URL 미설정·연결 불가·시간 초과·구버전)는 예외 없이 `None` 게이지와 사유로 끝난다(침묵 금지 —
사유가 바뀔 때 warning 1줄). 실패도 같은 시간만큼 캐시해 조사 서비스가 내려가 있을 때 요청마다
연결을 시도하지 않는다.

계층: interface(`src/api/`). 조사 서비스 통신은 `noise_gate.infrastructure.sre_agent_client`만 쓴다.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

#: 캐시 유지 시간(초).
CACHE_SECONDS = 10.0
#: 헬스는 받았는데 부하 게이지가 없을 때(구버전 조사 서비스 · 실 dispatcher 아님)의 사유.
NO_GAUGE_REASON = "조사 서비스가 부하 게이지를 주지 않습니다(구버전 또는 조사 실행기 미구성)"
#: app.state 속성 이름.
_STATE_ATTR = "investigation_service_health"

FetchFn = Callable[[Any], Awaitable[tuple[dict[str, Any] | None, str | None]]]


@dataclass(frozen=True)
class HealthSnapshot:
    """조사 서비스 헬스 1회 조회 결과."""

    health: dict[str, Any] | None
    error: str | None
    checked_at: str  # ISO 8601(UTC) — 실제로 조사 서비스에 물은 시각


def _default_fetch(ng: Any) -> Awaitable[tuple[dict[str, Any] | None, str | None]]:
    """`sre_agent_client.fetch_health`(호출 시점에 모듈 속성을 찾는다)."""
    from noise_gate.infrastructure import sre_agent_client

    return sre_agent_client.fetch_health(ng)


class ServiceHealthCache:
    """앱 단위 조사 서비스 헬스 캐시(TTL · 잠금 1개 · 실패도 캐시)."""

    def __init__(
        self,
        ng: Any,
        *,
        ttl: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        fetch: FetchFn | None = None,
    ) -> None:
        """설정(noise_gate)·유지 시간·시계·조회 함수(주입 가능)를 받는다."""
        self._ng = ng
        self._ttl = float(ttl)
        self._clock = clock
        self._fetch = fetch or _default_fetch
        self._lock = asyncio.Lock()
        self._cached: tuple[float, HealthSnapshot] | None = None
        self._logged_error: str | None = None
        self._refresh_task: asyncio.Task[HealthSnapshot] | None = None

    def _fresh(self) -> HealthSnapshot | None:
        cached = self._cached
        if cached is not None and self._clock() - cached[0] < self._ttl:
            return cached[1]
        return None

    async def snapshot(self) -> HealthSnapshot:
        """캐시가 살아 있으면 그것, 만료됐으면 직전 값(갱신은 백그라운드 1건), 없으면 1회 묻는다."""
        fresh = self._fresh()
        if fresh is not None:
            return fresh
        cached = self._cached
        if cached is not None:
            self._start_refresh()
            return cached[1]
        async with self._lock:
            if self._cached is not None:
                return self._cached[1]
            return await self._refresh()

    def _start_refresh(self) -> None:
        """백그라운드 갱신을 하나만 띄운다(이미 같은 이벤트 루프에서 돌고 있으면 그대로 둔다)."""
        task = self._refresh_task
        loop = asyncio.get_running_loop()
        if task is not None and not task.done() and task.get_loop() is loop:
            return
        self._refresh_task = loop.create_task(self._refresh())

    async def _refresh(self) -> HealthSnapshot:
        """조사 서비스에 1회 묻고 캐시를 갈아 끼운다(실패도 캐시 · 사유가 바뀔 때만 warning)."""
        try:
            health, error = await self._fetch(self._ng)
        except Exception as exc:  # noqa: BLE001 — 주입 함수 예외도 사유로(게이지만 빈다)
            health, error = None, f"조사 서비스 상태 조회 실패: {type(exc).__name__}"
        if error and error != self._logged_error:
            logger.warning("조사 서비스 부하 게이지 조회 실패(게이지 null): %s", error)
        self._logged_error = error
        snap = HealthSnapshot(
            health=health if isinstance(health, dict) else None,
            error=error,
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._cached = (self._clock(), snap)
        return snap

    async def service_load(self) -> dict[str, int | None] | None:
        """화면용 부하 게이지(`SERVICE_LOAD_KEYS`) 또는 None(실패·구버전)."""
        from noise_gate.infrastructure.sre_agent_client import service_load_view

        return service_load_view((await self.snapshot()).health)


def health_cache(app_state: Any) -> ServiceHealthCache:
    """앱 단위 캐시(첫 사용 때 만든다)."""
    cache = getattr(app_state, _STATE_ATTR, None)
    if cache is None:
        cache = ServiceHealthCache(app_state.config.noise_gate)
        setattr(app_state, _STATE_ATTR, cache)
    return cache


__all__ = [
    "CACHE_SECONDS",
    "HealthSnapshot",
    "NO_GAUGE_REASON",
    "ServiceHealthCache",
    "health_cache",
]
