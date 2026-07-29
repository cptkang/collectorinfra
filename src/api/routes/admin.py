"""운영자 설정 관련 라우트.

환경변수 설정 조회/수정, DB 연결 설정 조회/수정/테스트 엔드포인트를 제공한다.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.dependencies import require_admin_user
from src.api.settings_catalog import (
    FieldError,
    SettingsSchemaResponse,
    build_catalog,
    dry_run_updates,
    field_index,
    validate_updates,
)
from src.domain.user import UserRole, UserStatus
from src.api.schemas import (
    UpdatePermissionsRequest,
    UpdateUserRequest,
    UserInfoResponse,
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
    requires_restart_keys: list[str] = Field(default_factory=list)
    applied_immediately_keys: list[str] = Field(default_factory=list)
    ignored_keys: list[str] = Field(
        default_factory=list, description="마스킹 값 수신으로 무시한 키(원값 보존)"
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


class AuditLogFilterParams(BaseModel):
    """감사 로그 조회 필터."""

    start_date: Optional[str] = None
    end_date: Optional[str] = None
    user_id: Optional[str] = None
    event_type: Optional[str] = None
    target_db: Optional[str] = None
    success: Optional[bool] = None
    keyword: Optional[str] = None
    page: int = 1
    page_size: int = 50


class AuditLogPageResponse(BaseModel):
    """페이지네이션 감사 로그 응답."""

    logs: list[dict]
    total: int
    page: int
    page_size: int
    total_pages: int


# --- 유틸리티 ---


def _is_sensitive_key(key: str) -> bool:
    """키가 민감한 설정인지 확인한다."""
    upper_key = key.upper()
    return any(kw in upper_key for kw in _SENSITIVE_KEYWORDS)


def _read_env_file() -> dict[str, str]:
    """환경변수 파일을 파싱한다.

    Returns:
        키-값 딕셔너리
    """
    if not _ENV_FILE.exists():
        return {}

    settings: dict[str, str] = {}
    with open(_ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # 따옴표 제거
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            settings[key] = value
    return settings


def _write_env_file(settings: dict[str, str]) -> None:
    """환경변수 파일을 작성한다.

    기존 파일의 주석과 빈 줄을 보존하면서 값을 업데이트한다.

    Args:
        settings: 업데이트할 키-값 딕셔너리
    """
    existing_lines: list[str] = []
    updated_keys: set[str] = set()

    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding="utf-8") as f:
            existing_lines = f.readlines()

    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in settings:
                new_lines.append(f"{key}={settings[key]}\n")
                updated_keys.add(key)
                continue
        new_lines.append(line)

    # 새로운 키 추가
    for key, value in settings.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}\n")

    with open(_ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


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
    """`.env` 새 내용을 만든다 (주석·순서 보존 + 중복 키 정리 + reset 줄 제거).

    중복 등재된 키는 **첫 줄 위치에 마지막 유효값**을 남기고 이후 줄을 제거한다
    (`_read_env_file`이 마지막 값을 채택하므로 실효값이 바뀌지 않는다).

    Args:
        updates: 새로 기록할 키-값
        reset_keys: 파일에서 제거할 키(기본값 복귀)

    Returns:
        새 파일 전체 내용
    """
    existing_lines: list[str] = []
    if _ENV_FILE.exists():
        with open(_ENV_FILE, encoding="utf-8") as f:
            existing_lines = f.readlines()

    resolved = _read_env_file()  # 중복 키는 마지막 값이 실효값
    occurrences: dict[str, int] = {}
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            occurrences[key] = occurrences.get(key, 0) + 1

    new_lines: list[str] = []
    written: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key in reset_keys:
            continue
        if key in written:
            continue  # 중복 등재 — 첫 줄에 이미 기록했다
        written.add(key)

        if key in updates:
            new_lines.append(f"{key}={updates[key]}\n")
        elif occurrences.get(key, 0) > 1:
            new_lines.append(f"{key}={resolved.get(key, '')}\n")
        else:
            new_lines.append(line)

    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    for key, value in updates.items():
        if key not in written:
            new_lines.append(f"{key}={value}\n")

    return "".join(new_lines)


def _atomic_write_env(content: str) -> None:
    """백업 → 임시 파일 → 원자 교체로 `.env`를 저장한다(실패 시 롤백).

    Args:
        content: 새 파일 전체 내용

    Raises:
        OSError: 파일 기록 실패 시(롤백 후 재전파)
    """
    original: Optional[str] = None
    if _ENV_FILE.exists():
        original = _ENV_FILE.read_text(encoding="utf-8")
        backup = _ENV_FILE.parent / (_ENV_FILE.name + ".bak")
        backup.write_text(original, encoding="utf-8")

    tmp_file = _ENV_FILE.parent / (_ENV_FILE.name + ".tmp")
    try:
        tmp_file.write_text(content, encoding="utf-8")
        os.replace(tmp_file, _ENV_FILE)
    except Exception:
        if original is not None:
            _ENV_FILE.write_text(original, encoding="utf-8")  # 롤백
        tmp_file.unlink(missing_ok=True)
        raise


async def _log_settings_update(
    request: Request,
    admin_id: Optional[str],
    changes: dict[str, dict[str, Optional[str]]],
    sensitive_keys: list[str],
    reset_keys: list[str],
) -> bool:
    """설정 변경을 감사 로그에 기록한다.

    비민감 키는 이전값→새값 쌍을, 민감/시크릿 키는 **키 이름만** 남긴다.

    Returns:
        기록 성공 여부(둘 다 미구성이면 False — 호출부가 응답에 명시한다)
    """
    from src.domain.audit import AuditEvent, AuditLogEntry

    extra = {
        "changes": changes,
        "sensitive_keys": sensitive_keys,
        "reset_keys": reset_keys,
    }
    entry = AuditLogEntry(
        event=AuditEvent.SETTINGS_UPDATE.value,
        user_id=admin_id,
        client_ip=getattr(request.state, "client_ip", None),
        request_id=getattr(request.state, "request_id", None),
        success=True,
        extra=extra,
    )

    audit_service = getattr(request.app.state, "audit_service", None)
    if audit_service:
        try:
            await audit_service.log(entry)
            return True
        except Exception as e:
            logger.error("설정 변경 감사 기록 실패, 폴백: %s", e)

    audit_repo = getattr(request.app.state, "audit_repo", None)
    if audit_repo:
        try:
            await audit_repo.log_event({
                "event_type": AuditEvent.SETTINGS_UPDATE.value,
                "user_id": admin_id,
                "ip_address": getattr(request.state, "client_ip", None),
                "detail": extra,
            })
            return True
        except Exception as e:
            logger.error("설정 변경 감사 기록 실패: %s", e)

    logger.warning("감사 저장소가 없어 설정 변경을 기록하지 못했습니다: %s", sorted(changes))
    return False


def _parse_connection_string(conn_str: str) -> dict[str, str]:
    """연결 문자열을 파싱한다.

    예: postgresql://user:pass@host:5432/dbname

    Args:
        conn_str: 연결 문자열

    Returns:
        파싱된 딕셔너리
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
        result["password"] = match.group(3)
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
        is_sensitive = _is_sensitive_key(key)
        settings_list.append(
            EnvSetting(
                key=key,
                value=_MASK_VALUE if is_sensitive else value,
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
        logger.error("실효값 계산 실패: %s", e)
        warnings.append(f"현재 적용 중인 값을 계산하지 못했습니다: {e}")

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
        수정 결과 (재시작 필요 키·즉시 반영 키 포함)

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
        logger.error("설정 저장 실패: %s", e)
        raise HTTPException(status_code=500, detail=f"설정 저장에 실패했습니다: {str(e)}")

    # 8. load_config 캐시 무효화 (즉시 반영 경로용 — 대부분의 설정은 재시작이 필요하다)
    from src.config import load_config
    load_config.cache_clear()

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
    )

    changed_keys = list(updates) + reset_keys
    requires_restart_keys = [
        key for key in changed_keys
        if (spec := index.get(key)) is None or spec.requires_restart
    ]
    applied_immediately_keys = [key for key in changed_keys if key not in requires_restart_keys]

    logger.info(
        "환경변수 설정 수정: updated=%s reset=%s (by %s)",
        list(updates), reset_keys, _admin.get("sub"),
    )

    message = f"{len(changed_keys)}개 설정이 저장되었습니다."
    if not audit_ok:
        message += " (감사 기록 불가 — 감사 저장소 미구성)"

    return EnvUpdateResponse(
        updated_keys=list(updates),
        reset_keys=reset_keys,
        requires_restart_keys=requires_restart_keys,
        applied_immediately_keys=applied_immediately_keys,
        ignored_keys=ignored_keys,
        message=message,
    )


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
        logger.error(f"DB 설정 저장 실패: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"DB 설정 저장에 실패했습니다: {str(e)}",
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
    conn_str = (
        f"{body.db_type}://{body.username}:{body.password}"
        f"@{body.host}:{body.port}/{body.database}"
    )

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
        logger.error(f"DB 연결 테스트 실패: {e}")
        return DbTestResponse(
            success=False,
            message="DB 연결에 실패했습니다.",
            details=str(e),
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


@router.get(
    "/admin/users",
    response_model=list[UserInfoResponse],
)
async def list_users(
    request: Request,
    _admin: dict = Depends(require_admin_user),
) -> list[UserInfoResponse]:
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
        UserInfoResponse(
            user_id=u.user_id,
            username=u.username,
            role=u.role.value,
            department=u.department,
            allowed_db_ids=u.allowed_db_ids,
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
    if user.status.value == "locked":
        from src.domain.user import UserStatus
        user.status = UserStatus.ACTIVE

    await user_repo.update(user)
    logger.info("관리자가 비밀번호 초기화: %s (by %s)", user_id, _admin.get("sub"))

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

    user.allowed_db_ids = body.allowed_db_ids
    await user_repo.update(user)
    logger.info(
        "관리자가 사용자 권한 수정: %s -> allowed_db_ids=%s (by %s)",
        user_id, body.allowed_db_ids, _admin.get("sub"),
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


@router.get(
    "/admin/audit-logs",
)
async def get_audit_logs(
    request: Request,
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
    limit: int = 100,
    _admin: dict = Depends(require_admin_user),
) -> list[dict]:
    """감사 로그를 조회한다.

    Args:
        request: FastAPI Request
        user_id: 필터: 사용자 ID
        event_type: 필터: 이벤트 타입
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        감사 로그 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    return await audit_repo.query_logs(
        user_id=user_id,
        event_type=event_type,
        limit=min(limit, 1000),
    )


# --- 엔드포인트: 감사 로그 확장 ---


@router.get(
    "/admin/audit/logs",
    response_model=AuditLogPageResponse,
)
async def get_audit_logs_paginated(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
    target_db: Optional[str] = None,
    success: Optional[bool] = None,
    keyword: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
    _admin: dict = Depends(require_admin_user),
) -> AuditLogPageResponse:
    """확장된 감사 로그 조회 (페이지네이션, 필터).

    Args:
        request: FastAPI Request
        start_date: 시작 날짜 (ISO 형식)
        end_date: 종료 날짜 (ISO 형식)
        user_id: 필터: 사용자 ID
        event_type: 필터: 이벤트 타입
        target_db: 필터: 대상 DB
        success: 필터: 성공 여부
        keyword: 키워드 검색
        page: 페이지 번호
        page_size: 페이지 크기
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        페이지네이션된 감사 로그 응답
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return AuditLogPageResponse(
            logs=[], total=0, page=page, page_size=page_size, total_pages=0,
        )

    page_size = min(page_size, 200)  # 최대 200

    try:
        logs, total = await audit_repo.query_logs_paginated(
            start_date=start_date,
            end_date=end_date,
            user_id=user_id,
            event_type=event_type,
            target_db=target_db,
            success=success,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    except AttributeError:
        # query_logs_paginated 미구현 시 기존 메서드 폴백
        logs = await audit_repo.query_logs(
            user_id=user_id, event_type=event_type, limit=page_size,
        )
        total = len(logs)

    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0

    return AuditLogPageResponse(
        logs=logs,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get(
    "/admin/audit/stats",
)
async def get_audit_stats(
    request: Request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    _admin: dict = Depends(require_admin_user),
) -> dict:
    """감사 통계를 반환한다.

    Args:
        request: FastAPI Request
        start_date: 통계 시작 날짜 (ISO 형식)
        end_date: 통계 종료 날짜 (ISO 형식)
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        감사 통계 딕셔너리
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return {"error": "감사 서비스를 사용할 수 없습니다."}

    try:
        return await audit_repo.get_stats(
            start_date=start_date,
            end_date=end_date,
        )
    except AttributeError:
        return {"error": "통계 기능이 지원되지 않습니다."}


@router.get(
    "/admin/audit/users/{user_id}/activity",
)
async def get_user_activity(
    request: Request,
    user_id: str,
    limit: int = 100,
    _admin: dict = Depends(require_admin_user),
) -> list[dict]:
    """특정 사용자의 활동 이력을 반환한다.

    Args:
        request: FastAPI Request
        user_id: 대상 사용자 ID
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        사용자 활동 이력 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    try:
        return await audit_repo.get_user_activity(
            user_id=user_id,
            limit=min(limit, 500),
        )
    except AttributeError:
        return await audit_repo.query_logs(
            user_id=user_id, limit=min(limit, 500),
        )


@router.get(
    "/admin/audit/alerts",
)
async def get_security_alerts(
    request: Request,
    limit: int = 100,
    _admin: dict = Depends(require_admin_user),
) -> list[dict]:
    """보안 경고 목록을 반환한다.

    Args:
        request: FastAPI Request
        limit: 최대 조회 수
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        보안 경고 목록
    """
    audit_repo = getattr(request.app.state, "audit_repo", None)
    if not audit_repo:
        return []

    try:
        return await audit_repo.get_alerts(limit=min(limit, 500))
    except AttributeError:
        return await audit_repo.query_logs(
            event_type="security_alert", limit=min(limit, 500),
        )


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
    return {"deleted": deleted, "retention_days": days}
