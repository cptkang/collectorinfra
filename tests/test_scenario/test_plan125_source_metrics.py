"""plans/125 M-1·M-3·M-4 — 4소스 골드 초안 · 지표 정의 · 계획 요약 칸(LLM·서버 0)."""

from __future__ import annotations

import importlib

import pytest

from scripts.scenario import source_metrics as sm
from scripts.scenario.catalog import known_source_ids, load_catalog

query_routes = importlib.import_module("src.api.routes.query")


def _row(sid: str, db_ids=(), statuses=(), link=None, text="") -> dict:
    task = {"id": "t1", "agent": "apm_query"}
    if statuses:
        task["source_status"] = list(statuses)
    if link:
        task["link"] = link
    return {"scenario_id": sid, "db_ids": list(db_ids), "response_text": text,
            "plan_summary": {"tasks": [task]}}


# ── M-3 지표 ──────────────────────────────────────────────────────────────────

def test_observed_sources_count_only_successful_nonsql_queries() -> None:
    row = _row("FS-06", db_ids=["polestar_cm_gp"], statuses=["apm:ok"])
    assert sm.observed_sources(row) == {"polestar_cm_gp", "apm"}
    assert sm.observed_sources(_row("FS-23", statuses=["apm:not_queried"])) == set()
    assert sm.observed_sources(_row("FS-01", statuses=["apm:unavailable"])) == set()


def test_source_selection_recall_and_unnecessary_rate() -> None:
    rows = [_row("FS-06", ["polestar_cm_gp"], ["apm:ok"]),          # 필수 2/2
            _row("FS-01", ["polestar_cm_gp"], ["apm:partial"])]      # 필수 1/1 · 불필요 1/2
    got = sm.source_selection(rows, {"FS-06": ["polestar_cm_gp", "apm"], "FS-01": ["apm"]})
    assert got == {"turns": 2, "required_recall": 1.0, "unnecessary_rate": 0.25}
    assert sm.abstention_accuracy([_row("FS-21"), _row("FS-22", ["polestar_cm_gp"])],
                                  ["FS-21", "FS-22"]) == 0.5


def test_link_and_answer_scores() -> None:
    truth = {"svr-1": "linked", "svr-2": "unlinked", "web01": "linked"}
    assert sm.link_precision_recall({"svr-1": "linked", "svr-2": "unlinked", "web01": "linked"},
                                    truth) == {"precision": 1.0, "recall": 1.0}
    assert sm.link_precision_recall({"svr-1": "linked", "svr-2": "linked"}, truth) == {
        "precision": 0.5, "recall": 0.5}
    flagged = _row("FS-09", link={"apm_instance": {"linked:high": 1, "unlinked": 1}},
                   text="WAS 연결 1/2(high 1) · 미연결 1")
    silent = _row("FS-10", link={"server_name": {"ambiguous": 1}}, text="결과입니다")
    assert sm.unlinked_disclosure_rate([flagged, silent]) == 0.5
    assert sm.final_answer_score(["complete", "accept", "missing", "wrong"]) == 0.125
    with pytest.raises(ValueError):
        sm.final_answer_score(["great"])


# ── M-1 골드 초안 ─────────────────────────────────────────────────────────────

def test_four_source_gold_draft_shape() -> None:
    catalog = load_catalog()
    fs = [s for s in catalog.scenarios if s.group == "FS"]
    assert len(fs) == 36, ("125 초안 24건 + 132 소스 선별 9건(FS-25~FS-33 · plans/132 W0)"
                           " + 130 대상 이름 해석 3건(FS-34~FS-36)")
    assert all(s.requires_sources for s in fs), "전부 판정 전제 소스를 선언한다(D-276 ②)"
    assert set().union(*(s.requires_sources for s in fs)) <= known_source_ids()
    assert sum(1 for s in fs if "apm" in s.requires_sources) >= 12
    three_hop = [s for s in fs if s.turns[0].expect.get("plan", {}).get("min_tasks") == 3]
    assert len(three_hop) >= 4
    assert not any(t.expect.get("manual_review") for s in fs for t in s.turns), "기계 단언만"
    assert catalog.groups["FS"].policy_confirmed is False


# ── M-4 계획 요약 칸 ──────────────────────────────────────────────────────────

def test_plan_summary_nonsql_fields_only_when_present() -> None:
    assert query_routes._nonsql_task_summary({"agent": "data_query"}, {}) == {}
    res = {"apm_query": {"inserted_steps": [{"edge": "E2"}, {"view": "apm.instances"}],
                         "link_summary": {"server_name": {"linked:one": 2}}},
           "source_status": [{"system": "apm", "status": "partial"}]}
    assert query_routes._nonsql_task_summary(
        {"agent": "apm_query", "views": ["apm.app_health"]}, res) == {
        "views": ["apm.app_health"], "inserted_steps": ["E2", "apm.instances"],
        "link": {"server_name": {"linked:one": 2}}, "source_status": ["apm:partial"]}
