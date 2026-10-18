"""plans/166 W1 X3·X4·N4·N1(b) — 사본을 정본 1곳으로 모은 뒤 값·판정·재노출을 고정한다.

동작 불변 리팩터라 기대값은 「지금 값」이다. 정본 값과 옛 이름(재노출·별칭)이 같은 객체를
가리키는지까지 본다 — 사본이 다시 생기면 이 테스트가 먼저 깨진다.
"""

from __future__ import annotations

import re
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from src.config import AppConfig, RouterConfig
from src.routing.capability_ownership import capability_ownership_on
from src.utils.clock import now_iso
from src.utils.sql_dialect import engine_key
from src.utils.task_result_keys import DEGRADED_KEY, DISPLAY_CUT_KEY

# ── X3 처리기 결과 키 ───────────────────────────────────────────


def test_task_result_key_values() -> None:
    assert DEGRADED_KEY == "degraded_reason"
    assert DISPLAY_CUT_KEY == "display_rows_cut"


def test_handler_modules_reexport_the_same_keys() -> None:
    from src.orchestration import apm_query, doc_query, host_inspect, portal_help

    for mod in (apm_query, doc_query, host_inspect, portal_help):
        assert mod.DEGRADED_KEY is DEGRADED_KEY, mod.__name__
    assert apm_query.DISPLAY_CUT_KEY is DISPLAY_CUT_KEY


# ── X4 ① 답변 영역 소유 플래그 ───────────────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (SimpleNamespace(), False),
        (SimpleNamespace(router=None), False),
        (SimpleNamespace(router=SimpleNamespace()), False),
        (SimpleNamespace(router=SimpleNamespace(capability_ownership_enabled=False)), False),
        (SimpleNamespace(router=SimpleNamespace(capability_ownership_enabled=True)), True),
        (SimpleNamespace(router=SimpleNamespace(capability_ownership_enabled=1)), True),
    ],
)
def test_capability_ownership_on(config: Any, expected: bool) -> None:
    assert capability_ownership_on(config) is expected


def test_capability_ownership_on_reads_app_config() -> None:
    cfg = AppConfig()
    cfg.router = RouterConfig(capability_ownership_enabled=True)
    assert capability_ownership_on(cfg) is True
    cfg.router = RouterConfig(capability_ownership_enabled=False)
    assert capability_ownership_on(cfg) is False


def test_intent_planner_keeps_old_alias() -> None:
    import src.orchestration.intent_planner  # noqa: F401

    ip = sys.modules["src.orchestration.intent_planner"]
    assert ip._capability_ownership_on is capability_ownership_on


# ── X4 ② 엔진 이름 정규화 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        ("postgres", "postgresql"),
        (" Postgres ", "postgresql"),
        ("postgresql", "postgresql"),
        ("MySQL", "mariadb"),
        ("mariadb", "mariadb"),
        ("DB2", "db2"),
        ("", ""),
        (None, ""),
    ],
)
def test_engine_key(engine: str | None, expected: str) -> None:
    assert engine_key(engine) == expected


def test_schema_probe_reexports_engine_key() -> None:
    from src.schema_cache import schema_probe

    assert schema_probe.engine_key is engine_key


# ── X4 ③ 현재 시각 문자열 ────────────────────────────────────────


def test_now_iso_format() -> None:
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[+-]\d\d:\d\d", now_iso())


def test_now_iso_copies_point_to_one_function() -> None:
    from src.orchestration import apm_jobs
    from src.schema_cache import admin_jobs, db_structure_service, structure_store

    for mod in (admin_jobs, structure_store, db_structure_service, apm_jobs):
        assert mod._now_iso is now_iso, mod.__name__


# ── N4 어댑터 조회 헬퍼 ──────────────────────────────────────────


class _Cfg:
    def __init__(self, ids: set[str]) -> None:
        self._ids = ids

    def get_polestar_db_ids(self) -> set[str]:
        return self._ids


def test_get_adapter_for_matches_get_adapter_pair() -> None:
    from src.db_adapters import get_adapter, get_adapter_for

    for ids, db_id in (({"polestar"}, "polestar"), (set(), "polestar"), ({"polestar"}, "itam")):
        expected = get_adapter(db_id, ids or None)
        got = get_adapter_for(db_id, _Cfg(ids))
        assert type(got) is type(expected), (ids, db_id)
        assert getattr(got, "name", None) == getattr(expected, "name", None)


def test_get_adapter_for_passes_none_for_empty_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 담당 집합은 `None`으로 넘긴다(종전 `... or None`) — 모듈 속성을 호출 시점에 읽는다."""
    import src.db_adapters as adapters

    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(adapters, "get_adapter", lambda db_id, ids=None: seen.append((db_id, ids)))
    adapters.get_adapter_for("x", _Cfg(set()))
    adapters.get_adapter_for("y", _Cfg({"y"}))
    assert seen == [("x", None), ("y", {"y"})]


# ── N1(b) 단일·멀티 경로 사본 ────────────────────────────────────


def test_eav_pattern_wrappers_alias_the_shared_builder() -> None:
    """단일·멀티의 `_get_eav_pattern`(바이트 동일 래퍼)은 `first_eav_pattern` 별칭이다."""
    import src.nodes.multi_db_executor  # noqa: F401
    import src.nodes.query_generator  # noqa: F401
    from src.nodes.prompt_blocks import first_eav_pattern

    qg = sys.modules["src.nodes.query_generator"]
    mdb = sys.modules["src.nodes.multi_db_executor"]
    assert qg._get_eav_pattern is first_eav_pattern
    assert mdb._get_eav_pattern is first_eav_pattern
