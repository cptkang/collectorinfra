"""plans/140 감사 — 정확성: W1 식별자 인용 · W2 반출 → W3 빌더 종단 계약 · 1회차 산출 (D-311).

- W1: `snapshot_identifier`가 인용 문자·구분자·보이지 않는 문자가 든 이름을 거부하는지(식별자 주입).
- W2 → W3: 가짜 `StructureStore`로 6파일 반출을 tmp에 만들고, 그 run으로 빌더 `build`(쓰기 없음)가
  `entity_keys`·`relationships`·`query_rules`를 실제로 만드는지(2회차 모양 재현).
- 1회차 커밋 프로필: `read_current_profile` 로드 · 108/98 · `source: manual` ·
  environment·query_guide 없음.

감사 결함(M-2·L-4)의 재현은 교정 뒤 일반 통과 테스트로 남겼다. 실 Redis·DB·LLM 0 · 픽스처 값은 합성.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import REPO_ROOT
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import redact as rd
from src.domain import schema_inference as inference
from src.schema_cache import asset_generation_service as ags
from src.schema_cache.db_structure_service import build_code_value_sql, snapshot_identifier
from src.schema_cache.structure_store import StructureStore

# --- W1 식별자 인용 ---------------------------------------------------------------------

_ENGINES = ("mariadb", "mysql", "postgresql", "db2")


class TestSnapshotIdentifier:
    @pytest.mark.parametrize(
        "bad",
        [
            "상태`코드", '상태"코드', "상태'코드", "상태 코드", "상태;코드", "상태\n", "상태\t코드",
            "상태--", "상태/*x*/", "상태.코드", "상태)코드", "ㅅㅏㅇ태", "상태​", "ａ상태",
            "상태\\", "", "1상태",
        ],
    )
    @pytest.mark.parametrize("engine", _ENGINES)
    def test_injection_shapes_rejected(self, bad: str, engine: str) -> None:
        with pytest.raises(ValueError):
            snapshot_identifier(bad, engine)

    @pytest.mark.parametrize(
        ("engine", "quote"),
        [("mariadb", "`"), ("mysql", "`"), ("postgresql", '"'), ("db2", '"')],
    )
    def test_hangul_with_dollar_hash_quoted(self, engine: str, quote: str) -> None:
        assert snapshot_identifier("자산$번호#2", engine) == f"{quote}자산$번호#2{quote}"

    @pytest.mark.parametrize(
        ("table", "column"),
        [("자산`; DROP TABLE x; --", "상태코드"), ("자산", "상태코드` FROM y; --")],
    )
    def test_injection_rejected_in_sql_builder(self, table: str, column: str) -> None:
        with pytest.raises(ValueError):
            build_code_value_sql(
                table, column, engine="mariadb", db_schema=None, table_schema=None, limit=51
            )

    def test_hangul_sql_passes_guard(self) -> None:
        sql = build_code_value_sql(
            "자산", "상태코드", engine="mariadb", db_schema=None, table_schema=None, limit=51
        )
        assert "`자산`" in sql and "`상태코드`" in sql


# --- W2 → W3 종단 -----------------------------------------------------------------------


class _Store:
    def __init__(self, snapshot: dict[str, Any], draft: dict[str, Any], comments: dict[str, str]):
        self._snapshot, self._draft, self._comments = snapshot, draft, comments

    async def load_snapshot(self, db_id: str) -> dict[str, Any]:
        return self._snapshot

    async def load_ddl_comments(self, db_id: str) -> dict[str, str]:
        return dict(self._comments)

    async def list_asset_drafts(self, db_id: str) -> list[dict[str, Any]]:
        return [self._draft]

    async def get_description_draft(self, db_id: str, draft_id: str) -> None:
        return None


def _evidence_row(key: str, values: list[str], *, candidate: str = "format") -> dict[str, Any]:
    """P1 `_column_evidence`를 그대로 불러 만든 컬럼 근거(원값 → 비율)."""
    item = {
        "candidate": candidate, "code": candidate == "code", "values": values,
        "distinct": len(set(values)), "truncated": False,
        "profile": inference.classify_values(values), "error": None,
    }
    table, _, column = key.rpartition(".")
    kind = inference.entity_key_kind(column, None, item["profile"])
    return ags._column_evidence(key, item, kind)


def _hosts(n: int) -> list[str]:
    return [f"srv{i:03d}.example.internal" for i in range(n)]


def _ips(n: int) -> list[str]:
    return [f"10.0.{i // 200}.{i % 200 + 1}" for i in range(n)]


def _ymd(n: int) -> list[str]:
    return [f"2026{(i % 12) + 1:02d}{(i % 28) + 1:02d}" for i in range(n)]


def _store(*, date_values: list[str] | None = None) -> _Store:
    snapshot = {
        "hash": "h2",
        "snapshot": {
            "tables": {
                "t_srv": {
                    "columns": {
                        "srv_id": {"type": "varchar", "nullable": False, "primary_key": True},
                        "host_name": {"type": "varchar", "nullable": True},
                        "ip_addr": {"type": "varchar", "nullable": True},
                        "reg_ymd": {"type": "char", "nullable": True},
                    },
                    "foreign_keys": [],
                },
                "t_app": {
                    "columns": {
                        "app_id": {"type": "varchar", "nullable": True},
                        "srv_id": {"type": "varchar", "nullable": True},
                    },
                    "foreign_keys": [],
                },
            }
        },
    }
    draft = {
        "draft_id": "audit0000003", "kind": "profile", "status": "pending",
        "created_at": "2026-10-07T00:00:00", "engine": "mariadb", "snapshot_hash": "h2",
        "assets": {"code_values": {}, "code_labels": {}},
        "evidence": {
            "columns": [
                _evidence_row("t_srv.host_name", _hosts(100)),
                _evidence_row("t_srv.ip_addr", _ips(100)),
                _evidence_row("t_srv.reg_ymd", date_values or _ymd(100)),
            ],
            "code_columns": [],
            "relationships": [
                {"child": "t_app", "parent": "t_srv", "child_columns": ["srv_id"],
                 "parent_columns": ["srv_id"], "origin": "name_match", "overlap": 0.97,
                 "sampled": 200, "accepted": True, "error": None, "unique_parent": True},
            ],
            "allowed_tables": [
                {"table": "t_srv", "rows": 100, "comment": None},
                {"table": "t_app", "rows": 50, "comment": None},
            ],
            "budget": {"limit": 400, "used": 5, "skipped": 0, "requested": 5, "cap": 2000},
        },
    }
    comments = {"t_srv.host_name": "서버 호스트명"}
    return _Store(snapshot, draft, comments)


_SEED = {
    "db_id": "itam",
    "tables": {
        "t_srv": {"group": "서버", "kind": "현행", "manages": "서버 원장(시드)"},
        "t_app": {"group": "앱", "kind": "현행", "manages": "앱 원장(시드)"},
    },
}


def _export(tmp_path: Path, store: _Store, *, profile: dict[str, Any]) -> Path:
    policy = cat.ColumnPolicy(db_id="itam", scope="closed", tables={})
    schema = cat.load_schema_source("structure_store", store=store)
    draft = schema["_p1_draft"]
    catalog = cat.build_schema_catalog(
        schema, policy, assets={"p1": cat.p1_asset(draft)}, profile=profile,
        repo_root=tmp_path,
    )
    run = {"run_id": "audit-run", "env": "closed", "assets": catalog["assets"]}
    staged, gate = cli.stage_gated(
        run_meta=run, catalog_doc=catalog, records=[], policy=policy,
        vault=rd.PiiVault.from_policy(policy), user_values={}, p1_draft=draft,
    )
    run_dir = tmp_path / "results" / "20261107-000000"
    ok, violations = rd.write_gated(run_dir, staged, gate)
    assert ok, violations
    return run_dir


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    seed = repo / ba.SEED_DEFINITIONS_REL
    seed.parent.mkdir(parents=True)
    seed.write_text(yaml.safe_dump(_SEED, allow_unicode=True), encoding="utf-8")
    return repo


_PROFILE = {"source": "manual", "allowed_tables": ["t_srv", "t_app"]}


def _built_profile(tmp_path: Path, store: _Store, profile: dict[str, Any] = _PROFILE) -> dict:
    run_dir = _export(tmp_path, store, profile=profile)
    result = ba.build(run_dir, repo_root=_repo(tmp_path), generated_at="T")
    return yaml.safe_load(result["files"][str(ba.PROFILE_REL)])


def test_export_feeds_builder_end_to_end(tmp_path: Path) -> None:
    profile = _built_profile(tmp_path, _store())
    assert profile["entity_keys"] == {
        "entity": "server", "table": "t_srv",
        "keys": [
            {"type": "hostname", "column": "host_name", "priority": 1, "compare": "casefold"},
            {"type": "ip", "column": "ip_addr", "priority": 2},
        ],
    }
    assert profile["relationships"] == [
        {"from": "t_app.srv_id", "to": "t_srv.srv_id", "origin": "name_match", "overlap": 0.97}
    ]
    assert len(profile["query_rules"]) == 1 and "`t_srv.reg_ymd`" in profile["query_rules"][0]
    assert profile["allowed_tables"] == ["t_srv", "t_app"]
    assert "environment" not in profile and "query_guide" not in profile


def test_builder_rules_match_p1_rules_on_same_values(tmp_path: Path) -> None:
    """같은 값 표본이면 P1(원값 비율)과 빌더(반출 비율)의 쿼리 규칙이 같다 — 일반 경우."""
    values = _ymd(100)
    p1_rules, _ = inference.rules_and_entity_candidates({
        "t_srv.reg_ymd": {"profile": inference.classify_values(values), "comment": None},
    })
    built = _built_profile(tmp_path, _store(date_values=values))
    assert built["query_rules"] == p1_rules


def test_builder_rules_match_p1_rules_at_ratio_boundary(tmp_path: Path) -> None:
    """감사 L-4 — 근거 비율은 내림이라 경계(379/399 = 0.94987 < 0.95)에서 빌더가 P1보다 관대하지
    않다."""
    values = _ymd(379) + [f"X{i:07d}" for i in range(20)]
    p1_rules, _ = inference.rules_and_entity_candidates({
        "t_srv.reg_ymd": {"profile": inference.classify_values(values), "comment": None},
    })
    assert p1_rules == []  # P1 은 규칙 없음
    built = _built_profile(tmp_path, _store(date_values=values))
    assert built.get("query_rules", []) == p1_rules


def test_internal_manual_definition_keeps_group(tmp_path: Path) -> None:
    """감사 M-2 — 내부망 manual 정의의 `group`이 반출·빌더 병합을 지나 커밋 프로필에 남는다."""
    internal = {
        **_PROFILE,
        "table_definitions": {
            "t_srv": {"group": "내부망 묶음", "kind": "현행", "manages": "내부망 편집",
                      "origin": "manual"},
        },
    }
    built = _built_profile(tmp_path, _store(), profile=internal)
    entry = built["table_definitions"]["t_srv"]
    assert entry["origin"] == "manual" and entry["manages"] == "내부망 편집"
    assert entry.get("group") == "내부망 묶음"


# --- 1회차 커밋 산출 --------------------------------------------------------------------


def test_committed_profile_loads_as_current() -> None:
    store = StructureStore(
        None, backup_root=REPO_ROOT / ".cache" / "__audit_unused__",
        profiles_dir=REPO_ROOT / "config" / "db_profiles",
    )
    current = store.read_current_profile("itam")
    assert current and isinstance(current["profile"], dict)
    profile = current["profile"]
    assert current["source"] == "manual" and current["environment"] is None
    # D-316 ②가 D-311 ③을 부분 개정 — 커밋 프로필의 query_guide는 지식 오버레이(plans/143)로만
    # 들어온다(빌더 W3 산출에는 없다)
    if "query_guide" in profile:
        header = (REPO_ROOT / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert "# 지식 오버레이(plans/143 W4 · D-316 ②)" in header
    assert len(profile["table_definitions"]) == 108
    assert len(profile["allowed_tables"]) == 98
    assert not any("tcdmsif81" in str(t) for t in profile["allowed_tables"])
    assert not (REPO_ROOT / ".cache" / "__audit_unused__").exists()
