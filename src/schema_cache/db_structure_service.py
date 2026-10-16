"""관리자 「DB 구조」 서비스 — 소스 목록 · 변경 점검 · 구조 분석 초안 · 승인·되돌리기.

plans/104 A-2~A-6. API(`src/api/routes/db_structure.py`)가 부르는 유일한 진입이다. 무거운
작업(`run_check`·`run_analyze`)은 관리자 잡(`admin_jobs.AdminJobRunner`) 안에서 `JobContext`를
받아 실행된다.

- **소스 목록(A-2)**: MCP `list_sources` × 실행 중 레지스트리 × `ACTIVE_DB_IDS`를 대조한다.
  MCP에 닿지 못하면 연결·불일치 칸은 "확인 못 함"(None)으로 둔다.
- **변경 점검(A-3·A-4)**: `get_full_schema` 1회 → 스냅샷 diff → 구조 정보 영향 → 구조 정보가 지목한
  코드값(EAV 속성명·계층 유형)과 관리자가 지정한 코드 컬럼(G-10 (a))의 신규 값. 코드값 SQL은 코드가
  조립하고(식별자 정규식·스냅샷 실존 검증 · DB2는 스키마 한정) `SQLGuard.is_safe_select`를 통과해야
  실행한다(D-003). 스키마 캐시는 건드리지 않는다.
- **구조 분석(A-5)**: 범위(전체·변경·선택) → FK 묶음(묶음마다 LLM 1회) → 병합 → 샘플 SQL(패턴이 있을
  때 1회) → 결정적 검증 4종 → 현행 프로필과 필드 단위 병합(G-4 (c)) → 초안 저장.
- **승인·되돌리기(A-6)**: 검증 통과 · 같은 env · pending인 초안만 승인한다. 승인 시점의 현행
  프로필로 병합을 다시 계산해 `StructureStore.apply_profile`로 쓴다 — 프로필 파일 쓰기는 저장소
  한 곳뿐이다.
- **레거시 분석본(2026-09-17 사용자 확정)**: 승인 버전 없는 Redis `structure_meta`(예전 질의 경로
  LLM 분석)는 적용본이 아니다. 목록·상세에 "승인 필요" 후보로 보이고, `run_legacy_draft`가 그 값을
  분석 결과로 삼아(LLM 구조 분석 0) 구조 분석과 같은 검증·병합으로 초안을 만든다. 키는 지우지
  않는다.

공통 기반(`AdminServiceBase` — `admin_base`, plans/166 I3)은 등록 서비스
(`db_registration_service`)와 함께 쓴다 — 클라이언트·LLM·레지스트리·저장소 주입, 구조 상태, 준비도
입력 수집. 코드값 수집은 이 모듈에 있다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from src.dbhub.models import SchemaInfo, schema_to_dict
from src.domain.db_readiness import ReadinessReport, is_local_sandbox_env
from src.domain.profile_merge import merge_profile, profile_field_diff
from src.domain.schema_snapshot import (
    bare_name,
    build_snapshot,
    diff_snapshots,
    structure_impact,
)

# 공통 기반은 `admin_base`로 옮겼다(plans/166 I3 · D-337 ① — 옛 경로 재노출 shim 1사이클).
# `X as X`는 mypy strict(no_implicit_reexport)용 명시 재노출이다 — 이 블록만 정렬에서 뺀다.
# isort: off
from src.schema_cache.admin_base import (
    APPROVED_VERSION_KINDS as APPROVED_VERSION_KINDS,
    HEALTH_TIMEOUT_SECONDS as HEALTH_TIMEOUT_SECONDS,
    AdminServiceBase as AdminServiceBase,
    ClientFactory as ClientFactory,
    LLMFactory as LLMFactory,
    RegistryGetter as RegistryGetter,
    _DB_ID_RE as _DB_ID_RE,
    _default_registry as _default_registry,
    _is_valid_source as _is_valid_source,
    _legacy_meta_summary as _legacy_meta_summary,
    resolve_table as resolve_table,
)
# isort: on
from src.schema_cache.structure_analysis import (
    analyze_structure,
    generate_structure_samples,
    merge_outcomes,
    split_fk_groups,
    validate_structure_draft,
)
from src.schema_cache.structure_store import count_comment_lines, validate_db_id
from src.schema_cache.value_index import _extract_value, build_distinct_values_sql
from src.security.sql_guard import SQLGuard
from src.utils.clock import now_iso as _now_iso
from src.utils.sql_dialect import is_db2

if TYPE_CHECKING:
    from src.schema_cache.admin_jobs import JobContext

logger = logging.getLogger(__name__)

#: 코드값 DISTINCT 수집 상한(A-4 · G-10) — 값 목록이 이보다 많으면 truncated로 표시한다
CODE_VALUE_LIMIT = 500
#: 구조 분석 범위
ANALYZE_SCOPES: tuple[str, ...] = ("all", "changed", "tables")
#: 로컬 샌드박스 산출물 머리말(R7 · `plans/95` §4.6.2)
LOCAL_SANDBOX_HEADER = "# LOCAL SANDBOX — 운영 정본 재료로 쓰지 말 것"

# SQL에 넣는 식별자(스키마·테이블·컬럼 한 조각) — 따옴표·공백·구분자를 허용하지 않는다
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
# 스냅샷에서 확인한 테이블·컬럼 조각은 한글 음절(U+AC00–U+D7A3)도 받는다(plans/140 W1-0)
_SNAPSHOT_IDENT_RE = re.compile(r"^[A-Za-z_가-힣][A-Za-z0-9_$#가-힣]*$")
# 비ASCII 조각을 감싸는 엔진별 인용 문자
_IDENT_QUOTES: Mapping[str, str] = {
    "mariadb": "`", "mysql": "`", "postgresql": '"', "postgres": '"', "db2": '"',
}


class DraftNotApprovable(ValueError):  # noqa: N818 - 계약 확정 이름
    """초안 승인·반려·보고서나 버전 되돌리기를 할 수 없는 상태(API 409).

    Attributes:
        code: `not_found` · `not_pending` · `validation_failed` · `env_mismatch` ·
            `version_not_found` · `legacy_not_found`(레거시 분석본 후보 아님)
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _norm(value: Any) -> str:
    """엔진·env 비교용 정규화(앞뒤 공백 · 끝 `/` 제거 · 소문자)."""
    return str(value or "").strip().rstrip("/").lower()


# ──────────────────────────────────────────────
# 스키마 수집 · 스냅샷 → 분석용 스키마 딕셔너리
# ──────────────────────────────────────────────


def schema_dict_from_full_schema(full: SchemaInfo) -> tuple[dict[str, Any], dict[str, str]]:
    """`get_full_schema` 결과를 캐시 모양 스키마 딕셔너리와 테이블별 스키마명으로 바꾼다.

    `schema_to_dict`는 부분 테이블용 관계 필터(`from`의 첫 점 앞 = 테이블)를 거치는데, 스키마
    접두가 있는 테이블 키(`schema.table`)의 관계는 그 필터에서 빠진다. 여기서는 전체 테이블을
    넣으므로 걸러야 할 관계가 없다 — 관계는 수집 결과 그대로 둔다(질의 경로 지연 수집과 같은 모양).

    Returns:
        (스키마 딕셔너리, 테이블 키 → 스키마명)
    """
    schema_dict = schema_to_dict(full, list(full.tables))
    schema_dict["relationships"] = list(full.relationships)
    table_schemas = {name: table.schema_name for name, table in full.tables.items()}
    return schema_dict, table_schemas


def schema_dict_from_snapshot(
    snapshot: Mapping[str, Any], tables: Sequence[str] | None = None
) -> dict[str, Any]:
    """스냅샷을 구조 분석 입력(캐시 모양 스키마 딕셔너리)으로 되돌린다.

    컬럼 순서는 스냅샷 기록 순서를 따른다. 관계는 범위 안 테이블끼리만 남긴다(범위 밖 참조를 LLM에
    보이지 않는다 — 부분 분석이 범위 밖 테이블을 지어내지 않게).

    Args:
        snapshot: `build_snapshot` 결과
        tables: 포함할 테이블 키(없으면 전체)

    Returns:
        ``{"tables": {table: {"columns": [...]}}, "relationships": [{"from", "to"}]}``
    """
    snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
    names = [t for t in (tables if tables is not None else list(snap_tables)) if t in snap_tables]
    in_scope = {bare_name(t) for t in names}
    out_tables: dict[str, Any] = {}
    relationships: list[dict[str, str]] = []
    for name in names:
        data = snap_tables[name]
        fk_map: dict[str, str] = {}
        for fk in data.get("foreign_keys") or []:
            column, _, target = str(fk).partition("->")
            if not column or "." not in target:
                continue
            fk_map.setdefault(column, target)
            if bare_name(target.rsplit(".", 1)[0]) in in_scope:
                relationships.append({"from": f"{name}.{column}", "to": target})
        out_tables[name] = {
            "columns": [
                {
                    "name": column,
                    "type": attrs.get("type", ""),
                    "nullable": attrs.get("nullable", True),
                    "primary_key": attrs.get("primary_key", False),
                    "foreign_key": column in fk_map,
                    "references": fk_map.get(column),
                }
                for column, attrs in (data.get("columns") or {}).items()
                if isinstance(attrs, Mapping)
            ],
        }
    return {"tables": out_tables, "relationships": relationships}


def _resolve_column(
    snapshot: Mapping[str, Any], table: str, column: str
) -> tuple[str, str, str | None]:
    """스냅샷에서 테이블·컬럼 실제 표기와 테이블 스키마명을 찾는다.

    Raises:
        ValueError: 테이블·컬럼이 스냅샷에 없거나 모호할 때
    """
    snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
    table_key = resolve_table(snap_tables, table)
    if table_key is None:
        raise ValueError(f"테이블 '{table}'이(가) 스냅샷에 없습니다")
    columns: Mapping[str, Any] = snap_tables[table_key].get("columns") or {}
    if column in columns:
        column_name = column
    else:
        matches = [c for c in columns if c.casefold() == column.casefold()]
        if len(matches) != 1:
            raise ValueError(f"컬럼 '{table_key}.{column}'이(가) 스냅샷에 없습니다")
        column_name = matches[0]
    schema_name = snap_tables[table_key].get("schema")
    return table_key, column_name, schema_name if isinstance(schema_name, str) else None


# ──────────────────────────────────────────────
# 코드값 수집 (A-4 · G-3 (b) · G-10 (a))
# ──────────────────────────────────────────────


def _known_attribute_names(pattern: Mapping[str, Any]) -> list[str]:
    """EAV 패턴 `known_attributes`의 이름 목록(문자열 항목 또는 `{name}` 항목)."""
    names: list[str] = []
    for item in pattern.get("known_attributes") or []:
        name = item.get("name") if isinstance(item, Mapping) else item
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def structure_code_targets(structure_meta: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """구조 정보가 코드값 목록을 가진 곳 — EAV 속성명 컬럼 · 계층 유형 컬럼.

    Returns:
        ``[{"key", "table", "column", "origin": "eav_attribute"|"hierarchy_type", "known": [...]}]``
    """
    targets: list[dict[str, Any]] = []
    patterns = (structure_meta or {}).get("patterns") or []
    for pattern in patterns if isinstance(patterns, list) else []:
        if not isinstance(pattern, Mapping):
            continue
        if pattern.get("type") == "eav":
            table, column = pattern.get("config_table"), pattern.get("attribute_column")
            origin, known = "eav_attribute", _known_attribute_names(pattern)
        elif pattern.get("type") == "hierarchy":
            table, column = pattern.get("table"), pattern.get("type_column")
            origin, known = "hierarchy_type", []
        else:
            continue
        if isinstance(table, str) and isinstance(column, str) and table and column:
            targets.append({
                "key": f"{table}.{column}", "table": table, "column": column,
                "origin": origin, "known": known,
            })
    return targets


def admin_code_targets(code_columns: Sequence[str] | None) -> list[dict[str, Any]]:
    """관리자가 지정한 코드 컬럼(`"table.column"` · 스키마 접두 허용)을 수집 대상으로 바꾼다.

    형식이 틀린 항목도 버리지 않고 `error`를 달아 돌려준다(침묵 탈락 금지).
    """
    targets: list[dict[str, Any]] = []
    for raw in code_columns or []:
        text = str(raw or "").strip()
        table, _, column = text.rpartition(".")
        target: dict[str, Any] = {
            "key": text, "table": table, "column": column, "origin": "admin", "known": [],
        }
        if not table or not column:
            target["error"] = f"코드 컬럼 형식 오류(table.column 필요): {text!r}"
        targets.append(target)
    return targets


def _dedupe_targets(targets: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """같은 키(대소문자 무시)의 수집 대상을 하나로 합친다(먼저 나온 것 유지 · known 합집합)."""
    merged: dict[str, dict[str, Any]] = {}
    for target in targets:
        folded = str(target.get("key", "")).casefold()
        if folded in merged:
            known = merged[folded].setdefault("known", [])
            known.extend(k for k in target.get("known") or [] if k not in known)
            continue
        merged[folded] = dict(target)
    return list(merged.values())


def snapshot_identifier(part: str, engine: str | None) -> str:
    """스냅샷에서 확인한 식별자 한 조각을 SQL 표기로 만든다(plans/140 W1-0).

    라틴 조각(`^[A-Za-z_][A-Za-z0-9_$#]*$`)은 인용 없이 그대로 둔다 — 종전 조립 SQL과 바이트
    동일하다. 한글 음절이 든 조각만 엔진 인용으로 감싼다(MariaDB·MySQL 백틱 · PostgreSQL·DB2
    큰따옴표). 인용 문자·공백·구분자가 든 이름은 거부한다.

    Raises:
        ValueError: 허용되지 않는 식별자 · 한글 조각의 인용 방식을 모르는 엔진
    """
    if _IDENT_RE.fullmatch(part):
        return part
    if not _SNAPSHOT_IDENT_RE.fullmatch(part):
        raise ValueError(f"허용되지 않는 식별자: {part!r}")
    quote = _IDENT_QUOTES.get(str(engine or "").strip().lower())
    if quote is None:
        raise ValueError(f"한글 식별자의 인용 방식을 모르는 엔진입니다: {engine!r}")
    return f"{quote}{part}{quote}"


def build_code_value_sql(
    table_key: str,
    column: str,
    *,
    engine: str,
    db_schema: str | None,
    table_schema: str | None,
    limit: int,
) -> str:
    """코드값 `SELECT DISTINCT` 문을 조립하고 안전성 검사를 통과시킨다.

    테이블·컬럼 조각은 `snapshot_identifier`를 지난다(라틴은 그대로 · 한글은 엔진 인용). DB2는
    테이블 키에 스키마가 없으면 레지스트리 `db_schema`(없으면 스냅샷 테이블 스키마)로 한정한다 —
    스키마명은 라틴 식별자만 받는다.

    Raises:
        ValueError: 식별자 형식 오류 · `SQLGuard.is_safe_select` 불통과
    """
    table_ref = ".".join(snapshot_identifier(part, engine) for part in table_key.split("."))
    column = snapshot_identifier(column, engine)
    if is_db2(engine) and "." not in table_key:
        schema = (db_schema or table_schema or "").strip()
        if schema:
            if not _IDENT_RE.fullmatch(schema):
                raise ValueError(f"허용되지 않는 스키마 식별자: {schema!r}")
            table_ref = f"{schema}.{table_ref}"
    sql = build_distinct_values_sql(table_ref, column, engine, limit)
    safe, reason = SQLGuard().is_safe_select(sql)
    if not safe:
        raise ValueError(f"조립 SQL이 안전성 검사를 통과하지 못했습니다: {reason}")
    return sql


async def collect_code_values(
    client: Any,
    targets: Sequence[Mapping[str, Any]],
    *,
    snapshot: Mapping[str, Any],
    engine: str,
    db_schema: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """코드값을 대상마다 읽기 전용 `SELECT DISTINCT`로 수집한다(대상별 실패 격리 · 사유 기록).

    Returns:
        ``[{"key", "table", "column", "origin", "known", "sql", "values", "truncated", "error"}]``
    """
    results: list[dict[str, Any]] = []
    for target in targets:
        item: dict[str, Any] = {
            "key": target.get("key"), "table": target.get("table"),
            "column": target.get("column"), "origin": target.get("origin"),
            "known": list(target.get("known") or []),
            "sql": None, "values": [], "truncated": False, "error": target.get("error"),
        }
        results.append(item)
        if item["error"]:
            continue
        try:
            table_key, column, table_schema = _resolve_column(
                snapshot, str(item["table"]), str(item["column"])
            )
            item["sql"] = build_code_value_sql(
                table_key, column, engine=engine, db_schema=db_schema,
                table_schema=table_schema, limit=limit,
            )
        except ValueError as e:
            item["error"] = str(e)
            continue
        try:
            result = await client.execute_sql(item["sql"])
        except Exception as e:  # noqa: BLE001 — 대상별 실패는 사유로 돌려준다
            item["error"] = f"{type(e).__name__}: {e}"
            logger.warning("코드값 수집 실패 (key=%s, sql=%s): %s", item["key"], item["sql"], e)
            continue
        values: list[str] = []
        for row in getattr(result, "rows", None) or []:
            value = _extract_value(row)
            if value is not None and value not in values:
                values.append(value)
        item["values"] = values
        item["truncated"] = len(values) >= limit or bool(getattr(result, "truncated", False))
    return results


def readiness_summary(report: ReadinessReport) -> dict[str, Any]:
    """목록 준비도 열 요약 — 건수·문구 + 미충족 필수 항목(맨 위에 보일 것)."""
    data = report.to_dict()
    data.pop("items", None)
    data["unmet_required"] = [
        {"code": item.code, "label": item.label, "detail": item.detail}
        for item in report.unmet_required()
    ]
    return data


# ──────────────────────────────────────────────
# DB 구조 서비스
# ──────────────────────────────────────────────


class DBStructureService(AdminServiceBase):
    """관리자 「DB 구조」 탭의 소스 목록·점검·분석·승인 서비스."""

    # --- 목록·상세 ---

    async def list_sources(self) -> dict[str, Any]:
        """MCP 소스 × 레지스트리 × `ACTIVE_DB_IDS` 대조 목록(A-2 · G-6 (a)).

        Returns:
            ``{"mcp_available", "mcp_error", "env", "local_sandbox", "provider", "sources": [row]}``
            — row 필드는 계약 §4.1(`mismatch`는 MCP 미가용이면 None)
        """
        registry = self._registry()
        registry_ids = list(registry.db_ids()) if registry is not None else []
        active_ids = self._active_db_ids()
        view = await self._mcp_view()
        names = list(dict.fromkeys([*view["order"], *registry_ids, *active_ids]))
        rows = [await self._source_row(name, view, registry, active_ids) for name in names]
        return {
            "mcp_available": view["available"],
            "mcp_error": view["error"],
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "sources": rows,
        }

    async def _source_row(
        self, name: str, view: Mapping[str, Any], registry: Any, active_ids: Sequence[str]
    ) -> dict[str, Any]:
        entry = registry.get(name) if registry is not None else None
        mcp_row = view["sources"].get(name)
        in_mcp = mcp_row is not None
        registered = entry is not None
        active = name in active_ids
        mismatch: str | None = None
        if view["available"]:
            if in_mcp and not registered:
                mismatch = "mcp_only"
            elif not in_mcp and (registered or active):
                mismatch = "app_only"
        mcp_type = (mcp_row or {}).get("type") or None
        registry_engine = entry.engine if entry is not None else None
        row: dict[str, Any] = {
            "source": name,
            "engine": mcp_type or registry_engine,
            "readonly": (mcp_row or {}).get("readonly"),
            "in_mcp": in_mcp,
            "health": view["health"].get(name),
            "registered": registered,
            "registry_enabled": bool(getattr(entry, "enabled", True)) if registered else None,
            "registry_engine": registry_engine,
            "engine_mismatch": bool(
                mcp_type and registry_engine and _norm(mcp_type) != _norm(registry_engine)
            ),
            "active": active,
            "mismatch": mismatch,
        }
        if not _is_valid_source(name):
            row.update({
                "structure": {"status": "none", "latest_ver": None, "drift": False,
                              "environment": None, "pending_drafts": 0, "warning": None,
                              "legacy_candidate": False},
                "last_check": None, "registration": {},
                "readiness": readiness_summary(await self._readiness_for(name, view)),
                "error": "소스 이름 형식이 관리자 자산 키로 허용되지 않습니다(^[A-Za-z0-9_-]+$)",
            })
            return row

        structure = await self._structure_state(name)
        drafts = await self._store.list_drafts(name)
        warning: str | None = None
        if structure["legacy_candidate"]:
            warning = "legacy_unapproved"
        elif active and structure["status"] == "none":
            warning = "active_without_structure"
        elif structure["drift"]:
            warning = "profile_drift"
        registration = await self._store.load_registration(name)
        snapshot_record = await self._store.load_snapshot(name)
        report = await self._readiness_for(
            name, view, structure=structure, registration=registration,
            snapshot_record=snapshot_record,
        )
        row.update({
            "structure": {
                "status": structure["status"],
                "latest_ver": structure["latest_ver"],
                "drift": structure["drift"],
                "environment": structure["environment"],
                "pending_drafts": sum(1 for d in drafts if d.get("status") == "pending"),
                "warning": warning,
                "legacy_candidate": structure["legacy_candidate"],
            },
            "last_check": _last_check_summary(await self._store.load_check_result(name)),
            "registration": registration,
            "readiness": readiness_summary(report),
        })
        return row

    async def get_detail(self, source: str) -> dict[str, Any]:
        """소스 상세 — 현행 프로필 · 초안 · 버전(원문 제외) · 마지막 점검 · 스냅샷 · 등록 · 준비도.

        `legacy_meta`: 레거시 분석본 후보면 요약(패턴 수 · query_guide 길이 · samples 유무), 아니면
        None.

        Raises:
            ValueError: 소스 이름 형식 오류
        """
        validate_db_id(source)
        state = dict(self._store.profile_state(source))
        current = self._store.read_current_profile(source)
        versions = [
            {k: v for k, v in version.items() if k not in ("content", "profile")}
            for version in await self._store.list_versions(source)
        ]
        snapshot_record = await self._store.load_snapshot(source)
        registration = await self._store.load_registration(source)
        structure = await self._structure_state(source)
        view = await self._mcp_view(health_for=[source])
        report = await self._readiness_for(
            source, view, structure=structure, registration=registration,
            snapshot_record=snapshot_record,
        )
        snapshot_summary = None
        if snapshot_record:
            snapshot = snapshot_record.get("snapshot") or {}
            snapshot_summary = {
                "hash": snapshot_record.get("hash"),
                "taken_at": snapshot_record.get("taken_at"),
                "env": snapshot_record.get("env"),
                "by": snapshot_record.get("by"),
                "table_count": snapshot.get("table_count"),
                # 기준선 출처 — DDL 등록(D-292)만 표기가 있고 없으면 MCP 수집이다
                "origin": snapshot_record.get("origin") or "mcp",
            }
        return {
            "source": source,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "profile": {**state, "profile": current.get("profile") if current else None},
            "structure": structure,
            "legacy_meta": structure["legacy_meta"],
            "drafts": await self._store.list_drafts(source),
            "versions": versions,
            "last_check": await self._store.load_check_result(source),
            "snapshot": snapshot_summary,
            "registration": registration,
            "readiness": report.to_dict(),
        }

    # --- 변경 점검 ---

    async def _collect_snapshot(
        self, client: Any, source: str, by: str | None
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
        """스키마를 1회 수집해 스냅샷을 만든다(저장은 호출자).

        Returns:
            (스키마 딕셔너리, 스냅샷 기록 `{snapshot, hash, taken_at, env, by}`, 직전 스냅샷 기록)
        """
        full = await client.get_full_schema()
        schema_dict, table_schemas = schema_dict_from_full_schema(full)
        snapshot = build_snapshot(schema_dict, table_schemas)
        previous = await self._store.load_snapshot(source)
        record = {
            "snapshot": snapshot, "hash": snapshot["hash"], "taken_at": _now_iso(),
            "env": self.env, "by": by,
        }
        return schema_dict, record, previous

    async def run_check(
        self,
        source: str,
        *,
        by: str | None,
        code_columns: list[str] | None,
        ctx: JobContext,
    ) -> dict[str, Any]:
        """변경 점검 잡 본문(A-3·A-4) — 스냅샷 diff · 구조 영향 · 신규 코드값. LLM 0.

        신규 코드값: 이전 점검 값(없으면 EAV `known_attributes` 이름)과 대조한다. 대조 기준이 전혀
        없는 첫 수집은 `baseline=True`로 표시하고 신규 값을 세지 않는다.

        Raises:
            ValueError: 소스 이름 형식 오류
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        await ctx.progress(0, 3, "스키마 수집")
        async with self._client_factory(source) as client:
            _schema_dict, record, previous = await self._collect_snapshot(client, source, by)
            snapshot = record["snapshot"]
            diff = diff_snapshots((previous or {}).get("snapshot"), snapshot)
            structure = await self._current_structure(source)
            impact = structure_impact(diff, structure)

            await ctx.progress(1, 3, "코드값 대조")
            targets = _dedupe_targets([
                *structure_code_targets(structure), *admin_code_targets(code_columns),
            ])
            engine, db_schema = await self._engine_and_schema(source, client)
            items = await collect_code_values(
                client, targets, snapshot=snapshot, engine=engine, db_schema=db_schema,
                limit=CODE_VALUE_LIMIT,
            )

        previous_check = await self._store.load_check_result(source)
        previous_values = {
            str(item.get("key")): item.get("values") or []
            for item in (previous_check or {}).get("code_values") or []
            if isinstance(item, Mapping) and not item.get("error")
        }
        new_total = 0
        for item in items:
            reference = set(item["known"])
            prior = previous_values.get(str(item["key"]))
            item["baseline"] = not item["error"] and prior is None and not reference
            if prior is not None:
                reference |= set(prior)
            item["new_values"] = (
                [] if item["error"] or item["baseline"]
                else [v for v in item["values"] if v not in reference]
            )
            new_total += len(item["new_values"])

        await ctx.progress(2, 3, "결과 저장")
        taken_at = record["taken_at"]
        result = {
            "source": source,
            "at": taken_at,
            "env": self.env,
            "by": by,
            "snapshot_hash": record["hash"],
            "previous_snapshot_hash": (previous or {}).get("hash"),
            "table_count": snapshot["table_count"],
            "diff": diff,
            "impact": impact,
            "reanalysis_required": impact["reanalysis_required"],
            "code_values": items,
            "new_code_value_count": new_total,
            "summary": {**diff["summary"], "new_code_values": new_total},
        }
        await self._store.save_snapshot(source, record)
        await self._store.save_check_result(source, result)
        await ctx.progress(3, 3, "완료")
        logger.info(
            "DB 구조 변경 점검: source=%s, tables=%d, changes=%d, reanalysis=%s, "
            "new_codes=%d, by=%s",
            source, snapshot["table_count"], diff["summary"]["total"],
            impact["reanalysis_required"], new_total, by,
        )
        return result

    # --- 구조 분석 ---

    async def _analysis_scope(
        self, source: str, scope: str, tables: list[str] | None, snapshot: Mapping[str, Any]
    ) -> list[str]:
        snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
        if scope == "all":
            names = list(snap_tables)
        elif scope == "changed":
            check = await self._store.load_check_result(source)
            if not check:
                raise ValueError("변경 점검 결과가 없습니다 — 변경 점검을 먼저 실행하세요")
            changed = ((check.get("diff") or {}).get("changed_tables")) or []
            names = [t for t in changed if t in snap_tables]
        else:
            if not tables:
                raise ValueError("scope=tables에는 tables가 필요합니다")
            names, unknown = [], []
            for name in tables:
                key = resolve_table(snap_tables, str(name))
                if key is None:
                    unknown.append(str(name))
                elif key not in names:
                    names.append(key)
            if unknown:
                raise ValueError(f"스냅샷에 없는 테이블: {', '.join(unknown)}")
        if not names:
            raise ValueError(f"분석할 테이블이 없습니다(scope={scope})")
        return names

    async def run_analyze(
        self,
        source: str,
        *,
        scope: str,
        tables: list[str] | None,
        code_columns: list[str] | None,
        by: str | None,
        ctx: JobContext,
    ) -> dict[str, Any]:
        """구조 분석 잡 본문(A-5) — FK 묶음 분석 · 샘플 · 결정적 검증 · 필드 병합 초안.

        LLM 호출 수 = 묶음 수 + (패턴이 있으면 샘플 SQL 생성 1회). 전 묶음이 실패하면 초안을 만들지
        않고 사유를 돌려준다. 일부 묶음만 실패하면 초안은 저장되지만 검증이 통과하지 않는다.

        Raises:
            ValueError: 소스 이름·scope·tables 오류
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        if scope not in ANALYZE_SCOPES:
            raise ValueError(f"scope는 {', '.join(ANALYZE_SCOPES)} 중 하나여야 합니다: {scope!r}")
        await self._require_store()
        max_tables = int(self._config.schema_cache.structure_group_max_tables)
        provider = self.provider_info()

        record = await self._ensure_snapshot(source, by, ctx)
        snapshot = record["snapshot"]
        scoped = await self._analysis_scope(source, scope, tables, snapshot)
        schema_dict = schema_dict_from_snapshot(snapshot, scoped)
        groups = split_fk_groups(schema_dict, max_tables)
        total = len(groups) + 2

        llm = self._llm_factory()
        outcomes = []
        for index, group in enumerate(groups):
            await ctx.progress(index, total, f"구조 분석 묶음 {index + 1}/{len(groups)}")
            group_schema = schema_dict_from_snapshot(snapshot, group)
            outcomes.append(await analyze_structure(llm, group_schema))
        merged, errors = merge_outcomes(outcomes)
        llm_calls = {"structure": len(groups), "samples": 0}
        base_result = {
            "source": source, "scope": scope, "tables": scoped, "groups": len(groups),
            "provider": provider, "env": self.env, "llm_calls": llm_calls,
        }
        if merged.status == "failed" or merged.meta is None:
            logger.warning("DB 구조 분석 실패: source=%s, errors=%s", source, errors)
            return {
                **base_result, "analysis_status": "failed", "draft_id": None,
                "errors": errors or [merged.error or "분석 실패"],
            }

        meta: dict[str, Any] = dict(merged.meta)
        collected = await self._samples_and_code_values(
            source, llm, schema_dict, meta, snapshot,
            with_samples=merged.status == "ok", code_columns=code_columns,
            ctx=ctx, progress_at=(len(groups), total),
        )
        llm_calls["samples"] = collected["sample_llm_calls"]
        code_values = {
            str(i["key"]): i["values"] for i in collected["code_items"] if not i["error"]
        }
        if code_values:
            meta["code_values"] = code_values

        await ctx.progress(len(groups) + 1, total, "검증·초안 저장")
        draft = await self._save_structure_draft(
            source, meta=meta, record=record, collected=collected, scope=scope,
            tables=scoped, groups=groups, provider=provider, by=by,
            analysis_status=merged.status, analysis_errors=errors, llm_calls=llm_calls,
        )
        passed = draft["validation"]["passed"]
        await ctx.progress(total, total, "완료")
        logger.info(
            "DB 구조 분석 초안: source=%s, draft_id=%s, status=%s, passed=%s, groups=%d, by=%s",
            source, draft.get("draft_id"), merged.status, passed, len(groups), by,
        )
        return {
            **base_result,
            "analysis_status": merged.status,
            "draft_id": draft.get("draft_id"),
            "validation_passed": passed,
            "errors": errors,
        }

    # --- 구조 분석·레거시 초안 공통 ---

    async def _ensure_snapshot(
        self, source: str, by: str | None, ctx: JobContext
    ) -> dict[str, Any]:
        """마지막 스냅샷 기록을 돌려준다 — 없으면 MCP에서 1회 수집해 저장한다."""
        record = await self._store.load_snapshot(source)
        if record is None:
            await ctx.progress(0, 1, "스냅샷 수집")
            async with self._client_factory(source) as client:
                _schema_dict, record, _previous = await self._collect_snapshot(client, source, by)
            await self._store.save_snapshot(source, record)
        return record

    async def _samples_and_code_values(
        self,
        source: str,
        llm: Any,
        schema_dict: dict[str, Any],
        meta: dict[str, Any],
        snapshot: Mapping[str, Any],
        *,
        with_samples: bool,
        code_columns: list[str] | None,
        ctx: JobContext,
        progress_at: tuple[int, int],
    ) -> dict[str, Any]:
        """샘플 SQL 생성·실행(패턴이 있을 때 LLM 1회)과 관리자 지정 코드 컬럼 값 수집(G-10).

        둘 다 필요 없으면 DB에 연결하지 않는다.

        Returns:
            ``{"attempts", "samples", "sample_error", "code_items", "sample_llm_calls"}``
        """
        collected: dict[str, Any] = {
            "attempts": [], "samples": {}, "sample_error": None, "code_items": [],
            "sample_llm_calls": 0,
        }
        if not (with_samples or code_columns):
            return collected
        async with self._client_factory(source) as client:
            if with_samples:
                await ctx.progress(*progress_at, "샘플 SQL 생성·실행")
                collection = await generate_structure_samples(llm, client, schema_dict, meta)
                collected.update(
                    attempts=collection.attempts, samples=collection.samples,
                    sample_error=collection.error, sample_llm_calls=1,
                )
            if code_columns:
                engine, db_schema = await self._engine_and_schema(source, client)
                collected["code_items"] = await collect_code_values(
                    client, admin_code_targets(code_columns), snapshot=snapshot,
                    engine=engine, db_schema=db_schema, limit=CODE_VALUE_LIMIT,
                )
        return collected

    async def _save_structure_draft(
        self,
        source: str,
        *,
        meta: dict[str, Any],
        record: Mapping[str, Any],
        collected: Mapping[str, Any],
        scope: str,
        tables: list[str],
        groups: list[list[str]],
        provider: dict[str, str],
        by: str | None,
        analysis_status: str,
        analysis_errors: list[str],
        llm_calls: dict[str, int],
    ) -> dict[str, Any]:
        """결정적 검증 4종 → 현행 프로필과 필드 단위 병합(G-4 (c)) → 초안 저장.

        Returns:
            저장된 초안
        """
        attempts = collected["attempts"]
        validation = validate_structure_draft(
            meta, record["snapshot"], attempts, analysis_errors=analysis_errors
        )
        current = self._store.read_current_profile(source)
        base = current.get("profile") if current else None
        merged_profile = merge_profile(base, meta, local_sandbox=self.local_sandbox)
        return await self._store.add_draft(source, {
            "meta": meta,
            "merged_profile": merged_profile,
            "field_diff": profile_field_diff(base, merged_profile),
            "validation": validation,
            "samples": collected["samples"],
            "sample_attempts": attempts,
            "sample_error": collected["sample_error"],
            "code_value_results": collected["code_items"],
            "scope": scope,
            "tables": tables,
            "groups": groups,
            "provider": provider,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "created_by": by,
            "analysis_status": analysis_status,
            "analysis_errors": analysis_errors,
            "llm_calls": llm_calls,
            "source_snapshot_hash": record.get("hash"),
            "base_sha256": current.get("sha256") if current else None,
            "comment_lines_in_current": count_comment_lines(
                current.get("content") if current else None
            ),
        })

    # --- 레거시 분석본 → 초안 ---

    async def legacy_structure_meta(self, source: str) -> dict[str, Any]:
        """레거시 분석본 후보의 원시 구조 정보를 돌려준다(API가 잡 시작 전 확인에도 쓴다).

        Raises:
            ValueError: 소스 이름 형식 오류
            StructureStoreUnavailable: Redis 미연결(원시 값을 확인할 수 없다)
            DraftNotApprovable: 후보가 아님(code=legacy_not_found)
        """
        validate_db_id(source)
        await self._require_store()
        legacy = await self._legacy_meta(
            source,
            profile_source=self._store.profile_state(source).get("source"),
            has_approved=self._store.has_approved_version(source),
        )
        if legacy is None:
            raise DraftNotApprovable(
                "legacy_not_found",
                f"{source}에 승인 대기 레거시 분석본이 없습니다"
                "(승인 버전이 있거나 · 수동 프로필이거나 · Redis 구조 정보가 없음)",
            )
        return legacy

    async def run_legacy_draft(
        self, source: str, *, by: str | None, ctx: JobContext
    ) -> dict[str, Any]:
        """레거시 분석본(승인 버전 없는 Redis `structure_meta`)으로 구조 초안을 만든다(잡 본문).

        LLM 구조 분석은 하지 않고 레거시 `patterns`·`query_guide`를 분석 결과로 쓴다. 나머지는
        구조 분석과 같은 함수를 쓴다 — 스냅샷(없으면 MCP 수집) · 패턴이 있으면 샘플 SQL을 새로
        생성(LLM 1회)·실행 · 결정적 검증 4종 · 현행 프로필과 필드 단위 병합 · 초안 저장
        (`scope="legacy"`). 승인·버전화는 일반 초안과 같은 `approve_draft`다. 레거시 Redis 키는
        지우지 않는다(승인하면 적용본 캐시가 그 키를 덮는다).

        Raises:
            ValueError: 소스 이름 형식 오류
            StructureStoreUnavailable: Redis 미연결
            DraftNotApprovable: 레거시 분석본 후보가 아님(code=legacy_not_found)
        """
        legacy = await self.legacy_structure_meta(source)
        provider = self.provider_info()
        total = 3
        record = await self._ensure_snapshot(source, by, ctx)
        snapshot = record["snapshot"]
        tables = list(snapshot.get("tables") or {})
        patterns = legacy.get("patterns")
        guide = legacy.get("query_guide")
        meta: dict[str, Any] = {
            "patterns": list(patterns) if isinstance(patterns, list) else [],
            "query_guide": guide if isinstance(guide, str) else "",
        }
        status = "ok" if meta["patterns"] else "no_patterns"
        llm_calls = {"structure": 0, "samples": 0}

        collected = await self._samples_and_code_values(
            source, self._llm_factory() if status == "ok" else None,
            schema_dict_from_snapshot(snapshot), meta, snapshot,
            with_samples=status == "ok", code_columns=None, ctx=ctx, progress_at=(1, total),
        )
        llm_calls["samples"] = collected["sample_llm_calls"]

        await ctx.progress(2, total, "검증·초안 저장")
        draft = await self._save_structure_draft(
            source, meta=meta, record=record, collected=collected, scope="legacy",
            tables=tables, groups=[], provider=provider, by=by,
            analysis_status=status, analysis_errors=[], llm_calls=llm_calls,
        )
        passed = draft["validation"]["passed"]
        await ctx.progress(total, total, "완료")
        logger.info(
            "DB 구조 레거시 분석본 초안: source=%s, draft_id=%s, status=%s, passed=%s, by=%s",
            source, draft.get("draft_id"), status, passed, by,
        )
        return {
            "source": source, "scope": "legacy", "tables": tables, "groups": 0,
            "provider": provider, "env": self.env, "llm_calls": llm_calls,
            "analysis_status": status,
            "draft_id": draft.get("draft_id"),
            "validation_passed": passed,
            "errors": [],
            "legacy_meta": _legacy_meta_summary(legacy),
        }

    # --- 승인·반려·되돌리기 ---

    async def _pending_draft(self, source: str, draft_id: str) -> dict[str, Any]:
        draft = await self._store.get_draft(source, draft_id)
        if draft is None:
            raise DraftNotApprovable("not_found", f"초안이 없습니다: {draft_id}")
        if draft.get("status") != "pending":
            raise DraftNotApprovable(
                "not_pending", f"대기 중인 초안이 아닙니다(status={draft.get('status')})"
            )
        return draft

    async def approve_draft(
        self, source: str, draft_id: str, *, by: str | None, reason: str
    ) -> dict[str, Any]:
        """초안을 승인해 프로필에 적용한다(A-6 · G-4 (c) — 수동 프로필 DB도 허용).

        승인 시점의 현행 프로필로 병합을 다시 계산한다(초안 생성 뒤 파일이 바뀌었을 수 있다).

        Raises:
            DraftNotApprovable: 초안 없음 · pending 아님 · 검증 실패 · 다른 env에서 만든 초안
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        draft = await self._pending_draft(source, draft_id)
        if not (draft.get("validation") or {}).get("passed"):
            raise DraftNotApprovable(
                "validation_failed", "결정적 검증을 통과하지 못한 초안은 승인할 수 없습니다"
            )
        if _norm(draft.get("env")) != _norm(self.env):
            raise DraftNotApprovable(
                "env_mismatch",
                f"다른 환경에서 만든 초안입니다(초안 env={draft.get('env')} · 현재 env={self.env})",
            )
        current = self._store.read_current_profile(source)
        base = current.get("profile") if current else None
        merged_profile = merge_profile(
            base, draft.get("meta") or {}, local_sandbox=self.local_sandbox
        )
        field_diff = profile_field_diff(base, merged_profile)
        version = await self._store.apply_profile(
            source, merged_profile, by=by, reason=reason, env=self.env,
            draft_id=draft_id, field_diff=field_diff,
        )
        await self._store.update_draft(
            source, draft_id, status="approved", approved_by=by, approved_at=_now_iso(),
            approve_reason=reason, approved_ver=version.get("ver"),
        )
        logger.info(
            "DB 구조 초안 승인: source=%s, draft_id=%s, ver=%s, by=%s",
            source, draft_id, version.get("ver"), by,
        )
        return {
            "source": source,
            "draft_id": draft_id,
            "version": _version_meta(version),
            "field_diff": field_diff,
            "recomputed": field_diff != draft.get("field_diff"),
        }

    async def reject_draft(
        self, source: str, draft_id: str, *, by: str | None, reason: str
    ) -> dict[str, Any]:
        """초안을 반려한다(프로필 변경 없음).

        Raises:
            DraftNotApprovable: 초안 없음 · pending 아님
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        await self._pending_draft(source, draft_id)
        updated = await self._store.update_draft(
            source, draft_id, status="rejected", rejected_by=by, rejected_at=_now_iso(),
            reject_reason=reason,
        )
        logger.info("DB 구조 초안 반려: source=%s, draft_id=%s, by=%s", source, draft_id, by)
        return {"source": source, "draft_id": draft_id, "status": (updated or {}).get("status")}

    async def rollback(
        self, source: str, ver: int, *, by: str | None, reason: str
    ) -> dict[str, Any]:
        """버전 원문으로 프로필을 되돌린다(새 버전 kind=rollback).

        Raises:
            DraftNotApprovable: 그 버전이 없음(code=version_not_found)
            StructureStoreUnavailable: 저장소가 요구하는 Redis 미연결
        """
        validate_db_id(source)
        versions = await self._store.list_versions(source)
        if not any(v.get("ver") == ver for v in versions):
            raise DraftNotApprovable("version_not_found", f"버전이 없습니다: v{ver}")
        version = await self._store.rollback(source, ver, by=by, reason=reason, env=self.env)
        logger.info(
            "DB 구조 되돌리기: source=%s, to=v%s, new=v%s, by=%s",
            source, ver, version.get("ver"), by,
        )
        return {"source": source, "rolled_back_to": ver, "version": _version_meta(version)}

    async def diff_report(self, source: str, draft_id: str) -> str:
        """초안 차이 보고서(YAML) — 지금 승인하면 현행 프로필이 어떻게 바뀌는지.

        Raises:
            DraftNotApprovable: 초안 없음(code=not_found)
        """
        validate_db_id(source)
        draft = await self._store.get_draft(source, draft_id)
        if draft is None:
            raise DraftNotApprovable("not_found", f"초안이 없습니다: {draft_id}")
        current = self._store.read_current_profile(source)
        base = current.get("profile") if current else None
        draft_local = bool(draft.get("local_sandbox")) or is_local_sandbox_env(
            str(draft.get("env") or "")
        )
        merged_profile = merge_profile(
            base, draft.get("meta") or {}, local_sandbox=self.local_sandbox
        )
        comment_lines = count_comment_lines(current.get("content") if current else None)
        header = []
        if draft_local or self.local_sandbox:
            header.append(LOCAL_SANDBOX_HEADER)
        header.extend([
            "# plans/104 구조 초안 차이 보고서 — 지금 승인하면 현행 프로필이 이렇게 바뀐다",
            f"# 소스={source} · 초안={draft_id}",
            f"# 초안 env={draft.get('env')} · 현재 env={self.env}",
            f"# 생성={draft.get('created_at')} · 작성={draft.get('created_by')}",
            f"# 보고서={_now_iso()}",
        ])
        if comment_lines:
            header.append(
                f"# 경고: 현행 프로필 주석 {comment_lines}줄은 승인 적용 시 사라진다"
                "(적용 직전 원문은 버전으로 보관되어 되돌리기로 복원된다)"
            )
        body = {
            "source": source,
            "draft_id": draft_id,
            "draft_status": draft.get("status"),
            "draft_env": draft.get("env"),
            "current_env": self.env,
            "local_sandbox": draft_local or self.local_sandbox,
            "analysis_status": draft.get("analysis_status"),
            "validation_passed": bool((draft.get("validation") or {}).get("passed")),
            "base_changed_since_draft": (
                (current.get("sha256") if current else None) != draft.get("base_sha256")
            ),
            "comment_lines_dropped": comment_lines,
            "field_diff": profile_field_diff(base, merged_profile),
        }
        dumped = str(yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
        return "\n".join(header) + "\n" + dumped


def _version_meta(version: Mapping[str, Any]) -> dict[str, Any]:
    """버전 항목에서 원문·프로필 본문을 뺀 메타."""
    return {k: v for k, v in version.items() if k not in ("content", "profile")}


def _last_check_summary(check: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """목록 배지용 마지막 점검 요약."""
    if not check:
        return None
    return {
        "at": check.get("at"),
        "summary": check.get("summary"),
        "reanalysis_required": bool(check.get("reanalysis_required")),
        "new_code_values": int(check.get("new_code_value_count") or 0),
    }
