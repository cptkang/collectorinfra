"""plans/198 W3 · I-2 — 판별 조회로 가설을 검증하는 1회 루프(플레이북 · D-350 ①).

판별 스텝이 판단 보류를 지지·반박으로 바꾸는지, 결손이 그대로면 보류를 유지하는지, 상한 1회 ·
상위 N 제한 · 예산 부족 건너뜀 · 순위 결정성 · 플래그 off 비트 동일을 가짜 스텝 라이브러리로 본다.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from sre_agent.application.playbook_runner import (
    DISCRIMINATOR_NONE,
    DISCRIMINATOR_PRESENT,
    finalize_playbook_run,
    run_playbook,
)
from sre_agent.application.playbook_steps import STEP_LIBRARY, StepResult
from sre_agent.domain.playbook import STEP_ACTIONS, parse_playbook
from sre_agent.domain.playbook_hypotheses import (
    HYPOTHESIS_RULES,
    INCONCLUSIVE,
    REFUTED,
    SUPPORTED,
    discriminator_targets,
    rank_hypotheses,
    reverify_hypotheses,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "plan187"
ALARM = json.loads((FIXTURES / "normal.json").read_text(encoding="utf-8"))["alarm"]
_RUNBOOK = {"meaning": "m", "impact": "i", "owner": "o", "last_reviewed": "2026-10-17"}


def _playbook(*steps: dict):
    return parse_playbook({
        "id": "was.mini198",
        "version": 1,
        "match": {"resource_type": "apm.Instance"},
        "steps": [*steps, {"id": "S9", "action": "synthesize"}],
        "runbook": _RUNBOOK,
    })


S3 = {"id": "S3", "action": "apm.slow_tx_breakdown"}
S7 = {"id": "S7", "action": "change.recent"}


def _s3_sql(ctx):
    return StepResult(status="ok", data={"rows": [{"response_time_ms": 1000, "sql_ms": 800}]})


def _s7_near(ctx):
    cand = {"source": "deploy", "minutes_before_alarm": 10}
    return StepResult(status="ok", data={"candidates": [cand]})


class Lib(dict):
    """가짜 스텝 라이브러리 — 액션별 호출 수를 센다."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    def _wrap(self, action, fn):
        def run(ctx):
            self.calls[action] += 1
            return fn(ctx)
        return run


def _lib(s1=None, s2=None, s7=_s7_near, s3=_s3_sql) -> Lib:
    fns = {"change.recent": s7, "apm.slow_tx_breakdown": s3}
    if s1 is not None:
        fns["apm.golden_signals"] = s1
    if s2 is not None:
        fns["apm.runtime_and_pool"] = s2
    lib = Lib()
    for action, fn in fns.items():
        lib[action] = lib._wrap(action, fn)
    return lib


def _go(pb, lib, **kw):
    run = run_playbook(ALARM, pb, matched_by="t", polestar=None, apm=None, steps=lib, **kw)
    return finalize_playbook_run(run, pb)


def _hyp(run, rule_id):
    return next(h for h in run.synthesis["hypotheses"] if h["rule_id"] == rule_id)


# ── 보류 → 지지 / 반박 / 유지 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("verdict", "status", "after"),
    [("degraded", "ok", SUPPORTED), ("normal", "ok", REFUTED), (None, "missing", INCONCLUSIVE)],
)
def test_discriminator_changes_or_keeps_inconclusive(verdict, status, after):
    lib = _lib(s1=lambda ctx: StepResult(status=status, verdict=verdict))
    run = _go(_playbook(S7), lib, discriminate_top_n=2)
    h = _hyp(run, "H-CHANGE")
    v = h["verification"]
    assert v["ran"] is True and v["before"] == INCONCLUSIVE and v["after"] == after
    assert h["status"] == after
    assert [s["step"] for s in v["steps"]] == ["S1"]
    assert v["steps"][0]["condition"] == "S1.verdict == 'degraded'"
    assert v["reason"] is None
    # S9 표 데이터에도 같은 키가 실린다
    s9 = run.section("S9")
    assert s9.data["hypotheses"][0]["verification"] == v
    # 브리핑 판독은 1차 값(판별 스텝은 섹션이 아니다)
    assert run.section("S1") is None


def test_pool_discriminator_supports_db_pool_chain():
    def s2(ctx):
        return StepResult(status="ok", data={
            "targets": {"i1": {"pool": {"active": 10}}},
            "was_signals": [{"kind": "was_db_pool_exhaustion"}],
        })

    run = _go(_playbook(S3), _lib(s2=s2), discriminate_top_n=1)
    h = _hyp(run, "H-DB-POOL-CHAIN")
    assert h["verification"]["before"] == INCONCLUSIVE
    assert h["status"] == SUPPORTED and h["rank"] == 1


# ── 상한 1회 · 상위 N · 예산 · 이미 있는 단계 ───────────────────────────────


def test_loop_runs_once_even_if_still_inconclusive():
    lib = _lib(s1=lambda ctx: StepResult(status="missing", reason="결손"))
    run = _go(_playbook(S7), lib, discriminate_top_n=5)
    assert lib.calls["apm.golden_signals"] == 1
    assert _hyp(run, "H-CHANGE")["status"] == INCONCLUSIVE


def test_top_n_limits_targets():
    # S3 sql 지배 + S7 근접 → 보류: POOL-CHAIN(S2) · DB-SHARED · DB-INSTANCE · CHANGE(S1) 순
    s1 = lambda ctx: StepResult(status="ok", verdict="degraded")  # noqa: E731
    s2 = lambda ctx: StepResult(status="ok", data={})  # noqa: E731
    pb = _playbook(S3, S7)
    lib = _lib(s1=s1, s2=s2)
    run = _go(pb, lib, discriminate_top_n=2)
    with_v = [h["rule_id"] for h in run.synthesis["hypotheses"] if "verification" in h]
    assert sorted(with_v) == ["H-DB-POOL-CHAIN", "H-DB-SHARED"]
    assert lib.calls["apm.runtime_and_pool"] == 1 and lib.calls["apm.golden_signals"] == 0
    shared = _hyp(run, "H-DB-SHARED")["verification"]
    assert shared["ran"] is False and shared["steps"] == []
    assert shared["reason"] == DISCRIMINATOR_NONE

    lib4 = _lib(s1=s1, s2=s2)
    run4 = _go(pb, lib4, discriminate_top_n=4)
    assert lib4.calls["apm.golden_signals"] == 1
    assert _hyp(run4, "H-CHANGE")["verification"]["after"] == SUPPORTED


def test_budget_exhausted_skips_with_reason():
    t = {"now": 0.0}

    def s7(ctx):
        t["now"] += 500.0
        return _s7_near(ctx)

    lib = _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded"), s7=s7)
    run = _go(_playbook(S7), lib, discriminate_top_n=2, clock=lambda: t["now"], budget_s=240.0)
    v = _hyp(run, "H-CHANGE")["verification"]
    assert lib.calls["apm.golden_signals"] == 0
    assert v["ran"] is False and v["after"] == INCONCLUSIVE
    assert v["reason"].startswith("budget") and "판별 조회 생략" in v["reason"]
    assert v["steps"][0]["status"] == "skipped"


def test_step_already_in_playbook_is_not_rerun():
    s1_missing = {"id": "S1", "action": "apm.golden_signals"}
    lib = _lib(s1=lambda ctx: StepResult(status="missing", reason="결손"))
    run = _go(_playbook(s1_missing, S7), lib, discriminate_top_n=2)
    v = _hyp(run, "H-CHANGE")["verification"]
    assert lib.calls["apm.golden_signals"] == 1  # 플레이북 단계 1회뿐(재수집 없음)
    assert v["ran"] is False and v["reason"] == DISCRIMINATOR_PRESENT


# ── 결정성 · 플래그 off ──────────────────────────────────────────────────────


def test_ranking_deterministic():
    def once():
        lib = _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded"),
                   s2=lambda ctx: StepResult(status="ok", data={}))
        run = _go(_playbook(S3, S7), lib, discriminate_top_n=4)
        return json.dumps(run.synthesis["hypotheses"], ensure_ascii=False, sort_keys=True)

    assert once() == once()


def _strip_ms(text: str) -> str:
    return re.sub(r'"(total_ms|synthesis_ms|elapsed_ms)": \d+', r'"\1": 0', text)


def test_flag_off_bit_identical():
    def briefing(**kw):
        lib = _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded"))
        run = _go(_playbook(S7), lib, **kw)
        assert lib.calls["apm.golden_signals"] == 0
        assert run.discrimination is None
        return _strip_ms(json.dumps(run.to_briefing_dict(), ensure_ascii=False, sort_keys=True))

    base = briefing()
    assert base == briefing(discriminate_top_n=0)
    assert "verification" not in base


def test_verification_block_is_small():
    lib = _lib(s1=lambda ctx: StepResult(status="ok", verdict="degraded"),
               s2=lambda ctx: StepResult(status="ok", data={}))
    run = _go(_playbook(S3, S7), lib, discriminate_top_n=4)
    blocks = [h["verification"] for h in run.synthesis["hypotheses"] if "verification" in h]
    assert len(json.dumps(blocks, ensure_ascii=False).encode()) < 4096


# ── 도메인 ───────────────────────────────────────────────────────────────────


def test_rule_table_discriminators_are_well_formed():
    filled = [r.id for r in HYPOTHESIS_RULES if r.discriminators]
    assert filled == [
        "H-DB-POOL-CHAIN", "H-GC-LEAK", "H-CPU-LOGIC-CHANGE", "H-CPU-LOGIC", "H-CHANGE",
    ]
    for r in HYPOTHESIS_RULES:
        for d in r.discriminators:
            assert d.condition in r.conditions
            assert d.condition.split(".")[0] == d.step
            assert d.action in STEP_ACTIONS and d.action in STEP_LIBRARY


def test_reverify_only_targets_and_keeps_list():
    ctx = {"S7": {"change": "near"}, "dominant": "sql", "db_split": None}
    hyps = rank_hypotheses(ctx)
    targets = discriminator_targets(hyps, 2)
    assert [h.rule_id for h, _ in targets] == ["H-DB-POOL-CHAIN", "H-DB-SHARED"]
    after_ctx = {**ctx, "S1": {"verdict": "degraded"}, "S2": {"pool": "exhausted"}}
    out = reverify_hypotheses(hyps, after_ctx, {"H-DB-POOL-CHAIN"})
    assert [h.rule_id for h in out] == [h.rule_id for h in hyps]  # 지지는 이미 1순위 · 목록 불변
    assert out[0].status == SUPPORTED
    # 대상 밖 H-CHANGE는 S1이 생겨도 1차 상태 그대로
    assert next(h for h in out if h.rule_id == "H-CHANGE").status == INCONCLUSIVE
    assert reverify_hypotheses(hyps, after_ctx, set()) == hyps
