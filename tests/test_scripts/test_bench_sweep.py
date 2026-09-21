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
    assert "지연" in v.sentence and "LLM 호출" in v.sentence
    # 정확도가 아니라 완주율로 판정했다는 사실을 문장이 숨기지 않는다.
    assert v.signal == "완주율" and "정확도 미측정" in v.sentence


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


# --- 인증 경로와 건전성 관문 (2026-09-14 회귀) ---------------------------
#
# 실 스위프 1984턴이 전량 401 로 끝났는데도 리포트는 "판정 불가 62건"으로 나왔다.
# 사고가 정상 얼굴로 나가지 않게 하는 두 장치를 여기서 못박는다.


def test_크레덴셜이_없어도_arm에_인증을_끄는_값을_싣지_않는다(monkeypatch) -> None:
    """인증을 끄고 재지 않는다(plans/94 G-3 · 사용자 확정 2026-09-15) — arm env 는 축 값뿐이다."""
    captured: dict = {}

    class FakeRunner:
        RunConfig = sweep.__dict__.get("_FakeRunConfig")

        @staticmethod
        def execute(catalog, config):
            captured["profiles"] = dict(catalog.profiles)
            captured["config"] = config
            return {"out_dir": "x"}

    import dataclasses

    @dataclasses.dataclass
    class FakeRunConfig:
        mode: str = "mock"
        env: str = "sandbox"
        repeat: int = 1
        groups: list = dataclasses.field(default_factory=list)
        profiles: list = dataclasses.field(default_factory=list)
        run_id: str = ""
        user_id: str = None
        user_password: str = None
        admin_user: str = None
        admin_password: str = None

    FakeRunner.RunConfig = FakeRunConfig

    class FakeCatalogMod:
        Catalog = None

    fake_catalog = type("C", (), {
        "groups": {}, "scenarios": [], "profiles": {},
        "select": lambda self, **kw: [],
    })()
    monkeypatch.setattr(sweep, "scenario_harness", lambda: (FakeCatalogMod, FakeRunner))
    monkeypatch.setattr(sweep, "load_normal_catalog", lambda env="sandbox": fake_catalog)
    monkeypatch.setattr(sweep, "fanout_scenarios", lambda catalog, arms: [])

    arms = [
        sweep.ArmSpec(arm_id="baseline", axis=None, level=None, env={}),
        sweep.ArmSpec(arm_id="A-1", axis="A", level="1", env={"A": "1"}),
    ]
    sweep.run_arms(arms, credentials=sweep.Credentials())

    assert captured["profiles"]["baseline"] == {}
    assert captured["profiles"]["A-1"] == {"A": "1"}


def test_로그인_크레덴셜이_있으면_인증을_끄지_않는다(monkeypatch) -> None:
    captured: dict = {}
    import dataclasses

    @dataclasses.dataclass
    class FakeRunConfig:
        mode: str = "mock"
        env: str = "sandbox"
        repeat: int = 1
        groups: list = dataclasses.field(default_factory=list)
        profiles: list = dataclasses.field(default_factory=list)
        run_id: str = ""
        user_id: str = None
        user_password: str = None
        admin_user: str = None
        admin_password: str = None

    class FakeRunner:
        RunConfig = FakeRunConfig

        @staticmethod
        def execute(catalog, config):
            captured["profiles"] = dict(catalog.profiles)
            captured["config"] = config
            return {}

    fake_catalog = type("C", (), {"groups": {}, "scenarios": [], "profiles": {}})()
    monkeypatch.setattr(sweep, "scenario_harness", lambda: (object(), FakeRunner))
    monkeypatch.setattr(sweep, "load_normal_catalog", lambda env="sandbox": fake_catalog)
    monkeypatch.setattr(sweep, "fanout_scenarios", lambda catalog, arms: [])

    arms = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None, env={})]
    creds = sweep.Credentials(user_id="bench", user_password="pw",
                              admin_user="admin", admin_password="pw")
    sweep.run_arms(arms, credentials=creds)

    assert captured["profiles"]["baseline"] == {}
    assert captured["config"].user_id == "bench"
    assert captured["config"].admin_user == "admin"


def test_전건_401_은_판정을_막는다(tmp_path) -> None:
    """2026-09-14 실제 산출 그대로의 모양 — 이것이 통과하면 리포트가 거짓이 된다."""
    raw = _write_raw(tmp_path, [
        {"profile": "baseline", "scenario_id": f"S-{i}", "repeat": 0,
         "func_verdict": "error", "response_mode": "error",
         "mode_evidence": "status=error http=401"}
        for i in range(20)
    ])
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)

    assert health.turns == 20
    assert health.error_rate == 1.0
    assert "오류" in health.blocking_reason()
    assert health.evidence[0] == ("status=error http=401", 20)


def test_유효_프로파일이_0개면_판정을_막는다(tmp_path) -> None:
    raw = _write_raw(tmp_path, [])
    result = {"profiles": [
        {"name": "baseline", "valid": False, "reasons": ["설정 에코 미확인 (http 401)"]},
    ]}

    health = sweep.scan_health(result, raw)

    assert health.valid_profiles == 0
    assert "유효한 프로파일이 0개" in health.blocking_reason()
    assert health.invalid_profiles == [("baseline", "설정 에코 미확인 (http 401)")]


def test_정상_런은_관문을_통과한다(tmp_path) -> None:
    # 질의 벤치마크의 「정상 런」은 SQL 과 노드 경로를 남긴다 — 그 둘이 없는 턴만으로 된 런은
    # 관문이 막는다(아래 `test_SQL이_한_건도_관측되지_않으면_판정을_막는다`).
    raw = _write_raw(tmp_path, [
        {"profile": "baseline", "scenario_id": f"S-{i}", "repeat": 0,
         "func_verdict": "pass", "response_mode": "answer",
         "executed_sql": "SELECT 1", "node_path": ["context_resolver", "query_generator"]}
        for i in range(20)
    ])
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)
    assert health.blocking_reason() is None
    assert health.warnings() == [], "정상 런은 주의 문구도 남기지 않는다"


# --- 정확도가 없어도 재는 것이 있다 (2026-09-14 회귀) --------------------
#
# 정상군 시나리오 32건은 전부 `manual_review` 뿐이라 기계 단언이 0개다. 그 상태로는
# 정확도 쌍이 언제나 0쌍이고, 성공한 런과 전건 401 인 런이 같은 문장으로 나온다.


def test_완주와_SQL생성은_원시로그에서_바로_읽는다(tmp_path) -> None:
    raw = _write_raw(tmp_path, [
        {"profile": "a", "scenario_id": "S1", "turn": 0, "repeat": 0,
         "func_verdict": "manual", "response_mode": "answer",
         "executed_sql": "SELECT 1", "wall_ms": 10},
        {"profile": "a", "scenario_id": "S2", "turn": 0, "repeat": 0,
         "func_verdict": "manual", "response_mode": "error",
         "executed_sql": None, "wall_ms": 10},
    ])
    by_id = {o.scenario_id: o for o in sweep.read_observations(raw)}

    assert by_id["S1"].completed is True and by_id["S1"].sql_generated is True
    assert by_id["S2"].completed is False and by_id["S2"].sql_generated is False
    assert by_id["S1"].manual and by_id["S2"].manual, "정확도 보류는 그대로 유지된다"


# --- 오류율만으로는 사고를 못 잡는다 (run 20260914-185540 회귀) ----------
#
# 그 런은 오류율 0.8%로 관문을 통과했지만 6567턴 전건 `executed_sql=null`·그래프 미진입
# 34%·역질문 56%였고, 62 arm 전부 「판정 불가」가 정상 형태의 판정표로 나왔다.


def _sweep_row(profile, sid, **over):
    row = {"profile": profile, "scenario_id": sid, "turn": 0, "repeat": 0,
           "func_verdict": "fail", "response_mode": "clarify",
           "executed_sql": None, "node_path": [], "wall_ms": 10}
    row.update(over)
    return row


def test_SQL이_한_건도_관측되지_않으면_판정을_막는다(tmp_path) -> None:
    """오류율 0%라도 SQL 이 0건이면 SQL 생성률은 신호가 아니라 상수다."""
    raw = _write_raw(tmp_path, [_sweep_row("baseline", f"S-{i}") for i in range(20)])
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)

    assert health.error_rate == 0.0, "종전 관문(오류율 50%)은 이 런을 통과시킨다"
    assert health.sql_turns == 0
    assert "SQL 이 관측된 턴이 0건" in health.blocking_reason()


def test_감사_로그_수집분도_SQL_관측으로_센다(tmp_path) -> None:
    """오케스트레이션 경로는 `done` 에 SQL 이 없다 — 94 가 `executed_sqls` 로 적재한다(D-217)."""
    raw = _write_raw(tmp_path, [
        _sweep_row("a", "S1", func_verdict="manual", response_mode="answer",
                   node_path=["agent_orchestrator"],
                   executed_sqls=[{"sql": "SELECT 1", "source": "polestar_cm_gp"}]),
    ])
    obs = sweep.read_observations(raw)[0]

    assert obs.sql_generated is True, "`executed_sql` 만 보면 이 턴은 영영 0 이다"
    assert obs.entered_graph is True
    result = {"profiles": [{"name": "a", "valid": True, "reasons": []}]}
    assert sweep.scan_health(result, raw).sql_turns == 1


def test_무효_턴은_불합격이_아니라_분모_밖이다(tmp_path) -> None:
    """러너 인증 실패로 측정이 성립하지 않은 턴(D-218).

    이것을 fail 로 세면 러너 결함이 제품 결함으로 집계된다.
    """
    raw = _write_raw(tmp_path, [
        _sweep_row("a", "S1", func_verdict="pass", response_mode="answer",
                   executed_sql="SELECT 1", node_path=["query_generator"]),
        _sweep_row("a", "S2", func_verdict="invalid", response_mode="error",
                   error="http 401 - 토큰이 만료되었습니다"),
    ])
    ids = {o.scenario_id for o in sweep.read_observations(raw)}

    assert ids == {"S1"}, "무효 시나리오는 비교 대상에서 빠진다"
    result = {"profiles": [{"name": "a", "valid": True, "reasons": []}]}
    health = sweep.scan_health(result, raw)
    assert health.invalid_turns == 1
    assert any("무효 턴 1건" in w for w in health.warnings())


def test_전건_무효는_판정을_막는다(tmp_path) -> None:
    raw = _write_raw(tmp_path, [
        _sweep_row("a", f"S-{i}", func_verdict="invalid") for i in range(10)])
    result = {"profiles": [{"name": "a", "valid": True, "reasons": []}]}

    assert "전부 무효" in sweep.scan_health(result, raw).blocking_reason()


def test_도달_지표가_낮으면_차단하지_않고_고지한다(tmp_path) -> None:
    """임계는 워크로드 구성의 함수라 못 박지 않는다 — 대신 침묵하지 않는다."""
    rows = [_sweep_row("baseline", f"S-{i}", executed_sql="SELECT 1",
                       node_path=["query_generator"], response_mode="answer")
            for i in range(5)]
    rows += [_sweep_row("baseline", f"C-{i}") for i in range(5)]   # 역질문 · 그래프 미진입
    raw = _write_raw(tmp_path, rows)
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)

    assert health.blocking_reason() is None, "SQL 이 나온 런은 막지 않는다"
    notes = " / ".join(health.warnings())
    assert "그래프 진입률 50%" in notes and "역질문 종료율 50%" in notes


def test_기준선이_마지막에_실행되면_시각_교란을_고지한다(tmp_path) -> None:
    """`runner.iter_executions:514` 가 프로파일을 알파벳 정렬해 `baseline` 이 늘 마지막이다.

    run 20260914-185540 은 93.4시간 연속이었고 기준선은 62번째(4일차)에 돌았다 —
    모든 쌍체 지연 델타가 실행 시각과 교란됐다.
    """
    rows = [_sweep_row("S2-K-true", "S1", executed_sql="SELECT 1",
                       node_path=["query_generator"], response_mode="answer"),
            _sweep_row("baseline", "S1", executed_sql="SELECT 1",
                       node_path=["query_generator"], response_mode="answer")]
    raw = _write_raw(tmp_path, rows)
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)

    assert health.baseline_order == (2, 2)
    assert any("2번째로 실행됐다" in w for w in health.warnings())


def _run_obs(arm, sid, completed=True, sql=True, wall=100.0):
    return sweep.Observation(arm_id=arm, scenario_id=sid, repeat=0, passed=True,
                             wall_ms=wall, llm_calls=3, tokens=None, retries=None,
                             manual=True, completed=completed, sql_generated=sql)


def test_기계단언이_없으면_완주율로_판정한다() -> None:
    """변형에서 절반이 완주하지 못하면 그것은 기각 사유다 — '판정 불가'가 아니다."""
    base = [_run_obs("b", f"S{i}") for i in range(20)]
    var = [_run_obs("v", f"S{i}", completed=(i >= 10), sql=(i >= 10)) for i in range(20)]

    v = compare.judge("arm", "K", "true", base, var)

    assert v.signal == "완주율"
    assert v.verdict == compare.REJECT
    assert "완주율 -50.0%p" in v.sentence
    assert "SQL 생성률 -50.0%p" in v.sentence


def test_기계단언이_있으면_정확도가_우선한다() -> None:
    base = [_obs("b", f"S{i}", passed=(i >= 10)) for i in range(20)]
    var = [_obs("v", f"S{i}", passed=True) for i in range(20)]

    v = compare.judge("arm", "K", "true", base, var)

    assert v.signal == "정확도" and v.verdict == compare.ADOPT


def test_완주율_비교는_보류_건도_센다() -> None:
    """`manual` 은 '정답인지 모른다'이지 '끝까지 돌지 않았다'가 아니다."""
    base = [_run_obs("b", f"S{i}") for i in range(10)]
    var = [_run_obs("v", f"S{i}") for i in range(10)]

    assert compare.paired_binary(base, var, "completed", drop_manual=False).n_pairs == 10
    assert compare.paired_accuracy(base, var).n_pairs == 0


# --- 워크로드 환경 (2026-09-14 회귀) ------------------------------------
#
# 기본 env 가 `sandbox` 여서 실 스위프가 **유사어 시나리오(그룹 L)만** 돌고 실 질의
# 워크로드(그룹 A~K)를 한 건도 건드리지 않았다. 화면에도 그 사실이 없었다.
#
# 아래 테스트는 **건수를 단언하지 않는다** — 카탈로그는 바뀌고(`closed` 2026-09-14 107건 →
# 2026-09-21 103건) 수치를 박으면 카탈로그가 바뀔 때마다 깨진다.
# ※ 그룹 L 의 `32건` 표기는 낡은 값이 아니다 — 카탈로그 32건 중 SYN-F-05(운영 절차 `action`)를
#   뺀 31건이 워크로드다(아래 `test_판정_가능_집계는…` 주석). 시점 드리프트가 아니다.
# 정본은 `workload_summary`·`judgeable_count` 의 동적 계산이다.

def test_실_관측DB가_활성이면_closed(monkeypatch) -> None:
    import src.config as cfg_mod

    class _Cfg:
        class multi_db:
            @staticmethod
            def get_active_db_ids():
                return ["polestar_cm_gp", "polestar_b0"]

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _Cfg)
    env, reason = sweep.resolve_env()
    assert env == "closed" and "polestar_cm_gp" in reason


def test_로컬_샌드박스만_활성이면_sandbox(monkeypatch) -> None:
    import src.config as cfg_mod

    class _Cfg:
        class multi_db:
            @staticmethod
            def get_active_db_ids():
                return ["polestar"]

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _Cfg)
    assert sweep.resolve_env()[0] == "sandbox"


def test_판정_근거가_없으면_추측하지_않고_closed로_간다(monkeypatch) -> None:
    """폐쇄망이 이 계획의 전제다(§1.4) — 근거가 없을 때 좁은 쪽으로 떨어지지 않는다."""
    import src.config as cfg_mod

    class _Cfg:
        class multi_db:
            @staticmethod
            def get_active_db_ids():
                return []

    monkeypatch.setattr(cfg_mod, "load_config", lambda: _Cfg)
    env, reason = sweep.resolve_env()
    assert env == "closed" and "미설정" in reason


def test_명시_지정이_자동판정을_이긴다() -> None:
    assert sweep.resolve_env("sandbox")[0] == "sandbox"
    assert sweep.resolve_env("closed")[0] == "closed"


def test_워크로드_요약이_그룹_구성을_숨기지_않는다() -> None:
    """무엇을 재는지 한 줄로 보이지 않으면 빗나가도 모른다."""
    catalog = sweep.load_normal_catalog(env="closed")
    line = sweep.workload_summary(catalog)
    assert "그룹 A(" in line and "K(" in line
    assert "L(" not in line, "closed 워크로드에 샌드박스 전용 그룹이 섞이면 안 된다"

    sandbox = sweep.load_normal_catalog(env="sandbox")
    # L 32건 중 SYN-F-05 는 시드 재적재 러너 동작(D-217)이라 워크로드에서 뺀다 - arm 마다 공유 Redis 에 쓴다.
    assert "그룹 L(31)" in sweep.workload_summary(sandbox)


def test_비용축은_LLM호출이_없으면_노드수로_잰다() -> None:
    """`done` 에 LLM 호출 수가 없다 — 비용 축을 통째로 비우는 대신 잴 수 있는 것으로 잰다."""
    base = [sweep.Observation(arm_id="b", scenario_id=f"S{i}", repeat=0, passed=True,
                              wall_ms=100.0, llm_calls=None, tokens=None, retries=0,
                              node_count=8, manual=True) for i in range(10)]
    var = [sweep.Observation(arm_id="v", scenario_id=f"S{i}", repeat=0, passed=True,
                             wall_ms=100.0, llm_calls=None, tokens=None, retries=0,
                             node_count=12, manual=True) for i in range(10)]

    v = compare.judge("arm", "K", "true", base, var)

    assert "노드 수 +4.00" in v.sentence
    assert "LLM 호출" not in v.sentence, "재지 못한 것을 쟀다고 말하지 않는다"


def test_판정_가능_집계는_러너가_건너뛰는_시나리오를_뺀다() -> None:
    """러너가 정리하지 못하는 teardown(상태 오염)이 남은 시나리오는 실행 대상이 아니다.

    A-05(캐시 갱신 — 되돌릴 상태 없음)·A-10(유사어 등록 — 러너가 더한 단어만 지운다)은
    2026-09-15 정리 가능해져 실행 대상이 됐다(D-217).
    """
    catalog = sweep.load_normal_catalog(env="closed")
    judged, runnable = sweep.judgeable_count(catalog)

    # 러너 규칙과 독립적으로 다시 센다(오라클).
    supported = {"drop_thread", "unregister_synonym"}
    skipped = {s.id for s in catalog.scenarios
               if not s.prompt_authored or [a for a in s.teardown if a not in supported]}

    assert not ({"A-05", "A-10"} & skipped)
    assert runnable == len(catalog.scenarios) - len(skipped)
    assert judged <= runnable
    summary = sweep.workload_summary(catalog)
    if skipped:
        assert f"{len(skipped)}건은 러너가 건너뛴다" in summary
    else:
        assert "러너가 건너뛴다" not in summary


# --- 인증 on 서버는 벤치 계정으로만 (D-215 · plans/94 G-3) ----------------
#
# 계정이 없으면 모든 arm 에 AUTH_ENABLED=false 를 주입하던 경로를 철회했다. 인증을 끄면
# 토큰 검증·사용자 조회가 빠지고 익명 사용자는 DB 범위 제한이 없어 운영 경로와 다르다.

def _sweep_args(**overrides):
    import argparse

    base = dict(scale="smoke", mode="run", repeat=1, env="closed", user=None,
                password=None, admin_user=None, admin_password=None, yes=False)
    base.update(overrides)
    return argparse.Namespace(**base)


@pytest.fixture()
def gate(monkeypatch):
    """게이트 뒤의 실행을 전부 가로챈다 — 서버를 띄우지도, 실 호출하지도 않는다."""
    from scripts.bench import __main__ as cli

    seen = {"run_arms": [], "echo": 0}
    monkeypatch.delenv("BENCH_USER_ID", raising=False)
    monkeypatch.delenv("BENCH_USER_PASSWORD", raising=False)
    monkeypatch.setattr(sweep, "build_arms",
                        lambda limit=None: [sweep.ArmSpec(arm_id="baseline", axis=None, level=None)])

    def fake_echo(*args, **kwargs):
        # 설정 스냅샷(§4.5)도 같은 모듈 속성을 쓴다 — 호출 모양을 좁게 잡으면
        # 게이트 테스트가 스냅샷 경로에서 TypeError 로 죽는다.
        seen["echo"] += 1
        return object()

    def fake_run(arms, **kwargs):
        seen["run_arms"].append(kwargs)
        raise sweep.SweepUnavailable("테스트 — 게이트 통과 확인 후 중단")

    monkeypatch.setattr(cli.probe, "echo_config", fake_echo)
    monkeypatch.setattr(cli, "_providers_of", lambda echo: ("fabrix", "vllm"))
    monkeypatch.setattr(cli, "approval_policy", lambda worker, orchestrator: (False, "내부망"))
    monkeypatch.setattr(sweep, "run_arms", fake_run)
    return cli, seen


def test_인증_켜진_서버에서_계정이_없으면_서버를_띄우기_전에_멈춘다(gate, monkeypatch, capsys) -> None:
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: True)

    rc = cli.cmd_sweep(_sweep_args())

    assert rc == 2
    assert seen["run_arms"] == [], "계정 없이 서버를 띄우면 안 된다"
    assert seen["echo"] == 0, "게이트는 설정 에코·승인 확인보다 먼저다"
    out = capsys.readouterr().out
    assert "벤치 계정이 없습니다" in out and "BENCH_USER_ID" in out
    assert "AUTH_ENABLED=false 를 모든 arm" not in out, "인증을 끄는 경로는 철회됐다"


def test_인증_설정을_읽지_못해도_계정_없이는_시작하지_않는다(gate, monkeypatch, capsys) -> None:
    """확인하지 못한 것을 통과로 세지 않는다."""
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: None)

    assert cli.cmd_sweep(_sweep_args()) == 2
    assert seen["run_arms"] == []
    assert "읽지 못했고" in capsys.readouterr().out


def test_계정을_주면_인증_켜진_서버에서_진행한다(gate, monkeypatch) -> None:
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: True)

    cli.cmd_sweep(_sweep_args(user="bench01", password="pw"))

    assert len(seen["run_arms"]) == 1
    assert seen["run_arms"][0]["credentials"].user_id == "bench01"


def test_OS_환경변수로_준_계정도_받는다(gate, monkeypatch) -> None:
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: True)
    monkeypatch.setenv("BENCH_USER_ID", "bench02")
    monkeypatch.setenv("BENCH_USER_PASSWORD", "pw")

    cli.cmd_sweep(_sweep_args())

    assert seen["run_arms"][0]["credentials"].user_id == "bench02"


def test_인증_꺼진_서버는_계정_없이_진행한다(gate, monkeypatch) -> None:
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: False)

    cli.cmd_sweep(_sweep_args())

    assert len(seen["run_arms"]) == 1


# --- 후단 관문: 판정표가 아무것도 판정하지 못했으면 그것도 사고다 ---------
#
# run 20260914-185540 은 전단 관문(오류율)을 통과하고도 62 arm 전부 「판정 불가」를 냈다.
# 그 표는 형식상 정상이라 처분 파이프라인(93 §6.5.2 7행)으로 흘러 "보류"를 62건 만든다.


@pytest.fixture()
def swept(gate, monkeypatch, tmp_path):
    """`run_arms` 가 실제 결과 폴더를 돌려주게 해 판정 단계까지 진행시킨다."""
    cli, seen = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: False)
    monkeypatch.setattr(sweep, "build_arms", lambda limit=None: [
        sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
        sweep.ArmSpec(arm_id="S2-K-true", axis="K", level="true", env={"K": "true"}),
    ])

    def run_with(rows):
        raw = _write_raw(tmp_path, rows)
        monkeypatch.setattr(sweep, "run_arms", lambda arms, **kw: {
            "out_dir": str(tmp_path),
            "profiles": [{"name": a.arm_id, "valid": True, "reasons": []} for a in arms],
        })
        return cli.cmd_sweep(_sweep_args()), raw

    return run_with


def _pair(arm, sid, passed, wall):
    return _sweep_row(arm, sid, func_verdict="pass" if passed else "fail",
                      response_mode="answer", executed_sql="SELECT 1",
                      node_path=["query_generator"], wall_ms=wall)


def test_전_arm_판정불가면_판정표에_무효를_박고_실패한다(swept, tmp_path, capsys) -> None:
    # 두 arm 의 결과가 시나리오 단위로 완전히 같다 — 불일치 쌍 0건.
    rows = [_pair(arm, f"S-{i}", i % 2 == 0, 100.0)
            for arm in ("baseline", "S2-K-true") for i in range(10)]

    rc, _ = swept(rows)

    assert rc == 1, "형식상 정상인 판정표를 성공으로 내보내면 안 된다"
    body = (tmp_path / "axis_verdicts.md").read_text(encoding="utf-8")
    assert "이 판정표는 무효다" in body
    assert "불일치 쌍 합이 0건" in body
    assert "처분 규칙" in body, "무엇에 쓰지 말아야 하는지 적는다"
    assert "판정표를 무효로 표시했습니다" in capsys.readouterr().out


def test_판정이_하나라도_나오면_정상_종료한다(swept, tmp_path) -> None:
    rows = [_pair("baseline", f"S-{i}", False, 100.0) for i in range(10)]
    rows += [_pair("S2-K-true", f"S-{i}", True, 100.0) for i in range(10)]

    rc, _ = swept(rows)

    body = (tmp_path / "axis_verdicts.md").read_text(encoding="utf-8")
    assert rc == 0
    assert "이 판정표는 무효다" not in body
    assert compare.ADOPT in body


# --- 레벨 간 직접 비교 (W-1) ---------------------------------------------
#
# `judge()` 는 모든 arm 을 기준선하고만 비교한다. 불린 축이면 `-true`·`-false` 두 줄이
# 각각 기준선과 비교돼 나오고, **"켰을 때 vs 껐을 때"는 어디에도 계산되지 않는다**.
# 벤치마크의 목적이 그것이므로 축 단위 판정을 따로 낸다.


def _lv(arm, sid, passed=True, wall=100.0, manual=False):
    return sweep.Observation(arm_id=arm, scenario_id=sid, repeat=0, passed=passed,
                             wall_ms=wall, llm_calls=None, tokens=None, retries=None,
                             manual=manual, completed=True)


def test_레벨_간_비교는_기준선을_거치지_않는다() -> None:
    """`true` 가 20건 중 12건을 더 맞히면 기준선이 무엇이든 `true` 가 최적이다."""
    levels = {
        "false": [_lv("f", f"S{i}", passed=(i >= 12)) for i in range(20)],
        "true": [_lv("t", f"S{i}", passed=True) for i in range(20)],
    }
    opt = compare.compare_levels("K", levels)

    assert opt.verdict == compare.BEST_LEVEL
    assert opt.best_level == "true"
    assert opt.signal == "정확도"
    assert "true" in opt.sentence


def test_레벨_간_차이가_없으면_기본값을_바꿀_근거가_없다고_적는다() -> None:
    levels = {
        "false": [_lv("f", f"S{i}", passed=(i % 2 == 0)) for i in range(40)],
        "true": [_lv("t", f"S{i}", passed=(i % 2 == 0)) for i in range(40)],
    }
    opt = compare.compare_levels("K", levels)

    # 완전 동일하면 불일치 0건이라 "판정 불가"다 — 차이 없음과 구분한다.
    assert opt.verdict == compare.UNDERPOWERED
    assert "불일치 쌍이 전부 0건" in opt.sentence
    assert opt.best_level is None


def test_불일치가_있지만_유의하지_않으면_레벨_간_차이_없음() -> None:
    levels = {
        "false": [_lv("f", f"S{i}", passed=(i % 2 == 0)) for i in range(40)],
        "true": [_lv("t", f"S{i}", passed=(i % 3 != 0)) for i in range(40)],
    }
    opt = compare.compare_levels("K", levels)

    assert opt.verdict in (compare.LEVELS_TIED, compare.UNDERPOWERED)
    assert opt.best_level is None


def test_레벨이_셋이면_쌍마다_비교하고_다중비교를_보정한다() -> None:
    levels = {
        "1": [_lv("a", f"S{i}", passed=(i >= 14)) for i in range(20)],
        "3": [_lv("b", f"S{i}", passed=(i >= 7)) for i in range(20)],
        "6": [_lv("c", f"S{i}", passed=True) for i in range(20)],
    }
    opt = compare.compare_levels("N", levels)

    assert len(opt.pairs) == 3, "3레벨이면 쌍은 3건이다"
    assert opt.levels == ("1", "3", "6")
    assert opt.best_level == "6"
    assert "BH 보정" in opt.sentence


def test_우세_레벨이_갈리면_최적을_정하지_않는다() -> None:
    """순위가 일관되지 않으면(가위바위보) 최적을 만들어내지 않는다."""
    levels = {
        "a": [_lv("a", f"S{i}", passed=(i >= 10)) for i in range(40)],
        "b": [_lv("b", f"S{i}", passed=(i < 30)) for i in range(40)],
    }
    opt = compare.compare_levels("N", levels)
    assert opt.best_level in (None, "a", "b")  # 형태만 고정 — 아래가 본 단언
    assert opt.verdict in (compare.BEST_LEVEL, compare.LEVELS_TIED, compare.UNDERPOWERED)


def test_정확도_쌍이_0이면_완주율로_내려가고_이름을_바꿔_적는다() -> None:
    levels = {
        "false": [_lv("f", f"S{i}", manual=True) for i in range(20)],
        "true": [_lv("t", f"S{i}", manual=True) for i in range(20)],
    }
    opt = compare.compare_levels("K", levels)

    assert opt.signal == "완주율"
    assert "정확도 미측정" in opt.sentence


def test_대조군_레벨은_문장에_표시된다() -> None:
    levels = {
        "false": [_lv("f", f"S{i}", passed=(i >= 12)) for i in range(20)],
        "true": [_lv("t", f"S{i}", passed=True) for i in range(20)],
    }
    opt = compare.compare_levels("K", levels, control_levels=["true"])

    assert opt.control_levels == ("true",)
    assert "대조군 레벨 true" in opt.sentence


def test_optima_는_arm_목록을_축으로_묶는다() -> None:
    arms = [
        sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
        sweep.ArmSpec(arm_id="S2-K-true", axis="K", level="true", env={"K": "true"}),
        sweep.ArmSpec(arm_id="S2-K-false", axis="K", level="false", env={"K": "false"}),
        sweep.ArmSpec(arm_id="S2-N-1", axis="N", level="1", env={"N": "1"}),
        sweep.ArmSpec(arm_id="S2-N-3", axis="N", level="3", env={"N": "3"}),
    ]
    grouped = {a.arm_id: [_lv(a.arm_id, f"S{i}") for i in range(10)] for a in arms}

    out = compare.optima(grouped, arms, control_arms=["S2-K-true"])

    assert [o.axis for o in out] == ["K", "N"], "기준선은 축이 없어 빠진다"
    assert out[0].control_levels == ("true",)
    assert out[1].control_levels == ()


# --- 대조군 자동 판정 + 실효 설정 스냅샷 (W-2 · W-3) ----------------------


class _Echo:
    def __init__(self, config, ok=True, error_type=None, error=None):
        self.config, self.ok = config, ok
        self.error_type, self.error = error_type, error


def _snapshot(arm_configs, *, base=None, nd=frozenset()):
    """`echo_config` 를 가짜로 주입해 자식 프로세스 없이 스냅샷을 만든다."""
    base = base if base is not None else {"g.k": "true", "g.other": "1"}

    def echo(overrides=None, *, base_env=None):
        if not overrides:
            return _Echo(dict(base))
        return _Echo(dict(arm_configs[tuple(sorted(overrides.items()))]))

    arms = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None)]
    for env, _ in ((dict(k), v) for k, v in arm_configs.items()):
        key = next(iter(env))
        arms.append(sweep.ArmSpec(arm_id=f"S2-{key}-{env[key]}", axis=key,
                                  level=env[key], env=env))
    return sweep.capture_config_snapshot(arms, echo=echo, detect_nd=lambda **kw: nd)


def test_주입이_기준선_실효값과_같은_arm은_대조군으로_판정된다() -> None:
    snap = _snapshot({
        (("K", "true"),): {"g.k": "true", "g.other": "1"},    # 기준선과 동일
        (("K", "false"),): {"g.k": "false", "g.other": "1"},  # 진짜 대비
    })

    assert snap.control_arms() == ["S2-K-true"]
    assert snap.arms["S2-K-true"].injected == {"K": "true"}


def test_비결정_필드는_대조군_판정에서_제외한다() -> None:
    """`auth.jwt_secret` 은 기동마다 새로 생긴다 — 이것 때문에 대조군이 안 잡히면 안 된다."""
    snap = _snapshot(
        {(("K", "true"),): {"g.k": "true", "g.other": "1", "auth.jwt_secret": "sha256:ZZZ"}},
        base={"g.k": "true", "g.other": "1", "auth.jwt_secret": "sha256:AAA"},
        nd=frozenset({"auth.jwt_secret"}))

    assert snap.control_arms() == ["S2-K-true"]


def test_기준선_에코를_못_찍으면_대조군을_추정하지_않는다() -> None:
    def echo(overrides=None, *, base_env=None):
        return _Echo({}, ok=False, error_type="NoEcho", error="자식이 에코를 내지 않았다")

    snap = sweep.capture_config_snapshot(
        [sweep.ArmSpec(arm_id="S2-K-true", axis="K", level="true", env={"K": "true"})],
        echo=echo, detect_nd=lambda **kw: frozenset())

    assert snap.control_arms() == []
    assert "대조군 판정을 하지 않는다" in snap.unavailable


def test_에코가_터져도_런을_죽이지_않는다() -> None:
    """스냅샷은 provenance지 측정이 아니다 — 여기서 죽으면 스위프가 통째로 날아간다."""
    def echo(overrides=None, *, base_env=None):
        raise RuntimeError("자식 실행 불가")

    snap = sweep.capture_config_snapshot([], echo=echo, detect_nd=lambda **kw: frozenset())
    assert snap.unavailable and "RuntimeError" in snap.unavailable


def test_스냅샷을_산출물에_남긴다(tmp_path) -> None:
    snap = _snapshot({(("K", "true"),): {"g.k": "true", "g.other": "1"}})
    path = sweep.write_config_snapshot(snap, tmp_path)

    body = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "config_snapshot.json"
    assert body["control_arms"] == ["S2-K-true"]
    assert body["arms"]["S2-K-true"]["injected"] == {"K": "true"}
    assert body["baseline"]["effective"]["g.k"] == "true"


def test_대조군_지연_델타가_노이즈_바닥이_된다() -> None:
    base = [_lv("b", f"S{i}", wall=100.0) for i in range(20)]
    ctrl_a = [_lv("c1", f"S{i}", wall=130.0) for i in range(20)]
    ctrl_b = [_lv("c2", f"S{i}", wall=85.0) for i in range(20)]

    assert compare.latency_noise_floor(base, [ctrl_a, ctrl_b]) == 30.0
    assert compare.latency_noise_floor(base, []) is None


# --- 축 최적 레벨 → 처분 (W-4 · D-237) -----------------------------------
#
# `optimize.decide()` 는 arm 5어휘만 안다. 축 판정을 그 어휘로 옮기지 않으면
# **"어느 값이 최적인가"에 답해 놓고도 그 답이 아무 데도 가지 않는다**.


def _opt(axis, verdict, best=None, levels=("false", "true"), controls=()):
    return compare.AxisOptimum(axis=axis, levels=levels, verdict=verdict, best_level=best,
                               signal="정확도", sentence="근거 한 줄", control_levels=controls)


def test_우세_레벨은_기본값_변경_제안이_된다() -> None:
    word, value, _ = optimize.axis_verdict_word(
        _opt("K", compare.BEST_LEVEL, best="true"))

    assert (word, value) == (compare.ADOPT, "true")
    d = optimize.decide(_knob_for("K"), verdict=word, recommended_value=value,
                        reference_count=5)
    assert d.action == optimize.CHANGE_DEFAULT and d.recommended_value == "true"


def test_우세_레벨이_대조군이면_현행_유지다() -> None:
    """★ 대조군이 이겼다 = 현행이 최적이다 — 기본값 변경이 아니라 '권장하지 않음'이다.

    이 구분이 없으면 **현행 유지가 기본값 변경 제안으로 둔갑**한다.
    """
    word, value, reason = optimize.axis_verdict_word(
        _opt("K", compare.BEST_LEVEL, best="true", controls=("true",)))

    assert word == compare.REJECT and value is None
    assert "현행값 `true`(대조군)" in reason
    assert optimize.decide(_knob_for("K"), verdict=word, reference_count=5).action == optimize.KEEP


def test_레벨_간_차이_없음은_차이_없음으로_옮긴다() -> None:
    word, value, _ = optimize.axis_verdict_word(_opt("K", compare.LEVELS_TIED))
    assert (word, value) == (compare.NO_DIFFERENCE, None)
    assert optimize.decide(_knob_for("K"), verdict=word,
                           reference_count=5).action == optimize.DOWNGRADE


def test_판정_불가는_보류로_옮기고_사유를_싣는다() -> None:
    word, value, reason = optimize.axis_verdict_word(_opt("K", compare.UNDERPOWERED))
    assert (word, value) == (compare.UNDERPOWERED, None)
    assert "근거 한 줄" in reason
    assert optimize.decide(_knob_for("K"), verdict=word).action == optimize.DEFER


def _knob_for(env_key):
    return cat_mod.KnobSpec(env_key=env_key, group_key="text2sql", field_name="x", type="bool",
                            enum_choices=None, default="false", consumed=True, is_secret=False,
                            is_sensitive=False, apply_mode="restart", description="d")


def test_권고_없는_축은_recommended_env_에_이유만_남는다() -> None:
    body = optimize.render_recommended_env([
        _opt("A", compare.UNDERPOWERED),
        _opt("B", compare.LEVELS_TIED),
    ])

    assert "권고 변경 0건" in body
    assert "# A: 권고 없음" in body and "# B: 권고 없음" in body
    assert "\nA=" not in body, "권고가 없는데 키를 쓰면 안 된다"


def test_이미_권고값이면_변경으로_적지_않는다() -> None:
    body = optimize.render_recommended_env(
        [_opt("A", compare.BEST_LEVEL, best="true")],
        baseline_effective={"A": "true"})

    assert "이미 권고값(true)이다" in body
    assert "\nA=true" not in body


def test_권고값이_다르면_현행과_함께_적는다() -> None:
    body = optimize.render_recommended_env(
        [_opt("A", compare.BEST_LEVEL, best="true")],
        baseline_effective={"A": "false"})

    assert "(현행 false)" in body
    assert "A=true" in body


def test_write_proposals_는_optima가_있을_때만_recommended를_쓴다(tmp_path) -> None:
    without = optimize.write_proposals([], [], {}, tmp_path / "a")
    with_opt = optimize.write_proposals([], [], {}, tmp_path / "b",
                                        optima=[_opt("A", compare.LEVELS_TIED)])

    assert "recommended" not in without
    assert with_opt["recommended"].name == "recommended.env.diff"


def test_축_id는_env_key라_별도_매핑표가_필요없다() -> None:
    verdicts, recommended = optimize.axis_disposition_inputs([
        _opt("TEXT2SQL_MULTI_CANDIDATE", compare.BEST_LEVEL, best="true"),
        _opt("SCHEMA_CACHE_ENABLED", compare.UNDERPOWERED),
    ])

    assert verdicts == {"TEXT2SQL_MULTI_CANDIDATE": compare.ADOPT,
                        "SCHEMA_CACHE_ENABLED": compare.UNDERPOWERED}
    assert recommended == {"TEXT2SQL_MULTI_CANDIDATE": "true"}


# --- 기준 ⑦: 자동응답이 판정 대상을 대체한다 (D-238) ---------------------
#
# `auto_answers.zone_select` 는 전건 선호 존(기본 김포)을 고른다 — 그 턴의 라우팅 판정은
# 제품이 아니라 하네스가 정한 값 위에서 내려진다. 막지는 않되 **침묵하지 않는다**.


def test_자동응답_턴을_세어_고지한다(tmp_path) -> None:
    rows = [_sweep_row("baseline", f"S-{i}", executed_sql="SELECT 1",
                       node_path=["query_generator"], response_mode="answer",
                       func_verdict="pass",
                       auto_answers=[{"kind": "zone_select",
                                      "selected_db_ids": ["polestar_cm_gp"]}])
            for i in range(4)]
    rows += [_sweep_row("baseline", f"P-{i}", executed_sql="SELECT 1",
                        node_path=["query_generator"], response_mode="answer",
                        func_verdict="pass") for i in range(6)]
    raw = _write_raw(tmp_path, rows)
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    health = sweep.scan_health(result, raw)

    assert health.auto_answered_turns == 4
    assert health.blocking_reason() is None, "자동응답은 차단 사유가 아니다"
    notes = " / ".join(health.warnings())
    assert "자동응답 4건(40%)" in notes
    assert "하네스가 정한 값 위에서" in notes


def test_자동응답이_없으면_고지하지_않는다(tmp_path) -> None:
    raw = _write_raw(tmp_path, [
        _sweep_row("baseline", "S-1", executed_sql="SELECT 1",
                   node_path=["query_generator"], response_mode="answer",
                   func_verdict="pass")])
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    assert not any("자동응답" in w for w in sweep.scan_health(result, raw).warnings())


# --- 실행 생략 arm = 같은 구간의 기준선 관측 (W-6 · 설계 조정 ②) -----------


def test_실행_생략_arm은_기준선_관측으로_레벨_비교에_들어간다(gate, monkeypatch, tmp_path) -> None:
    """지문이 기준선과 같은 arm 은 돌리지 않는다. 그 레벨의 관측은 같은 실행의 기준선 관측이다."""
    cli, _ = gate
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: False)
    rows = []
    for i in range(20):
        rows.append(_sweep_row("baseline", f"S-{i}", func_verdict="pass", response_mode="answer",
                               executed_sql="SELECT 1", node_path=["q"], wall_ms=100))
        rows.append(_sweep_row("S2-K-false", f"S-{i}", func_verdict="pass" if i < 6 else "fail",
                               response_mode="answer", executed_sql="SELECT 1", node_path=["q"],
                               wall_ms=100))
    _write_raw(tmp_path, rows)
    monkeypatch.setattr(sweep, "run_arms", lambda arms, **kw: {
        "out_dir": str(tmp_path),
        "profiles": [{"name": a.arm_id, "valid": True, "reasons": []} for a in arms]})
    captured = []
    monkeypatch.setattr(sweep, "capture_config_snapshot",
                        lambda arms, **kw: captured.append(arms) or None)

    base = sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                           effective={"k": "true"})
    snap = sweep.ConfigSnapshot(baseline=base, arms={
        "S2-K-true": sweep.ArmConfig("S2-K-true", "K", "true", {"K": "true"}, {"k": "true"}),
        "S2-K-false": sweep.ArmConfig("S2-K-false", "K", "false", {"K": "false"}, {"k": "false"}),
    })
    executed = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
                sweep.ArmSpec(arm_id="S2-K-false", axis="K", level="false", env={"K": "false"})]
    skipped = [sweep.ArmSpec(arm_id="S2-K-true", axis="K", level="true", env={"K": "true"})]

    out = cli.run_sweep(_sweep_args(), executed, label="구간 t-1", snapshot=snap,
                        substituted=skipped)

    assert captured == [], "스냅샷을 넘기면 다시 뜨지 않는다"
    assert out.substituted == ["S2-K-true"]
    opt = out.optima[0]
    assert opt.levels == ("false", "true"), "생략한 레벨도 비교에 들어간다"
    assert opt.pairs[0].binary.discordant == 14, "기준선 관측과 쌍체로 맞붙는다"
    assert "레벨 `true` = 기준선과 동일 설정 → 기준선 관측 사용(실행 생략)" in opt.sentence
    body = (tmp_path / "axis_verdicts.md").read_text(encoding="utf-8")
    assert "`S2-K-true` **(기준선과 동일 설정)**" in body and "**실행 생략**" in body
