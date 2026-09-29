"""이벤트 폴러 — APM 이벤트를 `alarm:raw`에 발행 (plans/87 §5.5 [v3]·[v3.2]·[v3.3] · D-274 ④ ·
SPEC-apm-gateway §5).

- 도메인별로 `[커서, 지금]`을 **경계 포함** 재조회한다. 커서는 `지금 − 겹침(60초)`까지만
  전진한다(늦게 들어온 이벤트).
- 응답에 eventId가 없어 합성 멱등 키로 중복을 막는다 — Redis `SET NX EX`가 성공한 이벤트만 XADD
  하고, XADD가 실패하면 키를 지워 다음 주기에 다시 시도한다(재기동·경계 재조회에도 중복 발행 0).
- 도메인 미접속(`source_unavailable`)은 커서를 전진시키지 않고 백오프한다(주기 ×2, 최대 ×8) — 이벤트
  유실 방지 · 토큰 사용량은 실패 응답까지 센다(§0.10 #10). `contract_violation`은 폴러 버그이므로 그
  도메인 폴링을 멈춘다.
- 상태는 `status()`로 드러낸다(`gateway_health`·기동 로그 — 침묵 금지).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from apm_gateway.adapters.jennifer.api import SOURCE, SOURCE_LABEL, JenniferApi
from apm_gateway.application.masking import mask_text, mask_url
from apm_gateway.application.resolver import InstanceResolver
from apm_gateway.config import GatewayConfig
from apm_gateway.domain import signals as sig
from apm_gateway.domain.errors import CONTRACT_VIOLATION, ApmError
from apm_gateway.domain.events import build_alarm_payload, passes_min_level, severity_for_level

logger = logging.getLogger(__name__)

OVERLAP_MS = 60_000
SEEN_TTL_SECONDS = 24 * 3600
MAX_BACKOFF_FACTOR = 8
_CURSOR_KEY = "apm_gateway:poller:cursor:{domain_id}"
_SEEN_KEY = "apm_gateway:poller:seen:{key}"


class EventPoller:
    def __init__(
        self,
        api: JenniferApi,
        resolver: InstanceResolver,
        cfg: GatewayConfig,
        redis: Any,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.api = api
        self.resolver = resolver
        self.cfg = cfg
        self.redis = redis
        self.clock = clock
        self._state: dict[int, dict[str, Any]] = {}
        self.published_total = 0
        self.duplicates_total = 0

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.cfg.poller.enabled,
            "interval_seconds": self.cfg.poller.interval_seconds,
            "min_level": self.cfg.poller.min_level,
            "published_total": self.published_total,
            "duplicates_total": self.duplicates_total,
            "domains": {
                str(k): {kk: v for kk, v in st.items() if kk != "next_at"}
                for k, st in self._state.items()
            },
        }

    async def run_forever(self) -> None:
        interval = self.cfg.poller.interval_seconds
        logger.info(
            "이벤트 폴러 시작: 주기 %ss · 최소 레벨 %s · stream=%s",
            interval,
            self.cfg.poller.min_level,
            self.cfg.poller.stream_key,
        )
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # 한 주기 실패가 폴러를 죽이지 않게 — 사유는 로그로 남긴다
                logger.exception("이벤트 폴링 주기 실패")
            await asyncio.sleep(interval)

    async def poll_once(self) -> int:
        """한 주기 — 발행 건수를 돌려준다."""
        try:
            inv = await self.resolver.inventory()
        except ApmError as e:
            logger.warning("폴러: 도메인 목록 조회 실패(%s) — 이번 주기 건너뜀", e.code)
            return 0
        published = 0
        now_s = self.clock()
        for domain in inv.domains:
            domain_id = domain["domain_id"]
            state = self._state.setdefault(
                domain_id, {"state": "idle", "failures": 0, "next_at": 0.0}
            )
            if state["state"] == "stopped" or now_s < state["next_at"]:
                continue
            published += await self._poll_domain(domain, inv, state, now_s)
        return published

    async def _poll_domain(
        self, domain: dict[str, Any], inv: Any, state: dict[str, Any], now_s: float
    ) -> int:
        domain_id = domain["domain_id"]
        interval = self.cfg.poller.interval_seconds
        end_ms = int(now_s * 1000)
        cursor_raw = await self.redis.get(_CURSOR_KEY.format(domain_id=domain_id))
        cursor = int(cursor_raw) if cursor_raw else end_ms - interval * 1000
        try:
            events = await self.api.events(domain_id, None, cursor, end_ms)
        except ApmError as e:
            if e.code == CONTRACT_VIOLATION:
                state.update(state="stopped", reason=e.reason)
                logger.warning(
                    "폴러: 도메인 %s 계약 위반 — 폴링 중지(게이트웨이 버그): %s",
                    domain_id,
                    e.reason,
                )
                return 0
            state["failures"] += 1
            factor = min(2 ** state["failures"], MAX_BACKOFF_FACTOR)
            state.update(state="unavailable", reason=e.code, next_at=now_s + interval * factor)
            logger.warning(
                "폴러: 도메인 %s 수집 불가(%s) — 커서 유지 · 다음 시도 %s초 뒤",
                domain_id,
                e.code,
                interval * factor,
            )
            return 0
        published = 0
        publish_failed = False
        for event in sorted(events, key=lambda e: e.get("time_ms") or 0):
            if not passes_min_level(event["level"], self.cfg.poller.min_level):
                continue
            outcome = await self._publish(event, domain, inv)
            if outcome is None:
                publish_failed = True
                break
            published += outcome
        if not publish_failed:
            new_cursor = max(cursor, end_ms - OVERLAP_MS)
            await self.redis.set(_CURSOR_KEY.format(domain_id=domain_id), str(new_cursor))
        state.update(state="ok", failures=0, next_at=0.0, reason="", last_poll_ms=end_ms)
        return published

    async def _publish(self, event: dict[str, Any], domain: dict[str, Any], inv: Any) -> int | None:
        """이벤트 1건 발행. 1 = 발행 · 0 = 중복 · None = Redis 실패(커서 유지)."""
        clean = {
            **event,
            "domain_id": event.get("domain_id")
            if event.get("domain_id") is not None
            else domain["domain_id"],
            "domain_name": event.get("domain_name") or domain["domain_name"],
            "message": mask_text(event.get("message", "")),
            "application": mask_url(event.get("application", "")),
        }
        hostname, confidence, reason, inst = self.resolver.reverse(
            inv, clean["domain_id"], clean.get("instance_id"), clean.get("instance_name", "")
        )
        mapped = self.api.event_signal(clean.get("event_type", ""))
        signal = sig.signal_from_event(
            mapped,
            clean.get("event_type", ""),
            instance_id=clean.get("instance_id"),
            source_tool="event_poller",
        )
        payload = build_alarm_payload(
            clean,
            source=SOURCE,
            source_label=SOURCE_LABEL,
            hostname=hostname,
            ip_address=(inst or {}).get("ip_address", ""),
            match_confidence=confidence,
            match_reason=reason,
            severity=severity_for_level(
                clean.get("level", ""),
                self.cfg.policies.level_severity,
                self.cfg.policies.unknown_level_severity,
            ),
            was_signals=[signal] if signal else [],
            tz=self.cfg.runtime.timezone,
        )
        seen_key = _SEEN_KEY.format(key=payload["apm"]["idempotency_key"])
        try:
            fresh = await self.redis.set(seen_key, "1", nx=True, ex=SEEN_TTL_SECONDS)
        except Exception as e:  # Redis 장애 — 발행하지 않고 커서를 유지한다
            logger.warning("폴러: 멱등 키 기록 실패 — 커서 유지(%s)", type(e).__name__)
            return None
        if not fresh:
            self.duplicates_total += 1
            return 0
        try:
            await self.redis.xadd(
                self.cfg.poller.stream_key, {"data": json.dumps(payload, ensure_ascii=False)}
            )
        except Exception as e:
            await self.redis.delete(seen_key)
            logger.warning("폴러: XADD 실패 — 다음 주기 재시도(%s)", type(e).__name__)
            return None
        self.published_total += 1
        if not hostname:
            logger.warning(
                "폴러: 인스턴스 hostname 미해소 — hostname 빈 값으로 발행"
                "(조사 트리거는 사유를 남기고 생략): "
                "domain=%s instance=%s",
                clean["domain_id"],
                clean.get("instance_id"),
            )
        return 1
