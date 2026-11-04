"""APM·SMS 연계 매핑 진단 고속 경로 — 조회 순서·되적재 발행·수동 재분석 복원 (plans/210 · D-362).

`NOISE_APM_HOST_MAP_ENABLED`일 때 `_attach_polestar_link`(워커: 자동·사건 / API: 수동 재분석)가
이 모듈의 `resolve_map_link`로 `meta.hints.polestar_link`를 정한다. 첫 성립에서 멈춘다.

| 순 | 출처 | result |
|---|---|---|
| 1 | 매핑 manual·verified(instance → workload → domain) · disabled = `absent` | hit |
| 2 | 매핑 auto · instance | hit |
| 3 | 페이로드 `apm.host_address`(`NODE_ADDRESS_LINK_STATUSES`) → SMS IP 색인(1대일 때만) | index |
| 3' | 색인 miss → `node_address_link`(전 존 프로브) | live |
| 4 | 매핑 auto · workload → domain(`MapDecision.deferred` · 대상 ≤ 상한 전부) | hit |
| 5 | VM(파드 아님): 인스턴스 `ip`·`host_name` → SMS 색인 → miss면 `vm_exact_link` | index·live |
| 6 | D-348 저장 확정 연결(`host_link_enabled`일 때) | hit |
| 7 | 미확정 `unresolved_hint` + ITAM 생성 요청(auto · inflight 선점 시) | miss |

1·2·4는 사본 HMGET 1회로 판정한다. 3'·5 직접 조회 합계는 `apm_host_map_live_budget_seconds`
안이다(넘거나 예외면 사유 로그 후 다음 순 — 3'이 예외면 힌트 `node_address_status`에
`node_unchecked`를 싣는다). 판정마다 로그 1줄 `apm_map_lookup …`을 남긴다.

되적재(`apm_host_map_auto_enabled`일 때만): 3·3'·5 결과는 `learn`, 1·2·4 hit는 `touch`를
`apmhostmap:req`에 발행한다 — 워커는 PG에 연결하지 않는다(D-049 · API 소비자가 반영한다).

계층: application — domain(apm_host_map·host_link) + infrastructure(사본·스트림 헬퍼) + 같은
계층 `host_candidates`(직접 조회) 소비.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from src.domain.host_names import normalize_hostname

from noise_gate.domain import apm_host_map as hm
from noise_gate.domain.host_link import BASIS_NODE_ADDRESS, NODE_UNCHECKED, apm_source_id

logger = logging.getLogger(__name__)

#: 3·3'순에 쓰는 노드 주소 상태. 계획 표(§3.1)는 `ok`·`stale`이나 D-355 ⑦이 `stale` 자동 연결을
#: 금지하므로 `ok`만 둔다(계획 이탈 — 되돌리려면 이 집합에 `stale`을 더한다).
NODE_ADDRESS_LINK_STATUSES: frozenset[str] = frozenset({"ok"})
#: 힌트 `node_address_status`로 사유를 싣는 게이트웨이 상태(§3.6 알람 줄) + 키 없음(`missing`).
NODE_ADDRESS_REASON_STATUSES: frozenset[str] = frozenset(
    {"disabled", "login_failed", "login_blocked", "error"}
)
NODE_ADDRESS_MISSING = "missing"

RESULT_HIT = "hit"
RESULT_INDEX = "index"
RESULT_LIVE = "live"
RESULT_MISS = "miss"

#: 수동 재분석 복원용 판정 기록 칸(`apm_ref` · §3.8).
APM_REF_FIELDS: tuple[str, ...] = (
    "source_id", "domain_id", "domain_name", "instance_name", "ip", "host_address",
    "host_address_status",
)

StoredLinkFn = Callable[[], Awaitable[dict[str, Any]]]


# ── 설정 ────────────────────────────────────────────────────────────────
def map_enabled(cfg: Any) -> bool:
    """`apm_host_map_enabled`가 정확히 True인가(`MagicMock`·미설정은 off)."""
    return getattr(getattr(cfg, "noise_gate", None), "apm_host_map_enabled", False) is True


def auto_enabled(cfg: Any) -> bool:
    """`apm_host_map_auto_enabled`가 정확히 True인가."""
    return getattr(getattr(cfg, "noise_gate", None), "apm_host_map_auto_enabled", False) is True


def _max_targets(cfg: Any) -> int:
    value = getattr(getattr(cfg, "noise_gate", None), "apm_host_map_max_targets", None)
    return value if type(value) is int and value > 0 else hm.DEFAULT_MAX_TARGETS


def _budget_seconds(cfg: Any) -> float:
    value = getattr(getattr(cfg, "noise_gate", None), "apm_host_map_live_budget_seconds", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return 5.0
    return float(value)


# ── 이벤트 판독 ─────────────────────────────────────────────────────────
def apm_block(event: Any) -> dict[str, Any]:
    """원문 `apm` 블록(없으면 빈 dict)."""
    raw = getattr(event, "raw_payload", None)
    apm = raw.get("apm") if isinstance(raw, dict) else None
    return apm if isinstance(apm, dict) else {}


def node_address_status(apm: Mapping[str, Any]) -> str | None:
    """힌트에 실을 노드 주소 사유 — 호출 못 한 상태면 그 값, 키가 없으면 `missing`, 아니면 None."""
    if "host_address_status" not in apm:
        return NODE_ADDRESS_MISSING
    status = str(apm.get("host_address_status") or "").strip()
    if not status:
        return NODE_ADDRESS_MISSING
    return status if status in NODE_ADDRESS_REASON_STATUSES else None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def apm_ref_fields(event: Any) -> dict[str, Any]:
    """판정 기록 `apm_ref`(§3.8) — 값이 있는 칸만. APM 블록이 없으면 빈 dict.

    `ip`는 원문 `apm.ip`가 없으면 알람 `ipAddress`(인스턴스 IP)를 쓴다.
    """
    apm = apm_block(event)
    if not apm:
        return {}
    values: dict[str, Any] = {k: apm.get(k) for k in APM_REF_FIELDS}
    if values.get("ip") in (None, ""):
        values["ip"] = _text(getattr(event, "ip_address", ""))
    return {k: v for k, v in values.items() if v not in (None, "")}


# ── 직접 조회 예산 ──────────────────────────────────────────────────────
class _LiveBudget:
    """3'·5순 직접 조회 합계 예산 — 남은 시간 안에서만 기다린다."""

    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._deadline = clock() + seconds
        #: 예외로 끝난 단계(`step`) — 호출부가 힌트 사유로 옮긴다(침묵 폴백 금지).
        self.failed: set[str] = set()

    async def run(self, label: str, alarm_id: Any, factory: Callable[[], Awaitable[Any]]) -> Any:
        """예산 안에 끝나면 결과, 남은 예산이 없거나 넘거나 예외면 None(사유 로그).

        조회 예외(소스 목록 해석 실패 등)는 올리지 않는다 — 종전 노드 주소 경로
        (`_node_address_hint`의 `except Exception` → `node_unchecked`)와 같이 알람 처리를 막지
        않고 다음 순으로 간다. 예외 단계는 `failed`에 남는다.
        """
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            logger.info("apm_map 직접 조회 생략 — 예산 소진: alarm_id=%s step=%s", alarm_id, label)
            return None
        try:
            return await asyncio.wait_for(factory(), remaining)
        except TimeoutError:
            logger.warning(
                "apm_map 직접 조회 예산 초과 — 다음 순으로: alarm_id=%s step=%s budget_left=%.2fs",
                alarm_id, label, remaining,
            )
            return None
        except Exception as exc:  # noqa: BLE001 — 직접 조회 실패가 알람 처리를 막지 않는다
            self.failed.add(label)
            logger.warning(
                "apm_map 직접 조회 실패 — 다음 순으로: alarm_id=%s step=%s error=%s",
                alarm_id, label, type(exc).__name__, exc_info=True,
            )
            return None


# ── 되적재 발행 ─────────────────────────────────────────────────────────
async def _publish(redis: Any, message: dict[str, Any]) -> None:
    from noise_gate.infrastructure.apm_host_map_cache import publish_request

    if redis is None:
        logger.warning("apm_map 요청 발행 생략 — Redis 없음: type=%s", message.get("type"))
        return
    await publish_request(redis, message)


def _node_target(found: Mapping[str, Any], host_address: str) -> dict[str, Any]:
    return hm.make_target(
        db_id=_text(found.get("db_id")), server_name=_text(found.get("server_name")),
        hostname=_text(found.get("hostname")), ip=host_address,
        resource_id=_text(found.get("resource_id")),
    )


def _node_hint(target: Mapping[str, Any], host_address: str, at: str) -> dict[str, Any]:
    """3·3'순 linked — 현행 노드 주소 연결 모양(D-355) + `resource_id`(색인에 있을 때)."""
    hint: dict[str, Any] = {
        "status": "linked",
        "db_id": target["db_id"],
        "server_name": target["server_name"],
        "hostname": target.get("hostname", ""),
        "basis": BASIS_NODE_ADDRESS,
        "host_address": host_address,
        "at": at,
    }
    if target.get("resource_id"):
        hint["resource_id"] = target["resource_id"]
    return hint


def _vm_hint(target: Mapping[str, Any], at: str) -> dict[str, Any]:
    """5순 linked — 자동 연계(VM 일치) 힌트. 아직 저장 전이라 `map_id`는 0이다."""
    record = {"origin": hm.ORIGIN_AUTO, "basis": hm.BASIS_VM_EXACT, "id": 0}
    return hm.linked_hint(hm.MapPick(hm.SCOPE_INSTANCE, record, (dict(target),)), at=at)


# ── 조회 순서 ───────────────────────────────────────────────────────────
async def resolve_map_link(
    cfg: Any,  # AppConfig (덕 타이핑)
    event: Any,  # AlarmEvent
    hints: Mapping[str, Any],
    *,
    cache: Any = None,  # ApmHostMapCache | None
    redis: Any = None,  # redis.asyncio 클라이언트 | None — 되적재·inflight
    stored_link: StoredLinkFn | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """§3.1 조회 순서로 `polestar_link` 힌트를 정한다(첫 성립에서 멈춤 · 늘 dict를 돌려준다).

    Args:
        cache: 사본·색인(없으면 1·2·3·4·5의 색인 단계를 건너뛴다 — 오연결 없음)
        redis: 되적재·ITAM 요청 발행 연결(없으면 발행 생략 · 로그)
        stored_link: 6순 D-348 저장 연결 조회(호출부가 `host_link_enabled`일 때만 준다)
        clock: 단조 시계(테스트 주입점 · 예산·측정 로그)

    7순 사유:
        - 사본의 쓰지 못한 행이 `failed` → `apm_map_failed`, `ambiguous` → `apm_map_ambiguous`
          (5순 VM 대조가 2대 이상이어도 `apm_map_ambiguous`)
        - 그 밖: auto on이면 `apm_map_pending`(되적재·ITAM 생성 요청이 진행 중 — 다음 알람에 hit
          가능), auto off면 `apm_map_no_inventory`(자동 생성이 없어 관리자 수동 매핑만 남는다)
    """
    from noise_gate.application import host_candidates as hc
    from noise_gate.infrastructure.apm_host_map_cache import claim_inflight

    started = clock()
    apm = apm_block(event)
    alarm_id = getattr(event, "alarm_id", "")
    source_id = apm_source_id(_text(getattr(event, "db_id", "")), hints.get("source_id"))
    domain_id = _text(apm.get("domain_id") if apm.get("domain_id") is not None
                      else hints.get("domain_id"))
    domain_name = _text(apm.get("domain_name"))
    instance_name = _text(apm.get("instance_name")) or _text(getattr(event, "resource_name", ""))
    max_targets = _max_targets(cfg)
    auto = auto_enabled(cfg)
    nas = node_address_status(apm)
    at = datetime.now(UTC).isoformat()
    budget = _LiveBudget(_budget_seconds(cfg), clock)
    learnable = bool(instance_name) and hm.valid_key(
        source_id, domain_id, hm.SCOPE_INSTANCE, hm.instance_match_key(instance_name)
    )

    def _done(hint: dict[str, Any], order: str, result: str, scope: str = "-") -> dict[str, Any]:
        if nas and hint.get("status") in ("linked", "unresolved"):
            hint["node_address_status"] = nas
        logger.info(
            "apm_map_lookup source=%s order=%s scope=%s result=%s ms=%d",
            source_id or "-", order, scope, result, int((clock() - started) * 1000),
        )
        return hint

    async def _learn(targets: list[dict[str, Any]], basis: str, step: str,
                     state: str = hm.STATE_ACTIVE, **evidence: Any) -> None:
        if not auto or not learnable or not targets:
            return
        await _publish(redis, hm.learn_message(
            source_id=source_id, domain_id=domain_id, instance_name=instance_name, basis=basis,
            targets=targets, domain_name=domain_name, state=state,
            evidence={"step": step, **{k: v for k, v in evidence.items() if v}},
        ))

    async def _hit(pick: hm.MapPick, order: str) -> dict[str, Any]:
        if auto:
            map_id = int(pick.record.get("id") or 0)
            if map_id > 0:
                await _publish(redis, hm.touch_message(source_id=source_id, map_ids=[map_id]))
        return _done(hm.linked_hint(pick, at=at), order, RESULT_HIT, pick.scope)

    # ── 1·2·4: 사본 HMGET 1회 ─────────────────────────────────────────
    decision = hm.MapDecision(stage=hm.STAGE_MISS)
    if cache is not None and source_id and domain_id and instance_name:
        lookup = await cache.lookup(source_id, domain_id, instance_name)
        if lookup.error:
            logger.info("apm_map 사본 조회 miss: alarm_id=%s error=%s", alarm_id, lookup.error)
        decision = hm.decide_from_map(lookup.records, max_targets=max_targets)
    if decision.stage == hm.STAGE_ABSENT and decision.pick is not None:
        return _done(hm.absent_hint(), "1", RESULT_HIT, decision.pick.scope)
    if decision.stage == hm.STAGE_MANUAL and decision.pick is not None:
        return await _hit(decision.pick, "1")
    if decision.stage == hm.STAGE_AUTO_INSTANCE and decision.pick is not None:
        return await _hit(decision.pick, "2")

    # ── 3·3': 페이로드 노드 주소 ─────────────────────────────────────
    host_address = _text(apm.get("host_address"))
    status = _text(apm.get("host_address_status"))
    node_ok = False
    if status in NODE_ADDRESS_LINK_STATUSES and host_address:
        try:
            ipaddress.IPv4Address(host_address)  # 프로브 리터럴 이중 방어(D-355)
            node_ok = True
        except ValueError:
            logger.warning("apm_map 노드 주소 형식 불일치 — 3순 생략: alarm_id=%s", alarm_id)
    if node_ok:
        index_miss = True
        if cache is not None:
            idx = await cache.sms_by_ip(host_address)
            servers = hm.normalize_targets(list(idx.servers), None)
            if len(servers) == 1:
                target = {**servers[0], "ip": host_address}
                await _learn([target], hm.BASIS_NODE_ADDRESS, "3", host_address=host_address)
                return _done(_node_hint(target, host_address, at), "3", RESULT_INDEX)
            if len(servers) > 1:
                index_miss = False
                logger.info("apm_map SMS IP 색인 2대 이상 — 3순 불성립: alarm_id=%s n=%d",
                            alarm_id, len(servers))
        if index_miss:
            found = await budget.run(
                "3'", alarm_id, lambda: hc.node_address_link(cfg, host_address=host_address)
            )
            if isinstance(found, dict) and found.get("status") == "linked":
                target = _node_target(found, host_address)
                hint = _node_hint(target, host_address, at)
                unchecked = [
                    _text(u.get("db_id")) for u in found.get("unchecked") or []
                    if isinstance(u, dict) and u.get("db_id")
                ]
                if unchecked:
                    hint["unchecked_db_ids"] = ",".join(unchecked)
                await _learn([target], hm.BASIS_NODE_ADDRESS, "3'", host_address=host_address)
                return _done(hint, "3'", RESULT_LIVE)
            if isinstance(found, dict):
                logger.info("apm_map 노드 주소 직접 조회 미확정: alarm_id=%s reason=%s",
                            alarm_id, found.get("reason"))
            if "3'" in budget.failed and nas is None:
                # 종전 경로와 같은 어휘 — 노드 주소를 확인하지 못했다(`node_unchecked`)
                nas = NODE_UNCHECKED

    # ── 4: 자동 workload → domain ────────────────────────────────────
    if decision.deferred is not None:
        return await _hit(decision.deferred, "4")

    # ── 5: VM 인스턴스 IP·호스트명 ───────────────────────────────────
    vm_ambiguous = False
    if apm.get("pod") is not True and hm.workload_match_key(instance_name) is None:
        vm_ip = _text(apm.get("ip")) or _text(getattr(event, "ip_address", ""))
        vm_host = _text(apm.get("host_name")) or _text(getattr(event, "hostname", ""))
        vm = None
        vm_result = RESULT_INDEX
        if cache is not None and (vm_ip or normalize_hostname(vm_host)):
            ip_idx = await cache.sms_by_ip(vm_ip) if vm_ip else hm.IndexLookup()
            host_idx = await cache.sms_by_host(vm_host) if vm_host else hm.IndexLookup()
            vm = hm.vm_exact_match(ip_idx.servers, host_idx.servers, max_targets=max_targets)
            if vm.status == "none":
                vm = None
        if vm is None and (vm_ip or vm_host):
            vm_result = RESULT_LIVE
            out = await budget.run("5", alarm_id, lambda: hc.vm_exact_link(
                cfg, ip=vm_ip, host_name=vm_host, max_targets=max_targets,
            ))
            vm = out[0] if isinstance(out, tuple) else None
        if vm is not None and vm.status == "linked":
            await _learn(list(vm.targets), hm.BASIS_VM_EXACT, "5")
            return _done(_vm_hint(vm.targets[0], at), "5", vm_result)
        if vm is not None and vm.status == "ambiguous":
            vm_ambiguous = True
            await _learn(list(vm.targets), hm.BASIS_VM_EXACT, "5", state=hm.STATE_AMBIGUOUS)

    # ── 6: D-348 저장 확정 연결 ──────────────────────────────────────
    if stored_link is not None:
        stored = await stored_link()
        if stored.get("status") == "linked":
            return _done(stored, "6", RESULT_HIT)

    # ── 7: 미확정 ─────────────────────────────────────────────────────
    if decision.reason:
        reason = decision.reason
    elif vm_ambiguous:
        reason = hm.REASON_AMBIGUOUS
    else:
        reason = hm.REASON_PENDING if auto else hm.REASON_NO_INVENTORY
    if auto and redis is not None and source_id and domain_id:
        claimed = await claim_inflight(redis, source_id, domain_id)
        if claimed:
            await _publish(redis, hm.itam_request_message(
                source_id=source_id, domain_id=domain_id, domain_name=domain_name,
            ))
    hint = hm.unresolved_hint(reason)
    if apm.get("pod") is True:
        # D-355 ④ — 파드는 「서버 선택 대기」 UI를 열지 않는다(조사 쪽이 파드 문장으로 낸다)
        hint["pod"] = True
    return _done(hint, "7", RESULT_MISS)


# ── 수동 재분석 — `apm` 블록 복원 (§3.8) ───────────────────────────────
async def restore_apm_block(
    cfg: Any,  # AppConfig (덕 타이핑)
    decision: Mapping[str, Any],
    *,
    client: Any = None,  # ApmGatewayClient | None — None이면 설정으로 만든다
) -> dict[str, Any] | None:
    """판정 기록 → 이벤트 `raw_payload["apm"]` 복원(플래그 on에서만 부른다).

    `apm_ref`가 있으면 그대로 쓴다. 없으면(옛 기록) 제니퍼 알람(`db_id`가 `jennifer_<소스>`)일 때
    `resource_name`(인스턴스 이름)으로 게이트웨이 `apm_instance_map(query=…)`를 역조회해 이름이
    정확히 같은 1건을 쓴다. 역조회 실패·0건·2건 이상은 사유 로그 후 None(침묵 금지 · 매핑 없이
    직접 조회 경로로 진행).
    """
    ref = decision.get("apm_ref")
    if isinstance(ref, dict) and ref:
        return {k: ref[k] for k in APM_REF_FIELDS if k in ref}
    db_id = _text(decision.get("db_id"))
    instance_name = _text(decision.get("resource_name"))
    if not db_id.startswith("jennifer") or not instance_name:
        return None
    source_id = apm_source_id(db_id)
    if client is None:
        from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client

        client = build_apm_gateway_client(cfg)
    if client is None:
        logger.warning(
            "apm_ref 역조회 생략 — 게이트웨이 URL 미설정(MCP_SOURCE_ENDPOINTS의 apm): instance=%s",
            instance_name,
        )
        return None
    try:
        result = await client.apm_instance_map(query=instance_name, source_ids=[source_id])
    except Exception as exc:  # noqa: BLE001 — 역조회 실패가 재분석을 막지 않는다(사유는 로그)
        logger.warning("apm_ref 역조회 실패: instance=%s err=%s", instance_name, exc)
        return None
    wanted = normalize_hostname(instance_name)
    rows = [
        r for r in (result.get("rows") or []) if isinstance(r, dict)
        and _text(r.get("source_id")) == source_id
        and normalize_hostname(_text(r.get("instance_name"))) == wanted
    ]
    if len(rows) != 1:
        logger.warning(
            "apm_ref 역조회 미확정: instance=%s 일치=%d (0 또는 2건 이상 — 복원 안 함)",
            instance_name, len(rows),
        )
        return None
    row = rows[0]
    apm: dict[str, Any] = {
        "source_id": source_id,
        "domain_id": row.get("domain_id"),
        "domain_name": _text(row.get("domain_name")),
        "instance_name": _text(row.get("instance_name")),
        "ip": _text(row.get("ip_address")),
    }
    host_address = _text(row.get("host_address"))
    if host_address:
        # 게이트웨이가 지금 확인한 주소(`domain_addresses`)라 `ok`로 싣는다.
        apm["host_address"] = host_address
        apm["host_address_status"] = "ok"
    logger.info("apm_ref 역조회 복원: instance=%s domain=%s", instance_name, apm["domain_id"])
    return {k: v for k, v in apm.items() if v not in (None, "")}


__all__ = [
    "APM_REF_FIELDS",
    "NODE_ADDRESS_LINK_STATUSES",
    "NODE_ADDRESS_MISSING",
    "NODE_ADDRESS_REASON_STATUSES",
    "RESULT_HIT",
    "RESULT_INDEX",
    "RESULT_LIVE",
    "RESULT_MISS",
    "apm_block",
    "apm_ref_fields",
    "auto_enabled",
    "map_enabled",
    "node_address_status",
    "resolve_map_link",
    "restore_apm_block",
]
