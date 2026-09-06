"""사건 구간 앵커 조회 도구 (plans/50 G1·G2·G3 · SPEC-incident-window-tools · D-197).

핵심 회귀 형태: **인자를 지정하지 않으면 생성 SQL이 2026-09-02 스냅샷과 문자열 동일**하다.
스냅샷 리터럴은 변경 직전 `build_*_sql` 실행 결과를 그대로 박은 것이다(자기 델타 검증).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

try:
    from mcp_server import polestar_tools as pt
    from mcp_server import promql_tools as pq
    HAS_MCP = True
except ImportError:  # pragma: no cover
    HAS_MCP = False

pytestmark = pytest.mark.skipif(not HAS_MCP, reason="mcp 패키지가 설치되지 않음")

REF = "2026-09-01T14:00:00"

_SNAP_AH_PG = "SELECT\n    CA.ID AS alarm_id,\n    CA.CTIME AS alarm_time,\n    CA.ALARMSEVERITY AS severity,\n    CA.CURRENTALARMSTATUS AS alarm_status,\n    CR.NAME AS resource_name\nFROM polestar.cmm_alarm CA\nJOIN polestar.cmm_alarm_def D ON CA.DEFINITION_ID = D.ID\nJOIN polestar.cmm_resource CR ON CA.RESOURCE_ID = CR.ID\nJOIN polestar.cmm_resource SVR ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)\n                     AND SVR.RESOURCE_TYPE = 'server.Server'\n                     AND SVR.DTIME IS NULL\nWHERE CR.DTIME IS NULL\n  AND CA.ALARMSEVERITY IN (0, 1, 2, 3)\n  AND D.NAME = 'CPU'\n  AND SVR.NAME = 'web01'\n  AND CA.CTIME >= NOW() - INTERVAL '24 hours'\nORDER BY CA.CTIME DESC\nLIMIT 100"
_SNAP_AH_DB2 = "SELECT\n    CA.ID AS alarm_id,\n    CA.CTIME AS alarm_time,\n    CA.ALARMSEVERITY AS severity,\n    CA.CURRENTALARMSTATUS AS alarm_status,\n    CR.NAME AS resource_name\nFROM POLESTAR.cmm_alarm CA\nJOIN POLESTAR.cmm_alarm_def D ON CA.DEFINITION_ID = D.ID\nJOIN POLESTAR.cmm_resource CR ON CA.RESOURCE_ID = CR.ID\nJOIN POLESTAR.cmm_resource SVR ON SVR.ID = COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)\n                     AND SVR.RESOURCE_TYPE = 'server.Server'\n                     AND SVR.DTIME IS NULL\nWHERE CR.DTIME IS NULL\n  AND CA.ALARMSEVERITY IN (0, 1, 2, 3)\n  AND D.NAME = 'CPU'\n  AND SVR.NAME = 'web01'\n  AND CA.CTIME >= CURRENT TIMESTAMP - 24 HOURS\n  AND CA.ID <> 999\nORDER BY CA.CTIME DESC\nFETCH FIRST 100 ROWS ONLY"
_SNAP_MT_PG = "SELECT\n    s.stat_date AS stat_date,\n    s.avg_val AS avg_val,\n    s.min_val AS min_val,\n    s.max_val AS max_val\nFROM polestar.cmm_resource svr\nJOIN polestar.cmm_resource child ON child.platform_resource_id = svr.id\nJOIN polestar.cmm_metric_stat_h s ON s.resource_id = child.id\nWHERE svr.resource_type = 'server.Server'\n  AND svr.name = 'web01'\n  AND svr.dtime IS NULL\n  AND child.resource_type = 'server.Cpus'\n  AND child.dtime IS NULL\n  AND s.definition_name = 'Utilization'\nORDER BY s.stat_date DESC\nLIMIT 24"
_SNAP_MT_DB2 = "SELECT\n    s.stat_date AS stat_date,\n    s.avg_val AS avg_val,\n    s.min_val AS min_val,\n    s.max_val AS max_val\nFROM POLESTAR.cmm_resource svr\nJOIN POLESTAR.cmm_resource child ON child.platform_resource_id = svr.id\nJOIN POLESTAR.cmm_metric_stat_d s ON s.resource_id = child.id\nWHERE svr.resource_type = 'server.Server'\n  AND svr.name = 'web01'\n  AND svr.dtime IS NULL\n  AND child.resource_type = 'server.Memory'\n  AND child.dtime IS NULL\n  AND s.definition_name = 'Utilization'\nORDER BY s.stat_date DESC\nFETCH FIRST 12 ROWS ONLY"


# ── 회귀 0: 미지정 경로는 스냅샷과 문자열 동일 ────────────────────────


def test_default_sql_unchanged_alarm_history():
    assert pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100) == _SNAP_AH_PG
    assert pt.build_alarm_history_sql(True, "web01", "CPU", 24, "999", 100) == _SNAP_AH_DB2


def test_default_sql_unchanged_metric_trend():
    assert pt.build_metric_trend_sql(False, "web01", "cpu", "h", 24) == _SNAP_MT_PG
    assert pt.build_metric_trend_sql(True, "web01", "memory", "d", 12) == _SNAP_MT_DB2


def test_explicit_none_equals_default():
    a = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100,
                                   reference_time=None, lookback_minutes=None)
    assert a == _SNAP_AH_PG
    m = pt.build_metric_trend_sql(False, "web01", "cpu", "h", 24,
                                  reference_time=None, lookback_minutes=None, baseline_periods=None)
    assert m == _SNAP_MT_PG


# ── reference_time 파싱 · 거부 ──────────────────────────────────────


def test_reference_time_parsed_iso():
    assert pt.parse_reference_time(REF) == datetime(2026, 9, 1, 14, 0, 0)
    assert pt.parse_reference_time("2026-09-01 14:00:00") == datetime(2026, 9, 1, 14, 0, 0)


@pytest.mark.parametrize("bad", ["어제 14시", "2026-09-01' OR 1=1 --", "", "1756735200", None])
def test_reference_time_rejected(bad):
    with pytest.raises(ValueError, match="ISO 8601"):
        pt.parse_reference_time(bad)


def test_reference_time_offset_stripped_to_wall_clock_for_sql():
    """오프셋이 붙어도 폴스타 SQL은 벽시계 값을 쓴다(변환하지 않는다)."""
    sql = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100,
                                     reference_time="2026-09-01T14:00:00+09:00", lookback_minutes=60)
    assert "TIMESTAMP '2026-09-01 14:00:00'" in sql


# ── 앵커 alarm_history ─────────────────────────────────────────────


def test_anchored_alarm_history_pg():
    sql = pt.build_alarm_history_sql(False, "web01", "CPU", 24, None, 100,
                                     reference_time=REF, lookback_minutes=60)
    assert "NOW()" not in sql
    assert "CA.CTIME >= TIMESTAMP '2026-09-01 13:00:00'" in sql
    assert "CA.CTIME <= TIMESTAMP '2026-09-01 14:00:00'" in sql
    assert "LIMIT 100" in sql


def test_anchored_alarm_history_db2():
    sql = pt.build_alarm_history_sql(True, "web01", "CPU", 24, None, 100,
                                     reference_time=REF, lookback_minutes=30)
    assert "CURRENT TIMESTAMP" not in sql
    assert "CA.CTIME >= TIMESTAMP('2026-09-01 13:30:00')" in sql
    assert "CA.CTIME <= TIMESTAMP('2026-09-01 14:00:00')" in sql
    assert "FETCH FIRST 100 ROWS ONLY" in sql


def test_anchored_alarm_history_lookback_defaults_to_hours():
    sql = pt.build_alarm_history_sql(False, "web01", "CPU", 2, None, 100, reference_time=REF)
    assert "CA.CTIME >= TIMESTAMP '2026-09-01 12:00:00'" in sql


# ── 앵커 metric_trend (stat_date varchar 포맷) ──────────────────────


def test_anchored_metric_trend_hourly():
    sql = pt.build_metric_trend_sql(False, "web01", "cpu", "h", 24,
                                    reference_time=REF, lookback_minutes=60)
    assert "s.stat_date >= '2026090113'" in sql
    assert "s.stat_date <= '2026090114'" in sql
    assert "ORDER BY s.stat_date DESC" in sql


def test_anchored_metric_trend_with_baseline():
    sql = pt.build_metric_trend_sql(False, "web01", "cpu", "h", 24,
                                    reference_time=REF, lookback_minutes=60, baseline_periods=24)
    assert "s.stat_date >= '2026083113'" in sql
    assert "s.stat_date <= '2026090114'" in sql


# m: lookback 기본 1단위(30일) → 사건 구간 202608~202609, baseline 6개월 → 202602.
@pytest.mark.parametrize("gran,lo,hi", [("d", "20260825", "20260901"), ("m", "202602", "202609")])
def test_anchored_metric_trend_daily_monthly_formats(gran, lo, hi):
    sql = pt.build_metric_trend_sql(True, "web01", "memory", gran, 12,
                                    reference_time=REF, baseline_periods=6)
    assert f"s.stat_date >= '{lo}'" in sql and f"s.stat_date <= '{hi}'" in sql


def test_incident_window_meta_month_boundary():
    w = pt.incident_window("2026-01-15T10:00:00", None, "m", 2)
    assert w["stat_date_to"] == "202601"
    assert w["stat_date_incident_from"] == "202512"
    assert w["stat_date_from"] == "202510"
    assert w["reference_time"] == "2026-01-15T10:00:00"


def test_incident_window_meta_hourly_default_lookback():
    w = pt.incident_window(REF, None, "h", None)
    assert w["incident_from"] == "2026-09-01T13:00:00"
    assert w["baseline_from"] is None
    assert w["stat_date_from"] == "2026090113"


# ── 신설: 구간 내 전 알람 SQL ───────────────────────────────────────


def test_incident_alarms_sql_no_alarm_name_filter():
    sql = pt.build_incident_alarms_sql(False, "web01", REF, 60, None, 100)
    assert "D.NAME AS alarm_name" in sql
    assert "CR.RESOURCE_TYPE AS resource_type" in sql
    assert "D.NAME =" not in sql
    assert "COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)" in sql
    assert "CA.ALARMSEVERITY IN (0, 1, 2, 3)" in sql
    assert "resource_conf_id" not in sql.lower()
    assert "ORDER BY CA.CTIME ASC" in sql
    assert "CA.CTIME >= TIMESTAMP '2026-09-01 13:00:00'" in sql
    assert "NOW()" not in sql


def test_incident_alarms_sql_optional_alarm_name_and_db2():
    sql = pt.build_incident_alarms_sql(True, "a'b", REF, 60, "CPU", 50)
    assert "D.NAME = 'CPU'" in sql
    assert "SVR.NAME = 'a''b'" in sql
    assert "POLESTAR.cmm_alarm" in sql and "FETCH FIRST 50 ROWS ONLY" in sql
    assert "TIMESTAMP('2026-09-01 13:00:00')" in sql


# ── 도구 레벨 ────────────────────────────────────────────────────────


class _CaptureMCP:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *a: Any, **k: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


class _FakePool:
    def __init__(self, source_type: str = "postgresql") -> None:
        self.executed: list[str] = []
        self._type = source_type
        self._src = SimpleNamespace(readonly=True, max_rows=100)

    def is_source_active(self, source: str) -> bool:
        return source == "s"

    def get_active_sources(self) -> list[str]:
        return ["s"]

    def get_source_type(self, source: str) -> str:
        return self._type

    def get_source_config(self, source: str) -> Any:
        return self._src

    async def execute(self, source: str, sql: str) -> list[dict]:
        self.executed.append(sql)
        return [{"alarm_id": 1}]


def _ctx(pool: _FakePool) -> SimpleNamespace:
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool_manager": pool}))


def _tools() -> dict[str, Any]:
    cap = _CaptureMCP()
    pt.register_polestar_tools(cap)
    return cap.tools


def test_incident_alarms_tool_registered_and_returns_window():
    tools = _tools()
    assert "polestar_incident_alarms" in tools
    pool = _FakePool()
    out = json.loads(asyncio.run(tools["polestar_incident_alarms"](
        source="s", server_name="web01", reference_time=REF, lookback_minutes=60, ctx=_ctx(pool)
    )))
    assert out["source_kind"] == "polestar_db" and out["row_count"] == 1
    assert out["window"]["reference_time"] == REF
    assert out["window"]["incident_from"] == "2026-09-01T13:00:00"
    assert "ORDER BY CA.CTIME ASC" in pool.executed[0]


def test_incident_alarms_tool_rejects_bad_time_before_sql():
    tools = _tools()
    pool = _FakePool()
    out = json.loads(asyncio.run(tools["polestar_incident_alarms"](
        source="s", server_name="web01", reference_time="어제", lookback_minutes=60, ctx=_ctx(pool)
    )))
    assert "ISO 8601" in out["error"] and pool.executed == []


def test_metric_trend_tool_anchored_returns_window_and_widens_limit():
    tools = _tools()
    pool = _FakePool()
    out = json.loads(asyncio.run(tools["polestar_metric_trend"](
        source="s", server_name="web01", kind="cpu", granularity="h", periods=2,
        reference_time=REF, lookback_minutes=60, baseline_periods=24, ctx=_ctx(pool)
    )))
    assert out["window"]["stat_date_from"] == "2026083113"
    assert "LIMIT 26" in pool.executed[0]


def test_metric_trend_tool_default_has_no_window():
    tools = _tools()
    pool = _FakePool()
    out = json.loads(asyncio.run(tools["polestar_metric_trend"](
        source="s", server_name="web01", kind="cpu", ctx=_ctx(pool)
    )))
    assert "window" not in out
    assert pool.executed[0] == _SNAP_MT_PG


def test_alarm_history_tool_accepts_anchor():
    tools = _tools()
    pool = _FakePool()
    out = json.loads(asyncio.run(tools["polestar_alarm_history"](
        source="s", server_name="web01", alarm_name="CPU", reference_time=REF, lookback_minutes=30, ctx=_ctx(pool)
    )))
    assert out["window"]["incident_from"] == "2026-09-01T13:30:00"
    assert "NOW()" not in pool.executed[0]


# ── PromQL range end 앵커 ───────────────────────────────────────────


def test_prom_range_end_anchored(monkeypatch):
    import httpx

    captured: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append(req)
        return httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}})

    cfg = pq.PrometheusConfig(url="http://prom:9090")

    async def _do():
        async with httpx.AsyncClient(base_url=cfg.url, transport=httpx.MockTransport(handler)) as c:
            return await pq.run_metric_range(
                cfg, "web-01", "node_load1", "5m", "30s", client=c,
                reference_time="2026-09-01T14:00:00+00:00",
            )

    out = json.loads(asyncio.run(_do()))
    assert out["source_kind"] == "prometheus"
    end = float(captured[0].url.params["end"])
    assert end == datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc).timestamp()
    assert round(end - float(captured[0].url.params["start"])) == 300


def test_prom_range_bad_reference_time_rejected():
    out = json.loads(asyncio.run(pq.run_metric_range(
        pq.PrometheusConfig(url="http://prom:9090"), "web-01", "node_load1", "5m", "30s",
        reference_time="어제",
    )))
    assert "ISO 8601" in out["error"]
