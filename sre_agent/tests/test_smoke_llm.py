"""smoke_llm 하네스 구조 테스트 — 임포트 가능·키 부재 시 graceful(실 API 호출 없음)."""

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


def test_main_graceful_when_key_absent(capsys):
    # 키 부재 시 실 API 호출 없이 exit 0 + '보류' 문구를 명확히 출력해야 한다.
    rc = smoke_llm.main(settings=_no_key_settings())
    assert rc == 0
    out = capsys.readouterr().out
    assert smoke_llm.HELD_MSG in out


def test_smoke_tool_definition_single_function():
    # tool-calling 강제를 위한 목업 도구는 함수 1개여야 한다.
    assert smoke_llm.SMOKE_TOOL["type"] == "function"
    assert smoke_llm.SMOKE_TOOL["function"]["name"] == "get_server_cpu_load"
