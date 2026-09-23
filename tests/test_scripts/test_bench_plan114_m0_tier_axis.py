"""plans/114 M-0 — 사다리 단을 벤치 캠페인 **첫 구간 축**으로 잰다(D-250 · `109·CS-31` X1).

벤치는 종전에 서버 `.env` 가 우연히 확정한 단을 기준선으로 두고 다른 축만 흔들었다
(run `20260922-112010`: 4 프로파일 전부 2단). 단은 노드 집합을 바꿔 다른 축의 효과를 조건부로
만들므로, **단 자체를 첫 구간의 다중 키 축으로 재고 이긴 단을 남은 구간에 주입**한다.

여기서 지키는 것 여섯.

  1. 사다리 3키가 **한 축**으로 전개된다(레벨 정의는 `config/scenarios/profiles.yaml` 을 읽는다)
  2. 캠페인 계획의 **첫 구간**이 그 축이다 — 보정 구간보다도 앞
  3. 구간이 끝나면 **이긴 레벨**이 `campaign.json` 에 남고 남은 구간 **전 arm** 에 주입된다
  4. 주입 뒤 구간에서 실제 단이 승자와 다르면 **차단**
  5. 단 축 구간에서는 단이 갈리는 것이 **정상**(예외)
  6. **판정 불가면 기준 경로(D-251 · 2단 — D-225 개정)로 고정하고 고지와 함께 계속 돈다** —
     멈추는 것은 주입할 env 를 만들 수 없을 때뿐이다(D-250 ② 2026-09-23 개정)

서버·LLM·DB 0 — 스위프 1회는 가짜다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes, campaign as cm, compare, optimize, sweep  # noqa: E402
from tests.test_scripts.test_bench_campaign import _health, _rate, _state  # noqa: E402

LADDER = axes.LADDER_AXIS
TIER2 = "intent_orchestration"
TIER3 = "semantic_router"
L2, L3 = "tier2_intent", "tier3_router"


# ── 1. 다중 키 축 ────────────────────────────────────────────────────


def test_사다리_3키가_한_축으로_전개된다() -> None:
    axis = axes.ladder_axis()

    assert axis.multi_key and axis.structural
    assert axis.env_keys == axes.LADDER_ENV_KEYS
    assert axis.levels == (L2, L3)
    # 축 id 는 env 키가 아니다 — 주입은 레벨이 정한다.
    assert axis.env_for(L2)["ENABLE_INTENT_ORCHESTRATION"] == "true"
    assert axis.env_for(L3)["ENABLE_INTENT_ORCHESTRATION"] == "false"
    assert all(axis.env_for(lv)["ENABLE_SEMANTIC_ROUTING"] == "true" for lv in axis.levels)


def test_레벨_정의는_profiles_yaml을_읽는다_사본_아님() -> None:
    """정의가 바뀌면 축도 바뀐다 — 사본을 두면 110 재테스트 arm 과 갈린다(D-053)."""
    from scripts.scenario.catalog import load_profiles

    profiles = load_profiles()
    axis = axes.ladder_axis()

    for level in axis.levels:
        assert axis.env_for(level) == {k: profiles[level][k] for k in axes.LADDER_ENV_KEYS}


def test_1단은_기본_레벨에_없다() -> None:
    """오케스트레이터 서빙이 전제라 불성립 시 조용히 강등돼 arm 이 무효가 된다(D-250 ①)."""
    axis = axes.ladder_axis()

    assert all(axis.env_for(lv)["ENABLE_DEEPAGENTS_PACKAGE"] == "false" for lv in axis.levels)


def test_정의를_못_읽으면_사유를_들고_다닌다() -> None:
    with pytest.raises(axes.StructuralAxisUnavailable, match="profiles.yaml 에 없다"):
        axes.ladder_axis({"baseline": {}})
    with pytest.raises(axes.StructuralAxisUnavailable, match="사다리 키"):
        axes.ladder_axis({L2: {"ENABLE_SEMANTIC_ROUTING": "true"},
                          L3: {"ENABLE_SEMANTIC_ROUTING": "true"}})

    _, decisions = axes.structural_axes()
    assert all(d.stage == "F0" and d.reason.strip() for d in decisions)


def test_모르는_레벨은_빈_주입이_아니라_오류다() -> None:
    """빈 주입은 곧 기준선이라 조용히 A/A arm 이 된다 — 드러낸다."""
    with pytest.raises(KeyError, match="레벨"):
        axes.ladder_axis().env_for("tier1_deep")


def test_전개된_arm이_3키를_함께_싣는다() -> None:
    arms = axes.expand_ofat([axes.ladder_axis()])

    assert arms[0]["env"] == {}
    assert [a["arm_id"] for a in arms[1:]] == [f"S2-{LADDER}-{L2}", f"S2-{LADDER}-{L3}"]
    assert all(set(a["env"]) == set(axes.LADDER_ENV_KEYS) for a in arms[1:])


def test_사다리_3키는_단일_키_축으로_중복_전개되지_않는다() -> None:
    """같은 설정을 두 축이 흔들면 구간이 갈리고 판정이 서로 모순된다."""
    selected, decisions = axes.select_axes()

    assert {a.env_key for a in selected} & set(axes.LADDER_ENV_KEYS) == set()
    claimed = [d for d in decisions if d.env_key in axes.LADDER_ENV_KEYS]
    assert claimed and all(not d.included and LADDER in d.reason for d in claimed)


def test_구조_축이_목록_맨_앞이다() -> None:
    selected, _ = axes.select_axes()

    assert selected[0].env_key == LADDER


def test_노브만_넘긴_호출에는_구조_축이_끼지_않는다() -> None:
    """*"이 노브들로 축을 뽑아라"* 에 노브가 아닌 축을 끼워 넣지 않는다."""
    from scripts.bench import catalog

    selected, _ = axes.select_axes(catalog.load_knobs()[:20])

    assert all(a.env_key != LADDER for a in selected)


# ── 2. 첫 구간 배치 ──────────────────────────────────────────────────


def _arms_with_ladder(others: dict[str, int]) -> list[sweep.ArmSpec]:
    axis = axes.ladder_axis()
    out = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None)]
    for level in axis.levels:
        out.append(sweep.ArmSpec(arm_id=f"S2-{LADDER}-{level}", axis=LADDER, level=level,
                                 env=axis.env_for(level)))
    for name, n in others.items():
        for i in range(n):
            out.append(sweep.ArmSpec(arm_id=f"S2-{name}-{i}", axis=name, level=str(i),
                                     env={name: str(i)}))
    return out


def _cats(others: dict[str, int]) -> dict[str, str]:
    return {LADDER: axes.LADDER_CATEGORY, **{k: "x" for k in others}}


def test_단_축_구간이_보정_구간보다_앞이다() -> None:
    others = {"A": 2, "B": 2}
    plan = cm.plan_segments(_arms_with_ladder(others), _cats(others), rate=_rate(1.0),
                            max_hours=10, calibrate=True, first_axis=LADDER)

    assert plan.segments[0].axes == (LADDER,)
    assert plan.segments[0].structural and not plan.segments[0].calibration
    assert plan.segments[0].segment_id == f"{axes.LADDER_CATEGORY}-1"
    assert any(s.calibration for s in plan.segments[1:]), "보정 구간은 그 다음이다"
    # 구조 축이 다른 구간에 다시 담기지 않는다.
    assert sum(1 for s in plan.segments if LADDER in s.axes) == 1


def test_first_axis가_없으면_종전_그대로다() -> None:
    """구조 축을 못 읽는 캠페인은 종전 계획이다 — 조용히 막지 않는다."""
    others = {"A": 2, "B": 2, "C": 2}
    plan = cm.plan_segments(_arms_with_ladder(others), _cats(others), rate=_rate(1.0),
                            max_hours=10, calibrate=True)

    assert not any(s.structural for s in plan.segments)
    assert plan.segments[0].calibration


def test_예산을_넘어도_구간_불가로_떨구지_않는다() -> None:
    """빼면 캠페인이 성립하지 않는다 — 사유를 달아 사람에게 넘긴다(D-250 주의 ②)."""
    others = {"A": 2}
    plan = cm.plan_segments(_arms_with_ladder(others), _cats(others), rate=_rate(5.0),
                            max_hours=10, first_axis=LADDER)

    first = plan.segments[0]
    assert first.structural and first.over_budget
    assert first.est_hours > plan.budget_hours
    assert LADDER not in dict(plan.unplaceable), "「구간 불가」로 빠지지 않는다"


def test_계획표에_예산_초과와_승자가_보인다(tmp_path) -> None:
    others = {"A": 2}
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    plan = cm.plan_segments(_arms_with_ladder(others), _cats(others), rate=_rate(5.0),
                            max_hours=10, first_axis=LADDER)

    text = "\n".join(cm.render_plan(camp, plan))
    assert "구조 축" in text and "예산 초과" in text and "--max-hours" in text
    assert "사다리 단: **미측정**" in text

    camp.tier_decision = {"segment_id": "ladder-1", "level": L3, "tier": TIER3,
                          "verdict": compare.BEST_LEVEL,
                          "env": axes.ladder_axis().env_for(L3)}
    text = "\n".join(cm.render_plan(camp, plan))
    assert f"**`{L3}`**(단 `{TIER3}`) 승" in text
    assert "ENABLE_INTENT_ORCHESTRATION=false" in text


# ── 3·4·5. 관문 — 기대 단 ────────────────────────────────────────────


def _tiers(**by_arm: str) -> tuple[tuple[str, str, str], ...]:
    return tuple((f"p+{arm}", arm, tier) for arm, tier in by_arm.items())


def test_단_축_구간에서는_단이_갈려도_정상이다() -> None:
    health = _health(tier_axis=True,
                     tiers=_tiers(baseline=TIER2, **{f"S2-{LADDER}-{L2}": TIER2,
                                                     f"S2-{LADDER}-{L3}": TIER3}))

    assert health.tier_problems() == []
    assert health.qualification_problems() == []
    # 그 대신 103 잔여 대조 주의가 붙는다(D-250 주의 ①).
    assert any("plans/103" in n for n in health.tier_notes())


def test_단_축_구간이_아니면_단_갈림은_종전대로_사고다() -> None:
    health = _health(tiers=_tiers(baseline=TIER2, **{"S2-A-1": TIER3}))

    assert any("프로파일마다 다르다" in p for p in health.tier_problems())


def test_기대_단과_다르면_차단한다() -> None:
    """승자 주입 뒤 구간 — 실제 단이 승자와 다르면 주입이 먹지 않았거나 강등됐다."""
    health = _health(tiers=_tiers(baseline=TIER3, **{"S2-A-1": TIER2}),
                     expected_tiers={"baseline": TIER3, "S2-A-1": TIER3})

    problems = health.tier_problems()
    assert problems and "기대한 단과 다르다" in problems[0]
    assert "S2-A-1" in problems[0] and TIER2 in problems[0]
    assert problems == health.qualification_problems()
    assert set(health.qualification_problems()) <= set(health.warnings())


def test_기대_단과_같으면_통과한다() -> None:
    health = _health(tiers=_tiers(baseline=TIER3, **{"S2-A-1": TIER3}),
                     expected_tiers={"baseline": TIER3, "S2-A-1": TIER3})

    assert health.tier_problems() == [] and health.stop_reasons() == []


def test_단_축_구간에서도_레벨이_기대와_다르면_차단한다() -> None:
    """레벨마다 자기 단이 있다 — `tier2_intent` arm 이 3단으로 떴으면 주입 실패다."""
    health = _health(tier_axis=True,
                     tiers=_tiers(**{f"S2-{LADDER}-{L2}": TIER3, f"S2-{LADDER}-{L3}": TIER3}),
                     expected_tiers={f"S2-{LADDER}-{L2}": TIER2, f"S2-{LADDER}-{L3}": TIER3})

    assert any("기대한 단과 다르다" in p for p in health.tier_problems())


def test_기대가_비면_추정하지_않는다() -> None:
    """그래프를 못 떴으면 종전 규칙(단 갈림=사고)만 적용한다."""
    health = _health(tiers=_tiers(baseline=TIER2, **{"S2-A-1": TIER2}))

    assert health.tier_problems() == []


# ── tier_context — 기대 단은 스냅샷에서만 읽는다 ─────────────────────


def _snapshot(*, base_injected=None, base_reach=None, arms=()) -> sweep.ConfigSnapshot:
    baseline = sweep.ArmConfig(arm_id="baseline", axis=None, level=None,
                               injected=dict(base_injected or {}), effective={"k": "v"},
                               reachable=base_reach)
    return sweep.ConfigSnapshot(baseline=baseline, arms={a.arm_id: a for a in arms})


def _arm_config(arm_id, axis, level, reachable) -> sweep.ArmConfig:
    return sweep.ArmConfig(arm_id=arm_id, axis=axis, level=level, injected={},
                           effective={"k": arm_id}, reachable=reachable)


ROUTER_NODES = ("input_parser", "semantic_router", "schema_analyzer")
INTENT_NODES = ("input_parser", "intent_planner", "agent_orchestrator")


def test_tier_context_단_축_구간은_arm마다_자기_레벨의_단이다() -> None:
    arms = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
            sweep.ArmSpec(arm_id=f"S2-{LADDER}-{L2}", axis=LADDER, level=L2),
            sweep.ArmSpec(arm_id=f"S2-{LADDER}-{L3}", axis=LADDER, level=L3)]
    snapshot = _snapshot(base_reach=INTENT_NODES, arms=[
        _arm_config(f"S2-{LADDER}-{L2}", LADDER, L2, INTENT_NODES),
        _arm_config(f"S2-{LADDER}-{L3}", LADDER, L3, ROUTER_NODES)])

    tier_axis, expected = sweep.tier_context(arms, snapshot)

    assert tier_axis
    assert expected == {"baseline": TIER2, f"S2-{LADDER}-{L2}": TIER2,
                        f"S2-{LADDER}-{L3}": TIER3}


def test_tier_context_주입_뒤_구간은_전_arm이_기준선의_단이다() -> None:
    arms = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
            sweep.ArmSpec(arm_id="S2-A-1", axis="A", level="1")]
    snapshot = _snapshot(base_injected=axes.ladder_axis().env_for(L3), base_reach=ROUTER_NODES)

    tier_axis, expected = sweep.tier_context(arms, snapshot)

    assert not tier_axis
    assert expected == {"baseline": TIER3, "S2-A-1": TIER3}


def test_tier_context_주입이_없으면_기대도_없다() -> None:
    arms = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
            sweep.ArmSpec(arm_id="S2-A-1", axis="A", level="1")]

    assert sweep.tier_context(arms, _snapshot(base_reach=ROUTER_NODES)) == (False, {})
    assert sweep.tier_context(arms, None) == (False, {})


def test_tier_context_그래프를_못_뜨면_기대가_비고_추정하지_않는다() -> None:
    arms = [sweep.ArmSpec(arm_id=f"S2-{LADDER}-{L2}", axis=LADDER, level=L2)]
    snapshot = _snapshot(arms=[_arm_config(f"S2-{LADDER}-{L2}", LADDER, L2, None)])

    tier_axis, expected = sweep.tier_context(arms, snapshot)

    assert tier_axis and expected == {}


def test_사다리_arm은_도달_불가로_판정되지_않는다() -> None:
    """도달성(M-3)은 **노드 안에서 읽는 플래그**용이다 — 단 자체를 바꾸는 축은 대상이 아니다."""
    assert LADDER not in sweep.GRAPH_REGISTRATION_FLAGS
    snapshot = _snapshot(base_reach=INTENT_NODES, arms=[
        _arm_config(f"S2-{LADDER}-{L2}", LADDER, L2, INTENT_NODES)])

    assert snapshot.unreachable_arms() == {}


# ── continuity — 구조 축 뒤의 단 변경은 정상 ─────────────────────────


def _record(sid, *, tier, structural=False) -> cm.SegmentRecord:
    return cm.SegmentRecord(segment_id=sid, category="x", axes=[sid], arm_ids=[],
                            status=cm.DONE, mode="run", baseline_tier=tier,
                            structural=structural, finished_at=f"2026-09-23T0{len(sid)}:00:00")


def test_구조_축_뒤의_단_변경은_멈추지_않고_고지한다() -> None:
    stop, notes = cm.continuity(_record("ladder-1", tier=TIER2, structural=True),
                               _record("x-1", tier=TIER3))

    assert stop == []
    assert notes and "이라 정상이다" in notes[0] and "D-250 ②" in notes[0]


def test_구조_축이_아니면_단_변경은_멈춤이다() -> None:
    stop, _ = cm.continuity(_record("x-1", tier=TIER2), _record("y-1", tier=TIER3))

    assert stop and "캠페인 도중 단이 바뀌면" in stop[0]


# ── 처분 제안 — 다중 키 전개 ─────────────────────────────────────────


def _opt(axis, verdict, *, best=None, levels=("a", "b")):
    return compare.AxisOptimum(axis=axis, levels=levels, verdict=verdict, best_level=best,
                               signal="정확도", sentence="근거")


def test_권고_diff는_축_id가_아니라_실제_키로_적는다() -> None:
    body = optimize.render_recommended_env(
        [_opt(LADDER, compare.BEST_LEVEL, best=L3, levels=(L2, L3))],
        baseline_effective={"ENABLE_INTENT_ORCHESTRATION": "true",
                            "ENABLE_SEMANTIC_ROUTING": "true",
                            "ENABLE_DEEPAGENTS_PACKAGE": "false"})

    assert f"{LADDER}=" not in body, "축 id 는 env 키가 아니다"
    assert "ENABLE_INTENT_ORCHESTRATION=false" in body
    assert "ENABLE_SEMANTIC_ROUTING=true" in body
    assert "다중 키 축" in body


def test_권고_diff는_구조_축을_1순위로_싣는다() -> None:
    body = optimize.render_recommended_env(
        [_opt("A_KNOB", compare.BEST_LEVEL, best="true"),
         _opt(LADDER, compare.BEST_LEVEL, best=L3, levels=(L2, L3))],
        baseline_effective={})

    assert body.index(LADDER) < body.index("A_KNOB")


def test_이미_그_단이면_변경으로_적지_않는다() -> None:
    body = optimize.render_recommended_env(
        [_opt(LADDER, compare.BEST_LEVEL, best=L3, levels=(L2, L3))],
        baseline_effective=axes.ladder_axis().env_for(L3))

    assert "이미 권고값" in body and "권고 변경 0건" in body


# ── 3·6. `--segment` 흐름 — 승자 주입 · 판정 불가 멈춤 ───────────────


@pytest.fixture()
def ladder_seg(monkeypatch, tmp_path):
    """단 축 1개 + 다른 축 2개. 스위프는 가짜이고 판정도 주입한다."""
    from scripts.bench import __main__ as cli

    others = {"A": 2, "B": 2}
    arms = _arms_with_ladder(others)
    monkeypatch.setattr(cli, "_RESULTS_DIR", tmp_path / "bench")
    monkeypatch.setattr(sweep, "build_arms", lambda limit=None: list(arms))
    monkeypatch.setattr(sweep, "axis_categories", lambda tier="primary": _cats(others))
    # 소비처 없음(F3 · plans/118 B-3) 제외 축은 실 저장소 판정이다 — 가짜 축 캠페인에 섞지 않는다.
    monkeypatch.setattr(sweep, "excluded_axes", lambda tier="primary": {})
    monkeypatch.setattr(sweep, "planned_turns_per_arm", lambda catalog: 100)
    monkeypatch.setattr(sweep, "load_normal_catalog", lambda env="closed": object())
    monkeypatch.setattr(sweep, "resolve_env", lambda explicit=None: ("closed", "테스트"))
    monkeypatch.setattr(cli, "_sweep_proposals", lambda *a, **kw: {})

    def fake_snapshot(snap_arms, **kw):
        base = sweep.ArmConfig(arm_id="baseline", axis=None, level=None,
                               injected=dict(next((a.env for a in snap_arms
                                                   if a.arm_id == "baseline"), {})),
                               effective={"k": "base"})
        cfg = {a.arm_id: sweep.ArmConfig(arm_id=a.arm_id, axis=a.axis, level=a.level,
                                         injected=dict(a.env), effective={"k": a.arm_id})
               for a in snap_arms if a.axis}
        return sweep.ConfigSnapshot(baseline=base, arms=cfg)

    monkeypatch.setattr(sweep, "capture_config_snapshot", fake_snapshot)

    calls: list[list[str]] = []
    envs: list[dict] = []
    outcomes: list = []

    def fake_run(args, run_arms, *, label, snapshot=None, substituted=(), run_id="",
                 resume_from=None, on_start=None, **_extra):
        calls.append([a.arm_id for a in run_arms])
        envs.append({a.arm_id: dict(a.env) for a in run_arms})
        if on_start:
            on_start()
        out = tmp_path / f"run{len(calls)}"
        out.mkdir()
        rows = [{"profile": a.arm_id, "scenario_id": f"S{i}", "turn": 0, "repeat": 0,
                 "func_verdict": "pass", "response_mode": "answer", "executed_sql": "SELECT 1",
                 "node_path": ["q"], "wall_ms": 100}
                for a in run_arms for i in range(3)]
        (out / "raw.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        health, extra = outcomes.pop(0) if outcomes else (_health(), {})
        return cli.SweepOutcome(rc=0, ran=True, out_dir=out, health=health, elapsed_sec=36.0,
                                **extra)

    monkeypatch.setattr(cli, "run_sweep", fake_run)

    def run(*argv):
        return cli.main(["--segment", *argv])

    run.envs = envs
    return run, calls, outcomes, tmp_path / "bench" / "campaigns"


def _won(level=L3, verdict=compare.BEST_LEVEL, tier=TIER3):
    """단 축 구간의 결과 — 판정 + 관측된 단."""
    health = _health(tiers=_tiers(baseline=TIER2,
                                  **{f"S2-{LADDER}-{L2}": TIER2, f"S2-{LADDER}-{L3}": TIER3}),
                     tier_axis=True)
    return health, {"optima": [_opt(LADDER, verdict,
                                    best=level if verdict == compare.BEST_LEVEL else None,
                                    levels=(L2, L3))]}


def test_첫_구간이_단_축이고_승자가_남은_구간에_주입된다(ladder_seg) -> None:
    run, calls, outcomes, root = ladder_seg
    outcomes.append(_won())

    assert run("next", "--mode", "mock") == 0
    assert calls[0] == ["baseline", f"S2-{LADDER}-{L2}", f"S2-{LADDER}-{L3}"], "첫 구간 = 단 축"

    state = _state(root, "mock-closed")
    decision = state["tier_decision"]
    assert decision["level"] == L3 and decision["tier"] == TIER3
    assert decision["env"]["ENABLE_INTENT_ORCHESTRATION"] == "false"
    assert state["records"][0]["structural"] is True

    assert run("next", "--mode", "mock") == 0
    # **전 arm** 에 주입된다 — 기준선만 바꾸면 변이 arm 이 `.env` 의 단으로 돈다.
    for arm_id, env in run.envs[1].items():
        assert env["ENABLE_INTENT_ORCHESTRATION"] == "false", arm_id
        assert env["ENABLE_SEMANTIC_ROUTING"] == "true", arm_id
    # arm 자신의 축 값은 그대로다.
    assert run.envs[1][[a for a in calls[1] if a != "baseline"][0]].get("A") is not None \
        or run.envs[1][[a for a in calls[1] if a != "baseline"][0]].get("B") is not None


def test_레벨_간_차이가_없으면_기준_경로_2단을_쓴다(ladder_seg) -> None:
    """D-251 — 기준 경로는 2단이다(D-225 의 3단을 개정 · plans/118 B-5 가 정본을 읽게 고쳤다)."""
    run, _, outcomes, root = ladder_seg
    outcomes.append(_won(verdict=compare.LEVELS_TIED))

    assert run("next", "--mode", "mock") == 0

    decision = _state(root, "mock-closed")["tier_decision"]
    assert sweep.canonical_tier() == TIER2
    assert decision["level"] == L2 and decision["tier"] == TIER2
    assert "기준 경로" in decision["sentence"] and "D-251" in decision["sentence"]


def test_판정_불가면_기준_경로로_고정하고_고지와_함께_계속_돈다(ladder_seg, capsys) -> None:
    """「판정 불가」는 차이를 **보이지 못한 것**이라 「차이 없음」과 같은 처분을 쓴다(D-250 ②).

    멈추면 반복 1회 설계에서 캠페인 전체가 진행되지 못한다(collectorinfra-d9 2026-09-23 지적 ·
    run 20260914 는 전 축이 판정 불가였다). 대신 승자를 **재서 고른 것이 아니라는** 사실을
    `caveat` 로 남겨 이후 판정문·계획 표·합산 리포트에 싣는다.
    """
    run, calls, outcomes, root = ladder_seg
    outcomes.append(_won(verdict=compare.UNDERPOWERED))

    assert run("next", "--mode", "mock") == 0
    decision = _state(root, "mock-closed")["tier_decision"]
    assert "blocked" not in decision
    assert decision["level"] == L2 and decision["tier"] == sweep.canonical_tier() == TIER2
    assert "기준 경로" in decision["sentence"] and compare.UNDERPOWERED in decision["caveat"]
    capsys.readouterr()

    assert run("next", "--mode", "mock") == 0, "고지와 함께 남은 구간을 계속 돈다"
    out = capsys.readouterr().out
    assert "재서 고른 것이 아니라" in out and "조건부" in out
    assert len(calls) == 2


def test_단_축_구간_전에는_다른_구간을_돌리지_않는다(ladder_seg, capsys) -> None:
    run, calls, _, _ = ladder_seg

    assert run("x-1", "--mode", "mock") == 1
    out = capsys.readouterr().out
    assert "사다리 단 축 구간이 아직 끝나지 않았습니다" in out and "D-250 ①" in out
    assert calls == []


def test_단_축_구간이_실패하면_승자를_정하지_않는다(ladder_seg) -> None:
    run, _, outcomes, root = ladder_seg
    health, extra = _won()
    outcomes.append((_health(clarify_turns=120, tier_axis=True), extra))   # 역질문 60% → 실패

    assert run("next", "--mode", "mock") == 1
    decision = _state(root, "mock-closed")["tier_decision"]
    assert "level" not in decision and "실패로 끝났다" in decision["blocked"]


def test_합산_리포트에_승자와_103_주의가_실린다(ladder_seg) -> None:
    run, _, outcomes, root = ladder_seg
    outcomes.append(_won())

    assert run("next", "--mode", "mock") == 0

    body = (root / "mock-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    assert f"`{L3}`(단 `{TIER3}`) 승" in body
    assert "남은 구간 기준선에 주입했다" in body
    assert "plans/103" in body and "103 잔여와 대조해 읽을 것" in body
    # 단 축 행이 표 맨 위다.
    assert body.index(f"| `{LADDER}` |") < body.index("## 구간")


def test_dry는_첫_행이_단_축_구간이고_아무것도_돌리지_않는다(ladder_seg, capsys) -> None:
    run, calls, _, root = ladder_seg

    assert run("next", "--mode", "dry") == 0
    out = capsys.readouterr().out

    assert calls == [] and not root.exists()
    first = next(ln for ln in out.splitlines() if f"{axes.LADDER_CATEGORY}-1" in ln)
    assert "구조 축" in first and LADDER in first
    assert out.index(f"{axes.LADDER_CATEGORY}-1") < out.index("x-1"), "계획표 첫 행이다"
    assert "사다리 단: **미측정**" in out


# ── 벤치 소유 검토 ① — 단 축 구간의 타임아웃은 실패가 아니라 고지 ──────────


def _timeout_health(*, tier_axis: bool, rate: float = 0.43):
    turns = 223
    return _health(turns=turns, tier_axis=tier_axis,
                   unevaluated={"timeout": round(turns * rate), "invalid": 0,
                                "clarify_blocked": 0},
                   tiers=_tiers(baseline=TIER2, **{f"S2-{LADDER}-{L2}": TIER2,
                                                   f"S2-{LADDER}-{L3}": TIER3}))


def test_단_축_구간은_타임아웃률로_실패하지_않는다() -> None:
    """★교착 방지 — 첫 구간이 자기 관문에 걸리면 승자를 못 뽑아 캠페인 전체가 막힌다.

    1구간 실측 42.6%였고 3단에서도 텍스트 타임아웃 93턴 중 최소 59턴이 60초를 넘는다(§2.4).
    """
    health = _timeout_health(tier_axis=True)

    assert health.timeout_rate >= sweep.TIMEOUT_STOP, "임계는 넘는다"
    assert health.timeout_problem(), "값 자체는 계속 낸다"
    assert health.qualification_problems() == [], "구간 실패로 세지 않는다"
    assert [r for r in health.stop_reasons() if "타임아웃" in r] == []


def test_단_축_구간의_타임아웃은_고지로_남는다() -> None:
    """조용히 빼지 않는다 — 빠뜨리면 42.6% 구간이 표시 없이 「완료」로 지나간다."""
    notes = [n for n in _timeout_health(tier_axis=True).warnings() if "타임아웃" in n]

    assert len(notes) == 1
    assert "단 축 구간이라 실패로 세지 않는다" in notes[0]
    assert "승자 판정의 완주율·지연으로 읽을 것" in notes[0]


def test_단_축이_아닌_구간은_종전대로_타임아웃이_실패다() -> None:
    """예외는 사다리 축 구간에만 걸린다 — 그 뒤 구간은 종전대로다."""
    health = _timeout_health(tier_axis=False)

    assert [r for r in health.qualification_problems() if "타임아웃" in r]
    assert [r for r in health.stop_reasons() if "타임아웃" in r]


def test_승자_판정에_타임아웃이_실린다(ladder_seg) -> None:
    run, _, outcomes, root = ladder_seg
    health, extra = _won()
    outcomes.append((_timeout_health(tier_axis=True), extra))

    assert run("next", "--mode", "mock") == 0

    decision = _state(root, "mock-closed")["tier_decision"]
    assert decision["turns"] == 223 and decision["timeout_turns"] == 96
    assert decision["timeout_rate"] == pytest.approx(0.4305, abs=1e-3)


# ── 벤치 소유 검토 ② — 재계획 속도는 승자 arm 의 턴만으로 ────────────────


def _raw(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "raw.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return path


def test_arm_rate는_그_arm의_턴만_센다(tmp_path) -> None:
    """단 축 구간에는 속도가 다른 두 단이 섞인다 — 구간 평균을 쓰면 진 단이 섞인다."""
    rows = ([{"arm": "fast", "scenario_id": f"S{i}", "turn": 0, "repeat": 0, "wall_ms": 1000}
             for i in range(10)]
            + [{"arm": "slow", "scenario_id": f"T{i}", "turn": 0, "repeat": 0, "wall_ms": 3000}
               for i in range(10)])

    fast = sweep.arm_rate(_raw(tmp_path, rows), "fast", elapsed_sec=400.0)
    slow = sweep.arm_rate(_raw(tmp_path, rows), "slow", elapsed_sec=400.0)

    # 경과 400초를 wall 비율(1:3)로 나눠 각자 턴 수로 나눈다 — 구간 평균 20초/턴이 아니다.
    assert fast == (pytest.approx(10.0), 10)
    assert slow == (pytest.approx(30.0), 10)


def test_arm_rate는_못_재면_None이다(tmp_path) -> None:
    rows = [{"arm": "a", "scenario_id": "S", "turn": 0, "repeat": 0, "wall_ms": 100}]
    assert sweep.arm_rate(_raw(tmp_path, rows), "없는arm", 100.0) is None
    assert sweep.arm_rate(_raw(tmp_path, rows), "a", 0.0) is None
    assert sweep.arm_rate(tmp_path / "없다.jsonl", "a", 100.0) is None


def test_단_축_구간_뒤_재계획은_승자_arm_실측을_쓴다(tmp_path) -> None:
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    camp.records["ladder-1"] = cm.SegmentRecord(
        segment_id="ladder-1", category="ladder", axes=[LADDER], arm_ids=[f"S2-{LADDER}-{L3}"],
        status=cm.DONE, mode="run", elapsed_sec=7200.0, turns=200,
        finished_at="2026-09-23T10:00:00", structural=True)
    camp.tier_decision = {"level": L3, "winner_sec_per_turn": 12.5, "winner_turns_per_arm": 130}

    rate = camp.rate(129)

    assert rate.sec_per_turn == 12.5 and rate.turns_per_arm == 130
    assert "승자 arm" in rate.source and "구간 평균을 쓰지 않는다" in rate.source


def test_승자_실측이_없으면_섞인_평균_대신_기본값이다(tmp_path) -> None:
    """구간 평균(36초/턴)으로 내려가면 지지 않은 단의 속도가 섞인다."""
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    camp.records["ladder-1"] = cm.SegmentRecord(
        segment_id="ladder-1", category="ladder", axes=[LADDER], arm_ids=[],
        status=cm.DONE, mode="run", elapsed_sec=7200.0, turns=200,
        finished_at="2026-09-23T10:00:00", structural=True)

    rate = camp.rate(129)

    assert rate.sec_per_turn == cm.DEFAULT_SEC_PER_TURN
    assert "단 축이라 구간 평균을 쓰지 않는다" in rate.source


def test_단_축이_아닌_구간은_종전대로_구간_평균이다(tmp_path) -> None:
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    camp.records["x-1"] = cm.SegmentRecord(
        segment_id="x-1", category="x", axes=["A"], arm_ids=["S2-A-0"], status=cm.DONE,
        mode="run", elapsed_sec=6000.0, turns=100, finished_at="2026-09-23T10:00:00")

    assert camp.rate(129).sec_per_turn == pytest.approx(60.0)


# ── 벤치 소유 검토 ③⑤ — 노이즈 바닥 제외 고지 · 묶음 축 귀속 ─────────────


def test_단_축_구간은_노이즈_바닥에_기여하지_않는다(ladder_seg) -> None:
    run, _, outcomes, root = ladder_seg
    outcomes.append(_won())

    assert run("next", "--mode", "mock") == 0
    assert run("next", "--mode", "mock") == 0

    body = (root / "mock-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    assert "단 축 구간은 노이즈 바닥에 기여하지 않는다" in body
    assert "`ladder-1`" in body
    # 두 구간이 끝났지만 기준선 반복은 1회뿐이라 노이즈 바닥 줄이 뜨지 않는다.
    assert "기준선 반복 2회" not in body


def test_묶음_축_귀속이_판정표에_박힌다(ladder_seg) -> None:
    """레벨 하나가 3키를 함께 정한다 — 델타를 개별 키 효과로 읽으면 안 된다."""
    run, _, outcomes, root = ladder_seg
    outcomes.append(_won())

    assert run("next", "--mode", "mock") == 0

    body = (root / "mock-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    assert "플래그 3종 묶음" in body and "개별 키의 효과가 아니다" in body
    for key in axes.LADDER_ENV_KEYS:
        assert key in body


def test_귀속_문구는_3키를_모두_적는다() -> None:
    for key in axes.LADDER_ENV_KEYS:
        assert key in sweep.TIER_AXIS_ATTRIBUTION
    assert "한 키만 바꾼 값은 이 측정의 어느 레벨도 아니다" in sweep.TIER_AXIS_ATTRIBUTION


def test_기준선과_같은_레벨은_다중_키_축에서도_대조군이다() -> None:
    """지문 일치 판정은 축 종류와 무관하다 — 그 레벨은 실행을 생략하고 기준선 관측을 쓴다."""
    axis = axes.ladder_axis()
    same = {"a": 1, "b": 2}
    snapshot = sweep.ConfigSnapshot(
        baseline=sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                                 effective=same),
        arms={f"S2-{LADDER}-{L2}": sweep.ArmConfig(
                  arm_id=f"S2-{LADDER}-{L2}", axis=LADDER, level=L2,
                  injected=axis.env_for(L2), effective=same),
              f"S2-{LADDER}-{L3}": sweep.ArmConfig(
                  arm_id=f"S2-{LADDER}-{L3}", axis=LADDER, level=L3,
                  injected=axis.env_for(L3), effective={"a": 9, "b": 2})})

    assert snapshot.control_arms() == [f"S2-{LADDER}-{L2}"]
