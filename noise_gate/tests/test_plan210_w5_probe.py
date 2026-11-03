"""plans/210 W5 — W0 실측 스크립트(읽기 전용 · 건수·비율만 · 쓰기 0)."""

from __future__ import annotations

import inspect
import json

from noise_gate.scripts import probe_apm_host_map as probe
from noise_gate.tests.plan210_w5_fakes import FakeSqlExecutor
from noise_gate.tests.test_plan210_w5_scan import (
    APPS,
    CFG,
    ENGINES,
    LEDGER,
    SMS,
    ZONES,
    _gateway,
)

ROW_VALUES = ("ordweb01", "node01", "node02", "stlbat01", "10.1.0.1", "10.9.0.5", "결제정산",
              "settle-batch", "pay-api", "order", "s1")


async def _run() -> tuple[dict, FakeSqlExecutor, object]:
    gateway = _gateway()
    execute = FakeSqlExecutor(servers=SMS, apps=APPS, itam_servers=LEDGER)
    report = await probe.run_probe(gateway=gateway, execute=execute, zones=ZONES, itam_cfg=CFG,
                                   page_rows=100, engine_of=ENGINES)
    return report, execute, gateway


async def test_probe_metrics() -> None:
    report, _ex, _gw = await _run()
    node = report["node_address_match"]
    assert (node["distinct_node_ips"], node["exact_single"], node["none"]) == (2, 2, 0)
    assert node["exact_single_rate"] == 1.0
    assert node["on_nic_ip"] == 2 and node["single_by_zone"] == {"zone_a": 1, "zone_b": 1}
    vm = report["vm_exact_match"]
    assert (vm["with_ip_or_host"], vm["linked"], vm["none"]) == (3, 1, 2)
    chain = report["itam_chain"]
    assert chain["domains"] == 3
    assert chain["state"]["active"] >= 1 and "app_name" in chain["stage"]
    assert chain["servers_per_app"]["count"] == 1
    match = report["itam_polestar_match"]
    assert (match["servers"], match["either_match"], match["either_match_rate"]) == (1, 1, 1.0)
    scale = report["scale"]
    assert scale["per_source"] == {"source_1": {"domains": 3, "instances": 4}}
    assert scale["sms_zones"]["zone_a"]["servers"] == 2
    assert scale["itam"] == {"apps": 1, "servers": 1}
    assert set(scale["seconds"]) == {"apm_inventory", "node_address", "sms", "itam"}


async def test_probe_prints_no_row_values() -> None:
    report, _ex, _gw = await _run()
    text = json.dumps(report, ensure_ascii=False)
    for value in ROW_VALUES:
        assert value not in text, value


async def test_probe_performs_zero_writes() -> None:
    _report, execute, gateway = await _run()
    assert execute.sqls
    assert all(sql.lstrip().startswith("SELECT ") for _db, sql in execute.sqls)
    for word in ("INSERT", "UPDATE", "DELETE", "MERGE", "DROP", "ALTER", "CREATE"):
        assert all(word not in sql.upper() for _db, sql in execute.sqls)
    assert {name for name, _args in gateway.calls} <= set(probe.GATEWAY_TOOLS)
    signature = inspect.signature(probe.run_probe)
    assert not {"repo", "cache", "redis"} & set(signature.parameters)
    source = inspect.getsource(probe)
    for module in ("apm_host_map_repository", "apm_host_map_cache", "apm_host_map_scan_lock",
                   "redis", "asyncpg"):
        assert module not in source, module


def test_dry_run_lists_queries_only() -> None:
    plan = probe.dry_run_plan(zones=ZONES, itam_cfg=CFG, page_rows=50, engine_of=ENGINES)
    assert plan["gateway_tools"] == list(probe.GATEWAY_TOOLS)
    sqls = [s["sql"] for s in plan["sms_sql"]]
    assert len(sqls) == 4
    assert any("FETCH FIRST 50 ROWS ONLY" in s for s in sqls)
    assert any("LIMIT 50" in s for s in sqls)
    assert len(plan["itam_sql"]) == 2 and all("`" in s for s in plan["itam_sql"])


async def test_probe_without_itam_and_gateway() -> None:
    execute = FakeSqlExecutor(servers=SMS)
    report = await probe.run_probe(gateway=None, execute=execute, zones=ZONES, itam_cfg=None,
                                   engine_of=ENGINES)
    assert report["scanners"]["apm_inventory"]["error_kind"] == "gateway_not_configured"
    assert "itam_chain" not in report
    assert report["vm_exact_match"]["instances"] == 0
