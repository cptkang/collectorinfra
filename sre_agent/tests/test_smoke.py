"""임포트 및 에이전트 구성 스모크 테스트 — LLM 호출 없이 검증한다."""

from sre_agent.diagnosis import DiagnosisAgent, DiagnosisResult
from sre_agent.settings import AgentSettings


def make_settings(**overrides) -> AgentSettings:
    # 검증 대상 필드를 명시해 .env 누수를 차단한다
    defaults = {"model": "test/model", "api_key": None, "max_steps": 3}
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def test_holmes_importable():
    from holmes import get_version

    assert get_version()


def test_agent_construction_without_llm_init():
    agent = DiagnosisAgent(settings=make_settings())
    assert agent.settings.model == "test/model"
    assert agent._llm is None


def test_diagnosis_result_defaults():
    result = DiagnosisResult(answer="ok")
    assert result.tool_calls == []
    assert result.total_tokens == 0
