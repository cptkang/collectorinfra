"""plans/94 §19 — `plans/107`(의도 재작성) 경계의 하네스 계약: Y-11·Y-12 · O-e · V28~V31.

하네스가 소유하는 것만 본다 — 재작성 감사의 **수집·판정·리포트**. 적재(제품)는 plans/107
테스트(`tests/test_nodes/test_plan107_*`)가 소유한다. 서버·LLM 0(D-127).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import analyze
from scripts.scenario.assertions import Observation, Verdict, evaluate_turn
from scripts.scenario.catalog import Group, Scenario, Turn, load_catalog
from scripts.scenario.client import _apply_done
from scripts.scenario.report import classify_failure
from scripts.scenario.runner import SqlAuditTail, _row

REPO_ROOT = Path(__file__).resolve().parents[2]

_PASS = {"consumer": "query_generator", "gate": {"needed": False, "reason": "pass_through"},
         "verify": {"user_query": "pass"}, "slots": {"metrics": {}}}
_NEEDED = {"needed": True, "reason": "non_utterance_source"}
_REWRITTEN = {"consumer": "query_generator", "gate": _NEEDED,
              "verify": {}, "slots": {"targets.db_ids": {}}}
_LEAK = {"consumer": "multi_db_executor", "gate": _NEEDED,
         "verify": {"original_query": "fail:location_leak"}, "slots": {"targets.db_ids": {}}}


def _eval(expect: dict, traces: list[dict]) -> Verdict:
    scenario = Scenario(id="T-01", group="T", plans=[107], title="t",
                        turns=[Turn(send={"query": "q"}, expect=expect)])
    group = Group(id="T", name="t", latency_target_ms=10000)
    obs = Observation(status="completed", executed_sql="SELECT 1", rewrite_traces=traces)
    return evaluate_turn(scenario, 1, scenario.turns[0], obs, group)


# ──────────────────────────────────────────────
# Y-11·Y-12 — 관측하지 못한 것을 판정하지 않는다
# ──────────────────────────────────────────────

class TestRewriteAssertion:
    def test_no_trace_is_manual_not_fail(self):
        """서버가 INTENT_FRAME_ENABLED 가 아니면 레코드가 없다 — 불합격이 아니라 보류."""
        verdict = _eval({"rewrite": {"gate": "pass_through"}}, [])
        assert verdict.func == "manual"
        assert any("재작성 감사 레코드가 없다" in n for n in verdict.manual_notes)

    def test_rewritten_expectation(self):
        assert _eval({"rewrite": {"gate": "rewritten"}}, [_REWRITTEN]).func == "pass"
        assert _eval({"rewrite": {"gate": "rewritten"}}, [_PASS]).func == "fail"

    def test_unknown_gate_value_fails_loudly(self):
        verdict = _eval({"rewrite": {"gate": "maybe"}}, [_PASS])
        assert verdict.func == "fail" and verdict.failures[0].key == "rewrite.gate"

    def test_slots_preserved_without_verify_is_manual(self):
        """검증 결과가 비었으면(REWRITE_VERIFY_MODE off) 통과로 세지 않는다."""
        verdict = _eval({"rewrite": {"slots_preserved": True}}, [_REWRITTEN])
        assert verdict.func == "manual"
        assert any("REWRITE_VERIFY_MODE" in n for n in verdict.manual_notes)

    def test_slots_preserved_failure_names_channel_and_reason(self):
        """실패 메시지가 채널별 사유를 싣는다(Y-3 형식 — 원인을 짚는다)."""
        verdict = _eval({"rewrite": {"slots_preserved": True}}, [_PASS, _LEAK])
        failure = verdict.failures[0]
        assert failure.key == "rewrite.slots_preserved"
        expected = {"multi_db_executor.original_query": "fail:location_leak"}
        assert failure.actual["broken"] == expected

    def test_rewrite_failure_has_its_own_class(self):
        row = {"failed_assertions": [{"key": "rewrite.slots_preserved"}],
               "executed_sql": "SELECT 1", "error": None, "row_count": 3}
        assert classify_failure(row) == "rewrite"


# ──────────────────────────────────────────────
# O-e — done 1순위 · 감사 로그 폴백 · raw.jsonl 적재
# ──────────────────────────────────────────────

class TestCollection:
    def test_done_payload_is_parsed(self):
        obs = Observation()
        _apply_done(obs, {"response": "r", "rewrite_trace": [_PASS, "쓰레기"]})
        assert obs.rewrite_traces == [_PASS]

    def test_done_without_key_leaves_empty(self):
        """기능이 꺼진 서버는 키를 싣지 않는다 — 빈 목록이지 오류가 아니다."""
        obs = Observation()
        _apply_done(obs, {"response": "r"})
        assert obs.rewrite_traces == []

    def test_audit_log_fallback_filters_thread_and_event(self, tmp_path: Path):
        log = tmp_path / "server.log"
        lines = [
            json.dumps({"event": "rewrite_trace", "thread_id": "t-1", "rewrite_trace": _PASS}),
            json.dumps({"event": "rewrite_trace", "thread_id": "t-2", "rewrite_trace": _LEAK}),
            json.dumps({"event": "query_executed", "thread_id": "t-1", "sql": "SELECT 1"}),
            "평문 로그 rewrite_trace t-1",
        ]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert SqlAuditTail(log).collect_rewrite_traces(0, "t-1") == [_PASS]

    def test_audit_log_fallback_reads_from_mark(self, tmp_path: Path):
        log = tmp_path / "server.log"
        old = json.dumps({"event": "rewrite_trace", "thread_id": "t-1", "rewrite_trace": _LEAK})
        log.write_text(old + "\n", encoding="utf-8")
        tail = SqlAuditTail(log)
        since = tail.mark()
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "rewrite_trace", "thread_id": "t-1",
                                     "rewrite_trace": _PASS}) + "\n")
        assert tail.collect_rewrite_traces(since, "t-1") == [_PASS]

    def test_raw_row_carries_trace_or_none(self):
        """레코드가 없으면 None — 분석기가 「미측정」과 「0건」을 구별한다."""
        meta = {"run_id": "r", "env": "dev", "mode": "live"}
        scenario = Scenario(id="T-01", group="T", plans=[107], title="t",
                            turns=[Turn(send={"query": "q"}, expect={})])
        empty = _row(meta, "baseline", scenario, 1, 0, Observation(), Verdict())
        traced = Observation(rewrite_traces=[_PASS])
        full = _row(meta, "baseline", scenario, 1, 0, traced, Verdict())
        assert empty["rewrite_trace"] is None
        assert full["rewrite_trace"] == [_PASS]


# ──────────────────────────────────────────────
# V28 — 107 §2.4 오염 4건 중 3건의 대응 시나리오가 카탈로그에 단언과 함께 있다
# ──────────────────────────────────────────────

_V28_PINS: list[tuple[str, str, Any]] = [
    # (시나리오, 단언 키, 단언에 포함돼야 하는 값) — plans/94 §19.1 대응표
    ("F-01", "sql_must_not_match", "(?i)여의도"),      # 2026-08-05 미선택 존 누출
    ("F-03", "sql_must_not_match", "(?i)여의도"),
    ("F-04", "sql_must_not_match", "(?i)ㅇㅇ존"),
    ("A-01", "sql_must_not_match", "(?i)여의도"),
    ("B-10", "sql_must_not_match", r"(?i)\blimit\s+\d"),  # 2026-07-24 LIMIT 절단
    ("A-02", "sql_must_not_match", r"(?i)\blimit\s+\d"),
    ("G-02", "sql_must_not_match", r"(?i)\bin\s*\("),     # 2026-08-04 직전 엔티티 축소
]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.mark.parametrize("scenario_id,key,value", _V28_PINS, ids=lambda v: str(v))
def test_V28_contamination_scenarios_keep_their_assertions(catalog, scenario_id, key, value):
    """W0.5(슬롯 승격) 전후 회귀 판정의 기준 — 이 단언이 빠지면 V28 이 조용히 무력화된다."""
    scenario = catalog.by_id(scenario_id)
    assert scenario is not None, f"{scenario_id} 가 카탈로그에서 사라졌다"
    declared = [p for turn in scenario.turns for p in turn.expect.get(key) or []]
    assert value in declared, f"{scenario_id}.{key} = {declared}"


def test_V28_limit_scenario_still_demands_full_rows(catalog):
    """B-10 은 행 수 하한(2,000)으로 2,328→1,000 절단을 잡는다.

    하한이 빠지면 LIMIT 단언만 남는다.
    """
    b10 = catalog.by_id("B-10")
    assert any((t.expect.get("row_count") or {}).get("min", 0) >= 2000 for t in b10.turns)


# ──────────────────────────────────────────────
# V29 — 회귀 비교는 같은 사다리 단끼리만
# ──────────────────────────────────────────────

def _summary(tier: str | None, verdict: str = "pass", *, arm: str | None = None,
             invalid: bool = False) -> dict[str, Any]:
    name = f"baseline+{arm}" if arm else "baseline"
    profile = {"name": name, "tier": tier}
    if arm:
        profile.update({"arm": arm, "base_profile": "baseline"})
    return {"meta": {"env": "closed", "repeat": 3},
            "invalid": {"over_threshold": invalid}, "profiles": [profile],
            "scenario_verdicts": {"A-01": {"verdict": verdict}}}


def _runs(tmp_path: Path, *names: str) -> Path:
    runs = tmp_path / "scenario"
    for name in names:
        (runs / name).mkdir(parents=True)
    return runs


class TestV29Baseline:
    def test_comparison_key_is_extensible_tuple(self):
        """비교 키는 (env, tier, base_profile, arm) — 옛 행은 arm=None, base_profile=이름."""
        assert analyze.COMPARISON_AXES == ("env", "tier", "base_profile", "arm")
        old = analyze.comparison_keys(_summary("intent_orchestration"))
        new = analyze.comparison_keys(_summary("semantic_router", arm="tier3_router"))
        assert old == {("closed", "intent_orchestration", "baseline", None)}
        assert new == {("closed", "semantic_router", "baseline", "tier3_router")}
        assert analyze.comparison_keys(_summary("mock")) == {("closed", None, "baseline", None)}

    def test_regression_skips_other_ladder_tier(self, tmp_path: Path, monkeypatch):
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000", "20260903-000000")
        fake = {
            "20260901-000000": _summary("semantic_router", verdict="fail"),
            "20260902-000000": _summary("intent_orchestration", verdict="pass"),
        }
        monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: fake[path.name])
        text = analyze.regression(runs / "20260903-000000", _summary("semantic_router", "pass"))
        assert "직전 비교 대상: `20260901-000000`" in text
        assert "`20260902-000000`(사다리 단이 다르다" in text

    def test_regression_skips_other_arm(self, tmp_path: Path, monkeypatch):
        """arm 이 없던 옛 run 과 arm 을 덧씌운 run 은 같은 (base_profile, arm) 조합이 아니다."""
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000")
        monkeypatch.setattr(analyze, "build_summary",
                            lambda path, _cat: _summary("semantic_router"))
        text = analyze.regression(runs / "20260902-000000",
                                  _summary("semantic_router", arm="tier3_router"))
        assert "비교 가능한 직전 run 이 없다." in text
        assert "프로파일·arm 구성이 다르다" in text

    def test_mock_run_is_never_a_baseline_for_a_real_run(self, tmp_path: Path, monkeypatch):
        """109·CS-43 — mock 은 tier 가 미관측(None)이라 비교 키를 통과한다. 모드로 거른다."""
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000", "20260903-000000")
        real, mock = _summary("semantic_router", "fail"), _summary("mock", "pass")
        real["meta"]["mode"], mock["meta"]["mode"] = "run", "mock"
        fake = {"20260901-000000": real, "20260902-000000": mock}
        monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: fake[path.name])
        now = _summary("semantic_router", "pass")
        now["meta"]["mode"] = "run"
        text = analyze.regression(runs / "20260903-000000", now)
        assert "직전 비교 대상: `20260901-000000`" in text
        assert "`20260902-000000`(모드가 다르다(mock → run))" in text

    def test_unfinished_run_is_not_a_baseline(self, tmp_path: Path, monkeypatch):
        """109·CS-19③ — 끊긴 run 도 시작 시점에 `run.json` 을 쓴다.

        일부만 돈 run 과는 비교하지 않는다.
        """
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000", "20260903-000000")
        cut = _summary("semantic_router")
        cut["meta"]["in_progress"] = True
        fake = {"20260901-000000": _summary("semantic_router"), "20260902-000000": cut}
        monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: fake[path.name])
        text = analyze.regression(runs / "20260903-000000", _summary("semantic_router"))
        assert "직전 비교 대상: `20260901-000000`" in text
        assert "`20260902-000000`(끝나지 않은 run" in text

    def test_unobserved_tier_does_not_block_comparison(self, tmp_path: Path, monkeypatch):
        """미관측(tier 없음)은 강등으로 세지 않는다(O-c) — 비교를 막지 않는다."""
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000")
        monkeypatch.setattr(analyze, "build_summary", lambda path, _cat: _summary(None))
        text = analyze.regression(runs / "20260902-000000", _summary("semantic_router"))
        assert "직전 비교 대상: `20260901-000000`" in text

    def test_baseline_is_recorded_in_summary_meta(self, tmp_path: Path, monkeypatch):
        """고른 기준선 run id 가 summary.meta.regression_baseline 에 남는다(파일에도)."""
        runs = _runs(tmp_path, "20260901-000000", "20260902-000000")
        run_dir = runs / "20260902-000000"
        (run_dir / "summary.json").write_text(
            json.dumps(_summary("semantic_router"), ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(analyze, "build_summary",
                            lambda path, _cat: _summary("semantic_router"))
        monkeypatch.setattr(analyze, "load_rows", lambda _run_dir: [])
        analyze.analyze(run_dir, None)
        record = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))["meta"][
            "regression_baseline"]
        assert record["run_id"] == "20260901-000000"
        assert record["axes"] == ["env", "tier", "base_profile", "arm"]
        assert record["keys"] == [["closed", "semantic_router", "baseline", None]]


# ──────────────────────────────────────────────
# V30 — 레코드가 없으면 비율을 만들지 않는다
# ──────────────────────────────────────────────

class TestV30Unmeasurable:
    def test_no_trace_summary_has_no_ratio(self):
        summary = analyze.rewrite_trace_summary([{"rewrite_trace": None}, {}])
        assert summary["measured"] is False
        assert summary["pass_through_ratio"] is None and summary["verify_failures"] is None

    def test_bottleneck_prints_fixed_phrase(self, tmp_path: Path):
        text = analyze.bottleneck(tmp_path, [{"node_elapsed_ms": {}}])
        assert analyze.REWRITE_TRACE_UNMEASURABLE in text

    def test_measured_summary_counts(self):
        rows = [{"rewrite_trace": [_PASS, _LEAK]}, {"rewrite_trace": [_REWRITTEN]}, {}]
        summary = analyze.rewrite_trace_summary(rows)
        assert (summary["turns"], summary["traces"], summary["pass_through"]) == (2, 3, 1)
        assert summary["pass_through_ratio"] == round(1 / 3, 4)
        assert summary["verified"] == 2
        assert summary["verify_failures"] == {"fail:location_leak": 1}

    def test_bottleneck_with_traces_shows_table_not_phrase(self, tmp_path: Path):
        text = analyze.bottleneck(tmp_path, [{"rewrite_trace": [_PASS]}])
        assert analyze.REWRITE_TRACE_UNMEASURABLE not in text
        assert "게이트 통과(pass_through)" in text


# ──────────────────────────────────────────────
# V31 — 슬롯 골든셋은 하네스를 거치지 않는다(두 자산이 서로를 부르지 않는다)
# ──────────────────────────────────────────────

def test_V31_harness_does_not_import_or_read_routing_gold():
    """`scripts/scenario/` 가 `routing_gold/` 를 import·참조하지 않는다.

    슬롯 채점은 서버 왕복이 없어 실행 원자가 다르다 — 하네스가 그것을 읽으면 D-127 게이트·
    프로파일·teardown 을 우회한 채점이 하네스 판정에 섞인다.
    """
    offenders = []
    for path in sorted((REPO_ROOT / "scripts" / "scenario").glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "routing_gold" in text:
            offenders.append(path.name)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                if any("routing_gold" in n or "intent_frame" in n for n in names):
                    offenders.append(f"{path.name}:import")
    assert not offenders, offenders


def test_V31_catalog_loads_only_scenario_dir(catalog):
    """카탈로그는 `testdata/scenarios/` 만 읽는다 — 골든셋 항목(rw-*)이 섞이지 않는다."""
    assert not [s.id for s in catalog.scenarios if s.id.startswith("rw-")]
