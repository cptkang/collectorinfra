"""게이트웨이 테스트 공용 픽스처.

- 실행: `cd apm_gateway && ../.venv/bin/python -m pytest -q`(자체 cwd). 루트에서
  `apm_gateway/tests`를 직접 돌려도 되도록 게이트웨이 루트(`apm_gateway/`)를 `sys.path` 맨 앞에 둔다
  — 루트의 같은 이름 디렉터리(네임스페이스 패키지) 대신 2단 중첩 안쪽 패키지를 잡기 위해서다.
- 네트워크는 127.0.0.1 임시 포트의 목 Open API 서버(`testdata/jennifer/scripts/mock_openapi.py`)와
  `httpx.MockTransport`만 쓴다. 실 제니퍼·외부 호출 없음.
- **합성 픽스처(`spec-synthetic`)**: 녹화본은 라이선스 없는 상태라 실데이터 모양이 없다(§0.10 #19).
  도구 경로를 검증하려고 Open API 5.6.4 스키마 필드명대로 만든 합성 응답을 **테스트 임시
  디렉터리에만** 만든다 (`recorded/`에 두지 않는다 — 추정 모양을 계약으로 굳히지 않는다). J0-L-b
  녹화 뒤 교체 대상이다.
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
if str(GATEWAY_ROOT) not in sys.path:
    sys.path.insert(0, str(GATEWAY_ROOT))
SCRIPTS = GATEWAY_ROOT / "testdata" / "jennifer" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(1, str(SCRIPTS))
RECORDED = GATEWAY_ROOT / "testdata" / "jennifer" / "recorded" / "local-docker"

TOKEN = "tok-SECRET-9f3a"  # 로그·오류·반환에 나오면 안 되는 값(식별 쉬운 문자열)
DOMAIN = 1000
# 기준 시각 2026-09-29 10:00:00 KST = 01:00:00Z
NOW_S = 1790643600.0
NOW_MS = int(NOW_S * 1000)


def _fx(
    template: str,
    body,
    *,
    label: str = "",
    status: int = 200,
    content_type: str = "application/json",
) -> dict:
    resp = {"status": status, "content_type": content_type}
    if isinstance(body, str):
        resp["body_text"] = body
    else:
        resp["body_json"] = body
    return {
        "fixture_version": 1,
        "source": "spec-synthetic",
        "jennifer_version": "5.6.4-spec",
        "request": {"method": "GET", "template": template, "path": template, "query": {}},
        "response": resp,
        "variant": "ok",
        "label": label,
    }


def synthetic_fixtures() -> list[dict]:
    """합성 응답(Open API 5.6.4 스키마 필드명) — 호스트 was-host01에 인스턴스 2개 · api02 1개."""
    instances = [
        {
            "instanceId": 1001,
            "name": "was01_a",
            "hostName": "was-host01.example.local",
            "ipAddress": "10.0.0.11",
            "platform": "JAVA",
            "status": "RUNNING",
            "version": "5.6.5",
        },
        {
            "instanceId": 1002,
            "name": "was01_b",
            "hostName": "WAS-HOST01",
            "ipAddress": "10.0.0.11",
            "platform": "JAVA",
            "status": "RUNNING",
            "version": "5.6.5",
        },
        {
            "instanceId": 1003,
            "name": "api02_main",
            "hostName": "",
            "ipAddress": "10.0.0.12",
            "platform": "JAVA",
            "status": "RUNNING",
            "version": "5.6.5",
        },
    ]
    realtime = [
        {
            "domainId": DOMAIN,
            "instanceId": 1001,
            "instanceName": "was01_a",
            "responseTime": 850.0,
            "tps": 42.5,
            "activeService": 37,
            "badResponseActiveService": 4,
            "rejectRate": 2.0,
            "concurrentUser": 120.0,
            "arrivalRate": 44.0,
            "heapUsed": 930.0,
            "heapCommitted": 1000.0,
            "nonHeapUsed": 150.0,
            "gcTimeUsage": 12.5,
            "procCPU": 71.0,
            "procMemory": 1400.0,
            "threadCurrent": 210,
            "activeDBConnection": 19.0,
            "averageDbPoolIdleCount": 1,
            "averageDbPoolConfiguredCount": 20,
        },
        {
            "domainId": DOMAIN,
            "instanceId": 1002,
            "instanceName": "was01_b",
            "responseTime": 120.0,
            "tps": 40.0,
            "activeService": 3,
            "badResponseActiveService": 0,
            "rejectRate": 0.0,
            "concurrentUser": 80.0,
            "arrivalRate": 40.0,
            "heapUsed": 400.0,
            "heapCommitted": 1000.0,
            "nonHeapUsed": 140.0,
            "gcTimeUsage": 1.0,
            "procCPU": 20.0,
            "procMemory": 900.0,
            "threadCurrent": 120,
            "activeDBConnection": 2.0,
            "averageDbPoolIdleCount": 18,
            "averageDbPoolConfiguredCount": 20,
        },
    ]
    transactions = [
        {
            "domainId": DOMAIN,
            "instanceId": 1001,
            "applicationName": f"/order/list?user=kim&page={i}",
            "txid": str(9000 + i),
            "responseTime": 4000 - i * 100,
            "cpuTime": 100,
            "sqlTime": 2500 - i * 50,
            "fetchTime": 300,
            "externalcallTime": 50,
            "networkTime": 10,
            "errorType": "SQL_EXCEPTION" if i == 0 else "",
            "endTime": str(NOW_MS - 30_000 - i * 1000),
        }
        for i in range(6)
    ]
    active = [
        {
            "domainId": DOMAIN,
            "instanceId": 1001,
            "application": f"/pay?card=1234-5678&i={i}",
            "status": "RUNNING",
            "statusName": "SQL_EXECUTING",
            "elapseTime": 700_000 + i,
            "statusElapseTime": 650_000,
            "runningMode": "SQL",
            "runningFullText": (
                "select * from orders where user_email = 'kim@example.com' and id = 42"
            ),
            "runningDataSourceName": "jdbc/orderDS",
            "clientIp": "192.168.10.77",
            "txid": str(8000 + i),
            "startTime": NOW_MS - 700_000,
        }
        for i in range(4)
    ]
    events = [
        {
            "domainId": DOMAIN,
            "domainName": "demo-domain",
            "instanceId": 1001,
            "instanceName": "was01_a",
            "errorType": "ERROR_OUTOFMEMORY",
            "metricsName": "",
            "eventLevel": "FATAL",
            "message": "java.lang.OutOfMemoryError: Java heap space user=kim@example.com",
            "value": 1.0,
            "time": str(NOW_MS - 120_000),
            "txid": "9000",
            "applicationName": "/order/list?user=kim",
        },
    ]
    errors = [
        {
            "domainId": DOMAIN,
            "instanceId": 1001,
            "errorType": "SQL_EXCEPTION",
            "message": "ORA-00001",
            "time": str(NOW_MS - 60_000),
            "txid": "9000",
            "applicationName": "/order/list",
        },
    ]

    def series(values):
        return {
            "result": [
                {"time": str(NOW_MS - (len(values) - k) * 300_000), "value": v}
                for k, v in enumerate(values)
            ]
        }

    return [
        _fx("/api/domain", {"result": [{"domainId": DOMAIN, "name": "demo-domain"}]}),
        _fx("/api/instance", {"result": instances}),
        _fx("/api/realtime/instance", {"result": realtime}),
        _fx("/api/transaction/time", {"result": transactions}),
        _fx("/api/activeService/list", {"result": active}),
        _fx("/api/dbsearch/event", {"result": events}),
        _fx("/api/dbsearch/error", {"result": errors}),
        _fx("/api/dbmetrics/instance", series([700, 910, 920, 950, 960, 990]), label="heap_used"),
        _fx("/api/dbmetrics/instance", series([1000] * 6), label="heap_committed"),
        _fx("/api/dbmetrics/instance", series([2, 3, 4, 5, 6, 7]), label="gc_time_usage"),
        _fx(
            "/api/status/application",
            {
                "result": [
                    {
                        "name": "/order/list?user=kim",
                        "calls": 300,
                        "failures": 12,
                        "badResponses": 20,
                        "responseTime": 900.0,
                        "maxResponseTime": 9000,
                    },
                ]
            },
        ),
        _fx("/api/transaction/txid", {"result": [transactions[0]]}),
        _fx(
            "/api/transaction/profile.txt",
            "START /order/list\n"
            "  SQL select * from orders where id = 42 and name = 'kim'\n"
            "END 4000ms\n",
            content_type="text/plain",
        ),
        _fx("/api/transaction/sql", {"result": [{"sql": "select * from orders where id = 42"}]}),
    ]


def write_fixtures(directory: Path, fixtures: list[dict]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for idx, fx in enumerate(fixtures):
        (directory / f"fx_{idx:02d}.json").write_text(
            json.dumps(fx, ensure_ascii=False), encoding="utf-8"
        )
    return directory


@pytest.fixture
def mock_server_factory(tmp_path):
    """`(fixtures_dir, mode)` → `(base_url, state)` — 127.0.0.1 임시 포트 목 서버를 띄운다."""
    from mock_openapi import make_server

    servers = []

    def start(fixtures_dir: Path | None, mode: str = "connected"):
        server, state = make_server(fixtures_dir, TOKEN, mode)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", state

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def synthetic_dir(tmp_path) -> Path:
    return write_fixtures(tmp_path / "spec-synthetic", synthetic_fixtures())


def make_cfg(url: str, *, token: str = TOKEN, extra: dict | None = None):
    from apm_gateway.config import load_config

    env = {
        "JENNIFER_API_URL": url,
        "JENNIFER_API_TOKEN": token,
        "JENNIFER_RATE_LIMIT_PER_SEC": "0",
        **(extra or {}),
    }
    return load_config(env)


def make_tools(url: str, *, clock=lambda: NOW_S, extra: dict | None = None, transport=None):
    from apm_gateway.adapters.jennifer.api import JenniferApi
    from apm_gateway.adapters.jennifer.client import JenniferClient
    from apm_gateway.application.resolver import InstanceResolver
    from apm_gateway.application.tools import ApmTools

    cfg = make_cfg(url, extra=extra)
    api = JenniferApi(JenniferClient(cfg.jennifer, transport=transport))
    resolver = InstanceResolver(
        api, cfg.policies.instance_map, domain_filter=cfg.jennifer.domain_ids
    )
    return ApmTools(api, resolver, cfg, clock=clock), cfg
