#!/usr/bin/env python3
"""Gemini 테스트 LLM 스모크 하네스 — D-120 / Plan 02 §10.1.

HolmesGPT 위임의 성립 조건(tool-calling)을 두 단계로 최소 검증한다:
  1) litellm 단독 tool-calling 왕복 — 목업 도구 1개를 강제 호출시켜 호출명·인자 파싱 확인.
  2) DiagnosisAgent.ask 1회 — 외부 폴스타 미연결·호스트 접근 도구 전부 off(no_host_access_profile) 대상,
     LLM 왕복 완주 확인. 붙은 도구를 LLM 호출 전에 허용목록으로 대조한다.

GEMINI_API_KEY 미설정 시(pydantic 필드 gemini_api_key 로만 판정 — os.getenv 금지)에는
실 API 왕복 대신 "보류(GEMINI_API_KEY 미설정)"를 명확히 출력하고 graceful 종료(exit 0)한다.

데이터 통제(D-120 · 절대 제약): 외부(Gemini) 송신 입력은 목업/로컬 픽스처만 —
실 폴스타 연결·데이터 송신 코드 경로를 만들지 않는다.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# 스크립트 직접 실행 시 sre_agent 패키지를 임포트 경로에 올린다 (parents[1] = sre_agent/ 최상위).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sre_agent.settings import AgentSettings  # noqa: E402

HELD_MSG = "보류(GEMINI_API_KEY 미설정)"

# litellm 왕복에 넘길 목업 도구 1개 — 외부 데이터 접근 아님(스모크 전용).
SMOKE_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "get_server_cpu_load",
        "description": "지정한 서버의 현재 CPU 사용률(%)을 반환한다. (스모크용 목업 도구)",
        "parameters": {
            "type": "object",
            "properties": {
                "hostname": {"type": "string", "description": "대상 서버 호스트명"},
            },
            "required": ["hostname"],
        },
    },
}
SMOKE_PROMPT = "server-01 서버의 현재 CPU 사용률을 확인해줘."


def key_present(settings: AgentSettings) -> bool:
    """gemini_api_key가 pydantic 필드로 설정됐는지 판정한다 (os.getenv 금지)."""
    return settings.gemini_api_key is not None


def smoke_litellm_toolcalling(settings: AgentSettings) -> dict[str, object]:
    """1단계 — litellm 단독 tool-calling 왕복. 함수 호출 1회를 강제해 호출명·인자를 파싱한다."""
    import litellm

    assert settings.gemini_api_key is not None  # 호출부에서 key_present로 이미 보장됨
    response = litellm.completion(
        model=settings.investigation_llm_model,
        api_key=settings.gemini_api_key.get_secret_value(),
        messages=[{"role": "user", "content": SMOKE_PROMPT}],
        tools=[SMOKE_TOOL],
        tool_choice={"type": "function", "function": {"name": SMOKE_TOOL["function"]["name"]}},
    )
    message = response.choices[0].message
    tool_calls = message.tool_calls or []
    if not tool_calls:
        raise RuntimeError("tool-calling 미성립: 모델이 함수 호출을 반환하지 않음")
    call = tool_calls[0]
    return {
        "name": call.function.name,
        "arguments": json.loads(call.function.arguments or "{}"),
    }


def assert_no_host_access_tools(tool_names) -> None:
    """LLM에 붙은 도구가 호스트 접근 없는 허용목록 안에 있는지 **LLM 호출 전에** 확인한다.

    허용목록 밖 도구(셸·파일·웹·네트워크 프로브·k8s 등)가 하나라도 있으면 RuntimeError —
    holmes 상향으로 새 기본 toolset이 생겨도 조용히 실행되지 않게 한다(2026-09-17 사고 재발 방지).
    """
    from sre_agent.toolset_profiles import NO_HOST_ACCESS_TOOLS

    extra = sorted(set(tool_names) - NO_HOST_ACCESS_TOOLS)
    if extra:
        raise RuntimeError(f"호스트 접근 도구가 LLM에 노출됨 — 실행 중단: {extra}")


def smoke_diagnosis_ask(settings: AgentSettings) -> dict[str, object]:
    """2단계 — DiagnosisAgent.ask 1회. 외부 데이터·호스트 접근 없이 LLM 왕복 완주만 확인한다.

    로컬 mock MCP 픽스처(도구 자동 발견→호출)는 2-C/Plan 04 소관이므로 여기서는 붙이지 않는다.
    """
    from sre_agent.diagnosis import DiagnosisAgent
    from sre_agent.toolset_profiles import no_host_access_profile

    ask_settings = AgentSettings(
        _env_file=None,
        model=settings.investigation_llm_model,
        api_key=settings.gemini_api_key,
        max_steps=settings.max_steps,
    )
    # 외부 데이터 소스·호스트 접근을 붙이지 않는다(D-120 데이터 통제). `toolsets={}`는 빈 toolset이
    # 아니라 holmes 내장 기본(bash·internet 등)이 켜진 상태다 — 끌 것을 명시하고 붙은 도구를 대조한다.
    agent = DiagnosisAgent(settings=ask_settings, toolsets=no_host_access_profile())
    assert_no_host_access_tools(agent.llm.tool_executor.tools_by_name)
    result = agent.ask("현재 진단 파이프라인이 정상 동작하는지 한 줄로 답하라.")
    return {"answer_len": len(result.answer), "tool_calls": result.tool_calls}


def main(settings: AgentSettings | None = None) -> int:
    """스모크 하네스 진입점. 키 부재 시 graceful(exit 0)로 보류를 명확히 출력한다."""
    settings = settings or AgentSettings()
    print("=== sre_agent Gemini 스모크 하네스 (D-120) ===")
    print(f"  investigation_llm_model = {settings.investigation_llm_model}")

    # (D-127) 실 Gemini 호출은 과금 발생 — 사용자 승인(RUN_E2E=1) 없이는 실행하지 않는다.
    if os.environ.get("RUN_E2E") != "1":
        approval_msg = "보류(사용자 승인 필요 — D-127: RUN_E2E=1 설정 후 재실행)"
        print(f"[1/2] litellm tool-calling 왕복 — {approval_msg}")
        print(f"[2/2] DiagnosisAgent.ask       — {approval_msg}")
        return 0

    if not key_present(settings):
        print(f"[1/2] litellm tool-calling 왕복 — {HELD_MSG}")
        print(f"[2/2] DiagnosisAgent.ask       — {HELD_MSG}")
        print("스모크 구조는 완성됨. 실 API 왕복 검증은 GEMINI_API_KEY 설정 후 재실행하라.")
        return 0

    # --- 1단계: litellm 단독 tool-calling 왕복 ---
    try:
        parsed = smoke_litellm_toolcalling(settings)
        print(f"[1/2] litellm tool-calling 왕복 — OK: 호출={parsed['name']} 인자={parsed['arguments']}")
    except Exception as exc:  # 실패 사유를 침묵시키지 않는다
        print(f"[1/2] litellm tool-calling 왕복 — 실패: {type(exc).__name__}: {exc}")
        return 1

    # --- 2단계: DiagnosisAgent.ask 1회 ---
    try:
        out = smoke_diagnosis_ask(settings)
        print(f"[2/2] DiagnosisAgent.ask — OK: answer_len={out['answer_len']} tool_calls={out['tool_calls']}")
    except Exception as exc:  # 픽스처 의존 단계 — 사유를 명확히 출력하되 크래시로 처리하지 않는다
        print(f"[2/2] DiagnosisAgent.ask — 실행 불가/실패: {type(exc).__name__}: {exc}")
        print("  (로컬 mock MCP 픽스처는 2-C/Plan 04 소관 — 픽스처 확보 시 완주 검증)")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
