"""plans/188 W3 — 툴셋 가용성(C1 재생성 · C2 A안 조사 생략·지침 도구 이름 필터 · C6 헬스).

실 LLM 0. holmes 툴셋 상태는 ① 가짜 상태 객체 ② 닫힌 포트에 대한 실 holmes 선행검사
③ 가짜 세계 MCP 서버(`investigation_bench.world_server`) 끄고 켜기로 만든다.
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from holmes.core.tools import ToolsetStatusEnum, ToolsetType
from sre_agent.application.briefing_builder import SKIPPED_CAUSE, UNRESOLVED_PREFIX
from sre_agent.application.investigation_dispatcher import InvestigationDispatcher
from sre_agent.application.investigation_guidance import (
    APM_FOCUS_NOTE_TEMPLATE,
    APM_TOOLS_UNLOADED_NOTE,
    TOOL_NAME_RE,
    build_guidance,
    filter_tool_mentions,
)
from sre_agent.application.investigation_jobs import CONTRACT_VERSION, InvestigationJob
from sre_agent.diagnosis import (
    TOOLS_UNAVAILABLE_PREFIX,
    DiagnosisAgent,
    DiagnosisResult,
    ToolCallRecord,
    clean_toolset_error,
    tools_unavailable_result,
)
from sre_agent.interface import mcp_service
from sre_agent.settings import AgentSettings

_FIX = Path(__file__).resolve().parent / "fixtures" / "investigation_bench"
_SNAPSHOT = (
    Path(__file__).resolve().parents[1] / "testdata" / "investigation_bench" / "tool_schemas.json"
)
_CONNECT_ERR = (
    "Failed to load mcp server polestar: All connection attempts failed. "
    "If the server is still starting up, Holmes will retry automatically"
)
_CLEAN_ERR = "Failed to load mcp server polestar: All connection attempts failed"


def _settings(**kw) -> AgentSettings:
    kw.setdefault("_env_file", None)
    kw.setdefault("model", "openai/test-model")
    kw.setdefault("api_key", "x")
    kw.setdefault("gemini_api_key", None)
    kw.setdefault("service_bearer_token", None)
    kw.setdefault("evidence_correlation_enabled", False)
    return AgentSettings(**kw)


def _job(**kw) -> SimpleNamespace:
    base = {
        "kind": "alarm",
        "question": None,
        "investigation_id": "inv-187",
        "payload": {
            "event": {"serverName": "web-01", "resourceType": "cpu", "alarmName": "CPU High"}
        },
        "reference_time": "2026-10-19T10:00:00",
        "lookback_minutes": 120,
    }
    base.update(kw)
    return SimpleNamespace(**base)


def _snapshot_names(*endpoints: str) -> frozenset[str]:
    data = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    return frozenset(t["name"] for ep in endpoints for t in data[ep])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ts(
    name: str,
    status: ToolsetStatusEnum,
    error: str | None = None,
    type_: ToolsetType = ToolsetType.MCP,
) -> SimpleNamespace:
    return SimpleNamespace(name=name, status=status, error=error, type=type_)


def _agent_with(toolsets: list, tools: list[str]) -> DiagnosisAgent:
    agent = DiagnosisAgent(_settings(), toolsets={})
    agent._llm = SimpleNamespace(  # type: ignore[assignment]
        tool_executor=SimpleNamespace(toolsets=toolsets, tools_by_name=dict.fromkeys(tools))
    )
    return agent


# ── C1 · DiagnosisAgent 툴셋 검사 ──────────────────────────────────


def test_mcp_unavailable_lists_only_failed_mcp_toolsets():
    agent = _agent_with(
        [
            _ts("polestar", ToolsetStatusEnum.FAILED, _CONNECT_ERR),
            _ts("apm", ToolsetStatusEnum.ENABLED),
            _ts("bash", ToolsetStatusEnum.FAILED, "no bash", ToolsetType.BUILTIN),
        ],
        ["fetch_webpage"],
    )
    assert agent.mcp_unavailable() == {"polestar": _CLEAN_ERR}
    assert agent.tool_names() == frozenset({"fetch_webpage"})


def test_mcp_unavailable_empty_when_all_enabled():
    agent = _agent_with([_ts("polestar", ToolsetStatusEnum.ENABLED)], ["polestar_metric_trend"])
    assert agent.mcp_unavailable() == {}


def test_real_holmes_closed_port_marks_polestar_failed_eagerly():
    """실측 고정: MCP 툴셋은 생성 직후 이미 FAILED다(지연 초기화 아님) — 닫힌 포트 재현."""
    s = _settings(polestar_mcp_url=f"http://127.0.0.1:{_free_port()}/sse")
    agent = DiagnosisAgent(s, toolsets={}, mcp_servers=mcp_service._build_mcp_servers(s))
    unavailable = agent.mcp_unavailable()
    assert "All connection attempts failed" in unavailable["polestar"]
    assert not any(n.startswith("polestar_") for n in agent.tool_names())


def test_tools_unavailable_result_is_llm_free_marker():
    r = tools_unavailable_result({"polestar": _CONNECT_ERR})
    assert r.answer == TOOLS_UNAVAILABLE_PREFIX   # 서술(요약)에는 오류 원문 없음
    assert r.tools_unavailable == f"{TOOLS_UNAVAILABLE_PREFIX}(polestar: {_CLEAN_ERR})"
    assert r.tool_outputs == [] and r.total_tokens == 0 and r.steps is None
    assert DiagnosisResult(answer="x").tools_unavailable is None  # 기본값 = 종전


# ── C1·C2 · _default_diagnose_fn 재생성 · A안 ──────────────────────


class _ScriptedAgent:
    """생성될 때마다 다음 상태를 집는 가짜 에이전트(생성 수·ask 호출을 센다)."""

    statuses: list[dict[str, str]] = []
    created: list[_ScriptedAgent] = []
    asked: list[str | None] = []
    tools: frozenset[str] = frozenset()

    def __init__(self, settings, toolsets=None, mcp_servers=None):
        cls = type(self)
        self._unavailable = cls.statuses.pop(0) if cls.statuses else {}
        cls.created.append(self)

    def mcp_unavailable(self) -> dict[str, str]:
        return dict(self._unavailable)

    def tool_names(self) -> frozenset[str]:
        return type(self).tools

    def ask(self, question, system_prompt_additions=None):
        type(self).asked.append(system_prompt_additions)
        return DiagnosisResult(answer="ok")


@pytest.fixture
def scripted(monkeypatch):
    class Agent(_ScriptedAgent):
        statuses = []
        created = []
        asked = []
        tools = _snapshot_names("polestar")

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", Agent)
    return Agent


def test_healthy_agent_is_never_rebuilt(scripted):
    check = mcp_service.ToolsetCheck()
    fn = mcp_service._default_diagnose_fn(_settings(polestar_mcp_url="http://p/sse"), check)
    for _ in range(3):
        assert fn(_job()).tools_unavailable is None
    assert len(scripted.created) == 1 and len(scripted.asked) == 3
    assert check.polestar_reachable is True and check.polestar_error is None


def test_polestar_failed_skips_llm_then_recovers_by_rebuild(scripted):
    scripted.statuses = [{"polestar": _CONNECT_ERR}, {}]
    check = mcp_service.ToolsetCheck()
    fn = mcp_service._default_diagnose_fn(_settings(polestar_mcp_url="http://p/sse"), check)

    first = fn(_job())
    assert first.tools_unavailable and _CLEAN_ERR in first.tools_unavailable
    assert scripted.asked == [] and len(scripted.created) == 1  # 첫 생성 직후엔 다시 만들지 않는다
    assert check.polestar_reachable is False and check.polestar_error == _CLEAN_ERR

    second = fn(_job())  # 캐시된 agent가 FAILED → 1회 재생성 → 정상
    assert second.tools_unavailable is None and len(scripted.asked) == 1
    assert len(scripted.created) == 2
    assert check.polestar_reachable is True and check.polestar_error is None


def test_still_failed_after_rebuild_rebuilds_once_per_job(scripted):
    scripted.statuses = [{"polestar": _CONNECT_ERR}] * 3
    fn = mcp_service._default_diagnose_fn(_settings(polestar_mcp_url="http://p/sse"))
    assert fn(_job()).tools_unavailable and fn(_job()).tools_unavailable
    assert len(scripted.created) == 2 and scripted.asked == []


def test_apm_only_failed_proceeds_with_filtered_guidance(scripted):
    scripted.statuses = [{"apm": "Failed to load mcp server apm: x"}]
    s = _settings(
        polestar_mcp_url="http://p/sse",
        apm_mcp_url="http://a/sse",
        apm_guidance_enabled=True,
        openmetrics_guidance_enabled=True,
    )
    check = mcp_service.ToolsetCheck()
    result = mcp_service._default_diagnose_fn(s, check)(_job())
    assert result.tools_unavailable is None and len(scripted.asked) == 1
    guidance = scripted.asked[0]
    assert set(TOOL_NAME_RE.findall(guidance)) <= scripted.tools
    assert APM_TOOLS_UNLOADED_NOTE in guidance
    assert check.polestar_reachable is True


def test_no_polestar_url_leaves_health_unchecked(scripted):
    check = mcp_service.ToolsetCheck()
    mcp_service._default_diagnose_fn(_settings(polestar_mcp_url=""), check)(_job())
    assert check.polestar_reachable is None


# ── C2 · 지침 도구 이름 필터 ────────────────────────────────────────


_GUIDANCE_SETTINGS = {
    "apm_guidance_enabled": True,
    "apm_mcp_url": "http://a/sse",
    "openmetrics_guidance_enabled": True,
}


@pytest.mark.parametrize(
    "payload",
    [
        {"event": {"serverName": "web-01", "resourceType": kind, "alarmName": kind}}
        for kind in ("cpu", "memory", "disk", "network", "process", "log")
    ]
    + [
        {
            "event": {
                "serverName": "web-01",
                "resourceType": "apm.Instance",
                "alarmName": "TX_BAD_RESPONSE",
            }
        }
    ],
)
def test_no_missing_tool_name_in_guidance(payload):
    """폴스타 도구만 로드(APM FAILED · om_* 없음) → 지침의 도구 이름은 전부 로드된 것."""
    available = _snapshot_names("polestar")
    g = build_guidance(
        _settings(**_GUIDANCE_SETTINGS), _job(payload=payload), available_tools=available
    )
    assert g is not None
    assert set(TOOL_NAME_RE.findall(g)) <= available


def test_all_tools_loaded_is_bit_identical_to_unfiltered():
    s = _settings(**_GUIDANCE_SETTINGS)
    everything = frozenset(TOOL_NAME_RE.findall(build_guidance(s, _job()) or "")) | _snapshot_names(
        "polestar", "apm"
    )
    assert build_guidance(s, _job(), available_tools=everything) == build_guidance(s, _job())


def test_list_item_removed_but_line_kept():
    line = "⑤ 대조 — polestar_metric_trend · prom_metric_range로 같은 구간 지표를 본다."
    assert filter_tool_mentions(line, {"polestar_metric_trend"}) == (
        "⑤ 대조 — polestar_metric_trend로 같은 구간 지표를 본다."
    )
    assert filter_tool_mentions(line, set()) == ""
    assert (
        filter_tool_mentions("도구 이름 없는 줄 · apm_* 와일드카드", set())
        == "도구 이름 없는 줄 · apm_* 와일드카드"
    )


def test_scope_note_lists_only_loaded_anchored_tools():
    g = build_guidance(
        _settings(), _job(), available_tools={"polestar_metric_trend", "polestar_incident_alarms"}
    )
    assert "polestar_metric_trend · polestar_incident_alarms 를 호출할 때" in g
    assert "prom_metric_range" not in g and "polestar_alarm_history" not in g


def test_apm_unloaded_swaps_focus_note_for_fallback():
    g = build_guidance(
        _settings(**_GUIDANCE_SETTINGS), _job(), available_tools=_snapshot_names("polestar")
    )
    assert APM_TOOLS_UNLOADED_NOTE in g
    assert APM_FOCUS_NOTE_TEMPLATE.splitlines()[0] not in g
    assert "polestar_process_snapshot" in g  # 폴백 도구 안내는 남는다


def test_cross_source_note_apm_unloaded_adds_not_connected_limit():
    from sre_agent.application.investigation_guidance import cross_source_note
    from sre_agent.domain.cross_source_episode import APM_TOOLS_NOT_CONNECTED_LIMIT, EpisodeContext

    s = _settings(apm_mcp_url="http://a/sse")
    ctx = EpisodeContext(episode_id="ep-187")
    assert APM_TOOLS_NOT_CONNECTED_LIMIT not in cross_source_note(s, ctx)
    assert APM_TOOLS_NOT_CONNECTED_LIMIT not in cross_source_note(s, ctx, apm_loaded=True)
    assert APM_TOOLS_NOT_CONNECTED_LIMIT in cross_source_note(s, ctx, apm_loaded=False)


def test_operator_extra_is_not_filtered():
    g = build_guidance(
        _settings(investigation_guidance_extra="prom_metric_range 먼저"),
        _job(),
        available_tools=set(),
    )
    assert g is not None and g.endswith("prom_metric_range 먼저")


# ── C2 · dispatcher A안 표지 ─────────────────────────────────────────


def _alarm_job(server: str) -> InvestigationJob:
    return InvestigationJob(
        investigation_id=f"id-{server}",
        kind="alarm",
        status="running",
        created_at=0.0,
        updated_at=0.0,
        fingerprint=server,
        payload={"event": {"serverName": server, "severity": 2}, "decision": {"tier": "PAGE"}},
    )


def _dispatcher(result_fn) -> InvestigationDispatcher:
    s = _settings(
        gemini_api_key="k", investigation_dedup_ttl_seconds=None, severity_judge_enabled=False
    )
    from sre_agent.application.briefing_builder import build_briefing

    return InvestigationDispatcher(s, diagnose_fn=result_fn, briefing_fn=build_briefing)


def test_dispatcher_marks_unavailable_result_unresolved_with_reason():
    line = tools_unavailable_result({"polestar": _CONNECT_ERR}).tools_unavailable
    disp = _dispatcher(lambda job: tools_unavailable_result({"polestar": _CONNECT_ERR}))
    job = _alarm_job("a-plan")
    disp(job)
    disp.wait_workers()
    b = job.briefing
    assert job.status == "done"
    assert b["summary"] == UNRESOLVED_PREFIX + TOOLS_UNAVAILABLE_PREFIX   # [가설]·오류 원문 없음
    assert b["cause"] == SKIPPED_CAUSE and b["hypotheses"] == []
    assert b["root_cause_hypotheses"] == []
    assert b["limitations"] == [line]   # 사유는 한계에만 · 인용 결여 강등 줄 없음
    assert job.tokens == 0 and job.tool_calls_summary == []


def test_dispatcher_a_plan_drops_apm_limits_and_audits_reason(tmp_path):
    line = tools_unavailable_result({"polestar": _CONNECT_ERR}).tools_unavailable
    s = _settings(gemini_api_key="k", investigation_dedup_ttl_seconds=None,
                  severity_judge_enabled=False, apm_guidance_enabled=True, apm_mcp_url="")
    from sre_agent.application.briefing_builder import build_briefing

    disp = InvestigationDispatcher(
        s, diagnose_fn=lambda job: tools_unavailable_result({"polestar": _CONNECT_ERR}),
        briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    job = _alarm_job("a-plan-apm")
    disp(job)
    disp.wait_workers()
    assert job.briefing["limitations"] == [line]   # APM 미설정 대체 문구 없음
    done = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()
            if json.loads(x)["event"] == "done"]
    assert done[0]["tools_unavailable"] == line


def test_dispatcher_normal_audit_has_no_tools_unavailable_key(tmp_path):
    s = _settings(gemini_api_key="k", investigation_dedup_ttl_seconds=None,
                  severity_judge_enabled=False)
    from sre_agent.application.briefing_builder import build_briefing

    disp = InvestigationDispatcher(s, diagnose_fn=lambda job: DiagnosisResult(answer="ok"),
                                   briefing_fn=build_briefing, audit_path=tmp_path / "a.jsonl")
    disp(_alarm_job("normal-audit"))
    disp.wait_workers()
    done = [json.loads(x) for x in (tmp_path / "a.jsonl").read_text().splitlines()
            if json.loads(x)["event"] == "done"]
    assert "tools_unavailable" not in done[0]


@pytest.mark.parametrize(("raw", "expected"), [
    (_CONNECT_ERR, _CLEAN_ERR),
    ("Client error '401 Unauthorized' for url 'http://u:pw@h:9099/sse'\n"
     "For more information check: https://x/401",
     "Client error '401 Unauthorized' for url 'http://***@h:9099/sse'"),
    ("plain reason", "plain reason"),
    (None, ""),
])
def test_clean_toolset_error(raw, expected):
    assert clean_toolset_error(raw) == expected


def test_dispatcher_normal_result_not_unresolved():
    disp = _dispatcher(
        lambda job: DiagnosisResult(
            answer="OOM ← journalctl",
            tool_calls=["j"],
            tool_outputs=[ToolCallRecord("journalctl", "j", "success", "ok")],
        )
    )
    job = _alarm_job("normal")
    disp(job)
    disp.wait_workers()
    assert not job.briefing["summary"].startswith(UNRESOLVED_PREFIX)


# ── C6 · sre_health ────────────────────────────────────────────────


def _call(mcp, name, args=None) -> dict:
    result = asyncio.run(mcp.call_tool(name, args or {}))
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


def test_health_reflects_last_toolset_check(scripted, tmp_path):
    scripted.statuses = [{"polestar": _CONNECT_ERR}]
    s = _settings(
        gemini_api_key="k",
        polestar_mcp_url="http://p/sse",
        investigation_audit_path=str(tmp_path / "audit.jsonl"),
    )
    mcp = mcp_service.create_service(s)
    before = _call(mcp, "sre_health")
    assert before["polestar_mcp_reachable"] is None and "polestar_mcp_error" not in before

    payload = {
        "contract_version": CONTRACT_VERSION,
        "event": {"serverName": "web-01", "hostname": "web-01.local", "severity": 2},
        "decision": {"fingerprint": "fp-187"},
    }
    iid = _call(mcp, "sre_investigate_alarm", {"payload": payload})["investigation_id"]
    mcp_service.get_job_store(mcp)._executor.wait_workers()

    health = _call(mcp, "sre_health")
    assert health["polestar_mcp_reachable"] is False
    assert health["polestar_mcp_error"] == _CLEAN_ERR
    assert health["contract_version"] == CONTRACT_VERSION
    got = _call(mcp, "sre_get_investigation", {"investigation_id": iid})
    assert got["status"] == "done" and got["briefing"]["summary"].startswith(UNRESOLVED_PREFIX)
    assert scripted.asked == []


# ── 종단: 가짜 세계 MCP 서버 꺼짐 → 켜짐 (LLM 가짜) ────────────────────


def test_e2e_world_server_down_then_up_recovers_without_restart(monkeypatch, tmp_path):
    from investigation_bench import schema
    from investigation_bench.world_server import WorldServer
    from investigation_bench.worlds import load_worlds

    created: list[DiagnosisAgent] = []
    asked: list[frozenset[str]] = []

    class CountingAgent(DiagnosisAgent):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            created.append(self)

        def ask(self, question, system_prompt_additions=None):  # LLM 대역 — 호출 0
            asked.append(self.tool_names())
            return DiagnosisResult(answer="ok")

    monkeypatch.setattr(mcp_service, "DiagnosisAgent", CountingAgent)
    port = _free_port()
    check = mcp_service.ToolsetCheck()
    fn = mcp_service._default_diagnose_fn(
        _settings(polestar_mcp_url=f"http://127.0.0.1:{port}/sse"), check
    )

    down = fn(_job())  # 서버 꺼짐 → A안(LLM 0)
    assert down.tools_unavailable and "All connection attempts failed" in down.tools_unavailable
    assert asked == [] and check.polestar_reachable is False

    ws = WorldServer(
        schema.load_snapshot(_FIX / "tool_schemas.json"),
        load_worlds([_FIX / "worlds"]),
        tmp_path / "world_calls.jsonl",
    )
    ws.start({"polestar": port})
    try:
        up = fn(_job())  # 재기동 없이 재생성 → 도구 로드
        assert up.tools_unavailable is None and len(created) == 2
        assert "polestar_metric_trend" in asked[0]
        assert check.polestar_reachable is True

        fn(_job())  # 정상이면 재생성 0회
        assert len(created) == 2 and len(asked) == 2
    finally:
        ws.stop()
