"""plans/120 F-3 — 폼필 매핑 우선 DB = 이번 턴 대상 DB(존 선택).

벤치 run `20260923-140539` H-10: 실행 DB는 존 선택으로 김포(gp)였는데 매핑 우선 DB는 질의 텍스트
힌트뿐이라 `field_mapper 완료: 17/17 매핑, DB=['polestar_b0','polestar_cm_yd']`가 됐다.
`selected_db_ids`(폼필 답변 턴은 라우트가 확정 존을 여기로 복원)를 텍스트 힌트보다 앞에 둔다.
선택이 없으면 종전 텍스트 힌트 동작과 비트 동일. LLM 0 · Redis 0.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.nodes.field_mapper import (
    _load_db_cache_data,
    _resolve_mapping_priority_db_ids,
    field_mapper,
)
from src.routing.location_hints import resolve_priority_db_ids

B0, GP, YD = "polestar_b0", "polestar_cm_gp", "polestar_cm_yd"
ACTIVE = [B0, GP, YD]


class TestResolveMappingPriority:
    def test_selection_wins_over_text_hints(self):
        assert _resolve_mapping_priority_db_ids(["여의도"], ACTIVE, [GP]) == [GP]

    def test_selection_keeps_order_and_dedups(self):
        assert _resolve_mapping_priority_db_ids([], ACTIVE, [YD, GP, YD]) == [YD, GP]

    @pytest.mark.parametrize(
        "hints", [[], ["여의도"], ["김포"], ["공동존"], ["은행존"], ["폴스타"], ["없는 위치"]]
    )
    @pytest.mark.parametrize("selected", [None, []])
    def test_no_selection_bit_identical_to_text_hints(self, hints, selected):
        assert _resolve_mapping_priority_db_ids(hints, ACTIVE, selected) == (
            resolve_priority_db_ids(hints, ACTIVE)
        )

    def test_selection_outside_active_falls_back_to_text_hints(self):
        assert _resolve_mapping_priority_db_ids(["여의도"], ACTIVE, ["retired_db"]) == (
            resolve_priority_db_ids(["여의도"], ACTIVE)
        )

    def test_active_unknown_keeps_selection(self):
        """활성 목록을 못 얻으면 선택을 그대로 쓴다(semantic_router 우선순위 2.5와 같은 필터)."""
        assert _resolve_mapping_priority_db_ids([], [], [GP]) == [GP]


class _FakeCacheManager:
    """DB별 유사어만 돌려주는 캐시 매니저 대역(구조 선언·설명 없음)."""

    redis_available = True

    def __init__(self, synonyms: dict[str, dict[str, list[str]]]) -> None:
        self._synonyms = synonyms
        self._redis_cache = MagicMock()
        self._redis_cache.load_eav_name_synonyms = AsyncMock(return_value={"_": ["_"]})

    async def load_synonyms_with_global_fallback(self, db_id: str) -> dict[str, list[str]]:
        return self._synonyms.get(db_id, {})

    async def get_descriptions(self, db_id: str) -> dict[str, str]:
        return {}

    async def get_global_synonyms(self) -> dict[str, list[str]]:
        return {"_": ["_"]}

    async def get_structure_meta_or_profile(self, db_id: str) -> Any:
        return None


def _state(selected: list[str] | None, hints: list[str]) -> dict[str, Any]:
    return {
        "user_query": "2026년 6월 기준 CPU 양식 채워줘",
        "template_structure": {"file_type": "xlsx", "sheets": [{"headers": ["호스트명"]}]},
        "parsed_requirements": {"field_mapping_hints": [], "target_db_hints": hints},
        "selected_db_ids": selected,
    }


def _config() -> MagicMock:
    cfg = MagicMock()
    cfg.multi_db.get_active_db_ids.return_value = list(ACTIVE)
    return cfg


class TestFieldMapperNode:
    """노드 통합 — 세 DB에 같은 유사어가 있어도 매핑은 존 선택 DB로 간다."""

    SYNONYMS = {db: {"polestar.cmm_resource.hostname": ["호스트명"]} for db in ACTIVE}

    async def _run(self, selected: list[str] | None, hints: list[str]) -> dict:
        with patch(
            "src.schema_cache.cache_manager.get_cache_manager",
            return_value=_FakeCacheManager(self.SYNONYMS),
        ):
            return await field_mapper(
                _state(selected, hints), llm=AsyncMock(), app_config=_config()
            )

    async def test_selected_gp_maps_to_gp(self):
        out = await self._run([GP], [])
        assert out["mapped_db_ids"] == [GP]
        assert out["db_column_mapping"] == {GP: {"호스트명": "polestar.cmm_resource.hostname"}}

    async def test_selected_gp_beats_conflicting_text_hint(self):
        out = await self._run([GP], ["여의도"])
        assert out["mapped_db_ids"] == [GP]

    async def test_no_selection_keeps_text_hint_behavior(self):
        """선택이 없으면 종전대로 — 힌트 없음이면 첫 DB, 여의도 힌트면 yd."""
        assert (await self._run(None, []))["mapped_db_ids"] == [B0]
        assert (await self._run(None, ["여의도"]))["mapped_db_ids"] == [YD]

    async def test_multi_zone_selection_replicates_to_all_selected(self):
        """공동존 두 DB 선택 — 다중 위치 복제가 선택 DB 전체로 퍼진다(텍스트 힌트와 같은 규칙)."""
        out = await self._run([GP, YD], [])
        assert out["mapped_db_ids"] == [GP, YD]
        assert set(out["db_column_mapping"]) == {GP, YD}


async def test_load_db_cache_data_priority_uses_selection():
    with patch(
        "src.schema_cache.cache_manager.get_cache_manager",
        return_value=_FakeCacheManager(TestFieldMapperNode.SYNONYMS),
    ):
        out = await _load_db_cache_data(MagicMock(), ACTIVE, ["은행존"], selected_db_ids=[GP])
    assert out[2] == [GP]
