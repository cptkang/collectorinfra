"""`sre_agent` 제니퍼 소비측 배선 (plans/87 J3 · spec/SPEC-apm-sre-agent.md · LLM 0회).

범위: 설정 4키 · `_build_mcp_servers` 두 번째 서버 · R-16 kind 선판정 · APM 지침(앵커·노트·플레이북·힌트) ·
브리핑 소스 라벨·한계 · 폴백 사유 노출 · 정체 가드(사후 판정). 플래그 off 바이트 동일을 명시 단언한다.
게이트웨이 반환 모양은 `spec/SPEC-apm-gateway.md` §3을 복제한다(`apm_gateway` import 0 — D-274 ③).
"""

from __future__ import annotations

import itertools
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from sre_agent.application.briefing_builder import (
    APM_SOURCE_LABEL,
    INFRA_SOURCE_LABEL,
    UNRESOLVED_PREFIX,
    build_briefing,
)
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_guidance import (
    ANCHORED_TOOLS,
    APM_ANCHORED_TOOLS,
    APM_FALLBACK_NOTE,
    APM_NOT_CONFIGURED_NOTE,
    APM_PLAYBOOK_NOTE,
    APM_REALTIME_NOTE,
    OPENMETRICS_NOTE,
    PLAYBOOK_NOTES,
    alarm_kind_from_job,
    build_guidance,
    classify_alarm_kind,
    correlation_note,
    incident_scope_note,
    playbook_note,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, to_tool_records
from sre_agent.domain.investigation_limits import (
    APM_NOT_CALLED_LIMIT,
    APM_NOT_CONFIGURED_LIMIT,
    apm_limitations,
    repeated_calls,
)
from sre_agent.domain.severity_signatures import ImportanceVerdict
from sre_agent.interface import mcp_service
from sre_agent.interface.mcp_service import _build_mcp_servers
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import REMOTE_VM_SHELL_NOTE

_TOP = Path(__file__).resolve().parents[1]
_APM_KEYS = ("APM_MCP_URL", "APM_MCP_TOKEN", "APM_GUIDANCE_ENABLED", "APM_SIGNATURES_ENABLED")


def _settings(**kw) -> AgentSettings:
    base = {"model": "test/model", "api_key": None, "gemini_api_key": None}
    return AgentSettings(_env_file=None, **{**base, **kw})


def _job(**kw) -> SimpleNamespace:
    base = {"kind": "alarm", "question": None, "payload": None, "reference_time": None,
            "lookback_minutes": None}
    base.update(kw)
    return SimpleNamespace(**base)


# ── 1. 설정 ──────────────────────────────────────────────────────────────


def test_apm_settings_defaults(monkeypatch):
    for key in _APM_KEYS:
        monkeypatch.delenv(key, raising=False)
    s = _settings()
    assert s.apm_mcp_url == "" and s.apm_mcp_token is None
    assert s.apm_guidance_enabled is False and s.apm_signatures_enabled is False


def test_apm_settings_env_loading(monkeypatch):
    monkeypatch.setenv("APM_MCP_URL", "http://127.0.0.1:9096/sse")
    monkeypatch.setenv("APM_MCP_TOKEN", "gw-secret")
    monkeypatch.setenv("APM_GUIDANCE_ENABLED", "true")
    monkeypatch.setenv("APM_SIGNATURES_ENABLED", "true")
    s = _settings()
    assert s.apm_mcp_url == "http://127.0.0.1:9096/sse"
    assert isinstance(s.apm_mcp_token, SecretStr) and s.apm_mcp_token.get_secret_value() == "gw-secret"
    assert "gw-secret" not in repr(s)
    assert s.apm_guidance_enabled is True and s.apm_signatures_enabled is True


def test_env_example_lists_apm_keys_without_inline_comments():
    lines = (_TOP / ".env.example").read_text(encoding="utf-8").splitlines()
    for key in _APM_KEYS:
        hits = [ln for ln in lines if ln.lstrip("# ").startswith(f"{key}=")]
        assert hits, f".env.example에 {key} 없음"
        assert all(" #" not in ln.split("=", 1)[1] for ln in hits), f"{key} 인라인 주석 금지"


# ── 2. _build_mcp_servers ────────────────────────────────────────────────


def test_mcp_servers_unset_apm_is_identical_to_pre_j3():
    """★ APM_MCP_URL 미설정 — 종전 dict(또는 None)와 같다."""
    url = "http://localhost:9099/sse"
    assert _build_mcp_servers(_settings(polestar_mcp_url=url)) == {
        "polestar": {"config": {"mode": "sse", "url": url, "health_check_tool": "list_sources"}}}
    assert _build_mcp_servers(_settings(polestar_mcp_url=url, polestar_mcp_token="t")) == {
        "polestar": {"config": {"mode": "sse", "url": url, "health_check_tool": "list_sources",
                                "headers": {"Authorization": "Bearer t"}}}}
    assert _build_mcp_servers(_settings(polestar_mcp_url="")) is None


def test_mcp_servers_both_registered():
    servers = _build_mcp_servers(_settings(
        polestar_mcp_url="http://localhost:9099/sse", apm_mcp_url="http://127.0.0.1:9096/sse",
        apm_mcp_token="gw"))
    assert list(servers) == ["polestar", "apm"]
    assert servers["apm"] == {"config": {"mode": "sse", "url": "http://127.0.0.1:9096/sse",
                                         "health_check_tool": "gateway_health",
                                         "headers": {"Authorization": "Bearer gw"}}}
    assert servers["polestar"]["config"]["health_check_tool"] == "list_sources"


def test_mcp_servers_apm_only():
    servers = _build_mcp_servers(_settings(polestar_mcp_url="", apm_mcp_url="http://gw:9096/sse",
                                           apm_mcp_token=""))
    assert servers == {"apm": {"config": {"mode": "sse", "url": "http://gw:9096/sse",
                                          "health_check_tool": "gateway_health"}}}


# ── 3. R-16 — kind 선판정 ────────────────────────────────────────────────

# plans/87 §2.3 제니퍼 이벤트 유형 표본(ERROR_*·WARNING_* 계열).
JENNIFER_TYPES = (
    "SERVICE_QUEUING", "PLC_REJECTED", "JDBC_CONNECTION_FAIL", "DB_CONNECTION_FAIL", "OUTOFMEMORY",
    "SYSTEM_DOWN", "PROCESS_DOWN", "JVM_DOWN", "JVM_CPU_HIGH_LONGTIME", "HIGH_RATE_REJECT",
    "HIGH_RATE_FAIL", "MAYBE_GC_TIME_DELAY", "MAYBE_BUSY_PROCESS", "UNCAUGHT_EXCEPTION",
    "TX_BAD_RESPONSE", "APP_BAD_RESPONSE", "DB_BAD_RESPONSE", "JDBC_BAD_RESPONSE", "TX_CALL_EXCEPTION",
    "JDBC_STMT_EXCEPTION", "JVM_CPU_HIGH", "JVM_HEAP_MEM_HIGH", "RESOURCE_LEAK", "DB_TOOMANY_FETCH",
    "DB_CONN_UNCLOSED",
)
_OS_KINDS = {"cpu", "memory", "disk", "network", "process", "log"}
_SAMPLES = [f"{prefix}{t}" for t in JENNIFER_TYPES for prefix in ("", "ERROR_", "WARNING_")]


@pytest.mark.parametrize("alarm_name", _SAMPLES)
def test_r16_jennifer_types_classify_as_apm(alarm_name):
    for resource_type in ("apm.Instance", "APM.INSTANCE", "apm.instance"):
        kind = classify_alarm_kind(resource_type, alarm_name)
        assert kind == "apm" and kind not in _OS_KINDS


def test_r16_precedence_is_what_prevents_os_kinds():
    """선판정이 없으면 부분 문자열 키워드가 OS kind로 잡는 이름들(2026-09-29 실측 재현)."""
    leaking = {"WARNING_JVM_HEAP_MEM_HIGH": "memory", "ERROR_OUTOFMEMORY": "memory",
               "ERROR_JVM_CPU_HIGH_LONGTIME": "cpu", "ERROR_PROCESS_DOWN": "process"}
    for name, os_kind in leaking.items():
        assert classify_alarm_kind("server.Server", name) == os_kind
        assert classify_alarm_kind("apm.Instance", name) == "apm"


@pytest.mark.parametrize("resource_type,alarm_name,expected", [
    ("server.Server", "CPU Utilization Critical", "cpu"),
    ("server.Cpus", "CPU Utilization", "cpu"),
    ("server.Memory", "Memory Utilization", "memory"),
    ("server.FileSystem", "FS Util 90%", "disk"),
    ("server.ProcessMonitor", "ntpd down", "process"),
    ("server.Server", "Host unreachable", None),
    ("", "", None),
    (None, None, None),
])
def test_r16_polestar_events_unchanged(resource_type, alarm_name, expected):
    assert classify_alarm_kind(resource_type, alarm_name) == expected


def test_apm_kind_is_not_an_os_playbook():
    assert "apm" not in PLAYBOOK_NOTES and playbook_note("apm") is None


# ── 4. 지침 ──────────────────────────────────────────────────────────────

_APM_EVENT = {"event": {"resourceType": "apm.Instance", "alarmName": "WARNING_JVM_HEAP_MEM_HIGH",
                        "serverName": "was-01", "hostname": "was-01", "severity": 2},
              "meta": {"hints": {"solution": "apm", "instance_id": 1001, "domain_id": 1000,
                                 "event_type": "WARNING_JVM_HEAP_MEM_HIGH", "txid": ""}}}
_MEMORY_EVENT = {"event": {"resourceType": "server.Memory", "alarmName": "Memory Utilization"}}
_CORR = {"leading_signal": "memory", "metric_findings": {}, "alarm_summary": {}, "timeline": [], "notes": []}


def _pre_j3_guidance(job, *, extra, om, remote) -> str | None:
    """J3 이전 조립 규칙의 재현 — 공개 조각 함수(기본 앵커)만으로 만든 대조 기준."""
    parts = [REMOTE_VM_SHELL_NOTE] if remote else []
    for piece in (incident_scope_note(job.reference_time, job.lookback_minutes),
                  correlation_note(getattr(job, "correlation", None)),
                  playbook_note(alarm_kind_from_job(job)),
                  OPENMETRICS_NOTE if om else None,
                  (extra or "").strip() or None):
        if piece:
            parts.append(piece)
    return "\n\n".join(parts) or None


def _combo_job(scope: bool, corr: bool, playbook: bool) -> SimpleNamespace:
    kw: dict = {}
    if scope:
        kw.update(reference_time="2026-09-29T10:00:00", lookback_minutes=60)
    if corr:
        kw["correlation"] = _CORR
    if playbook:
        kw["payload"] = _MEMORY_EVENT
    return _job(**kw)


@pytest.mark.parametrize("remote,scope,corr,playbook,with_extra,om",
                         list(itertools.product((True, False), repeat=6)))
def test_guidance_off_is_byte_identical_for_polestar_events(remote, scope, corr, playbook, with_extra, om):
    """★ apm_guidance_enabled off(기본·명시) — 폴스타 이벤트 지침 문자열이 종전과 바이트 동일."""
    extra = "운영자 지침" if with_extra else None
    job = _combo_job(scope, corr, playbook)
    expected = _pre_j3_guidance(job, extra=extra, om=om, remote=remote)
    kw = {"investigation_guidance_extra": extra, "openmetrics_guidance_enabled": om}
    assert build_guidance(_settings(**kw), job, remote=remote) == expected
    assert build_guidance(_settings(apm_guidance_enabled=False, apm_mcp_url="http://gw/sse", **kw),
                          job, remote=remote) == expected


def test_guidance_off_apm_event_gets_no_os_playbook():
    """R-16(플래그 무관) — WAS 사건에 OS 메모리 플레이북이 붙지 않는다 · APM 노트도 없다."""
    g = build_guidance(_settings(), _job(payload=_APM_EVENT))
    assert g == build_guidance(_settings(), _job())
    assert PLAYBOOK_NOTES["memory"] not in g and APM_PLAYBOOK_NOTE not in g


def test_incident_scope_note_default_unchanged_and_apm_anchors_opt_in():
    assert ANCHORED_TOOLS == ("polestar_metric_trend", "polestar_alarm_history",
                              "polestar_incident_alarms", "prom_metric_range")
    base = incident_scope_note("2026-09-29T10:00:00", 60)
    g_on = build_guidance(_settings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"),
                          _job(reference_time="2026-09-29T10:00:00", lookback_minutes=60))
    assert base not in g_on
    anchored = incident_scope_note("2026-09-29T10:00:00", 60, ANCHORED_TOOLS + APM_ANCHORED_TOOLS)
    assert anchored in g_on
    for tool in APM_ANCHORED_TOOLS:
        assert tool in anchored and tool not in base
    assert "apm_active_services" not in anchored and "apm_resource_pool" not in anchored


def test_guidance_on_apm_event_injects_apm_playbook_only():
    s = _settings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse",
                  investigation_guidance_extra="운영자 지침", openmetrics_guidance_enabled=True)
    job = _job(payload=_APM_EVENT, investigation_id="inv-42",
               reference_time="2026-09-29T10:00:00", lookback_minutes=30)
    g = build_guidance(s, job)
    assert g.count(APM_PLAYBOOK_NOTE) == 1
    assert not any(note in g for note in PLAYBOOK_NOTES.values())
    assert "트리거 힌트: event_type=WARNING_JVM_HEAP_MEM_HIGH · instance_id=1001 · domain_id=1000" in g
    assert "txid=" not in g.split("트리거 힌트:")[1].split("\n")[0]     # 빈 txid는 싣지 않는다
    assert 'investigation_id="inv-42"' in g
    for token in ("apm_instance_map", "apm_events", "apm_app_health", "apm_runtime_health",
                  "apm_active_services", "apm_slow_transactions", "apm_transaction_profile",
                  "profile_ref", "apm_resource_pool", "polestar_metric_trend", "prom_metric_range", "반증"):
        assert token in g, token
    assert APM_REALTIME_NOTE in g and APM_FALLBACK_NOTE in g and APM_NOT_CONFIGURED_NOTE not in g
    # 순서: 플레이북 → OpenMetrics 노트 → APM 노트 → 운영자 지침
    assert g.index(APM_PLAYBOOK_NOTE) < g.index(OPENMETRICS_NOTE) < g.index("APM(제니퍼 게이트웨이) 조사 순서") \
        < g.index(APM_FALLBACK_NOTE) < g.index("운영자 지침")


def test_guidance_on_hint_solution_selects_apm_playbook():
    payload = {"event": {"resourceType": "server.Server", "alarmName": "CPU Utilization"},
               "meta": {"hints": {"solution": "apm", "event_type": "ERROR_SERVICE_QUEUING"}}}
    g = build_guidance(_settings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"), _job(payload=payload))
    assert APM_PLAYBOOK_NOTE in g and PLAYBOOK_NOTES["cpu"] not in g
    assert "트리거 힌트: event_type=ERROR_SERVICE_QUEUING" in g


def test_guidance_on_polestar_event_keeps_os_playbook():
    g = build_guidance(_settings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"),
                       _job(payload=_MEMORY_EVENT))
    assert PLAYBOOK_NOTES["memory"] in g and APM_PLAYBOOK_NOTE not in g
    assert APM_FALLBACK_NOTE in g


def test_guidance_on_without_url_states_fallback_reason():
    """APM 미가용(게이트웨이 미설정) — 폴백 사유가 지침에 나온다."""
    g = build_guidance(_settings(apm_guidance_enabled=True, apm_mcp_url=""), _job(payload=_APM_EVENT))
    assert APM_NOT_CONFIGURED_NOTE in g and "APM_MCP_URL 미설정" in g
    for code in ("source_unavailable", "instance_unresolved", "not_configured"):
        assert code in APM_FALLBACK_NOTE
    for tool in ("polestar_process_snapshot", "polestar_os_config", "polestar_metric_trend"):
        assert tool in APM_FALLBACK_NOTE


def test_default_diagnose_fn_wires_apm_server_and_notes(monkeypatch):
    captured: list = []

    class _FakeAgent:
        def __init__(self, settings, toolsets=None, mcp_servers=None):
            captured.append(("servers", mcp_servers))

        def ask(self, question, system_prompt_additions=None):
            captured.append(("guidance", system_prompt_additions))
            return SimpleNamespace(answer="ok", tool_outputs=[], tool_calls=[], total_tokens=0, total_cost=0.0)

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", _FakeAgent)
    s = _settings(apm_guidance_enabled=True, apm_mcp_url="http://gw:9096/sse")
    mcp_service._default_diagnose_fn(s)(_job(payload=_APM_EVENT, investigation_id="inv-7"))
    servers = dict(captured)["servers"]
    guidance = dict(captured)["guidance"]
    assert servers["apm"]["config"]["health_check_tool"] == "gateway_health"
    assert APM_PLAYBOOK_NOTE in guidance and 'investigation_id="inv-7"' in guidance


# ── 5. 브리핑 — 소스 라벨 · 미결 · off 바이트 동일 ───────────────────────


def _verdict() -> ImportanceVerdict:
    return ImportanceVerdict(level="경고", confidence="none", escalate=False)


_ANSWER = "힙 사용률 0.93 계단식 상승 ← apm_runtime_health\nCPU 추이 정상 ← polestar_metric_trend\n결론 서술"
_TOOLS = ["apm_runtime_health", "polestar_metric_trend"]


def test_briefing_defaults_are_byte_identical():
    base = build_briefing(answer=_ANSWER, verdict=_verdict(), tool_names=_TOOLS, gate_tier="PAGE",
                          correlation=_CORR | {"timeline": [{"t_offset_min": -5, "kind": "metric", "detail": "mem"}]})
    explicit = build_briefing(answer=_ANSWER, verdict=_verdict(), tool_names=_TOOLS, gate_tier="PAGE",
                              correlation=_CORR | {"timeline": [{"t_offset_min": -5, "kind": "metric", "detail": "mem"}]},
                              source_labels=False, unresolved=False)
    assert base == explicit
    assert not any(ln.startswith("[") for ln in base["timeline"])


def test_briefing_source_labels_split_apm_and_infra():
    corr = _CORR | {"timeline": [{"t_offset_min": -5, "kind": "metric", "detail": "mem 급등"}]}
    b = build_briefing(answer=_ANSWER, verdict=_verdict(), tool_names=_TOOLS, gate_tier="PAGE",
                       correlation=corr, source_labels=True)
    assert b["timeline"] == [
        f"[{INFRA_SOURCE_LABEL}] T-5m 메트릭 mem 급등",
        f"[{APM_SOURCE_LABEL}] 힙 사용률 0.93 계단식 상승 ← apm_runtime_health",
        f"[{INFRA_SOURCE_LABEL}] CPU 추이 정상 ← polestar_metric_trend",
    ]
    assert set(b) == set(build_briefing(answer="x ← t", verdict=_verdict(), tool_names=["t"]))


def test_briefing_label_requires_called_apm_tool():
    b = build_briefing(answer="apm_events 에 따르면 ← 근거", verdict=_verdict(),
                       tool_names=["polestar_metric_trend"], source_labels=True)
    assert b["timeline"] == [f"[{INFRA_SOURCE_LABEL}] apm_events 에 따르면 ← 근거"]


def test_briefing_unresolved_prefixes_summary():
    b = build_briefing(answer=_ANSWER, verdict=_verdict(), tool_names=_TOOLS, unresolved=True,
                       limitations=["조사 정체 — …"])
    assert b["summary"].startswith(UNRESOLVED_PREFIX)
    assert b["limitations"][0] == "조사 정체 — …"


# ── 6. 한계 — 폴백·정합 신뢰도·limits ─────────────────────────────────────


def _err(code: str, tool: str = "apm_app_health", reason: str = "Domain is not connected") -> dict:
    return {"error": code, "reason": reason, "source_kind": "apm_api", "source": "jennifer", "tool": tool}


def test_apm_limitations_cover_fallback_confidence_and_limits():
    payloads = [
        _err("source_unavailable"),
        _err("instance_unresolved", tool="apm_runtime_health", reason="hostname 정합 실패"),
        _err("invalid_argument", reason="n 범위 밖"),           # 미가용 코드가 아니다 → 폴백 문구 없음
        {"rows": [], "source_kind": "apm_api", "tool": "apm_events",
         "instance_resolution": {"matched": True, "confidence": "medium", "reason": "ipAddress"},
         "limits": ["[한계] 1분 창 분할 상한(10분) 도달", "[한계] 1분 창 분할 상한(10분) 도달"]},
        {"rows": [], "source_kind": "apm_api", "tool": "apm_app_health",
         "instance_resolution": {"matched": True, "confidence": "high", "reason": "hostName"}, "limits": []},
    ]
    assert apm_limitations(payloads, apm_configured=True) == [
        "APM 미가용(apm_app_health: source_unavailable — Domain is not connected) — 폴스타 MCP 도구로 대체",
        "APM 미가용(apm_runtime_health: instance_unresolved — hostname 정합 실패) — 폴스타 MCP 도구로 대체",
        "APM 인스턴스 정합 신뢰도 medium(ipAddress) — apm_events 결과의 인스턴스 귀속이 불확실",
        "APM apm_events: 1분 창 분할 상한(10분) 도달",
    ]
    assert apm_limitations([], apm_configured=False) == [APM_NOT_CONFIGURED_LIMIT]
    assert apm_limitations([], apm_configured=True) == []
    # 설정됐는데 apm 사건에서 apm_* 호출 0건 — 헬스체크 실패로 도구가 없으면 오류 반환도 없다.
    assert apm_limitations([], apm_configured=True, apm_incident=True, apm_called=False) == [APM_NOT_CALLED_LIMIT]
    assert apm_limitations([], apm_configured=True, apm_incident=True, apm_called=True) == []
    assert apm_limitations([], apm_configured=True, apm_incident=False, apm_called=False) == []
    assert apm_limitations([], apm_configured=False, apm_incident=True) == [APM_NOT_CONFIGURED_LIMIT]


def test_repeated_calls_threshold():
    call = ("apm_events", '{"hostname": "was-01"}')
    assert repeated_calls([call, call]) == []
    assert repeated_calls([call, ("x", "{}"), call, call]) == [("apm_events", '{"hostname": "was-01"}', 3)]


def test_tool_records_keep_params():
    from holmes.core.models import ToolCallResult
    from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus

    def _tc(params):
        return ToolCallResult(tool_call_id="c", tool_name="apm_events", description="d",
                              result=StructuredToolResult(status=StructuredToolResultStatus.SUCCESS,
                                                          data="{}", params=params))

    records = to_tool_records([_tc({"hostname": "was-01"}), _tc(None)])
    assert records[0].params == {"hostname": "was-01"} and records[1].params is None


# ── 7. dispatcher 배선 — 폴백 사유 · 라벨 · 정체 가드 (LLM 0) ───────────────


def _dsettings(**kw) -> AgentSettings:
    base = {"model": "test/model", "api_key": None, "gemini_api_key": "k", "max_steps": 3,
            "investigation_dedup_ttl_seconds": None, "investigation_hourly_budget": None}
    return AgentSettings(_env_file=None, **{**base, **kw})


def _alarm_job() -> InvestigationJob:
    now = time.time()
    return InvestigationJob(investigation_id=f"inv-{now}", kind="alarm", status="running",
                            created_at=now, updated_at=now, fingerprint=None, payload=_APM_EVENT)


def _run(settings: AgentSettings, records: list[ToolCallRecord], answer: str = _ANSWER):
    audit: list[dict] = []

    def _diagnose(job):
        return DiagnosisResult(answer=answer, tool_calls=[r.description for r in records], tool_outputs=records)

    disp = InvestigationDispatcher(settings, diagnose_fn=_diagnose, briefing_fn=build_briefing)
    disp._audit = audit.append   # 감사 레코드 수집(파일 미사용)
    job = _alarm_job()
    disp(job)
    disp.wait_workers(5)
    assert job.status == "done"
    return job, audit


def _rec(tool: str, output: str = "{}", params: dict | None = None, description: str = "") -> ToolCallRecord:
    return ToolCallRecord(tool, description or f"apm: {tool} {params}", "success", output, params=params)


def test_dispatcher_on_exposes_fallback_reason_and_labels():
    records = [_rec("apm_runtime_health", json.dumps(_err("source_unavailable", tool="apm_runtime_health"))),
               _rec("polestar_metric_trend", '{"rows": []}', {"kind": "memory"})]
    job, _ = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"), records)
    b = job.briefing
    assert "APM 미가용(apm_runtime_health: source_unavailable — Domain is not connected) — 폴스타 MCP 도구로 대체" \
        in b["limitations"]
    assert f"[{APM_SOURCE_LABEL}] 힙 사용률 0.93 계단식 상승 ← apm_runtime_health" in b["timeline"]
    assert f"[{INFRA_SOURCE_LABEL}] CPU 추이 정상 ← polestar_metric_trend" in b["timeline"]
    assert not b["summary"].startswith(UNRESOLVED_PREFIX)


def test_dispatcher_on_without_url_puts_not_configured_in_limitations():
    job, _ = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url=""),
                  [_rec("polestar_metric_trend", '{"rows": []}', {"kind": "cpu"})])
    assert APM_NOT_CONFIGURED_LIMIT in job.briefing["limitations"]


def test_dispatcher_on_apm_incident_without_apm_calls_states_absence():
    """게이트웨이가 죽어 apm_* 도구가 등록되지 않은 경우 — 오류 반환이 없어도 한계에 사유가 나온다."""
    job, _ = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"),
                  [_rec("polestar_metric_trend", '{"rows": []}', {"kind": "memory"})])
    assert APM_NOT_CALLED_LIMIT in job.briefing["limitations"]


def test_stall_guard_marks_unresolved_after_three_identical_calls():
    same = [{"hostname": "was-01", "lookback_minutes": 30}, {"lookback_minutes": 30, "hostname": "was-01"},
            {"hostname": "was-01", "lookback_minutes": 30}]            # 키 순서가 달라도 같은 인자
    records = [_rec("apm_events", "{}", p) for p in same] + [_rec("apm_app_health", "{}", {"hostname": "was-01"})]
    job, audit = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"), records)
    b = job.briefing
    assert b["summary"].startswith(UNRESOLVED_PREFIX)
    stall = [ln for ln in b["limitations"] if ln.startswith("조사 정체")]
    assert len(stall) == 1 and "3회" in stall[0] and "apm_events" in stall[0] and "미결" in stall[0]
    done = [r for r in audit if r.get("event") == "done"][0]
    assert done["unresolved"] == ["apm_events x3"]


def test_stall_guard_uses_description_when_params_absent_and_ignores_two_repeats():
    records = [_rec("bash", "", None, description="kubectl get pods")] * 3
    job, _ = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"), records)
    assert job.briefing["summary"].startswith(UNRESOLVED_PREFIX)
    two = [_rec("apm_events", "{}", {"hostname": "was-01"})] * 2
    job2, _ = _run(_dsettings(apm_guidance_enabled=True, apm_mcp_url="http://gw/sse"), two)
    assert not job2.briefing["summary"].startswith(UNRESOLVED_PREFIX)


def test_dispatcher_off_briefing_and_audit_unchanged_even_with_stall():
    """★ apm_guidance_enabled off — 3회 반복·APM 오류가 있어도 브리핑·감사가 종전 호출과 같다."""
    records = [_rec("apm_events", json.dumps(_err("source_unavailable", tool="apm_events")),
                    {"hostname": "was-01"})] * 3
    job, audit = _run(_dsettings(apm_mcp_url="http://gw/sse"), records)
    assert job.briefing == build_briefing(
        answer=_ANSWER, verdict=ImportanceVerdict(level="경고", confidence="none", escalate=False),
        tool_names=["apm_events"] * 3, gate_tier=None, remediation=None)
    done = [r for r in audit if r.get("event") == "done"][0]
    assert set(done) == {"event", "investigation_id", "level", "confidence", "escalate", "signals",
                         "tokens", "cost"}
