"""멀티 DB 동일 스키마 소급 복구 테스트 (D-150).

버그(2026-08-04 실측): 공동존 gp/yd 멀티 조회에서 첫 DB(gp)의 LLM SQL 생성이 형식
비결정성으로 2회 연속 추출·검증에 실패하면 gp만 "SQL 검증 실패: SELECT 문이 아닙니다"로
누락되고 yd만 반환됐다. gp/yd는 (postgresql, polestar) 동일 스키마라 yd의 검증 통과
SQL을 gp에 재실행하면 결정적으로 복구된다(D-066 후속6 재사용 시맨틱의 대칭 완성).
전부 mock — LLM·네트워크 미사용.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.nodes.multi_db_executor as mde

_GOOD_SQL = "SELECT hostname, os_type FROM polestar.cmm_resource WHERE dtime IS NULL LIMIT 100"
_PROSE = "요청하신 조회를 위해서는 추가 정보가 필요합니다."  # 추출 실패 형태(비-SQL)


class _FakeClient:
    """execute_sql 호출을 기록하는 가짜 DBHub 클라이언트."""

    def __init__(self, db_id: str, calls: list):
        self._db_id = db_id
        self._calls = calls

    async def execute_sql(self, sql: str):
        self._calls.append((self._db_id, sql))
        return SimpleNamespace(rows=[{"hostname": f"{self._db_id}-srv", "os_type": "Linux"}], row_count=1)


class _FakeRegistry:
    def __init__(self, calls: list):
        self._calls = calls

    def is_registered(self, db_id: str) -> bool:
        return True

    def get_client(self, db_id: str):
        calls = self._calls
        client = _FakeClient(db_id, calls)

        class _Ctx:
            async def __aenter__(self):
                return client

            async def __aexit__(self, *args):
                return False

        return _Ctx()


def _app_config():
    return SimpleNamespace(query=SimpleNamespace(default_limit=1000))


def _state(targets):
    return {
        "user_query": "전체 서버들에 대해 OS 종류를 확인하시오",
        "target_databases": targets,
        "parsed_requirements": {"original_query": "OS 종류 확인"},
        "query_attempts": [],
    }


def _patch_common(monkeypatch, calls, gen_by_db):
    """스키마 분석·SQL 생성·레지스트리·감사 로깅을 결정적 mock으로 고정한다."""

    async def fake_analyze(client, parsed, *, db_id, app_config):
        return {"tables": {"polestar.cmm_resource": {"columns": []}}}

    async def fake_generate(llm, parsed, schema_info, sub_ctx, limit, **kwargs):
        db_id = kwargs.get("db_id")
        return gen_by_db[db_id].pop(0)

    monkeypatch.setattr(mde, "_analyze_schema", fake_analyze)
    monkeypatch.setattr(mde, "_generate_sql", fake_generate)
    monkeypatch.setattr(mde, "DBRegistry", lambda cfg: _FakeRegistry(calls))
    monkeypatch.setattr(mde, "log_query_execution", AsyncMock())


@pytest.mark.asyncio
async def test_first_db_validation_failure_recovered_by_same_schema_sql(monkeypatch):
    """gp 생성 3회 연속 실패(원 시도+재생성 2회) → yd 검증 통과 SQL로 gp 소급 복구(존 누락 0)."""
    calls: list = []
    # gp: 산문 3회(원 시도 + 재생성 2회 모두 검증 실패 — D-150 후속1), yd: 정상 SQL
    gen_by_db = {
        "polestar_cm_gp": [_PROSE, _PROSE, _PROSE],
        "polestar_cm_yd": [_GOOD_SQL],
    }
    _patch_common(monkeypatch, calls, gen_by_db)

    targets = [
        {"db_id": "polestar_cm_gp", "sub_query_context": "OS 종류 확인"},
        {"db_id": "polestar_cm_yd", "sub_query_context": "OS 종류 확인"},
    ]
    result = await mde.multi_db_executor(
        _state(targets), llm=MagicMock(), app_config=_app_config()
    )

    # 두 존 모두 결과 존재 — gp가 yd의 SQL로 복구됨
    assert set(result["db_results"].keys()) == {"polestar_cm_gp", "polestar_cm_yd"}
    assert result["db_errors"] == {}
    assert result["error_message"] is None
    # gp 실행은 yd의 검증 통과 SQL로 수행됨
    gp_calls = [sql for db, sql in calls if db == "polestar_cm_gp"]
    assert gp_calls == [_GOOD_SQL]
    # 병합 결과에 양쪽 소스 태그 존재
    assert {r["_source_db"] for r in result["query_results"]} == {
        "polestar_cm_gp", "polestar_cm_yd",
    }


@pytest.mark.asyncio
async def test_no_recovery_source_keeps_original_error(monkeypatch):
    """동일 스키마의 검증 통과 SQL이 없으면(단일 존) 원 검증 에러를 유지한다."""
    calls: list = []
    gen_by_db = {"polestar_cm_gp": [_PROSE, _PROSE, _PROSE]}
    _patch_common(monkeypatch, calls, gen_by_db)

    targets = [{"db_id": "polestar_cm_gp", "sub_query_context": "OS 종류 확인"}]
    result = await mde.multi_db_executor(
        _state(targets), llm=MagicMock(), app_config=_app_config()
    )

    assert "polestar_cm_gp" in result["db_errors"]
    assert "SQL 검증 실패" in result["db_errors"]["polestar_cm_gp"]
    assert calls == []  # 검증 실패 SQL은 실행되지 않음


@pytest.mark.asyncio
async def test_recovery_execution_failure_keeps_original_error(monkeypatch):
    """복구 재실행 자체가 실패하면 원 검증 에러를 유지한다(침묵 폴백 금지 — 로그로 가시화)."""
    calls: list = []
    gen_by_db = {
        "polestar_cm_gp": [_PROSE, _PROSE, _PROSE],
        "polestar_cm_yd": [_GOOD_SQL],
    }
    _patch_common(monkeypatch, calls, gen_by_db)

    # 복구 시점의 gp 실행만 실패시키는 레지스트리
    class _FailingOnRecovery(_FakeRegistry):
        def __init__(self, calls):
            super().__init__(calls)
            self._yd_done = False

        def get_client(self, db_id):
            if db_id == "polestar_cm_gp" and self._yd_done:
                class _Boom:
                    async def __aenter__(self):
                        raise ConnectionError("gp 연결 실패")

                    async def __aexit__(self, *args):
                        return False

                return _Boom()
            if db_id == "polestar_cm_yd":
                self._yd_done = True
            return super().get_client(db_id)

    import src.nodes.multi_db_executor as _m
    _m.DBRegistry = lambda cfg: _FailingOnRecovery(calls)

    targets = [
        {"db_id": "polestar_cm_gp", "sub_query_context": "OS 종류 확인"},
        {"db_id": "polestar_cm_yd", "sub_query_context": "OS 종류 확인"},
    ]
    result = await mde.multi_db_executor(
        _state(targets), llm=MagicMock(), app_config=_app_config()
    )

    assert "polestar_cm_gp" in result["db_errors"]  # 원 에러 유지
    assert "polestar_cm_yd" in result["db_results"]  # yd는 정상


_FILTER_BLOCKED = (
    "Your request was blocked by the filter. filterBlockReason: personal information"
)


@pytest.mark.asyncio
async def test_filter_blocked_response_fails_fast_with_clear_reason(monkeypatch):
    """PII 필터 차단 응답(D-150 후속2): 재생성 중단(동일 프롬프트 재차단) + 명확한 사유.

    gen_by_db에 차단 응답 1건만 둔다 — 재시도가 발생하면 IndexError로 실패하므로
    "1회만 호출"이 테스트로 고정된다.
    """
    calls: list = []
    gen_by_db = {"polestar_cm_gp": [_FILTER_BLOCKED]}
    _patch_common(monkeypatch, calls, gen_by_db)

    targets = [{"db_id": "polestar_cm_gp", "sub_query_context": "OS 종류 확인"}]
    result = await mde.multi_db_executor(
        _state(targets), llm=MagicMock(), app_config=_app_config()
    )

    assert "polestar_cm_gp" in result["db_errors"]
    assert "PII 필터 차단" in result["db_errors"]["polestar_cm_gp"]
    # 차단문 발췌가 UI 에러에 실린다(로그 없는 환경 자가 진단 — D-150 후속2)
    assert "LLM 산출 발췌" in result["db_errors"]["polestar_cm_gp"]
    assert calls == []  # 차단 응답은 실행되지 않음


@pytest.mark.asyncio
async def test_prose_failure_error_includes_output_excerpt(monkeypatch):
    """비-SQL 산출 최종 실패 시 db_errors에 산출 발췌가 실린다(UI 자가 진단 — D-150 후속2)."""
    calls: list = []
    gen_by_db = {"polestar_cm_gp": [_PROSE, _PROSE, _PROSE]}
    _patch_common(monkeypatch, calls, gen_by_db)

    targets = [{"db_id": "polestar_cm_gp", "sub_query_context": "OS 종류 확인"}]
    result = await mde.multi_db_executor(
        _state(targets), llm=MagicMock(), app_config=_app_config()
    )

    err = result["db_errors"]["polestar_cm_gp"]
    assert "SELECT 문이 아닙니다" in err
    assert "LLM 산출 발췌" in err
    assert "추가 정보가 필요" in err  # 산문 앞부분이 그대로 보임
