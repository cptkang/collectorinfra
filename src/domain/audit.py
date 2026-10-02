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
    SETTINGS_UPDATE = "settings_update"
    SETTINGS_RELOAD = "settings_reload"

    # 보안
    SECURITY_ALERT = "security_alert"


# 옛 기록 이름. 2026-09-27 이전에는 로그인·로그아웃 라우트가 `login`·`logout`으로 기록했다.
# DB 행은 고치지 않고 조회가 두 이름을 함께 찾는다. 옛 행은 보관 기간(`AUDIT_RETENTION_DAYS`)이
# 지나면 자동 정리되므로, 그 뒤에는 이 표를 지워도 된다.
LEGACY_EVENT_ALIASES: dict[str, tuple[str, ...]] = {
    AuditEvent.USER_LOGIN.value: ("login",),
    AuditEvent.USER_LOGOUT.value: ("logout",),
}


def event_type_filter_values(event_type: str) -> list[str]:
    """이벤트 필터 값에 해당하는 저장 이름 목록(현행 이름 + 옛 이름)을 돌려준다."""
    return [event_type, *LEGACY_EVENT_ALIASES.get(event_type, ())]


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
