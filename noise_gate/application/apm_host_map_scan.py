"""APM·SMS 연계 사전 스캔 서비스 · ITAM 비동기 요청 처리 (plans/210 §3.4 · §3.5 · D-362).

관리자 「지금 스캔」(W6 `POST /scan`)이 `ApmHostMapScanService.start_scan`을 부른다 — API 프로세스
백그라운드 태스크로 돌고(D-049 — PG 기록자는 API 프로세스뿐) `run_id`를 바로 돌려준다.

- **단독 실행**: Redis 락 `SET NX EX 3600`(실행 중이면 `ScanRunningError` → 409 `scan_running`) ·
  30초마다 연장 · 끝나면 해제. `NOISE_APM_HOST_MAP_AUTO_ENABLED` off면 `ScanDisabledError`.
- **스캐너**(APM 인벤토리 → 노드 주소 → SMS → ITAM → DPM 슬롯): 스캐너마다 격리(try) · 성공한
  스캐너의 스냅샷만 적재하고 「현재 스냅샷」 포인터를 바꾼다(`load_snapshot` → `swap_snapshot`).
  실패 스캐너는 이전 스냅샷을 그대로 두고, 그 스냅샷이 필요한 규칙만 건너뛴다. 일부 소스·존만
  실패하면 그 소스·존의 이전 스냅샷 행을 이어 적재한다.
- **일괄 생성**: node_address → vm_exact → itam(`plan_scan_proposals`) → `apply_auto_many`(행별 개별
  트랜잭션 · 관리자 행 불변 · scan_suggests) → `set_targets_gone`(끝까지 읽은 SMS 존만 — 미확인 존
  대상 불변) → 정리(`cleanup` — 180일 미관측 자동 행) → Redis 사본·SMS 색인 전체 재구성.
- **실행 기록**: `status`(ok·partial·failed — 취소면 aborted) · `scanners`(스캐너별 `{status, rows,
  seconds, error_kind, unchecked_zones, …}` — 스캐너가 끝날 때마다 갱신) · `link_summary`
  (`{new, changed, ambiguous, failed, gone, kept_manual, errors, skipped}`).

`handle_link_request`(별칭 `handle_itam_request`)는 알람 miss 때 워커가 보낸 `itam_request`(W3
소비자가 부른다)를 처리한다 — 그 도메인만 게이트웨이로 다시 읽어 plans/211 §9.2 순서(노드 주소 →
SMS 색인·직접 조회 → VM 일치 → 아무것도 못 이었을 때만 ITAM)로 잇는다. 계획(`plan_domain_link` ·
쓰기 0)과 저장(`apply_domain_link`)을 나눠 관리자 「다시 찾기」가 재사용한다. 노드 주소·VM 일치로
이은 도메인의 남은 자동 domain 행(failed·ambiguous)에는 「대체됨」 표지를 단다(`settle_superseded`
— 스캔 일괄 생성도 같다).

계층: application.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol

from noise_gate.domain.apm_host_map import (
    DEFAULT_MAX_TARGETS,
    MSG_ITAM_REQUEST,
    REASON_NO_INVENTORY,
    SCAN_ABORTED,
    SCAN_FAILED,
    SCOPE_DOMAIN,
    SCOPE_INSTANCE,
    SCOPE_WORKLOAD,
    STATE_ACTIVE,
    WRITE_INSERT,
    WRITE_SAME,
    WRITE_UPDATE,
    ApmHostMapCache,
    ApmHostMapRepository,
    AutoProposal,
    VmMatch,
    build_sms_index,
    instance_match_key,
    is_superseded,
    make_target,
    sms_host_servers,
    vm_exact_match,
    workload_match_key,
)
from noise_gate.domain.apm_host_map_itam import (
    DEFAULT_MAX_SERVERS,
    apps_from_rows,
    itam_proposal,
    resolve_itam_chain,
    servers_from_rows,
)
from noise_gate.domain.apm_host_map_scan import (
    NODE_STATUS_OK,
    NODE_SUMMARY_KEYS,
    RULE_ITAM,
    RULE_NODE_ADDRESS,
    RULE_VM_EXACT,
    SCANNER_APM_INVENTORY,
    SCANNER_DPM,
    SCANNER_FAILED,
    SCANNER_ITAM,
    SCANNER_NODE_ADDRESS,
    SCANNER_OK,
    SCANNER_SMS,
    SCANNER_USABLE,
    STAT_NODE_IP_AMBIGUOUS,
    STAT_NODE_IP_UNMATCHED,
    STAT_NODE_NOT_OK,
    STAT_VM_AMBIGUOUS,
    STAT_VM_LINKED,
    STAT_VM_TRIED,
    ItamSnapshot,
    ScannerResult,
    add_write_results,
    empty_link_summary,
    failed_result,
    plan_scan_proposals,
    present_servers,
    run_status,
)
from noise_gate.infrastructure.apm_gateway_client import ApmGatewayClient
from noise_gate.infrastructure.apm_host_map_scanners import (
    ERROR_NOT_CONFIGURED,
    ERROR_TOOL_UNREGISTERED,
    INSTANCE_MAP_TOOL,
    NODE_ADDRESS_TOOL,
    GatewayCaller,
    GatewayReadError,
    GatewayToolCaller,
    NodeAddresses,
    instance_key,
    inventory_row,
    merge_node_addresses,
    parse_node_addresses,
    read_all_rows,
    scan_dpm,
)

logger = logging.getLogger(__name__)

#: 스냅샷 표(저장소 `SNAPSHOT_COLUMNS` 키).
TABLE_APM = "apm_inventory"
TABLE_SMS = "sms_inventory"
TABLE_ITAM_APP = "itam_snapshot_app"
TABLE_ITAM_SERVER = "itam_snapshot_server"

#: 스캔 거부 사유 — 자동 생성 플래그 off.
REASON_AUTO_DISABLED = "apm_host_map_auto_disabled"
#: 락 수명·연장 간격(초).
LOCK_TTL_SECONDS = 3600
HEARTBEAT_SECONDS = 30.0
#: 스냅샷 적재 실패의 스캐너 `error_kind`.
ERROR_SNAPSHOT_STORE = "snapshot_store"
#: 연계 요청 처리의 게이트웨이 읽기 예산(초).
_REQUEST_READ = {"wait_seconds": 10.0, "call_timeout": 15.0, "deadline_seconds": 60.0}


class ScanRunningError(Exception):
    """다른 스캔이 실행 중이다(W6 → 409 `scan_running`)."""

    def __init__(self, run_id: str | None) -> None:
        """실행 중인 run_id(모르면 None)를 싣는다."""
        super().__init__(f"apm_host_map 스캔 실행 중(run_id={run_id})")
        self.run_id = run_id


class ScanDisabledError(Exception):
    """스캔을 시작할 수 없다 — `reason`(`apm_host_map_auto_disabled`)."""

    def __init__(self, reason: str) -> None:
        """사유 코드를 싣는다."""
        super().__init__(reason)
        self.reason = reason


class ScanLock(Protocol):
    """단독 실행 락(구현: `infrastructure.apm_host_map_scan_lock.RedisScanLock`)."""

    async def acquire(self, run_id: str, ttl_seconds: int = ...) -> bool: ...

    async def holder(self) -> str | None: ...

    async def extend(self, run_id: str, ttl_seconds: int = ...) -> bool: ...

    async def release(self, run_id: str) -> None: ...


ScannerFn = Callable[[], Awaitable[ScannerResult]]


@dataclass(frozen=True)
class ScanScanners:
    """스캐너 공급(실 배선은 `build_scan_service`)."""

    apm_inventory: ScannerFn
    node_address: ScannerFn
    sms: ScannerFn
    itam: ScannerFn
    dpm: ScannerFn = scan_dpm

    def ordered(self) -> list[tuple[str, ScannerFn]]:
        """실행 순서."""
        return [
            (SCANNER_APM_INVENTORY, self.apm_inventory),
            (SCANNER_NODE_ADDRESS, self.node_address),
            (SCANNER_SMS, self.sms),
            (SCANNER_ITAM, self.itam),
            (SCANNER_DPM, self.dpm),
        ]


def _strip(row: Mapping[str, Any]) -> dict[str, Any]:
    """이전 스냅샷 행 → 적재용(저장소 칸 `id`·`run_id` 제외)."""
    return {k: v for k, v in row.items() if k not in ("id", "run_id")}


class ApmHostMapScanService:
    """사전 스캔 실행기 — 한 API 프로세스에 하나(W6가 `app.state`에 둔다)."""

    def __init__(
        self,
        *,
        repo: ApmHostMapRepository,
        cache: ApmHostMapCache,
        lock: ScanLock,
        scanners: ScanScanners,
        auto_enabled: bool,
        max_targets: int = DEFAULT_MAX_TARGETS,
        retry_hours: int = 24,
        generic_terms: Collection[str] = (),
        itam_max_servers: int = DEFAULT_MAX_SERVERS,
        lock_ttl_seconds: int = LOCK_TTL_SECONDS,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        clock: Callable[[], datetime] | None = None,
        canonical: bool = False,
    ) -> None:
        """의존성과 설정(`NOISE_APM_HOST_MAP_*` 해석값 · `canonical` = SMS hostname 색인 키 공간
        `HOST_CANONICAL_MATCH_ENABLED`)을 받는다. `canonical`이면 일괄 생성이 이름 후보 행도 남긴다
        (plans/211 §10.4). Redis SMS 색인 재구성은 늘 현행 키다."""
        self._repo = repo
        self._cache = cache
        self._lock = lock
        self._scanners = scanners
        self._auto_enabled = auto_enabled is True
        self._max_targets = max(1, int(max_targets))
        self._retry_hours = int(retry_hours)
        self._generic_terms = tuple(generic_terms)
        self._itam_max_servers = max(1, int(itam_max_servers))
        self._lock_ttl = int(lock_ttl_seconds)
        self._heartbeat = float(heartbeat_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._canonical = canonical is True
        self._tasks: set[asyncio.Task[Any]] = set()

    # ── 시작 ───────────────────────────────────────────────────────────
    async def start_scan(self, *, requested_by: str) -> str:
        """스캔을 백그라운드로 시작하고 `run_id`를 돌려준다.

        Raises:
            ScanDisabledError: `NOISE_APM_HOST_MAP_AUTO_ENABLED` off(`apm_host_map_auto_disabled`)
            ScanRunningError: 다른 스캔이 락을 잡고 있다
            Exception: 락(Redis)·실행 기록(PG) 오류는 그대로 올린다(락은 풀고 올린다)
        """
        if not self._auto_enabled:
            raise ScanDisabledError(REASON_AUTO_DISABLED)
        run_id = uuid.uuid4().hex
        if not await self._lock.acquire(run_id, self._lock_ttl):
            raise ScanRunningError(await self._lock.holder())
        try:
            await self._repo.start_scan_run(requested_by=requested_by, run_id=run_id)
        except BaseException:
            await self._lock.release(run_id)
            raise
        task = asyncio.create_task(self.run_scan(run_id), name=f"apm-host-map-scan-{run_id[:8]}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        logger.info("APM·SMS 연계 스캔 시작: run_id=%s by=%s", run_id, requested_by)
        return run_id

    async def wait_idle(self) -> None:
        """진행 중인 스캔 태스크가 끝날 때까지 기다린다(테스트·종료용)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    # ── 본체 ───────────────────────────────────────────────────────────
    async def run_scan(self, run_id: str) -> dict[str, Any]:
        """스캔 본체(락은 잡혀 있어야 한다) → 최종 실행 기록 칸.

        Returns:
            `{status, scanners, link_summary}`
        """
        heartbeat = asyncio.create_task(self._extend_lock(run_id))
        results: dict[str, ScannerResult] = {}
        summary = empty_link_summary()
        status = SCAN_FAILED
        try:
            for name, scan in self._scanners.ordered():
                results[name] = await self._run_scanner(name, scan)
                await self._record(run_id, results)
            inventory = await self._store_inventory(run_id, results)
            sms_rows, scanned = await self._store_sms(run_id, results)
            itam = await self._store_itam(run_id, results)
            await self._generate(run_id, summary, results, inventory, sms_rows, scanned, itam)
            status = run_status(results)
        except asyncio.CancelledError:
            status = SCAN_ABORTED
            raise
        except Exception:  # noqa: BLE001 — 실행 기록에 failed로 남긴다(사유는 로그)
            logger.exception("APM·SMS 연계 스캔 실패: run_id=%s", run_id)
            status = SCAN_FAILED
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await heartbeat
            await self._finish(run_id, status, results, summary)
        return {"status": status, "scanners": self._records(results), "link_summary": summary}

    async def _run_scanner(self, name: str, scan: ScannerFn) -> ScannerResult:
        started = time.monotonic()
        try:
            result = await scan()
        except Exception as exc:  # noqa: BLE001 — 스캐너 하나 실패가 나머지를 막지 않는다
            logger.warning("APM·SMS 연계 스캔 — 스캐너 %s 예외: %s", name, type(exc).__name__,
                           exc_info=True)
            result = failed_result(type(exc).__name__)
        if not result.seconds:
            result.seconds = time.monotonic() - started
        return result

    @staticmethod
    def _records(results: Mapping[str, ScannerResult]) -> dict[str, Any]:
        return {name: r.record() for name, r in results.items()}

    async def _record(self, run_id: str, results: Mapping[str, ScannerResult]) -> None:
        """스캐너가 끝날 때마다 진행 기록(화면 폴링용 · 실패는 로그만)."""
        try:
            await self._repo.update_scan_run(run_id, scanners=self._records(results))
        except Exception:  # noqa: BLE001 — 진행 기록 실패가 스캔을 멈추지 않는다
            logger.warning("APM·SMS 연계 스캔 진행 기록 실패: run_id=%s", run_id, exc_info=True)

    async def _previous(self, table: str) -> list[dict[str, Any]]:
        try:
            return [_strip(r) for r in await self._repo.snapshot_rows(table)]
        except Exception:  # noqa: BLE001 — 이어 쓸 이전 행이 없을 뿐이다
            logger.warning("APM·SMS 연계 스캔 — 이전 스냅샷 읽기 실패: %s", table, exc_info=True)
            return []

    async def _store(self, run_id: str, tables: Mapping[str, list[dict[str, Any]]]) -> bool:
        """적재(전 표) 뒤 포인터 교체(전 표) — 실패면 False(이전 포인터 유지)."""
        try:
            for table, rows in tables.items():
                await self._repo.load_snapshot(table, run_id, rows)
            for table in tables:
                await self._repo.swap_snapshot(table, run_id)
        except Exception:  # noqa: BLE001 — 그 스캐너를 실패로 바꾸고 이전 스냅샷을 쓴다
            logger.warning("APM·SMS 연계 스캔 — 스냅샷 적재 실패: %s", list(tables),
                           exc_info=True)
            return False
        return True

    @staticmethod
    def _store_failed(result: ScannerResult) -> None:
        result.status = SCANNER_FAILED
        result.error_kind = ERROR_SNAPSHOT_STORE

    async def _store_inventory(
        self, run_id: str, results: Mapping[str, ScannerResult]
    ) -> list[dict[str, Any]] | None:
        """APM 인벤토리 + 노드 주소 칸 → `apm_inventory` 적재 → 행(쓸 수 없으면 None)."""
        inv = results[SCANNER_APM_INVENTORY]
        if inv.status not in SCANNER_USABLE:
            return None
        rows = [dict(r) for r in inv.data or []]
        node = results[SCANNER_NODE_ADDRESS]
        node_ok = node.status in SCANNER_USABLE
        previous: list[dict[str, Any]] = []
        if inv.unchecked_zones or not node_ok:
            previous = await self._previous(TABLE_APM)
        if inv.unchecked_zones:
            lost = set(inv.unchecked_zones)
            rows += [r for r in previous if str(r.get("source_id") or "") in lost]
        if node_ok:
            merge_node_addresses(rows, node.data or {})
        else:  # 노드 주소 스캐너 실패·꺼짐 — 이전 스냅샷의 노드 주소 칸을 이어 쓴다
            prior = {instance_key(p): p for p in previous}
            for row in rows:
                old = prior.get(instance_key(row))
                if old is not None:
                    row["host_address"] = old.get("host_address") or row.get("host_address") or ""
                    row["host_address_status"] = old.get("host_address_status") or ""
        if not await self._store(run_id, {TABLE_APM: rows}):
            self._store_failed(inv)
            return None
        return rows

    async def _store_sms(
        self, run_id: str, results: Mapping[str, ScannerResult]
    ) -> tuple[list[dict[str, Any]] | None, list[str]]:
        """SMS → `sms_inventory` 적재 → (행, 끝까지 읽은 존) · 쓸 수 없으면 (None, [])."""
        sms = results[SCANNER_SMS]
        if sms.status not in SCANNER_USABLE:
            return None, []
        data = sms.data or {}
        rows = [dict(r) for r in data.get("rows") or []]
        scanned = [str(d) for d in data.get("scanned") or []]
        if sms.unchecked_zones:
            lost = set(sms.unchecked_zones)
            rows += [r for r in await self._previous(TABLE_SMS)
                     if str(r.get("db_id") or "") in lost]
        if not await self._store(run_id, {TABLE_SMS: rows}):
            self._store_failed(sms)
            return None, []
        return rows, scanned

    async def _store_itam(
        self, run_id: str, results: Mapping[str, ScannerResult]
    ) -> ItamSnapshot | None:
        """ITAM → 업무·서버 스냅샷 적재 → 사슬 입력(쓸 수 없으면 None)."""
        itam = results[SCANNER_ITAM]
        if itam.status != SCANNER_OK:
            return None
        data = itam.data or {}
        apps = [dict(r) for r in data.get("apps") or []]
        servers = [dict(r) for r in data.get("servers") or []]
        if not await self._store(run_id, {TABLE_ITAM_APP: apps, TABLE_ITAM_SERVER: servers}):
            self._store_failed(itam)
            return None
        return ItamSnapshot(
            apps=apps_from_rows(apps), servers=servers_from_rows(servers),
            generic_terms=self._generic_terms, max_servers=self._itam_max_servers,
        )

    async def _generate(
        self,
        run_id: str,
        summary: dict[str, Any],
        results: Mapping[str, ScannerResult],
        inventory: list[dict[str, Any]] | None,
        sms_rows: list[dict[str, Any]] | None,
        scanned: list[str],
        itam: ItamSnapshot | None,
    ) -> None:
        """일괄 생성(§3.5 표) — 결과는 `summary`에 더한다."""
        if inventory is None or sms_rows is None:
            summary["skipped"] = [RULE_NODE_ADDRESS, RULE_VM_EXACT, RULE_ITAM]
        else:
            use_node = results[SCANNER_NODE_ADDRESS].status in SCANNER_USABLE
            if not use_node:
                summary["skipped"].append(RULE_NODE_ADDRESS)
            if itam is None:
                summary["skipped"].append(RULE_ITAM)
            by_ip, by_host = build_sms_index(sms_rows, canonical=self._canonical)
            stats: dict[str, int] = {}
            plan = plan_scan_proposals(
                inventory, by_ip, by_host, use_node_address=use_node, itam=itam,
                max_targets=self._max_targets, evidence={"run_id": run_id},
                canonical=self._canonical, stats=stats,
            )
            for key in NODE_SUMMARY_KEYS:
                summary[key] = int(summary.get(key) or 0) + stats.get(key, 0)
            now = self._clock()
            for source_id, proposals in plan.items():
                written = await self._repo.apply_auto_many(
                    source_id, proposals, now=now, retry_hours=self._retry_hours,
                )
                add_write_results(summary, proposals, written)
                await settle_superseded(
                    self._repo, source_id, list(zip(proposals, written, strict=False)), at=now,
                    extra={"source": "scan", "run_id": run_id},
                )
        if sms_rows is not None and scanned:
            summary["gone"] = await self._repo.set_targets_gone(present_servers(sms_rows),
                                                                scanned)

    async def _finish(
        self,
        run_id: str,
        status: str,
        results: Mapping[str, ScannerResult],
        summary: Mapping[str, Any],
    ) -> None:
        """정리·사본 재구성·실행 기록 마감·락 해제(각각 격리)."""
        try:
            await self._repo.cleanup(now=self._clock())
        except Exception:  # noqa: BLE001
            logger.warning("APM·SMS 연계 스캔 — 정리 실패: run_id=%s", run_id, exc_info=True)
        try:
            await self._cache.rebuild(await self._repo.all_rows())
            by_ip, by_host = build_sms_index(await self._repo.snapshot_rows(TABLE_SMS),
                                             canonical=self._canonical)
            await self._cache.rebuild_sms_index(by_ip, by_host)
        except Exception:  # noqa: BLE001 — 주기 대조(10분)가 다시 맞춘다
            logger.warning("APM·SMS 연계 스캔 — 사본 재구성 실패: run_id=%s", run_id,
                           exc_info=True)
        try:
            await self._repo.update_scan_run(
                run_id, status=status, scanners=self._records(results),
                link_summary=dict(summary), finished=True,
            )
        except Exception:  # noqa: BLE001
            logger.warning("APM·SMS 연계 스캔 — 실행 기록 마감 실패: run_id=%s", run_id,
                           exc_info=True)
        try:
            await self._lock.release(run_id)
        except Exception:  # noqa: BLE001 — 락은 수명(1시간) 뒤 풀린다
            logger.warning("APM·SMS 연계 스캔 — 락 해제 실패: run_id=%s", run_id, exc_info=True)
        logger.info("APM·SMS 연계 스캔 끝: run_id=%s status=%s summary=%s", run_id, status,
                    dict(summary))

    async def _extend_lock(self, run_id: str) -> None:
        """30초마다 락 연장(놓쳤으면 경고 — 스캔은 계속한다)."""
        while True:
            await asyncio.sleep(self._heartbeat)
            try:
                if not await self._lock.extend(run_id, self._lock_ttl):
                    logger.warning("APM·SMS 연계 스캔 락을 놓침: run_id=%s", run_id)
            except Exception:  # noqa: BLE001
                logger.warning("APM·SMS 연계 스캔 락 연장 실패: run_id=%s", run_id,
                               exc_info=True)


# ── 연계 요청 (알람 miss → 노드 주소 → VM 일치 → ITAM · plans/211 §9.2) ──
#: 노드 주소 조회 사유(`DomainLinkPlan.node_reason` · 단계별 결과 `tried["node_address"]`).
NODE_REASON_OK = "ok"
NODE_REASON_DISABLED = "node_address_disabled"
#: 조회 실패 머리 — 값은 `node_address_error:<kind>`.
NODE_REASON_ERROR = "node_address_error"
#: ITAM 단계를 건너뛴 사유(`DomainLinkPlan.itam_skip`).
ITAM_SKIP_LINKED = "linked"
ITAM_SKIP_RETRY_WAIT = "retry_wait"
ITAM_SKIP_NO_INVENTORY = "no_inventory"
#: 직접 조회(N3·V) 합계 예산 기본값(초 · `NOISE_APM_HOST_MAP_LIVE_BUDGET_SECONDS`).
DEFAULT_LIVE_BUDGET_SECONDS = 5.0
#: 단계별 결과 키.
TRIED_NODE = "node_address"
TRIED_VM = "vm_exact"
TRIED_ITAM = "itam"
#: 게이트웨이 `apm_node_addresses`의 `instance_ids` 1회 상한.
_NODE_IDS_PER_CALL = 50
#: 연계 요청이 만든 제안의 evidence `source`.
_LINK_SOURCE = "link_request"

#: 노드 IP(IPv4) → `node_address_link` 판정 dict(`status=linked`면 `db_id`·`server_name`…).
NodeLinkFn = Callable[[str], Awaitable[Mapping[str, Any]]]
#: `(ip, host_name)` → `VmMatch` 또는 `(VmMatch, unchecked)`(`vm_exact_link` 반환 그대로).
VmLinkFn = Callable[[str, str], Awaitable[Any]]
#: ITAM 사슬 입력 공급(스냅샷이 없으면 None — I 단계만 건너뛴다).
ItamSourceFn = Callable[[], Awaitable[ItamSnapshot | None]]


def _retry_at(row: Mapping[str, Any] | None) -> datetime | None:
    """행의 `next_retry_at`(ISO 문자열·datetime) → 시각대 있는 datetime. 없거나 읽지 못하면 None."""
    raw = (row or {}).get("next_retry_at")
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            value = datetime.fromisoformat(raw.strip())
        except ValueError:
            logger.warning("APM·SMS 연계 next_retry_at 형식 오류 — 무시: %r", raw)
            return None
    else:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _as_caller(gateway_client: GatewayCaller | ApmGatewayClient) -> GatewayCaller:
    return (
        GatewayToolCaller(gateway_client) if isinstance(gateway_client, ApmGatewayClient)
        else gateway_client
    )


def _str(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _ipv4(value: Any) -> str | None:
    """IPv4 정규 표기(아니면 None) — 직접 조회 리터럴 이중 방어(D-355)."""
    try:
        return str(ipaddress.IPv4Address(_str(value)))
    except ValueError:
        return None


def _any_ip(value: Any) -> str | None:
    try:
        return str(ipaddress.ip_address(_str(value)))
    except ValueError:
        return None


@dataclass
class DomainLinkPlan:
    """도메인 1개의 연계 계획(쓰기 0) — `apply_domain_link`가 저장한다.

    - `proposals`: 노드 주소·VM 일치 제안(instance·workload — `plan_scan_proposals` 결과)
    - `itam`: ITAM 사슬 domain 행 제안(I 단계를 돌렸을 때만)
    - `linked`: 노드 주소·VM 일치로 `active`로 이은 인스턴스 수
    - `tried`: 단계별 결과 `{node_address, vm_exact, itam}` — 사람이 읽는 한 줄씩
      (예: `{"node_address": "ok 2/3 · unmatched 1", "vm_exact": "-", "itam": "skipped:linked"}`)
    - `node_reason`: `ok` · `node_address_disabled` · `node_address_error:<kind>`
    - `itam_skip`: I 단계를 건너뛴 사유(`linked`·`retry_wait`·`no_inventory`) 또는 None
    """

    source_id: str
    domain_id: str
    domain_name: str
    proposals: list[AutoProposal] = field(default_factory=list)
    itam: AutoProposal | None = None
    linked: int = 0
    tried: dict[str, str] = field(default_factory=dict)
    node_reason: str = NODE_REASON_OK
    itam_skip: str | None = None


class _LinkBudget:
    """N3·V 직접 조회 합계 예산 — 남은 시간 안에서만 기다린다(초과·예외는 사유 로그 후 None)."""

    def __init__(self, seconds: float, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._deadline = clock() + max(0.0, float(seconds))

    async def run(self, label: str, scope: str, factory: Callable[[], Awaitable[Any]]
                  ) -> tuple[Any, bool]:
        """`(결과, 성공)` — 예산 소진·초과·예외면 `(None, False)`."""
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            logger.info("APM·SMS 연계 직접 조회 생략 — 예산 소진: %s step=%s", scope, label)
            return None, False
        try:
            return await asyncio.wait_for(factory(), remaining), True
        except TimeoutError:
            logger.warning("APM·SMS 연계 직접 조회 예산 초과 — 다음 단계로: %s step=%s "
                           "budget_left=%.2fs", scope, label, remaining)
        except Exception as exc:  # noqa: BLE001 — 직접 조회 실패가 다음 단계를 막지 않는다
            logger.warning("APM·SMS 연계 직접 조회 실패 — 다음 단계로: %s step=%s error=%s",
                           scope, label, type(exc).__name__, exc_info=True)
        return None, False


async def read_domain_instances(
    caller: GatewayCaller,
    source_id: str,
    domain_id: str,
    *,
    read_kwargs: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """`apm_instance_map(domain_id, source_ids=[src])` → 그 소스·도메인의 인스턴스 행.

    Raises:
        GatewayReadError: 게이트웨이 읽기 실패
    """
    _env, rows = await read_all_rows(
        caller, INSTANCE_MAP_TOOL, {"domain_id": int(domain_id), "source_ids": [source_id]},
        **{**_REQUEST_READ, **dict(read_kwargs or {})},
    )
    return [
        r for r in rows
        if _str(r.get("source_id")) == source_id and _str(r.get("domain_id")) == domain_id
    ]


def _node_source_reason(env: Mapping[str, Any], source_id: str) -> str:
    """봉투의 소스별 노드 주소 상태 → 사유(ok·partial이면 ok)."""
    for entry in env.get("node_address_sources") or []:
        if not isinstance(entry, Mapping) or _str(entry.get("source_id")) != source_id:
            continue
        status = _str(entry.get("status"))
        if status in ("", "ok", "partial"):
            return NODE_REASON_OK
        if status == "disabled":
            return NODE_REASON_DISABLED
        return f"{NODE_REASON_ERROR}:{status}"
    return NODE_REASON_OK


async def fetch_node_addresses(
    caller: GatewayCaller,
    source_id: str,
    domain_id: str,
    *,
    instance_ids: Sequence[Any] | None = None,
    read_kwargs: Mapping[str, Any] | None = None,
) -> tuple[NodeAddresses, str]:
    """노드 주소 N1 — `apm_node_addresses(source_ids=[src], domain_ids=[dom][, instance_ids])`.

    `instance_ids`는 50개씩 나눠 부른다(게이트웨이 상한). 예외를 올리지 않는다.

    Returns:
        `({instance_key: (host_address, status)}, 사유)` — 사유는 `ok` · `node_address_disabled`
        (도구 미등록 · 소스 꺼짐) · `node_address_error:<kind>`(읽기 실패 · 소스 로그인 실패 등).
    """
    base: dict[str, Any] = {"source_ids": [source_id], "domain_ids": [int(domain_id)]}
    batches: list[dict[str, Any]] = [base]
    if instance_ids:
        ids = [int(i) for i in (_str(v) for v in instance_ids) if i.isdigit()]
        if not ids:
            return {}, f"{NODE_REASON_ERROR}:invalid_instance_ids"
        batches = [
            {**base, "instance_ids": ids[i:i + _NODE_IDS_PER_CALL]}
            for i in range(0, len(ids), _NODE_IDS_PER_CALL)
        ]
    found: NodeAddresses = {}
    reason = NODE_REASON_OK
    for args in batches:
        try:
            env, rows = await read_all_rows(
                caller, NODE_ADDRESS_TOOL, args, **{**_REQUEST_READ, **dict(read_kwargs or {})},
            )
        except GatewayReadError as e:
            kind = NODE_REASON_DISABLED if e.kind == ERROR_TOOL_UNREGISTERED else (
                f"{NODE_REASON_ERROR}:{e.kind}")
            logger.info("APM·SMS 연계 노드 주소 조회 불가: source=%s domain=%s %s",
                        source_id, domain_id, kind)
            return found, kind
        except Exception as exc:  # noqa: BLE001 — 노드 주소 실패는 V·I 단계를 막지 않는다
            logger.warning("APM·SMS 연계 노드 주소 조회 예외: source=%s domain=%s %s",
                           source_id, domain_id, type(exc).__name__, exc_info=True)
            return found, f"{NODE_REASON_ERROR}:{type(exc).__name__}"
        found.update(parse_node_addresses(rows))
        if reason == NODE_REASON_OK:
            reason = _node_source_reason(env, source_id)
    return found, reason


def _inventory(instances: Iterable[Mapping[str, Any]], source_id: str, domain_id: str
               ) -> list[dict[str, Any]]:
    """인스턴스 행(게이트웨이 원형 또는 스냅샷 행) → 그 도메인의 인벤토리 행(상태 칸은 보존)."""
    out: list[dict[str, Any]] = []
    for raw in instances:
        row = inventory_row(raw)
        if not row["instance_name"] or (row["source_id"], row["domain_id"]) != (
                source_id, domain_id):
            continue
        row["host_address_status"] = _str(raw.get("host_address_status"))
        out.append(row)
    return out


def _node_ok(row: Mapping[str, Any]) -> bool:
    return (row.get("host_address_status") == NODE_STATUS_OK
            and _any_ip(row.get("host_address")) is not None)


def _node_target(found: Mapping[str, Any], host_address: str) -> dict[str, Any]:
    return make_target(
        db_id=_str(found.get("db_id")), server_name=_str(found.get("server_name")),
        hostname=_str(found.get("hostname")), ip=host_address,
        resource_id=_str(found.get("resource_id")),
    )


def _join(head: str, counts: Iterable[tuple[str, int]], extra: Iterable[str] = ()) -> str:
    """단계별 결과 한 줄 — `머리 · 이름 건수(0 제외) · 직접 조회 조각`."""
    return " · ".join([head, *(f"{k} {n}" for k, n in counts if n), *extra])


def _live_parts(hit: int, tried: int, failed: int, off: int) -> list[str]:
    """직접 조회 조각 — 시도했으면 `live 성공/시도` · 실패(`live_failed`) · 꺼짐(`live_off`)."""
    out = [f"live {hit}/{tried}"] if tried else []
    if failed:
        out.append(f"live_failed {failed}")
    if off:
        out.append(f"live_off {off}")
    return out


async def plan_domain_link(
    caller: GatewayCaller | None,
    source_id: str,
    domain_id: str,
    instances: Sequence[Mapping[str, Any]],
    *,
    by_ip: Mapping[str, Sequence[Mapping[str, Any]]],
    by_host: Mapping[str, Sequence[Mapping[str, Any]]],
    itam_source: ItamSourceFn | None = None,
    itam_skip: str | None = None,
    node_link: NodeLinkFn | None = None,
    vm_link: VmLinkFn | None = None,
    live_budget_seconds: float = DEFAULT_LIVE_BUDGET_SECONDS,
    max_targets: int = DEFAULT_MAX_TARGETS,
    canonical: bool = False,
    instance_ids: Sequence[Any] | None = None,
    read_kwargs: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    domain_name: str = "",
    name_candidates: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> DomainLinkPlan:
    """도메인 1개의 연계 계획 — §9.2 순서(N1 → N2 → N3 → V → I) · **저장하지 않는다**.

    - N1: 노드 주소(`ok`)가 없는 인스턴스가 있으면 `fetch_node_addresses` 1회(`instance_ids`를 주면
      그 인스턴스만) — 결과는 `instance_key`로 합친다. 도구가 없으면 `node_address_disabled`.
    - N2: 노드 IP → SMS IP 색인 1대 · N3: 색인 miss면 `node_link`(None이면 건너뜀 — `live_off`).
    - V: 노드 주소로 잇지 못한 파드 아닌 인스턴스 — 색인 0대면 `vm_link`(None이면 건너뜀).
    - N2·V 제안은 `plan_scan_proposals`(ITAM 제외) 재사용 — 노드 주소 상태는 `ok`만 · 2대 이상은
      고르지 않는다(D-355 ⑦ · D-224 ④).
    - `name_candidates`(관리자 다시 찾기만 · `canonical`과 함께): N·V로 잇지 못한 VM 인스턴스에 L3
      이름 후보 제안(`ambiguous` · plans/211 §10.4). 알람 miss 요청 처리는 늘 끈다.
    - I: N·V로 하나도 잇지 못했고 `itam_skip`이 없을 때만 `itam_source()`로 ITAM 사슬(스냅샷이
      없으면 `no_inventory`로 건너뜀).
    N3·V 직접 조회 합계는 `live_budget_seconds` 안 — 초과·예외는 사유 로그 후 다음 단계다.
    """
    scope = f"source={source_id} domain={domain_id}"
    base = dict(evidence or {})
    inventory = _inventory(instances, source_id, domain_id)
    name = next((r["domain_name"] for r in inventory if r["domain_name"]), "") or _str(domain_name)
    cap = max(1, int(max_targets))

    # N1 — 노드 주소(ok)가 없는 인스턴스가 있을 때만 묻는다
    node_reason = NODE_REASON_OK
    if any(not _node_ok(r) for r in inventory):
        if caller is None:
            node_reason = f"{NODE_REASON_ERROR}:{ERROR_NOT_CONFIGURED}"
        else:
            found, node_reason = await fetch_node_addresses(
                caller, source_id, domain_id, instance_ids=instance_ids, read_kwargs=read_kwargs,
            )
            merge_node_addresses(inventory, found)

    budget = _LinkBudget(live_budget_seconds, clock)
    live = dict.fromkeys(("n_hit", "n_tried", "n_failed", "n_off",
                          "v_hit", "v_tried", "v_failed", "v_off"), 0)

    # N3 — 노드 IP가 SMS 색인에 없으면 직접 조회(IP마다 1회)
    node_extra: dict[str, list[dict[str, Any]]] = {}
    misses = list(dict.fromkeys(
        ip for ip in (_ipv4(r.get("host_address")) for r in inventory if _node_ok(r))
        if ip and not by_ip.get(ip)
    ))
    for ip in misses:
        if node_link is None:
            live["n_off"] += 1
            continue
        live["n_tried"] += 1
        hit, ok = await budget.run("N3", scope, partial(node_link, ip))
        if not ok:
            live["n_failed"] += 1
        elif isinstance(hit, Mapping) and hit.get("status") == "linked":
            node_extra[ip] = [_node_target(hit, ip)]
            live["n_hit"] += 1
        else:
            logger.info("APM·SMS 연계 노드 주소 직접 조회 미확정: %s reason=%s", scope,
                        (hit or {}).get("reason") if isinstance(hit, Mapping) else None)

    # V — 노드 주소로 잇지 못한 파드 아닌 인스턴스: 색인 0대면 직접 조회
    vm_extra: dict[tuple[str, str, str], VmMatch] = {}
    for row in inventory:
        key = instance_match_key(row["instance_name"])
        if not key or workload_match_key(row["instance_name"]) is not None:
            continue
        node_ip = _any_ip(row.get("host_address")) if _node_ok(row) else None
        if node_ip and vm_exact_match(by_ip.get(node_ip) or node_extra.get(node_ip), None,
                                      max_targets=cap).status == "linked":
            continue
        vip, host = _any_ip(row.get("ip")), _str(row.get("host_name"))
        if not vip and not host:
            continue
        indexed = vm_exact_match(by_ip.get(vip) if vip else None,
                                 sms_host_servers(by_host, host, canonical=canonical),
                                 max_targets=cap)
        if indexed.status != "none":
            continue
        if vm_link is None:
            live["v_off"] += 1
            continue
        live["v_tried"] += 1
        out, ok = await budget.run("V", scope, partial(vm_link, vip or "", host))
        match = out[0] if isinstance(out, tuple) and out else out
        if not ok:
            live["v_failed"] += 1
        elif isinstance(match, VmMatch) and match.state is not None:
            vm_extra[(source_id, domain_id, key)] = match
            live["v_hit"] += 1

    stats: dict[str, int] = {}
    planned = plan_scan_proposals(
        inventory, by_ip, by_host, use_node_address=True, itam=None, max_targets=cap,
        evidence=base, canonical=canonical, node_extra=node_extra, vm_extra=vm_extra,
        stats=stats, evidence_source=_LINK_SOURCE, name_candidates=name_candidates,
    )
    proposals = planned.get(source_id, [])
    linked_rows = [p for p in proposals if p.scope == SCOPE_INSTANCE and p.state == STATE_ACTIVE]
    by_node = sum(1 for p in linked_rows if p.basis == RULE_NODE_ADDRESS)
    total = sum(1 for r in inventory if instance_match_key(r["instance_name"]))

    tried: dict[str, str] = {}
    if node_reason != NODE_REASON_OK:
        tried[TRIED_NODE] = node_reason
    else:
        tried[TRIED_NODE] = _join(f"ok {by_node}/{total}", [
            ("unmatched", stats.get(STAT_NODE_IP_UNMATCHED, 0)),
            ("ambiguous", stats.get(STAT_NODE_IP_AMBIGUOUS, 0)),
            ("not_ok", stats.get(STAT_NODE_NOT_OK, 0)),
        ], _live_parts(live["n_hit"], live["n_tried"], live["n_failed"], live["n_off"]))
    vm_tried = stats.get(STAT_VM_TRIED, 0)
    tried[TRIED_VM] = "-" if not vm_tried else _join(
        f"linked {stats.get(STAT_VM_LINKED, 0)}/{vm_tried}",
        [("ambiguous", stats.get(STAT_VM_AMBIGUOUS, 0))],
        _live_parts(live["v_hit"], live["v_tried"], live["v_failed"], live["v_off"]),
    )

    plan = DomainLinkPlan(source_id=source_id, domain_id=domain_id, domain_name=name,
                          proposals=list(proposals), linked=len(linked_rows), tried=tried,
                          node_reason=node_reason)
    skip = ITAM_SKIP_LINKED if linked_rows else itam_skip
    snapshot: ItamSnapshot | None = None
    if skip is None:
        try:
            snapshot = await itam_source() if itam_source is not None else None
        except Exception:  # noqa: BLE001 — 스냅샷 읽기 실패는 I 단계만 건너뛴다(사유 로그)
            logger.warning("APM·SMS 연계 ITAM 스냅샷 읽기 실패 — I 단계 건너뜀: %s", scope,
                           exc_info=True)
        if snapshot is None:
            skip = ITAM_SKIP_NO_INVENTORY
    if skip is not None or snapshot is None:
        plan.itam_skip = skip
        tried[TRIED_ITAM] = f"skipped:{skip}"
        return plan
    description = next((r["domain_description"] for r in inventory if r["domain_description"]),
                       "")
    result = resolve_itam_chain(
        domain_name=name, domain_description=description, apps=snapshot.apps,
        servers=snapshot.servers, by_ip=by_ip, by_host=by_host,
        generic_terms=snapshot.generic_terms, max_servers=snapshot.max_servers,
        max_targets=cap, canonical=canonical,
    )
    reason = _str(result.evidence.get("reason"))
    tried[TRIED_ITAM] = result.state if result.state == STATE_ACTIVE or not reason else (
        f"{result.state} · {reason}")
    plan.itam = itam_proposal(
        domain_id=domain_id, domain_name=name, result=result,
        instance_example=inventory[0]["instance_name"] if inventory else "",
        extra_evidence={**base, "tried": dict(tried)},
    )
    return plan



Written = Sequence[tuple[AutoProposal, tuple[str, dict[str, Any] | None]]]


async def _set_superseded(
    repo: ApmHostMapRepository, source_id: str, domain_id: str, marker: Mapping[str, Any] | None
) -> dict[str, Any] | None:
    try:
        return await repo.set_domain_superseded(source_id, domain_id, marker)
    except Exception:  # noqa: BLE001 — 표지 실패는 연계 행 쓰기를 되돌리지 않는다(사유는 로그)
        logger.warning("APM·SMS 연계 domain 행 대체 표지 %s 실패: source=%s domain=%s",
                       "달기" if marker is not None else "떼기", source_id, domain_id,
                       exc_info=True)
        return None


async def settle_superseded(
    repo: ApmHostMapRepository,
    source_id: str,
    written: Written,
    *,
    at: datetime,
    extra: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """쓰기 결과 → domain 행 「대체됨」 표지 정리(plans/211 §9.3 E) → 바뀐 행.

    - 노드 주소·VM 일치로 `active` instance·workload 행을 쓴(insert·update·same) 도메인 → 그
      도메인의 자동·미확인 domain 행(failed·ambiguous)에 `superseded: {bases, linked, at, …extra}`.
    - 이번에 ITAM으로 다시 쓴 domain 행에 표지가 남아 있으면(결과 동일 — evidence 유지) 뗀다.
    """
    linked: dict[str, tuple[set[str], list[int]]] = {}
    stale: set[str] = set()
    for proposal, (action, row) in written:
        if (proposal.scope in (SCOPE_INSTANCE, SCOPE_WORKLOAD) and proposal.state == STATE_ACTIVE
                and action in (WRITE_INSERT, WRITE_UPDATE, WRITE_SAME)):
            bases, count = linked.setdefault(proposal.domain_id, (set(), [0]))
            bases.add(proposal.basis)
            count[0] += 1 if proposal.scope == SCOPE_INSTANCE else 0
        elif proposal.scope == SCOPE_DOMAIN and is_superseded(row):
            stale.add(proposal.domain_id)
    changed: list[dict[str, Any]] = []
    for domain_id, (bases, count) in linked.items():
        marker = {"bases": sorted(bases), "linked": count[0], "at": at.isoformat(),
                  **dict(extra or {})}
        row = await _set_superseded(repo, source_id, domain_id, marker)
        if row is not None:
            changed.append(row)
    for domain_id in sorted(stale - set(linked)):
        row = await _set_superseded(repo, source_id, domain_id, None)
        if row is not None:
            changed.append(row)
    return changed


def _write_view(proposal: AutoProposal, action: str, row: Mapping[str, Any] | None
                ) -> dict[str, Any]:
    return {"scope": proposal.scope, "match_key": proposal.match_key, "basis": proposal.basis,
            "state": proposal.state, "action": action, "map_id": int((row or {}).get("id") or 0)}


async def apply_domain_link(
    plan: DomainLinkPlan,
    repo: ApmHostMapRepository,
    cache: ApmHostMapCache,
    *,
    now: datetime | None = None,
    retry_hours: int = 24,
) -> dict[str, Any]:
    """계획 저장 — instance·workload는 `apply_auto_many`, ITAM은 `apply_auto` · 쓴 행은 사본에 ·
    domain 행 「대체됨」 표지 정리(`settle_superseded`).

    Returns:
        `{status, linked, tried, rows[, action, state, map_id][, reason]}` — `status`는 쓴 제안이
        있으면 `ok`, 없으면 I 단계를 건너뛴 사유(`retry_wait`·`no_inventory`). `action`·`state`·
        `map_id`는 ITAM domain 행을 썼을 때만, `rows`는 instance·workload 쓰기 결과
        `[{scope, match_key, basis, state, action, map_id}]`.
    """
    written: list[tuple[AutoProposal, tuple[str, dict[str, Any] | None]]] = []
    if plan.proposals:
        results = await repo.apply_auto_many(plan.source_id, plan.proposals, now=now,
                                             retry_hours=retry_hours)
        written.extend(zip(plan.proposals, results, strict=False))
    out: dict[str, Any] = {"status": "ok", "linked": plan.linked, "tried": dict(plan.tried),
                           "rows": [_write_view(p, a, r) for p, (a, r) in written]}
    for _p, (_a, row) in written:
        if row is not None:
            await cache.put(row)
    if plan.itam is not None:
        action, row = await repo.apply_auto(plan.source_id, plan.itam, now=now,
                                            retry_hours=retry_hours)
        if row is not None:
            await cache.put(row)
        written.append((plan.itam, (action, row)))
        out.update(action=action, state=plan.itam.state,
                   map_id=int((row or {}).get("id") or 0))
    for row in await settle_superseded(repo, plan.source_id, written,
                                       at=now or datetime.now(UTC),
                                       extra={"tried": dict(plan.tried)}):
        await cache.put(row)
    if not written:
        out["status"] = plan.itam_skip or "ok"
        if plan.itam_skip == ITAM_SKIP_NO_INVENTORY:
            out["reason"] = REASON_NO_INVENTORY
    return out


async def load_sms_index(
    repo: ApmHostMapRepository, *, canonical: bool = False
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """현재 SMS 스냅샷 → `(ip 색인, hostname 색인)`(스냅샷이 없으면 빈 색인 — 직접 조회로 간다)."""
    if await repo.current_snapshot_run(TABLE_SMS) is None:
        return {}, {}
    return build_sms_index(await repo.snapshot_rows(TABLE_SMS), canonical=canonical)


def repo_itam_source(
    repo: ApmHostMapRepository,
    *,
    generic_terms: Collection[str] = (),
    max_servers: int = DEFAULT_MAX_SERVERS,
) -> ItamSourceFn:
    """현재 ITAM·SMS 스냅샷으로 ITAM 사슬 입력을 만드는 공급자(셋 중 하나라도 없으면 None)."""

    async def _load() -> ItamSnapshot | None:
        for table in (TABLE_ITAM_APP, TABLE_ITAM_SERVER, TABLE_SMS):
            if await repo.current_snapshot_run(table) is None:
                logger.info("APM·SMS 연계 ITAM 단계 — %s (스냅샷 없음 %s)", REASON_NO_INVENTORY,
                            table)
                return None
        return ItamSnapshot(
            apps=apps_from_rows(await repo.snapshot_rows(TABLE_ITAM_APP)),
            servers=servers_from_rows(await repo.snapshot_rows(TABLE_ITAM_SERVER)),
            generic_terms=tuple(generic_terms), max_servers=max(1, int(max_servers)),
        )

    return _load


async def handle_link_request(
    msg: Mapping[str, Any],
    *,
    repo: ApmHostMapRepository,
    cache: ApmHostMapCache,
    gateway_client: GatewayCaller | ApmGatewayClient | None,
    generic_terms: Collection[str] = (),
    itam_max_servers: int = DEFAULT_MAX_SERVERS,
    max_targets: int = DEFAULT_MAX_TARGETS,
    retry_hours: int = 24,
    now: datetime | None = None,
    read_kwargs: Mapping[str, Any] | None = None,
    node_link: NodeLinkFn | None = None,
    vm_link: VmLinkFn | None = None,
    live_budget_seconds: float = DEFAULT_LIVE_BUDGET_SECONDS,
    canonical: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """`itam_request` 1건 → 그 도메인을 §9.2 순서(노드 주소 → VM 일치 → ITAM)로 잇는다.

    `gateway_client`는 `ApmGatewayClient`(실 배선) 또는 `call_tool`을 가진 호출기(테스트 가짜)다.
    도메인을 `apm_instance_map(domain_id, source_ids=[src])`로 다시 읽고 `plan_domain_link` →
    `apply_domain_link`. SMS 스냅샷이 없으면 N3·V 직접 조회로 가고, ITAM·SMS 스냅샷이 없으면 I만
    건너뛴다. 이미 있는 domain 행의 `next_retry_at`(failed·ambiguous 행 —
    `NOISE_APM_HOST_MAP_RETRY_HOURS`)이 아직 오지 않았으면 **I 단계만** 건너뛴다(N·V는 매번).
    메시지 타입은 `itam_request` 그대로다(스트림 호환 · `handle_itam_request`는 별칭).

    Returns:
        `apply_domain_link` 결과 + I를 재시도 대기로 건너뛰었으면 `next_retry_at` · 앞 단계 실패는
        `{"status": invalid|error|no_domain, ...}`.
    """
    source_id = _str(msg.get("source_id"))
    domain_text = _str(msg.get("domain_id"))
    if msg.get("type") != MSG_ITAM_REQUEST or not source_id or not domain_text.isdigit():
        logger.warning("APM·SMS 연계 요청 형식 오류 — 건너뜀")
        return {"status": "invalid"}
    if gateway_client is None:
        return {"status": "error", "error_kind": ERROR_NOT_CONFIGURED}
    caller = _as_caller(gateway_client)
    try:
        rows = await read_domain_instances(caller, source_id, domain_text, read_kwargs=read_kwargs)
    except GatewayReadError as e:
        logger.warning("APM·SMS 연계 요청 — 도메인 읽기 실패: source=%s domain=%s %s",
                       source_id, domain_text, e.kind)
        return {"status": "error", "error_kind": e.kind}
    if not rows:
        return {"status": "no_domain"}
    existing = await repo.get_by_key(source_id, domain_text, SCOPE_DOMAIN, "")
    retry_at = _retry_at(existing)
    itam_skip: str | None = None
    if retry_at is not None and retry_at > (now or datetime.now(UTC)):
        itam_skip = ITAM_SKIP_RETRY_WAIT
    by_ip, by_host = await load_sms_index(repo, canonical=canonical)
    plan = await plan_domain_link(
        caller, source_id, domain_text, rows, by_ip=by_ip, by_host=by_host,
        itam_source=repo_itam_source(repo, generic_terms=generic_terms,
                                     max_servers=itam_max_servers),
        itam_skip=itam_skip, node_link=node_link, vm_link=vm_link,
        live_budget_seconds=live_budget_seconds, max_targets=max_targets, canonical=canonical,
        read_kwargs=read_kwargs, evidence={"trigger": "alarm_miss"},
        domain_name=_str(msg.get("domain_name")), clock=clock,
    )
    out = await apply_domain_link(plan, repo, cache, now=now, retry_hours=retry_hours)
    if plan.itam_skip == ITAM_SKIP_RETRY_WAIT and retry_at is not None:
        out["next_retry_at"] = retry_at.isoformat()
        logger.info("APM·SMS 연계 요청 — 재시도 시각 전이라 ITAM 단계만 건너뜀: source=%s "
                    "domain=%s state=%s next_retry_at=%s", source_id, domain_text,
                    (existing or {}).get("state"), retry_at.isoformat())
    logger.info("APM·SMS 연계 요청 처리: source=%s domain=%s status=%s linked=%d tried=%s",
                source_id, domain_text, out["status"], plan.linked, plan.tried)
    return out


#: 하위 호환 이름(W3 소비자·기존 테스트) — 본체는 `handle_link_request`.
handle_itam_request = handle_link_request


# ── 실 배선 (API 프로세스 · W6·팀 리드가 부른다) ───────────────────────
def _scan_settings(app_config: Any) -> Any:
    return getattr(app_config, "noise_gate", None)


def build_scan_service(
    app_config: Any, *, repo: ApmHostMapRepository, cache: Any
) -> ApmHostMapScanService:
    """설정·저장소·사본(`RedisApmHostMapCache` — `.redis`로 락)으로 스캔 서비스를 조립한다."""
    from noise_gate.application.host_candidates import node_lookup_db_ids
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scan_lock import RedisScanLock
    from noise_gate.infrastructure.apm_host_map_scanners import (
        scan_apm_inventory,
        scan_node_addresses,
    )
    from noise_gate.infrastructure.apm_host_map_sql_scanners import (
        ScanSqlError,
        load_itam_lookup,
        registry_executor,
        scan_itam,
        scan_sms,
    )

    ng = _scan_settings(app_config)
    client = build_apm_gateway_client(app_config)
    caller = GatewayToolCaller(client) if client is not None else None
    execute = registry_executor(app_config)
    page_rows = int(getattr(ng, "apm_host_map_scan_page_rows", 1000))
    itam_cfg: Any = None
    itam_error: str | None = None
    try:
        itam_cfg = load_itam_lookup()
    except ScanSqlError as e:
        itam_error = e.kind
        logger.warning("APM·SMS 연계 — ITAM 설정 오류(ITAM 스캐너는 실패로 기록): %s", e)
    zones = node_lookup_db_ids(app_config)
    scanners = ScanScanners(
        apm_inventory=lambda: scan_apm_inventory(caller),
        node_address=lambda: scan_node_addresses(caller),
        sms=lambda: scan_sms(execute, zones, page_rows=page_rows),
        itam=lambda: scan_itam(execute, itam_cfg, page_rows=page_rows, config_error=itam_error),
    )
    return ApmHostMapScanService(
        repo=repo, cache=cache, lock=RedisScanLock(cache.redis), scanners=scanners,
        auto_enabled=getattr(ng, "apm_host_map_auto_enabled", False) is True,
        max_targets=int(getattr(ng, "apm_host_map_max_targets", DEFAULT_MAX_TARGETS)),
        retry_hours=int(getattr(ng, "apm_host_map_retry_hours", 24)),
        generic_terms=itam_cfg.generic_terms if itam_cfg is not None else (),
        itam_max_servers=itam_cfg.max_servers if itam_cfg is not None else DEFAULT_MAX_SERVERS,
        canonical=_canonical(app_config),
    )


def _canonical(app_config: Any) -> bool:
    """`HOST_CANONICAL_MATCH_ENABLED`(plans/211 · 기동 시 1회) — `is True`로 목 오발동 차단."""
    return getattr(app_config, "host_canonical_match_enabled", False) is True


def _live_budget(ng: Any) -> float:
    """`NOISE_APM_HOST_MAP_LIVE_BUDGET_SECONDS`(음수·형식 밖이면 기본 5초)."""
    value = getattr(ng, "apm_host_map_live_budget_seconds", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return DEFAULT_LIVE_BUDGET_SECONDS
    return float(value)


def build_itam_request_handler(
    app_config: Any, *, repo: ApmHostMapRepository, cache: ApmHostMapCache
) -> Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]:
    """W3 소비자에 등록할 `itam_request` 처리기(`handle_link_request`).

    설정·게이트웨이·ITAM 일반어 + 직접 조회(`node_address_link`·`vm_exact_link` — 전 존 정확 일치 ·
    알람 3'·5순과 같은 함수)·예산·정규형 플래그를 묶는다.
    """
    from noise_gate.application.host_candidates import node_address_link, vm_exact_link
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_sql_scanners import ScanSqlError, load_itam_lookup

    ng = _scan_settings(app_config)
    client = build_apm_gateway_client(app_config)
    try:
        itam_cfg: Any = load_itam_lookup()
    except ScanSqlError:
        itam_cfg = None
    terms = itam_cfg.generic_terms if itam_cfg is not None else ()
    max_servers = itam_cfg.max_servers if itam_cfg is not None else DEFAULT_MAX_SERVERS
    max_targets = int(getattr(ng, "apm_host_map_max_targets", DEFAULT_MAX_TARGETS))
    retry_hours = int(getattr(ng, "apm_host_map_retry_hours", 24))
    budget = _live_budget(ng)
    canonical = _canonical(app_config)

    async def _node(ip: str) -> Mapping[str, Any]:
        return await node_address_link(app_config, host_address=ip)

    async def _vm(ip: str, host_name: str) -> Any:
        return await vm_exact_link(app_config, ip=ip or None, host_name=host_name or None,
                                   max_targets=max_targets)

    async def _handle(msg: Mapping[str, Any]) -> dict[str, Any]:
        return await handle_link_request(
            msg, repo=repo, cache=cache, gateway_client=client, generic_terms=terms,
            itam_max_servers=max_servers, max_targets=max_targets, retry_hours=retry_hours,
            node_link=_node, vm_link=_vm, live_budget_seconds=budget, canonical=canonical,
        )

    return _handle


__all__ = [
    "DEFAULT_LIVE_BUDGET_SECONDS",
    "ERROR_SNAPSHOT_STORE",
    "HEARTBEAT_SECONDS",
    "ITAM_SKIP_LINKED",
    "ITAM_SKIP_NO_INVENTORY",
    "ITAM_SKIP_RETRY_WAIT",
    "LOCK_TTL_SECONDS",
    "NODE_REASON_DISABLED",
    "NODE_REASON_ERROR",
    "NODE_REASON_OK",
    "REASON_AUTO_DISABLED",
    "TABLE_APM",
    "TABLE_ITAM_APP",
    "TABLE_ITAM_SERVER",
    "TABLE_SMS",
    "TRIED_ITAM",
    "TRIED_NODE",
    "TRIED_VM",
    "ApmHostMapScanService",
    "DomainLinkPlan",
    "ItamSourceFn",
    "NodeLinkFn",
    "ScanDisabledError",
    "ScanLock",
    "ScanRunningError",
    "ScanScanners",
    "VmLinkFn",
    "apply_domain_link",
    "build_itam_request_handler",
    "build_scan_service",
    "fetch_node_addresses",
    "handle_itam_request",
    "handle_link_request",
    "load_sms_index",
    "plan_domain_link",
    "read_domain_instances",
    "repo_itam_source",
    "settle_superseded",
]
