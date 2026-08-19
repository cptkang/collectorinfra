"""mcp_server SQL 파일 로거 테스트 (D-140).

본체와 같은 `logs/sql/`에 append한다. 별도 프로세스라 동시 쓰기 시
레코드가 섞이지 않아야 한다(`O_APPEND` + 단일 write).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from mcp_server import sql_log


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("OBS_SQL_LOG_ENABLED", raising=False)
    sql_log._LOG_DIR = None
    sql_log._enabled = False
    yield
    sql_log._LOG_DIR = None
    sql_log._enabled = False


def _today_file(root: Path) -> Path:
    return root / "logs" / "sql" / f"{datetime.now().strftime('%Y-%m-%d')}.sql"


def test_init_creates_shared_logs_sql_dir(tmp_path):
    """본체와 같은 `logs/sql/` 경로를 쓴다."""
    sql_log.init(tmp_path)

    assert (tmp_path / "logs" / "sql").is_dir()
    assert sql_log.is_enabled()


def test_log_sql_marks_mcp_server_source(tmp_path):
    """레코드에 출처가 mcp_server로 구분 표기된다."""
    sql_log.init(tmp_path)

    sql_log.log_sql("SELECT 1", source="polestar_b0", execution_time_ms=3.0, row_count=2)

    content = _today_file(tmp_path).read_text(encoding="utf-8")
    assert "mcp_server" in content
    assert "SELECT 1;" in content
    assert "polestar_b0" in content


def test_error_is_recorded(tmp_path):
    """실패 SQL은 사유와 함께 기록된다."""
    sql_log.init(tmp_path)

    sql_log.log_sql("SELECT bad", source="s", error="boom")

    assert "boom" in _today_file(tmp_path).read_text(encoding="utf-8")


def test_disabled_via_env(tmp_path, monkeypatch):
    """OBS_SQL_LOG_ENABLED=false면 기록하지 않는다."""
    monkeypatch.setenv("OBS_SQL_LOG_ENABLED", "false")

    sql_log.init(tmp_path)
    sql_log.log_sql("SELECT 1", source="s")

    assert not sql_log.is_enabled()
    assert not _today_file(tmp_path).exists()


def test_write_failure_does_not_raise(tmp_path, monkeypatch):
    """쓰기 실패가 SQL 실행 경로로 전파되지 않는다."""
    sql_log.init(tmp_path)
    monkeypatch.setattr("builtins.open", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))

    sql_log.log_sql("SELECT 1", source="s")  # 예외 없음


def test_concurrent_appends_do_not_interleave(tmp_path):
    """여러 writer가 동시에 append해도 레코드가 온전하다.

    본체 프로세스와 mcp_server가 같은 파일에 쓰는 상황을 모사한다.
    각 레코드는 `-- ==========` 헤더로 시작해 SQL 종결 세미콜론으로 끝난다.
    """
    import threading

    sql_log.init(tmp_path)

    def _writer(tag: str) -> None:
        for i in range(50):
            sql_log.log_sql(f"SELECT {tag}_{i}", source=tag)

    threads = [threading.Thread(target=_writer, args=(t,)) for t in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    content = _today_file(tmp_path).read_text(encoding="utf-8")
    assert content.count("-- ==========") == 100
    for tag in ("a", "b"):
        for i in range(50):
            assert f"SELECT {tag}_{i};" in content


def test_project_root_autodetect():
    """인자 없이 초기화하면 리포지토리 루트를 찾는다."""
    root = sql_log._default_project_root()

    assert (root / "mcp_server").is_dir()
    assert root.name == "collectorinfra" or (root / "pyproject.toml").exists()
