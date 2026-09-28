"""plans/120 트랙 V(하네스) — 완주 정의 · 생략 대조군의 단 · 합성 축 오보 · 미측정 처분 · 단 인용.

근거 run `20260923-140539`(캠페인 `run-closed` 구간 `ladder-1` · 226턴).

  V-2  완주 = 응답 모드가 오류·크래시·행이 아니고 타임아웃 미평가가 아니다 — **기능 불합격은
       완주다**. 종전 정의로 3단 완주율이 −14.6%p 였고 교정 정의로 +6.8%p(2:9 · p=0.065)다.
  V-3  실행 생략 arm(기준선과 같은 설정)의 단 = 기준선의 관측 단. 종전에는 `tier: null` 이었고
       판정이 「차이 없음」·「판정 불가」였다면 캠페인이 `blocked` 였다.
  V-5  `LADDER_TIER`(합성 축)는 "카탈로그에 없는 축"이 아니다 · 재지 않은 축은 「검정력 부족」이
       아니라 「미측정 — 구간 미완」으로 처분한다.
  G-1  새 캠페인이 단 축 구간을 돌지 않고 이 run 의 보정 판정을 **인용**해 단을 고정한다.

서버·LLM·DB 0 — 스위프는 가짜다. 실 run 산출물(`results/` · git 미추적)이 있으면 읽기만 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.bench import axes, compare, optimize, sweep
from scripts.bench import campaign as cm
from tests.test_scripts import test_bench_plan114_m0_tier_axis as m0_tests
from tests.test_scripts import test_bench_sweep as sweep_tests
from tests.test_scripts.test_bench_campaign import _health, _state
from tests.test_scripts.test_bench_sweep import _sweep_args, _write_raw

#: 단 축 캠페인 흐름(가짜 스위프) · 게이트 뒤 실행 가로채기 픽스처를 그대로 쓴다.
ladder_seg = m0_tests.ladder_seg
gate = sweep_tests.gate

_ROOT = Path(__file__).resolve().parent.parent.parent
LADDER = axes.LADDER_AXIS
TIER2 = "intent_orchestration"
TIER3 = "semantic_router"
L2, L3 = "tier2_intent", "tier3_router"
ARM2, ARM3 = f"S2-{LADDER}-{L2}", f"S2-{LADDER}-{L3}"
CITATION = _ROOT / "scripts" / "bench" / "citations" / "ladder-1-20260923-140539.json"
RUN_DIR = _ROOT / "results" / "bench" / "run-closed-20260928-triage.tar" / "20260923-140539"


# ── V-2 완주 정의 ────────────────────────────────────────────────────


def _row(sid: str, **over) -> dict:
    row = {"profile": "a", "scenario_id": sid, "turn": 0, "repeat": 0,
           "func_verdict": "pass", "response_mode": "answer", "wall_ms": 10}
    row.update(over)
    return row


@pytest.mark.parametrize(("over", "completed"), [
    ({"func_verdict": "fail"}, True),                                   # ★ 기능 불합격은 완주다
    ({"func_verdict": "manual"}, True),
    ({"func_verdict": "error"}, True),              # 판정어는 완주 판정에 쓰지 않는다(정의)
    ({"response_mode": "error"}, False),
    ({"response_mode": "crash"}, False),
    ({"response_mode": "hang", "func_verdict": "error"}, False),
    ({"func_verdict": "fail", "error": "처리 시간이 초과되었습니다."}, False),   # 타임아웃 미평가
    ({"func_verdict": "fail", "unevaluated_reason": "timeout"}, False),
    ({"func_verdict": "fail", "response_mode": "clarify"}, True),       # 역질문 차단은 완주다
])
def test_완주는_응답_모드와_타임아웃으로만_정한다(tmp_path, over, completed) -> None:
    obs = sweep.read_observations(_write_raw(tmp_path, [_row("S1", **over)]))

    assert obs[0].completed is completed


def test_완주율은_정확도의_그림자가_아니다(tmp_path) -> None:
    """기준선이 전부 불합격 · 변형이 전부 합격이어도 둘 다 끝까지 답했으면 완주율 차이는 0이다."""
    rows = ([_row(f"S{i}", profile="baseline", func_verdict="fail") for i in range(6)]
            + [_row(f"S{i}", profile="S2-K-1", func_verdict="pass") for i in range(6)])
    grouped = sweep.group_by_arm(sweep.read_observations(_write_raw(tmp_path, rows)))

    accuracy = compare.paired_binary(grouped["baseline"], grouped["S2-K-1"], "passed")
    completion = compare.paired_binary(grouped["baseline"], grouped["S2-K-1"], "completed",
                                       drop_manual=False)
    assert accuracy.discordant == 6
    assert completion.n_pairs == 6 and completion.discordant == 0


@pytest.mark.skipif(not (RUN_DIR / "raw.jsonl").exists(),
                    reason="실 run 산출물(results/ · git 미추적)이 없다")
def test_실_run_3단_완주율이_교정_정의로_뒤집힌다() -> None:
    """★ 읽기만 한다 — 새 정의는 단언 결과와 무관하므로 `db_ids` 보정(V-1) 없이 같은 값이다."""
    grouped = sweep.group_by_arm(sweep.read_observations(RUN_DIR / "raw.jsonl"))
    base, tier3 = grouped["baseline"], grouped[ARM3]

    completion = compare.paired_binary(base, tier3, "completed", drop_manual=False)
    assert (completion.n_pairs, completion.only_baseline_passed,
            completion.only_variant_passed) == (103, 2, 9)
    assert completion.delta_pp == 6.8 and round(completion.p_value, 3) == 0.065
    # 정확도는 V-2 와 무관하다(보정 전 값 그대로 — V-1 은 시나리오 하네스 몫).
    accuracy = compare.paired_accuracy(base, tier3)
    assert (accuracy.only_baseline_passed, accuracy.only_variant_passed) == (14, 0)


# ── V-3 생략 대조군 레벨의 단 ─────────────────────────────────────────


def _tiers(**by_arm: str) -> tuple[tuple[str, str, str], ...]:
    return tuple((f"p+{arm}", arm, tier) for arm, tier in by_arm.items())


def test_생략_arm은_기준선의_관측_단을_싣는다() -> None:
    health = _health(tier_axis=True, tiers=_tiers(baseline=TIER2, **{ARM3: TIER3}),
                     substituted=(ARM2,))

    assert health.tier_by_arm() == {"baseline": TIER2, ARM3: TIER3, ARM2: TIER2}


def test_기준선_단을_모르면_생략_arm도_싣지_않는다() -> None:
    """기준선 단이 프로파일마다 갈렸거나 관측이 없으면 추정하지 않는다."""
    health = _health(tier_axis=True,
                     tiers=(("baseline", "baseline", TIER2), ("optin+baseline", "baseline", TIER3),
                            ("p3", ARM3, TIER3)),
                     substituted=(ARM2,))

    assert ARM2 not in health.tier_by_arm()


def _outcome(verdict: str, *, substituted: bool = True):
    from scripts.bench import __main__ as cli

    health = _health(tier_axis=True, tiers=_tiers(baseline=TIER2, **{ARM3: TIER3}),
                     substituted=(ARM2,) if substituted else ())
    optimum = compare.AxisOptimum(axis=LADDER, levels=(L2, L3), verdict=verdict,
                                  best_level=L2 if verdict == compare.BEST_LEVEL else None,
                                  signal="정확도", sentence="모의 판정")
    segment = cm.Segment(segment_id="ladder-1", category="ladder", axes=(LADDER,),
                         arm_ids=(ARM3,), est_hours=1.0, substituted=(ARM2,), structural=True)
    camp = cm.Campaign(name="t", path=Path("/없음/c.json"), env="closed", mode="run")
    outcome = cli.SweepOutcome(rc=0, ran=True, health=health, optima=[optimum],
                               substituted=[ARM2])
    return cli._decide_tier_winner(camp, segment, outcome)


@pytest.mark.parametrize("verdict", [compare.BEST_LEVEL, compare.LEVELS_TIED,
                                     compare.UNDERPOWERED])
def test_대조군이_생략돼도_세_분기_모두_기준_경로_단을_찾는다(verdict) -> None:
    decision = _outcome(verdict)

    assert "blocked" not in decision
    assert decision["level"] == L2 and decision["tier"] == TIER2 == sweep.canonical_tier()


@pytest.mark.parametrize("verdict", [compare.LEVELS_TIED, compare.UNDERPOWERED])
def test_생략_arm을_모르면_종전대로_막힌다(verdict) -> None:
    """V-3 이전 동작의 재현 — plans/120 §2.8 「판정이 차이 없음·판정 불가였다면 차단」."""
    assert "blocked" in _outcome(verdict, substituted=False)


def test_run_sweep은_생략_arm을_건전성에_넘긴다(gate, monkeypatch, tmp_path) -> None:
    cli, _ = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: False)
    monkeypatch.setattr(cli, "_sweep_proposals", lambda *a, **kw: {})
    import scripts.scenario.report as sc_report
    monkeypatch.setattr(sc_report, "write_report", lambda out_dir, catalog=None: {})
    rows = [{"profile": arm, "arm": arm, "scenario_id": f"S{i}", "turn": 0, "repeat": 0,
             "func_verdict": "pass", "response_mode": "answer", "executed_sql": "SELECT 1",
             "node_path": ["q"], "wall_ms": 100}
            for arm in ("baseline", ARM3) for i in range(5)]
    _write_raw(tmp_path, rows)
    monkeypatch.setattr(sweep, "run_arms", lambda arms, **kw: {
        "out_dir": str(tmp_path),
        "profiles": [{"name": "baseline", "arm": "baseline", "valid": True, "tier": TIER2},
                     {"name": ARM3, "arm": ARM3, "valid": True, "tier": TIER3}]})
    two, three = ("context_resolver", "intent_planner"), ("context_resolver", "semantic_router")
    env2, env3 = axes.ladder_axis().env_for(L2), axes.ladder_axis().env_for(L3)
    snap = sweep.ConfigSnapshot(
        baseline=sweep.ArmConfig("baseline", None, None, {}, {"k": "2"}, reachable=two),
        arms={ARM2: sweep.ArmConfig(ARM2, LADDER, L2, env2, {"k": "2"}, reachable=two),
              ARM3: sweep.ArmConfig(ARM3, LADDER, L3, env3, {"k": "3"}, reachable=three)})
    executed = [sweep.ArmSpec("baseline", None, None), sweep.ArmSpec(ARM3, LADDER, L3, env3)]

    out = cli.run_sweep(_sweep_args(), executed, label="구간 ladder-1", snapshot=snap,
                        substituted=[sweep.ArmSpec(ARM2, LADDER, L2, env2)])

    assert out.health.substituted == (ARM2,)
    assert out.health.tier_by_arm()[ARM2] == TIER2


@pytest.mark.skipif(not (RUN_DIR / "run.json").exists(),
                    reason="실 run 산출물(results/ · git 미추적)이 없다")
def test_실_run_단_판정이_null에서_2단이_된다() -> None:
    """★ 읽기만 한다 — 이 run 의 `tier_decision.tier` 는 `null` 이었다."""
    result = json.loads((RUN_DIR / "run.json").read_text(encoding="utf-8"))
    health = sweep.scan_health(result, RUN_DIR / "raw.jsonl", tier_axis=True,
                               substituted=[ARM2])

    assert health.tier_by_arm()[ARM2] == TIER2


# ── V-5 합성 축 · 미측정 처분 ─────────────────────────────────────────


def test_합성_축은_카탈로그에_없는_축으로_보고하지_않는다(capsys, tmp_path) -> None:
    from scripts.bench import __main__ as cli

    optima = [compare.AxisOptimum(axis=axis, levels=("a", "b"), verdict=compare.LEVELS_TIED,
                                  best_level=None, signal="정확도", sentence="모의")
              for axis in (LADDER, "NO_SUCH_KEY_PLAN120")]
    cli._sweep_proposals(optima, sweep.ConfigSnapshot(
        baseline=sweep.ArmConfig("baseline", None, None, {})), tmp_path)

    out = capsys.readouterr().out
    assert "카탈로그에 없는 축 1건" in out and "NO_SUCH_KEY_PLAN120" in out
    assert LADDER not in out


def test_미측정과_검정력_부족은_처분_문구가_다르다() -> None:
    knob = sweep_tests._knob()
    unmeasured = optimize.decide(knob, verdict=compare.UNMEASURED, reference_count=5)
    weak = optimize.decide(knob, verdict=compare.UNDERPOWERED, reference_count=5)

    assert unmeasured.action == weak.action == optimize.DEFER
    assert unmeasured.reason.startswith("미측정 — 구간 미완") and "검정력" not in unmeasured.reason
    assert weak.reason == "검정력 부족 — 정적 규칙으로 폴백, 재측정 대기", \
        "쟀는데 약한 축은 그대로다"


def test_미측정_축은_처분_입력에서_미측정으로_간다() -> None:
    opt = compare.AxisOptimum(axis="K", levels=("a", "b"), verdict=compare.UNMEASURED,
                              best_level=None, signal="없음", sentence="미측정(구간 k-1 미완)")
    verdicts, recommended = optimize.axis_disposition_inputs([opt])

    assert verdicts == {"K": compare.UNMEASURED} and recommended == {}
    body = optimize.render_recommended_env([opt])
    assert "# K: 권고 없음 — 축 미측정 — 미측정(구간 k-1 미완)" in body


# ── G-1 단 인용 ─────────────────────────────────────────────────────


def test_인용_기록은_필수_칸과_보정_수치를_싣는다() -> None:
    data = cm.load_tier_citation(CITATION)

    assert data["source"] == cm.CITED and data["axis"] == LADDER
    assert (data["run_id"], data["segment_id"]) == ("20260923-140539", "ladder-1")
    assert (data["level"], data["tier"]) == (L2, TIER2)
    assert data["corrected"]["accuracy"] == {"delta_pp": -19.6, "discordant": "9:0",
                                             "p": 0.004, "pairs": 46}
    assert data["corrected"]["completion"]["delta_pp"] == 6.8
    assert data["corrected"]["completion"]["discordant"] == "2:9"
    assert data["corrected"]["completion"]["significant"] is False
    assert data["reported"]["accuracy"]["delta_pp"] == -30.4
    assert data["reported"]["accuracy"]["discordant"] == "14:0"
    assert data["reported"]["completion"]["delta_pp"] == -14.6
    assert "600" in data["cap_warning"] and "180/180" in data["cap_warning"]
    assert "plans/103" in data["remeasure_when"] and "P5" in data["remeasure_when"]
    joined = " ".join(data["corrections"])
    assert "db_scope" in joined and "V-2" in joined
    # 레벨 주입값은 현재 축 정의와 같다 — 같은 레벨을 인용한다.
    assert data["env"] == axes.ladder_axis().env_for(L2)


def test_인용_기록의_필수_칸이_비면_받지_않는다(tmp_path) -> None:
    data = json.loads(CITATION.read_text(encoding="utf-8"))
    del data["cap_warning"]
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(cm.CitationError, match="cap_warning"):
        cm.load_tier_citation(path)


def _cite(*argv: str) -> tuple[str, ...]:
    return ("next", "--mode", "mock", "--campaign", "new-closed", "--cite-tier",
            str(CITATION), *argv)


def test_인용_캠페인은_단_축_구간을_돌지_않고_2단을_주입한다(ladder_seg, monkeypatch) -> None:
    from scripts.bench import __main__ as cli

    run, calls, _, root = ladder_seg
    caveats: list = []
    inner = cli.run_sweep
    monkeypatch.setattr(cli, "run_sweep",
                        lambda *a, **kw: caveats.append(kw.get("tier_caveat")) or inner(*a, **kw))

    assert run(*_cite()) == 0

    assert calls and not any(LADDER in arm for arm in calls[0]), "단 축 구간을 돌지 않는다"
    for arm_id, env in run.envs[0].items():
        assert env["ENABLE_INTENT_ORCHESTRATION"] == "true", arm_id
        assert env["ENABLE_DEEPAGENTS_PACKAGE"] == "false", arm_id
    decision = _state(root, "new-closed")["tier_decision"]
    assert decision["source"] == "cited" and decision["run_id"] == "20260923-140539"
    assert decision["segment_id"] == "ladder-1" and decision["tier"] == TIER2
    assert decision["corrected"]["accuracy"]["discordant"] == "9:0"
    assert decision["sentence"].startswith("**인용")
    # 인용은 측정인 척하지 않는다 — 이후 구간 판정문 머리에 조건부 고지가 실린다.
    assert caveats[0] and "재지 않았다" in caveats[0] and "20260923-140539" in caveats[0]


def test_인용이_합산_리포트에_필수_칸과_함께_남는다(ladder_seg) -> None:
    run, _, _, root = ladder_seg
    assert run(*_cite()) == 0

    body = (root / "new-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    for text in ("사다리 단 — 인용", "`20260923-140539`", "`ladder-1`", f"단 `{TIER2}`",
                 "보정 수치: 정확도 -19.6%p · 불일치 9:0 · p=0.004",
                 "완주율(교정 정의) +6.8%p · 불일치 2:9 · p=0.065(비유의)",
                 "원 보고 수치: 정확도 -30.4%p · 불일치 14:0 · p=0.0001 · 완주율 -14.6%p",
                 "db_scope", "plans/120 V-2", "600", "180/180", "plans/103", "P5"):
        assert text in body, text
    # 단 축 행은 「미측정」이 아니라 「인용」이고 표 맨 위다.
    ladder_row = next(line for line in body.splitlines() if line.startswith(f"| `{LADDER}` |"))
    assert "인용" in ladder_row and "미측정" not in ladder_row
    assert body.index(f"| `{LADDER}` |") < body.index("| `A` |")
    assert "· 인용 1" in body


def test_인용은_구간을_돌기_전에도_상태와_리포트에_남는다(ladder_seg, monkeypatch) -> None:
    """환경 관문에서 멈춰도(서버를 띄우지 못함) 인용은 캠페인 상태다."""
    from scripts.bench import __main__ as cli

    run, _, _, root = ladder_seg
    monkeypatch.setattr(cli, "run_sweep", lambda *a, **kw: cli.SweepOutcome(rc=2))

    assert run(*_cite()) == 2
    state = _state(root, "new-closed")
    assert state["tier_decision"]["source"] == "cited" and state["records"] == []
    assert "사다리 단 — 인용" in (root / "new-closed" / "campaign_verdicts.md").read_text(
        encoding="utf-8")


def test_인용_캠페인의_미측정_축은_인용_축을_섞지_않는다(ladder_seg, monkeypatch) -> None:
    run, calls, _, _ = ladder_seg
    seen: list = []
    monkeypatch.setattr(optimize, "write_proposals",
                        lambda *a, optima=(), **kw: seen.append(list(optima)) or {})

    assert run(*_cite()) == 0

    ran = {arm.split("-")[1] for arm in calls[0] if arm != "baseline"}
    last = seen[-1]
    assert LADDER not in {o.axis for o in last}, "인용 축은 미측정 처분에 들어가지 않는다"
    rest = [o for o in last if o.axis not in ran]
    assert rest and all(o.verdict == compare.UNMEASURED for o in rest), \
        "끝나지 않은 축은 「판정 불가」가 아니라 「미측정」이다"


def test_dry는_인용을_보여주되_상태를_쓰지_않는다(ladder_seg, capsys) -> None:
    run, calls, _, root = ladder_seg

    assert run("next", "--mode", "dry", "--campaign", "new-closed",
               "--cite-tier", str(CITATION)) == 0
    out = capsys.readouterr().out

    assert calls == [] and not root.exists()
    assert "**인용**" in out and "보정 수치" in out
    assert not any(line.strip().startswith(f"{axes.LADDER_CATEGORY}-1")
                   for line in out.splitlines()), "인용한 단 축 구간은 계획 행에 없다"


def test_인용은_캠페인_이름을_명시해야_한다(ladder_seg, capsys) -> None:
    run, calls, _, root = ladder_seg

    assert run("next", "--mode", "mock", "--cite-tier", str(CITATION)) == 1
    assert "--campaign" in capsys.readouterr().out and calls == [] and not root.exists()


def test_구간을_돈_캠페인에는_인용하지_않는다(ladder_seg, capsys) -> None:
    """D-266 ⑤ — 닫힌 캠페인(`run-closed`)을 인용으로 잇지 않는다."""
    run, calls, outcomes, _ = ladder_seg
    outcomes.append(m0_tests._won(level=L2))
    assert run("next", "--mode", "mock", "--campaign", "old") == 0
    capsys.readouterr()

    assert run("next", "--mode", "mock", "--campaign", "old", "--cite-tier",
               str(CITATION)) == 1
    out = capsys.readouterr().out
    assert "인용을 받지 않습니다" in out and "새 캠페인 이름" in out
    assert len(calls) == 1


def test_같은_인용은_반복해도_되고_다른_인용은_거부한다(ladder_seg, tmp_path, capsys) -> None:
    run, calls, _, _ = ladder_seg
    assert run(*_cite()) == 0
    assert run(*_cite()) == 0, "같은 인용 — 명령을 반복해 쳐도 된다"

    other = json.loads(CITATION.read_text(encoding="utf-8"))
    other["run_id"] = "20990101-000000"
    path = tmp_path / "other.json"
    path.write_text(json.dumps(other, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()
    assert run("next", "--mode", "mock", "--campaign", "new-closed",
               "--cite-tier", str(path)) == 1
    assert "이미 있다" in capsys.readouterr().out
    assert len(calls) == 2


def test_레벨_주입값이_현재_정의와_다르면_인용하지_않는다(tmp_path) -> None:
    from scripts.bench import __main__ as cli

    data = json.loads(CITATION.read_text(encoding="utf-8"))
    data["env"] = {**data["env"], "ENABLE_INTENT_ORCHESTRATION": "false"}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    camp = cm.Campaign(name="t", path=tmp_path / "s.json", env="closed", mode="run")

    with pytest.raises(cm.CitationError, match="같은 레벨이 아니라"):
        cli._cite_tier(camp, path)
    assert camp.tier_decision == {}


def test_인용_캠페인에도_측정_상한_가드가_선다(tmp_path, monkeypatch) -> None:
    """D-266 ① — 구간 기록 전이라도 인용 때 기록한 주입값과 다른 코드로 잇지 않는다."""
    from scripts.bench import __main__ as cli

    monkeypatch.setattr(cli, "_RESULTS_DIR", tmp_path)
    monkeypatch.setattr(sweep, "resolve_env", lambda explicit=None: ("closed", "테스트"))
    path = cm.campaign_path(tmp_path, "new-closed")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "new-closed", "env": "closed", "mode": "run",
                                "repeat": 1, "records": [],
                                "measurement_env": {"API_QUERY_TIMEOUT": "60"},
                                "tier_decision": {"source": "cited", "level": L2}}),
                    encoding="utf-8")
    args = cli.build_parser().parse_args(["--segment", "next", "--mode", "run",
                                          "--campaign", "new-closed"])

    problem = cli._campaign_guard(args)

    assert problem and "측정 상한 주입" in problem and "새 캠페인 이름" in problem


def test_인용_캠페인에서도_출처가_섞인_재개는_구간_실패다(ladder_seg) -> None:
    """plans/118 B-1 — 인용이 재개 가드를 우회하지 않는다."""
    run, _, outcomes, root = ladder_seg
    outcomes.append((_health(provenance_mixed="설정이 다르다(API_QUERY_TIMEOUT 60 -> 180)"), {}))

    assert run(*_cite()) == 1
    record = _state(root, "new-closed")["records"][0]
    assert record["status"] == cm.FAILED
    assert any("출처 섞임" in reason for reason in record["stop_reasons"])
