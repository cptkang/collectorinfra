"""플레이북 실행 시간축 측정 — 실 dispatcher + 녹화 픽스처 + 호출 지연 주입(plans/200 I-0).

녹화 픽스처(`tests/fixtures/plan187/*.json` + `tests/fixtures/plan200/*.json`)를 실
`InvestigationDispatcher`(심층 off)로 돌리면서 도구 배치 호출·소견 LLM 호출에 지연을 넣어
스텝·도구 배치·LLM 호출의 시작·종료 시각, 총 벽시계, 플레이북 수집 벽시계(`collect_s`)를 낸다.
병렬화(plans/200 W2·W3)의 전후 비교 기준선이다.

- 도구 지연: 배치 1회에 `tool_delay × ceil(호출 수 / batch_concurrency)`초(게이트웨이·MCP의 배치
  동시 처리 근사). LLM 지연: 소견기 호출 1회에 `llm_delay`초.
- `--dag`·`--axis`·`--max-workers`는 설정(`sre_playbook_dag_scheduling_enabled`·
  `sre_playbook_axis_concurrency_enabled`·`sre_playbook_max_workers`)으로만 넘긴다 — 실행기가 그
  설정을 읽을 때부터 효과가 있다.
- `--activity on`이면 실행 기록을 켠다(단계 끝 해석 · `opinion_wait_s` 값이 생긴다).
- 스텝 구간은 스텝 라이브러리(`STEP_LIBRARY`)를 이 함수 안에서만 감싸 잰다(끝나면 원복 — 같은
  프로세스에서 동시에 다른 플레이북을 돌리지 않는다). 액션 → 스텝 ID는 플레이북 YAML로 푼다.
- 실 LLM·MCP·DB 호출 0(가짜 소견기 · 녹화 조회).

실행(sre_agent venv): `.venv/bin/python scripts/investigation_bench/playbook_timeline.py host_cpu
--tool-delay 0.5 --llm-delay 1.0 --dag off`
"""

from __future__ import annotations

import argparse
import math
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TOP = Path(__file__).resolve().parents[2]
for _p in (TOP, TOP / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from investigation_bench import playbook_fixture_eval as fe  # noqa: E402

FIXTURE_DIRS: tuple[Path, ...] = (fe.FIXTURE_DIR, TOP / "tests" / "fixtures" / "plan200")


@dataclass
class Span:
    """시간축 한 칸 — 시작·종료는 실행 시작 기준 초."""

    kind: str  # step | tool | llm
    start: float
    end: float
    label: str
    step: str | None = None


@dataclass
class Timeline:
    """픽스처 1건의 측정 결과."""

    name: str
    playbook_id: str | None
    status: str
    total_s: float
    instrumentation: dict[str, Any]
    spans: list[Span] = field(default_factory=list)
    tool_calls: int = 0
    unrecorded: int = 0
    llm_calls: int = 0

    def of(self, kind: str) -> list[Span]:
        return sorted((s for s in self.spans if s.kind == kind), key=lambda s: (s.start, s.label))


def load_all_fixtures(dirs: Sequence[Path] = FIXTURE_DIRS) -> dict[str, dict[str, Any]]:
    """픽스처 전부(이름 → 내용) — 뒤 폴더가 같은 이름을 덮지 않게 이름 충돌은 오류."""
    out: dict[str, dict[str, Any]] = {}
    for d in dirs:
        for name, fx in fe.load_fixtures(d).items():
            if name in out:
                raise ValueError(f"픽스처 이름 중복: {name}")
            out[name] = fx
    return out


def timeline_settings(*, dag: bool, axis: bool, max_workers: int | None,
                      activity: bool) -> Any:
    """`offline_settings()` 사본에 측정 플래그만 바꾼다."""
    update: dict[str, Any] = {
        "sre_playbook_dag_scheduling_enabled": dag,
        "sre_playbook_axis_concurrency_enabled": axis,
        "sre_activity_log_enabled": activity,
    }
    if max_workers is not None:
        update["sre_playbook_max_workers"] = max_workers
    return fe.offline_settings().model_copy(update=update)


class _Clock:
    """실행 시작 기준 경과 초 + 구간 기록(스레드 안전)."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.spans: list[Span] = []
        self.local = threading.local()
        self._lock = threading.Lock()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def add(self, span: Span) -> None:
        with self._lock:
            self.spans.append(span)


class _DelayedCaller(fe.RecordedCaller):
    """녹화 조회 + 배치 지연 — 배치마다 도구 구간 1칸."""

    def __init__(self, fixture: Mapping[str, Any], clock: _Clock, delay: float,
                 concurrency: int) -> None:
        super().__init__(fixture)
        self._clock, self._delay, self._conc = clock, delay, max(1, concurrency)

    def __call__(self, calls: Sequence[tuple[str, Mapping[str, Any]]]) -> list[str]:
        s = self._clock.now()
        time.sleep(self._delay * math.ceil(len(calls) / self._conc))
        out = super().__call__(calls)
        self._clock.add(Span("tool", s, self._clock.now(), ",".join(t for t, _ in calls),
                             getattr(self._clock.local, "step", None)))
        return out


class _DelayedLLM(fe.CountingLLM):
    """가짜 소견기 + 호출 지연 — 호출마다 LLM 구간 1칸."""

    def __init__(self, clock: _Clock, delay: float) -> None:
        super().__init__()
        self._clock, self._delay = clock, delay

    def __call__(self, messages: list[dict[str, str]]) -> str:
        s = self._clock.now()
        time.sleep(self._delay)
        self._clock.add(Span("llm", s, self._clock.now(), "소견기"))
        return super().__call__(messages)


def _timed_library(library: dict[str, Callable[[Any], Any]],
                   clock: _Clock) -> dict[str, Callable[[Any], Any]]:
    """스텝 함수 감싸기 — 시작·종료 구간 + 스레드 지역 액션(도구 배치 귀속용 · 스텝 ID는 실행 뒤
    플레이북으로 푼다)."""

    def wrap(action: str, fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        def timed(ctx: Any) -> Any:
            clock.local.step = action
            s = clock.now()
            try:
                return fn(ctx)
            finally:
                clock.add(Span("step", s, clock.now(), action, action))
                clock.local.step = None

        return timed

    return {a: wrap(a, f) for a, f in library.items()}


def run_timeline(name: str, fx: Mapping[str, Any], *, tool_delay: float = 0.5,
                 llm_delay: float = 1.0, batch_concurrency: int = 4, dag: bool = False,
                 axis: bool = False, max_workers: int | None = None,
                 activity: bool = False, playbooks: Sequence[Any] | None = None) -> Timeline:
    """픽스처 1건 → 시간축(실 dispatcher · 심층 off — `evaluate_one`과 같은 배선)."""
    from sre_agent.application import playbook_steps
    from sre_agent.application.briefing_builder import build_briefing
    from sre_agent.application.investigation_dispatcher import (
        InvestigationDispatcher,
        PlaybookWiring,
    )
    from sre_agent.application.investigation_jobs import InvestigationJob
    from sre_agent.diagnosis import DiagnosisResult
    from sre_agent.domain.activity_log import ActivityLog
    from sre_agent.infrastructure.activity_masking import mask_activity_text
    from sre_agent.infrastructure.playbook_loader import load_playbooks

    pbs = tuple(load_playbooks().playbooks if playbooks is None else playbooks)
    alarm = fe.fixture_alarm(fx)
    settings = timeline_settings(dag=dag, axis=axis, max_workers=max_workers, activity=activity)

    def deep(job: Any) -> DiagnosisResult:
        return DiagnosisResult(answer="", tool_calls=[], tool_outputs=[], total_tokens=0,
                               total_cost=0.0)

    original = dict(playbook_steps.STEP_LIBRARY)
    with tempfile.TemporaryDirectory(prefix="pb-timeline-") as tmp:
        clock = _Clock()
        caller = _DelayedCaller(fx, clock, tool_delay, batch_concurrency)
        llm = _DelayedLLM(clock, llm_delay)
        d = InvestigationDispatcher(
            settings, diagnose_fn=deep, briefing_fn=build_briefing,
            audit_path=Path(tmp) / "audit.jsonl",
            playbook=PlaybookWiring(playbooks=pbs, polestar=caller, apm=caller, llm=llm),
        )
        now = time.time()
        job = InvestigationJob(investigation_id=f"tl-{name}", kind="alarm", status="accepted",
                               created_at=now, updated_at=now, payload=alarm)
        if activity:
            job.activity = ActivityLog(masker=mask_activity_text)
        playbook_steps.STEP_LIBRARY.update(_timed_library(original, clock))
        try:
            clock.t0 = time.monotonic()
            d(job)
            d.wait_workers(600)
            total = round(clock.now(), 3)
        finally:
            playbook_steps.STEP_LIBRARY.clear()
            playbook_steps.STEP_LIBRARY.update(original)
    briefing = job.briefing if isinstance(job.briefing, Mapping) else {}
    pb_id = (briefing.get("playbook") or {}).get("id")
    # 액션 → 스텝 ID(현재 플레이북에는 중복 액션이 없다 — plans/200 §1.3)
    step_of = {st.action: st.id for pb in pbs if pb.id == pb_id for st in pb.steps}
    for span in clock.spans:
        if span.step is not None:
            span.step = step_of.get(span.step, span.step)
    return Timeline(
        name=name, playbook_id=pb_id, status=job.status,
        total_s=total, instrumentation=dict(job.instrumentation or {}), spans=list(clock.spans),
        tool_calls=len(caller.calls), unrecorded=len(caller.unrecorded), llm_calls=llm.calls,
    )


def render(tl: Timeline, *, opts: str = "") -> str:
    """시간축 → 텍스트 표."""
    ins = tl.instrumentation
    lines = [
        f"[{tl.name}] playbook={tl.playbook_id} status={tl.status} {opts}".rstrip(),
        f"  총 벽시계 {tl.total_s:.2f}s · 수집(collect_s) {ins.get('collect_s')}s"
        f" · 해석 대기(opinion_wait_s) {ins.get('opinion_wait_s')}"
        f" · 소견·종합(synthesis_s) {ins.get('synthesis_s')}",
        f"  단계 벽시계(stage_wall_s) {ins.get('stage_wall_s')}",
        f"  도구 호출 {tl.tool_calls} · 도구 배치 {len(tl.of('tool'))} · 미녹화 {tl.unrecorded}"
        f" · LLM 호출 {tl.llm_calls}",
        "  -- 스텝 --",
    ]
    lines += [f"  {s.start:7.2f}-{s.end:7.2f}  {s.step or '-':5s} {s.label}" for s in tl.of("step")]
    lines.append("  -- 도구 배치 --")
    lines += [f"  {s.start:7.2f}-{s.end:7.2f}  {s.step or '-':5s} {s.label[:90]}"
              for s in tl.of("tool")]
    lines.append("  -- LLM --")
    lines += [f"  {s.start:7.2f}-{s.end:7.2f}  {s.label}" for s in tl.of("llm")]
    return "\n".join(lines)


def _on_off(v: str) -> bool:
    return v == "on"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="playbook_timeline",
                                description="플레이북 실행 시간축(오프라인 · 실 LLM·MCP 0)")
    p.add_argument("fixtures", nargs="*", help="픽스처 이름(파일 stem) — 여럿 가능")
    p.add_argument("--all", action="store_true", help="픽스처 전부")
    p.add_argument("--tool-delay", type=float, default=0.5, help="배치당 도구 지연 초(기본 0.5)")
    p.add_argument("--llm-delay", type=float, default=1.0, help="소견기 호출당 지연 초(기본 1.0)")
    p.add_argument("--batch-concurrency", type=int, default=4,
                   help="배치 안 동시 처리 수(기본 4 — 지연 = tool_delay × ceil(n/이 값))")
    p.add_argument("--dag", choices=("on", "off"), default="off",
                   help="sre_playbook_dag_scheduling_enabled")
    p.add_argument("--axis", choices=("on", "off"), default="off",
                   help="sre_playbook_axis_concurrency_enabled")
    p.add_argument("--max-workers", type=int, default=None, help="sre_playbook_max_workers")
    p.add_argument("--activity", choices=("on", "off"), default="off",
                   help="실행 기록(단계 끝 해석) — sre_activity_log_enabled")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 진입 — 종료 코드(미녹화가 있으면 1)."""
    parser = build_parser()
    args = parser.parse_args(argv)
    fixtures = load_all_fixtures()
    names = sorted(fixtures) if args.all else list(args.fixtures)
    if not names:
        parser.error("픽스처 이름 또는 --all이 필요하다")
    unknown = [n for n in names if n not in fixtures]
    if unknown:
        parser.error(f"없는 픽스처: {', '.join(unknown)} (있는 것: {', '.join(sorted(fixtures))})")
    opts = (f"dag={args.dag} axis={args.axis} max_workers={args.max_workers} "
            f"activity={args.activity} tool_delay={args.tool_delay} llm_delay={args.llm_delay} "
            f"batch_concurrency={args.batch_concurrency}")
    rc = 0
    for name in names:
        tl = run_timeline(
            name, fixtures[name], tool_delay=args.tool_delay, llm_delay=args.llm_delay,
            batch_concurrency=args.batch_concurrency, dag=_on_off(args.dag),
            axis=_on_off(args.axis), max_workers=args.max_workers,
            activity=_on_off(args.activity),
        )
        print(render(tl, opts=opts))
        rc = rc or (1 if tl.unrecorded else 0)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
