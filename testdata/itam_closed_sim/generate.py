#!/usr/bin/env python3
"""ITAM 외부망 모의 DB 생성기 — 반출 카탈로그 → DDL · 합성 행 (plans/140 W4 · D-311 ②⑤ · G-4).

내부망 벤치 반출물(`schema_catalog.yaml` · 2회차부터 `code_samples.yaml`)로 외부망에서
쿼리 자동 생성을 시험할 MariaDB 모의 DB의 init SQL을 만든다. 두 하위 명령이 있다.

    ddl   반출 카탈로그 → `01_schema.sql`(빈 스키마) + `09_readonly_user.sql`(읽기 전용 계정 사본)
    rows  반출 카탈로그 (+ code_samples) → `02_rows.sql`(테이블당 N행 합성 INSERT)

규칙:
  - **산출물은 커밋하지 않는다** — 치환 코드값·합성 행이 추적 경로에 떨어지지 않게
    출력은 `generated/`(이 디렉터리의 `.gitignore`가 막는다) · 저장소 밖 · git 무시 경로만
    허용한다(`ensure_safe_out`).
  - **타입 길이가 없으면 보수적 기본값**(`DEFAULT_TYPE_ARGS`) — 값이 잘리지 않는 쪽이면서
    MariaDB 행 크기 한도(65,535바이트)·인덱스 키 한도(3,072바이트) 안. 길이가 있으면 그대로
    쓴다. README에 같은 표가 있다.
  - **주석은 DB 주석 원문만**(`meaning_source: db_comment`) — LLM 설명·캐시 설명은 싣지 않는다.
  - **합성 값 규칙(우선순위)**: 채택 관계 자식 컬럼 = 부모 생성 값 → 주석 코드 열거
    (정의 유래 · 2쌍 이상) → `code_samples` 치환값(`substitution: ok`) → 카탈로그
    `profile.flag` → 값 형식 비율 0.95 이상 형식 → 타입·이름 기반 합성값. 기본키·유일 부모
    컬럼은 유일하게 만든다. 사람 이름·주민번호·전화 형태는 만들지 않는다(IPv4는 문서용
    대역 · 호스트명은 `sim-` 접두).
  - 같은 입력·같은 seed면 같은 출력이다(난수는 `random.Random(seed)` 하나).

실행(저장소 루트에서):
    python testdata/itam_closed_sim/generate.py ddl  <반출 경로|schema_catalog.yaml> [--out DIR]
    python testdata/itam_closed_sim/generate.py rows <반출 경로|schema_catalog.yaml>
        [--code-samples PATH] [--rows N] [--seed S] [--out DIR]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import random
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
if str(REPO_ROOT) not in sys.path:  # 스크립트 직접 실행 시 src 패키지를 찾게 한다
    sys.path.insert(0, str(REPO_ROOT))

from src.domain.schema_inference import parse_comment_enum  # noqa: E402

DEFAULT_OUT = HERE / "generated"
READONLY_USER_SQL = HERE / "readonly_user.sql"
SCHEMA_FILE = "01_schema.sql"
ROWS_FILE = "02_rows.sql"
READONLY_FILE = "09_readonly_user.sql"
CATALOG_FILE = "schema_catalog.yaml"
CODE_SAMPLES_FILE = "code_samples.yaml"

#: 길이 없는 타입의 보수적 기본값(README 「기본 타입 표」와 같아야 한다).
#: CHAR 최대 255 · utf8mb4 4바이트 기준 — 1회차 반출 최대 테이블(CHAR 29·VARCHAR 22·DECIMAL 17)이
#: 행 크기 한도 65,535바이트 안에 들어가는 값이다.
DEFAULT_TYPE_ARGS: dict[str, str] = {"char": "100", "varchar": "500", "decimal": "38,10"}

ROW_BYTES_MAX = 65_535  # MariaDB 서버 행 크기 한도(BLOB·TEXT 제외 컬럼 최대 바이트 합)
KEY_BYTES_MAX = 3_072  # InnoDB(DYNAMIC) 인덱스 키 한도
MB_MAXLEN = 4  # utf8mb4

FORMAT_MIN_RATIO = 0.95
NULL_RATE = 0.15
DEFAULT_ROWS = 20
DEFAULT_SEED = 140
MAX_ROW_ATTEMPTS = 30
INSERT_CHUNK = 200

#: 문서용 IPv4 대역(RFC 5737) — 실 주소가 아니다.
DOC_IPV4_NETS: tuple[str, ...] = ("192.0.2", "198.51.100", "203.0.113")
DATE_BASE = _dt.date(2024, 1, 1)
DATE_SPAN_DAYS = 3 * 365

_TYPE_RE = re.compile(
    r"^\s*([A-Za-z][A-Za-z0-9_ ]*?)\s*(?:\(\s*([^)]*?)\s*\))?"
    r"\s*((?:unsigned|signed|zerofill|\s)*)$",
    re.IGNORECASE,
)
_NUMERIC_TYPES = {
    "tinyint",
    "smallint",
    "mediumint",
    "int",
    "integer",
    "bigint",
    "decimal",
    "numeric",
}
_TEXT_LOB_TYPES = {
    "text",
    "tinytext",
    "mediumtext",
    "longtext",
    "blob",
    "mediumblob",
    "longblob",
    "clob",
}


# ──────────────────────────────────────────────
# 입력
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class ColumnSpec:
    """반출 카탈로그의 컬럼 1개."""

    name: str
    base: str  # 소문자 타입 이름(char · varchar · decimal …)
    args: str | None  # 괄호 안 원문(길이 · 정밀도) — 없으면 기본값 표
    suffix: str  # 타입 뒤 수식어(unsigned 등) — 대개 빈 문자열
    nullable: bool
    comment: str | None  # DB 주석 원문(meaning_source == db_comment일 때만)
    profile: Mapping[str, Any]

    @property
    def effective_args(self) -> str | None:
        """실제 DDL에 쓰는 괄호 인자 — 원문 길이가 없으면 기본값 표."""
        return self.args if self.args else DEFAULT_TYPE_ARGS.get(self.base)

    @property
    def sql_type(self) -> str:
        """DDL 타입 표기(예: ``CHAR(100)``)."""
        args = self.effective_args
        text = self.base.upper() + (f"({args})" if args else "")
        return f"{text} {self.suffix.upper()}" if self.suffix else text

    @property
    def length(self) -> int | None:
        """문자 타입 길이(문자 수) — 문자 타입이 아니거나 모르면 None."""
        if self.base not in {"char", "varchar"} or not self.effective_args:
            return None
        head = self.effective_args.split(",")[0].strip()
        return int(head) if head.isdigit() else None

    @property
    def decimal_ps(self) -> tuple[int, int]:
        """DECIMAL 정밀도·소수 자릿수(모르면 MariaDB 기본 10,0)."""
        parts = [p.strip() for p in (self.effective_args or "").split(",") if p.strip()]
        precision = int(parts[0]) if parts and parts[0].isdigit() else 10
        scale = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return precision, scale


@dataclass(frozen=True)
class Relation:
    """채택된 관계 1건 — 자식 컬럼 ← 부모 컬럼."""

    child: str
    parent: str
    pairs: tuple[tuple[str, str], ...]
    unique_parent: bool | None


@dataclass(frozen=True)
class TableSpec:
    """반출 카탈로그의 테이블 1개."""

    name: str
    columns: tuple[ColumnSpec, ...]
    key: tuple[str, ...]
    comment: str | None

    def column(self, name: str) -> ColumnSpec | None:
        """이름으로 컬럼을 찾는다."""
        return next((c for c in self.columns if c.name == name), None)


@dataclass
class Catalog:
    """DDL·행 생성 입력 — 테이블은 이름순(결정적)."""

    tables: dict[str, TableSpec]
    relations: list[Relation]
    warnings: list[str] = field(default_factory=list)


def _parse_type(raw: Any) -> tuple[str, str | None, str]:
    """``char`` · ``varchar(30)`` · ``decimal(15, 2) unsigned`` → (base, args, suffix)."""
    text = str(raw or "varchar").strip()
    match = _TYPE_RE.match(text)
    if not match:
        raise ValueError(f"타입 표기를 읽을 수 없습니다: {text!r}")
    base, args, suffix = match.groups()
    return base.strip().lower(), (args.replace(" ", "") if args else None), suffix.strip().lower()


def _db_comment(node: Mapping[str, Any]) -> str | None:
    """DB 주석 원문만 — 다른 출처의 의미 문장은 None."""
    if node.get("meaning_source") != "db_comment":
        return None
    meaning = node.get("meaning")
    return str(meaning) if meaning else None


def resolve_export(path: Path) -> tuple[Path, Path | None]:
    """반출 경로(디렉터리 또는 카탈로그 파일) → (카탈로그, 옆 code_samples 파일 또는 None)."""
    catalog = path / CATALOG_FILE if path.is_dir() else path
    if not catalog.is_file():
        raise FileNotFoundError(f"반출 카탈로그가 없습니다: {catalog}")
    samples = catalog.parent / CODE_SAMPLES_FILE
    return catalog, (samples if samples.is_file() else None)


def load_catalog(data: Mapping[str, Any]) -> Catalog:
    """반출 `schema_catalog.yaml` 내용 → `Catalog`.

    관계는 `kind: p1`이고 `accepted: true`인 것만 쓴다. 양쪽 테이블에 같은 관계가 실려도
    1건으로 친다.
    없는 테이블·컬럼을 가리키는 관계와 자기 참조는 경고와 함께 버린다.
    """
    raw_tables = data.get("tables") or {}
    if not isinstance(raw_tables, Mapping) or not raw_tables:
        raise ValueError("카탈로그에 tables가 없습니다")
    tables: dict[str, TableSpec] = {}
    for name in sorted(raw_tables):
        node = raw_tables[name] or {}
        columns: list[ColumnSpec] = []
        for col in node.get("columns") or []:
            base, args, suffix = _parse_type(col.get("type"))
            columns.append(
                ColumnSpec(
                    name=str(col["name"]),
                    base=base,
                    args=args,
                    suffix=suffix,
                    nullable=bool(col.get("nullable", True)),
                    comment=_db_comment(col),
                    profile=col.get("profile") or {},
                )
            )
        if not columns:
            raise ValueError(f"컬럼이 없는 테이블: {name}")
        tables[str(name)] = TableSpec(
            name=str(name),
            columns=tuple(columns),
            key=tuple(str(k) for k in node.get("key") or []),
            comment=_db_comment(node),
        )

    relations: list[Relation] = []
    seen: set[tuple[str, str, tuple[tuple[str, str], ...]]] = set()
    warnings: list[str] = []
    for name in sorted(raw_tables):
        for rel in (raw_tables[name] or {}).get("relations") or []:
            if rel.get("kind") != "p1" or rel.get("accepted") is not True:
                continue
            child, parent = str(rel.get("from")), str(rel.get("to"))
            pairs = tuple((str(a), str(b)) for a, b in rel.get("columns") or [])
            ident = (child, parent, pairs)
            if ident in seen:
                continue
            seen.add(ident)
            if child == parent or not pairs:
                warnings.append(f"관계 제외(자기 참조·컬럼 없음): {child} → {parent}")
                continue
            if child not in tables or parent not in tables:
                warnings.append(f"관계 제외(없는 테이블): {child} → {parent}")
                continue
            missing = [a for a, _ in pairs if tables[child].column(a) is None] + [
                b for _, b in pairs if tables[parent].column(b) is None
            ]
            if missing:
                warnings.append(f"관계 제외(없는 컬럼 {missing}): {child} → {parent}")
                continue
            relations.append(Relation(child, parent, pairs, rel.get("unique_parent")))
    return Catalog(tables=tables, relations=relations, warnings=warnings)


def load_code_samples(data: Mapping[str, Any] | None) -> dict[tuple[str, str], list[str]]:
    """`code_samples.yaml` → ``{(테이블, 컬럼): 치환값}`` — `substitution: ok`·값 있는 것만."""
    if not data:
        return {}
    result: dict[tuple[str, str], list[str]] = {}
    for key, node in (data.get("columns") or {}).items():
        table, sep, column = str(key).partition(".")
        if not sep or not isinstance(node, Mapping) or node.get("substitution") != "ok":
            continue
        values = [str(v) for v in node.get("values") or []]
        if values:
            result[(table, column)] = values
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML 매핑이 아닙니다: {path}")
    return loaded


# ──────────────────────────────────────────────
# 출력 경로 가드
# ──────────────────────────────────────────────


def _is_git_ignored(path: Path) -> bool:
    """저장소 안 경로가 git 무시 대상인지(git이 없거나 실패하면 False — 거부 쪽)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-q", str(path)],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def ensure_safe_out(out: Path) -> Path:
    """출력 디렉터리 허용 여부 — `generated/` 아래 · 저장소 밖 · git 무시 경로만 통과.

    Raises:
        ValueError: 추적될 수 있는 저장소 경로(치환값·합성 행이 커밋될 위험)
    """
    resolved = out.resolve()
    default = DEFAULT_OUT.resolve()
    if resolved == default or default in resolved.parents:
        return resolved
    root = REPO_ROOT.resolve()
    if resolved != root and root not in resolved.parents:
        return resolved
    probe = resolved / SCHEMA_FILE  # 디렉터리 패턴(`dir/`) 무시 규칙도 잡히게 파일 경로로 묻는다
    if _is_git_ignored(probe):
        return resolved
    raise ValueError(
        f"출력 경로 거부: {resolved} — 저장소 추적 경로다. "
        f"{default} · 저장소 밖 · .gitignore 대상 경로만 쓸 수 있다"
        "(D-311 ② 치환값·합성 행 커밋 금지)"
    )


# ──────────────────────────────────────────────
# DDL
# ──────────────────────────────────────────────


def quote_ident(name: str) -> str:
    """MariaDB 식별자 인용(백틱 · 내부 백틱은 두 번)."""
    return "`" + name.replace("`", "``") + "`"


def quote_string(text: str) -> str:
    """MariaDB 문자열 리터럴(기본 sql_mode — 백슬래시도 이스케이프)."""
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _column_bytes(col: ColumnSpec) -> int:
    """행 크기 한도 계산용 최대 바이트(근사)."""
    if col.base in {"char", "varchar"}:
        length = col.length or 255
        return length * MB_MAXLEN + (2 if col.base == "varchar" else 0)
    if col.base in {"decimal", "numeric"}:
        precision, scale = col.decimal_ps
        return ((precision - scale) // 9 + 1) * 4 + (scale // 9 + 1) * 4
    if col.base in _TEXT_LOB_TYPES:
        return 12
    return 8


def check_limits(table: TableSpec) -> list[str]:
    """MariaDB 생성 한도(행 크기 · 기본키 길이) 위반 사유 — 없으면 빈 목록."""
    problems: list[str] = []
    row = sum(_column_bytes(c) for c in table.columns)
    if row > ROW_BYTES_MAX:
        problems.append(f"{table.name}: 행 최대 바이트 {row} > {ROW_BYTES_MAX}")
    key_cols = [table.column(k) for k in table.key]
    key = sum(_column_bytes(c) for c in key_cols if c is not None)
    if key > KEY_BYTES_MAX:
        problems.append(f"{table.name}: 기본키 바이트 {key} > {KEY_BYTES_MAX}")
    missing = [k for k, c in zip(table.key, key_cols, strict=True) if c is None]
    if missing:
        problems.append(f"{table.name}: 기본키 컬럼 없음 {missing}")
    return problems


def render_table_ddl(table: TableSpec) -> str:
    """테이블 1개의 ``CREATE TABLE`` 문."""
    lines: list[str] = []
    for col in table.columns:
        parts = [quote_ident(col.name), col.sql_type]
        if not col.nullable or col.name in table.key:
            parts.append("NOT NULL")
        if col.comment:
            parts.append(f"COMMENT {quote_string(col.comment)}")
        lines.append("  " + " ".join(parts))
    if table.key:
        lines.append("  PRIMARY KEY (" + ", ".join(quote_ident(k) for k in table.key) + ")")
    options = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"
    if table.comment:
        options += f" COMMENT={quote_string(table.comment)}"
    return f"CREATE TABLE {quote_ident(table.name)} (\n" + ",\n".join(lines) + f"\n) {options};\n"


def render_schema_sql(catalog: Catalog, source: str) -> str:
    """빈 스키마 DDL 전체(테이블 이름순)."""
    header = [
        "-- ITAM 외부망 모의 DB — 빈 스키마 (plans/140 W4 · D-311 ⑤)",
        f"-- 원천: {source}",
        "-- 생성물 — 커밋 금지(generated/는 .gitignore)",
        "-- 재생성: testdata/itam_closed_sim/generate.py ddl",
        "-- 길이 없는 타입은 보수적 기본값: "
        + " · ".join(f"{k}→{k.upper()}({v})" for k, v in DEFAULT_TYPE_ARGS.items()),
        "",
    ]
    body = [render_table_ddl(catalog.tables[name]) for name in sorted(catalog.tables)]
    return "\n".join(header) + "\n".join(body)


# ──────────────────────────────────────────────
# 합성 행
# ──────────────────────────────────────────────

ValueFn = Callable[[random.Random, int | None], Any]


def _fit(text: str, length: int | None) -> str:
    return text if length is None else text[:length]


def _date8(offset: int) -> str:
    return (DATE_BASE + _dt.timedelta(days=offset % DATE_SPAN_DAYS)).strftime("%Y%m%d")


def _datetime14(offset: int) -> str:
    moment = _dt.datetime.combine(DATE_BASE, _dt.time()) + _dt.timedelta(seconds=offset * 3_607)
    return moment.strftime("%Y%m%d%H%M%S")


def _ipv4(n: int) -> str:
    net = DOC_IPV4_NETS[(n // 254) % len(DOC_IPV4_NETS)]
    return f"{net}.{n % 254 + 1}"


def _format_of(col: ColumnSpec) -> str | None:
    """값 형식 비율이 기준 이상인 형식 하나(가장 높은 비율 · 동률은 이름순)."""
    formats = col.profile.get("formats") or {}
    best: tuple[float, str] | None = None
    for kind in ("date8", "datetime14", "ipv4", "hostname"):
        ratio = formats.get(kind)
        if isinstance(ratio, (int, float)) and ratio >= FORMAT_MIN_RATIO:
            if best is None or ratio > best[0]:
                best = (float(ratio), kind)
    return best[1] if best else None


def _finite_values(
    table: TableSpec, col: ColumnSpec, code_samples: Mapping[tuple[str, str], list[str]]
) -> tuple[str, list[str]] | None:
    """유한 값 집합(출처 · 값) — 주석 열거 > 치환 코드값 > 플래그."""
    enum = parse_comment_enum(col.comment)
    if enum:
        return "comment_enum", list(enum)
    samples = code_samples.get((table.name, col.name))
    if samples:
        return "code_samples", list(samples)
    flag = col.profile.get("flag")
    if isinstance(flag, list) and flag:
        return "flag", [str(v) for v in flag]
    return None


def _value_fn(
    table: TableSpec, col: ColumnSpec, code_samples: Mapping[tuple[str, str], list[str]]
) -> tuple[str, ValueFn]:
    """컬럼 값 생성 함수와 출처. 함수는 ``seq``가 주어지면 seq마다 다른 값(유일 컬럼용)을 낸다."""
    finite = _finite_values(table, col, code_samples)
    if finite:
        source, values = finite

        def pick(rng: random.Random, seq: int | None) -> Any:
            return values[seq % len(values)] if seq is not None else rng.choice(values)

        return source, pick

    length = col.length
    kind = _format_of(col)
    if kind is not None:

        def fmt(rng: random.Random, seq: int | None) -> Any:
            n = (
                seq
                if seq is not None
                else rng.randrange(DATE_SPAN_DAYS if kind == "date8" else 10_000)
            )
            if kind == "date8":
                return _date8(n)
            if kind == "datetime14":
                return _datetime14(n)
            if kind == "ipv4":
                return _ipv4(n)
            return _fit(f"sim-{n:04d}", length)

        return f"format:{kind}", fmt

    if col.base in _NUMERIC_TYPES:
        precision, scale = col.decimal_ps
        int_digits = max(1, min(precision - scale, 6))
        frac = min(scale, 2)

        def num(rng: random.Random, seq: int | None) -> Any:
            whole = seq + 1 if seq is not None else rng.randrange(10**int_digits)
            if frac == 0 or seq is not None:
                return whole
            return Decimal(f"{whole}.{rng.randrange(10**frac):0{frac}d}")

        return "type", num

    def text(rng: random.Random, seq: int | None) -> Any:
        n = seq if seq is not None else rng.randrange(1_000)
        candidate = f"{col.name}{n}"
        if length is not None and len(candidate) > length:
            candidate = f"S{n}"
        return _fit(candidate, length)

    return "type", text


@dataclass
class RowsResult:
    """합성 행 생성 결과."""

    rows: dict[str, list[dict[str, Any]]]
    sources: dict[str, int]
    shortfall: dict[str, int]
    warnings: list[str]


def _generation_order(catalog: Catalog) -> tuple[list[str], list[Relation], list[str]]:
    """부모 먼저 순서(위상 정렬 · 동률 이름순) — 순환을 만드는 관계는 빼고 경고로 돌려준다."""
    names = sorted(catalog.tables)
    used: list[Relation] = []
    parents: dict[str, set[str]] = {n: set() for n in names}

    def reaches(src: str, dst: str) -> bool:
        stack, seen = [src], set()
        while stack:
            cur = stack.pop()
            if cur == dst:
                return True
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(parents[cur])
        return False

    dropped: list[Relation] = []
    for rel in catalog.relations:
        if reaches(rel.parent, rel.child):  # 부모가 이미 자식에 의존 → 순환
            dropped.append(rel)
            continue
        parents[rel.child].add(rel.parent)
        used.append(rel)

    order: list[str] = []
    done: set[str] = set()
    while len(order) < len(names):
        ready = [n for n in names if n not in done and parents[n] <= done]
        order.append(ready[0])
        done.add(ready[0])
    return order, used, [f"관계 제외(순환): {rel.child} → {rel.parent}" for rel in dropped]


def generate_rows(
    catalog: Catalog,
    code_samples: Mapping[tuple[str, str], list[str]] | None = None,
    *,
    n_rows: int = DEFAULT_ROWS,
    seed: int = DEFAULT_SEED,
) -> RowsResult:
    """테이블당 ``n_rows``행을 합성한다.

    - 채택 관계의 자식 컬럼은 부모 테이블의 생성 행 하나에서 함께 복사한다(복합 조인 키 일관).
      한 자식 컬럼이 여러 관계에 걸리면 먼저 채운 값과 맞는 부모 행만 고른다.
    - 기본키(복합이면 묶음) · 유일 부모 컬럼 묶음(`unique_parent`가 False가 아닌 관계의 부모 쪽)은
      중복 없이 만든다. 유한 값 집합으로 유일성을 채울 수 없으면 그 테이블은 행이
      모자란다(`shortfall`).
    - NULL 허용 컬럼은 키·관계(자식·부모 쪽) 컬럼이 아니면 ``NULL_RATE`` 비율로 NULL.
    """
    rng = random.Random(seed)
    samples = code_samples or {}
    order, relations, order_warnings = _generation_order(catalog)
    child_rels: dict[str, list[Relation]] = {}
    unique_sets: dict[str, list[tuple[str, ...]]] = {n: [] for n in catalog.tables}
    ref_cols: dict[str, set[str]] = {n: set() for n in catalog.tables}
    for rel in relations:
        child_rels.setdefault(rel.child, []).append(rel)
        parent_cols = tuple(b for _, b in rel.pairs)
        ref_cols[rel.parent].update(parent_cols)
        if rel.unique_parent is not False and parent_cols not in unique_sets[rel.parent]:
            unique_sets[rel.parent].append(parent_cols)
    for name, table in catalog.tables.items():
        if table.key and table.key not in unique_sets[name]:
            unique_sets[name].insert(0, table.key)

    generated: dict[str, list[dict[str, Any]]] = {}
    sources: dict[str, int] = {}
    shortfall: dict[str, int] = {}
    for name in order:
        table = catalog.tables[name]
        rels = child_rels.get(name, [])
        fk_cols = {a for rel in rels for a, _ in rel.pairs}
        unique_cols = {c for group in unique_sets[name] for c in group}
        not_null = unique_cols | fk_cols | ref_cols[name]
        fns: dict[str, ValueFn] = {}
        for col in table.columns:
            if col.name in fk_cols:
                sources["relation"] = sources.get("relation", 0) + 1
                continue
            source, fn = _value_fn(table, col, samples)
            fns[col.name] = fn
            sources[source.split(":")[0]] = sources.get(source.split(":")[0], 0) + 1
        perms = {
            rel: rng.sample(range(len(generated[rel.parent])), len(generated[rel.parent]))
            for rel in rels
        }
        seen: dict[tuple[str, ...], set[tuple[Any, ...]]] = {g: set() for g in unique_sets[name]}
        seq = 0
        rows: list[dict[str, Any]] = []
        for i in range(n_rows):
            for attempt in range(MAX_ROW_ATTEMPTS):
                row = _make_row(
                    table, rels, generated, perms, fns, unique_cols, not_null, rng, i, attempt, seq
                )
                if row is None:
                    break
                seq += 1
                keys = {g: tuple(row[c] for c in g) for g in seen}
                if all(k not in seen[g] for g, k in keys.items()):
                    for g, k in keys.items():
                        seen[g].add(k)
                    rows.append(row)
                    break
        if len(rows) < n_rows:
            shortfall[name] = n_rows - len(rows)
        generated[name] = rows
    return RowsResult(
        rows=generated,
        sources=sources,
        shortfall=shortfall,
        warnings=[*catalog.warnings, *order_warnings],
    )


def _make_row(
    table: TableSpec,
    rels: Sequence[Relation],
    generated: Mapping[str, list[dict[str, Any]]],
    perms: Mapping[Relation, list[int]],
    fns: Mapping[str, ValueFn],
    unique_cols: set[str],
    not_null: set[str],
    rng: random.Random,
    index: int,
    attempt: int,
    seq: int,
) -> dict[str, Any] | None:
    """행 1개 — 부모 행이 없는 관계가 있으면 None(만들 수 없음)."""
    row: dict[str, Any] = {}
    for rel in rels:
        parent_rows = generated[rel.parent]
        if not parent_rows:
            return None
        fixed = {a: row[a] for a, _ in rel.pairs if a in row}
        candidates = [
            r for r in parent_rows if all(r[b] == fixed[a] for a, b in rel.pairs if a in fixed)
        ] or parent_rows
        if attempt == 0 and not fixed:
            chosen = parent_rows[perms[rel][index % len(parent_rows)]]
        else:
            chosen = rng.choice(candidates)
        for a, b in rel.pairs:
            row.setdefault(a, chosen[b])
    for col in table.columns:
        if col.name in row:
            continue
        if col.nullable and col.name not in not_null and rng.random() < NULL_RATE:
            row[col.name] = None
            continue
        fn = fns[col.name]
        row[col.name] = fn(rng, seq) if col.name in unique_cols else fn(rng, None)
    return row


def _sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    return quote_string(str(value))


def render_rows_sql(
    catalog: Catalog, result: RowsResult, source: str, *, n_rows: int, seed: int
) -> str:
    """합성 행 INSERT 전체(테이블 생성 순서 — 부모 먼저)."""
    out = [
        "-- ITAM 외부망 모의 DB — 합성 행 (plans/140 W4 · D-311 ②⑤)",
        f"-- 원천: {source} · rows={n_rows} · seed={seed}",
        "-- 생성물 — 커밋 금지(치환 코드값 포함 가능)",
        "-- 재생성: testdata/itam_closed_sim/generate.py rows",
        "",
    ]
    for name, rows in result.rows.items():
        if not rows:
            continue
        cols = [c.name for c in catalog.tables[name].columns]
        head = (
            f"INSERT INTO {quote_ident(name)} ("
            + ", ".join(quote_ident(c) for c in cols)
            + ") VALUES\n"
        )
        for start in range(0, len(rows), INSERT_CHUNK):
            chunk = rows[start : start + INSERT_CHUNK]
            values = ",\n".join(
                "(" + ", ".join(_sql_value(r[c]) for c in cols) + ")" for r in chunk
            )
            out.append(head + values + ";\n")
    return "\n".join(out)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def _write(out_dir: Path, filename: str, text: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / filename
    target.write_text(text, encoding="utf-8")
    return target


def _evidence_counts(
    catalog: Catalog, samples: Mapping[tuple[str, str], list[str]]
) -> dict[str, int]:
    cols: Iterable[ColumnSpec] = (c for t in catalog.tables.values() for c in t.columns)
    counts = {
        "key_tables": sum(1 for t in catalog.tables.values() if t.key),
        "relations": len(catalog.relations),
    }
    counts["profiled_columns"] = sum(1 for c in cols if c.profile)
    counts["code_sample_columns"] = len(samples)
    return counts


def cmd_ddl(args: argparse.Namespace) -> int:
    """``ddl`` 하위 명령 — 빈 스키마 DDL + 읽기 전용 계정 스크립트."""
    from src.schema_cache.ddl_schema_parser import parse_ddl

    out_dir = ensure_safe_out(Path(args.out))
    catalog_path, _ = resolve_export(Path(args.export))
    catalog = load_catalog(_read_yaml(catalog_path))
    problems = [p for t in catalog.tables.values() for p in check_limits(t)]
    if problems:
        print("MariaDB 생성 한도 위반 — DDL을 쓰지 않는다:", *problems, sep="\n  ", file=sys.stderr)
        return 1
    text = render_schema_sql(catalog, catalog_path.name)
    reparsed = parse_ddl(text, "mariadb")
    n_tables = len(reparsed.schema.tables)
    n_cols = sum(len(t.columns) for t in reparsed.schema.tables.values())
    expected_cols = sum(len(t.columns) for t in catalog.tables.values())
    if n_tables != len(catalog.tables) or n_cols != expected_cols or reparsed.warnings:
        print(
            f"parse_ddl 재해석 불일치: {n_tables}/{len(catalog.tables)}테이블 · "
            f"{n_cols}/{expected_cols}컬럼 · 경고 {reparsed.warnings}",
            file=sys.stderr,
        )
        return 1
    schema_path = _write(out_dir, SCHEMA_FILE, text)
    shutil.copyfile(READONLY_USER_SQL, out_dir / READONLY_FILE)
    print(f"{schema_path}: {n_tables}테이블 · {n_cols}컬럼 (parse_ddl 재해석 일치)")
    print(f"{out_dir / READONLY_FILE}: 읽기 전용 계정")
    for warning in catalog.warnings:
        print(f"경고: {warning}")
    return 0


def cmd_rows(args: argparse.Namespace) -> int:
    """``rows`` 하위 명령 — 합성 행 INSERT."""
    out_dir = ensure_safe_out(Path(args.out))
    catalog_path, sibling_samples = resolve_export(Path(args.export))
    catalog = load_catalog(_read_yaml(catalog_path))
    samples_path = Path(args.code_samples) if args.code_samples else sibling_samples
    samples = load_code_samples(_read_yaml(samples_path)) if samples_path else {}
    evidence = _evidence_counts(catalog, samples)
    if not any(evidence.values()):
        print(
            "경고: 형식·관계·코드 근거가 없다(1회차 반출?) — 값은 전부 타입 기본 합성이다",
            file=sys.stderr,
        )
    result = generate_rows(catalog, samples, n_rows=args.rows, seed=args.seed)
    text = render_rows_sql(catalog, result, catalog_path.name, n_rows=args.rows, seed=args.seed)
    path = _write(out_dir, ROWS_FILE, text)
    total = sum(len(r) for r in result.rows.values())
    print(
        f"{path}: {len(result.rows)}테이블 · {total}행 · 근거 {evidence} · 값 출처 {result.sources}"
    )
    if result.shortfall:
        print(f"행 부족(유일성 충족 불가): {result.shortfall}")
    for warning in result.warnings:
        print(f"경고: {warning}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """CLI 인자 정의."""
    parser = argparse.ArgumentParser(description="ITAM 외부망 모의 DB 생성기 (plans/140 W4)")
    sub = parser.add_subparsers(dest="command", required=True)
    ddl = sub.add_parser("ddl", help="반출 카탈로그 → 빈 스키마 DDL")
    ddl.add_argument("export", help="반출 디렉터리 또는 schema_catalog.yaml")
    ddl.add_argument("--out", default=str(DEFAULT_OUT), help="출력 디렉터리(기본 generated/)")
    ddl.set_defaults(func=cmd_ddl)
    rows = sub.add_parser("rows", help="반출 카탈로그 + code_samples → 합성 행 INSERT")
    rows.add_argument("export", help="반출 디렉터리 또는 schema_catalog.yaml")
    rows.add_argument(
        "--code-samples", default=None, help="code_samples.yaml(기본: 카탈로그 옆 파일)"
    )
    rows.add_argument(
        "--rows", type=int, default=DEFAULT_ROWS, help=f"테이블당 행 수(기본 {DEFAULT_ROWS})"
    )
    rows.add_argument(
        "--seed", type=int, default=DEFAULT_SEED, help=f"난수 seed(기본 {DEFAULT_SEED})"
    )
    rows.add_argument("--out", default=str(DEFAULT_OUT), help="출력 디렉터리(기본 generated/)")
    rows.set_defaults(func=cmd_rows)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """진입점."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (ValueError, FileNotFoundError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
