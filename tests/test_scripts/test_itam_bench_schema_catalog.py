"""ITAM 질의 벤치 — 구조화 스키마 카탈로그 (plans/135 W1 · §3.4 · D-301 ③).

조회문이 아니라 테이블·컬럼 목록 YAML 이다. 입력 3종(파일 스키마 캐시 · `itam_erd` 스냅숏 · 샌드박스
전사본)이 같은 구조를 주면 카탈로그도 같아야 한다(동형). DB 에 붙지 않는다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import catalog as cat

_ROOT = Path(__file__).resolve().parents[2]
_TRANSCRIPT = _ROOT / "testdata" / "itam" / "schema.yaml"


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def transcript() -> dict[str, Any]:
    return yaml.safe_load(_TRANSCRIPT.read_text(encoding="utf-8"))


def _cache_file(tmp_path: Path, transcript: dict[str, Any]) -> Path:
    """`PersistentSchemaCache` 파일 모양.

    타입 길이 없음 · 표본 행 포함 — 카탈로그는 표본을 읽지 않는다.
    """
    tables = {}
    for name, spec in transcript["tables"].items():
        tables[name] = {
            "columns": [
                {
                    "name": c["var"],
                    "type": c["type"].split("(")[0].lower(),
                    "nullable": c["nullable"],
                    "primary_key": bool(c.get("pk")),
                    "foreign_key": False,
                    "references": None,
                }
                for c in spec["columns"]
            ],
            "row_count_estimate": 30,
            "sample_data": [{"rspblPsnEmnm": "홍길동", "rspblPsnEmpid": "T000003"}],
        }
    body = {
        "_cache_version": 1,
        "_fingerprint": "x",
        "_db_id": "itam",
        "_cached_at": 0.0,
        "_cached_at_iso": "",
        "schema": {"tables": tables, "relationships": []},
        "_descriptions": {"TCDMSIF79.hWSportEndYmd": "하드웨어 지원 종료일"},
        "_synonyms": {},
    }
    directory = tmp_path / "cache"
    directory.mkdir()
    (directory / "itam_schema.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _snapshot_file(tmp_path: Path, transcript: dict[str, Any]) -> Path:
    """`scripts/itam_erd.py` `build_snapshot` 출력 모양."""
    tables = []
    for name, spec in transcript["tables"].items():
        tables.append(
            {
                "name": name,
                "type": "BASE TABLE",
                "engine": "InnoDB",
                "rows_estimate": 30,
                "comment": "",
                "collation": "utf8mb4_general_ci",
                "columns": [
                    {
                        "name": c["var"],
                        "ordinal": c["seq"],
                        "data_type": c["type"].split("(")[0].lower(),
                        "column_type": c["type"].lower(),
                        "nullable": c["nullable"],
                        "key": "PRI" if c.get("pk") else "",
                        "default": None,
                        "comment": "",
                    }
                    for c in spec["columns"]
                ],
                "primary_key": list(spec["primary_key"]),
                "indexes": [],
                "foreign_keys": [],
            }
        )
    path = tmp_path / "itam_schema.json"
    path.write_text(
        json.dumps({"meta": {}, "tables": tables}, ensure_ascii=False), encoding="utf-8"
    )
    return path


def _projection(catalog: dict[str, Any]) -> dict[str, Any]:
    return {
        "tables": {
            name: {
                "key": t["key"],
                "relations": t["relations"],
                "columns": [
                    (c["name"], c["value_kind"], c["log_policy"], c["nullable"])
                    for c in t["columns"]
                ],
            }
            for name, t in catalog["tables"].items()
        },
        "groups": catalog["same_key_groups"],
    }


class TestSchemaCatalog:
    def test_transcript_catalog_shape(self, policy: cat.ColumnPolicy) -> None:
        schema = cat.load_schema_source("transcript")
        catalog = cat.build_schema_catalog(schema, policy, assets={})
        assert catalog["summary"] == {
            "tables": 2,
            "tables_with_meaning": 0,
            "columns": 77,
            "columns_with_meaning": 0,
            "columns_with_synonyms": 0,
            "unclassified_columns": 0,
            "relations": {},
        }
        assert catalog["same_key_groups"] == [["TCDMSIF79", "TCDMSIF80"]]  # 3열 동일 기본키
        cols = {c["name"]: c for c in catalog["tables"]["TCDMSIF80"]["columns"]}
        assert cols["manmenCtrcEndYmd"]["value_kind"] == "date_text_yyyymmdd"
        assert cols["manmenHopeYm"]["value_kind"] == "month_text_yyyymm"
        assert cols["sysRegiPrcssYMS"]["value_kind"] == "datetime_text"
        assert cols["vrtlSevrMapngYn"]["value_kind"] == "flag_yn"
        assert cols["acqsiAmt"]["value_kind"] == "amount"
        assert cols["cPUCnt"]["value_kind"] == "number"
        assert cols["sevrPtrnDstcd"]["value_kind"] == "code_text"
        assert cols["rspblPsnEmnm"]["log_policy"] == "pii"
        assert catalog["tables"]["TCDMSIF80"]["key"] == ["groupCoCd", "sevrHostName", "iPCtnt"]

    def test_three_sources_are_isomorphic(
        self, tmp_path: Path, policy: cat.ColumnPolicy, transcript: dict[str, Any]
    ) -> None:
        catalogs = [
            cat.build_schema_catalog(cat.load_schema_source("transcript"), policy, assets={}),
            cat.build_schema_catalog(
                cat.load_schema_source("schema_cache", cache_dir=_cache_file(tmp_path, transcript)),
                policy,
                assets={},
            ),
            cat.build_schema_catalog(
                cat.load_schema_source("snapshot", path=_snapshot_file(tmp_path, transcript)),
                policy,
                assets={},
            ),
        ]
        assert [c["source"] for c in catalogs] == [
            "transcript",
            "schema_cache",
            "snapshot:itam_schema.json",
        ]
        first = _projection(catalogs[0])
        assert all(_projection(c) == first for c in catalogs[1:])
        # 의미 출처는 캐시 설명으로 갈린다(시스템이 실제로 가진 설명)
        meaning = {c["name"]: c for c in catalogs[1]["tables"]["TCDMSIF79"]["columns"]}
        assert meaning["hWSportEndYmd"]["meaning_source"] == "cache_description"
        assert catalogs[1]["summary"]["columns_with_meaning"] == 1

    def test_catalog_has_no_values_and_no_query_forms(
        self, tmp_path: Path, policy: cat.ColumnPolicy, transcript: dict[str, Any]
    ) -> None:
        schema = cat.load_schema_source("schema_cache", cache_dir=_cache_file(tmp_path, transcript))
        text = yaml.safe_dump(
            cat.build_schema_catalog(schema, policy, assets={}), allow_unicode=True, sort_keys=False
        )
        assert "sample" not in text
        assert policy.canary_hits(text) == 0
        assert cat.schema_form_violations(text) == 0
        assert (
            cat.schema_form_violations(
                "CREATE TABLE x (a int)\nSELECT * FROM information_schema.columns"
            )
            == 2
        )

    def test_unclassified_column_gets_pii_suggestion(self, policy: cat.ColumnPolicy) -> None:
        schema = {
            "source": "t",
            "descriptions": {},
            "tables": {
                "TNEW": {
                    "comment": "",
                    "rows_estimate": None,
                    "primary_key": [],
                    "foreign_keys": [],
                    "columns": [
                        {
                            "name": "custEmailAddr",
                            "type": "varchar(100)",
                            "nullable": True,
                            "is_key": False,
                            "comment": "",
                        },
                        {
                            "name": "itemCnt",
                            "type": "decimal(3,0)",
                            "nullable": True,
                            "is_key": False,
                            "comment": "",
                        },
                    ],
                }
            },
        }
        cols = {
            c["name"]: c
            for c in cat.build_schema_catalog(schema, policy, assets={})["tables"]["TNEW"][
                "columns"
            ]
        }
        assert cols["custEmailAddr"]["log_policy"] == "unclassified"
        assert cols["custEmailAddr"]["policy_suggestion"] == "pii"
        assert "policy_suggestion" not in cols["itemCnt"]

    def test_missing_inputs_fail_loudly(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            cat.load_schema_source("schema_cache", cache_dir=tmp_path)
        with pytest.raises(ValueError):
            cat.load_schema_source("snapshot")
        with pytest.raises(ValueError):
            cat.load_schema_source("ddl")


class TestAssetFingerprints:
    def test_profile_header_is_not_copied(self, tmp_path: Path) -> None:
        profile = tmp_path / "config" / "db_profiles" / "itam.yaml"
        profile.parent.mkdir(parents=True)
        profile.write_text(
            "# plans/104 관리자 승인 적용 v1 · 2026-09-23 · 5488923 · env=http://h:9099/sse\n"
            "source: manual\nenvironment: local_sandbox\npatterns: []\n",
            encoding="utf-8",
        )
        assets = cat.asset_fingerprints("itam", repo_root=tmp_path, descriptions=9)
        assert assets["profile"]["source"] == "manual"
        assert assets["profile"]["environment"] == "local_sandbox"
        assert len(assets["profile"]["fingerprint"]) == 12
        assert assets["seeds"] is None and assets["prompt_template"] is None
        assert assets["column_descriptions"] == 9
        assert "5488923" not in json.dumps(assets) and "9099" not in json.dumps(assets)

    def test_fingerprint_changes_with_asset(self, tmp_path: Path) -> None:
        seeds = tmp_path / "config" / "synonym_seeds" / "itam.yaml"
        seeds.parent.mkdir(parents=True)
        seeds.write_text("a: 1\n", encoding="utf-8")
        before = cat.asset_fingerprints("itam", repo_root=tmp_path)["seeds"]
        seeds.write_text("a: 2\n", encoding="utf-8")
        assert cat.asset_fingerprints("itam", repo_root=tmp_path)["seeds"] != before


# --- 「DB 구조」 탭 산출물 전체 반영 (plans/135 v1.4) -----------------------------------


def _tab_cache(directory: Path, tables: dict[str, list[dict[str, Any]]], **extra: Any) -> Path:
    """「DB 구조」 탭 등록·DDL 등록이 쓰는 `.cache/schema/{db_id}_schema.json` 모양."""
    body = {
        "_cache_version": 1,
        "_fingerprint": "x",
        "_db_id": "itam",
        "_cached_at": 0.0,
        "_cached_at_iso": "",
        "schema": {
            "tables": {
                name: {"columns": cols, "row_count_estimate": 10, "sample_data": []}
                for name, cols in tables.items()
            },
            "relationships": extra.pop("relationships", []),
        },
        **extra,
    }
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "itam_schema.json").write_text(
        json.dumps(body, ensure_ascii=False), encoding="utf-8"
    )
    return directory


def _col(name: str, *, pk: bool = False, fk: str | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "type": "varchar",
        "nullable": not pk,
        "primary_key": pk,
        "foreign_key": bool(fk),
        "references": fk,
    }


class TestDbStructureTabOutputs:
    def test_declared_fk_synonym_counts_and_db_description(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        tables = {
            "TSVC01": [_col("svcId", pk=True), _col("svcNm")],
            "TSVCSRV": [_col("svcId", pk=True, fk="TSVC01.svcId"), _col("sevrHostName", pk=True)],
            "TCDMSIF80": [_col("sevrHostName", pk=True), _col("svcCd", fk=None)],
        }
        cache = _tab_cache(
            tmp_path / "c",
            tables,
            relationships=[{"from": "TCDMSIF80.svcCd", "to": "TSVC01.svcId"}],
            _descriptions={"TSVC01.svcNm": "서비스 이름"},
            _synonyms={"TSVC01.svcNm": ["서비스명", "업무명", "홍길동"]},
            _db_description="IT 자산 원장",
            _db_description_origin="llm",
        )
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("schema_cache", cache_dir=cache), policy, assets={}
        )
        rels = {
            (t, r["to"], r["kind"]): r["columns"]
            for t, table in catalog["tables"].items()
            for r in table["relations"]
        }
        assert rels[("TSVCSRV", "TSVC01", "declared")] == [["svcId", "svcId"]]  # references
        assert rels[("TCDMSIF80", "TSVC01", "declared")] == [["svcCd", "svcId"]]  # relationships
        column = {c["name"]: c for c in catalog["tables"]["TSVC01"]["columns"]}["svcNm"]
        assert column["synonyms"] == 3 and column["meaning_source"] == "cache_description"
        assert catalog["db_description"] == {"text": "IT 자산 원장", "origin": "llm"}
        assert "구별할 수 없다" in catalog["meaning_sources"]["cache_description"]
        text = yaml.safe_dump(catalog, allow_unicode=True)
        assert "서비스명" not in text and "홍길동" not in text  # 유사어 낱말은 싣지 않는다

    def test_approved_profile_structure_only(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        repo = tmp_path / "repo"
        profile_path = repo / "config" / "db_profiles" / "itam.yaml"
        profile_path.parent.mkdir(parents=True)
        profile_path.write_text(
            "# plans/104 관리자 승인 적용 v3 · 2026-10-06 · 5488923 · env=http://h:9099/sse\n"
            + yaml.safe_dump(
                {
                    "source": "manual",
                    "query_guide": "담당자 홍길동 서버는 ...",
                    "allowed_tables": ["TCDMSIF80"],
                    "entity_keys": {
                        "entity": "server",
                        "table": "TCDMSIF80",
                        "keys": [
                            {
                                "type": "hostname",
                                "column": "sevrHostName",
                                "priority": 1,
                                "compare": "casefold",
                            }
                        ],
                    },
                    "relationships": [
                        {
                            "from": "TCDMSIF79.sevrHostName",
                            "to": "TCDMSIF80.sevrHostName",
                            "origin": "same_key",
                            "overlap": 0.97,
                        },
                        {
                            "from": "TCDMSIF79.iPCtnt",
                            "to": "TCDMSIF80.iPCtnt",
                            "origin": "same_key",
                            "overlap": 0.95,
                        },
                    ],
                    "code_values": {"TCDMSIF80.asstStusDstcd": ["1", "2", "홍길동"]},
                    "code_labels": {"TCDMSIF80.asstStusDstcd": {"1": "운영", "2": "폐기"}},
                    "query_rules": ["담당자 홍길동은 ..."],
                    "query_examples": [{"question": "q"}],
                    "patterns": [],
                    "custom_note": "x",
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        versions = repo / ".cache" / "structure" / "itam" / "versions"
        versions.mkdir(parents=True)
        (versions / "v1.yaml").write_text("ver: 1\nby: 홍길동\n", encoding="utf-8")
        (versions / "v3.yaml").write_text("ver: 3\nby: 5488923\n", encoding="utf-8")
        profile = cat.load_profile("itam", repo_root=repo)
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("transcript"), policy, assets={}, profile=profile, repo_root=repo
        )
        structure = catalog["approved_profile"]
        assert structure["version"] == 3 and structure["source"] == "manual"
        assert structure["allowed_tables"] == ["TCDMSIF80"]
        assert structure["entity_keys"]["keys"][0]["column"] == "sevrHostName"
        assert structure["counts"]["code_values"] == {"TCDMSIF80.asstStusDstcd": 3}
        assert structure["counts"]["code_labels"] == {"TCDMSIF80.asstStusDstcd": 2}
        assert structure["counts"]["query_rules"] == 1 and structure["other_keys"] == [
            "custom_note"
        ]
        rel = [
            r
            for r in catalog["tables"]["TCDMSIF79"]["relations"]
            if r["kind"] == "approved_profile"
        ]
        assert rel == [
            {
                "from": "TCDMSIF79",
                "to": "TCDMSIF80",
                "kind": "approved_profile",
                "origin": "same_key",
                "overlap": 0.95,
                "columns": [["sevrHostName", "sevrHostName"], ["iPCtnt", "iPCtnt"]],
            }
        ]
        assert catalog["tables"]["TCDMSIF80"]["allowed"] is True
        assert catalog["tables"]["TCDMSIF79"]["allowed"] is False  # 거르지 않고 표시만
        assert catalog["tables"]["TCDMSIF80"]["entity_key_table"] is True
        text = yaml.safe_dump(catalog, allow_unicode=True)
        for leaked in ("5488923", "홍길동", "운영", "폐기", "9099"):
            assert leaked not in text, leaked

    def test_all_108_tables_are_kept(self, tmp_path: Path, policy: cat.ColumnPolicy) -> None:
        """운영 108테이블 전부(사용자 확정) — 정책·허용 테이블과 무관하게 거르지 않는다."""
        families = ("TCDMSAM", "TCDMSBR", "TCDMSCM", "TCDMSGT", "TCDMSHR", "TCDMSIF")
        tables = {
            f"{family}{i:02d}": [
                _col("groupCoCd", pk=True),
                _col(f"col{i}Nm"),
                _col("rspblPsnEmnm"),
                _col("sysRegiUno"),
            ]
            for family in families
            for i in range(1, 19)
        }
        cache = _tab_cache(tmp_path / "c108", tables)
        profile = {"source": "manual", "allowed_tables": ["TCDMSIF01", "TCDMSIF02"]}
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("schema_cache", cache_dir=cache),
            policy,
            assets={},
            profile=profile,
        )
        assert catalog["summary"]["tables"] == 108 == len(catalog["tables"])
        assert catalog["summary"]["columns"] == 108 * 4
        assert sum(t["allowed"] for t in catalog["tables"].values()) == 2
        hr = {c["name"]: c for c in catalog["tables"]["TCDMSHR07"]["columns"]}
        assert hr["rspblPsnEmnm"]["log_policy"] == "pii"  # 정책에 있는 이름
        assert hr["col7Nm"]["log_policy"] == "unclassified"  # 정책 밖 = 기본 거부
