"""APM·SMS 연계 매핑 되적재 소비자 — `apmhostmap:req` → PG 정본·Redis 사본 (plans/210 §3.1 · §3.3).

워커는 PG에 쓰지 않으므로(D-049) 진단 고속 경로의 결과를 스트림으로 넘기고, API 프로세스의 이
소비자가 반영한다(기록자는 API 프로세스뿐).

| 메시지 | 처리 |
|---|---|
| `learn` | 키·대상 검증 → workload 행 → `learn_proposals` → `apply_auto_many` → 바뀐 행 사본 반영 |
| `touch` | id(메시지당 `MAX_TOUCH_IDS`까지)를 모아 `TOUCH_FLUSH_SECONDS`(1분)마다 `repo.touch` |
| `itam_request` | 등록된 처리기(`set_itam_handler` — W5)로 넘긴다. 없으면 로그만 |

- consumer group(`CONSUMER_GROUP`)으로 읽는다 — API 프로세스가 여럿이어도 한 메시지는 한 곳만
  처리한다. 기동 시 그룹 생성은 멱등(BUSYGROUP 무시).
- 스트림 메시지를 그대로 믿지 않는다 — `learn`은 `source_id`·`domain_id`가 매핑 키 규칙
  (`valid_key`)에 맞고 대상 `db_id`가 전부 노드 주소 조회 소스(`node_lookup_db_ids`) 안일 때만
  쓴다. 어긋나면 경고 로그 후 쓰기 없이 ack한다.
- 처리 뒤 늘 ack한다 — 반영 실패는 사유 로그를 남기고 버린다(다음 알람이 다시 miss → 다시
  되적재하므로 독 메시지로 루프를 막지 않는 편이 낫다). 비정상 종료로 남은 pending도 같은 이유로
  다시 읽지 않는다.

계층: application — domain(apm_host_map) + infrastructure(스트림 헬퍼) 소비.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Any

from noise_gate.domain import apm_host_map as hm

logger = logging.getLogger(__name__)

#: consumer group 이름(API 프로세스 공용).
CONSUMER_GROUP = "apmhostmap-api"
#: touch 묶음 갱신 간격(초).
TOUCH_FLUSH_SECONDS = 60.0
#: 한 번에 읽는 메시지 수.
READ_COUNT = 50
#: XREADGROUP 대기(ms) — Redis 소켓 시간 초과(2초)보다 짧아야 한다.
READ_BLOCK_MS = 1000
#: `touch` 메시지 1건에서 받는 id 상한(넘으면 잘라 내고 로그).
MAX_TOUCH_IDS = 200
#: 바뀐 행으로 보고 사본에 반영하는 쓰기 판정.
_CHANGED_ACTIONS = frozenset({hm.WRITE_INSERT, hm.WRITE_UPDATE, hm.WRITE_SUGGEST})

#: `itam_request` 처리기 — 메시지 dict(`{type, source_id, domain_id, domain_name}`)를 받는다.
ItamRequestHandler = Callable[[dict[str, Any]], Awaitable[Any]]


def _consumer_name() -> str:
    return f"api-{socket.gethostname()}-{os.getpid()}"


def _default_allowed_db_ids() -> list[str]:
    """`learn` 대상 `db_id` 허용 목록 — API 프로세스 설정의 노드 주소 조회 소스.

    API 기동(`src/api/server.py`)과 같은 `load_config()`(프로세스 1회 캐시)를 읽는다.
    """
    from src.config import load_config

    from noise_gate.application.host_candidates import node_lookup_db_ids

    return node_lookup_db_ids(load_config())


class ApmHostMapConsumer:
    """`apmhostmap:req` 소비자 — `run()`을 API 수명 태스크로 돌린다."""

    def __init__(
        self,
        repo: hm.ApmHostMapRepository,
        cache: hm.ApmHostMapCache,
        *,
        redis: Any,  # redis.asyncio 클라이언트
        max_targets: int = hm.DEFAULT_MAX_TARGETS,
        retry_hours: int = 24,
        consumer: str | None = None,
        touch_flush_seconds: float = TOUCH_FLUSH_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        allowed_db_ids: Callable[[], Collection[str]] = _default_allowed_db_ids,
    ) -> None:
        """저장소·사본·스트림 연결을 받는다.

        Args:
            allowed_db_ids: `learn` 대상 `db_id` 허용 목록 공급(기본 — 설정의 노드 주소 조회 소스)
        """
        self._repo = repo
        self._cache = cache
        self._redis = redis
        self._max_targets = max(1, int(max_targets))
        self._retry_hours = int(retry_hours)
        self._consumer = consumer or _consumer_name()
        self._touch_flush_seconds = float(touch_flush_seconds)
        self._clock = clock
        self._pending_touch: set[int] = set()
        self._last_flush = clock()
        self._itam_handler: ItamRequestHandler | None = None
        self._allowed_db_ids = allowed_db_ids

    # ── W5 등록점 ──────────────────────────────────────────────────────
    def set_itam_handler(self, handler: ItamRequestHandler | None) -> None:
        """`itam_request` 처리기를 등록한다(None이면 해제 — 로그만 남기고 ack)."""
        self._itam_handler = handler

    # ── 기동 ───────────────────────────────────────────────────────────
    async def ensure_group(self) -> None:
        """consumer group을 만든다(이미 있으면 그대로 — 멱등)."""
        from noise_gate.infrastructure.apm_host_map_cache import REQUEST_STREAM
        from noise_gate.infrastructure.redis_queue import ensure_consumer_group

        await ensure_consumer_group(self._redis, REQUEST_STREAM, CONSUMER_GROUP)

    # ── 처리 ───────────────────────────────────────────────────────────
    async def handle(self, message: Mapping[str, Any]) -> None:
        """메시지 1건 처리(예외는 올린다 — `consume_once`가 사유 로그 후 ack)."""
        kind = message.get("type")
        if kind == hm.MSG_LEARN:
            await self._learn(message)
        elif kind == hm.MSG_TOUCH:
            raw_ids = message.get("ids")
            ids = list(raw_ids) if isinstance(raw_ids, list) else []
            if len(ids) > MAX_TOUCH_IDS:
                logger.warning("apm_host_map touch id 상한 초과 — %d건만 반영: source=%s n=%d",
                               MAX_TOUCH_IDS, message.get("source_id"), len(ids))
                ids = ids[:MAX_TOUCH_IDS]
            for raw in ids:
                try:
                    map_id = int(raw)
                except (TypeError, ValueError):
                    continue
                if map_id > 0:
                    self._pending_touch.add(map_id)
        elif kind == hm.MSG_ITAM_REQUEST:
            if self._itam_handler is None:
                logger.info(
                    "apm_host_map ITAM 생성 요청 — 처리기 미등록(로그만): source=%s domain=%s",
                    message.get("source_id"), message.get("domain_id"),
                )
                return
            await self._itam_handler(dict(message))

    async def _learn(self, message: Mapping[str, Any]) -> None:
        source_id = str(message.get("source_id") or "")
        domain_id = str(message.get("domain_id") or "")
        instance_name = str(message.get("instance_name") or "")
        if not hm.valid_key(source_id, domain_id, hm.SCOPE_DOMAIN, ""):
            logger.warning("apm_host_map 되적재 거부 — 매핑 키 규칙 밖: source=%r domain=%r",
                           source_id[:64], domain_id[:64])
            return
        targets = [t for t in message.get("targets") or [] if isinstance(t, Mapping)]
        allowed = set(self._allowed_db_ids())
        foreign = sorted({str(t.get("db_id") or "") for t in targets} - allowed)
        if foreign:
            logger.warning("apm_host_map 되적재 거부 — 조회 소스 밖 db_id: source=%s domain=%s "
                           "db_ids=%s", source_id, domain_id, ",".join(d[:64] for d in foreign))
            return
        wl_key = hm.workload_match_key(instance_name)
        existing_wl = (
            await self._repo.get_by_key(source_id, domain_id, hm.SCOPE_WORKLOAD, wl_key)
            if wl_key else None
        )
        evidence = message.get("evidence")
        proposals = hm.learn_proposals(
            domain_id=domain_id,
            instance_name=instance_name,
            targets=targets,
            basis=str(message.get("basis") or ""),
            existing_workload=existing_wl,
            evidence=evidence if isinstance(evidence, Mapping) else None,
            domain_name=str(message.get("domain_name") or ""),
            state=str(message.get("state") or hm.STATE_ACTIVE),
            max_targets=self._max_targets,
        )
        if not proposals:
            logger.info("apm_host_map 되적재 — 제안 없음: source=%s instance=%s",
                        source_id, instance_name)
            return
        results = await self._repo.apply_auto_many(
            source_id, proposals, retry_hours=self._retry_hours,
        )
        changed = 0
        for action, row in results:
            if row is not None and action in _CHANGED_ACTIONS:
                await self._cache.put(row)
                changed += 1
        logger.info(
            "apm_host_map 되적재: source=%s domain=%s instance=%s 결과=%s 사본반영=%d",
            source_id, domain_id, instance_name, ",".join(a for a, _ in results), changed,
        )

    async def flush_touches(self, *, force: bool = False) -> int:
        """모은 touch를 간격이 지났거나 `force`면 `repo.touch` 1회로 반영 → 갱신 행 수."""
        now = self._clock()
        if not self._pending_touch or (
            not force and now - self._last_flush < self._touch_flush_seconds
        ):
            return 0
        ids = sorted(self._pending_touch)
        self._pending_touch.clear()
        self._last_flush = now
        try:
            return await self._repo.touch(ids)
        except Exception:  # noqa: BLE001 — 사용 시각은 정리 기준일 뿐이다(사유는 로그)
            logger.warning("apm_host_map touch 반영 실패: n=%d", len(ids), exc_info=True)
            return 0

    async def consume_once(self, *, block_ms: int | None = None) -> int:
        """한 번 읽어 처리·ack → 읽은 메시지 수(`block_ms` None이면 대기 없음)."""
        from noise_gate.infrastructure.apm_host_map_cache import REQUEST_STREAM, decode_request

        kwargs: dict[str, Any] = {"count": READ_COUNT}
        if block_ms is not None:
            kwargs["block"] = block_ms
        results = await self._redis.xreadgroup(
            CONSUMER_GROUP, self._consumer, {REQUEST_STREAM: ">"}, **kwargs
        )
        count = 0
        for _stream, entries in results or []:
            for msg_id, fields in entries:
                count += 1
                message = decode_request(fields or {})
                if message is None:
                    logger.warning("apm_host_map 요청 형식 오류 — 버림: id=%s", msg_id)
                else:
                    try:
                        await self.handle(message)
                    except Exception:  # noqa: BLE001 — 한 메시지 실패가 소비를 멈추지 않는다
                        logger.warning(
                            "apm_host_map 요청 처리 실패 — 버림(다음 알람이 다시 되적재): "
                            "type=%s id=%s", message.get("type"), msg_id, exc_info=True,
                        )
                await self._redis.xack(REQUEST_STREAM, CONSUMER_GROUP, msg_id)
        await self.flush_touches()
        return count

    async def run(self) -> None:
        """소비 루프 — 취소되면 남은 touch를 반영하고 끝낸다. 읽기 오류는 로그 후 잠시 쉬고 계속."""
        await self.ensure_group()
        logger.info("apm_host_map 되적재 소비 시작 (group=%s consumer=%s)",
                    CONSUMER_GROUP, self._consumer)
        try:
            while True:
                try:
                    await self.consume_once(block_ms=READ_BLOCK_MS)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — Redis 일시 장애가 루프를 끝내지 않는다
                    logger.warning("apm_host_map 되적재 읽기 실패 — 1초 뒤 다시", exc_info=True)
                    await asyncio.sleep(1.0)
        finally:
            await self.flush_touches(force=True)


__all__ = [
    "CONSUMER_GROUP",
    "MAX_TOUCH_IDS",
    "READ_BLOCK_MS",
    "READ_COUNT",
    "TOUCH_FLUSH_SECONDS",
    "ApmHostMapConsumer",
    "ItamRequestHandler",
]
