"""관리자 「DB 구조」 스키마 자산 자동 생성 서비스 (D-294 · plans/133 W4).

폴스타 프로필·시드·전용 프롬프트에 해당하는 자산을 신규 DB에도 만든다 — 스키마 스냅샷(O-2 MCP 수집 ·
D-292 DDL 등록)과 **읽기 전용 데이터 조회**(`schema_probe`)로 결정적으로 만들고, 결정적으로 만들 수
없는 것(쿼리 예시 · DB 전용 규칙 섹션)만 LLM 초안을 쓴 뒤 결정적 검증과 실제 실행으로 거른다.

- **P1 프로파일링 잡(`run_asset_profile` · LLM 0)**: 카탈로그 주석·행 수 → 관계(선언 FK · 기본키
  일치 추론 · 같은 기본키 군 — 추론은 값 겹침 ≥ `OVERLAP_MIN`만 채택) → 코드 컬럼(`DISTINCT` ≤ 50) ·
  값 형식 → 코드 라벨(주석 열거 · 공통코드 테이블) · 식별 키 · 쿼리 규칙 · 조회 대상 후보 → **자산
  초안**. 주석이 있으면 **설명 초안**(출처 `comment`)도 기존 「설명 초안 검토·적용」에 넣는다. DB에
  닿지 못하면 스키마만으로 만들 수 있는 것(선언 관계 · 주석)만 만든다(`offline`).
- **P2 LLM 보조 잡(`run_asset_llm`)**: 초안 자산 요약 → 쿼리 예시(안전성 · 실제 실행 성공만) · DB
  전용 규칙 섹션(중괄호 금지 · 식별자 실존 · 섹션 안 SQL 실행 성공 · 길이 상한).
- **승인(`approve_asset_draft`)**: 자산별 포함 목록 → 프로필 키는 `merge_profile`(사람 값 보존) →
  `StructureStore.apply_profile` · 시드·전용 섹션은 `AssetFileStore`(버전 · 되돌리기). 시드는 기존
  O-7 로더(`SynonymLoader.load_seed_yaml`)로 적재한다 — DB별 `column_synonyms` + DB 공용 사전
  `column_values`(폴스타·ITAM 공유 · `plans/132` G-13 사용자 확정).
- **테이블 정의(`table_definitions` · D-305 ① · plans/138 W3)**: 테이블마다 「관리하는 정보」.
  초안 경로 3가지 — 가져오기(`import_table_definitions` · 시드 YAML 형식) · 주석(P1이 테이블
  주석을 `comment`
  정의로) · LLM 묶음 초안(`run_table_definition_llm` · 주석 없는 테이블만 · 군 접두 단위 묶음 ·
  `admin_llm_concurrency` · 실행 전 `estimate_table_definition_llm` · 실패 묶음만 재실행).
  형식·검증은
  `src.domain.table_definitions`가 정하고, 검증 실패 행은 승인할 수 없다(테이블 단위 선택 승인 ·
  사람 편집 `manual` 보존 병합 · 되돌리기는 프로필 「버전 이력」).

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml  # type: ignore[import-untyped]
from langchain_core.messages import HumanMessage

from src.domain import schema_inference as inference
from src.domain.profile_merge import merge_profile, profile_field_diff
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import (
    FIELDS,
    IMPORT_MAX_CHARS,
    KEY_COLUMNS_MAX,
    KINDS,
    MANAGES_MAX_CHARS,
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
    TABLE_DEFINITIONS_PROMPT,
)
from src.schema_cache import schema_probe as probe
from src.schema_cache.asset_store import AssetFileStore
from src.schema_cache.db_structure_service import (
    LOCAL_SANDBOX_HEADER,
    AdminServiceBase,
    DraftNotApprovable,
    _now_iso,
    schema_dict_from_snapshot,
)
from src.schema_cache.structure_analysis import parse_llm_json, validate_sample_sql
from src.schema_cache.structure_store import validate_db_id
from src.security.sql_guard import SQLGuard
from src.utils.json_extract import coerce_content_text
from src.utils.sql_dialect import row_limit_clause

if TYPE_CHECKING:
    from src.schema_cache.admin_jobs import JobContext

logger = logging.getLogger(__name__)

#: LLM SQL 검증기 ``(sql, schema_info, engine, db_id) → 오류 목록`` — 질의 경로 `validate_sql`(참조
#: 테이블·컬럼 실존 포함)은 application 계층이라 이 모듈이 import하지 않고 조립부(API)가 주입한다.
#: ``db_id``는 DB별 검증 정책(한글 식별자 허용 — plans/137)을 조립부가 레지스트리에서 해석하는 데 쓴다
SqlChecker = Callable[[str, Mapping[str, Any], str, str], list[str]]

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
#: 추론 관계 채택 하한(자식 키 표본 중 부모에 있는 비율)
OVERLAP_MIN = 0.9
#: 공통 컬럼 제외를 적용할 최소 테이블 수(그보다 적으면 함께 있는 컬럼이 전부 공통이 된다 —
#: 2테이블 로컬 샌드박스 실측 2026-10-01)
MIN_TABLES_FOR_COMMON = 5
OVERLAP_SAMPLE = 200
FORMAT_SAMPLE = 200
CODE_VALUE_MAX_LENGTH = 40
CODE_TABLE_PAIRS = 1000
MAX_CODE_TABLES = 10
#: 공통코드 라벨 채택 하한(코드 컬럼 값 중 코드 테이블에 있는 비율)
CODE_TABLE_COVERAGE = 0.9
EXAMPLE_COUNT = 5
SECTION_MAX_CHARS = 6000
SUMMARY_MAX_CHARS = 12000
SQL_CHECK_LIMIT = 50
#: 테이블 정의 LLM 묶음 크기(군 접두 단위 · D-305)
DEFINITION_BATCH_SIZE = 10
#: 테이블 정의 LLM 입력의 컬럼 설명 길이 상한(자)
DEFINITION_DESCRIPTION_MAX_CHARS = 100
#: 테이블 정의 LLM 입력의 컬럼당 코드 라벨 수 상한
DEFINITION_LABELS_MAX = 10
#: 검토 화면에서 고칠 수 있는 정의 필드(고친 행은 출처 `manual`)
EDITABLE_DEFINITION_FIELDS: tuple[str, ...] = ("manages", "notes", "kind", "key_columns")

_STRING_TYPE_RE = re.compile(
    r"^(char|character|varchar|character varying|nchar|nvarchar|varchar2|text|graphic|"
    r"vargraphic|tinytext|mediumtext|longtext|clob)\b",
    re.IGNORECASE,
)
_INTEGER_TYPE_RE = re.compile(r"^(tinyint|smallint|int|integer|mediumint|bigint)\b", re.IGNORECASE)
_DATE_HINT_RE = re.compile(r"(?i)(ymd|ymdhms|dt|date|dttm|day)$|일자|일시|년월일|날짜")
_HOST_IP_HINT_RE = re.compile(r"(?i)host|hst|(^|_|[a-z])ip|호스트|아이피|서버\s*명")
_NAME_COL_RE = re.compile(r"(?i)(nm|name)$|명$|이름")
_CODE_TABLE_HINT_RE = re.compile(r"(?i)code|(^|_)cd|코드")
# 섹션의 코드 펜스 — 언어 표기와 상관없이 전부 SQL로 보고 검사한다(표기 없는 펜스로 검증 우회 금지)
_FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
MAX_SECTION_SQL_BLOCKS = 5
# LLM SQL을 실행하기 전에 막는 부수효과·서버 조작 함수(읽기 전용 계정에서도 일부는 동작한다)
_SIDE_EFFECT_FUNC_RE = re.compile(
    r"(?i)\b(pg_terminate_backend|pg_cancel_backend|pg_sleep\w*|pg_(?:try_)?advisory\w*|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|pg_reload_conf|pg_rotate_logfile|"
    r"lo_import|lo_export|lo_unlink|set_config|nextval|setval|dblink\w*|get_lock|release_lock|"
    r"is_free_lock|sys_exec|sys_eval|sleep|benchmark|load_file)\s*\("
)
# 섹션 백틱 안에 쓰여도 되는 SQL 낱말(식별자 실존 검증에서 제외)
_SQL_WORDS = frozenset({
    "select", "from", "where", "join", "left", "right", "inner", "outer", "on", "and", "or",
    "not", "in", "is", "null", "like", "between", "group", "by", "order", "having", "limit",
    "fetch", "first", "rows", "only", "distinct", "as", "case", "when", "then", "else", "end",
    "count", "sum", "max", "min", "avg", "upper", "lower", "trim", "cast", "substr",
    "substring", "concat", "coalesce", "ifnull", "nvl", "exists", "asc", "desc", "union",
})


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

            await ctx.progress(1, total, "관계 추론·값 겹침")
            relationships, relation_evidence = await self._relationships(
                client, snapshot, engine, db_schema, budget,
            )

            await ctx.progress(2, total, "코드값·값 형식")
            columns = await self._profile_columns(
                client, snapshot, scope, comments, engine, db_schema, budget,
            )

            await ctx.progress(3, total, "공통코드 라벨")
            code_tables = await self._code_tables(
                client, snapshot, comments, engine, db_schema, budget,
            )

        await ctx.progress(4, total, "자산 조립")
        assets, evidence = _assemble(
            source, snapshot, scope, comments, catalog, relationships, columns, code_tables,
        )
        # 테이블 주석 → 테이블 정의 초안(출처 `comment` · LLM 0 · 검증 실패는 오류 행)
        definitions, definition_errors = _definition_rows(
            {t: {"manages": comments[t], "origin": ORIGIN_COMMENT}
             for t in scope if comments.get(t)},
            _table_columns(snap_tables), ORIGIN_COMMENT,
        )
        assets[TABLE_DEFINITIONS_KEY] = definitions
        evidence.update({
            "relationships": relation_evidence,
            "catalog": {"comments": len(comments), "errors": catalog.errors},
            "budget": budget.to_dict(),
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
            "budget": budget.to_dict(), "offline": offline,
        }

    async def _relationships(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """관계 후보(선언 · 추론 · 같은 기본키 군) → 값 겹침 검증 → 채택 관계(컬럼 쌍 단위)."""
        shapes = inference.shapes_from_snapshot(snapshot)
        relations, groups = inference.infer_relations(
            shapes, min_tables_for_common=MIN_TABLES_FOR_COMMON,
        )
        candidates = list(relations)
        for group in groups:
            hub = group[0]
            hub_pk = list(shapes[hub].primary_key)
            for other in group[1:]:
                other_cols = {c.lower(): c for c in shapes[other].columns}
                candidates.append(inference.InferredRelation(
                    other, hub, tuple(other_cols[c.lower()] for c in hub_pk), tuple(hub_pk),
                    "same_key",
                ))
        accepted: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        for rel in candidates:
            item: dict[str, Any] = {
                "child": rel.child, "parent": rel.parent, "child_columns": list(rel.child_columns),
                "parent_columns": list(rel.parent_columns), "origin": rel.origin,
                "overlap": None, "sampled": None, "accepted": False, "error": None,
            }
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
            if item["accepted"]:
                for child_col, parent_col in zip(rel.child_columns, rel.parent_columns):
                    accepted.append({
                        "from": f"{rel.child}.{child_col}", "to": f"{rel.parent}.{parent_col}",
                        "origin": rel.origin, "overlap": item["overlap"],
                    })
        return accepted, evidence

    async def _profile_columns(
        self,
        client: Any,
        snapshot: Mapping[str, Any],
        scope: Sequence[str],
        comments: Mapping[str, str],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> dict[str, dict[str, Any]]:
        """범위 컬럼의 코드 후보·형식 후보 값 표본 → ``{key: {type, comment, code, values, profile,
        error}}``."""
        snap_tables: Mapping[str, Any] = snapshot["tables"]
        code_keys: list[str] = []
        format_keys: list[str] = []
        meta: dict[str, dict[str, Any]] = {}
        for table in scope:
            for column, attrs in (snap_tables[table].get("columns") or {}).items():
                key = f"{table}.{column}"
                dtype = str((attrs or {}).get("type") or "")
                comment = comments.get(key)
                is_code = inference.is_code_candidate(column, dtype, comment)
                hint = f"{column} {comment or ''}"
                is_format = bool(_STRING_TYPE_RE.match(dtype)) and bool(
                    _DATE_HINT_RE.search(column) or _DATE_HINT_RE.search(comment or "")
                    or _HOST_IP_HINT_RE.search(hint)
                )
                if is_code or is_format:
                    meta[key] = {"type": dtype, "comment": comment, "code": is_code,
                                 "values": [], "profile": None, "error": None}
                    (code_keys if is_code else format_keys).append(key)
        if client is None or not meta:
            for item in meta.values():
                item["error"] = "DB 연결 없음" if client is None else None
            return meta
        samples = await probe.sample_distinct(
            client, code_keys, snapshot=snapshot, engine=engine, db_schema=db_schema,
            limit=inference.CODE_MAX_DISTINCT + 1, budget=budget,
        )
        samples.update(await probe.sample_distinct(
            client, format_keys, snapshot=snapshot, engine=engine, db_schema=db_schema,
            limit=FORMAT_SAMPLE, budget=budget,
        ))
        for key, item in meta.items():
            sample = samples.get(key) or {}
            values = [str(v) for v in sample.get("values") or []]
            item["error"] = sample.get("error")
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
        comments: Mapping[str, str],
        engine: str,
        db_schema: str | None,
        budget: probe.ProbeBudget,
    ) -> list[dict[str, Any]]:
        """공통코드 테이블 후보(이름·주석에 코드 단서 + 코드 컬럼 · 이름 컬럼) → (코드, 이름) 쌍."""
        if client is None:
            return []
        out: list[dict[str, Any]] = []
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
        # 잡이 도는 동안 바뀐 초안(테이블 정의 편집 등 — D-305)을 덮지 않도록
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

    # --- 테이블 정의 (D-305 ① · plans/138 W3) ---

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
        try:
            document = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML을 읽지 못했습니다: {e}") from e
        raw = parse_import_document(document)
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
            kept = [
                t for t, item in draft_meta[TABLE_DEFINITIONS_KEY].items()
                if by_bare.get(bare_name(t)) != item
            ]
            if kept:
                result["table_definitions_kept_manual"] = kept
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


def _scope_tables(
    tables: list[str] | None, profile: Mapping[str, Any], snap_tables: Mapping[str, Any]
) -> list[str]:
    """프로파일링 범위 — 지정 테이블 · 없으면 프로필 `allowed_tables` · 없으면 스냅샷 전체."""
    if tables:
        resolved, unknown = [], []
        for name in tables:
            key = name if name in snap_tables else next(
                (k for k in snap_tables if bare_name(k) == bare_name(name)), None
            )
            if key is None:
                unknown.append(name)
            elif key not in resolved:
                resolved.append(key)
        if unknown:
            raise ValueError(f"스냅샷에 없는 테이블: {', '.join(unknown)}")
        return resolved
    allowed = [t for t in profile.get("allowed_tables") or [] if isinstance(t, str)]
    from_profile = [
        k for k in snap_tables if bare_name(k) in {bare_name(t) for t in allowed}
    ]
    return from_profile or list(snap_tables)


def _assemble(
    source: str,
    snapshot: Mapping[str, Any],
    scope: Sequence[str],
    comments: Mapping[str, str],
    catalog: probe.CatalogInfo,
    relationships: list[dict[str, Any]],
    columns: Mapping[str, Mapping[str, Any]],
    code_tables: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """프로파일링 결과를 자산(초안 값)과 근거로 조립한다(결정적)."""
    code_values: dict[str, list[str]] = {}
    code_labels: dict[str, dict[str, str]] = {}
    column_values: dict[str, dict[str, Any]] = {}
    code_evidence: list[dict[str, Any]] = []
    rules: list[str] = []
    format_evidence: list[dict[str, Any]] = []
    entity: dict[str, dict[str, Any]] = {}
    for key, item in columns.items():
        table, _, column = key.rpartition(".")
        profile: inference.ValueProfile | None = item.get("profile")
        if item.get("code") and item.get("values"):
            values = list(item["values"])
            code_values[key] = values
            enum = inference.parse_comment_enum(item.get("comment"))
            labels, labels_from = inference.code_value_labels(values, enum, None), "comment"
            if not labels:
                best = _best_code_table(values, code_tables)
                if best is not None:
                    labels = inference.code_value_labels(values, {}, best["labels"])
                    labels_from = f"code_table:{best['table']}"
            if labels:
                code_labels[key] = labels
                is_int = bool(_INTEGER_TYPE_RE.match(str(item.get("type") or "")))
                column_values[key] = {
                    label: {"op": "=", "value": int(v) if is_int and v.lstrip("-").isdigit() else v}
                    for v, label in labels.items()
                }
            code_evidence.append({
                "key": key, "distinct": len(values), "labels": len(labels),
                "labels_from": labels_from if labels else None,
            })
        if profile is None:
            if item.get("error"):
                format_evidence.append({"key": key, "error": item["error"]})
            continue
        column_rules = inference.query_rules_for_column(table, column, profile)
        rules.extend(column_rules)
        kind = inference.entity_key_kind(column, item.get("comment"), profile)
        if kind:
            entity.setdefault(table, {})[kind] = {
                "column": column, "ratio": profile.ipv4 if kind == "ip" else profile.hostname,
                "multi_value": profile.multi_value >= 0.05,
            }
        if column_rules or kind or item.get("error"):
            format_evidence.append({
                "key": key, "total": profile.total, "date8": round(profile.date8, 3),
                "datetime14": round(profile.datetime14, 3), "ipv4": round(profile.ipv4, 3),
                "hostname": round(profile.hostname, 3),
                "multi_value": round(profile.multi_value, 3),
                "flag": list(profile.flag), "entity_key": kind, "rules": len(column_rules),
                "error": item.get("error"),
            })

    column_synonyms: dict[str, list[str]] = {}
    for key, text in comments.items():
        table, _, column = key.rpartition(".")
        label = inference.comment_label(text)
        if table and column and label and label.casefold() != column.casefold():
            column_synonyms[key] = [label]

    connected = {r["from"].rpartition(".")[0] for r in relationships} | {
        r["to"].rpartition(".")[0] for r in relationships
    }
    table_rows = [
        {"table": t, "family": inference.table_family(bare_name(t)),
         "rows": catalog.row_estimates.get(t), "connected": t in connected,
         "comment": comments.get(t)}
        for t in scope
    ]
    allowed = [
        t for t in scope
        if catalog.row_estimates.get(t) is None or (catalog.row_estimates.get(t) or 0) > 0
    ]

    assets = {
        "relationships": relationships,
        "allowed_tables": allowed,
        "code_values": code_values,
        "code_labels": code_labels,
        "entity_keys": _entity_keys(entity, catalog.row_estimates),
        "query_rules": rules,
        "seeds": {"column_synonyms": column_synonyms, "column_values": column_values}
        if (column_synonyms or column_values) else None,
        "query_examples": [],
        "prompt_template": None,
    }
    evidence = {
        "allowed_tables": table_rows, "code_columns": code_evidence,
        "code_tables": [{k: v for k, v in t.items() if k != "labels"} | {"labels": len(t["labels"])}
                        for t in code_tables],
        "formats": format_evidence,
    }
    return assets, evidence


def _best_code_table(
    values: Sequence[str], code_tables: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """코드 컬럼 값을 가장 많이 덮는 공통코드 테이블(덮는 비율 ≥ `CODE_TABLE_COVERAGE`)."""
    best, best_cover = None, 0.0
    for table in code_tables:
        labels = table.get("labels") or {}
        cover = sum(1 for v in values if v in labels) / len(values) if values else 0.0
        if cover >= CODE_TABLE_COVERAGE and cover > best_cover:
            best, best_cover = table, cover
    return best


def _entity_keys(
    entity: Mapping[str, Mapping[str, Any]], rows: Mapping[str, int | None]
) -> dict[str, Any] | None:
    """식별 키 후보 중 호스트명을 가진(있으면 IP도 가진) 테이블 하나를 고른다."""
    candidates = [t for t, kinds in entity.items() if "hostname" in kinds]
    if not candidates:
        return None
    table = sorted(
        candidates, key=lambda t: ("ip" not in entity[t], -(rows.get(t) or 0), t)
    )[0]
    kinds = entity[table]
    keys: list[dict[str, Any]] = []
    # 호스트명은 DNS처럼 대소문자를 가리지 않고 비교한다(폴스타 `entity_keys`와 같은 표기)
    keys.append({"type": "hostname", "column": kinds["hostname"]["column"], "priority": 1,
                 "compare": "casefold"})
    if "ip" in kinds:
        ip: dict[str, Any] = {"type": "ip", "column": kinds["ip"]["column"], "priority": 2}
        if kinds["ip"]["multi_value"]:
            ip["multi_value"] = True
        keys.append(ip)
    return {"entity": "server", "table": table, "keys": keys}


def _asset_summary(assets: Mapping[str, Any]) -> dict[str, int]:
    return {
        "relationships": len(assets.get("relationships") or []),
        "allowed_tables": len(assets.get("allowed_tables") or []),
        "code_values": len(assets.get("code_values") or {}),
        "code_labels": len(assets.get("code_labels") or {}),
        "entity_keys": len((assets.get("entity_keys") or {}).get("keys") or []),
        "query_rules": len(assets.get("query_rules") or []),
        "seed_synonyms": len((assets.get("seeds") or {}).get("column_synonyms") or {}),
        "table_definitions": len(assets.get(TABLE_DEFINITIONS_KEY) or {}),
    }


def _draft_comments(draft: Mapping[str, Any]) -> dict[str, str]:
    """초안 근거의 테이블 주석(요약용 — 컬럼 주석은 DDL·카탈로그를 다시 읽지 않고 근거에서만)."""
    out: dict[str, str] = {}
    for row in ((draft.get("evidence") or {}).get("allowed_tables") or []):
        if isinstance(row, Mapping) and row.get("comment"):
            out[str(row["table"])] = str(row["comment"])
    return out


def build_asset_summary(
    snapshot: Mapping[str, Any], assets: Mapping[str, Any], comments: Mapping[str, str]
) -> str:
    """LLM 입력용 스키마 요약 — 조회 대상 테이블의 컬럼·주석 · 관계 · 코드값 · 규칙 · 식별
    키(결정적)."""
    snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
    allowed = [t for t in assets.get("allowed_tables") or [] if t in snap_tables]
    tables = allowed or list(snap_tables)
    labels: Mapping[str, Mapping[str, str]] = assets.get("code_labels") or {}
    lines = ["### 테이블"]
    for table in tables:
        comment = comments.get(table)
        lines.append(f"- `{table}`" + (f" — {comment}" if comment else ""))
        for column, attrs in (snap_tables[table].get("columns") or {}).items():
            key = f"{table}.{column}"
            pk = " [PK]" if (attrs or {}).get("primary_key") else ""
            note = comments.get(key)
            lines.append(f"  - `{column}` {(attrs or {}).get('type', '')}{pk}"
                         + (f" — {note}" if note else ""))
    lines.append("\n### 관계")
    for rel in assets.get("relationships") or []:
        lines.append(f"- `{rel['from']}` = `{rel['to']}` ({rel.get('origin')})")
    if not assets.get("relationships"):
        lines.append("- (확인된 관계 없음)")
    lines.append("\n### 코드값")
    for key, values in (assets.get("code_values") or {}).items():
        mapping = labels.get(key) or {}
        rendered = ", ".join(f"{v}={mapping[v]}" if v in mapping else str(v) for v in values[:30])
        lines.append(f"- `{key}`: {rendered}")
    lines.append("\n### 쿼리 규칙")
    lines.extend(f"- {rule}" for rule in assets.get("query_rules") or [])
    entity = assets.get("entity_keys") or {}
    if entity:
        lines.append("\n### 식별 키")
        lines.extend(
            f"- {k['type']}: `{entity['table']}.{k['column']}`" for k in entity.get("keys") or []
        )
    text = "\n".join(lines)
    if len(text) > SUMMARY_MAX_CHARS:
        text = text[:SUMMARY_MAX_CHARS] + "\n… (요약 상한에서 잘림)"
    return text


# ──────────────────────────────────────────────
# 테이블 정의 (D-305 ① · plans/138 W3)
# ──────────────────────────────────────────────


@dataclass
class _DefinitionPlan:
    """LLM 묶음 초안 계획 — 묶음 · 건너뛴 테이블 수(사유별) · 스냅샷 테이블 · 주석."""

    batches: list[list[str]]
    skipped: dict[str, int]
    snap_tables: Mapping[str, Any]
    comments: dict[str, str]


@dataclass
class _DefinitionContext:
    """LLM 입력 재료(표본 값 없음) — 설명·라벨 키는 ``table.column``을 casefold한 것."""

    snap_tables: Mapping[str, Any]
    descriptions: dict[str, str]
    labels: dict[str, Mapping[str, Any]]
    relationships: list[tuple[str, str]]
    db_description: str

    def column_lookup(self, store: Mapping[str, Any], table: str, column: str) -> Any:
        """스키마 키 · 맨 이름 어느 쪽으로 저장됐어도 찾는다."""
        for key in (f"{table}.{column}", f"{bare_name(table)}.{column}"):
            value = store.get(key.casefold())
            if value:
                return value
        return None


def _str_list(value: Any) -> list[str]:
    return [t for t in value if isinstance(t, str)] if isinstance(value, list) else []


def _row_origin(row: Any) -> str:
    """초안 행의 출처(행이 없으면 LLM이 채울 빈 자리로 본다)."""
    if not isinstance(row, Mapping):
        return ORIGIN_LLM
    return str(row.get("origin") or "")


def _table_columns(snap_tables: Mapping[str, Any]) -> dict[str, list[str]]:
    """스냅샷 → ``{테이블 키: [컬럼…]}``(정의 검증의 실존 기준)."""
    return {
        str(table): [str(c) for c in ((data or {}).get("columns") or {})]
        for table, data in snap_tables.items()
    }


def _display_row(raw: Any, origin: str) -> dict[str, Any]:
    """검증 실패 행의 표시용 사본 — 아는 필드만 · 글은 문자열로(JSON 저장 가능 · 승인 불가)."""
    fields: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    row: dict[str, Any] = {}
    for name in FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        if name == "key_columns":
            row[name] = [str(v) for v in value] if isinstance(value, (list, tuple)) else [
                str(value)
            ]
        elif name == "related":
            row[name] = (
                {str(k): str(v) for k, v in value.items()} if isinstance(value, Mapping) else {}
            )
        else:
            row[name] = str(value)
    row.setdefault("manages", "")
    row.setdefault("origin", origin)
    return row


def _definition_rows(
    raw: Mapping[str, Any], table_columns: Mapping[str, Sequence[str]], origin: str
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """정의 원본 → ``(초안 행, 오류)`` — 유효 행은 정규화 값, 검증 실패 행은 표시용 사본이다.

    행·오류 키는 스키마 테이블 키(스키마에 없는 테이블은 입력 이름)다.
    """
    valid, problems = validate_table_definitions(raw, table_columns)
    index: dict[str, str] = {}
    for key in table_columns:
        index.setdefault(bare_name(key), key)
    rows: dict[str, dict[str, Any]] = dict(valid)
    errors: dict[str, list[str]] = {}
    for label, messages in problems.items():
        key = index.get(bare_name(label), label)
        errors.setdefault(key, []).extend(messages)
        rows.setdefault(key, _display_row(raw.get(label), origin))
    return rows, errors


def _definition_summary(
    rows: Mapping[str, Any], errors: Mapping[str, Any]
) -> dict[str, int]:
    invalid = sum(1 for table in rows if errors.get(table))
    return {"total": len(rows), "valid": len(rows) - invalid, "invalid": invalid}


def definition_batches(
    tables: Sequence[str], size: int = DEFINITION_BATCH_SIZE
) -> list[list[str]]:
    """LLM 묶음 — 군 접두(이름 끝 숫자를 뗀 앞부분) 단위로 `size`개씩 묶는다.

    군마다 꽉 찬 묶음을 먼저 만들고, 남은 조각은 큰 것부터 들어갈 수 있는 묶음에 합친다(호출 수를
    줄인다 — 조각은 쪼개지 않는다). 순서는 맨 이름 기준으로 결정적이다.
    """
    families: dict[str, list[str]] = {}
    for table in sorted(tables, key=lambda t: (bare_name(t), t)):
        families.setdefault(inference.table_family(bare_name(table)), []).append(table)
    batches: list[list[str]] = []
    rests: list[list[str]] = []
    for family in sorted(families):
        members = families[family]
        full = len(members) // size * size
        batches.extend(members[i:i + size] for i in range(0, full, size))
        if full < len(members):
            rests.append(members[full:])
    packed: list[list[str]] = []
    for rest in sorted(rests, key=len, reverse=True):
        target = next((b for b in packed if len(b) + len(rest) <= size), None)
        if target is None:
            packed.append(list(rest))
        else:
            target.extend(rest)
    return batches + packed


def _render_definition_input(tables: Sequence[str], context: _DefinitionContext) -> str:
    """LLM 입력 — 테이블마다 컬럼 이름/타입 · 컬럼 설명 · 코드 라벨 · 그 테이블의 승인 관계."""
    lines: list[str] = []
    for table in tables:
        lines.append(f"### {table}")
        columns = (context.snap_tables.get(table) or {}).get("columns") or {}
        for column, attrs in columns.items():
            line = f"- `{column}` {(attrs or {}).get('type') or ''}".rstrip()
            note = context.column_lookup(context.descriptions, table, column)
            if note:
                text = " ".join(str(note).split())
                if len(text) > DEFINITION_DESCRIPTION_MAX_CHARS:
                    text = text[:DEFINITION_DESCRIPTION_MAX_CHARS] + "…"
                line += f" — {text}"
            labels = context.column_lookup(context.labels, table, column)
            if labels:
                names = list(dict.fromkeys(str(v) for v in labels.values()))
                line += f" (코드 라벨: {', '.join(names[:DEFINITION_LABELS_MAX])})"
            lines.append(line)
        own = bare_name(table)
        for left, right in context.relationships:
            if own in (bare_name(left.rpartition(".")[0]), bare_name(right.rpartition(".")[0])):
                lines.append(f"- 관계: `{left}` = `{right}`")
        lines.append("")
    return "\n".join(lines).strip()


async def _definition_batch(
    llm: Any, tables: Sequence[str], context: _DefinitionContext
) -> tuple[dict[str, Any], str | None]:
    """LLM 묶음 1회 → ``(묶음 테이블의 정의 원본, 실패 사유)`` — 원본은 검증 전이다.

    응답에서 `manages`·`kind`·`key_columns`만 읽고 출처는 `llm`으로 정한다.
    """
    prompt = TABLE_DEFINITIONS_PROMPT.format(
        db_description=context.db_description or "(설명 없음)",
        tables=_render_definition_input(tables, context),
        manages_max=MANAGES_MAX_CHARS,
        kinds=" · ".join(KINDS),
        key_columns_max=KEY_COLUMNS_MAX,
    )
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        parsed = parse_llm_json(coerce_content_text(getattr(response, "content", response)))
    except Exception as e:  # noqa: BLE001 — 호출·파싱 실패는 묶음 실패로 기록한다
        logger.warning("테이블 정의 LLM 묶음 실패(%d개): %s", len(tables), e)
        return {}, f"{type(e).__name__}: {e}"
    if isinstance(parsed, Mapping) and set(parsed) == {"tables"} and isinstance(
        parsed["tables"], Mapping
    ):
        parsed = parsed["tables"]
    if not isinstance(parsed, Mapping):
        return {}, "JSON 객체가 아닙니다"
    answered = {bare_name(str(k)): v for k, v in parsed.items()}
    raw: dict[str, Any] = {}
    for table in tables:
        item = answered.get(bare_name(table))
        if item is None:
            continue
        if isinstance(item, Mapping):
            item = {k: item[k] for k in ("manages", "kind", "key_columns") if k in item}
            item["origin"] = ORIGIN_LLM
        raw[table] = item
    return raw, None


def _merge_definition_batch(
    rows: dict[str, Any],
    errors: dict[str, list[str]],
    tables: Sequence[str],
    raw: Mapping[str, Any],
    error: str | None,
    table_columns: Mapping[str, Sequence[str]],
) -> dict[str, Any]:
    """LLM 묶음 1개의 결과를 초안 행·오류에 반영하고(제자리 갱신) 묶음 기록을 돌려준다.

    LLM 행이나 빈 자리만 바꾼다 — 그 사이 사람이 고쳤거나 다른 경로로 정의된 테이블은 그대로 둔다.
    호출·파싱이 실패하면 이미 유효한 LLM 행은 남기고 나머지를 오류 행으로 둔다.
    """
    targets = [t for t in tables if _row_origin(rows.get(t)) == ORIGIN_LLM]
    if error is not None:
        for table in targets:
            if table in rows and not errors.get(table):
                continue
            rows[table] = {"manages": "", "origin": ORIGIN_LLM}
            errors[table] = [f"LLM 초안 실패: {error}"]
        status = "failed"
    else:
        answered = {t: raw[t] for t in targets if t in raw}
        new_rows, new_errors = _definition_rows(answered, table_columns, ORIGIN_LLM)
        for table in targets:
            errors.pop(table, None)
        rows.update(new_rows)
        errors.update(new_errors)
        for table in targets:
            if table not in raw:
                rows[table] = {"manages": "", "origin": ORIGIN_LLM}
                errors[table] = ["LLM 응답에 이 테이블이 없습니다"]
        status = "partial" if any(errors.get(t) for t in targets) else "ok"
    return {
        "tables": list(tables),
        "families": sorted({inference.table_family(bare_name(t)) for t in tables}),
        "status": status,
        "error": error,
    }


def _approvable_definitions(
    rows: Mapping[str, Any],
    errors: Mapping[str, Any],
    selected: list[str] | None,
    table_columns: Mapping[str, Sequence[str]],
) -> dict[str, dict[str, Any]]:
    """승인할 테이블 정의 — 고른 행(없으면 전부)을 현재 스냅샷으로 다시 검증한 정규화 값.

    Raises:
        ValueError: 빈 선택 · 초안에 없는 테이블
        DraftNotApprovable: 고른 행에 검증 실패 행이 있음(`validation_failed` → 409)
    """
    names = list(rows) if selected is None else list(dict.fromkeys(selected))
    if not names:
        raise ValueError("승인할 테이블 정의를 하나 이상 고르세요")
    unknown = [n for n in names if n not in rows]
    if unknown:
        raise ValueError(f"초안에 없는 테이블 정의: {', '.join(unknown[:10])}")
    failed = [n for n in names if errors.get(n)]
    if failed:
        raise DraftNotApprovable(
            "validation_failed",
            f"검증 실패 행은 승인할 수 없습니다({len(failed)}개): {', '.join(failed[:10])}",
        )
    valid, problems = validate_table_definitions({n: rows[n] for n in names}, table_columns)
    if problems:
        raise DraftNotApprovable(
            "validation_failed",
            f"현재 스키마로 다시 검증하지 못한 행({len(problems)}개): "
            f"{', '.join(list(problems)[:10])}",
        )
    return valid


async def _ask(llm: Any, prompt: str) -> str:
    """LLM 1회 호출 — 실패는 빈 문자열(검증 단계가 사유를 남긴다)."""
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as e:  # noqa: BLE001 — LLM 실패는 검증 실패로 드러난다
        logger.warning("자산 LLM 호출 실패: %s", e)
        return ""
    return coerce_content_text(getattr(response, "content", response))


@dataclass
class _SqlCheck:
    """LLM SQL 실행 검증 문맥 — 클라이언트 · 주입된 검증기 · 스냅샷 스키마 · 엔진 · 소스."""

    client: Any
    checker: SqlChecker | None
    schema_info: Mapping[str, Any]
    engine: str
    db_id: str = ""


async def _execute_check(check: _SqlCheck, sql: str) -> tuple[int | None, str | None]:
    """LLM SQL을 검사한 뒤에만 실행한다(행 수 · 오류).

    순서: 문장 형식(SELECT 한 문장 · 행 제한) → `SQLGuard` → 부수효과 함수 차단 → 주입된 검증기
    (참조 테이블·컬럼 실존 — 스냅샷 밖 테이블 거절) → 바깥 행 제한으로 감싸 실행.
    """
    text = sql.strip().rstrip(";").strip()
    if not validate_sample_sql(text) or ";" in text:
        return None, "SELECT 한 문장 · 행 제한 절이 필요합니다"
    safe, reason = SQLGuard().is_safe_select(text)
    if not safe:
        return None, reason
    if (found := _SIDE_EFFECT_FUNC_RE.search(text)) is not None:
        return None, f"부수효과가 있는 함수는 쓸 수 없습니다: {found.group(1)}"
    if check.checker is None:
        return None, "SQL 검증기가 없어 실행하지 않았습니다"
    try:
        problems = check.checker(text, check.schema_info, check.engine, check.db_id)
    except Exception as e:  # noqa: BLE001 — 검증기 실패는 실행하지 않는 쪽으로
        return None, f"SQL 검증 실패: {type(e).__name__}: {e}"
    if problems:
        return None, "SQL 검증 실패: " + " / ".join(str(p) for p in problems[:3])
    wrapped = f"SELECT * FROM ({text}) q {row_limit_clause(check.engine, SQL_CHECK_LIMIT)}"
    try:
        result = await check.client.execute_sql(wrapped)
    except Exception as e:  # noqa: BLE001 — 실행 실패는 사유로 돌려준다
        return None, f"{type(e).__name__}: {e}"
    return len(getattr(result, "rows", None) or []), None


async def _validate_examples(
    check: _SqlCheck, raw: str
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """쿼리 예시 — JSON 배열 파싱 → 질문·SQL 형식 → 검사 → 실제 실행 성공만 남긴다."""
    checks: list[dict[str, Any]] = []
    passed: list[dict[str, str]] = []
    try:
        parsed = parse_llm_json(raw)
    except Exception as e:  # noqa: BLE001 — 파싱 실패는 사유로 남긴다
        return [], [{"error": f"JSON 파싱 실패: {e}"}]
    if not isinstance(parsed, list):
        return [], [{"error": "JSON 배열이 아닙니다"}]
    for item in parsed[: EXAMPLE_COUNT * 2]:
        fields = item if isinstance(item, Mapping) else {}
        question = str(fields.get("question") or "").strip()
        sql = str(fields.get("sql") or "").strip().rstrip(";")
        entry: dict[str, Any] = {"question": question, "sql": sql, "rows": None, "error": None}
        checks.append(entry)
        if not question or not sql:
            entry["error"] = "질문·SQL이 비었습니다"
            continue
        entry["rows"], entry["error"] = await _execute_check(check, sql)
        if entry["error"] is None and len(passed) < EXAMPLE_COUNT:
            passed.append({"question": question, "sql": sql})
    return passed, checks


async def _validate_section(
    check: _SqlCheck, raw: str, snapshot: Mapping[str, Any]
) -> tuple[str, dict[str, Any]]:
    """DB 전용 규칙 섹션 — 구조 검사(길이 · 중괄호 · 펜스 · 식별자 실존)를 통과해야
    섹션 안 SQL을 실행한다."""
    section = raw.strip()
    errors: list[str] = []
    if not section:
        errors.append("섹션이 비었습니다")
    if len(section) > SECTION_MAX_CHARS:
        errors.append(f"길이 상한({SECTION_MAX_CHARS}자)을 넘었습니다: {len(section)}자")
    if "{" in section or "}" in section:
        errors.append("중괄호를 쓸 수 없습니다(프롬프트 자리표시자와 충돌)")
    if section.count("```") % 2:
        errors.append("닫히지 않은 코드 펜스가 있습니다")
    blocks = [b.strip() for b in _FENCE_RE.findall(section)]
    if len(blocks) > MAX_SECTION_SQL_BLOCKS:
        errors.append(f"코드 블록은 {MAX_SECTION_SQL_BLOCKS}개까지입니다: {len(blocks)}개")
    snap_tables: Mapping[str, Any] = snapshot.get("tables") or {}
    table_names = {bare_name(t).lower() for t in snap_tables} | {t.lower() for t in snap_tables}
    column_names = {
        c.lower() for data in snap_tables.values() for c in (data.get("columns") or {})
    }
    qualified = {
        f"{bare_name(t).lower()}.{c.lower()}"
        for t, data in snap_tables.items() for c in (data.get("columns") or {})
    }
    prose = _FENCE_RE.sub(" ", section)
    unknown = sorted(
        ident for ident in inference.referenced_identifiers(prose)
        if not (
            ident in table_names or ident in column_names or ident in qualified
            or ident in _SQL_WORDS
        )
    )
    if unknown:
        errors.append(f"스키마에 없는 식별자: {', '.join(unknown[:20])}")
    sql_checks: list[dict[str, Any]] = []
    if errors:
        # 구조가 틀린 섹션의 SQL은 실행하지 않는다
        skipped = "구조 검사 실패로 실행 안 함"
        sql_checks = [{"sql": b, "rows": None, "error": skipped} for b in blocks]
    else:
        for block in blocks:
            rows, error = await _execute_check(check, block)
            sql_checks.append({"sql": block, "rows": rows, "error": error})
            if error:
                errors.append(f"섹션 SQL 검사·실행 실패: {error}")
    return section, {
        "passed": not errors, "errors": errors, "sql_checks": sql_checks,
        "length": len(section), "unknown_identifiers": unknown,
    }
