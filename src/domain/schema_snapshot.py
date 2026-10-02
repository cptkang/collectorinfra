"""스키마 스냅샷 · diff · 구조 정보 영향 판정 (plans/104 §3.4 · D-227).

**무엇을 하나.** 스키마 딕셔너리(`schema_to_dict` 모양)에서 컬럼 순서와 무관한 스냅샷을 만들고,
두 스냅샷의 차이(테이블·컬럼 추가/삭제 · 타입·NULL·PK·FK 변경)를 분류한다. 구조 정보(EAV·계층
패턴)가 참조하는 테이블·컬럼을 뽑아 diff에 걸리는지 판정한다(재분석 필요 배지).

**왜 지문이 아니라 스냅샷인가.** 런타임 지문(`fingerprint.py`)은 테이블별 컬럼 **개수**만 해시해
같은 개수의 컬럼 이름 교체·타입 변경을 못 잡는다(§1.2). 스냅샷은 `(컬럼명, 타입, NULL, PK)`와
FK 목록을 해시한다.

**엔진별 입력 형태.** PostgreSQL(소문자 · 비 public 스키마는 `schema.table` 키) · DB2(대문자 ·
bare 키 · 스키마는 별도 전달) · MariaDB(대소문자 구분 camelCase)를 같은 규칙으로 정규화한다.
- 테이블·컬럼 **키는 원형 그대로** 둔다 — MariaDB는 테이블명이 대소문자를 구분하므로(D-214)
  같은 DB를 두 번 찍은 스냅샷끼리의 diff는 원형 비교가 맞다.
- 타입은 `normalize_type`(소문자·공백 1개)으로, NULL·PK는 bool로 정규화한다(`YES`/`NO`/`Y`/`N`).
- 구조 정보 ↔ 스냅샷 매칭만 **대소문자 무시 + 스키마 접두 무시(bare name)** 로 한다 — 구조 정보는
  LLM·사람이 쓴 표기라 스키마 표기와 어긋날 수 있다.

계층: domain — 순수 함수 · I/O·LLM 0 · 표준 라이브러리만 · 스키마 리터럴 0.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

_TRUE_STRINGS = frozenset({"yes", "y", "true", "t", "1"})
_FALSE_STRINGS = frozenset({"no", "n", "false", "f", "0"})

# 조인 조건의 한정 식별자(`a.b` · `s.a.b`) — 따옴표는 파싱 전에 제거한다
_QUALIFIED_IDENT = r"[A-Za-z_][\w$#]*(?:\.[A-Za-z_][\w$#]*)+"
_QUALIFIED_RE = re.compile(_QUALIFIED_IDENT)
_EQUALITY_RE = re.compile(rf"({_QUALIFIED_IDENT})\s*=\s*({_QUALIFIED_IDENT})")

# 계층 패턴에서 `table`에 속하는 컬럼 키
_HIERARCHY_COLUMN_KEYS: tuple[str, ...] = (
    "id_column", "parent_column", "type_column", "name_column",
)


def normalize_type(raw: str) -> str:
    """컬럼 타입 표기를 정규화한다(앞뒤 공백 제거 · 연속 공백 1개 · 소문자).

    Args:
        raw: 엔진이 돌려준 타입 문자열(예: ``VARCHAR`` · ``character  varying``)

    Returns:
        정규화된 타입 문자열
    """
    return " ".join(str(raw or "").split()).lower()


def _as_bool(value: Any, default: bool) -> bool:
    """bool · ``YES``/``NO`` · ``Y``/``N`` · 0/1 표기를 bool로 바꾼다(모르는 값은 기본값)."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int):
        return value != 0
    text = str(value).strip().lower()
    if text in _TRUE_STRINGS:
        return True
    if text in _FALSE_STRINGS:
        return False
    return default


def _split_ref(ref: str) -> tuple[str, str]:
    """``table.column``(테이블에 스키마 접두가 있어도 된다)을 (테이블, 컬럼)으로 나눈다."""
    text = str(ref or "").strip().replace('"', "").replace("`", "")
    if "." not in text:
        return "", text
    table, column = text.rsplit(".", 1)
    return table, column


def bare_name(name: str) -> str:
    """스키마 접두·따옴표를 떼고 casefold한 이름(구조 정보 ↔ 스냅샷 매칭 키)."""
    text = str(name or "").strip().replace('"', "").replace("`", "")
    return text.rsplit(".", 1)[-1].casefold()


def _fk_target(ref: str) -> str:
    """FK 대상 ``[schema.]table.column``을 ``table.column``(원형 대소문자)으로 맞춘다.

    컬럼 `references`는 bare 표기, 관계 목록은 테이블 키 표기(스키마 접두 가능)라 같은 FK가
    두 번 잡히거나, 관계 표기만 바뀌어도 FK 변경으로 보이는 것을 막는다.
    """
    table, column = _split_ref(ref)
    if not table or not column:
        return ""
    return f"{table.rsplit('.', 1)[-1]}.{column}"


def _sha256(payload: Any) -> str:
    """JSON 정규 직렬화의 SHA-256 16진 문자열."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iter_columns(table_data: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    """테이블 데이터의 컬럼 항목을 순회한다(목록 또는 `{컬럼명: 속성}` 매핑 모두 허용)."""
    columns = table_data.get("columns") or []
    if isinstance(columns, Mapping):
        for name, attrs in columns.items():
            merged = dict(attrs) if isinstance(attrs, Mapping) else {}
            merged.setdefault("name", name)
            yield merged
        return
    for col in columns:
        if isinstance(col, Mapping):
            yield col


def _column_name(col: Mapping[str, Any]) -> str:
    """컬럼 항목의 이름(`name` 또는 MCP 원형 `column_name`)."""
    return str(col.get("name") or col.get("column_name") or "").strip()


def build_snapshot(
    schema_dict: Mapping[str, Any],
    table_schemas: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """스키마 딕셔너리에서 컬럼 순서와 무관한 스냅샷을 만든다.

    컬럼 항목은 캐시 모양(`name`·`type`·`nullable`·`primary_key`·`references`)과 MCP 원형
    (`column_name`·`data_type`·`is_nullable`·`is_primary_key`)을 모두 받는다. FK는 컬럼
    `references`와 최상위 `relationships`(`from` 테이블이 이 테이블인 것)를 합친다.

    Args:
        schema_dict: ``{"tables": {table: {"columns": [...]}}, "relationships": [...]}``
        table_schemas: 테이블 키 → 스키마명(없으면 키의 접두에서 추정, 접두도 없으면 None)

    Returns:
        ``{"tables": {table: {"schema", "columns": {col: {"type","nullable","primary_key"}},
        "foreign_keys": [정렬된 "col->table.col"], "hash"}}, "hash", "table_count"}`` — JSON 직렬화
        가능. FK 대상 테이블은 스키마 접두를 뗀다(`_fk_target`).
        테이블 해시에는 `schema`를 넣지 않는다(키가 이미 접두를 담고, 전달 여부로 해시가 흔들리지
        않게).
    """
    raw_tables = schema_dict.get("tables") or {}
    schemas = table_schemas or {}

    rel_fks: dict[str, set[str]] = {}
    for rel in schema_dict.get("relationships") or []:
        if not isinstance(rel, Mapping):
            continue
        from_table, from_col = _split_ref(str(rel.get("from", "")))
        to_ref = _fk_target(str(rel.get("to", "")))
        if from_table and from_col and to_ref:
            rel_fks.setdefault(from_table, set()).add(f"{from_col}->{to_ref}")

    tables: dict[str, Any] = {}
    for table_name in raw_tables:
        table_data = raw_tables[table_name]
        if not isinstance(table_data, Mapping):
            continue
        key = str(table_name)
        columns: dict[str, dict[str, Any]] = {}
        fks: set[str] = set(rel_fks.get(key, set()))
        for col in _iter_columns(table_data):
            name = _column_name(col)
            if not name:
                continue
            columns[name] = {
                "type": normalize_type(str(col.get("type", col.get("data_type", "")) or "")),
                "nullable": _as_bool(col.get("nullable", col.get("is_nullable")), True),
                "primary_key": _as_bool(
                    col.get("primary_key", col.get("is_primary_key")), False
                ),
            }
            ref = _fk_target(str(col.get("references") or ""))
            if ref:
                fks.add(f"{name}->{ref}")
        foreign_keys = sorted(fks)
        schema_name = schemas.get(key)
        if schema_name is None and "." in key:
            schema_name = key.rsplit(".", 1)[0]
        tables[key] = {
            "schema": schema_name,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "hash": _sha256({"columns": columns, "foreign_keys": foreign_keys}),
        }

    db_hash = _sha256(sorted((name, data["hash"]) for name, data in tables.items()))
    return {"tables": tables, "hash": db_hash, "table_count": len(tables)}


def _empty_diff(baseline: bool) -> dict[str, Any]:
    """빈 diff 결과 골격."""
    return {
        "baseline": baseline,
        "tables_added": [],
        "tables_removed": [],
        "columns_added": [],
        "columns_removed": [],
        "type_changed": [],
        "nullable_changed": [],
        "pk_changed": [],
        "fk_changed": [],
        "changed_tables": [],
        "summary": {},
        "has_changes": False,
    }


def diff_snapshots(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """두 스냅샷의 차이를 분류한다(키는 원형 그대로 비교).

    Args:
        old: 이전 스냅샷(없으면 기준선 — 변경 0으로 보고 `baseline=True`)
        new: 새 스냅샷

    Returns:
        ``{"baseline", "tables_added", "tables_removed", "columns_added"·"columns_removed"
        (``{table, column, type}``), "type_changed"·"nullable_changed"·"pk_changed"
        (``{table, column, old, new}``), "fk_changed"(``{table, added, removed}``),
        "changed_tables"(추가·삭제·수정된 테이블 전부 · 정렬), "summary"(항목별 건수 + total),
        "has_changes"}``
    """
    if old is None:
        result = _empty_diff(baseline=True)
        result["summary"] = _summarize(result)
        return result

    result = _empty_diff(baseline=False)
    old_tables: Mapping[str, Any] = old.get("tables") or {}
    new_tables: Mapping[str, Any] = new.get("tables") or {}

    result["tables_added"] = sorted(t for t in new_tables if t not in old_tables)
    result["tables_removed"] = sorted(t for t in old_tables if t not in new_tables)
    changed: set[str] = set(result["tables_added"]) | set(result["tables_removed"])

    for table in sorted(t for t in new_tables if t in old_tables):
        old_t = old_tables[table]
        new_t = new_tables[table]
        if old_t.get("hash") and old_t.get("hash") == new_t.get("hash"):
            continue
        old_cols: Mapping[str, Any] = old_t.get("columns") or {}
        new_cols: Mapping[str, Any] = new_t.get("columns") or {}

        for col in sorted(c for c in new_cols if c not in old_cols):
            result["columns_added"].append(
                {"table": table, "column": col, "type": new_cols[col].get("type", "")}
            )
            changed.add(table)
        for col in sorted(c for c in old_cols if c not in new_cols):
            result["columns_removed"].append(
                {"table": table, "column": col, "type": old_cols[col].get("type", "")}
            )
            changed.add(table)
        for col in sorted(c for c in new_cols if c in old_cols):
            for field, bucket in (
                ("type", "type_changed"),
                ("nullable", "nullable_changed"),
                ("primary_key", "pk_changed"),
            ):
                old_v = old_cols[col].get(field)
                new_v = new_cols[col].get(field)
                if old_v != new_v:
                    result[bucket].append(
                        {"table": table, "column": col, "old": old_v, "new": new_v}
                    )
                    changed.add(table)

        old_fks = set(old_t.get("foreign_keys") or [])
        new_fks = set(new_t.get("foreign_keys") or [])
        if old_fks != new_fks:
            result["fk_changed"].append({
                "table": table,
                "added": sorted(new_fks - old_fks),
                "removed": sorted(old_fks - new_fks),
            })
            changed.add(table)

    result["changed_tables"] = sorted(changed)
    result["summary"] = _summarize(result)
    result["has_changes"] = result["summary"]["total"] > 0
    return result


def _summarize(diff: Mapping[str, Any]) -> dict[str, int]:
    """diff 항목별 건수와 합계(`fk_changed`는 추가·삭제된 FK 개수로 센다)."""
    summary = {
        key: len(diff[key])
        for key in (
            "tables_added", "tables_removed", "columns_added", "columns_removed",
            "type_changed", "nullable_changed", "pk_changed",
        )
    }
    summary["fk_changed"] = sum(
        len(item.get("added", [])) + len(item.get("removed", [])) for item in diff["fk_changed"]
    )
    summary["total"] = sum(summary.values())
    return summary


def _ref(table: Any, column: Any, path: str) -> dict[str, Any] | None:
    """참조 항목 1건(테이블이 비었으면 None — 대조할 수 없다)."""
    table_text = str(table or "").strip()
    if not table_text:
        return None
    column_text = str(column).strip() if column not in (None, "") else None
    return {"table": table_text, "column": column_text, "path": path}


def parse_join_pairs(condition: str) -> list[tuple[tuple[str, str], tuple[str, str]]]:
    """조인 조건 문자열에서 ``a.b = c.d`` 등식 쌍을 뽑는다(따옴표 제거 · AND 여러 개 허용).

    Args:
        condition: 예 ``child_t.parent_id = parent_t.id AND ...``

    Returns:
        ``[((왼쪽 테이블, 왼쪽 컬럼), (오른쪽 테이블, 오른쪽 컬럼)), ...]``
    """
    text = str(condition or "").replace('"', "").replace("`", "")
    return [(_split_ref(left), _split_ref(right)) for left, right in _EQUALITY_RE.findall(text)]


def structure_refs(structure_meta: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """구조 정보 패턴이 참조하는 테이블·컬럼을 뽑는다.

    대상 키: ``entity_table`` · ``config_table`` · ``attribute_column``·``value_column``·``lob_*``
    (config 테이블 컬럼) · ``join_condition``(``a.b = c.d`` 파싱) · ``value_joins``
    (``eav_value_column``→config · ``entity_column``→entity) · ``direct_join``
    (``entity_column``→entity · ``config_column``→config) · ``entity_columns``(→entity) ·
    계층 ``table``·``id_column``·``parent_column``·``type_column``·``name_column``.
    테이블을 알 수 없는 컬럼 참조는 대조할 수 없어 뺀다.

    Args:
        structure_meta: ``{"patterns": [...], ...}`` (None이면 빈 목록)

    Returns:
        ``[{"table": str, "column": str | None, "path": "patterns[i].키"}]``
    """
    if not structure_meta:
        return []
    patterns = structure_meta.get("patterns") or []
    if not isinstance(patterns, list):
        return []

    refs: list[dict[str, Any] | None] = []
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, Mapping):
            continue
        base = f"patterns[{i}]"
        entity_table = pattern.get("entity_table")
        config_table = pattern.get("config_table")

        for key in ("entity_table", "config_table"):
            if pattern.get(key):
                refs.append(_ref(pattern.get(key), None, f"{base}.{key}"))
        for key in pattern:
            if key in ("attribute_column", "value_column") or (
                str(key).startswith("lob_") and str(key).endswith("_column")
            ):
                if pattern.get(key):
                    refs.append(_ref(config_table, pattern.get(key), f"{base}.{key}"))

        condition = pattern.get("join_condition")
        if isinstance(condition, str) and condition.strip():
            text = condition.replace('"', "").replace("`", "")
            for ident in _QUALIFIED_RE.findall(text):
                table, column = _split_ref(ident)
                refs.append(_ref(table, column, f"{base}.join_condition"))

        value_joins = pattern.get("value_joins") or []
        if isinstance(value_joins, list):
            for j, vj in enumerate(value_joins):
                if not isinstance(vj, Mapping):
                    continue
                if vj.get("eav_value_column"):
                    refs.append(_ref(
                        config_table, vj.get("eav_value_column"),
                        f"{base}.value_joins[{j}].eav_value_column",
                    ))
                if vj.get("entity_column"):
                    refs.append(_ref(
                        entity_table, vj.get("entity_column"),
                        f"{base}.value_joins[{j}].entity_column",
                    ))

        direct_join = pattern.get("direct_join")
        if isinstance(direct_join, Mapping):
            if direct_join.get("entity_column"):
                refs.append(_ref(
                    entity_table, direct_join.get("entity_column"),
                    f"{base}.direct_join.entity_column",
                ))
            if direct_join.get("config_column"):
                refs.append(_ref(
                    config_table, direct_join.get("config_column"),
                    f"{base}.direct_join.config_column",
                ))

        entity_columns = pattern.get("entity_columns") or []
        if isinstance(entity_columns, list):
            for j, ec in enumerate(entity_columns):
                name = ec.get("name") if isinstance(ec, Mapping) else ec
                if name:
                    refs.append(_ref(entity_table, name, f"{base}.entity_columns[{j}]"))

        hierarchy_table = pattern.get("table")
        if hierarchy_table:
            refs.append(_ref(hierarchy_table, None, f"{base}.table"))
            for key in _HIERARCHY_COLUMN_KEYS:
                if pattern.get(key):
                    refs.append(_ref(hierarchy_table, pattern.get(key), f"{base}.{key}"))

    return [r for r in refs if r is not None]


def structure_impact(
    diff: Mapping[str, Any],
    structure_meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """diff가 구조 정보 참조에 걸리는지 판정한다(재분석 필요 배지).

    - 테이블 참조: 그 테이블이 삭제·추가됐거나(``table_removed``·``table_added``) 컬럼·FK가
      하나라도 바뀌었으면(``table_changed``) 적중 — 보수적으로 본다.
    - 컬럼 참조: 그 컬럼이 삭제(``column_removed``)·타입(``type_changed``)·NULL
      (``nullable_changed``)·PK(``pk_changed``) 변경됐거나 테이블이 삭제되면 적중.
    매칭은 대소문자 무시 + 스키마 접두 무시(bare name)다.

    Args:
        diff: `diff_snapshots` 결과
        structure_meta: 적용본 또는 수동 프로필 구조 정보

    Returns:
        ``{"reanalysis_required": bool, "hits": [{"table","column","path","change"}]}``
    """
    removed = {bare_name(t) for t in diff.get("tables_removed") or []}
    added = {bare_name(t) for t in diff.get("tables_added") or []}
    modified: set[str] = set()
    column_changes: dict[tuple[str, str], list[str]] = {}
    for bucket, change in (
        ("columns_removed", "column_removed"),
        ("columns_added", None),
        ("type_changed", "type_changed"),
        ("nullable_changed", "nullable_changed"),
        ("pk_changed", "pk_changed"),
    ):
        for item in diff.get(bucket) or []:
            table = bare_name(str(item.get("table", "")))
            modified.add(table)
            if change:
                key = (table, str(item.get("column", "")).casefold())
                column_changes.setdefault(key, []).append(change)
    for item in diff.get("fk_changed") or []:
        modified.add(bare_name(str(item.get("table", ""))))

    hits: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str, str]] = set()

    def _hit(ref: Mapping[str, Any], change: str) -> None:
        key = (ref["table"], ref["column"], ref["path"], change)
        if key not in seen:
            seen.add(key)
            hits.append({
                "table": ref["table"], "column": ref["column"],
                "path": ref["path"], "change": change,
            })

    for ref in structure_refs(structure_meta):
        table = bare_name(ref["table"])
        if table in removed:
            _hit(ref, "table_removed")
            continue
        if ref["column"] is None:
            if table in added:
                _hit(ref, "table_added")
            elif table in modified:
                _hit(ref, "table_changed")
            continue
        for change in column_changes.get((table, ref["column"].casefold()), []):
            _hit(ref, change)

    return {"reanalysis_required": bool(hits), "hits": hits}
