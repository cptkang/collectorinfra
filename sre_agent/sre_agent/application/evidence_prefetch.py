"""사건 구간 증거 사전수집 (plans/50 G4-b · SPEC-evidence-correlation · D-194).

조사 LLM이 도구를 부르기 전에 코드가 사건 구간의 **전 알람 + 지표 4종(baseline 포함)** 을 결정적으로
수집해 `domain.correlation.correlate`에 넘긴다. 관측 제품 어휘(도구명·행 컬럼·지표 종류)는 이 모듈까지만
둔다 — `correlation.py`는 벤더 중립(§6 제약).

호출자는 **배치 콜러블을 주입**받는다(`diagnose_fn`·`briefing_fn` 주입 패턴 계승) — 테스트는 가짜 콜러블로
실 서버 없이 검증한다. 축별 실패는 `notes`에 결손으로 남기고 나머지로 계산한다(부분 반환 보장).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sre_agent.domain.correlation import AlarmPoint, CorrelationResult, MetricSeries, correlate

logger = logging.getLogger(__name__)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]

#: 지표 종류 → 절대 임계(사용률 %). None이면 z-score만(IO는 절대 기준이 없다).
METRIC_KINDS: tuple[tuple[str, float | None], ...] = (
    ("cpu", 90.0), ("memory", 90.0), ("filesystem", 90.0), ("disk_io", None),
)
_STAT_DATE_FMT = {"h": "%Y%m%d%H", "d": "%Y%m%d", "m": "%Y%m"}
_GRANULARITY_MINUTES = {"h": 60, "d": 1440, "m": 43200}
TOOL_ALARMS = "polestar_incident_alarms"
TOOL_METRIC = "polestar_metric_trend"


@dataclass(frozen=True)
class EvidenceScope:
    source: str
    server_name: str
    reference_time: str
    lookback_minutes: int
    baseline_periods: int = 24
    granularity: str = "h"


def scope_from_job(job, *, baseline_periods: int = 24) -> EvidenceScope | None:  # noqa: ANN001 — JobLike
    """잡에서 사전수집 범위를 뽑는다. 두 진입점의 payload 형태가 다르다(`_host_key`와 같은 이유):

        알람 트리거  payload["event"]["dbId"|"serverName"]
        pull 진단    payload["db_id"|"server_name"]

    기준시각·소스·서버명 중 하나라도 없으면 None(사전수집 불가 — 조사는 그대로 진행).
    """
    ref = getattr(job, "reference_time", None)
    if not ref:
        return None
    payload = getattr(job, "payload", None) or {}
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") or {}
    source = str(event.get("dbId") or payload.get("db_id") or "").strip()
    server = str(event.get("serverName") or payload.get("server_name") or "").strip()
    if not source or not server:
        return None
    return EvidenceScope(
        source=source, server_name=server, reference_time=ref,
        lookback_minutes=int(getattr(job, "lookback_minutes", None) or 60),
        baseline_periods=int(baseline_periods),
    )


def build_calls(scope: EvidenceScope) -> list[tuple[str, dict]]:
    """배치 호출 목록 — 전 알람 1 + 지표 4(순서 고정, 결과 해석이 이 순서에 의존)."""
    base = {"source": scope.source, "server_name": scope.server_name,
            "reference_time": scope.reference_time, "lookback_minutes": scope.lookback_minutes}
    calls: list[tuple[str, dict]] = [(TOOL_ALARMS, dict(base))]
    for kind, _ in METRIC_KINDS:
        calls.append((TOOL_METRIC, {**base, "kind": kind, "granularity": scope.granularity,
                                    "periods": scope.lookback_minutes // _GRANULARITY_MINUTES[scope.granularity] + 2,
                                    "baseline_periods": scope.baseline_periods}))
    return calls


def _load(raw: str) -> tuple[dict | None, str | None]:
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None, "응답 JSON 파싱 실패"
    if not isinstance(data, dict):
        return None, "응답 형식 오류"
    if data.get("error"):
        return None, str(data["error"])
    return data, None


def _alarms(data: dict, notes: list[str]) -> list[AlarmPoint]:
    out: list[AlarmPoint] = []
    for row in data.get("rows") or []:
        try:
            t = datetime.fromisoformat(str(row.get("alarm_time")).strip()).replace(tzinfo=None)
            out.append(AlarmPoint(
                time=t.isoformat(), severity=int(row.get("severity") or 0),
                name=str(row.get("alarm_name") or "?"), resource=str(row.get("resource_name") or ""),
                status=None if row.get("alarm_status") is None else str(row.get("alarm_status")),
            ))
        except (TypeError, ValueError):
            notes.append("알람 행 시각 해석 실패(건너뜀)")
    return out


def _series(kind: str, abs_threshold: float | None, data: dict, scope: EvidenceScope, notes: list[str]) -> MetricSeries:
    fmt = _STAT_DATE_FMT[scope.granularity]
    window = data.get("window") or {}
    incident_from = str(window.get("stat_date_incident_from") or "")
    incident: list[tuple[str, float]] = []
    baseline: list[float] = []
    for row in data.get("rows") or []:
        sd = str(row.get("stat_date") or "")
        try:
            when = datetime.strptime(sd, fmt).isoformat()
            val = float(row.get("avg_val"))
        except (TypeError, ValueError):
            notes.append(f"{kind}: 지표 행 해석 실패(건너뜀)")
            continue
        if incident_from and sd < incident_from:
            baseline.append(val)
        else:
            incident.append((when, val))
    incident.sort()
    return MetricSeries(
        name=kind, points=tuple(incident), baseline=tuple(baseline),
        granularity_minutes=_GRANULARITY_MINUTES[scope.granularity], abs_threshold=abs_threshold,
    )


def prefetch_and_correlate(scope: EvidenceScope, call_batch: BatchCaller) -> CorrelationResult:
    """도구 배치를 호출하고 상관을 계산한다. 축별 실패는 notes로 남기고 계산은 계속한다."""
    calls = build_calls(scope)
    raws = call_batch(calls)
    if len(raws) != len(calls):
        raws = list(raws) + [json.dumps({"error": "응답 누락"})] * (len(calls) - len(raws))
    notes: list[str] = []

    alarms_data, err = _load(raws[0])
    alarms = _alarms(alarms_data, notes) if alarms_data else []
    if err:
        notes.append(f"알람 축 결손: {err}")

    series: list[MetricSeries] = []
    for (kind, abs_thr), raw in zip(METRIC_KINDS, raws[1:]):
        data, err = _load(raw)
        if data is None:
            notes.append(f"{kind} 축 결손: {err}")
            continue
        series.append(_series(kind, abs_thr, data, scope, notes))

    return correlate(scope.reference_time, alarms, series, notes)


__all__ = ["EvidenceScope", "METRIC_KINDS", "scope_from_job", "build_calls", "prefetch_and_correlate"]
