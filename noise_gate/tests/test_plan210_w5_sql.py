"""plans/210 W5 — 관측 DB 스캐너 고정 SELECT · 방언 · 식별자 검증 · ITAM 설정."""

from __future__ import annotations

from pathlib import Path

import pytest

from noise_gate.infrastructure.apm_host_map_sql_scanners import (
    DEFAULT_ITAM_CONFIG_PATH,
    ItamLookupConfig,
    ScanSqlError,
    build_itam_app_page_sql,
    build_itam_server_page_sql,
    build_sms_nic_sql,
    build_sms_server_page_sql,
    keyset_pages,
    load_itam_lookup,
    page_literal,
    quote_ident,
    registry_executor,
    scan_itam,
    scan_sms,
)
from noise_gate.tests.plan210_w5_fakes import FakeSqlExecutor, sms_server

ENGINES = {"zone_db2": "db2", "zone_pg": "postgresql"}.__getitem__


def _cfg(**over) -> ItamLookupConfig:
    base = dict(app_table="apps", app_code_column="code", app_name_column="name",
                server_table="ledger", server_hostname_column="host", server_ip_column="ip",
                server_clue_columns=("clue_a", "설명"), server_active_column="use_yn",
                server_active_values=(), generic_terms=(), max_servers=50)
    base.update(over)
    return ItamLookupConfig(**base)


# ── 방언 ──────────────────────────────────────────────────────────────
def test_db2_server_page_uses_fetch_first_and_upper_schema() -> None:
    sql = build_sms_server_page_sql("polestar_b0", "db2", after_id=10, page_rows=500)
    assert "FETCH FIRST 500 ROWS ONLY" in sql
    assert "LIMIT" not in sql
    assert "FROM POLESTAR.cmm_resource r" in sql
    assert "r.id > 10" in sql and "ORDER BY r.id" in sql
    assert sql.startswith("SELECT ")


def test_pg_server_page_uses_limit_and_schema() -> None:
    sql = build_sms_server_page_sql("polestar_cm_gp", "postgresql", after_id=0, page_rows=200)
    assert sql.rstrip().endswith("LIMIT 200")
    assert "FETCH FIRST" not in sql
    assert "FROM polestar.cmm_resource r" in sql


def test_nic_sql_in_list_of_validated_ids() -> None:
    sql = build_sms_nic_sql("polestar_b0", "db2", [3, 1, 3])
    assert "IN (1, 3)" in sql
    assert "POLESTAR.core_config_prop" in sql
    with pytest.raises(ScanSqlError):
        build_sms_nic_sql("polestar_b0", "db2", [])
    with pytest.raises(ScanSqlError):
        build_sms_nic_sql("polestar_b0", "db2", ["1 OR 1=1"])  # type: ignore[list-item]


def test_page_key_int_validation() -> None:
    with pytest.raises(ScanSqlError) as e:
        build_sms_server_page_sql("polestar_cm_gp", "postgresql", after_id="1;DROP",  # type: ignore[arg-type]
                                  page_rows=10)
    assert e.value.kind == "page_key_invalid"


def test_mariadb_itam_sql_backticks_and_limit() -> None:
    cfg = _cfg(server_active_values=("Y",))
    app = build_itam_app_page_sql(cfg, after=None, inclusive=False, page_rows=100)
    assert app.startswith("SELECT `code` AS app_code, `name` AS app_name")
    assert "FROM `apps`" in app and app.rstrip().endswith("LIMIT 100")
    server = build_itam_server_page_sql(cfg, after="web01", inclusive=True, page_rows=50)
    assert "`설명` AS clue_2" in server
    assert "`use_yn` IN ('Y')" in server
    assert "`host` >= 'web01'" in server
    assert "FETCH FIRST" not in server


def test_default_config_has_no_active_filter_and_no_pii_columns() -> None:
    cfg = load_itam_lookup()
    assert cfg.server_active_values == ()
    assert cfg.max_servers == 50
    sql = " ".join([
        build_itam_app_page_sql(cfg, after=None, inclusive=False, page_rows=10),
        build_itam_server_page_sql(cfg, after=None, inclusive=False, page_rows=10),
    ])
    for pii in ("담당", "직원번호", "성명"):
        assert pii not in sql
    text = Path(DEFAULT_ITAM_CONFIG_PATH).read_text(encoding="utf-8")
    assert "추정 — W0-4 확정 대기" in text.splitlines()[0]


# ── 식별자·리터럴 검증 ────────────────────────────────────────────────
@pytest.mark.parametrize("bad", ["a`b", "a b", "a;b", "t.c", "", "a'--", "x)"])
def test_quote_ident_rejects_bad_names(bad: str) -> None:
    with pytest.raises(ScanSqlError) as e:
        quote_ident(bad)
    assert e.value.kind == "config_invalid"


def test_quote_ident_accepts_korean_and_underscore() -> None:
    assert quote_ident("서버_호스트명1") == "`서버_호스트명1`"


@pytest.mark.parametrize("bad", ["it's", "a\\b", "a\nb", ""])
def test_page_literal_rejects(bad: str) -> None:
    with pytest.raises(ScanSqlError):
        page_literal(bad)


def test_load_rejects_bad_identifier(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(
        "app_table: {table: 'apps; DROP', code_column: c, name_column: n}\n"
        "server_table: {table: s, hostname_column: h, ip_column: i}\n", encoding="utf-8")
    with pytest.raises(ScanSqlError) as e:
        load_itam_lookup(path)
    assert e.value.kind == "config_invalid"


def test_load_rejects_bad_active_value(tmp_path: Path) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(
        "app_table: {table: a, code_column: c, name_column: n}\n"
        "server_table: {table: s, hostname_column: h, ip_column: i, active_column: u,"
        " active_values: [\"Y' OR '1\"]}\n", encoding="utf-8")
    with pytest.raises(ScanSqlError):
        load_itam_lookup(path)


# ── 실행기 가드 ───────────────────────────────────────────────────────
async def test_registry_executor_rejects_non_select(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Reg:
        def __init__(self, cfg) -> None:  # noqa: ANN001
            pass

        def is_registered(self, db_id: str) -> bool:
            raise AssertionError("가드보다 먼저 실행되면 안 된다")

    monkeypatch.setattr("src.routing.db_registry.DBRegistry", _Reg)
    execute = registry_executor(object())
    with pytest.raises(ScanSqlError) as e:
        await execute("polestar", "DELETE FROM t")
    assert e.value.kind == "sql_rejected"


# ── 스캐너 ────────────────────────────────────────────────────────────
async def test_scan_sms_pages_nic_and_zone_isolation() -> None:
    servers = [sms_server("zone_pg", i, f"srv{i:02d}", f"10.0.0.{i}", nic=[f"10.9.0.{i}, bad"])
               for i in range(1, 6)]
    execute = FakeSqlExecutor(servers=servers, fail_db=["zone_db2"])
    result = await scan_sms(execute, ["zone_db2", "zone_pg"], page_rows=2, engine_of=ENGINES)
    assert result.status == "partial"
    assert result.unchecked_zones == ["zone_db2"]
    rows = result.data["rows"]
    assert result.data["scanned"] == ["zone_pg"]
    assert [r["server_name"] for r in rows] == [f"srv{i:02d}" for i in range(1, 6)]
    assert rows[0]["nic_ips"] == ["10.9.0.1"]
    assert result.extra["zones"]["zone_db2"]["status"] == "failed"
    assert all(sql.startswith("SELECT ") for _db, sql in execute.sqls)


async def test_scan_sms_all_failed() -> None:
    result = await scan_sms(FakeSqlExecutor(fail_db=["zone_pg"]), ["zone_pg"], engine_of=ENGINES)
    assert result.status == "failed"
    assert result.error_kind == "all_zones_failed"


async def test_scan_itam_keyset_duplicate_keys_not_lost() -> None:
    ledger = [{"hostname": h, "ip_text": "10.3.0.1", "clue_1": "주문", "clue_2": ""}
              for h in ("a01", "a02", "a02", "a03", "a04")]
    apps = [{"app_code": f"C{i}", "app_name": f"업무{i}"} for i in range(3)]
    execute = FakeSqlExecutor(apps=apps, itam_servers=ledger)
    result = await scan_itam(execute, _cfg(), page_rows=2)
    assert result.status == "ok"
    assert len(result.data["apps"]) == 3
    assert [s["hostname"] for s in result.data["servers"]] == ["a01", "a02", "a02", "a03", "a04"]
    assert result.data["servers"][0]["active"] is None
    assert result.data["servers"][0]["clue_text"] == "주문"


async def test_keyset_all_same_key_page_moves_on() -> None:
    calls: list[tuple[str | None, bool]] = []

    async def fetch(after: str | None, inclusive: bool) -> list[dict]:
        calls.append((after, inclusive))
        if after is None:
            return [{"k": "x"}, {"k": "x"}]
        return [{"k": "y"}]

    rows = await keyset_pages(fetch, lambda r: r["k"], 2)
    assert [r["k"] for r in rows] == ["x", "x", "y"]
    assert calls == [(None, False), ("x", False)]


async def test_scan_itam_config_error_fails() -> None:
    result = await scan_itam(FakeSqlExecutor(), None, config_error="config_invalid")
    assert (result.status, result.error_kind) == ("failed", "config_invalid")


async def test_no_external_input_reaches_sql() -> None:
    """도메인명·설명(외부 입력)은 SQL 조립에 들어가지 않는다 — 스캐너 인자에 그 자리가 없다."""
    hostile = "x'; DROP TABLE t; --"
    servers = [sms_server("zone_pg", 1, hostile, "10.0.0.1")]
    ledger = [{"hostname": hostile, "ip_text": "", "clue_1": hostile, "clue_2": ""}]
    execute = FakeSqlExecutor(servers=servers, itam_servers=ledger,
                              apps=[{"app_code": hostile, "app_name": hostile}])
    await scan_sms(execute, ["zone_pg"], engine_of=ENGINES)
    await scan_itam(execute, _cfg(), page_rows=10)
    assert execute.sqls
    assert all(hostile not in sql and "DROP" not in sql for _db, sql in execute.sqls)
