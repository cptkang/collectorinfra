"""plans/173 W1 T4 특성화 — `_read_condition`(via `extract_column_conditions`) 현재 값 골든.

Extract Function(N5) 전에 조건 읽기의 전 분기(비교·뒤집힌 비교·BETWEEN·IN·NOT·LIKE·IS·
함수로 감싼 컬럼·산술/형 변환 접미·CASE WHEN·OR 결합·컬럼 참조)를 반환 전 필드로 고정한다.
기대값은 1ad7c161 시점의 「현재 값」이다 — 리팩터 뒤 이 값이 바뀌면 리팩터가 아니다.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from src.sql_time_conditions import extract_column_conditions

_F = "SELECT a FROM t x WHERE "

#: (이름, sql) — 컬럼은 모두 `ts`.
CASES: list[tuple[str, str]] = [
    ("eq_str", _F + "x.ts = '2026-01-01'"),
    ("ge_num_and_lt", _F + "x.ts >= 20260101 AND x.ts < 20260201"),
    ("typed_literal", _F + "ts >= TIMESTAMP '2026-01-01 00:00:00'"),
    ("typed_call", _F + "ts < DATE('2026-02-01')"),
    ("cast_literal", _F + "ts = CAST('2026-01-01' AS DATE)"),
    ("pg_cast_suffix", _F + "ts = '2026-01-01'::date"),
    ("literal_then_arith", _F + "ts = '2026-01-01' + 1"),
    ("rhs_expression", _F + "ts >= CURRENT_DATE - INTERVAL '7 days'"),
    ("rhs_column", "SELECT a FROM t x JOIN p ON x.ts = p.m WHERE p.k = 1"),
    ("rhs_column_wrapped", _F + "DATE(x.ts) = p.m"),
    ("flipped_literal", _F + "'2026-01-01' <= x.ts"),
    ("flipped_column", _F + "p.m > x.ts"),
    ("flipped_expr", _F + "CURRENT_DATE - 1 DAY <= x.ts"),
    ("flipped_wrapped", _F + "'2026' = YEAR(x.ts)"),
    ("between_lit", _F + "x.ts BETWEEN '2026-01-01' AND '2026-01-31'"),
    ("between_expr", _F + "x.ts BETWEEN CURRENT_DATE - 7 AND CURRENT_DATE"),
    ("between_lo_only", _F + "x.ts BETWEEN '2026-01-01' AND CURRENT DATE"),
    ("between_wrapped", _F + "DATE(x.ts) BETWEEN '2026-01-01' AND '2026-01-31'"),
    ("in_lit", _F + "x.ts IN ('2026-01-01', '2026-01-02')"),
    ("in_nonlit", _F + "x.ts IN ('2026-01-01', CURRENT_DATE)"),
    ("in_trailing_arith", _F + "x.ts IN ('2026-01-01' || 'x')"),
    ("in_subquery", _F + "x.ts IN (SELECT m FROM p)"),
    ("in_empty", _F + "x.ts IN ()"),
    ("in_then_arith", _F + "x.ts IN ('a') + 1 = 2"),
    ("in_wrapped", _F + "DATE(x.ts) IN ('2026-01-01')"),
    ("not_in", _F + "x.ts NOT IN ('a') AND x.k = 1"),
    ("not_between", _F + "x.ts NOT BETWEEN '1' AND '2' AND x.k = 1"),
    ("not_like", _F + "x.ts NOT LIKE '2026%'"),
    ("not_other", _F + "x.ts NOT NULL"),
    ("not_at_end", _F + "x.ts NOT"),
    ("like", _F + "x.ts LIKE '2026%' AND x.k = 1"),
    ("ilike", _F + "x.ts ILIKE '2026%'"),
    ("is_null", _F + "x.ts IS NULL"),
    ("bare_at_start", "ts"),
    ("no_operator", _F + "x.ts"),
    ("func_wrapped_cmp", _F + "TO_CHAR(x.ts, 'YYYYMM') = '202601'"),
    ("nested_func", _F + "SUBSTR(TO_CHAR(x.ts, 'YYYYMMDD'), 1, 6) = '202601'"),
    ("arith_concat", _F + "x.ts || '01' = '20260101'"),
    ("arith_paren", _F + "x.ts + (1) = 2"),
    ("arith_dangling", _F + "x.ts + = 2"),
    ("pg_cast_col", _F + "x.ts::date = '2026-01-01'"),
    ("pg_cast_col_args", _F + "x.ts::varchar(8) = '20260101'"),
    ("pg_cast_col_bare", _F + "x.ts:: = 1"),
    ("case_when", "SELECT CASE WHEN x.ts = '2026-01' THEN v END FROM t x"),
    ("select_list_cmp", "SELECT x.ts = '2026' FROM t x"),
    ("subquery_filter", "SELECT * FROM (SELECT a FROM t x WHERE x.ts = '1') s WHERE s.ts > '0'"),
    ("or_joined", _F + "x.ts = '1' OR x.k = 2"),
    ("or_paren", _F + "(x.ts >= '1' AND x.ts < '2') OR x.k = 2"),
    ("or_paren_inner", _F + "x.k = 1 AND (x.ts = '1' OR x.ts = '2')"),
    ("union_scope", _F + "x.ts = '1' UNION SELECT a FROM t x WHERE x.ts = '2'"),
    ("having", "SELECT a FROM t x GROUP BY a HAVING MAX(x.ts) > '2026'"),
    ("now_function", _F + "x.ts >= NOW() - INTERVAL '1 day' AND x.ts < CURRENT_TIMESTAMP"),
    ("quoted_ident", 'SELECT a FROM t x WHERE x."TS" = \'1\''),
    ("alias_decl", "SELECT a AS ts FROM t x WHERE x.ts = '1'"),
    ("unbalanced", _F + "(x.ts = '1'"),
    ("unclosed_literal", _F + "x.ts = '1"),
    ("on_clause", "SELECT a FROM t x JOIN p ON p.k = x.k AND x.ts >= '2026' WHERE 1 = 1"),
]


def _dump(conds: list | None) -> list[dict] | None:
    return None if conds is None else [dataclasses.asdict(c) for c in conds]


#: 1ad7c161 현재 값 스냅샷(이 파일을 갱신하는 변경은 리팩터가 아니다).
_SNAP = Path(__file__).resolve().parent / "snapshots" / "plan173_t4" / "read_condition.json"
GOLDEN: dict[str, list[dict] | None] = json.loads(_SNAP.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name,sql", CASES, ids=[c[0] for c in CASES])
def test_extract_column_conditions_golden(name: str, sql: str) -> None:
    """조건 읽기의 op·values·literal·scope·text·now_functions·reason이 현재 값과 같다."""
    got = json.loads(json.dumps(_dump(extract_column_conditions(sql, "ts"))))
    assert got == GOLDEN[name]
