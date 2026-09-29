"""plans/123 S-8(SQL 주석 자기 고백 수집) · S-11 1차(조건 반영 대조) 판정기 단위 테스트."""

from __future__ import annotations

from typing import Any

import pytest

from src.domain.sql_disclosure import (
    CONFESSION_TERMS,
    ConditionChange,
    condition_changes,
    extract_sql_comments,
    generator_confessions,
    strip_sql_comments,
)


def cond(field: str, op: str, value: Any) -> dict[str, Any]:
    return {"field": field, "op": op, "value": value}


def kinds(changes: list[ConditionChange]) -> list[str]:
    return [c.kind for c in changes]


# ── 주석 처리 ──────────────────────────────────────────────────────────────


class TestComments:
    def test_line_comment(self) -> None:
        sql = "SELECT hostname -- 호스트명\nFROM t -- 대상 테이블"
        assert extract_sql_comments(sql) == ["호스트명", "대상 테이블"]

    def test_block_comment_multiline(self) -> None:
        sql = "SELECT a /* 첫 줄\n   둘째 줄 */ FROM t"
        assert extract_sql_comments(sql) == ["첫 줄\n   둘째 줄"]

    def test_dash_inside_string_literal_is_not_comment(self) -> None:
        sql = "SELECT * FROM t WHERE name = 'a--b'"
        assert extract_sql_comments(sql) == []
        assert strip_sql_comments(sql) == sql

    def test_escaped_quote_inside_string_literal(self) -> None:
        sql = "SELECT * FROM t WHERE name = 'it''s -- x' -- 진짜 주석"
        assert extract_sql_comments(sql) == ["진짜 주석"]

    def test_block_open_inside_double_quoted_identifier(self) -> None:
        sql = 'SELECT "a/*b" FROM t /* 실제 주석 */'
        assert extract_sql_comments(sql) == ["실제 주석"]
        assert strip_sql_comments(sql) == 'SELECT "a/*b" FROM t  '

    def test_line_marker_inside_block_comment(self) -> None:
        assert extract_sql_comments("SELECT 1 /* a -- b */ FROM t") == ["a -- b"]

    def test_unterminated_block_comment_runs_to_end(self) -> None:
        assert extract_sql_comments("SELECT 1 /* 끝나지 않음") == ["끝나지 않음"]

    def test_empty_comment_bodies_excluded(self) -> None:
        assert extract_sql_comments("SELECT 1 --   \nFROM t /**/") == []

    def test_strip_replaces_comment_with_single_space(self) -> None:
        assert strip_sql_comments("SELECT 1--x\nFROM t/* y */WHERE a = 1") == (
            "SELECT 1 \nFROM t WHERE a = 1"
        )

    @pytest.mark.parametrize("sql", [None, ""])
    def test_empty_or_none(self, sql: str | None) -> None:
        assert extract_sql_comments(sql) == []
        assert strip_sql_comments(sql) == ""


# ── S-8 생성기 자기 고백 ──────────────────────────────────────────────────────


class TestGeneratorConfessions:
    def test_quotes_confession_verbatim(self) -> None:
        sql = "SELECT h FROM t WHERE 1=1 -- 판교존(지역 힌트는 스키마에 없으므로 무시)"
        assert generator_confessions([sql]) == ["판교존(지역 힌트는 스키마에 없으므로 무시)"]

    def test_plain_comment_is_not_confession(self) -> None:
        assert generator_confessions(["SELECT h FROM t -- 서버 목록 조회"]) == []

    @pytest.mark.parametrize("term", CONFESSION_TERMS)
    def test_every_term_detected(self, term: str) -> None:
        assert generator_confessions([f"SELECT 1 -- 조건 {term} 처리"]) == [f"조건 {term} 처리"]

    def test_block_comment_whitespace_normalized(self) -> None:
        sql = "SELECT 1 /* 기간 조건은\n    생략함 */ FROM t"
        assert generator_confessions([sql]) == ["기간 조건은 생략함"]

    def test_term_inside_string_literal_is_ignored(self) -> None:
        assert generator_confessions(["SELECT 1 FROM t WHERE note = '-- 무시'"]) == []

    def test_dedupe_across_sqls_after_normalization(self) -> None:
        sqls = ["SELECT 1 -- 조건  무시", "SELECT 2 /* 조건 무시 */", "SELECT 3 -- 단위 생략"]
        assert generator_confessions(sqls) == ["조건 무시", "단위 생략"]

    def test_max_items_in_order(self) -> None:
        sql = "SELECT 1 -- a 무시\n-- b 생략\n-- c 상충\n-- d 대신"
        assert generator_confessions([sql], max_items=2) == ["a 무시", "b 생략"]
        assert generator_confessions([sql]) == ["a 무시", "b 생략", "c 상충"]

    def test_max_len_truncates_with_ellipsis(self) -> None:
        body = "무시" + "가" * 50
        [out] = generator_confessions([f"SELECT 1 -- {body}"], max_len=20)
        assert len(out) == 20
        assert out.endswith("…")
        assert out.startswith("무시가")

    def test_none_and_empty_sqls(self) -> None:
        assert generator_confessions([None, "", "SELECT 1 -- 기간 생략"]) == ["기간 생략"]
        assert generator_confessions([]) == []


# ── S-11 1차 조건 반영 대조 ────────────────────────────────────────────────────

R3_07_CONDS = [cond("cpu", ">", 90), cond("cpu", "<", 10)]
R3_07_SQL = "SELECT hostname FROM t WHERE cpu_usage > 90 OR cpu_usage < 10 LIMIT 1000"

#: 대조군 — 정상 조건 반영. 어느 판정도 나오면 안 된다(오탐 0).
NORMAL_CASES: list[tuple[str, list[dict[str, Any]], str, str]] = [
    (
        "pg_cast_numeric",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE CAST(x AS NUMERIC) >= 80 LIMIT 1000",
        "CPU 80% 이상 서버",
    ),
    (
        "db2_decimal_fetch_first",
        [cond("cpu", ">", 90)],
        "SELECT h FROM POLESTAR.T WHERE CAST(x AS DECIMAL(10,2)) > 90 "
        "FETCH FIRST 1000 ROWS ONLY",
        "CPU 90% 초과",
    ),
    (
        "ratio_form",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE usage_ratio >= 0.80",
        "CPU 80% 이상",
    ),
    (
        "ratio_form_no_leading_zero",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE usage_ratio >= .8",
        "CPU 80% 이상",
    ),
    (
        "ratio_value_percent_sql",
        [cond("cpu", ">=", 0.8)],
        "SELECT h FROM t WHERE cpu >= 80",
        "CPU 사용률 0.8 이상",
    ),
    (
        "trailing_zero",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE cpu >= 80.00",
        "CPU 80 이상",
    ),
    (
        "decimal_string_value",
        [cond("cpu", ">", "90.5")],
        "SELECT h FROM t WHERE cpu > 90.50",
        "CPU 90.5 초과",
    ),
    (
        "percent_string_value",
        [cond("cpu", ">", "90%")],
        "SELECT h FROM t WHERE cpu > 90",
        "CPU 90% 초과",
    ),
    (
        "literal_left_side",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE 80 <= cpu",
        "CPU 80 이상",
    ),
    (
        "two_conditions_and",
        [cond("cpu", ">", 90), cond("mem", "<", 10)],
        "SELECT h FROM t WHERE cpu > 90 AND mem < 10",
        "CPU 90% 초과이고 메모리 10% 미만",
    ),
    (
        "two_conditions_or_requested",
        R3_07_CONDS,
        R3_07_SQL,
        "CPU 90% 초과 또는 10% 미만",
    ),
    (
        "nearest_pair_and",
        [cond("cpu", ">=", 90), cond("mem", ">=", 80)],
        "SELECT h FROM t WHERE (cpu >= 90 OR cpu_max >= 90) AND mem >= 80",
        "CPU 90 이상이고 메모리 80 이상",
    ),
    (
        "between_not_judged",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE cpu BETWEEN 80 AND 100",
        "CPU 80 이상",
    ),
    (
        "equality_not_judged",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE cpu = 80",
        "CPU 80 이상",
    ),
    (
        "same_direction_any_occurrence",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE cpu >= 80 OR cpu_min < 80",
        "CPU 80 이상",
    ),
    (
        "not_group_not_judged",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE NOT (cpu < 80)",
        "CPU 80 이상",
    ),
    (
        "where_one_equals_one_and",
        [cond("cpu", ">=", 80)],
        "SELECT h FROM t WHERE 1=1 AND cpu >= 80",
        "CPU 80 이상",
    ),
    (
        "unit_normalized_case",
        [cond("mem", ">=", 64)],
        "SELECT h FROM t WHERE CASE WHEN v LIKE '%TB' "
        "THEN CAST(SUBSTR(v, 1, LENGTH(v) - 2) AS NUMERIC) * 1024 "
        "ELSE CAST(SUBSTR(v, 1, LENGTH(v) - 2) AS NUMERIC) END >= 64",
        "메모리 64GB 이상",
    ),
    (
        "non_target_conditions_ignored",
        [
            cond("hostname", "=", "web01"),
            cond("os", "LIKE", "%linux%"),
            cond("zone", "IN", ["a", "b"]),
            cond("status", "!=", 0),
            cond("mem", ">=", "64GB"),
            cond("flag", ">", True),
        ],
        "SELECT h FROM t",
        "web01 리눅스",
    ),
    (
        "limit_equals_value_not_judged",
        [cond("cpu", ">=", 1000)],
        "SELECT h FROM t LIMIT 1000",
        "값 1000 이상",
    ),
    (
        "db2_fetch_first_equals_value",
        [cond("cnt", ">=", 1000)],
        "SELECT h FROM t FETCH FIRST 1000 ROWS ONLY",
        "1000건 이상",
    ),
]


class TestConditionChangesNormal:
    @pytest.mark.parametrize(
        ("conds", "sql", "raw"),
        [case[1:] for case in NORMAL_CASES],
        ids=[case[0] for case in NORMAL_CASES],
    )
    def test_no_false_positive(self, conds: list[dict[str, Any]], sql: str, raw: str) -> None:
        assert condition_changes(conds, sql, raw) == []

    def test_control_group_is_large_enough(self) -> None:
        assert len(NORMAL_CASES) >= 8

    @pytest.mark.parametrize("sql", [None, "", "   "])
    def test_empty_sql(self, sql: str | None) -> None:
        assert condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상") == []

    def test_no_conditions(self) -> None:
        assert condition_changes(None, "SELECT h FROM t", "서버 목록") == []
        assert condition_changes([], "SELECT h FROM t", "서버 목록") == []


class TestMissing:
    def test_value_changed(self) -> None:
        [change] = condition_changes(
            [cond("cpu", ">=", 80)], "SELECT h FROM t WHERE cpu >= 70", "CPU 80 이상"
        )
        assert change.kind == "missing"
        assert change.field == "cpu"
        assert change.condition == ">= 80"
        assert "cpu >= 80" in change.detail

    def test_condition_dropped(self) -> None:
        changes = condition_changes([cond("cpu", ">=", 80)], "SELECT h FROM t", "CPU 80 이상")
        assert kinds(changes) == ["missing"]

    def test_digit_boundary(self) -> None:
        """180·80.5·x80은 80이 아니다."""
        sql = "SELECT h FROM t WHERE a >= 180 AND b >= 80.5 AND x80 > 1"
        assert kinds(condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상")) == [
            "missing"
        ]

    def test_number_only_in_comment(self) -> None:
        sql = "SELECT h FROM t WHERE 1=1 -- cpu >= 80 조건 생략"
        assert kinds(condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상")) == [
            "missing"
        ]

    def test_number_only_in_string_literal_is_not_missing(self) -> None:
        """문자열 비교(EAV 값 `>= '80'` 꼴)는 누락으로 말하지 않는다 — 보수적 판정(오탐 < 미탐)."""
        sql = "SELECT h FROM t WHERE v.value_text >= '80'"
        assert condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상") == []

    def test_unit_changed_silently(self) -> None:
        """R3-11 r3 모양 — 64MB 조건이 GB 환산 값으로 바뀌었다."""
        sql = "SELECT h FROM t WHERE mem_gb >= 0.0625"
        assert kinds(condition_changes([cond("mem", ">=", 64)], sql, "메모리 64MB 이상")) == [
            "missing"
        ]


class TestReversed:
    def test_opposite_operator(self) -> None:
        [change] = condition_changes(
            [cond("cpu", ">=", 80)], "SELECT h FROM t WHERE cpu < 80", "CPU 80 이상"
        )
        assert change.kind == "reversed"
        assert change.condition == ">= 80"
        assert "'<'" in change.detail

    def test_opposite_with_literal_left(self) -> None:
        """`80 >= cpu`는 `cpu <= 80`이다."""
        [change] = condition_changes(
            [cond("cpu", ">=", 80)], "SELECT h FROM t WHERE 80 >= cpu", "CPU 80 이상"
        )
        assert change.kind == "reversed"
        assert "'<='" in change.detail

    def test_less_than_condition_reversed(self) -> None:
        changes = condition_changes(
            [cond("mem", "<", 10)], "SELECT h FROM t WHERE mem >= 10", "메모리 10% 미만"
        )
        assert kinds(changes) == ["reversed"]


class TestAndToOr:
    def test_r3_07_shape(self) -> None:
        [change] = condition_changes(R3_07_CONDS, R3_07_SQL, "CPU 90% 초과이고 10% 미만")
        assert change.kind == "and_to_or"
        assert change.field == "cpu"
        assert change.condition == "> 90 / < 10"
        assert "OR" in change.detail

    @pytest.mark.parametrize(
        "raw",
        [
            "CPU 90% 초과 또는 10% 미만",
            "CPU 90% 초과이거나 10% 미만",
            "CPU 90% 초과하거나 10% 미만",
            "CPU 90% 초과 혹은 10% 미만",
            "CPU 90% 초과 아니면 10% 미만",
            "CPU > 90 or CPU < 10",
            "CPU > 90 OR CPU < 10",
        ],
    )
    def test_declared_or_in_raw_query(self, raw: str) -> None:
        assert condition_changes(R3_07_CONDS, R3_07_SQL, raw) == []

    def test_or_inside_word_is_not_declared(self) -> None:
        raw = "monitor 기준 CPU 90% 초과이고 10% 미만"
        assert kinds(condition_changes(R3_07_CONDS, R3_07_SQL, raw)) == ["and_to_or"]

    def test_raw_query_unknown(self) -> None:
        assert condition_changes(R3_07_CONDS, R3_07_SQL, None) == []

    def test_single_condition_never(self) -> None:
        sql = "SELECT h FROM t WHERE cpu > 90 OR mem > 1"
        assert condition_changes([cond("cpu", ">", 90)], sql, "CPU 90 초과") == []

    def test_clause_boundary_not_judged(self) -> None:
        sql = (
            "SELECT h FROM t WHERE cpu > 90 OR x = 1 "
            "UNION ALL SELECT h FROM u WHERE cpu < 10"
        )
        assert condition_changes(R3_07_CONDS, sql, "CPU 90% 초과이고 10% 미만") == []


class TestNeutralized:
    @pytest.mark.parametrize(
        ("sql", "span"),
        [
            ("SELECT h FROM t WHERE cpu >= 80 OR 1=1", "OR 1=1"),
            ("SELECT h FROM t WHERE cpu >= 80 or 1 = 1", "or 1 = 1"),
            ("SELECT h FROM t WHERE cpu >= 80 OR '1'='1'", "OR '1'='1'"),
            ("SELECT h FROM t WHERE cpu >= 80 OR TRUE", "OR TRUE"),
        ],
    )
    def test_always_true(self, sql: str, span: str) -> None:
        [change] = condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상")
        assert change.kind == "neutralized"
        assert change.field == ""
        assert change.condition == span
        assert span in change.detail

    def test_without_target_conditions(self) -> None:
        changes = condition_changes(None, "SELECT h FROM t WHERE a = 'x' OR 1=1", "x 서버")
        assert kinds(changes) == ["neutralized"]

    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT h FROM t WHERE cpu >= 80 -- OR 1=1",
            "SELECT h FROM t WHERE cpu >= 80 AND note = 'OR 1=1'",
            "SELECT h FROM t WHERE cpu >= 80 OR 1=10",
        ],
    )
    def test_not_always_true(self, sql: str) -> None:
        assert condition_changes([cond("cpu", ">=", 80)], sql, "CPU 80 이상") == []


class TestCommonContract:
    def test_order_and_dedupe(self) -> None:
        conds = [cond("cpu", ">=", 80), cond("cpu", ">=", 80), cond("mem", "<", 10)]
        sql = "SELECT h FROM t WHERE mem >= 10 OR 1=1"
        changes = condition_changes(conds, sql, "CPU 80 이상이고 메모리 10 미만")
        assert kinds(changes) == ["missing", "reversed", "neutralized"]
        assert [c.field for c in changes] == ["cpu", "mem", ""]

    def test_detail_is_one_line_without_daesin(self) -> None:
        cases = [
            ([cond("cpu", ">=", 80)], "SELECT h FROM t", "CPU 80 이상"),
            ([cond("cpu", ">=", 80)], "SELECT h FROM t WHERE cpu < 80", "CPU 80 이상"),
            (R3_07_CONDS, R3_07_SQL, "CPU 90% 초과이고 10% 미만"),
            (None, "SELECT h FROM t WHERE a = 1 OR 1=1", "a"),
        ]
        details = [c.detail for args in cases for c in condition_changes(*args)]
        assert len(details) == 4
        for detail in details:
            assert "대신" not in detail
            assert "\n" not in detail
