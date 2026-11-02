"""plans/198 W5 · I-9 — 자유 조사의 SOP 앵커와 kind별 툴셋 좁히기.

플레이북 스텝 순서가 심층 지시에 정적 앵커로만 들어가는지(결과 미포함), kind별 허용 소스로 그 잡의
도구만 좁혀지는지(허용 밖 제외 · 미등록 kind는 종전), 「추가 조사 필요」 지시 on/off, 두 플래그
off 비트 동일을 가짜 에이전트·가짜 HolmesGPT로 본다. 실 LLM 0.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.tools_utils.tool_executor import ToolExecutor
from sre_agent.application import investigation_guidance as guide_mod
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_guidance import (
    ADDITIONAL_INVESTIGATION_PREFIX,
    APM_FOCUS_NOTE_TEMPLATE,
    PRIOR_FINDINGS_HEADER,
    SOP_ANCHOR_HEADER,
    build_guidance,
    deep_dive_note,
    sop_anchor_note,
    toolset_kind,
    toolset_scope_note,
)
from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult, _with_tool_allowlist
from sre_agent.domain.failure_guards import ExpectedSources
from sre_agent.infrastructure.expected_sources_loader import load_expected_sources
from sre_agent.interface import mcp_service
from sre_agent.toolset_profiles import narrowed_tool_names
from test_plan187_w4_dispatch import (
    PLAYBOOKS,
    FakeCaller,
    FakeDeep,
    _load,
    alarm_of,
    build,
    make_settings,
    run_job,
)

from sre_agent import diagnosis as diag_mod

HOST_CPU = next(p for p in PLAYBOOKS if p.id == "host.cpu")
TOOLS = frozenset({
    "polestar_metric_trend", "polestar_alarm_timeline", "prom_metric_range",
    "apm_app_health", "apm_events", "TodoWrite", "fetch_skill",
})


def _job(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "kind": "alarm",
        "question": None,
        "investigation_id": "inv-198w5",
        "payload": {"event": {"serverName": "web-01", "resourceType": "cpu",
                              "alarmName": "CPU High"}},
        "reference_time": "2026-10-28T10:00:00",
        "lookback_minutes": 120,
    }
    base.update(kw)
    return SimpleNamespace(**base)


APM_PAYLOAD = {"event": {"serverName": "was-01", "resourceType": "apm.Instance",
                         "alarmName": "TX_BAD_RESPONSE"}}


# ── ① SOP 앵커 ──────────────────────────────────────────────────────────────


def test_sop_anchor_lists_steps_in_order_without_results() -> None:
    note = sop_anchor_note(HOST_CPU.steps)
    assert note is not None
    lines = note.split("\n")
    assert lines[0] == SOP_ANCHOR_HEADER
    kept = [s for s in HOST_CPU.steps if s.action != "synthesize"]
    assert len(lines) == 1 + len(kept)
    for i, (line, step) in enumerate(zip(lines[1:], kept, strict=True), start=1):
        assert line.startswith(f"  {i}. {step.narrate.focus}")
        assert f"(`{step.action}`)" in line
        assert line.endswith("(조건부)") == bool(step.when)
    # 정적 지시만 — 종합 단계·조건식·판정 어휘 없음
    assert "synthesize" not in note and "S0r.status" not in note
    for word in ("verdict", "status", "supported", "refuted"):
        assert word not in note


def test_sop_anchor_none_when_only_synthesize() -> None:
    steps = [s for s in HOST_CPU.steps if s.action == "synthesize"]
    assert sop_anchor_note(steps) is None


def test_deep_dive_note_without_sop_steps_is_bit_identical() -> None:
    kw = {"meaning": "m", "impact": "i", "focus": "f"}
    assert deep_dive_note("host.cpu", **kw) == deep_dive_note("host.cpu", **kw, sop_steps=None)
    with_anchor = deep_dive_note("host.cpu", **kw, sop_steps=HOST_CPU.steps)
    assert with_anchor.startswith(deep_dive_note("host.cpu", **kw) + "\n" + SOP_ANCHOR_HEADER)


@pytest.mark.parametrize("on", [False, True])
def test_parallel_deep_job_carries_anchor_only_when_flag_on(on: bool) -> None:
    settings = make_settings(sre_deep_dive_mode="parallel", sop_anchor_enabled=on)
    deep = FakeDeep(settings)
    run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep), alarm_of("latency_sql"))
    guidance = deep.guidance[0] or ""
    assert (SOP_ANCHOR_HEADER in guidance) is on
    # parallel = 독립 의견 — 플레이북 결과는 어느 쪽이든 넘기지 않는다(D-341 ⑮)
    assert PRIOR_FINDINGS_HEADER not in guidance and "dominant=" not in guidance
    if on:
        pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
        assert sop_anchor_note(pb.steps) in guidance


def test_sop_flag_missing_on_settings_stand_in_is_off() -> None:
    stand_in = SimpleNamespace(_settings=SimpleNamespace())
    assert InvestigationDispatcher._sop_anchor(stand_in) is False  # type: ignore[arg-type]


# ── ② kind별 허용 소스 · 툴셋 좁히기 ─────────────────────────────────────────


def test_yaml_allowed_matches_plan_and_keeps_expected_unchanged() -> None:
    spec = load_expected_sources()
    assert spec.allowed_for("host") == ("polestar", "prometheus")
    assert spec.allowed_for("host.cpu") == ("polestar", "prometheus")  # 앞 마디 폴백
    assert spec.allowed_for("was") == ("apm", "polestar")
    assert spec.allowed_for("db") == () and spec.allowed_for(None) == ()
    # W2 미조회 소스 기대값은 그대로
    assert spec.expected_for("host") == ("polestar", "prometheus")
    assert spec.expected_for("was") == ("apm", "polestar")


def test_loader_allowed_optional_and_validated(tmp_path: Path) -> None:
    base = "sources: {polestar: [polestar_]}\nkinds: {host: [polestar]}\n"
    f = tmp_path / "a.yaml"
    f.write_text(base, encoding="utf-8")
    assert load_expected_sources(f).allowed_for("host") == ()
    f.write_text(base + "allowed: {host: [apm]}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="allowed.host"):
        load_expected_sources(f)
    f.write_text(base + "allowed: [host]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_expected_sources(f)


def test_narrowed_tool_names_drops_only_disallowed_sources() -> None:
    spec = load_expected_sources()
    host = narrowed_tool_names(TOOLS, spec.allowed_for("host"), spec.source_of)
    assert host == TOOLS - {"apm_app_health", "apm_events"}
    was = narrowed_tool_names(TOOLS, spec.allowed_for("was"), spec.source_of)
    assert was == TOOLS - {"prom_metric_range"}
    # 소스를 모르는 도구(할 일 목록·스킬 문서)는 남는다
    assert {"TodoWrite", "fetch_skill"} <= host & was


def test_toolset_kind_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    s = SimpleNamespace(cross_source_investigation_enabled=False)
    assert toolset_kind(s, _job()) == "host"
    assert toolset_kind(s, _job(payload=APM_PAYLOAD)) == "was"
    assert toolset_kind(s, _job(kind="diagnosis")) is None
    monkeypatch.setattr(guide_mod, "active_episode", lambda *a: object())
    assert toolset_kind(s, _job()) is None  # 크로스소스 사건은 두 소스를 함께 본다


def test_with_tool_allowlist_clones_executor_and_leaves_original() -> None:
    executor = ToolExecutor([])
    for name in TOOLS:
        executor.tools_by_name[name] = SimpleNamespace(name=name)
    llm = SimpleNamespace(tool_executor=executor)
    keep = frozenset({"polestar_metric_trend", "TodoWrite"})
    clone = _with_tool_allowlist(llm, keep)  # type: ignore[arg-type]
    assert set(clone.tool_executor.tools_by_name) == keep
    assert clone.tool_executor.get_tool_by_name("apm_app_health") is None  # 실행 조회도 막힌다
    assert set(executor.tools_by_name) == TOOLS and llm.tool_executor is executor


class _FakeDrain:
    seen: list[Any] = []

    def __init__(self, llm: Any, **kw: Any) -> None:
        _FakeDrain.seen.append(llm)

    def run(self, messages: list[dict]) -> SimpleNamespace:
        return SimpleNamespace(result="ok", tool_calls=[], total_tokens=0, total_cost=0.0,
                               num_llm_calls=1)


@pytest.mark.parametrize("allow", [None, frozenset({"polestar_metric_trend"})])
def test_ask_passes_narrowed_llm_only_when_allowlist(monkeypatch: pytest.MonkeyPatch,
                                                     allow: frozenset[str] | None) -> None:
    executor = ToolExecutor([])
    for name in TOOLS:
        executor.tools_by_name[name] = SimpleNamespace(name=name)
    agent = DiagnosisAgent(make_settings(), toolsets={})
    agent._llm = SimpleNamespace(tool_executor=executor)  # type: ignore[assignment]
    executors: list[Any] = []
    monkeypatch.setattr(diag_mod, "build_initial_ask_messages",
                        lambda **kw: executors.append(kw["tool_executor"]) or [])
    _FakeDrain.seen = []
    monkeypatch.setattr(diag_mod, "_StreamDrain", _FakeDrain)
    kw = {} if allow is None else {"tool_allowlist": allow}
    assert agent.ask("q", **kw).answer == "ok"
    if allow is None:
        assert _FakeDrain.seen == [agent._llm] and executors == [executor]
    else:
        assert set(_FakeDrain.seen[0].tool_executor.tools_by_name) == allow
        assert executors == [_FakeDrain.seen[0].tool_executor]
        assert set(executor.tools_by_name) == TOOLS


# ── ③ 지침 범위 노트 · 「추가 조사 필요」 ─────────────────────────────────────


def test_build_guidance_without_scope_is_bit_identical() -> None:
    s = make_settings(apm_guidance_enabled=True, prometheus_anchor_guidance_enabled=True)
    job = _job()
    assert build_guidance(s, job, available_tools=TOOLS) == build_guidance(
        s, job, available_tools=TOOLS, toolset_scope=None)


def test_scope_note_before_operator_extra_and_drops_out_of_scope_notes() -> None:
    s = make_settings(apm_guidance_enabled=True, prometheus_anchor_guidance_enabled=True,
                      investigation_guidance_extra="운영자 지침")
    job = _job()
    host_tools = TOOLS - {"apm_app_health", "apm_events"}
    text = build_guidance(s, job, available_tools=host_tools,
                          toolset_scope=("host", ("polestar", "prometheus"))) or ""
    note = toolset_scope_note("host", ("polestar", "prometheus"))
    assert text.endswith(note + "\n\n운영자 지침")
    assert ADDITIONAL_INVESTIGATION_PREFIX in note and "폴스타 · 프로메테우스" in note
    # APM은 범위 밖 — 「연결 실패」 폴백 노트로 오인시키지 않고 싣지 않는다
    assert "APM 게이트웨이 도구가 로드되지 않았다" not in text
    assert APM_FOCUS_NOTE_TEMPLATE.split("{")[0] not in text
    was = build_guidance(s, _job(payload=APM_PAYLOAD),
                         available_tools=TOOLS - {"prom_metric_range"},
                         toolset_scope=("was", ("apm", "polestar"))) or ""
    assert "Prometheus 호스트 지표 도구" not in was  # 프로메테우스는 was 범위 밖
    assert "Prometheus 호스트 지표 도구" in text
    assert toolset_scope_note("was", ("apm", "polestar")) in was


# ── 배선 · _default_diagnose_fn ─────────────────────────────────────────────


class _Agent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def mcp_unavailable(self) -> dict[str, str]:
        return {}

    def tool_names(self) -> frozenset[str]:
        return TOOLS

    def ask(self, question: str, system_prompt_additions: str | None = None,
            **kw: Any) -> DiagnosisResult:
        self.calls.append({"guidance": system_prompt_additions, **kw})
        return DiagnosisResult(answer="ok")


def _run(settings: Any, job: SimpleNamespace) -> dict[str, Any]:
    agent = _Agent()
    mcp_service._default_diagnose_fn(settings, agent_getter=lambda: agent)(job)
    return agent.calls[0]


def test_diagnose_flag_off_is_bit_identical() -> None:
    s = make_settings()
    call = _run(s, _job())
    assert "tool_allowlist" not in call
    assert call["guidance"] == build_guidance(s, _job(), remote=True, available_tools=TOOLS)
    assert ADDITIONAL_INVESTIGATION_PREFIX not in (call["guidance"] or "")


def test_diagnose_flag_on_narrows_host_and_was_jobs() -> None:
    s = make_settings(kind_toolset_narrowing_enabled=True)
    host = _run(s, _job())
    assert host["tool_allowlist"] == TOOLS - {"apm_app_health", "apm_events"}
    assert toolset_scope_note("host", ("polestar", "prometheus")) in host["guidance"]
    was = _run(s, _job(payload=APM_PAYLOAD))
    assert was["tool_allowlist"] == TOOLS - {"prom_metric_range"}
    assert ADDITIONAL_INVESTIGATION_PREFIX in was["guidance"]


def test_diagnose_flag_on_unregistered_kind_or_load_failure_keeps_toolset(
        monkeypatch: pytest.MonkeyPatch) -> None:
    s = make_settings(kind_toolset_narrowing_enabled=True)
    off = _run(make_settings(), _job(kind="diagnosis"))
    diag_job = _run(s, _job(kind="diagnosis"))
    assert "tool_allowlist" not in diag_job and diag_job["guidance"] == off["guidance"]
    monkeypatch.setattr(mcp_service, "load_expected_sources",
                        lambda: ExpectedSources(sources={"apm": ("apm_",)}, kinds={}))
    no_key = _run(s, _job())
    assert "tool_allowlist" not in no_key
    assert no_key["guidance"] == _run(make_settings(), _job())["guidance"]

    def _boom() -> ExpectedSources:
        raise ValueError("깨진 YAML")

    monkeypatch.setattr(mcp_service, "load_expected_sources", _boom)
    assert "tool_allowlist" not in _run(s, _job())
