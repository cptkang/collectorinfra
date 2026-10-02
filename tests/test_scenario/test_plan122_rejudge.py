"""plans/122 J-4 — 오프라인 재판정기(`scripts.scenario.rejudge`).

핵심 계약은 **항등**이다: 러너가 적재한 행을 같은 카탈로그로 다시 판정하면 원 판정과 행 단위로
같아야 한다. 그래야 다른 카탈로그로 재판정했을 때 나는 차이를 계약 변화로 읽을 수 있다.
행은 러너 `_run_once`/`_run_replay` 를 가짜 클라이언트로 실제로 돌려 만든다(추론이 아니라 산출물).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import rejudge
from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn
from scripts.scenario.runner import RawLog, RunConfig

META = {"run_id": "20260929-000000", "env": "closed", "mode": "run", "commit": "deadbeef",
        "dirty": False}


class _FakeClient:
    def __init__(self, responses: list[Observation]) -> None:
        self.responses = list(responses)

    def send(self, endpoint: str, payload: dict, upload: Any = None) -> Observation:
        return self.responses.pop(0)

    def download(self, *_a: Any, **_kw: Any) -> None:
        return None


def _ok(**kw: Any) -> Observation:
    base: dict[str, Any] = {"http_status": 200, "status": "completed", "response": "정상 응답",
                            "row_count": 3, "sse_events": ["node_start", "done"],
                            "node_path": ["input_parser"], "processing_time_ms": 100.0}
    base.update(kw)
    return Observation(**base)


def _zone_question() -> Observation:
    return Observation(http_status=200, status="clarification", sse_events=["done"], clarification={
        "kind": "zone_select", "question": "조회할 존이 지정되지 않았습니다.",
        "options": [{"db_id": "polestar_b0", "label": "은행존"},
                    {"db_id": "polestar_cm_gp", "label": "공동존 김포"}]})


def _catalog(**overrides: dict[str, Any]) -> Catalog:
    expects = {
        "P-01": {"status": "completed", "row_count": {"min": 1}},
        "F-01": {"status": "completed", "row_count": {"min": 10}},
        "M-01": {"status": "completed", "manual_review": "눈으로 볼 것"},
        "L-01": {"status": "completed", "row_count": {"min": 1}},
        "C-01": {"status": "clarification", "clarification": {"kind": "zone_select"}},
        "E-01": {"http_status": 422},
    }
    expects.update(overrides)
    scenarios = [
        Scenario(id=sid, group="T", plans=[122], title="t",
                 env="sandbox" if sid == "L-01" else "both",
                 turns=[Turn({"query": f"질의 {sid}"}, expect)])
        for sid, expect in expects.items()
    ]
    scenarios.append(Scenario(id="K-01", group="T", plans=[122], title="t",
                              turns=[Turn({"query": "q"}, {"manual_review": "묶음 메모"})],
                              replay={"scenarios": ["P-01"], "repeat": 2}))
    return Catalog(groups={"T": Group(id="T", name="t", latency_target_ms=1000)},
                   scenarios=scenarios, profiles={"baseline": {}})


_RESPONSES: dict[str, list[Observation]] = {
    "P-01": [_ok()],
    "F-01": [_ok(row_count=2)],
    "M-01": [_ok()],
    "L-01": [_ok()],
    "C-01": [_zone_question()],
    "E-01": [Observation(http_status=422, status="error", response='{"detail":"x"}',
                         error='http 422: {"detail":"x"}')],
}


def _make_run(tmp_path: Path, catalog: Catalog) -> Path:
    run_dir = tmp_path / META["run_id"]
    run_dir.mkdir(parents=True)
    raw = RawLog(run_dir / "raw.jsonl")
    config = RunConfig(mode="run")
    for sid, responses in _RESPONSES.items():
        scenario = catalog.by_id(sid)
        assert scenario is not None
        runner_mod._run_once(catalog, config, dict(META), "baseline", scenario, 0,
                             _FakeClient(list(responses)), raw, run_dir, [],
                             preference=["polestar_cm_gp"])
    bundle = catalog.by_id("K-01")
    assert bundle is not None
    runner_mod._run_replay(catalog, config, dict(META), "baseline", bundle,
                           _FakeClient([_ok(), _ok(row_count=0)]), raw, run_dir, [],
                           preference=["polestar_cm_gp"])
    (run_dir / "run.json").write_text(json.dumps({
        "meta": META, "profiles": [{"name": "baseline", "tier": "intent_orchestration"}],
        "skipped": []}, ensure_ascii=False), encoding="utf-8")
    return run_dir


def test_j4_identity_with_same_catalog(tmp_path: Path) -> None:
    catalog = _catalog()
    run_dir = _make_run(tmp_path, catalog)
    result = rejudge.rejudge_run(run_dir, catalog, identity=True)
    verdicts = {(d["scenario_id"], d["turn"], d["repeat"]): d["before"]["func"]
                for d in result["diffs"]}
    # 표본이 판정 어휘를 고루 덮는지 먼저 본다(항등이 공허하지 않게).
    assert set(verdicts.values()) >= {"pass", "fail", "manual"}
    assert verdicts[("E-01", 1, 0)] == "pass"               # 기대한 422
    assert verdicts[("K-01", 1, 0)] == "pass"               # 묶음은 참조 시나리오(P-01) 단언으로
    assert verdicts[("K-01", 1, 1)] == "fail"               # 2회차 0행 → 참조 단언으로 불합격
    mismatched = [d for d in result["diffs"] if d["detail_changed"]]
    assert not mismatched, mismatched
    summary = rejudge.summarize(result)
    assert summary["detail_equal"] == summary["rejudged"] == len(result["rows"])


def test_j4_env_mismatch_still_held(tmp_path: Path) -> None:
    catalog = _catalog()
    run_dir = _make_run(tmp_path, catalog)
    result = rejudge.rejudge_run(run_dir, catalog)
    row = next(r for r in result["new_rows"] if r["scenario_id"] == "L-01")
    assert row["func_verdict"] == "manual"
    assert row["manual_sources"] == ["env_mismatch"]
    assert row["env_mismatch"] == {"scenario_env": "sandbox", "run_env": "closed"}


def test_j4_catalog_change_is_reported(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, _catalog())
    changed = _catalog(**{"F-01": {"status": "completed", "row_count": {"min": 1}},
                          "M-01": {"status": "completed"}})
    result = rejudge.rejudge_run(run_dir, changed)
    diffs = {d["scenario_id"]: d for d in result["diffs"] if d["detail_changed"]}
    assert set(diffs) == {"F-01", "M-01"}
    assert (diffs["F-01"]["before"]["func"], diffs["F-01"]["after"]["func"]) == ("fail", "pass")
    assert diffs["F-01"]["causes"] == ["catalog_change"]     # 원 기대값(min 10)이 없다
    assert (diffs["M-01"]["before"]["func"], diffs["M-01"]["after"]["func"]) == ("manual", "pass")
    assert diffs["M-01"]["causes"] == ["catalog_change"]     # 카탈로그 수동 문구가 사라졌다


def test_j4_dirty_identity_catalog_diff_is_uncommitted(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, _catalog())
    changed = _catalog(**{"M-01": {"status": "completed"}})
    rows = rejudge.load_rows(run_dir)
    row = next(r for r in rows if r["scenario_id"] == "M-01")
    _new, diff = rejudge.rejudge_row(row, changed, {**META, "dirty": True}, run_dir, identity=True)
    assert diff["causes"] == ["uncommitted_catalog"]


def test_j4_missing_scenario_kept_with_reason(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path, _catalog())
    small = Catalog(groups=_catalog().groups,
                    scenarios=[s for s in _catalog().scenarios if s.id != "P-01"],
                    profiles={"baseline": {}})
    result = rejudge.rejudge_run(run_dir, small)
    missing = [d for d in result["diffs"] if d["status"] == "catalog_missing"]
    # P-01 과 그것을 참조하는 K-01 두 회차.
    assert {d["scenario_id"] for d in missing} == {"P-01", "K-01"}


# --- 관측치 복원 규칙 ----------------------------------------------------------------------

@pytest.mark.parametrize(("row", "expected"), [
    ({"http_status": 418}, (418, False)),
    ({"mode_evidence": "status=error http=422"}, (422, False)),
    ({"mode_evidence": "http_status=502"}, (502, False)),
    ({"error": "http 401 - 토큰 없음: x"}, (401, False)),
    ({"failed_assertions": [{"key": "http_status", "expected": 200, "actual": 400}]}, (400, False)),
    ({"error": "ReadTimeout: timed out", "sse_events": []}, (0, True)),
    ({"error": "처리 시간이 초과되었습니다", "sse_events": ["error"]}, (200, True)),
    ({}, (200, True)),
])
def test_j4_http_status_restore_rules(row: dict[str, Any], expected: tuple[int, bool]) -> None:
    assert rejudge._restore_http_status(row) == expected


@pytest.mark.parametrize(("row", "expected"), [
    ({"status": "partial"}, ("partial", False)),
    ({"failed_assertions": [{"key": "status", "expected": "completed",
                             "actual": "clarification"}]}, ("clarification", False)),
    ({"mode_evidence": "status=error http=400"}, ("error", False)),
    ({"response_mode": "clarify"}, ("clarification", True)),
    ({"error": "boom"}, ("error", True)),
    ({}, ("completed", True)),
])
def test_j4_status_restore_rules(row: dict[str, Any], expected: tuple[str, bool]) -> None:
    assert rejudge._restore_status(row) == expected


def test_j4_artifacts_relocated_and_sql_bodies(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "H-01-0-1-result.xlsx").write_bytes(b"x")
    row = {"artifacts": ["C:\\run\\artifacts\\H-01-0-1-result.xlsx"],
           "executed_sqls": [{"sql": "SELECT 1", "source": "polestar_cm_gp", "row_count": 1},
                             {"sql": "", "source": "polestar_b0"}],
           "clarification_options": {"kind": "zone_select", "options": ["은행존"],
                                     "question": "q"}}
    obs, estimated = rejudge.restore_observation(row, tmp_path)
    assert obs.artifacts == [str(tmp_path / "artifacts" / "H-01-0-1-result.xlsx")]
    assert obs.has_file is True and obs.file_name == "H-01-0-1-result.xlsx"
    assert obs.executed_sqls == ["SELECT 1"]
    assert obs.db_ids == ["polestar_b0", "polestar_cm_gp"] and obs.db_ids_source == "executed"
    assert obs.clarification == {"kind": "zone_select", "question": "q",
                                 "options": [{"label": "은행존"}]}
    assert "clarification" in estimated and "artifacts" not in estimated


# --- 카탈로그 선택 · CLI -------------------------------------------------------------------

def test_j4_git_ref_catalog_without_checkout(tmp_path: Path) -> None:
    catalog, sha = rejudge.load_catalog_at_ref("HEAD", tmp_path)
    assert len(sha) == 40 and catalog.scenarios
    assert (tmp_path / "testdata" / "scenarios").is_dir()


def test_j4_unknown_ref_rejected(tmp_path: Path) -> None:
    with pytest.raises(rejudge.RejudgeError):
        rejudge.load_catalog_at_ref("0000000000000000000000000000000000000000", tmp_path)


def test_j4_identity_requires_substitute_ref_when_commit_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = _make_run(tmp_path, _catalog())
    assert rejudge.main([str(run_dir), "--identity"]) == 2
    assert "로컬 저장소에 없다" in capsys.readouterr().err


def test_j4_cli_writes_outputs_without_touching_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog = _catalog()
    run_dir = _make_run(tmp_path, catalog)
    before = {p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()}
    monkeypatch.setattr(rejudge, "load_catalog", lambda *_a, **_kw: catalog)
    out = tmp_path / "out"
    assert rejudge.main([str(run_dir), "--out", str(out)]) == 0
    assert {p.name: p.read_bytes() for p in run_dir.iterdir() if p.is_file()} == before
    for name in ("raw.jsonl", "run.json", "rejudge_diff.jsonl", "rejudge.md", "report.md",
                 "summary.json"):
        assert (out / name).exists(), name
    meta = json.loads((out / "run.json").read_text(encoding="utf-8"))["meta"]
    assert meta["judgement_contract"]["catalog_digest"] == rejudge.judgement_digest(catalog)
    assert meta["rejudge"]["original"]["run_id"] == META["run_id"]
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["meta"]["judgement_contract"]["catalog_digest"] == meta["catalog_digest"]
