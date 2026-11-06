"""업무 서비스 Redis 읽기 사본·귀속 색인 (plans/215 §3.5 · §4.2 · D-368).

키(기록자는 API 프로세스뿐 — D-049 · 워커·수집기는 읽기만):
- `bizsvc:idx:sms` 해시 — 필드 = SMS 구성원 키(`sms_member_key`) → service_id
- `bizsvc:idx:host` 해시 — 필드 두 모양 → service_id(그 필드의 서버가 1대일 때만):
  `canonical_host_key`(전 DB 통틀어 1대 · db_id 없는 알람용) · `{db_id}|{canonical_host_key}`
  (그 DB 안에서 1대 · SMS DB 알람용 — 다른 DB의 같은 이름 서버로 귀속하지 않는다)
- `bizsvc:idx:apm` 해시 — 필드 = APM 인스턴스 구성원 키(`apm_instance_member_key`) → service_id
- `bizsvc:idx:apmdom` 해시 — 필드 `{source}|{domain}`(`apm_domain_member_key`) → service_id
- `bizsvc:svc:{id}` — 서비스 사본 JSON(표시 정보 + 활성 구성원 목록)
- `bizsvc:svcs` — 활성 서비스 id 집합

사본은 PG 정본의 활성 서비스(state=active)·활성 구성원(state=active)만 담는다(ambiguous·disabled
서비스와 excluded·target_gone 구성원은 귀속하지 않는다). 재구성은 한 트랜잭션(MULTI)으로 전체를
교체한다 — 읽는 쪽은 이전 사본이나 새 사본만 본다.

귀속(`attribute`)은 결정적이며 첫 성립에서 멈춘다(§4.2): 폴스타 연계 대상 → APM 인스턴스 → APM
도메인 → 호스트. 연계 대상이 서로 다른 서비스 2개 이상에 걸리면 고르지 않고 미상이다(D-224 ④).
호스트 단계는 알람 db_id(SMS DB일 때만 호출자가 넘긴다)가 있으면 `{db_id}|{호스트}` 필드만 본다.
Redis 미연결·키 없음·값 손상은 미상(None — 오귀속 0)이며 예외는 debug 로그로 남긴다.

redis.asyncio 클라이언트(bytes·str 응답 모두)를 주입받는다. 계층: infrastructure.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from src.domain.host_names import canonical_host_key

from noise_gate.domain.apm_host_map import SCOPE_INSTANCE, SCOPE_WORKLOAD, match_key_for
from noise_gate.domain.biz_service_keys import (
    KIND_APM_DOMAIN,
    KIND_APM_INSTANCE,
    KIND_SMS_SERVER,
    apm_domain_member_key,
    apm_instance_member_key,
    sms_member_key,
)
from noise_gate.infrastructure.apm_host_map_cache import connect_redis

logger = logging.getLogger(__name__)

KEY_PREFIX = "bizsvc:"
IDX_SMS_KEY = "bizsvc:idx:sms"
IDX_HOST_KEY = "bizsvc:idx:host"
IDX_APM_KEY = "bizsvc:idx:apm"
IDX_APMDOM_KEY = "bizsvc:idx:apmdom"
SVC_PREFIX = "bizsvc:svc:"
SVCS_KEY = "bizsvc:svcs"
INDEX_KEYS = (IDX_SMS_KEY, IDX_HOST_KEY, IDX_APM_KEY, IDX_APMDOM_KEY)

#: 귀속 근거.
BASIS_POLESTAR_LINK = "polestar_link"
BASIS_APM_INSTANCE = "apm_instance"
BASIS_APM_DOMAIN = "apm_domain"
BASIS_HOST = "host"

#: 서비스 사본 JSON에 싣는 서비스 칼럼(evidence·note 등 큰 칸은 뺀다).
SERVICE_COPY_FIELDS = (
    "id", "service_key", "display_name", "tier", "description", "owner_org", "origin",
    "state", "verified", "monitored", "version",
)
#: 서비스 사본 JSON의 구성원 칼럼.
MEMBER_COPY_FIELDS = (
    "id", "kind", "member_key", "db_id", "resource_id", "server_name", "hostname", "ip", "zone",
    "source_id", "domain_id", "domain_name", "instance_name", "prefix_code", "origin", "basis",
    "confidence", "layer", "layer_origin", "apm_map_id",
)


@dataclass(frozen=True)
class Attribution:
    """알람 1건의 서비스 귀속 결과 — `basis`는 성립한 단계(`BASIS_*`)."""

    service_id: int
    basis: str


class _ActiveSource(Protocol):
    async def active_services_with_members(self) -> list[dict[str, Any]]: ...


def _text(raw: Any) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", errors="replace")
    return str(raw)


def _service_id(raw: Any) -> int | None:
    text = _text(raw)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def svc_key(service_id: int) -> str:
    """서비스 사본 키 `bizsvc:svc:{id}`."""
    return f"{SVC_PREFIX}{int(service_id)}"


def _host_keys(member: Mapping[str, Any]) -> set[str]:
    keys = {canonical_host_key(member.get(f)) for f in ("hostname", "server_name")}
    keys.discard("")
    return keys


def host_db_field(db_id: Any, host: Any) -> str:
    """`idx:host`의 DB 한정 필드 `{db_id}|{canonical_host_key}`.

    어느 쪽이 비거나 `|`를 품으면 빈 값이다.
    """
    db = (_text(db_id) or "").strip()
    hk = canonical_host_key(_text(host))
    if not db or not hk or "|" in db or "|" in hk:
        return ""
    return f"{db}|{hk}"


def build_copy(
    services: Iterable[Mapping[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, str]]]:
    """활성 서비스(+`members`) → (서비스 사본 `{id: JSON용 dict}`, 색인 `{키: {필드: service_id}}`).

    `idx:host`는 정규 호스트 키 하나에 SMS 서버(구성원 키) 1대만 걸릴 때만 싣는다 — 전 DB 기준
    필드(`{호스트}`)와 DB 한정 필드(`{db_id}|{호스트}` · 그 DB 안에서 1대)를 함께 싣는다.
    """
    copies: dict[int, dict[str, Any]] = {}
    sms: dict[str, str] = {}
    apm: dict[str, str] = {}
    apmdom: dict[str, str] = {}
    host_servers: dict[str, set[str]] = {}
    host_service: dict[str, str] = {}
    for svc in services:
        sid = int(svc["id"])
        sid_text = str(sid)
        members = [dict(m) for m in svc.get("members") or []]
        copy = {f: svc.get(f) for f in SERVICE_COPY_FIELDS}
        copy["members"] = [{f: m.get(f) for f in MEMBER_COPY_FIELDS} for m in members]
        copies[sid] = copy
        for m in members:
            kind = m.get("kind")
            key = str(m.get("member_key") or "")
            if not key:
                continue
            if kind == KIND_SMS_SERVER:
                sms[key] = sid_text
                for hk in _host_keys(m):
                    for field_ in (hk, host_db_field(m.get("db_id"), hk)):
                        if field_:
                            host_servers.setdefault(field_, set()).add(key)
                            host_service[field_] = sid_text
            elif kind == KIND_APM_INSTANCE:
                apm[key] = sid_text
            elif kind == KIND_APM_DOMAIN:
                apmdom[key] = sid_text
    host = {hk: host_service[hk] for hk, servers in host_servers.items() if len(servers) == 1}
    return copies, {
        IDX_SMS_KEY: sms, IDX_HOST_KEY: host, IDX_APM_KEY: apm, IDX_APMDOM_KEY: apmdom,
    }


class RedisBizServiceCache:
    """업무 서비스 Redis 사본·귀속 색인."""

    def __init__(self, redis: Any) -> None:  # redis.asyncio.Redis
        """Redis 클라이언트를 받는다."""
        self._redis = redis

    @property
    def redis(self) -> Any:
        """주입받은 Redis 클라이언트(락·지표 키에 같은 연결을 쓴다)."""
        return self._redis

    # ── 쓰기(API 프로세스) ────────────────────────────────────────────
    async def sync_from_repo(self, repo: _ActiveSource) -> dict[str, int]:
        """PG 정본 → 사본·색인 전체 재구성(기동 · 적용 뒤) → 건수.

        한 트랜잭션(MULTI)으로 색인 4종·서비스 사본·id 집합을 교체하고 없어진 서비스 키를 지운다.
        Redis 오류는 올린다(호출부가 로그를 남긴다).
        """
        copies, indexes = build_copy(await repo.active_services_with_members())
        new_keys = {svc_key(sid) for sid in copies}
        stale: list[str] = []
        async for raw_key in self._redis.scan_iter(match=f"{SVC_PREFIX}*"):
            name = _text(raw_key) or ""
            if name not in new_keys:
                stale.append(name)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(*INDEX_KEYS, SVCS_KEY, *stale)
            for key, mapping in indexes.items():
                if mapping:
                    pipe.hset(key, mapping=mapping)
            for sid, copy in copies.items():
                pipe.set(svc_key(sid), _dumps(copy))
            if copies:
                pipe.sadd(SVCS_KEY, *(str(sid) for sid in copies))
            await pipe.execute()
        counts = {k[len(KEY_PREFIX):]: len(v) for k, v in indexes.items()}
        return {"services": len(copies), **counts}

    # ── 읽기(워커·API 어디서나) ───────────────────────────────────────
    async def get_service(self, service_id: int) -> dict[str, Any] | None:
        """서비스 사본 1건(없음·손상·Redis 오류면 None)."""
        try:
            raw = await self._redis.get(svc_key(service_id))
        except Exception:  # noqa: BLE001 — 사본 미상은 None(호출부가 사유를 낸다)
            logger.debug("biz_service 사본 조회 실패: id=%s", service_id, exc_info=True)
            return None
        text = _text(raw)
        if not text:
            return None
        try:
            value = json.loads(text)
        except ValueError:
            logger.debug("biz_service 사본 값 손상: id=%s", service_id)
            return None
        return value if isinstance(value, dict) else None

    async def list_service_ids(self) -> list[int]:
        """활성 서비스 id 목록(오름차순 · Redis 오류면 빈 목록)."""
        try:
            raw = await self._redis.smembers(SVCS_KEY)
        except Exception:  # noqa: BLE001 — 사본 미상은 빈 목록
            logger.debug("biz_service id 집합 조회 실패", exc_info=True)
            return []
        ids = {_service_id(v) for v in raw or ()}
        return sorted(i for i in ids if i is not None)

    async def attribute(
        self,
        *,
        polestar_link_targets: Sequence[tuple[Any, Any]] | None = None,
        apm: tuple[Any, Any, Any] | None = None,
        hostname: str | None = None,
        db_id: str | None = None,
    ) -> Attribution | None:
        """알람 1건 → 서비스 귀속(§4.2 순서 · 첫 성립에서 멈춤 · 미상이면 None).

        Args:
            polestar_link_targets: `meta.hints.polestar_link`의 `(db_id, resource_id)` 목록
            apm: 제니퍼 알람의 `(source_id, domain_id, instance_name)`
            hostname: 알람 호스트 이름(정규 호스트 키로 `idx:host`)
            db_id: SMS DB 알람의 db_id — 있으면 호스트 단계가 그 DB 구성원만 본다
                (`{db_id}|{호스트}` 필드 · 전 DB 기준 필드로 물러서지 않는다). APM 알람은 None.
        """
        try:
            return await self._attribute(polestar_link_targets, apm, hostname, db_id)
        except Exception:  # noqa: BLE001 — Redis 오류는 미상(오귀속 0)
            logger.debug("biz_service 귀속 조회 실패 — 미상으로 처리", exc_info=True)
            return None

    async def _attribute(
        self,
        targets: Sequence[tuple[Any, Any]] | None,
        apm: tuple[Any, Any, Any] | None,
        hostname: str | None,
        db_id: str | None,
    ) -> Attribution | None:
        fields = [k for k in (sms_member_key(d, r) for d, r in targets or ()) if k]
        if fields:
            values = await self._redis.hmget(IDX_SMS_KEY, fields)
            found = {sid for sid in (_service_id(v) for v in values or ()) if sid is not None}
            if len(found) > 1:
                return None  # 서로 다른 서비스 2개 이상 — 고르지 않는다(D-224 ④)
            if found:
                return Attribution(found.pop(), BASIS_POLESTAR_LINK)
        if apm is not None:
            source_id, domain_id, instance_name = apm
            keys = (
                apm_instance_member_key(
                    source_id, domain_id, scope, match_key_for(scope, instance_name)
                )
                for scope in (SCOPE_INSTANCE, SCOPE_WORKLOAD)
            )
            inst = [k for k in keys if k]
            if inst:
                for raw in await self._redis.hmget(IDX_APM_KEY, inst):
                    sid = _service_id(raw)
                    if sid is not None:
                        return Attribution(sid, BASIS_APM_INSTANCE)
            dom = apm_domain_member_key(source_id, domain_id)
            if dom:
                sid = _service_id(await self._redis.hget(IDX_APMDOM_KEY, dom))
                if sid is not None:
                    return Attribution(sid, BASIS_APM_DOMAIN)
        host = host_db_field(db_id, hostname) if _text(db_id) else canonical_host_key(hostname)
        if host:
            sid = _service_id(await self._redis.hget(IDX_HOST_KEY, host))
            if sid is not None:
                return Attribution(sid, BASIS_HOST)
        return None


__all__ = [
    "BASIS_APM_DOMAIN",
    "BASIS_APM_INSTANCE",
    "BASIS_HOST",
    "BASIS_POLESTAR_LINK",
    "IDX_APMDOM_KEY",
    "IDX_APM_KEY",
    "IDX_HOST_KEY",
    "IDX_SMS_KEY",
    "INDEX_KEYS",
    "MEMBER_COPY_FIELDS",
    "SERVICE_COPY_FIELDS",
    "SVCS_KEY",
    "SVC_PREFIX",
    "Attribution",
    "RedisBizServiceCache",
    "build_copy",
    "connect_redis",
    "host_db_field",
    "svc_key",
]
