"""프록시 설정 — `fabrix_proxy/.env` + `.encenv` (plans/148 §3.2·§3.6·§3.7).

- pydantic-settings가 패키지 루트(`fabrix_proxy/`)의 `.env`·`.encenv`를 읽는다. 접두사 없이 키 이름
  그대로다. `env_file`은 `os.environ`에 주입되지 않으므로 설정 판단에 `os.getenv`를 쓰지 않는다.
- list/dict 값은 JSON으로 적는다(`FABRIX_PROXY_MODEL_ALIASES=["fabrix-tools"]`). 인라인 주석 금지.
- FabriX 자격증명(`FABRIX_BASE_URL`·`FABRIX_API_KEY`·`FABRIX_CLIENT_KEY`·`FABRIX_MODEL`)은 이
  프로세스에만 둔다(D-274 원칙). 소비자에게는 프록시 토큰만 준다.
- 검증 오류 메시지에 입력값을 싣지 않는다(`hide_input_in_errors` — 자격증명 누출 방지).
- 테스트는 `ProxySettings(_env_file=None, ...)`로 필드를 명시해 `.env` 누수를 막는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # fabrix_proxy/ (자체 cwd)

ContentsMode = Literal["turns", "transcript"]
ProtocolLang = Literal["en", "ko"]
FewshotMode = Literal["none", "static", "dynamic"]
FewshotPlacement = Literal["system", "contents"]


class ConfigError(ValueError):
    """기동을 거부해야 하는 설정 오류. 메시지에 자격증명 값을 싣지 않는다."""


class ProxySettings(BaseSettings):
    """프록시 설정 전체. 기동 시 1회 읽는다."""

    model_config = SettingsConfigDict(
        env_file=(PACKAGE_ROOT / ".env", PACKAGE_ROOT / ".encenv"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        hide_input_in_errors=True,
    )

    # ── 프록시 표면 ──
    fabrix_proxy_token: str = ""
    fabrix_proxy_host: str = "127.0.0.1"
    fabrix_proxy_port: int = 9095
    fabrix_proxy_log_level: str = "INFO"
    fabrix_proxy_model_aliases: list[str] = Field(default_factory=lambda: ["fabrix-tools"])

    # ── 업스트림 KBGenAI (src/clients/fabrix_kbgenai.py 이식) ──
    fabrix_base_url: str = ""
    fabrix_api_key: str = ""
    fabrix_client_key: str = ""
    fabrix_model: str = ""
    # 현행 `httpx.AsyncClient(verify=False)` 동치가 기본값이다.
    fabrix_verify_ssl: bool = False
    # httpx read 간격(초) — 벽시계 총상한은 아래 total_timeout(D-198).
    fabrix_timeout: float = 300.0
    # 호출 1건(교정 재질의 포함) 벽시계 총상한(초).
    fabrix_total_timeout: float = 300.0
    # D-194 llmConfig — 비면 페이로드에 llmConfig 키를 넣지 않는다.
    fabrix_llm_config: dict[str, Any] = Field(default_factory=dict)
    # passthrough 대상(네이티브 OpenAI 호환 엔드포인트) — 비면 passthrough 불가(400).
    fabrix_native_url: str = ""
    fabrix_native_model: str = ""

    # ── 에뮬레이션 선택지(1단계 측정 축) ──
    fabrix_proxy_contents_mode: ContentsMode = "turns"
    fabrix_proxy_protocol_lang: ProtocolLang = "en"
    fabrix_proxy_protocol_file: str = ""
    fabrix_proxy_fewshot: FewshotMode = "static"
    fabrix_proxy_fewshot_placement: FewshotPlacement = "system"
    fabrix_proxy_fewshot_file: str = ""
    fabrix_proxy_repair_max: int = Field(default=1, ge=0)
    fabrix_proxy_passthrough: bool = False
    # PoC 전용 — 요청별 `fabrix_proxy_options` 덮어쓰기와 응답 `fabrix_proxy_diag`를 허용한다.
    fabrix_proxy_poc_mode: bool = False


def require_token(settings: ProxySettings) -> None:
    """프록시 토큰이 비어 있으면 기동을 거부한다(plans/148 §3.7)."""
    if not settings.fabrix_proxy_token.strip():
        raise ConfigError("FABRIX_PROXY_TOKEN이 비어 있다 — 프록시 토큰 없이 기동하지 않는다")
    if not settings.fabrix_proxy_model_aliases:
        raise ConfigError("FABRIX_PROXY_MODEL_ALIASES가 비어 있다 — 별칭이 하나 이상 필요하다")


def resolve_path(raw: str) -> Path:
    """설정 파일 경로를 해석한다. 상대 경로는 패키지 루트(자체 cwd) 기준이다."""
    path = Path(raw).expanduser()
    return path if path.is_absolute() else PACKAGE_ROOT / path
