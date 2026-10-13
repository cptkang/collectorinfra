"""APM 조회 벤치 — P층(빈 결과 판별 · 허용값 탐침) (plans/152 W3 · D-323).

게이트웨이 호출은 주입 callable 대역으로만 한다(네트워크 0). 판정 보정은 `judge.judge_turn`이
레코드 `probe`의 `empty_check` 행을 읽는 경로로 함께 본다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts.apm_bench import judge as jd
from scripts.apm_bench import probe as pb
from scripts.apm_bench import scenarios as sc

KST = timezone(timedelta(hours=9))
ANCHOR = datetime(2026, 10, 12, 14, 37, tzinfo=KST)
SENT = ANCHOR.isoformat()


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


class FakeGateway:
    """`call_tool` 대역 — 호출을 기록하고 정해 둔 봉투(또는 예외)를 돌려준다."""

    def __init__(self, reply: Any = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reply = reply if reply is not None else {"row_count": 0, "rows": []}

    def __call__(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(args)))
        reply = self.reply(name, args) if callable(self.reply) else self.reply
        if isinstance(reply, Exception):
            raise reply
        return reply


def _win(start: datetime, end: datetime) -> dict[str, Any]:
    return {"start": start.isoformat(timespec="seconds"), "end": end.isoformat(timespec="seconds"),
            "minutes": round((end - start).total_seconds() / 60)}


def _record(*calls: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"scenario_id": "S-1", "turn": 0, "sent_at": SENT,
            "observed": {"calls": list(calls)}, **extra}


PAST = _win(ANCHOR - timedelta(days=1, hours=3), ANCHOR - timedelta(days=1))
RELATIVE = _win(ANCHOR - timedelta(minutes=30), ANCHOR + timedelta(seconds=10))


def test_fixed_window_args() -> None:
    args, why = pb.fixed_window_args(PAST, SENT)
    assert why == "" and args == {"reference_time": PAST["end"], "lookback_minutes": 180}
    assert pb.fixed_window_args(RELATIVE, SENT) == (None, "relative_window")
    assert pb.fixed_window_args(None, SENT) == (None, "no_window")
    assert pb.fixed_window_args({"current": {}, "baseline": {}}, SENT) == (None, "compare_window")
    assert pb.fixed_window_args(PAST, None) == (None, "no_sent_at")


def test_empty_probe_recalls_same_args_with_window() -> None:
    gw = FakeGateway()
    rec = _record(
        {"tool": "apm_events", "args": {"instance_name": "w", "level": "fatal"}, "window": PAST,
         "rows": 0},
        {"tool": "apm_events", "args": {}, "window": PAST, "rows": 4},  # 행 있음 — 대상 아님
        {"tool": "apm_events", "args": {}, "window": PAST, "rows": 0, "error": "timeout"},
        {"tool": "apm_app_health", "args": {}, "window": RELATIVE, "rows": 0},
    )
    rows = pb.empty_result_probes([rec], gw)
    assert gw.calls == [("apm_events", {"instance_name": "w", "level": "fatal",
                                        "reference_time": PAST["end"], "lookback_minutes": 180})]
    assert [(r["call_index"], r["status"]) for r in rows] == [(0, "no_data"), (3, "skipped")]
    assert rows[1]["reason"] == "relative_window"


def test_empty_probe_carries_call_hostname() -> None:
    """대상이 provenance `hostname`에만 있는 호출은 재호출 인자에 hostname 을 싣는다."""
    gw = FakeGateway()
    rec = _record({"tool": "apm_source_changes", "hostname": "h1", "args": {"source_ids": ["c"]},
                   "window": PAST, "rows": 0})
    pb.empty_result_probes([rec], gw)
    assert gw.calls[0][1]["hostname"] == "h1" and gw.calls[0][1]["source_ids"] == ["c"]


def test_empty_probe_rows_present_error_budget_and_skipped_turn() -> None:
    rec = _record({"tool": "t", "args": {}, "window": PAST, "rows": 0})
    present = pb.empty_result_probes([rec], FakeGateway({"row_count": 2}))
    assert present[0]["status"] == "rows_present"
    bad = pb.empty_result_probes([rec], FakeGateway({"error": "apm_api_error", "reason": "x"}))
    assert bad[0]["status"] == "error" and bad[0]["error"] == "apm_api_error"
    boom = pb.empty_result_probes([rec], FakeGateway(RuntimeError("down")))
    assert boom[0]["status"] == "error" and boom[0]["error"] == "RuntimeError"
    gw = FakeGateway()
    out = pb.empty_result_probes([rec], gw, budget_ok=lambda: False)
    assert gw.calls == [] and out[0]["reason"] == "budget"
    skipped = dict(rec, verdict={"skipped": "e2_skip"})
    assert pb.empty_result_probes([skipped], gw) == []


def test_empty_probe_skips_heavy_scenario() -> None:
    """무거운 등급 시나리오의 0행 호출은 다시 부르지 않는다(부하 한도)."""
    rec = _record({"tool": "t", "args": {}, "window": PAST, "rows": 0}, heavy=True)
    gw = FakeGateway()
    out = pb.empty_result_probes([rec], gw)
    assert gw.calls == [] and out[0]["status"] == "skipped" and out[0]["reason"] == "heavy"


def test_attach_then_judge_drops_empty_result(registry) -> None:
    """P층 `no_data`를 붙이고 다시 판정하면 `empty_result`가 빠진다."""
    expect = sc.Expect(("apm_query",), ("apm.events",), "none", "default")
    scenario = sc.Scenario("S-1", ("COV-EVENT",), "dev", (sc.Turn("이벤트", expect),))
    rec = _record({"tool": "apm_events", "args": {}, "window": PAST, "rows": 0})
    obs = rec["observed"]
    obs.update(agents=["apm_query"], apm_called=True, plan_final=[
        {"agent": "apm_query", "views": ["apm.events"], "view_args": {}}])
    assert jd.judge_turn(scenario, 0, rec, registry)["categories"][-1] == "empty_result"
    pb.attach([rec], pb.empty_result_probes([rec], FakeGateway()))
    assert rec["probe"][0]["status"] == "no_data"
    assert "empty_result" not in jd.judge_turn(scenario, 0, rec, registry)["categories"]


def test_allowed_value_probes(registry) -> None:
    def reply(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if args.get("interval_minute") == 1:
            return {"error": "invalid_argument", "reason": "interval_minute 거부"}
        return {"row_count": 1}

    gw = FakeGateway(reply)
    rows = pb.allowed_value_probes(gw, registry, {"instance_name": "w"})
    expected = sum(len(v) for *_x, v in pb.ALLOWED_VALUE_PROBES)
    assert len(gw.calls) == len(rows) == expected
    first = rows[0]
    assert (first["param"], first["value"], first["status"], first["error"]) == (
        "interval_minute", 1, "rejected", "invalid_argument")
    assert first["tool"] == registry.views["apm.runtime"].tool
    sort_call = next(args for name, args in gw.calls if "sort_by" in args)
    assert sort_call["kind"] == "application" and sort_call["instance_name"] == "w"
    assert {r["status"] for r in rows[1:]} == {"accepted"}
    assert {r["param"] for r in rows} == {"interval_minute", "sort_by_metrics", "max_row"}


def test_allowed_value_budget_and_unknown_view(registry) -> None:
    budget = iter([True, True])
    gw = FakeGateway({"row_count": 0})
    rows = pb.allowed_value_probes(gw, registry, {}, budget_ok=lambda: next(budget, False))
    assert len(gw.calls) == 2 and rows[2]["status"] == "skipped" and rows[2]["reason"] == "budget"
    rows = pb.allowed_value_probes(gw, registry, {}, probes=[("x", "apm.nope", "n", (1,))])
    assert rows == [{"kind": "allowed_value", "param": "x", "view": "apm.nope",
                     "status": "skipped", "reason": "레지스트리에 보기·조건 없음"}]
