"""업무 서비스 계산 서비스 — 스냅샷 로드 → 보강 읽기 → 계산 → 미리보기 → 적용.

plans/215 §3.6 · D-368.

관리자 「미리보기」·「적용」(W4 라우트)이 부른다. API 프로세스 안에서만 돈다(D-049 — PG 기록자).

- **입력**: 210 「현재 스냅샷」(SMS·APM·ITAM) + 210 매핑 전 행 + 저장 상태(서비스·구성원·후보) →
  도메인 입력(`ProposalInputs`). 존은 db_id → 레지스트리 존이다.
- **보강 읽기**(§3.2.4 · 미리보기 때만 · 존별 · 동시 1): ① 서버 행 ② 트리 폴더 ③ (S10 on) ITAM 계약.
  실패는 신호 단위(`AttrFailure`)로 넘기고 계산은 계속한다(개별 try · 사유 노출). 결과는
  `biz_service_attr`(run별)에 저장하고 적용은 같은 미리보기 run의 값을 쓴다. 트리는 서버마다 도메인
  `tree_group_of`가 고른 폴더 1개만 남긴다(같은 서버·같은 일반어면 같은 폴더가 다시 골라진다).
  필요한 신호가 전부 꺼져 있으면 보강 SELECT 0.
- **S11**(on일 때만): 결정 이력 `recent_decisions` 창 → (서버 · 분) — 서버는 `canonical_host_key`로
  스냅샷 서버 1대 일치만. 관측 조회 0. 적용은 미리보기 시각까지의 같은 창을 다시 읽는다.
- **미리보기**: 실행 기록 `preview` + 보강 속성만 저장(서비스·구성원·후보 쓰기 0) · 응답은 저장
  상태와의 차이(`plan_apply` 결과) · 신호 표 · 기준 스캔 run id.
- **적용**: 미리보기 뒤 스냅샷 run·210 활성 매핑 지문이 바뀌었으면
  `BizServiceSnapshotChangedError` · 210 스캔 락이 잡혀 있으면 `BizServiceScanRunningError` ·
  같은 입력으로 재계산 → `plan_apply` → 저장소 한 트랜잭션 → Redis 사본 재구성 → 실행 기록 `apply`.
- 미리보기·적용은 단독 락 `bizsvc:lock`(`SET NX EX 600` · 자기 값만 해제)으로 하나씩만 돈다 —
  잡혀 있으면 `BizServiceBusyError`. 플래그(`NOISE_BIZ_SERVICE_ENABLED`) off면
  `BizServiceDisabledError`(보강 SELECT 0).

계층: application. LLM 호출 0.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections import Counter, defaultdict
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from src.domain.host_names import canonical_host_key

from noise_gate.application.apm_host_map_scan import (
    TABLE_APM,
    TABLE_ITAM_APP,
    TABLE_ITAM_SERVER,
    TABLE_SMS,
)
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain.apm_host_map_scan import SCANNER_OK, SCANNER_SMS
from noise_gate.domain.biz_service import compute_proposal, plan_apply
from noise_gate.domain.biz_service_apply import (
    OP_INSERT,
    OP_SAME,
    OP_SKIP,
    OP_SUGGEST,
    OP_UPDATE,
    ApplyOps,
)
from noise_gate.domain.biz_service_model import (
    CONF_INFERRED,
    MEMBER_ACTIVE,
    ORIGIN_AUTO,
    SIG_ALARM_COOCCURRENCE,
    SIG_AVAIL_DEPENDENCY,
    SIG_ITAM_CONTRACT,
    SIG_REGISTERED_BATCH,
    SIG_TREE_GROUP,
    SVC_ACTIVE,
    SVC_DISABLED,
    AlarmMinute,
    ApmInventoryRow,
    ApmMapRow,
    AttrFailure,
    ItamAppRow,
    ItamServerRow,
    Proposal,
    ProposalInputs,
    ServerAttr,
    ServerRef,
    SmsServer,
    StoredCandidate,
    StoredMember,
    StoredService,
    StoredState,
    TreeFolder,
    fold_terms,
    text,
)
from noise_gate.domain.biz_service_signals import tree_group_of
from noise_gate.infrastructure.apm_host_map_sql_scanners import SqlExecutor, db_engine_of
from noise_gate.infrastructure.biz_service_sql import (
    ATTR_PAGE_ROWS,
    FolderRow,
    read_itam_contracts,
    read_server_attrs,
    read_tree_folders,
)

logger = logging.getLogger(__name__)

#: 계산 단독 락 키·수명(초).
BIZSVC_LOCK_KEY = "bizsvc:lock"
LOCK_TTL_SECONDS = 600
MODE_PREVIEW = "preview"
MODE_APPLY = "apply"
SNAPSHOT_TABLES = (TABLE_SMS, TABLE_APM, TABLE_ITAM_APP, TABLE_ITAM_SERVER)
#: ① 서버 행이 공급하는 신호(실패하면 그 존에서 함께 끈다).
SERVER_READ_SIGNALS = (SIG_TREE_GROUP, SIG_REGISTERED_BATCH, SIG_AVAIL_DEPENDENCY)
#: 보강 속성 칸 상한(DDL `biz_service_attr`와 같다).
_ATTR_LIMITS = {"tree_group_id": 64, "tree_group_name": 255, "tree_code": 255,
                "contract_no": 100, "contract_name": 255, "maint_contract_no": 100,
                "owner_branch": 255}
#: 직전 적용 요약을 찾을 때 되짚는 실행 기록 수.
_PREVIOUS_LOOKBACK = 20


# ── 예외(라우트가 409·404로 옮긴다) ─────────────────────────────────────
class BizServiceError(Exception):
    """계산 서비스 거부 — `code`는 응답 사유 코드."""

    code = "biz_service_error"

    def __init__(self, message: str = "", **detail: Any) -> None:
        """사람이 읽을 사유와 상세 칸을 싣는다."""
        super().__init__(message or self.code)
        self.detail = detail


class BizServiceDisabledError(BizServiceError):
    """`NOISE_BIZ_SERVICE_ENABLED` off."""

    code = "biz_service_disabled"


class BizServiceBusyError(BizServiceError):
    """다른 미리보기·적용이 `bizsvc:lock`을 잡고 있다."""

    code = "biz_service_running"


class BizServiceScanRunningError(BizServiceError):
    """210 사전 스캔이 실행 중이다(스냅샷이 바뀌는 중)."""

    code = "scan_running"


class BizServiceSnapshotChangedError(BizServiceError):
    """미리보기 뒤 스냅샷 run·210 매핑이 바뀌었거나 미리보기 보강 속성이 정리됐다."""

    code = "snapshot_changed"


class BizServicePreviewNotFoundError(BizServiceError):
    """적용할 미리보기 실행 기록이 없다(없음 · preview 아님 · 완료 아님)."""

    code = "preview_not_found"


# ── 주입 계약 ───────────────────────────────────────────────────────────
class _Lock(Protocol):
    async def acquire(self, run_id: str, ttl_seconds: int = ...) -> bool: ...

    async def holder(self) -> str | None: ...

    async def release(self, run_id: str) -> None: ...


class _LockView(Protocol):
    async def holder(self) -> str | None: ...


class _SnapshotSource(Protocol):
    """210 저장소에서 읽는 것만(`PostgresApmHostMapRepository`)."""

    async def current_snapshot_run(self, table: str) -> str | None: ...

    async def snapshot_rows(self, table: str) -> list[dict[str, Any]]: ...

    async def all_rows(self) -> list[dict[str, Any]]: ...

    async def get_scan_run(self, run_id: str) -> dict[str, Any] | None: ...


class _Store(Protocol):
    """업무 서비스 저장소(`PostgresBizServiceRepository`)에서 쓰는 것만."""

    async def all_services_with_members(self) -> list[dict[str, Any]]: ...

    async def all_candidates(self) -> list[dict[str, Any]]: ...

    async def apply_ops(self, ops: ApplyOps, *, run_id: str, by: str = "") -> dict[str, int]: ...

    async def start_run(self, *, mode: str, requested_by: str = "",
                        scan_run_id: str | None = None,
                        run_id: str | None = None) -> dict[str, Any]: ...

    async def finish_run(self, run_id: str, *, status: str,
                         summary: Mapping[str, Any] | None = None) -> dict[str, Any] | None: ...

    async def get_run(self, run_id: str) -> dict[str, Any] | None: ...

    async def list_runs(self, *, mode: str | None = None,
                        limit: int = 20) -> list[dict[str, Any]]: ...

    async def save_attrs(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int: ...

    async def get_attrs(self, run_id: str | None = None
                        ) -> tuple[str | None, list[dict[str, Any]]]: ...

    async def active_services_with_members(self) -> list[dict[str, Any]]: ...


class _Cache(Protocol):
    async def sync_from_repo(self, repo: Any) -> dict[str, int]: ...


class _Decisions(Protocol):
    def recent_decisions(self, window_seconds: int) -> list[dict[str, Any]]: ...


def _error_kind(exc: BaseException) -> str:
    return str(getattr(exc, "kind", "") or type(exc).__name__)


def registry_zone(db_id: str) -> str:
    """db_id → 레지스트리 존 코드(미배정·해석 실패는 빈 문자열)."""
    from src.routing.zones import db_id_to_zone

    try:
        return db_id_to_zone(db_id) or ""
    except Exception:  # noqa: BLE001 — 레지스트리 실패는 존 미상(그 db 서버는 존 "" 묶음)
        logger.debug("존 해석 실패: db_id=%s", db_id, exc_info=True)
        return ""


# ── 스냅샷 · 저장 상태 → 도메인 입력 ────────────────────────────────────
def _strs(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(t for t in (text(v) for v in value) if t)
    return ()


def sms_servers(rows: Iterable[Mapping[str, Any]], zone_of: Callable[[str], str]
                ) -> tuple[SmsServer, ...]:
    """`sms_inventory` 행 → SMS 서버(존은 db_id마다 1회 해석)."""
    zones: dict[str, str] = {}
    out: list[SmsServer] = []
    for r in rows:
        db_id, rid = text(r.get("db_id")), text(r.get("resource_id"))
        if not db_id or not rid:
            continue
        if db_id not in zones:
            zones[db_id] = zone_of(db_id)
        out.append(SmsServer(db_id, rid, text(r.get("server_name")), text(r.get("hostname")),
                             text(r.get("ip")), _strs(r.get("nic_ips")), zones[db_id]))
    return tuple(out)


def apm_inventory(rows: Iterable[Mapping[str, Any]]) -> tuple[ApmInventoryRow, ...]:
    """`apm_inventory` 행 → APM 인벤토리."""
    return tuple(
        ApmInventoryRow(text(r.get("source_id")), text(r.get("domain_id")),
                        text(r.get("domain_name")), text(r.get("instance_id")),
                        text(r.get("instance_name")), text(r.get("workload_key")))
        for r in rows if text(r.get("source_id")) and text(r.get("domain_id"))
    )


def apm_maps(rows: Iterable[Mapping[str, Any]]) -> tuple[ApmMapRow, ...]:
    """210 `apm_host_map` 행 → 연계 매핑(대상 목록은 210 모양 그대로)."""
    out: list[ApmMapRow] = []
    for r in rows:
        try:
            map_id = int(r.get("id") or 0)
        except (TypeError, ValueError):
            continue
        raw_targets = r.get("targets")
        targets = raw_targets if isinstance(raw_targets, list) else []
        out.append(ApmMapRow(
            map_id, text(r.get("source_id")), text(r.get("domain_id")), text(r.get("scope")),
            text(r.get("match_key")), text(r.get("state")), text(r.get("origin")),
            r.get("verified") is True, tuple(t for t in targets if isinstance(t, Mapping)),
            text(r.get("domain_name")), text(r.get("instance_example")),
        ))
    return tuple(out)


def itam_apps(rows: Iterable[Mapping[str, Any]]) -> tuple[ItamAppRow, ...]:
    """`itam_snapshot_app` 행 → ITAM 업무."""
    return tuple(ItamAppRow(text(r.get("app_code")), text(r.get("app_name"))) for r in rows
                 if text(r.get("app_code")) or text(r.get("app_name")))


def itam_servers(rows: Iterable[Mapping[str, Any]]) -> tuple[ItamServerRow, ...]:
    """`itam_snapshot_server` 행 → ITAM 서버."""
    out: list[ItamServerRow] = []
    for r in rows:
        if not text(r.get("hostname")):
            continue
        active = r.get("active")
        out.append(ItamServerRow(text(r.get("hostname")), _strs(r.get("ips")),
                                 active if isinstance(active, bool) else None,
                                 text(r.get("clue_text")), text(r.get("app_code")),
                                 text(r.get("app_name"))))
    return tuple(out)


def _opt_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def stored_state(services: Iterable[Mapping[str, Any]],
                 candidates: Iterable[Mapping[str, Any]]) -> StoredState:
    """저장소 행(전 상태 서비스·구성원 · 후보) → 도메인 저장 상태.

    후보의 서비스 키·대상 키는 적용 때 `evidence`에 남긴 값으로 되살린다
    (없으면 판정 재료가 못 된다).
    """
    out: list[StoredService] = []
    for s in services:
        members = tuple(
            StoredMember(
                text(m.get("kind")), text(m.get("member_key")),
                text(m.get("origin")) or ORIGIN_AUTO,
                text(m.get("state")) or MEMBER_ACTIVE, text(m.get("confidence")) or CONF_INFERRED,
                text(m.get("basis")), m.get("layer") or None, m.get("layer_origin") or None,
                _opt_int(m.get("apm_map_id")), text(m.get("gone_at")) or None,
            )
            for m in s.get("members") or []
        )
        evidence = s.get("evidence")
        out.append(StoredService(
            int(s.get("id") or 0), text(s.get("service_key")), text(s.get("display_name")),
            text(s.get("origin")) or ORIGIN_AUTO, text(s.get("state")) or SVC_ACTIVE,
            s.get("verified") is True, dict(evidence) if isinstance(evidence, Mapping) else {},
            members,
        ))
    cands: list[StoredCandidate] = []
    for c in candidates:
        raw_ev = c.get("evidence")
        ev: Mapping[str, Any] = raw_ev if isinstance(raw_ev, Mapping) else {}
        service_key, target_key = text(ev.get("service_key")), text(ev.get("target_key"))
        if service_key and target_key:
            cands.append(StoredCandidate(text(c.get("kind")), service_key, target_key,
                                         text(c.get("status"))))
    return StoredState(tuple(out), tuple(cands))


def _ms_to_dt(ms: int | None) -> datetime | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _dt_to_ms(value: Any) -> int | None:
    if isinstance(value, str) and value:
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(round(value.timestamp() * 1000))


def attrs_from_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[ServerAttr, ...]:
    """`biz_service_attr` 행 → 보강 속성(트리는 고른 폴더 1개 경로)."""
    out: list[ServerAttr] = []
    for r in rows:
        db_id, rid = text(r.get("db_id")), text(r.get("resource_id"))
        if not db_id or not rid:
            continue
        name = text(r.get("tree_group_name"))
        path = (TreeFolder(text(r.get("tree_group_id")), name),) if name else ()
        out.append(ServerAttr(db_id, rid, path, _dt_to_ms(r.get("registered_at")),
                              _strs(r.get("depends")), text(r.get("contract_no")),
                              text(r.get("maint_contract_no"))))
    return tuple(out)


def _host_index(servers: Iterable[SmsServer]) -> dict[str, set[ServerRef]]:
    """`canonical_host_key`(hostname · 서버명) → 스냅샷 서버."""
    index: dict[str, set[ServerRef]] = defaultdict(set)
    for s in servers:
        for raw in (s.hostname, s.server_name):
            key = canonical_host_key(raw)
            if key:
                index[key].add(s.ref)
    return index


def _parse_ts(raw: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def alarm_minutes_from(
    records: Iterable[Mapping[str, Any]],
    servers: Sequence[SmsServer],
    *,
    since_ts: float,
    until_ts: float,
) -> tuple[tuple[AlarmMinute, ...], dict[str, int]]:
    """결정 이력 → (서버 · 분) 목록과 건수 메모 — 서버는 1대 일치만(2대 이상은 고르지 않는다).

    레코드의 hostname · 서버명 · 자원명을 `canonical_host_key`로 스냅샷 서버에 잇는다.
    레코드 db_id가 스냅샷 db면 그 db 서버로 좁힌다. 시각은 `ts`(기록 시각)이고
    `[since, until]` 밖은 버린다.
    """
    index = _host_index(servers)
    dbs = {s.ref[0] for s in servers}
    seen: set[tuple[ServerRef, int]] = set()
    notes: Counter[str] = Counter()
    for rec in records:
        ts = _parse_ts(rec.get("ts"))
        if ts is None or ts < since_ts or ts > until_ts:
            continue
        refs: set[ServerRef] = set()
        for k in ("hostname", "server_name", "resource_name"):
            key = canonical_host_key(text(rec.get(k)))
            if key:
                refs |= index.get(key, set())
        db_id = text(rec.get("db_id"))
        if db_id in dbs:
            refs = {r for r in refs if r[0] == db_id}
        if len(refs) != 1:
            notes["unmatched" if not refs else "ambiguous"] += 1
            continue
        seen.add((next(iter(refs)), int(ts // 60)))
    minutes = tuple(AlarmMinute(r[0], r[1], m) for r, m in sorted(seen))
    notes["minutes"] = len(minutes)
    return minutes, dict(sorted(notes.items()))


# ── 차이 ────────────────────────────────────────────────────────────────
def describe_ops(stored: StoredState, proposal: Proposal, ops: ApplyOps) -> dict[str, Any]:
    """적용 연산 목록 → 미리보기 차이.

    신규·변경·삭제 서비스 · 구성원 증감 · scan_suggests · 후보.
    """
    by_id = {s.service_id: s for s in stored.services}
    proposed_keys = {p.service_key for p in proposal.services}
    new, changed, suggests = [], [], []
    service_counts: Counter[str] = Counter()
    for op in ops.services:
        service_counts[op.action] += 1
        if op.action == OP_INSERT:
            new.append({"service_key": op.service_key, "display_name": op.display_name,
                        "state": op.state, "sms_members": op.evidence.get("sms_members", 0)})
        elif op.action == OP_UPDATE:
            changed.append({"service_id": op.service_id, "service_key": op.service_key,
                            "display_name": op.display_name, "state": op.state,
                            "previous": op.evidence.get("previous", {})})
        elif op.action == OP_SUGGEST:
            before = by_id[op.service_id].evidence if op.service_id in by_id else {}
            suggest = op.evidence.get("scan_suggests", {})
            suggests.append({"service_id": op.service_id, "service_key": op.service_key,
                             "display_name": op.display_name, "suggest": suggest,
                             "unchanged": before.get("scan_suggests") == suggest})
    removed = [
        {"service_id": s.service_id, "service_key": s.service_key,
         "display_name": s.display_name}
        for s in sorted(stored.services, key=lambda x: x.service_id)
        if s.origin == ORIGIN_AUTO and not s.verified and s.state != SVC_DISABLED
        and s.service_key not in proposed_keys
    ]
    member_counts: Counter[str] = Counter(m.action for m in ops.members)
    by_service: dict[str, dict[str, int]] = defaultdict(lambda: {"add": 0, "remove": 0,
                                                                  "update": 0})
    key_of = {s.service_id: s.service_key for s in stored.services}
    for m in ops.members:
        if m.action in ("insert", "move", "restore"):
            by_service[m.service_key]["add"] += 1
            if m.action == "move" and m.from_service_id in key_of:
                by_service[key_of[m.from_service_id]]["remove"] += 1
        elif m.action in ("delete", "purge", "target_gone"):
            by_service[m.service_key]["remove"] += 1
        else:
            by_service[m.service_key]["update"] += 1
    cand_kinds = Counter(c.candidate.kind for c in ops.candidates)
    total = (len(new) + len(changed) + sum(1 for s in suggests if not s["unchanged"])
             + len(ops.members) + len(ops.candidates))
    return {
        "services": {"new": new, "changed": changed, "removed": removed,
                     "counts": {a: service_counts.get(a, 0)
                                for a in (OP_INSERT, OP_UPDATE, OP_SAME, OP_SUGGEST, OP_SKIP)}},
        "members": {"by_action": dict(sorted(member_counts.items())),
                    "by_service": {k: by_service[k] for k in sorted(by_service)}},
        "scan_suggests": suggests,
        "candidates": {"new": len(ops.candidates), "by_kind": dict(sorted(cand_kinds.items()))},
        "total_changes": total,
    }


def _diff_counts(diff: Mapping[str, Any]) -> dict[str, Any]:
    """실행 기록에 남길 차이 건수(목록 제외)."""
    return {
        "services": dict(diff["services"]["counts"]),
        "removed_services": len(diff["services"]["removed"]),
        "members": dict(diff["members"]["by_action"]),
        "scan_suggests": len(diff["scan_suggests"]),
        "candidates": int(diff["candidates"]["new"]),
        "total_changes": int(diff["total_changes"]),
    }


def _signal_rows(proposal: Proposal) -> list[dict[str, Any]]:
    return [
        {"signal": s.signal, "zone": s.zone, "enabled": s.enabled, "purity": s.purity,
         "evaluated": s.evaluated, "pure": s.pure, "reason": s.reason,
         "reason_text": s.reason_text}
        for s in proposal.signal_status
    ]


# ── 서비스 ──────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _Snapshot:
    """계산 입력 중 스냅샷·매핑 부분과 그 기준(`basis` — 적용 때 대조)."""

    servers: tuple[SmsServer, ...]
    apm_inventory: tuple[ApmInventoryRow, ...]
    apm_maps: tuple[ApmMapRow, ...]
    itam_apps: tuple[ItamAppRow, ...]
    itam_servers: tuple[ItamServerRow, ...]
    scanned_db_ids: frozenset[str]
    basis: dict[str, Any] = field(default_factory=dict)

    @property
    def scan_run_id(self) -> str | None:
        snapshots = self.basis.get("snapshots") or {}
        return snapshots.get(TABLE_SMS)


@dataclass(frozen=True)
class _AttrRead:
    rows: list[dict[str, Any]]
    failures: list[AttrFailure]
    reads: dict[str, Any]


def _map_fingerprint(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """210 활성 매핑 지문 — 활성 행 수 + (id · version) sha256(변경·추가·비활성·삭제를 잡는다)."""
    active = sorted(
        (int(r.get("id") or 0), int(r.get("version") or 0)) for r in rows
        if text(r.get("state")) == "active"
    )
    digest = hashlib.sha256(";".join(f"{i}:{v}" for i, v in active).encode()).hexdigest()
    return {"active": len(active), "fingerprint": digest}


def _scanned(scan_run: Mapping[str, Any] | None) -> tuple[frozenset[str], bool]:
    """SMS 스냅샷 run의 스캐너 기록 → 끝까지 읽은 db와 판정 가능 여부(기록 없으면 빈 집합)."""
    scanners = (scan_run or {}).get("scanners")
    sms = scanners.get(SCANNER_SMS) if isinstance(scanners, Mapping) else None
    zones = sms.get("zones") if isinstance(sms, Mapping) else None
    if not isinstance(zones, Mapping):
        return frozenset(), False
    return frozenset(str(d) for d, z in zones.items()
                     if isinstance(z, Mapping) and z.get("status") == SCANNER_OK), True


def _clip(column: str, value: Any) -> str | None:
    t = text(value)
    return t[:_ATTR_LIMITS[column]] if t else None


class BizServiceComputeService:
    """업무 서비스 미리보기·적용 — 한 API 프로세스에 하나(W4가 `app.state`에 둔다)."""

    def __init__(
        self,
        *,
        repo: _Store,
        map_repo: _SnapshotSource,
        cache: _Cache,
        lock: _Lock,
        scan_lock: _LockView,
        execute: SqlExecutor,
        settings: BizServiceSettings,
        enabled: bool,
        decisions: _Decisions | None = None,
        engine_of: Callable[[str], str] = db_engine_of,
        zone_of: Callable[[str], str] = registry_zone,
        page_rows: int = ATTR_PAGE_ROWS,
        lock_ttl_seconds: int = LOCK_TTL_SECONDS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """저장소 · 210 저장소 · 사본 · 락(`bizsvc:lock`) · 210 스캔 락 보기 · readonly SQL 실행기 ·
        설정(기동 시 1회 로드) · 플래그(`biz_service_active`) · 결정 이력(S11)을 받는다."""
        self._repo = repo
        self._maps = map_repo
        self._cache = cache
        self._lock = lock
        self._scan_lock = scan_lock
        self._execute = execute
        self._settings = settings
        self._enabled = enabled is True
        self._decisions = decisions
        self._engine_of = engine_of
        self._zone_of = zone_of
        self._page_rows = max(1, min(int(page_rows), ATTR_PAGE_ROWS))
        self._lock_ttl = int(lock_ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    # ── 진입 ───────────────────────────────────────────────────────────
    async def preview(self, requested_by: str) -> dict[str, Any]:
        """미리보기 1회 → 차이·신호 표·기준 스캔 run id(저장은 실행 기록·보강 속성만).

        Raises:
            BizServiceDisabledError · BizServiceBusyError · BizServiceScanRunningError
        """
        self._require_enabled()
        run_id = uuid.uuid4().hex
        async with self._exclusive(run_id):
            await self._require_scan_idle()
            snap = await self._load_snapshot()
            await self._repo.start_run(mode=MODE_PREVIEW, requested_by=requested_by,
                                       scan_run_id=snap.scan_run_id, run_id=run_id)
            try:
                read = await self._read_attrs(snap.servers)
                saved = await self._repo.save_attrs(run_id, read.rows)
                until = self._clock()
                minutes, s11_fail, s11_notes = await self._alarm_minutes(snap.servers, until)
                failures = [*read.failures, *s11_fail]
                basis = {**snap.basis, "attr_rows": saved, "alarm_until": until.isoformat(),
                         "attr_failures": [{"signal": f.signal, "reason": f.reason,
                                            "zone": f.zone} for f in failures]}
                stored, proposal, ops = await self._compute(
                    snap, attrs_from_rows(read.rows), minutes, failures)
                diff = describe_ops(stored, proposal, ops)
                reads = {**read.reads, "alarm": s11_notes}
                await self._repo.finish_run(run_id, status="done", summary={
                    **proposal.summary, "diff": _diff_counts(diff), "basis": basis,
                    "attr_reads": reads, "warnings": list(self._settings.warnings),
                })
            except BaseException as exc:
                await self._fail_run(run_id, exc)
                raise
        logger.info("업무 서비스 미리보기: run_id=%s by=%s 변경 %d건", run_id, requested_by,
                    diff["total_changes"])
        return {
            "run_id": run_id, "mode": MODE_PREVIEW, "scan_run_id": snap.scan_run_id,
            "basis": basis, **diff, "signals": _signal_rows(proposal),
            "attr_failures": basis["attr_failures"], "attr_reads": reads,
            "conflicts": len(proposal.conflicts), "summary": proposal.summary,
            "warnings": list(self._settings.warnings),
        }

    async def apply(self, preview_run_id: str, requested_by: str) -> dict[str, Any]:
        """미리보기 결과 적용 → 적용 건수·사본 재구성 결과·차이.

        Raises:
            BizServiceDisabledError · BizServicePreviewNotFoundError · BizServiceBusyError ·
            BizServiceScanRunningError · BizServiceSnapshotChangedError ·
            BizServiceExistsError(그 사이 같은 키 서비스가 생겼다 — 롤백)
        """
        self._require_enabled()
        prev = await self._repo.get_run(str(preview_run_id))
        prev_summary = prev.get("summary") if prev else None
        prev_basis = prev_summary.get("basis") if isinstance(prev_summary, Mapping) else None
        if (prev is None or prev.get("mode") != MODE_PREVIEW or prev.get("status") != "done"
                or not isinstance(prev_basis, Mapping)):
            raise BizServicePreviewNotFoundError(f"미리보기 없음: {preview_run_id}",
                                            preview_run_id=preview_run_id)
        run_id = uuid.uuid4().hex
        async with self._exclusive(run_id):
            await self._require_scan_idle()
            snap = await self._load_snapshot()
            changed = [k for k in ("snapshots", "maps") if prev_basis.get(k) != snap.basis.get(k)]
            if changed:
                raise BizServiceSnapshotChangedError("미리보기 뒤 스냅샷·연계 매핑이 바뀌었습니다",
                                                changed=changed, preview_run_id=preview_run_id)
            _rid, attr_rows = await self._repo.get_attrs(str(preview_run_id))
            if len(attr_rows) != int(prev_basis.get("attr_rows") or 0):
                raise BizServiceSnapshotChangedError(
                    "미리보기 보강 속성이 정리됐습니다 — 다시 계산",
                    changed=["attrs"], preview_run_id=preview_run_id)
            failures = [AttrFailure(text(f.get("signal")), text(f.get("reason")), f.get("zone"))
                        for f in prev_basis.get("attr_failures") or [] if isinstance(f, Mapping)]
            until = datetime.fromisoformat(str(prev_basis.get("alarm_until")))
            minutes, s11_fail, _ = await self._alarm_minutes(snap.servers, until)
            failures += [f for f in s11_fail if f not in failures]
            await self._repo.start_run(mode=MODE_APPLY, requested_by=requested_by,
                                       scan_run_id=snap.scan_run_id, run_id=run_id)
            try:
                stored, proposal, ops = await self._compute(
                    snap, attrs_from_rows(attr_rows), minutes, failures)
                diff = describe_ops(stored, proposal, ops)
                applied = await self._repo.apply_ops(ops, run_id=run_id, by=requested_by)
                cache = await self._sync_cache()
                await self._repo.finish_run(run_id, status="done", summary={
                    **proposal.summary, "diff": _diff_counts(diff), "applied": applied,
                    "basis": dict(prev_basis), "preview_run_id": str(preview_run_id),
                    "cache": cache,
                })
            except BaseException as exc:
                await self._fail_run(run_id, exc)
                raise
        logger.info("업무 서비스 적용: run_id=%s preview=%s by=%s 건수=%s", run_id,
                    preview_run_id, requested_by, applied)
        return {
            "run_id": run_id, "mode": MODE_APPLY, "preview_run_id": str(preview_run_id),
            "scan_run_id": snap.scan_run_id, "applied": applied, "cache": cache, **diff,
            "signals": _signal_rows(proposal), "summary": proposal.summary,
        }

    # ── 공통 ───────────────────────────────────────────────────────────
    def _require_enabled(self) -> None:
        if not self._enabled:
            raise BizServiceDisabledError("NOISE_BIZ_SERVICE_ENABLED off")

    async def _require_scan_idle(self) -> None:
        holder = await self._scan_lock.holder()
        if holder:
            raise BizServiceScanRunningError("APM·SMS 연계 스캔 실행 중", scan_run_id=holder)

    @asynccontextmanager
    async def _exclusive(self, run_id: str) -> AsyncIterator[None]:
        if not await self._lock.acquire(run_id, self._lock_ttl):
            raise BizServiceBusyError("업무 서비스 계산 실행 중", run_id=await self._lock.holder())
        try:
            yield
        finally:
            try:
                await self._lock.release(run_id)
            except Exception:  # noqa: BLE001 — 해제 실패는 수명(600초) 뒤 풀린다
                logger.warning("업무 서비스 락 해제 실패: run_id=%s", run_id, exc_info=True)

    async def _fail_run(self, run_id: str, exc: BaseException) -> None:
        try:
            await self._repo.finish_run(run_id, status="failed",
                                        summary={"error_kind": _error_kind(exc)})
        except Exception:  # noqa: BLE001 — 원래 예외를 가리지 않는다
            logger.warning("업무 서비스 실행 기록 실패 표시 실패: run_id=%s", run_id,
                           exc_info=True)

    async def _sync_cache(self) -> dict[str, Any]:
        """사본 재구성 — PG는 이미 커밋됐으므로 Redis 오류는 결과에 사유로 싣고 올리지 않는다."""
        try:
            return {"ok": True, **await self._cache.sync_from_repo(self._repo)}
        except Exception as exc:  # noqa: BLE001 — 다음 적용·기동이 다시 재구성한다
            logger.warning("업무 서비스 사본 재구성 실패: %s", _error_kind(exc), exc_info=True)
            return {"ok": False, "error_kind": _error_kind(exc)}

    async def _load_snapshot(self) -> _Snapshot:
        runs = {t: await self._maps.current_snapshot_run(t) for t in SNAPSHOT_TABLES}
        rows = {t: (await self._maps.snapshot_rows(t)) if runs[t] else [] for t in SNAPSHOT_TABLES}
        map_rows = await self._maps.all_rows()
        sms_run = runs[TABLE_SMS]
        scanned, known = _scanned(await self._maps.get_scan_run(sms_run) if sms_run else None)
        basis = {"snapshots": runs, "maps": _map_fingerprint(map_rows),
                 "scanned_db_ids": sorted(scanned), "scanned_known": known}
        return _Snapshot(
            sms_servers(rows[TABLE_SMS], self._zone_of), apm_inventory(rows[TABLE_APM]),
            apm_maps(map_rows), itam_apps(rows[TABLE_ITAM_APP]),
            itam_servers(rows[TABLE_ITAM_SERVER]), scanned, basis,
        )

    async def _compute(
        self, snap: _Snapshot, attrs: tuple[ServerAttr, ...], minutes: tuple[AlarmMinute, ...],
        failures: Sequence[AttrFailure],
    ) -> tuple[StoredState, Proposal, ApplyOps]:
        stored = stored_state(await self._repo.all_services_with_members(),
                              await self._repo.all_candidates())
        inputs = ProposalInputs(
            servers=snap.servers, apm_inventory=snap.apm_inventory, apm_maps=snap.apm_maps,
            itam_apps=snap.itam_apps, itam_servers=snap.itam_servers, attrs=attrs,
            alarm_minutes=minutes, attr_failures=tuple(failures), stored=stored,
            generic_terms=self._settings.generic_terms, scanned_db_ids=snap.scanned_db_ids,
        )
        proposal = compute_proposal(inputs, self._settings.config,
                                    await self._previous_summary())
        return stored, proposal, plan_apply(stored, proposal, now=self._clock())

    async def _previous_summary(self) -> Mapping[str, Any] | None:
        """직전 적용(완료) 실행 기록의 요약 — 변화 지표 기준(없으면 None)."""
        for run in await self._repo.list_runs(mode=MODE_APPLY, limit=_PREVIOUS_LOOKBACK):
            summary = run.get("summary")
            if run.get("status") == "done" and isinstance(summary, Mapping) \
                    and isinstance(summary.get("services"), Mapping):
                return summary
        return None

    # ── 보강 읽기 ───────────────────────────────────────────────────────
    async def _read_attrs(self, servers: Sequence[SmsServer]) -> _AttrRead:
        """§3.2.4 ①②③ — 신호별 개별 실패 · 필요한 신호가 모두 꺼져 있으면 SELECT 0."""
        cfg = self._settings.config
        server_signals = [s for s in SERVER_READ_SIGNALS if cfg.signal_enabled(s)]
        need_tree = cfg.signal_enabled(SIG_TREE_GROUP)
        generic = fold_terms(self._settings.generic_terms)
        by_db: dict[str, list[SmsServer]] = defaultdict(list)
        for s in servers:
            by_db[s.ref[0]].append(s)
        rows: dict[ServerRef, dict[str, Any]] = {}
        failures: list[AttrFailure] = []
        zones: dict[str, Any] = {}
        for db_id in sorted(by_db) if server_signals else ():
            zone, engine = by_db[db_id][0].zone, self._engine_of(db_id)
            info: dict[str, Any] = {"zone": zone, "engine": engine}
            zones[db_id] = info
            try:
                got = await read_server_attrs(self._execute, db_id, engine,
                                              page_rows=self._page_rows)
            except Exception as exc:  # noqa: BLE001 — 그 존의 ① 신호만 끄고 계속한다
                kind = _error_kind(exc)
                logger.warning("업무 서비스 보강 읽기 — 서버 행 실패: db_id=%s err=%s", db_id, kind)
                info.update(status="failed", error_kind=kind)
                failures += [AttrFailure(sig, f"서버 행 읽기 실패({db_id} · {kind})", zone)
                             for sig in server_signals]
                continue
            info.update(status="ok", servers=len(got))
            folders: dict[str, FolderRow] | None = None
            if need_tree:
                wanted = {a for s in by_db[db_id] if s.ref[1] in got
                          for a in got[s.ref[1]].ancestors}
                try:
                    folders = (await read_tree_folders(self._execute, db_id, engine, wanted)
                               if wanted else {})
                    info["folders"] = len(folders)
                except Exception as exc:  # noqa: BLE001 — S7만 끄고 계속한다
                    kind = _error_kind(exc)
                    logger.warning("업무 서비스 보강 읽기 — 트리 폴더 실패: db_id=%s err=%s",
                                   db_id, kind)
                    info["folders_error_kind"] = kind
                    failures.append(AttrFailure(SIG_TREE_GROUP,
                                                f"트리 폴더 읽기 실패({db_id} · {kind})", zone))
            for s in by_db[db_id]:
                item = got.get(s.ref[1])
                if item is None:
                    continue
                row: dict[str, Any] = {"db_id": s.ref[0], "resource_id": s.ref[1],
                                       "registered_at": _ms_to_dt(item.ctime_ms),
                                       "depends": list(item.depends)}
                if folders:
                    path = tuple(TreeFolder(f, folders[f].name) for f in item.ancestors
                                 if f in folders)
                    group = tree_group_of(s, ServerAttr(s.ref[0], s.ref[1], tree_path=path),
                                          generic)
                    if group is not None:
                        row.update(tree_group_id=_clip("tree_group_id", group.folder_id),
                                   tree_group_name=_clip("tree_group_name", group.name),
                                   tree_code=_clip("tree_code", group.tree_code))
                rows[s.ref] = row
        itam: dict[str, Any] | None = None
        if cfg.signal_enabled(SIG_ITAM_CONTRACT):
            itam = await self._read_itam(servers, rows, failures)
        return _AttrRead([rows[r] for r in sorted(rows)], failures,
                         {"zones": zones, "itam": itam})

    async def _read_itam(
        self, servers: Sequence[SmsServer], rows: dict[ServerRef, dict[str, Any]],
        failures: list[AttrFailure],
    ) -> dict[str, Any]:
        """③ ITAM 계약 칸 → 스냅샷 서버 1대 일치만 — 한 서버에 값이 갈리면 그 칸은 비운다."""
        try:
            contracts = await read_itam_contracts(self._execute, page_rows=self._page_rows)
        except Exception as exc:  # noqa: BLE001 — S10만 끄고 계속한다
            kind = _error_kind(exc)
            logger.warning("업무 서비스 보강 읽기 — ITAM 계약 실패: err=%s", kind)
            failures.append(AttrFailure(SIG_ITAM_CONTRACT, f"ITAM 계약 읽기 실패({kind})", None))
            return {"status": "failed", "error_kind": kind}
        index = _host_index(servers)
        values: dict[ServerRef, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        notes: Counter[str] = Counter()
        for c in contracts:
            refs = index.get(canonical_host_key(c.hostname), set())
            if len(refs) != 1:
                notes["unmatched" if not refs else "ambiguous"] += 1
                continue
            ref = next(iter(refs))
            for col in ("contract_no", "contract_name", "maint_contract_no", "owner_branch"):
                if text(getattr(c, col)):
                    values[ref][col].add(text(getattr(c, col)))
        for ref, cols in values.items():
            row = rows.setdefault(ref, {"db_id": ref[0], "resource_id": ref[1]})
            for col, found in cols.items():
                if len(found) == 1:
                    row[col] = _clip(col, next(iter(found)))
                else:
                    notes[f"split_{col}"] += 1
        notes["matched_servers"] = len(values)
        return {"status": "ok", "rows": len(contracts), **dict(sorted(notes.items()))}

    async def _alarm_minutes(
        self, servers: Sequence[SmsServer], until: datetime
    ) -> tuple[tuple[AlarmMinute, ...], list[AttrFailure], dict[str, Any]]:
        """S11 입력 — `until`에서 `lookback_days` 전까지의 결정 이력(on일 때만)."""
        cfg = self._settings.config
        if not cfg.signal_enabled(SIG_ALARM_COOCCURRENCE):
            return (), [], {}
        if self._decisions is None:
            return (), [AttrFailure(SIG_ALARM_COOCCURRENCE, "결정 이력 미연결", None)], \
                {"status": "failed", "error_kind": "no_decision_store"}
        lookback = timedelta(days=cfg.aux.alarm_cooccurrence.lookback_days)
        window = lookback + max(timedelta(0), self._clock() - until) + timedelta(minutes=1)
        try:
            records = await asyncio.to_thread(self._decisions.recent_decisions,
                                              int(window.total_seconds()))
        except Exception as exc:  # noqa: BLE001 — S11만 끄고 계속한다
            kind = _error_kind(exc)
            logger.warning("업무 서비스 S11 — 결정 이력 읽기 실패: %s", kind)
            return (), [AttrFailure(SIG_ALARM_COOCCURRENCE, f"결정 이력 읽기 실패({kind})",
                                    None)], {"status": "failed", "error_kind": kind}
        minutes, notes = alarm_minutes_from(records, servers,
                                            since_ts=(until - lookback).timestamp(),
                                            until_ts=until.timestamp())
        return minutes, [], {"status": "ok", "records": len(records), **notes}


def build_biz_service(
    app_config: Any, *, repo: Any, map_repo: Any, cache: Any, decisions: Any = None
) -> BizServiceComputeService:
    """설정·저장소·210 저장소·사본(`RedisBizServiceCache` — `.redis`로 두 락)으로 조립한다."""
    from noise_gate.application.biz_service_config import load_biz_service_config
    from noise_gate.domain.biz_service_model import DEFAULT_MAX_MEMBERS
    from noise_gate.infrastructure.apm_host_map_scan_lock import RedisScanLock
    from noise_gate.infrastructure.apm_host_map_sql_scanners import registry_executor

    ng = getattr(app_config, "noise_gate", None)
    settings = load_biz_service_config(
        max_members=int(getattr(ng, "biz_service_max_members", DEFAULT_MAX_MEMBERS)))
    return BizServiceComputeService(
        repo=repo, map_repo=map_repo, cache=cache,
        lock=RedisScanLock(cache.redis, key=BIZSVC_LOCK_KEY), scan_lock=RedisScanLock(cache.redis),
        execute=registry_executor(app_config), settings=settings,
        enabled=getattr(ng, "biz_service_active", False) is True, decisions=decisions,
    )


__all__ = [
    "BIZSVC_LOCK_KEY",
    "LOCK_TTL_SECONDS",
    "BizServiceBusyError",
    "BizServiceComputeService",
    "BizServiceDisabledError",
    "BizServiceError",
    "BizServicePreviewNotFoundError",
    "BizServiceScanRunningError",
    "BizServiceSnapshotChangedError",
    "alarm_minutes_from",
    "attrs_from_rows",
    "build_biz_service",
    "describe_ops",
    "stored_state",
]
