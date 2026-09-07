"""stack_dump.log / task_dump.log 요약 — 덤프별로 스레드(태스크)당 최심(innermost)
프레임 1줄로 압축하고, 연속 동일 구간을 접어서 출력한다. 마지막에 "지속 등장"
분석을 붙인다 — 전체 덤프의 60% 이상에서 같은 지점에 멈춰 있는 항목이 유력 용의다.

무한대기 구간은 "같은 시그니처가 장시간 반복되는 구간"으로 드러나므로
발현 시각을 몰라도 된다. diag-stack-dumper 자신은 제외한다.

사용:
  python scripts/stack_dump_summary.py                      # logs/stack_dump.log
  python scripts/stack_dump_summary.py logs/task_dump.log   # 태스크 덤프
  python scripts/stack_dump_summary.py logs/task_dump.log --full   # 구간 전체까지

기본 출력은 손 필사 가능한 요약(지속 등장 + 용의)만 낸다. "용의" = 상주
백그라운드 루프가 아니면서 3회(1.5분) 이상 연속으로 같은 지점에 고정된 태스크
— 무한대기의 미귀환 await가 여기에 걸린다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "logs" / "stack_dump.log"

# 기동 시 상주하는 정상 백그라운드 태스크/유휴 스레드 (2026-09-04 유휴 서버 실측 6종 + 진단 장치)
_IDLE_ALLOWLIST = (
    "_run_audit_retention_loop",
    "_run_file_log_retention_loop",
    "run_sse_bridge_subscriber",
    "run_incident_event_subscriber",
    "LifespanOn.main",
    "Server.serve",
    "diag-task-dumper",
    "diag-stack-dumper",
    "MainThread",
    "asyncio_",
    "AnyIO worker",
)

_TS_RE = re.compile(r"^===== (.+?) =====")
_THREAD_RE = re.compile(r"^--- (?:thread|task) (.+?)(?: \(id=\d+\))? ---")
_FRAME_RE = re.compile(r'^\s*File "(.+?)", line (\d+), in (.+)')


def _short(path: str) -> str:
    parts = re.split(r"[\\/]", path)
    return "/".join(parts[-2:]) if len(parts) >= 2 else path


def parse(path: Path) -> list[tuple[str, dict[str, str]]]:
    """[(timestamp, {thread_name: 'file:line in func'})] 목록을 반환한다."""
    dumps: list[tuple[str, dict[str, str]]] = []
    ts: str | None = None
    thread: str | None = None
    threads: dict[str, str] = {}
    last_frame: str | None = None

    def _commit_thread() -> None:
        nonlocal thread, last_frame
        if thread and last_frame and thread != "diag-stack-dumper":
            threads[thread] = last_frame
        thread, last_frame = None, None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _TS_RE.match(line)
        if m:
            _commit_thread()
            if ts and threads:
                dumps.append((ts, threads))
            ts, threads = m.group(1), {}
            continue
        m = _THREAD_RE.match(line)
        if m:
            _commit_thread()
            thread = m.group(1)
            continue
        m = _FRAME_RE.match(line)
        if m:
            last_frame = f"{_short(m.group(1))}:{m.group(2)} in {m.group(3)}"
    _commit_thread()
    if ts and threads:
        dumps.append((ts, threads))
    return dumps


def _find_suspects(
    dumps: list[tuple[str, dict[str, str]]], min_streak: int = 3
) -> list[tuple[str, str, str, str, int]]:
    """상주 루프 제외, min_streak회 이상 연속으로 같은 프레임에 고정된 항목.

    Returns: [(name, frame, start_ts, end_ts, streak)]
    """
    streaks: dict[tuple[str, str], tuple[str, str, int]] = {}  # (name,frame) -> (start,end,n)
    results: dict[tuple[str, str], tuple[str, str, int]] = {}
    for ts, threads in dumps:
        seen = set()
        for name, frame in threads.items():
            if any(pat in name for pat in _IDLE_ALLOWLIST):
                continue
            key = (name, frame)
            seen.add(key)
            if key in streaks:
                start, _end, n = streaks[key]
                streaks[key] = (start, ts, n + 1)
            else:
                streaks[key] = (ts, ts, 1)
        for key in list(streaks):
            if key not in seen:  # 끊긴 스트릭은 최고 기록만 보존
                cur = streaks.pop(key)
                if cur[2] >= results.get(key, ("", "", 0))[2]:
                    results[key] = cur
    for key, cur in streaks.items():
        if cur[2] >= results.get(key, ("", "", 0))[2]:
            results[key] = cur
    return sorted(
        [(n, f, s, e, c) for (n, f), (s, e, c) in results.items() if c >= min_streak],
        key=lambda x: -x[4],
    )


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--full"]
    full = "--full" in sys.argv[1:]
    path = Path(args[0]) if args else DEFAULT_PATH
    if not path.exists():
        print(f"파일 없음: {path}")
        return
    dumps = parse(path)
    if not dumps:
        print("파싱된 덤프가 없습니다.")
        return

    # 연속 동일 시그니처 접기
    runs: list[tuple[str, str, int, dict[str, str]]] = []  # (start, end, count, threads)
    for ts, threads in dumps:
        sig = tuple(sorted(threads.items()))
        if runs and tuple(sorted(runs[-1][3].items())) == sig:
            start, _end, count, th = runs[-1]
            runs[-1] = (start, ts, count + 1, th)
        else:
            runs.append((ts, ts, 1, threads))

    print(f"총 {len(dumps)}개 덤프 ({dumps[0][0]} ~ {dumps[-1][0]}) → {len(runs)}개 구간\n")
    if full:
        for start, end, count, threads in runs:
            span = start if count == 1 else f"{start} ~ {end}"
            print(f"{span} (x{count})")
            width = max(len(n) for n in threads) if threads else 0
            for name in sorted(threads):
                print(f"  {name:<{width}} : {threads[name]}")
            print()

    suspects = _find_suspects(dumps)
    print("== 용의 (상주 백그라운드 제외, 1.5분+ 연속 고정) ==")
    if suspects:
        for name, frame, start, end, streak in suspects:
            print(f"  {start} ~ {end} (x{streak}) {name} : {frame}")
    else:
        print("  없음 — 이 창에서 장기 대기한 요청 태스크 흔적 없음")
    print()

    # 지속 등장 분석 — 요청 수명 태스크(Task-N)가 여러 덤프에 걸쳐 같은 지점에
    # 멈춰 있으면 그게 미귀환 await의 정체다. 유휴 서버 태스크도 걸리므로
    # src/ 경로 포함 여부를 함께 표기한다.
    total = len(dumps)
    counts: dict[tuple[str, str], int] = {}
    for _ts, threads in dumps:
        for name, frame in threads.items():
            counts[(name, frame)] = counts.get((name, frame), 0) + 1
    persistent = [
        (n, f, c)
        for (n, f), c in counts.items()
        if c >= max(2, int(total * 0.6)) and not any(p in n for p in _IDLE_ALLOWLIST)
    ]
    if persistent:
        print(f"== 지속 등장 (전체 {total}개 덤프 중 60%+, 상주 백그라운드 제외) ==")
        for name, frame, c in sorted(persistent, key=lambda x: -x[2]):
            mark = "  ◀ src/" if "src/" in frame or "src\\" in frame else ""
            print(f"  x{c:<3} {name} : {frame}{mark}")


if __name__ == "__main__":
    main()
