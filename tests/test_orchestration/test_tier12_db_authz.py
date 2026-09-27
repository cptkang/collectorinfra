"""plans/116 §10.3 결함 ② · D-232 — 1·2단 경로의 사용자별 DB 조회 인가.

3단은 `graph.py`가 `semantic_router`를 `authorized_router`로 감싸 라우터 노드 경계에서 거른다.
1·2단(`deep_agent`·`intent_orchestration`)에는 라우터가 없고 조회 대상 DB를 task 핸들러가
정한다. 2026-09-23 녹화 실측: 조회 가능 DB가 빈 목록인 계정이 2단에서는 샌드박스를 조회했고
같은 계정·같은 구성의 3단은 「조회할 수 있는 DB가 없습니다」로 거절했다.

고정하는 계약(3단과 같은 의미 — `src/routing/db_authz.py`):
① `None`/관리자 = 제한 없음 · `[]` = 조회 불가 · 목록 = 그 안의 DB만
② 대상 출처(분류·계획 고정·존 선택·승계)와 무관하게 확정된 대상에 건다
③ 거른 결과가 없으면 조회하지 않고 3단과 같은 의도·문구로 끝낸다(침묵 강등 금지)
④ 2단 한 턴 전체(오케스트레이터 → 재계획 → 집계)에서도 같은 문구로 끝나고 재계획 LLM을 부르지 않는다
⑤ 1단 도구 경로·프로세스 조회·호스트 조사도 같은 판정을 받는다

LLM·DB·Redis 0 — 전부 대역이다.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.orchestration import subagents
from src.orchestration.agent_orchestrator import agent_orchestrator
from src.orchestration.deepagents_tools import _run_subagent_tool
from src.orchestration.host_inspect import run_host_inspect
from src.orchestration.process_query import run_process_query
from src.orchestration.replanner import replanner
from src.orchestration.result_aggregator import result_aggregator
from src.routing.db_authz import ACCESS_DENIED_INTENT, ACCESS_DENIED_MESSAGE
from src.state import create_initial_state

ACTIVE = ["db_a", "db_b", "db_c"]
USER = "user"


def _target(db_id: str) -> dict:
    return {
        "db_id": db_id,
        "relevance_score": 0.9,
        "sub_query_context": "서버 목록",
        "user_specified": False,
        "reason": "테스트 분류",
    }


def _config() -> MagicMock:
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ACTIVE
    config.multi_db.zone_group_exclusive = True
    config.router.capability_ownership_enabled = False
    config.polestar_rest.realtime_usage_enabled = False
    config.orchestrator.max_tool_result_tokens = 2000
    return config


class _PipelineSpy:
    """조회 실행 노드 대역 — 실제로 조회에 실린 대상 DB를 기록한다."""

    def __init__(self) -> None:
        self.executed: list[str] | None = None

    async def record(self, node_state, *args, **kwargs):
        self.executed = [t["db_id"] for t in node_state["target_databases"]]
        return {}


@asynccontextmanager
async def _patched_pipeline(classified: list[str]):
    spy = _PipelineSpy()
    classify = AsyncMock(return_value=[_target(d) for d in classified])
    with patch.object(subagents, "_run_single_db_pipeline", AsyncMock(side_effect=spy.record)), \
         patch.object(subagents, "multi_db_executor", AsyncMock(side_effect=spy.record)), \
         patch.object(subagents, "result_merger", AsyncMock(return_value={})), \
         patch.object(subagents, "result_organizer", AsyncMock(return_value={})), \
         patch.object(subagents, "classify_dbs", classify):
        yield spy, classify


async def _run_pipeline(
    allowed, role=USER, *, classified=("db_a", "db_b"), task_extra=None, isolated_extra=None,
):
    """1·2단 공통 합류점(`run_data_query_pipeline`)을 대역으로 태운다.

    Returns:
        `(task 결과, 조회에 실린 db_id 목록 또는 None, 분류 호출 여부)`
    """
    task = {"task_id": "t1", "agent": "data_query", "sub_query": "서버 목록", **(task_extra or {})}
    state = {
        "user_query": "서버 목록 보여줘",
        "allowed_db_ids": allowed,
        "user_role": role,
        **(isolated_extra or {}),
    }
    isolated = subagents._make_isolated_input(task, state, {})
    async with _patched_pipeline(list(classified)) as (spy, classify):
        result = await subagents.run_data_query_pipeline(
            task, isolated, llm=AsyncMock(), app_config=_config()
        )
    return result, spy.executed, classify.called


def _assert_denied(result: dict) -> None:
    assert result.get("final_response") == ACCESS_DENIED_MESSAGE
    assert result.get("routing_intent") == ACCESS_DENIED_INTENT
    # 거부된 DB가 다음 턴 승계(previous_db_ids)로 새지 않는다
    assert not result.get("target_db_ids")


# ──────────────────────────────────────────────
# ①② 2단 핸들러 — 대상 DB 산출
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_allowed_list_is_denied_without_querying():
    """★ 결함 ② 재현 — 빈 목록 계정은 어떤 DB도 조회하지 않고 분류(LLM)도 부르지 않는다."""
    result, executed, classified = await _run_pipeline([])
    assert executed is None, f"조회가 실행됐다: {executed}"
    assert classified is False
    _assert_denied(result)


@pytest.mark.asyncio
async def test_partial_allowed_list_keeps_only_the_intersection():
    """부분 허용 계정은 분류 결과와 허용 목록의 교집합만 조회한다."""
    result, executed, _ = await _run_pipeline(["db_a"], classified=("db_a", "db_b"))
    assert executed == ["db_a"]
    assert result.get("target_db_ids") == ["db_a"]


@pytest.mark.asyncio
async def test_partial_allowed_list_is_denied_when_no_target_is_authorized():
    """교집합이 비면 조회하지 않고 사유를 돌려준다(빈 대상으로 계속 보내지 않는다)."""
    result, executed, _ = await _run_pipeline(["db_a"], classified=("db_c",))
    assert executed is None
    _assert_denied(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fixed, expected",
    [(["db_a", "db_c"], ["db_a"]), (["db_c"], None)],
    ids=["partly-authorized", "unauthorized"],
)
async def test_planner_fixed_db_ids_are_filtered(fixed, expected):
    """계획이 고정한 DB(`task.db_ids` — 양식 `mapped_db_ids`·소유 고정)도 같은 필터를 지난다."""
    result, executed, classified = await _run_pipeline(["db_a"], task_extra={"db_ids": fixed})
    assert classified is False
    assert executed == expected
    if expected is None:
        _assert_denied(result)


@pytest.mark.asyncio
async def test_zone_selection_is_intersected_with_allowed_list():
    """존 선택·스코프 칩(`selected_db_ids`)도 교집합만 남는다.

    요청 경계(`apply_selection_authorization`)가 1차로 거르지만, 체크포인트 복원 등 경계 밖
    유입도 조회 직전에 한 번 더 막는다.
    """
    _, executed, _ = await _run_pipeline(
        ["db_a"], isolated_extra={"selected_db_ids": ["db_a", "db_c"]}
    )
    assert executed == ["db_a"]


@pytest.mark.asyncio
async def test_inherited_unauthorized_db_is_denied():
    """직전 턴 DB 승계가 권한 밖 DB(권한 회수 등)를 되살리지 않는다."""
    result, executed, _ = await _run_pipeline(
        ["db_a"],
        classified=("db_a",),
        isolated_extra={"conversation_context": {"previous_db_ids": ["db_c"]}},
    )
    assert executed is None
    _assert_denied(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed, role",
    [(None, USER), ([], "admin"), (["db_b"], "admin")],
    ids=["unset", "admin-empty", "admin-listed"],
)
async def test_unrestricted_users_are_unchanged(allowed, role):
    """미설정(None)·관리자는 종전과 같다 — 분류 결과 전체를 조회한다(D-232 ①⑤)."""
    _, executed, _ = await _run_pipeline(allowed, role, classified=("db_a", "db_b"))
    assert executed == ["db_a", "db_b"]


_ZONES = ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]


def _zone_gate(allowed, role=USER):
    """1·2단 존 역질문 후단 게이트 — 위치어 없는 첫 턴이 폴스타 3존으로 팬아웃된 상황."""
    config = SimpleNamespace(
        multi_db=SimpleNamespace(get_active_db_ids=lambda: list(_ZONES)),
        get_polestar_db_ids=lambda: set(_ZONES),
    )
    isolated = {
        "zone_clarification_allowed": True,
        "original_user_query": "OS 종류를 확인하시오",
        "parsed_requirements": {"query_targets": ["os_type"], "filter_conditions": []},
        "allowed_db_ids": allowed,
        "user_role": role,
    }
    return subagents._zone_clarification_or_none_task(
        {"task_id": "t1", "agent": "data_query", "sub_query": "OS 종류 확인"},
        isolated, [_target(d) for d in _ZONES],
        db_pinned=False, db_succeeded=False, app_config=config,
    )


def test_zone_question_offers_only_authorized_zones():
    """존 선택지는 권한 내 존만 — 3단 라우터·라우트 사전 게이트와 같은 규칙(plans/116 §10.3)."""
    payload = _zone_gate(["polestar_cm_gp"])
    assert payload is not None
    assert [o["db_id"] for o in payload["options"]] == ["polestar_cm_gp"]


def test_zone_question_is_skipped_without_authorized_zone():
    """권한 내 존이 없으면 묻지 않는다 — 뒤이은 인가 필터가 사유를 알린다(막다른 선택지 금지)."""
    assert _zone_gate(["db_a"]) is None


@pytest.mark.parametrize("allowed, role", [(None, USER), ([], "admin")], ids=["unset", "admin"])
def test_zone_question_unchanged_for_unrestricted_users(allowed, role):
    payload = _zone_gate(allowed, role)
    assert [o["db_id"] for o in payload["options"]] == _ZONES


# ──────────────────────────────────────────────
# ④ 2단 한 턴 — 오케스트레이터 → 재계획 → 집계
# ──────────────────────────────────────────────


def _replanner_llm_wanting_retry() -> AsyncMock:
    """재계획 LLM 대역 — 부르면 같은 조회를 다시 하자고 답한다."""
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps({
        "needs_followup": True,
        "reason": "데이터 미확보",
        "new_tasks": [{"agent": "data_query", "sub_query": "서버 목록 다시 조회"}],
    }, ensure_ascii=False))
    return llm


@pytest.mark.asyncio
async def test_tier2_turn_ends_with_the_tier3_denial_message(mock_config):
    """빈 목록 계정의 2단 한 턴이 3단과 같은 문구로 끝난다 — 조회 0 · 재계획 LLM 0 · DB 승격 0."""
    state = create_initial_state(
        user_query="서버 목록 보여줘", user_role=USER, allowed_db_ids=[],
    )
    state["task_plan"] = [{
        "task_id": "t1", "agent": "data_query", "sub_query": "서버 목록",
        "depends_on": [], "input_from": [], "order": 1, "status": "pending",
    }]
    state["task_results"] = {}
    state["replan_count"] = 0
    replan_llm = _replanner_llm_wanting_retry()

    async with _patched_pipeline(["db_a", "db_b"]) as (spy, _):
        state.update(await agent_orchestrator(state, llm=AsyncMock(), app_config=mock_config))
        decision = await replanner(state, llm=replan_llm, app_config=mock_config)
        state.update(decision)
        out = await result_aggregator(
            state, llm=AsyncMock(), app_config=mock_config, synthesize=True
        )

    assert spy.executed is None, f"조회가 실행됐다: {spy.executed}"
    assert decision["needs_replan"] is False
    replan_llm.ainvoke.assert_not_awaited()
    assert out["final_response"] == ACCESS_DENIED_MESSAGE
    assert "active_db_id" not in out and "target_databases" not in out


# ──────────────────────────────────────────────
# ⑤ 1단 도구 경로 · 프로세스 조회 · 호스트 조사
# ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tier1_tool_call_is_denied_for_empty_allowed_list():
    """1단(deepagents) 도구도 같은 핸들러를 지나 거부된다 — 도구 결과에 사유가 실린다."""
    collector: list = []
    async with _patched_pipeline(["db_a"]) as (spy, _):
        text = await _run_subagent_tool(
            "data_query", "서버 목록",
            worker_llm=AsyncMock(), app_config=_config(),
            ambient_state={"allowed_db_ids": [], "user_role": USER},
            collector=collector,
        )
    assert spy.executed is None
    assert ACCESS_DENIED_MESSAGE in text
    _assert_denied(collector[-1][1])


class _ProcCfg:
    process_top_n = 5

    def __init__(self, mapping: dict) -> None:
        self._m = mapping

    def get_process_api_base_url(self, db_id):
        return self._m.get(db_id)


def _process_config() -> SimpleNamespace:
    return SimpleNamespace(
        alarm=_ProcCfg({"db_a": "http://a", "db_b": "http://b"}),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["db_a", "db_b"]),
    )


def _process_spy(monkeypatch) -> dict:
    """프로세스 API·호스트명 해소 대역 — API가 불렸는지 기록한다."""
    from noise_gate.infrastructure.polestar_hostname_resolver import HostLookup
    from noise_gate.infrastructure.polestar_process_api import ProcessApiResult
    from src.domain.host_availability import judge_availability

    seen: dict = {"called_db_ids": []}

    async def _fake_resolve(self, db_id, value):
        return HostLookup(value, value, judge_availability(avail_status=0))

    async def _fake_list(self, db_id, hostname):
        seen["called_db_ids"].append(db_id)
        return ProcessApiResult(captured_at=None, processes=[])

    monkeypatch.setattr(
        "noise_gate.infrastructure.polestar_hostname_resolver."
        "PolestarHostnameResolver.resolve_with_status", _fake_resolve,
    )
    monkeypatch.setattr(
        "noise_gate.infrastructure.polestar_process_api."
        "PolestarProcessApiClient.list_by_hostname", _fake_list,
    )
    return seen


def _process_isolated(allowed, previous_db: str) -> dict:
    return {
        "allowed_db_ids": allowed,
        "user_role": USER,
        "conversation_context": {
            "previous_db_ids": [previous_db],
            "previous_entities": [{"field": "hostname", "value": "svweb001"}],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed, previous_db",
    [([], "db_a"), (["db_b"], "db_a")],
    ids=["empty-list", "unauthorized-zone"],
)
async def test_process_query_denied_without_calling_the_api(monkeypatch, allowed, previous_db):
    """프로세스 조회(폴스타 프로세스 API — 존 단위)도 인가 밖 존을 부르지 않는다."""
    seen = _process_spy(monkeypatch)
    result = await run_process_query(
        {"sub_query": "해당 서버 현재 프로세스"},
        _process_isolated(allowed, previous_db), llm=None, app_config=_process_config(),
    )
    assert seen["called_db_ids"] == []
    _assert_denied(result)


@pytest.mark.asyncio
async def test_process_query_authorized_zone_still_calls_the_api(monkeypatch):
    """허용된 존은 종전대로 조회한다(회귀 방지)."""
    seen = _process_spy(monkeypatch)
    result = await run_process_query(
        {"sub_query": "해당 서버 현재 프로세스"},
        _process_isolated(["db_a"], "db_a"), llm=None, app_config=_process_config(),
    )
    assert seen["called_db_ids"] == ["db_a"]
    assert result.get("routing_intent") != ACCESS_DENIED_INTENT


def _inspect_config() -> SimpleNamespace:
    return SimpleNamespace(
        composite=SimpleNamespace(investigation_enabled=True),
        alarm=_ProcCfg({}),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["db_a", "db_b"]),
        dbhub=SimpleNamespace(source_name="db_default"),
    )


def _inspect_spy(monkeypatch) -> dict:
    """`get_db_client` 대역 — 어느 db_id로 붙었는지 기록한다."""
    seen: dict = {"db_ids": []}

    class _Client:
        async def inspect_host(self, **kwargs):
            return {"rows": [], "row_count": 0}

    @asynccontextmanager
    async def _fake_client(config, *, db_id=None):
        seen["db_ids"].append(db_id)
        yield _Client()

    monkeypatch.setattr("src.orchestration.host_inspect.get_db_client", _fake_client)
    return seen


def _inspect_isolated(allowed, previous_db: str | None) -> dict:
    ctx = {"previous_db_ids": [previous_db]} if previous_db else {}
    return {
        "allowed_db_ids": allowed,
        "user_role": USER,
        "parsed_requirements": {"filter_conditions": [{"field": "hostname", "value": "svweb001"}]},
        "conversation_context": ctx,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed, previous_db",
    [([], "db_a"), (["db_b"], "db_a"), (["db_a"], None)],
    ids=["empty-list", "unauthorized-zone", "default-source-unauthorized"],
)
async def test_host_inspect_denied_without_connecting(monkeypatch, allowed, previous_db):
    """호스트 조사도 인가 밖 DB에 붙지 않는다 — 대상 존 미확정이면 기본 소스로 판정한다."""
    seen = _inspect_spy(monkeypatch)
    result = await run_host_inspect(
        {"sub_query": "svweb001 OS 정보 보여줘"},
        _inspect_isolated(allowed, previous_db), llm=None, app_config=_inspect_config(),
    )
    assert seen["db_ids"] == []
    _assert_denied(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "allowed, previous_db, expected",
    [(["db_a"], "db_a", "db_a"), (["db_default"], None, None), (None, None, None)],
    ids=["authorized-zone", "authorized-default-source", "unset"],
)
async def test_host_inspect_authorized_target_still_connects(
    monkeypatch, allowed, previous_db, expected
):
    """허용된 존·기본 소스·미설정 사용자는 종전대로 조사한다(회귀 방지)."""
    seen = _inspect_spy(monkeypatch)
    result = await run_host_inspect(
        {"sub_query": "svweb001 OS 정보 보여줘"},
        _inspect_isolated(allowed, previous_db), llm=None, app_config=_inspect_config(),
    )
    assert seen["db_ids"] == [expected]
    assert result.get("routing_intent") != ACCESS_DENIED_INTENT
