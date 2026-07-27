"""AgentSettings 확장 필드 검증 (D-120) — investigation_llm_model·gemini_api_key(SecretStr)·.env 누수 차단."""

from pydantic import SecretStr

from sre_agent.settings import AgentSettings


def make_settings(**overrides) -> AgentSettings:
    # 검증 대상 필드를 명시해 .env / 환경변수 누수를 차단한다
    defaults = {
        "model": "test/model",
        "api_key": None,
        "max_steps": 3,
        "investigation_llm_model": "gemini/gemini-2.0-flash",
        "gemini_api_key": None,
    }
    return AgentSettings(_env_file=None, **{**defaults, **overrides})


def test_default_investigation_llm_model(monkeypatch):
    # 환경변수 누수를 명시적으로 차단하고 클래스 기본값을 확인한다.
    monkeypatch.delenv("INVESTIGATION_LLM_MODEL", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    s = AgentSettings(_env_file=None, gemini_api_key=None)
    # 기본값은 D-021 준수(gemini-2.5-* 사용 금지 → 권장 gemini-2.0-flash),
    # litellm 1.89.0 실측으로 tool-calling(function-calling)을 지원하는 gemini 모델.
    assert s.investigation_llm_model == "gemini/gemini-2.0-flash"


def test_gemini_api_key_is_secretstr():
    s = make_settings(gemini_api_key="secret-value")
    assert isinstance(s.gemini_api_key, SecretStr)
    assert s.gemini_api_key.get_secret_value() == "secret-value"
    # repr에 원문이 노출되지 않아야 한다
    assert "secret-value" not in repr(s)


def test_gemini_api_key_default_none():
    assert make_settings().gemini_api_key is None


def test_env_file_none_blocks_leak():
    # _env_file=None + 필드 명시로 .env 값이 새어들지 않는다
    s = make_settings(investigation_llm_model="x/y")
    assert s.investigation_llm_model == "x/y"


def test_existing_fields_preserved():
    s = make_settings()
    assert s.model == "test/model"
    assert s.api_key is None
    assert s.max_steps == 3
