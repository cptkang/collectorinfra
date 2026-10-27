"""interface — 게이트웨이 설정 관리 어댑터 (plans/191 W3 · D-345 · SPEC-component-config-admin §3).

설정이 dataclass라 인트로스펙션할 수 없어 필드 표를 **명시 표**로 둔다(`.env.example` 커버리지
테스트가 누락을 잡는다). 소스별 접두 키 `JENNIFER_<ID>_*`는 기동 시 선언된 소스 id로 표를 넓힌다 —
소스 추가·삭제(`JENNIFER_SOURCES`)는 읽기 전용이고 기존 소스의 비시크릿 키만 고칠 수 있다(G-5).
URL·호스트·경로·도메인 제한 꼴 키도 읽기 전용이다(자격증명이 다른 서버로 가거나 데이터 경계가
바뀐다).
쓰기 대상은 패키지 루트 `apm_gateway/.env`(CWD 무관 — 전체 차단 없음). `load_dotenv`가 넣지 않았는데
`os.environ`에 있는 키는 `override_os`다. 제니퍼·Bearer·Redis·관리 토큰 값은 응답·감사로 나가지
않는다(D-274 — 시크릿은 설정 여부만).
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apm_gateway import config as config_mod
from apm_gateway.config import PACKAGE_ROOT, POLICY_DIR, GatewayConfig, load_config
from apm_gateway.domain.jobs import DEFAULT_PRINCIPAL
from apm_gateway.domain.sources import DEFAULT_SOURCE_ID
from apm_gateway.interface.config_admin import ComponentAdapter, FieldSpec, read_env_values

VERSION = "0.1.0"  # pyproject.toml `version`
AUDIT_PATH = PACKAGE_ROOT / "var" / "config_admin_audit.jsonl"
RESTART_HINT = "cd apm_gateway && ../.venv/bin/python -m apm_gateway"
# 읽기 전용 = 명시 목록 ∪ 꼴 규칙(sre_agent 어댑터와 같은 규칙 + 자격증명이 따라가는 `_HOST`).
# URL·호스트를 바꾸면 토큰·비밀번호가 다른 서버로 간다 · 경로·도메인 제한은 데이터 경계다.
_READONLY_EXPLICIT = frozenset({"APM_GATEWAY_HOST", "JENNIFER_SOURCES"})
_READONLY_PATTERN = re.compile(r"(_URL|_BASE|_PATH|_DIR|_FILE|_HOST|_DOMAIN_IDS)$|GUARD|VERIFY")
GROUP_TITLES = {
    "jennifer": "제니퍼 연결",
    "server": "MCP 서버",
    "runtime": "런타임",
    "jobs": "장기 작업",
    "poller": "이벤트 폴러",
    "redis": "Redis",
}
_Getter = Callable[[GatewayConfig], Any]


def _other_bearer(cfg: GatewayConfig) -> bool:
    """`APM_GATEWAY_BEARER_TOKENS`에서 온 주체가 있는가(단일 토큰의 default는 빼고 본다)."""
    single = bool(cfg.server.bearer_token)
    return any(p != DEFAULT_PRINCIPAL or not single for p in cfg.server.bearer_tokens)


# (env 키, 그룹, type, 기본값, 기동 값 읽기, 설명) — 시크릿은 type `secret`으로 명시한다
# (접미어 규칙은 int 키를 오판한다). 시크릿은 값 대신 설정 여부만 다룬다.
_TABLE: tuple[tuple[str, str, str, Any, _Getter, str], ...] = (
    (
        "JENNIFER_API_URL",
        "jennifer",
        "string",
        "",
        lambda c: c.jennifer.url,
        "단일 설정 뷰 서버 Open API 주소",
    ),
    (
        "JENNIFER_API_TOKEN",
        "jennifer",
        "secret",
        None,
        lambda c: c.jennifer.token,
        "단일 설정 AIOps 전용 토큰",
    ),
    (
        "JENNIFER_SOURCES",
        "jennifer",
        "json_list",
        "",
        lambda c: [s.source_id for s in c.sources if s.source_id != DEFAULT_SOURCE_ID] or None,
        "다중 설정 소스 id 목록(소스 추가·삭제는 파일에서 직접)",
    ),
    (
        "JENNIFER_DOMAIN_IDS",
        "jennifer",
        "json_list",
        "[]",
        lambda c: list(c.jennifer.domain_ids),
        "조회 도메인 제한(비면 전체)",
    ),
    (
        "JENNIFER_API_TIMEOUT_SECONDS",
        "jennifer",
        "float",
        "10",
        lambda c: c.jennifer.timeout_seconds,
        "제니퍼 API 타임아웃(초)",
    ),
    (
        "JENNIFER_RATE_LIMIT_PER_SEC",
        "jennifer",
        "float",
        "5",
        lambda c: c.jennifer.rate_limit_per_sec,
        "초당 호출 상한",
    ),
    (
        "JENNIFER_MAX_RESPONSE_BYTES",
        "jennifer",
        "int",
        "4194304",
        lambda c: c.jennifer.max_response_bytes,
        "응답 본문 메모리 임계(바이트 — 넘으면 스풀 파일)",
    ),
    (
        "JENNIFER_MAX_INFLIGHT_PER_SOURCE",
        "jennifer",
        "int",
        "8",
        lambda c: c.jennifer.max_inflight_per_source,
        "소스별 동시 호출 상한(대기는 API 타임아웃까지)",
    ),
    (
        "APM_GATEWAY_HOST",
        "server",
        "string",
        "127.0.0.1",
        lambda c: c.server.host,
        "바인딩 호스트(관리 화면에서 바꾸지 않는다)",
    ),
    ("APM_GATEWAY_PORT", "server", "int", "9096", lambda c: c.server.port, "MCP 서버 포트"),
    (
        "APM_GATEWAY_LOG_LEVEL",
        "server",
        "string",
        "INFO",
        lambda c: c.server.log_level,
        "로그 수준",
    ),
    (
        "APM_GATEWAY_BEARER_TOKEN",
        "server",
        "secret",
        None,
        lambda c: c.server.bearer_token,
        "전송 인증 단일 토큰(주체 default)",
    ),
    (
        "APM_GATEWAY_BEARER_TOKENS",
        "server",
        "secret",
        None,
        _other_bearer,
        "소비자별 토큰 JSON 객체",
    ),
    (
        "APM_GATEWAY_ADMIN_TOKEN",
        "server",
        "secret",
        None,
        lambda c: c.server.admin_token,
        "설정 관리 API 토큰",
    ),
    ("APM_TIMEZONE", "runtime", "string", "Asia/Seoul", lambda c: c.runtime.timezone, "시간대"),
    (
        "APM_INSTANCE_CACHE_SECONDS",
        "runtime",
        "int",
        "600",
        lambda c: c.runtime.instance_cache_seconds,
        "인스턴스 목록 캐시(초)",
    ),
    (
        "APM_PROFILE_CALLS_PER_INVESTIGATION",
        "runtime",
        "int",
        "5",
        lambda c: c.runtime.profile_calls_per_investigation,
        "프로파일 호출 예산(1시간 · 칸마다)",
    ),
    (
        "APM_METRIC_CATALOG_TTL_SECONDS",
        "runtime",
        "int",
        "3600",
        lambda c: c.runtime.metric_catalog_ttl_seconds,
        "지표 카탈로그 캐시 수명(초)",
    ),
    (
        "APM_SPOOL_DIR",
        "jobs",
        "string",
        "var/spool",
        lambda c: str(c.jobs.spool_dir),
        "결과 스풀 디렉터리(상대 경로는 게이트웨이 루트 기준)",
    ),
    ("APM_INLINE_ROWS", "jobs", "int", "500", lambda c: c.jobs.inline_rows, "응답에 싣는 앞 행 수"),
    (
        "APM_ARTIFACT_CHUNK_ROWS",
        "jobs",
        "int",
        "2000",
        lambda c: c.jobs.chunk_rows,
        "결과 파일 청크 행 수",
    ),
    (
        "APM_ARTIFACT_RETENTION_SECONDS",
        "jobs",
        "int",
        "86400",
        lambda c: c.jobs.retention_seconds,
        "결과 파일·작업 기록 보관(초)",
    ),
    (
        "APM_JOB_MAX_CONCURRENT",
        "jobs",
        "int",
        "4",
        lambda c: c.jobs.max_concurrent,
        "백그라운드 작업 동시 실행 수",
    ),
    (
        "APM_JOB_STALL_SECONDS",
        "jobs",
        "int",
        "300",
        lambda c: c.jobs.stall_seconds,
        "작업 정체 판정(초)",
    ),
    (
        "APM_PRIORITY_AGING_SECONDS",
        "jobs",
        "float",
        "10",
        lambda c: c.jobs.priority_aging_seconds,
        "호출 대기열 에이징(초)",
    ),
    (
        "APM_EVENT_POLLER_ENABLED",
        "poller",
        "bool",
        "false",
        lambda c: c.poller.enabled,
        "이벤트 폴러 사용",
    ),
    (
        "APM_EVENT_POLL_INTERVAL_SECONDS",
        "poller",
        "int",
        "30",
        lambda c: c.poller.interval_seconds,
        "폴링 주기(초 · 하한 10)",
    ),
    (
        "APM_EVENT_MIN_LEVEL",
        "poller",
        "string",
        "warning",
        lambda c: c.poller.min_level,
        "발행 최소 이벤트 레벨",
    ),
    (
        "APM_EVENT_STREAM_KEY",
        "poller",
        "string",
        "alarm:raw",
        lambda c: c.poller.stream_key,
        "발행 Redis Stream 키",
    ),
    (
        "APM_EVENT_BUFFER_MINUTES",
        "poller",
        "int",
        "60",
        lambda c: c.poller.buffer_minutes,
        "조회용 이벤트 버퍼 보관(분)",
    ),
    (
        "APM_EVENT_BUFFER_MAX_EVENTS",
        "poller",
        "int",
        "200000",
        lambda c: c.poller.buffer_max_events,
        "조회용 이벤트 버퍼 최대 건수",
    ),
    ("REDIS_HOST", "redis", "string", "localhost", lambda c: c.redis.host, "Redis 호스트"),
    ("REDIS_PORT", "redis", "int", "6379", lambda c: c.redis.port, "Redis 포트"),
    ("REDIS_DB", "redis", "int", "0", lambda c: c.redis.db, "Redis DB 번호"),
    ("REDIS_PASSWORD", "redis", "secret", None, lambda c: c.redis.password, "Redis 비밀번호"),
)
# 소스별 접두 키 접미 → (type, 기동 값 읽기, 설명) — `config.SOURCE_KEY_SUFFIXES`와 같은 집합
_SOURCE_SUFFIX_META: dict[str, tuple[str, Callable[[Any], Any], str]] = {
    "API_URL": ("string", lambda s: s.url, "뷰 서버 Open API 주소"),
    "API_TOKEN": ("secret", lambda s: s.token, "소스별 AIOps 전용 토큰"),
    "DOMAIN_IDS": ("json_list", lambda s: list(s.domain_ids), "조회 도메인 제한(비면 전역 값)"),
    "API_TIMEOUT_SECONDS": ("float", lambda s: s.timeout_seconds, "타임아웃(초 · 비면 전역 값)"),
    "RATE_LIMIT_PER_SEC": ("float", lambda s: s.rate_limit_per_sec, "초당 호출 상한(비면 전역 값)"),
    "MAX_RESPONSE_BYTES": ("int", lambda s: s.max_response_bytes, "응답 메모리 임계(비면 전역 값)"),
    "MAX_INFLIGHT_PER_SOURCE": (
        "int",
        lambda s: s.max_inflight_per_source,
        "동시 호출 상한(비면 전역 값)",
    ),
}


def _effective(kind: str, value: Any) -> Any:
    """시크릿은 설정됐으면 True, 아니면 None만 — 값은 담지 않는다."""
    if kind == "secret":
        return True if value else None
    return value


def build_fields(cfg: GatewayConfig) -> tuple[list[FieldSpec], dict[str, Any], dict[str, str]]:
    """(필드 표, 기동 값, 그룹 제목). 소스별 키는 기동 시 선언된 소스(다중 설정)로 넓힌다."""
    specs: list[FieldSpec] = []
    values: dict[str, Any] = {}
    titles = dict(GROUP_TITLES)
    for key, group, kind, default, getter, description in _TABLE:
        specs.append(FieldSpec(key, group, kind, default, description, is_secret=kind == "secret"))
        values[key] = _effective(kind, getter(cfg))
    for source in cfg.sources:
        if source.source_id == DEFAULT_SOURCE_ID:
            continue
        group = f"source_{source.source_id}"
        titles[group] = f"제니퍼 소스 {source.source_id}"
        for suffix in config_mod.SOURCE_KEY_SUFFIXES:
            kind, getter, description = _SOURCE_SUFFIX_META[suffix]
            key = f"JENNIFER_{source.source_id.upper()}_{suffix}"
            specs.append(FieldSpec(key, group, kind, None, description, is_secret=kind == "secret"))
            values[key] = _effective(kind, getter(source))
    return specs, values, titles


def readonly_keys(specs: list[FieldSpec]) -> frozenset[str]:
    """필드 표 중 읽기 전용 키(명시 목록 ∪ 꼴 규칙)."""
    return frozenset(
        s.env_key
        for s in specs
        if s.env_key in _READONLY_EXPLICIT or _READONLY_PATTERN.search(s.env_key)
    )


def _os_origin() -> dict[str, str]:
    """`load_dotenv`가 넣지 않은 `os.environ` 키 — 프로세스 환경 원천(파일보다 우선)."""
    return {k: v for k, v in os.environ.items() if k not in config_mod.DOTENV_INJECTED_KEYS}


def build_adapter(
    cfg: GatewayConfig,
    write_target: Path | None = None,
    audit_path: Path | None = None,
    policy_dir: Path = POLICY_DIR,
) -> ComponentAdapter:
    """게이트웨이 어댑터. write_target·audit_path·policy_dir는 테스트 주입점이다(None = 실 경로)."""
    dotenv = write_target or PACKAGE_ROOT / ".env"
    specs, values, titles = build_fields(cfg)

    def override_for(env_key: str) -> str | None:
        return "os" if env_key in _os_origin() else None

    def dry_run(candidate: Path) -> list[str]:
        env = {**read_env_values(candidate), **_os_origin()}
        try:
            load_config(env, policy_dir=policy_dir, dotenv=None)
        except Exception as exc:  # 예외 문구는 입력 값을 실을 수 있다 — 사유는 형 이름만
            return [f"설정 로드 실패: {type(exc).__name__}"]
        return []

    def sources() -> list[dict[str, Any]]:
        return [
            {
                "path": str(dotenv),
                "role": "write_target",
                "exists": dotenv.is_file(),
                "priority": 1,
            },
            {"path": "(process environment)", "role": "override", "exists": True, "priority": 2},
        ]

    return ComponentAdapter(
        name="apm_gateway",
        version=VERSION,
        restart_hint=RESTART_HINT,
        fields=specs,
        write_target=dotenv,
        audit_path=audit_path or AUDIT_PATH,
        dry_run=dry_run,
        group_titles=titles,
        effective_values=values,
        readonly_keys=readonly_keys(specs),
        override_for=override_for,
        sources=sources,
    )
