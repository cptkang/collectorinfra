"""mcp_server 설정 관리 어댑터 (plans/191 W4 · D-345 · SPEC §3 `mcp_server` 행).

쓰기 대상은 `config.toml`과 같은 디렉토리의 `.env`다(CWD 무관). `config.toml`은 읽기 전용
원천이고 여기서 고치지 않는다(G-3). 쓰기 대상 키는 `_apply_env_overrides`가 읽는 env 키뿐이다.
데이터 경계 가드 키와 바인딩 호스트는 읽기 전용이다(D-003 취지). MCP 도구 표면은 건드리지 않는다 —
관리 경로는 `AdminPathRouter`가 기존 앱 앞에서 가로채는 별도 HTTP 경로다.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from pathlib import Path
from typing import Any

from starlette.types import ASGIApp

from mcp_server import __version__
from mcp_server.config import (
    _DEFAULT_CONFIG_PATH,
    DOTENV_INJECTED_KEYS,
    OPENMETRICS_FALLBACK_POLICIES,
    PROMETHEUS_ANCHOR_MODES,
    AppServerConfig,
    _apply_env_overrides,
    _load_toml,
    read_dotenv,
)
from mcp_server.config_admin import (
    AdminPathRouter,
    ComponentAdapter,
    FieldSpec,
    build_admin_app,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = _PROJECT_ROOT / "logs" / "config_admin" / "mcp_server_audit.jsonl"
RESTART_HINT = "cd mcp_server && ../.venv/bin/python -m mcp_server"
#: 데이터 경계 가드(D-003 취지) — 명시 목록.
_GUARD_KEYS = frozenset(
    {
        "SERVER_HOST",
        "EXPOSE_EXECUTE_SQL",
        "POLESTAR_DOMAIN_GUARD",
        "EXPOSE_RAW_PROMQL",
        "PROMETHEUS_SOURCES",
    }
)
#: URL·경로·호스트·가드 꼴 키도 읽기 전용이다 —
#: 바꾸면 자격증명(인증 헤더 등)이 다른 곳으로 따라간다.
_READONLY_PATTERN = re.compile(r"(_URL|_BASE|_PATH|_DIR|_FILE|_HOST)$|GUARD|VERIFY")
GROUP_TITLES = {
    "server": "서버",
    "auth": "인증",
    "gates": "도구 표면 게이트",
    "prometheus": "Prometheus",
    "openmetrics": "OpenMetrics",
    "sources": "데이터소스 연결",
}

#: (env 키, 그룹, type, 기본값, 설명, enum 선택지, 시크릿, (설정 섹션, 속성)).
#: 연결 문자열(`{SOURCE}_CONNECTION`)은 `config.toml` `[[sources]]`에서 따로 만든다.
_FIELDS: tuple[tuple[str, str, str, Any, str, tuple[str, ...], bool, tuple[str, str]], ...] = (
    ("SERVER_NAME", "server", "string", "dbhub-server", "FastMCP 서버 이름", (), False,
     ("server", "name")),
    ("SERVER_HOST", "server", "string", "0.0.0.0", "바인딩 호스트(읽기 전용)", (), False,
     ("server", "host")),
    ("SERVER_PORT", "server", "int", 9099, "서버 포트", (), False, ("server", "port")),
    ("SERVER_TRANSPORT", "server", "enum", "sse", "전송 방식", ("sse", "stdio", "streamable-http"),
     False, ("server", "transport")),
    ("SERVER_LOG_LEVEL", "server", "enum", "info", "로그 레벨",
     ("debug", "info", "warning", "error", "critical"), False, ("server", "log_level")),
    ("PROCESS_API_BASE_URL", "server", "string", "",
     "실시간 프로세스 API base_url(읽기 전용)", (), False,
     ("server", "process_api_base_url")),
    ("PROCESS_API_BASE_URLS_CSV", "server", "string", "",
     "소스별 실시간 프로세스 API base_url(db_id=URL,… · 읽기 전용)", (), False,
     ("server", "process_api_base_urls_csv")),
    ("MCP_BEARER_TOKEN", "auth", "secret", None, "MCP 전송 정적 Bearer 토큰", (), True,
     ("server", "bearer_token")),
    ("MCP_ADMIN_TOKEN", "auth", "secret", None, "설정 관리 API 관리 토큰", (), True,
     ("server", "admin_token")),
    ("EXPOSE_EXECUTE_SQL", "gates", "bool", False,
     "execute_sql 노출(읽기 전용 — 데이터 경계 가드)", (), False,
     ("server", "expose_execute_sql")),
    ("POLESTAR_DOMAIN_GUARD", "gates", "bool", True,
     "execute_sql 도메인 deny 적용(읽기 전용 — 데이터 경계 가드)", (), False,
     ("server", "polestar_domain_guard")),
    ("EXPOSE_POLESTAR_TOOLS", "gates", "bool", True, "고수준 폴스타 도구 등록", (), False,
     ("server", "expose_polestar_tools")),
    ("EXPOSE_RAW_PROMQL", "gates", "bool", False,
     "원시 PromQL 패스스루 노출(읽기 전용 — 데이터 경계 가드)", (), False,
     ("prometheus", "expose_raw_promql")),
    ("PROMETHEUS_URL", "prometheus", "string", "", "Prometheus URL(읽기 전용)", (), False,
     ("prometheus", "url")),
    ("PROMETHEUS_AUTH_HEADER", "prometheus", "secret", None, "Prometheus 인증 헤더 전체 값", (),
     True, ("prometheus", "auth_header")),
    ("PROMETHEUS_QUERY_TIMEOUT", "prometheus", "int", 30, "PromQL 쿼리 timeout(초)", (), False,
     ("prometheus", "query_timeout")),
    ("PROMETHEUS_ANCHOR_MODE", "prometheus", "enum", "nodename",
     "고수준 도구 hostname 셀렉터 조립 방식", PROMETHEUS_ANCHOR_MODES, False,
     ("prometheus", "anchor_mode")),
    ("PROMETHEUS_HOST_LABEL", "prometheus", "string", "hostname", "OS 호스트명 라벨 이름", (),
     False, ("prometheus", "host_label")),
    ("PROMETHEUS_IP_LABEL", "prometheus", "string", "node_ip", "OS IP 라벨 이름", (), False,
     ("prometheus", "ip_label")),
    ("PROMETHEUS_INSTANCE_LABEL", "prometheus", "string", "instance",
     "「모호」 판정용 라벨 이름", (), False, ("prometheus", "instance_label")),
    ("PROMETHEUS_SOURCES", "prometheus", "string", "[]",
     "Prometheus 수집 폴스타 source(JSON 배열 · 읽기 전용 — 존 범위 가드)", (), False,
     ("prometheus", "sources")),
    ("EXPOSE_OPENMETRICS_TOOLS", "openmetrics", "bool", False, "om_* 도구 등록", (), False,
     ("openmetrics", "expose_openmetrics_tools")),
    ("OPENMETRICS_SCRAPE_TIMEOUT", "openmetrics", "int", 10, "스크레이프 timeout(초)", (), False,
     ("openmetrics", "scrape_timeout")),
    ("OPENMETRICS_MAX_BODY_BYTES", "openmetrics", "int", 4 * 1024 * 1024, "응답 본문 상한(바이트)",
     (), False, ("openmetrics", "max_body_bytes")),
    ("OPENMETRICS_MAX_SERIES", "openmetrics", "int", 200, "도구 1회 반환 시리즈 상한", (), False,
     ("openmetrics", "max_series")),
    ("OPENMETRICS_FALLBACK_POLICY", "openmetrics", "enum", "off", "source=auto 강등 정책",
     OPENMETRICS_FALLBACK_POLICIES, False, ("openmetrics", "fallback_policy")),
    ("OPENMETRICS_COVERAGE_TTL_SECONDS", "openmetrics", "int", 600,
     "nodename 커버리지 캐시 TTL(초)", (), False, ("openmetrics", "coverage_ttl_seconds")),
    ("OPENMETRICS_CROSS_CHECK_TOLERANCE", "openmetrics", "float", 0.05, "교차 검증 상대 허용 오차",
     (), False, ("openmetrics", "cross_check_tolerance")),
    ("OPENMETRICS_SCRAPE_INTERVAL_HINT", "openmetrics", "int", 15, "stale 판정 스크레이프 주기(초)",
     (), False, ("openmetrics", "scrape_interval_hint")),
    ("EXPOSE_POLESTAR_EXPORTER", "openmetrics", "bool", False, "GET /metrics 브리지 라우트 등록",
     (), False, ("openmetrics", "expose_polestar_exporter")),
    ("OPENMETRICS_BRIDGE_CACHE_SECONDS", "openmetrics", "int", 300, "브리지 응답 캐시 TTL(초)",
     (), False, ("openmetrics", "bridge_cache_seconds")),
)

READONLY_KEYS = _GUARD_KEYS | frozenset(
    key for key, *_ in _FIELDS if _READONLY_PATTERN.search(key)
)


def _toml_sources(config_path: Path) -> list[tuple[str, str]]:
    """`config.toml` `[[sources]]`의 (이름, 타입). 연결 문자열 값은 읽지 않는다."""
    if not config_path.is_file():
        return []
    return [(s.name, s.type) for s in _load_toml(config_path).sources if s.name]


def build_field_specs(config_path: Path = _DEFAULT_CONFIG_PATH) -> list[FieldSpec]:
    """필드 표 — env 오버라이드 키 + `[[sources]]`별 연결 문자열(시크릿)."""
    specs = [
        FieldSpec(key, group, kind, default, desc, choices, secret)
        for key, group, kind, default, desc, choices, secret, _ in _FIELDS
    ]
    specs += [
        FieldSpec(
            env_key=f"{name.upper()}_CONNECTION",
            group_key="sources",
            type="secret",
            description=f"[[sources]] {name} ({kind}) 연결 문자열",
            is_secret=True,
        )
        for name, kind in _toml_sources(config_path)
    ]
    return specs


def _effective(config: AppServerConfig, specs: list[FieldSpec]) -> dict[str, Any]:
    """기동 시 값(toml + env 반영). 시크릿은 설정됐으면 True, 아니면 None."""
    attrs = {key: attr for key, *_, attr in _FIELDS}
    active = {f"{s.name.upper()}_CONNECTION" for s in config.sources if s.connection}
    values: dict[str, Any] = {}
    for spec in specs:
        if spec.env_key in attrs:
            section, name = attrs[spec.env_key]
            value = getattr(getattr(config, section), name)
        else:
            value = spec.env_key in active
        values[spec.env_key] = (True if value else None) if spec.is_secret else value
    return values


def _os_origin_env() -> dict[str, str]:
    """`.env`가 넣지 않은 `os.environ` 키 — 기동 전 셸·systemd가 준 값."""
    return {k: v for k, v in os.environ.items() if k not in DOTENV_INJECTED_KEYS}


def build_adapter(
    config: AppServerConfig,
    config_path: Path | None = None,
    audit_path: Path | None = None,
) -> ComponentAdapter:
    """어댑터. config_path·audit_path는 테스트 주입점이다(None = 실 경로)."""
    config_path = (config_path or _DEFAULT_CONFIG_PATH).resolve()
    dotenv = config_path.parent / ".env"
    specs = build_field_specs(config_path)

    def override_for(env_key: str) -> str | None:
        return "os" if env_key in _os_origin_env() else None

    def dry_run(candidate: Path) -> list[str]:
        # 기동 경로와 같은 우선순위(OS 원천 > .env)로 매핑을 만들고 os.environ은 건드리지 않는다.
        try:
            env = read_dotenv(candidate) | _os_origin_env()
            loaded = _load_toml(config_path) if config_path.is_file() else AppServerConfig()
            _apply_env_overrides(loaded, env=env)
        except (ValueError, TypeError, OSError) as exc:  # TOMLDecodeError ⊂ ValueError
            return [f"설정 로드 실패: {type(exc).__name__}"]
        return []

    def sources() -> list[dict[str, Any]]:
        return [
            {"path": str(config_path), "role": "readonly", "exists": config_path.is_file(),
             "priority": 0},
            {"path": str(dotenv), "role": "write_target", "exists": dotenv.is_file(),
             "priority": 1},
            {"path": "(process environment)", "role": "override", "exists": True, "priority": 2},
        ]

    return ComponentAdapter(
        name="mcp_server",
        version=__version__,
        restart_hint=RESTART_HINT,
        fields=specs,
        write_target=dotenv,
        audit_path=audit_path or AUDIT_PATH,
        dry_run=dry_run,
        cwd=Path.cwd().resolve(),
        group_titles=GROUP_TITLES,
        effective_values=_effective(config, specs),
        readonly_keys=READONLY_KEYS,
        override_for=override_for,
        sources=sources,
    )


def wrap_admin_app(
    app: ASGIApp,
    config: AppServerConfig,
    config_path: Path | None = None,
    audit_path: Path | None = None,
) -> ASGIApp:
    """관리 토큰이 있을 때만 `/admin/config` 경로 분기 래퍼로 감싼다(없으면 `app` 그대로).

    관리 토큰이 MCP Bearer 토큰과 같으면 감싸지 않고 경고 1줄을 남긴다(G-1).
    """
    admin_token = config.server.admin_token
    if not admin_token:
        return app
    bearer = config.server.bearer_token
    if bearer and hmac.compare_digest(admin_token.encode(), bearer.encode()):
        logger.warning(
            "MCP_ADMIN_TOKEN이 MCP_BEARER_TOKEN과 같다 — 설정 관리 라우트를 등록하지 않는다"
        )
        return app
    adapter = build_adapter(config, config_path=config_path, audit_path=audit_path)
    logger.info("설정 관리 API 활성: /admin/config (쓰기 대상 %s)", adapter.write_target)
    return AdminPathRouter(app, build_admin_app(adapter, admin_token))
