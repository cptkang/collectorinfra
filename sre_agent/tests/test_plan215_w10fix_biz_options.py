"""plans/215 W10 교정 — 업무 서비스 맥락 소비 옵션 3종(`BizServiceOptions` · 전부 기본 off).

① `SRE_BIZ_COMPARE_WAIT_S` — S4 비교 지표 호출을 본 판정과 다른 배치로 동시에 내고 상한까지만
기다린다(넘으면 `biz_compare` 행만 결손 + 사유 · 본 판정 불변 · 늦은 배치는 기록·적재 0) ·
기동 경고(S4 스텝 `timeout_s` 이상).
② `SRE_S0_BIZ_SERVICE_AUTHORITATIVE` — 맥락은 있고 APM 구성원이 비면 업무명 해석·업무 검색 생략.
③ `SRE_S0_BIZ_PEERS_WITHOUT_RESOLVE` — 대상 확정 실패여도 구성원을 범위 비교 호출에만(대상 비승격).
off(미입력·false)면 호출·순서·사유·결과가 옵션을 넘기지 않은 실행과 같다. 녹화 픽스처·가짜
콜러블만 쓴다(실 MCP·LLM 호출 0).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter
from datetime import datetime
from typing import Any

import pytest
from sre_agent.application import investigation_dispatcher as disp_mod
from sre_agent.application.evidence_prefetch import TOOL_METRIC
from sre_agent.application.playbook_runner import run_playbook
from sre_agent.application.playbook_steps import (
    STEP_ABORTED,
    BizServiceOptions,
    RunScope,
    RunWindows,
    StepContext,
)
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load
from test_plan187_w4_dispatch import alarm_of, build, make_settings, run_job
from test_plan215_w10_biz_service import _alarm, _AnyServer, _biz

#: was.latency S4 인자 `kinds`(YAML).
KINDS = ["cpu", "memory", "filesystem"]


def _run(alarm: dict[str, Any], caller: FakeCaller, **kw: Any):  # noqa: ANN202
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    return run_playbook(alarm, pb, matched_by="t", polestar=caller, apm=caller,
                        polestar_source="polestar", max_workers=1, for_each_max=1, **kw)


def _plain(run) -> dict[str, Any]:  # noqa: ANN001
    block = run.to_briefing_dict()
    for s in block["sections"]:
        s.pop("elapsed_ms", None)
    block.pop("elapsed_ms", None)
    block.pop("timings", None)
    return block


def _sec(run, sid: str) -> dict[str, Any]:  # noqa: ANN001
    sec = run.section(sid)
    assert sec is not None
    out = sec.to_dict()
    out.pop("elapsed_ms", None)
    return out


class _SlowCompare(_AnyServer):
    """비교 구성원(SVR*) 지표만 `delay`초 늦게 돌려준다 — 배치 단위로 잠든다."""

    def __init__(self, fx: dict, delay: float) -> None:
        super().__init__(fx)
        self.delay = delay
        self.side_done = threading.Event()

    def __call__(self, calls):
        side = any(t == TOOL_METRIC and str(a.get("server_name", "")).startswith("SVR")
                   for t, a in calls)
        if side and self.delay:
            time.sleep(self.delay)
        try:
            return super().__call__(calls)
        finally:
            if side:
                self.side_done.set()


class _Unresolved(_AnyServer):
    """알람 인스턴스 정합 0건(대상 확정 실패) · 구성원 app_health는 B1 녹화 응답으로 돌려준다."""

    def __init__(self, fx: dict) -> None:
        super().__init__(fx)
        self.overrides = {"apm_instance_map": {"rows": []}}

    def lookup(self, tool: str, args: dict) -> dict | None:
        if tool == "apm_app_health" and str(args.get("instance_name", "")).startswith("C"):
            return super().lookup(tool, {**args, "instance_name": "B1"})
        return super().lookup(tool, args)


# ── 기본값 · off 비트 동일 ───────────────────────────────────────────────────


def test_defaults_are_off() -> None:
    s = AgentSettings(_env_file=None)
    assert s.sre_biz_compare_wait_s is None
    assert s.sre_s0_biz_service_authoritative is False
    assert s.sre_s0_biz_peers_without_resolve is False
    windows = RunWindows(alarm_time="2026-10-19T10:00:00", reference_time="2026-10-19T10:10:00",
                         lookback_minutes=40, incident_start="2026-10-19T09:30:00",
                         incident_end="2026-10-19T10:10:00")
    ctx = StepContext(payload={}, windows=windows, scope=RunScope(), args={}, targets=[],
                      polestar=None, apm=None, now=datetime.now, prior={})
    assert ctx.biz_options == BizServiceOptions()


@pytest.mark.parametrize("case", ["peers", "no_apm", "unresolved", "no_key"])
def test_off_equals_omitted(case: str) -> None:
    """옵션 기본값(off)을 명시해 넘긴 실행 = 넘기지 않은 실행(호출·순서·섹션·사유 전부)."""
    fx = _load("latency_sql")
    biz = {"peers": _biz(), "no_apm": _biz(n_apm=0), "unresolved": _biz(),
           "no_key": None}[case]
    cls = _Unresolved if case == "unresolved" else _AnyServer
    a, b = cls(fx), cls(fx)
    ra = _run(_alarm(biz), a)
    rb = _run(_alarm(biz), b, biz_options=BizServiceOptions())
    assert a.calls == b.calls
    assert _plain(ra) == _plain(rb)
    assert ra.biz_service == rb.biz_service


def test_dispatcher_passes_options_only_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict] = []
    real = disp_mod.run_playbook

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        seen.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(disp_mod, "run_playbook", spy)
    fx = _load("latency_sql")
    run_job(build(make_settings(), FakeCaller(fx, strict=False)), alarm_of("latency_sql"))
    run_job(build(make_settings(sre_biz_compare_wait_s=5.0,
                                sre_s0_biz_service_authoritative=True,
                                sre_s0_biz_peers_without_resolve=True),
                  FakeCaller(fx, strict=False)), alarm_of("latency_sql"), iid="inv-2")
    assert len(seen) == 2
    assert "biz_options" not in seen[0]  # off — 새 인자 자체를 싣지 않는다(docs/18 2026-11-05)
    assert seen[1]["biz_options"] == BizServiceOptions(5.0, True, True)


# ── ① S4 비교 호출 분리 ─────────────────────────────────────────────────────


def test_s4_split_in_time_equals_same_batch() -> None:
    fx = _load("latency_sql")
    off_caller, on_caller = _AnyServer(fx, hot="SVR2"), _AnyServer(fx, hot="SVR2")
    off = _run(_alarm(_biz()), off_caller)
    on = _run(_alarm(_biz()), on_caller, biz_options=BizServiceOptions(compare_wait_s=10.0))
    assert _sec(on, "S4") == _sec(off, "S4")  # 표·사유·biz_compare·tools·queries 전부
    assert Counter(json.dumps(c, sort_keys=True) for c in on_caller.calls) == Counter(
        json.dumps(c, sort_keys=True) for c in off_caller.calls)
    assert on.biz_service == off.biz_service


def test_s4_split_timeout_marks_only_compare_missing() -> None:
    fx = _load("latency_sql")
    base = _run(_alarm(_biz()), _AnyServer(fx))  # off · 같은 배치
    caller = _SlowCompare(fx, delay=1.5)
    t0 = time.monotonic()
    run = _run(_alarm(_biz()), caller, biz_options=BizServiceOptions(compare_wait_s=0.2))
    assert time.monotonic() - t0 < 1.4  # 본 판정·이후 스텝이 늦은 비교를 기다리지 않는다
    s4, b4 = run.section("S4"), base.section("S4")
    assert s4 is not None and b4 is not None
    assert (s4.status, s4.verdict, s4.table, s4.reason, s4.missing) == (
        b4.status, b4.verdict, b4.table, b4.reason, b4.missing)
    assert {k: v for k, v in s4.data.items() if k != "biz_compare"} == {
        k: v for k, v in b4.data.items() if k != "biz_compare"}
    rows = s4.data["biz_compare"]
    assert [r["server_name"] for r in rows] == ["SVR1", "SVR2", "SVR3"]
    for r in rows:
        assert r == {"db_id": "polestar", "server_name": r["server_name"], "status": "missing",
                     "verdict": None, "anomalous": [], "missing": KINDS,
                     "reason": "비교 수집 0.2s 초과"}
    # 늦은 비교 배치는 섹션 호출 목록에 싣지 않는다(본 판정 호출만)
    assert s4.queries == [q for q in b4.queries if "SVR" not in json.dumps(q, ensure_ascii=False)]
    brief = run.biz_service
    assert brief is not None and {r["status"] for r in brief["compared_sms"]} == {"missing"}
    assert caller.side_done.wait(5)


# ── ① 단위: call_with_side ──────────────────────────────────────────────────


class _Batch:
    """`side*` 도구가 든 배치만 늦추거나 예외를 낸다."""

    def __init__(self, *, delay: float = 0.0, exc: Exception | None = None) -> None:
        self.delay = delay
        self.exc = exc
        self.side_done = threading.Event()

    def __call__(self, calls):
        side = any(t.startswith("side") for t, _ in calls)
        try:
            if side and self.delay:
                time.sleep(self.delay)
            if side and self.exc is not None:
                raise self.exc
            return [json.dumps({"rows": [{"tool": t}]}) for t, _ in calls]
        finally:
            if side:
                self.side_done.set()


MAIN = [("main_a", {"x": 1}), ("main_b", {"x": 2})]
SIDE = [("side_a", {"y": 1})]


def _ctx(caller: Any, log: ActivityLog | None = None) -> StepContext:
    windows = RunWindows(alarm_time="2026-10-19T10:00:00", reference_time="2026-10-19T10:10:00",
                         lookback_minutes=40, incident_start="2026-10-19T09:30:00",
                         incident_end="2026-10-19T10:10:00")
    ctx = StepContext(payload={}, windows=windows, scope=RunScope(), args={}, targets=[],
                      polestar=caller, apm=None, now=datetime.now, prior={})
    ctx.activity = log
    ctx.step_id = "S4"
    return ctx


def _events(log: ActivityLog) -> list[tuple[str, str, Any]]:
    return [(e["kind"], e["tool"], e.get("status")) for e in log.since(0)["events"]]


def test_side_in_time_stored_after_main() -> None:
    log = ActivityLog(masker=mask_activity_text)
    ctx = _ctx(_Batch(), log)
    main, side = ctx.call_with_side("polestar", MAIN, SIDE, 5.0, "초과")
    assert [r.tool for r in main] == ["main_a", "main_b"] and all(r.ok for r in main)
    assert side is not None and [r.tool for r in side] == ["side_a"] and side[0].ok
    assert ctx.calls == [(t, a) for t, a in MAIN + SIDE]  # 같은 배치와 같은 적재 순서
    assert _events(log) == [
        ("tool_start", "main_a", None), ("tool_start", "main_b", None),
        ("tool_start", "side_a", None),
        ("tool_end", "main_a", "success"), ("tool_end", "main_b", "success"),
        ("tool_end", "side_a", "success"),
    ]
    assert (ctx.requested, ctx.completed, ctx.inflight) == (3, 3, [])


def test_side_timeout_late_return_not_recorded() -> None:
    log = ActivityLog(masker=mask_activity_text)
    batch = _Batch(delay=0.8)
    ctx = _ctx(batch, log)
    t0 = time.monotonic()
    main, side = ctx.call_with_side("polestar", MAIN, SIDE, 0.1, "비교 수집 0.1s 초과")
    assert time.monotonic() - t0 < 0.6
    assert all(r.ok for r in main) and side is None
    ends = [e for e in log.since(0)["events"] if e["kind"] == "tool_end" and e["tool"] == "side_a"]
    assert len(ends) == 1 and ends[0]["status"] == "error"
    assert "비교 수집 0.1s 초과" in ends[0]["excerpt"]  # 「진행 중」이 남지 않는다
    before = _events(log)
    assert batch.side_done.wait(5)
    time.sleep(0.05)
    # 늦게 돌아온 배치 — 기록·적재 0, 계수만 갱신(대기 목록 비움)
    assert _events(log) == before
    assert ctx.calls == [(t, a) for t, a in MAIN] and "side_a" not in ctx.tools
    assert len(ctx.outputs) == 2
    assert (ctx.requested, ctx.completed, ctx.inflight) == (3, 3, [])


def test_side_exception_does_not_block_main() -> None:
    log = ActivityLog(masker=mask_activity_text)
    ctx = _ctx(_Batch(exc=RuntimeError("boom")), log)
    main, side = ctx.call_with_side("polestar", MAIN, SIDE, 5.0, "초과")
    assert all(r.ok for r in main)
    assert side is not None and side[0].error == "RuntimeError: boom"
    assert ("tool_end", "side_a", "error") in _events(log)


def test_side_cancelled_and_unconfigured() -> None:
    ctx = _ctx(_Batch())
    ctx.cancel.set()
    main, side = ctx.call_with_side("polestar", MAIN, SIDE, 5.0, "초과")
    assert {r.error for r in main} | {r.error for r in side or []} == {STEP_ABORTED}
    none = _ctx(None)
    main, side = none.call_with_side("polestar", MAIN, SIDE, 5.0, "초과")
    assert {r.error for r in main} | {r.error for r in side or []} == {"폴스타 MCP 미구성"}


# ── ① 기동 경고 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("wait", "warned"), [(None, False), (30.0, False), (90.0, True)])
def test_startup_warning_when_wait_reaches_s4_timeout(
    caplog: pytest.LogCaptureFixture, wait: float | None, warned: bool
) -> None:
    caplog.set_level(logging.INFO, logger=mcp_service.__name__)
    wiring = mcp_service._default_playbook_wiring(
        make_settings(sre_biz_compare_wait_s=wait), lambda: None
    )
    assert wiring is not None
    warns = [r.getMessage() for r in caplog.records
             if r.levelno == logging.WARNING and "SRE_BIZ_COMPARE_WAIT_S" in r.getMessage()]
    assert len(warns) == (1 if warned else 0)
    if warned:
        # S4 스텝은 YAML timeout_s가 없어 실행기 기본값(90s)이 상한이다
        assert "SRE_BIZ_COMPARE_WAIT_S 90s ≥ S4 스텝 timeout_s(" in warns[0]
        assert "was.latency 90s" in warns[0]


# ── ② S0 업무 서비스 맥락 정본 ───────────────────────────────────────────────


def test_authoritative_skips_business_lookup_when_no_apm_members() -> None:
    fx = _load("latency_sql")
    off_caller, on_caller = _AnyServer(fx), _AnyServer(fx)
    _run(_alarm(_biz(n_apm=0)), off_caller)
    on = _run(_alarm(_biz(n_apm=0)), on_caller,
              biz_options=BizServiceOptions(s0_authoritative=True))
    off_maps = [a for t, a in off_caller.calls if t == "apm_instance_map"]
    on_maps = [a for t, a in on_caller.calls if t == "apm_instance_map"]
    assert any(a.get("business") == "계좌조회" for a in off_maps)  # off — 종전 업무 검색
    assert not any("business" in a for a in on_maps)  # ③ 생략
    assert any(a.get("hostname") == "wasdemo01" for a in on_maps)  # 호스트 조회는 그대로
    s0 = on.section("S0")
    assert s0 is not None
    assert "업무 서비스 『인터넷뱅킹』 구성원 기준 — 게이트웨이 업무 검색 생략" in (s0.reason or "")
    assert (s0.data["business"], s0.data["business_source"]) == ("인터넷뱅킹", "meta.biz_service")


@pytest.mark.parametrize("biz", [None, "peers"])
def test_authoritative_noop_without_key_or_with_members(biz: str | None) -> None:
    fx = _load("latency_sql")
    payload = _alarm(_biz() if biz else None)
    a, b = _AnyServer(fx), _AnyServer(fx)
    ra = _run(payload, a)
    rb = _run(payload, b, biz_options=BizServiceOptions(s0_authoritative=True))
    assert a.calls == b.calls and _plain(ra) == _plain(rb)


# ── ③ 대상 확정 실패 · 구성원 범위 비교 ─────────────────────────────────────


def test_peers_without_resolve_compares_scope_only() -> None:
    fx = _load("latency_sql")
    off_caller, on_caller = _Unresolved(fx), _Unresolved(fx)
    off = _run(_alarm(_biz()), off_caller)
    on = _run(_alarm(_biz()), on_caller,
              biz_options=BizServiceOptions(s0_peers_without_resolve=True))
    s0_off, s0 = off.section("S0"), on.section("S0")
    assert s0_off is not None and s0 is not None
    assert "대상 확정 실패(0건) — 이후 APM 스텝은 알람 인스턴스 단독" in (s0_off.reason or "")
    assert "대상 확정 실패(0건) — 업무 서비스 구성원 2개로 범위만 비교" in (s0.reason or "")
    assert "이후 APM 스텝은 알람 인스턴스 단독" not in (s0.reason or "")
    assert s0.data["resolution"] == "failed"
    assert [i["instance"] for i in s0.data["instances"]] == ["A1", "C1", "C2"]
    assert [i["source_id"] for i in s0.data["instances"]] == [None, "default", "default"]
    # 대상 비승격 — 진단 대상은 알람 인스턴스 하나 그대로
    assert [t["instance"] for t in on.scope["targets"]] == ["A1"]
    assert on.scope["targets"] == off.scope["targets"]
    # 추가 호출은 구성원 범위 비교 app_health 2건뿐(이후 스텝 호출은 off와 같다)
    extra = Counter(json.dumps(c, sort_keys=True) for c in on_caller.calls)
    extra.subtract(Counter(json.dumps(c, sort_keys=True) for c in off_caller.calls))
    added = [json.loads(k) for k, n in extra.items() for _ in range(n) if n > 0]
    assert all(n >= 0 for n in extra.values())
    assert sorted((t, a["instance_name"]) for t, a in added) == [
        ("apm_app_health", "C1"), ("apm_app_health", "C2")]
    for sid in [s.step for s in off.sections if s.step != "S0"]:
        assert _sec(on, sid) == _sec(off, sid)
    brief = on.biz_service
    assert brief is not None
    assert {r["instance_name"] for r in brief["compared_apm"]} == {"C1", "C2"}


def test_peers_without_resolve_keeps_compare_cap() -> None:
    fx = _load("latency_sql")
    run = _run(_alarm(_biz(n_apm=12)), _Unresolved(fx),
               biz_options=BizServiceOptions(s0_peers_without_resolve=True))
    s0 = run.section("S0")
    assert s0 is not None and len(s0.data["instances"]) == 8  # max_compare 8 그대로
    assert "업무 서비스 구성원 12개로 범위만 비교" in (s0.reason or "")
    assert "범위 비교 상한 8" in (s0.reason or "")
    assert [t["instance"] for t in run.scope["targets"]] == ["A1"]


def test_peers_without_resolve_noop_when_resolved() -> None:
    fx = _load("latency_sql")
    a, b = _AnyServer(fx), _AnyServer(fx)
    ra = _run(_alarm(_biz()), a)
    rb = _run(_alarm(_biz()), b, biz_options=BizServiceOptions(s0_peers_without_resolve=True))
    assert a.calls == b.calls and _plain(ra) == _plain(rb)
