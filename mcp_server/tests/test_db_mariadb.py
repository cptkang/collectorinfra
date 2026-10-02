"""db.py MariaDB 경로 단위 테스트 (plans/95 W-2).

드라이버(aiomysql)를 목 모듈로 갈아끼워 연결 없이 검증한다 — 풀 생성 인자(autocommit·문장
타임아웃·DSN 해석)·실행 라우팅·행 정규화·풀 종료. 실 MariaDB 연결은
`test_mariadb_integration.py`(RUN_DOCKER_IT=1 옵트인)가 맡는다.
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from mcp_server.config import SourceConfig
from mcp_server.db import DBPoolManager, _mariadb_connect_kwargs

# =====================================================================
# DSN 해석
# =====================================================================


class TestMariadbConnectKwargs:
    def test_full_dsn(self):
        assert _mariadb_connect_kwargs("mariadb://ro:pw@db.local:3307/INST1") == {
            "host": "db.local",
            "port": 3307,
            "user": "ro",
            "password": "pw",
            "db": "INST1",
        }

    def test_default_port(self):
        assert _mariadb_connect_kwargs("mariadb://ro:pw@db.local/INST1")["port"] == 3306

    def test_mysql_scheme_accepted(self):
        assert _mariadb_connect_kwargs("mysql://ro:pw@h/d")["db"] == "d"

    def test_percent_encoded_credentials(self):
        """비밀번호의 특수문자(@ : /)는 퍼센트 인코딩으로 적는다."""
        kw = _mariadb_connect_kwargs("mariadb://r%40o:p%40ss%3Aw%2Fd@h:3307/INST1")
        assert kw["user"] == "r@o"
        assert kw["password"] == "p@ss:w/d"

    @pytest.mark.parametrize(
        "dsn",
        [
            "postgresql://ro:pw@h:5432/d",  # 다른 엔진 스킴
            "ro:pw@h:3307/INST1",  # 스킴 없음
            "mariadb://ro:pw@h:3307",  # database 없음
            "mariadb://ro:pw@h:3307/INST1?ssl=true",  # 쿼리 옵션 — 조용히 버리지 않는다
        ],
    )
    def test_rejected(self, dsn):
        with pytest.raises(ValueError):
            _mariadb_connect_kwargs(dsn)

    def test_error_message_hides_password(self):
        with pytest.raises(ValueError) as exc:
            _mariadb_connect_kwargs("mariadb://ro:SECRET-PW@h:3307/INST1?x=1")
        assert "SECRET-PW" not in str(exc.value)


# =====================================================================
# 풀·실행·종료 — aiomysql 목
# =====================================================================


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]], executed: list[str]) -> None:
        self._rows = rows
        self._executed = executed

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, args=None):
        assert args is None  # 인자 없이 실행해야 `%`가 포맷 지시자로 해석되지 않는다
        self._executed.append(query)

    async def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    def cursor(self, cursor_cls):
        assert cursor_cls is _FAKE_AIOMYSQL.DictCursor
        return _FakeCursor(self._pool.rows, self._pool.executed)


class _FakeAcquire:
    def __init__(self, pool: _FakePool) -> None:
        self._pool = pool

    async def __aenter__(self):
        return _FakeConn(self._pool)

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.kwargs = kwargs
        self.rows: list[dict[str, Any]] = []
        self.executed: list[str] = []
        self.closed = False
        self.wait_closed_called = False

    def acquire(self):
        return _FakeAcquire(self)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True


_CREATED: list[_FakePool] = []


async def _fake_create_pool(**kwargs):
    pool = _FakePool(kwargs)
    _CREATED.append(pool)
    return pool


_FAKE_AIOMYSQL = SimpleNamespace(create_pool=_fake_create_pool, DictCursor=object())


@pytest.fixture
def fake_aiomysql(monkeypatch):
    _CREATED.clear()
    monkeypatch.setitem(sys.modules, "aiomysql", _FAKE_AIOMYSQL)
    return _CREATED


def _source(**overrides) -> SourceConfig:
    base = dict(
        name="asset",
        type="mariadb",
        connection="mariadb://ro:pw@localhost:3307/INST1",
        query_timeout=17,
        pool_min_size=1,
        pool_max_size=3,
    )
    base.update(overrides)
    return SourceConfig(**base)


class TestMariadbPool:
    def test_pool_created_with_contract_kwargs(self, fake_aiomysql):
        pm = DBPoolManager([_source()])
        asyncio.run(pm.initialize())

        assert pm.is_source_active("asset")
        assert len(fake_aiomysql) == 1
        kw = fake_aiomysql[0].kwargs
        assert kw["autocommit"] is True  # 없으면 풀이 매 요청 재연결로 퇴화
        assert kw["init_command"] == "SET SESSION max_statement_time=17"
        assert kw["connect_timeout"] == 17
        assert (kw["minsize"], kw["maxsize"]) == (1, 3)
        assert (kw["host"], kw["port"], kw["user"], kw["db"]) == (
            "localhost", 3307, "ro", "INST1",
        )

    def test_execute_routes_and_normalizes(self, fake_aiomysql):
        pm = DBPoolManager([_source()])

        async def _run():
            await pm.initialize()
            fake_aiomysql[0].rows = [{"rate": Decimal("12.37"), "code": "Z9"}]
            return await pm.execute("asset", "SELECT 1 FROM t WHERE c LIKE 'a%'")

        rows = asyncio.run(_run())
        assert rows == [{"rate": 12.37, "code": "Z9"}]
        assert isinstance(rows[0]["rate"], float)
        assert fake_aiomysql[0].executed == ["SELECT 1 FROM t WHERE c LIKE 'a%'"]

    def test_health_check(self, fake_aiomysql):
        pm = DBPoolManager([_source()])

        async def _run():
            await pm.initialize()
            fake_aiomysql[0].rows = [{"ok": 1}]
            return await pm.health_check("asset")

        assert asyncio.run(_run()) is True
        assert fake_aiomysql[0].executed == ["SELECT 1 AS ok"]

    def test_bad_dsn_leaves_source_unregistered(self, fake_aiomysql):
        """DSN 오류는 PG 풀 실패와 같게 로그만 남고, 실행 시 '알 수 없는 소스'로 드러난다."""
        pm = DBPoolManager([_source(connection="postgresql://ro:pw@h/d")])

        async def _run():
            await pm.initialize()
            return await pm.execute("asset", "SELECT 1")

        with pytest.raises(ValueError, match="알 수 없는 소스"):
            asyncio.run(_run())
        assert fake_aiomysql == []

    def test_close_all_closes_and_waits(self, fake_aiomysql):
        pm = DBPoolManager([_source()])

        async def _run():
            await pm.initialize()
            await pm.close_all()

        asyncio.run(_run())
        assert fake_aiomysql[0].closed and fake_aiomysql[0].wait_closed_called
