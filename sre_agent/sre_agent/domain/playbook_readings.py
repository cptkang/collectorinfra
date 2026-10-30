"""플레이북 결정적 판독 규칙 R-A~R-H (plans/187 §4.2 · D-341 ③).

LLM이 아니라 코드가 계산하는 분류다. 입력은 스텝 결과 dict(스텝 ID → `{"action", "status",
"verdict", "verdict_source", "data", "missing"}` — `StepResult.to_dict()` 모양)이고, 출력은
`Readings`다.

- `Readings.values`: 판독 이름 → enum 값(§4.2 어휘). `None` = 판정 불가(입력 결손 · 해당 없음 —
  사유는 `evidence[이름].reason`). 침묵 기본값을 두지 않는다.
- `Readings.context()`: `when`·런북 mitigation·알려진 오류 평가 문맥(`playbook_expr`). `db_split`의
  `n/a`는 결손(`None`)으로 바꿔 넣는다 — 비교가 `UNKNOWN`(판단 보류)이 된다(§5.7.3).
- `Readings.facts`: 판독 enum이 아닌 보조 사실(스텝 ID 아래 · 예 `S2.pool`) — 게이트웨이 판정
  (`was_signals`)을 그대로 인용한다(D-274 ⑤ — 다시 계산하지 않는다).

판독마다 담당 액션(`READING_ACTIONS`)이 있다. 플레이북에 그 액션 스텝이 없으면 판독하지 않는다.
스텝 인자 `readings`(목록)가 있으면 그 스텝이 담당하는 판독을 그 목록으로 좁힌다(예: was.heap의 S1은
트래픽 비례 여부만 대고 R-B를 판독하지 않는다 — `readings: []`). 임계는 [제안] 기본값이며 스텝 인자
`reading_params`(dict)로 재정의한다.

순수 도메인 — 표준 라이브러리와 같은 domain 패키지만 의존한다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from sre_agent.domain.playbook import Playbook

#: R-D 판정 불가 값(§4.2 enum). 다른 판독의 판정 불가는 `None`이다.
NA = "n/a"

#: 판독 → 담당 액션(이 액션 스텝이 플레이북에 있어야 판독한다).
READING_ACTIONS: dict[str, tuple[str, ...]] = {
    "scope": ("scope.resolve_was_targets",),
    "service_spread": ("apm.slow_tx_breakdown",),
    "load_mode": ("apm.golden_signals",),
    "little_check": ("apm.golden_signals",),
    "dominant": ("apm.slow_tx_breakdown",),
    "db_split": ("apm.slow_tx_breakdown",),
    "cpu_owner": ("apm.runtime_and_pool", "host.usage_trend", "host.process_top"),
    "heap_shape": ("apm.runtime_and_pool",),
    "host_saturation": ("host.usage_trend",),
    "fs_eta": ("host.usage_trend",),
    "change_candidates": ("change.recent",),
}

#: 판독 → 그 판독 함수가 실제로 읽는 액션 전부(plans/200 I-1 · 실행 의존 계산용).
#: `READING_ACTIONS`(담당 = 판독 귀속)와 다르다 — 알람 인스턴스(`_alarm_instance` → 대상 확정)와
#: TPS 변화율(`_tps_change_pct` → 골든 시그널)처럼 담당이 아닌 입력도 싣는다. 가드:
#: `tests/test_plan200_w1_deps.py`(기록 프록시로 실제 접근 ⊆ 이 표).
READING_INPUTS: Mapping[str, frozenset[str]] = {
    "scope": frozenset({"scope.resolve_was_targets"}),
    "service_spread": frozenset({"apm.slow_tx_breakdown"}),
    "load_mode": frozenset({"scope.resolve_was_targets", "apm.golden_signals"}),
    "little_check": frozenset(
        {"scope.resolve_was_targets", "apm.golden_signals", "apm.runtime_and_pool"}
    ),
    "dominant": frozenset({"apm.slow_tx_breakdown"}),
    "db_split": frozenset({"apm.slow_tx_breakdown"}),
    "cpu_owner": frozenset(
        {
            "scope.resolve_was_targets",
            "apm.golden_signals",
            "apm.runtime_and_pool",
            "host.usage_trend",
            "host.process_top",
        }
    ),
    "heap_shape": frozenset(
        {"scope.resolve_was_targets", "apm.runtime_and_pool", "apm.golden_signals"}
    ),
    "host_saturation": frozenset({"host.usage_trend"}),
    "fs_eta": frozenset({"host.usage_trend"}),
    "change_candidates": frozenset({"change.recent"}),
}

#: 판독 → 규칙 ID(§4.2).
READING_RULES: dict[str, str] = {
    "scope": "R-A",
    "service_spread": "R-A",
    "load_mode": "R-B",
    "little_check": "R-B′",
    "dominant": "R-C",
    "db_split": "R-D",
    "cpu_owner": "R-E",
    "heap_shape": "R-F",
    "host_saturation": "R-G",
    "fs_eta": "R-G",
    "change_candidates": "R-H",
}

#: R-C 시간 분해 항목(데이터 키 → enum).
_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("sql_ms", "sql"),
    ("fetch_ms", "fetch"),
    ("external_ms", "external"),
    ("cpu_ms", "cpu"),
    ("network_ms", "network"),
)
#: R-C 동률 최종 순서(합계 시간까지 같을 때).
_DOMINANT_ORDER = ("sql", "fetch", "external", "cpu", "network", "other")

#: 게이트웨이 `was_signals` kind 인용(D-274 ⑤) — 풀 고갈 · GC/힙 압박.
_POOL_SIGNAL_KINDS = frozenset({"was_db_pool_exhaustion"})
_GC_SIGNAL_KINDS = frozenset({"was_gc_stall", "was_heap_pressure"})

_SOURCE_RULE = "playbook_rule"
_SOURCE_GATEWAY = "apm_gateway"


@dataclass(frozen=True)
class ReadingParams:
    """판독 임계 — 전부 [제안] 초기값(§4.2 · 파일럿 튜닝 · G-10).
    스텝 인자 `reading_params`로 바꾼다.

    host_min_instances:   R-A 「호스트 전체」로 보려면 같은 호스트 인스턴스가 이 수 이상이어야 한다.
    service_single_share: R-A Top N에서 최다 서비스 비중이 이 값을 **넘으면** single(과반).
    p95_rise_ratio:       R-B p95가 기준선의 이 배수 이상이어야 지연 유형을 판독한다.
    load_increase_pct:    R-B·R-E·R-F TPS가 기준선 대비 이 % 이상 늘면 부하 증가.
    buckle_tps_drop_pct:  R-B TPS가 기준선 대비 이 % 이상 줄면 붕괴 구간(buckle).
    little_excess_ratio:  R-B′ 관측 액티브 / (TPS × 평균 응답시간)이 이 배수 이상이면 active_excess.
    dominant_min_share:   R-C 한 건의 최대 분해 항목이 응답시간의 이 비중 미만이면 other.
    cpu_gap_pct:          R-E 호스트 CPU − WAS 프로세스 CPU가 이 %p 이상이면 WAS 밖 프로세스.
    was_cpu_high_pct:     R-E WAS 프로세스 CPU가 이 % 이상이면 WAS가 CPU를 쓴다.
    host_cores:           R-G 코어 수(None = 미상 → 4코어 이상 기준을 쓴다 · 사유 기록).
    cpu_high_pct:         R-G 코어 4개 이상 또는 미상일 때 CPU 포화 기준(%).
    cpu_high_pct_small:   R-G 코어 `small_core_max`개 이하일 때 CPU 포화 기준(%).
    small_core_max:       R-G 소형 호스트 코어 상한.
    memory_high_pct:      R-G 메모리 포화 기준(%).
    fs_high_pct:          R-G 파일시스템 포화 기준(%).
    heap_min_points:      R-F 힙 추세 최소 점 수.
    heap_drop_pct:        R-F 연속 두 점의 하락이 힙 committed의 이 % 이상이면 큰 하락(GC·재기동).
    heap_periodic_min_drops: R-F 큰 하락이 이 수 이상이고 간격이 고르면 periodic.
    heap_periodic_tolerance: R-F 하락 간격의 평균 대비 허용 편차(비율).
    heap_leak_rise_pct:   R-F 세 구간 바닥이 단조 상승하고 상승폭이 committed의 이 %
                          이상이면 누수 의심.
    heap_high_pct:        R-F 힙 최대가 committed의 이 % 이상이고 TPS가 늘면 load_proportional.
    change_near_minutes:  가설 사실 `S7.change` — 알람 전 이 분 안의 변경이면 near.
    """

    host_min_instances: int = 2
    service_single_share: float = 0.5
    p95_rise_ratio: float = 1.5
    load_increase_pct: float = 20.0
    buckle_tps_drop_pct: float = 30.0
    little_excess_ratio: float = 3.0
    dominant_min_share: float = 0.3
    cpu_gap_pct: float = 20.0
    was_cpu_high_pct: float = 80.0
    host_cores: int | None = None
    cpu_high_pct: float = 80.0
    cpu_high_pct_small: float = 50.0
    small_core_max: int = 2
    memory_high_pct: float = 90.0
    fs_high_pct: float = 90.0
    heap_min_points: int = 6
    heap_drop_pct: float = 30.0
    heap_periodic_min_drops: int = 3
    heap_periodic_tolerance: float = 0.25
    heap_leak_rise_pct: float = 5.0
    heap_high_pct: float = 85.0
    change_near_minutes: int = 60


DEFAULT_PARAMS = ReadingParams()


@dataclass(frozen=True)
class ReadingEvidence:
    """판독 1건의 근거 — 규칙 ID · 입력 스텝 · 충족 수치 · 판정 불가 사유 · 출처."""

    rule: str
    steps: tuple[str, ...] = ()
    values: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None
    source: str = _SOURCE_RULE

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["steps"] = list(self.steps)
        return out


@dataclass
class Readings:
    """판독 결과 — `values`(판독 이름 → enum) · `evidence` · 보조 사실 `facts`(스텝 ID → 이름 →
    값)."""

    values: dict[str, Any]
    evidence: dict[str, ReadingEvidence]
    facts: dict[str, dict[str, Any]] = field(default_factory=dict)
    fact_evidence: dict[str, ReadingEvidence] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def context(self) -> dict[str, Any]:
        """`playbook_expr` 평가 문맥 조각(판독 이름 → 값). `n/a`는 결손(None)으로 넣는다."""
        return {k: (None if v == NA else v) for k, v in self.values.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "values": dict(self.values),
            "evidence": {k: e.to_dict() for k, e in self.evidence.items()},
            "facts": {k: dict(v) for k, v in self.facts.items()},
            "fact_evidence": {k: e.to_dict() for k, e in self.fact_evidence.items()},
            "notes": list(self.notes),
        }


# ── 플레이북 → 판독 범위 · 임계 ─────────────────────────────────────────────


def enabled_readings(playbook: Playbook) -> frozenset[str]:
    """플레이북이 판독하는 이름 — 담당 액션 스텝이 있는 판독(스텝 인자 `readings`로 좁힘)."""
    out: set[str] = set()
    for step in playbook.steps:
        own = {name for name, actions in READING_ACTIONS.items() if step.action in actions}
        declared = step.args.get("readings")
        if isinstance(declared, list):
            own &= {str(x) for x in declared}
        out |= own
    return frozenset(out)


def reading_params(playbook: Playbook) -> tuple[ReadingParams, list[str]]:
    """스텝 인자 `reading_params`를 합쳐 임계를 만든다. 모르는 키·잘못된 값은 무시하고 메모를
    남긴다."""
    known = {f.name: f for f in fields(ReadingParams)}
    updates: dict[str, Any] = {}
    notes: list[str] = []
    for step in playbook.steps:
        raw = step.args.get("reading_params")
        if raw is None:
            continue
        if not isinstance(raw, Mapping):
            notes.append(f"{step.id} reading_params가 매핑이 아니다 — 무시")
            continue
        for key, value in raw.items():
            if key not in known:
                notes.append(f"{step.id} 알 수 없는 판독 인자 {key!r} — 무시")
                continue
            if value is not None and not (
                isinstance(value, (int, float)) and not isinstance(value, bool)
            ):
                notes.append(f"{step.id} 판독 인자 {key}={value!r}는 수치가 아니다 — 무시")
                continue
            updates[key] = value
    return replace(DEFAULT_PARAMS, **updates), notes


# ── 입력 헬퍼 ───────────────────────────────────────────────────────────────


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _find(
    steps: Mapping[str, Mapping[str, Any]], action: str
) -> tuple[str | None, Mapping[str, Any] | None]:
    for sid, step in steps.items():
        if step.get("action") == action:
            return sid, step
    return None, None


def _data(step: Mapping[str, Any] | None) -> Mapping[str, Any]:
    data = (step or {}).get("data")
    return data if isinstance(data, Mapping) else {}


def _ok(step: Mapping[str, Any] | None) -> bool:
    return step is not None and step.get("status") == "ok"


def _alarm_instance(steps: Mapping[str, Mapping[str, Any]]) -> str | None:
    _, s0 = _find(steps, "scope.resolve_was_targets")
    scope = _data(s0).get("scope")
    if isinstance(scope, Mapping) and scope.get("alarm_instance"):
        return str(scope["alarm_instance"])
    return None


def _target(data: Mapping[str, Any], instance: str | None) -> Mapping[str, Any] | None:
    targets = data.get("targets")
    if not isinstance(targets, Mapping) or not targets:
        return None
    if instance and isinstance(targets.get(instance), Mapping):
        return targets[instance]
    first = next(iter(targets.values()))
    return first if isinstance(first, Mapping) else None


def _tps_change_pct(
    steps: Mapping[str, Mapping[str, Any]], instance: str | None
) -> tuple[float | None, str | None, dict[str, Any]]:
    """S1(골든 시그널) 사건창 TPS의 기준선(전일·전주 평균) 대비 변화율(%)."""
    sid, s1 = _find(steps, "apm.golden_signals")
    if s1 is None:
        return None, None, {}
    if not _ok(s1):
        return None, sid, {}
    tgt = _target(_data(s1), instance) or {}
    tps = _num((tgt.get("incident") or {}).get("tps"))
    base_vals = [
        v
        for b in (tgt.get("baselines") or {}).values()
        if isinstance(b, Mapping) and (v := _num(b.get("tps"))) is not None
    ]
    if tps is None or not base_vals or mean(base_vals) <= 0:
        return None, sid, {"tps": tps, "baseline_tps": base_vals}
    base = mean(base_vals)
    pct = round((tps - base) / base * 100, 1)
    return pct, sid, {"tps": tps, "baseline_tps": round(base, 3), "tps_change_pct": pct}


def _signal_kinds(data: Mapping[str, Any]) -> set[str]:
    return {
        str(s.get("kind"))
        for s in data.get("was_signals") or []
        if isinstance(s, Mapping) and s.get("kind")
    }


# ── R-A 범위 ────────────────────────────────────────────────────────────────


def read_scope(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-A — 지연 인스턴스 분포 → instance(같은 호스트 일부) · host(같은 호스트 전체) ·
    shared_dependency(같은 업무 다른 호스트까지).

    알람 인스턴스가 지연(degraded)이 아니면 해당 없음(None). 대상 확정 실패면 비교 불가(None).
    판정 불가 인스턴스는 제외하고 사유에 적는다.
    """
    sid, s0 = _find(steps, "scope.resolve_was_targets")
    if s0 is None:
        return None, ReadingEvidence("R-A", reason="대상 확정 스텝 없음")
    st = (sid,) if sid else ()
    if not _ok(s0):
        return None, ReadingEvidence("R-A", st, reason=f"대상 확정 결손 — {s0.get('reason')}")
    data = _data(s0)
    instances = [i for i in data.get("instances") or [] if isinstance(i, Mapping)]
    if data.get("resolution") != "resolved" or not instances:
        return None, ReadingEvidence("R-A", st, reason="대상 확정 실패 — 범위 비교 불가")
    alarm = instances[0]
    if alarm.get("verdict") is None:
        return None, ReadingEvidence("R-A", st, reason="알람 인스턴스 판정 불가")
    if alarm.get("verdict") != "degraded":
        return None, ReadingEvidence(
            "R-A", st, reason="알람 인스턴스 지연 아님 — 범위 판독 해당 없음"
        )
    host = alarm.get("host")
    degraded = [i for i in instances if i.get("verdict") == "degraded"]
    unknown = [str(i.get("instance")) for i in instances if i.get("verdict") is None]
    other_hosts = sorted(
        {str(i.get("host")) for i in degraded if i.get("host") and host and i.get("host") != host}
    )
    same_host = [i for i in instances if host and i.get("host") == host]
    values: dict[str, Any] = {
        "alarm_host": host,
        "compared": len(instances),
        "degraded": [str(i.get("instance")) for i in degraded],
        "same_host_instances": len(same_host),
        "other_hosts_degraded": other_hosts,
    }
    reason = f"판정 불가 인스턴스 제외: {', '.join(unknown)}" if unknown else None
    if other_hosts:
        value = "shared_dependency"
    elif len(same_host) >= p.host_min_instances and all(
        i.get("verdict") == "degraded" for i in same_host
    ):
        value = "host"
    else:
        value = "instance"
    return value, ReadingEvidence("R-A", st, values, reason)


def read_service_spread(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-A — Top N 느린 거래의 서비스(`application`) 분포. 최다 서비스 비중 > 기준이면 single."""
    sid, s3 = _find(steps, "apm.slow_tx_breakdown")
    st = (sid,) if sid else ()
    if s3 is None:
        return None, ReadingEvidence("R-A", reason="트랜잭션 분해 스텝 없음")
    if not _ok(s3):
        return None, ReadingEvidence("R-A", st, reason=f"트랜잭션 분해 {s3.get('status')}")
    apps = [
        str(r.get("application"))
        for r in _data(s3).get("rows") or []
        if isinstance(r, Mapping) and r.get("application")
    ]
    if not apps:
        return None, ReadingEvidence("R-A", st, reason="서비스명 없음")
    top, count = Counter(apps).most_common(1)[0]
    share = round(count / len(apps), 3)
    value = "single" if share > p.service_single_share else "many"
    return value, ReadingEvidence(
        "R-A",
        st,
        {"top_service": top, "top_count": count, "rows": len(apps), "share": share},
    )


# ── R-B 부하형/처리 지연형 · R-B′ Little 정합 ───────────────────────────────


def read_load_mode(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-B — 사건창 vs 기준선: p95가 오르지 않으면 해당 없음. TPS +X% 이상 → load_increase,
    −Y% 이하 → buckle, 그 사이 → processing_delay."""
    instance = _alarm_instance(steps)
    sid, s1 = _find(steps, "apm.golden_signals")
    st = (sid,) if sid else ()
    if s1 is None:
        return None, ReadingEvidence("R-B", reason="골든 시그널 스텝 없음")
    if not _ok(s1):
        return None, ReadingEvidence("R-B", st, reason=f"골든 시그널 {s1.get('status')}")
    tgt = _target(_data(s1), instance) or {}
    p95 = _num((tgt.get("incident") or {}).get("p95_ms"))
    base_p95 = _num(tgt.get("baseline_p95_ms"))
    pct, _, tvals = _tps_change_pct(steps, instance)
    values = {"p95_ms": p95, "baseline_p95_ms": base_p95, **tvals}
    if p95 is None or not base_p95:
        return None, ReadingEvidence("R-B", st, values, "p95 또는 기준선 p95 결손")
    ratio = round(p95 / base_p95, 2)
    values["p95_ratio"] = ratio
    if ratio < p.p95_rise_ratio:
        return None, ReadingEvidence(
            "R-B", st, values, f"p95 상승 없음(기준선 대비 {ratio}배) — 지연 유형 해당 없음"
        )
    if pct is None:
        return None, ReadingEvidence("R-B", st, values, "TPS 또는 기준선 TPS 결손")
    if pct >= p.load_increase_pct:
        value = "load_increase"
    elif pct <= -p.buckle_tps_drop_pct:
        value = "buckle"
    else:
        value = "processing_delay"
    return value, ReadingEvidence("R-B", st, values)


def _s2_target(
    steps: Mapping[str, Mapping[str, Any]], instance: str | None
) -> tuple[str | None, Mapping[str, Any] | None, Mapping[str, Any]]:
    sid, s2 = _find(steps, "apm.runtime_and_pool")
    return sid, s2, (_target(_data(s2), instance) or {}) if s2 is not None else {}


def _series_failed(step: Mapping[str, Any] | None, present: bool) -> bool:
    """스텝이 결손이거나, 값이 없는데 스텝이 결손 도구를 보고했으면 참(시도했으나 실패)."""
    if step is None:
        return False
    if not _ok(step):
        return True
    return not present and bool(step.get("missing"))


def read_little_check(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-B′ — 관측 액티브(S2 사건창 평균) / (TPS × 평균 응답시간(초))가 기준 배수 이상이면
    active_excess."""
    instance = _alarm_instance(steps)
    sid1, s1 = _find(steps, "apm.golden_signals")
    sid2, s2, t2 = _s2_target(steps, instance)
    st = tuple(s for s in (sid1, sid2) if s)
    if s1 is None or not _ok(s1):
        return None, ReadingEvidence("R-B′", st, reason="골든 시그널 결손")
    tgt = _target(_data(s1), instance) or {}
    tps = _num((tgt.get("incident") or {}).get("tps"))
    avg_ms = next(
        (
            _num(((c or {}).get("current") or {}).get("avg_response_ms"))
            for c in (tgt.get("period_compare") or {}).values()
            if _num(((c or {}).get("current") or {}).get("avg_response_ms")) is not None
        ),
        None,
    )
    active = (t2.get("trend_summary") or {}).get("active_services") if t2 else None
    active_avg = _num((active or {}).get("avg")) if isinstance(active, Mapping) else None
    values = {"tps": tps, "avg_response_ms": avg_ms, "active_avg": active_avg}
    if s2 is None:
        return None, ReadingEvidence("R-B′", st, values, "런타임 스텝 없음 — 액티브 추세 없음")
    if active_avg is None:
        why = "결손" if _series_failed(s2, False) else "미수집"
        return None, ReadingEvidence("R-B′", st, values, f"액티브 서비스 추세 {why}")
    if tps is None or avg_ms is None or tps * avg_ms <= 0:
        return None, ReadingEvidence("R-B′", st, values, "TPS 또는 평균 응답시간 결손")
    expected = round(tps * avg_ms / 1000, 3)
    ratio = round(active_avg / expected, 2)
    values.update(expected_active=expected, ratio=ratio)
    value = "active_excess" if ratio >= p.little_excess_ratio else "consistent"
    return value, ReadingEvidence("R-B′", st, values)


# ── R-C 지배 계층 · R-D 커넥션 획득 분리 ────────────────────────────────────


def read_dominant(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-C — Top N 각 건의 최대 분해 항목(비중 < 기준이면 other) → 다수결.

    동률: 동률 항목 중 N건 합계 시간이 큰 항목(other는 응답시간 − 분해 합) → 그래도 같으면
    sql·fetch·external·cpu·network·other 순서.
    """
    sid, s3 = _find(steps, "apm.slow_tx_breakdown")
    st = (sid,) if sid else ()
    if s3 is None:
        return None, ReadingEvidence("R-C", reason="트랜잭션 분해 스텝 없음")
    if not _ok(s3):
        return None, ReadingEvidence("R-C", st, reason=f"트랜잭션 분해 {s3.get('status')}")
    labels: list[str] = []
    totals: Counter[str] = Counter()
    for row in _data(s3).get("rows") or []:
        if not isinstance(row, Mapping):
            continue
        rt = _num(row.get("response_time_ms"))
        comps = [(lab, _num(row.get(key))) for key, lab in _COMPONENTS]
        known = [(lab, v) for lab, v in comps if v is not None]
        if not rt or rt <= 0 or not known:
            continue
        for lab, v in known:
            totals[lab] += v
        totals["other"] += max(0.0, rt - sum(v for _, v in known))
        lab, v = max(known, key=lambda kv: kv[1])  # 같은 값이면 _COMPONENTS 순서의 앞
        labels.append(lab if v / rt >= p.dominant_min_share else "other")
    if not labels:
        return None, ReadingEvidence("R-C", st, reason="분해 가능한 거래 없음")
    votes = Counter(labels)
    top = max(votes.values())
    tied = [lab for lab in _DOMINANT_ORDER if votes.get(lab) == top]
    value = max(tied, key=lambda lab: (totals[lab], -_DOMINANT_ORDER.index(lab)))
    values: dict[str, Any] = {
        "per_row": labels,
        "votes": dict(votes),
        "totals_ms": {k: round(v, 1) for k, v in totals.items()},
    }
    if len(tied) > 1:
        values["tie"] = tied
    return value, ReadingEvidence("R-C", st, values)


def read_db_split(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str, ReadingEvidence]:
    """R-D — 1위 건 프로파일의 **구조화** 필드(`acquire_ms`·`execute_ms`)가 있을 때만 판독한다.

    획득 > 실행이면 acquire, 아니면 execute. 구조화 필드가 없으면 `n/a` — 프로파일 텍스트
    (`profile_excerpt`)는 형식이 미검증이라 해석하지 않는다(W0 실측 · 게이트웨이 [한계]).
    """
    sid, s3 = _find(steps, "apm.slow_tx_breakdown")
    st = (sid,) if sid else ()
    if s3 is None:
        return NA, ReadingEvidence("R-D", reason="트랜잭션 분해 스텝 없음")
    if not _ok(s3):
        return NA, ReadingEvidence("R-D", st, reason=f"트랜잭션 분해 {s3.get('status')}")
    profile = _data(s3).get("profile")
    if not isinstance(profile, Mapping):
        return NA, ReadingEvidence("R-D", st, reason="1위 건 프로파일 없음")
    acquire = _num(profile.get("acquire_ms"))
    execute = _num(profile.get("execute_ms"))
    if acquire is None or execute is None:
        return NA, ReadingEvidence(
            "R-D",
            st,
            reason="프로파일에 커넥션 획득·SQL 실행 구조화 필드(acquire_ms·execute_ms) 없음 — "
            "프로파일 텍스트는 형식 미검증이라 해석하지 않는다",
        )
    value = "acquire" if acquire > execute else "execute"
    return value, ReadingEvidence("R-D", st, {"acquire_ms": acquire, "execute_ms": execute})


# ── R-E CPU 귀속 ────────────────────────────────────────────────────────────


def _host_finding(
    steps: Mapping[str, Mapping[str, Any]],
) -> tuple[str | None, Mapping[str, Any] | None, Mapping[str, Any]]:
    sid, s4 = _find(steps, "host.usage_trend")
    findings = _data(s4).get("findings") if _ok(s4) else None
    return sid, s4, findings if isinstance(findings, Mapping) else {}


def was_cpu_top_instance(s2_data: Mapping[str, Any]) -> str | None:
    """S2(`apm.runtime_and_pool`) 대상 중 사건창 프로세스 CPU 최대 인스턴스(동률은 S2 순서 앞).

    알람 인스턴스가 없는 호스트 알람에서 R-E가 보는 인스턴스이자 S3a(`target: was_cpu_top`)의
    대상이다(plans/187 W20b — 같은 함수로 둘을 맞춘다). 값이 하나도 없으면 None.
    """
    targets = s2_data.get("targets")
    best: tuple[float, str] | None = None
    for name, tgt in (targets.items() if isinstance(targets, Mapping) else ()):
        proc = ((tgt or {}).get("trend_summary") or {}).get("process_cpu_pct")
        value = _num(proc.get("max")) if isinstance(proc, Mapping) else None
        if value is not None and (best is None or value > best[0]):
            best = (value, str(name))
    return best[1] if best else None


def read_cpu_owner(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-E — 호스트 CPU(이상 판정 = `correlation` 판정 인용) vs WAS 프로세스 CPU(사건창 최대).

    호스트 CPU 이상 아님 → none. 이상이고 (호스트 − WAS) ≥ 기준 %p → other_process.
    WAS ≥ 기준 % → TPS가 늘었으면 was_load, 아니면 was_logic. 그 밖 → none.
    호스트 CPU 결손 · WAS 프로세스 CPU 조회 실패 → None(판단 보류). S6 상위 프로세스는 대조 근거로
    싣는다. 알람 인스턴스가 없으면(호스트 알람) S2 프로세스 CPU 최대 인스턴스를 본다(W20b).
    """
    instance = _alarm_instance(steps)
    if instance is None:
        _, s2_any = _find(steps, "apm.runtime_and_pool")
        instance = was_cpu_top_instance(_data(s2_any))
    sid4, s4, findings = _host_finding(steps)
    sid2, s2, t2 = _s2_target(steps, instance)
    sid6, s6 = _find(steps, "host.process_top")
    st = tuple(s for s in (sid4, sid2, sid6) if s)
    cpu = findings.get("cpu") if isinstance(findings.get("cpu"), Mapping) else None
    if cpu is None:
        return None, ReadingEvidence("R-E", st, reason="호스트 CPU 추이 결손")
    host_peak = _num(cpu.get("peak_value"))
    anomalous = bool(cpu.get("is_anomalous"))
    proc = (t2.get("trend_summary") or {}).get("process_cpu_pct") if t2 else None
    was_max = _num((proc or {}).get("max")) if isinstance(proc, Mapping) else None
    values: dict[str, Any] = {
        "host_cpu_peak": host_peak,
        "host_cpu_anomalous": anomalous,
        "was_cpu_max": was_max,
    }
    top = [r for r in _data(s6).get("rows") or [] if isinstance(r, Mapping)] if _ok(s6) else []
    if top:
        values["top_process"] = {"name": top[0].get("name"), "pcpu": top[0].get("pcpu")}
    if _series_failed(s2, was_max is not None):
        return None, ReadingEvidence("R-E", st, values, "WAS 프로세스 CPU 조회 실패")
    if not anomalous:
        return "none", ReadingEvidence("R-E", st, values)
    if was_max is None or host_peak is None:
        return None, ReadingEvidence("R-E", st, values, "WAS 프로세스 CPU 미수집 — 귀속 불가")
    gap = round(host_peak - was_max, 1)
    values["gap_pct"] = gap
    if gap >= p.cpu_gap_pct:
        return "other_process", ReadingEvidence("R-E", st, values)
    if was_max >= p.was_cpu_high_pct:
        pct, sid1, tvals = _tps_change_pct(steps, instance)
        values.update(tvals)
        st1 = st + ((sid1,) if sid1 else ())
        if pct is None:
            return None, ReadingEvidence("R-E", st1, values, "TPS 결손 — 부하/로직 구분 불가")
        return ("was_load" if pct >= p.load_increase_pct else "was_logic"), ReadingEvidence(
            "R-E", st1, values
        )
    return "none", ReadingEvidence("R-E", st, values)


# ── R-F 힙 모양 ─────────────────────────────────────────────────────────────


def _split3(values: Sequence[float]) -> list[list[float]]:
    n = len(values)
    sizes = [n // 3 + (1 if i < n % 3 else 0) for i in range(3)]
    out, start = [], 0
    for size in sizes:
        out.append(list(values[start : start + size]))
        start += size
    return out


def read_heap_shape(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-F — 힙 사용량 추세(시간순).

    큰 하락(committed의 X% 이상)이 N회 이상이고 간격이 고르면 periodic → 세 구간 바닥이 단조
    상승하고 상승폭이 기준 이상이면 leak_suspect → 힙 최대가 committed의 기준 이상이고 TPS가
    늘었으면
    load_proportional → normal.
    """
    instance = _alarm_instance(steps)
    sid, s2, t2 = _s2_target(steps, instance)
    st = (sid,) if sid else ()
    if s2 is None:
        return None, ReadingEvidence("R-F", reason="런타임 스텝 없음")
    trend = t2.get("trend") if t2 else None
    raw = (trend or {}).get("heap_used_mb") if isinstance(trend, Mapping) else None
    points = sorted(
        (
            (_num(pt.get("time_ms")) or 0.0, _num(pt.get("value")))
            for pt in raw or []
            if isinstance(pt, Mapping) and _num(pt.get("value")) is not None
        ),
        key=lambda tv: tv[0],
    )
    if _series_failed(s2, bool(points)):
        return None, ReadingEvidence("R-F", st, reason="힙 추세 조회 실패")
    if len(points) < p.heap_min_points:
        return None, ReadingEvidence(
            "R-F", st, {"points": len(points)}, f"힙 추세 점 부족(<{p.heap_min_points})"
        )
    vals = [float(v) for _, v in points if v is not None]
    committed_raw = (trend or {}).get("heap_committed_mb") or []
    committed_vals = [_num(pt.get("value")) for pt in committed_raw if isinstance(pt, Mapping)]
    committed = max([c for c in committed_vals if c] or [max(vals)])
    values: dict[str, Any] = {
        "points": len(vals),
        "min": min(vals),
        "max": max(vals),
        "committed": committed,
    }
    drop_at = [
        points[i][0]
        for i in range(1, len(points))
        if vals[i - 1] - vals[i] >= p.heap_drop_pct / 100 * committed
    ]
    values["large_drops"] = len(drop_at)
    if len(drop_at) >= p.heap_periodic_min_drops:
        gaps = [b - a for a, b in zip(drop_at, drop_at[1:])]
        avg = mean(gaps)
        if avg > 0 and max(abs(g - avg) for g in gaps) <= p.heap_periodic_tolerance * avg:
            values["period_minutes"] = round(avg / 60000, 1)
            return "periodic", ReadingEvidence("R-F", st, values)
    floors = [min(seg) for seg in _split3(vals)]
    values["floors"] = floors
    rise = floors[-1] - floors[0]
    if all(b > a for a, b in zip(floors, floors[1:])) and rise >= (
        p.heap_leak_rise_pct / 100 * committed
    ):
        values["floor_rise"] = round(rise, 1)
        return "leak_suspect", ReadingEvidence("R-F", st, values)
    if max(vals) >= p.heap_high_pct / 100 * committed:
        pct, sid1, tvals = _tps_change_pct(steps, instance)
        values.update(tvals)
        if pct is not None and pct >= p.load_increase_pct:
            st1 = st + ((sid1,) if sid1 else ())
            return "load_proportional", ReadingEvidence("R-F", st1, values)
    return "normal", ReadingEvidence("R-F", st, values)


# ── R-G 호스트 포화 · 파일시스템 고갈 예상 ──────────────────────────────────

_SAT_KINDS: tuple[tuple[str, str], ...] = (
    ("cpu", "cpu"),
    ("memory", "memory"),
    ("filesystem", "fs"),
)


def read_host_saturation(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-G — 사건창 최대값 기준: CPU ≥ 80%(4코어 이상·미상) 또는 ≥ 50%(1~2코어) · 메모리 · FS.

    여럿이면 cpu → memory → fs 순서의 첫 값. 받은 지표가 하나도 없으면 None. 일부 결손은 사유에
    적는다.
    """
    sid, s4, findings = _host_finding(steps)
    st = (sid,) if sid else ()
    if s4 is None:
        return None, ReadingEvidence("R-G", reason="호스트 추이 스텝 없음")
    if not findings:
        return None, ReadingEvidence("R-G", st, reason="호스트 지표 결손")
    small = p.host_cores is not None and p.host_cores <= p.small_core_max
    thresholds = {
        "cpu": p.cpu_high_pct_small if small else p.cpu_high_pct,
        "memory": p.memory_high_pct,
        "filesystem": p.fs_high_pct,
    }
    notes: list[str] = []
    if p.host_cores is None:
        notes.append(f"코어 수 미상 → {p.cpu_high_pct:g}% 기준")
    peaks: dict[str, float | None] = {}
    saturated: list[str] = []
    for key, label in _SAT_KINDS:
        f = findings.get(key)
        if not isinstance(f, Mapping):
            notes.append(f"{key} 결손")
            continue
        peak = _num(f.get("peak_value"))
        peaks[key] = peak
        if peak is not None and peak >= thresholds[key]:
            saturated.append(label)
    if not peaks:
        return None, ReadingEvidence("R-G", st, reason="CPU·메모리·FS 지표 결손")
    values = {"peaks": peaks, "thresholds": thresholds, "saturated": saturated}
    return (saturated[0] if saturated else "none"), ReadingEvidence(
        "R-G", st, values, " · ".join(notes) or None
    )


def read_fs_eta(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[str | None, ReadingEvidence]:
    """R-G — 파일시스템 사용률(%) 추세의 첫·끝 점 증가율로 100% 도달 예상 시각(ISO). 증가 없으면
    None."""
    sid, s4 = _find(steps, "host.usage_trend")
    st = (sid,) if sid else ()
    if s4 is None:
        return None, ReadingEvidence("R-G", reason="호스트 추이 스텝 없음")
    series = (_data(s4).get("series") or {}).get("filesystem") if _ok(s4) else None
    pts = [
        (str(pt[0]), _num(pt[1]))
        for pt in (series or {}).get("points") or []
        if isinstance(pt, (list, tuple)) and len(pt) == 2 and _num(pt[1]) is not None
    ]
    if len(pts) < 2:
        return None, ReadingEvidence("R-G", st, reason="파일시스템 추세 결손(2점 미만)")
    try:
        t0, t1 = datetime.fromisoformat(pts[0][0]), datetime.fromisoformat(pts[-1][0])
    except ValueError:
        return None, ReadingEvidence("R-G", st, reason="파일시스템 추세 시각 형식 오류")
    v0, v1 = float(pts[0][1] or 0.0), float(pts[-1][1] or 0.0)
    hours = (t1 - t0).total_seconds() / 3600
    values: dict[str, Any] = {"first": v0, "last": v1, "hours": round(hours, 2)}
    if hours <= 0 or v1 <= v0:
        return None, ReadingEvidence(
            "R-G", st, values, "파일시스템 사용률 증가 없음 — 고갈 예상 없음"
        )
    rate = (v1 - v0) / hours
    values["rate_pct_per_hour"] = round(rate, 3)
    eta = t1 + timedelta(hours=max(0.0, 100.0 - v1) / rate)
    return eta.replace(microsecond=0).isoformat(), ReadingEvidence("R-G", st, values)


# ── R-H 변경 근접 ───────────────────────────────────────────────────────────


def read_change_candidates(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams = DEFAULT_PARAMS
) -> tuple[list[dict[str, Any]] | None, ReadingEvidence]:
    """R-H — 변경 후보를 알람까지 시간 근접순으로 정렬한다(「상관 후보」로만 — 원인 확정 아님)."""
    sid, s7 = _find(steps, "change.recent")
    st = (sid,) if sid else ()
    if s7 is None:
        return None, ReadingEvidence("R-H", reason="변경 대조 스텝 없음")
    if not _ok(s7):
        return None, ReadingEvidence("R-H", st, reason=f"변경 대조 {s7.get('status')}")
    cands = [
        {k: c.get(k) for k in ("source", "at", "minutes_before_alarm", "description")}
        for c in _data(s7).get("candidates") or []
        if isinstance(c, Mapping) and _num(c.get("minutes_before_alarm")) is not None
    ]
    cands.sort(key=lambda c: (float(c["minutes_before_alarm"]), str(c.get("source"))))
    return cands, ReadingEvidence("R-H", st, {"count": len(cands)}, "상관 후보 — 원인 확정 아님")


_READERS = {
    "scope": read_scope,
    "service_spread": read_service_spread,
    "load_mode": read_load_mode,
    "little_check": read_little_check,
    "dominant": read_dominant,
    "db_split": read_db_split,
    "cpu_owner": read_cpu_owner,
    "heap_shape": read_heap_shape,
    "host_saturation": read_host_saturation,
    "fs_eta": read_fs_eta,
    "change_candidates": read_change_candidates,
}


# ── 보조 사실(게이트웨이 판정 인용 · 변경 근접) ─────────────────────────────


def _facts(
    steps: Mapping[str, Mapping[str, Any]], p: ReadingParams
) -> tuple[dict[str, dict[str, Any]], dict[str, ReadingEvidence]]:
    facts: dict[str, dict[str, Any]] = {}
    evidence: dict[str, ReadingEvidence] = {}
    instance = _alarm_instance(steps)
    sid2, s2, t2 = _s2_target(steps, instance)
    if sid2 and s2 is not None:
        data = _data(s2)
        kinds = _signal_kinds(data)
        ok = _ok(s2)
        pool_present = bool(t2.get("pool")) if t2 else False
        if kinds & _POOL_SIGNAL_KINDS:
            pool, src = "exhausted", _SOURCE_GATEWAY
        else:
            pool, src = ("ok" if ok and pool_present else None), _SOURCE_GATEWAY
        heap_present = bool(((t2.get("trend") or {}) if t2 else {}).get("heap_used_mb"))
        if kinds & _GC_SIGNAL_KINDS:
            gc = "pressure"
        else:
            gc = "ok" if ok and not _series_failed(s2, heap_present) and heap_present else None
        facts[sid2] = {"pool": pool, "gc": gc}
        evidence[f"{sid2}.pool"] = ReadingEvidence(
            "fact",
            (sid2,),
            {"was_signals": sorted(kinds & _POOL_SIGNAL_KINDS)},
            None if pool else "풀 현재값 결손",
            src,
        )
        evidence[f"{sid2}.gc"] = ReadingEvidence(
            "fact",
            (sid2,),
            {"was_signals": sorted(kinds & _GC_SIGNAL_KINDS)},
            None if gc else "힙 추세 결손",
            _SOURCE_GATEWAY,
        )
    sid7, s7 = _find(steps, "change.recent")
    if sid7 and s7 is not None:
        cands, ev = read_change_candidates(steps, p)
        if cands is None:
            change: str | None = None
        elif not cands:
            change = "none"
        else:
            change = (
                "near"
                if float(cands[0]["minutes_before_alarm"]) <= p.change_near_minutes
                else "far"
            )
        facts[sid7] = {"change": change}
        evidence[f"{sid7}.change"] = ReadingEvidence(
            "fact",
            (sid7,),
            {
                "nearest_minutes": cands[0]["minutes_before_alarm"] if cands else None,
                "near_minutes": p.change_near_minutes,
            },
            ev.reason if cands is None else None,
        )
    return facts, evidence


def compute_readings(
    steps: Mapping[str, Mapping[str, Any]],
    *,
    enabled: frozenset[str] | None = None,
    params: ReadingParams = DEFAULT_PARAMS,
) -> Readings:
    """스텝 결과 dict(스텝 ID → `StepResult.to_dict()` 모양 · YAML 순서)로 R-A~R-H를 계산한다.

    `enabled`에 없는 판독은 None(db_split은 `n/a`)과 사유 「이 플레이북에 해당 판독 없음」.
    예외를 내지 않는다 — 판독 함수가 실패하면 None과 사유.
    """
    values: dict[str, Any] = {}
    evidence: dict[str, ReadingEvidence] = {}
    for name, reader in _READERS.items():
        rule = READING_RULES[name]
        if enabled is not None and name not in enabled:
            values[name] = NA if name == "db_split" else None
            evidence[name] = ReadingEvidence(rule, reason="이 플레이북에 해당 판독 없음")
            continue
        try:
            value, ev = reader(steps, params)
        except Exception as e:  # noqa: BLE001 — 판독 1건 실패가 나머지를 막지 않는다(사유 노출)
            value = NA if name == "db_split" else None
            ev = ReadingEvidence(rule, reason=f"판독 실패 — {type(e).__name__}: {e}")
        values[name] = value
        evidence[name] = ev
    facts, fact_ev = _facts(steps, params)
    return Readings(values=values, evidence=evidence, facts=facts, fact_evidence=fact_ev)


__all__ = [
    "DEFAULT_PARAMS",
    "NA",
    "READING_ACTIONS",
    "READING_INPUTS",
    "READING_RULES",
    "ReadingEvidence",
    "ReadingParams",
    "Readings",
    "compute_readings",
    "enabled_readings",
    "read_change_candidates",
    "read_cpu_owner",
    "was_cpu_top_instance",
    "read_db_split",
    "read_dominant",
    "read_fs_eta",
    "read_heap_shape",
    "read_host_saturation",
    "read_little_check",
    "read_load_mode",
    "read_scope",
    "read_service_spread",
    "reading_params",
]
