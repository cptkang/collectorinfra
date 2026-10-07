"""plans/141 W4 — K8 템플릿 검증 연결 · 빌더 지식 오버레이(`--build-assets`) · D-314 ②③④.

원천 디렉터리는 tmp, 모의 DB는 가짜 실행기(테스트 더블)다 — 실 DB·LLM 0. SQL 검사기는 「DB 구조」
탭과 같은 `asset_sql_checker`(validate_sql)를 그대로 쓴다. 실제 `config/**`는 읽지도 쓰지도
않는다(저장소 루트는 tmp).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import knowledge as kn
from src.api.routes.db_structure import asset_sql_checker
from src.domain import knowledge_assets as ka
from src.domain import query_templates as qt

_RUN_ID = "20261101-000000"
_STAMP = "2026-11-01T00:00:00+0900"
_SECRET = "QZ7K"  # 치환 코드값 — 커밋 산출물·검증 문구에 나오면 안 된다


# ──────────────────────────────────────────────
# 픽스처 — 반출 카탈로그 · 시드 · 원천 파일
# ──────────────────────────────────────────────


def _col(name: str, *, fmt: str | None = None, comment: str | None = None) -> dict[str, Any]:
    col: dict[str, Any] = {
        "name": name, "type": "varchar", "nullable": True,
        "meaning": comment, "meaning_source": "db_comment" if comment else "none",
    }
    if fmt:
        keys = ("date8", "datetime14", "ipv4", "hostname", "multi_value")
        col["profile"] = {
            "candidate": False, "code": False, "distinct": 10, "truncated": False, "total": 100,
            "formats": {k: (0.99 if k == fmt else 0.0) for k in keys}, "mixed_case": False,
            "flag": [], "entity_key": None, "error": None,
        }
    return col


def _catalog() -> dict[str, Any]:
    return {
        "db_id": "itam", "source": "structure_store",
        "approved_profile": {
            "source": "manual", "environment": None,
            "allowed_tables": ["t_srv", "t_app", "tcdmsif81"],
        },
        "tables": {
            "t_srv": {"key": ["srv_id"], "rows_estimate": 100, "relations": [], "columns": [
                _col("host_name", fmt="hostname", comment="서버 호스트명"),
                _col("reg_ymd", fmt="date8"),
                _col("srv_id"),
                _col("활성여부"),
            ]},
            "t_app": {"key": [], "rows_estimate": 50, "relations": [], "columns": [
                _col("app_id"), _col("srv_id"), _col("app_nm"),
            ]},
            "tcdmsif81": {"key": [], "rows_estimate": None, "relations": [],
                          "columns": [_col("usr_id"), _col("pwd")]},
        },
    }


_SEED = {
    "db_id": "itam",
    "tables": {
        "t_srv": {"kind": "현행", "manages": "서버 원장(시드)", "key_columns": ["host_name"]},
        "t_app": {"kind": "현행", "manages": "앱 원장(시드)"},
        "tcdmsif81": {"kind": "설정", "manages": "계정(시드)"},
    },
}


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / ba.SEED_DEFINITIONS_REL, _SEED)
    return repo


def _run_dir(tmp_path: Path, samples: dict[str, Any] | None = None) -> Path:
    run = tmp_path / "results" / _RUN_ID
    _write(run / ba.CATALOG_FILE, _catalog())
    if samples is not None:
        _write(run / ba.CODE_SAMPLES_FILE, samples)
    return run


def _item(item_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": item_id, "origin": ka.ORIGIN, "evidence": _RUN_ID, "status": ka.STATUS_ACTIVE,
            **fields}


def _tpl(tid: str, sql: str, tables: list[str], slots: list[dict[str, Any]] | None = None,
         **fields: Any) -> dict[str, Any]:
    return _item(tid, intent="의도", triggers=["표면어"], slots=slots or [], sql=sql,
                 tables=tables, **fields)


_WITHDRAWN = {"status": ka.STATUS_WITHDRAWN, "reason": "내부망 회귀"}
_GUIDE_OK = "서버 원장은 `t_srv`이고 호스트는 `t_srv.host_name`으로 찾는다."
_SECTION_OK = "- 앱은 `t_app.srv_id`로 서버에 잇는다."
_EXAMPLE_OK = _item(
    "e_ok", question="서버 호스트 목록", description="원장 호스트",
    sql="SELECT host_name FROM t_srv LIMIT 10", tables=["t_srv"],
)
_TPL_OK = _tpl(
    "t_ok", "SELECT host_name FROM t_srv WHERE host_name = :host", ["t_srv"],
    [{"name": "host", "type": "hostname"}],
)

#: 통과/거절/철회 혼합 원천
_MIXED: dict[str, list[dict[str, Any]]] = {
    ka.GUIDE_FILE: [
        _item("g_ok", text=_GUIDE_OK),
        _item("g_bad", text="호스트는 `t_srv.호스트명`으로 찾는다."),
        _item("g_old", text="철회된 안내", **_WITHDRAWN),
    ],
    ka.EXAMPLES_FILE: [
        _EXAMPLE_OK,
        _item("e_bad", question="지우기", description="쓰기", sql="DELETE FROM t_srv",
              tables=["t_srv"]),
    ],
    ka.DESCRIPTIONS_FILE: [
        _item("d_ok", table="T_SRV", column="HOST_NAME", text="OS가 인식하는 서버 이름"),
        _item("d_bad", table="t_srv", column="reg_ymd", text="1:정상, 2:장애"),
    ],
    ka.SYNONYMS_FILE: [
        _item("s_ok", table="t_srv", column="host_name", words=["서버 호스트명", "호스트이름"]),
    ],
    ka.SECTION_FILE: [_item("p_ok", text=_SECTION_OK)],
}
_TEMPLATES = [
    _TPL_OK,
    _tpl("t_lit", "SELECT host_name FROM t_srv WHERE host_name LIKE '%:kw%'", ["t_srv"],
         [{"name": "kw", "type": "keyword"}]),
    _tpl("t_old", "SELECT srv_id FROM t_srv", ["t_srv"], **_WITHDRAWN),
]


def _source(root: Path, files: dict[str, list[dict[str, Any]]] | None = None,
            templates: list[dict[str, Any]] | None = None) -> Path:
    for name, items in (files if files is not None else _MIXED).items():
        _write(root / name, {"version": 1, "items": items})
    if templates is not None:
        _write(root / ka.TEMPLATES_FILE, {"version": 1, "templates": templates})
    return root


class FakeClient:
    """읽기 전용 실행 더블 — 받은 SQL을 적고, `fail_on`이 든 SQL은 실패시킨다(오류에 SQL을
    싣는다)."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.sqls: list[str] = []
        self.fail_on = fail_on

    async def health_check(self) -> bool:
        return True

    async def execute_sql(self, sql: str) -> Any:
        self.sqls.append(sql)
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError(f"near {sql}")
        return SimpleNamespace(rows=[])


def _deps(client: Any) -> kn.KnowledgeDeps:
    @asynccontextmanager
    async def factory() -> AsyncIterator[Any]:
        if isinstance(client, Exception):
            raise client
        yield client

    return kn.KnowledgeDeps(client_factory=factory, sql_checker=asset_sql_checker)


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ba.time, "strftime", lambda fmt, *a: _STAMP)


def _outputs(repo: Path) -> dict[str, str]:
    """빌더 산출물(원천 디렉터리 제외) — 스키마 캐시는 저장 시각 키만 지운다."""
    out: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        rel = str(path.relative_to(repo))
        if not path.is_file() or rel.startswith(str(kn.KNOWLEDGE_DIR_REL)):
            continue
        text = path.read_text(encoding="utf-8")
        if rel == str(ba.SCHEMA_SEED_REL):
            data = json.loads(text)
            for key in ("_cached_at", "_cached_at_iso"):
                data.pop(key, None)
            text = json.dumps(data, ensure_ascii=False, sort_keys=True)
        out[rel] = text
    return out


# ──────────────────────────────────────────────
# K8 검증 연결
# ──────────────────────────────────────────────

_SCHEMA = ba.schema_cache_dict(_catalog())


async def _validate_templates(root: Path, templates: list[dict[str, Any]], **kwargs: Any
                              ) -> dict[str, Any]:
    _write(root / ka.TEMPLATES_FILE, {"version": 1, "templates": templates})
    params: dict[str, Any] = dict(
        catalog=_SCHEMA, allowed=["t_srv", "t_app"], executor=FakeClient(),
        sql_checker=asset_sql_checker, code_values={_SECRET},
    )
    params.update(kwargs)
    return await kn.avalidate_dir(root, **params)


def _codes(result: dict[str, Any]) -> dict[str, list[str]]:
    return {r["id"]: [i["code"] for i in r["issues"]] for r in result["results"]}


class TestTemplateValidation:
    async def test_contract_scope_substitution_and_pass(self, tmp_path: Path) -> None:
        executor = FakeClient()
        templates = [
            _TPL_OK,
            _tpl("t_kor", "SELECT `활성여부` FROM t_srv", ["t_srv"]),
            _tpl("t_lit", "SELECT host_name FROM t_srv WHERE host_name LIKE '%:kw%'", ["t_srv"],
                 [{"name": "kw", "type": "keyword"}]),
            _tpl("t_semi", "SELECT host_name FROM t_srv; DELETE FROM t_srv", ["t_srv"]),
            _tpl("t_col", "SELECT srv_id FROM t_srv WHERE reg_ymd = :k", ["t_srv"],
                 [{"name": "k", "type": "code", "column": "t_srv.nope"}]),
            _tpl("t_out", "SELECT usr_id FROM tcdmsif81", ["tcdmsif81"]),
            _tpl("t_sub", f"SELECT srv_id FROM t_srv WHERE reg_ymd = '{_SECRET}'", ["t_srv"]),
            _tpl("t_old", "SELECT srv_id FROM t_srv", ["t_srv"], **_WITHDRAWN),
        ]
        result = await _validate_templates(tmp_path, templates, executor=executor)
        codes = _codes(result)
        assert codes["t_ok"] == [] and codes["t_kor"] == []
        assert codes["t_lit"] == [kn.TEMPLATE_CONTRACT]  # 리터럴 안 자리표 = 주입 슬롯
        assert codes["t_semi"] == [kn.TEMPLATE_CONTRACT]
        assert codes["t_col"] == [kn.TEMPLATE_CONTRACT]
        assert codes["t_out"][0] == ka.TABLE_NOT_ALLOWED
        assert codes["t_sub"] == [kn.SUBSTITUTED]
        assert "t_old" not in codes
        assert result["withdrawn"] == [
            {"file": ka.TEMPLATES_FILE, "id": "t_old", "reason": "내부망 회귀"}
        ]
        assert result["skipped"] == []
        # 정적 통과분만 바깥 행 제한으로 감싸 실행한다(대표값 바인딩)
        assert executor.sqls == [
            "SELECT * FROM (SELECT host_name FROM t_srv WHERE host_name = 'host01') q LIMIT 50",
            "SELECT * FROM (SELECT `활성여부` FROM t_srv) q LIMIT 50",
        ]
        assert _SECRET not in repr(result)

    async def test_all_optional_combinations_executed(self, tmp_path: Path) -> None:
        executor = FakeClient()
        tpl = _tpl(
            "t_opt", "SELECT app_id FROM t_app WHERE (:kind IS NULL OR app_nm = :kind)"
            " AND (:host IS NULL OR srv_id = :host)", ["t_app"],
            [{"name": "kind", "type": "code", "column": "T_APP.APP_NM", "required": False},
             {"name": "host", "type": "hostname", "required": False}],
        )
        result = await _validate_templates(
            tmp_path, [tpl], executor=executor, code_samples={"t_app.app_nm": [_SECRET]}
        )
        assert _codes(result) == {"t_opt": []}
        assert len(executor.sqls) == 4  # 선택 슬롯 둘 → 있음/없음 4조합

    async def test_code_without_samples_is_pending_not_silent(self, tmp_path: Path) -> None:
        templates = [
            _tpl("t_req", "SELECT srv_id FROM t_app WHERE app_nm = :kind", ["t_app"],
                 [{"name": "kind", "type": "code", "column": "t_app.app_nm"}]),
            _tpl("t_opt", "SELECT app_id FROM t_app WHERE (:kind IS NULL OR app_nm = :kind)",
                 ["t_app"],
                 [{"name": "kind", "type": "code", "column": "t_app.app_nm", "required": False}]),
        ]
        executor = FakeClient()
        found = {r["id"]: r for r in (
            await _validate_templates(tmp_path, templates, executor=executor)
        )["results"]}
        assert [i["code"] for i in found["t_req"]["issues"]] == [kn.CODE_UNVERIFIED]
        assert "코드값 없음 — 실행 보류" in found["t_req"]["issues"][0]["message"]
        assert "조합 1/2" in found["t_opt"]["issues"][0]["message"]
        assert not found["t_req"]["ok"] and not found["t_opt"]["ok"]
        assert len(executor.sqls) == 1  # 코드 없는 조합만 실행
        static = await _validate_templates(
            tmp_path / "s", templates, executor=None, static_only=True
        )
        assert all(r["ok"] for r in static["results"])
        assert static["summary"][kn.CODE_UNVERIFIED] == 2

    async def test_injection_code_sample_rejected_and_db_error_masked(self, tmp_path: Path
                                                                       ) -> None:
        templates = [
            _tpl("t_inj", "SELECT srv_id FROM t_app WHERE app_nm = :k", ["t_app"],
                 [{"name": "k", "type": "code", "column": "t_app.app_nm"}]),
            _tpl("t_fail", "SELECT srv_id FROM t_srv WHERE reg_ymd = :k", ["t_srv"],
                 [{"name": "k", "type": "code", "column": "t_srv.reg_ymd"}]),
        ]
        executor = FakeClient(fail_on=_SECRET)
        result = await _validate_templates(
            tmp_path, templates, executor=executor,
            code_samples={"t_app.app_nm": ["x' OR '1'='1"], "t_srv.reg_ymd": [_SECRET]},
        )
        found = {r["id"]: r for r in result["results"]}
        assert [i["code"] for i in found["t_inj"]["issues"]] == [kn.TEMPLATE_BIND]
        assert "OR '1'" not in found["t_inj"]["issues"][0]["message"]
        assert [i["code"] for i in found["t_fail"]["issues"]] == [kn.DB_FAILED]
        assert _SECRET not in repr(result) and "<코드값>" in repr(result)
        assert all("OR '1'" not in s for s in executor.sqls)

    async def test_no_executor_is_db_unverified(self, tmp_path: Path) -> None:
        result = await _validate_templates(tmp_path, [_TPL_OK], executor=None)
        assert _codes(result) == {"t_ok": [kn.DB_UNVERIFIED]}

    async def test_runtime_shape_required(self, tmp_path: Path) -> None:
        _write(tmp_path / ka.TEMPLATES_FILE, {"version": 1, "items": [_TPL_OK]})
        result = await kn.avalidate_dir(
            tmp_path, catalog=_SCHEMA, allowed=["t_srv"], executor=None,
            sql_checker=asset_sql_checker,
        )
        assert _codes(result) == {"*": [ka.FILE_INVALID]}

    def test_code_samples_only_ok_substitution(self, tmp_path: Path) -> None:
        _write(tmp_path / "r1" / ba.CODE_SAMPLES_FILE, {"columns": {
            "T_APP.APP_NM": {"substitution": "ok", "values": ["B", "A"]},
            "t_srv.reg_ymd": {"substitution": "exhausted"},
        }})
        assert kn.load_code_samples({"r1", "없음"}, tmp_path) == {"t_app.app_nm": ["B", "A"]}

    def test_template_path_matches_runtime(self) -> None:
        from src.db_adapters.template_assembler import TEMPLATE_PATH

        assert str(ba.TEMPLATES_REL) == TEMPLATE_PATH.format(db_id="itam")


# ──────────────────────────────────────────────
# 빌더 지식 오버레이
# ──────────────────────────────────────────────


def _build(tmp_path: Path, *, client: Any = None, source: Path | None = None,
           static_only: bool = False, samples: dict[str, Any] | None = None,
           repo: Path | None = None) -> tuple[int, Path]:
    repo = repo or _repo(tmp_path)
    code = ba.run_build(
        _run_dir(tmp_path, samples) if not (tmp_path / "results" / _RUN_ID).exists()
        else tmp_path / "results" / _RUN_ID,
        repo_root=repo,
        knowledge_dir=source,
        knowledge_static_only=static_only,
        knowledge_deps=_deps(client if client is not None else FakeClient()),
    )
    return code, repo


def _profile(repo: Path) -> dict[str, Any]:
    return yaml.safe_load((repo / ba.PROFILE_REL).read_text(encoding="utf-8"))


class TestOverlay:
    def test_mixed_source_writes_only_passing_active(self, tmp_path, fixed_time, capsys) -> None:
        source = _source(tmp_path / "src", templates=_TEMPLATES)
        code, repo = _build(tmp_path, source=source)
        assert code == ba.EXIT_OK
        out = capsys.readouterr().out
        profile = _profile(repo)
        assert profile["query_guide"] == _GUIDE_OK
        assert profile["query_examples"] == [{
            "question": "서버 호스트 목록", "sql": "SELECT host_name FROM t_srv LIMIT 10",
            "explanation": "원장 호스트", "id": "e_ok",
        }]
        text = (repo / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert "# 지식 오버레이(plans/141 W4 · D-314 ②)" in text
        assert f"#   원천 근거 run {_RUN_ID} · 검증 {kn.VERIFIED_DB}" in text
        assert "#   query_guide        K1 가이드 1건" in text
        # K3 설명 — 카탈로그 원 이름으로 · 정본 파일 로더 형식
        from src.schema_cache.knowledge_descriptions import load_knowledge_descriptions

        assert load_knowledge_descriptions(repo / "config" / "knowledge", "itam") == {
            "t_srv.host_name": "OS가 인식하는 서버 이름"
        }
        section = yaml.safe_load((repo / ba.SECTION_REL).read_text(encoding="utf-8"))
        assert section["db_id"] == "itam" and section["section"] == _SECTION_OK
        templates, issues = qt.parse_templates(
            yaml.safe_load((repo / ba.TEMPLATES_REL).read_text(encoding="utf-8"))
        )
        assert [t.id for t in templates] == ["t_ok"] and issues == []
        # 철회·거절 항목은 어디에도 없다
        everything = "\n".join(_outputs(repo).values())
        for gone in ("철회된 안내", "t_srv.호스트명", "DELETE", "1:정상", "t_lit", "t_old"):
            assert gone not in everything
        assert "거절 guide.yaml g_bad" in out and "거절 query_templates.yaml t_lit" in out
        assert "지식 오버레이(모의 DB 실행" in out

    def test_existing_first_merge_rules_and_synonyms(self, tmp_path, fixed_time) -> None:
        base_repo = _repo(tmp_path / "base")
        assert ba.run_build(_run_dir(tmp_path), repo_root=base_repo) == ba.EXIT_OK
        p1_rules = _profile(base_repo)["query_rules"]
        assert p1_rules  # 반출 P1 값 형식 근거 규칙

        repo = _repo(tmp_path)
        _write(repo / ba.SYNONYM_SEED_REL, {
            "db_id": "itam", "column_synonyms": {"t_srv.host_name": ["기존낱말"]},
        })
        code, _ = _build(tmp_path, source=_source(tmp_path / "src"), repo=repo)
        assert code == ba.EXIT_OK
        rules = _profile(repo)["query_rules"]
        assert rules[: len(p1_rules)] == p1_rules
        assert any("`t_srv.활성여부`" in r for r in rules[len(p1_rules):])  # K6 파생 뒤에
        seeds = yaml.safe_load((repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8"))
        assert seeds["column_synonyms"]["t_srv.host_name"] == [
            "기존낱말", "서버 호스트명", "호스트이름",
        ]
        assert "K3 유사어 1건" in (repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8")

    def test_no_source_or_zero_passing_is_byte_identical(self, tmp_path, fixed_time) -> None:
        run = _run_dir(tmp_path)
        repo_a = _repo(tmp_path / "a")
        assert ba.run_build(run, repo_root=repo_a) == ba.EXIT_OK  # 원천 디렉터리 없음
        failing = {
            ka.GUIDE_FILE: [_MIXED[ka.GUIDE_FILE][1], _MIXED[ka.GUIDE_FILE][2]],
            ka.EXAMPLES_FILE: [_MIXED[ka.EXAMPLES_FILE][1]],
        }
        repo_b = _repo(tmp_path / "b")
        source = _source(repo_b / kn.KNOWLEDGE_DIR_REL, failing, templates=[_TEMPLATES[1]])
        code = ba.run_build(run, repo_root=repo_b, knowledge_deps=_deps(FakeClient()))
        assert code == ba.EXIT_OK and source.is_dir()
        assert _outputs(repo_b) == _outputs(repo_a)
        assert not (repo_b / ba.TEMPLATES_REL).exists()
        assert not (repo_b / ba.DESCRIPTIONS_REL).exists()

    def test_db_unavailable_skips_overlay_unless_static_only(self, tmp_path, fixed_time,
                                                            capsys) -> None:
        run = _run_dir(tmp_path)
        repo_a = _repo(tmp_path / "a")
        assert ba.run_build(run, repo_root=repo_a) == ba.EXIT_OK
        source = _source(tmp_path / "src", templates=_TEMPLATES)
        repo_b = _repo(tmp_path / "b")
        code = ba.run_build(run, repo_root=repo_b, knowledge_dir=source,
                            knowledge_deps=_deps(ConnectionError("refused")))
        assert code == ba.EXIT_OK
        assert _outputs(repo_b) == _outputs(repo_a)
        assert "지식 오버레이: 건너뜀 — 모의 DB 미연결" in capsys.readouterr().out

        repo_c = _repo(tmp_path / "c")
        code = ba.run_build(run, repo_root=repo_c, knowledge_dir=source,
                            knowledge_static_only=True,
                            knowledge_deps=_deps(ConnectionError("정적 전용은 붙지 않는다")))
        assert code == ba.EXIT_OK
        text = (repo_c / ba.PROFILE_REL).read_text(encoding="utf-8")
        assert f"검증 {kn.VERIFIED_STATIC}" in text
        assert yaml.safe_load(text)["query_guide"] == _GUIDE_OK
        assert kn.VERIFIED_STATIC in (repo_c / ba.TEMPLATES_REL).read_text(encoding="utf-8")
        assert capsys.readouterr().out.count("모의 DB 실행 미실시(--static-only)") == 1

    def test_zero_templates_no_file(self, tmp_path, fixed_time, capsys) -> None:
        code, repo = _build(tmp_path, source=_source(tmp_path / "src", templates=[_TEMPLATES[1]]))
        assert code == ba.EXIT_OK
        assert not (repo / ba.TEMPLATES_REL).exists()
        assert "K8 템플릿: 통과분 0" in capsys.readouterr().out

    def test_substituted_template_dropped_build_not_refused(self, tmp_path, fixed_time) -> None:
        samples = {"columns": {"t_srv.reg_ymd": {"substitution": "ok", "values": [_SECRET]}}}
        tpl = _tpl("t_sub", f"SELECT srv_id FROM t_srv WHERE reg_ymd = '{_SECRET}'", ["t_srv"])
        code, repo = _build(tmp_path, source=_source(tmp_path / "src", templates=[_TPL_OK, tpl]),
                            samples=samples)
        assert code == ba.EXIT_OK
        assert _SECRET not in "\n".join(_outputs(repo).values())
        ids = [t["id"] for t in yaml.safe_load(
            (repo / ba.TEMPLATES_REL).read_text(encoding="utf-8"))["templates"]]
        assert ids == ["t_ok"]

    def test_k4_replaces_p2_section(self) -> None:
        overlay = {
            "runs": [_RUN_ID], "verification": kn.VERIFIED_DB, "query_guide": None,
            "query_examples": [], "query_rules": [], "section": _SECTION_OK, "descriptions": {},
            "synonyms": {}, "templates": [],
            "counts": {"query_guide": 0, "query_examples": 0, "query_rules": 0,
                       "prompt_section": 1, "column_descriptions": 0, "synonyms": 0,
                       "query_templates": 0},
        }
        assert kn.overlay_size(overlay) == 1
        text = ba.render_knowledge_section_file(_RUN_ID, _STAMP, overlay, "h")
        assert yaml.safe_load(text)["section"] == _SECTION_OK
        assert text.startswith("# plans/141 W4")

    def test_history_seed_reads_overlaid_examples(self, tmp_path, fixed_time) -> None:
        from scripts.query_history_seed import collect_profile_entries

        code, repo = _build(tmp_path, source=_source(tmp_path / "src"))
        assert code == ba.EXIT_OK
        entries = collect_profile_entries(repo / ba.PROFILE_REL.parent, verified_at="T")
        assert [(e["query"], e["sql"]) for e in entries["itam"]] == [
            ("서버 호스트 목록", "SELECT host_name FROM t_srv LIMIT 10")
        ]


# ──────────────────────────────────────────────
# 철회 반영 — 통과 0인 지식 산출 파일 정리
# ──────────────────────────────────────────────


def _withdrawn(files: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {name: [{**i, **_WITHDRAWN} for i in items] for name, items in files.items()}


_KNOWLEDGE_FILES = (ba.SECTION_REL, ba.DESCRIPTIONS_REL, ba.TEMPLATES_REL)


class TestRetraction:
    def test_withdrawn_templates_and_descriptions_removed(self, tmp_path, fixed_time,
                                                          capsys) -> None:
        source = _source(tmp_path / "src", templates=_TEMPLATES)
        code, repo = _build(tmp_path, source=source)
        assert code == ba.EXIT_OK
        assert all((repo / rel).is_file() for rel in _KNOWLEDGE_FILES)
        capsys.readouterr()

        # K8 전부 철회 · K3 설명 전부 철회(가이드·섹션은 그대로 통과)
        files = {**_MIXED, ka.DESCRIPTIONS_FILE: [
            {**i, **_WITHDRAWN} for i in _MIXED[ka.DESCRIPTIONS_FILE]
        ]}
        _source(source, files, templates=[{**t, **_WITHDRAWN} for t in _TEMPLATES])
        code, _ = _build(tmp_path, source=source, repo=repo)
        assert code == ba.EXIT_OK
        assert not (repo / ba.TEMPLATES_REL).exists()
        assert not (repo / ba.DESCRIPTIONS_REL).exists()
        assert (repo / ba.SECTION_REL).is_file()
        assert _profile(repo)["query_guide"] == _GUIDE_OK
        out = capsys.readouterr().out
        assert f"지움(철회 반영 — 이번 지식 통과분 0): {ba.TEMPLATES_REL}" in out
        assert f"지움(철회 반영 — 이번 지식 통과분 0): {ba.DESCRIPTIONS_REL}" in out

    def test_zero_passing_matches_no_overlay_build(self, tmp_path, fixed_time) -> None:
        run = _run_dir(tmp_path)
        repo_a = _repo(tmp_path / "a")
        assert ba.run_build(run, repo_root=repo_a) == ba.EXIT_OK  # 원천 없음
        repo = _repo(tmp_path / "b")
        _write(repo / ba.SYNONYM_SEED_REL, {
            "db_id": "itam", "column_synonyms": {"t_srv.host_name": ["기존낱말"]},
        })
        source = _source(tmp_path / "src", templates=_TEMPLATES)
        assert ba.run_build(run, repo_root=repo, knowledge_dir=source,
                            knowledge_deps=_deps(FakeClient())) == ba.EXIT_OK
        seeds = yaml.safe_load((repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8"))
        assert "호스트이름" in seeds["column_synonyms"]["t_srv.host_name"]

        # 전부 철회 → 통과 0: 지식 산출 파일 삭제 · 오버레이 낱말만 걷음(DB 주석 · 기존 낱말 남김)
        _source(source, _withdrawn(_MIXED), templates=[{**t, **_WITHDRAWN} for t in _TEMPLATES])
        assert ba.run_build(run, repo_root=repo, knowledge_dir=source,
                            knowledge_deps=_deps(FakeClient())) == ba.EXIT_OK
        assert not any((repo / rel).exists() for rel in _KNOWLEDGE_FILES)
        seeds = yaml.safe_load((repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8"))
        assert seeds["column_synonyms"]["t_srv.host_name"] == ["기존낱말", "서버 호스트명"]
        a, b = _outputs(repo_a), _outputs(repo)
        assert a.pop(str(ba.SYNONYM_SEED_REL)) != b.pop(str(ba.SYNONYM_SEED_REL))  # 기존낱말
        assert a == b

    def test_seed_file_removed_when_only_overlay_words(self, tmp_path, fixed_time,
                                                       capsys) -> None:
        catalog = _catalog()
        catalog["tables"]["t_srv"]["columns"][0] = _col("host_name", fmt="hostname")
        run = tmp_path / "results" / _RUN_ID
        _write(run / ba.CATALOG_FILE, catalog)  # DB 주석 근거 0
        source = _source(tmp_path / "src", {ka.SYNONYMS_FILE: _MIXED[ka.SYNONYMS_FILE]})
        code, repo = _build(tmp_path, source=source, static_only=True)
        assert code == ba.EXIT_OK and (repo / ba.SYNONYM_SEED_REL).is_file()
        capsys.readouterr()
        _source(source, _withdrawn({ka.SYNONYMS_FILE: _MIXED[ka.SYNONYMS_FILE]}))
        code, _ = _build(tmp_path, source=source, static_only=True, repo=repo)
        assert code == ba.EXIT_OK
        assert not (repo / ba.SYNONYM_SEED_REL).exists()
        assert f"지움(철회 반영 — 이번 지식 통과분 0): {ba.SYNONYM_SEED_REL}" in (
            capsys.readouterr().out
        )

    def test_unmarked_section_files_kept(self, tmp_path, fixed_time, capsys) -> None:
        p2_text = ba.render_section_file(_RUN_ID, _STAMP, {"section": "P2 섹션"})
        approved = "db_id: itam\nsection: 탭 승인 섹션\n"  # 「DB 구조」 탭 승인본(표지 없음)
        source = _source(tmp_path / "src", {ka.GUIDE_FILE: _MIXED[ka.GUIDE_FILE]})
        for text in (p2_text, approved):
            repo = _repo(tmp_path / str(len(text)))
            (repo / ba.SECTION_REL).parent.mkdir(parents=True, exist_ok=True)
            (repo / ba.SECTION_REL).write_text(text, encoding="utf-8")
            code, _ = _build(tmp_path, source=source, repo=repo)
            assert code == ba.EXIT_OK
            assert (repo / ba.SECTION_REL).read_text(encoding="utf-8") == text
            assert f"주의: {ba.SECTION_REL} 남김 — 지식 오버레이 산출 표지가 없다" in (
                capsys.readouterr().out
            )

    def test_db_unavailable_or_no_source_removes_nothing(self, tmp_path, fixed_time,
                                                         capsys) -> None:
        source = _source(tmp_path / "src", templates=_TEMPLATES)
        code, repo = _build(tmp_path, source=source)
        assert code == ba.EXIT_OK
        before = {str(rel): (repo / rel).read_text(encoding="utf-8") for rel in _KNOWLEDGE_FILES}
        words = yaml.safe_load(
            (repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8")
        )["column_synonyms"]["t_srv.host_name"]
        _source(source, _withdrawn(_MIXED), templates=[{**t, **_WITHDRAWN} for t in _TEMPLATES])
        capsys.readouterr()
        # 모의 DB 미연결(정적 전용 아님) — 검증 미수행
        code, _ = _build(tmp_path, source=source, repo=repo, client=ConnectionError("refused"))
        assert code == ba.EXIT_OK
        # 원천 디렉터리 없음
        code, _ = _build(tmp_path, source=tmp_path / "없음", repo=repo)
        assert code == ba.EXIT_OK
        after = {str(rel): (repo / rel).read_text(encoding="utf-8") for rel in _KNOWLEDGE_FILES}
        assert after == before
        # 유사어 시드는 오버레이 없는 빌드로 다시 쓰이지만 낱말은 하나도 걷지 않는다
        assert sorted(yaml.safe_load(
            (repo / ba.SYNONYM_SEED_REL).read_text(encoding="utf-8")
        )["column_synonyms"]["t_srv.host_name"]) == sorted(words)
        assert "호스트이름" in words
        assert "지움(" not in capsys.readouterr().out


def test_cli_passes_knowledge_args(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def fake_run_build(run_dir: Path, **kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(ba, "run_build", fake_run_build)
    assert cli.main([
        "--build-assets", str(tmp_path), "--knowledge", str(tmp_path / "k"),
        "--knowledge-static-only",
    ]) == 0
    assert seen["knowledge_dir"] == tmp_path / "k" and seen["knowledge_static_only"] is True
    assert isinstance(seen["knowledge_deps"], kn.KnowledgeDeps)
