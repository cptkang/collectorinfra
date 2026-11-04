"""사전 스캔 — 관측 DB 스캐너(SMS · ITAM) · 고정 SELECT 조립 · ITAM 설정 로더.

plans/210 §3.5 · D-362.

SQL은 전부 코드가 조립한 **고정 SELECT**다(LLM 0). 가변부는 둘뿐이다.
- 설정 식별자 — ITAM 표·칼럼(`config/knowledge/itam/service_host_lookup.yaml`)은
  `^[0-9A-Za-z_가-힣]+$` 검증 뒤 백틱(MariaDB). 폴스타 표는 이 모듈의 고정 이름 + 레지스트리 스키마.
- 페이지 키 — 폴스타 자원 id(정수 검증) · ITAM 키셋 문자열(따옴표·역슬래시·제어문자 거부 검증).
도메인명·설명 같은 외부 입력은 여기 들어오지 않는다(ITAM 사슬은 스냅샷 메모리 대조).

실행은 레지스트리 클라이언트(`DBRegistry.get_client(db_id).execute_sql` — readonly MCP)이고 실행 전
`SQLGuard.is_safe_select`로 한 번 더 막는다(D-003 3중 방어).

- SMS: 존(db_id)마다 서버 행(`dtime IS NULL`)을 자원 id 키셋 페이지로 읽고(DB2 `FETCH FIRST` ·
  `POLESTAR` / PG `LIMIT` · `polestar_hostname_resolver._table` 규약) 그 페이지 서버의
  NIC EAV `IP`를 묶어 읽는다. 존별 실패는 `unchecked_zones` — 그 존은 「사라짐」 판정에서 빠진다.
- ITAM: 업무 표 전체 · 서버 원장(활성 코드값이 설정돼 있으면 그 행만) 키셋 페이지 · 필요한 칼럼만
  (PII 칼럼 없음) · IP 다중값 분해·IPv4 검증.

계층: infrastructure.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from noise_gate.domain.apm_host_map_itam import parse_ipv4s
from noise_gate.domain.apm_host_map_scan import (
    SCANNER_FAILED,
    SCANNER_OK,
    SCANNER_PARTIAL,
    ScannerResult,
    failed_result,
)
from noise_gate.infrastructure.polestar_hostname_resolver import _row_value, _table

logger = logging.getLogger(__name__)

#: `(db_id, sql) → 행` — 실행기(실 배선은 `registry_executor`).
SqlExecutor = Callable[[str, str], Awaitable[list[dict[str, Any]]]]

ITAM_DB_ID = "itam"
DEFAULT_ITAM_CONFIG_PATH = (
    Path(__file__).resolve().parents[2]
    / "config" / "knowledge" / "itam" / "service_host_lookup.yaml"
)
#: ITAM 식별자 — 영숫자·밑줄·한글만(백틱 안에서도 이 밖의 문자는 받지 않는다).
_IDENT = re.compile(r"^[0-9A-Za-z_가-힣]+$")
#: 키셋 문자열 페이지 키 — 따옴표·역슬래시·제어문자 없음 · 길이 1~255.
_PAGE_KEY = re.compile(r"^[^'\\\x00-\x1f\x7f]{1,255}$")
#: 자원 id 페이지 키.
_INT_KEY = re.compile(r"^\d{1,19}$")
#: NIC 조회 한 번에 묶는 서버 수(IN 목록 길이 · 결과 행 상한 여유).
NIC_IN_CHUNK = 200
#: 한 표를 읽는 최대 페이지 수(키가 나아가지 않는 결함의 무한 루프 방어).
MAX_PAGES = 10000

ERROR_CONFIG_INVALID = "config_invalid"
ERROR_PAGE_KEY_INVALID = "page_key_invalid"
ERROR_SQL_REJECTED = "sql_rejected"


class ScanSqlError(Exception):
    """스캔 SQL 조립·실행 거부 — `kind`는 실행 기록 `error_kind`."""

    def __init__(self, kind: str, detail: str = "") -> None:
        """종류와 상세를 싣는다."""
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def _error_kind(exc: BaseException) -> str:
    return exc.kind if isinstance(exc, ScanSqlError) else type(exc).__name__


# ── 실행기 ──────────────────────────────────────────────────────────────
def registry_executor(app_config: Any) -> SqlExecutor:
    """레지스트리 클라이언트 실행기 — 실행 전 `SQLGuard`로 SELECT 단일문만 통과시킨다."""
    from src.routing.db_registry import DBRegistry
    from src.security.sql_guard import SQLGuard

    registry = DBRegistry(app_config)
    guard = SQLGuard()

    async def _execute(db_id: str, sql: str) -> list[dict[str, Any]]:
        ok, reason = guard.is_safe_select(sql)
        if not ok or not sql.lstrip().upper().startswith("SELECT"):
            raise ScanSqlError(ERROR_SQL_REJECTED, reason)
        if not registry.is_registered(db_id):
            raise LookupError(f"활성 DB가 아닙니다: {db_id}")
        async with registry.get_client(db_id) as client:
            result = await client.execute_sql(sql)
        return [r for r in result.rows if isinstance(r, dict)]

    return _execute


def db_engine_of(db_id: str) -> str:
    """레지스트리 엔진(미등록 = postgresql)."""
    from src.routing.domain_config import get_domain_by_id

    domain = get_domain_by_id(db_id)
    return domain.db_engine if domain else "postgresql"


# ── SMS (폴스타) ───────────────────────────────────────────────────────
def _int_key(value: Any) -> int:
    text = _text(value)
    if not _INT_KEY.match(text):
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "자원 id가 정수가 아님")
    return int(text)


def _row_limit(engine: str, rows: int) -> str:
    n = max(1, int(rows))
    return f"FETCH FIRST {n} ROWS ONLY" if engine == "db2" else f"LIMIT {n}"


def build_sms_server_page_sql(db_id: str, engine: str, *, after_id: int, page_rows: int) -> str:
    """서버 행 키셋 페이지(자원 id 오름차순 · 삭제 자원 제외)."""
    after = _int_key(after_id)
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT r.id AS resource_id, r.name AS server_name, r.hostname AS hostname,\n"
        "       r.ipaddress AS ip\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND r.id > {after}\n"
        "ORDER BY r.id\n"
        f"{_row_limit(engine, page_rows)}"
    )


def build_sms_nic_sql(db_id: str, engine: str, server_ids: Sequence[int]) -> str:
    """서버들의 NIC EAV `IP`(같은 서버 소속 = `platform_resource_id`)."""
    ids = sorted({_int_key(i) for i in server_ids})
    if not ids:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "NIC 조회 서버 id가 비었다")
    t_resource = _table(db_id, "cmm_resource", engine)
    t_prop = _table(db_id, "core_config_prop", engine)
    return (
        "SELECT n.platform_resource_id AS resource_id, cc.stringvalue_short AS ip\n"
        f"FROM {t_resource} n\n"
        f"JOIN {t_prop} cc\n"
        "  ON cc.configuration_id = n.resource_conf_id AND cc.name = 'IP'\n"
        "WHERE n.resource_type = 'server.NetworkInterface'\n"
        "  AND n.dtime IS NULL\n"
        f"  AND n.platform_resource_id IN ({', '.join(str(i) for i in ids)})"
    )


async def _scan_zone(
    execute: SqlExecutor, db_id: str, engine: str, page_rows: int
) -> tuple[list[dict[str, Any]], int]:
    """존 하나 → (SMS 스냅샷 행, NIC IP 행 수)."""
    out: list[dict[str, Any]] = []
    nic_rows = 0
    after = 0
    for _page in range(MAX_PAGES):
        rows = await execute(db_id, build_sms_server_page_sql(
            db_id, engine, after_id=after, page_rows=page_rows))
        servers: dict[int, dict[str, Any]] = {}
        for row in rows:
            rid = _int_key(_row_value(row, "resource_id"))
            name = _text(_row_value(row, "server_name"))
            host = _text(_row_value(row, "hostname"))
            servers[rid] = {
                "db_id": db_id, "resource_id": str(rid), "server_name": name or host,
                "hostname": host, "ip": _text(_row_value(row, "ip")), "nic_ips": [],
            }
        ids = list(servers)
        for start in range(0, len(ids), NIC_IN_CHUNK):
            nics = await execute(db_id, build_sms_nic_sql(db_id, engine,
                                                          ids[start:start + NIC_IN_CHUNK]))
            nic_rows += len(nics)
            for nic in nics:
                server = servers.get(_int_key(_row_value(nic, "resource_id")))
                if server is None:
                    continue
                for ip in parse_ipv4s(_row_value(nic, "ip")):
                    if ip not in server["nic_ips"]:
                        server["nic_ips"].append(ip)
        out.extend(s for s in servers.values() if s["server_name"])
        if len(rows) < page_rows or not ids:
            return out, nic_rows
        last = max(ids)
        if last <= after:
            raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "자원 id 키가 나아가지 않음")
        after = last
    raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "페이지 상한 초과")


async def scan_sms(
    execute: SqlExecutor,
    db_ids: Sequence[str],
    *,
    page_rows: int = 1000,
    engine_of: Callable[[str], str] = db_engine_of,
) -> ScannerResult:
    """SMS 스캐너 — `data` = `{"rows": sms_inventory 행, "scanned": 끝까지 읽은 존}`.

    존별로 격리한다 — 실패 존은 `unchecked_zones`(상태 partial · 전 존 실패면 failed).
    """
    started = time.monotonic()
    if not db_ids:
        return failed_result("no_zones")
    rows: list[dict[str, Any]] = []
    scanned: list[str] = []
    unchecked: list[str] = []
    zones: dict[str, dict[str, Any]] = {}
    for db_id in db_ids:
        engine = engine_of(db_id)
        zone_started = time.monotonic()
        try:
            zone_rows, nic_rows = await _scan_zone(execute, db_id, engine, page_rows)
        except Exception as exc:  # noqa: BLE001 — 존 하나 실패가 나머지 존을 막지 않는다
            logger.warning("APM·SMS 연계 스캔 — SMS 존 실패: db_id=%s engine=%s err=%s",
                           db_id, engine, _error_kind(exc))
            unchecked.append(db_id)
            zones[db_id] = {"status": SCANNER_FAILED, "error_kind": _error_kind(exc)}
            continue
        rows.extend(zone_rows)
        scanned.append(db_id)
        zones[db_id] = {"status": SCANNER_OK, "servers": len(zone_rows), "nic_rows": nic_rows,
                        "seconds": round(time.monotonic() - zone_started, 3)}
    status = SCANNER_FAILED if not scanned else (SCANNER_PARTIAL if unchecked else SCANNER_OK)
    return ScannerResult(
        status, rows=len(rows), seconds=time.monotonic() - started,
        error_kind=None if scanned else "all_zones_failed", unchecked_zones=unchecked,
        extra={"zones": zones}, data={"rows": rows, "scanned": scanned},
    )


# ── ITAM ───────────────────────────────────────────────────────────────
def quote_ident(name: Any) -> str:
    """ITAM 식별자 검증 + 백틱.

    Raises:
        ScanSqlError: `^[0-9A-Za-z_가-힣]+$` 밖(`config_invalid`)
    """
    text = _text(name)
    if not _IDENT.match(text):
        raise ScanSqlError(ERROR_CONFIG_INVALID, f"ITAM 식별자 규칙 밖: {text[:40]!r}")
    return f"`{text}`"


def page_literal(value: Any) -> str:
    """키셋 문자열 페이지 키 → 따옴표 리터럴(형식 검증 — 따옴표·역슬래시·제어문자 거부)."""
    text = str(value if value is not None else "")
    if not _PAGE_KEY.match(text):
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "키셋 키 형식 밖")
    return f"'{text}'"


@dataclass(frozen=True)
class ItamLookupConfig:
    """ITAM 사슬 설정(`service_host_lookup.yaml`) — 식별자는 로드 시 검증을 끝낸 값."""

    app_table: str
    app_code_column: str
    app_name_column: str
    server_table: str
    server_hostname_column: str
    server_ip_column: str
    server_clue_columns: tuple[str, ...]
    server_active_column: str | None
    server_active_values: tuple[str, ...]
    generic_terms: tuple[str, ...]
    max_servers: int


def load_itam_lookup(path: Path | str = DEFAULT_ITAM_CONFIG_PATH) -> ItamLookupConfig:
    """설정 파일 → `ItamLookupConfig`.

    Raises:
        ScanSqlError: 파일 없음·형식 오류·식별자·활성 코드값 규칙 밖(`config_invalid`)
    """
    import yaml  # type: ignore[import-untyped]

    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ScanSqlError(ERROR_CONFIG_INVALID, type(e).__name__) from e
    if not isinstance(raw, dict):
        raise ScanSqlError(ERROR_CONFIG_INVALID, "최상위가 매핑이 아님")
    app = raw.get("app_table") or {}
    server = raw.get("server_table") or {}
    if not isinstance(app, dict) or not isinstance(server, dict):
        raise ScanSqlError(ERROR_CONFIG_INVALID, "app_table·server_table이 매핑이 아님")

    def ident(value: Any) -> str:
        quote_ident(value)
        return _text(value)

    clues = server.get("clue_columns") or []
    active_column = server.get("active_column")
    active_values = server.get("active_values") or []
    if not isinstance(clues, list) or not isinstance(active_values, list):
        raise ScanSqlError(ERROR_CONFIG_INVALID, "clue_columns·active_values는 목록")
    for value in active_values:
        if not _IDENT.match(_text(value)):
            raise ScanSqlError(ERROR_CONFIG_INVALID, "활성 코드값 규칙 밖")
    terms = raw.get("generic_terms") or []
    try:
        max_servers = int(raw.get("max_servers") or 50)
    except (TypeError, ValueError) as e:
        raise ScanSqlError(ERROR_CONFIG_INVALID, "max_servers") from e
    return ItamLookupConfig(
        app_table=ident(app.get("table")),
        app_code_column=ident(app.get("code_column")),
        app_name_column=ident(app.get("name_column")),
        server_table=ident(server.get("table")),
        server_hostname_column=ident(server.get("hostname_column")),
        server_ip_column=ident(server.get("ip_column")),
        server_clue_columns=tuple(ident(c) for c in clues),
        server_active_column=ident(active_column) if active_column else None,
        server_active_values=tuple(_text(v) for v in active_values),
        generic_terms=tuple(_text(t) for t in terms if _text(t)),
        max_servers=max(1, max_servers),
    )


def _key_condition(column: str, after: str | None, inclusive: bool) -> str:
    if after is None:
        return ""
    return f"  AND {column} {'>=' if inclusive else '>'} {page_literal(after)}\n"


def build_itam_app_page_sql(
    cfg: ItamLookupConfig, *, after: str | None, inclusive: bool, page_rows: int
) -> str:
    """업무 표 키셋 페이지(코드 오름차순)."""
    code, name = quote_ident(cfg.app_code_column), quote_ident(cfg.app_name_column)
    return (
        f"SELECT {code} AS app_code, {name} AS app_name\n"
        f"FROM {quote_ident(cfg.app_table)}\n"
        f"WHERE {code} IS NOT NULL\n"
        f"{_key_condition(code, after, inclusive)}"
        f"ORDER BY {code}\n"
        f"LIMIT {max(1, int(page_rows))}"
    )


def build_itam_server_page_sql(
    cfg: ItamLookupConfig, *, after: str | None, inclusive: bool, page_rows: int
) -> str:
    """서버 원장 키셋 페이지(호스트명 오름차순 · 활성 코드값이 설정돼 있으면 그 행만)."""
    host = quote_ident(cfg.server_hostname_column)
    columns = [f"{host} AS hostname", f"{quote_ident(cfg.server_ip_column)} AS ip_text"]
    columns += [
        f"{quote_ident(c)} AS clue_{i}" for i, c in enumerate(cfg.server_clue_columns, start=1)
    ]
    active = ""
    if cfg.server_active_column and cfg.server_active_values:
        values = ", ".join(page_literal(v) for v in cfg.server_active_values)
        active = f"  AND {quote_ident(cfg.server_active_column)} IN ({values})\n"
    return (
        f"SELECT {', '.join(columns)}\n"
        f"FROM {quote_ident(cfg.server_table)}\n"
        f"WHERE {host} IS NOT NULL\n"
        f"{active}"
        f"{_key_condition(host, after, inclusive)}"
        f"ORDER BY {host}\n"
        f"LIMIT {max(1, int(page_rows))}"
    )


async def keyset_pages(
    fetch: Callable[[str | None, bool], Awaitable[list[dict[str, Any]]]],
    key_of: Callable[[Mapping[str, Any]], str],
    page_rows: int,
) -> list[dict[str, Any]]:
    """문자열 키셋 페이지를 끝까지 읽는다 — 키가 중복인 표도 경계에서 행을 잃지 않는다.

    꽉 찬 페이지의 마지막 키(대소문자 무시) 행들은 버리고 다음 페이지를 `>= 그 키`로 다시 읽는다.
    한 페이지가 전부 같은 키면 그대로 받고 `> 그 키`로 넘어간다(그 키의 나머지 행은 잃는다 — 로그).
    """
    out: list[dict[str, Any]] = []
    after: str | None = None
    inclusive = False
    for _page in range(MAX_PAGES):
        rows = await fetch(after, inclusive)
        if len(rows) < page_rows:
            out.extend(rows)
            return out
        last = key_of(rows[-1])
        keep = [r for r in rows if key_of(r).lower() != last.lower()]
        if keep:
            out.extend(keep)
            if after is not None and inclusive and last.lower() == after.lower():
                raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "키셋 키가 나아가지 않음")
            after, inclusive = last, True
        else:
            logger.warning("ITAM 키셋 — 한 키의 행이 페이지(%d)를 넘어 나머지를 건너뜀", page_rows)
            out.extend(rows)
            after, inclusive = last, False
    raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "페이지 상한 초과")


def _clue(row: Mapping[str, Any], count: int) -> str:
    parts = [_text(_row_value(dict(row), f"clue_{i}")) for i in range(1, count + 1)]
    return " | ".join(p for p in parts if p)


async def scan_itam(
    execute: SqlExecutor,
    cfg: ItamLookupConfig | None,
    *,
    page_rows: int = 1000,
    config_error: str | None = None,
) -> ScannerResult:
    """ITAM 스캐너 — `data` = `{"apps": itam_snapshot_app 행, "servers": itam_snapshot_server 행}`.

    서버 행: `{app_code: "", app_name: "", hostname, ips, active, clue_text}` — 원장에
    업무 코드 키가 없어 `app_code`·`app_name`은 비우고 업무 단서는 `clue_text`(자유 기재
    칸을 이은 값)에 싣는다.
    `active`는 활성 코드값으로 거른 행이면 True, 코드값 미설정이면 None(미상).
    """
    started = time.monotonic()
    if cfg is None:
        return failed_result(config_error or ERROR_CONFIG_INVALID)

    async def fetch_apps(after: str | None, inclusive: bool) -> list[dict[str, Any]]:
        return await execute(ITAM_DB_ID, build_itam_app_page_sql(
            cfg, after=after, inclusive=inclusive, page_rows=page_rows))

    async def fetch_servers(after: str | None, inclusive: bool) -> list[dict[str, Any]]:
        return await execute(ITAM_DB_ID, build_itam_server_page_sql(
            cfg, after=after, inclusive=inclusive, page_rows=page_rows))

    try:
        app_rows = await keyset_pages(
            fetch_apps, lambda r: _text(_row_value(dict(r), "app_code")), page_rows)
        server_rows = await keyset_pages(
            fetch_servers, lambda r: _text(_row_value(dict(r), "hostname")), page_rows)
    except Exception as exc:  # noqa: BLE001 — 실패는 기록으로(이전 스냅샷 유지)
        logger.warning("APM·SMS 연계 스캔 — ITAM 실패: %s", _error_kind(exc))
        return failed_result(_error_kind(exc), seconds=time.monotonic() - started)
    seen: set[tuple[str, str]] = set()
    apps: list[dict[str, Any]] = []
    for row in app_rows:
        code = _text(_row_value(dict(row), "app_code"))
        name = _text(_row_value(dict(row), "app_name"))
        if (code or name) and (code, name) not in seen:
            seen.add((code, name))
            apps.append({"app_code": code, "app_name": name})
    active = True if cfg.server_active_column and cfg.server_active_values else None
    servers = [
        {
            "app_code": "", "app_name": "",
            "hostname": _text(_row_value(dict(row), "hostname")),
            "ips": list(parse_ipv4s(_row_value(dict(row), "ip_text"))),
            "active": active,
            "clue_text": _clue(row, len(cfg.server_clue_columns)),
        }
        for row in server_rows if _text(_row_value(dict(row), "hostname"))
    ]
    return ScannerResult(
        SCANNER_OK, rows=len(apps) + len(servers), seconds=time.monotonic() - started,
        extra={"apps": len(apps), "servers": len(servers),
               "active_filter": active is True},
        data={"apps": apps, "servers": servers},
    )


def itam_sql_preview(cfg: ItamLookupConfig, page_rows: int) -> list[str]:
    """첫 페이지 SQL(점검 스크립트 `--dry-run`)."""
    return [
        build_itam_app_page_sql(cfg, after=None, inclusive=False, page_rows=page_rows),
        build_itam_server_page_sql(cfg, after=None, inclusive=False, page_rows=page_rows),
    ]


def sms_sql_preview(
    db_ids: Iterable[str], page_rows: int, engine_of: Callable[[str], str] = db_engine_of
) -> list[tuple[str, str]]:
    """존별 첫 페이지·NIC SQL(점검 스크립트 `--dry-run` · NIC는 자리 id 0)."""
    out: list[tuple[str, str]] = []
    for db_id in db_ids:
        engine = engine_of(db_id)
        out.append((db_id, build_sms_server_page_sql(db_id, engine, after_id=0,
                                                     page_rows=page_rows)))
        out.append((db_id, build_sms_nic_sql(db_id, engine, [0])))
    return out


__all__ = [
    "DEFAULT_ITAM_CONFIG_PATH",
    "ERROR_CONFIG_INVALID",
    "ERROR_PAGE_KEY_INVALID",
    "ERROR_SQL_REJECTED",
    "ITAM_DB_ID",
    "NIC_IN_CHUNK",
    "ItamLookupConfig",
    "ScanSqlError",
    "SqlExecutor",
    "build_itam_app_page_sql",
    "build_itam_server_page_sql",
    "build_sms_nic_sql",
    "build_sms_server_page_sql",
    "db_engine_of",
    "itam_sql_preview",
    "keyset_pages",
    "load_itam_lookup",
    "page_literal",
    "quote_ident",
    "registry_executor",
    "scan_itam",
    "scan_sms",
    "sms_sql_preview",
]
