"""DB 실행 에러 SQL의 파일 로그 기록 테스트 (D-160 FIX-G — D-140 커버리지 공백).

폐쇄망 실측(2026-08-21): DBHub isError → QueryExecutionError 경로가 log_sql 없이
재던져져 실패 SQL이 logs/sql에 통째로 빠졌다(감사 로그 수동 대조로만 확인 가능).
가장 진단 가치가 큰 실패 건이 기록되는지, 이중 기록이 없는지를 고정한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import DBHubConfig
from src.dbhub.client import DBHubClient, QueryExecutionError


def _make_client() -> DBHubClient:
    client = DBHubClient(DBHubConfig(
        server_url="http://localhost:9099/sse",
        source_name="polestar_cm_gp",
        mcp_call_timeout=10,
    ))
    # 연결 대역 — _ensure_connected_with_retry가 재연결을 시도하지 않게 한다
    client._connected = True
    client._mcp_session = MagicMock()
    return client


async def test_db_error_sql_is_file_logged(monkeypatch):
    """DBHub isError(DB 측 SQL 에러) 발생 시 실패 SQL이 error와 함께 기록된다."""
    client = _make_client()
    client._call_tool = AsyncMock(return_value=SimpleNamespace(
        isError=True,
        content=[SimpleNamespace(text='invalid input syntax for type bigint: "4.0"')],
    ))
    logged: list[dict] = []
    from src.dbhub import client as client_mod

    monkeypatch.setattr(
        client_mod.sql_file_logger, "log_sql",
        lambda sql, **kw: logged.append({"sql": sql, **kw}),
    )

    failing_sql = "SELECT SUM(CAST(cc.stringvalue_short AS BIGINT)) FROM t"
    with pytest.raises(QueryExecutionError):
        await client.execute_sql(failing_sql)

    assert len(logged) == 1, "실패 1건당 정확히 1회 기록(이중 기록 금지)"
    assert logged[0]["sql"] == failing_sql
    assert "bigint" in (logged[0].get("error") or "")


async def test_success_sql_still_logged_once(monkeypatch):
    """기존 성공 경로 기록은 불변(회귀 방지)."""
    client = _make_client()
    client._call_tool = AsyncMock(return_value=SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text='{"columns": [], "rows": []}')],
    ))
    logged: list[dict] = []
    from src.dbhub import client as client_mod

    monkeypatch.setattr(
        client_mod.sql_file_logger, "log_sql",
        lambda sql, **kw: logged.append({"sql": sql, **kw}),
    )

    await client.execute_sql("SELECT 1")

    assert len(logged) == 1
    assert logged[0].get("error") is None
