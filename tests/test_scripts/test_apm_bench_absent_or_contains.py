"""APM 벤치 기대 인자 `{absent_or_contains: [이름…]}` 형(plans/164 W7-A).

- 판정 3경우: 미지정 = 통과 · 이름 포함 = 통과 · 불포함 = `arg_missing`(환각으로 세지 않음)
- 스키마 검증: str_list 인자에만 · 비지 않은 문자열 목록만 · 그 밖 매핑은 위반
- 가짜 LLM 정답 계획은 이 형의 인자를 붙이지 않는다(보기 기본값)
- 실 시나리오 `APM-INSTTREND-hourly`가 이 형을 쓴다

순수 로직이다(네트워크·LLM 0).
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.apm_bench import judge as jd
from scripts.apm_bench import scenarios as sc
from scripts.apm_bench.fake_llm import plan_from_expect
from tests.test_scripts.test_apm_bench_judge import _judge, _plan, synth

VIEW = "apm.instance_trend"
FORM = {"absent_or_contains": ["service_time", "response_time_avg_ms"]}


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


def _scenario(args: dict[str, Any]) -> sc.Scenario:
    expect = sc.Expect(agents=("apm_query",), views=(VIEW,), target="host:1",
                       window="range:30", args=args)
    return sc.Scenario("T-AOC", ("COV-DBM-INSTANCE",), "dev",
                       (sc.Turn("{{host_1}} 최근 30분 시간당 응답시간", expect),))


def _with_metrics(registry: sc.RegistryInfo, metrics: Any) -> tuple[sc.Scenario, dict]:
    s = _scenario({"metrics": FORM, "interval_minute": 60})
    r = synth(s, 0, registry)
    view_args: dict[str, Any] = {"interval_minute": 60}
    if metrics is not None:
        view_args["metrics"] = metrics
    _plan(r, view_args={VIEW: view_args})
    return s, r


def test_absent_passes(registry) -> None:
    s, r = _with_metrics(registry, None)
    v = _judge(s, r, registry)
    assert v["axes"]["args"] == "pass" and v["turn_pass"] is True


@pytest.mark.parametrize("metrics", [["response_time_avg_ms"], ["service_time", "service_count"],
                                     ["SERVICE_TIME"]])
def test_containing_passes(registry, metrics) -> None:
    s, r = _with_metrics(registry, metrics)
    v = _judge(s, r, registry)
    assert v["axes"]["args"] == "pass", v["axis_detail"]["args"]
    assert v["axis_detail"]["args"]["hallucinated"] == []


def test_not_containing_fails_as_missing(registry) -> None:
    s, r = _with_metrics(registry, ["heap_used"])
    v = _judge(s, r, registry)
    detail = v["axis_detail"]["args"]
    assert v["axes"]["args"] == "fail" and "arg_missing" in v["categories"]
    assert [m["arg"] for m in detail["missing"]] == ["metrics"]
    assert detail["missing"][0]["observed"] == ["heap_used"]
    assert detail["hallucinated"] == [] and detail["sanitize_dropped"] == []


def test_not_containing_after_sanitize_is_not_a_drop(registry) -> None:
    """원출력에도 없었다면(정제 뒤에 붙음) 정제가 버린 것이 아니다."""
    s, r = _with_metrics(registry, ["heap_used"])
    r["observed"]["plan_raw"][0]["view_args"] = {VIEW: {"interval_minute": 60}}
    v = _judge(s, r, registry)
    assert "sanitize_drop" not in v["categories"] and "arg_missing" in v["categories"]


def test_other_values_keep_old_rule(registry) -> None:
    """이 형이 아닌 기대 값은 종전 판정 — 붙이지 않으면 arg_missing."""
    s = _scenario({"metrics": sc.ANY_VALUE, "interval_minute": 60})
    r = synth(s, 0, registry)
    _plan(r, view_args={VIEW: {"interval_minute": 60}})
    assert "arg_missing" in _judge(s, r, registry)["categories"]


@pytest.mark.parametrize(("value", "ok"), [
    (FORM, True),
    ({"absent_or_contains": []}, False),
    ({"absent_or_contains": "service_time"}, False),
    ({"absent_or_contains": ["service_time"], "x": 1}, False),
    ({"contains": ["service_time"]}, False),
])
def test_schema_validation(registry, value, ok) -> None:
    issues = sc.check_registry([_scenario({"metrics": value, "interval_minute": 60})], registry)
    assert (not [i for i in issues if i.rule == "G-arg-value"]) is ok, issues


def test_schema_rejects_non_list_arg(registry) -> None:
    issues = sc.check_registry([_scenario({"interval_minute": FORM})], registry)
    assert any(i.rule == "G-arg-value" and "str_list" in i.message for i in issues)


def test_fake_llm_plan_omits_the_arg(registry) -> None:
    s = _scenario({"metrics": FORM, "interval_minute": 60})
    plan = plan_from_expect(s.turns[0].expect, "q", mapping={"host_1": "was-host01"},
                            views=registry.views)
    assert plan["tasks"][0]["view_args"] == {VIEW: {"interval_minute": 60}}


def test_real_scenario_uses_the_form() -> None:
    hourly = {s.id: s for s in sc.load_scenarios()}["APM-INSTTREND-hourly"].turns[0].expect
    assert sc.absent_or_contains(hourly.args["metrics"]) == ("service_time",
                                                             "response_time_avg_ms")
    assert jd.absent_or_contains is sc.absent_or_contains
