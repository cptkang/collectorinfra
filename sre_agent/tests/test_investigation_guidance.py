"""조사 지침 주입 배선 (plans/50 G5 · SPEC-investigation-guidance · D-197).

핵심 고정: `_default_diagnose_fn`이 `ask()`에 `system_prompt_additions`를 **실제로 넘긴다**
(2026-09-02 실측 0건이던 결함). 실 LLM은 호출하지 않는다 — DiagnosisAgent를 대역으로 바꾼다.
"""

from types import SimpleNamespace

from sre_agent.application.investigation_guidance import (
    ANCHORED_TOOLS,
    build_guidance,
    incident_scope_note,
)
from sre_agent.diagnosis import _with_load_guard_note
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from sre_agent.toolset_profiles import LOAD_GUARD_NOTE, REMOTE_VM_SHELL_NOTE


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "test/model")
    kw.setdefault("gemini_api_key", None)
    return AgentSettings(**kw)


def _job(**kw) -> SimpleNamespace:
    base = {"kind": "diagnosis", "question": "web-01 원인", "payload": None,
            "reference_time": None, "lookback_minutes": None}
    base.update(kw)
    return SimpleNamespace(**base)


# ── 조립 ────────────────────────────────────────────────────────


def test_remote_note_is_first_by_default():
    g = build_guidance(_settings(), _job())
    assert g is not None and g.startswith(REMOTE_VM_SHELL_NOTE)


def test_nothing_to_inject_returns_none():
    assert build_guidance(_settings(), _job(), remote=False) is None


def test_incident_scope_note_carries_anchor_args():
    note = incident_scope_note("2026-09-01T14:00:00", 180)
    assert note is not None
    assert 'reference_time="2026-09-01T14:00:00"' in note
    assert "lookback_minutes=180" in note
    for tool in ANCHORED_TOOLS:
        assert tool in note
    assert incident_scope_note(None, 60) is None


def test_order_remote_then_scope_then_extra():
    g = build_guidance(
        _settings(investigation_guidance_extra="플레이북: WAS 힙 먼저"),
        _job(reference_time="2026-09-01T14:00:00", lookback_minutes=120),
    )
    i_remote = g.index(REMOTE_VM_SHELL_NOTE)
    i_scope = g.index("사건 기준시각")
    i_extra = g.index("플레이북: WAS 힙 먼저")
    assert i_remote < i_scope < i_extra


def test_blank_extra_is_ignored():
    g = build_guidance(_settings(investigation_guidance_extra="   "), _job())
    assert g == REMOTE_VM_SHELL_NOTE


def test_load_guard_appended_once_by_ask_path():
    g = build_guidance(_settings(), _job(reference_time="2026-09-01T14:00:00", lookback_minutes=60))
    final = _with_load_guard_note(g)
    assert final.count(LOAD_GUARD_NOTE) == 1
    assert final.startswith(REMOTE_VM_SHELL_NOTE)


# ── 프로덕션 배선 ───────────────────────────────────────────────


def test_default_diagnose_fn_passes_additions(monkeypatch):
    """★ 종전 0건이던 `system_prompt_additions` 전달을 고정한다."""
    captured: dict = {}

    class _FakeAgent:
        def __init__(self, settings, toolsets=None, mcp_servers=None):
            captured["toolsets"] = toolsets

        def ask(self, question, system_prompt_additions=None):
            captured["question"] = question
            captured["additions"] = system_prompt_additions
            return SimpleNamespace(answer="ok", tool_outputs=[], tool_calls=[], total_tokens=0, total_cost=0.0)

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", _FakeAgent)
    fn = mcp_service._default_diagnose_fn(_settings(investigation_guidance_extra="추가 지침"))
    fn(_job(reference_time="2026-09-01T14:00:00", lookback_minutes=120))

    assert captured["additions"] is not None
    assert REMOTE_VM_SHELL_NOTE in captured["additions"]
    assert 'reference_time="2026-09-01T14:00:00"' in captured["additions"]
    assert "추가 지침" in captured["additions"]


def test_push_question_includes_alarm_time():
    job = _job(kind="alarm", question=None,
               payload={"event": {"serverName": "web-01", "hostname": "web-01.local"}},
               reference_time="2026-09-01T14:00:00")
    q = mcp_service._job_to_question(job)
    assert "web-01" in q and "발생 시각 2026-09-01T14:00:00" in q


def test_push_question_without_time_unchanged():
    job = _job(kind="alarm", question=None, payload={"event": {"serverName": "web-01"}})
    q = mcp_service._job_to_question(job)
    assert "발생 시각" not in q and q.startswith("web-01 장애 원인을 조사하고")


def test_pull_question_is_verbatim():
    assert mcp_service._job_to_question(_job(question="어제 14시 web-01 원인")) == "어제 14시 web-01 원인"


# ── 상관 결과 지침 (plans/50 G4) ─────────────────────────────────


def test_correlation_note_cites_computed_values():
    from sre_agent.application.investigation_guidance import correlation_note

    corr = {
        "leading_signal": "disk_io",
        "metric_findings": {
            "disk_io": {"is_anomalous": True, "kind": "sustained", "onset_offset_min": -15,
                        "peak_value": 97.0, "peak_time": "2026-09-01T13:50:00", "z_score": 12.5, "lead_lag_min": -5},
            "cpu": {"is_anomalous": False},
        },
        "alarm_summary": {"count": 2, "first_offset_min": -10, "resolved": 1},
        "timeline": [{"t_offset_min": -15, "kind": "metric", "detail": "disk_io sustained 시작"}],
        "notes": ["지표 정밀도 60분 단위"],
    }
    note = correlation_note(corr)
    assert "선행 신호: disk_io" in note
    assert "disk_io: sustained · onset T-15m" in note and "첫 알람 대비 -5분" in note
    assert "cpu" not in note.split("구간 알람")[0].split("선행 신호")[1]  # 비이상 지표는 나열하지 않는다
    assert "구간 알람: 2건 (첫 알람 T-10m, 해소 1건)" in note
    assert "T-15m metric disk_io sustained 시작" in note
    assert "한계: 지표 정밀도 60분 단위" in note
    assert correlation_note(None) is None


def test_build_guidance_includes_correlation_after_scope():
    g = build_guidance(_settings(), _job(reference_time="2026-09-01T14:00:00", lookback_minutes=60,
                                         correlation={"leading_signal": "memory", "metric_findings": {},
                                                      "alarm_summary": {}, "timeline": [], "notes": []}))
    assert g.index("사건 기준시각") < g.index("결정적 상관 결과")
    assert "선행 신호: memory" in g


# ── 플레이북 편입 (plans/91 1-5 · plans/51 §6 · D-035 결정적 문구) ─────────────────────────
import pytest  # noqa: E402

from sre_agent.application.investigation_guidance import (  # noqa: E402
    PLAYBOOK_NOTES,
    alarm_kind_from_job,
    classify_alarm_kind,
    playbook_note,
)


@pytest.mark.parametrize("resource_type,alarm_name,expected", [
    ("server.Cpus", "CPU Utilization Critical", "cpu"),
    ("server.Memory", "Memory Utilization", "memory"),
    ("server.FileSystem", "FS Util 90%", "disk"),
    ("server.Disk", "Disk IO", "disk"),
    ("server.NetworkInterface", "traffic burst", "network"),
    ("server.ProcessMonitor", "ntpd down", "process"),
    ("server.LogMonitor", "log pattern", "log"),
    ("server.Server", "Host unreachable", None),
    ("", "", None),
])
def test_classify_alarm_kind_matches_gate_vocabulary(resource_type, alarm_name, expected):
    assert classify_alarm_kind(resource_type, alarm_name) == expected


def test_alarm_kind_from_job_reads_payload_event_only():
    assert alarm_kind_from_job(_job(payload=None)) is None
    assert alarm_kind_from_job(_job(payload={"event": {"resourceType": "server.Memory", "alarmName": "x"}})) == "memory"
    assert alarm_kind_from_job(_job(payload={"event": {"alarmName": "Disk Full"}})) == "disk"


def test_playbook_note_has_six_kinds_and_is_deterministic():
    assert set(PLAYBOOK_NOTES) == {"cpu", "memory", "disk", "network", "process", "log"}
    for kind, text in PLAYBOOK_NOTES.items():
        assert text.startswith("장애 유형 플레이북") and "증거" in text and "기법" in text and "서술" in text
    assert playbook_note("memory") == PLAYBOOK_NOTES["memory"] and playbook_note(None) is None and playbook_note("weird") is None


def test_guidance_appends_playbook_for_matched_kind_after_correlation_before_extra():
    g = build_guidance(
        _settings(investigation_guidance_extra="운영자 지침"),
        _job(reference_time="2026-09-01T14:00:00", lookback_minutes=60,
             correlation={"leading_signal": "memory", "metric_findings": {}, "alarm_summary": {}, "timeline": [], "notes": []},
             payload={"event": {"resourceType": "server.Memory", "alarmName": "Memory Utilization"}}),
    )
    i_corr = g.index("결정적 상관 결과")
    i_pb = g.index(PLAYBOOK_NOTES["memory"])
    i_extra = g.index("운영자 지침")
    assert i_corr < i_pb < i_extra
    assert "OOM" in PLAYBOOK_NOTES["memory"] and "누수" in PLAYBOOK_NOTES["memory"]


def test_guidance_unmatched_kind_is_string_identical():
    base = build_guidance(_settings(), _job(payload={"event": {"resourceType": "server.Server", "alarmName": "Host"}}))
    assert base == build_guidance(_settings(), _job())   # 미매칭 kind → 종전 지침과 문자열 동일


# ── OpenMetrics 서술 노트 (plans/92 O3 · R-12 · F-6) ─────────────────────────
import itertools  # noqa: E402
import re  # noqa: E402

from sre_agent.application.investigation_guidance import (  # noqa: E402
    OPENMETRICS_NOTE,
    correlation_note,
)

_CORR = {"leading_signal": "memory", "metric_findings": {}, "alarm_summary": {},
         "timeline": [], "notes": []}
_MEMORY_EVENT = {"event": {"resourceType": "server.Memory", "alarmName": "Memory Utilization"}}


def _combo_job(scope: bool, corr: bool, playbook: bool) -> SimpleNamespace:
    kw: dict = {}
    if scope:
        kw.update(reference_time="2026-09-01T14:00:00", lookback_minutes=90)
    if corr:
        kw["correlation"] = _CORR
    if playbook:
        kw["payload"] = _MEMORY_EVENT
    return _job(**kw)


def _pre_o3_guidance(job, extra: str | None, *, remote: bool) -> str | None:
    """노트 도입 전(O3 이전) 조립 규칙의 재현 — 공개 조각 함수만으로 만든 대조 기준."""
    parts = [REMOTE_VM_SHELL_NOTE] if remote else []
    for piece in (
        incident_scope_note(job.reference_time, job.lookback_minutes),
        correlation_note(getattr(job, "correlation", None)),
        playbook_note(alarm_kind_from_job(job)),
        (extra or "").strip() or None,
    ):
        if piece:
            parts.append(piece)
    return "\n\n".join(parts) or None


_COMBOS = list(itertools.product((True, False), repeat=5))


def _om_settings(extra: str | None, enabled: bool | None) -> AgentSettings:
    """enabled=None이면 필드를 넘기지 않는다(클래스 기본값 경로)."""
    if enabled is None:
        return _settings(investigation_guidance_extra=extra)
    return _settings(investigation_guidance_extra=extra, openmetrics_guidance_enabled=enabled)


@pytest.mark.parametrize("remote,scope,corr,playbook,with_extra", _COMBOS)
def test_openmetrics_off_is_byte_identical_to_pre_o3(remote, scope, corr, playbook, with_extra):
    """★ 기본 off면 조립 문자열이 종전과 바이트 동일하다 — 조사 LLM 프롬프트 접두·KV 캐시 불변."""
    extra = "운영자 지침" if with_extra else None
    job = _combo_job(scope, corr, playbook)
    expected = _pre_o3_guidance(job, extra, remote=remote)
    default = build_guidance(_om_settings(extra, None), job, remote=remote)
    explicit_off = build_guidance(_om_settings(extra, False), job, remote=remote)
    assert default == expected
    assert explicit_off == expected
    assert default is None or OPENMETRICS_NOTE not in default


@pytest.mark.parametrize("remote,scope,corr,playbook,with_extra", _COMBOS)
def test_openmetrics_on_inserts_note_after_playbook_before_extra(
    remote, scope, corr, playbook, with_extra
):
    """켜면 노트가 정확히 한 번, ④ 플레이북 뒤 · ⑥ 자유 지침 앞에 들어가고 나머지는 그대로다."""
    extra = "운영자 지침" if with_extra else None
    job = _combo_job(scope, corr, playbook)
    g = build_guidance(_om_settings(extra, True), job, remote=remote)
    assert g is not None and g.count(OPENMETRICS_NOTE) == 1
    before = _pre_o3_guidance(job, None, remote=remote)
    head = f"{before}\n\n{OPENMETRICS_NOTE}" if before else OPENMETRICS_NOTE
    assert g == (f"{head}\n\n{extra}" if extra else head)
    if playbook:
        assert g.index(PLAYBOOK_NOTES["memory"]) < g.index(OPENMETRICS_NOTE)
    if extra:
        assert g.index(OPENMETRICS_NOTE) < g.index(extra)


def test_openmetrics_note_states_r12_rules():
    """노트는 결정적 문구다 — 현재 상태 서술·hint 추종·catalog 선행·source_kind·신원 불일치."""
    for token in ("observed_at", "사건 구간 증거로 인용하지 말고", "현재 상태", "counter는 누적값",
                  "types", "PROMETHEUS_URL", "hint", "om_metric_catalog", "추측하지 마라",
                  "source_kind", "openmetrics", "fallback_reason", "target_identity", "mismatch"):
        assert token in OPENMETRICS_NOTE, token


def test_om_tools_are_not_anchored():
    """★ R-12 — `om_*`는 현재값 전용이라 앵커 도구가 아니다(사건 구간 노트가 가리키지 않는다)."""
    assert ANCHORED_TOOLS == (
        "polestar_metric_trend", "polestar_alarm_history", "polestar_incident_alarms",
        "prom_metric_range",
    )
    assert not any(t.startswith("om_") for t in ANCHORED_TOOLS)
    note = incident_scope_note("2026-09-01T14:00:00", 60)
    assert not re.search(r"\bom_", note)   # 단어 경계 — `prom_metric_range`의 부분 문자열 제외


def test_incident_scope_note_unchanged_by_openmetrics():
    """사건 구간 노트 문자열은 설정과 무관하게 종전 그대로다(스냅샷)."""
    expected = (
        "사건 기준시각: 2026-09-01T14:00:00 · 조사 구간: 기준시각 이전 60분.\n"
        "증거는 반드시 이 구간에서 가져온다 — polestar_metric_trend · polestar_alarm_history · "
        "polestar_incident_alarms · prom_metric_range 를 호출할 때 "
        'reference_time="2026-09-01T14:00:00", lookback_minutes=60 인자를 함께 넘길 것. '
        "인자 없이 호출하면 현재 시각 기준 최신 데이터가 나오며 그것은 이 사건의 증거가 아니다.\n"
        "구간 안의 전 알람은 polestar_incident_alarms 로 먼저 확보하라(선행 알람 탐색)."
    )
    assert incident_scope_note("2026-09-01T14:00:00", 60) == expected
    g = build_guidance(_settings(openmetrics_guidance_enabled=True),
                       _job(reference_time="2026-09-01T14:00:00", lookback_minutes=60))
    assert expected in g


def test_default_diagnose_fn_carries_openmetrics_note_when_enabled(monkeypatch):
    """프로덕션 배선(`_default_diagnose_fn` → `ask`)에서도 설정대로 노트가 실린다/안 실린다."""
    captured: list = []

    class _FakeAgent:
        def __init__(self, settings, toolsets=None, mcp_servers=None):
            pass

        def ask(self, question, system_prompt_additions=None):
            captured.append(system_prompt_additions)
            return SimpleNamespace(answer="ok", tool_outputs=[], tool_calls=[],
                                   total_tokens=0, total_cost=0.0)

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", _FakeAgent)
    mcp_service._default_diagnose_fn(_settings(openmetrics_guidance_enabled=True))(_job())
    mcp_service._default_diagnose_fn(_settings())(_job())
    assert OPENMETRICS_NOTE in captured[0]
    assert OPENMETRICS_NOTE not in captured[1]
