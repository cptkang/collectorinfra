"""서버명 → 호스트명 해소(D-046) 단위 테스트.

폴스타 프로세스 API의 조회 키는 hostname이지만 사용자는 서버명(cmm_resource.name)으로
질의한다. 공동존 폴스타(gp/yd)는 name ≠ hostname 이므로 서버명을 그대로 hostname으로
보내면 0건 → 환각으로 이어진다. PolestarHostnameResolver가 입력을 정규 hostname으로
해소하고, run_process_query가 그 hostname으로 프로세스 API를 호출하는지 검증한다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from src.alarm.infrastructure.polestar_hostname_resolver import (
    PolestarHostnameResolver,
    _sql_literal,
    build_hostname_sql,
)
from src.orchestration.process_query import run_process_query


class _FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.executed_sql = None

    async def execute_sql(self, sql):
        self.executed_sql = sql
        return SimpleNamespace(rows=self.rows, row_count=len(self.rows))


class _FakeRegistry:
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


class TestBuildHostnameSql:
    def test_matches_name_or_hostname_on_server_rows(self):
        sql = build_hostname_sql("polestar_cm_gp", "WEB-SVR-01")
        assert "polestar.cmm_resource" in sql
        assert "r.resource_type = 'server.Server'" in sql
        assert "r.dtime IS NULL" in sql
        assert "r.name = 'WEB-SVR-01'" in sql
        assert "r.hostname = 'WEB-SVR-01'" in sql
        # name 일치 우선
        assert "ORDER BY CASE WHEN r.name = 'WEB-SVR-01' THEN 0 ELSE 1 END" in sql
        # D-022/2026-06-10 정합: 금지 패턴 미포함
        assert "RESOURCE_CONF_ID" not in sql
        assert "is_lob" not in sql

    def test_db2_uses_fetch_first_and_no_schema(self):
        # 은행 레거시 b0(DB2)는 LIMIT 미지원 → FETCH FIRST, 무스키마 테이블(CURRENT SCHEMA).
        sql = build_hostname_sql("polestar_b0", "WEB-SVR-01", db_engine="db2")
        assert "FETCH FIRST 1 ROWS ONLY" in sql
        assert "LIMIT" not in sql
        assert "polestar.cmm_resource" not in sql
        assert "FROM cmm_resource r" in sql
        # 매칭 규칙은 엔진 무관 동일
        assert "r.resource_type = 'server.Server'" in sql
        assert "ORDER BY CASE WHEN r.name = 'WEB-SVR-01' THEN 0 ELSE 1 END" in sql

    def test_postgresql_default_keeps_limit_and_schema(self):
        # 기본(postgresql) 경로는 회귀 없이 LIMIT + polestar. 스키마 유지.
        sql = build_hostname_sql("polestar_cm_gp", "WEB-SVR-01", db_engine="postgresql")
        assert "LIMIT 1" in sql
        assert "polestar.cmm_resource" in sql
        assert "FETCH FIRST" not in sql

    def test_literal_escaping(self):
        sql = build_hostname_sql("polestar_cm_gp", "x'; DROP TABLE y--")
        assert "x''; DROP TABLE y--" in sql

    def test_sql_literal(self):
        assert _sql_literal("abc") == "'abc'"
        assert _sql_literal("a'b") == "'a''b'"
        assert _sql_literal("a\x00b") == "'ab'"


class TestResolver:
    async def test_server_name_resolves_to_hostname(self):
        # 사용자가 서버명(name)을 줬고, DB에는 hostname이 별도로 존재
        client = _FakeClient([{"hostname": "saisvd01", "name": "WEB-SVR-01"}])
        registry = _FakeRegistry(client)
        resolver = PolestarHostnameResolver(registry)

        result = await resolver.resolve("polestar_cm_gp", "WEB-SVR-01")
        assert result == "saisvd01"
        assert registry.requested_db_id == "polestar_cm_gp"

    async def test_unregistered_db_returns_none(self):
        client = _FakeClient([{"hostname": "saisvd01", "name": "WEB-SVR-01"}])
        registry = _FakeRegistry(client, registered=False)
        resolver = PolestarHostnameResolver(registry)

        assert await resolver.resolve("polestar_cm_gp", "WEB-SVR-01") is None

    async def test_no_rows_returns_none(self):
        client = _FakeClient([])
        resolver = PolestarHostnameResolver(_FakeRegistry(client))
        assert await resolver.resolve("polestar_cm_gp", "unknown-server") is None

    async def test_empty_hostname_returns_none(self):
        client = _FakeClient([{"hostname": "", "name": "WEB-SVR-01"}])
        resolver = PolestarHostnameResolver(_FakeRegistry(client))
        assert await resolver.resolve("polestar_cm_gp", "WEB-SVR-01") is None

    async def test_b0_resolve_executes_db2_dialect(self):
        # b0(db2)는 resolve가 엔진을 조회해 FETCH FIRST·무스키마 SQL을 실행해야 한다.
        client = _FakeClient([{"hostname": "bankhost01", "name": "은행서버-01"}])
        resolver = PolestarHostnameResolver(_FakeRegistry(client))
        result = await resolver.resolve("polestar_b0", "은행서버-01")
        assert result == "bankhost01"
        assert "FETCH FIRST 1 ROWS ONLY" in client.executed_sql
        assert "polestar.cmm_resource" not in client.executed_sql

    async def test_uppercase_keys(self):
        client = _FakeClient([{"HOSTNAME": "saisvd01", "NAME": "WEB-SVR-01"}])
        resolver = PolestarHostnameResolver(_FakeRegistry(client))
        assert await resolver.resolve("polestar_cm_gp", "WEB-SVR-01") == "saisvd01"

    async def test_query_failure_returns_none(self):
        class _Boom(_FakeClient):
            async def execute_sql(self, sql):
                raise RuntimeError("db down")

        resolver = PolestarHostnameResolver(_FakeRegistry(_Boom([])))
        assert await resolver.resolve("polestar_cm_gp", "WEB-SVR-01") is None


class _ProcCfg:
    process_top_n = 5

    def __init__(self, mapping):
        self._m = mapping

    def get_process_api_base_url(self, db_id):
        return self._m.get(db_id)


class _AppCfgStub:
    def __init__(self, alarm, active_db_ids):
        self.alarm = alarm

        class _Multi:
            def get_active_db_ids(self_inner):
                return active_db_ids

        self.multi_db = _Multi()


class TestResolveDbIdLocationHint:
    """첫 턴(task.db_ids/previous 공백)에 질의 텍스트로 db_id를 재도출하는 위치 힌트."""

    def test_bank_keyword_resolves_b0(self):
        from src.orchestration.process_query import _resolve_db_id

        cfg = _AppCfgStub(_ProcCfg({"polestar_b0": "http://10.37.16.51:9010"}), ["polestar_b0"])
        db_id = _resolve_db_id(
            {}, {}, "은행 폴스타 ### 서버 현재 프로세스 리스트 조회", cfg
        )
        assert db_id == "polestar_b0"

    def test_legacy_keyword_resolves_b0(self):
        from src.orchestration.process_query import _resolve_db_id

        cfg = _AppCfgStub(_ProcCfg({"polestar_b0": "http://b0"}), ["polestar_b0"])
        assert _resolve_db_id({}, {}, "레거시 폴스타 SVR-1 프로세스", cfg) == "polestar_b0"

    def test_bank_hint_without_base_url_still_returns_b0(self):
        # base_url 미매핑이어도 위치 신호가 명확하면 db_id를 반환 → 호출부가 정확한 안내.
        from src.orchestration.process_query import _resolve_db_id

        cfg = _AppCfgStub(_ProcCfg({}), [])
        assert _resolve_db_id({}, {}, "은행 폴스타 ### 프로세스", cfg) == "polestar_b0"

    def test_gimpo_still_resolves_gp(self):
        from src.orchestration.process_query import _resolve_db_id

        cfg = _AppCfgStub(_ProcCfg({"polestar_cm_gp": "http://gp"}), ["polestar_cm_gp"])
        assert _resolve_db_id({}, {}, "김포 ### 서버 프로세스", cfg) == "polestar_cm_gp"


class TestRunProcessQueryUsesResolvedHostname:
    async def test_api_called_with_resolved_hostname(self, monkeypatch):
        """서버명으로 질의해도 프로세스 API는 해소된 hostname으로 호출되어야 한다."""
        from src.alarm.infrastructure.polestar_process_api import ProcessApiResult

        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp"])
        isolated = {
            "conversation_context": {
                "previous_db_ids": ["polestar_cm_gp"],
                # 직전 턴이 서버명으로 필터 → previous_entities에 서버명이 저장됨
                "previous_entities": [{"field": "hostname", "value": "WEB-SVR-01"}],
            }
        }

        # 서버명 → hostname 해소를 가짜로 대체 (DB 미연결 환경)
        async def _fake_resolve(self, db_id, value):
            assert value == "WEB-SVR-01"
            return "saisvd01"

        monkeypatch.setattr(
            "src.alarm.infrastructure.polestar_hostname_resolver."
            "PolestarHostnameResolver.resolve",
            _fake_resolve,
        )

        seen = {}

        async def _fake_list(self, db_id, hostname):
            seen["hostname"] = hostname
            return ProcessApiResult(
                captured_at=None,
                processes=[{"name": "java", "pid": 1, "p100cpu": 9.0, "pmem": 1.0, "args": "java"}],
            )

        monkeypatch.setattr(
            "src.alarm.infrastructure.polestar_process_api."
            "PolestarProcessApiClient.list_by_hostname",
            _fake_list,
        )

        out = await run_process_query(
            {"sub_query": "WEB-SVR-01 서버 현재 프로세스"}, isolated, llm=None, app_config=cfg
        )

        # API는 서버명이 아닌 해소된 hostname으로 호출
        assert seen["hostname"] == "saisvd01"
        # 관찰성: 원래 서버명과 해소된 hostname을 모두 보존
        assert out["process_query"]["server_name"] == "WEB-SVR-01"
        assert out["process_query"]["hostname"] == "saisvd01"
        # 요약에 서버명·호스트명 동시 표기
        assert "WEB-SVR-01" in out["organized_data"]["summary"]
        assert "saisvd01" in out["organized_data"]["summary"]

    async def test_resolution_failure_falls_back_to_raw(self, monkeypatch):
        """해소 실패(None) 시 원시 입력 값을 그대로 hostname으로 사용(회귀 없음)."""
        from src.alarm.infrastructure.polestar_process_api import ProcessApiResult

        alarm = _ProcCfg({"polestar_cm_gp": "http://gp"})
        cfg = _AppCfgStub(alarm, ["polestar_cm_gp"])
        isolated = {
            "conversation_context": {
                "previous_db_ids": ["polestar_cm_gp"],
                "previous_entities": [{"field": "hostname", "value": "saisvd01"}],
            }
        }

        async def _fake_resolve(self, db_id, value):
            return None  # 해소 불가

        monkeypatch.setattr(
            "src.alarm.infrastructure.polestar_hostname_resolver."
            "PolestarHostnameResolver.resolve",
            _fake_resolve,
        )

        seen = {}

        async def _fake_list(self, db_id, hostname):
            seen["hostname"] = hostname
            return ProcessApiResult(captured_at=None, processes=[])

        monkeypatch.setattr(
            "src.alarm.infrastructure.polestar_process_api."
            "PolestarProcessApiClient.list_by_hostname",
            _fake_list,
        )

        out = await run_process_query(
            {"sub_query": "해당 서버 현재 프로세스"}, isolated, llm=None, app_config=cfg
        )
        assert seen["hostname"] == "saisvd01"
        # 해소 전후 동일 → 요약에 중복 호스트명 표기를 하지 않음
        assert "호스트명" not in out["organized_data"]["summary"]
