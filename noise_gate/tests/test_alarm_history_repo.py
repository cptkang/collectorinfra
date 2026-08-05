"""폴스타 알람 이력 조회 리포지토리(Plan 47) 단위 테스트.

고정 SQL 조립(서버 매칭/리터럴 이스케이프/D-022 준수)과
조회 결과 행 → AlarmHistoryEntry 변환을 검증한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.infrastructure.polestar_history import (
    PolestarAlarmHistoryRepository,
    _sql_literal,
    build_history_sql,
)
from src.config import AlarmConfig

REF = datetime(2026, 6, 11, 14, 35, 0)


def make_event(**kwargs) -> AlarmEvent:
    defaults = dict(
        db_id="polestar_cm_gp",
        server_name="cop0-aisapd02",
        hostname="saisvd01",
        ip_address="10.1.2.3",
        resource_ancestry="/Servers/svr/Cpus",
        alarm_id="CUR-001",
        severity=2,
        alarm_status="NOT_ACK",
        resource_type="server.Cpus",
        resource_name="svr-001-CPU",
        alarm_name="CPU 사용률 임계 초과",
        alarm_time=REF,
        conditions="",
        condition_log="",
        is_clear=False,
    )
    defaults.update(kwargs)
    return AlarmEvent(**defaults)


def make_alarm_cfg(**kwargs) -> AlarmConfig:
    defaults = dict(
        history_enabled=True,
        history_lookback_days=90,
        history_max_rows=2000,
        history_cache_ttl_seconds=300,
        enrich_timeout_seconds=5,
        burst_threshold_24h=5,
    )
    defaults.update(kwargs)
    return AlarmConfig(**defaults)


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None

    async def execute_sql(self, sql):
        self.executed_sql = sql
        return SimpleNamespace(rows=self.rows, row_count=len(self.rows))


class FakeRegistry:
    def __init__(self, client, registered=True):
        self._client = client
        self._registered = registered
        self.requested_db_id = None

    def is_registered(self, db_id):
        return self._registered

    @asynccontextmanager
    async def get_client(self, db_id):
        self.requested_db_id = db_id
        yield self._client


class TestBuildHistorySql:
    def test_basic_structure(self):
        lookback = REF - timedelta(days=90)
        sql = build_history_sql(
            db_id="polestar_cm_gp",
            alarm_name="CPU 사용률 임계 초과",
            server_name="cop0-aisapd02",
            lookback_start=lookback,
            max_rows=2000,
        )
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql                         # 단일문
        assert "RESOURCE_CONF_ID" not in upper        # D-022 준수
        assert "CMM_ALARM_ACTIVE" not in upper        # 이력 조회 — ACTIVE JOIN 금지
        assert "CA.ALARMSEVERITY IN (0, 1, 2, 3)" in sql  # D-030: 해소 포함
        assert "CR.DTIME IS NULL" in sql
        assert "LIMIT 2000" in sql
        assert "D.NAME = 'CPU 사용률 임계 초과'" in sql
        assert f"TO_TIMESTAMP('{lookback:%Y-%m-%d %H:%M:%S}'" in sql

    def test_tables_are_schema_qualified(self):
        # 폴스타 테이블은 'polestar' 스키마에 존재 — 스키마 한정 필수
        # (미한정 시 PostgreSQL이 public에서 cmm_alarm을 찾다 relation does not exist)
        sql = build_history_sql(
            db_id="polestar_cm_yd",
            alarm_name="Memory 사용률",
            server_name="cop0-aisapd02",
            lookback_start=REF,
            max_rows=10,
        )
        assert "polestar.cmm_alarm" in sql
        assert "polestar.cmm_alarm_def" in sql
        assert "polestar.cmm_resource" in sql
        # 스키마 없는 맨 테이블 참조가 없어야 함
        assert "FROM cmm_alarm" not in sql
        assert "JOIN cmm_alarm_def" not in sql
        assert "JOIN cmm_resource" not in sql

    def test_server_match_uses_resource_name_for_gp(self):
        # 공동존 폴스타: 서버 매칭은 r.name(=SVR.NAME) — hostname 금지
        sql = build_history_sql(
            db_id="polestar_cm_gp",
            alarm_name="A",
            server_name="cop0-aisapd02",
            lookback_start=REF,
            max_rows=10,
        )
        assert "SVR.NAME = 'cop0-aisapd02'" in sql
        assert "HOSTNAME = " not in sql.upper()

    def test_sub_resource_platform_join(self):
        # 하위 자원 알람도 매칭되도록 Template C-6 패턴 사용
        sql = build_history_sql(
            db_id="polestar",
            alarm_name="A",
            server_name="svr",
            lookback_start=REF,
            max_rows=10,
        )
        assert "COALESCE(CR.PLATFORM_RESOURCE_ID, CR.ID)" in sql
        assert "SVR.RESOURCE_TYPE = 'server.Server'" in sql

    def test_literal_escaping(self):
        sql = build_history_sql(
            db_id="polestar",
            alarm_name="O'Reilly's alarm",
            server_name="svr'; DROP TABLE x--",
            lookback_start=REF,
            max_rows=10,
        )
        assert "O''Reilly''s alarm" in sql
        assert "svr''; DROP TABLE x--" in sql

    def test_sql_literal(self):
        assert _sql_literal("abc") == "'abc'"
        assert _sql_literal("a'b") == "'a''b'"
        assert _sql_literal("a\x00b") == "'ab'"


class TestFetch:
    async def test_fetch_parses_rows(self):
        rows = [
            {
                "alarm_id": 1001,
                "alarm_time": "2026-06-10 02:11:00",
                "severity": 2,
                "alarm_status": "NOT_ACK",
                "resource_name": "svr-001-CPU",
            },
            {
                # 대문자 키 + datetime 값도 처리
                "ALARM_ID": "1002",
                "ALARM_TIME": datetime(2026, 6, 9, 2, 5, 0),
                "SEVERITY": "0",
                "ALARM_STATUS": "",
                "RESOURCE_NAME": "svr-001-CPU",
            },
        ]
        client = FakeClient(rows)
        registry = FakeRegistry(client)
        repo = PolestarAlarmHistoryRepository(registry, make_alarm_cfg())

        entries = await repo.fetch(make_event())

        assert registry.requested_db_id == "polestar_cm_gp"
        assert len(entries) == 2
        assert entries[0].alarm_id == "1001"
        assert entries[0].severity == 2
        assert entries[0].alarm_time == datetime(2026, 6, 10, 2, 11, 0)
        assert entries[1].alarm_id == "1002"
        assert entries[1].severity == 0
        assert entries[1].alarm_time == datetime(2026, 6, 9, 2, 5, 0)
        # lookback_start는 event.alarm_time 기준
        expected_start = REF - timedelta(days=90)
        assert f"TO_TIMESTAMP('{expected_start:%Y-%m-%d %H:%M:%S}'" in client.executed_sql

    async def test_fetch_unregistered_db_returns_empty(self):
        client = FakeClient([])
        registry = FakeRegistry(client, registered=False)
        repo = PolestarAlarmHistoryRepository(registry, make_alarm_cfg())

        entries = await repo.fetch(make_event(db_id="unknown_db"))

        assert entries == []
        assert client.executed_sql is None  # DB 미접근
        assert repo.is_db_registered("unknown_db") is False

    async def test_fetch_skips_unparseable_rows(self):
        rows = [
            {"alarm_id": 1, "alarm_time": "not-a-date", "severity": 2,
             "alarm_status": "", "resource_name": "r"},
            {"alarm_id": 2, "alarm_time": "2026-06-10T02:11:00", "severity": 2,
             "alarm_status": "", "resource_name": "r"},
        ]
        repo = PolestarAlarmHistoryRepository(
            FakeRegistry(FakeClient(rows)), make_alarm_cfg()
        )
        entries = await repo.fetch(make_event())
        assert len(entries) == 1
        assert entries[0].alarm_id == "2"
