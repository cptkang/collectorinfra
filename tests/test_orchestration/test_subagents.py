"""subagents 모듈 단위 테스트 (Plan 48 §9).

검증 대상:
- SUBAGENT_REGISTRY / fallback (test_subagent_registry, S1·S5)
- 격리 입력 컨텍스트 (test_isolated_input, S3)
- data_query 단일/멀티 분기 + 재시도 보존 (test_data_query_single_vs_multi, R-09)
- input_from 식별 키·행수 상한 (test_input_from_size_limit, R-12)
"""

import pytest
from unittest.mock import AsyncMock, patch

from src.orchestration import subagents
from src.orchestration.agent_orchestrator import _fallback_spec, _run_agent
from src.orchestration.subagents import (
    SUBAGENT_REGISTRY,
    SubAgentSpec,
    _extract_identity_rows,
    _make_isolated_input,
    run_data_query_pipeline,
)
from src.state import create_initial_state


# ──────────────────────────────────────────────
# test_subagent_registry (S1·S5)
# ──────────────────────────────────────────────

def test_subagent_registry_has_expected_agents():
    """SUBAGENT_REGISTRY에 기대 agent가 존재하고 각 handler가 callable이다.

    Plan 50 M4에서 process_query(실시간 프로세스 API)가 추가되었다.
    """
    expected = {
        "data_query",
        "process_query",
        "alarm_query",
        "cache_management",
        "synonym_registration",
        "general_inference",
    }
    assert set(SUBAGENT_REGISTRY.keys()) == expected
    for name, spec in SUBAGENT_REGISTRY.items():
        assert isinstance(spec, SubAgentSpec)
        assert callable(spec.handler), f"{name} handler가 callable이 아님"


def test_general_inference_is_fallback():
    """general_inference만 fallback=True이고, _fallback_spec()이 이를 반환한다."""
    assert SUBAGENT_REGISTRY["general_inference"].fallback is True
    fb = _fallback_spec()
    assert fb.name == "general_inference"
    assert fb.fallback is True
    # 나머지는 fallback=False
    for name, spec in SUBAGENT_REGISTRY.items():
        if name != "general_inference":
            assert spec.fallback is False


@pytest.mark.asyncio
async def test_unknown_agent_delegates_to_fallback(mock_config):
    """미지정 agent("unknown") → _run_agent가 fallback(general_inference) handler로 위임한다."""
    task = {
        "task_id": "t1",
        "agent": "unknown",
        "sub_query": "안녕하세요",
        "depends_on": [],
        "input_from": [],
        "order": 1,
        "status": "in_progress",
    }
    state = create_initial_state(user_query="안녕하세요")
    llm = AsyncMock()

    # SubAgentSpec는 frozen이므로 handler 속성 직접 패치는 불가.
    # registry 엔트리 전체를 mock handler를 가진 새 spec으로 교체한다.
    fb_handler = AsyncMock(return_value={"final_response": "fallback 응답"})
    fake_spec = SubAgentSpec(
        "general_inference", "fallback", fb_handler, fallback=True
    )
    with patch.dict(SUBAGENT_REGISTRY, {"general_inference": fake_spec}):
        # _fallback_spec은 registry.values()를 순회하므로 교체된 spec이 반영됨
        result = await _run_agent(task, state, llm, mock_config, prior={})

    fb_handler.assert_awaited_once()
    assert result == {"final_response": "fallback 응답"}


# ──────────────────────────────────────────────
# test_isolated_input (S3)
# ──────────────────────────────────────────────

def test_isolated_input_filters_large_fields():
    """_make_isolated_input이 대형 누적 필드를 빈 값으로 초기화하고 필요 필드만 전달한다."""
    state = create_initial_state(user_query="원본 질의", user_id="kim")
    # 원본 state에 대형 누적 데이터 주입
    state["query_results"] = [{"hostname": f"srv-{i}"} for i in range(5000)]
    state["db_results"] = {"polestar": [{"x": 1}] * 1000}
    state["parsed_requirements"] = {"query_targets": ["서버"]}

    task = {
        "task_id": "t1",
        "agent": "data_query",
        "sub_query": "서브 질의",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }

    isolated = _make_isolated_input(task, state, prior={})

    # 대형 누적 필드는 빈 값으로 초기화됨 (원본 데이터 격리)
    assert isolated["query_results"] == []
    assert isolated["db_results"] == {}
    assert isolated["messages"] == []
    assert isolated["retry_count"] == 0
    # 필요 필드는 전달됨 (original_query는 sub-task 스코프로 교체 — D-094)
    assert isolated["user_id"] == "kim"
    assert isolated["parsed_requirements"] == {
        "query_targets": ["서버"], "original_query": "서브 질의",
    }
    # 전체 AgentState가 아니라 필터된 dict (원본 5000행이 들어가지 않음)
    assert len(isolated["query_results"]) == 0


def test_isolated_input_scopes_original_query_to_sub_query():
    """parsed_requirements["original_query"]는 전체 질의가 아니라 task sub_query로 좁힌다(D-094).

    회귀 방지(2026-07-20 라이브 실측): 전체 질의가 SQL 생성 프롬프트의 "## 사용자 질의"로
    새면 sub_query의 제약(선행 결과 서버명 한정)이 아니라 전체 질문에 대한 SQL이 생성되고,
    data_query는 알람 테이블 접근이 없어 "알람 서버 중" 조건이 침묵 탈락한다(전 서버 오답).
    """
    full_q = "심각 알람 서버 중 6월 CPU 최고 서버의 제조사와 일련번호"
    state = create_initial_state(user_query=full_q)
    state["parsed_requirements"] = {
        "original_query": full_q,
        "time_range": {"start": "2026-06"},
        "query_targets": ["서버"],
    }
    task = {"task_id": "t2", "agent": "data_query",
            "sub_query": 'SV-WEB-001, SV-BATCH-009 서버의 제조사와 일련번호 조회',
            "depends_on": [], "input_from": [], "order": 2}

    isolated = _make_isolated_input(task, state, prior={})

    assert isolated["parsed_requirements"]["original_query"] == task["sub_query"]
    # 구조화 필드(기간 등)는 보조 맥락으로 유지
    assert isolated["parsed_requirements"]["time_range"] == {"start": "2026-06"}
    # 원본 state는 비오염
    assert state["parsed_requirements"]["original_query"] == full_q


def test_isolated_input_propagates_form_fill_fields():
    """양식 채우기 필드(uploaded_file/template_structure)가 격리 state로 전파된다.

    회귀 방지: uploaded_file을 빠뜨리면 output_generator가 원본 파일 없음으로
    CSV 강등된다(비대칭 전파, D-053 계열).
    """
    state = create_initial_state(user_query="양식 채워줘", user_id="kim")
    state["uploaded_file"] = b"PK\x03\x04fake-xlsx-bytes"
    state["template_structure"] = {"sheets": [{"name": "Sheet1", "header_cells": []}]}
    state["file_type"] = "xlsx"

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "조회",
            "depends_on": [], "input_from": [], "order": 1}

    isolated = _make_isolated_input(task, state, prior={})

    assert isolated["uploaded_file"] == b"PK\x03\x04fake-xlsx-bytes"
    assert isolated["template_structure"] == state["template_structure"]
    assert isolated["file_type"] == "xlsx"


# ──────────────────────────────────────────────
# test_data_query_single_vs_multi (R-09, §4.9.3)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_data_query_single_db_path(mock_config):
    """단일 DB target → _run_single_db_pipeline 경로 (multi_db_executor 미호출)."""
    task = {
        "task_id": "t1",
        "agent": "data_query",
        "sub_query": "서버 조회",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }
    isolated = {"user_query": "서버 조회", "parsed_requirements": {}}
    llm = AsyncMock()

    single_targets = [
        {"db_id": "polestar", "relevance_score": 1.0, "sub_query_context": "서버 조회",
         "user_specified": False, "reason": "단일"}
    ]

    with patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=single_targets)), \
         patch.object(subagents, "_run_single_db_pipeline",
                      new=AsyncMock(return_value={"query_results": [{"hostname": "web-01"}]})) as single, \
         patch.object(subagents, "multi_db_executor", new=AsyncMock()) as multi, \
         patch.object(subagents, "result_merger", new=AsyncMock()) as merger, \
         patch.object(subagents, "result_organizer",
                      new=AsyncMock(return_value={"organized_data": {"rows": [{"hostname": "web-01"}]}})):
        result = await run_data_query_pipeline(task, isolated, llm=llm, app_config=mock_config)

    single.assert_awaited_once()
    multi.assert_not_called()
    merger.assert_not_called()
    assert result["organized_data"]["rows"] == [{"hostname": "web-01"}]


@pytest.mark.asyncio
async def test_data_query_multi_db_path(mock_config):
    """멀티 DB(2개) target → multi_db_executor + result_merger 경로."""
    task = {
        "task_id": "t1",
        "agent": "data_query",
        "sub_query": "전체 DB 조회",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }
    isolated = {"user_query": "전체 DB 조회", "parsed_requirements": {}}
    llm = AsyncMock()

    multi_targets = [
        {"db_id": "polestar", "relevance_score": 0.9, "sub_query_context": "조회",
         "user_specified": False, "reason": "1"},
        {"db_id": "polestar2", "relevance_score": 0.8, "sub_query_context": "조회",
         "user_specified": False, "reason": "2"},
    ]

    with patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=multi_targets)), \
         patch.object(subagents, "_run_single_db_pipeline", new=AsyncMock()) as single, \
         patch.object(subagents, "multi_db_executor",
                      new=AsyncMock(return_value={"db_results": {"polestar": []}})) as multi, \
         patch.object(subagents, "result_merger",
                      new=AsyncMock(return_value={"query_results": []})) as merger, \
         patch.object(subagents, "result_organizer",
                      new=AsyncMock(return_value={"organized_data": {"rows": []}})):
        await run_data_query_pipeline(task, isolated, llm=llm, app_config=mock_config)

    multi.assert_awaited_once()
    merger.assert_awaited_once()
    single.assert_not_called()


@pytest.mark.asyncio
async def test_single_db_pipeline_retry_loop(mock_config):
    """_run_single_db_pipeline: 검증 실패(passed=False)+retry<3이면 query_generator 재호출.

    각 노드를 AsyncMock으로 대체하고, query_generator가 retry_count를 증가시키는 것을
    시뮬레이션한다. 첫 검증은 실패, 두 번째는 성공으로 두어 재시도 1회 후 실행 진입을 검증한다.
    핵심: 단일 분기가 풀 검증·재시도 루프를 보존함.
    """
    # query_generator가 호출될 때마다 retry_count 증가를 시뮬레이션
    gen_calls = {"n": 0}

    async def fake_query_generator(state, **kwargs):
        gen_calls["n"] += 1
        # 첫 호출은 retry_count 0 유지(최초 생성), 재시도 호출부터 증가
        return {"generated_sql": "SELECT 1", "retry_count": gen_calls["n"] - 1}

    # 첫 검증 실패, 두 번째 검증 성공
    validate_calls = {"n": 0}

    async def fake_query_validator(state, **kwargs):
        validate_calls["n"] += 1
        passed = validate_calls["n"] >= 2
        return {"validation_result": {"passed": passed, "reason": "", "auto_fixed_sql": None}}

    with patch.object(subagents, "schema_analyzer",
                      new=AsyncMock(return_value={"schema_info": {"tables": {}}})), \
         patch.object(subagents, "query_generator", new=AsyncMock(side_effect=fake_query_generator)), \
         patch.object(subagents, "query_validator", new=AsyncMock(side_effect=fake_query_validator)), \
         patch.object(subagents, "query_executor",
                      new=AsyncMock(return_value={"query_results": [{"x": 1}], "error_message": None})) as executor:
        out = await subagents._run_single_db_pipeline(
            {"user_query": "q", "retry_count": 0}, AsyncMock(), mock_config
        )

    # query_generator가 2회 호출됨(최초 + 재시도 1회)
    assert gen_calls["n"] == 2
    # 검증이 2회 호출되고 최종 통과
    assert validate_calls["n"] == 2
    # 검증 통과 후 executor가 1회 실행됨
    executor.assert_awaited_once()
    assert out["query_results"] == [{"x": 1}]


# ──────────────────────────────────────────────
# test_input_from_size_limit (R-12)
# ──────────────────────────────────────────────

def test_extract_identity_rows_keeps_only_key_columns():
    """식별 키 컬럼(hostname 등)이 있으면 해당 컬럼만 추출한다."""
    rows = [
        {"hostname": "web-01", "ip_address": "10.0.0.1", "cpu": 85.3, "mem": 70.1},
        {"hostname": "web-02", "ip_address": "10.0.0.2", "cpu": 90.0, "mem": 60.0},
    ]
    extracted = _extract_identity_rows(rows)

    # hostname/ip_address(name 포함 아님)만? hint는 hostname/host_name/name/server_name/id
    # → "hostname" 매칭. ip_address는 매칭 안 됨, cpu/mem도 매칭 안 됨.
    assert all(set(r.keys()) == {"hostname"} for r in extracted)
    assert extracted[0]["hostname"] == "web-01"


def test_extract_identity_rows_limits_to_100():
    """행수가 100을 초과하면 100행으로 제한한다."""
    rows = [{"hostname": f"srv-{i}"} for i in range(250)]
    extracted = _extract_identity_rows(rows)
    assert len(extracted) == 100


def test_extract_identity_rows_no_key_keeps_all_columns():
    """식별 키 컬럼이 없으면 전체 컬럼을 유지하되 행수 상한만 적용한다."""
    rows = [{"cpu": 85.0, "mem": 70.0}, {"cpu": 90.0, "mem": 60.0}]
    extracted = _extract_identity_rows(rows)
    assert extracted[0] == {"cpu": 85.0, "mem": 70.0}


def test_isolated_input_sets_routing_intent_from_task_agent():
    """alarm_query task는 routing_intent=alarm_query로 격리 입력에 매핑된다(D-076 후속3).

    orchestration 경로는 semantic_router를 타지 않아 routing_intent가 항상 None이었고,
    alarm_query task도 allowed_tables 필터(알람 테이블 제외)·일반 템플릿으로 실행되던 결함 회귀 방지.
    """
    state = create_initial_state(user_query="최근 event가 발생한 서버")
    alarm_task = {
        "task_id": "t1", "agent": "alarm_query", "sub_query": "최근 event 조회",
        "depends_on": [], "input_from": [], "order": 1,
    }
    data_task = {
        "task_id": "t2", "agent": "data_query", "sub_query": "서버 목록",
        "depends_on": [], "input_from": [], "order": 2,
    }

    assert _make_isolated_input(alarm_task, state, prior={})["routing_intent"] == "alarm_query"
    assert _make_isolated_input(data_task, state, prior={})["routing_intent"] is None


def test_make_isolated_input_injects_prior_rows_with_limit():
    """input_from + prior → base["prior_rows"]에 식별 키·행수 상한이 적용된다."""
    task = {
        "task_id": "t2",
        "agent": "data_query",
        "sub_query": "후속 질의",
        "depends_on": ["t1"],
        "input_from": ["t1"],
        "order": 2,
    }
    state = create_initial_state(user_query="원본")
    # 선행 task t1이 식별 키 + 비식별 컬럼이 섞인 많은 행을 반환
    prior = {
        "t1": {
            "query_results": [
                {"hostname": f"srv-{i}", "cpu": float(i)} for i in range(300)
            ]
        }
    }

    isolated = _make_isolated_input(task, state, prior)

    assert "prior_rows" in isolated
    assert "t1" in isolated["prior_rows"]
    rows = isolated["prior_rows"]["t1"]
    # 행수 상한 100 적용
    assert len(rows) == 100
    # 식별 키 컬럼(hostname)만 유지 (cpu 제거)
    assert all(set(r.keys()) == {"hostname"} for r in rows)


# ──────────────────────────────────────────────
# test_routing_signal_preservation (§4.9.6, R-14)
#   위치→DB 등 라우팅 신호가 planner→classify_dbs 단계에서 보존되는지 검증
# ──────────────────────────────────────────────

def test_planner_prompt_has_db_signal_preservation_rule():
    """planner 프롬프트에 DB 식별 신호 보존 규칙과 위치 예시가 포함된다 (§4.9.6)."""
    from src.prompts.intent_planner import INTENT_PLANNER_SYSTEM_TEMPLATE

    tmpl = INTENT_PLANNER_SYSTEM_TEMPLATE
    assert "DB 식별 신호" in tmpl
    # 위치 신호(김포/여의도)가 규칙/예시에 명시되어 누락 방지
    assert "김포" in tmpl
    assert "여의도" in tmpl


@pytest.mark.asyncio
async def test_classify_dbs_passes_db_descriptions(mock_config):
    """classify_dbs가 Redis 캐시 db_descriptions를 _llm_classify에 전달한다 (§4.9.6).

    원래 semantic_router는 db_descriptions를 라우팅 프롬프트에 주입했으나
    Phase 1 초기 구현에서 누락되었던 것을 복원한 회귀 테스트.
    """
    from src.config import MultiDBConfig

    mock_config.multi_db = MultiDBConfig(
        active_db_ids_csv="polestar,polestar_cm_gp,polestar_cm_yd"
    )

    fake_classify = AsyncMock(return_value={
        "intent": "data_query",
        "databases": [
            {"db_id": "polestar_cm_gp", "relevance_score": 0.95,
             "sub_query_context": "CPU 사용률 높은 서버 조회",
             "user_specified": False, "reason": "김포 → polestar_cm_gp"}
        ],
    })
    fake_cache_mgr = AsyncMock()
    fake_cache_mgr.get_db_descriptions = AsyncMock(
        return_value={"polestar_cm_gp": "K리전 공동존 김포 운영 폴스타"}
    )

    with patch.object(subagents, "_llm_classify", new=fake_classify), \
         patch("src.schema_cache.cache_manager.get_cache_manager",
               return_value=fake_cache_mgr):
        targets = await subagents.classify_dbs(
            AsyncMock(), "김포 폴스타에서 CPU 사용률 높은 서버", mock_config
        )

    # _llm_classify가 db_descriptions kwarg로 캐시 설명을 전달받았는지
    _, kwargs = fake_classify.call_args
    assert kwargs.get("db_descriptions") == {"polestar_cm_gp": "K리전 공동존 김포 운영 폴스타"}
    # 김포 → polestar_cm_gp 선택 확인
    assert targets[0]["db_id"] == "polestar_cm_gp"


@pytest.mark.asyncio
async def test_run_data_query_single_uses_sub_query_context(mock_config):
    """단일 DB: SQL 생성 입력(user_query)이 정제 질의(sub_query_context)로 설정된다 (§4.9.6 디멘전 7).

    위치 신호("여의도")가 SQL WHERE로 누출되지 않도록, 위치가 제거된 sub_query_context를
    _run_single_db_pipeline의 user_query로 사용하는지 검증한다.
    """
    task = {
        "task_id": "t1",
        "agent": "data_query",
        "sub_query": "여의도 개발 폴스타에서 CPU 높은 서버 조회",
        "depends_on": [],
        "input_from": [],
        "order": 1,
    }
    isolated = {"user_query": task["sub_query"], "parsed_requirements": {}}

    # classify_dbs가 위치 제거된 sub_query_context를 가진 단일 target 반환
    single_targets = [
        {"db_id": "polestar_cm_yd", "relevance_score": 1.0,
         "sub_query_context": "CPU 사용률 높은 서버 조회",  # "여의도" 제거됨
         "user_specified": False, "reason": "여의도 → polestar_cm_yd"}
    ]

    captured: dict = {}

    async def fake_single(s, llm, app_config):
        captured["user_query"] = s["user_query"]
        return {"query_results": []}

    with patch.object(subagents, "classify_dbs", new=AsyncMock(return_value=single_targets)), \
         patch.object(subagents, "_run_single_db_pipeline", new=AsyncMock(side_effect=fake_single)), \
         patch.object(subagents, "result_organizer",
                      new=AsyncMock(return_value={"organized_data": {"rows": []}})):
        await run_data_query_pipeline(task, isolated, llm=AsyncMock(), app_config=mock_config)

    # 단일 DB SQL 생성 입력 = 정제 질의 (위치 미포함)
    assert captured["user_query"] == "CPU 사용률 높은 서버 조회"
    assert "여의도" not in captured["user_query"]
