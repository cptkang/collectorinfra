"""조사 지침 주입 배선 (plans/50 G5 · SPEC-investigation-guidance · D-194).

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
