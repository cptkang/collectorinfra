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

from src.api.routes.admin_auth import require_admin
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
        ..., description="수정할 설정값 (키: 값)"
    )


class EnvUpdateResponse(BaseModel):
    """환경변수 설정 수정 응답."""

    updated_keys: list[str]
    message: str


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
    _username: str = Depends(require_admin),
) -> EnvSettingsResponse:
    """환경변수 설정 목록을 조회한다.

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


@router.put(
    "/admin/settings",
    response_model=EnvUpdateResponse,
)
async def update_settings(
    body: EnvUpdateRequest,
    _username: str = Depends(require_admin),
) -> EnvUpdateResponse:
    """환경변수 설정을 수정한다.

    수정된 값은 .env 파일에 저장된다.

    Args:
        body: 수정할 설정값
        _username: 인증된 관리자 (의존성 주입)

    Returns:
        수정 결과

    Raises:
        HTTPException: 설정 저장 실패 시
    """
    if not body.settings:
        raise HTTPException(status_code=400, detail="수정할 설정이 없습니다.")

    try:
        _write_env_file(body.settings)
        # load_config 캐시 무효화
        from src.config import load_config
        load_config.cache_clear()

        logger.info(f"환경변수 설정 수정: {list(body.settings.keys())}")

        return EnvUpdateResponse(
            updated_keys=list(body.settings.keys()),
            message=f"{len(body.settings)}개 설정이 업데이트되었습니다.",
        )
    except Exception as e:
        logger.error(f"설정 저장 실패: {e}")
        raise HTTPException(status_code=500, detail=f"설정 저장에 실패했습니다: {str(e)}")


# --- 엔드포인트: DB 연결 설정 ---


@router.get(
    "/admin/db-config",
    response_model=DbConfigResponse,
)
async def get_db_config(
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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


@router.get(
    "/admin/users",
    response_model=list[UserInfoResponse],
)
async def list_users(
    request: Request,
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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

    from src.domain.user import UserRole, UserStatus

    user = await user_repo.get_by_user_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    if body.username is not None:
        user.username = body.username
    if body.role is not None:
        user.role = UserRole(body.role)
    if body.department is not None:
        user.department = body.department
    if body.status is not None:
        user.status = UserStatus(body.status)
        if body.status == "active":
            user.login_fail_count = 0

    await user_repo.update(user)
    logger.info("관리자가 사용자 수정: %s (by %s)", user_id, _username)

    return UserInfoResponse(
        user_id=user.user_id,
        username=user.username,
        role=user.role.value,
        department=user.department,
        allowed_db_ids=user.allowed_db_ids,
        status=user.status.value,
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


@router.delete(
    "/admin/users/{user_id}",
)
async def delete_user(
    request: Request,
    user_id: str,
    _username: str = Depends(require_admin),
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

    if not await user_repo.exists(user_id):
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")

    await user_repo.delete(user_id)
    logger.info("관리자가 사용자 삭제: %s (by %s)", user_id, _username)

    return {"message": f"사용자 '{user_id}'가 삭제되었습니다."}


@router.post(
    "/admin/users/{user_id}/reset-password",
)
async def reset_user_password(
    request: Request,
    user_id: str,
    _username: str = Depends(require_admin),
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

    import secrets

    from src.utils.password import hash_password

    temp_password = secrets.token_urlsafe(12)
    user.hashed_password = hash_password(temp_password)
    user.login_fail_count = 0
    if user.status.value == "locked":
        from src.domain.user import UserStatus
        user.status = UserStatus.ACTIVE

    await user_repo.update(user)
    logger.info("관리자가 비밀번호 초기화: %s (by %s)", user_id, _username)

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
    _username: str = Depends(require_admin),
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
        user_id, body.allowed_db_ids, _username,
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
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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
    _username: str = Depends(require_admin),
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
