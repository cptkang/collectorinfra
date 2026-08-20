"""intent_planner 노드 단위 테스트 (Plan 48 §9).

검증 대상:
- 단일/복합/폴백 분해 (test_intent_planner_single/composite/fallback)
- 계층 A pre-check 3종 (test_pre_route_pending, R-10/§4.9.1)
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.orchestration.intent_planner import intent_planner
from src.state import create_initial_state


def _mock_llm(content: str) -> AsyncMock:
    """ainvoke가 주어진 content를 가진 응답을 반환하는 mock LLM을 만든다.

    코드가 isinstance(llm, KBGenAIChat)로 분기하지만 AsyncMock은 KBGenAIChat이
    아니므로 AIMessage 삽입 분기를 타지 않는다(정상). ainvoke가 호출되고
    response.content가 extract_json_from_response로 파싱된다.
    """
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=content)
    return llm


@pytest.mark.asyncio
async def test_intent_planner_single(mock_config):
    """단일 작업 질의 → LLM이 task 1개 반환 → task_plan 길이 1, is_composite False."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",
                    "sub_query": "CPU 사용률 80% 이상 서버 조회",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="CPU 사용률 80% 이상 서버 조회")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["is_composite"] is False
    assert result["task_plan"][0]["agent"] == "data_query"
    assert result["task_plan"][0]["status"] == "pending"
    assert result["current_node"] == "intent_planner"
    llm.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_intent_planner_composite(mock_config):
    """복합 질의 → LLM이 2개 task(분류·depends_on) 반환 → 길이 2, is_composite True."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",
                    "sub_query": "장애 서버 목록 조회",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                },
                {
                    "task_id": "t2",
                    "agent": "data_query",
                    "sub_query": "해당 서버의 CPU 사용률 조회",
                    "depends_on": ["t1"],
                    "input_from": ["t1"],
                    "order": 2,
                },
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="장애 서버와 그 CPU 사용률을 보여줘")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 2
    assert result["is_composite"] is True
    # 각 task가 status="pending"이고 필수 키를 보유
    for t in result["task_plan"]:
        assert t["status"] == "pending"
        assert "depends_on" in t
        assert "input_from" in t
        assert "order" in t
    # 의존성·분류 정확
    assert result["task_plan"][0]["task_id"] == "t1"
    assert result["task_plan"][1]["depends_on"] == ["t1"]
    assert result["task_plan"][1]["input_from"] == ["t1"]


@pytest.mark.asyncio
async def test_intent_planner_fallback_invalid_json(mock_config):
    """LLM이 비JSON 반환 → 단일 data_query task 폴백."""
    llm = _mock_llm("비JSON 응답입니다 그냥 텍스트")
    state = create_initial_state(user_query="아무 질의")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    t = result["task_plan"][0]
    assert t["task_id"] == "t1"
    assert t["agent"] == "data_query"
    assert t["status"] == "pending"
    assert result["is_composite"] is False


@pytest.mark.asyncio
async def test_intent_planner_fallback_exception(mock_config):
    """ainvoke가 예외 → 단일 data_query task 폴백."""
    llm = AsyncMock()
    llm.ainvoke.side_effect = RuntimeError("LLM 호출 실패")
    state = create_initial_state(user_query="아무 질의")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["task_plan"][0]["agent"] == "data_query"
    assert result["task_plan"][0]["status"] == "pending"
    assert result["is_composite"] is False


@pytest.mark.asyncio
async def test_intent_planner_fallback_empty_tasks(mock_config):
    """LLM이 빈 tasks 배열 반환 → 단일 data_query task 폴백."""
    llm = _mock_llm(json.dumps({"tasks": []}))
    state = create_initial_state(user_query="아무 질의")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["task_plan"][0]["agent"] == "data_query"


@pytest.mark.asyncio
async def test_process_intent_coerced_from_data_query(mock_config):
    """시간성 신호 없는 '프로세스 조회'를 LLM이 data_query로 분류해도 process_query로 교정(D-046)."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",  # LLM 보수적 오분류
                    "sub_query": "김포 ### 서버에 대한 프로세스 조회",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="김포 ### 서버에 대한 프로세스 조회")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "process_query"


@pytest.mark.asyncio
async def test_alarm_intent_coerced_from_data_query(mock_config):
    """알람/이벤트(event) 질의를 LLM이 data_query로 분류해도 alarm_query로 교정(D-076 후속3)."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",  # LLM 보수적 오분류 (bare "event" 실측 사례)
                    "sub_query": "최근 event가 발생한 서버와 서버별 이벤트 내용을 5건 보여줘.",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="최근 event가 발생한 서버와 서버별 이벤트 내용을 5건 보여줘.")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "alarm_query"


@pytest.mark.asyncio
async def test_non_alarm_query_stays_data_query(mock_config):
    """알람 신호가 없는 일반 조회(CPU 사용률)는 data_query를 유지한다(과잉 교정 방지)."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",
                    "sub_query": "전체 서버의 CPU 사용률 현황을 알려줘",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="전체 서버의 CPU 사용률 현황을 알려줘")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "data_query"


@pytest.mark.asyncio
async def test_process_history_stays_data_query(mock_config):
    """'프로세스 이력' 등 과거/이력 신호가 있으면 data_query 유지(교정 제외)."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",
                    "sub_query": "김포 ### 서버의 지난 7일 프로세스 이력 추세 조회",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="김포 ### 서버의 지난 7일 프로세스 이력 추세 조회")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "data_query"


@pytest.mark.asyncio
async def test_process_intent_coerced_on_fallback(mock_config):
    """LLM 분해 실패로 data_query 폴백돼도 프로세스 질의면 process_query로 교정."""
    llm = _mock_llm("비JSON 응답")  # 폴백 → 단일 data_query(user_query)
    state = create_initial_state(user_query="### 서버 현재 프로세스 리스트 보여줘")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "process_query"


@pytest.mark.asyncio
async def test_pre_route_pending_synonym_reuse(mock_config):
    """(a) pending_synonym_reuse 감지 → cache_management 단일 task, LLM 미호출."""
    llm = AsyncMock()
    state = create_initial_state(user_query="유사어 재활용")
    state["pending_synonym_reuse"] = {
        "target_column": "server_name",
        "suggestions": [{"column": "hostname", "words": ["서버명"], "description": ""}],
    }

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["task_plan"][0]["agent"] == "cache_management"
    assert result["task_plan"][0]["status"] == "pending"
    assert result["is_composite"] is False
    # 계층 A pre-check는 LLM 분해를 스킵해야 함
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_pre_route_pending_synonym_registration(mock_config):
    """(b) synonym_registration 요청 → synonym_registration 단일 task, LLM 미호출."""
    llm = AsyncMock()
    state = create_initial_state(user_query="유사어 등록해줘")
    state["parsed_requirements"] = {"synonym_registration": {"field": "서버명"}}
    state["pending_synonym_registrations"] = [
        {"index": 0, "field": "서버명", "column": "hostname", "db_id": "polestar"}
    ]

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["task_plan"][0]["agent"] == "synonym_registration"
    assert result["task_plan"][0]["status"] == "pending"
    assert result["is_composite"] is False
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_pre_route_pending_mapped_db_ids(mock_config):
    """(c) mapped_db_ids 감지 → data_query 단일 task에 db_ids 포함, LLM 미호출."""
    llm = AsyncMock()
    state = create_initial_state(user_query="양식 업로드 조회")
    state["mapped_db_ids"] = ["polestar"]

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    t = result["task_plan"][0]
    assert t["agent"] == "data_query"
    assert t["db_ids"] == ["polestar"]
    assert t["status"] == "pending"
    assert result["is_composite"] is False
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_pre_route_template_structure_single_task(mock_config):
    """(d) ③.5 양식 업로드(template_structure) → data_query 단일 task 고정, LLM 미호출.

    라이브 실측(2026-07-30 B0): LLM 분해가 폼필을 서버정보/월지표 2개 task로 쪼개
    병합 결과가 2배 행이 됐다(D-147).
    """
    llm = AsyncMock()
    state = create_initial_state(user_query="양식 채워줘")
    state["template_structure"] = {"sheets": [{"name": "Sheet1"}]}

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    assert result["task_plan"][0]["agent"] == "data_query"
    assert result["is_composite"] is False
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_pre_route_form_fill_without_file_guidance(mock_config):
    """(e) ③.6 파일 없는 '양식 채워줘' → general_inference 안내 단락, LLM 미호출.

    template_structure 없이 LLM 분해로 가면 data_query가 존재하지 않는 양식을
    환각 처리한다(라이브 실측 2026-07-30 7차, D-147).
    """
    llm = AsyncMock()
    state = create_initial_state(user_query="금감원 양식 채워줘")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert len(result["task_plan"]) == 1
    t = result["task_plan"][0]
    assert t["agent"] == "general_inference"
    # 고정 안내문은 LLM을 통과시키지 않고 direct_response로 결정적 반환
    # (라이브 실측 2026-07-30: LLM 재서술 경유 시 호출 실패 → 일반 오류 문구 강등)
    assert "첨부" in t["direct_response"]
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_direct_response_skips_llm():
    """direct_response가 있는 task는 general_inference LLM을 호출하지 않는다(D-147)."""
    from src.orchestration.subagents import run_general_inference

    task = {"task_id": "t1", "agent": "general_inference", "direct_response": "양식 파일을 첨부해 주세요."}
    result = await run_general_inference(task, {}, llm=None, app_config=None)

    assert result["final_response"] == "양식 파일을 첨부해 주세요."
    assert result["routing_intent"] == "general_inference"


@pytest.mark.asyncio
async def test_form_noun_without_fill_verb_goes_llm(mock_config):
    """양식 명사만으로는 단락하지 않는다(채움 동사 필요) — LLM 분해 유지(오발동 방지)."""
    content = json.dumps(
        {
            "tasks": [
                {
                    "task_id": "t1",
                    "agent": "data_query",
                    "sub_query": "양식 관련 서버 조회",
                    "depends_on": [],
                    "input_from": [],
                    "order": 1,
                }
            ]
        },
        ensure_ascii=False,
    )
    llm = _mock_llm(content)
    state = create_initial_state(user_query="양식 관련 서버 조회")

    result = await intent_planner(state, llm=llm, app_config=mock_config)

    assert result["task_plan"][0]["agent"] == "data_query"
    llm.ainvoke.assert_awaited_once()
