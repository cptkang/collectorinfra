"""holmes 토큰 예산(출력 상한·컨텍스트 창)을 pydantic 필드로 지정한다 (2026-09-17 MLX 검증 결함).

실측(holmesgpt 0.36.0 `core/llm.py`): litellm 목록에 없는 모델(사내 vLLM·로컬 OpenAI 호환 서버의
served name)은 `get_maximum_output_token()`이 `max(64000, 컨텍스트×12%)`, 컨텍스트는 폴백 200000이고,
`DefaultLLM.completion`이 그 값을 **매 요청 `max_tokens`로 보낸다**. OpenAI 호환 서버는 요청값을
서버 기본 상한보다 우선하므로 퇴행 루프 한 번이 64000토큰 생성을 붙든다.

holmes의 조정 수단은 `OVERRIDE_MAX_OUTPUT_TOKEN`·`OVERRIDE_MAX_CONTENT_SIZE` **프로세스 환경변수**
(임포트 시 1회 읽음)뿐이라 `.env`에 적으면 먹지 않는다(pydantic `env_file`은 `os.environ`에 주입되지
않는다 — Known Mistakes). 같은 이름의 `AgentSettings` 필드가 에이전트 인스턴스에 적용한다. LLM 호출 0.
"""

import litellm
import pytest

from sre_agent.diagnosis import DiagnosisAgent
from sre_agent.settings import AgentSettings


@pytest.fixture(autouse=True)
def _no_holmes_env_override(monkeypatch):
    # 프로세스 env가 holmes 임포트 시 상수로 굳었을 수 있다 — 테스트 입력을 명시값으로 고정
    import holmes.core.llm as hl

    monkeypatch.setattr(hl, "OVERRIDE_MAX_OUTPUT_TOKEN", None)
    monkeypatch.setattr(hl, "OVERRIDE_MAX_CONTENT_SIZE", None)


def _agent(**fields) -> DiagnosisAgent:
    s = AgentSettings(_env_file=None, model="openai/test-model", api_key="dummy",
                      api_base="http://127.0.0.1:9/v1", max_steps=3, gemini_api_key=None, **fields)
    return DiagnosisAgent(settings=s, toolsets={})


def _sent_max_tokens(agent: DiagnosisAgent, monkeypatch) -> object:
    """completion 1회를 가짜 litellm으로 받아 실제 전송될 max_tokens를 회수한다(네트워크 없음)."""
    captured: dict = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return litellm.ModelResponse()

    monkeypatch.setattr(litellm, "completion", fake_completion)
    agent.llm.llm.completion(messages=[{"role": "user", "content": "q"}])
    return captured.get("max_tokens")


def test_unset_fields_keep_holmes_defaults(monkeypatch):
    """미설정이면 holmes 동작 그대로(비트 동일) — 알 수 없는 모델은 64000 · 폴백 컨텍스트."""
    agent = _agent()
    llm = agent.llm.llm

    assert llm.max_context_size is None
    assert llm.get_maximum_output_token() == 64000
    assert _sent_max_tokens(agent, monkeypatch) == 64000


def test_fields_bound_request_and_context(monkeypatch):
    agent = _agent(override_max_content_size=32768, override_max_output_token=4096)
    llm = agent.llm.llm

    assert llm.get_context_window_size() == 32768
    # 압축·초과 판정의 출력 예약분과 요청 max_tokens가 같은 값이어야 한다
    assert llm.get_maximum_output_token() == 4096
    assert _sent_max_tokens(agent, monkeypatch) == 4096


def test_field_takes_precedence_over_holmes_process_env(monkeypatch):
    import holmes.core.llm as hl

    monkeypatch.setattr(hl, "OVERRIDE_MAX_OUTPUT_TOKEN", 2048)
    assert _agent().llm.llm.get_maximum_output_token() == 2048            # 필드 미설정 → holmes env
    assert _agent(override_max_output_token=4096).llm.llm.get_maximum_output_token() == 4096


def test_fields_load_from_env_file(tmp_path):
    """`.env`에 적은 값이 필드로 들어온다 — holmes 환경변수로는 안 되던 경로."""
    env = tmp_path / ".env"
    env.write_text("OVERRIDE_MAX_OUTPUT_TOKEN=2048\nOVERRIDE_MAX_CONTENT_SIZE=30000\n")

    s = AgentSettings(_env_file=str(env))

    assert s.override_max_output_token == 2048
    assert s.override_max_content_size == 30000


@pytest.mark.parametrize("field", ["override_max_output_token", "override_max_content_size"])
def test_non_positive_rejected(field):
    with pytest.raises(ValueError):
        AgentSettings(_env_file=None, **{field: 0})
