#!/usr/bin/env python3
"""자산관리(ITAM) 로컬 MariaDB 샌드박스 init SQL 생성기 (plans/95 §4.6.3·§4.6.4 · W-S1).

전사본 `testdata/itam/schema.yaml`에서 두 파일을 파생한다.

    init/01_schema.sql  — 테이블 DDL(타입 · NULL · 3열 복합 PK). FK·인덱스는 전사본에 없다
    init/02_seed.sql    — 합성 시드(운영 데이터 반입 금지 — 금액·성명 포함 테이블이다)

규칙:
  - **DDL을 손으로 쓰지 않는다** — 컬럼 정의가 전사본과 SQL 두 곳에 있으면 한 곳이 늙는다.
    생성물은 커밋하고, 재생성 diff 0을 `tests/test_testdata/test_itam_generate_init.py`가 고정한다.
  - **전사본을 먼저 검증한다** — 컬럼 수 68/9 · 순번 연속 · PK 3열 · 타입 형식. YAML 함정
    (`no:`/`on:` 불리언 키 · flow 매핑 쉼표 절단)은 파일이 정상 로드된 채 값만 틀어진다.
  - **식별자는 전사본 `var`를 그대로 쓴다(G-4 미확정 가정)** — 변환은 `ident()` 한 곳에 가둔다.
    G-4가 다른 표기로 확정되면 그 함수만 바꿔 재생성한다. 전사본은 수정하지 않는다.
  - **이 DB에서 프로필·지식·유사어 시드를 추출하지 않는다**(plans/95 §4.6.2 파생 금지 — 순환 검증).

실행: python testdata/itam/generate_init.py
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
TRANSCRIPT = HERE / "schema.yaml"
SCHEMA_SQL = HERE / "init" / "01_schema.sql"
SEED_SQL = HERE / "init" / "02_seed.sql"

#: 전사본이 담아야 할 테이블별 컬럼 수(plans/95 §3.2 — 벤더 시트 전수 판독 결과).
EXPECTED_COLUMN_COUNTS = {"TCDMSIF80": 68, "TCDMSIF79": 9}
PK_WIDTH = 3

_TYPE_RE = re.compile(r"^(CHAR|VARCHAR)\((\d+)\)$|^DECIMAL\((\d+),(\d+)\)$")

EOL_TABLE = "TCDMSIF79"
MAIN_TABLE = "TCDMSIF80"

HEADER = """\
-- ============================================================================
-- 생성물 — 직접 수정하지 말 것. 재생성: python testdata/itam/generate_init.py
-- 출처: testdata/itam/schema.yaml (벤더 시트 전사본 · plans/95 §3.2)
-- ⚠ G-4 미확정 — 컬럼·테이블 식별자는 전사본 `var`·시트 표기를 그대로 쓴 **가정**이다.
--   운영 DB의 실제 식별자(대소문자·스네이크·한글 여부)를 증명하지 않는다(plans/95 §4.6.2).
-- ============================================================================
"""


class Sql(str):
    """리터럴로 인용하지 않고 그대로 넣을 SQL 식."""


# ──────────────────────────────────────────────
# 전사본 로드 · 검증
# ──────────────────────────────────────────────


def load_transcript(path: Path = TRANSCRIPT) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    validate_transcript(data)
    return data


def validate_transcript(data: dict[str, Any]) -> None:
    """생성 전에 전사본 무결성을 단언한다. 위반 시 ValueError."""
    tables = data.get("tables") or {}
    if set(tables) != set(EXPECTED_COLUMN_COUNTS):
        raise ValueError(f"테이블 집합 불일치: {sorted(tables)}")
    for name, expected in EXPECTED_COLUMN_COUNTS.items():
        table = tables[name]
        columns = table["columns"]
        if not (len(columns) == table.get("column_count") == expected):
            raise ValueError(
                f"{name}: 컬럼 수 {len(columns)} · 선언 {table.get('column_count')} "
                f"· 기대 {expected}"
            )
        seqs = [c.get("seq") for c in columns]
        if seqs != list(range(1, expected + 1)):
            raise ValueError(f"{name}: seq가 1..{expected} 연속이 아니다")
        names = [c.get("var") for c in columns]
        if len(set(names)) != len(names) or not all(isinstance(n, str) for n in names):
            raise ValueError(f"{name}: var 누락 또는 중복")
        for col in columns:
            if not _TYPE_RE.match(str(col.get("type", ""))):
                raise ValueError(
                    f"{name}.{col.get('var')}: 해석할 수 없는 타입 {col.get('type')!r}"
                )
            if not isinstance(col.get("nullable"), bool):
                raise ValueError(f"{name}.{col.get('var')}: nullable이 불리언이 아니다")
        pk = [c["var"] for c in sorted((c for c in columns if c.get("pk")), key=lambda c: c["pk"])]
        if len(pk) != PK_WIDTH or pk != table.get("primary_key"):
            raise ValueError(f"{name}: PK {pk} ≠ primary_key {table.get('primary_key')}")


# ──────────────────────────────────────────────
# DDL
# ──────────────────────────────────────────────


def ident(name: str) -> str:
    """물리 식별자. **G-4 미확정 가정** — 전사본 표기를 그대로 쓴다(변환 지점은 여기 한 곳)."""
    return f"`{name}`"


def render_schema_sql(data: dict[str, Any]) -> str:
    parts = [HEADER, "SET NAMES utf8mb4;\n"]
    for name in EXPECTED_COLUMN_COUNTS:
        table = data["tables"][name]
        lines = [
            f"  {ident(c['var'])} {c['type']}{'' if c['nullable'] else ' NOT NULL'}"
            for c in table["columns"]
        ]
        lines.append(f"  PRIMARY KEY ({', '.join(ident(k) for k in table['primary_key'])})")
        parts.append(
            f"\n-- {table['description']}\n"
            f"CREATE TABLE {ident(name)} (\n" + ",\n".join(lines) + "\n);\n"
        )
    return "".join(parts)


# ──────────────────────────────────────────────
# 합성 시드 — 정답을 알고 있는 데이터 (plans/95 §4.6.4)
# ──────────────────────────────────────────────
#
# 날짜는 init 시점 상대값이다 — 고정 리터럴이면 달이 바뀌면 기대 행이 조용히 0건이 된다.
# 기대 결과(호스트 목록)는 testdata/itam/README.md에 수기 SQL과 함께 고정한다.

_TODAY = "CURDATE()"
_QUARTER_START = "(MAKEDATE(YEAR(CURDATE()), 1) + INTERVAL (QUARTER(CURDATE()) - 1) QUARTER)"
_QUARTER_END = f"({_QUARTER_START} + INTERVAL 1 QUARTER - INTERVAL 1 DAY)"
_NOW_YMS = Sql("DATE_FORMAT(CURRENT_TIMESTAMP(6), '%Y%m%d%H%i%s%f')")

#: 사용률 판별 소수부(T5) — 응답 숫자만으로 자산 DB가 답했는지 가린다.
USAGE_RATE_COLUMNS = ("sevrCPUUseRt", "sevrMmryUseRt", "wholStrgeUseRt")
USAGE_RATE_FRACTION = "37"

_SYNTH_NAMES = ("홍길동", "김철수", "이영희")  # 성명 형태의 합성값(실존 인물 아님)


def ymd(expr: str) -> Sql:
    return Sql(f"DATE_FORMAT({expr}, '%Y%m%d')")


def code_marker(sql_type: str) -> str:
    """합성 코드값 표식 — `Z` + `9` 채움(G-5 미확정 · 프로필로 새면 즉시 들킨다)."""
    width = int(_TYPE_RE.match(sql_type).group(2))
    return "Z" + "9" * (width - 1)


# 폴스타 로컬 샌드박스(testdata/pg/init/02_insert_cmm_resource.sql)에 실재하는 호스트·IP 30쌍.
_SANDBOX_HOSTS = [
    (f"svr-{role}-{n:02d}", f"10.0.{net}.{n}")
    for role, net in (("web", 1), ("was", 2), ("db", 3))
    for n in range(1, 11)
]

# 호스트별 설계. 키: 목적 설명(note) · 80 덮어쓰기(main) · 79 덮어쓰기(eol, None이면 79 짝 없음).
_DESIGN: dict[str, dict[str, Any]] = {
    # ── 호스트 키 4형 (G-6 · 트랙 E 병합) ─────────────────────────────
    "svr-web-01": {"note": "호스트 키 ① 폴스타와 정확 일치"},
    "svr-web-02": {"note": "호스트 키 ② 대소문자만 다름", "host": "SVR-WEB-02"},
    "svr-web-03": {"note": "호스트 키 ③ FQDN", "host": "svr-web-03.synth.example"},
    "svr-web-04": {"note": "호스트 키 ④ 한 칸에 다중 IP", "ip": "10.0.1.4,10.0.1.104"},
    # ── 질의 ① 지원 종료일 6개월 내 (TCDMSIF79) ─────────────────────
    "svr-was-01": {"note": "① 안 — HW 종료 +3개월",
                   "eol": {"hWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 3 MONTH")}},
    "svr-was-02": {"note": "① 안 — SW 축만 +2개월(HW는 기준값)",
                   "eol": {"sWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 2 MONTH")}},
    "svr-was-03": {"note": "① 경계 — HW 종료 정확히 +6개월(포함)",
                   "eol": {"hWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 6 MONTH")}},
    "svr-was-04": {"note": "① 밖 — HW 종료 +7개월",
                   "eol": {"hWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 7 MONTH")}},
    "svr-was-05": {"note": "① 밖 — 이미 종료(-1개월)",
                   "eol": {"hWSportEndYmd": ymd(f"{_TODAY} - INTERVAL 1 MONTH")}},
    "svr-was-06": {"note": "① 밖 — TCDMSIF79 짝 없음(INNER/LEFT 조인 차이)", "eol": None},
    # ── 질의 ② 유지보수 계약 이번 분기 만료 ─────────────────────────
    "svr-db-01": {"note": "② 경계 — 분기 첫날(포함)",
                  "main": {"manmenCtrcEndYmd": ymd(_QUARTER_START)}},
    "svr-db-02": {"note": "② 경계 — 분기 마지막 날(포함)",
                  "main": {"manmenCtrcEndYmd": ymd(_QUARTER_END)}},
    "svr-db-03": {"note": "② 안 — 분기 첫날 +1개월",
                  "main": {"manmenCtrcEndYmd": ymd(f"{_QUARTER_START} + INTERVAL 1 MONTH")}},
    "svr-db-04": {"note": "② 밖 — 분기 첫날 -1일",
                  "main": {"manmenCtrcEndYmd": ymd(f"{_QUARTER_START} - INTERVAL 1 DAY")}},
    "svr-db-05": {"note": "② 밖 — 분기 마지막 날 +1일",
                  "main": {"manmenCtrcEndYmd": ymd(f"{_QUARTER_END} + INTERVAL 1 DAY")}},
    # ── 질의 ③ 경과년수 5년 이상 ─────────────────────────────────────
    "svr-db-06": {"note": "③ 밖 — 경과 4년", "main": {"elapsNoy": 4}},
    "svr-db-07": {"note": "③ 경계 — 경과 5년(포함)", "main": {"elapsNoy": 5}},
    "svr-db-08": {"note": "③ 안 — 경과 7년", "main": {"elapsNoy": 7}},
    "svr-db-09": {"note": "③ 밖 — 경과년수 NULL", "main": {"elapsNoy": None}},
}


def _base_main(i: int, host: str, ip: str) -> dict[str, Any]:
    """모든 행의 기준값 — 세 질의 어느 것에도 걸리지 않는다."""
    return {
        "sevrHostName": host,
        "iPCtnt": ip,
        "cPUCnt": 8,
        "sevrMmryCapc": 64,
        "wholStrgeCapc": 500,
        "sevrCPUUseRt": Sql(f"{10 + i}.{USAGE_RATE_FRACTION}"),
        "sevrMmryUseRt": Sql(f"{40 + i}.{USAGE_RATE_FRACTION}"),
        "wholStrgeUseRt": Sql(f"{50 + i}.{USAGE_RATE_FRACTION}"),
        "rspblPsnEmpid": f"T{i:06d}",
        "rspblPsnEmnm": _SYNTH_NAMES[i % len(_SYNTH_NAMES)],
        "rspblBrnName": "합성부점",
        "byCtrcName": f"합성 구매계약 {i:02d}",
        "acqsiAmt": 10_000_000 + i * 100_000,
        "elapsNoy": 2,
        "manmenCnpr": 1_200_000 + i * 10_000,
        "manmenCtrcStartYmd": ymd(f"{_TODAY} + INTERVAL 1 YEAR"),
        "manmenCtrcEndYmd": ymd(f"{_TODAY} + INTERVAL 2 YEAR"),
        "manmenCtrcName": f"합성 유지보수계약 {i:02d}",
        "rmainAcbkAmt": 1_000_000 + i * 10_000,
        "srialNoCtnt": f"SN-SYNTH-{i:04d}",
        "sysRegiUno": "T000000",
        "sysRegiPrcssYMS": _NOW_YMS,
        "sysLastUno": "T000000",
        "sysLastPrcssYMS": _NOW_YMS,
    }


def _base_eol() -> dict[str, Any]:
    return {
        "hWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 36 MONTH"),
        "sWSportEndYmd": ymd(f"{_TODAY} + INTERVAL 36 MONTH"),
        "sysRegiUno": "T000000",
        "sysRegiPrcssYMS": _NOW_YMS,
        "sysLastUno": "T000000",
        "sysLastPrcssYMS": _NOW_YMS,
    }


def _fill(columns: list[dict[str, Any]], values: dict[str, Any], table: str) -> list[Any]:
    """전사본 순서로 값을 채운다. 코드 컬럼은 표식, 나머지 미지정은 NULL."""
    unknown = set(values) - {c["var"] for c in columns}
    if unknown:
        raise ValueError(f"{table}: 전사본에 없는 컬럼 {sorted(unknown)}")
    row = []
    for col in columns:
        if col.get("code"):
            value: Any = code_marker(col["type"])
        else:
            value = values.get(col["var"])
        if value is None and not col["nullable"]:
            raise ValueError(f"{table}.{col['var']}: NOT NULL 컬럼에 값이 없다")
        row.append(value)
    return row


def build_seed_rows(data: dict[str, Any]) -> dict[str, list[tuple[str, list[Any]]]]:
    """테이블별 (설계 설명, 값 목록) — 값 순서는 전사본 컬럼 순서."""
    main_cols = data["tables"][MAIN_TABLE]["columns"]
    eol_cols = data["tables"][EOL_TABLE]["columns"]
    out: dict[str, list[tuple[str, list[Any]]]] = {MAIN_TABLE: [], EOL_TABLE: []}
    for i, (sandbox_host, sandbox_ip) in enumerate(_SANDBOX_HOSTS):
        design = _DESIGN.get(sandbox_host, {})
        host = design.get("host", sandbox_host)
        ip = design.get("ip", sandbox_ip)
        note = f"{host} · {design.get('note', '기준 행 — 세 질의 모두 밖')}"

        main = _base_main(i, host, ip)
        main.update(design.get("main", {}))
        if main["elapsNoy"] is not None:  # 취득일은 경과년수와 맞춘다
            main["acqsiYmd"] = ymd(f"{_TODAY} - INTERVAL {main['elapsNoy']} YEAR")
        out[MAIN_TABLE].append((note, _fill(main_cols, main, MAIN_TABLE)))

        if "eol" in design and design["eol"] is None:
            continue
        eol = {"sevrHostName": host, "iPCtnt": ip, **_base_eol(), **design.get("eol", {})}
        out[EOL_TABLE].append((note, _fill(eol_cols, eol, EOL_TABLE)))
    return out


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Sql):
        return str(value)
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def render_seed_sql(data: dict[str, Any]) -> str:
    rows = build_seed_rows(data)
    parts = [
        HEADER,
        "-- 합성 시드(plans/95 §4.6.4) — 날짜는 init 시점 상대값, 코드값은 `Z9…` 표식,\n"
        "-- 사용률 소수부 .37 고정(T5 판별), 성명은 합성값. 기대 결과는 testdata/itam/README.md.\n",
        "SET NAMES utf8mb4;\n",
    ]
    for table in (MAIN_TABLE, EOL_TABLE):
        columns = ", ".join(ident(c["var"]) for c in data["tables"][table]["columns"])
        body = []
        for note, values in rows[table]:
            body.append(f"-- {note}\n(" + ", ".join(_literal(v) for v in values) + ")")
        parts.append(
            f"\nINSERT INTO {ident(table)} ({columns}) VALUES\n" + ",\n".join(body) + ";\n"
        )
    return "".join(parts)


def main() -> None:
    data = load_transcript()
    SCHEMA_SQL.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_SQL.write_text(render_schema_sql(data), encoding="utf-8")
    SEED_SQL.write_text(render_seed_sql(data), encoding="utf-8")
    print(f"생성: {SCHEMA_SQL.relative_to(HERE)} · {SEED_SQL.relative_to(HERE)}")


if __name__ == "__main__":
    main()
