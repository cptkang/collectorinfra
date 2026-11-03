"""plans/210 W5 테스트 공용 가짜 — 저장소(메모리) · 사본 · 락 · 게이트웨이 · SQL 실행기.

저장소 가짜는 쓰기 규칙을 실 저장소와 같은 순수 함수(`plan_auto_write`·`mark_targets_gone`)로
판정하고, 스냅샷 적재는 실 저장소의 칼럼 화이트리스트(`SNAPSHOT_COLUMNS`)를 그대로 따른다.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from noise_gate.domain.apm_host_map import (
    ORIGIN_AUTO,
    WRITE_INSERT,
    AutoProposal,
    mark_targets_gone,
    plan_auto_write,
)
from noise_gate.infrastructure.apm_gateway_client import ApmGatewayClientError
from noise_gate.infrastructure.apm_host_map_repository import SNAPSHOT_COLUMNS

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


class FakeRepo:
    """`ApmHostMapRepository` 메모리 가짜(스캔·ITAM 요청이 쓰는 메서드만)."""

    def __init__(self, *, extra_columns: Mapping[str, Sequence[str]] | None = None) -> None:
        self.rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.current: dict[str, str] = {}
        self.runs: dict[str, dict[str, Any]] = {}
        self.run_updates: list[dict[str, Any]] = []
        self.fail_load: set[str] = set()
        self.cleanups = 0
        self.gone_calls: list[tuple[set[tuple[str, str]], list[str]]] = []
        self._extra = {k: tuple(v) for k, v in (extra_columns or {}).items()}
        self._next_id = 1

    # 매핑 행
    def seed(self, source_id: str, domain_id: str, scope: str, match_key: str,
             **fields: Any) -> dict[str, Any]:
        row = {"id": self._next_id, "source_id": source_id, "domain_id": domain_id,
               "scope": scope, "match_key": match_key, "origin": ORIGIN_AUTO, "verified": False,
               "state": "active", "basis": "vm_exact", "targets": [], "evidence": {},
               "domain_name": "", "instance_example": "", **fields}
        self._next_id += 1
        self.rows[(source_id, domain_id, scope, match_key)] = row
        return row

    def row(self, source_id: str, domain_id: str, scope: str, match_key: str
            ) -> dict[str, Any] | None:
        return self.rows.get((source_id, domain_id, scope, match_key))

    async def get_by_key(self, source_id: str, domain_id: str, scope: str, match_key: str
                         ) -> dict[str, Any] | None:
        row = self.rows.get((source_id, domain_id, scope, match_key))
        return dict(row) if row else None

    async def apply_auto(self, source_id: str, proposal: AutoProposal, *,
                         now: datetime | None = None, retry_hours: int = 24
                         ) -> tuple[str, dict[str, Any] | None]:
        key = (source_id, proposal.domain_id, proposal.scope, proposal.match_key)
        existing = self.rows.get(key)
        write = plan_auto_write(existing, proposal, now_iso=(now or NOW).isoformat())
        if existing is None:
            row = self.seed(*key, origin=ORIGIN_AUTO, basis=write.basis, state=write.state,
                            targets=[dict(t) for t in write.targets],
                            evidence=dict(write.evidence), domain_name=proposal.domain_name,
                            instance_example=proposal.instance_example)
            return WRITE_INSERT, row
        existing.update(basis=write.basis, state=write.state,
                        targets=[dict(t) for t in write.targets], evidence=dict(write.evidence))
        return write.action, existing

    async def apply_auto_many(self, source_id: str, proposals: Sequence[AutoProposal], *,
                              now: datetime | None = None, retry_hours: int = 24
                              ) -> list[tuple[str, dict[str, Any] | None]]:
        out: list[tuple[str, dict[str, Any] | None]] = []
        for p in proposals:
            try:
                out.append(await self.apply_auto(source_id, p, now=now, retry_hours=retry_hours))
            except Exception:  # noqa: BLE001
                out.append(("error", None))
        return out

    async def set_targets_gone(self, present: Any, scanned_db_ids: Any) -> int:
        self.gone_calls.append((set(present), list(scanned_db_ids)))
        changed = 0
        for row in self.rows.values():
            new = mark_targets_gone(row["targets"], present, scanned_db_ids)
            if new is not None:
                row["targets"] = new
                changed += 1
        return changed

    async def cleanup(self, *, now: datetime | None = None) -> dict[str, int]:
        self.cleanups += 1
        return {}

    async def all_rows(self) -> list[dict[str, Any]]:
        return list(self.rows.values())

    # 실행 기록
    async def start_scan_run(self, *, requested_by: str, run_id: str | None = None
                             ) -> dict[str, Any]:
        assert run_id is not None
        self.runs[run_id] = {"run_id": run_id, "status": "running", "requested_by": requested_by,
                             "scanners": {}, "link_summary": {}, "finished": False}
        return dict(self.runs[run_id])

    async def update_scan_run(self, run_id: str, *, status: str | None = None,
                              scanners: Mapping[str, Any] | None = None,
                              link_summary: Mapping[str, Any] | None = None,
                              finished: bool = False) -> None:
        run = self.runs[run_id]
        self.run_updates.append({"status": status, "finished": finished})
        if status is not None:
            run["status"] = status
        if scanners is not None:
            run["scanners"] = dict(scanners)
        if link_summary is not None:
            run["link_summary"] = dict(link_summary)
        run["finished"] = run["finished"] or finished

    # 스냅샷
    def _columns(self, table: str) -> tuple[str, ...]:
        return (*SNAPSHOT_COLUMNS[table], *self._extra.get(table, ()))

    async def load_snapshot(self, table: str, run_id: str, rows: Sequence[Mapping[str, Any]]
                            ) -> int:
        if table in self.fail_load:
            raise RuntimeError(f"적재 실패 {table}")
        cols = self._columns(table)
        self.snapshots.setdefault(table, {})[run_id] = [{c: r.get(c) for c in cols} for r in rows]
        return len(rows)

    async def swap_snapshot(self, table: str, run_id: str) -> None:
        self.current[table] = run_id
        runs = self.snapshots.setdefault(table, {})
        for other in [r for r in runs if r != run_id]:
            del runs[other]

    async def current_snapshot_run(self, table: str) -> str | None:
        return self.current.get(table)

    async def snapshot_rows(self, table: str, *, query: str | None = None,
                            limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        run_id = self.current.get(table)
        if run_id is None:
            return []
        rows = self.snapshots.get(table, {}).get(run_id, [])
        return [{"id": i + 1, "run_id": run_id, **r} for i, r in enumerate(rows)]

    def set_snapshot(self, table: str, run_id: str, rows: Sequence[Mapping[str, Any]]) -> None:
        """이전 스캔 스냅샷을 직접 심는다."""
        cols = self._columns(table)
        self.snapshots.setdefault(table, {})[run_id] = [{c: r.get(c) for c in cols} for r in rows]
        self.current[table] = run_id


class FakeCache:
    """사본 가짜 — 재구성·put 호출만 기록."""

    def __init__(self) -> None:
        self.rebuilt: list[list[dict[str, Any]]] = []
        self.sms_index: tuple[dict[str, Any], dict[str, Any]] | None = None
        self.put_rows: list[dict[str, Any]] = []

    async def rebuild(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.rebuilt.append([dict(r) for r in rows])

    async def rebuild_sms_index(self, by_ip: Any, by_host: Any) -> None:
        self.sms_index = (dict(by_ip), dict(by_host))

    async def put(self, row: Mapping[str, Any]) -> None:
        self.put_rows.append(dict(row))


class FakeLock:
    """단독 실행 락 가짜."""

    def __init__(self) -> None:
        self.holder_id: str | None = None
        self.extended = 0
        self.released: list[str] = []

    async def acquire(self, run_id: str, ttl_seconds: int = 3600) -> bool:
        if self.holder_id is not None:
            return False
        self.holder_id = run_id
        return True

    async def holder(self) -> str | None:
        return self.holder_id

    async def extend(self, run_id: str, ttl_seconds: int = 3600) -> bool:
        self.extended += 1
        return self.holder_id == run_id

    async def release(self, run_id: str) -> None:
        self.released.append(run_id)
        if self.holder_id == run_id:
            self.holder_id = None


Handler = Callable[[dict[str, Any]], dict[str, Any]]


class FakeGateway:
    """게이트웨이 호출기 가짜 — `tools[도구] = 봉투 | (인자 → 봉투)` · 없는 도구는 Unknown tool."""

    def __init__(self, tools: Mapping[str, dict[str, Any] | Handler]) -> None:
        self.tools = dict(tools)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: Mapping[str, Any], *,
                        timeout: float | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(arguments)))
        spec = self.tools.get(tool_name)
        if spec is None:
            raise ApmGatewayClientError(
                f"게이트웨이 도구 오류({tool_name}): Unknown tool: {tool_name}")
        if isinstance(spec, Exception):
            raise spec
        return spec(dict(arguments)) if callable(spec) else dict(spec)


def apm_row(source: str, domain: str, instance_id: str, name: str, *, ip: str = "",
            host: str = "", domain_name: str = "", description: str = "") -> dict[str, Any]:
    """`apm_instance_map` 행."""
    return {"source_id": source, "domain_id": int(domain), "domain_name": domain_name,
            "domain_description": description, "instance_id": instance_id,
            "instance_name": name, "ip_address": ip, "host_name": host}


def node_row(source: str, domain: str, instance_id: str, addr: str, status: str = "ok"
             ) -> dict[str, Any]:
    """`apm_node_addresses` 행."""
    return {"source_id": source, "domain_id": int(domain), "instance_id": instance_id,
            "host_address": addr, "status": status}


def sms_server(db_id: str, rid: int, name: str, ip: str, *, nic: Sequence[str] = ()
               ) -> dict[str, Any]:
    """SMS 가짜 DB의 서버 1대(`nic`는 NIC EAV IP 값)."""
    return {"db_id": db_id, "resource_id": rid, "server_name": name, "hostname": name,
            "ip": ip, "nic": list(nic)}


_AFTER_ID = re.compile(r"r\.id > (\d+)")
_LIMIT = re.compile(r"(?:LIMIT|FETCH FIRST) (\d+)")
_IN = re.compile(r"IN \(([\d, ]+)\)")
_KEY = re.compile(r"(>=|>) '([^']*)'")


class FakeSqlExecutor:
    """SMS·ITAM 가짜 실행기 — 고정 SELECT 모양만 해석한다(실행 SQL은 `sqls`에 남는다)."""

    def __init__(self, *, servers: Sequence[Mapping[str, Any]] = (),
                 apps: Sequence[Mapping[str, Any]] = (),
                 itam_servers: Sequence[Mapping[str, Any]] = (),
                 fail_db: Sequence[str] = ()) -> None:
        self.servers = list(servers)
        self.apps = list(apps)
        self.itam_servers = list(itam_servers)
        self.fail_db = set(fail_db)
        self.sqls: list[tuple[str, str]] = []

    async def __call__(self, db_id: str, sql: str) -> list[dict[str, Any]]:
        self.sqls.append((db_id, sql))
        if db_id in self.fail_db:
            raise ConnectionError(f"{db_id} 연결 실패")
        if db_id == "itam":
            return self._itam(sql)
        mine = [s for s in self.servers if s["db_id"] == db_id]
        ids = _IN.search(sql)
        if "NetworkInterface" in sql and ids:
            wanted = {int(x) for x in ids.group(1).split(",")}
            return [{"RESOURCE_ID": s["resource_id"], "IP": ip}
                    for s in mine if s["resource_id"] in wanted for ip in s["nic"]]
        after = int(_AFTER_ID.search(sql).group(1))  # type: ignore[union-attr]
        limit = int(_LIMIT.search(sql).group(1))  # type: ignore[union-attr]
        page = sorted((s for s in mine if s["resource_id"] > after),
                      key=lambda s: s["resource_id"])[:limit]
        return [{"resource_id": s["resource_id"], "server_name": s["server_name"],
                 "hostname": s["hostname"], "ip": s["ip"]} for s in page]

    def _itam(self, sql: str) -> list[dict[str, Any]]:
        limit = int(_LIMIT.search(sql).group(1))  # type: ignore[union-attr]
        key = _KEY.search(sql)
        if "AS app_code" in sql:
            rows = sorted(self.apps, key=lambda r: r["app_code"])
            field = "app_code"
        else:
            rows = sorted(self.itam_servers, key=lambda r: r["hostname"])
            field = "hostname"
        if key:
            op, value = key.groups()
            rows = [r for r in rows if (r[field] >= value if op == ">=" else r[field] > value)]
        return [dict(r) for r in rows[:limit]]
