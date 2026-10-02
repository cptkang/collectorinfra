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

from noise_gate.domain.alarm import AlarmEvent
from noise_gate.infrastructure.polestar_noise_context import (
    PolestarNoiseContextRepository,
    _sql_literal,
    build_dependency_sql,
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
    """SQL 내용으로 분기하여 자원/알림정책/의존성 행을 돌려주는 가짜 DB 클라이언트."""

    def __init__(
        self, resource_rows=None, noti_rows=None, dependency_rows=None, raise_on=None
    ):
        self._resource_rows = resource_rows if resource_rows is not None else []
        self._noti_rows = noti_rows if noti_rows is not None else []
        self._dependency_rows = dependency_rows if dependency_rows is not None else []
        # "any" | "resource" | "noti" | "dependency" | None
        self._raise_on = raise_on
        self.executed_sqls: list[str] = []

    async def execute_sql(self, sql):
        self.executed_sqls.append(sql)
        # 의존성 SQL은 self-join이라 'cmm_resource'를 포함하므로 resource보다 먼저 식별.
        is_dependency = "AVAIL_DEPEND_RESOURCE_ID" in sql
        is_noti = "cmm_alarm_def_noti" in sql
        if self._raise_on == "any":
            raise RuntimeError("db boom")
        if self._raise_on == "noti" and is_noti:
            raise RuntimeError("noti boom")
        if self._raise_on == "dependency" and is_dependency:
            raise RuntimeError("dependency boom")
        if self._raise_on == "resource" and not is_noti and not is_dependency:
            raise RuntimeError("resource boom")
        if is_dependency:
            rows = self._dependency_rows
        elif is_noti:
            rows = self._noti_rows
        else:
            rows = self._resource_rows
        return SimpleNamespace(rows=rows, row_count=len(rows))


class FakeRegistry:
    def __init__(self, client, registered=True, connect_error=False):
        self._client = client
        self._registered = registered
        self._connect_error = connect_error  # get_client 진입 시 예외(연결 실패 모사)
        self.requested_db_id = None

    def is_registered(self, db_id):
        return self._registered

    @asynccontextmanager
    async def get_client(self, db_id):
        self.requested_db_id = db_id
        if self._connect_error:
            raise RuntimeError("connection refused")
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


class TestBuildDependencySql:
    def test_basic_structure(self):
        sql = build_dependency_sql("polestar_cm_gp", "cop0-aisapd02")
        upper = sql.upper()
        assert upper.lstrip().startswith("SELECT")
        assert ";" not in sql                          # 단일문
        for forbidden in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "MERGE"):
            assert forbidden not in upper
        assert "RESOURCE_CONF_ID" not in upper         # D-022 준수
        # E2 의존성 신호 컬럼이 포함되어야 함
        assert "AVAIL_DEPEND_RESOURCE_ID" in upper
        assert "AVAIL_STATUS" in upper
        # self-join: 부모(P)는 자식(SVR)의 의존 부모 ID로 매칭
        assert "P.ID = SVR.AVAIL_DEPEND_RESOURCE_ID" in sql
        assert "SVR.DTIME IS NULL" in sql
        assert "P.DTIME IS NULL" in sql
        assert "SVR.RESOURCE_TYPE = 'server.Server'" in sql
        assert "LIMIT 1" in sql
        # E2 1차에서 보조 의존(_2)은 생략(현 SQL에 등장하지 않음)
        assert "AVAIL_DEPEND_RESOURCE_ID_2" not in upper

    def test_schema_qualified(self):
        # 폴스타 테이블은 'polestar' 스키마 — self-join 양쪽 모두 스키마 한정
        sql = build_dependency_sql("polestar_cm_yd", "svr-x")
        assert "polestar.cmm_resource SVR" in sql
        assert "polestar.cmm_resource P" in sql
        assert "JOIN cmm_resource" not in sql

    def test_server_match_uses_name_for_gp(self):
        sql = build_dependency_sql("polestar_cm_gp", "cop0-aisapd02")
        assert "SVR.NAME = 'cop0-aisapd02'" in sql
        assert "HOSTNAME" not in sql.upper()

    def test_server_match_uses_name_for_yd(self):
        sql = build_dependency_sql("polestar_cm_yd", "cop0-aisapd02")
        assert "SVR.NAME = 'cop0-aisapd02'" in sql
        assert "HOSTNAME" not in sql.upper()

    def test_literal_escaping(self):
        sql = build_dependency_sql("polestar", "svr'; DROP TABLE x--")
        assert "svr''; DROP TABLE x--" in sql


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


class TestPartialFailureGracefulDegradation:
    """부분 실패 시 부분 반환 보장 (실 폴스타 DB 검증으로 발견된 결함 회귀 방지).

    실 폴스타 DB에 `cmm_alarm_def` 테이블이 없어 noti 조회만 실패할 때, 이전 구현은
    두 조회를 한 try 블록에 묶어 **실재하는 중요도/유지보수 신호까지 전부 손실**(unavailable)
    시켰다. 두 조회를 독립 try/except로 분리한 뒤에는 한쪽 실패가 다른 신호를 보존한다.
    mock가 항상 성공하던 기존 단위 테스트는 이 결함을 잡지 못했으므로 그 공백을 메운다.
    """

    async def test_noti_query_fails_resource_signals_preserved(self):
        # (a) noti 조회만 예외(실 폴스타 cmm_alarm_def 부재 시나리오) →
        #     중요도/유지보수는 보존, noti_policy만 None, source는 polestar_db
        client = FakeClient(
            resource_rows=[{"importance_id": 1, "maintenance": 0}],
            noti_rows=[{"noti_count": 9}],  # 실행되면 notify가 되겠지만, raise로 도달 못함
            raise_on="noti",
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["importance_id"] == 1          # 실재 신호 보존
        assert ctx["maintenance"] is False        # 실재 신호 보존
        assert ctx["noti_policy"] is None         # noti 조회 실패 → None
        assert ctx["parent_avail_status"] is None
        assert ctx["source"] == "polestar_db"     # ★ unavailable 아님(회귀 핵심)

    async def test_resource_query_fails_noti_preserved(self):
        # (b) resource 조회만 예외 → 중요도/유지보수 None, 알림정책은 보존
        client = FakeClient(
            resource_rows=[{"importance_id": 3, "maintenance": 1}],  # 도달 못함
            noti_rows=[{"noti_count": 4}],
            raise_on="resource",
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["importance_id"] is None       # resource 조회 실패 → None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] == "notify"     # 실재 신호 보존
        assert ctx["source"] == "polestar_db"     # ★ 부분 반환

    async def test_both_queries_fail_unavailable(self):
        # (c) 양쪽 조회 모두 예외(연결은 성공) → 신호 없음 → 보수적 unavailable
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 1}],
            noti_rows=[{"noti_count": 2}],
            raise_on="any",
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["source"] == "unavailable"
        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] is None
        assert ctx["parent_avail_status"] is None
        # 양쪽 모두 execute_sql까지는 도달(연결은 성공)
        assert len(client.executed_sqls) == 2

    async def test_connection_failure_unavailable(self):
        # (d) 연결(get_client) 자체 예외 → execute_sql 미도달 → unavailable
        client = FakeClient(
            resource_rows=[{"importance_id": 3, "maintenance": 0}],
            noti_rows=[{"noti_count": 1}],
        )
        registry = FakeRegistry(client, connect_error=True)
        repo = PolestarNoiseContextRepository(registry, make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["source"] == "unavailable"
        assert ctx["importance_id"] is None
        assert ctx["maintenance"] is None
        assert ctx["noti_policy"] is None
        assert client.executed_sqls == []          # 연결 실패로 SQL 미실행


class TestFetchDependency:
    """의존성/부모 상태(E2, §3.6) 수집 — `collect_dependency` 게이팅 + graceful degradation."""

    async def test_dependency_off_by_default_no_dependency_sql(self):
        # 기본(collect_dependency=False): 의존성 SQL 미실행, parent_avail_status None
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 0}],
            noti_rows=[{"noti_count": 1}],
            dependency_rows=[{"parent_avail_status": 1}],  # 있어도 미실행이라 미반영
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event())

        assert ctx["parent_avail_status"] is None
        assert ctx["source"] == "polestar_db"
        joined = "\n".join(client.executed_sqls).upper()
        assert "AVAIL_DEPEND_RESOURCE_ID" not in joined   # 의존성 SQL 미실행
        # 기본 경로 SQL 2건만 실행(resource + noti)
        assert len(client.executed_sqls) == 2

    async def test_dependency_parent_abnormal(self):
        # collect_dependency=True + 부모행 존재 → parent_avail_status 채워짐
        client = FakeClient(
            resource_rows=[{"importance_id": 3, "maintenance": 0}],
            noti_rows=[{"noti_count": 1}],
            dependency_rows=[{"parent_avail_status": 1}],  # ≠0 → 비정상
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["parent_avail_status"] == 1
        assert ctx["importance_id"] == 3
        assert ctx["noti_policy"] == "notify"
        assert ctx["source"] == "polestar_db"
        joined = "\n".join(client.executed_sqls).upper()
        assert "AVAIL_DEPEND_RESOURCE_ID" in joined       # 의존성 SQL 실행됨

    async def test_dependency_parent_normal_zero(self):
        # 부모행 AVAIL_STATUS=0(정상) → parent_avail_status==0 (int 보존, None 아님)
        client = FakeClient(
            resource_rows=[{"importance_id": 1, "maintenance": 0}],
            noti_rows=[{"noti_count": 0}],
            dependency_rows=[{"parent_avail_status": 0}],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["parent_avail_status"] == 0
        assert ctx["source"] == "polestar_db"

    async def test_dependency_no_parent_rows_none(self):
        # 의존성 미설정(AVAIL_DEPEND_RESOURCE_ID NULL → JOIN 0행) → None (보수적, 억제 안 함)
        # 실측: 도커 폴스타 testdata는 avail_depend_resource_id 전부 NULL → 이 경로가 정답
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 0}],
            noti_rows=[{"noti_count": 1}],
            dependency_rows=[],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["parent_avail_status"] is None
        assert ctx["importance_id"] == 2          # 타 신호 보존
        assert ctx["noti_policy"] == "notify"
        assert ctx["source"] == "polestar_db"

    async def test_dependency_query_fails_other_signals_preserved(self):
        # ★ graceful 회귀(D-041): 의존성 execute_sql만 예외 →
        #    importance/maintenance/noti_policy 보존 + source polestar_db
        client = FakeClient(
            resource_rows=[{"importance_id": 1, "maintenance": 0}],
            noti_rows=[{"noti_count": 9}],
            dependency_rows=[{"parent_avail_status": 1}],  # 실행되면 1이겠지만 raise로 미도달
            raise_on="dependency",
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["importance_id"] == 1          # 실재 신호 보존
        assert ctx["maintenance"] is False        # 실재 신호 보존
        assert ctx["noti_policy"] == "notify"     # 실재 신호 보존
        assert ctx["parent_avail_status"] is None  # 의존성 조회 실패 → None
        assert ctx["source"] == "polestar_db"     # ★ unavailable 아님(회귀 핵심)

    async def test_dependency_and_noti_both_succeed(self):
        # 의존성 + noti 동시 성공: 모든 신호 채워짐
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 1}],
            noti_rows=[{"noti_count": 3}],
            dependency_rows=[{"parent_avail_status": 2}],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["importance_id"] == 2
        assert ctx["maintenance"] is True
        assert ctx["noti_policy"] == "notify"
        assert ctx["parent_avail_status"] == 2
        assert ctx["source"] == "polestar_db"
        # 3개 고정 SQL 모두 실행
        assert len(client.executed_sqls) == 3

    async def test_all_queries_fail_unavailable_with_dependency(self):
        # collect_dependency=True에서도 resource/noti/dependency 모두 실패(연결은 성공) →
        # 신호 없음 → 보수적 unavailable. execute_sql은 3건 모두 시도(연결 성공).
        client = FakeClient(
            resource_rows=[{"importance_id": 5, "maintenance": 1}],
            noti_rows=[{"noti_count": 7}],
            dependency_rows=[{"parent_avail_status": 1}],
            raise_on="any",
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["source"] == "unavailable"
        assert ctx["parent_avail_status"] is None
        assert ctx["importance_id"] is None
        assert ctx["noti_policy"] is None
        assert len(client.executed_sqls) == 3     # 3건 모두 execute_sql 시도(연결 성공)

    async def test_dependency_bad_value_graceful_none(self):
        # 부모 AVAIL_STATUS가 int 변환 불가 → None으로 강등(graceful), 타 신호 보존
        client = FakeClient(
            resource_rows=[{"importance_id": 2, "maintenance": 0}],
            noti_rows=[{"noti_count": 1}],
            dependency_rows=[{"parent_avail_status": "not-a-number"}],
        )
        repo = PolestarNoiseContextRepository(FakeRegistry(client), make_alarm_cfg())

        ctx = await repo.fetch(make_event(), collect_dependency=True)

        assert ctx["parent_avail_status"] is None
        assert ctx["importance_id"] == 2
        assert ctx["source"] == "polestar_db"
