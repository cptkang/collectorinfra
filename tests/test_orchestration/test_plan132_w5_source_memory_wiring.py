"""plans/132 W5 — 소스 선택 기억 배선(계획 출구 · 칩 답변 쓰기 · 라우트 쓰기 게이트 · 명령).

판정 순서 `명시 > 단독 소유 > [기억] > 칩` — 기억은 칩이 걸린 task에만 쓴다. TTL 0(기본)이면
저장소에 닿지 않는다(비트 동일). 쓰기는 칩 선택·「다른 소스로 보기」 정정·관리자 정리 셋뿐이다.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.orchestration import conditional_agents as ca
from src.routing.registry import get_registry
from src.schema_cache import source_memory as sm
from src.state import create_initial_state
from tests.test_schema_cache.test_plan132_source_memory import FakeRedis

ip = importlib.import_module("src.orchestration.intent_planner")


def _cfg(ttl_days: int = 30) -> SimpleNamespace:
    return SimpleNamespace(
        router=SimpleNamespace(source_memory_ttl_days=ttl_days),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar", "itam"],
                                 zone_group_exclusive=False),
    )


@pytest.fixture
def store(monkeypatch) -> sm.SourceMemoryStore:
    s = sm.SourceMemoryStore(FakeRedis())
    monkeypatch.setattr(sm, "open_store", AsyncMock(return_value=s))
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _c: {"apm": "apm_query"})
    return s


def _chip_plan(query: str = "인스턴스 목록 보여줘") -> tuple[dict[str, Any], dict[str, Any]]:
    task = {"task_id": "t1", "agent": "data_query", "sub_query": query, "depends_on": [],
            "input_from": [], "order": 1, "status": "pending",
            "areas": ["was_instance", "host_location"]}
    state = create_initial_state(user_query=query, user_id="u1", allow_zone_clarification=True)
    ca.apply_source_selection([task], state, _cfg())
    assert ca.SOURCE_CLARIFICATION_KEY in task, "전제 — 칩이 걸린 task"
    return {"task_plan": [task]}, state


@pytest.mark.asyncio
async def test_ttl_zero_never_touches_store(monkeypatch) -> None:
    opened = AsyncMock(side_effect=AssertionError("TTL 0 — 저장소 접근 금지"))
    monkeypatch.setattr(sm, "open_store", opened)
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _c: {"apm": "apm_query"})
    result, state = _chip_plan()
    await ip._apply_source_memory(result, state, _cfg(0))
    state["selected_sources"] = ["apm"]
    state["source_selection_meta"] = {"origin": "user_choice", "areas": []}
    await ip._record_source_choice(result, state, _cfg(0))
    assert ca.SOURCE_CLARIFICATION_KEY in result["task_plan"][0] and "source_switch" not in result
    opened.assert_not_called()


@pytest.mark.asyncio
async def test_remembered_choice_replaces_chip_with_notice_and_switch(store) -> None:
    fp = sm.registry_fingerprint(get_registry())
    await store.save(sm.build_case("인스턴스 목록 보여줘", ["apm"], origin="user_choice",
                                   scope="user:u1", registry_fp=fp), ttl_seconds=86400)
    result, state = _chip_plan()
    await ip._apply_source_memory(result, state, _cfg())
    task = result["task_plan"][0]
    assert task["agent"] == "apm_query" and ca.SOURCE_CLARIFICATION_KEY not in task
    switch = result["source_switch"]
    assert switch["kind"] == "source_select" and switch["correction"] is True
    assert [o.get("source") for o in switch["options"]] == ["polestar"], "나머지 후보만"
    assert [n["reason"] for n in result["dependency_notes"]] == ["source_memory"]
    assert (await store.load("user:u1"))[0]["use_count"] == 1, "사용 = 재확인(sliding)"


@pytest.mark.asyncio
async def test_other_users_choice_is_not_used(store) -> None:
    """개인 사례는 그 사용자에게만(G-11 (a)) — 조직 공용 승격 전에는 남의 선택이 판정을 바꾸지
    않는다."""
    fp = sm.registry_fingerprint(get_registry())
    await store.save(sm.build_case("인스턴스 목록 보여줘", ["apm"], origin="user_choice",
                                   scope="user:someone", registry_fp=fp), ttl_seconds=86400)
    result, state = _chip_plan()
    await ip._apply_source_memory(result, state, _cfg())
    assert ca.SOURCE_CLARIFICATION_KEY in result["task_plan"][0]


@pytest.mark.asyncio
async def test_seed_case_resolves_chip(store) -> None:
    result, state = _chip_plan("WAS 인스턴스 목록 보여줘")
    await ip._apply_source_memory(result, state, _cfg())
    assert result["task_plan"][0]["agent"] == "apm_query"
    assert await store.load(sm.SCOPE_ORG) == [], "시드는 저장소에 쓰지 않는다"


@pytest.mark.asyncio
async def test_chip_answer_writes_user_case_only_with_route_meta(store) -> None:
    state = create_initial_state(user_query="인스턴스 목록 보여줘", user_id="u1",
                                 selected_sources=["apm"])
    await ip._record_source_choice({}, state, _cfg())
    assert await store.load("user:u1") == [], "칩 응답 표지 없으면 쓰지 않는다(API 직접 호출 등)"
    state["source_selection_meta"] = {"origin": "user_choice", "areas": ["was_instance"]}
    await ip._record_source_choice({}, state, _cfg())
    [case] = await store.load("user:u1")
    assert case["sources"] == ["apm"] and case["origin"] == "user_choice"
    state["selected_sources"] = ["polestar"]
    state["source_selection_meta"] = {"origin": "feedback", "areas": []}
    await ip._record_source_choice({}, state, _cfg())
    [case] = await store.load("user:u1")
    assert (case["sources"], case["origin"]) == (["polestar"], "feedback"), "정정 = 갱신"


@pytest.mark.asyncio
async def test_system_judgement_is_not_written(store) -> None:
    """계획 출구가 스스로 고른 소스(단독 소유 고정)는 기억에 남지 않는다."""
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "WAS 인스턴스 목록",
            "depends_on": [], "input_from": [], "order": 1, "status": "pending",
            "areas": ["was_instance"]}
    state = create_initial_state(user_query="WAS 인스턴스 목록", user_id="u1",
                                 allow_zone_clarification=True)
    result = {"task_plan": [task]}
    ip._apply_source_selection(result, state, _cfg())
    await ip._apply_source_memory(result, state, _cfg())
    await ip._record_source_choice(result, state, _cfg())
    assert task["agent"] == "apm_query" and await store.scopes() == []


def test_route_meta_only_for_chip_or_switch_answers() -> None:
    from src.api.routes.query import _source_selection_meta
    from src.api.schemas import QueryRequest

    body = QueryRequest(query="q", selected_sources=["apm"])
    chip = {"zone_clarification": {"kind": "source_select", "areas": ["was_instance"]}}
    assert _source_selection_meta(body, chip) == {
        "origin": "user_choice", "areas": ["was_instance"]}
    switch = {"source_switch": {"correction": True, "areas": []}}
    assert _source_selection_meta(body, switch) == {"origin": "feedback", "areas": []}
    assert _source_selection_meta(body, {"zone_clarification": {"kind": "zone_select"}}) is None
    assert _source_selection_meta(QueryRequest(query="q"), chip) is None


@pytest.mark.asyncio
async def test_chat_command_view_and_delete(store) -> None:
    fp = sm.registry_fingerprint(get_registry())
    await store.save(sm.build_case("인스턴스 목록 보여줘", ["apm"], origin="user_choice",
                                   scope="user:u1", registry_fp=fp), ttl_seconds=86400)
    assert ip._is_source_memory_command("소스 기억 보여줘")
    assert not ip._is_source_memory_command("소스 기억해줘")
    assert not ip._is_source_memory_command("기억 보여줘")
    state = create_initial_state(user_query="소스 기억 보여줘", user_id="u1")
    view = await ip._source_memory_command_plan(state, _cfg(), "소스 기억 보여줘")
    assert "1건" in view["task_plan"][0]["direct_response"]
    gone = await ip._source_memory_command_plan(state, _cfg(), "소스 기억 삭제해줘")
    assert "1건을 모두 지웠습니다" in gone["task_plan"][0]["direct_response"]
    assert await store.load("user:u1") == []
    off = await ip._source_memory_command_plan(state, _cfg(0), "소스 기억 보여줘")
    assert "꺼져 있습니다" in off["task_plan"][0]["direct_response"]
