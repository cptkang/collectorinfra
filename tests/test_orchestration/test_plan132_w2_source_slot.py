"""plans/132 W2 — 소스 선별 정식화: 답변 영역 칸 · 영역 소유 판정 · 소스 선택 칩 (LLM 0회).

- N-5 분해 출력 `areas`·`requested_source` 칸(프롬프트 골격 · 정제 · JSON 경로 보존)
- N-6 영역의 정본 소유 시스템으로 소스를 판정(등록 전체 — G-7 (a)) — **C2-2 재현**: 제니퍼 미연결 +
  명시 없는 「WAS 인스턴스 목록」이 폴스타 SQL로 가지 않고 안내만 한다
- N-10 모호할 때만 소스 선택 칩(존 역질문 위젯 · 권한 안 활성 후보만) · 스레드 선택 기억 ·
  답변 턴 고정
- 소스 슬롯 판정은 `plans/106` H1 자리(`src/domain/clarification_policy.py` — G-8 (a))
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from src.domain.clarification_policy import (
    ASK,
    KEEP,
    NOTICE,
    PIN,
    SourceOwner,
    decide_source_slot,
)
from src.orchestration import conditional_agents as ca
from src.orchestration import subagents
from src.prompts.intent_planner import (
    INTENT_PLANNER_AREAS_SECTION,
    INTENT_PLANNER_SYSTEM_TEMPLATE,
    render_intent_planner_areas_template,
)
from src.routing.registry import get_registry
from src.state import create_followup_input, create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")


def _cfg(active: list[str] | None = None, *, zone_exclusive: bool = False) -> SimpleNamespace:
    ids = ["polestar", "itam"] if active is None else active
    return SimpleNamespace(multi_db=SimpleNamespace(
        get_active_db_ids=lambda: list(ids), zone_group_exclusive=zone_exclusive))


@pytest.fixture
def systems(monkeypatch):
    """활성 비DB 시스템(시스템 → 처리기)을 테스트마다 정한다."""
    state: dict[str, dict[str, str]] = {"value": {}}
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _cfg: dict(state["value"]))

    def set_active(**pairs: str) -> None:
        state["value"] = dict(pairs)

    return set_active


def _task(tid: str, agent: str, sub_query: str, **extra: Any) -> dict[str, Any]:
    return {"task_id": tid, "agent": agent, "sub_query": sub_query, "depends_on": [],
            "input_from": [], "order": int(tid[1:]), "status": "pending", **extra}


def _state(**extra: Any) -> dict[str, Any]:
    return {"parsed_requirements": {"target_db_hints": []}, "allowed_db_ids": None,
            "allowed_sources": None, "user_role": "user", "zone_clarification_allowed": True,
            "user_query": "WAS 인스턴스 목록 보여줘", **extra}


# ── 소스 슬롯 판정(순수) ──────────────────────────────────────────────────────

def _o(system: str, *, non_db: bool = False, active: bool = True, allowed: bool = True):
    return SourceOwner(system, non_db=non_db, active=active, allowed=allowed)


def test_slot_single_owner() -> None:
    assert decide_source_slot([]).action == KEEP
    pin = decide_source_slot([_o("apm", non_db=True)])
    assert (pin.action, pin.system) == (PIN, "apm")
    off = decide_source_slot([_o("apm", non_db=True, active=False)])
    assert (off.action, off.unavailable) == (NOTICE, ("apm",))
    assert decide_source_slot([_o("polestar")]).action == KEEP
    assert decide_source_slot([_o("itam", active=False)]).action == NOTICE


def test_slot_multiple_owners() -> None:
    apm, pol = _o("apm", non_db=True), _o("polestar")
    ask = decide_source_slot([apm, pol])
    assert (ask.action, ask.candidates) == (ASK, ("apm", "polestar"))
    # 기억이 후보를 가리키면 묻지 않는다(판정 순서: 단독 소유 > 기억 > 칩)
    assert decide_source_slot([apm, pol], remembered=["polestar"]).action == KEEP
    assert decide_source_slot([apm, pol], remembered=["apm"]).action == PIN
    # 권한 안 후보가 하나뿐이면 묻지 않는다(선택지 하나짜리 질문 금지)
    one = decide_source_slot([_o("apm", non_db=True, allowed=False), pol])
    assert (one.action, one.system) == (KEEP, "polestar")
    # 비DB 소유자가 비활성이면 활성 소유자로 가되 조회 못 한 소유자를 돌려준다(사유 노트)
    part = decide_source_slot([_o("apm", non_db=True, active=False), pol])
    assert (part.action, part.system, part.unavailable) == (KEEP, "polestar", ("apm",))
    assert decide_source_slot([_o("apm", non_db=True, active=False),
                               _o("polestar", active=False)]).action == NOTICE
    # DB 시스템끼리는 종전 SQL 분류(교차 조회 — plans/102)
    db_only = decide_source_slot([pol, _o("itam", active=False)])
    assert (db_only.action, db_only.unavailable) == (KEEP, ("itam",))


# ── N-5 분해 출력 칸 ──────────────────────────────────────────────────────────

def test_areas_prompt_lists_every_registered_area_without_system_names() -> None:
    rows = ip._area_rows()
    rendered = render_intent_planner_areas_template(INTENT_PLANNER_SYSTEM_TEMPLATE, rows)
    for spec in get_registry().capability_specs():
        assert f"| {spec.code} |" in rendered, spec.code
    assert '"areas": ["server_status"], "requested_source": ""' in rendered
    assert "두 키를 **항상** 적습니다" in rendered
    # 영역 표는 소스 이름을 렌더하지 않는다(비활성 소스 비노출 — D-283 ②)
    assert "제니퍼" not in rendered and "apm_query" not in rendered
    # 삽입 전용 — 절·골격 키·규칙 한 줄을 빼면 원본이다
    restored = (rendered.replace(INTENT_PLANNER_AREAS_SECTION.replace("<area_rows>", rows), "")
                .replace(',\n         "areas": ["server_status"], "requested_source": ""}}', "}}")
                .replace("- 모든 task에 `areas`·`requested_source` 두 키를 **항상** 적습니다"
                         " — 아래 예시들은 분해 모양만 보여 줍니다.\n", ""))
    assert restored == INTENT_PLANNER_SYSTEM_TEMPLATE


def test_planner_prompt_has_areas_but_no_inactive_handler(mock_config) -> None:
    prompt = ip._planner_system_prompt(mock_config)
    assert "## 답변 영역(areas)" in prompt and "| was_instance |" in prompt
    assert "apm_query" not in prompt, "APM 비활성 배포는 처리기 줄·보기 절 바이트 불변(D-283 ②)"


def test_sanitize_task_areas() -> None:
    result = {"tasks": [
        {"task_id": "t1", "areas": ["was_instance", "nope", "was_instance"],
         "requested_source": "  제니퍼  "},
        {"task_id": "t2", "areas": "server_usage", "requested_source": None},
        {"task_id": "t3"},
    ]}
    ip._sanitize_task_areas(result)
    t1, t2, t3 = result["tasks"]
    assert t1["areas"] == ["was_instance"] and t1["requested_source"] == "제니퍼"
    assert t2["areas"] == ["server_usage"] and t2["requested_source"] == ""
    assert t3["areas"] == [] and t3["requested_source"] == ""


@pytest.mark.asyncio
async def test_json_decompose_keeps_area_fields(mock_config) -> None:
    content = ('{"tasks": [{"task_id": "t1", "agent": "data_query", '
               '"sub_query": "WAS 인스턴스 목록",'
               ' "areas": ["was_instance"], "requested_source": ""}]}')
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=content)))
    out = await ip._llm_decompose(llm, "WAS 인스턴스 목록 보여줘", mock_config)
    assert out["tasks"][0]["areas"] == ["was_instance"]


# ── N-6 영역 소유 판정 · C2-2 ─────────────────────────────────────────────────

def test_c2_2_was_list_with_apm_off_is_notice_not_polestar(systems) -> None:
    """실측 C2-2 재현 — 제니퍼 미연결 + 명시 없는 「WAS 인스턴스 목록」은 폴스타 SQL 0 · 안내
    1건."""
    systems()
    tasks = [_task("t1", "data_query", "WAS 인스턴스 목록 조회", areas=["was_instance"])]
    sel = ca.apply_source_selection(tasks, _state(), _cfg())
    t1 = tasks[0]
    assert t1["agent"] == "general_inference" and "연결되어 있지 않" in t1["direct_response"]
    assert "WAS 인스턴스 목록" in t1["direct_response"] and "제니퍼" not in t1["direct_response"]
    assert t1[ca.SOURCE_NOTICE_KEY] == ["apm"] and sel.turn_sources == ["apm"]


def test_was_list_with_apm_on_pins_handler_without_question(systems) -> None:
    """R-7 — WAS 영역은 제니퍼 단독 소유: 묻지 않고 제니퍼(질문 0)."""
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스 목록 조회", areas=["was_instance"],
                   views=["x"])]
    sel = ca.apply_source_selection(tasks, _state(), _cfg())
    assert tasks[0]["agent"] == "apm_query" and tasks[0]["views"] == []
    assert ca.SOURCE_CLARIFICATION_KEY not in tasks[0] and sel.notes == []


def test_polestar_and_untagged_tasks_unchanged(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "CPU 상위 10", areas=["server_usage"]),
             _task("t2", "alarm_query", "심각 알람", areas=["alarm"]),
             _task("t3", "data_query", "서버 목록", areas=[]),
             _task("t4", "data_query", "서버 목록")]
    before = [dict(t) for t in tasks]
    sel = ca.apply_source_selection(tasks, _state(), _cfg())
    assert tasks == before and sel.changed == [] and sel.turn_sources == []


def test_inactive_db_owner_is_notice(systems) -> None:
    """운영 모양(자산관리 미활성) — 「자산 담당자」를 폴스타로 대신 답하지 않는다."""
    systems()
    tasks = [_task("t1", "data_query", "자산 담당자 목록", areas=["asset_owner"])]
    ca.apply_source_selection(tasks, _state(), _cfg(["polestar"]))
    assert tasks[0]["agent"] == "general_inference"
    assert tasks[0][ca.SOURCE_NOTICE_KEY] == ["itam"]


def test_partial_inactive_owner_keeps_active_and_notes(systems) -> None:
    systems()
    tasks = [_task("t1", "data_query", "web01 CPU와 WAS 힙", areas=["server_usage", "was_runtime"])]
    sel = ca.apply_source_selection(tasks, _state(), _cfg())
    assert tasks[0]["agent"] == "data_query"
    assert [n["reason"] for n in sel.notes] == ["source_inactive"]
    assert "JVM" in sel.notes[0]["detail"]


def test_explicit_mention_wins_over_area(systems) -> None:
    """명시 소스 > 영역 — ITAM을 지목한 task는 영역 판정을 하지 않는다."""
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "ITAM에서 서버 사용률", areas=["was_runtime"])]
    ca.apply_source_selection(tasks, _state(parsed_requirements={"target_db_hints": ["ITAM"]}),
                              _cfg())
    assert tasks[0]["agent"] == "data_query"


def test_requested_source_attributes_mention_in_composite(systems) -> None:
    """복합 계획 — task 질의에 이름이 없어도 `requested_source`가 이 턴 지목과 맞으면 귀속한다."""
    systems()
    tasks = [_task("t1", "alarm_query", "심각 알람 서버", areas=["alarm"]),
             _task("t2", "data_query", "선행 서버들의 응답시간", areas=["server_usage"],
                   requested_source="제니퍼", depends_on=["t1"], input_from=["t1"])]
    ca.apply_source_selection(tasks, _state(parsed_requirements={"target_db_hints": ["제니퍼"]}),
                              _cfg())
    assert tasks[0]["agent"] == "alarm_query"
    assert tasks[1]["agent"] == "general_inference" and "「제니퍼」" in tasks[1]["direct_response"]


def test_requested_source_without_turn_mention_is_ignored(systems) -> None:
    """LLM이 추측한 이름으로 소스를 만들지 않는다(D-004) — 이 턴 결정적 지목이 없으면 무시."""
    systems()
    tasks = [_task("t1", "data_query", "CPU 상위", areas=["server_usage"],
                   requested_source="제니퍼")]
    ca.apply_source_selection(tasks, _state(), _cfg())
    assert tasks[0]["agent"] == "data_query"


# ── N-10 소스 선택 칩 ─────────────────────────────────────────────────────────

def test_ambiguous_task_becomes_source_chip(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스 목록",
                   areas=["was_instance", "host_location"])]
    sel = ca.apply_source_selection(
        tasks, _state(), _cfg(["polestar_b0", "polestar_cm_gp", "polestar_cm_yd", "polestar"]))
    t1 = tasks[0]
    payload = t1[ca.SOURCE_CLARIFICATION_KEY]
    assert t1["agent"] == "general_inference" and t1["direct_response"] == payload["question"]
    assert payload["kind"] == ca.SOURCE_SELECT_KIND and payload["group_exclusive"] is True
    assert payload["original_query"] == "WAS 인스턴스 목록 보여줘"
    options = payload["options"]
    assert options[0] == {"source": "apm", "label": get_registry().system_label("apm"),
                          "group": "apm"}
    zone_rows = [o for o in options if o.get("db_id")]
    assert [o["db_id"] for o in zone_rows] == ["polestar_b0", "polestar_cm_gp", "polestar_cm_yd"]
    assert {o["group"] for o in zone_rows} == {"polestar"}, "존 동시 선택 개방(D-206) — 한 그룹"
    assert options[-1]["db_ids"] == ["polestar"], "존 없는 같은 시스템 DB는 한 선택지"
    assert t1[ca.SOURCE_SLOT_KEY]["from_agent"] == "data_query" and sel.changed == ["t1"]


def test_chip_zone_groups_split_when_exclusive(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스", areas=["was_instance", "host_location"])]
    ca.apply_source_selection(tasks, _state(),
                              _cfg(["polestar_b0", "polestar_cm_gp"], zone_exclusive=True))
    groups = [o["group"] for o in tasks[0][ca.SOURCE_CLARIFICATION_KEY]["options"]]
    assert groups == ["apm", "bank", "common"]


def test_chip_hides_unauthorized_source_and_does_not_ask(systems) -> None:
    """권한 밖 소스는 선택지에 없다(D-264 ②) — 남은 후보가 하나면 묻지 않는다."""
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스", areas=["was_instance", "host_location"])]
    ca.apply_source_selection(tasks, _state(allowed_sources=[]), _cfg())
    assert tasks[0]["agent"] == "data_query" and ca.SOURCE_CLARIFICATION_KEY not in tasks[0]


def test_non_interactive_channel_does_not_ask(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스", areas=["was_instance", "host_location"])]
    sel = ca.apply_source_selection(tasks, _state(zone_clarification_allowed=False), _cfg())
    assert tasks[0]["agent"] == "apm_query"
    assert [n["reason"] for n in sel.notes] == ["source_slot_default"]


def test_thread_choice_is_reused_without_asking(systems) -> None:
    systems(apm="apm_query")
    tasks = [_task("t1", "data_query", "WAS 인스턴스", areas=["was_instance", "host_location"])]
    ca.apply_source_selection(tasks, _state(), _cfg(), remembered=["polestar"])
    assert tasks[0]["agent"] == "data_query" and ca.SOURCE_CLARIFICATION_KEY not in tasks[0]


@pytest.mark.asyncio
async def test_general_inference_carries_chip_payload() -> None:
    payload = {"kind": "source_select", "question": "q", "options": []}
    out = await subagents.run_general_inference(
        {"task_id": "t1", "agent": "general_inference", "direct_response": "q",
         "source_clarification": payload},
        {}, llm=None, app_config=None)
    assert out["zone_clarification"] is payload and out["final_response"] == "q"


# ── 계획 출구 배선 · 칩 답변 턴 ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_planner_exit_writes_turn_sources_and_notes(monkeypatch, systems) -> None:
    systems()
    plan = {"task_plan": [_task("t1", "data_query", "WAS 인스턴스 목록", areas=["was_instance"])],
            "is_composite": False, "current_node": "intent_planner"}
    monkeypatch.setattr(ip, "_plan_turn", AsyncMock(return_value=plan))
    state = create_initial_state(user_query="WAS 인스턴스 목록 보여줘")
    out = await ip.intent_planner(state, llm=None, app_config=_cfg())
    assert out["task_plan"][0]["agent"] == "general_inference"
    assert out["turn_sources"] == ["apm"] and "source_choice" not in out


@pytest.mark.asyncio
async def test_chip_answer_turn_pins_chosen_handler(monkeypatch) -> None:
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _c: {"apm": "apm_query"})
    state = create_initial_state(user_query="WAS 인스턴스 목록 보여줘", selected_sources=["apm"])
    plan = ip._source_reentry_plan(state, _cfg(), state["user_query"])
    assert plan["task_plan"][0]["agent"] == "apm_query" and plan["plan_path"] == "source_reentry"
    result: dict[str, Any] = {}
    await ip._record_source_choice(result, state, _cfg())
    assert result == {"source_choice": {"system": "apm"}}


def test_chip_answer_turn_restores_composite_plan(monkeypatch) -> None:
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _c: {"apm": "apm_query"})
    snapshot = {"tasks": [
        {"task_id": "t1", "agent": "alarm_query", "sub_query": "심각 알람 서버", "depends_on": [],
         "input_from": [], "order": 1},
        {"task_id": "t2", "agent": "general_inference", "sub_query": "그 서버 WAS 인스턴스",
         "depends_on": ["t1"], "input_from": ["t1"], "order": 2,
         "source_slot": {"from_agent": "data_query"}},
    ], "gated_task_ids": ["t2"]}
    state = create_initial_state(user_query="q", selected_sources=["apm"])
    state["reuse_task_plan"] = snapshot
    plan = ip._source_reentry_plan(state, _cfg(), "q")
    agents = [t["agent"] for t in plan["task_plan"]]
    assert agents == ["alarm_query", "apm_query"] and plan["plan_path"] == "source_reentry_restored"
    assert "source_slot" not in plan["task_plan"][1]
    # DB 소스를 고른 답변(존 선택과 같은 ②.5 복원)은 칩 전 담당 + 고른 DB
    state = create_initial_state(user_query="q", selected_db_ids=["polestar"])
    state["reuse_task_plan"] = snapshot
    restored = ip._restored_zone_reentry_plan(state, ["polestar"])
    assert restored["task_plan"][1]["agent"] == "data_query"
    assert restored["task_plan"][1]["db_ids"] == ["polestar"]


def test_chip_answer_for_inactive_source_is_notice(monkeypatch) -> None:
    monkeypatch.setattr(ca, "active_conditional_systems", lambda _c: {})
    state = create_initial_state(user_query="q", selected_sources=["apm"])
    plan = ip._source_reentry_plan(state, _cfg(), "q")
    assert plan["task_plan"][0]["agent"] == "general_inference"
    assert plan["task_plan"][0]["source_notice"] == ["apm"]


# ── 상태 · 라우트 · 맥락 ──────────────────────────────────────────────────────

def test_state_scopes() -> None:
    delta = create_followup_input("q", selected_sources=["apm"])
    assert delta["selected_sources"] == ["apm"] and "source_choice" not in delta
    assert create_followup_input("q")["selected_sources"] is None, "요청 스코프 — 매 턴 재공급"
    assert create_followup_input("q", reset_db_scope=True)["source_choice"] is None


def test_route_filters_selected_sources() -> None:
    from src.api.routes.query import apply_source_selection_authorization as authz

    user = {"allowed_sources": ["apm"], "role": "user"}
    assert authz(["apm", "nope", "polestar"], user) == ["apm", "polestar"]
    assert authz(["apm"], {"allowed_sources": [], "role": "user"}) is None
    assert authz(["doc"], {"allowed_sources": None, "role": "user"}) == ["doc"]
    assert authz(None, user) is None


def test_chip_answer_reuses_parse_like_zone_answer() -> None:
    from src.api.routes.query import _zone_answer_parse_reuse
    from src.api.schemas import QueryRequest

    checkpoint = {"zone_clarification": {"kind": "source_select", "original_query": "q"},
                  "parsed_requirements": {"query_targets": ["인스턴스"]}}
    body = QueryRequest(query="q", selected_sources=["apm"])
    assert _zone_answer_parse_reuse(body, checkpoint) == {"query_targets": ["인스턴스"]}


@pytest.mark.asyncio
async def test_context_resolver_carries_previous_sources() -> None:
    from langchain_core.messages import AIMessage, HumanMessage

    from src.nodes.context_resolver import context_resolver

    state = create_initial_state(user_query="두 번째")
    state["messages"] = [HumanMessage("첫"), AIMessage("답"), HumanMessage("두 번째")]
    state["turn_sources"] = ["apm"]
    ctx = (await context_resolver(state))["conversation_context"]
    assert ctx["previous_sources"] == ["apm"]
    state["db_scope_reset"] = True
    assert (await context_resolver(state))["conversation_context"]["previous_sources"] == []
