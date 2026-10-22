"""plans/187 v2.0 독립 검증(W13) — 슬롯 누수 · 명령행 비밀값 추가 표본 · 계약 JSON 덤프.

실 LLM·MCP 호출 0. 가짜 조사 함수만 쓴다.
`PLAN187_V2_CONTRACT_DUMP=<경로>`를 주면 실물 `JobStore.get(id, activity_since)` 응답을
JSON 파일로 남긴다(본체 venv 테스트가 허브 가짜 클라이언트 입력으로 읽는다 — 두 venv 교차 검증).
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.activity_log import ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.settings import AgentSettings

SECRET = "Sup3rS3cret"


def _settings(**overrides: Any) -> AgentSettings:
    base: dict[str, Any] = {
        "model": "test/model", "api_key": None, "max_steps": 3, "gemini_api_key": "k",
        "service_bearer_token": None, "sre_activity_log_enabled": True,
        "investigation_max_concurrent": 2,
    }
    return AgentSettings(_env_file=None, **{**base, **overrides})


def _wait(cond, timeout: float = 5.0) -> None:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("조건 미충족")


# ── 의심 1: _acquire_slot 획득 뒤 기록 예외 → 슬롯 누수 ─────────────────────────


class _BoomOnStarted(ActivityLog):
    def append(self, kind: str, **kw: Any) -> int:  # type: ignore[override]
        if kind == "started":
            raise RuntimeError("기록 실패(재현용)")
        return super().append(kind, **kw)


def test_acquire_slot_does_not_leak_when_started_append_raises(tmp_path: Path) -> None:
    settings = _settings(investigation_max_concurrent=1)
    disp = InvestigationDispatcher(
        settings, diagnose_fn=lambda job, activity=None: DiagnosisResult(answer="ok"),
        briefing_fn=lambda **kw: {"ok": True}, audit_path=tmp_path / "d.jsonl",
    )
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    orig = store._new_job

    def new_job(*a: Any, **kw: Any):  # noqa: ANN202
        job = orig(*a, **kw)
        if job.activity is not None:
            job.activity = _BoomOnStarted(masker=mask_activity_text)
        return job

    store._new_job = new_job  # type: ignore[method-assign]
    iid = store.submit_diagnosis("q", server_name="s1")["investigation_id"]
    disp.wait_workers(10)
    assert store._jobs[iid].status == "failed"
    # 슬롯이 반환됐다면 비차단 획득이 성공해야 한다
    got = disp._semaphore.acquire(blocking=False)
    if got:
        disp._semaphore.release()
    assert got, "동시 상한 슬롯 누수"


# ── 의심 6: 명령행 비밀값 추가 표본 ─────────────────────────────────────────────

#: 계획 §5.6.3 ⓑ 목록 + 흔한 출력 꼴. 모두 SECRET이 남지 않아야 한다.
COVERED = (
    f"mysql -uroot -p{SECRET} -h db1",
    f"/usr/bin/mysql -uapp -p{SECRET} db",
    f"mariadb -p{SECRET}",
    f'{{"cmd": "mysql -uroot -p{SECRET} -h db"}}',
    f"--password={SECRET}",
    f"--password {SECRET}",
    f"mysql -u app --password '{SECRET}'",
    f"ftp://app:{SECRET}@host/x",
    f"mongodb://app:{SECRET}@m1:27017",
    f"redis://:{SECRET}@r1:6379",
    f"token={SECRET}",
    f"secret={SECRET}",
    f"Authorization: Bearer {SECRET}",
    f'{{"Authorization": "Bearer {SECRET}"}}',
    f"jdbc:mysql://h/db?password={SECRET}",
    f"jdbc:db2://db2:50000/PST:user=app;password={SECRET};",
    f'{{"password": "{SECRET}"}}',
    f'apm_x {{"password": "{SECRET}"}}',
    f"PGPASSWORD={SECRET} psql -h db",
)

#: 실측 누출(보고 지적용 — 표본 밖 꼴). strict xfail로 고정해 보강 시 알림이 오게 한다.
LEAKING = (
    f"password: {SECRET}",            # YAML·설정 파일 출력(cat application.yml)
    f"apikey: {SECRET}",
    f"curl -u app:{SECRET} http://x",  # 스킴 없는 user:pw
    f"export DB_PASS={SECRET}",
    f"sqlplus app/{SECRET}@db",
    f"IDENTIFIED BY '{SECRET}'",
    f"--pass {SECRET}",
)

#: 오탐 금지 — 포트·pid 옵션.
UNCHANGED = (
    "psql -h db -p 5432 -U app",
    "pg_isready -p 5432",
    "mysql -h db -P 3306 -p",
    "mysql -p 5432",
    "java -jar a.jar --port 8080",
    "ss -p 5432",
    "ps -p 1234",
    'tool {"port": 5432}',
    "password policy ok",
)


def _log_blob(sample: str) -> str:
    log = ActivityLog(masker=mask_activity_text)
    log.append("tool_end", engine="holmes", tool=sample, label=sample,
               excerpt=f"$ {sample}\nok", note=f"메모 {sample}")
    return json.dumps(log.since(0), ensure_ascii=False)


@pytest.mark.parametrize("sample", COVERED)
def test_covered_cmdline_secret_masked(sample: str) -> None:
    assert SECRET not in _log_blob(sample)


@pytest.mark.parametrize("sample", LEAKING)
def test_uncovered_secret_forms_leak(sample: str) -> None:
    assert SECRET not in _log_blob(sample)


@pytest.mark.parametrize("sample", UNCHANGED)
def test_port_and_pid_options_unchanged(sample: str) -> None:
    assert mask_activity_text(sample) == sample


# ── 의심 3: 계약 JSON 덤프(실물 JobStore.get) ────────────────────────────────────


class _Gated:
    """잡별 게이트 — 진행 중 응답을 찍을 수 있게 멈춘다. 이벤트·비밀값·AI 메모를 남긴다."""

    def __init__(self) -> None:
        self.gates: dict[str, threading.Event] = {}
        self.entered: list[str] = []
        self._lock = threading.Lock()

    def gate(self, q: str) -> threading.Event:
        with self._lock:
            return self.gates.setdefault(q, threading.Event())

    def __call__(self, job: Any, activity: ActivityLog | None = None) -> DiagnosisResult:
        assert activity is not None
        activity.append("tool_start", engine="holmes", tool="bash", label="bash")
        activity.append("tool_end", engine="holmes", tool="bash",
                        label=f"mysql -uroot -p{SECRET} -e status", status="success",
                        elapsed_ms=12, excerpt=f"url=postgres://u:{SECRET}@db/x\n4111111111111111")
        activity.append("ai_note", engine="holmes", label="HolmesGPT 1회차 메모",
                        note=f"커넥션 대기 의심 token={SECRET}")
        with self._lock:
            self.entered.append(job.question)
        assert self.gate(job.question).wait(10)
        return DiagnosisResult(answer="ok", tool_outputs=[
            ToolCallRecord("polestar_metric_trend", "추이", "success", '{"rows": []}')])


def test_contract_dump_queue_running_terminal(tmp_path: Path) -> None:
    settings = _settings()
    fake = _Gated()
    disp = InvestigationDispatcher(settings, diagnose_fn=fake,
                                   briefing_fn=lambda **kw: {"ok": True},
                                   audit_path=tmp_path / "d.jsonl")
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    ids = [store.submit_diagnosis(f"q{n}", server_name=f"srv-{n}")["investigation_id"]
           for n in range(3)]
    _wait(lambda: len(fake.entered) == 2)
    log3 = store._jobs[ids[2]].activity
    assert log3 is not None
    _wait(lambda: log3.phase == "queued")
    pages: dict[str, Any] = {
        "ids": ids,
        "running_0": store.get(ids[0], activity_since=0),
        "queued_2": store.get(ids[2], activity_since=0),
        "plain_0": store.get(ids[0]),
    }
    # 커서 중간(1 이후)
    pages["running_0_since1"] = store.get(ids[0], activity_since=1)
    for q in ("q0", "q1", "q2"):
        fake.gate(q).set()
    disp.wait_workers(10)
    pages["done_0"] = store.get(ids[0], activity_since=0)
    tail_since = pages["running_0"]["activity"]["next_seq"]
    pages["done_0_tail"] = store.get(ids[0], activity_since=tail_since)
    pages["done_2"] = store.get(ids[2], activity_since=0)
    pages["not_found"] = store.get("nope", activity_since=0)

    # sre_agent 단 계약 단언
    q = pages["queued_2"]
    assert q["status"] == "running" and q["phase"] == "queued" and q["queue_position"] == 1
    r = pages["running_0"]
    assert r["phase"] == "deep_dive" and r["queue_position"] is None
    assert [e["seq"] for e in pages["running_0_since1"]["activity"]["events"]] == list(
        range(2, r["activity"]["next_seq"] + 1))
    assert "activity" not in pages["plain_0"]
    d = pages["done_0"]
    assert d["status"] == "done" and d["phase"] is None and d["queue_position"] is None
    assert d["activity"]["events"][-1]["kind"] == "finalize"
    assert [e["kind"] for e in pages["done_0_tail"]["activity"]["events"]][-1] == "finalize"
    blob = json.dumps(pages, ensure_ascii=False)
    assert SECRET not in blob
    assert any(e["kind"] == "ai_note" for e in d["activity"]["events"])  # 관리자 경로 원천

    out = os.environ.get("PLAN187_V2_CONTRACT_DUMP")
    if out:
        Path(out).write_text(json.dumps(pages, ensure_ascii=False, indent=1), encoding="utf-8")
