"""plans/211 W4 — 0건 진단 식별자 존재 확인 SQL의 정규형 비교(플래그 on) · off 비트 동일.

DB는 대역이다. SQL 조건 기대값은 `build_canonical_host_condition` 반환값을 그대로 쓴다.
"""

from __future__ import annotations

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from src.config import AppConfig, QueryConfig, Text2SQLConfig
from src.db_adapters import get_adapter
from src.db_adapters.polestar.entity_probe import build_entity_probe_sql
from src.dbhub.models import QueryResult
from src.domain.host_names import build_canonical_host_condition

ro = importlib.import_module("src.nodes.result_organizer")


def _cond(column: str, value: str) -> str:
    cond = build_canonical_host_condition(column, [value], business_suffix=True)
    assert cond is not None
    return cond


@pytest.mark.parametrize("value", ["SBHDBO53", "Web01.Bank.Local", "10.1.2.3", "a'b", "web%01"])
@pytest.mark.parametrize(("engine", "schema"), [("postgresql", "polestar"), ("db2", "POLESTAR")])
def test_off_is_default(value: str, engine: str, schema: str) -> None:
    assert build_entity_probe_sql(value, db_engine=engine, db_schema=schema) == (
        build_entity_probe_sql(value, db_engine=engine, db_schema=schema, canonical=False)
    )


@pytest.mark.parametrize("value", ["WEB-AP01", "webap01(인터넷뱅킹)", "Web01.Bank.Local"])
def test_on_uses_canonical_condition(value: str) -> None:
    sql = build_entity_probe_sql(
        value, db_engine="postgresql", db_schema="polestar", canonical=True
    )
    assert f"({_cond('r.name', value)} OR {_cond('r.hostname', value)})" in sql
    assert "LOWER(r.name) IN" not in sql and "LOWER(r.hostname) LIKE" not in sql
    assert sql.startswith("SELECT 1 AS hit\n  FROM polestar.cmm_resource r\n")
    assert sql.endswith(" LIMIT 1")


def test_on_db2() -> None:
    sql = build_entity_probe_sql("web_ap01", db_engine="db2", db_schema="POLESTAR", canonical=True)
    assert _cond("r.name", "web_ap01") in sql and _cond("r.hostname", "web_ap01") in sql
    assert "POLESTAR.cmm_resource" in sql and "FETCH FIRST 1 ROWS ONLY" in sql


@pytest.mark.parametrize("value", ["10.1.2.3", "web%01", "(인터넷뱅킹)", "a'b"])
def test_on_keeps_current_for_ip_and_non_key(value: str) -> None:
    on = build_entity_probe_sql(value, db_engine="postgresql", db_schema="s", canonical=True)
    assert on == build_entity_probe_sql(value, db_engine="postgresql", db_schema="s")


def test_adapter_threads_canonical() -> None:
    adapter = get_adapter("db_a", {"db_a"})
    assert adapter is not None
    for canonical in (True, False):
        assert adapter.entity_probe_sql(
            "web-ap01", db_engine="db2", db_schema="POLESTAR", canonical=canonical
        ) == build_entity_probe_sql(
            "web-ap01", db_engine="db2", db_schema="POLESTAR", canonical=canonical
        )
    assert adapter.entity_probe_sql(
        "web-ap01", db_engine="db2", db_schema="POLESTAR"
    ) == build_entity_probe_sql("web-ap01", db_engine="db2", db_schema="POLESTAR")


_DOMAINS = {
    "db_a": SimpleNamespace(display_name="김포", db_engine="postgresql", db_schema="polestar"),
}


@pytest.fixture
def calls(monkeypatch) -> list[str]:  # noqa: ANN001
    out: list[str] = []

    class _Client:
        async def execute_sql(self, sql: str) -> QueryResult:
            out.append(sql)
            return QueryResult(columns=["hit"], rows=[], row_count=0)

    @asynccontextmanager
    async def _ctx(_config, *, db_id=None):  # noqa: ANN001, ANN202
        yield _Client()

    monkeypatch.setattr(ro, "get_db_client", _ctx)
    monkeypatch.setattr(ro, "get_domain_by_id", _DOMAINS.get)
    return out


def _config(enabled: object) -> AppConfig:
    """검증 대상 필드를 명시해 `.env` 누수를 막는다(Known Mistakes)."""
    config = AppConfig(polestar_db_ids="db_a")
    config.query = QueryConfig()
    config.text2sql = Text2SQLConfig(empty_diagnosis_enabled=True, empty_diagnosis_max_probes=5)
    config.host_canonical_match_enabled = enabled  # type: ignore[assignment]
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False, "true"])
async def test_result_organizer_passes_flag(calls: list[str], enabled: object) -> None:
    state = {"active_db_id": "db_a", "is_multi_db": False}
    await ro._check_entities(state, _config(enabled), ("web-ap01",), 5)  # type: ignore[arg-type]
    expected = build_entity_probe_sql(
        "web-ap01", db_engine="postgresql", db_schema="polestar", canonical=enabled is True
    )
    assert calls == [expected]
