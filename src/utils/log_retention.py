"""날짜 기반 로그 산출물의 보존 정리 (D-140/D-141).

`logs/sql/YYYY-MM-DD.sql`과 `logs/trace/YYYY-MM-DD/`를 보존 기간 기준으로 삭제한다.
감사 로그(`logs/audit-*.jsonl`)·알람 판정(`logs/alarm_decisions.jsonl`)은 **대상이 아니다**
— 그쪽은 `audit_repo.cleanup_old_logs()`가 별도 정책으로 관리한다.

이름이 날짜로 해석되지 않는 파일·디렉토리는 건드리지 않는다(오삭제 방지).
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

#: `YYYY-MM-DD` 정확 일치. 접두·접미가 붙은 이름은 대상에서 제외해 오삭제를 막는다.
_DATE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _as_days(value: object) -> int | None:
    """보존 일수를 정수로 해석한다. 해석 불가면 None.

    `int(value)`를 바로 쓰지 않는다 — `MagicMock`처럼 `__int__`를 구현한 임의 객체가
    조용히 1로 변환되어 **의도치 않은 삭제**를 일으킬 수 있다(2026-08-19 실측).
    허용 타입은 `int`(bool 제외)와 정수 문자열(env 유래)뿐이다.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _cutoff(retention_days: object) -> date | None:
    """삭제 기준 **날짜**를 반환한다. 보존 일수가 0 이하면 None(정리 비활성).

    로그 파일명이 날짜 단위(`YYYY-MM-DD`)라 비교도 날짜 단위로 한다 — 시각까지 쓰면
    "정확히 N일 경과분"이 실행 시각에 따라 삭제되기도 남기도 한다.
    이 날짜 **미만**이 삭제 대상이므로 정확히 N일 경과분은 보존된다.

    정수로 해석되지 않는 값(설정 오류·테스트 대역 등)은 **정리 비활성**으로 처리하고
    사유를 남긴다. 삭제는 되돌릴 수 없으므로 불확실하면 지우지 않는 쪽이 안전하다.
    """
    days = _as_days(retention_days)
    if days is None:
        logger.warning(
            "보존 일수를 해석할 수 없어 로그 정리를 건너뜁니다: %r", retention_days
        )
        return None

    if days <= 0:
        return None
    return (datetime.now() - timedelta(days=days)).date()


def _parse_date(name: str) -> date | None:
    """`YYYY-MM-DD` 이름을 날짜로 해석한다. 형식이 다르면 None."""
    if not _DATE_NAME.match(name):
        return None
    try:
        return datetime.strptime(name, "%Y-%m-%d").date()
    except ValueError:
        return None


def cleanup_old_sql_logs(project_root: str | Path, retention_days: object) -> int:
    """보존 기간이 지난 `logs/sql/*.sql`을 삭제한다.

    Args:
        project_root: 프로젝트 루트 경로
        retention_days: 보존 일수. 0 이하면 정리하지 않는다.

    Returns:
        삭제한 파일 수
    """
    cutoff = _cutoff(retention_days)
    if cutoff is None:
        return 0

    sql_dir = Path(project_root) / "logs" / "sql"
    if not sql_dir.is_dir():
        return 0

    deleted = 0
    for path in sql_dir.glob("*.sql"):
        stamp = _parse_date(path.stem)
        if stamp is None or stamp >= cutoff:
            continue
        try:
            path.unlink()
            deleted += 1
        except Exception as e:
            logger.warning("SQL 로그 삭제 실패(%s): %s", path, e)

    if deleted:
        logger.info("SQL 로그 정리: %s일 경과 %s개 삭제", retention_days, deleted)
    return deleted


def cleanup_old_traces(project_root: str | Path, retention_days: object) -> int:
    """보존 기간이 지난 `logs/trace/YYYY-MM-DD/`를 디렉토리째 삭제한다.

    Args:
        project_root: 프로젝트 루트 경로
        retention_days: 보존 일수. 0 이하면 정리하지 않는다.

    Returns:
        삭제한 디렉토리 수
    """
    cutoff = _cutoff(retention_days)
    if cutoff is None:
        return 0

    trace_dir = Path(project_root) / "logs" / "trace"
    if not trace_dir.is_dir():
        return 0

    deleted = 0
    for path in trace_dir.iterdir():
        if not path.is_dir():
            continue
        stamp = _parse_date(path.name)
        if stamp is None or stamp >= cutoff:
            continue
        try:
            shutil.rmtree(path)
            deleted += 1
        except Exception as e:
            logger.warning("트레이스 삭제 실패(%s): %s", path, e)

    if deleted:
        logger.info("트레이스 정리: %s일 경과 %s개 삭제", retention_days, deleted)
    return deleted


def cleanup_file_logs(project_root: str | Path, sql_days: object, trace_days: object) -> None:
    """SQL 로그·트레이스를 한 번에 정리한다 (기동 시·주기 루프 공용 진입점).

    로그 정리 실패가 **서버 기동을 막아서는 안 되므로** 예외를 여기서 차단한다.
    다만 침묵시키지 않고 사유를 WARNING으로 남긴다.
    """
    try:
        cleanup_old_sql_logs(project_root, sql_days)
        cleanup_old_traces(project_root, trace_days)
    except Exception as e:
        logger.warning("파일 로그 정리 실패: %s", e)
