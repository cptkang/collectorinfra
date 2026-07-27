"""서버 설정 로딩 모듈.

config.toml과 환경변수에서 서버 설정을 로드한다.
환경변수가 TOML 설정을 오버라이드한다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 기본 설정 파일 경로 (패키지 루트 기준)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = _PACKAGE_ROOT / "config.toml"


@dataclass
class ServerConfig:
    """MCP 서버 설정."""

    name: str = "dbhub-server"
    host: str = "0.0.0.0"
    port: int = 9099
    transport: str = "sse"
    log_level: str = "info"
    # execute_sql 저수준 도구 노출 여부 (기본 비노출 — 계획 §3/§6, D-022/D-028).
    # 고수준 도구는 SQL을 받지 않으므로 항상 안전하다. raw SQL 실행이 필요한 탐색적
    # 조사·본체 NL→SQL 파이프라인 배치에서만 config.toml/환경변수로 True 옵트인한다.
    expose_execute_sql: bool = False
    # 폴스타 실시간 프로세스 API base_url (process_snapshot 도구용). 비면 도구가 오류 반환.
    process_api_base_url: str = ""


@dataclass
class PrometheusConfig:
    """Prometheus 접속 설정 (mcp_server 전용 — 소비자 미보유, Plan 06 §3 · D-119).

    ``url``·``auth_header``는 서버 설정에만 존재한다(sre_agent 미보유 — 접근 경계
    일원화). ``query_timeout``은 원시 패스스루를 포함한 모든 PromQL HTTP 호출에
    서버가 강제하는 쿼리 timeout(초)이다. ``expose_raw_promql``은 원시 PromQL
    패스스루 도구의 노출 여부로 기본 비노출이다(execute_sql 전례).
    """

    url: str = ""
    auth_header: str = ""
    query_timeout: int = 30
    expose_raw_promql: bool = False


@dataclass
class SourceConfig:
    """데이터소스 설정."""

    name: str = ""
    type: str = "postgresql"  # "postgresql" | "db2"
    connection: str = ""
    readonly: bool = True
    query_timeout: int = 30
    max_rows: int = 10000
    pool_min_size: int = 1
    pool_max_size: int = 5


@dataclass
class AppServerConfig:
    """MCP 서버 전체 설정."""

    server: ServerConfig = field(default_factory=ServerConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)


def load_config(config_path: str | Path | None = None) -> AppServerConfig:
    """설정을 로드한다.

    1. config.toml에서 기본 설정을 읽는다.
    2. 환경변수에서 DB 연결 문자열을 오버라이드한다.
    3. 연결 문자열이 비어있는 소스는 비활성으로 필터링한다.

    환경변수 규칙:
    - 소스별 연결 문자열: {SOURCE_NAME_UPPER}_CONNECTION
      예: INFRA_DB_CONNECTION, POLESTAR_CONNECTION

    Args:
        config_path: 설정 파일 경로 (없으면 패키지 루트의 config.toml)

    Returns:
        서버 전체 설정
    """
    if config_path is None:
        config_path = _DEFAULT_CONFIG_PATH
    config_path = Path(config_path)

    # .env 파일 로드 (있으면)
    _load_dotenv(config_path.parent)

    config = AppServerConfig()

    if config_path.exists():
        config = _load_toml(config_path)
        logger.info("설정 파일 로드: %s", config_path)
    else:
        logger.warning("설정 파일을 찾을 수 없음: %s (기본값 사용)", config_path)

    # 환경변수 오버라이드
    _apply_env_overrides(config)

    # 연결 문자열이 있는 활성 소스만 필터링
    active_sources = [s for s in config.sources if s.connection]
    inactive_names = [s.name for s in config.sources if not s.connection]
    config.sources = active_sources

    if inactive_names:
        logger.info("비활성 소스 (연결 문자열 없음): %s", inactive_names)
    logger.info(
        "활성 소스: %s",
        [f"{s.name} ({s.type})" for s in config.sources],
    )

    return config


def _load_dotenv(directory: Path) -> None:
    """디렉토리에서 .env 파일을 로드한다."""
    env_path = directory / ".env"
    if not env_path.exists():
        return
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                # 이미 설정된 환경변수는 오버라이드하지 않음
                if key not in os.environ:
                    os.environ[key] = value
        logger.info(".env 파일 로드: %s", env_path)
    except Exception as e:
        logger.warning(".env 파일 로드 실패: %s", e)


def _load_toml(path: Path) -> AppServerConfig:
    """TOML 파일에서 설정을 파싱한다."""
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # 서버 설정
    server_data = data.get("server", {})
    server = ServerConfig(
        name=server_data.get("name", "dbhub-server"),
        host=server_data.get("host", "0.0.0.0"),
        port=server_data.get("port", 9099),
        transport=server_data.get("transport", "sse"),
        log_level=server_data.get("log_level", "info"),
        expose_execute_sql=server_data.get("expose_execute_sql", False),
        process_api_base_url=server_data.get("process_api_base_url", ""),
    )

    # 소스 설정
    sources: list[SourceConfig] = []
    for src_data in data.get("sources", []):
        src = SourceConfig(
            name=src_data.get("name", ""),
            type=src_data.get("type", "postgresql"),
            connection=src_data.get("connection", ""),
            readonly=src_data.get("readonly", True),
            query_timeout=src_data.get("query_timeout", 30),
            max_rows=src_data.get("max_rows", 10000),
            pool_min_size=src_data.get("pool_min_size", 1),
            pool_max_size=src_data.get("pool_max_size", 5),
        )
        sources.append(src)

    # Prometheus 설정 (PromQL 도구 — Plan 06 §3 · D-119)
    prom_data = data.get("prometheus", {})
    prometheus = PrometheusConfig(
        url=prom_data.get("url", ""),
        auth_header=prom_data.get("auth_header", ""),
        query_timeout=prom_data.get("query_timeout", 30),
        expose_raw_promql=prom_data.get("expose_raw_promql", False),
    )

    return AppServerConfig(server=server, sources=sources, prometheus=prometheus)


def _apply_env_overrides(config: AppServerConfig) -> None:
    """환경변수로 설정을 오버라이드한다.

    규칙:
    - SERVER_*: 서버 설정
      SERVER_NAME, SERVER_HOST, SERVER_PORT, SERVER_TRANSPORT, SERVER_LOG_LEVEL
    - {SOURCE_NAME_UPPER}_CONNECTION: 소스별 DB 연결 문자열
      예: INFRA_DB_CONNECTION, INFRA_DB2_CONNECTION, POLESTAR_CONNECTION
    """
    # 서버 설정 오버라이드
    _env_overrides = {
        "SERVER_NAME": ("name", str),
        "SERVER_HOST": ("host", str),
        "SERVER_PORT": ("port", int),
        "SERVER_TRANSPORT": ("transport", str),
        "SERVER_LOG_LEVEL": ("log_level", str),
        "PROCESS_API_BASE_URL": ("process_api_base_url", str),
    }
    for env_key, (attr, cast) in _env_overrides.items():
        env_val = os.environ.get(env_key, "")
        if env_val:
            setattr(config.server, attr, cast(env_val))
            logger.debug("환경변수 오버라이드: %s = %s", env_key, env_val)

    # expose_execute_sql은 불리언이라 별도 파싱 ("1"/"true"/"yes"/"on" → True)
    expose_env = os.environ.get("EXPOSE_EXECUTE_SQL", "")
    if expose_env:
        config.server.expose_execute_sql = expose_env.strip().lower() in (
            "1", "true", "yes", "on",
        )
        logger.debug("환경변수 오버라이드: EXPOSE_EXECUTE_SQL = %s",
                     config.server.expose_execute_sql)

    # Prometheus 설정 오버라이드 (문자열/정수 — Plan 06 §3 · D-119)
    _prom_overrides = {
        "PROMETHEUS_URL": ("url", str),
        "PROMETHEUS_AUTH_HEADER": ("auth_header", str),
        "PROMETHEUS_QUERY_TIMEOUT": ("query_timeout", int),
    }
    for env_key, (attr, cast) in _prom_overrides.items():
        env_val = os.environ.get(env_key, "")
        if env_val:
            setattr(config.prometheus, attr, cast(env_val))
            logger.debug("환경변수 오버라이드: %s = %s", env_key, env_val)

    # expose_raw_promql도 불리언이라 별도 파싱
    raw_env = os.environ.get("EXPOSE_RAW_PROMQL", "")
    if raw_env:
        config.prometheus.expose_raw_promql = raw_env.strip().lower() in (
            "1", "true", "yes", "on",
        )
        logger.debug("환경변수 오버라이드: EXPOSE_RAW_PROMQL = %s",
                     config.prometheus.expose_raw_promql)

    # 소스별 연결 문자열 오버라이드
    for source in config.sources:
        env_key = f"{source.name.upper()}_CONNECTION"
        env_val = os.environ.get(env_key, "")
        if env_val:
            source.connection = env_val
            logger.debug("환경변수 오버라이드: %s -> %s", env_key, source.name)
