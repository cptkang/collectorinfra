"""deepagents Advisory Enricher 단위 테스트 (Plan 52 Phase E5, §8.7, D-048.7).

E5 검증 기준(§10 verify)을 결정적으로 고정한다:
    - 3경로 자동 선택(트랙 B / 트랙 A / no-op)이 vLLM 가용성·fallback 설정으로 갈린다.
    - **enable_agentic_enricher=False → 완전 no-op**(E1~E4 배선·판단 무변경, 회귀 0).
    - **심각도3은 enricher가 개입하지 않는다**(step0 PAGE 단락, 비용 절감 + PAGE 불변).
    - **메시지형 한정** — 임계형(CPU/메모리 수치) 알람은 도구 호출 없이 통과.
    - 보강은 **승격 전용** — 후보가 event.severity를 초과할 때만 상향, 하향/강등 절대 없음.
    - 트랙 B ReAct 루프는 **max_tool_calls 상한**을 넘지 않는다.
    - enricher 실패(LLM 예외/타임아웃)는 발송을 막지 않는다(graceful no-op).
    - 그래프 배선: enable_agentic_enricher on/off에 따라 agentic_enricher 노드 포함/제외.

vLLM 미서빙 환경(현 운영·CI)을 전제로 하며, 라이브 호출 없이 트랙 A/no-op/배선을 검증한다.
트랙 B는 fake bound LLM으로 ReAct 루프 상한만 검증한다(실 vLLM 불요).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import src.alarm.application.nodes.agentic_enricher as enricher_mod
from src.alarm.application.nodes.agentic_enricher import (
    _is_message_alarm,
    _select_backend,
    agentic_enricher_node,
)
from src.alarm.domain.alarm import AlarmEvent
from src.alarm.infrastructure.noise_signal_tools import (
    build_noise_signal_tools,
    collect_signal,
    extract_escalation_candidate,
    run_signal_react_loop,
    scan_message_signature,
    vllm_healthy,
)
from src.alarm.orchestration.alarm_graph import build_alarm_graph

REF = datetime(2026, 6, 30, 10, 0, 0)


# ── 픽스처 헬퍼 ──────────────────────────────────────────────────────────
def make_event(**kwargs) -> AlarmEvent:
    """테스트용 AlarmEvent(메시지형 기본 — condition_log 보유). severity로 is_clear 동기화."""
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="srv-log01",
        hostname="hlog01",
        ip_address="10.1.2.9",
        resource_ancestry="/Servers/srv/LogMonitor",
        alarm_id="LOG-001",
        severity=1,
        alarm_status="NOT_ACK",
        resource_type="server.LogMonitor",
        resource_name="syslog",
        alarm_name="로그 패턴 감지",
        alarm_time=REF,
        conditions="",
        condition_log="too many open files",  # → (2, "FD 고갈") 시그니처
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


def make_gate_cfg(**kwargs) -> SimpleNamespace:
    """NoiseGateConfig를 덕타이핑으로 모사(E5 필드 한정)."""
    base = dict(
        enable_agentic_enricher=True,
        agentic_enricher_message_alarms_only=True,
        agentic_enricher_fallback="semantic_routing",
        agentic_enricher_timeout_seconds=8.0,
        agentic_enricher_max_tool_calls=5,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def make_orchestrator(
    *, provider="vllm", base_url="", api_key="", health_timeout=3
) -> SimpleNamespace:
    """OrchestratorConfig 덕타이핑(provider 분기·vLLM/gemini 키)."""
    return SimpleNamespace(
        provider=provider, base_url=base_url, api_key=api_key, health_timeout=health_timeout
    )


def make_app_cfg(gate=None, *, orchestrator=None, gemini_api_key="") -> SimpleNamespace:
    """노드가 소비하는 app_config(noise_gate + orchestrator + llm).

    기본 orchestrator는 provider="vllm"·base_url=""(→ vLLM 미가용 → 폴백)이라,
    별도 지정 없는 노드 테스트는 트랙 A/no-op 경로를 탄다.
    """
    return SimpleNamespace(
        noise_gate=gate if gate is not None else make_gate_cfg(),
        orchestrator=orchestrator if orchestrator is not None else make_orchestrator(),
        llm=SimpleNamespace(gemini_api_key=gemini_api_key),
    )


def make_analysis(ai_message_severity=None) -> SimpleNamespace:
    """alarm_analyzer 산출 AlarmAnalysisResult를 모사(승격 대상 필드만 가변)."""
    return SimpleNamespace(
        ai_message_severity=ai_message_severity,
        ai_severity_reason="",
    )


def make_config(app_cfg, noise_repo=None) -> dict:
    return {"configurable": {"app_config": app_cfg, "noise_repo": noise_repo}}


class _FakeLLM:
    """트랙 A용 fake — content(JSON 문자열)를 그대로 반환한다."""

    def __init__(self, content: str):
        self._content = content

    async def ainvoke(self, messages):
        return SimpleNamespace(content=self._content)


class _ToolCallLLM:
    """트랙 B용 fake — 매 호출마다 scan_message_signature 1개 호출을 지시한다(무한)."""

    def __init__(self):
        self.n = 0

    async def ainvoke(self, messages):
        self.n += 1
        return SimpleNamespace(
            content="",
            tool_calls=[{"name": "scan_message_signature", "args": {}, "id": f"c{self.n}"}],
        )


class _NoToolLLM:
    """도구를 호출하지 않고 즉시 종료하는 fake bound LLM."""

    async def ainvoke(self, messages):
        return SimpleNamespace(content="done", tool_calls=[])


# ── 1. 3경로 자동 선택 ────────────────────────────────────────────────────
class TestBackendSelection:
    def test_track_a_when_vllm_down_and_semantic_routing(self):
        gate = make_gate_cfg(agentic_enricher_fallback="semantic_routing")
        cfg = make_app_cfg(gate, orchestrator=make_orchestrator(provider="vllm", base_url=""))
        assert _select_backend(gate, cfg) == "track_a"

    def test_noop_when_vllm_down_and_deterministic_only(self):
        gate = make_gate_cfg(agentic_enricher_fallback="deterministic_only")
        cfg = make_app_cfg(gate, orchestrator=make_orchestrator(provider="vllm", base_url=""))
        assert _select_backend(gate, cfg) == "noop"

    def test_track_b_when_vllm_healthy(self, monkeypatch):
        monkeypatch.setattr(enricher_mod, "vllm_healthy", lambda url, t: True)
        gate = make_gate_cfg()
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="vllm", base_url="http://vllm:8000/v1")
        )
        assert _select_backend(gate, cfg) == "track_b"

    def test_vllm_healthy_empty_url_false(self):
        # health check는 base_url 미설정 시 네트워크 호출 없이 False.
        assert vllm_healthy("", 3) is False

    # ── gemini 오케스트레이터(테스트/PoC) 트랙 B ─────────────────────────────
    def test_track_b_when_gemini_with_orchestrator_key(self):
        # provider="gemini" + orchestrator.api_key → vLLM 없이 트랙 B(네이티브 tool-calling)
        gate = make_gate_cfg()
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="gemini", api_key="k-123")
        )
        assert _select_backend(gate, cfg) == "track_b"

    def test_track_b_when_gemini_key_via_llm_fallback(self):
        # orchestrator.api_key 없어도 llm.gemini_api_key로 폴백 해석 → 트랙 B
        gate = make_gate_cfg()
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="gemini", api_key=""),
            gemini_api_key="k-from-llm",
        )
        assert _select_backend(gate, cfg) == "track_b"

    def test_gemini_without_key_falls_back_to_track_a(self):
        # provider="gemini"인데 키가 전무 → 트랙 B 안 켜고 fallback(semantic_routing→트랙 A)
        gate = make_gate_cfg(agentic_enricher_fallback="semantic_routing")
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="gemini", api_key=""), gemini_api_key=""
        )
        assert _select_backend(gate, cfg) == "track_a"

    def test_gemini_without_key_deterministic_only_noop(self):
        gate = make_gate_cfg(agentic_enricher_fallback="deterministic_only")
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="gemini", api_key=""), gemini_api_key=""
        )
        assert _select_backend(gate, cfg) == "noop"

    def test_gemini_provider_does_not_call_vllm_health(self, monkeypatch):
        # provider="gemini"면 vLLM health check를 아예 타지 않는다(불필요 네트워크 회피).
        def _boom(url, t):
            raise AssertionError("gemini provider에서 vllm_healthy가 호출되면 안 됨")

        monkeypatch.setattr(enricher_mod, "vllm_healthy", _boom)
        gate = make_gate_cfg()
        cfg = make_app_cfg(
            gate, orchestrator=make_orchestrator(provider="gemini", api_key="k")
        )
        assert _select_backend(gate, cfg) == "track_b"


# ── 2. 메시지형 알람 판정 ────────────────────────────────────────────────
class TestMessageAlarmDetection:
    def test_message_alarm_with_log(self):
        e = make_event(condition_log="failed password for root", resource_type="server.LogMonitor")
        assert _is_message_alarm(e) is True

    def test_metric_resource_excluded(self):
        # resource_type에 메트릭 토큰(cpu) → 임계형 → 제외
        e = make_event(condition_log="usage 95%", resource_type="host.cpu")
        assert _is_message_alarm(e) is False

    def test_empty_log_excluded(self):
        e = make_event(condition_log="   ", resource_type="server.LogMonitor")
        assert _is_message_alarm(e) is False


# ── 3. 신호 수집·후보 추출(결정적) ───────────────────────────────────────
class TestSignalCollection:
    def test_scan_signature_hit(self):
        r = scan_message_signature("kernel: Out of memory: Killed process 1 (java)")
        assert r["signal"] == "message_signature"
        assert r["severity"] == 3
        assert r["label"]

    def test_scan_signature_miss(self):
        r = scan_message_signature("routine heartbeat ok")
        assert r["severity"] is None

    def test_extract_candidate_picks_highest(self):
        coll = [
            {"signal": "message_signature", "severity": 2, "label": "FD 고갈"},
            {"signal": "message_signature", "severity": 3, "label": "OOM"},
            {"signal": "dependency", "context": None},
        ]
        assert extract_escalation_candidate(coll) == (3, "OOM")

    def test_extract_candidate_none_when_absent(self):
        assert extract_escalation_candidate([{"signal": "dependency", "context": None}]) is None
        assert extract_escalation_candidate([]) is None
        assert extract_escalation_candidate(None) is None

    async def test_collect_signal_message_signature(self):
        r = await collect_signal(
            "message_signature", noise_repo=None, event=make_event(condition_log="segfault at 0")
        )
        assert r["signal"] == "message_signature" and r["severity"] == 3

    async def test_collect_signal_unknown_returns_none(self):
        assert await collect_signal("bogus", noise_repo=None, event=make_event()) is None

    async def test_collect_signal_importance_no_repo(self):
        r = await collect_signal("importance_maintenance", noise_repo=None, event=make_event())
        assert r["signal"] == "importance_maintenance" and r["context"] is None


# ── 4. 트랙 B ReAct 루프 상한 ─────────────────────────────────────────────
class TestReactLoopCap:
    async def test_react_loop_respects_cap(self):
        collector: list = []
        tools = build_noise_signal_tools(
            None, make_event(condition_log="too many open files"), collector=collector
        )
        calls = await run_signal_react_loop(
            _ToolCallLLM(), tools, system_prompt="s", user_prompt="u", max_tool_calls=2
        )
        assert calls == 2  # 상한 준수
        assert len(collector) == 2  # 상한만큼만 원본 적재

    async def test_react_loop_stops_when_no_tool_calls(self):
        collector: list = []
        tools = build_noise_signal_tools(None, make_event(), collector=collector)
        calls = await run_signal_react_loop(
            _NoToolLLM(), tools, system_prompt="s", user_prompt="u", max_tool_calls=5
        )
        assert calls == 0 and collector == []


# ── 5. 노드 no-op 안전(회귀 0·심각도3·메시지형·백엔드 부재) ───────────────
class TestNodeNoOp:
    async def test_off_returns_empty(self):
        # enable_agentic_enricher=False → {} (E1~E4 무변경, 회귀 0)
        cfg = make_app_cfg(make_gate_cfg(enable_agentic_enricher=False))
        state = {"alarm_event": make_event(), "analysis_result": make_analysis()}
        assert await agentic_enricher_node(state, make_config(cfg)) == {}

    async def test_no_analysis_returns_empty(self):
        cfg = make_app_cfg()
        state = {"alarm_event": make_event(), "analysis_result": None}
        assert await agentic_enricher_node(state, make_config(cfg)) == {}

    async def test_error_state_returns_empty(self):
        cfg = make_app_cfg()
        state = {"alarm_event": make_event(), "analysis_result": make_analysis(), "error": "boom"}
        assert await agentic_enricher_node(state, make_config(cfg)) == {}

    async def test_missing_app_config_returns_empty(self):
        state = {"alarm_event": make_event(), "analysis_result": make_analysis()}
        assert await agentic_enricher_node(state, {"configurable": {}}) == {}

    async def test_severity3_short_circuit(self):
        # 심각도3 → enricher 미개입(PAGE 불변). 상향 후보(OOM=3)가 있어도 개입 안 함.
        cfg = make_app_cfg()
        state = {
            "alarm_event": make_event(severity=3, condition_log="out of memory: Killed"),
            "analysis_result": make_analysis(),
        }
        assert await agentic_enricher_node(state, make_config(cfg)) == {}

    async def test_metric_alarm_excluded_when_message_only(self):
        # message_alarms_only=True + 임계형(cpu) → 도구 호출 없이 통과
        cfg = make_app_cfg(make_gate_cfg(agentic_enricher_message_alarms_only=True))
        state = {
            "alarm_event": make_event(resource_type="host.cpu", condition_log="usage 99%"),
            "analysis_result": make_analysis(),
        }
        assert await agentic_enricher_node(state, make_config(cfg)) == {}

    async def test_noop_backend_deterministic_only(self):
        # vLLM 미서빙 + deterministic_only → no-op(결정적 게이트만)
        cfg = make_app_cfg(make_gate_cfg(agentic_enricher_fallback="deterministic_only"))
        state = {"alarm_event": make_event(), "analysis_result": make_analysis()}
        assert await agentic_enricher_node(state, make_config(cfg)) == {}


# ── 6. 트랙 A 승격(상향 전용·하향 금지·noise_context 확장·graceful) ────────
class TestNodeTrackA:
    def _patch_llm(self, monkeypatch, content='{"needed_signals": ["message_signature"]}'):
        import src.llm as llm_mod

        monkeypatch.setattr(llm_mod, "create_llm", lambda cfg: _FakeLLM(content))

    async def test_track_a_escalates_upgrade_only(self, monkeypatch):
        self._patch_llm(monkeypatch)
        cfg = make_app_cfg(make_gate_cfg(agentic_enricher_fallback="semantic_routing"))
        analysis = make_analysis(ai_message_severity=None)
        # severity1 + "too many open files"(시그니처 2) → 2 > 1 → 상향
        state = {
            "alarm_event": make_event(severity=1, condition_log="too many open files"),
            "analysis_result": analysis,
        }
        out = await agentic_enricher_node(state, make_config(cfg))
        assert out["analysis_result"].ai_message_severity == 2
        assert "Advisory Enricher(track_a)" in out["analysis_result"].ai_severity_reason
        # noise_context 확장: Plan 55 예약키 + agentic 감사 스냅샷
        ctx = out["noise_context"]
        assert ctx["agentic"]["backend"] == "track_a"
        assert "message_signature" in ctx["agentic"]["signals"]
        assert "app_impact" in ctx and "db_impact" in ctx

    async def test_track_a_no_signature_no_escalation(self, monkeypatch):
        self._patch_llm(monkeypatch)
        cfg = make_app_cfg(make_gate_cfg())
        analysis = make_analysis(ai_message_severity=None)
        # 시그니처 없는 로그 → 상향 후보 없음 → analysis_result 미변경
        state = {
            "alarm_event": make_event(severity=2, condition_log="routine ok heartbeat"),
            "analysis_result": analysis,
        }
        out = await agentic_enricher_node(state, make_config(cfg))
        assert "analysis_result" not in out
        assert analysis.ai_message_severity is None

    async def test_track_a_candidate_not_above_severity_no_change(self, monkeypatch):
        self._patch_llm(monkeypatch)
        cfg = make_app_cfg(make_gate_cfg())
        analysis = make_analysis(ai_message_severity=None)
        # 후보 severity(2)가 event.severity(2)를 초과 못 함 → 미상향(> 조건, 상향 전용)
        state = {
            "alarm_event": make_event(severity=2, condition_log="too many open files"),
            "analysis_result": analysis,
        }
        out = await agentic_enricher_node(state, make_config(cfg))
        assert "analysis_result" not in out
        assert analysis.ai_message_severity is None

    async def test_track_a_no_downgrade_existing_ai(self, monkeypatch):
        self._patch_llm(monkeypatch)
        cfg = make_app_cfg(make_gate_cfg())
        # 기존 ai_message_severity=3인데 후보(2)가 낮음 → 하향 절대 금지(3 유지)
        analysis = make_analysis(ai_message_severity=3)
        state = {
            "alarm_event": make_event(severity=1, condition_log="too many open files"),
            "analysis_result": analysis,
        }
        out = await agentic_enricher_node(state, make_config(cfg))
        assert "analysis_result" not in out
        assert analysis.ai_message_severity == 3  # 강등 없음

    async def test_track_a_graceful_on_llm_error(self, monkeypatch):
        # create_llm 예외 → no-op {} (enricher 실패가 발송을 막지 않음)
        import src.llm as llm_mod

        def _boom(cfg):
            raise RuntimeError("llm down")

        monkeypatch.setattr(llm_mod, "create_llm", _boom)
        cfg = make_app_cfg(make_gate_cfg())
        state = {
            "alarm_event": make_event(severity=1, condition_log="too many open files"),
            "analysis_result": make_analysis(),
        }
        assert await agentic_enricher_node(state, make_config(cfg)) == {}


# ── 7. 그래프 배선(플래그 조합) ──────────────────────────────────────────
class TestGraphWiring:
    def _cfg(self, *, gate: bool, enricher: bool, history: bool = False) -> SimpleNamespace:
        return SimpleNamespace(
            alarm=SimpleNamespace(history_enabled=history),
            noise_gate=SimpleNamespace(
                enable_noise_gate=gate, enable_agentic_enricher=enricher
            ),
        )

    def test_wiring_includes_enricher_when_enabled(self):
        graph = build_alarm_graph(self._cfg(gate=True, enricher=True))
        nodes = set(graph.get_graph().nodes)
        assert "agentic_enricher" in nodes
        assert "notification_gate" in nodes

    def test_wiring_excludes_enricher_when_disabled(self):
        graph = build_alarm_graph(self._cfg(gate=True, enricher=False))
        nodes = set(graph.get_graph().nodes)
        assert "agentic_enricher" not in nodes
        assert "notification_gate" in nodes  # 게이트만 있고 enricher는 없음

    def test_wiring_gate_off_no_enricher(self):
        # 게이트 off면 enricher_enabled=False (gate and ...) → 노드 없음(회귀 0)
        graph = build_alarm_graph(self._cfg(gate=False, enricher=True, history=True))
        nodes = set(graph.get_graph().nodes)
        assert "agentic_enricher" not in nodes
        assert "notification_gate" not in nodes
