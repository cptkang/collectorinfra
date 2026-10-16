"""운영자 설정 관련 라우트.

환경변수 설정 조회/수정, DB 연결 설정 조회/수정/테스트 엔드포인트를 제공한다.

책임별 하위 라우터(plans/166 A7)를 `router`가 원래 선언 위치에 포함한다 — 경로·이름·OpenAPI·
매칭 순서 불변: 설정 리로드 `admin_settings_reload` · 감사 로그 조회 `admin_audit_logs` ·
DRM 진단 `admin_drm`. `.env` 파일 I/O 본문은 `src.infrastructure.env_file_store`에 있고 이 모듈은
`_ENV_FILE`을 넘기는 얇은 함수만 둔다. 테스트가 이 모듈에서 patch하는 심볼(`_ENV_FILE` ·
`_atomic_write_env` · `_build_connection_string` · `build_catalog` ·
`_READINESS_TIMEOUT_SECONDS` · `get_registry` · `check_apm_sources` · `_audit_admin_action`)을
부르는 핸들러는 여기 남는다(D-337 ②).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.admin_audit import log_admin_event as _log_settings_event
from src.api.dependencies import require_admin_user
from src.api.error_detail import admin_error_detail
from src.api.settings_catalog import (
    FieldError,
    SettingsSchemaResponse,
    build_catalog,
    dry_run_updates,
    field_index,
    mask_value,
    validate_updates,
)
from src.api.settings_help import SettingHelp, build_help
from src.domain.user import UserRole, UserStatus
from src.routing.apm_source_health import SourceHealthReport, check_sources
from src.routing.registry import get_registry
from src.api.schemas import (
    AdminUserInfoResponse,
    UpdatePermissionsRequest,
    UpdateSourcePermissionsRequest,
    UpdateUserRequest,
    UserInfoResponse,
)
from src.api.routes.admin_audit_logs import (
    AuditLogFilterParams as AuditLogFilterParams,
)
from src.api.routes.admin_audit_logs import (
    AuditLogPageResponse as AuditLogPageResponse,
)
from src.api.routes.admin_audit_logs import get_audit_logs as get_audit_logs
from src.api.routes.admin_audit_logs import (
    get_audit_logs_paginated as get_audit_logs_paginated,
)
from src.api.routes.admin_audit_logs import get_audit_stats as get_audit_stats
from src.api.routes.admin_audit_logs import get_security_alerts as get_security_alerts
from src.api.routes.admin_audit_logs import get_user_activity as get_user_activity
from src.api.routes.admin_audit_logs import router as _audit_logs_router
from src.api.routes.admin_drm import get_drm_status as get_drm_status
from src.api.routes.admin_drm import router as _drm_router
from src.api.routes.admin_drm import verify_drm_sample as verify_drm_sample
from src.api.routes.admin_settings_reload import (
    SettingsReloadResponse as SettingsReloadResponse,
)
from src.api.routes.admin_settings_reload import reload_settings as reload_settings
from src.api.routes.admin_settings_reload import router as _settings_reload_router
from src.infrastructure.env_file_store import (
    atomic_write_env,
    compose_env_content,
    read_env_file,
    write_env_file,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# 프로젝트 루트 경로
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
# DEPRECATED: dbhub.toml은 MCP 서버 도입으로 더 이상 사용하지 않음.
# DB 연결 정보는 MCP 서버 VM의 config.toml + .env에서 관리한다.
_DBHUB_TOML_FILE = _PROJECT_ROOT / "dbhub.toml"

# 민감 키워드: 이 키워드가 포함된 설정값은 마스킹한다
_SENSITIVE_KEYWORDS = {
    "PASSWORD", "SECRET", "KEY", "TOKEN", "API_KEY",
    "APIKEY", "CREDENTIAL", "PRIVATE",
}

_MASK_VALUE = "********"

# ACTIVE_DB_IDS 저장 시 준비도 판정 전체 상한(plans/104 B-7) — 넘기면 경고로 알리고 저장은 유지한다
_READINESS_TIMEOUT_SECONDS = 20.0


# --- 요청/응답 모델 ---


class EnvSetting(BaseModel):
    """환경변수 설정 항목."""

    key: str = Field(..., description="설정 키")
    value: str = Field(..., description="설정 값 (민감 값은 마스킹)")
    is_sensitive: bool = Field(default=False, description="민감 값 여부")


class EnvSettingsResponse(BaseModel):
    """환경변수 설정 목록 응답."""

    settings: list[EnvSetting]
    env_file_path: str


class EnvUpdateRequest(BaseModel):
    """환경변수 설정 수정 요청."""

    settings: dict[str, str] = Field(
        default_factory=dict, description="수정할 설정값 (키: 값)"
    )
    reset_keys: list[str] = Field(
        default_factory=list, description="기본값으로 되돌릴 키 (.env에서 줄 제거)"
    )


class EnvUpdateResponse(BaseModel):
    """환경변수 설정 수정 응답."""

    updated_keys: list[str]
    message: str
    reset_keys: list[str] = Field(default_factory=list)
    requires_restart_keys: list[str] = Field(
        default_factory=list, description="재시작해야만 반영되는 키(apply_mode=restart)"
    )
    reload_keys: list[str] = Field(
        default_factory=list, description="'설정 리로드' 실행 시 반영되는 키(apply_mode=reload)"
    )
    applied_immediately_keys: list[str] = Field(default_factory=list)
    ignored_keys: list[str] = Field(
        default_factory=list, description="마스킹 값 수신으로 무시한 키(원값 보존)"
    )
    readiness_warnings: dict[str, Any] | None = Field(
        default=None,
        description=(
            "ACTIVE_DB_IDS에 새로 넣은 DB의 준비도 필수 미충족 항목 "
            "{db_id: [{code, label, detail}]} · 판정 실패면 {\"_error\": 사유} · "
            "경고가 없으면 null(plans/104 B-7 — 저장은 그대로 진행)"
        ),
    )


class DbConfigResponse(BaseModel):
    """DB 연결 설정 응답."""

    db_type: str = Field(default="", description="DB 유형 (postgresql, mysql, mariadb 등)")
    host: str = Field(default="", description="호스트")
    port: int = Field(default=5432, description="포트")
    database: str = Field(default="", description="데이터베이스명")
    username: str = Field(default="", description="사용자명")
    password: str = Field(default=_MASK_VALUE, description="비밀번호 (마스킹)")


class DbConfigUpdateRequest(BaseModel):
    """DB 연결 설정 수정 요청."""

    db_type: str = Field(..., description="DB 유형 (postgresql, mysql, mariadb)")
    host: str = Field(..., description="호스트")
    port: int = Field(..., ge=1, le=65535, description="포트")
    database: str = Field(..., description="데이터베이스명")
    username: str = Field(..., description="사용자명")
    password: str = Field(..., description="비밀번호")


class DbConfigUpdateResponse(BaseModel):
    """DB 연결 설정 수정 응답."""

    connection_string: str = Field(..., description="생성된 연결 문자열 (비밀번호 마스킹)")
    message: str


class DbTestRequest(BaseModel):
    """DB 연결 테스트 요청."""

    db_type: str = Field(..., description="DB 유형")
    host: str = Field(..., description="호스트")
    port: int = Field(..., ge=1, le=65535, description="포트")
    database: str = Field(..., description="데이터베이스명")
    username: str = Field(..., description="사용자명")
    password: str = Field(..., description="비밀번호")


class DbTestResponse(BaseModel):
    """DB 연결 테스트 응답."""

    success: bool
    message: str
    details: Optional[str] = None


# --- 유틸리티 ---


def _is_sensitive_key(key: str) -> bool:
    """키가 민감한 설정인지 확인한다."""
    upper_key = key.upper()
    return any(kw in upper_key for kw in _SENSITIVE_KEYWORDS)


def _has_url_credentials(value: str) -> bool:
    """값이 `scheme://user:password@host` 형태로 비밀번호를 품고 있는지 판정한다.

    `DB_CONNECTION_STRING`처럼 키 이름에 민감 키워드가 없어도 값에 비밀번호가 들어 있는
    설정을 마스킹 대상으로 잡기 위한 판정이다(자격증명은 authority 구간에만 존재).
    """
    if "://" not in value:
        return False
    authority = value.partition("://")[2].split("/", 1)[0]
    if "@" not in authority:
        return False
    return ":" in authority.rpartition("@")[0]


def _read_env_file() -> dict[str, str]:
    """환경변수 파일을 파싱한다(`_ENV_FILE` — 본문은 `env_file_store.read_env_file`).

    Returns:
        키-값 딕셔너리
    """
    return read_env_file(_ENV_FILE)


def _write_env_file(settings: dict[str, str]) -> None:
    """환경변수 파일을 작성한다(`_ENV_FILE` — 본문은 `env_file_store.write_env_file`).

    Args:
        settings: 업데이트할 키-값 딕셔너리
    """
    write_env_file(_ENV_FILE, settings)


def _field_error(key: str, message: str) -> FieldError:
    """필드 단위 검증 실패 항목을 만든다."""
    return FieldError(key=key, message=message)


def _error_detail(errors: list[FieldError]) -> dict:
    """검증 실패 목록을 HTTP 400 detail로 변환한다(필드별 사유 노출)."""
    return {
        "message": "설정 검증에 실패했습니다.",
        "errors": [{"key": e.key, "message": e.message} for e in errors],
    }


def _compose_env_content(
    updates: dict[str, str],
    reset_keys: set[str],
) -> str:
    """`.env` 새 내용을 만든다(`_ENV_FILE` — 본문은 `env_file_store.compose_env_content`).

    Args:
        updates: 새로 기록할 키-값
        reset_keys: 파일에서 제거할 키(기본값 복귀)

    Returns:
        새 파일 전체 내용
    """
    return compose_env_content(_ENV_FILE, updates, reset_keys)


def _atomic_write_env(content: str) -> None:
    """백업 → 임시 파일 → 원자 교체로 `.env`를 저장한다(`_ENV_FILE` — 본문은
    `env_file_store.atomic_write_env`).

    Args:
        content: 새 파일 전체 내용

    Raises:
        OSError: 파일 기록 실패 시(롤백 후 재전파)
    """
    atomic_write_env(_ENV_FILE, content)


async def _log_settings_update(
    request: Request,
    admin_id: Optional[str],
    changes: dict[str, dict[str, Optional[str]]],
    sensitive_keys: list[str],
    reset_keys: list[str],
    readiness_warnings: dict[str, Any] | None = None,
) -> bool:
    """설정 변경을 감사 로그에 기록한다.

    비민감 키는 이전값→새값 쌍을, 민감/시크릿 키는 **키 이름만** 남긴다.
    `ACTIVE_DB_IDS`에 준비도 필수 미충족 DB를 넣었으면 그 경고도 남긴다(plans/104 B-7 · G-7 (a)).
    """
    from src.domain.audit import AuditEvent

    extra: dict[str, Any] = {
        "changes": changes,
        "sensitive_keys": sensitive_keys,
        "reset_keys": reset_keys,
    }
    if readiness_warnings:
        extra["readiness_warnings"] = readiness_warnings
    return await _log_settings_event(
        request,
        admin_id,
        AuditEvent.SETTINGS_UPDATE.value,
        extra,
    )


def _parse_db_ids(value: Optional[str]) -> list[str]:
    """`ACTIVE_DB_IDS` 값(쉼표 구분)을 순서 보존·중복 제거 목록으로 바꾼다."""
    return list(dict.fromkeys(
        part.strip() for part in (value or "").split(",") if part.strip()
    ))


async def _active_db_readiness_warnings(
    request: Request, added_db_ids: list[str],
) -> dict[str, Any]:
    """새로 활성화한 DB의 준비도 필수 미충족 항목을 모은다(plans/104 B-7 · G-7 (a) 경고만).

    판정은 **실행 중 프로세스**의 설정(`app.state.config`)으로 한다(C2 — 레지스트리는 재기동 전까지
    실행 중인 것이 기준). 판정 자체가 실패하면 저장을 막지 않고 `{"_error": 사유}`를 돌려준다.

    Returns:
        `{db_id: [{code, label, detail}]}`(필수 미충족이 있는 DB만) 또는 `{"_error": 사유}`
    """
    config = getattr(request.app.state, "config", None)
    if config is None:
        return {"_error": "실행 중 설정을 찾지 못해 준비도를 판정하지 못했습니다"}

    try:
        from src.api.routes.db_structure import build_registration_service

        service = build_registration_service(config)
        reports = await asyncio.wait_for(
            service.readiness_many(added_db_ids),
            timeout=_READINESS_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.warning("ACTIVE_DB_IDS 준비도 판정 시간 초과: %s", added_db_ids)
        return {"_error": f"준비도 판정 시간 초과({_READINESS_TIMEOUT_SECONDS:g}초)"}
    except Exception as e:
        logger.warning("ACTIVE_DB_IDS 준비도 판정 실패: %s", added_db_ids)
        return {"_error": admin_error_detail("준비도 판정", e, logger, request)}

    warnings: dict[str, Any] = {}
    for db_id in added_db_ids:
        report = reports.get(db_id)
        if report is None:
            warnings[db_id] = [{
                "code": "unknown",
                "label": "준비도 판정 결과 없음",
                "detail": "판정 결과에 이 DB가 없습니다",
            }]
            continue
        unmet = [
            {"code": item.code, "label": item.label, "detail": item.detail}
            for item in report.unmet_required()
        ]
        if unmet:
            warnings[db_id] = unmet
    return warnings


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    """연결 문자열을 파싱한다.

    예: postgresql://user:pass@host:5432/dbname

    비밀번호는 평문으로 돌려주지 않는다. 파싱 결과는 화면 표시용이며 평문 비밀번호를
    응답·로그로 흘릴 이유가 없으므로, 비밀번호가 있으면 마스킹 값만 채운다.

    Args:
        conn_str: 연결 문자열

    Returns:
        파싱된 딕셔너리 (password는 마스킹 값 또는 빈 문자열)
    """
    result = {
        "db_type": "",
        "host": "",
        "port": "5432",
        "database": "",
        "username": "",
        "password": "",
    }

    if not conn_str:
        return result

    pattern = r"^(\w+)://([^:]+):([^@]*)@([^:]+):(\d+)/(.+)$"
    match = re.match(pattern, conn_str)
    if match:
        result["db_type"] = match.group(1)
        result["username"] = match.group(2)
        result["password"] = _MASK_VALUE if match.group(3) else ""
        result["host"] = match.group(4)
        result["port"] = match.group(5)
        result["database"] = match.group(6)

    return result


def _build_connection_string(config: DbConfigUpdateRequest) -> str:
    """연결 설정으로부터 연결 문자열을 생성한다.

    Args:
        config: DB 연결 설정

    Returns:
        연결 문자열
    """
    return (
        f"{config.db_type}://{config.username}:{config.password}"
        f"@{config.host}:{config.port}/{config.database}"
    )


def _update_dbhub_toml(
    db_type: str,
    connection_string: str,
) -> None:
    """dbhub.toml 파일을 업데이트한다.

    DEPRECATED: MCP 서버 도입으로 dbhub.toml은 더 이상 사용하지 않음.
    DB 연결 정보는 MCP 서버 VM의 config.toml + .env에서 관리한다.
    이 함수는 하위 호환성을 위해 유지하지만, 실행 시 경고를 기록한다.

    Args:
        db_type: DB 유형
        connection_string: 연결 문자열
    """
    logger.warning(
        "dbhub.toml 업데이트는 deprecated입니다. "
        "DB 연결 정보는 MCP 서버의 config.toml + .env에서 관리합니다."
    )


# --- 엔드포인트: 환경변수 설정 ---


@router.get(
    "/admin/settings",
    response_model=EnvSettingsResponse,
)
async def get_settings(
    _admin: dict = Depends(require_admin_user),
) -> EnvSettingsResponse:
    """환경변수 설정 목록을 조회한다.

    DEPRECATED: `.env`에 실존하는 키만 평면 목록으로 돌려준다(카탈로그·타입·반영 시점 메타 없음).
    운영자 UI는 `GET /admin/settings/schema`로 전환했으며, 이 엔드포인트는 하위호환용으로만 유지한다.

    민감한 설정값은 마스킹 처리된다.

    Args:
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        설정 목록
    """
    raw_settings = _read_env_file()
    settings_list = []

    for key, value in raw_settings.items():
        # 키 이름이 민감하지 않아도 값에 접속 비밀번호가 들어 있으면 마스킹한다.
        is_sensitive = _is_sensitive_key(key) or _has_url_credentials(value)
        settings_list.append(
            EnvSetting(
                key=key,
                value=mask_value(key, value) if is_sensitive else value,
                is_sensitive=is_sensitive,
            )
        )

    return EnvSettingsResponse(
        settings=settings_list,
        env_file_path=str(_ENV_FILE),
    )


@router.get(
    "/admin/settings/schema",
    response_model=SettingsSchemaResponse,
)
async def get_settings_schema(
    _admin: dict = Depends(require_admin_user),
) -> SettingsSchemaResponse:
    """설정 카탈로그(그룹·타입·메타)와 현재값을 조회한다 (Plan 68 / D-129).

    카탈로그는 `AppConfig` 인트로스펙션으로 생성되므로 config.py에 필드를 추가하면
    코드 수정 없이 UI에 편입된다. 시크릿은 값 없이 "설정됨/미설정" 상태만 반환한다.

    Returns:
        그룹별 설정 스키마
    """
    from src.config import AppConfig

    warnings: list[str] = []
    config: Optional[AppConfig] = None
    try:
        # lru_cache(load_config)를 우회한 fresh 인스턴스 — OS env/.encenv가 반영된 실효값
        config = AppConfig()
    except Exception as e:
        warnings.append(admin_error_detail("현재 적용값 계산", e, logger))

    if not _ENV_FILE.exists():
        warnings.append(f".env 파일이 없습니다({_ENV_FILE}). 저장 시 새로 생성됩니다.")

    if Path.cwd().resolve() != _PROJECT_ROOT:
        warnings.append(
            f"서버 작업 디렉토리({Path.cwd()})가 프로젝트 루트({_PROJECT_ROOT})와 다릅니다 — "
            "웹UI가 수정하는 .env와 애플리케이션이 읽는 .env가 다를 수 있습니다."
        )

    return build_catalog(
        file_values=_read_env_file(),
        config=config,
        os_environ=dict(os.environ),
        env_file_path=str(_ENV_FILE),
        warnings=warnings,
    )


@router.get(
    "/admin/settings/help/{env_key}",
    response_model=SettingHelp,
)
async def get_setting_help(
    env_key: str,
    _admin: dict = Depends(require_admin_user),
) -> SettingHelp:
    """설정 1건의 상세 도움말을 조회한다 (D-191).

    "이 값을 이렇게 두면 무엇이 어떻게 동작하는가"를 돌려준다. 큐레이션 YAML
    (`config/settings_help/`)이 있으면 그 내용을, 없으면 카탈로그 메타에서 결정적으로
    파생한 설명을 준다(`source` 필드로 구분). 반영 시점·오버라이드 경고(`operational`)는
    두 경우 모두 카탈로그 실측으로 채운다.

    Args:
        env_key: 설정 키

    Returns:
        도움말 1건

    Raises:
        HTTPException: 카탈로그에 없는 키면 404
    """
    if env_key not in field_index():
        raise HTTPException(status_code=404, detail=f"알 수 없는 설정 키입니다: {env_key}")

    # 오버라이드·현재값 표시는 카탈로그 실측에 의존한다(OS env/.encenv 우선순위 반영).
    # 실패해도 도움말 본문은 유효하므로 메타 없이 진행한다.
    item: Optional[dict] = None
    try:
        from src.config import AppConfig

        catalog = build_catalog(
            file_values=_read_env_file(),
            config=AppConfig(),
            os_environ=dict(os.environ),
            env_file_path=str(_ENV_FILE),
            warnings=[],
        )
        for group in catalog.groups:
            for setting in group.settings:
                if setting.env_key == env_key:
                    item = setting.model_dump()
                    break
            if item is not None:
                break
    except Exception as e:  # noqa: BLE001 — 메타 부재가 도움말을 막지 않는다
        logger.warning("도움말 메타 계산 실패 %s: %s", env_key, e)

    help_entry = build_help(env_key, item)
    if help_entry is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 설정 키입니다: {env_key}")
    return help_entry


@router.put(
    "/admin/settings",
    response_model=EnvUpdateResponse,
)
async def update_settings(
    request: Request,
    body: EnvUpdateRequest,
    _admin: dict = Depends(require_admin_user),
) -> EnvUpdateResponse:
    """환경변수 설정을 수정한다 (Plan 68 §3.2).

    처리 순서: 시크릿 차단 → 미지 키 거부 → 마스킹 값 무시 → sanitize → 타입 검증 →
    그룹 단위 pydantic dry-run → 백업·원자 교체(실패 시 롤백) → 캐시 무효화 → 감사 로그.

    Args:
        request: FastAPI Request
        body: 수정할 설정값과 초기화 키
        _admin: 인증된 관리자 (의존성 주입)

    Returns:
        수정 결과 (재시작 필요 키·리로드 반영 키·즉시 반영 키 3분류 포함)

    Raises:
        HTTPException: 검증 실패(400) 또는 저장 실패(500)
    """
    index = field_index()
    updates = dict(body.settings or {})
    reset_keys = list(dict.fromkeys(body.reset_keys or []))

    if not updates and not reset_keys:
        raise HTTPException(status_code=400, detail="수정할 설정이 없습니다.")

    before = _read_env_file()

    # 3. 마스킹 값 수신 → 해당 키 무시(서버측 원값 보존 가드)
    ignored_keys = [
        key for key, value in updates.items()
        if (spec := index.get(key)) is not None
        and spec.is_sensitive
        and isinstance(value, str)
        and _MASK_VALUE in value
    ]
    for key in ignored_keys:
        updates.pop(key)

    # 1·2·4·5. 시크릿 차단 / 미지 키 / sanitize / 타입 검증
    errors = list(validate_updates(updates))
    for key in reset_keys:
        spec = index.get(key)
        if spec is None:
            errors.append(_field_error(key, "카탈로그에 없는 설정 키입니다. 초기화할 수 없습니다."))
        elif spec.is_secret:
            errors.append(_field_error(key, ".encenv에서 관리하는 시크릿은 웹UI에서 변경할 수 없습니다."))
        elif key in updates:
            errors.append(_field_error(key, "같은 키를 수정과 초기화에 동시에 지정할 수 없습니다."))
    if errors:
        raise HTTPException(status_code=400, detail=_error_detail(errors))

    if not updates and not reset_keys:
        return EnvUpdateResponse(
            updated_keys=[],
            message="변경된 설정이 없습니다(마스킹 값은 무시됩니다).",
            ignored_keys=ignored_keys,
        )

    # 6. 그룹 단위 dry-run — 저장 후 상태로 실제 pydantic 모델을 재구성해 본다
    merged = {**before, **updates}
    for key in reset_keys:
        merged.pop(key, None)
    dry_run_errors = dry_run_updates(merged, list(updates) + reset_keys)
    if dry_run_errors:
        raise HTTPException(status_code=400, detail=_error_detail(dry_run_errors))

    # 7. 백업 → 임시 파일 → 원자 교체 (실패 시 롤백)
    try:
        _atomic_write_env(_compose_env_content(updates, set(reset_keys)))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=admin_error_detail("설정 저장", e, logger, request)
        )

    # 8. load_config 캐시 무효화 (즉시 반영 경로용 — 대부분의 설정은 재시작이 필요하다)
    from src.config import load_config
    load_config.cache_clear()

    # 8-1. (plans/104 B-7 · G-7 (a)) ACTIVE_DB_IDS에 새로 넣은 DB만 준비도를 판정해 경고한다.
    # 저장은 이미 끝났다 — 경고는 저장을 막지 않고, 추가 입력도 요구하지 않는다.
    readiness_warnings: dict[str, Any] = {}
    if "ACTIVE_DB_IDS" in updates or "ACTIVE_DB_IDS" in reset_keys:
        old_db_ids = _parse_db_ids(before.get("ACTIVE_DB_IDS"))
        added_db_ids = [
            db_id for db_id in _parse_db_ids(updates.get("ACTIVE_DB_IDS"))
            if db_id not in old_db_ids
        ]
        if added_db_ids:
            readiness_warnings = await _active_db_readiness_warnings(request, added_db_ids)

    # 9. 감사 로그 — 비민감 키는 old→new 쌍, 민감/시크릿 키는 키 이름만
    changes: dict[str, dict[str, Optional[str]]] = {}
    sensitive_keys: list[str] = []
    for key, value in updates.items():
        spec = index.get(key)
        if spec is not None and spec.is_sensitive:
            sensitive_keys.append(key)
        else:
            changes[key] = {"old": before.get(key), "new": value}
    audit_ok = await _log_settings_update(
        request, _admin.get("sub"), changes, sensitive_keys, reset_keys,
        readiness_warnings=readiness_warnings,
    )

    changed_keys = list(updates) + reset_keys
    requires_restart_keys = [
        key for key in changed_keys
        if (spec := index.get(key)) is None or spec.apply_mode == "restart"
    ]
    reload_keys = [
        key for key in changed_keys
        if (spec := index.get(key)) is not None and spec.apply_mode == "reload"
    ]
    applied_immediately_keys = [
        key for key in changed_keys
        if key not in requires_restart_keys and key not in reload_keys
    ]

    logger.info(
        "환경변수 설정 수정: updated=%s reset=%s (by %s)",
        list(updates), reset_keys, _admin.get("sub"),
    )

    message = f"{len(changed_keys)}개 설정이 저장되었습니다."
    if readiness_warnings:
        if "_error" in readiness_warnings:
            message += " (활성화 준비도를 판정하지 못했습니다 — 경고 참조)"
        else:
            message += (
                " (활성화 준비도 필수 미충족: "
                + ", ".join(readiness_warnings)
                + " — 저장은 완료되었습니다)"
            )
    if not audit_ok:
        message += " (감사 기록 불가 — 감사 저장소 미구성)"

    return EnvUpdateResponse(
        updated_keys=list(updates),
        reset_keys=reset_keys,
        requires_restart_keys=requires_restart_keys,
        reload_keys=reload_keys,
        applied_immediately_keys=applied_immediately_keys,
        ignored_keys=ignored_keys,
        readiness_warnings=readiness_warnings or None,
        message=message,
    )


# 설정 리로드 — `admin_settings_reload.py`(plans/166 A7). 포함 위치가 라우트 매칭 순서다.
router.include_router(_settings_reload_router)


# --- 엔드포인트: DB 연결 설정 ---


@router.get(
    "/admin/db-config",
    response_model=DbConfigResponse,
)
async def get_db_config(
    _admin: dict = Depends(require_admin_user),
) -> DbConfigResponse:
    """DB 연결 설정을 조회한다.

    비밀번호는 마스킹 처리된다.

    Args:
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        DB 연결 설정
    """
    env_settings = _read_env_file()
    conn_str = env_settings.get("DB_CONNECTION_STRING", "")
    parsed = _parse_connection_string(conn_str)

    return DbConfigResponse(
        db_type=parsed["db_type"],
        host=parsed["host"],
        port=int(parsed["port"]),
        database=parsed["database"],
        username=parsed["username"],
        password=_MASK_VALUE,  # 비밀번호는 항상 마스킹
    )


@router.put(
    "/admin/db-config",
    response_model=DbConfigUpdateResponse,
)
async def update_db_config(
    body: DbConfigUpdateRequest,
    _admin: dict = Depends(require_admin_user),
) -> DbConfigUpdateResponse:
    """DB 연결 설정을 수정한다.

    .env 파일과 dbhub.toml을 업데이트한다.

    Args:
        body: DB 연결 설정
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        수정 결과

    Raises:
        HTTPException: 저장 실패 시
    """
    try:
        conn_str = _build_connection_string(body)

        # .env 업데이트
        _write_env_file({"DB_CONNECTION_STRING": conn_str})

        # dbhub.toml 업데이트
        _update_dbhub_toml(body.db_type, conn_str)

        # load_config 캐시 무효화
        from src.config import load_config
        load_config.cache_clear()

        # 마스킹된 연결 문자열
        masked_conn = (
            f"{body.db_type}://{body.username}:{_MASK_VALUE}"
            f"@{body.host}:{body.port}/{body.database}"
        )

        logger.info(f"DB 연결 설정 수정: {masked_conn}")

        return DbConfigUpdateResponse(
            connection_string=masked_conn,
            message="DB 연결 설정이 업데이트되었습니다.",
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=admin_error_detail("DB 설정 저장", e, logger),
        )


@router.post(
    "/admin/db-config/test",
    response_model=DbTestResponse,
)
async def test_db_connection(
    body: DbTestRequest,
    _admin: dict = Depends(require_admin_user),
) -> DbTestResponse:
    """DB 연결을 테스트한다.

    입력된 정보로 실제 DB 연결을 시도하고 결과를 반환한다.

    Args:
        body: DB 연결 정보
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        연결 테스트 결과
    """
    try:
        if body.db_type == "postgresql":
            import asyncpg

            conn = await asyncpg.connect(
                host=body.host,
                port=body.port,
                user=body.username,
                password=body.password,
                database=body.database,
                timeout=10,
            )
            version = await conn.fetchval("SELECT version()")
            await conn.close()
            return DbTestResponse(
                success=True,
                message="DB 연결에 성공했습니다.",
                details=f"DB 버전: {version}",
            )
        else:
            # MySQL/MariaDB 등은 추후 지원
            return DbTestResponse(
                success=False,
                message=f"{body.db_type} 연결 테스트는 아직 지원하지 않습니다.",
                details="현재 PostgreSQL만 연결 테스트를 지원합니다.",
            )
    except ImportError:
        return DbTestResponse(
            success=False,
            message="DB 드라이버가 설치되지 않았습니다.",
            details="asyncpg 패키지를 설치해주세요: pip install asyncpg",
        )
    except Exception as e:
        return DbTestResponse(
            success=False,
            message=admin_error_detail("DB 연결 테스트", e, logger),
            details=None,
        )


# --- 엔드포인트: 사용자 관리 ---


async def _ensure_not_last_active_admin(user_repo, target_user_id: str) -> None:
    """대상 사용자를 강등/비활성화/삭제해도 활성 관리자가 최소 1명 남는지 확인한다.

    통합 RBAC(D-069)에서 role==admin이 실제 어드민 접근을 열므로, 마지막 관리자를
    잃으면 아무도 어드민 페이지에 못 들어가는 자기 잠금이 발생한다. 이를 차단한다.
    """
    users = await user_repo.list_all()
    other_admins = [
        u for u in users
        if u.role == UserRole.ADMIN and u.is_active and u.user_id != target_user_id
    ]
    if not other_admins:
        raise HTTPException(
            status_code=409,
            detail="최소 1명의 활성 관리자가 필요합니다. 마지막 관리자는 강등·비활성화·삭제할 수 없습니다.",
        )


async def _audit_admin_action(
    request: Request, admin: dict, action: str, **fields: Any
) -> None:
    """사용자 관리·감사 로그 정리를 `ADMIN_ACTION`으로 감사 기록한다.

    형식은 DB 구조 작업(`db_structure._audit`)과 같다 — `{"action": ..., 필드...}`.
    비밀번호 등 비밀값은 호출부가 넘기지 않는다. 기록 실패는 헬퍼가 경고 로그로 남긴다.
    """
    from src.domain.audit import AuditEvent

    await _log_settings_event(
        request,
        admin.get("sub"),
        AuditEvent.ADMIN_ACTION.value,
        {"action": action, **fields},
    )


def _user_audit_fields(user) -> dict[str, Any]:
    """감사 변경 전후 비교에 쓰는 사용자 속성(비밀값 제외)."""
    return {
        "username": user.username,
        "role": user.role.value,
        "department": user.department,
        "alarm_zones": list(user.alarm_zones) if user.alarm_zones is not None else None,
        "status": user.status.value,
    }


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """두 스냅샷에서 달라진 필드만 `{필드: {"before": 이전, "after": 이후}}`로 모은다."""
    return {
        key: {"before": before[key], "after": after[key]}
        for key in after
        if before.get(key) != after[key]
    }


@router.get(
    "/admin/users",
    response_model=list[AdminUserInfoResponse],
)
async def list_users(
    request: Request,
    _admin: dict = Depends(require_admin_user),
) -> list[AdminUserInfoResponse]:
    """사용자 목록을 조회한다.

    Args:
        request: FastAPI Request
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        사용자 목록
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    users = await user_repo.list_all()
    return [
        AdminUserInfoResponse(
            user_id=u.user_id,
            username=u.username,
            role=u.role.value,
            department=u.department,
            allowed_db_ids=u.allowed_db_ids,
            allowed_sources=u.allowed_sources,
            alarm_zones=u.alarm_zones,
            is_protected=u.is_protected,
            status=u.status.value,
            last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
        )
        for u in users
    ]


@router.put(
    "/admin/users/{user_id}",
    response_model=UserInfoResponse,
)
async def update_user(
    request: Request,
    user_id: str,
    body: UpdateUserRequest,
    _admin: dict = Depends(require_admin_user),
) -> UserInfoResponse:
    """사용자 정보를 수정한다 (역할/상태/부서 등).

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        body: 수정 요청
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        수정된 사용자 정보

    Raises:
        HTTPException: 사용자를 찾을 수 없을 때
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    user = await user_repo.get_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    # 보호 root 계정: 역할·상태는 변경 불가(부서·알림그룹 등 비권한 필드는 허용) — Plan 59-a §9
    if user.is_protected and (body.role is not None or body.status is not None):
        raise HTTPException(
            status_code=403,
            detail="보호된 root 계정의 역할·상태는 변경할 수 없습니다.",
        )

    before = _user_audit_fields(user)
    if body.username is not None:
        user.username = body.username
    if body.role is not None:
        new_role = UserRole(body.role)
        # 최소-1-admin 가드: 마지막 활성 관리자를 강등하지 못하게 한다(D-069)
        if user.role == UserRole.ADMIN and new_role != UserRole.ADMIN:
            await _ensure_not_last_active_admin(user_repo, user_id)
        user.role = new_role
    if body.department is not None:
        user.department = body.department
    if body.alarm_zones is not None:
        from src.routing.zones import normalize_zones
        user.alarm_zones = normalize_zones(body.alarm_zones)
    if body.status is not None:
        new_status = UserStatus(body.status)
        # 관리자를 비활성/잠금으로 바꾸는 것도 접근 회수이므로 동일 가드
        if user.role == UserRole.ADMIN and new_status != UserStatus.ACTIVE:
            await _ensure_not_last_active_admin(user_repo, user_id)
        user.status = new_status
        if body.status == "active":
            user.login_fail_count = 0

    await user_repo.update(user)
    logger.info("관리자가 사용자 수정: %s (by %s)", user_id, _admin.get("sub"))
    await _audit_admin_action(
        request, _admin, "user_update",
        target_user_id=user_id,
        changes=_changes(before, _user_audit_fields(user)),
    )

    return UserInfoResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role.value,
        department=user.department,
        allowed_db_ids=user.allowed_db_ids,
        alarm_zones=user.alarm_zones,
        is_protected=user.is_protected,
        status=user.status.value,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


@router.delete(
    "/admin/users/{user_id}",
)
async def delete_user(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """사용자를 삭제한다.

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        삭제 결과

    Raises:
        HTTPException: 사용자를 찾을 수 없을 때
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    target = await user_repo.get_by_user_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    # 보호 root 계정은 삭제 불가(Plan 59-a §9)
    if target.is_protected:
        raise HTTPException(status_code=403, detail="보호된 root 계정은 삭제할 수 없습니다.")

    # 최소-1-admin 가드: 마지막 활성 관리자는 삭제 불가(D-069)
    if target.role == UserRole.ADMIN and target.is_active:
        await _ensure_not_last_active_admin(user_repo, user_id)

    await user_repo.delete(user_id)
    logger.info("관리자가 사용자 삭제: %s (by %s)", user_id, _admin.get("sub"))
    await _audit_admin_action(
        request, _admin, "user_delete",
        target_user_id=user_id,
        before={
            "role": target.role.value,
            "status": target.status.value,
            "allowed_db_ids": target.allowed_db_ids,
        },
    )

    return {"message": f"사용자 '{user_id}'가 삭제되었습니다."}


@router.post(
    "/admin/users/{user_id}/reset-password",
)
async def reset_user_password(
    request: Request,
    user_id: str,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """사용자 비밀번호를 초기화한다.

    임시 비밀번호를 생성하여 설정하고, 사용자에게 알려준다.

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        임시 비밀번호 정보
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    user = await user_repo.get_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    # 보호 root 계정은 관리자 강제 PW초기화 불가(본인은 /auth/password로 변경) — Plan 59-a §9
    if user.is_protected:
        raise HTTPException(
            status_code=403,
            detail="보호된 root 계정은 비밀번호를 초기화할 수 없습니다.",
        )

    import secrets

    from src.utils.password import hash_password

    temp_password = secrets.token_urlsafe(12)
    user.hashed_password = hash_password(temp_password)
    user.login_fail_count = 0
    status_before = user.status.value
    if user.status.value == "locked":
        from src.domain.user import UserStatus
        user.status = UserStatus.ACTIVE

    await user_repo.update(user)
    logger.info("관리자가 비밀번호 초기화: %s (by %s)", user_id, _admin.get("sub"))
    # 임시 비밀번호는 남기지 않는다 — 초기화 사실과 잠금 해제 여부만 남긴다
    await _audit_admin_action(
        request, _admin, "user_password_reset",
        target_user_id=user_id,
        changes=_changes({"status": status_before}, {"status": user.status.value}),
    )

    return {
        "message": f"사용자 '{user_id}'의 비밀번호가 초기화되었습니다.",
        "temp_password": temp_password,
    }


@router.put(
    "/admin/users/{user_id}/permissions",
    response_model=UserInfoResponse,
)
async def update_user_permissions(
    request: Request,
    user_id: str,
    body: UpdatePermissionsRequest,
    _admin: dict = Depends(require_admin_user),
) -> UserInfoResponse:
    """사용자의 DB 접근 권한을 수정한다.

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        body: 권한 수정 요청
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        수정된 사용자 정보
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    user = await user_repo.get_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    allowed_before = user.allowed_db_ids
    user.allowed_db_ids = body.allowed_db_ids
    await user_repo.update(user)
    logger.info(
        "관리자가 사용자 권한 수정: %s -> allowed_db_ids=%s (by %s)",
        user_id, body.allowed_db_ids, _admin.get("sub"),
    )
    await _audit_admin_action(
        request, _admin, "user_permissions_update",
        target_user_id=user_id,
        changes=_changes(
            {"allowed_db_ids": allowed_before}, {"allowed_db_ids": user.allowed_db_ids}
        ),
    )

    return UserInfoResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role.value,
        department=user.department,
        allowed_db_ids=user.allowed_db_ids,
        status=user.status.value,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


@router.get("/admin/sources")
async def list_observation_sources(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, list[dict[str, Any]]]:
    """관측 소스 권한 편집의 후보 — DB 없는 시스템과 활성 여부(plans/125 A-7).

    활성 = 엔드포인트가 설정됨(D-283 ②). 문서 소스(`doc` · plans/127 G-8 (a))는 MCP 엔드포인트가
    아니라 문서 채팅 라우팅 설정이 활성을 정한다. 관리자 전용이다 — 공개 헬스 응답에 싣지 않는다
    (권한 밖 소스를 드러내지 않는다 · D-264 ② 선례).
    """
    from src.orchestration.doc_query import DOC_SYSTEM, doc_active

    config = request.app.state.config
    active = set(config.dbhub.active_source_codes())
    if doc_active(config):
        active.add(DOC_SYSTEM)
    return {
        "sources": [
            {"code": spec.code, "label": spec.label or spec.code, "active": spec.code in active}
            for spec in get_registry().non_db_systems()
        ]
    }


# --- 제니퍼 소스 정합 점검 (plans/147 W4 · D-322 ⑥) ---


def _apm_source_scope(config: Any) -> tuple[tuple[str, str | None] | None, str | None]:
    """(엔드포인트, 비대상 사유) — 점검 대상은 APM 활성 ∧ 레지스트리 소스 ≥2뿐이다."""
    from src.orchestration import apm_query as aq

    if not aq.apm_active(config):
        return None, "apm_inactive"
    if len(get_registry().sources_of(aq.APM_SYSTEM)) < 2:
        return None, "single_source"
    return aq.apm_endpoint(config), None


async def check_apm_sources(config: Any) -> SourceHealthReport | None:
    """게이트웨이 `gateway_health` 1회로 정합 보고서를 만든다 — 비대상 배포는 None(호출 0).

    예외를 올리지 않는다(점검 실패는 보고서의 `check_ok=False`).
    """
    from src.orchestration import apm_query as aq

    endpoint, _reason = _apm_source_scope(config)
    if endpoint is None:
        return None
    url, token = endpoint
    return await check_sources(url, token, get_registry().sources_of(aq.APM_SYSTEM),
                               session_factory=aq._SESSION_FACTORY)


@router.get("/admin/apm/sources")
async def get_apm_source_health(
    request: Request,
    refresh: bool = False,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """제니퍼 소스 정합 점검 결과 — 기동 시 보관한 보고서(`?refresh=1`이면 재점검해 보고만).

    재점검 결과는 사용 가능 소스 집합에 반영하지 않는다 — 반영은 재기동 시(D-322 ⑥ 「주기 재점검
    없음」).
    `applied_available`이 지금 질의에 쓰이는 집합이다(None = 전 소스). URL·토큰은 싣지 않는다.
    """
    from src.orchestration import apm_query as aq
    from src.routing.apm_source_select import get_available_apm_sources

    config = request.app.state.config
    endpoint, reason = _apm_source_scope(config)
    report: SourceHealthReport | None = getattr(request.app.state, "apm_source_report", None)
    refreshed = False
    if endpoint is not None and refresh:
        fresh = await check_apm_sources(config)
        if fresh is not None:
            report, refreshed = fresh, True
    applied = get_available_apm_sources() if endpoint is not None else None
    order = [s.id for s in get_registry().sources_of(aq.APM_SYSTEM)]
    return {
        "active": endpoint is not None,
        "reason": reason,
        "refreshed": refreshed,
        "applied_available": None if applied is None else [sid for sid in order if sid in applied],
        "report": report.to_dict() if (endpoint is not None and report is not None) else None,
    }


@router.put(
    "/admin/users/{user_id}/source-permissions",
    response_model=AdminUserInfoResponse,
)
async def update_user_source_permissions(
    request: Request,
    user_id: str,
    body: UpdateSourcePermissionsRequest,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> AdminUserInfoResponse:
    """사용자의 관측 소스 권한을 수정한다(plans/125 A-7 · D-232 권한 편집의 확장).

    DB 권한과 별도 요청이라 한쪽을 저장해도 다른 쪽은 그대로다.
    """
    user_repo = getattr(request.app.state, "user_repo", None)
    if not user_repo:
        raise HTTPException(status_code=503, detail="인증 서비스를 사용할 수 없습니다.")

    user = await user_repo.get_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    before = user.allowed_sources
    user.allowed_sources = body.allowed_sources
    await user_repo.update(user)
    logger.info(
        "관리자가 사용자 관측 소스 권한 수정: %s -> allowed_sources=%s (by %s)",
        user_id, body.allowed_sources, _admin.get("sub"),
    )
    await _audit_admin_action(
        request, _admin, "user_source_permissions_update",
        target_user_id=user_id,
        changes=_changes({"allowed_sources": before}, {"allowed_sources": user.allowed_sources}),
    )

    return AdminUserInfoResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role.value,
        department=user.department,
        allowed_db_ids=user.allowed_db_ids,
        allowed_sources=user.allowed_sources,
        status=user.status.value,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


# 감사 로그 조회 — `admin_audit_logs.py`(plans/166 A7). 포함 위치가 라우트 매칭 순서다.
router.include_router(_audit_logs_router)


@router.post(
    "/admin/audit/cleanup",
)
async def cleanup_audit_logs(
    request: Request,
    retention_days: Optional[int] = None,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """보관 기간이 지난 감사 로그를 즉시 삭제한다(Plan 59-a §11, 수동 트리거).

    retention_days 미지정 시 설정값(AuditConfig.retention_days)을 사용한다.

    Returns:
        {"deleted": 삭제 건수, "retention_days": 적용 보관일수}
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        raise HTTPException(status_code=503, detail="감사 저장소를 사용할 수 없습니다.")

    days = retention_days if retention_days is not None else request.app.state.config.audit.retention_days
    if days is None or days <= 0:
        raise HTTPException(status_code=400, detail="보관 일수는 1 이상이어야 합니다.")

    try:
        deleted = await audit_repo.cleanup_old_logs(days)
    except Exception as e:
        logger.error("감사 로그 정리 실패: %s", e)
        raise HTTPException(status_code=500, detail="감사 로그 정리 중 오류가 발생했습니다.")

    logger.info("관리자 감사 로그 정리: %s일 경과 %s건 삭제 (by %s)", days, deleted, _admin.get("sub"))
    await _audit_admin_action(
        request, _admin, "audit_cleanup", retention_days=days, deleted=deleted,
    )
    return {"deleted": deleted, "retention_days": days}


# DRM 연동 진단 — `admin_drm.py`(plans/166 A7). 포함 위치가 라우트 매칭 순서다.
router.include_router(_drm_router)


# ──────────────────────────────────────────────
# 소스 선택 기억(plans/132 W5 · §6.5 · G-11) — 조회·삭제·승격
# ──────────────────────────────────────────────

_SOURCE_MEMORY_SCOPE_RE = re.compile(r"^(org|user:[\w.@\-]{1,64})$")
_SOURCE_MEMORY_OFF = "소스 선택 기억 기능이 꺼져 있거나 저장소가 없습니다."


async def _source_memory_store(request: Request) -> Any:
    """기억 저장소 — 기능 off(TTL 0)이거나 Redis가 없으면 None."""
    from src.schema_cache import source_memory as sm

    config = request.app.state.config
    if sm.ttl_seconds(config) <= 0:
        return None
    return await sm.open_store(config)


def _check_source_memory_scope(scope: str) -> None:
    if not _SOURCE_MEMORY_SCOPE_RE.match(scope or ""):
        raise HTTPException(status_code=400, detail="알 수 없는 기억 범위입니다.")


@router.get("/admin/source-memory")
async def list_source_memory(
    request: Request,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """소스 선택 기억 — 기능 상태 · 범위별 사례(개인 · 조직 공용) · 시드(git 정본)."""
    from src.schema_cache import source_memory as sm

    config = request.app.state.config
    reg = get_registry()
    ttl_days = sm.ttl_seconds(config) // 86400
    seeds = [{"text": c["text"], "sources": c["sources"], "areas": c["areas"],
              "source_labels": [reg.system_label(x) for x in c["sources"]]}
             for c in sm.seed_cases(reg)]
    store = await _source_memory_store(request)
    scopes: list[dict[str, Any]] = []
    if store is not None:
        for scope in await store.scopes():
            scopes.append({"scope": scope, "cases": [
                {**case, "source_labels": [reg.system_label(s) for s in case.get("sources") or []]}
                for case in await store.load(scope)
            ]})
    return {"enabled": ttl_days > 0, "ttl_days": ttl_days, "connected": store is not None,
            "scopes": scopes, "seeds": seeds}


@router.delete("/admin/source-memory/{scope}/{case_id}")
async def delete_source_memory(
    request: Request,
    scope: str,
    case_id: str,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """사례 1건을 지운다(개인·조직 공용 모두)."""
    _check_source_memory_scope(scope)
    store = await _source_memory_store(request)
    if store is None:
        raise HTTPException(status_code=409, detail=_SOURCE_MEMORY_OFF)
    removed = await store.delete(scope, case_id)
    if not removed:
        raise HTTPException(status_code=404, detail="기억 사례를 찾을 수 없습니다.")
    await _audit_admin_action(request, _admin, "source_memory_delete", scope=scope, case_id=case_id)
    return {"message": "기억 사례를 지웠습니다.", "removed": removed}


@router.post("/admin/source-memory/{scope}/{case_id}/promote")
async def promote_source_memory(
    request: Request,
    scope: str,
    case_id: str,
    _admin: dict[str, Any] = Depends(require_admin_user),
) -> dict[str, Any]:
    """개인 사례를 조직 공용으로 승격한다(관리자 승인 — 다른 사용자 판정에도 쓰인다 · G-11 (a))."""
    _check_source_memory_scope(scope)
    if scope == "org":
        raise HTTPException(status_code=400, detail="이미 조직 공용 사례입니다.")
    store = await _source_memory_store(request)
    if store is None:
        raise HTTPException(status_code=409, detail=_SOURCE_MEMORY_OFF)
    promoted = await store.promote(scope, case_id, by=str(_admin.get("sub") or ""))
    if promoted is None:
        raise HTTPException(status_code=404, detail="기억 사례를 찾을 수 없습니다.")
    await _audit_admin_action(request, _admin, "source_memory_promote", scope=scope,
                              case_id=case_id, org_case_id=promoted["case_id"])
    return {"message": "조직 공용 사례로 승격했습니다.", "case": promoted}
