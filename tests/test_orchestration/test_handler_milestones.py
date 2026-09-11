"""핸들러 내부 마일스톤 — plans/89 T4 · D-204 (`SPEC-composite-task-progress.md` 보강).

실 LLM 0. `adispatch_custom_event`를 가로채(모의 콜백) 무이벤트 구간 앞뒤에서 `step` 이벤트가
나는지 단언한다 — ①`schema_analyzer` 라이브 샘플 수집(D-154 타임박스 구간) ②서브에이전트
파이프라인 단계(`pipeline.<stage>`) ③deep_agent 재개/합성(기존). 발행 공통부는 utils 계층
(`src/utils/progress_events.py`)이라 nodes·orchestration이 같은 함수를 쓴다.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

# `src.nodes` 패키지가 같은 이름의 노드 함수를 노출해 모듈이 가려진다 — 모듈을 직접 잡는다
sa_mod = importlib.import_module("src.nodes.schema_analyzer")
from src.orchestration import subagents as sub
from src.orchestration import task_progress as tp
from src.utils import progress_events as pe

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def captured(monkeypatch):
    events: list[tuple[str, dict]] = []

    async def _fake(name, data, *, config=None):
        events.append((name, data))

    monkeypatch.setattr(pe, "adispatch_custom_event", _fake)
    return events


# ---------------------------------------------------------------------------
# 공통 발행부 (utils)
# ---------------------------------------------------------------------------


def test_emit_step_lives_in_utils_and_is_reexported(captured):
    """nodes(application)가 orchestration을 참조하지 않도록 발행부는 utils에 있고, 기존 임포트는 유지된다."""
    assert tp.emit_step is pe.emit_step
    asyncio.run(pe.emit_step("x", "end", label="끝"))
    assert captured == [("x", {"phase": "end", "label": "끝"})]


def test_emit_step_without_parent_run_is_silent():
    asyncio.run(pe.emit_step("schema.sample", label="샘플 수집 1/1"))  # RuntimeError를 삼킨다


def test_emit_step_swallows_unexpected_dispatch_errors(monkeypatch):
    async def _boom(name, data, *, config=None):
        raise ValueError("callback broke")

    monkeypatch.setattr(pe, "adispatch_custom_event", _boom)
    asyncio.run(pe.emit_step("pipeline.execute"))  # 표시 실패가 질의를 죽이면 안 된다


# ---------------------------------------------------------------------------
# ① schema_analyzer 라이브 샘플 수집 — 테이블마다 k/n · 종료 1회
# ---------------------------------------------------------------------------


def _schema_dict(tables: dict[str, dict]) -> dict:
    return {"tables": {name: dict(cols) for name, cols in tables.items()}}


def test_live_sample_loop_emits_progress_per_table(captured):
    client = SimpleNamespace(get_sample_data=AsyncMock(return_value=[{"a": 1}]))
    sd = _schema_dict({"t1": {}, "t2": {"sample_data": [{"cached": 1}]}, "t3": {}})
    asyncio.run(sa_mod._collect_live_samples(client, sd, ["t1", "t2", "t3"], "polestar"))

    # 캐시된 t2는 건너뛰므로 대상은 2개 — 시작 2회(1/2·2/2) + 종료 1회
    assert [(n, d["phase"], d.get("label")) for n, d in captured] == [
        ("schema.sample", "start", "샘플 수집 1/2"),
        ("schema.sample", "start", "샘플 수집 2/2"),
        ("schema.sample", "end", "샘플 수집 완료 2/2"),
    ]
    assert sd["tables"]["t1"]["sample_data"] == [{"a": 1}]
    assert sd["tables"]["t2"]["sample_data"] == [{"cached": 1}]  # 캐시 보존
    assert client.get_sample_data.await_count == 2


def test_live_sample_loop_no_pending_emits_nothing(captured):
    client = SimpleNamespace(get_sample_data=AsyncMock())
    sd = _schema_dict({"t1": {"sample_data": [{"x": 1}]}})
    asyncio.run(sa_mod._collect_live_samples(client, sd, ["t1"], "polestar"))
    assert captured == [] and client.get_sample_data.await_count == 0


def test_live_sample_loop_failure_still_ends_and_keeps_going(captured):
    """조회 실패·타임아웃은 종전대로 스킵(경고)하고 다음 테이블로 간다 — 종료 이벤트는 그대로 1회."""

    async def _fetch(table_name, limit=5):
        if table_name == "t1":
            raise RuntimeError("boom")
        return [{"ok": 1}]

    client = SimpleNamespace(get_sample_data=_fetch)
    sd = _schema_dict({"t1": {}, "t2": {}})
    asyncio.run(sa_mod._collect_live_samples(client, sd, ["t1", "t2"], None))
    assert "sample_data" not in sd["tables"]["t1"] and sd["tables"]["t2"]["sample_data"] == [{"ok": 1}]
    assert captured[-1] == ("schema.sample", {"phase": "end", "label": "샘플 수집 완료 2/2"})


def test_live_sample_loop_budget_exhaustion_counts_skipped(captured, monkeypatch):
    """총량 예산 소진 뒤 테이블은 조회·시작 이벤트 없이 스킵되고, 종료 라벨이 완료 수를 정직하게 적는다."""
    monkeypatch.setattr(sa_mod, "_SAMPLE_TOTAL_BUDGET_SEC", -1.0)  # 첫 판정부터 소진
    client = SimpleNamespace(get_sample_data=AsyncMock(return_value=[{"a": 1}]))
    sd = _schema_dict({"t1": {}, "t2": {}})
    asyncio.run(sa_mod._collect_live_samples(client, sd, ["t1", "t2"], "polestar"))
    assert client.get_sample_data.await_count == 0
    assert captured == [("schema.sample", {"phase": "end", "label": "샘플 수집 완료 0/2"})]


def test_schema_analyzer_node_calls_the_helper():
    src = (_ROOT / "src" / "nodes" / "schema_analyzer.py").read_text(encoding="utf-8")
    assert "from src.utils.progress_events import emit_step" in src
    assert "await _collect_live_samples(client, schema_dict, relevant, db_id)" in src


# ---------------------------------------------------------------------------
# ② 서브에이전트 파이프라인 단계 — schema → generate → validate → execute (재시도 라벨 포함)
# ---------------------------------------------------------------------------


def _cfg(max_retry: int = 3):
    return SimpleNamespace(query=SimpleNamespace(max_retry_count=max_retry))


def _patch_pipeline(monkeypatch, *, validate_passes: list[bool], exec_errors: list[str | None]):
    calls: list[str] = []
    v_iter = iter(validate_passes)
    e_iter = iter(exec_errors)

    async def _schema(state, llm=None, app_config=None):
        calls.append("schema")
        return {}

    async def _gen(state, llm=None, app_config=None):
        calls.append("generate")
        return {"retry_count": state.get("retry_count", 0) + (1 if "generate" in calls[:-1] else 0)}

    async def _validate(state, app_config=None):
        calls.append("validate")
        return {"validation_result": {"passed": next(v_iter), "reason": "bad sql"}}

    async def _exec(state, app_config=None):
        calls.append("execute")
        err = next(e_iter)
        return {"error_message": err} if err else {"error_message": None, "query_results": [{"h": 1}]}

    monkeypatch.setattr(sub, "schema_analyzer", _schema)
    monkeypatch.setattr(sub, "query_generator", _gen)
    monkeypatch.setattr(sub, "query_validator", _validate)
    monkeypatch.setattr(sub, "query_executor", _exec)
    return calls


def _steps(captured):
    return [(n, d["phase"], d.get("label")) for n, d in captured]


def test_single_db_pipeline_emits_stage_events_in_order(monkeypatch, captured):
    calls = _patch_pipeline(monkeypatch, validate_passes=[True], exec_errors=[None])
    out = asyncio.run(sub._run_single_db_pipeline({"user_query": "q"}, llm=None, app_config=_cfg()))
    assert calls == ["schema", "generate", "validate", "execute"]
    assert out["query_results"] == [{"h": 1}]
    assert _steps(captured) == [
        ("pipeline.schema", "start", "스키마 분석"), ("pipeline.schema", "end", None),
        ("pipeline.generate", "start", "SQL 생성"), ("pipeline.generate", "end", None),
        ("pipeline.validate", "start", "SQL 검증"), ("pipeline.validate", "end", None),
        ("pipeline.execute", "start", "SQL 실행"), ("pipeline.execute", "end", None),
    ]


def test_single_db_pipeline_retry_labels_count_regenerations(monkeypatch, captured):
    """검증 실패 → 재생성 라벨 '1회차', 실행 에러 → '2회차'. 이벤트 순서가 실제 호출 순서와 같다."""
    calls = _patch_pipeline(monkeypatch, validate_passes=[False, True, True], exec_errors=["db error", None])
    asyncio.run(sub._run_single_db_pipeline({"user_query": "q"}, llm=None, app_config=_cfg()))
    assert calls == ["schema", "generate", "validate", "generate", "validate", "execute",
                     "generate", "validate", "execute"]
    gen_labels = [lab for n, ph, lab in _steps(captured) if n == "pipeline.generate" and ph == "start"]
    assert gen_labels == ["SQL 생성", "SQL 재생성 1회차", "SQL 재생성 2회차"]
    # 검증 실패 회차에는 execute 이벤트가 없다 — 이벤트는 실행된 단계만 반영한다
    exec_starts = [1 for n, ph, _ in _steps(captured) if n == "pipeline.execute" and ph == "start"]
    assert len(exec_starts) == 2


def test_data_query_pipeline_wraps_organizer_and_multi_db():
    src = (_ROOT / "src" / "orchestration" / "subagents.py").read_text(encoding="utf-8")
    assert "from src.utils.progress_events import emit_step" in src
    for name in ("pipeline.schema", "pipeline.generate", "pipeline.validate", "pipeline.execute",
                 "pipeline.multi_db", "pipeline.organize"):
        assert src.count(f'emit_step("{name}", "start"') == 1, name
        assert src.count(f'emit_step("{name}", "end"') == 1, name


# ---------------------------------------------------------------------------
# UI 라벨 사전 — 서버 이벤트 이름이 전부 사람이 읽는 라벨을 가진다
# ---------------------------------------------------------------------------


def test_ui_step_labels_cover_all_milestone_names():
    js = (_ROOT / "src" / "static" / "js" / "app.js").read_text(encoding="utf-8")
    for name in ("agent.resume", "agent.aggregate", "schema.sample", "pipeline.schema", "pipeline.generate",
                 "pipeline.validate", "pipeline.execute", "pipeline.multi_db", "pipeline.organize"):
        assert f'"{name}":' in js, name
