"""plans/140 W2 — ITAM 벤치 반출 확장 (D-311 ①②).

가짜 `StructureStore`(스냅샷 · P1 초안 · 설명 초안 · DDL 주석)로 카탈로그 계약 칸 · 치환 코드값
`code_samples.yaml` · 누출 관문 `code_original` · 폴백·경고 · 에러 범주화를 고정한다. 실 Redis·DB·
LLM 0. 픽스처 값은 전부 합성이다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import RESULTS_ROOT, TRANSCRIPT_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import code_samples as cs
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.itam_bench import substitute as sb

_RAW_SQL_ERROR = "ProgrammingError: SELECT DISTINCT 상태코드 FROM 자산마스터 WHERE x='가상비밀값'"

# --- 합성 픽스처 -------------------------------------------------------------------


def _snapshot() -> dict[str, Any]:
    return {
        "snapshot": {
            "tables": {
                "자산마스터": {
                    "schema": "INST1",
                    "columns": {
                        "자산번호": {"type": "varchar", "nullable": False, "primary_key": True},
                        "상태코드": {"type": "char", "nullable": True, "primary_key": False},
                        "사용여부": {"type": "char", "nullable": True, "primary_key": False},
                        "담당자명": {"type": "varchar", "nullable": True, "primary_key": False},
                        "금액구분": {"type": "char", "nullable": True, "primary_key": False},
                        "chrgEmnm": {"type": "varchar", "nullable": True, "primary_key": False},
                        "등급": {"type": "char", "nullable": True, "primary_key": False},
                        "지역코드": {"type": "char", "nullable": True, "primary_key": False},
                        "기호구분": {"type": "char", "nullable": True, "primary_key": False},
                    },
                    "foreign_keys": [],
                },
                "자산이력": {
                    "schema": "INST1",
                    "columns": {
                        "이력번호": {"type": "int", "nullable": False, "primary_key": True},
                        "자산번호": {"type": "varchar", "nullable": True, "primary_key": False},
                        "이력코드": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "foreign_keys": ["자산번호->자산마스터.자산번호"],
                },
                "공통코드": {
                    "schema": "INST1",
                    "columns": {
                        "코드": {"type": "varchar", "nullable": False, "primary_key": True},
                        "코드명": {"type": "varchar", "nullable": True, "primary_key": False},
                    },
                    "foreign_keys": [],
                },
            }
        },
        "hash": "snaphash-current",
        "taken_at": "2026-10-07T00:00:00",
    }


def _column(key: str, **over: Any) -> dict[str, Any]:
    base = {
        "key": key,
        "candidate": "code",
        "code": True,
        "distinct": 3,
        "truncated": False,
        "total": 120,
        "date8": 0.0,
        "datetime14": 0.0,
        "ipv4": 0.0,
        "hostname": 0.0,
        "multi_value": 0.0,
        "mixed_case": False,
        "flag": [],
        "entity_key": None,
        "error": None,
    }
    base.update(over)
    return base


def _draft(snapshot_hash: str = "snaphash-current") -> dict[str, Any]:
    return {
        "draft_id": "d0000000aaaa",
        "created_at": "2026-10-07T01:00:00",
        "status": "pending",
        "kind": "profile",
        "engine": "mariadb",
        "snapshot_hash": snapshot_hash,
        "description_draft_id": "desc00000001",
        "assets": {
            "code_values": {
                "자산마스터.상태코드": ["A01", "B02", "C03"],
                "자산이력.이력코드": ["A01", "Z9"],
                "자산마스터.사용여부": ["N", "Y"],
                "자산마스터.담당자명": ["가상인물"],
                "자산마스터.금액구분": ["H1", "L1"],
                "자산마스터.chrgEmnm": ["가상직원"],
                "자산마스터.등급": [str(d) for d in range(10)],
                "자산마스터.지역코드": ["1023", "2381", "4410", "6512", "7734"],
                "자산마스터.기호구분": ["*", "-"],
            },
            "code_labels": {
                "자산마스터.상태코드": {"A01": "가동중", "B02": "폐기예정", "C03": "수리"},
                "자산이력.이력코드": {"A01": "신규", "Z9": "기타"},
            },
            "relationships": [],
            "table_definitions": {},
        },
        "evidence": {
            "columns": [
                _column("자산마스터.상태코드"),
                _column("자산이력.이력코드", distinct=2),
                _column("자산마스터.사용여부", distinct=2, flag=["N", "Y"]),
                _column("자산마스터.담당자명", candidate="format", code=False, distinct=None),
                _column(
                    "자산마스터.자산번호", candidate="format", code=False, error=_RAW_SQL_ERROR
                ),
                _column("자산마스터.등급", distinct=10),
                _column("자산마스터.지역코드", distinct=5, error="예산 초과"),
            ],
            "relationships": [
                {
                    "child": "자산이력",
                    "parent": "자산마스터",
                    "child_columns": ["자산번호"],
                    "parent_columns": ["자산번호"],
                    "origin": "declared",
                    "overlap": 0.98,
                    "sampled": 200,
                    "accepted": True,
                    "error": None,
                    "unique_parent": None,
                },
                {
                    "child": "공통코드",
                    "parent": None,
                    "child_columns": ["코드"],
                    "parent_columns": [],
                    "origin": "name_match",
                    "overlap": None,
                    "sampled": None,
                    "accepted": False,
                    "error": "부모 유일성 없음",
                    "unique_parent": False,
                },
            ],
            "code_columns": [
                {
                    "key": "자산마스터.상태코드",
                    "distinct": 3,
                    "labels": 3,
                    "labels_from": "code_table:공통코드",
                },
                {"key": "자산이력.이력코드", "distinct": 2, "labels": 2, "labels_from": "comment"},
            ],
            "allowed_tables": [
                {
                    "table": "자산마스터",
                    "family": None,
                    "rows": 1500,
                    "connected": True,
                    "comment": "자산 원장(초안 주석)",
                },
                {
                    "table": "자산이력",
                    "family": None,
                    "rows": 9000,
                    "connected": True,
                    "comment": None,
                },
                {
                    "table": "공통코드",
                    "family": None,
                    "rows": 40,
                    "connected": False,
                    "comment": None,
                },
            ],
            "budget": {
                "limit": 600,
                "used": 412,
                "skipped": 3,
                "skipped_sample": ["overlap:자산이력->자산마스터"],
                "requested": 790,
                "cap": 1200,
            },
            "offline": None,
        },
    }


class FakeStore:
    """`StructureStore` 읽기 API 만 흉내 낸다(쓰기 메서드 없음 — 쓰면 AttributeError)."""

    def __init__(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        drafts: list[dict[str, Any]] | None = None,
        comments: dict[str, str] | None = None,
        descriptions: dict[str, Any] | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._drafts = drafts or []
        self._comments = comments or {}
        self._descriptions = descriptions or {}

    async def load_snapshot(self, db_id: str) -> dict[str, Any] | None:
        return self._snapshot

    async def load_ddl_comments(self, db_id: str) -> dict[str, str]:
        return dict(self._comments)

    async def list_asset_drafts(self, db_id: str) -> list[dict[str, Any]]:
        return list(self._drafts)

    async def get_description_draft(self, db_id: str, draft_id: str) -> dict[str, Any] | None:
        return self._descriptions.get(draft_id)


def _store(**over: Any) -> FakeStore:
    kwargs: dict[str, Any] = {
        "snapshot": _snapshot(),
        "drafts": [
            _draft(),
            {"draft_id": "old", "kind": "profile", "assets": {}, "evidence": {}},
        ],
        "comments": {
            "자산마스터": "자산 원장",
            "자산마스터.상태코드": "자산 상태 1:정상, 2:장애, 3:폐기",
        },
        "descriptions": {
            "desc00000001": {
                "descriptions": {"자산이력": {"자산이력.이력코드": "이력 종류"}},
                "origin": "comment",
            }
        },
    }
    kwargs.update(over)
    return FakeStore(**kwargs)


@pytest.fixture()
def policy() -> cat.ColumnPolicy:
    return cat.ColumnPolicy(
        db_id="itam",
        scope="closed",
        tables={
            "자산마스터": {"담당자명": "pii", "금액구분": "amount", "상태코드": "general"},
        },
        canary_literals=("가상카나리아",),
    )


def _originals() -> rd.CodeOriginals:
    return rd.CodeOriginals(cs.original_values(_draft()))


def _gate(policy: cat.ColumnPolicy, **over: Any) -> rd.LeakGate:
    kwargs: dict[str, Any] = {
        "policy": policy,
        "vault": rd.PiiVault.from_policy(policy),
        "user_values": {"login_id": "user9999"},
        "code_originals": _originals(),
    }
    kwargs.update(over)
    return rd.LeakGate(**kwargs)


def _samples(policy: cat.ColumnPolicy, *, reject: Any = None) -> dict[str, Any]:
    gate = _gate(policy)
    return cs.build_code_samples(
        _draft(),
        policy,
        db_id="itam",
        run_id="20261007-010000",
        comments={},
        originals=_originals(),
        reject=reject or (lambda text: bool(gate.rules(text, schema_section=False))),
    )


# --- 카탈로그 입력 `structure_store` (W2-1·2) -----------------------------------------


class TestStructureStoreCatalog:
    def _catalog(self, policy: cat.ColumnPolicy, store: FakeStore | None = None) -> dict[str, Any]:
        schema = cat.load_schema_source("structure_store", store=store or _store())
        return cat.build_schema_catalog(schema, policy, assets={"p1": cat.p1_asset(_draft())})

    def test_contract_fields(self, policy: cat.ColumnPolicy) -> None:
        catalog = self._catalog(policy)
        assert catalog["source"] == "structure_store"
        assert catalog["p1"] == {
            "draft_id": "d0000000aaaa",
            "created_at": "2026-10-07T01:00:00",
            "status": "pending",
            "snapshot_hash": "snaphash-current",
            "engine": "mariadb",
            "offline": False,
            "budget": {"limit": 600, "used": 412, "skipped": 3, "requested": 790, "cap": 1200},
        }
        assert catalog["p1_fallback"] is None and catalog["p1_warnings"] == []
        master = catalog["tables"]["자산마스터"]
        assert master["key"] == ["자산번호"]
        assert master["rows_estimate"] == 1500
        assert (master["meaning"], master["meaning_source"]) == ("자산 원장", "db_comment")
        # DDL 주석이 없으면 P1 테이블 주석
        assert catalog["tables"]["자산이력"]["rows_estimate"] == 9000
        cols = {c["name"]: c for c in master["columns"]}
        status = cols["상태코드"]
        assert status["type"] == "char"
        assert status["meaning_source"] == "db_comment" and status["comment_enum"] == 3
        assert status["profile"] == {
            "candidate": "code",
            "code": True,
            "distinct": 3,
            "truncated": False,
            "total": 120,
            "formats": {
                "date8": 0.0,
                "datetime14": 0.0,
                "ipv4": 0.0,
                "hostname": 0.0,
                "multi_value": 0.0,
            },
            "mixed_case": False,
            "flag": [],
            "entity_key": None,
            "error": None,
        }
        assert "comment_enum" not in cols["사용여부"]  # 0이면 생략
        assert cols["사용여부"]["profile"]["flag"] == ["N", "Y"]
        # 설명 초안(주석 유래)도 DB 주석이다
        history = {c["name"]: c for c in catalog["tables"]["자산이력"]["columns"]}
        assert history["이력코드"]["meaning"] == "이력 종류"
        assert history["이력코드"]["meaning_source"] == "db_comment"
        summary = catalog["summary"]
        assert summary["comment_enum_columns"] == 1
        assert summary["p1_profiled_columns"] == 7
        assert summary["p1_code_columns"] == 5
        assert summary["relations"]["p1"] == 2 and summary["relations"]["declared"] == 1

    def test_p1_relations(self, policy: cat.ColumnPolicy) -> None:
        catalog = self._catalog(policy)
        p1 = [r for r in catalog["tables"]["자산이력"]["relations"] if r["kind"] == "p1"]
        assert p1 == [
            {
                "from": "자산이력",
                "to": "자산마스터",
                "columns": [["자산번호", "자산번호"]],
                "kind": "p1",
                "origin": "declared",
                "overlap": 0.98,
                "sampled": 200,
                "accepted": True,
                "unique_parent": None,
                "error": None,
            }
        ]
        orphan = [r for r in catalog["tables"]["공통코드"]["relations"] if r["kind"] == "p1"]
        assert orphan[0]["to"] is None and orphan[0]["columns"] == [["코드", None]]
        assert orphan[0]["error"] == "부모 유일성 없음"
        declared = [
            r for r in catalog["tables"]["자산이력"]["relations"] if r["kind"] == "declared"
        ]
        assert declared[0]["to"] == "자산마스터"

    def test_errors_are_categories_only(self, policy: cat.ColumnPolicy) -> None:
        catalog = self._catalog(policy)
        cols = {c["name"]: c for c in catalog["tables"]["자산마스터"]["columns"]}
        assert cols["자산번호"]["profile"]["error"] == "조회 실패:ProgrammingError"
        assert cols["지역코드"]["profile"]["error"] == "예산 초과"
        text = yaml.safe_dump(catalog, allow_unicode=True)
        assert "가상비밀값" not in text and "SELECT" not in text
        assert "skipped_sample" not in text and "overlap:자산이력" not in text

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("예산 초과", "예산 초과"),
            ("후보 상한 초과", "후보 상한 초과"),
            ("부모 유일성 없음", "부모 유일성 없음"),
            ("DB 연결 없음 — 값 겹침을 확인하지 못했습니다", "DB 연결 없음"),
            ("허용되지 않는 스키마 식별자: 'x;y'", "식별자 거부"),
            (
                'asyncpg.exceptions.UndefinedTableError: relation "가상"',
                "조회 실패:UndefinedTableError",
            ),
            ("컬럼 수 불일치 '가상값'", "조회 실패:미상"),
            (None, None),
        ],
    )
    def test_error_category(self, raw: str | None, expected: str | None) -> None:
        assert cat.error_category(raw) == expected

    def test_no_code_values_in_catalog(self, policy: cat.ColumnPolicy) -> None:
        text = yaml.safe_dump(self._catalog(policy), allow_unicode=True)
        for original in ("A01", "B02", "가동중", "폐기예정", "가상인물", "가상직원", "6512"):
            assert original not in text

    def test_reads_latest_profile_draft_only(self, policy: cat.ColumnPolicy) -> None:
        import_draft = {"draft_id": "imp", "kind": "import", "assets": {}, "evidence": {}}
        schema = cat.load_schema_source(
            "structure_store", store=_store(drafts=[import_draft, _draft()])
        )
        assert schema["p1"]["draft_id"] == "d0000000aaaa"

    def test_hash_mismatch_warns(self, policy: cat.ColumnPolicy) -> None:
        store = _store(drafts=[_draft(snapshot_hash="older")])
        catalog = self._catalog(policy, store)
        assert catalog["p1_warnings"] == [cat.P1_HASH_MISMATCH]
        report = rp.render_report({"run_id": "r"}, catalog, [])
        assert cat.P1_HASH_MISMATCH in report.split("## 0.")[0]

    def test_snapshot_without_p1_uses_structure_only(self, policy: cat.ColumnPolicy) -> None:
        catalog = self._catalog(policy, _store(drafts=[]))
        assert catalog["source"] == "structure_store"
        assert catalog["p1"] is None and catalog["p1_fallback"] == "P1 초안 없음"
        assert catalog["tables"]["자산마스터"]["key"] == ["자산번호"]
        assert catalog["summary"]["p1_profiled_columns"] == 0
        head = rp.render_report({"run_id": "r"}, catalog, []).split("## 1.")[0]
        assert cat.P1_MISSING_WARNING in head and "P1 초안 없음" in head

    def test_missing_snapshot_falls_back_to_schema_cache(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "itam_schema.json").write_text(
            json.dumps(
                {
                    "_cache_version": 1,
                    "_fingerprint": "x",
                    "_db_id": "itam",
                    "_cached_at": 0.0,
                    "_cached_at_iso": "",
                    "schema": {
                        "tables": {
                            "자산마스터": {
                                "columns": [
                                    {
                                        "name": "자산번호",
                                        "type": "varchar",
                                        "nullable": False,
                                        "primary_key": True,
                                    }
                                ],
                            }
                        },
                        "relationships": [],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        schema = cat.load_schema_source(
            "structure_store", store=_store(snapshot=None), cache_dir=cache
        )
        assert schema["source"] == "schema_cache"
        assert schema["p1"] is None and schema["p1_fallback"] == "스냅샷 없음"
        assert schema["_p1_draft"] is None
        catalog = cat.build_schema_catalog(schema, policy, assets={})
        assert catalog["p1_fallback"] == "스냅샷 없음"
        head = rp.render_report({"run_id": "r"}, catalog, []).split("## 1.")[0]
        assert "**P1 근거 없음 — 자산 없는 기준선**" in head

    def test_redis_unavailable_falls_back(self, tmp_path: Path) -> None:
        class Down:
            async def ensure_connected(self) -> bool:
                return False

            async def disconnect(self) -> None:  # pragma: no cover - owned=False
                raise AssertionError

        store = _store()
        store.redis_cache = Down()  # type: ignore[attr-defined]
        with pytest.raises(FileNotFoundError, match="Redis 연결 불가"):
            cat.load_schema_source("structure_store", store=store, cache_dir=tmp_path)

    def test_redis_error_message_not_leaked(self, tmp_path: Path) -> None:
        class Broken(FakeStore):
            async def load_snapshot(self, db_id: str) -> dict[str, Any] | None:
                raise RuntimeError("가상비밀값 in redis")

        with pytest.raises(FileNotFoundError) as info:
            cat.load_schema_source("structure_store", store=Broken(), cache_dir=tmp_path)
        assert "Redis 읽기 실패:RuntimeError" in str(info.value)
        assert "가상비밀값" not in str(info.value)

    def test_app_store_paths_match_app(self) -> None:
        from types import SimpleNamespace

        cfg = SimpleNamespace(
            schema_cache=SimpleNamespace(backend="file", cache_dir=".cache/schema")
        )
        store = cat.app_structure_store(cfg)
        assert store.redis_cache is None  # 앱도 file 백엔드면 Redis 를 붙이지 않는다
        assert store.backup_root == cat.REPO_ROOT / ".cache" / "structure"
        assert store.profiles_dir == cat.REPO_ROOT / "config" / "db_profiles"

    def test_cli_default_source_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, str] = {}

        def fake_run(args: Any) -> int:
            seen["source"] = args.schema_source
            return 0

        monkeypatch.setattr(cli, "cmd_run", fake_run)
        cli.main(["--run", "--env", "closed"])
        assert seen["source"] == "structure_store"
        cli.main(["--run"])
        assert seen["source"] == "schema_cache"
        cli.main(["--run", "--env", "closed", "--schema-source", "schema_cache"])
        assert seen["source"] == "schema_cache"


# --- 승인 프로필 테이블 정의 반출 (W2-3) ---------------------------------------------


class TestTableDefinitions:
    def _profile(self) -> dict[str, Any]:
        return {
            "source": "manual",
            "table_definitions": {
                "자산마스터": {
                    "group": "원장",
                    "kind": "master",
                    "manages": "자산 1건",
                    "key_columns": ["자산번호"],
                    "related": {"자산이력": "자산번호로 이력"},
                    "notes": "운영 원장",
                    "origin": "manual",
                },
                "자산이력": {"manages": "가상카나리아 담당 이력", "origin": "manual"},
                "공통코드": {"manages": "연락처 010-1234-5678", "origin": "llm"},
            },
        }

    def test_export_and_withhold(self, policy: cat.ColumnPolicy, tmp_path: Path) -> None:
        catalog = cat.build_schema_catalog(
            cat.load_schema_source("structure_store", store=_store()),
            policy,
            assets={},
            profile=self._profile(),
            repo_root=tmp_path,
        )
        structure = catalog["approved_profile"]
        assert structure["table_definitions_withheld"] == 0
        assert "table_definitions" not in structure["other_keys"]
        gate = _gate(policy)
        gated = cat.gate_table_definitions(
            catalog, lambda text: bool(gate.rules(text, schema_section=True))
        )
        definitions = gated["approved_profile"]["table_definitions"]
        assert definitions == {
            "자산마스터": {
                "manages": "자산 1건",
                "kind": "master",
                "key_columns": ["자산번호"],
                "related": {"자산이력": "자산번호로 이력"},
                "notes": "운영 원장",
                "origin": "manual",
                "group": "원장",  # 감사 M-2 — 내부망 편집 묶음도 반출
            }
        }
        assert gated["approved_profile"]["table_definitions_withheld"] == 2
        assert catalog["approved_profile"]["table_definitions_withheld"] == 0  # 원본 불변


# --- 치환 코드값 (W2-4) -----------------------------------------------------------------


def _class(ch: str) -> str:
    if "0" <= ch <= "9":
        return "digit"
    if "A" <= ch <= "Z":
        return "upper"
    if "a" <= ch <= "z":
        return "lower"
    if 0xAC00 <= ord(ch) <= 0xD7A3:
        return "hangul"
    return ch


class TestSubstitution:
    """plans/145 — 치환기는 `substitute.FakeValues`(난수 · 키 없음). 결정성 대신 성질을 단언한다."""

    def test_format_preserved(self) -> None:
        sub = sb.FakeValues(originals=rd.CodeOriginals([]))
        for original in ("Ab-3가나", "07", "10", "A_1.b", "가", "Z9"):
            out = sub.fake_or_none(original)
            assert out is not None and out != original and len(out) == len(original)
            assert [_class(c) for c in out] == [_class(c) for c in original]
        out = sub.fake_or_none("10")
        assert out is not None and out[0] != "0"  # 여러 자리 수의 첫 자리 0 아님 유지

    def test_same_original_same_substitute_and_injective(self) -> None:
        originals = rd.CodeOriginals(["A01", "a01", "B02"])
        sub = sb.FakeValues(originals=originals)
        first = sub.fake_or_none("A01")
        assert first is not None and sub.fake_or_none("A01") == first
        # ASCII 대소문자만 다른 원값은 같은 가짜 값에 원값 대소문자를 다시 입힌다(plans/145 A2)
        assert sub.fake_or_none("a01") == first.lower()
        other = sub.fake_or_none("B02")
        assert other is not None and other.casefold() != first.casefold()
        assert all(not originals.hit(v) for v in (first, other))

    def test_reject_redraws(self) -> None:
        rejected: list[str] = []

        def reject_first(text: str) -> bool:
            if not rejected:
                rejected.append(text)
                return True
            return text == rejected[0]  # 재추첨이 같은 후보를 다시 뽑아도 거부(간헐 실패)

        out = sb.FakeValues(originals=_originals(), reject=reject_first).fake_or_none("C03")
        assert rejected and out is not None and out != rejected[0]

    def test_gate_rule_collision_redraws(self, policy: cat.ColumnPolicy) -> None:
        """첫 후보가 카나리아(기존 관문 규칙)와 같으면 재추첨한다 — 관문은 그대로 통과."""
        gates: list[rd.LeakGate] = []

        def reject(text: str) -> bool:
            if not gates:  # 첫 후보를 카나리아로 심은 관문
                trapped = cat.ColumnPolicy(
                    db_id="itam", scope="closed", tables=policy.tables, canary_literals=(text,)
                )
                gates.append(_gate(trapped, vault=rd.PiiVault.from_policy(trapped)))
            return bool(gates[0].rules(text, schema_section=False))

        out = sb.FakeValues(originals=_originals(), reject=reject).fake_or_none("C03")
        assert out is not None and not gates[0].rules(out, schema_section=False)

    def test_mapping_not_exposed(
        self, policy: cat.ColumnPolicy, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        sub = sb.FakeValues(originals=_originals())
        fake = sub.fake_or_none("C03")
        assert fake is not None and "C03" not in repr(sub) and fake not in repr(sub)
        text = yaml.safe_dump(_samples(policy), allow_unicode=True)
        assert "C03" not in text and "C03" not in caplog.text
        assert repr(_originals()).startswith("<CodeOriginals") and "A01" not in repr(_originals())


class TestCodeSamples:
    def test_shape_and_summary(self, policy: cat.ColumnPolicy) -> None:
        doc = _samples(policy)
        assert list(doc) == ["db_id", "run_id", "p1_draft_id", "note", "summary", "columns"]
        assert doc["db_id"] == "itam" and doc["p1_draft_id"] == "d0000000aaaa"
        assert doc["note"] == cs.NOTE
        assert doc["summary"] == {
            "columns": 9,
            "substituted": 3,
            "exhausted": 2,
            "flag": 1,
            "excluded": {
                "pii": 1,
                "free_text": 0,
                "amount": 1,
                "network": 0,
                "person_hint": 1,
            },
        }
        columns = doc["columns"]
        # 제외 컬럼은 이름도 싣지 않는다
        assert set(columns) == {
            "자산마스터.상태코드",
            "자산이력.이력코드",
            "자산마스터.사용여부",
            "자산마스터.등급",
            "자산마스터.지역코드",
            "자산마스터.기호구분",
        }
        assert columns["자산마스터.사용여부"] == {"distinct": 2, "substitution": "flag"}
        # 1자리 숫자 10개 전부 — 원 집합 밖 1자리 숫자가 없다
        assert columns["자산마스터.등급"] == {"distinct": 10, "substitution": "exhausted"}
        # 바꿀 글자가 없는 값
        assert columns["자산마스터.기호구분"] == {"distinct": 2, "substitution": "exhausted"}
        region = columns["자산마스터.지역코드"]
        assert region["substitution"] == "ok" and len(region["values"]) == 5
        assert all(len(v) == 4 and v.isdigit() and v[0] != "0" for v in region["values"])

    def test_joined_values_tripping_gate_exhaust_column(
        self, policy: cat.ColumnPolicy
    ) -> None:
        """관문은 잎을 줄바꿈으로 이어 붙여서도 본다 — 이어 붙인 글이 걸리면 그 컬럼은 값 없이."""
        gate = _gate(policy)

        def reject(text: str) -> bool:
            lines = text.split("\n")
            joined_digits = len(lines) >= 4 and all(x.isdigit() and len(x) == 4 for x in lines)
            return joined_digits or bool(gate.rules(text, schema_section=False))

        columns = _samples(policy, reject=reject)["columns"]
        assert columns["자산마스터.지역코드"] == {"distinct": 5, "substitution": "exhausted"}
        assert columns["자산마스터.상태코드"]["substitution"] == "ok"

    def test_values_and_labels(self, policy: cat.ColumnPolicy) -> None:
        columns = _samples(policy)["columns"]
        status = columns["자산마스터.상태코드"]
        history = columns["자산이력.이력코드"]
        assert status["substitution"] == "ok" and status["distinct"] == 3
        assert status["values"] == sorted(status["values"]) and len(set(status["values"])) == 3
        # 공통코드 라벨 쌍 — 코드는 같은 컬럼 치환값과 같다
        assert [row[0] for row in status["labels"]] == status["values"]
        assert all(len(row) == 2 for row in status["labels"])
        # labels_from 이 comment 면 라벨 없음
        assert "labels" not in history
        # 같은 원값(A01)은 컬럼이 달라도 같은 치환값
        assert len(set(status["values"]) & set(history["values"])) == 1
        originals = _originals()
        leaves = set(status["values"]) | set(history["values"]) | {r[1] for r in status["labels"]}
        # 원값 7개(A01·B02·C03·Z9 + 라벨 3) → 치환값 7개(casefold 기준 단사)
        assert len({v.casefold() for v in leaves}) == len(leaves) == 7
        assert not any(originals.hit(v) for v in leaves)
        for value in leaves:
            assert {_class(c) for c in value} <= {"digit", "upper", "lower", "hangul"}

    def test_no_originals_frequency_or_hash(
        self, policy: cat.ColumnPolicy
    ) -> None:
        """원값 비노출 — 관문 `code_original`과 같은 기준이다(plans/145 W1 교정).

        3자(`CODE_ORIGINAL_MIN_SUBSTRING`) 이상 원값은 부분 문자열로도 나오면 안 되고, 그보다
        짧은 원값은 잎·키와 같음만 금지다(2자 원값이 무작위 가짜 값 안에 우연히 들어가는 것은
        누출이 아니다 — 예: 원값 `H1` · 가짜 값 `H14`).
        """
        doc = _samples(policy)
        text = yaml.safe_dump(doc, allow_unicode=True)

        def leaves(node: Any) -> list[str]:
            if isinstance(node, dict):
                return [str(k) for k in node] + [x for v in node.values() for x in leaves(v)]
            if isinstance(node, (list, tuple)):
                return [x for v in node for x in leaves(v)]
            return [] if node is None else [str(node)]

        folded_leaves = {leaf.casefold() for leaf in leaves(doc)}
        for original in (
            "A01",
            "B02",
            "C03",
            "Z9",
            "가동중",
            "폐기예정",
            "수리",
            "가상인물",
            "가상직원",
            "1023",
            "H1",
            "담당자명",
            "금액구분",
            "chrgEmnm",
        ):
            if len(original) >= rd.CODE_ORIGINAL_MIN_SUBSTRING:
                assert original.casefold() not in text.casefold()
            else:
                assert original.casefold() not in folded_leaves
        assert "freq" not in text and "hash" not in text and "count" not in text


# --- 누출 관문 `code_original` (W2-5) --------------------------------------------------


class TestCodeOriginalGate:
    def _text(self, doc: dict[str, Any]) -> str:
        return yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)

    def test_clean_samples_pass(self, policy: cat.ColumnPolicy) -> None:
        assert _gate(policy).check({rd.CODE_SAMPLES_FILE: self._text(_samples(policy))}) == []

    @pytest.mark.parametrize("injected", ["a01", "XA01Q", "가동중이다", "z9"])
    def test_injected_original_blocks_write(
        self, tmp_path: Path, policy: cat.ColumnPolicy, injected: str
    ) -> None:
        doc = _samples(policy)
        doc["columns"]["자산마스터.상태코드"]["values"][0] = injected
        ok, violations = rd.write_gated(
            tmp_path / "run", {rd.CODE_SAMPLES_FILE: self._text(doc)}, _gate(policy)
        )
        assert not ok
        assert [v["rule"] for v in violations] == ["code_original"]
        position = list(doc["columns"]).index("자산마스터.상태코드")
        assert violations[0]["field"] == f"columns[#{position}].values[0]"
        assert sorted(p.name for p in (tmp_path / "run").iterdir()) == ["leak_check.json"]
        check = json.loads((tmp_path / "run" / "leak_check.json").read_text(encoding="utf-8"))
        assert check["rules"][-1] == "code_original" and injected not in json.dumps(check)

    def test_structural_and_key_leaves_ignored(
        self, policy: cat.ColumnPolicy
    ) -> None:
        # 2자리 원값 'Z9' 와 같은 이름의 컬럼 키 · 원값 '1' 과 같은 distinct 수는 위반이 아니다
        gate = _gate(policy, code_originals=rd.CodeOriginals(["Z9", "3", "ok", "flag"]))
        doc = {
            "db_id": "itam",
            "note": cs.NOTE,
            "summary": {"columns": 3},
            "columns": {"T.Z9": {"distinct": 3, "substitution": "ok", "values": ["QQ"]}},
        }
        assert gate.check({rd.CODE_SAMPLES_FILE: self._text(doc)}) == []

    def test_missing_originals_fails_closed(
        self, policy: cat.ColumnPolicy
    ) -> None:
        gate = _gate(policy, code_originals=None)
        violations = gate.check({rd.CODE_SAMPLES_FILE: self._text(_samples(policy))})
        assert [v["rule"] for v in violations] == ["code_original"]
        # 다른 파일에는 code_original 을 적용하지 않는다
        assert gate.check({"report.md": "A01 가동중\n"}) == []


# --- 산출물 묶음 · run.json · 리포트 · --compare/--sync (W2-6·7) ----------------------


class TestStaging:
    RUN = {
        "run_id": "20261007-010000",
        "env": "closed",
        "profile": "tier2_intent",
        "tier": "intent_orchestration",
        "planes": {"worker": "mlx", "orchestrator": "mlx"},
        "policy_scope": "closed",
        "scenarios": 0,
        "repeat": 1,
        "sql_observed_turns": 0,
        "judged": True,
    }

    def _staged(self, policy: cat.ColumnPolicy, tmp_path: Path) -> tuple[dict[str, str], Any]:
        schema = cat.load_schema_source("structure_store", store=_store())
        assets = {"profile": None, "p1": cat.p1_asset(schema["_p1_draft"])}
        catalog = cat.build_schema_catalog(schema, policy, assets=assets, repo_root=tmp_path)
        run = {**self.RUN, "assets": catalog["assets"]}
        return cli.stage_gated(
            run_meta=run,
            catalog_doc=catalog,
            records=[],
            policy=policy,
            vault=rd.PiiVault.from_policy(policy),
            user_values={"login_id": "user9999"},
            p1_draft=schema["_p1_draft"],
        )

    def test_six_files_written(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        staged, gate = self._staged(policy, tmp_path)
        ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
        assert ok and violations == []
        names = sorted(p.name for p in (tmp_path / "run").iterdir())
        assert names == [
            "code_samples.yaml",
            "leak_check.json",
            "report.md",
            "run.json",
            "schema_catalog.yaml",
            "trace.jsonl",
        ]
        run = json.loads((tmp_path / "run" / "run.json").read_text(encoding="utf-8"))
        p1 = run["assets"]["p1"]
        assert set(p1) == {"draft_id", "fingerprint", "created_at", "status", "budget"}
        assert len(p1["fingerprint"]) == 12 and p1["draft_id"] == "d0000000aaaa"
        assert p1["budget"] == {
            "limit": 600,
            "used": 412,
            "requested": 790,
            "cap": 1200,
            "skipped": 3,
        }
        check = json.loads((tmp_path / "run" / "leak_check.json").read_text(encoding="utf-8"))
        assert check["rules"] == list(rd.GATE_RULES) and "code_original" in check["rules"]
        report = (tmp_path / "run" / "report.md").read_text(encoding="utf-8")
        assert "## 0. P1 근거" in report and "주석 코드 열거 보유 컬럼 | 1" in report
        assert "치환 3 · 고갈 2 · 플래그 1" in report
        assert "P1 근거 없음" not in report
        everything = "".join(p.read_text(encoding="utf-8") for p in (tmp_path / "run").iterdir())
        for original in ("가동중", "폐기예정", "가상인물", "가상직원", "가상비밀값", "A01"):
            assert original not in everything

    def test_fingerprint_tracks_draft(self) -> None:
        a = cat.p1_asset(_draft())
        changed = _draft()
        changed["assets"]["code_values"]["자산이력.이력코드"] = ["A01"]
        assert a["fingerprint"] != cat.p1_asset(changed)["fingerprint"]
        assert cat.p1_asset(None) is None

    def test_no_p1_no_code_samples(self, tmp_path: Path, policy: cat.ColumnPolicy) -> None:
        staged = cli.build_artifacts(
            run_meta=self.RUN, catalog_doc={"source": "schema_cache", "assets": {}}, records=[]
        )
        assert rd.CODE_SAMPLES_FILE not in staged

    def test_compare_and_sync_read_old_and_new_runs(
        self, tmp_path: Path, policy: cat.ColumnPolicy
    ) -> None:
        old = RESULTS_ROOT / "20261006-152938"
        if not (old / "run.json").is_file():
            pytest.skip("1회차 반출 run 없음")
        staged, gate = self._staged(policy, tmp_path)
        new = tmp_path / "new"
        assert rd.write_gated(new, staged, gate)[0]
        text = rp.compare_runs(old, new)
        assert "`p1`" in text  # 새 자산 칸이 바뀐 자산으로 잡힌다
        assert rp.compare_runs(new, old).startswith("# 비교")
        closed_policy = cat.load_policy(cli.CLOSED_POLICY_PATH)
        assert rp.sync_report(new, transcript_path=TRANSCRIPT_PATH, policy=closed_policy)
        assert rp.sync_report(old, transcript_path=TRANSCRIPT_PATH, policy=closed_policy)
