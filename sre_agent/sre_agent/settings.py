"""에이전트 설정 — .env 로딩은 pydantic-settings 필드로만 판정한다 (os.getenv 금지)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model: str = "anthropic/claude-sonnet-5"
    api_key: SecretStr | None = None
    max_steps: int = 10

    # 개발·테스트 LLM — Gemini API (D-120). 운영 LLM(model)과 분리한다.
    # 기본값은 D-021 준수(gemini-2.5-*는 2026-06-17 deprecated·사용 금지 → 권장 기본
    # gemini-2.0-flash 채택)이며, litellm 1.89.0 실측으로 tool-calling(function-calling)
    # 지원을 확인했다(supports_function_calling=True). gemini_api_key는 SecretStr | None
    # 으로 pydantic 필드로만 판정한다(env: GEMINI_API_KEY, 미설정 시 None → 스모크·e2e 보류).
    investigation_llm_model: str = "gemini/gemini-2.0-flash"
    gemini_api_key: SecretStr | None = None
