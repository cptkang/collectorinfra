"""plans/140 W1-2~W1-5 — 이름 일치 관계 · 조회 예산 산정 · 컬럼별 근거 · 공개 조립 함수 동치.

결정적 가짜 클라이언트(`test_plan140_w1_fixtures`)로 `run_asset_profile`을 돌린다. 유일성·겹침
조회의 답은 테스트가 표로 정한다. 네트워크·DB·Redis·LLM 0.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from src.domain import schema_inference as inf
from src.schema_cache import asset_generation_service as ags
from tests.test_schema_cache.test_plan140_w1_fixtures import (
    COMMENTS,
    ROOT,
    VALUE_SETS,
    profile,
    snapshot_from_columns,
    snapshot_from_ddl,
    stable_hash,
)

KEY = "자산번호"
_UNIQUE_RE = re.compile(r"COUNT\(DISTINCT `(.+?)`\) AS distinct_count FROM (\w+) ")
_OVERLAP_RE = re.compile(r"FROM (\w+) c WHERE .* LEFT JOIN (\w+) p ON")


def _snapshot(members: list[str], *, n: int = 8, pk: tuple[str, ...] = (),
              fks: dict[str, list[tuple[str, str, str]]] | None = None) -> dict:
    """``n``개 테이블(``t_a``…) — ``members``에 `KEY` 컬럼, ``pk`` 테이블은 그 컬럼이 기본키."""
    names = [f"t_{chr(ord('a') + i)}" for i in range(n)]
    tables: dict[str, list[tuple[str, str, bool]]] = {t: [("비고내용", "varchar", False)]
                                                      for t in names}
    for table in members:
        tables[table].append((KEY, "char", table in pk))
    return snapshot_from_columns(tables, fks)


def _responder(unique: dict[str, tuple[int, int]],
               overlap: dict[tuple[str, str], tuple[int, int]]) -> Any:
    def respond(sql: str) -> list[dict[str, Any]] | None:
        if (m := _UNIQUE_RE.search(sql)):
            non_null, distinct = unique[m.group(2)]
            return [{"non_null": non_null, "distinct_count": distinct}]
        if KEY in sql and (m := _OVERLAP_RE.search(sql)):
            sampled, matched = overlap[(m.group(1), m.group(2))]
            return [{"sampled": sampled, "matched": matched}]
        return None

    return respond


async def _run(members: list[str], unique: dict[str, tuple[int, int]],
               overlap: dict[tuple[str, str], tuple[int, int]] | None = None,
               **kwargs: Any) -> dict[str, Any]:
    snap_kwargs = {k: kwargs.pop(k) for k in ("n", "pk", "fks") if k in kwargs}
    out = await profile(_snapshot(members, **snap_kwargs), "mariadb",
                        responder=_responder(unique, overlap or {}), **kwargs)
    evidence = out["draft"]["evidence"]
    out["name_match"] = [e for e in evidence["relationships"] if e["origin"] == "name_match"]
    out["unique_sql"] = [s for s in out["sql"] if "distinct_count" in s]
    out["overlap_sql"] = [s for s in out["sql"] if "AS sampled" in s and KEY in s]
    return out


UNIQ, DUP = (10, 10), (10, 7)


class TestNameMatch:
    async def test_adopts_child_to_unique_parent(self):
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": DUP}, {("t_b", "t_a"): (10, 10)})
        assert out["draft"]["assets"]["relationships"] == [
            {"from": f"t_b.{KEY}", "to": f"t_a.{KEY}", "origin": "name_match", "overlap": 1.0},
        ]
        (item,) = out["name_match"]
        assert item == {
            "child": "t_b", "parent": "t_a", "child_columns": [KEY], "parent_columns": [KEY],
            "origin": "name_match", "overlap": 1.0, "sampled": 10, "accepted": True,
            "error": None, "unique_parent": True,
        }
        assert len(out["unique_sql"]) == 2 and len(out["overlap_sql"]) == 1
        # 유일성은 겹침보다 먼저 · 한글 컬럼은 백틱 인용
        assert out["sql"].index(out["unique_sql"][-1]) < out["sql"].index(out["overlap_sql"][0])
        assert f"`{KEY}`" in out["overlap_sql"][0]

    async def test_no_unique_parent(self):
        out = await _run(["t_a", "t_b"], {"t_a": DUP, "t_b": (0, 0)})
        assert [(e["child"], e["parent"], e["error"], e["unique_parent"], e["accepted"])
                for e in out["name_match"]] == [
            ("t_a", None, "부모 유일성 없음", False, False),
            ("t_b", None, "부모 유일성 없음", False, False),
        ]
        assert out["overlap_sql"] == [] and out["draft"]["assets"]["relationships"] == []

    async def test_overlap_below_threshold(self):
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": DUP}, {("t_b", "t_a"): (10, 5)})
        (item,) = out["name_match"]
        assert item["overlap"] == 0.5 and item["accepted"] is False and item["unique_parent"]
        assert out["draft"]["assets"]["relationships"] == []

    async def test_one_to_one_uses_higher_direction(self):
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": UNIQ},
                         {("t_b", "t_a"): (10, 8), ("t_a", "t_b"): (10, 10)})
        (item,) = out["name_match"]
        assert (item["child"], item["parent"], item["overlap"]) == ("t_a", "t_b", 1.0)
        assert len(out["overlap_sql"]) == 2

    async def test_one_to_one_tie_parent_is_name_first(self):
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": UNIQ},
                         {("t_b", "t_a"): (10, 10), ("t_a", "t_b"): (10, 10)})
        (item,) = out["name_match"]
        assert (item["child"], item["parent"]) == ("t_b", "t_a")

    async def test_column_overlap_cap(self, monkeypatch):
        monkeypatch.setattr(ags, "NAME_MATCH_MAX_OVERLAPS_PER_COLUMN", 1)
        out = await _run(["t_a", "t_b", "t_c"], {"t_a": UNIQ, "t_b": DUP, "t_c": DUP},
                         {("t_b", "t_a"): (10, 10), ("t_c", "t_a"): (10, 10)})
        assert [(e["child"], e["accepted"], e["error"]) for e in out["name_match"]] == [
            ("t_b", True, None), ("t_c", False, "후보 상한 초과"),
        ]
        assert len(out["overlap_sql"]) == 1

    async def test_total_query_cap(self, monkeypatch):
        monkeypatch.setattr(ags, "NAME_MATCH_MAX_QUERIES", 2)
        out = await _run(["t_a", "t_b", "t_c"], {"t_a": UNIQ, "t_b": DUP, "t_c": DUP})
        assert len(out["unique_sql"]) == 2 and out["overlap_sql"] == []
        assert [(e["child"], e["parent"], e["error"]) for e in out["name_match"]] == [
            ("t_b", "t_a", "후보 상한 초과"), ("t_c", "t_a", "후보 상한 초과"),
        ]

    async def test_total_cap_before_any_parent(self, monkeypatch):
        monkeypatch.setattr(ags, "NAME_MATCH_MAX_QUERIES", 1)
        out = await _run(["t_a", "t_b"], {"t_a": DUP, "t_b": UNIQ})
        assert [(e["child"], e["error"], e["unique_parent"]) for e in out["name_match"]] == [
            ("t_a", "부모 유일성 없음", False), ("t_b", "후보 상한 초과", None),
        ]

    async def test_budget_exceeded(self, monkeypatch):
        monkeypatch.setattr(ags, "DEFAULT_PROBE_BUDGET", 3)
        monkeypatch.setattr(ags, "PROBE_BUDGET_CAP", 3)
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": UNIQ})
        # 카탈로그 2 + t_a 유일성 1 → t_b 유일성 · 겹침은 예산 초과
        assert len(out["unique_sql"]) == 1 and out["overlap_sql"] == []
        (item,) = out["name_match"]
        assert (item["child"], item["parent"], item["error"]) == ("t_b", "t_a", "예산 초과")
        budget = out["draft"]["evidence"]["budget"]
        assert budget["limit"] == 3 and budget["cap"] == 3 and budget["requested"] > 3
        assert "unique:t_b.자산번호" in budget["skipped_sample"]

    async def test_half_or_more_is_common(self):
        out = await _run(["t_a", "t_b", "t_c", "t_d"], {})
        assert out["name_match"] == [] and out["unique_sql"] == []

    async def test_primary_keyed_table_does_not_participate(self):
        out = await _run(["t_a", "t_b", "t_c"], {"t_b": UNIQ, "t_c": DUP},
                         {("t_c", "t_b"): (10, 10)}, pk=("t_a",))
        assert all("FROM t_a " not in s for s in out["unique_sql"]) and len(out["unique_sql"]) == 2
        assert [(e["child"], e["parent"]) for e in out["name_match"]] == [("t_c", "t_b")]
        # 기본키 테이블과의 쌍은 종전 경로(기본키 일치 추론)가 맡는다
        inferred = [e for e in out["draft"]["evidence"]["relationships"]
                    if e["origin"] == "inferred"]
        assert {(e["child"], e["parent"]) for e in inferred} == {("t_b", "t_a"), ("t_c", "t_a")}

    async def test_existing_candidate_pair_skipped(self):
        out = await _run(["t_a", "t_b"], {"t_a": UNIQ, "t_b": DUP},
                         fks={"t_b": [(KEY, "t_a", KEY)]})
        assert out["name_match"] == [] and out["overlap_sql"] == []
        declared = [e for e in out["draft"]["evidence"]["relationships"]
                    if e["origin"] == "declared"]
        assert declared and declared[0]["unique_parent"] is None

    async def test_offline(self):
        out = await _run(["t_a", "t_b"], {}, offline=True)
        assert [(e["child"], e["error"]) for e in out["name_match"]] == [
            ("t_a", "DB 연결 없음 — 값 겹침을 확인하지 못했습니다"),
            ("t_b", "DB 연결 없음 — 값 겹침을 확인하지 못했습니다"),
        ]


class TestBudget:
    def test_requested_queries(self):
        rels = [inf.InferredRelation("c", "p", ("k",), ("k",), origin)
                for origin in ("declared", "inferred", "same_key")]
        cols = [inf.NameMatchColumn("k", "string", (("a", "k"), ("b", "k"), ("c", "k")), 3),
                inf.NameMatchColumn("j", "string", tuple((f"t{i}", "j") for i in range(40)), 40)]
        plan = ags._ColumnPlan(meta={"a.x": {}, "a.y": {}}, code_keys=[], format_keys=[])
        # 카탈로그 2 + 겹침(0+1+2) + 이름 일치((3+6)+(40+30)) + 컬럼 2 + 코드 테이블 1
        assert ags._requested_queries(2, rels, cols, plan, [("t", "c", "n")]) == 2 + 3 + 79 + 2 + 1

    @pytest.mark.parametrize(("default", "cap"), [(5, 1000), (5, 7)])
    async def test_limit_follows_requested_with_cap(self, monkeypatch, default, cap):
        monkeypatch.setattr(ags, "DEFAULT_PROBE_BUDGET", default)
        monkeypatch.setattr(ags, "PROBE_BUDGET_CAP", cap)
        out = await profile(snapshot_from_ddl(ROOT / "testdata/itam/init/01_schema.sql", "mariadb"),
                            "mariadb")
        budget = out["draft"]["evidence"]["budget"]
        assert budget["requested"] > 7
        assert budget["limit"] == min(max(default, budget["requested"]), cap)
        assert budget["cap"] == cap
        assert (budget["skipped"] > 0) is (cap < budget["requested"])


COLUMN_KEYS = {
    "key", "candidate", "code", "distinct", "truncated", "total", "date8", "datetime14", "ipv4",
    "hostname", "multi_value", "mixed_case", "flag", "entity_key", "error",
}
SANDBOX = ROOT / "testdata/itam/init/01_schema.sql"
PG = ROOT / "testdata/pg/init/01_create_tables.sql"


class TestColumnEvidence:
    async def test_shape_without_values(self):
        out = await profile(snapshot_from_ddl(SANDBOX, "mariadb"), "mariadb")
        evidence = out["draft"]["evidence"]
        rows = evidence["columns"]
        assert rows and all(set(r) == COLUMN_KEYS for r in rows)
        assert {r["candidate"] for r in rows} == {"code", "format"}
        coded = {k for k in out["draft"]["assets"]["code_values"]}
        assert {r["key"] for r in rows if r["code"]} == coded
        assert all(r["candidate"] == "code" for r in rows if r["code"])
        assert all(isinstance(r["distinct"], int) and r["error"] is None for r in rows)
        # 값은 싣지 않는다(플래그 집합만 예외 — 고정 어휘)
        text = json.dumps(rows, ensure_ascii=False)
        for value in {v for values in VALUE_SETS for v in values} - {"Y", "N", "1", "2"}:
            assert f'"{value}"' not in text
        # 기존 근거는 그대로 있다
        assert "formats" in evidence and "code_columns" in evidence

    async def test_offline_rows(self):
        out = await profile(snapshot_from_ddl(SANDBOX, "mariadb"), "mariadb", offline=True)
        rows = out["draft"]["evidence"]["columns"]
        assert rows and all(r["error"] == "DB 연결 없음" and r["total"] is None
                            and r["date8"] is None and r["distinct"] is None
                            and r["code"] is False and r["flag"] == [] for r in rows)


class TestPublicAssemblyEquivalence:
    """반출 근거(`evidence`)만으로 공개 함수가 P1과 같은 자산을 다시 만든다(W3 외부망 빌더 경로)."""

    @pytest.mark.parametrize(("path", "engine"), [(SANDBOX, "mariadb"), (PG, "postgresql")])
    async def test_rebuild_from_evidence(self, path, engine):
        out = await profile(snapshot_from_ddl(path, engine), engine)
        assets, evidence = out["draft"]["assets"], out["draft"]["evidence"]
        assert inf.accepted_relationships(evidence["relationships"]) == assets["relationships"]

        columns: dict[str, dict[str, Any]] = {}
        for row in evidence["columns"]:
            n = stable_hash(row["key"]) % 7  # 가짜 카탈로그 주석과 같은 규칙
            profile_ = None if row["total"] is None else inf.ValueProfile(
                total=row["total"], date8=row["date8"], datetime14=row["datetime14"],
                ipv4=row["ipv4"], hostname=row["hostname"], multi_value=row["multi_value"],
                mixed_case=row["mixed_case"], flag=tuple(row["flag"]),
            )
            columns[row["key"]] = {"type": None, "error": row["error"], "profile": profile_,
                                   "comment": COMMENTS[n] if n < len(COMMENTS) else None}
        rules, entity = inf.rules_and_entity_candidates(columns)
        assert rules == assets["query_rules"]
        rows = {r["table"]: r["rows"] for r in evidence["allowed_tables"]}
        assert inf.entity_keys_asset(entity, rows) == assets["entity_keys"]
