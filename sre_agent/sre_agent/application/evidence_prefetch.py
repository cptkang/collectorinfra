"""사건 구간 증거 사전수집 (plans/50 G4-b · SPEC-evidence-correlation · D-197).

조사 LLM이 도구를 부르기 전에 코드가 사건 구간의 **전 알람 + 지표 4종(baseline 포함)(+ 변경 이력 1건 — plans/91 1-2 플래그)** 을 결정적으로
수집해 `domain.correlation.correlate`에 넘긴다. 관측 제품 어휘(도구명·행 컬럼·지표 종류)는 이 모듈까지만
둔다 — `correlation.py`는 벤더 중립(§6 제약).

호출자는 **배치 콜러블을 주입**받는다(`diagnose_fn`·`briefing_fn` 주입 패턴 계승) — 테스트는 가짜 콜러블로
실 서버 없이 검증한다. 축별 실패는 `notes`에 결손으로 남기고 나머지로 계산한다(부분 반환 보장).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from sre_agent.domain.correlation import (
    AlarmPoint,
    ChangePoint,
    CorrelationResult,
    EpisodePoint,
    MetricSeries,
    correlate,
)
from sre_agent.domain.cross_source_episode import (
    active_episode,
    episode_from_payload,
    episode_points,
)
from sre_agent.domain.investigation_limits import is_apm_trigger
from sre_agent.domain.topology_neighbors import (
    NEIGHBOR_LEAD_ANCHOR,
    Neighbor,
    link_neighbor,
    neighbor_finding,
    select_neighbors,
    topology_neighbors,
)

logger = logging.getLogger(__name__)

BatchCaller = Callable[[Sequence[tuple[str, dict]]], list[str]]
#: 세션 자체 실패 때 배치 호출자가 전 호출에 싣는 오류 문구의 머리
#: (`infrastructure/mcp_tool_client`와 같은 값).
SESSION_FAILURE_PREFIX = "MCP 세션 실패: "

#: 지표 종류 → 절대 임계(사용률 %). None이면 z-score만(IO는 절대 기준이 없다).
METRIC_KINDS: tuple[tuple[str, float | None], ...] = (
    ("cpu", 90.0), ("memory", 90.0), ("filesystem", 90.0), ("disk_io", None),
)
_STAT_DATE_FMT = {"h": "%Y%m%d%H", "d": "%Y%m%d", "m": "%Y%m"}
_GRANULARITY_MINUTES = {"h": 60, "d": 1440, "m": 43200}
TOOL_ALARMS = "polestar_incident_alarms"
TOOL_METRIC = "polestar_metric_trend"
TOOL_CHANGES = "polestar_change_history"   # plans/91 1-2 — 배치 말미(플래그 on일 때만)
TOOL_TOPOLOGY = "polestar_topology"   # plans/198 I-7 — 배치 맨 끝 1건(플래그 on일 때만 · 1홉)
# plans/153 R-4 — APM(제니퍼) 트리거인데 폴스타 소스를 모를 때. 제니퍼 dbId를 폴스타 도구에 넘기면
# mcp_server가 「알 수 없는 소스」로 전 축을 결손 처리한다 — 호출 자체를 하지 않고 이 사실만 남긴다.
INFRA_SOURCE_UNKNOWN_NOTE = "인프라 소스 미상 — 폴스타 사전수집 생략"


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
    # plans/153 R-4 — True면 폴스타 도구를 호출하지 않는다(source 미상).
    skip_infra: bool = False
    # plans/153 §4.6 — 사건 멤버(추가 호출 없이 타임라인 편입)와 그 해석 노트.
    episode_points: tuple[EpisodePoint, ...] = ()
    episode_notes: tuple[str, ...] = ()
    # plans/198 I-7 — 토폴로지 1홉 이웃 상한(0 = off · 비트 동일)과 확정 연결 이웃(D-348).
    neighbors_max: int = 0
    link_neighbor: Neighbor | None = None


def scope_from_job(
    job, *, baseline_periods: int = 24, change_overlay: bool = False, related_hosts_max: int = 0,
    cross_source: bool = False, neighbors_max: int = 0,
) -> EvidenceScope | None:  # noqa: ANN001 — JobLike
    """잡에서 사전수집 범위를 뽑는다. 두 진입점의 payload 형태가 다르다(`_host_key`와 같은 이유):

        알람 트리거  payload["event"]["dbId"|"serverName"]
        pull 진단    payload["db_id"|"server_name"]

    기준시각·소스·서버명 중 하나라도 없으면 None(사전수집 불가 — 조사는 그대로 진행).

    plans/153 R-4(플래그 무관 결함 교정): APM 트리거(`is_apm_trigger` — 지침·dispatcher와
    같은 판정)의 dbId는 제니퍼 소스라 폴스타 도구의 source가 될 수 없다.
    `cross_source`(CROSS_SOURCE_INVESTIGATION_ENABLED)이고 `meta.episode.infra_db_ids[0]`가
    있으면 그것을 쓰고, 아니면 폴스타 축을 호출하지 않는 범위(`skip_infra`)를 돌려준다.
    `cross_source`이고 두 소스 멤버가 있는 사건이면 멤버를 타임라인 점으로 싣는다.

    `neighbors_max`(plans/198 I-7 · 0 = off)면 이웃 상한과 확정 연결 이웃
    (`meta.hints.polestar_link` — 이전 확정 연결만)을 범위에 싣는다.
    """
    ref = getattr(job, "reference_time", None)
    if not ref:
        return None
    payload = getattr(job, "payload", None) or {}
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") or {}
    server = str(event.get("serverName") or payload.get("server_name") or "").strip()
    skip_infra = False
    if is_apm_trigger(payload):
        episode = episode_from_payload(payload) if cross_source else None
        source = episode.infra_db_ids[0] if episode is not None and episode.infra_db_ids else ""
        skip_infra = not source
    else:
        source = str(event.get("dbId") or payload.get("db_id") or "").strip()
    if (not source and not skip_infra) or not server:
        return None
    points: tuple[EpisodePoint, ...] = ()
    point_notes: tuple[str, ...] = ()
    xs = active_episode(payload, cross_source, ref)
    if xs is not None:
        pts, pnotes = episode_points(xs)
        points, point_notes = tuple(pts), tuple(pnotes)
    n_max = max(int(neighbors_max), 0)
    link = _confirmed_link(payload) if n_max else None
    if skip_infra:
        return EvidenceScope(
            source="", server_name=server, reference_time=ref,
            lookback_minutes=int(getattr(job, "lookback_minutes", None) or 60),
            baseline_periods=int(baseline_periods), skip_infra=True,
            episode_points=points, episode_notes=point_notes,
            neighbors_max=n_max, link_neighbor=link,
        )
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
        episode_points=points, episode_notes=point_notes, neighbors_max=n_max, link_neighbor=link,
    )


def _confirmed_link(payload: dict) -> Neighbor | None:
    """APM 알람의 확정 연결 힌트(`meta.hints.polestar_link` — D-348)를 이웃으로. 형식 검증은
    플레이북과 같은 해석(`playbook_runner.polestar_link`)을 쓰고, 확정 여부는 도메인이 정한다."""
    # 지연 import — playbook_runner → playbook_steps → 이 모듈 순환을 피한다(플래그 on일 때만 적재)
    from sre_agent.application.playbook_runner import polestar_link

    return link_neighbor(polestar_link(payload))


def build_calls(scope: EvidenceScope) -> list[tuple[str, dict]]:
    """배치 호출 목록 — 전 알람 1 + 지표 4(순서 고정, 결과 해석이 이 순서에 의존).

    `skip_infra`(plans/153 R-4)면 빈 목록.
    """
    if scope.skip_infra:
        return []
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
    if scope.neighbors_max > 0:
        # plans/198 I-7 — 맨 끝에 두어 앞 인덱스 규약을 깨지 않는다. 1홉만.
        calls.append((TOOL_TOPOLOGY, {"source": scope.source, "server_name": scope.server_name,
                                      "max_hops": 1}))
    return calls


def _neighbor_calls(scope: EvidenceScope, neighbors: Sequence[Neighbor]) -> list[tuple[str, dict]]:
    """이웃 알람 배치(plans/198 I-7) — 이웃마다 **알람 1건만**(연관 서버와 같은 부하 가드)."""
    base = {"reference_time": scope.reference_time, "lookback_minutes": scope.lookback_minutes}
    return [(TOOL_ALARMS, {"source": n.source, "server_name": n.name, **base}) for n in neighbors]


@dataclass
class _NeighborFetch:
    """이웃 수집 중간 결과(plans/198 I-7) — 상관 계산 전후로 나눠 쓴다."""

    picked: list[Neighbor]
    alarms: dict[str, list[AlarmPoint]]
    fetched: dict[str, list[AlarmPoint]]
    errors: dict[str, str]
    dropped: int
    tool_calls: int
    notes: list[str]


def _fetch_neighbors(
    scope: EvidenceScope,
    call_batch: BatchCaller,
    topology_raw: str | None,
    known: dict[str, list[AlarmPoint]],
    deadline: float | None = None,
) -> _NeighborFetch:
    """토폴로지 1홉 이웃을 고르고 이웃 알람을 **배치 1회**로 부른다(plans/198 I-7).

    이웃은 확정 연결(D-348)과 토폴로지 행만이다. 이미 받은 연관 서버 알람(`known`)은 다시 부르지
    않는다. 결손은 노트로 남기고 0건으로 단정하지 않는다. 이웃 배치가 예외로 끝나거나
    `deadline`(monotonic 초)까지 끝나지 않으면 이웃 전부를 결손으로 남긴다(대표 결과는 보존).
    """
    notes: list[str] = []
    candidates: list[Neighbor] = [scope.link_neighbor] if scope.link_neighbor else []
    tool_calls = 0
    if topology_raw is not None:
        tool_calls += 1
        data, err = _load(topology_raw)
        if data is None:
            notes.append(f"토폴로지 이웃 결손: {err}")
        else:
            candidates.extend(topology_neighbors(data.get("rows") or [], scope.source))
    picked, dropped = select_neighbors(scope.server_name, candidates, scope.neighbors_max)
    if dropped:
        notes.append(f"이웃 {dropped}대는 상한 {scope.neighbors_max}대 밖이라 조회하지 않음")
    pending = [n for n in picked if n.name not in known]
    alarms = {n.name: known[n.name] for n in picked if n.name in known}
    fetched: dict[str, list[AlarmPoint]] = {}
    errors: dict[str, str] = {}
    if pending:
        calls = _neighbor_calls(scope, pending)
        tool_calls += len(calls)
        try:
            raws = list(_call_until(call_batch, calls, deadline))
        except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001 — 대표 상관은 보존(부분 반환)
            # 재검토 Major1 — 이웃 배치 실패가 사전수집 전체(대표 서버 상관)를 잃게 하지 않는다.
            if isinstance(exc, _BudgetExhaustedError):
                tool_calls -= len(calls)   # 부르지 않았다
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.warning("이웃 알람 배치 실패 — 대표 상관은 보존: %s", reason)
            raws = [json.dumps({"error": reason}, ensure_ascii=False)] * len(calls)
        raws += [json.dumps({"error": "응답 누락"})] * (len(calls) - len(raws))
        for n, raw in zip(pending, raws):
            data, err = _load(raw)
            if data is None:
                errors[n.name] = str(err)
                notes.append(f"이웃 {n.name} 알람 결손: {err}")
                continue
            fetched[n.name] = _alarms(data, notes, server=n.name)
    alarms.update(fetched)
    return _NeighborFetch(picked, alarms, fetched, errors, dropped, tool_calls, notes)


class _BudgetExhaustedError(TimeoutError):
    """이웃 배치를 부르기 전에 사전수집 시간이 이미 다 쓰였다(호출하지 않음)."""


def _call_until(
    call_batch: BatchCaller, calls: list[tuple[str, dict]], deadline: float | None
) -> list[str]:
    """`deadline`(monotonic 초)까지만 배치를 기다린다. None이면 그대로 부른다.

    남은 시간이 없으면 부르지 않고, 만료되면 `TimeoutError` — 매달린 호출 스레드는 데몬으로 버린다
    (도구 1건 상한은 배치 호출자가 이미 강제한다 · dispatcher `_join_timebox`와 같은 방식).
    """
    if deadline is None:
        return list(call_batch(calls))
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _BudgetExhaustedError("사전수집 타임박스 소진 — 이웃 알람 조회 생략")
    result: list[list[str]] = []
    error: list[BaseException] = []

    def _target() -> None:
        try:
            result.append(list(call_batch(calls)))
        except BaseException as exc:  # noqa: BLE001 — 원예외를 호출 스레드로 그대로 전달
            error.append(exc)

    worker = threading.Thread(target=_target, name="sre-prefetch-neighbors", daemon=True)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise TimeoutError(f"이웃 알람 배치가 남은 사전수집 시간 {remaining:.0f}s 안에 끝나지 않음")
    if error:
        raise error[0]
    return result[0]


def _attach_neighbors(
    result: CorrelationResult, scope: EvidenceScope, nf: _NeighborFetch
) -> CorrelationResult:
    """상관 결과에 `neighbors` 키(이웃·외부 원인 후보·도구 호출 수)를 더한다 — 가산만(D-197)."""
    finding = neighbor_finding(
        scope.reference_time, result.alarm_summary.get("first_offset_min"), nf.picked, nf.alarms,
        errors=nf.errors, limit=scope.neighbors_max, dropped=nf.dropped,
        tool_calls=nf.tool_calls, notes=nf.notes,
    )
    return replace(result, neighbors=finding)


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


def prefetch_and_correlate(
    scope: EvidenceScope, call_batch: BatchCaller, *, neighbor_budget_s: float | None = None
) -> CorrelationResult:
    """도구 배치를 호출하고 상관을 계산한다. 축별 실패는 notes로 남기고 계산은 계속한다.

    `skip_infra`(plans/153 R-4)면 배치를 부르지 않고 「인프라 소스 미상」 노트와
    사건 멤버만으로 계산한다.

    `neighbors_max`(plans/198 I-7)가 0보다 크면 첫 배치 말미의 토폴로지 1건과 확정 연결로 이웃을
    고르고 이웃 알람을 배치 1회 더 부른다 — 이웃 알람은 연관 서버 알람으로 타임라인에 들어가고,
    이웃·외부 원인 후보·도구 호출 수는 `neighbors` 키로 더한다. `skip_infra`면 확정 연결 이웃만
    (그 연결의 소스로) 조회한다. `neighbor_budget_s`(이 호출 시작부터의 초)가 있으면 이웃 배치는
    그 안에서만 기다린다 — 사전수집 타임박스를 대표 수집과 나눠 쓰므로, 남은 시간을 넘기면 이웃만
    결손으로 남긴다. 이웃 알람의 선행 판정 기준점은 `neighbors` 블록과 같다(`lead_anchor=0`).
    """
    deadline = None if neighbor_budget_s is None else time.monotonic() + float(neighbor_budget_s)
    if scope.skip_infra:
        nf = (_fetch_neighbors(scope, call_batch, None, {}, deadline)
              if scope.neighbors_max > 0 and scope.link_neighbor else None)
        result = correlate(
            scope.reference_time, [], [],
            [INFRA_SOURCE_UNKNOWN_NOTE, *scope.episode_notes, *(nf.notes if nf else [])],
            related_alarms=(nf.fetched or None) if nf else None,
            episode_points=list(scope.episode_points), alarm_axis=False,
            lead_anchor=NEIGHBOR_LEAD_ANCHOR if nf else None,
        )
        return _attach_neighbors(result, scope, nf) if nf else result
    calls = build_calls(scope)
    raws = call_batch(calls)
    if len(raws) != len(calls):
        raws = list(raws) + [json.dumps({"error": "응답 누락"})] * (len(calls) - len(raws))
    # plans/188 C4′ — 전 호출이 같은 세션 실패면 축별 결손 대신 노트 1줄(사유는 하나다).
    errs = {_load(raw)[1] for raw in raws}
    if len(errs) == 1:
        (only,) = errs
        if only and only.startswith(SESSION_FAILURE_PREFIX):
            result = correlate(
                scope.reference_time, [], [], [f"사전수집 {only}", *scope.episode_notes],
                episode_points=list(scope.episode_points) or None, alarm_axis=False,
            )
            if scope.neighbors_max > 0:   # 이웃 배치는 부르지 않는다(같은 세션 실패)
                result = _attach_neighbors(result, scope, _NeighborFetch(
                    [], {}, {}, {}, 0, 1, [f"토폴로지 이웃 결손: {only}"]))
            return result
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

    nf: _NeighborFetch | None = None
    if scope.neighbors_max > 0:
        idx += len(scope.related_servers)
        nf = _fetch_neighbors(scope, call_batch, raws[idx], dict(related or {}), deadline)
        notes.extend(nf.notes)
        if nf.fetched:
            related = {**(related or {}), **nf.fetched}

    notes.extend(scope.episode_notes)
    result = correlate(
        scope.reference_time, alarms, series, notes, changes=changes, related_alarms=related,
        episode_points=list(scope.episode_points) or None,
        alarm_axis=alarms_data is not None,   # plans/188 C5 — 조회 실패는 「알람 0건」이 아니다
        lead_anchor=NEIGHBOR_LEAD_ANCHOR if nf is not None else None,
    )
    return _attach_neighbors(result, scope, nf) if nf is not None else result


__all__ = ["EvidenceScope", "METRIC_KINDS", "TOOL_CHANGES", "TOOL_TOPOLOGY",
           "INFRA_SOURCE_UNKNOWN_NOTE",
           "scope_from_job", "build_calls", "prefetch_and_correlate"]
