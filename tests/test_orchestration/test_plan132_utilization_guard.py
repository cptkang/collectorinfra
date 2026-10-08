"""사용률 소스 가드 — 사용률 task에서 정본이 아닌 DB 시스템을 뺀다(D-308 ⑨ · plans/132).

ITAM-114(「자산관리에서 지난주 CPU 사용률 추이」)는 표 선별 LLM이 자산 DB의 수집 이력 표로 사용률을
지어냈다. 가드는 LLM 출력과 무관하게 2단 data_query 처리기의 대상 확정 뒤·존 역질문 앞에서 판정한다.
가짜 LLM·목만 쓴다(실 LLM 0).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration import subagents
from src.routing.source_hints import utilization_notice_text
from src.utils.prior_dependency import NOTE_OWNERSHIP

ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "itam"]
POLESTAR_ONLY = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _target(db_id: str) -> dict:
    return {"db_id": db_id, "relevance_score": 0.9, "sub_query_context": "테스트",
            "user_specified": False, "reason": "테스트 분류"}


def _config(active: list[str]) -> MagicMock:
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = active
    config.multi_db.zone_group_exclusive = True
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    return config


@asynccontextmanager
async def _patched(classified: list[str]):
    seen: dict[str, Any] = {"executed": None, "zone_gate": None, "sql_calls": 0}

    async def _record(node_state, *args, **kwargs):
        seen["sql_calls"] += 1
        seen["executed"] = [t["db_id"] for t in node_state["target_databases"]]
        return {}

    def _zone_gate(task, isolated, targets, **kwargs):
        seen["zone_gate"] = [t["db_id"] for t in targets]
        return None

    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})), \
         patch.object(subagents, "_zone_clarification_before_classify", lambda *a, **k: None), \
         patch.object(subagents, "_zone_clarification_or_none_task", _zone_gate), \
         patch.object(subagents, "classify_dbs",
                      AsyncMock(return_value=[_target(d) for d in classified])):
        yield seen


async def _run(
    sub_query: str, *, hints: list[str], active: list[str], classified: list[str],
    user_query: str | None = None, composite: bool = False, db_ids: list[str] | None = None,
) -> tuple[dict, dict[str, Any]]:
    task: dict[str, Any] = {"task_id": "t1", "agent": "data_query", "sub_query": sub_query}
    if db_ids:
        task["db_ids"] = db_ids
    state = {"user_query": user_query or sub_query, "is_composite": composite,
             "parsed_requirements": {"target_db_hints": hints}}
    isolated = subagents._make_isolated_input(task, state, {})
    isolated["user_query"] = sub_query
    async with _patched(classified) as seen:
        result = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_config(active)
        )
    return result, seen


def _ownership_notes(result: dict) -> list[dict]:
    return [n for n in result.get("dependency_notes") or [] if n.get("kind") == NOTE_OWNERSHIP]


# ── ① 지목한 비소유 소스 — 안내만 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_named_asset_source_utilization_is_notice_only():
    result, seen = await _run(
        "자산관리에서 지난주 CPU 사용률 추이 보여줘", hints=["자산관리"],
        active=ACTIVE, classified=["itam", "polestar_b0"],
    )
    assert seen["sql_calls"] == 0 and seen["zone_gate"] is None
    assert result["degraded_reason"] == subagents.REASON_SOURCE_NOT_CANONICAL
    assert result["query_results"] == []
    assert result["final_response"] == utilization_notice_text(["자산관리"], kind="trend")
    assert "「자산관리」" in result["final_response"]
    # 사용자 표현만 — 레지스트리 표시명 비노출(D-264)
    assert "ITAM" not in result["final_response"]


@pytest.mark.asyncio
async def test_named_source_attributed_from_original_in_single_task():
    """단일 task는 task 질의가 이름을 빠뜨려도 원문 지목을 그 task에 귀속한다."""
    result, seen = await _run(
        "지난주 CPU 사용률 추이", hints=["자산관리"],
        user_query="자산관리에서 지난주 CPU 사용률 추이 보여줘",
        active=ACTIVE, classified=["itam"],
    )
    assert seen["sql_calls"] == 0
    assert result["degraded_reason"] == subagents.REASON_SOURCE_NOT_CANONICAL


# ── ② 지목 없이 분류가 끌어온 비소유 DB — 빼고 진행 + 노트 ────────────────────


@pytest.mark.asyncio
async def test_classified_asset_db_is_dropped_with_note():
    result, seen = await _run(
        "은행존 서버 CPU 사용률 추이", hints=[], active=ACTIVE,
        classified=["itam", "polestar_b0"],
    )
    assert seen["zone_gate"] == ["polestar_b0"]
    assert seen["executed"] == ["polestar_b0"]
    assert result["target_db_ids"] == ["polestar_b0"]
    notes = _ownership_notes(result)
    assert len(notes) == 1
    assert notes[0]["from_db_ids"] == ["itam"] and notes[0]["to_db_ids"] == ["polestar_b0"]
    assert notes[0]["capability"] == "server_usage" and notes[0]["task_id"] == "t1"


@pytest.mark.asyncio
async def test_planned_db_ids_path_is_guarded_too():
    """계획 고정(`db_ids`) 경로에도 같은 가드가 걸린다."""
    result, seen = await _run(
        "서버 메모리 사용률 상위 10건", hints=[], active=ACTIVE, classified=[],
        db_ids=["itam", "polestar_cm_gp"],
    )
    assert seen["executed"] == ["polestar_cm_gp"]
    assert len(_ownership_notes(result)) == 1


@pytest.mark.asyncio
async def test_all_targets_dropped_is_notice_not_default_fallback():
    result, seen = await _run(
        "서버 CPU 사용률 추이", hints=[], active=ACTIVE, classified=["itam"],
    )
    assert seen["sql_calls"] == 0
    assert result["degraded_reason"] == subagents.REASON_SOURCE_NOT_CANONICAL
    assert result["final_response"] == utilization_notice_text([], kind="trend")


@pytest.mark.asyncio
async def test_owner_also_named_drops_asset_db_and_proceeds():
    """소유 시스템(위치어 포함)도 함께 지목했으면 대신 답하기가 아니다 — 비소유만 뺀다."""
    result, seen = await _run(
        "김포 자산관리 서버 CPU 사용률", hints=["김포", "자산관리"], active=ACTIVE,
        classified=["itam", "polestar_cm_gp"],
    )
    assert seen["executed"] == ["polestar_cm_gp"]
    assert len(_ownership_notes(result)) == 1


# ── ③~⑥ 불변 ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_non_utilization_asset_query_unchanged():
    result, seen = await _run(
        "자산관리에서 서버별 담당 부서", hints=["자산관리"], active=ACTIVE, classified=["itam"],
    )
    assert seen["executed"] == ["itam"]
    assert "degraded_reason" not in result
    assert not _ownership_notes(result)


@pytest.mark.asyncio
async def test_observability_only_deployment_unchanged():
    result, seen = await _run(
        "은행존 서버 CPU 사용률 추이", hints=[], active=POLESTAR_ONLY, classified=["polestar_b0"],
    )
    assert seen["executed"] == ["polestar_b0"]
    assert "dependency_notes" not in result


@pytest.mark.asyncio
async def test_composite_non_utilization_asset_task_unchanged():
    """복합 계획에서 사용률을 말하지 않는 자산 task는 건드리지 않는다(판정 텍스트 = task 질의)."""
    result, seen = await _run(
        "선행 결과 서버들의 담당 부서", hints=["자산관리"], composite=True,
        user_query="CPU 사용률 높은 서버의 자산관리 담당 부서",
        active=ACTIVE, classified=["itam"],
    )
    assert seen["executed"] == ["itam"]
    assert not _ownership_notes(result)


@pytest.mark.asyncio
async def test_availability_is_not_utilization():
    """가동률(D-200)은 사용률이 아니다."""
    result, seen = await _run(
        "자산관리에서 서버 가동률", hints=["자산관리"], active=ACTIVE, classified=["itam"],
    )
    assert seen["executed"] == ["itam"]
    assert "degraded_reason" not in result


@pytest.mark.asyncio
async def test_no_active_owner_is_noop():
    """사용률 소유 시스템에 활성 DB가 없으면 가드는 아무것도 하지 않는다(로그만)."""
    result, seen = await _run(
        "서버 CPU 사용률 추이", hints=[], active=["itam"], classified=["itam"],
    )
    assert seen["executed"] == ["itam"]
    assert not _ownership_notes(result)


# ── 사용량(현재값)·사용 추이(기간 변화) 정의(plans/132 v1.5 후속 ②) ──────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("query, word", [
    ("자산관리에서 CPU 사용량 보여줘", "요청하신 사용량·사용률 정보는"),
    ("자산관리에서 메모리 사용 추이", "요청하신 사용 추이 정보는"),
])
async def test_named_asset_source_usage_and_trend_notice_follow_user_wording(
    query: str, word: str,
):
    result, seen = await _run(query, hints=["자산관리"], active=ACTIVE, classified=["itam"])
    assert seen["sql_calls"] == 0
    assert result["degraded_reason"] == subagents.REASON_SOURCE_NOT_CANONICAL
    assert word in result["final_response"] and "「자산관리」" in result["final_response"]


@pytest.mark.asyncio
async def test_unnamed_trend_drops_asset_db():
    """지목 없는 「사용 추이」형도 사용률 영역 비소유 DB를 뺀다(종전 정규식은 못 잡았다)."""
    result, seen = await _run(
        "서버 CPU 사용 추이", hints=[], active=ACTIVE, classified=["itam", "polestar_cm_gp"],
    )
    assert seen["executed"] == ["polestar_cm_gp"]
    assert len(_ownership_notes(result)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("query, hints", [
    ("자산관리에서 서버 메모리 용량", ["자산관리"]),
    ("CPU 코어 수", []),
])
async def test_capacity_and_spec_queries_keep_asset_db(query: str, hints: list[str]):
    """용량·사양(설치 크기)은 가드 대상이 아니다 — 자산 DB 조회 유지."""
    result, seen = await _run(query, hints=hints, active=ACTIVE, classified=["itam"])
    assert seen["executed"] == ["itam"]
    assert "degraded_reason" not in result
    assert not _ownership_notes(result)
