"""plans/122 T-4 — 2단·3단 task에 요청 시간 해석(`time_resolution`) 배선 (D-309).

고정하는 계약:
① 2단 격리 입력(`_make_isolated_input`)이 `time_resolution`을 싣는다 — 격리 입력은 키를 직접
   고르므로 빠지면 task 안 소비처 전부가 None(종전 경로)을 받는다.
② 원문 해석이 전역 기본, task 문장에 **명시 기간**이 있을 때만 task별 해석(§10.3 「2단 task」).
   기준 시각은 원문 해석과 같다.
③ 3단 계획 루프는 원문 해석을 넘기고 `task_prompt`가 task 문장으로 좁힌다 — LangGraph 서브그래프
   채널(`TaskRunState`)을 지나 하류 노드까지 닿는다.
④ 대칭 — 같은 질의에서 그래프 state · 2단 task · 3단 task가 같은 `[start, end)`를 쓴다.
⑤ 원문 해석이 없으면(플래그 off) 모든 경로가 None — 종전 동작.

기준 시각 2026-09-29(화) 10:00 KST. 그래프 state 값은 `input_parser`가 쓰는 함수
(`resolve_query_time`)로 만든다(T-3 담당 — 여기서는 값의 하류 전달만 본다). LLM·DB 0.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.graph import END, START, StateGraph

import src.orchestration.subagents as subagents
import src.orchestration.tier3_plan as tier3
from src.domain.query_time import QueryTime, resolve_query_time
from src.domain.time_spec import KST
from src.orchestration.subagents import SubAgentSpec, _make_isolated_input
from src.orchestration.tier3_plan import TaskRunState, task_handler, task_payload, task_prompt
from src.state import create_initial_state

# 패키지 `__init__`이 같은 이름의 노드 함수를 재노출해 `import … as`로는 모듈을 못 잡는다
orch = importlib.import_module("src.orchestration.agent_orchestrator")

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=KST)

#: task별 기간이 갈리는 복합 질의 — 원문 해석은 두 기간의 합(`multiple_periods`)이다.
COMPOSITE = "지난달 CPU 상위 서버의 이번 달 메모리"
T1 = "지난달 CPU 사용률 상위 서버"
T2 = "앞 단계 서버들의 이번 달 메모리 사용률"
T3 = "서버별 디스크 사용률"  # 명시 기간 없음 → 원문 해석
#: 단일 기간 질의 — 단일 task(원문 그대로)
SINGLE = "최근 30일 CPU 상위 10대"


def _d(day: str, hour: int = 0) -> datetime:
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, tzinfo=KST)


#: 기대 `[start, end)` · 입도 (성능 통계 `metric`)
EXPECTED: dict[str, tuple[datetime, datetime, str]] = {
    COMPOSITE: (_d("2026-08-01"), _d("2026-09-29"), "day"),
    T1: (_d("2026-08-01"), _d("2026-09-01"), "month"),
    T2: (_d("2026-09-01"), _d("2026-09-29"), "day"),
    T3: (_d("2026-08-01"), _d("2026-09-29"), "day"),  # = 원문 해석
    SINGLE: (_d("2026-08-30"), _d("2026-09-29"), "day"),
}


def _graph_state(query: str, *, resolved: bool = True) -> dict[str, Any]:
    """`input_parser`를 지난 그래프 state(요청 스코프 `time_resolution` 포함)."""
    state: dict[str, Any] = dict(create_initial_state(user_query=query, thread_id="th-t4"))
    if resolved:
        state["time_resolution"] = resolve_query_time(query, NOW).to_state()
    return state


def _task(task_id: str, sub_query: str, **extra: Any) -> dict[str, Any]:
    return {
        "task_id": task_id, "agent": "data_query", "sub_query": sub_query,
        "depends_on": [], "input_from": [], "order": 1, "status": "in_progress", **extra,
    }


def _composite_tasks() -> list[dict[str, Any]]:
    return [
        _task("t1", T1),
        _task("t2", T2, depends_on=["t1"], input_from=["t1"], order=2),
        _task("t3", T3, order=3),
    ]


def _bounds(value: Any) -> tuple[datetime, datetime, datetime, str, datetime | None, datetime]:
    """state 값 → (기준 시각, metric start, metric end, grain, event start, event end)."""
    qt = QueryTime.from_state(value)
    assert qt is not None and qt.metric is not None and qt.event is not None, value
    return (qt.anchor_at, qt.metric.start, qt.metric.end, qt.metric.grain,
            qt.event.start, qt.event.end)


def _expect(text: str) -> tuple[datetime, datetime, str]:
    return EXPECTED[text]


# ──────────────────────────────────────────────
# ① ② 2단 격리 입력
# ──────────────────────────────────────────────

def test_graph_state_bounds_are_reference_values():
    """ⓐ 그래프 state — 기준 값 자체를 먼저 못 박는다(이하 대칭 단언의 기준)."""
    for query in (COMPOSITE, SINGLE):
        anchor, start, end, grain, _, _ = _bounds(_graph_state(query)["time_resolution"])
        assert anchor == NOW
        assert (start, end, grain) == _expect(query)


@pytest.mark.parametrize("task", _composite_tasks(), ids=lambda t: t["task_id"])
def test_tier2_isolated_input_scopes_explicit_task_period(task):
    """ⓑ 2단 — 명시 기간 task는 자기 기간, 없는 task는 원문 해석. 기준 시각은 같다."""
    state = _graph_state(COMPOSITE)
    isolated = _make_isolated_input(task, state, {})
    anchor, start, end, grain, _, _ = _bounds(isolated["time_resolution"])
    assert anchor == NOW
    assert (start, end, grain) == _expect(task["sub_query"])


def test_tier2_task_without_period_keeps_request_value_as_is():
    """기간 없는 task·원문 그대로인 단일 task는 원문 값 **그대로**(슬롯 처리 결과까지)."""
    composite = _graph_state(COMPOSITE)
    assert _make_isolated_input(_task("t3", T3), composite, {})["time_resolution"] is (
        composite["time_resolution"]
    )
    single = _graph_state(SINGLE)
    assert _make_isolated_input(_task("t1", SINGLE), single, {})["time_resolution"] is (
        single["time_resolution"]
    )


def test_tier2_task_period_event_runs_to_anchor():
    """알람(event) 해석도 task 범위 — 진행 중 기간(이번 달)은 기준 시각까지(D-291)."""
    isolated = _make_isolated_input(_task("t2", T2), _graph_state(COMPOSITE), {})
    *_, ev_start, ev_end = _bounds(isolated["time_resolution"])
    assert (ev_start, ev_end) == (_d("2026-09-01"), NOW)


def test_tier2_isolated_input_does_not_mutate_request_state():
    state = _graph_state(COMPOSITE)
    before = dict(state["time_resolution"])
    _make_isolated_input(_task("t1", T1), state, {})
    assert state["time_resolution"] == before


def test_flag_off_isolated_input_is_none():
    """⑤ 원문 해석이 없으면(플래그 off · 옛 체크포인트) task도 None — 종전 경로."""
    for task in _composite_tasks():
        assert _make_isolated_input(task, _graph_state(COMPOSITE, resolved=False), {})[
            "time_resolution"
        ] is None


@pytest.mark.asyncio
async def test_tier2_run_agent_handler_receives_task_scope():
    """2단 오케스트레이터 진입(`_run_agent`) — 처리기가 받는 격리 입력에 task 해석이 있다."""
    seen: dict[str, Any] = {}

    async def handler(task, isolated, *, llm, app_config):
        seen.update(isolated)
        return {}

    spec = SubAgentSpec("data_query", "capture", handler)
    with patch.object(orch, "resolve_subagent", return_value=spec):
        await orch._run_agent(_task("t2", T2), _graph_state(COMPOSITE), AsyncMock(), None, prior={})
    assert seen["user_query"] == T2
    assert _bounds(seen["time_resolution"])[1:4] == _expect(T2)


@pytest.mark.asyncio
@pytest.mark.parametrize("db_ids", [["polestar"], ["polestar_cm_gp", "polestar_cm_yd"]],
                         ids=["single_db", "multi_db"])
async def test_tier2_pipeline_nodes_receive_task_scope(db_ids, mock_config):
    """2단 데이터 조회 — 단일 DB 파이프라인·멀티 DB 실행기 입력 state까지 task 해석이 닿는다.

    재작성문(`user_query` = task 정제 질의)과 함께 실리므로 하류는 재작성문을 다시 해석하지 않고
    이 값을 읽는다(§10.2 ⑦ 단일 출처).
    """
    captured: dict[str, Any] = {}

    async def single(s, llm, app_config):
        captured.update(s)
        return {}

    async def multi(s, *, llm, app_config):
        captured.update(s)
        return {"db_results": {}}

    task = _task("t1", T1, db_ids=db_ids)
    isolated = _make_isolated_input(task, _graph_state(COMPOSITE), {})
    isolated["user_query"] = task["sub_query"]
    with patch.object(subagents, "_run_single_db_pipeline", new=single), \
         patch.object(subagents, "multi_db_executor", new=multi), \
         patch.object(subagents, "result_merger", new=AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", new=AsyncMock(return_value={})):
        out = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=mock_config,
        )
    assert captured["is_multi_db"] is (len(db_ids) > 1)
    assert _bounds(captured["time_resolution"])[1:4] == _expect(T1)
    # task 결과에도 싣는다 — 집계기 task 마감·원천별 기간 고지 입력(`res["time_resolution"]`)
    assert out["time_resolution"] == captured["time_resolution"]


# ──────────────────────────────────────────────
# ②' 원문 대조 — 분해 LLM이 지어 넣은 기간은 task 기간으로 승격하지 않는다(리뷰 상태 계약)
# ──────────────────────────────────────────────

#: 원문에 기간이 없다 — task 문장의 「지난주」는 분해 LLM이 지어낸 것이다.
NO_PERIOD_ORIGINAL = "CPU 상위 서버의 메모리"
HALLUCINATED_TASK = "지난주 CPU 상위 서버"


def test_tier2_task_period_absent_from_original_keeps_request_value():
    state = _graph_state(NO_PERIOD_ORIGINAL)
    isolated = _make_isolated_input(_task("t1", HALLUCINATED_TASK), state, {})
    assert isolated["time_resolution"] is state["time_resolution"]
    assert QueryTime.from_state(isolated["time_resolution"]).metric.source == "default"  # type: ignore[union-attr]


def test_tier1_ambient_uses_parsed_original_query():
    """1단 ambient에는 `user_query`가 없다 — `parsed_requirements.original_query`(원문)로 대조."""
    tr = resolve_query_time(COMPOSITE, NOW).to_state()
    ambient = {"time_resolution": tr, "parsed_requirements": {"original_query": COMPOSITE}}
    scoped = _make_isolated_input(_task("t1", T1), ambient, {})["time_resolution"]
    assert _bounds(scoped)[1:4] == _expect(T1)
    no_period = {
        "time_resolution": resolve_query_time(NO_PERIOD_ORIGINAL, NOW).to_state(),
        "parsed_requirements": {"original_query": NO_PERIOD_ORIGINAL},
    }
    kept = _make_isolated_input(_task("t1", HALLUCINATED_TASK), no_period, {})["time_resolution"]
    assert kept is no_period["time_resolution"]


def test_unknown_original_keeps_request_value():
    """원문을 알 수 없으면 task별 해석을 하지 않는다(대조할 수 없는 기간은 받지 않는다)."""
    tr = resolve_query_time(COMPOSITE, NOW).to_state()
    assert _make_isolated_input(_task("t1", T1), {"time_resolution": tr}, {})[
        "time_resolution"
    ] is tr


# ──────────────────────────────────────────────
# ③ 3단 계획 루프
# ──────────────────────────────────────────────

def test_tier3_payload_carries_request_value_unscoped():
    """`task_payload`는 좁히지 않는다 — 원문 해석 그대로(좁히기는 `task_prompt`)."""
    state = _graph_state(COMPOSITE)
    payload = task_payload(_task("t1", T1), state, {}, total=3)
    assert payload["time_resolution"] is state["time_resolution"]


@pytest.mark.asyncio
@pytest.mark.parametrize("task", _composite_tasks(), ids=lambda t: t["task_id"])
async def test_tier3_task_prompt_scopes_like_tier2(task):
    """ⓒ 3단 task — 2단과 같은 함수·같은 결과."""
    state = _graph_state(COMPOSITE)
    payload = task_payload(task, state, {}, total=3)
    out = await task_prompt(payload)  # type: ignore[arg-type]
    assert _bounds(out["time_resolution"]) == _bounds(
        _make_isolated_input(task, state, {})["time_resolution"]
    )


@pytest.mark.asyncio
async def test_tier3_task_prompt_uses_rendered_span_text():
    """조각(spans)으로 찍은 task 문장이 기준이다 — 계획 `sub_query`가 아니다."""
    state = _graph_state(COMPOSITE)
    task = _task("t2", "planner 재작성문", spans=["이번 달 메모리"])
    out = await task_prompt(task_payload(task, state, {}, total=2))  # type: ignore[arg-type]
    assert out["user_query"] == "이번 달 메모리"
    assert _bounds(out["time_resolution"])[1:4] == _expect(T2)


@pytest.mark.asyncio
async def test_tier3_scope_survives_task_subgraph_channels():
    """`TaskRunState` 서브그래프 채널을 지나 하류 노드가 task 해석을 읽는다.

    LangGraph는 선언하지 않은 키를 버린다 — 값이 `task_prompt` 출력에서 끊기지 않는지 실측.
    """
    seen: dict[str, Any] = {}

    def downstream(state: TaskRunState) -> dict[str, Any]:
        seen["time_resolution"] = state.get("time_resolution")
        return {}

    sub = StateGraph(TaskRunState)
    sub.add_node("task_prompt", task_prompt)
    sub.add_node("downstream", downstream)
    sub.add_edge(START, "task_prompt")
    sub.add_edge("task_prompt", "downstream")
    sub.add_edge("downstream", END)
    payload = task_payload(_task("t1", T1), _graph_state(COMPOSITE), {}, total=2)
    await sub.compile().ainvoke(payload)
    assert _bounds(seen["time_resolution"])[1:4] == _expect(T1)


@pytest.mark.asyncio
async def test_tier3_task_handler_isolated_input_carries_scope(mock_config):
    """노드 체인 없는 담당(APM·프로세스 등)도 `task_prompt`가 좁힌 값을 받는다."""
    seen: dict[str, Any] = {}

    async def handler(task, isolated, *, llm, app_config):
        seen.update(isolated)
        return {}

    task = _task("t2", T2, agent="apm_query")
    payload = task_payload(task, _graph_state(COMPOSITE), {}, total=2)
    payload.update(await task_prompt(payload))  # type: ignore[arg-type]
    spec = SubAgentSpec("apm_query", "capture", handler)
    with patch.object(tier3, "resolve_subagent", return_value=spec):
        await task_handler(payload, llm=AsyncMock(), app_config=mock_config)  # type: ignore[arg-type]
    assert _bounds(seen["time_resolution"])[1:4] == _expect(T2)


@pytest.mark.asyncio
async def test_tier3_pack_outcome_result_carries_task_scope():
    """3단 task 결과(`pack_outcome` — 2단과 같은 접기 함수)에도 task 해석이 실린다."""
    payload = task_payload(_task("t2", T2), _graph_state(COMPOSITE), {}, total=2)
    payload.update(await task_prompt(payload))  # type: ignore[arg-type]
    payload.update({"target_databases": [{"db_id": "polestar"}], "error_message": None})
    result = tier3.pack_outcome(payload)["task_outcomes"][0]["result"]  # type: ignore[arg-type]
    assert _bounds(result["time_resolution"])[1:4] == _expect(T2)


def test_flag_off_task_result_has_no_key():
    result = subagents._pack_pipeline_result(
        {"time_resolution": None}, [], None,
        ownership_notes=[], db_origin="planned", db_succeeded=False, db_pinned=False,
    )
    assert "time_resolution" not in result


@pytest.mark.asyncio
async def test_tier3_task_period_absent_from_original_keeps_request_value():
    state = _graph_state(NO_PERIOD_ORIGINAL)
    payload = task_payload(_task("t1", HALLUCINATED_TASK), state, {}, total=1)
    assert payload["request_query"] == NO_PERIOD_ORIGINAL  # 서브그래프 안 원문 대조 기준
    out = await task_prompt(payload)  # type: ignore[arg-type]
    assert out["time_resolution"] == state["time_resolution"]


@pytest.mark.asyncio
async def test_tier3_task_prompt_without_request_query_keeps_request_value():
    state = _graph_state(COMPOSITE)
    payload = task_payload(_task("t1", T1), state, {}, total=2)
    payload.pop("request_query")
    out = await task_prompt(payload)  # type: ignore[arg-type]
    assert out["time_resolution"] == state["time_resolution"]


@pytest.mark.asyncio
async def test_tier3_handler_input_excludes_request_query(mock_config):
    """`request_query`는 서브그래프 전용 — 2단 핸들러 격리 입력 모양은 종전 그대로."""
    seen: dict[str, Any] = {}

    async def handler(task, isolated, *, llm, app_config):
        seen.update(isolated)
        return {}

    payload = task_payload(_task("t1", T1, agent="apm_query"), _graph_state(COMPOSITE), {}, total=1)
    spec = SubAgentSpec("apm_query", "capture", handler)
    with patch.object(tier3, "resolve_subagent", return_value=spec):
        await task_handler(payload, llm=AsyncMock(), app_config=mock_config)  # type: ignore[arg-type]
    assert "request_query" not in seen and "time_resolution" in seen


@pytest.mark.asyncio
async def test_tier3_flag_off_is_none():
    payload = task_payload(_task("t1", T1), _graph_state(COMPOSITE, resolved=False), {}, total=1)
    assert payload["time_resolution"] is None
    assert (await task_prompt(payload))["time_resolution"] is None  # type: ignore[arg-type]


# ──────────────────────────────────────────────
# ④ 대칭 — 그래프 · 2단 · 3단
# ──────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query,tasks",
    [
        (COMPOSITE, _composite_tasks()),
        (SINGLE, [_task("t1", SINGLE)]),
    ],
    ids=["composite", "single"],
)
async def test_symmetry_graph_tier2_tier3_same_bounds(query, tasks):
    """같은 원문 → task마다 2단·3단이 같은 `[start, end)` · 기간 없는 task는 그래프 state와 같다."""
    state = _graph_state(query)
    graph_bounds = _bounds(state["time_resolution"])
    for task in tasks:
        tier2 = _bounds(_make_isolated_input(task, state, {})["time_resolution"])
        tier3_out = await task_prompt(task_payload(task, state, {}, total=len(tasks)))  # type: ignore[arg-type]
        tier3_bounds = _bounds(tier3_out["time_resolution"])
        assert tier2 == tier3_bounds
        assert tier2[0] == graph_bounds[0] == NOW  # 기준 시각은 하나
        assert tier2[1:4] == _expect(task["sub_query"])
        if _expect(task["sub_query"]) == _expect(query):
            assert tier2 == graph_bounds
