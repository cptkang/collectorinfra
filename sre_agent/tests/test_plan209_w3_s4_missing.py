"""plans/209 W3 (D-360 ⑤) — S4 조회 성공 0건·판정 불가를 「정상」으로 쓰지 않는다.

- `metric_finding` 3상태: 점 0개 · (z 미산출 AND 절대 임계 없음) → `is_anomalous=None`(판정 불가).
  z를 못 구해도 절대 임계가 있으면 종전 판정(True/False) 유지.
- `usage_trend`: 전부 판정 불가 → `missing` · 일부 판정 불가 + 나머지 정상 → 판단 보류(verdict None)
  · 일부 판정 불가 + 나머지 이상 → degraded · 판정 가능만 → 종전 비트 동일.
- 하류: R-E는 None을 결손으로, S4 결과 한 줄은 「모든 지표 평소 범위」를 쓰지 않는다.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sre_agent.application.playbook_runner import compute_windows
from sre_agent.application.playbook_steps import RunScope, StepContext, usage_trend
from sre_agent.domain.correlation import MetricSeries, correlate, metric_finding
from sre_agent.domain.playbook import WindowSpec
from sre_agent.domain.playbook_readings import read_cpu_owner
from sre_agent.domain.step_findings import summarize

ALARM_TIME = "2026-09-01T13:30:00"
REF = ALARM_TIME
_BASE = (20.0, 22.0, 19.0, 21.0, 20.0, 23.0, 18.0, 20.0)


# ── metric_finding 3상태 ────────────────────────────────────────────────────


def _series(points=(), baseline=(), abs_thr: float | None = None, name: str = "cpu") -> MetricSeries:
    return MetricSeries(name=name, points=tuple(points), baseline=tuple(baseline),
                        granularity_minutes=60, abs_threshold=abs_thr)


def test_zero_points_is_unjudgeable_even_with_abs_threshold() -> None:
    f, notes = metric_finding(_series(abs_thr=90.0), REF)
    assert f["is_anomalous"] is None and f["judgeable"] is False
    assert f["reason"] == "cpu: 사건 구간 데이터 없음" and f["reason"] in notes


def test_no_z_and_no_abs_threshold_is_unjudgeable() -> None:
    f, notes = metric_finding(_series([(REF, 50.0)], baseline=(1.0,), name="disk_io"), REF)
    assert f["is_anomalous"] is None and f["judgeable"] is False and "판정 불가" in f["reason"]
    assert any("baseline 부족" in n for n in notes)
    assert f["peak_value"] == 50.0  # 판정만 보류 — 관측값은 그대로 싣는다


def test_no_z_but_abs_threshold_keeps_current_judgement() -> None:
    low, _ = metric_finding(_series([(REF, 50.0)], baseline=(1.0,), abs_thr=90.0), REF)
    high, _ = metric_finding(_series([(REF, 95.0)], baseline=(1.0,), abs_thr=90.0), REF)
    assert low["is_anomalous"] is False and "judgeable" not in low and "reason" not in low
    assert high["is_anomalous"] is True and "judgeable" not in high


def test_judgeable_finding_keys_unchanged() -> None:
    f, _ = metric_finding(_series([(REF, 21.0)], baseline=_BASE), REF)
    assert f["is_anomalous"] is False
    assert set(f) == {"is_anomalous", "kind", "peak_value", "peak_time", "onset_offset_min", "z_score",
                      "baseline_mean", "baseline_std", "points"}


def test_correlate_treats_none_as_not_anomalous() -> None:
    r = correlate(REF, [], [_series(), _series([(REF, 95.0)], _BASE, 90.0, name="memory")], alarm_axis=False)
    assert r.metric_findings["cpu"]["is_anomalous"] is None
    assert r.metric_findings["cpu"]["lead_lag_min"] is None
    assert r.leading_signal == "memory"
    assert all("cpu" not in t.detail for t in r.timeline)


# ── usage_trend(S4) ─────────────────────────────────────────────────────────


def _metric(incident: list[float], baseline: list[float] = list(_BASE)) -> str:
    rows = [{"stat_date": f"20260831{i:02d}", "avg_val": v} for i, v in enumerate(baseline)]
    rows += [{"stat_date": f"20260901{13 + i:02d}", "avg_val": v} for i, v in enumerate(incident)]
    return json.dumps({"rows": rows, "window": {"stat_date_incident_from": "2026090113"}})


_EMPTY = json.dumps({"rows": [], "row_count": 0, "window": {"stat_date_incident_from": "2026090113"}})
_FAIL = json.dumps({"error": "timeout"})


def _ctx(responses: dict[str, str], basis: str | None = None) -> StepContext:
    kinds = list(responses)

    def polestar(calls: Any) -> list[str]:
        return [responses[str(args["kind"])] for _, args in calls]

    return StepContext(
        payload={"event": {}}, windows=compute_windows(ALARM_TIME, WindowSpec()),
        scope=RunScope(polestar_source="polestar", polestar_server="SAE0011", polestar_basis=basis),
        args={"kinds": kinds}, targets=[], polestar=polestar, apm=None, now=datetime.now, prior={},
    )


def test_all_kinds_zero_rows_is_missing_not_normal() -> None:
    res = usage_trend(_ctx({"cpu": _EMPTY, "memory": _EMPTY}, basis="설정 매핑"))
    assert res.status == "missing" and res.verdict is None
    assert res.missing == ["cpu", "memory"]
    assert res.reason is not None and "cpu: 사건 구간 데이터 없음" in res.reason
    assert res.reason.endswith("설정 매핑")


def test_failure_plus_zero_rows_is_missing() -> None:
    res = usage_trend(_ctx({"cpu": _FAIL, "memory": _EMPTY}))
    assert res.status == "missing" and res.missing == ["cpu", "memory"]
    assert "cpu 축 결손" in (res.reason or "") and "memory: 사건 구간 데이터 없음" in (res.reason or "")


def test_partial_zero_rows_rest_normal_is_hold() -> None:
    res = usage_trend(_ctx({"cpu": _metric([21.0, 20.0]), "memory": _EMPTY}))
    assert res.status == "ok" and res.verdict is None and res.verdict_source is not None
    assert res.missing == ["memory"]
    rows = {r[0]: r for r in res.table["rows"]}  # type: ignore[index]
    assert rows["memory"][4] is None and rows["cpu"][4] is False
    assert "memory: 사건 구간 데이터 없음" in (res.reason or "")
    text = summarize("host.usage_trend", res.to_dict(), {})
    assert text is not None and "모든 지표 평소 범위" not in text
    assert text.startswith("판단 보류") and "메모리 판정 불가" in text
    assert "결손" not in text  # 본문에 「판정 불가」로 쓴 kind는 끝 결손 목록에 다시 싣지 않는다


def test_summary_keeps_failed_kind_in_missing_list() -> None:
    res = usage_trend(_ctx({"cpu": _metric([21.0]), "memory": _EMPTY, "filesystem": _FAIL}))
    assert res.verdict is None and res.missing == ["filesystem", "memory"]
    text = summarize("host.usage_trend", res.to_dict(), {}) or ""
    assert text.endswith(" · 결손 파일시스템") and "메모리 판정 불가" in text


def test_partial_zero_rows_rest_anomalous_is_degraded() -> None:
    res = usage_trend(_ctx({"cpu": _metric([97.0, 98.0]), "memory": _EMPTY}))
    assert res.status == "ok" and res.verdict == "degraded"
    assert res.data["anomalous"] == ["cpu"] and res.missing == ["memory"]
    text = summarize("host.usage_trend", res.to_dict(), {})
    assert text is not None and text.startswith("이상 지표 있음")


def test_all_judgeable_unchanged_shape() -> None:
    res = usage_trend(_ctx({"cpu": _metric([21.0]), "memory": _metric([20.0])}))
    assert res.status == "ok" and res.verdict == "normal" and res.missing == [] and res.reason is None
    assert all("judgeable" not in f for f in res.data["findings"].values())
    text = summarize("host.usage_trend", res.to_dict(), {})
    assert text is not None and text.startswith("모든 지표 평소 범위")


# ── R-E · 결과 한 줄 ────────────────────────────────────────────────────────


def _s4(cpu: dict) -> dict:
    return {"action": "host.usage_trend", "status": "ok", "data": {"findings": {"cpu": cpu}}, "missing": []}


def test_cpu_owner_reads_none_as_missing() -> None:
    unjudged = {"is_anomalous": None, "judgeable": False, "reason": "cpu: 사건 구간 데이터 없음"}
    value, ev = read_cpu_owner({"S4": _s4(unjudged)})
    assert value is None and ev.reason == "호스트 CPU 추이 결손"
    normal, _ = read_cpu_owner({"S4": _s4({"is_anomalous": False, "peak_value": 30.0})})
    assert normal == "none"  # 판정 가능한 「이상 없음」은 종전 그대로


def test_summary_line_never_says_all_normal_with_unjudged() -> None:
    sec = {"action": "host.usage_trend", "status": "ok", "verdict": None, "missing": ["disk_io"],
           "data": {"findings": {"cpu": {"is_anomalous": False, "peak_value": 30.0},
                                 "disk_io": {"is_anomalous": None, "peak_value": 5.0}}}}
    text = summarize("host.usage_trend", sec, {})
    assert text is not None and not text.startswith("모든 지표 평소 범위")
    assert text.startswith("판단 보류")
