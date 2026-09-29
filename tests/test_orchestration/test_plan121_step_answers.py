"""plans/121 TP-4.5 — 복합(병합 불성립) 턴을 단계별 코드 표 + 짧은 요약으로 잇는다.

G-7 ① · D-062 의미 개정.

  ① 배선: 2단 그래프만 `composite_answer="steps"` · 1단 `deep_agent`·3단 계획 루프
     `finalize`는 LLM 합성 유지.
  ② 조립: 합성 LLM 0회(−1) · 단계 머리말 없음(D-005 `_merge_finalized` 모양) · 첫 파일·
     실패 안내·경과 노트 보존.
  ③ 스트리밍: 앞 단계는 한 번에 선행 본문, 마지막 단계는 표 먼저 + 요약 토큰 — 스트림이
     `done` 본문의 앞부분과 같고(화면 교체 때 튀지 않음) 남는 것은 본문 끝 덧붙임뿐이다.
     병합 성립 경로와 이중 발행이 없다.
  ④ 결정적 숨김: 실패·0건 선행 + 뒤 바퀴 같은 담당 독립 후속 성공이면 `supersedes`가 없어도
     숨긴다.
  골드 3종: (a) 0건 → 재조회(supersedes 있음·없음) (b) 부분 성공 → 재조회
  (c) 1단 재시도(LLM 합성 유지).

LLM·DB 0 — 요약·합성 LLM은 대역.
"""

from __future__ import annotations

import functools
import inspect
import sys
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import src.graph as graph_module
from src.config import AppConfig
from src.graph import build_graph
from src.llm import USER_RESPONSE_TAG
from src.orchestration.deep_agent import _aggregate_with_fabrix
from src.orchestration.replanner import _filter_futile_retries
from src.orchestration.result_aggregator import (
    _collect_implicit_superseded,
    _task_rounds,
    result_aggregator,
)
from src.state import AgentState, create_initial_state

agg_mod = sys.modules["src.orchestration.result_aggregator"]

_NO_ROWS = "데이터가 없습니다"


def _task(tid: str, agent: str, order: int, **kw: Any) -> dict[str, Any]:
    return {"task_id": tid, "agent": agent, "sub_query": f"{tid} 질의", "order": order,
            "status": "completed", "depends_on": [], "input_from": [], **kw}


def _rows(*values: int, db: str | None = None) -> dict[str, Any]:
    """식별 컬럼이 없는 행(공통 서버 키 병합 불성립)."""
    rows = [{"metric": "cpu", "value": v, **({"_source_db": db} if db else {})} for v in values]
    return {"organized_data": {"rows": rows, "summary": f"{len(rows)}건"}, "query_results": rows}


def _empty() -> dict[str, Any]:
    return {"organized_data": {"rows": [], "summary": ""}, "query_results": []}


def _state(tasks: list[dict[str, Any]], results: dict[str, Any], *,
           replan_added: list[int] | None = None) -> AgentState:
    state = create_initial_state("복합 질의")
    state["task_plan"] = tasks
    state["task_results"] = results
    state["replan_history"] = [
        {"count": i + 1, "reason": "재조회", "added": n} for i, n in enumerate(replan_added or [])
    ]
    return state


class _Stream:
    """SSE 라우트가 화면에 내보내는 답변 본문(선행 본문 + 태그 토큰)을 순서대로 모은다."""

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.summary_calls: list[list[str] | None] = []

    async def prefix(self, text: str) -> None:
        if text:
            self.chunks.append(text)

    async def summarize(self, llm: Any, messages: Any, tags: Any = None,
                        max_tokens: Any = None) -> str:
        self.summary_calls.append(tags)
        text = f"요약{len(self.summary_calls)} 입니다."
        if tags and USER_RESPONSE_TAG in tags:
            self.chunks.append(text)  # 토큰 스트림(대역은 한 청크)
        return text

    @property
    def text(self) -> str:
        return "".join(self.chunks)


async def _run_steps(
    state: AgentState, mock_config: AppConfig,
) -> tuple[dict[str, Any], _Stream, AsyncMock]:
    """실물 output_generator로 2단 집계기를 돌린다(요약·합성 LLM만 대역)."""
    stream = _Stream()
    synth = AsyncMock(return_value="합성 본문")
    with patch("src.nodes.output_generator.astream_text", stream.summarize), \
            patch("src.nodes.output_generator.emit_answer_prefix", stream.prefix), \
            patch.object(agg_mod, "emit_answer_prefix", stream.prefix), \
            patch.object(agg_mod, "astream_text", synth):
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=True,
            composite_answer="steps",
        )
    return out, stream, synth


# ── ① 배선 ────────────────────────────────────────────────────────────────────


def _bound_partial(compiled: Any, node: str) -> functools.partial[Any] | None:
    rc = compiled.nodes[node].bound
    target = rc.afunc if getattr(rc, "afunc", None) is not None else rc.func
    target = inspect.unwrap(target)
    return target if isinstance(target, functools.partial) else None


def _tier_cfg(mock_config: AppConfig, *, tier: int) -> AppConfig:
    mock_config.enable_deepagents_package = False
    mock_config.enable_intent_orchestration = tier == 2
    mock_config.enable_semantic_routing = tier == 3
    mock_config.tier3_plan_loop_enabled = tier == 3
    mock_config.enable_sql_approval = False
    return mock_config


def test_tier2_graph_wires_step_answers(
    mock_config: AppConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 2단 기준 경로만 단계별 조립 모드다(합성 모드 신호 `synthesize=True`는 유지 — 병합 선행)."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    bound = _bound_partial(build_graph(_tier_cfg(mock_config, tier=2)), "result_aggregator")
    assert bound is not None
    assert bound.keywords.get("synthesize") is True
    assert bound.keywords.get("composite_answer") == "steps"


def test_tier3_plan_loop_finalize_keeps_llm_synthesis(
    mock_config: AppConfig, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3단 계획 루프 `finalize`는 모드 인자를 넘기지 않는다 — 기본값(LLM 합성) 그대로."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")
    bound = _bound_partial(build_graph(_tier_cfg(mock_config, tier=3)), "finalize")
    assert bound is not None
    assert bound.keywords.get("synthesize") is True
    assert "composite_answer" not in bound.keywords
    default = inspect.signature(result_aggregator).parameters["composite_answer"].default
    assert default == "synthesize"


# ── ② 조립 · ③ 스트리밍 ─────────────────────────────────────────────────────────


def _two_zone_step(db_a: str, db_b: str, values: tuple[int, int]) -> dict[str, Any]:
    """존 2곳 행(출처 태그) + 존별 요약 — output_generator가 「존별 결과」 덧붙임을 단다."""
    rows = [{"metric": "cpu", "value": values[0], "_source_db": db_a},
            {"metric": "cpu", "value": values[1], "_source_db": db_b}]
    return {
        "organized_data": {"rows": rows, "summary": "2건"}, "query_results": rows,
        "db_result_summary": {db_a: {"row_count": 1, "display_name": "A존"},
                              db_b: {"row_count": 1, "display_name": "B존"}},
    }


@pytest.mark.asyncio
async def test_steps_stream_matches_done_body_and_drops_synthesis(mock_config: AppConfig) -> None:
    """★ 스트림 = done 본문 앞부분 — 앞 단계 덧붙임은 스트림 안, 마지막 단계 덧붙임만 끝에.

    합성 LLM 0회(−1) · 요약은 단계당 1회(앞 단계 비스트림 · 마지막 단계 USER_RESPONSE_TAG).
    """
    tasks = [_task("t1", "data_query", 1), _task("t2", "alarm_query", 2)]
    state = _state(tasks, {"t1": _two_zone_step("db_a", "db_b", (10, 20)),
                           "t2": _two_zone_step("db_a", "db_b", (3, 4))})
    out, stream, synth = await _run_steps(state, mock_config)

    body = out["final_response"]
    synth.assert_not_awaited()
    assert stream.summary_calls == [None, [USER_RESPONSE_TAG]]
    # 화면에 나간 본문이 done 본문의 앞부분이다(같은 순서·내용) — 교체 때 튀지 않는다
    assert body.startswith(stream.text)
    tail = body[len(stream.text):]
    assert tail.strip().startswith("**[존별 결과]**")  # 마지막 단계 덧붙임만 끝에 붙는다
    # 앞 단계 덧붙임은 이미 스트림에 실렸다
    first_note = stream.text.index("**[존별 결과]**")
    assert first_note < stream.text.index("요약2")
    # 단계 머리말 없음 · 표 → 요약 순서(단계마다)
    assert "작업 1" not in body and "[조회" not in body
    assert body.index("요약1") < body.index("요약2")
    assert out["query_results"] == (state["task_results"]["t1"]["query_results"]
                                    + state["task_results"]["t2"]["query_results"])


@pytest.mark.asyncio
async def test_steps_lead_chunk_is_emitted_once_before_last_step(mock_config: AppConfig) -> None:
    """앞 단계 셋 중 둘은 한 번에(단계 사이 무토큰 구간 없음), 마지막 단계는 표 먼저."""
    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2),
             _task("t3", "alarm_query", 3)]
    state = _state(tasks, {"t1": _rows(1), "t2": _rows(2), "t3": _rows(3)})
    out, stream, _ = await _run_steps(state, mock_config)

    lead, table3, summary3 = stream.chunks
    assert lead.endswith("\n\n") and "요약1" in lead and "요약2" in lead
    assert table3.startswith("|") and summary3 == "요약3 입니다."
    assert out["final_response"].startswith(stream.text)


@pytest.mark.asyncio
async def test_steps_turn_level_no_template_notice_appears_once(mock_config: AppConfig) -> None:
    """양식 없는 「엑셀로」 요청 안내(D-264 ④)는 턴 단위다 — 단계 수만큼 반복되지 않는다(결함 B).

    `output_generator`가 턴 전체 `output_format`으로 단계마다 안내를 머리에 붙인다. 첫 단계 것만
    남기고, `done` 본문이 스트림 본문의 뒤를 잇는 불변식도 지킨다(마지막 단계 안내가 중간에
    끼지 않음).
    """
    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2),
             _task("t3", "data_query", 3)]
    state = _state(tasks, {"t1": _rows(1), "t2": _rows(2), "t3": _rows(3)})
    state["parsed_requirements"] = {**(state.get("parsed_requirements") or {}),
                                    "output_format": "xlsx"}
    out, stream, _ = await _run_steps(state, mock_config)

    body = out["final_response"]
    assert body.startswith("양식 파일이 첨부되지 않아 Excel 파일은 만들지 않았습니다.")
    assert body.count("양식 파일이 첨부되지 않아") == 1
    assert body.startswith(stream.text)
    assert body.index("요약1") < body.index("요약2") < body.index("요약3")


@pytest.mark.asyncio
async def test_steps_keep_first_file_failure_notice_and_tail_notes(mock_config: AppConfig) -> None:
    """파일(첫 산출) · 실패 안내 · 재계획 종료 사유는 보존되고 본문 끝에 붙는다."""
    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2)]
    state = _state(tasks, {"t1": _rows(1), "t2": {"error": "RuntimeError"}})
    state["replan_stop_notice"] = "재계획을 멈춘 사유"

    async def fake_og(s: Any, **kw: Any) -> dict[str, Any]:
        return {"final_response": "표1", "output_file": b"xlsx", "output_file_name": "a.xlsx"}

    stream = _Stream()
    with patch.object(agg_mod, "output_generator", new=fake_og), \
            patch.object(agg_mod, "emit_answer_prefix", stream.prefix):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config,
                                      synthesize=True, composite_answer="steps")
    body = out["final_response"]
    assert out["output_file"] == b"xlsx" and out["output_file_name"] == "a.xlsx"
    assert body.startswith(stream.text) and stream.text == "표1\n\n"
    assert "일부 작업이 실패했습니다" in body and body.rstrip().endswith("재계획을 멈춘 사유")


@pytest.mark.asyncio
async def test_merged_path_is_not_emitted_twice_in_steps_mode(mock_config: AppConfig) -> None:
    """★ 병합 성립(TP-4.1)이면 단계 조립으로 가지 않는다 — 집계기 선행 본문 0 · 병합 표 1회."""
    tasks = [_task("t1", "alarm_query", 1), _task("t2", "data_query", 2)]
    state = _state(tasks, {
        "t1": {"organized_data": {"rows": [{"server_name": "SV1", "sev": 3}], "summary": "1건"}},
        "t2": {"organized_data": {"rows": [{"server_name": "SV1", "cpu": 42}], "summary": "1건"}},
    })
    calls: list[dict[str, Any]] = []

    async def fake_og(s: Any, **kw: Any) -> dict[str, Any]:
        calls.append(kw)
        return {"final_response": "병합 표", "output_file": None, "output_file_name": None}

    stream = _Stream()
    with patch.object(agg_mod, "output_generator", new=fake_og), \
            patch.object(agg_mod, "emit_answer_prefix", stream.prefix):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config,
                                      synthesize=True, composite_answer="steps")
    assert out["final_response"] == "병합 표"
    assert len(calls) == 1 and "stream_user_response" not in calls[0]  # 병합 표만 스트림(기본 True)
    assert stream.chunks == []


@pytest.mark.asyncio
async def test_single_task_in_steps_mode_passes_through_streaming(mock_config: AppConfig) -> None:
    tasks = [_task("t1", "data_query", 1)]
    out, stream, synth = await _run_steps(_state(tasks, {"t1": _rows(5)}), mock_config)
    assert stream.summary_calls == [[USER_RESPONSE_TAG]]
    assert out["final_response"].startswith(stream.text)
    synth.assert_not_awaited()


# ── ④ 결정적 숨김 ─────────────────────────────────────────────────────────────


def test_task_rounds_restores_replan_rounds_and_bails_on_mismatch() -> None:
    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2),
             _task("t3", "data_query", 3)]
    assert _task_rounds(tasks, [{"added": 1}]) == {"t1": 0, "t2": 0, "t3": 1}
    assert _task_rounds(tasks, [{"added": 1}, {"added": 1}]) == {"t1": 0, "t2": 1, "t3": 2}
    assert _task_rounds(tasks, [{"added": 4}]) == {}  # 복원 불가 → 숨기지 않는다


@pytest.mark.parametrize(
    ("pred_res", "succ", "succ_res", "added", "hidden"),
    [
        (_empty(), {"agent": "data_query"}, _rows(8), [1], {"t1"}),             # 0건 → 재조회 성공
        ({"error": "TimeoutError"}, {"agent": "data_query"}, _rows(8), [1], {"t1"}),  # 실패 → 성공
        ({"error": "게이트", "skipped": True}, {"agent": "data_query"}, _rows(8), [1], {"t1"}),
        (_empty(), {"agent": "data_query"}, _rows(8), [], set()),       # 같은 바퀴 = 다른 질문
        (_empty(), {"agent": "alarm_query"}, _rows(8), [1], set()),     # 다른 담당
        # 데이터 의존(input_from) 후속은 재조회가 아니다
        (_empty(), {"agent": "data_query", "input_from": ["t1"]}, _rows(8), [1], set()),
        (_empty(), {"agent": "data_query"}, _empty(), [1], set()),      # 후속도 0건
        (_empty(), {"agent": "data_query"}, {"error": "ValueError"}, [1], set()),  # 후속 실패
        (_rows(1), {"agent": "data_query"}, _rows(8), [1], set()),      # 행 있는 선행(부분 성공)
    ],
)
def test_implicit_supersede_rules(
    pred_res: dict[str, Any], succ: dict[str, Any], succ_res: dict[str, Any],
    added: list[int], hidden: set[str],
) -> None:
    tasks = [_task("t1", "data_query", 1), {**_task("t2", "data_query", 2), **succ}]
    assert _collect_implicit_superseded(tasks, {"t1": pred_res, "t2": succ_res},
                                        [{"added": n} for n in added]) == hidden


def test_implicit_supersede_text_agent_needs_body() -> None:
    tasks = [_task("t1", "general_inference", 1), _task("t2", "general_inference", 2)]
    results = {"t1": {"error": "RuntimeError"}, "t2": {"final_response": "설명"}}
    assert _collect_implicit_superseded(tasks, results, [{"added": 1}]) == {"t1"}
    results["t2"] = {"final_response": "  "}
    assert _collect_implicit_superseded(tasks, results, [{"added": 1}]) == set()


# ── 골드 3종 — 모순 이중 서술 0 ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("supersedes", [["t1"], []], ids=["supersedes있음", "supersedes없음"])
async def test_gold_a_zero_rows_then_requery(mock_config: AppConfig, supersedes: list[str]) -> None:
    """★ (a) 0건 → 재조회 성공: supersedes가 있든 없든 0건 서술이 본문에 남지 않는다."""
    tasks = [_task("t1", "data_query", 1),
             _task("t2", "data_query", 2, supersedes=supersedes)]
    state = _state(tasks, {"t1": _empty(), "t2": _rows(8)}, replan_added=[1])
    out, stream, synth = await _run_steps(state, mock_config)
    body = out["final_response"]
    assert _NO_ROWS not in body and _NO_ROWS not in stream.text
    assert "| 8 |" in body
    synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_gold_a_control_same_round_questions_both_kept(mock_config: AppConfig) -> None:
    """대조: 최초 분해의 두 질문(같은 바퀴)은 하나가 0건이어도 둘 다 싣는다 — 침묵 손실 없음."""
    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2)]
    out, stream, _ = await _run_steps(_state(tasks, {"t1": _empty(), "t2": _rows(8)}),
                                      mock_config)
    body = out["final_response"]
    assert _NO_ROWS in body and "| 8 |" in body
    assert body.index(_NO_ROWS) < body.index("| 8 |")
    assert body.startswith(stream.text)


@pytest.mark.asyncio
async def test_gold_b_partial_success_then_requery(mock_config: AppConfig) -> None:
    """★ (b) 부분 성공(행 있음 · 값 null) → 재조회.

    - 2단 재계획은 이 재조회를 만들기 전에 거른다(D-063 — supersedes 유무 무관).
    - 명시 supersedes로 도착하면 D-043이 선행을 숨긴다 → 본문은 재조회 결과 하나.
    """
    partial = {"organized_data": {"rows": [{"metric": "cpu", "value": None}], "summary": "1건"},
               "query_results": [{"metric": "cpu", "value": None}]}
    existing = [_task("t1", "data_query", 1)]
    for sup in (["t1"], []):
        proposed = [{**_task("t9", "data_query", 9), "supersedes": sup}]
        assert _filter_futile_retries(proposed, existing, {"t1": partial}) == []

    tasks = [_task("t1", "data_query", 1), _task("t2", "data_query", 2, supersedes=["t1"])]
    state = _state(tasks, {"t1": partial, "t2": _rows(8)}, replan_added=[1])
    out, stream, synth = await _run_steps(state, mock_config)
    body = out["final_response"]
    assert "| 8 |" in body and body.count("| metric |") == 1
    assert body.startswith(stream.text)
    synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_gold_c_tier1_retry_keeps_llm_synthesis(mock_config: AppConfig) -> None:
    """★ (c) 1단 재시도(도구 결과 1·2차): `deep_agent` 경로는 LLM 합성 1회를 유지한다.

    결정적 숨김도 적용하지 않는다(1단 바이트 불변) — 두 결과를 합성 입력에 싣고 LLM이 하나로 쓴다.
    """
    collector = [
        ({"task_id": "tool_data_query_1", "agent": "data_query", "order": 1, "sub_query": "q"},
         _empty()),
        ({"task_id": "tool_data_query_2", "agent": "data_query", "order": 2, "sub_query": "q"},
         _rows(8)),
    ]

    async def fake_og(s: Any, **kw: Any) -> dict[str, Any]:
        text = "CPU 8코어입니다" if s["organized_data"]["rows"] else f"CPU {_NO_ROWS}"
        return {"final_response": text, "output_file": None, "output_file_name": None}

    synth = AsyncMock(return_value="CPU 8코어입니다(합성)")
    stream = _Stream()
    with patch.object(agg_mod, "output_generator", new=fake_og), \
            patch.object(agg_mod, "astream_text", synth), \
            patch.object(agg_mod, "emit_answer_prefix", stream.prefix):
        out = await _aggregate_with_fabrix(collector, {"user_query": "q"}, mock_config, AsyncMock())

    assert out["final_response"] == "CPU 8코어입니다(합성)"
    synth.assert_awaited_once()
    assert USER_RESPONSE_TAG in synth.call_args.kwargs["tags"]
    prompt = synth.call_args.args[1][-1].content
    assert _NO_ROWS in prompt and "8코어" in prompt  # 숨김 없이 두 결과 모두 합성 입력
    assert stream.chunks == []  # 단계 선행 본문 없음(합성 중간 산출은 표 비발행 — D-268 ①)
