"""업무 서비스 보강 읽기 — 고정 SELECT 빌더·읽기 루프 (plans/215 §3.2.4 · §9 · D-368).

계산 서비스(`noise_gate/application/biz_service.py`)가 미리보기 때만 존(db_id)별로 부른다.
210 스캐너와 스냅샷은 바꾸지 않고, 215이 소유하는 고정 SELECT를 따로 둔다
(Q29 · 210 `apm_host_map_sql_scanners` 선례 — `_table` 스키마 한정 ·
DB2 `FETCH FIRST`/PG `LIMIT` · 자원 id 키셋 페이지).

- ① 서버 행 키셋 페이지(≤ `ATTR_PAGE_ROWS` · 210 서버 페이지와 같은 조건 — 서버 자원 · 삭제 제외):
  자원 id · 트리 조상 경로 · 등록 시각 · 가용성 의존 2칼럼.
- ② 트리 폴더: ①의 조상 id 집합을 IN 묶음(≤ `TREE_IN_CHUNK`)으로 나눠 id · 이름 · 자원 유형.
- ③ (S10 on일 때만) ITAM 서버 통합 현황 — 호스트명 · IP · 구매 계약번호·이름 · 유지보수 계약번호 ·
  담당 부점명. **담당자 성명·사번 칸은 SELECT에 넣지 않는다**(PII). 표·칼럼 물리 이름은
  `testdata/itam/schema.yaml` 판독본이다(실 DB 대조 미실시 — plans/95 G-4 · yaml 기본 off · W0-22).

가변부는 정수 id(정규식 검증)와 ITAM 키셋 문자열(210 `page_literal` — 따옴표·역슬래시·제어문자
거부)뿐이다. 실행은 210과 같은 레지스트리 실행기(`registry_executor` — 실행 전 `SQLGuard` ·
readonly MCP)를 주입받는다(D-003). 호출부가 한 번에 하나씩 await한다(동시 1).

④ (W7 · M3) SMS 최신 정각 통계 — 존 하나의 감시 서버 묶음(≤ `SMS_IN_CHUNK`)마다 최신 `stat_date`
확인 1건 → 직전과 다를 때만 지표 행 1건(서버 · 지표 원천 쌍 · 묶음 최대값). 지표 원천(자원 유형 ·
지표 정의 이름)은 `config/knowledge/biz_service.yaml` `sms_metric_source`(W0-8)에서 오고, 문자 규칙
(`valid_sms_source_value`) 밖이면 조립하지 않는다. 조회 동시 1은 `sms_query_semaphore()`(루프마다
1개 — W8c 지표 보기와 공유)로 지킨다.

⑤ (W8c · 지표 보기) SMS 이력 — 존 하나의 조회 서버(≤ `HISTORY_MAX_SERVERS` = 지표 보기 구성원
상한)마다 고정 SELECT 1건(서버 · 지표 원천 쌍 · 통계 키마다 묶음 최대값 · 0~1000). 24시간 이하
창은 시간 통계(키 `YYYYMMDDHH` · ≤24점), 7일 창은 일 통계(키 `YYYYMMDD` · ≤7점)를 읽는다 —
시간 통계 7일은 20대 기준 10,080행이라 MCP `max_rows`(10,000)를 넘는다. 결과 행 상한 =
점 수 × 서버 수 × 지표 수(24시간 1,440 · 7일 420)이고 통계 키 범위에서 점 수를 계산해 그보다
긴 범위는 조립하지 않는다(행이 잘려 서버가 빠지지 않게).

계층: infrastructure. W7(M3 SMS 지표)·W8c가 이 모듈에 빌더를 더한다.
"""

from __future__ import annotations

import asyncio
import re
import weakref
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noise_gate.infrastructure.apm_host_map_sql_scanners import (
    ERROR_PAGE_KEY_INVALID,
    ITAM_DB_ID,
    MAX_PAGES,
    ScanSqlError,
    SqlExecutor,
    keyset_pages,
    page_literal,
    quote_ident,
)
from noise_gate.infrastructure.polestar_hostname_resolver import _row_value, _table

#: ① 서버 행 한 페이지 상한.
ATTR_PAGE_ROWS = 1000
#: ② 트리 폴더 IN 묶음 상한.
TREE_IN_CHUNK = 500
#: 자원 id(정수) 키.
_INT_KEY = re.compile(r"^\d{1,19}$")

#: ③ ITAM 서버 통합 현황 표·칼럼(판독본 물리 이름 — 개인 칸 없음).
ITAM_CONTRACT_TABLE = "TCDMSIF80"
ITAM_CONTRACT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("sevrHostName", "hostname"),
    ("iPCtnt", "ip_text"),
    ("byCtrcNo", "contract_no"),
    ("byCtrcName", "contract_name"),
    ("sevrManmenCtrcNo", "maint_contract_no"),
    ("rspblBrnName", "owner_branch"),
)
#: SELECT에 넣지 않는 ITAM 개인 칸(담당자 성명·사번 — 테스트가 문자열로 단언한다).
ITAM_PII_COLUMNS = ("rspblPsnEmnm", "rspblPsnEmpid")


def _text(value: Any) -> str:
    return str(value if value is not None else "").strip()


def int_key(value: Any) -> int:
    """자원 id → 정수(규칙 밖이면 `ScanSqlError(page_key_invalid)`)."""
    text = _text(value)
    if not _INT_KEY.match(text):
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "자원 id가 정수가 아님")
    return int(text)


def _opt_int(value: Any) -> int | None:
    text = _text(value)
    return int(text) if _INT_KEY.match(text) else None


def _row_limit(engine: str, rows: int) -> str:
    n = max(1, int(rows))
    return f"FETCH FIRST {n} ROWS ONLY" if engine == "db2" else f"LIMIT {n}"


# ── ① 서버 행 ──────────────────────────────────────────────────────────
def build_attr_server_page_sql(
    db_id: str, engine: str, *, after_id: int, page_rows: int = ATTR_PAGE_ROWS
) -> str:
    """서버 행 키셋 페이지(자원 id 오름차순 · 삭제 자원 제외) — 조상 경로·등록 시각·가용성 의존."""
    after = int_key(after_id)
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT r.id AS resource_id, r.id_ancestry AS id_ancestry, r.ctime AS ctime,\n"
        "       r.avail_depend_resource_id AS depend_1,\n"
        "       r.avail_depend_resource_id_2 AS depend_2\n"
        f"FROM {t_resource} r\n"
        "WHERE r.resource_type = 'server.Server'\n"
        "  AND r.dtime IS NULL\n"
        f"  AND r.id > {after}\n"
        "ORDER BY r.id\n"
        f"{_row_limit(engine, min(int(page_rows), ATTR_PAGE_ROWS))}"
    )


@dataclass(frozen=True)
class ServerAttrRow:
    """① 서버 1행 — `ancestors`는 루트·자기 자신을 뺀 조상 id(루트 쪽 → 부모 순)."""

    resource_id: str
    ancestors: tuple[str, ...] = ()
    ctime_ms: int | None = None
    depends: tuple[str, ...] = ()


def parse_ancestry(raw: Any, self_id: str) -> tuple[str, ...]:
    """조상 경로 `1>10812>…>{자기}>` → 루트·자기 자신을 뺀 정수 id(정수가 아닌 조각은 버린다)."""
    ids = [t for t in (p.strip() for p in _text(raw).split(">")) if _INT_KEY.match(t)]
    if ids and ids[-1] == self_id:
        ids = ids[:-1]
    return tuple(ids[1:])


def _server_attr_row(row: dict[str, Any]) -> ServerAttrRow:
    rid = str(int_key(_row_value(row, "resource_id")))
    ctime = _opt_int(_row_value(row, "ctime"))
    deps: list[str] = []
    for col in ("depend_1", "depend_2"):
        dep = _opt_int(_row_value(row, col))
        if dep and str(dep) != rid and str(dep) not in deps:
            deps.append(str(dep))
    return ServerAttrRow(rid, parse_ancestry(_row_value(row, "id_ancestry"), rid),
                         ctime if ctime and ctime > 0 else None, tuple(deps))


async def read_server_attrs(
    execute: SqlExecutor, db_id: str, engine: str, *, page_rows: int = ATTR_PAGE_ROWS
) -> dict[str, ServerAttrRow]:
    """① 존 하나의 서버 행 전부 → 자원 id → 행(키셋 페이지 · 키가 나아가지 않으면 실패)."""
    page = max(1, min(int(page_rows), ATTR_PAGE_ROWS))
    out: dict[str, ServerAttrRow] = {}
    after = 0
    for _page in range(MAX_PAGES):
        rows = await execute(db_id, build_attr_server_page_sql(
            db_id, engine, after_id=after, page_rows=page))
        parsed = [_server_attr_row(r) for r in rows]
        for item in parsed:
            out[item.resource_id] = item
        if len(rows) < page or not parsed:
            return out
        last = max(int(p.resource_id) for p in parsed)
        if last <= after:
            raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "자원 id 키가 나아가지 않음")
        after = last
    raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "페이지 상한 초과")


# ── ② 트리 폴더 ─────────────────────────────────────────────────────────
def build_tree_folder_sql(db_id: str, engine: str, folder_ids: Collection[Any]) -> str:
    """조상 id 묶음(≤ `TREE_IN_CHUNK`) → 폴더 id·이름·자원 유형."""
    ids = sorted({int_key(i) for i in folder_ids})
    if not ids:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "폴더 id가 비었다")
    if len(ids) > TREE_IN_CHUNK:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"폴더 id 묶음은 {TREE_IN_CHUNK}개까지")
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT f.id AS folder_id, f.name AS folder_name, f.resource_type AS resource_type\n"
        f"FROM {t_resource} f\n"
        f"WHERE f.id IN ({', '.join(str(i) for i in ids)})"
    )


@dataclass(frozen=True)
class FolderRow:
    """② 폴더 1개."""

    folder_id: str
    name: str
    resource_type: str = ""


async def read_tree_folders(
    execute: SqlExecutor, db_id: str, engine: str, folder_ids: Collection[str]
) -> dict[str, FolderRow]:
    """② 조상 id 집합 → 폴더 id → 행(IN 묶음마다 SELECT 1회)."""
    ids = sorted({int_key(i) for i in folder_ids})
    out: dict[str, FolderRow] = {}
    for start in range(0, len(ids), TREE_IN_CHUNK):
        rows = await execute(db_id, build_tree_folder_sql(
            db_id, engine, ids[start:start + TREE_IN_CHUNK]))
        for row in rows:
            fid = _opt_int(_row_value(row, "folder_id"))
            if fid is None:
                continue
            out[str(fid)] = FolderRow(str(fid), _text(_row_value(row, "folder_name")),
                                      _text(_row_value(row, "resource_type")))
    return out


# ── ③ ITAM 계약 ─────────────────────────────────────────────────────────
def build_itam_contract_page_sql(
    *, after: str | None, inclusive: bool, page_rows: int = ATTR_PAGE_ROWS
) -> str:
    """ITAM 서버 통합 현황 키셋 페이지(호스트명 오름차순 · MariaDB 백틱 · 개인 칸 없음)."""
    host = quote_ident(ITAM_CONTRACT_COLUMNS[0][0])
    columns = ", ".join(f"{quote_ident(c)} AS {alias}" for c, alias in ITAM_CONTRACT_COLUMNS)
    key = ""
    if after is not None:
        key = f"  AND {host} {'>=' if inclusive else '>'} {page_literal(after)}\n"
    return (
        f"SELECT {columns}\n"
        f"FROM {quote_ident(ITAM_CONTRACT_TABLE)}\n"
        f"WHERE {host} IS NOT NULL\n"
        f"{key}"
        f"ORDER BY {host}\n"
        f"LIMIT {max(1, min(int(page_rows), ATTR_PAGE_ROWS))}"
    )


@dataclass(frozen=True)
class ItamContractRow:
    """③ ITAM 1행(계약 칸 · 담당 부점명)."""

    hostname: str
    ip_text: str = ""
    contract_no: str = ""
    contract_name: str = ""
    maint_contract_no: str = ""
    owner_branch: str = ""


async def read_itam_contracts(
    execute: SqlExecutor, *, page_rows: int = ATTR_PAGE_ROWS
) -> list[ItamContractRow]:
    """③ ITAM 계약 칸 전부(210 `keyset_pages` — 키 중복 경계에서도 행을 잃지 않는다)."""
    page = max(1, min(int(page_rows), ATTR_PAGE_ROWS))

    async def fetch(after: str | None, inclusive: bool) -> list[dict[str, Any]]:
        return await execute(ITAM_DB_ID, build_itam_contract_page_sql(
            after=after, inclusive=inclusive, page_rows=page))

    rows = await keyset_pages(fetch, lambda r: _text(_row_value(dict(r), "hostname")), page)
    out: list[ItamContractRow] = []
    for raw in rows:
        row = dict(raw)
        host = _text(_row_value(row, "hostname"))
        if host:
            out.append(ItamContractRow(
                host, *(_text(_row_value(row, alias)) for _, alias in ITAM_CONTRACT_COLUMNS[1:])))
    return out


# ── ④ M3 SMS 최신 정각 통계 ────────────────────────────────────────────
#: 감시 서버 IN 묶음 상한(존마다 이 크기로 나눠 묶음마다 확인 1건 · 지표 행 1건).
SMS_IN_CHUNK = 500
#: SMS 조회 동시 상한(M3 수집기 · W8c 지표 보기 공용).
SMS_QUERY_CONCURRENCY = 1
#: 정각 통계 키 `YYYYMMDDHH`.
_STAT_HOUR = re.compile(r"^\d{10}$")
#: 지표 원천 값(자원 유형 · 지표 정의 이름) 문자 규칙 — 영문으로 시작 · 영숫자·밑줄·점 · 64자 이하.
_SOURCE_VALUE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")

#: 지표(cpu·memory·filesystem) → (자원 유형, 지표 정의 이름).
SmsMetricSources = Mapping[str, tuple[str, str]]

_SMS_SEMAPHORES: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def sms_query_semaphore() -> asyncio.Semaphore:
    """SMS 조회 공용 제한기(동시 `SMS_QUERY_CONCURRENCY`) — 실행 중 이벤트 루프마다 1개."""
    loop = asyncio.get_running_loop()
    sem = _SMS_SEMAPHORES.get(loop)
    if sem is None:
        sem = _SMS_SEMAPHORES[loop] = asyncio.Semaphore(SMS_QUERY_CONCURRENCY)
    return sem


def valid_sms_source_value(value: Any) -> bool:
    """지표 원천 값이 SQL 문자열 리터럴로 넣을 수 있는 문자 규칙 안인가."""
    return isinstance(value, str) and bool(_SOURCE_VALUE.match(value))


def stat_hour(value: Any) -> str:
    """정각 통계 키(`YYYYMMDDHH` 10자리 숫자) — 규칙 밖이면 `ScanSqlError(page_key_invalid)`."""
    text = _text(value)
    if not _STAT_HOUR.match(text):
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "정각 통계 키가 YYYYMMDDHH가 아님")
    return text


def _sms_ids(server_ids: Collection[Any]) -> list[int]:
    ids = sorted({int_key(i) for i in server_ids})
    if not ids:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "감시 서버 id가 비었다")
    if len(ids) > SMS_IN_CHUNK:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"감시 서버 id 묶음은 {SMS_IN_CHUNK}개까지")
    return ids


def _source_pairs(sources: SmsMetricSources) -> str:
    pairs: list[str] = []
    for metric in sorted(sources):
        rtype, definition = sources[metric]
        if not (valid_sms_source_value(rtype) and valid_sms_source_value(definition)):
            raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"지표 원천 값이 문자 규칙 밖: {metric}")
        pairs.append(f"(r.resource_type = '{rtype}' AND s.definition_name = '{definition}')")
    if not pairs:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "지표 원천이 비었다")
    return " OR ".join(pairs)


def build_sms_latest_stat_sql(
    db_id: str, engine: str, server_ids: Collection[Any], sources: SmsMetricSources,
    *, since_hour: str,
) -> str:
    """감시 서버 묶음의 최신 정각 통계 키(`since_hour` 이후 · 지표 원천 쌍만) — 1행."""
    ids = _sms_ids(server_ids)
    since = stat_hour(since_hour)
    t_stat = _table(db_id, "cmm_metric_stat_h", engine)
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT MAX(s.stat_date) AS stat_date\n"
        f"FROM {t_stat} s\n"
        f"JOIN {t_resource} r ON r.id = s.resource_id\n"
        f"WHERE r.platform_resource_id IN ({', '.join(str(i) for i in ids)})\n"
        "  AND r.dtime IS NULL\n"
        f"  AND s.stat_date >= '{since}'\n"
        f"  AND ({_source_pairs(sources)})"
    )


def build_sms_metric_rows_sql(
    db_id: str, engine: str, server_ids: Collection[Any], sources: SmsMetricSources,
    *, stat_hour_key: str,
) -> str:
    """감시 서버 묶음의 그 정각 통계 — 서버 · 지표 원천 쌍마다 평균값의 최대(여러 개 = 최악값).

    값은 0~1000만 받는다(쓰레기 값 걸러내기 — 구조 정본 관행). 결과 행 상한은 서버 수 × 지표 수다
    (묶음 집계라 그보다 많을 수 없다).
    """
    ids = _sms_ids(server_ids)
    hour = stat_hour(stat_hour_key)
    t_stat = _table(db_id, "cmm_metric_stat_h", engine)
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT r.platform_resource_id AS server_id, r.resource_type AS resource_type,\n"
        "       s.definition_name AS definition_name, MAX(s.avg_val) AS avg_val\n"
        f"FROM {t_stat} s\n"
        f"JOIN {t_resource} r ON r.id = s.resource_id\n"
        f"WHERE r.platform_resource_id IN ({', '.join(str(i) for i in ids)})\n"
        "  AND r.dtime IS NULL\n"
        f"  AND s.stat_date = '{hour}'\n"
        "  AND s.avg_val BETWEEN 0 AND 1000\n"
        f"  AND ({_source_pairs(sources)})\n"
        "GROUP BY r.platform_resource_id, r.resource_type, s.definition_name\n"
        "ORDER BY r.platform_resource_id\n"
        f"{_row_limit(engine, len(ids) * max(1, len(sources)))}"
    )


@dataclass(frozen=True)
class SmsMetricRow:
    """④ 서버 1대 · 지표 1개의 최신 정각 통계값(%)."""

    server_id: str
    metric: str
    value: float | None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def read_sms_latest_stat(
    execute: SqlExecutor, db_id: str, engine: str, server_ids: Collection[Any],
    sources: SmsMetricSources, *, since_hour: str,
) -> str | None:
    """④ 확인 질의 1건 → 최신 정각 통계 키(없으면 None)."""
    rows = await execute(db_id, build_sms_latest_stat_sql(
        db_id, engine, server_ids, sources, since_hour=since_hour))
    for row in rows:
        text = _text(_row_value(row, "stat_date"))
        if text:
            return stat_hour(text)
    return None


async def read_sms_metric_rows(
    execute: SqlExecutor, db_id: str, engine: str, server_ids: Collection[Any],
    sources: SmsMetricSources, *, stat_hour_key: str,
) -> list[SmsMetricRow]:
    """④ 지표 행 1건 → 서버 · 지표별 값(원천 쌍이 설정에 없는 행은 버린다)."""
    by_pair = {(rtype, definition): metric for metric, (rtype, definition) in sources.items()}
    rows = await execute(db_id, build_sms_metric_rows_sql(
        db_id, engine, server_ids, sources, stat_hour_key=stat_hour_key))
    out: list[SmsMetricRow] = []
    for row in rows:
        sid = _opt_int(_row_value(row, "server_id"))
        metric = by_pair.get((_text(_row_value(row, "resource_type")),
                              _text(_row_value(row, "definition_name"))))
        if sid is None or metric is None:
            continue
        out.append(SmsMetricRow(str(sid), metric, _float_or_none(_row_value(row, "avg_val"))))
    return out


# ── ⑤ (W8c) 지표 보기 SMS 이력 ─────────────────────────────────────────
#: 이력 조회 서버 상한(지표 보기 구성원 상한 `NOISE_BIZ_MONITOR_HISTORY_MAX_MEMBERS` ≤ 20과 같다).
HISTORY_MAX_SERVERS = 20
HISTORY_GRAIN_HOUR = "hour"
HISTORY_GRAIN_DAY = "day"
#: 통계 단위 → (통계 표, 키 형식, 키 간격 초, 점 상한).
_HISTORY_GRAINS: dict[str, tuple[str, str, int, int]] = {
    HISTORY_GRAIN_HOUR: ("cmm_metric_stat_h", "%Y%m%d%H", 3600, 24),
    HISTORY_GRAIN_DAY: ("cmm_metric_stat_d", "%Y%m%d", 86400, 7),
}
#: 통계 단위 → 점 상한(시간 통계 24점 · 일 통계 7점).
HISTORY_MAX_POINTS: dict[str, int] = {g: spec[3] for g, spec in _HISTORY_GRAINS.items()}
_STAT_KEY = {HISTORY_GRAIN_HOUR: re.compile(r"^\d{10}$"), HISTORY_GRAIN_DAY: re.compile(r"^\d{8}$")}


def _grain(grain: str) -> tuple[str, str, int, int]:
    spec = _HISTORY_GRAINS.get(grain)
    if spec is None:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"통계 단위가 hour·day가 아님: {grain!r}")
    return spec


def history_stat_key(value: Any, grain: str) -> str:
    """이력 통계 키(시간 `YYYYMMDDHH` · 일 `YYYYMMDD`) — 규칙 밖이면 `ScanSqlError`."""
    _grain(grain)
    text = _text(value)
    if not _STAT_KEY[grain].match(text):
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"통계 키가 {grain} 형식이 아님")
    return text


def history_points(grain: str, since_key: str, until_key: str) -> int:
    """통계 키 범위(양끝 포함)의 점 수 — 거꾸로이거나 점 상한을 넘으면 `ScanSqlError`."""
    _table_name, fmt, step, max_points = _grain(grain)
    try:
        since = datetime.strptime(history_stat_key(since_key, grain), fmt)
        until = datetime.strptime(history_stat_key(until_key, grain), fmt)
    except ValueError as exc:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "통계 키가 날짜가 아님") from exc
    points = int((until - since).total_seconds()) // step + 1
    if points < 1:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "통계 키 범위가 거꾸로다")
    if points > max_points:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"{grain} 통계는 {max_points}점까지")
    return points


def build_sms_history_sql(
    db_id: str, engine: str, server_ids: Collection[Any], sources: SmsMetricSources,
    *, grain: str, since_key: str, until_key: str,
) -> str:
    """조회 서버 묶음의 통계 키 범위 이력 — 서버 · 지표 원천 쌍 · 통계 키마다 평균값의 최대.

    값은 0~1000만 받는다(M3과 같은 쓰레기 값 걸러내기). 행 상한 = 점 수 × 서버 수 × 지표 수.
    """
    ids = sorted({int_key(i) for i in server_ids})
    if not ids:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, "조회 서버 id가 비었다")
    if len(ids) > HISTORY_MAX_SERVERS:
        raise ScanSqlError(ERROR_PAGE_KEY_INVALID, f"이력 조회 서버는 {HISTORY_MAX_SERVERS}대까지")
    table, _fmt, _step, _max = _grain(grain)
    points = history_points(grain, since_key, until_key)
    t_stat = _table(db_id, table, engine)
    t_resource = _table(db_id, "cmm_resource", engine)
    return (
        "SELECT r.platform_resource_id AS server_id, r.resource_type AS resource_type,\n"
        "       s.definition_name AS definition_name, s.stat_date AS stat_date,\n"
        "       MAX(s.avg_val) AS avg_val\n"
        f"FROM {t_stat} s\n"
        f"JOIN {t_resource} r ON r.id = s.resource_id\n"
        f"WHERE r.platform_resource_id IN ({', '.join(str(i) for i in ids)})\n"
        "  AND r.dtime IS NULL\n"
        f"  AND s.stat_date BETWEEN '{since_key}' AND '{until_key}'\n"
        "  AND s.avg_val BETWEEN 0 AND 1000\n"
        f"  AND ({_source_pairs(sources)})\n"
        "GROUP BY r.platform_resource_id, r.resource_type, s.definition_name, s.stat_date\n"
        "ORDER BY r.platform_resource_id, s.stat_date\n"
        f"{_row_limit(engine, points * len(ids) * max(1, len(sources)))}"
    )


@dataclass(frozen=True)
class SmsHistoryRow:
    """⑤ 서버 1대 · 지표 1개 · 통계 키 1개의 값(%)."""

    server_id: str
    metric: str
    stat_key: str
    value: float


async def read_sms_history(
    execute: SqlExecutor, db_id: str, engine: str, server_ids: Collection[Any],
    sources: SmsMetricSources, *, grain: str, since_key: str, until_key: str,
) -> list[SmsHistoryRow]:
    """⑤ 이력 SELECT 1건 → 행(원천 쌍이 설정에 없거나 키·값이 규칙 밖인 행은 버린다 · 보간 0)."""
    by_pair = {(rtype, definition): metric for metric, (rtype, definition) in sources.items()}
    rows = await execute(db_id, build_sms_history_sql(
        db_id, engine, server_ids, sources, grain=grain, since_key=since_key,
        until_key=until_key))
    out: list[SmsHistoryRow] = []
    for row in rows:
        sid = _opt_int(_row_value(row, "server_id"))
        metric = by_pair.get((_text(_row_value(row, "resource_type")),
                              _text(_row_value(row, "definition_name"))))
        key = _text(_row_value(row, "stat_date"))
        value = _float_or_none(_row_value(row, "avg_val"))
        if sid is None or metric is None or value is None or not _STAT_KEY[grain].match(key):
            continue
        out.append(SmsHistoryRow(str(sid), metric, key, value))
    return out


__all__ = [
    "ATTR_PAGE_ROWS",
    "HISTORY_GRAIN_DAY",
    "HISTORY_GRAIN_HOUR",
    "HISTORY_MAX_POINTS",
    "HISTORY_MAX_SERVERS",
    "ITAM_CONTRACT_COLUMNS",
    "ITAM_CONTRACT_TABLE",
    "ITAM_PII_COLUMNS",
    "SMS_IN_CHUNK",
    "SMS_QUERY_CONCURRENCY",
    "TREE_IN_CHUNK",
    "FolderRow",
    "ItamContractRow",
    "ServerAttrRow",
    "SmsHistoryRow",
    "SmsMetricRow",
    "SmsMetricSources",
    "build_attr_server_page_sql",
    "build_itam_contract_page_sql",
    "build_sms_history_sql",
    "build_sms_latest_stat_sql",
    "build_sms_metric_rows_sql",
    "build_tree_folder_sql",
    "history_points",
    "history_stat_key",
    "int_key",
    "parse_ancestry",
    "read_itam_contracts",
    "read_sms_history",
    "read_server_attrs",
    "read_sms_latest_stat",
    "read_sms_metric_rows",
    "read_tree_folders",
    "sms_query_semaphore",
    "stat_hour",
    "valid_sms_source_value",
]
