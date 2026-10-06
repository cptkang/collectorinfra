"""측정 연결점 `run_capture` — 기본 no-op · 예외 격리 · 2단 task 당 1건 (plans/135 W3 · D-301 ⑥).

LLM·DB 0 — 조회 파이프라인은 대역이다(`test_tier12_db_authz.py`와 같은 방식).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.itam_bench._serve import FileSink, schema_context_record
from src.observability import run_capture
from src.orchestration import subagents


@pytest.fixture(autouse=True)
def _clean_sink():
    run_capture.uninstall()
    yield
    run_capture.uninstall()


def _pipeline_state(thread_id: str) -> dict[str, Any]:
    """단일 DB 파이프라인이 남기는 상태 모양 — 표본 행·설명 문구·사용자 필드가 섞여 있다."""
    return {
        "thread_id": thread_id,
        "user_id": "5488923",
        "user_department": "합성부서",
        "active_db_id": "itam",
        "relevant_tables": ["TCDMSIF80"],
        "schema_info": {
            "tables": {
                "TCDMSIF80": {
                    "columns": [
                        {"name": "sevrHostName", "type": "varchar"},
                        {"name": "rspblPsnEmnm", "type": "varchar"},
                    ],
                    "sample_data": [{"sevrHostName": "svr-db-03", "rspblPsnEmnm": "홍길동"}],
                }
            },
            "relationships": [],
        },
        "column_descriptions": {"TCDMSIF80.sevrHostName": "서버 호스트 이름"},
        "generated_sql": "SELECT sevrHostName FROM TCDMSIF80 LIMIT 1000",
        "query_results": [{"sevrHostName": "svr-db-03", "rspblPsnEmnm": "홍길동"}],
    }


def _pack(state: dict[str, Any]) -> dict[str, Any]:
    return subagents._pack_pipeline_result(
        state,
        [{"db_id": "itam"}],
        None,
        ownership_notes=[],
        db_origin="classified",
        db_succeeded=False,
        db_pinned=False,
    )


class TestSeam:
    def test_emit_without_sink_is_noop(self) -> None:
        assert run_capture.installed() is False
        run_capture.emit("task_pipeline_state", {"x": 1})  # 예외 없음 · 아무 일 없음

    def test_install_and_uninstall(self) -> None:
        seen: list[tuple[str, Any]] = []
        run_capture.install(lambda kind, payload: seen.append((kind, payload)))
        run_capture.emit("k", 1)
        run_capture.uninstall()
        run_capture.emit("k", 2)
        assert seen == [("k", 1)]

    def test_sink_exception_is_swallowed(self) -> None:
        def boom(_kind: str, _payload: Any) -> None:
            raise RuntimeError("측정 실패")

        run_capture.install(boom)
        run_capture.emit("k", 1)

    def test_pack_result_unchanged_by_sink(self) -> None:
        baseline = _pack(_pipeline_state("t-1"))
        run_capture.install(lambda _k, _p: (_ for _ in ()).throw(RuntimeError("x")))
        with_failing_sink = _pack(_pipeline_state("t-1"))
        seen: list[Any] = []
        run_capture.install(lambda kind, payload: seen.append(kind))
        with_sink = _pack(_pipeline_state("t-1"))
        assert baseline == with_failing_sink == with_sink
        assert seen == ["task_pipeline_state"]


class TestSinkRecord:
    def test_record_copies_names_and_flags_only(self) -> None:
        record = schema_context_record("task_pipeline_state", _pipeline_state("t-9"))
        assert record == {
            "kind": "task_pipeline_state",
            "thread_id": "t-9",
            "dbs": {
                "itam": {
                    "tables": {
                        "TCDMSIF80": {
                            "columns": ["sevrHostName", "rspblPsnEmnm"],
                            "with_meaning": ["sevrHostName"],
                            "sample_rows": True,
                        }
                    },
                    "structure_meta": False,
                    # plans/139 W6-d — 상태에 예산·선별·종결이 없으면 null
                    "prompt_tokens_est": None,
                    "budget_stage": None,
                    "backend_reported_tokens": None,
                    "selection_source": None,
                    "selected_count": None,
                    "stop_reason": None,
                }
            },
        }
        text = repr(record)
        for leaked in ("5488923", "합성부서", "홍길동", "svr-db-03", "서버 호스트 이름", "SELECT"):
            assert leaked not in text

    def test_multi_db_meaning_is_unknown(self) -> None:
        state = {
            "thread_id": "t",
            "is_multi_db": True,
            "db_schemas": {"itam": _pipeline_state("t")["schema_info"]},
        }
        record = schema_context_record("task_pipeline_state", state)
        assert record["dbs"]["itam"]["tables"]["TCDMSIF80"]["with_meaning"] is None

    def test_other_kinds_ignored(self) -> None:
        assert schema_context_record("other", {}) is None
        assert schema_context_record("task_pipeline_state", None) is None


# ── 2단 핸들러 경유 — task 당 1건 ──────────────────────────────────────


def _config() -> MagicMock:
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["itam"]
    config.multi_db.zone_group_exclusive = True
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    return config


@pytest.mark.asyncio
async def test_tier2_handler_emits_once_per_task(tmp_path) -> None:
    capture = tmp_path / "capture.jsonl"
    run_capture.install(FileSink(capture))
    state = {
        "user_query": "자산관리 서버 목록 보여줘",
        "thread_id": "itam-bench-ITAM-02-abc",
        "user_id": "5488923",
        "user_department": "합성부서",
        "allowed_db_ids": None,
        "user_role": "user",
    }

    async def fake_single(node_state, *_args, **_kwargs):
        return {
            key: value
            for key, value in _pipeline_state(node_state["thread_id"]).items()
            if key not in ("thread_id", "user_id", "user_department")
        }

    target = {
        "db_id": "itam",
        "relevance_score": 0.9,
        "sub_query_context": "서버 목록",
        "user_specified": False,
        "reason": "테스트",
    }
    for task_id in ("t1", "t2"):
        task = {"task_id": task_id, "agent": "data_query", "sub_query": "서버 목록"}
        isolated = subagents._make_isolated_input(task, state, {})
        with (
            patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=fake_single)),
            patch.object(subagents, "result_organizer", AsyncMock(return_value={})),
            patch.object(subagents, "classify_dbs", AsyncMock(return_value=[target])),
        ):
            await subagents.run_data_query_pipeline(
                task, isolated, llm=AsyncMock(), app_config=_config()
            )
    lines = capture.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all('"thread_id": "itam-bench-ITAM-02-abc"' in line for line in lines)
    for leaked in ("5488923", "합성부서", "홍길동", "svr-db-03"):
        assert all(leaked not in line for line in lines)
