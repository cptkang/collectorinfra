"""`scripts/bench/sweep.py`·`compare.py`·`optimize.py` 테스트 (plans/93 트랙 A~C)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes as axes_mod  # noqa: E402
from scripts.bench import catalog as cat_mod  # noqa: E402
from scripts.bench import compare, optimize, sweep  # noqa: E402


def _obs(arm, sid, passed=True, wall=100.0, calls=3, repeat=0, manual=False):
    return sweep.Observation(arm_id=arm, scenario_id=sid, repeat=repeat, passed=passed,
                             wall_ms=wall, llm_calls=calls, tokens=None, retries=None,
                             manual=manual)


# ── arm 전개 ───────────────────────────────────────────────

def test_build_arms_includes_baseline_first():
    axis = axes_mod.AxisCandidate("K", "g", "bool", ("true", "false"), ("llm_calls",), "r")
    arms = sweep.build_arms([axis])
    assert arms[0].arm_id == sweep.BASELINE_ARM and arms[0].env == {}
    assert len(arms) == 3


def test_build_arms_each_variant_changes_one_key():
    made = [axes_mod.AxisCandidate(f"K{i}", "g", "bool", ("true",), ("llm_calls",), "r")
            for i in range(3)]
    for arm in sweep.build_arms(made)[1:]:
        assert len(arm.env) == 1


# ── 관측치 접기 ────────────────────────────────────────────

def _write_raw(tmp_path, rows):
    p = tmp_path / "raw.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return p


def test_read_observations_folds_turns_into_one_scenario(tmp_path):
    """멀티턴은 턴마다 행이 생기지만 비교 단위는 시나리오다."""
    raw = _write_raw(tmp_path, [
        {"profile": "a", "scenario_id": "S1", "turn": 0, "repeat": 0,
         "func_verdict": "pass", "wall_ms": 100, "llm_calls": 2},
        {"profile": "a", "scenario_id": "S1", "turn": 1, "repeat": 0,
         "func_verdict": "pass", "wall_ms": 50, "llm_calls": 1},
    ])
    obs = sweep.read_observations(raw)
    assert len(obs) == 1
    assert obs[0].passed and obs[0].wall_ms == 150 and obs[0].llm_calls == 3


def test_read_observations_one_failed_turn_fails_scenario():
    pass  # 아래 파라미터 테스트가 덮는다


@pytest.mark.parametrize("verdicts,expected_passed", [
    (["pass", "pass"], True),
    (["pass", "fail"], False),
    (["fail", "fail"], False),
])
def test_scenario_pass_requires_every_turn(tmp_path, verdicts, expected_passed):
    raw = _write_raw(tmp_path, [
        {"profile": "a", "scenario_id": "S1", "turn": i, "repeat": 0,
         "func_verdict": v, "wall_ms": 10}
        for i, v in enumerate(verdicts)
    ])
    assert sweep.read_observations(raw)[0].passed is expected_passed


def test_read_observations_marks_manual(tmp_path):
    """mock의 `manual`은 합격도 불합격도 아니다 — 통과로 세면 비교가 무의미해진다."""
    raw = _write_raw(tmp_path, [
        {"profile": "a", "scenario_id": "S1", "turn": 0, "repeat": 0,
         "func_verdict": "manual", "wall_ms": 10},
    ])
    assert sweep.read_observations(raw)[0].manual is True


def test_read_observations_skips_malformed_lines(tmp_path):
    p = tmp_path / "raw.jsonl"
    p.write_text('{"broken\n{"profile":"a","scenario_id":"S","turn":0,"repeat":0,'
                 '"func_verdict":"pass","wall_ms":5}\n', encoding="utf-8")
    assert len(sweep.read_observations(p)) == 1


def test_read_observations_missing_file_is_empty(tmp_path):
    assert sweep.read_observations(tmp_path / "nope.jsonl") == []


# ── 쌍체 정확도 ────────────────────────────────────────────

def test_paired_accuracy_counts_discordant_only():
    base = [_obs("b", "S1", True), _obs("b", "S2", True), _obs("b", "S3", False)]
    var = [_obs("v", "S1", True), _obs("v", "S2", False), _obs("v", "S3", True)]
    acc = compare.paired_accuracy(base, var)
    assert acc.n_pairs == 3
    assert acc.only_baseline_passed == 1 and acc.only_variant_passed == 1
    assert acc.delta_pp == 0.0


def test_paired_accuracy_excludes_manual():
    base = [_obs("b", "S1", True, manual=True)]
    var = [_obs("v", "S1", False, manual=True)]
    assert compare.paired_accuracy(base, var).n_pairs == 0


def test_exact_binomial_symmetric():
    assert compare._exact_binomial_p(0, 0) is None
    assert compare._exact_binomial_p(5, 5) == 1.0
    assert compare._exact_binomial_p(0, 10) < 0.01


# ── 부트스트랩 ─────────────────────────────────────────────

def test_paired_metric_detects_consistent_shift():
    base = [_obs("b", f"S{i}", wall=100.0) for i in range(20)]
    var = [_obs("v", f"S{i}", wall=150.0) for i in range(20)]
    m = compare.paired_metric(base, var, "wall_ms")
    assert m.mean_delta == 50.0 and m.ci_low > 0


def test_paired_metric_is_deterministic():
    base = [_obs("b", f"S{i}", wall=100.0 + i) for i in range(10)]
    var = [_obs("v", f"S{i}", wall=110.0 + i) for i in range(10)]
    a = compare.paired_metric(base, var, "wall_ms")
    b = compare.paired_metric(base, var, "wall_ms")
    assert (a.ci_low, a.ci_high) == (b.ci_low, b.ci_high), "시드가 고정돼야 재현된다"


def test_paired_metric_needs_two_pairs():
    assert compare.paired_metric([_obs("b", "S1")], [_obs("v", "S1")], "wall_ms") is None


# ── 판정 5어휘 ─────────────────────────────────────────────

def test_judge_adopt_on_clear_improvement():
    base = [_obs("b", f"S{i}", passed=(i >= 10)) for i in range(20)]
    var = [_obs("v", f"S{i}", passed=True) for i in range(20)]
    v = compare.judge("arm", "K", "true", base, var)
    assert v.verdict == compare.ADOPT and "채택" in v.sentence


def test_judge_reject_on_clear_degradation():
    base = [_obs("b", f"S{i}", passed=True) for i in range(20)]
    var = [_obs("v", f"S{i}", passed=(i >= 10)) for i in range(20)]
    assert compare.judge("arm", "K", "true", base, var).verdict == compare.REJECT


def test_judge_underpowered_when_few_discordant():
    """★ 없는 유의성을 만들지 않는다."""
    base = [_obs("b", f"S{i}", passed=True) for i in range(20)]
    var = [_obs("v", f"S{i}", passed=(i != 0)) for i in range(20)]
    v = compare.judge("arm", "K", "true", base, var)
    assert v.verdict == compare.UNDERPOWERED and "표본이 부족" in v.sentence


def test_judge_no_difference_reports_rule6_target():
    base = [_obs("b", f"S{i}", passed=(i % 2 == 0)) for i in range(40)]
    var = [_obs("v", f"S{i}", passed=(i % 2 == 0)) for i in range(40)]
    v = compare.judge("arm", "K", "true", base, var)
    assert v.verdict in (compare.NO_DIFFERENCE, compare.UNDERPOWERED)


def test_judge_mock_run_still_reports_cost_axis():
    """정확도를 못 재도 지연·호출 수는 낸다 — mock 실행의 가치."""
    base = [_obs("b", f"S{i}", wall=100.0, manual=True) for i in range(10)]
    var = [_obs("v", f"S{i}", wall=130.0, manual=True) for i in range(10)]
    v = compare.judge("arm", "K", "true", base, var)
    assert v.verdict == compare.UNDERPOWERED
    assert "지연" in v.sentence and "mock" in v.sentence


def test_judge_verdict_vocabulary_is_closed():
    allowed = {compare.ADOPT, compare.CONDITIONAL, compare.REJECT,
               compare.NO_DIFFERENCE, compare.UNDERPOWERED}
    base = [_obs("b", f"S{i}", passed=(i % 3 == 0)) for i in range(30)]
    var = [_obs("v", f"S{i}", passed=(i % 2 == 0)) for i in range(30)]
    assert compare.judge("a", "K", "v", base, var).verdict in allowed


# ── 노이즈 상한 · 다중비교 ────────────────────────────────

def test_noise_floor_from_baseline_repeats():
    runs = [[_obs("b", "S1", True), _obs("b", "S2", True)],
            [_obs("b", "S1", True), _obs("b", "S2", False)]]
    assert compare.noise_floor(runs) == 50.0


def test_noise_floor_needs_two_runs():
    assert compare.noise_floor([[_obs("b", "S1", True)]]) == 0.0


def test_benjamini_hochberg_controls_fdr():
    keep = compare.benjamini_hochberg([0.001, 0.04, 0.9, None])
    assert keep[0] is True and keep[3] is False


# ── 처분 결정 절차 ────────────────────────────────────────

def _knob(key="FLAG", **over):
    base = dict(env_key=key, group_key="text2sql", field_name="flag", type="bool",
                enum_choices=None, default="false", consumed=True, is_secret=False,
                is_sensitive=False, apply_mode="restart", description="d")
    base.update(over)
    return cat_mod.KnobSpec(**base)


def test_decide_defects_come_first():
    """★ 0단계는 간소화가 아니라 결함 수정 — 성능 판정보다 먼저다."""
    d = optimize.decide(_knob(), boot_failed=True, verdict=compare.ADOPT)
    assert d.action == optimize.FIX_NOW and d.rule == "0-a"


@pytest.mark.parametrize("kwargs,rule", [
    ({"boot_failed": True}, "0-a"),
    ({"integrity_kinds": ["orphan"]}, "0-b"),
    ({"integrity_kinds": ["missing_example"]}, "0-c"),
    ({"shadowed": True}, "0-d"),
])
def test_decide_zero_stage_rules(kwargs, rule):
    assert optimize.decide(_knob(), **kwargs).rule == rule


def test_decide_delete_needs_unchanged_and_zero_refs():
    d = optimize.decide(_knob(), consumption="unchanged", reference_count=0)
    assert d.action == optimize.PROPOSE_DELETE and d.stage == "R4"


def test_decide_adopt_becomes_default_change():
    d = optimize.decide(_knob(), verdict=compare.ADOPT, recommended_value="true",
                        reference_count=5)
    assert d.action == optimize.CHANGE_DEFAULT and d.recommended_value == "true"
    assert d.stage == "R3"


def test_decide_conditional_keeps_default():
    d = optimize.decide(_knob(), verdict=compare.CONDITIONAL, reference_count=5)
    assert d.action == optimize.KEEP_WITH_VALUE and "맞바꾼" not in d.reason


def test_decide_no_difference_with_refs_downgrades():
    d = optimize.decide(_knob(), verdict=compare.NO_DIFFERENCE, reference_count=5)
    assert d.action == optimize.DOWNGRADE and d.rule == "6"


def test_decide_underpowered_defers():
    d = optimize.decide(_knob(), verdict=compare.UNDERPOWERED, reference_count=5)
    assert d.action == optimize.DEFER


def test_decide_is_deterministic():
    """같은 입력이면 같은 처분 — 리포트를 두 번 넣어도 결과가 같아야 한다."""
    args = dict(verdict=compare.ADOPT, reference_count=3, recommended_value="x")
    assert optimize.decide(_knob(), **args) == optimize.decide(_knob(), **args)


# ── D-161 증거 ────────────────────────────────────────────

def test_evidence_incomplete_when_any_field_blank():
    e = optimize.Evidence("K", "v", "r", "", "i")
    assert e.complete is False


def test_evidence_complete_when_all_present():
    assert optimize.Evidence("K", "a", "b", "c", "d").complete is True


def test_count_references_excludes_venv_and_tests():
    """`docs/flag_audit.md` 실사례: 벤더 venv가 참조 수를 10배로 부풀렸다."""
    n = optimize.count_references("TEXT2SQL_SEMANTIC_COMPOSE")
    assert isinstance(n, int) and n >= 0


def test_render_evidence_marks_incomplete():
    text = optimize.render_evidence([optimize.Evidence("K", "a", "b", "", "d")])
    assert "미완(보류)" in text


# ── 산출물 ────────────────────────────────────────────────

def test_write_proposals_creates_three_files(tmp_path):
    paths = optimize.write_proposals(
        [optimize.Disposition("K", optimize.KEEP, "9", "r")],
        [optimize.Evidence("K", "a", "b", "c", "d")],
        {"K": "1"}, tmp_path / "p")
    assert set(paths) == {"disposition", "evidence", "pin"}
    assert all(p.exists() for p in paths.values())


def test_migration_pin_explains_why_it_comes_first():
    text = optimize.render_migration_pin({"K": "1"})
    assert "값 변화 0" in text and "K=1" in text


def test_write_proposals_does_not_touch_repo_config(tmp_path):
    """★ V5 — 제안기는 파일을 고치지 않는다."""
    env, cfg = _ROOT / ".env", _ROOT / "src" / "config.py"
    before = (env.stat().st_mtime if env.exists() else None, cfg.stat().st_mtime)
    optimize.write_proposals([optimize.Disposition("K", optimize.KEEP, "9", "r")],
                             [], {}, tmp_path / "p")
    after = (env.stat().st_mtime if env.exists() else None, cfg.stat().st_mtime)
    assert before == after
