"""`scripts/bench/probe.py` 테스트 (plans/93 · T-03).

프로세스를 실제로 띄우는 케이스는 **1건만** 둔다(느리다). 나머지는 러너를 주입해
파싱·판정 로직만 검증한다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import probe  # noqa: E402


def _proc(stdout: str = "", stderr: str = "", rc: int = 0):
    return subprocess.CompletedProcess(args=["x"], returncode=rc, stdout=stdout, stderr=stderr)


def _runner(stdout: str = "", stderr: str = "", rc: int = 0):
    def run(env, timeout):
        run.seen_env = env
        return _proc(stdout, stderr, rc)
    return run


def _echo_line(payload: dict) -> str:
    return probe._MARKER + json.dumps(payload, ensure_ascii=False)


# ── 파싱 ───────────────────────────────────────────────────

def test_echo_ok():
    line = _echo_line({"ok": True, "config": {"llm.provider": "fabrix"}})
    res = probe.echo_config(runner=_runner(stdout=line))
    assert res.ok and res.value_of("llm.provider") == "fabrix"


def test_echo_survives_log_noise_before_and_after():
    """설정 로딩 중 로그가 stdout에 섞여도 마커 줄만 골라낸다.

    `eval_text2sql`가 감사 structlog 때문에 JSON 파싱이 깨진 전례(2026-08-04)와 같은 유형.
    """
    line = _echo_line({"ok": True, "config": {"a": 1}})
    noisy = f"WARNING 뭔가\n{line}\n[info] 뒤에 붙은 로그\n"
    res = probe.echo_config(runner=_runner(stdout=noisy))
    assert res.ok and res.config == {"a": 1}


def test_echo_reports_validation_error_as_data_not_exception():
    """기동 실패는 L2의 **측정 대상**이므로 예외가 아니라 결과로 돌려준다."""
    line = _echo_line({"ok": False, "error_type": "ValidationError", "error": "bad int"})
    res = probe.echo_config(runner=_runner(stdout=line))
    assert res.ok is False
    assert res.error_type == "ValidationError" and "bad int" in res.error


def test_echo_missing_marker():
    res = probe.echo_config(runner=_runner(stdout="아무것도 없음", stderr="ImportError: x"))
    assert res.ok is False and res.error_type == "NoEcho"
    assert "ImportError" in (res.stderr_tail or "")


def test_echo_malformed_json_falls_through():
    res = probe.echo_config(runner=_runner(stdout=probe._MARKER + "{not json"))
    assert res.ok is False and res.error_type == "NoEcho"


def test_echo_timeout_is_data():
    def run(env, timeout):
        raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
    res = probe.echo_config(runner=run, timeout=1.0)
    assert res.ok is False and res.error_type == "TimeoutExpired"


def test_echo_generic_exception_is_data():
    def run(env, timeout):
        raise OSError("실행 불가")
    res = probe.echo_config(runner=run)
    assert res.ok is False and res.error_type == "OSError"


# ── env 주입 ───────────────────────────────────────────────

def test_override_sets_and_deletes():
    r = _runner(stdout=_echo_line({"ok": True, "config": {}}))
    probe.echo_config({"NEW_KEY": "1", "DROP_ME": None},
                      base_env={"DROP_ME": "x", "KEEP": "y"}, runner=r)
    assert r.seen_env["NEW_KEY"] == "1"
    assert "DROP_ME" not in r.seen_env
    assert r.seen_env["KEEP"] == "y"


def test_override_values_are_stringified():
    r = _runner(stdout=_echo_line({"ok": True, "config": {}}))
    probe.echo_config({"N": 7}, base_env={}, runner=r)
    assert r.seen_env["N"] == "7"


# ── 지문 ───────────────────────────────────────────────────

def test_fingerprint_stable_and_order_independent():
    a = probe.EchoResult(ok=True, config={"a": 1, "b": 2})
    b = probe.EchoResult(ok=True, config={"b": 2, "a": 1})
    assert probe.config_fingerprint(a) == probe.config_fingerprint(b)


def test_fingerprint_changes_on_value_change():
    a = probe.EchoResult(ok=True, config={"a": 1})
    b = probe.EchoResult(ok=True, config={"a": 2})
    assert probe.config_fingerprint(a) != probe.config_fingerprint(b)


def test_fingerprint_excludes_keys():
    a = probe.EchoResult(ok=True, config={"a": 1, "auth.jwt_secret": "x"})
    b = probe.EchoResult(ok=True, config={"a": 1, "auth.jwt_secret": "y"})
    ex = frozenset({"auth.jwt_secret"})
    assert probe.config_fingerprint(a, exclude_keys=ex) == probe.config_fingerprint(b, exclude_keys=ex)


def test_detect_nondeterministic_keys():
    """같은 조건 2회에서 달라지는 필드를 찾는다."""
    seq = iter([
        _echo_line({"ok": True, "config": {"stable": 1, "random": "aaa"}}),
        _echo_line({"ok": True, "config": {"stable": 1, "random": "bbb"}}),
    ])

    def run(env, timeout):
        return _proc(stdout=next(seq))

    assert probe.detect_nondeterministic_keys(runner=run) == frozenset({"random"})


def test_detect_nondeterministic_returns_empty_when_baseline_fails():
    """기준을 못 읽으면 판정을 지어내지 않는다."""
    def run(env, timeout):
        return _proc(stdout="")
    assert probe.detect_nondeterministic_keys(runner=run) == frozenset()


# ── 실제 프로세스 왕복 (2건 · 각 1초 미만) ────────────────

def test_real_child_roundtrip_and_masking():
    """실제로 파이썬을 띄워 설정을 읽어온다.

    함께 확인하는 것: ①주입이 실제로 먹는가 ②민감값이 평문으로 새지 않는가.
    """
    res = probe.echo_config({"TEXT2SQL_CANDIDATE_COUNT": "7"})
    assert res.ok, f"자식 실행 실패: {res.error_type} {res.error}"
    assert res.value_of("text2sql.candidate_count") == 7, "주입이 자식에 반영되지 않았다"

    secret = str(res.value_of("auth.jwt_secret") or "")
    assert secret.startswith("sha256:"), "민감값이 평문으로 에코됐다"


def test_real_child_reports_validation_error():
    res = probe.echo_config({"TEXT2SQL_CANDIDATE_COUNT": "not-a-number"})
    assert res.ok is False
    assert res.error_type == "ValidationError"
    assert "candidate_count" in (res.error or "")


# ── Windows 인코딩 회귀 (2026-09-11 폐쇄망 실측) ──────────

def test_default_runner_forces_utf8_on_child(monkeypatch):
    """★ 자식에게 `PYTHONIOENCODING=utf-8`을 준다.

    이것이 없으면 Windows 콘솔 코드페이지(cp949)와 어긋나 `subprocess`의 리더 스레드가
    죽고, `capture_output=True`인데도 stdout이 None이 된다. 우리 판정에서는 "에코 없음"이
    되어 **인코딩 문제가 설정 문제로 오판**된다.
    """
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return _proc(stdout=_echo_line({"ok": True, "config": {}}))

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.echo_config({"K": "1"}, base_env={})
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"
    assert seen["encoding"] == "utf-8"
    assert seen["errors"] == "replace", "깨진 바이트에 죽지 않고 치환해야 한다"


def test_default_runner_preserves_caller_env(monkeypatch):
    """인코딩을 주입하면서 호출자의 env를 잃지 않는다."""
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return _proc(stdout=_echo_line({"ok": True, "config": {}}))

    monkeypatch.setattr(probe.subprocess, "run", fake_run)
    probe.echo_config({"MY_KEY": "v"}, base_env={"KEEP": "y"})
    assert seen["env"]["MY_KEY"] == "v" and seen["env"]["KEEP"] == "y"


def test_echo_survives_none_stdout():
    """리더 스레드가 죽어 stdout이 None으로 와도 예외를 던지지 않는다.

    사용자 화면의 `AttributeError: 'NoneType' object has no attribute 'strip'`이
    바로 이 상황에서 났다(다른 모듈에서). 우리 쪽은 데이터로 돌려준다.
    """
    def run(env, timeout):
        return subprocess.CompletedProcess(args=["x"], returncode=1, stdout=None, stderr=None)

    res = probe.echo_config(runner=run)
    assert res.ok is False and res.error_type == "NoEcho"


def test_echo_handles_replacement_characters():
    """`errors="replace"`가 남긴 치환 문자가 섞여도 마커 줄은 파싱된다."""
    line = _echo_line({"ok": True, "config": {"a": 1}})
    res = probe.echo_config(runner=_runner(stdout="\ufffd\ufffd 깨진앞줄\n" + line))
    assert res.ok and res.config == {"a": 1}
