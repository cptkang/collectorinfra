"""2단 실시간 사용률 경로의 DB 승격 재료 (plans/120 S-1b · D-205 대칭).

S-1 확인 중 드러난 결함: `run_data_query_pipeline`의 실시간 사용률 분기(Plan 71)가 SQL 경로와 달리
`target_db_ids`·`db_origin`을 싣지 않아, 집계기 `_collect_db_promotion`이 승격할 DB를 못 찾는다.
그러면 그 턴의 `db_scope.db_ids`가 스트림·비스트림 모두 비고(이 run C-12) 다음 턴 DB 승계가 끊긴다.

전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.orchestration.subagents as sub
from src.orchestration.result_aggregator import _collect_db_promotion

_DB = "polestar_cm_gp"


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        multi_db=SimpleNamespace(
            get_active_db_ids=lambda: [_DB], zone_group_exclusive=False,
        ),
        get_polestar_db_ids=lambda: {_DB},
        polestar_rest=SimpleNamespace(realtime_usage_enabled=True),
        router=SimpleNamespace(capability_ownership_enabled=False),
    )


@pytest.mark.asyncio
async def test_realtime_result_carries_promotion_keys(monkeypatch):
    rows = [{"server_name": "svr01", "cpu_pct": 12.5}]
    lookup = AsyncMock(return_value={
        "query_results": rows,
        "organized_data": {"rows": rows, "summary": "실시간 사용률 1건"},
        "source": "realtime_usage",
    })
    monkeypatch.setattr(sub, "realtime_usage_lookup", lookup)
    # SQL 경로로 새면 이 표지가 불린다 — 실시간 분기에서 끝나야 한다.
    monkeypatch.setattr(sub, "_run_single_db_pipeline", AsyncMock(
        side_effect=AssertionError("SQL 경로로 새면 안 된다")))

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "지금 CPU", "db_ids": [_DB]}
    isolated = {
        "user_query": "지금 CPU 사용률",
        "realtime_usage_intent": True,
        "parsed_requirements": {"original_query": "지금 CPU 사용률"},
        "conversation_context": None,
    }
    out = await sub.run_data_query_pipeline(task, isolated, llm=MagicMock(), app_config=_cfg())

    lookup.assert_awaited_once()
    assert out["active_db_id"] == _DB
    assert out["target_db_ids"] == [_DB]
    assert out["db_origin"]  # 출처 신호(D-205 ②) — 값은 SQL 경로와 같은 판정

    promoted = _collect_db_promotion([task], {"t1": out})
    assert promoted["active_db_id"] == _DB
    assert promoted["target_databases"] == [{"db_id": _DB}]
    assert promoted["db_scope_source"] == out["db_origin"]
