"""plans/215 W3 — 계산 서비스 종단(가짜 스냅샷·가짜 저장소·가짜 실행기·가짜 락 · 실 PG·Redis 불요).

메모리 저장소 가짜의 `apply_ops`는 실 저장소 SQL의 WHERE 가드
(자동 행만 · excluded 제외 · 상태 조건)를 그대로 옮긴 판정으로 연산을 실행한다.
수용 1·4·8·31 · `snapshot_changed`·`scan_running` · 락 · 플래그 off·신호 off 보강 SELECT 0 ·
보강 실패 신호 단위 · S11 집계를 본다.
"""

from __future__ import annotations

import itertools
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from noise_gate.application import biz_service as svc
from noise_gate.application.biz_service import (
    BizServiceBusyError,
    BizServiceComputeService,
    BizServiceDisabledError,
    BizServicePreviewNotFoundError,
    BizServiceScanRunningError,
    BizServiceSnapshotChangedError,
    alarm_minutes_from,
)
from noise_gate.application.biz_service_config import BizServiceSettings
from noise_gate.domain import biz_service_model as m
from noise_gate.domain.biz_service_apply import ApplyOps
from noise_gate.infrastructure.apm_host_map_sql_scanners import ITAM_DB_ID
from noise_gate.infrastructure.biz_service_repository import BizServiceExistsError, candidate_key
from noise_gate.infrastructure.biz_service_sql import ITAM_PII_COLUMNS

GENERIC = frozenset({"운영", "개발", "서버", "web", "was"})
NO_S6 = m.BizServiceConfig(host_prefix=m.HostPrefixConfig(enabled=False))


# ── 가짜 ────────────────────────────────────────────────────────────────
class FakeStore:
    """업무 서비스 저장소 메모리 가짜(계산 서비스가 쓰는 메서드만)."""

    def __init__(self) -> None:
        self.services: dict[int, dict[str, Any]] = {}
        self.members: dict[int, dict[str, Any]] = {}
        self.candidates: dict[int, dict[str, Any]] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.attrs: dict[str, list[dict[str, Any]]] = {}
        self.apply_calls = 0
        self._ids = itertools.count(1)
        self._tick = itertools.count(1)

    # 읽기
    async def all_services_with_members(self) -> list[dict[str, Any]]:
        return [{**s, "members": [dict(mm) for mm in self._members_of(sid)]}
                for sid, s in sorted(self.services.items())]

    async def active_services_with_members(self) -> list[dict[str, Any]]:
        return [{**s, "members": [dict(mm) for mm in self._members_of(sid)
                                  if mm["state"] == "active"]}
                for sid, s in sorted(self.services.items()) if s["state"] == "active"]

    async def all_candidates(self) -> list[dict[str, Any]]:
        return [dict(c) for _, c in sorted(self.candidates.items())]

    def _members_of(self, sid: int) -> list[dict[str, Any]]:
        return [mm for _, mm in sorted(self.members.items()) if mm["service_id"] == sid]

    def member(self, key: str) -> dict[str, Any]:
        return next(mm for mm in self.members.values() if mm["member_key"] == key)

    def service(self, key: str) -> dict[str, Any]:
        return next(s for s in self.services.values() if s["service_key"] == key)

    # 실행 기록
    async def start_run(self, *, mode: str, requested_by: str = "",
                        scan_run_id: str | None = None,
                        run_id: str | None = None) -> dict[str, Any]:
        rid = run_id or f"r{next(self._ids)}"
        self.runs[rid] = {"run_id": rid, "mode": mode, "requested_by": requested_by,
                          "scan_run_id": scan_run_id, "status": "running", "summary": {},
                          "seq": next(self._tick)}
        return dict(self.runs[rid])

    async def finish_run(self, run_id: str, *, status: str,
                         summary: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        self.runs[run_id].update(status=status, summary=dict(summary or {}))
        return dict(self.runs[run_id])

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.runs.get(run_id)
        return dict(run) if run else None

    async def list_runs(self, *, mode: str | None = None, limit: int = 20
                        ) -> list[dict[str, Any]]:
        runs = sorted(self.runs.values(), key=lambda r: -r["seq"])
        return [dict(r) for r in runs if mode is None or r["mode"] == mode][:limit]

    # 보강 속성
    async def save_attrs(self, run_id: str, rows: Sequence[Mapping[str, Any]]) -> int:
        kept = [dict(r) for r in rows if r.get("db_id") and r.get("resource_id")]
        self.attrs[run_id] = kept
        for old in list(self.attrs)[:-3]:
            del self.attrs[old]
        return len(kept)

    async def get_attrs(self, run_id: str | None = None
                        ) -> tuple[str | None, list[dict[str, Any]]]:
        return run_id, [dict(r) for r in self.attrs.get(run_id or "", [])]

    # 적용 — 실 저장소 SQL의 WHERE 가드와 같은 판정
    async def apply_ops(self, ops: ApplyOps, *, run_id: str, by: str = "") -> dict[str, int]:
        self.apply_calls += 1
        counts: dict[str, int] = {}

        def bump(name: str) -> None:
            counts[name] = counts.get(name, 0) + 1

        ids = {s["service_key"]: sid for sid, s in self.services.items()}
        for op in ops.services:
            if op.action == "insert":
                if op.service_key in ids:
                    raise BizServiceExistsError(ids[op.service_key])
                sid = next(self._ids)
                self.services[sid] = {
                    "id": sid, "service_key": op.service_key, "display_name": op.display_name,
                    "origin": "auto", "state": op.state, "verified": False, "monitored": False,
                    "evidence": dict(op.evidence), "version": 1, "last_run_id": run_id}
                ids[op.service_key] = sid
                bump("service_insert")
                continue
            s = self.services.get(op.service_id or -1)
            if s is None or op.action == "skip":
                continue
            if op.action == "update" and s["origin"] == "auto" and not s["verified"] \
                    and s["state"] != "disabled":
                s.update(display_name=op.display_name, state=op.state,
                         evidence=dict(op.evidence))
            elif op.action == "suggest" and s["state"] != "disabled":
                s["evidence"] = {**s["evidence"],
                                 "scan_suggests": dict(op.evidence).get("scan_suggests")}
            bump(f"service_{op.action}")
        by_key = {(mm["kind"], mm["member_key"]): mid for mid, mm in self.members.items()}
        for mop in ops.members:
            sid = mop.service_id if mop.service_id is not None else ids.get(mop.service_key)
            mid = by_key.get((mop.kind, mop.member_key))
            mm = self.members.get(mid) if mid is not None else None
            if mop.action == "insert":
                if mm is not None or sid is None:
                    continue
                new = next(self._ids)
                self.members[new] = {
                    "id": new, "service_id": sid, "kind": mop.kind, "member_key": mop.member_key,
                    "origin": "auto", "basis": mop.basis, "confidence": mop.confidence,
                    "state": "active", "apm_map_id": mop.apm_map_id, "layer": mop.layer,
                    "layer_origin": mop.layer_origin, "evidence": dict(mop.evidence),
                    "gone_at": None, **dict(mop.display)}
                by_key[(mop.kind, mop.member_key)] = new
            elif mm is None:
                continue
            elif mop.action in ("update", "move"):
                where_sid = mop.from_service_id if mop.action == "move" else sid
                if mm["origin"] != "auto" or mm["state"] == "excluded" \
                        or mm["service_id"] != where_sid:
                    continue
                mm.update(service_id=sid, basis=mop.basis, confidence=mop.confidence,
                          state="active", gone_at=None, apm_map_id=mop.apm_map_id,
                          layer=mop.layer, layer_origin=mop.layer_origin,
                          evidence=dict(mop.evidence))
            elif mop.action == "target_gone":
                if mm["state"] != "active" or mm["service_id"] != sid:
                    continue
                mm.update(state="target_gone", gone_at="2026-11-05T00:00:00+00:00")
            elif mop.action == "restore":
                if mm["state"] != "target_gone":
                    continue
                mm.update(state="active", gone_at=None)
            elif mop.action in ("delete", "purge"):
                ok = mm["origin"] == "auto" and (
                    mm["state"] == "target_gone" if mop.action == "purge"
                    else mm["state"] != "excluded")
                if not ok:
                    continue
                del self.members[mid]  # type: ignore[arg-type]
                del by_key[(mop.kind, mop.member_key)]
            bump(f"member_{mop.action}")
        keys = {c["candidate_key"] for c in self.candidates.values()}
        for cop in ops.candidates:
            c = cop.candidate
            ck = candidate_key(c.kind, c.service_key, c.target_kind, c.target_key)
            if ck in keys:
                continue
            cid = next(self._ids)
            self.candidates[cid] = {
                "id": cid, "kind": c.kind, "candidate_key": ck, "status": "open",
                "evidence": {"service_key": c.service_key, "target_kind": c.target_kind,
                             "target_key": c.target_key}}
            keys.add(ck)
            bump("candidate_insert")
        return counts


class FakeMaps:
    """210 저장소 가짜 — 현재 스냅샷 포인터·행 · 매핑 행 · 스캔 실행 기록."""

    def __init__(self, sms: list[dict[str, Any]], *, run_id: str = "scan1") -> None:
        self.current: dict[str, str] = {"sms_inventory": run_id}
        self.rows: dict[str, list[dict[str, Any]]] = {"sms_inventory": sms}
        self.maps: list[dict[str, Any]] = []
        self.scan_runs: dict[str, dict[str, Any]] = {}
        self.set_scan(run_id, sorted({r["db_id"] for r in sms}))

    def set_scan(self, run_id: str, ok_dbs: list[str]) -> None:
        self.current["sms_inventory"] = run_id
        self.scan_runs[run_id] = {"run_id": run_id, "scanners": {"sms": {
            "status": "ok", "zones": {d: {"status": "ok"} for d in ok_dbs}}}}

    async def current_snapshot_run(self, table: str) -> str | None:
        return self.current.get(table)

    async def snapshot_rows(self, table: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.rows.get(table, [])]

    async def all_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.maps]

    async def get_scan_run(self, run_id: str) -> dict[str, Any] | None:
        return self.scan_runs.get(run_id)


class FakeLock:
    """`SET NX` 락 가짜 — 자기 값일 때만 해제한다."""

    def __init__(self, holder: str | None = None) -> None:
        self.value = holder
        self.acquired: list[tuple[str, int]] = []

    async def acquire(self, run_id: str, ttl_seconds: int = 600) -> bool:
        if self.value is not None:
            return False
        self.value = run_id
        self.acquired.append((run_id, ttl_seconds))
        return True

    async def holder(self) -> str | None:
        return self.value

    async def release(self, run_id: str) -> None:
        if self.value == run_id:
            self.value = None


class FakeCache:
    def __init__(self) -> None:
        self.synced: list[int] = []

    async def sync_from_repo(self, repo: Any) -> dict[str, int]:
        services = await repo.active_services_with_members()
        self.synced.append(len(services))
        return {"services": len(services)}


class FakeExec:
    """보강 SELECT 가짜 — ① 서버 행 · ② 폴더 · ③ ITAM. `fail`에 든 종류는 예외."""

    def __init__(self, servers: dict[str, dict[str, Any]], folders: dict[str, str],
                 itam: list[dict[str, Any]] | None = None) -> None:
        self.servers = servers
        self.folders = folders
        self.itam = itam or []
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        self.calls.append((db_id, sql))
        if db_id == ITAM_DB_ID:
            if "itam" in self.fail:
                raise RuntimeError("itam down")
            return list(self.itam)
        if "id_ancestry" in sql:
            if "server" in self.fail:
                raise RuntimeError("db down")
            after = int(re.search(r"r\.id > (\d+)", sql).group(1))  # type: ignore[union-attr]
            limit = int(re.search(r"LIMIT (\d+)", sql).group(1))  # type: ignore[union-attr]
            rows = sorted((int(k), v) for k, v in self.servers.items() if int(k) > after)
            return [{"resource_id": k, **v} for k, v in rows][:limit]
        if "f.id IN" in sql:
            if "folder" in self.fail:
                raise RuntimeError("folder down")
            ids = re.search(r"IN \(([^)]*)\)", sql).group(1).split(", ")  # type: ignore[union-attr]
            return [{"folder_id": i, "folder_name": self.folders[i], "resource_type": "g"}
                    for i in ids if i in self.folders]
        raise AssertionError(sql)


class FakeDecisions:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.windows: list[int] = []

    def recent_decisions(self, window_seconds: int) -> list[dict[str, Any]]:
        self.windows.append(window_seconds)
        return list(self.records)


# ── 픽스처 ──────────────────────────────────────────────────────────────
NAMES = {
    "101": "ibkweb01(인터넷뱅킹)", "102": "ibkwas01(인터넷뱅킹)", "103": "ibkdb01(인터넷뱅킹)",
    "201": "cardweb01(카드)", "202": "cardwas01(카드)", "301": "etcsrv01",
}


def _sms(names: Mapping[str, str] = NAMES) -> list[dict[str, Any]]:
    return [{"db_id": "pg_a", "resource_id": rid, "server_name": name,
             "hostname": name.split("(")[0], "ip": f"10.0.{int(rid) // 100}.{int(rid) % 100}",
             "nic_ips": []} for rid, name in names.items()]


def _attr_servers() -> dict[str, dict[str, Any]]:
    folder = {"1": "20", "2": "30", "3": "40"}
    return {rid: {"id_ancestry": f"1>10>{folder[rid[0]]}>{rid}>", "ctime": 1574822258074 + i,
                  "depend_1": None, "depend_2": None}
            for i, rid in enumerate(NAMES)}


FOLDERS = {"10": "운영", "20": "인터넷뱅킹시스템(IBK)", "30": "카드시스템", "40": "기타"}


def _settings(config: m.BizServiceConfig = NO_S6) -> BizServiceSettings:
    return BizServiceSettings(config, GENERIC, ("경고 예시",))


def _service(*, store: FakeStore | None = None, maps: FakeMaps | None = None,
             execute: FakeExec | None = None, config: m.BizServiceConfig = NO_S6,
             enabled: bool = True, lock: FakeLock | None = None,
             scan_lock: FakeLock | None = None, decisions: Any = None
             ) -> tuple[BizServiceComputeService, dict[str, Any]]:
    parts: dict[str, Any] = {
        "store": store or FakeStore(), "maps": maps or FakeMaps(_sms()),
        "exec": execute or FakeExec(_attr_servers(), FOLDERS), "lock": lock or FakeLock(),
        "scan_lock": scan_lock or FakeLock(), "cache": FakeCache(),
    }
    service = BizServiceComputeService(
        repo=parts["store"], map_repo=parts["maps"], cache=parts["cache"], lock=parts["lock"],
        scan_lock=parts["scan_lock"], execute=parts["exec"], settings=_settings(config),
        enabled=enabled, decisions=decisions, engine_of=lambda db: "postgresql",
        zone_of=lambda db: "z1",
    )
    return service, parts


# ── 종단 ────────────────────────────────────────────────────────────────
async def test_preview_apply_then_same_snapshot_has_zero_diff() -> None:
    """수용 1 — 적용 뒤 같은 스냅샷 재계산은 차이 0 · 미리보기는 저장 0."""
    service, p = _service()
    first = await service.preview("adm")
    assert {s["service_key"] for s in first["services"]["new"]} == {"인터넷뱅킹", "카드"}
    assert first["total_changes"] > 0 and first["scan_run_id"] == "scan1"
    assert p["store"].services == {} and p["store"].apply_calls == 0  # 미리보기 저장 0
    assert p["store"].runs[first["run_id"]]["mode"] == "preview"
    assert p["lock"].value is None and p["lock"].acquired[0][1] == 600

    applied = await service.apply(first["run_id"], "adm")
    assert applied["applied"]["service_insert"] == 2
    assert applied["applied"]["member_insert"] == 5
    assert applied["cache"] == {"ok": True, "services": 2}
    assert p["store"].runs[applied["run_id"]]["mode"] == "apply"
    assert p["store"].service("카드")["origin"] == "auto"

    second = await service.preview("adm")
    assert second["total_changes"] == 0
    assert second["services"]["new"] == [] and second["members"]["by_action"] == {}


async def test_row_order_does_not_change_result() -> None:
    """수용 1 — 스냅샷 행 순서를 섞어도 같은 차이·요약."""
    service, p = _service()
    a = await service.preview("adm")
    random.Random(7).shuffle(p["maps"].rows["sms_inventory"])
    b = await service.preview("adm")
    for key in ("services", "members", "candidates", "total_changes", "summary", "signals"):
        assert a[key] == b[key], key


async def test_admin_decisions_survive_recompute() -> None:
    """수용 4 — 제외·확인·「서비스 아님」·거절 후보는 재계산·적용이 바꾸지 않는다."""
    service, p = _service()
    await service.apply((await service.preview("adm"))["run_id"], "adm")
    store: FakeStore = p["store"]
    store.member("pg_a|103").update(state="excluded")
    store.service("카드").update(verified=True)
    store.member("pg_a|101").update(layer="db", layer_origin="manual")
    for c in store.candidates.values():
        c["status"] = "rejected"
    rejected = {c["candidate_key"] for c in store.candidates.values()}
    # 카드에 서버가 하나 늘어난 새 스냅샷 — verified 서비스는 scan_suggests만
    p["maps"].rows["sms_inventory"] = _sms({**NAMES, "203": "carddb01(카드)"})
    p["maps"].set_scan("scan2", ["pg_a"])

    preview = await service.preview("adm")
    suggest = {s["service_key"]: s["suggest"] for s in preview["scan_suggests"]}
    assert suggest["카드"] == {"add": ["pg_a|203"]}
    await service.apply(preview["run_id"], "adm")

    assert store.member("pg_a|103")["state"] == "excluded"
    assert store.member("pg_a|101")["layer"] == "db"
    assert store.member("pg_a|101")["layer_origin"] == "manual"
    card_members = {mm["member_key"] for mm in store._members_of(store.service("카드")["id"])}
    assert card_members == {"pg_a|201", "pg_a|202"}  # verified — 자동이 넣지 않는다
    assert store.service("카드")["evidence"]["scan_suggests"] == {"add": ["pg_a|203"]}
    assert all(store.candidates[c]["status"] == "rejected" for c in store.candidates
               if store.candidates[c]["candidate_key"] in rejected)
    assert len({c["candidate_key"] for c in store.candidates.values()}) == len(store.candidates)


async def test_run_summary_trend_in_run_order() -> None:
    """수용 8 — 실행 기록 summary에 §3.4 지표 · 적용 순으로 쌓이고 두 번째는 직전 대비 변화."""
    service, p = _service()
    await service.apply((await service.preview("adm"))["run_id"], "adm")
    p["maps"].rows["sms_inventory"] = _sms({**NAMES, "104": "ibkbat01(인터넷뱅킹)"})
    p["maps"].set_scan("scan2", ["pg_a"])
    await service.apply((await service.preview("adm"))["run_id"], "adm")
    runs = await p["store"].list_runs(mode="apply")
    assert len(runs) == 2
    newest, oldest = runs
    assert oldest["summary"]["change"] == {"baseline": False}
    assert newest["summary"]["change"]["baseline"] is True
    assert newest["summary"]["services"]["인터넷뱅킹"]["sms_servers"] == 4
    assert oldest["summary"]["services"]["인터넷뱅킹"]["sms_servers"] == 3
    assert "diff" in newest["summary"] and "applied" in newest["summary"]


# ── 적용 거부 ───────────────────────────────────────────────────────────
async def test_snapshot_changed_after_preview() -> None:
    service, p = _service()
    preview = await service.preview("adm")
    p["maps"].set_scan("scan9", ["pg_a"])
    with pytest.raises(BizServiceSnapshotChangedError) as exc:
        await service.apply(preview["run_id"], "adm")
    assert exc.value.code == "snapshot_changed" and exc.value.detail["changed"] == ["snapshots"]
    assert p["store"].apply_calls == 0 and p["lock"].value is None


async def test_map_change_after_preview_is_snapshot_changed() -> None:
    service, p = _service()
    p["maps"].maps = [{"id": 1, "state": "active", "version": 1, "source_id": "s",
                       "domain_id": "d", "scope": "domain", "match_key": "", "targets": []}]
    preview = await service.preview("adm")
    p["maps"].maps[0]["version"] = 2
    with pytest.raises(BizServiceSnapshotChangedError) as exc:
        await service.apply(preview["run_id"], "adm")
    assert exc.value.detail["changed"] == ["maps"]


async def test_expired_attrs_is_snapshot_changed() -> None:
    service, p = _service()
    preview = await service.preview("adm")
    assert p["store"].attrs[preview["run_id"]]
    del p["store"].attrs[preview["run_id"]]
    with pytest.raises(BizServiceSnapshotChangedError) as exc:
        await service.apply(preview["run_id"], "adm")
    assert exc.value.detail["changed"] == ["attrs"]


async def test_scan_running_blocks_preview_and_apply() -> None:
    service, p = _service()
    preview = await service.preview("adm")
    p["scan_lock"].value = "scan-live"
    with pytest.raises(BizServiceScanRunningError) as exc:
        await service.preview("adm")
    assert exc.value.code == "scan_running"
    with pytest.raises(BizServiceScanRunningError):
        await service.apply(preview["run_id"], "adm")
    assert p["lock"].value is None  # 자기 락은 풀고 나간다


async def test_lock_held_is_busy_and_not_released() -> None:
    service, p = _service(lock=FakeLock("other-run"))
    with pytest.raises(BizServiceBusyError) as exc:
        await service.preview("adm")
    assert exc.value.detail["run_id"] == "other-run"
    assert p["lock"].value == "other-run"  # 남의 락은 건드리지 않는다
    assert p["exec"].calls == []


async def test_preview_not_found() -> None:
    service, p = _service()
    with pytest.raises(BizServicePreviewNotFoundError):
        await service.apply("nope", "adm")
    applied = await service.apply((await service.preview("adm"))["run_id"], "adm")
    with pytest.raises(BizServicePreviewNotFoundError):  # 적용 run은 미리보기가 아니다
        await service.apply(applied["run_id"], "adm")


async def test_apply_uses_preview_attrs_without_select() -> None:
    """적용은 같은 미리보기 run의 보강 속성을 쓴다 — 적용 때 보강 SELECT 0."""
    service, p = _service()
    preview = await service.preview("adm")
    rows = p["store"].attrs[preview["run_id"]]
    ibk = next(r for r in rows if r["resource_id"] == "101")
    assert ibk["tree_group_name"] == "인터넷뱅킹시스템(IBK)" and ibk["tree_code"] == "IBK"
    assert ibk["registered_at"] == datetime.fromtimestamp(1574822258.074, tz=UTC)
    n = len(p["exec"].calls)
    await service.apply(preview["run_id"], "adm")
    assert len(p["exec"].calls) == n


# ── 플래그 · 신호 off · 보강 실패 ───────────────────────────────────────
async def test_flag_off_no_select() -> None:
    """수용 31 — 플래그 off → 보강 SELECT 0."""
    service, p = _service(enabled=False)
    with pytest.raises(BizServiceDisabledError):
        await service.preview("adm")
    with pytest.raises(BizServiceDisabledError):
        await service.apply("x", "adm")
    assert p["exec"].calls == [] and p["store"].runs == {}


async def test_all_attr_signals_off_no_select() -> None:
    aux = m.AuxSignalsConfig(registered_batch=m.RegisteredBatchConfig(enabled=False),
                             avail_dependency=m.AvailDependencyConfig(enabled=False))
    config = replace(NO_S6, tree_group=m.TreeGroupConfig(enabled=False), aux=aux)
    service, p = _service(config=config)
    out = await service.preview("adm")
    assert p["exec"].calls == []
    assert out["attr_reads"]["zones"] == {} and out["attr_reads"]["itam"] is None


async def test_folder_failure_turns_off_only_s7() -> None:
    """수용 31 — 트리 폴더 SELECT 실패 → S7만 꺼짐(사유 노출) · 나머지 계산 정상."""
    ex = FakeExec(_attr_servers(), FOLDERS)
    ex.fail.add("folder")
    service, p = _service(execute=ex)
    out = await service.preview("adm")
    status = {(s["signal"], s["zone"]): s for s in out["signals"]}
    s7 = status[(m.SIG_TREE_GROUP, "z1")]
    assert s7["enabled"] is False and s7["reason"] == m.REASON_ATTR_FAILED
    assert "트리 폴더 읽기 실패" in s7["reason_text"]
    assert status[(m.SIG_REGISTERED_BATCH, "z1")]["reason"] != m.REASON_ATTR_FAILED
    assert [f["signal"] for f in out["attr_failures"]] == [m.SIG_TREE_GROUP]
    assert {s["service_key"] for s in out["services"]["new"]} == {"인터넷뱅킹", "카드"}
    rows = p["store"].attrs[out["run_id"]]
    assert rows and all("tree_group_name" not in r for r in rows)
    assert all(r["registered_at"] is not None for r in rows)  # ① 결과는 그대로 쓴다


async def test_server_row_failure_turns_off_server_signals() -> None:
    ex = FakeExec(_attr_servers(), FOLDERS)
    ex.fail.add("server")
    service, _ = _service(execute=ex)
    out = await service.preview("adm")
    assert sorted(f["signal"] for f in out["attr_failures"]) == sorted(svc.SERVER_READ_SIGNALS)
    assert out["attr_reads"]["zones"]["pg_a"]["status"] == "failed"
    status = {s["signal"]: s for s in out["signals"]}
    assert status[m.SIG_IP_SUBNET]["reason"] != m.REASON_ATTR_FAILED  # S9는 새 조회 0
    assert out["total_changes"] > 0


async def test_itam_contract_on_reads_itam_without_pii() -> None:
    itam = [{"hostname": "cardweb01", "ip_text": "", "contract_no": "C-1",
             "contract_name": "구매", "maint_contract_no": "M-1", "owner_branch": "본부"},
            {"hostname": "ghost01", "contract_no": "C-9"}]
    ex = FakeExec(_attr_servers(), FOLDERS, itam)
    config = replace(NO_S6, aux=replace(NO_S6.aux, itam_contract=m.ItamContractConfig(True)))
    service, p = _service(execute=ex, config=config)
    out = await service.preview("adm")
    itam_sql = [sql for db, sql in ex.calls if db == ITAM_DB_ID]
    assert itam_sql and all(pii not in sql for sql in itam_sql for pii in ITAM_PII_COLUMNS)
    row = next(r for r in p["store"].attrs[out["run_id"]] if r["resource_id"] == "201")
    assert row["contract_no"] == "C-1" and row["owner_branch"] == "본부"
    assert out["attr_reads"]["itam"]["unmatched"] == 1


async def test_itam_failure_turns_off_s10_all_zones() -> None:
    ex = FakeExec(_attr_servers(), FOLDERS)
    ex.fail.add("itam")
    config = replace(NO_S6, aux=replace(NO_S6.aux, itam_contract=m.ItamContractConfig(True)))
    service, _ = _service(execute=ex, config=config)
    out = await service.preview("adm")
    assert out["attr_failures"] == [
        {"signal": m.SIG_ITAM_CONTRACT, "reason": "ITAM 계약 읽기 실패(RuntimeError)",
         "zone": None}]


# ── S11 ─────────────────────────────────────────────────────────────────
def test_alarm_minutes_one_server_match_only() -> None:
    servers = [m.SmsServer("pg_a", "1", "web01(카드)", "web01", zone="z1"),
               m.SmsServer("pg_a", "2", "web-02", "web02", zone="z1"),
               m.SmsServer("pg_b", "3", "dup", "dup", zone="z1"),
               m.SmsServer("pg_c", "4", "dup", "dup", zone="z1")]
    base = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    recs = [
        {"ts": base.isoformat(), "hostname": "WEB01"},
        {"ts": (base + timedelta(seconds=30)).isoformat(), "server_name": "web01(카드)"},
        {"ts": base.isoformat(), "resource_name": "web02"},
        {"ts": base.isoformat(), "hostname": "dup"},                   # 2대 — 고르지 않는다
        {"ts": base.isoformat(), "hostname": "dup", "db_id": "pg_b"},  # db로 좁혀 1대
        {"ts": (base - timedelta(days=30)).isoformat(), "hostname": "web01"},  # 창 밖
        {"ts": base.isoformat(), "hostname": "nobody"},
    ]
    minutes, notes = alarm_minutes_from(recs, servers, since_ts=(base - timedelta(days=14))
                                        .timestamp(), until_ts=base.timestamp() + 60)
    got = {(a.db_id, a.resource_id, a.minute) for a in minutes}
    minute = int(base.timestamp() // 60)
    assert got == {("pg_a", "1", minute), ("pg_a", "2", minute), ("pg_b", "3", minute)}
    assert notes["ambiguous"] == 1 and notes["unmatched"] == 1


async def test_s11_off_reads_no_decisions_and_on_reads_window() -> None:
    decisions = FakeDecisions([])
    service, _ = _service(decisions=decisions)
    await service.preview("adm")
    assert decisions.windows == []  # S11 off(기본)
    config = replace(NO_S6, aux=replace(
        NO_S6.aux, alarm_cooccurrence=m.AlarmCooccurrenceConfig(enabled=True, lookback_days=2)))
    service, _ = _service(decisions=decisions, config=config)
    preview = await service.preview("adm")
    assert decisions.windows and decisions.windows[0] >= 2 * 86400
    assert preview["attr_reads"]["alarm"]["status"] == "ok"
    service2, _ = _service(config=config)  # 결정 이력 미연결 → S11만 꺼짐 + 사유
    out = await service2.preview("adm")
    assert out["attr_failures"] == [{"signal": m.SIG_ALARM_COOCCURRENCE,
                                     "reason": "결정 이력 미연결", "zone": None}]


def test_lock_key_contract() -> None:
    assert svc.BIZSVC_LOCK_KEY == "bizsvc:lock" and svc.LOCK_TTL_SECONDS == 600
