"""관리자 신규 시스템 연동 서비스 — 등록 단계 · 준비도 · 설명 초안 적용 · 설정 조각.

plans/104 §3.8 · B-1·B-3~B-5.

「발견 → 스키마 수집·캐시 등록 → 설명·DB 설명 초안 → 적용 → 준비도 판정 → 활성화」 흐름의 서비스다.
새 등록 로직을 만들지 않고 기존 함수(`get_full_schema`·`save_schema`·`DescriptionGenerator`·
`SynonymLoader.load_seed_yaml`·값 인덱스)를 잡 하나로 묶는다.

- **등록 잡(`run_register`)**: 단계
  `probe → schema → descriptions → db_description → seeds → value_index` 중 요청한 것만
  이 순서로 실행하고, 단계마다 `schema:{db_id}:registration`에 상태를 남긴다(N2).
  한 단계의 실패는 그 단계에 사유로 기록하고 다음 단계로 간다(독립 신호 · 부분 결과 보존).
- **수집 1회 공유(§3.8.2)**: schema 단계의 `get_full_schema` 결과 하나로 스키마 캐시와 스냅샷을 함께
  만든다. 지문은 `save_schema(fingerprint=None)` — 지연 경로와 같은 저장 모양이고 지문 SQL을 부르지
  않아 DB2도 등록된다(N4).
- **설명 초안(G-8 (a))**: 테이블당 LLM 1회 · 동시성 상한 `SCHEMA_CACHE_ADMIN_LLM_CONCURRENCY` ·
  실패 테이블 목록. 적용은 테이블 단위 제외 후 저장 + 유사어 병합(S5) + 전역 동기화 +
  설명 백업(B-6).
- **설정 조각(R11)**: 레지스트리 항목 YAML 문자열만 돌려준다. 앱은 `config/db_registry.yaml`·`.env`·
  `mcp_server/`에 쓰지 않는다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]

from src.domain.db_readiness import ReadinessReport
from src.domain.schema_snapshot import bare_name, build_snapshot
from src.schema_cache.db_structure_service import (
    LOCAL_SANDBOX_HEADER,
    AdminServiceBase,
    DraftNotApprovable,
    _now_iso,
    collect_code_values,
    resolve_table,
    schema_dict_from_full_schema,
    schema_dict_from_snapshot,
)
from src.schema_cache.structure_analysis import split_fk_groups
from src.schema_cache.structure_store import StructureStoreUnavailable, validate_db_id
from src.schema_cache.value_index import derive_value_specs
from src.security.sql_guard import INJECTION_PATTERNS, SQLGuard

if TYPE_CHECKING:
    from src.schema_cache.admin_jobs import JobContext

logger = logging.getLogger(__name__)

#: 등록 단계 — 요청 순서와 무관하게 이 순서로 실행한다
REGISTER_STEPS: tuple[str, ...] = (
    "probe", "schema", "descriptions", "db_description", "seeds", "value_index",
)
STEP_LABELS: Mapping[str, str] = {
    "probe": "연결·엔진 확인",
    "schema": "스키마 수집·캐시 등록",
    "descriptions": "컬럼 설명·유사어 초안",
    "db_description": "DB 설명 초안",
    "seeds": "유사어 시드 로드",
    "value_index": "값 인덱스",
}
#: LLM을 부르는 단계(등록 상태에 provider를 남긴다)
LLM_STEPS: frozenset[str] = frozenset({"descriptions", "db_description"})
#: 앱이 지원하는 엔진(MCP 소스 type 기준)
SUPPORTED_ENGINES: tuple[str, ...] = ("postgresql", "db2", "mariadb")
#: 유사어 시드 배포 위치(읽기만 한다)
SEEDS_DIR = Path("config") / "synonym_seeds"
#: 값 인덱스 컬럼당 수집 상한 — 질의 경로 지연 빌드(`value_index` 기본 상한)와 같은 값
VALUE_INDEX_LIMIT = 1000

#: 엔진별 서버 변수 조회(O-1) — 코드 상수(입력 조립 없음)
SERVER_VARIABLE_SQL: Mapping[str, str] = {
    "postgresql": (
        "SELECT version() AS version, current_setting('server_encoding') AS server_encoding, "
        "current_setting('search_path') AS search_path"
    ),
    "db2": (
        "SELECT CURRENT SERVER AS current_server, CURRENT SCHEMA AS current_schema "
        "FROM SYSIBM.SYSDUMMY1"
    ),
    "mariadb": (
        "SELECT @@version AS version, @@sql_mode AS sql_mode, "
        "@@lower_case_table_names AS lower_case_table_names, "
        "@@collation_server AS collation_server"
    ),
}
_ENGINE_ALIASES: Mapping[str, str] = {"postgres": "postgresql", "mysql": "mariadb"}
# 서버 변수 조회는 시스템 변수 읽기가 목적이라 이 패턴만 예외로 둔다(나머지 안전성 검사는 그대로)
_SYSTEM_VARIABLE_PATTERN = r"@@\w+"


def _engine_key(engine: str | None) -> str:
    """엔진 이름 정규화(별칭 흡수)."""
    text = str(engine or "").strip().lower()
    return _ENGINE_ALIASES.get(text, text)


def assert_constant_select(sql: str) -> None:
    """코드 상수 조회문(서버 변수)의 안전성 검사.

    `SQLGuard`의 금지 키워드·인젝션 패턴 검사를 그대로 쓰되, 시스템 변수 접근 패턴(`@@`)만
    제외한다 — MariaDB 서버 변수 읽기가 이 조회의 목적이고, 문장은 입력 조립이 없는 모듈 상수다.

    Raises:
        ValueError: SELECT 단일 문이 아니거나 금지 키워드·위험 패턴이 있을 때
    """
    text = sql.strip()
    if not text.upper().startswith("SELECT") or ";" in text:
        raise ValueError("서버 변수 조회는 세미콜론 없는 단일 SELECT여야 합니다")
    guard = SQLGuard()
    forbidden = guard.detect_forbidden_keywords(text)
    if forbidden:
        raise ValueError(f"금지 키워드: {', '.join(forbidden)}")
    patterns = [p for p in INJECTION_PATTERNS if p != _SYSTEM_VARIABLE_PATTERN]
    if guard.detect_injection_patterns(text, patterns):
        raise ValueError("위험 패턴이 감지되었습니다")


@dataclass
class _StepOutcome:
    """등록 단계 1개의 결과."""

    status: str
    count: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class _RegisterRun:
    """등록 잡 1회의 공유 상태 — 지연 연결 클라이언트 · LLM · 이번 잡에서 수집한 스키마."""

    def __init__(
        self,
        service: DBRegistrationService,
        source: str,
        *,
        tables: list[str] | None,
        by: str | None,
        concurrency: int,
        stack: AsyncExitStack,
        ctx: JobContext,
    ) -> None:
        self.service = service
        self.source = source
        self.tables = tables
        self.by = by
        self.concurrency = concurrency
        self.ctx = ctx
        self.schema_dict: dict[str, Any] | None = None
        self.snapshot: dict[str, Any] | None = None
        self._stack = stack
        self._client: Any = None
        self._llm: Any = None

    async def client(self) -> Any:
        """연결된 DB 클라이언트(잡 안에서 1회 연결)."""
        if self._client is None:
            factory = self.service._client_factory(self.source)
            self._client = await self._stack.enter_async_context(factory)
        return self._client

    def llm(self) -> Any:
        """LLM(잡 안에서 1회 생성)."""
        if self._llm is None:
            self._llm = self.service._llm_factory()
        return self._llm


def _normalize_generated(
    table: str, columns: Sequence[Mapping[str, Any]], result: Any
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """LLM 설명 결과를 `"<테이블 키>.<실제 컬럼명>"` 키로 맞춘다(이 테이블에 없는 컬럼은 버린다)."""
    names = {str(c.get("name")).casefold(): str(c.get("name")) for c in columns if c.get("name")}
    descriptions: dict[str, str] = {}
    synonyms: dict[str, list[str]] = {}
    if not isinstance(result, Mapping):
        return descriptions, synonyms
    for raw_key, info in result.items():
        if not isinstance(info, Mapping):
            continue
        prefix, _, column = str(raw_key).rpartition(".")
        if prefix and bare_name(prefix) != bare_name(table):
            continue
        actual = names.get(column.casefold())
        if actual is None:
            continue
        key = f"{table}.{actual}"
        text = info.get("description")
        if isinstance(text, str) and text.strip():
            descriptions[key] = text.strip()
        words = [w.strip() for w in info.get("synonyms") or [] if isinstance(w, str) and w.strip()]
        if words:
            synonyms[key] = list(dict.fromkeys(words))
    return descriptions, synonyms


class DBRegistrationService(AdminServiceBase):
    """관리자 「신규 연동」 등록 단계·준비도·설명 초안·설정 조각 서비스."""

    # --- 조회 ---

    async def readiness(
        self, source: str, *, mcp_rows: dict[str, Any] | None = None
    ) -> ReadinessReport:
        """준비도 C1~C10(B-1) — 목록·설정 탭(B-7)이 공유한다.

        Args:
            source: 대상 DB(= MCP 소스명)
            mcp_rows: 이미 모은 MCP 보기(`_mcp_view` 반환 모양 — `{available, error, order,
                sources, health}`). 없으면 이 소스만 연결 확인한다

        Raises:
            ValueError: 소스 이름 형식 오류
        """
        validate_db_id(source)
        view = mcp_rows if mcp_rows is not None else await self._mcp_view(health_for=[source])
        return await self._readiness_for(source, view)

    async def readiness_many(self, db_ids: list[str]) -> dict[str, ReadinessReport]:
        """여러 DB의 준비도를 MCP 조회 1회로 판정한다(B-7 — 새로 추가된 `ACTIVE_DB_IDS`).

        이름 형식이 관리자 자산 키로 허용되지 않는 id도 저장소 조회 없이 판정한다
        (미충족으로 보인다).
        """
        ids = list(dict.fromkeys(str(i).strip() for i in db_ids if str(i).strip()))
        view = await self._mcp_view(health_for=ids)
        return {db_id: await self._readiness_for(db_id, view) for db_id in ids}

    async def get_registration(self, source: str) -> dict[str, Any]:
        """등록 단계 상태 · 준비도 · 예상 LLM 호출 수 · 마지막 서버 변수 · 설명 초안 목록.

        Raises:
            ValueError: 소스 이름 형식 오류
        """
        validate_db_id(source)
        steps = await self._store.load_registration(source)
        snapshot_record = await self._store.load_snapshot(source)
        structure = await self._structure_state(source)
        view = await self._mcp_view(health_for=[source])
        report = await self._readiness_for(
            source, view, structure=structure, registration=steps,
            snapshot_record=snapshot_record,
        )
        probe_detail = (steps.get("probe") or {}).get("detail") or {}
        return {
            "source": source,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "steps": steps,
            "readiness": report.to_dict(),
            "estimate": await self._estimate(source, snapshot_record),
            "server_variables": probe_detail.get("server_variables"),
            "description_drafts": await self._store.list_description_drafts(source),
        }

    async def _estimate(
        self, source: str, snapshot_record: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """예상 LLM 호출 수(O-3) — 설명 = 기본 범위 테이블 수 · 구조 = FK 묶음 수."""
        estimate: dict[str, Any] = {
            "description_calls": None,
            "structure_calls": None,
            "sample_calls_max": 1,
            "db_description_calls": 1,
            "scope_tables": None,
            "scope": None,
            "concurrency": int(self._config.schema_cache.admin_llm_concurrency),
            "provider": self.provider_info(),
        }
        snapshot = (snapshot_record or {}).get("snapshot") or {}
        tables = list(snapshot.get("tables") or {})
        if not tables:
            estimate["note"] = "스키마 수집(schema 단계) 뒤에 계산됩니다"
            return estimate
        scope, kind = await self._default_description_scope(source, tables)
        groups = split_fk_groups(
            schema_dict_from_snapshot(snapshot, tables),
            int(self._config.schema_cache.structure_group_max_tables),
        )
        estimate.update({
            "description_calls": len(scope),
            "scope_tables": len(scope),
            "scope": kind,
            "structure_calls": len(groups),
        })
        return estimate

    # --- 등록 잡 ---

    async def run_register(
        self,
        source: str,
        *,
        steps: list[str],
        tables: list[str] | None,
        by: str | None,
        ctx: JobContext,
    ) -> dict[str, Any]:
        """등록 잡 본문(B-3·B-4) — 요청 단계를 고정 순서로 실행하고 단계마다 상태를 기록한다.

        Args:
            source: 대상 소스(미등록·미활성이어도 된다 — G-6 (a))
            steps: `REGISTER_STEPS`의 부분집합
            tables: descriptions 단계 범위(없으면 현행 프로필 `allowed_tables` 또는 전체)
            by: 실행 관리자
            ctx: 잡 진행률 보고

        Returns:
            ``{"source", "env", "provider", "steps": {step: 등록 상태 항목}}``

        Raises:
            ValueError: 소스 이름·단계 이름 오류
            StructureStoreUnavailable: Redis 미연결(상태를 기록할 수 없다)
        """
        validate_db_id(source)
        requested = {str(s) for s in steps or []}
        unknown = sorted(requested - set(REGISTER_STEPS))
        if unknown or not requested:
            raise ValueError(
                f"steps는 {', '.join(REGISTER_STEPS)}의 부분집합이어야 합니다"
                + (f"(알 수 없는 단계: {', '.join(unknown)})" if unknown else "(비어 있음)")
            )
        await self._require_store()
        ordered = [s for s in REGISTER_STEPS if s in requested]
        concurrency = max(1, int(self._config.schema_cache.admin_llm_concurrency))
        provider_label = self._provider_label()
        recorded: dict[str, Any] = {}

        async with AsyncExitStack() as stack:
            run = _RegisterRun(
                self, source, tables=tables, by=by, concurrency=concurrency, stack=stack, ctx=ctx,
            )
            for index, step in enumerate(ordered):
                await ctx.progress(index, len(ordered), STEP_LABELS[step])
                try:
                    outcome = await getattr(self, f"_step_{step}")(run)
                except StructureStoreUnavailable:
                    raise
                except Exception as e:  # noqa: BLE001 — 단계 실패는 사유로 기록하고 다음 단계로 간다
                    logger.warning("등록 단계 실패: source=%s, step=%s: %s", source, step, e)
                    outcome = _StepOutcome("failed", None, {"error": f"{type(e).__name__}: {e}"})
                recorded[step] = await self._store.record_step(
                    source, step, status=outcome.status, count=outcome.count, by=by,
                    env=self.env, provider=provider_label if step in LLM_STEPS else None,
                    detail=outcome.detail,
                )
            await ctx.progress(len(ordered), len(ordered), "완료")

        logger.info(
            "DB 등록 잡: source=%s, steps=%s, by=%s",
            source, {k: v.get("status") for k, v in recorded.items()}, by,
        )
        return {
            "source": source, "env": self.env, "provider": self.provider_info(), "steps": recorded,
        }

    async def _step_probe(self, run: _RegisterRun) -> _StepOutcome:
        """O-1 — 연결 · MCP type ↔ 레지스트리 engine · 지원 엔진 · 서버 변수."""
        client = await run.client()
        source = run.source
        health = dict(await client.health_check_detail(source))
        detail: dict[str, Any] = {"health": health}

        mcp_type: str | None = None
        try:
            rows = await client.list_sources()
            row = next((r for r in rows if r.get("name") == source), None)
            detail["in_mcp"] = row is not None
            if row is not None and row.get("type"):
                mcp_type = str(row["type"])
        except Exception as e:  # noqa: BLE001 — 목록 실패는 사유로 남긴다
            detail["in_mcp"] = None
            detail["list_sources_error"] = f"{type(e).__name__}: {e}"

        registry = self._registry()
        entry = registry.get(source) if registry is not None else None
        registry_engine = entry.engine if entry is not None else None
        detail.update({"mcp_type": mcp_type, "registry_engine": registry_engine})
        if entry is None:
            detail["engine_match"] = None
            detail["registry_note"] = (
                "레지스트리 미등록 — 설정 조각(O-8)을 반영하고 앱을 재기동해야 라우터에 보입니다"
            )
        else:
            detail["engine_match"] = (
                _engine_key(mcp_type) == _engine_key(registry_engine) if mcp_type else None
            )
        engine = mcp_type or registry_engine
        detail["engine_supported"] = _engine_key(engine) in SUPPORTED_ENGINES if engine else None
        detail["server_variables"] = await self._server_variables(client, engine)

        if not health.get("healthy"):
            status = "failed"
        elif (
            detail["engine_match"] is False
            or detail["engine_supported"] is False
            or detail["server_variables"].get("error")
        ):
            status = "warning"
        else:
            status = "ok"
        return _StepOutcome(status, None, detail)

    @staticmethod
    async def _server_variables(client: Any, engine: str | None) -> dict[str, Any]:
        """엔진별 서버 변수(방언 판단 근거) — 실패는 "확인 못 함"."""
        sql = SERVER_VARIABLE_SQL.get(_engine_key(engine))
        if sql is None:
            return {"engine": engine, "sql": None, "values": None,
                    "error": f"확인 못 함 — 서버 변수 조회를 모르는 엔진: {engine or '(미상)'}"}
        try:
            assert_constant_select(sql)
            result = await client.execute_sql(sql)
            rows = list(getattr(result, "rows", None) or [])
            values = dict(rows[0]) if rows and isinstance(rows[0], Mapping) else None
            error = None if values else "확인 못 함 — 결과 행이 없습니다"
        except Exception as e:  # noqa: BLE001 — 서버 변수는 참고 정보라 실패를 사유로 남긴다
            values, error = None, f"확인 못 함 — {type(e).__name__}: {e}"
        return {"engine": engine, "sql": sql, "values": values, "error": error}

    async def _step_schema(self, run: _RegisterRun) -> _StepOutcome:
        """O-2 — `get_full_schema` 1회로 스키마 캐시와 스냅샷을 함께 만든다(지문 SQL 미호출)."""
        client = await run.client()
        source = run.source
        full = await client.get_full_schema()
        schema_dict, table_schemas = schema_dict_from_full_schema(full)
        snapshot = build_snapshot(schema_dict, table_schemas)
        await self._store.save_snapshot(source, {
            "snapshot": snapshot, "hash": snapshot["hash"], "taken_at": _now_iso(),
            "env": self.env, "by": run.by,
        })
        run.schema_dict, run.snapshot = schema_dict, snapshot
        detail: dict[str, Any] = {
            "relationships": len(schema_dict.get("relationships") or []),
            "snapshot_hash": snapshot["hash"],
        }
        saved = await self._cache_mgr.save_schema(source, schema_dict, fingerprint=None)
        if not saved:
            detail["error"] = (
                "스키마 캐시 저장이 거부되었거나 실패했습니다"
                "(테이블 0개·컬럼 없는 테이블 등 — 로그 참조)"
            )
            return _StepOutcome("failed", snapshot["table_count"], detail)
        cleanup = await self._cache_mgr.cleanup_stale_entries(source, schema_dict)
        self._cache_mgr.invalidate_memory_cache(source)
        detail["removed_descriptions"] = len(cleanup.get("removed_descriptions") or [])
        detail["removed_synonyms"] = len(cleanup.get("removed_synonyms") or [])
        return _StepOutcome("ok", snapshot["table_count"], detail)

    async def _schema_for(self, run: _RegisterRun) -> dict[str, Any]:
        schema_dict = run.schema_dict or await self._cache_mgr.get_schema(run.source)
        if not schema_dict or not schema_dict.get("tables"):
            raise ValueError("스키마 캐시가 없습니다 — schema 단계를 먼저 실행하세요")
        return schema_dict

    async def _step_descriptions(self, run: _RegisterRun) -> _StepOutcome:
        """O-4 — 범위 테이블마다 LLM 1회(동시성 상한)로 설명·유사어 초안을 만든다."""
        from src.schema_cache.description_generator import DescriptionGenerator

        source = run.source
        schema_dict = await self._schema_for(run)
        all_tables = list(schema_dict["tables"])
        if run.tables:
            scope, unknown = [], []
            for name in run.tables:
                key = resolve_table(all_tables, str(name))
                if key is None:
                    unknown.append(str(name))
                elif key not in scope:
                    scope.append(key)
            if unknown:
                raise ValueError(f"스키마에 없는 테이블: {', '.join(unknown)}")
            scope_kind = "tables"
        else:
            scope, scope_kind = await self._default_description_scope(source, all_tables)

        generator = DescriptionGenerator(run.llm())
        semaphore = asyncio.Semaphore(run.concurrency)
        done = 0

        async def _one(table: str) -> tuple[str, dict[str, str], dict[str, list[str]]]:
            nonlocal done
            table_data = schema_dict["tables"][table]
            columns = table_data.get("columns") or []
            async with semaphore:
                result = await generator.generate_for_table(
                    table, columns, table_data.get("sample_data") or None
                )
            descriptions, synonyms = _normalize_generated(table, columns, result)
            done += 1
            await run.ctx.progress(done, len(scope), f"컬럼 설명 초안 {done}/{len(scope)}")
            return table, descriptions, synonyms

        results = await asyncio.gather(*(_one(t) for t in scope))
        draft_descriptions = {t: d for t, d, _s in results if d}
        draft_synonyms = {t: s for t, d, s in results if d and s}
        failed = [t for t, d, _s in results if not d]

        previous = ((await self._store.load_registration(source)).get("descriptions") or {})
        previous_detail = previous.get("detail") or {}
        scope_names = scope
        if run.tables and previous_detail.get("scope_table_names"):
            scope_names = list(dict.fromkeys([*previous_detail["scope_table_names"], *scope]))
        detail: dict[str, Any] = {
            "scope": scope_kind,
            "scope_table_names": scope_names,
            "excluded_table_names": list(previous_detail.get("excluded_table_names") or []),
            "run_tables": scope,
            "failed_tables": failed,
            "succeeded_tables": len(draft_descriptions),
            "llm_calls": len(scope),
            "concurrency": run.concurrency,
        }
        if not draft_descriptions:
            detail["error"] = "모든 테이블의 설명 생성이 실패했습니다(로그 참조)"
            return _StepOutcome("failed", 0, detail)
        draft = await self._store.add_description_draft(source, {
            "descriptions": draft_descriptions,
            "synonyms": draft_synonyms,
            "failed_tables": failed,
            "scope": scope_kind,
            "scope_tables": scope,
            "created_by": run.by,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "llm_calls": len(scope),
        })
        detail["draft_id"] = draft.get("draft_id")
        return _StepOutcome("draft", len(draft_descriptions), detail)

    async def _step_db_description(self, run: _RegisterRun) -> _StepOutcome:
        """O-5 — DB 설명 LLM 초안(적용은 `set_db_description`)."""
        from src.schema_cache.description_generator import DescriptionGenerator

        schema_dict = await self._schema_for(run)
        generator = DescriptionGenerator(run.llm())
        text = await generator.generate_db_description(run.source, schema_dict)
        origin = await self._cache_mgr.get_db_description_origin(run.source)
        detail: dict[str, Any] = {"current_origin": origin}
        if not text:
            detail["error"] = "DB 설명 생성에 실패했습니다(로그 참조)"
            return _StepOutcome("failed", None, detail)
        detail["draft_text"] = text
        if origin == "manual":
            detail["note"] = (
                "현재 설명은 수동(manual)입니다 — LLM 초안으로 적용해도 덮이지 않습니다"
            )
        return _StepOutcome("draft", 1, detail)

    async def _step_seeds(self, run: _RegisterRun) -> _StepOutcome:
        """O-7 ① — 배포된 시드 파일이 있으면 병합 로드(없으면 해당 없음)."""
        from src.schema_cache.synonym_loader import SynonymLoader

        path = SEEDS_DIR / f"{run.source}.yaml"
        if not path.is_file():
            return _StepOutcome(
                "skipped", None, {"reason": "해당 없음 — 시드 파일이 없습니다", "path": str(path)}
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        seed_db_id = data.get("db_id") if isinstance(data, Mapping) else None
        if seed_db_id != run.source:
            return _StepOutcome("failed", None, {
                "path": str(path),
                "error": f"시드 파일 db_id({seed_db_id})가 소스({run.source})와 다릅니다",
            })
        redis_cache = self._store.redis_cache
        if redis_cache is None:
            return _StepOutcome(
                "failed", None, {"path": str(path), "error": "Redis 캐시가 없습니다"}
            )
        result = await SynonymLoader(redis_cache).load_seed_yaml(str(path))
        status = {"success": "ok", "partial": "warning"}.get(result.status, "failed")
        return _StepOutcome(status, result.columns_loaded, {
            "path": str(path),
            "message": result.message,
            "eav_names_loaded": result.eav_names_loaded,
            "column_values_loaded": result.column_values_loaded,
            "total_words": result.total_words,
            "errors": list(result.errors),
        })

    async def _step_value_index(self, run: _RegisterRun) -> _StepOutcome:
        """O-7 ② — 현행 구조가 EAV면 속성명 값 인덱스를 만든다(키 표기는 질의 경로와 같다)."""
        source = run.source
        structure = await self._current_structure(source)
        specs = derive_value_specs(structure, "")
        value_retrieval = bool(self._config.synonym.value_retrieval)
        if not specs:
            return _StepOutcome("skipped", None, {
                "reason": "해당 없음 — 현행 구조 정보에 EAV 패턴이 없습니다",
                "value_retrieval": value_retrieval,
            })
        snapshot = run.snapshot or ((await self._store.load_snapshot(source)) or {}).get("snapshot")
        if not snapshot:
            raise ValueError("스냅샷이 없습니다 — schema 단계를 먼저 실행하세요")
        client = await run.client()
        engine, db_schema = await self._engine_and_schema(source, client)
        items = await collect_code_values(
            client,
            [{"key": s["key"], "table": s["table"], "column": s["column"], "origin": "value_index"}
             for s in specs],
            snapshot=snapshot, engine=engine, db_schema=db_schema, limit=VALUE_INDEX_LIMIT,
        )
        index = {str(i["key"]): i["values"] for i in items if i["values"]}
        detail: dict[str, Any] = {
            "keys": {str(i["key"]): len(i["values"]) for i in items},
            "errors": [{"key": i["key"], "error": i["error"]} for i in items if i["error"]],
            "value_retrieval": value_retrieval,
            "ttl_note": "값 인덱스는 1일 뒤 만료되며 이후 질의 경로가 필요할 때 다시 만듭니다",
        }
        if not value_retrieval:
            detail["note"] = (
                "SYNONYM_VALUE_RETRIEVAL이 꺼져 있어 만들어 두지만 질의에서 쓰이지 않습니다"
            )
        redis_cache = self._store.redis_cache
        if not index:
            detail["error"] = "수집된 값이 없습니다"
            return _StepOutcome("failed", 0, detail)
        if redis_cache is None or not await redis_cache.save_column_value_index(source, index):
            detail["error"] = "값 인덱스 저장에 실패했습니다"
            return _StepOutcome("failed", len(index), detail)
        return _StepOutcome("warning" if detail["errors"] else "ok", len(index), detail)

    # --- 설명 초안 적용·폐기 ---

    async def _pending_description_draft(self, source: str, draft_id: str) -> dict[str, Any]:
        draft = await self._store.get_description_draft(source, draft_id)
        if draft is None:
            raise DraftNotApprovable("not_found", f"설명 초안이 없습니다: {draft_id}")
        if draft.get("status") != "pending":
            raise DraftNotApprovable(
                "not_pending", f"대기 중인 설명 초안이 아닙니다(status={draft.get('status')})"
            )
        return draft

    async def apply_description_draft(
        self,
        source: str,
        draft_id: str,
        *,
        exclude_tables: list[str] | None,
        by: str | None,
    ) -> dict[str, Any]:
        """설명·유사어 초안을 테이블 단위 제외 후 적용한다(G-8 (a) · S5 · B-6).

        적용 = `save_descriptions` + `save_synonyms(source="llm")`(운영자 단어 보존 병합) +
        `sync_global_synonyms` + 적용 후 전체 설명·유사어 파일 백업.

        Raises:
            DraftNotApprovable: 초안 없음 · pending 아님 · 다른 env에서 만든 초안
            ValueError: 초안에 없는 제외 테이블
            StructureStoreUnavailable: Redis 미연결
            RuntimeError: 캐시 저장 실패
        """
        validate_db_id(source)
        await self._require_store()
        draft = await self._pending_description_draft(source, draft_id)
        if str(draft.get("env") or "").rstrip("/") != self.env.rstrip("/"):
            raise DraftNotApprovable(
                "env_mismatch",
                f"다른 환경에서 만든 초안입니다(초안 env={draft.get('env')} · 현재 env={self.env})",
            )
        draft_descriptions: Mapping[str, Mapping[str, str]] = draft.get("descriptions") or {}
        draft_synonyms: Mapping[str, Mapping[str, list[str]]] = draft.get("synonyms") or {}
        known_tables = {*draft_descriptions, *(draft.get("failed_tables") or [])}
        exclude = list(dict.fromkeys(str(t) for t in exclude_tables or []))
        unknown = [t for t in exclude if t not in known_tables]
        if unknown:
            raise ValueError(f"초안에 없는 제외 테이블: {', '.join(unknown)}")

        applied_tables = [t for t in draft_descriptions if t not in exclude]
        descriptions = {
            k: v for t in applied_tables for k, v in (draft_descriptions.get(t) or {}).items()
        }
        synonyms = {
            k: list(v) for t in applied_tables for k, v in (draft_synonyms.get(t) or {}).items()
        }
        if descriptions and not await self._cache_mgr.save_descriptions(source, descriptions):
            raise RuntimeError(f"컬럼 설명 저장에 실패했습니다(source={source})")
        if synonyms and not await self._cache_mgr.save_synonyms(source, synonyms, source="llm"):
            raise RuntimeError(f"유사어 저장에 실패했습니다(source={source})")
        global_synced = await self._cache_mgr.sync_global_synonyms(source) if synonyms else 0

        redis_cache = self._store.redis_cache
        all_synonyms = (
            await redis_cache.load_synonyms_with_sources(source) if redis_cache is not None else {}
        )
        backup_path = self._store.backup_descriptions(
            source, await self._cache_mgr.get_descriptions(source), all_synonyms
        )

        registration = await self._store.load_registration(source)
        previous_detail = ((registration.get("descriptions") or {}).get("detail")) or {}
        detail: dict[str, Any] = {
            **previous_detail,
            "draft_id": draft_id,
            "excluded_table_names": list(dict.fromkeys(
                [*(previous_detail.get("excluded_table_names") or []), *exclude]
            )),
            "applied_table_names": applied_tables,
            "synonym_columns": len(synonyms),
            "global_synced_columns": global_synced,
            "backup_path": str(backup_path),
        }
        detail.pop("error", None)
        scope_n, applied_n, excluded_n = await self._description_coverage(
            source, {"descriptions": {"detail": detail}}, await self._store.load_snapshot(source)
        )
        detail.update({
            "scope_tables": scope_n, "applied_tables": applied_n, "excluded_tables": excluded_n,
        })
        provider = draft.get("provider") or {}
        entry = await self._store.record_step(
            source, "descriptions", status="applied", count=applied_n, by=by, env=self.env,
            provider=f"{provider.get('provider')}/{provider.get('model')}" if provider else None,
            detail=detail,
        )
        await self._store.update_description_draft(
            source, draft_id, status="applied", applied_by=by, applied_at=_now_iso(),
            excluded_tables=exclude,
        )
        logger.info(
            "컬럼 설명 초안 적용: source=%s, draft_id=%s, tables=%d, excluded=%d, "
            "synonyms=%d, by=%s",
            source, draft_id, len(applied_tables), len(exclude), len(synonyms), by,
        )
        return {
            "source": source,
            "draft_id": draft_id,
            "applied_tables": applied_tables,
            "excluded_tables": exclude,
            "description_count": len(descriptions),
            "synonym_columns": len(synonyms),
            "global_synced_columns": global_synced,
            "backup_path": str(backup_path),
            "coverage": {"scope": scope_n, "applied": applied_n, "excluded": excluded_n},
            "registration": entry,
        }

    async def discard_description_draft(
        self, source: str, draft_id: str, *, by: str | None
    ) -> dict[str, Any]:
        """설명 초안을 폐기한다(캐시 변경 없음).

        Raises:
            DraftNotApprovable: 초안 없음 · pending 아님
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        await self._pending_description_draft(source, draft_id)
        updated = await self._store.update_description_draft(
            source, draft_id, status="discarded", discarded_by=by, discarded_at=_now_iso(),
        )
        registration = await self._store.load_registration(source)
        step = registration.get("descriptions") or {}
        detail = step.get("detail") or {}
        if step.get("status") == "draft" and detail.get("draft_id") == draft_id:
            await self._store.record_step(
                source, "descriptions", status="discarded", count=0, by=by, env=self.env,
                provider=step.get("provider"), detail=detail,
            )
        logger.info("컬럼 설명 초안 폐기: source=%s, draft_id=%s, by=%s", source, draft_id, by)
        return {"source": source, "draft_id": draft_id, "status": (updated or {}).get("status")}

    async def set_db_description(
        self, source: str, *, text: str, origin: str, by: str | None
    ) -> dict[str, Any]:
        """DB 상세 설명을 출처와 함께 적용한다(O-5 · S5 — LLM 값은 수동 설명을 덮지 않는다).

        Returns:
            ``{"source", "saved": bool, "origin", "preserved_manual": bool, "reason"?,
            "registration"}``

        Raises:
            ValueError: 소스 이름 · 빈 설명 · origin 오류
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        if origin not in ("manual", "llm"):
            raise ValueError(f"origin은 manual 또는 llm이어야 합니다: {origin!r}")
        clean = str(text or "").strip()
        if not clean:
            raise ValueError("DB 설명이 비어 있습니다")
        await self._require_store()
        previous_origin = await self._cache_mgr.get_db_description_origin(source)
        saved = await self._cache_mgr.save_db_description(source, clean, origin=origin)
        preserved = not saved and origin == "llm" and previous_origin == "manual"

        registration = await self._store.load_registration(source)
        step = registration.get("db_description") or {}
        detail = {
            **(step.get("detail") or {}),
            "applied_origin": origin,
            "applied_text": clean if saved else None,
            "preserved_manual": preserved,
        }
        result: dict[str, Any] = {
            "source": source, "saved": saved, "origin": origin, "preserved_manual": preserved,
        }
        if saved:
            status = "applied"
        elif preserved:
            status = "skipped"
            result["reason"] = "수동(manual) 설명이 있어 LLM 설명으로 덮지 않았습니다"
        else:
            status = "failed"
            result["reason"] = "DB 설명 저장에 실패했습니다(로그 참조)"
        detail["reason"] = result.get("reason")
        result["registration"] = await self._store.record_step(
            source, "db_description", status=status, count=1 if saved else 0, by=by,
            env=self.env, provider=step.get("provider"), detail=detail,
        )
        logger.info(
            "DB 설명 적용: source=%s, origin=%s, saved=%s, preserved_manual=%s, by=%s",
            source, origin, saved, preserved, by,
        )
        return result

    # --- 설정 조각 ---

    async def config_snippets(self, source: str) -> dict[str, Any]:
        """레지스트리 항목 조각(O-8 · §3.8.4) — 파일을 쓰지 않고 문자열만 돌려준다(R11).

        Returns:
            ``{"source", "registry_yaml", "local_sandbox", "entry", "notes"}``

        Raises:
            ValueError: 소스 이름 형식 오류
        """
        validate_db_id(source)
        registry = self._registry()
        entry = registry.get(source) if registry is not None else None
        registration = await self._store.load_registration(source)
        snapshot_record = await self._store.load_snapshot(source)
        probe_detail = (registration.get("probe") or {}).get("detail") or {}
        db_description_detail = (registration.get("db_description") or {}).get("detail") or {}
        draft_text = db_description_detail.get("draft_text")

        schemas = [
            str(t.get("schema")) for t in (
                ((snapshot_record or {}).get("snapshot") or {}).get("tables") or {}
            ).values()
            if isinstance(t, Mapping) and t.get("schema")
        ]
        schema_candidate = Counter(schemas).most_common(1)[0][0] if schemas else None
        engine = probe_detail.get("mcp_type") or (entry.engine if entry is not None else None)
        description = draft_text or (entry.description if entry is not None else "")
        item = {
            "db_id": source,
            "enabled": True,
            "display_name": entry.display_name if entry is not None else "",
            "description": description or "",
            "aliases": list(entry.aliases) if entry is not None else [],
            "engine": engine or "",
            "db_schema": schema_candidate or (entry.db_schema if entry is not None else ""),
            "zone": entry.zone if entry is not None else "",
        }

        dumped = yaml.safe_dump({"databases": [item]}, allow_unicode=True, sort_keys=False)
        body: list[str] = []
        for line in dumped.splitlines():
            if line.startswith("  description:"):
                body.append(
                    "  # 사람 검토 필요 — "
                    + (
                        "LLM 초안(O-5)" if draft_text
                        else "현행 레지스트리 값(비어 있으면 작성 필요)"
                    )
                )
            body.append(line)
        header: list[str] = []
        if self.local_sandbox:
            header.append(LOCAL_SANDBOX_HEADER)
        header.extend([
            "# plans/104 설정 조각 — config/db_registry.yaml 의 databases: 목록에 붙여 넣는다",
            "# 앱은 이 파일을 쓰지 않는다(R11) · 사람이 검토해 PR·배포로 반영한다",
            f"# 생성={_now_iso()} · 소스={source} · env={self.env}",
            "# 반영 뒤: 앱 재기동(레지스트리는 기동 시 1회 읽는다) → 준비도 재판정",
        ])

        notes = [
            "레지스트리 항목 조각만 제공합니다 — 구조 프로필은 승인 시 "
            f"config/db_profiles/{source}.yaml에 직접 기록됩니다(G-2).",
            "MCP 소스 설정(config.toml·연결 문자열)은 비밀 경계라 조각을 만들지 않습니다.",
            "반영 뒤 앱을 재기동하고 준비도를 다시 판정하세요(레지스트리 핫 리로드 없음).",
        ]
        if draft_text:
            notes.append("description은 LLM 초안입니다 — 사람 검토가 필요합니다.")
        elif not description:
            notes.append(
                "description이 비어 있습니다 — 라우터가 빈 설명을 렌더하므로 작성해야 합니다(C3)."
            )
        if schema_candidate is None:
            notes.append(
                "db_schema 후보를 찾지 못했습니다(스냅샷 없음) — 스키마 수집 뒤 다시 내려받으세요."
            )
        if _engine_key(engine) == "db2" and not item["db_schema"]:
            notes.append("DB2는 db_schema가 필요합니다(대문자 스키마 한정 — C4).")
        if self.local_sandbox:
            notes.append("로컬 샌드박스에서 만든 조각입니다 — 운영 정본 재료로 쓰지 마세요.")
        return {
            "source": source,
            "registry_yaml": "\n".join([*header, *body]) + "\n",
            "local_sandbox": self.local_sandbox,
            "entry": item,
            "notes": notes,
        }
