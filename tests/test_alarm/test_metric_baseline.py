"""동적 baseline 이상탐지 어댑터 테스트 (Plan 60 E3 · §11).

- **비메트릭 알람(매핑 없음) skip→None** — classify_alarm_kind가 disk/network/log를 반환해도
  METRIC_SOURCE_BY_KIND 화이트리스트가 걸러 DB 접근조차 안 함(Known Mistakes 2026-06-29 E2).
- 해소 이벤트·미등록 db_id·히스토리 부족·조회 실패 → None(graceful).
- CPU 메트릭 + 충분한 합성 시계열(스파이크) → 상향 후보 심각도.
- 읽기전용 SELECT 단일문 검증.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.infrastructure.polestar_metric_baseline import (
    PolestarMetricBaselineAdapter,
    build_metric_series_sql,
)

REF = datetime(2026, 7, 22, 10, 0, 0)
PERIOD = 24


def make_event(*, resource_type="server.Cpus", alarm_name="CPU Utilization",
               severity=2, is_clear=False, db_id="polestar_cm_gp",
               server_name="SV-WEB-001") -> AlarmEvent:
    return AlarmEvent(
        db_id=db_id, server_name=server_name, hostname="h1", ip_address="10.0.0.1",
        resource_ancestry="/Servers/SV/Cpus", alarm_id="A-1", severity=severity,
        alarm_status="NOT_ACK", resource_type=resource_type, resource_name="cpu0",
        alarm_name=alarm_name, alarm_time=REF, conditions=">90", condition_log="95",
        is_clear=is_clear,
    )


def make_cfg(**over) -> SimpleNamespace:
    base = dict(
        anomaly_z_high=3.0, anomaly_min_periods=3,
        anomaly_baseline_cache_ttl_seconds=3600, noise_context_timeout_seconds=3.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    async def execute_sql(self, sql):
        self.last_sql = sql
        return _FakeResult(self._rows)


class _FakeClientCtx:
    """async with registry.get_client(db_id) as client 컨텍스트 대역."""

    def __init__(self, client):
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, *exc):
        return False


class _FakeRegistry:
    """is_registered/get_client을 흉내내고 get_client 호출 여부를 기록한다."""

    def __init__(self, rows, registered=True):
        self._client = _FakeClient(rows)
        self._registered = registered
        self.get_client_calls = 0

    def is_registered(self, db_id):
        return self._registered

    def get_client(self, db_id):
        self.get_client_calls += 1
        return _FakeClientCtx(self._client)


def _spike_rows() -> list[dict]:
    """DESC(최신 우선) 시계열 행 — hour 3만 90, 나머지 20, 최신값은 큰 스파이크(150)."""
    total = 5 * PERIOD + 1  # 121점 (baseline 120 >= 72, +최신 관측 1)
    asc: list[float] = []
    for i in range(total - 1):
        h = i % PERIOD
        asc.append((90.0 if h == 3 else 20.0) + (i % 5 - 2) * 0.3)
    asc.append(150.0)  # 최신 관측 = 실제 스파이크
    # 어댑터는 DESC로 조회 후 뒤집으므로, 여기서는 DESC(역순)로 제공한다.
    return [{"stat_date": str(i), "avg_val": v} for i, v in enumerate(reversed(asc))]


class TestWhitelistGating:
    async def test_disk_kind_skipped_without_db_access(self):
        # classify_alarm_kind('server.Disk')='disk'이지만 매핑 부재 → None, DB 미접근.
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event(resource_type="server.Disk", alarm_name="Disk Full")
        result = await adapter.compute_severity(ev, make_cfg(), redis_client=None)
        assert result is None
        assert reg.get_client_calls == 0  # 화이트리스트가 DB 접근 전에 차단

    async def test_log_kind_skipped(self):
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event(resource_type="server.LogMonitor", alarm_name="로그 패턴")
        assert await adapter.compute_severity(ev, make_cfg(), None) is None
        assert reg.get_client_calls == 0

    async def test_network_kind_skipped(self):
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event(resource_type="server.Network", alarm_name="traffic")
        assert await adapter.compute_severity(ev, make_cfg(), None) is None
        assert reg.get_client_calls == 0


class TestGracefulSkips:
    async def test_clear_event_skipped(self):
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event(severity=0, is_clear=True)
        assert await adapter.compute_severity(ev, make_cfg(), None) is None
        assert reg.get_client_calls == 0

    async def test_unregistered_db_skipped(self):
        reg = _FakeRegistry(_spike_rows(), registered=False)
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event()
        assert await adapter.compute_severity(ev, make_cfg(), None) is None
        assert reg.get_client_calls == 0

    async def test_insufficient_history_returns_none(self):
        # 40행(<72) → baseline 부족 → None.
        rows = [{"stat_date": str(i), "avg_val": 20.0} for i in range(40)]
        reg = _FakeRegistry(rows)
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        assert await adapter.compute_severity(make_event(), make_cfg(), None) is None

    async def test_query_failure_returns_none(self):
        class _BoomClient:
            async def execute_sql(self, sql):
                raise RuntimeError("db down")

        class _BoomReg:
            def is_registered(self, db_id):
                return True

            def get_client(self, db_id):
                return _FakeClientCtx(_BoomClient())

        adapter = PolestarMetricBaselineAdapter(_BoomReg(), SimpleNamespace())
        assert await adapter.compute_severity(make_event(), make_cfg(), None) is None


class TestEscalation:
    async def test_cpu_spike_escalates(self):
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        sev = await adapter.compute_severity(make_event(), make_cfg(), redis_client=None)
        assert sev == 3  # 최신 관측 150 >> 계절 baseline → 상향 후보 3

    async def test_memory_kind_uses_memory_source(self):
        reg = _FakeRegistry(_spike_rows())
        adapter = PolestarMetricBaselineAdapter(reg, SimpleNamespace())
        ev = make_event(resource_type="server.Memory", alarm_name="Memory Utilization")
        sev = await adapter.compute_severity(ev, make_cfg(), None)
        assert sev == 3
        assert "server.Memory" in reg._client.last_sql


class TestSql:
    def test_read_only_single_select(self):
        sql = build_metric_series_sql(
            "polestar_cm_gp", "SV-1", "server.Cpus", "Utilization", 120
        )
        lowered = sql.lower()
        assert lowered.strip().startswith("select")
        for forbidden in ("insert", "update", "delete", "drop", ";"):
            assert forbidden not in lowered
        assert "polestar.cmm_metric_stat_h" in sql
        assert "s.resource_id = child.id" in sql
        assert "s.definition_name = 'Utilization'" in sql

    def test_server_name_escaped(self):
        sql = build_metric_series_sql(
            "polestar_cm_gp", "SV'; DROP", "server.Cpus", "Utilization", 120
        )
        assert "SV''; DROP" in sql  # 작은따옴표 이스케이프(인젝션 방어)
