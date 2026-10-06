"""오라클 대조 어댑터 · SQL 분석 · 실패 분류 (plans/135 §3.3 · W4).

판정은 전부 **결정적**이다(LLM 0). SQL 은 파서 없이 주석·리터럴을 지운 뒤 본다. 방언 함정은 샌드박스
리허설(`testdata/itam/README.md`)로 **실측 확인된 4종만** 잡는다 — 추정 패턴으로 오탐을 만들지
않는다.

이 모듈은 메모리의 원값(실행 SQL · 결과 행 · 응답 문장)을 읽어 판정하지만 **아무것도 기록하지
않는다** — 기록은 `redact` 를 거친 뒤 드라이버가 한다.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

#: 실패 분류 → 고칠 곳(plans/135 §3.3 표). 순서가 리포트의 「첫 원인」 우선순위다.
TAXONOMY: dict[str, str] = {
    "permission_denied": "벤치 환경 문제(계정 인가 D-232) — 판정 제외",
    "routing_miss": "ITAM 프롬프트 밖 — 소스 선별(plans/132) · 분리 집계",
    "asked_back": "프롬프트 모호성 · 되묻기 규칙(plans/132) · 분리 집계",
    "backend_limit": "plans/139 W1·W2 LLM 입력 한도 — 조회 대상 크기(`schema_context.dbs`) · "
    "테이블 정의 선별(W4)",
    "selection_none": "plans/139 W4 선별 0개 — 「DB 구조」 탭 테이블 정의(manages·key_columns)",
    "no_sql": "plans/133 A8 · 프로필 query_guide",
    "fabricated": "plans/133 A8 · 고지 규칙",
    "dialect_error": "plans/133 A8 DB 전용 프롬프트 섹션 · 프로필 방언 규칙",
    "dialect_silent": "plans/133 A8 · 반복되면 결정적 교정 후보(plans/95 §4.2)",
    "schema_miss": "plans/133 A2 조회 대상 · A1 관계",
    "meaning_absent": "plans/133 A3 컬럼 설명",
    "column_misread": "plans/133 A3 컬럼 설명·유사어",
    "date_text": "plans/133 A6 쿼리 규칙",
    "join_key_partial": "plans/133 A1 · A6",
    "code_value": "plans/133 A4",
    "key_mismatch": "수동 검토",
}
#: 소스 선별·되묻기는 ITAM 프롬프트 수치와 분리 집계한다(§3.3).
SEPARATE = frozenset({"routing_miss", "asked_back", "permission_denied"})

_COMMENTS = re.compile(r"/\*.*?\*/|--[^\n]*", re.S)
_SINGLE_LITERAL = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_DOUBLE_LITERAL = re.compile(r"\"(?:[^\"\\]|\\.|\"\")*\"")
_SYNTAX_ERROR = re.compile(r"(?i)\b1064\b|syntax")
_DATE_FUNCS = re.compile(
    r"(?i)\b(curdate|current_date|now|sysdate|date_add|date_sub|adddate|subdate)\s*\(|"
    r"\bcurrent_date\b|\binterval\b|\bdate\s*'|\bdate\s*\("
)
_DATE_SAFE = re.compile(r"(?i)\b(date_format|str_to_date)\s*\(")
#: 술어 조각 경계 — 쉼표로는 자르지 않는다(함수 인자 `DATEDIFF(col, NOW())`가 갈라진다).
_PREDICATE_SPLIT = re.compile(
    r"(?i)\b(?:and|or|where|on|having|when|then|else|select|from|group|order)\b|;"
)
_COMPARISON = re.compile(r"(?i)[<>=]|\bbetween\b|\b(?:datediff|timestampdiff)\s*\(")
_TABULAR_LINE = re.compile(r"^\s*(\|.*\||[-*•]\s+\S|\d+[.)]\s+\S)")


def strip_comments(sql: str) -> str:
    return _COMMENTS.sub(" ", sql or "")


def strip_literals(sql: str) -> str:
    """주석·작은따옴표 리터럴을 지운다.

    큰따옴표는 남긴다 — MariaDB 에서 문자열이지만 식별자 오용의 증거다.
    """
    return _SINGLE_LITERAL.sub("''", strip_comments(sql))


def _bare(name: str) -> str:
    return str(name).strip('`"').rsplit(".", 1)[-1]


def sql_tables(sql: str) -> list[str]:
    """사용 테이블(백틱을 먼저 벗긴다 — `_extract_table_names` 식별자 패턴이 `[\\w]+`다)."""
    from src.sql_validation import _extract_table_names

    return sorted({_bare(t) for t in _extract_table_names(strip_literals(sql).replace("`", ""))})


def sql_columns(sql: str, catalog_columns: Iterable[str]) -> list[str]:
    """사용 컬럼 — 카탈로그 컬럼 이름의 단어 경계 일치(대소문자 무시 — MariaDB 컬럼 이름 규칙)."""
    body = _DOUBLE_LITERAL.sub(" ", strip_literals(sql))
    present = {token.casefold() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body)}
    return sorted({c for c in catalog_columns if c.casefold() in present})


def dialect_hazards(sql: str, identifiers: Iterable[str]) -> list[str]:
    """실측 확인된 방언 함정 4종(정적 · 실행 성공에도 침묵 오답이 되는 것 포함)."""
    body = strip_literals(sql)
    names = {str(n).casefold() for n in identifiers}
    hazards = []
    if "||" in _DOUBLE_LITERAL.sub(" ", body):
        hazards.append("pipes_concat")
    if any(m.group(0)[1:-1].casefold() in names for m in _DOUBLE_LITERAL.finditer(body)):
        hazards.append("double_quoted_identifier")
    if re.search(r"::\s*[A-Za-z]", body):
        hazards.append("pg_cast")
    if re.search(r"(?i)\binterval\s+'\s*\d+\s+[a-z]+\s*'", strip_comments(sql)):
        hazards.append("interval_string")
    return hazards


def date_text_misuse(sql: str, date_columns: Iterable[str]) -> bool:
    """`CHAR(8)` 날짜 문자열 컬럼을 날짜 함수·DATE 리터럴과 직접 비교했는가(술어 조각 단위).

    같은 조각에 `DATE_FORMAT(`·`STR_TO_DATE(`가 있으면 변환했다고 본다.
    """
    columns = {c.casefold() for c in date_columns}
    if not columns:
        return False
    for segment in _PREDICATE_SPLIT.split(strip_comments(sql)):
        tokens = {t.casefold() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", segment)}
        if (
            tokens & columns
            and _COMPARISON.search(segment)
            and _DATE_FUNCS.search(segment)
            and not _DATE_SAFE.search(segment)
        ):
            return True
    return False


def join_key_partial(sql: str, key_groups: Iterable[tuple[frozenset[str], Sequence[str]]]) -> bool:
    """같은 복합 키를 가진 테이블 둘을 함께 쓰면서 조인 조건이 키 전부를 잇지 않았는가.

    `key_groups`: (테이블 이름 집합(소문자), 공통 키 컬럼 목록). 조인 조건은 `a.k = b.k`(같은 이름)·
    `USING (k, …)` 형태만 센다.
    """
    body = strip_literals(sql)
    used = {t.casefold() for t in sql_tables(sql)}
    joined = {
        m.group(2).casefold()
        for m in re.finditer(r"\b\w+\.(\w+)\s*=\s*\w+\.(\w+)\b", body)
        if m.group(1).casefold() == m.group(2).casefold()
    }
    for using in re.finditer(r"(?i)\bUSING\s*\(([^)]*)\)", body):
        joined |= {part.strip().strip("`").casefold() for part in using.group(1).split(",")}
    for tables, keys in key_groups:
        if len(tables & used) >= 2 and not {k.casefold() for k in keys} <= joined:
            return True
    return False


# --- 오라클 어댑터 (§3.3 v1.2) -------------------------------------------------------


def _first_name(ref: Any) -> str:
    return str(ref[0] if isinstance(ref, list) else ref)


def evaluate(
    spec: Mapping[str, Any],
    outcome: Mapping[str, Any] | None,
    result: Mapping[str, Any] | None,
    *,
    count_rows_ok: bool = False,
) -> tuple[str, Any, str]:
    """하네스 `evaluate_oracle` + 벤치 어댑터 2종 → (판정, 상세, 판정 방식).

    판정 방식: `as_is` · `single_cell_renamed`(1×1 결과의 열 이름을 명세 첫 후보로 바꿔 판정) ·
    `count_fallback`(건수 질문에 목록으로 답함 → 같은 오라클을 행 수로 판정).
    """
    from scripts.scenario.oracle import _resolve, evaluate_oracle

    spec = dict(spec)
    mode = "as_is"
    rows = list((result or {}).get("rows") or [])
    columns = list((result or {}).get("columns") or [])
    if (
        spec.get("compare") == "value"
        and isinstance(result, Mapping)
        and result.get("status") == "ok"
    ):
        if count_rows_ok and len(rows) != 1:
            spec = {k: v for k, v in spec.items() if k != "value"}
            spec["compare"] = "count"
            mode = "count_fallback"
        elif len(rows) == 1 and len(columns) == 1 and _resolve(spec.get("value"), columns) is None:
            new = _first_name(spec["value"])
            result = {**result, "columns": [new], "rows": [{new: rows[0].get(columns[0])}]}
            mode = "single_cell_renamed"
    verdict, detail = evaluate_oracle(
        spec, dict(outcome) if outcome else None, dict(result) if result else None
    )
    return verdict, detail, mode


_KEY_LISTS = (
    "missing",
    "extra",
    "overlap",
    "lost_stable",
    "outside_union",
    "missing_must",
    "not_allowed",
    "system_top",
    "oracle_top",
)
_ASCII_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sanitize_detail(
    detail: Any,
    *,
    value_grade: str | None,
    keys_allowed: bool = True,
    labels: Mapping[str, str] | None = None,
) -> Any:
    """판정 상세 거르기(§3.5.6).

    - 금액 값 열의 행별 값은 지운다(`oracle_top`의 값 · `value_diffs`의 수치).
    - `keys_allowed=False`(시스템 쪽 키 열이 일반 컬럼이 아니거나 값이 강등됨)면 키 목록을 건수로만
      남긴다 — 키 명세가 general 이어도 시스템 값은 별칭으로 찾은 「아무 열」에서 온다.
    - `header`(시스템이 지은 열 이름 — 값일 수 있다)는 요약과 같은 이름(`labels`)으로 바꾸고, 모르는
      이름은 ASCII 식별자 모양일 때만 남긴다.
    """
    if not isinstance(detail, Mapping):
        return detail
    out = copy.deepcopy(dict(detail))
    if value_grade == "amount":
        if isinstance(out.get("oracle_top"), list):
            out["oracle_top"] = [
                list(item[:-1]) for item in out["oracle_top"] if isinstance(item, list)
            ]
        if isinstance(out.get("value_diffs"), list):
            out["value_diffs"] = [
                {"key": d.get("key"), "differs": True}
                for d in out["value_diffs"]
                if isinstance(d, Mapping)
            ]
    if not keys_allowed:
        for name in _KEY_LISTS:
            if isinstance(out.get(name), list):
                out[name] = len(out[name])
        if isinstance(out.get("value_diffs"), list):
            out["value_diffs"] = len(out["value_diffs"])
    if isinstance(out.get("header"), list):
        known = dict(labels or {})
        out["header"] = [
            known.get(str(name), str(name) if _ASCII_NAME.match(str(name)) else f"열#{i}")
            for i, name in enumerate(out["header"], start=1)
        ]
    return out


# --- 실패 분류 -------------------------------------------------------------------------


@dataclass
class TurnFacts:
    """턴 하나의 판정 재료(메모리 전용 — 원값 포함)."""

    status: str
    expected_db_ids: list[str] = field(default_factory=list)
    observed_db_ids: list[str] = field(default_factory=list)
    executed: list[dict[str, Any]] = field(default_factory=list)  # sql·source·success·error
    response: str = ""
    result: Mapping[str, Any] | None = None
    verdict: str | None = None  # pass·fail·hold · observe 턴은 None
    observe: Mapping[str, Any] | None = None
    reply_declared: bool = False
    key_refs: list[list[str]] = field(default_factory=list)
    gold_tables: list[str] = field(default_factory=list)
    schema_context: Mapping[str, Any] | None = None
    access_denied: bool = False


@dataclass(frozen=True)
class CatalogFacts:
    """카탈로그에서 뽑은 판정용 사실 — 컬럼·테이블 이름 · 날짜 문자열 컬럼 · 복합 키 군."""

    columns: frozenset[str]
    tables: frozenset[str]
    date_columns: frozenset[str]
    key_groups: tuple[tuple[frozenset[str], tuple[str, ...]], ...]
    #: 승인된 코드값(plans/133 A4) — 컬럼 → 값. 비었으면 `code_value` 판정은 보류(분류하지 않는다).
    code_values: Mapping[str, frozenset[str]] = field(default_factory=dict)

    @classmethod
    def from_catalog(
        cls, catalog: Mapping[str, Any], *, code_values: Mapping[str, Any] | None = None
    ) -> CatalogFacts:
        """카탈로그(이름·값 종류·키) + 승인 프로필 코드값.

        코드값은 메모리 전용이다 — 카탈로그에는 건수만 싣는다.
        """
        tables = catalog.get("tables") or {}
        columns = {c["name"] for t in tables.values() for c in t.get("columns") or []}
        dates = {
            c["name"]
            for t in tables.values()
            for c in t.get("columns") or []
            if c.get("value_kind") == "date_text_yyyymmdd"
        }
        groups = []
        for group in catalog.get("same_key_groups") or []:
            keys = tuple(tables.get(group[0], {}).get("key") or [])
            if keys:
                groups.append((frozenset(t.casefold() for t in group), keys))
        codes = {
            str(key).rsplit(".", 1)[-1]: frozenset(str(v) for v in values)
            for key, values in (code_values or {}).items()
            if isinstance(values, (list, tuple)) and values
        }
        return cls(frozenset(columns), frozenset(tables), frozenset(dates), tuple(groups), codes)


def _sqls_for(facts: TurnFacts, db_id: str) -> list[dict[str, Any]]:
    return [e for e in facts.executed if e.get("sql") and (e.get("source") in (None, "", db_id))]


def _has_tabular_answer(response: str) -> bool:
    lines = [line for line in (response or "").splitlines() if _TABULAR_LINE.match(line)]
    return len(lines) >= 2


def analyze_sql(facts: TurnFacts, catalog: CatalogFacts, *, db_id: str) -> dict[str, Any]:
    """실행 SQL 분석 — 테이블·컬럼·방언 함정·날짜 문자열 오용·조인 키 부족.

    기록해도 되는 이름·불린만 돌려준다.
    """
    entries = _sqls_for(facts, db_id)
    tables: set[str] = set()
    columns: set[str] = set()
    hazards: set[str] = set()
    date_misuse = join_partial = schema_probe = unknown_code = False
    identifiers = catalog.columns | catalog.tables
    for entry in entries:
        sql = str(entry["sql"])
        tables |= set(sql_tables(sql))
        columns |= set(sql_columns(sql, catalog.columns))
        hazards |= set(dialect_hazards(sql, identifiers))
        date_misuse = date_misuse or date_text_misuse(sql, catalog.date_columns)
        join_partial = join_partial or join_key_partial(sql, catalog.key_groups)
        schema_probe = schema_probe or bool(
            re.search(r"(?i)information_schema|show\s+(create|columns)", strip_comments(sql))
        )
        unknown_code = unknown_code or _unknown_code_value(sql, catalog.code_values)
    used = {c.casefold() for c in columns}
    key_used = [alt for ref in facts.key_refs for alt in ref if alt.casefold() in used]
    return {
        "tables": sorted(tables),
        "columns": sorted(columns),
        "key_columns_used": sorted(set(key_used)),
        "dialect_hazards": sorted(hazards),
        "date_text_misuse": date_misuse,
        "join_key_partial": join_partial,
        "schema_query_by_system": schema_probe,
        "unknown_code_value": unknown_code if catalog.code_values else None,
        "sql_count": len(entries),
        "sql_count_all": sum(1 for e in facts.executed if e.get("sql")),
        "failed_syntax": sum(
            1
            for e in entries
            if e.get("success") is False and _SYNTAX_ERROR.search(str(e.get("error") or ""))
        ),
    }


def classify(facts: TurnFacts, analysis: Mapping[str, Any], *, db_id: str) -> list[str]:
    """실패 분류(한 턴 여러 개 · `TAXONOMY` 순서). 통과 턴은 빈 목록."""
    if facts.access_denied:
        return ["permission_denied"]
    labels: set[str] = set()
    observed = set(facts.observed_db_ids) | {
        str(e.get("source")) for e in facts.executed if e.get("source")
    }
    if facts.expected_db_ids and not set(facts.expected_db_ids) <= observed:
        labels.add("routing_miss")
    if facts.status == "clarification" and not facts.reply_declared and facts.observe is None:
        labels.add("asked_back")
    if analysis.get("failed_syntax"):
        labels.add("dialect_error")
    if analysis.get("dialect_hazards"):
        labels.add("dialect_silent")
    labels |= _prompt_stop_labels(facts.schema_context, db_id)
    rows = list((facts.result or {}).get("rows") or [])
    if facts.observe is not None:
        if facts.observe.get("no_data") and _fabricated(facts, rows):
            labels.add("fabricated")
        return [
            label
            for label in TAXONOMY
            if label in labels
            and label
            in (
                "routing_miss",
                "backend_limit",
                "selection_none",
                "fabricated",
                "dialect_error",
                "dialect_silent",
            )
        ]
    if facts.verdict == "pass":
        return []
    if not analysis.get("sql_count_all") and facts.status != "clarification":
        labels.add("no_sql")
    if analysis.get("date_text_misuse"):
        labels.add("date_text")
    if analysis.get("join_key_partial"):
        labels.add("join_key_partial")
    if analysis.get("unknown_code_value"):
        labels.add("code_value")
    context = facts.schema_context
    if context is not None:
        if facts.gold_tables and not context.get("gold_tables_presented"):
            labels.add("schema_miss")
        with_meaning = context.get("key_columns_with_meaning")
        presented = {c.casefold() for c in context.get("key_columns_presented") or []}
        if with_meaning is not None:
            meaning = {c.casefold() for c in with_meaning}
            if any(
                any(a.casefold() in presented for a in ref)
                and not any(a.casefold() in meaning for a in ref)
                for ref in facts.key_refs
            ):
                labels.add("meaning_absent")
    if analysis.get("sql_count"):
        used = {c.casefold() for c in analysis.get("key_columns_used") or []}
        if any(not any(a.casefold() in used for a in ref) for ref in facts.key_refs):
            labels.add("column_misread")
    if facts.verdict == "fail" and not labels:
        labels.add("key_mismatch")
    return [label for label in TAXONOMY if label in labels]


def _prompt_stop_labels(context: Mapping[str, Any] | None, db_id: str) -> set[str]:
    """측정 연결점이 옮긴 DB 별 프롬프트 칸(plans/139 W6-d) → `backend_limit`·`selection_none`.

    입력 한도: 재생성 종결 사유 `backend_limit` · 예산 단계 `exceeded` · 백엔드 보고 토큰 수 있음.
    선별 0개: 선별 출처 `none` · 종결 사유 `selection_none`. 이 벤치 DB 칸만 본다.
    """
    entry = ((context or {}).get("dbs") or {}).get(db_id)
    if not isinstance(entry, Mapping):
        return set()
    reasons = set(entry.get("stop_reasons") or [])
    labels: set[str] = set()
    if (
        "backend_limit" in reasons
        or entry.get("budget_stage") == "exceeded"
        or entry.get("backend_reported_tokens") is not None
    ):
        labels.add("backend_limit")
    if "selection_none" in reasons or entry.get("selection_source") == "none":
        labels.add("selection_none")
    return labels


def _unknown_code_value(sql: str, code_values: Mapping[str, frozenset[str]]) -> bool:
    """코드 컬럼과 비교한 리터럴이 승인된 코드값에 없는가(A4 미승인 = 판정하지 않음)."""
    from src.utils.synonym_usage import _extract_column_literals

    for column, allowed in code_values.items():
        if any(
            value not in allowed
            for value in _extract_column_literals(strip_comments(sql), [column])
        ):
            return True
    return False


def _fabricated(facts: TurnFacts, rows: list[Mapping[str, Any]]) -> bool:
    """데이터가 없어야 할 질문에 값을 내놓았는가.

    결과 행의 핵심 컬럼이 채워졌거나, SQL 없이 표·목록을 냈다.
    """
    if not any(e.get("sql") for e in facts.executed):
        return _has_tabular_answer(facts.response)
    if not rows:
        return False
    if not facts.key_refs:
        return True
    keys = {a.casefold() for ref in facts.key_refs for a in ref}
    return any(
        str(k).casefold() in keys and str(v or "").strip() not in ("", "None", "null")
        for row in rows
        for k, v in row.items()
    )


#: 예산 단계·선별 출처의 「나쁜 정도」 순서.
#: 한 턴에 같은 DB task 가 여럿이면 가장 나쁜 값을 남긴다.
_STAGE_RANK = {"within": 0, "materials": 1, "samples": 2, "exceeded": 3}
_SELECTION_RANK = {"llm": 0, "lexical": 1, "none": 2}


def _worst(current: Any, new: Any, rank: Mapping[str, int]) -> Any:
    if new not in rank:
        return current
    return new if current not in rank or rank[new] > rank[current] else current


def _larger(current: Any, new: Any) -> Any:
    if not isinstance(new, int) or isinstance(new, bool):
        return current
    return new if current is None else max(current, new)


def prompt_by_db(records: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """측정 수신 레코드의 DB 별 프롬프트 칸(plans/139 W6-d) → 턴 단위 DB 별 요약.

    같은 DB task 가 여럿이면 칸마다 따로 모은다 — 수는 최댓값, 예산 단계·선별 출처는 가장 나쁜 값,
    종결 사유는 정렬한 합집합. 칸이 없던 옛 레코드는 null 로 남는다(숫자·열거만 · 값 없음).
    """
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        for db_id, shape in (record.get("dbs") or {}).items():
            if not isinstance(shape, Mapping):
                continue
            entry = out.setdefault(
                str(db_id),
                {
                    "prompt_tokens_est": None,
                    "budget_stage": None,
                    "backend_reported_tokens": None,
                    "selection_source": None,
                    "selected_count": None,
                    "stop_reasons": [],
                },
            )
            for key in ("prompt_tokens_est", "backend_reported_tokens", "selected_count"):
                entry[key] = _larger(entry[key], shape.get(key))
            entry["budget_stage"] = _worst(
                entry["budget_stage"], shape.get("budget_stage"), _STAGE_RANK
            )
            entry["selection_source"] = _worst(
                entry["selection_source"], shape.get("selection_source"), _SELECTION_RANK
            )
            reason = shape.get("stop_reason")
            if isinstance(reason, str) and reason:
                entry["stop_reasons"] = sorted({*entry["stop_reasons"], reason})
    return out


def schema_context(
    records: Sequence[Mapping[str, Any]],
    *,
    db_id: str,
    gold_tables: list[str],
    key_refs: list[list[str]],
) -> dict[str, Any] | None:
    """측정 수신 레코드(같은 thread_id · 이 턴) → 턴의 스키마 맥락 요약(§3.4 (2)).

    레코드가 없으면 None. `dbs`는 이 턴에 잡힌 DB 전부의 프롬프트 크기·선별 칸이다(`prompt_by_db`).
    """
    if not records:
        return None
    tables: dict[str, set[str]] = {}
    meaning: set[str] | None = set()
    sample = structure = False
    for record in records:
        shape = (record.get("dbs") or {}).get(db_id)
        if not isinstance(shape, Mapping):
            continue
        structure = structure or bool(shape.get("structure_meta"))
        for name, table in (shape.get("tables") or {}).items():
            bare = _bare(name)
            tables.setdefault(bare, set()).update(table.get("columns") or [])
            sample = sample or bool(table.get("sample_rows"))
            if table.get("with_meaning") is None:
                meaning = None
            elif meaning is not None:
                meaning.update(table["with_meaning"])
    presented_cols = {c.casefold(): c for cols in tables.values() for c in cols}
    keys_presented = sorted(
        {
            presented_cols[a.casefold()]
            for ref in key_refs
            for a in ref
            if a.casefold() in presented_cols
        }
    )
    folded_tables = {t.casefold() for t in tables}
    return {
        "tasks": len(records),
        "presented_tables": sorted(tables),
        "presented_column_count": len(presented_cols),
        "columns_with_meaning": None if meaning is None else len(meaning),
        "sample_rows_presented": sample,
        "structure_meta_present": structure,
        "gold_tables_presented": all(g.casefold() in folded_tables for g in gold_tables),
        "key_columns_presented": keys_presented,
        "key_columns_with_meaning": None
        if meaning is None
        else sorted(c for c in keys_presented if c.casefold() in {m.casefold() for m in meaning}),
        "dbs": prompt_by_db(records),
    }
