"""plans/210 W5 — 사전 스캔 서비스(락·스캐너 격리·스냅샷 교체·일괄 생성·실행 기록) · ITAM 요청."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from tests.mocks.async_redis import FakeAsyncRedis

from noise_gate.application.apm_host_map_scan import (
    ApmHostMapScanService,
    ScanDisabledError,
    ScanRunningError,
    ScanScanners,
    handle_itam_request,
)
from noise_gate.domain.apm_host_map import (
    ORIGIN_MANUAL,
    REASON_NO_INVENTORY,
    itam_request_message,
    make_target,
)
from noise_gate.domain.apm_host_map_scan import ScannerResult
from noise_gate.infrastructure.apm_host_map_scan_lock import SCAN_LOCK_KEY, RedisScanLock
from noise_gate.infrastructure.apm_host_map_scanners import (
    scan_apm_inventory,
    scan_node_addresses,
)
from noise_gate.infrastructure.apm_host_map_sql_scanners import (
    ItamLookupConfig,
    scan_itam,
    scan_sms,
)
from noise_gate.tests.plan210_w5_fakes import (
    FakeCache,
    FakeGateway,
    FakeLock,
    FakeRepo,
    FakeSqlExecutor,
    apm_row,
    node_row,
    sms_server,
)

ZONES = ["zone_a", "zone_b"]
ENGINES = {"zone_a": "postgresql", "zone_b": "db2"}.__getitem__
POD1 = "pay-api-7d9f8c6b5-x2k4q"
POD2 = "pay-api-7d9f8c6b5-b7n9r"
CFG = ItamLookupConfig(
    app_table="apps", app_code_column="code", app_name_column="name", server_table="ledger",
    server_hostname_column="host", server_ip_column="ip", server_clue_columns=("clue",),
    server_active_column=None, server_active_values=(), generic_terms=("prod",), max_servers=50,
)

INVENTORY = [
    apm_row("s1", "10", "i1", "ordweb01", ip="10.1.0.1", domain_name="order"),
    apm_row("s1", "20", "p1", POD1, ip="172.16.0.1", domain_name="pay-k8s"),
    apm_row("s1", "20", "p2", POD2, ip="172.16.0.2", domain_name="pay-k8s"),
    apm_row("s1", "30", "b1", "settle-batch", domain_name="결제정산"),
]
NODES = [node_row("s1", "20", "p1", "10.9.0.5"), node_row("s1", "20", "p2", "10.9.0.6")]
SMS = [
    sms_server("zone_a", 1, "ordweb01", "10.1.0.1"),
    sms_server("zone_a", 2, "node01", "10.1.0.5", nic=["10.9.0.5"]),
    sms_server("zone_b", 3, "node02", "10.2.0.6", nic=["10.9.0.6"]),
    sms_server("zone_b", 4, "stlbat01", "10.2.0.9"),
]
APPS = [{"app_code": "STL", "app_name": "결제정산"}]
LEDGER = [{"hostname": "stlbat01", "ip_text": "10.2.0.9", "clue_1": "결제정산 배치"}]


def _gateway(*, inventory=INVENTORY, nodes: Any = NODES, sources_status: str = "ok"
             ) -> FakeGateway:
    tools: dict[str, Any] = {
        "apm_instance_map": {"rows": list(inventory),
                             "sources": [{"source_id": "s1", "status": sources_status}]},
    }
    if nodes is not None:
        tools["apm_node_addresses"] = {
            "rows": list(nodes),
            "node_address_sources": [{"source_id": "s1", "status": "ok", "reason": ""}],
        }
    return FakeGateway(tools)


def _service(repo: FakeRepo, cache: FakeCache, *, gateway: FakeGateway | None = None,
             execute: FakeSqlExecutor | None = None, cfg: ItamLookupConfig | None = CFG,
             lock: Any = None, auto_enabled: bool = True, **kw: Any) -> ApmHostMapScanService:
    gw = gateway or _gateway()
    ex = execute or FakeSqlExecutor(servers=SMS, apps=APPS, itam_servers=LEDGER)
    scanners = ScanScanners(
        apm_inventory=lambda: scan_apm_inventory(gw),
        node_address=lambda: scan_node_addresses(gw),
        sms=lambda: scan_sms(ex, ZONES, page_rows=100, engine_of=ENGINES),
        itam=lambda: scan_itam(ex, cfg, page_rows=100, config_error="config_invalid"),
    )
    return ApmHostMapScanService(
        repo=repo, cache=cache, lock=lock or FakeLock(), scanners=scanners,
        auto_enabled=auto_enabled, generic_terms=CFG.generic_terms, **kw,
    )


async def _scan(service: ApmHostMapScanService) -> str:
    run_id = await service.start_scan(requested_by="admin1")
    await service.wait_idle()
    return run_id


# ── 정상 ──────────────────────────────────────────────────────────────
async def test_scan_ok_generates_all_rules() -> None:
    repo, cache, lock = FakeRepo(), FakeCache(), FakeLock()
    run_id = await _scan(_service(repo, cache, lock=lock))
    run = repo.runs[run_id]
    assert run["status"] == "ok" and run["finished"] is True
    assert run["requested_by"] == "admin1"
    assert set(run["scanners"]) == {"apm_inventory", "node_address", "sms", "itam", "dpm"}
    assert run["scanners"]["dpm"]["status"] == "not_integrated"
    for rec in run["scanners"].values():
        assert set(rec) >= {"status", "rows", "seconds", "error_kind", "unchecked_zones"}
    summary = run["link_summary"]
    assert summary["new"] == 5 and summary["errors"] == 0 and summary["skipped"] == []

    vm = repo.row("s1", "10", "instance", "ordweb01")
    assert vm and (vm["basis"], vm["state"]) == ("vm_exact", "active")
    assert [t["server_name"] for t in vm["targets"]] == ["ordweb01"]

    pod = repo.row("s1", "20", "instance", POD1)
    assert pod and pod["basis"] == "node_address"
    assert [t["server_name"] for t in pod["targets"]] == ["node01"]
    workload = repo.row("s1", "20", "workload", "pay-api")
    assert workload and workload["basis"] == "node_address"
    assert {t["server_name"] for t in workload["targets"]} == {"node01", "node02"}

    domain = repo.row("s1", "30", "domain", "")
    assert domain and (domain["basis"], domain["state"]) == ("itam", "active")
    assert [t["server_name"] for t in domain["targets"]] == ["stlbat01"]
    assert repo.row("s1", "10", "domain", "") is None  # 이미 잇는 도메인은 itam 안 함

    for table in ("apm_inventory", "sms_inventory", "itam_snapshot_app", "itam_snapshot_server"):
        assert repo.current[table] == run_id
    inv = {r["instance_id"]: r for r in repo.snapshots["apm_inventory"][run_id]}
    assert (inv["p1"]["host_address"], inv["p1"]["host_address_status"]) == ("10.9.0.5", "ok")
    assert len(repo.snapshots["sms_inventory"][run_id]) == 4

    assert cache.rebuilt and len(cache.rebuilt[-1]) == 5
    assert cache.sms_index is not None and "10.9.0.5" in cache.sms_index[0]
    assert repo.cleanups == 1
    assert lock.holder_id is None and lock.released == [run_id]


async def test_second_start_while_running_raises() -> None:
    gate = asyncio.Event()

    async def slow() -> ScannerResult:
        await gate.wait()
        return ScannerResult("failed", error_kind="x")

    repo, lock = FakeRepo(), FakeLock()
    service = ApmHostMapScanService(
        repo=repo, cache=FakeCache(), lock=lock, auto_enabled=True, heartbeat_seconds=0.01,
        scanners=ScanScanners(apm_inventory=slow, node_address=slow, sms=slow, itam=slow),
    )
    first = await service.start_scan(requested_by="a")
    with pytest.raises(ScanRunningError) as e:
        await service.start_scan(requested_by="b")
    assert e.value.run_id == first
    await asyncio.sleep(0.05)
    gate.set()
    await service.wait_idle()
    assert lock.extended >= 1
    assert repo.runs[first]["status"] == "failed"
    assert len(repo.runs) == 1
    assert await service.start_scan(requested_by="c")  # 해제 뒤 다시 시작 가능
    await service.wait_idle()


async def test_disabled_flag_refuses() -> None:
    repo, lock = FakeRepo(), FakeLock()
    with pytest.raises(ScanDisabledError) as e:
        await _service(repo, FakeCache(), lock=lock, auto_enabled=False).start_scan(
            requested_by="a")
    assert e.value.reason == "apm_host_map_auto_disabled"
    assert repo.runs == {} and lock.holder_id is None


async def test_run_record_failure_releases_lock() -> None:
    repo, lock = FakeRepo(), FakeLock()

    async def boom(**_kw: Any) -> None:
        raise RuntimeError("pg down")

    repo.start_scan_run = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await _service(repo, FakeCache(), lock=lock).start_scan(requested_by="a")
    assert lock.holder_id is None


# ── 부분 실패 ─────────────────────────────────────────────────────────
async def test_partial_keeps_previous_snapshots_and_skips_rules() -> None:
    repo, cache = FakeRepo(), FakeCache()
    old_zone_b = {"db_id": "zone_b", "resource_id": "3", "server_name": "node02",
                  "hostname": "node02", "ip": "10.2.0.6", "nic_ips": ["10.9.0.6"]}
    repo.set_snapshot("sms_inventory", "old", [
        {"db_id": "zone_a", "resource_id": "9", "server_name": "stale", "ip": "10.1.0.99"},
        old_zone_b,
    ])
    repo.set_snapshot("itam_snapshot_app", "old", APPS)
    repo.set_snapshot("itam_snapshot_server", "old", [{"hostname": "stlbat01"}])
    kept = repo.seed("s1", "99", "instance", "x", targets=[
        make_target(db_id="zone_b", server_name="node02")])
    gone = repo.seed("s1", "98", "instance", "y", targets=[
        make_target(db_id="zone_a", server_name="removed01")])
    execute = FakeSqlExecutor(servers=SMS, fail_db=["zone_b"])
    run_id = await _scan(_service(repo, cache, execute=execute, cfg=None))
    run = repo.runs[run_id]
    assert run["status"] == "partial"
    assert run["scanners"]["sms"]["status"] == "partial"
    assert run["scanners"]["sms"]["unchecked_zones"] == ["zone_b"]
    assert run["scanners"]["itam"]["status"] == "failed"
    assert run["scanners"]["itam"]["error_kind"] == "config_invalid"

    # ITAM 실패 — 이전 스냅샷 포인터 유지 · itam 규칙만 건너뜀
    assert repo.current["itam_snapshot_app"] == "old"
    assert repo.current["itam_snapshot_server"] == "old"
    assert run["link_summary"]["skipped"] == ["itam"]
    assert repo.row("s1", "30", "domain", "") is None
    # SMS 부분 실패 — 실패 존의 이전 행을 이어 적재 · 끝까지 읽은 존의 옛 행은 버림
    sms_now = repo.snapshots["sms_inventory"][run_id]
    names = {(r["db_id"], r["server_name"]) for r in sms_now}
    assert ("zone_b", "node02") in names and ("zone_a", "stale") not in names
    # 실패 존 대상은 사라짐 판정 밖 · 읽은 존에서 없어진 대상은 target_gone
    assert repo.gone_calls[-1][1] == ["zone_a"]
    assert kept["targets"][0].get("target_gone") is not True
    assert gone["targets"][0]["target_gone"] is True
    assert run["link_summary"]["gone"] == 1
    # 이어 적재한 zone_b 노드로 node_address 규칙은 계속 성립
    pod2 = repo.row("s1", "20", "instance", POD2)
    assert pod2 and [t["server_name"] for t in pod2["targets"]] == ["node02"]


async def test_inventory_failure_skips_all_rules_and_keeps_pointer() -> None:
    repo = FakeRepo()
    repo.set_snapshot("apm_inventory", "old", [{"source_id": "s1", "domain_id": "10",
                                                "instance_id": "i1", "instance_name": "a"}])
    gateway = FakeGateway({"apm_instance_map": {"error": "rate_limited", "reason": "x"}})
    run_id = await _scan(_service(repo, FakeCache(), gateway=gateway))
    run = repo.runs[run_id]
    assert run["status"] == "partial"
    assert run["scanners"]["apm_inventory"]["error_kind"] == "rate_limited"
    assert run["scanners"]["node_address"]["status"] == "disabled"
    assert repo.current["apm_inventory"] == "old"
    assert run["link_summary"]["skipped"] == ["node_address", "vm_exact", "itam"]
    assert run["link_summary"]["new"] == 0


async def test_all_core_failed_is_failed() -> None:
    gateway = FakeGateway({})
    execute = FakeSqlExecutor(fail_db=ZONES)
    repo = FakeRepo()
    run_id = await _scan(_service(repo, FakeCache(), gateway=gateway, execute=execute, cfg=None))
    assert repo.runs[run_id]["status"] == "failed"


async def test_snapshot_store_failure_marks_scanner_failed() -> None:
    repo = FakeRepo()
    repo.fail_load.add("sms_inventory")
    run_id = await _scan(_service(repo, FakeCache()))
    run = repo.runs[run_id]
    assert run["scanners"]["sms"]["status"] == "failed"
    assert run["scanners"]["sms"]["error_kind"] == "snapshot_store"
    assert run["status"] == "partial"
    assert "sms_inventory" not in repo.current
    assert run["link_summary"]["new"] == 0


async def test_scanner_exception_isolated() -> None:
    async def explode() -> ScannerResult:
        raise ValueError("boom")

    repo = FakeRepo()
    gw = _gateway()
    ex = FakeSqlExecutor(servers=SMS, apps=APPS, itam_servers=LEDGER)
    service = ApmHostMapScanService(
        repo=repo, cache=FakeCache(), lock=FakeLock(), auto_enabled=True,
        scanners=ScanScanners(
            apm_inventory=lambda: scan_apm_inventory(gw), node_address=explode,
            sms=lambda: scan_sms(ex, ZONES, engine_of=ENGINES),
            itam=lambda: scan_itam(ex, CFG)),
    )
    run_id = await _scan(service)
    run = repo.runs[run_id]
    assert run["scanners"]["node_address"] == {**run["scanners"]["node_address"],
                                               "status": "failed", "error_kind": "ValueError"}
    assert "node_address" in run["link_summary"]["skipped"]
    # 노드 주소 없이도 vm_exact·itam은 돈다 — 파드 IP는 SMS에 없어 instance 행 없음
    assert repo.row("s1", "10", "instance", "ordweb01") is not None
    assert repo.row("s1", "20", "workload", "pay-api") is None


# ── 노드 주소 끔 → vm_exact ───────────────────────────────────────────
async def test_node_address_disabled_falls_back_to_vm_exact() -> None:
    inventory = [apm_row("s1", "20", "p1", POD1, ip="10.1.0.5", domain_name="pay")]
    repo = FakeRepo()
    run_id = await _scan(_service(repo, FakeCache(), gateway=_gateway(inventory=inventory,
                                                                      nodes=None)))
    run = repo.runs[run_id]
    assert run["status"] == "ok"
    assert run["scanners"]["node_address"]["status"] == "disabled"
    pod = repo.row("s1", "20", "instance", POD1)
    assert pod and (pod["basis"], pod["state"]) == ("vm_exact", "active")
    assert repo.row("s1", "20", "workload", "pay-api") is None


async def test_vm_exact_ambiguous_candidates() -> None:
    servers = [sms_server("zone_a", 1, "web01", "10.1.0.1"),
               sms_server("zone_b", 2, "web01-dr", "10.1.0.1")]
    inventory = [apm_row("s1", "10", "i1", "web01", ip="10.1.0.1", domain_name="order")]
    repo = FakeRepo()
    run_id = await _scan(_service(repo, FakeCache(), gateway=_gateway(inventory=inventory),
                                  execute=FakeSqlExecutor(servers=servers)))
    row = repo.row("s1", "10", "instance", "web01")
    assert row and row["state"] == "ambiguous" and len(row["targets"]) == 2
    assert repo.runs[run_id]["link_summary"]["ambiguous"] >= 1


# ── 관리자 행 불변 ────────────────────────────────────────────────────
async def test_manual_row_unchanged_with_scan_suggests() -> None:
    repo = FakeRepo()
    manual_targets = [make_target(db_id="zone_a", server_name="node01")]
    repo.seed("s1", "10", "instance", "ordweb01", origin=ORIGIN_MANUAL, basis="manual",
              targets=list(manual_targets))
    run_id = await _scan(_service(repo, FakeCache()))
    row = repo.row("s1", "10", "instance", "ordweb01")
    assert row is not None
    assert (row["origin"], row["basis"]) == (ORIGIN_MANUAL, "manual")
    assert [t["server_name"] for t in row["targets"]] == ["node01"]
    suggests = row["evidence"]["scan_suggests"]
    assert suggests["basis"] == "vm_exact"
    assert [t["server_name"] for t in suggests["targets"]] == ["ordweb01"]
    assert repo.runs[run_id]["link_summary"]["kept_manual"] == 1


async def test_verified_row_same_result_skipped() -> None:
    repo = FakeRepo()
    repo.seed("s1", "10", "instance", "ordweb01", verified=True, targets=[
        make_target(db_id="zone_a", server_name="ordweb01", ip="10.1.0.1", resource_id="1",
                    hostname="ordweb01")])
    run_id = await _scan(_service(repo, FakeCache()))
    row = repo.row("s1", "10", "instance", "ordweb01")
    assert row and "scan_suggests" not in row["evidence"]
    assert repo.runs[run_id]["link_summary"]["kept_manual"] == 1


# ── 락 ────────────────────────────────────────────────────────────────
class _EvalRedis(FakeAsyncRedis):
    """`EVAL` 대역 — 락 모듈의 비교 후 연장·삭제 스크립트 2종만 해석한다(루트 venv에 Lua 없음).

    스크립트 본문·키 수·인자를 기록해 원자 호출(EVAL 1회)로 갔는지 단언하게 한다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.evals: list[tuple[str, int, tuple[Any, ...]]] = []

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
        self.evals.append((script, numkeys, keys_and_args))
        assert numkeys == 1
        key, owner, *rest = keys_and_args
        assert "redis.call('get', KEYS[1]) == ARGV[1]" in script
        if await self.get(key) != owner:
            return 0
        if "'expire'" in script:
            return int(await self.expire(key, int(rest[0])))
        assert "'del'" in script and not rest
        return await self.delete(key)


async def test_redis_scan_lock_nx_extend_release() -> None:
    redis = _EvalRedis()
    lock = RedisScanLock(redis)
    assert await lock.acquire("r1", 3600) is True
    assert await lock.acquire("r2", 3600) is False
    assert await lock.holder() == "r1"
    assert await lock.extend("r1", 3600) is True
    assert await lock.extend("r2", 3600) is False
    await lock.release("r2")
    assert await redis.get(SCAN_LOCK_KEY) == "r1"
    await lock.release("r1")
    assert await lock.holder() is None
    assert ":" in SCAN_LOCK_KEY.removeprefix("apmhostmap:")  # 사본 재구성 정리 대상 밖
    # 연장·해제는 늘 EVAL 1회(비교와 쓰기를 원자적으로) — 키·자기 run_id·수명이 인자로 간다
    assert [(n, args) for _s, n, args in redis.evals] == [
        (1, (SCAN_LOCK_KEY, "r1", 3600)), (1, (SCAN_LOCK_KEY, "r2", 3600)),
        (1, (SCAN_LOCK_KEY, "r2")), (1, (SCAN_LOCK_KEY, "r1")),
    ]


async def test_redis_scan_lock_never_touches_key_outside_script() -> None:
    """연장·해제가 GET 뒤 EXPIRE/DEL을 따로 부르지 않는다(사이에 남의 락이 끼는 경쟁 차단)."""
    class _Strict(_EvalRedis):
        def __init__(self) -> None:
            super().__init__()
            self.in_eval = False
            self.outside: list[str] = []

        async def expire(self, name: str, time: int) -> bool:  # noqa: A002 - redis-py 인자명
            if not self.in_eval:
                self.outside.append("expire")
            return await super().expire(name, time)

        async def delete(self, *names: str) -> int:
            if not self.in_eval:
                self.outside.append("delete")
            return await super().delete(*names)

        async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> int:
            self.in_eval = True
            try:
                return await super().eval(script, numkeys, *keys_and_args)
            finally:
                self.in_eval = False

    redis = _Strict()
    lock = RedisScanLock(redis)
    assert await lock.acquire("r1", 60) is True
    await redis.set(SCAN_LOCK_KEY, "r2")  # 만료 뒤 다른 실행이 다시 잡은 상황
    assert await lock.extend("r1", 60) is False
    await lock.release("r1")
    assert await lock.holder() == "r2"
    assert await lock.extend("r2", 60) is True
    await lock.release("r2")
    assert await lock.holder() is None and redis.outside == []


# ── ITAM 요청 ─────────────────────────────────────────────────────────
def _itam_repo() -> FakeRepo:
    repo = FakeRepo(extra_columns={"itam_snapshot_server": ("clue_text",)})
    repo.set_snapshot("itam_snapshot_app", "r0", APPS)
    repo.set_snapshot("itam_snapshot_server", "r0", [
        {"hostname": "stlbat01", "ips": ["10.2.0.9"], "active": None,
         "clue_text": "결제정산 배치"}])
    repo.set_snapshot("sms_inventory", "r0", [
        {"db_id": "zone_b", "resource_id": "4", "server_name": "stlbat01",
         "hostname": "stlbat01", "ip": "10.2.0.9", "nic_ips": []}])
    return repo


async def test_itam_request_upserts_domain_row() -> None:
    repo, cache = _itam_repo(), FakeCache()
    gateway = FakeGateway({"apm_instance_map": lambda args: {"rows": [
        apm_row("s1", "30", "b1", "settle-batch", domain_name="결제정산"),
        apm_row("s2", "30", "z", "other", domain_name="다른"),
    ]}})
    msg = itam_request_message(source_id="s1", domain_id="30", domain_name="결제정산")
    out = await handle_itam_request(msg, repo=repo, cache=cache, gateway_client=gateway)
    assert out["status"] == "ok" and out["state"] == "active"
    name, args = gateway.calls[0]
    assert name == "apm_instance_map"
    assert (args["domain_id"], args["source_ids"]) == (30, ["s1"])
    row = repo.row("s1", "30", "domain", "")
    assert row and row["basis"] == "itam"
    assert [t["server_name"] for t in row["targets"]] == ["stlbat01"]
    assert row["evidence"]["trigger"] == "alarm_miss"
    assert cache.put_rows and cache.put_rows[0]["id"] == out["map_id"]


async def test_itam_request_without_snapshot_writes_nothing(caplog: pytest.LogCaptureFixture
                                                            ) -> None:
    # plans/211 W6 — 스냅샷이 없어도 노드 주소(N1)·VM 일치는 시도하고 ITAM(I) 단계만 건너뛴다
    repo, cache = FakeRepo(), FakeCache()
    gateway = FakeGateway({"apm_instance_map": lambda args: {"rows": [
        apm_row("s1", "30", "b1", "settle-batch", domain_name="결제정산")]}})
    msg = itam_request_message(source_id="s1", domain_id="30")
    with caplog.at_level("INFO"):
        out = await handle_itam_request(msg, repo=repo, cache=cache, gateway_client=gateway)
    assert out["status"] == "no_inventory" and out["reason"] == REASON_NO_INVENTORY
    assert out["tried"]["itam"] == "skipped:no_inventory"
    assert REASON_NO_INVENTORY in caplog.text
    assert repo.rows == {} and cache.put_rows == []
    assert [name for name, _ in gateway.calls] == ["apm_instance_map", "apm_node_addresses"]


@pytest.mark.parametrize("msg", [
    {"type": "itam_request", "source_id": "s1", "domain_id": "x1"},
    {"type": "other", "source_id": "s1", "domain_id": "1"},
    {"type": "itam_request", "source_id": "", "domain_id": "1"},
])
async def test_itam_request_invalid(msg: dict[str, Any]) -> None:
    out = await handle_itam_request(msg, repo=_itam_repo(), cache=FakeCache(),
                                    gateway_client=FakeGateway({}))
    assert out["status"] == "invalid"


async def test_itam_request_domain_gone_and_gateway_error() -> None:
    msg = itam_request_message(source_id="s1", domain_id="30")
    empty = FakeGateway({"apm_instance_map": {"rows": []}})
    assert (await handle_itam_request(msg, repo=_itam_repo(), cache=FakeCache(),
                                      gateway_client=empty))["status"] == "no_domain"
    broken = FakeGateway({"apm_instance_map": {"error": "source_unavailable"}})
    out = await handle_itam_request(msg, repo=_itam_repo(), cache=FakeCache(),
                                    gateway_client=broken)
    assert out == {"status": "error", "error_kind": "source_unavailable"}
    none = await handle_itam_request(msg, repo=_itam_repo(), cache=FakeCache(),
                                     gateway_client=None)
    assert none["status"] == "error"
