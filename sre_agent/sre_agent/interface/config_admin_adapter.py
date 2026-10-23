"""interface — `AgentSettings`용 설정 관리 어댑터 (plans/191 W1 · D-345 · SPEC §3).

필드 표는 `AgentSettings.model_fields` 인트로스펙션으로 만든다. 쓰기 대상은 CWD `.env`이며,
CWD가 패키지 루트(`pyproject.toml`이 있는 곳)가 아니면 전체 쓰기를 막는다 — 레포 루트 CWD는
본체 `.env`/`.encenv`를 읽으므로 거기에 쓰면 두 주체가 같은 파일을 고친다(G-2).
"""

from __future__ import annotations

import re
import types
import typing
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, SecretStr, ValidationError
from pydantic_core import PydanticUndefined
from pydantic_settings import EnvSettingsSource, SettingsError

import sre_agent
from sre_agent import __version__
from sre_agent.interface.config_admin import (
    ComponentAdapter,
    FieldSpec,
    read_env_values,
)
from sre_agent.settings import AgentSettings

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = PACKAGE_ROOT / ".data" / "config_admin_audit.jsonl"
RESTART_HINT = "cd sre_agent && .venv/bin/python -m sre_agent.run_service"
# 웹 쓰기로 경계를 넓힐 수 있는 키는 읽기 전용(G-3 보정): 바인딩 호스트 · 자격증명이 따라가는 URL ·
# 가드 · 파일 경로 · 프롬프트 주입 경로. 꼴 규칙은 필드가 늘어도 자동으로 걸리게 한다.
_READONLY_EXPLICIT = frozenset(
    {"SERVICE_HOST", "BASH_WRITE_GUARD_ENABLED", "INVESTIGATION_GUIDANCE_EXTRA"}
)
_READONLY_PATTERN = re.compile(r"(_URL|_BASE|_PATH|_DIR|_FILE)$|GUARD|VERIFY")
# 그룹 분류 — 접두어 첫 일치. 순서가 의미를 가진다(`SRE_AGENT_ADMIN_`이 `SRE_`보다 앞).
_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "llm",
        "LLM",
        (
            "MODEL",
            "API_",
            "INVESTIGATION_LLM_",
            "GEMINI_",
            "OVERRIDE_MAX_",
            "MAX_STEPS",
            "SYSTEM_MESSAGE_",
            "TOOL_CALL_",
        ),
    ),
    ("mcp", "MCP 연결", ("POLESTAR_MCP_", "APM_MCP_")),
    ("service", "조사 서비스", ("SERVICE_", "SRE_AGENT_ADMIN_")),
    ("playbook", "플레이북", ("SRE_",)),
    ("evidence", "증거 사전수집", ("EVIDENCE_",)),
)
_DEFAULT_GROUP = ("investigation", "조사")


def _env_key(name: str, info: Any) -> str:
    """env 키 — validation_alias(AliasChoices면 첫 선택지) · alias 우선, 없으면 필드명 대문자."""
    alias = info.validation_alias or info.alias
    if isinstance(alias, AliasChoices):
        alias = next((c for c in alias.choices if isinstance(c, str)), None)
    return (alias if isinstance(alias, str) else name).upper()


def _field_type(annotation: Any) -> tuple[str, tuple[str, ...]]:
    """주석 → (필드 표 type, enum 선택지). Optional은 벗긴다."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _field_type(inner[0]) if len(inner) == 1 else ("string", ())
    if origin is Literal:
        return "enum", tuple(str(a) for a in typing.get_args(annotation))
    simple = {
        bool: "bool",
        int: "int",
        float: "float",
        SecretStr: "secret",
        list: "json_list",
        dict: "json_object",
    }
    return simple.get(origin or annotation, "string"), ()


def _group(env_key: str) -> str:
    for key, _title, prefixes in _GROUPS:
        if env_key.startswith(prefixes):
            return key
    return _DEFAULT_GROUP[0]


def readonly_keys(specs: list[FieldSpec]) -> frozenset[str]:
    """필드 표 중 읽기 전용 키(명시 목록 ∪ 꼴 규칙)."""
    keys = {s.env_key for s in specs}
    return frozenset(k for k in keys if k in _READONLY_EXPLICIT or _READONLY_PATTERN.search(k))


def _dry_run_settings_cls(env_keys: frozenset[str]) -> type[AgentSettings]:
    """프로세스 환경 원천을 `env_keys`로 거른 AgentSettings 하위 클래스(dry-run 전용).

    litellm이 import 시 `.env`를 os.environ에 넣어 두므로, 거르지 않으면 env 원천이 후보 파일을
    이겨 dry-run이 옛 값을 검증한다. `_load_env_vars`는 pydantic-settings 내부 메서드다(2.14 실측).
    """

    class _SnapshotEnv(EnvSettingsSource):
        def _load_env_vars(self):  # type: ignore[no-untyped-def]
            loaded = super()._load_env_vars()
            return {k: v for k, v in loaded.items() if k.upper() in env_keys}

    class _DryRunSettings(AgentSettings):
        @classmethod
        def settings_customise_sources(  # type: ignore[override]
            cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        ):
            return (
                init_settings,
                _SnapshotEnv(settings_cls),
                dotenv_settings,
                file_secret_settings,
            )

    return _DryRunSettings


def build_field_specs() -> list[FieldSpec]:
    """`AgentSettings` 필드 표. 시크릿 = `SecretStr` 필드(이 컴포넌트의 시크릿은 전부 SecretStr)."""
    specs: list[FieldSpec] = []
    for name, info in AgentSettings.model_fields.items():
        env_key = _env_key(name, info)
        kind, choices = _field_type(info.annotation)
        secret = kind == "secret"
        default = None if info.default is PydanticUndefined else info.default
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


def _effective(settings: AgentSettings, specs: list[FieldSpec]) -> dict[str, Any]:
    """기동 시 값. 시크릿은 설정됐으면 True, 아니면 None만 담는다."""
    by_key = {_env_key(n, i): n for n, i in AgentSettings.model_fields.items()}
    values: dict[str, Any] = {}
    for spec in specs:
        value = getattr(settings, by_key[spec.env_key])
        if spec.is_secret:
            values[spec.env_key] = True if value is not None else None
        else:
            values[spec.env_key] = value
    return values


def build_adapter(
    settings: AgentSettings,
    cwd: Path | None = None,
    package_root: Path | None = None,
    audit_path: Path | None = None,
    process_env_keys: frozenset[str] | None = None,
) -> ComponentAdapter:
    """`AgentSettings` 어댑터. 인자는 테스트 주입점이다(None = 실 경로 · import 시 환경 스냅샷)."""
    if process_env_keys is None:
        process_env_keys = sre_agent.PROCESS_ENV_KEYS_AT_IMPORT
    env_keys = frozenset(k.upper() for k in process_env_keys)
    dry_run_cls = _dry_run_settings_cls(env_keys)
    cwd = (cwd or Path.cwd()).resolve()
    package_root = package_root or PACKAGE_ROOT
    audit_path = audit_path or AUDIT_PATH
    dotenv, encenv = cwd / ".env", cwd / ".encenv"
    specs = build_field_specs()

    def write_block_reason() -> str | None:
        if cwd != package_root.resolve():
            return (
                f"기동 CWD({cwd})가 sre_agent 패키지 루트({package_root.resolve()})가 아니다 — "
                "이 CWD의 .env는 본체 설정일 수 있어 쓰지 않는다. 패키지 루트에서 기동하라"
            )
        return None

    def override_for(env_key: str) -> str | None:
        if env_key in env_keys:
            return "os"
        if any(k.upper() == env_key for k in read_env_values(encenv)):
            return "encenv"
        return None

    def dry_run(candidate: Path) -> list[str]:
        try:
            dry_run_cls(_env_file=(candidate, encenv))  # type: ignore[call-arg]
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
        name="sre_agent",
        version=__version__,
        restart_hint=RESTART_HINT,
        fields=specs,
        write_target=dotenv,
        audit_path=audit_path,
        dry_run=dry_run,
        cwd=cwd,
        group_titles={key: title for key, title, _ in _GROUPS} | dict([_DEFAULT_GROUP]),
        effective_values=_effective(settings, specs),
        readonly_keys=readonly_keys(specs),
        override_for=override_for,
        write_block_reason=write_block_reason,
        sources=sources,
    )
