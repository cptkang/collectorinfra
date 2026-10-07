"""plans/141 W5 — `--verify-assets`(반입 K2·K4·K8 SQL 읽기 전용 실행 · 7번째 반출 파일) · D-314 ⑤.

실 DB·MCP 0 — 실행기는 가짜(`execute_sql`)로 주입한다. 값·SQL 원문·DB 오류 문구가 산출물에 실리지
않는지, 쓰기 SQL이 실행 전에 거절되는지, 행 수 구간 경계를 본다.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import asset_verify as av
from scripts.itam_bench import catalog as cat

_SECRET = "svr-db-03"


class FakeDb:
    """가짜 읽기 전용 클라이언트 — 받은 SQL 을 남기고 정한 행 수·예외를 돌려준다."""

    def __init__(self, rows: int = 3, error: BaseException | None = None) -> None:
        self.rows, self.error, self.calls = rows, error, []

    async def execute_sql(self, sql: str) -> Any:
        self.calls.append(sql)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(rows=[{"v": _SECRET}] * self.rows, truncated=False)


def _run(items: list[av.VerifyItem], db: FakeDb | None) -> list[dict[str, Any]]:
    return asyncio.run(av.averify(items, db.execute_sql if db else None))


def _item(sql: str, item_id: str = "e1") -> av.VerifyItem:
    return av.VerifyItem(item_id, av.KIND_EXAMPLE, sql)


@pytest.mark.parametrize(
    "count, bucket",
    [(0, "0"), (1, "1~10"), (10, "1~10"), (11, "11~100"), (100, "11~100"), (101, "100+")],
)
def test_row_bucket_boundaries(count: int, bucket: str) -> None:
    assert av.row_bucket(count) == bucket
    assert _run([_item("SELECT 1 FROM t")], FakeDb(rows=count))[0]["rows"] == bucket


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM TCDMSIF80",
        "UPDATE TCDMSIF80 SET a = 1",
        "INSERT INTO TCDMSIF80 VALUES (1)",
        "SELECT 1 FROM t; DROP TABLE t",
        "DROP TABLE TCDMSIF80",
        "SELECT SLEEP(5) FROM t",
        "SELECT a FROM t /*!50000 INTO OUTFILE '/tmp/x' */",
        "SELECT a /*M! INTO DUMPFILE '/tmp/x' */ FROM t",
        "",
    ],
)
def test_write_and_unsafe_sql_rejected_before_execution(sql: str) -> None:
    db = FakeDb()
    result = _run([_item(sql)], db)[0]
    assert (result["ok"], result["error"], result["rows"]) == (False, av.ERR_GUARD, None)
    assert db.calls == []


def test_execution_is_wrapped_with_outer_limit() -> None:
    db = FakeDb()
    result = _run([_item("SELECT a FROM t LIMIT 1000;")], db)[0]
    assert db.calls == ["SELECT * FROM (SELECT a FROM t LIMIT 1000) q LIMIT 101"]
    assert result == {
        "id": "e1", "kind": "K2", "fp": result["fp"], "ok": True, "error": None, "rows": "1~10",
    }
    assert len(result["fp"]) == 12


@pytest.mark.parametrize(
    "error, category",
    [
        (RuntimeError(f"(1146, \"Table 'itam.{_SECRET}' doesn't exist\")"), av.ERR_UNKNOWN_OBJECT),
        (RuntimeError(f"Unknown column '{_SECRET}' in 'where clause'"), av.ERR_UNKNOWN_OBJECT),
        (RuntimeError(f"You have an error in your SQL syntax near '{_SECRET}'"), av.ERR_SYNTAX),
        (RuntimeError("SELECT command denied to user 'kim'@'10.1.2.3'"), av.ERR_PERMISSION),
        (TimeoutError(), av.ERR_TIMEOUT),
        (ConnectionRefusedError("refused 10.1.2.3:3306"), av.ERR_CONNECTION),
        (ValueError(f"뭔가 이상하다 {_SECRET}"), av.ERR_DB),
    ],
)
def test_errors_become_categories_only(error: BaseException, category: str) -> None:
    result = _run([_item("SELECT 1 FROM t")], FakeDb(error=error))[0]
    assert (result["ok"], result["error"], result["rows"]) == (False, category, None)
    assert category in av.ERROR_CATEGORIES
    assert _SECRET not in repr(result) and "10.1.2.3" not in repr(result)


def test_slow_query_times_out() -> None:
    class Slow:
        async def execute_sql(self, sql: str) -> Any:
            await asyncio.sleep(1)

    result = asyncio.run(
        av.averify([_item("SELECT 1 FROM t")], Slow().execute_sql, timeout_sec=0.01)
    )[0]
    assert result["error"] == av.ERR_TIMEOUT


def test_truncated_flag_and_unreachable_client() -> None:
    class Truncating(FakeDb):
        async def execute_sql(self, sql: str) -> Any:
            return SimpleNamespace(rows=[{}] * 50, truncated=True)

    assert _run([_item("SELECT 1 FROM t")], Truncating())[0]["truncated"] is True

    @asynccontextmanager
    async def refused() -> Any:
        raise ConnectionRefusedError("no route")
        yield  # pragma: no cover

    items = [_item("SELECT 1 FROM t"), _item("DELETE FROM t", "e2")]
    results = asyncio.run(av.averify_with_client(items, refused))
    assert [r["error"] for r in results] == [av.ERR_CONNECTION, av.ERR_GUARD]


# ── 항목 수집(반입본) ─────────────────────────────────────────────────────────

_TEMPLATES = {
    "version": 1,
    "templates": [
        {
            "id": "server_by_dept",
            "intent": "부서별 서버 목록",
            "triggers": ["부서 서버"],
            "slots": [{"name": "dept_code", "type": "dept_code"}],
            "sql": "SELECT sevrHostName FROM TCDMSIF80 WHERE dept = :dept_code",
            "tables": ["TCDMSIF80"],
        },
        {
            "id": "server_by_state",
            "intent": "상태별 서버",
            "slots": [{"name": "state", "type": "code", "column": "TCDMSIF80.useYn"}],
            "sql": "SELECT sevrHostName FROM TCDMSIF80 WHERE useYn = :state",
            "tables": ["TCDMSIF80"],
        },
        {
            "id": "server_all",
            "intent": "전체 서버",
            "slots": [
                {"name": "state", "type": "code", "column": "TCDMSIF80.useYn", "required": False}
            ],
            "sql": "SELECT sevrHostName FROM TCDMSIF80 WHERE (:state IS NULL OR useYn = :state)",
            "tables": ["TCDMSIF80"],
        },
        {
            "id": "old_one",
            "intent": "철회",
            "status": "withdrawn",
            "sql": "SELECT 1 FROM TCDMSIF80",
            "tables": ["TCDMSIF80"],
        },
        {"id": "broken", "intent": "깨짐", "sql": "DELETE FROM TCDMSIF80", "tables": ["X"]},
    ],
}


def _repo(tmp_path: Path, *, code_values: dict[str, Any] | None = None) -> Path:
    profile: dict[str, Any] = {
        "source": "manual",
        "query_examples": [
            {"id": "ex_server_list", "question": "서버 목록", "sql": "SELECT 1 FROM TCDMSIF80"},
            {
                "question": f"{_SECRET} 담당자",
                "sql": f"SELECT 1 FROM TCDMSIF80 WHERE h = '{_SECRET}'",
            },
            {"id": "ex_server_list", "question": "중복 id", "sql": "SELECT 2 FROM TCDMSIF80"},
            {"id": "1bad id", "question": "모양 위반 id", "sql": "SELECT 3 FROM TCDMSIF80"},
        ],
    }
    if code_values is not None:
        profile["code_values"] = code_values
    knowledge = tmp_path / "config" / "knowledge" / "itam"
    knowledge.mkdir(parents=True)
    (tmp_path / "config" / "db_profiles").mkdir(parents=True)
    (tmp_path / "config" / "db_profiles" / "itam.yaml").write_text(
        yaml.safe_dump(profile, allow_unicode=True), encoding="utf-8"
    )
    section = (
        "규칙\n```sql\nSELECT 1 FROM TCDMSIF80\n```\n설명\n```sql\nSELECT 2 FROM TCDMSIF79\n```\n"
    )
    (knowledge / "prompt_template.yaml").write_text(
        yaml.safe_dump({"db_id": "itam", "section": section}, allow_unicode=True), encoding="utf-8"
    )
    (knowledge / "query_templates.yaml").write_text(
        yaml.safe_dump(_TEMPLATES, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


def test_collect_items_ids_and_pending_without_code_values(tmp_path: Path) -> None:
    items, sources = av.collect_items(_repo(tmp_path))
    ids = [(i.kind, i.id) for i in items]
    k2 = [i for k, i in ids if k == "K2"]
    assert k2[0] == "ex_server_list" and k2[2] == "ex_server_list-2"
    assert k2[1].startswith("k2-") and len(k2[1]) == 13 and k2[3].startswith("k2-")
    assert [i for k, i in ids if k == "K4"] == ["k4-1", "k4-2"]
    assert [i for k, i in ids if k == "K8"] == [
        "server_by_dept#1",
        "server_by_state#pending",
        "server_all#1",
        "server_all#pending",
    ]
    pending = next(i for i in items if i.id == "server_by_state#pending")
    assert pending.sql is None and pending.pending == av.PENDING_CODE
    assert sources["template_contract_issues"] == 1
    assert sources["query_examples"]["n"] == 4 and sources["prompt_template"]["n"] == 2


def test_collect_items_binds_code_slot_with_first_value(tmp_path: Path) -> None:
    items, _ = av.collect_items(_repo(tmp_path, code_values={"TCDMSIF80.useYn": ["Y", "N"]}))
    k8 = {i.id: i for i in items if i.kind == "K8"}
    assert set(k8) == {"server_by_dept#1", "server_by_state#1", "server_all#1", "server_all#2"}
    assert "'Y'" in (k8["server_by_state#1"].sql or "")
    assert k8["server_all#1"].slots == ("state",) and k8["server_all#2"].slots == ()


def test_missing_files_give_no_items(tmp_path: Path) -> None:
    assert av.collect_items(tmp_path)[0] == []


# ── 본체: 관문 · 산출물 ──────────────────────────────────────────────────────


def _cfg(backend: str = "dbhub") -> SimpleNamespace:
    return SimpleNamespace(db_backend=backend, dbhub=SimpleNamespace(server_url="http://u:p@h:9099"))


def _verify(tmp_path: Path, db: FakeDb, **kwargs: Any) -> tuple[int, Path, list[str]]:
    lines: list[str] = []

    @asynccontextmanager
    async def opener() -> Any:
        yield db

    results = tmp_path / "results"
    code = av.run_verify(
        policy=cat.load_policy(POLICY_PATH),
        env="sandbox",
        provenance={"sha": "abc", "dirty": False},
        user_values=kwargs.pop("user_values", {"login_id": None}),
        dsn=None,
        cfg=kwargs.pop("cfg", _cfg()),
        repo_root=_repo(tmp_path / "repo"),
        results_root=results,
        open_client=opener,
        say=lines.append,
    )
    runs = sorted(results.iterdir()) if results.exists() else []
    return code, (runs[0] if runs else results), lines


def test_run_verify_writes_seventh_file_without_values(tmp_path: Path) -> None:
    db = FakeDb(rows=12)
    code, run_dir, lines = _verify(tmp_path, db)
    assert code == 0, lines
    assert sorted(p.name for p in run_dir.iterdir()) == [
        av.VERIFICATION_FILE, "leak_check.json", "report.md", "run.json",
    ]
    doc = yaml.safe_load((run_dir / av.VERIFICATION_FILE).read_text(encoding="utf-8"))
    assert doc["summary"]["pending"] == 2 and doc["summary"]["error"] == 0
    assert {i["rows"] for i in doc["items"] if i["ok"]} == {"11~100"}
    for name in (av.VERIFICATION_FILE, "report.md", "run.json"):
        text = (run_dir / name).read_text(encoding="utf-8")
        assert _SECRET not in text and "SELECT" not in text and "u:p" not in text
    run = yaml.safe_load((run_dir / "run.json").read_text(encoding="utf-8"))
    assert run["mode"] == "verify_assets" and av.VERIFICATION_FILE in run["files"]
    assert "| server_by_state#pending | K8 | 보류 |" in (run_dir / "report.md").read_text("utf-8")
    assert all("SELECT * FROM (" in c and c.endswith("LIMIT 101") for c in db.calls)


def test_run_verify_error_items_exit_one(tmp_path: Path) -> None:
    db = FakeDb(error=RuntimeError("You have an error in your SQL syntax"))
    code, run_dir, _ = _verify(tmp_path, db)
    doc = yaml.safe_load((run_dir / av.VERIFICATION_FILE).read_text(encoding="utf-8"))
    assert code == 1 and doc["summary"]["by_error"] == {av.ERR_SYNTAX: doc["summary"]["error"]}


def test_run_verify_refuses_direct_backend(tmp_path: Path) -> None:
    code, run_dir, lines = _verify(tmp_path, FakeDb(), cfg=_cfg("direct"))
    assert code == 1 and "DB_BACKEND=direct" in lines[0] and not run_dir.exists()


def test_gate_failure_writes_only_leak_check(tmp_path: Path) -> None:
    # 원천 항목 id 가 사용자 정보와 같으면 관문에 걸린다 — 산출물을 쓰지 않는다
    code, run_dir, lines = _verify(tmp_path, FakeDb(), user_values={"login_id": "ex_server_list"})
    assert code == 1
    assert [p.name for p in run_dir.iterdir()] == ["leak_check.json"]
    assert any("미기록" in line for line in lines)


def test_cli_dispatches_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    from scripts.itam_bench import __main__ as cli

    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(av, "run_verify", fake)
    monkeypatch.setattr(cli, "_git_provenance", lambda: {"sha": "x", "dirty": False})
    monkeypatch.setattr(cli, "_itam_dsn", lambda: None)
    assert cli.main(["--verify-assets", "--env", "closed"]) == 0
    assert seen["env"] == "closed" and seen["policy"].scope == "closed"
