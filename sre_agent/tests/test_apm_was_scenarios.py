"""목업 WAS 시나리오 6종 — 게이트웨이 도구 출력 → 승격 신호 kind · 판정 · 권고 후보 (plans/87 J3 · LLM 0회).

픽스처는 `spec/SPEC-apm-gateway.md` §3.1(정상 반환)·§4(`was_signals` 항목)를 **복제**한 것이다 — `apm_gateway`를
import하지 않는다(D-274 ③ · R-21). 계약이 바뀌면 이 파일의 `_ok`·`_sig`를 SPEC에 맞춰 함께 고친다.

검증: ① `was_signals_from_outputs`가 kind·category를 그대로 옮긴다(WAS 규칙 재구현 없음 — D-274 ⑤)
② `judge(extra_signals=…)`가 escalate-only로 합친다 ③ 권고 후보가 결정적으로 나온다(검증·롤백 포함)
④ dispatcher 배선(`apm_signatures_enabled`) on/off — off면 판정·권고 불변.
"""

from __future__ import annotations

import json
import time

import pytest

from sre_agent.application.briefing_builder import build_briefing
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord
from sre_agent.domain.remediation import RISK_HIGH, recommend, recommend_lines
from sre_agent.domain.severity_signatures import (
    SIGNATURES,
    Signal,
    apm_payloads,
    judge,
    was_signals_from_outputs,
)
from sre_agent.settings import AgentSettings

# ── SPEC-apm-gateway §3.1·§4 복제 ────────────────────────────────────────


def _ok(tool: str, rows: list[dict], was_signals: list[dict], *, window: bool = True,
        confidence: str = "high", limits: tuple[str, ...] = (), summary: dict | None = None) -> dict:
    out: dict = {
        "rows": rows, "row_count": len(rows), "queried_at": "2026-09-29T10:00:00+09:00",
        "source_kind": "apm_api", "source": "jennifer", "tool": tool,
        "instance_resolution": {"matched": True, "confidence": confidence, "reason": "hostName",
                                "instances": [1001]},
        "was_signals": was_signals, "limits": list(limits),
    }
    if window:
        out["window"] = {"start": "2026-09-29T09:30:00+09:00", "end": "2026-09-29T10:00:00+09:00",
                         "minutes": 30}
    if summary is not None:
        out["summary"] = summary
    return out


def _sig(kind: str, level: str, category: str, label: str, evidence: str, tool: str,
         instance_id: int = 1001) -> dict:
    return {"kind": kind, "level": level, "category": category, "label": label,
            "evidence": evidence, "instance_id": instance_id, "source_tool": tool}


def _tx(response_ms: int, sql_ms: int, fetch_ms: int, external_ms: int, txid: str) -> dict:
    return {"application": "/order/submit", "response_time_ms": response_ms, "cpu_ms": 40,
            "sql_ms": sql_ms, "fetch_ms": fetch_ms, "external_ms": external_ms, "network_ms": 0,
            "error_type": "", "end_time_ms": 1790000000000,
            "profile_ref": {"domain_id": 1000, "txid": txid, "time_ms": 1790000000000}}


QUEUING = _ok("apm_app_health", [{
    "instance_id": 1001, "response_time_avg_ms": 5200.0, "tps": 3.1, "active_services": 180,
    "bad_response_active_services": 150, "reject_rate": 0.12, "concurrent_users": 900,
    "window": {"calls": 400, "errors": 8, "error_rate": 0.02, "response_time_p50_ms": 4100,
               "response_time_p95_ms": 9800, "response_time_max_ms": 15000},
}], [_sig("was_service_queuing", "CRITICAL", "strong", "WAS 서비스 큐잉(유입 대기·PLC 거절)",
          "reject_rate=0.12 · 이벤트 SERVICE_QUEUING", "apm_app_health")])

DB_POOL = _ok("apm_resource_pool", [{
    "instance_id": 1001, "db_pool_active": 49, "db_pool_idle_avg": 0.5, "db_pool_configured_avg": 50,
    "db_pool_usage_ratio": 0.98, "thread_current": 200, "active_services": 60,
    "active_by_running_mode": {"DB_CONNECTING": 44}, "active_by_datasource": {"jdbc/orderDS": 49},
}], [_sig("was_db_pool_exhaustion", "CRITICAL", "strong", "DB 커넥션 풀 고갈",
          "db_pool_usage_ratio=0.98 (49/50)", "apm_resource_pool")], window=False)

GC_STALL = _ok("apm_runtime_health", [{
    "instance_id": 1001, "heap_used_mb": 700.0, "heap_committed_mb": 1000.0, "heap_usage_ratio": 0.7,
    "non_heap_used_mb": 180.0, "gc_time_usage_pct": 14.2, "process_cpu_pct": 71.0,
    "process_memory_mb": 1500.0, "thread_current": 210,
    "trend": {"heap_used_mb": [], "heap_committed_mb": [], "gc_time_usage_pct": [
        {"time_ms": 1789999700000, "value": 12.0}, {"time_ms": 1790000000000, "value": 14.2}]},
}], [_sig("was_gc_stall", "WARNING", "medium", "GC 지연(GC 시간 비중 과다)",
          "gc_time_usage_pct=14.2", "apm_runtime_health")])

HEAP = _ok("apm_runtime_health", [{
    "instance_id": 1001, "heap_used_mb": 930.0, "heap_committed_mb": 1000.0, "heap_usage_ratio": 0.93,
    "non_heap_used_mb": 180.0, "gc_time_usage_pct": 4.0, "process_cpu_pct": 35.0,
    "process_memory_mb": 1600.0, "thread_current": 120,
    "trend": {"heap_used_mb": [], "heap_committed_mb": [], "gc_time_usage_pct": []},
}], [_sig("was_heap_pressure", "WARNING", "medium", "힙 메모리 압박",
          "heap_used/committed=0.93 (930.0MB/1000.0MB)", "apm_runtime_health")])

SLOW_SQL = _ok("apm_slow_transactions",
               [_tx(8000, 5200, 600, 100, "tx-1"), _tx(7000, 4600, 500, 80, "tx-2"),
                _tx(6500, 4100, 400, 90, "tx-3")],
               [_sig("was_slow_sql", "WARNING", "medium", "SQL 지연(SQL·Fetch 비중 과다)",
                     "(sql+fetch)/response=0.72 (n=3)", "apm_slow_transactions")],
               summary={"calls": 300, "errors": 3, "error_rate": 0.01, "p50": 3000, "p95": 7800,
                        "sql_fetch_share": 0.72, "external_share": 0.01},
               limits=("[한계] 1분 창 분할 상한(10분) 도달 — 앞 20분은 조회하지 않았다",))

EXTERNAL = _ok("apm_slow_transactions",
               [_tx(9000, 300, 50, 6400, "tx-4"), _tx(8200, 250, 40, 5600, "tx-5"),
                _tx(7600, 200, 30, 5100, "tx-6")],
               [_sig("was_external_call_delay", "WARNING", "medium", "외부 호출 지연",
                     "external/response=0.68 (n=3)", "apm_slow_transactions")],
               summary={"calls": 250, "errors": 2, "error_rate": 0.01, "p50": 4200, "p95": 8800,
                        "sql_fetch_share": 0.04, "external_share": 0.68})

# (id, 픽스처, 승격 kind, category, gate→기대 레벨, 첫 권고(가역성 1순위) 문구 일부)
SCENARIOS = [
    ("queuing", QUEUING, "was_service_queuing", "strong", (2, "심각"), "제니퍼 콘솔에서 인터럽트"),
    ("db_pool", DB_POOL, "was_db_pool_exhaustion", "strong", (2, "심각"), "누수 의심 트랜잭션"),
    ("gc_stall", GC_STALL, "was_gc_stall", "medium", (1, "경고"), "GC 시간 비중 추이 확인"),
    ("heap", HEAP, "was_heap_pressure", "medium", (1, "경고"), "힙 덤프 채취"),
    ("slow_sql", SLOW_SQL, "was_slow_sql", "medium", (1, "경고"), "상위 SQL을 DBA 검토로 전달"),
    ("external", EXTERNAL, "was_external_call_delay", "medium", (1, "경고"), "의존(외부) 서비스 조사로 전이"),
]
_IDS = [s[0] for s in SCENARIOS]


# ── ① 승격 — kind·category를 옮길 뿐(판정 없음) ────────────────────────────


@pytest.mark.parametrize("_id,fixture,kind,category,_lvl,_first", SCENARIOS, ids=_IDS)
def test_was_signals_promoted_verbatim(_id, fixture, kind, category, _lvl, _first):
    signals = was_signals_from_outputs([json.dumps(fixture, ensure_ascii=False)])
    assert [s.name for s in signals] == [kind]
    s = signals[0]
    assert s.category == category and s.source == "apm"
    item = fixture["was_signals"][0]
    assert s.label == item["label"]
    assert s.evidence.startswith(item["evidence"])
    assert item["source_tool"] in s.evidence and "instance 1001" in s.evidence


@pytest.mark.parametrize("_id,fixture,kind,category,lvl,_first", SCENARIOS, ids=_IDS)
def test_judge_merges_was_signal_escalate_only(_id, fixture, kind, category, lvl, _first):
    text = json.dumps(fixture, ensure_ascii=False)
    gate, expected = lvl
    v = judge(gate, [text], remote=True, extra_signals=was_signals_from_outputs([text]))
    assert v.level == expected and v.escalate is True
    assert v.confidence == ("high" if category == "strong" else "medium")
    assert [s.name for s in v.signals] == [kind]
    assert v.evidence_insufficient is False
    # 승격 없이(=off 경로)는 OS 시그니처만 본다 — 게이트웨이 JSON은 OS 패턴에 걸리지 않는다.
    off = judge(gate, [text], remote=True)
    assert off.signals == [] and off.escalate is False


@pytest.mark.parametrize("_id,fixture,kind,category,_lvl,first", SCENARIOS, ids=_IDS)
def test_remediation_candidates_are_deterministic(_id, fixture, kind, category, _lvl, first):
    signals = was_signals_from_outputs([json.dumps(fixture, ensure_ascii=False)])
    out = recommend(signals)
    assert out, f"{kind} 권고 후보 없음"
    assert first in out[0].action
    for r in out:
        assert r.verification and r.rollback          # WAS 항목은 검증·롤백을 담는다(§5.4(c))
        line = r.to_line()
        assert f" · 검증: {r.verification}" in line and f" · 롤백: {r.rollback}" in line
        assert fixture["was_signals"][0]["label"] in r.rationale
        if r.risk == RISK_HIGH and category != "strong":
            assert r.review_only                      # 고위험 × 저신뢰 → 검토 필요(종전 규칙 그대로)
    assert recommend_lines(signals) == [r.to_line() for r in out]


def test_queuing_candidates_follow_reversibility_order():
    out = recommend(was_signals_from_outputs([json.dumps(QUEUING, ensure_ascii=False)]))
    assert [r.risk for r in out] == ["medium", "medium", "high"]
    assert "인스턴스 재기동" in out[-1].action and out[-1].review_only is False  # strong → 정식 권고


# ── 승격 입력 방어 ─────────────────────────────────────────────────────────


def test_same_kind_collapses_and_strong_wins():
    medium = _ok("apm_runtime_health", [], [_sig("was_heap_pressure", "WARNING", "medium", "힙 메모리 압박",
                                                  "heap_usage_ratio=0.91", "apm_runtime_health")])
    strong = _ok("apm_events", [], [_sig("was_heap_pressure", "CRITICAL", "strong", "힙 메모리 압박",
                                         "이벤트 OUTOFMEMORY", "apm_events")])
    signals = was_signals_from_outputs([json.dumps(medium), json.dumps(strong)])
    assert len(signals) == 1 and signals[0].category == "strong" and "OUTOFMEMORY" in signals[0].evidence


def test_non_gateway_and_malformed_outputs_are_ignored():
    polestar = json.dumps({"rows": [], "source_kind": "polestar_process_realtime",
                           "was_signals": [_sig("was_gc_stall", "WARNING", "medium", "x", "y", "t")]})
    broken = '{"source_kind": "apm_api", "was_signals": ['              # 잘린 JSON
    bad_cat = json.dumps(_ok("apm_app_health", [], [
        _sig("was_error_burst", "CRITICAL", "weird", "오류 급증", "e", "apm_app_health"),
        {"kind": "", "category": "strong"}, "not-a-dict"]))
    error = json.dumps({"error": "source_unavailable", "reason": "Domain is not connected",
                        "source_kind": "apm_api", "source": "jennifer", "tool": "apm_app_health"})
    texts = ["plain text apm_api", polestar, broken, bad_cat, error, ""]
    assert was_signals_from_outputs(texts) == []
    assert [p.get("tool") for p in apm_payloads(texts)] == ["apm_app_health", "apm_app_health"]


def test_extra_signals_default_keeps_judge_identical():
    oom = "kernel: Out of memory: Killed process 12345 (java)"
    assert judge(2, [oom]) == judge(2, [oom], extra_signals=())
    assert judge(0, []) == judge(0, [], extra_signals=())


# ── 권고 표 — OS 항목 렌더 바이트 동일 · WAS kind 전수 ─────────────────────


def _pre_j3_line(r) -> str:
    """J3 이전 `Remediation.to_line()` 형식의 재현."""
    head = "[검토 필요] " if r.review_only else ""
    return f"{head}{r.action} (위험도 {r.risk}·신뢰도 {r.confidence}) — 근거: {r.rationale}"


@pytest.mark.parametrize("category", ["strong", "medium"])
def test_os_remediation_render_is_byte_identical(category):
    for sig in SIGNATURES:
        out = recommend([Signal(sig.name, category, sig.source, sig.label, "발췌")])
        assert out, sig.name
        for r in out:
            assert r.verification == "" and r.rollback == ""
            assert r.to_line() == _pre_j3_line(r)


def test_os_remediation_snapshot():
    lines = recommend_lines([Signal("oom_kill", "strong", "log", "메모리 고갈(OOM Killer)",
                                    "Out of memory: Killed process 1")])
    assert lines == [
        "메모리 상위 프로세스 누수 점검(추이가 지속 증가인지 확인) (위험도 low·신뢰도 high) — "
        "근거: 메모리 고갈(OOM Killer) — Out of memory: Killed process 1",
        "OOM 종료된 프로세스의 힙·워커 수 설정 검토 후 재기동 (위험도 high·신뢰도 high) — "
        "근거: 메모리 고갈(OOM Killer) — Out of memory: Killed process 1",
    ]


def test_every_gateway_kind_has_candidates():
    """SPEC-apm-gateway §4의 kind 8종 전부 권고 후보가 있다(계획서 표 6 + 보수적 2)."""
    kinds = ("was_service_queuing", "was_thread_pool_exhaustion", "was_db_pool_exhaustion",
             "was_gc_stall", "was_heap_pressure", "was_slow_sql", "was_external_call_delay",
             "was_error_burst")
    for kind in kinds:
        out = recommend([Signal(kind, "strong", "apm", kind, "e")])
        assert out and all(r.verification and r.rollback for r in out), kind
    conservative = [r.risk for k in ("was_thread_pool_exhaustion", "was_gc_stall")
                    for r in recommend([Signal(k, "strong", "apm", k, "e")])]
    assert set(conservative) == {"low"}


# ── ④ dispatcher 배선 (LLM 0 — fake diagnose_fn) ─────────────────────────


def _settings(**kw) -> AgentSettings:
    base = {"model": "test/model", "api_key": None, "gemini_api_key": "k", "max_steps": 3,
            "investigation_dedup_ttl_seconds": None, "investigation_hourly_budget": None,
            "severity_judge_enabled": True, "remediation_recommender_enabled": True}
    return AgentSettings(_env_file=None, **{**base, **kw})


def _job(severity: int) -> InvestigationJob:
    now = time.time()
    return InvestigationJob(
        investigation_id=f"id-{now}", kind="alarm", status="running", created_at=now, updated_at=now,
        fingerprint=None,
        payload={"event": {"serverName": "was-01", "hostname": "was-01", "severity": severity,
                           "resourceType": "apm.Instance", "alarmName": "WARNING_JVM_HEAP_MEM_HIGH"},
                 "decision": {"tier": "PAGE"}},
    )


def _diagnose_with(fixture: dict):
    text = json.dumps(fixture, ensure_ascii=False)

    def _fn(job):
        return DiagnosisResult(
            answer=f"원인 ← {fixture['tool']}",
            tool_calls=[fixture["tool"]],
            tool_outputs=[ToolCallRecord(fixture["tool"], "apm: 호출", "success", text,
                                         params={"hostname": "was-01"})],
        )

    return _fn


def _run(settings: AgentSettings, fixture: dict, severity: int) -> InvestigationJob:
    disp = InvestigationDispatcher(settings, diagnose_fn=_diagnose_with(fixture), briefing_fn=build_briefing)
    job = _job(severity)
    disp(job)
    disp.wait_workers(5)
    assert job.status == "done"
    return job


@pytest.mark.parametrize("_id,fixture,kind,category,lvl,first", SCENARIOS, ids=_IDS)
def test_dispatcher_promotes_when_enabled(_id, fixture, kind, category, lvl, first):
    gate, expected = lvl
    job = _run(_settings(apm_signatures_enabled=True), fixture, gate)
    assert job.briefing["severity"]["signals"] == [kind]
    assert job.briefing["severity"]["level"] == expected
    assert job.verdict.startswith(expected)
    assert any(first in item for item in job.briefing["recommendation"]["items"])
    assert fixture["was_signals"][0]["label"] in job.briefing["bottleneck"]


@pytest.mark.parametrize("_id,fixture,kind,category,lvl,first", SCENARIOS, ids=_IDS)
def test_dispatcher_off_keeps_verdict_and_briefing(_id, fixture, kind, category, lvl, first):
    """★ `apm_signatures_enabled` off(기본) — 판정·권고·브리핑이 종전 호출과 바이트 동일."""
    gate, _ = lvl
    settings = _settings()
    assert settings.apm_signatures_enabled is False
    job = _run(settings, fixture, gate)
    text = json.dumps(fixture, ensure_ascii=False)
    old_verdict = judge(gate, [text], remote=False)
    assert job.briefing == build_briefing(
        answer=f"원인 ← {fixture['tool']}", verdict=old_verdict, tool_names=[fixture["tool"]],
        gate_tier="PAGE", remediation=None,
    )
    assert job.briefing["severity"]["signals"] == []
