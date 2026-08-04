"""멀티 DB 실행 실패 경로의 attempt·감사 기록 회귀 테스트 (Plan 69 P0-⑤).

실행 실패 시 QueryAttempt.sql=""·execution_time_ms=0으로 SQL이 유실되고
감사 로그가 성공 경로에만 기록되던 결함(단일 경로는 4경로 전부 감사)의
재발 방지.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.nodes import multi_db_executor as mdb
from src.state import create_initial_state

_SQL = "SELECT hostname FROM servers LIMIT 100"


def _fake_config():
    return SimpleNamespace(
        query=SimpleNamespace(default_limit=100),
        text2sql=SimpleNamespace(multi_full_validation=False),
        get_polestar_db_ids=lambda: set(),
    )


class _FailingClientCtx:
    """execute_sql이 예외를 던지는 DB 클라이언트 컨텍스트."""

    async def __aenter__(self):
        client = AsyncMock()
        client.execute_sql = AsyncMock(side_effect=RuntimeError("connection reset"))
        return client

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_failure_attempt_preserves_sql_and_audits(monkeypatch):
    """실행 실패 시 attempt에 실패 SQL이 보존되고 감사 로그가 기록된다."""

    class _Registry:
        def __init__(self, cfg):
            pass

        def is_registered(self, db_id):
            return True

        def get_client(self, db_id):
            return _FailingClientCtx()

    async def _schema(client, parsed, *, db_id, app_config):
        return {"tables": {"servers": {"columns": []}}}

    async def _gen(*args, **kwargs):
        return _SQL

    audit_mock = AsyncMock()
    monkeypatch.setattr(mdb, "DBRegistry", _Registry)
    monkeypatch.setattr(mdb, "_analyze_schema", _schema)
    monkeypatch.setattr(mdb, "_generate_sql", _gen)
    monkeypatch.setattr(mdb, "_validate_sql_simple", lambda sql, schema: None)
    monkeypatch.setattr(mdb, "get_domain_by_id", lambda db_id: None)
    monkeypatch.setattr(mdb, "log_query_execution", audit_mock)

    state = create_initial_state(user_query="서버 목록")
    state["target_databases"] = [{"db_id": "db_a"}]

    result = await mdb.multi_db_executor(
        state, llm=AsyncMock(), app_config=_fake_config()
    )

    attempts = result["query_attempts"]
    assert len(attempts) == 1
    assert attempts[0]["success"] is False
    assert attempts[0]["sql"] == _SQL, "실패 attempt에 SQL이 보존되어야 한다"

    assert audit_mock.await_count == 1, "실패 경로도 감사 로그를 기록해야 한다"
    kwargs = audit_mock.await_args.kwargs
    assert kwargs.get("success") is False
    assert kwargs.get("sql") == _SQL
    assert "connection reset" in (kwargs.get("error") or "")
