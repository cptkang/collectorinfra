"""DB 구조 분석 공용 모듈 (plans/104 §3.5 · D-227).

질의 경로(`schema_analyzer`)의 비공개 함수였던 LLM 구조 분석·샘플 SQL 수집을 관리자 기능이
DB 단위로 부를 수 있게 옮겼다. 질의 경로는 구조를 분석하지 않고 적용본을 읽기만 한다(R1).

- "패턴 없음"과 "분석 실패"를 구분한다 — 종전에는 둘 다 None이었다(§3.5 v2).
- 샘플 SQL은 LLM이 만들고, 읽기 전용·LIMIT 규칙을 통과한 것만 실행한다(D-003).

계층: infrastructure(LLM·DB 클라이언트 호출). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage

from src.domain.schema_snapshot import bare_name, parse_join_pairs, structure_refs
from src.utils.json_extract import coerce_content_text, strip_code_fence

logger = logging.getLogger(__name__)

AnalysisStatus = Literal["ok", "no_patterns", "failed"]

# 샘플 SQL 최대 실행 건수(종전 질의 경로와 같다)
_MAX_SAMPLE_QUERIES = 3


@dataclass
class StructureAnalysisOutcome:
    """구조 분석 1회의 결과.

    Attributes:
        status: ok(패턴 1건 이상) · no_patterns(패턴 0건 — 빈 승인본 후보) · failed(호출·파싱 실패)
        meta: ok/no_patterns일 때 `{"patterns": [...], "query_guide": str}` · failed면 None
        error: failed일 때 사유
    """

    status: AnalysisStatus
    meta: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class SampleCollection:
    """샘플 SQL 생성·실행 결과.

    Attributes:
        samples: `{purpose: rows}` — 실행에 성공하고 행이 있는 것만
        attempts: 시도별 `{purpose, sql, status, rows, error}` — status는
            ok · empty(0행) · unsafe(안전성 검증 실패) · failed(실행 실패)
        error: LLM 호출·파싱 실패 사유(시도 자체가 없을 때)
    """

    samples: dict[str, Any] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def format_schema_for_analysis(schema_dict: dict[str, Any]) -> str:
    """스키마 딕셔너리를 LLM 분석용 텍스트로 변환한다.

    각 테이블의 컬럼 정보(이름, 타입, PK, FK, nullable)를 나열하여
    LLM이 구조적 패턴을 감지할 수 있도록 한다.

    Args:
        schema_dict: 스키마 딕셔너리 (tables 키 포함)

    Returns:
        LLM 프롬프트에 삽입할 텍스트
    """
    lines: list[str] = []
    tables = schema_dict.get("tables", {})
    for table_name, table_data in tables.items():
        lines.append(f"### {table_name}")
        columns = table_data.get("columns", [])
        for col in columns:
            attrs: list[str] = []
            if col.get("primary_key"):
                attrs.append("PK")
            if col.get("foreign_key"):
                ref = col.get("references", "")
                attrs.append(f"FK->{ref}" if ref else "FK")
            if not col.get("nullable", True):
                attrs.append("NOT NULL")
            attr_str = f" ({', '.join(attrs)})" if attrs else ""
            col_type = col.get("type", "")
            lines.append(f"  - {col['name']}: {col_type}{attr_str}")
        lines.append("")

    # 관계 정보가 있으면 추가
    relationships = schema_dict.get("relationships", [])
    if relationships:
        lines.append("### FK 관계")
        for rel in relationships:
            from_t = rel.get("from", "")
            to_t = rel.get("to", "")
            lines.append(f"  - {from_t} -> {to_t}")
        lines.append("")

    return "\n".join(lines)


def parse_llm_json(raw_text: str) -> Any:
    """LLM 응답에서 JSON을 추출하여 파싱한다.

    마크다운 코드 블록(```json ... ```)을 자동 제거한다.

    Args:
        raw_text: LLM 응답 원문

    Returns:
        파싱된 Python 객체

    Raises:
        ValueError: JSON 파싱 실패 시
    """
    text = strip_code_fence(raw_text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 응답 JSON 파싱 실패: {exc}") from exc


async def analyze_structure(
    llm: BaseChatModel,
    schema_dict: dict[str, Any],
) -> StructureAnalysisOutcome:
    """LLM으로 스키마의 구조적 패턴(EAV·계층형 등)과 쿼리 가이드를 분석한다.

    Args:
        llm: LLM 인스턴스
        schema_dict: 분석 대상 테이블만 담은 스키마 딕셔너리

    Returns:
        StructureAnalysisOutcome — 패턴 0건은 실패가 아니라 `no_patterns`다
    """
    from src.prompts.structure_analyzer import STRUCTURE_ANALYSIS_PROMPT

    schema_text = format_schema_for_analysis(schema_dict)
    prompt = STRUCTURE_ANALYSIS_PROMPT + "\n\n## DB 스키마\n\n" + schema_text
    logger.debug("구조 분석 LLM 프롬프트: %s", prompt)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        # content가 블록 리스트로 올 수 있다(b04c5dd) — 평문으로 합친 뒤 파싱
        result = parse_llm_json(coerce_content_text(response.content))
    except ValueError as e:
        logger.warning("구조 분석 LLM 응답 파싱 실패: %s", e)
        return StructureAnalysisOutcome(status="failed", error=str(e))
    except Exception as e:  # noqa: BLE001 — LLM 호출 실패는 결과로 돌려준다
        logger.warning("구조 분석 LLM 호출 실패: %s", e)
        return StructureAnalysisOutcome(status="failed", error=f"LLM 호출 실패: {e}")

    if not isinstance(result, dict):
        return StructureAnalysisOutcome(
            status="failed", error="LLM 응답이 JSON 객체가 아님"
        )
    patterns = result.get("patterns") or []
    if not isinstance(patterns, list):
        return StructureAnalysisOutcome(
            status="failed", error="patterns가 배열이 아님"
        )
    guide = result.get("query_guide") or ""
    meta = {"patterns": patterns, "query_guide": guide if isinstance(guide, str) else str(guide)}
    if not patterns:
        logger.info("LLM 구조 분석: 특수 패턴 미감지")
        return StructureAnalysisOutcome(status="no_patterns", meta=meta)
    logger.info("LLM 구조 분석 완료: %d개 패턴 감지", len(patterns))
    return StructureAnalysisOutcome(status="ok", meta=meta)


def validate_sample_sql(sql: str) -> bool:
    """샘플 SQL의 안전성을 검증한다.

    SELECT 문만 허용하며, DML/DDL 키워드가 포함되면 거부한다.
    LIMIT 또는 FETCH FIRST 절이 있는지도 확인한다.

    Args:
        sql: 검증할 SQL 문자열

    Returns:
        안전하면 True, 위험하면 False
    """
    sql_upper = sql.strip().upper()

    # SELECT 로 시작해야 함
    if not sql_upper.startswith("SELECT"):
        return False

    # 위험한 키워드 검사
    forbidden = [
        "INSERT ", "UPDATE ", "DELETE ", "DROP ", "CREATE ",
        "ALTER ", "TRUNCATE ", "GRANT ", "REVOKE ", "EXEC ",
        "EXECUTE ", "MERGE ",
    ]
    for kw in forbidden:
        if kw in sql_upper:
            return False

    # LIMIT 또는 FETCH FIRST 절 필수
    has_limit = "LIMIT " in sql_upper or "FETCH FIRST" in sql_upper
    if not has_limit:
        return False

    return True


async def generate_structure_samples(
    llm: BaseChatModel,
    client: Any,
    schema_dict: dict[str, Any],
    structure_meta: dict[str, Any],
) -> SampleCollection:
    """구조 분석 결과에 맞는 샘플 SQL을 LLM으로 만들고, 안전한 것만 실행한다.

    Args:
        llm: LLM 인스턴스
        client: DB 클라이언트(`execute_sql`)
        schema_dict: 분석 대상 스키마 딕셔너리
        structure_meta: 구조 분석 결과(patterns·query_guide)

    Returns:
        SampleCollection — 시도별 결과를 모두 담는다(결정적 검증 ③④의 입력)
    """
    from src.prompts.structure_analyzer import SAMPLE_SQL_GENERATION_PROMPT

    schema_text = format_schema_for_analysis(schema_dict)
    structure_text = json.dumps(structure_meta, ensure_ascii=False, indent=2)
    prompt = (
        SAMPLE_SQL_GENERATION_PROMPT
        + "\n\n## 구조 분석 결과\n\n"
        + structure_text
        + "\n\n## DB 스키마\n\n"
        + schema_text
    )

    collection = SampleCollection()
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        sql_list = parse_llm_json(coerce_content_text(response.content))
    except ValueError as e:
        logger.warning("샘플 SQL 생성 LLM 응답 파싱 실패: %s", e)
        collection.error = str(e)
        return collection
    except Exception as e:  # noqa: BLE001
        logger.warning("샘플 SQL 생성 LLM 호출 실패: %s", e)
        collection.error = f"LLM 호출 실패: {e}"
        return collection

    if not isinstance(sql_list, list):
        logger.warning("샘플 SQL 생성 결과가 배열이 아님")
        collection.error = "샘플 SQL 생성 결과가 배열이 아님"
        return collection

    for item in sql_list[:_MAX_SAMPLE_QUERIES]:
        if not isinstance(item, dict):
            continue
        purpose = str(item.get("purpose", ""))
        sql = str(item.get("sql", "") or "")
        attempt: dict[str, Any] = {
            "purpose": purpose, "sql": sql, "status": "", "rows": 0, "error": None,
        }
        collection.attempts.append(attempt)

        if not sql or not validate_sample_sql(sql):
            logger.warning("샘플 SQL 안전성 검증 실패 (skip): %s", purpose)
            attempt["status"] = "unsafe"
            continue

        try:
            result = await client.execute_sql(sql)
        except Exception as e:  # noqa: BLE001
            logger.warning("샘플 SQL 실행 실패 (%s): %s", purpose, e)
            attempt["status"] = "failed"
            attempt["error"] = str(e)
            continue

        rows = list(getattr(result, "rows", None) or [])
        attempt["rows"] = len(rows)
        if rows:
            attempt["status"] = "ok"
            collection.samples[purpose] = rows
            logger.debug("샘플 수집 성공: %s (%d행)", purpose, len(rows))
        else:
            attempt["status"] = "empty"

    return collection


# ──────────────────────────────────────────────
# DB 단위 분석 — FK 묶음 분할 · 결과 병합 · 결정적 검증 (plans/104 §3.5 A-5)
# ──────────────────────────────────────────────

# 조인 컬럼 타입 계열(첫 단어 기준). 모르는 타입은 판정하지 않는다(검증 ②에서 건너뜀).
_NUMERIC_TYPE_WORDS = frozenset({
    "int", "integer", "int2", "int4", "int8", "bigint", "smallint", "tinyint", "mediumint",
    "serial", "serial4", "serial8", "bigserial", "smallserial", "decimal", "dec", "numeric",
    "number", "float", "float4", "float8", "double", "real", "decfloat", "money",
})
_STRING_TYPE_WORDS = frozenset({
    "char", "character", "varchar", "varchar2", "nchar", "nvarchar", "bpchar", "text",
    "tinytext", "mediumtext", "longtext", "clob", "nclob", "dbclob", "string", "graphic",
    "vargraphic", "long", "citext", "enum", "set",
})
_DATE_TYPE_WORDS = frozenset({
    "date", "time", "timetz", "timestamp", "timestamptz", "datetime", "year",
})


def _type_family(type_name: str) -> str | None:
    """컬럼 타입을 숫자·문자·날짜 계열로 분류한다(모르면 None)."""
    text = " ".join(str(type_name or "").split()).lower()
    words = text.split("(", 1)[0].split()
    if not words:
        return None
    head = words[0]
    if head in _NUMERIC_TYPE_WORDS:
        return "숫자"
    if head in _STRING_TYPE_WORDS:
        return "문자"
    if head in _DATE_TYPE_WORDS:
        return "날짜"
    return None


def _table_resolver(table_keys: Sequence[str]) -> Callable[[str], str | None]:
    """테이블 이름을 스키마 키로 바꾸는 함수(정확 일치 → 대소문자·접두 무시 유일 일치)."""
    keys = set(table_keys)
    by_bare: dict[str, list[str]] = {}
    for key in table_keys:
        by_bare.setdefault(bare_name(key), []).append(key)

    def _resolve(name: str) -> str | None:
        if name in keys:
            return name
        matches = by_bare.get(bare_name(name), [])
        return matches[0] if len(matches) == 1 else None

    return _resolve


def _fk_edges(schema_dict: Mapping[str, Any]) -> list[tuple[str, str]]:
    """스키마 딕셔너리의 FK 간선(테이블 쌍) — 관계 목록 + 컬럼 `references`."""
    edges: list[tuple[str, str]] = []
    for rel in schema_dict.get("relationships") or []:
        if not isinstance(rel, Mapping):
            continue
        from_ref = str(rel.get("from", ""))
        to_ref = str(rel.get("to", ""))
        if "." in from_ref and "." in to_ref:
            edges.append((from_ref.rsplit(".", 1)[0], to_ref.rsplit(".", 1)[0]))
    for table_name, table_data in (schema_dict.get("tables") or {}).items():
        if not isinstance(table_data, Mapping):
            continue
        for col in table_data.get("columns") or []:
            ref = str(col.get("references") or "") if isinstance(col, Mapping) else ""
            if "." in ref:
                edges.append((str(table_name), ref.rsplit(".", 1)[0]))
    return edges


def split_fk_groups(schema_dict: Mapping[str, Any], max_tables: int) -> list[list[str]]:
    """분석 대상 테이블을 FK 연결 묶음으로 나눈다(묶음마다 LLM 1회).

    - FK로 이어진 테이블(연결 성분)은 같은 묶음에 둔다.
    - 성분이 상한보다 크면 이름순으로 상한 크기씩 자른다.
    - 작은 성분은 상한을 넘지 않는 만큼 이어 붙인다(FK 없는 단독 테이블 포함).
    - 순서는 결정적이다 — 성분은 가장 앞 이름순, 묶음 안은 이름순.

    Args:
        schema_dict: ``{"tables": {...}, "relationships": [...]}``
        max_tables: 묶음 크기 상한(`SCHEMA_CACHE_STRUCTURE_GROUP_MAX_TABLES`)

    Returns:
        테이블 키 묶음 목록

    Raises:
        ValueError: max_tables가 1 미만일 때
    """
    if max_tables < 1:
        raise ValueError(f"max_tables는 1 이상이어야 합니다: {max_tables}")
    tables = sorted(str(t) for t in (schema_dict.get("tables") or {}))
    if not tables:
        return []

    parent = {t: t for t in tables}

    def _find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    resolve = _table_resolver(tables)
    for from_table, to_table in _fk_edges(schema_dict):
        left, right = resolve(from_table), resolve(to_table)
        if left and right:
            root_l, root_r = _find(left), _find(right)
            if root_l != root_r:
                parent[max(root_l, root_r)] = min(root_l, root_r)

    components: dict[str, list[str]] = {}
    for table in tables:
        components.setdefault(_find(table), []).append(table)

    groups: list[list[str]] = []
    current: list[str] = []
    for component in sorted(components.values(), key=lambda c: c[0]):
        if len(component) > max_tables:
            groups.extend(
                component[i:i + max_tables] for i in range(0, len(component), max_tables)
            )
            continue
        if current and len(current) + len(component) > max_tables:
            groups.append(current)
            current = []
        current = current + component
    if current:
        groups.append(current)
    return [sorted(group) for group in groups]


def merge_outcomes(
    outcomes: Sequence[StructureAnalysisOutcome],
) -> tuple[StructureAnalysisOutcome, list[str]]:
    """묶음별 분석 결과를 하나로 합친다.

    - 패턴은 정규 JSON(키 정렬) 기준으로 중복을 제거하고 처음 나온 순서를 지킨다.
    - query_guide는 비어 있지 않은 것을 중복 없이 빈 줄(``\\n\\n``)로 잇는다.
    - 실패 묶음은 결과에서 빼고 오류 목록(``묶음 N: 사유``)으로 돌려준다 — 초안 검증에서
      `analysis_errors`로 넘기면 승인이 막힌다(부분 결과를 조용히 승인하지 않게).
    - 성공 묶음이 하나도 없으면 status=failed.

    Args:
        outcomes: 묶음 순서대로의 분석 결과

    Returns:
        (합친 결과, 실패 묶음 오류 목록)
    """
    errors: list[str] = []
    patterns: list[Any] = []
    seen: set[str] = set()
    guides: list[str] = []
    succeeded = 0
    for index, outcome in enumerate(outcomes, start=1):
        if outcome.status == "failed" or outcome.meta is None:
            errors.append(f"묶음 {index}: {outcome.error or '분석 실패(사유 없음)'}")
            continue
        succeeded += 1
        for pattern in outcome.meta.get("patterns") or []:
            key = json.dumps(pattern, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                seen.add(key)
                patterns.append(pattern)
        guide = str(outcome.meta.get("query_guide") or "").strip()
        if guide and guide not in guides:
            guides.append(guide)

    if succeeded == 0:
        reason = "; ".join(errors) or "분석 결과가 없습니다"
        return StructureAnalysisOutcome(status="failed", error=reason), errors
    meta = {"patterns": patterns, "query_guide": "\n\n".join(guides)}
    status: AnalysisStatus = "ok" if patterns else "no_patterns"
    return StructureAnalysisOutcome(status=status, meta=meta), errors


def _column_maps(
    tables: Mapping[str, Any], name: str
) -> list[dict[str, Mapping[str, Any]]]:
    """스냅샷에서 테이블을 찾아 `{컬럼 casefold: 속성}` 목록을 돌려준다.

    정확 일치가 있으면 그것만, 없으면 대소문자·스키마 접두 무시로 걸리는 테이블 전부.
    """
    def _as_map(table: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
        columns = table.get("columns") or {}
        return {
            str(col).casefold(): attrs for col, attrs in columns.items()
            if isinstance(attrs, Mapping)
        }

    if name in tables and isinstance(tables[name], Mapping):
        return [_as_map(tables[name])]
    target = bare_name(name)
    return [
        _as_map(data) for key, data in tables.items()
        if bare_name(key) == target and isinstance(data, Mapping)
    ]


def _column_type(tables: Mapping[str, Any], table: str, column: str) -> str | None:
    """스냅샷에서 컬럼 타입을 찾는다(없으면 None)."""
    for columns in _column_maps(tables, table):
        attrs = columns.get(column.casefold())
        if attrs is not None:
            return str(attrs.get("type") or "")
    return None


def _join_pairs(patterns: Sequence[Any]) -> list[tuple[str, tuple[str, str], tuple[str, str]]]:
    """타입 호환을 확인할 조인 컬럼 쌍 — join_condition · direct_join · 계층 parent↔id."""
    pairs: list[tuple[str, tuple[str, str], tuple[str, str]]] = []
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, Mapping):
            continue
        base = f"patterns[{i}]"
        condition = pattern.get("join_condition")
        if isinstance(condition, str):
            for left, right in parse_join_pairs(condition):
                pairs.append((f"{base}.join_condition", left, right))
        direct = pattern.get("direct_join")
        entity_table = str(pattern.get("entity_table") or "")
        config_table = str(pattern.get("config_table") or "")
        if isinstance(direct, Mapping) and entity_table and config_table:
            entity_col = str(direct.get("entity_column") or "")
            config_col = str(direct.get("config_column") or "")
            if entity_col and config_col:
                pairs.append((
                    f"{base}.direct_join",
                    (entity_table, entity_col),
                    (config_table, config_col),
                ))
        table = str(pattern.get("table") or "")
        id_col = str(pattern.get("id_column") or "")
        parent_col = str(pattern.get("parent_column") or "")
        if table and id_col and parent_col:
            pairs.append((f"{base}.parent_column", (table, parent_col), (table, id_col)))
    return pairs


def validate_structure_draft(
    meta: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    sample_attempts: Sequence[Mapping[str, Any]],
    *,
    analysis_errors: Sequence[str] = (),
) -> dict[str, Any]:
    """구조 초안을 결정적으로 검증한다(센서 4종 · 하나라도 실패하면 승인 불가).

    ① ``refs_exist`` — 패턴이 참조하는 테이블·컬럼이 스냅샷에 실존(`structure_refs` 재사용 ·
       대소문자·스키마 접두 무시)
    ② ``join_types`` — 조인 조건(`join_condition`·`direct_join`·계층 parent↔id) 양쪽 컬럼의
       타입 계열(숫자·문자·날짜)이 같음. 모르는 타입·없는 컬럼(①이 잡는다)은 건너뛴다
    ③ ``sample_sql_safe`` — 샘플 시도에 unsafe가 0건(기록된 SQL도 다시 검사)
    ④ ``sample_exec`` — 행이 있는 성공(ok) 시도가 1건 이상
    패턴 0건(no_patterns) 초안은 ③④가 해당 없음(통과)이다. `analysis_errors`(일부 묶음 실패)가
    있으면 검사와 무관하게 통과하지 않는다.

    Args:
        meta: 초안 구조 정보 ``{"patterns": [...], "query_guide": str}``
        snapshot: `build_snapshot` 결과
        sample_attempts: `SampleCollection.attempts`
        analysis_errors: `merge_outcomes`의 실패 묶음 오류

    Returns:
        ``{"passed": bool, "checks": [{"code", "passed", "failures": [str]}],
        "analysis_errors": [str]}``
    """
    tables: Mapping[str, Any] = snapshot.get("tables") or {}
    raw_patterns = meta.get("patterns")
    patterns: list[Any] = raw_patterns if isinstance(raw_patterns, list) else []

    # ① 참조 실존
    ref_failures: list[str] = []
    if raw_patterns is not None and not isinstance(raw_patterns, list):
        ref_failures.append("patterns가 배열이 아님")
    for ref in structure_refs(meta):
        column_maps = _column_maps(tables, ref["table"])
        if not column_maps:
            message = f"{ref['path']}: 테이블 '{ref['table']}'이(가) 스냅샷에 없음"
        elif ref["column"] and not any(ref["column"].casefold() in m for m in column_maps):
            message = f"{ref['path']}: 컬럼 '{ref['table']}.{ref['column']}'이(가) 스냅샷에 없음"
        else:
            continue
        if message not in ref_failures:
            ref_failures.append(message)

    # ② 조인 컬럼 타입 호환
    type_failures: list[str] = []
    for path, (l_table, l_col), (r_table, r_col) in _join_pairs(patterns):
        l_type = _column_type(tables, l_table, l_col)
        r_type = _column_type(tables, r_table, r_col)
        if l_type is None or r_type is None:
            continue
        l_family, r_family = _type_family(l_type), _type_family(r_type)
        if l_family and r_family and l_family != r_family:
            type_failures.append(
                f"{path}: {l_table}.{l_col}({l_type}) ↔ {r_table}.{r_col}({r_type}) "
                f"타입 계열 불일치({l_family}·{r_family})"
            )

    # ③ 샘플 SQL 안전성 · ④ 샘플 실행 — 패턴이 없으면 해당 없음
    safe_failures: list[str] = []
    exec_failures: list[str] = []
    if patterns:
        for attempt in sample_attempts:
            purpose = str(attempt.get("purpose", ""))
            sql = str(attempt.get("sql") or "")
            if attempt.get("status") == "unsafe" or (sql and not validate_sample_sql(sql)):
                safe_failures.append(f"샘플 '{purpose}': 읽기 전용·LIMIT 규칙 위반")
        if not any(a.get("status") == "ok" for a in sample_attempts):
            if not sample_attempts:
                exec_failures.append("샘플 SQL 시도가 없습니다")
            for attempt in sample_attempts:
                status = attempt.get("status") or "미실행"
                detail = f" — {attempt['error']}" if attempt.get("error") else ""
                exec_failures.append(f"샘플 '{attempt.get('purpose', '')}': {status}{detail}")

    checks = [
        {"code": "refs_exist", "passed": not ref_failures, "failures": ref_failures},
        {"code": "join_types", "passed": not type_failures, "failures": type_failures},
        {"code": "sample_sql_safe", "passed": not safe_failures, "failures": safe_failures},
        {"code": "sample_exec", "passed": not exec_failures, "failures": exec_failures},
    ]
    errors = [str(e) for e in analysis_errors]
    return {
        "passed": all(c["passed"] for c in checks) and not errors,
        "checks": checks,
        "analysis_errors": errors,
    }
