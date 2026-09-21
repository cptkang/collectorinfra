"""존 선택 재개 턴에서 존 미배정 DB 보존 (plans/95 W-10 · plans/102 트랙 R 결함).

존 선택 역질문·범위 선택의 선택지는 **존 그룹**으로 만들어져 존 없는 DB는 고를 수 없다.
그런데 재개 턴이 `selected_db_ids`로 대상을 통째로 고정해 그 DB가 사유 없이 빠졌다.
후보가 있을 때만 원문을 분류해 그 DB를 보존하고, 후보가 없으면 분류 호출 0 · 반환 동일이다.
LLM·Redis·DB 0.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.routing.semantic_router import semantic_router
from src.state import create_initial_state

_B0, _GP, _YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
_ITAM = "itam"
_QUERY = "전체 서버의 유지보수 계약 만료일과 CPU 사용률을 알려줘"


def _state(selected: list[str]) -> dict:
    state = create_initial_state(_QUERY)
    state.update(selected_db_ids=selected)
    return state


def _config(active: list[str]):
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    config.enable_semantic_routing = True
    return config


def _row(db_id: str, score: float = 0.9) -> dict:
    return {
        "db_id": db_id,
        "relevance_score": score,
        "sub_query_context": _QUERY,
        "user_specified": False,
        "reason": "분류 결과",
    }


async def _route(active: list[str], selected: list[str], classify) -> dict:
    with patch("src.routing.semantic_router._llm_classify", classify):
        return await semantic_router(_state(selected), llm=AsyncMock(), app_config=_config(active))


class TestZonedOnlyIsUnchanged:
    """존 그룹만 활성인 조합 — 분류 호출 0 · 종전 반환 그대로."""

    @pytest.mark.asyncio
    async def test_no_zoneless_active_db_skips_classification(self):
        classify = AsyncMock()
        result = await _route([_B0, _GP, _YD], [_B0, _GP], classify)

        classify.assert_not_awaited()
        assert [t["db_id"] for t in result["target_databases"]] == [_B0, _GP]
        assert all(t["user_specified"] and t["relevance_score"] == 1.0
                   for t in result["target_databases"])
        assert result["is_multi_db"] is True
        assert result["active_db_id"] == _B0
        assert result["user_specified_db"] is None
        assert result["db_scope_source"] == "selected"

    @pytest.mark.asyncio
    async def test_return_value_is_bit_identical(self):
        """반환 dict 전체(키·값)가 종전과 같다 — 후보 없는 조합의 무플래그 근거."""
        classify = AsyncMock()
        result = await _route([_B0, _GP, _YD], [_GP], classify)

        classify.assert_not_awaited()
        assert result == {
            "target_databases": [{
                "db_id": _GP,
                "relevance_score": 1.0,
                "sub_query_context": _QUERY,
                "user_specified": True,
                "reason": "존 선택 역질문에서 사용자가 확정한 DB",
            }],
            "is_multi_db": False,
            "active_db_id": _GP,
            "user_specified_db": _GP,
            "routing_intent": "data_query",
            "db_scope_source": "selected",
            "current_node": "semantic_router",
        }

    @pytest.mark.asyncio
    async def test_local_sandbox_alone_is_unchanged(self):
        """`ACTIVE_DB_IDS=polestar` 단독(개발 머신 표기) — 샌드박스도 존 미배정이지만
        선택에 이미 들어와 후보가 없다 → 분류 호출 0 · 반환 동일."""
        classify = AsyncMock()
        result = await _route(["polestar"], ["polestar"], classify)

        classify.assert_not_awaited()
        assert [t["db_id"] for t in result["target_databases"]] == ["polestar"]
        assert result["user_specified_db"] == "polestar"

    @pytest.mark.asyncio
    async def test_zoneless_db_already_selected_is_not_a_candidate(self):
        """선택에 이미 들어온 DB는 후보가 아니다 — 분류하지 않는다."""
        classify = AsyncMock()
        result = await _route([_B0, _ITAM], [_B0, _ITAM], classify)

        classify.assert_not_awaited()
        assert [t["db_id"] for t in result["target_databases"]] == [_B0, _ITAM]


class TestZonelessDbIsKept:
    """존 미배정 DB가 활성이면 분류 결과에 한해 보존한다."""

    @pytest.mark.asyncio
    async def test_kept_when_classifier_picks_it(self):
        classify = AsyncMock(return_value=[_row(_GP), _row(_ITAM)])
        result = await _route([_B0, _GP, _YD, _ITAM], [_B0], classify)

        classify.assert_awaited_once()
        # 선택하지 않은 존(gp)은 들어오지 않고, 존 미배정 DB만 보존된다
        assert [t["db_id"] for t in result["target_databases"]] == [_B0, _ITAM]
        assert result["target_databases"][0]["user_specified"] is True
        assert "W-10" in result["target_databases"][1]["reason"]
        assert result["is_multi_db"] is True
        assert result["active_db_id"] == _B0
        assert result["user_specified_db"] is None

    @pytest.mark.asyncio
    async def test_not_added_when_classifier_ignores_it(self):
        classify = AsyncMock(return_value=[_row(_GP), _row(_YD)])
        result = await _route([_B0, _GP, _YD, _ITAM], [_B0], classify)

        assert [t["db_id"] for t in result["target_databases"]] == [_B0]
        assert result["is_multi_db"] is False
        assert result["user_specified_db"] == _B0

    @pytest.mark.asyncio
    async def test_below_min_relevance_is_dropped(self):
        classify = AsyncMock(return_value=[_row(_ITAM, score=0.2)])
        result = await _route([_B0, _ITAM], [_B0], classify)

        assert [t["db_id"] for t in result["target_databases"]] == [_B0]

    @pytest.mark.asyncio
    async def test_dict_shape_classification_is_read(self):
        """`_llm_classify`가 intent를 담은 dict를 돌려주는 형태도 읽는다."""
        classify = AsyncMock(return_value={"intent": "data_query", "databases": [_row(_ITAM)]})
        result = await _route([_B0, _ITAM], [_B0], classify)

        assert [t["db_id"] for t in result["target_databases"]] == [_B0, _ITAM]

    @pytest.mark.asyncio
    async def test_classification_failure_keeps_selection_with_warning(self, caplog):
        classify = AsyncMock(side_effect=RuntimeError("boom"))
        with caplog.at_level(logging.WARNING, logger="src.routing.semantic_router"):
            result = await _route([_B0, _ITAM], [_B0], classify)

        assert [t["db_id"] for t in result["target_databases"]] == [_B0]
        assert any("존 미배정 DB 보존 판정 실패" in r.getMessage() for r in caplog.records)
