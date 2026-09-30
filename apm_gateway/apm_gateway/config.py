"""게이트웨이 설정 — `.env` + 정책 yaml (plans/87 §7.0 · SPEC-apm-gateway §2.1).

- 비밀·접속값은 `apm_gateway/.env`(gitignore)에서 읽는다. `mcp_server` `_load_dotenv` 전례대로
  **이미 있는 환경변수는 덮어쓰지 않는다.** 인라인 주석은 쓰지 않는다(Known Mistakes).
- 정책(정합 파일 · 이벤트 레벨 · WAS 임계)은 `apm_gateway/config/*.yaml` — 게이트웨이 소유 설정이다.
  자체 cwd로 뜨므로 루트 `config/`를 참조하지 않는다.
- 폴스타 DB 연결 문자열은 받지 않는다(게이트웨이는 폴스타 DB 자격증명을 갖지 않는다 — §0.7 (8)).
- 제니퍼 키 이름 `JENNIFER_*`는 이 프로세스 환경에만 둔다 — 에이전트를 붙인 WAS JVM 환경에
  export하지 않는다(R-29). 소스별 접두 키 `JENNIFER_<ID>_*`도 같다.
- 제니퍼 소스(plans/87 J8 · D-287 ③): `JENNIFER_SOURCES`(JSON 배열) + 소스별
  `JENNIFER_<ID>_API_URL`·`_API_TOKEN`(필수)과 선택 키(비면 전역 `JENNIFER_*` 값).
  `JENNIFER_SOURCES`가 없으면 단일 설정(`JENNIFER_API_URL` → 소스 `default`). 두 방식을 함께 쓰면
  기동 실패다(정본 모호 — 침묵 선택 금지).
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from apm_gateway.domain.events import DEFAULT_LEVEL_SEVERITY, DEFAULT_UNKNOWN_SEVERITY
from apm_gateway.domain.signals import WasThresholds
from apm_gateway.domain.sources import DEFAULT_SOURCE_ID, source_id_error

logger = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent.parent  # apm_gateway/ (자체 cwd)
POLICY_DIR = PACKAGE_ROOT / "config"

POLL_INTERVAL_FLOOR = 10
# 소스별 접두 키 `JENNIFER_<ID>_<접미>` — 앞 둘은 필수, 나머지는 비면 전역 `JENNIFER_<접미>` 값.
SOURCE_KEY_SUFFIXES = (
    "API_URL",
    "API_TOKEN",
    "DOMAIN_IDS",
    "API_TIMEOUT_SECONDS",
    "RATE_LIMIT_PER_SEC",
    "MAX_RESPONSE_BYTES",
)


@dataclass
class JenniferApiConfig:
    url: str = ""
    token: str = ""
    domain_ids: tuple[int, ...] = ()
    timeout_seconds: float = 10.0
    rate_limit_per_sec: float = 5.0
    max_response_bytes: int = 4 * 1024 * 1024
    source_id: str = DEFAULT_SOURCE_ID


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 9096
    log_level: str = "INFO"
    bearer_token: str = ""


@dataclass
class RuntimeConfig:
    timezone: str = "Asia/Seoul"
    instance_cache_seconds: int = 600
    profile_calls_per_investigation: int = 5


@dataclass
class PollerConfig:
    enabled: bool = False
    interval_seconds: int = 30
    min_level: str = "warning"
    stream_key: str = "alarm:raw"


@dataclass
class RedisConfig:
    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: str = ""


@dataclass
class Policies:
    instance_map: dict[str, Any] = field(default_factory=dict)
    level_severity: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_LEVEL_SEVERITY))
    unknown_level_severity: int = DEFAULT_UNKNOWN_SEVERITY
    thresholds: WasThresholds = field(default_factory=WasThresholds)


@dataclass
class GatewayConfig:
    # 전역 `JENNIFER_*` 값 — 단일 설정 소스 `default`이자 다중 설정 선택 키의 기본값.
    jennifer: JenniferApiConfig = field(default_factory=JenniferApiConfig)
    # 조회 대상 소스(선언 순서 = 조회·표시 순서). 0개면 미설정.
    sources: tuple[JenniferApiConfig, ...] = ()
    server: ServerConfig = field(default_factory=ServerConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    poller: PollerConfig = field(default_factory=PollerConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    policies: Policies = field(default_factory=Policies)


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int_list(value: str | None, key: str = "JENNIFER_DOMAIN_IDS") -> tuple[int, ...]:
    if value is None or not value.strip():
        return ()
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{key}는 JSON 배열이어야 한다(예: [1000, 2000])")
    return tuple(int(x) for x in parsed)


def _source_ids(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return ()
    parsed = json.loads(value)
    if not isinstance(parsed, list) or not all(isinstance(x, str) for x in parsed):
        raise ValueError('JENNIFER_SOURCES는 문자열 JSON 배열이어야 한다(예: ["bank", "common"])')
    ids = tuple(x.strip() for x in parsed)
    for sid in ids:
        problem = source_id_error(sid)
        if problem:
            raise ValueError(f"JENNIFER_SOURCES: {problem}")
    dup = sorted({sid for sid in ids if ids.count(sid) > 1})
    if dup:
        raise ValueError(f"JENNIFER_SOURCES 중복 id: {dup}")
    return ids


def _load_sources(
    declared: str | None, base: JenniferApiConfig, env: Mapping[str, str]
) -> tuple[JenniferApiConfig, ...]:
    """`JENNIFER_SOURCES`가 없으면 단일 설정(`base` — URL이 있을 때만 소스 `default`)."""
    ids = _source_ids(declared)
    if not ids:
        return (base,) if base.url else ()
    if base.url or base.token:
        raise ValueError(
            "JENNIFER_SOURCES와 단일 설정 키(JENNIFER_API_URL·JENNIFER_API_TOKEN)를 함께 쓸 수 없다"
            " — 한쪽만 설정한다(정본 모호)"
        )
    sources = []
    for sid in ids:
        prefix = f"JENNIFER_{sid.upper()}_"

        def pick(suffix: str, prefix: str = prefix) -> str:
            return (env.get(prefix + suffix) or "").strip()

        missing = [prefix + k for k in SOURCE_KEY_SUFFIXES[:2] if not pick(k)]
        if missing:
            raise ValueError(f"소스 {sid!r} 필수 키 없음: {missing}")
        sources.append(
            JenniferApiConfig(
                url=pick("API_URL"),
                token=pick("API_TOKEN"),
                domain_ids=_int_list(pick("DOMAIN_IDS"), prefix + "DOMAIN_IDS")
                if pick("DOMAIN_IDS")
                else base.domain_ids,
                timeout_seconds=float(pick("API_TIMEOUT_SECONDS") or base.timeout_seconds),
                rate_limit_per_sec=float(pick("RATE_LIMIT_PER_SEC") or base.rate_limit_per_sec),
                max_response_bytes=int(pick("MAX_RESPONSE_BYTES") or base.max_response_bytes),
                source_id=sid,
            )
        )
    return tuple(sources)


def _warn_unknown_policy_sources(instance_map: dict[str, Any], configured: set[str]) -> None:
    """정합 파일이 설정에 없는 소스를 가리키면 경고한다(그 항목은 쓰이지 않는다 — 침묵 금지)."""
    named = {
        str(ov["source_id"]) for ov in instance_map.get("overrides") or [] if ov.get("source_id")
    }
    named |= {str(k) for k in (instance_map.get("per_source") or {})}
    unknown = sorted(named - configured)
    if unknown:
        logger.warning("정합 파일이 설정에 없는 소스를 가리킨다(쓰이지 않음): %s", unknown)


def load_dotenv(path: Path) -> None:
    """`.env`를 `os.environ`에 넣는다(이미 있는 키는 그대로 · 값은 로그에 남기지 않는다)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()
    logger.info(".env 로드: %s", path)


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        logger.warning("정책 파일 없음 — 기본값 사용: %s", path)
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"정책 파일은 매핑이어야 한다: {path}")
    return data


def load_policies(policy_dir: Path = POLICY_DIR) -> Policies:
    instance_map = _load_yaml(policy_dir / "instance_map.yaml")
    levels = _load_yaml(policy_dir / "event_levels.yaml")
    thresholds = _load_yaml(policy_dir / "was_signatures.yaml")
    level_severity = dict(DEFAULT_LEVEL_SEVERITY)
    level_severity.update({str(k).lower(): int(v) for k, v in (levels.get("levels") or {}).items()})
    th_data = thresholds.get("thresholds") or {}
    unknown = set(th_data) - set(WasThresholds.__dataclass_fields__)
    if unknown:
        logger.warning("was_signatures.yaml 미지 키 무시: %s", sorted(unknown))
    return Policies(
        instance_map=instance_map,
        level_severity=level_severity,
        unknown_level_severity=int(levels.get("unknown_level_severity", DEFAULT_UNKNOWN_SEVERITY)),
        thresholds=WasThresholds.from_mapping(th_data),
    )


def load_config(
    env: Mapping[str, str] | None = None,
    *,
    policy_dir: Path = POLICY_DIR,
    dotenv: Path | None = PACKAGE_ROOT / ".env",
) -> GatewayConfig:
    """설정을 조립한다. `env`를 주면 그것만 본다(테스트 — `.env`·프로세스 환경 누수 차단)."""
    if env is None:
        if dotenv is not None:
            load_dotenv(dotenv)
        env = os.environ
    get = env.get
    interval = int(get("APM_EVENT_POLL_INTERVAL_SECONDS") or 30)
    if interval < POLL_INTERVAL_FLOOR:
        logger.warning("폴링 주기 %ss < 하한 %ss — 하한으로 올린다", interval, POLL_INTERVAL_FLOOR)
        interval = POLL_INTERVAL_FLOOR
    jennifer = JenniferApiConfig(
        url=(get("JENNIFER_API_URL") or "").strip(),
        token=(get("JENNIFER_API_TOKEN") or "").strip(),
        domain_ids=_int_list(get("JENNIFER_DOMAIN_IDS")),
        timeout_seconds=float(get("JENNIFER_API_TIMEOUT_SECONDS") or 10),
        rate_limit_per_sec=float(get("JENNIFER_RATE_LIMIT_PER_SEC") or 5),
        max_response_bytes=int(get("JENNIFER_MAX_RESPONSE_BYTES") or 4 * 1024 * 1024),
    )
    sources = _load_sources(get("JENNIFER_SOURCES"), jennifer, env)
    policies = load_policies(policy_dir)
    _warn_unknown_policy_sources(policies.instance_map, {s.source_id for s in sources})
    return GatewayConfig(
        jennifer=jennifer,
        sources=sources,
        server=ServerConfig(
            host=(get("APM_GATEWAY_HOST") or "127.0.0.1").strip(),
            port=int(get("APM_GATEWAY_PORT") or 9096),
            log_level=(get("APM_GATEWAY_LOG_LEVEL") or "INFO").strip(),
            bearer_token=(get("APM_GATEWAY_BEARER_TOKEN") or "").strip(),
        ),
        runtime=RuntimeConfig(
            timezone=(get("APM_TIMEZONE") or "Asia/Seoul").strip(),
            instance_cache_seconds=int(get("APM_INSTANCE_CACHE_SECONDS") or 600),
            profile_calls_per_investigation=int(get("APM_PROFILE_CALLS_PER_INVESTIGATION") or 5),
        ),
        poller=PollerConfig(
            enabled=_bool(get("APM_EVENT_POLLER_ENABLED"), False),
            interval_seconds=interval,
            min_level=(get("APM_EVENT_MIN_LEVEL") or "warning").strip().lower(),
            stream_key=(get("APM_EVENT_STREAM_KEY") or "alarm:raw").strip(),
        ),
        redis=RedisConfig(
            host=(get("REDIS_HOST") or "localhost").strip(),
            port=int(get("REDIS_PORT") or 6379),
            db=int(get("REDIS_DB") or 0),
            password=get("REDIS_PASSWORD") or "",
        ),
        policies=policies,
    )


def describe(cfg: GatewayConfig) -> dict[str, Any]:
    """기동 로그용 요약(비밀 값 없음 — 소스 id와 설정 여부만 · URL·토큰 값 없음)."""
    return {
        "sources": [
            {
                "id": s.source_id,
                "url_set": bool(s.url),
                "token_set": bool(s.token),
                "domain_filter": list(s.domain_ids),
            }
            for s in cfg.sources
        ],
        "bearer": bool(cfg.server.bearer_token),
        "poller": cfg.poller.enabled,
        "poll_interval": cfg.poller.interval_seconds,
        "overrides": len(cfg.policies.instance_map.get("overrides") or []),
    }
