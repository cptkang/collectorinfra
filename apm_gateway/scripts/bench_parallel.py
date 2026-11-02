"""조회 가속 측정(개발용) — plans/205 W4 · D-356.

가짜 전송(`httpx.MockTransport` · 실 제니퍼 접속 0)으로 응답 지연·응답 크기를 바꿔 가며, 도구 안
동시 호출(`APM_FANOUT_CONCURRENCY`) · 파싱 프로세스 풀(`APM_PARSE_WORKERS`) · 호출자 공정 분배
(`JENNIFER_MAX_INFLIGHT_PER_CALLER`·`APM_JOB_MAX_CONCURRENT_PER_OWNER`) 조합마다 한 행을 낸다.
사용자마다 대화형 작업 1개(`JobManager.execute`)를 동시에 띄우고, 작업 안에서 독립 호출
`--calls`건을 `gather_ordered`로 보낸다 — 제품 경로(자리 → 속도 제어 → HTTP → 파싱·자격증명
검사) 그대로다.

열: 전체 소요(실측·무축척 환산) · 이벤트 루프 최대 지연(10ms 틱 코루틴 · 축척과 무관한 CPU 지연이라
환산하지 않는다) · 최대 자리 대기(환산) · 자리 대기 초과 건수(`SOURCE_UNAVAILABLE` 「대기 초과」) ·
사용자 완료 p50/p95/최대(환산 · 사용자 2명 이상일 때).

축척(`--scale`): 응답 지연·API timeout·우선순위 에이징을 곱하고 초당 상한을 나눠 비율을 보존한다.
파싱 CPU 시간은 줄지 않으므로 큰 응답에서 환산값(÷축척)은 실제보다 크게 나온다(보수적). 자리·초당
상한·timeout은 설정 기본값(8 · 5 · 30s)을 읽어 쓰고 `--slots`·`--rate`·`--timeout`으로만 바꾼다 —
제품 기본값은 바꾸지 않는다. 파싱 풀은 조합마다 만들어 워커 수만큼 데운 뒤 재고 끝에 닫는다.

사용 예(`apm_gateway/`에서):
    ../.venv/bin/python scripts/bench_parallel.py --scale 0.1 --latency 1 3 --size-kib 512 \
        --calls 12
    ../.venv/bin/python scripts/bench_parallel.py --users 10 --latency 3 --fanout 1 2 4 \
        --scale 0.1 --fair on off --out /tmp/bench.md

폐쇄망 재측정(배포 뒤 · 계획서 §6): `APM_GATEWAY_LOG_LEVEL=DEBUG`로 같은 질의를 현행 값
(`APM_FANOUT_CONCURRENCY=1` · `APM_PARSE_WORKERS=0`)과 기본값으로 각각 실행해, 도구 감사 줄의
`elapsed_ms`·`waits=`(자리·속도 제어 대기 합·최대) · DEBUG 로그 `apm http … elapsed_ms` 합 · 스풀
로그 건수 · 게이트웨이 RSS(워커 수 × 증가분)를 비교한다. 이 스크립트의 표는 그 비교의 방향만
미리 본다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# 패키지 미설치 venv에서도 돈다(spawn 워커도 이 경로를 받는다)
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import httpx  # noqa: E402
from apm_gateway.application.fanout import gather_ordered  # noqa: E402
from apm_gateway.application.jobs import Job, JobManager  # noqa: E402
from apm_gateway.application.sources import SourceSet, build_source_set  # noqa: E402
from apm_gateway.domain.errors import SOURCE_UNAVAILABLE, ApmError  # noqa: E402

from apm_gateway.config import GatewayConfig, load_config  # noqa: E402

PATH = "/api/realtime/instance"
TICK = 0.01
HEADER = (
    "| 지연(s) | 크기(KiB) | 사용자 | 호출 | fanout | 워커 | 공정 | 전체 소요 실측(s) | "
    "전체 소요 환산(s) | 루프 최대 지연(ms) | 최대 자리 대기 환산(s) | 자리 대기 초과 | "
    "사용자 완료 p50/p95/최대 환산(s) |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|"
)


@dataclass(frozen=True)
class Case:
    latency: float
    size_kib: int
    fanout: int
    workers: int
    fair: bool


def _record(i: int) -> dict[str, Any]:
    return {
        "txid": i,
        "guid": f"g{i:010d}",
        "instanceId": i % 50,
        "domainId": 1,
        "applicationName": f"/api/v1/orders/{i}/detail",
        "responseTime": i % 9000,
        "sqlTime": 12,
        "externalCallTime": 3,
        "errorType": "",
        "clientIp": "10.1.2.3",
        "startTime": 1730000000000 + i,
        "endTime": 1730000000500 + i,
        "message": "SELECT * FROM t WHERE id = ? and user='app'",
    }


def make_body(size_kib: int) -> bytes:
    """`size_kib` 이상이 되는 합성 JSON 응답(거래 행 반복)."""
    one = len(json.dumps(_record(0)).encode()) + 2
    count = max(1, math.ceil(size_kib * 1024 / one))
    return json.dumps({"result": [_record(i) for i in range(count)]}).encode()


def _ok(tool: str, rows: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"tool": tool, "rows": rows, "row_count": len(rows), "limits": [], **extra}


def _err(tool: str, code: str, reason: str) -> dict[str, Any]:
    return {"tool": tool, "error": code, "reason": reason}


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def _env(args: argparse.Namespace, base: GatewayConfig, case: Case, spool: str) -> dict[str, str]:
    scale = args.scale
    slots = args.slots if args.slots is not None else base.jennifer.max_inflight_per_source
    rate = args.rate if args.rate is not None else base.jennifer.rate_limit_per_sec
    timeout = args.timeout if args.timeout is not None else base.jennifer.timeout_seconds
    env = {
        "JENNIFER_API_URL": "http://apm.bench",
        "JENNIFER_API_TOKEN": "bench-token",
        "JENNIFER_MAX_INFLIGHT_PER_SOURCE": str(slots),
        "JENNIFER_RATE_LIMIT_PER_SEC": str(rate / scale),
        "JENNIFER_API_TIMEOUT_SECONDS": str(timeout * scale),
        "APM_PRIORITY_AGING_SECONDS": str(base.jobs.priority_aging_seconds * scale),
        "APM_FANOUT_CONCURRENCY": str(case.fanout),
        "APM_PARSE_WORKERS": str(case.workers),
        "APM_SPOOL_DIR": spool,
    }
    if not case.fair:  # 되돌리기 값 — 호출자 상한·owner 작업 상한 끔
        env["JENNIFER_MAX_INFLIGHT_PER_CALLER"] = "0"
        env["APM_JOB_MAX_CONCURRENT_PER_OWNER"] = "0"
    return env


async def _lag_monitor(stop: asyncio.Event, lags: list[float]) -> None:
    """10ms마다 깨어나 예정보다 늦은 만큼(이벤트 루프 막힘)을 모은다."""
    loop = asyncio.get_running_loop()
    while not stop.is_set():
        started = loop.time()
        await asyncio.sleep(TICK)
        lags.append(loop.time() - started - TICK)


async def _close(sources: SourceSet) -> None:
    for src in sources:
        await src.api.client.aclose()
    if sources.parse_pool is not None:
        sources.parse_pool.shutdown()


async def run_case(
    args: argparse.Namespace, base: GatewayConfig, case: Case, body: bytes
) -> dict[str, Any]:
    """조합 1개를 돌려 지표(초 · 축척 그대로)를 돌려준다."""
    delay = case.latency * args.scale

    async def handler(request: httpx.Request) -> httpx.Response:
        if delay:
            await asyncio.sleep(delay)
        return httpx.Response(
            200, content=body, headers={"content-type": "application/json; charset=utf-8"}
        )

    with tempfile.TemporaryDirectory(prefix="apm-bench-") as spool:
        cfg = load_config(_env(args, base, case, spool), dotenv=None)
        sources = build_source_set(cfg, transport=httpx.MockTransport(handler))
        mgr = JobManager.from_config(cfg, envelope=_ok, error_envelope=_err)
        client = next(iter(sources)).api.client
        try:
            pool = sources.parse_pool
            # 워커를 다 띄워 둔다(기동 비용은 측정에서 뺀다)
            if pool is not None and len(body) >= pool.min_bytes:
                text = body.decode()
                await asyncio.gather(
                    *(pool.json(text, None, "utf-8", len(body)) for _ in range(pool.workers))
                )
            loop = asyncio.get_running_loop()
            over = 0
            jobs: list[Job] = []
            done: list[float] = []

            def tool(u: int) -> Any:
                async def call() -> dict[str, Any]:
                    nonlocal over
                    got = await gather_ordered(
                        range(args.calls),
                        lambda i: client.get_json(PATH, {"domain_id": u * args.calls + i + 1}),
                        limit=cfg.perf.fanout_concurrency,
                    )
                    for _, result in got:
                        if not isinstance(result, ApmError):
                            continue
                        if result.code == SOURCE_UNAVAILABLE and "대기 초과" in result.reason:
                            over += 1
                        else:  # 가짜 전송에서 다른 오류는 측정이 틀렸다는 뜻 — 숨기지 않는다
                            raise RuntimeError(f"예상 밖 오류: {result.code} {result.reason}")
                    return _ok("bench_parallel", [])

                return call

            async def user(u: int, t0: float) -> None:
                await mgr.execute(
                    "bench_parallel",
                    tool(u),
                    principal="bench",
                    owner=f"user:u{u}",
                    on_job=jobs.append,
                )
                done.append(loop.time() - t0)

            stop = asyncio.Event()
            lags: list[float] = []
            monitor = asyncio.create_task(_lag_monitor(stop, lags))
            t0 = loop.time()
            await asyncio.gather(*(user(u, t0) for u in range(args.users)))
            elapsed = loop.time() - t0
            stop.set()
            await monitor
        finally:
            await mgr.aclose()
            await _close(sources)
    return {
        "elapsed": elapsed,
        "lag_max": max(lags, default=0.0),
        "slot_wait_max": max((j.slot_wait_max_ms for j in jobs), default=0.0) / 1000,
        "over": over,
        "done": done,
    }


def row(args: argparse.Namespace, case: Case, r: dict[str, Any]) -> str:
    k = 1 / args.scale
    done = r["done"]
    users = (
        f"{statistics.median(done) * k:.1f} / {_pct(done, 0.95) * k:.1f} / {max(done) * k:.1f}"
        if args.users > 1
        else "-"
    )
    return (
        f"| {case.latency:g} | {case.size_kib} | {args.users} | {args.calls} | {case.fanout} | "
        f"{case.workers} | {'on' if case.fair else 'off'} | {r['elapsed']:.2f} | "
        f"{r['elapsed'] * k:.1f} | {r['lag_max'] * 1000:.0f} | {r['slot_wait_max'] * k:.1f} | "
        f"{r['over']} | {users} |"
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="APM 게이트웨이 조회 가속 측정(가짜 전송 · plans/205)")
    p.add_argument("--latency", type=float, nargs="+", default=[1.0], help="응답 지연(초 · 무축척)")
    p.add_argument("--size-kib", type=int, nargs="+", default=[512], help="응답 크기(KiB)")
    p.add_argument("--users", type=int, default=1, help="동시 사용자 수")
    p.add_argument("--calls", type=int, default=12, help="도구당 독립 호출 수")
    p.add_argument("--scale", type=float, default=1.0, help="시간 축척(0.1 = 10배 빠르게)")
    p.add_argument("--fanout", type=int, nargs="+", default=[1, 2, 4], help="도구 안 동시 수")
    p.add_argument(
        "--workers", nargs="+", default=["0", "default"], help="파싱 워커 수(default = 설정 기본값)"
    )
    p.add_argument("--fair", nargs="+", choices=["on", "off"], default=["on"], help="공정 분배")
    p.add_argument("--slots", type=int, default=None, help="소스당 자리(기본 = 설정 기본값)")
    p.add_argument("--rate", type=float, default=None, help="초당 상한(기본 = 설정 기본값)")
    p.add_argument("--timeout", type=float, default=None, help="API timeout 초(기본 = 설정 기본값)")
    p.add_argument("--out", type=Path, default=None, help="표를 이 파일에도 쓴다")
    args = p.parse_args(argv)
    if not args.scale > 0 or args.users < 1 or args.calls < 1:
        p.error("--scale > 0 · --users ≥ 1 · --calls ≥ 1")
    return args


async def amain(args: argparse.Namespace) -> list[str]:
    base = load_config({}, dotenv=None)  # 설정 기본값(자리·초당·timeout·에이징·워커 수)
    workers = sorted({base.perf.parse_workers if w == "default" else int(w) for w in args.workers})
    slots = args.slots if args.slots is not None else base.jennifer.max_inflight_per_source
    rate = args.rate if args.rate is not None else base.jennifer.rate_limit_per_sec
    timeout = args.timeout if args.timeout is not None else base.jennifer.timeout_seconds
    lines = [
        f"축척 {args.scale:g} · 자리 {slots} · 초당 {rate:g} · timeout {timeout:g}s · "
        f"사용자 {args.users} · 도구당 호출 {args.calls} · CPU {os.cpu_count()} · "
        f"풀 임계 {base.perf.parse_process_min_bytes}B",
        "",
        HEADER,
    ]
    for line in lines:
        print(line, flush=True)
    for size in args.size_kib:
        body = make_body(size)
        for latency in args.latency:
            for fair in (f == "on" for f in args.fair):
                for fanout in args.fanout:
                    for w in workers:
                        case = Case(latency, size, fanout, w, fair)
                        line = row(args, case, await run_case(args, base, case, body))
                        print(line, flush=True)
                        lines.append(line)
    return lines


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    lines = asyncio.run(amain(args))
    if args.out is not None:
        args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":  # spawn 풀 워커가 이 파일을 다시 import해도 측정이 돌지 않게
    sys.exit(main())
