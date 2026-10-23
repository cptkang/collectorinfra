"""plans/187 W3 — 단계 소견기 · 종합기 · 결정적 가드 · 프롬프트 렌더 스냅숏.

LLM은 가짜 콜러블만 쓴다(실 LLM·과금 API·MLX 0). 스냅숏 갱신: `UPDATE_PLAN187_SNAPSHOTS=1`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from sre_agent.application.playbook_narrator import (
    NOT_CONFIGURED,
    POLARITY_DROPPED,
    NarratorLimits,
    PlaybookNarrator,
    StepNarration,
    guard_text,
    polarity_conflict,
    source_numbers,
    unknown_numbers,
)
from test_plan187_w2_runner import SCENARIOS
from test_plan187_w3_readings import run_fixture

SNAPSHOTS = Path(__file__).resolve().parent / "fixtures" / "plan187" / "snapshots"
FIXED_NOW = datetime(2026, 10, 19, 10, 5, 0)


class RecordingLlm:
    """메시지를 기록하고 정해진 응답을 돌려주는 가짜 LLM(스레드 안전)."""

    def __init__(self, reply=lambda messages: "관찰한 내용을 적는다.") -> None:
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []
        self._lock = threading.Lock()

    def __call__(self, messages):
        with self._lock:
            self.messages.append(messages)
        return self.reply(messages)


def _user(messages) -> str:
    return messages[-1]["content"]


def _is_synth(messages) -> bool:
    return _user(messages).startswith("[플레이북]")


def _step_of(messages) -> str:
    return _user(messages).split()[1]


# ── 가드 단위 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,verdict,conflict",
    [
        ("응답 지연이 급증했다.", "normal", True),
        ("응답 지연은 없다.", "normal", False),
        ("이상 징후는 보이지 않는다.", "normal", False),
        ("CPU가 80% 이상인 구간은 기준 미달이다.", "normal", False),  # 비교 뜻 「이상」
        ("기준선과 비슷하게 정상이다.", "degraded", True),
        ("정상 대비 지연이 크다.", "degraded", False),  # 이상 어휘가 함께 있으면 유지
        ("응답 지연이 크다.", None, False),  # 판정 없음은 극성 검사 안 함
    ],
)
def test_polarity_guard(text, verdict, conflict):
    assert polarity_conflict(text, verdict) is conflict
    op = guard_text(text, verdict=verdict, allowed=[80], max_sentences=4, max_chars=420)
    assert (op.dropped_reason == POLARITY_DROPPED) is conflict
    assert (op.text == "") is conflict


@pytest.mark.parametrize(
    "sentence,bad",
    [
        ("p95는 1234.5ms다.", []),
        ("p95는 약 1.2초다.", []),  # ÷1000 · 소수 1자리 반올림
        ("p95는 1.2345초다.", []),
        ("천 단위 쉼표 1,234.5ms도 같다.", []),
        ("오류율은 42%다.", []),  # 0.42 × 100
        ("상위 3건이 같은 서비스다.", []),  # 10 이하 개수
        ("평균은 999ms다.", ["999"]),
        ("15건이 느렸다.", ["15"]),  # 10 초과 개수는 허용 집합 대조
        ("p95는 1.3초다.", ["1.3"]),
    ],
)
def test_hallucinated_number_rules(sentence, bad):
    allowed = source_numbers("p95_ms 1234.5 · error_ratio 0.42 · rows 250")
    assert unknown_numbers(sentence, allowed) == bad


def test_hallucinated_number_sentence_removed_rest_kept():
    op = guard_text(
        "p95는 1234.5ms다. 평균은 999ms다. 원인은 종합에서 정한다.",
        verdict="degraded",
        allowed=[1234.5],
        max_sentences=4,
        max_chars=420,
    )
    assert op.text == "p95는 1234.5ms다. 원인은 종합에서 정한다."
    assert any("환각 수치" in g and "999" in g for g in op.guard)


def test_sentence_and_length_caps():
    text = " ".join(f"관찰 {chr(0xAC00 + i)}이다." for i in range(6))
    op = guard_text(text, verdict=None, allowed=[], max_sentences=4, max_chars=420)
    assert len(op.text.split(". ")) == 4 and any("문장 수 상한" in g for g in op.guard)
    op = guard_text(
        "가" * 50 + ". " + "나" * 50 + ".", verdict=None, allowed=[], max_sentences=4, max_chars=60
    )
    assert op.text == "가" * 50 + "." and any("길이 상한" in g for g in op.guard)
    op = guard_text("가" * 100 + ".", verdict=None, allowed=[], max_sentences=4, max_chars=30)
    assert len(op.text) == 30 and op.text.endswith("…")


def test_all_sentences_removed_gives_reason():
    op = guard_text("평균은 999ms다.", verdict=None, allowed=[], max_sentences=4, max_chars=420)
    assert op.text == "" and "환각 수치" in op.dropped_reason
    assert guard_text("  ", verdict=None, allowed=[], max_sentences=4, max_chars=420).dropped_reason


def test_rank_guard_synthesis_and_step():
    kw = dict(verdict=None, allowed=[], max_sentences=6, max_chars=900, rank_guard=True)
    op = guard_text(
        "1순위는 업무 DB 지연(공통 의존)이다. 1순위는 호스트 CPU 포화다.",
        top_title="업무 DB 지연(공통 의존)",
        **kw,
    )
    assert op.text == "1순위는 업무 DB 지연(공통 의존)이다."
    op = guard_text("가장 유력한 원인은 GC다. 관찰만 적는다.", top_title=None, **kw)
    assert op.text == "관찰만 적는다."


# ── 소견기 ──────────────────────────────────────────────────────────────────


def _req(step: str, verdict: str | None = "degraded", badges=()) -> StepNarration:
    return StepNarration(
        step=step,
        section={
            "step": step,
            "title": "t",
            "verdict": verdict,
            "table": {"columns": ["v"], "rows": [[12]]},
        },
        focus="f",
        max_sentences=4,
        readings={},
        realtime_badges=tuple(badges),
    )


def test_exception_and_timeout_empty_only_that_step():
    def reply(messages):
        step = _step_of(messages)
        if step == "S1":
            raise RuntimeError("boom")
        if step == "S2":
            time.sleep(1.0)
        return "값은 12다."

    n = PlaybookNarrator(RecordingLlm(reply), NarratorLimits(step_timeout_s=0.3))
    out = n.narrate_steps([_req("S1"), _req("S2"), _req("S3")])
    assert out["S1"].text == "" and "소견기 실패 — RuntimeError: boom" == out["S1"].dropped_reason
    assert out["S2"].text == "" and "타임아웃" in out["S2"].dropped_reason
    assert out["S3"].text == "값은 12다." and out["S3"].dropped_reason is None
    assert n.calls == 3


def test_realtime_step_states_current_value():
    n = PlaybookNarrator(RecordingLlm(lambda m: "풀 사용은 12다."))
    out = n.narrate_steps([_req("S2", badges=["현재값 · 알람 후 5분"]), _req("S3")])
    assert out["S2"].text.startswith("(현재값 기준)") and "현재값" not in out["S3"].text
    n = PlaybookNarrator(RecordingLlm(lambda m: "현재값 기준 풀 사용은 12다."))
    assert (
        n.narrate_steps([_req("S2", badges=["현재값"])])["S2"].text == "현재값 기준 풀 사용은 12다."
    )


def test_synthesis_exception_keeps_code_ranking():
    def boom(messages):
        raise TimeoutError("llm down")

    _, _, ref = run_fixture("latency_sql", synthesize=True)
    _, _, run = run_fixture("latency_sql", narrator=PlaybookNarrator(RecordingLlm(boom)))
    assert run.synthesis["hypotheses"] == ref.synthesis["hypotheses"]
    assert run.synthesis["narrative"] == "" and "실패" in run.synthesis["narrative_dropped_reason"]
    assert all(op.text == "" for op in run.opinions.values())


# ── 실행기 통합 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", SCENARIOS)
def test_llm_calls_every_narratable_step_plus_1(name):
    # 기본은 호출 상한 없음(plans/187 v2.8) — narrate 지시가 있고 ok인 단계 전부 + 종합 1.
    llm = RecordingLlm()
    n = PlaybookNarrator(llm)
    _, pb, run = run_fixture(name, narrator=n, now=lambda: FIXED_NOW)
    steps = [m for m in llm.messages if not _is_synth(m)]
    synth = [m for m in llm.messages if _is_synth(m)]
    assert len(synth) == 1 and n.calls == len(llm.messages) == len(steps) + 1
    assert not any(
        (op.dropped_reason or "").startswith("소견 호출 상한") for op in run.opinions.values()
    )
    assert run.timings["llm_calls"] == n.calls
    called = {_step_of(m) for m in steps}
    for step in pb.steps:
        op = run.opinions.get(step.id)
        if step.action == "synthesize":
            continue
        if step.narrate is None:
            assert op is None
        elif step.id not in called:
            assert op.text == "" and op.dropped_reason  # 침묵 생략 없음


def test_over_limit_cut_by_deterministic_priority():
    # 상한을 명시로 줄 때만 자른다(기본은 상한 없음).
    llm = RecordingLlm()
    _, _, run = run_fixture(
        "latency_sql", narrator=PlaybookNarrator(llm, NarratorLimits(max_step_calls=7))
    )
    called = [_step_of(m) for m in llm.messages if not _is_synth(m)]
    cut = [
        sid
        for sid, op in run.opinions.items()
        if (op.dropped_reason or "").startswith("소견 호출 상한")
    ]
    assert len(called) == 7 and cut == ["S7"]  # 가설 근거·degraded 스텝 먼저 → YAML 순서
    llm2 = RecordingLlm()
    run_fixture("latency_sql", narrator=PlaybookNarrator(llm2, NarratorLimits(max_step_calls=7)))
    assert sorted(_step_of(m) for m in llm2.messages if not _is_synth(m)) == sorted(called)


def test_ranking_unchanged_whatever_llm_writes():
    adversarial = RecordingLlm(
        lambda m: (
            "1순위는 호스트 CPU 포화다. 가장 유력한 원인은 GC다. 전혀 문제없다. 777건 발생했다."
        )
    )
    _, _, ref = run_fixture("case2_ldap_external", synthesize=True)
    _, _, run = run_fixture("case2_ldap_external", narrator=PlaybookNarrator(adversarial))
    for key in ("hypotheses", "actions", "followups", "top_inconclusive"):
        assert run.synthesis[key] == ref.synthesis[key]
    assert run.section("S9").table == ref.section("S9").table
    assert (
        "호스트 CPU" not in run.synthesis["narrative"] and "777" not in run.synthesis["narrative"]
    )
    # degraded 단계에 「문제없다」만 남는 서술은 아니지만 「포화」가 함께 있어 극성은 유지 —
    # 순위·환각 수치 문장은 제거되고 남은 문장만 실린다.
    for op in run.opinions.values():
        assert "777" not in op.text and "1순위" not in op.text


def test_normal_step_opposite_polarity_dropped_in_run():
    llm = RecordingLlm(lambda m: "응답 지연이 급증했다.")
    _, _, run = run_fixture("normal", narrator=PlaybookNarrator(llm))
    normal_steps = [
        s.step for s in run.sections if s.verdict == "normal" and s.step in run.opinions
    ]
    assert normal_steps
    for sid in normal_steps:
        assert run.opinions[sid].text == "" and run.opinions[sid].dropped_reason == POLARITY_DROPPED


def test_briefing_carries_opinions_and_synthesis():
    llm = RecordingLlm(
        lambda m: "업무 DB 지연(공통 의존)이 1순위다." if _is_synth(m) else "관찰한 내용을 적는다."
    )
    _, _, run = run_fixture("latency_sql", narrator=PlaybookNarrator(llm), now=lambda: FIXED_NOW)
    b = run.to_briefing_dict()
    json.dumps(b, ensure_ascii=False)
    s9 = next(s for s in b["sections"] if s["step"] == "S9")
    assert s9["opinion"] == b["synthesis"]["narrative"] == "업무 DB 지연(공통 의존)이 1순위다."
    assert set(b["synthesis"]["hypotheses"][0]) == {
        "rank",
        "title",
        "status",
        "status_label",
        "evidence_steps",
        "rule_id",
    }
    s2 = next(s for s in b["sections"] if s["step"] == "S2")
    assert s2["opinion"].startswith("(현재값 기준)") and s2["opinion_guard"]


def test_no_narrator_reason_is_not_configured():
    _, _, run = run_fixture("gc", synthesize=True)
    assert run.opinions["S1"].dropped_reason == NOT_CONFIGURED
    assert run.synthesis["narrative_dropped_reason"] == NOT_CONFIGURED


# ── 프롬프트 렌더 스냅숏 ────────────────────────────────────────────────────


def test_prompt_render_snapshot_latency_sql():
    llm = RecordingLlm()
    run_fixture("latency_sql", narrator=PlaybookNarrator(llm), now=lambda: FIXED_NOW)
    got = {
        "S2": next(m for m in llm.messages if not _is_synth(m) and _step_of(m) == "S2"),
        "S3": next(m for m in llm.messages if not _is_synth(m) and _step_of(m) == "S3"),
        "synthesis": next(m for m in llm.messages if _is_synth(m)),
    }
    path = SNAPSHOTS / "latency_sql_prompts.json"
    text = json.dumps(got, ensure_ascii=False, indent=2) + "\n"
    if os.environ.get("UPDATE_PLAN187_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert text == path.read_text(encoding="utf-8")
    # 실시간 단면 지시 · 판정 · 판독이 프롬프트에 실린다.
    assert "[실시간 단면]" in _user(got["S2"]) and "[실시간 단면]" not in _user(got["S3"])
    assert "- dominant=sql (R-C)" in _user(got["S3"]) and "db_split=n/a" in _user(got["S3"])
    assert "1. 업무 DB 지연(공통 의존) [판단 보류]" in _user(got["synthesis"])


def test_time_digits_in_source_are_allowed():
    allowed = source_numbers("변경 at 2026-10-19T07:20:00")
    assert unknown_numbers("07시 20분 변경이 있었다.", allowed) == []
