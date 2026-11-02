"""plans/205 W4 — 측정 스크립트 `scripts/bench_parallel.py` 스모크.

축척 0.05 · 작은 인자로 별도 프로세스에서 1회 돌려(`__main__` 가드 · spawn 풀 포함) 표 머리와
조합 수만큼의 행이 stdout과 `--out` 파일에 나오는지만 본다. 외부 네트워크 0 — 스크립트는
가짜 전송만 쓴다.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bench_parallel.py"
ARGS = "--scale 0.05 --latency 0.2 --size-kib 300 --calls 4 --users 2 --fanout 1 2 --workers 0 1"


def _rows(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("| ") and "---" not in line]


def test_bench_script_prints_header_and_one_row_per_combination(tmp_path):
    out = tmp_path / "bench.md"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            *ARGS.split(),
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    for text in (proc.stdout, out.read_text(encoding="utf-8")):
        rows = _rows(text)
        assert rows[0].startswith("| 지연(s) | 크기(KiB) |")
        assert "루프 최대 지연(ms)" in rows[0] and "자리 대기 초과" in rows[0]
        assert len(rows) == 1 + 2 * 2  # 머리 + fanout 2 × 워커 2
        assert all(row.count("|") == rows[0].count("|") for row in rows[1:])
