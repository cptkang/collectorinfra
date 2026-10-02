"""실행 SQL을 프로젝트 공용 `logs/sql/`에 기록하는 미니 로거 (D-140).

`mcp_server`는 자체 venv·별도 프로세스라 본체 `src.utils.sql_file_logger`를 import할 수
없다(D-139 패키지 경계). 그래서 같은 출력 규약을 지키는 최소 구현을 여기 둔다 —
코드 중복은 경계 유지의 대가로 의도적으로 수용한 것이다.

본체와 **같은 파일**에 append하므로 동시 쓰기가 발생한다. 레코드를 한 번의 `write()`
호출로 기록해 `O_APPEND` 원자성에 기대며, 이 성질은 테스트로 고정한다.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_DIR: Path | None = None
_enabled: bool = False

#: 레코드 출처 표기. 본체가 남긴 줄과 구분해 사후에 프로세스를 특정할 수 있게 한다.
_SOURCE_TAG = "mcp_server"


def _default_project_root() -> Path:
    """리포지토리 루트를 반환한다.

    이 파일은 `<root>/mcp_server/mcp_server/sql_log.py`에 있으므로 두 단계 위가 루트다.
    """
    return Path(__file__).resolve().parents[2]


def _env_enabled() -> bool:
    """`OBS_SQL_LOG_ENABLED` 환경변수를 해석한다 (미설정이면 활성)."""
    raw = os.environ.get("OBS_SQL_LOG_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"false", "0", "no", "off"}


def init(project_root: str | Path | None = None) -> None:
    """SQL 파일 로거를 초기화한다.

    Args:
        project_root: 리포지토리 루트. None이면 이 파일 위치에서 역산한다.

    Note:
        디렉토리 생성 실패는 전파하지 않는다 — 로깅 부재가 서버 기동을 막으면 안 된다.
    """
    global _LOG_DIR, _enabled

    _LOG_DIR = None
    _enabled = False

    if not _env_enabled():
        logger.info("SQL 파일 로거 비활성 (OBS_SQL_LOG_ENABLED=false)")
        return

    root = _default_project_root() if project_root is None else Path(project_root)
    log_dir = root / "logs" / "sql"

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.warning("SQL 파일 로거 초기화 실패(%s): %s", log_dir, e)
        return

    _LOG_DIR = log_dir
    _enabled = True
    logger.info("SQL 파일 로거 초기화: %s", _LOG_DIR)


def is_enabled() -> bool:
    """로거가 활성 상태인지 반환한다."""
    return _enabled


def log_sql(
    sql: str,
    *,
    source: str = "",
    execution_time_ms: float = 0.0,
    row_count: int = 0,
    error: str | None = None,
) -> None:
    """실행된 SQL을 파일에 기록한다.

    Args:
        sql: 실행된 SQL 문자열
        source: 데이터소스 이름 (예: "polestar_b0")
        execution_time_ms: 소요 시간 (ms)
        row_count: 결과 행 수
        error: 실패 사유 (실패 시)
    """
    if not _enabled or _LOG_DIR is None:
        return

    try:
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

        lines = [
            f"-- ========== {timestamp} ==========",
            f"-- 호출: {_SOURCE_TAG}",
        ]
        if source:
            lines.append(f"-- DB: {source}")
        lines.append(f"-- 소요: {execution_time_ms:.1f}ms | 행: {row_count}")
        if error:
            lines.append(f"-- 에러: {error}")
        lines.append(sql.rstrip(";") + ";")
        lines.append("")

        content = "\n".join(lines) + "\n"
        log_file = _LOG_DIR / f"{now.strftime('%Y-%m-%d')}.sql"

        # 단일 write — 동시 append 시 레코드가 섞이지 않게 한다.
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(content)

    except Exception as e:
        # 로깅 실패가 SQL 실행 경로에 영향을 주면 안 된다.
        logger.debug("SQL 파일 로깅 실패: %s", e)
