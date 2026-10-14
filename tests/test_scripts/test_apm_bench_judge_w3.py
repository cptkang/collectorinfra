"""APM 조회 벤치 — 판정 집계 교정(plans/164 W3 · D-323 ① 턴 통과·분모 불변).

- 오류 감사 줄의 호출 단계 구분(보기 / 대상 해석 / 판별 불가) — 해석 단계 `instance_unresolved`는
  `gw_error`가 아니라 정보성 `resolve_miss`, 모호하면 현행대로 `gw_error`(G-5 (a))
- `primary_failure_categories`(실패 일반 턴만) 추가 · `primary_categories` 의미 불변
- 오류 호출 교차표 · target 실패 하위 사유 집계 · `report.md` 노출
- `build_observed`가 처리기 보기 호출 실패(보기 · 오류 코드)와 해석 기록 건수를 싣는다
- **턴 통과·5축은 바뀌지 않는다** — 같은 레코드에서 새 칸을 지운 판정과 비교한다
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from scripts.apm_bench import driver as dv
from scripts.apm_bench import judge as jd
from scripts.apm_bench import scenarios as sc
from tests.test_scripts.test_apm_bench_judge import _base, _judge, synth

RESOLVE_TOOL = "apm_instance_map"


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


def _err(tool: str, code: str) -> dict[str, Any]:
    return {"tool": tool, "args": {}, "window": None, "rows": None, "error": code,
            "sources": []}


def _biz(registry: sc.RegistryInfo) -> tuple[sc.Scenario, dict[str, Any]]:
    """업무명 대상 턴 — 해석(업무명 간선 정합)이 인스턴스 없는 서버를 만난 모양."""
    s, r = _base(registry, prompt="{{biz_1}} 최근 30분 응답시간", target="biz:1")
    r["observed"]["calls"].insert(0, _err(RESOLVE_TOOL, jd.INSTANCE_UNRESOLVED))
    r["observed"]["view_failures"] = []
    return s, r


def _same_pass(s: sc.Scenario, r: dict[str, Any], registry: sc.RegistryInfo) -> None:
    """새 칸(`view_failures`)을 지운 판정과 턴 통과·5축이 같다."""
    old = copy.deepcopy(r)
    old["observed"].pop("view_failures", None)
    new_v, old_v = _judge(s, r, registry), _judge(s, old, registry)
    assert new_v["turn_pass"] == old_v["turn_pass"]
    assert new_v["axes"] == old_v["axes"]


# --- 호출 단계 ------------------------------------------------------------------------------


def test_resolve_stage_instance_unresolved_is_not_gw_error(registry) -> None:
    s, r = _biz(registry)
    v = _judge(s, r, registry)
    res = v["axis_detail"]["results"]
    assert "gw_error" not in v["categories"]
    assert res["resolve_miss"] == 1 and res["errors"] == [jd.INSTANCE_UNRESOLVED]
    assert res["error_stages"] == [{"tool": RESOLVE_TOOL, "code": jd.INSTANCE_UNRESOLVED,
                                    "stage": jd.STAGE_RESOLVE}]
    assert v["turn_pass"] is True
    _same_pass(s, r, registry)


def test_old_record_without_view_failures_keeps_gw_error(registry) -> None:
    """칸이 없으면(옛 레코드 · 처리기 결과 캡처 없음) 판별을 포기한다 — 현행대로."""
    s, r = _biz(registry)
    r["observed"].pop("view_failures")
    v = _judge(s, r, registry)
    assert v["categories"][0] == "gw_error"
    assert v["axis_detail"]["results"]["resolve_miss"] == 0
    assert v["axis_detail"]["results"]["error_stages"][0]["stage"] == jd.STAGE_UNKNOWN


def test_view_call_failure_stays_gw_error(registry) -> None:
    """보기 호출이 받은 `instance_unresolved`는 대상을 못 찾은 정당한 실패다(§2.1 주의)."""
    s, r = _base(registry)
    tool = registry.views["apm.app_health"].tool
    r["observed"]["calls"][0].update(error=jd.INSTANCE_UNRESOLVED, rows=None)
    r["observed"]["view_failures"] = [{"view": "apm.app_health",
                                       "code": jd.INSTANCE_UNRESOLVED}]
    v = _judge(s, r, registry)
    assert v["categories"][0] == "gw_error"
    assert v["axis_detail"]["results"]["error_stages"] == [
        {"tool": tool, "code": jd.INSTANCE_UNRESOLVED, "stage": jd.STAGE_VIEW}]
    _same_pass(s, r, registry)


def test_instances_view_failure_matched_by_count(registry) -> None:
    """같은 도구(인스턴스 목록)를 보기·해석이 함께 써도 건수로 가른다 — 보기 1 · 해석 1."""
    s, r = _biz(registry)
    r["observed"]["calls"].insert(0, _err(RESOLVE_TOOL, jd.INSTANCE_UNRESOLVED))
    r["observed"]["view_failures"] = [{"view": jd.INSTANCES_VIEW,
                                       "code": jd.INSTANCE_UNRESOLVED}]
    v = _judge(s, r, registry)
    stages = [x["stage"] for x in v["axis_detail"]["results"]["error_stages"]]
    assert sorted(stages) == [jd.STAGE_RESOLVE, jd.STAGE_VIEW]
    assert v["categories"][0] == "gw_error"
    assert v["axis_detail"]["results"]["resolve_miss"] == 1


@pytest.mark.parametrize("case", ["no_targets", "other_tool", "other_code", "leftover",
                                  "no_registry"])
def test_ambiguous_stays_gw_error(registry, case: str) -> None:
    """맞추기가 모호하면 보수적으로 `gw_error`(G-5 (a))."""
    s, r = _biz(registry)
    reg: sc.RegistryInfo | None = registry
    if case == "no_targets":  # 대상 해석이 없던 턴 — 해석 호출일 수 없다
        r["observed"]["targets"] = []
    elif case == "other_tool":
        r["observed"]["calls"][0]["tool"] = "apm_app_health"
    elif case == "other_code":
        r["observed"]["calls"][0]["error"] = "apm_api_error"
    elif case == "leftover":  # 보기 실패가 감사 줄보다 많다 — 건수 맞추기 불성립
        r["observed"]["view_failures"] = [{"view": "apm.app_health",
                                           "code": "source_unavailable"}]
    else:
        reg = None
    stages = jd.error_stages(r["observed"], reg)
    assert [x["stage"] for x in stages] == [jd.STAGE_UNKNOWN]
    details, cats = jd.judge_results(r, reg)
    assert cats[0] == "gw_error" and details["resolve_miss"] == 0
    if reg is not None:
        _same_pass(s, r, registry)


def test_non_code_failures_are_ignored(registry) -> None:
    """코드가 없는 처리기 실패(「대상 해석 0건」 등 게이트웨이 호출이 아닌 것)는 건수에서 뺀다."""
    s, r = _biz(registry)
    r["observed"]["view_failures"] = [{"view": "apm.app_health", "code": None},
                                      {"view": None, "code": None}]
    assert jd.error_stages(r["observed"], registry)[0]["stage"] == jd.STAGE_RESOLVE


# --- target 하위 사유 -----------------------------------------------------------------------


def test_target_fail_reasons_enum(registry) -> None:
    cases: dict[str, Any] = {}
    s, r = _base(registry)
    r["observed"]["targets"][0].update(resolved=False, selected=[])
    cases["unresolved"] = (s, r)
    s, r = _base(registry)
    r["observed"]["targets"][0]["kind"] = "business"
    cases["kind_mismatch"] = (s, r)
    s, r = _base(registry)
    r["observed"]["targets"] = []
    r["observed"]["untargeted"] = True
    cases["other_target"] = (s, r)
    s, r = _base(registry)
    r["observed"]["targets"] = []
    cases["dropped"] = (s, r)
    s, r = _base(registry, prompt="최근 30분 응답시간", target="none")
    r["observed"]["untargeted"] = True
    cases["unexpected_target"] = (s, r)
    s, r = _base(registry, prompt="최근 30분 응답시간 상위", target="untargeted")
    r["observed"]["untargeted"] = False
    r["observed"]["targets"] = [{"text": "x", "kind": "instance", "resolved": True,
                                 "selected": ["x"]}]
    cases["not_untargeted"] = (s, r)
    for reason, (s, r) in cases.items():
        v = _judge(s, r, registry)
        assert v["axes"]["target"] == "fail", reason
        assert jd.target_fail_reason(v["axis_detail"]["target"]) == reason
    assert set(cases) | {"ref_not_called", "other"} == set(jd.TARGET_FAIL_REASONS)
    assert jd.target_fail_reason({"expected": "ref", "reference_views": ["v"],
                                  "called": False}) == "ref_not_called"
    assert jd.target_fail_reason({"expected": "inst:1"}) == "other"


# --- 집계 -----------------------------------------------------------------------------------


def _run(registry: sc.RegistryInfo) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    # 1) 통과 턴인데 분류(empty_result)가 붙음 — 대표(통과 포함)에만 센다
    s, r = _base(registry)
    r["observed"]["calls"][0]["rows"] = 0
    r["verdict"] = _judge(s, r, registry)
    recs.append(r)
    # 2) 해석 단계 instance_unresolved 만 있는 통과 턴 — gw_error 아님 · resolve_miss
    s, r = _biz(registry)
    r["verdict"] = _judge(s, r, registry)
    recs.append(r)
    # 3) 실패 턴 — 보기 호출 오류 + target 미해석
    s, r = _base(registry)
    r["observed"]["targets"][0].update(resolved=False, selected=[])
    r["observed"]["calls"][0].update(error=jd.INSTANCE_UNRESOLVED, rows=None)
    r["observed"]["view_failures"] = [{"view": "apm.app_health",
                                       "code": jd.INSTANCE_UNRESOLVED}]
    r["verdict"] = _judge(s, r, registry)
    recs.append(r)
    for i, rec in enumerate(recs):
        rec["scenario_id"] = f"T-{i}"
    return recs


def test_summarize_new_keys(registry) -> None:
    recs = _run(registry)
    assert [r["verdict"]["turn_pass"] for r in recs] == [True, True, False]
    out = jd.summarize(recs)
    assert out["primary_categories"] == {"empty_result": 1, "gw_error": 1}  # 종전 의미 그대로
    assert out["primary_failure_categories"] == {"gw_error": 1}
    tool = registry.views["apm.app_health"].tool
    assert out["error_calls"] == [
        {"tool": RESOLVE_TOOL, "code": jd.INSTANCE_UNRESOLVED, "stage": jd.STAGE_RESOLVE,
         "count": 1},
        {"tool": tool, "code": jd.INSTANCE_UNRESOLVED, "stage": jd.STAGE_VIEW, "count": 1}]
    assert out["resolve_miss"] == {"turns": 1, "calls": 1}
    assert out["target_fail_reasons"] == {"unresolved": 1}
    assert out["turn"] == {"pass": 2, "total": 3, "accuracy": round(2 / 3, 4)}


def test_summarize_empty_run_has_new_keys() -> None:
    out = jd.summarize([])
    assert out["primary_failure_categories"] == {} and out["error_calls"] == []
    assert out["resolve_miss"] == {"turns": 0, "calls": 0} and out["target_fail_reasons"] == {}


def test_report_shows_new_keys(registry) -> None:
    from scripts.apm_bench.__main__ import render_report

    text = render_report(_run(registry), {"run_id": "r", "env": "e1"}, term_lines=[],
                         missing=[], probe_rows=[])
    assert "| 분류 | 대표(실패 턴) | 대표(통과 턴 포함) | 전체 |" in text
    assert "| gw_error | 1 | 1 | 1 |" in text and "| empty_result | 0 | 1 | 1 |" in text
    assert "target 실패 하위 사유: {'unresolved': 1}" in text
    assert "resolve_miss` · gw_error 아님): 턴 1 · 호출 1" in text
    assert f"- {RESOLVE_TOOL} · {jd.INSTANCE_UNRESOLVED} · resolve 1" in text


def test_self_consistency_unchanged_with_empty_view_failures(registry) -> None:
    """실 시나리오 전 턴 — 빈 `view_failures`를 더해도 판정(통과·축·분류)이 같다."""
    for scenario in sc.load_scenarios():
        for i in range(len(scenario.turns)):
            r = synth(scenario, i, registry)
            base = jd.judge_turn(scenario, i, copy.deepcopy(r), registry)
            r["observed"]["view_failures"] = []
            new = jd.judge_turn(scenario, i, r, registry)
            assert (new["turn_pass"], new["axes"], new["categories"]) == (
                base["turn_pass"], base["axes"], base["categories"]), (scenario.id, i)


# --- 드라이버 관측 조립 -------------------------------------------------------------------------


def _result(meta: dict[str, Any]) -> dict[str, Any]:
    return {"kind": "investigation_result", "thread_id": "th", "task": None, "meta": meta,
            "answer_lines": []}


def test_build_observed_carries_view_failures_and_resolve_counts() -> None:
    meta = {"targets": {"texts": [{"text": "주문", "kind": "business", "instances": 0,
                                   "searched": ["a", "b"], "suggestions": [],
                                   "failures": ["web-host01 인스턴스 정합 실패 — x"]}],
                        "instances": []},
            "failures": [
                {"view": "apm.app_health", "hostname": "web-host01",
                 "reason": "instance_unresolved: web-host01 인스턴스 없음"},
                {"view": "apm.app_health", "hostname": None, "reason": "대상 '주문' 해석 0건"},
                {"view": None, "hostname": None, "reason": "Upper: 코드 모양 아님"}]}
    obs = type("Obs", (), {"response": "", "node_elapsed_ms": {}, "wall_ms": 1, "error": None})()
    observed: dict[str, Any] = dict(dv.build_observed(obs=obs, done={}, captures=[_result(meta)],
                                                      audit=[]))
    assert observed["view_failures"] == [
        {"view": "apm.app_health", "code": "instance_unresolved"},
        {"view": "apm.app_health", "code": None}, {"view": None, "code": None}]
    target = observed["targets"][0]
    assert target["searched"] == 2 and target["failures"] == 1
    # 사유 원문(서버 이름이 섞인 자유 문장)은 싣지 않는다
    assert "web-host01" not in repr(observed["view_failures"])
    # 처리기 결과 캡처가 없으면 칸을 두지 않는다(판별 포기 = 현행)
    assert "view_failures" not in dv.build_observed(obs=obs, done={}, captures=[], audit=[])
