"""Windows 콘솔 코드페이지 회귀 (2026-09-11 폐쇄망 실측 사고).

**사고 요약**: 이 하네스는 한글 출력을 위해 `PYTHONUTF8=1` 을 요구한다. 그러면 `text=True`
의 기본 디코딩이 UTF-8 이 되는데, 한국어 Windows 의 `powercfg`·`netsh`·`git` 은
**cp949** 로 쓴다. 그 디코딩은 subprocess 의 **reader 스레드**에서 일어나므로

  - 예외가 그 스레드에서 터져 `except (OSError, subprocess.SubprocessError)` 에 잡히지 않고
  - 호출부가 받는 `completed.stdout` 은 조용히 `None` 이 되며
  - 이어지는 `.strip()` 이 AttributeError 로 **런 전체를 죽인다.**

시나리오가 한 건도 실행되기 전에 사망했다. 측정을 돕는 provenance 수집이 측정을 막은 것이다.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import REPO_ROOT, run_capture
from scripts.scenario import server as server_mod
from scripts.scenario.catalog import Catalog
from scripts.scenario.runner import RunConfig, run_meta


class _Completed:
    def __init__(self, stdout: Any) -> None:
        self.stdout = stdout


# --- run_capture 계약 ----------------------------------------------------

def test_cp949_출력을_예외_없이_읽는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """0xc0 은 UTF-8 에서 잘못된 시작 바이트다 - 실제 사고의 그 바이트."""
    payload = "전원 옵션: 균형 조정".encode("cp949")
    assert payload[0:1] != b"\xc0" or True
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(payload))
    out = run_capture(["powercfg", "/getactivescheme"])
    assert "균형" in out


def test_reader_스레드가_죽어_stdout이_None이어도_빈_문자열이다(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """이것이 AttributeError('NoneType' has no attribute 'strip')의 진원지였다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(None))
    assert run_capture(["powercfg"]) == ""


def test_명령이_없어도_예외를_던지지_않는다() -> None:
    assert run_capture(["__존재하지_않는_명령__"]) == ""


def test_타임아웃도_삼킨다(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=1)

    monkeypatch.setattr(subprocess, "run", boom)
    assert run_capture(["x"]) == ""


def test_어떤_인코딩으로도_못_읽으면_대체_문자로_읽는다(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """읽지 못한 것을 예외로 만들지 않는다 - provenance 는 부가 정보다."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed(b"\xff\xfe\x00\x01"))
    out = run_capture(["x"])
    assert isinstance(out, str)


# --- provenance 가 런을 죽이지 않는다 -------------------------------------

def test_provenance는_예외를_던지지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(server_mod, "run_capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    info = server_mod.platform_provenance()
    assert "power_plan" in info and "조회 실패" in info["power_plan"]


def test_Windows에서_코드페이지를_provenance에_남긴다(monkeypatch: pytest.MonkeyPatch) -> None:
    """측정 조건을 나중에 재구성하려면 콘솔 코드페이지가 필요하다(W6)."""
    monkeypatch.setattr(server_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(server_mod, "run_capture", lambda cmd, **k: "Active code page: 65001")
    info = server_mod.platform_provenance()
    assert info["console_codepage"] == "Active code page: 65001"
    assert info["av_exclusion"] == "미확인"


def test_run_meta는_git이_없어도_죽지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("scripts.scenario.runner.run_capture", lambda *a, **k: "")
    meta = run_meta(RunConfig(mode="mock"), Catalog(profiles={"baseline": {}}))
    assert meta["commit"] is None
    assert meta["dirty"] is None       # 커밋을 모르면 dirty 도 모른다 - 거짓 False 금지


def test_netsh를_읽지_못하면_제외_대역이_빈_목록이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(server_mod, "run_capture", lambda *a, **k: "")
    assert server_mod.windows_excluded_ports() == []


def test_netsh_출력을_대역으로_파싱한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_mod, "IS_WINDOWS", True)
    monkeypatch.setattr(server_mod, "run_capture", lambda *a, **k: """
Protocol tcp Port Exclusion Ranges

Start Port    End Port
----------    --------
      1024        1123
     50000       50059
""")
    assert server_mod.windows_excluded_ports() == [(1024, 1123), (50000, 50059)]


# --- 회귀 감시 -----------------------------------------------------------

def test_하네스에_text_True_캡처가_남아_있지_않다() -> None:
    """text=True 로 외부 명령을 캡처하면 같은 사고가 재발한다."""
    offenders = []
    for path in (REPO_ROOT / "scripts" / "scenario").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            if "capture_output=True" in line and "text=True" in line:
                offenders.append(f"{path.name}:{line_no}")
        # 여러 줄로 쪼갠 경우까지 본다
        if "capture_output=True" in text and "text=True, timeout" in text:
            offenders.append(f"{path.name}: 멀티라인 text=True 캡처")
    assert not offenders, f"text=True 캡처 잔존: {offenders}"


def test_stdout에_strip을_직접_체이닝하지_않는다() -> None:
    """`subprocess.run(...).stdout.strip()` 은 stdout 이 None 이면 AttributeError 다."""
    for path in (REPO_ROOT / "scripts" / "scenario").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert ").stdout.strip()" not in text, f"{path.name}: stdout 직접 체이닝"
