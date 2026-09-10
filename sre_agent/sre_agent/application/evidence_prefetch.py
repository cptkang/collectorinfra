"""사건 구간 증거 사전수집 (plans/50 G4-b · SPEC-evidence-correlation · D-197).

조사 LLM이 도구를 부르기 전에 코드가 사건 구간의 **전 알람 + 지표 4종(baseline 포함)(+ 변경 이력 1건 — plans/91 1-2 플래그)** 을 결정적으로
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

from sre_agent.domain.correlation import AlarmPoint, ChangePoint, CorrelationResult, MetricSeries, correlate

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
TOOL_CHANGES = "polestar_change_history"   # plans/91 1-2 — 배치 말미(플래그 on일 때만)


@dataclass(frozen=True)
class EvidenceScope:
    source: str
    server_name: str
    reference_time: str
    lookback_minutes: int
    baseline_periods: int = 24
    granularity: str = "h"
    change_overlay: bool = False   # plans/91 1-2 — True면 변경 이력 1건을 배치 말미에 더한다
    related_servers: tuple[str, ...] = ()   # plans/91 1-3 — 연관 서버(알람 1건씩만 추가 수집)


def scope_from_job(
    job, *, baseline_periods: int = 24, change_overlay: bool = False, related_hosts_max: int = 0,
) -> EvidenceScope | None:  # noqa: ANN001 — JobLike
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
    # plans/91 1-3: 게이트 E4가 판정한 root 서버 **이름**(meta.root_resource_name)만 소비 — 토폴로지 재조회 없음(D-207).
    related: tuple[str, ...] = ()
    if int(related_hosts_max) > 0:
        meta = payload.get("meta") or {}
        root_name = str((meta.get("root_resource_name") if isinstance(meta, dict) else None) or "").strip()
        if root_name and root_name != server:
            related = (root_name,)[: int(related_hosts_max)]
    return EvidenceScope(
        source=source, server_name=server, reference_time=ref,
        lookback_minutes=int(getattr(job, "lookback_minutes", None) or 60),
        baseline_periods=int(baseline_periods), change_overlay=bool(change_overlay), related_servers=related,
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
    if scope.change_overlay:
        # 말미에 두어 알람·지표 인덱스 규약(raws[0] · raws[1:5])을 깨지 않는다.
        calls.append((TOOL_CHANGES, dict(base)))
    for host in scope.related_servers:
        # plans/91 1-3 — 연관 서버는 **알람 1건만**(지표는 대표 서버만 · 부하 가드 R-4).
        calls.append((TOOL_ALARMS, {**base, "server_name": host}))
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


def _alarms(data: dict, notes: list[str], server: str = "") -> list[AlarmPoint]:
    out: list[AlarmPoint] = []
    for row in data.get("rows") or []:
        try:
            t = datetime.fromisoformat(str(row.get("alarm_time")).strip()).replace(tzinfo=None)
            out.append(AlarmPoint(
                time=t.isoformat(), severity=int(row.get("severity") or 0),
                name=str(row.get("alarm_name") or "?"), resource=str(row.get("resource_name") or ""),
                status=None if row.get("alarm_status") is None else str(row.get("alarm_status")),
                server=server,
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


def _changes(data: dict, scope: EvidenceScope, notes: list[str]) -> list[ChangePoint] | None:
    """변경 이력 행 → ChangePoint. 미지원 소스(DB2)는 결정적 한계 문장을 남기고 None."""
    if data.get("unsupported"):
        notes.append(f"변경 이력은 PostgreSQL 소스만 지원 — 이 소스({scope.source})는 변경 오버레이 없음")
        return None
    out: list[ChangePoint] = []
    for row in data.get("rows") or []:
        try:
            when = datetime.fromtimestamp(int(row.get("event_time"))).replace(tzinfo=None)
        except (TypeError, ValueError, OSError, OverflowError):
            notes.append("변경 이력 행 시각 해석 실패(건너뜀)")
            continue
        out.append(ChangePoint(time=when.isoformat(), description=str(row.get("description") or "?"),
                               lifecycle=str(row.get("lifecycle_type") or "")))
    return out


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
    for (kind, abs_thr), raw in zip(METRIC_KINDS, raws[1:1 + len(METRIC_KINDS)]):
        data, err = _load(raw)
        if data is None:
            notes.append(f"{kind} 축 결손: {err}")
            continue
        series.append(_series(kind, abs_thr, data, scope, notes))

    idx = 1 + len(METRIC_KINDS)
    changes: list[ChangePoint] | None = None
    if scope.change_overlay:
        data, err = _load(raws[idx])
        idx += 1
        if data is None:
            notes.append(f"변경 축 결손: {err}")
        else:
            changes = _changes(data, scope, notes)

    related: dict[str, list[AlarmPoint]] | None = None
    if scope.related_servers:
        related = {}
        for host, raw in zip(scope.related_servers, raws[idx: idx + len(scope.related_servers)]):
            data, err = _load(raw)
            if data is None:
                notes.append(f"연관 서버 {host} 알람 결손: {err}")   # 대표 결과는 보존(부분 반환)
                continue
            related[host] = _alarms(data, notes, server=host)

    return correlate(scope.reference_time, alarms, series, notes, changes=changes, related_alarms=related)


__all__ = ["EvidenceScope", "METRIC_KINDS", "TOOL_CHANGES", "scope_from_job", "build_calls", "prefetch_and_correlate"]
