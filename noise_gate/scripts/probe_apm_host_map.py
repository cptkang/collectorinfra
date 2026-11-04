"""APM·SMS 연계 W0 폐쇄망 실측 스크립트 (plans/210 §7 2·3·4·5·8 · D-362).

사전 스캔과 **같은 스캐너**(`apm_host_map_scanners`·`apm_host_map_sql_scanners`)로
게이트웨이·폴스타·ITAM을 한 번 읽고, 매핑 저장소(PG)·Redis에는 **아무것도 쓰지 않은 채**
메모리에서 대조한다.

출력은 건수·일치율·소요(초)뿐이다 — 행 값(서버명·IP·도메인명·업무명)과 PII는 찍지 않는다.

| §7 | 출력 키 | 내용 |
|---|---|---|
| 2 | `node_address_match` | 노드 주소(ok) IP ↔ SMS 서버·NIC IP 1대·여러 대·0대 · 존별 |
| 3 | `vm_exact_match` | VM 인스턴스 `ip`·`host_name` ↔ SMS 서버 linked·ambiguous·none |
| 4 | `itam_chain` | 설명 보유 수 · 토큰 수 분포 · 사슬 단계·상태 분포 · 업무별 서버 수 |
| 5 | `itam_polestar_match` | ITAM 서버 호스트명·IP ↔ SMS 일치율 |
| 8 | `scale` | 소스별 도메인·인스턴스 수 · 존별 서버·NIC 행 수 · ITAM 행 수 · 스캐너별 소요 |

사용:
    python -m noise_gate.scripts.probe_apm_host_map --dry-run        # 실행할 SQL·도구 목록만
    python -m noise_gate.scripts.probe_apm_host_map                  # 실측(JSON 출력)
    python -m noise_gate.scripts.probe_apm_host_map --skip-itam      # ITAM 미등록 환경

읽기 전용(D-003) — SQL은 고정 SELECT(실행 전 `SQLGuard`) · 게이트웨이는 데이터 도구·작업 조회만.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from noise_gate.domain.apm_host_map import build_sms_index, vm_exact_match
from noise_gate.domain.apm_host_map_itam import (
    apps_from_rows,
    domain_tokens,
    resolve_itam_chain,
    servers_for_app,
    servers_from_rows,
)
from noise_gate.domain.apm_host_map_scan import SCANNER_USABLE, ScannerResult
from noise_gate.infrastructure.apm_host_map_scanners import (
    INSTANCE_MAP_TOOL,
    JOB_READ_TOOL,
    JOB_STATUS_TOOL,
    NODE_ADDRESS_TOOL,
    GatewayCaller,
    scan_apm_inventory,
    scan_node_addresses,
)
from noise_gate.infrastructure.apm_host_map_sql_scanners import (
    ItamLookupConfig,
    SqlExecutor,
    db_engine_of,
    itam_sql_preview,
    scan_itam,
    scan_sms,
    sms_sql_preview,
)
from src.domain.host_names import normalize_hostname

#: 이 스크립트가 부르는 게이트웨이 도구(전부 읽기).
GATEWAY_TOOLS = (INSTANCE_MAP_TOOL, NODE_ADDRESS_TOOL, JOB_STATUS_TOOL, JOB_READ_TOOL)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _distribution(values: Sequence[int]) -> dict[str, Any]:
    """정수 분포 요약(값 자체가 아니라 건수·최소·중앙·최대·구간)."""
    if not values:
        return {"count": 0}
    buckets = Counter(
        "0" if v == 0 else "1" if v == 1 else "2-10" if v <= 10 else "11-50" if v <= 50 else ">50"
        for v in values
    )
    return {"count": len(values), "min": min(values), "median": statistics.median(values),
            "max": max(values), "buckets": dict(sorted(buckets.items()))}


def _scanner(result: ScannerResult) -> dict[str, Any]:
    """스캐너 기록에서 건수·상태·소요만(소스별 사유 문구는 뺀다)."""
    record = result.record()
    sources = record.get("sources")
    if isinstance(sources, list):
        record["sources"] = dict(Counter(str(s.get("status")) for s in sources))
    return record


def _node_address_match(
    inventory: Sequence[Mapping[str, Any]],
    found: Mapping[tuple[str, str, str], tuple[str, str]],
    sms_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """§7-2 — 노드 주소(상태 ok)가 SMS 서버 IP·NIC IP에 몇 대로 걸리는가."""
    server_ips = {str(r.get("ip") or "").strip() for r in sms_rows} - {""}
    nic_ips = {ip for r in sms_rows for ip in (r.get("nic_ips") or [])}
    by_ip, _ = build_sms_index(sms_rows)
    statuses = Counter(status or "unknown" for _addr, status in found.values())
    known = sorted({addr for addr, status in found.values() if status == "ok" and addr})
    single = multi = none = on_server = on_nic = 0
    zones: Counter[str] = Counter()
    for addr in known:
        servers = by_ip.get(addr) or []
        if len(servers) == 1:
            single += 1
            zones[str(servers[0].get("db_id") or "")] += 1
        elif servers:
            multi += 1
        else:
            none += 1
        on_server += addr in server_ips
        on_nic += addr in nic_ips
    return {
        "instances": len(inventory), "address_rows": len(found),
        "address_status": dict(sorted(statuses.items())),
        "distinct_node_ips": len(known), "exact_single": single, "multiple": multi, "none": none,
        "exact_single_rate": _rate(single, len(known)),
        "on_server_ip": on_server, "on_nic_ip": on_nic,
        "single_by_zone": dict(sorted(zones.items())),
    }


def _vm_exact_match(
    inventory: Sequence[Mapping[str, Any]], sms_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """§7-3 — 인스턴스 `ip`·`host_name` ↔ SMS 서버(vm_exact 판정 분포)."""
    by_ip, by_host = build_sms_index(sms_rows)
    counts: Counter[str] = Counter()
    with_key = 0
    for row in inventory:
        ip = str(row.get("ip") or "").strip()
        host = normalize_hostname(row.get("host_name"))
        if not ip and not host:
            continue
        with_key += 1
        match = vm_exact_match(by_ip.get(ip) if ip else None, by_host.get(host) if host else None)
        counts[match.status] += 1
    return {"instances": len(inventory), "with_ip_or_host": with_key,
            "linked": counts["linked"], "ambiguous": counts["ambiguous"], "none": counts["none"],
            "linked_rate": _rate(counts["linked"], with_key)}


def _itam_chain(
    inventory: Sequence[Mapping[str, Any]],
    itam_data: Mapping[str, Any],
    sms_rows: Sequence[Mapping[str, Any]],
    cfg: ItamLookupConfig,
    max_targets: int,
) -> dict[str, Any]:
    """§7-4 — 도메인 형식(건수만) · ITAM 사슬 단계·상태 분포 · 업무별 서버 수 분포."""
    apps = apps_from_rows(itam_data.get("apps") or [])
    servers = servers_from_rows(itam_data.get("servers") or [])
    by_ip, by_host = build_sms_index(sms_rows)
    domains: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in inventory:
        domains.setdefault((str(row.get("source_id")), str(row.get("domain_id"))), row)
    stages: Counter[str] = Counter()
    states: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    token_counts: list[int] = []
    described = 0
    for row in domains.values():
        name = str(row.get("domain_name") or "")
        description = str(row.get("domain_description") or "")
        described += bool(description.strip())
        token_counts.append(len(domain_tokens([name, description], cfg.generic_terms)))
        result = resolve_itam_chain(
            domain_name=name, domain_description=description, apps=apps, servers=servers,
            by_ip=by_ip, by_host=by_host, generic_terms=cfg.generic_terms,
            max_servers=cfg.max_servers, max_targets=max_targets,
        )
        stages[str(result.evidence.get("stage") or "")] += 1
        states[result.state] += 1
        reasons[str(result.evidence.get("reason") or "")] += 1
    per_app = [len(servers_for_app(app, servers)) for app in apps]
    return {
        "domains": len(domains), "with_description": described,
        "tokens_per_domain": _distribution(token_counts),
        "apps": len(apps), "servers": len(servers),
        "servers_per_app": _distribution(per_app),
        "stage": dict(sorted(stages.items())), "state": dict(sorted(states.items())),
        "reason": dict(sorted(reasons.items())),
    }


def _itam_polestar_match(
    itam_data: Mapping[str, Any], sms_rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """§7-5 — ITAM 서버 호스트명·IP ↔ SMS 정합(G-6)."""
    servers = servers_from_rows(itam_data.get("servers") or [])
    by_ip, by_host = build_sms_index(sms_rows)
    host_hit = ip_hit = either = with_ip = 0
    for server in servers:
        h = bool(by_host.get(normalize_hostname(server.hostname)))
        i = any(by_ip.get(ip) for ip in server.ips)
        with_ip += bool(server.ips)
        host_hit += h
        ip_hit += i
        either += h or i
    total = len(servers)
    return {"servers": total, "with_ip": with_ip, "hostname_match": host_hit,
            "ip_match": ip_hit, "either_match": either,
            "hostname_match_rate": _rate(host_hit, total), "ip_match_rate": _rate(ip_hit, with_ip),
            "either_match_rate": _rate(either, total)}


def _scale(
    inventory: Sequence[Mapping[str, Any]], results: Mapping[str, ScannerResult]
) -> dict[str, Any]:
    """§7-8 — 스캔 규모·소요."""
    per_source: dict[str, dict[str, int]] = {}
    for row in inventory:
        slot = per_source.setdefault(str(row.get("source_id")), {"domains": 0, "instances": 0})
        slot["instances"] += 1
    for source, slot in per_source.items():
        slot["domains"] = len({str(r.get("domain_id")) for r in inventory
                               if str(r.get("source_id")) == source})
    labels = {s: f"source_{i}" for i, s in enumerate(sorted(per_source), start=1)}
    return {
        "per_source": {labels[s]: v for s, v in sorted(per_source.items())},
        "sms_zones": results["sms"].extra.get("zones", {}) if "sms" in results else {},
        "itam": ({k: results["itam"].extra.get(k) for k in ("apps", "servers")}
                 if "itam" in results else {}),
        "seconds": {name: round(r.seconds, 3) for name, r in results.items()},
    }


async def run_probe(
    *,
    gateway: GatewayCaller | None,
    execute: SqlExecutor,
    zones: Sequence[str],
    itam_cfg: ItamLookupConfig | None,
    page_rows: int = 1000,
    max_targets: int = 10,
    engine_of: Callable[[str], str] = db_engine_of,
) -> dict[str, Any]:
    """스캐너 4종을 한 번 돌려 §7 2·3·4·5·8 지표를 만든다(PG·Redis 미사용 — 메모리 대조만)."""
    started = time.monotonic()
    results: dict[str, ScannerResult] = {
        "apm_inventory": await scan_apm_inventory(gateway),
        "node_address": await scan_node_addresses(gateway),
        "sms": await scan_sms(execute, zones, page_rows=page_rows, engine_of=engine_of),
    }
    if itam_cfg is not None:
        results["itam"] = await scan_itam(execute, itam_cfg, page_rows=page_rows)
    inventory = list(results["apm_inventory"].data or [])
    found = (results["node_address"].data or {}) if results["node_address"].status in \
        SCANNER_USABLE else {}
    sms_rows = list((results["sms"].data or {}).get("rows") or [])
    report: dict[str, Any] = {
        "scanners": {name: _scanner(r) for name, r in results.items()},
        "node_address_match": _node_address_match(inventory, found, sms_rows),
        "vm_exact_match": _vm_exact_match(inventory, sms_rows),
        "scale": _scale(inventory, results),
    }
    itam = results.get("itam")
    if itam_cfg is not None and itam is not None and itam.status in SCANNER_USABLE:
        report["itam_chain"] = _itam_chain(inventory, itam.data or {}, sms_rows, itam_cfg,
                                           max_targets)
        report["itam_polestar_match"] = _itam_polestar_match(itam.data or {}, sms_rows)
    report["total_seconds"] = round(time.monotonic() - started, 3)
    return report


def dry_run_plan(
    *,
    zones: Sequence[str],
    itam_cfg: ItamLookupConfig | None,
    page_rows: int = 1000,
    engine_of: Callable[[str], str] = db_engine_of,
) -> dict[str, Any]:
    """`--dry-run` — 실행할 첫 페이지 SQL과 게이트웨이 도구 목록(접속 없음)."""
    return {
        "gateway_tools": list(GATEWAY_TOOLS),
        "sms_sql": [{"db_id": db, "sql": sql}
                    for db, sql in sms_sql_preview(zones, page_rows, engine_of)],
        "itam_sql": itam_sql_preview(itam_cfg, page_rows) if itam_cfg is not None else [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """CLI — 결과 JSON을 표준 출력에 쓴다."""
    parser = argparse.ArgumentParser(description="APM·SMS 연계 W0 실측(읽기 전용 · 건수·비율만)")
    parser.add_argument("--dry-run", action="store_true", help="SQL·도구 목록만 출력(접속 없음)")
    parser.add_argument("--skip-itam", action="store_true", help="ITAM 스캐너를 건너뛴다")
    parser.add_argument("--page-rows", type=int, default=None, help="페이지 행 수(기본 설정값)")
    args = parser.parse_args(argv)

    from noise_gate.application.host_candidates import node_lookup_db_ids
    from noise_gate.infrastructure.apm_gateway_client import build_apm_gateway_client
    from noise_gate.infrastructure.apm_host_map_scanners import GatewayToolCaller
    from noise_gate.infrastructure.apm_host_map_sql_scanners import (
        load_itam_lookup,
        registry_executor,
    )
    from src.config import AppConfig

    cfg = AppConfig()
    ng = cfg.noise_gate
    page_rows = int(args.page_rows or ng.apm_host_map_scan_page_rows)
    zones = node_lookup_db_ids(cfg)
    itam_cfg = None if args.skip_itam else load_itam_lookup()
    if args.dry_run:
        out = dry_run_plan(zones=zones, itam_cfg=itam_cfg, page_rows=page_rows)
    else:
        client = build_apm_gateway_client(cfg)
        out = asyncio.run(run_probe(
            gateway=GatewayToolCaller(client) if client is not None else None,
            execute=registry_executor(cfg), zones=zones, itam_cfg=itam_cfg,
            page_rows=page_rows, max_targets=int(ng.apm_host_map_max_targets),
        ))
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
