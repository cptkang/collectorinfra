"""scripts/itam_erd.py — ITAM 스키마 스냅숏·관계 추론·ERD 렌더 (plans/95 G-4).

DB 접속 0 — `information_schema` 원시 행을 합성해 순수 함수만 검증한다. 합성 테이블명은 운영 명명
규약(`TCDMS` + 군 2자 + 번호)을 따르되 컬럼은 가상이다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location("itam_erd", REPO / "scripts" / "itam_erd.py")
itam_erd = importlib.util.module_from_spec(_spec)
sys.modules["itam_erd"] = itam_erd
_spec.loader.exec_module(itam_erd)


def _cols(table: str, names: list[str], pk: list[str]) -> list[dict]:
    return [
        {
            "TABLE_NAME": table, "NAME": n, "ORDINAL": i + 1, "DATA_TYPE": "varchar",
            "COLUMN_TYPE": "varchar(20)", "NULLABLE": "NO" if n in pk else "YES",
            "COLUMN_KEY": "PRI" if n in pk else "", "DEFAULT_VALUE": None, "COMMENT": f"{n} 설명",
        }
        for i, n in enumerate(names)
    ]


_TABLES = {
    # 그룹사 코드 단독 PK — 공통 컬럼뿐이라 부모 후보가 아니다
    "TCDMSCM01": (["groupCoCd", "coNm"], ["groupCoCd"]),
    "TCDMSIF80": (["groupCoCd", "sevrHostName", "iPCtnt", "empNo"], ["groupCoCd", "sevrHostName", "iPCtnt"]),
    "TCDMSIF79": (["groupCoCd", "sevrHostName", "iPCtnt", "hWSportEndYmd"], ["groupCoCd", "sevrHostName", "iPCtnt"]),
    "TCDMSIF81": (["groupCoCd", "sevrHostName", "iPCtnt", "chgSno"], ["groupCoCd", "sevrHostName", "iPCtnt", "chgSno"]),
    "TCDMSAM01": (["groupCoCd", "assetNo"], ["groupCoCd", "assetNo"]),
    "TCDMSAM02": (["groupCoCd", "assetNo", "seq"], ["groupCoCd", "assetNo", "seq"]),
    "TCDMSAM03": (["groupCoCd", "assetNo", "seq", "histNo"], ["groupCoCd", "assetNo", "seq", "histNo"]),
    "TCDMSHR01": (["groupCoCd", "empNo", "empNm"], ["groupCoCd", "empNo"]),
    "TCDMSBR01": (["groupCoCd", "brNo", "assetNo"], ["groupCoCd", "brNo"]),
    "TCDMSGT01": (["groupCoCd", "memo"], []),  # 기본키 없음
}


def _raw() -> dict:
    tables, columns, indexes = [], [], []
    for name, (cols, pk) in _TABLES.items():
        tables.append({
            "NAME": name, "TYPE": "BASE TABLE", "ENGINE": "InnoDB", "ROWS_ESTIMATE": 10,
            "COMMENT": f"{name} 테이블 | 파이프", "COLLATION": "utf8mb4_general_ci",
        })
        columns += _cols(name, cols, pk)
        indexes += [
            {"TABLE_NAME": name, "INDEX_NAME": "PRIMARY", "NON_UNIQUE": 0, "SEQ": i + 1, "COLUMN_NAME": c}
            for i, c in enumerate(pk)
        ]
    indexes.append(
        {"TABLE_NAME": "TCDMSIF80", "INDEX_NAME": "IX_EMP", "NON_UNIQUE": 1, "SEQ": 1, "COLUMN_NAME": "empNo"}
    )
    fks = [
        {"CONSTRAINT_NAME": "FK_BR_AM", "TABLE_NAME": "TCDMSBR01", "COLUMN_NAME": c, "SEQ": i + 1,
         "REF_TABLE": "TCDMSAM01", "REF_COLUMN": c}
        for i, c in enumerate(["groupCoCd", "assetNo"])
    ]
    server = {"VERSION": b"10.6.12-MariaDB", "LOWER_CASE_TABLE_NAMES": 0}
    return {"server": server, "tables": tables, "columns": columns, "indexes": indexes, "fks": fks}


@pytest.fixture
def snapshot() -> dict:
    return itam_erd.build_snapshot(_raw(), "INST1")


def _pairs(relations, kind=None) -> set[tuple[str, str]]:
    return {(r.child, r.parent) for r in relations if kind is None or r.kind == kind}


class TestSnapshot:
    def test_shape(self, snapshot):
        meta = snapshot["meta"]
        assert meta["schema"] == "INST1"
        assert meta["table_count"] == len(_TABLES)
        assert meta["server"]["version"] == "10.6.12-MariaDB"  # bytes 정규화 · 키 소문자화
        t = {x["name"]: x for x in snapshot["tables"]}
        assert t["TCDMSIF80"]["primary_key"] == ["groupCoCd", "sevrHostName", "iPCtnt"]
        assert t["TCDMSIF80"]["indexes"] == [{"name": "IX_EMP", "unique": False, "columns": ["empNo"]}]
        assert t["TCDMSBR01"]["foreign_keys"][0]["ref_columns"] == ["groupCoCd", "assetNo"]
        assert t["TCDMSGT01"]["primary_key"] == []

    def test_pk_falls_back_to_column_key(self):
        raw = _raw()
        raw["indexes"] = []  # STATISTICS가 안 보이는 권한
        t = {x["name"]: x for x in itam_erd.build_snapshot(raw, "INST1")["tables"]}
        assert t["TCDMSAM02"]["primary_key"] == ["groupCoCd", "assetNo", "seq"]


class TestInference:
    def test_relations(self, snapshot):
        relations, same_key = itam_erd.infer_relations(snapshot)
        assert _pairs(relations, "declared") == {("TCDMSBR01", "TCDMSAM01")}
        assert _pairs(relations, "inferred") == {
            ("TCDMSAM02", "TCDMSAM01"),
            ("TCDMSAM03", "TCDMSAM02"),   # AM01은 AM02 기본키의 진부분집합이라 빠진다
            ("TCDMSIF80", "TCDMSHR01"),   # 군 간
            ("TCDMSIF81", "TCDMSIF79"),   # 동일 키 군 IF79·IF80 중 이름순 대표만
        }
        assert same_key == [["TCDMSIF79", "TCDMSIF80"]]

    def test_common_only_pk_is_not_parent(self, snapshot):
        relations, _ = itam_erd.infer_relations(snapshot)
        assert all(r.parent != "TCDMSCM01" for r in relations)

    def test_declared_pair_not_duplicated(self, snapshot):
        relations, _ = itam_erd.infer_relations(snapshot)
        assert [r.kind for r in relations if (r.child, r.parent) == ("TCDMSBR01", "TCDMSAM01")] == ["declared"]

    def test_common_ratio_one_keeps_group_code_parent(self, snapshot):
        relations, _ = itam_erd.infer_relations(snapshot, common_ratio=1.0)
        assert ("TCDMSGT01", "TCDMSCM01") in _pairs(relations, "inferred")

    def test_family(self):
        assert itam_erd.family_of("TCDMSIF80") == "TCDMSIF"
        assert itam_erd.family_label("TCDMSIF") == "IF"
        assert itam_erd.family_of("ETC") == "ETC"


class TestRender:
    def test_erd(self, snapshot):
        relations, same_key = itam_erd.infer_relations(snapshot)
        md = itam_erd.render_erd(snapshot, relations, same_key)
        assert md.count("```mermaid") == 1 + 6  # 군 간 개요 + 군 6개
        assert 'TCDMSAM01 ||--o{ TCDMSBR01 : "groupCoCd,assetNo"' in md
        assert 'TCDMSAM01 ||..o{ TCDMSAM02 : "groupCoCd,assetNo"' in md
        assert "TCDMSIF -->|1| TCDMSHR" in md
        assert "varchar empNo FK" in md
        assert "coNm" not in md  # keys 모드: 기본키·관계 컬럼만
        assert "`lower_case_table_names`" in md
        assert "## 기본키 없는 테이블" in md

    def test_erd_all_columns(self, snapshot):
        relations, same_key = itam_erd.infer_relations(snapshot)
        assert "coNm" in itam_erd.render_erd(snapshot, relations, same_key, mode="all")

    def test_dictionary_escapes_pipe(self, snapshot):
        relations, _ = itam_erd.infer_relations(snapshot)
        md = itam_erd.render_dictionary(snapshot, relations)
        assert "TCDMSIF80 테이블 \\| 파이프" in md
        assert "참조(부모): `TCDMSHR01` (groupCoCd, empNo) 추론" in md
        assert "인덱스: `IX_EMP`(empNo)" in md

    def test_mermaid_identifier_sanitized(self):
        table = {"name": "T1", "primary_key": ["자산 번호"], "columns": [
            {"name": "자산 번호", "data_type": "varchar", "comment": 'a"b'}]}
        body = "\n".join(itam_erd.render_entity(table, set(), "keys"))
        assert "varchar _____ PK" in body and '"자산 번호 a\'b"' in body


class TestConnection:
    def test_parse_dsn_decodes(self):
        kw = itam_erd.parse_dsn("mariadb://SDQ000:p%40ss%3Aw@10.0.0.5:3306/INST1")
        assert kw == {"host": "10.0.0.5", "port": 3306, "user": "SDQ000", "password": "p@ss:w", "database": "INST1"}

    @pytest.mark.parametrize("dsn", ["postgresql://a:b@h/d", "mariadb://a:b@h:3306", "mariadb://a:b@h/d?ssl=1"])
    def test_parse_dsn_rejects(self, dsn):
        with pytest.raises(ValueError):
            itam_erd.parse_dsn(dsn)

    def test_mask(self):
        assert itam_erd.mask_dsn("mariadb://SDQ000:secret@h:3306/INST1") == "mariadb://SDQ000:********@h:3306/INST1"

    def test_env_precedence(self, tmp_path, monkeypatch):
        env = tmp_path / ".env"
        env.write_text("# c\nITAM_CONNECTION=mariadb://f:f@h/d\n", encoding="utf-8")
        monkeypatch.delenv("ITAM_CONNECTION", raising=False)
        assert itam_erd.read_env_value(env, "ITAM_CONNECTION") == "mariadb://f:f@h/d"
        monkeypatch.setenv("ITAM_CONNECTION", "mariadb://o:o@h/d")
        assert itam_erd.read_env_value(env, "ITAM_CONNECTION") == "mariadb://o:o@h/d"


def test_cli_from_json(snapshot, tmp_path):
    src = tmp_path / "snap.json"
    src.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    assert itam_erd.main(["--from-json", str(src), "--out-dir", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {"itam_schema.json", "itam_erd.md", "itam_dictionary.md"}


def test_cli_without_dsn_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("ITAM_CONNECTION", raising=False)
    rc = itam_erd.main(["--env-file", str(tmp_path / "none.env"), "--out-dir", str(tmp_path / "o")])
    assert rc == 2
