"""턴 내 동일 sub_query 메모이제이션 단위 테스트.

deep agent 재개 루프(D-093)에서 오케스트레이터가 같은 조회를 다시 호출하면 워커
파이프라인(LLM 2~8회)이 통째로 재실행되던 문제의 회귀 방지. collector(요청 스코프)를
메모 저장소로 재사용하므로 요청 경계를 넘으면 메모가 자연 초기화된다.
LLM 실호출 없음(fake handler).
"""

import logging

import pytest

from src.orchestration.deepagents_tools import (
    _find_memoized_result,
    _normalize_sub_query,
    _run_subagent_tool,
)
from src.orchestration.subagents import SUBAGENT_REGISTRY, SubAgentSpec


def _install_handler(monkeypatch, agent_name: str, calls: list, results):
    """호출 횟수를 기록하는 fake handler를 registry에 설치한다.

    results: 호출 순서대로 반환할 dict 목록(소진 후 마지막 값 반복).
    """
    async def fake_handler(task, isolated, *, llm, app_config):
        calls.append(isolated.get("user_query"))
        idx = min(len(calls) - 1, len(results) - 1)
        return results[idx]

    monkeypatch.setitem(
        SUBAGENT_REGISTRY, agent_name,
        SubAgentSpec(agent_name, "테스트 핸들러", fake_handler),
    )


def _ok_result(rows=None):
    """조회 성공 형태의 handler 결과를 만든다."""
    return {
        "organized_data": {
            "summary": "1건",
            "rows": rows if rows is not None else [{"hostname": "web-01"}],
            "is_sufficient": True,
        }
    }


# ──────────────────────────────────────────────
# _normalize_sub_query
# ──────────────────────────────────────────────

def test_normalize_strips_and_collapses_whitespace():
    """strip + 연속 공백 단일화로 정규화한다."""
    assert _normalize_sub_query("  서버   목록 \n 조회  ") == "서버 목록 조회"
    assert _normalize_sub_query(None) == ""


# ──────────────────────────────────────────────
# 메모 히트 — 같은 (agent, 정규화 sub_query) 2회 호출
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_sub_query_runs_pipeline_once(mock_config, monkeypatch, caplog):
    """같은 sub_query 2회 호출 시 handler는 1회만 실행되고 두 결과가 동일하다."""
    calls: list = []
    _install_handler(monkeypatch, "data_query", calls, [_ok_result()])
    collector: list = []

    out1 = await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    with caplog.at_level(logging.INFO):
        # 공백 차이는 정규화로 흡수된다(strip + 연속 공백 단일화).
        out2 = await _run_subagent_tool(
            "data_query", "  서버   목록 조회 ",
            worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
        )

    assert len(calls) == 1  # 파이프라인(handler)은 1회만 실행
    assert out1 == out2  # 도구 반환 텍스트 동일
    assert "sub_query 메모 히트" in caplog.text


@pytest.mark.asyncio
async def test_memo_hit_appended_to_collector_as_copy(mock_config, monkeypatch):
    """메모 히트도 collector에 정상 적재되고(D-062 합성 경로 동일), 복사본이라 원본 오염이 없다."""
    calls: list = []
    _install_handler(monkeypatch, "data_query", calls, [_ok_result()])
    collector: list = []

    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )

    assert len(collector) == 2
    task1, res1 = collector[0]
    task2, res2 = collector[1]
    assert task2["agent"] == "data_query"
    assert task2["status"] == "completed"
    assert task2["task_id"] != task1["task_id"]  # 합성 시 task_results 키 충돌 없음
    assert res2 == res1
    assert res2 is not res1  # deepcopy — 하류 변형이 원본을 오염시키지 않는다
    res2["organized_data"]["rows"].append({"hostname": "mutated"})
    assert len(res1["organized_data"]["rows"]) == 1


@pytest.mark.asyncio
async def test_memo_key_uses_corrected_agent(mock_config, monkeypatch):
    """data_query→alarm_query 결정적 교정(D-076 후속3) 후의 agent로 메모가 키잉된다."""
    alarm_calls: list = []
    _install_handler(monkeypatch, "alarm_query", alarm_calls, [_ok_result()])
    collector: list = []

    # 두 호출 모두 알람 신호로 alarm_query로 교정된다 → 두 번째는 메모 히트.
    await _run_subagent_tool(
        "data_query", "활성 알람 서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    await _run_subagent_tool(
        "data_query", "활성 알람 서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )

    assert len(alarm_calls) == 1
    assert len(collector) == 2
    assert all(t["agent"] == "alarm_query" for t, _ in collector)


# ──────────────────────────────────────────────
# 메모 미스 — 다른 sub_query / 다른 agent / 실패 결과 / 요청 경계
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_different_sub_query_runs_each(mock_config, monkeypatch):
    """다른 sub_query는 각각 실행된다."""
    calls: list = []
    _install_handler(monkeypatch, "data_query", calls, [_ok_result()])
    collector: list = []

    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    await _run_subagent_tool(
        "data_query", "디스크 사용량 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )

    assert len(calls) == 2
    assert len(collector) == 2


@pytest.mark.asyncio
async def test_same_sub_query_different_agent_runs_each(mock_config, monkeypatch):
    """같은 sub_query라도 다른 도구(agent)면 메모를 공유하지 않는다."""
    dq_calls: list = []
    gi_calls: list = []
    _install_handler(monkeypatch, "data_query", dq_calls, [_ok_result()])
    _install_handler(monkeypatch, "general_inference", gi_calls, [_ok_result()])
    collector: list = []

    await _run_subagent_tool(
        "data_query", "서버 현황 정리",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    await _run_subagent_tool(
        "general_inference", "서버 현황 정리",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )

    assert len(dq_calls) == 1
    assert len(gi_calls) == 1


@pytest.mark.asyncio
async def test_failed_result_not_memoized(mock_config, monkeypatch):
    """실패(error) 결과는 메모하지 않는다 — 같은 sub_query 재호출 시 재실행(재시도 정당)."""
    calls: list = []
    _install_handler(
        monkeypatch, "data_query", calls,
        [{"error": "DB 연결 실패"}, _ok_result()],
    )
    collector: list = []

    out1 = await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )
    out2 = await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=collector,
    )

    assert len(calls) == 2  # 실패는 재사용하지 않고 재실행
    assert out1.startswith("[실패]")
    assert "1건" in out2  # 재실행이 성공 결과를 반환
    assert collector[0][0]["status"] == "failed"
    assert collector[1][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_memo_resets_across_requests(mock_config, monkeypatch):
    """요청 경계(새 collector)를 넘으면 메모가 초기화된다 — 동시/후속 요청 오염 없음."""
    calls: list = []
    _install_handler(monkeypatch, "data_query", calls, [_ok_result()])

    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=[],
    )
    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={}, collector=[],
    )

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_no_collector_no_memoization(mock_config, monkeypatch):
    """collector가 없으면(요청 스코프 부재) 메모이제이션 없이 기존 동작 그대로다."""
    calls: list = []
    _install_handler(monkeypatch, "data_query", calls, [_ok_result()])

    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={},
    )
    await _run_subagent_tool(
        "data_query", "서버 목록 조회",
        worker_llm=None, app_config=mock_config, ambient_state={},
    )

    assert len(calls) == 2


# ──────────────────────────────────────────────
# _find_memoized_result 단위
# ──────────────────────────────────────────────

def test_find_memoized_result_skips_failed_and_error():
    """status=failed 또는 error 결과는 메모 후보에서 제외한다."""
    collector = [
        ({"agent": "data_query", "sub_query": "서버 목록", "status": "failed"},
         {"error": "실패"}),
        ({"agent": "data_query", "sub_query": "서버 목록", "status": "completed"},
         {"error": "실패했지만 status 오기록"}),
    ]
    assert _find_memoized_result("data_query", "서버 목록", collector) is None


def test_find_memoized_result_empty_query_never_matches():
    """빈/공백 sub_query는 메모 매칭하지 않는다."""
    collector = [
        ({"agent": "data_query", "sub_query": "", "status": "completed"}, {"ok": 1}),
    ]
    assert _find_memoized_result("data_query", "   ", collector) is None
