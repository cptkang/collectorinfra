"""JobStore 잡 저장소 검증 (Plan 05 §3·§8) — 상태 전이·dedup·sweep·재기동 복구·감사 JSONL·스텁 명시성."""

import json

from sre_agent.application.investigation_jobs import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    JobStore,
    make_stub_executor,
)
from sre_agent.settings import AgentSettings


def make_settings(gemini_api_key=None, **overrides) -> AgentSettings:
    # .env / 환경변수 누수 차단 — 검증 대상 필드를 명시한다.
    defaults = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "investigation_llm_model": "gemini/gemini-2.0-flash",
        "gemini_api_key": gemini_api_key,
        "service_bearer_token": None,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def valid_payload(fingerprint="fp-1", severity=2, **event_overrides) -> dict:
    event = {
        "dbId": "db1",
        "serverName": "web-01",
        "hostname": "web-01.local",
        "alarmId": "a1",
        "severity": severity,
    }
    event.update(event_overrides)
    return {
        "contract_version": "1",
        "event": event,
        "decision": {"tier": "PAGE", "reason": "x", "fingerprint": fingerprint},
        "meta": {"source": "collectorinfra"},
    }


def _store(tmp_path, **kw) -> JobStore:
    return JobStore(make_settings(), audit_path=tmp_path / "audit.jsonl", **kw)


# ── submit / dispatch(스텁) ──────────────────────────────────────


def test_submit_accepted_then_stub(tmp_path):
    store = _store(tmp_path)
    res = store.submit(valid_payload())
    assert res["status"] == "accepted"
    jid = res["investigation_id"]

    got = store.get(jid)
    # 스텁 executor가 done이 아니라 명시적 stub 상태로 확정한다(침묵 금지).
    assert got["status"] == "stub"
    assert "조사 미실행" in got["verdict"]
    assert got["briefing"]["stub"] is True
    assert "LLM 키 부재" in got["briefing"]["message"]


def test_stub_executor_explicit_no_key_vs_key():
    no_key = make_stub_executor(make_settings(gemini_api_key=None))
    with_key = make_stub_executor(make_settings(gemini_api_key="k"))

    class _J:
        status = "running"
        verdict = None
        briefing = None
        tool_calls_summary = None
        tokens = None
        cost = None
        error = None

    j1, j2 = _J(), _J()
    no_key(j1)
    with_key(j2)
    assert j1.status == "stub" and "LLM 키 부재" in j1.verdict
    # 키가 있어도 실 dispatcher는 2-D 소관 → 여전히 스텁이되 사유를 다르게 명시한다.
    assert j2.status == "stub" and "dispatcher 미배선" in j2.verdict


# ── dedup ────────────────────────────────────────────────────────


def test_duplicate_same_fingerprint(tmp_path):
    store = _store(tmp_path)
    first = store.submit(valid_payload(fingerprint="dup"))
    second = store.submit(valid_payload(fingerprint="dup"))
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"
    assert second["investigation_id"] == first["investigation_id"]


def test_no_fingerprint_not_deduped(tmp_path):
    store = _store(tmp_path)
    p = valid_payload()
    p["decision"].pop("fingerprint")
    a = store.submit(p)
    b = store.submit(p)
    assert a["status"] == "accepted"
    assert b["status"] == "accepted"
    assert a["investigation_id"] != b["investigation_id"]


# ── sweep (값 bound + 키 만료) ───────────────────────────────────


def test_sweep_ttl_expiry(tmp_path):
    now = [1000.0]
    store = _store(tmp_path, ttl_seconds=100.0, clock=lambda: now[0])
    jid = store.submit(valid_payload())["investigation_id"]

    now[0] = 1050.0  # ttl 내
    assert store.get(jid)["status"] == "stub"

    now[0] = 1200.0  # ttl 초과 → sweep으로 제거
    assert store.get(jid)["status"] == "not_found"


def test_sweep_max_jobs_bound(tmp_path):
    now = [0.0]
    store = _store(tmp_path, max_jobs=3, ttl_seconds=1e9, clock=lambda: now[0])
    ids = []
    for i in range(5):
        now[0] = float(i)
        ids.append(store.submit(valid_payload(fingerprint=f"fp{i}"))["investigation_id"])

    store.list()  # sweep 트리거
    assert len(store._jobs) <= 3
    # 가장 오래된 잡은 축출됐다.
    assert store.get(ids[0])["status"] == "not_found"


# ── 재기동 복구 ──────────────────────────────────────────────────


def _hang_executor(job):
    # 실 async dispatcher의 in-flight를 흉내 — running으로 남긴다(terminal 미확정).
    job.status = "running"


def test_restart_running_to_failed(tmp_path):
    audit = tmp_path / "audit.jsonl"
    # 1) 크래시 전 프로세스: running 잡을 남긴다.
    store1 = JobStore(make_settings(), executor=_hang_executor, audit_path=audit)
    jid = store1.submit(valid_payload(fingerprint="crash"))["investigation_id"]
    assert store1.get(jid)["status"] == "running"

    # 2) 재기동: 새 프로세스(빈 in-memory)에서 감사 JSONL로 복구.
    store2 = JobStore(make_settings(), audit_path=audit)
    recovered = store2.recover_on_start()
    assert recovered == 1
    got = store2.get(jid)
    assert got["status"] == "failed"
    assert got["reason"] == "restart"

    # 3) 복구 사실이 감사 JSONL에 남는다(침묵 유실 금지).
    events = [json.loads(x)["event"] for x in audit.read_text().splitlines() if x.strip()]
    assert "restart_failed" in events


def test_restart_ignores_terminal(tmp_path):
    audit = tmp_path / "audit.jsonl"
    store1 = JobStore(make_settings(), audit_path=audit)  # 기본 스텁 → terminal(stub)
    store1.submit(valid_payload(fingerprint="ok"))

    store2 = JobStore(make_settings(), audit_path=audit)
    assert store2.recover_on_start() == 0  # terminal 잡은 복구 대상 아님


# ── 감사 JSONL ──────────────────────────────────────────────────


def test_audit_jsonl_records_lifecycle(tmp_path):
    audit = tmp_path / "audit.jsonl"
    store = JobStore(make_settings(), audit_path=audit)
    store.submit(valid_payload())
    records = [json.loads(x) for x in audit.read_text().splitlines() if x.strip()]
    events = {r["event"] for r in records}
    assert {"accepted", "running", "terminal"} <= events
    assert all("ts" in r for r in records)


# ── 진단(pull) / 조회 ────────────────────────────────────────────


def test_submit_diagnosis(tmp_path):
    store = _store(tmp_path)
    res = store.submit_diagnosis("web-01 메모리 원인 분석", server_name="web-01")
    assert res["status"] == "accepted"
    got = store.get(res["investigation_id"])
    assert got["kind"] == "diagnosis"
    assert got["status"] == "stub"


def test_submit_diagnosis_empty_rejected(tmp_path):
    store = _store(tmp_path)
    res = store.submit_diagnosis("   ")
    assert res["status"] == "rejected"


def test_get_unknown_not_found(tmp_path):
    store = _store(tmp_path)
    assert store.get("nope")["status"] == "not_found"


def test_list_ordering_and_limit(tmp_path):
    now = [0.0]
    store = _store(tmp_path, clock=lambda: now[0])
    ids = []
    for i in range(3):
        now[0] = float(i)
        ids.append(store.submit(valid_payload(fingerprint=f"fp{i}"))["investigation_id"])
    now[0] = 100.0
    listing = store.list(limit=2)
    assert listing["count"] == 2
    returned = [j["investigation_id"] for j in listing["investigations"]]
    # updated_at 내림차순 → 최신(ids[2], ids[1])
    assert returned == [ids[2], ids[1]]


def test_status_sets_consistent():
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
