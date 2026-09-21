"""result_aggregator 노드 단위 테스트 (Plan 48 §9).

검증 대상:
- 결과 이질성 정규화 (test_result_heterogeneity, §4.9.4)
- 복합 결과 통합 (test_result_aggregator_merge)
"""

import pytest
from unittest.mock import AsyncMock, patch

import sys

from src.orchestration.result_aggregator import (
    _apply_incomplete_notice,
    _build_output_state,
    _collect_superseded,
    _finalize_task,
    _merge_finalized,
    _synthesize_finalized,
    result_aggregator,
)
from src.llm import USER_RESPONSE_TAG

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


@pytest.mark.asyncio
async def test_finalize_process_query_empty_keeps_diagnostic_summary(mock_config):
    """process_query 빈 결과(rows=[])는 진단 summary를 output_generator로 덮어쓰지 않고 노출한다.

    회귀 방지: 과거 result_aggregator가 organized_data를 무조건 output_generator로
    최종화하여, process_query의 원인 메시지(서버 식별 실패·API 미응답·0건 등)가
    일반 "조건에 해당하는 …데이터가 없습니다" 문구로 사라졌다(Plan 50 M4 / D-046).
    """
    task = {"task_id": "t1", "agent": "process_query", "sub_query": "김포 ### 프로세스", "order": 1}
    res = {
        "organized_data": {
            "summary": "서버 '###'의 실시간 프로세스를 조회하지 못했습니다 (프로세스 API 미응답/타임아웃).",
            "rows": [],
            "is_sufficient": False,
        },
        "query_results": [],
        "process_query": {"db_id": "polestar_cm_gp", "hostname": "###", "total_count": 0},
    }
    state = create_initial_state(user_query="김포 운영 ###의 프로세스 리스트 조회")

    # 빈 결과여도 output_generator(일반 빈-결과 문구)를 호출하지 않아야 함
    with patch.object(agg_mod, "output_generator", new=AsyncMock()) as og:
        out = await _finalize_task(task, res, state, AsyncMock(), mock_config)

    assert out["text"] == res["organized_data"]["summary"]
    assert "데이터가 없습니다" not in out["text"]
    og.assert_not_called()


@pytest.mark.asyncio
async def test_finalize_process_query_nonempty_uses_output_generator(mock_config):
    """process_query 비-빈 결과(rows 존재)는 기존대로 output_generator로 최종화한다."""
    task = {"task_id": "t1", "agent": "process_query", "sub_query": "김포 ### 프로세스", "order": 1}
    res = {
        "organized_data": {
            "summary": "상위 5건",
            "rows": [{"name": "java", "pid": 1234}],
            "is_sufficient": True,
        },
        "query_results": [{"name": "java", "pid": 1234}],
        "process_query": {"db_id": "polestar_cm_gp", "hostname": "###", "total_count": 1},
    }
    state = create_initial_state(user_query="김포 운영 ###의 프로세스 리스트 조회")

    with patch.object(
        agg_mod, "output_generator",
        new=AsyncMock(return_value={"final_response": "java(pid 1234)", "output_file": None, "output_file_name": None}),
    ) as og:
        out = await _finalize_task(task, res, state, AsyncMock(), mock_config)

    og.assert_awaited_once()
    assert out["text"] == "java(pid 1234)"


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


# ──────────────────────────────────────────────
# test_synthesize (D-062: 딥 에이전트 경로 단일 LLM 합성)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthesize_merges_into_single_answer(mock_config):
    """synthesize=True + 복합 task → LLM 1회 합성으로 단일 답변(이어붙이기 아님)을 만든다.

    회귀 방지: 딥 에이전트가 동일 질문을 재시도하면 collector에 1·2차 결과가 모두 쌓여
    "없음→있음" 모순 이중 답변이 한 말풍선에 이어붙는다(D-062). 합성으로 단일화한다.
    """
    tasks = [
        {"task_id": "tool_data_query_1", "agent": "general_inference", "sub_query": "q", "order": 1, "status": "completed"},
        {"task_id": "tool_data_query_2", "agent": "general_inference", "sub_query": "q", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="김포 ### 서버 사양")
    state["task_plan"] = tasks
    state["task_results"] = {
        "tool_data_query_1": {"final_response": "CPU/메모리는 데이터에 존재하지 않습니다"},
        "tool_data_query_2": {"final_response": "CPU 8코어, 메모리 32GB로 확인되었습니다"},
    }

    synth = AsyncMock(return_value="CPU 8코어, 메모리 32GB입니다")
    with patch.object(agg_mod, "astream_text", new=synth) as st:
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=True
        )

    # 단일 합성 결과만 노출(이어붙인 모순 서술 없음)
    assert out["final_response"] == "CPU 8코어, 메모리 32GB입니다"
    assert "존재하지 않습니다" not in out["final_response"]
    # 합성은 최종 사용자 응답 태그로 1회 스트리밍 호출되어야 한다(D-009)
    st.assert_awaited_once()
    assert USER_RESPONSE_TAG in st.call_args.kwargs.get("tags", [])


@pytest.mark.asyncio
async def test_synthesize_suppresses_pertask_streaming(mock_config):
    """합성 모드에서 per-task output_generator는 USER_RESPONSE_TAG를 끄고 호출된다.

    중간 per-task 토큰이 SSE로 새어 합성 전 답변이 보이는 것을 방지(D-062/D-009).
    """
    tasks = [
        {"task_id": "tool_data_query_1", "agent": "data_query", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "tool_data_query_2", "agent": "data_query", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합")
    state["task_plan"] = tasks
    state["task_results"] = {
        "tool_data_query_1": {"organized_data": {"summary": "s1", "rows": [{"a": 1}], "is_sufficient": True}, "query_results": [{"a": 1}]},
        "tool_data_query_2": {"organized_data": {"summary": "s2", "rows": [{"a": 2}], "is_sufficient": True}, "query_results": [{"a": 2}]},
    }

    og = AsyncMock(return_value={"final_response": "표", "output_file": None, "output_file_name": None})
    with patch.object(agg_mod, "output_generator", new=og), \
         patch.object(agg_mod, "astream_text", new=AsyncMock(return_value="합성")):
        await result_aggregator(state, llm=AsyncMock(), app_config=mock_config, synthesize=True)

    # 두 task 모두 stream_user_response=False로 마감되어야 함
    assert og.await_count == 2
    for call in og.await_args_list:
        assert call.kwargs.get("stream_user_response") is False


@pytest.mark.asyncio
async def test_synthesize_falls_back_to_merge_on_error(mock_config):
    """합성 LLM 호출이 실패하면 deterministic 이어붙이기로 안전 폴백한다."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "q", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="q")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "첫 결과"},
        "t2": {"final_response": "둘째 결과"},
    }

    with patch.object(agg_mod, "astream_text", new=AsyncMock(side_effect=RuntimeError("boom"))):
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=True
        )

    # 폴백: 두 결과가 이어붙어 노출(빈 답변 방지)
    body = out["final_response"]
    assert "첫 결과" in body and "둘째 결과" in body


@pytest.mark.asyncio
async def test_synthesize_single_task_passthrough(mock_config):
    """synthesize=True여도 단일 task는 합성 없이 그대로 통과한다(스트리밍 유지)."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q", "order": 1, "status": "completed"},
    ]
    state = create_initial_state(user_query="단일")
    state["task_plan"] = tasks
    state["task_results"] = {"t1": {"final_response": "단일 답변"}}

    synth = AsyncMock(return_value="합성되면 안 됨")
    with patch.object(agg_mod, "astream_text", new=synth):
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=True
        )

    assert out["final_response"] == "단일 답변"
    synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesize_finalized_preserves_file_and_rows(mock_config):
    """합성은 첫 산출 task의 output_file을 채택하고 query_results를 누적한다."""
    finalized = [
        {"order": 1, "agent": "data_query", "text": "표1", "output_file": None,
         "output_file_name": None, "error": None, "query_results": [{"a": 1}]},
        {"order": 2, "agent": "data_query", "text": "표2", "output_file": b"xlsx",
         "output_file_name": "r.xlsx", "error": None, "query_results": [{"a": 2}]},
    ]
    state = create_initial_state(user_query="복합")

    with patch.object(agg_mod, "astream_text", new=AsyncMock(return_value="합성 본문")):
        out = await _synthesize_finalized(finalized, state, AsyncMock(), mock_config)

    assert out["final_response"] == "합성 본문"
    assert out["output_file"] == b"xlsx"
    assert out["output_file_name"] == "r.xlsx"
    assert out["query_results"] == [{"a": 1}, {"a": 2}]


@pytest.mark.asyncio
async def test_non_synthesize_composite_still_concatenates(mock_config):
    """synthesize 미지정(replanner 경로)은 기존 deterministic 이어붙이기를 유지한다(D-005)."""
    tasks = [
        {"task_id": "t1", "agent": "general_inference", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "첫 번째"},
        "t2": {"final_response": "두 번째"},
    }

    synth = AsyncMock(return_value="합성되면 안 됨")
    with patch.object(agg_mod, "astream_text", new=synth):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    body = out["final_response"]
    assert "첫 번째" in body and "두 번째" in body
    synth.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_aggregator_promotes_query_results(mock_config):
    """단일 task의 query_results를 top-level state로 승격한다(CSV/row_count, D-047)."""
    tasks = [
        {"task_id": "t1", "agent": "process_query", "sub_query": "프로세스", "order": 1, "status": "completed"},
    ]
    state = create_initial_state(user_query="### 서버 프로세스")
    state["task_plan"] = tasks
    full = [{"name": f"p{i}", "pid": i} for i in range(7)]
    state["task_results"] = {
        "t1": {
            "organized_data": {"summary": "상위 5건", "rows": full[:5], "is_sufficient": True},
            "query_results": full,
        }
    }

    with patch.object(
        agg_mod, "output_generator",
        new=AsyncMock(return_value={"final_response": "표", "output_file": None, "output_file_name": None}),
    ):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    # 전체 7건이 top-level query_results로 승격됨(CSV 다운로드/row_count 근거)
    assert out["query_results"] == full
    assert len(out["query_results"]) == 7


@pytest.mark.asyncio
async def test_result_aggregator_promotes_db_id_for_multiturn(mock_config):
    """실행 task의 target_db_ids를 active_db_id/target_databases로 승격한다(멀티턴 DB 승계, D-053)."""
    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "은행 ### OS/CPU", "order": 1, "status": "completed"},
    ]
    state = create_initial_state(user_query="은행 ### 서버 OS/CPU/메모리")
    state["task_plan"] = tasks
    rows = [{"hostname": "bankhost01", "os": "RHEL"}]
    state["task_results"] = {
        "t1": {
            "organized_data": {"summary": "조회됨", "rows": rows, "is_sufficient": True},
            "query_results": rows,
            "target_db_ids": ["polestar_b0"],
        }
    }

    with patch.object(
        agg_mod, "output_generator",
        new=AsyncMock(return_value={"final_response": "결과", "output_file": None, "output_file_name": None}),
    ):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    # 다음 턴 context_resolver._extract_previous_db_ids가 읽을 필드로 승격
    assert out["active_db_id"] == "polestar_b0"
    assert out["target_databases"] == [{"db_id": "polestar_b0"}]

    # 승격된 필드가 실제로 previous_db_ids로 추출되는지 종단 확인
    from src.nodes.context_resolver import _extract_previous_db_ids
    assert _extract_previous_db_ids({"target_databases": out["target_databases"],
                                     "active_db_id": out["active_db_id"]}) == ["polestar_b0"]


# ──────────────────────────────────────────────
# 조기 종료 안내 · per-task 최종화 스코프 (D-092)
# ──────────────────────────────────────────────

def test_build_output_state_scopes_original_query_to_sub_query():
    """per-task 최종화 입력의 original_query는 전체 질의가 아니라 sub_query로 좁힌다(D-092).

    회귀 방지(2026-07-20 실측): 전체 질의 original_query가 새면 output_generator가
    하위 task 결과(알람 서버 목록 2행)만으로 전체 질문에 답한 듯 서술한다
    ("2026년 6월 CPU 사용률 평균이 가장 높았던 서버는 SV-WEB-001입니다" 환각).
    """
    state = create_initial_state(
        user_query="심각 알람 서버 중 6월 CPU 최고 서버의 제조사와 일련번호"
    )
    state["parsed_requirements"] = {
        "original_query": state["user_query"],
        "output_format": "text",
    }
    task = {"task_id": "t1", "agent": "alarm_query",
            "sub_query": "활성 심각 알람 서버 목록 조회", "order": 1}

    out = _build_output_state(state, task, {"organized_data": {"rows": []}})

    assert out["user_query"] == "활성 심각 알람 서버 목록 조회"
    assert out["parsed_requirements"]["original_query"] == "활성 심각 알람 서버 목록 조회"
    # 나머지 파싱 필드(output_format 등)는 유지
    assert out["parsed_requirements"]["output_format"] == "text"
    # 원본 state의 parsed_requirements는 변형하지 않는다(공유 dict 오염 방지)
    assert state["parsed_requirements"]["original_query"] == state["user_query"]


def test_build_output_state_falls_back_to_user_query_without_sub_query():
    """sub_query가 없으면 기존대로 전체 user_query를 사용한다(단일 task 경로 동작 불변)."""
    state = create_initial_state(user_query="전체 서버 조회")
    state["parsed_requirements"] = {"original_query": "전체 서버 조회"}
    task = {"task_id": "t1", "agent": "data_query", "order": 1}

    out = _build_output_state(state, task, {})

    assert out["user_query"] == "전체 서버 조회"
    assert out["parsed_requirements"]["original_query"] == "전체 서버 조회"


def test_apply_incomplete_notice_noop_without_flag():
    """안내문이 없으면(정상 완료·replanner 경로) 결과를 그대로 반환한다."""
    out = _apply_incomplete_notice({"final_response": "본문"}, {})
    assert out["final_response"] == "본문"


def test_apply_incomplete_notice_appends_to_body():
    """안내문이 있으면 본문 말미에 구분선과 함께 덧붙인다."""
    notice = "⚠️ 내부 처리가 중간에 중단되어 요청의 일부만 수행되었습니다."
    out = _apply_incomplete_notice(
        {"final_response": "알람 서버 2대입니다"},
        {"orchestration_incomplete_notice": notice},
    )
    assert out["final_response"].startswith("알람 서버 2대입니다")
    assert notice in out["final_response"]


@pytest.mark.asyncio
async def test_result_aggregator_appends_incomplete_notice(mock_config):
    """orchestration_incomplete_notice가 있으면 최종 응답 말미에 결정적으로 덧붙는다(D-092).

    딥 에이전트 경로에서 오케스트레이터 루프가 빈 응답으로 조기 종료되면(예: 알람
    조회만 수행되고 CPU 평균·제조사/일련번호 조회 미실행) 부분 결과임을 명시해야 한다.
    """
    notice = (
        "⚠️ 내부 처리가 중간에 중단되어 요청의 일부만 수행되었습니다.\n"
        "수행된 조회:\n- 활성 심각 알람 서버 목록 조회\n"
        "질문의 나머지 항목은 수행되지 않았습니다."
    )
    state = create_initial_state(user_query="심각 알람 서버 중 6월 CPU 최고 서버의 제조사와 일련번호")
    state["task_plan"] = [
        {"task_id": "t1", "agent": "alarm_query",
         "sub_query": "활성 심각 알람 서버 목록 조회", "order": 1, "status": "completed"},
    ]
    state["task_results"] = {
        "t1": {"organized_data": {"summary": "2건",
                                  "rows": [{"server_name": "SV-WEB-001"}],
                                  "is_sufficient": True}}
    }
    state["orchestration_incomplete_notice"] = notice

    with patch.object(
        agg_mod, "output_generator",
        new=AsyncMock(return_value={"final_response": "알람 서버 2대입니다",
                                    "output_file": None, "output_file_name": None}),
    ):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    assert out["final_response"].startswith("알람 서버 2대입니다")
    assert "수행되지 않았습니다" in out["final_response"]
    assert "활성 심각 알람 서버 목록 조회" in out["final_response"]
    # 답변 이력(messages)에도 안내문 포함 응답이 기록된다(다음 턴 근거 정합)
    assert "수행되지 않았습니다" in out["messages"][0].content


# ──────────────────────────────────────────────
# 프롬프트 항목 전체 병합 표시 (D-100)
# ──────────────────────────────────────────────

from src.orchestration.result_aggregator import (
    _find_identity_col,
    _merge_task_results_by_identity,
)


def _rows_task(tid, order, rows):
    return {"task_id": tid, "order": order}, {"organized_data": {"rows": rows}}


def test_find_identity_col_prefers_server_name():
    """식별 컬럼 탐지는 server_name을 우선하고 alarm_name은 고르지 않는다."""
    assert _find_identity_col([{"server_name": "s1", "alarm_name": "a", "severity": 3}]) == "server_name"
    assert _find_identity_col([{"name": "s1", "Vendor": "HPE"}]) == "name"
    assert _find_identity_col([{"count": 5}]) is None


def test_merge_scopes_to_smallest_source_ranking():
    """순위 1건 조회(base)의 서버만 남긴다 — '가장 높은 서버'가 대상 전체로 번지지 않음(D-100)."""
    alarm_rows = [
        {"server_name": "SV-WEB-001", "alarm_name": "CPU 임계", "severity": 3},
        {"server_name": "SV-BATCH-009", "alarm_name": "메모리 임계", "severity": 3},
    ]
    cpu_rows = [{"name": "SV-WEB-001", "Vendor": "HPE", "SerialNumber": "KR2024", "cpus_avg": 42.8}]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    results = {
        "t1": {"organized_data": {"rows": alarm_rows}},
        "t2": {"organized_data": {"rows": cpu_rows}},
    }
    merged = _merge_task_results_by_identity(tasks, results)
    assert len(merged) == 1
    row = merged[0]
    # 질의에 언급된 모든 항목이 한 행에
    assert row["server_name"] == "SV-WEB-001"
    assert row["alarm_name"] == "CPU 임계"
    assert row["severity"] == 3
    assert row["Vendor"] == "HPE"
    assert row["SerialNumber"] == "KR2024"
    assert row["cpus_avg"] == 42.8
    # name/server_name 식별 컬럼 중복 없음
    assert "name" not in row


def test_merge_two_servers_when_equal_cardinality():
    """동수 조회(각 2행)는 선별 서버 전체를 유지한다."""
    alarm = [{"server_name": "A", "severity": 3}, {"server_name": "B", "severity": 2}]
    cpu = [{"server_name": "A", "cpus_avg": 40}, {"server_name": "B", "cpus_avg": 20}]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    merged = _merge_task_results_by_identity(
        tasks, {"t1": {"organized_data": {"rows": alarm}}, "t2": {"organized_data": {"rows": cpu}}}
    )
    assert len(merged) == 2
    by_key = {r["server_name"]: r for r in merged}
    assert by_key["A"]["cpus_avg"] == 40 and by_key["A"]["severity"] == 3
    assert by_key["B"]["cpus_avg"] == 20 and by_key["B"]["severity"] == 2


def test_merge_none_when_no_common_identity():
    """식별 컬럼 없는 조회가 있으면 병합 취소(None) → LLM 합성 폴백."""
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    results = {
        "t1": {"organized_data": {"rows": [{"total_count": 5}]}},
        "t2": {"organized_data": {"rows": [{"server_name": "A", "Vendor": "HPE"}]}},
    }
    assert _merge_task_results_by_identity(tasks, results) is None


def test_merge_none_when_single_source():
    """행 있는 조회가 1개뿐이면 병합 대상 아님(None)."""
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    results = {
        "t1": {"organized_data": {"rows": [{"server_name": "A", "Vendor": "HPE"}]}},
        "t2": {"organized_data": {"rows": []}},
    }
    assert _merge_task_results_by_identity(tasks, results) is None


@pytest.mark.asyncio
async def test_result_aggregator_merges_and_shows_all_items(mock_config):
    """딥에이전트 복합 결과를 서버 키로 병합해 단일 표(모든 항목)로 최종화한다(D-100)."""
    tasks = [
        {"task_id": "t1", "agent": "alarm_query", "sub_query": "알람", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "CPU", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="심각 알람 서버 중 6월 CPU 최고 서버의 제조사와 일련번호")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"organized_data": {"rows": [{"server_name": "SV-WEB-001", "alarm_name": "CPU 임계", "severity": 3}], "is_sufficient": True}},
        "t2": {"organized_data": {"rows": [{"name": "SV-WEB-001", "Vendor": "HPE", "SerialNumber": "KR2024", "cpus_avg": 42.8}], "is_sufficient": True}},
    }

    captured = {}
    async def fake_og(s, **kw):
        captured["rows"] = (s.get("organized_data") or {}).get("rows")
        return {"final_response": "표", "output_file": None, "output_file_name": None}

    with patch.object(agg_mod, "output_generator", new=fake_og):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config, synthesize=True)

    # 병합된 단일 행이 output_generator로 전달됨(6개 항목 전부)
    assert len(captured["rows"]) == 1
    assert set(captured["rows"][0]) == {"server_name", "alarm_name", "severity", "Vendor", "SerialNumber", "cpus_avg"}
    # 병합 rows가 top-level query_results로 승격
    assert out["query_results"] == captured["rows"]


# ──────────────────────────────────────────────
# 존 역질문 턴당 1회 (G-3 확정 2026-09-16 · D-143 후속2 확대)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_존_역질문은_복합_계획에서도_턴당_1회로_묶인다(mock_config):
    """G-3 로 복합 계획에 게이트가 열리면서 task 마다 같은 질문이 나올 수 있게 됐다.

    `result_aggregator` 가 하나만 취해 턴당 1회로 묶고 나머지 task 결과를 버린다 —
    종전 주석의 "중간 task 역질문은 UX 어색" 우려를 여기서 해소한다.
    """
    question = "어느 존의 데이터를 조회할까요?"
    payload = {"kind": "zone_select", "question": question, "options": [{"db_id": "polestar_cm_gp"}]}
    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "alarm_query", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합 질의")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": question, "zone_clarification": payload, "source": []},
        "t2": {"final_response": question, "zone_clarification": payload, "source": []},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    assert out["zone_clarification"] == payload
    assert out["final_response"] == question          # 질문 2개가 이어붙지 않는다
    assert out["final_response"].count(question) == 1


@pytest.mark.asyncio
async def test_복합_경로가_존_역질문을_본문으로_삼키지_않는다(mock_config):
    """**두 줄만 지웠다면 여기서 깨진다.**

    `_merge_finalized`·`_synthesize_finalized` 는 `zone_clarification` 키를 옮기지 않는다.
    단락이 없으면 질문이 본문 텍스트로만 합쳐져 API 응답의 `clarification` 이 비고,
    사용자는 구조화 응답(존 선택)을 할 수 없다 — 침묵 강등이다.
    """
    payload = {"kind": "zone_select", "question": "어느 존?", "options": []}
    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합 질의")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"final_response": "어느 존?", "zone_clarification": payload, "source": []},
        "t2": {"organized_data": {"summary": "3대", "rows": [{"hostname": "web-01"}], "is_sufficient": True}},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    assert out["zone_clarification"] == payload
    assert out["query_results"] == []                  # 부분 결과를 답변으로 내지 않는다
    assert "3대" not in out["final_response"]


@pytest.mark.asyncio
async def test_존_역질문_턴은_DB_승계를_오염시키지_않는다(mock_config):
    """임의 분류 결과가 `previous_db_ids` 로 새면 다음 턴 승계가 틀어진다(요청 스코프 원칙)."""
    payload = {"kind": "zone_select", "question": "어느 존?", "options": []}
    tasks = [
        {"task_id": "t1", "agent": "data_query", "sub_query": "q1", "order": 1, "status": "completed"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "q2", "order": 2, "status": "completed"},
    ]
    state = create_initial_state(user_query="복합 질의")
    state["task_plan"] = tasks
    state["task_results"] = {
        # 역질문 task 는 target_db_ids 를 남기지 않지만, **다른 task 는 남긴다** —
        # 복합 경로에서 그것이 승격되면 안 된다.
        "t1": {"final_response": "어느 존?", "zone_clarification": payload, "source": []},
        "t2": {"target_db_ids": ["polestar_b0", "polestar_cm_gp"], "db_origin": "classified"},
    }

    out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)

    assert "active_db_id" not in out
    assert "target_databases" not in out
    assert "db_scope_source" not in out


# ──────────────────────────────────────────────
# 병합 키 정규화 · 겹침 판정 (plans/49 §12.3 B-1/B-1a)
# ──────────────────────────────────────────────

def test_merge_normalizes_identity_key_forms():
    """표기형이 다른 식별 값(`SV-WEB-001` ↔ `svweb001`)을 같은 서버로 묶는다(B-1a).

    실측(2026-09-21 E3): 알람 조회는 `server_name='SV-WEB-001'`, 후속 조회는
    `hostname='svweb001'`을 돌려준다. 원시 값으로 키를 잡으면 두 조회가 갈라져
    후속 조회 행이 통째로 탈락하고 OSType이 공란이 됐다.
    """
    alarm = [
        {"server_name": "SV-WEB-001", "alarm_name": "CPU 임계", "severity": 3},
        {"server_name": "SV-BATCH-009", "alarm_name": "CPU 임계", "severity": 3},
    ]
    osq = [
        {"hostname": "svweb001", "ipaddress": "10.61.0.1", "OSType": "LINUX"},
        {"hostname": "svbatch009", "ipaddress": "10.61.0.4", "OSType": "AIX"},
    ]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    merged = _merge_task_results_by_identity(
        tasks,
        {"t1": {"organized_data": {"rows": alarm}}, "t2": {"organized_data": {"rows": osq}}},
    )
    assert merged is not None and len(merged) == 2
    by_key = {r["server_name"]: r for r in merged}
    # 표시 값은 선행 조회 표기형을 유지한다
    assert set(by_key) == {"SV-WEB-001", "SV-BATCH-009"}
    assert by_key["SV-WEB-001"]["OSType"] == "LINUX"
    assert by_key["SV-WEB-001"]["ipaddress"] == "10.61.0.1"
    assert by_key["SV-BATCH-009"]["OSType"] == "AIX"


def test_merge_none_when_sources_share_no_server():
    """공통 서버가 하나도 없는 독립 조회는 결정적 병합을 포기한다 → LLM 합성 폴백(B-1).

    실측(2026-09-17 E2 계열): CPU 상위 3 + 메모리 상위 3처럼 서로 다른 서버를 가리키는
    독립 조회를 병합하면 base 절단으로 한쪽이 통째로 사라졌다.
    """
    cpu = [{"name": "web-01", "cpu": 90}, {"name": "web-02", "cpu": 80},
           {"name": "web-03", "cpu": 70}]
    mem = [{"name": "db-01", "mem": 95}, {"name": "db-02", "mem": 85},
           {"name": "db-03", "mem": 75}]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    assert _merge_task_results_by_identity(
        tasks, {"t1": {"organized_data": {"rows": cpu}}, "t2": {"organized_data": {"rows": mem}}}
    ) is None


def test_merge_keeps_servers_no_other_source_covers():
    """일부만 겹치는 독립 조회는 절단하지 않는다 — 어느 조회에도 없는 서버를 지우지 않는다.

    절단(D-100 좁히기)은 base 집합이 다른 조회들의 집합 안에 온전히 들어갈 때만 뜻이 있다.
    """
    cpu = [{"name": "web-01", "cpu": 90}, {"name": "web-02", "cpu": 80},
           {"name": "shared-01", "cpu": 70}]
    mem = [{"name": "shared-01", "mem": 95}, {"name": "db-02", "mem": 85},
           {"name": "db-03", "mem": 75}]
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    merged = _merge_task_results_by_identity(
        tasks, {"t1": {"organized_data": {"rows": cpu}}, "t2": {"organized_data": {"rows": mem}}}
    )
    assert merged is not None
    assert {r["name"] for r in merged} == {"web-01", "web-02", "shared-01", "db-02", "db-03"}
