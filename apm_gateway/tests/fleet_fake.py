"""plans/134 W3 시험용 합성 Open API — 소스 N개 × 도메인 M개(실시간 인스턴스·이벤트를 도메인별로
만든다) + 가상 시계. `httpx.MockTransport` 핸들러로만 쓴다(외부 네트워크 0).

필드명은 Open API 5.6.4 스키마 이름이다(합성 — `conftest` 합성 픽스처와 같은 원칙).
"""

from __future__ import annotations

import asyncio
import json
import random
from typing import Any

import httpx

NOT_CONNECTED = {"exception": {"message": "Domain is not connected"}}


class Clock:
    """가상 시계(초) — 폴러·버퍼·도구가 함께 읽는다."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def event(
    domain_id: int,
    instance_id: int,
    time_ms: int,
    *,
    level: str = "WARNING",
    error_type: str = "ERROR_SERVICE_QUEUING",
    txid: str = "",
    message: str = "queue full user=kim@example.com",
) -> dict[str, Any]:
    return {
        "domainId": domain_id,
        "domainName": "",  # 응답에 이름이 없으면 인벤토리 이름으로 채운다
        "instanceId": instance_id,
        "instanceName": f"inst-{instance_id}",
        "errorType": error_type,
        "metricsName": "",
        "eventLevel": level,
        "message": message,
        "value": 1.0,
        "time": str(time_ms),
        "txid": txid,
        "applicationName": "/order/list?user=kim",
        "instanceOid": 90_000 + instance_id,
    }


class FakeFleet:
    """`hosts` = 소스 URL 호스트(소스 id = 첫 마디). 도메인 id 1..`domains` · 도메인마다 인스턴스
    `per_domain`개(id = 도메인×100 + k · hostName `<소스>-h<도메인>-<k>`). 실시간 응답시간은 전부
    다른 값이고, 일부 인스턴스는 그 칸이 없다(None)."""

    def __init__(
        self,
        domains: int,
        *,
        per_domain: int = 2,
        hosts: tuple[str, ...] = ("bank.test", "common.test"),
        seed: int = 7,
        domain_names: dict[int, str] | None = None,
    ) -> None:
        self.hosts = hosts
        # 도메인 이름(서비스 이름) — 주면 모든 소스가 같은 이름을 쓴다(소스마다 같은 도메인 id·이름)
        self.domain_names = domain_names
        self.domains = list(range(1, domains + 1))
        self.per_domain = per_domain
        rnd = random.Random(seed)
        total = len(hosts) * domains * per_domain
        values = iter(rnd.sample(range(1, total * 10), total))
        self.realtime: dict[tuple[str, int], list[dict[str, Any]]] = {}
        # 오라클 재료 — (값 또는 None, 소스 id, 도메인, 인스턴스)
        self.truth: list[tuple[float | None, str, int, int]] = []
        for host in hosts:
            sid = host.split(".")[0]
            for d in self.domains:
                recs = []
                for k in range(per_domain):
                    iid = d * 100 + k
                    value: float | None = next(values) / 10
                    raw = {
                        "domainId": d,
                        "instanceId": iid,
                        "instanceName": f"{sid}-i{d}-{k}",
                        "responseTime": value,
                        "tps": float(d + k),
                        "visitDay": d * 10 + k,
                        "instanceDescription": "담당 lee@example.com",
                        "instanceOid": 70_000 + iid,
                    }
                    if (d * 7 + k) % 23 == 0:  # 응답시간 칸 없음 → 순위 밖
                        del raw["responseTime"]
                        value = None
                    recs.append(raw)
                    self.truth.append((value, sid, d, iid))
                self.realtime[(host, d)] = recs
        self.events: dict[tuple[str, int], list[dict[str, Any]]] = {}
        self.realtime_fail: set[tuple[str, int]] = set()
        self.event_fail: set[tuple[str, int]] = set()
        # 이벤트 응답 본문을 그대로 바꾼다(모양 시험 — 봉투가 아닌 본문) · 구간을 무시하고 전부 준다
        self.event_body: dict[tuple[str, int], Any] = {}
        self.ignore_window = False
        self.gate: asyncio.Event | None = None
        self.gate_paths: tuple[str, ...] = ()
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def source_ids(self) -> list[str]:
        return [h.split(".")[0] for h in self.hosts]

    def env(self, **extra: str) -> dict[str, str]:
        """다중 설정(소스 2개 이상) 또는 단일 설정(소스 1개 · id default)."""
        base = {"JENNIFER_RATE_LIMIT_PER_SEC": "0", **extra}
        if len(self.hosts) == 1:
            return {
                "JENNIFER_API_URL": f"http://{self.hosts[0]}",
                "JENNIFER_API_TOKEN": "tok-SECRET-9f3a",
                **base,
            }
        ids = self.source_ids()
        env = {"JENNIFER_SOURCES": json.dumps(ids), **base}
        for host, sid in zip(self.hosts, ids, strict=True):
            env[f"JENNIFER_{sid.upper()}_API_URL"] = f"http://{host}"
            env[f"JENNIFER_{sid.upper()}_API_TOKEN"] = f"tok-{sid}-SECRET"
        return env

    def add_event(self, host: str, raw: dict[str, Any]) -> None:
        self.events.setdefault((host, int(raw["domainId"])), []).append(raw)

    def event_calls(self) -> list[tuple[str, dict[str, str]]]:
        return [(h, q) for h, p, q in self.calls if p == "/api/dbsearch/event"]

    async def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        query = dict(request.url.params)
        self.calls.append((host, path, query))
        if self.gate is not None and path in self.gate_paths:
            await self.gate.wait()
        sid = host.split(".")[0]
        if path == "/api/domain":
            names = self.domain_names or {}
            return httpx.Response(
                200,
                json={
                    "result": [
                        {"domainId": d, "name": names.get(d, f"{sid}-dom-{d}")}
                        for d in self.domains
                    ]
                },
            )
        d = int(query.get("domain_id", "0"))
        if path == "/api/instance":
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "instanceId": d * 100 + k,
                            "name": f"{sid}-i{d}-{k}",
                            "hostName": f"{sid}-h{d}-{k}",
                            "ipAddress": "10.0.0.1",
                            "platform": "JAVA",
                            "status": "RUNNING",
                            "version": "5.6.5",
                        }
                        for k in range(self.per_domain)
                    ]
                },
            )
        if path == "/api/realtime/instance":
            if (host, d) in self.realtime_fail:
                return httpx.Response(500, json=NOT_CONNECTED)
            return httpx.Response(200, json={"result": self.realtime.get((host, d), [])})
        if path == "/api/dbsearch/event":
            if (host, d) in self.event_fail:
                return httpx.Response(500, json=NOT_CONNECTED)
            if (host, d) in self.event_body:
                return httpx.Response(200, json=self.event_body[(host, d)])
            lo, hi = int(query["start_time"]), int(query["end_time"])
            found = [
                e
                for e in self.events.get((host, d), [])
                if self.ignore_window or lo <= int(e["time"]) <= hi
            ]
            return httpx.Response(200, json={"result": found})
        return httpx.Response(404, text="no fixture")
