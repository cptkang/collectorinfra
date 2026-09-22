"""plans/114 M-2·M-3 — 벤치 관문 확장(단 일관성 · 타임아웃률 · 무효 판정표)과 축 도달성 사전 판정.

근거 run `20260922-112010`(폐쇄망 `general-1` · 보정 구간): 4 프로파일 전부 2단
(`intent_orchestration`), 타임아웃 95/223턴(42.6%), 전 arm 판정 불가인데 불일치 쌍 17건
(A/A 노이즈)이라 구간이 「완료」가 됐다. 측정 축 `CROSS_SYSTEM_PROBE_ENABLED` 는 소비 노드
`entity_locator` 가 3단에서만 등록돼 true arm 이 사실상 기준선의 A/A 반복이었다.

실 LLM·DB·네트워크 0 — 그래프 빌드는 모의 설정(`mock_config`)과 소켓 차단 자식으로만 한다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

import src.graph as graph_module
from scripts.bench import campaign as cm
from scripts.bench import probe, sweep
from tests.test_scripts import test_bench_campaign as campaign_tests
from tests.test_scripts import test_bench_sweep as sweep_tests
from tests.test_scripts.test_bench_campaign import _arms, _health, _rate, _state
from tests.test_scripts.test_bench_sweep import _sweep_args, _sweep_row, _write_raw

#: 캠페인 흐름(`seg`)·게이트 뒤 실행 가로채기(`gate`) 픽스처를 그대로 쓴다.
seg = campaign_tests.seg
gate = sweep_tests.gate

TIER2 = "intent_orchestration"
TIER3 = "semantic_router"
PROBE = "CROSS_SYSTEM_PROBE_ENABLED"


# ── M-2 ①② — 단 일관성 · 타임아웃률 ─────────────────────────────────────────


def _run_shape(tmp_path: Path, *, tier: str = TIER2, turns: int = 223, timeouts: int = 95,
               tiers: dict[str, str] | None = None) -> sweep.RunHealth:
    """이번 run 형태 — 프로파일 4개(arm 2 × 시나리오 자기 프로파일 2) · 타임아웃 `timeouts`턴."""
    names = {"baseline": "baseline",
             f"baseline+S2-{PROBE}-true": f"S2-{PROBE}-true",
             "optin_alarm+baseline": "baseline",
             f"optin_alarm+S2-{PROBE}-true": f"S2-{PROBE}-true"}
    profiles = [{"name": name, "arm": arm, "valid": True,
                 "tier": (tiers or {}).get(name, tier)} for name, arm in names.items()]
    rows = []
    for i in range(turns):
        name = list(names)[i % 4]
        timeout = i < timeouts
        rows.append({"profile": name, "arm": names[name], "scenario_id": f"S{i // 4}",
                     "turn": 1, "repeat": 0,
                     "func_verdict": "fail" if timeout else "pass",
                     "response_mode": "error" if timeout else "answer",
                     "error": "처리 시간이 초과되었습니다" if timeout else None,
                     "unevaluated_reason": "timeout" if timeout else None,
                     "executed_sqls": [{"sql": "SELECT 1 FROM t"}], "node_path": ["input_parser"]})
    raw = tmp_path / "raw.jsonl"
    raw.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return sweep.scan_health({"profiles": profiles}, raw)


def test_이번_run_형태는_단과_타임아웃으로_구간_실패다(tmp_path) -> None:
    health = _run_shape(tmp_path)

    assert health.timeout_turns == 95 and round(health.timeout_rate, 3) == 0.426
    reasons = health.stop_reasons()
    # 구간 실패는 **타임아웃률**이 낸다. 기준선이 2단인 것은 실패가 아니라 고지다(D-250 ③) —
    # 실패로 세면 단 축(M-0)에서 2단이 이겼을 때 남은 구간이 전부 실패한다.
    timeout = [r for r in reasons if r.startswith("타임아웃")]
    assert timeout and "43% ≥ 20%" in timeout[0] and "(95/223턴)" in timeout[0]
    assert "축 비교 표본이 줄었다" in timeout[0]
    assert [r for r in reasons if "기준선 단" in r] == []

    tier = [n for n in health.warnings() if "기준선 단" in n]
    assert len(tier) == 1
    assert "`intent_orchestration`" in tier[0] and "`semantic_router`" in tier[0]
    assert "사다리 단 축(plans/114 M-0 · D-250)을 재기 전에는 다른 축 결과가 이 단에 조건부다" \
        in tier[0]
    # 실패 사유는 고지에도 실린다(둘이 갈리면 화면은 주의인데 구간은 완료가 된다)
    assert set(health.qualification_problems()) <= set(health.warnings())


def test_기준_경로_단이고_타임아웃이_적으면_자격_문제가_없다(tmp_path) -> None:
    health = _run_shape(tmp_path, tier=TIER3, timeouts=20)          # 9%

    assert health.qualification_problems() == []
    assert health.stop_reasons() == []


def test_타임아웃_임계는_20퍼센트다(tmp_path) -> None:
    assert sweep.TIMEOUT_STOP == 0.2
    assert _run_shape(tmp_path, tier=TIER3, turns=100, timeouts=19).timeout_problem() is None
    assert _run_shape(tmp_path, tier=TIER3, turns=100, timeouts=20).timeout_problem()


def test_프로파일마다_단이_갈리면_주입_강등_사고다(tmp_path) -> None:
    health = _run_shape(tmp_path, tier=TIER3, timeouts=0,
                        tiers={f"optin_alarm+S2-{PROBE}-true": "legacy"})

    problems = health.tier_problems()
    assert any("프로파일마다 다르다" in p and "legacy" in p for p in problems)


def test_모의_단과_미관측은_세지_않는다(tmp_path) -> None:
    """미관측을 강등으로 세면 주의가 상시 켜진다 — mock 리허설이 항상 멈춘다."""
    assert _run_shape(tmp_path, tier="mock", timeouts=0).tier_problems() == []
    assert sweep.RunHealth(valid_profiles=1, invalid_profiles=[], turns=10,
                           verdicts={"pass": 10}, evidence=[]).tier_problems() == []


def test_무효_프로파일의_단은_보지_않는다(tmp_path) -> None:
    raw = tmp_path / "raw.jsonl"
    raw.write_text(json.dumps({"profile": "baseline", "scenario_id": "S", "turn": 1,
                               "func_verdict": "pass", "executed_sql": "SELECT 1",
                               "node_path": ["x"]}), encoding="utf-8")
    health = sweep.scan_health({"profiles": [
        {"name": "baseline", "valid": True, "tier": TIER3},
        {"name": "S2-X-1", "valid": False, "tier": TIER2, "reasons": ["에코 불일치"]}]}, raw)
    assert health.tier_problems() == []


# ── M-2 ③ — 후단 관문: 판정표 무효 + 자격 문제면 불일치 쌍과 무관하게 실패 ──────────────

_TIER2_HEALTH = dict(tiers=(("baseline", "baseline", TIER2), ("S2-C-1", "S2-C-1", TIER2)))


def test_무효_판정표라도_2단_기준선만으로는_실패가_아니다(seg, capsys) -> None:
    """기준선이 기준 경로가 아닌 것은 고지다(D-250 ③) — 실패로 세면 단 축(M-0)에서 2단이
    이겼을 때 남은 구간이 전부 실패한다. 단은 벤치가 재는 값이지 관문이 고르는 값이 아니다."""
    run, _, outcomes, root = seg
    outcomes.append((_health(**_TIER2_HEALTH), {"all_unjudged": True, "discordant": 17}))

    assert run("next", "--mode", "run") == 0
    rec = _state(root, "run-closed")["records"][0]
    assert rec["status"] == cm.DONE
    assert not any("기준선 단" in r for r in rec["stop_reasons"])
    assert "검정력 부족" in capsys.readouterr().out


def test_무효_판정표에_단이_갈리면_실패다(seg) -> None:
    """프로파일마다 단이 다른 것은 주입·강등 사고라 종전대로 실패다(M-2 ①)."""
    run, _, outcomes, root = seg
    outcomes.append((_health(tiers=(("baseline", "baseline", TIER3),
                                    ("S2-C-1", "S2-C-1", TIER2))),
                     {"all_unjudged": True, "discordant": 17}))

    assert run("next", "--mode", "run") == 1
    rec = _state(root, "run-closed")["records"][0]
    assert rec["status"] == cm.FAILED
    assert any("프로파일마다 다르다" in r for r in rec["stop_reasons"])
    post = [r for r in rec["stop_reasons"] if "판정표 무효" in r]
    assert post and "불일치 쌍 합 17건" in post[0] and "사다리 단" in post[0]


def test_무효_판정표에_타임아웃률만_겹쳐도_실패다(seg) -> None:
    run, _, outcomes, root = seg
    outcomes.append((_health(unevaluated={"timeout": 85}),       # 200턴 중 42.5%
                     {"all_unjudged": True, "discordant": 17}))

    assert run("next", "--mode", "run") == 1
    reasons = _state(root, "run-closed")["records"][0]["stop_reasons"]
    assert any(r.startswith("타임아웃") for r in reasons)
    assert any("판정표 무효" in r and "타임아웃률" in r for r in reasons)


def test_무효_판정표에_도달_불가_arm_이_있으면_실패다(seg) -> None:
    """도달 불가(M-3)는 단독으로는 멈춤 사유가 아니다 — 판정표가 무효일 때만 겹친 원인이 된다."""
    run, _, outcomes, root = seg
    outcomes.append((_health(), {"all_unjudged": True, "discordant": 17,
                                 "unreachable": ["S2-C-1"]}))

    assert run("next", "--mode", "run") == 1
    reasons = _state(root, "run-closed")["records"][0]["stop_reasons"]
    assert reasons and all("판정표 무효" in r for r in reasons)
    assert "도달 불가 arm" in reasons[0]


def test_자격_문제가_없으면_종전대로_완료_검정력_부족이다(seg, capsys) -> None:
    run, _, outcomes, root = seg
    outcomes.append((_health(), {"all_unjudged": True, "discordant": 17}))

    assert run("next", "--mode", "run") == 0
    assert _state(root, "run-closed")["records"][0]["status"] == cm.DONE
    assert "검정력 부족" in capsys.readouterr().out


# ── M-2 ①b — 구간 간 판(단·설정 지문·커밋) 기록과 비교 ─────────────────────────


def _record(segment_id: str, **over) -> cm.SegmentRecord:
    base = dict(segment_id=segment_id, category="x", axes=["A"], arm_ids=["S2-A-1"],
                status=cm.DONE, mode="run", finished_at=f"2026-09-23T0{segment_id[-1]}:00:00",
                baseline_tier=TIER2, config_fingerprint="abc12345", commit="deadbeefcafe",
                dirty=False)
    base.update(over)
    return cm.SegmentRecord(**base)


def test_첫_구간은_비교할_앞_구간이_없다() -> None:
    assert cm.continuity(None, _record("x-1")) == ([], [])


def test_같은_판이면_사유도_고지도_없다() -> None:
    assert cm.continuity(_record("x-1"), _record("x-2")) == ([], [])


def test_구간_사이에_단이_바뀌면_멈춘다() -> None:
    stop, notes = cm.continuity(_record("x-1"), _record("x-2", baseline_tier=TIER3))
    assert stop and "`x-1`" in stop[0] and TIER2 in stop[0] and TIER3 in stop[0]
    assert "노이즈 바닥" in stop[0] and notes == []


def test_설정_지문과_커밋_차이는_고지다() -> None:
    stop, notes = cm.continuity(_record("x-1"),
                                _record("x-2", config_fingerprint="zzz99999",
                                        commit="0123456789ab"))
    assert stop == []
    assert any("설정 지문" in n for n in notes) and any("판이 바뀌었다" in n for n in notes)


def test_옛_기록은_칸이_없어_비교하지_않는다() -> None:
    """칸 없는 구간(이 기능 이전 run)을 「바뀌었다」로 세지 않는다 — 없음과 안 잼은 다르다."""
    old = _record("x-1", baseline_tier=None, config_fingerprint=None, commit=None, dirty=None)
    assert cm.continuity(old, _record("x-2")) == ([], [])


def test_구간_기록에_단_지문_커밋이_남는다(seg) -> None:
    run, _, outcomes, root = seg
    outcomes.append((_health(**_TIER2_HEALTH),
                     {"baseline_tier": TIER2, "config_fingerprint": "abc12345",
                      "commit": "deadbeefcafe", "dirty": True}))

    assert run("next", "--mode", "run") == 0
    record = _state(root, "run-closed")["records"][0]
    assert record["baseline_tier"] == TIER2
    assert record["config_fingerprint"] == "abc12345"
    assert record["commit"] == "deadbeefcafe" and record["dirty"] is True

    report = (root / "run-closed" / "campaign_verdicts.md").read_text(encoding="utf-8")
    assert "사다리 단 축(plans/114 M-0 · D-250) 미측정" in report
    assert "deadbeef (dirty)" in report and "abc12345" in report


def test_다음_구간에서_단이_바뀌면_그_구간이_실패한다(seg) -> None:
    run, _, outcomes, root = seg
    common = {"config_fingerprint": "abc12345", "commit": "deadbeefcafe"}
    outcomes.append((_health(**_TIER2_HEALTH), {"baseline_tier": TIER2, **common}))
    outcomes.append((_health(), {"baseline_tier": TIER3, **common}))

    assert run("next", "--mode", "run") == 0
    assert run("next", "--mode", "run") == 1
    records = {r["segment_id"]: r for r in _state(root, "run-closed")["records"]}
    failed = [r for r in records.values() if r["status"] == cm.FAILED]
    assert len(failed) == 1
    assert any("기준선 단이" in reason and TIER3 in reason for reason in failed[0]["stop_reasons"])


# ── M-3 — 축 도달성 ───────────────────────────────────────────────────────────


def _cfg(mock_config, *, tier: int, probe_on: bool = False, loop: bool = False,
         approval: bool = False):
    cfg = mock_config
    cfg.enable_deepagents_package = False
    cfg.enable_intent_orchestration = tier == 2
    cfg.enable_semantic_routing = tier in (2, 3)
    cfg.cross_system_probe_enabled = probe_on
    cfg.tier3_plan_loop_enabled = loop
    cfg.enable_sql_approval = approval
    return cfg


def _reach(cfg) -> tuple[str, ...]:
    return tuple(probe.reachable_from_start(graph_module.build_graph(cfg,
                                                                     checkpointer=InMemorySaver())))


@pytest.fixture()
def semantic_backend(monkeypatch):
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")


def _snapshot(base_reach, arm_reach, *, axis: str = PROBE) -> sweep.ConfigSnapshot:
    arm_id = f"S2-{axis}-true"
    return sweep.ConfigSnapshot(
        baseline=sweep.ArmConfig(arm_id="baseline", axis=None, level=None, injected={},
                                 effective={"k": "0"}, reachable=base_reach),
        arms={arm_id: sweep.ArmConfig(arm_id=arm_id, axis=axis, level="true",
                                      injected={axis: "true"}, effective={"k": "1"},
                                      reachable=arm_reach)})


def test_2단_기준선에_소재_프로브를_얹으면_도달_불가다(mock_config, semantic_backend) -> None:
    base = _reach(_cfg(mock_config, tier=2))
    arm = _reach(_cfg(mock_config, tier=2, probe_on=True))

    assert "entity_locator" not in arm and set(base) == set(arm)
    unreachable = _snapshot(base, arm).unreachable_arms()
    assert list(unreachable) == [f"S2-{PROBE}-true"]
    reason = unreachable[f"S2-{PROBE}-true"]
    assert "entity_locator" in reason and "`intent_orchestration`" in reason


def test_3단_기준선이면_노드_집합이_달라져_잰다(mock_config, semantic_backend) -> None:
    base = _reach(_cfg(mock_config, tier=3))
    arm = _reach(_cfg(mock_config, tier=3, probe_on=True))

    assert "entity_locator" in arm and set(base) != set(arm)
    assert _snapshot(base, arm).unreachable_arms() == {}


def test_2단은_등록만_되고_배선되지_않은_승인_노드도_도달_불가다(
        mock_config, semantic_backend) -> None:
    """2단은 `approval_gate` 를 등록하지만 START 에서 닿지 않는다(에이전트 내부 파이프라인)."""
    base = _reach(_cfg(mock_config, tier=2))
    arm = _reach(_cfg(mock_config, tier=2, approval=True))

    assert "approval_gate" in graph_module.build_graph(
        _cfg(mock_config, tier=2, approval=True), checkpointer=InMemorySaver()).get_graph().nodes
    assert _snapshot(base, arm, axis="ENABLE_SQL_APPROVAL").unreachable_arms()


def test_등록_플래그가_아닌_축은_판정하지_않는다() -> None:
    """노드 안에서 읽는 노브는 이 방법으로 판정하지 않는다(한계) — 종전대로 잰다."""
    same = ("context_resolver", "input_parser")
    assert _snapshot(same, same, axis="QUERY_MAX_RETRY_COUNT").unreachable_arms() == {}


def test_도달성을_못_뜬_쪽이_있으면_추정하지_않는다() -> None:
    same = ("context_resolver",)
    assert _snapshot(None, same).unreachable_arms() == {}
    assert _snapshot(same, None).unreachable_arms() == {}


def test_3단_전_기능_빌드는_등록_노드가_전부_닿는다(mock_config, semantic_backend) -> None:
    """도달 판정의 전제 — 조건부 엣지가 경로 맵 없이 추가되면 LangGraph 가 `__end__` 로만 그려
    뒤 노드를 못 닿는 것으로 센다. 그러면 이 테스트가 먼저 깨진다."""
    cfg = _cfg(mock_config, tier=3, probe_on=True, loop=True, approval=True)
    compiled = graph_module.build_graph(cfg, checkpointer=InMemorySaver())
    registered = {n for n in compiled.get_graph().nodes if not n.startswith("__")}
    assert set(probe.reachable_from_start(compiled)) == registered


def test_등록_플래그표의_노드는_실제로_그_플래그가_등록한다(mock_config, semantic_backend) -> None:
    """표가 낡으면(노드 개명·조건 이동) 도달 불가 판정이 엉뚱한 노드를 본다."""
    flags = {"CROSS_SYSTEM_PROBE_ENABLED": "cross_system_probe_enabled",
             "TIER3_PLAN_LOOP_ENABLED": "tier3_plan_loop_enabled",
             "ENABLE_SQL_APPROVAL": "enable_sql_approval"}
    for env_key, attr in flags.items():
        on = _cfg(mock_config, tier=3)
        setattr(on, attr, True)
        nodes_on = set(graph_module.build_graph(on, checkpointer=InMemorySaver()).get_graph().nodes)
        off = _cfg(mock_config, tier=3)
        setattr(off, attr, False)
        nodes_off = set(
            graph_module.build_graph(off, checkpointer=InMemorySaver()).get_graph().nodes)
        assert set(sweep.GRAPH_REGISTRATION_FLAGS[env_key]) == nodes_on - nodes_off, env_key
    cfg = _cfg(mock_config, tier=3)     # 같은 객체를 고쳐 쓰므로 빌드를 먼저 한다
    cfg.composite.sequential_fallback_tiers_enabled = True
    seq_on = set(graph_module.build_graph(cfg, checkpointer=InMemorySaver()).get_graph().nodes)
    cfg.composite.sequential_fallback_tiers_enabled = False
    seq_off = set(graph_module.build_graph(cfg, checkpointer=InMemorySaver()).get_graph().nodes)
    assert set(sweep.GRAPH_REGISTRATION_FLAGS["COMPOSITE_SEQUENTIAL_FALLBACK_TIERS_ENABLED"]) \
        == seq_on - seq_off


def test_그래프는_등록_플래그_축이_있을_때만_뜬다() -> None:
    calls: list = []

    def echo(overrides=None, *, base_env=None):
        return probe.EchoResult(ok=True, config={"k": str(sorted((overrides or {}).items()))})

    def graph(overrides=None, *, base_env=None):
        calls.append(dict(overrides or {}))
        return probe.GraphReach(ok=True, reachable=("context_resolver",))

    plain = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None),
             sweep.ArmSpec(arm_id="S2-K-1", axis="K", level="1", env={"K": "1"})]
    sweep.capture_config_snapshot(plain, echo=echo, graph=graph, detect_nd=lambda **kw: frozenset())
    assert calls == [], "등록 플래그 축이 없으면 자식을 띄우지 않는다"

    armed = plain + [sweep.ArmSpec(arm_id=f"S2-{PROBE}-true", axis=PROBE, level="true",
                                   env={PROBE: "true"})]
    snap = sweep.capture_config_snapshot(armed, echo=echo, graph=graph,
                                         detect_nd=lambda **kw: frozenset())
    assert calls == [{PROBE: "true"}, {}], "그 arm 과 기준선만 뜬다"
    assert snap.arms["S2-K-1"].reachable is None
    assert list(snap.unreachable_arms()) == [f"S2-{PROBE}-true"]


def test_그래프_실패는_데이터다() -> None:
    def echo(overrides=None, *, base_env=None):
        return probe.EchoResult(ok=True, config={})

    def graph(overrides=None, *, base_env=None):
        return probe.GraphReach(ok=False, error="그래프 빌드가 네트워크 연결을 시도했다")

    arms = [sweep.ArmSpec(arm_id=f"S2-{PROBE}-true", axis=PROBE, level="true", env={PROBE: "true"})]
    snap = sweep.capture_config_snapshot(arms, echo=echo, graph=graph,
                                         detect_nd=lambda **kw: frozenset())
    assert snap.baseline.graph_error and "네트워크" in snap.baseline.graph_error
    assert snap.unreachable_arms() == {}


def test_스냅샷_파일에_도달_노드가_남고_되읽힌다(tmp_path) -> None:
    snap = _snapshot(("a", "b"), ("a", "b"))
    path = sweep.write_config_snapshot(snap, tmp_path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["baseline"]["reachable"] == ["a", "b"]
    assert f"S2-{PROBE}-true" in body["unreachable_arms"]

    again = sweep.load_config_snapshot(path)
    assert again is not None and again.unreachable_arms() == snap.unreachable_arms()


def test_캠페인은_도달_불가_축을_돌리지_않고_사유를_남긴다() -> None:
    arms = _arms({PROBE: 2, "B": 2})
    reason = "도달 불가 — 테스트"
    plan = cm.plan_segments(arms, {PROBE: "x", "B": "x"}, rate=_rate(1.0), max_hours=10,
                            controls=frozenset({f"S2-{PROBE}-0", f"S2-{PROBE}-1", "S2-B-0"}),
                            reasons={f"S2-{PROBE}-1": reason})
    assert (PROBE, reason) in plan.unplaceable
    assert all(PROBE not in seg.axes for seg in plan.segments)
    campaign = cm.Campaign(path=Path("/nonexistent/campaign.json"), name="t", env="closed",
                           mode="run", repeat=1, max_hours=10)
    assert cm.unmeasured_reason(PROBE, campaign, plan) == f"미측정({reason})"


def test_전_레벨_AA_축의_구간_불가_사유는_종전_문구다() -> None:
    arms = _arms({"A": 2})
    plan = cm.plan_segments(arms, {"A": "x"}, rate=_rate(1.0), max_hours=10,
                            controls=frozenset({"S2-A-0", "S2-A-1"}))
    assert plan.unplaceable == (
        ("A", "모든 레벨의 실효 설정이 기준선과 같다 — 비교할 레벨이 없다"),)


# ── 실 자식 1회 — 소켓 차단 자식이 2단·3단을 구별한다 ───────────────────────────


def test_자식_프로브가_2단과_3단의_도달_노드를_구별한다() -> None:
    """`probe.graph_reach` 의 실 자식(소켓 차단).

    사다리·LLM 설정은 명시해 `.env` 에 흔들리지 않게 한다.
    """
    pinned = {"ENABLE_DEEPAGENTS_PACKAGE": "false", "ENABLE_SEMANTIC_ROUTING": "true",
              "LLM_PROVIDER": "ollama", PROBE: "true", "TIER3_PLAN_LOOP_ENABLED": "false"}
    tier2 = probe.graph_reach({**pinned, "ENABLE_INTENT_ORCHESTRATION": "true"})
    tier3 = probe.graph_reach({**pinned, "ENABLE_INTENT_ORCHESTRATION": "false"})

    assert tier2.ok, tier2.error
    assert tier3.ok, tier3.error
    assert sweep.ladder_tier_of(tier2.reachable) == TIER2
    assert sweep.ladder_tier_of(tier3.reachable) == TIER3
    assert "entity_locator" not in tier2.reachable and "entity_locator" in tier3.reachable


# ── run_sweep 배선 — M-7 ① 카탈로그 전달 · M-3 판정표 표기 ──────────────────────

def _sweep_fixture(cli, monkeypatch, tmp_path, snap):
    monkeypatch.setattr(sweep, "server_auth_enabled", lambda: False)
    rows = []
    for i in range(20):
        rows.append(_sweep_row("baseline", f"S-{i}", func_verdict="pass", response_mode="answer",
                               executed_sql="SELECT 1", node_path=["q"], wall_ms=100))
    _write_raw(tmp_path, rows)
    monkeypatch.setattr(sweep, "run_arms", lambda arms, **kw: {
        "out_dir": str(tmp_path),
        "profiles": [{"name": a.arm_id, "valid": True, "reasons": []} for a in arms]})
    captured: list = []
    import scripts.scenario.report as sc_report
    monkeypatch.setattr(sc_report, "write_report",
                        lambda out_dir, catalog=None: captured.append(catalog) or {})
    executed = [sweep.ArmSpec(arm_id="baseline", axis=None, level=None)]
    skipped = [sweep.ArmSpec(arm_id=f"S2-{PROBE}-true", axis=PROBE, level="true",
                             env={PROBE: "true"})]
    out = cli.run_sweep(_sweep_args(), executed, label="구간 t-1", snapshot=snap,
                        substituted=skipped)
    return out, captured


def test_스위프는_94_리포트에_카탈로그를_넘긴다(gate, monkeypatch, tmp_path) -> None:
    """없으면 3절 `목표(ms)` 가 전 칸 공란이다(run 20260922-112010 · plans/114 M-7 ①)."""
    cli, _ = gate
    two = ("agent_orchestrator", "context_resolver", "intent_planner")
    _, captured = _sweep_fixture(cli, monkeypatch, tmp_path, _snapshot(two, two))

    assert len(captured) == 1 and captured[0] is not None
    assert all(g.latency_target_ms for g in captured[0].groups.values())


def test_도달_불가로_생략한_arm은_판정표에_사유가_남는다(gate, monkeypatch, tmp_path) -> None:
    cli, _ = gate
    two = ("agent_orchestrator", "context_resolver", "intent_planner")
    out, _ = _sweep_fixture(cli, monkeypatch, tmp_path, _snapshot(two, two))

    assert out.unreachable == [f"S2-{PROBE}-true"]
    assert "레벨 `true` = 도달 불가 → 기준선 관측 사용(실행 생략)" in out.optima[0].sentence
    body = (tmp_path / "axis_verdicts.md").read_text(encoding="utf-8")
    assert f"`S2-{PROBE}-true` **(도달 불가)**" in body and "entity_locator" in body
    assert "도달 불가 arm 1개" in body
