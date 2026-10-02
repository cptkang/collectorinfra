"""plans/121 묶음 B — 신규 플래그 없이 기본 동작을 바꾼 결함 교정(D-162 예외 · D-272 ③ · D-273 ①).

  TP-1.1  후속 턴 입력이 2단 계획·재계획 6키를 비우고, 재계획기 결정적 판정은 이번 계획 task만 본다.
  TP-1.1b 시간 상한 부분 결과는 확정 단이 2단이면 이번 계획 하위 작업 행만(최상위 행은 앞 턴 값).
  TP-1.3  재계획 신규 task도 담당 교정 + DAG 검증을 지난다(위반 task만 제외 + 노트 · 재요청 없음).
  TP-1.7  목록 밖 담당은 task 단위로 현행 디스패치 의미(폴백 담당)로 적는다.
  TP-11.4 재계획 예시 1은 사용자 조건을 완화하지 않는다.  TP-11.2 분해 프롬프트 환경어에 DR
          (잔여 — 환경어를 레지스트리 정본에서 렌더 · 종전 사본과 바이트 동일).

LLM·DB 0 — LLM 은 모의 객체다.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.partial_result import extract_partial_answer
from src.orchestration.schemas import AGENT_FALLBACK_KEY, close_agent_vocabulary
from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE
from src.prompts.replanner import REPLANNER_SYSTEM_TEMPLATE
from src.state import create_followup_input, create_initial_state
from src.utils.prior_dependency import NOTE_DECOMPOSE

_rp = importlib.import_module("src.orchestration.replanner")
_ip = importlib.import_module("src.orchestration.intent_planner")

_NO_FOLLOWUP = {"needs_followup": False, "reason": "충분", "new_tasks": []}


def _llm(payload: dict[str, Any]) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(payload, ensure_ascii=False))
    return llm


def _task(tid: str = "t1", agent: str = "data_query", **over: Any) -> dict[str, Any]:
    task = {"task_id": tid, "agent": agent, "sub_query": "김포 서버 CPU 상위 10건",
            "depends_on": [], "input_from": [], "order": int(tid[1:]), "status": "completed"}
    task.update(over)
    return task


def _rows(n: int = 2) -> dict[str, Any]:
    return {"query_results": [{"hostname": f"web-0{i}"} for i in range(n)],
            "organized_data": {"rows": [{"hostname": f"web-0{i}"} for i in range(n)]},
            "target_db_ids": ["polestar_cm_gp"]}


def _empty() -> dict[str, Any]:
    return {"query_results": [], "organized_data": {"rows": []},
            "target_db_ids": ["polestar_cm_gp"]}


def _state(tasks, results, **over) -> dict[str, Any]:
    state = create_initial_state(user_query="김포 서버 CPU 상위 10건")
    state["task_plan"] = tasks
    state["task_results"] = results
    state.update(over)
    return state


def _cfg(mock_config, *, dag: bool = True):
    mock_config.server.answer_reserve_sec = 0
    mock_config.replan_budget_prompt_enabled = False
    mock_config.composite.plan_dag_validation_enabled = dag
    return mock_config


# ── TP-1.1 ──────────────────────────────────────────────────────────────────


def test_followup_input_resets_six_plan_keys():
    delta = create_followup_input("다음 질문")
    assert delta["task_plan"] == [] and delta["task_results"] == {}
    assert delta["replan_count"] == 0 and delta["replan_history"] == []
    assert delta["needs_replan"] is False and delta["is_composite"] is False
    # 지시어·승계가 쓰는 키는 비우지 않는다(§12.5 TP-1.1)
    for kept in ("query_results", "target_databases", "parsed_requirements", "mapped_db_ids"):
        assert kept not in delta


@pytest.mark.asyncio
async def test_previous_turn_cap_does_not_block_this_turn(mock_config):
    """1턴이 재계획 상한이어도 2턴(델타 병합 뒤)은 재계획 평가를 탄다."""
    turn1 = _state([_task()], {"t1": _empty()}, replan_count=3)
    merged = {**turn1, **create_followup_input("다음 질문")}
    merged["task_plan"] = [_task()]
    merged["task_results"] = {"t1": _empty()}
    llm = _llm(_NO_FOLLOWUP)
    await _rp.replanner(merged, llm=llm, app_config=_cfg(mock_config))
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_zone_clarification_result_does_not_skip_replan(mock_config):
    """★ 앞 턴 task 결과의 존 역질문이 남아 있어도 이번 계획 판정에 끼지 않는다(7구간 실측)."""
    state = _state([_task()], {
        "t1": _empty(),
        "t9": {"zone_clarification": {"question": "어느 존?"}},  # 앞 턴 잔존
    })
    llm = _llm(_NO_FOLLOWUP)
    await _rp.replanner(state, llm=llm, app_config=_cfg(mock_config))
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_this_turn_zone_clarification_still_skips(mock_config):
    state = _state([_task()], {"t1": {"zone_clarification": {"question": "어느 존?"}}})
    llm = _llm(_NO_FOLLOWUP)
    out = await _rp.replanner(state, llm=llm, app_config=_cfg(mock_config))
    llm.ainvoke.assert_not_awaited()
    assert out["needs_replan"] is False


# ── TP-1.1b ─────────────────────────────────────────────────────────────────


def test_partial_answer_task_scoped_ignores_previous_turn_rows():
    state = {
        "query_results": [{"hostname": "앞턴"}],          # 집계기가 앞 턴 끝에 쓴 값
        "organized_data": {"rows": [{"hostname": "앞턴"}]},
        "task_plan": [{"task_id": "t1"}],
        "task_results": {"t1": _rows(1), "t7": _rows(3)},  # t7은 이번 계획 밖
    }
    scoped = extract_partial_answer(state, task_scoped=True)
    assert scoped is not None and scoped.source == "task" and scoped.task_id == "t1"
    assert scoped.rows == [{"hostname": "web-00"}]
    # 기본(1·3단)은 종전 규칙 그대로 — 최상위 행이 먼저다
    legacy = extract_partial_answer(state)
    assert legacy is not None and legacy.source == "query_results"


def test_partial_answer_task_scoped_without_this_turn_rows_is_none():
    state = {"query_results": [{"hostname": "앞턴"}], "task_plan": [], "task_results": {}}
    assert extract_partial_answer(state, task_scoped=True) is None


@pytest.mark.asyncio
async def test_route_scopes_partial_by_graph_tier(monkeypatch):
    """판정은 이 턴을 돌린 그래프로 한다(`intent_planner` 노드 = 2단) — 프로세스 전역 기록 아님."""
    from types import SimpleNamespace

    from src.api.routes import query as q

    state = {"query_results": [{"hostname": "앞턴"}], "task_plan": [{"task_id": "t1"}],
             "task_results": {"t1": _rows(1)}}
    monkeypatch.setattr(q, "_get_checkpoint_state", AsyncMock(return_value=state))
    tier2 = await q._partial_on_timeout(SimpleNamespace(nodes={"intent_planner": 1}), {})
    tier3 = await q._partial_on_timeout(SimpleNamespace(nodes={"semantic_router": 1}), {})
    unknown = await q._partial_on_timeout(None, {})
    assert tier2.source == "task"
    assert tier3.source == "query_results" and unknown.source == "query_results"


# ── TP-1.3 · TP-1.7 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_replanned_task_gets_alarm_coercion(mock_config):
    llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
        {"agent": "data_query", "sub_query": "김포 서버의 현재 활성 알람 현황", "depends_on": [],
         "input_from": [], "supersedes": []},
    ]})
    state = _state([_task()], {"t1": _empty()})
    out = await _rp.replanner(state, llm=llm, app_config=_cfg(mock_config))
    added = out["task_plan"][-1]
    assert added["agent"] == "alarm_query"
    assert out["replan_history"][-1]["added"] == 1


@pytest.mark.asyncio
async def test_replanned_task_with_invalid_reference_is_dropped_with_note(mock_config):
    llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
        {"agent": "data_query", "sub_query": "메모리 조회", "depends_on": ["t99"],
         "input_from": ["t99"], "supersedes": []},
        {"agent": "data_query", "sub_query": "디스크 조회", "depends_on": [], "input_from": [],
         "supersedes": []},
    ]})
    state = _state([_task()], {"t1": _empty()})
    out = await _rp.replanner(state, llm=llm, app_config=_cfg(mock_config))
    assert [t["sub_query"] for t in out["task_plan"][1:]] == ["디스크 조회"]
    assert out["replan_history"][-1]["added"] == 1          # 최종 수(§12.5 TP-1.3)
    notes = [n for n in out["dependency_notes"] if n.get("reason") == "replan_task_invalid"]
    assert len(notes) == 1 and notes[0]["kind"] == NOTE_DECOMPOSE
    llm.ainvoke.assert_awaited_once()                        # 재요청 없음


@pytest.mark.asyncio
async def test_all_invalid_replanned_tasks_end_with_note(mock_config):
    llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
        {"agent": "data_query", "sub_query": "x", "depends_on": ["t99"], "input_from": []},
    ]})
    out = await _rp.replanner(_state([_task()], {"t1": _empty()}), llm=llm,
                              app_config=_cfg(mock_config))
    assert out["needs_replan"] is False
    assert any(n.get("reason") == "replan_task_invalid" for n in out["dependency_notes"])


@pytest.mark.asyncio
async def test_dag_flag_off_keeps_prior_behavior(mock_config):
    llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
        {"agent": "data_query", "sub_query": "x", "depends_on": ["t99"], "input_from": []},
    ]})
    out = await _rp.replanner(_state([_task()], {"t1": _empty()}), llm=llm,
                              app_config=_cfg(mock_config, dag=False))
    assert out["needs_replan"] is True and len(out["task_plan"]) == 2
    assert "dependency_notes" not in out


def test_close_agent_vocabulary_is_task_level_and_keeps_known():
    known = {"agent": "alarm_query"}
    assert close_agent_vocabulary(known) is known and AGENT_FALLBACK_KEY not in known
    unknown = close_agent_vocabulary({"agent": "fault_diagnosis"})
    assert unknown["agent"] == "general_inference" and unknown[AGENT_FALLBACK_KEY] is True


@pytest.mark.asyncio
async def test_replanned_unknown_agent_uses_fallback_agent(mock_config):
    llm = _llm({"needs_followup": True, "reason": "보강", "new_tasks": [
        {"agent": "promql_query", "sub_query": "추이", "depends_on": [], "input_from": []},
        {"agent": "data_query", "sub_query": "디스크 조회", "depends_on": [], "input_from": []},
    ]})
    out = await _rp.replanner(_state([_task()], {"t1": _empty()}), llm=llm,
                              app_config=_cfg(mock_config))
    agents = [t["agent"] for t in out["task_plan"][1:]]
    assert agents == ["general_inference", "data_query"]


@pytest.mark.asyncio
async def test_json_decompose_unknown_agent_maps_per_task(mock_config):
    """JSON 분해 경로 — 목록 밖 담당만 폴백 담당으로(계획 전체 폴백·data_query 매핑 금지)."""
    mock_config.structured_output_backend = "none"
    payload = {"tasks": [
        {"task_id": "t1", "agent": "data_query", "sub_query": "CPU"},
        {"task_id": "t2", "agent": "fault_diagnosis", "sub_query": "원인 분석"},
    ]}
    llm = _llm(payload)
    fallback = {"tasks": [_task(status="pending")], "clarification_needed": None}
    plan = await _ip._decompose_once(llm, [], "CPU와 원인", mock_config, fallback)
    by_id = {t["task_id"]: t for t in plan["tasks"]}
    assert by_id["t1"]["agent"] == "data_query" and AGENT_FALLBACK_KEY not in by_id["t1"]
    assert by_id["t2"]["agent"] == "general_inference" and by_id["t2"][AGENT_FALLBACK_KEY]


# ── TP-11.4 · TP-11.2 ───────────────────────────────────────────────────────


def test_replanner_example_keeps_user_condition():
    assert "임계값을 낮춰" not in REPLANNER_SYSTEM_TEMPLATE
    assert "80% 이상인 서버 목록 조회" not in REPLANNER_SYSTEM_TEMPLATE
    assert "사용자가 정한 조건(임계값·기간·건수)은 재조회에서도 **바꾸지 않습니다.**" \
        in REPLANNER_SYSTEM_TEMPLATE


def _planner_cfg(*, ownership: bool = False, task_frame: bool = False) -> Any:
    """검증 대상 두 플래그를 명시한 설정 사본(.env 누수 차단)."""
    from src.config import load_config

    base = load_config().model_copy(deep=True)
    return base.model_copy(update={
        "composite": base.composite.model_copy(update={"task_frame_enabled": task_frame}),
        "router": base.router.model_copy(update={"capability_ownership_enabled": ownership}),
    })


#: 레지스트리 렌더 전(하드코딩 사본 "환경(운영/개발/스테이징/DR)") 기본 분해 프롬프트의 sha256 —
#: 소유·task 프레임 off. TP-11.2 레지스트리 렌더는 이 바이트를 바꾸지 않는다(의도한 프롬프트
#: 변경이면 함께 갱신한다).
_PRE_REGISTRY_RENDER_SHA256 = "97a59a2b59dc6d6a57fdace500c2933f856220d2902522b914c7eafaa8cb2590"


def test_decompose_prompt_env_terms_include_dr_and_anchors_kept():
    rendered = _ip._planner_system_prompt(_planner_cfg())
    assert "환경(운영/개발/스테이징/DR)" in rendered
    assert "## 출력 형식\n" in INTENT_PLANNER_SYSTEM_TEMPLATE
    assert "### 예시 4 " in INTENT_PLANNER_SYSTEM_TEMPLATE


def test_decompose_prompt_env_terms_rendered_from_registry():
    """TP-11.2 잔여 — 환경어는 템플릿 사본이 아니라 레지스트리 정본에서 렌더한다(D-131 · D-271)."""
    import hashlib

    from src.prompts import intent_planner as prompts
    from src.routing.registry import get_registry

    terms = get_registry().environment_terms
    assert terms == ("운영", "개발", "스테이징", "DR")                    # D-271 확정값
    assert INTENT_PLANNER_SYSTEM_TEMPLATE.count(prompts.ENVIRONMENT_TERMS_SLOT) == 1
    assert "운영/개발/스테이징" not in INTENT_PLANNER_SYSTEM_TEMPLATE        # 사본 없음
    rendered = _ip._planner_system_prompt(_planner_cfg())
    assert f"환경({'/'.join(terms)})" in rendered
    # 지문은 TP-11.2 렌더(환경어 채움)만 고정한다 — 답변 영역 칸(plans/132 N-5 · 2026-10-01 의도한
    # 프롬프트 변경)은 그 위의 삽입이라 기본 템플릿 렌더를 따로 대조한다.
    env_only = prompts.render_intent_planner_environment_terms(
        INTENT_PLANNER_SYSTEM_TEMPLATE, terms)
    assert hashlib.sha256(env_only.encode("utf-8")).hexdigest() == _PRE_REGISTRY_RENDER_SHA256
    assert rendered == prompts.render_intent_planner_environment_terms(
        prompts.render_intent_planner_areas_template(
            INTENT_PLANNER_SYSTEM_TEMPLATE, _ip._area_rows()), terms)
    assert _ip._planner_system_prompt(_planner_cfg()) is rendered        # 캐시 — 접두 불변


@pytest.mark.parametrize("ownership,task_frame", [(False, True), (True, False), (True, True)])
def test_decompose_prompt_env_slot_filled_on_every_flag_path(ownership, task_frame):
    from src.prompts import intent_planner as prompts

    rendered = _ip._planner_system_prompt(_planner_cfg(ownership=ownership, task_frame=task_frame))
    assert prompts.ENVIRONMENT_TERMS_SLOT not in rendered
    assert rendered.count("환경(운영/개발/스테이징/DR)") == 1


def test_decompose_prompt_env_terms_follow_registry_value(monkeypatch):
    """정본 값이 바뀌면 렌더가 따라간다 — 2단 분해·3단 순차 러너는 같은 함수를 쓴다."""
    from types import SimpleNamespace

    sequential_runner = importlib.import_module("src.orchestration.sequential_runner")
    real = _ip.get_registry()
    # 답변 영역 칸(plans/132 N-5)도 같은 조립 함수가 레지스트리에서 렌더한다 — 영역은 정본 그대로
    monkeypatch.setattr(_ip, "get_registry", lambda: SimpleNamespace(
        environment_terms=("운영", "DR"), capability_specs=real.capability_specs))
    rendered = _ip._planner_system_prompt(_planner_cfg())
    assert "환경(운영/DR)" in rendered and "스테이징" not in rendered
    assert sequential_runner._llm_decompose is _ip._llm_decompose


def test_decompose_prompt_env_render_guards():
    from src.prompts import intent_planner as prompts

    render = prompts.render_intent_planner_environment_terms
    with pytest.raises(RuntimeError, match="비어 분해 프롬프트"):
        render(INTENT_PLANNER_SYSTEM_TEMPLATE, ())
    with pytest.raises(RuntimeError, match="자리가 1회가 아니다"):
        render("자리 없음", ("운영",))
    with pytest.raises(RuntimeError, match="자리가 1회가 아니다"):
        render(prompts.ENVIRONMENT_TERMS_SLOT * 2, ("운영",))
