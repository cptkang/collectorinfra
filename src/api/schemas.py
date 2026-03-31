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
