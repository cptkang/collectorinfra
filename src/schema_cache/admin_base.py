"""관리자 DB 구조·등록·자산 서비스 공통 기반 — `AdminServiceBase`.

`db_structure_service`(DB 구조) · `db_registration_service`(등록) · `asset_generation_service`(자산
자동 생성)가 함께 쓴다 — 클라이언트·LLM·레지스트리·저장소 주입, 구조 상태, 준비도 입력 수집.
구현 파일(`db_structure_service`)에서 옮겨 왔다(plans/166 I3) — 옛 경로에서도 재노출한다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from src.domain.db_readiness import (
    ReadinessInputs,
    ReadinessReport,
    evaluate_readiness,
    is_local_sandbox_env,
)
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import PROFILE_KEY as TABLE_DEFINITIONS_KEY
from src.domain.table_definitions import defined_table_count
from src.schema_cache.structure_store import StructureStore, StructureStoreUnavailable

if TYPE_CHECKING:
    from src.config import AppConfig
    from src.schema_cache.cache_manager import SchemaCacheManager

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I3)
logger = logging.getLogger("src.schema_cache.db_structure_service")

#: 소스별 연결 확인 타임아웃(초) — 목록을 열 때 병렬로 부른다
HEALTH_TIMEOUT_SECONDS = 5.0
#: 구조 상태 판정에서 "승인본"으로 보는 버전 종류
APPROVED_VERSION_KINDS: tuple[str, ...] = ("approved", "rollback")

_DB_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")

ClientFactory = Callable[[str | None], AbstractAsyncContextManager[Any]]
LLMFactory = Callable[[], Any]
RegistryGetter = Callable[[], Any]


def _is_valid_source(name: str) -> bool:
    """저장소 키·파일 경로에 쓸 수 있는 소스 이름인지."""
    return bool(_DB_ID_RE.fullmatch(str(name or "")))


def resolve_table(tables: Mapping[str, Any] | Sequence[str], name: str) -> str | None:
    """테이블 이름을 스키마 키로 맞춘다(정확 일치 → 대소문자·스키마 접두 무시 유일 일치)."""
    keys = list(tables)
    if name in keys:
        return name
    matches = [k for k in keys if bare_name(k) == bare_name(name)]
    return matches[0] if len(matches) == 1 else None


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
        elif provider == "vllm":
            model = llm.vllm_model
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
        """준비도 C1~C11 입력을 모아 판정한다(이미 읽은 값은 인자로 받아 중복 조회를 피한다)."""
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
        current = self._store.read_current_profile(source) if valid else None
        profile = (current or {}).get("profile") or {}
        allowed_raw = profile.get("allowed_tables")
        allowed = [t for t in allowed_raw if isinstance(t, str)] if isinstance(
            allowed_raw, list
        ) else []
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
            allowed_table_count=len(allowed),
            defined_allowed_count=defined_table_count(profile.get(TABLE_DEFINITIONS_KEY), allowed),
        )
        return evaluate_readiness(inputs)


def _default_registry() -> Any:
    from src.routing.registry import get_registry

    return get_registry()


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
