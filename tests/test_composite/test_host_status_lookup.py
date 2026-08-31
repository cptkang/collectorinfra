"""가용성 근거 조회 단위 테스트 (Plan 81 T2 · D-175).

고정하는 것:
    ① **추가 왕복 0** — 단일 경로는 기존 hostname 해소 SELECT 하나로 판정 근거까지 가져온다.
    ② **다대상 1쿼리** — `lookup_many`는 대상 수와 무관하게 execute_sql 1회.
    ③ **엔진 방언 대칭** — PostgreSQL/DB2 양쪽에서 스키마·LIMIT 규약이 유지된다.
    ④ **fail-open** — 조회 실패는 판정 불가(`lookup_failed`)이지 '가용하지 않음'이 아니다.
"""

import pytest

from noise_gate.infrastructure.polestar_hostname_resolver import (
    PolestarHostnameResolver,
    build_host_status_sql,
    build_hostname_sql,
)
from src.domain.host_availability import (
    REASON_LOOKUP_FAILED,
    REASON_NOT_REGISTERED,
    STATE_AVAILABLE,
    STATE_MAINTENANCE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)


class _Result:
    def __init__(self, rows):
        self.rows = rows


class _Client:
    """execute_sql 호출을 기록하는 최소 클라이언트(왕복 수 단언용)."""

    def __init__(self, rows=None, boom=False):
        self._rows = rows or []
        self._boom = boom
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_sql(self, sql):
        self.calls.append(sql)
        if self._boom:
            raise RuntimeError("DB 연결 실패")
        return _Result(self._rows)


class _Registry:
    def __init__(self, client, registered=True):
        self._client = client
        self._registered = registered

    def is_registered(self, db_id):
        return self._registered

    def get_client(self, db_id):
        return self._client


class TestSqlShape:
    def test_해소_SQL이_판정_컬럼을_함께_가져온다(self):
        sql = build_hostname_sql("polestar_cm_gp", "svr-01")
        assert "avail_status" in sql and "is_maintenance" in sql
        # 기존 규약 불변 — 삭제 리소스 제외·server.Server 한정·조인 없음(D-022)
        assert "r.dtime IS NULL" in sql and "server.Server" in sql
        assert "RESOURCE_CONF_ID" not in sql.upper()

    def test_배치_SQL은_IN절_하나로_조립된다(self):
        sql = build_host_status_sql("polestar_cm_gp", ["a-01", "b-02"])
        assert sql.count("IN (") == 2  # name IN (...) OR hostname IN (...)
        assert "'a-01'" in sql and "'b-02'" in sql
        assert "LIMIT" not in sql  # max_rows가 안전망 — _server_list_sql 선례

    def test_배치_SQL_따옴표_이스케이프(self):
        sql = build_host_status_sql("polestar_cm_gp", ["o'brien"])
        assert "'o''brien'" in sql

    def test_db2는_무스키마_참조(self):
        sql = build_host_status_sql("polestar_b0", ["a-01"], "db2")
        assert "polestar.cmm_resource" not in sql

    def test_postgresql은_스키마_한정(self):
        sql = build_host_status_sql("polestar_cm_gp", ["a-01"], "postgresql")
        assert "cmm_resource" in sql


@pytest.mark.asyncio
class TestResolveWithStatus:
    async def test_정상_서버는_hostname과_available을_함께_반환한다(self):
        client = _Client([{"hostname": "h-01", "name": "svr-01", "avail_status": 0,
                           "is_maintenance": 0}])
        lk = await PolestarHostnameResolver(_Registry(client)).resolve_with_status(
            "polestar_cm_gp", "svr-01"
        )
        assert lk.hostname == "h-01"
        assert lk.server_name == "svr-01"
        assert lk.availability.state == STATE_AVAILABLE
        assert len(client.calls) == 1, "판정을 위해 쿼리를 더 날리면 안 된다(추가 왕복 0)"

    async def test_DOWN_서버도_hostname은_해소된다(self):
        """차단 판정이어도 hostname은 돌려준다 — 문구에 정확한 대상을 실어야 한다."""
        client = _Client([{"hostname": "h-01", "name": "svr-01", "avail_status": 1,
                           "is_maintenance": 0}])
        lk = await PolestarHostnameResolver(_Registry(client)).resolve_with_status(
            "polestar_cm_gp", "svr-01"
        )
        assert lk.hostname == "h-01"
        assert lk.availability.state == STATE_UNAVAILABLE
        assert lk.availability.as_of  # 확인 시각이 채워진다

    async def test_DB2_대문자_컬럼도_읽는다(self):
        client = _Client([{"HOSTNAME": "h-01", "NAME": "svr-01", "AVAIL_STATUS": 1,
                           "IS_MAINTENANCE": 0}])
        lk = await PolestarHostnameResolver(_Registry(client)).resolve_with_status(
            "polestar_b0", "svr-01"
        )
        assert lk.hostname == "h-01"
        assert lk.availability.state == STATE_UNAVAILABLE

    async def test_점검_상태(self):
        client = _Client([{"hostname": "h-01", "name": "svr-01", "avail_status": 0,
                           "is_maintenance": 1}])
        lk = await PolestarHostnameResolver(_Registry(client)).resolve_with_status(
            "polestar_cm_gp", "svr-01"
        )
        assert lk.availability.state == STATE_MAINTENANCE

    async def test_0건이면_미등록_판정(self):
        lk = await PolestarHostnameResolver(_Registry(_Client([]))).resolve_with_status(
            "polestar_cm_gp", "없는서버"
        )
        assert lk.hostname is None
        assert lk.availability.reason == REASON_NOT_REGISTERED

    async def test_조회_실패는_판정_불가이지_불가용이_아니다(self):
        lk = await PolestarHostnameResolver(_Registry(_Client(boom=True))).resolve_with_status(
            "polestar_cm_gp", "svr-01"
        )
        assert lk.availability.reason == REASON_LOOKUP_FAILED
        assert lk.availability.blocks_collection is False

    async def test_미등록_db_id는_조회하지_않는다(self):
        client = _Client([])
        lk = await PolestarHostnameResolver(_Registry(client, registered=False)).resolve_with_status(
            "unknown_db", "svr-01"
        )
        assert client.calls == []
        assert lk.availability.reason == REASON_LOOKUP_FAILED

    async def test_resolve는_종전_반환형을_유지한다(self):
        client = _Client([{"hostname": "h-01", "name": "svr-01", "avail_status": 1,
                           "is_maintenance": 0}])
        assert await PolestarHostnameResolver(_Registry(client)).resolve(
            "polestar_cm_gp", "svr-01"
        ) == "h-01"


@pytest.mark.asyncio
class TestLookupMany:
    async def test_대상이_여럿이어도_쿼리는_한_번이다(self):
        client = _Client([
            {"hostname": "h-01", "name": "svr-01", "avail_status": 0, "is_maintenance": 0},
            {"hostname": "h-02", "name": "svr-02", "avail_status": 1, "is_maintenance": 0},
        ])
        out = await PolestarHostnameResolver(_Registry(client)).lookup_many(
            "polestar_cm_gp", ["svr-01", "svr-02"]
        )
        assert len(client.calls) == 1
        assert out["svr-01"].availability.state == STATE_AVAILABLE
        assert out["svr-02"].availability.state == STATE_UNAVAILABLE
        assert out["svr-02"].hostname == "h-02"

    async def test_hostname으로도_매칭된다(self):
        client = _Client([
            {"hostname": "h-01", "name": "svr-01", "avail_status": 1, "is_maintenance": 0},
        ])
        out = await PolestarHostnameResolver(_Registry(client)).lookup_many(
            "polestar_cm_gp", ["h-01"]
        )
        assert out["h-01"].availability.state == STATE_UNAVAILABLE

    async def test_매칭되지_않은_대상은_미등록_판정(self):
        client = _Client([
            {"hostname": "h-01", "name": "svr-01", "avail_status": 0, "is_maintenance": 0},
        ])
        out = await PolestarHostnameResolver(_Registry(client)).lookup_many(
            "polestar_cm_gp", ["svr-01", "없는서버"]
        )
        assert out["없는서버"].availability.reason == REASON_NOT_REGISTERED
        assert out["없는서버"].availability.blocks_collection is False

    async def test_조회_실패시_전_대상이_판정_불가로_채워진다(self):
        """일부만 비우면 호출부가 '판정 없음'과 '판정 불가'를 구분하지 못한다."""
        out = await PolestarHostnameResolver(_Registry(_Client(boom=True))).lookup_many(
            "polestar_cm_gp", ["a", "b"]
        )
        assert set(out) == {"a", "b"}
        assert all(lk.availability.state == STATE_UNKNOWN for lk in out.values())
        assert all(not lk.availability.blocks_collection for lk in out.values())

    async def test_중복_대상은_한_번만_조회한다(self):
        client = _Client([
            {"hostname": "h-01", "name": "svr-01", "avail_status": 0, "is_maintenance": 0},
        ])
        out = await PolestarHostnameResolver(_Registry(client)).lookup_many(
            "polestar_cm_gp", ["svr-01", "svr-01"]
        )
        assert len(out) == 1
        assert client.calls[0].count("'svr-01'") == 2  # name IN·hostname IN 각 1회
