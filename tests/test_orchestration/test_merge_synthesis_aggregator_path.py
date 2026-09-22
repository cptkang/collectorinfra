"""멀티 DB 종합(S-1 순위 재정렬 · S-2 미리보기·존별 줄 · S-3 집계)이 1·2단 경로에서도 응답에 닿는다
(plans/113 · D-066 경로 대칭).

2단: `run_data_query_pipeline`(→ `result_merger`·`result_organizer` 실물) → `result_aggregator`
(→ `output_generator` 실물). 1단: 도구 결과 요약(`_serialize_for_tool`)도 DB 순 절단을 하지 않는다.
응답 LLM 호출은 대역으로 바꿔 프롬프트를 잡는다. LLM·Redis·DB 0.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.multi_db_executor import _merge_results
from src.orchestration import subagents
from src.orchestration.result_aggregator import result_aggregator

_GP, _YD = "polestar_cm_gp", "polestar_cm_yd"
_RANK_SQL = (
    'SELECT r.hostname AS "서버명", MAX(s.cpu) AS "최대 CPU" FROM x r JOIN y s ON 1=1 '
    'GROUP BY r.hostname ORDER BY "최대 CPU" DESC NULLS LAST LIMIT 10'
)
_AGG_SQL = "SELECT COUNT(*) AS server_count, AVG(r.cpu) AS avg_cpu FROM x r"


def _executor(db_results: dict, sqls: dict):
    async def _run(state, **kwargs):
        return {
            "db_results": db_results, "db_errors": {}, "db_executed_sqls": sqls,
            "query_results": _merge_results(db_results), "query_attempts": [],
            "error_message": None,
        }
    return _run


async def _two_tier_response(mock_config, db_results: dict, sqls: dict) -> tuple[str, str, dict]:
    """2단 파이프라인 → 집계기까지 돌리고 (응답 LLM 프롬프트, 최종 응답, task 결과)를 돌려준다."""
    mock_config.multi_db = MagicMock()
    mock_config.multi_db.get_active_db_ids.return_value = [_GP, _YD]
    mock_config.multi_db.zone_group_exclusive = False
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "공동존 VM 최대 CPU top 10",
            "order": 1, "status": "completed"}
    isolated = {
        "user_query": task["sub_query"],
        "parsed_requirements": {"original_query": task["sub_query"], "query_targets": ["cpu"],
                                "output_format": "text", "target_db_hints": ["공동존"]},
    }
    classify = AsyncMock(return_value=[{"db_id": _GP, "relevance_score": 0.9,
                                        "sub_query_context": "q", "user_specified": False}])
    with patch.object(subagents, "classify_dbs", classify), \
            patch.object(subagents, "multi_db_executor",
                         AsyncMock(side_effect=_executor(db_results, sqls))):
        res = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=mock_config,
        )
    prompts: list[str] = []

    async def _fake_stream(llm, messages, tags=None):
        prompts.append(messages[-1].content)
        return "응답 본문"

    state = {"user_query": task["sub_query"],
             "parsed_requirements": isolated["parsed_requirements"],
             "task_plan": [task], "task_results": {"t1": res}}
    with patch("src.nodes.output_generator.astream_text", _fake_stream):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config)
    return prompts[0], out["final_response"], res


@pytest.mark.asyncio
async def test_ranking_and_zone_line_reach_two_tier_response(mock_config):
    gp = [{"서버명": f"gp-{i}", "최대 CPU": float(90 - i)} for i in range(10)]
    yd_values = [95, 94, 93, 50, 49, 48, 47, 46, 45, 44]
    yd = [{"서버명": f"yd-{i}", "최대 CPU": v} for i, v in enumerate(yd_values)]
    prompt, response, res = await _two_tier_response(
        mock_config, {_GP: gp, _YD: yd}, {_GP: _RANK_SQL, _YD: _RANK_SQL},
    )
    assert "전체 기준 상위 10건만 남겼습니다" in prompt
    assert '"출처"' in prompt
    body = prompt.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert [r["서버명"] for r in json.loads(body)][:3] == ["yd-0", "yd-1", "yd-2"]
    assert "**[존별 결과]**" in response and "→ 전체 기준 상위 10건" in response
    assert len(res["query_results"]) == 20  # CSV 원천은 원본(G-4)
    assert len(res["rows"]) == 10  # 같은 턴 선행 결과 전달은 전체 상위 N


@pytest.mark.asyncio
async def test_aggregates_reach_two_tier_response(mock_config):
    prompt, response, _ = await _two_tier_response(
        mock_config,
        {_GP: [{"server_count": 12, "avg_cpu": 40.0}], _YD: [{"server_count": 8, "avg_cpu": 30.0}]},
        {_GP: _AGG_SQL, _YD: _AGG_SQL},
    )
    assert "## 수치 요약 (DB별 집계 — 코드 계산값)" in prompt
    assert "→ 전체 20" in prompt and "(전체 값 없음)" in prompt
    assert "**[존별 집계]**" in response and "전체 20" in response


def test_tier1_tool_summary_is_not_cut_by_db_order():
    """1단 도구 결과(오케스트레이터에 보이는 요약)도 DB별로 고르게 자른다.

    뒤 DB 행이 사라지지 않는다.
    """
    from src.orchestration.deepagents_tools import _MAX_TOOL_ROWS, _serialize_for_tool

    rows = _merge_results({
        _GP: [{"h": f"gp-{i}"} for i in range(_MAX_TOOL_ROWS + 10)],
        _YD: [{"h": f"yd-{i}"} for i in range(5)],
    })
    payload = json.loads(_serialize_for_tool({"organized_data": {"summary": "", "rows": rows}}))
    assert len(payload["rows"]) == _MAX_TOOL_ROWS
    assert sum(r["_source_db"] == _YD for r in payload["rows"]) == 5
    assert payload["truncated"] is True
