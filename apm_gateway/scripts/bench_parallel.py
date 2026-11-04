"""도구 안 동시 호출 · 파싱 프로세스 풀 측정 — `plans/205` W4 · D-356 ①②.

가짜 전송(httpx `MockTransport`)으로 `apm_active_services(hostname=…)`를 돌린다 — 도메인마다 1회씩
독립 호출(`--calls`개)을 하고, 응답마다 실행 중 요청 `--size`건(본문 크기 = 표의 `응답 KiB`)을
돌려준다. 같은 도구 호출 `--jobs`개를 동시에 부른다(다중 사용자). **실 제니퍼를 부르지 않는다.**

네 조합을 차례로 잰다 — 순차(`APM_FANOUT_CONCURRENCY=1`)·동시(기본값) × 풀
끔(`APM_PARSE_WORKERS=0`)·켬(기본값). 열은 소요(초 · 동시 도구 호출 전체) · 이벤트 루프 최대
지연(ms · 5ms 주기 틱의 최대 초과 — 파싱·자격증명 검사가 루프를 붙잡은 길이) · 전송 호출 수 · 최대
동시 in-flight다. 풀은 측정 전에 워커를 미리 띄운다(spawn 기동 시간 제외 — 운영은 첫 사용 때 1회
든다).

사용 예(`apm_gateway/`에서 — 개발 맥·폐쇄망 게이트웨이 서버 공통):
    ../.venv/bin/python scripts/bench_parallel.py
    ../.venv/bin/python scripts/bench_parallel.py --delay 0.3 --size 5000 --calls 12 --jobs 4
    ../.venv/bin/python scripts/bench_parallel.py --rate 5 --inflight 8   # 운영 속도·자리 상한

폐쇄망 재측정(배포 뒤 · plans/205 §6과 함께): 실 응답 크기에 맞춰 `--size`를(표의 `응답 KiB`가
`apm http … bytes` 로그와 비슷해지게), 실 응답 지연에 맞춰 `--delay`를 고른다. 풀 임계
(`APM_PARSE_PROCESS_MIN_BYTES`)·워커 수(`APM_PARSE_WORKERS`)·폭(`APM_FANOUT_CONCURRENCY`)의
기본값은 이 프로세스의 설정 기본값을 쓴다(`.env`는 읽지 않는다 — 비교 조건을 고정한다). 다른
값을 재려면 `--fanout`·`--workers`·`--min-bytes`로 「켬」 쪽 값을 준다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from apm_gateway.adapters.parse_pool import build_parse_pool  # noqa: E402
from apm_gateway.application.sources import build_source_set  # noqa: E402
from apm_gateway.application.tools import ApmTools  # noqa: E402

from apm_gateway.config import PerfConfig, load_config  # noqa: E402

HOST = "bench-host"
TICK = 0.005  # 루프 지연 측정 틱(초)


class FakeJennifer:
    """도메인 1..`domains` · 도메인마다 인스턴스 1개(hostName 모두 `HOST`) · 실행 중 요청 목록만
    크게 돌려준다(본문은 미리 만들어 둔다 — 전송 쪽 직렬화 비용을 재지 않는다)."""

    def __init__(self, domains: int, size: int, delay: float) -> None:
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.peak = 0
        self._domain = json.dumps(
            {"result": [{"domainId": d, "name": f"dom-{d}"} for d in range(1, domains + 1)]}
        ).encode()
        self._instance = {
            d: json.dumps(
                {
                    "result": [
                        {
                            "instanceId": d * 100,
                            "name": f"was-{d}",
                            "hostName": HOST,
                            "ipAddress": "10.0.0.1",
                            "platform": "JAVA",
                            "status": "RUNNING",
                            "version": "5.6.5",
                        }
                    ]
                }
            ).encode()
            for d in range(1, domains + 1)
        }
        self._active = {d: self._active_body(d, size) for d in range(1, domains + 1)}

    @staticmethod
    def _active_body(domain: int, size: int) -> bytes:
        return json.dumps(
            {
                "result": [
                    {
                        "domainId": domain,
                        "instanceId": domain * 100,
                        "instanceName": f"was-{domain}",
                        "application": f"/app/{domain}/svc/{k % 97}",
                        "txid": f"{domain}-{k}",
                        "elapseTime": k,
                        "runningMode": "SQL" if k % 2 else "",
                        "runningDataSourceName": "ds" if k % 2 else "",
                        "startTime": 1_700_000_000_000 - k,
                        "sql": f"select * from t{k % 13} where id = ? and name like '%x{k}%'",
                    }
                    for k in range(size)
                ]
            }
        ).encode()

    @property
    def response_kib(self) -> float:
        return len(next(iter(self._active.values()), b"")) / 1024

    async def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        domain = int(request.url.params.get("domain_id", "0") or 0)
        if path == "/api/domain":
            return httpx.Response(200, content=self._domain)
        if path == "/api/instance":
            return httpx.Response(200, content=self._instance.get(domain, b'{"result": []}'))
        if path != "/api/activeService/list":
            return httpx.Response(404, text="no fixture")
        self.calls += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.active -= 1
        return httpx.Response(200, content=self._active[domain])


async def _loop_lag(stop: asyncio.Event, out: list[float]) -> None:
    worst = 0.0
    while not stop.is_set():
        started = time.perf_counter()
        await asyncio.sleep(TICK)
        worst = max(worst, time.perf_counter() - started - TICK)
    out.append(worst)


async def measure(args: argparse.Namespace, fanout: int, workers: int) -> dict[str, Any]:
    """한 조합 — (소요 초, 루프 최대 지연 ms, 전송 호출 수, 최대 in-flight)."""
    fake = FakeJennifer(args.calls, args.size, args.delay)
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config(
            {
                "JENNIFER_API_URL": "http://bench.test",
                "JENNIFER_API_TOKEN": "bench-token",
                "JENNIFER_RATE_LIMIT_PER_SEC": str(args.rate),
                "JENNIFER_MAX_INFLIGHT_PER_SOURCE": str(args.inflight),
                "APM_SPOOL_DIR": str(Path(tmp) / "spool"),
                "APM_FANOUT_CONCURRENCY": str(fanout),
                "APM_PARSE_WORKERS": str(workers),
                "APM_PARSE_PROCESS_MIN_BYTES": str(args.min_bytes),
            }
        )
        pool = build_parse_pool(cfg.perf.parse_workers, cfg.perf.parse_process_min_bytes)
        try:
            if pool is not None:  # 워커 미리 띄우기(기동 시간은 재지 않는다)
                await asyncio.gather(*(pool.text("warm") for _ in range(workers)))
            tools = ApmTools(
                build_source_set(cfg, transport=httpx.MockTransport(fake.handler), parse_pool=pool),
                cfg,
            )
            for src in tools.sources:  # 인스턴스 명단 선적재(측정 밖)
                await src.resolver.inventory()
            fake.calls = fake.peak = 0
            stop = asyncio.Event()
            lag: list[float] = []
            ticker = asyncio.create_task(_loop_lag(stop, lag))
            await asyncio.sleep(TICK * 2)
            started = time.perf_counter()
            results = await asyncio.gather(
                *(tools.apm_active_services(hostname=HOST) for _ in range(args.jobs))
            )
            took = time.perf_counter() - started
            stop.set()
            await ticker
            for src in tools.sources:
                await src.api.client.aclose()
        finally:
            if pool is not None:
                pool.shutdown()
    bad = [r for r in results if "error" in r]
    if bad:
        raise SystemExit(f"도구 오류: {bad[0].get('error')} {bad[0].get('reason')}")
    return {
        "seconds": took,
        "lag_ms": lag[0] * 1000 if lag else 0.0,
        "calls": fake.calls,
        "peak": fake.peak,
        "kib": fake.response_kib,
    }


def _table(rows: list[tuple[str, str, dict[str, Any]]]) -> str:
    head = (
        "| 호출 | 파싱 풀 | 소요(초) | 루프 최대 지연(ms) | 전송 호출 | 최대 in-flight"
        " | 응답 KiB |"
    )
    lines = [head, "|---|---|---:|---:|---:|---:|---:|"]
    for fan, pool, m in rows:
        lines.append(
            f"| {fan} | {pool} | {m['seconds']:.3f} | {m['lag_ms']:.1f} | {m['calls']} |"
            f" {m['peak']} | {m['kib']:.0f} |"
        )
    return "\n".join(lines)


async def main(args: argparse.Namespace) -> None:
    rows = []
    for fan_label, fanout in (("순차(1)", 1), (f"동시({args.fanout})", args.fanout)):
        for pool_label, workers in (("끔(0)", 0), (f"켬({args.workers})", args.workers)):
            rows.append((fan_label, pool_label, await measure(args, fanout, workers)))
    print(
        f"지연 {args.delay}s · 응답 {args.size}건 · 독립 호출 {args.calls} · 동시 도구 호출"
        f" {args.jobs} · 속도 {args.rate}/s(0 = 없음) · 자리 {args.inflight} · 풀 임계"
        f" {args.min_bytes}B"
    )
    print(_table(rows))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    perf = PerfConfig()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    ap.add_argument("--delay", type=float, default=0.05, help="가짜 전송 응답 지연(초)")
    ap.add_argument("--size", type=int, default=2000, help="응답 1건의 실행 중 요청 수(본문 크기)")
    ap.add_argument("--calls", type=int, default=8, help="도구 호출 1회의 독립 호출 수(도메인 수)")
    ap.add_argument("--jobs", type=int, default=4, help="동시에 부르는 도구 호출 수")
    ap.add_argument("--rate", type=float, default=0.0, help="소스 초당 호출 상한(0 = 없음)")
    ap.add_argument("--inflight", type=int, default=8, help="소스 동시 호출 자리 수")
    ap.add_argument(
        "--fanout", type=int, default=perf.fanout_concurrency, help="「동시」 쪽 폭(기본 = 설정)"
    )
    ap.add_argument(
        "--workers", type=int, default=perf.parse_workers, help="「켬」 쪽 워커 수(기본 = 설정)"
    )
    ap.add_argument(
        "--min-bytes",
        type=int,
        default=perf.parse_process_min_bytes,
        help="풀로 보내는 본문 임계(바이트 · 기본 = 설정)",
    )
    args = ap.parse_args(argv)
    if args.workers < 1 or args.fanout < 1:
        ap.error("--workers·--fanout은 1 이상이어야 한다(끔·순차 쪽은 표가 따로 잰다)")
    return args


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
