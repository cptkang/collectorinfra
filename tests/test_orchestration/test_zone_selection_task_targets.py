"""존 선택 재개 턴의 task 고정에서 존 미배정 DB 보존 (plans/95 W-10 · 3단 순차 러너 경로).

3단은 순차 러너(`sequential_runner`)로 2단 부품(`agent_orchestrator` → `run_data_query_pipeline`)을
재사용한다(`docs/21_orchestration_ladder.md` §7 · `src/graph.py:162,180`). 그래서 task 고정이
`selected_db_ids`만 보면 라우터가 살려 둔 존 미배정 DB가 다시 떨어진다. LLM·DB 0.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration import subagents
from src.routing.db_scope import zone_selection_db_ids

_B0, _GP = "polestar_b0", "polestar_cm_gp"
_ITAM = "itam"


class TestZoneSelectionDbIds:
    """선택 존 + 존 미배정 DB 병합 규칙."""

    def test_zoneless_target_is_appended(self):
        merged = zone_selection_db_ids([_B0], [{"db_id": _B0}, {"db_id": _ITAM}])
        assert merged == [_B0, _ITAM]

    def test_zoned_targets_do_not_change_selection(self):
        """선택하지 않은 존은 들어오지 않는다 — 존 선택의 의미가 유지된다."""
        assert zone_selection_db_ids([_B0], [{"db_id": _B0}, {"db_id": _GP}]) == [_B0]

    def test_empty_selection_returns_empty(self):
        assert zone_selection_db_ids([], [{"db_id": _ITAM}]) == []
        assert zone_selection_db_ids(None, None) == []

    def test_no_targets_keeps_selection(self):
        assert zone_selection_db_ids([_B0, _GP], None) == [_B0, _GP]


def _config():
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = [_B0, _GP, _ITAM]
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    return config


async def _pipeline_targets(selected: list[str], targets: list[dict]) -> list[str]:
    """task 고정 경로를 돌리고 파이프라인에 실린 대상 db_id를 돌려준다."""
    captured: dict = {}

    async def _capture(state, **kwargs):
        captured["state"] = state
        return {}

    task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 목록"}
    isolated = {
        "user_query": "서버 목록",
        "parsed_requirements": {},
        "selected_db_ids": selected,
        "zone_selection_db_ids": zone_selection_db_ids(selected, targets),
    }
    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})):
        await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_config()
        )
    return [t["db_id"] for t in captured["state"]["target_databases"]]


class TestTaskPinningKeepsZonelessDb:
    @pytest.mark.asyncio
    async def test_zoneless_router_target_survives_task_pinning(self):
        got = await _pipeline_targets([_B0], [{"db_id": _B0}, {"db_id": _ITAM}])
        assert got == [_B0, _ITAM]

    @pytest.mark.asyncio
    async def test_zoned_only_is_unchanged(self):
        """존 미배정 대상이 없으면 종전대로 선택값 그대로다."""
        got = await _pipeline_targets([_B0, _GP], [{"db_id": _B0}, {"db_id": _GP}])
        assert got == [_B0, _GP]


def test_isolated_input_carries_router_targets():
    """격리 입력에 라우터 산출물이 실제로 실린다(배선 확인 — 정의만으론 무효)."""
    state = {
        "user_query": "서버 목록",
        "selected_db_ids": [_B0],
        "target_databases": [{"db_id": _B0}, {"db_id": _ITAM}],
    }
    isolated = subagents._make_isolated_input(
        {"task_id": "t1", "agent": "data_query", "sub_query": "서버 목록"}, state, {}
    )
    assert isolated["selected_db_ids"] == [_B0]
    # task 격리 규약상 target_databases는 비운다 — 그래서 접은 값을 따로 싣는다
    assert isolated["target_databases"] == []
    assert isolated["zone_selection_db_ids"] == [_B0, _ITAM]


def test_tier3_reuses_this_pipeline():
    """3단이 이 부품을 재사용한다는 근거 — 순차 러너 진입이 그래프 라우팅에 있다."""
    from src.graph import route_after_semantic_router_sequential
    from src.orchestration.sequential_runner import sequential_entry

    cfg = SimpleNamespace(
        composite=SimpleNamespace(sequential_fallback_tiers_enabled=True),
        enable_sql_approval=False,
    )
    state = {
        "routing_intent": "data_query",
        "user_query": "먼저 서버를 찾고 그 다음 계약을 조회해줘",
    }
    assert sequential_entry(state, cfg) is True
    assert route_after_semantic_router_sequential(state, config=cfg) == "sequential_runner"


# ──────────────────────────────────────────────
# 3단 순차 러너 경로 재현 — 팀 리드 판독(2026-09-21) 검증
# ──────────────────────────────────────────────

async def _sequential_targets(state_extra: dict) -> list[list[str]]:
    """3단 순차 러너를 돌리고 task별로 파이프라인에 실린 대상 db_id를 모은다."""
    import importlib

    # 패키지가 같은 이름의 함수를 재노출해 `import ... as`가 함수를 잡는다 — 모듈을 명시 로드한다
    sr = importlib.import_module("src.orchestration.sequential_runner")
    seen: list[list[str]] = []

    async def _capture(state, *args, **kwargs):
        seen.append([t["db_id"] for t in state["target_databases"]])
        return {"query_results": [], "organized_data": {"is_sufficient": True, "rows": []}}

    plan = {
        "tasks": [
            {"task_id": "t1", "agent": "data_query", "sub_query": "폴스타 서버 목록",
             "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
            {"task_id": "t2", "agent": "data_query", "sub_query": "그 서버의 자산 정보",
             "depends_on": ["t1"], "input_from": ["t1"], "order": 2, "status": "pending"},
        ],
        "degraded": [],
    }
    state = {
        "user_query": "폴스타에서 찾은 서버의 자산 정보를 그 다음 조회해줘",
        "parsed_requirements": {},
        **state_extra,
    }
    with patch.object(sr, "_llm_decompose", AsyncMock(return_value=plan)), \
         patch.object(sr, "result_aggregator", AsyncMock(return_value={})), \
         patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_capture)), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})):
        await sr.sequential_runner(state, llm=AsyncMock(), app_config=_config())
    return seen


class TestSequentialRunnerKeepsZonelessDb:
    """3단 기준 경로: 존 선택 재개 턴 + 순차 표지 질의에서 존 미배정 DB가 살아남는가."""

    @pytest.mark.asyncio
    async def test_zoneless_db_survives_sequential_runner(self):
        seen = await _sequential_targets({
            "selected_db_ids": [_B0],
            "target_databases": [{"db_id": _B0}, {"db_id": _ITAM}],
        })
        assert seen, "task 파이프라인이 실행되지 않았다"
        assert all(_ITAM in targets for targets in seen), seen

    @pytest.mark.asyncio
    async def test_zoned_only_selection_is_unchanged(self):
        seen = await _sequential_targets({
            "selected_db_ids": [_B0, _GP],
            "target_databases": [{"db_id": _B0}, {"db_id": _GP}],
        })
        assert all(targets == [_B0, _GP] for targets in seen), seen
