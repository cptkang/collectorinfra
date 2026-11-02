"""plans/206 W0 — 측정 스크립트 2종 스모크(`scripts/db2_probe.py` · `scripts/bench_cpu.py`).

`db2_probe`는 가짜 `ibm_db` 모듈을 `sys.modules`에 넣어 ①~④ 경로가 예외 없이 돌고 출력에
연결 문자열·비밀번호가 실리지 않음을 본다(ibm_db 미설치 · 실 드라이버 동작은 폐쇄망 실측).
`bench_cpu`는 `--repeat 1`로 구간 4개가 출력되는지만 본다.
"""

from __future__ import annotations

import importlib.util
import itertools
import subprocess
import sys
import threading
import types
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
_SECRET = "s3cretPw"
_CONN = f"DATABASE=sample;HOSTNAME=db.example;PORT=50000;PROTOCOL=TCPIP;UID=reader;PWD={_SECRET};"


def _load_probe() -> ModuleType:
    """`db2_probe.py`를 경로로 로드한다."""
    spec = importlib.util.spec_from_file_location("db2_probe_under_test", _SCRIPTS / "db2_probe.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Stmt:
    def __init__(self, conn_id: int) -> None:
        self.rows = iter([{"ONE": 1}])
        self.conn_id = conn_id


def _fake_ibm_db(
    monkeypatch: pytest.MonkeyPatch, connect_error: bool = False, app_id_error: bool = False
) -> dict[str, Any]:
    """가짜 ibm_db를 넣고 호출 기록(`connect`·`active` 횟수·실행 스레드 이름)을 돌려준다."""
    log: dict[str, Any] = {"connect": 0, "active": 0, "threads": []}
    ids = itertools.count(1)
    persistent = {"id": next(ids)}
    mod = types.ModuleType("ibm_db")

    def connect(conn_str: str, user: str, pw: str) -> dict[str, int]:
        if connect_error:
            raise RuntimeError(f"SQL30082N 접속 실패: {conn_str}")
        log["connect"] += 1
        return {"id": next(ids)}

    def exec_immediate(conn: dict[str, int], sql: str, *options: Any) -> _Stmt:
        log["threads"].append(threading.current_thread().name)
        return _Stmt(conn["id"])

    def active(conn: dict[str, int]) -> bool:
        log["active"] += 1
        return True

    def fetch_tuple(stmt: _Stmt) -> tuple[str]:
        if app_id_error:
            raise RuntimeError(f"SQL0440N APPLICATION_ID 없음: {_CONN}")
        return (f"APP-{stmt.conn_id}",)

    mod.SQL_ATTR_QUERY_TIMEOUT = 0  # type: ignore[attr-defined]
    mod.SQL_AUTOCOMMIT_ON = 1  # type: ignore[attr-defined]
    mod.connect = connect  # type: ignore[attr-defined]
    mod.pconnect = lambda conn_str, user, pw: persistent  # type: ignore[attr-defined]
    mod.close = lambda conn: True  # type: ignore[attr-defined]
    mod.active = active  # type: ignore[attr-defined]
    mod.autocommit = lambda conn, *value: 1  # type: ignore[attr-defined]
    mod.free_stmt = lambda stmt: True  # type: ignore[attr-defined]
    mod.exec_immediate = exec_immediate  # type: ignore[attr-defined]
    mod.fetch_assoc = lambda stmt: next(stmt.rows, False)  # type: ignore[attr-defined]
    mod.fetch_tuple = fetch_tuple  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ibm_db", mod)
    return log


def test_db2_probe_runs_all_items_without_leaking_connection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """①~④가 예외 없이 돌고, ①은 매회 다른 스레드이며, 출력에 연결 문자열·비밀번호가 없다."""
    log = _fake_ibm_db(monkeypatch)
    monkeypatch.setenv("PROBE_DB2_CONN", _CONN)
    probe = _load_probe()

    code = probe.main(["--conn-env", "PROBE_DB2_CONN"])

    out = capsys.readouterr()
    text = out.out + out.err
    assert code == 0, text
    for header in ("[1]", "[2]", "[3]", "[4]"):
        assert header in text
    assert "실패" not in text
    assert "성공 5/5" in text
    assert len(set(log["threads"][:5])) == 5  # ① 매회 다른 스레드
    assert "같은 객체(is): True" in text  # ③ 가짜 pconnect는 같은 핸들을 돌려준다
    assert "autocommit 현재값: 1" in text  # ① 연결의 autocommit
    assert "ibm_db.autocommit:" in text and "ibm_db.SQL_AUTOCOMMIT_ON: 있음" in text  # ②
    assert "비율(connect 평균 / 실행 p50)" in text
    assert "(connect 평균 − active 평균) / 실행 p50 · 판정 기준" in text  # ④ 풀 경로 반영
    assert log["active"] == 10  # ④ active 기본 10회
    assert _SECRET not in text
    assert _CONN not in text
    assert "HOSTNAME=" not in text


def test_db2_probe_masks_connection_in_driver_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """드라이버 예외 메시지에 연결 문자열이 섞여도 가리고, 실패 항목과 무관한 항목은 진행한다."""
    _fake_ibm_db(monkeypatch, connect_error=True)
    monkeypatch.setenv("PROBE_DB2_CONN", _CONN)
    probe = _load_probe()

    code = probe.main(["--conn-env", "PROBE_DB2_CONN", "--only", "1,2,4"])

    text = capsys.readouterr().out
    assert code == 1
    assert "[1] 실패" in text and "[4] 실패" in text
    assert "ibm_db.connect" in text  # ②는 진행됐다
    assert _SECRET not in text
    assert _CONN not in text


def test_db2_probe_app_id_unsupported_is_not_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """③의 APPLICATION_ID 조회가 실패해도 안내만 내고 ③을 실패로 치지 않는다(비밀값 가림)."""
    _fake_ibm_db(monkeypatch, app_id_error=True)
    monkeypatch.setenv("PROBE_DB2_CONN", _CONN)
    probe = _load_probe()

    code = probe.main(["--conn-env", "PROBE_DB2_CONN", "--only", "3"])

    text = capsys.readouterr().out
    assert code == 0, text
    assert "APPLICATION_ID 미지원 — is 비교만 유효" in text
    assert "같은 객체(is): True" in text
    assert _SECRET not in text and _CONN not in text


def test_db2_probe_config_load_error_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`load_config`의 설정 오류는 traceback 없이 종료 코드 2 — 메시지에 비밀값이 없다."""
    log = _fake_ibm_db(monkeypatch)
    probe = _load_probe()

    def broken(path: Any) -> None:
        raise ValueError(f"PROBE_DB2_CONNECTION 무효: {_CONN}")

    monkeypatch.setattr(probe, "load_config", broken)

    code = probe.main(["--source", "probe_db2"])

    err = capsys.readouterr().err
    assert code == 2
    assert "설정 로드 실패 — ValueError" in err
    assert _SECRET not in err
    assert log["connect"] == 0


def test_db2_probe_rejects_write_sql_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """읽기 전용 위반 SQL은 접속 전에 종료 코드 2로 거부한다(D-003)."""
    log = _fake_ibm_db(monkeypatch)
    monkeypatch.setenv("PROBE_DB2_CONN", _CONN)
    probe = _load_probe()

    code = probe.main(["--conn-env", "PROBE_DB2_CONN", "--sql", "DELETE FROM t_sample"])

    assert code == 2
    assert log["connect"] == 0
    assert "읽기 전용 위반" in capsys.readouterr().err


def test_db2_probe_without_ibm_db_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`ibm_db` 임포트 실패면 안내 한 줄 뒤 종료 코드 2."""
    monkeypatch.setitem(sys.modules, "ibm_db", None)  # 임포트 시 ImportError
    probe = _load_probe()

    assert probe.main(["--conn-env", "PROBE_DB2_CONN"]) == 2
    assert "ibm_db 미설치" in capsys.readouterr().err


def test_db2_probe_reads_source_from_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--source`는 서버와 같은 로더(`config.toml` + `{SOURCE}_CONNECTION`)에서 연결을 얻는다."""
    log = _fake_ibm_db(monkeypatch)
    (tmp_path / "config.toml").write_text(
        '[[sources]]\nname = "probe_db2"\ntype = "db2"\n', encoding="utf-8"
    )
    monkeypatch.setenv("PROBE_DB2_CONNECTION", _CONN)
    probe = _load_probe()

    code = probe.main(
        ["--source", "probe_db2", "--config", str(tmp_path / "config.toml"), "--only", "4",
         "--repeat", "2"]
    )

    text = capsys.readouterr().out
    assert code == 0, text
    assert log["connect"] == 3  # 단독 2회 + 실행용 1회
    assert _SECRET not in text


def test_bench_cpu_smoke() -> None:
    """`bench_cpu.py --repeat 1`이 구간 4개를 출력한다."""
    pytest.importorskip("prometheus_client")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "bench_cpu.py"), "--repeat", "1"],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 5  # 머리 1줄 + 구간 4줄
    for key in ("1,000행", "10,000행", "validate_readonly", "parse_exposition"):
        assert any(key in line for line in lines[1:])
