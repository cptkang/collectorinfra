"""InvestigationDispatcher 가드 검증 (Plan 02 §4) — dedup TTL·동시 상한·전체 타임아웃·예산·sweep·감사.

실 LLM 없이 fake diagnose_fn으로 결정적으로 검증한다. 백그라운드 워커는 wait_workers()로 조인한다.
"""

import asyncio
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


def test_timeout_fires_even_when_investigation_thread_hangs():
    """D-211 — 조사 스레드가 **영원히 반환하지 않아도** 타임아웃이 발화한다.

    종전 구현(asyncio.run + run_in_executor)은 wait_for 만료 후 정리 단계가 스레드
    종료를 무기한 대기해, 무한 read(죽은 MCP SSE 등)에서 timeout 감사 없이 잡이
    영원히 running으로 남았다(2026-09-09 폐쇄망 실측 960s+). 위의
    test_overall_timeout_fires_across_whole_investigation은 '결국 끝나는' 스레드만
    검증해 이 결함을 잡지 못했다 — 본 테스트가 그 구멍을 고정한다.
    """
    release = threading.Event()

    def hanging_fn(job):
        release.wait()  # 테스트가 풀어줄 때까지 절대 반환하지 않는 조사 대역
        return DiagnosisResult(answer="", tool_outputs=[])

    disp = InvestigationDispatcher(
        make_settings(), diagnose_fn=hanging_fn, timeout_seconds=0.2
    )
    job = make_job()
    started = time.monotonic()
    disp(job)
    disp.wait_workers(10)
    elapsed = time.monotonic() - started
    try:
        assert job.status == "timeout"
        assert job.reason == "investigation_timeout"
        assert "타임아웃" in job.verdict
        assert elapsed < 5, f"타임박스 미작동 — {elapsed:.1f}s 소요(무기한 대기 회귀)"
    finally:
        release.set()  # 버려진 데몬 스레드를 풀어 테스트 프로세스 잔류 방지


def test_base_exception_from_investigation_finalizes_job(tmp_path):
    """D-211 근본원인 — 조사가 **BaseException**(CancelledError)을 올려도 잡이 유실되지 않는다.

    2026-09-11 폐쇄망 스택 덤프로 확정: 조사 중 mcp_server가 죽으면 anyio 취소 스코프가
    `asyncio.CancelledError`를 올리는데, 이 예외는 3.8+에서 **BaseException 파생**이라
    종전 `except Exception`을 그대로 통과했다. 그러면 워커 스레드가 감사도 상태 전이도 없이
    조용히 죽어 잡이 영원히 running으로 남는다(덤프에 조사 스레드가 아예 없었다 — 매달린
    것이 아니라 죽은 것이었고, 그래서 타임박스도 워치독 전까지 아무것도 잡지 못했다).
    """
    audit = tmp_path / "a.jsonl"

    def cancelled_fn(job):
        raise asyncio.CancelledError("mcp 상대 사망 모사")

    disp = InvestigationDispatcher(
        make_settings(), diagnose_fn=cancelled_fn, briefing_fn=build_briefing,
        timeout_seconds=5.0, audit_path=audit,
    )
    job = make_job(server="cancel-01")
    disp(job)
    disp.wait_workers(10)

    assert job.status == "failed", f"잡이 유실됐다(영구 running 회귀): {job.status}"
    assert "CancelledError" in (job.error or "")
    events = [json.loads(l) for l in audit.read_text().splitlines()]
    assert any(e["event"] == "failed" for e in events), "실패가 감사에 남아야 한다(침묵 금지)"


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


# ── 결정적 사전수집 주입 (plans/50 G4 · D-197) ──────────────────────


def _run(disp, job):
    disp(job)
    disp.wait_workers()
    return job


def test_prefetch_skipped_without_fn_or_reference_time():
    calls = []
    disp = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing,
                                   prefetch_fn=lambda job: calls.append(job) or {"leading_signal": "x"})
    job = _run(disp, make_job(server="pf-none"))          # reference_time 없음 → 호출 자체가 없다
    assert calls == [] and job.correlation is None and job.status == "done"

    disp2 = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing)
    job2 = make_job(server="pf-nofn"); job2.reference_time = "2026-09-01T14:00:00"
    assert _run(disp2, job2).correlation is None


def test_prefetch_result_lands_on_job_and_is_audited(tmp_path):
    audit = tmp_path / "a.jsonl"
    corr = {"leading_signal": "disk_io", "metric_findings": {"disk_io": {"is_anomalous": True}},
            "alarm_summary": {"count": 1}, "timeline": [], "notes": ["n"]}
    disp = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing,
                                   prefetch_fn=lambda job: corr, audit_path=audit)
    job = make_job(server="pf-ok"); job.reference_time = "2026-09-01T14:00:00"; job.lookback_minutes = 60
    _run(disp, job)
    assert job.correlation == corr and job.status == "done"
    events = [json.loads(l) for l in audit.read_text().splitlines()]
    pf = next(e for e in events if e["event"] == "prefetch")
    assert pf["leading_signal"] == "disk_io" and pf["anomalies"] == ["disk_io"] and pf["alarms"] == 1


def test_prefetch_hang_is_timeboxed_and_investigation_proceeds(tmp_path):
    """D-211 후속 — 사전수집이 **영원히 반환하지 않아도** 조사는 계속된다.

    prefetch는 조사 타임박스 **앞의** 무가드 구간이었다 — 죽은 MCP read에 매달리면
    전체 타임아웃에 도달조차 못 하고 잡이 영원히 running으로 남는다(조사 wedge와
    같은 계열). 타임박스 만료 시 correlation=None 강등 + prefetch_failed 감사 후
    조사를 계속한다(D-197 "사전수집 실패는 조사를 막지 않는다"의 hang 확장).
    """
    audit = tmp_path / "a.jsonl"
    release = threading.Event()

    def hanging_prefetch(job):
        release.wait()  # 테스트가 풀어줄 때까지 절대 반환하지 않는 사전수집 대역
        return {"leading_signal": "x"}

    disp = InvestigationDispatcher(
        make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing,
        prefetch_fn=hanging_prefetch, prefetch_timeout_seconds=0.2, audit_path=audit,
    )
    job = make_job(server="pf-hang")
    job.reference_time = "2026-09-01T14:00:00"
    started = time.monotonic()
    disp(job)
    disp.wait_workers(10)
    elapsed = time.monotonic() - started
    try:
        assert job.status == "done", f"조사가 완주해야 한다: {job.status}"
        assert job.correlation is None
        assert elapsed < 5, f"prefetch 타임박스 미작동 — {elapsed:.1f}s 소요"
        events = [json.loads(l) for l in audit.read_text().splitlines()]
        assert any(
            e["event"] == "prefetch_failed" and "타임박스" in e["error"] for e in events
        )
    finally:
        release.set()  # 버려진 데몬 스레드 정리(테스트 잔류 방지)


def test_prefetch_failure_does_not_block_investigation(tmp_path):
    audit = tmp_path / "a.jsonl"

    def boom(job):
        raise RuntimeError("mcp down")

    disp = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing,
                                   prefetch_fn=boom, audit_path=audit)
    job = make_job(server="pf-fail"); job.reference_time = "2026-09-01T14:00:00"
    _run(disp, job)
    assert job.status == "done" and job.correlation is None
    events = [json.loads(l) for l in audit.read_text().splitlines()]
    assert any(e["event"] == "prefetch_failed" and "mcp down" in e["error"] for e in events)


# ── 상관 on 경로 브리핑 계약 단언 (plans/91 1-1 · C′-0 · SPEC-correlation-e2e-assertions) ──
# 레벨 A(과금 0): 가짜 배치로 사전수집을 **실제로 계산**해 dispatcher → build_briefing 끝까지 통과시킨다.
# 뒤 모듈(변경 오버레이·연관 호스트)이 타임라인·가설·한계에 무엇을 더하든 브리핑 도달을 이 경로가 단언한다.


def _stub_prefetch_fn():
    from sre_agent.application.evidence_prefetch import prefetch_and_correlate, scope_from_job
    from tests.test_evidence_prefetch import TOOL_ALARMS, _alarm_rows, _batch, _metric_rows

    def base(center):
        return [center + d for d in (0, 2, 1, 0, 2, 1, 0, 2, 1, 0, 2)]

    responses = {
        TOOL_ALARMS: _alarm_rows(),
        "cpu": _metric_rows("cpu", [11.0, 95.0], base(10.0)),
        "memory": _metric_rows("memory", [40.0, 41.0], base(40.0)),
        "filesystem": _metric_rows("filesystem", [50.0, 50.0], base(50.0)),
        "disk_io": _metric_rows("disk_io", [90.0, 95.0], base(10.0)),
    }

    def _fn(job):
        scope = scope_from_job(job)
        return None if scope is None else prefetch_and_correlate(scope, _batch(responses)).to_dict()

    return _fn


def _stub_job(server="corr-e2e"):
    job = make_job(server=server)
    job.payload["event"]["dbId"] = "polestar"
    job.reference_time = "2026-09-01T14:00:00"
    job.lookback_minutes = 60
    return job


def test_correlation_on_briefing_contract_reaches_user(tmp_path):
    """상관 on: rank·confidence 가설 · `T-` 상대시각 타임라인 · 결정적 한계 · prefetch 감사가 전부 도달한다."""
    from sre_agent.application.briefing_builder import CORRELATION_NOT_CAUSATION_NOTE

    audit = tmp_path / "a.jsonl"
    disp = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(answer="원인: 디스크 IO 폭주 ← polestar_metric_trend"),
                                   briefing_fn=build_briefing, prefetch_fn=_stub_prefetch_fn(), audit_path=audit)
    job = _run(disp, _stub_job())
    assert job.status == "done" and job.correlation and job.correlation["leading_signal"] == "disk_io"
    b = job.briefing
    hyps = b["root_cause_hypotheses"]
    assert [h["rank"] for h in hyps] == list(range(1, len(hyps) + 1)) and len(hyps) >= 2
    assert all(h["confidence"] in {"high", "medium", "low"} for h in hyps)
    assert hyps[0]["cause"].startswith("disk_io") and hyps[0]["confidence"] == "high"   # 선행 신호 = rank 1
    assert b["cause"] == hyps[0]["cause"]
    assert b["timeline"][0].startswith("T-") and any(" 알람 " in ln for ln in b["timeline"])
    assert CORRELATION_NOT_CAUSATION_NOTE in b["limitations"]
    assert any("정밀도" in lim for lim in b["limitations"])          # 상관 notes → limitations 자동 렌더
    events = [json.loads(l) for l in audit.read_text().splitlines()]
    pf = next(e for e in events if e["event"] == "prefetch")
    assert pf["leading_signal"] == "disk_io" and pf["investigation_id"] == job.investigation_id


def test_correlation_off_briefing_is_unchanged():
    """대조: prefetch_fn 없음 → 가설 목록 비고 `T-` 타임라인 없음(현행 비트 동일)."""
    from sre_agent.application.briefing_builder import CORRELATION_NOT_CAUSATION_NOTE

    disp = InvestigationDispatcher(make_settings(), diagnose_fn=fake_diagnose(), briefing_fn=build_briefing)
    job = _run(disp, _stub_job(server="corr-off"))
    b = job.briefing
    assert job.correlation is None and b["root_cause_hypotheses"] == []
    assert not any(ln.startswith("T-") for ln in b["timeline"])
    assert CORRELATION_NOT_CAUSATION_NOTE not in b["limitations"]
