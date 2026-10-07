"""plans/141 W1·W2 — 근거 묶음(`--evidence`) · 원천 파일 검증(`--validate-knowledge`).

DB·LLM 0 — 검증의 DB 실행은 가짜 실행기(테스트 더블)로 시험한다. SQL 검사기는 「DB 구조」 탭과 같은
`asset_sql_checker`(validate_sql · 한글 식별자 허용)를 그대로 쓴다. 실 반출 run은 있을 때만 본다.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import RESULTS_ROOT
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import build_assets as ba
from scripts.itam_bench import knowledge as kn
from src.api.routes.db_structure import asset_sql_checker
from src.domain import knowledge_assets as ka

_RUN_ID = "20261101-000000"
_SECRET = "RUNX7"  # 치환 코드값(원값 대체) — 산출물·근거 묶음에 나오면 안 된다
_USER = "5488923"  # 누출 관문 user_info 규칙 유발값

_SCHEMA = {
    "tables": {
        "zzab01": {
            "columns": [
                {"name": "그룹코드", "type": "varchar"},
                {"name": "서버호스트명", "type": "varchar"},
                {"name": "활성화여부", "type": "char"},
                {"name": "상태구분", "type": "char"},
            ]
        },
        "zzab02": {
            "columns": [
                {"name": "그룹코드", "type": "varchar"},
                {"name": "서버호스트명", "type": "varchar"},
            ]
        },
        "zzab03": {
            "columns": [
                {"name": "그룹코드", "type": "varchar"},
                {"name": "기준년월일", "type": "char"},
            ]
        },
    }
}
_ALLOWED = ["zzab01", "zzab02", "zzab03"]
_DEFS = {"zzab01": {"kind": "현행"}, "zzab02": {"kind": "수집적재"}, "zzab03": {"kind": "수집이력"}}


def _item(item_id: str, **fields: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "origin": ka.ORIGIN,
        "evidence": _RUN_ID,
        "status": ka.STATUS_ACTIVE,
        **fields,
    }


def _write(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")


_FIXTURES: dict[str, list[dict[str, Any]]] = {
    ka.GUIDE_FILE: [
        _item("g_ok", text="서버 원장은 `zzab01`이고 호스트는 `zzab01.서버호스트명`으로 찾는다."),
        _item("g_hall", text="호스트는 `zzab01.호스트명`으로 찾는다."),
        _item("g_fence", text="예시\n```sql\nSELECT 1\n```"),
        _item("g_brace", text="값은 {x}이다"),
        _item("g_util", text="서버 CPU 사용률은 `zzab01`에서 조회한다."),
        _item("g_old", status=ka.STATUS_WITHDRAWN, reason="내부망 회귀"),
    ],
    ka.EXAMPLES_FILE: [
        _item(
            "e_ok",
            question="서버 호스트 목록",
            description="원장 호스트",
            sql="SELECT 서버호스트명 FROM zzab01 LIMIT 10",
            tables=["zzab01"],
        ),
        _item(
            "e_hall",
            question="없는 칸",
            description="환각",
            sql="SELECT 호스트명 FROM zzab01 LIMIT 10",
            tables=["zzab01"],
        ),
        _item(
            "e_sub",
            question="운영 상태 서버",
            description="치환값",
            sql=f"SELECT 서버호스트명 FROM zzab01 WHERE 상태구분 = '{_SECRET}' LIMIT 10",
            tables=["zzab01"],
        ),
        _item(
            "e_guard",
            question="지우기",
            description="쓰기",
            sql="DELETE FROM zzab01",
            tables=["zzab01"],
        ),
        _item(
            "e_tables",
            question="그룹 호스트",
            description="불일치",
            sql="SELECT 서버호스트명 FROM zzab02 LIMIT 10",
            tables=["zzab01"],
        ),
        _item(
            "e_fail",
            question="그룹 목록",
            description="DB 실패",
            sql="SELECT 그룹코드 FROM zzab03 LIMIT 10",
            tables=["zzab03"],
        ),
    ],
    ka.DESCRIPTIONS_FILE: [
        _item("d_ok", table="zzab01", column="활성화여부", text="현재 사용 중인 원장 행 표시"),
        _item("d_val", table="zzab01", column="상태구분", text="1:정상, 2:장애"),
    ],
    ka.SYNONYMS_FILE: [
        _item("s_ok", table="zzab01", column="서버호스트명", words=["호스트이름"]),
        _item("s_conf", table="zzab01", column="그룹코드", words=["상태구분"]),
        _item("s_amb1", table="zzab01", column="활성화여부", words=["상태값"]),
        _item("s_amb2", table="zzab01", column="상태구분", words=["상태값"]),
        _item("s_short", table="zzab02", column="그룹코드", words=["그"]),
    ],
    ka.SECTION_FILE: [
        _item(
            "p_ok",
            text="- 호스트는 `zzab01.서버호스트명`.\n"
            "```sql\nSELECT 서버호스트명 FROM zzab01 LIMIT 5\n```",
        ),
        _item("p_hall", text="- 원장 키는 `zzab01.관리키`."),
    ],
}

_EXPECTED = {
    (ka.GUIDE_FILE, "g_ok"): [],
    (ka.GUIDE_FILE, "g_hall"): [ka.UNKNOWN_IDENTIFIER],
    (ka.GUIDE_FILE, "g_fence"): [ka.CODE_FENCE],
    (ka.GUIDE_FILE, "g_brace"): [ka.BRACES],
    (ka.GUIDE_FILE, "g_util"): [ka.UTILIZATION_RULE],
    (ka.EXAMPLES_FILE, "e_ok"): [],
    (ka.EXAMPLES_FILE, "e_hall"): [kn.SQL_INVALID],
    (ka.EXAMPLES_FILE, "e_sub"): [kn.SUBSTITUTED],
    (ka.EXAMPLES_FILE, "e_guard"): [kn.SQL_GUARD],
    (ka.EXAMPLES_FILE, "e_tables"): [kn.TABLES_MISMATCH],
    (ka.EXAMPLES_FILE, "e_fail"): [kn.DB_FAILED],
    (ka.DESCRIPTIONS_FILE, "d_ok"): [],
    (ka.DESCRIPTIONS_FILE, "d_val"): [ka.VALUE_LITERAL],
    (ka.SYNONYMS_FILE, "s_ok"): [],
    (ka.SYNONYMS_FILE, "s_conf"): [ka.NAME_CONFLICT],
    (ka.SYNONYMS_FILE, "s_amb1"): [ka.AMBIGUOUS],
    (ka.SYNONYMS_FILE, "s_amb2"): [ka.AMBIGUOUS],
    (ka.SYNONYMS_FILE, "s_short"): [ka.TOO_SHORT],
    (ka.SECTION_FILE, "p_ok"): [],
    (ka.SECTION_FILE, "p_hall"): [ka.UNKNOWN_IDENTIFIER],
}


class FakeExecutor:
    """읽기 전용 실행 더블 — 받은 SQL을 적고, `zzab03`을 읽는 SQL은 실패시킨다."""

    def __init__(self) -> None:
        self.sqls: list[str] = []

    async def execute_sql(self, sql: str) -> Any:
        self.sqls.append(sql)
        if "zzab03" in sql:
            raise RuntimeError("table missing")
        return SimpleNamespace(rows=[{"x": 1}])


@pytest.fixture
def knowledge_dir(tmp_path: Path) -> Path:
    root = tmp_path / "knowledge"
    for name, items in _FIXTURES.items():
        _write(root / name, {"version": 1, "items": items})
    return root


async def _validate(knowledge_dir: Path, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = dict(
        catalog=_SCHEMA,
        allowed=_ALLOWED,
        executor=FakeExecutor(),
        sql_checker=asset_sql_checker,
        definitions=_DEFS,
        code_values={_SECRET},
    )
    params.update(kwargs)
    return await kn.avalidate_dir(knowledge_dir, **params)


def _by_id(result: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(r["file"], r["id"]): r for r in result["results"]}


class TestValidate:
    async def test_rejection_reasons_exact(self, knowledge_dir: Path) -> None:
        executor = FakeExecutor()
        result = await _validate(knowledge_dir, executor=executor)
        found = _by_id(result)
        assert set(found) == set(_EXPECTED)
        for key, codes in _EXPECTED.items():
            assert [i["code"] for i in found[key]["issues"]] == codes, key
            assert found[key]["ok"] is (not codes), key
        assert not kn.passed(result)
        assert result["withdrawn"] == [
            {"file": ka.GUIDE_FILE, "id": "g_old", "reason": "내부망 회귀"}
        ]
        assert result["skipped"] == []  # K8은 W4에서 연결 — test_plan141_build_overlay
        assert len(result["derived"]["query_rules"]) == 3 and result["derived"]["issues"] == []
        # 정적 통과분만 실행하고 바깥 행 제한을 씌운다(읽기 전용)
        assert all(s.lstrip().upper().startswith("SELECT") and "LIMIT" in s for s in executor.sqls)
        assert not any(_SECRET in s or "DELETE" in s for s in executor.sqls)
        assert any("서버호스트명" in s and "zzab01" in s for s in executor.sqls)
        assert _SECRET not in repr(result)

    async def test_only_normal_passes(self, knowledge_dir: Path) -> None:
        result = await _validate(knowledge_dir)
        passing = sorted(r["id"] for r in result["results"] if r["ok"])
        assert passing == ["d_ok", "e_ok", "g_ok", "p_ok", "s_ok"]

    async def test_no_executor_is_failure(self, knowledge_dir: Path) -> None:
        found = _by_id(await _validate(knowledge_dir, executor=None))
        for key in [
            (ka.EXAMPLES_FILE, "e_ok"),
            (ka.SECTION_FILE, "p_ok"),
            (ka.EXAMPLES_FILE, "e_fail"),
        ]:
            assert [i["code"] for i in found[key]["issues"]] == [kn.DB_UNVERIFIED]
            assert found[key]["ok"] is False

    async def test_static_only_tolerates_unverified(self, knowledge_dir: Path) -> None:
        result = await _validate(knowledge_dir, executor=None, static_only=True)
        found = _by_id(result)
        assert found[(ka.EXAMPLES_FILE, "e_ok")]["ok"] is True
        assert found[(ka.EXAMPLES_FILE, "e_fail")]["ok"] is True  # 실행 못 해 실패를 모른다
        assert found[(ka.EXAMPLES_FILE, "e_hall")]["ok"] is False
        assert result["db_executed"] is False and result["summary"][kn.DB_UNVERIFIED] == 3

    async def test_executable_comment_rejected_before_execution(self, tmp_path: Path) -> None:
        """MariaDB 실행 주석(`/*! … */`) — K2·K4는 `sql_guard`로 거절되고 DB에 가지 않는다."""
        hidden = "/*!50000 INTO OUTFILE '/tmp/x' */"
        root = tmp_path / "exec"
        _write(root / ka.EXAMPLES_FILE, {"version": 1, "items": [_item(
            "e_exec", question="호스트", description="실행 주석",
            sql=f"SELECT 서버호스트명 FROM zzab01 {hidden} LIMIT 10", tables=["zzab01"],
        )]})
        _write(root / ka.SECTION_FILE, {"version": 1, "items": [_item(
            "p_exec",
            text=f"- 호스트.\n```sql\nSELECT 서버호스트명 FROM zzab01 {hidden} LIMIT 5\n```",
        )]})
        executor = FakeExecutor()
        found = _by_id(await _validate(root, executor=executor))
        for key in [(ka.EXAMPLES_FILE, "e_exec"), (ka.SECTION_FILE, "p_exec")]:
            assert kn.SQL_GUARD in [i["code"] for i in found[key]["issues"]], key
        assert executor.sqls == []

    def test_executable_comment_in_bound_template_sql(self) -> None:
        """K8 조립 SQL도 실행 주석이면 검사기·DB 전에 거절."""
        sql = "SELECT 서버호스트명 FROM zzab01 /*!50000 INTO OUTFILE '/tmp/x' */"
        assert kn._bound_sql_problem(sql, SimpleNamespace())[0] == kn.SQL_GUARD

    async def test_normal_only_dir_passes(self, tmp_path: Path) -> None:
        root = tmp_path / "ok"
        for name, items in _FIXTURES.items():
            keep = [i for i in items if i["id"].endswith("_ok")]
            _write(root / name, {"version": 1, "items": keep})
        assert kn.passed(await _validate(root))

    async def test_guide_total_length(self, tmp_path: Path) -> None:
        text = "서버 원장은 `zzab01`이다. " + "가" * 4100
        _write(
            tmp_path / ka.GUIDE_FILE,
            {"version": 1, "items": [_item("a", text=text), _item("b", text=text)]},
        )
        found = _by_id(await _validate(tmp_path))
        assert found[(ka.GUIDE_FILE, "a")]["ok"] and found[(ka.GUIDE_FILE, "b")]["ok"]
        assert [i["code"] for i in found[(ka.GUIDE_FILE, "*")]["issues"]] == [ka.TOO_LONG]

    async def test_broken_yaml(self, tmp_path: Path) -> None:
        (tmp_path / ka.GUIDE_FILE).write_text("version: 1\nitems: [\n", encoding="utf-8")
        found = _by_id(await _validate(tmp_path))
        assert [i["code"] for i in found[(ka.GUIDE_FILE, "*")]["issues"]] == [ka.FILE_INVALID]

    def test_code_values_from_evidence_run(self, knowledge_dir: Path, tmp_path: Path) -> None:
        run = tmp_path / "results" / _RUN_ID
        _write(run / ba.CATALOG_FILE, {"tables": {"zzab01": {"columns": [{"name": "상태구분"}]}}})
        _write(run / ba.CODE_SAMPLES_FILE, {"columns": {"zzab01.상태구분": {"values": [_SECRET]}}})
        values, runs = kn.load_code_values(kn.evidence_runs(knowledge_dir), tmp_path / "results")
        assert runs == [_RUN_ID] and _SECRET in values
        assert kn.load_code_values({"없는run"}, tmp_path / "results") == (set(), [])


class _Client:
    def __init__(self, healthy: bool = True) -> None:
        self.healthy = healthy

    async def health_check(self) -> bool:
        return self.healthy

    async def execute_sql(self, sql: str) -> Any:
        return SimpleNamespace(rows=[])


def _deps(client: Any) -> kn.KnowledgeDeps:
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory() -> Any:
        if isinstance(client, Exception):
            raise client
        yield client

    return kn.KnowledgeDeps(client_factory=factory, sql_checker=asset_sql_checker)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _write(repo / ba.PROFILE_REL, {"allowed_tables": _ALLOWED, "table_definitions": _DEFS})
    return repo


class TestRunValidate:
    def _run(self, tmp_path: Path, knowledge_dir: Path, **kwargs: Any) -> int:
        catalog = tmp_path / "schema.json"
        catalog.write_text(json.dumps(_SCHEMA, ensure_ascii=False), encoding="utf-8")
        return kn.run_validate(
            knowledge_dir,
            catalog_path=catalog,
            repo_root=_repo(tmp_path),
            results_root=tmp_path / "results",
            **kwargs,
        )

    def test_static_only_first_line(self, tmp_path: Path, knowledge_dir: Path, capsys: Any) -> None:
        out = tmp_path / "out" / "validation.yaml"
        assert self._run(tmp_path, knowledge_dir, static_only=True, out=out) == kn.EXIT_FAILED
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("모의 DB 실행 미실시")
        written = yaml.safe_load(out.read_text(encoding="utf-8"))
        assert written["static_only"] is True and written["summary"]["failed"] > 0

    def test_db_unavailable_fails(self, tmp_path: Path, capsys: Any) -> None:
        root = tmp_path / "ok"
        _write(root / ka.EXAMPLES_FILE, {"version": 1, "items": _FIXTURES[ka.EXAMPLES_FILE][:1]})
        code = self._run(tmp_path, root, deps=_deps(ConnectionError("refused")))
        assert code == kn.EXIT_FAILED
        assert (
            capsys.readouterr()
            .out.splitlines()[0]
            .startswith("모의 DB 실행 미실시 — itam 소스 연결 실패")
        )
        assert self._run(tmp_path, root, deps=_deps(_Client(healthy=False))) == kn.EXIT_FAILED
        assert self._run(tmp_path, root, deps=_deps(_Client())) == kn.EXIT_OK
        assert capsys.readouterr().out.splitlines()[-4].startswith("모의 DB 실행 —")

    def test_missing_dir_is_input_error(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, tmp_path / "없음", static_only=True) == kn.EXIT_INPUT


class TestCli:
    def test_existing_modes_untouched(self) -> None:
        args = cli.build_parser().parse_args([])
        assert args.evidence is None and args.validate_knowledge is None
        assert args.static_only is False and args.out is None and args.catalog is None
        args = cli.build_parser().parse_args(["--validate-knowledge"])
        assert args.validate_knowledge == ""
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--evidence", "x", "--dry-run"])

    def test_validate_cli_empty_dir(self, tmp_path: Path, capsys: Any) -> None:
        if (
            not (kn.REPO_ROOT / ba.SCHEMA_SEED_REL).is_file()
            or not (kn.REPO_ROOT / ba.PROFILE_REL).is_file()
        ):
            pytest.skip("ITAM 시드 카탈로그·프로필 없음")
        code = cli.main(["--validate-knowledge", str(tmp_path), "--static-only"])
        out = capsys.readouterr().out.splitlines()
        assert out[0].startswith("모의 DB 실행 미실시")
        assert code == kn.EXIT_OK  # 원천 0개 · 현행 정의의 K6 파생 규칙 문제 0


# ──────────────────────────────────────────────
# 근거 묶음 (W1)
# ──────────────────────────────────────────────


def _export(tmp_path: Path, *, meaning: str = "서버 원장") -> Path:
    run = tmp_path / "results" / _RUN_ID
    _write(
        run / ba.CATALOG_FILE,
        {
            "approved_profile": {"allowed_tables": _ALLOWED},
            "tables": {
                "zzab01": {
                    "meaning": meaning,
                    "meaning_source": "db_comment",
                    "columns": [
                        {
                            "name": "상태구분",
                            "type": "char",
                            "meaning": f"상태({_SECRET}:운영, B:중지)",
                            "meaning_source": "db_comment",
                            "profile": {"candidate": "code", "distinct": 2, "flag": [_SECRET]},
                        },
                    ],
                },
                "zzab02": {"columns": [{"name": "그룹코드", "type": "varchar"}]},
            },
        },
    )
    _write(run / ba.CODE_SAMPLES_FILE, {"columns": {"zzab01.상태구분": {"values": [_SECRET]}}})
    record = {
        "id": "ITAM-1",
        "turn": 1,
        "repeat": 0,
        "status": "completed",
        "prompt": "서버 상태",
        "taxonomy": [],
        "result": {"status": "empty", "columns": [{"top_values": [_SECRET]}]},
        "executed_sqls": [
            {
                "sql": f"SELECT 1 FROM zzab01 WHERE 상태구분='{_SECRET}'",
                "source": "itam",
                "success": True,
            }
        ],
    }
    (run / kn.TRACE_FILE).write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return run


def _policy(tmp_path: Path) -> Path:
    path = tmp_path / "policy.yaml"
    _write(path, {"db_id": "itam", "tables": {}})
    return path


def _evidence(tmp_path: Path, run: Path, **kwargs: Any) -> int:
    return kn.run_evidence(
        run,
        policy_path=_policy(tmp_path),
        scenarios_path=tmp_path / "없는.yaml",
        repo_root=_repo(tmp_path),
        knowledge_dir=tmp_path / "knowledge",
        **kwargs,
    )


def _snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


class TestEvidence:
    def test_writes_deterministic_without_values(self, tmp_path: Path) -> None:
        run = _export(tmp_path)
        assert _evidence(tmp_path, run) == kn.EXIT_OK
        out = run / kn.EVIDENCE_DIR
        first = _snapshot(out)
        assert {
            "index.yaml",
            "p1.yaml",
            "turns.yaml",
            "tables.zzab.yaml",
            "leak_check.json",
        } <= set(first)
        assert _evidence(tmp_path, run) == kn.EXIT_OK
        assert _snapshot(out) == first
        # 값 칸(주석 열거·프로필 값·결과 값) 0 — 실행 SQL 글은 이미 반출된 trace 그대로 싣는다
        for name, body in first.items():
            if name.endswith(".yaml") and name != "turns.yaml":
                assert _SECRET.encode() not in body, name
        turns = yaml.safe_load(first["turns.yaml"])
        assert turns["turns"][0]["failure_reasons"] == ["result:empty"]
        assert turns["turns"][0]["result"] == {"status": "empty"}
        table = yaml.safe_load(first["tables.zzab.yaml"])["tables"]["zzab01"]
        assert table["allowed"] is True and table["comment"] == "서버 원장"
        assert table["columns"][0]["meaning"] == "상태" and table["columns"][0]["comment_enum"] == 2

    def test_leak_gate_blocks_write(self, tmp_path: Path, capsys: Any) -> None:
        run = _export(tmp_path)
        assert _evidence(tmp_path, run) == kn.EXIT_OK
        leaky = _export(tmp_path, meaning=f"담당 {_USER}")
        code = _evidence(tmp_path, leaky, user_values={"login_id": _USER})
        assert code == kn.EXIT_FAILED
        out = leaky / kn.EVIDENCE_DIR
        assert sorted(p.name for p in out.iterdir()) == ["leak_check.json"]  # 옛 묶음도 지운다
        assert _USER not in capsys.readouterr().out

    def test_missing_export_is_input_error(self, tmp_path: Path) -> None:
        assert _evidence(tmp_path, tmp_path / "없는run") == kn.EXIT_INPUT


_REAL_RUN = RESULTS_ROOT / "20261006-152938"


@pytest.mark.skipif(
    not (_REAL_RUN / ba.CATALOG_FILE).is_file(), reason="실 반출 run 없음(git 무시)"
)
def test_real_export_evidence_deterministic() -> None:
    first = kn.build_evidence_files(_REAL_RUN)
    assert kn.build_evidence_files(_REAL_RUN) == first
    assert "index.yaml" in first and any(n.startswith("tables.") for n in first)
