"""plans/190 W1·W2 — 플레이북 APM 호출의 해소 소스 한정 · X-View 사전 확인 (D-344).

가짜 배치 콜러블만 쓴다(실 게이트웨이·LLM 호출 0). 수용 기준(§4):
- 같은 이름이 2소스에 있으면 각 호출에 해당 `source_ids`만 실린다.
- 소스 미확정이면 종전 인자 + 한계 「APM 소스 미확정 — 전 소스 조회」.
- 상한 이하 = 인자 전송 · 데이터 정상 처리 / `confirm_required` = X-View 재호출 0 + 대체 호출 +
  한계 / 설정 None·0 이하 = 인자 미전송(비트 동일).
"""

from __future__ import annotations

import ast
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application.playbook_runner import run_playbook
from sre_agent.application.playbook_steps import (
    APM_SOURCE_IDS_TOOLS,
    APM_SOURCE_UNRESOLVED_LIMIT,
    APM_XVIEW_CONFIRM_TOOLS,
    XVIEW_CONFIRM_REQUIRED,
    RunScope,
    RunWindows,
    StepContext,
    ToolResponse,
    _apm_args,
    _note_confirm,
    _prior_window_compare_call,
    _row_source,
)
from sre_agent.infrastructure.playbook_loader import load_playbooks

PLAYBOOKS = {pb.id: pb for pb in load_playbooks().playbooks}
GATEWAY_SERVER = (
    Path(__file__).resolve().parents[2] / "apm_gateway" / "apm_gateway" / "interface" / "server.py"
)

ALARM = {
    "contract_version": "1",
    "event": {
        "dbId": "jennifer_common",
        "serverName": "wasdemo01",
        "hostname": "wasdemo01",
        "alarmName": "TX_BAD_RESPONSE",
        "resourceType": "apm.Instance",
        "resourceName": "A1",
        "alarmTime": "20261019100000",
    },
    "meta": {"hints": {"solution": "apm", "event_type": "TX_BAD_RESPONSE"}},
}
HOST_ALARM = {
    "contract_version": "1",
    "event": {
        "dbId": "polestar",
        "serverName": "wasdemo01",
        "hostname": "wasdemo01",
        "alarmName": "CPU",
        "resourceType": "server",
        "resourceName": "wasdemo01",
        "alarmTime": "20261019100000",
    },
}


def _inst(name: str, src: str | None, host: str = "wasdemo01", **extra: Any) -> dict:
    row = {"instance_name": name, "hostname": host, **extra}
    if src is not None:
        row["source_id"] = src
    return row


def _health(p95: float = 1000.0, signals: list | None = None) -> dict:
    return {
        "rows": [
            {
                "instance_name": "x",
                "window": {
                    "calls": 2400,
                    "errors": 0,
                    "error_rate": 0.0,
                    "response_time_p50_ms": 500.0,
                    "response_time_p95_ms": p95,
                    "response_time_max_ms": 3000,
                },
            }
        ],
        "was_signals": signals or [],
    }


CONFIRM = {
    "rows": [],
    "row_count": 0,
    "tool": "apm_app_health",
    "limits": ["[한계] 사전 확인 — 예상 X-View 호출 40회가 기준 10회를 넘어"],
    "status": XVIEW_CONFIRM_REQUIRED,
    "expected_xview_calls": 40,
    "expected_api_calls": 41,
    "xview_confirm_over": 10,
    "instance_count": 1,
    "estimate": {"api_calls": 41, "seconds": 12.0, "basis": "default"},
}
COMPARE = {
    "rows": [
        {
            "instance_name": "A1",
            "current": {"calls": 4000, "failures": 4, "failure_rate": 0.1,
                        "avg_response_ms": 900.0},
            "baseline": {"calls": 3800, "failures": 2, "failure_rate": 0.05,
                         "avg_response_ms": 400.0},
        }
    ]
}


class Fake:
    """규칙 함수로 응답하는 배치 콜러블 — 실제 호출을 순서대로 기록한다."""

    def __init__(self, rule) -> None:
        self.rule = rule
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def __call__(self, calls):
        out = []
        for tool, args in calls:
            with self._lock:
                self.calls.append((tool, dict(args)))
            resp = self.rule(tool, dict(args))
            out.append(json.dumps({"rows": []} if resp is None else resp, ensure_ascii=False))
        return out

    def of(self, tool: str) -> list[dict]:
        return [a for t, a in self.calls if t == tool]


def _run(payload: dict, pb_id: str, apm: Fake, **kw):
    kw.setdefault("max_workers", 1)
    return run_playbook(
        payload, PLAYBOOKS[pb_id], matched_by="test", polestar=None, apm=apm, **kw
    )


# ── W0 — 도구 허용 목록 = 게이트웨이 시그니처(사본 고정) ───────────────────────


def _gateway_params() -> dict[str, set[str]]:
    tree = ast.parse(GATEWAY_SERVER.read_text(encoding="utf-8"))
    return {
        n.name: {a.arg for a in n.args.args + n.args.kwonlyargs}
        for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name.startswith("apm_")
    }


@pytest.mark.skipif(not GATEWAY_SERVER.exists(), reason="apm_gateway 소스 없음(배포 사본)")
def test_allowlists_match_gateway_signatures():
    params = _gateway_params()
    assert APM_SOURCE_IDS_TOOLS == {t for t, p in params.items() if "source_ids" in p}
    assert APM_XVIEW_CONFIRM_TOOLS == {t for t, p in params.items() if "xview_confirm_over" in p}


# ── 헬퍼 단위 ───────────────────────────────────────────────────────────────


def _ctx(xview: int | None = None) -> StepContext:
    w = RunWindows("2026-10-19T10:00:00", "2026-10-19T10:10:00", 40,
                   "2026-10-19T09:30:00", "2026-10-19T10:10:00")
    return StepContext(payload={}, windows=w, scope=RunScope(), args={}, targets=["A1"],
                       polestar=None, apm=None, now=datetime.now,
                       prior={}, xview_confirm_over=xview)


def test_apm_args_adds_source_ids_only_for_allowlisted_tools():
    ctx = _ctx()
    assert _apm_args(ctx, "apm_events", "b0", instance_name="A1") == {
        "instance_name": "A1", "source_ids": ["b0"],
    }
    prof = _apm_args(ctx, "apm_transaction_profile", "b0", instance_name="A1", source_id="cm")
    assert prof == {"instance_name": "A1", "source_id": "cm"}
    assert ctx.limits == []


def test_apm_args_unresolved_source_keeps_args_and_notes_limit():
    ctx = _ctx()
    assert _apm_args(ctx, "apm_events", None, instance_name="A1") == {"instance_name": "A1"}
    assert ctx.limits == [APM_SOURCE_UNRESOLVED_LIMIT]
    ctx2 = _ctx()
    assert _apm_args(ctx2, "apm_instance_map", None, discovery=True, query="A1") == {"query": "A1"}
    assert ctx2.limits == []


@pytest.mark.parametrize("value, sent", [(10, 10), (None, None), (0, None), (-3, None)])
def test_apm_args_xview_confirm_over(value, sent):
    ctx = _ctx(value)
    args = _apm_args(ctx, "apm_app_health", "b0", instance_name="A1",
                     reference_time="2026-10-19T10:10:00", lookback_minutes=40)
    assert args.get("xview_confirm_over") == sent
    assert ("xview_confirm_over" in args) is (sent is not None)
    # 현재값 모드(reference_time 없음)·다른 도구에는 싣지 않는다
    assert "xview_confirm_over" not in _apm_args(ctx, "apm_app_health", "b0", instance_name="A1")
    assert "xview_confirm_over" not in _apm_args(
        ctx, "apm_runtime_health", "b0", instance_name="A1", reference_time="2026-10-19T10:10:00"
    )


# ── W1 — 같은 이름 2소스 ────────────────────────────────────────────────────


def _two_source_rule(tool: str, args: dict):
    src = (args.get("source_ids") or [None])[0]
    if tool == "apm_instance_map":
        if "query" in args:
            return {"rows": [_inst("A1", "b0", search_confidence="high",
                                   business_names=["계좌조회"]),
                             _inst("A1", "cm_prd", host="other01", search_confidence="high")]}
        if "hostname" in args:
            assert args.get("source_ids") == ["b0"]  # 알람 서버의 소스로 좁힌다
            return {"rows": [_inst("A1", "b0")]}
        if "business" in args:
            return {"rows": [_inst("A1", "b0"), _inst("A1", "cm_prd", host="other01"),
                             _inst("B1", "cm_prd", host="other02")]}
    if tool == "apm_app_health":
        if args["instance_name"] == "A1" and src == "cm_prd":
            return _health(signals=[{"kind": "was_slow_sql"}])
        return _health()
    return None


def test_same_name_two_sources_each_call_carries_its_own_source():
    apm = Fake(_two_source_rule)
    run = _run(ALARM, "was.latency", apm, for_each_max=None)
    s0 = run.section("S0")
    assert [(i["instance"], i["source_id"]) for i in s0.data["instances"]] == [
        ("A1", "b0"), ("A1", "cm_prd"), ("B1", "cm_prd"),
    ]
    assert [t["source_id"] for t in run.scope["targets"]] == ["b0", "cm_prd"]
    # 대상 확정 뒤 모든 데이터 호출은 소스 1개만 싣고, (이름, 소스)는 대상 확정 결과와 같다
    pairs = set()
    for tool, args in apm.calls:
        if tool == "apm_instance_map" or "instance_name" not in args:
            continue
        if tool in APM_SOURCE_IDS_TOOLS:
            assert len(args["source_ids"]) == 1, (tool, args)
            pairs.add((args["instance_name"], args["source_ids"][0]))
    assert pairs == {("A1", "b0"), ("A1", "cm_prd"), ("B1", "cm_prd")}
    # S1 대상별 키 — 첫 행은 이름 그대로, 같은 이름 두 번째는 `이름@소스`
    assert list(run.section("S1").data["targets"]) == ["A1", "A1@cm_prd"]
    cmp_pairs = {(a["instance_name"], a["source_ids"][0]) for a in apm.of("apm_period_compare")}
    assert cmp_pairs == {("A1", "b0"), ("A1", "cm_prd")}
    assert APM_SOURCE_UNRESOLVED_LIMIT not in run.notes


def test_unresolved_source_keeps_old_args_and_notes_limit():
    def rule(tool, args):
        if tool == "apm_instance_map" and "query" in args:
            return {"rows": [_inst("A1", None, search_confidence="high")]}
        if tool == "apm_app_health":
            return _health()
        return None

    apm = Fake(rule)
    run = _run(ALARM, "was.latency", apm)
    assert all("source_ids" not in a for _, a in apm.calls)
    assert APM_SOURCE_UNRESOLVED_LIMIT in run.notes
    assert APM_SOURCE_UNRESOLVED_LIMIT in run.to_briefing_dict()["notes"]


def test_s0_failure_falls_back_to_alarm_instance_without_source():
    apm = Fake(lambda tool, args: {"rows": []} if tool == "apm_instance_map" else _health())
    run = _run(ALARM, "was.latency", apm)
    assert run.section("S0").data["resolution"] == "failed"
    assert all("source_ids" not in a for t, a in apm.calls)
    assert APM_SOURCE_UNRESOLVED_LIMIT in run.notes


def test_host_to_was_splits_same_name_by_source():
    pb = next((p for p in PLAYBOOKS.values()
               if any(s.action == "scope.host_to_was" for s in p.steps)), None)
    if pb is None:
        pytest.skip("역방향 플레이북 없음")

    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0"), _inst("A1", "cm_prd")]}
        if tool == "apm_app_health":
            return _health()
        return None

    apm = Fake(rule)
    run = run_playbook(HOST_ALARM, pb, matched_by="test", polestar=None, apm=apm, max_workers=1)
    sec = next(s for s in run.sections if s.action == "scope.host_to_was")
    assert [(i["instance"], i["source_id"]) for i in sec.data["instances"]] == [
        ("A1", "b0"), ("A1", "cm_prd"),
    ]
    assert sorted(a["source_ids"][0] for a in apm.of("apm_app_health")) == ["b0", "cm_prd"]
    assert sec.table["rows"][1][0] == "A1@cm_prd"


# ── W2 — X-View 사전 확인 ────────────────────────────────────────────────────


def _resolved_rule(app_health):
    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0", search_confidence="high")]}
        if tool == "apm_app_health":
            return app_health(args)
        if tool == "apm_period_compare":
            return COMPARE
        return None

    return rule


def test_under_threshold_sends_arg_and_processes_data():
    apm = Fake(_resolved_rule(lambda a: _health(p95=6000.0) if a["reference_time"].startswith(
        "2026-10-19") else _health(p95=1000.0)))
    run = _run(ALARM, "was.latency", apm, xview_confirm_over=10)
    health = apm.of("apm_app_health")
    assert health and all(a["xview_confirm_over"] == 10 for a in health)
    s1 = run.section("S1")
    assert s1.verdict == "degraded"  # 6000 ≥ 2 × 1000 — 데이터 정상 처리
    assert not any("X-View 사전 확인 초과" in n for n in run.notes)


def test_confirm_required_falls_back_without_xview_recall():
    apm = Fake(_resolved_rule(lambda a: CONFIRM))
    run = _run(ALARM, "was.latency", apm, xview_confirm_over=10)
    health = apm.of("apm_app_health")
    # X-View 재호출 0 — 모든 app_health는 상한을 실은 사전 확인 호출뿐(상한 없이 다시 부르지 않음)
    assert health and all(a.get("xview_confirm_over") == 10 for a in health)
    # S0 대체 — 사건창(09:30~10:10 → 시 경계 09~11시, 2시간)의 직전 시 경계 구간 비교 1회
    prior = [a for a in apm.of("apm_period_compare")
             if a["baseline_end"] == "2026-10-19T09:00:00"]
    assert prior == [{
        "instance_name": "A1",
        "current_start": "2026-10-19T09:30:00",
        "current_end": "2026-10-19T10:10:00",
        "baseline_start": "2026-10-19T07:00:00",
        "baseline_end": "2026-10-19T09:00:00",
        "source_ids": ["b0"],
    }]
    s0 = run.section("S0")
    head = s0.data["instances"][0]
    assert head["xview_confirm_required"] is True and head["period_compare"] == COMPARE["rows"][0]
    # p95 근거 없음 → 판정 불가(정상으로 강등하지 않는다)
    assert s0.verdict is None and head["verdict"] is None
    s1 = run.section("S1")
    assert s1.verdict is None and s1.status == "ok"
    assert s1.data["targets"]["A1"]["xview_confirm_required"] is True
    assert s1.data["targets"]["A1"]["period_compare"]  # §3.2 ① 시 단위 통계는 그대로
    limit = "X-View 사전 확인 초과(예상 40회 > 상한 10) — 구간 p95·에러율 생략, 시 단위 통계로 대체"
    assert limit in run.notes and limit in run.to_briefing_dict()["notes"]


def test_confirm_required_and_fallback_failure_marks_missing():
    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0", search_confidence="high")]}
        if tool == "apm_app_health":
            return CONFIRM
        if tool == "apm_period_compare":
            return {"error": "source_unavailable", "reason": "타임아웃"}
        return None

    run = _run(ALARM, "was.latency", Fake(rule), xview_confirm_over=10)
    s0, s1 = run.section("S0"), run.section("S1")
    assert "apm_app_health" in s0.missing and "apm_period_compare" in s0.missing
    assert s0.data["instances"][0]["period_compare"] is None
    assert "apm_app_health" in s1.missing and s1.verdict is None


def test_host_to_was_confirm_required_is_not_normal():
    pb = next((p for p in PLAYBOOKS.values()
               if any(s.action == "scope.host_to_was" for s in p.steps)), None)
    if pb is None:
        pytest.skip("역방향 플레이북 없음")

    def rule(tool, args):
        if tool == "apm_instance_map":
            return {"rows": [_inst("A1", "b0")]}
        if tool == "apm_app_health":
            return CONFIRM
        if tool == "apm_period_compare":
            return COMPARE
        return None

    apm = Fake(rule)
    run = run_playbook(HOST_ALARM, pb, matched_by="test", polestar=None, apm=apm,
                       max_workers=1, xview_confirm_over=10)
    sec = next(s for s in run.sections if s.action == "scope.host_to_was")
    assert sec.verdict is None and sec.data["instances"][0]["verdict"] is None
    assert len(apm.of("apm_period_compare")) >= 1
    assert any("X-View 사전 확인 초과" in n for n in run.notes)


@pytest.mark.parametrize("value", [None, 0])
def test_setting_off_sends_no_arg_bit_identical(value):
    rule = _resolved_rule(lambda a: _health())
    off, base = Fake(rule), Fake(rule)
    _run(ALARM, "was.latency", off, xview_confirm_over=value)
    _run(ALARM, "was.latency", base)
    assert all("xview_confirm_over" not in a for _, a in off.calls)
    assert off.calls == base.calls


# ── 교정(verifier Minor) — 시 경계 직전 구간 · 형식 가드 ──────────────────────


def _hours(start: str, end: str) -> tuple[datetime, datetime]:
    """게이트웨이 `hour_floor`·`hour_ceil`과 같은 규칙으로 넓힌 구간."""
    s, e = datetime.fromisoformat(start), datetime.fromisoformat(end)
    f = s.replace(minute=0, second=0, microsecond=0)
    c = e.replace(minute=0, second=0, microsecond=0)
    return f, (c if c == e else c + timedelta(hours=1))


@pytest.mark.parametrize("start, end, base", [
    ("2026-10-19T09:50:00", "2026-10-19T10:20:00", ("2026-10-19T07:00:00", "2026-10-19T09:00:00")),
    ("2026-10-19T09:00:00", "2026-10-19T10:00:00", ("2026-10-19T08:00:00", "2026-10-19T09:00:00")),
    ("2026-10-19T09:10:00", "2026-10-19T09:40:00", ("2026-10-19T08:00:00", "2026-10-19T09:00:00")),
])
def test_prior_window_does_not_overlap_after_hour_widening(start, end, base):
    w = RunWindows("2026-10-19T10:00:00", end, 30, start, end)
    ctx = StepContext(payload={}, windows=w, scope=RunScope(), args={}, targets=["A1"],
                      polestar=None, apm=None, now=datetime.now, prior={})
    _, args = _prior_window_compare_call(ctx, "A1", "b0")
    assert (args["baseline_start"], args["baseline_end"]) == base
    cur = _hours(args["current_start"], args["current_end"])
    prev = _hours(args["baseline_start"], args["baseline_end"])
    assert prev[1] <= cur[0]  # 넓힌 뒤에도 겹치지 않는다
    assert prev[1] - prev[0] == cur[1] - cur[0]  # 같은 길이(시 단위)


@pytest.mark.parametrize("n, m, shown", [
    (40, 10, "예상 40회 > 상한 10"),
    (True, 10, "예상 ?회 > 상한 10"),
    ("40", 10.5, "예상 ?회 > 상한 ?"),
    (None, None, "예상 ?회 > 상한 ?"),
])
def test_note_confirm_counts_must_be_int(n, m, shown):
    ctx = _ctx(10)
    data = {"status": XVIEW_CONFIRM_REQUIRED, "expected_xview_calls": n, "xview_confirm_over": m}
    _note_confirm(ctx, ToolResponse("apm_app_health", {}, data, None))
    assert len(ctx.limits) == 1 and shown in ctx.limits[0]


@pytest.mark.parametrize("value, src", [
    ("b0", "b0"), ("  b0 ", "b0"), ("", None), ("   ", None), (None, None),
    (7, None), (["b0"], None), ("x" * 64, "x" * 64), ("x" * 65, None),
])
def test_row_source_format_guard(value, src):
    assert _row_source({"source_id": value}) == src
