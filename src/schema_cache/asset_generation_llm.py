"""스키마 자산 자동 생성 — 테이블 정의 초안(가져오기 · LLM 묶음) · LLM 호출 · LLM SQL 검증.

`asset_generation_service`(D-294 · plans/133 W4 · 테이블 정의 D-308 ① · plans/139 W3)가 쓰는 보조
타입·파서·검증을 둔다. 서비스 모듈에서 옮겨 왔다(plans/166 I3) — 옛 경로(옛 `_` 이름 포함)에서도
재노출한다.

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import yaml  # type: ignore[import-untyped]
from langchain_core.messages import HumanMessage

from src.domain import schema_inference as inference
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import (
    FIELDS,
    KEY_COLUMNS_MAX,
    KINDS,
    MANAGES_MAX_CHARS,
    ORIGIN_LLM,
    describe_value,
    validate_table_definitions,
)
from src.prompts.asset_generation import TABLE_DEFINITIONS_PROMPT
from src.schema_cache.db_structure_service import DraftNotApprovable
from src.schema_cache.structure_analysis import parse_llm_json, validate_sample_sql
from src.security.sql_guard import SQLGuard
from src.utils.json_extract import coerce_content_text
from src.utils.sql_dialect import row_limit_clause

# 로거 이름은 이동 전 경로 고정 — 로그 %(name)s 불변(plans/166 I3)
logger = logging.getLogger("src.schema_cache.asset_generation_service")

#: LLM SQL 검증기 ``(sql, schema_info, engine, db_id) → 오류 목록`` — 질의 경로 `validate_sql`(참조
#: 테이블·컬럼 실존 포함)은 application 계층이라 이 모듈이 import하지 않고 조립부(API)가 주입한다.
#: ``db_id``는 DB별 검증 정책(한글 식별자 허용 — plans/137)을 조립부가 레지스트리에서 해석하는 데
#: 쓴다
SqlChecker = Callable[[str, Mapping[str, Any], str, str], list[str]]

EXAMPLE_COUNT = 5
SECTION_MAX_CHARS = 6000
SQL_CHECK_LIMIT = 50
#: 테이블 정의 LLM 묶음 크기(군 접두 단위 · D-308)
DEFINITION_BATCH_SIZE = 10
#: 테이블 정의 LLM 입력의 컬럼 설명 길이 상한(자)
DEFINITION_DESCRIPTION_MAX_CHARS = 100
#: 테이블 정의 LLM 입력의 컬럼당 코드 라벨 수 상한
DEFINITION_LABELS_MAX = 10

# 섹션의 코드 펜스 — 언어 표기와 상관없이 전부 SQL로 보고 검사한다(표기 없는 펜스로 검증 우회 금지)
FENCE_RE = re.compile(r"```[^\n`]*\n?(.*?)```", re.DOTALL)
MAX_SECTION_SQL_BLOCKS = 5
# LLM SQL을 실행하기 전에 막는 부수효과·서버 조작 함수(읽기 전용 계정에서도 일부는 동작한다)
SIDE_EFFECT_FUNC_RE = re.compile(
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


# ──────────────────────────────────────────────
# 테이블 정의 (D-308 ① · plans/139 W3)
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


class _NoAliasSafeLoader(yaml.SafeLoader):  # type: ignore[misc]  # yaml 스텁 없음(Any)
    """앵커(&)·별칭(*)을 거절하는 SafeLoader.

    작은 입력이 별칭 펼치기로 거대한 값이 되는 것을 막는다 — 노드를 만들기 전(펼치기 전)에
    이벤트를 보고 거절한다.
    """

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None) is not None:
            raise yaml.composer.ComposerError(
                None, None, "앵커(&)·별칭(*)은 쓸 수 없습니다", event.start_mark,
            )
        return super().compose_node(parent, index)


def load_import_yaml(text: str) -> Any:
    """테이블 정의 가져오기 YAML을 읽는다 — 앵커·별칭 거절 · 문법 오류·깊은 중첩은 사용자 오류.

    Raises:
        ValueError: YAML 문법 오류 · 앵커·별칭 · 중첩이 너무 깊음(API는 422로 돌려준다)
    """
    try:
        return yaml.load(text, Loader=_NoAliasSafeLoader)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML을 읽지 못했습니다: {e}") from e
    except RecursionError as e:
        raise ValueError("YAML을 읽지 못했습니다: 중첩이 너무 깊습니다") from e


def _display_text(value: Any) -> str:
    """표시용 사본의 글 한 칸 — 문자열은 그대로, 그 밖의 값은 타입 이름 + 짧은 발췌."""
    return value if isinstance(value, str) else describe_value(value)


def display_row(raw: Any, origin: str) -> dict[str, Any]:
    """검증 실패 행의 표시용 사본 — 아는 필드만 · 글은 문자열로(JSON 저장 가능 · 승인 불가)."""
    fields: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    row: dict[str, Any] = {}
    for name in FIELDS:
        value = fields.get(name)
        if value is None:
            continue
        if name == "key_columns":
            row[name] = [_display_text(v) for v in value] if isinstance(
                value, (list, tuple)
            ) else [_display_text(value)]
        elif name == "related":
            row[name] = (
                {str(k): _display_text(v) for k, v in value.items()}
                if isinstance(value, Mapping) else {}
            )
        else:
            row[name] = _display_text(value)
    row.setdefault("manages", "")
    row.setdefault("origin", origin)
    return row


def definition_rows(
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
        rows.setdefault(key, display_row(raw.get(label), origin))
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


def _cap_llm_key_columns(table: str, item: Any) -> Any:
    """LLM 초안 1건의 대표 컬럼이 상한을 넘으면 앞 `KEY_COLUMNS_MAX`개로 자른다(거절 대신 교정).

    LLM은 프롬프트의 개수 지시를 어기기도 한다 — 결정적으로 자르고 로그를 남긴다. 가져오기·편집
    경로는 자르지 않고 종전대로 검증에서 거절한다.
    """
    if not isinstance(item, Mapping):
        return item
    keys = item.get("key_columns")
    if not isinstance(keys, (list, tuple)) or len(keys) <= KEY_COLUMNS_MAX:
        return item
    logger.info("테이블 정의 LLM 초안 key_columns 절단: table=%s, %d개 → 앞 %d개",
                table, len(keys), KEY_COLUMNS_MAX)
    return {**item, "key_columns": list(keys[:KEY_COLUMNS_MAX])}


def merge_definition_batch(
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
        answered = {t: _cap_llm_key_columns(t, raw[t]) for t in targets if t in raw}
        new_rows, new_errors = definition_rows(answered, table_columns, ORIGIN_LLM)
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


async def ask(llm: Any, prompt: str) -> str:
    """LLM 1회 호출 — 실패는 빈 문자열(검증 단계가 사유를 남긴다)."""
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
    except Exception as e:  # noqa: BLE001 — LLM 실패는 검증 실패로 드러난다
        logger.warning("자산 LLM 호출 실패: %s", e)
        return ""
    return coerce_content_text(getattr(response, "content", response))


@dataclass
class SqlCheck:
    """LLM SQL 실행 검증 문맥 — 클라이언트 · 주입된 검증기 · 스냅샷 스키마 · 엔진 · 소스."""

    client: Any
    checker: SqlChecker | None
    schema_info: Mapping[str, Any]
    engine: str
    db_id: str = ""


async def execute_check(check: SqlCheck, sql: str) -> tuple[int | None, str | None]:
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
    if (found := SIDE_EFFECT_FUNC_RE.search(text)) is not None:
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


async def validate_examples(
    check: SqlCheck, raw: str
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
        entry["rows"], entry["error"] = await execute_check(check, sql)
        if entry["error"] is None and len(passed) < EXAMPLE_COUNT:
            passed.append({"question": question, "sql": sql})
    return passed, checks


async def validate_section(
    check: SqlCheck, raw: str, snapshot: Mapping[str, Any]
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
    blocks = [b.strip() for b in FENCE_RE.findall(section)]
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
    prose = FENCE_RE.sub(" ", section)
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
            rows, error = await execute_check(check, block)
            sql_checks.append({"sql": block, "rows": rows, "error": error})
            if error:
                errors.append(f"섹션 SQL 검사·실행 실패: {error}")
    return section, {
        "passed": not errors, "errors": errors, "sql_checks": sql_checks,
        "length": len(section), "unknown_identifiers": unknown,
    }


# 옛 이름 별칭(D-337 ③ · 1사이클) — 모듈 밖에서 옛 이름으로 쓴다
_definition_rows = definition_rows
_display_row = display_row
_merge_definition_batch = merge_definition_batch
_ask = ask
_SqlCheck = SqlCheck
_execute_check = execute_check
_validate_examples = validate_examples
_validate_section = validate_section
_FENCE_RE = FENCE_RE
_SIDE_EFFECT_FUNC_RE = SIDE_EFFECT_FUNC_RE
