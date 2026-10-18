"""`result_organizer` 데이터 부족 반환의 `sheet_mappings` 키 단언 (plans/184 K6).

종전에는 `data_insufficient` 반환의 `OrganizedData`만 `sheet_mappings` 키가 없어, 다른 두 반환
(이름 칸 힌트·정리 완료)과 모양이 달랐다. 이제 `sheet_mappings=None`을 명시한다.
협력 함수(충분성 판정·이름 칸 힌트·0건 진단)는 가짜로 주입한다 — LLM·DB 미사용.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest

from tests.test_nodes.test_plan173_t6_result_organizer import _config, _state

ro = importlib.import_module("src.nodes.result_organizer")


@pytest.mark.parametrize("rows", [[], [{"hostname": "web-01", "cpu": 12.5}]])
async def test_data_insufficient_carries_sheet_mappings_none(
    monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]
) -> None:
    """데이터 부족 재시도 반환에도 `sheet_mappings` 키가 있고 값은 None이다."""

    async def _insufficient(*args: Any, **kwargs: Any) -> bool:
        return False

    async def _no_diag(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(ro, "_check_data_sufficiency", _insufficient)
    monkeypatch.setattr(ro, "name_match_retry_hint", lambda *a, **k: None)
    monkeypatch.setattr(ro, "_diagnose_empty_result", _no_diag)

    out = await ro.result_organizer(_state(query_results=rows), llm=object(), app_config=_config())

    assert out["error_message"] == "data_insufficient"
    organized = out["organized_data"]
    assert "sheet_mappings" in organized
    assert organized["sheet_mappings"] is None
