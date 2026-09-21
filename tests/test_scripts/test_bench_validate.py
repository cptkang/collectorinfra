"""`scripts/bench/validate.py` 테스트 (plans/93 · T-04·T-05·T-06).

전 케이스가 **프로세스를 띄우지 않는다** — `probe.Runner`를 주입해 에코를 흉내낸다.
실제 왕복은 `test_bench_probe.py`가 이미 1건 덮는다.
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

from scripts.bench import catalog, probe, validate  # noqa: E402


def _knob(env_key="FLAG_A", **over) -> catalog.KnobSpec:
    base = dict(
        env_key=env_key, group_key="text2sql", field_name="flag_a", type="bool",
        enum_choices=None, default="false", consumed=True, is_secret=False,
        is_sensitive=False, apply_mode="restart", description="d",
    )
    base.update(over)
    return catalog.KnobSpec(**base)


def _echo_runner(config_for):
    """주입 env에 따라 에코 config를 만들어 주는 가짜 러너."""
    def run(env, timeout):
        payload = {"ok": True, "config": config_for(env)}
        return subprocess.CompletedProcess(
            args=["x"], returncode=0,
            stdout=probe._MARKER + json.dumps(payload), stderr="")
    return run


def _fail_runner(error_type="ValidationError", message="bad"):
    def run(env, timeout):
        payload = {"ok": False, "error_type": error_type, "error": message}
        return subprocess.CompletedProcess(
            args=["x"], returncode=0,
            stdout=probe._MARKER + json.dumps(payload), stderr="")
    return run


# ── 경로 해석 ──────────────────────────────────────────────

def test_dotted_uses_catalog_field_name_not_env_prefix():
    """env 접두와 그룹명이 다른 실사례(`API_PORT` → `server.port`)를 덮는다."""
    knob = _knob("API_PORT", group_key="server", field_name="port", type="int")
    assert validate._dotted_of(knob) == "server.port"


def test_dotted_top_level_has_no_prefix():
    knob = _knob("CHECKPOINT_BACKEND", group_key="general", field_name="checkpoint_backend", type="str")
    assert validate._dotted_of(knob) == "checkpoint_backend"


def test_every_catalog_knob_path_resolves():
    """★ 335필드 전건의 경로가 실제 에코에 존재해야 한다.

    숫자를 단언하지 않고 **미적중 0**만 단언한다(설정이 늘어도 낡지 않는다).
    """
    echo = probe.echo_config()
    assert echo.ok, f"기준 에코 실패: {echo.error_type}"
    missed = [k.env_key for k in catalog.load_knobs() if validate._dotted_of(k) not in echo.config]
    assert missed == [], f"경로 미적중: {missed[:10]}"


# ── 정규화 ─────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    (True, "true"), (False, "false"), (1, "1"), ("ON", "true"), ("off", "false"),
    (3, "3.0"), ("Fabrix", "fabrix"),
])
def test_norm_equivalences(a, b):
    assert validate._norm(a) == validate._norm(b)


def test_norm_distinguishes_real_differences():
    assert validate._norm("3") != validate._norm("7")


# ── L3 주입 실효성 ────────────────────────────────────────

def test_injection_clean_when_value_takes_effect():
    runner = _echo_runner(lambda env: {"text2sql.flag_a": env.get("FLAG_A")})
    assert validate.check_injection([_knob()], base_env={}, runner=runner) == []


def test_injection_detects_shadowed_key():
    """주입해도 에코가 안 바뀌면 가림이다 — *"바꿨는데 왜 안 바뀌지"* 의 정체."""
    runner = _echo_runner(lambda env: {"text2sql.flag_a": "false"})  # 주입 무시
    found = validate.check_injection([_knob()], base_env={}, runner=runner)
    assert len(found) == 1 and found[0].env_key == "FLAG_A"
    assert found[0].injected == "true" and found[0].effective == "false"


def test_injection_attributes_os_env_source():
    runner = _echo_runner(lambda env: {"text2sql.flag_a": "false"})
    found = validate.check_injection([_knob()], base_env={"FLAG_A": "false"}, runner=runner)
    assert found[0].suspected_source == "os_env"


def test_injection_skips_when_boot_fails():
    """기동 실패는 L2 소관이지 가림이 아니다."""
    assert validate.check_injection([_knob()], base_env={}, runner=_fail_runner()) == []


def test_injection_skips_unknown_path_instead_of_guessing():
    """경로를 못 찾으면 판정하지 않는다(추측 금지)."""
    runner = _echo_runner(lambda env: {"other.key": 1})
    assert validate.check_injection([_knob()], base_env={}, runner=runner) == []


# ── L2 기동 안전성 ────────────────────────────────────────

def test_boot_records_success():
    runner = _echo_runner(lambda env: {"text2sql.flag_a": True})
    found = validate.check_boot([_knob()], runner=runner)
    assert len(found) == 1 and found[0].ok


def test_boot_preserves_validation_error_text():
    found = validate.check_boot([_knob()], runner=_fail_runner(message="candidate_count 오류"))
    assert found[0].ok is False
    assert found[0].error_type == "ValidationError"
    assert "candidate_count" in found[0].error


def test_boot_exhaustive_tries_every_enum_choice():
    knob = _knob("SEL", type="str", enum_choices=["a", "b", "c"])
    runner = _echo_runner(lambda env: {"text2sql.flag_a": 1})
    assert len(validate.check_boot([knob], exhaustive=True, runner=runner)) == 3
    assert len(validate.check_boot([knob], exhaustive=False, runner=runner)) == 1



@pytest.mark.parametrize(("knob_type", "expected"), [
    ("tristate", ("true", "false")),                       # 미입력(None)은 표본이 아니다
    ("json_list", ("[]", '["bench-probe-sentinel"]')),     # `.env` 의 list 는 JSON 배열
])
def test_boot_samples_match_the_field_format(knob_type, expected):
    """`x` 를 넣으면 설정이 **올바르게** 거부해 허위 「기동 실패」가 된다(2026-09-21 6건)."""
    knob = _knob("K", type=knob_type, default=None)
    assert validate._sample_values(knob, exhaustive=True) == expected
    assert validate._sample_values(knob, exhaustive=False)[0] in expected


def _json_object_runner():
    """JSON 객체만 받는 문자열 설정(`LLM_FABRIX_*_CONFIG` 모양)을 흉내 낸다."""
    def run(env, timeout):
        value = env.get("K", "")
        if value and not value.startswith("{"):
            payload = {"ok": False, "error_type": "ValidationError",
                       "error": "Value error, Expecting value: line 1 column 1 (char 0)"}
        else:
            payload = {"ok": True, "config": {"text2sql.flag_a": value}}
        return subprocess.CompletedProcess(args=["x"], returncode=0,
                                           stdout=probe._MARKER + json.dumps(payload), stderr="")
    return run


def test_boot_retries_json_string_with_an_object():
    """JSON 을 요구하는 문자열은 타입으로 드러나지 않는다 — 파싱 오류면 `{}` 로 다시 본다."""
    found = validate.check_boot([_knob("K", type="string", default="")],
                                runner=_json_object_runner())
    assert [(f.value, f.ok) for f in found] == [("{}", True)]


def test_boot_keeps_real_string_failures():
    """JSON 파싱 오류가 아닌 거부는 그대로 실패다 — 재시도는 그 한 모양에만 건다."""
    found = validate.check_boot([_knob("K", type="string", default="")],
                                runner=_fail_runner(message="값이 너무 짧다"))
    assert [(f.value, f.ok) for f in found] == [("x", False)]


# ── L4 소비 실증 ──────────────────────────────────────────

def _consumption(config_for, knobs=None):
    runner = _echo_runner(config_for)
    base = probe.echo_config(base_env={}, runner=runner)
    return validate.check_consumption(knobs or [_knob()], baseline=base, base_env={}, runner=runner)


def test_consumption_changed():
    found = _consumption(lambda env: {"text2sql.flag_a": env.get("FLAG_A", "false")})
    assert found[0].verdict == "changed"


def test_consumption_unchanged_is_knob_illusion_candidate():
    found = _consumption(lambda env: {"text2sql.flag_a": "false"})  # 무엇을 넣든 그대로
    assert found[0].verdict == "unchanged"


def test_consumption_unchecked_when_no_alternative_value():
    knob = _knob("ODD", type="list", default=None)
    found = _consumption(lambda env: {"text2sql.flag_a": 1}, knobs=[knob])
    assert found[0].verdict == "unchecked" and "만들 수 없다" in found[0].detail


def test_consumption_unchecked_when_boot_fails():
    runner = _fail_runner()
    base = probe.EchoResult(ok=True, config={"text2sql.flag_a": False})
    found = validate.check_consumption([_knob()], baseline=base, base_env={}, runner=runner)
    assert found[0].verdict == "unchecked" and "기동 실패" in found[0].detail


def test_consumption_all_unchecked_when_baseline_unreadable():
    base = probe.EchoResult(ok=False, error_type="X")
    found = validate.check_consumption([_knob()], baseline=base, base_env={}, runner=_fail_runner())
    assert found[0].verdict == "unchecked"


def test_consumption_ignores_nondeterministic_keys():
    """랜덤 필드(jwt_secret 등)가 모든 키를 '변함'으로 만들지 않아야 한다."""
    counter = {"n": 0}

    def config_for(env):
        counter["n"] += 1
        return {"text2sql.flag_a": "false", "auth.jwt_secret": f"rnd{counter['n']}"}

    runner = _echo_runner(config_for)
    base = probe.echo_config(base_env={}, runner=runner)
    found = validate.check_consumption(
        [_knob()], baseline=base, nondeterministic=frozenset({"auth.jwt_secret"}),
        base_env={}, runner=runner)
    assert found[0].verdict == "unchanged"


# ── UNCONSUMED 대조 ───────────────────────────────────────

def test_unconsumed_comparison_three_buckets(monkeypatch):
    monkeypatch.setattr(catalog.sc, "UNCONSUMED_KEYS", frozenset({"DECLARED_UNUSED", "DECLARED_STALE"}))
    findings = [
        validate.ConsumptionFinding("DECLARED_UNUSED", "unchanged"),   # 일치
        validate.ConsumptionFinding("UNDECLARED_UNUSED", "unchanged"), # 목록이 놓침
        validate.ConsumptionFinding("DECLARED_STALE", "changed"),      # 목록이 낡음
        validate.ConsumptionFinding("NORMAL", "changed"),
    ]
    cmp = validate.compare_with_unconsumed(findings)
    assert cmp.agreed_unconsumed == ("DECLARED_UNUSED",)
    assert cmp.list_missed == ("UNDECLARED_UNUSED",)
    assert cmp.list_stale == ("DECLARED_STALE",)
