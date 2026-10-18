"""MCP 서버 읽기 전용 강화 테스트 (plans/163 W1 S1·S1+ · D-003 강화).

바뀌는 동작을 단언한다.

- S1+ 스키마 조회 헬퍼·고수준 도구의 값 보간 주입 거부(작은따옴표 탈출 · 백슬래시 탈출).
- S1 검사기 허용 목록화 — 단일 문장 · `SELECT`/`WITH … SELECT`만. W0이 확인한 우회 구문 전부 거부.
- 공용 실행 지점 — `DBPoolManager.execute` 한 곳에서 readonly 소스의 모든 경로(고수준 도구 ·
  exporter · 스키마 헬퍼 · 헬스체크)가 검사를 받는다.
- 세션 읽기 전용 — PG `default_transaction_read_only` · MariaDB `transaction_read_only`.

실연결(로컬 도커 PG·MariaDB)은 `RUN_DOCKER_IT=1` 옵트인이다. 옵트인했는데 미도달이면 skip이 아니라
실패한다(침묵 skip 금지 — D-181). 쓰기 시도는 RO 세션 연결에서 0행 대상으로만 하고 트랜잭션을
롤백한다.
"""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp_server.config import AppServerConfig, SourceConfig  # noqa: E402
from mcp_server.db import DBPoolManager  # noqa: E402
from mcp_server.security import ReadOnlyViolationError, validate_readonly  # noqa: E402

from mcp_server import polestar_exporter as ex  # noqa: E402
from mcp_server import polestar_tools as pt  # noqa: E402
from mcp_server import tools as tl  # noqa: E402

# =====================================================================
# 공용 대역
# =====================================================================


class _CaptureMCP:
    """@mcp.tool() 등록 함수를 이름→함수로 포획한다."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


_REGISTRY = {"postgresql": "_pg_pools", "db2": "_db2_configs", "mariadb": "_mariadb_pools"}
_RUNNER = {"postgresql": "_execute_pg", "db2": "_execute_db2", "mariadb": "_execute_mariadb"}


def _pool_with_fake_runner(
    monkeypatch: pytest.MonkeyPatch, engine: str = "postgresql", readonly: bool = True
) -> tuple[DBPoolManager, list[str]]:
    """실 `DBPoolManager`에 엔진 러너만 가짜로 바꾼 풀 — 러너에 도달한 SQL을 모은다."""
    src = SourceConfig(name="src", type=engine, connection="x", readonly=readonly)
    pm = DBPoolManager([src])
    getattr(pm, _REGISTRY[engine])["src"] = src
    reached: list[str] = []

    async def _runner(source: str, sql: str) -> list[dict[str, Any]]:
        reached.append(sql)
        return [{"ok": 1}]

    monkeypatch.setattr(pm, _RUNNER[engine], _runner)
    return pm, reached


def _ctx(pm: Any) -> SimpleNamespace:
    return SimpleNamespace(
        request_context=SimpleNamespace(
            lifespan_context={"pool_manager": pm, "config": AppServerConfig()}
        )
    )


# =====================================================================
# S1+ — 스키마 조회 헬퍼 주입 거부
# =====================================================================

_INJECTED_TABLE_NAMES = [
    "servers' UNION SELECT current_user --",
    "public.servers' OR '1'='1",
    "servers\\' UNION SELECT current_user -- ",
    "servers; DROP TABLE servers",
    "servers /* x */",
    'servers" OR 1=1',
    "",
]


@pytest.mark.parametrize("engine", ["postgresql", "db2", "mariadb"])
@pytest.mark.parametrize("table_name", _INJECTED_TABLE_NAMES)
async def test_get_table_schema_rejects_injected_table_name(
    engine: str, table_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """식별자가 아닌 테이블명은 SQL을 만들지 않고 기존 도구 오류 형태(`{"error"}`)로 거부한다."""
    pm, reached = _pool_with_fake_runner(monkeypatch, engine)
    capture = _CaptureMCP()
    tl.register_tools(capture)
    out = json.loads(
        await capture.tools["get_table_schema"](source="src", table_name=table_name, ctx=_ctx(pm))
    )
    assert set(out) == {"error"}
    assert reached == []


_PG_DB2_HELPERS = [
    "_pg_get_columns", "_pg_get_primary_keys", "_pg_get_foreign_keys",
    "_db2_get_columns", "_db2_get_primary_keys", "_db2_get_foreign_keys",
]


@pytest.mark.parametrize("helper", _PG_DB2_HELPERS)
@pytest.mark.parametrize(
    "table_name", ["servers' UNION SELECT current_user --", "a.b' OR 'x'='x", "x\\'"]
)
async def test_pg_db2_helpers_reject_injection_directly(helper: str, table_name: str) -> None:
    """헬퍼를 직접 불러도(도구 밖 호출) 주입 문자열은 SQL로 가지 않는다."""

    class _Pool:
        sql: list[str] = []

        async def execute(self, source: str, sql: str) -> list[dict[str, Any]]:
            self.sql.append(sql)
            return []

    pool = _Pool()
    pool.sql = []
    with pytest.raises(ValueError):
        await getattr(tl, helper)(pool, "src", table_name)
    assert pool.sql == []


@pytest.mark.parametrize("engine", ["postgresql", "db2", "mariadb"])
@pytest.mark.parametrize("table_name", ["servers", "myschema.servers", "TCDMSIF79", "서버_목록"])
async def test_get_table_schema_accepts_plain_identifiers(
    engine: str, table_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """일반 식별자(스키마 한정 · 대문자 · 한글)는 그대로 조회한다."""
    pm, reached = _pool_with_fake_runner(monkeypatch, engine)
    monkeypatch.setattr(pm, _RUNNER[engine], _empty_runner(reached))
    capture = _CaptureMCP()
    tl.register_tools(capture)
    out = json.loads(
        await capture.tools["get_table_schema"](source="src", table_name=table_name, ctx=_ctx(pm))
    )
    assert "error" not in out
    assert len(reached) == 3


def _empty_runner(reached: list[str]):
    async def _runner(source: str, sql: str) -> list[dict[str, Any]]:
        reached.append(sql)
        return []

    return _runner


# =====================================================================
# S1+ — 고수준 도구 값 리터럴의 백슬래시 탈출 거부
# =====================================================================


@pytest.mark.parametrize(
    "value", ["web01\\' UNION SELECT 1 -- ", "a\\", "\\'"]
)
def test_sql_literal_rejects_backslash(value: str) -> None:
    """백슬래시가 든 값은 리터럴로 만들지 않는다(MariaDB 기본 sql_mode에서 `\\'` 탈출)."""
    with pytest.raises(ValueError):
        pt._sql_literal(value)


def test_sql_literal_keeps_quote_doubling() -> None:
    """작은따옴표 이중화·널바이트 제거 계약은 그대로다."""
    assert pt._sql_literal("o'drop\x00") == "'o''drop'"


@pytest.mark.parametrize(
    ("tool", "kwargs"),
    [
        ("polestar_resource_status", {"server_name": "web01\\'"}),
        ("polestar_topology", {"server_name": "web01\\'"}),
        ("polestar_os_config", {"hostname": "web01\\'"}),
        ("polestar_change_history", {"server_name": "web01\\'"}),
        ("polestar_condition_log", {"alarm_id": "A\\'"}),
        ("polestar_alarm_history", {"server_name": "web01\\'", "alarm_name": "x"}),
        (
            "polestar_metric_trend",
            {"server_name": "web01\\'", "kind": "cpu"},
        ),
    ],
)
async def test_polestar_tools_return_error_on_backslash_value(
    tool: str, kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """빌더가 값을 거부하면 도구는 예외를 전파하지 않고 `{"error"}`를 돌려주며 DB에 가지 않는다."""
    pm, reached = _pool_with_fake_runner(monkeypatch, "postgresql")
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    out = json.loads(await capture.tools[tool](source="src", ctx=_ctx(pm), **kwargs))
    assert set(out) == {"error"}
    assert reached == []


# =====================================================================
# S1 — 검사기 허용 목록화
# =====================================================================

_BYPASS_SQL = [
    # 문장 유형 — SELECT·WITH가 아닌 것 (W0 S1-b 목록)
    "SET TRANSACTION READ WRITE",
    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE",
    "COPY (SELECT 1) TO PROGRAM 'id'",
    "COPY servers TO STDOUT",
    "LOCK TABLE servers",
    "DO $$BEGIN PERFORM 1; END$$",
    "VACUUM servers",
    "REINDEX TABLE servers",
    "REFRESH MATERIALIZED VIEW mv",
    "PREPARE p AS SELECT 1",
    "SET ROLE postgres",
    "BEGIN",
    "START TRANSACTION",
    "COMMIT",
    "LOAD DATA INFILE '/etc/passwd' INTO TABLE t",
    "INSTALL PLUGIN x SONAME 'x.so'",
    "SET GLOBAL read_only=0",
    "EXPLAIN ANALYZE SELECT 1",
    "VALUES (1)",
    "TABLE servers",
    "SHOW ALL",
    # SELECT … INTO (PG 테이블 생성 · MariaDB 파일 출력 · 변수)
    "SELECT * INTO new_tbl FROM servers",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT * FROM t INTO DUMPFILE '/tmp/x'",
    "SELECT 1 INTO @v",
    # 잠금
    "SELECT * FROM t FOR UPDATE",
    "SELECT * FROM t FOR SHARE",
    "SELECT * FROM t FOR NO KEY UPDATE",
    "SELECT * FROM t FOR KEY SHARE",
    "SELECT * FROM t LOCK IN SHARE MODE",
    # 데이터 변경 CTE
    "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",
    "WITH x AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM x",
    "WITH x AS (SELECT 1) INSERT INTO t SELECT * FROM x",
    # 부작용 함수
    "SELECT set_config('default_transaction_read_only','off',false)",
    "SELECT pg_catalog.set_config('a','b',false)",
    "SELECT pg_terminate_backend(123)",
    "SELECT pg_cancel_backend(123)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_read_binary_file('/etc/passwd')",
    "SELECT * FROM pg_ls_dir('.')",
    "SELECT lo_import('/etc/passwd')",
    "SELECT lo_export(1, '/tmp/x')",
    "SELECT nextval('s')",
    "SELECT NEXTVAL ('s')",
    'SELECT "nextval"(\'s\')',
    "SELECT setval('s', 1)",
    "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)",
    "SELECT dblink_exec('host=x', 'DELETE FROM t')",
    "SELECT pg_advisory_lock(1)",
    "SELECT pg_advisory_xact_lock(1)",
    "SELECT pg_try_advisory_lock(1)",
    "SELECT get_lock('a', 1)",
    "SELECT load_file('/etc/passwd')",
    "SELECT sys_exec('id')",
    "SELECT query_to_xml('DELETE FROM t RETURNING 1', true, false, '')",
    # 다중 문장
    "SELECT 1; SELECT 2",
    "SELECT 1;SET ROLE x",
    "SELECT 1; -- c\nSET ROLE x",
    # 엔진별 주석·리터럴 해석 차이로 검사기에서 숨기는 시도
    "SELECT 1 /*! , load_file('/etc/passwd') */",
    "SELECT 1 /*M!100000 , load_file('/etc/passwd') */",
    "SELECT 1 --load_file('/etc/passwd')",
    "SELECT 1 # nextval('s')",
    "SELECT 'x\\'' , load_file('/etc/passwd') -- '",
    "SELECT E'\\'' , pg_read_file('/etc/passwd') -- '",
    "SELECT 1 # '\n, load_file('/x') -- '",
    # 교정 M1 — PG 유니코드 이스케이프 식별자·문자열(U&"…" · U&'…')로 함수 이름 숨기기
    "SELECT U&\"set\\005fconfig\"('default_transaction_read_only','off',false)",
    "SELECT u&\"nextval\"('s')",
    "SELECT U&'\\0041' AS a",
    # 교정 M2 — 부작용 함수·구문 보강
    "SELECT pg_sleep(10)",
    "SELECT pg_sleep_for('5 minutes')",
    "SELECT lo_open(1, 131072)",
    "SELECT lo_get(1)",
    "SELECT lo_truncate(1, 0)",
    "SELECT loread(0, 10)",
    "SELECT lowrite(0, 'x')",
    "SELECT table_to_xml('t', true, false, '')",
    "SELECT schema_to_xml('public', true, false, '')",
    "SELECT cursor_to_xml('c', 1, true, false, '')",
    "SELECT pg_stat_reset()",
    "SELECT pg_stat_reset_shared('bgwriter')",
    "SELECT pg_wal_replay_pause()",
    "SELECT pg_wal_replay_resume()",
    "SELECT pg_backup_start('x')",
    "SELECT pg_replication_origin_create('x')",
    "SELECT pg_replication_slot_advance('s', '0/0')",
    "SELECT pg_copy_logical_replication_slot('a', 'b')",
    "SELECT * FROM pg_logical_slot_get_changes('s', NULL, NULL)",
    "SELECT pg_log_backend_memory_contexts(1)",
    "SELECT SLEEP(10)",
    "SELECT BENCHMARK(1000000, MD5('x'))",
    "SELECT @a := 1",
    "SELECT NEXT VALUE FOR seq1",
    "SELECT NEXTVAL FOR seq1 FROM SYSIBM.SYSDUMMY1",
    "SELECT * FROM TABLE(SYSPROC.ADMIN_CMD('REORG TABLE t')) AS x",
    "SELECT SYSPROC.ADMIN_CMD('RUNSTATS ON TABLE t') FROM SYSIBM.SYSDUMMY1",
    "SELECT UTL_FILE.FOPEN('D', 'f', 'w') FROM SYSIBM.SYSDUMMY1",
    "SELECT SYSPROC . ENV_GET_INST_INFO() FROM SYSIBM.SYSDUMMY1",
    # 후속 교정(security-auditor Low) — 통계 초기화 · 트랜잭션 ID 할당 · 인덱스 정비
    "SELECT pg_stat_statements_reset()",
    "SELECT txid_current()",
    "SELECT pg_current_xact_id()",
    "SELECT brin_summarize_new_values('idx'::regclass)",
    "SELECT brin_summarize_range('idx'::regclass, 0)",
    "SELECT brin_desummarize_range('idx'::regclass, 0)",
    "SELECT gin_clean_pending_list('idx'::regclass)",
]


@pytest.mark.parametrize("sql", _BYPASS_SQL)
def test_validate_readonly_rejects_bypass(sql: str) -> None:
    """금지 목록이 통과시키던 비-SELECT·부작용 구문을 허용 목록 검사기가 거부한다."""
    with pytest.raises(ReadOnlyViolationError):
        validate_readonly(sql)


_ALLOWED_SQL = [
    "SELECT 1",
    "select * from servers",
    "  SELECT 1;  ",
    "SELECT 1;\n-- 끝",
    "WITH a AS (SELECT 1 AS x) SELECT x FROM a",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) SELECT n FROM r",
    "(SELECT 1) UNION (SELECT 2)",
    "SELECT 'nextval(1); DROP TABLE t' AS s",
    "SELECT * FROM t WHERE note = 'pg_read_file(x) INTO y FOR UPDATE'",
    "SELECT 1 -- set_config(\n",
    "SELECT 1 /* load_file( */",
    "SELECT update_time, created_into FROM t",
    "SELECT * FROM t WHERE c LIKE 'a%' FETCH FIRST 10 ROWS ONLY",
    'SELECT "Hostname" FROM "polestar_view"',
    # 교정 M2 오탐 확인 — 이름이 비슷한 일반 함수·컬럼은 막지 않는다
    "SELECT lower(name), lo, slope, next_value FROM t",
    "SELECT xml_data, sleep_count FROM t WHERE a = 'SLEEP(1)'",
    "SELECT * FROM t WHERE a >= 1 AND b <= 2 AND c <> 3",
    "SELECT 'U&\"x\"' AS s",
    # 후속 교정 오탐 확인 — 트랜잭션 ID·스냅숏을 할당 없이 읽기만 하는 함수는 막지 않는다
    "SELECT txid_current_if_assigned(), pg_current_xact_id_if_assigned()",
    "SELECT txid_current_snapshot(), pg_current_snapshot()",
]


@pytest.mark.parametrize("sql", _ALLOWED_SQL)
def test_validate_readonly_allows_plain_select(sql: str) -> None:
    """정상 SELECT·WITH…SELECT와 리터럴·주석 안의 단어는 막지 않는다."""
    validate_readonly(sql)


# =====================================================================
# 공용 실행 지점 — DBPoolManager.execute
# =====================================================================

_WRITE_SQL = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "DROP TABLE t",
    "CREATE TABLE t (a int)",
    "SET TRANSACTION READ WRITE",
]


@pytest.mark.parametrize("engine", ["postgresql", "db2", "mariadb"])
@pytest.mark.parametrize("sql", _WRITE_SQL)
async def test_execute_blocks_write_on_readonly_source(
    engine: str, sql: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """readonly 소스면 엔진과 무관하게 실행 지점에서 차단되고 러너에 도달하지 않는다."""
    pm, reached = _pool_with_fake_runner(monkeypatch, engine)
    with pytest.raises(ReadOnlyViolationError):
        await pm.execute("src", sql)
    assert reached == []


async def test_execute_allows_select_on_readonly_source(monkeypatch: pytest.MonkeyPatch) -> None:
    pm, reached = _pool_with_fake_runner(monkeypatch)
    assert await pm.execute("src", "SELECT 1 AS ok") == [{"ok": 1}]
    assert reached == ["SELECT 1 AS ok"]


async def test_execute_readonly_false_switch_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    """소스 `readonly=false` 스위치는 현행 유지 — 검사를 건너뛴다(보고 대상)."""
    pm, reached = _pool_with_fake_runner(monkeypatch, readonly=False)
    await pm.execute("src", "SET TRANSACTION READ WRITE")
    assert reached == ["SET TRANSACTION READ WRITE"]


async def test_polestar_tool_path_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """고수준 도구가 만든 SQL이 쓰기라면 실행 지점에서 막혀 `{"error"}`로 돌아온다."""
    pm, reached = _pool_with_fake_runner(monkeypatch)
    monkeypatch.setattr(pt, "build_resource_status_sql", lambda *a, **k: "DELETE FROM t")
    capture = _CaptureMCP()
    pt.register_polestar_tools(capture)
    out = json.loads(
        await capture.tools["polestar_resource_status"](
            source="src", server_name="web01", ctx=_ctx(pm)
        )
    )
    assert "읽기 전용 위반" in out["error"]
    assert reached == []


async def test_exporter_path_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """exporter 전용 풀(`DBPoolManager`)도 같은 실행 지점 검사를 받는다."""
    pm, reached = _pool_with_fake_runner(monkeypatch)
    monkeypatch.setattr(ex, "build_server_info_sql", lambda *a, **k: "UPDATE t SET a = 1")
    bridge = object.__new__(ex.PolestarBridge)
    src = pm.get_source_config("src")
    snap = await bridge._snapshot(pm, ex.BridgeSource(db_id="src", zone="z", source=src), "x")
    assert snap.up is False
    assert reached == []


async def test_schema_helper_path_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """스키마 헬퍼 SQL도 실행 지점 검사를 받는다(정상 식별자는 통과)."""
    pm, reached = _pool_with_fake_runner(monkeypatch)
    await tl._pg_get_columns(pm, "src", "servers")
    assert len(reached) == 1


@pytest.mark.parametrize("readonly", [True, False])
async def test_health_check_goes_through_guard(
    readonly: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """헬스체크 상수 SQL도 readonly 소스면 같은 검사를 받는다."""
    from mcp_server import db as db_mod

    pm, reached = _pool_with_fake_runner(monkeypatch, readonly=readonly)
    seen: list[str] = []
    monkeypatch.setattr(db_mod, "validate_readonly", lambda sql: seen.append(sql))
    assert await pm.health_check("src") is True
    assert seen == (["SELECT 1 AS ok"] if readonly else [])


# =====================================================================
# 세션 읽기 전용 — 풀 생성 인자
# =====================================================================


class _KwCapture:
    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    async def create_pool(self, **kwargs: Any) -> object:
        self.kwargs.append(kwargs)
        return object()


@pytest.mark.parametrize("readonly", [True, False])
async def test_pg_pool_session_readonly(readonly: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _KwCapture()
    monkeypatch.setitem(sys.modules, "asyncpg", SimpleNamespace(create_pool=fake.create_pool))
    pm = DBPoolManager(
        [SourceConfig(name="pg", type="postgresql", connection="x", readonly=readonly)]
    )
    await pm.initialize()
    kw = fake.kwargs[0]
    if readonly:
        assert kw["server_settings"] == {"default_transaction_read_only": "on"}
    else:
        assert "server_settings" not in kw


@pytest.mark.parametrize("readonly", [True, False])
async def test_mariadb_pool_session_readonly(
    readonly: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _KwCapture()
    monkeypatch.setitem(
        sys.modules, "aiomysql", SimpleNamespace(create_pool=fake.create_pool, DictCursor=object())
    )
    pm = DBPoolManager(
        [
            SourceConfig(
                name="m", type="mariadb", connection="mariadb://ro:pw@h:3307/D",
                query_timeout=17, readonly=readonly,
            )
        ]
    )
    await pm.initialize()
    expected = (
        "SET SESSION max_statement_time=17, SESSION transaction_read_only=1"
        if readonly
        else "SET SESSION max_statement_time=17"
    )
    assert fake.kwargs[0]["init_command"] == expected


# =====================================================================
# 세션 읽기 전용 — 로컬 도커 실연결 (RUN_DOCKER_IT=1)
# =====================================================================

_DOCKER = pytest.mark.skipif(
    os.environ.get("RUN_DOCKER_IT") != "1",
    reason="로컬 도커 DB 미사용 — RUN_DOCKER_IT=1로 옵트인 시에만 실행",
)


def _pg_dsn() -> str:
    host = os.environ.get("DOCKER_IT_PG_HOST", "localhost")
    port = os.environ.get("DOCKER_IT_PG_PORT", "5434")
    db = os.environ.get("DOCKER_IT_PG_DB", "infradb")
    user = os.environ.get("DOCKER_IT_PG_USER", "polestar_user")
    password = os.environ.get("DOCKER_IT_PG_PASSWORD", "polestar_pass_2024")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _mariadb_dsn() -> str:
    host = os.environ.get("DOCKER_IT_ITAM_HOST", "localhost")
    port = os.environ.get("DOCKER_IT_ITAM_PORT", "3307")
    db = os.environ.get("DOCKER_IT_ITAM_DB", "INST1")
    user = os.environ.get("DOCKER_IT_ITAM_USER", "itam_ro")
    password = os.environ.get("DOCKER_IT_ITAM_PASSWORD", "itam_ro_pass_2024")
    return f"mariadb://{user}:{password}@{host}:{port}/{db}"


@_DOCKER
async def test_pg_session_rejects_write_in_readonly_session() -> None:
    """RO 세션 연결에서 0행 쓰기가 `ReadOnlySQLTransactionError`로 거부된다(트랜잭션 롤백)."""
    import asyncpg

    pm = DBPoolManager([SourceConfig(name="pg", type="postgresql", connection=_pg_dsn())])
    await pm.initialize()
    try:
        assert "pg" in pm._pg_pools, "PG 픽스처 미도달 — RUN_DOCKER_IT=1 · 도커 PG(5434) 확인"
        async with pm._pg_pools["pg"].acquire() as conn:
            assert await conn.fetchval("SHOW default_transaction_read_only") == "on"
            target = await conn.fetchrow(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' "
                "AND table_schema NOT IN ('pg_catalog', 'information_schema') LIMIT 1"
            )
            assert target is not None
            qualified = '"{}"."{}"'.format(
                target["table_schema"].replace('"', '""'),
                target["table_name"].replace('"', '""'),
            )
            with pytest.raises(asyncpg.exceptions.ReadOnlySQLTransactionError):
                async with conn.transaction():
                    await conn.execute(f"DELETE FROM {qualified} WHERE false")
    finally:
        await pm.close_all()


@_DOCKER
async def test_mariadb_session_is_readonly() -> None:
    """RO 세션 연결은 `transaction_read_only=1`이고 0행 쓰기가 DB에서 거부된다.

    로컬 `itam_ro`는 SELECT 권한만 있어 권한 오류(1142)가 먼저 날 수 있다 — 1142·1792 중
    하나를 받는다.
    """
    import pymysql

    pm = DBPoolManager([SourceConfig(name="m", type="mariadb", connection=_mariadb_dsn())])
    await pm.initialize()
    try:
        assert "m" in pm._mariadb_pools, "MariaDB 픽스처 미도달 — RUN_DOCKER_IT=1 · 3307 확인"
        rows = await pm.execute(
            "m",
            "SELECT @@SESSION.transaction_read_only AS ro, "
            "@@SESSION.max_statement_time AS mst",
        )
        assert int(rows[0]["ro"]) == 1
        assert float(rows[0]["mst"]) == 30.0
        tables = await pm.execute(
            "m",
            "SELECT table_name AS t FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' LIMIT 1",
        )
        assert tables
        with pytest.raises(pymysql.err.MySQLError) as exc:
            await pm._execute_mariadb("m", f"DELETE FROM `{tables[0]['t']}` WHERE 1 = 0")
        assert exc.value.args[0] in (1142, 1792)
    finally:
        await pm.close_all()


# =====================================================================
# 교정 M3 — search_objects 패턴 백슬래시 거부 (PG·DB2)
# =====================================================================


@pytest.mark.parametrize("fn", ["_pg_search_objects_sql", "_db2_search_objects_sql"])
@pytest.mark.parametrize("pattern", ["srv\\' OR 1=1 -- ", "a\\", "\\'"])
def test_search_objects_pattern_rejects_backslash(fn: str, pattern: str) -> None:
    """`standard_conforming_strings=off` 서버에서 `\\'`로 리터럴을 탈출하지 못하게 거부한다."""
    with pytest.raises(ValueError):
        getattr(tl, fn)(pattern, "table")


@pytest.mark.parametrize("engine", ["postgresql", "db2"])
async def test_search_objects_tool_returns_error_on_backslash(
    engine: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, reached = _pool_with_fake_runner(monkeypatch, engine)
    capture = _CaptureMCP()
    tl.register_tools(capture)
    out = json.loads(
        await capture.tools["search_objects"](source="src", pattern="x\\'", ctx=_ctx(pm))
    )
    assert set(out) == {"error"}
    assert reached == []


@pytest.mark.parametrize("fn", ["_pg_search_objects_sql", "_db2_search_objects_sql"])
def test_search_objects_pattern_keeps_quote_doubling(fn: str) -> None:
    assert "LIKE 'o''x%'" in getattr(tl, fn)("o'x*", "table")


# =====================================================================
# 교정 L2 — _id_literal은 ASCII 십진수만 정수 리터럴로
# =====================================================================


@pytest.mark.parametrize("value", ["\u0661\u0662\u0663", "\u00b2", "\uff11\uff12"])
def test_id_literal_non_ascii_digits_are_quoted(value: str) -> None:
    assert pt._id_literal(value) == f"'{value}'"


def test_id_literal_ascii_digits_stay_integer() -> None:
    assert pt._id_literal(" 12345 ") == "12345"
