"""관제 메타모니터링 집계의 루프 밖 실행 · 사용자 공유 짧은 캐시 · 단일 비행 (plans/207 M-1·M-2).

`/admin/noise/health`와 `/alarm/metrics`는 판정 기록 파일을 **처음부터 끝까지** 읽는다(꼬리 증분
캐시 밖 · 보존 상한 없음). 종전에는 이벤트 루프 위에서 요청마다 2~3회 순회해, 그동안 같은 프로세스의
모든 요청과 알람 워커가 멈췄다(D-358 · `plans/207` §7.2 U1).

- 플래그 `NOISE_AGGREGATE_OFFLOAD_ENABLED`는 **기동 시 1회** 해석한다 — `create_app`이 켜졌을 때만
  `app.state.noise_aggregate_cache`에 이 객체를 둔다. 없으면 두 라우트는 종전 동기 경로 그대로다.
- 켜면 집계는 `DecisionStore.health_snapshot`(파일 1회 순회)을 스레드에서 돌리고, 상한 시간
  (`NOISE_AGGREGATE_TIMEOUT_SECONDS`)을 넘으면 중단 신호로 순회를 멈춘다.
- 결과는 `NOISE_AGGREGATE_CACHE_SECONDS` 동안 프로세스 안에서 사용자 모두가 나눠 쓰고, 같은 키를
  동시에 부르면 계산은 하나만 돌고 나머지는 그 결과를 기다린다. 키는 결과를 바꾸는 인자 전부
  (파일 실경로·기록 활성·창·세 임계값)라, 두 라우트가 같은 키면 같은 항목을 공유한다.
- 상한 시간 초과·순회 실패는 **503 + 사유**다. 직전 캐시로 대신하지 않는다 — 무수신 워치독
  (`no_events`)이 멈춘 값을 정상처럼 보이게 하면 안 되고, 화면(`noise.js` `loadHealth`)은 주기 갱신
  실패 때 마지막으로 그린 값을 그대로 두므로 대체 응답이 없어도 깨지지 않는다.
- uvicorn 단일 워커 전제(D-358 ⑤) — 캐시는 프로세스 안에만 있다.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

_CacheKey = tuple[str, bool, int, float, int, float]


@dataclass
class _Entry:
    """계산 1회의 결과(성공이면 `agg`·`alerts`, 실패면 `reason`)."""

    at: float
    agg: dict[str, Any] | None = None
    alerts: list[dict[str, Any]] | None = None
    reason: str | None = None


class NoiseAggregateCache:
    """메타모니터링 집계(`aggregate` + `meta_alerts`)의 공유 캐시 · 단일 비행 · 상한 시간."""

    def __init__(self, *, ttl_seconds: float, timeout_seconds: float) -> None:
        """캐시 유지 시간(초 · 0이면 캐시 없이 동시 호출만 합친다)과 계산 상한 시간(초)을 받는다."""
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self._entries: dict[_CacheKey, _Entry] = {}
        self._inflight: dict[_CacheKey, asyncio.Task[_Entry]] = {}

    async def health(
        self,
        store: Any,
        *,
        window_seconds: int,
        suppress_ratio_threshold: float,
        min_events: int,
        collection_failed_ratio_threshold: float,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """`(store.aggregate(window), store.meta_alerts(...))`와 같은 값을 돌려준다.

        Raises:
            HTTPException: 503 — 상한 시간 초과 또는 순회 실패(사유를 `detail`에 싣는다).
        """
        key: _CacheKey = (
            os.path.realpath(store.path),
            bool(store.enabled),
            int(window_seconds),
            float(suppress_ratio_threshold),
            int(min_events),
            float(collection_failed_ratio_threshold),
        )
        entry = self._entries.get(key)
        if entry is not None and time.monotonic() - entry.at < self.ttl_seconds:
            return self._copy(entry)
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._compute(key, store), name="noise-aggregate")
            self._inflight[key] = task
        # 기다리던 요청이 끊겨도 공유 계산은 취소하지 않는다 — 다른 사용자가 같은 결과를 기다린다
        result = await asyncio.shield(task)
        if result.reason == "timeout":
            raise HTTPException(
                status_code=503,
                detail=(
                    f"관제 집계가 상한 시간({self.timeout_seconds:g}초)을 넘어 중단했습니다 — "
                    "판정 기록 파일이 큽니다. 잠시 뒤 다시 시도하세요 "
                    "(NOISE_AGGREGATE_TIMEOUT_SECONDS)"
                ),
            )
        if result.reason is not None:
            raise HTTPException(
                status_code=503, detail=f"관제 집계에 실패했습니다 ({result.reason})"
            )
        return self._copy(result)

    async def _compute(self, key: _CacheKey, store: Any) -> _Entry:
        """스레드에서 1회 계산한다 — 성공만 캐시에 넣고, 실패는 사유를 담아 돌려준다(예외 없음)."""
        _path, _enabled, window, threshold, min_events, cf_threshold = key
        stop = threading.Event()

        def _run() -> tuple[dict[str, Any], list[dict[str, Any]]]:
            result: tuple[dict[str, Any], list[dict[str, Any]]] = store.health_snapshot(
                window_seconds=window,
                suppress_ratio_threshold=threshold,
                min_events=min_events,
                collection_failed_ratio_threshold=cf_threshold,
                should_stop=stop.is_set,
            )
            return result

        succeeded = False
        try:
            agg, alerts = await asyncio.wait_for(
                asyncio.to_thread(_run), timeout=self.timeout_seconds
            )
            succeeded = True
        except TimeoutError:
            stop.set()
            logger.warning(
                "관제 집계 시간 초과(%.0f초 · 창 %d초) — 순회 중단 · 503 응답",
                self.timeout_seconds,
                window,
            )
            return _Entry(at=time.monotonic(), reason="timeout")
        except Exception as exc:  # noqa: BLE001 — 사유를 응답과 로그에 남기고 503으로 답한다
            # 응답 detail에는 타입명만 — 원인(스택)은 서버 로그에만 남긴다
            logger.warning(
                "관제 집계 실패(창 %d초) — 503 응답: %s", window, type(exc).__name__, exc_info=True
            )
            return _Entry(at=time.monotonic(), reason=type(exc).__name__)
        finally:
            # 성공하지 못했으면(초과·실패·태스크 취소) 스레드 순회도 멈춘다
            if not succeeded:
                stop.set()
            self._inflight.pop(key, None)
        entry = _Entry(at=time.monotonic(), agg=agg, alerts=alerts)
        now = entry.at
        # 만료 항목 정리 — 키는 설정값 조합이라 몇 개뿐이지만 리로드로 임계가 바뀌면 옛 키가 남는다
        for old in [k for k, e in self._entries.items() if now - e.at >= self.ttl_seconds]:
            self._entries.pop(old, None)
        if self.ttl_seconds > 0:
            self._entries[key] = entry
        return entry

    @staticmethod
    def _copy(entry: _Entry) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """공유 결과의 사본 — 최근 결정 경과(초)는 계산 뒤 흐른 시간만큼 늘려 준다."""
        agg = copy.deepcopy(entry.agg) or {}
        alerts = copy.deepcopy(entry.alerts) or []
        age = agg.get("last_event_age_seconds")
        if isinstance(age, (int, float)):
            agg["last_event_age_seconds"] = float(age) + max(0.0, time.monotonic() - entry.at)
        return agg, alerts


def noise_aggregate_cache(request: Request) -> NoiseAggregateCache | None:
    """이 기동에서 집계 루프 밖 실행이 켜졌으면 공유 캐시를, 아니면 None(종전 동기 경로)을 준다."""
    cache = getattr(request.app.state, "noise_aggregate_cache", None)
    return cache if isinstance(cache, NoiseAggregateCache) else None


def build_noise_aggregate_cache(noise_gate_cfg: Any) -> NoiseAggregateCache | None:
    """`create_app`용 — `NOISE_AGGREGATE_OFFLOAD_ENABLED`가 정확히 True일 때만 캐시를 만든다."""
    if getattr(noise_gate_cfg, "aggregate_offload_enabled", False) is not True:
        return None
    return NoiseAggregateCache(
        ttl_seconds=float(noise_gate_cfg.aggregate_cache_seconds),
        timeout_seconds=float(noise_gate_cfg.aggregate_timeout_seconds),
    )
