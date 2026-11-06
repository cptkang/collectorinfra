"""업무 서비스 D1 진단 스냅샷 — 판독 R1~R6 · 최초 이상 순 · 대표 선정 · 결손 (plans/215 §4.7).

입력은 조립 단계(`noise_gate.application.biz_diagnosis`)가 모은 사실이다 — 구성원 · 알람(M2) ·
관측값(M3 최신값 또는 즉석 1회) · 210 연계(APM 인스턴스 → 이어진 SMS 구성원). 여기서는 새로 조회하지
않고, 원인 영역을 판정하지 않으며(D-352 ②), 결과는 「가설 · 참고용」이다(D-335 ② 어휘 — 이상이 없는
쪽은 「이상 신호 없음」).

판독(위가 우선):

| # | 조건 | 판독 |
|---|---|---|
| R1 | 이상 구성원 0 | 이상 구성원 없음 |
| R2 | 이상 SMS 서버 2대 이상 · SMS 구성원의 50% 이상 · 또는 계층 둘 이상 | 여러 서버 동시 |
| R3 | APM 이상 · 이어진 SMS 서버가 OS 이상(알람 또는 SMS 임계) | APM 이상 + 그 서버 OS 동반 |
| R4 | APM 이상인데 이어진 SMS 서버가 이상 신호 없음 | APM 단독 — 애플리케이션 쪽 추정 |
| R5 | APM 이상인데 이어진 서버 없음(미연계) · 또는 SMS 지표 판정 불가 | APM 이상 · 서버 확인 불가 |
| R6 | SMS 이상만 | 서버 단독 — 서비스 영향(APM) 미관측 |

- **이번 창**: `now - window_minutes` 이후의 알람과 열린 사건(진행 중이라 창 안으로 본다) · 관측값은
  현재값이다. 그래서 이상 구성원은 모두 같은 창에 든다. SMS 시간 통계는 그 정각 1시간이 창과 겹칠
  때만 쓴다(F12 — 겹치지 않으면 「지표 없음」 → R5). SMS 쪽은 알람(분 단위)을 먼저 본다.
- **최초 이상 순**: 구성원마다 첫 이상 시각(알람 발생 · APM 관측 · SMS 통계)을 정렬한다 — 「누가
  먼저」만 보이고 인과를 단정하지 않는다(상관≠인과).
- **대표**(D3 입력): ① 최초 이상 SMS 서버 ② 없으면 최초 이상 APM 구성원의 이어진 SMS 서버(1대일
  때만 · D-224 ④) ③ 없으면 대표 없음 + 사유. 같은 시각은 `(db_id, resource_id)` 오름차순.
- 같은 입력이면 입력 순서와 무관하게 같은 출력이다(모든 목록을 고정 키로 정렬한다).

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from noise_gate.domain.apm_host_map import STATE_ACTIVE
from noise_gate.domain.biz_service_keys import APM_KINDS, KIND_APM_INSTANCE, KIND_SMS_SERVER
from noise_gate.domain.biz_service_model import LAYERS, SmsThreshold, text
from noise_gate.domain.biz_service_state import (
    RULE_APM_SIGNAL,
    RULE_SMS_CRIT,
    RULE_SMS_WARN,
    SMS_NO_DATA,
    SMS_NO_THRESHOLD,
    ApmSignalFact,
    SmsMetricFact,
    judge_service_state,
)

VERDICT_R1 = "R1"
VERDICT_R2 = "R2"
VERDICT_R3 = "R3"
VERDICT_R4 = "R4"
VERDICT_R5 = "R5"
VERDICT_R6 = "R6"
VERDICT_TEXTS: Mapping[str, str] = {
    VERDICT_R1: "이상 구성원 없음",
    VERDICT_R2: "여러 서버 동시 — 공통 원인 의심(공용 자원·네트워크)",
    VERDICT_R3: "APM 이상 + 그 서버 OS 동반",
    VERDICT_R4: "APM 단독 — 애플리케이션 쪽 추정",
    VERDICT_R5: "APM 이상 · 서버 확인 불가",
    VERDICT_R6: "서버 단독 — 서비스 영향(APM) 미관측",
}
VERDICT_GUIDANCE: Mapping[str, str] = {
    VERDICT_R1: "",
    VERDICT_R2: "구성 맵에서 공용 자원·네트워크 연결을 확인하세요(확정 연결만 근거로 봅니다)",
    VERDICT_R3: "서버 자원 쪽을 먼저 확인하세요",
    VERDICT_R4: "애플리케이션(APM) 쪽을 먼저 확인하세요",
    VERDICT_R5: "서버 쪽 근거가 비어 있습니다 — 결손 항목을 확인하세요",
    VERDICT_R6: "서버 쪽을 확인하세요 — APM 영향은 관측되지 않았습니다",
}
#: 결과 표지(D-335 ② · 판정이 아니라 가설).
HYPOTHESIS_LABEL = "가설 · 참고용"
#: 최초 이상 순 병기 문구(D-325 ⑨와 같은 표현).
CORRELATION_NOTE = "최초 이상 순서는 「누가 먼저」만 보입니다 — 상관≠인과(원인을 단정하지 않습니다)"

#: SMS 시간 통계 한 칸의 길이(초) — 정각부터 1시간.
SMS_STAT_PERIOD_SECONDS = 3600

BASIS_INCIDENT = "incident"
BASIS_ALARM = "alarm"
BASIS_APM_SIGNAL = "apm_signal"
BASIS_SMS_THRESHOLD = "sms_threshold"
TIME_OCCURRED = "발생"
TIME_OBSERVED = "관측"
TIME_STAT = "통계"

GAP_NO_LINK = "no_link"
GAP_LINK_OUTSIDE = "link_outside"
GAP_NO_METRIC = "no_metric"
GAP_SERVICE_STAT_TIME = "service_stat_time"
GAP_OUT_OF_SCOPE = "out_of_scope"
_GAP_ORDER = {GAP_NO_LINK: 0, GAP_LINK_OUTSIDE: 1, GAP_NO_METRIC: 2, GAP_SERVICE_STAT_TIME: 3,
              GAP_OUT_OF_SCOPE: 4}
CAUSE_NO_LINK = "연계 없음"
CAUSE_LINK_OUTSIDE = "연계 서버 확인 범위 밖"
CAUSE_NO_METRIC = "지표 없음"
REASON_NO_METRIC = "지표 없음"
REASON_STAT_OUT_OF_WINDOW = "창 안 정각 통계 없음"
CAUSE_SERVICE_STAT_TIME = "존별 정각 통계 시각 없음 — 서비스 단위 시각으로 창 판정"

REP_FIRST_SMS = "first_sms"
REP_APM_LINKED = "apm_linked"
REP_LABELS: Mapping[str, str] = {
    REP_FIRST_SMS: "최초 이상 서버",
    REP_APM_LINKED: "최초 이상 APM 구성원의 이어진 서버(1대)",
}
REP_NONE_NO_ANOMALY = "이상 구성원 없음 — 대표 없음"

SMS_ANOMALOUS = "anomalous"
SMS_CLEAR = "no_signal"
SMS_UNKNOWN = "unknown"

_KIND_ORDER = {KIND_SMS_SERVER: 0}
_SMS_RULES = (RULE_SMS_CRIT, RULE_SMS_WARN)


@dataclass(frozen=True)
class DiagMember:
    """진단 대상 구성원 1개(조립 단계가 존 필터를 마친 보이는 구성원)."""

    kind: str
    member_key: str
    name: str = ""
    layer: str = ""
    db_id: str = ""
    resource_id: str = ""


@dataclass(frozen=True)
class DiagAlarm:
    """구성원 알람 1건 — `open_incident`면 열린 사건(진행 중 · 창 안으로 본다). `severity`는 0~3."""

    member_key: str
    alarm_id: str
    severity: int
    at: float | None
    open_incident: bool = False


@dataclass(frozen=True)
class DiagLink:
    """APM 인스턴스 구성원 1개의 210 연계 — `targets`는 이어진 SMS 구성원 키(끊긴 대상 제외)."""

    state: str
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagMetrics:
    """관측값 1회분(M3 최신값 또는 즉석 조회).

    `apm_observed_at`은 APM 값을 관측한 시각(최초 발생 시각 아님) · `sms_stats`는 SMS 구성원
    키 → (그 구성원 존의 최신 정각 통계 시각 epoch, 표기 「HH시 통계」) · 구성원 시각이 없으면
    서비스 단위 `sms_stat_at`·`sms_stat_label`(존별 시각이 없는 옛 형태 M3 최신값 폴백)을 쓴다 —
    그렇게 판정한 SMS 구성원은 결손 「서비스 단위 통계 시각」으로 남는다.
    """

    apm_signals: tuple[ApmSignalFact, ...] = ()
    sms_metrics: tuple[SmsMetricFact, ...] = ()
    apm_observed_at: float | None = None
    sms_stat_at: float | None = None
    sms_stat_label: str = ""
    sms_stats: Mapping[str, tuple[float, str]] = field(default_factory=dict)

    def stat_of(self, member_key: str) -> tuple[float | None, str]:
        """구성원의 정각 통계 (시각, 표기) — 구성원 시각 우선, 없으면 서비스 단위."""
        own = self.sms_stats.get(member_key)
        return own if own is not None else (self.sms_stat_at, self.sms_stat_label)


@dataclass(frozen=True)
class _Signal:
    at: float | None
    basis: str
    label: str
    time_basis: str


@dataclass(frozen=True)
class _SmsState:
    state: str
    reason: str = ""


def iso_of(epoch: float | None) -> str | None:
    """epoch 초 → ISO 8601(UTC) · None은 None."""
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC).isoformat()


def _time_key(at: float | None) -> tuple[int, float]:
    """시각 정렬 키 — 시각을 모르는 것은 뒤로."""
    return (1, 0.0) if at is None else (0, at)


def _rid_key(resource_id: str) -> tuple[int, int, str]:
    """`resource_id` 정렬 키 — 숫자면 수치 순, 아니면 글자 순(숫자 뒤)."""
    rid = text(resource_id)
    return (0, int(rid), "") if rid.isdigit() else (1, 0, rid)


def _member_order(m: DiagMember, first: float | None) -> tuple[Any, ...]:
    return (*_time_key(first), _KIND_ORDER.get(m.kind, 1), m.db_id, _rid_key(m.resource_id),
            m.member_key)


def _num(value: float) -> str:
    return f"{value:g}"


def _alarm_signals(
    alarms: Sequence[DiagAlarm], keys: Mapping[str, DiagMember], since: float
) -> dict[str, list[_Signal]]:
    out: dict[str, list[_Signal]] = {}
    for a in sorted(alarms, key=lambda x: (x.member_key, *_time_key(x.at), x.alarm_id)):
        if a.member_key not in keys or a.severity < 1:
            continue
        if not a.open_incident and (a.at is None or a.at < since):
            continue
        basis = BASIS_INCIDENT if a.open_incident else BASIS_ALARM
        label = "열린 사건" if a.open_incident else "알람"
        out.setdefault(a.member_key, []).append(
            _Signal(a.at, basis, f"{label} {a.alarm_id}".strip(), TIME_OCCURRED))
    return out


def _judgeable(metric: SmsMetricFact, thresholds: Mapping[str, SmsThreshold]) -> bool:
    th = thresholds.get(text(metric.metric).lower())
    return th is not None and (th.warn is not None or th.crit is not None) \
        and metric.value is not None


def _metric_signals(
    members: Sequence[DiagMember],
    metrics: DiagMetrics,
    thresholds: Mapping[str, SmsThreshold],
    since: float,
) -> tuple[dict[str, list[_Signal]], dict[str, str]]:
    """관측값 이상(APM 신호 · SMS 임계)과 SMS 구성원별 판정 불가 사유(판정했으면 빈 문자열)."""
    signals: dict[str, list[_Signal]] = {}
    sms_reason: dict[str, str] = {}
    for m in members:
        key = m.member_key
        stat_at, stat_label = metrics.stat_of(key)
        stat_in_window = stat_at is not None and stat_at + SMS_STAT_PERIOD_SECONDS > since
        stat_suffix = f" ({stat_label})" if stat_label else ""
        apm = [s for s in metrics.apm_signals if s.member_key == key] \
            if m.kind in APM_KINDS else []
        sms = [s for s in metrics.sms_metrics if s.member_key == key] \
            if m.kind == KIND_SMS_SERVER else []
        if m.kind == KIND_SMS_SERVER:
            if not sms:
                sms_reason[key] = REASON_NO_METRIC
            elif not stat_in_window:
                sms_reason[key] = REASON_STAT_OUT_OF_WINDOW
                sms = []
            elif not any(_judgeable(s, thresholds) for s in sms):
                missing = any(
                    thresholds.get(text(s.metric).lower()) is None for s in sms)
                sms_reason[key] = SMS_NO_THRESHOLD if missing else SMS_NO_DATA
            else:
                sms_reason[key] = ""
        if not apm and not sms:
            continue
        judgement = judge_service_state(monitored=True, apm_signals=apm, sms_metrics=sms,
                                        thresholds=thresholds)
        for e in judgement.evidence:
            if e.rule == RULE_APM_SIGNAL:
                signals.setdefault(key, []).append(_Signal(
                    metrics.apm_observed_at, BASIS_APM_SIGNAL, f"APM 신호 {e.metric}",
                    TIME_OBSERVED))
            elif e.rule in _SMS_RULES and e.threshold is not None:
                signals.setdefault(key, []).append(_Signal(
                    stat_at, BASIS_SMS_THRESHOLD,
                    f"SMS {e.metric} {_num(float(e.value))}% ≥ {_num(e.threshold)}%{stat_suffix}",
                    TIME_STAT))
    return signals, sms_reason


def _sms_state(key: str, signals: Mapping[str, list[_Signal]],
               sms_reason: Mapping[str, str]) -> _SmsState:
    if signals.get(key):
        return _SmsState(SMS_ANOMALOUS)
    reason = sms_reason.get(key, REASON_NO_METRIC)
    return _SmsState(SMS_CLEAR) if reason == "" else _SmsState(SMS_UNKNOWN, reason)


def _apm_outcome(
    m: DiagMember,
    links: Mapping[str, DiagLink],
    sms_keys: Mapping[str, DiagMember],
    states: Mapping[str, _SmsState],
) -> tuple[str, str, tuple[str, ...]]:
    """APM 이상 구성원 1개 → (R3|R4|R5, R5 원인, 보이는 이어진 SMS 구성원 키)."""
    link = links.get(m.member_key) if m.kind == KIND_APM_INSTANCE else None
    targets = link.targets if link is not None and link.state == STATE_ACTIVE else ()
    if not targets:
        return VERDICT_R5, CAUSE_NO_LINK, ()
    inside = tuple(sorted({t for t in targets if t in sms_keys}))
    if not inside:
        return VERDICT_R5, CAUSE_LINK_OUTSIDE, ()
    linked = [states[t].state for t in inside]
    if SMS_ANOMALOUS in linked:
        return VERDICT_R3, "", inside
    if all(s == SMS_CLEAR for s in linked):
        return VERDICT_R4, "", inside
    return VERDICT_R5, CAUSE_NO_METRIC, inside


def _gap(code: str, label: str, keys: Sequence[str], count: int | None = None) -> dict[str, Any]:
    return {"code": code, "label": label, "count": len(keys) if count is None else count,
            "member_keys": sorted(keys)}


def _representative(
    anomalous: Sequence[tuple[DiagMember, float | None]],
    links: Mapping[str, DiagLink],
    sms_keys: Mapping[str, DiagMember],
) -> tuple[dict[str, Any] | None, str]:
    """대표 1대(D3 입력)와 사유 — 고르지 못하면 (None, 사유)."""
    def _view(m: DiagMember, rule: str) -> dict[str, Any]:
        return {"kind": m.kind, "member_key": m.member_key, "name": m.name, "db_id": m.db_id,
                "resource_id": m.resource_id, "rule": rule, "rule_label": REP_LABELS[rule]}

    sms = [(m, at) for m, at in anomalous if m.kind == KIND_SMS_SERVER]
    if sms:
        first = min(sms, key=lambda x: (*_time_key(x[1]), x[0].db_id,
                                        _rid_key(x[0].resource_id), x[0].member_key))[0]
        return _view(first, REP_FIRST_SMS), REP_LABELS[REP_FIRST_SMS]
    apm = [(m, at) for m, at in anomalous if m.kind in APM_KINDS]
    if not apm:
        return None, REP_NONE_NO_ANOMALY
    first = min(apm, key=lambda x: (*_time_key(x[1]), x[0].member_key))[0]
    name = first.name or first.member_key
    link = links.get(first.member_key) if first.kind == KIND_APM_INSTANCE else None
    targets = sorted(set(link.targets)) if link is not None and link.state == STATE_ACTIVE else []
    if not targets:
        return None, f"최초 이상 APM 구성원 {name}에 이어진 서버 없음(연계 없음) — 대표 없음"
    if len(targets) >= 2:
        return None, (f"최초 이상 APM 구성원 {name}에 이어진 서버 {len(targets)}대 — 고르지 않음"
                      "(D-224 ④)")
    target = sms_keys.get(targets[0])
    if target is None:
        return None, f"최초 이상 APM 구성원 {name}의 이어진 서버가 확인 범위 밖 — 대표 없음"
    return _view(target, REP_APM_LINKED), REP_LABELS[REP_APM_LINKED]


def diagnose_service(
    *,
    members: Sequence[DiagMember],
    alarms: Sequence[DiagAlarm] = (),
    links: Mapping[str, DiagLink] | None = None,
    metrics: DiagMetrics | None = None,
    thresholds: Mapping[str, SmsThreshold] | None = None,
    now: float,
    window_minutes: int,
    hidden_members: int = 0,
) -> dict[str, Any]:
    """D1 판독 — `{verdict, verdict_text, verdict_guidance, hypothesis_label, correlation_note,
    anomalous_members, timeline, representative, representative_reason, gaps}`.

    Args:
        members: 보이는 구성원(존 필터 뒤) — 같은 `member_key`는 첫 것만 쓴다
        alarms: 보이는 구성원의 알람·열린 사건(M2)
        links: APM 인스턴스 구성원 키 → 210 연계(없는 키 = 연계 매핑 없음)
        metrics: 관측값(없으면 빈 관측 — SMS는 「지표 없음」)
        thresholds: SMS 임계(비면 SMS 「판정 불가 · 임계 미설정」)
        now: 기준 시각(epoch 초) · window_minutes: 이번 창 길이(분)
        hidden_members: 존 권한 밖 구성원 수(건수만 결손에 싣는다)
    """
    links = links or {}
    metrics = metrics or DiagMetrics()
    thresholds = thresholds or {}
    since = now - max(1, int(window_minutes)) * 60
    by_key: dict[str, DiagMember] = {}
    for m in sorted(members, key=lambda x: (x.member_key, x.kind, x.name)):
        if m.member_key:
            by_key.setdefault(m.member_key, m)
    ordered = sorted(by_key.values(), key=lambda x: (x.kind, x.member_key))
    sms_keys = {k: m for k, m in by_key.items() if m.kind == KIND_SMS_SERVER}

    signals = _alarm_signals(alarms, by_key, since)
    metric_sigs, sms_reason = _metric_signals(ordered, metrics, thresholds, since)
    for key, items in metric_sigs.items():
        signals.setdefault(key, []).extend(items)
    for items in signals.values():
        items.sort(key=lambda s: (*_time_key(s.at), s.basis, s.label))
    states = {k: _sms_state(k, signals, sms_reason) for k in sms_keys}

    anomalous = sorted(
        ((m, signals[m.member_key][0].at) for m in ordered if signals.get(m.member_key)),
        key=lambda x: _member_order(x[0], x[1]),
    )
    no_link: list[str] = []
    outside: list[str] = []
    apm_outcomes: list[str] = []
    causes: set[str] = set()
    rows: list[dict[str, Any]] = []
    for m, first in anomalous:
        row: dict[str, Any] = {
            "kind": m.kind, "member_key": m.member_key, "name": m.name, "layer": m.layer,
            "db_id": m.db_id, "resource_id": m.resource_id, "first_at": iso_of(first),
            "signals": [{"basis": s.basis, "label": s.label, "at": iso_of(s.at),
                         "time_basis": s.time_basis} for s in signals[m.member_key]],
            "linked_servers": [],
        }
        if m.kind in APM_KINDS:
            outcome, cause, inside = _apm_outcome(m, links, sms_keys, states)
            apm_outcomes.append(outcome)
            row["linked_servers"] = list(inside)
            if cause:
                causes.add(cause)
            if cause == CAUSE_NO_LINK:
                no_link.append(m.member_key)
            elif cause == CAUSE_LINK_OUTSIDE:
                outside.append(m.member_key)
        rows.append(row)

    anomalous_sms = [m for m, _ in anomalous if m.kind == KIND_SMS_SERVER]
    layers = {text(m.layer).lower() for m, _ in anomalous} & set(LAYERS)
    many = len(anomalous_sms) >= 2 and len(anomalous_sms) * 2 >= len(sms_keys)
    if not anomalous:
        verdict = VERDICT_R1
    elif many or len(layers) >= 2:
        verdict = VERDICT_R2
    elif apm_outcomes:
        verdict = min(apm_outcomes)
    else:
        verdict = VERDICT_R6
    verdict_text = VERDICT_TEXTS[verdict]
    if verdict == VERDICT_R5:
        order = (CAUSE_NO_LINK, CAUSE_LINK_OUTSIDE, CAUSE_NO_METRIC)
        verdict_text += "(" + "/".join(c for c in order if c in causes) + ")"

    gaps: list[dict[str, Any]] = []
    if no_link:
        gaps.append(_gap(GAP_NO_LINK, CAUSE_NO_LINK, no_link))
    if outside:
        gaps.append(_gap(GAP_LINK_OUTSIDE, CAUSE_LINK_OUTSIDE, outside))
    by_reason: dict[str, list[str]] = {}
    for key, st in states.items():
        if st.state == SMS_UNKNOWN:
            by_reason.setdefault(st.reason, []).append(key)
    for reason in sorted(by_reason):
        gaps.append(_gap(GAP_NO_METRIC, f"{CAUSE_NO_METRIC} · {reason}"
                         if reason != REASON_NO_METRIC else CAUSE_NO_METRIC, by_reason[reason]))
    service_stat = [
        k for k in sms_keys
        if k not in metrics.sms_stats and metrics.sms_stat_at is not None
        and any(s.member_key == k for s in metrics.sms_metrics)
    ]
    if service_stat:
        gaps.append(_gap(GAP_SERVICE_STAT_TIME, CAUSE_SERVICE_STAT_TIME, service_stat))
    if hidden_members > 0:
        gaps.append(_gap(GAP_OUT_OF_SCOPE, f"권한 밖 구성원 {hidden_members}", (),
                         count=hidden_members))
    gaps.sort(key=lambda g: (_GAP_ORDER[g["code"]], g["label"]))

    representative, rep_reason = _representative(anomalous, links, sms_keys)
    timeline = [
        {"member_key": r["member_key"], "kind": r["kind"], "name": r["name"],
         "layer": r["layer"], "at": r["first_at"], "basis": r["signals"][0]["basis"],
         "label": r["signals"][0]["label"], "time_basis": r["signals"][0]["time_basis"]}
        for r in rows
    ]
    return {
        "verdict": verdict,
        "verdict_text": verdict_text,
        "verdict_guidance": VERDICT_GUIDANCE[verdict],
        "hypothesis_label": HYPOTHESIS_LABEL,
        "correlation_note": CORRELATION_NOTE,
        "anomalous_members": rows,
        "timeline": timeline,
        "representative": representative,
        "representative_reason": rep_reason,
        "gaps": gaps,
    }


__all__ = [
    "CORRELATION_NOTE",
    "HYPOTHESIS_LABEL",
    "VERDICT_GUIDANCE",
    "VERDICT_TEXTS",
    "DiagAlarm",
    "DiagLink",
    "DiagMember",
    "DiagMetrics",
    "diagnose_service",
    "iso_of",
]
