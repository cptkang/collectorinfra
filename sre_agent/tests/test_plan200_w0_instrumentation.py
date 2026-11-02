"""plans/200 W0(I-0 측정) — 턴 계측 · 플레이북 실행 시간 계측 · 타임라인 하네스 · 합성 픽스처.

- ① `_StreamDrain`이 턴별 도구 수를 세고 `_instrumentation`이 `tool_batch_sizes`·
  `max_tools_per_turn`·`turns_n`을 가산한다(도구 0인 턴은 목록에 없고 `turns_n`에는 센다).
- ② 기존 계측 키·값은 그대로다(가산만) · 플레이북 경로는 `PLAYBOOK_TIMING_KEYS`를 가산한다.
- ③ 하네스가 host.cpu를 오프라인으로 끝까지 돌고, 현행 단계 장벽
  (`S4,S6 | S0r | S1,S2 | S3a | S3b,S5,S7`)과 S5·S7 축 직렬을 재현한다.
- ④ plans/200 합성 픽스처는 미녹화 호출 0 · 전 스텝 ok.

실 LLM·MCP 0 — 가짜 스트림·가짜 소견기·녹화 조회만 쓴다.
"""

from __future__ import annotations

from typing import Any

import pytest
from holmes.core.models import ToolCallResult
from holmes.core.tools import StructuredToolResult, StructuredToolResultStatus
from holmes.utils.stream import StreamEvents, StreamMessage
from investigation_bench import playbook_timeline as tl
from sre_agent.application.investigation_dispatcher import (
    INSTRUMENTATION_KEYS,
    PLAYBOOK_TIMING_KEYS,
)
from sre_agent.application.playbook_runner import plan_stages
from sre_agent.diagnosis import DiagnosisResult, ToolCallRecord, _StreamDrain
from sre_agent.infrastructure.playbook_loader import load_playbooks
from test_plan154_w0_instrumentation_gaps import _dispatcher, _events, _one, _run, make_job

_TURN_KEYS = ("tool_batch_sizes", "max_tools_per_turn", "turns_n")


# ── ① 턴 계측 ─────────────────────────────────────────────────────────


def _start(call_id: str) -> StreamMessage:
    return StreamMessage(event=StreamEvents.START_TOOL, data={"tool_name": "t", "id": call_id})


def _result(call_id: str) -> StreamMessage:
    tcr = ToolCallResult(
        tool_call_id=call_id, tool_name="t", description=f"t {call_id}",
        result=StructuredToolResult(status=StructuredToolResultStatus.SUCCESS, data="x",
                                    params={"id": call_id}),
    )
    return StreamMessage(event=StreamEvents.TOOL_RESULT, data=tcr.to_client_dict())


class _OneStreamLLM:
    """`call_stream` 1회 — 턴 1(도구 3) → 턴 2(도구 1) → 턴 3(결론 · 도구 0)."""

    def call_stream(self, **kwargs: Any):  # noqa: ANN201
        yield StreamMessage(event=StreamEvents.AI_MESSAGE, data={"content": "", "reasoning": None})
        yield from (_start("a"), _start("b"), _start("c"))
        yield from (_result("b"), _result("a"), _result("c"))
        yield _start("d")
        yield _result("d")
        yield StreamMessage(event=StreamEvents.ANSWER_END, data={
            "content": "결론", "messages": [], "metadata": {}, "num_llm_calls": 3,
            "costs": {"total_tokens": 10, "total_cost": 0.0},
        })


def test_stream_drain_counts_tools_per_turn() -> None:
    drain = _StreamDrain(_OneStreamLLM())  # type: ignore[arg-type]
    result = drain.run([{"role": "user", "content": "q"}])

    assert drain.tool_batch_sizes == [3, 1]  # 도구 0인 결론 턴은 목록에 없다
    assert drain.turns_n == 3  # 결론 턴까지 센다
    assert result.num_llm_calls == 3 and len(result.tool_calls or []) == 4  # 결과 조립 불변


def _diag(**turn: Any):  # noqa: ANN202
    outputs = [ToolCallRecord(tool_name="t", description="t a", status="success", output="x",
                              params={"id": "a"})]

    def diagnose(job: Any) -> DiagnosisResult:
        return DiagnosisResult(answer="ok", tool_calls=["t a"], tool_outputs=list(outputs),
                               total_tokens=5, steps=3, **turn)

    return diagnose


def test_instrumentation_adds_turn_keys(tmp_path) -> None:  # noqa: ANN001
    audit = tmp_path / "a.jsonl"
    job = _run(_dispatcher(_diag(tool_batch_sizes=[3, 1], turns_n=3), audit), make_job("p199-t"))
    record = _one(_events(audit), "done")

    for view in (job.summary(), record):
        assert view["tool_batch_sizes"] == [3, 1]
        assert view["max_tools_per_turn"] == 3
        assert view["turns_n"] == 3


def test_instrumentation_turn_keys_none_when_result_lacks_them(tmp_path) -> None:  # noqa: ANN001
    audit = tmp_path / "a.jsonl"
    _run(_dispatcher(_diag(), audit), make_job("p199-n"))
    record = _one(_events(audit), "done")
    assert all(record[k] is None for k in _TURN_KEYS)


def test_instrumentation_max_is_zero_without_tool_turns(tmp_path) -> None:  # noqa: ANN001
    audit = tmp_path / "a.jsonl"
    _run(_dispatcher(_diag(tool_batch_sizes=[], turns_n=1), audit), make_job("p199-z"))
    record = _one(_events(audit), "done")
    assert (record["tool_batch_sizes"], record["max_tools_per_turn"], record["turns_n"]) == (
        [], 0, 1)


# ── ② 기존 키·값 불변(가산만) ───────────────────────────────────────────


def test_existing_instrumentation_keys_and_values_unchanged(tmp_path) -> None:  # noqa: ANN001
    audit = tmp_path / "a.jsonl"
    _run(_dispatcher(_diag(tool_batch_sizes=[3, 1], turns_n=3), audit), make_job("p199-k"))
    record = _one(_events(audit), "done")

    legacy = INSTRUMENTATION_KEYS[: INSTRUMENTATION_KEYS.index("tool_batch_sizes")]
    assert legacy == ("incomplete", "elapsed_s", "prefetch_s", "steps", "tool_calls_n", "model",
                      "max_steps", "tool_calls")
    assert INSTRUMENTATION_KEYS[len(legacy):] == _TURN_KEYS
    assert record["incomplete"] is False and record["steps"] == 3 and record["tool_calls_n"] == 1
    assert [c["name"] for c in record["tool_calls"]] == ["t"]
    assert not (set(PLAYBOOK_TIMING_KEYS) & set(record))  # 플레이북 경로 밖에는 없다


# ── ③ 하네스 — host.cpu 오프라인 종단 · 현행 장벽 ──────────────────────────


@pytest.fixture(scope="module")
def host_cpu_timeline() -> tl.Timeline:
    fixtures = tl.load_all_fixtures()
    return tl.run_timeline("host_cpu", fixtures["host_cpu"], tool_delay=0.03, llm_delay=0.0)


def _steps(t: tl.Timeline) -> dict[str, tl.Span]:
    return {s.step: s for s in t.of("step") if s.step}


def test_harness_runs_host_cpu_offline_and_records_timing(host_cpu_timeline: tl.Timeline) -> None:
    t = host_cpu_timeline
    assert t.status == "done" and t.playbook_id == "host.cpu" and t.unrecorded == 0
    ins = t.instrumentation
    assert set(PLAYBOOK_TIMING_KEYS) <= set(ins)
    assert len(ins["stage_wall_s"]) == 5
    assert ins["collect_s"] > 0 and ins["synthesis_s"] is not None
    # 실행 기록 off(해석 실행기 없음) · 심층 off
    assert ins["opinion_wait_s"] is None and ins["deep_dive_wait_s"] is None
    assert ins["collect_s"] <= t.total_s
    assert set(_steps(t)) == {"S4", "S6", "S0r", "S1", "S2", "S3a", "S3b", "S5", "S7"}


def test_harness_off_follows_current_stage_barriers(host_cpu_timeline: tl.Timeline) -> None:
    pb = next(p for p in load_playbooks().playbooks if p.id == "host.cpu")
    stages = [[st.id for st in stage if st.action != "synthesize"] for stage in plan_stages(pb)]
    stages = [s for s in stages if s]
    assert stages == [["S4", "S6"], ["S0r"], ["S1", "S2"], ["S3a"], ["S3b", "S5", "S7"]]
    steps = _steps(host_cpu_timeline)
    for before, after in zip(stages, stages[1:]):
        barrier = max(steps[s].end for s in before)
        assert all(steps[s].start >= barrier for s in after), (before, after)


def test_harness_reproduces_serial_axes_in_s5_s7(host_cpu_timeline: tl.Timeline) -> None:
    batches = {(s.step, s.label): s for s in host_cpu_timeline.of("tool")}
    # S5: 폴스타 알람 → 제니퍼 이벤트 직렬 · S7: 제니퍼 변경 → 폴스타 변경 이력 직렬
    assert batches[("S5", "polestar_incident_alarms")].end <= batches[("S5", "apm_events")].start
    assert (batches[("S7", "apm_source_changes,apm_change_impact")].end
            <= batches[("S7", "polestar_change_history")].start)


# ── ④ 합성 픽스처 — 미녹화 0 · 전 스텝 ok ─────────────────────────────────


@pytest.mark.parametrize(("name", "playbook_id"), [("host_cpu", "host.cpu"),
                                                   ("host_process", "host.process")])
def test_plan200_fixtures_have_no_unrecorded_calls(name: str, playbook_id: str) -> None:
    fx = tl.load_all_fixtures()[name]
    t = tl.run_timeline(name, fx, tool_delay=0.0, llm_delay=0.0)
    assert t.playbook_id == playbook_id == fx["expected"]["playbook_id"]
    assert t.unrecorded == 0 and t.tool_calls > 0
    ran = {s.step for s in t.of("step")}
    assert ran == set(fx["expected"]["section_order"]) - {"S9"}
