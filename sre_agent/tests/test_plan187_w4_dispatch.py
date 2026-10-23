"""plans/187 W4 — dispatcher 배선 · 심층 모드 3종 · 판정 합집합 · 플레이북 브리핑 · 진척.

W0 골든 픽스처 녹화(W2 `FakeCaller`)로 플레이북 도구를 흉내 내고, HolmesGPT는 가짜 조사 함수,
소견기 LLM은 가짜 콜러블이다. 실 LLM·MCP·DB 호출 0.
"""

from __future__ import annotations

import ast
import copy
import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sre_agent.application import investigation_dispatcher as disp_mod
from sre_agent.application.briefing_builder import BRIEFING_ELEMENTS, build_briefing
from sre_agent.application.investigation_dispatcher import (
    DEEP_DIVE_LABEL,
    DEEP_SKIP_MODE_OFF,
    NO_PLAYBOOK_LIMIT,
    InvestigationDispatcher,
    PlaybookWiring,
)
from sre_agent.application.investigation_guidance import (
    DEEP_DIVE_HEADER,
    PRIOR_FINDINGS_HEADER,
    build_guidance,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.application.playbook_runner import (
    GATEWAY_EVENT_SIGNAL_KINDS,
    alarm_facts_from_payload,
    normalize_event_type,
)
from sre_agent.application.playbook_steps import NO_WAS_VERDICT, STEP_LIBRARY, StepResult
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.playbook import parse_playbook, select_playbook
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from test_plan187_w2_runner import PLAYBOOKS, FakeCaller, _load

OOM_RAW = "kernel: Out of memory: Killed process 12345 (java)"
OPINION = "수집된 표의 값을 확인했다."
REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_FIELDS = REPO_ROOT / "apm_gateway" / "apm_gateway" / "adapters" / "jennifer" / "fields.py"
NEW_SETTINGS = ("sre_deep_dive_mode", "sre_playbook_dir", "sre_playbook_budget_s")


def make_settings(**overrides: Any) -> AgentSettings:
    defaults: dict[str, Any] = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "gemini_api_key": "k",
        "service_bearer_token": None,
        "investigation_timeout_seconds": 300,
        "investigation_dedup_ttl_seconds": None,
        "investigation_max_concurrent": 2,
        "investigation_hourly_budget": None,
        "severity_judge_enabled": False,
        "cross_source_investigation_enabled": False,
        "apm_signatures_enabled": False,
        "apm_guidance_enabled": False,
        "remediation_recommender_enabled": False,
        "sre_playbook_enabled": True,
        "sre_deep_dive_mode": "off",
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


class Guarded:
    """설정 대역 — 금지 필드를 읽으면 실패한다(off면 신규 설정을 읽지 않는다 단언)."""

    def __init__(self, settings: AgentSettings, forbidden: tuple[str, ...]) -> None:
        self._settings = settings
        self._forbidden = forbidden

    def __getattr__(self, name: str) -> Any:
        if name in self._forbidden:
            raise AssertionError(f"off 경로가 신규 설정 {name}을 읽었다")
        return getattr(self._settings, name)


def alarm_of(name: str, *, polestar: bool = True) -> dict:
    """픽스처 알람 — 폴스타 소스는 `meta.episode.infra_db_ids`로 결정적으로 준다."""
    fx = _load(name)
    alarm = copy.deepcopy(fx["alarm"])
    if polestar and not fx["expected"].get("polestar_unresolved"):
        alarm["meta"]["episode"] = {"episode_id": "ep", "infra_db_ids": ["polestar"], "members": []}
    return alarm


def make_job(payload: dict, iid: str = "inv-1") -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=iid, kind="alarm", status="accepted", created_at=now, updated_at=now,
        payload=payload,
    )


class FakeLLM:
    def __init__(self, text: str = OPINION) -> None:
        self.text = text
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self, messages: list[dict[str, str]]) -> str:
        with self._lock:
            self.calls += 1
        return self.text


class FakeDeep:
    """가짜 HolmesGPT — 받은 잡과 그 잡의 조사 지침을 기록한다. `gate`가 있으면 풀릴 때까지 대기."""

    def __init__(self, settings: AgentSettings, *, answer: str = "심층 서술 ← apm_resource_pool",
                 outputs: list[ToolCallRecord] | None = None, gate: threading.Event | None = None,
                 error: Exception | None = None) -> None:
        self.settings = settings
        self.answer = answer
        self.outputs = outputs if outputs is not None else [
            ToolCallRecord("apm_resource_pool", "풀 조회", "success", '{"ok": true}')
        ]
        self.gate = gate
        self.error = error
        self.jobs: list[Any] = []
        self.guidance: list[str | None] = []
        self.started = threading.Event()

    def __call__(self, job: Any) -> DiagnosisResult:
        self.jobs.append(job)
        self.guidance.append(build_guidance(self.settings, job, remote=True))
        self.started.set()
        if self.gate is not None:
            assert self.gate.wait(10)
        if self.error is not None:
            raise self.error
        return DiagnosisResult(
            answer=self.answer,
            tool_calls=[f"{o.tool_name} {o.description}" for o in self.outputs],
            tool_outputs=list(self.outputs),
            total_tokens=11,
            total_cost=0.0,
        )


def build(settings: Any, caller: Any, *, deep: Any = None, llm: Any = None,
          playbooks: Any = PLAYBOOKS, audit: list | None = None,
          wiring: bool = True) -> InvestigationDispatcher:
    d = InvestigationDispatcher(
        settings,
        diagnose_fn=deep or FakeDeep(settings if isinstance(settings, AgentSettings) else
                                     settings._settings),
        briefing_fn=build_briefing,
        playbook=PlaybookWiring(playbooks=tuple(playbooks), polestar=caller, apm=caller, llm=llm)
        if wiring else None,
    )
    if audit is not None:
        d._audit = audit.append  # type: ignore[method-assign]
    return d


def run_job(d: InvestigationDispatcher, payload: dict, iid: str = "inv-1") -> InvestigationJob:
    job = make_job(payload, iid)
    d(job)
    d.wait_workers(30)
    return job


# ── off = 비트 동일 · 신규 설정 미열람 ──────────────────────────────────────


def _comparable(job: InvestigationJob) -> dict:
    s = job.summary()
    for k in ("investigation_id", "created_at", "updated_at", "elapsed_s"):
        s.pop(k, None)
    return s


def test_off_is_bit_identical_and_new_settings_never_read() -> None:
    settings = make_settings(sre_playbook_enabled=False)
    guarded = Guarded(settings, NEW_SETTINGS)
    caller = FakeCaller(_load("latency_sql"))
    base_audit: list = []
    off_audit: list = []
    base = build(settings, caller, wiring=False, audit=base_audit)
    off = build(guarded, caller, audit=off_audit)  # 배선은 있으나 플래그 off
    j1 = run_job(base, alarm_of("latency_sql"))
    j2 = run_job(off, alarm_of("latency_sql"))
    assert _comparable(j1) == _comparable(j2)
    assert "progress" not in j2.summary() and "partial_briefing" not in j2.summary()
    assert "playbook" not in (j2.briefing or {})
    assert caller.calls == []  # 플레이북 도구 호출 0

    def keys(audit: list) -> list:
        return [sorted(k for k in r if k not in ("ts",)) for r in audit]

    assert keys(base_audit) == keys(off_audit)


def test_no_wiring_never_reads_any_playbook_setting() -> None:
    guarded = Guarded(make_settings(), ("sre_playbook_enabled",) + NEW_SETTINGS)
    d = build(guarded, FakeCaller(_load("latency_sql")), wiring=False)
    job = run_job(d, alarm_of("latency_sql"))
    assert job.status == "done" and "playbook" not in job.briefing


def test_mcp_service_wiring_off_reads_only_flag() -> None:
    guarded = Guarded(make_settings(sre_playbook_enabled=False), NEW_SETTINGS)
    assert mcp_service._default_playbook_wiring(guarded, lambda: None) is None


# ── 플레이북 브리핑 · 수용 기준 1 ───────────────────────────────────────────


def test_acceptance1_latency_sql_sections_in_order_with_table_source_opinion_tools() -> None:
    llm = FakeLLM()
    settings = make_settings(sre_deep_dive_mode="off")
    deep = FakeDeep(settings)
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep, llm=llm),
                  alarm_of("latency_sql"))
    assert job.status == "done"
    b = job.briefing
    pb = b["playbook"]
    assert pb["id"] == "was.latency"
    steps = [s["step"] for s in pb["sections"]]
    assert steps == [f"S{i}" for i in range(10)]
    for s in pb["sections"][:8]:  # S0~S7 — 수집 스텝
        assert s["status"] == "ok", s
        assert s["table"] and s["table"]["columns"], s["step"]
        assert s["tools"], s["step"]
        # 소견은 붙거나(현재값 배지 접두 가능), 호출 상한 밖이면 사유가 남는다(침묵 생략 금지)
        assert OPINION in s["opinion"] or "호출 상한" in (s["opinion_dropped_reason"] or ""), (
            s["step"], s["opinion_dropped_reason"])
        assert "verdict_source" in s
        if s["verdict"] is not None:
            assert s["verdict_source"], s["step"]
    # 단계 소견 = ok인 수집 스텝 전부(S0~S7 · 호출 상한 없음 — plans/187 v2.8) + 종합 서술 1(S9)
    assert sum(1 for s in pb["sections"][:9] if OPINION in s["opinion"]) == 8
    assert pb["sections"][8]["status"] == "skipped" and pb["sections"][8]["reason"]
    s9 = pb["sections"][9]
    assert s9["action"] == "synthesize" and s9["status"] == "ok" and s9["table"]["rows"]
    assert pb["synthesis"]["hypotheses"] and pb["synthesis"]["narrative"] == OPINION
    # 6요소 = 플레이북 종합
    for key in BRIEFING_ELEMENTS:
        assert key in b
    top = pb["synthesis"]["hypotheses"][0]
    assert top["title"] in b["summary"] and top["status_label"] in b["summary"]
    assert b["cause"].startswith("1위 ") and top["status_label"] in b["cause"]
    assert b["bottleneck"].startswith("지배 계층 db")
    assert b["timeline"]
    assert b["recommendation"]["items"]
    assert b["root_cause_hypotheses"][0]["confidence"] == top["status_label"]
    # 심층 생략도 사유와 함께(침묵 금지)
    assert b["deep_dive"] == {"status": "skipped", "mode": "off", "reason": DEEP_SKIP_MODE_OFF,
                              "label": DEEP_DIVE_LABEL}
    assert any(DEEP_SKIP_MODE_OFF in line for line in b["limitations"])
    assert b["engine_agreement"]["result"] == "not_comparable"
    assert deep.jobs == []
    # 플레이북 LLM 호출 = 소견 대상 단계 전부 + 종합 1(호출 상한 없음 — plans/187 v2.8)
    assert llm.calls == 9 and pb["timings"]["llm_calls"] == llm.calls  # 단계 8 + 종합 1
    # 사전수집 생략 — 상관은 S4·S5로 다시 계산한 값
    assert job.correlation is not None and job.correlation == job.summary()["correlation"]
    json.dumps(job.summary(), ensure_ascii=False)


def test_playbook_block_matches_followup_contract() -> None:
    """본체 후속 조사(W10)가 읽는 계약 — 평탄 readings · scope 대상·사건창."""
    job = run_job(build(make_settings(), FakeCaller(_load("latency_sql"))), alarm_of("latency_sql"))
    pb = job.briefing["playbook"]
    assert pb["readings"]["dominant"] == "sql"
    assert set(pb["readings_detail"]) == {"values", "evidence", "facts", "fact_evidence", "notes"}
    scope = pb["scope"]
    assert scope["host"] and scope["polestar_source"] == "polestar"
    window = scope["incident_window"]
    assert window == {"start": "2026-10-19T09:30:00", "end": "2026-10-19T10:10:00", "minutes": 40}
    # W8 — 런북 블록 · 알려진 오류(기본 배선은 KE 없음 → 빈 목록)
    assert pb["runbook"]["meaning"] and pb["runbook"]["owner"]
    assert isinstance(pb["runbook"]["stale"], bool)
    assert pb["known_errors"] == []


def test_partial_step_failure_still_synthesizes(monkeypatch: pytest.MonkeyPatch) -> None:
    """수용 기준 4 — 스텝 일부 실패(예외 · 녹화 결손)에도 종합이 나온다."""

    def boom(ctx):  # noqa: ANN001
        raise RuntimeError("변경 이력 실패")

    monkeypatch.setitem(STEP_LIBRARY, "change.recent", boom)
    job = run_job(build(make_settings(), FakeCaller(_load("partial_missing")), llm=FakeLLM()),
                  alarm_of("partial_missing"))
    pb = job.briefing["playbook"]
    status = {s["step"]: s["status"] for s in pb["sections"]}
    assert status["S7"] == "missing" and status["S3"] == "missing" and status["S9"] == "ok"
    assert pb["synthesis"] is not None and "hypotheses" in pb["synthesis"]
    limits = "\n".join(job.briefing["limitations"])
    assert "S7" in limits and "변경 이력 실패" in limits and "S3" in limits


# ── 심층 모드 3종 ───────────────────────────────────────────────────────────


def test_conditional_triggered_passes_prior_findings_summary() -> None:
    settings = make_settings(sre_deep_dive_mode="conditional")
    deep = FakeDeep(settings)
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep),
                  alarm_of("latency_sql"))  # 1순위 판단 보류 → 트리거
    assert job.briefing["playbook"]["synthesis"]["top_inconclusive"] is True
    assert len(deep.jobs) == 1
    guidance = deep.guidance[0] or ""
    assert DEEP_DIVE_HEADER.format(playbook_id="was.latency") in guidance
    assert PRIOR_FINDINGS_HEADER in guidance and "dominant=sql" in guidance
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    assert pb.runbook.meaning in guidance and pb.deep_dive.focus in guidance
    assert deep.jobs[0].correlation is None
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "done" and dd["mode"] == "conditional" and dd["label"] == DEEP_DIVE_LABEL
    assert "severity" not in dd and dd["summary"] and dd["tool_calls"]
    assert job.tokens == 11


def test_conditional_not_triggered_skips_with_reason() -> None:
    settings = make_settings(sre_deep_dive_mode="conditional")
    deep = FakeDeep(settings)
    job = run_job(build(settings, FakeCaller(_load("latency_external")), deep=deep),
                  alarm_of("latency_external"))
    assert deep.jobs == []
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "skipped" and "트리거 미충족" in dd["reason"]


def test_parallel_both_engines_partial_first_then_deep_attached() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel")
    gate = threading.Event()
    deep = FakeDeep(settings, gate=gate)
    d = build(settings, FakeCaller(_load("latency_sql")), deep=deep, llm=FakeLLM())
    job = make_job(alarm_of("latency_sql"))
    d(job)
    assert deep.started.wait(10)
    # HolmesGPT 입력에 플레이북 결과 없음 — 정적 지시만(독립성)
    guidance = deep.guidance[0] or ""
    assert DEEP_DIVE_HEADER.format(playbook_id="was.latency") in guidance
    assert PRIOR_FINDINGS_HEADER not in guidance and "dominant=" not in guidance
    assert deep.jobs[0].correlation is None
    assert getattr(deep.jobs[0], "deep_dive_guidance", None) is not None
    # 플레이북이 먼저 끝나면 그 브리핑이 부분 결과로 보인다
    deadline = time.time() + 10
    while time.time() < deadline:
        partial = job.summary().get("partial_briefing") or {}
        if "summary" in partial:
            break
        time.sleep(0.01)
    partial = job.summary()["partial_briefing"]
    assert job.status == "running" and partial["partial"] is True
    assert partial["deep_dive"]["status"] == "running"
    assert partial["engine_agreement"]["result"] == "not_comparable"
    assert partial["playbook"]["synthesis"]["hypotheses"]
    gate.set()
    d.wait_workers(30)
    assert job.status == "done" and "partial_briefing" not in job.summary()
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "done" and dd["mode"] == "parallel"
    ea = job.briefing["engine_agreement"]
    assert ea["result"] == "same_layer" and ea["holmesgpt"]["basis"] == ["tool:apm_resource_pool"]


def test_parallel_disagreement_shows_both_and_badge() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel")
    deep = FakeDeep(settings, answer="GC 과다 ← apm_runtime_health", outputs=[
        ToolCallRecord("apm_runtime_health", "런타임", "success", '{"ok": true}')
    ])
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep),
                  alarm_of("latency_sql"))
    b = job.briefing
    assert b["engine_agreement"]["result"] == "different_layer"
    assert b["engine_agreement"]["badge"] == "엔진 간 불일치"
    assert any("엔진 간 불일치" in line for line in b["limitations"])
    assert b["deep_dive"]["status"] == "done" and b["bottleneck"].startswith("지배 계층 db")


def test_parallel_degrades_to_conditional_on_hourly_budget() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel", investigation_hourly_budget=2)
    deep = FakeDeep(settings)
    d = build(settings, FakeCaller(_load("latency_external")), deep=deep)
    d._deep_window.extend([d._clock(), d._clock()])  # 최근 1시간 심층 2건
    job = run_job(d, alarm_of("latency_external"))
    assert deep.jobs == []  # 강등 → conditional 트리거 미충족 → 생략
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "skipped" and dd["mode"] == "conditional"
    assert "심층 생략 — 예산" in dd["reason"] and "parallel → conditional" in dd["reason"]
    assert any("심층 생략 — 예산" in line for line in job.briefing["limitations"])


def test_degraded_but_triggered_runs_deep_and_states_degrade() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel", investigation_hourly_budget=2)
    deep = FakeDeep(settings)
    d = build(settings, FakeCaller(_load("latency_sql")), deep=deep)
    d._deep_window.extend([d._clock(), d._clock()])
    job = run_job(d, alarm_of("latency_sql"))
    assert len(deep.jobs) == 1 and PRIOR_FINDINGS_HEADER in (deep.guidance[0] or "")
    assert job.briefing["deep_dive"]["mode"] == "conditional"
    assert any("심층 생략 — 예산" in line for line in job.briefing["limitations"])


def test_yaml_mode_overrides_global_setting() -> None:
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    raw = pb.model_dump(mode="json")
    raw["deep_dive"]["mode"] = "off"
    override = parse_playbook(raw)
    settings = make_settings(sre_deep_dive_mode="parallel")
    deep = FakeDeep(settings)
    others = [p for p in PLAYBOOKS if p.id != "was.latency"] + [override]
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep, playbooks=others),
                  alarm_of("latency_sql"))
    assert deep.jobs == [] and job.briefing["deep_dive"]["mode"] == "off"


def test_fallback_playbook_always_runs_deep_even_when_mode_off() -> None:
    settings = make_settings(sre_deep_dive_mode="off")
    deep = FakeDeep(settings)
    payload = {
        "contract_version": "1",
        "event": {"dbId": "polestar", "serverName": "h1", "hostname": "h1", "alarmId": "a-1",
                  "severity": 2, "alarmName": "알 수 없는 알람", "resourceType": "Unknown",
                  "alarmTime": "20261019100000"},
        "decision": {"tier": "page"},
        "meta": {},
    }
    pb, matched_by = select_playbook(alarm_facts_from_payload(payload), PLAYBOOKS)
    assert (pb.id, matched_by) == ("fallback", "fallback")
    job = run_job(build(settings, FakeCaller({"tool_responses": {}}), deep=deep), payload)
    assert len(deep.jobs) == 1 and job.briefing["playbook"]["id"] == "fallback"
    assert job.briefing["deep_dive"]["status"] == "done"


def test_deep_failure_is_reported_and_playbook_result_kept() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel")
    deep = FakeDeep(settings, error=RuntimeError("holmes 연결 실패"))
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep),
                  alarm_of("latency_sql"))
    assert job.status == "done"
    dd = job.briefing["deep_dive"]
    assert dd["status"] == "failed" and "holmes 연결 실패" in dd["reason"]
    assert job.briefing["engine_agreement"]["result"] == "not_comparable"


# ── 판정 입력 = 두 엔진 도구 출력 합집합(G-25) ───────────────────────────────


def _oom_caller(name: str) -> FakeCaller:
    fx = _load(name)
    caller = FakeCaller(fx)
    caller.overrides["polestar_process_snapshot"] = {"note": OOM_RAW}
    return caller


def test_escalate_held_when_deep_skipped() -> None:
    settings = make_settings(sre_deep_dive_mode="off", severity_judge_enabled=True)
    deep = FakeDeep(settings)
    job = run_job(build(settings, _oom_caller("latency_external"), deep=deep),
                  alarm_of("latency_external"))
    assert deep.jobs == []
    sev = job.briefing["severity"]
    assert sev["escalate"] is True and "oom_kill" in sev["signals"]
    assert "escalate=True" in job.verdict


def test_verdict_uses_union_of_both_engines_outputs() -> None:
    settings = make_settings(sre_deep_dive_mode="parallel", severity_judge_enabled=True)
    deep = FakeDeep(settings, outputs=[ToolCallRecord("journalctl", "로그", "success", OOM_RAW)])
    job = run_job(build(settings, FakeCaller(_load("latency_external")), deep=deep),
                  alarm_of("latency_external"))
    assert job.briefing["severity"]["escalate"] is True
    assert job.briefing["engine_agreement"]["holmesgpt"]["basis"] == ["signal:oom_kill"]


# ── 잡 단위 캐시 · 진척 ─────────────────────────────────────────────────────


def test_job_cache_dedupes_real_calls_and_reports_counts() -> None:
    audit: list = []
    caller = FakeCaller(_load("latency_sql"))
    job = run_job(build(make_settings(), caller, audit=audit), alarm_of("latency_sql"))
    done = next(r for r in audit if r["event"] == "done")
    keys = [json.dumps([t, a], sort_keys=True, ensure_ascii=False) for t, a in caller.calls]
    assert len(keys) == len(set(keys))  # 같은 도구·인자는 실제로 한 번만
    assert done["cache_real_calls"] == len(caller.calls)
    assert done["playbook_tool_calls"] == len(caller.calls) + done["cache_hits"]
    assert done["playbook_id"] == "was.latency" and done["deep_dive_status"] == "skipped"
    assert job.tool_calls_summary and len(job.tool_calls_summary) == done["playbook_tool_calls"]


def test_progress_and_partial_briefing_during_collection() -> None:
    snaps: list[dict] = []
    base = FakeCaller(_load("latency_sql"))
    holder: dict = {}

    def caller(calls):  # noqa: ANN001
        job = holder.get("job")
        if job is not None:
            s = job.summary()
            snaps.append({"progress": s.get("progress"), "partial": s.get("partial_briefing")})
        return base(calls)

    d = build(make_settings(), caller)
    job = make_job(alarm_of("latency_sql"))
    holder["job"] = job
    d(job)
    d.wait_workers(30)
    progress = [s["progress"] for s in snaps if s["progress"]]
    assert progress and all(p["total"] == 10 for p in progress)
    dones = [p["done"] for p in progress]
    assert dones == sorted(dones) and dones[-1] >= 5
    assert {p["current_step"] for p in progress} <= {f"S{i}" for i in range(10)}
    partial = [s["partial"] for s in snaps if s["partial"]]
    assert partial and partial[-1]["playbook"]["id"] == "was.latency"
    assert partial[-1]["partial"] is True and "summary" not in partial[-1]  # 6요소는 종결 후
    assert len(partial[-1]["playbook"]["sections"]) >= 5
    final = job.summary()
    assert final["status"] == "done" and final["progress"] == {
        "done": 10, "total": 10, "current_step": None
    }
    assert "partial_briefing" not in final


# ── 사건(episode) 경로 ─────────────────────────────────────────────────────


def test_episode_uses_representative_alarm_playbook_and_keeps_cross_source() -> None:
    settings = make_settings(cross_source_investigation_enabled=True)
    alarm = alarm_of("latency_sql")
    alarm_id = alarm["event"]["alarmId"]
    alarm["meta"]["episode"] = {
        "episode_id": "ep-1",
        "host_key": "wasdemo01",
        "infra_db_ids": ["polestar"],
        "members": [
            {"alarm_id": "p-1", "source": "polestar", "db_id": "polestar", "kind": "cpu",
             "alarm_name": "CPU 사용률", "occurred_at": "2026-10-19T09:58:00"},
            {"alarm_id": alarm_id, "source": "jennifer", "kind": "apm",
             "was_kinds": ["was_slow_sql"], "alarm_name": "TX_BAD_RESPONSE",
             "occurred_at": "2026-10-19T10:00:00"},
        ],
        "links": [],
    }
    facts = alarm_facts_from_payload(alarm)
    assert facts.kinds[0] == "was_slow_sql"  # 게이트웨이가 준 kind 우선
    job = run_job(build(settings, FakeCaller(_load("latency_sql"))), alarm)
    b = job.briefing
    assert b["playbook"]["id"] == "was.latency"
    assert b["cross_source"]["episode_id"] == "ep-1"


def test_unmatched_without_fallback_goes_legacy_with_limitation() -> None:
    settings = make_settings()
    only_heap = [p for p in PLAYBOOKS if p.id == "was.heap"]
    deep = FakeDeep(settings)
    audit: list = []
    job = run_job(build(settings, FakeCaller(_load("latency_sql")), deep=deep,
                        playbooks=only_heap, audit=audit), alarm_of("latency_sql"))
    assert "playbook" not in job.briefing and len(deep.jobs) == 1
    assert NO_PLAYBOOK_LIMIT in job.briefing["limitations"]
    assert any(r["event"] == "playbook_unmatched" for r in audit)


# ── 후속 ①~④ ───────────────────────────────────────────────────────────────


def _gateway_literal() -> tuple[dict[str, str], ast.FunctionDef]:
    tree = ast.parse(GATEWAY_FIELDS.read_text(encoding="utf-8"))
    table: dict[str, str] | None = None
    func: ast.FunctionDef | None = None
    for node in tree.body:
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == "EVENT_TYPE_SIGNALS":
            value = ast.literal_eval(node.value)  # type: ignore[arg-type]
            table = {k: v[0] for k, v in value.items()}
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_event_type":
            func = node
    assert table is not None and func is not None
    return table, func


def test_gateway_event_signal_literal_copy_in_sync() -> None:
    table, _ = _gateway_literal()
    assert GATEWAY_EVENT_SIGNAL_KINDS == table


def test_normalize_event_type_matches_gateway_rule() -> None:
    _, func = _gateway_literal()
    namespace: dict[str, Any] = {"Any": Any}
    module = ast.Module(body=[func], type_ignores=[])
    exec(compile(module, str(GATEWAY_FIELDS), "exec"), namespace)  # noqa: S102 — 저장소 내부 파일
    for raw in ("ERROR_OUTOFMEMORY", "warning_jvm_heap_mem_high", " http_io_exception ", None, ""):
        assert normalize_event_type(raw) == namespace["normalize_event_type"](raw)


@pytest.mark.parametrize(
    ("event_type", "kind", "playbook_id"),
    [
        ("ERROR_OUTOFMEMORY", "was_heap_pressure", "was.heap"),
        ("HTTP_IO_EXCEPTION", "was_external_call_delay", "was.latency"),
        ("MAYBE_GC_TIME_DELAY", "was_gc_stall", "was.heap"),
    ],
)
def test_event_type_normalization_selects_playbook(event_type, kind, playbook_id) -> None:
    payload = {"event": {"resourceType": "apm.Instance", "alarmName": event_type},
               "meta": {"hints": {"solution": "apm", "event_type": event_type}}}
    facts = alarm_facts_from_payload(payload)
    assert kind in facts.kinds
    assert select_playbook(facts, PLAYBOOKS)[0].id == playbook_id


def test_latency_playbook_matches_slow_sql_and_external_kinds() -> None:
    pb = next(p for p in PLAYBOOKS if p.id == "was.latency")
    assert {"was_external_call_delay", "was_slow_sql"} <= set(pb.match.any_kind)


def test_heap_playbook_s1_reads_nothing() -> None:
    pb = next(p for p in PLAYBOOKS if p.id == "was.heap")
    s1 = next(s for s in pb.steps if s.id == "S1")
    assert s1.args == {"readings": []}


def test_host_to_was_without_was_is_explicit_no_was() -> None:
    from sre_agent.application.playbook_steps import RunScope, StepContext

    def apm(calls):  # noqa: ANN001
        return [json.dumps({"instances": []}) for _ in calls]

    ctx = StepContext(payload={}, windows=None, scope=RunScope(host="h1", polestar_server="h1"),
                      args={}, targets=[], polestar=None, apm=apm, now=None, prior={})
    res: StepResult = STEP_LIBRARY["scope.host_to_was"](ctx)
    assert res.status == "ok" and res.verdict == NO_WAS_VERDICT
    assert res.verdict_source == "playbook_rule"


# ── mcp_service 배선 ───────────────────────────────────────────────────────


def test_mcp_service_wiring_loads_playbooks_and_llm_adapter(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "bad.yaml").write_text("id: [broken\n", encoding="utf-8")
    seen: dict = {}

    def completion(messages, temperature=None):  # noqa: ANN001
        seen.update(messages=messages, temperature=temperature)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="소견"))])

    agent = SimpleNamespace(llm=SimpleNamespace(llm=SimpleNamespace(completion=completion)))
    settings = make_settings(sre_playbook_dir=str(tmp_path))
    with caplog.at_level(logging.WARNING):
        wiring = mcp_service._default_playbook_wiring(settings, lambda: agent)
    assert wiring is not None and wiring.playbooks == ()
    text = caplog.text
    assert "플레이북 거부" in text and "fallback 플레이북 미적재" in text
    assert wiring.llm is not None
    assert wiring.llm([{"role": "user", "content": "x"}]) == "소견"
    assert seen["temperature"] == 0

    default = mcp_service._default_playbook_wiring(make_settings(), lambda: agent)
    assert default is not None and any(p.is_fallback for p in default.playbooks)


def test_build_dispatcher_injects_wiring_only_when_enabled() -> None:
    on = mcp_service._build_dispatcher(make_settings(), audit_path=None)
    off = mcp_service._build_dispatcher(make_settings(sre_playbook_enabled=False), audit_path=None)
    assert isinstance(on._playbook, PlaybookWiring) and off._playbook is None


def test_dispatcher_module_exports() -> None:
    assert {"PlaybookWiring", "DEEP_DIVE_LABEL"} <= set(disp_mod.__all__)
