"""운영자 설정 관련 라우트.

환경변수 설정 조회/수정, DB 연결 설정 조회/수정/테스트 엔드포인트를 제공한다.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.api.routes.admin_auth import require_admin

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
