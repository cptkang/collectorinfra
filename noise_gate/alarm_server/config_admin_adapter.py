"""alarm_server — `AlarmServerConfig`용 설정 관리 어댑터 (plans/191 W5 · D-345 · SPEC §3).

필드 표는 `AlarmServerConfig.model_fields` 인트로스펙션으로 만든다. 쓰기 대상은 CWD
`alarm_server.env`다. 이 파일이 없으면 전체 쓰기를 막는다 — 없는 상태에서 쓰면 공용 `.env`를
고치게 되거나 빈 전용 파일이 생겨 운영자가 원천을 오해한다(G-4).
로더 순서는 `[.env, alarm_server.env]`(뒤 파일 우선)라 전용 파일이 공용 `.env`를 이긴다.
`.env`는 보충 원천(`fallback`)일 뿐 덮어쓰기 원천이 아니므로 `.env`에 있는 키도 쓸 수 있다
(쓰면 전용 파일 값이 적용된다 · D-345 부기 2026-10-21).
"""

from __future__ import annotations

import os
import types
import typing
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_core import PydanticUndefined
from pydantic_settings import SettingsError

from noise_gate.alarm_server.config import AlarmServerConfig
from noise_gate.alarm_server.config_admin import ComponentAdapter, FieldSpec

VERSION = "0.1.0"  # 루트 pyproject 버전 — alarm_server는 별도 배포 버전이 없다
WRITE_TARGET_NAME = "alarm_server.env"
AUDIT_NAME = "alarm_server_config_audit.jsonl"
RESTART_HINT = "python -m noise_gate.alarm_server  (레포 루트에서)"
ENV_PREFIX = "ALARM_SERVER_"
SECRET_KEYS = frozenset({"ALARM_SERVER_REDIS_PASSWORD", "ALARM_SERVER_ADMIN_TOKEN"})
# 웹 쓰기로 경계를 넓힐 수 있는 키는 읽기 전용(D-345 · G-3 보정): 바인딩 노출 · 관리 리스너 자체 ·
# 알람 적재 대상(비밀번호가 따라가는 Redis 호스트 · 소비자가 읽는 스트림 키).
READONLY_KEYS = frozenset(
    {
        "ALARM_SERVER_SOCKET_HOST",
        "ALARM_SERVER_ADMIN_HOST",
        "ALARM_SERVER_ADMIN_PORT",
        "ALARM_SERVER_REDIS_HOST",
        "ALARM_SERVER_STREAM_KEY",
    }
)
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("socket", "소켓 수신", ("ALARM_SERVER_SOCKET_",)),
    ("redis", "Redis", ("ALARM_SERVER_REDIS_", "ALARM_SERVER_STREAM_")),
    ("admin", "설정 관리", ("ALARM_SERVER_ADMIN_",)),
)
_DEFAULT_GROUP = ("logging", "로그")


def _field_type(annotation: Any) -> str:
    """주석 → 필드 표 type. Optional은 벗긴다."""
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        annotation = inner[0] if len(inner) == 1 else str
    return {bool: "bool", int: "int", float: "float"}.get(annotation, "string")


def _group(env_key: str) -> str:
    for key, _title, prefixes in _GROUPS:
        if env_key.startswith(prefixes):
            return key
    return _DEFAULT_GROUP[0]


def build_field_specs() -> list[FieldSpec]:
    """`AlarmServerConfig` 필드 표. 시크릿은 명시 목록(`SECRET_KEYS`)으로 판정한다."""
    specs: list[FieldSpec] = []
    for name, info in AlarmServerConfig.model_fields.items():
        env_key = f"{ENV_PREFIX}{name}".upper()
        secret = env_key in SECRET_KEYS
        default = None if info.default is PydanticUndefined else info.default
        specs.append(
            FieldSpec(
                env_key=env_key,
                group_key=_group(env_key),
                type="secret" if secret else _field_type(info.annotation),
                default=None if secret else default,
                description=info.description or "",
                is_secret=secret,
            )
        )
    return specs


def _effective(config: AlarmServerConfig, specs: list[FieldSpec]) -> dict[str, Any]:
    """기동 시 값. 시크릿은 비어 있지 않으면 True, 아니면 None만 담는다."""
    values: dict[str, Any] = {}
    for spec in specs:
        value = getattr(config, spec.env_key[len(ENV_PREFIX) :].lower())
        values[spec.env_key] = (True if value else None) if spec.is_secret else value
    return values


def build_adapter(
    config: AlarmServerConfig,
    cwd: Path | None = None,
    audit_path: Path | None = None,
) -> ComponentAdapter:
    """`AlarmServerConfig` 어댑터. cwd·audit_path는 테스트 주입점이다(None = 실 경로)."""
    cwd = (cwd or Path.cwd()).resolve()
    target, dotenv = cwd / WRITE_TARGET_NAME, cwd / ".env"
    audit_path = audit_path or cwd / "logs" / AUDIT_NAME
    specs = build_field_specs()

    def write_block_reason() -> str | None:
        if not target.is_file():
            return (
                f"전용 설정 파일({target})이 없다 — 공용 .env에는 쓰지 않는다. 먼저 전용 파일을 "
                f"만들어라: cp noise_gate/alarm_server/alarm_server.env.example {WRITE_TARGET_NAME}"
            )
        return None

    def override_for(env_key: str) -> str | None:
        if any(k.upper() == env_key for k in os.environ):
            return "os"
        return None

    def dry_run(candidate: Path) -> list[str]:
        try:
            AlarmServerConfig(_env_file=[dotenv, candidate])  # type: ignore[call-arg]
        except ValidationError as exc:
            return [
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in exc.errors(include_url=False, include_input=False)
            ]
        except (SettingsError, ValueError) as exc:
            return [f"설정 로드 실패: {type(exc).__name__}"]
        return []

    def sources() -> list[dict[str, Any]]:
        return [
            {"path": str(dotenv), "role": "fallback", "exists": dotenv.is_file(), "priority": 1},
            {
                "path": str(target),
                "role": "write_target",
                "exists": target.is_file(),
                "priority": 2,
            },
            {"path": "(process environment)", "role": "override", "exists": True, "priority": 3},
        ]

    return ComponentAdapter(
        name="alarm_server",
        version=VERSION,
        restart_hint=RESTART_HINT,
        fields=specs,
        write_target=target,
        audit_path=audit_path,
        dry_run=dry_run,
        cwd=cwd,
        group_titles={key: title for key, title, _ in _GROUPS} | dict([_DEFAULT_GROUP]),
        effective_values=_effective(config, specs),
        readonly_keys=READONLY_KEYS,
        override_for=override_for,
        write_block_reason=write_block_reason,
        sources=sources,
    )
