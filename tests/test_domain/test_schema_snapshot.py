"""스키마 스냅샷 · diff · 구조 영향 순수 함수 테스트 (plans/104 A-3).

픽스처 이름은 가상(`entity_t`·`attr_t` 등)이다 — 특정 운영 스키마를 가정하지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

from src.domain.schema_snapshot import (
    build_snapshot,
    diff_snapshots,
    normalize_type,
    parse_join_pairs,
    structure_impact,
    structure_refs,
)


def _col(
    name: str,
    type_: str = "integer",
    *,
    nullable: bool = True,
    pk: bool = False,
    references: str | None = None,
) -> dict[str, Any]:
    """캐시 모양 컬럼 항목."""
    return {
        "name": name,
        "type": type_,
        "nullable": nullable,
        "primary_key": pk,
        "foreign_key": references is not None,
        "references": references,
    }


def _schema(
    tables: dict[str, list[dict[str, Any]]], relationships: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "tables": {name: {"columns": cols} for name, cols in tables.items()},
        "relationships": relationships or [],
    }


BASE_TABLES: dict[str, list[dict[str, Any]]] = {
    "entity_t": [
        _col("id", pk=True, nullable=False),
        _col("label", "varchar(100)"),
        _col("parent_id"),
    ],
    "attr_t": [
        _col("id", pk=True, nullable=False),
        _col("entity_ref", references="entity_t.id"),
        _col("attr_name", "varchar(50)"),
        _col("attr_value", "text"),
    ],
}

EAV_META: dict[str, Any] = {
    "patterns": [
        {
            "type": "eav",
            "entity_table": "entity_t",
            "config_table": "attr_t",
            "join_condition": "attr_t.entity_ref = entity_t.id",
            "attribute_column": "attr_name",
            "value_column": "attr_value",
            "value_joins": [
                {
                    "eav_attribute": "Label",
                    "eav_value_column": "attr_value",
                    "entity_column": "label",
                },
            ],
        },
        {
            "type": "hierarchy",
            "table": "entity_t",
            "id_column": "id",
            "parent_column": "parent_id",
        },
    ],
    "query_guide": "가이드",
}


class TestNormalizeType:
    def test_case_and_whitespace(self) -> None:
        assert normalize_type("  CHARACTER   VARYING ") == "character varying"
        assert normalize_type("VARCHAR(20)") == "varchar(20)"
        assert normalize_type("") == ""


class TestBuildSnapshot:
    def test_shape_and_json_serializable(self) -> None:
        snap = build_snapshot(_schema(BASE_TABLES))
        assert snap["table_count"] == 2
        entity = snap["tables"]["entity_t"]
        assert entity["columns"]["id"] == {
            "type": "integer",
            "nullable": False,
            "primary_key": True,
        }
        assert snap["tables"]["attr_t"]["foreign_keys"] == ["entity_ref->entity_t.id"]
        assert len(snap["hash"]) == 64
        json.dumps(snap)  # 직렬화 가능

    def test_column_order_does_not_change_hash(self) -> None:
        reordered = {name: list(reversed(cols)) for name, cols in BASE_TABLES.items()}
        a = build_snapshot(_schema(BASE_TABLES))
        b = build_snapshot(_schema(reordered))
        assert a["hash"] == b["hash"]
        assert diff_snapshots(a, b)["has_changes"] is False

    def test_pg_schema_prefixed_keys(self) -> None:
        """PG 비 public 스키마: `schema.table` 키 · 소문자 타입 · 스키마 접두에서 schema 추정."""
        snap = build_snapshot(
            _schema(
                {
                    "app.entity_t": [_col("id", "integer", pk=True, nullable=False)],
                    "app.attr_t": [_col("entity_ref", "integer", references="entity_t.id")],
                },
                relationships=[{"from": "app.attr_t.entity_ref", "to": "app.entity_t.id"}],
            )
        )
        assert snap["tables"]["app.entity_t"]["schema"] == "app"
        # 관계(접두 표기)와 컬럼 references(bare 표기)는 같은 FK 1건으로 합쳐진다
        assert snap["tables"]["app.attr_t"]["foreign_keys"] == ["entity_ref->entity_t.id"]

    def test_db2_uppercase_bare_with_table_schemas(self) -> None:
        """DB2: 대문자 bare 키 · 대문자 타입 · YES/NO 표기 · 스키마는 별도 전달."""
        raw = {
            "tables": {
                "ENTITY_T": {
                    "columns": [
                        {
                            "column_name": "ID",
                            "data_type": "INTEGER",
                            "is_nullable": "NO",
                            "is_primary_key": True,
                        },
                        {"column_name": "LABEL", "data_type": "VARCHAR", "is_nullable": "YES"},
                    ]
                },
            },
        }
        snap = build_snapshot(raw, table_schemas={"ENTITY_T": "APPSCHEMA"})
        table = snap["tables"]["ENTITY_T"]
        assert table["schema"] == "APPSCHEMA"
        assert table["columns"]["ID"] == {"type": "integer", "nullable": False, "primary_key": True}
        assert table["columns"]["LABEL"]["nullable"] is True

    def test_mariadb_camelcase_preserved(self) -> None:
        """MariaDB: camelCase 키는 원형 보존(대소문자 구분 엔진)."""
        snap = build_snapshot(_schema({"assetMaster": [_col("assetId", "int", pk=True)]}))
        assert "assetMaster" in snap["tables"]
        assert "assetId" in snap["tables"]["assetMaster"]["columns"]
        assert snap["tables"]["assetMaster"]["schema"] is None

    def test_schema_passing_does_not_change_hash(self) -> None:
        a = build_snapshot(_schema(BASE_TABLES))
        b = build_snapshot(
            _schema(BASE_TABLES), table_schemas={"entity_t": "public", "attr_t": "public"}
        )
        assert a["hash"] == b["hash"]


class TestDiffSnapshots:
    def _snap(self, tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return build_snapshot(_schema(tables))

    def test_baseline_when_old_missing(self) -> None:
        diff = diff_snapshots(None, self._snap(BASE_TABLES))
        assert diff["baseline"] is True
        assert diff["has_changes"] is False
        assert diff["summary"]["total"] == 0

    def test_same_count_column_rename_detected(self) -> None:
        renamed = dict(BASE_TABLES)
        renamed["attr_t"] = [
            _col("id", pk=True, nullable=False),
            _col("entity_ref", references="entity_t.id"),
            _col("attr_key", "varchar(50)"),  # attr_name → attr_key (개수 동일)
            _col("attr_value", "text"),
        ]
        diff = diff_snapshots(self._snap(BASE_TABLES), self._snap(renamed))
        assert diff["columns_added"] == [
            {"table": "attr_t", "column": "attr_key", "type": "varchar(50)"}
        ]
        assert diff["columns_removed"] == [
            {"table": "attr_t", "column": "attr_name", "type": "varchar(50)"}
        ]
        assert diff["changed_tables"] == ["attr_t"]
        assert diff["has_changes"] is True

    def test_type_change_detected(self) -> None:
        changed = dict(BASE_TABLES)
        changed["entity_t"] = [
            _col("id", "bigint", pk=True, nullable=False),
            _col("label", "varchar(100)"),
            _col("parent_id"),
        ]
        diff = diff_snapshots(self._snap(BASE_TABLES), self._snap(changed))
        assert diff["type_changed"] == [
            {"table": "entity_t", "column": "id", "old": "integer", "new": "bigint"}
        ]
        assert diff["summary"]["type_changed"] == 1

    def test_table_added_and_removed(self) -> None:
        new_tables = {"entity_t": BASE_TABLES["entity_t"], "log_t": [_col("id")]}
        diff = diff_snapshots(self._snap(BASE_TABLES), self._snap(new_tables))
        assert diff["tables_added"] == ["log_t"]
        assert diff["tables_removed"] == ["attr_t"]
        assert diff["changed_tables"] == ["attr_t", "log_t"]
        assert diff["summary"]["total"] == 2

    def test_nullable_pk_fk_changes(self) -> None:
        changed = {
            "entity_t": [
                _col("id", pk=True, nullable=False),
                _col("label", "varchar(100)", nullable=False),  # NULL 변경
                _col("parent_id", pk=True),  # PK 변경
            ],
            "attr_t": [
                _col("id", pk=True, nullable=False),
                _col("entity_ref"),  # FK 제거
                _col("attr_name", "varchar(50)"),
                _col("attr_value", "text"),
            ],
        }
        diff = diff_snapshots(self._snap(BASE_TABLES), self._snap(changed))
        assert diff["nullable_changed"] == [
            {"table": "entity_t", "column": "label", "old": True, "new": False}
        ]
        assert diff["pk_changed"] == [
            {"table": "entity_t", "column": "parent_id", "old": False, "new": True}
        ]
        assert diff["fk_changed"] == [
            {"table": "attr_t", "added": [], "removed": ["entity_ref->entity_t.id"]}
        ]
        assert diff["summary"]["fk_changed"] == 1

    def test_column_order_only_is_no_change(self) -> None:
        reordered = {name: list(reversed(cols)) for name, cols in BASE_TABLES.items()}
        diff = diff_snapshots(self._snap(BASE_TABLES), self._snap(reordered))
        assert diff["has_changes"] is False
        assert diff["changed_tables"] == []

    def test_type_notation_only_is_no_change(self) -> None:
        upper = {
            name: [{**c, "type": str(c["type"]).upper()} for c in cols]
            for name, cols in BASE_TABLES.items()
        }
        assert diff_snapshots(self._snap(BASE_TABLES), self._snap(upper))["has_changes"] is False

    def test_relationship_notation_change_is_no_fk_change(self) -> None:
        """관계 표기만 bare → 스키마 접두로 바뀌어도 FK 변경이 아니다(B-2 전후 캐시 대조)."""
        old = build_snapshot(
            _schema(
                {"app.a_t": [_col("b_ref")], "app.b_t": [_col("id")]},
                relationships=[{"from": "app.a_t.b_ref", "to": "b_t.id"}],
            )
        )
        new = build_snapshot(
            _schema(
                {"app.a_t": [_col("b_ref")], "app.b_t": [_col("id")]},
                relationships=[{"from": "app.a_t.b_ref", "to": "app.b_t.id"}],
            )
        )
        assert diff_snapshots(old, new)["has_changes"] is False


class TestStructureRefs:
    def test_eav_and_hierarchy_refs(self) -> None:
        refs = {(r["table"], r["column"], r["path"]) for r in structure_refs(EAV_META)}
        assert ("entity_t", None, "patterns[0].entity_table") in refs
        assert ("attr_t", None, "patterns[0].config_table") in refs
        assert ("attr_t", "attr_name", "patterns[0].attribute_column") in refs
        assert ("attr_t", "attr_value", "patterns[0].value_column") in refs
        assert ("attr_t", "entity_ref", "patterns[0].join_condition") in refs
        assert ("entity_t", "id", "patterns[0].join_condition") in refs
        assert ("attr_t", "attr_value", "patterns[0].value_joins[0].eav_value_column") in refs
        assert ("entity_t", "label", "patterns[0].value_joins[0].entity_column") in refs
        assert ("entity_t", None, "patterns[1].table") in refs
        assert ("entity_t", "parent_id", "patterns[1].parent_column") in refs

    def test_lob_direct_join_entity_columns(self) -> None:
        meta = {
            "patterns": [
                {
                    "type": "eav",
                    "entity_table": "entity_t",
                    "config_table": "attr_t",
                    "lob_value_column": "attr_blob",
                    "lob_flag_column": "is_blob",
                    "direct_join": {"entity_column": "conf_key", "config_column": "conf_ref"},
                    "entity_columns": [{"name": "label"}, "code"],
                }
            ]
        }
        refs = {(r["table"], r["column"]) for r in structure_refs(meta)}
        assert {
            ("attr_t", "attr_blob"),
            ("attr_t", "is_blob"),
            ("entity_t", "conf_key"),
            ("attr_t", "conf_ref"),
            ("entity_t", "label"),
            ("entity_t", "code"),
        } <= refs

    def test_empty_or_missing(self) -> None:
        assert structure_refs(None) == []
        assert structure_refs({"patterns": []}) == []
        assert structure_refs({"patterns": "x"}) == []

    def test_parse_join_pairs_quotes_and_and(self) -> None:
        pairs = parse_join_pairs('"attr_t"."entity_ref" = "entity_t"."id" AND s.a_t.x = s.b_t.y')
        assert pairs == [
            (("attr_t", "entity_ref"), ("entity_t", "id")),
            (("s.a_t", "x"), ("s.b_t", "y")),
        ]


class TestStructureImpact:
    def _diff(self, new_tables: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        return diff_snapshots(
            build_snapshot(_schema(BASE_TABLES)), build_snapshot(_schema(new_tables))
        )

    def test_referenced_column_rename_requires_reanalysis(self) -> None:
        renamed = dict(BASE_TABLES)
        renamed["attr_t"] = [
            _col("id", pk=True, nullable=False),
            _col("entity_ref", references="entity_t.id"),
            _col("attr_key", "varchar(50)"),
            _col("attr_value", "text"),
        ]
        impact = structure_impact(self._diff(renamed), EAV_META)
        assert impact["reanalysis_required"] is True
        changes = {(h["column"], h["change"]) for h in impact["hits"]}
        assert ("attr_name", "column_removed") in changes
        assert (None, "table_changed") in changes  # config_table 참조

    def test_referenced_table_removed(self) -> None:
        impact = structure_impact(self._diff({"entity_t": BASE_TABLES["entity_t"]}), EAV_META)
        assert impact["reanalysis_required"] is True
        assert all(h["table"] == "attr_t" for h in impact["hits"])
        assert {h["change"] for h in impact["hits"]} == {"table_removed"}

    def test_hierarchy_column_type_change(self) -> None:
        changed = dict(BASE_TABLES)
        changed["entity_t"] = [
            _col("id", pk=True, nullable=False),
            _col("label", "varchar(100)"),
            _col("parent_id", "varchar(20)"),
        ]
        impact = structure_impact(self._diff(changed), EAV_META)
        assert {
            "table": "entity_t",
            "column": "parent_id",
            "path": "patterns[1].parent_column",
            "change": "type_changed",
        } in impact["hits"]

    def test_unreferenced_change_does_not_require(self) -> None:
        tables = dict(BASE_TABLES)
        tables["other_t"] = [_col("id")]
        base = build_snapshot(_schema(tables))
        changed = dict(tables)
        changed["other_t"] = [_col("id", "bigint")]
        diff = diff_snapshots(base, build_snapshot(_schema(changed)))
        assert diff["has_changes"] is True
        assert structure_impact(diff, EAV_META) == {"reanalysis_required": False, "hits": []}

    def test_no_meta_or_no_diff(self) -> None:
        diff = self._diff(BASE_TABLES)
        assert structure_impact(diff, EAV_META)["reanalysis_required"] is False
        assert structure_impact(diff, None)["reanalysis_required"] is False

    def test_case_and_schema_prefix_insensitive_matching(self) -> None:
        """DB2 대문자 스냅샷 ↔ 소문자·스키마 접두 구조 정보도 매칭된다."""
        old = build_snapshot(
            {
                "tables": {
                    "ATTR_T": {
                        "columns": [
                            {"column_name": "ATTR_NAME", "data_type": "VARCHAR"},
                        ]
                    }
                }
            }
        )
        new = build_snapshot(
            {
                "tables": {
                    "ATTR_T": {
                        "columns": [
                            {"column_name": "ATTR_KEY", "data_type": "VARCHAR"},
                        ]
                    }
                }
            }
        )
        meta = {
            "patterns": [
                {"type": "eav", "config_table": "APPSCHEMA.attr_t", "attribute_column": "attr_name"}
            ]
        }
        impact = structure_impact(diff_snapshots(old, new), meta)
        assert impact["reanalysis_required"] is True
        assert {
            "table": "APPSCHEMA.attr_t",
            "column": "attr_name",
            "path": "patterns[0].attribute_column",
            "change": "column_removed",
        } in impact["hits"]
