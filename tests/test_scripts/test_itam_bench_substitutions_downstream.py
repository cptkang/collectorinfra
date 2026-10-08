"""plans/145 W3 — 외부망 하류가 `substitutions.yaml`(형식 보존 가짜 값)을 자산에서 거절한다 · D-321.

빌더(`build_assets`)·`--validate-knowledge`·`--evidence`가 반출 run의 새 파일을 읽는지 본다 —
가짜 값이 든 산출 거부 · 짧은 값 하한 · 파일 없음 = 현행 · 형식 손상 = 닫힌 쪽 · K8 대표값은
`code_samples.yaml`에서만 · P2 제외 수는 코드값과 따로 센다. LLM·DB·Redis 0(가짜 LLM·DB · 저장소
루트·결과 루트는 tmp).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import knowledge as kn
from scripts.itam_bench import redact as rd
from scripts.itam_bench.redact import MIN_VALUE_LEN
from src.api.routes.db_structure import asset_sql_checker
from src.domain import knowledge_assets as ka

_RUN_ID = "20261101-000000"
_FAKE = "QX7MV"  # 형식 보존 가짜 값 — 커밋 산출물·검증 문구에 나오면 안 된다
_CODE = "WZ4K"  # 치환 코드값(code_samples.yaml) — K8 대표값


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _col(name: str) -> dict[str, Any]:
    return {"name": name, "type": "varchar", "nullable": True, "meaning": None,
            "meaning_source": "none"}


def _catalog() -> dict[str, Any]:
    return {
        "db_id": "itam", "source": "structure_store",
        "approved_profile": {"source": "manual", "environment": None,
                             "allowed_tables": ["t_srv", "t_app"]},
        "tables": {
            "t_srv": {"key": [], "rows_estimate": None, "relations": [],
                      "columns": [_col("host_name"), _col("srv_id")]},
            "t_app": {"key": [], "rows_estimate": None, "relations": [],
                      "columns": [_col("app_id"), _col("app_nm")]},
        },
    }


def _seed(notes: str = "주의 없음") -> dict[str, Any]:
    return {"db_id": "itam", "tables": {
        "t_srv": {"kind": "현행", "manages": "서버 원장(시드)", "notes": notes},
        "t_app": {"kind": "현행", "manages": "앱 원장(시드)"},
    }}


def _repo(tmp_path: Path, seed: dict[str, Any] | None = None) -> Path:
    repo = tmp_path / "repo"
    _write(repo / ba.SEED_DEFINITIONS_REL, seed or _seed())
    return repo


def _substitutions(values: list[Any]) -> dict[str, Any]:
    return {
        "db_id": "itam", "run_id": _RUN_ID,
        "note": "형식 보존 치환값 — 원값 아님 · 대응표 없음",
        "summary": {"values": len(values), "fallback": {"no_change": 0, "space": 0, "draws": 0}},
        "values": values,
    }


def _run(tmp_path: Path, *, fakes: list[Any] | None = None,
         samples: dict[str, Any] | None = None, raw: str | None = None) -> Path:
    run = tmp_path / "results" / _RUN_ID
    _write(run / ba.CATALOG_FILE, _catalog())
    if fakes is not None:
        _write(run / rd.SUBSTITUTIONS_FILE, _substitutions(fakes))
    if raw is not None:
        (run / rd.SUBSTITUTIONS_FILE).write_text(raw, encoding="utf-8")
    if samples is not None:
        _write(run / ba.CODE_SAMPLES_FILE, samples)
    return run


def _outputs(repo: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(repo))
        text = path.read_text(encoding="utf-8")
        if rel == str(ba.SCHEMA_SEED_REL):
            data = json.loads(text)
            for key in ("_cached_at", "_cached_at_iso"):
                data.pop(key, None)
            text = json.dumps(data, ensure_ascii=False, sort_keys=True)
        out[rel] = text
    return out


@pytest.fixture
def fixed_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ba.time, "strftime", lambda fmt, *a: "2026-11-01T00:00:00+0900")


# ──────────────────────────────────────────────
# ① 빌더 거부 · ② 짧은 값 하한 · ③ 파일 없음 · ④ 형식 손상
# ──────────────────────────────────────────────


class TestBuilder:
    def test_fake_value_in_output_refuses_and_hides_value(self, tmp_path, capsys) -> None:
        repo = _repo(tmp_path, _seed(notes=f"상태 '{_FAKE}' 주의"))
        run = _run(tmp_path, fakes=[_FAKE, "ZQ81T"])
        assert ba.run_build(run, repo_root=repo) == ba.EXIT_REFUSED
        out = capsys.readouterr().out
        assert "형식 보존 치환값(substitutions.yaml)이 커밋 대상 파일에 1곳" in out
        assert "config/db_profiles/itam.yaml table_definitions.t_srv.notes 스칼라" in out
        assert _FAKE not in out
        assert not (repo / ba.PROFILE_REL).exists()

    def test_clean_build_passes_and_reports_counts(self, tmp_path, capsys) -> None:
        repo = _repo(tmp_path)
        run = _run(tmp_path, fakes=[_FAKE, "ZQ81T", "7", "8231", "host_name"])
        assert ba.run_build(run, repo_root=repo) == ba.EXIT_OK
        out = capsys.readouterr().out
        assert (
            "형식 보존 치환값 파일 있음(차단 검사 통과 · 5건 · 짧은 값 1건·식별자 1건 대조 제외)"
        ) in out
        assert _FAKE not in (repo / ba.PROFILE_REL).read_text(encoding="utf-8")

    def test_short_value_floor(self) -> None:
        """한 글자 값과 세 자리 이하 숫자 값을 뺀다(W3 교정 · `blocked_fakes`)."""
        assert (MIN_VALUE_LEN, ba.FAKE_MIN_DIGIT_LEN) == (2, 4)
        catalog = _catalog()
        fakes = ["A", "7", "AB", "38", "143", "1234", "12345", "가", "가나", ""]
        blocked, counts = ba.blocked_fakes(fakes, catalog)
        assert blocked == {"AB", "1234", "12345", "가나"}
        assert counts == {"values": 9, "short_skipped": 5, "identifier_skipped": 0}
        # 하한 미만은 산출에 나와도 거부하지 않는다(우연 일치 잡음 방지)
        assert ba.substitution_hits(
            {"p.yaml": ("", {"n": "A 7 가 38건 plans/143"})}, blocked
        ) == []
        hits = ba.substitution_hits({"p.yaml": ("", {"n": "x 1234", "m": "AB 값"})}, blocked)
        assert hits == ["p.yaml n 스칼라", "p.yaml m 스칼라"]

    def test_identifier_values_excluded_like_code_samples(self) -> None:
        blocked, counts = ba.blocked_fakes(["host_name", "T_APP", _FAKE], _catalog())
        assert blocked == {_FAKE} and counts["identifier_skipped"] == 2

    def test_missing_file_is_current_behavior(self, tmp_path, fixed_time, capsys) -> None:
        repo_a = _repo(tmp_path / "a")
        run = _run(tmp_path)
        assert ba.load_substitutions(run) is None
        assert ba.run_build(run, repo_root=repo_a) == ba.EXIT_OK
        out = capsys.readouterr().out
        assert "형식 보존 치환값" not in out
        assert ba.build(run, repo_root=repo_a)["summary"]["substitutions"] is None
        # 차단 값이 산출과 무관하면 산출 바이트는 파일 없을 때와 같다
        (run / rd.SUBSTITUTIONS_FILE).write_text(
            yaml.safe_dump(_substitutions([_FAKE])), encoding="utf-8"
        )
        repo_b = _repo(tmp_path / "b")
        assert ba.run_build(run, repo_root=repo_b) == ba.EXIT_OK
        assert _outputs(repo_b) == _outputs(repo_a)

    @pytest.mark.parametrize("raw", [
        "values: [\n",                      # YAML 오류
        "- QX7MV\n",                         # 매핑 아님
        "db_id: itam\n",                     # values 없음
        "values: QX7MV\n",                   # 목록 아님
        "values: [QX7MV, 12345]\n",          # 문자열 아닌 원소
        "values: [QX7MV, {a: b}]\n",
    ])
    def test_malformed_file_refuses(self, tmp_path, capsys, raw) -> None:
        repo = _repo(tmp_path)
        run = _run(tmp_path, raw=raw)
        assert ba.run_build(run, repo_root=repo) == ba.EXIT_INPUT
        out = capsys.readouterr().out
        assert "substitutions.yaml 형식 오류" in out and "QX7MV" not in out
        assert not (repo / ba.PROFILE_REL).exists()


# ──────────────────────────────────────────────
# ⑤ --validate-knowledge 거절 · ⑥ K8 대표값은 code_samples에서만
# ──────────────────────────────────────────────

_SCHEMA = ba.schema_cache_dict(_catalog())
_ALLOWED = ["t_srv", "t_app"]


def _item(item_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": item_id, "origin": ka.ORIGIN, "evidence": _RUN_ID, "status": ka.STATUS_ACTIVE,
            **fields}


def _tpl(tid: str, sql: str, tables: list[str], slots: list[dict[str, Any]]) -> dict[str, Any]:
    return _item(tid, intent="의도", triggers=["표면어"], slots=slots, sql=sql, tables=tables)


class _Executor:
    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute_sql(self, sql: str) -> Any:
        from types import SimpleNamespace

        self.sqls.append(sql)
        return SimpleNamespace(rows=[])


def _knowledge(root: Path) -> Path:
    _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
        _item("g_ok", text="서버 원장은 `t_srv`이고 호스트는 `t_srv.host_name`으로 찾는다."),
        _item("g_fake", text=f"서버 원장 `t_srv`에서 상태 {_FAKE}는 제외한다."),
    ]})
    _write(root / ka.EXAMPLES_FILE, {"version": 1, "items": [
        _item("e_fake", question="앱 목록", description="앱",
              sql=f"SELECT app_id FROM t_app WHERE app_nm = '{_FAKE}' LIMIT 10",
              tables=["t_app"]),
    ]})
    return root


def _codes(result: dict[str, Any]) -> dict[str, list[str]]:
    return {r["id"]: [i["code"] for i in r["issues"]] for r in result["results"]}


class TestValidate:
    async def test_fake_values_rejected_without_value_in_message(self, tmp_path) -> None:
        root = _knowledge(tmp_path / "k")
        result = await kn.avalidate_dir(
            root, catalog=_SCHEMA, allowed=_ALLOWED, executor=_Executor(),
            sql_checker=asset_sql_checker, fake_values={_FAKE},
        )
        codes = _codes(result)
        assert codes["g_ok"] == []
        assert codes["g_fake"] == [kn.SUBSTITUTED] and codes["e_fake"] == [kn.SUBSTITUTED]
        message = next(r for r in result["results"] if r["id"] == "g_fake")["issues"][0]["message"]
        assert message.startswith("형식 보존 치환값(substitutions.yaml)이 나옵니다(위치: ")
        assert _FAKE not in repr(result)

    async def test_withdrawn_item_with_fake_value_rejected(self, tmp_path) -> None:
        root = tmp_path / "k"
        _write(root / ka.GUIDE_FILE, {"version": 1, "items": [
            _item("g_ok", text="서버 원장은 `t_srv`이다."),
            _item("g_old", text="옛 안내", status=ka.STATUS_WITHDRAWN, reason=f"{_FAKE} 회귀"),
            _item("g_old2", text="옛 안내 2", status=ka.STATUS_WITHDRAWN, reason="내부망 회귀"),
        ]})
        result = await kn.avalidate_dir(
            root, catalog=_SCHEMA, allowed=_ALLOWED, executor=None,
            sql_checker=asset_sql_checker, fake_values={_FAKE}, static_only=True,
        )
        codes = _codes(result)
        assert codes["g_old"] == [kn.SUBSTITUTED] and "g_old2" not in codes
        assert [w["id"] for w in result["withdrawn"]] == ["g_old2"]
        assert _FAKE not in repr(result)

    def test_load_substitution_values(self, tmp_path) -> None:
        _run(tmp_path, fakes=[_FAKE, "7", "host_name"])
        results = tmp_path / "results"
        values, runs, counts = kn.load_substitution_values({_RUN_ID, "없는run"}, results)
        assert values == {_FAKE} and runs == [_RUN_ID]
        assert counts == {"values": 3, "short_skipped": 1, "identifier_skipped": 1}
        # 파일 없는 run(1·2회차)은 건너뛴다
        assert kn.load_substitution_values({"없는run"}, tmp_path / "results") == (
            set(), [], {"values": 0, "short_skipped": 0, "identifier_skipped": 0}
        )

    def test_run_validate_rejects_and_reports(self, tmp_path, capsys) -> None:
        _run(tmp_path, fakes=[_FAKE])
        root = _knowledge(tmp_path / "k")
        schema = tmp_path / "schema.json"
        schema.write_text(json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
        repo = tmp_path / "repo"
        _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
        out_path = tmp_path / "validation.yaml"
        code = kn.run_validate(root, catalog_path=schema, static_only=True, out=out_path,
                               repo_root=repo, results_root=tmp_path / "results")
        assert code == kn.EXIT_FAILED
        out = capsys.readouterr().out
        assert f"형식 보존 치환값 대조: {_RUN_ID}(1개 · 짧은 값 0개·식별자 0개 제외" in out
        assert "거절 guide.yaml g_fake: substituted_literal" in out
        assert _FAKE not in out
        written = yaml.safe_load(out_path.read_text(encoding="utf-8"))
        assert written["substitutions_runs"] == [_RUN_ID]

    def test_run_validate_malformed_is_input_error(self, tmp_path, capsys) -> None:
        _run(tmp_path, raw="values: [QX7MV, 1]\n")
        root = _knowledge(tmp_path / "k")
        repo = tmp_path / "repo"
        _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
        schema = tmp_path / "schema.json"
        schema.write_text(json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
        code = kn.run_validate(root, catalog_path=schema, static_only=True, repo_root=repo,
                               results_root=tmp_path / "results")
        assert code == kn.EXIT_INPUT
        assert "substitutions.yaml 형식 오류" in capsys.readouterr().out

    def test_run_validate_without_file_prints_no_extra_line(self, tmp_path, capsys) -> None:
        _run(tmp_path)
        root = _knowledge(tmp_path / "k")
        repo = tmp_path / "repo"
        _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
        schema = tmp_path / "schema.json"
        schema.write_text(json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
        kn.run_validate(root, catalog_path=schema, static_only=True, repo_root=repo,
                        results_root=tmp_path / "results")
        assert "형식 보존 치환값" not in capsys.readouterr().out

    async def test_k8_representatives_only_from_code_samples(self, tmp_path) -> None:
        _run(tmp_path, fakes=[_FAKE], samples={"columns": {
            "t_app.app_nm": {"substitution": "ok", "values": [_CODE]},
        }})
        results = tmp_path / "results"
        samples = kn.load_code_samples({_RUN_ID}, results)
        assert samples == {"t_app.app_nm": [_CODE]}
        fakes = kn.load_substitution_values({_RUN_ID}, results)[0]
        assert fakes == {_FAKE}
        templates = [
            _tpl("t_app", "SELECT app_id FROM t_app WHERE app_nm = :k", ["t_app"],
                 [{"name": "k", "type": "code", "column": "t_app.app_nm"}]),
            _tpl("t_srv", "SELECT srv_id FROM t_srv WHERE host_name = :k", ["t_srv"],
                 [{"name": "k", "type": "code", "column": "t_srv.host_name"}]),
        ]
        root = tmp_path / "k8"
        _write(root / ka.TEMPLATES_FILE, {"version": 1, "templates": templates})
        executor = _Executor()
        result = await kn.avalidate_dir(
            root, catalog=_SCHEMA, allowed=_ALLOWED, executor=executor,
            sql_checker=asset_sql_checker, code_samples=samples, fake_values=fakes,
        )
        codes = _codes(result)
        assert codes["t_app"] == []
        # 가짜 값은 대표값으로 쓰지 않는다 — code_samples에 없는 컬럼은 실행 보류
        assert codes["t_srv"] == [kn.CODE_UNVERIFIED]
        assert executor.sqls == [
            f"SELECT * FROM (SELECT app_id FROM t_app WHERE app_nm = '{_CODE}') q LIMIT 50"
        ]
        assert all(_FAKE not in s for s in executor.sqls)


# ──────────────────────────────────────────────
# --evidence 고지 · 빌더 오버레이
# ──────────────────────────────────────────────


class TestEvidenceAndOverlay:
    def _evidence(self, tmp_path: Path, run: Path) -> int:
        policy = tmp_path / "policy.yaml"
        _write(policy, {"db_id": "itam", "tables": {}})
        repo = tmp_path / "repo"
        _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
        return kn.run_evidence(run, policy_path=policy, scenarios_path=tmp_path / "없는.yaml",
                               repo_root=repo, knowledge_dir=tmp_path / "knowledge")

    def test_evidence_index_notice_deterministic(self, tmp_path) -> None:
        run = _run(tmp_path, fakes=[_FAKE, "ZQ81T"])
        assert self._evidence(tmp_path, run) == kn.EXIT_OK
        index_path = run / kn.EVIDENCE_DIR / "index.yaml"
        first = index_path.read_bytes()
        index = yaml.safe_load(first)
        assert index["substitutions"] == (
            "가짜 값 2건(substitutions.yaml) — 형식 보존 치환값 · 원값 아님 · 자산에 쓰지 않는다"
        )
        assert self._evidence(tmp_path, run) == kn.EXIT_OK
        assert index_path.read_bytes() == first

    def test_evidence_without_file_has_no_notice(self, tmp_path) -> None:
        run = _run(tmp_path)
        files = kn.build_evidence_files(run, repo_root=self._repo(tmp_path),
                                        scenarios_path=tmp_path / "없는.yaml",
                                        knowledge_dir=tmp_path / "knowledge")
        assert "substitutions" not in yaml.safe_load(files["index.yaml"])

    def test_evidence_malformed_is_input_error(self, tmp_path) -> None:
        run = _run(tmp_path, raw="values: QX7MV\n")
        assert self._evidence(tmp_path, run) == kn.EXIT_INPUT

    @staticmethod
    def _repo(tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": {}})
        return repo

    def test_overlay_rejects_fake_value_items(self, tmp_path, capsys) -> None:
        run = _run(tmp_path, fakes=[_FAKE])
        repo = _repo(tmp_path)
        source = _knowledge(tmp_path / "src")
        code = ba.run_build(run, repo_root=repo, knowledge_dir=source,
                            knowledge_static_only=True)
        assert code == ba.EXIT_OK
        out = capsys.readouterr().out
        assert "거절 guide.yaml g_fake: substituted_literal" in out
        profile = yaml.safe_load((repo / ba.PROFILE_REL).read_text(encoding="utf-8"))
        assert profile["query_guide"].startswith("서버 원장은 `t_srv`")
        assert all(_FAKE not in text for text in _outputs(repo).values())


# ──────────────────────────────────────────────
# P2 — 코드값 제외와 형식 보존 치환값 제외를 따로 센다
# ──────────────────────────────────────────────


class TestP2SeparateCounts:
    def test_counts_split_and_section_fake(self) -> None:
        from tests.test_scripts import test_plan140_w5_p2 as w5

        fake_example = {
            "question": "앱", "sql": f"SELECT app_id FROM t_app WHERE app_nm = '{_FAKE}' LIMIT 10",
        }
        llm = w5.FakeLLM(
            w5._examples(w5._OK_EXAMPLE, w5._SUB_EXAMPLE, fake_example),
            w5._SECTION + f"\n- 사용 중 상태는 `app_st` = '{_FAKE}' 이다.\n",
        )
        result = asyncio.run(ba.build_p2(
            w5._catalog(), llm=llm, client=w5.FakeClient(), sql_checker=w5.RecordingChecker(),
            code_values={w5._SUBSTITUTE}, fake_values={_FAKE},
        ))
        assert result["query_examples"] == [w5._OK_EXAMPLE]
        assert result["excluded_substituted"] == 1 and result["excluded_fake"] == 1
        assert result["section"] is None
        assert result["section_substituted"] is False and result["section_fake"] is True

    def test_header_and_stdout(self, tmp_path, capsys) -> None:
        from tests.test_scripts import test_plan140_w5_p2 as w5

        p2 = {"query_examples": [w5._OK_EXAMPLE], "excluded_substituted": 1, "excluded_fake": 2,
              "section": None, "section_fake": True, "checks": {}}
        header = ba.profile_header(_RUN_ID, "t", counts={}, dropped=[], evidence={}, p2=p2)
        assert (
            "치환 코드 리터럴 제외 1건 · 형식 보존 치환값 리터럴 제외 2건)" in header
        )
        ba._print_p2(p2)
        out = capsys.readouterr().out
        assert "치환 코드 리터럴 제외 1 · 형식 보존 치환값 리터럴 제외 2 · 씀 1" in out
        assert "형식 보존 치환값 리터럴 포함 — 통째로 버림" in out
        # 형식 보존 치환값 제외 0이면 종전 문구 그대로
        header = ba.profile_header(_RUN_ID, "t", counts={}, dropped=[], evidence={},
                                   p2={**p2, "excluded_fake": 0})
        assert "치환 코드 리터럴 제외 1건)" in header
