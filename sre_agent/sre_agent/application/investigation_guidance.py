"""조사 지침 조립 (plans/50 G5 · SPEC-investigation-guidance · D-197).

`system_prompt_additions`를 넘기는 프로덕션 호출부가 0건이라(2026-08-27·09-02 실측) 원격 프로파일이
요구한 `REMOTE_VM_SHELL_NOTE`조차 주입되지 않았다. 여기서 지침을 한 문자열로 조립해
`_default_diagnose_fn`이 `DiagnosisAgent.ask()`에 넘긴다.

구성(순서 고정): ① REMOTE_VM_SHELL_NOTE(원격 프로파일) ② 사건 구간 지침(잡의 reference_time —
도구 호출에 앵커 인자를 쓰라는 지시) ③ 상관 결과 ④ kind별 플레이북(plans/91 1-5 · plans/51 §6 —
결정적 문구) ⑤ OpenMetrics 서술 노트(settings.openmetrics_guidance_enabled일 때만 ·
plans/92 O3 · R-12) ⑥ settings.investigation_guidance_extra(운영자 자유 지침).
부하 가드(LOAD_GUARD_NOTE)는 `ask()`가 항상 덧붙이므로 여기서 넣지 않는다.

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


# ---------------------------------------------------------------------
# 장애 유형별 플레이북 (plans/91 1-5 · plans/51 §6 · D-035 — 결정적 문구, LLM 0)
#
# 알람 kind는 게이트(noise_gate `classify_alarm_kind`)와 **같은 어휘·같은 판정 순서**로 페이로드
# `event.resourceType`·`event.alarmName`에서 유도한다(패키지 경계상 import 불가 — 동형 재정의, D-139).
# 미매칭 kind면 아무것도 덧붙이지 않아 종전 지침과 문자열이 같다.
# ---------------------------------------------------------------------

_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cpu", ("cpu",)),
    ("memory", ("memory", "메모리", "mem")),
    ("disk", ("disk", "디스크", "volume", "filesystem", "inode")),
    ("network", ("network", "net", "traffic", "네트워크", "bandwidth")),
    ("process", ("process", "프로세스", "daemon", "service down", "프로세스다운")),
    ("log", ("log", "logmonitor", "로그")),
)


def classify_alarm_kind(resource_type: str | None, alarm_name: str | None) -> str | None:
    """알람 kind(cpu·memory·disk·network·process·log) — 게이트 분류기와 동형(순서 고정 · 첫 매칭)."""
    haystack = f"{resource_type or ''} {alarm_name or ''}".lower()
    if not haystack.strip():
        return None
    for kind, words in _KIND_KEYWORDS:
        if any(w in haystack for w in words):
            return kind
    return None


def alarm_kind_from_job(job) -> str | None:  # noqa: ANN001 — JobLike
    payload = getattr(job, "payload", None)
    if not isinstance(payload, dict):
        return None
    event = payload.get("event") or {}
    if not isinstance(event, dict):
        return None
    return classify_alarm_kind(event.get("resourceType"), event.get("alarmName"))


PLAYBOOK_NOTES: dict[str, str] = {
    "cpu": (
        "장애 유형 플레이북 — CPU 포화(plans/51 §6.1):\n"
        "- 증거: CPU Util 추이(polestar_metric_trend kind=cpu) · 구간 알람 타임라인 · 실시간 Top CPU 프로세스 · "
        "가능하면 iowait/steal 분해와 런큐(원격 vmstat/mpstat) · 변경 이벤트.\n"
        "- 기법: USE(사용률·포화·오류) 순으로 본다. iowait↑면 디스크로 드릴다운, steal↑면 가상화 경합, 단일 핫코어면 "
        "단일 스레드 앱. 타임라인으로 지표가 알람에 선행했는지 먼저 판정한다.\n"
        "- 서술: \"Top 프로세스 X가 CPU Util 선행 상승과 일치 → 유력 원인(신뢰도 medium — 프로세스 목록은 현재 단면)\" "
        "형식으로, 수치는 도구 출력에서 인용한다."
    ),
    "memory": (
        "장애 유형 플레이북 — 메모리 고갈/OOM(plans/51 §6.2):\n"
        "- 증거: Mem Util 추이(kind=memory) · OOM 로그(dmesg 'Out of memory: Killed process') 또는 syslog 관제 알람 · "
        "실시간 Top Mem 프로세스 · swap/slab 분해와 누수 추이(원격 free/vmstat).\n"
        "- 기법: vmstat si/so 스왑과 OOM 시그니처가 결정적 증거다. 추이로 누수(지속 증가) vs 스파이크(급등)를 구분한다.\n"
        "- 서술: OOM 라인의 killed process와 Mem 추이를 함께 인용한다. swap 분해가 없으면 그 한계를 명시한다."
    ),
    "disk": (
        "장애 유형 플레이북 — 디스크 풀/inode/IO 지연(plans/51 §6.3):\n"
        "- 증거: FS Util·Disk MaxIORate 추이(kind=filesystem·disk_io) · df -i(inode) · iostat await/%util · "
        "삭제된 열린 파일(lsof +L1) · FS read-only 리마운트(dmesg) · FS 알람.\n"
        "- 기법: 용량이 100%가 아닌데 쓰기 실패면 inode 고갈을 의심한다. await↑와 %util↑가 함께면 IO 포화, "
        "RO 리마운트는 FS 손상 신호다.\n"
        "- 서술: \"FS Util 100% + df -i 99% → inode 고갈\"처럼 두 증거를 결합해 쓴다. iostat이 없으면 IO 포화 단정을 보류한다."
    ),
    "network": (
        "장애 유형 플레이북 — 네트워크 이상(plans/51 §6.4):\n"
        "- 증거: 인터페이스 errors/drops · ss 상태·재전송 · conntrack/ephemeral 포트 고갈 · Netstat 리소스 · NW 알람.\n"
        "- 기법: USE(사용률·포화·오류). 재전송↑면 네트워크/원격 문제, TIME_WAIT 폭증은 커넥션 과다, conntrack 고갈은 "
        "신규 연결 실패로 나타난다.\n"
        "- 서술: 손실·재전송 수치를 인용한다. 대부분 원격 명령(L2/L3)에 의존하므로 L1 지표만 있으면 신뢰도 제한을 명시한다."
    ),
    "process": (
        "장애 유형 플레이북 — 프로세스 다운/플래핑(plans/51 §6.5):\n"
        "- 증거: ProcessMonitor avail_status·알람 · 실시간 프로세스 존재 여부 · 크래시 시그널(dmesg segfault) · "
        "FD/스레드 한계 · 변경 이벤트.\n"
        "- 기법: avail_status 변화 타임라인으로 재시작 루프(짧은 간격 반복)를 판정하고, segfault/FD 고갈로 원인을 분기한다.\n"
        "- 서술: \"ProcessMonitor ntpd 14:02 다운, 직전 배포 13:58 → 변경 기반 용의(변경 이벤트 확인 시 신뢰도 상승)\" 형식."
    ),
    "log": (
        "장애 유형 플레이북 — 로그 패턴 오류(plans/51 §6.6):\n"
        "- 증거: LogMonitor 알람의 conditionLogText · 전체 로그 컨텍스트(원격 journalctl) · 동시간대 타 신호.\n"
        "- 기법: 매칭 로그 줄을 타임라인에 배치하고 동반 자원 이상과 상관시킨다. 보안 로그면 인증 로그와 교차한다.\n"
        "- 서술: 매칭 로그 줄을 그대로 인용한다(환각 금지). 전체 컨텍스트가 없으면 \"추가 로그 확인 필요\"라고 쓴다."
    ),
}


def playbook_note(kind: str | None) -> str | None:
    """kind별 결정적 플레이북 문구. 미매칭이면 None(종전 지침과 문자열 동일)."""
    return PLAYBOOK_NOTES.get(kind) if kind else None


# ---------------------------------------------------------------------
# OpenMetrics 서술 노트 (plans/92 O3 · R-12 · D-035 — 결정적 문구, LLM 0)
#
# `om_*`는 **현재값 전용**이라 ANCHORED_TOOLS에 넣지 않는다 — 사건 구간 노트가 "앵커 인자 없이
# 호출하면 사건 증거가 아니다"라고 지시하는데 `om_*`에는 앵커 인자가 없다. 대신 "현재 상태로만
# 서술"을 여기서 못 박는다. 설정(`openmetrics_guidance_enabled`)이 꺼져 있으면 붙이지 않는다 —
# 조립 문자열이 종전과 바이트 동일해 조사 LLM 프롬프트 접두(KV 캐시)가 흔들리지 않는다.
# ---------------------------------------------------------------------

OPENMETRICS_NOTE: str = (
    "OpenMetrics 현재값 도구(om_metric_instant · om_metric_catalog) 서술 지침:\n"
    "- om_* 결과는 조회 시점(observed_at)의 현재 상태다. "
    "사건 구간 증거로 인용하지 말고 '현재 상태'로만 서술하라. "
    "이력·rate는 없고 counter는 누적값이다(types 참조).\n"
    "- prom_* 도구가 PROMETHEUS_URL 미설정 오류와 hint를 함께 돌려주면 "
    "hint의 도구로 현재값을 조회하라. "
    "메트릭 이름은 om_metric_catalog로 먼저 확인하고 추측하지 마라.\n"
    "- 메트릭 도구 결과의 source_kind가 openmetrics면 "
    "Prometheus 대신 exporter에서 직접 읽은 현재값이다. "
    "fallback_reason이 있으면 그대로 언급하라.\n"
    "- target_identity가 mismatch면 스크레이프 허용목록 오등록 가능성을 명시하고, "
    "그 값을 그 서버의 증거로 쓰지 마라."
)


def build_guidance(settings, job, *, remote: bool = True) -> str | None:  # noqa: ANN001 — AgentSettings · JobLike(덕 타이핑)
    """조사 지침을 조립한다. 넣을 것이 없으면 None(`ask()`는 부하 가드만 붙인다).

    순서: ① 원격 셸 ② 사건 구간 ③ 상관 결과 ④ **kind별 플레이북**(plans/91 1-5)
    ⑤ **OpenMetrics 서술 노트**(`openmetrics_guidance_enabled`일 때만 · plans/92 O3)
    ⑥ 운영자 자유 지침.
    """
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
    playbook = playbook_note(alarm_kind_from_job(job))
    if playbook:
        parts.append(playbook)
    if getattr(settings, "openmetrics_guidance_enabled", False):
        parts.append(OPENMETRICS_NOTE)
    extra = (getattr(settings, "investigation_guidance_extra", None) or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) or None


__all__ = ["ANCHORED_TOOLS", "INCIDENT_SCOPE_NOTE_TEMPLATE", "PLAYBOOK_NOTES", "OPENMETRICS_NOTE",
           "incident_scope_note", "correlation_note", "classify_alarm_kind", "alarm_kind_from_job",
           "playbook_note", "build_guidance"]
