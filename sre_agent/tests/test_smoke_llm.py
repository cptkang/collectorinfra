"""smoke_llm 하네스 구조 테스트 — 임포트 가능·키 부재 시 graceful(실 API 호출 없음)."""

import pytest

import smoke_llm
from sre_agent.settings import AgentSettings


def _no_key_settings() -> AgentSettings:
    # gemini_api_key를 명시적으로 None으로 고정 — 환경변수·.env 누수 차단
    return AgentSettings(_env_file=None, gemini_api_key=None)


def _with_key_settings() -> AgentSettings:
    return AgentSettings(_env_file=None, gemini_api_key="dummy")


def test_module_importable():
    assert hasattr(smoke_llm, "main")
    assert hasattr(smoke_llm, "smoke_litellm_toolcalling")
    assert hasattr(smoke_llm, "smoke_diagnosis_ask")


def test_key_present_judged_by_field():
    assert smoke_llm.key_present(_no_key_settings()) is False
    assert smoke_llm.key_present(_with_key_settings()) is True


def test_main_graceful_when_key_absent(capsys, monkeypatch):
    # (D-127) 실 Gemini 호출은 RUN_E2E=1 승인 게이트 뒤에 있다 — 이 게이트를 통과시킨 뒤
    # 키 부재 경로(실 API 호출 없이 exit 0 + HELD_MSG)를 검증한다. RUN_E2E=1이라도
    # 키가 없으면 API를 호출하지 않고 HELD_MSG로 graceful 종료해야 한다.
    monkeypatch.setenv("RUN_E2E", "1")
    rc = smoke_llm.main(settings=_no_key_settings())
    assert rc == 0
    out = capsys.readouterr().out
    assert smoke_llm.HELD_MSG in out


def test_main_holds_without_run_e2e_approval(capsys, monkeypatch):
    # (D-127) RUN_E2E 미승인 시 키 유무와 무관하게 실 API를 호출하지 않고 승인 요구로 보류.
    monkeypatch.delenv("RUN_E2E", raising=False)
    rc = smoke_llm.main(settings=_no_key_settings())
    assert rc == 0
    out = capsys.readouterr().out
    assert "D-127" in out and "RUN_E2E=1" in out


def test_host_access_guard_blocks_before_llm():
    # 허용목록 밖 도구가 하나라도 붙으면 LLM 호출 전에 끊는다(2026-09-17 kubectl 실행 사고)
    smoke_llm.assert_no_host_access_tools(["TodoWrite", "fetch_skill"])
    with pytest.raises(RuntimeError, match="bash"):
        smoke_llm.assert_no_host_access_tools(["TodoWrite", "bash"])


def test_smoke_diagnosis_ask_uses_no_host_profile_and_guards(monkeypatch):
    # 실 LLM 없이: 스모크 2단계가 호스트 접근 off 프로파일로 에이전트를 만들고, ask 전에 도구를 대조한다.
    import sre_agent.diagnosis as diagnosis
    from sre_agent.diagnosis import DiagnosisResult
    from sre_agent.toolset_profiles import no_host_access_profile

    seen: dict = {}

    class FakeAgent:
        def __init__(self, settings, toolsets=None, mcp_servers=None):
            seen["toolsets"] = toolsets
            self.llm = type("L", (), {"tool_executor": type("E", (), {"tools_by_name": {"bash": 1}})()})()

        def ask(self, question):
            seen["asked"] = True
            return DiagnosisResult(answer="ok")

    monkeypatch.setattr(diagnosis, "DiagnosisAgent", FakeAgent)
    with pytest.raises(RuntimeError):
        smoke_llm.smoke_diagnosis_ask(_with_key_settings())
    assert seen["toolsets"] == no_host_access_profile()
    assert "asked" not in seen   # 노출 도구가 있으면 LLM을 부르지 않는다


def test_smoke_tool_definition_single_function():
    # tool-calling 강제를 위한 목업 도구는 함수 1개여야 한다.
    assert smoke_llm.SMOKE_TOOL["type"] == "function"
    assert smoke_llm.SMOKE_TOOL["function"]["name"] == "get_server_cpu_load"
