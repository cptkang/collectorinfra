"""plans/140 W3 — 외부망 자산 빌더(`scripts/itam_bench/build_assets.py`) · D-311 ③⑤.

합성 반출(2회차 계약 모양 — P1 값 형식 비율·값 겹침 관계·DB 주석·내부망 정의 편집분·치환
코드값)로 근거 있는 자산만 만드는지, 근거가 없으면 키를 넣지 않는지, 정의 검증 실패·치환값
검출이면 쓰지 않는지, 로컬 샌드박스 프로필을 바이트 그대로 보존하는지 본다. 1회차 실반출이
있으면 커밋 파일 재현도 본다.

LLM·DB·Redis 0. 저장소 루트는 tmp(실반출 재현만 저장소를 읽기 전용으로 읽는다).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import REPO_ROOT
from scripts.itam_bench import build_assets as ba
from src.domain.table_definitions import has_table_definitions
from src.schema_cache.structure_store import StructureStore

_REAL_RUN = REPO_ROOT / "results" / "itam_bench" / "20261006-152938"
_SANDBOX_PROFILE = (
    "# plans/104 관리자 승인 적용 v1 · 2026-09-23T08:17:37+09:00 · anonymous\n"
    "source: manual\nenvironment: local_sandbox\npatterns: []\nquery_guide: '로컬 안내'\n"
)
_SUBSTITUTE = "QZ7K"


def _col(name: str, profile: dict[str, Any] | None = None, *, comment: str | None = None,
         type_: str = "varchar") -> dict[str, Any]:
    col: dict[str, Any] = {
        "name": name, "type": type_, "nullable": True,
        "meaning": comment, "meaning_source": "db_comment" if comment else "none",
    }
    if profile is not None:
        col["profile"] = profile
    return col


def _fmt(total: int = 100, **ratios: float) -> dict[str, Any]:
    keys = ("date8", "datetime14", "ipv4", "hostname", "multi_value")
    return {
        "candidate": False, "code": False, "distinct": 10, "truncated": False, "total": total,
        "formats": {k: ratios.get(k, 0.0) for k in keys}, "mixed_case": False, "flag": [],
        "entity_key": None, "error": None,
    }


def _catalog(*, evidence: bool = True, exported_defs: dict[str, Any] | None = None,
             allowed: list[str] | None = None) -> dict[str, Any]:
    """2회차 계약 모양 합성 카탈로그(evidence=False면 1회차처럼 근거 칸이 빈다)."""
    srv_cols = [
        _col("host_name", _fmt(hostname=0.98) if evidence else None,
             comment="서버 호스트명" if evidence else None),
        _col("ip_addr", _fmt(ipv4=0.97) if evidence else None),
        _col("reg_ymd", _fmt(date8=0.99) if evidence else None, type_="char"),
        _col("srv_id"),
    ]
    app_cols = [_col("app_id"), _col("srv_id"), _col("app_nm")]
    relations = [
        {"from": "t_app", "to": "t_srv", "columns": [["srv_id", "srv_id"]], "kind": "p1",
         "origin": "name_match", "overlap": 0.96, "sampled": 200, "accepted": True,
         "unique_parent": True, "error": None},
        {"from": "t_app", "to": "t_srv", "columns": [["app_nm", "srv_id"]], "kind": "p1",
         "origin": "name_match", "overlap": 0.41, "sampled": 200, "accepted": False,
         "unique_parent": True, "error": None},
        {"from": "t_app", "to": None, "columns": [["app_id", "app_id"]], "kind": "p1",
         "origin": "name_match", "overlap": None, "sampled": 0, "accepted": False,
         "unique_parent": None, "error": "예산 초과"},
    ] if evidence else []
    approved: dict[str, Any] = {
        "source": "manual", "environment": None,
        "allowed_tables": allowed if allowed is not None else ["t_srv", "t_app", "tcdmsif81"],
    }
    if exported_defs is not None:
        approved["table_definitions"] = exported_defs
    return {
        "db_id": "itam", "source": "structure_store",
        "db_description": {"text": "합성 자산 DB", "origin": "llm"},
        "approved_profile": approved,
        "tables": {
            "t_srv": {"key": ["srv_id"] if evidence else [],
                      "rows_estimate": 100 if evidence else None,
                      "relations": [], "columns": srv_cols},
            "t_app": {"key": [], "rows_estimate": 50 if evidence else None,
                      "relations": relations, "columns": app_cols},
            "tcdmsif81": {"key": [], "rows_estimate": None, "relations": [],
                          "columns": [_col("usr_id"), _col("pwd")]},
        },
    }


_SEED = {
    "db_id": "itam",
    "tables": {
        "t_srv": {"group": "서버", "kind": "현행", "manages": "서버 원장(시드)",
                  "key_columns": ["host_name"], "related": {"t_app": "srv_id"}},
        "t_app": {"kind": "현행", "manages": "앱 원장(시드)", "notes": "주의 없음"},
        "tcdmsif81": {"kind": "설정", "manages": "계정(시드)", "notes": "비밀번호 칸 보유"},
    },
}


def _repo(tmp_path: Path, *, seed: dict[str, Any] | None = None,
          profile: str | None = _SANDBOX_PROFILE) -> Path:
    repo = tmp_path / "repo"
    seed_path = repo / ba.SEED_DEFINITIONS_REL
    seed_path.parent.mkdir(parents=True)
    seed_path.write_text(yaml.safe_dump(seed or _SEED, allow_unicode=True), encoding="utf-8")
    if profile is not None:
        (repo / ba.PROFILE_REL).parent.mkdir(parents=True)
        (repo / ba.PROFILE_REL).write_text(profile, encoding="utf-8")
    return repo


def _run_dir(
    tmp_path: Path, catalog: dict[str, Any], samples: dict[str, Any] | None = None
) -> Path:
    run = tmp_path / "results" / "20261101-000000"
    run.mkdir(parents=True)
    (run / ba.CATALOG_FILE).write_text(
        yaml.safe_dump(catalog, allow_unicode=True), encoding="utf-8"
    )
    if samples is not None:
        (run / ba.CODE_SAMPLES_FILE).write_text(
            yaml.safe_dump(samples, allow_unicode=True), encoding="utf-8"
        )
    return run


def _profile(repo: Path) -> dict[str, Any]:
    return yaml.safe_load((repo / ba.PROFILE_REL).read_text(encoding="utf-8"))


# ──────────────────────────────────────────────
# 근거 있는 자산만
# ──────────────────────────────────────────────


class TestEvidenceAssets:
    def test_entity_keys_relationships_rules_from_p1(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        profile = _profile(repo)
        assert profile["entity_keys"] == {
            "entity": "server", "table": "t_srv",
            "keys": [
                {"type": "hostname", "column": "host_name", "priority": 1, "compare": "casefold"},
                {"type": "ip", "column": "ip_addr", "priority": 2},
            ],
        }
        # 채택분만 — 낮은 겹침·오류(부모 없음) 항목은 빠진다
        assert profile["relationships"] == [
            {"from": "t_app.srv_id", "to": "t_srv.srv_id", "origin": "name_match", "overlap": 0.96}
        ]
        assert len(profile["query_rules"]) == 1 and "`t_srv.reg_ymd`" in profile["query_rules"][0]

    def test_no_evidence_no_keys(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog(evidence=False)), repo_root=repo) == 0
        profile = _profile(repo)
        assert list(profile) == ["source", "allowed_tables", "table_definitions"]
        assert "environment" not in profile
        for key in (*ba.VALUE_KEYS, "entity_keys", "relationships"):
            assert key not in profile
        assert not (repo / ba.SYNONYM_SEED_REL).exists()

    def test_assets_only_for_allowed_tables(self, tmp_path):
        repo = _repo(tmp_path)
        run = _run_dir(tmp_path, _catalog(allowed=["t_app"]))
        assert ba.run_build(run, repo_root=repo) == 0
        profile = _profile(repo)
        assert "entity_keys" not in profile and "query_rules" not in profile
        assert "relationships" not in profile  # 부모가 조회 대상 밖

    def test_synonym_seeds_only_from_db_comment(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        text = (repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8")
        assert text.startswith("# plans/140 W3")
        seeds = yaml.safe_load(text)
        assert seeds == {
            "version": "1.0", "db_id": "itam", "source_tag": "operator",
            "column_synonyms": {"t_srv.host_name": ["서버 호스트명"]},
        }


# ──────────────────────────────────────────────
# 조회 대상 · 정의 · 머리말
# ──────────────────────────────────────────────


class TestProfileShape:
    def test_default_exclusion_and_keep(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        assert _profile(repo)["allowed_tables"] == ["t_srv", "t_app"]
        repo2 = _repo(tmp_path / "b")
        run2 = _run_dir(tmp_path / "b", _catalog())
        assert ba.run_build(run2, repo_root=repo2, keep_excluded=True) == 0
        assert _profile(repo2)["allowed_tables"] == ["t_srv", "t_app", "tcdmsif81"]

    def test_manual_wins_over_seed(self, tmp_path):
        exported = {
            "t_srv": {"manages": "서버 원장(내부망 편집)", "origin": "manual"},
            "t_app": {"manages": "앱 원장(내부망 LLM)", "origin": "llm"},
        }
        repo = _repo(tmp_path)
        run = _run_dir(tmp_path, _catalog(exported_defs=exported))
        assert ba.run_build(run, repo_root=repo) == 0
        defs = _profile(repo)["table_definitions"]
        assert defs["t_srv"] == {"manages": "서버 원장(내부망 편집)", "origin": "manual"}
        assert defs["t_app"]["manages"] == "앱 원장(시드)" and defs["t_app"]["origin"] == "import"
        assert defs["tcdmsif81"]["origin"] == "import"
        header = (repo / ba.PROFILE_REL).read_text(encoding="utf-8").split("source:")[0]
        assert "반출 내부망 편집분(manual) 1건" in header and "시드 초안 2건" in header

    def test_definition_errors_refuse_write(self, tmp_path, capsys):
        seed = json.loads(json.dumps(_SEED))
        seed["tables"]["t_srv"]["key_columns"] = ["no_such_column"]
        repo = _repo(tmp_path, seed=seed)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == ba.EXIT_REFUSED
        assert (repo / ba.PROFILE_REL).read_text(encoding="utf-8") == _SANDBOX_PROFILE
        assert not (repo / ba.SCHEMA_SEED_REL).exists()
        assert "검증 오류 1건" in capsys.readouterr().out

    def test_header(self, tmp_path):
        repo = _repo(tmp_path)
        run = _run_dir(tmp_path, _catalog())
        assert ba.run_build(run, repo_root=repo) == 0
        text = (repo / ba.PROFILE_REL).read_text(encoding="utf-8")
        header = [line for line in text.splitlines() if line.startswith("#")]
        joined = "\n".join(header)
        assert text.startswith("# plans/140 W3 외부망 자산 빌더")
        assert f"출처 반출 run {run.name}" in joined and "생성 " in joined
        assert "직접 커밋 경로 — D-311 · 내부망 승인·실행 검증 없음" in joined
        for label in ("반출 승인 프로필", "시드 초안", "「(추정)」", "P1 값 형식 비율",
                      "P1 값 겹침", "코드값·코드 라벨은 반입 뒤 내부망 P1 승인"):
            assert label in joined, label

    def test_profile_reads_as_approved_with_definitions(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        store = StructureStore(None, backup_root=tmp_path / "bk",
                               profiles_dir=repo / ba.PROFILE_REL.parent)
        current = store.read_current_profile("itam")
        assert isinstance(current["profile"], dict)
        assert current["source"] == "manual" and current["environment"] is None
        assert has_table_definitions(current["profile"])

    def test_missing_allowed_tables_is_input_error(self, tmp_path):
        catalog = _catalog()
        catalog["approved_profile"] = None
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, catalog), repo_root=repo) == ba.EXIT_INPUT


# ──────────────────────────────────────────────
# 치환값 차단
# ──────────────────────────────────────────────


class TestSubstitutionGuard:
    def _samples(self) -> dict[str, Any]:
        return {"columns": {"t_app.app_st": {
            "distinct": 2, "substitution": "ok", "values": [_SUBSTITUTE, "WX2P"],
            "labels": [[_SUBSTITUTE, "갸뇨"]],
        }}}

    def test_literal_in_file_refuses_and_hides_value(self, tmp_path, capsys):
        seed = json.loads(json.dumps(_SEED))
        seed["tables"]["t_app"]["notes"] = f"상태 '{_SUBSTITUTE}' 주의"
        repo = _repo(tmp_path, seed=seed)
        run = _run_dir(tmp_path, _catalog(), self._samples())
        assert ba.run_build(run, repo_root=repo) == ba.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "config/db_profiles/itam.yaml table_definitions.t_app.notes 스칼라" in out
        assert _SUBSTITUTE not in out and "갸뇨" not in out
        assert (repo / ba.PROFILE_REL).read_text(encoding="utf-8") == _SANDBOX_PROFILE

    def test_clean_build_passes_with_samples(self, tmp_path):
        repo = _repo(tmp_path)
        run = _run_dir(tmp_path, _catalog(), self._samples())
        assert ba.run_build(run, repo_root=repo) == 0
        text = (repo / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert _SUBSTITUTE not in text and "WX2P" not in text

    def test_scalar_in_value_keys(self):
        values = ba.substituted_values(self._samples())
        assert values == {_SUBSTITUTE, "WX2P", "갸뇨"}
        data = {"code_values": {"t_app.app_st": ["WX2P"]}, "query_rules": ["무관한 문장"],
                "column_values": {"t.c": {"갸뇨": {"op": "=", "value": "A"}}}}
        hits = ba.substitution_hits({"p.yaml": ("source: manual\n", data)}, values)
        assert hits == [  # 값이 키인 칸은 위치에 순번만(감사 L-5)
            "p.yaml code_values.t_app.app_st[0] 스칼라",
            "p.yaml column_values.t.c[#0] 키",
        ]
        assert ba.substitution_hits({"p.yaml": ("x", data)}, set()) == []


# ──────────────────────────────────────────────
# 스키마 캐시 · 로컬 보존
# ──────────────────────────────────────────────


class TestSchemaCacheAndPreserve:
    def test_schema_cache_shape(self, tmp_path):
        catalog = _catalog()
        catalog["tables"]["t_app"]["relations"].append(
            {"from": "t_app", "to": "t_srv", "columns": [["srv_id", "srv_id"]], "kind": "declared"}
        )
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, catalog), repo_root=repo) == 0
        data = json.loads((repo / ba.SCHEMA_SEED_REL).read_text(encoding="utf-8"))
        assert data["_db_id"] == "itam" and data["_db_description"] == "합성 자산 DB"
        tables = data["schema"]["tables"]
        assert sorted(tables) == ["t_app", "t_srv", "tcdmsif81"]
        srv = {c["name"]: c for c in tables["t_srv"]["columns"]}
        assert srv["srv_id"]["primary_key"] is True and srv["host_name"]["primary_key"] is False
        app = {c["name"]: c for c in tables["t_app"]["columns"]}
        assert app["srv_id"]["foreign_key"] is True
        assert app["srv_id"]["references"] == "t_srv.srv_id"
        assert tables["t_srv"]["sample_data"] == [] and tables["t_srv"]["row_count_estimate"] == 100
        assert data["schema"]["relationships"] == [{"from": "t_app.srv_id", "to": "t_srv.srv_id"}]
        assert "profile" not in json.dumps(tables)

    def test_install_cache_backs_up(self, tmp_path):
        repo = _repo(tmp_path)
        target = repo / ba.INSTALL_CACHE_REL
        target.parent.mkdir(parents=True)
        target.write_text('{"old": true}', encoding="utf-8")
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo, install_cache=True) == 0
        backups = list(target.parent.glob(f"{target.name}.bak-*"))
        assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == '{"old": true}'
        assert target.read_bytes() == (repo / ba.SCHEMA_SEED_REL).read_bytes()

    def test_cache_not_installed_by_default(self, tmp_path):
        repo = _repo(tmp_path)
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        assert not (repo / ba.INSTALL_CACHE_REL).exists()

    def test_local_sandbox_preserved_byte_identical_once(self, tmp_path):
        repo = _repo(tmp_path)
        run = _run_dir(tmp_path, _catalog())
        assert ba.run_build(run, repo_root=repo) == 0
        kept = repo / ba.LOCAL_SANDBOX_PROFILE_REL
        assert kept.read_bytes() == _SANDBOX_PROFILE.encode("utf-8")
        # 다시 돌려도 보존본을 덮지 않는다(현 프로필은 이제 빌더 산출)
        assert ba.run_build(run, repo_root=repo) == 0
        assert kept.read_bytes() == _SANDBOX_PROFILE.encode("utf-8")

    def test_non_sandbox_profile_not_preserved(self, tmp_path):
        repo = _repo(tmp_path, profile="source: manual\nallowed_tables: []\n")
        assert ba.run_build(_run_dir(tmp_path, _catalog()), repo_root=repo) == 0
        assert not (repo / ba.LOCAL_SANDBOX_PROFILE_REL).exists()


# ──────────────────────────────────────────────
# 1회차 실반출 재현(있을 때만)
# ──────────────────────────────────────────────


@pytest.mark.skipif(
    not (_REAL_RUN / ba.CATALOG_FILE).is_file(), reason="1회차 반출물 없음(외부망 로컬 전용)"
)
def test_first_export_reproduces_committed_files():
    result = ba.build(_REAL_RUN, repo_root=REPO_ROOT, generated_at="T")
    s = result["summary"]
    assert (s["tables"], s["columns"], s["allowed_tables"]) == (108, 1988, 98)
    assert s["excluded"] == ["tcdmsif81"]
    assert (s["table_definitions"], s["definition_errors"]) == (108, 0)
    evidence = (s["entity_keys"], s["relationships"], s["query_rules"], s["synonym_seeds"])
    assert evidence == (0, 0, 0, 0)
    assert s["schema_cache_diff"] == []
    assert str(ba.SYNONYM_SEED_REL) not in result["files"]

    def body(text: str) -> str:
        return "\n".join(line for line in text.splitlines() if not line.startswith("#"))

    # 커밋 파일 = 빌더 + 지식 오버레이(정적 전용) 산출(D-316 ②가 D-311 ③을 부분 개정 · plans/143
    # W4) — 같은 원천으로 오버레이를 재현해 비교한다(위 근거 단언은 오버레이 전 빌더 산출 기준)
    from scripts.itam_bench.knowledge import KNOWLEDGE_DIR_REL, KnowledgeDeps
    from src.api.routes.db_structure import asset_sql_checker

    # K2 예시(SQL 항목)는 SQL 검증기가 없으면 거절된다 — 실제 빌드와 같은 정적 검사기
    # (`asset_sql_checker` · DB 접속 없음)를 넘긴다. 정적 전용이라 DB 클라이언트는 열지 않는다
    # (plans/146 W4 교정 1).
    def _no_db() -> Any:
        raise AssertionError("정적 전용 재현은 DB에 접속하지 않는다")

    overlay = ba.knowledge_overlay(
        _REAL_RUN, repo_root=REPO_ROOT, knowledge_dir=REPO_ROOT / KNOWLEDGE_DIR_REL,
        static_only=True, deps=KnowledgeDeps(client_factory=_no_db, sql_checker=asset_sql_checker),
        keep_excluded=False,
    )
    assert overlay is not None
    result = ba.build(_REAL_RUN, repo_root=REPO_ROOT, generated_at="T", knowledge=overlay)
    assert result["remove"] == [] and result["summary"]["knowledge_kept"] == []
    for rel, text in result["files"].items():
        if rel == str(ba.SCHEMA_SEED_REL):
            continue  # 위 schema_cache_diff로 본다
        committed = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert body(text) == body(committed), rel


def test_comment_synonyms_shared_pure_function():
    """P1(`_assemble`)과 빌더가 같이 쓰는 주석 유사어 — 이름과 같은 라벨·테이블 주석은 뺀다."""
    from src.domain.schema_inference import comment_synonyms

    assert comment_synonyms({
        "t.host_nm": "호스트명(서버 이름)", "t.status": "status", "t": "테이블 주석",
        "t.long": "가" * 30,
    }) == {"t.host_nm": ["호스트명"]}
