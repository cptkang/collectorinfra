"""이벤트 루프 위 CPU 비용 재현 벤치 (plans/206 §1.2 · W0 · 개발용).

요청 1건이 이벤트 루프를 쥐는 동기 구간 4개를 합성 입력으로 잰다. 외부 접속은 없다.

- 행 정규화(``db._normalize_row``) + ``json.dumps`` · 1,000행 × 12열(약 221KiB)
- 같음 · 10,000행(= ``max_rows`` 기본 상한 · 약 2.2MiB)
- ``security.validate_readonly`` · 약 1.7KB SELECT
- ``openmetrics.parse_exposition`` · 0.0.4 텍스트(약 197KiB · 3,000샘플)

구간마다 ``--repeat``회 반복해 최소값(ms)을 출력한다. 정규화는 행 dict를 제자리 수정하므로
반복마다 행을 새로 만들고, 생성 시간은 측정에서 뺀다.

사용:
    cd mcp_server && python scripts/bench_cpu.py
    python scripts/bench_cpu.py --repeat 10 --json

종료 코드: 0 = 정상.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

# `python scripts/bench_cpu.py`로 실행해도 패키지(`mcp_server`)가 잡히게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp_server.db import _normalize_row  # noqa: E402
from mcp_server.openmetrics import parse_exposition  # noqa: E402
from mcp_server.security import validate_readonly  # noqa: E402

#: 0.0.4 노출 텍스트의 Content-Type — exporter가 내는 값(``openmetrics.PROM_SCRAPE_ACCEPT`` 참조).
TEXT_004_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

_BASE_TS = datetime(2026, 1, 1, 0, 0, 0)


def make_rows(n: int) -> list[dict[str, Any]]:
    """정규화가 실제로 일하도록 datetime·date·Decimal·str·int를 섞은 12열 합성 행을 만든다."""
    rows: list[dict[str, Any]] = []
    for i in range(n):
        ts = _BASE_TS + timedelta(seconds=i * 60)
        rows.append({
            "id": i,
            "host": f"host-{i % 500:04d}",
            "item": f"demo_{i % 37}",
            "ts": ts,
            "updated": ts + timedelta(seconds=30),
            "day": date(2026, 1, 1 + i % 28),
            "avg": Decimal(f"{(i * 7) % 10000}.{i % 1000:03d}"),
            "max": Decimal(f"{(i * 13) % 10000}.{i % 997:03d}"),
            "min": Decimal(f"{(i * 3) % 1000}.{i % 991:03d}"),
            "cnt": 60 + i % 5,
            "status": "ok" if i % 11 else "warn",
            "note": f"r{i}-g{i % 17}",
        })
    return rows


def make_sql() -> str:
    """약 1.7KB 합성 SELECT — 주석·문자열 리터럴·조인·CTE를 섞어 검사기가 전 단계를 돌게 한다."""
    cols = ",\n       ".join(f"a.col_{i:02d} AS c{i:02d}" for i in range(43))
    return (
        "-- cpu bench synthetic query\n"
        "WITH recent AS (\n"
        "  SELECT s.row_id, s.host_name, MAX(s.collected_at) AS last_at\n"
        "  FROM t_sample s\n"
        "  WHERE s.collected_at >= CURRENT_TIMESTAMP - INTERVAL '1' DAY\n"
        "    AND s.status_text <> 'deleted; drop'\n"
        "  GROUP BY s.row_id, s.host_name\n"
        ")\n"
        f"SELECT {cols},\n"
        "       r.last_at, b.item_name, b.avg_value\n"
        "FROM t_sample a\n"
        "JOIN recent r ON r.row_id = a.row_id\n"
        "LEFT JOIN t_sample_detail b ON b.row_id = a.row_id /* 상세 */\n"
        "WHERE a.host_name LIKE 'sample-host-%'\n"
        "  AND a.description NOT LIKE '%update%'\n"
        "  AND b.avg_value BETWEEN 10 AND 90\n"
        "  AND a.item_name IN ('demo_item_1', 'demo_item_2', 'demo_item_3', 'demo_item_4')\n"
        "ORDER BY r.last_at DESC, a.row_id\n"
        "LIMIT 1000"
    )


def make_exposition_004(samples: int = 3000) -> str:
    """0.0.4 합성 노출 텍스트 — gauge·counter 패밀리 10개에 샘플을 고르게 나눈다."""
    families = 10
    per_family = samples // families
    lines: list[str] = []
    for f in range(families):
        kind = "counter" if f % 2 else "gauge"
        name = f"demo_metric_{f}_total" if kind == "counter" else f"demo_metric_{f}"
        lines.append(f"# HELP {name} synthetic {kind} for cpu bench")
        lines.append(f"# TYPE {name} {kind}")
        for s in range(per_family):
            lines.append(
                f'{name}{{instance="host-{s % 100:03d}",'
                f'dev="d{s % 8}",mode="m{s // 100}"}} {s * 1.5 + f:.3f}'
            )
    return "\n".join(lines) + "\n"


def _min_ms(
    fn: Callable[[Any], None], repeat: int, setup: Callable[[], Any] = lambda: None
) -> float:
    """``fn(setup())``을 ``repeat``회 돌려 최소 소요(ms)를 돌려준다. ``setup``은 측정 밖이다."""
    best = float("inf")
    for _ in range(repeat):
        arg = setup()
        start = time.perf_counter()
        fn(arg)
        best = min(best, (time.perf_counter() - start) * 1000)
    return best


def _bench_rows(n: int, repeat: int) -> dict[str, Any]:
    """행 정규화 + ``json.dumps`` 구간. 크기는 직렬화 결과 바이트 수다."""
    size = len(json.dumps([_normalize_row(r) for r in make_rows(n)]).encode())

    def run(rows: list[dict[str, Any]]) -> None:
        json.dumps([_normalize_row(r) for r in rows])

    return {
        "section": f"_normalize_row + json.dumps · {n:,}행 × 12열",
        "size_bytes": size,
        "min_ms": _min_ms(run, repeat, setup=lambda: make_rows(n)),
    }


def run_bench(repeat: int) -> list[dict[str, Any]]:
    """4개 구간을 재고 ``{section, size_bytes, min_ms}`` 목록을 돌려준다."""
    sql = make_sql()
    text = make_exposition_004()
    return [
        _bench_rows(1_000, repeat),
        _bench_rows(10_000, repeat),
        {
            "section": "validate_readonly · SELECT",
            "size_bytes": len(sql.encode()),
            "min_ms": _min_ms(lambda _: validate_readonly(sql), repeat),
        },
        {
            "section": "parse_exposition · 0.0.4 · 3,000샘플",
            "size_bytes": len(text.encode()),
            "min_ms": _min_ms(lambda _: parse_exposition(text, TEXT_004_CONTENT_TYPE), repeat),
        },
    ]


def _format_size(n: int) -> str:
    """바이트 수를 B·KiB·MiB로 읽기 쉽게 적는다."""
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f}MiB"
    if n >= 1024:
        return f"{n / 1024:.1f}KiB"
    return f"{n}B"


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점. 종료 코드를 돌려준다(모듈 독스트링 참조)."""
    parser = argparse.ArgumentParser(description="이벤트 루프 위 CPU 비용 벤치 (plans/206 §1.2)")
    parser.add_argument("--repeat", type=int, default=5,
                        help="구간별 반복 횟수(최소값 출력 · 기본 5)")
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    args = parser.parse_args(argv)
    if args.repeat < 1:
        parser.error("--repeat는 1 이상이어야 한다")

    results = run_bench(args.repeat)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    print(f"구간\t크기\t최소 ms (repeat={args.repeat})")
    for r in results:
        print(f"{r['section']}\t{_format_size(r['size_bytes'])}\t{r['min_ms']:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
