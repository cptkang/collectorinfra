"""동시 상한 대기 중 종결된 잡은 슬롯을 얻어도 조사하지 않는다(`_skip_if_stale`).

과부하에서 JobStore 워치독이 대기 중인 잡을 `failed(stuck_watchdog)`로 확정한 뒤에도 워커가 슬롯을 얻으면
조사를 끝까지 돌렸다 — 결과를 받을 쪽이 없고 종결 상태가 뒤집히며, 뒤의 새 잡이 그만큼 더 기다렸다.
holmes 없이 돌도록 `sre_agent.diagnosis`를 import하지 않는다(조사 함수는 호출 여부만 본다).
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.domain.job_watchdog import stuck_after_seconds
from sre_agent.settings import AgentSettings


def _settings(**overrides) -> AgentSettings:
    defaults = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "gemini_api_key": "k",
        "service_bearer_token": None,
        "investigation_timeout_seconds": 300,
        "investigation_dedup_ttl_seconds": None,
        "investigation_max_concurrent": 1,
        "investigation_hourly_budget": None,
        "severity_judge_enabled": False,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _job(name: str, now: float) -> InvestigationJob:
    return InvestigationJob(
        investigation_id=f"id-{name}",
        kind="alarm",
        status="running",
        created_at=now,
        updated_at=now,
        fingerprint=f"fp-{name}",
        payload={"event": {"serverName": f"host-{name}", "severity": 2}, "decision": {"tier": "PAGE"}},
    )


class _Harness:
    """슬롯 1개를 첫 잡이 붙잡고 있는 동안 두 번째 잡을 대기시킨다."""

    def __init__(self, tmp_path) -> None:
        self.clock = _Clock()
        self.called: list[str] = []
        self.first_started = threading.Event()
        self.release_first = threading.Event()
        self.audit_path = tmp_path / "audit.jsonl"
        self.disp = InvestigationDispatcher(
            _settings(),
            diagnose_fn=self._diagnose,
            briefing_fn=lambda **_: {},
            timeout_seconds=300.0,
            audit_path=self.audit_path,
            wall_clock=self.clock,
        )

    def _diagnose(self, job):
        self.called.append(job.investigation_id)
        if job.investigation_id == "id-first":
            self.first_started.set()
            self.release_first.wait(5)
        return SimpleNamespace(answer="x", tool_calls=[], tool_outputs=[], total_tokens=0, total_cost=0.0)

    def queue_second(self) -> InvestigationJob:
        self.disp(_job("first", self.clock.now))
        assert self.first_started.wait(5)
        second = _job("second", self.clock.now)
        self.disp(second)
        time.sleep(0.2)  # 두 번째 워커가 세마포어 대기에 들어가게
        assert second.status == "running"
        return second

    def finish(self) -> None:
        self.release_first.set()
        self.disp.wait_workers(10)

    def audit(self) -> list[dict]:
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]


def test_watchdog_failed_job_is_not_investigated_after_acquire(tmp_path):
    h = _Harness(tmp_path)
    second = h.queue_second()
    # 대기 중 JobStore 워치독이 확정한 상황을 재현.
    second.status, second.reason = "failed", "stuck_watchdog"
    h.finish()

    assert h.called == ["id-first"]
    assert (second.status, second.reason) == ("failed", "stuck_watchdog")
    skipped = [r for r in h.audit() if r["event"] == "skipped_stale"]
    assert len(skipped) == 1
    assert skipped[0]["investigation_id"] == "id-second"
    assert skipped[0]["status"] == "failed"  # 재기동 복구가 미종결로 오인하지 않게


def test_job_past_watchdog_threshold_is_finalized_and_skipped(tmp_path):
    h = _Harness(tmp_path)
    second = h.queue_second()
    # 조회가 없어 워치독이 돌지 않은 채 임계를 넘긴 상황.
    h.clock.now += stuck_after_seconds(300) + 1
    h.finish()

    assert h.called == ["id-first"]
    assert second.status == "failed"
    assert second.reason == "stuck_watchdog"  # 워치독과 같은 사유 어휘
    assert "조사 미착수" in (second.error or "")
    assert second.updated_at == h.clock.now
    assert any(r["event"] == "skipped_stale" and r["status"] == "failed" for r in h.audit())


def test_job_within_threshold_still_runs(tmp_path):
    h = _Harness(tmp_path)
    h.queue_second()
    h.clock.now += stuck_after_seconds(300) - 1
    h.finish()

    assert h.called == ["id-first", "id-second"]
    assert not any(r["event"] == "skipped_stale" for r in h.audit())


def test_skip_releases_slot_and_host_for_next_job(tmp_path):
    h = _Harness(tmp_path)
    second = h.queue_second()
    second.status = "failed"
    h.finish()

    third = _job("third", h.clock.now)
    h.disp(third)
    h.disp.wait_workers(10)
    assert h.called == ["id-first", "id-third"]
    assert h.disp._inflight_hosts == {}


def test_stuck_after_seconds_matches_watchdog_formula():
    assert stuck_after_seconds(300) == 600.0
    assert stuck_after_seconds(900) == 1800.0
    assert stuck_after_seconds(60) == 600.0  # 하한
