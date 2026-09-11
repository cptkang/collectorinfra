"""복합 질의 단계 이벤트 헬퍼 — plans/89 T9 · D-204 (`SPEC-composite-task-progress.md`).

실 LLM 0. `adispatch_custom_event`를 가로채 페이로드를 단언하고, 1단·2단 호출부가 같은
헬퍼를 쓰는지 소스로 고정한다(plans/88 `test_all_three_paths_use_the_common_module` 방식).
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.orchestration import task_progress as tp
from src.utils import progress_events as pe

_SRC = Path(__file__).resolve().parents[2] / "src" / "orchestration"


@pytest.fixture
def captured(monkeypatch):
    events: list[tuple[str, dict]] = []

    async def _fake(name, data, *, config=None):
        events.append((name, data))

    monkeypatch.setattr(pe, "adispatch_custom_event", _fake)  # 발행 공통부는 utils(T4 이관)
    return events


# ---------------------------------------------------------------------------
# 페이로드
# ---------------------------------------------------------------------------


def test_payload_start_carries_identity_fields():
    task = {"task_id": "t2", "order": 2, "agent": "data_query", "sub_query": "그 서버들의 CPU",
            "input_from": ["t1"], "status": "in_progress"}
    p = tp.build_task_payload(task, "start", total=2)
    assert p == {
        "task_id": "t2", "order": 2, "total": 2, "agent": "data_query",
        "sub_query": "그 서버들의 CPU", "input_from": ["t1"], "phase": "start", "status": "in_progress",
    }


def test_payload_end_counts_rows_from_any_result_shape():
    task = {"task_id": "t1", "order": 1, "agent": "data_query", "status": "completed"}
    assert tp.build_task_payload(task, "end", result={"rows": [1, 2, 3]})["row_count"] == 3
    assert tp.build_task_payload(task, "end", result={"query_results": [1]})["row_count"] == 1
    assert tp.build_task_payload(task, "end", result={"organized_data": {"rows": []}})["row_count"] == 0
    assert "row_count" not in tp.build_task_payload(task, "end", result={"error": "x"})


def test_payload_skipped_result_uses_plan88_field_names():
    """plans/88 `skip_result` → status=skipped · reason=skip_reason (UI가 '건너뜀'을 그리는 근거)."""
    task = {"task_id": "t2", "order": 2, "agent": "data_query", "status": "skipped"}
    res = {"error": "1단계 조건에 해당하는 서버가 0대라 실행하지 않았습니다.", "skipped": True, "skip_reason": "prior_empty"}
    p = tp.build_task_payload(task, "end", result=res)
    assert p["status"] == "skipped" and p["reason"] == "prior_empty"
    assert p["error"].startswith("1단계")


def test_payload_verdict_fields_copied_verbatim():
    verdict = SimpleNamespace(ok=True, reason=None, scope_col="hostname", scope_size=7,
                              truncated=True, truncated_count=30)
    task = {"task_id": "t2", "order": 2, "agent": "data_query", "status": "in_progress"}
    p = tp.build_task_payload(task, "end", verdict=verdict)
    assert (p["scope_col"], p["scope_size"], p["truncated"], p["truncated_count"]) == ("hostname", 7, True, 30)
    bad = SimpleNamespace(ok=False, reason="prior_failed", scope_col="", scope_size=0, truncated=False, truncated_count=0)
    assert tp.build_task_payload(task, "end", verdict=bad)["reason"] == "prior_failed"


def test_payload_promotes_first_ladder_dependency_note():
    """1단은 verdict 노트를 task['dependency_note']에 싣는다 — 같은 필드명으로 승격."""
    task = {"task_id": "tool_data_query_2", "order": 2, "agent": "data_query", "status": "completed",
            "dependency_note": {"kind": "trace", "reason": "ok", "scope_col": "hostname", "scope_size": 5}}
    p = tp.build_task_payload(task, "end", result={"rows": [1]})
    assert p["scope_col"] == "hostname" and p["scope_size"] == 5 and "reason" not in p


# ---------------------------------------------------------------------------
# 발행
# ---------------------------------------------------------------------------


def test_emit_dispatches_task_event(captured):
    task = {"task_id": "t1", "order": 1, "agent": "data_query", "status": "in_progress"}
    asyncio.run(tp.emit_task_progress(task, "start", total=1))
    asyncio.run(tp.emit_step("agent.aggregate", label="최종 응답 합성"))
    assert captured[0][0] == "task" and captured[0][1]["phase"] == "start"
    assert captured[1] == ("agent.aggregate", {"phase": "start", "label": "최종 응답 합성"})


def test_emit_without_parent_run_is_silent():
    """그래프 밖(단위 테스트·CLI)에서는 RuntimeError를 삼킨다 — 진행 표시가 질의를 죽이지 않는다."""
    task = {"task_id": "t1", "order": 1, "agent": "data_query", "status": "in_progress"}
    asyncio.run(tp.emit_task_progress(task, "start"))  # raises nothing
    asyncio.run(tp.emit_step("x"))


# ---------------------------------------------------------------------------
# 대칭 — 1단·2단 호출부가 같은 헬퍼를 부른다
# ---------------------------------------------------------------------------


def test_both_ladders_call_the_common_helper():
    orch = (_SRC / "agent_orchestrator.py").read_text(encoding="utf-8")
    tools = (_SRC / "deepagents_tools.py").read_text(encoding="utf-8")
    for src in (orch, tools):
        assert "from src.orchestration.task_progress import emit_task_progress" in src
        assert 'emit_task_progress(' in src
    # 2단: 시작·종료 + 게이트 건너뜀(end만) — 세 호출
    assert orch.count("emit_task_progress(") == 3
    # 1단: 시작 · 게이트 건너뜀 end · 정상 end — 세 호출
    assert tools.count("emit_task_progress(") == 3


def test_deep_agent_milestones_present():
    src = (_SRC / "deep_agent.py").read_text(encoding="utf-8")
    assert 'emit_step("agent.resume"' in src
    assert 'emit_step("agent.aggregate"' in src


def test_orchestrator_level_loop_emits_in_order(monkeypatch, captured):
    """2단 실행: task마다 start 1건·end 1건, 레벨 순서 보존, end의 status가 확정값."""
    import importlib

    # 패키지 __init__이 동명 함수를 재노출해 submodule 이름을 가린다 → importlib로 실제 모듈
    ao = importlib.import_module("src.orchestration.agent_orchestrator")

    async def fake_run_agent(task, state, llm, app_config, *, prior, injected=None):
        return {"rows": [{"hostname": "a"}]} if task["task_id"] == "t1" else {"error": "boom"}

    monkeypatch.setattr(ao, "_run_agent", fake_run_agent)
    state = {
        "task_plan": [
            {"task_id": "t1", "agent": "data_query", "sub_query": "q1", "depends_on": [], "input_from": [], "order": 1, "status": "pending"},
            {"task_id": "t2", "agent": "data_query", "sub_query": "q2", "depends_on": ["t1"], "input_from": [], "order": 2, "status": "pending"},
        ],
        "task_results": {},
    }
    cfg = SimpleNamespace(composite=SimpleNamespace(sequential_gate_enabled=False))
    node = getattr(ao, "agent_orchestrator", None) or getattr(ao, "run")
    sig = inspect.signature(node)
    kwargs = {"app_config": cfg}
    if "llm" in sig.parameters:
        kwargs["llm"] = object()
    asyncio.run(node(state, **kwargs))
    seq = [(d["task_id"], d["phase"], d["status"]) for n, d in captured if n == "task"]
    assert seq == [("t1", "start", "in_progress"), ("t1", "end", "completed"),
                   ("t2", "start", "in_progress"), ("t2", "end", "failed")], seq
    ends = [d for n, d in captured if d["phase"] == "end"]
    assert ends[0]["row_count"] == 1 and ends[0]["total"] == 2
    assert ends[1]["error"] == "boom"
