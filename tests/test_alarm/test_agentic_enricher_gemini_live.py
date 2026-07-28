"""E5 Advisory Enricher — Gemini API 기반 트랙 B live 통합 테스트 (Plan 52 E5, D-048.7).

vLLM 인프라 없이 **Gemini 네이티브 tool-calling**으로 agentic 경로(트랙 B)를 실제로 검증한다.
`ORCHESTRATOR_PROVIDER=gemini` + gemini API 키(`LLM_GEMINI_API_KEY`/`GOOGLE_API_KEY` 또는
`ORCHESTRATOR_API_KEY`)가 설정된 환경에서만 동작하고, 키가 없으면 **모듈 전체 skip**한다(CI 안전).

검증 대상:
    - `create_orchestrator_llm`(provider=gemini)이 `bind_tools`를 지원하고,
      `run_signal_react_loop`가 실 Gemini로 읽기전용 신호 수집 도구를 호출한다(agentic 경로).
    - `agentic_enricher_node`가 실 Gemini 트랙 B로 완주하며 **승격 전용·하향 없음** 불변을 지킨다.

주의: LLM 응답은 비결정적이므로, 상향이 일어났다면 반드시 시그니처 상향값(=3)이어야 한다는
**불변식**을 조건부로 단언한다(모델이 도구를 호출하지 않아도 테스트가 거짓 실패하지 않도록).
직접 ReAct 루프 테스트는 명시적 도구 사용 프롬프트로 최소 1회 호출을 기대한다(flash 계열 기준).
"""

from __future__ import annotations

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

pytest.importorskip("langchain_google_genai")

from src.alarm.application.nodes.agentic_enricher import (
    _select_backend,
    agentic_enricher_node,
)
from src.alarm.domain.alarm import AlarmEvent
from src.alarm.infrastructure.noise_signal_tools import (
    build_noise_signal_tools,
    run_signal_react_loop,
)
from src.alarm.prompts.agentic_enricher import AGENTIC_ENRICHER_SYSTEM_PROMPT
from src.config import AppConfig
from src.llm import create_orchestrator_llm

REF = datetime(2026, 6, 30, 10, 0, 0)

# 키를 리포지토리 설정 해석기(.env/.encenv 포함)로 확인 — 없으면 모듈 skip.
_REAL = AppConfig()
_KEY = _REAL.orchestrator.api_key or _REAL.llm.gemini_api_key
_MODEL = _REAL.llm.gemini_model or "gemini-2.5-flash-lite"

# (D-127) 실 Gemini 호출은 과금 발생 — 키 존재만으로 실행 금지, 사용자 승인(RUN_E2E=1) 필수.
# 종전 "키 있으면 실행" 게이팅은 키가 .encenv에 상존하는 환경에서 기본 스위트가 무단 과금
# 호출을 하게 만들었다(known_mistakes 2026-07-28).
pytestmark = pytest.mark.skipif(
    not (os.environ.get("RUN_E2E") == "1" and _KEY),
    reason="실 Gemini 호출은 사용자 승인 필수(D-127) — RUN_E2E=1 + 키 설정 시에만 실행",
)

# OOM 로그 → 결정적 시그니처 스캐너가 severity=3 후보를 산출(상향 대상).
OOM_LOG = "kernel: Out of memory: Killed process 1 (java) score 900 total-vm:8192kB"


def _gemini_cfg(**gate_overrides) -> SimpleNamespace:
    """provider=gemini로 강제한 테스트 cfg(로컬 .env provider와 무관하게 포터블).

    실 키/모델은 AppConfig 해석값을 재사용하되, orchestrator/llm은 SimpleNamespace로 감싸
    `create_orchestrator_llm`(gemini 경로)이 소비하는 필드만 채운다.
    """
    gate = dict(
        enable_agentic_enricher=True,
        agentic_enricher_message_alarms_only=True,
        agentic_enricher_fallback="semantic_routing",
        agentic_enricher_timeout_seconds=30.0,  # 실 API 왕복 여유
        agentic_enricher_max_tool_calls=3,
    )
    gate.update(gate_overrides)
    return SimpleNamespace(
        orchestrator=SimpleNamespace(
            provider="gemini", api_key=_KEY, model=_MODEL, base_url="", health_timeout=3
        ),
        llm=SimpleNamespace(gemini_api_key=_KEY, gemini_model=_MODEL),
        noise_gate=SimpleNamespace(**gate),
    )


def _event(**kwargs) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="srv-log01",
        hostname="hlog01",
        ip_address="10.1.2.9",
        resource_ancestry="/Servers/srv/LogMonitor",
        alarm_id="LOG-LIVE-001",
        severity=1,
        alarm_status="NOT_ACK",
        resource_type="server.LogMonitor",
        resource_name="syslog",
        alarm_name="로그 패턴 감지",
        alarm_time=REF,
        conditions="",
        condition_log=OOM_LOG,
    )
    defaults.update(kwargs)
    if "is_clear" not in defaults:
        defaults["is_clear"] = defaults["severity"] == 0
    return AlarmEvent(**defaults)


class TestGeminiTrackBLive:
    def test_backend_selects_track_b_for_gemini(self):
        # 결정적: provider=gemini + 키 존재 → 네트워크 없이 트랙 B 선택.
        cfg = _gemini_cfg()
        assert _select_backend(cfg.noise_gate, cfg) == "track_b"

    async def test_react_loop_calls_tool_live(self):
        # 실 Gemini bind_tools ReAct가 읽기전용 신호 수집 도구를 실제 호출하는지 검증.
        cfg = _gemini_cfg()
        collector: list = []
        event = _event()
        tools = build_noise_signal_tools(None, event, collector=collector)
        bound = create_orchestrator_llm(cfg).bind_tools(tools)
        calls = await run_signal_react_loop(
            bound,
            tools,
            system_prompt=AGENTIC_ENRICHER_SYSTEM_PROMPT,
            user_prompt=(
                "다음 알람 메시지의 위험 신호를 보강하려 한다. 사용 가능한 읽기전용 도구를 사용해 "
                f"메시지 시그니처를 반드시 스캔하라.\n메시지: {OOM_LOG}"
            ),
            max_tool_calls=3,
        )
        assert calls >= 1  # Gemini가 도구를 최소 1회 호출
        assert calls <= 3  # 상한 준수
        assert any(
            isinstance(c, dict) and c.get("signal") == "message_signature" for c in collector
        )

    async def test_node_end_to_end_upgrade_only_live(self):
        # 전체 노드를 실 Gemini 트랙 B로 완주 — 예외 없이 dict 반환, 상향 시 반드시 3(하향 없음).
        cfg = _gemini_cfg()
        analysis = SimpleNamespace(ai_message_severity=None, ai_severity_reason="")
        state = {"alarm_event": _event(severity=1), "analysis_result": analysis}
        out = await agentic_enricher_node(
            state, {"configurable": {"app_config": cfg, "noise_repo": None}}
        )
        assert isinstance(out, dict)  # graceful — 예외 없이 완료
        # 상향이 일어났다면 승격 전용 불변: 반드시 시그니처 상향값 3, backend=track_b.
        if "analysis_result" in out:
            assert out["analysis_result"].ai_message_severity == 3
            assert out["noise_context"]["agentic"]["backend"] == "track_b"
            assert "message_signature" in out["noise_context"]["agentic"]["signals"]
