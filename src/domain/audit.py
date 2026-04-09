"""감사 로그 도메인 모델.

감사 이벤트 유형, 로그 엔트리, 조회 필터, 통계 응답 모델을 정의한다.
Clean Architecture: domain 계층에 위치하여 infrastructure/interface에서 참조 가능.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class AuditEvent(str, Enum):
    """감사 이벤트 유형."""

    # 인증
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    LOGIN_FAIL = "login_fail"
    REGISTER = "register"
    PASSWORD_CHANGE = "password_change"

    # 질의
    USER_REQUEST = "user_request"
    QUERY_EXECUTION = "query_execution"
    DATA_ACCESS = "data_access"

    # 파일
    FILE_UPLOAD = "file_upload"
    FILE_DOWNLOAD = "file_download"

    # 관리
    ADMIN_ACTION = "admin_action"
    CACHE_OPERATION = "cache_operation"

    # 보안
    SECURITY_ALERT = "security_alert"


class AlertSeverity(str, Enum):
    """보안 경고 심각도."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AuditLogEntry:
    """확장된 감사 로그 엔트리."""

    # 공통 필드
    event: str
    timestamp: str = ""
    user_id: Optional[str] = None
    username: Optional[str] = None
    department: Optional[str] = None
    client_ip: Optional[str] = None
    session_id: Optional[str] = None
    request_id: Optional[str] = None

    # 질의 관련
    user_query: Optional[str] = None
    generated_sql: Optional[str] = None
    target_tables: Optional[list[str]] = None
    target_db: Optional[str] = None
    row_count: Optional[int] = None
    execution_time_ms: Optional[float] = None
    success: Optional[bool] = None
    error: Optional[str] = None

    # 파일 관련
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    file_size_bytes: Optional[int] = None

    # 보안 관련
    masked_columns: Optional[list[str]] = None
    security_flags: Optional[list[str]] = None
    severity: Optional[str] = None

    # 메타데이터
    extra: Optional[dict[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """None이 아닌 필드만 포함하는 딕셔너리로 변환한다."""
        return {k: v for k, v in asdict(self).items() if v is not None}
