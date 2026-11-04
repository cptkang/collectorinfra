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

`handle_itam_request`는 알람 miss 때 워커가 보낸 `itam_request`(W3 소비자가 부른다)를 처리한다 —
그 도메인만 게이트웨이로 다시 읽어 현재 ITAM·SMS 스냅샷과 대조한 뒤 domain 행을 upsert한다.

계층: application.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from noise_gate.domain.apm_host_map import (
    DEFAULT_MAX_TARGETS,
    MSG_ITAM_REQUEST,
    REASON_NO_INVENTORY,
    SCAN_ABORTED,
    SCAN_FAILED,
    SCOPE_DOMAIN,
    ApmHostMapCache,
    ApmHostMapRepository,
    build_sms_index,
)
from noise_gate.domain.apm_host_map_itam import (
    DEFAULT_MAX_SERVERS,
    apps_from_rows,
    itam_proposal,
    resolve_itam_chain,
    servers_from_rows,
)
from noise_gate.domain.apm_host_map_scan import (
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
    INSTANCE_MAP_TOOL,
    GatewayCaller,
    GatewayReadError,
    GatewayToolCaller,
    instance_key,
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
#: ITAM 요청 처리의 게이트웨이 읽기 예산(초).
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
        canonical_match: bool = False,
    ) -> None:
        """의존성과 설정(`NOISE_APM_HOST_MAP_*` 해석값)을 받는다.

        `canonical_match`(`HOST_CANONICAL_MATCH_ENABLED` · plans/211 W7)면 일괄 생성의 SMS hostname
        색인을 정규형으로 만들고 이름 후보 행을 남긴다. Redis SMS 색인 재구성은 늘 현행 키다.
        """
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
        self._canonical = canonical_match is True
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
            found: Mapping[tuple[str, str, str], tuple[str, str]] = node.data or {}
            for row in rows:
                hit = found.get(instance_key(row))
                if hit is not None:
                    row["host_address"] = hit[0] or row.get("host_address") or ""
                    row["host_address_status"] = hit[1]
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
            plan = plan_scan_proposals(
                inventory, by_ip, by_host, use_node_address=use_node, itam=itam,
                max_targets=self._max_targets, evidence={"run_id": run_id},
                canonical=self._canonical,
            )
            now = self._clock()
            for source_id, proposals in plan.items():
                written = await self._repo.apply_auto_many(
                    source_id, proposals, now=now, retry_hours=self._retry_hours,
                )
                add_write_results(summary, proposals, written)
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
            by_ip, by_host = build_sms_index(await self._repo.snapshot_rows(TABLE_SMS))
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


# ── ITAM 비동기 요청 (알람 miss → 다음 알람부터 4순) ──────────────────
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


async def handle_itam_request(
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
) -> dict[str, Any]:
    """`itam_request` 1건 → 그 도메인의 domain 행 upsert.

    `gateway_client`는 `ApmGatewayClient`(실 배선) 또는 `call_tool`을 가진 호출기(테스트 가짜)다.
    그 도메인만 `apm_instance_map(domain_id, source_ids=[src])`로 다시 읽어 현재 ITAM·SMS 스냅샷과
    대조한다(SQL 없음). 스냅샷이 없으면 행에 아무것도 쓰지 않고 `apm_map_no_inventory`로
    로그만 남긴다.

    이미 있는 domain 행의 `next_retry_at`(failed·ambiguous 행 — `NOISE_APM_HOST_MAP_RETRY_HOURS`)이
    아직 오지 않았으면 게이트웨이·스냅샷을 읽지 않고 건너뛴다(`retry_wait`).

    Returns:
        `{"status": ok|no_inventory|no_domain|invalid|error|retry_wait, ...}` — ok면
        `action`·`state`·`map_id`, retry_wait면 `next_retry_at`.
    """
    source_id = str(msg.get("source_id") or "").strip()
    domain_text = str(msg.get("domain_id") or "").strip()
    if msg.get("type") != MSG_ITAM_REQUEST or not source_id or not domain_text.isdigit():
        logger.warning("APM·SMS 연계 ITAM 요청 형식 오류 — 건너뜀")
        return {"status": "invalid"}
    existing = await repo.get_by_key(source_id, domain_text, SCOPE_DOMAIN, "")
    retry_at = _retry_at(existing)
    if retry_at is not None and retry_at > (now or datetime.now(UTC)):
        logger.info("APM·SMS 연계 ITAM 요청 — 재시도 시각 전이라 건너뜀: source=%s domain=%s "
                    "state=%s next_retry_at=%s", source_id, domain_text,
                    (existing or {}).get("state"), retry_at.isoformat())
        return {"status": "retry_wait", "next_retry_at": retry_at.isoformat()}
    for table in (TABLE_ITAM_APP, TABLE_ITAM_SERVER, TABLE_SMS):
        if await repo.current_snapshot_run(table) is None:
            logger.info("APM·SMS 연계 ITAM 요청 — %s: source=%s domain=%s (스냅샷 없음 %s)",
                        REASON_NO_INVENTORY, source_id, domain_text, table)
            return {"status": "no_inventory", "reason": REASON_NO_INVENTORY}
    if gateway_client is None:
        return {"status": "error", "error_kind": ERROR_NOT_CONFIGURED}
    caller: GatewayCaller = (
        GatewayToolCaller(gateway_client) if isinstance(gateway_client, ApmGatewayClient)
        else gateway_client
    )
    try:
        _env, rows = await read_all_rows(
            caller, INSTANCE_MAP_TOOL,
            {"domain_id": int(domain_text), "source_ids": [source_id]},
            **{**_REQUEST_READ, **dict(read_kwargs or {})},
        )
    except GatewayReadError as e:
        logger.warning("APM·SMS 연계 ITAM 요청 — 도메인 읽기 실패: source=%s domain=%s %s",
                       source_id, domain_text, e.kind)
        return {"status": "error", "error_kind": e.kind}
    mine = [
        r for r in rows
        if str(r.get("source_id") or "").strip() == source_id
        and str(r.get("domain_id") or "").strip() == domain_text
    ]
    if not mine:
        return {"status": "no_domain"}
    domain_name = next((str(r.get("domain_name") or "").strip() for r in mine
                        if r.get("domain_name")), str(msg.get("domain_name") or "").strip())
    description = next((str(r.get("domain_description") or "").strip() for r in mine
                        if r.get("domain_description")), "")
    by_ip, by_host = build_sms_index(await repo.snapshot_rows(TABLE_SMS))
    result = resolve_itam_chain(
        domain_name=domain_name, domain_description=description,
        apps=apps_from_rows(await repo.snapshot_rows(TABLE_ITAM_APP)),
        servers=servers_from_rows(await repo.snapshot_rows(TABLE_ITAM_SERVER)),
        by_ip=by_ip, by_host=by_host, generic_terms=generic_terms,
        max_servers=itam_max_servers, max_targets=max_targets,
    )
    proposal = itam_proposal(
        domain_id=domain_text, domain_name=domain_name, result=result,
        instance_example=str(mine[0].get("instance_name") or ""),
        extra_evidence={"trigger": "alarm_miss"},
    )
    action, row = await repo.apply_auto(source_id, proposal, now=now, retry_hours=retry_hours)
    if row is not None:
        await cache.put(row)
    logger.info("APM·SMS 연계 ITAM 요청 처리: source=%s domain=%s action=%s state=%s",
                source_id, domain_text, action, result.state)
    return {"status": "ok", "action": action, "state": result.state,
            "map_id": int((row or {}).get("id") or 0)}


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
        canonical_match=getattr(app_config, "host_canonical_match_enabled", False) is True,
    )


def build_itam_request_handler(
    app_config: Any, *, repo: ApmHostMapRepository, cache: ApmHostMapCache
) -> Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]:
    """W3 소비자에 등록할 `itam_request` 처리기(설정·게이트웨이·ITAM 일반어를 묶는다)."""
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

    async def _handle(msg: Mapping[str, Any]) -> dict[str, Any]:
        return await handle_itam_request(
            msg, repo=repo, cache=cache, gateway_client=client, generic_terms=terms,
            itam_max_servers=max_servers, max_targets=max_targets, retry_hours=retry_hours,
        )

    return _handle


__all__ = [
    "ERROR_SNAPSHOT_STORE",
    "HEARTBEAT_SECONDS",
    "LOCK_TTL_SECONDS",
    "REASON_AUTO_DISABLED",
    "TABLE_APM",
    "TABLE_ITAM_APP",
    "TABLE_ITAM_SERVER",
    "TABLE_SMS",
    "ApmHostMapScanService",
    "ScanDisabledError",
    "ScanLock",
    "ScanRunningError",
    "ScanScanners",
    "build_itam_request_handler",
    "build_scan_service",
    "handle_itam_request",
]
