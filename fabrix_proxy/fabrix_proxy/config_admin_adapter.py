"""`ProxySettings`용 설정 관리 어댑터·배선 (plans/191 W5 · D-345 · SPEC-component-config-admin §3).

필드 표는 `ProxySettings.model_fields` 인트로스펙션으로 만든다. 쓰기 대상은 패키지 루트 `.env`
(설정이 패키지 루트 고정이라 CWD와 무관)이고, `.encenv`가 `.env`를 이기므로 `.encenv`에 있는 키는
`override_encenv`, OS 환경변수 키는 `override_os`다. 자격증명 키는 명시 목록으로 시크릿 처리한다.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import types
import typing
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError
from pydantic_core import PydanticUndefined
from pydantic_settings import SettingsError
from starlette.types import ASGIApp

from fabrix_proxy.config import PACKAGE_ROOT, ProxySettings
from fabrix_proxy.config_admin import (
    AdminPathRouter,
    ComponentAdapter,
    FieldSpec,
    build_admin_app,
    read_env_values,
)

logger = logging.getLogger("fabrix_proxy")

VERSION = "0.1.0"  # pyproject `fabrix-proxy` 버전
AUDIT_PATH = PACKAGE_ROOT / "logs" / "config_admin_audit.jsonl"
RESTART_HINT = "cd fabrix_proxy && python -m fabrix_proxy"
SECRET_KEYS = frozenset(
    {"FABRIX_PROXY_TOKEN", "FABRIX_API_KEY", "FABRIX_CLIENT_KEY", "FABRIX_PROXY_ADMIN_TOKEN"}
)
# 웹 쓰기로 경계를 넓힐 수 있는 키는 읽기 전용(D-345 · G-3 보정): 바인딩 호스트 ·
# 자격증명이 따라가는 URL · TLS 검증 · 프롬프트 주입 파일 경로.
# 꼴 규칙은 필드가 늘어도 자동으로 걸리게 한다.
_READONLY_EXPLICIT = frozenset({"FABRIX_PROXY_HOST"})
_READONLY_PATTERN = re.compile(r"(_URL|_BASE|_PATH|_DIR|_FILE)$|GUARD|VERIFY")
READONLY_KEYS = frozenset(
    name.upper()
    for name in ProxySettings.model_fields
    if name.upper() in _READONLY_EXPLICIT or _READONLY_PATTERN.search(name.upper())
)
# 그룹 분류 — 접두어 첫 일치. 순서가 의미를 가진다(`FABRIX_PROXY_`가 `FABRIX_`보다 앞).
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "surface",
        "프록시 표면",
        (
            "FABRIX_PROXY_TOKEN",
            "FABRIX_PROXY_HOST",
            "FABRIX_PROXY_PORT",
            "FABRIX_PROXY_LOG_LEVEL",
            "FABRIX_PROXY_MODEL_ALIASES",
            "FABRIX_PROXY_ADMIN_",
        ),
    ),
    ("emulation", "에뮬레이션", ("FABRIX_PROXY_",)),
    ("upstream", "업스트림 KBGenAI", ("FABRIX_",)),
)
_DEFAULT_GROUP = ("etc", "기타")


def _field_type(annotation: Any) -> tuple[str, tuple[str, ...]]:
    """주석 → (필드 표 type, enum 선택지). Optional은 벗긴다."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _field_type(inner[0]) if len(inner) == 1 else ("string", ())
    if origin is Literal:
        return "enum", tuple(str(a) for a in typing.get_args(annotation))
    simple = {bool: "bool", int: "int", float: "float", list: "json_list", dict: "json_object"}
    return simple.get(origin or annotation, "string"), ()


def _group(env_key: str) -> str:
    for key, _title, prefixes in _GROUPS:
        if env_key.startswith(prefixes):
            return key
    return _DEFAULT_GROUP[0]


def build_field_specs() -> list[FieldSpec]:
    """`ProxySettings` 필드 표. 시크릿은 명시 목록(`SECRET_KEYS`)으로 판정한다."""
    specs: list[FieldSpec] = []
    for name, info in ProxySettings.model_fields.items():
        env_key = name.upper()
        secret = env_key in SECRET_KEYS
        kind, choices = ("secret", ()) if secret else _field_type(info.annotation)
        if info.default is not PydanticUndefined:
            default = info.default
        elif info.default_factory is not None:
            default = info.default_factory()  # type: ignore[call-arg]
        else:
            default = None
        specs.append(
            FieldSpec(
                env_key=env_key,
                group_key=_group(env_key),
                type=kind,
                default=None if secret else default,
                description=info.description or "",
                enum_choices=choices,
                is_secret=secret,
            )
        )
    return specs


def _effective(settings: ProxySettings, specs: list[FieldSpec]) -> dict[str, Any]:
    """기동 시 값. 시크릿은 비어 있지 않으면 True, 아니면 None만 담는다."""
    values: dict[str, Any] = {}
    for spec in specs:
        value = getattr(settings, spec.env_key.lower())
        values[spec.env_key] = (True if value else None) if spec.is_secret else value
    return values


def build_adapter(
    settings: ProxySettings,
    package_root: Path | None = None,
    audit_path: Path | None = None,
) -> ComponentAdapter:
    """`ProxySettings` 어댑터. package_root·audit_path는 테스트 주입점이다(None = 실 경로)."""
    root = (package_root or PACKAGE_ROOT).resolve()
    dotenv, encenv = root / ".env", root / ".encenv"
    specs = build_field_specs()

    def override_for(env_key: str) -> str | None:
        if any(k.upper() == env_key for k in os.environ):
            return "os"
        if any(k.upper() == env_key for k in read_env_values(encenv)):
            return "encenv"
        return None

    def dry_run(candidate: Path) -> list[str]:
        try:
            ProxySettings(_env_file=(candidate, encenv))  # type: ignore[call-arg]
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
            {
                "path": str(dotenv),
                "role": "write_target",
                "exists": dotenv.is_file(),
                "priority": 1,
            },
            {"path": str(encenv), "role": "override", "exists": encenv.is_file(), "priority": 2},
            {"path": "(process environment)", "role": "override", "exists": True, "priority": 3},
        ]

    return ComponentAdapter(
        name="fabrix_proxy",
        version=VERSION,
        restart_hint=RESTART_HINT,
        fields=specs,
        write_target=dotenv,
        audit_path=audit_path or AUDIT_PATH,
        dry_run=dry_run,
        cwd=Path.cwd(),
        group_titles={key: title for key, title, _ in _GROUPS} | dict([_DEFAULT_GROUP]),
        effective_values=_effective(settings, specs),
        readonly_keys=READONLY_KEYS,
        override_for=override_for,
        sources=sources,
    )


def wrap_admin(settings: ProxySettings, app: ASGIApp, **adapter_kwargs: Any) -> ASGIApp:
    """관리 토큰이 있고 프록시 토큰과 다를 때만 `/admin/config` 경로 분기 래퍼로 감싼다.

    토큰이 없으면 `app`을 그대로 돌려준다(현행 비트 동일). 프록시 토큰과 같으면 등록하지 않고
    경고 1줄을 남긴다(G-1 — 소비자 토큰으로 설정을 바꿀 수 있게 되므로).
    """
    admin_token = settings.fabrix_proxy_admin_token.strip()
    if not admin_token:
        return app
    if hmac.compare_digest(admin_token.encode(), settings.fabrix_proxy_token.strip().encode()):
        logger.warning(
            "FABRIX_PROXY_ADMIN_TOKEN이 프록시 토큰과 같아 설정 관리 라우트를 등록하지 않는다"
        )
        return app
    return AdminPathRouter(
        app, build_admin_app(build_adapter(settings, **adapter_kwargs), admin_token)
    )
