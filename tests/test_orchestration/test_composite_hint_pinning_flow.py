"""2단 복합 계획의 task 단위 위치 힌트 고정 — `agent_orchestrator` 경로 통합 (plans/113 F-2).

결함(plans/113 A-3 · 로컬 MLX 재현 §1.2 #4·#5): 복합 분해(`is_composite`)면 원문 힌트 고정을
통째로 꺼서, task마다 LLM 분류가 DB를 골랐다 — "공동존 CPU top10과 메모리 top10"이 CPU task → 김포,
메모리 task → 여의도로 갈라졌다. 이제 task 단위로 고정한다. LLM·Redis·DB 0.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration import subagents
from src.orchestration.agent_orchestrator import agent_orchestrator

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"


def _row(db_id: str) -> dict:
    return {"db_id": db_id, "relevance_score": 0.9, "sub_query_context": "정제 질의",
            "user_specified": False, "reason": "LLM 분류"}


def _task(tid: str, sub_query: str) -> dict:
    return {"task_id": tid, "agent": "data_query", "sub_query": sub_query,
            "depends_on": [], "input_from": [], "order": int(tid[1:]), "status": "pending"}


async def _run(mock_config, hints: list[str], tasks: list[dict], classify: dict[str, str]):
    """두 task를 `agent_orchestrator`로 돌리고 task별로 파이프라인에 실린 대상을 모은다."""
    mock_config.multi_db = MagicMock()
    mock_config.multi_db.get_active_db_ids.return_value = [_B0, _GP, _YD]
    mock_config.multi_db.zone_group_exclusive = False
    seen: dict[str, list[str]] = {}

    async def _classify(llm, sub_query, app_config):
        return [_row(classify[sub_query])]

    async def _capture(s, *args, **kwargs):
        seen[s["parsed_requirements"]["original_query"]] = [
            t["db_id"] for t in s["target_databases"]
        ]
        return {"query_results": [], "db_results": {}, "error_message": None}

    state = {
        "user_query": "원문",
        "parsed_requirements": {"original_query": "원문", "target_db_hints": hints},
        "task_plan": tasks,
        "task_results": {},
        "is_composite": len(tasks) > 1,
    }
    with patch.object(subagents, "classify_dbs", AsyncMock(side_effect=_classify)), \
            patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
            patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
            patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
            patch.object(subagents, "result_organizer",
                         AsyncMock(return_value={"organized_data": {"rows": []}})):
        await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config)
    return seen


@pytest.mark.asyncio
async def test_zone_hint_reaches_both_composite_tasks(mock_config):
    """분류가 CPU task → 김포, 메모리 task → 여의도로 갈라도(A-3 재현) 두 task 모두 공동존 전체."""
    q1, q2 = "VM 최대 CPU 사용률 상위 10개 서버", "VM 최대 메모리 사용률 상위 10개 서버"
    seen = await _run(
        mock_config, ["공동존"], [_task("t1", q1), _task("t2", q2)], {q1: _GP, q2: _YD},
    )
    assert seen == {q1: [_GP, _YD], q2: [_GP, _YD]}


@pytest.mark.asyncio
async def test_location_split_tasks_get_their_own_zone(mock_config):
    """원문 "김포 CPU top10과 여의도 메모리 top10" — 분류가 반대로 골라도 task별 [gp] / [yd]."""
    q1, q2 = "김포 서버 CPU 상위 10개", "여의도 서버 메모리 상위 10개"
    seen = await _run(
        mock_config, ["김포", "여의도"], [_task("t1", q1), _task("t2", q2)], {q1: _YD, q2: _GP},
    )
    assert seen == {q1: [_GP], q2: [_YD]}
