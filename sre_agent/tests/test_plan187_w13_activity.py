"""plans/187 W13 — 조사 잡 실행 기록(activity) · `sre_get_investigation(activity_since)` · 가림.

실 LLM·MCP 호출 0 — 가짜 holmes 스트림(`_ScriptedLLM`) · 가짜 조사 함수 · W0 녹화 콜러블만 쓴다.
"""

from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents, StreamMessage
from sre_agent.application.investigation_dispatcher import (
    PLAYBOOK_TIMING_KEYS,
    InvestigationDispatcher,
)
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.application.playbook_steps import StepContext
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, _StreamDrain
from sre_agent.domain.activity_log import CLIP_MARK, ActivityLimits, ActivityLog
from sre_agent.infrastructure.activity_masking import mask_activity_text
from sre_agent.infrastructure.tool_result_cache import ToolResultCache
from sre_agent.interface import mcp_service
from sre_agent.interface.mcp_service import create_service
from sre_agent.settings import AgentSettings
from test_mcp_service import _call
from test_plan154_w1_instrumentation import _ScriptedLLM, _tool_event
from test_plan187_w2_runner import FakeCaller, _load
from test_plan187_w4_dispatch import FakeLLM, _comparable, alarm_of, build, make_job
from test_plan187_w4_dispatch import make_settings as playbook_settings

SECRET = "Sup3rS3cret"

#: 명령행 비밀값 표본(G-36) — 한 줄·발췌·AI 메모 어디에도 SECRET이 남으면 안 된다.
CMDLINE_SAMPLES = (
    f"mysql --password={SECRET} -h db1",
    f"mysql --password {SECRET} -h db1",
    f"mysql -uroot -p{SECRET} -h db1 -e 'select 1'",
    f"mysqldump -h db1 -p{SECRET} itam | gzip",
    f"java -jar app.jar token={SECRET}",
    f"secret={SECRET}",
    f"curl -H 'Authorization: Bearer {SECRET}' http://gw/api",
    f"curl -H 'Authorization: Basic {SECRET}' http://gw/api",
    f"psql postgresql://app:{SECRET}@db1:5432/polestar",
    f"jdbc:db2://db2:50000/PST:user=app;password={SECRET};",
    f"jdbc:postgresql://db1:5432/x?user=app&password={SECRET}",
    f"java -Dspring.datasource.password={SECRET} -jar app.jar",
    f"agent --token {SECRET} --client-secret {SECRET} --api-key {SECRET}",
    f"db2 connect to PST user app using {SECRET}",
    f"redis-cli -h r1 -a {SECRET} ping",
)

#: 오탐 금지 표본 — 가림이 바꾸면 안 된다(docs/18 2026-09-11 이름 부분일치 오탐).
NOT_SECRET_SAMPLES = (
    "ps -p 1234 -o pid,cmd",
    "mysql -P3306 -h db1",
    "llm --max-tokens 100 --token-count 3",
    "pg_dump --no-password -h db1",
    "app --password-file /etc/app/pw",
    "java -Xmx2g -jar app.jar --port 8080",
)


def new_log(**limits: Any) -> ActivityLog:
    return ActivityLog(masker=mask_activity_text, limits=ActivityLimits(**limits))


def kinds(log: ActivityLog) -> list[str]:
    return [e["kind"] for e in log.since(0)["events"]]


# ── domain 링 버퍼 ────────────────────────────────────────────────────────────


def test_cursor_is_monotonic_and_has_no_duplicates() -> None:
    log = new_log()
    seen: list[int] = []
    cursor = 0
    for batch in (3, 0, 5, 1):
        for i in range(batch):
            log.append("tool_start", engine="playbook", label=f"t{i}")
        page = log.since(cursor)
        seen += [e["seq"] for e in page["events"]]
        assert all(e["seq"] > cursor for e in page["events"])
        cursor = page["next_seq"]
    assert seen == list(range(1, 10))
    assert log.since(cursor) == {"next_seq": 9, "truncated": False, "events": []}


def test_optional_keys_absent_when_none() -> None:
    log = new_log()
    log.append("queued", engine="system", label="대기")
    (event,) = log.since(0)["events"]
    assert set(event) == {"seq", "at", "engine", "kind", "label"}


def test_event_count_cap_drops_oldest_and_marks_truncated() -> None:
    log = new_log(max_events=5)
    for i in range(8):
        log.append("tool_end", engine="holmes", label=f"t{i}")
    page = log.since(0)
    assert page["truncated"] is True and page["next_seq"] == 8
    assert [e["seq"] for e in page["events"]] == [4, 5, 6, 7, 8]


def test_total_bytes_cap_and_per_field_clip() -> None:
    log = new_log(max_total_bytes=6000)
    for _ in range(10):
        log.append("tool_end", engine="playbook", label="x", excerpt="가" * 5000, note="n" * 9000)
    page = log.since(0)
    assert page["truncated"] is True
    assert log.stats()["bytes"] <= 6000
    for e in page["events"]:
        assert len(e["excerpt"].encode()) <= 2048 and e["excerpt"].endswith(CLIP_MARK)
        assert len(e["note"].encode()) <= 2048
    assert len(json.dumps(page["events"], ensure_ascii=False).encode()) <= 6000 + 100


def test_concurrent_append_is_thread_safe() -> None:
    log = new_log(max_events=10_000, max_total_bytes=10**8)
    barrier = threading.Barrier(8)

    def worker(n: int) -> None:
        barrier.wait()
        for i in range(200):
            log.append("tool_start", engine="playbook", label=f"{n}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    events = log.since(0)["events"]
    assert [e["seq"] for e in events] == list(range(1, 1601))
    assert len({e["label"] for e in events}) == 1600


# ── 가림(G-36) ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("sample", CMDLINE_SAMPLES)
def test_cmdline_secret_never_leaks_to_label_excerpt_or_note(sample: str) -> None:
    log = new_log()
    log.append("tool_end", engine="holmes", tool=sample, label=sample, excerpt=f"$ {sample}\nok",
               note=f"메모: {sample}")
    blob = json.dumps(log.since(0), ensure_ascii=False)
    assert SECRET not in blob


@pytest.mark.parametrize("sample", NOT_SECRET_SAMPLES)
def test_masking_does_not_widen_false_positives(sample: str) -> None:
    assert mask_activity_text(sample) == sample


def test_masking_is_idempotent() -> None:
    for sample in CMDLINE_SAMPLES:
        once = mask_activity_text(sample)
        assert mask_activity_text(once) == once


# ── JobStore · MCP 계약 ───────────────────────────────────────────────────────


def store_settings(**overrides: Any) -> AgentSettings:
    base: dict[str, Any] = {
        "model": "test/model", "api_key": None, "max_steps": 3, "gemini_api_key": None,
        "service_bearer_token": None,
    }
    return AgentSettings(_env_file=None, **{**base, **overrides})


def diag_payload(n: int) -> dict:
    return {"question": f"q{n}", "server_name": f"srv-{n}"}


def test_flag_on_by_default() -> None:
    # 기본 on(사용자 결정 2026-10-29) — 끄는 경로는 아래 off 테스트가 명시 False로 지킨다.
    assert store_settings().sre_activity_log_enabled is True


def test_flag_off_activity_since_is_ignored(tmp_path) -> None:
    settings = store_settings(sre_activity_log_enabled=False)
    store = JobStore(settings, audit_path=tmp_path / "a.jsonl")
    mcp = create_service(settings, job_store=store)
    iid = store.submit_diagnosis("q")["investigation_id"]
    assert store._jobs[iid].activity is None  # 기록 자체를 만들지 않는다
    base = _call(mcp, "sre_get_investigation", {"investigation_id": iid})
    for since in (-1, 0, 7):
        got = _call(mcp, "sre_get_investigation",
                    {"investigation_id": iid, "activity_since": since})
        assert got == base


def test_flag_on_default_since_is_bit_identical_and_since_adds_keys(tmp_path) -> None:
    settings = store_settings(sre_activity_log_enabled=True, gemini_api_key="k")
    store = JobStore(settings, audit_path=tmp_path / "a.jsonl")
    mcp = create_service(settings, job_store=store)
    iid = store.submit_diagnosis("q")["investigation_id"]  # 스텁 executor → stub 종결
    job = store._jobs[iid]
    assert isinstance(job.activity, ActivityLog)
    plain = _call(mcp, "sre_get_investigation", {"investigation_id": iid})
    assert plain == json.loads(json.dumps(job.summary(), ensure_ascii=False))
    assert {"activity", "phase", "queue_position"}.isdisjoint(plain)
    live = _call(mcp, "sre_get_investigation", {"investigation_id": iid, "activity_since": 0})
    assert {k: v for k, v in live.items() if k not in plain} == {
        "activity": {"next_seq": 0, "truncated": False, "events": []},
        "phase": None, "queue_position": None, "elapsed_s": live["elapsed_s"],
    }
    # 거부 잡(기록 없음)도 같은 모양(빈 기록)
    rid = store.submit_diagnosis("")["investigation_id"]
    got = _call(mcp, "sre_get_investigation", {"investigation_id": rid, "activity_since": 0})
    assert got["activity"]["events"] == [] and got["phase"] is None


class GatedDiagnose:
    """잡마다 따로 풀리는 가짜 HolmesGPT — `activity` 선택 인자를 받는다."""

    def __init__(self) -> None:
        self.gates: dict[str, threading.Event] = {}
        self.started: list[str] = []
        self.activity_seen: list[bool] = []
        self._lock = threading.Lock()

    def gate(self, question: str) -> threading.Event:
        with self._lock:
            return self.gates.setdefault(question, threading.Event())

    def __call__(self, job: Any, activity: ActivityLog | None = None) -> DiagnosisResult:
        with self._lock:
            self.started.append(job.question)
            self.activity_seen.append(activity is not None)
        assert self.gate(job.question).wait(10)
        return DiagnosisResult(answer="ok", tool_outputs=[
            ToolCallRecord("polestar_metric_trend", "추이", "success", '{"rows": []}')])


def wait_until(cond, timeout: float = 5.0) -> None:  # noqa: ANN001
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return
        time.sleep(0.01)
    raise AssertionError("조건 미충족")


def test_concurrency_two_third_job_is_queued_then_runs(tmp_path) -> None:
    settings = store_settings(sre_activity_log_enabled=True, gemini_api_key="k",
                              investigation_max_concurrent=2)
    fake = GatedDiagnose()
    disp = InvestigationDispatcher(settings, diagnose_fn=fake,
                                   briefing_fn=lambda **kw: {"ok": True},
                                   audit_path=tmp_path / "d.jsonl")
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    ids = [store.submit_diagnosis(f"q{n}", server_name=f"srv-{n}")["investigation_id"]
           for n in range(3)]
    wait_until(lambda: len(fake.started) == 2)
    log3 = store._jobs[ids[2]].activity
    assert log3 is not None
    wait_until(lambda: log3.phase == "queued")
    third = store.get(ids[2], activity_since=0)
    assert third["status"] == "running"  # D-123 어휘 불변 — 대기도 running
    assert third["phase"] == "queued" and third["queue_position"] == 1
    assert [e["kind"] for e in third["activity"]["events"]] == ["queued"]
    first = store.get(ids[0], activity_since=0)
    assert first["phase"] == "deep_dive" and first["queue_position"] is None
    assert [e["kind"] for e in first["activity"]["events"]] == ["started", "deep_start"]

    fake.gate(fake.started[0]).set()  # 앞 조사 하나 종결 → 3번째 차례
    wait_until(lambda: len(fake.started) == 3)
    third = store.get(ids[2], activity_since=1)
    assert third["phase"] in ("collecting", "deep_dive") and third["queue_position"] is None
    assert [e["kind"] for e in third["activity"]["events"]][:1] == ["started"]
    for q in ("q0", "q1", "q2"):
        fake.gate(q).set()
    disp.wait_workers(10)
    done = store.get(ids[2], activity_since=0)
    assert done["status"] == "done" and done["phase"] is None
    assert [e["kind"] for e in done["activity"]["events"]] == [
        "queued", "started", "deep_start", "deep_end", "finalize"]
    assert fake.activity_seen == [True, True, True]  # diagnose_fn 선택 인자로 넘어간다


def test_flag_off_dispatcher_calls_diagnose_without_activity(tmp_path) -> None:
    settings = store_settings(sre_activity_log_enabled=False, gemini_api_key="k")
    seen: list[dict] = []

    def diagnose(job: Any, **kwargs: Any) -> DiagnosisResult:
        seen.append(kwargs)
        return DiagnosisResult(answer="ok")

    disp = InvestigationDispatcher(settings, diagnose_fn=diagnose,
                                   briefing_fn=lambda **kw: {"ok": True})
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    iid = store.submit_diagnosis("q")["investigation_id"]
    disp.wait_workers(10)
    assert seen == [{}]  # 종전 호출 그대로(선택 인자 없음)
    assert "activity" not in store.get(iid, activity_since=0)


# ── HolmesGPT 스트림 훅 ───────────────────────────────────────────────────────


def _answer_end(content: str = "결론") -> StreamMessage:
    return StreamMessage(event=StreamEvents.ANSWER_END, data={
        "content": content, "messages": [{"role": "user", "content": "q"}],
        "metadata": {"finish_reason": "stop"}, "num_llm_calls": 2,
        "costs": {"total_tokens": 50, "total_cost": 0.0},
    })


def _holmes_script() -> list[list[StreamMessage]]:
    error_result = ToolCallResult(
        tool_call_id="c2", tool_name="bash", description=f"mysql -uroot -p{SECRET} -e status",
        result=StructuredToolResult(status=StructuredToolResultStatus.ERROR,
                                    error=f"denied password={SECRET}"),
    )
    return [[
        StreamMessage(event=StreamEvents.AI_MESSAGE,
                      data={"content": f"커넥션 대기 의심 — token={SECRET} 확인",
                            "reasoning": None}),
        StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "polestar_metric_trend",
                                                           "id": "c1"}),
        StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "bash", "id": "c2"}),
        _tool_event("c1", "polestar_metric_trend", f"cpu 91% url=postgres://u:{SECRET}@db/x"),
        StreamMessage(event=StreamEvents.TOOL_RESULT, data=error_result.to_client_dict()),
        StreamMessage(event=StreamEvents.TOKEN_COUNT, data={"metadata": {}}),
        _answer_end(),
    ]]


def test_holmes_stream_hooks_record_tools_judge_rounds_and_ai_note() -> None:
    log = new_log()
    result = _StreamDrain(_ScriptedLLM(_holmes_script()), activity=log).run(
        [{"role": "user", "content": "q"}])
    events = log.since(0)["events"]
    assert [e["kind"] for e in events] == [
        "llm_start", "llm_end", "ai_note", "tool_start", "tool_start", "tool_end", "tool_end",
        "llm_start", "llm_end"]
    assert all(e["engine"] == "holmes" for e in events)
    by_tool = {(e["kind"], e["tool"]): e["seq"] for e in events if "tool" in e}
    for name in ("polestar_metric_trend", "bash"):
        assert by_tool[("tool_start", name)] < by_tool[("tool_end", name)]
    ends = [e for e in events if e["kind"] == "tool_end"]
    assert ends[0]["label"] == "polestar_metric_trend 호출" and ends[0]["status"] == "success"
    assert "cpu 91%" in ends[0]["excerpt"] and isinstance(ends[0]["elapsed_ms"], int)
    assert ends[1]["status"] == "error" and "denied" in ends[1]["excerpt"]
    notes = [e for e in events if e["kind"] == "ai_note"]
    assert len(notes) == 1 and "커넥션 대기 의심" in notes[0]["note"]
    assert [e["label"] for e in events if e["kind"] == "llm_start"] == [
        "HolmesGPT 판단 1회차", "HolmesGPT 판단 2회차"]
    assert SECRET not in json.dumps(events, ensure_ascii=False)
    # 기록이 결과 조립을 바꾸지 않는다
    plain = _StreamDrain(_ScriptedLLM(_holmes_script())).run([{"role": "user", "content": "q"}])
    assert result.model_dump() == plain.model_dump()


def test_default_diagnose_fn_passes_activity_to_agent_ask() -> None:
    seen: list[Any] = []
    agent = SimpleNamespace(
        mcp_unavailable=lambda: {},
        tool_names=lambda: frozenset(),
        ask=lambda q, system_prompt_additions=None, activity=None: (
            seen.append(activity) or DiagnosisResult(answer="ok")),
    )
    fn = mcp_service._default_diagnose_fn(store_settings(), agent_getter=lambda: agent)
    job = SimpleNamespace(investigation_id="i", kind="diagnosis", question="q", payload={},
                          reference_time=None, lookback_minutes=None, correlation=None)
    log = new_log()
    fn(job, activity=log)
    fn(job)
    assert seen == [log, None]


# ── 플레이북 경로 ─────────────────────────────────────────────────────────────


def _untimed(value: Any) -> Any:
    """소요 시간 칸(`elapsed_ms`·`timings`·플레이북 실행 시간 계측 — plans/200 I-0)을 뺀 사본.

    `opinion_wait_s`는 단계 끝 해석 실행기(실행 기록 on)가 있을 때만 값이 있어 on/off가 다르다.
    """
    if isinstance(value, dict):
        return {k: _untimed(v) for k, v in value.items()
                if k not in ("elapsed_ms", "timings", *PLAYBOOK_TIMING_KEYS)}
    if isinstance(value, list):
        return [_untimed(v) for v in value]
    return value


def test_playbook_job_records_steps_tools_llm_notes_and_is_otherwise_unchanged() -> None:
    settings = playbook_settings()
    with_log = make_job(alarm_of("latency_sql"), "inv-a")
    with_log.activity = new_log()
    d1 = build(settings, FakeCaller(_load("latency_sql")), llm=FakeLLM())
    d1(with_log)
    d1.wait_workers(30)
    plain = make_job(alarm_of("latency_sql"), "inv-b")
    d2 = build(settings, FakeCaller(_load("latency_sql")), llm=FakeLLM())
    d2(plain)
    d2.wait_workers(30)
    # 기록은 결과를 바꾸지 않는다(소요 시간 칸만 다르다)
    assert _untimed(_comparable(with_log)) == _untimed(_comparable(plain))

    events = with_log.activity.since(0)["events"]
    ks = [e["kind"] for e in events]
    assert ks[0] == "started" and ks[-1] == "finalize" and events[-1]["status"] == "done"
    for kind in ("step_start", "step_end", "tool_start", "tool_end", "llm_start", "llm_end",
                 "ai_note"):
        assert kind in ks, kind
    starts = {}
    for e in events:
        if e["kind"] == "tool_start":
            starts.setdefault((e["step"], e["tool"]), e["seq"])
        if e["kind"] == "tool_end" and e["status"] != "error":
            assert starts[(e["step"], e["tool"])] < e["seq"]
            assert "excerpt" in e and "elapsed_ms" in e
    notes = [e for e in events if e["kind"] == "ai_note"]
    assert notes and all(e["engine"] == "playbook" and e["step"] for e in notes)
    assert with_log.summary().get("activity") is None  # summary()에는 키가 없다


def test_step_context_marks_cache_hits_as_cached() -> None:
    raw = json.dumps({"rows": [1, 2, 3]})
    cache = ToolResultCache()
    caller = cache.wrap(lambda calls: [raw for _ in calls])
    log = new_log()

    def ctx() -> StepContext:
        c = StepContext(payload={}, windows=None, scope=None, args={}, targets=[],  # type: ignore[arg-type]
                        polestar=caller, apm=None, now=None, prior={})  # type: ignore[arg-type]
        c.activity, c.step_id = log, "S3"
        return c

    ctx().call_one("polestar", "polestar_process_snapshot", {"server": "a"})
    ctx().call_one("polestar", "polestar_process_snapshot", {"server": "a"})
    ctx().call_one("apm", "apm_slow_transactions", {"instance": "A1"})  # 축 미구성
    ends = [e for e in log.since(0)["events"] if e["kind"] == "tool_end"]
    assert [e["status"] for e in ends] == ["success", "cached", "error"]
    assert ends[0]["rows"] == 3 and ends[0]["label"] == 'polestar_process_snapshot {"server": "a"}'
    assert ends[2]["excerpt"] == "APM 게이트웨이 미구성"


def test_parallel_deep_dive_gets_activity_and_records_deep_events() -> None:
    settings = playbook_settings(sre_deep_dive_mode="parallel")
    seen: list[Any] = []

    def deep(job: Any, activity: ActivityLog | None = None) -> DiagnosisResult:
        seen.append(activity)
        activity.append("tool_start", engine="holmes", tool="uptime", label="uptime")
        return DiagnosisResult(answer="심층 서술", tool_outputs=[
            ToolCallRecord("apm_resource_pool", "풀 조회", "success", '{"ok": true}')])

    job = make_job(alarm_of("latency_sql"), "inv-p")
    job.activity = new_log()
    d = build(settings, FakeCaller(_load("latency_sql")), deep=deep, llm=FakeLLM())
    d(job)
    d.wait_workers(30)
    assert job.status == "done" and seen == [job.activity]
    events = job.activity.since(0)["events"]
    deep_start = next(e for e in events if e["kind"] == "deep_start")
    deep_end = next(e for e in events if e["kind"] == "deep_end")
    assert deep_start["label"] == "심층 조사(HolmesGPT) 시작 — 병렬"  # plans/196 D6
    assert deep_end["status"] == "done" and deep_start["seq"] < deep_end["seq"]
    assert any(e["engine"] == "holmes" for e in events)
    assert events[-1]["kind"] == "finalize"


# ── 교정 1(plans/187 v2 검증 High · Medium 1 · Low 2) ─────────────────────────

#: 표본 밖 꼴 보강 뒤에도 그대로 둘 것(낱말 안의 `pass` · sysdba · 띄어 쓴 `=` · 토큰 수).
FIX1_NOT_SECRET = (
    "bypass: true",
    "compass: north",
    "sqlplus / as sysdba",
    "max_tokens: 100",
    "password:",
)


@pytest.mark.parametrize("sample", FIX1_NOT_SECRET)
def test_fix1_new_patterns_keep_non_secrets(sample: str) -> None:
    assert mask_activity_text(sample) == sample


def test_fix1_quoted_identified_by_keeps_quotes() -> None:
    assert mask_activity_text(f"ALTER USER app IDENTIFIED BY '{SECRET}';") == (
        "ALTER USER app IDENTIFIED BY '***';")


class _BoomLog(ActivityLog):
    """모든 append가 실패하는 기록 — best-effort 확인용."""

    def append(self, kind: str, **kw: Any) -> int:  # type: ignore[override]
        raise RuntimeError("기록 실패(재현용)")


def test_fix1_recording_failure_does_not_break_holmes_stream() -> None:
    boom = _BoomLog(masker=mask_activity_text)
    result = _StreamDrain(_ScriptedLLM(_holmes_script()), activity=boom).run(
        [{"role": "user", "content": "q"}])
    plain = _StreamDrain(_ScriptedLLM(_holmes_script())).run([{"role": "user", "content": "q"}])
    assert result.model_dump() == plain.model_dump()


def test_fix1_recording_failure_does_not_break_step_call_or_narrator() -> None:
    from sre_agent.application.playbook_narrator import PlaybookNarrator

    raw = json.dumps({"rows": [1]})
    ctx = StepContext(payload={}, windows=None, scope=None, args={}, targets=[],  # type: ignore[arg-type]
                      polestar=lambda calls: [raw for _ in calls], apm=None, now=None,  # type: ignore[arg-type]
                      prior={})
    ctx.activity, ctx.step_id = _BoomLog(masker=mask_activity_text), "S1"
    assert ctx.call_one("polestar", "polestar_process_snapshot", {"server": "a"}).ok
    assert ctx.call_one("apm", "apm_slow_transactions", {"instance": "A1"}).error

    boom = _BoomLog(masker=mask_activity_text)
    narrator = PlaybookNarrator(lambda messages: "소견", activity=boom)
    assert narrator._logged_call([{"role": "user", "content": "q"}], step="S1", label="S1",
                                 note_label="S1 소견 초안") == "소견"


def test_fix1_finalize_is_recorded_before_terminal_status(tmp_path) -> None:
    seen: list[str] = []
    settings = store_settings(sre_activity_log_enabled=True, gemini_api_key="k")
    disp = InvestigationDispatcher(
        settings, diagnose_fn=lambda job, activity=None: DiagnosisResult(answer="ok"),
        briefing_fn=lambda **kw: {"ok": True}, audit_path=tmp_path / "d.jsonl",
    )
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    orig = store._new_job

    def new_job(*a: Any, **kw: Any):  # noqa: ANN202
        job = orig(*a, **kw)

        class _Spy(ActivityLog):
            def append(self, kind: str, **fields: Any) -> int:  # type: ignore[override]
                if kind == "finalize":
                    seen.append(job.status)
                return super().append(kind, **fields)

        if job.activity is not None:
            job.activity = _Spy(masker=mask_activity_text)
        return job

    store._new_job = new_job  # type: ignore[method-assign]
    iid = store.submit_diagnosis("q", server_name="s1")["investigation_id"]
    disp.wait_workers(10)
    job = store._jobs[iid]
    assert job.status == "done" and seen == ["running"]
    fin = job.activity.since(0)["events"][-1]
    assert fin["kind"] == "finalize" and fin["status"] == "done"


# ── 교정 2 — dispatcher 기록 best-effort 통일 · 이중 finalize 고정 ────────────


class _BoomOnStepAndDeep(ActivityLog):
    """스텝·심층·대기 기록만 실패 — 슬롯 획득(`started`)·`finalize`는 정상."""

    def append(self, kind: str, **kw: Any) -> int:  # type: ignore[override]
        if kind in ("step_start", "step_end", "deep_start", "deep_end", "queued"):
            raise RuntimeError("기록 실패(재현용)")
        return super().append(kind, **kw)


def test_fix2_step_wrapper_record_failure_keeps_step_results() -> None:
    settings = playbook_settings()
    boom = make_job(alarm_of("latency_sql"), "inv-boom")
    boom.activity = _BoomOnStepAndDeep(masker=mask_activity_text)
    d1 = build(settings, FakeCaller(_load("latency_sql")), llm=FakeLLM())
    d1(boom)
    d1.wait_workers(30)
    plain = make_job(alarm_of("latency_sql"), "inv-plain")
    d2 = build(settings, FakeCaller(_load("latency_sql")), llm=FakeLLM())
    d2(plain)
    d2.wait_workers(30)
    assert boom.status == "done"
    assert _untimed(_comparable(boom)) == _untimed(_comparable(plain))
    ks = kinds(boom.activity)
    assert "step_start" not in ks and "tool_end" in ks and ks[-1] == "finalize"


def test_fix2_audit_failure_after_done_leaves_two_finalize_events(tmp_path) -> None:
    """수용된 동작 고정: done 확정 뒤 감사 단계 예외면 finalize(done)·finalize(failed) 2건.

    실제로 일어난 일(종결 확정 → 후처리 실패 → failed)을 그대로 보인다(교정 2 판단).
    """
    settings = store_settings(sre_activity_log_enabled=True, gemini_api_key="k")
    disp = InvestigationDispatcher(
        settings, diagnose_fn=lambda job, activity=None: DiagnosisResult(answer="ok"),
        briefing_fn=lambda **kw: {"ok": True}, audit_path=tmp_path / "d.jsonl",
    )
    orig_audit = disp._audit

    def audit(record: dict) -> None:
        if record.get("event") == "done":
            raise RuntimeError("감사 실패(재현용)")
        orig_audit(record)

    disp._audit = audit  # type: ignore[method-assign]
    store = JobStore(settings, executor=disp, audit_path=tmp_path / "a.jsonl")
    iid = store.submit_diagnosis("q", server_name="s1")["investigation_id"]
    disp.wait_workers(10)
    job = store._jobs[iid]
    assert job.status == "failed"
    finals = [e["status"] for e in job.activity.since(0)["events"] if e["kind"] == "finalize"]
    assert finals == ["done", "failed"]


# ── 교정 3 — 띄어 쓴 `=` 누출 · 판정·수량 꼴 오탐(재검증 Low 4건) ─────────────


def test_fix3_spaced_equals_with_sensitive_key_is_masked() -> None:
    assert mask_activity_text(f"secret_key = {SECRET}") == "secret_key = ***"
    assert mask_activity_text("password = changed") == "password = ***"
    assert mask_activity_text("password: 123456") == "password: ***"  # 숫자 비밀번호는 가린다


@pytest.mark.parametrize("sample", ("pass: 3/5 checks", "test pass: ok", "secret_count: 0",
                                    "token_total: 5000", "token: true"))
def test_fix3_verdict_and_count_forms_are_not_masked(sample: str) -> None:
    assert mask_activity_text(sample) == sample


def test_fix3_existing_masked_samples_still_masked() -> None:
    from test_plan187_v2_verify_activity import COVERED, LEAKING

    for sample in (*CMDLINE_SAMPLES, *COVERED, *LEAKING, f"pass: {SECRET}"):
        assert SECRET not in mask_activity_text(sample), sample
