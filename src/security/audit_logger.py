"""감사 로그 모듈.

모든 쿼리 실행 이력을 기록한다. Phase 1에서는 파일 기반,
Phase 3에서는 DB 기반 저장소로 확장한다.
날짜별 로그 파일 로테이션을 지원한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import structlog

logger = structlog.get_logger("audit")

# Phase 1: 파일 기반 감사 로그 (날짜별 분리)
AUDIT_LOG_DIR = Path("logs")
MAX_LOG_SIZE_MB = 100


class AuditEntry:
    """감사 로그 엔트리."""

    def __init__(self, **kwargs: Any) -> None:
        """엔트리를 생성한다.

        Args:
            **kwargs: 로그 필드 (timestamp, event, sql 등)
        """
        self._data = kwargs

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환한다.

        Returns:
            None이 아닌 필드만 포함하는 딕셔너리
        """
        return {k: v for k, v in self._data.items() if v is not None}

    def to_json(self) -> str:
        """JSON 문자열로 변환한다.

        Returns:
            JSON 문자열
        """
        return json.dumps(self.to_dict(), ensure_ascii=False)


def _get_audit_log_path() -> Path:
    """날짜 기반 감사 로그 파일 경로를 반환한다.

    Returns:
        오늘 날짜의 감사 로그 파일 경로
    """
    today = datetime.now().strftime("%Y-%m-%d")
    return AUDIT_LOG_DIR / f"audit-{today}.jsonl"


async def log_query_execution(
    sql: str,
    row_count: int,
    execution_time_ms: float,
    success: bool,
    error: Optional[str] = None,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
    validation_warnings: Optional[list[str]] = None,
    retry_attempt: int = 0,
    source_name: Optional[str] = None,
    masked_columns: Optional[list[str]] = None,
) -> None:
    """쿼리 실행을 감사 로그에 기록한다.

    Args:
        sql: 실행된 SQL
        row_count: 결과 행 수
        execution_time_ms: 실행 시간 (ms)
        success: 성공 여부
        error: 에러 메시지 (실패 시)
        user_id: 사용자 ID (Phase 3)
        thread_id: 세션 ID
        validation_warnings: SQL 검증 경고 목록
        retry_attempt: 재시도 횟수
        source_name: DB 소스명
        masked_columns: 마스킹된 컬럼 목록
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="query_execution",
        sql=sql,
        row_count=row_count,
        execution_time_ms=round(execution_time_ms, 2),
        success=success,
        error=error,
        user_id=user_id,
        thread_id=thread_id,
        validation_warnings=validation_warnings,
        retry_attempt=retry_attempt,
        source_name=source_name,
        masked_columns=masked_columns,
    )

    # 구조화된 로깅 (event 키 충돌 방지)
    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("query_executed", **log_data)

    # 파일에 기록 (Phase 1)
    await _write_audit_file(entry)


async def log_user_request(
    user_query: str,
    output_format: str,
    has_file: bool,
    user_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> None:
    """사용자 요청을 감사 로그에 기록한다.

    Args:
        user_query: 사용자 질의
        output_format: 요청 출력 형식
        has_file: 파일 업로드 여부
        user_id: 사용자 ID
        thread_id: 세션 ID
    """
    entry = AuditEntry(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event="user_request",
        user_query=user_query,
        output_format=output_format,
        has_file=has_file,
        user_id=user_id,
        thread_id=thread_id,
    )

    log_data = {k: v for k, v in entry.to_dict().items() if k != "event"}
    logger.info("user_request", **log_data)
    await _write_audit_file(entry)


async def _write_audit_file(entry: AuditEntry) -> None:
    """감사 로그를 날짜별 JSONL 파일에 추가한다.

    파일 크기가 MAX_LOG_SIZE_MB를 초과하면 순번을 붙여 로테이션한다.
    동기 파일 I/O를 asyncio.to_thread()로 감싸 이벤트 루프 블로킹을 방지한다.

    Args:
        entry: 감사 로그 엔트리
    """
    try:
        await asyncio.to_thread(_write_audit_file_sync, entry)
    except Exception as e:
        logging.getLogger(__name__).error(f"감사 로그 파일 쓰기 실패: {e}")


def _write_audit_file_sync(entry: AuditEntry) -> None:
    """감사 로그를 동기적으로 파일에 기록한다 (스레드에서 실행).

    Args:
        entry: 감사 로그 엔트리
    """
    log_path = _get_audit_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 파일 크기 체크 및 로테이션
    if log_path.exists() and log_path.stat().st_size > MAX_LOG_SIZE_MB * 1024 * 1024:
        counter = 1
        while True:
            rotated = log_path.with_suffix(f".{counter}.jsonl")
            if not rotated.exists():
                log_path.rename(rotated)
                break
            counter += 1

    with log_path.open("a", encoding="utf-8") as f:
        f.write(entry.to_json() + "\n")


def setup_logging(log_level: str = "INFO") -> None:
    """structlog 기반 구조화된 로깅을 설정한다.

    표준 logging 루트 로거도 함께 설정하여
    logging.getLogger(__name__) 로그도 출력되도록 한다.

    Args:
        log_level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # 표준 logging 설정
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )

    # structlog 설정
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
