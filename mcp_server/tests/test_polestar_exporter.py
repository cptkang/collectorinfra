"""polestar_exporter.py 테스트 — 폴스타 → OpenMetrics 브리지 (plans/92 트랙 B-2 · O5 · §4.5 [v3]).

DB 없이 페이크 풀로 검증한다: 일괄 SQL 빌더(PG/DB2 방언·SELECT 전용·security 검증) ·
노출 골든(OpenMetrics 1.0 / text 0.0.4) · 샘플 타임스탬프 부재 · disk_io 제외 · 행 상한 절단 gauge ·
소스 실패 격리(source_up 0) · 미등록/비활성/비폴스타 소스 · 전용 지연 풀(생성 1회 · 종료 시 close) ·
라우트 게이팅(off 404 / on 200) · Bearer · 도구 목록 불변.
픽스처 Prometheus 실 스크레이프는 RUN_DOCKER_IT=1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any

import pytest

try:
    from mcp_server.config import (
        AppServerConfig,
        OpenMetricsBridgeSource,
        OpenMetricsConfig,
        ServerConfig,
        SourceConfig,
    )
    from mcp_server.om_exposition import render_exposition
    from mcp_server.security import validate_polestar_domain, validate_readonly
    from mcp_server.server import build_asgi_app, create_server
    from prometheus_client.openmetrics.parser import text_string_to_metric_families
    from prometheus_client.utils import floatToGoString
    from starlette.testclient import TestClient

    from mcp_server import polestar_exporter as pe
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

pytestmark = pytest.mark.skipif(not HAS_DEPS, reason="mcp/prometheus-client 미설치")

PROM_ACCEPT = (
    "application/openmetrics-text;version=1.0.0,"
    "application/openmetrics-text;version=0.0.1;q=0.75,"
    "text/plain;version=0.0.4;q=0.5,*/*;q=0.1"
)
NOW = datetime(2026, 9, 22, 14, 35)  # 하한 = 24시간 전 버킷 2026092114


@pytest.fixture(autouse=True)
def _no_sql_file_log(monkeypatch):
    """브리지가 풀 생성 때 부르는 SQL 파일 로거 초기화가 저장소 logs/에 쓰지 않게 한다."""
    monkeypatch.setenv("OBS_SQL_LOG_ENABLED", "false")


# =====================================================================
# 페이크 풀 · 설정 헬퍼
# =====================================================================


class FakePool:
    """DBPoolManager 계약(initialize·execute·close_all)만 구현한 대역.

    ``data[source]``가 예외면 그 소스의 execute는 그 예외를 올린다. 아니면 SQL 종류(알람 집계·
    사용률 최신 1행·서버 info)에 따라 행 목록을 돌려준다.
    """

    def __init__(self, sources: list, data: dict[str, Any], created: list) -> None:
        self.sources = sources
        self.data = data
        self.initialized = 0
        self.closed = 0
        self.executed: list[tuple[str, str]] = []
        created.append(self)

    async def initialize(self) -> None:
        self.initialized += 1

    async def execute(self, source: str, sql: str) -> list[dict]:
        self.executed.append((source, sql))
        spec = self.data[source]
        if isinstance(spec, Exception):
            raise spec
        lowered = sql.lower()
        if "cmm_alarm_active" in lowered:
            return [dict(r) for r in spec.get("alarms", [])]
        if "row_number" in lowered:
            return [dict(r) for r in spec.get("util", [])]
        return [dict(r) for r in spec.get("servers", [])]

    async def close_all(self) -> None:
        self.closed += 1


def _factory(data: dict[str, Any], created: list):
    return lambda sources: FakePool(sources, data, created)


def _config(
    bridge: list[tuple[str, str]],
    sources: list[SourceConfig],
    *,
    ttl: int = 300,
    expose: bool = True,
) -> AppServerConfig:
    return AppServerConfig(
        server=ServerConfig(name="bridge-test"),
        sources=sources,
        openmetrics=OpenMetricsConfig(
            expose_polestar_exporter=expose,
            bridge_cache_seconds=ttl,
            bridge_sources=[OpenMetricsBridgeSource(name=n, zone=z) for n, z in bridge],
        ),
    )


def _pg(name: str, max_rows: int = 10000) -> SourceConfig:
    return SourceConfig(
        name=name, type="postgresql", connection="postgresql://x", max_rows=max_rows
    )


def _db2(name: str, max_rows: int = 10000) -> SourceConfig:
    return SourceConfig(name=name, type="db2", connection="DATABASE=x", max_rows=max_rows)


def _util(server: str, kind: str, stat_date: str, value: Any) -> dict:
    resource_type, definition_name = {
        "cpu": ("server.Cpus", "Utilization"),
        "memory": ("server.Memory", "Utilization"),
        "filesystem": ("server.FileSystems", "Utilization"),
        "disk_io": ("server.Disks", "MaxIORate"),
    }[kind]
    return {
        "server_name": server, "resource_type": resource_type,
        "definition_name": definition_name, "stat_date": stat_date, "avg_val": value,
    }


GP_DATA = {
    "servers": [
        {"server_name": "svr-web-01", "ipaddress": "192.0.2.10", "os": "Linux"},
        {"server_name": "svr-db-01", "ipaddress": None, "os": None},
    ],
    "util": [
        _util("svr-web-01", "cpu", "2026092214", 37.5),
        _util("svr-web-01", "memory", "2026092214", 61.25),
        _util("svr-web-01", "filesystem", "2026092213", 80),
    ],
    "alarms": [
        {"server_name": "svr-web-01", "severity": 3, "alarm_count": 2},
        {"server_name": "svr-web-01", "severity": 1, "alarm_count": 1},
    ],
}


def _samples(text: str) -> dict[str, list]:
    """OpenMetrics 1.0 본문을 strict 파서로 읽어 {패밀리명: 샘플 목록}으로 만든다."""
    return {f.name: list(f.samples) for f in text_string_to_metric_families(text)}


async def _collect_text(bridge: pe.PolestarBridge, accept: str = PROM_ACCEPT) -> str:
    body, _ = render_exposition(await bridge.cache.get(), accept)
    return body.decode("utf-8")


# =====================================================================
# 일괄 SQL 빌더 — 방언 · 읽기 전용
# =====================================================================


def _all_sql(is_db2: bool) -> dict[str, str]:
    return {
        "info": pe.build_server_info_sql(is_db2, 10000),
        "util": pe.build_latest_utilization_sql(is_db2, "2026092114", 10000),
        "alarm": pe.build_active_alarm_count_sql(is_db2, 10000),
    }


class TestSqlBuilders:
    """PG/DB2 방언 분기와 안전 계약."""

    @pytest.mark.parametrize("is_db2", [False, True])
    def test_select_only_and_security_validators_pass(self, is_db2):
        """모든 SQL은 SELECT 단일문이고 security.py 읽기 전용·폴스타 도메인 검증을 통과한다."""
        for kind, sql in _all_sql(is_db2).items():
            assert sql.lstrip().upper().startswith("SELECT"), kind
            validate_readonly(sql)
            validate_polestar_domain(sql)

    def test_pg_schema_and_limit(self):
        """PostgreSQL: 소문자 polestar. 한정 · LIMIT · FETCH FIRST 없음."""
        for kind, sql in _all_sql(False).items():
            assert "polestar.cmm_resource" in sql, kind
            assert "POLESTAR." not in sql, kind
            assert sql.rstrip().endswith("LIMIT 10000"), kind
            assert "FETCH FIRST" not in sql, kind

    def test_db2_schema_and_fetch_first(self):
        """DB2: 대문자 POLESTAR. 한정 · FETCH FIRST · LIMIT 없음."""
        for kind, sql in _all_sql(True).items():
            assert "POLESTAR.cmm_resource" in sql, kind
            assert "polestar." not in sql, kind
            assert sql.rstrip().endswith("FETCH FIRST 10000 ROWS ONLY"), kind
            assert "LIMIT" not in sql, kind

    @pytest.mark.parametrize("is_db2", [False, True])
    def test_no_resource_conf_join_no_lookup_tables(self, is_db2):
        """RESOURCE_CONF_ID 조인 금지(D-022) · cmm_os/cmm_vendor lookup 미참조(D-028)."""
        for kind, sql in _all_sql(is_db2).items():
            lowered = sql.lower()
            assert "resource_conf_id" not in lowered, kind
            assert "cmm_os" not in lowered and "cmm_vendor" not in lowered, kind

    def test_utilization_is_batch_latest_per_server_kind(self):
        """서버×kind 최신 1행을 ROW_NUMBER로 한 번에 자른다 — stat_date 하한은 리터럴."""
        sql = pe.build_latest_utilization_sql(False, "2026092114", 500)
        assert "ROW_NUMBER() OVER" in sql
        assert "PARTITION BY svr.name, child.resource_type, s.definition_name" in sql
        assert "ORDER BY s.stat_date DESC" in sql
        assert "WHERE latest.rn = 1" in sql
        assert "s.stat_date >= '2026092114'" in sql
        assert "polestar.cmm_metric_stat_h" in sql
        assert "svr.name = " not in sql  # 서버 1대용 필터가 없다(일괄)

    @pytest.mark.parametrize("is_db2", [False, True])
    def test_utilization_kinds_exclude_disk_io(self, is_db2):
        """사용률 SQL은 퍼센트 kind 3종만 — server.Disks/MaxIORate는 조회하지 않는다."""
        sql = pe.build_latest_utilization_sql(is_db2, "2026092114", 10)
        for resource_type in ("server.Cpus", "server.Memory", "server.FileSystems"):
            assert f"child.resource_type = '{resource_type}'" in sql
        assert "server.Disks" not in sql and "MaxIORate" not in sql

    def test_alarm_sql_counts_active_by_server_and_severity(self):
        """활성 알람 = cmm_alarm_active 행 · COALESCE 승격 조인 · 서버·심각도 GROUP BY."""
        sql = pe.build_active_alarm_count_sql(False, 100)
        assert "FROM polestar.cmm_alarm_active a" in sql
        assert "COALESCE(cr.platform_resource_id, cr.id)" in sql
        assert "GROUP BY svr.name, a.alarmseverity" in sql
        assert "COUNT(*) AS alarm_count" in sql
        assert "currentalarmstatus" not in sql.lower()

    def test_info_sql_one_row_per_server_via_hostname_bridge(self):
        """info는 서버당 1행(GROUP BY svr.name) · OS는 Hostname 브릿지 EAV OSType."""
        sql = pe.build_server_info_sql(False, 100)
        assert "GROUP BY svr.name" in sql
        assert "p_host.name = 'Hostname'" in sql
        assert "p_host.stringvalue_short = svr.hostname" in sql
        assert "p_os.name = 'OSType'" in sql

    def test_lower_bound_literal_is_escaped(self):
        """하한은 _sql_literal로 보간된다(따옴표 이중화)."""
        sql = pe.build_latest_utilization_sql(False, "x' OR '1'='1", 10)
        assert "s.stat_date >= 'x'' OR ''1''=''1'" in sql


class TestStatDateConvention:
    """stat_date 하한·epoch 규약(로컬 벽시계)."""

    def test_lower_bound_24h(self):
        """하한 = now − 24시간의 시간 버킷 문자열."""
        assert pe.stat_date_lower_bound(NOW) == "2026092114"
        assert pe.stat_date_lower_bound(NOW, hours=3) == "2026092211"

    def test_epoch_is_bucket_start_local(self):
        """버킷 시작 시각을 로컬 시각으로 해석한 epoch 초."""
        assert pe.stat_date_to_epoch("2026092214") == datetime(2026, 9, 22, 14).timestamp()
        assert pe.stat_date_to_epoch(" 2026092214 ") == datetime(2026, 9, 22, 14).timestamp()

    @pytest.mark.parametrize("bad", ["stat_date_1", "20260922", "", None])
    def test_malformed_raises(self, bad):
        """형식이 YYYYMMDDHH가 아니면 ValueError."""
        with pytest.raises(ValueError):
            pe.stat_date_to_epoch(bad)


class TestVocabulary:
    """O6가 재사용할 어휘 상수."""

    def test_exported_kinds_are_percent_only(self):
        """노출 kind는 cpu·memory·filesystem — disk_io 제외(§4.5 [v3] 2)."""
        assert pe.EXPORTED_KINDS == ("cpu", "memory", "filesystem")
        assert "disk_io" not in pe.EXPORTED_KINDS

    def test_every_server_family_has_nodename_and_db_id(self):
        """서버별 패밀리는 전부 nodename·db_id를 가진다 — 존 간 server_name 중복 방지."""
        for labels in (pe.SERVER_INFO_LABELS, pe.UTILIZATION_LABELS, pe.ALARM_ACTIVE_LABELS):
            assert labels[:2] == ("nodename", "db_id")
        assert "ipaddress" in pe.SERVER_INFO_LABELS
        assert "ipaddress" not in pe.UTILIZATION_LABELS + pe.ALARM_ACTIVE_LABELS


# =====================================================================
# 노출 골든 — 패밀리 6종
# =====================================================================


def _golden_snapshots() -> list:
    return [
        pe.SourceSnapshot(
            db_id="polestar_cm_gp", zone="gongjon", up=True,
            servers=GP_DATA["servers"], utilization=GP_DATA["util"], alarms=GP_DATA["alarms"],
        ),
        pe.SourceSnapshot(db_id="polestar_b0", zone="bankjon", up=False),
    ]


def _ts(hour: int) -> str:
    return floatToGoString(datetime(2026, 9, 22, hour).timestamp())


# 골든 조각 — 두 형식이 공유하는 HELP 문구·샘플 줄(긴 줄은 인접 리터럴로 나눴다).
_HELP_SERVER = "폴스타 등록 서버 정보 — nodename은 폴스타 server_name(OS hostname 아님)"
_HELP_UTIL = "폴스타 시간 통계 최신 버킷의 평균 사용률(%) — kind: cpu·memory·filesystem"
_HELP_TS = "사용률 값이 속한 시간 통계 버킷의 시작 시각(epoch 초) — 신선도 판단용"
_HELP_ALARM = "폴스타 활성 알람 수(서버·심각도별)"
_HELP_TRUNC = "소스 쿼리 결과가 행 상한(max_rows)에 닿음 — 1이면 일부 시리즈가 빠졌을 수 있다"
_HELP_UP = "소스 조회 성공 여부 — 0이면 그 소스의 시리즈를 내지 않았다"

_INFO_SAMPLES = (
    'polestar_server_info{db_id="polestar_cm_gp",ipaddress="192.0.2.10",'
    'nodename="svr-web-01",os="Linux",zone="gongjon"} 1.0\n'
    'polestar_server_info{db_id="polestar_cm_gp",ipaddress="",'
    'nodename="svr-db-01",os="",zone="gongjon"} 1.0\n'
)
_UTIL_SAMPLES = (
    'polestar_metric_utilization_percent{db_id="polestar_cm_gp",kind="cpu",'
    'nodename="svr-web-01"} 37.5\n'
    'polestar_metric_utilization_percent{db_id="polestar_cm_gp",kind="memory",'
    'nodename="svr-web-01"} 61.25\n'
    'polestar_metric_utilization_percent{db_id="polestar_cm_gp",kind="filesystem",'
    'nodename="svr-web-01"} 80.0\n'
)


def _ts_samples() -> str:
    return (
        'polestar_metric_stat_timestamp_seconds{db_id="polestar_cm_gp",kind="cpu",'
        f'nodename="svr-web-01"}} {_ts(14)}\n'
        'polestar_metric_stat_timestamp_seconds{db_id="polestar_cm_gp",kind="memory",'
        f'nodename="svr-web-01"}} {_ts(14)}\n'
        'polestar_metric_stat_timestamp_seconds{db_id="polestar_cm_gp",kind="filesystem",'
        f'nodename="svr-web-01"}} {_ts(13)}\n'
    )


_TAIL_SAMPLES = (
    f"# HELP polestar_alarm_active {_HELP_ALARM}\n"
    "# TYPE polestar_alarm_active gauge\n"
    'polestar_alarm_active{db_id="polestar_cm_gp",nodename="svr-web-01",severity="3"} 2.0\n'
    'polestar_alarm_active{db_id="polestar_cm_gp",nodename="svr-web-01",severity="1"} 1.0\n'
    f"# HELP polestar_bridge_truncated {_HELP_TRUNC}\n"
    "# TYPE polestar_bridge_truncated gauge\n"
    'polestar_bridge_truncated{db_id="polestar_cm_gp"} 0.0\n'
    'polestar_bridge_truncated{db_id="polestar_b0"} 0.0\n'
    f"# HELP polestar_bridge_source_up {_HELP_UP}\n"
    "# TYPE polestar_bridge_source_up gauge\n"
    'polestar_bridge_source_up{db_id="polestar_cm_gp"} 1.0\n'
    'polestar_bridge_source_up{db_id="polestar_b0"} 0.0\n'
)


def _golden_om() -> str:
    return (
        f"# HELP polestar_server {_HELP_SERVER}\n"
        "# TYPE polestar_server info\n"
        f"{_INFO_SAMPLES}"
        f"# HELP polestar_metric_utilization_percent {_HELP_UTIL}\n"
        "# TYPE polestar_metric_utilization_percent gauge\n"
        "# UNIT polestar_metric_utilization_percent percent\n"
        f"{_UTIL_SAMPLES}"
        f"# HELP polestar_metric_stat_timestamp_seconds {_HELP_TS}\n"
        "# TYPE polestar_metric_stat_timestamp_seconds gauge\n"
        "# UNIT polestar_metric_stat_timestamp_seconds seconds\n"
        f"{_ts_samples()}"
        f"{_TAIL_SAMPLES}"
        "# EOF\n"
    )


def _golden_004() -> str:
    return (
        f"# HELP polestar_server_info {_HELP_SERVER}\n"
        "# TYPE polestar_server_info gauge\n"
        f"{_INFO_SAMPLES}"
        f"# HELP polestar_metric_utilization_percent {_HELP_UTIL}\n"
        "# TYPE polestar_metric_utilization_percent gauge\n"
        f"{_UTIL_SAMPLES}"
        f"# HELP polestar_metric_stat_timestamp_seconds {_HELP_TS}\n"
        "# TYPE polestar_metric_stat_timestamp_seconds gauge\n"
        f"{_ts_samples()}"
        f"{_TAIL_SAMPLES}"
    )


class TestExpositionGolden:
    """build_families → render_exposition 골든."""

    def test_openmetrics_golden(self):
        """OpenMetrics 1.0 — 패밀리 6종 전부 · UNIT · info 타입 · # EOF."""
        body, content_type = render_exposition(pe.build_families(_golden_snapshots()), PROM_ACCEPT)
        assert content_type.startswith("application/openmetrics-text; version=1.0.0")
        assert body.decode("utf-8") == _golden_om()

    def test_text_004_golden(self):
        """text 0.0.4 — info는 _info gauge · UNIT·# EOF 없음."""
        body, content_type = render_exposition(pe.build_families(_golden_snapshots()), "*/*")
        assert content_type == "text/plain; version=0.0.4; charset=utf-8"
        assert body.decode("utf-8") == _golden_004()

    def test_strict_parse_and_no_sample_timestamps(self):
        """1.0 출력이 strict 파서를 통과하고, 어느 샘플에도 타임스탬프가 없다."""
        body, _ = render_exposition(pe.build_families(_golden_snapshots()), PROM_ACCEPT)
        parsed = _samples(body.decode("utf-8"))
        assert set(parsed) == {
            "polestar_server", "polestar_metric_utilization_percent",
            "polestar_metric_stat_timestamp_seconds", "polestar_alarm_active",
            "polestar_bridge_truncated", "polestar_bridge_source_up",
        }
        assert all(s.timestamp is None for samples in parsed.values() for s in samples)

    def test_disk_io_and_malformed_rows_not_exposed(self, caplog):
        """disk_io 행·stat_date 형식 불량·NULL 값 행은 내지 않고 WARNING으로 건수를 남긴다."""
        snap = pe.SourceSnapshot(
            db_id="g", zone="", up=True,
            utilization=[
                _util("s1", "cpu", "2026092214", 10),
                _util("s1", "disk_io", "2026092214", 999),
                _util("s2", "cpu", "stat_date_1", 20),
                _util("s3", "cpu", "2026092214", None),
            ],
        )
        caplog.set_level(logging.WARNING, logger="mcp_server.polestar_exporter")
        body, _ = render_exposition(pe.build_families([snap]), PROM_ACCEPT)
        util = _samples(body.decode("utf-8"))["polestar_metric_utilization_percent"]
        assert [(s.labels["nodename"], s.labels["kind"]) for s in util] == [("s1", "cpu")]
        assert "3건 제외" in caplog.text


# =====================================================================
# 브리지 — 지연 풀 · 소스 실패 · 절단 · 소스 해석
# =====================================================================


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TestBridgeCollect:
    """PolestarBridge 수집 경로(페이크 풀)."""

    async def test_lazy_pool_created_once_then_closed(self):
        """첫 스크레이프 전 0개 → 첫 스크레이프에 1회 생성(활성 소스만·풀 1-1) → 종료 시 close."""
        created: list = []
        clock = _Clock()
        cfg = _config(
            [("polestar_cm_gp", "gongjon"), ("ghost", "x")],
            [_pg("polestar_cm_gp", max_rows=5000), _pg("unrelated")],
            ttl=300,
        )
        bridge = pe.PolestarBridge(
            cfg, pool_factory=_factory({"polestar_cm_gp": GP_DATA}, created),
            clock=clock, now=lambda: NOW,
        )
        assert created == []

        await bridge.cache.get()
        assert len(created) == 1
        pool = created[0]
        assert pool.initialized == 1
        # 미등록 ghost·비브리지 소스 제외
        assert [s.name for s in pool.sources] == ["polestar_cm_gp"]
        assert all((s.pool_min_size, s.pool_max_size) == (1, 1) for s in pool.sources)
        assert pool.sources[0].max_rows == 5000
        assert len(pool.executed) == 3  # info · 사용률 · 알람 — 존별 3쿼리

        clock.now += 301  # TTL 경과 → 재수집해도 풀은 재생성하지 않는다
        await bridge.cache.get()
        assert len(created) == 1 and len(pool.executed) == 6

        await bridge.aclose()
        assert pool.closed == 1
        await bridge.aclose()  # 두 번째 종료는 아무것도 하지 않는다
        assert pool.closed == 1

    async def test_cache_ttl_skips_db(self):
        """TTL 안의 재스크레이프는 SQL을 치지 않는다."""
        created: list = []
        clock = _Clock()
        bridge = pe.PolestarBridge(
            _config([("polestar_cm_gp", "gongjon")], [_pg("polestar_cm_gp")], ttl=300),
            pool_factory=_factory({"polestar_cm_gp": GP_DATA}, created),
            clock=clock,
            now=lambda: NOW,
        )
        await bridge.cache.get()
        clock.now += 299
        await bridge.cache.get()
        assert len(created[0].executed) == 3

    async def test_concurrent_scrapes_single_flight(self):
        """동시 스크레이프 5건 → 풀 1개 · 쿼리 3건(수집 1회)."""
        created: list = []
        bridge = pe.PolestarBridge(
            _config([("polestar_cm_gp", "gongjon")], [_pg("polestar_cm_gp")]),
            pool_factory=_factory({"polestar_cm_gp": GP_DATA}, created), now=lambda: NOW,
        )
        await asyncio.gather(*(bridge.cache.get() for _ in range(5)))
        assert len(created) == 1 and len(created[0].executed) == 3

    async def test_executed_sql_uses_source_dialect_and_lower_bound(self):
        """소스 엔진별 방언으로 조회하고, stat_date 하한은 주입 시계(now − 24h)로 계산한다."""
        created: list = []
        bridge = pe.PolestarBridge(
            _config([("gp", ""), ("b0", "bankjon")], [_pg("gp"), _db2("b0")]),
            pool_factory=_factory({"gp": GP_DATA, "b0": GP_DATA}, created), now=lambda: NOW,
        )
        await bridge.cache.get()
        by_source: dict[str, list[str]] = {}
        for source, sql in created[0].executed:
            by_source.setdefault(source, []).append(sql)
        assert all("FETCH FIRST" in s and "POLESTAR." in s for s in by_source["b0"])
        assert all("LIMIT" in s and "polestar." in s for s in by_source["gp"])
        assert any("s.stat_date >= '2026092114'" in s for s in by_source["gp"])

    async def test_source_failure_is_isolated_and_visible(self, caplog):
        """한 소스 조회 실패 → 그 소스는 source_up 0·시리즈 없음, 다른 소스는 정상 노출."""
        created: list = []
        bridge = pe.PolestarBridge(
            _config([("gp", "gongjon"), ("b0", "bankjon")], [_pg("gp"), _db2("b0")]),
            pool_factory=_factory({"gp": GP_DATA, "b0": RuntimeError("SQL0204N")}, created),
            now=lambda: NOW,
        )
        caplog.set_level(logging.WARNING, logger="mcp_server.polestar_exporter")
        parsed = _samples(await _collect_text(bridge))
        up = {s.labels["db_id"]: s.value for s in parsed["polestar_bridge_source_up"]}
        assert up == {"gp": 1.0, "b0": 0.0}
        info_dbs = {s.labels["db_id"] for s in parsed["polestar_server"]}
        assert info_dbs == {"gp"}
        assert {s.labels["db_id"] for s in parsed["polestar_metric_utilization_percent"]} == {"gp"}
        assert "b0" in caplog.text and "SQL0204N" in caplog.text

    async def test_truncated_when_rows_reach_max_rows(self, caplog):
        """행 수 == max_rows면 truncated 1 — 받은 행은 자르지 않고 그대로 낸다."""
        created: list = []
        data = {
            "small": {"servers": [
                {"server_name": "a", "ipaddress": "", "os": ""},
                {"server_name": "b", "ipaddress": "", "os": ""},
            ]},
            "big": {"servers": [{"server_name": "c", "ipaddress": "", "os": ""}]},
        }
        bridge = pe.PolestarBridge(
            _config(
                [("small", ""), ("big", "")],
                [_pg("small", max_rows=2), _pg("big", max_rows=2)],
            ),
            pool_factory=_factory(data, created), now=lambda: NOW,
        )
        caplog.set_level(logging.WARNING, logger="mcp_server.polestar_exporter")
        parsed = _samples(await _collect_text(bridge))
        truncated = {s.labels["db_id"]: s.value for s in parsed["polestar_bridge_truncated"]}
        assert truncated == {"small": 1.0, "big": 0.0}
        nodes = {
            s.labels["nodename"]
            for s in parsed["polestar_server"]
            if s.labels["db_id"] == "small"
        }
        assert nodes == {"a", "b"}
        assert "행 상한 도달" in caplog.text
        assert all(
            sql.rstrip().endswith("LIMIT 2") for _, sql in created[0].executed
        )  # 쿼리 행 제한 = 소스 max_rows

    async def test_unknown_inactive_and_non_polestar_sources(self, caplog):
        """미등록·비활성·비폴스타 엔진 소스 → source_up 0 + WARNING, 풀은 만들지 않는다."""
        created: list = []
        caplog.set_level(logging.WARNING, logger="mcp_server.polestar_exporter")
        cfg = _config(
            [("ghost", ""), ("inactive", ""), ("itam", "")],
            [
                SourceConfig(name="inactive", type="postgresql", connection=""),
                SourceConfig(name="itam", type="mariadb", connection="mariadb://u:p@h/db"),
            ],
        )
        bridge = pe.PolestarBridge(cfg, pool_factory=_factory({}, created), now=lambda: NOW)
        assert "ghost" in caplog.text and "inactive" in caplog.text and "mariadb" in caplog.text
        parsed = _samples(await _collect_text(bridge))
        up = {s.labels["db_id"]: s.value for s in parsed["polestar_bridge_source_up"]}
        assert up == {"ghost": 0.0, "inactive": 0.0, "itam": 0.0}
        assert created == []  # 조회 가능한 소스가 없으면 풀을 만들지 않는다

    async def test_duplicate_bridge_source_deduplicated(self, caplog):
        """같은 이름이 두 번 와도 db_id 시리즈는 1개(중복 시리즈 = 스크레이프 거부 방지)."""
        created: list = []
        caplog.set_level(logging.WARNING, logger="mcp_server.polestar_exporter")
        bridge = pe.PolestarBridge(
            _config([("gp", "a"), ("gp", "b")], [_pg("gp")]),
            pool_factory=_factory({"gp": GP_DATA}, created), now=lambda: NOW,
        )
        assert [bs.zone for bs in bridge.sources] == ["a"]
        parsed = _samples(await _collect_text(bridge))
        assert [s.labels["db_id"] for s in parsed["polestar_bridge_source_up"]] == ["gp"]
        assert "중복" in caplog.text

    async def test_sql_file_logger_initialized_on_pool_creation(self, monkeypatch):
        """브리지 SQL도 logs/sql/에 남도록 로거가 꺼져 있으면 풀 생성 때 초기화한다(D-140)."""
        calls: list[int] = []
        monkeypatch.setattr(pe.sql_log, "is_enabled", lambda: False)
        monkeypatch.setattr(pe.sql_log, "init", lambda *a, **k: calls.append(1))
        bridge = pe.PolestarBridge(
            _config([("gp", "")], [_pg("gp")]),
            pool_factory=_factory({"gp": GP_DATA}, []), now=lambda: NOW,
        )
        await bridge.cache.get()
        assert calls == [1]


# =====================================================================
# 라우트 게이팅 · Bearer · 도구 목록 · 앱 종료 정리
# =====================================================================


def _server(expose: bool, **kwargs):
    return create_server(_config([], [], expose=expose, **kwargs))


class TestRoute:
    """create_server + build_asgi_app 배선."""

    def test_flag_off_route_absent_404(self):
        """expose_polestar_exporter=False면 GET /metrics는 404(라우트 부재 = 비트 동일)."""
        with TestClient(build_asgi_app(_server(False), None)) as client:
            assert client.get("/metrics").status_code == 404

    def test_flag_on_route_200_openmetrics(self):
        """True면 200 + 협상된 OpenMetrics 1.0 본문."""
        with TestClient(build_asgi_app(_server(True), None)) as client:
            r = client.get("/metrics", headers={"accept": PROM_ACCEPT})
            r_txt = client.get("/metrics", headers={"accept": "*/*"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/openmetrics-text; version=1.0.0")
        assert r.text.endswith("# EOF\n")
        assert "# TYPE polestar_server info" in r.text
        assert r_txt.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"

    def test_bearer_required_when_token_set(self):
        """MCP_BEARER_TOKEN 설정 시 무토큰 401 · 올바른 토큰 200(브리지 라우트도 미들웨어 안쪽)."""
        with TestClient(build_asgi_app(_server(True), "secret")) as client:
            assert client.get("/metrics").status_code == 401
            wrong = client.get("/metrics", headers={"authorization": "Bearer wrong"})
            assert wrong.status_code == 401
            ok = client.get("/metrics", headers={"authorization": "Bearer secret"})
        assert ok.status_code == 200

    async def test_tool_list_identical_on_off(self):
        """브리지 플래그는 MCP 도구 표면을 바꾸지 않는다(list_tools 동일)."""
        off = [t.name for t in await _server(False).list_tools()]
        on = [t.name for t in await _server(True).list_tools()]
        assert off == on

    def test_app_lifespan_closes_lazy_pool(self, monkeypatch):
        """앱 수준: 스크레이프 전 풀 0개 → 첫 GET에 1개 → 앱 종료(lifespan) 시 close_all."""
        created: list = []
        data = {"polestar_cm_gp": GP_DATA}

        class _PatchedPool(FakePool):
            def __init__(self, sources):
                super().__init__(sources, data, created)

        monkeypatch.setattr(pe, "DBPoolManager", _PatchedPool)
        mcp = create_server(_config([("polestar_cm_gp", "gongjon")], [_pg("polestar_cm_gp")]))
        with TestClient(build_asgi_app(mcp, None)) as client:
            assert created == []
            r = client.get("/metrics", headers={"accept": PROM_ACCEPT})
            assert r.status_code == 200
            assert 'polestar_bridge_source_up{db_id="polestar_cm_gp"} 1.0' in r.text
            assert len(created) == 1 and created[0].closed == 0
        assert created[0].closed == 1

    def test_flag_off_app_lifespan_untouched(self):
        """플래그 off면 종료 훅을 걸지 않는다(앱 lifespan 비트 동일)."""
        mcp = _server(False)
        plain = mcp.sse_app().router.lifespan_context
        app = build_asgi_app(mcp, None)
        assert type(app.router.lifespan_context) is type(plain)


# =====================================================================
# 픽스처 Prometheus 실 스크레이프 (옵트인)
# =====================================================================


@pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason=(
        "Docker Prometheus 픽스처 미기동 — RUN_DOCKER_IT=1로 옵트인 시에만 실행. "
        "전제: mcp_server를 EXPOSE_POLESTAR_EXPORTER=true로 띄우고 픽스처 prometheus.yml에 "
        "job 'polestar'(→ mcp_server /metrics)를 둔다."
    ),
)
def test_fixture_prometheus_scrapes_bridge():
    """픽스처 Prometheus가 브리지를 실제로 긁는다 — up{job="polestar"} == 1."""
    base = os.environ.get("DOCKER_IT_PROM_URL", "http://localhost:9190")
    query = urllib.parse.urlencode({"query": 'up{job="polestar"}'})
    with urllib.request.urlopen(f"{base}/api/v1/query?{query}", timeout=10) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = payload["data"]["result"]
    assert result, f"job=polestar 스크레이프 대상 없음: {payload}"
    assert all(r["value"][1] == "1" for r in result)
