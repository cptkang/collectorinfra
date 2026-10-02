"""SQL 파일 로거 테스트 (D-140).

출력 경로가 `logs/sql/`인지, 게이트·실패 격리·단일 write가 지켜지는지 실 파일로 검증한다.
mock이 아니라 tmp_path에 실제로 쓰고 읽어 확인한다 —
"mock 통과 ≠ 프로덕션 동작"(Known Mistakes).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from src.utils import sql_file_logger


@pytest.fixture(autouse=True)
def _reset_logger():
    """각 테스트가 로거 전역 상태를 물려받지 않도록 초기화한다."""
    sql_file_logger._SQL_LOG_DIR = None
    sql_file_logger._enabled = False
    yield
    sql_file_logger._SQL_LOG_DIR = None
    sql_file_logger._enabled = False


def _today_file(root: Path) -> Path:
    return root / "logs" / "sql" / f"{datetime.now().strftime('%Y-%m-%d')}.sql"


def test_init_creates_logs_sql_dir(tmp_path):
    """초기화가 `logs/sql/`을 만든다 — 종전 `sqls/act/`가 아니다."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    assert (tmp_path / "logs" / "sql").is_dir()
    assert not (tmp_path / "sqls" / "act").exists()
    assert sql_file_logger.is_enabled()


def test_log_sql_writes_to_logs_sql(tmp_path):
    """실행 SQL이 날짜별 파일에 기록된다."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    sql_file_logger.log_sql(
        "SELECT 1 FROM dual",
        execution_time_ms=12.5,
        row_count=1,
        source="polestar_b0",
    )

    content = _today_file(tmp_path).read_text(encoding="utf-8")
    assert "SELECT 1 FROM dual;" in content
    assert "polestar_b0" in content
    assert "12.5ms" in content
    assert "행: 1" in content


def test_log_sql_records_error(tmp_path):
    """실패한 SQL은 에러 사유와 함께 기록된다 (침묵 폴백 금지)."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    sql_file_logger.log_sql("SELECT bad", error="SQLCODE=-206", source="polestar_b0")

    content = _today_file(tmp_path).read_text(encoding="utf-8")
    assert "SQLCODE=-206" in content


def test_log_sql_noop_when_not_initialized(tmp_path):
    """초기화 전 호출은 아무 파일도 만들지 않고 예외도 내지 않는다."""
    sql_file_logger.log_sql("SELECT 1")

    assert not (tmp_path / "logs").exists()


def test_disabled_flag_skips_write(tmp_path):
    """`enabled=False`로 초기화하면 파일을 만들지 않는다 (OBS_SQL_LOG_ENABLED 게이트)."""
    sql_file_logger.init_sql_file_logger(tmp_path, enabled=False)

    sql_file_logger.log_sql("SELECT 1")

    assert not sql_file_logger.is_enabled()
    assert not _today_file(tmp_path).exists()


def test_init_failure_does_not_raise(tmp_path, monkeypatch):
    """디렉토리 생성 실패가 앱을 죽이지 않는다."""

    def _boom(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "mkdir", _boom)

    sql_file_logger.init_sql_file_logger(tmp_path)  # 예외 전파 없음

    assert not sql_file_logger.is_enabled()


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    """쓰기 실패가 메인 로직으로 전파되지 않는다."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)

    sql_file_logger.log_sql("SELECT 1")  # 예외 전파 없음


def test_record_written_in_single_write_call(tmp_path, monkeypatch):
    """레코드가 단일 write() 호출로 기록된다 (동시 append 인터리브 방지)."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    calls: list[str] = []
    real_open = open

    class _CountingFile:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            calls.append(data)
            return self._fh.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

    def _wrapped(path, *args, **kwargs):
        return _CountingFile(real_open(path, *args, **kwargs))

    monkeypatch.setattr("builtins.open", _wrapped)

    sql_file_logger.log_sql("SELECT 1", source="db1")

    assert len(calls) == 1, f"레코드가 {len(calls)}번에 나눠 기록됨 — 인터리브 위험"


def test_shallow_stack_still_logs(tmp_path):
    """호출 스택이 얕아도 기록된다.

    회귀 고정: `inspect.stack()[caller_depth]`가 IndexError를 던져 SQL 기록이 통째로
    유실됐다(2026-08-19 스모크에서 실측). 예외를 삼키는 구조라 조용히 사라졌다.
    """
    sql_file_logger.init_sql_file_logger(tmp_path)

    sql_file_logger.log_sql("SELECT 1", source="db1", caller_depth=99)

    assert "SELECT 1;" in _today_file(tmp_path).read_text(encoding="utf-8")


def test_appends_multiple_records(tmp_path):
    """여러 레코드가 같은 파일에 누적된다."""
    sql_file_logger.init_sql_file_logger(tmp_path)

    sql_file_logger.log_sql("SELECT 1", source="db1")
    sql_file_logger.log_sql("SELECT 2", source="db2")

    content = _today_file(tmp_path).read_text(encoding="utf-8")
    assert "SELECT 1;" in content and "SELECT 2;" in content
