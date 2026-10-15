"""plans/164 — apm_bench Windows 분기(내부망 Windows 실행 · `os.geteuid` 없음
AttributeError 교정).

맥·리눅스에서 `rundir.IS_WINDOWS`를 켜 Windows 경로의 판정만 확인한다(실제 Win32 호출은
가짜로 바꾼다).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.apm_bench import __main__ as cli
from scripts.apm_bench import rundir as rd


@pytest.fixture(autouse=True)
def _windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows 흉내 — `os.geteuid`·`os.getuid`·`os.kill`이 불리면 실패한다."""
    monkeypatch.setattr(rd, "IS_WINDOWS", True)
    monkeypatch.delattr(os, "geteuid", raising=False)
    monkeypatch.delattr(os, "getuid", raising=False)

    def _no_kill(*_a: Any) -> None:
        raise AssertionError("Windows 에서 os.kill 은 프로세스를 끝낸다 — 쓰면 안 된다")

    monkeypatch.setattr(os, "kill", _no_kill)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(cli, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path / "results")
    monkeypatch.setattr(rd, "default_quarantine_base", lambda: tmp_path / "quarantine")


def test_trust_check_skips_owner_and_mode_but_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "p.yaml"
    target.write_text("values: {}\n", encoding="utf-8")
    target.chmod(0o666)  # POSIX 라면 「그룹·기타 쓰기」 거부
    assert cli.untrusted_reason(target) is None
    link = tmp_path / "link.yaml"
    link.symlink_to(target)
    assert cli.untrusted_reason(link) == "심볼릭 링크다"


def test_init_local_then_e2_reaches_unfilled_guard(capsys: pytest.CaptureFixture[str]) -> None:
    """사용자 화면의 traceback 경로(--run --env e2 --smoke → 로컬 파일 신뢰 검사) 재현."""
    assert cli.main(["--init-local"]) == 0
    capsys.readouterr()
    assert cli.main(["--run", "--env", "e2", "--smoke"]) == 2
    assert "바꾸지 않은 칸" in capsys.readouterr().out


def test_pid_alive_uses_process_handle_not_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(rd, "_windows_pid_alive", lambda pid: seen.append(pid) or True)
    assert rd.pid_alive(4321) is True and seen == [4321]
    assert rd.pid_alive(None) is False and rd.pid_alive(0) is False


def test_quarantine_written_by_path_and_trusted(tmp_path: Path) -> None:
    run_dir = tmp_path / "results" / "r"
    run_dir.mkdir(parents=True)
    final = rd.write_quarantine(tmp_path / "quarantine", run_dir, "20261015-090000-e2-aaaaaaa",
                                {"context.json": {"a": 1}}, "scenarios: []\n")
    assert (final / "context.json").read_text(encoding="utf-8") == '{"a": 1}'
    (final / "context.json").chmod(0o644)  # Windows 에는 권한 비트가 없다 — 판정하지 않는다
    assert rd.check_trusted(final) == final.resolve()


def test_log_symlink_rejected_without_o_nofollow(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rd, "O_NOFOLLOW", 0)
    monkeypatch.setattr(rd, "O_NONBLOCK", 0)
    real = tmp_path / "elsewhere.log"
    real.write_text("x\n", encoding="utf-8")
    (tmp_path / rd.LOG_FILE).symlink_to(real)
    with pytest.raises(rd.QuarantineError), rd.opened_log(tmp_path, append=True):
        pass
    assert rd.last_log_line(tmp_path) == rd.LOG_UNVERIFIED
    (tmp_path / rd.LOG_FILE).unlink()
    with rd.opened_log(tmp_path) as handle:
        handle.write("끝\n")
    assert rd.last_log_line(tmp_path) is not None


def test_bg_detaches_with_creationflags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "BG_WAIT_SEC", 0.2)
    monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    calls: list[dict[str, Any]] = []

    class _Proc:
        pid = 4321

        def poll(self) -> None:
            return None

    def popen(cmd: list[str], **kw: Any) -> _Proc:
        calls.append(kw)
        return _Proc()

    argv = ["--run", "--env", "e1", "--smoke", "--bg"]
    args = cli.build_parser().parse_args(argv)
    args.raw_argv = argv
    assert cli.cmd_run(args, popen=popen) == 0
    (kw,) = calls
    assert kw["creationflags"] == 0x8 | 0x200 and "start_new_session" not in kw
    assert kw["stdout"] is subprocess.DEVNULL


@pytest.mark.skipif(sys.platform != "win32", reason="실 Win32 API — Windows 에서만")
def test_windows_pid_alive_real() -> None:
    assert rd._windows_pid_alive(os.getpid()) is True


def test_full_run_path_without_posix_calls(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """가짜 드라이버로 run 하나를 끝까지(run.pid · run.log · 관문 · 산출물) — POSIX 전용 호출
    없음."""
    from tests.test_scripts.test_apm_bench_cli import SCENARIO, _fake_driver

    monkeypatch.setattr(rd, "_windows_pid_alive", lambda pid: pid == os.getpid())
    out = tmp_path / "out"
    args = cli.build_parser().parse_args(["--run", "--only", SCENARIO, "--out", str(out)])
    assert cli.cmd_run(args, driver_run=_fake_driver({})) == 0
    assert (out / rd.LOG_FILE).is_file() and not (out / rd.PID_FILE).exists()
    assert cli.main(["--status", str(out)]) == 0
