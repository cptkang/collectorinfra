"""`scripts/bench/campaign.py`·`__main__.cmd_segment` 테스트 — 구간 캠페인(1회 구동 ≤ 10시간).

사용자 지시(2026-09-21): *"1회 구동시 최대 10시간 이내에서 구간별로 테스트가 진행되도록 config의
카테고리별로 나눠서 단계적으로"*. 서버를 띄우지 않는다 — 스위프 1회(`run_sweep`)를 가짜로 바꿔
상태 파일 진행·실패 멈춤·합산 리포트만 본다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import campaign as cm  # noqa: E402
from scripts.bench import sweep  # noqa: E402


def _arms(spec: dict[str, int]) -> list[sweep.ArmSpec]:
    """{축: 레벨 수} → 기준선 + 레벨별 arm."""
    out = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None)]
    for axis, n in spec.items():
        for i in range(n):
            out.append(sweep.ArmSpec(arm_id=f"S2-{axis}-{i}", axis=axis, level=str(i),
                                     env={axis: str(i)}))
    return out


def _rate(hours_per_arm: float) -> cm.RateModel:
    """arm 1개가 정확히 `hours_per_arm` 시간이 되는 속도(기동 0)."""
    return cm.RateModel(sec_per_turn=hours_per_arm * 3600.0 / 100, turns_per_arm=100,
                        source="테스트", boot_sec=0.0)


# ── 계획 ────────────────────────────────────────────────────────────


def test_기본_속도는_그래프_진입_턴_평균이다() -> None:
    """전체 평균 51.1초는 0.01초짜리 pre-gate 턴 34%가 끌어내린 값이다 — 쓰지 않는다."""
    rate = cm.RateModel(sec_per_turn=cm.DEFAULT_SEC_PER_TURN, turns_per_arm=129, source="t")
    assert cm.DEFAULT_SEC_PER_TURN == 77.4
    assert rate.arm_hours() == pytest.approx((129 * 77.4 + 30) / 3600)


def test_모든_구간이_채움_상한_안이다() -> None:
    arms = _arms({"A": 2, "B": 3, "C": 2, "D": 2, "E": 3})
    plan = cm.plan_segments(arms, {a: "g" for a in "ABCDE"}, rate=_rate(1.8), max_hours=10)

    assert plan.budget_hours == pytest.approx(9.0), "안전 여유 10% — 10시간이면 9시간까지만 채운다"
    assert plan.max_arms == 5
    assert plan.segments and all(s.est_hours <= plan.budget_hours for s in plan.segments)
    assert all(s.n_arms <= plan.max_arms for s in plan.segments)


def test_한_축의_레벨은_구간_사이로_갈리지_않는다() -> None:
    """★ 깨면 안 되는 제약 — 레벨 간 비교는 같은 시간대·같은 기준선이어야 성립한다."""
    arms = _arms({"A": 3, "B": 2, "C": 3, "D": 2, "E": 2, "F": 3})
    plan = cm.plan_segments(arms, {a: "g" for a in "ABCDEF"}, rate=_rate(1.0), max_hours=10)

    for axis in "ABCDEF":
        homes = [s.segment_id for s in plan.segments if axis in s.axes]
        assert len(homes) == 1, f"{axis} 가 {homes} 로 갈렸다"
        seg = next(s for s in plan.segments if axis in s.axes)
        width = {'A': 3, 'C': 3, 'F': 3}.get(axis, 2)
        assert {f"S2-{axis}-{i}" for i in range(width)} <= set(seg.arm_ids)


def test_예산을_넘는_카테고리는_번호를_붙여_나눈다() -> None:
    arms = _arms({f"K{i}": 2 for i in range(6)})
    plan = cm.plan_segments(arms, {f"K{i}": "composite" for i in range(6)},
                            rate=_rate(1.8), max_hours=10)   # 변이 4개/구간 → 2축씩

    ids = sorted(s.segment_id for s in plan.segments)
    assert ids == ["composite-1", "composite-2", "composite-3"]
    assert all(len(s.axes) == 2 for s in plan.segments)


def test_카테고리를_섞어_묶지_않는다() -> None:
    """구간 = 설정 카테고리 — 예산이 남아도 다른 카테고리 축은 같은 구간에 넣지 않는다."""
    arms = _arms({"Q": 2, "R": 2})
    plan = cm.plan_segments(arms, {"Q": "query", "R": "router"}, rate=_rate(1.0), max_hours=10)

    assert sorted(s.segment_id for s in plan.segments) == ["query-1", "router-1"]


def test_한_축만으로_예산을_넘으면_구간을_만들지_않고_남긴다() -> None:
    arms = _arms({"WIDE": 5, "OK": 2})
    plan = cm.plan_segments(arms, {"WIDE": "g", "OK": "g"}, rate=_rate(2.0), max_hours=10)

    assert [s.axes for s in plan.segments] == [("OK",)]
    assert plan.unplaceable and plan.unplaceable[0][0] == "WIDE"
    assert "채움 상한" in plan.unplaceable[0][1]


def test_완료된_번호는_다시_쓰지_않고_남은_이름은_그대로다() -> None:
    arms = _arms({"A": 2, "B": 2, "C": 2})
    cats = {a: "c" for a in "ABC"}
    first = cm.plan_segments(arms, cats, rate=_rate(2.0), max_hours=10)
    names = {s.axes[0]: s.segment_id for s in first.segments}

    done_axis = next(a for a, sid in names.items() if sid == "c-2")
    rest = [a for a in arms if a.axis != done_axis]
    again = cm.plan_segments(rest, cats, rate=_rate(2.0), max_hours=10, used_ids={"c": {2}})

    assert {s.axes[0]: s.segment_id for s in again.segments} == {
        a: sid for a, sid in names.items() if a != done_axis}


def test_가장_짧은_구간이_먼저_온다() -> None:
    """첫 구간 = 관문(종전 smoke). 관문이 실패하면 버리는 시간이 가장 적어야 한다."""
    arms = _arms({"WIDE": 3, "NARROW": 2})
    plan = cm.plan_segments(arms, {"WIDE": "a", "NARROW": "b"}, rate=_rate(1.5), max_hours=10)

    assert plan.segments[0].axes == ("NARROW",)


def test_기준선과_같은_arm은_실행하지_않고_구간_크기에서_뺀다() -> None:
    arms = _arms({"A": 2, "B": 2, "C": 3})
    controls = frozenset({"S2-A-0", "S2-B-0", "S2-C-1"})
    plan = cm.plan_segments(arms, {a: "g" for a in "ABC"}, rate=_rate(2.9),
                            max_hours=10, controls=controls)   # arm 3개 → 변이 2개/구간

    assert plan.max_arms == 3
    by_axes = {s.axes: s for s in plan.segments}
    assert by_axes[("C",)].arm_ids == ("S2-C-0", "S2-C-2")
    assert by_axes[("C",)].substituted == ("S2-C-1",)
    pair = next(s for s in plan.segments if len(s.axes) == 2)
    assert pair.n_arms == 3, "불린 축 둘이 한 구간에 든다(각 1 arm만 실행)"
    assert set(pair.substituted) == {"S2-A-0", "S2-B-0"}


def test_스냅샷이_없으면_아무것도_빼지_않는다() -> None:
    """추정 금지 — 지문이 없으면 기준선과 같은지 모른다."""
    arms = _arms({"A": 2})
    plan = cm.plan_segments(arms, {"A": "g"}, rate=_rate(1.0), max_hours=10)

    assert plan.segments[0].arm_ids == ("S2-A-0", "S2-A-1")
    assert plan.segments[0].substituted == ()


def test_첫_구간은_보정용으로_축_하나만_담는다() -> None:
    arms = _arms({"A": 2, "B": 2, "C": 2})
    plan = cm.plan_segments(arms, {"A": "x", "B": "x", "C": "y"}, rate=_rate(1.0),
                            max_hours=10, calibrate=True)

    first = plan.segments[0]
    assert first.calibration and first.axes == ("C",), "축이 적은 카테고리의 축 하나"
    assert first.segment_id == "y-1"
    rest = [s for s in plan.segments[1:]]
    assert rest and all(not s.calibration for s in rest)
    assert ("A", "B") in [s.axes for s in rest] or ("B", "A") in [s.axes for s in rest], \
        "보정 구간 뒤는 예산대로 묶인다"


def test_구간_arm은_기준선이_맨_앞이다() -> None:
    arms = _arms({"A": 2, "B": 2})
    got = cm.segment_arms(arms, ["B"])

    assert got[0].arm_id == "baseline"
    assert [a.axis for a in got[1:]] == ["B", "B"]


# ── 속도 · 상태 파일 ────────────────────────────────────────────────


def _record(sid, status=cm.DONE, mode="run", elapsed=3600.0, turns=100,
            finished="2026-09-21T10:00:00"):
    return cm.SegmentRecord(segment_id=sid, category=sid.rpartition("-")[0], axes=[sid],
                            arm_ids=[f"S2-{sid}-0"], status=status, mode=mode, elapsed_sec=elapsed,
                            turns=turns, finished_at=finished)


def test_실측이_없으면_기본_속도_run_구간이_끝나면_그_실측(tmp_path) -> None:
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    assert camp.rate(129).sec_per_turn == cm.DEFAULT_SEC_PER_TURN

    camp.records["a-1"] = _record("a-1", elapsed=6000.0, turns=100)
    rate = camp.rate(129)
    assert rate.sec_per_turn == pytest.approx(60.0)
    assert "a-1 실측" in rate.source
    assert rate.turns_per_arm == 50, "턴 수도 실측이다 — 실행 arm 2개(기준선+1)에 100턴"


def test_mock_구간은_계획_속도를_바꾸지_않는다(tmp_path) -> None:
    """모의 속도로 짜면 남은 구간이 한 덩어리로 뭉쳐 run 계획과 달라진다 — 리허설이 무의미해진다."""
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="mock")
    camp.records["a-1"] = _record("a-1", mode="mock", elapsed=30.0, turns=100)

    assert camp.rate(129).sec_per_turn == cm.DEFAULT_SEC_PER_TURN


def test_상태_파일은_저장하고_다시_읽힌다(tmp_path) -> None:
    camp = cm.Campaign(name="t", path=tmp_path / "x" / "campaign.json", env="closed", mode="run")
    camp.records["a-1"] = _record("a-1", status=cm.FAILED)
    camp.save()

    again = cm.Campaign.load_or_new(camp.path, name="t", env="closed", mode="run", repeat=1,
                                    max_hours=10)
    assert again.failed()[0].segment_id == "a-1"
    assert again.used_ids() == {"a": {1}}


def test_arm_of_는_옛_행에서_profile을_읽는다() -> None:
    assert sweep.arm_of({"profile": "S2-K-true", "scenario_id": "A-01"}) == "S2-K-true"


def test_arm_of_는_새_행에서_arm을_읽는다() -> None:
    """36 계약: 조합 이름은 `profile` 에(ASCII `+`), 덧씌운 arm 은 `arm` 에."""
    row = {"arm": "baseline", "base_profile": "optin_alarm", "profile": "optin_alarm+baseline"}
    assert sweep.arm_of(row) == "baseline"


def test_arm_of_는_덧씌우기가_없으면_profile로_폴백한다() -> None:
    row = {"arm": None, "base_profile": "optin_alarm", "profile": "optin_alarm"}
    assert sweep.arm_of(row) == "optin_alarm"


def test_새_행의_D군은_기준선_arm으로_묶여_쌍체_비교에_들어간다(tmp_path) -> None:
    from scripts.bench import compare

    rows = []
    for arm, passed in (("baseline", True), ("S2-K-true", False)):
        rows.append({"arm": arm, "base_profile": "optin_alarm", "profile": f"optin_alarm+{arm}",
                     "scenario_id": "D-07", "turn": 1, "repeat": 0,
                     "func_verdict": "pass" if passed else "fail", "response_mode": "answer",
                     "executed_sql": "SELECT 1", "node_path": ["q"], "wall_ms": 100})
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    grouped = sweep.group_by_arm(sweep.read_observations(raw))
    assert set(grouped) == {"baseline", "S2-K-true"}, "조합 이름이 아니라 arm 으로 묶인다"
    acc = compare.paired_accuracy(grouped["baseline"], grouped["S2-K-true"])
    assert acc.n_pairs == 1 and acc.only_baseline_passed == 1
    health = sweep.scan_health({"profiles": []}, raw)
    assert health.baseline_order == (1, 2)


def test_stop_reasons는_가이드의_멈춤_목록과_같다() -> None:
    def health(**over):
        base = dict(valid_profiles=3, invalid_profiles=[], turns=100, verdicts={"pass": 100},
                    evidence=[], sql_turns=100, graph_turns=100, clarify_turns=0,
                    baseline_order=(1, 3))
        base.update(over)
        return sweep.RunHealth(**base)

    assert health().stop_reasons() == []
    assert "그래프 진입률" in health(graph_turns=70).stop_reasons()[0]
    assert "역질문 종료율" in health(clarify_turns=30).stop_reasons()[0]
    six = health(invalid_turns=6, verdicts={"invalid": 6, "pass": 94})
    assert "무효 턴" in six.stop_reasons()[0]
    assert "번째로 실행" in health(baseline_order=(3, 3)).stop_reasons()[0]
    assert health(sql_turns=0).stop_reasons()[0].startswith("차단")
    assert health(invalid_turns=5, verdicts={"invalid": 5, "pass": 95}).stop_reasons() == [], \
        "무효 5%는 한도 이내다(D-219 ③ — 넘을 때만 멈춘다)"


# ── `--segment` 흐름 (서버 없이) ────────────────────────────────────


def _health(**over):
    base = dict(valid_profiles=3, invalid_profiles=[], turns=200, verdicts={"pass": 200},
                evidence=[], sql_turns=200, graph_turns=200, clarify_turns=0,
                baseline_order=(1, 3))
    base.update(over)
    return sweep.RunHealth(**base)


def _state(root, name):
    return json.loads((root / name / "campaign.json").read_text(encoding="utf-8"))


@pytest.fixture()
def seg(monkeypatch, tmp_path):
    """캠페인 흐름만 본다 — 축 3개(카테고리 둘), 스위프 1회는 가짜."""
    from scripts.bench import __main__ as cli

    arms = _arms({"A": 2, "B": 2, "C": 2})
    monkeypatch.setattr(cli, "_RESULTS_DIR", tmp_path / "bench")
    monkeypatch.setattr(sweep, "build_arms", lambda limit=None: arms)
    monkeypatch.setattr(sweep, "axis_categories",
                        lambda tier="primary": {"A": "x", "B": "x", "C": "y"})
    monkeypatch.setattr(sweep, "planned_turns_per_arm", lambda catalog: 100)
    monkeypatch.setattr(sweep, "load_normal_catalog", lambda env="closed": object())
    monkeypatch.setattr(sweep, "resolve_env", lambda explicit=None: ("closed", "테스트"))
    monkeypatch.setattr(cli, "_sweep_proposals", lambda *a, **kw: {})
    # 각 축의 레벨 0 이 기준선과 같은 설정이다(실제 스위프처럼 축마다 대조군 1개).
    controls = {"S2-A-0", "S2-B-0", "S2-C-0"}

    def fake_snapshot(snap_arms, **kw):
        base = sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                               effective={"k": "base"})
        def eff(a):
            return {"k": "base" if a.arm_id in controls else a.arm_id}

        cfg = {a.arm_id: sweep.ArmConfig(arm_id=a.arm_id, axis=a.axis, level=a.level,
                                         injected=dict(a.env), effective=eff(a))
               for a in snap_arms if a.axis}
        return sweep.ConfigSnapshot(baseline=base, arms=cfg)

    monkeypatch.setattr(sweep, "capture_config_snapshot", fake_snapshot)

    calls: list[list[str]] = []
    subs: list[list[str]] = []
    outcomes: list = []

    def fake_run(args, run_arms, *, label, snapshot=None, substituted=()):
        calls.append([a.arm_id for a in run_arms])
        subs.append([a.arm_id for a in substituted])
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

    run.subs = subs
    return run, calls, outcomes, tmp_path / "bench" / "campaigns"


def test_next_를_반복하면_다음_미완_구간이_돈다(seg) -> None:
    run, calls, _, root = seg

    assert run("next", "--mode", "mock") == 0
    assert run("next", "--mode", "mock") == 0

    assert len(calls) == 2
    assert calls[0][0] == calls[1][0] == "baseline", "구간마다 자기 기준선이 맨 앞"
    assert set(calls[0][1:]).isdisjoint(calls[1][1:]), "같은 구간을 두 번 돌지 않는다"
    # 첫 구간 = 보정 구간(축 하나) · 기준선과 같은 arm 은 뺀다
    assert calls[0] == ["baseline", "S2-C-1"]
    assert run.subs[0] == ["S2-C-0"], "뺀 arm 은 기준선 관측으로 대신하라고 넘긴다"
    state = json.loads((root / "mock-closed" / "campaign.json").read_text(encoding="utf-8"))
    assert [r["status"] for r in state["records"]] == [cm.DONE, cm.DONE]


def test_dry_는_계획만_보여주고_아무것도_돌리지_않는다(seg, capsys) -> None:
    run, calls, _, root = seg

    assert run("next", "--mode", "dry") == 0
    out = capsys.readouterr().out
    assert calls == [] and not root.exists()
    assert "추정 근거: 턴당" in out and "구간당 arm ≤" in out
    assert "x-1" in out and "y-1" in out


def test_멈춤_주의가_뜬_구간은_실패이고_다음_next_는_멈춘다(seg, capsys) -> None:
    run, calls, outcomes, root = seg
    outcomes.append((_health(clarify_turns=120), {}))       # 역질문 60%

    assert run("next", "--mode", "mock") == 1
    assert run("next", "--mode", "mock") == 1, "실패 구간을 건너뛰지 않는다"
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "멈춥니다 — 실패한 구간이 있습니다" in out
    state = json.loads((root / "mock-closed" / "campaign.json").read_text(encoding="utf-8"))
    assert state["records"][0]["status"] == cm.FAILED
    assert any("역질문 종료율" in r for r in state["records"][0]["stop_reasons"])


def test_실패_구간은_id_로_다시_돌린다(seg) -> None:
    run, calls, outcomes, root = seg
    outcomes.append((_health(graph_turns=100), {}))           # 진입률 50% → 실패
    assert run("next", "--mode", "mock") == 1
    failed_id = json.loads((root / "mock-closed" / "campaign.json").read_text(
        encoding="utf-8"))["records"][0]["segment_id"]

    assert run(failed_id, "--mode", "mock") == 0
    assert calls[0] == calls[1], "같은 축으로 다시 돈다"
    rec = _state(root, "mock-closed")["records"][0]
    assert rec["status"] == cm.DONE and rec["attempts"] == 2


def test_run_모드는_전_arm_판정_불가_불일치_0을_실패로_본다(seg) -> None:
    run, _, outcomes, root = seg
    outcomes.append((_health(), {"all_unjudged": True, "discordant": 0}))

    assert run("next", "--mode", "run") == 1
    rec = _state(root, "run-closed")["records"][0]
    assert any("불일치 쌍 합 0건" in r for r in rec["stop_reasons"])


def test_mock_모드는_후단_관문을_실패로_보지_않는다(seg) -> None:
    """모의 응답은 arm 간 같아 불일치 쌍이 늘 0이다 — 이걸 실패로 세면 리허설이 항상 멈춘다."""
    run, _, outcomes, root = seg
    outcomes.append((_health(), {"all_unjudged": True, "discordant": 0}))

    assert run("next", "--mode", "mock") == 0


def test_환경_관문에서_멈추면_구간을_기록하지_않는다(seg, monkeypatch) -> None:
    from scripts.bench import __main__ as cli

    run, _, _, root = seg
    monkeypatch.setattr(cli, "run_sweep", lambda args, arms, **kw: cli.SweepOutcome(rc=2))

    assert run("next", "--mode", "mock") == 2
    assert not (root / "mock-closed" / "campaign.json").exists()


def test_합산_리포트는_미완_축을_미측정으로_남긴다(seg) -> None:
    run, _, _, root = seg

    assert run("next", "--mode", "mock") == 0
    body = (root / "mock-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")

    assert "측정된 축 1/3" in body
    assert body.count("미측정(구간") == 2, "조용히 빠뜨리지 않는다"
    assert "모의 캠페인이다" in body
    assert "레벨 `0` = 기준선과 동일 설정 → 기준선 관측 사용(실행 생략)" in body
    assert "실행 생략 1개 arm" in body


def test_모든_구간이_끝나면_완료를_알린다(seg, capsys) -> None:
    run, calls, _, root = seg
    # 보정 구간(y-1: C) 뒤, x 카테고리 두 축은 각각 1 arm 만 실행하므로 한 구간에 든다.
    for _ in range(2):
        assert run("next", "--mode", "mock") == 0

    assert run("next", "--mode", "mock") == 0
    assert len(calls) == 2
    assert "모든 구간이 끝났습니다" in capsys.readouterr().out
    body = (root / "mock-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    assert "측정된 축 3/3" in body and "기준선 반복 2회" in body
