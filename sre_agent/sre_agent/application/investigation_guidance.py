"""조사 지침 조립 (plans/50 G5 · SPEC-investigation-guidance · D-197).

`system_prompt_additions`를 넘기는 프로덕션 호출부가 0건이라(2026-08-27·09-02 실측) 원격 프로파일이
요구한 `REMOTE_VM_SHELL_NOTE`조차 주입되지 않았다. 여기서 지침을 한 문자열로 조립해
`_default_diagnose_fn`이 `DiagnosisAgent.ask()`에 넘긴다.

구성(순서 고정): ① REMOTE_VM_SHELL_NOTE(원격 프로파일) ② 사건 구간 지침(잡의 reference_time —
도구 호출에 앵커 인자를 쓰라는 지시) ③ 상관 결과 ④ kind별 플레이북(plans/91 1-5 · plans/51 §6 —
결정적 문구) ⑤ OpenMetrics 서술 노트(settings.openmetrics_guidance_enabled일 때만 ·
plans/92 O3 · R-12) ⑥ APM 노트(settings.apm_guidance_enabled일 때만 · plans/87 J3)
⑦ settings.investigation_guidance_extra(운영자 자유 지침).
부하 가드(LOAD_GUARD_NOTE)는 `ask()`가 항상 덧붙이므로 여기서 넣지 않는다.

계층: application. LLM을 호출하지 않는다(문자열 조립만).
"""

from __future__ import annotations

from sre_agent.domain.investigation_limits import (
    APM_UNAVAILABLE_CODES,
    is_apm_resource_type,
    is_apm_trigger,
)
from sre_agent.toolset_profiles import REMOTE_VM_SHELL_NOTE

# 앵커 인자를 받는 mcp_server 도구(incident-window-tools).
ANCHORED_TOOLS: tuple[str, ...] = (
    "polestar_metric_trend",
    "polestar_alarm_history",
    "polestar_incident_alarms",
    "prom_metric_range",
)

# 앵커 인자를 받는 제니퍼 게이트웨이 구간 도구(SPEC-apm-gateway §3). `apm_guidance_enabled`일 때만 사건창에 더한다.
# `apm_active_services`·`apm_resource_pool`은 현재값 전용이라 넣지 않는다(`APM_REALTIME_NOTE` — om_* 전례).
APM_ANCHORED_TOOLS: tuple[str, ...] = (
    "apm_app_health",
    "apm_runtime_health",
    "apm_events",
    "apm_slow_transactions",
)

INCIDENT_SCOPE_NOTE_TEMPLATE: str = (
    "사건 기준시각: {reference_time} · 조사 구간: 기준시각 이전 {lookback_minutes}분.\n"
    "증거는 반드시 이 구간에서 가져온다 — {tools} 를 호출할 때 "
    'reference_time="{reference_time}", lookback_minutes={lookback_minutes} 인자를 함께 넘길 것. '
    "인자 없이 호출하면 현재 시각 기준 최신 데이터가 나오며 그것은 이 사건의 증거가 아니다.\n"
    "구간 안의 전 알람은 polestar_incident_alarms 로 먼저 확보하라(선행 알람 탐색)."
)


def incident_scope_note(
    reference_time: str | None,
    lookback_minutes: int | None,
    tools: tuple[str, ...] = ANCHORED_TOOLS,
) -> str | None:
    """잡의 사건 구간을 조사 LLM 지침 한 단락으로 만든다. 기준시각이 없으면 None.

    `tools`는 `apm_guidance_enabled`일 때만 `ANCHORED_TOOLS + APM_ANCHORED_TOOLS`로 넘어온다 —
    기본값이면 렌더가 종전과 바이트 동일하다.
    """
    if not reference_time:
        return None
    return INCIDENT_SCOPE_NOTE_TEMPLATE.format(
        reference_time=reference_time,
        lookback_minutes=int(lookback_minutes or 0),
        tools=" · ".join(tools),
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
#
# R-16(plans/87 · D-195 ②): 게이트웨이 이벤트(`resourceType="apm.Instance"`)는 OS 키워드보다 **먼저** `apm`이다.
# 제니퍼 이벤트 이름(`JVM_HEAP_MEM_HIGH`·`OUTOFMEMORY`·`JVM_CPU_HIGH_LONGTIME`·`PROCESS_DOWN`…)이 부분 문자열
# 키워드에 걸려 OS 플레이북이 WAS 사건에 붙는 것을 막는다. 플래그와 무관하다(게이트웨이 이벤트에서만 발현).
# `apm`은 `PLAYBOOK_NOTES`에 없다 — APM 플레이북은 `apm_guidance_enabled`일 때만 붙는다.
# 판정 기준(`is_apm_resource_type`)은 dispatcher와 함께 쓰므로 domain `investigation_limits`에 있다.
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
    """알람 kind(apm·cpu·memory·disk·network·process·log) — 게이트 분류기와 동형(순서 고정 · 첫 매칭).

    `resourceType="apm.Instance"`(대소문자 무시)는 이름과 무관하게 `apm`이다(R-16 선판정).
    """
    if is_apm_resource_type(resource_type):
        return "apm"
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


def trigger_hints(job) -> dict:  # noqa: ANN001 — JobLike
    """트리거 페이로드 `meta.hints`(noise_gate가 apm 이벤트에만 싣는다 — SPEC-apm-sre-agent §3.7). 없으면 {}."""
    payload = getattr(job, "payload", None)
    meta = payload.get("meta") if isinstance(payload, dict) else None
    hints = meta.get("hints") if isinstance(meta, dict) else None
    return hints if isinstance(hints, dict) else {}


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


# ---------------------------------------------------------------------
# APM(제니퍼 게이트웨이) 지침 (plans/87 J3 · SPEC-apm-sre-agent §3.3 · D-035 — 결정적 문구, LLM 0)
#
# 전부 `apm_guidance_enabled`일 때만 붙는다 — 꺼져 있으면 조립 문자열이 종전과 바이트 동일하다.
# 폴백은 셸이 아니라 폴스타 MCP 도구다(D-233 — 운영 원격 조사에 셸 없음).
# ---------------------------------------------------------------------

APM_FOCUS_NOTE_TEMPLATE: str = (
    "APM(제니퍼 게이트웨이) 조사 순서 — 대상 호스트에 WAS 인스턴스가 있을 때(apm_* 도구):\n"
    "① apm_instance_map(hostname)으로 대상 인스턴스를 확정한다(match_confidence 확인).\n"
    "② apm_events로 사건 구간의 선행 이벤트를 본다.\n"
    "③ apm_app_health · apm_runtime_health로 골든 시그널(응답시간·TPS·오류율)과 런타임(힙·GC·CPU·스레드)을 본다.\n"
    "④ 증상별로 한 갈래를 판다 — 큐잉이면 apm_active_services, 지연이면 apm_slow_transactions → "
    "apm_transaction_profile(앞 도구가 준 profile_ref의 domain_id·txid·time_ms를 그대로 넘긴다), "
    "커넥션 풀이면 apm_resource_pool.\n"
    "⑤ 인프라와 대조한다 — polestar_metric_trend · prom_metric_range로 같은 구간의 호스트 지표를 본다.\n"
    "- 주 가설을 세우면 그 가설을 반증할 수 있는 도구를 1회 호출해 확인한다.\n"
    "- apm_* 도구를 부를 때 {investigation_id_arg} 인자를 함께 넘긴다(감사 추적).\n"
    "- was_signals는 게이트웨이가 결정적으로 판정한 결과다 — 임계를 다시 판단하지 말고 kind·evidence를 인용한다."
)

APM_REALTIME_NOTE: str = (
    "APM 현재값 도구(apm_active_services · apm_resource_pool) 서술 지침:\n"
    "- 이 두 도구는 조회 시점의 현재 상태만 돌려준다(구간 인자 없음). "
    "과거 사건의 증거로 서술하지 말고 '현재 상태'로만 쓴다.\n"
    "- 사건 구간 증거는 apm_events · apm_app_health · apm_runtime_health · apm_slow_transactions에서 가져온다."
)

APM_FALLBACK_NOTE: str = (
    "APM 미가용 시 폴백:\n"
    "- apm_* 도구가 없거나 error(" + " · ".join(APM_UNAVAILABLE_CODES) + ")를 돌려주면 "
    "폴스타 MCP 도구로 대체한다 — 프로세스(polestar_process_snapshot) · OS 구성(polestar_os_config) · "
    "메트릭 추세(polestar_metric_trend).\n"
    "- 대체했다는 사실과 사유(오류 코드·reason)를 결론의 한계에 적는다. 셸 명령으로 대체하지 않는다(원격 조사에 셸 없음)."
)

APM_NOT_CONFIGURED_NOTE: str = (
    "현재 APM 게이트웨이가 설정되지 않았다(APM_MCP_URL 미설정) — apm_* 도구가 없으므로 처음부터 위 폴백으로 조사하고, "
    "그 사실을 한계에 적는다."
)

APM_PLAYBOOK_NOTE: str = (
    "장애 유형 플레이북 — WAS(APM) 사건(plans/87 §5.4):\n"
    "- 증거: 트리거 이벤트(event_type · instance_id · txid) · apm_events 선행 이벤트 · "
    "apm_app_health 응답시간·오류율·reject_rate · apm_runtime_health 힙·GC·스레드 추세 · was_signals · "
    "같은 구간 호스트 지표.\n"
    "- 기법: 증상별로 분기한다. 큐잉·PLC 거절(SERVICE_QUEUING·PLC_REJECTED)이면 apm_active_services로 정체 지점"
    "(DB·외부 호출·동일 실행 모드)을, 커넥션 실패·미반납(JDBC_CONNECTION_FAIL·DB_CONN_UNCLOSED)이면 "
    "apm_resource_pool 사용률을, 힙·GC(OUTOFMEMORY·JVM_HEAP_MEM_HIGH·MAYBE_GC_TIME_DELAY)면 "
    "apm_runtime_health 추세(계단식 상승은 누수 의심)를, 응답 지연(TX_BAD_RESPONSE 등)이면 "
    "apm_slow_transactions 분해(sql·fetch·external 비중) → apm_transaction_profile을 본다.\n"
    "- 서술: was_signals kind·evidence → 선행 이벤트 → 인프라 대조 순으로 인용한다. 호스트 CPU·메모리 이상은 "
    "교차 증거로 병기하고, 인스턴스 정합 신뢰도가 medium 이하면 그 한계를 명시한다."
)

#: 트리거 힌트 중 지침에 싣는 키(순서 고정). `solution`은 플레이북 선택에만 쓴다.
_HINT_KEYS: tuple[str, ...] = ("event_type", "instance_id", "domain_id", "txid")


def apm_playbook_note(hints: dict) -> str:
    """APM 플레이북 + 트리거 힌트 한 줄(값이 있는 키만 — 도구 인자로 쓸 수 있게)."""
    shown = [f"{k}={hints[k]}" for k in _HINT_KEYS if hints.get(k) not in (None, "")]
    if not shown:
        return APM_PLAYBOOK_NOTE
    return f"{APM_PLAYBOOK_NOTE}\n- 트리거 힌트: " + " · ".join(shown)


def apm_notes(settings, job) -> list[str]:  # noqa: ANN001 — AgentSettings · JobLike
    """⑥ APM 노트(조사 순서 · 현재값 · 폴백 [· 미설정 사유]). `apm_guidance_enabled`일 때만 호출된다."""
    iid = getattr(job, "investigation_id", None)
    iid_arg = f'investigation_id="{iid}"' if iid else "investigation_id(이 조사의 id)"
    notes = [APM_FOCUS_NOTE_TEMPLATE.format(investigation_id_arg=iid_arg), APM_REALTIME_NOTE, APM_FALLBACK_NOTE]
    if not getattr(settings, "apm_mcp_url", ""):
        notes.append(APM_NOT_CONFIGURED_NOTE)
    return notes


def build_guidance(settings, job, *, remote: bool = True) -> str | None:  # noqa: ANN001 — AgentSettings · JobLike(덕 타이핑)
    """조사 지침을 조립한다. 넣을 것이 없으면 None(`ask()`는 부하 가드만 붙인다).

    순서: ① 원격 셸 ② 사건 구간 ③ 상관 결과 ④ **kind별 플레이북**(plans/91 1-5)
    ⑤ **OpenMetrics 서술 노트**(`openmetrics_guidance_enabled`일 때만 · plans/92 O3)
    ⑥ **APM 노트**(`apm_guidance_enabled`일 때만 · plans/87 J3) ⑦ 운영자 자유 지침.

    `apm_guidance_enabled`면 ②에 apm_* 구간 도구를 더하고, ④는 apm 사건(kind `apm` 또는 트리거
    `meta.hints.solution == "apm"`)이면 OS 플레이북 대신 APM 플레이북 하나만 싣는다.
    """
    apm_on = bool(getattr(settings, "apm_guidance_enabled", False))
    parts: list[str] = []
    if remote:
        parts.append(REMOTE_VM_SHELL_NOTE)
    scope = incident_scope_note(
        getattr(job, "reference_time", None),
        getattr(job, "lookback_minutes", None),
        ANCHORED_TOOLS + APM_ANCHORED_TOOLS if apm_on else ANCHORED_TOOLS,
    )
    if scope:
        parts.append(scope)
    corr = correlation_note(getattr(job, "correlation", None))
    if corr:
        parts.append(corr)
    if apm_on and is_apm_trigger(getattr(job, "payload", None)):   # kind apm 또는 hints.solution == apm
        playbook: str | None = apm_playbook_note(trigger_hints(job))
    else:
        playbook = playbook_note(alarm_kind_from_job(job))
    if playbook:
        parts.append(playbook)
    if getattr(settings, "openmetrics_guidance_enabled", False):
        parts.append(OPENMETRICS_NOTE)
    if apm_on:
        parts.extend(apm_notes(settings, job))
    extra = (getattr(settings, "investigation_guidance_extra", None) or "").strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts) or None


__all__ = ["ANCHORED_TOOLS", "APM_ANCHORED_TOOLS", "INCIDENT_SCOPE_NOTE_TEMPLATE", "PLAYBOOK_NOTES",
           "OPENMETRICS_NOTE", "APM_UNAVAILABLE_CODES", "APM_FOCUS_NOTE_TEMPLATE", "APM_REALTIME_NOTE",
           "APM_FALLBACK_NOTE", "APM_NOT_CONFIGURED_NOTE", "APM_PLAYBOOK_NOTE",
           "incident_scope_note", "correlation_note", "classify_alarm_kind", "alarm_kind_from_job",
           "trigger_hints", "playbook_note", "apm_playbook_note", "apm_notes", "build_guidance"]
