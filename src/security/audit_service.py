"""통합 감사 로그 서비스.

JSONL 파일과 PostgreSQL DB에 이중 기록한다.
모든 감사 이벤트는 이 서비스를 통해 기록한다.
"""

from __future__ import annotations

import logging
from typing import Optional

from src.config import AuditConfig
from src.domain.audit import AlertSeverity, AuditEvent, AuditLogEntry
from src.domain.user import AuditRepository
from src.security.audit_logger import AuditEntry, _write_audit_file

logger = logging.getLogger(__name__)


class AuditService:
    """통합 감사 로그 서비스."""

    def __init__(
        self,
        config: AuditConfig,
        audit_repo: Optional[AuditRepository] = None,
    ) -> None:
        """서비스를 초기화한다.

        Args:
            config: 감사 로그 설정
            audit_repo: DB 감사 저장소 (None이면 DB 기록 비활성화)
        """
        self._config = config
        self._audit_repo = audit_repo

    async def log(self, entry: AuditLogEntry) -> None:
        """감사 이벤트를 기록한다 (JSONL + DB).

        Args:
            entry: 감사 로그 엔트리
        """
        # JSONL 파일 기록
        if self._config.jsonl_enabled:
            try:
                file_entry = AuditEntry(**entry.to_dict())
                await _write_audit_file(file_entry)
            except Exception as e:
                logger.error("JSONL 감사 로그 기록 실패: %s", e)

        # DB 기록
        if self._config.db_enabled and self._audit_repo:
            try:
                # 기존 audit_logs 테이블 형식으로 변환
                detail = entry.to_dict()
                event_type = detail.pop("event", "unknown")
                user_id = detail.pop("user_id", None)
                ip_address = detail.pop("client_ip", None)
                detail.pop("timestamp", None)  # DB에서 자동 생성

                await self._audit_repo.log_event({
                    "event_type": event_type,
                    "user_id": user_id,
                    "detail": detail,
                    "ip_address": ip_address,
                })
            except Exception as e:
                logger.error("DB 감사 로그 기록 실패: %s", e)

    async def log_login(
        self,
        user_id: str,
        success: bool,
        client_ip: str,
        username: Optional[str] = None,
        error: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """로그인 이벤트를 기록한다.

        Args:
            user_id: 사용자 ID
            success: 로그인 성공 여부
            client_ip: 클라이언트 IP
            username: 사용자 표시 이름
            error: 에러 메시지 (실패 시)
            request_id: 요청 추적 ID
        """
        event = AuditEvent.USER_LOGIN if success else AuditEvent.LOGIN_FAIL
        entry = AuditLogEntry(
            event=event.value,
            user_id=user_id,
            username=username,
            client_ip=client_ip,
            request_id=request_id,
            success=success,
            error=error,
        )
        await self.log(entry)

        # 로그인 실패 반복 보안 경고
        if not success:
            await self._check_login_failure_alert(user_id, client_ip, request_id)

    async def log_logout(
        self,
        user_id: str,
        client_ip: str,
        request_id: Optional[str] = None,
    ) -> None:
        """로그아웃 이벤트를 기록한다.

        Args:
            user_id: 사용자 ID
            client_ip: 클라이언트 IP
            request_id: 요청 추적 ID
        """
        entry = AuditLogEntry(
            event=AuditEvent.USER_LOGOUT.value,
            user_id=user_id,
            client_ip=client_ip,
            request_id=request_id,
        )
        await self.log(entry)

    async def log_user_request(
        self,
        user_id: Optional[str],
        user_query: str,
        output_format: str,
        has_file: bool,
        client_ip: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """사용자 질의 요청을 기록한다.

        Args:
            user_id: 사용자 ID
            user_query: 자연어 질의
            output_format: 요청 출력 형식
            has_file: 파일 업로드 여부
            client_ip: 클라이언트 IP
            session_id: 세션 ID
            request_id: 요청 추적 ID
        """
        entry = AuditLogEntry(
            event=AuditEvent.USER_REQUEST.value,
            user_id=user_id,
            client_ip=client_ip,
            session_id=session_id,
            request_id=request_id,
            user_query=user_query,
            extra={"output_format": output_format, "has_file": has_file},
        )
        await self.log(entry)

    async def log_query_execution(
        self,
        sql: str,
        row_count: int,
        execution_time_ms: float,
        success: bool,
        error: Optional[str] = None,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        target_db: Optional[str] = None,
        target_tables: Optional[list[str]] = None,
        retry_attempt: int = 0,
        masked_columns: Optional[list[str]] = None,
    ) -> None:
        """쿼리 실행을 기록한다.

        Args:
            sql: 실행된 SQL
            row_count: 결과 행 수
            execution_time_ms: 실행 시간 (ms)
            success: 성공 여부
            error: 에러 메시지 (실패 시)
            user_id: 사용자 ID
            session_id: 세션 ID
            request_id: 요청 추적 ID
            target_db: 대상 DB
            target_tables: 접근한 테이블 목록
            retry_attempt: 재시도 횟수
            masked_columns: 마스킹된 컬럼 목록
        """
        entry = AuditLogEntry(
            event=AuditEvent.QUERY_EXECUTION.value,
            user_id=user_id,
            session_id=session_id,
            request_id=request_id,
            generated_sql=sql,
            row_count=row_count,
            execution_time_ms=round(execution_time_ms, 2),
            success=success,
            error=error,
            target_db=target_db,
            target_tables=target_tables,
            masked_columns=masked_columns,
            extra={"retry_attempt": retry_attempt} if retry_attempt else None,
        )
        await self.log(entry)

        # 대량 데이터 경고
        if success and row_count > self._config.alert_on_large_result:
            await self.log_security_alert(
                event_detail=f"대량 데이터 조회: {row_count}건",
                user_id=user_id,
                client_ip=None,
                request_id=request_id,
                severity=AlertSeverity.INFO.value,
            )

    async def log_data_access(
        self,
        user_id: Optional[str],
        tables: list[str],
        db: Optional[str],
        row_count: int,
        client_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """데이터 접근 요약을 기록한다.

        Args:
            user_id: 사용자 ID
            tables: 접근한 테이블 목록
            db: 대상 DB
            row_count: 결과 행 수
            client_ip: 클라이언트 IP
            request_id: 요청 추적 ID
        """
        entry = AuditLogEntry(
            event=AuditEvent.DATA_ACCESS.value,
            user_id=user_id,
            client_ip=client_ip,
            request_id=request_id,
            target_tables=tables,
            target_db=db,
            row_count=row_count,
        )
        await self.log(entry)

    async def log_file_download(
        self,
        user_id: Optional[str],
        file_name: str,
        file_type: str,
        file_size: int,
        client_ip: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> None:
        """파일 다운로드를 기록한다.

        Args:
            user_id: 사용자 ID
            file_name: 파일명
            file_type: 파일 유형
            file_size: 파일 크기 (바이트)
            client_ip: 클라이언트 IP
            request_id: 요청 추적 ID
        """
        entry = AuditLogEntry(
            event=AuditEvent.FILE_DOWNLOAD.value,
            user_id=user_id,
            client_ip=client_ip,
            request_id=request_id,
            file_name=file_name,
            file_type=file_type,
            file_size_bytes=file_size,
        )
        await self.log(entry)

    async def log_security_alert(
        self,
        event_detail: str,
        user_id: Optional[str],
        client_ip: Optional[str],
        request_id: Optional[str] = None,
        severity: str = "warning",
    ) -> None:
        """보안 경고를 기록한다.

        Args:
            event_detail: 경고 상세 내용
            user_id: 사용자 ID
            client_ip: 클라이언트 IP
            request_id: 요청 추적 ID
            severity: 경고 심각도 (info, warning, critical)
        """
        entry = AuditLogEntry(
            event=AuditEvent.SECURITY_ALERT.value,
            user_id=user_id,
            client_ip=client_ip,
            request_id=request_id,
            severity=severity,
            security_flags=[event_detail],
            extra={"severity": severity, "detail": event_detail},
        )
        await self.log(entry)

    async def _check_login_failure_alert(
        self,
        user_id: str,
        client_ip: str,
        request_id: Optional[str] = None,
    ) -> None:
        """로그인 실패 반복 시 보안 경고를 발생시킨다.

        Args:
            user_id: 사용자 ID
            client_ip: 클라이언트 IP
            request_id: 요청 추적 ID
        """
        if not self._audit_repo:
            return
        try:
            recent = await self._audit_repo.query_logs(
                user_id=user_id,
                event_type="login_fail",
                limit=self._config.alert_on_failed_login,
            )
            if len(recent) >= self._config.alert_on_failed_login:
                await self.log_security_alert(
                    event_detail=f"로그인 {len(recent)}회 연속 실패: {user_id}",
                    user_id=user_id,
                    client_ip=client_ip,
                    request_id=request_id,
                    severity=AlertSeverity.CRITICAL.value,
                )
        except Exception as e:
            logger.error("로그인 실패 경고 확인 중 오류: %s", e)
