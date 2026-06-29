"""폴스타 노이즈 컨텍스트 리포지토리(Plan 52, Phase E1) 단위 테스트.

고정 SQL 조립(서버 매칭/리터럴 이스케이프/스키마 한정/D-022 준수)과
조회 결과 행 → noise_ctx dict 변환(중요도 원값 보존, 유지보수 bool, 알림정책 보수 판정,
graceful degradation)을 검증한다.

mock 패턴은 tests/test_alarm_history_repo.py 를 계승한다(SimpleNamespace registry,
asynccontextmanager get_client, execute_sql → SimpleNamespace(rows=[...])).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

from src.alarm.domain.alarm import AlarmEvent
from src.alarm.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
    _sql_literal,
    build_noti_policy_sql,
    build_resource_signal_sql,
)
from src.config import AlarmConfig

REF = datetime(2026, 6, 26, 14, 35, 0)


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
    """SQL 내용으로 분기하여 자원/알림정책 행을 돌려주는 가짜 DB 클라이언트."""

    def __init__(self, resource_rows=None, noti_rows=None, raise_on=None):
        self._resource_rows = resource_rows if resource_rows is not None else []
        self._noti_rows = noti_rows if noti_rows is not None else []
        self._raise_on = raise_on  # "any" | "resource" | "noti" | None
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        is_noti = "cmm_alarm_def_noti" in sql
        if self._raise_on == "any":
            raise RuntimeError("db boom")
        if self._raise_on == "noti" and is_noti:
            raise RuntimeError("noti boom")
        if self._raise_on == "resource" and not is_noti:
            raise RuntimeError("resource boom")
        rows = self._noti_rows if is_noti else self._resource_rows
        return SimpleNamespace(rows=rows, row_count=len(rows))


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


class TestBuildResourceSignalSql:
    def test_basic_structure(self):
        sql = build_resource_signal_sql("polestar_cm_gp", "cop0-aisapd02")
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql                          # 단일문
        # DML/DDL 미포함
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "RESOURCE_CONF_ID" not in upper         # D-022 준수
        assert "IMPORTANCE_ID" in upper
        assert "IS_MAINTENANCE" in upper
        assert "SVR.DTIME IS NULL" in sql
        assert "SVR.RESOURCE_TYPE = 'server.Server'" in sql
        assert "LIMIT 1" in sql

    def test_schema_qualified(self):
        # 폴스타 테이블은 'polestar' 스키마 — 스키마 한정 필수
        sql = build_resource_signal_sql("polestar_cm_yd", "svr-x")
        assert "polestar.cmm_resource" in sql
        assert "FROM cmm_resource" not in sql

    def test_server_match_uses_name_for_gp(self):
        # 공동존 폴스타: 서버 매칭은 SVR.NAME — hostname 금지
        sql = build_resource_signal_sql("polestar_cm_gp", "cop0-aisapd02")
        assert "SVR.NAME = 'cop0-aisapd02'" in sql
        assert "HOSTNAME" not in sql.upper()

    def test_server_match_uses_name_for_yd(self):
        sql = build_resource_signal_sql("polestar_cm_yd", "cop0-aisapd02")
        assert "SVR.NAME = 'cop0-aisapd02'" in sql
        assert "HOSTNAME" not in sql.upper()

    def test_literal_escaping(self):
        sql = build_resource_signal_sql("polestar", "svr'; DROP TABLE x--")
        assert "svr''; DROP TABLE x--" in sql


class TestBuildNotiPolicySql:
    def test_basic_structure(self):
        sql = build_noti_policy_sql("polestar_cm_gp", "CPU 사용률 임계 초과")
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "RESOURCE_CONF_ID" not in upper                       # D-022
        assert "COUNT(*)" in upper
        # 통보정의 조인 키는 DN.DEFINITION_ID = D.MASTERDEFINITION_ID (query_generator 실측)
        assert "DN.DEFINITION_ID = D.MASTERDEFINITION_ID" in sql
        assert "D.NAME = 'CPU 사용률 임계 초과'" in sql

    def test_schema_qualified(self):
        sql = build_noti_policy_sql("polestar_cm_gp", "A")
        assert "polestar.cmm_alarm_def" in sql
        assert "polestar.cmm_alarm_def_noti" in sql
        assert "FROM cmm_alarm_def" not in sql
        assert "JOIN cmm_alarm_def_noti" not in sql

    def test_literal_escaping(self):
        sql = build_noti_policy_sql("polestar", "O'Reilly's alarm")
        assert "O''Reilly''s alarm" in sql


class TestSqlLiteral:
    def test_sql_literal(self):
        assert _sql_literal("abc") == "'abc'"
        assert _sql_literal("a'b") == "'a''b'"
        assert _sql_literal("a\x00b") == "'ab'"


class TestFetch:
    async def test_fetch_full_signals(self):
        client = FakeClient(
            resource_rows=[{"importance_id": 3, "maintenance": 0}],
            noti_rows=[{"noti_count": 2}],
        )
        registry = FakeRegistry(client)
        repo = PolestarNoiseContextRepository(registry, make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert registry.requested_db_id == "polestar_cm_gp"
        assert ctx["importance_id"] == 3          # 원값 보존(매핑 없음)
        assert ctx["maintenance"] is False        # 0 → False
        assert ctx["noti_policy"] == "notify"     # 통보정의 존재
        assert ctx["parent_avail_status"] is None  # E2 자리예약
        assert ctx["source"] == "polestar_db"
        # 두 고정 SQL이 실제 실행되었는지 + 이스케이프된 alarm_name 포함 확인
        joined = "\n".join(client.executed_sqls)
        assert "polestar.cmm_resource" in joined
        assert "polestar.cmm_alarm_def_noti" in joined
        assert "D.NAME = 'CPU 사용률 임계 초과'" in joined

    async def test_importance_raw_value_preserved_as_string(self):
        # 드라이버가 대문자 키 + 문자열로 돌려줘도(별칭 importance_id/maintenance 기준)
        # 대소문자 무시 조회 + 매핑/형변환 없이 원값 보존
        client = FakeClient(
            resource_rows=[{"IMPORTANCE_ID": "1", "MAINTENANCE": 1}],
            noti_rows=[{"noti_count": 0}],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["importance_id"] == "1"        # 문자열 원값 그대로
        assert ctx["maintenance"] is True         # 1 → True
        assert ctx["noti_policy"] is None         # 통보정의 0건 → 보수적 None

    async def test_noti_policy_never_suppress_when_absent(self):
        # 통보정의 행이 없어도 "suppress"로 단정하지 않고 None (보수적)
        client = FakeClient(
            resource_rows=[{"importance_id": 1, "maintenance": None}],
            noti_rows=[],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["noti_policy"] is None
        assert ctx["noti_policy"] != "suppress"
        assert ctx["maintenance"] is None          # NULL → None
        assert ctx["source"] == "polestar_db"

    async def test_empty_resource_rows_partial_none(self):
        # 서버행 미발견(빈 결과) → 중요도/유지보수 None, source 는 polestar_db 유지
        client = FakeClient(resource_rows=[], noti_rows=[{"noti_count": 5}])
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] == "notify"
        assert ctx["source"] == "polestar_db"

    async def test_unregistered_db_returns_unavailable(self):
        client = FakeClient(resource_rows=[{"importance_id": 3, "maintenance": 0}])
        registry = FakeRegistry(client, registered=False)
        repo = PolestarNoiseContextRepository(registry, make_alarm_cfg())

        ctx = await repo.fetch(make_event(db_id="unknown_db"))

        assert ctx["source"] == "unavailable"
        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] is None
        assert ctx["parent_avail_status"] is None
        assert client.executed_sqls == []          # DB 미접근
        assert repo.is_db_registered("unknown_db") is False

    async def test_query_exception_graceful_unavailable(self):
        # 조회 중 예외 → 전파 안 함, source="unavailable"
        client = FakeClient(raise_on="any")
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["source"] == "unavailable"
        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] is None
        assert ctx["parent_avail_status"] is None

    async def test_parent_avail_status_always_none(self):
        # E2 신호(parent_avail_status)는 본 파일에서 수집 금지 — 항상 None
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 1}],
            noti_rows=[{"noti_count": 1}],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["parent_avail_status"] is None
        # E2 의존성 컬럼이 SQL에 등장하지 않아야 함
        joined = "\n".join(client.executed_sqls).upper()
        assert "AVAIL_DEPEND_RESOURCE_ID" not in joined
        assert "AVAIL_STATUS" not in joined
