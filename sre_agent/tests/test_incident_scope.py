"""사건 좌표계 — 잡 필드·push alarmTime·pull 인자·형식 거부 (plans/50 A′-5 · SPEC-incident-scope)."""

import asyncio
import json

import pytest

from sre_agent.application.investigation_jobs import CONTRACT_VERSION, JobStore
from sre_agent.domain.incident_scope import (
    DEFAULT_LOOKBACK_MINUTES,
    MAX_LOOKBACK_MINUTES,
    alarm_time_to_iso,
    normalize_lookback,
    normalize_reference_time,
)
from sre_agent.interface.mcp_service import create_service
from sre_agent.settings import AgentSettings


def _settings() -> AgentSettings:
    return AgentSettings(_env_file=None, model="test/model", api_key=None, gemini_api_key=None,
                         service_bearer_token=None)


def _store(tmp_path) -> JobStore:
    return JobStore(_settings(), audit_path=tmp_path / "audit.jsonl")


# ── 순수 함수 ─────────────────────────────────────────────────


def test_alarm_time_to_iso():
    assert alarm_time_to_iso("20260901140530") == "2026-09-01T14:05:30"


@pytest.mark.parametrize("bad", [None, "", "2026-09-01", "2026090114", "abcdefghijklmn", "20261301000000", 20260901140530])
def test_alarm_time_to_iso_rejects(bad):
    assert alarm_time_to_iso(bad) is None


def test_normalize_reference_time():
    assert normalize_reference_time("2026-09-01 14:00") == "2026-09-01T14:00:00"
    with pytest.raises(ValueError, match="ISO 8601"):
        normalize_reference_time("어제 14시")


def test_normalize_lookback_bounds():
    assert normalize_lookback(None) == DEFAULT_LOOKBACK_MINUTES
    assert normalize_lookback("x") == DEFAULT_LOOKBACK_MINUTES
    assert normalize_lookback(0) == 1
    assert normalize_lookback(10**9) == MAX_LOOKBACK_MINUTES
    assert normalize_lookback("90") == 90


# ── push: alarmTime → reference_time ─────────────────────────────


def test_push_job_gets_reference_time_from_alarm_time(tmp_path):
    store = _store(tmp_path)
    res = store.submit({
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "h", "severity": 2, "alarmTime": "20260901140000"},
        "decision": {"fingerprint": "fp-1"},
    })
    got = store.get(res["investigation_id"])
    assert got["reference_time"] == "2026-09-01T14:00:00"
    assert got["lookback_minutes"] == DEFAULT_LOOKBACK_MINUTES


def test_push_job_without_alarm_time_has_no_anchor(tmp_path):
    store = _store(tmp_path)
    res = store.submit({
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "h", "severity": 2},
        "decision": {"fingerprint": "fp-2"},
    })
    got = store.get(res["investigation_id"])
    assert got["reference_time"] is None and got["lookback_minutes"] is None


# ── pull: sre_diagnose 인자 ───────────────────────────────────────


def test_submit_diagnosis_keeps_scope(tmp_path):
    store = _store(tmp_path)
    res = store.submit_diagnosis("어제 14시 원인", server_name="web-01",
                                 reference_time="2026-09-01T15:00:00", lookback_minutes=180)
    got = store.get(res["investigation_id"])
    assert got["reference_time"] == "2026-09-01T15:00:00" and got["lookback_minutes"] == 180


def test_submit_diagnosis_without_scope_unchanged(tmp_path):
    store = _store(tmp_path)
    res = store.submit_diagnosis("원인 분석", server_name="web-01")
    got = store.get(res["investigation_id"])
    assert res["status"] == "accepted"
    assert got["reference_time"] is None and got["lookback_minutes"] is None


def test_submit_diagnosis_rejects_bad_reference_time(tmp_path):
    store = _store(tmp_path)
    res = store.submit_diagnosis("원인", reference_time="어제 14시")
    assert res["status"] == "rejected" and "ISO 8601" in res["reason"]


def test_sre_diagnose_tool_accepts_scope(tmp_path):
    settings = _settings()
    store = JobStore(settings, audit_path=tmp_path / "audit.jsonl")
    mcp = create_service(settings, job_store=store)
    result = asyncio.run(mcp.call_tool("sre_diagnose", {
        "question": "q", "server_name": "web-01",
        "reference_time": "2026-09-01T15:00:00", "lookback_minutes": 180,
    }))
    content = result[0] if isinstance(result, tuple) else result
    res = json.loads(content[0].text)
    assert res["status"] == "accepted"
    assert store.get(res["investigation_id"])["reference_time"] == "2026-09-01T15:00:00"
