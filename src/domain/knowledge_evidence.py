"""지식 자산 근거 묶음 — 반출 run 산출물에서 자산 작성 입력을 결정적으로 뽑는다.

plans/143 W1 · D-316 ①.

입력은 호출부가 이미 읽은 dict다(I/O 0): 반출 카탈로그(`schema_catalog.yaml`) · 턴
기록(`trace.jsonl` 행) · 시나리오 문서 · 현행 테이블 정의 · 조회 대상 · 원천 지식 파일 · 직전
검증 결과. 출력은 ``{파일 이름: 문서}``이고, 호출부가 YAML로 쓰기 전에 누출 관문을 통과시킨다.

**값을 싣지 않는다** — 반출물에도 값은 없지만 값이 실릴 수 있는 칸은 여기서 한 번 더 뺀다: 결과 열
요약(표본·상위 값·열 별칭) · P1 플래그 값(있는지만) · 오라클 판정 상세 · DB 주석(테이블·컬럼)의
코드 열거(주석 첫 마디와 쌍 수만 — `comment_enum`).

**결정적** — 시각을 찍지 않고 목록은 이름·ID 순으로 정렬한다(같은 입력 → 같은 출력).

파일
- `index.yaml` — run · 입력 유무 · 업무 영역 · 테이블 군 목록 · 파일 목록
- `tables.<군>.yaml` — 테이블 군(이름 앞 영문 접두)별 테이블: 조회 대상 여부 ·
  정의(`kind`·`manages`·`key_columns`·`related`·`notes`) · 컬럼(이름·타입·NULL·값 종류·기록
  등급·의미) · 선언·추론 관계
- `p1.yaml` — P1 근거(관계 채택·값 형식 비율·코드 판정) · 없으면 ``available: false``
- `turns.yaml` — 턴별 실패 사유(분류·결과 상태·ITAM SQL 없음)·가린 SQL·스키마 맥락 요약
- `scenarios.yaml` — 시나리오 질문·관찰 대상·오라클 ID(정답 SQL이 있으면 그 글)
- `previous_cycle.yaml` — 철회 항목(사유) · 직전 검증 실패 항목(코드·사유)

계층: domain — 순수 함수 · I/O·LLM 0 · 스키마 리터럴 0.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from src.domain.schema_inference import comment_label, parse_comment_enum
from src.domain.schema_snapshot import bare_name

INDEX_FILE = "index.yaml"
P1_FILE = "p1.yaml"
TURNS_FILE = "turns.yaml"
SCENARIOS_FILE = "scenarios.yaml"
PREVIOUS_FILE = "previous_cycle.yaml"
#: 테이블 군 파일 이름 형식
TABLES_FILE_FORMAT = "tables.{family}.yaml"
#: 영문 접두가 없는 테이블의 군 이름
OTHER_FAMILY = "other"

_FAMILY_RE = re.compile(r"[A-Za-z]+")
#: 테이블 정의에서 싣는 칸(순서 고정)
_DEFINITION_FIELDS: tuple[str, ...] = (
    "group", "kind", "manages", "key_columns", "related", "notes", "origin",
)
#: 컬럼에서 싣는 칸(값이 없는 구조 칸만)
_COLUMN_FIELDS: tuple[str, ...] = ("type", "nullable", "value_kind", "log_policy")
#: P1 컬럼 근거에서 싣는 스칼라 칸(플래그 값은 빼고 있는지만)
_P1_COLUMN_FIELDS: tuple[str, ...] = (
    "candidate", "code", "distinct", "truncated", "total", "mixed_case", "entity_key", "error",
)
#: P1 관계에서 싣는 칸
_P1_RELATION_FIELDS: tuple[str, ...] = (
    "from", "to", "columns", "origin", "overlap", "sampled", "accepted", "unique_parent", "error",
)
#: 실행 SQL에서 싣는 칸(가린 SQL · 성공 · 행 수 · 오류 문구)
_SQL_FIELDS: tuple[str, ...] = ("source", "success", "row_count", "retry_attempt", "error", "sql")


def table_family(name: str) -> str:
    """테이블 군 — 맨 이름 앞 영문 접두(소문자). 없으면 `OTHER_FAMILY`."""
    match = _FAMILY_RE.match(bare_name(str(name)))
    return match.group(0).lower() if match else OTHER_FAMILY


def _comment_parts(text: str) -> tuple[str | None, int]:
    """주석 → ``(실을 글, 코드 열거 쌍 수)`` — 열거가 있으면 첫 쌍 앞까지의 첫 마디만(값 0).

    첫 마디는 첫 쌍의 코드 앞에서 자른 뒤 `comment_label`로 뽑는다(「구분 1:운영」의 `1`이 라벨에
    남지 않게). 열거가 없으면 원문.
    """
    pairs = parse_comment_enum(text)
    if not pairs:
        return text, 0
    head = text
    for code in pairs:
        found = re.search(rf"(?<![A-Za-z0-9]){re.escape(code)}\s*[:=]", text)
        if found and found.start() < len(head):
            head = text[:found.start()]
    return comment_label(head), len(pairs)


def _column_meaning(column: Mapping[str, Any]) -> dict[str, Any]:
    """컬럼 의미 — 코드 열거가 든 주석은 첫 마디와 쌍 수만(값 칸 0)."""
    meaning = column.get("meaning")
    source = column.get("meaning_source")
    if not meaning or source in (None, "none"):
        return {}
    text, pairs = _comment_parts(str(meaning))
    out: dict[str, Any] = {}
    if text:
        out["meaning"] = text
    if pairs:
        out["comment_enum"] = pairs
    if out.get("meaning"):
        out["meaning_source"] = str(source)
    return out


def _definition(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {}
    return {key: raw[key] for key in _DEFINITION_FIELDS if raw.get(key) not in (None, "", [], {})}


def _table_entry(
    name: str, info: Mapping[str, Any], definition: Mapping[str, Any], allowed: bool
) -> dict[str, Any]:
    entry: dict[str, Any] = {"allowed": allowed}
    entry.update(_definition(definition))
    if info.get("meaning") and info.get("meaning_source") == "db_comment":
        comment, pairs = _comment_parts(str(info["meaning"]))
        if comment:
            entry["comment"] = comment
        if pairs:
            entry["comment_enum"] = pairs
    if info.get("rows_estimate") is not None:
        entry["rows_estimate"] = info["rows_estimate"]
    if info.get("key"):
        entry["key"] = [str(k) for k in info["key"]]
    relations = [
        {k: rel.get(k) for k in ("kind", "to", "columns") if rel.get(k) is not None}
        for rel in info.get("relations") or []
        if isinstance(rel, Mapping) and rel.get("kind") != "p1"
    ]
    if relations:
        entry["relations"] = sorted(relations, key=lambda r: (str(r.get("to")), str(r)))
    columns = []
    for column in info.get("columns") or []:
        if not isinstance(column, Mapping) or not column.get("name"):
            continue
        item: dict[str, Any] = {"name": str(column["name"])}
        item.update({k: column[k] for k in _COLUMN_FIELDS if column.get(k) is not None})
        item.update(_column_meaning(column))
        columns.append(item)
    entry["columns"] = columns
    return entry


def table_documents(
    catalog: Mapping[str, Any],
    definitions: Mapping[str, Any],
    allowed: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """``{군: {"family", "tables": {테이블: 항목}}}`` — 카탈로그 테이블 + 정의에만 있는 테이블."""
    scope = {bare_name(str(t)).lower() for t in allowed}
    by_bare = {bare_name(str(t)).lower(): definitions[t] for t in definitions}
    tables: Mapping[str, Any] = catalog.get("tables") or {}
    names = sorted({str(t) for t in tables} | {str(t) for t in definitions})
    seen: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    for name in names:
        key = bare_name(name).lower()
        if key in seen:
            continue
        seen.add(key)
        info = tables.get(name) if isinstance(tables.get(name), Mapping) else {}
        family = table_family(name)
        doc = out.setdefault(family, {"family": family, "tables": {}})
        doc["tables"][name] = _table_entry(name, info or {}, by_bare.get(key) or {}, key in scope)
    return dict(sorted(out.items()))


def p1_document(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """P1 근거 — 컬럼 값 형식 비율·코드 판정 · P1 관계(채택 여부·겹침). 플래그 값은 있는지만."""
    columns: dict[str, Any] = {}
    relations: list[dict[str, Any]] = []
    for table in sorted(catalog.get("tables") or {}):
        info = catalog["tables"][table]
        if not isinstance(info, Mapping):
            continue
        for column in info.get("columns") or []:
            profile = column.get("profile") if isinstance(column, Mapping) else None
            if not isinstance(profile, Mapping):
                continue
            item = {k: profile[k] for k in _P1_COLUMN_FIELDS if profile.get(k) is not None}
            formats = profile.get("formats")
            if isinstance(formats, Mapping):
                nonzero = {k: formats[k] for k in sorted(formats) if formats.get(k)}
                if nonzero:
                    item["formats"] = nonzero
            item["has_flag"] = bool(profile.get("flag"))
            columns[f"{table}.{column.get('name')}"] = item
        for rel in info.get("relations") or []:
            if isinstance(rel, Mapping) and rel.get("kind") == "p1":
                relations.append({k: rel.get(k) for k in _P1_RELATION_FIELDS})
    relations.sort(key=lambda r: (str(r.get("from")), str(r.get("to")), str(r.get("columns"))))
    available = bool(columns or relations)
    doc: dict[str, Any] = {"available": available}
    if not available:
        doc["reason"] = "반출 카탈로그에 P1 근거(컬럼 profile · kind p1 관계)가 없다"
    doc["accepted_relations"] = sum(1 for r in relations if r.get("accepted"))
    doc["relations"] = relations
    doc["columns"] = columns
    return doc


def failure_reasons(record: Mapping[str, Any]) -> list[str]:
    """턴 실패 사유(정렬 없음 · 발생 순).

    분류 · 오라클 · 상태 · 결과 상태 · ITAM SQL 없음 · 오류 순이다.
    """
    reasons = [f"taxonomy:{t}" for t in record.get("taxonomy") or []]
    verdict = (record.get("oracle") or {}).get("verdict") if record.get("oracle") else None
    if verdict and verdict != "pass":
        reasons.append(f"oracle:{verdict}")
    status = record.get("status")
    if status != "completed":
        reasons.append(f"status:{status}")
    result_status = (record.get("result") or {}).get("status")
    if result_status not in (None, "ok"):
        reasons.append(f"result:{result_status}")
    analysis = record.get("sql_analysis")
    sql_count = (
        analysis.get("sql_count") if isinstance(analysis, Mapping) else None
    )
    if sql_count is None:
        sql_count = len(record.get("executed_sqls") or [])
    if not sql_count and "taxonomy:no_sql" not in reasons:
        reasons.append("no_itam_sql")
    if record.get("error"):
        reasons.append("error")
    return reasons


def _schema_context(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    out = {str(k): v for k, v in raw.items() if k != "presented_tables"}
    presented = raw.get("presented_tables")
    if isinstance(presented, list):
        out["presented_table_count"] = len(presented)
        out["presented_tables"] = sorted(str(t) for t in presented)
    return dict(sorted(out.items()))


def _turn_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    result = record.get("result") or {}
    entry: dict[str, Any] = {
        "id": record.get("id"),
        "turn": record.get("turn"),
        "repeat": record.get("repeat"),
        "category": record.get("category"),
        "traps": list(record.get("traps") or []),
        "prompt": record.get("prompt"),
        "failure_reasons": failure_reasons(record),
        "status": record.get("status"),
        "db_ids": list(record.get("db_ids") or []),
        "result": {
            k: result.get(k) for k in ("status", "total_rows", "truncated") if k in result
        },
        "retries": record.get("retries"),
    }
    oracle = record.get("oracle")
    if isinstance(oracle, Mapping):
        entry["oracle"] = {k: oracle.get(k) for k in ("verdict", "mode") if k in oracle}
    observe = record.get("observe")
    if isinstance(observe, Mapping) and observe.get("what"):
        entry["observe"] = observe.get("what")
    analysis = record.get("sql_analysis")
    if isinstance(analysis, Mapping):
        entry["sql_analysis"] = dict(sorted(analysis.items()))
    entry["executed_sqls"] = [
        {k: sql.get(k) for k in _SQL_FIELDS if k in sql}
        for sql in record.get("executed_sqls") or []
        if isinstance(sql, Mapping)
    ]
    context = _schema_context(record.get("schema_context"))
    if context is not None:
        entry["schema_context"] = context
    return entry


def turns_document(
    records: Sequence[Mapping[str, Any]], fix_hints: Mapping[str, str]
) -> dict[str, Any]:
    """턴 기록 → 턴 목록(시나리오 ID·턴·반복 순) · 실패 사유 집계 · 분류별 고칠 곳."""
    turns = sorted(
        (_turn_entry(r) for r in records),
        key=lambda t: (str(t["id"]), int(t["turn"] or 0), int(t["repeat"] or 0)),
    )
    reasons = Counter(reason for t in turns for reason in t["failure_reasons"])
    tags = sorted({r.split(":", 1)[1] for r in reasons if r.startswith("taxonomy:")})
    return {
        "turns_total": len(turns),
        "turns_failed": sum(1 for t in turns if t["failure_reasons"]),
        "failure_reasons": dict(sorted(reasons.items())),
        "fix_hints": {tag: fix_hints[tag] for tag in tags if tag in fix_hints},
        "turns": turns,
    }


def scenarios_document(
    document: Mapping[str, Any] | None, oracle_sqls: Mapping[str, str]
) -> dict[str, Any]:
    """시나리오 문서 → 질문·관찰 대상·오라클 ID(정답 SQL 글이 있으면 함께) — ID 순."""
    scenarios = []
    for raw in (document or {}).get("scenarios") or []:
        if not isinstance(raw, Mapping):
            continue
        turns = []
        for index, turn in enumerate(raw.get("turns") or [], start=1):
            if not isinstance(turn, Mapping):
                continue
            send = turn.get("send") or {}
            expect = turn.get("expect") or {}
            item: dict[str, Any] = {"turn": index, "query": send.get("query")}
            observe = expect.get("observe")
            if isinstance(observe, Mapping) and observe.get("what"):
                item["observe"] = observe["what"]
            oracle = expect.get("oracle")
            if isinstance(oracle, Mapping) and oracle.get("id"):
                item["oracle"] = {"id": oracle["id"], "compare": oracle.get("compare")}
                if str(oracle["id"]) in oracle_sqls:
                    item["oracle_sql"] = oracle_sqls[str(oracle["id"])]
            turns.append(item)
        scenarios.append({
            "id": raw.get("id"),
            "title": raw.get("title"),
            "category": raw.get("category"),
            "traps": list(raw.get("traps") or []),
            "gold_tables": list(raw.get("gold_tables") or []),
            "key_columns": list(raw.get("key_columns") or []),
            "turns": turns,
        })
    scenarios.sort(key=lambda s: str(s["id"]))
    return {"scenarios": scenarios}


def previous_cycle_document(
    knowledge: Mapping[str, Any], validation: Mapping[str, Any] | None
) -> dict[str, Any]:
    """철회 항목(파일·ID·사유) · 직전 검증 실패 항목(파일·ID·코드·사유) — 파일·ID 순."""
    withdrawn = []
    for file_name in sorted(knowledge):
        doc = knowledge[file_name]
        for item in (doc.get("items") or []) if isinstance(doc, Mapping) else []:
            if isinstance(item, Mapping) and item.get("status") == "withdrawn":
                withdrawn.append({
                    "file": file_name, "id": item.get("id"), "reason": item.get("reason"),
                    "evidence": item.get("evidence"),
                })
    failed = []
    for result in (validation or {}).get("results") or []:
        if isinstance(result, Mapping) and not result.get("ok"):
            failed.append({
                "file": result.get("file"), "id": result.get("id"),
                "issues": [
                    {"code": i.get("code"), "message": i.get("message")}
                    for i in result.get("issues") or [] if isinstance(i, Mapping)
                ],
            })
    withdrawn.sort(key=lambda w: (str(w["file"]), str(w["id"])))
    failed.sort(key=lambda f: (str(f["file"]), str(f["id"])))
    return {"withdrawn": withdrawn, "validation_failed": failed}


def build_evidence(
    *,
    run_id: str,
    catalog: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    definitions: Mapping[str, Any],
    allowed: Sequence[str],
    groups: Mapping[str, Any] | None = None,
    scenarios: Mapping[str, Any] | None = None,
    oracle_sqls: Mapping[str, str] | None = None,
    knowledge: Mapping[str, Any] | None = None,
    validation: Mapping[str, Any] | None = None,
    fix_hints: Mapping[str, str] | None = None,
    inputs: Mapping[str, bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """근거 묶음 전체 — ``{파일 이름: 문서}`` (파일 이름 순 · 값 칸 0 · 결정적).

    Args:
        run_id: 반출 run ID
        catalog: 반출 카탈로그
        records: 턴 기록(`trace.jsonl` 행)
        definitions: 현행 테이블 정의(`table_definitions`)
        allowed: 조회 대상(`allowed_tables`)
        groups: 업무 영역 설명(시드 정의 `groups`)
        scenarios: 시나리오 문서
        oracle_sqls: ``{오라클 ID: 정답 SQL 글}``
        knowledge: ``{원천 파일 이름: 문서}`` — 철회 항목 수집용
        validation: 직전 검증 결과(`validate_dir` 출력)
        fix_hints: 실패 분류 → 고칠 곳
        inputs: 입력 파일 유무(색인에 그대로 싣는다)
    """
    families = table_documents(catalog, definitions, allowed)
    files: dict[str, dict[str, Any]] = {
        TABLES_FILE_FORMAT.format(family=family): doc for family, doc in families.items()
    }
    files[P1_FILE] = p1_document(catalog)
    files[TURNS_FILE] = turns_document(records, fix_hints or {})
    files[SCENARIOS_FILE] = scenarios_document(scenarios, oracle_sqls or {})
    files[PREVIOUS_FILE] = previous_cycle_document(knowledge or {}, validation)
    kinds = Counter(
        str(entry.get("kind"))
        for doc in families.values() for entry in doc["tables"].values() if entry.get("kind")
    )
    index = {
        "run_id": run_id,
        "note": "값 칸 0(반출 근거만) · 결정적(시각 없음) — 근거에 없는 사실은 쓰지 않는다",
        "inputs": dict(sorted((inputs or {}).items())),
        "groups": dict(groups or {}),
        "kinds": dict(sorted(kinds.items())),
        "families": {
            family: {
                "tables": len(doc["tables"]),
                "allowed": sum(1 for e in doc["tables"].values() if e.get("allowed")),
                "file": TABLES_FILE_FORMAT.format(family=family),
            }
            for family, doc in families.items()
        },
        "files": sorted([*files, INDEX_FILE]),
    }
    files[INDEX_FILE] = index
    return dict(sorted(files.items()))
