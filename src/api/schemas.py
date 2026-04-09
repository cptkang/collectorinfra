"""FastAPI 요청/응답 Pydantic 모델.

API 엔드포인트의 입출력 데이터 구조를 정의한다.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OutputFormat(str, Enum):
    """출력 형식."""

    TEXT = "text"
    XLSX = "xlsx"
    DOCX = "docx"


# --- 요청 모델 ---


class QueryRequest(BaseModel):
    """자연어 질의 요청. POST /api/v1/query"""

    query: str = Field(
        ..., min_length=1, max_length=2000, description="자연어 질의"
    )
    output_format: OutputFormat = Field(
        default=OutputFormat.TEXT,
        description="출력 형식",
    )
    thread_id: Optional[str] = Field(
        default=None,
        description="세션 ID (멀티턴 대화용, Phase 3)",
    )


# --- 응답 모델 ---


class QueryResponse(BaseModel):
    """질의 응답."""

    query_id: str = Field(..., description="쿼리 고유 ID")
    status: str = Field(
        ...,
        description="처리 상태: completed | awaiting_approval | error",
    )
    response: str = Field(..., description="자연어 응답 텍스트")
    thread_id: Optional[str] = Field(
        default=None, description="세션 ID (멀티턴 대화용)"
    )
    awaiting_approval: bool = Field(
        default=False, description="사용자 승인 대기 여부"
    )
    approval_context: Optional[dict] = Field(
        default=None, description="승인 요청 컨텍스트 (SQL 등)"
    )
    has_file: bool = Field(default=False, description="생성된 파일 존재 여부")
    file_name: Optional[str] = Field(default=None, description="생성된 파일명")
    executed_sql: Optional[str] = Field(default=None, description="실행된 SQL")
    row_count: Optional[int] = Field(default=None, description="결과 행 수")
    processing_time_ms: Optional[float] = Field(
        default=None, description="처리 시간 (ms)"
    )
    turn_count: Optional[int] = Field(
        default=None, description="현재 대화 턴 수"
    )
    has_mapping_report: bool = Field(
        default=False, description="매핑 보고서 존재 여부"
    )


class HealthResponse(BaseModel):
    """헬스체크 응답."""

    status: str = Field(..., description="서비스 상태: healthy | unhealthy")
    version: str = Field(..., description="버전")
    db_connected: bool = Field(..., description="DB 연결 상태")
    timestamp: datetime = Field(default_factory=datetime.now)


class ErrorResponse(BaseModel):
    """에러 응답."""

    error: str = Field(..., description="에러 메시지")
    detail: Optional[str] = Field(default=None, description="상세 설명")
    query_id: Optional[str] = Field(default=None)


# --- 사용자 인증 관련 모델 ---


class UserRegisterRequest(BaseModel):
    """사용자 가입 요청. 승인 없이 즉시 가입."""

    user_id: str = Field(
        ..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_]+$",
        description="사용자 ID (영문, 숫자, _ 조합)",
    )
    username: str = Field(
        ..., min_length=1, max_length=100, description="표시 이름"
    )
    password: str = Field(..., min_length=8, description="비밀번호 (최소 8자)")
    department: Optional[str] = Field(None, max_length=100, description="부서")


class UserLoginRequest(BaseModel):
    """사용자 로그인 요청."""

    user_id: str = Field(..., min_length=1, description="사용자 ID")
    password: str = Field(..., min_length=1, description="비밀번호")


class UserInfoResponse(BaseModel):
    """사용자 정보 응답."""

    user_id: str
    username: str
    role: str
    department: Optional[str] = None
    allowed_db_ids: Optional[list[str]] = None
    status: str = "active"
    last_login_at: Optional[str] = None


class UserLoginResponse(BaseModel):
    """사용자 로그인 응답."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserInfoResponse


class ChangePasswordRequest(BaseModel):
    """비밀번호 변경 요청. 현재 비밀번호 확인 필수."""

    current_password: str = Field(..., min_length=1, description="현재 비밀번호")
    new_password: str = Field(..., min_length=8, description="새 비밀번호 (최소 8자)")


class UpdateUserRequest(BaseModel):
    """관리자용 사용자 수정. 변경할 필드만 포함."""

    username: Optional[str] = Field(None, min_length=1, max_length=100)
    role: Optional[str] = Field(None, pattern=r"^(user|admin)$")
    department: Optional[str] = None
    status: Optional[str] = Field(None, pattern=r"^(active|inactive|locked)$")


class UpdatePermissionsRequest(BaseModel):
    """관리자용 DB 접근 권한 수정."""

    allowed_db_ids: Optional[list[str]] = Field(
        None, description="접근 허용 DB 목록 (null=전체 허용 불가)"
    )


class AuthStatusResponse(BaseModel):
    """인증 상태 응답 (클라이언트에서 AUTH_ENABLED 확인용)."""

    auth_enabled: bool
    user: Optional[UserInfoResponse] = None
