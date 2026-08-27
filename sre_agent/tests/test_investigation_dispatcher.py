"""InvestigationDispatcher 가드 검증 (Plan 02 §4) — dedup TTL·동시 상한·전체 타임아웃·예산·sweep·감사.

실 LLM 없이 fake diagnose_fn으로 결정적으로 검증한다. 백그라운드 워커는 wait_workers()로 조인한다.
"""

import json
import threading
import time

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob, JobStore
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.settings import AgentSettings

OOM_RAW = "kernel: Out of memory: Killed process 12345 (java)"


def make_settings(gemini_api_key="k", **overrides) -> AgentSettings:
    defaults = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "gemini_api_key": gemini_api_key,
        "service_bearer_token": None,
        "investigation_timeout_seconds": 300,
        "investigation_dedup_ttl_seconds": None,
        "investigation_max_concurrent": 2,
        "investigation_hourly_budget": None,
        "severity_judge_enabled": False,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def make_job(
    fingerprint="fp", severity=2, kind="alarm", server="web-01"
) -> InvestigationJob:
    """조사 잡 대역.

    `server`가 인자인 이유(2026-08-27 · L-4 도입): **같은 호스트는 동시 조사가 거부된다**
    (`host_investigation_in_flight`). 예산·동시성처럼 **다른 가드를 검증하는 테스트**는
    서로 다른 호스트를 써야 그 가드에 도달한다 — 아니면 L-4가 먼저 막아 의도한 경로를
    한 번도 밟지 못한다.
    """
    now = time.time()
    return InvestigationJob(
        investigation_id=f"id-{fingerprint}-{now}",
        kind=kind,
        status="running",
        created_at=now,
        updated_at=now,
        fingerprint=fingerprint,
        payload={"event": {"serverName": server, "severity": severity}, "decision": {"tier": "PAGE"}},
    )


def fake_diagnose(answer="원인 서술 ← journalctl", tokens=0, cost=0.0, raw=None):
    """고정 DiagnosisResult를 반환하는 fake diagnose_fn을 만든다."""

    def _fn(job):
        outputs = [ToolCallRecord("journalctl", "로그 조회", "success", raw or "clean")]
        return DiagnosisResult(
            answer=answer,
            tool_calls=["journalctl 로그 조회"],
            tool_outputs=outputs,
            total_tokens=tokens,
            total_cost=cost,
        )

    return _fn


# ── 스텁(LLM 키 부재) — 가드는 적용하되 조사 미실행 ─────────────────


def test_stub_when_no_key():
    disp = InvestigationDispatcher(make_settings(gemini_api_key=None), diagnose_fn=fake_diagnose())
    job = make_job()
    disp(job)  # 동기 스텁 확정(백그라운드 없음)
    assert job.status == "stub"
    assert "LLM 키 부재" in job.verdict
    assert job.briefing["stub"] is True


# ── dedup TTL ─────────────────────────────────────────────────────


def test_dedup_ttl_suppresses_within_window():
    now = [1000.0]
    disp = InvestigationDispatcher(
        make_settings(investigation_dedup_ttl_seconds=100.0),
        diagnose_fn=fake_diagnose(),
        briefing_fn=build_briefing,
        clock=lambda: now[0],
    )
    j1 = make_job(fingerprint="dup")
    disp(j1)
    disp.wait_workers(5)
    assert j1.status == "done"

    now[0] = 1050.0  # ttl 내 → 억제
    j2 = make_job(fingerprint="dup")
    disp(j2)
    assert j2.status == "rejected"
    assert j2.reason == "dedup_ttl_active"

    now[0] = 1200.0  # ttl 경과 → 재조사 허용
    j3 = make_job(fingerprint="dup")
    disp(j3)
    disp.wait_workers(5)
    assert j3.status == "done"


# ── 시간당 예산 ───────────────────────────────────────────────────


def test_hourly_budget_rejects_over_limit():
    now = [0.0]
    disp = InvestigationDispatcher(
        make_settings(investigation_hourly_budget=2),
        diagnose_fn=fake_diagnose(),
        briefing_fn=build_briefing,
        clock=lambda: now[0],
    )
    # 호스트를 분리한다 — 같은 호스트면 L-4가 먼저 막아 예산 가드에 도달하지 못한다.
    j1, j2, j3 = (
        make_job("a", server="web-01"),
        make_job("b", server="web-02"),
        make_job("c", server="web-03"),
    )
    disp(j1)
    disp(j2)
    disp(j3)
    disp.wait_workers(5)
    assert j1.status == "done"
    assert j2.status == "done"
    assert j3.status == "rejected"
    assert j3.reason == "hourly_budget_exceeded"


# ── 동시 상한(세마포어) ───────────────────────────────────────────


def test_max_concurrent_caps_parallelism():
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def slow_fn(job):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.15)
        with lock:
            state["current"] -= 1
        return DiagnosisResult(answer="x ← t", tool_outputs=[ToolCallRecord("t", "d", "success", "clean")])

    disp = InvestigationDispatcher(
        make_settings(investigation_max_concurrent=2),
        diagnose_fn=slow_fn,
        briefing_fn=build_briefing,
        timeout_seconds=10.0,
    )
    # 호스트를 분리한다 — 같은 호스트면 L-4가 직렬화해 동시 상한을 측정할 수 없다.
    for i in range(6):
        disp(make_job(fingerprint=f"c{i}", server=f"web-{i:02d}"))
    disp.wait_workers(10)
    assert state["peak"] == 2  # 상한 2를 넘지 않고, 병렬성이 실제로 2까지 도달


# ── 전체 타임아웃(per-call 아닌 조사 전체) ────────────────────────


def test_overall_timeout_fires_across_whole_investigation():
    # 내부 '도구 호출' 여러 번(각 0.05s < 타임아웃)이지만 **합계 0.3s > 타임아웃 0.1s**.
    # per-call 타임아웃이면 트립하지 않지만, 조사 전체를 감싸므로 트립한다.
    def multi_step_fn(job):
        for _ in range(6):
            time.sleep(0.05)
        return DiagnosisResult(answer="완료", tool_outputs=[])

    disp = InvestigationDispatcher(
        make_settings(),
        diagnose_fn=multi_step_fn,
        timeout_seconds=0.1,
    )
    job = make_job()
    disp(job)
    disp.wait_workers(10)
    assert job.status == "timeout"
    assert job.reason == "investigation_timeout"
    assert "타임아웃" in job.verdict


def test_fast_investigation_does_not_timeout():
    disp = InvestigationDispatcher(
        make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing, timeout_seconds=5.0
    )
    job = make_job()
    disp(job)
    disp.wait_workers(10)
    assert job.status == "done"


# ── 토큰 비용 감사 ────────────────────────────────────────────────


def test_token_cost_audit(tmp_path):
    audit = tmp_path / "decisions.jsonl"
    disp = InvestigationDispatcher(
        make_settings(),
        diagnose_fn=fake_diagnose(tokens=123, cost=0.5),
        briefing_fn=build_briefing,
        timeout_seconds=5.0,
        audit_path=audit,
    )
    job = make_job()
    disp(job)
    disp.wait_workers(10)
    assert job.tokens == 123
    assert job.cost == 0.5
    records = [json.loads(x) for x in audit.read_text().splitlines() if x.strip()]
    done = [r for r in records if r["event"] == "done"]
    assert done and done[0]["tokens"] == 123 and done[0]["cost"] == 0.5


# ── severity_judge 배선(escalate-only) ────────────────────────────


def test_severity_judge_wired_escalates():
    disp = InvestigationDispatcher(
        make_settings(severity_judge_enabled=True),
        diagnose_fn=fake_diagnose(answer="OOM 확인 ← journalctl", raw=OOM_RAW),
        briefing_fn=build_briefing,
        timeout_seconds=5.0,
    )
    job = make_job(severity=2)  # 경고
    disp(job)
    disp.wait_workers(10)
    assert job.status == "done"
    assert job.briefing["severity"]["level"] == "심각"
    assert job.briefing["severity"]["escalate"] is True


def test_severity_judge_disabled_inherits_gate():
    disp = InvestigationDispatcher(
        make_settings(severity_judge_enabled=False),
        diagnose_fn=fake_diagnose(answer="OOM 확인 ← journalctl", raw=OOM_RAW),
        briefing_fn=build_briefing,
        timeout_seconds=5.0,
    )
    job = make_job(severity=2)
    disp(job)
    disp.wait_workers(10)
    assert job.briefing["severity"]["level"] == "경고"  # 게이트 승계(상향 없음)
    assert job.briefing["severity"]["escalate"] is False


# ── sweep(값 bound + 키 만료) ─────────────────────────────────────


def test_dedup_sweep_removes_expired_keys():
    now = [0.0]
    disp = InvestigationDispatcher(
        make_settings(investigation_dedup_ttl_seconds=10.0),
        diagnose_fn=fake_diagnose(),
        clock=lambda: now[0],
    )
    disp(make_job("fp1"))
    disp.wait_workers(5)
    assert "fp1" in disp._dedup

    now[0] = 100.0  # ttl 경과 → sweep이 fp1 제거
    disp(make_job("fp2"))
    disp.wait_workers(5)
    assert "fp1" not in disp._dedup
    assert "fp2" in disp._dedup


def test_budget_window_sweep_prunes_old():
    now = [0.0]
    disp = InvestigationDispatcher(
        make_settings(investigation_hourly_budget=100),
        diagnose_fn=fake_diagnose(),
        clock=lambda: now[0],
    )
    disp(make_job("a"))
    disp.wait_workers(5)
    assert len(disp._budget_window) == 1

    now[0] = 4000.0  # 1시간 초과 → 이전 항목 sweep
    disp(make_job("b"))
    disp.wait_workers(5)
    assert len(disp._budget_window) == 1  # 오래된 것 제거, 새 것만


# ── JobStore executor로 주입(엔드투엔드) ──────────────────────────


def test_injected_as_jobstore_executor_stub_path(tmp_path):
    # 키 부재 → JobStore submit이 dispatcher 스텁으로 확정된다(기존 계약 유지).
    settings = make_settings(gemini_api_key=None)
    disp = InvestigationDispatcher(settings, diagnose_fn=fake_diagnose())
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "audit.jsonl")
    payload = {
        "contract_version": "1",
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 2},
        "decision": {"fingerprint": "e2e", "tier": "PAGE"},
    }
    res = store.submit(payload)
    assert res["status"] == "accepted"
    got = store.get(res["investigation_id"])
    assert got["status"] == "stub"
    assert "LLM 키 부재" in got["verdict"]


def test_injected_as_jobstore_executor_real_path(tmp_path):
    settings = make_settings(gemini_api_key="k", severity_judge_enabled=True)
    disp = InvestigationDispatcher(
        settings,
        diagnose_fn=fake_diagnose(answer="OOM ← journalctl", raw=OOM_RAW, tokens=50, cost=0.1),
        briefing_fn=build_briefing,
        timeout_seconds=5.0,
    )
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "audit.jsonl")
    payload = {
        "contract_version": "1",
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 2},
        "decision": {"fingerprint": "e2e-real", "tier": "PAGE"},
    }
    res = store.submit(payload)
    assert res["status"] == "accepted"  # 백그라운드 위임 → running 남김
    disp.wait_workers(10)
    got = store.get(res["investigation_id"])
    assert got["status"] == "done"
    assert got["tokens"] == 50
    assert got["briefing"]["severity"]["level"] == "심각"
