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

공통 기반(`AdminServiceBase`)은 등록 서비스(`db_registration_service`)와 함께 쓴다 — 클라이언트·LLM·
레지스트리·저장소 주입, 구조 상태, 준비도 입력 수집, 코드값 수집.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from src.dbhub.models import SchemaInfo, schema_to_dict
from src.domain.db_readiness import (
    ReadinessInputs,
    ReadinessReport,
    evaluate_readiness,
    is_local_sandbox_env,
)
from src.domain.profile_merge import merge_profile, profile_field_diff
from src.domain.schema_snapshot import (
    bare_name,
    build_snapshot,
    diff_snapshots,
    structure_impact,
)
from src.schema_cache.structure_analysis import (
    analyze_structure,
    generate_structure_samples,
    merge_outcomes,
    split_fk_groups,
    validate_structure_draft,
)
from src.schema_cache.structure_store import (
    StructureStore,
    StructureStoreUnavailable,
    count_comment_lines,
    validate_db_id,
)
from src.schema_cache.value_index import _extract_value, build_distinct_values_sql
from src.security.sql_guard import SQLGuard
from src.utils.sql_dialect import is_db2

if TYPE_CHECKING:
    from src.config import AppConfig
    from src.schema_cache.admin_jobs import JobContext
    from src.schema_cache.cache_manager import SchemaCacheManager

logger = logging.getLogger(__name__)

#: 코드값 DISTINCT 수집 상한(A-4 · G-10) — 값 목록이 이보다 많으면 truncated로 표시한다
CODE_VALUE_LIMIT = 500
#: 소스별 연결 확인 타임아웃(초) — 목록을 열 때 병렬로 부른다
HEALTH_TIMEOUT_SECONDS = 5.0
#: 구조 분석 범위
ANALYZE_SCOPES: tuple[str, ...] = ("all", "changed", "tables")
#: 구조 상태 판정에서 "승인본"으로 보는 버전 종류
APPROVED_VERSION_KINDS: tuple[str, ...] = ("approved", "rollback")
#: 로컬 샌드박스 산출물 머리말(R7 · `plans/95` §4.6.2)
LOCAL_SANDBOX_HEADER = "# LOCAL SANDBOX — 운영 정본 재료로 쓰지 말 것"

# SQL에 넣는 식별자(스키마·테이블·컬럼 한 조각) — 따옴표·공백·구분자를 허용하지 않는다
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]*$")
_DB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

ClientFactory = Callable[[str | None], AbstractAsyncContextManager[Any]]
LLMFactory = Callable[[], Any]
RegistryGetter = Callable[[], Any]


class DraftNotApprovable(ValueError):  # noqa: N818 - 계약 확정 이름
    """초안 승인·반려·보고서나 버전 되돌리기를 할 수 없는 상태(API 409).

    Attributes:
        code: `not_found` · `not_pending` · `validation_failed` · `env_mismatch` ·
            `version_not_found` · `legacy_not_found`(레거시 분석본 후보 아님)
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _now_iso() -> str:
    """현재 시각(로컬 시간대 오프셋 포함 ISO 8601, 초 단위)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    """엔진·env 비교용 정규화(앞뒤 공백 · 끝 `/` 제거 · 소문자)."""
    return str(value or "").strip().rstrip("/").lower()


def _is_valid_source(name: str) -> bool:
    """저장소 키·파일 경로에 쓸 수 있는 소스 이름인지."""
    return bool(_DB_ID_RE.fullmatch(str(name or "")))


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


def resolve_table(tables: Mapping[str, Any] | Sequence[str], name: str) -> str | None:
    """테이블 이름을 스키마 키로 맞춘다(정확 일치 → 대소문자·스키마 접두 무시 유일 일치)."""
    keys = list(tables)
    if name in keys:
        return name
    matches = [k for k in keys if bare_name(k) == bare_name(name)]
    return matches[0] if len(matches) == 1 else None


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

    식별자는 조각마다 `^[A-Za-z_][A-Za-z0-9_$#]*$`여야 한다. DB2는 테이블 키에 스키마가 없으면
    레지스트리 `db_schema`(없으면 스냅샷 테이블 스키마)로 한정한다.

    Raises:
        ValueError: 식별자 형식 오류 · `SQLGuard.is_safe_select` 불통과
    """
    for part in [*table_key.split("."), column]:
        if not _IDENT_RE.fullmatch(part):
            raise ValueError(f"허용되지 않는 식별자: {part!r}")
    table_ref = table_key
    if is_db2(engine) and "." not in table_key:
        schema = (db_schema or table_schema or "").strip()
        if schema:
            if not _IDENT_RE.fullmatch(schema):
                raise ValueError(f"허용되지 않는 스키마 식별자: {schema!r}")
            table_ref = f"{schema}.{table_key}"
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


# ──────────────────────────────────────────────
# 공통 기반
# ──────────────────────────────────────────────


class AdminServiceBase:
    """관리자 DB 구조·등록 서비스 공통 기반 — 의존성 주입과 상태 조회."""

    def __init__(
        self,
        config: AppConfig,
        cache_mgr: SchemaCacheManager | None = None,
        *,
        client_factory: ClientFactory | None = None,
        llm_factory: LLMFactory | None = None,
        registry_getter: RegistryGetter | None = None,
        store: StructureStore | None = None,
    ) -> None:
        """서비스를 만든다.

        설정 값(동시성·묶음 상한 등)은 여기서 캐시하지 않고 잡 시작 시점에 읽는다 — API는 요청마다
        현재 `app.state.config`로 서비스를 만들면 설정 리로드가 반영된다.

        Args:
            config: 애플리케이션 설정
            cache_mgr: 스키마 캐시 매니저(없으면 싱글톤)
            client_factory: `source → async context manager(연결된 DB 클라이언트)`
                (기본 `get_db_client(config, db_id=source)`)
            llm_factory: `() → LLM`(기본 `create_llm(config)`)
            registry_getter: `() → DBRegistry`(기본 실행 중 레지스트리 `get_registry`)
            store: 구조 저장소(기본 `cache_mgr.structure_store`)
        """
        if cache_mgr is None:
            from src.schema_cache.cache_manager import get_cache_manager

            cache_mgr = get_cache_manager(config)
        self._config = config
        self._cache_mgr = cache_mgr
        self._client_factory: ClientFactory = client_factory or self._default_client
        self._llm_factory: LLMFactory = llm_factory or self._default_llm
        self._registry_getter: RegistryGetter = registry_getter or _default_registry
        self._store: StructureStore = store if store is not None else cache_mgr.structure_store

    # --- 기본 의존성 ---

    def _default_client(self, source: str | None) -> AbstractAsyncContextManager[Any]:
        from src.db import get_db_client

        return get_db_client(self._config, db_id=source)

    def _default_llm(self) -> Any:
        from src.llm import create_llm

        return create_llm(self._config)

    # --- 환경 ---

    @property
    def env(self) -> str:
        """기록 시점 실행 환경(`DBHUB_SERVER_URL`)."""
        return str(self._config.dbhub.server_url or "")

    @property
    def local_sandbox(self) -> bool:
        """현재 env가 로컬 샌드박스인지."""
        return is_local_sandbox_env(self.env)

    def provider_info(self) -> dict[str, str]:
        """워커 LLM provider와 그 provider가 실제로 쓰는 모델 이름."""
        llm = self._config.llm
        provider = str(llm.provider)
        if provider == "mlx":
            model = llm.mlx_model or "default_model"
        elif provider == "gemini":
            model = llm.gemini_model or llm.model
        elif provider == "fabrix":
            model = llm.fabrix_chat_model or llm.model
        else:
            model = llm.model
        return {"provider": provider, "model": str(model or "")}

    def _provider_label(self) -> str:
        info = self.provider_info()
        return f"{info['provider']}/{info['model']}"

    async def _require_store(self) -> None:
        """관리자 자산 쓰기 전 Redis 연결을 확인한다.

        Raises:
            StructureStoreUnavailable: Redis 미연결
        """
        redis_cache = self._store.redis_cache
        if redis_cache is None or not await redis_cache.ensure_connected():
            raise StructureStoreUnavailable(
                "Redis에 연결할 수 없어 관리자 DB 구조 자산을 저장할 수 없습니다"
            )

    # --- 레지스트리·활성 목록 ---

    def _registry(self) -> Any | None:
        try:
            return self._registry_getter()
        except Exception as e:  # noqa: BLE001 — 레지스트리 로드 실패는 미등록으로 보고 사유를 남긴다
            logger.warning("DB 레지스트리 조회 실패 — 미등록으로 표시합니다: %s", e)
            return None

    @staticmethod
    def _registry_entry_dict(entry: Any) -> dict[str, Any] | None:
        if entry is None:
            return None
        return {
            "db_id": entry.db_id,
            "enabled": bool(getattr(entry, "enabled", True)),
            "engine": entry.engine,
            "description": entry.description,
            "db_schema": entry.db_schema,
            "zone": entry.zone,
        }

    def _active_db_ids(self) -> list[str]:
        return list(self._config.multi_db.get_active_db_ids())

    async def _engine_and_schema(self, source: str, client: Any | None) -> tuple[str, str]:
        """엔진과 DB2 스키마 한정값 — 레지스트리 우선, 미등록이면 MCP 소스 type."""
        registry = self._registry()
        entry = registry.get(source) if registry is not None else None
        if entry is not None:
            return str(entry.engine or ""), str(entry.db_schema or "")
        engine = ""
        if client is not None and hasattr(client, "list_sources"):
            try:
                for row in await client.list_sources():
                    if row.get("name") == source:
                        engine = str(row.get("type") or "")
                        break
            except Exception as e:  # noqa: BLE001 — 엔진을 모르면 방언 기본(LIMIT)으로 간다
                logger.warning("MCP 소스 type 조회 실패 (source=%s): %s", source, e)
        return engine, ""

    # --- MCP 보기 ---

    def _probe_source_name(self) -> str:
        """소스 목록 조회용 연결에 쓸 소스 이름(목록·연결 확인 도구는 소스를 인자로 받는다)."""
        configured = str(self._config.dbhub.source_name or "").strip()
        if configured:
            return configured
        registry = self._registry()
        candidates = [*self._active_db_ids(), *(registry.db_ids() if registry else ())]
        return candidates[0] if candidates else "_admin"

    @staticmethod
    async def _health(client: Any, source: str) -> dict[str, Any]:
        try:
            detail = await asyncio.wait_for(
                client.health_check_detail(source), timeout=HEALTH_TIMEOUT_SECONDS
            )
            return dict(detail)
        except TimeoutError:
            error = f"응답 없음({HEALTH_TIMEOUT_SECONDS:g}초 초과)"
        except Exception as e:  # noqa: BLE001 — 소스별 실패는 사유로 돌려준다
            error = f"{type(e).__name__}: {e}"
        return {"source": source, "healthy": False, "latency_ms": None, "error": error}

    async def _mcp_view(self, health_for: Sequence[str] | None = None) -> dict[str, Any]:
        """MCP 소스 목록과 연결 상태를 한 번에 모은다.

        Args:
            health_for: 연결 확인할 소스(없으면 MCP 목록 전부) — 목록에 없는 이름은 확인하지 않는다

        Returns:
            ``{"available": bool, "error": str|None, "order": [name], "sources": {name: row},
            "health": {name: {source, healthy, latency_ms, error}}}`` —
            `readiness(mcp_rows=)`에 그대로 넘길 수 있다
        """
        view: dict[str, Any] = {
            "available": False, "error": None, "order": [], "sources": {}, "health": {},
        }
        try:
            async with self._client_factory(self._probe_source_name()) as client:
                if not hasattr(client, "list_sources"):
                    view["error"] = (
                        "MCP 클라이언트가 아니어서(DB_BACKEND=direct) "
                        "소스 목록을 조회할 수 없습니다"
                    )
                    return view
                rows = await client.list_sources()
                view["available"] = True
                for row in rows:
                    name = str(row.get("name") or "")
                    if name and name not in view["sources"]:
                        view["order"].append(name)
                        view["sources"][name] = dict(row)
                targets = [
                    name for name in (health_for if health_for is not None else view["order"])
                    if name in view["sources"]
                ]
                details = await asyncio.gather(*(self._health(client, n) for n in targets))
                view["health"] = dict(zip(targets, details))
        except Exception as e:  # noqa: BLE001 — MCP 미가용은 "확인 못 함"으로 보인다
            view["available"] = False
            view["error"] = f"{type(e).__name__}: {e}"
            logger.warning("MCP 소스 목록 조회 실패: %s", view["error"])
        return view

    # --- 구조 상태 ---

    async def _structure_state(self, source: str) -> dict[str, Any]:
        """프로필 파일·버전 이력으로 구조 상태를 판정한다(수동 프로필 · 승인본 · 없음).

        `legacy_candidate`: 승인 버전 없는 Redis `structure_meta`(레거시 분석본)가 있어 관리자
        승인이 필요한지 · `legacy_meta`: 그 요약(`_legacy_meta_summary` — 후보가 아니면 None).
        """
        state = dict(self._store.profile_state(source))
        versions = await self._store.list_versions(source)
        approved = [v for v in versions if v.get("kind") in APPROVED_VERSION_KINDS]
        if state.get("exists") and state.get("source") == "manual":
            status = "approved" if approved else "manual"
        else:
            status = "none"
        legacy = await self._legacy_meta(
            source, profile_source=state.get("source"), has_approved=bool(approved)
        )
        return {
            "status": status,
            "latest_ver": state.get("latest_ver"),
            "latest_kind": state.get("latest_kind"),
            "drift": bool(state.get("drift")),
            "environment": state.get("environment"),
            "approved_env": approved[-1].get("env") if approved else None,
            "legacy_candidate": legacy is not None,
            "legacy_meta": _legacy_meta_summary(legacy),
        }

    async def _legacy_meta(
        self, source: str, *, profile_source: str | None, has_approved: bool
    ) -> dict[str, Any] | None:
        """레거시 분석본 후보의 Redis `structure_meta` 원시 값(후보가 아니면 None).

        후보 = 승인 버전(approved·rollback) 없음 · 프로필 파일이 수동 프로필(`source: manual`)이
        아님 · Redis `structure_meta`가 비어 있지 않은 dict. 이 값은 질의 경로·전역 등록 판정이
        적용본으로 보지 않는다(2026-09-17 사용자 확정) — 관리자가 초안으로 만들어 승인해야 쓰인다.
        원시 조회 실패는 WARNING 후 후보 아님으로 본다.
        """
        if has_approved or profile_source == "manual":
            return None
        try:
            raw = await self._cache_mgr.get_structure_meta(source)
        except Exception as e:  # noqa: BLE001 — 조회 실패는 사유를 남기고 후보 아님으로 본다
            logger.warning("레거시 구조 정보 조회 실패 (source=%s): %s", source, e)
            return None
        return dict(raw) if isinstance(raw, Mapping) and raw else None

    async def _current_structure(self, source: str) -> dict[str, Any] | None:
        """질의 경로와 같은 우선순위의 현행 구조 정보(수동 프로필 → 적용본 캐시)."""
        current = self._store.read_current_profile(source)
        profile = current.get("profile") if current else None
        if current and current.get("source") == "manual" and isinstance(profile, Mapping):
            return {k: v for k, v in profile.items() if k not in ("source", "environment")}
        return await self._cache_mgr.get_applied_structure_meta(source)

    # --- 준비도 ---

    async def _default_description_scope(
        self, source: str, table_names: Sequence[str]
    ) -> tuple[list[str], str]:
        """설명 범위 기본값 — 현행 프로필 `allowed_tables`(수집 테이블과 일치하는 것) 또는 전체."""
        current = self._store.read_current_profile(source)
        profile = current.get("profile") if current else None
        allowed = profile.get("allowed_tables") if isinstance(profile, Mapping) else None
        if isinstance(allowed, list) and allowed:
            scoped: list[str] = []
            for name in allowed:
                key = resolve_table(table_names, str(name))
                if key is not None and key not in scoped:
                    scoped.append(key)
            if scoped:
                return scoped, "profile_allowed_tables"
        return list(table_names), "all"

    async def _description_coverage(
        self,
        source: str,
        registration: Mapping[str, Any],
        snapshot_record: Mapping[str, Any] | None,
    ) -> tuple[int, int, int]:
        """C8 입력 — (범위 테이블 수, 설명이 실제로 있는 범위 테이블 수, 명시 제외 테이블 수).

        적용 건수는 기록이 아니라 캐시의 실제 설명 키에서 센다. 범위 기록이 없으면 스냅샷 테이블로
        기본 범위를 계산한다.
        """
        detail = (registration.get("descriptions") or {}).get("detail") or {}
        scope = [str(t) for t in detail.get("scope_table_names") or []]
        if not scope and snapshot_record:
            tables = list((snapshot_record.get("snapshot") or {}).get("tables") or {})
            if tables:
                scope, _ = await self._default_description_scope(source, tables)
        if not scope:
            return 0, 0, 0
        descriptions = await self._cache_mgr.get_descriptions(source)
        described = {key.rsplit(".", 1)[0] for key in descriptions if "." in key}
        applied = [t for t in scope if t in described]
        excluded = [
            t for t in detail.get("excluded_table_names") or []
            if t in scope and t not in described
        ]
        return len(scope), len(applied), len(excluded)

    async def _llm_synonym_columns(self, source: str) -> int:
        """출처 `llm` 단어를 가진 유사어 컬럼 수(Redis 실측 · 미연결이면 0)."""
        redis_cache = self._store.redis_cache
        if redis_cache is None or not await redis_cache.ensure_connected():
            return 0
        entries = await redis_cache.load_synonyms_with_sources(source)
        return sum(
            1 for entry in entries.values()
            if "llm" in ((entry or {}).get("sources") or {}).values()
        )

    async def _readiness_for(
        self,
        source: str,
        view: Mapping[str, Any],
        *,
        structure: Mapping[str, Any] | None = None,
        registration: Mapping[str, Any] | None = None,
        snapshot_record: Mapping[str, Any] | None = None,
    ) -> ReadinessReport:
        """준비도 C1~C10 입력을 모아 판정한다(이미 읽은 값은 인자로 받아 중복 조회를 피한다)."""
        registry = self._registry()
        entry = self._registry_entry_dict(registry.get(source) if registry else None)
        mcp_row = (view.get("sources") or {}).get(source)
        health = (view.get("health") or {}).get(source)
        status = await self._cache_mgr.get_status(source)

        valid = _is_valid_source(source)
        if valid:
            if structure is None:
                structure = await self._structure_state(source)
            if registration is None:
                registration = await self._store.load_registration(source)
            if snapshot_record is None:
                snapshot_record = await self._store.load_snapshot(source)
        registration = registration or {}
        snapshot = (snapshot_record or {}).get("snapshot") or {}
        scope, applied, excluded = (
            await self._description_coverage(source, registration, snapshot_record)
            if valid else (0, 0, 0)
        )
        seeds = registration.get("seeds") or {}
        structure_status = (structure or {}).get("status")
        inputs = ReadinessInputs(
            db_id=source,
            mcp_available=bool(view.get("available")),
            mcp_listed=mcp_row is not None,
            mcp_type=(mcp_row or {}).get("type") or None,
            mcp_health=health.get("healthy") if isinstance(health, Mapping) else None,
            registry_entry=entry,
            cache_exists=status.backend != "none",
            cache_table_count=int(status.table_count or 0),
            snapshot_table_count=(
                int(snapshot.get("table_count", len(snapshot.get("tables") or {})))
                if snapshot else None
            ),
            cache_env=(registration.get("schema") or {}).get("env"),
            structure_source=(
                structure_status if structure_status in ("manual", "approved") else None
            ),
            approved_env=(structure or {}).get("approved_env"),
            current_env=self.env,
            description_scope_tables=scope,
            description_applied_tables=applied,
            description_excluded_tables=excluded,
            seed_loaded_count=(
                int(seeds.get("count") or 0) if seeds.get("status") in ("ok", "warning") else 0
            ),
            llm_synonym_applied_count=await self._llm_synonym_columns(source) if valid else 0,
            default_allowed_db_ids=tuple(
                s.strip() for s in str(self._config.auth.default_allowed_db_ids or "").split(",")
                if s.strip()
            ),
        )
        return evaluate_readiness(inputs)


def _default_registry() -> Any:
    from src.routing.registry import get_registry

    return get_registry()


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


def _legacy_meta_summary(meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """레거시 분석본 요약(화면용) — 패턴 수 · query_guide 길이 · samples 유무. 값이 없으면 None."""
    if not meta:
        return None
    patterns = meta.get("patterns")
    guide = meta.get("query_guide")
    return {
        "pattern_count": len(patterns) if isinstance(patterns, list) else 0,
        "query_guide_length": len(guide) if isinstance(guide, str) else 0,
        "has_samples": bool(meta.get("samples")),
    }


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
