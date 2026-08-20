"""로그 보존 정리 테스트 (D-140/D-141).

D-083 선례 — `cleanup_old_logs()`가 "구현·설정은 있으나 호출부 전역 0건이라 무효"였다.
그래서 동작 검증뿐 아니라 **호출부가 실제로 존재하는지**도 여기서 단언한다.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from src.utils import log_retention


def _make_sql_log(root: Path, days_ago: int) -> Path:
    d = root / "logs" / "sql"
    d.mkdir(parents=True, exist_ok=True)
    stamp = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    f = d / f"{stamp}.sql"
    f.write_text("-- dummy\n", encoding="utf-8")
    return f


def _make_trace_dir(root: Path, days_ago: int) -> Path:
    stamp = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    d = root / "logs" / "trace" / stamp
    d.mkdir(parents=True, exist_ok=True)
    (d / "abc123.jsonl").write_text('{"step":1}\n', encoding="utf-8")
    return d


def test_sql_log_boundary(tmp_path):
    """정확히 N일 경과분은 남기고 N+1일부터 삭제한다."""
    keep_new = _make_sql_log(tmp_path, 0)
    keep_edge = _make_sql_log(tmp_path, 30)
    drop = _make_sql_log(tmp_path, 31)

    deleted = log_retention.cleanup_old_sql_logs(tmp_path, 30)

    assert deleted == 1
    assert keep_new.exists() and keep_edge.exists()
    assert not drop.exists()


def test_trace_boundary(tmp_path):
    """트레이스는 날짜 디렉토리 단위로 삭제된다."""
    keep = _make_trace_dir(tmp_path, 14)
    drop = _make_trace_dir(tmp_path, 15)

    deleted = log_retention.cleanup_old_traces(tmp_path, 14)

    assert deleted == 1
    assert keep.is_dir()
    assert not drop.exists()


def test_retention_zero_disables(tmp_path):
    """보존 일수 0 이하면 정리하지 않는다."""
    old = _make_sql_log(tmp_path, 999)
    old_trace = _make_trace_dir(tmp_path, 999)

    assert log_retention.cleanup_old_sql_logs(tmp_path, 0) == 0
    assert log_retention.cleanup_old_traces(tmp_path, -1) == 0
    assert old.exists() and old_trace.is_dir()


def test_missing_dir_is_noop(tmp_path):
    """대상 디렉토리가 없어도 예외 없이 0을 반환한다."""
    assert log_retention.cleanup_old_sql_logs(tmp_path, 30) == 0
    assert log_retention.cleanup_old_traces(tmp_path, 14) == 0


def test_other_log_files_untouched(tmp_path):
    """감사 로그·알람 판정 파일은 건드리지 않는다."""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    audit = logs / "audit-2020-01-01.jsonl"
    audit.write_text("{}\n", encoding="utf-8")
    alarm = logs / "alarm_decisions.jsonl"
    alarm.write_text("{}\n", encoding="utf-8")
    _make_sql_log(tmp_path, 999)

    log_retention.cleanup_old_sql_logs(tmp_path, 1)
    log_retention.cleanup_old_traces(tmp_path, 1)

    assert audit.exists() and alarm.exists()


def test_unparsable_names_are_kept(tmp_path):
    """날짜로 해석되지 않는 이름은 삭제 대상에서 제외한다(오삭제 방지)."""
    d = tmp_path / "logs" / "sql"
    d.mkdir(parents=True, exist_ok=True)
    odd = d / "notes.sql"
    odd.write_text("-- keep me\n", encoding="utf-8")

    assert log_retention.cleanup_old_sql_logs(tmp_path, 1) == 0
    assert odd.exists()


def test_failure_does_not_raise(tmp_path, monkeypatch):
    """삭제 실패가 기동을 막지 않는다."""
    _make_sql_log(tmp_path, 999)

    def _boom(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "unlink", _boom)

    assert log_retention.cleanup_old_sql_logs(tmp_path, 1) == 0  # 예외 전파 없음


def test_cleanup_file_logs_calls_both(tmp_path):
    """공용 진입점이 SQL·트레이스 정리를 모두 수행한다."""
    old_sql = _make_sql_log(tmp_path, 99)
    old_trace = _make_trace_dir(tmp_path, 99)

    log_retention.cleanup_file_logs(tmp_path, 30, 14)

    assert not old_sql.exists()
    assert not old_trace.exists()


def test_non_integer_retention_is_safe(tmp_path):
    """보존 일수가 정수가 아니면 정리를 건너뛴다 (삭제 0건·예외 0건).

    회귀 고정: MagicMock config를 쓰는 테스트(`test_api/test_routes.py`)에서
    `retention_days <= 0` 비교가 TypeError를 던져 기동 경로가 통째로 깨졌다(2026-08-19).
    """
    from unittest.mock import MagicMock

    kept_sql = _make_sql_log(tmp_path, 999)
    kept_trace = _make_trace_dir(tmp_path, 999)

    for bad in (MagicMock(), None, "삼십일", object()):
        assert log_retention.cleanup_old_sql_logs(tmp_path, bad) == 0
        assert log_retention.cleanup_old_traces(tmp_path, bad) == 0

    log_retention.cleanup_file_logs(tmp_path, MagicMock(), MagicMock())

    assert kept_sql.exists() and kept_trace.is_dir()


def test_numeric_string_retention_works(tmp_path):
    """정수로 해석 가능한 값은 정상 동작한다 (env 유래 문자열 방어)."""
    drop = _make_sql_log(tmp_path, 99)

    assert log_retention.cleanup_old_sql_logs(tmp_path, "30") == 1
    assert not drop.exists()


def test_cleanup_is_actually_wired():
    """호출부가 실제로 존재한다 (D-083 재발 방지 — 구현만 있고 호출 0건인 상태 차단).

    server.py는 공용 진입점 `cleanup_file_logs`를 부르고, 그 함수가 두 정리 함수를
    호출한다(위 테스트가 사슬을 고정). 여기서는 배선 지점 2곳을 단언한다 —
    기동 시 1회 + 주기 루프.
    """
    server_src = Path("src/api/server.py").read_text(encoding="utf-8")

    assert server_src.count("cleanup_file_logs(") >= 2, (
        "파일 로그 정리 호출부가 부족함 (기동 1회 + 주기 루프 2곳 필요)"
    )
    assert "_run_file_log_retention_loop" in server_src, "주기 정리 루프가 배선되지 않음"


class TestFailureIsolation:
    """정리 실패가 기동을 막지 않되, 조용히 넘어가지도 않는다."""

    def test_trace_removal_failure_is_tolerated(self, tmp_path, monkeypatch, caplog):
        """트레이스 디렉토리 삭제 실패는 사유를 남기고 계속 진행한다."""
        import shutil

        _make_trace_dir(tmp_path, 99)
        _make_trace_dir(tmp_path, 98)

        def _boom(path):
            raise PermissionError("denied")

        monkeypatch.setattr(shutil, "rmtree", _boom)

        with caplog.at_level("WARNING"):
            deleted = log_retention.cleanup_old_traces(tmp_path, 1)

        assert deleted == 0
        assert any("트레이스 삭제 실패" in r.message for r in caplog.records)

    def test_cleanup_file_logs_absorbs_unexpected_errors(self, tmp_path, monkeypatch, caplog):
        """예상 밖 예외도 기동을 막지 않는다 (다만 사유는 남는다)."""
        def _boom(*a, **k):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(log_retention, "cleanup_old_sql_logs", _boom)

        with caplog.at_level("WARNING"):
            log_retention.cleanup_file_logs(tmp_path, 30, 14)

        assert any("파일 로그 정리 실패" in r.message for r in caplog.records)

    def test_non_directory_entries_in_trace_root_are_skipped(self, tmp_path):
        """트레이스 루트의 파일(디렉토리 아님)은 건드리지 않는다."""
        trace_root = tmp_path / "logs" / "trace"
        trace_root.mkdir(parents=True)
        stray = trace_root / "2000-01-01.txt"
        stray.write_text("x", encoding="utf-8")

        assert log_retention.cleanup_old_traces(tmp_path, 1) == 0
        assert stray.exists()
