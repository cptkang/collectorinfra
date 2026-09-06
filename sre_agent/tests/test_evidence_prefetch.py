"""사전수집 — 배치 호출 조립·행 해석·부분 실패 (plans/50 G4-b · SPEC-evidence-correlation)."""

import json
from types import SimpleNamespace

from sre_agent.application.evidence_prefetch import (
    METRIC_KINDS,
    TOOL_ALARMS,
    TOOL_METRIC,
    EvidenceScope,
    build_calls,
    prefetch_and_correlate,
    scope_from_job,
)

REF = "2026-09-01T14:00:00"
SCOPE = EvidenceScope(source="polestar", server_name="web-01", reference_time=REF, lookback_minutes=60, baseline_periods=24)


def _alarm_rows():
    return json.dumps({"rows": [
        {"alarm_id": 1, "alarm_time": "2026-09-01 13:50:00", "severity": 3, "alarm_status": "OPEN",
         "alarm_name": "CPU Utilization Critical", "resource_name": "cpu0", "resource_type": "server.Cpus"},
    ], "row_count": 1, "window": {"reference_time": REF, "incident_from": "2026-09-01T13:00:00"}})


def _metric_rows(kind, incident_vals, baseline_vals):
    rows = []
    # baseline: 2026-08-31 13시부터 24시간, incident: 2026-09-01 13·14시
    for i, v in enumerate(baseline_vals):
        rows.append({"stat_date": f"202608{31}{(13 + i) % 24:02d}" if 13 + i < 24 else f"20260901{(13 + i) % 24:02d}", "avg_val": v})
    for i, v in enumerate(incident_vals):
        rows.append({"stat_date": f"20260901{13 + i:02d}", "avg_val": v})
    return json.dumps({"rows": rows, "kind": kind, "granularity": "h",
                       "window": {"stat_date_incident_from": "2026090113", "stat_date_to": "2026090114"}})


def _batch(responses):
    calls_seen = []

    def _call(calls):
        calls_seen.extend(calls)
        return [responses[name if name == TOOL_ALARMS else args["kind"]] for name, args in calls]

    _call.seen = calls_seen  # type: ignore[attr-defined]
    return _call


def test_build_calls_order_and_anchor_args():
    calls = build_calls(SCOPE)
    assert calls[0][0] == TOOL_ALARMS and [c[0] for c in calls[1:]] == [TOOL_METRIC] * 4
    assert [c[1]["kind"] for c in calls[1:]] == [k for k, _ in METRIC_KINDS]
    for _, args in calls:
        assert args["reference_time"] == REF and args["lookback_minutes"] == 60 and args["source"] == "polestar"
    assert calls[1][1]["baseline_periods"] == 24 and calls[1][1]["granularity"] == "h"


def test_prefetch_splits_baseline_and_finds_leading_signal():
    def base(center):  # 분산이 있는 baseline(분산 0이면 z-score 미판정 — test_correlation 참조)
        return [center + d for d in (0, 2, 1, 0, 2, 1, 0, 2, 1, 0, 2)]

    responses = {
        TOOL_ALARMS: _alarm_rows(),
        "cpu": _metric_rows("cpu", [11.0, 95.0], base(10.0)),
        "memory": _metric_rows("memory", [40.0, 41.0], base(40.0)),
        "filesystem": _metric_rows("filesystem", [50.0, 50.0], base(50.0)),
        "disk_io": _metric_rows("disk_io", [90.0, 95.0], base(10.0)),
    }
    r = prefetch_and_correlate(SCOPE, _batch(responses))
    assert r.metric_findings["disk_io"]["is_anomalous"] and r.metric_findings["cpu"]["is_anomalous"]
    assert not r.metric_findings["memory"]["is_anomalous"] and not r.metric_findings["filesystem"]["is_anomalous"]
    assert r.leading_signal == "disk_io"        # 13시 onset(T-60m) — cpu는 14시(T+0m)
    assert r.metric_findings["disk_io"]["points"] == 2
    assert r.metric_findings["cpu"]["baseline_mean"] == 11.0 and r.metric_findings["cpu"]["baseline_std"] > 0
    assert r.alarm_summary["count"] == 1 and r.alarm_summary["first_offset_min"] == -10


def test_partial_failure_is_noted_not_fatal():
    responses = {
        TOOL_ALARMS: json.dumps({"error": "알 수 없는 소스: x"}),
        "cpu": _metric_rows("cpu", [11.0], [10.0] * 5),
        "memory": "not json",
        "filesystem": json.dumps({"rows": [{"stat_date": "bad", "avg_val": "x"}], "window": {}}),
        "disk_io": _metric_rows("disk_io", [], [10.0] * 5),
    }
    r = prefetch_and_correlate(SCOPE, _batch(responses))
    notes = " | ".join(r.notes)
    assert "알람 축 결손: 알 수 없는 소스" in notes
    assert "memory 축 결손" in notes and "JSON 파싱 실패" in notes
    assert "filesystem: 지표 행 해석 실패" in notes
    assert "disk_io: 사건 구간 데이터 없음" in notes
    assert "알람 0건" in notes
    assert "cpu" in r.metric_findings and r.leading_signal is None


def test_short_batch_response_is_padded():
    r = prefetch_and_correlate(SCOPE, lambda calls: [])
    assert len([n for n in r.notes if "결손" in n]) == 5


def test_scope_from_job_both_payload_shapes():
    push = SimpleNamespace(reference_time=REF, lookback_minutes=120,
                           payload={"event": {"dbId": "polestar_cm_gp", "serverName": "web-01"}})
    pull = SimpleNamespace(reference_time=REF, lookback_minutes=None,
                           payload={"db_id": "polestar", "server_name": "web-02"})
    s1 = scope_from_job(push, baseline_periods=12)
    s2 = scope_from_job(pull)
    assert (s1.source, s1.server_name, s1.lookback_minutes, s1.baseline_periods) == ("polestar_cm_gp", "web-01", 120, 12)
    assert (s2.source, s2.server_name, s2.lookback_minutes) == ("polestar", "web-02", 60)


def test_scope_from_job_missing_pieces():
    assert scope_from_job(SimpleNamespace(reference_time=None, payload={"db_id": "p", "server_name": "s"})) is None
    assert scope_from_job(SimpleNamespace(reference_time=REF, payload={"server_name": "s"})) is None
    assert scope_from_job(SimpleNamespace(reference_time=REF, payload={"db_id": "p"})) is None
    assert scope_from_job(SimpleNamespace(reference_time=REF, payload="x")) is None
