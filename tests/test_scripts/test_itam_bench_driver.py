"""ITAM 질의 벤치 — 턴 루프 · 사전 점검 · 산출물 · 비교 (plans/135 W4·W5·W6).

벤치 서버·LLM·DB 를 띄우지 않는다. 클라이언트·감사 로그·측정 수신·오라클을 가짜로 주입한다(v1.2 —
하네스 모의 서버는 하네스 카탈로그만 읽어 벤치 시나리오를 모른다).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH, SCENARIOS_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.scenario.assertions import Observation

_NAME = "홍길동"
_NAME2 = "박서준"


@pytest.fixture(scope="module")
def policy() -> cat.ColumnPolicy:
    return cat.load_policy(POLICY_PATH)


@pytest.fixture(scope="module")
def scenarios(policy: cat.ColumnPolicy) -> dict[str, cat.Scenario]:
    return {s.id: s for s in cat.load_scenarios(SCENARIOS_PATH, policy)}


@pytest.fixture(scope="module")
def catalog_doc(policy: cat.ColumnPolicy) -> dict[str, Any]:
    return cat.build_schema_catalog(
        cat.load_schema_source("transcript"),
        policy,
        assets={"profile": {"fingerprint": "abc123def456"}},
    )


def _sid(thread_id: str) -> str:
    match = re.match(r"itam-bench-(ITAM-\d+)-", thread_id)
    assert match, thread_id
    return match.group(1)


class FakeClient:
    """시나리오별 응답 각본 — (상태, 결과 행, 응답 문장)."""

    def __init__(self, script: dict[tuple[str, int], dict[str, Any]]) -> None:
        self.script = script
        self.sent: list[dict[str, Any]] = []
        self.turns: dict[str, int] = {}
        self._results: dict[str, dict[str, Any]] = {}

    def send(self, endpoint: str, payload: dict[str, Any]) -> Observation:
        assert endpoint == "stream"
        self.sent.append(dict(payload))
        sid = _sid(payload["thread_id"])
        self.turns[payload["thread_id"]] = self.turns.get(payload["thread_id"], 0) + 1
        plan = self.script[(sid, self.turns[payload["thread_id"]])]
        obs = Observation(
            http_status=200,
            status=plan.get("status", "completed"),
            response=plan.get("response", "답변"),
            db_ids=plan.get("db_ids", ["itam"]),
            wall_ms=1234.5,
            retries=0,
            disclosures=[{"kind": "row_limit", "text": "x"}],
        )
        if plan.get("result") is not None:
            obs.query_id = f"q-{len(self.sent)}"
            self._results[obs.query_id] = plan["result"]
        return obs

    def download_csv(self, query_id: str) -> dict[str, Any]:
        return self._results[query_id]


class FakeTail:
    def __init__(self, by_scenario: dict[str, list[dict[str, Any]]]) -> None:
        self.by_scenario = by_scenario

    def mark(self) -> int:
        return 0

    def collect(self, since: int, thread_id: str) -> list[dict[str, Any]]:
        return [dict(e) for e in self.by_scenario.get(_sid(thread_id), [])]


def _result(columns: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "status": "ok",
        "columns": columns,
        "rows": [dict(zip(columns, r)) for r in rows],
        "total_rows": len(rows),
        "truncated": False,
        "reason": None,
    }


def _ctx(
    policy: cat.ColumnPolicy, catalog_doc: dict[str, Any], outcomes: dict[str, dict[str, Any]]
) -> cli.RunContext:
    def oracle(spec: Any, _anchor: str, _tag: str) -> dict[str, Any]:
        rows = outcomes[spec["id"]]
        return {
            "status": "ok",
            "reason": None,
            "rows_by_db": {"itam": rows},
            "elapsed_ms": 1.0,
            "phase": "post",
            "limit_by_db": {"itam": 100},
        }

    return cli.RunContext(
        run_id="20261006-120000",
        tier="intent_orchestration",
        policy=policy,
        catalog=jd.CatalogFacts.from_catalog(catalog_doc),
        vault=rd.PiiVault.from_policy(policy),
        oracle=oracle,
        denied_messages=("조회할 수 있는 DB가 없습니다",),
    )


_OWNER_SQL = (
    "SELECT sevrHostName AS 호스트명, rspblBrnName AS 담당부점, rspblPsnEmnm AS 담당자 "
    f"FROM TCDMSIF80 WHERE sevrHostName = 'svr-db-03' OR rspblPsnEmnm = '{_NAME2}' LIMIT 1000"
)


def _owner_run(scenarios, policy, catalog_doc):
    client = FakeClient(
        {
            ("ITAM-06", 1): {
                "result": _result(
                    ["호스트명", "담당부점", "담당자"], [["svr-db-03", "합성부점", _NAME]]
                )
            }
        }
    )
    audit = FakeTail(
        {
            "ITAM-06": [
                {
                    "sql": _OWNER_SQL.replace(" LIMIT 1000", ""),
                    "source": "itam",
                    "success": False,
                    "row_count": 0,
                    "retry_attempt": 0,
                    "error": f"(1064, \"You have an error in your SQL syntax near '{_NAME2}'\")",
                },
                {
                    "sql": _OWNER_SQL,
                    "source": "itam",
                    "success": True,
                    "row_count": 1,
                    "retry_attempt": 1,
                    "error": None,
                },
            ]
        }
    )
    capture = FakeTail(
        {
            "ITAM-06": [
                {
                    "kind": "task_pipeline_state",
                    "dbs": {
                        "itam": {
                            "structure_meta": False,
                            "tables": {
                                "TCDMSIF80": {
                                    "columns": ["sevrHostName", "rspblBrnName", "rspblPsnEmnm"],
                                    "with_meaning": [],
                                    "sample_rows": True,
                                }
                            },
                        }
                    },
                }
            ]
        }
    )
    ctx = _ctx(policy, catalog_doc, {"ITAM-06": [{"n": 1}]})
    records = cli.run_scenarios(
        [scenarios["ITAM-06"]],
        client=client,
        audit=audit,
        capture=capture,
        ctx=ctx,
        progress=lambda _t: None,
    )
    return records, ctx, client


class TestTurnLoop:
    def test_owner_turn_records_no_person_values(self, scenarios, policy, catalog_doc) -> None:
        records, ctx, client = _owner_run(scenarios, policy, catalog_doc)
        assert len(records) == 1
        record = records[0]
        text = json.dumps(record, ensure_ascii=False)
        assert _NAME not in text and _NAME2 not in text
        assert ctx.counters["canary_in_results"] == 1  # 관문 시험 성립 재료
        assert record["oracle"]["verdict"] == "pass"
        assert record["schema_context"]["gold_tables_presented"] is True
        assert record["schema_context"]["sample_rows_presented"] is True
        owner = {c["name"]: c for c in record["result"]["columns"]}["담당자"]
        assert owner["log_policy"] == "pii" and set(owner) == {
            "name",
            "source",
            "log_policy",
            "count",
            "nulls",
            "distinct",
        }
        assert "rspblPsnEmnm = '<가림>'" in record["executed_sqls"][1]["sql"]
        assert record["executed_sqls"][0]["error"] and "1064" in record["executed_sqls"][0]["error"]
        assert record["disclosure_kinds"] == ["row_limit"]
        # 첫 턴 요청 본문 = query + 벤치가 만든 thread_id 뿐(대상 DB 고정 주입 0)
        assert set(client.sent[0]) == {"query", "thread_id"}

    def test_trace_field_contract(self, scenarios, policy, catalog_doc) -> None:
        records, _ctx_, _client = _owner_run(scenarios, policy, catalog_doc)
        assert set(records[0]) == {
            "run_id",
            "id",
            "turn",
            "repeat",
            "category",
            "traps",
            "prompt",
            "reply_to",
            "send_keys",
            "expected",
            "tier",
            "status",
            "http_status",
            "db_ids",
            "disclosure_kinds",
            "executed_sqls",
            "result",
            "oracle",
            "observe",
            "taxonomy",
            "sql_analysis",
            "schema_context",
            "retries",
            "response_chars",
            "latency_ms",
            "error",
        }
        assert "response" not in records[0]  # 응답 원문은 기록하지 않는다(G-5)

    def test_multiturn_shares_thread_and_clarification_stops(
        self, scenarios, policy, catalog_doc
    ) -> None:
        hosts = [["svr-db-01"], ["svr-db-02"], ["svr-db-03"]]
        client = FakeClient(
            {
                ("ITAM-22", 1): {"result": _result(["호스트명"], hosts)},
                ("ITAM-22", 2): {"result": _result(["호스트명"], hosts)},
            }
        )
        sql = [
            {
                "sql": "SELECT sevrHostName FROM TCDMSIF80 "
                "WHERE manmenCtrcEndYmd BETWEEN '1' AND '2'",
                "source": "itam",
                "success": True,
            }
        ]
        ctx = _ctx(
            policy,
            catalog_doc,
            {
                "ITAM-07": [{"sevrHostName": h[0]} for h in hosts],
                "ITAM-22": [{"sevrHostName": h[0]} for h in hosts],
            },
        )
        records = cli.run_scenarios(
            [scenarios["ITAM-22"]],
            client=client,
            audit=FakeTail({"ITAM-22": sql}),
            capture=FakeTail({}),
            ctx=ctx,
            progress=lambda _t: None,
        )
        assert [r["turn"] for r in records] == [1, 2]
        assert len({payload["thread_id"] for payload in client.sent}) == 1
        assert records[0]["schema_context"] is None  # 수신 레코드 없음 → 판정 불가로 남는다
        asking = FakeClient({("ITAM-22", 1): {"status": "clarification", "result": None}})
        records = cli.run_scenarios(
            [scenarios["ITAM-22"]],
            client=asking,
            audit=FakeTail({}),
            capture=FakeTail({}),
            ctx=ctx,
            progress=lambda _t: None,
        )
        assert len(records) == 1 and "asked_back" in records[0]["taxonomy"]

    def test_each_scenario_gets_a_new_thread(self, scenarios, policy, catalog_doc) -> None:
        client = FakeClient(
            {
                ("ITAM-02", 1): {"result": _result(["호스트명"], [["a"]])},
                ("ITAM-03", 1): {"result": _result(["호스트명"], [["svr-web-01"]])},
            }
        )
        ctx = _ctx(
            policy,
            catalog_doc,
            {"ITAM-02": [{"n": 30}], "ITAM-03": [{"sevrHostName": "svr-web-01"}]},
        )
        ctx.repeat = 2
        records = cli.run_scenarios(
            [scenarios["ITAM-02"], scenarios["ITAM-03"]],
            client=client,
            audit=FakeTail({}),
            capture=FakeTail({}),
            ctx=ctx,
            progress=lambda _t: None,
        )
        assert len(records) == 4 and len({p["thread_id"] for p in client.sent}) == 4
        assert records[0]["oracle"]["verdict"] == "fail" and "no_sql" in records[0]["taxonomy"]
        assert records[2]["oracle"]["verdict"] == "pass"


class TestTails:
    def test_audit_tail_keeps_error_drops_user_fields(self, tmp_path: Path) -> None:
        log = tmp_path / "server.log"
        lines = [
            json.dumps(
                {
                    "event": "query_executed",
                    "thread_id": "t1",
                    "sql": "SELECT 1",
                    "source_name": "itam",
                    "row_count": 0,
                    "success": False,
                    "error": "1064 syntax",
                    "user_id": "5488923",
                    "retry_attempt": 0,
                }
            ),
            json.dumps({"event": "query_executed", "thread_id": "t2", "sql": "SELECT 2"}),
            "일반 로그 줄",
        ]
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tail = cli._audit_tail_class()(log, settle_sec=0.0)
        entries = tail.collect(0, "t1")
        assert entries == [
            {
                "sql": "SELECT 1",
                "source": "itam",
                "row_count": 0,
                "success": False,
                "retry_attempt": 0,
                "error": "1064 syntax",
            }
        ]

    def test_capture_tail_filters_thread_and_offset(self, tmp_path: Path) -> None:
        path = tmp_path / "capture.jsonl"
        path.write_text(json.dumps({"thread_id": "a", "n": 1}) + "\n", encoding="utf-8")
        tail = cli.CaptureTail(path)
        mark = tail.mark()
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"thread_id": "a", "n": 2}) + "\n")
            handle.write(json.dumps({"thread_id": "b", "n": 3}) + "\n")
        assert [r["n"] for r in tail.collect(mark, "a")] == [2]


class TestPreflight:
    def _cfg(
        self,
        worker: str = "mlx",
        orchestrator: str = "mlx",
        active=("polestar", "itam"),
        backend: str = "dbhub",
    ) -> SimpleNamespace:
        return SimpleNamespace(
            llm=SimpleNamespace(provider=worker),
            orchestrator=SimpleNamespace(provider=orchestrator),
            multi_db=SimpleNamespace(get_active_db_ids=lambda: list(active)),
            db_backend=backend,
        )

    def test_local_planes_pass(self) -> None:
        assert cli.preflight(self._cfg(), check_mlx=False) == []

    def test_billed_plane_refused_without_explicit_opt_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("RUN_E2E", raising=False)
        reasons = cli.preflight(self._cfg(orchestrator="gemini"), check_mlx=False)
        assert len(reasons) == 1 and "과금 평면" in reasons[0]
        monkeypatch.setenv("GEMINI_API_KEY", "x")  # 키 존재만으로 열리지 않는다
        assert cli.preflight(self._cfg(worker="gemini"), check_mlx=False)
        monkeypatch.setenv("RUN_E2E", "1")
        assert cli.preflight(self._cfg(worker="gemini"), check_mlx=False) == []

    def test_inactive_source_and_direct_backend(self) -> None:
        reasons = cli.preflight(self._cfg(active=("polestar",), backend="direct"), check_mlx=False)
        assert any("ACTIVE_DB_IDS" in r for r in reasons) and any("direct" in r for r in reasons)

    def test_unknown_provider_is_treated_as_billed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUN_E2E", raising=False)
        assert cli.preflight(self._cfg(worker=""), check_mlx=False)


# plans/135 §4 W7 — 대표 3문항 스모크(사용자 확정 2026-10-06). 모르는 ID 는 `select`가 막지만 환경
# 필터는 조용히 빼므로, 세 역할(관찰 · 카나리아 · 정답 대조)이 그대로인지 여기서 고정한다.
W7_SMOKE = ("ITAM-01", "ITAM-06", "ITAM-16")


class TestW7Smoke:
    def test_smoke_selection_keeps_three_roles(self, scenarios: dict[str, cat.Scenario]) -> None:
        selected = cat.select(list(scenarios.values()), env="sandbox", only=W7_SMOKE)
        picked = {s.id: s for s in selected}
        assert sorted(picked) == list(W7_SMOKE)
        observe = picked["ITAM-01"].turns[0]
        assert observe.oracle is None and observe.observe and observe.observe["no_data"]
        assert "pii_canary" in picked["ITAM-06"].traps and picked["ITAM-06"].turns[0].oracle
        oracle = picked["ITAM-16"].turns[0]
        assert oracle.oracle and oracle.oracle["compare"] == "rowset" and "자산관리" in oracle.query


class TestArtifacts:
    RUN = {
        "run_id": "20261006-120000",
        "env": "sandbox",
        "profile": "tier2_intent",
        "tier": "intent_orchestration",
        "planes": {"worker": "mlx", "orchestrator": "mlx"},
        "login_user": "5***",
        "operator": "c***",
        "git": {"sha": "da3ea4f00000", "dirty": True},
        "scenarios": 1,
        "repeat": 1,
        "sql_observed_turns": 1,
        "judged": True,
        "canary_in_results": 1,
        "assets": {"profile": {"fingerprint": "abc123def456"}},
    }

    def test_gated_write_of_real_records(
        self, tmp_path: Path, scenarios, policy, catalog_doc
    ) -> None:
        records, ctx, _client = _owner_run(scenarios, policy, catalog_doc)
        staged = cli.build_artifacts(run_meta=self.RUN, catalog_doc=catalog_doc, records=records)
        gate = rd.LeakGate(
            policy=policy,
            vault=ctx.vault,
            user_values={"login_id": "5488923", "os_user": "cptkang", "home": "/Users/cptkang"},
        )
        ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
        assert ok and violations == []
        assert sorted(p.name for p in (tmp_path / "run").iterdir()) == [
            "leak_check.json",
            "report.md",
            "run.json",
            "schema_catalog.yaml",
            "trace.jsonl",
        ]
        for path in (tmp_path / "run").iterdir():
            body = path.read_text(encoding="utf-8")
            assert _NAME not in body and _NAME2 not in body and "5488923" not in body
            assert "information_schema" not in body and "CREATE TABLE" not in body

    def test_report_snapshot(self) -> None:
        records = [
            json.loads(line)
            for line in (Path(__file__).parent / "fixtures" / "itam_bench_trace.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        text = rp.render_report(
            self.RUN, {"source": "transcript", "assets": self.RUN["assets"]}, records
        )
        expected = (Path(__file__).parent / "fixtures" / "itam_bench_report.md").read_text(
            encoding="utf-8"
        )
        assert text == expected

    def test_session_temp_dir_is_removed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = tmp_path / "session"
        session.mkdir()
        (session / "server.log").write_text("감사 줄", encoding="utf-8")
        monkeypatch.setattr(cli.tempfile, "mkdtemp", lambda prefix="": str(session))
        monkeypatch.setattr(cli, "preflight", lambda cfg, check_mlx=True: [])
        monkeypatch.setattr("src.config.load_config", lambda: SimpleNamespace())

        def boom(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("서버 기동 실패")

        monkeypatch.setattr(cli, "_run_with_server", boom)
        with pytest.raises(RuntimeError):
            cli.main(["--run", "--only", "ITAM-02", "--schema-source", "transcript"])
        assert not session.exists()


class TestCompare:
    def _write(self, directory: Path, run: dict[str, Any], records: list[dict[str, Any]]) -> None:
        directory.mkdir(parents=True)
        (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
        (directory / "trace.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
        )

    def _rec(self, sid: str, verdict: str, labels: list[str]) -> dict[str, Any]:
        return {
            "id": sid,
            "turn": 1,
            "repeat": 0,
            "oracle": {"verdict": verdict},
            "taxonomy": labels,
        }

    def test_transitions_and_label_delta(self, tmp_path: Path) -> None:
        run = {
            "run_id": "a",
            "assets": {"profile": {"fingerprint": "x"}},
            "tier": "t",
            "env": "sandbox",
        }
        self._write(
            tmp_path / "a",
            run,
            [self._rec("ITAM-07", "fail", ["date_text"]), self._rec("ITAM-09", "pass", [])],
        )
        self._write(
            tmp_path / "b",
            {**run, "run_id": "b", "assets": {"profile": {"fingerprint": "y"}}},
            [self._rec("ITAM-07", "pass", []), self._rec("ITAM-09", "pass", [])],
        )
        text = rp.compare_runs(tmp_path / "a", tmp_path / "b")
        assert "바뀐 자산: `profile`" in text
        assert "| ITAM-07 | 1 | fail | pass |" in text
        assert "| date_text | 1 | 0 | -1 |" in text

    def test_same_assets_notice_and_missing_run(self, tmp_path: Path) -> None:
        run = {"run_id": "a", "assets": {}, "tier": "t", "env": "sandbox"}
        self._write(tmp_path / "a", run, [self._rec("ITAM-02", "pass", [])])
        self._write(tmp_path / "b", {**run, "run_id": "b"}, [self._rec("ITAM-02", "pass", [])])
        assert "프롬프트 자산 변화 없음" in rp.compare_runs(tmp_path / "a", tmp_path / "b")
        with pytest.raises(FileNotFoundError):
            rp.compare_runs(tmp_path / "a", tmp_path / "missing")
