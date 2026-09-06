"""결정적 상관 계산 골든 케이스 (plans/50 §6 · SPEC-evidence-correlation · D-197)."""

import pathlib

from sre_agent.domain.correlation import (
    AlarmPoint,
    MetricSeries,
    correlate,
    format_offset,
    metric_finding,
    offset_minutes,
)

REF = "2026-09-01T14:00:00"


def _series(name, points, baseline=(10, 12, 11, 10, 12, 11), gran=60, abs_thr=None):
    return MetricSeries(name=name, points=tuple(points), baseline=tuple(baseline),
                        granularity_minutes=gran, abs_threshold=abs_thr)


def test_offset_and_format():
    assert offset_minutes(REF, "2026-09-01T13:45:00") == -15
    assert offset_minutes(REF, "2026-09-01T14:02:30") == 2
    assert format_offset(-15) == "T-15m" and format_offset(2) == "T+2m" and format_offset(0) == "T+0m"


def test_golden_disk_leads_cpu_leads_alarm():
    """§9.1 예시: 디스크IO T-15m → CPU T-12m → 알람 T-10m ⇒ leading=disk_io, 지표 선행(음수 시차)."""
    disk = _series("disk_io", [("2026-09-01T13:30:00", 11.0), ("2026-09-01T13:45:00", 95.0), ("2026-09-01T13:50:00", 97.0)])
    cpu = _series("cpu", [("2026-09-01T13:30:00", 12.0), ("2026-09-01T13:48:00", 88.0)], abs_thr=90.0)
    alarms = [AlarmPoint("2026-09-01T13:50:00", 3, "CPU Utilization Critical", "cpu0"),
              AlarmPoint("2026-09-01T13:58:00", 0, "CPU Utilization Critical", "cpu0")]
    r = correlate(REF, alarms, [disk, cpu])
    assert r.leading_signal == "disk_io"
    assert r.metric_findings["disk_io"]["is_anomalous"] and r.metric_findings["disk_io"]["kind"] == "sustained"
    assert r.metric_findings["disk_io"]["onset_offset_min"] == -15
    assert r.metric_findings["disk_io"]["lead_lag_min"] == -5      # 첫 알람 T-10m 대비 5분 선행
    assert r.metric_findings["cpu"]["is_anomalous"] and r.metric_findings["cpu"]["kind"] == "spike"
    assert r.alarm_summary == {
        "count": 2, "by_severity": {"3": 1, "0": 1}, "names": ["CPU Utilization Critical"],
        "first_offset_min": -10, "last_offset_min": -2, "resolved": 1,
    }
    kinds = [(t.t_offset_min, t.kind) for t in r.timeline]
    assert kinds == [(-15, "metric"), (-12, "metric"), (-10, "alarm"), (-2, "alarm")]
    assert any("정밀도 60분" in n for n in r.notes)


def test_no_anomaly_no_leading_signal():
    s = _series("cpu", [("2026-09-01T13:00:00", 11.0), ("2026-09-01T14:00:00", 12.0)], abs_thr=90.0)
    r = correlate(REF, [AlarmPoint("2026-09-01T13:50:00", 2, "X")], [s])
    assert r.leading_signal is None
    assert r.metric_findings["cpu"]["is_anomalous"] is False
    assert r.metric_findings["cpu"]["lead_lag_min"] is None


def test_baseline_insufficient_is_noted_but_abs_threshold_still_applies():
    s = _series("memory", [("2026-09-01T13:30:00", 96.0)], baseline=(50.0,), abs_thr=90.0)
    f, notes = metric_finding(s, REF)
    assert f["is_anomalous"] and f["z_score"] is None
    assert any("baseline 부족" in n for n in notes)


def test_zero_variance_baseline_noted():
    s = _series("cpu", [("2026-09-01T13:30:00", 12.0)], baseline=(10.0, 10.0, 10.0))
    f, notes = metric_finding(s, REF)
    assert f["is_anomalous"] is False and any("분산 0" in n for n in notes)


def test_missing_series_and_no_alarms_noted():
    r = correlate(REF, [], [_series("cpu", [])])
    assert any("알람 0건" in n for n in r.notes)
    assert any("cpu: 사건 구간 데이터 없음" in n for n in r.notes)
    assert r.alarm_summary["first_offset_min"] is None


def test_metric_not_leading_alarm_is_noted():
    s = _series("cpu", [("2026-09-01T13:55:00", 99.0)], abs_thr=90.0)
    r = correlate(REF, [AlarmPoint("2026-09-01T13:50:00", 3, "X")], [s])
    assert r.leading_signal == "cpu"
    assert r.metric_findings["cpu"]["lead_lag_min"] == 5
    assert any("앞서지 않음" in n for n in r.notes)


def test_to_dict_roundtrip_shape():
    r = correlate(REF, [], [])
    d = r.to_dict()
    assert set(d) == {"reference_time", "timeline", "metric_findings", "alarm_summary", "leading_signal", "notes"}
    assert d["reference_time"] == REF


def test_correlation_module_is_vendor_neutral():
    """§6 설계 규율 — sre_agent는 overfit 게이트 밖이라 테스트로 고정한다."""
    src = pathlib.Path(__file__).resolve().parents[1] / "sre_agent" / "domain" / "correlation.py"
    text = src.read_text(encoding="utf-8").lower()
    for token in ("polestar", "cmm_", "stat_date", "avg_val", "prometheus", "promql"):
        assert token not in text, token
