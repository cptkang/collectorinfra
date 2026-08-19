"""SQL 실행 이력을 파일로 기록하는 모듈.

실행된 SQL을 logs/sql/ 디렉토리에 날짜별 파일로 저장한다(D-140 — 종전 sqls/act/에서 이전).
각 SQL에 호출 위치(파일:라인), 실행 시각, 소요 시간, 결과 행 수를 함께 기록한다.

로그 산출물은 감사(logs/audit-*.jsonl)·트레이스(logs/trace/)와 함께 `logs/` 한 루트에 모인다.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# logs/sql/ 디렉토리 경로 (프로젝트 루트 기준)
_SQL_LOG_DIR: Path | None = None
_enabled: bool = False


def init_sql_file_logger(
    project_root: str | Path | None = None,
    *,
    enabled: bool = True,
) -> None:
    """SQL 파일 로거를 초기화한다.

    Args:
        project_root: 프로젝트 루트 디렉토리 경로.
                      None이면 현재 작업 디렉토리 사용.
        enabled: False면 로거를 비활성 상태로 둔다 (OBS_SQL_LOG_ENABLED).

    Note:
        디렉토리 생성 실패는 예외로 전파하지 않는다 — 로깅 부재가 앱 기동을 막으면 안 된다.
    """
    global _SQL_LOG_DIR, _enabled

    _SQL_LOG_DIR = None
    _enabled = False

    if not enabled:
        logger.info("SQL 파일 로거 비활성 (OBS_SQL_LOG_ENABLED=false)")
        return

    root = Path.cwd() if project_root is None else Path(project_root)
    log_dir = root / "logs" / "sql"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        # 침묵 금지 — 사유를 WARNING으로 남기고 비활성 상태를 유지한다.
        logger.warning("SQL 파일 로거 초기화 실패(%s): %s", log_dir, e)
        return

    _SQL_LOG_DIR = log_dir
    _enabled = True
    logger.info("SQL 파일 로거 초기화: %s", _SQL_LOG_DIR)


def is_enabled() -> bool:
    """SQL 파일 로거가 활성화되었는지 반환한다."""
    return _enabled


def log_sql(
    sql: str,
    *,
    execution_time_ms: float = 0.0,
    row_count: int = 0,
    source: str = "",
    error: str | None = None,
    caller_depth: int = 2,
) -> None:
    """실행된 SQL을 파일에 기록한다.

    Args:
        sql: 실행된 SQL 문자열
        execution_time_ms: 실행 소요 시간 (ms)
        row_count: 결과 행 수
        source: DB 소스명 (예: "polestar", "infra_db")
        error: 에러 메시지 (실패 시)
        caller_depth: 호출 스택에서 실제 호출자까지의 깊이 (기본 2)
    """
    if not _enabled or _SQL_LOG_DIR is None:
        return

    try:
        # 호출 위치 추출. 스택이 얕으면(모듈 레벨 호출·REPL 등) 가장 바깥 프레임으로 물러난다 —
        # 종전에는 IndexError가 나서 **SQL 기록 자체가 통째로 유실**됐다(2026-08-19 스모크 실측).
        stack = inspect.stack()
        frame = stack[min(caller_depth, len(stack) - 1)] if stack else None
        caller_file = frame.filename if frame else "unknown"
        caller_line = frame.lineno if frame else 0
        caller_func = frame.function if frame else "unknown"

        # 프로젝트 내 상대 경로로 변환
        try:
            caller_file = str(Path(caller_file).relative_to(Path.cwd()))
        except ValueError:
            pass

        # 날짜별 파일명
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = _SQL_LOG_DIR / f"{today}.sql"

        # 타임스탬프
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        # 기록 내용 구성
        lines = [
            f"-- ========== {timestamp} ==========",
            f"-- 호출: {caller_file}:{caller_line} ({caller_func})",
        ]
        if source:
            lines.append(f"-- DB: {source}")
        lines.append(f"-- 소요: {execution_time_ms:.1f}ms | 행: {row_count}")
        if error:
            lines.append(f"-- 에러: {error}")
        lines.append(sql.rstrip(";") + ";")
        lines.append("")  # 빈 줄 구분

        content = "\n".join(lines) + "\n"

        # 파일에 추가 (append 모드)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(content)

    except Exception as e:
        # SQL 로깅 실패가 메인 로직에 영향을 주면 안 됨
        logger.debug("SQL 파일 로깅 실패: %s", e)
