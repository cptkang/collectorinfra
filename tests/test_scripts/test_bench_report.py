"""`scripts/bench/report.py`·`__main__.py` 테스트 (plans/93 · T-07·T-09)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import catalog, report as rp, validate  # noqa: E402
from scripts.bench import __main__ as cli  # noqa: E402


def _knob(env_key="FLAG_A", **over) -> catalog.KnobSpec:
    base = dict(
        env_key=env_key, group_key="text2sql", field_name="flag_a", type="bool",
        enum_choices=None, default="false", consumed=True, is_secret=False,
        is_sensitive=False, apply_mode="restart", description="d",
    )
    base.update(over)
    return catalog.KnobSpec(**base)


def _report(**over) -> rp.HealthReport:
    base = dict(run_id="t-01", integrity=[], shadowed=[], boot=[], consumption=[],
                unconsumed_cmp=None, ledger=[], notes={})
    base.update(over)
    return rp.HealthReport(**base)


# ── 장부: 사유 강제 ────────────────────────────────────────

def test_ledger_row_requires_reason_for_unmeasured():
    """★ 사유 없는 미측정은 예외다 — 침묵 제외 금지의 기계적 강제(§5.4.2)."""
    row = rp.LedgerRow(env_key="K", group_key="g", type="bool",
                       l1=rp.COVERED, l2=rp.UNMEASURED, l3=rp.COVERED,
                       l4=rp.COVERED, l5=rp.COVERED, grade="B", unmeasured_reason="")
    with pytest.raises(rp.LedgerIncomplete):
        row.validate()


def test_ledger_row_with_reason_passes():
    row = rp.LedgerRow(env_key="K", group_key="g", type="bool",
                       l1=rp.COVERED, l2=rp.UNMEASURED, l3=rp.COVERED,
                       l4=rp.COVERED, l5=rp.COVERED, grade="B",
                       unmeasured_reason="L2 미실행")
    row.validate()  # 예외 없음


def test_ledger_row_all_covered_needs_no_reason():
    row = rp.LedgerRow(env_key="K", group_key="g", type="bool",
                       l1=rp.COVERED, l2=rp.COVERED, l3=rp.COVERED,
                       l4=rp.COVERED, l5=rp.COVERED, grade="B")
    row.validate()


def test_build_ledger_covers_every_knob():
    knobs = [_knob("A_FLAG"), _knob("B_FLAG"), _knob("C_FLAG")]
    rows = rp.build_ledger(knobs)
    assert len(rows) == len(knobs)
    assert {r.env_key for r in rows} == {"A_FLAG", "B_FLAG", "C_FLAG"}


def test_build_ledger_marks_shadowed_as_failed():
    knob = _knob()
    rows = rp.build_ledger(
        [knob], shadowed=[validate.ShadowedKey("FLAG_A", "true", "false", "os_env")],
        injection_checked=frozenset({"FLAG_A"}))
    assert rows[0].l3 == rp.FAILED


def test_build_ledger_marks_boot_failure():
    knob = _knob()
    rows = rp.build_ledger(
        [knob], boot=[validate.BootFinding("FLAG_A", "true", ok=False, error_type="ValidationError")])
    assert rows[0].l2 == rp.FAILED


def test_build_ledger_reason_uses_f1_exclusion_when_out_of_scope():
    """대상 밖인 키는 '미실행'이 아니라 **왜 대상이 아닌지**를 적는다."""
    knob = _knob("NOISE_X", group_key="noise_gate")
    rows = rp.build_ledger([knob])
    assert "질의 경로 밖" in rows[0].unmeasured_reason


def test_build_ledger_l5_always_unmeasured_here():
    """이 모듈은 성능을 재지 않는다 — 장부가 그 사실을 숨기지 않아야 한다."""
    rows = rp.build_ledger([_knob()])
    assert rows[0].l5 == rp.UNMEASURED
    assert "L5 미측정" in rows[0].unmeasured_reason


# ── 등급 ───────────────────────────────────────────────────

@pytest.mark.parametrize("key,over,expected", [
    ("LLM_PROVIDER", {}, "A"),
    ("ACTIVE_DB_IDS", {}, "A"),
    ("SOME_SECRET", {"is_secret": True}, "A"),
    ("DBHUB_BASE_URL", {}, "A"),
    ("TEXT2SQL_SEMANTIC_COMPOSE", {}, "B"),
    ("NOISE_X", {"group_key": "noise_gate"}, "C"),
])
def test_assign_grade(key, over, expected):
    knob = _knob(key, **over)
    assert rp.assign_grade(knob, in_env=False,
                           excluded_reason=catalog.f1_exclusion_reason(knob)) == expected


# ── 즉시 조치 ──────────────────────────────────────────────

def test_immediate_actions_orders_defects_first():
    report = _report(
        integrity=[catalog.IntegrityFinding("GHOST", "orphan", "파일에만")],
        shadowed=[validate.ShadowedKey("SHADOW", "1", "2", "encenv")],
        boot=[validate.BootFinding("BAD", "x", ok=False, error_type="ValidationError", error="e")],
    )
    actions = report.immediate_actions
    assert len(actions) == 3
    assert "고아" in actions[0] and "가림" in actions[1] and "기동 실패" in actions[2]


def test_immediate_actions_empty_when_clean():
    assert _report().immediate_actions == []


# ── 렌더 ───────────────────────────────────────────────────

def test_render_summary_always_has_next_step():
    """★ `다음 할 일` 줄이 항상 있다 — 개발자가 '그래서 뭘 하죠'를 묻지 않게(§5.5)."""
    assert "다음 할 일" in rp.render_summary(_report())
    dirty = _report(integrity=[catalog.IntegrityFinding("G", "orphan", "d")])
    assert "다음 할 일" in rp.render_summary(dirty)


def test_render_ledger_row_count_matches():
    rows = rp.build_ledger([_knob("A_FLAG"), _knob("B_FLAG")])
    text = rp.render_ledger(rows)
    assert text.count("| `") == 2


def test_render_health_has_required_sections():
    text = rp.render_health(_report(ledger=rp.build_ledger([_knob()])))
    for section in ("즉시 조치", "카탈로그 정합", "주입 실효성", "기동 안전성",
                    "소비 실증", "등급 초안", "커버리지 요약"):
        assert section in text


def test_write_report_creates_three_files(tmp_path):
    report = _report(ledger=rp.build_ledger([_knob()]))
    paths = rp.write_report(report, tmp_path / "run")
    assert set(paths) == {"summary", "health", "ledger"}
    assert all(p.exists() and p.read_text(encoding="utf-8") for p in paths.values())


def test_write_report_does_not_touch_repo_config(tmp_path):
    """★ V5 — 어떤 경로로도 `.env`·`config.py`를 쓰지 않는다."""
    env, cfg = _ROOT / ".env", _ROOT / "src" / "config.py"
    before = (env.stat().st_mtime if env.exists() else None, cfg.stat().st_mtime)
    rp.write_report(_report(ledger=rp.build_ledger([_knob()])), tmp_path / "run")
    after = (env.stat().st_mtime if env.exists() else None, cfg.stat().st_mtime)
    assert before == after


# ── CLI 승인 정책 ─────────────────────────────────────────

@pytest.mark.parametrize("worker,orchestrator,need", [
    ("fabrix", "vllm", False), ("ollama", "vllm", False),
    ("mlx", "mlx", False), ("fabrix", "mlx", False),
    ("gemini", "vllm", True), ("unknown", "vllm", True),
    # D-222(G-3): 오케스트레이터 평면도 판정한다 — 워커가 내부망이어도
    # gemini 오케스트레이터는 승인 대상
    ("mlx", "gemini", True), ("ollama", "gemini", True), ("fabrix", "unknown", True),
])
def test_approval_policy(worker, orchestrator, need):
    """내부망 면제는 **내부망에만** 적용된다 — 외부는 D-127이 그대로 산다(§4.4)."""
    required, reason = cli.approval_policy(worker, orchestrator)
    assert required is need
    if need:
        # 사유에는 **승인이 필요한 평면**이 적힌다
        assert (f"LLM_PROVIDER={worker}" in reason
                or f"ORCHESTRATOR_PROVIDER={orchestrator}" in reason)
    else:
        assert worker in reason and orchestrator in reason


def test_approval_reason_is_human_readable():
    _, reason = cli.approval_policy("fabrix", "vllm")
    assert "승인 없이" in reason


def test_approval_policy_reads_both_planes_from_echo():
    """에코가 설정 전체를 평탄화하므로 오케스트레이터 provider도 실려 있다(plans/100 J-4)."""
    echo = cli.probe.EchoResult(
        ok=True, config={"llm.provider": "mlx", "orchestrator.provider": "gemini"}
    )
    assert cli._providers_of(echo) == ("mlx", "gemini")


def test_cli_sweep_dry_run_expands_arms(capsys):
    """`--mode dry`는 arm 전개만 하고 아무것도 실행하지 않는다."""
    rc = cli.main(["--sweep", "--mode", "dry", "--scale", "smoke"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "baseline" in out and "arm" in out


def test_cli_sweep_run_mode_blocks_external_provider_without_approval(monkeypatch, capsys):
    """★ 내부망 면제는 내부망에만 — 외부 프로바이더는 승인 없이 실행하지 않는다(D-127)."""
    monkeypatch.setattr(cli, "_providers_of", lambda echo: ("gemini", "vllm"))
    rc = cli.main(["--sweep", "--mode", "run", "--scale", "smoke"])
    out = capsys.readouterr().out
    assert rc == 2
    assert "승인" in out and "--yes" in out


def test_cli_sweep_run_mode_allows_internal_provider(monkeypatch):
    """내부망이면 승인 프롬프트 없이 통과한다(이후 실행은 하네스 몫)."""
    monkeypatch.setattr(cli, "_providers_of", lambda echo: ("fabrix", "vllm"))
    calls = {}

    def fake_run(arms, **kwargs):
        calls["n"] = len(arms)
        raise cli.sweep_mod.SweepUnavailable("테스트 — 여기서 멈춘다")

    monkeypatch.setattr(cli.sweep_mod, "run_arms", fake_run)
    rc = cli.main(["--sweep", "--mode", "run", "--scale", "smoke"])
    assert calls.get("n", 0) > 0, "내부망인데 승인에서 막혔다"
    assert rc == 2  # SweepUnavailable로 멈춘 것


def test_cli_sweep_run_mode_stops_when_mlx_cannot_generate(monkeypatch, capsys):
    """arm 서버를 띄우기 전에 로컬 MLX 가 생성하는지 본다 — 죽은 서버면 arm 전부가 무효다."""
    from scripts.scenario.preflight import VERDICT_STOP, Check

    monkeypatch.setattr(cli, "_providers_of", lambda echo: ("mlx", "mlx"))
    monkeypatch.setattr(cli, "mlx_run_blockers", lambda: [
        Check("MLX 생성(워커)", "응답 없음", VERDICT_STOP, "서버를 내리고 다시 띄운다")])
    monkeypatch.setattr(cli.sweep_mod, "run_arms",
                        lambda *a, **k: pytest.fail("MLX 가 준비되지 않았는데 arm 을 돌렸다"))
    rc = cli.main(["--sweep", "--mode", "run", "--scale", "smoke"])
    assert rc == 2
    assert "MLX 생성(워커)" in capsys.readouterr().out


def test_cli_parser_defaults():
    args = cli.build_parser().parse_args([])
    assert not args.quick and not args.ci and not args.sweep


# --- L5: 「판정 불가」는 「미실행」이 아니다 (D-237 ⑤) --------------------
#
# 종전 장부는 L5 미측정 사유를 "성능 스위프 미실행(실 LLM 필요)" 하나로 적었다.
# 스위프를 **돌렸는데** 축이 판정 불가로 나온 경우까지 그렇게 적히면, 재측정 대상인지
# 미착수인지 장부만 보고 구별할 수 없다(run 20260914-185540: 28축 중 27축이 판정 불가).


def test_스위프를_돌린_축은_미실행이_아니라_판정_불가로_적는다():
    rows = rp.build_ledger(
        [_knob("TEXT2SQL_MULTI_CANDIDATE")],
        l5_unjudged={"TEXT2SQL_MULTI_CANDIDATE": "불일치 쌍이 전부 0건이다"},
    )
    reason = rows[0].unmeasured_reason

    assert "L5 판정 불가" in reason
    assert "성능 스위프 미실행" not in reason, "돌렸는데 미실행이라고 적으면 거짓이다"


def test_스위프를_안_돌린_축은_종전대로_미실행이다():
    rows = rp.build_ledger([_knob("TEXT2SQL_MULTI_CANDIDATE")])
    assert "L5 미측정 — 성능 스위프 미실행" in rows[0].unmeasured_reason


def test_커버리지_요약의_L5는_장부에서_센다():
    """종전에는 "0건"이 상수로 박혀 있어 스위프 뒤에도 미실행이라고 적혔다."""
    ledger = rp.build_ledger([_knob("A"), _knob("B")], l5_unjudged={"A": "불일치 0건"})

    body = rp.render_health(_report(ledger=ledger))

    assert "판정 불가 1건" in body
    assert "재측정 대기" in body
