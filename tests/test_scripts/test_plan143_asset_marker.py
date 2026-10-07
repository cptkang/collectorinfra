"""plans/143 W1(자산 사용 표지) · W8(자산별 끄기 · 자산별 효과 표·유지 규칙) — D-316 ⑤.

LLM·DB·서버 0. 수신기는 합성 상태로, 끄기는 로더 대역으로, 리포트는 가짜 run 두 벌로 본다.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.itam_bench import POLICY_PATH
from scripts.itam_bench import __main__ as cli
from scripts.itam_bench import _serve as serve
from scripts.itam_bench import catalog as cat
from scripts.itam_bench import judge as jd
from scripts.itam_bench import redact as rd
from scripts.itam_bench import report as rp
from scripts.itam_bench._serve import schema_context_record

_FIXTURES = Path(__file__).parent / "fixtures"
_HEX12 = re.compile(r"^[0-9a-f]{12}$")
_GUIDE = "서버 목록은 TCDMSIF80 을 쓴다 — 예: svr-db-03 은 운영 서버"
_META = {
    "query_guide": _GUIDE,
    "query_examples": [
        {"question": "부서 D001 서버", "sql": "SELECT * FROM TCDMSIF80 WHERE dept = 'D001'"},
        {"question": "svr-db-03 담당자", "sql": "SELECT 1 FROM TCDMSIF80"},
    ],
    "query_rules": ["활성화여부 = 'Y' 만 현행이다", "기준년월일은 YYYYMMDD 문자열"],
    "table_definitions": {"TCDMSIF80": {"manages": "서버 자산 현행 원장"}},
    "code_values": {"TCDMSIF80.useYn": ["Y", "N"]},
}
_SCHEMA = {
    "tables": {"TCDMSIF80": {"columns": [{"name": "sevrHostName"}]}},
    "_structure_meta": _META,
}
_TEMPLATE = {
    "outcome": "assembled",
    "template_id": "server_by_dept",
    "slot_names": ["dept_code"],
    "reason": None,
    "final_sql_from_template": True,
}


def _single(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "thread_id": "t-1",
        "active_db_id": "itam",
        "schema_info": _SCHEMA,
        "column_descriptions": {},
        "template_assembly": {"itam": dict(_TEMPLATE)},
    }
    state.update(overrides)
    return state


def _entry(state: dict[str, Any], db_id: str = "itam") -> dict[str, Any]:
    record = schema_context_record("task_pipeline_state", state)
    assert record is not None
    return record["dbs"][db_id]


# ── W1 자산 사용 표지 ───────────────────────────────────────────────────────


class TestAssetMarker:
    def test_single_path_marks_keys_fingerprints_counts(self) -> None:
        entry = _entry(_single())
        assert set(entry["assets"]) == set(serve.ASSET_MARKER_KEYS)
        assert {k: v["n"] for k, v in entry["assets"].items()} == {
            "query_guide": 1,
            "query_examples": 2,
            "query_rules": 2,
            "table_definitions": 1,
        }
        assert all(_HEX12.match(v["fp"]) for v in entry["assets"].values())
        assert entry["template"] == _TEMPLATE

    def test_no_values_copied(self) -> None:
        text = repr(schema_context_record("task_pipeline_state", _single()))
        leaks = ("svr-db-03", "D001", "운영 서버", "활성화여부", "현행 원장", "SELECT", "useYn")
        for leaked in leaks:
            assert leaked not in text

    def test_fingerprint_is_deterministic_and_content_bound(self) -> None:
        first = _entry(_single())["assets"]
        again = _entry(_single())["assets"]
        changed_meta = {**_META, "query_guide": _GUIDE + " ."}
        changed = _entry(_single(schema_info={**_SCHEMA, "_structure_meta": changed_meta}))
        assert first == again
        assert changed["assets"]["query_guide"]["fp"] != first["query_guide"]["fp"]
        assert changed["assets"]["query_rules"] == first["query_rules"]

    def test_absent_assets_and_template_leave_record_shape(self) -> None:
        bare = {"tables": _SCHEMA["tables"]}
        entry = _entry(_single(schema_info=bare, template_assembly=None))
        assert "assets" not in entry and "template" not in entry
        empty_meta = {**_SCHEMA, "_structure_meta": {"query_guide": "  ", "query_examples": []}}
        assert "assets" not in _entry(_single(schema_info=empty_meta, template_assembly={}))

    def test_multi_path_reads_db_schema_and_per_db_template(self) -> None:
        state = {
            "thread_id": "t-2",
            "is_multi_db": True,
            "db_schemas": {"itam": _SCHEMA, "polestar": {"tables": {}}},
            "template_assembly": {
                "itam": {"outcome": "fallback", "template_id": None, "slot_names": [],
                         "reason": "no_match", "final_sql_from_template": False},
            },
        }
        dbs = schema_context_record("task_pipeline_state", state)["dbs"]
        assert set(dbs["itam"]["assets"]) == set(serve.ASSET_MARKER_KEYS)
        assert dbs["itam"]["template"] == {
            "outcome": "fallback", "template_id": None, "slot_names": [], "reason": "no_match",
            "final_sql_from_template": False,
        }
        assert "assets" not in dbs["polestar"] and "template" not in dbs["polestar"]

    @pytest.mark.parametrize(
        "template_id",
        ["x" * 70, "10.0.0.1", "12345", "홍길동", "svr db 03", "a'b", ""],
    )
    def test_value_shaped_template_id_dropped(self, template_id: str) -> None:
        raw = {**_TEMPLATE, "template_id": template_id}
        entry = _entry(_single(template_assembly={"itam": raw}))
        assert entry["template"]["template_id"] is None
        assert template_id not in repr(entry) or template_id == ""

    def test_bad_slot_names_and_reason_dropped(self) -> None:
        raw = {
            **_TEMPLATE,
            "slot_names": ["dept_code", "Dept Code", "a'b", "x" * 40, 7, "center"],
            "reason": "입력이 너무 크다 — 합성부서",
        }
        entry = _entry(_single(template_assembly={"itam": raw}))
        assert entry["template"]["slot_names"] == ["dept_code", "center"]
        assert entry["template"]["reason"] is None
        free = _entry(_single(template_assembly={"itam": {**_TEMPLATE, "reason": "free_text"}}))
        assert free["template"]["reason"] is None

    def test_unknown_outcome_drops_whole_template(self) -> None:
        for outcome in ("done", None, "assembled; DROP"):
            state = _single(template_assembly={"itam": {**_TEMPLATE, "outcome": outcome}})
            assert "template" not in _entry(state)

    def test_final_sql_flag_is_bool_only(self) -> None:
        """fix3 작업 3 — 「적중 후 LLM 수정」 표지는 bool만 옮긴다(그 밖 값·없음은 None)."""
        revised = {**_TEMPLATE, "final_sql_from_template": False}
        assert _entry(_single(template_assembly={"itam": revised}))["template"][
            "final_sql_from_template"
        ] is False
        for bad in ("false", 0, None, ["x"]):
            raw = {**_TEMPLATE, "final_sql_from_template": bad}
            entry = _entry(_single(template_assembly={"itam": raw}))
            assert entry["template"]["final_sql_from_template"] is None
        legacy = {k: v for k, v in _TEMPLATE.items() if k != "final_sql_from_template"}
        assert _entry(_single(template_assembly={"itam": legacy}))["template"][
            "final_sql_from_template"
        ] is None

    def test_reason_enum_follows_domain(self) -> None:
        from src.domain import query_templates as qt

        state = _single(template_assembly={
            "itam": {**_TEMPLATE, "outcome": "fallback", "reason": qt.REASON_SLOT_REJECTED},
        })
        assert _entry(state)["template"]["reason"] == "slot_rejected"


class TestTurnAggregation:
    def test_turn_summary_merges_assets_and_templates(self) -> None:
        first = schema_context_record("task_pipeline_state", _single())
        other_meta = {**_META, "query_guide": "다른 가이드", "query_rules": ["하나"]}
        second = schema_context_record(
            "task_pipeline_state",
            _single(
                schema_info={**_SCHEMA, "_structure_meta": other_meta},
                template_assembly={"itam": {"outcome": "fallback", "template_id": None,
                                            "slot_names": [], "reason": "no_match",
                                            "final_sql_from_template": False}},
            ),
        )
        third = schema_context_record("task_pipeline_state", _single())
        # 같은 템플릿이라도 적중 후 LLM이 고친 턴은 따로 남는다(fix3 작업 3)
        fourth = schema_context_record("task_pipeline_state", _single(
            template_assembly={"itam": {**_TEMPLATE, "final_sql_from_template": False}},
        ))
        context = jd.schema_context(
            [first, second, third, fourth], db_id="itam", gold_tables=[], key_refs=[]
        )
        entry = context["dbs"]["itam"]
        assert entry["assets"]["query_guide"]["fp"] == jd.MIXED_FINGERPRINT
        assert entry["assets"]["query_rules"]["n"] == 2
        assert entry["assets"]["query_examples"] == first["dbs"]["itam"]["assets"]["query_examples"]
        assert entry["templates"] == [
            _TEMPLATE,
            {"outcome": "fallback", "template_id": None, "slot_names": [], "reason": "no_match",
             "final_sql_from_template": False},
            {**_TEMPLATE, "final_sql_from_template": False},
        ]

    def test_no_marker_keeps_turn_shape(self) -> None:
        record = schema_context_record(
            "task_pipeline_state", _single(schema_info={"tables": {}}, template_assembly=None)
        )
        entry = jd.prompt_by_db([record])["itam"]
        assert "assets" not in entry and "templates" not in entry


def test_leak_gate_passes_with_marker_fields(tmp_path: Path) -> None:
    policy = cat.load_policy(POLICY_PATH)
    records = [
        json.loads(line)
        for line in (_FIXTURES / "itam_bench_trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    context = jd.schema_context(
        [schema_context_record("task_pipeline_state", _single())],
        db_id="itam",
        gold_tables=["TCDMSIF80"],
        key_refs=[],
    )
    for record in records:
        record["schema_context"] = context
    staged = cli.build_artifacts(
        run_meta={"run_id": "20261007-120000", "env": "closed", "judged": True,
                  "asset_ablation": "query_rules"},
        catalog_doc={"source": "schema_cache", "assets": {}, "tables": {}},
        records=records,
    )
    gate = rd.LeakGate(
        policy=policy, vault=rd.PiiVault.from_policy(policy), user_values={"login_id": "5488923"}
    )
    ok, violations = rd.write_gated(tmp_path / "run", staged, gate)
    assert ok and violations == []
    trace = (tmp_path / "run" / "trace.jsonl").read_text(encoding="utf-8")
    assert '"template_id": "server_by_dept"' in trace and '"query_guide"' in trace
    for leaked in ("운영 서버", "현행 원장", "활성화여부"):
        assert leaked not in trace


# ── W8 자산 끄기(벤치 서버 프로세스) ─────────────────────────────────────────


class TestAblationInstall:
    def test_unknown_key_rejected(self) -> None:
        with pytest.raises(ValueError):
            serve.install_ablation("column_descriptions")

    def test_env_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(serve.ABLATION_ENV, raising=False)
        assert serve.install_ablation_from_env() is None
        monkeypatch.setenv(serve.ABLATION_ENV, "  ")
        assert serve.install_ablation_from_env() is None
        monkeypatch.setenv(serve.ABLATION_ENV, "nope")
        with pytest.raises(ValueError):
            serve.install_ablation_from_env()

    def test_query_templates_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(serve.TEMPLATE_SWITCH_ENV, raising=False)
        undo = serve.install_ablation("query_templates")
        assert serve.os.environ[serve.TEMPLATE_SWITCH_ENV] == "false"
        undo()
        assert serve.TEMPLATE_SWITCH_ENV not in serve.os.environ

    def test_prompt_template_section_off_for_itam_only(self, tmp_path: Path) -> None:
        from src.db_adapters.generated import GeneratedTemplateAdapter

        for db_id in ("itam", "other"):
            path = tmp_path / "config" / "knowledge" / db_id / "prompt_template.yaml"
            path.parent.mkdir(parents=True)
            path.write_text(f"db_id: {db_id}\nsection: 규칙 {db_id}\n", encoding="utf-8")
        adapter = GeneratedTemplateAdapter(root=tmp_path)
        assert adapter.section("itam") == "규칙 itam"
        undo = serve.install_ablation("prompt_template")
        try:
            assert adapter.section("itam") is None
            assert adapter.section("other") == "규칙 other"
        finally:
            undo()
        assert adapter.section("itam") == "규칙 itam"

    def test_structure_key_stripped_from_both_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import importlib

        from src.schema_cache.cache_manager import SchemaCacheManager

        schema_analyzer = importlib.import_module("src.nodes.schema_analyzer")

        profile = {"source": "manual", "query_guide": "가이드", "query_rules": ["r"]}
        monkeypatch.setattr(schema_analyzer, "_load_manual_profile", lambda db_id: dict(profile))

        async def applied(self: Any, db_id: str) -> dict[str, Any]:
            return {"query_guide": "승인 가이드", "query_examples": []}

        monkeypatch.setattr(SchemaCacheManager, "get_applied_structure_meta", applied)
        undo = serve.install_ablation("query_guide")
        try:
            assert schema_analyzer._load_manual_profile("itam") == {
                "source": "manual", "query_rules": ["r"],
            }
            assert schema_analyzer._load_manual_profile("polestar") == profile
            got = asyncio.run(SchemaCacheManager.get_applied_structure_meta(None, "itam"))
            assert got == {"query_examples": []}
            kept = asyncio.run(SchemaCacheManager.get_applied_structure_meta(None, "x"))
            assert kept["query_guide"] == "승인 가이드"
        finally:
            undo()
        assert schema_analyzer._load_manual_profile("itam") == profile


class TestAblationCli:
    def test_requires_run_and_known_key(self) -> None:
        assert cli.main(["--dry-run", "--asset-ablation", "query_guide"]) == 1
        assert cli.main(["--run", "--asset-ablation", "column_descriptions"]) == 1

    def test_run_passes_key_to_server_and_records_it(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.scenario.catalog as scenario_catalog
        import scripts.scenario.client as scenario_client
        import scripts.scenario.runner as scenario_runner
        import scripts.scenario.server as scenario_server

        seen: dict[str, Any] = {}

        class FakeHandle:
            def __init__(self, **kwargs: Any) -> None:
                seen["env"] = kwargs["env_overrides"]
                self.log_path = kwargs["log_path"]

            def start(self) -> None: ...
            def wait_healthy(self) -> tuple[bool, str]:
                return True, ""
            def stop(self) -> None: ...
            def port_released(self) -> None: ...

        class FakeClient:
            def __init__(self, *_a: Any, **_k: Any) -> None: ...
            def close(self) -> None: ...

        status = SimpleNamespace(
            valid=True, reasons=[], tier="intent_orchestration", degraded_reason=None,
            auth_enabled=False, server_timeouts=None,
        )
        monkeypatch.setattr(scenario_catalog, "load_profiles", lambda: {"tier2_intent": {}})
        monkeypatch.setattr(scenario_server, "ServerHandle", FakeHandle)
        monkeypatch.setattr(scenario_server, "pick_port", lambda _p: 1)
        monkeypatch.setattr(scenario_server, "verify_profile", lambda *a, **k: status)
        monkeypatch.setattr(scenario_client, "ScenarioClient", FakeClient)
        monkeypatch.setattr(scenario_runner, "resolve_admin_credentials", lambda *_a: (None, None))
        monkeypatch.setattr(scenario_runner, "jwt_lifetime_sec", lambda *a, **k: 3600.0)
        monkeypatch.setattr(cli, "run_scenarios", lambda *a, **k: [])
        monkeypatch.setattr(cli, "_git_provenance", lambda: {"sha": "abc", "dirty": False})
        monkeypatch.setattr(cli, "_itam_dsn", lambda: None)

        def fake_stage(**kwargs: Any) -> tuple[dict[str, str], Any]:
            seen["run_meta"] = kwargs["run_meta"]
            return {}, None

        monkeypatch.setattr(cli, "stage_gated", fake_stage)
        monkeypatch.setattr(cli.rd, "write_gated", lambda *a: (True, []))
        args = cli.build_parser().parse_args(["--run", "--asset-ablation", "query_rules"])
        args.scenarios = "s.yaml"
        cfg = SimpleNamespace(
            llm=SimpleNamespace(provider="mlx"), orchestrator=SimpleNamespace(provider="mlx"),
            db_backend="dbhub", dbhub=SimpleNamespace(server_url=""),
        )
        policy = cat.load_policy(POLICY_PATH)
        cli._run_with_server(args, cfg, policy, [], {"source": "x"}, None, tmp_path)  # type: ignore[arg-type]
        assert seen["env"][serve.ABLATION_ENV] == "query_rules"
        assert seen["run_meta"]["asset_ablation"] == "query_rules"

        args = cli.build_parser().parse_args(["--run"])
        args.scenarios = "s.yaml"
        cli._run_with_server(args, cfg, policy, [], {"source": "x"}, None, tmp_path)  # type: ignore[arg-type]
        assert seen["env"][serve.ABLATION_ENV] == ""
        assert seen["run_meta"]["asset_ablation"] is None


# ── W8 자산별 효과 표 · 유지 규칙 ────────────────────────────────────────────


def _turn(sid: str, verdict: str | None, labels: list[str], *, sql: bool = True) -> dict[str, Any]:
    return {
        "id": sid,
        "turn": 1,
        "repeat": 0,
        "status": "ok",
        "oracle": {"verdict": verdict} if verdict else None,
        "taxonomy": labels,
        "executed_sqls": [{"sql": "SELECT 1"}] if sql else [],
        "sql_analysis": {"sql_count": int(sql)},
        "expected": {},
    }


def _write_run(directory: Path, records: list[dict[str, Any]], **meta: Any) -> Path:
    directory.mkdir(parents=True)
    run = {"run_id": directory.name, "env": "closed", "tier": "intent_orchestration",
           "scenario_file": "s.yaml", "repeat": 1, "assets": {"profile": "fp"}, **meta}
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    (directory / "trace.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8"
    )
    return directory


_BASE = [
    _turn("s1", "pass", []),
    _turn("s2", "pass", []),
    _turn("s3", "pass", []),
    _turn("s4", "fail", ["wrong_join"]),
    _turn("s5", None, ["routing_miss"], sql=False),
]


class TestAblationVerdict:
    def test_keep_when_accuracy_holds_and_top_failure_drops(self) -> None:
        off = [
            _turn("s1", "pass", []),
            _turn("s2", "fail", ["wrong_join"]),
            _turn("s3", "fail", ["schema_miss"]),
            _turn("s4", "fail", ["wrong_join"]),
        ]
        result = rp.ablation_verdict(_BASE, off)
        assert result["on"] == (3, 4) and result["off"] == (1, 4)
        assert [t[0] for t in result["top"]] == ["wrong_join", "schema_miss"]
        assert result["verdict"] == rp.KEEP

    def test_withdraw_when_accuracy_drops(self) -> None:
        off = [_turn(f"s{i}", "pass", []) for i in range(1, 5)]
        off[0]["taxonomy"] = ["wrong_join"]
        result = rp.ablation_verdict(_BASE, off)
        assert result["verdict"] == rp.WITHDRAW

    def test_withdraw_when_no_top_failure_reduced(self) -> None:
        off = [dict(t) for t in _BASE]
        assert rp.ablation_verdict(_BASE, off)["verdict"] == rp.WITHDRAW

    def test_separate_labels_excluded_and_tie_order(self) -> None:
        counts = rp.failure_counts(
            [
                _turn("a", "fail", ["routing_miss", "zeta", "alpha"]),
                _turn("b", None, ["asked_back"]),
            ]
        )
        assert "routing_miss" not in counts and "asked_back" not in counts
        assert rp.top_failures(counts) == ["alpha", "zeta"]

    def test_undecided_without_sql_observed_oracle_turns(self) -> None:
        off = [_turn("s1", "pass", [], sql=False), _turn("s2", None, ["wrong_join"])]
        assert rp.ablation_verdict(_BASE, off)["verdict"] == rp.UNDECIDED

    def test_report_table_and_validation(self, tmp_path: Path) -> None:
        base = _write_run(tmp_path / "base", _BASE)
        off_records = [
            _turn("s1", "pass", []),
            _turn("s2", "fail", ["wrong_join"]),
            _turn("s3", "pass", []),
            _turn("s4", "fail", ["wrong_join"]),
        ]
        guide = _write_run(tmp_path / "abl-guide", off_records, asset_ablation="query_guide")
        rules = _write_run(tmp_path / "abl-rules", _BASE, asset_ablation="query_rules", env="x")
        text = rp.ablation_report(base, [guide, rules])
        rows = [line for line in text.splitlines() if line.startswith("| query_")]
        expected = (
            "| query_guide | abl-guide | 3/4 (75%) | 2/4 (50%) | +25%p | wrong_join 2→1 | 유지 |"
        )
        assert expected in rows
        assert rows[1].endswith("| 철회 후보 |")
        assert "abl-rules" in text.split("> 주의")[1]
        with pytest.raises(ValueError):
            rp.ablation_report(guide, [rules])
        with pytest.raises(ValueError):
            rp.ablation_report(base, [base])

    def test_compare_adds_asset_section_when_one_side_ablated(self, tmp_path: Path) -> None:
        base = _write_run(tmp_path / "base", _BASE)
        off = _write_run(tmp_path / "off", _BASE, asset_ablation="table_definitions")
        for a, b in ((base, off), (off, base)):
            text = rp.compare_runs(a, b)
            assert "## 자산별 효과" in text and "| table_definitions | off |" in text
            assert "끈 자산(run 단위" in text
        assert "## 자산별 효과" not in rp.compare_runs(base, base)

    def test_cli_ablation_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_run(tmp_path / "base", _BASE)
        _write_run(tmp_path / "off", _BASE, asset_ablation="query_examples")
        monkeypatch.setattr(cli, "RESULTS_ROOT", tmp_path)
        assert cli.main(["--ablation-report", "base", "off"]) == 0
        assert "| query_examples | off |" in capsys.readouterr().out
        assert cli.main(["--ablation-report", "base"]) == 1
        assert cli.main(["--ablation-report", "off", "base"]) == 1
