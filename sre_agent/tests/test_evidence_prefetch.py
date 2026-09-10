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


# ── 변경 이벤트 오버레이 (plans/91 1-2 · C′-2 · SPEC-change-event-overlay) ─────────────────
from sre_agent.application.evidence_prefetch import TOOL_CHANGES  # noqa: E402

SCOPE_CHG = EvidenceScope(source="polestar", server_name="web-01", reference_time=REF, lookback_minutes=60,
                          baseline_periods=24, change_overlay=True)


def _change_rows(*, epoch=1756734000, unsupported=False):
    if unsupported:
        return json.dumps({"rows": [], "row_count": 0, "unsupported": True,
                           "note": "변경 이력은 PostgreSQL(gp/yd)만 지원 — DB2(b0)는 미지원"})
    return json.dumps({"rows": [
        {"id": 1, "resource_id": 7, "resource_type": "server.Server", "lifecycle_type": "UPDATE",
         "description": "메모리 증설 32→64GB", "event_time": epoch},
    ], "row_count": 1, "window": {"reference_time": REF}})


def _batch_chg(responses):
    def _call(calls):
        out = []
        for name, args in calls:
            out.append(responses[name] if name in (TOOL_ALARMS, TOOL_CHANGES) else responses[args["kind"]])
        return out
    return _call


def _metric_set():
    def base(center):
        return [center + d for d in (0, 2, 1, 0, 2, 1, 0, 2, 1, 0, 2)]
    return {
        "cpu": _metric_rows("cpu", [11.0, 95.0], base(10.0)),
        "memory": _metric_rows("memory", [40.0, 41.0], base(40.0)),
        "filesystem": _metric_rows("filesystem", [50.0, 50.0], base(50.0)),
        "disk_io": _metric_rows("disk_io", [10.0, 11.0], base(10.0)),
    }


def test_change_overlay_off_is_bit_identical():
    assert len(build_calls(SCOPE)) == 5 and all(n != TOOL_CHANGES for n, _ in build_calls(SCOPE))
    r = prefetch_and_correlate(SCOPE, _batch({TOOL_ALARMS: _alarm_rows(), **_metric_set()}))
    assert r.change_finding is None and "change_finding" not in r.to_dict()


def test_change_overlay_on_appends_call_last_and_keeps_index_contract():
    calls = build_calls(SCOPE_CHG)
    assert len(calls) == 6 and calls[-1][0] == TOOL_CHANGES
    assert calls[-1][1] == {"source": "polestar", "server_name": "web-01", "reference_time": REF, "lookback_minutes": 60}
    assert calls[0][0] == TOOL_ALARMS and [c[0] for c in calls[1:5]] == [TOOL_METRIC] * 4


def test_change_before_first_alarm_lands_in_timeline_and_finding():
    # 변경 13:40(T-20m) · 첫 알람 13:50(T-10m)
    from datetime import datetime
    epoch = int(datetime(2026, 9, 1, 13, 40).timestamp())   # 로컬 좌표 — mcp_server `timestamp()` 앵커와 동형
    r = prefetch_and_correlate(SCOPE_CHG, _batch_chg({TOOL_ALARMS: _alarm_rows(), TOOL_CHANGES: _change_rows(epoch=epoch), **_metric_set()}))
    kinds = [(t.t_offset_min, t.kind) for t in r.timeline]
    assert (-20, "change") in kinds and kinds.index((-20, "change")) < kinds.index((-10, "alarm"))
    assert r.change_finding == {"count": 1, "last_change_offset_min": -20, "before_first_alarm": True,
                                "descriptions": ["[UPDATE] 메모리 증설 32→64GB"]}
    assert r.to_dict()["change_finding"]["before_first_alarm"] is True


def test_change_unsupported_source_notes_limit_and_keeps_rest():
    r = prefetch_and_correlate(SCOPE_CHG, _batch_chg({TOOL_ALARMS: _alarm_rows(), TOOL_CHANGES: _change_rows(unsupported=True), **_metric_set()}))
    assert any("PostgreSQL 소스만 지원" in n and "polestar" in n for n in r.notes)
    assert r.change_finding is None and r.alarm_summary["count"] == 1 and r.metric_findings["cpu"]["is_anomalous"]


def test_change_tool_failure_is_noted_not_fatal():
    r = prefetch_and_correlate(SCOPE_CHG, _batch_chg({TOOL_ALARMS: _alarm_rows(), TOOL_CHANGES: json.dumps({"error": "boom"}), **_metric_set()}))
    assert any("변경 축 결손: boom" in n for n in r.notes) and r.alarm_summary["count"] == 1


def test_scope_from_job_carries_change_overlay_flag():
    job = SimpleNamespace(reference_time=REF, lookback_minutes=60, payload={"db_id": "polestar", "server_name": "web-01"})
    assert scope_from_job(job).change_overlay is False
    assert scope_from_job(job, change_overlay=True).change_overlay is True


# ── 연관 호스트 소비 (plans/91 1-3 · C′-1 · 페이로드 meta.root_resource_name) ────────────────
def _job_with_meta(**meta):
    return SimpleNamespace(reference_time=REF, lookback_minutes=60,
                           payload={"event": {"dbId": "polestar", "serverName": "web-01"}, "meta": meta})


def test_related_servers_off_by_default_and_meta_absent_is_bit_identical():
    assert scope_from_job(_job_with_meta(root_resource_name="db-01")).related_servers == ()
    assert scope_from_job(_job_with_meta(), related_hosts_max=3).related_servers == ()
    assert scope_from_job(_job_with_meta(root_resource_name="web-01"), related_hosts_max=3).related_servers == ()  # 자기 자신 제외


def test_related_servers_from_root_resource_name_with_cap():
    sc = scope_from_job(_job_with_meta(root_resource_name="db-01"), related_hosts_max=3)
    assert sc.related_servers == ("db-01",)
    calls = build_calls(sc)
    assert len(calls) == 6 and calls[-1] == (TOOL_ALARMS, {"source": "polestar", "server_name": "db-01",
                                                          "reference_time": REF, "lookback_minutes": 60})
    assert [c[0] for c in calls[1:5]] == [TOOL_METRIC] * 4   # 인덱스 규약 불변(연관 알람은 말미)


def _related_batch(rep_alarms, related_alarms, changes=None):
    def _call(calls):
        out = []
        for name, args in calls:
            if name == TOOL_ALARMS:
                out.append(rep_alarms if args["server_name"] == "web-01" else related_alarms)
            elif name == TOOL_CHANGES:
                out.append(changes)
            else:
                out.append(_metric_set()[args["kind"]])
        return out
    return _call


def test_related_alarm_lands_in_timeline_with_server_prefix_and_leading_note():
    related = json.dumps({"rows": [
        {"alarm_id": 9, "alarm_time": "2026-09-01 13:35:00", "severity": 3, "alarm_status": "OPEN",
         "alarm_name": "DB Down", "resource_name": "pg", "resource_type": "server.ProcessMonitor"},
    ], "row_count": 1})
    sc = scope_from_job(_job_with_meta(root_resource_name="db-01"), related_hosts_max=3)
    r = prefetch_and_correlate(sc, _related_batch(_alarm_rows(), related))
    assert r.alarm_summary["count"] == 1 and r.alarm_summary["first_offset_min"] == -10   # 요약은 대표 서버만
    details = [(t.t_offset_min, t.kind, t.detail) for t in r.timeline]
    assert (-25, "alarm", "[db-01] [severity 3] DB Down (pg)") in details
    assert any("연관 서버 db-01의 첫 알람 T-25m — 대표보다 선행" in n for n in r.notes)


def test_related_alarm_failure_keeps_representative_result():
    sc = scope_from_job(_job_with_meta(root_resource_name="db-01"), related_hosts_max=3)
    r = prefetch_and_correlate(sc, _related_batch(_alarm_rows(), json.dumps({"error": "down"})))
    assert r.alarm_summary["count"] == 1 and any("연관 서버 db-01 알람 결손: down" in n for n in r.notes)


def test_related_and_change_overlay_index_contract():
    sc = EvidenceScope(source="polestar", server_name="web-01", reference_time=REF, lookback_minutes=60,
                       change_overlay=True, related_servers=("db-01",))
    calls = build_calls(sc)
    assert [c[0] for c in calls] == [TOOL_ALARMS] + [TOOL_METRIC] * 4 + [TOOL_CHANGES, TOOL_ALARMS]
    related = json.dumps({"rows": [{"alarm_time": "2026-09-01 13:55:00", "severity": 2, "alarm_name": "Late"}]})
    r = prefetch_and_correlate(sc, _related_batch(_alarm_rows(), related, changes=_change_rows(unsupported=True)))
    assert r.change_finding is None and any("PostgreSQL 소스만" in n for n in r.notes)
    assert (-5, "alarm", "[db-01] [severity 2] Late") in [(t.t_offset_min, t.kind, t.detail) for t in r.timeline]
    assert not any("대표보다 선행" in n for n in r.notes)   # 13:55 > 13:50 — 선행 아님
