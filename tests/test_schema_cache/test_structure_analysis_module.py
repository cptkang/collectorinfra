"""`src/schema_cache/structure_analysis.py` 테스트 (plans/104 §3.5 A-5).

- 이동 함수: `analyze_structure`의 ok/no_patterns/failed 구분 · `generate_structure_samples`의
  unsafe/failed/empty/ok 구분
- 추가 함수: `split_fk_groups` · `merge_outcomes` · `validate_structure_draft`

LLM·DB는 목이다(과금 호출 0). 픽스처 이름은 가상(`entity_t`·`attr_t` 등)이다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain.schema_snapshot import build_snapshot
from src.schema_cache.structure_analysis import (
    StructureAnalysisOutcome,
    analyze_structure,
    generate_structure_samples,
    merge_outcomes,
    split_fk_groups,
    validate_sample_sql,
    validate_structure_draft,
)


def _llm(content: str | Exception) -> Any:
    """`ainvoke`만 가진 목 LLM."""
    llm = SimpleNamespace()
    if isinstance(content, Exception):
        llm.ainvoke = AsyncMock(side_effect=content)
    else:
        llm.ainvoke = AsyncMock(return_value=SimpleNamespace(content=content))
    return llm


def _col(
    name: str, type_: str = "integer", *, pk: bool = False, references: str | None = None
) -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "nullable": True,
        "primary_key": pk,
        "foreign_key": references is not None,
        "references": references,
    }


SCHEMA: dict[str, Any] = {
    "tables": {
        "entity_t": {
            "columns": [
                _col("id", pk=True),
                _col("label", "varchar(100)"),
                _col("parent_id"),
                _col("conf_key", "varchar(40)"),
            ]
        },
        "attr_t": {
            "columns": [
                _col("id", pk=True),
                _col("entity_ref", references="entity_t.id"),
                _col("conf_ref", "varchar(40)"),
                _col("attr_name", "varchar(50)"),
                _col("attr_value", "text"),
            ]
        },
    },
    "relationships": [{"from": "attr_t.entity_ref", "to": "entity_t.id"}],
}

EAV_PATTERN: dict[str, Any] = {
    "type": "eav",
    "entity_table": "entity_t",
    "config_table": "attr_t",
    "join_condition": "attr_t.entity_ref = entity_t.id",
    "attribute_column": "attr_name",
    "value_column": "attr_value",
    "direct_join": {"entity_column": "conf_key", "config_column": "conf_ref"},
}
HIERARCHY_PATTERN: dict[str, Any] = {
    "type": "hierarchy",
    "table": "entity_t",
    "id_column": "id",
    "parent_column": "parent_id",
}


# ──────────────────────────────────────────────
# 이동 함수
# ──────────────────────────────────────────────


class TestAnalyzeStructure:
    async def test_ok(self) -> None:
        body = json.dumps({"patterns": [EAV_PATTERN], "query_guide": "EAV 조인"})
        outcome = await analyze_structure(_llm(body), SCHEMA)
        assert outcome.status == "ok"
        assert outcome.meta == {"patterns": [EAV_PATTERN], "query_guide": "EAV 조인"}
        assert outcome.error is None

    async def test_no_patterns_is_not_failure(self) -> None:
        outcome = await analyze_structure(
            _llm('```json\n{"patterns": [], "query_guide": ""}\n```'), SCHEMA
        )
        assert outcome.status == "no_patterns"
        assert outcome.meta == {"patterns": [], "query_guide": ""}

    @pytest.mark.parametrize(
        ("content", "fragment"),
        [
            ("not json", "파싱 실패"),
            ('["a"]', "JSON 객체가 아님"),
            ('{"patterns": "x"}', "배열이 아님"),
            (RuntimeError("provider down"), "LLM 호출 실패"),
        ],
    )
    async def test_failed(self, content: str | Exception, fragment: str) -> None:
        outcome = await analyze_structure(_llm(content), SCHEMA)
        assert outcome.status == "failed"
        assert outcome.meta is None
        assert fragment in (outcome.error or "")


class TestGenerateStructureSamples:
    async def test_attempt_statuses(self) -> None:
        sqls = [
            {"purpose": "안전하지 않음", "sql": "DELETE FROM attr_t"},
            {"purpose": "실행 실패", "sql": "SELECT broken FROM attr_t LIMIT 5"},
            {"purpose": "빈 결과", "sql": "SELECT id FROM attr_t WHERE 1=0 LIMIT 5"},
            {"purpose": "성공", "sql": "SELECT id FROM entity_t LIMIT 5"},
        ]
        client = SimpleNamespace()

        async def _execute(sql: str) -> Any:
            if "broken" in sql:
                raise RuntimeError("column does not exist")
            if "1=0" in sql:
                return SimpleNamespace(rows=[])
            return SimpleNamespace(rows=[{"id": 1}, {"id": 2}])

        client.execute_sql = AsyncMock(side_effect=_execute)
        # 최대 3건만 시도한다 — 네 번째(성공)는 잘린다
        collection = await generate_structure_samples(
            _llm(json.dumps(sqls)), client, SCHEMA, {"patterns": []}
        )
        assert [a["status"] for a in collection.attempts] == ["unsafe", "failed", "empty"]
        assert collection.attempts[1]["error"] == "column does not exist"
        assert collection.samples == {}

        collection = await generate_structure_samples(
            _llm(json.dumps(sqls[3:])), client, SCHEMA, {"patterns": []}
        )
        assert [a["status"] for a in collection.attempts] == ["ok"]
        assert collection.attempts[0]["rows"] == 2
        assert collection.samples == {"성공": [{"id": 1}, {"id": 2}]}
        assert collection.error is None

    async def test_llm_failure_recorded(self) -> None:
        client = SimpleNamespace(execute_sql=AsyncMock())
        collection = await generate_structure_samples(_llm("nope"), client, SCHEMA, {})
        assert collection.attempts == []
        assert "파싱 실패" in (collection.error or "")
        client.execute_sql.assert_not_awaited()

        collection = await generate_structure_samples(_llm('{"sql": "x"}'), client, SCHEMA, {})
        assert collection.error == "샘플 SQL 생성 결과가 배열이 아님"


class TestValidateSampleSql:
    def test_rules(self) -> None:
        assert validate_sample_sql("SELECT a FROM t LIMIT 3") is True
        assert validate_sample_sql("SELECT a FROM t FETCH FIRST 3 ROWS ONLY") is True
        assert validate_sample_sql("SELECT a FROM t") is False
        assert validate_sample_sql("UPDATE t SET a = 1 LIMIT 1") is False
        assert validate_sample_sql("SELECT a FROM t; DROP TABLE t LIMIT 1") is False


# ──────────────────────────────────────────────
# split_fk_groups
# ──────────────────────────────────────────────


def _tables(*names: str) -> dict[str, Any]:
    return {name: {"columns": [_col("id")]} for name in names}


class TestSplitFkGroups:
    def test_components_kept_together_and_packed(self) -> None:
        schema = {
            "tables": _tables("a_t", "b_t", "c_t", "d_t", "e_t"),
            "relationships": [
                {"from": "b_t.a_ref", "to": "a_t.id"},
                {"from": "d_t.c_ref", "to": "c_t.id"},
            ],
        }
        # 성분 {a,b} {c,d} {e} · 상한 3 → [a,b] + {c,d}는 넘치므로 새 묶음 → [c,d,e]
        assert split_fk_groups(schema, 3) == [["a_t", "b_t"], ["c_t", "d_t", "e_t"]]
        assert split_fk_groups(schema, 5) == [["a_t", "b_t", "c_t", "d_t", "e_t"]]

    def test_oversize_component_chunked_by_name(self) -> None:
        names = [f"t{i}_t" for i in range(5)]
        schema = {
            "tables": _tables(*names, "z_t"),
            "relationships": [{"from": f"{n}.ref", "to": "t0_t.id"} for n in names[1:]],
        }
        assert split_fk_groups(schema, 2) == [["t0_t", "t1_t"], ["t2_t", "t3_t"], ["t4_t"], ["z_t"]]

    def test_column_references_and_case_prefix_insensitive_edges(self) -> None:
        schema = {
            "tables": {
                "APP.PARENT_T": {"columns": [_col("ID")]},
                "APP.CHILD_T": {"columns": [_col("PARENT_REF", references="parent_t.ID")]},
                "APP.OTHER_T": {"columns": [_col("ID")]},
            },
        }
        assert split_fk_groups(schema, 2) == [["APP.CHILD_T", "APP.PARENT_T"], ["APP.OTHER_T"]]

    def test_deterministic_regardless_of_input_order(self) -> None:
        tables = _tables("m_t", "a_t", "k_t", "b_t")
        rels = [{"from": "k_t.b_ref", "to": "b_t.id"}]
        first = split_fk_groups({"tables": tables, "relationships": rels}, 2)
        reversed_tables = dict(reversed(list(tables.items())))
        assert split_fk_groups({"tables": reversed_tables, "relationships": rels}, 2) == first
        assert first == [["a_t"], ["b_t", "k_t"], ["m_t"]]

    def test_empty_and_invalid_limit(self) -> None:
        assert split_fk_groups({"tables": {}}, 10) == []
        with pytest.raises(ValueError):
            split_fk_groups({"tables": _tables("a_t")}, 0)


# ──────────────────────────────────────────────
# merge_outcomes
# ──────────────────────────────────────────────


class TestMergeOutcomes:
    def test_dedupe_patterns_and_join_guides(self) -> None:
        same_pattern_reordered = dict(reversed(list(EAV_PATTERN.items())))
        outcomes = [
            StructureAnalysisOutcome(
                status="ok", meta={"patterns": [EAV_PATTERN], "query_guide": "가이드 A"}
            ),
            StructureAnalysisOutcome(
                status="no_patterns", meta={"patterns": [], "query_guide": ""}
            ),
            StructureAnalysisOutcome(
                status="ok",
                meta={
                    "patterns": [same_pattern_reordered, HIERARCHY_PATTERN],
                    "query_guide": "가이드 B",
                },
            ),
        ]
        merged, errors = merge_outcomes(outcomes)
        assert errors == []
        assert merged.status == "ok"
        assert merged.meta == {
            "patterns": [EAV_PATTERN, HIERARCHY_PATTERN],
            "query_guide": "가이드 A\n\n가이드 B",
        }

    def test_partial_failure_reports_errors(self) -> None:
        outcomes = [
            StructureAnalysisOutcome(status="failed", error="LLM 호출 실패: timeout"),
            StructureAnalysisOutcome(
                status="no_patterns", meta={"patterns": [], "query_guide": ""}
            ),
        ]
        merged, errors = merge_outcomes(outcomes)
        assert merged.status == "no_patterns"
        assert merged.meta == {"patterns": [], "query_guide": ""}
        assert errors == ["묶음 1: LLM 호출 실패: timeout"]

    def test_all_failed(self) -> None:
        merged, errors = merge_outcomes(
            [
                StructureAnalysisOutcome(status="failed", error="e1"),
                StructureAnalysisOutcome(status="failed"),
            ]
        )
        assert merged.status == "failed"
        assert merged.meta is None
        assert errors == ["묶음 1: e1", "묶음 2: 분석 실패(사유 없음)"]
        assert "e1" in (merged.error or "")

    def test_empty(self) -> None:
        merged, errors = merge_outcomes([])
        assert merged.status == "failed"
        assert errors == []


# ──────────────────────────────────────────────
# validate_structure_draft
# ──────────────────────────────────────────────

OK_ATTEMPTS = [
    {
        "purpose": "샘플",
        "sql": "SELECT id FROM entity_t LIMIT 5",
        "status": "ok",
        "rows": 2,
        "error": None,
    }
]


def _check(result: dict[str, Any], code: str) -> dict[str, Any]:
    return next(c for c in result["checks"] if c["code"] == code)


class TestValidateStructureDraft:
    def test_all_pass(self) -> None:
        meta = {"patterns": [EAV_PATTERN, HIERARCHY_PATTERN], "query_guide": "g"}
        result = validate_structure_draft(meta, build_snapshot(SCHEMA), OK_ATTEMPTS)
        assert result["passed"] is True
        assert [c["code"] for c in result["checks"]] == [
            "refs_exist",
            "join_types",
            "sample_sql_safe",
            "sample_exec",
        ]
        assert all(c["failures"] == [] for c in result["checks"])
        assert result["analysis_errors"] == []

    def test_refs_missing_table_and_column(self) -> None:
        meta = {
            "patterns": [
                {**EAV_PATTERN, "attribute_column": "attr_key"},
                {
                    "type": "hierarchy",
                    "table": "ghost_t",
                    "id_column": "id",
                    "parent_column": "parent_id",
                },
            ]
        }
        result = validate_structure_draft(meta, build_snapshot(SCHEMA), OK_ATTEMPTS)
        refs = _check(result, "refs_exist")
        assert result["passed"] is False
        assert refs["passed"] is False
        assert any("attr_t.attr_key" in f for f in refs["failures"])
        assert any("ghost_t" in f for f in refs["failures"])

    def test_refs_case_and_schema_prefix_insensitive(self) -> None:
        """DB2 대문자 스냅샷 ↔ 소문자·스키마 접두 구조 정보."""
        upper = {
            "tables": {
                name.upper(): {
                    "columns": [
                        {**c, "name": c["name"].upper(), "type": c["type"].upper()}
                        for c in data["columns"]
                    ]
                }
                for name, data in SCHEMA["tables"].items()
            }
        }
        meta = {"patterns": [{**EAV_PATTERN, "entity_table": "APPSCHEMA.entity_t"}]}
        result = validate_structure_draft(meta, build_snapshot(upper), OK_ATTEMPTS)
        assert _check(result, "refs_exist")["failures"] == []
        assert result["passed"] is True

    def test_join_type_family_mismatch(self) -> None:
        meta = {
            "patterns": [
                {**EAV_PATTERN, "join_condition": "attr_t.attr_name = entity_t.id"},  # 문자 ↔ 숫자
                {
                    "type": "hierarchy",
                    "table": "entity_t",
                    "id_column": "id",
                    "parent_column": "label",
                },
            ]
        }
        result = validate_structure_draft(meta, build_snapshot(SCHEMA), OK_ATTEMPTS)
        join = _check(result, "join_types")
        assert join["passed"] is False
        assert len(join["failures"]) == 2
        assert "patterns[0].join_condition" in join["failures"][0]
        assert "patterns[1].parent_column" in join["failures"][1]

    def test_join_types_compatible_within_family_and_unknown_skipped(self) -> None:
        schema = {
            "tables": {
                "a_t": {
                    "columns": [_col("k", "bigint"), _col("u", "uuid"), _col("d", "timestamp")]
                },
                "b_t": {
                    "columns": [
                        _col("k", "numeric(10,0)"),
                        _col("u", "varchar(36)"),
                        _col("d", "date"),
                    ]
                },
            }
        }
        meta = {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "a_t",
                    "config_table": "b_t",
                    "join_condition": "b_t.k = a_t.k AND b_t.u = a_t.u AND b_t.d = a_t.d",
                }
            ]
        }
        result = validate_structure_draft(meta, build_snapshot(schema), OK_ATTEMPTS)
        assert _check(result, "join_types")["failures"] == []

    def test_unsafe_sample_fails(self) -> None:
        attempts = OK_ATTEMPTS + [
            {
                "purpose": "위험",
                "sql": "DELETE FROM attr_t",
                "status": "unsafe",
                "rows": 0,
                "error": None,
            }
        ]
        result = validate_structure_draft(
            {"patterns": [EAV_PATTERN]}, build_snapshot(SCHEMA), attempts
        )
        safe = _check(result, "sample_sql_safe")
        assert safe["passed"] is False
        assert safe["failures"] == ["샘플 '위험': 읽기 전용·LIMIT 규칙 위반"]
        assert result["passed"] is False

    def test_sample_without_limit_rechecked(self) -> None:
        attempts = [
            {
                "purpose": "무제한",
                "sql": "SELECT id FROM entity_t",
                "status": "ok",
                "rows": 3,
                "error": None,
            }
        ]
        result = validate_structure_draft(
            {"patterns": [EAV_PATTERN]}, build_snapshot(SCHEMA), attempts
        )
        assert _check(result, "sample_sql_safe")["passed"] is False

    def test_sample_exec_requires_one_ok(self) -> None:
        attempts = [
            {
                "purpose": "빈",
                "sql": "SELECT id FROM attr_t LIMIT 1",
                "status": "empty",
                "rows": 0,
                "error": None,
            },
            {
                "purpose": "실패",
                "sql": "SELECT x FROM attr_t LIMIT 1",
                "status": "failed",
                "rows": 0,
                "error": "no column",
            },
        ]
        result = validate_structure_draft(
            {"patterns": [EAV_PATTERN]}, build_snapshot(SCHEMA), attempts
        )
        exec_check = _check(result, "sample_exec")
        assert exec_check["passed"] is False
        assert exec_check["failures"] == ["샘플 '빈': empty", "샘플 '실패': failed — no column"]

        result = validate_structure_draft({"patterns": [EAV_PATTERN]}, build_snapshot(SCHEMA), [])
        assert _check(result, "sample_exec")["failures"] == ["샘플 SQL 시도가 없습니다"]

    def test_no_patterns_draft_samples_not_applicable(self) -> None:
        result = validate_structure_draft(
            {"patterns": [], "query_guide": ""}, build_snapshot(SCHEMA), []
        )
        assert result["passed"] is True
        assert _check(result, "sample_sql_safe")["passed"] is True
        assert _check(result, "sample_exec")["passed"] is True

    def test_analysis_errors_block_approval(self) -> None:
        result = validate_structure_draft(
            {"patterns": [EAV_PATTERN]},
            build_snapshot(SCHEMA),
            OK_ATTEMPTS,
            analysis_errors=["묶음 2: LLM 호출 실패"],
        )
        assert all(c["passed"] for c in result["checks"])
        assert result["passed"] is False
        assert result["analysis_errors"] == ["묶음 2: LLM 호출 실패"]

    def test_patterns_not_list(self) -> None:
        result = validate_structure_draft({"patterns": "oops"}, build_snapshot(SCHEMA), [])
        assert _check(result, "refs_exist")["failures"] == ["patterns가 배열이 아님"]
        assert result["passed"] is False
