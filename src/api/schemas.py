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
    # Plan 65 §4: 존 역질문(clarification)에서 사용자가 체크박스로 선택한 DB 목록.
    # 자연어 재조합 금지 원칙 — 선택 결과는 이 구조화 필드로만 전달되어
    # semantic_router/intent_planner의 결정적 고정(mapped_db_ids 선례)으로 주입된다.
    selected_db_ids: Optional[list[str]] = Field(
        default=None,
        description="존 선택 역질문 응답 — 조회 대상 DB 식별자 목록 (결정적 라우팅 고정)",
    )
    # Plan 68 §11 (D-118): 폼필 역질문 패널의 구조화 답변 — 자연어 재조합·LLM 파싱 없이
    # 이 필드로만 전달되어 결정적 검증(존재성)·적용을 거친다.
    form_fill_answers: Optional[dict[str, dict]] = Field(
        default=None,
        description=(
            "폼필 역질문 답변 {필드명: {action: blank|column|eav|literal, value}} "
            "(pending_form_fill 대기 중인 thread에서만 유효)"
        ),
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
    # Plan 65 §4: status="clarification"일 때 존 선택 컨텍스트
    # {kind, question, options: [{db_id, label}], original_query, multi}
    clarification: Optional[dict] = Field(
        default=None, description="역질문 컨텍스트 (존 선택 등)"
    )
    # Plan 68 §11 (D-118): 폼필 미해결 필드 역질문 — 결과와 함께 첨부(사후 패널).
    # {question, fields: [{name, label}], candidates: [{value, label, kind}]}
    form_fill_clarification: Optional[dict] = Field(
        default=None, description="폼필 미해결 필드 역질문 패널 컨텍스트"
    )


class HealthResponse(BaseModel):
    """헬스체크 응답."""

    status: str = Field(..., description="서비스 상태: healthy | unhealthy")
    version: str = Field(..., description="버전")
    db_connected: bool = Field(..., description="DB 연결 상태")
    db_status_map: dict[str, bool] = Field(
        default_factory=dict, description="DB ID별 연결 상태 (멀티 DB 모드)"
    )
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
    alarm_zones: Optional[list[str]] = None
    is_protected: bool = False
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
    # Plan 59 §17: 알림 지역 스코프(중복 할당 가능). 예: ["gongjon","bankjon"]. []=수신 안 함.
    alarm_zones: Optional[list[str]] = Field(
        None, description="알림 수신 존 목록(gongjon/bankjon). 빈 목록=수신 안 함"
    )


class UpdatePermissionsRequest(BaseModel):
    """관리자용 DB 접근 권한 수정."""

    allowed_db_ids: Optional[list[str]] = Field(
        None, description="접근 허용 DB 목록 (null=전체 허용 불가)"
    )


class AuthStatusResponse(BaseModel):
    """인증 상태 응답 (클라이언트에서 AUTH_ENABLED 확인용)."""

    auth_enabled: bool
    user: Optional[UserInfoResponse] = None
