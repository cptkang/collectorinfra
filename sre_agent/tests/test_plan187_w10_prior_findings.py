"""plans/187 W10 — `sre_diagnose`의 선택 인자 `prior_findings` (D-341 ⑭ · G-21).

- 인자 없음 = 종전 지침·payload 비트 동일(구버전 호출자 fail-open)
- 있으면 「이미 수집된 판정」 블록을 결정적으로 렌더
  (사건창 · 판정 · 판독 · 단계 · 재조회 불필요 도구)
- 서술 키(opinion·synthesis 등)·문장 값은 화이트리스트 재검증으로 버린다 · 크기 상한
- 알람 잡(kind=alarm)에는 붙지 않는다
실 LLM은 부르지 않는다(스텁 executor).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sre_agent.application.investigation_guidance import (
    PRIOR_FINDINGS_HEADER,
    PRIOR_FINDINGS_MAX_BYTES,
    build_guidance,
    clean_prior_findings,
    prior_findings_note,
)
from sre_agent.application.investigation_jobs import JobStore
from sre_agent.interface.mcp_service import create_service
from sre_agent.settings import AgentSettings

OPINION = "LLM 소견: DB 인덱스 문제로 보인다"


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "test/model")
    kw.setdefault("gemini_api_key", None)
    return AgentSettings(**kw)


def _job(payload=None, kind: str = "diagnosis", **kw) -> SimpleNamespace:  # noqa: ANN001
    base = {"kind": kind, "question": "q", "payload": payload,
            "reference_time": None, "lookback_minutes": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _pf() -> dict:
    return {
        "version": 1,
        "source": "playbook",
        "parent_investigation_id": "inv-1",
        "playbook_id": "was.latency",
        "window": {"reference_time": "2026-10-19T10:10:00", "lookback_minutes": 40},
        "verdict": {"level": "critical", "confidence": "medium", "note": OPINION},
        "readings": {"dominant": "sql", "fs_eta": None, "story": OPINION, "list": [1]},
        "sections": [
            {"step": "S1", "status": "ok", "verdict": "degraded", "verdict_source": "apm_gateway",
             "tools": ["apm_app_health", "apm_period_compare"], "opinion": OPINION},
            {"step": "S3", "status": "ok", "verdict": OPINION,
             "readings": {"dominant": "sql"}, "tools": ["apm_slow_transactions", "bad tool"]},
            {"title": "단계 없음"},
        ],
        "tools": ["apm_app_health", "apm_period_compare", "apm_slow_transactions"],
        "synthesis": {"hypotheses": [OPINION]},
        "opinion": OPINION,
    }


# ── 인자 없음 = 비트 동일 ───────────────────────────────────────────────
def test_guidance_unchanged_without_prior_findings():
    settings = _settings()
    for payload in (None, {"server_name": "web-01", "hostname": None, "db_id": None,
                           "target_state": None}):
        base = build_guidance(settings, _job(payload))
        assert base is not None and PRIOR_FINDINGS_HEADER not in base
    assert build_guidance(settings, _job(None), remote=False) is None


def test_submit_without_prior_findings_keeps_payload_shape(tmp_path):
    store = JobStore(_settings(), audit_path=tmp_path / "a.jsonl")
    res = store.submit_diagnosis("web-01 원인", server_name="web-01")
    job = store._jobs[res["investigation_id"]]
    assert job.payload == {"server_name": "web-01", "hostname": None, "db_id": None,
                           "target_state": None}


# ── 렌더 ───────────────────────────────────────────────────────────────
def test_guidance_renders_prior_findings_block():
    g = build_guidance(_settings(), _job({"prior_findings": _pf()}), remote=False)
    assert g is not None and g.startswith(PRIOR_FINDINGS_HEADER)
    assert "부모 조사 inv-1 · 플레이북 was.latency" in g
    assert "사건창: 기준 2026-10-19T10:10:00 · 이전 40분" in g
    assert "판정: level=critical · confidence=medium" in g
    assert "판독: dominant=sql · fs_eta=null" in g
    assert "단계 S1 · status=ok · verdict=degraded · verdict_source=apm_gateway" in g
    assert "apm_app_health · apm_period_compare · apm_slow_transactions" in g
    assert OPINION not in g and "bad tool" not in g
    # 결정적 — 같은 입력이면 같은 문자열.
    assert g == build_guidance(_settings(), _job({"prior_findings": _pf()}), remote=False)


def test_block_follows_correlation_and_precedes_extra():
    g = build_guidance(
        _settings(investigation_guidance_extra="운영자 지침"),
        _job({"prior_findings": _pf()}, reference_time="2026-10-19T10:10:00", lookback_minutes=40),
    )
    assert g is not None
    assert g.index("사건") < g.index(PRIOR_FINDINGS_HEADER) < g.index("운영자 지침")


def test_alarm_job_ignores_prior_findings_key():
    g = build_guidance(_settings(), _job({"prior_findings": _pf()}, kind="alarm"), remote=False)
    assert g is None or PRIOR_FINDINGS_HEADER not in g


# ── 화이트리스트 재검증 ──────────────────────────────────────────────────
def test_clean_drops_narrative_keys_and_sentence_values():
    cleaned = clean_prior_findings(_pf())
    assert cleaned is not None
    dumped = json.dumps(cleaned, ensure_ascii=False)
    assert OPINION not in dumped
    for key in ("opinion", "synthesis", "note", "story", "title"):
        assert f'"{key}"' not in dumped
    assert cleaned["verdict"] == {"level": "critical", "confidence": "medium"}
    assert cleaned["readings"] == {"dominant": "sql", "fs_eta": None}
    assert [s["step"] for s in cleaned["sections"]] == ["S1", "S3"]
    assert "verdict" not in cleaned["sections"][1]
    assert cleaned["sections"][1]["tools"] == ["apm_slow_transactions"]


def test_clean_rejects_empty_or_non_dict():
    assert clean_prior_findings(None) is None
    assert clean_prior_findings("문장") is None
    assert clean_prior_findings({"version": 1, "source": "briefing"}) is None
    assert prior_findings_note({"opinion": OPINION}) is None


def test_clean_caps_size():
    big = {"version": 1, "sections": [
        {"step": f"S{i}", "status": "ok", "tools": [f"tool_{i}_{j}" for j in range(40)]}
        for i in range(20)
    ]}
    cleaned = clean_prior_findings(big)
    assert cleaned is not None
    assert len(json.dumps(cleaned, ensure_ascii=False).encode("utf-8")) <= PRIOR_FINDINGS_MAX_BYTES
    assert 0 < len(cleaned["sections"]) < 20


# ── 잡 · MCP 도구 왕복 ───────────────────────────────────────────────────
def test_submit_keeps_cleaned_prior_findings(tmp_path):
    store = JobStore(_settings(), audit_path=tmp_path / "a.jsonl")
    res = store.submit_diagnosis("q", server_name="web-01", prior_findings=_pf())
    job = store._jobs[res["investigation_id"]]
    assert job.payload["prior_findings"] == clean_prior_findings(_pf())
    assert OPINION not in json.dumps(job.payload, ensure_ascii=False)


def test_sre_diagnose_tool_accepts_prior_findings(tmp_path):
    settings = _settings()
    store = JobStore(settings, audit_path=tmp_path / "a.jsonl")
    mcp = create_service(settings, job_store=store)
    result = asyncio.run(mcp.call_tool(
        "sre_diagnose", {"question": "q", "server_name": "web-01", "prior_findings": _pf()}
    ))
    content = result[0] if isinstance(result, tuple) else result
    res = json.loads(content[0].text)
    assert res["status"] == "accepted"
    assert store._jobs[res["investigation_id"]].payload["prior_findings"]["playbook_id"] == (
        "was.latency"
    )
