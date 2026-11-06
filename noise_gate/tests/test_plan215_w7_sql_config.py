"""plans/215 W7 — M3 SMS 고정 SELECT(방언 · `sql_guard` · IN ≤500 · 원천 값 규칙) · 지표 원천 설정
로더 · 공용 SMS 제한기 · 기동 배선(플래그 off = 태스크 0)."""

from __future__ import annotations

import asyncio
import inspect
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import yaml  # type: ignore[import-untyped]
from src.security.sql_guard import SQLGuard

from noise_gate.application import biz_monitor_collector as c
from noise_gate.application.biz_service_config import DEFAULT_CONFIG_PATH, load_biz_service_config
from noise_gate.infrastructure import biz_service_sql as q
from noise_gate.infrastructure.apm_host_map_sql_scanners import ScanSqlError

SOURCES = {"cpu": ("server.Cpus", "Utilization"), "filesystem": ("server.FileSystems",
                                                                 "Utilization")}


# ── SQL ─────────────────────────────────────────────────────────────────
def test_latest_stat_sql_db2_and_pg() -> None:
    db2 = q.build_sms_latest_stat_sql("polestar_b0", "db2", [3, 1, 3], SOURCES,
                                      since_hour="2026110300")
    assert db2.startswith("SELECT MAX(s.stat_date) AS stat_date")
    assert "FROM POLESTAR.cmm_metric_stat_h s" in db2
    assert "JOIN POLESTAR.cmm_resource r ON r.id = s.resource_id" in db2
    assert "r.platform_resource_id IN (1, 3)" in db2 and "s.stat_date >= '2026110300'" in db2
    assert ("(r.resource_type = 'server.Cpus' AND s.definition_name = 'Utilization') OR "
            "(r.resource_type = 'server.FileSystems' AND s.definition_name = 'Utilization')") in db2
    pg = q.build_sms_latest_stat_sql("polestar_cm_gp", "postgresql", [1], SOURCES,
                                     since_hour="2026110300")
    assert "FROM polestar.cmm_metric_stat_h s" in pg


def test_metric_rows_sql_dialect_limit_and_group() -> None:
    db2 = q.build_sms_metric_rows_sql("polestar_b0", "db2", range(1, 501), SOURCES,
                                      stat_hour_key="2026110514")
    assert db2.rstrip().endswith("FETCH FIRST 1000 ROWS ONLY") and "LIMIT" not in db2
    assert "s.stat_date = '2026110514'" in db2 and "s.avg_val BETWEEN 0 AND 1000" in db2
    assert "GROUP BY r.platform_resource_id, r.resource_type, s.definition_name" in db2
    assert "MAX(s.avg_val) AS avg_val" in db2 and "r.dtime IS NULL" in db2
    pg = q.build_sms_metric_rows_sql("polestar_cm_gp", "postgresql", [7], SOURCES,
                                     stat_hour_key="2026110514")
    assert pg.rstrip().endswith("LIMIT 2") and "FETCH FIRST" not in pg


def test_all_sms_builders_pass_sql_guard() -> None:
    guard = SQLGuard()
    for engine, db in (("db2", "polestar_b0"), ("postgresql", "polestar_cm_gp")):
        for sql in (
            q.build_sms_latest_stat_sql(db, engine, range(1, 501), SOURCES,
                                        since_hour="2026110300"),
            q.build_sms_metric_rows_sql(db, engine, range(1, 501), SOURCES,
                                        stat_hour_key="2026110514"),
        ):
            ok, reason = guard.is_safe_select(sql)
            assert ok, reason
            assert sql.startswith("SELECT")


@pytest.mark.parametrize("ids", [[], list(range(1, 502)), ["1 OR 1=1"], ["-1"]])
def test_sms_ids_rules(ids: list[Any]) -> None:
    with pytest.raises(ScanSqlError):
        q.build_sms_latest_stat_sql("polestar_b0", "db2", ids, SOURCES, since_hour="2026110300")


@pytest.mark.parametrize("bad", ["2026-11-05", "202611051", "2026110514' OR '1'='1", ""])
def test_stat_hour_rules(bad: str) -> None:
    with pytest.raises(ScanSqlError):
        q.build_sms_metric_rows_sql("polestar_b0", "db2", [1], SOURCES, stat_hour_key=bad)


@pytest.mark.parametrize("bad", ["server'.Cpus", "1abc", "", "a b", "x" * 65])
def test_source_value_rules(bad: str) -> None:
    assert not q.valid_sms_source_value(bad)
    with pytest.raises(ScanSqlError):
        q.build_sms_latest_stat_sql("polestar_b0", "db2", [1], {"cpu": (bad, "Utilization")},
                                    since_hour="2026110300")
    with pytest.raises(ScanSqlError):
        q.build_sms_latest_stat_sql("polestar_b0", "db2", [1], {}, since_hour="2026110300")


async def test_readers_parse_rows_case_insensitive() -> None:
    calls: list[str] = []

    async def execute(db_id: str, sql: str) -> list[dict[str, Any]]:
        calls.append(sql)
        if "MAX(s.stat_date)" in sql:
            return [{"STAT_DATE": "2026110514"}]
        return [
            {"SERVER_ID": 1, "RESOURCE_TYPE": "server.Cpus", "DEFINITION_NAME": "Utilization",
             "AVG_VAL": "41.5"},
            {"server_id": 1, "resource_type": "server.Other", "definition_name": "X",
             "avg_val": 9},
        ]

    latest = await q.read_sms_latest_stat(execute, "polestar_b0", "db2", [1], SOURCES,
                                          since_hour="2026110300")
    rows = await q.read_sms_metric_rows(execute, "polestar_b0", "db2", [1], SOURCES,
                                        stat_hour_key=latest or "")
    assert latest == "2026110514"
    assert rows == [q.SmsMetricRow("1", "cpu", 41.5)]


async def test_shared_sms_semaphore_is_one_per_loop() -> None:
    sem = q.sms_query_semaphore()
    assert sem is q.sms_query_semaphore()
    assert q.SMS_QUERY_CONCURRENCY == 1
    async with sem:
        assert sem.locked()

    other: list[Any] = []

    def run_other_loop() -> None:
        async def grab() -> None:
            other.append(q.sms_query_semaphore())

        asyncio.run(grab())

    await asyncio.to_thread(run_other_loop)
    assert other[0] is not sem


# ── 설정 로더 ───────────────────────────────────────────────────────────
def _load(tmp_path: Path, value: Any) -> Any:
    base = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    base["sms_metric_source"] = value
    p = tmp_path / "biz_service.yaml"
    p.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")
    return load_biz_service_config(p)


def test_repo_yaml_sms_source_is_empty_until_w0_8() -> None:
    raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    assert set(raw["sms_metric_source"]) == {"cpu", "memory", "filesystem"}
    assert all(v == {"resource_type": "", "definition_name": ""}
               for v in raw["sms_metric_source"].values())
    s = load_biz_service_config()
    assert s.sms_metric_source == {} and s.warnings == ()
    assert "W0-8" in DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")


def test_sms_source_parsed_and_bad_entries_dropped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        s = _load(tmp_path, {
            "cpu": {"resource_type": "server.Cpus", "definition_name": "Utilization"},
            "memory": {"resource_type": "server.Memory", "definition_name": ""},
            "filesystem": {"resource_type": "server.Cpus", "definition_name": "Utilization"},
            "disk": {"resource_type": "a", "definition_name": "b"},
        })
    assert s.sms_metric_source == {"cpu": ("server.Cpus", "Utilization")}
    text = " ".join(s.warnings)
    assert "sms_metric_source.memory" in text and "sms_metric_source.filesystem" in text
    assert "같은 원천 쌍" in text and "sms_metric_source.disk" in text


def test_sms_source_not_mapping_warns(tmp_path: Path) -> None:
    s = _load(tmp_path, ["cpu"])
    assert s.sms_metric_source == {} and any("sms_metric_source" in w for w in s.warnings)


# ── 기동 배선 ───────────────────────────────────────────────────────────
class _Cache:
    def __init__(self) -> None:
        self.redis = MagicMock()


@pytest.mark.parametrize("ng", [
    SimpleNamespace(biz_monitor_metrics_active=False),
    SimpleNamespace(),
    SimpleNamespace(biz_monitor_metrics_active=1),
])
async def test_flag_off_starts_no_task(ng: Any) -> None:
    before = len(asyncio.all_tasks())
    assert c.start_biz_monitor_collector(SimpleNamespace(noise_gate=ng), cache=_Cache()) is None
    assert len(asyncio.all_tasks()) == before


async def test_magicmock_and_real_default_config_start_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.config import NoiseGateConfig

    for key in ("NOISE_BIZ_SERVICE_ENABLED", "NOISE_BIZ_MONITOR_ENABLED",
                "NOISE_BIZ_MONITOR_METRICS_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    assert c.start_biz_monitor_collector(MagicMock(), cache=_Cache()) is None
    cfg = SimpleNamespace(noise_gate=NoiseGateConfig(_env_file=None))  # type: ignore[call-arg]
    assert c.start_biz_monitor_collector(cfg, cache=_Cache()) is None


def test_lifespan_guards_collector_with_metrics_flag() -> None:
    from src.api import server

    src = inspect.getsource(server.lifespan)
    guard = src.index('"biz_monitor_metrics_active", False) is True')
    assert guard < src.index("start_biz_monitor_collector(") < src.index("yield")
    assert src.index("await biz_monitor_collector.aclose()") < src.index(
        'await _close_quietly(biz_service_redis, "aclose")')


async def test_flag_on_starts_and_stops_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from noise_gate.infrastructure import apm_gateway_client, apm_host_map_sql_scanners

    async def _never(db_id: str, sql: str) -> list[dict[str, Any]]:
        raise AssertionError("SQL 0")

    monkeypatch.setattr(apm_host_map_sql_scanners, "registry_executor", lambda cfg: _never)
    monkeypatch.setattr(apm_gateway_client, "build_apm_gateway_client", lambda cfg: None)
    ng = SimpleNamespace(
        biz_monitor_metrics_active=True, biz_monitor_max_services=20,
        biz_monitor_max_apm_targets=100, biz_monitor_apm_interval_seconds=60,
        biz_monitor_sms_interval_seconds=600, biz_monitor_alarm_window_minutes=60,
        decision_store_path="",
    )

    class _Redis:
        async def set(self, *a: Any, **k: Any) -> bool:
            return True

        async def exists(self, key: str) -> int:
            return 0  # 보는 사람 없음

        async def eval(self, *a: Any) -> int:
            return 1

    cache = SimpleNamespace(redis=_Redis())
    col = c.start_biz_monitor_collector(SimpleNamespace(noise_gate=ng), cache=cache)
    assert col is not None
    task = col._task
    assert task is not None and not task.done()
    await asyncio.sleep(0)
    await col.aclose()
    assert task.done()
