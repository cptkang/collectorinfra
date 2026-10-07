"""plans/140 W1 — 한글 코드 후보 이름 단서 · 이름 일치 관계 후보 · 자산 조립 공개 함수(순수 함수).

- 코드 후보: 이름이 한글 낱말(`코드`·`구분`·`여부`·`상태`…)로 **끝나면** 이름 단서다. 타입
  조건과 라틴 이름 판정은 그대로다. 반출 시드(`testdata/itam_bench/closed/itam_schema.json` ·
  1,988컬럼)에서 300개 이상을 잡는다(종전 0).
- 이름 일치 후보: 식별자형 이름 · 2개 이상 · 범위 50% 미만 · 같은 타입 군 · 기본키 없는 테이블만.
- 공개 조립 함수는 외부망 빌더(W3)가 반출 비율로 `ValueProfile`을 다시 만들어 부른다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.domain import schema_inference as inf

SEED = Path(__file__).resolve().parents[2] / "testdata/itam_bench/closed/itam_schema.json"


class TestKoreanCodeName:
    @pytest.mark.parametrize(("name", "dtype", "expected"), [
        ("상태코드", "char", True),
        ("처리상태", "varchar", True),
        ("사용여부", "char(1)", True),
        ("자산구분", "char", True),
        ("장비유형", "varchar", True),
        ("등급", "char", True),
        ("업무단계", "smallint", True),
        ("상태코드", "decimal", False),  # 타입 조건은 그대로(decimal 미포함)
        ("코드명", "varchar", False),  # 끝 낱말만 본다
        ("상태코드명", "varchar", False),
        ("자산번호", "char", False),
    ])
    def test_korean_suffix(self, name, dtype, expected):
        assert inf.is_code_candidate(name, dtype) is expected

    @pytest.mark.parametrize(("name", "dtype", "comment", "expected"), [
        ("statCd", "char", None, True),
        ("useYn", "char", None, True),
        ("partTypCd", "varchar", None, True),
        ("STATUS", "varchar", None, True),
        ("hostNm", "varchar", None, False),
        ("codeNm", "varchar", None, False),
        ("regYmd", "varchar", "상태 일자", True),  # 주석 단서는 종전 그대로
        ("regYmd", "varchar", "등록 일자", False),
        ("statCd", "decimal", None, False),
    ])
    def test_latin_unchanged(self, name, dtype, comment, expected):
        assert inf.is_code_candidate(name, dtype, comment) is expected

    def test_seed_catalog_candidates(self):
        tables = json.loads(SEED.read_text(encoding="utf-8"))["schema"]["tables"]
        columns = [(c["name"], c["type"]) for t in tables.values() for c in t["columns"]]
        assert len(columns) == 1988
        count = sum(inf.is_code_candidate(name, dtype) for name, dtype in columns)
        assert count >= 300  # 실측 371


class TestNameMatchColumns:
    @staticmethod
    def _tables(
        n: int, extra: dict[str, list[tuple[str, str]]]
    ) -> dict[str, list[tuple[str, str]]]:
        tables = {f"t{i}": [("비고", "varchar")] for i in range(n)}
        for table, cols in extra.items():
            tables[table] = tables.get(table, []) + cols
        return tables

    def test_identifier_names(self):
        for name in ("자산ID", "자산번호", "그룹코드", "기기식별자", "asset_id", "assetId",
                     "ASSET_NO", "groupCd", "id"):
            assert inf.is_identifier_name(name), name
        for name in ("자산명", "비고", "valid", "casino", "hostNm", "자산번호명"):
            assert not inf.is_identifier_name(name), name

    def test_selects_under_half(self):
        tables = self._tables(6, {"t0": [("자산번호", "char")], "t1": [("자산번호", "varchar")]})
        (col,) = inf.name_match_columns(tables)
        assert (col.name, col.type_class, col.members, col.table_count) == (
            "자산번호", "string", (("t0", "자산번호"), ("t1", "자산번호")), 2,
        )

    def test_half_or_more_is_common(self):
        tables = self._tables(6, {f"t{i}": [("자산번호", "char")] for i in range(3)})
        assert inf.name_match_columns(tables) == []  # 3/6 = 50% — 미만이 아니다

    def test_single_table_or_non_identifier(self):
        tables = self._tables(6, {"t0": [("자산번호", "char")], "t1": [("자산명", "varchar")],
                                  "t2": [("자산명", "varchar")]})
        assert inf.name_match_columns(tables) == []

    def test_case_insensitive_and_type_groups(self):
        tables = self._tables(12, {
            "t0": [("ASSET_ID", "varchar")], "t1": [("asset_id", "char")],
            "t2": [("Asset_Id", "decimal")], "t3": [("asset_id", "bigint")],
            "t4": [("asset_id", "date")],
        })
        cols = inf.name_match_columns(tables)
        assert [(c.type_class, [t for t, _ in c.members]) for c in cols] == [
            ("number", ["t2", "t3"]), ("string", ["t0", "t1"]),
        ]
        assert cols[1].members == (("t0", "ASSET_ID"), ("t1", "asset_id"))
        assert {c.table_count for c in cols} == {5}

    def test_primary_keyed_tables_do_not_participate(self):
        tables = self._tables(8, {"t0": [("자산번호", "char")], "t1": [("자산번호", "char")],
                                  "t2": [("자산번호", "char")]})
        (col,) = inf.name_match_columns(tables, primary_keyed=["t0"])
        assert [t for t, _ in col.members] == ["t1", "t2"]
        assert inf.name_match_columns(tables, primary_keyed=["t0", "t1"]) == []

    def test_seed_catalog_participants(self):
        tables = json.loads(SEED.read_text(encoding="utf-8"))["schema"]["tables"]
        cols = inf.name_match_columns(
            {t: [(c["name"], c["type"]) for c in v["columns"]] for t, v in tables.items()}
        )
        assert len(cols) == 80  # 끝 ID·번호·코드·식별자 · 2개 이상 · 108테이블의 50% 미만
        assert max(len(c.members) for c in cols) == 26


class TestAssemblyFunctions:
    def test_accepted_relationships(self):
        evidence = [
            {"child": "c", "parent": "p", "child_columns": ["a", "b"], "parent_columns": ["x", "y"],
             "origin": "inferred", "overlap": 0.95, "accepted": True},
            {"child": "d", "parent": "p", "child_columns": ["a"], "parent_columns": ["x"],
             "origin": "inferred", "overlap": 0.5, "accepted": False},
            {"child": "e", "parent": None, "child_columns": ["k"], "parent_columns": [],
             "origin": "name_match", "overlap": None, "accepted": False},
            {"child": "f", "parent": "q", "child_columns": ["k"], "parent_columns": ["k"],
             "origin": "declared", "overlap": None, "accepted": True},
        ]
        assert inf.accepted_relationships(evidence) == [
            {"from": "c.a", "to": "p.x", "origin": "inferred", "overlap": 0.95},
            {"from": "c.b", "to": "p.y", "origin": "inferred", "overlap": 0.95},
            {"from": "f.k", "to": "q.k", "origin": "declared", "overlap": None},
        ]

    def test_rules_and_entity_from_rebuilt_profiles(self):
        """반출 비율로 다시 만든 `ValueProfile`에서 규칙·식별 키 후보·`entity_keys`를 만든다."""
        columns = {
            "srv.regYmd": {"type": "varchar", "comment": None, "error": None,
                           "profile": inf.ValueProfile(total=10, date8=1.0)},
            "srv.hostNm": {"type": "varchar", "comment": "호스트명", "error": None,
                           "profile": inf.ValueProfile(total=10, hostname=1.0, mixed_case=True)},
            "srv.ipAddr": {"type": "varchar", "comment": None, "error": None,
                           "profile": inf.ValueProfile(total=10, ipv4=1.0, hostname=0.0,
                                                       multi_value=0.2)},
            "srv.useYn": {"type": "char", "comment": None, "error": None,
                          "profile": inf.ValueProfile(total=2, flag=("N", "Y"))},
            "srv.gone": {"type": "char", "comment": None, "error": "예산 초과", "profile": None},
            "aux.hostNm": {"type": "varchar", "comment": None, "error": None,
                           "profile": inf.ValueProfile(total=3, hostname=1.0)},
        }
        rules, entity = inf.rules_and_entity_candidates(columns)
        assert len(rules) == 4  # 날짜 · 대소문자 · 다중값 · 플래그(조회 못 한 컬럼은 건너뜀)
        assert "'YYYYMMDD'" in rules[0] and "`srv.regYmd`" in rules[0]
        assert "대소문자" in rules[1] and "LIKE" in rules[2] and "플래그" in rules[3]
        assert entity == {
            "srv": {"hostname": {"column": "hostNm", "ratio": 1.0, "multi_value": False},
                    "ip": {"column": "ipAddr", "ratio": 1.0, "multi_value": True}},
            "aux": {"hostname": {"column": "hostNm", "ratio": 1.0, "multi_value": False}},
        }
        assert inf.entity_keys_asset(entity, {"srv": 5, "aux": 100}) == {
            "entity": "server", "table": "srv", "keys": [
                {"type": "hostname", "column": "hostNm", "priority": 1, "compare": "casefold"},
                {"type": "ip", "column": "ipAddr", "priority": 2, "multi_value": True},
            ],
        }
        assert inf.entity_keys_asset({}, {}) is None
