"""설정 로드 모듈.

환경변수에서 애플리케이션 설정을 읽어온다.
pydantic-settings를 사용하여 타입 안전한 설정 관리를 제공한다.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class LLMConfig(BaseSettings):
    """LLM 관련 설정."""

    provider: Literal["ollama", "fabrix", "gemini"] = "ollama"
    model: str = "llama3.1:8b"

    # Ollama 설정
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_timeout: int = 180

    # Gemini 설정
    gemini_api_key: str = ""
    gemini_model: str = ""

    # FabriX 설정
    fabrix_base_url: str = ""
    fabrix_api_key: str = ""
    fabrix_client_key: str = ""
    fabrix_chat_model: str = ""

    model_config = {"env_prefix": "LLM_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """환경변수를 직접 읽어 보정한다."""
        import os

        # FabriX 환경변수 (LLM_ 접두사 또는 직접)
        if not self.fabrix_base_url:
            self.fabrix_base_url = os.getenv("FABRIX_BASE_URL", "")
        if not self.fabrix_api_key:
            self.fabrix_api_key = os.getenv("FABRIX_API_KEY", "")
        if not self.fabrix_client_key:
            self.fabrix_client_key = os.getenv("FABRIX_CLIENT_KEY", "")
        if not self.fabrix_chat_model:
            self.fabrix_chat_model = os.getenv("FABRIX_CHAT_MODEL", "")

        # Gemini API 키
        if not self.gemini_api_key:
            self.gemini_api_key = os.getenv("GOOGLE_API_KEY", "")

        # Ollama API 키 (게이트웨이용)
        if not self.ollama_api_key:
            self.ollama_api_key = os.getenv("LLM_API_KEY", "")


class DBHubConfig(BaseSettings):
    """MCP 서버 접속 설정.

    DB 연결 정보는 포함하지 않는다 (MCP 서버 VM이 관리).
    클라이언트는 서버 URL만 보유한다.
    """

    server_url: str = "http://localhost:9090/sse"   # MCP 서버 SSE 엔드포인트
    source_name: str = ""                              # 기본 쿼리 대상 소스 (DBHUB_SOURCE_NAME으로 설정)
    mcp_call_timeout: int = 60                       # MCP 호출 전체 대기시간 (초)

    model_config = {"env_prefix": "DBHUB_", "env_file": ".env", "extra": "ignore"}


class QueryConfig(BaseSettings):
    """클라이언트 측 쿼리 정책.

    DB 레벨 제한(query_timeout, max_rows)은 MCP 서버에서 관리한다.
    클라이언트는 재시도 횟수와 SQL 생성 기본 LIMIT만 관리한다.
    """

    max_retry_count: int = 3   # MCP 호출 재시도 횟수
    default_limit: int = 1000  # SQL 생성 시 기본 LIMIT

    # 데이터 충분성 검사 임계값 (0.0 ~ 1.0)
    sufficiency_required_threshold: float = 0.7   # hint/synonym 매핑
    sufficiency_optional_threshold: float = 0.5   # llm_inferred 매핑

    model_config = {"env_prefix": "QUERY_", "env_file": ".env", "extra": "ignore"}


class SecurityConfig(BaseSettings):
    """보안 관련 설정."""

    sensitive_columns: list[str] = [
        "password", "passwd", "pwd",
        "secret", "secret_key",
        "token", "access_token", "refresh_token",
        "api_key", "apikey",
        "private_key", "priv_key",
        "credential", "credentials",
        "ssn", "social_security",
        "credit_card", "card_number",
        "pin", "pin_code",
        "auth", "authorization",
    ]
    mask_pattern: str = "***MASKED***"
    partial_mask_columns: list[str] = []
    mask_ip: bool = False
    mask_email: bool = False

    model_config = {"env_prefix": "SECURITY_", "env_file": ".env", "extra": "ignore"}


class ServerConfig(BaseSettings):
    """API 서버 설정."""

    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = ["*"]
    query_timeout: int = 60
    file_query_timeout: int = 120

    model_config = {"env_prefix": "API_", "env_file": ".env", "extra": "ignore"}


class AdminConfig(BaseSettings):
    """운영자 인증 설정."""

    username: str = "admin"
    password: str = "admin123"
    jwt_secret: str = ""
    jwt_expire_hours: int = 24

    model_config = {"env_prefix": "ADMIN_", "env_file": [".env", ".encenv"], "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """JWT 시크릿이 비어있으면 자동 생성한다."""
        import secrets

        if not self.jwt_secret:
            self.jwt_secret = secrets.token_hex(32)


class AuthConfig(BaseSettings):
    """사용자 인증 설정.

    AUTH_ENABLED=false (기본값): 개발 단계에서 인증 없이 모든 기능 동작.
    AUTH_ENABLED=true: 사용자 로그인 필수.
    """

    enabled: bool = False
    auth_db_url: str = ""
    jwt_expire_hours: int = 8
    max_login_attempts: int = 5
    lockout_minutes: int = 30
    password_min_length: int = 8
    default_allowed_db_ids: str = ""

    model_config = {"env_prefix": "AUTH_", "env_file": [".env", ".encenv"], "extra": "ignore"}


class MultiDBConfig(BaseSettings):
    """멀티 DB 라우팅 설정.

    연결 문자열은 MCP 서버 VM이 관리한다.
    클라이언트는 활성 DB 목록만 관리하여 시멘틱 라우팅에 사용한다.
    활성 DB 목록은 MCP 서버의 list_sources 도구로 동적 조회하거나,
    환경변수 ACTIVE_DB_IDS로 명시적으로 설정할 수 있다.
    """

    # 활성 DB ID 목록 (쉼표 구분, 환경변수로 설정)
    # 예: ACTIVE_DB_IDS=polestar,cloud_portal,itsm,itam
    active_db_ids_csv: str = ""

    model_config = {"env_prefix": "MULTI_DB_", "env_file": ".env", "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """환경변수를 직접 읽어 보정한다."""
        import os

        if not self.active_db_ids_csv:
            self.active_db_ids_csv = os.getenv("ACTIVE_DB_IDS", "")

    def get_active_db_ids(self) -> list[str]:
        """활성 DB 식별자 목록을 반환한다.

        Returns:
            활성 DB 식별자 목록
        """
        if not self.active_db_ids_csv:
            return []
        return [
            db_id.strip()
            for db_id in self.active_db_ids_csv.split(",")
            if db_id.strip()
        ]


class RedisConfig(BaseSettings):
    """Redis 관련 설정."""

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""
    ssl: bool = False
    socket_timeout: int = 5

    model_config = {"env_prefix": "REDIS_", "env_file": [".env", ".encenv"], "extra": "ignore"}


class AuditConfig(BaseSettings):
    """감사 로그 설정."""

    jsonl_enabled: bool = True
    db_enabled: bool = True
    retention_days: int = 90
    sensitive_tables: list[str] = []
    alert_on_failed_login: int = 5
    alert_on_large_result: int = 5000
    night_alert_start: int = 2
    night_alert_end: int = 6

    model_config = {"env_prefix": "AUDIT_", "env_file": ".env", "extra": "ignore"}


class SchemaCacheConfig(BaseSettings):
    """스키마 캐시 관련 설정."""

    cache_dir: str = ".cache/schema"
    enabled: bool = True
    backend: str = "redis"  # "redis" | "file"
    auto_generate_descriptions: bool = True
    fingerprint_ttl_seconds: int = 1800  # fingerprint 검증 주기 (기본 30분)

    model_config = {"env_prefix": "SCHEMA_CACHE_", "env_file": ".env", "extra": "ignore"}


class AppConfig(BaseSettings):
    """애플리케이션 전체 설정을 통합 관리한다."""

    llm: LLMConfig = LLMConfig()
    dbhub: DBHubConfig = DBHubConfig()
    query: QueryConfig = QueryConfig()
    security: SecurityConfig = SecurityConfig()
    server: ServerConfig = ServerConfig()
    admin: AdminConfig = AdminConfig()
    auth: AuthConfig = AuthConfig()
    multi_db: MultiDBConfig = MultiDBConfig()
    redis: RedisConfig = RedisConfig()
    schema_cache: SchemaCacheConfig = SchemaCacheConfig()
    audit: AuditConfig = AuditConfig()
    checkpoint_backend: Literal["sqlite", "postgres"] = "sqlite"
    checkpoint_db_url: str = "checkpoints.db"

    # DB 직접 연결 설정 (DBHub 대안 / 레거시 단일 DB)
    db_backend: Literal["dbhub", "direct"] = "direct"
    db_connection_string: str = ""

    # 시멘틱 라우팅 활성화 여부
    enable_semantic_routing: bool = False

    # Polestar 전용 프롬프트를 적용할 DB ID
    # .env에서 POLESTAR_DB_ID=polestar 로 설정하면
    # active_db_id가 이 값과 일치할 때 Polestar 전용 시스템 프롬프트를 사용한다.
    # 비어있으면 전용 프롬프트를 사용하지 않음 (범용 프롬프트 적용).
    polestar_db_id: str = ""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Phase 3: 멀티턴 대화 / Human-in-the-loop
    enable_sql_approval: bool = False         # SQL 승인 기능 활성화
    enable_structure_approval: bool = True    # 구조 분석 HITL 승인 (기본 활성화)
    conversation_max_turns: int = 20          # 대화 최대 턴 수
    conversation_ttl_hours: int = 24          # 대화 세션 유효 시간

    model_config = {"env_file": ".env", "extra": "ignore"}

    def model_post_init(self, __context: object) -> None:
        """시멘틱 라우팅 활성화를 자동 판단한다."""
        import os

        env_val = os.getenv("ENABLE_SEMANTIC_ROUTING", "")
        if env_val.lower() in ("true", "1", "yes"):
            self.enable_semantic_routing = True
        elif not env_val and self.multi_db.get_active_db_ids():
            # 멀티 DB 연결이 하나라도 설정되어 있으면 자동 활성화
            self.enable_semantic_routing = True


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    """설정을 로드하여 반환한다.

    싱글톤 패턴으로 동일한 설정 인스턴스를 재사용한다.

    Returns:
        애플리케이션 설정
    """
    config = AppConfig()
    logger.info("애플리케이션 설정 로드 완료")
    return config
