"""plans/187 W20a — 조사 흐름 배선: `step_skip` · 단계 시작·끝 문장 · 단계 끝 AI 해석(G-47).

- runner: `when` 거짓·예산 소진 자리표시마다 `step_skip` 1건(사유 문장 · 조건식 원문 없음) + 통지
- dispatcher(실행 기록 on): `because`·`finding`·`phrase` · 부분 브리핑 `finding`·`opinion`
- 단계 끝 해석: 종결 전 부분 브리핑에 opinion ok · 가드 탈락·타임아웃은 none+reason(finding 유지)
  · 알람당 LLM 호출 = 소견 대상 단계 + 종합 1(재호출 0) · 앞 단계 결과 블록은 이 경로에만
- 실행 기록 off → 새 키·새 이벤트 0 · 종결 일괄 경로(최종 응답 동일)
가짜 LLM만 쓴다(실 LLM 0).
"""

from __future__ import annotations

import threading
import time
from typing import Any

from sre_agent.application.investigation_dispatcher import PLAYBOOK_TIMING_KEYS
from sre_agent.application.playbook_narrator import (
    NarratorLimits,
    PlaybookNarrator,
    StepNarration,
    StepOpinionStream,
)
from sre_agent.application.playbook_prompts import render_step_messages
from sre_agent.application.playbook_runner import run_playbook
from sre_agent.application.playbook_steps import StepResult
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from test_plan187_w2_runner import FakeCaller, _load, _mini_playbook
from test_plan187_w4_dispatch import (
    PLAYBOOKS,
    FakeLLM,
    _comparable,
    alarm_of,
    build,
    make_job,
    make_settings,
)

NEW_KEYS = {"finding", "because", "phrase"}


def _log() -> ActivityLog:
    return ActivityLog(masker=mask_activity_text)


# ── runner: 건너뜀 ──────────────────────────────────────────────────────────


def test_when_false_emits_one_step_skip_with_sentence_and_notifies() -> None:
    lib = {
        "apm.golden_signals": lambda ctx: StepResult(status="ok", verdict="normal"),
        "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok"),
    }
    log, seen = _log(), []
    run = run_playbook(_load("normal")["alarm"], _mini_playbook(), matched_by="t", polestar=None,
                       apm=None, steps=lib, activity=log,
                       on_skip=lambda res, because: seen.append((res.step, because)))
    assert run.section("S3").reason.startswith("when_false")  # 원문은 기존 reason에만
    skips = [e for e in log.since(0)["events"] if e["kind"] == "step_skip"]
    assert len(skips) == 1
    ev = skips[0]
    assert ev["step"] == "S3" and ev["status"] == "skipped" and ev["engine"] == "playbook"
    assert ev["label"] == "S3 느린 트랜잭션 Top 시간 분해"
    assert ev["because"] == "WAS 골든 시그널 결과가 정상이라 생략"
    assert "!=" not in ev["because"] and "when_false" not in ev["because"]
    assert seen == [("S3", ev["because"])]


def test_budget_skip_emits_budget_sentence() -> None:
    t = {"now": 0.0}

    def slow(ctx):  # noqa: ANN001
        t["now"] += 200.0
        return StepResult(status="ok", verdict="degraded")

    lib = {"apm.golden_signals": slow, "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok")}
    log = _log()
    run_playbook(_load("normal")["alarm"], _mini_playbook(), matched_by="t", polestar=None,
                 apm=None, steps=lib, clock=lambda: t["now"], budget_s=120.0, activity=log)
    skips = [e for e in log.since(0)["events"] if e["kind"] == "step_skip"]
    assert [(e["step"], e["because"]) for e in skips] == [("S3", "시간 예산 소진으로 생략")]


def test_without_activity_runner_records_nothing() -> None:
    lib = {
        "apm.golden_signals": lambda ctx: StepResult(status="ok", verdict="normal"),
        "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok"),
    }
    run = run_playbook(_load("normal")["alarm"], _mini_playbook(), matched_by="t", polestar=None,
                       apm=None, steps=lib)
    assert run.section("S3").status == "skipped"


# ── 프롬프트 ────────────────────────────────────────────────────────────────


def test_prior_findings_block_only_when_given() -> None:
    sec = {"step": "S2", "title": "t", "verdict": "normal", "table": {"columns": [], "rows": []}}
    plain = render_step_messages(section=sec, focus="f", max_sentences=3, readings={})
    none = render_step_messages(section=sec, focus="f", max_sentences=3, readings={},
                                prior_findings=None)
    assert plain == none
    assert "[앞 단계 결과]" not in plain[1]["content"] and "추측" not in plain[0]["content"]
    step = render_step_messages(section=sec, focus="f", max_sentences=3, readings={},
                                prior_findings=["S0 대상 확정: 이상 · 표 3행"])
    assert "[앞 단계 결과]\n- S0 대상 확정: 이상 · 표 3행" in step[1]["content"]
    assert "뒤 단계 결과를 추측하지 마라" in step[0]["content"]
    assert "원인 순위" in step[0]["content"]
    empty = render_step_messages(section=sec, focus="f", max_sentences=3, readings={},
                                 prior_findings=[])
    assert "[앞 단계 결과]\n(없음)" in empty[1]["content"]


# ── 단계 끝 해석 실행기 ─────────────────────────────────────────────────────


def _request(step: str = "S1", verdict: str | None = "degraded") -> StepNarration:
    return StepNarration(
        step=step,
        section={"step": step, "title": "t", "verdict": verdict,
                 "table": {"columns": ["p95_ms"], "rows": [[6200]]}},
        focus="p95", max_sentences=3, readings={}, prior_findings=(),
    )


def test_stream_guard_drop_is_none_with_reason() -> None:
    nar = PlaybookNarrator(lambda m: "응답이 123456789ms로 폭증했다.")
    stream = StepOpinionStream(nar)
    got: list = []
    stream.submit(_request(), got.append)
    out = stream.drain()
    assert out["S1"].text == "" and out["S1"].dropped_reason
    assert len(got) == 1 and got[0].dropped_reason == out["S1"].dropped_reason


def test_stream_timeout_does_not_block_and_reports_reason() -> None:
    release = threading.Event()

    def slow(messages):  # noqa: ANN001
        release.wait(2)
        return "p95 6200ms로 지연이 있다."

    nar = PlaybookNarrator(slow, NarratorLimits(step_timeout_s=0.05))
    stream = StepOpinionStream(nar)
    got: list = []
    t0 = time.monotonic()
    stream.submit(_request(), got.append)
    assert time.monotonic() - t0 < 0.05  # 제출은 막지 않는다
    out = stream.drain()
    release.set()
    assert "타임아웃" in out["S1"].dropped_reason and got and "타임아웃" in got[0].dropped_reason


def test_stream_same_step_once_and_no_notify_after_close() -> None:
    llm = FakeLLM("p95 6200ms로 지연이 있다.")
    stream = StepOpinionStream(PlaybookNarrator(llm))
    assert stream.submit(_request()) is True
    assert stream.submit(_request()) is False
    out = stream.drain()
    assert out["S1"].text and llm.calls == 1
    got: list = []
    assert stream.submit(_request("S2"), got.append) is False and got == []


# ── dispatcher 종단 ─────────────────────────────────────────────────────────


def _targets(job: Any) -> list[str]:
    pb = next(p for p in PLAYBOOKS if p.id == job.briefing["playbook"]["id"])
    narrate = {st.id for st in pb.steps if st.narrate is not None and st.action != "synthesize"}
    return [s["step"] for s in job.briefing["playbook"]["sections"]
            if s["step"] in narrate and s["status"] == "ok"]


class _SnapCaller:
    """조회마다 부분 브리핑을 떠 둔다.

    `wait_ok_for`가 있으면 프로세스 조회 전에 그 단계 해석이 붙을 때까지 기다린다.
    """

    def __init__(self, fx: dict, wait_ok_for: str | None = None) -> None:
        self.base = FakeCaller(fx)
        self.job: Any = None
        self.snaps: list[dict] = []
        self.wait_ok_for = wait_ok_for

    def _opinion(self, sid: str) -> dict | None:
        partial = self.job.partial_briefing or {}
        for s in (partial.get("playbook") or {}).get("sections") or []:
            if s.get("step") == sid:
                return s.get("opinion")
        return None

    def __call__(self, calls):  # noqa: ANN001
        if self.job is not None:
            if self.wait_ok_for and any(t == "polestar_process_snapshot" for t, _ in calls):
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and (
                    (self._opinion(self.wait_ok_for) or {}).get("status") == "pending"
                ):
                    time.sleep(0.01)
            if self.job.partial_briefing:
                self.snaps.append(self.job.partial_briefing)
        return self.base(calls)


def _run_on(llm: Any, wait_ok_for: str | None = None) -> tuple[Any, _SnapCaller]:
    caller = _SnapCaller(_load("latency_sql"), wait_ok_for)
    job = make_job(alarm_of("latency_sql"), "inv-w20")
    job.activity = _log()
    caller.job = job
    d = build(make_settings(), caller, llm=llm)
    d(job)
    d.wait_workers(30)
    return job, caller


def test_activity_on_events_carry_because_finding_phrase() -> None:
    job, _ = _run_on(FakeLLM())
    events = job.activity.since(0)["events"]
    starts = [e for e in events if e["kind"] == "step_start"]
    ends = [e for e in events if e["kind"] == "step_end"]
    assert starts and all(e.get("because") for e in starts)
    assert starts[0]["because"].startswith("알람 「")  # 첫 단계는 알람 요약
    assert ends and all(e.get("finding") for e in ends)
    tools = [e for e in events if e["kind"] in ("tool_start", "tool_end")]
    assert tools and all(e.get("phrase") for e in tools)  # 플레이북 도구는 모두 사전 안
    when_steps = [e for e in starts if "→" in e["because"]]
    assert when_steps  # was.latency S3(when S1.verdict != 'normal')
    assert all("!=" not in e["because"] for e in when_steps)


def test_step_end_opinion_lands_in_partial_before_finalize_and_no_recall() -> None:
    llm = FakeLLM()
    job, caller = _run_on(llm, wait_ok_for="S0")
    ok_before_end = [
        s for snap in caller.snaps for s in snap["playbook"]["sections"]
        if (s.get("opinion") or {}).get("status") == "ok"
    ]
    assert ok_before_end, "수집 중 부분 브리핑에 단계 해석(ok)이 붙어야 한다"
    view = ok_before_end[0]["opinion"]
    assert view["basis"] == "이 단계까지의 근거" and view["text"]
    assert all("finding" in s for snap in caller.snaps for s in snap["playbook"]["sections"]
               if s.get("status") != "skipped")
    targets = _targets(job)
    assert llm.calls == len(targets) + 1  # 소견 대상 단계 + 종합 1 · 재호출 0
    assert job.briefing["playbook"]["timings"]["llm_calls"] == llm.calls
    for s in job.briefing["playbook"]["sections"]:
        if s["step"] in targets:
            assert s["opinion"] == "수집된 표의 값을 확인했다." or s["opinion"].endswith(
                "수집된 표의 값을 확인했다.")


def test_guard_drop_keeps_finding_and_reports_none() -> None:
    llm = FakeLLM("응답이 123456789ms로 폭증했다.")
    job, caller = _run_on(llm, wait_ok_for="S0")
    nones = [
        s for snap in caller.snaps for s in snap["playbook"]["sections"]
        if (s.get("opinion") or {}).get("status") == "none"
    ]
    assert nones and all(s["opinion"]["reason"] and s.get("finding") for s in nones)
    finals = {s["step"]: s for s in job.briefing["playbook"]["sections"]}
    for sid in _targets(job):
        assert finals[sid]["opinion"] == "" and finals[sid]["opinion_dropped_reason"]
    ends = [e for e in job.activity.since(0)["events"] if e["kind"] == "step_end"]
    assert all(e.get("finding") for e in ends)
    assert llm.calls == len(_targets(job)) + 1


class _RecLLM(FakeLLM):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[list[dict]] = []

    def __call__(self, messages):  # noqa: ANN001
        self.messages.append(messages)
        return super().__call__(messages)


def test_activity_off_is_unchanged_path_and_same_final_response() -> None:
    off_llm, on_llm = _RecLLM(), _RecLLM()
    caller = _SnapCaller(_load("latency_sql"))
    off_job = make_job(alarm_of("latency_sql"), "inv-off")
    caller.job = off_job
    d = build(make_settings(), caller, llm=off_llm)
    d(off_job)
    d.wait_workers(30)
    # 새 키 0 · 건너뜀 항목 0 · 앞 단계 결과 블록 0
    for snap in caller.snaps:
        for s in snap["playbook"]["sections"]:
            assert {"finding", "opinion", "because"}.isdisjoint(s), s
    assert all("[앞 단계 결과]" not in m[-1]["content"] for m in off_llm.messages)
    on_job, _ = _run_on(on_llm)
    assert any("[앞 단계 결과]" in m[-1]["content"] for m in on_llm.messages)
    assert off_llm.calls == on_llm.calls

    def final(job: Any) -> dict:
        s = _comparable(job)
        s.pop("phase", None)
        s.pop("queue_position", None)
        for k in PLAYBOOK_TIMING_KEYS:  # 실행 시간 계측(plans/200 I-0) — 소요 시간만 다르다
            s.pop(k, None)
        pb = s["briefing"]["playbook"]
        pb.pop("timings", None)  # 소요 시간만 다르다
        for sec in pb["sections"]:
            sec.pop("elapsed_ms", None)
        return s

    assert {"finding", "because"}.isdisjoint(off_job.briefing["playbook"]["sections"][0])
    assert final(off_job) == final(on_job)


def test_flow_hooks_without_stream_report_budget_reason_not_unconfigured() -> None:
    """LLM이 있어도 남은 시간 부족으로 실행기를 못 만들면 사유는 「소견기 미구성」이 아니다."""
    job = make_job(alarm_of("latency_sql"), "inv-w20-budget")
    job.activity = _log()
    d = build(make_settings(), _SnapCaller(_load("latency_sql"), None), llm=FakeLLM())
    hooks = d._flow_hooks(job, d._clock() + 1.0)
    assert hooks is not None and hooks.stream is None
    assert hooks.no_stream_reason and "남은 시간" in hooks.no_stream_reason
    ok = d._flow_hooks(job, d._clock() + 600.0)
    assert ok is not None and ok.stream is not None
    ok.stream.close()
