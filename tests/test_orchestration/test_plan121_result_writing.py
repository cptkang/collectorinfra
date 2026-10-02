"""plans/121 묶음 B — 결과 작성 경로 결함 교정(D-162 예외 · D-273 ①).

  TP-11.3  병합 표 "출처"는 모든 원천을 담고, 원천 차이로 「서버당 다건」을 세지 않는다.
  TP-11.5  2단·1단 0건 응답에도 퍼널 사유(`empty_diagnosis`)가 실린다 — 진단이 없으면 종전 문구.
  TP-11.6  병합 성공 경로가 병합 밖 task의 오류·0건 문구, 존 실패 각주, 산출 파일을 잃지 않는다.
  TP-4.1   병합 판정이 task 마감보다 먼저다 — 병합이 성립하면 요약 LLM은 병합 표 1회뿐이다.
  TP-11.10 테이블 선택 프롬프트는 스키마 사전의 순서와 무관하게 같은 바이트다.

LLM·DB 0.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from src.dbhub.models import ColumnInfo, SchemaInfo, TableInfo
from src.nodes.output_generator import _db_display_name, _generate_empty_result_response
from src.orchestration.result_aggregator import (
    _build_output_state,
    _finalize_task,
    _merge_task_results_by_identity,
    result_aggregator,
)
from src.orchestration.subagents import _pack_pipeline_result
from src.state import create_initial_state

agg_mod = sys.modules["src.orchestration.result_aggregator"]

_DIAG = {
    "stages": [
        {"label": "전체 서버", "counts": {"": 10}, "source": "probe"},
        {"label": "CPU 90% 이상", "counts": {"": 0}, "source": "probe"},
    ],
    "unexpressed": [], "notes": [], "regenerable": False,
}


# ── TP-11.3 ─────────────────────────────────────────────────────────────────


def _two_sources(rows_t1, rows_t2):
    tasks = [{"task_id": "t1", "order": 1}, {"task_id": "t2", "order": 2}]
    results = {"t1": {"organized_data": {"rows": rows_t1}},
               "t2": {"organized_data": {"rows": rows_t2}}}
    return tasks, results


def test_merge_source_lists_every_origin(caplog):
    tasks, results = _two_sources(
        [{"server_name": "A", "_source_db": "polestar_cm_gp", "sev": 3}],
        [{"server_name": "A", "_source_db": "polestar_cm_yd", "cpu": 40}],
    )
    with caplog.at_level("INFO"):
        merged = _merge_task_results_by_identity(tasks, results)
    assert merged == [{"server_name": "A", "_source_db": "polestar_cm_gp, polestar_cm_yd",
                       "sev": 3, "cpu": 40}]
    assert "서버당 다건" not in caplog.text


def test_single_source_merge_unchanged():
    tasks, results = _two_sources(
        [{"server_name": "A", "_source_db": "polestar_cm_gp", "sev": 3}],
        [{"server_name": "A", "_source_db": "polestar_cm_gp", "cpu": 40}],
    )
    merged = _merge_task_results_by_identity(tasks, results)
    assert merged == [{"server_name": "A", "_source_db": "polestar_cm_gp", "sev": 3, "cpu": 40}]


def test_display_name_renders_each_joined_origin():
    one = _db_display_name("polestar_cm_gp")
    joined = _db_display_name("polestar_cm_gp, polestar_cm_yd")
    assert joined == f"{one}, {_db_display_name('polestar_cm_yd')}"
    assert _db_display_name("unregistered_db") == "unregistered_db"


# ── TP-11.5 ─────────────────────────────────────────────────────────────────


def test_pack_pipeline_result_carries_empty_diagnosis():
    s = {"organized_data": {"rows": []}, "query_results": [], "empty_diagnosis": _DIAG}
    res = _pack_pipeline_result(s, [{"db_id": "polestar_cm_gp"}], None, ownership_notes=[],
                                db_origin="classified", db_succeeded=False, db_pinned=False)
    assert res["empty_diagnosis"] == _DIAG
    no_diag = _pack_pipeline_result({"organized_data": {"rows": []}}, [], None,
                                    ownership_notes=[], db_origin="classified",
                                    db_succeeded=False, db_pinned=False)
    assert "empty_diagnosis" not in no_diag


@pytest.mark.asyncio
async def test_two_tier_zero_rows_answer_has_funnel_reason(mock_config):
    """★ 2단·1단 0건 응답 — 3단 단일 경로와 같은 사유 블록(실물 `_finalize_task` 경유)."""
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "CPU 90% 이상 서버", "order": 1}
    state = create_initial_state("CPU 90% 이상 서버")
    state["parsed_requirements"] = {"query_targets": ["CPU"], "filter_conditions": ["cpu>=90"]}
    res = {"organized_data": {"rows": [], "summary": ""}, "query_results": [],
           "empty_diagnosis": _DIAG}
    out = await _finalize_task(task, res, state, AsyncMock(), mock_config)
    expected = _generate_empty_result_response(state["parsed_requirements"], _DIAG)
    assert out["text"] == expected and "여기서 끊겼습니다" in out["text"]
    # 진단이 없으면 종전 문구 바이트 그대로
    res.pop("empty_diagnosis")
    out = await _finalize_task(task, res, state, AsyncMock(), mock_config)
    assert out["text"] == _generate_empty_result_response(state["parsed_requirements"], None)


def test_build_output_state_passes_diagnosis():
    state = create_initial_state("q")
    out = _build_output_state(state, {"sub_query": "q"}, {"empty_diagnosis": _DIAG})
    assert out["empty_diagnosis"] == _DIAG
    assert _build_output_state(state, {"sub_query": "q"}, {})["empty_diagnosis"] is None


# ── TP-4.1 · TP-11.6 ────────────────────────────────────────────────────────


def _merge_state():
    tasks = [
        {"task_id": "t1", "agent": "alarm_query", "sub_query": "알람", "order": 1,
         "status": "completed"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "CPU", "order": 2,
         "status": "completed"},
        {"task_id": "t3", "agent": "data_query", "sub_query": "메모리", "order": 3,
         "status": "failed"},
    ]
    state = create_initial_state("심각 알람 서버의 CPU와 메모리")
    state["task_plan"] = tasks
    state["task_results"] = {
        "t1": {"organized_data": {"rows": [{"server_name": "SV1", "_source_db": "polestar_cm_gp",
                                           "severity": 3}]},
               "db_errors": {"polestar_cm_yd": "timeout"},
               "db_result_summary": {
                   "polestar_cm_gp": {"row_count": 1, "display_name": "김포"},
                   "polestar_cm_yd": {"row_count": 0, "display_name": "여의도"}}},
        "t2": {"organized_data": {"rows": [{"server_name": "SV1", "_source_db": "polestar_cm_gp",
                                           "cpu": 42}]}},
        "t3": {"error": "SQL 검증 실패"},
    }
    return state


@pytest.mark.asyncio
async def test_merge_decided_before_finalize_one_summary_call(mock_config):
    """★ 병합이 성립하면 output_generator는 병합 표 1회만(행 있는 task 요약 LLM 0)."""
    calls = []

    async def fake_og(s, **kw):
        calls.append(s)
        return {"final_response": "표+요약", "output_file": b"xlsx",
                "output_file_name": "merged.xlsx"}

    with patch.object(agg_mod, "output_generator", new=fake_og):
        out = await result_aggregator(_merge_state(), llm=AsyncMock(), app_config=mock_config,
                                      synthesize=True)
    assert len(calls) == 1
    merged_state = calls[0]
    # 존 실패 각주 재료(②) · 병합 표 기준 존별 건수
    assert merged_state["db_errors"] == {"polestar_cm_yd": "timeout"}
    assert merged_state["db_result_summary"]["polestar_cm_gp"]["row_count"] == 1
    assert merged_state["db_result_summary"]["polestar_cm_yd"]["row_count"] == 0
    # 병합 밖 실패 task 문구(①) · 파일 보존(③)
    assert out["final_response"].startswith("표+요약")
    assert "작업 처리 중 오류가 발생했습니다: SQL 검증 실패" in out["final_response"]
    assert out["output_file"] == b"xlsx" and out["output_file_name"] == "merged.xlsx"


@pytest.mark.asyncio
async def test_all_tasks_with_rows_merge_response_unchanged(mock_config):
    state = _merge_state()
    state["task_plan"] = state["task_plan"][:2]
    state["task_results"].pop("t3")
    for res in state["task_results"].values():
        res.pop("db_errors", None)
        res.pop("db_result_summary", None)

    async def fake_og(s, **kw):
        return {"final_response": "표+요약", "output_file": None, "output_file_name": None}

    with patch.object(agg_mod, "output_generator", new=fake_og):
        out = await result_aggregator(state, llm=AsyncMock(), app_config=mock_config,
                                      synthesize=True)
    assert out["final_response"] == "표+요약"
    assert "output_file" not in out


# ── TP-11.10 ────────────────────────────────────────────────────────────────


def _schema(order: list[str]) -> SchemaInfo:
    schema = SchemaInfo()
    for name in order:
        schema.tables[name] = TableInfo(name=name, columns=[
            ColumnInfo(name="id", data_type="int"), ColumnInfo(name="val", data_type="text"),
        ])
    rels = [{"from": "t_a.id", "to": "t_b.id"}, {"from": "t_b.id", "to": "t_c.id"}]
    schema.relationships = rels if order[0] == "t_a" else list(reversed(rels))
    return schema


@pytest.mark.asyncio
async def test_table_selection_prompt_is_order_independent():
    from src.nodes.schema_analyzer import _llm_select_relevant_tables

    prompts: list[str] = []

    class _LLM:
        async def ainvoke(self, messages):
            prompts.append(messages[0].content)
            return type("R", (), {"content": "t_b, t_a"})()

    a = await _llm_select_relevant_tables(_LLM(), _schema(["t_a", "t_b", "t_c"]), ["서버"], "질의")
    b = await _llm_select_relevant_tables(_LLM(), _schema(["t_c", "t_a", "t_b"]), ["서버"], "질의")
    assert prompts[0] == prompts[1]
    assert a == b == ["t_a", "t_b"]
