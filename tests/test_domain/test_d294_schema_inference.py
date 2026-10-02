"""D-294 W1 — `src/domain/schema_inference.py` (관계 추론 · 값 형식 · 코드 · 주석 · 식별 키 · 규칙).

관계 추론은 `scripts/itam_erd.py`(단독 실행 설계라 `src`를 import하지 않는다)와 같은 규칙이어야 한다
—
스크립트 테스트 픽스처로 두 구현의 결과 동등성을 고정한다.
"""

from __future__ import annotations

import pytest

from src.domain.schema_inference import (
    TableShape,
    classify_values,
    code_value_labels,
    comment_label,
    entity_key_kind,
    infer_relations,
    is_code_candidate,
    parse_comment_enum,
    query_rules_for_column,
    referenced_identifiers,
    shapes_from_snapshot,
    table_family,
)
from src.domain.schema_snapshot import build_snapshot
from tests.test_scripts.test_itam_erd import _raw, itam_erd


def _shape(columns: list[str], pk: list[str], fks=()) -> TableShape:
    return TableShape(columns=columns, primary_key=pk, foreign_keys=list(fks))


def _pairs(relations, origin=None) -> set[tuple[str, str]]:
    return {(r.child, r.parent) for r in relations if origin is None or r.origin == origin}


class TestRelations:
    def test_pk_match_inference_rules(self):
        tables = {
            "t_grp": _shape(["grp", "nm"], ["grp"]),            # 공통 컬럼 단독 PK — 부모 아님
            "t_asset": _shape(["grp", "asset_no"], ["grp", "asset_no"]),
            "t_part": _shape(["grp", "asset_no", "seq"], ["grp", "asset_no", "seq"]),
            "t_hist": _shape(["grp", "asset_no", "seq", "h"], ["grp", "asset_no", "seq", "h"]),
            "t_a1": _shape(["grp", "k", "v"], ["grp", "k"]),
            "t_a2": _shape(["grp", "k", "w"], ["grp", "k"]),     # t_a1과 같은 기본키 → 군
        }

        relations, groups = infer_relations(tables)

        assert _pairs(relations, "inferred") == {
            ("t_part", "t_asset"),
            ("t_hist", "t_part"),  # t_asset 기본키는 t_part 기본키의 진부분집합이라 빠진다
        }
        assert groups == [["t_a1", "t_a2"]]
        rel = next(r for r in relations if r.child == "t_part")
        assert rel.child_columns == ("grp", "asset_no")
        assert rel.parent_columns == ("grp", "asset_no")

    def test_declared_fk_suppresses_inference_and_keeps_case(self):
        tables = {
            "t_p": _shape(["Grp", "Id"], ["Grp", "Id"]),
            "t_c": _shape(
                ["grp", "id", "x"], ["grp", "id", "x"], [(("grp", "id"), "t_p", ("Grp", "Id"))]
            ),
        }

        relations, _ = infer_relations(tables)

        assert [(r.child, r.parent, r.origin) for r in relations] == [("t_c", "t_p", "declared")]

    def test_inferred_child_columns_use_child_spelling(self):
        tables = {
            "t_p": _shape(["Grp", "Id"], ["Grp", "Id"]),
            "t_c": _shape(["grp", "id", "x"], ["grp", "id", "x"]),
            "t_o1": _shape(["other1"], []),
            "t_o2": _shape(["other2"], []),
        }

        (rel,), _ = infer_relations(tables)

        assert rel.child_columns == ("grp", "id") and rel.parent_columns == ("Grp", "Id")

    @pytest.mark.parametrize("ratio", [0.5, 1.0, 0.2])
    def test_same_result_as_standalone_script(self, ratio):
        """스크립트(`scripts/itam_erd.py`) 구현과 관계·군이 같다 — 규칙 표류 방지."""
        snapshot = itam_erd.build_snapshot(_raw(), "INST1")
        script_relations, script_groups = itam_erd.infer_relations(snapshot, common_ratio=ratio)
        shapes = {
            t["name"]: TableShape(
                columns=[c["name"] for c in t["columns"]],
                primary_key=list(t["primary_key"]),
                foreign_keys=[
                    (tuple(fk["columns"]), fk["ref_table"], tuple(fk["ref_columns"]))
                    for fk in t["foreign_keys"]
                ],
            )
            for t in snapshot["tables"]
        }

        relations, groups = infer_relations(shapes, common_ratio=ratio)

        assert {(r.child, r.parent, r.origin) for r in relations} == {
            (r.child, r.parent, r.kind) for r in script_relations
        }
        assert groups == script_groups

    def test_small_table_count_skips_common_column_rule(self):
        """테이블이 몇 개뿐이면 공유 컬럼이 전부 「공통」이 된다 — 하한 미만이면 그 규칙을 끈다."""
        tables = {
            "t_a": _shape(["grp", "host", "ip", "x"], ["grp", "host", "ip"]),
            "t_b": _shape(["grp", "host", "ip", "y"], ["grp", "host", "ip"]),
        }
        assert infer_relations(tables) == ([], [])  # 스크립트와 같은 기본 동작
        _, groups = infer_relations(tables, min_tables_for_common=5)
        assert groups == [["t_a", "t_b"]]

    def test_shapes_from_snapshot(self):
        snapshot = build_snapshot({
            "tables": {
                "t_p": {"columns": [{"name": "id", "type": "int", "primary_key": True}]},
                "t_c": {"columns": [
                    {"name": "id", "type": "int", "primary_key": True},
                    {"name": "p_id", "type": "int", "references": "t_p.id"},
                ]},
            },
            "relationships": [],
        })

        shapes = shapes_from_snapshot(snapshot)

        assert shapes["t_c"].primary_key == ["id"]
        assert shapes["t_c"].foreign_keys == [(("p_id",), "t_p", ("id",))]

    def test_table_family(self):
        assert table_family("ABCIF80") == "ABCIF"
        assert table_family("t_node") == "t_node"


class TestValueProfile:
    def test_formats(self):
        assert classify_values(["20260901", "20261231", None, ""]).date8 == 1.0
        assert classify_values(["20260901123000"]).datetime14 == 1.0
        assert classify_values(["10.0.0.1", "10.0.0.2, 10.0.0.3", "x"]).ipv4 == pytest.approx(2 / 3)
        hosts = classify_values(["web01", "DB-02.example.com", "12345"])
        assert hosts.hostname == pytest.approx(2 / 3)  # 숫자뿐인 값은 호스트명이 아니다

    def test_flag_mixed_case_multi_value(self):
        assert classify_values(["Y", "n", "N"]).flag == ("N", "Y")
        assert classify_values(["web01", "WEB01"]).mixed_case is True
        assert classify_values(["a,b", "c"]).multi_value == 0.5
        assert classify_values([]).total == 0


class TestCodes:
    @pytest.mark.parametrize(
        ("name", "dtype", "comment", "expected"),
        [
            ("useYn", "char", None, True),
            ("statusCd", "varchar", None, True),
            ("asset_type", "varchar", None, True),
            ("memo", "varchar", "처리 상태", True),
            ("memo", "varchar", "비고", False),
            ("statusCd", "timestamp", None, False),
        ],
    )
    def test_code_candidate(self, name, dtype, comment, expected):
        assert is_code_candidate(name, dtype, comment) is expected

    def test_comment_enum_and_label(self):
        assert parse_comment_enum("상태(1:정상, 2:장애, 3:점검)") == {
            "1": "정상", "2": "장애", "3": "점검",
        }
        assert parse_comment_enum("사용여부 Y=사용/N=미사용") == {"Y": "사용", "N": "미사용"}
        assert parse_comment_enum("시각 12:30") == {}  # 1쌍뿐이면 열거가 아니다
        assert comment_label("서버 호스트명(FQDN 포함)") == "서버 호스트명"
        assert comment_label("x" * 30) is None

    def test_code_value_labels_prefer_comment(self):
        labels = code_value_labels(["1", "2", "9"], {"1": "정상"}, {"1": "무시", "2": "장애"})
        assert labels == {"1": "정상", "2": "장애"}


class TestEntityKeysAndRules:
    def test_entity_key_kind(self):
        ips = classify_values(["10.0.0.1", "10.0.0.2,10.0.0.3"])
        hosts = classify_values(["web01", "db02.example.com"])
        assert entity_key_kind("iPCtnt", None, ips) == "ip"
        assert entity_key_kind("sevrHostName", None, hosts) == "hostname"
        assert entity_key_kind("memo", "비고", hosts) is None  # 이름·주석 단서 없음
        assert entity_key_kind("srvIp", None, hosts) is None  # IP 단서뿐인데 값이 IP 형식이 아님

    def test_query_rules(self):
        rules = query_rules_for_column("t_a", "endYmd", classify_values(["20260101", "20261231"]))
        assert len(rules) == 1 and "'YYYYMMDD'" in rules[0] and "`t_a.endYmd`" in rules[0]
        flag_rules = query_rules_for_column("t_a", "useYn", classify_values(["Y", "N"]))
        assert flag_rules == ["`t_a.useYn`는 플래그 값 'N', 'Y' 중 하나다."]
        assert query_rules_for_column("t_a", "x", classify_values([])) == []

    def test_referenced_identifiers(self):
        text = "`t_a` 와 `t_b.col_1` 을 잇고 t_c.id = t_a.id 로 조인한다. `한글` 무시"
        assert referenced_identifiers(text) == {"t_a", "t_b.col_1", "t_c.id", "t_a.id"}
