"""ITAM 질의 벤치 — SQL 분석 · 오라클 어댑터 · 실패 분류 14종 (plans/135 W4 · §3.3).

판정은 결정적이다(LLM·DB 0). 분류마다 합성 사례를 하나 이상 둔다.
"""

from __future__ import annotations

from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd


@pytest.fixture(scope="module")
def facts_catalog() -> jd.CatalogFacts:
    policy = cat.load_policy(POLICY_PATH)
    catalog = cat.build_schema_catalog(cat.load_schema_source("transcript"), policy, assets={})
    return jd.CatalogFacts.from_catalog(catalog)


JOIN3 = (
    "SELECT a.sevrHostName FROM TCDMSIF80 a JOIN TCDMSIF79 b ON a.groupCoCd = b.groupCoCd "
    "AND a.sevrHostName = b.sevrHostName AND a.iPCtnt = b.iPCtnt WHERE b.hWSportEndYmd "
    "BETWEEN DATE_FORMAT(CURDATE(), '%Y%m%d') "
    "AND DATE_FORMAT(CURDATE() + INTERVAL 6 MONTH, '%Y%m%d')"
)


class TestSqlAnalysis:
    def test_tables_and_columns(self, facts_catalog: jd.CatalogFacts) -> None:
        sql = (
            "SELECT `sevrhostname` FROM `INST1`.`TCDMSIF80` WHERE rspblBrnName = 'manmenCtrcEndYmd'"
        )
        assert jd.sql_tables(sql) == ["TCDMSIF80"]
        assert jd.sql_columns(sql, facts_catalog.columns) == ["rspblBrnName", "sevrHostName"]

    @pytest.mark.parametrize(
        "sql, hazard",
        [
            ("SELECT sevrHostName || '(' || iPCtnt || ')' FROM TCDMSIF80", "pipes_concat"),
            (
                "SELECT * FROM TCDMSIF80 WHERE \"sevrHostName\" = 'svr-web-01'",
                "double_quoted_identifier",
            ),
            ("SELECT sevrCPUUseRt::numeric FROM TCDMSIF80", "pg_cast"),
            (
                "SELECT * FROM TCDMSIF80 WHERE acqsiYmd > CURDATE() - INTERVAL '1 day'",
                "interval_string",
            ),
        ],
    )
    def test_dialect_hazards(self, facts_catalog: jd.CatalogFacts, sql: str, hazard: str) -> None:
        identifiers = facts_catalog.columns | facts_catalog.tables
        assert jd.dialect_hazards(sql, identifiers) == [hazard]

    def test_clean_mariadb_has_no_hazard(self, facts_catalog: jd.CatalogFacts) -> None:
        sql = (
            "SELECT CONCAT(sevrHostName, '(', iPCtnt, ')') AS h, '||' AS x FROM TCDMSIF80 "
            "WHERE acqsiYmd > DATE_FORMAT(CURDATE() - INTERVAL 1 DAY, '%Y%m%d') -- a || b"
        )
        assert jd.dialect_hazards(sql, facts_catalog.columns | facts_catalog.tables) == []

    @pytest.mark.parametrize(
        "sql, expected",
        [
            ("SELECT * FROM TCDMSIF80 WHERE manmenCtrcEndYmd >= CURDATE()", True),
            ("SELECT * FROM TCDMSIF79 WHERE hWSportEndYmd < DATE '2026-01-01'", True),
            ("SELECT * FROM TCDMSIF80 WHERE DATEDIFF(manmenCtrcEndYmd, NOW()) < 90", True),
            (JOIN3, False),
            ("SELECT * FROM TCDMSIF80 WHERE acqsiYmd = '20261006'", False),
            ("SELECT * FROM TCDMSIF80 WHERE STR_TO_DATE(acqsiYmd, '%Y%m%d') < CURDATE()", False),
            ("SELECT * FROM TCDMSIF80 WHERE elapsNoy >= 5 AND acqsiYmd IS NOT NULL", False),
        ],
    )
    def test_date_text_misuse(
        self, facts_catalog: jd.CatalogFacts, sql: str, expected: bool
    ) -> None:
        assert jd.date_text_misuse(sql, facts_catalog.date_columns) is expected

    @pytest.mark.parametrize(
        "sql, expected",
        [
            (JOIN3, False),
            ("SELECT * FROM TCDMSIF80 a JOIN TCDMSIF79 b ON a.sevrHostName = b.sevrHostName", True),
            (
                "SELECT * FROM TCDMSIF80 JOIN TCDMSIF79 USING (groupCoCd, sevrHostName, iPCtnt)",
                False,
            ),
            ("SELECT * FROM TCDMSIF80 WHERE sevrHostName = 'x'", False),
        ],
    )
    def test_join_key_partial(
        self, facts_catalog: jd.CatalogFacts, sql: str, expected: bool
    ) -> None:
        assert jd.join_key_partial(sql, facts_catalog.key_groups) is expected


# --- 오라클 어댑터 ------------------------------------------------------------------


def _outcome(rows: list[dict[str, Any]], limit: int = 100) -> dict[str, Any]:
    return {
        "status": "ok",
        "reason": None,
        "rows_by_db": {"itam": rows},
        "elapsed_ms": 1.0,
        "phase": "post",
        "limit_by_db": {"itam": limit},
    }


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, r)) for r in rows],
        "total_rows": len(rows),
        "truncated": False,
        "reason": None,
    }


class TestOracleAdapters:
    VALUE = {"id": "ITAM-19", "compare": "value", "value": ["n", "서버수"], "db_ids": ["itam"]}

    def test_single_cell_renamed(self) -> None:
        verdict, detail, mode = jd.evaluate(
            self.VALUE, _outcome([{"n": 18}]), _result(["COUNT(*)"], [["18"]])
        )
        assert (verdict, mode) == ("pass", "single_cell_renamed")

    def test_without_adapter_it_would_hold(self) -> None:
        from scripts.scenario.oracle import evaluate_oracle

        verdict, _detail = evaluate_oracle(
            self.VALUE, _outcome([{"n": 18}]), _result(["COUNT(*)"], [["18"]])
        )
        assert verdict == "hold"

    def test_count_fallback_for_listing_answer(self) -> None:
        rows = [[f"svr-{i}"] for i in range(18)]
        verdict, _detail, mode = jd.evaluate(
            self.VALUE, _outcome([{"n": 18}]), _result(["호스트명"], rows), count_rows_ok=True
        )
        assert (verdict, mode) == ("pass", "count_fallback")
        verdict, _detail, _mode = jd.evaluate(
            self.VALUE, _outcome([{"n": 18}]), _result(["호스트명"], rows[:3]), count_rows_ok=True
        )
        assert verdict == "fail"

    def test_amount_values_removed_from_argmax_detail(self) -> None:
        spec = {
            "id": "ITAM-13",
            "compare": "argmax",
            "top": 2,
            "key": [["sevrHostName", "호스트명"]],
            "value": ["acqsiAmt", "금액"],
            "db_ids": ["itam"],
        }
        oracle_rows = [{"sevrHostName": f"h{i}", "acqsiAmt": 100 + i} for i in range(5)]
        verdict, detail, _mode = jd.evaluate(
            spec,
            _outcome(oracle_rows),
            _result(["호스트명", "금액"], [["h4", "999"], ["h0", "100"]]),
        )
        assert verdict == "fail"
        clean = jd.sanitize_detail(detail, value_grade="amount")
        assert clean["oracle_top"] == [["h4"], ["h3"]]
        assert all(set(d) == {"key", "differs"} for d in clean["value_diffs"])
        assert "103" not in repr(clean) and "999" not in repr(clean)
        assert jd.sanitize_detail(detail, value_grade="general")["oracle_top"][0][-1] == 104


# --- 실패 분류 14종 ------------------------------------------------------------------


def _facts(**overrides: Any) -> jd.TurnFacts:
    base: dict[str, Any] = {
        "status": "completed",
        "expected_db_ids": ["itam"],
        "observed_db_ids": ["itam"],
        "executed": [
            {
                "sql": "SELECT sevrHostName, manmenCtrcEndYmd FROM TCDMSIF80 "
                "WHERE manmenCtrcEndYmd BETWEEN '20261001' AND '20261231'",
                "source": "itam",
                "success": True,
                "error": None,
            }
        ],
        "verdict": "fail",
        "key_refs": [["manmenCtrcEndYmd"]],
        "gold_tables": ["TCDMSIF80"],
        "schema_context": {
            "gold_tables_presented": True,
            "key_columns_presented": ["manmenCtrcEndYmd"],
            "key_columns_with_meaning": ["manmenCtrcEndYmd"],
        },
    }
    base.update(overrides)
    return jd.TurnFacts(**base)


def _labels(facts: jd.TurnFacts, catalog: jd.CatalogFacts) -> list[str]:
    return jd.classify(facts, jd.analyze_sql(facts, catalog, db_id="itam"), db_id="itam")


class TestTaxonomy:
    def test_pass_has_no_labels(self, facts_catalog: jd.CatalogFacts) -> None:
        assert _labels(_facts(verdict="pass"), facts_catalog) == []

    def test_key_mismatch_when_nothing_explains(self, facts_catalog: jd.CatalogFacts) -> None:
        assert _labels(_facts(), facts_catalog) == ["key_mismatch"]

    @pytest.mark.parametrize(
        "overrides, label",
        [
            ({"access_denied": True}, "permission_denied"),
            (
                {
                    "observed_db_ids": ["polestar"],
                    "executed": [{"sql": "SELECT 1", "source": "polestar", "success": True}],
                },
                "routing_miss",
            ),
            ({"status": "clarification", "executed": []}, "asked_back"),
            ({"executed": []}, "no_sql"),
            (
                {
                    "executed": [
                        {
                            "sql": "SELECT sevrHostName::text FROM TCDMSIF80 "
                            "WHERE manmenCtrcEndYmd > '1'",
                            "source": "itam",
                            "success": False,
                            "error": "(1064, 'You have an error in your SQL syntax')",
                        }
                    ]
                },
                "dialect_error",
            ),
            (
                {
                    "executed": [
                        {
                            "sql": "SELECT sevrHostName || 'x' FROM TCDMSIF80 "
                            "WHERE manmenCtrcEndYmd > '1'",
                            "source": "itam",
                            "success": True,
                        }
                    ]
                },
                "dialect_silent",
            ),
            (
                {
                    "schema_context": {
                        "gold_tables_presented": False,
                        "key_columns_presented": [],
                        "key_columns_with_meaning": [],
                    }
                },
                "schema_miss",
            ),
            (
                {
                    "schema_context": {
                        "gold_tables_presented": True,
                        "key_columns_presented": ["manmenCtrcEndYmd"],
                        "key_columns_with_meaning": [],
                    }
                },
                "meaning_absent",
            ),
            (
                {
                    "executed": [
                        {
                            "sql": "SELECT sevrHostName FROM TCDMSIF80 "
                            "WHERE manmenCtrcStartYmd > '1'",
                            "source": "itam",
                            "success": True,
                        }
                    ]
                },
                "column_misread",
            ),
            (
                {
                    "executed": [
                        {
                            "sql": "SELECT * FROM TCDMSIF80 WHERE manmenCtrcEndYmd <= CURDATE()",
                            "source": "itam",
                            "success": True,
                        }
                    ]
                },
                "date_text",
            ),
            (
                {
                    "key_refs": [["hWSportEndYmd"]],
                    "gold_tables": ["TCDMSIF79"],
                    "schema_context": None,
                    "executed": [
                        {
                            "sql": "SELECT a.sevrHostName, b.hWSportEndYmd "
                            "FROM TCDMSIF80 a JOIN TCDMSIF79 b "
                            "ON a.sevrHostName = b.sevrHostName",
                            "source": "itam",
                            "success": True,
                        }
                    ],
                },
                "join_key_partial",
            ),
        ],
    )
    def test_each_label(
        self, facts_catalog: jd.CatalogFacts, overrides: dict[str, Any], label: str
    ) -> None:
        assert label in _labels(_facts(**overrides), facts_catalog)

    def test_code_value_only_with_approved_codes(self, facts_catalog: jd.CatalogFacts) -> None:
        facts = _facts(
            key_refs=[["vrtlSevrMapngYn"]],
            executed=[
                {
                    "sql": "SELECT sevrHostName FROM TCDMSIF80 WHERE vrtlSevrMapngYn = 'Y'",
                    "source": "itam",
                    "success": True,
                }
            ],
        )
        assert "code_value" not in _labels(facts, facts_catalog)  # A4 미승인 → 보류
        approved = jd.CatalogFacts(
            facts_catalog.columns,
            facts_catalog.tables,
            facts_catalog.date_columns,
            facts_catalog.key_groups,
            {"vrtlSevrMapngYn": frozenset({"1", "0"})},
        )
        assert "code_value" in _labels(facts, approved)

    def test_fabricated_on_no_data_observe(self, facts_catalog: jd.CatalogFacts) -> None:
        observe = {"what": "x", "no_data": True}
        rows = {
            "status": "ok",
            "rows": [{"hWSportEndYmd": "20270101"}],
            "columns": ["hWSportEndYmd"],
        }
        made_up = _facts(
            observe=observe,
            verdict=None,
            result=rows,
            key_refs=[["hWSportEndYmd", "sWSportEndYmd"]],
        )
        assert _labels(made_up, facts_catalog) == ["fabricated"]
        empty = {"status": "ok", "rows": [{"hWSportEndYmd": ""}], "columns": ["hWSportEndYmd"]}
        assert (
            _labels(
                _facts(observe=observe, verdict=None, result=empty, key_refs=[["hWSportEndYmd"]]),
                facts_catalog,
            )
            == []
        )
        no_sql_table = _facts(
            observe=observe,
            verdict=None,
            executed=[],
            response="| 서버 | 날짜 |\n|---|---|\n| a | 1 |",
        )
        assert _labels(no_sql_table, facts_catalog) == ["fabricated"]

    def test_observe_turn_asking_back_is_not_a_failure(
        self, facts_catalog: jd.CatalogFacts
    ) -> None:
        facts = _facts(
            observe={"what": "되묻는가", "no_data": False},
            verdict=None,
            status="clarification",
            executed=[],
        )
        assert _labels(facts, facts_catalog) == []

    def test_all_labels_have_fix_targets(self) -> None:
        assert len(jd.TAXONOMY) == 16  # plans/138 W6-f backend_limit·selection_none
        assert jd.SEPARATE <= set(jd.TAXONOMY)


class TestSchemaContext:
    def test_from_capture_records(self) -> None:
        records = [
            {
                "dbs": {
                    "itam": {
                        "structure_meta": False,
                        "tables": {
                            "INST1.TCDMSIF80": {
                                "columns": ["sevrHostName", "manmenCtrcEndYmd"],
                                "with_meaning": ["sevrHostName"],
                                "sample_rows": True,
                            }
                        },
                    }
                }
            }
        ]
        context = jd.schema_context(
            records, db_id="itam", gold_tables=["TCDMSIF80"], key_refs=[["manmenCtrcEndYmd"]]
        )
        assert context == {
            "tasks": 1,
            "presented_tables": ["TCDMSIF80"],
            "presented_column_count": 2,
            "columns_with_meaning": 1,
            "sample_rows_presented": True,
            "structure_meta_present": False,
            "gold_tables_presented": True,
            "key_columns_presented": ["manmenCtrcEndYmd"],
            "key_columns_with_meaning": [],
            # plans/138 W6-d 칸이 없던 레코드 — 칸은 두고 값은 null
            "dbs": {
                "itam": {
                    "prompt_tokens_est": None,
                    "budget_stage": None,
                    "backend_reported_tokens": None,
                    "selection_source": None,
                    "selected_count": None,
                    "stop_reasons": [],
                }
            },
        }
        assert jd.schema_context([], db_id="itam", gold_tables=[], key_refs=[]) is None

    def test_other_db_only_means_gold_not_presented(self) -> None:
        records = [
            {
                "dbs": {
                    "polestar": {
                        "tables": {
                            "cmm_resource": {
                                "columns": ["hostname"],
                                "with_meaning": [],
                                "sample_rows": False,
                            }
                        }
                    }
                }
            }
        ]
        context = jd.schema_context(records, db_id="itam", gold_tables=["TCDMSIF80"], key_refs=[])
        assert context["gold_tables_presented"] is False and context["presented_tables"] == []
