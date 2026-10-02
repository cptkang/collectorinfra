"""호스트 일괄 PromQL ↔ OpenMetrics 교차 검증 CLI (plans/92 §4.8.5 · O2b · D-210).

docs/27 §6.1 「라벨 규약 실측 5단계」(curl 5개)를 한 번에 대신한다. 호스트마다
``metric_source.run_cross_check``를 돌려 6판정(``label_mismatch``·``not_scraped``·
``stale``·``scrape_down``·``value_drift``·``consistent``)을 출력한다. 판정은 전부 결정적
코드다(LLM 없음).

설정은 서버와 같은 ``config.toml`` + ``.env``에서 읽는다
(``PROMETHEUS_URL`` · ``[[openmetrics.targets]]``).
읽기 전용이다 — Prometheus 읽기 API와 exporter GET만 쓴다.

사용:
    cd mcp_server && ../.venv/bin/python scripts/prom_om_cross_check.py --metric node_load1
    ../.venv/bin/python scripts/prom_om_cross_check.py --metric node_load1 \
        --hosts web-01,web-02 --json

종료 코드: 0 = 전 호스트 ``consistent`` · 1 = 그 밖의 판정·판정 보류·오류가 하나라도 있음 ·
2 = 인자·설정 오류(PROMETHEUS_URL 미설정 · 대상 호스트 0건).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# `python scripts/prom_om_cross_check.py`로 실행해도 패키지(`mcp_server`)가 잡히게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.config import AppServerConfig, load_config  # noqa: E402
from mcp_server.metric_source import VERDICT_CONSISTENT, run_cross_check  # noqa: E402


async def cross_check_hosts(
    config: AppServerConfig, hosts: list[str], metric: str
) -> list[dict[str, Any]]:
    """호스트마다 교차 검증을 돌려 판정 행 목록을 만든다.

    행은 ``{hostname, verdict, inconclusive_reason, diagnosis, compared_series, round_trips,
    evidence}``이고, 전제 오류면 ``{hostname, verdict: None, error}``다.
    """
    rows: list[dict[str, Any]] = []
    for host in hosts:
        result = await run_cross_check(config.prometheus, config.openmetrics, host, metric)
        if "error" in result:
            rows.append({"hostname": host, "verdict": None, "error": result["error"]})
            continue
        cc = result["cross_check"]
        rows.append({
            "hostname": host,
            "verdict": cc["verdict"],
            "inconclusive_reason": cc["inconclusive_reason"],
            "diagnosis": cc["diagnosis"],
            "compared_series": cc["compared_series"],
            "round_trips": cc["round_trips"],
            "evidence": cc["evidence"],
        })
    return rows


def _format_row(row: dict[str, Any]) -> str:
    """사람이 읽는 한 줄 — 호스트 · 판정(또는 보류 사유·오류) · 조치 안내."""
    verdict = row.get("verdict") or f"(보류: {row.get('inconclusive_reason') or row.get('error')})"
    return f"{row['hostname']}\t{verdict}\t{row.get('diagnosis') or ''}"


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 돌려준다(모듈 독스트링 참조)."""
    parser = argparse.ArgumentParser(description="PromQL ↔ OpenMetrics 교차 검증 (plans/92 §4.8.5)")
    parser.add_argument("--metric", required=True, help="bare 메트릭 이름(예: node_load1)")
    parser.add_argument("--hosts", default="",
                        help="쉼표 구분 hostname(server_name). 생략하면 허용목록 전체")
    parser.add_argument("--config", default=None, help="config.toml 경로(기본: 패키지 루트)")
    parser.add_argument("--json", action="store_true", help="판정 근거까지 JSON으로 출력")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if not config.prometheus.url:
        print("PROMETHEUS_URL 미설정 — 교차 검증 불가", file=sys.stderr)
        return 2
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip()] or [
        t.hostname for t in config.openmetrics.targets
    ]
    if not hosts:
        print("대상 호스트 없음 — --hosts 또는 [[openmetrics.targets]]를 지정", file=sys.stderr)
        return 2

    rows = asyncio.run(cross_check_hosts(config, hosts, args.metric))
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    else:
        for row in rows:
            print(_format_row(row))
    return 0 if all(r.get("verdict") == VERDICT_CONSISTENT for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
