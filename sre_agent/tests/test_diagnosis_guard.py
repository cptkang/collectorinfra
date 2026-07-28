"""DiagnosisAgent.ask의 step 상한 graceful 가드 단위 검증 (Plan 02 §12-④ · 결정적).

holmes가 max_steps 상한 도달 시 던지는 plain Exception을 하드 실패로 전파하지 않고
구조화 미완주(incomplete=True)로 반환하는지, 그 외 예외는 그대로 전파하는지를 실 LLM
없이 mock으로 결정적으로 고정한다.
"""

from unittest.mock import MagicMock, patch

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.settings import AgentSettings


def _agent(max_steps: int = 5) -> DiagnosisAgent:
    s = AgentSettings(_env_file=None, model="test/model", api_key=None, max_steps=max_steps)
    agent = DiagnosisAgent(settings=s, toolsets={})
    # lazy llm(create_toolcalling_llm)을 mock으로 대체 — prerequisite 검사·실 호출 우회.
    agent._llm = MagicMock()
    return agent


@patch("sre_agent.diagnosis.to_tool_records", return_value=[])
@patch("sre_agent.diagnosis.build_initial_ask_messages", return_value=[{"role": "user", "content": "q"}])
def test_ask_graceful_on_step_limit(_msgs, _rec):
    """step 상한 예외 → incomplete=True·사유 노출·하드 실패 없음."""
    agent = _agent(max_steps=5)
    agent._llm.call.side_effect = Exception("Too many LLM calls - exceeded max_steps: 5/5")

    r = agent.ask("svr-web-01 장애 원인을 조사하라")

    assert r.incomplete is True
    assert "미완주" in r.answer and "5" in r.answer  # max_steps 값 노출
    assert r.tool_calls == [] and r.tool_outputs == []


@patch("sre_agent.diagnosis.to_tool_records", return_value=[])
@patch("sre_agent.diagnosis.build_initial_ask_messages", return_value=[{"role": "user", "content": "q"}])
def test_ask_propagates_non_step_limit_exception(_msgs, _rec):
    """step 상한이 아닌 진짜 오류는 그대로 전파(dispatcher가 failed로 확정)."""
    agent = _agent()
    agent._llm.call.side_effect = RuntimeError("prometheus 연결 실패")

    try:
        agent.ask("조사")
        raise AssertionError("비-상한 예외는 전파되어야 한다")
    except RuntimeError as e:
        assert "prometheus" in str(e)


@patch("sre_agent.diagnosis.to_tool_records", return_value=[])
@patch("sre_agent.diagnosis.build_initial_ask_messages", return_value=[{"role": "user", "content": "q"}])
def test_ask_normal_completion_not_incomplete(_msgs, _rec):
    """정상 완주는 incomplete=False."""
    agent = _agent()
    result = MagicMock()
    result.result = "CPU 정상. node_load1 인용."
    result.tool_calls = []
    result.total_tokens = 123
    result.total_cost = 0.01
    agent._llm.call.return_value = result

    r = agent.ask("조사")

    assert r.incomplete is False
    assert r.answer == "CPU 정상. node_load1 인용."
    assert r.total_tokens == 123
