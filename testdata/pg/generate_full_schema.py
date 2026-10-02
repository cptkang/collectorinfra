#!/usr/bin/env python3
"""
sample/cleaned_tables.json (폴스타 DB 스키마 덤프)을 분석하여
polestar_pg 컨테이너용 전체 스키마 DDL과 타입 기반 더미 데이터를 생성한다.

산출물:
  testdata/pg/init/04_create_all_tables.sql  — 394개 테이블 CREATE (IF NOT EXISTS)
  testdata/pg/init/05_insert_dummy_data.sql  — 더미 데이터 INSERT

주의:
  - 실데이터가 있는 cmm_resource / core_config_prop 는 더미 INSERT 대상에서 제외(보존).
  - DDL 은 IF NOT EXISTS 라 기존 2개 테이블을 재생성/삭제하지 않는다.
  - JSON 에 PK/FK/길이/sample_data 정보가 없으므로 컬럼명+타입만 반영한다.
  - 사용자 선택에 따라 더미는 순수 타입 기반이며 테이블 간 JOIN 정합성은 보장하지 않는다.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "sample" / "cleaned_tables.json"
OUT_DIR = ROOT / "testdata" / "pg" / "init"

# 실데이터 보존: 더미 INSERT 에서 제외할 테이블
PRESERVE = {"cmm_resource", "core_config_prop"}

ROWS_PER_TABLE = 5

# JSON 타입 → PostgreSQL DDL 타입
TYPE_MAP = {
    "character varying": "varchar",
    "bigint": "bigint",
    "integer": "integer",
    "smallint": "smallint",
    "double precision": "double precision",
    "timestamp without time zone": "timestamp",
    "oid": "oid",
    "text": "text",
    "boolean": "boolean",
    "real": "real",
    "bytea": "bytea",
    "numeric": "numeric",
}


def q(ident: str) -> str:
    """식별자 인용 (예약어 대비)."""
    return '"' + ident.replace('"', '""') + '"'


def ddl_type(json_type: str) -> str:
    if json_type not in TYPE_MAP:
        raise ValueError(f"미정의 타입: {json_type}")
    return TYPE_MAP[json_type]


def dummy_value(col_name: str, json_type: str, i: int) -> str:
    """타입 기반 더미 SQL 리터럴. i 는 1부터 시작하는 행 인덱스."""
    if json_type in ("bigint", "integer", "smallint", "oid"):
        return str(i)
    if json_type in ("double precision", "real", "numeric"):
        return f"{i}.5"
    if json_type == "boolean":
        return "true" if i % 2 else "false"
    if json_type == "timestamp without time zone":
        # 2026-06-01 부터 i 일씩
        return f"'2026-06-{i:02d} 09:00:00'"
    if json_type == "bytea":
        return r"'\x00'::bytea"
    # character varying / text
    val = f"{col_name}_{i}"
    return "'" + val.replace("'", "''") + "'"


def main() -> None:
    data = json.loads(SRC.read_text())
    # 테이블명 정렬: 결정적 산출물
    tables = sorted(data.keys())

    # --- DDL ---
    ddl_lines = [
        "-- 자동 생성: testdata/pg/generate_full_schema.py",
        "-- 원본: sample/cleaned_tables.json (폴스타 DB 스키마 덤프)",
        f"-- 테이블 수: {len(tables)}",
        "-- IF NOT EXISTS 사용 — 기존 cmm_resource/core_config_prop 보존",
        "",
        "CREATE SCHEMA IF NOT EXISTS polestar;",
        "",
    ]
    for full in tables:
        schema, _, tname = full.partition(".")
        cols = data[full]["columns"]
        col_defs = []
        for c in cols:
            nn = "" if c["nullable"] else " NOT NULL"
            col_defs.append(f"  {q(c['name'])} {ddl_type(c['type'])}{nn}")
        ddl_lines.append(f"CREATE TABLE IF NOT EXISTS {q(schema)}.{q(tname)} (")
        ddl_lines.append(",\n".join(col_defs))
        ddl_lines.append(");")
        ddl_lines.append("")

    (OUT_DIR / "04_create_all_tables.sql").write_text("\n".join(ddl_lines))

    # --- 더미 INSERT ---
    dml_lines = [
        "-- 자동 생성: testdata/pg/generate_full_schema.py",
        "-- 타입 기반 더미 데이터 (테이블당 %d행)" % ROWS_PER_TABLE,
        "-- cmm_resource / core_config_prop 는 실데이터 보존을 위해 제외",
        "",
    ]
    dummy_count = 0
    for full in tables:
        schema, _, tname = full.partition(".")
        if tname in PRESERVE:
            continue
        cols = data[full]["columns"]
        collist = ", ".join(q(c["name"]) for c in cols)
        # 재적용 가능하도록 TRUNCATE 후 INSERT
        dml_lines.append(f"TRUNCATE TABLE {q(schema)}.{q(tname)};")
        rows = []
        for i in range(1, ROWS_PER_TABLE + 1):
            vals = ", ".join(dummy_value(c["name"], c["type"], i) for c in cols)
            rows.append(f"  ({vals})")
        dml_lines.append(
            f"INSERT INTO {q(schema)}.{q(tname)} ({collist}) VALUES\n"
            + ",\n".join(rows)
            + ";"
        )
        dml_lines.append("")
        dummy_count += 1

    (OUT_DIR / "05_insert_dummy_data.sql").write_text("\n".join(dml_lines))

    print(f"DDL: {len(tables)}개 테이블 → 04_create_all_tables.sql")
    print(f"DUMMY: {dummy_count}개 테이블 × {ROWS_PER_TABLE}행 → 05_insert_dummy_data.sql")
    print(f"보존(더미 제외): {sorted(PRESERVE)}")


if __name__ == "__main__":
    main()
