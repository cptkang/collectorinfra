"""결정적 상관 계산 (plans/50 §6 · SPEC-evidence-correlation · D-197).

기준시각 좌표계(`reference_time`, 상대 분 `t_offset_min` — 음수가 이전)에서 알람과 지표 이상을 병합해
"무엇이 먼저 일어났는가"를 계산한다. **벤더 중립**: 시각·수치·라벨만 다루며 관측 제품의 어휘(테이블·
컬럼·도구명)는 여기 두지 않는다(`sre_agent`는 overfit 게이트 밖이라 설계 규율로 지킨다 — §6 제약).

domain 계층 — 표준 라이브러리만 의존한다. now()를 쓰지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import mean, pstdev

# z-score 급등 임계(§6.2 기본 3.0) · 지속 판정 최소 연속 구간.
Z_THRESHOLD = 3.0
SUSTAINED_MIN_POINTS = 2
BASELINE_MIN_POINTS = 3


@dataclass(frozen=True)
class AlarmPoint:
    """사건 구간의 알람 1건(기준시각 좌표계로 옮기기 전 원시 시각)."""

    time: str          # ISO 8601
    severity: int      # 0=해소
    name: str
    resource: str = ""
    status: str | None = None
    server: str = ""   # plans/91 1-3 — 연관 서버 알람이면 서버명(대표 서버는 빈 값 = 종전 표기)


@dataclass(frozen=True)
class MetricSeries:
    """지표 1종의 사건 구간 시계열 + baseline 값."""

    name: str
    points: tuple[tuple[str, float], ...]   # (ISO 시각, 값) — 사건 구간, 시간순
    baseline: tuple[float, ...]             # 사건 직전 N기간 값
    granularity_minutes: int
    abs_threshold: float | None = None      # 절대 임계(사용률 등). None이면 z-score만


@dataclass(frozen=True)
class ChangePoint:
    """변경 이벤트 1건(plans/91 1-2 · C′-2) — 시각·설명·유형만(벤더 중립)."""

    time: str          # ISO 8601(naive)
    description: str
    lifecycle: str = ""


@dataclass(frozen=True)
class TimelineItem:
    t_offset_min: int
    kind: str        # "metric" | "change" | "alarm"
    detail: str


# 타임라인 같은 offset의 표시 순서 — 지표 → 변경 → 알람(종전 metric < alarm 순서 불변).
_KIND_ORDER = {"metric": 0, "change": 1, "alarm": 2}


@dataclass(frozen=True)
class CorrelationResult:
    reference_time: str
    timeline: tuple[TimelineItem, ...]
    metric_findings: dict[str, dict]
    alarm_summary: dict
    leading_signal: str | None
    notes: tuple[str, ...] = field(default_factory=tuple)
    change_finding: dict | None = None   # plans/91 1-2 — 변경 오버레이 off/미수집이면 None(키 자체를 내지 않는다)

    def to_dict(self) -> dict:
        out = {
            "reference_time": self.reference_time,
            "timeline": [
                {"t_offset_min": t.t_offset_min, "kind": t.kind, "detail": t.detail} for t in self.timeline
            ],
            "metric_findings": self.metric_findings,
            "alarm_summary": self.alarm_summary,
            "leading_signal": self.leading_signal,
            "notes": list(self.notes),
        }
        if self.change_finding is not None:
            out["change_finding"] = self.change_finding
        return out


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(str(iso).strip()).replace(tzinfo=None)


def offset_minutes(reference_time: str, when: str) -> int:
    """기준시각 대비 상대 분(음수=이전). 초 이하는 버린다."""
    delta = _parse(when) - _parse(reference_time)
    return int(delta.total_seconds() // 60)


def format_offset(t_offset_min: int) -> str:
    """상대 분 → `T-15m` / `T+2m` / `T+0m`."""
    sign = "-" if t_offset_min < 0 else "+"
    return f"T{sign}{abs(int(t_offset_min))}m"


def metric_finding(series: MetricSeries, reference_time: str) -> tuple[dict, list[str]]:
    """지표 1종의 이상 판정(§6.2). (finding, notes)를 돌려준다 — 판정 불가 사유는 notes에 남긴다."""
    notes: list[str] = []
    finding: dict = {
        "is_anomalous": False,
        "kind": None,
        "peak_value": None,
        "peak_time": None,
        "onset_offset_min": None,
        "z_score": None,
        "baseline_mean": None,
        "baseline_std": None,
        "points": len(series.points),
    }
    if not series.points:
        notes.append(f"{series.name}: 사건 구간 데이터 없음")
        return finding, notes

    values = [v for _, v in series.points]
    peak_idx = max(range(len(values)), key=lambda i: values[i])
    finding["peak_value"] = values[peak_idx]
    finding["peak_time"] = series.points[peak_idx][0]

    z_ok = len(series.baseline) >= BASELINE_MIN_POINTS
    mu = sigma = None
    if z_ok:
        mu, sigma = mean(series.baseline), pstdev(series.baseline)
        finding["baseline_mean"], finding["baseline_std"] = round(mu, 3), round(sigma, 3)
        if sigma > 0:
            finding["z_score"] = round((max(values) - mu) / sigma, 2)
        else:
            notes.append(f"{series.name}: baseline 분산 0 — z-score 미판정(절대 임계만 적용)")
    else:
        notes.append(f"{series.name}: baseline 부족(n={len(series.baseline)}<{BASELINE_MIN_POINTS}) — z-score 미판정")

    def _exceeds(v: float) -> bool:
        if finding["z_score"] is not None and sigma and (v - mu) / sigma >= Z_THRESHOLD:  # type: ignore[operator]
            return True
        return series.abs_threshold is not None and v >= series.abs_threshold

    flags = [_exceeds(v) for v in values]
    if not any(flags):
        return finding, notes

    onset_idx = flags.index(True)
    run = 0
    longest = 0
    for f in flags:
        run = run + 1 if f else 0
        longest = max(longest, run)
    finding["is_anomalous"] = True
    finding["kind"] = "sustained" if longest >= SUSTAINED_MIN_POINTS else "spike"
    finding["onset_offset_min"] = offset_minutes(reference_time, series.points[onset_idx][0])
    return finding, notes


def alarm_summary(reference_time: str, alarms: list[AlarmPoint]) -> dict:
    by_sev: dict[str, int] = {}
    for a in alarms:
        by_sev[str(a.severity)] = by_sev.get(str(a.severity), 0) + 1
    offsets = sorted(offset_minutes(reference_time, a.time) for a in alarms)
    return {
        "count": len(alarms),
        "by_severity": by_sev,
        "names": sorted({a.name for a in alarms}),
        "first_offset_min": offsets[0] if offsets else None,
        "last_offset_min": offsets[-1] if offsets else None,
        "resolved": sum(1 for a in alarms if a.severity == 0),
    }


def _change_label(c: ChangePoint) -> str:
    return f"[{c.lifecycle}] {c.description}" if c.lifecycle else c.description


def change_finding(reference_time: str, changes: list[ChangePoint], first_alarm_offset: int | None) -> dict:
    """plans/91 1-2 — lookback 내 변경 유무·최근 변경 offset·첫 알람 선행 여부(결정적)."""
    offsets = sorted(offset_minutes(reference_time, c.time) for c in changes)
    last = offsets[-1] if offsets else None
    before = (last < first_alarm_offset) if (last is not None and first_alarm_offset is not None) else None
    ordered = sorted(changes, key=lambda c: offset_minutes(reference_time, c.time), reverse=True)
    return {
        "count": len(changes),
        "last_change_offset_min": last,
        "before_first_alarm": before,
        "descriptions": [_change_label(c) for c in ordered[:3]],
    }


def merge_timeline(
    reference_time: str, alarms: list[AlarmPoint], findings: dict[str, dict],
    changes: list[ChangePoint] | None = None,
) -> list[TimelineItem]:
    """§6.1 — 알람 + 지표 이상(onset·peak) + 변경 이벤트를 기준시각 좌표계로 정렬한다."""
    items: list[TimelineItem] = []
    for c in changes or []:
        items.append(TimelineItem(offset_minutes(reference_time, c.time), "change", _change_label(c)))
    for a in alarms:
        label = "해소" if a.severity == 0 else f"severity {a.severity}"
        res = f" ({a.resource})" if a.resource else ""
        host = f"[{a.server}] " if a.server else ""
        items.append(TimelineItem(offset_minutes(reference_time, a.time), "alarm", f"{host}[{label}] {a.name}{res}"))
    for name, f in findings.items():
        if not f.get("is_anomalous"):
            continue
        z = f" z={f['z_score']}" if f.get("z_score") is not None else ""
        items.append(
            TimelineItem(
                int(f["onset_offset_min"]), "metric",
                f"{name} {f['kind']} 시작 (peak {f['peak_value']} @ {f['peak_time']}{z})",
            )
        )
    items.sort(key=lambda t: (t.t_offset_min, _KIND_ORDER.get(t.kind, 2), t.detail))
    return items


def correlate(
    reference_time: str,
    alarms: list[AlarmPoint],
    series: list[MetricSeries],
    notes: list[str] | None = None,
    changes: list[ChangePoint] | None = None,
    related_alarms: dict[str, list[AlarmPoint]] | None = None,
) -> CorrelationResult:
    """§6.1~6.4 — 알람·지표(·변경·연관 서버 알람)를 병합해 선행 신호와 한계를 결정적으로 산출한다.

    `changes`가 None이면(오버레이 off·미수집) 결과는 종전과 동일하다 — `change_finding=None`.
    `related_alarms`(plans/91 1-3)는 타임라인·notes에만 실린다 — 요약·선행성은 대표 서버 알람 기준을 유지한다.
    """
    all_notes: list[str] = list(notes or [])
    findings: dict[str, dict] = {}
    for s in series:
        f, n = metric_finding(s, reference_time)
        findings[s.name] = f
        all_notes.extend(n)

    summary = alarm_summary(reference_time, alarms)
    if not alarms:
        all_notes.append("사건 구간 내 알람 0건 — 알람 축 선후 판정 불가")

    # §6.2 선행성: 첫 알람 대비 onset 시차(음수=지표 선행).
    first_alarm = summary["first_offset_min"]
    for f in findings.values():
        if f.get("is_anomalous") and first_alarm is not None:
            f["lead_lag_min"] = int(f["onset_offset_min"]) - int(first_alarm)
        else:
            f["lead_lag_min"] = None

    # §6.3 단일 서버 시차 순서화: 가장 이른 onset의 지표가 선행 신호.
    anomalous = [(f["onset_offset_min"], name) for name, f in findings.items() if f.get("is_anomalous")]
    leading = min(anomalous)[1] if anomalous else None
    if anomalous and first_alarm is not None and min(anomalous)[0] >= first_alarm:
        all_notes.append("이상 지표가 첫 알람보다 앞서지 않음 — 지표 선행 근거 없음(상관 ≠ 인과)")

    # plans/91 1-3 — 연관 서버 첫 알람이 대표 첫 알람보다 앞서면 결정적으로 표기(상관 ≠ 인과).
    timeline_alarms: list[AlarmPoint] = list(alarms)
    for host, points in (related_alarms or {}).items():
        pts = [p if p.server else AlarmPoint(p.time, p.severity, p.name, p.resource, p.status, host) for p in points]
        timeline_alarms.extend(pts)
        offsets = [offset_minutes(reference_time, p.time) for p in pts]
        if offsets and (first_alarm is None or min(offsets) < first_alarm):
            all_notes.append(f"연관 서버 {host}의 첫 알람 {format_offset(min(offsets))} — 대표보다 선행")

    precisions = sorted({s.granularity_minutes for s in series})
    if precisions and max(precisions) > 1:
        all_notes.append(f"지표 정밀도 {'/'.join(str(p) for p in precisions)}분 단위 — 그보다 짧은 선후는 판정 불가")

    return CorrelationResult(
        reference_time=_parse(reference_time).isoformat(),
        timeline=tuple(merge_timeline(reference_time, timeline_alarms, findings, changes)),
        metric_findings=findings,
        alarm_summary=summary,
        leading_signal=leading,
        notes=tuple(dict.fromkeys(all_notes)),
        change_finding=change_finding(reference_time, changes, first_alarm) if changes is not None else None,
    )


__all__ = [
    "Z_THRESHOLD", "SUSTAINED_MIN_POINTS", "BASELINE_MIN_POINTS",
    "AlarmPoint", "ChangePoint", "MetricSeries", "TimelineItem", "CorrelationResult",
    "offset_minutes", "format_offset", "metric_finding", "alarm_summary", "change_finding", "merge_timeline", "correlate",
]
