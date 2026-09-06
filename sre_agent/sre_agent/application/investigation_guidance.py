"""조사 지침 조립 (plans/50 G5 · SPEC-investigation-guidance · D-194).

`system_prompt_additions`를 넘기는 프로덕션 호출부가 0건이라(2026-08-27·09-02 실측) 원격 프로파일이
요구한 `REMOTE_VM_SHELL_NOTE`조차 주입되지 않았다. 여기서 지침을 한 문자열로 조립해
`_default_diagnose_fn`이 `DiagnosisAgent.ask()`에 넘긴다.

구성(순서 고정): ① REMOTE_VM_SHELL_NOTE(원격 프로파일) ② 사건 구간 지침(잡의 reference_time —
도구 호출에 앵커 인자를 쓰라는 지시) ③ settings.investigation_guidance_extra(운영자 자유 지침 —
plans/51 §6 플레이북의 편입점). 부하 가드(LOAD_GUARD_NOTE)는 `ask()`가 항상 덧붙이므로 여기서 넣지 않는다.

계층: application. LLM을 호출하지 않는다(문자열 조립만).
"""

from __future__ import annotations

from sre_agent.toolset_profiles import REMOTE_VM_SHELL_NOTE

# 앵커 인자를 받는 mcp_server 도구(incident-window-tools).
ANCHORED_TOOLS: tuple[str, ...] = (
    "polestar_metric_trend",
    "polestar_alarm_history",
    "polestar_incident_alarms",
    "prom_metric_range",
)

INCIDENT_SCOPE_NOTE_TEMPLATE: str = (
    "사건 기준시각: {reference_time} · 조사 구간: 기준시각 이전 {lookback_minutes}분.\n"
    "증거는 반드시 이 구간에서 가져온다 — {tools} 를 호출할 때 "
    'reference_time="{reference_time}", lookback_minutes={lookback_minutes} 인자를 함께 넘길 것. '
    "인자 없이 호출하면 현재 시각 기준 최신 데이터가 나오며 그것은 이 사건의 증거가 아니다.\n"
    "구간 안의 전 알람은 polestar_incident_alarms 로 먼저 확보하라(선행 알람 탐색)."
)


def incident_scope_note(reference_time: str | None, lookback_minutes: int | None) -> str | None:
    """잡의 사건 구간을 조사 LLM 지침 한 단락으로 만든다. 기준시각이 없으면 None."""
    if not reference_time:
        return None
    return INCIDENT_SCOPE_NOTE_TEMPLATE.format(
        reference_time=reference_time,
        lookback_minutes=int(lookback_minutes or 0),
        tools=" · ".join(ANCHORED_TOOLS),
    )


def _fmt_offset(minutes: object) -> str:
    try:
        m = int(minutes)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "T?"
    return f"T{'-' if m < 0 else '+'}{abs(m)}m"


def correlation_note(correlation: dict | None) -> str | None:
    """결정적 상관 결과(plans/50 G4)를 지침 한 단락으로 — LLM은 이 수치를 계산하지 않고 **인용**한다."""
    if not correlation:
        return None
    lines = ["결정적 상관 결과(코드가 계산 — 아래 수치·순서를 그대로 인용하고 재계산하지 말 것):"]
    leading = correlation.get("leading_signal")
    lines.append(f"- 선행 신호: {leading if leading else '없음(이상 지표 없음 또는 알람 미선행)'}")
    for name, f in (correlation.get("metric_findings") or {}).items():
        if not isinstance(f, dict) or not f.get("is_anomalous"):
            continue
        lag = f.get("lead_lag_min")
        lag_s = f"첫 알람 대비 {lag:+d}분" if isinstance(lag, int) else "알람 대비 시차 미산출"
        lines.append(
            f"- {name}: {f.get('kind')} · onset {_fmt_offset(f.get('onset_offset_min'))} · "
            f"peak {f.get('peak_value')} @ {f.get('peak_time')} · z={f.get('z_score')} · {lag_s}"
        )
    summary = correlation.get("alarm_summary") or {}
    lines.append(
        f"- 구간 알람: {summary.get('count', 0)}건 "
        f"(첫 알람 {_fmt_offset(summary.get('first_offset_min'))}, 해소 {summary.get('resolved', 0)}건)"
    )
    for t in (correlation.get("timeline") or [])[:12]:
        if isinstance(t, dict):
            lines.append(f"  {_fmt_offset(t.get('t_offset_min'))} {t.get('kind')} {t.get('detail')}")
    for n in correlation.get("notes") or []:
        lines.append(f"- 한계: {n}")
    return "\n".join(lines)


def build_guidance(settings, job, *, remote: bool = True) -> str | None:  # noqa: ANN001 — AgentSettings · JobLike(덕 타이핑)
    """조사 지침을 조립한다. 넣을 것이 없으면 None(`ask()`는 부하 가드만 붙인다)."""
    parts: list[str] = []
    if remote:
        parts.append(REMOTE_VM_SHELL_NOTE)
    scope = incident_scope_note(
        getattr(job, "reference_time", None), getattr(job, "lookback_minutes", None)
    )
    if scope:
        parts.append(scope)
    corr = correlation_note(getattr(job, "correlation", None))
    if corr:
        parts.append(corr)
    extra = (getattr(settings, "investigation_guidance_extra", None) or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) or None


__all__ = ["ANCHORED_TOOLS", "INCIDENT_SCOPE_NOTE_TEMPLATE", "incident_scope_note", "correlation_note", "build_guidance"]
