"""스키마 자산 자동 생성 — 프로파일링 결과 조립 · LLM 입력 요약(결정적 순수 함수).

`asset_generation_service`(D-294 · plans/133 W4)의 P1 프로파일링 잡이 쓰는 범위 결정 · 관계·컬럼
후보 · 자산 초안·근거 조립과 P2 LLM 입력 요약을 둔다. 서비스 모듈에서 옮겨 왔다(plans/166 I3) —
옛 경로에서도 재노출한다. 테스트가 서비스 모듈에서 바꾸는 상한(`NAME_MATCH_*` · 조회 예산 ·
`MAX_DEFINED_CODE_PAIRS`)을 읽는 함수는 서비스 모듈에 남겼다(D-337 ②).

계층: infrastructure(`src/schema_cache`). 스키마 리터럴 금지(`overfit_check` 스캔 대상).
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.domain import schema_inference as inference
from src.domain.schema_snapshot import bare_name
from src.domain.table_definitions import PROFILE_KEY as TABLE_DEFINITIONS_KEY

if TYPE_CHECKING:
    from src.schema_cache import schema_probe as probe

#: 공통 컬럼 제외를 적용할 최소 테이블 수(그보다 적으면 함께 있는 컬럼이 전부 공통이 된다 —
#: 2테이블 로컬 샌드박스 실측 2026-10-01)
MIN_TABLES_FOR_COMMON = 5
#: 공통코드 라벨 채택 하한(코드 컬럼 값 중 코드 테이블에 있는 비율)
CODE_TABLE_COVERAGE = 0.9
SUMMARY_MAX_CHARS = 12000

_STRING_TYPE_RE = re.compile(
    r"^(char|character|varchar|character varying|nchar|nvarchar|varchar2|text|graphic|"
    r"vargraphic|tinytext|mediumtext|longtext|clob)\b",
    re.IGNORECASE,
)
_INTEGER_TYPE_RE = re.compile(r"^(tinyint|smallint|int|integer|mediumint|bigint)\b", re.IGNORECASE)
_DATE_HINT_RE = re.compile(r"(?i)(ymd|ymdhms|dt|date|dttm|day)$|일자|일시|년월일|날짜")
_HOST_IP_HINT_RE = re.compile(r"(?i)host|hst|(^|_|[a-z])ip|호스트|아이피|서버\s*명")


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


def _relation_item(
    child: str,
    parent: str | None,
    child_columns: list[str],
    parent_columns: list[str],
    origin: str,
    *,
    error: str | None = None,
    unique_parent: bool | None = None,
) -> dict[str, Any]:
    """관계 근거 항목 1건(`evidence.relationships[]`).

    ``unique_parent``는 이름 일치 후보만 채운다(그 밖은 None).
    """
    return {
        "child": child, "parent": parent, "child_columns": child_columns,
        "parent_columns": parent_columns, "origin": origin, "overlap": None, "sampled": None,
        "accepted": False, "error": error, "unique_parent": unique_parent,
    }


def _relation_candidates(
    shapes: Mapping[str, inference.TableShape],
) -> list[inference.InferredRelation]:
    """관계 후보 — 선언 FK · 기본키 일치 추론 · 같은 기본키 군(군의 첫 테이블을 부모로)."""
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
    return candidates


def _name_match_candidates(
    snap_tables: Mapping[str, Any],
    scope: Sequence[str],
    shapes: Mapping[str, inference.TableShape],
) -> list[inference.NameMatchColumn]:
    """범위 테이블의 이름 일치 관계 후보 컬럼(선언 기본키 있는 테이블은 참여하지 않는다)."""
    columns = {
        table: [(column, str((attrs or {}).get("type") or ""))
                for column, attrs in (snap_tables[table].get("columns") or {}).items()]
        for table in scope
    }
    keyed = [t for t in scope if t in shapes and shapes[t].primary_key]
    return inference.name_match_columns(columns, keyed)


@dataclass
class ColumnPlan:
    """값 표본을 읽을 컬럼 — 근거 틀(``meta``)과 코드 후보·형식 후보 키."""

    meta: dict[str, dict[str, Any]]
    code_keys: list[str]
    format_keys: list[str]


def _column_candidates(
    snap_tables: Mapping[str, Any], scope: Sequence[str], comments: Mapping[str, str]
) -> ColumnPlan:
    """범위 컬럼 중 코드 후보(이름·주석 단서 + 타입)·형식 후보(문자열 + 날짜·호스트·IP 단서)."""
    plan = ColumnPlan(meta={}, code_keys=[], format_keys=[])
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
                plan.meta[key] = {
                    "type": dtype, "comment": comment,
                    "candidate": "code" if is_code else "format", "code": is_code,
                    "values": [], "distinct": None, "truncated": False, "profile": None,
                    "error": None,
                }
                (plan.code_keys if is_code else plan.format_keys).append(key)
    return plan


def column_evidence_row(
    key: str, item: Mapping[str, Any], entity_key: str | None
) -> dict[str, Any]:
    """컬럼별 근거 1행(`evidence.columns[]` · plans/140 W1-4) — 값은 담지 않는다(비율·수·판정만).

    값 표본을 못 읽은 컬럼(``profile`` 없음)은 비율·``total``이 None이다. 비율은 소수 셋째 자리
    **내림**이다 — 반올림하면 경계(예: 0.94987)가 문턱(0.95) 위로 올라가 이 근거로 판정하는 쪽이
    원 비율 판정보다 관대해진다(D-311 감사 L-4).
    """

    def _floor3(x: float) -> float:
        return math.floor(x * 1000) / 1000

    p: inference.ValueProfile | None = item.get("profile")
    return {
        # 최종 판정 — 값 표본으로 확인된 코드 컬럼만(DB에 못 닿으면 후보여도 False)
        "key": key, "candidate": item.get("candidate"),
        "code": bool(item.get("code") and item.get("values")),
        "distinct": item.get("distinct"), "truncated": bool(item.get("truncated")),
        "total": p.total if p else None,
        "date8": _floor3(p.date8) if p else None,
        "datetime14": _floor3(p.datetime14) if p else None,
        "ipv4": _floor3(p.ipv4) if p else None,
        "hostname": _floor3(p.hostname) if p else None,
        "multi_value": _floor3(p.multi_value) if p else None,
        "mixed_case": bool(p.mixed_case) if p else False,
        "flag": list(p.flag) if p else [],
        "entity_key": entity_key, "error": item.get("error"),
    }


def assemble(
    source: str,
    snapshot: Mapping[str, Any],
    scope: Sequence[str],
    comments: Mapping[str, str],
    catalog: probe.CatalogInfo,
    relationships: list[dict[str, Any]],
    columns: Mapping[str, Mapping[str, Any]],
    code_tables: Sequence[Mapping[str, Any]],
    definitions: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """프로파일링 결과를 자산(초안 값)과 근거로 조립한다(결정적).

    ``definitions``는 승인 프로필의 테이블 정의다 — 식별 키 후보에서 수집 이력 테이블을 뺀다
    (plans/151 W7 · 없으면 종전).
    """
    code_values: dict[str, list[str]] = {}
    code_labels: dict[str, dict[str, str]] = {}
    column_values: dict[str, dict[str, Any]] = {}
    code_evidence: list[dict[str, Any]] = []
    format_evidence: list[dict[str, Any]] = []
    column_evidence: list[dict[str, Any]] = []
    rules, entity = inference.rules_and_entity_candidates(columns)
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
            column_evidence.append(column_evidence_row(key, item, None))
            if item.get("error"):
                format_evidence.append({"key": key, "error": item["error"]})
            continue
        column_rules = inference.query_rules_for_column(table, column, profile)
        kind = inference.entity_key_kind(column, item.get("comment"), profile)
        column_evidence.append(column_evidence_row(key, item, kind))
        if column_rules or kind or item.get("error"):
            format_evidence.append({
                "key": key, "total": profile.total, "date8": round(profile.date8, 3),
                "datetime14": round(profile.datetime14, 3), "ipv4": round(profile.ipv4, 3),
                "hostname": round(profile.hostname, 3),
                "multi_value": round(profile.multi_value, 3),
                "flag": list(profile.flag), "entity_key": kind, "rules": len(column_rules),
                "error": item.get("error"),
            })

    column_synonyms = inference.comment_synonyms(comments)

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
        "entity_keys": inference.entity_keys_asset(entity, catalog.row_estimates, definitions),
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
        "columns": column_evidence,
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


# 옛 이름 별칭(D-337 ③ · 1사이클) — 모듈 밖에서 옛 이름으로 쓴다
_ColumnPlan = ColumnPlan
_column_evidence = column_evidence_row
_assemble = assemble
