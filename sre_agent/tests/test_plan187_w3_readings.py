"""plans/187 W3 — 판독 규칙 R-A~R-H · 결정적 가설 순위 · 대응 순서 · 실행 후 처리(소견기 없음).

W0 골든 픽스처 10종을 W2 실행기로 돌려 판독이 `expected.readings`와 같은지 본다. 실 MCP·LLM 호출 0.

녹화 조회는 「녹화 args가 호출 args의 부분집합인 항목 중 **가장 구체적인**(키가 많은) 것」을 쓴다
(`SpecificCaller`). W2 `FakeCaller`는 첫 항목을 고르므로 `apm_runtime_health(metrics=[…])` 호출이
지표 없는 녹화에 걸려 S2 `process_cpu_pct`·`active_services` 추세가 비고, R-B′·R-E가 판정 불가가
된다.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from sre_agent.application.playbook_runner import (
    finalize_playbook_run,
    plan_stages,
    run_playbook,
)
from sre_agent.domain.playbook import parse_playbook
from sre_agent.domain.playbook_expr import READING_NAMES
from sre_agent.domain.playbook_hypotheses import (
    INCONCLUSIVE,
    REFUTED,
    SUPPORTED,
    build_followups,
    hypothesis_context,
    is_top_inconclusive,
    rank_hypotheses,
    select_actions,
)
from sre_agent.domain.playbook_readings import (
    _READERS,
    NA,
    READING_ACTIONS,
    READING_RULES,
    ReadingParams,
    Readings,
    compute_readings,
    enabled_readings,
    read_change_candidates,
    read_cpu_owner,
    read_db_split,
    read_dominant,
    read_fs_eta,
    read_heap_shape,
    read_host_saturation,
    read_little_check,
    read_load_mode,
    read_scope,
    read_service_spread,
    reading_params,
)
from test_plan187_w2_runner import (
    SCENARIOS,
    WAS_HEAP_TEST,
    FakeCaller,
    _load,
    _playbook_for,
)

# ── 픽스처 실행 헬퍼 ────────────────────────────────────────────────────────


class SpecificCaller(FakeCaller):
    """녹화 조회 — 부분집합 일치 중 가장 구체적인(args 키가 많은) 녹화."""

    def lookup(self, tool: str, args: dict) -> dict | None:
        if tool in self.overrides:
            return self.overrides[tool]
        best = None
        for entry in self.fixture["tool_responses"].get(tool, []):
            if all(args.get(k) == v for k, v in entry["args"].items()):
                if best is None or len(entry["args"]) > len(best["args"]):
                    best = entry
        return None if best is None else best["response"]


def heap_playbook():
    """case4용 was.heap — W2 테스트 정의 + S1에 판독 범위 `readings: []`(골든 시그널은 힙 판독의
    TPS 입력으로만 쓰고 R-B 지연 유형은 판독하지 않는다 — 픽스처 load_mode null)."""
    raw = copy.deepcopy(WAS_HEAP_TEST)
    s1 = next(s for s in raw["steps"] if s["id"] == "S1")
    s1["args"] = {"readings": []}
    return parse_playbook(raw)


def playbook_of(fx: dict):
    if fx["expected"]["playbook_id"] == "was.heap":
        return heap_playbook(), "test:was.heap"
    return _playbook_for(fx)


def run_fixture(name: str, **kw: Any):
    fx = _load(name)
    pb, matched_by = playbook_of(fx)
    caller = SpecificCaller(fx)
    kw.setdefault(
        "polestar_source", None if fx["expected"].get("polestar_unresolved") else "polestar"
    )
    kw.setdefault("max_workers", 1)
    kw.setdefault("for_each_max", 1)
    run = run_playbook(fx["alarm"], pb, matched_by=matched_by, polestar=caller, apm=caller, **kw)
    return fx, pb, run


def _norm(values: dict[str, Any]) -> dict[str, Any]:
    out = dict(values)
    cands = out.get("change_candidates")
    if cands is not None:
        out["change_candidates"] = [
            {k: c[k] for k in ("source", "at", "minutes_before_alarm")} for c in cands
        ]
    return out


# ── 판독 이름 정합 ──────────────────────────────────────────────────────────


def test_reading_names_consistent_with_expr_grammar():
    assert set(_READERS) == set(READING_ACTIONS) == set(READING_RULES) == set(READING_NAMES)


# ── 10 픽스처 = expected.readings ───────────────────────────────────────────


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_readings_match_expected(name):
    fx, _, run = run_fixture(name)
    assert run.readings is not None
    got = _norm(run.readings.values)
    exp = fx["expected"]["readings"]
    assert {k: got[k] for k in exp} == exp
    # R-D는 구조화 필드가 없는 픽스처 전부 n/a(텍스트 파서 없음).
    assert got["db_split"] == NA


def test_case2_external_dominant_and_cpu_other_process():
    _, _, run = run_fixture("case2_ldap_external")
    assert run.readings.values["dominant"] == "external"
    assert run.readings.values["cpu_owner"] == "other_process"
    ev = run.readings.evidence["cpu_owner"]
    assert ev.values["gap_pct"] >= 20 and "top_process" in ev.values


def test_case3_cpu_was_logic():
    _, _, run = run_fixture("case3_cpu_loop")
    assert run.readings.values["cpu_owner"] == "was_logic"


def test_case4_heap_periodic_with_test_defined_heap_playbook():
    _, _, run = run_fixture("case4_oom_periodic")
    assert run.readings.values["heap_shape"] == "periodic"
    assert run.readings.evidence["heap_shape"].values["period_minutes"] > 0
    # S1 `readings: []` — 지연 유형(R-B)은 이 플레이북에서 판독하지 않는다.
    assert run.readings.values["load_mode"] is None
    assert run.readings.evidence["load_mode"].reason == "이 플레이북에 해당 판독 없음"


def test_case1_db_split_na_gap_to_case_conclusion():
    """case1 사례 결론은 `db_split: acquire`지만 프로파일에 구조화 필드(acquire_ms·execute_ms)가
    없어 R-D는 n/a다(텍스트 파서 금지 — §8 수용 기준 2′ 미달 · 잔여)."""
    fx, _, run = run_fixture("case1_jdbc_acquire")
    assert fx["expected"]["case_conclusion"]["db_split"] == "acquire"
    assert run.readings.values["db_split"] == NA
    assert "구조화 필드" in run.readings.evidence["db_split"].reason


@pytest.mark.xfail(
    strict=True, reason="계약: 게이트웨이가 프로파일 구조화 필드를 내면 case1은 acquire가 된다"
)
def test_case1_db_split_acquire_when_structured_fields_exist():
    _, _, run = run_fixture("case1_jdbc_acquire")
    assert run.readings.values["db_split"] == "acquire"


# ── 판독 경계표 ─────────────────────────────────────────────────────────────


def _step(action: str, data: dict, status: str = "ok", missing=()) -> dict:
    return {"action": action, "status": status, "data": data, "missing": list(missing)}


def _s0(instances: list[tuple[str, str, str | None]], resolution: str = "resolved") -> dict:
    return _step(
        "scope.resolve_was_targets",
        {
            "resolution": resolution,
            "scope": {"alarm_instance": instances[0][0] if instances else None},
            "instances": [{"instance": i, "host": h, "verdict": v} for i, h, v in instances],
        },
    )


@pytest.mark.parametrize(
    "instances,resolution,expected",
    [
        ([("A1", "h1", "normal")], "resolved", None),
        ([("A1", "h1", None)], "resolved", None),
        ([("A1", "h1", "degraded")], "unresolved", None),
        ([("A1", "h1", "degraded")], "resolved", "instance"),  # 같은 호스트 1개 < 2
        ([("A1", "h1", "degraded"), ("A2", "h1", "normal")], "resolved", "instance"),
        ([("A1", "h1", "degraded"), ("A2", "h1", "degraded")], "resolved", "host"),
        ([("A1", "h1", "degraded"), ("B1", "h2", "degraded")], "resolved", "shared_dependency"),
        ([("A1", "h1", "degraded"), ("B1", "h2", "normal")], "resolved", "instance"),
    ],
)
def test_r_a_scope_boundaries(instances, resolution, expected):
    value, ev = read_scope({"S0": _s0(instances, resolution)})
    assert value == expected
    if expected is None:
        assert ev.reason


def _s3(rows: list[dict], profile: dict | None = None, status: str = "ok") -> dict:
    data: dict[str, Any] = {"rows": rows}
    if profile is not None:
        data["profile"] = profile
    return _step("apm.slow_tx_breakdown", data, status)


@pytest.mark.parametrize(
    "apps,expected",
    [(["a", "a", "b", "b"], "many"), (["a", "a", "b"], "single"), (["a"], "single")],
)
def test_r_a_service_spread_boundaries(apps, expected):
    value, ev = read_service_spread({"S3": _s3([{"application": a} for a in apps])})
    assert value == expected
    assert ev.values["share"] == round(apps.count(apps[0]) / len(apps), 3)


def _s1(tps, p95, base_p95, base_tps=100.0, avg_ms=None) -> dict:
    tgt: dict[str, Any] = {
        "incident": {"tps": tps, "p95_ms": p95},
        "baseline_p95_ms": base_p95,
        "baselines": {"prev_day": {"tps": base_tps}, "prev_week": {"tps": base_tps}},
    }
    if avg_ms is not None:
        tgt["period_compare"] = {"incident": {"current": {"avg_response_ms": avg_ms}}}
    return _step("apm.golden_signals", {"targets": {"A1": tgt}})


@pytest.mark.parametrize(
    "p95,tps,expected",
    [
        (149, 150, None),  # p95 1.49배 — 지연 유형 해당 없음
        (150, 120, "load_increase"),  # +20%
        (150, 119.9, "processing_delay"),
        (150, 70, "buckle"),  # −30%
        (150, 70.1, "processing_delay"),
    ],
)
def test_r_b_load_mode_boundaries(p95, tps, expected):
    value, ev = read_load_mode({"S1": _s1(tps, p95, 100)})
    assert value == expected
    if expected is None:
        assert "p95 상승 없음" in ev.reason


def test_r_b_load_mode_missing_baseline_is_none_with_reason():
    value, ev = read_load_mode({"S1": _s1(100, 300, None)})
    assert value is None and "기준선" in ev.reason


def _s2(target: dict, status: str = "ok", missing=()) -> dict:
    return _step("apm.runtime_and_pool", {"targets": {"A1": target}}, status, missing)


@pytest.mark.parametrize("active,expected", [(30.0, "active_excess"), (29.9, "consistent")])
def test_r_b_prime_little_boundaries(active, expected):
    steps = {
        "S1": _s1(10, 200, 100, avg_ms=1000),
        "S2": _s2({"trend_summary": {"active_services": {"avg": active}}}),
    }
    value, ev = read_little_check(steps)
    assert value == expected and ev.values["expected_active"] == 10


def test_r_b_prime_active_missing_is_none_with_reason():
    steps = {"S1": _s1(10, 200, 100, avg_ms=1000), "S2": _s2({}, missing=["apm_runtime_health"])}
    value, ev = read_little_check(steps)
    assert value is None and "결손" in ev.reason


def _row(rt, sql=0, fetch=0, external=0, cpu=0, network=0, app="a") -> dict:
    return {
        "application": app,
        "response_time_ms": rt,
        "sql_ms": sql,
        "fetch_ms": fetch,
        "external_ms": external,
        "cpu_ms": cpu,
        "network_ms": network,
    }


@pytest.mark.parametrize(
    "rows,expected",
    [
        ([_row(100, sql=30)], "sql"),  # 비중 0.3 = 기준
        ([_row(100, sql=29)], "other"),
        ([_row(100, sql=60), _row(100, external=90)], "external"),  # 1:1 동률 → 합계 큰 쪽
        ([_row(100, sql=50), _row(100, external=50)], "sql"),  # 합계도 같으면 고정 순서
        ([_row(100, sql=60), _row(100, sql=60), _row(100, external=90)], "sql"),
    ],
)
def test_r_c_dominant_boundaries(rows, expected):
    value, _ = read_dominant({"S3": _s3(rows)})
    assert value == expected


@pytest.mark.parametrize(
    "profile,expected",
    [
        ({"acquire_ms": 11, "execute_ms": 10}, "acquire"),
        ({"acquire_ms": 10, "execute_ms": 10}, "execute"),
        ({"acquire_ms": 1, "execute_ms": 10}, "execute"),
        ({"acquire_ms": 11}, NA),
        ({"profile_excerpt": "getConnection 900ms\nexecuteQuery 10ms"}, NA),  # 텍스트는 해석 안 함
        (None, NA),
    ],
)
def test_r_d_db_split_structured_fields_only(profile, expected):
    value, ev = read_db_split({"S3": _s3([_row(100)], profile)})
    assert value == expected
    if expected == NA:
        assert ev.reason


def _s4(cpu=None, memory=None, fs=None, series=None) -> dict:
    findings: dict[str, Any] = {}
    for key, f in (("cpu", cpu), ("memory", memory), ("filesystem", fs)):
        if f is not None:
            findings[key] = f
    return _step("host.usage_trend", {"findings": findings, "series": series or {}})


@pytest.mark.parametrize(
    "host,anomalous,was,tps,expected",
    [
        (90, False, 89, 100, "none"),
        (90, True, 70, 100, "other_process"),  # 차 20%p = 기준
        (90, True, 71, 100, "none"),  # 차 19%p · WAS 71% < 80
        (95, True, 80, 120, "was_load"),  # TPS +20%
        (95, True, 80, 119, "was_logic"),
    ],
)
def test_r_e_cpu_owner_boundaries(host, anomalous, was, tps, expected):
    steps = {
        "S1": _s1(tps, 200, 100),
        "S2": _s2({"trend_summary": {"process_cpu_pct": {"max": was}}}),
        "S4": _s4(cpu={"peak_value": host, "is_anomalous": anomalous}),
    }
    value, _ = read_cpu_owner(steps)
    assert value == expected


def test_r_e_cpu_owner_missing_inputs_are_none_with_reason():
    host = _s4(cpu={"peak_value": 95, "is_anomalous": True})
    assert read_cpu_owner({"S4": _s4()})[0] is None
    value, ev = read_cpu_owner({"S4": host, "S2": _s2({}, status="missing")})
    assert value is None and "조회 실패" in ev.reason


def _heap(values: list[float], committed: float = 1000.0, step_min: int = 10) -> dict:
    return _s2(
        {
            "trend": {
                "heap_used_mb": [
                    {"time_ms": i * step_min * 60000, "value": v} for i, v in enumerate(values)
                ],
                "heap_committed_mb": [{"time_ms": 0, "value": committed}],
            }
        }
    )


@pytest.mark.parametrize(
    "values,tps,expected",
    [
        ([400, 800, 300, 800, 300, 800, 300, 800, 300], 100, "periodic"),  # 하락 4회 · 간격 같음
        ([400, 800, 300, 800, 800, 800, 300, 800, 300], 100, "normal"),  # 간격 불규칙
        ([300, 320, 310, 340, 360, 350, 380, 400, 390], 100, "leak_suspect"),
        ([300, 310, 305, 310, 320, 315, 320, 330, 325], 100, "normal"),  # 바닥 상승 < 5%
        ([500, 600, 700, 850, 800, 700, 600, 500, 500], 120, "load_proportional"),
        ([500, 600, 700, 850, 800, 700, 600, 500, 500], 119, "normal"),
        ([300, 310, 320, 330, 340], 100, None),  # 점 부족
    ],
)
def test_r_f_heap_shape_boundaries(values, tps, expected):
    value, ev = read_heap_shape({"S1": _s1(tps, 200, 100), "S2": _heap(values)})
    assert value == expected
    if expected is None:
        assert "점 부족" in ev.reason


@pytest.mark.parametrize(
    "cores,cpu,mem,fs,expected",
    [
        (None, 80, 10, 10, "cpu"),
        (None, 79.9, 10, 10, "none"),
        (2, 50, 10, 10, "cpu"),  # 1~2코어는 50%
        (2, 49.9, 10, 10, "none"),
        (4, 79.9, 90, 10, "memory"),
        (4, 79.9, 89.9, 90, "fs"),
        (4, 95, 95, 95, "cpu"),  # 여럿이면 cpu → memory → fs
    ],
)
def test_r_g_host_saturation_boundaries(cores, cpu, mem, fs, expected):
    steps = {"S4": _s4(cpu={"peak_value": cpu}, memory={"peak_value": mem}, fs={"peak_value": fs})}
    value, ev = read_host_saturation(steps, ReadingParams(host_cores=cores))
    assert value == expected
    if cores is None:
        assert "코어 수 미상 → 80% 기준" in ev.reason


def test_r_g_host_saturation_partial_and_total_missing():
    value, ev = read_host_saturation({"S4": _s4(cpu={"peak_value": 10})})
    assert value == "none" and "memory 결손" in ev.reason
    value, ev = read_host_saturation({"S4": _s4()})
    assert value is None and ev.reason


def test_r_g_fs_eta():
    series = {"filesystem": {"points": [["2026-10-19T00:00:00", 80], ["2026-10-19T02:00:00", 90]]}}
    value, ev = read_fs_eta({"S4": _s4(series=series)})
    assert value == "2026-10-19T04:00:00" and ev.values["rate_pct_per_hour"] == 5
    flat = {"filesystem": {"points": [["2026-10-19T00:00:00", 80], ["2026-10-19T02:00:00", 80]]}}
    value, ev = read_fs_eta({"S4": _s4(series=flat)})
    assert value is None and "증가 없음" in ev.reason


def test_r_h_change_candidates_sorted_and_labelled():
    s7 = _step(
        "change.recent",
        {
            "candidates": [
                {"source": "b", "at": "t2", "minutes_before_alarm": 90},
                {"source": "a", "at": "t1", "minutes_before_alarm": 15},
                {"source": "c", "at": "t3"},  # 근접 분 없음 — 제외
            ]
        },
    )
    value, ev = read_change_candidates({"S7": s7})
    assert [c["source"] for c in value] == ["a", "b"]
    assert ev.reason == "상관 후보 — 원인 확정 아님"
    readings = compute_readings({"S7": s7})
    assert readings.facts["S7"]["change"] == "near"


def test_missing_inputs_never_silent_default():
    readings = compute_readings({})
    for name, value in readings.values.items():
        assert value == (NA if name == "db_split" else None)
        assert readings.evidence[name].reason, name


def test_reading_params_override_and_unknown_keys_noted():
    raw = copy.deepcopy(WAS_HEAP_TEST)
    s4 = next(s for s in raw["steps"] if s["id"] == "S4")
    s4["args"]["reading_params"] = {"cpu_high_pct": 70, "nope": 1, "memory_high_pct": "x"}
    pb = parse_playbook(raw)
    params, notes = reading_params(pb)
    assert params.cpu_high_pct == 70 and params.memory_high_pct == 90
    assert any("nope" in n for n in notes) and any("memory_high_pct" in n for n in notes)
    assert "host_saturation" in enabled_readings(pb) and "dominant" not in enabled_readings(pb)


# ── 가설 규칙표 ─────────────────────────────────────────────────────────────


def _ctx(**kw: Any) -> dict[str, Any]:
    base = {name: None for name in READING_NAMES}
    steps = {k: v for k, v in kw.items() if k.startswith("S")}
    base.update({k: v for k, v in kw.items() if not k.startswith("S")})
    base.update(steps)
    return base


@pytest.mark.parametrize(
    "ctx,top_rule,top_status",
    [
        # §4.2 예 — dominant=sql ∧ db_split=execute ∧ scope=shared_dependency
        (
            _ctx(dominant="sql", db_split="execute", scope="shared_dependency"),
            "H-DB-SHARED",
            SUPPORTED,
        ),
        # §4.2 예 — db_split=acquire ∧ scope=instance
        (_ctx(db_split="acquire", scope="instance"), "H-POOL-INSTANCE", SUPPORTED),
        # §5.4 예 — S3 SQL 지배 ∧ S2 풀 고갈
        (
            _ctx(dominant="sql", scope="instance", S2={"pool": "exhausted"}),
            "H-DB-POOL-CHAIN",
            SUPPORTED,
        ),
        # R-D 결손 — 판단 보류
        (
            _ctx(dominant="sql", scope="shared_dependency", S2={"pool": "ok"}),
            "H-DB-SHARED",
            INCONCLUSIVE,
        ),
        (_ctx(dominant="external", scope="shared_dependency"), "H-EXT-SHARED", SUPPORTED),
        (
            _ctx(dominant="external", scope="instance", service_spread="single"),
            "H-EXT-INSTANCE-SINGLE",
            SUPPORTED,
        ),
        (
            _ctx(heap_shape="leak_suspect", S1={"verdict": "degraded"}, S2={"gc": "pressure"}),
            "H-GC-LEAK",
            SUPPORTED,
        ),
        (_ctx(heap_shape="periodic", S2={"gc": "pressure"}), "H-HEAP-PERIODIC", SUPPORTED),
        (_ctx(cpu_owner="was_logic", S7={"change": "near"}), "H-CPU-LOGIC-CHANGE", SUPPORTED),
        (_ctx(cpu_owner="was_logic", S7={"change": "far"}), "H-CPU-LOGIC", SUPPORTED),
        (_ctx(cpu_owner="other_process"), "H-CPU-OTHER", SUPPORTED),
        (_ctx(host_saturation="fs"), "H-HOST-FS", SUPPORTED),
    ],
)
def test_hypothesis_rule_table(ctx, top_rule, top_status):
    hyps = rank_hypotheses(ctx)
    assert (hyps[0].rule_id, hyps[0].status) == (top_rule, top_status)
    assert [h.rank for h in hyps] == list(range(1, len(hyps) + 1))
    assert is_top_inconclusive(hyps) == (top_status == INCONCLUSIVE)


def test_refuted_hypotheses_rank_last_with_unmet_conditions():
    hyps = rank_hypotheses(_ctx(dominant="external", scope="shared_dependency"))
    statuses = [h.status for h in hyps]
    assert statuses == sorted(statuses, key=[SUPPORTED, INCONCLUSIVE, REFUTED].index)
    refuted = [h for h in hyps if h.status == REFUTED]
    assert refuted and all(h.unmet for h in refuted)
    assert hyps[0].to_dict()["status_label"] == "지지"


def test_no_hypothesis_top_inconclusive_follows_alarm_verdict():
    assert rank_hypotheses(_ctx()) == []
    assert is_top_inconclusive([], alarm_degraded=True) is True
    assert is_top_inconclusive([], alarm_degraded=False) is False


#: 픽스처 1순위 가설(규칙 ID) — 제목은 규칙표 일반형(case2 「LDAP」·case4 「3시간 주기 → OOM」은
#: 사례 특정 문구라 규칙 제목과 다르다 · 주기는 판독 근거 `period_minutes`에 있다).
TOP_RULE = {
    "case1_jdbc_acquire": ("H-POOL-INSTANCE", INCONCLUSIVE),
    "case2_ldap_external": ("H-EXT-SHARED", SUPPORTED),
    "case3_cpu_loop": ("H-CPU-LOGIC-CHANGE", SUPPORTED),
    "case4_oom_periodic": ("H-HEAP-PERIODIC", SUPPORTED),
    "gc": ("H-GC-LEAK", SUPPORTED),
    "latency_external": ("H-EXT-INSTANCE-SINGLE", SUPPORTED),
    "latency_sql": ("H-DB-SHARED", INCONCLUSIVE),
    "normal": None,
    "partial_missing": None,
    "polestar_unresolved": None,
}
TITLE_DIFFERS = {"case2_ldap_external", "case4_oom_periodic"}
#: R-D(db_split)가 n/a라 1순위가 판단 보류 → 심층 조사 조건 참. 픽스처 기대(false)는 사례 결론
#: (구조화 필드가 있을 때) 기준이다 — §8 수용 기준 2·2′ 잔여.
DEEP_DIVE_RD_GAP = {"case1_jdbc_acquire", "latency_sql"}


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_top_hypothesis_and_deep_dive(name):
    fx, pb, run = run_fixture(name, synthesize=True)
    syn = run.synthesis
    hyps = syn["hypotheses"]
    exp_top = fx["expected"]["top_hypothesis"]
    if TOP_RULE[name] is None:
        assert hyps == [] and exp_top is None
    else:
        assert (hyps[0]["rule_id"], hyps[0]["status"]) == TOP_RULE[name]
        if name not in TITLE_DIFFERS:
            assert hyps[0]["title"] == exp_top
    if name in DEEP_DIVE_RD_GAP:
        assert syn["top_inconclusive"] is True and fx["expected"]["deep_dive_expected"] is False
    else:
        assert syn["top_inconclusive"] == fx["expected"]["deep_dive_expected"]
    # S9는 실행 후 처리가 채운다(픽스처 section_status).
    assert run.section("S9").status == fx["expected"]["section_status"]["S9"]


def test_actions_order_triage_then_runbook_then_hints():
    fx, pb, run = run_fixture("case2_ldap_external", synthesize=True)
    actions = run.synthesis["actions"]
    assert actions[0]["key"] == "triage_first"
    sources = [a["source"] for a in actions]
    assert sources.index("runbook") < max(i for i, s in enumerate(sources) if s == "response_hint")
    assert any("연계 담당" in a["text"] for a in actions if a["source"] == "runbook")
    assert [a["order"] for a in actions] == list(range(1, len(actions) + 1))


def test_normal_fixture_has_no_actions():
    _, _, run = run_fixture("normal", synthesize=True)
    assert run.synthesis["actions"] == [] and run.synthesis["hypotheses"] == []


def test_followups_cite_reading_reason_and_missing_steps():
    _, _, run = run_fixture("latency_sql", synthesize=True)
    assert any("db_split" in f and "구조화 필드" in f for f in run.synthesis["followups"])
    _, _, run = run_fixture("partial_missing", synthesize=True)
    assert any(f.startswith("S3 ") and "수집 결손" in f for f in run.synthesis["followups"])


def test_select_actions_mitigation_when_uses_readings():
    readings = Readings(values={"dominant": "sql", "db_split": "execute"}, evidence={})
    ctx = hypothesis_context(readings, {})
    _, pb, _ = run_fixture("latency_sql")
    acts = select_actions([], pb.response_hints, pb.runbook.mitigation, ctx)
    assert [a.text for a in acts] == ["DBA에 해당 SQL 실행계획·락 대기 확인 요청"]
    assert build_followups([], readings) == []


# ── 실행기 통합(소견기 없음) ────────────────────────────────────────────────


def test_finalize_without_narrator_marks_opinions_not_configured():
    _, _, run = run_fixture("latency_sql", synthesize=True)
    b = run.to_briefing_dict()
    narrated = [s for s in b["sections"] if s["step"] != "S8"]
    assert all(s["opinion"] == "" for s in b["sections"])
    assert {s["opinion_dropped_reason"] for s in narrated} == {"소견기 미구성"}
    assert b["synthesis"]["narrative"] == "" and b["readings"]["values"]["dominant"] == "sql"
    assert run.timings["llm_calls"] == 0 and "synthesis_ms" in run.timings
    s9 = next(s for s in b["sections"] if s["step"] == "S9")
    assert s9["status"] == "ok" and s9["table"]["rows"][0][1] == "업무 DB 지연(공통 의존)"


def test_run_without_synthesize_is_backward_compatible():
    _, _, run = run_fixture("latency_sql")
    b = run.to_briefing_dict()
    assert b["synthesis"] == {"hypotheses": [], "actions": [], "followups": []}
    assert run.section("S9").status == "pending_synthesis"
    assert all(s["opinion_dropped_reason"] is None for s in b["sections"])


def test_finalize_is_separately_callable():
    fx, pb, run = run_fixture("gc")
    finalize_playbook_run(run, pb)
    assert run.synthesis["hypotheses"][0]["rule_id"] == "H-GC-LEAK"


def test_when_can_reference_readings_and_facts():
    """`when`이 앞 단계의 판독(dominant)·보조 사실(S2.pool)을 본다 — 단계마다 다시 계산."""
    fx, pb, _ = run_fixture("latency_sql")
    raw = pb.model_dump(mode="json", exclude_none=True)
    for step in raw["steps"]:
        if step["id"] == "S6":
            step["when"] = "dominant == 'external'"
        if step["id"] == "S7":
            step["when"] = "S2.pool == 'exhausted'"
    pb2 = parse_playbook(raw)
    caller = SpecificCaller(fx)
    run = run_playbook(
        fx["alarm"],
        pb2,
        matched_by="t",
        polestar=caller,
        apm=caller,
        polestar_source="polestar",
        max_workers=1,
        for_each_max=1,
    )
    stages = [[s.id for s in st] for st in plan_stages(pb2)]
    assert any("S3" in st for st in stages) and not any({"S3", "S6"} <= set(st) for st in stages)
    s6, s7 = run.section("S6"), run.section("S7")
    assert s6.status == "skipped" and "when_false" in s6.reason  # dominant=sql
    assert s7.status == "skipped" and "when_false" in s7.reason  # pool ok
