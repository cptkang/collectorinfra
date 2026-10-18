"""APM 조회 벤치 — 판정 5축 · 분류 13종 · 예문 복사 지목 · 집계 · 예문 커버리지 절.

plans/152 W3 · D-323.

순수 로직이다(네트워크·LLM 0). 레지스트리는 본체 로더로 실 파일을 읽고(D-053), 자기 일관성 시험은
실 시나리오 파일 전 턴에 「기대값 그대로의 관측」을 합성해 판정이 전부 통과하는지 본다.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from scripts.apm_bench import SCENARIOS_PATH
from scripts.apm_bench import judge as jd
from scripts.apm_bench import scenarios as sc

KST = timezone(timedelta(hours=9))
ANCHOR = datetime(2026, 10, 12, 14, 37, tzinfo=KST)
SENT = ANCHOR.isoformat()
MIDNIGHT = ANCHOR.replace(hour=0, minute=0)


@pytest.fixture(scope="module")
def registry() -> sc.RegistryInfo:
    return sc.load_registry_info()


@pytest.fixture(scope="module")
def examples(registry: sc.RegistryInfo) -> dict[str, tuple[jd.ParsedExample, ...]]:
    return jd.example_table(registry)


@pytest.fixture(scope="module")
def real() -> list[sc.Scenario]:
    return sc.load_scenarios(SCENARIOS_PATH)


# --- 합성기 ---------------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _win(start: datetime, end: datetime) -> dict[str, Any]:
    return {"start": _iso(start), "end": _iso(end),
            "minutes": max(1, round((end - start).total_seconds() / 60))}


def _call_window(window: str) -> dict[str, Any] | None:
    kind, _, arg = window.partition(":")
    hour = ANCHOR.replace(minute=0)
    if kind == "range":
        return _win(ANCHOR - timedelta(minutes=int(arg)), ANCHOR + timedelta(seconds=20))
    if kind == "hourly":
        if arg == "today":
            return _win(MIDNIGHT, ANCHOR)
        if arg == "yesterday":
            return _win(MIDNIGHT - timedelta(days=1), MIDNIGHT)
        return _win(hour - timedelta(minutes=int(arg)), hour)
    if kind == "compare":
        return {"current": _win(MIDNIGHT, ANCHOR),
                "baseline": _win(MIDNIGHT - timedelta(days=1), ANCHOR - timedelta(days=1))}
    return None


def _time_resolution(window: str) -> dict[str, Any]:
    kind = window.partition(":")[0]
    metric: dict[str, Any] = {"start": _iso(MIDNIGHT), "end": _iso(ANCHOR), "grain": "hour",
                              "source": "default" if kind in ("none", "default", "current")
                              else "rule", "notes": []}
    if kind == "compare":
        metric["notes"] = ["multiple_periods"]
    return {"version": 1, "anchor_at": SENT, "metric": metric, "event": metric,
            "clarify": None, "present": kind == "current"}


_KIND = {"inst": "instance", "inst_w": "instance", "biz": "business", "svc": "service",
         "host": "auto"}


def _values(scenario: sc.Scenario) -> dict[str, str]:
    names: list[str] = []
    for t in scenario.turns:
        names += sc.placeholders_of(t.prompt)
        names += [n for v in t.expect.args.values() if isinstance(v, str)
                  for n in sc.placeholders_of(v)]
        name = jd.target_placeholder(t.expect.target)
        if name:
            names.append(name)
    return {n: f"zz{n.replace('_', '')}q" for n in names}


def _target_texts(scenario: sc.Scenario, idx: int, values: dict[str, str],
                  registry: sc.RegistryInfo) -> list[dict[str, Any]]:
    target = scenario.turns[idx].expect.target
    if target == "ref":
        if any(registry.views[v].target == "reference" for v in scenario.turns[idx].expect.views):
            return []
        target = next(scenario.turns[j].expect.target for j in range(idx - 1, -1, -1)
                      if jd.target_placeholder(scenario.turns[j].expect.target))
    name = jd.target_placeholder(target)
    if not name:
        return []
    family = "inst_w" if target == "inst_w" else target.split(":", 1)[0]
    value = values[name]
    return [{"text": value, "kind": _KIND[family], "resolved": True, "selected": [value]}]


def synth(scenario: sc.Scenario, idx: int, registry: sc.RegistryInfo) -> dict[str, Any]:
    """기대값 그대로의 관측을 가진 레코드."""
    e = scenario.turns[idx].expect
    values = _values(scenario)
    args_by_view: dict[str, dict[str, Any]] = {}
    for name, value in e.args.items():
        owner = jd._arg_owner(name, e.views, registry) or e.views[0]
        if sc.absent_or_contains(value) is not None:
            continue  # 「안 붙였으면 통과」형 — 기대값 그대로의 관측 = 붙이지 않음
        if value == sc.ANY_VALUE:
            value = ["x"]
        elif isinstance(value, str):
            value = sc.fill_placeholders(value, values)
        args_by_view.setdefault(owner, {})[name] = value
    tasks = ([{"task_id": "t1", "agent": "apm_query", "views": list(e.views),
               "view_args": args_by_view, "sources": list(e.apm_sources)}]
             if "apm_query" in e.agents else [])
    tasks += [{"task_id": f"t{i + 2}", "agent": a} for i, a in enumerate(e.agents)
              if a != "apm_query"]
    asks = bool(e.clarify or e.refusal)
    calls = [] if asks or "apm_query" not in e.agents else [
        {"tool": registry.views[v].tool, "args": {}, "window": _call_window(e.window),
         "rows": 3, "error": None, "sources": list(e.apm_sources)} for v in e.views]
    observed: dict[str, Any] = {
        "agents": list(e.agents), "plan_raw": copy.deepcopy(tasks), "plan_final": tasks,
        "sanitize_changes": [], "reselect": None,
        "targets": _target_texts(scenario, idx, values, registry),
        "untargeted": e.target == "untargeted", "time_resolution": _time_resolution(e.window),
        "calls": calls, "answer_lines": [], "disclosures": [],
        "clarify_kind": e.clarify, "refused": bool(e.refusal), "response": "응답",
        "apm_called": bool(calls), "error": None,
    }
    return {"v": 1, "scenario_id": scenario.id, "turn": idx, "split": scenario.split,
            "covers": list(scenario.covers),
            "prompt": sc.fill_placeholders(scenario.turns[idx].prompt, values),
            "placeholders": values, "expect": {"agents": list(e.agents),
                                               "clarify": e.clarify, "refusal": e.refusal},
            "sent_at": SENT, "observed": observed}


def _one(prompt: str, **expect: Any) -> sc.Scenario:
    base: dict[str, Any] = {"agents": ("apm_query",), "views": ("apm.app_health",),
                            "target": "inst:1", "window": "range:30"}
    base.update(expect)
    return sc.Scenario("T-1", ("COV-X",), "dev", (sc.Turn(prompt, sc.Expect(**base)),))


def _plan(record: dict[str, Any], **fields: Any) -> None:
    """분해 원출력·디스패치 task 를 함께 바꾼다(정제가 버린 것이 아닌 경우)."""
    for key in ("plan_raw", "plan_final"):
        record["observed"][key][0].update(copy.deepcopy(fields))


def _judge(scenario: sc.Scenario, record: dict[str, Any], registry: sc.RegistryInfo,
           idx: int = 0) -> dict[str, Any]:
    record["verdict"] = jd.judge_turn(scenario, idx, record, registry)
    return record["verdict"]


# --- 예문 파서 ------------------------------------------------------------------------------


def test_all_registry_examples_parse(registry, examples) -> None:
    """레지스트리 F1 예문 50개 전부가 파싱되고, 인자 키는 그 보기 선언에 있다."""
    assert sum(len(v) for v in examples.values()) == 50  # plans/164 W7-A instance_trend 1
    assert sum(1 for v in examples.values() if v) == 28
    for view_id, exs in examples.items():
        names = {a.name for a in registry.views[view_id].args}
        for ex in exs:
            assert set(ex.args) <= names, (view_id, ex.index, ex.args)
            assert ex.question


def test_parse_example_forms() -> None:
    ex = jd.parse_example("「web01 fatal 이벤트만」 → level=fatal · level_mode=exact",
                          "apm.events", 1)
    assert ex.args == {"level": "fatal", "level_mode": "exact"}
    assert ex.question == "web01 fatal 이벤트만"
    ex = jd.parse_example("(느린 트랜잭션 목록에 이어) 「두 번째 트랜잭션 프로파일 보여줘」"
                          " → ref=2")
    assert ex.args == {"ref": 2} and ex.context == "느린 트랜잭션 목록에 이어"
    ex = jd.parse_example("「오늘 fatal 난 WAS 전부」 → level=fatal · level_mode=exact · full=true")
    assert ex.args["full"] is True
    assert jd.parse_example("「주문 서비스 지금 상태」 → targets에 「주문」").targets == ("주문",)
    note = jd.parse_example("「서비스별 현재 TPS」(이름 없음 = 전체 서비스)").note
    assert note == "이름 없음 = 전체 서비스"
    assert jd.parse_example("「web01 어제 소스 변경 있었어?」").args == {}
    with pytest.raises(jd.ExampleParseError):
        jd.parse_example("「질문」 → 이상한 조각")
    with pytest.raises(jd.ExampleParseError):
        jd.parse_example("괄호 없는 문장")


# --- 자기 일관성 ---------------------------------------------------------------------------


def test_self_consistency_real_scenarios(real, registry) -> None:
    """실 시나리오 파일 전 턴 — 기대값 그대로의 관측이면 전부 통과하고 분류가 없다."""
    failed = []
    for s in real:
        for i, _turn in enumerate(s.turns):
            v = _judge(s, synth(s, i, registry), registry, i)
            if not v["turn_pass"] or v["categories"]:
                failed.append((s.id, i, v["axes"], v["categories"], v["axis_detail"]))
    assert not failed, failed[:3]


# --- 분류 13종 -------------------------------------------------------------------------------


def _base(registry, prompt="{{inst_1}} 최근 30분 응답시간", **expect):
    s = _one(prompt, **expect)
    return s, synth(s, 0, registry)


def test_source_miss(registry) -> None:
    s, r = _base(registry)
    r["observed"]["agents"] = ["data_query"]
    v = _judge(s, r, registry)
    assert v["axes"]["source"] == "fail" and v["categories"][0] == "source_miss"
    assert v["fewshot_fixable"] is True and {"F2", "F3"} <= set(v["locus"])


def test_view_wrong_and_partial(registry) -> None:
    s, r = _base(registry)
    _plan(r, views=["apm.runtime"])
    v = _judge(s, r, registry)
    assert v["categories"][0] == "view_wrong" and "F1:apm.app_health" in v["locus"]
    s, r = _base(registry)
    _plan(r, views=["apm.app_health", "apm.runtime"])
    v = _judge(s, r, registry)
    assert v["categories"] == ["view_partial"]
    assert v["axis_detail"]["views"]["extra"] == ["apm.runtime"]


def test_view_retry_uses_result_views(registry) -> None:
    """재선택이 있으면 결과 보기가 판정 대상 — 맞게 고쳐졌으면 축은 통과, 분류 `view_retry`만."""
    s, r = _base(registry)
    _plan(r, views=["apm.runtime"])
    r["observed"]["reselect"] = {"happened": True, "result_views": ["apm.app_health"]}
    v = _judge(s, r, registry)
    assert v["axes"]["views"] == "pass" and v["categories"] == ["view_retry"]
    assert v["turn_pass"] is True and {"F1", "F4"} <= set(v["locus"])


def test_arg_missing_and_value_mismatch(registry) -> None:
    s, r = _base(registry, "{{inst_1}} 지난 1시간 느린 트랜잭션 5개", views=("apm.slow_tx",),
                 window="range:60", args={"n": 5})
    _plan(r, view_args={})
    v = _judge(s, r, registry)
    assert v["categories"] == ["arg_missing"] and "F1:apm.slow_tx:args.n" in v["locus"]
    s, r = _base(registry, "{{inst_1}} 지난 1시간 느린 트랜잭션 5개", views=("apm.slow_tx",),
                 window="range:60", args={"n": 5})
    _plan(r, view_args={"apm.slow_tx": {"n": "7"}})
    v = _judge(s, r, registry)
    assert v["axis_detail"]["args"]["missing"][0]["observed"] == "7"


def test_arg_value_normalization_and_star(registry) -> None:
    s, r = _base(registry, "{{inst_1}} 지난 3시간 OutOfMemory 오류", views=("apm.events",),
                 window="range:180", args={"error_type": "OUTOFMEMORY", "record": "error"})
    r["observed"]["plan_final"][0]["view_args"] = {
        "apm.events": {"error_type": "OutOfMemory", "record": "ERROR"}}
    assert _judge(s, r, registry)["axes"]["args"] == "pass"
    s, r = _base(registry, "{{inst_1}} 지난 3시간 CPU 추세 5분 단위", views=("apm.runtime",),
                 window="range:180", args={"metrics": "*", "interval_minute": 5})
    r["observed"]["plan_final"][0]["view_args"] = {"*": {"metrics": ["cpu"]},
                                                   "apm.runtime": {"interval_minute": "5"}}
    assert _judge(s, r, registry)["axes"]["args"] == "pass"


def test_arg_hallucination_without_copy(registry) -> None:
    s, r = _base(registry, "{{inst_1}} 최근 1시간 이벤트", views=("apm.events",), window="range:60")
    r["observed"]["plan_final"][0]["view_args"] = {"apm.events": {"n": 7}}
    v = _judge(s, r, registry)
    assert v["categories"] == ["arg_hallucination"] and v["fewshot_copy"] is None
    assert "F1:apm.events:args.n" in v["locus"]


def test_hallucination_exclusions(registry) -> None:
    """보기 기본값·고정 인자·코드가 채우는 인자는 환각이 아니다."""
    s, r = _base(registry, "지금 응답시간 가장 느린 WAS 5개", views=("apm.ranking",),
                 target="untargeted", window="current", args={"n": 5})
    r["observed"]["plan_final"][0]["view_args"] = {"apm.ranking": {
        "n": 5, "metric": "response_time_avg_ms", "order": "desc", "mode": "ranking",
        "source_ids": ["bank"], "lookback_minutes": 10}}
    v = _judge(s, r, registry)
    assert v["axes"]["args"] == "pass", v["axis_detail"]["args"]
    reasons = {e["arg"]: e["reason"] for e in v["axis_detail"]["args"]["excluded"]}
    assert reasons == {"metric": "view_default", "order": "view_default", "mode": "fixed_arg",
                       "source_ids": "code_filled", "lookback_minutes": "code_filled"}
    # 기본값과 다른 값은 환각
    r["observed"]["plan_final"][0]["view_args"]["apm.ranking"]["order"] = "asc"
    assert "arg_hallucination" in _judge(s, r, registry)["categories"]


def test_fewshot_copy_positive(registry) -> None:
    """질문에 근거 없는 부착 조합이 예문 인자와 같다 → 그 예문 지목(같은 보기 · 최다 겹침)."""
    s, r = _base(registry, "{{inst_1}} 오늘 SQL 실행 통계", views=("apm.sql_stats",),
                 window="hourly:today")
    r["observed"]["plan_final"][0]["view_args"] = {
        "apm.sql_stats": {"n": 5, "sort_by": "responseTime"}}
    v = _judge(s, r, registry)
    assert v["categories"][:2] == ["fewshot_copy", "arg_hallucination"]
    assert v["fewshot_copy"]["view"] == "apm.sql_stats"
    assert v["fewshot_copy"]["example_index"] == 2
    assert "F1:apm.sql_stats:example#2" in v["locus"]


def test_fewshot_copy_negative_when_grounded(registry) -> None:
    """질문에 값 근거(「5개」)가 있으면 복사가 아니다 — 환각(기대 누락)으로만 남는다."""
    s, r = _base(registry, "{{inst_1}} 오늘 SQL 5개만 보여줘", views=("apm.sql_stats",),
                 window="hourly:today")
    r["observed"]["plan_final"][0]["view_args"] = {"apm.sql_stats": {"n": 5}}
    v = _judge(s, r, registry)
    assert v["categories"] == ["arg_hallucination"] and v["fewshot_copy"] is None
    assert v["axis_detail"]["args"]["hallucinated"][0]["grounded"] is True


def test_fewshot_copy_on_failed_required_arg_clarify(registry, real) -> None:
    """필수 인자 되묻기가 정답인데 예문 값(PID 12345)으로 채워 조회 → 되묻기 실패 + 예문 지목."""
    s = next(x for x in real if x.id == "APM-PROC-missing")
    r = synth(s, 0, registry)
    obs = r["observed"]
    obs["clarify_kind"] = None
    obs["plan_final"][0]["view_args"] = {"apm.process": {"process_id": 12345}}
    obs["apm_called"] = True
    v = _judge(s, r, registry)
    assert v["expected_clarify"] and v["turn_pass"] is False
    assert v["fewshot_copy"]["view"] == "apm.process"
    assert v["fewshot_copy"]["example_index"] == 1


def test_source_select(registry) -> None:
    s, r = _base(registry, "공동존 운영 제니퍼에서 {{inst_c}} 최근 30분 응답시간", target="inst:c",
                 apm_sources=("cm_prd",))
    r["observed"]["calls"][0]["sources"] = ["b0"]
    v = _judge(s, r, registry)
    assert v["categories"] == ["source_select"] and v["fewshot_fixable"] is None
    assert "sources:cm_prd" in v["locus"]
    # 「전체」(`*`) 승계는 레지스트리 소스 전부로 편다
    s, r = _base(registry, "{{inst_1}}", apm_sources=("b0", "cm_prd", "cm_stg"))
    for c in r["observed"]["calls"]:
        c["sources"] = []
    r["observed"]["apm_source_scope"] = {"ids": ["*"], "basis": "all"}
    r["observed"]["plan_final"][0]["sources"] = []
    assert _judge(s, r, registry)["axes"]["source"] == "pass"


def test_sanitize_drop_view_arg_and_agent(registry) -> None:
    s, r = _base(registry, "{{inst_1}} 지난 1시간 느린 트랜잭션 5개", views=("apm.slow_tx",),
                 window="range:60", args={"n": 5})
    r["observed"]["plan_final"][0]["views"] = []
    r["observed"]["plan_final"][0]["view_args"] = {}
    v = _judge(s, r, registry)
    assert v["categories"][0] == "sanitize_drop" and v["fewshot_fixable"] is False
    assert "view_wrong" in v["categories"]
    assert v["axis_detail"]["args"]["sanitize_dropped"][0]["arg"] == "n"
    s, r = _base(registry)
    r["observed"]["agents"] = ["data_query"]
    r["observed"]["plan_final"] = [{"task_id": "t1", "agent": "data_query"}]
    v = _judge(s, r, registry)
    assert v["categories"][:2] == ["source_miss", "sanitize_drop"]
    assert v["axis_detail"]["source"]["sanitize_dropped_agent"] is True


def test_target_unresolved_and_wrong(registry) -> None:
    s, r = _base(registry)
    r["observed"]["targets"][0].update(resolved=False, selected=[])
    v = _judge(s, r, registry)
    assert v["categories"] == ["target_unresolved"] and v["fewshot_fixable"] is False
    s, r = _base(registry)
    r["observed"]["targets"] = []
    r["observed"]["untargeted"] = True
    assert _judge(s, r, registry)["categories"] == ["target_wrong"]
    s, r = _base(registry)
    r["observed"]["targets"][0]["kind"] = "business"
    assert _judge(s, r, registry)["categories"] == ["target_wrong"]
    # 값이 호출 인자(해석 인스턴스)에만 있어도 통과
    s, r = _base(registry)
    value = r["placeholders"]["inst_1"]
    r["observed"]["targets"] = []
    r["observed"]["calls"][0]["args"] = {"instance_name": value}
    assert _judge(s, r, registry)["axes"]["target"] == "pass"


def test_target_match_is_exact_or_token_bounded(registry) -> None:
    """대상 일치는 부분 문자열이 아니라 정확 일치(casefold)·토큰 경계다."""
    s, r = _base(registry)
    value = r["placeholders"]["inst_1"]
    r["observed"]["targets"] = []
    r["observed"]["calls"][0]["args"] = {"instance_name": value + "x"}
    assert _judge(s, r, registry)["axes"]["target"] != "pass"
    r["observed"]["calls"][0]["args"] = {"instance_name": value.upper() + ".example.local"}
    assert _judge(s, r, registry)["axes"]["target"] == "pass"


def test_missing_placeholder_value_is_unjudged(registry) -> None:
    """자리표 값이 없어 대상 축을 못 매기면 통과가 아니라 판정 불가다."""
    s, r = _base(registry)
    r["placeholders"] = {}
    v = _judge(s, r, registry)
    assert v["axes"]["target"] == "na" and v["turn_pass"] is False
    assert v["axis_detail"]["unjudged"] == "placeholder_missing:inst_1"


def test_target_none_untargeted_and_ref(registry, real) -> None:
    s, r = _base(registry, "지금 WAS들 응답시간 상태 어때?", target="untargeted", window="default")
    r["observed"]["untargeted"] = False
    assert _judge(s, r, registry)["categories"] == ["target_wrong"]
    s, r = _base(registry, "제니퍼 인스턴스 목록", views=("apm.instances",), target="none",
                 window="none")
    r["observed"]["targets"] = [{"text": "레거시", "kind": "auto", "resolved": False}]
    assert _judge(s, r, registry)["categories"] == ["target_wrong"]
    # 참조 보기 — 그 도구 호출이 실패하면 대상 미해석
    s = next(x for x in real if x.id == "APM-MT-profile")
    r = synth(s, 1, registry)
    r["observed"]["calls"][0]["error"] = "apm_api_error"
    v = _judge(s, r, registry, 1)
    assert v["axes"]["target"] == "fail" and v["categories"][0] == "gw_error"
    assert "target_unresolved" in v["categories"]
    # 대상 승계 — 앞 턴 값이 아닌 다른 대상
    s = next(x for x in real if x.id == "APM-MT-inherit-target")
    r = synth(s, 1, registry)
    r["observed"]["targets"] = [{"text": "다른것", "kind": "instance", "resolved": True,
                                 "selected": ["다른것"]}]
    assert _judge(s, r, registry, 1)["categories"] == ["target_wrong"]


def test_inherited_instance_via_host(registry, real) -> None:
    """「그 서버」 승계 — 앞 턴 인스턴스의 호스트(인벤토리)로 부른 호출은 통과.

    다른 호스트로 부르면 실패다.
    """
    s = next(x for x in real if x.id == "APM-MT-active-n-followup")
    r = synth(s, 1, registry)
    r["placeholders"] = {"inst_2": "api02_main"}
    r["observed"]["targets"] = []
    r["observed"]["calls"] = [{**r["observed"]["calls"][0], "args": {},
                               "hostname": "API-HOST02.example.local"}]
    v = _judge(s, r, registry, 1)
    assert v["axes"]["target"] == "fail" and v["categories"] == ["target_unresolved"]
    r["extra"] = {"inherited_hosts": ["api-host02.example.local"]}
    v = _judge(s, r, registry, 1)
    assert v["axes"]["target"] == "pass"
    assert v["axis_detail"]["target"]["via_host"] == ["API-HOST02.example.local"]
    r["extra"] = {"inherited_hosts": ["other-host.example.local"]}
    assert _judge(s, r, registry, 1)["axes"]["target"] == "fail"


def test_window_wrong_and_rules(registry) -> None:
    s, r = _base(registry)
    r["observed"]["calls"][0]["window"] = _win(ANCHOR - timedelta(minutes=60), ANCHOR)
    v = _judge(s, r, registry)
    assert v["categories"] == ["window_wrong"] and "code:plan_window" in v["locus"]
    j = jd.judge_window
    obs = {"calls": [{"window": _win(ANCHOR - timedelta(minutes=33), ANCHOR)}]}
    assert j("range:30", obs, SENT)[0] == "pass"  # 길이 오차 5분 이내
    obs = {"calls": [{"window": _win(ANCHOR - timedelta(minutes=36), ANCHOR)}]}
    assert j("range:30", obs, SENT)[0] == "fail"
    # 롤링(직전 정시 끝)은 끝 60분 허용
    hour = ANCHOR.replace(minute=0)
    obs = {"calls": [{"window": _win(hour - timedelta(minutes=180), hour)}]}
    assert j("range:180", obs, SENT)[0] == "pass"
    # 지난 3일 — 자정 끝(N 이내 앞)
    obs = {"calls": [{"window": _win(MIDNIGHT - timedelta(days=3), MIDNIGHT)}]}
    assert j("range:4320", obs, SENT)[0] == "pass"
    # 오늘/어제
    obs = {"calls": [{"window": _win(MIDNIGHT, ANCHOR)}]}
    assert j("hourly:today", obs, SENT)[0] == "pass"
    assert j("hourly:yesterday", obs, SENT)[0] == "fail"
    # 호출 창이 없으면 해석(끝은 기준 시각에서 자름)
    tr = {"metric": {"source": "rule", "start": _iso(MIDNIGHT),
                     "end": _iso(MIDNIGHT + timedelta(days=1)), "notes": []}}
    assert j("hourly:today", {"time_resolution": tr, "calls": []}, SENT)[0] == "pass"
    # 관측 구간이 아무것도 없으면 실패 · 송신 시각이 없으면 na
    assert j("range:30", {"calls": []}, SENT)[0] == "fail"
    assert j("range:30", obs, None)[0] == "na"


def test_window_none_default_current(registry) -> None:
    j = jd.judge_window
    default_tr = {"metric": {"source": "default"}, "present": True}
    assert j("current", {"time_resolution": default_tr, "calls": []}, SENT)[0] == "pass"
    explicit = {"metric": {"source": "rule", "start": _iso(MIDNIGHT), "end": _iso(ANCHOR)}}
    assert j("none", {"time_resolution": explicit, "calls": []}, SENT)[0] == "fail"
    past = {"calls": [{"window": _win(ANCHOR - timedelta(days=1, minutes=10),
                                      ANCHOR - timedelta(days=1))}]}
    assert j("default", past, SENT)[0] == "fail"
    recent = {"calls": [{"window": _win(ANCHOR - timedelta(minutes=10), ANCHOR)}]}
    assert j("default", recent, SENT)[0] == "pass"
    assert j("compare", {"calls": [], "time_resolution": explicit}, SENT)[0] == "fail"


def test_empty_result_and_probe_correction(registry) -> None:
    s, r = _base(registry)
    r["observed"]["calls"][0]["rows"] = 0
    assert _judge(s, r, registry)["categories"] == ["empty_result"]
    # 행 0만으로 턴 실패가 되지는 않는다(5축 판정)
    assert r["verdict"]["turn_pass"] is True
    r["probe"] = [{"kind": "empty_check", "call_index": 0, "status": "no_data"}]
    assert _judge(s, r, registry)["categories"] == []
    r["probe"] = [{"kind": "empty_check", "call_index": 0, "status": "rows_present"}]
    assert _judge(s, r, registry)["categories"] == ["empty_result"]


def test_gw_error(registry) -> None:
    s, r = _base(registry)
    r["observed"]["calls"][0].update(error="QUOTA_EXCEEDED", rows=None)
    v = _judge(s, r, registry)
    assert v["categories"][0] == "gw_error" and "gateway" in v["locus"]


def test_answer_drop(registry) -> None:
    s, r = _base(registry)
    r["observed"]["answer_lines"] = ["판정 응답 지연(warning) — web"]
    r["observed"]["response"] = "다른 응답"
    v = _judge(s, r, registry)
    assert v["categories"] == ["answer_drop"] and "code:result_aggregator" in v["locus"]
    r["observed"]["response"] = "본문\n**판정·집계**\n판정 응답  지연(warning) — web"
    assert _judge(s, r, registry)["categories"] == []


def test_priority_order_constant() -> None:
    names = [c.name for c in jd.CATEGORIES]
    assert len(names) == 15 and len({c.row for c in jd.CATEGORIES}) == 13
    assert names.index("sanitize_drop") < names.index("view_wrong")
    assert names.index("fewshot_copy") < names.index("arg_hallucination")
    assert names[0] == "gw_error" and names[-1] == "view_retry"


# --- 되묻기 · 거절 · 건너뜀 · 판정 불가 -----------------------------------------------------------


def test_clarify_and_refusal(registry, real) -> None:
    s = next(x for x in real if x.id == "APM-SRC-none-all")
    r = synth(s, 0, registry)
    v = _judge(s, r, registry)
    assert v["expected_clarify"] and v["turn_pass"] and set(v["axes"].values()) == {"na"}
    r["observed"]["clarify_kind"] = None
    r["observed"]["apm_source_clarification"] = {"options": []}
    assert _judge(s, r, registry)["turn_pass"] is True
    r["observed"]["apm_source_clarification"] = None
    assert _judge(s, r, registry)["turn_pass"] is False
    s = next(x for x in real if x.id == "APM-X-control")
    r = synth(s, 0, registry)
    assert _judge(s, r, registry)["turn_pass"] is True
    r["observed"].update(refused=False, apm_called=True, agents=["apm_query"])
    v = _judge(s, r, registry)
    assert v["expected_refusal"] and v["turn_pass"] is False and v["categories"] == ["source_miss"]


def test_unexpected_clarify_fails_but_source_clarify_does_not(registry) -> None:
    s, r = _base(registry)
    r["observed"]["clarify_kind"] = "source"
    assert _judge(s, r, registry)["turn_pass"] is True
    r["observed"]["clarify_kind"] = "required_arg"
    v = _judge(s, r, registry)
    assert v["turn_pass"] is False and v["axis_detail"]["unexpected"] == "clarify:required_arg"


def _pre_graph(record: dict[str, Any], kind: str) -> None:
    """분해 전 되묻기 관측 — 분해·호출 0 · 담당 없음."""
    record["observed"].update(agents=[], plan_raw=[], plan_final=[], calls=[], apm_called=False,
                              targets=[], clarify_kind=kind, refused=False,
                              time_resolution={"clarify": "unresolved", "metric": None})


def test_time_clarify_is_window_wrong_not_refusal(registry, real) -> None:
    s, r = _base(registry)
    _pre_graph(r, "time")
    v = _judge(s, r, registry)
    assert v["turn_pass"] is False and v["categories"] == ["window_wrong"]
    assert v["axes"] == {"source": "na", "views": "na", "target": "na", "window": "fail",
                         "args": "na"}
    assert v["fewshot_fixable"] is False and v["locus"][0] == "code:input_parser"
    assert v["axis_detail"]["unexpected"] == "clarify:time"
    out = jd.summarize([{**r, "verdict": v}])
    assert out["unexpected"] == [{"turn": "T-1#0", "kind": "clarify:time"}]
    assert out["code_fixes"][0]["category"] == "window_wrong"
    # 소스 되묻기가 정답인 턴에서 시간 되묻기 — 되묻기 실패 · 같은 분류
    s = next(x for x in real if x.id == "APM-SRC-ambiguous")
    r = synth(s, 0, registry)
    _pre_graph(r, "time")
    v = _judge(s, r, registry)
    assert v["expected_clarify"] and v["turn_pass"] is False
    assert v["categories"] == ["window_wrong"] and "unexpected" not in v["axis_detail"]


def test_zone_pre_gate_is_code_fix(registry) -> None:
    s, r = _base(registry)
    _pre_graph(r, "zone")
    v = _judge(s, r, registry)
    assert v["categories"] == ["source_miss"] and v["axes"]["source"] == "fail"
    assert v["fewshot_fixable"] is False and v["locus"][0] == "code:route_zone_gate"
    assert v["axis_detail"]["categories"]["source_miss"]["fewshot_fixable"] is False
    out = jd.summarize([{**r, "verdict": v, "split": "dev"}])
    assert out["code_fixes"] and not out["fewshot_locus"]
    from scripts.apm_bench.loop import fix_candidates

    assert fix_candidates([{**r, "verdict": v, "split": "dev"}]) == []
    # 모르는 사전 게이트 kind 도 같은 축 · 라우트 사전 게이트 지목
    _pre_graph(r, "scope_select")
    assert _judge(s, r, registry)["locus"][0] == "code:route_pre_gate"
    assert jd.pre_graph_clarify("source") is None and jd.pre_graph_clarify(None) is None


def test_untargeted_call_args_do_not_pass_named_target(registry) -> None:
    s, r = _base(registry, target="host:1")
    value = r["placeholders"]["host_1"]
    r["observed"]["targets"] = []
    r["observed"]["untargeted"] = True  # 순위 첫 홉 — 호출 대상은 순위가 고른 인스턴스
    r["observed"]["calls"][0]["hostname"] = value + ".example"
    v = _judge(s, r, registry)
    assert v["axes"]["target"] == "fail" and v["categories"] == ["target_wrong"]


def test_reference_call_window_not_counted_for_default(registry) -> None:
    j = jd.judge_window
    old = _win(ANCHOR - timedelta(days=13, minutes=5), ANCHOR - timedelta(days=13))
    assert j("default", {"calls": [{"window": old, "reference": {"row": 1}}]}, SENT)[0] == "pass"
    assert j("default", {"calls": [{"window": old, "reference": None}]}, SENT)[0] == "fail"


def test_skipped_and_unjudged(registry) -> None:
    s, r = _base(registry)
    r["verdict"] = {"skipped": "e2_skip"}
    v = jd.judge_turn(s, 0, r, registry)
    assert v["skipped"] == "e2_skip" and v["categories"] == []
    s, r = _base(registry)
    r["observed"].update(apm_called=False, agents=[], plan_final=[], calls=[])
    v = _judge(s, r, registry)
    assert v["axis_detail"]["unjudged"] == "no_observation" and v["turn_pass"] is False
    assert v["unjudged"] == "no_observation"  # 위생 `check_evidence`가 보는 칸
    s, r = _base(registry)
    r["observed"]["error"] = "timeout"
    assert _judge(s, r, registry)["axis_detail"]["unjudged"] == "turn_error:timeout"
    # 다른 담당만 배정(APM 미배정)은 관측이 있다 — 판정한다
    s, r = _base(registry)
    r["observed"].update(apm_called=False, agents=["data_query"], calls=[],
                         plan_final=[{"task_id": "t1", "agent": "data_query"}], plan_raw=[])
    v = _judge(s, r, registry)
    assert "unjudged" not in v["axis_detail"] and v["categories"][0] == "source_miss"


def test_run_invalid(registry, real) -> None:
    recs = [synth(s, i, registry) for s in real[:5] for i in range(len(s.turns))]
    assert jd.run_invalid(recs) is False
    for r in recs:
        r["observed"]["apm_called"] = False
    assert jd.run_invalid(recs) is True
    # 건너뛴 턴·되묻기 정답 턴만 남으면 무효 판정 대상이 없다
    for r in recs:
        r["verdict"] = {"skipped": "aborted"}
    assert jd.run_invalid(recs) is False
    assert jd.run_invalid([]) is False


# --- 집계 · 예문 커버리지 절 --------------------------------------------------------------------


def _judged_run(real, registry) -> list[dict[str, Any]]:
    recs = [synth(s, i, registry) for s in real for i in range(len(s.turns))]
    # 실패 하나: 첫 sql_stats 턴에 예문 복사
    target = next(r for r in recs if r["observed"]["plan_final"]
                  and r["observed"]["plan_final"][0]["views"] == ["apm.sql_stats"]
                  and not r["observed"]["plan_final"][0]["view_args"])
    target["observed"]["plan_final"][0]["view_args"] = {
        "apm.sql_stats": {"n": 5, "sort_by": "responseTime"}}
    recs[1]["verdict"] = {"skipped": "e2_skip"}
    return jd.judge_records(real, recs, registry)


def test_summarize(real, registry) -> None:
    recs = _judged_run(real, registry)
    out = jd.summarize(recs)
    c = out["counts"]
    assert c["records"] == len(recs) and c["skipped"] == 1 and c["unjudged"] == 0
    assert c["clarify"] == 3 and c["refusal"] == 3
    assert c["normal"] == len(recs) - 1 - 6
    assert out["turn"]["total"] == c["normal"] and out["turn"]["pass"] == c["normal"] - 1
    assert out["axes"]["args"]["fail"] == 1 and out["axes"]["views"]["accuracy"] == 1.0
    assert set(out["by_split"]) == {"dev", "holdout"}
    assert out["primary_categories"] == {"fewshot_copy": 1}
    assert out["fewshot_copies"][0]["example_index"] == 2
    assert out["fewshot_locus"]["F1:apm.sql_stats:example#2"] == 1
    assert out["clarify"]["accuracy"] == 1.0 and out["refusal"]["accuracy"] == 1.0
    assert out["run_invalid"] is False and out["code_fixes"] == []
    assert any(k.startswith("COV-STAT-SQL") for k in out["by_cov"])


def test_example_section_renders(real, registry) -> None:
    recs = _judged_run(real, registry)
    # 예문 없는 보기 실패 하나 — 처방 「예문 추가」
    pool = next(r for r in recs if r["observed"]["plan_final"]
                and r["observed"]["plan_final"][0]["views"] == ["apm.pool"])
    _plan(pool, views=["apm.active"])
    scenario = next(s for s in real if s.id == pool["scenario_id"])
    pool["verdict"] = jd.judge_turn(scenario, pool["turn"], pool, registry)
    for r in recs:  # 예문 절은 dev 만 센다(권고 ③) — 예문 복사 턴을 dev 로 둔다
        if (r.get("verdict") or {}).get("fewshot_copy"):
            r["split"] = "dev"
    section = jd.make_example_section(recs, registry)
    cap = sc.parse_capability_map()
    md = sc.render_coverage_md(sc.build_coverage(real, cap, registry), extra_sections=[section])
    assert "## 예문 커버리지 × 실패율 (F1 few-shot)" in md
    mine = md.split("## 예문 커버리지 × 실패율", 1)[1].splitlines()
    pool_line = next(ln for ln in mine if ln.startswith("| `apm.pool` |"))
    assert pool_line.endswith("| 예문 추가 |")
    arg_line = next(ln for ln in mine if ln.startswith("| `apm.sql_stats.sort_by` |"))
    assert "예문 교정 또는 결정적 처리 후보" in arg_line
    assert "#2 「web01 오늘 가장 오래 걸린 SQL 5개」" in md


def test_example_section_counts_dev_only(real, registry) -> None:
    """holdout 실패는 예문 절(보기 실패율·예문 복사 지목)에 들어가지 않는다(권고 ③)."""
    recs = [synth(s, i, registry) for s in real for i in range(len(s.turns))]
    held = next(r for r in recs if r["split"] == "holdout" and r["observed"]["plan_final"]
                and r["observed"]["plan_final"][0]["views"] == ["apm.sql_stats"]
                and not r["observed"]["plan_final"][0]["view_args"])
    held["observed"]["plan_final"][0]["view_args"] = {
        "apm.sql_stats": {"n": 5, "sort_by": "responseTime"}}
    recs = jd.judge_records(real, recs, registry)
    assert held["verdict"]["fewshot_copy"]  # 전제: holdout 턴에 예문 복사가 났다
    cap = sc.parse_capability_map()
    md = sc.render_coverage_md(sc.build_coverage(real, cap, registry),
                               extra_sections=[jd.make_example_section(recs, registry)])
    mine = md.split("## 예문 커버리지 × 실패율", 1)[1]
    assert "### 예문 복사 지목\n\n- 없음" in mine
    assert "holdout" in mine.splitlines()[2]
