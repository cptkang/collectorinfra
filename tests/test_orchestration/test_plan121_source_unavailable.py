"""plans/121 TP-1.11a — 요청 소스 불가 사유 노트(N-11 침묵 대체 가시화).

고정하는 계약:
① 입력 파서 `target_db_hints`가 **등록됐지만 비활성인 DB로만** 해소되면 노트 1건(LLM 0).
② 대상 집합은 바꾸지 않는다 — 분류·핀·승계 결과 그대로 조회한다.
③ 활성 DB로도 해소되는 힌트(제품명 단독)·해소 0건 힌트(미등록 소스)는 노트가 없다.
④ 문구는 사용자 표현만 싣고 레지스트리 표시명을 드러내지 않는다(D-264) · 하네스 표지어 회피.
⑤ 재계획 의미 = 안내성(§12.4) — 성공 턴의 결정적 종료(119 N-2)를 끄지 않는다.

LLM·DB·Redis 0 — 조회 노드는 대역이다.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.scenario.assertions import (
    _CORRECT_MARKERS,
    _GUIDE_MARKERS,
    _PARTIAL_MARKERS,
    _REFUSE_MARKERS,
)
from src.orchestration import subagents
from src.orchestration.replanner import _INFO_NOTE_KINDS, _all_tasks_succeeded
from src.routing.location_hints import inactive_hinted_sources
from src.utils.prior_dependency import NOTE_SOURCE_UNAVAILABLE

PROD_ACTIVE = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


# ── ① ③ 판정 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hints", "active", "expected"),
    [
        (["ITAM"], PROD_ACTIVE, ["ITAM"]),
        (["자산관리"], PROD_ACTIVE, ["자산관리"]),
        (["ITSM", "김포"], PROD_ACTIVE, ["ITSM"]),
        # 제품명 단독은 활성 폴스타로도 해소된다 — 비활성 샌드박스(`polestar`)를 이유로 노트 금지
        (["폴스타"], PROD_ACTIVE, []),
        (["김포"], PROD_ACTIVE, []),
        # 미등록 소스는 이번 판정 대상이 아니다(Prometheus는 TP-9.2 등재 뒤)
        (["프로메테우스"], PROD_ACTIVE, []),
        (["알 수 없는 위치"], PROD_ACTIVE, []),
        (["ITAM"], ["polestar", "itam"], []),
        ([], PROD_ACTIVE, []),
        (["ITAM", "ITAM"], PROD_ACTIVE, ["ITAM"]),
    ],
)
def test_inactive_hinted_sources(hints, active, expected):
    assert inactive_hinted_sources(hints, active) == expected


def test_non_list_hints_are_ignored():
    assert inactive_hinted_sources("ITAM", PROD_ACTIVE) == []  # type: ignore[arg-type]


# ── ② ④ 핸들러 배선 ──────────────────────────────────────────────────────


def _target(db_id: str) -> dict:
    return {"db_id": db_id, "relevance_score": 0.9, "sub_query_context": "계약 만료일",
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
    executed: dict[str, list[str]] = {}

    async def _record(node_state, *args, **kwargs):
        executed["db_ids"] = [t["db_id"] for t in node_state["target_databases"]]
        return {}

    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=_record)), \
         patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})), \
         patch.object(subagents, "_zone_clarification_before_classify", lambda *a, **k: None), \
         patch.object(subagents, "_zone_clarification_or_none_task", lambda *a, **k: None), \
         patch.object(subagents, "classify_dbs",
                      AsyncMock(return_value=[_target(d) for d in classified])):
        yield executed


async def _run(hints: list[str], *, active: list[str], classified: list[str]):
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 계약 만료일"}
    state = {"user_query": "서버 계약 만료일",
             "parsed_requirements": {"target_db_hints": hints}}
    isolated = subagents._make_isolated_input(task, state, {})
    async with _patched(classified) as executed:
        result = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_config(active)
        )
    return result, executed.get("db_ids")


@pytest.mark.asyncio
async def test_inactive_hint_adds_note_without_changing_targets():
    result, executed = await _run(["ITAM"], active=PROD_ACTIVE, classified=["polestar_cm_gp"])
    assert executed == ["polestar_cm_gp"]
    assert result["target_db_ids"] == ["polestar_cm_gp"]
    notes = [n for n in result.get("dependency_notes") or []
             if n.get("kind") == NOTE_SOURCE_UNAVAILABLE]
    assert len(notes) == 1
    note = notes[0]
    assert note["task_id"] is None and note["reason"] == "source_inactive"
    assert "「ITAM」" in note["detail"]
    # 레지스트리 표시명(ITAM DB 등)을 새로 드러내지 않는다 — 사용자 표현만
    assert "ITAM DB" not in note["detail"]
    markers = (*_REFUSE_MARKERS, *_GUIDE_MARKERS, *_CORRECT_MARKERS, *_PARTIAL_MARKERS)
    assert not any(m in note["detail"] for m in markers)


@pytest.mark.asyncio
async def test_active_hint_has_no_note_and_same_result_shape():
    result, executed = await _run(["김포"], active=PROD_ACTIVE, classified=["polestar_cm_gp"])
    assert executed == ["polestar_cm_gp"]
    assert not [n for n in result.get("dependency_notes") or []
                if n.get("kind") == NOTE_SOURCE_UNAVAILABLE]


@pytest.mark.asyncio
async def test_no_hint_result_has_no_dependency_notes():
    """힌트가 없으면 종전과 같다 — 노트 키 자체가 생기지 않는다."""
    result, _ = await _run([], active=PROD_ACTIVE, classified=["polestar_cm_gp"])
    assert "dependency_notes" not in result


# ── ⑤ 재계획 의미 선언 ───────────────────────────────────────────────────


def test_source_unavailable_is_informational():
    assert NOTE_SOURCE_UNAVAILABLE in _INFO_NOTE_KINDS
    res = {
        "query_results": [{"hostname": "a"}],
        "organized_data": {"rows": [{"hostname": "a"}]},
        "dependency_notes": [{"kind": NOTE_SOURCE_UNAVAILABLE, "task_id": None,
                              "detail": "요청하신 「ITAM」 데이터 소스는 …"}],
    }
    state = {
        "user_query": "ITAM 계약 만료일",
        "task_plan": [{"task_id": "t1", "agent": "data_query", "sub_query": "x",
                       "status": "completed", "depends_on": [], "input_from": []}],
        "task_results": {"t1": res},
    }
    assert _all_tasks_succeeded(state)
