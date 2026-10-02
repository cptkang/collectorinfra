"""자산관리(ITAM) MariaDB 스키마 수집 → ERD·테이블 정의서 생성 (plans/95 G-4).

운영 ITAM DB의 `information_schema`만 읽어 테이블·컬럼·인덱스·FK 메타데이터를 스냅숏(JSON)으로 남기고,
그 스냅숏에서 Mermaid ERD와 테이블 정의서(Markdown)를 만든다. **데이터 행은 한 건도 읽지 않는다**
(행 수는 `information_schema.TABLES.TABLE_ROWS` 추정값).

벤더 스키마는 FK 제약을 선언하지 않는 경우가 많다. 선언 FK가 없으면 관계를 **기본키 일치로 추론**한다 —
부모 테이블의 기본키 컬럼이 전부 자식 테이블에 같은 이름으로 있으면 자식 → 부모 관계로 본다. 추론 관계는
ERD에서 점선(`..`)으로, 선언 FK는 실선(`--`)으로 그린다. 추론은 후보일 뿐이다 — 업무 담당자 검토 전에는
구조 정본(`config/db_profiles/itam.yaml`)의 조인 규칙으로 옮기지 않는다.

실행 위치는 ITAM DB에 네트워크가 닿는 곳(운영 = MCP 서버)이다. 이 파일은 표준 라이브러리 + `pymysql`만
쓰므로 단독 복사로 동작한다(`pymysql`은 MCP 서버의 `aiomysql` 의존성으로 이미 설치돼 있다).

    # 수집 + 렌더 (접속 정보는 mcp_server/.env 의 ITAM_CONNECTION — OS 환경변수가 있으면 그쪽 우선)
    python scripts/itam_erd.py --out-dir /tmp/itam_erd
    python scripts/itam_erd.py --env-file mcp_server/.env --schema INST1 --out-dir /tmp/itam_erd

    # 스냅숏만으로 다시 렌더 (DB 접속 없음 — 개발 PC에서 가능)
    python scripts/itam_erd.py --from-json /tmp/itam_erd/itam_schema.json --out-dir ./itam_erd

산출물: `itam_schema.json`(스냅숏) · `itam_erd.md`(개요·군별 ERD·동일 키 군) · `itam_dictionary.md`(정의서).
Mermaid는 VS Code 「Markdown Preview Mermaid Support」 확장 또는 GitHub 미리보기로 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

TOOL_VERSION = 1
ENV_KEY = "ITAM_CONNECTION"
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / "mcp_server" / ".env"

# 기본키가 이 비율보다 많은 테이블에 나오는 컬럼으로만 이뤄졌으면 관계 추론의 부모로 쓰지 않는다
# (예: 그룹사 코드 단독 PK — 거의 모든 테이블에 있어 전 테이블이 자식으로 잡힌다).
DEFAULT_COMMON_RATIO = 0.5


# ============================================================================
# 수집 (DB 접속 — 이 절만 pymysql을 쓴다)
# ============================================================================


def read_env_value(env_file: Path, key: str) -> str:
    """`.env`에서 키 하나를 읽는다. OS 환경변수가 있으면 그 값이 우선이다(mcp_server와 같은 규칙)."""
    if os.environ.get(key):
        return os.environ[key]
    if not env_file.exists():
        return ""
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def parse_dsn(dsn: str) -> dict[str, Any]:
    """`mariadb://user:password@host:port/database` → 접속 인자 (mcp_server/db.py와 같은 형식)."""
    parts = urlparse(dsn)
    if parts.scheme not in ("mariadb", "mysql"):
        raise ValueError("연결 문자열은 mariadb:// 또는 mysql:// 형식이어야 한다")
    if parts.query:
        raise ValueError("연결 문자열의 쿼리 옵션(?…)은 지원하지 않는다")
    database = parts.path.lstrip("/")
    if not parts.hostname or not database:
        raise ValueError("연결 문자열에 host와 database가 필요하다")
    return {
        "host": parts.hostname,
        "port": parts.port or 3306,
        "user": unquote(parts.username or ""),
        "password": unquote(parts.password or ""),
        "database": unquote(database),
    }


def mask_dsn(dsn: str) -> str:
    """로그용 — 비밀번호를 가린다."""
    return re.sub(r"(://[^:/@]+:)[^@]*@", r"\1********@", dsn)


_Q_SERVER = (
    "SELECT @@version AS version, @@sql_mode AS sql_mode, "
    "@@lower_case_table_names AS lower_case_table_names, "
    "@@character_set_server AS character_set_server, @@collation_server AS collation_server"
)
_Q_TABLES = (
    "SELECT TABLE_NAME AS name, TABLE_TYPE AS type, ENGINE AS engine, TABLE_ROWS AS rows_estimate, "
    "TABLE_COMMENT AS comment, TABLE_COLLATION AS collation "
    "FROM information_schema.TABLES WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME"
)
_Q_COLUMNS = (
    "SELECT TABLE_NAME AS table_name, COLUMN_NAME AS name, ORDINAL_POSITION AS ordinal, "
    "DATA_TYPE AS data_type, COLUMN_TYPE AS column_type, IS_NULLABLE AS nullable, "
    "COLUMN_KEY AS column_key, COLUMN_DEFAULT AS default_value, COLUMN_COMMENT AS comment "
    "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME, ORDINAL_POSITION"
)
_Q_INDEXES = (
    "SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, NON_UNIQUE AS non_unique, "
    "SEQ_IN_INDEX AS seq, COLUMN_NAME AS column_name "
    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = %s "
    "ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX"
)
_Q_FKS = (
    "SELECT CONSTRAINT_NAME AS constraint_name, TABLE_NAME AS table_name, COLUMN_NAME AS column_name, "
    "ORDINAL_POSITION AS seq, REFERENCED_TABLE_NAME AS ref_table, REFERENCED_COLUMN_NAME AS ref_column "
    "FROM information_schema.KEY_COLUMN_USAGE "
    "WHERE TABLE_SCHEMA = %s AND REFERENCED_TABLE_NAME IS NOT NULL "
    "ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION"
)


def collect(dsn: str, schema: str | None) -> dict[str, Any]:
    """information_schema를 읽어 원시 행을 모은다. 읽기 전용 세션으로 연다."""
    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError as e:  # pragma: no cover — 실행 환경 안내
        raise SystemExit(
            "pymysql이 없습니다. MCP 서버 venv에서 실행하거나 `pip install pymysql`로 설치하세요."
        ) from e

    kwargs = parse_dsn(dsn)
    schema = schema or kwargs["database"]
    conn = pymysql.connect(
        **kwargs,
        charset="utf8mb4",
        autocommit=True,
        connect_timeout=10,
        read_timeout=120,
        cursorclass=DictCursor,
    )
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
            except Exception as e:  # noqa: BLE001 — 아래는 전부 SELECT라 수집은 계속한다
                print(f"[경고] 읽기 전용 세션 설정 실패(계속 진행): {e}", file=sys.stderr)
            cur.execute(_Q_SERVER)
            server = cur.fetchone() or {}
            raw: dict[str, Any] = {"server": server}
            for key, sql in (
                ("tables", _Q_TABLES),
                ("columns", _Q_COLUMNS),
                ("indexes", _Q_INDEXES),
                ("fks", _Q_FKS),
            ):
                cur.execute(sql, (schema,))
                raw[key] = list(cur.fetchall())
    finally:
        conn.close()
    return build_snapshot(raw, schema)


def _norm(value: Any) -> Any:
    """JSON 직렬화 가능한 값으로 — bytes·Decimal 등."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def build_snapshot(raw: dict[str, Any], schema: str) -> dict[str, Any]:
    """원시 행 → 테이블 단위 스냅숏. (순수 함수 — 테스트 대상)"""
    tables: dict[str, dict[str, Any]] = {}
    for row in raw.get("tables", []):
        row = {k.lower(): _norm(v) for k, v in row.items()}
        tables[row["name"]] = {
            "name": row["name"],
            "type": row.get("type") or "",
            "engine": row.get("engine") or "",
            "rows_estimate": row.get("rows_estimate"),
            "comment": row.get("comment") or "",
            "collation": row.get("collation") or "",
            "columns": [],
            "primary_key": [],
            "indexes": [],
            "foreign_keys": [],
        }
    for row in raw.get("columns", []):
        row = {k.lower(): _norm(v) for k, v in row.items()}
        table = tables.get(row["table_name"])
        if table is None:
            continue
        table["columns"].append({
            "name": row["name"],
            "ordinal": row.get("ordinal"),
            "data_type": row.get("data_type") or "",
            "column_type": row.get("column_type") or "",
            "nullable": (row.get("nullable") or "").upper() == "YES",
            "key": row.get("column_key") or "",
            "default": row.get("default_value"),
            "comment": row.get("comment") or "",
        })
    index_cols: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw.get("indexes", []):
        row = {k.lower(): _norm(v) for k, v in row.items()}
        if row["table_name"] not in tables:
            continue
        key = (row["table_name"], row["index_name"])
        entry = index_cols.setdefault(key, {
            "name": row["index_name"],
            "unique": str(row.get("non_unique")) in ("0", "False"),
            "columns": [],
        })
        entry["columns"].append(row["column_name"])
    for (table_name, index_name), entry in index_cols.items():
        if index_name == "PRIMARY":
            tables[table_name]["primary_key"] = list(entry["columns"])
        else:
            tables[table_name]["indexes"].append(entry)
    fks: dict[tuple[str, str], dict[str, Any]] = {}
    for row in raw.get("fks", []):
        row = {k.lower(): _norm(v) for k, v in row.items()}
        if row["table_name"] not in tables:
            continue
        key = (row["table_name"], row["constraint_name"])
        entry = fks.setdefault(key, {
            "name": row["constraint_name"],
            "columns": [],
            "ref_table": row["ref_table"],
            "ref_columns": [],
        })
        entry["columns"].append(row["column_name"])
        entry["ref_columns"].append(row["ref_column"])
    for (table_name, _), entry in fks.items():
        tables[table_name]["foreign_keys"].append(entry)
    # PRIMARY 인덱스가 STATISTICS에 없을 때(권한 제한 등) COLUMN_KEY='PRI'로 보완한다
    for table in tables.values():
        if not table["primary_key"]:
            table["primary_key"] = [c["name"] for c in table["columns"] if c["key"] == "PRI"]

    server = {k.lower(): _norm(v) for k, v in (raw.get("server") or {}).items()}
    return {
        "meta": {
            "tool_version": TOOL_VERSION,
            "schema": schema,
            "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "server": server,
            "table_count": len(tables),
            "column_count": sum(len(t["columns"]) for t in tables.values()),
            "note": "information_schema 메타데이터만 수집 — 데이터 행 0건",
        },
        "tables": [tables[name] for name in sorted(tables)],
    }


# ============================================================================
# 분석 (순수 함수)
# ============================================================================


def family_of(table_name: str) -> str:
    """테이블 군 — 끝자리 숫자를 뗀 접두(예: TCDMSIF80 → TCDMSIF)."""
    match = re.match(r"^(.*?[A-Za-z_])\d+$", table_name)
    return match.group(1) if match else table_name


def family_label(family: str) -> str:
    """군 표시명 — 공통 접두 `TCDMS` 뒤 두 글자(IF·AM …). 규약 밖 이름은 그대로."""
    match = re.match(r"^TCDMS([A-Z]{2})$", family)
    return match.group(1) if match else family


@dataclass(frozen=True)
class Relation:
    parent: str
    child: str
    columns: tuple[str, ...]
    kind: str  # declared | inferred


def common_columns(snapshot: dict[str, Any], ratio: float) -> set[str]:
    """테이블 비율 `ratio` 초과로 등장하는 컬럼(소문자)."""
    tables = snapshot["tables"]
    if not tables:
        return set()
    df = Counter()
    for table in tables:
        df.update({c["name"].lower() for c in table["columns"]})
    limit = ratio * len(tables)
    return {name for name, count in df.items() if count > limit}


def infer_relations(
    snapshot: dict[str, Any], common_ratio: float = DEFAULT_COMMON_RATIO
) -> tuple[list[Relation], list[list[str]]]:
    """선언 FK + 기본키 일치 추론 관계, 그리고 동일 기본키 군을 돌려준다.

    - 부모 P의 기본키 컬럼이 전부 자식 C에 있으면 C → P (이름 비교는 대소문자 무시).
    - P의 기본키가 공통 컬럼으로만 이뤄졌으면 부모 후보에서 뺀다.
    - C와 P의 기본키 집합이 같으면 관계선 대신 「동일 키 군」으로 묶는다(n개면 선이 n(n-1)/2개가 된다).
    - C의 부모 후보 중 기본키가 다른 후보의 진부분집합이면 뺀다(더 구체적인 부모를 거쳐 이어진다).
    - 기본키가 같은 부모 후보가 여럿이면 이름순 첫째만 잇는다(나머지는 동일 키 군 표로 보인다).
    - 선언 FK가 있는 (자식, 부모) 쌍은 추론하지 않는다.
    """
    tables = {t["name"]: t for t in snapshot["tables"]}
    common = common_columns(snapshot, common_ratio)
    relations: list[Relation] = []
    declared_pairs: set[tuple[str, str]] = set()
    for table in snapshot["tables"]:
        for fk in table["foreign_keys"]:
            relations.append(Relation(fk["ref_table"], table["name"], tuple(fk["columns"]), "declared"))
            declared_pairs.add((table["name"], fk["ref_table"]))

    pk_sets = {
        name: frozenset(c.lower() for c in t["primary_key"])
        for name, t in tables.items()
        if t["primary_key"]
    }
    parents = {name: pk for name, pk in pk_sets.items() if pk - common}
    col_sets = {name: {c["name"].lower() for c in t["columns"]} for name, t in tables.items()}

    same_key: dict[frozenset[str], set[str]] = defaultdict(set)
    for child, cols in col_sets.items():
        candidates: list[str] = []
        for parent, pk in parents.items():
            if parent == child or not pk <= cols:
                continue
            if pk_sets.get(child) == pk:
                same_key[pk].update({parent, child})
                continue
            candidates.append(parent)
        representative: dict[frozenset[str], str] = {}
        for parent in sorted(candidates):
            representative.setdefault(parents[parent], parent)
        for parent in candidates:
            pk = parents[parent]
            if representative[pk] != parent:
                continue  # 같은 기본키 후보가 여럿이면 대표(이름순 첫째)만 — 나머지는 「동일 기본키 군」 표
            if any(pk < parents[other] for other in candidates if other != parent):
                continue
            if (child, parent) in declared_pairs:
                continue
            ordered = tuple(c for c in tables[parent]["primary_key"])
            relations.append(Relation(parent, child, ordered, "inferred"))

    relations.sort(key=lambda r: (r.parent, r.child, r.kind))
    groups = sorted(sorted(g) for g in same_key.values())
    return relations, groups


# ============================================================================
# 렌더 (순수 함수)
# ============================================================================


def _ident(name: str) -> str:
    """Mermaid 식별자 — 영숫자·밑줄만, 숫자로 시작하면 접두."""
    safe = re.sub(r"[^A-Za-z0-9_]", "_", name)
    return safe if re.match(r"^[A-Za-z_]", safe) else f"c_{safe}"


def _mermaid_comment(text: str, limit: int = 40) -> str:
    text = re.sub(r"\s+", " ", text or "").replace('"', "'").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _md_cell(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).replace("|", "\\|").strip()


def render_entity(table: dict[str, Any], fk_cols: set[str], mode: str) -> list[str]:
    pk = {c.lower() for c in table["primary_key"]}
    lines = [f"    {_ident(table['name'])} {{"]
    for col in table["columns"]:
        lower = col["name"].lower()
        if mode == "keys" and lower not in pk and lower not in fk_cols:
            continue
        marks = [m for m, on in (("PK", lower in pk), ("FK", lower in fk_cols)) if on]
        parts = [_ident(col["data_type"] or "unknown"), _ident(col["name"])]
        if marks:
            parts.append(", ".join(marks))
        comment = col["comment"]
        if _ident(col["name"]) != col["name"]:
            comment = f"{col['name']} {comment}".strip()
        if comment:
            parts.append(f'"{_mermaid_comment(comment)}"')
        lines.append("        " + " ".join(parts))
    lines.append("    }")
    return lines


def _relation_line(rel: Relation) -> str:
    link = "||--o{" if rel.kind == "declared" else "||..o{"
    label = _mermaid_comment(",".join(rel.columns), 60)
    return f'    {_ident(rel.parent)} {link} {_ident(rel.child)} : "{label}"'


def render_erd(
    snapshot: dict[str, Any], relations: list[Relation], same_key: list[list[str]], mode: str = "keys"
) -> str:
    tables = {t["name"]: t for t in snapshot["tables"]}
    meta = snapshot["meta"]
    fams: dict[str, list[str]] = defaultdict(list)
    for name in tables:
        fams[family_of(name)].append(name)

    fk_cols: dict[str, set[str]] = defaultdict(set)
    for rel in relations:
        fk_cols[rel.child].update(c.lower() for c in rel.columns)

    out: list[str] = [
        f"# ITAM ERD — `{meta['schema']}`",
        "",
        f"- 수집: {meta['collected_at']} · 테이블 {meta['table_count']}개 · 컬럼 {meta['column_count']}개 "
        "· `information_schema`만 읽음(데이터 행 0건)",
        f"- 관계: 선언 FK {sum(r.kind == 'declared' for r in relations)}건(실선) · "
        f"기본키 일치 추론 {sum(r.kind == 'inferred' for r in relations)}건(점선) · "
        f"동일 기본키 군 {len(same_key)}개",
        "- **추론 관계는 후보다.** 업무 담당자 검토 전에는 조인 규칙으로 쓰지 않는다.",
        "",
    ]
    server = meta.get("server") or {}
    if server:
        out += ["## 서버 변수 (plans/95 G-13)", "", "| 변수 | 값 |", "|---|---|"]
        out += [f"| `{k}` | `{_md_cell(v)}` |" for k, v in server.items()]
        out.append("")

    out += ["## 테이블 군", "", "| 군 | 접두 | 테이블 수 | 군 내 관계 | 군 밖으로 |", "|---|---|---:|---:|---:|"]
    fam_edges: Counter[tuple[str, str]] = Counter()
    for rel in relations:
        if rel.parent in tables and rel.child in tables:
            fam_edges[(family_of(rel.child), family_of(rel.parent))] += 1
    for fam in sorted(fams):
        inner = fam_edges.get((fam, fam), 0)
        outer = sum(n for (c, p), n in fam_edges.items() if c == fam and p != fam)
        out.append(f"| {family_label(fam)} | `{fam}` | {len(fams[fam])} | {inner} | {outer} |")
    out.append("")

    cross = {k: n for k, n in fam_edges.items() if k[0] != k[1]}
    if cross:
        out += ["## 군 간 관계 개요", "", "화살표는 자식 군 → 부모 군, 숫자는 관계 수.", "", "```mermaid", "flowchart LR"]
        for fam in sorted(fams):
            out.append(f"    {_ident(fam)}[\"{family_label(fam)} ({len(fams[fam])})\"]")
        for (child, parent), n in sorted(cross.items()):
            out.append(f"    {_ident(child)} -->|{n}| {_ident(parent)}")
        out += ["```", ""]

    for fam in sorted(fams):
        members = set(fams[fam])
        rels = [r for r in relations if r.child in members or r.parent in members]
        shown = sorted(members | {r.parent for r in rels} | {r.child for r in rels})
        out += [f"## {family_label(fam)} 군 (`{fam}`·{len(members)}개)", ""]
        external = [n for n in shown if n not in members]
        if external:
            out += [f"군 밖 테이블(관계 상대만 표시): {', '.join(f'`{n}`' for n in external)}", ""]
        out += ["```mermaid", "erDiagram"]
        for name in shown:
            if name in tables:
                out += render_entity(tables[name], fk_cols.get(name, set()), mode)
        for rel in rels:
            out.append(_relation_line(rel))
        out += ["```", ""]

    if same_key:
        out += [
            "## 동일 기본키 군",
            "",
            "기본키 구성이 같은 테이블 묶음이다(1:1 또는 이력·확장 관계 후보). 관계선은 그리지 않았다.",
            "",
            "| 기본키 | 테이블 |",
            "|---|---|",
        ]
        for group in same_key:
            pk = ", ".join(tables[group[0]]["primary_key"])
            out.append(f"| `{pk}` | {', '.join(f'`{n}`' for n in group)} |")
        out.append("")

    no_pk = [n for n, t in sorted(tables.items()) if not t["primary_key"]]
    if no_pk:
        out += ["## 기본키 없는 테이블", "", "관계 추론의 부모가 될 수 없다.", "", ", ".join(f"`{n}`" for n in no_pk), ""]
    return "\n".join(out)


def render_dictionary(snapshot: dict[str, Any], relations: list[Relation]) -> str:
    meta = snapshot["meta"]
    tables = snapshot["tables"]
    parents_of: dict[str, list[Relation]] = defaultdict(list)
    children_of: dict[str, list[Relation]] = defaultdict(list)
    for rel in relations:
        parents_of[rel.child].append(rel)
        children_of[rel.parent].append(rel)

    fams: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for table in tables:
        fams[family_of(table["name"])].append(table)

    out = [f"# ITAM 테이블 정의서 — `{meta['schema']}`", "", f"수집 {meta['collected_at']} · 테이블 {len(tables)}개", ""]
    out += ["| 테이블 | 설명 | 컬럼 수 | 행 수(추정) | 기본키 |", "|---|---|---:|---:|---|"]
    for table in tables:
        out.append(
            f"| [`{table['name']}`](#{table['name'].lower()}) | {_md_cell(table['comment'])} | "
            f"{len(table['columns'])} | {_md_cell(table['rows_estimate'])} | "
            f"{_md_cell(', '.join(table['primary_key']))} |"
        )
    out.append("")
    for fam in sorted(fams):
        out += [f"## {family_label(fam)} 군 (`{fam}`)", ""]
        for table in fams[fam]:
            out += [f"### {table['name']}", ""]
            if table["comment"]:
                out += [_md_cell(table["comment"]), ""]
            out += [
                f"유형 {table['type']} · 엔진 {table['engine']} · 행 수(추정) {_md_cell(table['rows_estimate'])} "
                f"· collation {table['collation']}",
                "",
                "| # | 컬럼 | 타입 | NULL | 키 | 기본값 | 설명 |",
                "|---:|---|---|:-:|:-:|---|---|",
            ]
            for col in table["columns"]:
                out.append(
                    f"| {col['ordinal']} | `{col['name']}` | {_md_cell(col['column_type'])} | "
                    f"{'Y' if col['nullable'] else 'N'} | {col['key']} | {_md_cell(col['default'])} | "
                    f"{_md_cell(col['comment'])} |"
                )
            out.append("")
            if table["indexes"]:
                out.append("인덱스: " + " · ".join(
                    f"`{i['name']}`({', '.join(i['columns'])}){' UNIQUE' if i['unique'] else ''}"
                    for i in table["indexes"]
                ))
                out.append("")
            if parents_of[table["name"]]:
                out.append("참조(부모): " + " · ".join(
                    f"`{r.parent}` ({', '.join(r.columns)}){'' if r.kind == 'declared' else ' 추론'}"
                    for r in parents_of[table["name"]]
                ))
                out.append("")
            if children_of[table["name"]]:
                out.append("피참조(자식): " + " · ".join(
                    f"`{r.child}`{'' if r.kind == 'declared' else ' 추론'}" for r in children_of[table["name"]]
                ))
                out.append("")
    return "\n".join(out)


def write_outputs(
    snapshot: dict[str, Any], out_dir: Path, mode: str, common_ratio: float
) -> tuple[list[Relation], list[list[str]]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    relations, same_key = infer_relations(snapshot, common_ratio)
    (out_dir / "itam_schema.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "itam_erd.md").write_text(render_erd(snapshot, relations, same_key, mode), encoding="utf-8")
    (out_dir / "itam_dictionary.md").write_text(render_dictionary(snapshot, relations), encoding="utf-8")
    return relations, same_key


# ============================================================================
# CLI
# ============================================================================


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ITAM 스키마 수집 → ERD·테이블 정의서")
    parser.add_argument("--out-dir", required=True, type=Path, help="산출물 디렉터리")
    parser.add_argument("--from-json", type=Path, help="수집 대신 기존 스냅숏으로 렌더(DB 접속 없음)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE, help=f"{ENV_KEY}를 읽을 .env")
    parser.add_argument("--schema", help="대상 database(생략 시 연결 문자열의 database)")
    parser.add_argument(
        "--diagram-columns", choices=["keys", "all"], default="keys",
        help="ERD에 그릴 컬럼 — keys: 기본키·관계 컬럼만(기본) · all: 전 컬럼",
    )
    parser.add_argument(
        "--common-ratio", type=float, default=DEFAULT_COMMON_RATIO,
        help="이 비율 초과 테이블에 나오는 컬럼만으로 된 기본키는 추론 부모에서 제외(기본 0.5)",
    )
    args = parser.parse_args(argv)

    if args.from_json:
        snapshot = json.loads(args.from_json.read_text(encoding="utf-8"))
    else:
        dsn = read_env_value(args.env_file, ENV_KEY)
        if not dsn:
            print(f"{ENV_KEY}가 없습니다 — OS 환경변수 또는 {args.env_file}에 설정하세요.", file=sys.stderr)
            return 2
        print(f"수집: {mask_dsn(dsn)} schema={args.schema or '(연결 문자열)'}")
        snapshot = collect(dsn, args.schema)

    relations, same_key = write_outputs(snapshot, args.out_dir, args.diagram_columns, args.common_ratio)
    meta = snapshot["meta"]
    fams = Counter(family_of(t["name"]) for t in snapshot["tables"])
    print(f"테이블 {meta['table_count']}개 · 컬럼 {meta['column_count']}개")
    print("군: " + ", ".join(f"{family_label(f)}={n}" for f, n in sorted(fams.items())))
    print(
        f"관계: 선언 {sum(r.kind == 'declared' for r in relations)} · "
        f"추론 {sum(r.kind == 'inferred' for r in relations)} · 동일 키 군 {len(same_key)}"
    )
    for key, value in (meta.get("server") or {}).items():
        print(f"  {key} = {value}")
    print(f"산출물: {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
