"""plans/187 W2 — 플레이북 실행기 · 스텝 라이브러리 · 잡 단위 도구 결과 캐시.

W0 골든 픽스처의 녹화로 가짜 배치 콜러블을 만든다(조회 규칙: 녹화 args가 호출 args의
부분집합인 것 중 가장 구체적인 항목 — W3 `SpecificCaller`와 같다(W4 후속 ⑤) · 없으면
미녹화 = 실패). 실 MCP·LLM·DB 호출 0.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sre_agent.application.playbook_runner import (
    POLESTAR_BRIDGE_MISSING,
    alarm_facts_from_payload,
    compute_windows,
    plan_stages,
    resolve_polestar_source,
    run_playbook,
)
from sre_agent.application.playbook_steps import (
    POLESTAR_UNRESOLVED,
    STEP_LIBRARY,
    StepResult,
    realtime_badge,
)
from sre_agent.domain.playbook import WindowSpec, parse_playbook, select_playbook
from sre_agent.infrastructure.mcp_tool_client import make_apm_batch_caller, make_batch_caller
from sre_agent.infrastructure.playbook_loader import DEFAULT_PLAYBOOK_DIR, load_playbooks
from sre_agent.infrastructure.tool_result_cache import (
    CacheClosedError,
    ToolResultCache,
    cache_key,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan187"
SCENARIOS = sorted(p.stem for p in FIXTURES.glob("*.json"))
PLAYBOOKS = load_playbooks().playbooks
POLESTAR_TOOLS = ("polestar_",)

#: was.heap 플레이북 원본 dict — W6가 YAML을 만들었으므로 실제 파일을 읽는다(W4 후속 ⑤ · 이름은
#: W3 테스트가 deepcopy해 고쳐 쓰므로 유지).
WAS_HEAP_TEST = yaml.safe_load(
    (DEFAULT_PLAYBOOK_DIR / "was.heap.yaml").read_text(encoding="utf-8")
)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeCaller:
    """녹화 조회 배치 콜러블. 실제 호출(캐시 밖으로 나온 것)을 순서대로 기록한다."""

    def __init__(self, fixture: dict, overrides: dict | None = None, strict: bool = True) -> None:
        self.fixture = fixture
        self.overrides = overrides or {}
        self.strict = strict
        self.calls: list[tuple[str, dict]] = []
        self.unrecorded: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def lookup(self, tool: str, args: dict) -> dict | None:
        """부분집합 일치 녹화 중 가장 구체적인(args 키가 많은) 것 — 같은 수면 먼저 녹화된 것."""
        if tool in self.overrides:
            return self.overrides[tool]
        best = None
        for entry in self.fixture["tool_responses"].get(tool, []):
            if all(args.get(k) == v for k, v in entry["args"].items()):
                if best is None or len(entry["args"]) > len(best["args"]):
                    best = entry
        return None if best is None else best["response"]

    def __call__(self, calls):
        out = []
        for tool, args in calls:
            with self._lock:
                self.calls.append((tool, dict(args)))
            resp = self.lookup(tool, args)
            if resp is None:
                with self._lock:
                    self.unrecorded.append((tool, dict(args)))
                resp = {"error": "미녹화"}
            out.append(json.dumps(resp, ensure_ascii=False))
        return out


def _playbook_for(fx: dict):
    pb, matched_by = select_playbook(alarm_facts_from_payload(fx["alarm"]), PLAYBOOKS)
    return pb, matched_by


def _run(fx: dict, caller: FakeCaller, **kw):
    pb, matched_by = _playbook_for(fx)
    kw.setdefault(
        "polestar_source", None if fx["expected"].get("polestar_unresolved") else "polestar"
    )
    kw.setdefault("max_workers", 1)
    kw.setdefault("for_each_max", 1)
    return run_playbook(fx["alarm"], pb, matched_by=matched_by, polestar=caller, apm=caller, **kw)


# ── 10 픽스처 종단 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_end_to_end_matches_expected(name):
    fx = _load(name)
    exp = fx["expected"]
    caller = FakeCaller(fx)
    run = _run(fx, caller)

    assert caller.unrecorded == []
    assert run.playbook_id == exp["playbook_id"]
    assert [s.step for s in run.sections] == exp["section_order"]
    for s in run.sections:
        if s.action == "synthesize":
            assert s.status == "pending_synthesis"  # S9는 W3 — 실행기는 자리만
        else:
            assert s.status == exp["section_status"][s.step], (s.step, s.status, s.reason)
        if s.status in ("missing", "skipped"):
            assert s.reason, s.step  # 침묵 금지
    assert {s.step: s.missing for s in run.sections if s.missing} == exp.get("missing", {})
    s0 = exp["s0"]
    assert [(t["instance"], t["verdict"]) for t in run.scope["targets"]] == [
        (t["instance"], t["verdict"]) for t in s0["targets"]
    ]
    assert run.scope["alarm_instance"] == s0["alarm_instance"]
    assert run.scope["host"] == s0["host"]
    assert run.scope["polestar_source"] == s0["polestar_source"]
    assert run.scope["polestar_server"] == s0["polestar_server"]
    realtime = {s.step for s in run.sections if any(b.startswith("현재값") for b in s.badges)}
    assert realtime == set(exp["realtime_steps"])
    json.dumps(run.to_briefing_dict(), ensure_ascii=False)
    json.dumps(run.to_dict(), ensure_ascii=False)


def test_case4_selects_heap_playbook_via_event_type_normalization():
    """W4 후속 ① — OUTOFMEMORY가 게이트웨이 매핑 사본으로 `was_heap_pressure` → was.heap."""
    facts = alarm_facts_from_payload(_load("case4_oom_periodic")["alarm"])
    assert "was_heap_pressure" in facts.kinds
    pb, matched_by = select_playbook(facts, PLAYBOOKS)
    assert pb.id == "was.heap" and matched_by.startswith("match:")


def test_fixture_baseline_windows_match_compute_windows():
    for name in SCENARIOS:
        fx = _load(name)
        w = compute_windows(fx["alarm_time"], WindowSpec())
        assert w.reference_time == fx["tool_reference_time"]
        assert (w.incident_start, w.incident_end) == (
            fx["incident_window"]["start"],
            fx["incident_window"]["end"],
        )
        assert w.lookback_minutes == fx["incident_window"]["minutes"]
        assert {b.name: b.start for b in w.baselines} == fx["baseline_windows"]


# ── 호출 수·순서(골든) ───────────────────────────────────────────────────────

LATENCY_SQL_GOLDEN = [
    ("apm_instance_map", {"query": "A1"}),
    ("apm_instance_map", {"hostname": "wasdemo01"}),
    ("apm_instance_map", {"business": "계좌조회"}),
    ("apm_app_health", {"instance_name": "A1", "reference_time": "2026-10-19T10:10:00"}),
    ("apm_app_health", {"instance_name": "A2", "reference_time": "2026-10-19T10:10:00"}),
    ("apm_app_health", {"instance_name": "B1", "reference_time": "2026-10-19T10:10:00"}),
    ("apm_app_health", {"instance_name": "A1", "reference_time": "2026-10-18T10:10:00"}),
    ("apm_app_health", {"instance_name": "A1", "reference_time": "2026-10-12T10:10:00"}),
    ("apm_period_compare", {"baseline_start": "2026-10-18T09:30:00"}),
    ("apm_period_compare", {"baseline_start": "2026-10-12T09:30:00"}),
    ("apm_runtime_health", {"instance_name": "A1"}),
    ("apm_runtime_health", {"metrics": ["proc_cpu", "active_service"]}),
    ("apm_resource_pool", {"instance_name": "A1"}),
    ("apm_slow_transactions", {"instance_name": "A1", "n": 5}),
    ("apm_transaction_profile", {"txid": "770000"}),
    ("polestar_metric_trend", {"kind": "cpu", "periods": 2, "baseline_periods": 24}),
    ("polestar_metric_trend", {"kind": "memory"}),
    ("polestar_metric_trend", {"kind": "filesystem"}),
    ("polestar_incident_alarms", {"server_name": "wasdemo01", "lookback_minutes": 40}),
    ("apm_events", {"instance_name": "A1", "lookback_minutes": 40}),
    ("polestar_process_snapshot", {"hostname": "wasdemo01", "top_n": 10, "sort": "cpu"}),
    ("apm_source_changes", {"instance_name": "A1"}),
    ("apm_change_impact", {"instance_name": "A1"}),
    ("polestar_change_history", {"lookback_minutes": 1440}),
]


def test_call_count_and_order_golden_latency_sql():
    fx = _load("latency_sql")
    caller = FakeCaller(fx)
    run = _run(fx, caller)
    assert len(caller.calls) == len(LATENCY_SQL_GOLDEN) == 24
    for (tool, args), (g_tool, g_sub) in zip(caller.calls, LATENCY_SQL_GOLDEN):
        assert tool == g_tool and all(args.get(k) == v for k, v in g_sub.items()), (
            tool,
            args,
            g_sub,
        )
    # S1이 S0와 같은 app_health를 다시 요청해도 실호출은 1번(잡 캐시) — 요청 기록에는 둘 다 남는다
    requested = Counter(c["tool"] for c in run.tool_calls)
    assert (
        requested["apm_app_health"] == 8
        and Counter(t for t, _ in caller.calls)["apm_app_health"] == 5
    )
    # S3 프로파일 예산 1
    assert sum(1 for t, _ in caller.calls if t == "apm_transaction_profile") == 1
    # 원시 출력(G-25 입력)은 요청 수만큼 · was_signals를 담은 게이트웨이 출력 포함
    assert len(run.tool_outputs) == len(run.tool_calls)
    assert any("was_slow_sql" in o for o in run.tool_outputs)


def test_parallel_execution_same_calls_and_results():
    fx = _load("latency_sql")
    seq = FakeCaller(fx)
    par = FakeCaller(fx)
    r1 = _run(fx, seq, max_workers=1)
    r2 = _run(fx, par, max_workers=4)
    assert Counter(cache_key(t, a) for t, a in seq.calls) == Counter(
        cache_key(t, a) for t, a in par.calls
    )
    assert [(s.step, s.status, s.verdict) for s in r1.sections] == [
        (s.step, s.status, s.verdict) for s in r2.sections
    ]


def test_stages_group_independent_steps():
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    assert [[s.id for s in st] for st in plan_stages(pb)] == [
        ["S0"],
        ["S1", "S2"],
        ["S3", "S4", "S5", "S6", "S7", "S8"],
    ]


def test_for_each_targets_expands_to_all_s0_targets_by_default():
    fx = _load("latency_sql")
    caller = FakeCaller(fx, strict=False)
    run = _run(fx, caller, for_each_max=None)
    assert list(run.section("S1").data["targets"]) == ["A1", "A2", "B1"]
    compared = [a["instance_name"] for t, a in caller.calls if t == "apm_period_compare"]
    assert compared == ["A1", "A1", "A2", "A2", "B1", "B1"]
    # 녹화 밖(동료 인스턴스 기준선)은 결손으로 남는다 — 상태는 부분 성공(ok)
    assert run.section("S1").status == "ok" and "apm_period_compare" in run.section("S1").missing


# ── 부분 실패 · 결손 ─────────────────────────────────────────────────────────


def test_step_exception_is_isolated():
    fx = _load("latency_sql")

    def boom(ctx):
        raise RuntimeError("의도한 실패")

    lib = {**STEP_LIBRARY, "apm.runtime_and_pool": boom}
    run = _run(fx, FakeCaller(fx), steps=lib)
    s2 = run.section("S2")
    assert s2.status == "missing" and "RuntimeError" in s2.reason
    assert all(
        s.status == "ok"
        for s in run.sections
        if s.step in ("S0", "S1", "S3", "S4", "S5", "S6", "S7")
    )


def test_error_envelope_marks_step_missing_and_others_continue():
    fx = _load("latency_sql")
    err = {
        "error": "apm_api_error",
        "reason": "데이터 서버 500",
        "source_kind": "apm_api",
        "source": "jennifer",
        "tool": "apm_slow_transactions",
    }
    run = _run(fx, FakeCaller(fx, overrides={"apm_slow_transactions": err}))
    s3 = run.section("S3")
    assert s3.status == "missing" and s3.missing == ["apm_slow_transactions"]
    assert "apm_api_error: 데이터 서버 500" in s3.reason
    assert run.section("S4").status == "ok" and run.section("S7").status == "ok"


def test_polestar_unresolved_calls_no_polestar_tool_and_says_so():
    fx = _load("polestar_unresolved")
    caller = FakeCaller(fx)
    run = _run(fx, caller)
    assert not [t for t, _ in caller.calls if t.startswith(POLESTAR_TOOLS)]
    for sid in ("S4", "S5", "S6", "S7"):
        assert POLESTAR_UNRESOLVED in run.section(sid).reason, sid
    assert POLESTAR_UNRESOLVED in (run.section("S0").reason or "")
    assert run.scope["polestar_reason"] == POLESTAR_BRIDGE_MISSING
    assert any(POLESTAR_UNRESOLVED in n for n in run.notes)


def test_s0_resolution_failure_degrades_to_alarm_instance():
    fx = _load("latency_sql")
    empty = {"rows": [], "row_count": 0, "source_kind": "apm_api", "tool": "apm_instance_map"}
    caller = FakeCaller(fx, overrides={"apm_instance_map": empty})
    run = _run(fx, caller)
    s0 = run.section("S0")
    assert s0.data["resolution"] == "failed" and "대상 확정 실패" in s0.reason
    assert [t["instance"] for t in run.scope["targets"]] == ["A1"]
    assert [a for t, a in caller.calls if t == "apm_instance_map"] == [{"query": "A1"}]


def test_missing_alarm_time_marks_all_steps_missing():
    fx = _load("normal")
    alarm = json.loads(json.dumps(fx["alarm"]))
    alarm["event"]["alarmTime"] = "bad"
    pb, mb = _playbook_for(fx)
    caller = FakeCaller(fx)
    run = run_playbook(alarm, pb, matched_by=mb, polestar=caller, apm=caller)
    assert caller.calls == []
    assert {s.status for s in run.sections if s.action != "synthesize"} == {"missing"}
    assert all("알람 시각 미상" in s.reason for s in run.sections)


# ── 실시간 배지 · 시간창 경계 ────────────────────────────────────────────────


def test_realtime_badge_uses_capture_time_then_now():
    assert (
        realtime_badge("2026-10-19 10:12:00", "2026-10-19T10:00:00", datetime_now)
        == "현재값 · 알람 후 12분"
    )
    assert (
        realtime_badge("2026-10-19T10:03:00+09:00", "2026-10-19T10:00:00", datetime_now)
        == "현재값 · 알람 후 3분"
    )
    assert realtime_badge(None, "2026-10-19T10:00:00", datetime_now) == "현재값 · 알람 후 45분"


def datetime_now():
    from datetime import datetime

    return datetime(2026, 10, 19, 10, 45)


@pytest.mark.parametrize(
    ("alarm", "start", "prev_day", "prev_week_start", "ref"),
    [
        # 자정 넘김 — 사건창 시작이 전날
        (
            "2026-10-19T00:10:00",
            "2026-10-18T23:40:00",
            "2026-10-17T23:40:00",
            "2026-10-11T23:40:00",
            "2026-10-19T00:20:00",
        ),
        # 사건창 끝이 다음 날
        (
            "2026-10-18T23:55:00",
            "2026-10-18T23:25:00",
            "2026-10-17T23:25:00",
            "2026-10-11T23:25:00",
            "2026-10-19T00:05:00",
        ),
        # 월요일(2026-10-19) → 전주는 지난주 월요일 · 연말 넘김
        (
            "2026-01-03T00:05:00",
            "2026-01-02T23:35:00",
            "2026-01-01T23:35:00",
            "2025-12-26T23:35:00",
            "2026-01-03T00:15:00",
        ),
    ],
)
def test_window_boundaries(alarm, start, prev_day, prev_week_start, ref):
    w = compute_windows(alarm, WindowSpec())
    assert w.incident_start == start and w.reference_time == ref and w.lookback_minutes == 40
    base = {b.name: b for b in w.baselines}
    assert base["prev_day"].start == prev_day
    assert base["prev_week"].start == prev_week_start
    assert base["prev_day"].reference_time == base["prev_day"].end


# ── when 3값 · 예산 · 타임아웃 ───────────────────────────────────────────────


def _mini_playbook():
    return parse_playbook(
        {
            "id": "was.mini",
            "version": 1,
            "match": {"resource_type": "apm.Instance"},
            "steps": [
                {"id": "S1", "action": "apm.golden_signals"},
                {"id": "S3", "action": "apm.slow_tx_breakdown", "when": "S1.verdict != 'normal'"},
                {"id": "S9", "action": "synthesize"},
            ],
            "runbook": {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-17"},
        }
    )


@pytest.mark.parametrize(
    ("verdict", "status", "note"),
    [("degraded", "ok", None), ("normal", "skipped", "when_false"), (None, "ok", "when 판정 불가")],
)
def test_when_three_valued(verdict, status, note):
    lib = {
        "apm.golden_signals": lambda ctx: StepResult(status="ok", verdict=verdict),
        "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok"),
    }
    fx = _load("normal")
    run = run_playbook(
        fx["alarm"], _mini_playbook(), matched_by="t", polestar=None, apm=None, steps=lib
    )
    s3 = run.section("S3")
    assert s3.status == status
    if note is None:
        assert s3.reason is None
    else:
        assert note in s3.reason


def test_budget_exhaustion_skips_remaining_steps_with_reason():
    t = {"now": 0.0}

    def slow_step(ctx):
        t["now"] += 200.0  # 주입 시계 — 예산 120s를 넘긴다
        return StepResult(status="ok", verdict="degraded")

    lib = {
        "apm.golden_signals": slow_step,
        "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok"),
    }
    fx = _load("normal")
    run = run_playbook(
        fx["alarm"],
        _mini_playbook(),
        matched_by="t",
        polestar=None,
        apm=None,
        steps=lib,
        clock=lambda: t["now"],
        budget_s=120.0,
    )
    assert run.section("S1").status == "ok"
    s3 = run.section("S3")
    assert s3.status == "skipped" and s3.reason.startswith("budget")
    assert run.section("S9").status == "pending_synthesis"
    assert run.timings["budget_exceeded"] is True


def test_step_timeout_marks_missing_and_run_finishes():
    def hang(ctx):
        time.sleep(0.5)
        return StepResult(status="ok", verdict="degraded")

    lib = {"apm.golden_signals": hang, "apm.slow_tx_breakdown": lambda ctx: StepResult(status="ok")}
    fx = _load("normal")
    t0 = time.monotonic()
    run = run_playbook(
        fx["alarm"],
        _mini_playbook(),
        matched_by="t",
        polestar=None,
        apm=None,
        steps=lib,
        step_timeout_s=0.05,
    )
    assert time.monotonic() - t0 < 0.45
    s1 = run.section("S1")
    assert s1.status == "missing" and "타임아웃" in s1.reason
    assert run.section("S3").status == "ok" and "when 판정 불가" in run.section("S3").reason


# ── 잡 단위 캐시 (G-26) ─────────────────────────────────────────────────────


def test_cache_concurrent_same_args_single_real_call():
    started = threading.Event()
    release = threading.Event()
    real: list = []

    def slow_batch(calls):
        real.extend(calls)
        started.set()
        release.wait(2)
        return [json.dumps({"rows": [1]}) for _ in calls]

    cache = ToolResultCache()
    cached = cache.wrap(slow_batch)
    out: list = []
    th1 = threading.Thread(
        target=lambda: out.append(cached.call("apm_app_health", {"instance_name": "A1", "n": 1}))
    )
    th1.start()
    assert started.wait(2)
    th2 = threading.Thread(
        target=lambda: out.append(cached.call("apm_app_health", {"n": 1, "instance_name": "A1"}))
    )
    th2.start()
    time.sleep(0.05)
    release.set()
    th1.join(2)
    th2.join(2)
    assert len(real) == 1 and cache.real_calls == 1 and cache.hits == 1
    assert out == [json.dumps({"rows": [1]})] * 2


def test_cache_key_normalizes_order_and_none():
    assert cache_key("t", {"a": 1, "b": None, "c": {"x": None, "y": 2}}) == cache_key(
        "t", {"c": {"y": 2}, "a": 1}
    )
    assert cache_key("t", {"a": 1}) != cache_key("u", {"a": 1})


def test_cache_shares_errors_and_batches_only_misses():
    seen: list = []

    def batch(calls):
        seen.append(list(calls))
        raise ConnectionError("세션 실패")

    cache = ToolResultCache()
    cached = cache.wrap(batch)
    first = cached([("x", {"a": 1}), ("y", {})])
    again = cached([("x", {"a": 1}), ("z", {})])
    assert "세션 실패" in json.loads(first[0])["error"] and again[0] == first[0]
    assert seen == [[("x", {"a": 1}), ("y", {})], [("z", {})]]


def test_cache_closed_after_job_and_no_cross_job_leak():
    fx = _load("latency_sql")
    caller = FakeCaller(fx)
    _run(fx, caller)
    first = len(caller.calls)
    _run(fx, caller)
    assert len(caller.calls) == 2 * first  # 잡마다 새 캐시 — 이전 잡 결과를 쓰지 않는다

    with ToolResultCache() as cache:
        cache.wrap(caller).call("apm_instance_map", {"query": "A1"})
        assert len(cache) == 1
    with pytest.raises(CacheClosedError):
        cache.wrap(caller).call("apm_instance_map", {"query": "A1"})


def test_injected_cache_is_shared_with_other_engine_and_not_closed():
    fx = _load("latency_sql")
    caller = FakeCaller(fx)
    cache = ToolResultCache()
    _run(fx, caller, cache=cache)
    n = len(caller.calls)
    # 같은 잡의 다른 엔진(HolmesGPT)이 같은 도구·인자를 단일 호출로 부르면 실호출이 없다
    cache.wrap(caller).call("apm_instance_map", {"query": "A1"})
    assert len(caller.calls) == n and not cache.closed
    cache.close()


# ── 정규화 · 배선 도우미 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("event", "hints", "kind", "kinds"),
    [
        (
            {"resourceType": "apm.Instance", "alarmName": "TX_BAD_RESPONSE"},
            {"event_type": "TX_BAD_RESPONSE"},
            "apm",
            ("latency", "response_time"),
        ),
        (
            {"resourceType": "apm.Instance", "alarmName": "OUTOFMEMORY"},
            {"event_type": "OUTOFMEMORY"},
            "apm",
            ("was_heap_pressure",),  # W4 후속 ① — 게이트웨이 매핑 사본으로 정규화
        ),
        (
            {"resourceType": "Server", "alarmName": "Filesystem 사용률 임계 초과"},
            {},
            "disk",
            ("filesystem",),
        ),
        ({"resourceType": "Server", "alarmName": "Disk IO busy 높음"}, {}, "disk", ("disk_io",)),
        ({"resourceType": "Server", "alarmName": "CPU 사용률 90% 초과"}, {}, "cpu", ()),
    ],
)
def test_alarm_facts_from_payload(event, hints, kind, kinds):
    facts = alarm_facts_from_payload({"event": event, "meta": {"hints": hints}})
    assert facts.alarm_kind == kind and facts.kinds == kinds


def test_resolve_polestar_source_order():
    apm = {"event": {"resourceType": "apm.Instance", "dbId": "jennifer_common"}, "meta": {}}
    assert resolve_polestar_source(apm) == (None, POLESTAR_BRIDGE_MISSING)
    assert resolve_polestar_source(apm, "polestar") == ("polestar", None)
    with_episode = {**apm, "meta": {"episode": {"infra_db_ids": ["polestar_cm_gp"]}}}
    assert resolve_polestar_source(with_episode, "polestar") == ("polestar_cm_gp", None)
    host = {"event": {"resourceType": "Server", "dbId": "polestar"}}
    assert resolve_polestar_source(host) == ("polestar", None)


def test_make_apm_batch_caller_follows_settings():
    assert make_apm_batch_caller(SimpleNamespace(apm_mcp_url="")) is None
    assert callable(
        make_apm_batch_caller(
            SimpleNamespace(apm_mcp_url="http://127.0.0.1:9/sse", apm_mcp_token=None)
        )
    )
    # 기존 폴스타 배치 콜러블은 그대로
    assert callable(
        make_batch_caller(
            SimpleNamespace(polestar_mcp_url="http://127.0.0.1:9/sse", polestar_mcp_token=None)
        )
    )


def test_briefing_dict_shape():
    fx = _load("latency_sql")
    run = _run(fx, FakeCaller(fx))
    b = run.to_briefing_dict()
    assert (b["id"], b["version"]) == ("was.latency", 1) and b["matched_by"].startswith("match:")
    assert b["synthesis"] == {"hypotheses": [], "actions": [], "followups": []}
    sec = b["sections"][1]
    assert sec["opinion"] == "" and sec["opinion_dropped_reason"] is None and "data" not in sec
    assert {
        "step",
        "title",
        "status",
        "verdict",
        "verdict_source",
        "table",
        "badges",
        "tools",
    } <= set(sec)
    assert run.correlation is not None and set(run.correlation["metric_findings"]) == {
        "cpu",
        "memory",
        "filesystem",
    }
    s3 = run.section("S3")
    assert (
        s3.data["profile"]["profile_excerpt"] and s3.data["profile"]["sqls"]
    )  # R-D 원자료(해석은 W3)
