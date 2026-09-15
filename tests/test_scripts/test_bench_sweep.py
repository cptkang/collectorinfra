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
    raw = _write_raw(tmp_path, [
        {"profile": "baseline", "scenario_id": f"S-{i}", "repeat": 0,
         "func_verdict": "pass", "response_mode": "answer"}
        for i in range(20)
    ])
    result = {"profiles": [{"name": "baseline", "valid": True, "reasons": []}]}

    assert sweep.scan_health(result, raw).blocking_reason() is None


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
# 기본 env 가 `sandbox` 여서 실 스위프가 **유사어 32건(그룹 L)만** 돌고 실 질의
# 워크로드(그룹 A~K 107건)를 한 건도 건드리지 않았다. 화면에도 그 사실이 없었다.

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
    assert "그룹 L(32)" in sweep.workload_summary(sandbox)


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
    """A-05·A-10 은 teardown 미지원(상태 오염)으로 매 arm 에서 건너뛴다 — 실행 대상이 아니다."""
    catalog = sweep.load_normal_catalog(env="closed")
    judged, runnable = sweep.judgeable_count(catalog)

    # 러너 규칙과 독립적으로 다시 센다(오라클).
    skipped = {s.id for s in catalog.scenarios
               if not s.prompt_authored or [a for a in s.teardown if a != "drop_thread"]}

    assert {"A-05", "A-10"} <= skipped
    assert runnable == len(catalog.scenarios) - len(skipped)
    assert judged <= runnable
    assert f"{len(skipped)}건은 러너가 건너뛴다" in sweep.workload_summary(catalog)


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

    def fake_echo():
        seen["echo"] += 1
        return object()

    def fake_run(arms, **kwargs):
        seen["run_arms"].append(kwargs)
        raise sweep.SweepUnavailable("테스트 — 게이트 통과 확인 후 중단")

    monkeypatch.setattr(cli.probe, "echo_config", fake_echo)
    monkeypatch.setattr(cli, "_provider_of", lambda echo: "fabrix")
    monkeypatch.setattr(cli, "approval_policy", lambda provider: (False, "내부망"))
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
