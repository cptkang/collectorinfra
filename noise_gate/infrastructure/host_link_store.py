"""호스트 연결 저장소 — 제니퍼 hostname ↔ 관측 DB 등록 서버 (plans/196 §5.4 · Redis).

키 `hostlink:{source_id}:{normalize_hostname(hostname)}` → JSON 1건:
    {state: "confirmed"|"pending", kind: "link"|"absent", source_id, hostname, db_id, server_name,
     polestar_hostname, grade, by, by_role, at, investigation_id, approved_by?, approved_at?,
     expires_at?(absent만)}

- TTL을 쓰지 않는다 — 연결은 캐시가 아니라 사람이 확인한 사실이다. 부정 연결(absent)만
  `expires_at`(30일 · Q6)이 지나면 없는 것으로 읽는다(키는 남겨 관리자 목록에서 보인다).
- 패턴 해시 `hostlink:patterns` — 필드 = 패턴 키(`kind|from|to`), 값 = 적용 건수.
  **확정(confirmed) 연결에서만** 뽑는다(LLM 0 · 오염 자기강화 방지).
- 확정 대기(pending)는 기존 확정 연결을 덮지 않는다 — 한 사용자의 선택이 운영자 확정을 내리지
  않게 한다(그 조사의 재조회에는 호출부가 선택을 그대로 쓴다).
- 쓰기(저장·승인·삭제)는 연결 키 WATCH → 읽기·판정 → MULTI(SET/DEL + 패턴 HINCRBY) → EXEC
  낙관적 트랜잭션이다. 판정과 쓰기 사이에 다른 쓰기가 끼면 EXEC가 무효가 되어 승인은
  `HostLinkChangedError`, 저장·삭제는 짧게 다시 시도한다. Lua 대신 WATCH를 쓰는 것은 운영
  Redis 버전과 무관하고 판정 로직을 Python 한 곳에 두기 위해서다. 패턴 필드가 0 이하가 되면
  트랜잭션 뒤 따로(그 필드를 WATCH해) 지운다 — 그 사이에 남은 0 이하 필드는 `patterns()`가 뺀다.
- 라우트 호출 상한 카운터 `hostlinkrate:{route}:{sub}:{창 번호}`(고정 창 · INCR+EXPIRE 한
  트랜잭션) — `hostlink:` 접두사 밖이라 연결 목록 스캔에 걸리지 않는다.

redis.asyncio 클라이언트(bytes·str 응답 모두)를 주입받는다. 계층: infrastructure.
"""

from __future__ import annotations

import json
import logging
import math
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.exceptions import WatchError
from src.domain.host_names import normalize_hostname

from noise_gate.domain.host_link import (
    KIND_ABSENT,
    KIND_LINK,
    STATE_CONFIRMED,
    STATE_PENDING,
    absent_expired,
    extract_patterns,
    valid_link_key,
)

logger = logging.getLogger(__name__)

KEY_PREFIX = "hostlink:"
PATTERNS_KEY = "hostlink:patterns"
#: 부정 연결 만료(Q6).
ABSENT_TTL = timedelta(days=30)
#: 호출 상한 카운터 접두사 — `KEY_PREFIX` 밖이어야 `list_links` 스캔에 걸리지 않는다.
RATE_KEY_PREFIX = "hostlinkrate:"
#: 저장·삭제의 WATCH 충돌 재시도 횟수.
_WRITE_ATTEMPTS = 3
#: Redis 연결·응답 시간 초과(초) — 응답 없이 걸리면 오류로 끊어 호출 상한이 프로세스 내로 폴백한다.
_REDIS_TIMEOUT_SECONDS = 2.0


class HostLinkChangedError(Exception):
    """승인하려던 확정 대기 연결이 화면에서 본 것과 달라졌다(다른 선택이 그 사이에 덮었다)."""


def link_key(source_id: str, hostname: str) -> str:
    """연결 키(정규화 hostname).

    Raises:
        ValueError: `:`가 들었거나 정규화 hostname이 빈 키(`valid_link_key`)
    """
    if not valid_link_key(source_id, hostname):
        raise ValueError("연결 키로 쓸 수 없는 source_id·hostname")
    return f"{KEY_PREFIX}{str(source_id).strip()}:{normalize_hostname(str(hostname).strip())}"


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw)


def _now_iso(now: datetime | None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _parse(raw: Any) -> dict[str, Any] | None:
    text = _text(raw)
    if not text:
        return None
    try:
        rec = json.loads(text)
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _pattern_keys(rec: dict[str, Any]) -> list[str]:
    """서버 연결의 패턴 키(부정 연결은 없음)."""
    if rec.get("kind") != KIND_LINK:
        return []
    return list(extract_patterns(
        str(rec.get("hostname") or ""),
        [str(rec.get("server_name") or ""), str(rec.get("polestar_hostname") or "")],
    ))


class HostLinkStore:
    """확정·대기·부정 연결과 패턴 건수를 Redis에 읽고 쓴다."""

    def __init__(self, redis: Any) -> None:  # redis.asyncio.Redis (덕 타이핑)
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    async def get(
        self, source_id: str, hostname: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        """연결 1건(만료된 부정 연결·손상 값은 None)."""
        raw = _text(await self._redis.get(link_key(source_id, hostname)))
        if not raw:
            return None
        try:
            rec = json.loads(raw)
        except ValueError:
            logger.warning(
                "호스트 연결 값 손상 — 없는 것으로 읽음: %s", link_key(source_id, hostname)
            )
            return None
        if not isinstance(rec, dict):
            return None
        if rec.get("kind") == KIND_ABSENT and absent_expired(rec, now or datetime.now(UTC)):
            return None
        return rec

    async def _raw(self, key: str) -> dict[str, Any] | None:
        return _parse(await self._redis.get(key))

    async def save(
        self,
        source_id: str,
        hostname: str,
        *,
        kind: str,
        confirmed: bool,
        by: str,
        by_role: str,
        investigation_id: str | None = None,
        db_id: str = "",
        server_name: str = "",
        polestar_hostname: str = "",
        grade: str = "",
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """선택 1건을 저장한다 → `(레코드, 저장했는가)`.

        확정 대기가 기존 확정 연결을 덮게 되면 저장하지 않고 `(새 레코드, False)`를 돌려준다.
        확정으로 저장한 서버 연결이면 패턴을 뽑아 건수를 올린다(기존 확정 연결의 패턴은 내린다).
        """
        at = _now_iso(now)
        rec: dict[str, Any] = {
            "state": STATE_CONFIRMED if confirmed else STATE_PENDING,
            "kind": kind,
            "source_id": source_id,
            "hostname": str(hostname or "").strip(),
            "db_id": db_id if kind == KIND_LINK else "",
            "server_name": server_name if kind == KIND_LINK else "",
            "polestar_hostname": polestar_hostname if kind == KIND_LINK else "",
            "grade": grade if kind == KIND_LINK else "",
            "by": by,
            "by_role": by_role,
            "at": at,
            "investigation_id": investigation_id or "",
        }
        if kind == KIND_ABSENT:
            rec["expires_at"] = ((now or datetime.now(UTC)) + ABSENT_TTL).isoformat(
                timespec="seconds"
            )
        key = link_key(source_id, hostname)
        for attempt in range(1, _WRITE_ATTEMPTS + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    previous = _parse(await pipe.get(key))
                    if (
                        not confirmed
                        and previous is not None
                        and previous.get("state") == STATE_CONFIRMED
                        and not (previous.get("kind") == KIND_ABSENT and absent_expired(
                            previous, now or datetime.now(UTC)))
                    ):
                        logger.info("확정 대기 선택 — 기존 확정 연결 유지: key=%s by=%s", key, by)
                        return rec, False
                    deltas: dict[str, int] = {}
                    if previous is not None and previous.get("state") == STATE_CONFIRMED:
                        _add_deltas(deltas, previous, -1)
                    if confirmed:
                        _add_deltas(deltas, rec, 1)
                    deltas = {f: d for f, d in deltas.items() if d}  # 같은 연결 재확정은 0
                    pipe.multi()
                    pipe.set(key, json.dumps(rec, ensure_ascii=False))
                    for field, delta in deltas.items():
                        pipe.hincrby(PATTERNS_KEY, field, delta)
                    results = await pipe.execute()
            except WatchError:
                if attempt < _WRITE_ATTEMPTS:
                    continue
                logger.warning(
                    "호스트 연결 저장 실패 — 동시 쓰기 충돌 %d회: key=%s by=%s",
                    _WRITE_ATTEMPTS, key, by,
                )
                raise
            await self._drop_empty_patterns(list(deltas), results[1:])
            return rec, True
        raise AssertionError("unreachable")  # pragma: no cover — 루프는 return·raise로 끝난다

    async def approve(
        self,
        source_id: str,
        hostname: str,
        *,
        by: str,
        expect: dict[str, str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """확정 대기를 확정으로 올린다(없으면 None · 이미 확정이면 그대로 돌려준다).

        `expect`(`db_id`·`server_name`·`at`)가 있으면 현재 레코드의 같은 키와 비교해 다르면
        `HostLinkChangedError`를 낸다 — 관리자가 본 것과 다른 선택을 승인하지 않게 한다.
        비교와 저장은 키 WATCH 트랜잭션으로 묶인다 — 비교 뒤 저장 전에 다른 쓰기가 끼면 EXEC가
        무효가 되고 역시 `HostLinkChangedError`다(다시 시도하지 않는다 — 관리자가 새로 보고
        승인한다).

        Raises:
            HostLinkChangedError: `expect`와 현재 레코드가 다르거나, 비교 뒤 레코드가 바뀌었을 때
        """
        key = link_key(source_id, hostname)
        try:
            async with self._redis.pipeline(transaction=True) as pipe:
                await pipe.watch(key)
                rec = _parse(await pipe.get(key))
                if rec is None:
                    return None
                if expect is not None and any(
                    str(rec.get(k) or "") != str(v or "") for k, v in expect.items()
                ):
                    raise HostLinkChangedError(key)
                if rec.get("state") == STATE_CONFIRMED:
                    return rec
                rec["state"] = STATE_CONFIRMED
                rec["approved_by"] = by
                rec["approved_at"] = _now_iso(now)
                fields = _pattern_keys(rec)
                pipe.multi()
                pipe.set(key, json.dumps(rec, ensure_ascii=False))
                for field in fields:
                    pipe.hincrby(PATTERNS_KEY, field, 1)
                await pipe.execute()
        except WatchError:
            logger.info("호스트 연결 승인 — 비교 뒤 레코드가 바뀜: key=%s by=%s", key, by)
            raise HostLinkChangedError(key) from None
        return rec

    async def delete(self, source_id: str, hostname: str) -> dict[str, Any] | None:
        """연결을 지운다 → 지운 레코드(없으면 None). 확정 서버 연결이면 패턴 건수를 내린다.

        저장과 같은 WATCH 트랜잭션이다(충돌이면 짧게 다시 시도 · 끝내 충돌이면 `WatchError`).
        """
        key = link_key(source_id, hostname)
        for attempt in range(1, _WRITE_ATTEMPTS + 1):
            try:
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(key)
                    rec = _parse(await pipe.get(key))
                    if rec is None:
                        return None
                    fields = _pattern_keys(rec) if rec.get("state") == STATE_CONFIRMED else []
                    pipe.multi()
                    pipe.delete(key)
                    for field in fields:
                        pipe.hincrby(PATTERNS_KEY, field, -1)
                    results = await pipe.execute()
            except WatchError:
                if attempt < _WRITE_ATTEMPTS:
                    continue
                logger.warning(
                    "호스트 연결 삭제 실패 — 동시 쓰기 충돌 %d회: key=%s", _WRITE_ATTEMPTS, key
                )
                raise
            await self._drop_empty_patterns(fields, results[1:])
            return rec
        raise AssertionError("unreachable")  # pragma: no cover — 루프는 return·raise로 끝난다

    async def list_links(self) -> list[dict[str, Any]]:
        """모든 연결(관리자 목록 · 시각 최신 먼저). 만료된 부정 연결도 `expired=True`로 싣는다."""
        now = datetime.now(UTC)
        out: list[dict[str, Any]] = []
        async for raw_key in self._redis.scan_iter(match=f"{KEY_PREFIX}*"):
            key = _text(raw_key) or ""
            if key == PATTERNS_KEY:
                continue
            rec = await self._raw(key)
            if rec is None:
                continue
            if rec.get("kind") == KIND_ABSENT:
                rec["expired"] = absent_expired(rec, now)
            out.append(rec)
        out.sort(key=lambda r: str(r.get("approved_at") or r.get("at") or ""), reverse=True)
        return out

    async def patterns(self) -> dict[str, int]:
        """패턴 건수 `{키: 건수}`(0 이하는 뺀다)."""
        raw = await self._redis.hgetall(PATTERNS_KEY) or {}
        out: dict[str, int] = {}
        for k, v in raw.items():
            try:
                count = int(_text(v) or 0)
            except ValueError:
                continue
            if count > 0:
                out[_text(k) or ""] = count
        return out

    async def _drop_empty_patterns(self, fields: list[str], values: list[Any]) -> None:
        """트랜잭션 HINCRBY 결과가 0 이하인 패턴 필드를 지운다(best-effort).

        필드를 WATCH해 다시 읽고 여전히 0 이하일 때만 지운다 — 그 사이 다른 확정이 올린 건수를
        지우지 않는다. 충돌·오류면 남겨 둔다(`patterns()`가 0 이하를 뺀다).
        """
        for field, value in zip(fields, values):
            try:
                if int(value) > 0:
                    continue
                async with self._redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(PATTERNS_KEY)
                    current = _text(await pipe.hget(PATTERNS_KEY, field))
                    if current is not None and int(current) > 0:
                        continue
                    pipe.multi()
                    pipe.hdel(PATTERNS_KEY, field)
                    await pipe.execute()
            except (WatchError, ValueError):
                continue
            except Exception:  # noqa: BLE001 — 정리 실패는 건수 판독에 영향 없다(사유는 로그)
                logger.warning("패턴 필드 정리 실패: %s", field, exc_info=True)

    async def rate_hit(
        self, route: str, sub: str, *, limit: int, window_seconds: int, now: float | None = None
    ) -> int | None:
        """사용자별 고정 창 호출 상한 — 상한을 넘으면 창이 끝날 때까지 남은 초, 아니면 None.

        카운터 `hostlinkrate:{route}:{sub}:{창 번호}`를 INCR+EXPIRE 한 트랜잭션으로 올린다
        (거부된 호출도 센다 — 창이 끝나면 키가 만료된다). 여러 프로세스가 같은 Redis로 상한을
        공유한다. Redis 오류는 그대로 올린다(호출부가 in-process 카운터로 폴백한다).
        """
        t = time.time() if now is None else now
        window_no = int(t // window_seconds)
        key = f"{RATE_KEY_PREFIX}{route}:{sub}:{window_no}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds + 1)
            count, _ = await pipe.execute()
        if int(count) <= limit:
            return None
        return max(1, math.ceil((window_no + 1) * window_seconds - t))


def _add_deltas(deltas: dict[str, int], rec: dict[str, Any], delta: int) -> None:
    for field in _pattern_keys(rec):
        deltas[field] = deltas.get(field, 0) + delta


def build_host_link_store(config: Any, redis: Any = None) -> HostLinkStore | None:
    """플래그 on이면 저장소를 만든다(Redis 미주입이면 설정으로 연결을 만든다 · off면 None)."""
    ng = getattr(config, "noise_gate", None)
    if ng is None or not getattr(ng, "host_link_enabled", False):
        return None
    if redis is None:
        try:
            import redis.asyncio as aioredis

            redis = aioredis.from_url(
                f"redis://{config.redis.host}:{config.redis.port}",
                password=config.redis.password or None,
                db=config.redis.db,
                socket_timeout=_REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 — 연결 생성 실패는 저장소 없음으로(호출부가 사유를 남긴다)
            logger.warning("호스트 연결 저장소 Redis 연결 생성 실패", exc_info=True)
            return None
    return HostLinkStore(redis)


__all__ = [
    "ABSENT_TTL",
    "HostLinkChangedError",
    "HostLinkStore",
    "PATTERNS_KEY",
    "RATE_KEY_PREFIX",
    "build_host_link_store",
    "link_key",
]
