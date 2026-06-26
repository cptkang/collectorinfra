"""result_aggregator 노드 단위 테스트 (Plan 48 §9).

검증 대상:
- 결과 이질성 정규화 (test_result_heterogeneity, §4.9.4)
- 복합 결과 통합 (test_result_aggregator_merge)
"""

import pytest
from unittest.mock import AsyncMock, patch

import sys

from src.orchestration.result_aggregator import (
    _collect_superseded,
    _finalize_task,
    _merge_finalized,
    result_aggregator,
)

# 패키지 __init__.py가 result_aggregator(함수)를 재노출하여 동명 서브모듈을 가린다.
# 패치 대상 서브모듈은 sys.modules에서 직접 참조한다.
agg_mod = sys.modules["src.orchestration.result_aggregator"]
from src.state import create_initial_state


# ──────────────────────────────────────────────
# test_result_heterogeneity (§4.9.4)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_finalize_text_agent_uses_final_response(mock_config):
    """텍스트 agent 결과({"final_response":...})는 final_response를 그대로 사용한다."""
    task = {"task_id": "t1", "agent": "general_inference", "sub_query": "안녕", "order": 1}
    res = {"final_response": "안녕하세요, 무엇을 도와드릴까요?"}
    state = create_initial_state(user_query="안녕")

    # 텍스트 계열은 output_generator를 호출하지 않아야 함
    with patch.object(agg_mod, "output_generator", new=AsyncMock()) as og:
        out = await _finalize_task(task, res, state, AsyncMock(), mock_config)

    assert out["text"] == "안녕하세요, 무엇을 도와드릴까요?"
    assert out["output_file"] is None
    og.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_data_agent_uses_output_generator(mock_config):
    """data_query 결과({"organized_data":...})는 output_generator로 최종화한다."""
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 조회", "order": 1}
    res = {
        "organized_data": {"summary": "3대", "rows": [{"hostname": "web-01"}], "is_sufficient": True},
        "query_results": [{"hostname": "web-01"}],
    }
    state = create_initial_state(user_query="서버 조회")

    with patch.object(
        agg_mod, "output_generator",
        new=AsyncMock(return_value={"final_response": "서버 3대입니다", "output_file": None, "output_file_name": None}),
    ) as og:
        out = await _finalize_task(task, res, state, AsyncMock(), mock_config)

    og.assert_awaited_once()
    assert out["text"] == "서버 3대입니다"


# ──────────────────────────────────────────────
# test_result_aggregator_merge
# ──────────────────────────────────────────────

def test_merge_finalized_orders_and_marks_failure():
    """복합 결과를 order 순으로 묶되 본문에 내부 task 라벨을 노출하지 않고, 실패 task 안내는 포함."""
    finalized = [
        {"order": 1, "agent": "data_query", "text": "작업1 본문", "output_file": None,
         "output_file_name": None, "error": None},
        {"order": 2, "agent": "alarm_query", "text": "작업2 본문", "output_file": None,
         "output_file_name": None, "error": "조회 실패"},
    ]
    out = _merge_finalized(finalized)

    body = out["final_response"]
    # 본문에는 "### 작업 N (agent)" 라벨/내부 agent명을 노출하지 않는다 (처리 현황으로 이전)
    assert "### 작업" not in body
    assert "data_query" not in body
    assert "alarm_query" not in body
    # 각 task 결과 텍스트는 순서대로 이어붙인다
    assert "작업1 본문" in body
    assert "작업2 본문" in body
    assert body.index("작업1 본문") < body.index("작업2 본문")
    # 부분 실패 안내 포함 (D-005), 내부 agent명 없이 작업 순번만 노출
    assert "일부 작업이 실패했습니다" in body
    assert "조회 실패" in body
    assert out["current_node"] == "result_aggregator"


def test_merge_finalized_prefers_output_file():
    """output_file이 있는 task의 파일을 우선 반환한다."""
    finalized = [
        {"order": 1, "agent": "general_inference", "text": "설명", "output_file": None,
         "output_file_name": None, "error": None},
        {"order": 2, "agent": "data_query", "text": "표", "output_file": b"xlsxbytes",
         "output_file_name": "result.xlsx", "error": None},
    ]
    out = _merge_finalized(finalized)

    assert out["output_file"] == b"xlsxbytes"
    assert out["output_file_name"] == "result.xlsx"


@pytest.mark.asyncio
async def test_result_aggregator_composite_merge(mock_config):
    """복합 task 2개 → result_aggregator가 통합 final_response를 생성한다."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "첫 번째 답변"},
        "t2": {"final_response": "두 번째 답변"},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    body = out["final_response"]
    # 본문에는 내부 task 라벨을 노출하지 않고 각 답변을 순서대로 이어붙인다
    assert "### 작업" not in body
    assert "첫 번째 답변" in body
    assert "두 번째 답변" in body
    assert body.index("첫 번째 답변") < body.index("두 번째 답변")


# ──────────────────────────────────────────────
# test_supersedes (D-043: 재조회 대체 task 본문 제외)
# ──────────────────────────────────────────────

def test_collect_superseded_when_followup_succeeds():
    """후속 task가 성공했으면 그 supersedes 대상이 숨김 집합에 포함된다."""
    tasks = [
        {"task_id": "t1", "supersedes": []},
        {"task_id": "t2", "supersedes": ["t1"]},
    ]
    results = {"t1": {"final_response": "없음"}, "t2": {"final_response": "1건"}}
    assert _collect_superseded(tasks, results) == {"t1"}


def test_collect_superseded_skips_when_followup_failed():
    """후속(대체) task가 실패했으면 선행을 숨기지 않는다(안전)."""
    tasks = [
        {"task_id": "t1", "supersedes": []},
        {"task_id": "t2", "supersedes": ["t1"]},
    ]
    results = {"t1": {"final_response": "없음"}, "t2": {"error": "재조회 실패"}}
    assert _collect_superseded(tasks, results) == set()


@pytest.mark.asyncio
async def test_result_aggregator_hides_superseded_attempt(mock_config):
    """재조회로 대체된 1차 task 서술은 최종 답변 본문에서 제외되고, 성공한 후속만 노출."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q", "order": 1,
         "status": "completed", "supersedes": []},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "q", "order": 2,
         "status": "completed", "supersedes": ["t1"]},
    ]
    state = create_initial_state(user_query="김포 ### 서버 사양")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "조회된 1000건 중 존재하지 않습니다"},
        "t2": {"final_response": "기본 사양을 1건 확인했습니다"},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    body = out["final_response"]
    # 대체된 1차(실패 서술)는 숨기고, 성공한 재조회 결과만 단일 노출
    assert body == "기본 사양을 1건 확인했습니다"
    assert "존재하지 않습니다" not in body


@pytest.mark.asyncio
async def test_result_aggregator_keeps_attempt_when_followup_failed(mock_config):
    """재조회(대체) task가 실패하면 1차 결과를 그대로 유지한다(빈 답변 방지)."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q", "order": 1,
         "status": "completed", "supersedes": []},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "q", "order": 2,
         "status": "failed", "supersedes": ["t1"]},
    ]
    state = create_initial_state(user_query="q")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "1차 부분 결과"},
        "t2": {"error": "재조회 실패"},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    body = out["final_response"]
    assert "1차 부분 결과" in body


@pytest.mark.asyncio
async def test_result_aggregator_single_passthrough(mock_config):
    """단일 task → 묶음 헤더 없이 그대로 최종화한다."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q1", "order": 1, "status": "completed"},
    ]
    state = create_initial_state(user_query="단일")
    state["task_plan"] = tasks
    state["task_results"] = {"t1": {"final_response": "단일 답변"}}

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    assert out["final_response"] == "단일 답변"
    assert "### 작업" not in out["final_response"]
