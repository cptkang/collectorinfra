"""plans/118 트랙 B — 벤치 하네스 보강(게이트 없는 항목 B-2·B-3·B-4·B-5·B-6).

캠페인 `run-closed` 2구간(`composite-5` · run `20260922-162132`)이 축 측정으로 성립하지 않은
원인 중 **하네스 쪽**을 고친다. 서버·LLM·DB 0 — 스위프 1회는 가짜이고, 소비처 판정은 임시
디렉터리의 작은 가짜 저장소와 현 작업 트리 두 가지로 본다.

  B-2 재개·재시도 구간의 설정 스냅샷에 그 구간 arm 이 들어간다(종전 `arms: []`)
  B-3 소비처 없는 축은 축에서 빠지고(사유 「소비처 없음」), 단 전용 축은 기준선 단이 아니면
      도달 불가다 — 계획 표·합산 판정표 「미측정」 행에 사유가 남는다
  B-4 노이즈 바닥·구간 간 교란은 **같은 기준선 설정 지문끼리만** 잰다
  B-5 기준 경로 단은 `ladder.is_canonical` 정본을 읽는다(D-251 — 2단)
  B-6 지연 행에 둘 다 완주 쌍 지연 차 표준편차 · 효과가 그보다 작으면 「지연 신호 검정력 부족」
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.bench import axes, catalog, compare, sweep  # noqa: E402
from scripts.bench import campaign as cm  # noqa: E402
from tests.test_scripts import test_bench_campaign as campaign_tests  # noqa: E402
from tests.test_scripts.test_bench_campaign import _health, _state  # noqa: E402

seg = campaign_tests.seg

TIER1 = "deep_agent"
TIER2 = "intent_orchestration"
TIER3 = "semantic_router"
TIER2_NODES = ("input_parser", "intent_planner", "agent_orchestrator")
TIER1_NODES = ("input_parser", "deep_agent")


# ── B-2 재개·재시도 구간 스냅샷 ───────────────────────────────────────────────


def _capture_snapshots(monkeypatch):
    """`run_sweep` 이 받은 `snapshot` 을 모은다(원래 가짜 스위프에 그대로 넘긴다)."""
    from scripts.bench import __main__ as cli

    seen: list = []
    inner = cli.run_sweep

    def spy(args, run_arms, **kw):
        seen.append(kw.get("snapshot"))
        return inner(args, run_arms, **kw)

    monkeypatch.setattr(cli, "run_sweep", spy)
    return seen


def test_끊긴_구간을_이으면_스냅샷에_그_구간_arm_이_있다(seg, monkeypatch) -> None:
    """★ run `20260922-162132` 재현 — 재개 구간 `config_snapshot.json` 이 `arms: []` 였다."""
    run, calls, _, root = seg
    seen = _capture_snapshots(monkeypatch)
    run.interrupts.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run("next", "--mode", "mock")
    [rec] = _state(root, "mock-closed")["records"]
    assert rec["status"] == cm.RUNNING

    assert run("next", "--mode", "mock") == 0
    resumed = seen[1]
    assert resumed is not None and not resumed.unavailable
    assert set(resumed.arms) == set(rec["arm_ids"]), "재개 구간 arm 이 스냅샷에 있다"
    # 대조군 판정이 빈 입력으로 돌지 않는다(레벨 0 이 기준선과 같은 설정인 픽스처).
    assert resumed.control_arms(), "대조군 판정이 비어 있지 않다"
    assert calls[1] == calls[0]


def test_실패_구간을_다시_돌려도_스냅샷에_그_구간_arm_이_있다(seg, monkeypatch) -> None:
    run, _, outcomes, root = seg
    seen = _capture_snapshots(monkeypatch)
    outcomes.append((_health(clarify_turns=120), {}))          # 역질문 60% → 실패
    assert run("next", "--mode", "mock") == 1
    [rec] = _state(root, "mock-closed")["records"]
    assert rec["status"] == cm.FAILED

    assert run(rec["segment_id"], "--mode", "mock") == 0
    assert set(seen[1].arms) == set(rec["arm_ids"])


def test_재개_대상_축은_계획에_다시_배정되지_않는다(seg) -> None:
    """`frozen_axes` 를 DONE 으로 좁히지 않은 이유 — 좁히면 끊긴 축이 새 구간에도 배정된다."""
    from scripts.bench import __main__ as cli

    run, _, _, root = seg
    run.interrupts.append(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run("next", "--mode", "mock")
    [rec] = _state(root, "mock-closed")["records"]

    args = cli.build_parser().parse_args(["--segment", "next", "--mode", "mock"])
    _, _, campaign, _, _, plan, snapshot = cli._campaign_context(args)
    planned = {axis for s in plan.segments for axis in s.axes}
    assert not (set(rec["axes"]) & planned), "끊긴 구간의 축은 새 구간에 다시 배정하지 않는다"
    assert set(rec["arm_ids"]) <= set(snapshot.arms), "스냅샷에는 들어간다"
    assert campaign.reopened_axes() == set(rec["axes"])


def test_reopened_axes_는_실패_진행만_완료는_아니다(tmp_path) -> None:
    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    for sid, axis, status in (("x-1", "A", cm.DONE), ("x-2", "B", cm.FAILED),
                              ("y-1", "C", cm.RUNNING)):
        camp.records[sid] = cm.SegmentRecord(segment_id=sid, category=sid[0], axes=[axis],
                                             arm_ids=[], status=status, mode="run")
    assert camp.frozen_axes() == {"A", "B", "C"}
    assert camp.reopened_axes() == {"B", "C"}


# ── B-3 소비처 정적 판정 ──────────────────────────────────────────────────────


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for rel, body in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def _knob(env_key: str, group: str, field: str, type_: str = "bool") -> catalog.KnobSpec:
    return catalog.KnobSpec(env_key=env_key, group_key=group, field_name=field, type=type_,
                            enum_choices=None, default="false", consumed=True, is_secret=False,
                            is_sensitive=False, apply_mode="restart", description="t")


_FAKE_REPO = {
    "src/config.py":
        "class C:\n    ghost_llm_enabled: bool = False\n    render_mode: bool = False\n",
    "src/observability/investigation_metrics.py":
        "def echo(c):\n    return {'ghost_llm_enabled': getattr(c, 'ghost_llm_enabled', 0)}\n",
    "src/orchestration/process_query.py":
        'def f():\n    """ghost_llm_enabled 는 여기서 쓰지 않는다."""\n    return 1\n',
    "src/db_adapters/x/prompts.py":
        "_FIELD = 'render_mode'\n\ndef g(t):\n    return getattr(t, _FIELD)\n",
    "src/orchestration/deepagents_tools.py":
        "def h(cfg):\n    return getattr(cfg.composite, 'latest_scope_only', False)\n",
    "src/nodes/node.py": "def k(cfg):\n    return cfg.composite.fanout_probe\n",
    "src/tests/test_x.py": "def t(c):\n    return c.ghost_llm_enabled\n",
}


def test_읽기_형태_3종을_세고_독스트링_정의_에코는_세지_않는다(tmp_path) -> None:
    index = axes.scan_consumers(_repo(tmp_path, _FAKE_REPO))

    assert "ghost_llm_enabled" not in index.reads, \
        "정의(config.py)·기동 에코·독스트링·패키지 안 tests/ 는 소비처가 아니다"
    assert index.reads["render_mode"] == {"src/db_adapters/x/prompts.py"}, "필드명 상수"
    assert index.reads["latest_scope_only"] == {"src/orchestration/deepagents_tools.py"}
    assert index.reads["fanout_probe"] == {"src/nodes/node.py"}, "속성 접근"
    assert index.dynamic == (), "상수 이름으로 부른 getattr 는 동적 접근이 아니다"


def test_소비처_없음은_제외_단_전용은_표시_나머지는_남는다(tmp_path) -> None:
    index = axes.scan_consumers(_repo(tmp_path, _FAKE_REPO))
    knobs = [_knob("X_GHOST_LLM_ENABLED", "composite", "ghost_llm_enabled"),
             _knob("X_PRIOR_SCOPE_LATEST", "composite", "latest_scope_only"),
             _knob("X_FANOUT_PROBE", "composite", "fanout_probe"),
             _knob("X_KNOWLEDGE_RENDER", "text2sql", "render_mode")]

    found, decisions = axes.select_axes(knobs, consumers=index)
    by_key = {a.env_key: a for a in found}
    [dropped] = [d for d in decisions if d.stage == "F3"]

    assert dropped.env_key == "X_GHOST_LLM_ENABLED" and not dropped.included
    assert dropped.reason.startswith("소비처 없음") and dropped.tier == "primary"
    assert by_key["X_PRIOR_SCOPE_LATEST"].tier_only == TIER1
    assert by_key["X_FANOUT_PROBE"].tier_only is None
    assert "X_KNOWLEDGE_RENDER" in by_key, "필드명 상수로 읽는 축을 오탐으로 빼지 않는다"


def test_동적_접근이_있으면_판정하지_않는다(tmp_path) -> None:
    files = dict(_FAKE_REPO)
    files["src/nodes/dyn.py"] = "def d(cfg, name):\n    return getattr(cfg.composite, name)\n"
    index = axes.scan_consumers(_repo(tmp_path, files))
    verdict = axes.consumers_of(_knob("X_GHOST", "composite", "ghost_llm_enabled"), index)

    assert not verdict.none and verdict.undetermined
    assert "src/nodes/dyn.py:2" in verdict.undetermined
    found, decisions = axes.select_axes(
        [_knob("X_GHOST_LLM_ENABLED", "composite", "ghost_llm_enabled")], consumers=index)
    assert [a.env_key for a in found] == ["X_GHOST_LLM_ENABLED"], "종전대로 잰다"
    assert not [d for d in decisions if d.stage == "F3"]


def test_가짜_노브만_주면_저장소_판정을_하지_않는다() -> None:
    """단위 테스트가 저장소 코드에 묶이지 않는다(`include_structural` 과 같은 규칙)."""
    found, decisions = axes.select_axes([_knob("X_NOBODY_LLM_ENABLED", "composite", "nobody")])
    assert [a.env_key for a in found] == ["X_NOBODY_LLM_ENABLED"]
    assert not [d for d in decisions if d.stage == "F3"]


#: 현 작업 트리 실측(2026-09-23) — 1차 축 29개(구조 축 제외) 중 F3 로 빠지는 것은 1개뿐이다.
_PRIMARY_BEFORE_B3 = {
    "COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED", "COMPOSITE_SEQUENTIAL_REPLAN_ENABLED",
    "COMPOSITE_TARGET_COLUMN_LLM_ENABLED", "COMPOSITE_AVAILABILITY_PRECHECK_ENABLED",
    "COMPOSITE_DISCOVERY_EARLY_EXIT", "COMPOSITE_FANOUT_CONCURRENCY",
    "COMPOSITE_HOST_DISCOVERY_ENABLED", "COMPOSITE_PRIOR_SCOPE_BY_DB_ENABLED",
    "COMPOSITE_PRIOR_SCOPE_LATEST_ONLY", "COMPOSITE_PRIOR_TARGETS_ENABLED",
    "COMPOSITE_SCOPE_POSTCHECK_ENABLED", "COMPOSITE_SEQUENTIAL_GATE_ENABLED",
    "CROSS_SYSTEM_PROBE_ENABLED", "QUERY_INTENT_LLM_ASSIST", "ROUTER_TWO_STAGE_ENABLED",
    "SCHEMA_CACHE_ADMIN_LLM_CONCURRENCY", "SCHEMA_CACHE_BACKEND", "SCHEMA_CACHE_ENABLED",
    "TEXT2SQL_CANDIDATE_COUNT", "TEXT2SQL_CANDIDATE_STRATEGIES", "TEXT2SQL_COMPLEXITY_GATE",
    "TEXT2SQL_MULTI_CANDIDATE", "TEXT2SQL_PROMPT_KNOWLEDGE_RENDER",
    "TEXT2SQL_QUERY_HISTORY_FEWSHOT", "TEXT2SQL_QUERY_HISTORY_TOP_K", "TEXT2SQL_SELECTION",
    "TEXT2SQL_SEMANTIC_COMPOSE", "TEXT2SQL_SEMANTIC_FALLBACK", "ZONE_GROUP_EXCLUSIVE",
}


def test_현_작업_트리_실측() -> None:
    """118 B-3 검증 기준 — 소비처 0 축 제외 · 1단 전용 축 표시 · 나머지 판정 불변."""
    found, decisions = axes.select_axes()
    primary = {a.env_key for a in found if a.tier == "primary" and not a.structural}
    by_key = {a.env_key: a for a in found}

    assert primary == _PRIMARY_BEFORE_B3 - {"COMPOSITE_TARGET_COLUMN_LLM_ENABLED"}
    assert set(axes.consumer_exclusions()) == {"COMPOSITE_TARGET_COLUMN_LLM_ENABLED"}
    assert by_key["COMPOSITE_PRIOR_SCOPE_LATEST_ONLY"].tier_only == TIER1
    assert by_key["COMPOSITE_PRIOR_SCOPE_LATEST_ONLY"].consumers == (
        "src/orchestration/deepagents_tools.py",)
    # 필드명 상수로 읽는 축 · 2단이 import 하는 3단 모듈 함수로 읽는 축은 남는다.
    assert by_key["TEXT2SQL_PROMPT_KNOWLEDGE_RENDER"].tier_only is None
    assert by_key["ROUTER_TWO_STAGE_ENABLED"].tier_only is None
    assert [k for k, (t, _) in axes.tier_exclusive_axes().items() if k in primary] == [
        "COMPOSITE_PRIOR_SCOPE_LATEST_ONLY"]


def _snap(base_nodes, arm_nodes, *, tier_only=None):
    base = sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                           effective={"k": 1}, reachable=base_nodes)
    arm = sweep.ArmConfig(arm_id="S2-P-false", axis="P", level="false", injected={"P": "false"},
                          effective={"k": 2}, reachable=arm_nodes)
    return sweep.ConfigSnapshot(
        baseline=base, arms={"S2-P-false": arm},
        tier_only=tier_only if tier_only is not None
        else {"P": (TIER1, ("src/orchestration/deepagents_tools.py",))})


def test_단_전용_축은_기준선_단이_아니면_도달_불가다() -> None:
    reasons = _snap(TIER2_NODES, TIER2_NODES).unreachable_arms()
    assert list(reasons) == ["S2-P-false"]
    assert "`deep_agent` 단 전용 모듈" in reasons["S2-P-false"]
    assert "`intent_orchestration`" in reasons["S2-P-false"]


@pytest.mark.parametrize("base_nodes, arm_nodes", [
    (TIER1_NODES, TIER1_NODES),     # 기준선이 그 단 — 잰다
    (TIER2_NODES, None),            # arm 단을 모른다 — 판정하지 않는다
])
def test_단_전용_축_판정_예외(base_nodes, arm_nodes) -> None:
    assert _snap(base_nodes, arm_nodes).unreachable_arms() == {}


def test_단_전용_표가_비면_판정하지_않는다() -> None:
    assert _snap(TIER2_NODES, TIER2_NODES, tier_only={}).unreachable_arms() == {}


def test_스냅샷이_단_전용_축의_그래프를_뜨고_파일로_되읽힌다(tmp_path, monkeypatch) -> None:
    from types import SimpleNamespace

    monkeypatch.setattr(sweep, "isolation_env", lambda: {})
    graphs: list = []

    def echo(overrides=None, base_env=None):
        return SimpleNamespace(ok=True, config={"k": json.dumps(overrides or {})})

    def graph(overrides, base_env=None):
        graphs.append(overrides)
        return SimpleNamespace(ok=True, reachable=TIER2_NODES)

    arms = [sweep.ArmSpec("baseline", None, None),
            sweep.ArmSpec("S2-P-false", "P", "false", {"P": "false"})]
    snap = sweep.capture_config_snapshot(
        arms, echo=echo, graph=graph, detect_nd=lambda base_env=None: frozenset(),
        tier_only={"P": (TIER1, ("src/orchestration/deepagents_tools.py",))})

    assert len(graphs) == 2, "arm·기준선 둘 다 그래프를 뜬다(단을 알아야 판정한다)"
    assert "S2-P-false" in snap.unreachable_arms()
    path = tmp_path / "config_snapshot.json"
    path.write_text(json.dumps(snap.as_dict(), ensure_ascii=False), encoding="utf-8")
    loaded = sweep.load_config_snapshot(path)
    assert loaded is not None and loaded.unreachable_arms() == snap.unreachable_arms()
    assert snap.subset(["S2-P-false"]).tier_only == snap.tier_only


def test_소비처_없는_축은_계획_표와_합산_판정표에_사유와_남는다(seg, monkeypatch, capsys) -> None:
    from scripts.bench import __main__ as cli

    run, _, _, root = seg
    why = "소비처 없음 — `composite.ghost` 을 읽는 코드가 0건이다"
    monkeypatch.setattr(sweep, "excluded_axes", lambda tier="primary": {"Z_GHOST": why})

    assert run("next", "--mode", "dry", "--campaign", "mock-closed") == 0
    out = capsys.readouterr().out
    assert "(구간 불가)" in out and f"Z_GHOST — {why}" in out

    assert run("next", "--mode", "mock") == 0
    args = cli.build_parser().parse_args(["--segment", "next", "--mode", "mock"])
    _, _, campaign, all_arms, categories, plan, _ = cli._campaign_context(args)
    body = cli._write_campaign_report(campaign, plan, categories, all_arms).read_text(
        encoding="utf-8")
    assert f"**미측정({why})**" in body and "측정된 축 1/4" in body


# ── B-4 노이즈 바닥 · 구간 간 교란은 같은 지문끼리 ───────────────────────────


def _raw_rows(arm: str, walls: list[int], passed: list[bool]) -> list[dict]:
    return [{"profile": arm, "scenario_id": f"S{i}", "turn": 0, "repeat": 0,
             "func_verdict": "pass" if ok else "fail", "response_mode": "answer",
             "executed_sql": "SELECT 1", "node_path": ["q"], "wall_ms": w}
            for i, (w, ok) in enumerate(zip(walls, passed))]


def _done_segment(tmp_path: Path, sid: str, axis: str, *, timeout: int, fp: str,
                  walls: list[int], passed: list[bool], finished: str) -> cm.SegmentRecord:
    out = tmp_path / sid
    out.mkdir()
    rows = _raw_rows("baseline", walls, passed) + _raw_rows(f"S2-{axis}-1", walls, passed)
    (out / "raw.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    base = sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                           effective={"server.query_timeout": timeout, "run.nonce": sid})
    snap = sweep.ConfigSnapshot(baseline=base, nondeterministic=frozenset({"run.nonce"}))
    (out / "config_snapshot.json").write_text(json.dumps(snap.as_dict()), encoding="utf-8")
    return cm.SegmentRecord(segment_id=sid, category=sid.rpartition("-")[0], axes=[axis],
                            arm_ids=[f"S2-{axis}-1"], status=cm.DONE, mode="run",
                            run_id=sid, out_dir=str(out), finished_at=finished,
                            config_fingerprint=fp, turns=len(rows), elapsed_sec=100.0)


def _campaign_report(tmp_path, monkeypatch, records) -> str:
    from scripts.bench import __main__ as cli

    monkeypatch.setattr(cli, "_sweep_proposals", lambda *a, **kw: {})
    camp = cm.Campaign(name="run-closed", path=tmp_path / "c" / "campaign.json", env="closed",
                       mode="run")
    for record in records:
        camp.records[record.segment_id] = record
    arms = [sweep.ArmSpec("baseline", None, None)] + [
        sweep.ArmSpec(f"S2-{r.axes[0]}-1", r.axes[0], "1", {r.axes[0]: "1"}) for r in records]
    plan = cm.plan_segments([], {}, rate=cm.RateModel(sec_per_turn=1.0, turns_per_arm=1,
                                                         source="t"))
    return cli._write_campaign_report(camp, plan, {}, arms).read_text(encoding="utf-8")


def test_지문이_다른_두_구간은_바닥_미측정이고_교란_대신_바뀐_키를_적는다(
        tmp_path, monkeypatch) -> None:
    """★ run-closed 형태 — general-1 `query_timeout=60` · composite-5 `=180`.

    종전 리포트는 이 둘을 섞어 「노이즈 바닥 14.6%p」·「구간 간 시간 교란 −23.7초」를 냈다.
    """
    n = 40
    general = _done_segment(tmp_path, "general-1", "A", timeout=60, fp="fp60", finished="1",
                            walls=[60_000] * n, passed=[i % 2 == 0 for i in range(n)])
    composite = _done_segment(tmp_path, "composite-5", "B", timeout=180, fp="fp180",
                              finished="2", walls=[80_000 + i for i in range(n)],
                              passed=[i % 5 != 0 for i in range(n)])
    body = _campaign_report(tmp_path, monkeypatch, [general, composite])

    assert "**노이즈 바닥 미측정**" in body
    assert "구간 간 시간 교란이 크다" not in body
    assert "`general-1`↔`composite-5` — 설정이 달라 비교하지 않는다" in body
    assert "`server.query_timeout` 60 → 180" in body
    assert "run.nonce" not in body, "비결정 키는 바뀐 키로 세지 않는다"


def test_지문이_같은_구간끼리는_바닥을_잰다(tmp_path, monkeypatch) -> None:
    n = 40
    first = _done_segment(tmp_path, "a-1", "A", timeout=180, fp="same", finished="1",
                          walls=[60_000] * n, passed=[True] * n)
    second = _done_segment(tmp_path, "b-1", "B", timeout=180, fp="same", finished="2",
                           walls=[60_000] * n, passed=[i % 4 != 0 for i in range(n)])
    other = _done_segment(tmp_path, "c-1", "C", timeout=60, fp="diff", finished="3",
                          walls=[60_000] * n, passed=[False] * n)
    body = _campaign_report(tmp_path, monkeypatch, [first, second, other])

    assert "같은 설정 지문끼리만** 잰 정확도 노이즈 바닥 **25.0%p**" in body, \
        "지문이 다른 c-1(0%)을 섞으면 100%p 가 된다"
    assert "지문 `same` 2회(`a-1`, `b-1`)" in body
    assert "`b-1`↔`c-1` — 설정이 달라 비교하지 않는다" in body


def test_지문이_없는_구간은_같다고_치지_않는다() -> None:
    reps = [cm.BaselineRepeat("a", [], None), cm.BaselineRepeat("b", [], None),
            cm.BaselineRepeat("c", [], "x"), cm.BaselineRepeat("d", [], "x")]
    assert [[r.segment_id for r in g] for g in cm.fingerprint_groups(reps)] == [["c", "d"]]
    assert cm.config_diff(reps[0], reps[1]).startswith("확인 불가")


# ── B-5 기준 경로 단 = 정본 ──────────────────────────────────────────────────


def test_기준_경로_단은_ladder_정본을_읽는다(monkeypatch) -> None:
    from src.observability.ladder import LadderTier

    assert sweep.canonical_tier() == TIER2, "D-251 — 2단이 기준 경로다"
    monkeypatch.setattr(LadderTier, "is_canonical",
                        property(lambda self: self is LadderTier.SEMANTIC_ROUTER))
    assert sweep.canonical_tier() == TIER3, "단 이름을 사본으로 박지 않는다"


def test_합산_리포트는_3단_기준선_구간을_기준_경로_아님으로_고지한다(tmp_path) -> None:
    from scripts.bench import __main__ as cli

    camp = cm.Campaign(name="t", path=tmp_path / "c.json", env="closed", mode="run")
    for sid, tier, fin in (("a-1", TIER2, "1"), ("b-1", TIER3, "2")):
        camp.records[sid] = cm.SegmentRecord(segment_id=sid, category=sid[0], axes=[sid],
                                             arm_ids=[], status=cm.DONE, mode="run",
                                             finished_at=fin, baseline_tier=tier)
    text = "\n".join(cli._campaign_tier_lines(camp))
    assert f"`b-1`={TIER3}" in text and f"`a-1`={TIER2}" not in text
    assert f"기준 경로 `{TIER2}`(D-251)" in text


# ── B-6 지연 검정력 고지 ─────────────────────────────────────────────────────


def _obs(arm: str, sid: str, wall: float, *, timeout: bool = False) -> sweep.Observation:
    return sweep.Observation(arm_id=arm, scenario_id=sid, repeat=0, passed=True, wall_ms=wall,
                             llm_calls=None, tokens=None, retries=None,
                             unevaluated="timeout" if timeout else None)


def test_둘_다_완주한_쌍만_표준편차에_넣는다() -> None:
    base = [_obs("b", f"S{i}", 1000.0) for i in range(4)] + [_obs("b", "T", 60_000, timeout=True)]
    var = [_obs("v", f"S{i}", 1000.0 + d) for i, d in enumerate((0, 100, 200, 300))] + [
        _obs("v", "T", 90_000)]
    n, sd = compare.finished_latency_spread(base, var)
    assert n == 4 and sd == pytest.approx(129.1, abs=0.1), "타임아웃 쌍(검열값)은 뺀다"
    assert compare.finished_latency_spread(base[:1], var[:1]) is None


def test_효과가_표준편차보다_작으면_지연_신호_검정력_부족() -> None:
    """★ composite-5 형태 — 효과 +10.9초 · 둘 다 완주 쌍 표준편차 38.7초."""
    import random

    rng = random.Random(118)
    deltas = [rng.gauss(10_900, 38_700) for _ in range(57)]
    base = [_obs("baseline", f"S{i}", 70_000.0) for i in range(57)]
    var = [_obs("S2-X-true", f"S{i}", 70_000.0 + d) for i, d in enumerate(deltas)]
    verdict = compare.judge("S2-X-true", "X", "true", base, var)
    assert "둘 다 완주 57쌍 지연 차 표준편차" in verdict.sentence
    assert "지연 신호 검정력 부족" in verdict.sentence


def test_효과가_표준편차보다_크면_검정력_부족을_붙이지_않는다() -> None:
    base = [_obs("baseline", f"S{i}", 70_000.0) for i in range(20)]
    var = [_obs("S2-X-true", f"S{i}", 90_000.0 + (i % 3) * 100) for i in range(20)]
    verdict = compare.judge("S2-X-true", "X", "true", base, var)
    assert "지연 차 표준편차" in verdict.sentence
    assert "검정력 부족" not in verdict.sentence
