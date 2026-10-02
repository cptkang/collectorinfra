"""자산 생성용 읽기 전용 조회 — 카탈로그(주석·행 수)·값 표본·관계 값 겹침 (D-294 · plans/133 W2).

자산 자동 생성(`asset_generation_service`)이 DB에 묻는 것은 전부 여기를 거친다. 규칙:

- **SELECT만 · 코드가 조립한다**(LLM SQL 없음). 식별자는 조각마다 `^[A-Za-z_][A-Za-z0-9_$#]*$`이고
  스냅샷에 실존해야 한다(`db_structure_service._resolve_column`).
- 카탈로그 조회(주석·행 수)는 **엔진별 모듈 상수 SQL**이다. `SQLGuard`의 금지어·인젝션 검사를 그대로
  쓰되 카탈로그 직접 조회 패턴(`INFORMATION_SCHEMA.`)만 예외로 둔다 — 서버 변수 조회
  `assert_constant_select`(`@@`만 예외)와 같은 방식이다. 넣는 값은 식별자 정규식을 통과한
  스키마명뿐이다.
- 값 표본·코드값은 기존 `collect_code_values`(`SELECT DISTINCT … IS NOT NULL` + 엔진별 행 제한)를
  쓴다.
- 관계 값 겹침은 자식 키 표본(`DISTINCT` · 행 제한)을 부모 기본키에 `LEFT JOIN`해 센다.
- 조회 수는 `ProbeBudget`이 센다 — 넘으면 남은 대상을 「예산 초과」로 표시한다(침묵 생략 금지).
- 대상별 실패는 사유로 남기고 다음 대상으로 간다(독립 신호 · 부분 결과 보존).

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.schema_cache.db_structure_service import (
    _resolve_column,
    collect_code_values,
    resolve_table,
)
from src.security.sql_guard import INJECTION_PATTERNS, SQLGuard
from src.utils.sql_dialect import is_db2, row_limit_clause

logger = logging.getLogger(__name__)

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
# 카탈로그 상수 조회에서만 빼는 인젝션 패턴(카탈로그 직접 조회 — 이 조회의 목적이다)
_CATALOG_PATTERN = r"INFORMATION_SCHEMA\."

#: 엔진별 카탈로그 조회 — `{schema}`는 식별자 정규식을 통과한 스키마명 리터럴 자리
#: (MariaDB는 스키마를 모르면 연결 기본 database `DATABASE()`)
CATALOG_SQL: Mapping[str, Mapping[str, str]] = {
    "mariadb": {
        "tables": (
            "SELECT table_name AS table_name, table_comment AS table_comment, "
            "table_rows AS row_estimate FROM information_schema.tables "
            "WHERE table_schema = {schema} AND table_type = 'BASE TABLE'"
        ),
        "columns": (
            "SELECT table_name AS table_name, column_name AS column_name, "
            "column_comment AS column_comment FROM information_schema.columns "
            "WHERE table_schema = {schema} AND column_comment <> ''"
        ),
    },
    "postgresql": {
        "tables": (
            "SELECT c.relname AS table_name, obj_description(c.oid, 'pg_class') AS table_comment, "
            "c.reltuples AS row_estimate FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relkind IN ('r', 'p') AND n.nspname = {schema}"
        ),
        "columns": (
            "SELECT c.relname AS table_name, a.attname AS column_name, "
            "d.description AS column_comment FROM pg_catalog.pg_description d "
            "JOIN pg_catalog.pg_class c ON c.oid = d.objoid "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum = d.objsubid "
            "WHERE d.objsubid > 0 AND n.nspname = {schema}"
        ),
    },
    "db2": {
        "tables": (
            "SELECT TRIM(TABNAME) AS table_name, REMARKS AS table_comment, "
            "CARD AS row_estimate FROM SYSCAT.TABLES "
            "WHERE TABSCHEMA = {schema} AND TYPE = 'T'"
        ),
        "columns": (
            "SELECT TRIM(TABNAME) AS table_name, TRIM(COLNAME) AS column_name, "
            "REMARKS AS column_comment FROM SYSCAT.COLUMNS "
            "WHERE TABSCHEMA = {schema} AND REMARKS IS NOT NULL"
        ),
    },
}
_ENGINE_ALIASES: Mapping[str, str] = {"postgres": "postgresql", "mysql": "mariadb"}


def engine_key(engine: str | None) -> str:
    """엔진 이름 정규화(별칭 흡수 · 소문자)."""
    text = str(engine or "").strip().lower()
    return _ENGINE_ALIASES.get(text, text)


def assert_catalog_select(sql: str) -> None:
    """카탈로그 상수 조회문의 안전성 검사(카탈로그 직접 조회 패턴만 예외).

    Raises:
        ValueError: 세미콜론 없는 단일 SELECT가 아니거나 금지 키워드·위험 패턴이 있을 때
    """
    text = sql.strip()
    if not text.upper().startswith("SELECT") or ";" in text:
        raise ValueError("카탈로그 조회는 세미콜론 없는 단일 SELECT여야 합니다")
    guard = SQLGuard()
    forbidden = guard.detect_forbidden_keywords(text)
    if forbidden:
        raise ValueError(f"금지 키워드: {', '.join(forbidden)}")
    patterns = [p for p in INJECTION_PATTERNS if p != _CATALOG_PATTERN]
    if guard.detect_injection_patterns(text, patterns):
        raise ValueError("위험 패턴이 감지되었습니다")


def build_catalog_sql(engine: str, kind: str, schema: str | None) -> str:
    """카탈로그 조회문을 만든다(스키마명은 식별자 정규식 통과 값만 리터럴로).

    Raises:
        ValueError: 모르는 엔진·종류 · 스키마명 형식 오류 · 안전성 검사 불통과
    """
    templates = CATALOG_SQL.get(engine_key(engine))
    if templates is None or kind not in templates:
        raise ValueError(f"카탈로그 조회를 모르는 엔진/종류: {engine!r}/{kind!r}")
    if schema:
        if not _IDENT_RE.fullmatch(schema):
            raise ValueError(f"허용되지 않는 스키마 식별자: {schema!r}")
        # DB2 카탈로그의 TABSCHEMA는 대문자 — 소문자로 넣으면 0행이 조용히 나온다
        schema_expr = f"'{schema.upper() if engine_key(engine) == 'db2' else schema}'"
    elif engine_key(engine) == "mariadb":
        schema_expr = "DATABASE()"
    else:
        raise ValueError(f"{engine} 카탈로그 조회에는 스키마명이 필요합니다")
    sql = templates[kind].format(schema=schema_expr)
    assert_catalog_select(sql)
    return sql


@dataclass
class ProbeBudget:
    """조회 예산 — 잡당 `execute_sql` 횟수 상한과 넘어서 생략한 대상."""

    limit: int
    used: int = 0
    skipped: list[str] = field(default_factory=list)

    def take(self, label: str) -> bool:
        """조회 1회를 쓴다(남았으면 True) — 없으면 `skipped`에 남기고 False."""
        if self.used >= self.limit:
            self.skipped.append(label)
            return False
        self.used += 1
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"limit": self.limit, "used": self.used, "skipped": len(self.skipped),
                "skipped_sample": self.skipped[:20]}


@dataclass
class CatalogInfo:
    """카탈로그 조회 결과 — 키는 스냅샷 테이블 키(``table`` · ``table.column``)."""

    table_comments: dict[str, str] = field(default_factory=dict)
    column_comments: dict[str, str] = field(default_factory=dict)
    row_estimates: dict[str, int | None] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def _lower_keys(row: Any) -> dict[str, Any]:
    """결과 행 키를 소문자로(DB2는 열 이름을 대문자로 돌려준다)."""
    return {str(k).lower(): v for k, v in dict(row).items()} if isinstance(row, Mapping) else {}


def _snapshot_schemas(snapshot: Mapping[str, Any], db_schema: str | None) -> list[str | None]:
    """카탈로그를 조회할 스키마 목록 — 스냅샷 테이블 스키마, 없으면 레지스트리 `db_schema`.

    둘 다 없으면 `[None]` — MariaDB는 연결 기본 database, 그 밖의 엔진은 조회문 조립에서 거절된다.
    """
    schemas: list[str | None] = []
    for data in (snapshot.get("tables") or {}).values():
        schema = data.get("schema") if isinstance(data, Mapping) else None
        if isinstance(schema, str) and schema and schema not in schemas:
            schemas.append(schema)
    if not schemas:
        schemas.append(db_schema or None)
    return schemas


def _table_key(snap_tables: Mapping[str, Any], schema: str | None, name: str) -> str | None:
    """카탈로그 행의 (스키마, 테이블)을 스냅샷 키로 — `schema.table` → 정확 → 대소문자 무시 유일.

    스키마가 주어지면 스냅샷 테이블의 스키마가 같을 때만 잇는다(다른 스키마의 같은 이름 테이블에
    주석이 붙지 않게).
    """
    def same_schema(key: str) -> bool:
        table_schema = (snap_tables.get(key) or {}).get("schema")
        if schema is None or not table_schema:
            return True
        return str(table_schema).casefold() == schema.casefold()

    for candidate in ([f"{schema}.{name}"] if schema else []) + [name]:
        if candidate in snap_tables and same_schema(candidate):
            return candidate
    key = resolve_table(snap_tables, name)
    return key if key is not None and same_schema(key) else None


async def read_catalog(
    client: Any,
    *,
    engine: str,
    snapshot: Mapping[str, Any],
    db_schema: str | None,
    budget: ProbeBudget,
) -> CatalogInfo:
    """테이블·컬럼 주석과 행 수 추정을 카탈로그에서 읽는다(스키마마다 2회)."""
    info = CatalogInfo()
    snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
    for schema in _snapshot_schemas(snapshot, db_schema):
        for kind in ("tables", "columns"):
            label = f"catalog:{kind}:{schema or 'default'}"
            try:
                sql = build_catalog_sql(engine, kind, schema)
            except ValueError as e:
                info.errors.append(f"{label}: {e}")
                continue
            if not budget.take(label):
                continue
            try:
                result = await client.execute_sql(sql)
            except Exception as e:  # noqa: BLE001 — 카탈로그 실패는 사유로 남기고 계속한다
                info.errors.append(f"{label}: {type(e).__name__}: {e}")
                logger.warning("카탈로그 조회 실패 (%s): %s", label, e)
                continue
            if getattr(result, "truncated", False):
                info.errors.append(
                    f"{label}: 결과가 행 상한에서 잘렸습니다 — 일부 주석·행 수가 빠졌습니다"
                )
            for row in getattr(result, "rows", None) or []:
                values = _lower_keys(row)
                key = _table_key(snap_tables, schema, str(values.get("table_name") or "").strip())
                if key is None:
                    continue
                if kind == "tables":
                    comment = str(values.get("table_comment") or "").strip()
                    if comment:
                        info.table_comments[key] = comment
                    info.row_estimates[key] = _to_int(values.get("row_estimate"))
                    continue
                column = str(values.get("column_name") or "").strip()
                comment = str(values.get("column_comment") or "").strip()
                columns = (snap_tables.get(key) or {}).get("columns") or {}
                actual = column if column in columns else next(
                    (c for c in columns if c.casefold() == column.casefold()), None
                )
                if actual and comment:
                    info.column_comments[f"{key}.{actual}"] = comment
    return info


def _to_int(value: Any) -> int | None:
    """정수 변환 — 음수(DB2 `CARD` -1 = 통계 미수집)와 변환 실패는 None(모름)."""
    try:
        number = int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
    return number if number is None or number >= 0 else None


async def sample_distinct(
    client: Any,
    columns: Sequence[str],
    *,
    snapshot: Mapping[str, Any],
    engine: str,
    db_schema: str | None,
    limit: int,
    budget: ProbeBudget,
) -> dict[str, dict[str, Any]]:
    """컬럼(``table.column``)마다 서로 다른 값 표본을 읽는다(최대 ``limit``개).

    Returns:
        ``{key: {"values": [...], "truncated": bool, "error": str|None, "sql": str|None}}`` —
        예산 초과 대상은 ``error="예산 초과"``
    """
    out: dict[str, dict[str, Any]] = {}
    for key in columns:
        table, _, column = key.rpartition(".")
        if not budget.take(f"distinct:{key}"):
            out[key] = {"values": [], "truncated": False, "error": "예산 초과", "sql": None}
            continue
        target = {"key": key, "table": table, "column": column, "origin": "asset", "known": []}
        (item,) = await collect_code_values(
            client, [target], snapshot=snapshot, engine=engine, db_schema=db_schema, limit=limit,
        )
        out[key] = {k: item.get(k) for k in ("values", "truncated", "error", "sql")}
    return out


def _qualified(table_key: str, engine: str, db_schema: str | None, table_schema: str | None) -> str:
    """테이블 참조 — DB2는 접두 없는 키에 스키마를 붙인다(코드값 조립과 같은 규칙)."""
    for part in table_key.split("."):
        if not _IDENT_RE.fullmatch(part):
            raise ValueError(f"허용되지 않는 식별자: {part!r}")
    if is_db2(engine) and "." not in table_key:
        schema = (db_schema or table_schema or "").strip()
        if schema:
            if not _IDENT_RE.fullmatch(schema):
                raise ValueError(f"허용되지 않는 스키마 식별자: {schema!r}")
            return f"{schema}.{table_key}"
    return table_key


def build_overlap_sql(
    child: str,
    child_columns: Sequence[str],
    parent: str,
    parent_columns: Sequence[str],
    *,
    snapshot: Mapping[str, Any],
    engine: str,
    db_schema: str | None,
    sample: int,
) -> str:
    """관계 값 겹침 조회 — 자식 키 표본(서로 다른 값 ``sample``개) 중 부모에 있는 수.

    ``SELECT COUNT(*) AS sampled, COUNT(p.<첫 부모 컬럼>) AS matched FROM (자식 표본) s
    LEFT JOIN 부모 p ON …`` — 부모 쪽은 기본키라 행이 불어나지 않는다.

    Raises:
        ValueError: 컬럼 수 불일치 · 스냅샷에 없는 식별자 · 안전성 검사 불통과
    """
    if not child_columns or len(child_columns) != len(parent_columns):
        raise ValueError("자식·부모 컬럼 수가 맞지 않습니다")
    resolved_child = [_resolve_column(snapshot, child, c) for c in child_columns]
    resolved_parent = [_resolve_column(snapshot, parent, c) for c in parent_columns]
    child_key, _, child_schema = resolved_child[0]
    parent_key, _, parent_schema = resolved_parent[0]
    for _table, column, _schema in [*resolved_child, *resolved_parent]:
        if not _IDENT_RE.fullmatch(column):
            raise ValueError(f"허용되지 않는 식별자: {column!r}")
    child_ref = _qualified(child_key, engine, db_schema, child_schema)
    parent_ref = _qualified(parent_key, engine, db_schema, parent_schema)
    c_cols = [c for _t, c, _s in resolved_child]
    p_cols = [c for _t, c, _s in resolved_parent]
    select_cols = ", ".join(f"c.{c} AS k{i}" for i, c in enumerate(c_cols))
    not_null = " AND ".join(f"c.{c} IS NOT NULL" for c in c_cols)
    on = " AND ".join(f"p.{p} = s.k{i}" for i, p in enumerate(p_cols))
    sql = (
        f"SELECT COUNT(*) AS sampled, COUNT(p.{p_cols[0]}) AS matched FROM "
        f"(SELECT DISTINCT {select_cols} FROM {child_ref} c WHERE {not_null} "
        f"{row_limit_clause(engine, sample)}) s LEFT JOIN {parent_ref} p ON {on}"
    )
    safe, reason = SQLGuard().is_safe_select(sql)
    if not safe:
        raise ValueError(f"조립 SQL이 안전성 검사를 통과하지 못했습니다: {reason}")
    return sql


async def check_overlap(
    client: Any,
    *,
    child: str,
    child_columns: Sequence[str],
    parent: str,
    parent_columns: Sequence[str],
    snapshot: Mapping[str, Any],
    engine: str,
    db_schema: str | None,
    budget: ProbeBudget,
    sample: int = 200,
) -> dict[str, Any]:
    """관계 후보의 값 겹침 비율을 잰다.

    Returns:
        ``{"sampled", "matched", "ratio", "sql", "error"}`` — 자식 표본이 0이면 ratio None
    """
    out: dict[str, Any] = {"sampled": None, "matched": None, "ratio": None, "sql": None,
                           "error": None}
    try:
        out["sql"] = build_overlap_sql(
            child, child_columns, parent, parent_columns, snapshot=snapshot, engine=engine,
            db_schema=db_schema, sample=sample,
        )
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not budget.take(f"overlap:{child}->{parent}"):
        out["error"] = "예산 초과"
        return out
    try:
        result = await client.execute_sql(out["sql"])
    except Exception as e:  # noqa: BLE001 — 관계 후보별 실패는 사유로 돌려준다
        out["error"] = f"{type(e).__name__}: {e}"
        logger.warning("관계 값 겹침 조회 실패 (%s->%s): %s", child, parent, e)
        return out
    rows = getattr(result, "rows", None) or []
    values = _lower_keys(rows[0]) if rows else {}
    sampled, matched = _to_int(values.get("sampled")), _to_int(values.get("matched"))
    out.update(sampled=sampled, matched=matched)
    if sampled:
        out["ratio"] = round((matched or 0) / sampled, 4)
    return out


async def sample_pairs(
    client: Any,
    table: str,
    key_column: str,
    label_column: str,
    *,
    snapshot: Mapping[str, Any],
    engine: str,
    db_schema: str | None,
    limit: int,
    budget: ProbeBudget,
) -> dict[str, Any]:
    """두 컬럼(코드 · 이름)의 서로 다른 쌍을 읽는다 — 공통코드 테이블 라벨용.

    Returns:
        ``{"pairs": [(코드, 이름)], "truncated", "error", "sql"}``
    """
    out: dict[str, Any] = {"pairs": [], "truncated": False, "error": None, "sql": None}
    try:
        table_key, key_col, table_schema = _resolve_column(snapshot, table, key_column)
        _t, label_col, _s = _resolve_column(snapshot, table, label_column)
        for column in (key_col, label_col):
            if not _IDENT_RE.fullmatch(column):
                raise ValueError(f"허용되지 않는 식별자: {column!r}")
        ref = _qualified(table_key, engine, db_schema, table_schema)
        out["sql"] = (
            f"SELECT DISTINCT {key_col} AS code_value, {label_col} AS code_label FROM {ref} "
            f"WHERE {key_col} IS NOT NULL {row_limit_clause(engine, limit)}"
        )
        safe, reason = SQLGuard().is_safe_select(out["sql"])
        if not safe:
            raise ValueError(f"조립 SQL이 안전성 검사를 통과하지 못했습니다: {reason}")
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not budget.take(f"pairs:{table}"):
        out["error"] = "예산 초과"
        return out
    try:
        result = await client.execute_sql(out["sql"])
    except Exception as e:  # noqa: BLE001 — 코드 테이블별 실패는 사유로 돌려준다
        out["error"] = f"{type(e).__name__}: {e}"
        return out
    rows = getattr(result, "rows", None) or []
    pairs: list[tuple[str, str]] = []
    for row in rows:
        values = _lower_keys(row)
        code, label = values.get("code_value"), values.get("code_label")
        if code is not None and label is not None and str(label).strip():
            pairs.append((str(code).strip(), str(label).strip()))
    out["pairs"] = pairs
    out["truncated"] = len(rows) >= limit or bool(getattr(result, "truncated", False))
    return out
