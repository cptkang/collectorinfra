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

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain_core.messages import HumanMessage

from src.domain import schema_inference as inference
from src.domain.profile_merge import merge_profile, profile_field_diff
from src.domain.schema_snapshot import bare_name
from src.prompts.asset_generation import PROMPT_SECTION_PROMPT, QUERY_EXAMPLES_PROMPT
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

#: LLM SQL 검증기 ``(sql, schema_info, engine) → 오류 목록`` — 질의 경로 `validate_sql`(참조
#: 테이블·컬럼 실존 포함)은 application 계층이라 이 모듈이 import하지 않고 조립부(API)가 주입한다
SqlChecker = Callable[[str, Mapping[str, Any], str], list[str]]

#: 승인 화면에서 고를 수 있는 자산
ASSET_KINDS: tuple[str, ...] = (
    "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
    "query_examples", "seeds", "prompt_template",
)
#: 프로필 키로 들어가는 자산(`code_values`는 `code_labels`와 함께 간다)
PROFILE_ASSETS: tuple[str, ...] = (
    "relationships", "allowed_tables", "code_values", "entity_keys", "query_rules",
    "query_examples",
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
        """자산 초안 목록 · 자산 파일 버전 · 현행 파일 유무."""
        validate_db_id(source)
        files: dict[str, Any] = {}
        for kind in ("seeds", "prompt_template"):
            current = self._assets.read_current(source, kind)
            files[kind] = {
                "path": str(self._assets.path(source, kind)),
                "exists": current is not None,
                "versions": await self._assets.list_versions(source, kind),
            }
        return {
            "source": source, "env": self.env, "local_sandbox": self.local_sandbox,
            "provider": self.provider_info(),
            "drafts": await self._store.list_asset_drafts(source),
            "files": files,
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
            "validation": {},
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
        assets = dict(draft.get("assets") or {})
        validation = dict(draft.get("validation") or {})

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
            check = _SqlCheck(client, self._sql_checker, schema_info, engine)
            examples, example_checks = await _validate_examples(check, examples_raw)
            section, section_check = await _validate_section(check, section_raw, snapshot)
        assets["query_examples"] = examples
        assets["prompt_template"] = {"section": section} if section_check["passed"] else None
        validation["query_examples"] = {"passed": bool(examples), "checks": example_checks}
        validation["prompt_template"] = section_check
        provider = self.provider_info()
        updated = await self._store.update_asset_draft(
            source, draft_id, assets=assets, validation=validation, llm_calls=2,
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
    ) -> dict[str, Any]:
        """고른 자산만 적용한다 — 프로필 키(사람 값 보존 병합) · 시드 · DB 전용 섹션.

        Raises:
            ValueError: 모르는 자산 · 빈 포함 목록
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
        updated = await self._store.update_asset_draft(
            source, draft_id, status=status, approved_by=by, approved_at=_now_iso(),
            approve_reason=reason, approved_assets=chosen, applied=applied, apply_errors=failed,
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
    """LLM SQL 실행 검증 문맥 — 클라이언트 · 주입된 검증기 · 스냅샷 스키마 · 엔진."""

    client: Any
    checker: SqlChecker | None
    schema_info: Mapping[str, Any]
    engine: str


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
        problems = check.checker(text, check.schema_info, check.engine)
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
