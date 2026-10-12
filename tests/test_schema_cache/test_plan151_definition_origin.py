"""plans/151 W1·W7 — 승인 결과의 보존 출처 키 · P1 조립이 승인 정의로 식별 키 후보를 거른다.

확인하는 계약:
- W1: `_apply_profile_assets` 결과 `table_definitions_kept`(`{테이블: 보존한 출처}`) · 호환 키
  `table_definitions_kept_manual`(`manual` 보존분만) · 보존이 없으면 두 키 모두 없음
- W7: `_assemble(definitions=…)`의 `entity_keys`가 수집 이력 테이블을 고르지 않는다
  (정의 없으면 종전)
Redis는 페이크, 파일은 tmp — 네트워크 0.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domain import schema_inference as inf
from src.schema_cache import schema_probe as probe
from src.schema_cache.asset_generation_service import AssetGenerationService, _assemble
from tests.test_schema_cache.test_plan104_service_fixtures import Env, make_env

SRC = "app_defs"


@pytest.fixture
def env(tmp_path, monkeypatch) -> Env:
    return make_env(tmp_path, monkeypatch)


def _def(manages: str, origin: str | None) -> dict[str, Any]:
    item: dict[str, Any] = {"manages": manages}
    if origin is not None:
        item["origin"] = origin
    return item


async def _apply(env: Env, base_defs: dict[str, Any], draft_defs: dict[str, Any]) -> dict[str, Any]:
    env.write_profile(SRC, {"source": "manual", "table_definitions": base_defs})
    service = AssetGenerationService(env.config, env.mgr, **env.service_kwargs())
    return await service._apply_profile_assets(
        SRC, {"table_definitions": draft_defs}, None, "d1", "admin", "",
    )


async def test_kept_reports_origin_per_table_and_legacy_manual_key(env):
    result = await _apply(
        env,
        {"t_m": _def("사람", "manual"), "t_i": _def("가져옴", "import"),
         "t_none": _def("가져오기 이전 사본", None), "t_c": _def("주석", "comment")},
        {"t_m": _def("주석 m", "comment"), "T_I": _def("주석 i", "comment"),
         "t_none": _def("llm", "llm"), "t_c": _def("주석 갱신", "comment"),
         "t_new": _def("새 테이블", "comment")},
    )
    assert result["table_definitions_kept"] == {"t_m": "manual", "T_I": "import",
                                                "t_none": "import"}
    assert result["table_definitions_kept_manual"] == ["t_m"]
    profile = env.store.read_current_profile(SRC)["profile"]
    assert profile["table_definitions"] == {
        "t_m": _def("사람", "manual"), "t_i": _def("가져옴", "import"),
        "t_none": _def("가져오기 이전 사본", None), "t_c": _def("주석 갱신", "comment"),
        "t_new": _def("새 테이블", "comment"),
    }


async def test_no_kept_keys_when_every_draft_row_applied(env):
    result = await _apply(env, {"t_i": _def("가져옴", "import")},
                          {"t_i": _def("다시 가져옴", "import")})
    assert "table_definitions_kept" not in result
    assert "table_definitions_kept_manual" not in result


async def test_kept_without_manual_has_no_legacy_key(env):
    result = await _apply(env, {"t_i": _def("가져옴", "import")}, {"t_i": _def("주석", "comment")})
    assert result["table_definitions_kept"] == {"t_i": "import"}
    assert "table_definitions_kept_manual" not in result


# ─── W7 조립 ────────────────────────────────────────────────────


def _columns() -> dict[str, dict[str, Any]]:
    host = inf.ValueProfile(total=10, hostname=1.0)
    ip = inf.ValueProfile(total=10, ipv4=1.0)
    out: dict[str, dict[str, Any]] = {}
    for table in ("t_hist", "t_srv"):
        out[f"{table}.hostNm"] = {"type": "varchar", "comment": None, "error": None,
                                  "profile": host}
        out[f"{table}.ipAddr"] = {"type": "varchar", "comment": None, "error": None,
                                  "profile": ip}
    return out


def _entity_table(definitions: Any = None, *, pass_arg: bool = True) -> str | None:
    catalog = probe.CatalogInfo(row_estimates={"t_hist": 900_000, "t_srv": 5_000})
    args: tuple[Any, ...] = (
        SRC, {"tables": {}}, ["t_hist", "t_srv"], {}, catalog, [], _columns(), [],
    )
    assets, _evidence = _assemble(*args, definitions) if pass_arg else _assemble(*args)
    return (assets["entity_keys"] or {}).get("table")


def test_assemble_skips_history_table_with_definitions():
    assert _entity_table(pass_arg=False) == "t_hist"  # 종전 — 행 수 큰 순
    defs = {"t_hist": {"manages": "수집 이력", "kind": "수집이력", "origin": "import"}}
    assert _entity_table(defs) == "t_srv"


@pytest.mark.parametrize("definitions", [None, {}])
def test_assemble_without_definitions_identical(definitions):
    assert _entity_table(definitions) == _entity_table(pass_arg=False) == "t_hist"
