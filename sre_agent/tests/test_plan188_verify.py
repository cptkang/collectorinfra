"""plans/188 독립 검증(verifier) — 보안(토큰·userinfo) · 동시성 · A안 후처리 · 지침 필터.

실 LLM 0. 401 재현은 sre_agent 자신의 `StaticBearerAuthMiddleware`를 씌운 로컬 uvicorn
(127.0.0.1 · 임의 포트)으로 한다. `xfail(strict=True)`는 보고한 결함을 고정한 것이다
— 고쳐지면 XPASS로 실패해 표지를 걷게 한다.
"""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sre_agent.application.briefing_builder import UNRESOLVED_PREFIX, build_briefing
from sre_agent.application.evidence_prefetch import EvidenceScope, prefetch_and_correlate
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_guidance import (
    TOOL_NAME_RE,
    build_guidance,
    filter_tool_mentions,
)
from sre_agent.application.investigation_jobs import InvestigationJob
from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult, tools_unavailable_result
from sre_agent.infrastructure import mcp_tool_client as mc
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

_TOKEN = "s3cret-polestar-token-187"
_WRONG = "wrong-token-xyz-187"
_CONNECT_ERR = (
    "Failed to load mcp server polestar: All connection attempts failed. "
    "If the server is still starting up, Holmes will retry automatically"
)


def _settings(**kw) -> AgentSettings:
    # 검증 대상 필드를 명시해 .env 누수를 막는다.
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "openai/test-model")
    kw.setdefault("api_key", "x")
    kw.setdefault("gemini_api_key", None)
    kw.setdefault("service_bearer_token", None)
    kw.setdefault("evidence_correlation_enabled", False)
    kw.setdefault("apm_mcp_url", "")
    kw.setdefault("apm_guidance_enabled", False)
    kw.setdefault("polestar_mcp_token", None)
    return AgentSettings(**kw)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _job(**kw) -> SimpleNamespace:
    base = {
        "kind": "alarm",
        "question": None,
        "investigation_id": "inv-187v",
        "payload": {"event": {"serverName": "web-01", "resourceType": "cpu", "alarmName": "CPU"}},
        "reference_time": "2026-10-19T10:00:00",
        "lookback_minutes": 120,
    }
    base.update(kw)
    return SimpleNamespace(**base)


# ── 401 서버(정적 Bearer 미들웨어) ──────────────────────────────────


@pytest.fixture(scope="module")
def auth_server():
    import uvicorn

    async def sse(_request):  # 인증을 통과하면 닿는다(여기선 쓰지 않음)
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/sse", sse)])
    app.add_middleware(mcp_service.StaticBearerAuthMiddleware, token=_TOKEN)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off")
    )
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    yield port
    server.should_exit = True
    th.join(5)


def test_prefetch_401_names_setting_without_token_values(auth_server, caplog):
    url = f"http://127.0.0.1:{auth_server}/sse"
    with caplog.at_level(logging.DEBUG):
        out = mc.run_tool_batch(url, {"Authorization": f"Bearer {_WRONG}"}, [("a", {}), ("b", {})],
                                timeout_seconds=5)
    errs = {json.loads(o)["error"] for o in out}
    assert len(errs) == 1
    (err,) = errs
    assert err.startswith("MCP 세션 실패: HTTP 401 인증 실패") and "POLESTAR_MCP_TOKEN" in err
    for secret in (_TOKEN, _WRONG):
        assert secret not in err and secret not in caplog.text


def test_prefetch_closed_port_userinfo_never_in_text_or_log(caplog):
    url = f"http://user:pw-187-secret@127.0.0.1:{_free_port()}/sse"
    with caplog.at_level(logging.DEBUG, logger=mc.__name__):
        out = mc.run_tool_batch(url, None, [("a", {})], timeout_seconds=3)
    err = json.loads(out[0])["error"]
    assert "연결 불가(ConnectError" in err and "***@127.0.0.1" in err
    assert "pw-187-secret" not in err and "pw-187-secret" not in caplog.text


def _real_agent(settings: AgentSettings) -> DiagnosisAgent:
    servers = mcp_service._build_mcp_servers(settings)
    return DiagnosisAgent(settings, toolsets={}, mcp_servers=servers)


def test_holmes_401_error_has_no_token_and_flows_to_health_and_briefing(auth_server):
    """holmes 선행검사 401 — 사유 문구가 sre_health·브리핑 한계로 그대로 가므로 토큰 부재를 실측."""
    s = _settings(
        polestar_mcp_url=f"http://127.0.0.1:{auth_server}/sse",
        polestar_mcp_token=SecretStr(_WRONG),
    )
    unavailable = _real_agent(s).mcp_unavailable()
    assert "polestar" in unavailable
    err = unavailable["polestar"]
    assert "401" in err
    check = mcp_service.ToolsetCheck()
    check.record(unavailable)
    line = tools_unavailable_result(unavailable).tools_unavailable or ""
    for text in (err, check.polestar_error or "", line):
        assert _TOKEN not in text and _WRONG not in text and "Bearer" not in text


def test_holmes_401_userinfo_url_is_redacted_in_unavailable_line(auth_server):
    s = _settings(polestar_mcp_url=f"http://user:pw-187-secret@127.0.0.1:{auth_server}/sse")
    unavailable = _real_agent(s).mcp_unavailable()
    line = tools_unavailable_result(unavailable).tools_unavailable or ""
    assert "pw-187-secret" not in line


# ── 동시성 · _default_diagnose_fn 락 ────────────────────────────────


class _SlowAgent:
    """생성 직후 첫 mcp_unavailable(=holmes 선행검사)이 느린 가짜 에이전트."""

    created: list = []
    statuses: list = []
    delay = 0.3

    def __init__(self, settings, toolsets=None, mcp_servers=None):
        cls = type(self)
        self._unavailable = cls.statuses.pop(0) if cls.statuses else {}
        self._checked = False
        self.asked = 0
        cls.created.append(self)

    def mcp_unavailable(self):
        if not self._checked:
            time.sleep(type(self).delay)
            self._checked = True
        return dict(self._unavailable)

    def tool_names(self):
        return frozenset({"polestar_metric_trend", "prom_metric_range"})

    def ask(self, question, system_prompt_additions=None):
        self.asked += 1
        time.sleep(0.2)
        return DiagnosisResult(answer=f"agent#{type(self).created.index(self)}")


@pytest.fixture
def slow(monkeypatch):
    class Agent(_SlowAgent):
        created = []
        statuses = []

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", Agent)
    return Agent


def test_concurrent_first_calls_create_one_agent(slow):
    fn = mcp_service._default_diagnose_fn(_settings(polestar_mcp_url="http://p/sse"))
    results: list = []
    ths = [threading.Thread(target=lambda: results.append(fn(_job()))) for _ in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    assert len(results) == 4 and len(slow.created) == 1
    assert all(r.tools_unavailable is None for r in results)


def test_inflight_ask_on_old_agent_survives_rebuild(slow, monkeypatch):
    """A가 옛 agent로 ask 중에 B가 재생성해도 A는 옛 agent로 완주한다(교체는 참조 교체뿐)."""
    slow.statuses = [{"apm": "down"}, {}]
    slow.delay = 0.0
    fn = mcp_service._default_diagnose_fn(
        _settings(polestar_mcp_url="http://p/sse", apm_mcp_url="http://a/sse")
    )
    out: dict = {}
    a = threading.Thread(target=lambda: out.setdefault("a", fn(_job())))
    a.start()
    time.sleep(0.05)  # A가 ask(0.2s) 안에 있을 때
    out["b"] = fn(_job())
    a.join(5)
    assert out["a"].answer == "agent#0" and out["b"].answer == "agent#1"
    assert len(slow.created) == 2 and slow.created[0].asked == 1


def test_polestar_down_rebuild_serializes_but_stays_under_timeout(slow):
    """폴스타 다운: 매 조사 재생성(선행검사)이 락 안에서 직렬화된다 — 비용은 건당 선행검사 1회."""
    slow.statuses = [{"polestar": _CONNECT_ERR}] * 3
    slow.delay = 0.3
    fn = mcp_service._default_diagnose_fn(_settings(polestar_mcp_url="http://p/sse"))
    fn(_job())  # 첫 생성
    t0 = time.monotonic()
    ths = [threading.Thread(target=fn, args=(_job(),)) for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join(10)
    elapsed = time.monotonic() - t0
    assert len(slow.created) == 3
    assert elapsed >= 0.55  # 두 재생성이 겹치지 않는다(직렬)
    assert all(a.asked == 0 for a in slow.created)


# ── A안 후처리 · 판정 · 감사 · 타임아웃/스텁 불변 ──────────────────────


def _alarm_job(server: str, severity: int = 4) -> InvestigationJob:
    return InvestigationJob(
        investigation_id=f"id-{server}",
        kind="alarm",
        status="running",
        created_at=0.0,
        updated_at=0.0,
        fingerprint=server,
        payload={
            "event": {"serverName": server, "severity": severity},
            "decision": {"tier": "PAGE"},
        },
    )


def _disp(diagnose_fn, tmp_path, **kw) -> InvestigationDispatcher:
    s = _settings(
        gemini_api_key="k",
        investigation_dedup_ttl_seconds=None,
        severity_judge_enabled=True,
        remediation_recommender_enabled=True,
        **kw,
    )
    return InvestigationDispatcher(
        s, diagnose_fn=diagnose_fn, briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl"
    )


def _audit(tmp_path) -> list[dict]:
    return [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()]


@pytest.mark.parametrize("apm_on", [False, True])
def test_a_plan_judge_on_no_escalate_and_audit_done(tmp_path, apm_on):
    disp = _disp(
        lambda job: tools_unavailable_result({"polestar": _CONNECT_ERR}),
        tmp_path,
        apm_guidance_enabled=apm_on,
    )
    job = _alarm_job("judge")
    disp(job)
    disp.wait_workers()
    assert job.status == "done" and job.error is None
    assert job.briefing["severity"]["escalate"] is False
    assert job.briefing["summary"].startswith(UNRESOLVED_PREFIX)
    assert "escalate=False" in job.verdict
    assert job.instrumentation["tool_calls_n"] == 0 and job.instrumentation["tool_calls"] == []
    done = [r for r in _audit(tmp_path) if r["event"] == "done"]
    assert len(done) == 1 and done[0]["escalate"] is False and done[0]["tokens"] == 0


def test_a_plan_error_text_is_not_a_hypothesis(tmp_path):
    disp = _disp(lambda job: tools_unavailable_result({"polestar": _CONNECT_ERR}), tmp_path)
    job = _alarm_job("hyp")
    disp(job)
    disp.wait_workers()
    b = job.briefing
    assert _CONNECT_ERR not in (b.get("cause") or "")
    assert not any(_CONNECT_ERR in h for h in b.get("hypotheses") or [])


def test_timeout_path_unchanged(tmp_path):
    def slow_fn(job):
        time.sleep(1.0)
        return DiagnosisResult(answer="late")

    s = _settings(gemini_api_key="k", investigation_dedup_ttl_seconds=None)
    disp = InvestigationDispatcher(
        s, diagnose_fn=slow_fn, briefing_fn=build_briefing,
        audit_path=tmp_path / "a.jsonl", timeout_seconds=0.2,
    )
    job = _alarm_job("to")
    disp(job)
    disp.wait_workers(5)
    assert job.status == "timeout" and job.briefing["stub"] is True
    assert "limitations" not in job.briefing


def test_stub_path_unchanged_and_health_null(tmp_path):
    """LLM 게이트 차단(키 없음) → stub · 툴셋 검사 안 함 → sre_health null."""
    import asyncio

    s = _settings(
        gemini_api_key=None, api_key=None, polestar_mcp_url="http://p/sse",
        investigation_audit_path=str(tmp_path / "audit.jsonl"),
    )
    mcp = mcp_service.create_service(s)
    res = asyncio.run(mcp.call_tool("sre_health", {}))
    content = res[0] if isinstance(res, tuple) else res
    health = json.loads(content[0].text)
    assert health["polestar_mcp_reachable"] is None and "polestar_mcp_error" not in health


# ── 지침 필터 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("line", "available", "expected"),
    [
        ("prom_metric_range로 먼저 본다", set(), ""),
        ("prom_metric_range로 먼저 본다", {"prom_metric_range"}, "prom_metric_range로 먼저 본다"),
        ("도구: polestar_alarm_history · prom_metric_range · polestar_topology",
         {"polestar_alarm_history", "polestar_topology"},
         "도구: polestar_alarm_history · polestar_topology"),
        ("도구: polestar_alarm_history · prom_metric_range", {"prom_metric_range"},
         "도구: prom_metric_range"),
        ("a, prom_metric_range, polestar_topology", {"polestar_topology"}, "a, polestar_topology"),
        # 접두가 같은 긴 이름은 지우지 않는다(\b 경계)
        ("polestar_metric_trend · polestar_metric_trend_x", {"polestar_metric_trend"},
         "polestar_metric_trend"),
        ("apm_* 도구는 폴백", set(), "apm_* 도구는 폴백"),
        ("도구 이름 없음 · bash", set(), "도구 이름 없음 · bash"),
    ],
)
def test_filter_tool_mentions_cases(line, available, expected):
    assert filter_tool_mentions(line, available) == expected


def test_real_guidance_polestar_only_keeps_every_line_with_loaded_tool_only():
    """실 지침 · 폴스타만 로드 — 없는 이름 0건 · 로드된 이름만 있는 줄은 그대로 남는다.

    APM 노트는 미로드 시 폴백으로 통째 교체되는 것이 설계라 비교에서 뺀다(apm_guidance off).
    """
    loaded = frozenset({
        "list_sources", "polestar_alarm_history", "polestar_change_history",
        "polestar_condition_log", "polestar_incident_alarms", "polestar_metric_trend",
        "polestar_os_config", "polestar_process_snapshot", "polestar_resource_status",
        "polestar_topology", "prom_metric_instant", "prom_metric_range",
    })
    s = _settings(openmetrics_guidance_enabled=True)
    for rt in ("cpu", "memory", "disk", "network", "process", "log"):
        job = _job(payload={"event": {"serverName": "w", "resourceType": rt, "alarmName": rt}})
        full = build_guidance(s, job)
        got = build_guidance(s, job, available_tools=loaded)
        assert got is not None
        assert set(TOOL_NAME_RE.findall(got)) <= loaded
        for ln in full.split("\n"):
            names = set(TOOL_NAME_RE.findall(ln))
            if names and names <= loaded:
                assert ln in got.split("\n"), ln


def test_none_available_is_bit_identical_across_kinds():
    s = _settings(apm_guidance_enabled=True, apm_mcp_url="http://a/sse",
                  openmetrics_guidance_enabled=True)
    for rt in ("cpu", "apm.Instance"):
        job = _job(payload={"event": {"serverName": "w", "resourceType": rt, "alarmName": rt}})
        assert build_guidance(s, job, available_tools=None) == build_guidance(s, job)


# ── 사전수집 C4′·C5 경계 ─────────────────────────────────────────────


def _scope() -> EvidenceScope:
    from sre_agent.application.evidence_prefetch import scope_from_job

    event = {"serverName": "web-01", "dbId": "polestar", "resourceType": "cpu"}
    job = _job(payload={"event": event})
    sc = scope_from_job(job)
    assert sc is not None
    return sc


def test_all_calls_failing_with_different_reasons_stay_per_axis():
    """세션 실패가 아니라 호출마다 다른 실패면 1줄로 접지 않는다(축별 결손 유지)."""
    sc = _scope()

    def batch(calls):
        return [json.dumps({"error": f"{name} 호출 실패: boom"}) for name, _ in calls]

    notes = prefetch_and_correlate(sc, batch).notes
    assert any(n.startswith("알람 축 결손") for n in notes)
    assert not any(n.startswith("사전수집 MCP 세션 실패") for n in notes)
    assert not any("알람 0건" in n for n in notes)
