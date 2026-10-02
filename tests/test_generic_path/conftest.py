"""범용성 회귀 스위트 공용 픽스처 (Plan 63 P4-2)."""

import json
from pathlib import Path

import pytest

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "testdata" / "generic_mon" / "schema.json"
)


@pytest.fixture(scope="session")
def generic_mon_raw() -> dict:
    """generic_mon 픽스처 원본(JSON)."""
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def generic_mon_schema(generic_mon_raw) -> dict:
    """파이프라인이 소비하는 schema_info 형식(tables + _structure_meta 없음).

    비폴스타 DB는 프로필/시맨틱 모델이 없으므로 `_structure_meta`가 없다(공통 경로만).
    """
    return {"tables": generic_mon_raw["tables"]}
