"""업무 서비스 상태 판정 — 위험 · 주의 · 이상 신호 없음 · 판정 불가 (plans/215 §4.4).

| 상태 | 조건(어느 하나 · 위가 우선) |
|---|---|
| 위험 | 열린 사건 심각 이상 · 구성원 APM 게이트웨이 신호(큐잉·오류 급증) · SMS 사용률 ≥ crit |
| 주의 | 그 밖의 열린 사건 · SMS 사용률 ≥ warn (APM에는 주의 단계가 없다) |
| 이상 신호 없음 | 위에 해당 없음 · 지표가 주기 안에 들어옴(D-335 ② — 「정상」 어휘 금지) |
| 판정 불가 | 감시 꺼짐 · 지표 없음 · 조회 실패 — 사유를 함께 보인다 |

- APM 응답시간·TPS·에러율 값은 **표시만** 한다(본체 APM 지표 임계 리터럴 0 · D-335 ①).
- SMS 임계가 설정되지 않은 지표는 SMS 부분 「판정 불가 · 임계 미설정」(값을 지어내지 않는다).
- 서비스 상태는 구성원 최악값이고, 근거(어느 구성원 · 어떤 값 · 어떤 규칙)를 같이 돌려준다.
- 사건 심각도는 `severity.coerce_severity`로 정규화한다(미지 값은 보수적 폴백 + 사유).

계층: domain — stdlib + 같은 domain 모듈만 쓴다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from noise_gate.domain.biz_service_model import SmsThreshold, text
from noise_gate.domain.severity import SEVERITY_CRITICAL, coerce_severity

STATE_RISK = "risk"
STATE_CAUTION = "caution"
STATE_NO_SIGNAL = "no_signal"
STATE_UNKNOWN = "unknown"
STATE_LABELS: Mapping[str, str] = {
    STATE_RISK: "위험",
    STATE_CAUTION: "주의",
    STATE_NO_SIGNAL: "이상 신호 없음",
    STATE_UNKNOWN: "판정 불가",
}
_RANK = {STATE_RISK: 0, STATE_CAUTION: 1}

#: 위험으로 보는 APM 게이트웨이 신호(게이트웨이 결정적 판정 결과 — 본체 임계 아님).
APM_RISK_SIGNALS = frozenset({"was_service_queuing", "was_error_burst"})

RULE_INCIDENT_CRITICAL = "incident_critical"
RULE_INCIDENT_OPEN = "incident_open"
RULE_APM_SIGNAL = "apm_signal"
RULE_SMS_CRIT = "sms_crit"
RULE_SMS_WARN = "sms_warn"

SMS_NO_THRESHOLD = "판정 불가 · 임계 미설정"
SMS_NO_DATA = "판정 불가 · 지표 없음"


@dataclass(frozen=True)
class IncidentFact:
    """구성원의 사건 1건(노이즈 게이트 사건 집계)."""

    member_key: str
    severity: Any
    open: bool = True
    incident_id: str = ""


@dataclass(frozen=True)
class ApmSignalFact:
    """구성원의 APM 게이트웨이 신호 1건(`signal` 이름 · `level`은 게이트웨이 등급)."""

    member_key: str
    signal: str
    level: str = ""


@dataclass(frozen=True)
class SmsMetricFact:
    """구성원의 SMS 사용률 1건(`metric`은 cpu·memory·filesystem · 값 %)."""

    member_key: str
    metric: str
    value: float | None


@dataclass(frozen=True)
class ApmValueFact:
    """구성원의 APM 값 1건 — 표시 전용(응답시간·TPS·에러율 · 판정에 쓰지 않는다)."""

    member_key: str
    name: str
    value: float | None


@dataclass(frozen=True)
class StateEvidence:
    """판정 근거 1건."""

    state: str
    member_key: str
    rule: str
    metric: str = ""
    value: Any = None
    threshold: float | None = None
    note: str = ""


@dataclass(frozen=True)
class ServiceStateJudgement:
    """서비스 상태 판정 결과 — `sms_notes`는 SMS 지표별 판정 불가 사유."""

    state: str
    label: str
    evidence: tuple[StateEvidence, ...] = ()
    reasons: tuple[str, ...] = ()
    sms_notes: Mapping[str, str] = field(default_factory=dict)
    displays: tuple[ApmValueFact, ...] = ()


def _incident_evidence(incidents: Sequence[IncidentFact]) -> tuple[list[StateEvidence], list[str]]:
    out: list[StateEvidence] = []
    notes: list[str] = []
    for inc in incidents:
        if not inc.open:
            continue
        sev, why = coerce_severity(inc.severity)
        if why is not None:
            notes.append(f"사건 심각도 정규화 폴백 — {why}")
        if sev >= SEVERITY_CRITICAL:
            out.append(StateEvidence(STATE_RISK, inc.member_key, RULE_INCIDENT_CRITICAL,
                                     value=inc.severity, note=inc.incident_id))
        else:
            out.append(StateEvidence(STATE_CAUTION, inc.member_key, RULE_INCIDENT_OPEN,
                                     value=inc.severity, note=inc.incident_id))
    return out, notes


def _sms_evidence(
    metrics: Sequence[SmsMetricFact], thresholds: Mapping[str, SmsThreshold]
) -> tuple[list[StateEvidence], dict[str, str], bool]:
    """SMS 사용률 근거 · 지표별 판정 불가 사유 · 판정한 값이 하나라도 있는가."""
    out: list[StateEvidence] = []
    notes: dict[str, str] = {}
    judged = False
    for m in metrics:
        metric = text(m.metric).lower()
        th = thresholds.get(metric)
        if th is None or (th.warn is None and th.crit is None):
            notes[metric] = SMS_NO_THRESHOLD
            continue
        if m.value is None:
            notes.setdefault(metric, SMS_NO_DATA)
            continue
        judged = True
        value = float(m.value)
        if th.crit is not None and value >= th.crit:
            out.append(StateEvidence(STATE_RISK, m.member_key, RULE_SMS_CRIT, metric, value,
                                     th.crit))
        elif th.warn is not None and value >= th.warn:
            out.append(StateEvidence(STATE_CAUTION, m.member_key, RULE_SMS_WARN, metric, value,
                                     th.warn))
    return out, notes, judged


def judge_service_state(
    *,
    monitored: bool,
    incidents: Sequence[IncidentFact] = (),
    apm_signals: Sequence[ApmSignalFact] = (),
    sms_metrics: Sequence[SmsMetricFact] = (),
    apm_values: Sequence[ApmValueFact] = (),
    thresholds: Mapping[str, SmsThreshold] | None = None,
    metrics_in_window: bool = True,
    fetch_failures: Sequence[str] = (),
) -> ServiceStateJudgement:
    """§4.4 판정 — 위험 → 주의 → 이상 신호 없음 → 판정 불가 순.

    Args:
        monitored: M3 감시 대상인가(꺼져 있으면 지표 판정 불가 · 사건은 그대로 본다)
        metrics_in_window: 지표가 수집 주기 안에 들어왔는가
        fetch_failures: 조회 실패 사유(소스별) — 있으면 「이상 신호 없음」을 내지 않는다
    """
    evidence, reasons = _incident_evidence(incidents)
    for sig in apm_signals:
        level = text(sig.level).upper()
        if text(sig.signal) in APM_RISK_SIGNALS and level in ("", "CRITICAL"):
            evidence.append(StateEvidence(STATE_RISK, sig.member_key, RULE_APM_SIGNAL,
                                          text(sig.signal)))
    sms_ev, sms_notes, judged = _sms_evidence(sms_metrics, thresholds or {})
    evidence += sms_ev
    evidence.sort(key=lambda e: (_RANK[e.state], e.member_key, e.rule, e.metric))
    displays = tuple(sorted(apm_values, key=lambda v: (v.member_key, v.name)))
    if evidence:
        state = evidence[0].state
        return ServiceStateJudgement(state, STATE_LABELS[state], tuple(evidence),
                                     tuple(reasons), dict(sorted(sms_notes.items())), displays)
    unknown: list[str] = []
    if not monitored:
        unknown.append("감시 꺼짐")
    if not metrics_in_window:
        unknown.append("지표가 주기 안에 들어오지 않음")
    if not (sms_metrics or apm_values or apm_signals):
        unknown.append("지표 없음")
    unknown += [f"조회 실패 — {text(f)}" for f in fetch_failures if text(f)]
    state = STATE_UNKNOWN if unknown else STATE_NO_SIGNAL
    reasons += unknown
    if state == STATE_NO_SIGNAL and sms_metrics and not judged:
        reasons.append(f"SMS {SMS_NO_THRESHOLD}")
    return ServiceStateJudgement(state, STATE_LABELS[state], (), tuple(reasons),
                                 dict(sorted(sms_notes.items())), displays)
