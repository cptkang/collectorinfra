"""SQL 실행 이력을 파일로 기록하는 모듈.

실행된 SQL을 sqls/act/ 디렉토리에 날짜별 파일로 저장한다.
각 SQL에 호출 위치(파일:라인), 실행 시각, 소요 시간, 결과 행 수를 함께 기록한다.
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

# sqls/act/ 디렉토리 경로 (프로젝트 루트 기준)
_SQL_ACT_DIR: Path | None = None
_enabled: bool = False


def init_sql_file_logger(project_root: str | Path | None = None) -> None:
    """SQL 파일 로거를 초기화한다.

    Args:
        project_root: 프로젝트 루트 디렉토리 경로.
                      None이면 현재 작업 디렉토리 사용.
    """
    global _SQL_ACT_DIR, _enabled

    if project_root is None:
        project_root = Path.cwd()
    else:
        project_root = Path(project_root)

    _SQL_ACT_DIR = project_root / "sqls" / "act"
    _SQL_ACT_DIR.mkdir(parents=True, exist_ok=True)
    _enabled = True
    logger.info("SQL 파일 로거 초기화: %s", _SQL_ACT_DIR)


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
    if not _enabled or _SQL_ACT_DIR is None:
        return

    try:
        # 호출 위치 추출
        frame = inspect.stack()[caller_depth]
        caller_file = frame.filename
        caller_line = frame.lineno
        caller_func = frame.function

        # 프로젝트 내 상대 경로로 변환
        try:
            caller_file = str(Path(caller_file).relative_to(Path.cwd()))
        except ValueError:
            pass

        # 날짜별 파일명
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = _SQL_ACT_DIR / f"{today}.sql"

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
