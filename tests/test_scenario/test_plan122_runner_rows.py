"""러너 행 칸 · 판정 계약 (plans/122 J-1 ⑤ · J-3 · H-1 · H-2 · H-5) — 관측 수집 담당.

고정하는 계약:
  1. **기존 칸은 이름·값 그대로다.** 모의 서버로 기존 카탈로그 시나리오(F-01)와 합성 canned
     시나리오를 돈 행에서 새 칸을 빼면 변경 전 러너가 낸 행과 같다
     (`test_plan122_runner_baseline.json` — 변경 전 runner.py·client.py 로 뜬 행 · 벽시계 지연
     칸만 뺐다).
  2. 새 칸 여섯(`manual_sources`·`manual_source`·`anchor_at`·`dependency_notes`·`result_check`·
     `oracle_check`)이 모든 행에 있다. `result_check` 에는 행 원문이 없다(G-4).
  3. `run.json` `meta.judgement_contract` = (리포트 정의 버전, 카탈로그 지문).
전부 무과금이다(LLM·DB 0 · 모의 서버는 127.0.0.1 자식 프로세스).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.scenario import runner as runner_mod
from scripts.scenario.assertions import Observation, Verdict
from scripts.scenario.catalog import Catalog, Group, Scenario, Turn, judgement_digest, load_catalog
from scripts.scenario.runner import RunConfig, execute, run_meta

BASELINE = Path(__file__).with_name("test_plan122_runner_baseline.json")

#: 이번에 더한 칸. 이것 말고는 행이 바뀌지 않는다.
NEW_KEYS = frozenset({
    "manual_sources", "manual_source", "anchor_at", "dependency_notes",
    "result_check", "oracle_check",
})
#: plans/123 이 더한 칸(V-1 · V-2 · V-4) - 기존 칸은 이름·값 그대로다.
NEW_KEYS_123 = frozenset({
    "disclosures", "invariant_violations", "zone_selection", "pre_answer_mode",
})
#: 벽시계 지연 - 실행마다 다르다(기준선에서 뺐다). 서버 타임스탬프로 계산하는 노드 지연은
#: 결정적이라 남긴다.
VOLATILE_KEYS = frozenset({"wall_ms", "ttfb_ms", "ttft_ms", "max_event_gap_ms"})

SYNTHETIC = [
    Scenario(id="T-01", group="T", plans=[122], title="합성 stream", env="closed",
             turns=[Turn({"query": "h-run 기준선 합성 질의 하나"},
                         {"status": "completed", "sql_must_match": ["(?i)limit"],
                          "manual_review": "합성 수동 문구"})]),
    Scenario(id="T-02", group="T", plans=[122], title="합성 file_stream", env="closed",
             endpoint="file_stream", upload="testdata/templates/server_list_template.xlsx",
             turns=[Turn({"query": "h-run 기준선 합성 질의 둘"},
                         {"status": "completed", "has_file": False})]),
    Scenario(id="T-03", group="T", plans=[122], title="합성 plain", env="closed", endpoint="plain",
             turns=[Turn({"query": "h-run 기준선 합성 질의 셋"}, {"http_status": 200})]),
]


def _rows(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in
            (run_dir / "raw.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def mock_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """기준선을 뜬 것과 같은 조합 - 실 카탈로그 F-01(모의 블록 HITL 2턴) + 합성 canned 3건."""
    out_root = tmp_path_factory.mktemp("results")
    original = runner_mod.RESULTS_ROOT
    runner_mod.RESULTS_ROOT = out_root
    try:
        catalog = load_catalog()
        catalog.groups["T"] = Group(id="T", name="t", latency_target_ms=30000)
        catalog.scenarios += SYNTHETIC
        summary = execute(catalog, RunConfig(
            mode="mock", env="closed", only=["F-01", "T-01", "T-02", "T-03"], run_id="baseline"))
        run_dir = Path(summary["out_dir"])
        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        return {"rows": _rows(run_dir), "run": run, "catalog": catalog}
    finally:
        runner_mod.RESULTS_ROOT = original


def test_기존_칸은_새_칸을_빼면_변경_전_행과_같다(mock_run: dict[str, Any]) -> None:
    expected = json.loads(BASELINE.read_text(encoding="utf-8"))
    rows = mock_run["rows"]
    assert len(rows) == len(expected) == 5
    for actual, before in zip(rows, expected):
        added = set(actual) - set(before) - VOLATILE_KEYS
        assert added - NEW_KEYS_123 == NEW_KEYS, "122 새 칸은 정해진 여섯뿐이다"
        assert added - NEW_KEYS <= NEW_KEYS_123, "123 새 칸은 정해진 넷뿐이다"
        assert not set(before) - set(actual), "기존 칸이 사라지면 안 된다"
        kept = {k: v for k, v in actual.items()
                if k not in NEW_KEYS | NEW_KEYS_123 | VOLATILE_KEYS}
        where = f"{actual['scenario_id']} 턴 {actual['turn']}"
        assert kept == before, f"{where} 의 기존 칸 값이 바뀌었다"


def test_모의_run_행의_새_칸_값(mock_run: dict[str, Any]) -> None:
    by_key = {(row["scenario_id"], row["turn"]): row for row in mock_run["rows"]}
    t01 = by_key[("T-01", 1)]
    # 모의 canned 보류(unobservable)가 먼저 쌓이고 카탈로그 문구(catalog)가 뒤따른다.
    assert t01["manual_sources"] == ["unobservable", "catalog"]
    assert t01["manual_source"] == "catalog", "대표값은 MANUAL_SOURCES 순서로 고른다"
    f01 = by_key[("F-01", 2)]
    assert f01["manual_sources"] == [] and f01["manual_source"] is None
    for row in mock_run["rows"]:
        assert row["anchor_at"] and row["anchor_at"].endswith("+09:00"), "앵커는 KST"
        assert row["dependency_notes"] == []
        assert row["result_check"] is None, "결과 행을 요구하지 않는 턴은 받지 않는다"
        assert row["oracle_check"] is None


def test_run_json_에_판정_계약이_실린다(mock_run: dict[str, Any]) -> None:
    contract = mock_run["run"]["meta"]["judgement_contract"]
    assert contract["catalog_digest"] == judgement_digest(mock_run["catalog"])
    assert "report_version" in contract


def test_판정_계약의_리포트_버전은_리포트_상수를_읽고_없으면_None(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.scenario import report

    catalog = Catalog(profiles={"baseline": {}})
    monkeypatch.delattr(report, "JUDGEMENT_REPORT_VERSION", raising=False)
    from scripts.scenario.assertions import judge_digest

    assert run_meta(RunConfig(mode="mock"), catalog)["judgement_contract"] == {
        "report_version": None, "catalog_digest": judgement_digest(catalog),
        "judge_digest": judge_digest()}
    monkeypatch.setattr(report, "JUDGEMENT_REPORT_VERSION", 3, raising=False)
    assert run_meta(RunConfig(mode="mock"), catalog)["judgement_contract"]["report_version"] == 3


# --- `_row` 새 칸 단위 -----------------------------------------------------------

META = {"run_id": "r", "env": "closed", "mode": "run"}
SCENARIO = Scenario(id="T-09", group="T", plans=[122], title="t",
                    turns=[Turn({"query": "q"}, {"status": "completed"})])


def test_행은_보류_출처와_대표값을_싣는다() -> None:
    verdict = Verdict(func="manual", manual_notes=["a", "b"],
                      manual_sources=["unobservable", "policy"])
    row = runner_mod._row(META, "baseline", SCENARIO, 1, 0, Observation(), verdict)
    assert row["manual_sources"] == ["unobservable", "policy"]
    assert row["manual_source"] == "policy", "대표값은 목록 순서가 아니라 어휘 순서다"


def test_행은_앵커와_의존_노트를_그대로_싣는다() -> None:
    notes = [{"kind": "gate", "task": "t2"}, {"kind": "bridge", "count": 3}]
    obs = Observation(anchor_at="2026-09-29T09:00:00+09:00", dependency_notes=notes)
    row = runner_mod._row(META, "baseline", SCENARIO, 1, 0, obs, Verdict())
    assert row["anchor_at"] == "2026-09-29T09:00:00+09:00"
    assert row["dependency_notes"] == notes


def test_결과_요약은_행_원문을_싣지_않는다() -> None:
    obs = Observation(result={
        "status": "ok", "columns": ["hostname", "cpu"],
        "rows": [{"hostname": "비밀서버-01", "cpu": "93.1"}],
        "total_rows": 7000, "truncated": True, "reason": None,
    })
    row = runner_mod._row(META, "baseline", SCENARIO, 1, 0, obs, Verdict())
    assert row["result_check"] == {"status": "ok", "total_rows": 7000, "column_count": 2,
                                   "truncated": True, "reason": None}
    assert "비밀서버-01" not in json.dumps(row, ensure_ascii=False), "G-4 - 행 원문 금지"


def test_결과를_받지_못한_사유가_요약에_남는다() -> None:
    obs = Observation(result={"status": "unavailable", "columns": [], "rows": [],
                              "total_rows": 0, "truncated": False, "reason": "http 500"})
    row = runner_mod._row(META, "baseline", SCENARIO, 1, 0, obs, Verdict())
    assert row["result_check"]["status"] == "unavailable"
    assert row["result_check"]["reason"] == "http 500"


def test_무효로_돌린_판정은_보류_출처도_비운다() -> None:
    verdict = Verdict(func="manual", manual_notes=["x"], manual_sources=["catalog"])
    runner_mod._invalidate(verdict, "상태 오염")
    assert verdict.manual_sources == [] and verdict.manual_notes == []
