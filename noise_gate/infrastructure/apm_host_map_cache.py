"""APM·SMS 연계 매핑 Redis 읽기 사본·SMS 색인·되적재 스트림 (plans/210 §3.1 · §3.3 · D-362).

키(기록자는 API 프로세스뿐 — D-049):
- `apmhostmap:{source_id}` 해시 — 필드 `{scope}|{domain_id}|{match_key}` → JSON
  `{id, origin, basis, state, verified, targets, evidence_text, version}`(도메인 `cache_value`).
- `apmhostmap:smsip` 해시 — `ip` → JSON 서버 목록 · `apmhostmap:smshost` — 정규화 hostname → 목록.
  스캔 스냅샷에서 만든다(도메인 `build_sms_index`). 판정은 「1대일 때만 성립」(호출부).
- `apmhostmap:req` 스트림 — 워커가 `{type: learn|touch|itam_request, data: JSON}`을 XADD하고
  API 소비자(W3)가 PG에 반영한다.
- `apmhostmap:inflight:{source_id}:{domain_id}` — ITAM 생성 요청 중복 억제(`SET NX EX 600`).

읽기(`lookup` HMGET 1회 · 색인 HGET 1회)는 워커·API 어디서나 쓴다. Redis 오류는 삼키지 않고 결과의
`error`로 돌려준다(로그를 남긴다) — 호출부는 miss(직접 조회)로 다루며 오연결은 없다.

redis.asyncio 클라이언트(bytes·str 응답 모두)를 주입받는다. 계층: infrastructure.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.domain.host_names import normalize_hostname

from noise_gate.domain.apm_host_map import (
    MESSAGE_TYPES,
    RESERVED_SOURCE_IDS,
    ApmHostMapCache,
    ApmHostMapRepository,
    IndexLookup,
    MapLookup,
    build_sms_index,
    cache_field,
    cache_value,
    lookup_fields,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = "apmhostmap:"
SMS_IP_KEY = "apmhostmap:smsip"
SMS_HOST_KEY = "apmhostmap:smshost"
REQUEST_STREAM = "apmhostmap:req"
INFLIGHT_PREFIX = "apmhostmap:inflight:"
#: ITAM 생성 요청 중복 억제 창(초).
INFLIGHT_TTL_SECONDS = 600
#: 되적재 스트림 길이 상한(근사 MAXLEN) — 소비자가 멈춰도 메모리가 무한히 늘지 않게 한다.
REQUEST_STREAM_MAXLEN = 10000
#: 사본·색인 주기 대조 간격(초).
SYNC_INTERVAL_SECONDS = 600
#: 소스 해시 키로 쓰면 안 되는 이름(색인·스트림·inflight와 겹친다).
_RESERVED_SUFFIXES = RESERVED_SOURCE_IDS
#: Redis 연결·응답 시간 초과(초).
_REDIS_TIMEOUT_SECONDS = 2.0

ERROR_REDIS = "redis_error"
ERROR_INVALID_KEY = "invalid_key"


def map_key(source_id: str) -> str:
    """소스별 사본 해시 키.

    Raises:
        ValueError: 빈 값 · `:` 포함 · 예약어(`smsip`·`smshost`·`req`·`inflight`)
    """
    source = str(source_id or "").strip()
    if not source or ":" in source or source in _RESERVED_SUFFIXES:
        raise ValueError("사본 키로 쓸 수 없는 source_id")
    return f"{KEY_PREFIX}{source}"


def inflight_key(source_id: str, domain_id: str) -> str:
    """ITAM 생성 요청 inflight 키."""
    return f"{INFLIGHT_PREFIX}{str(source_id or '').strip()}:{str(domain_id or '').strip()}"


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw)


def _loads(raw: Any) -> Any:
    text = _text(raw)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _servers(raw: Any) -> tuple[dict[str, Any], ...]:
    value = _loads(raw)
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return ()
    return tuple(dict(v) for v in value if isinstance(v, dict))


class RedisApmHostMapCache(ApmHostMapCache):
    """연계 매핑 Redis 사본·SMS 색인."""

    def __init__(self, redis: Any) -> None:  # redis.asyncio.Redis (덕 타이핑)
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    @property
    def redis(self) -> Any:
        """주입받은 Redis 클라이언트(스트림 발행·inflight 헬퍼에 같은 연결을 쓴다)."""
        return self._redis

    # ── 읽기 ───────────────────────────────────────────────────────────
    async def lookup(self, source_id: str, domain_id: str, instance_name: str) -> MapLookup:
        """instance·workload·domain 필드를 HMGET 1회로 읽는다 → `{scope: 값}`."""
        try:
            key = map_key(source_id)
        except ValueError:
            return MapLookup(error=ERROR_INVALID_KEY)
        fields = lookup_fields(domain_id, instance_name)
        if not fields:
            return MapLookup(error=ERROR_INVALID_KEY)
        scopes = list(fields)
        try:
            values = await self._redis.hmget(key, [fields[s] for s in scopes])
        except Exception:  # noqa: BLE001 — 사유를 결과로 돌려준다(호출부는 miss로 다룬다)
            logger.warning(
                "apm_host_map 사본 조회 실패 — miss로 진행: source=%s domain=%s",
                source_id, domain_id, exc_info=True,
            )
            return MapLookup(error=ERROR_REDIS)
        records: dict[str, dict[str, Any]] = {}
        for scope, raw in zip(scopes, values or []):
            if raw is None:
                continue
            rec = _loads(raw)
            if not isinstance(rec, dict):
                logger.warning("apm_host_map 사본 값 손상 — 없는 것으로 읽음: %s %s",
                               key, fields[scope])
                continue
            records[scope] = rec
        return MapLookup(records=records)

    async def _index_get(self, key: str, member: str) -> IndexLookup:
        try:
            raw = await self._redis.hget(key, member)
        except Exception:  # noqa: BLE001 — 사유를 결과로 돌려준다
            logger.warning("apm_host_map SMS 색인 조회 실패 — miss로 진행: %s", key,
                           exc_info=True)
            return IndexLookup(error=ERROR_REDIS)
        return IndexLookup(servers=_servers(raw))

    async def sms_by_ip(self, ip: str) -> IndexLookup:
        """SMS IP 색인 HGET 1회(IP 형식이 아니면 `invalid_key`)."""
        try:
            member = str(ipaddress.ip_address(str(ip or "").strip()))
        except ValueError:
            return IndexLookup(error=ERROR_INVALID_KEY)
        return await self._index_get(SMS_IP_KEY, member)

    async def sms_by_host(self, hostname: str) -> IndexLookup:
        """SMS hostname 색인 HGET 1회(정규화 hostname)."""
        member = normalize_hostname(hostname)
        if not member:
            return IndexLookup(error=ERROR_INVALID_KEY)
        return await self._index_get(SMS_HOST_KEY, member)

    # ── 쓰기(API 프로세스) ────────────────────────────────────────────
    @staticmethod
    def _row_key(row: Mapping[str, Any]) -> tuple[str, str]:
        return (
            map_key(str(row.get("source_id") or "")),
            cache_field(
                str(row.get("scope") or ""), str(row.get("domain_id") or ""),
                str(row.get("match_key") or ""),
            ),
        )

    async def put(self, row: Mapping[str, Any]) -> bool:
        """행 1건을 사본에 반영 → 성공 여부(실패는 주기 대조가 메운다)."""
        try:
            key, fld = self._row_key(row)
            await self._redis.hset(key, fld, _dumps(cache_value(row)))
        except Exception:  # noqa: BLE001 — 사본 실패는 PG 정본에 영향 없다(사유는 로그)
            logger.warning("apm_host_map 사본 반영 실패: id=%s", row.get("id"), exc_info=True)
            return False
        return True

    async def remove(self, row: Mapping[str, Any]) -> bool:
        """행 1건을 사본에서 뺀다 → 성공 여부."""
        try:
            key, fld = self._row_key(row)
            await self._redis.hdel(key, fld)
        except Exception:  # noqa: BLE001 — 사본 실패는 PG 정본에 영향 없다(사유는 로그)
            logger.warning("apm_host_map 사본 삭제 실패: id=%s", row.get("id"), exc_info=True)
            return False
        return True

    async def rebuild(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """사본 전체 재구성 — 소스마다 DEL+HSET를 한 트랜잭션(MULTI)으로 · 없어진 소스 키 삭제.

        Redis 오류는 올린다(호출부 — 기동·주기 대조 — 가 로그를 남긴다).
        """
        by_source: dict[str, dict[str, str]] = {}
        for row in rows:
            try:
                key, fld = self._row_key(row)
            except ValueError:
                logger.warning("apm_host_map 사본 재구성 — 키 규칙 밖 행 건너뜀: id=%s",
                               row.get("id"))
                continue
            by_source.setdefault(key, {})[fld] = _dumps(cache_value(row))
        for key, mapping in by_source.items():
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.delete(key)
                pipe.hset(key, mapping=mapping)
                await pipe.execute()
        stale: list[str] = []
        async for raw_key in self._redis.scan_iter(match=f"{KEY_PREFIX}*"):
            name = _text(raw_key) or ""
            suffix = name[len(KEY_PREFIX):]
            if ":" in suffix or suffix in _RESERVED_SUFFIXES or name in by_source:
                continue
            stale.append(name)
        if stale:
            await self._redis.delete(*stale)
        return sum(len(m) for m in by_source.values())

    async def rebuild_sms_index(
        self,
        by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
        by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> int:
        """SMS 색인 전체 재구성(두 해시를 한 트랜잭션으로 교체) → 적재 키 수."""
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(SMS_IP_KEY, SMS_HOST_KEY)
            if by_ip:
                pipe.hset(SMS_IP_KEY, mapping={
                    k: _dumps([dict(s) for s in v]) for k, v in by_ip.items()
                })
            if by_host:
                pipe.hset(SMS_HOST_KEY, mapping={
                    k: _dumps([dict(s) for s in v]) for k, v in by_host.items()
                })
            await pipe.execute()
        return len(by_ip) + len(by_host)

    async def sync_from_repo(self, repo: ApmHostMapRepository) -> dict[str, int]:
        """PG 정본 → 사본·SMS 색인 전체 재구성(기동 · 10분 주기 대조 · 스캔 뒤)."""
        fields = await self.rebuild(await repo.all_rows())
        by_ip, by_host = build_sms_index(await repo.snapshot_rows("sms_inventory"))
        keys = await self.rebuild_sms_index(by_ip, by_host)
        return {"map_fields": fields, "index_keys": keys}


# ── 워커 쪽 헬퍼(읽기 연결로 발행 — PG 쓰기 없음 · D-049) ──────────────
async def publish_request(redis: Any, message: Mapping[str, Any]) -> str | None:
    """되적재 요청 1건을 `apmhostmap:req`에 XADD → 메시지 id(실패·형식 오류면 None · 로그).

    필드: `type`(learn|touch|itam_request) · `data`(나머지 키의 JSON).
    """
    kind = str(message.get("type") or "")
    if kind not in MESSAGE_TYPES:
        logger.warning("apm_host_map 요청 종류 오류 — 발행 안 함: %r", kind)
        return None
    data = {k: v for k, v in message.items() if k != "type"}
    try:
        msg_id = await redis.xadd(
            REQUEST_STREAM, {"type": kind, "data": _dumps(data)},
            maxlen=REQUEST_STREAM_MAXLEN, approximate=True,
        )
    except Exception:  # noqa: BLE001 — 되적재 실패는 이번 조사를 막지 않는다(사유는 로그)
        logger.warning("apm_host_map 요청 발행 실패: type=%s", kind, exc_info=True)
        return None
    return _text(msg_id)


def decode_request(fields: Mapping[Any, Any]) -> dict[str, Any] | None:
    """스트림 엔트리 필드 → 메시지 dict(`{type, ...}` · 형식 오류면 None)."""
    decoded = {_text(k): v for k, v in fields.items()}
    kind = _text(decoded.get("type"))
    if kind not in MESSAGE_TYPES:
        return None
    data = _loads(decoded.get("data"))
    if not isinstance(data, dict):
        return None
    return {"type": kind, **{k: v for k, v in data.items() if k != "type"}}


async def claim_inflight(
    redis: Any, source_id: str, domain_id: str, *, ttl_seconds: int = INFLIGHT_TTL_SECONDS
) -> bool | None:
    """ITAM 생성 요청 중복 억제.

    이번에 잡았으면 True · 이미 진행 중이면 False · Redis 오류면 None(로그).
    """
    try:
        ok = await redis.set(inflight_key(source_id, domain_id), "1", nx=True, ex=ttl_seconds)
    except Exception:  # noqa: BLE001 — 사유를 결과로 돌려준다
        logger.warning("apm_host_map inflight 설정 실패: source=%s domain=%s",
                       source_id, domain_id, exc_info=True)
        return None
    return bool(ok)


# ── 기동 배선 보조(API 프로세스) ─────────────────────────────────────
def connect_redis(config: Any) -> Any:
    """설정의 Redis로 연결 객체를 만든다(시간 초과 2초)."""
    import redis.asyncio as aioredis

    return aioredis.from_url(
        f"redis://{config.redis.host}:{config.redis.port}",
        password=config.redis.password or None,
        db=config.redis.db,
        socket_timeout=_REDIS_TIMEOUT_SECONDS,
        socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
    )


async def run_sync_loop(
    repo: ApmHostMapRepository,
    cache: RedisApmHostMapCache,
    *,
    interval_seconds: float = SYNC_INTERVAL_SECONDS,
) -> None:
    """주기 대조 — 간격마다 정리(보존 기한) 뒤 사본·색인 전체 재구성. 오류는 로그 후 다음 주기."""
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            break
        try:
            await repo.cleanup()
            await cache.sync_from_repo(repo)
        except Exception:  # noqa: BLE001 — 한 주기 실패가 루프를 끝내지 않는다(사유는 로그)
            logger.warning("apm_host_map 사본 주기 대조 실패", exc_info=True)


__all__ = [
    "ERROR_INVALID_KEY",
    "ERROR_REDIS",
    "INFLIGHT_PREFIX",
    "INFLIGHT_TTL_SECONDS",
    "KEY_PREFIX",
    "REQUEST_STREAM",
    "REQUEST_STREAM_MAXLEN",
    "SMS_HOST_KEY",
    "SMS_IP_KEY",
    "SYNC_INTERVAL_SECONDS",
    "RedisApmHostMapCache",
    "claim_inflight",
    "connect_redis",
    "decode_request",
    "inflight_key",
    "map_key",
    "publish_request",
    "run_sync_loop",
]
