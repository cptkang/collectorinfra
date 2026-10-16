"""관리자 「DB 구조」 스키마 자산 자동 생성 서비스 (D-294 · plans/133 W4).

폴스타 프로필·시드·전용 프롬프트에 해당하는 자산을 신규 DB에도 만든다 — 스키마 스냅샷(O-2 MCP 수집 ·
D-292 DDL 등록)과 **읽기 전용 데이터 조회**(`schema_probe`)로 결정적으로 만들고, 결정적으로 만들 수
없는 것(쿼리 예시 · DB 전용 규칙 섹션)만 LLM 초안을 쓴 뒤 결정적 검증과 실제 실행으로 거른다.

- **P1 프로파일링 잡(`run_asset_profile` · LLM 0)**: 카탈로그 주석·행 수 → 관계(선언 FK · 기본키
  일치 추론 · 같은 기본키 군 · 기본키 없는 테이블 쌍의 이름 일치(부모 유일성 확인 · plans/140
  W1-2) — 추론은 값 겹침 ≥ `OVERLAP_MIN`만 채택) → 코드 컬럼(`DISTINCT` ≤ 50) · 값 형식 → 코드
  라벨(주석 열거 · 공통코드 테이블 — 승인 테이블 정의 `kind` 기준·코드 테이블은 기본키 없이
  코드/이름 컬럼 쌍으로 · plans/143 W7) · 식별 키 · 쿼리 규칙 · 조회 대상 후보 → **자산 초안**. 조회
  예산은 필요량을 미리 세어 정한다(기본 `DEFAULT_PROBE_BUDGET` · 상한 `PROBE_BUDGET_CAP` ·
  plans/140 W1-3). 주석이 있으면 **설명 초안**(출처 `comment`)도 기존 「설명 초안 검토·적용」에
  넣는다. DB에 닿지 못하면 스키마만으로 만들 수 있는 것(선언 관계 · 주석)만 만든다(`offline`).
- **P2 LLM 보조 잡(`run_asset_llm`)**: 초안 자산 요약 → 쿼리 예시(안전성 · 실제 실행 성공만) · DB
  전용 규칙 섹션(중괄호 금지 · 식별자 실존 · 섹션 안 SQL 실행 성공 · 길이 상한).
- **승인(`approve_asset_draft`)**: 자산별 포함 목록 → 프로필 키는 `merge_profile`(사람 값 보존) →
  `StructureStore.apply_profile` · 시드·전용 섹션은 `AssetFileStore`(버전 · 되돌리기). 시드는 기존
  O-7 로더(`SynonymLoader.load_seed_yaml`)로 적재한다 — DB별 `column_synonyms` + DB 공용 사전
  `column_values`(폴스타·ITAM 공유 · `plans/132` G-13 사용자 확정).
- **테이블 정의(`table_definitions` · D-308 ① · plans/139 W3)**: 테이블마다 「관리하는 정보」.
  초안 경로 3가지 — 가져오기(`import_table_definitions` · 시드 YAML 형식) · 주석(P1이 테이블
  주석을 `comment`
  정의로) · LLM 묶음 초안(`run_table_definition_llm` · 주석 없는 테이블만 · 군 접두 단위 묶음 ·
  `admin_llm_concurrency` · 실행 전 `estimate_table_definition_llm` · 실패 묶음만 재실행).
  형식·검증은
  `src.domain.table_definitions`가 정하고, 검증 실패 행은 승인할 수 없다(테이블 단위 선택 승인 ·
  출처 순위 보존 병합 `manual` > `import` > `llm` > `comment`(plans/151 W1) · 되돌리기는 프로필
  「버전 이력」).

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from src.domain import schema_inference as inference
from src.domain.profile_merge import base_definition_origin, merge_profile, profile_field_diff
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import (
    FIELDS,
    IMPORT_MAX_CHARS,
    KINDS,
    ORIGIN_COMMENT,
    ORIGIN_IMPORT,
    ORIGIN_LLM,
    ORIGIN_MANUAL,
    defined_table_count,
    parse_import_document,
    validate_table_definitions,
)
from src.domain.table_definitions import PROFILE_KEY as TABLE_DEFINITIONS_KEY
from src.prompts.asset_generation import (
    PROMPT_SECTION_PROMPT,
    QUERY_EXAMPLES_PROMPT,
)
from src.schema_cache import schema_probe as probe
from src.schema_cache.admin_base import AdminServiceBase
from src.schema_cache.asset_store import AssetFileStore
from src.schema_cache.db_structure_service import (
    LOCAL_SANDBOX_HEADER,
    DraftNotApprovable,
    _now_iso,
    schema_dict_from_snapshot,
)
from src.schema_cache.structure_store import validate_db_id
from src.utils.sql_dialect import row_limit_clause

# ──────────────────────────────────────────────
# 분할 모듈 재노출 (plans/166 I3 · D-337 ① — shim 1사이클)
# ──────────────────────────────────────────────
# 조립·요약(`asset_generation_assembly`)과 테이블 정의·LLM 보조 검증(`asset_generation_llm`)으로
# 옮긴 이름을 `_` 이름까지 다시 노출한다. 이 모듈에 남긴 것(Facade): 테스트가 이 모듈에서 바꾸는
# 상한(`NAME_MATCH_*` · 조회 예산 · `MAX_DEFINED_CODE_PAIRS`)을 읽는 함수(D-337 ②).
# `X as X`는 mypy strict(no_implicit_reexport)용 명시 재노출이다 — 이 블록만 정렬에서 뺀다.
# isort: off
from src.schema_cache.asset_generation_assembly import (
    MIN_TABLES_FOR_COMMON as MIN_TABLES_FOR_COMMON,
    CODE_TABLE_COVERAGE as CODE_TABLE_COVERAGE,
    SUMMARY_MAX_CHARS as SUMMARY_MAX_CHARS,
    _STRING_TYPE_RE as _STRING_TYPE_RE,
    _INTEGER_TYPE_RE as _INTEGER_TYPE_RE,
    _DATE_HINT_RE as _DATE_HINT_RE,
    _HOST_IP_HINT_RE as _HOST_IP_HINT_RE,
    _scope_tables as _scope_tables,
    _relation_item as _relation_item,
    _relation_candidates as _relation_candidates,
    _name_match_candidates as _name_match_candidates,
    _ColumnPlan as _ColumnPlan,
    _column_candidates as _column_candidates,
    _column_evidence as _column_evidence,
    _assemble as _assemble,
    _best_code_table as _best_code_table,
    _asset_summary as _asset_summary,
    _draft_comments as _draft_comments,
    build_asset_summary as build_asset_summary,
)
from src.schema_cache.asset_generation_llm import (
    SqlChecker as SqlChecker,
    EXAMPLE_COUNT as EXAMPLE_COUNT,
    SECTION_MAX_CHARS as SECTION_MAX_CHARS,
    SQL_CHECK_LIMIT as SQL_CHECK_LIMIT,
    DEFINITION_BATCH_SIZE as DEFINITION_BATCH_SIZE,
    DEFINITION_DESCRIPTION_MAX_CHARS as DEFINITION_DESCRIPTION_MAX_CHARS,
    DEFINITION_LABELS_MAX as DEFINITION_LABELS_MAX,
    _FENCE_RE as _FENCE_RE,
    MAX_SECTION_SQL_BLOCKS as MAX_SECTION_SQL_BLOCKS,
    _SIDE_EFFECT_FUNC_RE as _SIDE_EFFECT_FUNC_RE,
    _SQL_WORDS as _SQL_WORDS,
    _DefinitionPlan as _DefinitionPlan,
    _DefinitionContext as _DefinitionContext,
    _str_list as _str_list,
    _row_origin as _row_origin,
    _table_columns as _table_columns,
    _NoAliasSafeLoader as _NoAliasSafeLoader,
    load_import_yaml as load_import_yaml,
    _display_text as _display_text,
    _display_row as _display_row,
    _definition_rows as _definition_rows,
    _definition_summary as _definition_summary,
    definition_batches as definition_batches,
    _render_definition_input as _render_definition_input,
    _definition_batch as _definition_batch,
    _cap_llm_key_columns as _cap_llm_key_columns,
    _merge_definition_batch as _merge_definition_batch,
    _approvable_definitions as _approvable_definitions,
    _ask as _ask,
    _SqlCheck as _SqlCheck,
    _execute_check as _execute_check,
    _validate_examples as _validate_examples,
    _validate_section as _validate_section,
)
# isort: on

if TYPE_CHECKING:
    from src.schema_cache.admin_jobs import JobContext

logger = logging.getLogger(__name__)

#: 승인 화면에서 고를 수 있는 자산
ASSET_KINDS: tuple[str, ...] = (
    "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
    "query_examples", "seeds", "prompt_template", TABLE_DEFINITIONS_KEY,
)
#: 프로필 키로 들어가는 자산(`code_values`는 `code_labels`와 함께 간다)
PROFILE_ASSETS: tuple[str, ...] = (
    "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
    "query_examples", TABLE_DEFINITIONS_KEY,
)
DEFAULT_PROBE_BUDGET = 400
#: 조회 예산 상한 — 필요량이 기본 예산을 넘으면 이 값까지 늘린다(plans/140 W1-3)
PROBE_BUDGET_CAP = 2000
#: 이름 일치 관계(plans/140 W1-2) — 컬럼당 값 겹침 조회 상한
NAME_MATCH_MAX_OVERLAPS_PER_COLUMN = 30
#: 이름 일치 관계 — 잡 전체 조회(유일성 + 값 겹침) 상한
NAME_MATCH_MAX_QUERIES = 600
#: 추론 관계 채택 하한(자식 키 표본 중 부모에 있는 비율)
OVERLAP_MIN = 0.9
OVERLAP_SAMPLE = 200
FORMAT_SAMPLE = 200
CODE_VALUE_MAX_LENGTH = 40
CODE_TABLE_PAIRS = 1000
MAX_CODE_TABLES = 10
#: 테이블 정의 기준·코드 쌍(plans/143 W7) 상한 — 쌍마다 조회 1회(예산 산정에 포함)
MAX_DEFINED_CODE_PAIRS = 20
#: 검토 화면에서 고칠 수 있는 정의 필드(고친 행은 출처 `manual`)
EDITABLE_DEFINITION_FIELDS: tuple[str, ...] = ("manages", "notes", "kind", "key_columns")

_NAME_COL_RE = re.compile(r"(?i)(nm|name)$|명$|이름")
_CODE_TABLE_HINT_RE = re.compile(r"(?i)code|(^|_)cd|코드")


class AssetGenerationService(AdminServiceBase):
    """스키마 자산 자동 생성 — 프로파일링 · LLM 보조 · 초안 승인·반려 · 자산 버전 되돌리기."""

    def __init__(
        self,
        *args: Any,
        asset_store: AssetFileStore | None = None,
        sql_checker: SqlChecker | None = None,
        **kwargs: Any,
    ) -> None:
        """서비스를 만든다(`AdminServiceBase` 인자 + 생성 자산 파일 저장소 + LLM SQL 검증기).

        `sql_checker`가 없으면 LLM이 만든 SQL을 실행하지 않는다(닫힌 쪽으로 실패).
        """
        super().__init__(*args, **kwargs)
        self._sql_checker = sql_checker
        self._assets = asset_store or AssetFileStore(
            self._store.profiles_dir.parents[1], self._store.backup_root
        )

    # --- 조회 ---

    async def overview(self, source: str) -> dict[str, Any]:
        """자산 초안 목록 · 자산 파일 버전 · 현행 파일 유무 · 승인된 테이블 정의 범위."""
        validate_db_id(source)
        files: dict[str, Any] = {}
        for kind in ("seeds", "prompt_template"):
            current = self._assets.read_current(source, kind)
            files[kind] = {
                "path": str(self._assets.path(source, kind)),
                "exists": current is not None,
                "versions": await self._assets.list_versions(source, kind),
            }
        profile = (self._store.read_current_profile(source) or {}).get("profile") or {}
        approved = profile.get(TABLE_DEFINITIONS_KEY)
        allowed = _str_list(profile.get("allowed_tables"))
        return {
            "source": source, "env": self.env, "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "drafts": await self._store.list_asset_drafts(source),
            "files": files,
            "table_definitions": {
                "approved": len(approved) if isinstance(approved, Mapping) else 0,
                "allowed": len(allowed),
                "covered": defined_table_count(approved, allowed),
                "kinds": list(KINDS),
                "import_max_chars": IMPORT_MAX_CHARS,
            },
        }

    # --- P1 프로파일링 ---

    async def run_asset_profile(
        self, source: str, *, tables: list[str] | None, by: str | None, ctx: JobContext
    ) -> dict[str, Any]:
        """프로파일링 잡 본문 — 결정적 자산 초안을 만든다(LLM 0).

        Raises:
            ValueError: 소스 이름 오류 · 스냅샷 없음 · 모르는 엔진 · 범위 테이블이 스냅샷에 없음
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        record = await self._store.load_snapshot(source)
        if not record or not (record.get("snapshot") or {}).get("tables"):
            raise ValueError("스냅샷이 없습니다 — O-2 스키마 수집 또는 DDL 등록을 먼저 하세요")
        snapshot: Mapping[str, Any] = record["snapshot"]
        snap_tables: Mapping[str, Any] = snapshot["tables"]
        current = self._store.read_current_profile(source)
        profile: Mapping[str, Any] = (current or {}).get("profile") or {}
        scope = _scope_tables(tables, profile, snap_tables)
        budget = probe.ProbeBudget(limit=DEFAULT_PROBE_BUDGET)
        total = 6

        async with AsyncExitStack() as stack:
            client: Any = None
            offline: str | None = None
            try:
                client = await stack.enter_async_context(self._client_factory(source))
            except Exception as e:  # noqa: BLE001 — DB에 닿지 못하면 스키마만으로 만든다
                offline = f"{type(e).__name__}: {e}"
                logger.warning("자산 프로파일링 DB 연결 실패 — 스키마만으로 진행: %s", offline)
            raw_engine, raw_schema = await self._engine_and_schema(source, client)
            engine = probe.engine_key(raw_engine)
            if engine not in probe.CATALOG_SQL:
                raise ValueError(f"자산 생성을 지원하지 않는 엔진입니다: {engine or '(미상)'}")
            db_schema: str | None = raw_schema or None

            await ctx.progress(0, total, "카탈로그(주석·행 수)")
            comments = dict(await self._store.load_ddl_comments(source))
            catalog = probe.CatalogInfo()
            if client is not None:
                catalog = await probe.read_catalog(
                    client, engine=engine, snapshot=snapshot, db_schema=db_schema, budget=budget,
                )
                comments.update(catalog.table_comments)
                comments.update(catalog.column_comments)

            # 조회 계획 — 필요량을 미리 세어 예산을 정한다(필요량 ≤ 기본 예산이면 종전 그대로)
            shapes = inference.shapes_from_snapshot(snapshot)
            relation_candidates = _relation_candidates(shapes)
            name_columns = _name_match_candidates(snap_tables, scope, shapes)
            column_plan = _column_candidates(snap_tables, scope, comments)
            code_table_plan = _code_table_candidates(
                snapshot, comments, profile.get(TABLE_DEFINITIONS_KEY)
            )
            requested = _requested_queries(
                probe.catalog_query_count(snapshot, db_schema), relation_candidates,
                name_columns, column_plan, code_table_plan,
            )
            budget.limit = min(max(DEFAULT_PROBE_BUDGET, requested), PROBE_BUDGET_CAP)

            await ctx.progress(1, total, "관계 추론·값 겹침")
            relationships, relation_evidence = await self._relationships(
                client, snapshot, relation_candidates, name_columns, engine, db_schema, budget,
            )

            await ctx.progress(2, total, "코드값·값 형식")
            columns = await self._profile_columns(
                client, snapshot, column_plan, engine, db_schema, budget,
            )

            await ctx.progress(3, total, "공통코드 라벨")
            code_tables = await self._code_tables(
                client, snapshot, code_table_plan, engine, db_schema, budget,
            )

        await ctx.progress(4, total, "자산 조립")
        assets, evidence = _assemble(
            source, snapshot, scope, comments, catalog, relationships, columns, code_tables,
            profile.get(TABLE_DEFINITIONS_KEY),
        )
        # 테이블 주석 → 테이블 정의 초안(출처 `comment` · LLM 0 · 검증 실패는 오류 행)
        definitions, definition_errors = _definition_rows(
            {t: {"manages": comments[t], "origin": ORIGIN_COMMENT}
             for t in scope if comments.get(t)},
            _table_columns(snap_tables), ORIGIN_COMMENT,
        )
        assets[TABLE_DEFINITIONS_KEY] = definitions
        budget_evidence = {**budget.to_dict(), "requested": requested, "cap": PROBE_BUDGET_CAP}
        evidence.update({
            "relationships": relation_evidence,
            "catalog": {"comments": len(comments), "errors": catalog.errors},
            "budget": budget_evidence,
            "offline": offline,
        })
        description_draft_id = await self._comment_description_draft(source, comments, by)

        await ctx.progress(5, total, "초안 저장")
        draft = await self._store.add_asset_draft(source, {
            "kind": "profile",
            "scope": scope,
            "engine": engine,
            "snapshot_hash": record.get("hash"),
            "assets": assets,
            "evidence": evidence,
            "validation": {
                TABLE_DEFINITIONS_KEY: {"errors": definition_errors, "batches": []},
            } if definitions else {},
            "description_draft_id": description_draft_id,
            "llm_calls": 0,
            "provider": None,
            "created_by": by,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
        })
        await ctx.progress(total, total, "완료")
        logger.info(
            "자산 프로파일링 초안: source=%s, draft_id=%s, relationships=%d, codes=%d, rules=%d, "
            "queries=%d/%d, offline=%s, by=%s",
            source, draft.get("draft_id"), len(assets["relationships"]),
            len(assets["code_values"]), len(assets["query_rules"]), budget.used, budget.limit,
            bool(offline), by,
        )
        return {
            "source": source, "draft_id": draft.get("draft_id"), "env": self.env,
            "summary": _asset_summary(assets),
            "description_draft_id": description_draft_id,
            "budget": budget_evidence, "offline": offline,
        }

    async def _relationships(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        candidates: Sequence[inference.InferredRelation],
        name_columns: Sequence[inference.NameMatchColumn],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """관계 후보(선언 · 추론 · 같은 기본키 군 · 이름 일치) → 값 겹침 검증 → 채택 관계(컬럼 쌍
        단위)."""
        evidence: list[dict[str, Any]] = []
        known_pairs: set[frozenset[str]] = set()
        for rel in candidates:
            known_pairs.add(frozenset((rel.child, rel.parent)))
            item = _relation_item(
                rel.child, rel.parent, list(rel.child_columns), list(rel.parent_columns),
                rel.origin,
            )
            evidence.append(item)
            if rel.origin == "declared":
                item["accepted"] = bool(rel.parent_columns)
            elif client is None:
                item["error"] = "DB 연결 없음 — 값 겹침을 확인하지 못했습니다"
            else:
                ov = await probe.check_overlap(
                    client, child=rel.child, child_columns=rel.child_columns, parent=rel.parent,
                    parent_columns=rel.parent_columns, snapshot=snapshot, engine=engine,
                    db_schema=db_schema, budget=budget, sample=OVERLAP_SAMPLE,
                )
                if rel.origin == "same_key" and (ov["ratio"] or 0) < OVERLAP_MIN:
                    # 같은 기본키 군은 방향이 없다 — 반대 방향(작은 쪽이 자식)도 재서 높은 쪽을 쓴다
                    reverse = await probe.check_overlap(
                        client, child=rel.parent, child_columns=rel.parent_columns,
                        parent=rel.child, parent_columns=rel.child_columns, snapshot=snapshot,
                        engine=engine, db_schema=db_schema, budget=budget, sample=OVERLAP_SAMPLE,
                    )
                    if (reverse["ratio"] or 0) > (ov["ratio"] or 0):
                        rel = inference.InferredRelation(
                            rel.parent, rel.child, rel.parent_columns, rel.child_columns,
                            rel.origin,
                        )
                        ov = reverse
                        item.update(child=rel.child, parent=rel.parent,
                                    child_columns=list(rel.child_columns),
                                    parent_columns=list(rel.parent_columns))
                item.update(overlap=ov["ratio"], sampled=ov["sampled"], error=ov["error"])
                item["accepted"] = ov["ratio"] is not None and ov["ratio"] >= OVERLAP_MIN
        evidence.extend(await self._name_match_relations(
            client, snapshot, name_columns, known_pairs, engine, db_schema, budget,
        ))
        return inference.accepted_relationships(evidence), evidence

    async def _name_match_relations(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        name_columns: Sequence[inference.NameMatchColumn],
        known_pairs: set[frozenset[str]],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> list[dict[str, Any]]:
        """기본키 없는 테이블 쌍의 이름 일치 관계(origin ``name_match`` · plans/140 W1-2).

        ① 참여 테이블마다 컬럼 유일성 1회 → 유일하면 부모 후보. ② 부모 후보 P마다 다른 참여
        테이블 C에서 C → P 값 겹침 — `OVERLAP_MIN` 이상이면 채택. 둘 다 유일하면(1:1) 양방향을
        재 높은 쪽을 쓰고 같으면 이름순 앞 테이블이 부모다. 기존 후보가 있는 테이블 쌍은 건너뛴다.
        부모 후보가 없으면 참여 테이블마다 「부모 유일성 없음」 항목을, 상한·예산을 넘은 쌍은
        「후보 상한 초과」·「예산 초과」 항목을 남긴다(침묵 생략 금지).
        """
        evidence: list[dict[str, Any]] = []
        spent = 0  # 이름 일치 조회 수(유일성 + 값 겹침)

        async def counted(call: Any) -> dict[str, Any]:
            nonlocal spent
            before = budget.used
            result: dict[str, Any] = await call
            spent += budget.used - before
            return result

        for col in name_columns:
            members = dict(col.members)
            tables = [table for table, _column in col.members]
            if client is None:
                evidence.extend(
                    _relation_item(t, None, [members[t]], [], "name_match",
                                   error="DB 연결 없음 — 값 겹침을 확인하지 못했습니다")
                    for t in tables
                )
                continue
            unique: dict[str, bool | None] = {}
            unique_errors: dict[str, str | None] = {}
            for table in tables:
                if spent >= NAME_MATCH_MAX_QUERIES:
                    unique[table], unique_errors[table] = None, "후보 상한 초과"
                    continue
                result = await counted(probe.check_unique(
                    client, table=table, column=members[table], snapshot=snapshot, engine=engine,
                    db_schema=db_schema, budget=budget,
                ))
                unique[table], unique_errors[table] = result["unique"], result["error"]
            parents = [t for t in tables if unique[t]]
            if not parents:
                evidence.extend(
                    _relation_item(t, None, [members[t]], [], "name_match",
                                   error=unique_errors[t] or "부모 유일성 없음",
                                   unique_parent=unique[t])
                    for t in tables
                )
                continue
            column_spent = 0
            measured: set[frozenset[str]] = set()
            for parent in parents:
                for child in tables:
                    pair = frozenset((child, parent))
                    if child == parent or pair in known_pairs or pair in measured:
                        continue
                    measured.add(pair)
                    # 둘 다 유일하면 반대 방향도 잰다 — 부모를 이름순으로 돌므로 첫 방향의 부모가
                    # 이름순 앞이다(같으면 첫 방향을 쓴다)
                    directions = [(child, parent)] + ([(parent, child)] if unique[child] else [])
                    best: tuple[str, str, dict[str, Any]] | None = None
                    for c, p in directions:
                        if (column_spent >= NAME_MATCH_MAX_OVERLAPS_PER_COLUMN
                                or spent >= NAME_MATCH_MAX_QUERIES):
                            ov: dict[str, Any] = {"ratio": None, "sampled": None,
                                                  "error": "후보 상한 초과"}
                        else:
                            before = spent
                            ov = await counted(probe.check_overlap(
                                client, child=c, child_columns=[members[c]], parent=p,
                                parent_columns=[members[p]], snapshot=snapshot, engine=engine,
                                db_schema=db_schema, budget=budget, sample=OVERLAP_SAMPLE,
                            ))
                            column_spent += spent - before
                        if best is None or (ov["ratio"] or 0) > (best[2]["ratio"] or 0):
                            best = (c, p, ov)
                    assert best is not None
                    c, p, ov = best
                    item = _relation_item(
                        c, p, [members[c]], [members[p]], "name_match",
                        error=ov["error"], unique_parent=True,
                    )
                    item.update(overlap=ov["ratio"], sampled=ov["sampled"])
                    item["accepted"] = ov["ratio"] is not None and ov["ratio"] >= OVERLAP_MIN
                    evidence.append(item)
        return evidence

    async def _profile_columns(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        plan: _ColumnPlan,
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> dict[str, dict[str, Any]]:
        """범위 컬럼의 코드 후보·형식 후보 값 표본 → ``{key: {type, comment, candidate, code,
        values, distinct, truncated, profile, error}}``."""
        meta = plan.meta
        if client is None or not meta:
            for item in meta.values():
                item["error"] = "DB 연결 없음" if client is None else None
            return meta
        samples = await probe.sample_distinct(
            client, plan.code_keys, snapshot=snapshot, engine=engine, db_schema=db_schema,
            limit=inference.CODE_MAX_DISTINCT + 1, budget=budget,
        )
        samples.update(await probe.sample_distinct(
            client, plan.format_keys, snapshot=snapshot, engine=engine, db_schema=db_schema,
            limit=FORMAT_SAMPLE, budget=budget,
        ))
        for key, item in meta.items():
            sample = samples.get(key) or {}
            values = [str(v) for v in sample.get("values") or []]
            item["error"] = sample.get("error")
            item["distinct"] = None if item["error"] else len(values)
            item["truncated"] = bool(sample.get("truncated"))
            item["profile"] = inference.classify_values(values)
            if item["code"]:
                is_code_column = (
                    not item["error"] and 0 < len(values) <= inference.CODE_MAX_DISTINCT
                    and not sample.get("truncated")
                    and all(len(v) <= CODE_VALUE_MAX_LENGTH for v in values)
                )
                item["code"] = is_code_column
                item["values"] = values if is_code_column else []
        return meta

    async def _code_tables(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        plan: Sequence[tuple[str, str, str]],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> list[dict[str, Any]]:
        """공통코드 테이블 후보 ``(테이블, 코드 컬럼, 이름 컬럼)`` → (코드, 이름) 쌍."""
        if client is None:
            return []
        out: list[dict[str, Any]] = []
        for table, code_col, name_col in plan:
            pairs = await probe.sample_pairs(
                client, table, code_col, name_col, snapshot=snapshot, engine=engine,
                db_schema=db_schema, limit=CODE_TABLE_PAIRS, budget=budget,
            )
            labels: dict[str, set[str]] = {}
            for code, label in pairs["pairs"]:
                labels.setdefault(code, set()).add(label)
            out.append({
                "table": table, "code_column": code_col, "name_column": name_col,
                "labels": {c: sorted(ls)[0] for c, ls in labels.items() if len(ls) == 1},
                "ambiguous": sorted(c for c, ls in labels.items() if len(ls) > 1),
                "truncated": pairs["truncated"], "error": pairs["error"],
            })
        return out

    async def _comment_description_draft(
        self, source: str, comments: Mapping[str, str], by: str | None
    ) -> str | None:
        """컬럼 주석을 기존 「설명 초안」(출처 `comment` · LLM 0)으로 넣는다(주석 없으면 None)."""
        descriptions: dict[str, dict[str, str]] = {}
        for key, text in comments.items():
            table, _, column = key.rpartition(".")
            if table and column and text:
                descriptions.setdefault(table, {})[key] = text
        if not descriptions:
            return None
        draft = await self._store.add_description_draft(source, {
            "descriptions": descriptions,
            "synonyms": {},
            "failed_tables": [],
            "scope": "comment",
            "scope_tables": sorted(descriptions),
            "origin": "comment",
            "created_by": by,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
            "provider": None,
            "llm_calls": 0,
        })
        return str(draft.get("draft_id"))

    # --- P2 LLM 보조 ---

    async def run_asset_llm(
        self, source: str, draft_id: str, *, by: str | None, ctx: JobContext
    ) -> dict[str, Any]:
        """LLM 보조 잡 본문 — 쿼리 예시 · DB 전용 규칙 섹션 초안과 결정적 검증 결과를 초안에 싣는다.

        Raises:
            DraftNotApprovable: 초안 없음 · 대기 아님 · 환경 불일치
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        draft = await self._pending_asset_draft(source, draft_id)
        record = await self._store.load_snapshot(source) or {}
        snapshot: Mapping[str, Any] = record.get("snapshot") or {}
        engine = str(draft.get("engine") or "")
        limit_clause = row_limit_clause(engine, SQL_CHECK_LIMIT)
        comments = dict(await self._store.load_ddl_comments(source))
        comments.update(_draft_comments(draft))
        summary = build_asset_summary(snapshot, draft.get("assets") or {}, comments)
        llm = self._llm_factory()

        await ctx.progress(0, 3, "쿼리 예시 생성")
        examples_raw = await _ask(llm, QUERY_EXAMPLES_PROMPT.format(
            engine=engine, count=EXAMPLE_COUNT, summary=summary, limit_clause=limit_clause,
        ))
        await ctx.progress(1, 3, "DB 전용 규칙 섹션 생성")
        section_raw = await _ask(llm, PROMPT_SECTION_PROMPT.format(
            engine=engine, summary=summary, limit_clause=limit_clause,
            max_chars=SECTION_MAX_CHARS,
        ))
        await ctx.progress(2, 3, "결정적 검증·실행")
        async with self._client_factory(source) as client:
            schema_info = schema_dict_from_snapshot(snapshot)
            check = _SqlCheck(client, self._sql_checker, schema_info, engine, source)
            examples, example_checks = await _validate_examples(check, examples_raw)
            section, section_check = await _validate_section(check, section_raw, snapshot)
        # 잡이 도는 동안 바뀐 초안(테이블 정의 편집 등 — D-308)을 덮지 않도록
        # 최신 초안에 이 두 자산만 얹는다
        draft = await self._store.get_asset_draft(source, draft_id) or draft
        assets = dict(draft.get("assets") or {})
        validation = dict(draft.get("validation") or {})
        assets["query_examples"] = examples
        assets["prompt_template"] = {"section": section} if section_check["passed"] else None
        validation["query_examples"] = {"passed": bool(examples), "checks": example_checks}
        validation["prompt_template"] = section_check
        provider = self.provider_info()
        updated = await self._store.update_asset_draft(
            source, draft_id, assets=assets, validation=validation,
            llm_calls=int(draft.get("llm_calls") or 0) + 2,
            provider=provider, llm_at=_now_iso(), llm_by=by,
        )
        await ctx.progress(3, 3, "완료")
        logger.info(
            "자산 LLM 보조: source=%s, draft_id=%s, examples=%d, section_passed=%s, by=%s",
            source, draft_id, len(examples), section_check["passed"], by,
        )
        return {
            "source": source, "draft_id": draft_id, "provider": provider, "llm_calls": 2,
            "query_examples": len(examples), "prompt_template_passed": section_check["passed"],
            "status": (updated or {}).get("status"),
        }

    # --- 테이블 정의 (D-308 ① · plans/139 W3) ---

    async def import_table_definitions(
        self, source: str, text: str, *, by: str | None
    ) -> dict[str, Any]:
        """가져오기 — 관리자가 올린 YAML(시드 형식)을 결정적으로 검증해 자산 초안을 만든다(LLM 0).

        검증 실패 테이블은 오류 행으로 초안에 싣는다(승인 불가 — 편집하거나 고쳐 다시 가져온다).

        Raises:
            ValueError: 소스 이름 · 본문 상한 · YAML 문법 · 최상위 `tables:` 없음 · 스냅샷 없음
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        if len(text) > IMPORT_MAX_CHARS:
            raise ValueError(f"가져오기 본문은 {IMPORT_MAX_CHARS:,}자 이하여야 합니다")
        raw = parse_import_document(load_import_yaml(text))
        await self._require_store()
        record = await self._store.load_snapshot(source) or {}
        snap_tables: Mapping[str, Any] = (record.get("snapshot") or {}).get("tables") or {}
        if not snap_tables:
            raise ValueError("스냅샷이 없습니다 — O-2 스키마 수집 또는 DDL 등록을 먼저 하세요")
        rows, errors = _definition_rows(raw, _table_columns(snap_tables), ORIGIN_IMPORT)
        profile = (self._store.read_current_profile(source) or {}).get("profile") or {}
        raw_engine, _schema = await self._engine_and_schema(source, None)
        draft = await self._store.add_asset_draft(source, {
            "kind": "import",
            "scope": _scope_tables(None, profile, snap_tables),
            "engine": probe.engine_key(raw_engine),
            "snapshot_hash": record.get("hash"),
            "assets": {TABLE_DEFINITIONS_KEY: rows},
            "evidence": {},
            "validation": {TABLE_DEFINITIONS_KEY: {"errors": errors, "batches": []}},
            "description_draft_id": None,
            "llm_calls": 0,
            "provider": None,
            "created_by": by,
            "env": self.env,
            "local_sandbox": self.local_sandbox,
        })
        summary = _definition_summary(rows, errors)
        logger.info("테이블 정의 가져오기 초안: source=%s, draft_id=%s, %s, by=%s",
                    source, draft.get("draft_id"), summary, by)
        return {
            "source": source, "draft_id": draft.get("draft_id"), "env": self.env,
            "summary": summary,
        }

    async def estimate_table_definition_llm(
        self, source: str, draft_id: str, *, only_failed: bool = False
    ) -> dict[str, Any]:
        """테이블 정의 LLM 묶음 초안의 예상 호출 수 — 실행 전에 보여 준다(LLM 0).

        Raises:
            DraftNotApprovable: 초안 없음 · 대기 아님 · 환경 불일치
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        draft = await self._pending_asset_draft(source, draft_id)
        plan = await self._definition_plan(source, draft, only_failed=only_failed)
        return {
            "source": source, "draft_id": draft_id, "only_failed": only_failed,
            "calls": len(plan.batches),
            "tables": sum(len(b) for b in plan.batches),
            "batch_size": DEFINITION_BATCH_SIZE,
            "concurrency": max(1, int(self._config.schema_cache.admin_llm_concurrency)),
            "skipped": plan.skipped,
            "provider": self.provider_info(),
        }

    async def run_table_definition_llm(
        self, source: str, draft_id: str, *, only_failed: bool, by: str | None, ctx: JobContext
    ) -> dict[str, Any]:
        """테이블 정의 LLM 묶음 초안 잡 본문 — 묶음마다 1회 호출(동시성 `admin_llm_concurrency`).

        응답은 결정적 검증을 거쳐 초안 행이 된다. 호출·JSON 파싱 실패와 검증 실패는 오류 행으로
        남기고 묶음 상태(`ok`·`partial`·`failed`)를 기록한다 — `only_failed`면 실패 묶음만
        다시 돈다.

        Raises:
            DraftNotApprovable: 초안 없음 · 대기 아님 · 환경 불일치
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        await self._require_store()
        draft = await self._pending_asset_draft(source, draft_id)
        plan = await self._definition_plan(source, draft, only_failed=only_failed)
        total = len(plan.batches)
        if not total:
            await ctx.progress(0, 0, "대상 없음")
            return {
                "source": source, "draft_id": draft_id, "llm_calls": 0, "tables": 0,
                "failed_batches": 0, "skipped": plan.skipped,
                "note": "LLM 초안을 만들 테이블이 없습니다",
            }
        context = await self._definition_context(source, draft, plan)
        llm = self._llm_factory()
        semaphore = asyncio.Semaphore(
            max(1, int(self._config.schema_cache.admin_llm_concurrency))
        )
        finished = 0
        await ctx.progress(0, total, "테이블 정의 LLM 초안")

        async def run(tables: list[str]) -> tuple[dict[str, Any], str | None]:
            nonlocal finished
            async with semaphore:
                outcome = await _definition_batch(llm, tables, context)
            finished += 1
            await ctx.progress(finished, total, f"묶음 {finished}/{total}")
            return outcome

        outcomes = await asyncio.gather(*(run(batch) for batch in plan.batches))

        # 잡이 도는 사이의 편집·반려를 반영한다 — 최신 초안에 얹고, LLM 행(또는 빈 자리)만 바꾼다
        latest = await self._pending_asset_draft(source, draft_id)
        assets = dict(latest.get("assets") or {})
        validation = dict(latest.get("validation") or {})
        rows = dict(assets.get(TABLE_DEFINITIONS_KEY) or {})
        report = dict(validation.get(TABLE_DEFINITIONS_KEY) or {})
        errors = {str(k): list(v) for k, v in (report.get("errors") or {}).items()}
        table_columns = _table_columns(plan.snap_tables)
        records = [
            _merge_definition_batch(rows, errors, tables, raw, error, table_columns)
            for tables, (raw, error) in zip(plan.batches, outcomes)
        ]
        kept = [
            b for b in report.get("batches") or [] if only_failed and b.get("status") == "ok"
        ]
        assets[TABLE_DEFINITIONS_KEY] = rows
        validation[TABLE_DEFINITIONS_KEY] = {**report, "errors": errors, "batches": kept + records}
        provider = self.provider_info()
        await self._store.update_asset_draft(
            source, draft_id, assets=assets, validation=validation,
            llm_calls=int(latest.get("llm_calls") or 0) + total, provider=provider,
            definitions_llm_at=_now_iso(), definitions_llm_by=by,
        )
        failed = sum(1 for r in records if r["status"] != "ok")
        await ctx.progress(total, total, "완료")
        logger.info(
            "테이블 정의 LLM 초안: source=%s, draft_id=%s, calls=%d, failed_batches=%d, "
            "only_failed=%s, by=%s", source, draft_id, total, failed, only_failed, by,
        )
        return {
            "source": source, "draft_id": draft_id, "provider": provider, "llm_calls": total,
            "tables": sum(len(b) for b in plan.batches), "failed_batches": failed,
            "skipped": plan.skipped, "summary": _definition_summary(rows, errors),
        }

    async def _definition_plan(
        self, source: str, draft: Mapping[str, Any], *, only_failed: bool
    ) -> _DefinitionPlan:
        """LLM 대상 묶음 — 범위 중 테이블 주석 · 초안의 비-LLM 정의 · 승인된 사람 정의가 없는
        테이블(`only_failed`면 실패 묶음 가운데 아직 실패한 LLM 행만)."""
        record = await self._store.load_snapshot(source) or {}
        snap_tables: Mapping[str, Any] = (record.get("snapshot") or {}).get("tables") or {}
        comments = dict(await self._store.load_ddl_comments(source))
        comments.update(_draft_comments(draft))
        rows: Mapping[str, Any] = (draft.get("assets") or {}).get(TABLE_DEFINITIONS_KEY) or {}
        report: Mapping[str, Any] = (
            (draft.get("validation") or {}).get(TABLE_DEFINITIONS_KEY) or {}
        )
        errors: Mapping[str, Any] = report.get("errors") or {}
        skipped = {"comment": 0, "defined": 0, "manual": 0}
        if only_failed:
            batches: list[list[str]] = []
            for batch in report.get("batches") or []:
                if not isinstance(batch, Mapping) or batch.get("status") == "ok":
                    continue
                still = [
                    t for t in batch.get("tables") or []
                    if t in snap_tables and _row_origin(rows.get(t)) == ORIGIN_LLM
                    and (t in errors or t not in rows)
                ]
                if still:
                    batches.append(still)
            return _DefinitionPlan(batches, skipped, snap_tables, comments)
        profile = (self._store.read_current_profile(source) or {}).get("profile") or {}
        approved = profile.get(TABLE_DEFINITIONS_KEY)
        manual = {
            bare_name(str(t)) for t, item in (approved or {}).items()
            if isinstance(item, Mapping) and item.get("origin") == ORIGIN_MANUAL
        } if isinstance(approved, Mapping) else set()
        targets: list[str] = []
        scope = [t for t in draft.get("scope") or [] if t in snap_tables] or list(snap_tables)
        for table in scope:
            if comments.get(table):
                skipped["comment"] += 1
            elif _row_origin(rows.get(table)) != ORIGIN_LLM:
                skipped["defined"] += 1
            elif bare_name(table) in manual:
                skipped["manual"] += 1
            else:
                targets.append(table)
        return _DefinitionPlan(definition_batches(targets), skipped, snap_tables, comments)

    async def _definition_context(
        self, source: str, draft: Mapping[str, Any], plan: _DefinitionPlan
    ) -> _DefinitionContext:
        """LLM 입력 재료 — 컬럼 설명(승인 설명 → DDL 주석) · 코드 라벨 · 승인 관계 · DB 설명."""
        descriptions: dict[str, str] = {}
        try:
            descriptions.update(await self._cache_mgr.get_descriptions(source) or {})
        except Exception as e:  # noqa: BLE001 — 설명이 없어도 이름·타입으로 만든다
            logger.warning("컬럼 설명 조회 실패 — 설명 없이 진행 (source=%s): %s", source, e)
        for key, text in plan.comments.items():
            descriptions.setdefault(key, text)
        db_description = ""
        try:
            db_description = str(await self._cache_mgr.get_db_description(source) or "")
        except Exception as e:  # noqa: BLE001 — DB 설명이 없으면 레지스트리 설명을 쓴다
            logger.warning("DB 설명 조회 실패 (source=%s): %s", source, e)
        if not db_description:
            registry = self._registry()
            entry = registry.get(source) if registry is not None else None
            db_description = str(getattr(entry, "description", "") or "")
        profile = (self._store.read_current_profile(source) or {}).get("profile") or {}
        labels: dict[str, Any] = dict((draft.get("assets") or {}).get("code_labels") or {})
        profile_labels = profile.get("code_labels")
        if isinstance(profile_labels, Mapping):
            labels.update(profile_labels)
        relationships = [
            (str(r["from"]), str(r["to"])) for r in profile.get("relationships") or []
            if isinstance(r, Mapping) and isinstance(r.get("from"), str)
            and isinstance(r.get("to"), str)
        ]
        return _DefinitionContext(
            snap_tables=plan.snap_tables,
            descriptions={k.casefold(): str(v) for k, v in descriptions.items() if v},
            labels={k.casefold(): v for k, v in labels.items() if isinstance(v, Mapping)},
            relationships=relationships,
            db_description=db_description,
        )

    async def update_table_definitions(
        self,
        source: str,
        draft_id: str,
        edits: Mapping[str, Mapping[str, Any]],
        *,
        by: str | None,
    ) -> dict[str, Any]:
        """검토 화면 편집 저장 — 고친 행은 출처 `manual`로 다시 검증해 초안에 싣는다.

        편집 필드는 `EDITABLE_DEFINITION_FIELDS`이고, 보내지 않은 필드는 기존 행 값을 쓴다. 검증에
        실패하면 아무것도 저장하지 않는다.

        Raises:
            ValueError: 빈 편집 · 초안에 없는 테이블 · 검증 실패(사유 포함)
            DraftNotApprovable: 초안 없음 · 대기 아님 · 환경 불일치
            StructureStoreUnavailable: Redis 미연결
        """
        validate_db_id(source)
        if not edits:
            raise ValueError("편집할 테이블 정의가 없습니다")
        await self._require_store()
        draft = await self._pending_asset_draft(source, draft_id)
        assets = dict(draft.get("assets") or {})
        rows = dict(assets.get(TABLE_DEFINITIONS_KEY) or {})
        unknown = sorted(t for t in edits if t not in rows)
        if unknown:
            raise ValueError(f"초안에 없는 테이블 정의: {', '.join(unknown[:10])}")
        candidates: dict[str, dict[str, Any]] = {}
        for table, fields in edits.items():
            base = rows[table] if isinstance(rows[table], Mapping) else {}
            row = {k: v for k, v in base.items() if k in FIELDS}
            row.update({k: fields[k] for k in EDITABLE_DEFINITION_FIELDS if k in fields})
            row["origin"] = ORIGIN_MANUAL
            candidates[table] = row
        record = await self._store.load_snapshot(source) or {}
        valid, problems = validate_table_definitions(
            candidates, _table_columns((record.get("snapshot") or {}).get("tables") or {}),
        )
        if problems:
            detail = " / ".join(f"{t}: {'; '.join(p)}" for t, p in problems.items())
            raise ValueError(f"편집 검증 실패 — {detail[:2000]}")
        validation = dict(draft.get("validation") or {})
        report = dict(validation.get(TABLE_DEFINITIONS_KEY) or {})
        errors = {str(k): list(v) for k, v in (report.get("errors") or {}).items()}
        for table in valid:
            errors.pop(table, None)
        rows.update(valid)
        assets[TABLE_DEFINITIONS_KEY] = rows
        validation[TABLE_DEFINITIONS_KEY] = {**report, "errors": errors}
        await self._store.update_asset_draft(
            source, draft_id, assets=assets, validation=validation,
            definitions_edited_at=_now_iso(), definitions_edited_by=by,
        )
        logger.info("테이블 정의 편집: source=%s, draft_id=%s, tables=%d, by=%s",
                    source, draft_id, len(valid), by)
        return {
            "source": source, "draft_id": draft_id, "updated": sorted(valid),
            "summary": _definition_summary(rows, errors),
        }

    # --- 승인 · 반려 · 되돌리기 ---

    async def _pending_asset_draft(self, source: str, draft_id: str) -> dict[str, Any]:
        draft = await self._store.get_asset_draft(source, draft_id)
        if draft is None:
            raise DraftNotApprovable("not_found", f"자산 초안이 없습니다: {draft_id}")
        if draft.get("status") != "pending":
            raise DraftNotApprovable(
                "not_pending", f"대기 중인 초안이 아닙니다: {draft.get('status')}"
            )
        if _norm_env(draft.get("env")) != _norm_env(self.env):
            raise DraftNotApprovable(
                "env_mismatch", f"다른 환경에서 만든 초안입니다(초안 env={draft.get('env')})",
            )
        return draft

    async def approve_asset_draft(
        self,
        source: str,
        draft_id: str,
        *,
        include: list[str],
        allowed_tables: list[str] | None,
        by: str | None,
        reason: str,
        table_definition_tables: list[str] | None = None,
    ) -> dict[str, Any]:
        """고른 자산만 적용한다 — 프로필 키(사람 값 보존 병합) · 시드 · DB 전용 섹션.

        테이블 정의는 `table_definition_tables`로 고른 테이블만 적용한다(없으면 초안 행 전부). 고른
        행에 검증 실패 행이 있으면 `validation_failed`(409)다.

        Raises:
            ValueError: 모르는 자산 · 빈 포함 목록 · 초안에 없는 테이블 정의
            DraftNotApprovable: 초안 상태·환경 · 자산 없음(`not_available`) · 검증 실패
        """
        validate_db_id(source)
        await self._require_store()
        chosen = list(dict.fromkeys(include or []))
        unknown = sorted(set(chosen) - set(ASSET_KINDS))
        if unknown or not chosen:
            detail = f"(모르는 자산: {', '.join(unknown)})" if unknown else "(비어 있음)"
            raise ValueError(f"include는 {', '.join(ASSET_KINDS)}의 부분집합이어야 합니다{detail}")
        draft = await self._pending_asset_draft(source, draft_id)
        assets: Mapping[str, Any] = draft.get("assets") or {}
        validation: Mapping[str, Any] = draft.get("validation") or {}
        explicit_tables = "allowed_tables" in chosen and allowed_tables is not None
        for kind in chosen:
            if _is_empty(assets.get(kind)) and not (kind == "allowed_tables" and explicit_tables):
                raise DraftNotApprovable("not_available", f"초안에 '{kind}' 자산이 없습니다")
            if kind in ("query_examples", "prompt_template") and not (
                validation.get(kind) or {}
            ).get("passed"):
                raise DraftNotApprovable(
                    "validation_failed", f"'{kind}' 결정적 검증을 통과하지 못했습니다"
                )

        # 쓰기 전에 입력을 모두 확정한다(검증 실패로 일부만 쓰는 일이 없게)
        record = await self._store.load_snapshot(source) or {}
        snap_tables = (record.get("snapshot") or {}).get("tables") or {}
        selected_tables: list[str] | None = None
        if explicit_tables:
            selected_tables = [t for t in dict.fromkeys(allowed_tables or []) if t in snap_tables]
            if not selected_tables:
                raise ValueError("allowed_tables에 스냅샷 테이블이 하나도 없습니다")
        draft_meta: dict[str, Any] = {k: assets.get(k) for k in PROFILE_ASSETS if k in chosen}
        if selected_tables is not None:
            draft_meta["allowed_tables"] = selected_tables
        if "code_values" in chosen and assets.get("code_labels"):
            draft_meta["code_labels"] = assets["code_labels"]
        approved_definitions: dict[str, Any] = {}
        if TABLE_DEFINITIONS_KEY in chosen:
            approved_definitions = _approvable_definitions(
                assets.get(TABLE_DEFINITIONS_KEY) or {},
                (validation.get(TABLE_DEFINITIONS_KEY) or {}).get("errors") or {},
                table_definition_tables, _table_columns(snap_tables),
            )
            draft_meta[TABLE_DEFINITIONS_KEY] = approved_definitions

        steps: list[tuple[str, Callable[[], Any]]] = []
        if draft_meta:
            steps.append(("profile", lambda: self._apply_profile_assets(
                source, draft_meta, selected_tables, draft_id, by, reason,
            )))
        if "seeds" in chosen:
            steps.append(("seeds", lambda: self._apply_seeds(
                source, assets["seeds"], draft_id, by, reason,
            )))
        if "prompt_template" in chosen:
            steps.append(("prompt_template", lambda: self._apply_prompt_template(
                source, draft, assets["prompt_template"]["section"], by, reason,
            )))
        applied: dict[str, Any] = {}
        failed: list[str] = []
        for name, run in steps:
            try:
                applied[name] = await run()
            except Exception as e:
                if not applied:
                    raise  # 아무것도 쓰지 않았으면 그대로 실패한다(부분 적용 없음)
                # 이미 쓴 자산이 있으면 멈추지 않고 사유를 남긴다 — 초안·감사에 부분 적용이 보이게
                logger.warning("자산 적용 일부 실패: source=%s, asset=%s: %s", source, name, e)
                applied[name] = {"error": f"{type(e).__name__}: {e}"}
                failed.append(name)
        status = "partially_applied" if failed else "approved"
        extra: dict[str, Any] = (
            {"approved_table_definitions": list(approved_definitions)}
            if TABLE_DEFINITIONS_KEY in chosen else {}
        )
        updated = await self._store.update_asset_draft(
            source, draft_id, status=status, approved_by=by, approved_at=_now_iso(),
            approve_reason=reason, approved_assets=chosen, applied=applied, apply_errors=failed,
            **extra,
        )
        logger.info("자산 초안 승인: source=%s, draft_id=%s, assets=%s, status=%s, by=%s",
                    source, draft_id, chosen, status, by)
        return {"source": source, "draft": updated, "applied": applied, "partial": bool(failed)}

    async def _apply_profile_assets(
        self,
        source: str,
        draft_meta: dict[str, Any],
        selected_tables: list[str] | None,
        draft_id: str,
        by: str | None,
        reason: str,
    ) -> dict[str, Any]:
        """프로필 자산 적용 — 사람 값 보존 병합 · 관리자가 표에서 고른 조회 대상은 그대로 쓴다.

        기존 값을 보존해 바뀌지 않은 자산은 `unchanged`로 알린다(조용히 성공처럼 보이지 않게).
        """
        current = self._store.read_current_profile(source)
        before = (current or {}).get("profile")
        merged = merge_profile(before, draft_meta, local_sandbox=self.local_sandbox)
        if selected_tables is not None:
            merged["allowed_tables"] = list(selected_tables)
        diff = profile_field_diff(before, merged)
        result: dict[str, Any] = {"ver": None, "changes": len(diff)}
        if diff:
            entry = await self._store.apply_profile(
                source, merged, by=by, reason=reason or "자산 자동 생성 승인", env=self.env,
                draft_id=draft_id, field_diff=diff,
            )
            result["ver"] = entry.get("ver")
        base = before if isinstance(before, Mapping) else {}
        unchanged = [
            k for k in draft_meta if k in base and base.get(k) == merged.get(k)
        ]
        if unchanged:
            result["unchanged"] = unchanged
            result["note"] = "기존 값을 보존해 바뀌지 않은 자산이 있습니다(사람이 쓴 값 우선)"
        if TABLE_DEFINITIONS_KEY in draft_meta:
            applied_defs = merged.get(TABLE_DEFINITIONS_KEY)
            by_bare = {
                bare_name(str(k)): v for k, v in applied_defs.items()
            } if isinstance(applied_defs, Mapping) else {}
            # 더 높은 출처의 base 정의를 보존한 테이블 → 보존한 출처(plans/151 W1)
            kept = {
                t: base_definition_origin(by_bare.get(bare_name(t)))
                for t, item in draft_meta[TABLE_DEFINITIONS_KEY].items()
                if by_bare.get(bare_name(t)) != item
            }
            if kept:
                result["table_definitions_kept"] = kept
            kept_manual = [t for t, origin in kept.items() if origin == ORIGIN_MANUAL]
            if kept_manual:  # 호환 키 — `manual` 보존분만
                result["table_definitions_kept_manual"] = kept_manual
        return result

    async def _apply_prompt_template(
        self, source: str, draft: Mapping[str, Any], section: str, by: str | None, reason: str
    ) -> dict[str, Any]:
        """DB 전용 규칙 섹션 파일 적용."""
        draft_id = str(draft.get("draft_id"))
        entry = await self._assets.apply(
            source, "prompt_template", {
                "db_id": source,
                "section": section,
                "generated": {
                    "draft_id": draft_id, "snapshot_hash": draft.get("snapshot_hash"),
                    "provider": draft.get("provider"), "approved_by": by,
                },
            },
            by=by, reason=reason or "DB 전용 규칙 섹션 승인", env=self.env, draft_id=draft_id,
            local_sandbox=self.local_sandbox,
            header=self._asset_header(source, "DB 전용 규칙 섹션", draft_id, by),
        )
        return {"ver": entry["ver"]}

    def _asset_header(self, source: str, label: str, draft_id: str, by: str | None) -> str:
        lines = []
        if self.local_sandbox:
            lines.append(LOCAL_SANDBOX_HEADER)
        lines += [
            f"# {label} — {source} (D-294 스키마 자산 자동 생성 · 관리자 승인 적용 · 직접 편집하면 "
            "다음 적용 때 외부 변경으로 보관된다)",
            f"# 초안 {draft_id} · {_now_iso()} · {' '.join(str(by or '-').split())} · "
            f"env={' '.join(self.env.split())}",
        ]
        return "\n".join(lines)

    async def _apply_seeds(
        self, source: str, seeds: Mapping[str, Any], draft_id: str, by: str | None, reason: str
    ) -> dict[str, Any]:
        """시드 파일 적용(사람 단어 보존 합집합) + DB별 `column_synonyms`만 Redis 적재."""
        current = self._assets.read_current(source, "seeds")
        base: Mapping[str, Any] = (current or {}).get("data") or {}
        synonyms: dict[str, list[str]] = {
            k: list(v) for k, v in (base.get("column_synonyms") or {}).items()
            if isinstance(v, list)
        }
        for key, words in (seeds.get("column_synonyms") or {}).items():
            existing = synonyms.setdefault(key, [])
            existing.extend(w for w in words if w not in existing)
        values: dict[str, dict[str, Any]] = {
            k: dict(v) for k, v in (base.get("column_values") or {}).items() if isinstance(v, dict)
        }
        for key, mapping in (seeds.get("column_values") or {}).items():
            target = values.setdefault(key, {})
            for word, cond in mapping.items():
                target.setdefault(word, cond)
        data = {
            **{k: v for k, v in base.items() if k not in ("column_synonyms", "column_values")},
            "version": base.get("version") or "1.0",
            "db_id": source,
            "source_tag": base.get("source_tag") or "operator",
            "column_synonyms": synonyms,
            "column_values": values,
        }
        entry = await self._assets.apply(
            source, "seeds", data, by=by, reason=reason or "유사어 시드 승인", env=self.env,
            draft_id=draft_id, local_sandbox=self.local_sandbox,
            header=self._asset_header(source, "유사어 시드", draft_id, by),
        )
        result: dict[str, Any] = {"ver": entry["ver"]}
        redis_cache = self._store.redis_cache
        if redis_cache is None:
            result["error"] = "Redis 캐시가 없어 시드를 적재하지 못했습니다(파일은 기록됨)"
            return result
        # 기존 O-7 로더 그대로 — DB별 column_synonyms(operator) + DB 공용 사전 column_values
        # (폴스타·ITAM 공유 · plans/132 G-13 사용자 확정 2026-10-01)
        from src.schema_cache.synonym_loader import SynonymLoader

        loaded = await SynonymLoader(redis_cache).load_seed_yaml(
            str(self._assets.path(source, "seeds"))
        )
        self._cache_mgr.invalidate_memory_cache(source)
        result.update({
            "status": loaded.status, "column_synonyms_loaded": loaded.columns_loaded,
            "column_values_loaded": loaded.column_values_loaded, "errors": list(loaded.errors),
        })
        return result

    async def reject_asset_draft(
        self, source: str, draft_id: str, *, by: str | None, reason: str
    ) -> dict[str, Any]:
        """자산 초안을 반려한다."""
        validate_db_id(source)
        await self._pending_asset_draft(source, draft_id)
        updated = await self._store.update_asset_draft(
            source, draft_id, status="rejected", rejected_by=by, rejected_at=_now_iso(),
            reject_reason=reason,
        )
        return {"source": source, "draft": updated}

    async def rollback_asset(
        self, source: str, kind: str, ver: int, *, by: str | None, reason: str
    ) -> dict[str, Any]:
        """시드·DB 전용 섹션 파일을 이전 버전 원문으로 되돌린다(프로필은 기존 「버전 이력」)."""
        validate_db_id(source)
        if kind not in ("seeds", "prompt_template"):
            raise ValueError("되돌리기는 seeds · prompt_template만 됩니다(프로필은 버전 이력에서)")
        entry = await self._assets.rollback(
            source, kind, ver, by=by, reason=reason or "자산 되돌리기", env=self.env,
        )
        return {"source": source, "kind": kind, "version": entry}


# ──────────────────────────────────────────────
# 조립 · 요약 · 검증 (순수 함수 또는 클라이언트만 받는 함수)
# ──────────────────────────────────────────────


def _norm_env(value: Any) -> str:
    return str(value or "").strip().rstrip("/").lower()


def _is_empty(value: Any) -> bool:
    return value is None or (isinstance(value, (list, dict, str)) and not value)


# 아래 셋은 이 모듈 상수(`MAX_*` · `NAME_MATCH_*`)를 읽는다 — 테스트가 이 모듈에서 상한을 바꾸므로
# 옮기지 않는다(D-337 ② · plans/166 I3)


def _code_table_candidates(
    snapshot: Mapping[str, Any], comments: Mapping[str, str], definitions: Any = None
) -> list[tuple[str, str, str]]:
    """공통코드 테이블 후보 ``(테이블, 코드 컬럼, 이름 컬럼)`` — 이름·주석에 코드 단서가 있고 코드
    컬럼(기본키의 마지막 코드 컬럼)과 이름 컬럼을 가진 테이블(최대 `MAX_CODE_TABLES`) 뒤에
    테이블 정의 기준·코드 쌍(`_defined_code_table_candidates`)을 붙인다."""
    out: list[tuple[str, str, str]] = []
    for table, data in (snapshot.get("tables") or {}).items():
        if len(out) >= MAX_CODE_TABLES:
            break
        if not (_CODE_TABLE_HINT_RE.search(bare_name(table))
                or _CODE_TABLE_HINT_RE.search(comments.get(table) or "")):
            continue
        columns = list((data.get("columns") or {}).items())
        # 기본키의 마지막 코드 컬럼(앞쪽은 보통 그룹 코드다)
        code_col = next((c for c, a in reversed(columns) if (a or {}).get("primary_key")
                         and inference.is_code_candidate(c, str((a or {}).get("type")),
                                                         comments.get(f"{table}.{c}"))), None)
        name_col = next((c for c, _a in columns if _NAME_COL_RE.search(c)
                         or _NAME_COL_RE.search(comments.get(f"{table}.{c}") or "")), None)
        if not code_col or not name_col or code_col == name_col:
            continue
        out.append((table, code_col, name_col))
    out.extend(_defined_code_table_candidates(snapshot, definitions, {t for t, _c, _n in out}))
    return out


def _defined_code_table_candidates(
    snapshot: Mapping[str, Any], definitions: Any, taken: set[str]
) -> list[tuple[str, str, str]]:
    """테이블 정의 `kind`가 기준·코드인 테이블의 코드/이름 컬럼 쌍(plans/143 W7 — 최대
    `MAX_DEFINED_CODE_PAIRS`). 기본키를 보지 않는다 — 선언 기본키 0인 DB용. 이미 후보인 테이블은
    건너뛴다. 정의가 없거나 그 `kind`가 없으면 빈 목록(종전 그대로)."""
    kinds = inference.defined_code_tables(definitions)
    out: list[tuple[str, str, str]] = []
    if not kinds:
        return out
    for table, data in (snapshot.get("tables") or {}).items():
        if table in taken or bare_name(table) not in kinds:
            continue
        columns = [(c, str((a or {}).get("type") or ""))
                   for c, a in (data.get("columns") or {}).items()]
        for code_col, name_col in inference.code_name_pairs(columns):
            if len(out) >= MAX_DEFINED_CODE_PAIRS:
                return out
            out.append((table, code_col, name_col))
    return out


def _requested_queries(
    catalog: int,
    relation_candidates: Sequence[inference.InferredRelation],
    name_columns: Sequence[inference.NameMatchColumn],
    column_plan: _ColumnPlan,
    code_table_plan: Sequence[tuple[str, str, str]],
) -> int:
    """잡이 쓸 조회 수의 상한 추정(plans/140 W1-3).

    카탈로그 + 관계 값 겹침(같은 기본키 군은 반대 방향 포함) + 이름 일치(유일성 + 컬럼당 겹침
    상한 · 전체 상한) + 코드·형식 후보 + 공통코드 테이블.

    이름 일치는 관계 단계(코드·형식 표본보다 먼저)에서 돌므로 상한으로 세야 뒤 단계 예산을 잠식하지
    않는다."""
    overlaps = sum(
        0 if rel.origin == "declared" else 2 if rel.origin == "same_key" else 1
        for rel in relation_candidates
    )
    name_match = sum(
        len(col.members) + min(
            NAME_MATCH_MAX_OVERLAPS_PER_COLUMN, len(col.members) * (len(col.members) - 1)
        )
        for col in name_columns
    )
    return (
        catalog + overlaps + min(name_match, NAME_MATCH_MAX_QUERIES)
        + len(column_plan.meta) + len(code_table_plan)
    )
