"""SQL conjunct 수술 + COUNT 프로브 조립 (Plan 82 W8-T4 · SPEC-empty-answer-diagnosis).

문자열 in / 문자열 out이라 **DB 없이 전량 검증**한다(D-127 · DBHub 미가동).
배관(BETWEEN·IS NULL·문자열 등호)을 사용자 조건으로 오인하면 무의미한 수가 나오므로
그 경계를 집중적으로 단언한다.
"""

from __future__ import annotations

import pytest

from src.nodes.condition_probe import (
    build_probe_sqls,
    split_user_conditions,
    truncated_stage_count,
)

BASE_SQL = """SELECT svr.name AS server_name, r.name AS fs, ROUND(MAX(s.max_val), 2) AS util
  FROM res r
  JOIN res svr ON r.parent_id = svr.id
  JOIN metric_stat s ON r.id = s.res_id
 WHERE r.res_type = 'FileSystems'
   AND s.def_name = 'Utilization'
   AND s.max_val BETWEEN 0 AND 1000
   AND r.dtime IS NULL
 GROUP BY svr.name, r.name
HAVING MAX(s.avg_val) >= 80
   AND MAX(s.max_val) >= 80
 ORDER BY util DESC
 LIMIT 100"""


class TestSplitUserConditions:
    def test_only_numeric_comparisons_are_user_conditions(self):
        conds = split_user_conditions(BASE_SQL)

        assert [c.text for c in conds] == [
            "MAX(s.avg_val) >= 80",
            "MAX(s.max_val) >= 80",
        ]
        assert all(c.clause == "HAVING" for c in conds)

    def test_plumbing_is_never_a_user_condition(self):
        texts = [c.text for c in split_user_conditions(BASE_SQL)]

        assert not any("BETWEEN" in t for t in texts), "값 타당성 가드는 배관이다"
        assert not any("IS NULL" in t for t in texts)
        assert not any("res_type" in t for t in texts), "문자열 등호는 배관이다"

    def test_between_and_is_not_a_conjunct_separator(self):
        """`BETWEEN a AND b`의 AND를 분리자로 세면 가드가 두 조각으로 찢어진다."""
        sql = "SELECT 1 FROM t WHERE a BETWEEN 0 AND 1000 AND b >= 80"

        conds = split_user_conditions(sql)

        assert [c.text for c in conds] == ["b >= 80"]

    def test_subquery_interior_is_untouched(self):
        sql = (
            "SELECT 1 FROM t WHERE id IN (SELECT id FROM u WHERE u.val >= 99) "
            "AND t.val >= 80"
        )

        conds = split_user_conditions(sql)

        assert [c.text for c in conds] == ["t.val >= 80"]

    def test_keywords_inside_string_literals_are_ignored(self):
        sql = "SELECT 1 FROM t WHERE t.name = 'AND HAVING x >= 80' AND t.val >= 80"

        conds = split_user_conditions(sql)

        assert [c.text for c in conds] == ["t.val >= 80"]

    def test_top_level_or_clause_is_not_split(self):
        """`A OR B AND C`는 conjunct 하나를 떼면 의미가 달라진다 — 포기가 맞다."""
        sql = "SELECT 1 FROM t WHERE t.a >= 80 OR t.b >= 90"

        assert split_user_conditions(sql) == []

    def test_value_columns_injection_narrows_the_whitelist(self):
        conds = split_user_conditions(BASE_SQL, value_columns=["avg_val"])

        assert [c.text for c in conds] == ["MAX(s.avg_val) >= 80"]


class TestBuildProbeSqls:
    def test_cumulative_prefixes(self):
        probes = build_probe_sqls(BASE_SQL, 5)

        assert [p.stage_index for p in probes] == [0, 1]
        assert "HAVING" not in probes[0].sql, "0단계는 사용자 조건이 전부 빠진다"
        assert "MAX(s.avg_val) >= 80" in probes[1].sql
        assert "MAX(s.max_val) >= 80" not in probes[1].sql
        assert probes[1].condition == "MAX(s.avg_val) >= 80"

    def test_plumbing_survives_every_stage(self):
        for probe in build_probe_sqls(BASE_SQL, 5):
            assert "BETWEEN 0 AND 1000" in probe.sql
            assert "r.dtime IS NULL" in probe.sql
            assert "res_type = 'FileSystems'" in probe.sql

    def test_group_by_is_preserved(self):
        """파일시스템 단위 행을 유지해야 잔존 건수가 사용자가 본 목록과 같은 단위다(U19)."""
        assert "GROUP BY svr.name, r.name" in build_probe_sqls(BASE_SQL, 5)[0].sql

    def test_count_wrapping_strips_order_and_limit(self):
        sql = build_probe_sqls(BASE_SQL, 5)[0].sql

        assert sql.startswith("SELECT COUNT(*) FROM (")
        assert "ORDER BY" not in sql, "행 제한이 남으면 잔존 건수가 상한에 눌린다"
        assert "LIMIT" not in sql
        assert sql.rstrip().endswith("probe_src"), "DB2는 별칭 없는 파생 테이블을 거부한다"

    def test_db2_row_limit_is_also_stripped(self):
        sql = (
            "SELECT a FROM t WHERE t.x = 'k' AND t.val >= 80 "
            "ORDER BY a FETCH FIRST 100 ROWS ONLY"
        )

        probe = build_probe_sqls(sql, 5)[0].sql

        assert "FETCH FIRST" not in probe
        assert "ORDER BY" not in probe

    def test_trailing_semicolon_removed(self):
        probe = build_probe_sqls("SELECT a FROM t WHERE t.val >= 80;", 5)[0].sql

        assert ";" not in probe

    def test_no_user_condition_yields_no_probe(self):
        """식별된 사용자 조건이 0개면 프로브를 **돌리지 않는다** — 사유는 호출부가 남긴다."""
        sql = "SELECT a FROM t WHERE t.name = 'x' AND t.dtime IS NULL"

        assert build_probe_sqls(sql, 5) == []

    def test_k_max_truncates_and_is_reportable(self):
        sql = (
            "SELECT a FROM t WHERE t.a >= 1 AND t.b >= 2 AND t.c >= 3 "
            "AND t.d >= 4 AND t.e >= 5 AND t.f >= 6"
        )

        probes = build_probe_sqls(sql, 3)

        assert [p.stage_index for p in probes] == [0, 1, 2]
        assert truncated_stage_count(sql, 3) == 3
        assert truncated_stage_count(sql, 10) == 0

    def test_k_max_zero_produces_nothing(self):
        assert build_probe_sqls(BASE_SQL, 0) == []
