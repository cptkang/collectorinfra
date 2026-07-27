"""에이전트 설정 — .env 로딩은 pydantic-settings 필드로만 판정한다 (os.getenv 금지)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    model: str = "anthropic/claude-sonnet-5"
    api_key: SecretStr | None = None
    max_steps: int = 10

    # 폴스타 MCP 접속 설정 (Plan 06 §94 · D-119). mcp_server(Plan 04)가 노출하는
    # SSE 엔드포인트로, DiagnosisAgent(mcp_servers=...)에 등록해 소비한다.
    # (D-119) prometheus_url·prometheus_auth_header는 여기 두지 않는다 —
    # Prometheus 접속 설정은 mcp_server 측(config.toml·서버 .env)으로 일원화한다.
    # 토큰은 SecretStr로 pydantic 필드로만 판정한다(env: POLESTAR_MCP_TOKEN,
    # 미설정 시 None → 무인증 로컬 픽스처 경로).
    polestar_mcp_url: str = "http://localhost:9099/sse"
    polestar_mcp_token: SecretStr | None = None

    # 개발·테스트 LLM — Gemini API (D-120). 운영 LLM(model)과 분리한다.
    # 기본값은 D-021 준수(gemini-2.5-*는 2026-06-17 deprecated·사용 금지 → 권장 기본
    # gemini-2.0-flash 채택)이며, litellm 1.89.0 실측으로 tool-calling(function-calling)
    # 지원을 확인했다(supports_function_calling=True). gemini_api_key는 SecretStr | None
    # 으로 pydantic 필드로만 판정한다(env: GEMINI_API_KEY, 미설정 시 None → 스모크·e2e 보류).
    investigation_llm_model: str = "gemini/gemini-2.0-flash"
    gemini_api_key: SecretStr | None = None

    # 조사 서비스(interface/mcp_service) 정적 Bearer 토큰 (Plan 05 §5-인증).
    # None이면 무인증(로컬/개발). SecretStr로 pydantic 필드로만 판정한다
    # (env: SERVICE_BEARER_TOKEN, os.getenv 금지). 협의 후 mTLS 승격 여지.
    service_bearer_token: SecretStr | None = None

    # ── 조사 dispatcher 폭주 방지 가드 (Plan 02 §4·§10 — 전부 기본 off/보수값) ──
    # 스칼라 필드이므로 nested Field(default_factory=...)는 불요(중첩 모델 없음).
    # 조사 1건 **전체** 타임아웃(per-call 아님·asyncio.wait_for). HolmesGPT 다단계
    # 조사를 감안해 300s(원본 45s보다 상향). collectorinfra MCP 동기 타임아웃(60s)보다
    # 길어 submit/poll 비동기 잡 패턴이 성립한다.
    investigation_timeout_seconds: int = 300
    # 동일 fingerprint 재조사 최소 간격(초). None이면 TTL dedup off
    # (JobStore active-fingerprint dedup만 동작). 켜면 완료된 조사도 이 간격 내 재조사 억제.
    investigation_dedup_ttl_seconds: float | None = None
    # 동시 조사 상한(세마포어). 보수적 기본 2.
    investigation_max_concurrent: int = 2
    # 시간당 조사 횟수 상한. None이면 예산 가드 off. 초과 시 신규 조사 거부.
    investigation_hourly_budget: int | None = None
    # 중요도 2차 판정(severity_judge) 활성화. 기본 off — 켜야 도구 원시 출력
    # 시그니처 매칭을 수행한다(escalate-only). off면 게이트 판정을 그대로 승계.
    severity_judge_enabled: bool = False
