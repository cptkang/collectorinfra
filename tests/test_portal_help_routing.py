"""plans/142 W3 — 포탈 도움말 처리기 `portal_help` 1·2단 배선 · D-315.

검증(실 LLM·외부 호출 0 — 분해 LLM 은 목, 서술 LLM 은 서비스 함수 대역):
- 활성 판정(설정 스위치 ∧ 색인) · 레지스트리 안내 항목의 부작용 차단(G-4)
- 명시 명령 ③.9(공용 지목 판정 · 데이터 요청 보류 · 비활성 안내 · 단락 순서)
- 분해 프롬프트 절(활성일 때만 · 비활성 바이트 불변 = 삽입만)
- 되돌림(G-5 (b)) 경계 — 분해 출구(2단) · handler 수렴점(1·2단)
- 1단 도구 목록·지침(G-8 (b))

매뉴얼 색인은 저장소 산출물(`src/static/manual/help_index.json`)을 그대로 읽는다 — 되돌림 판정
재료가 실 색인이기 때문이다.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig, PortalHelpConfig
from src.doc_qa.portal_help import PortalHelpResult, help_index_status
from src.orchestration import portal_help as ph
from src.orchestration.conditional_agents import (
    SOURCE_NOTICE_KEY,
    active_conditional_agents,
    active_conditional_systems,
    sanitize_task_views,
)
from src.orchestration.doc_command import parse_doc_command
from src.prompts.intent_planner import INTENT_PLANNER_PORTAL_SECTION
from src.prompts.orchestrator import (
    ORCHESTRATOR_INSTRUCTIONS,
    PORTAL_HELP_RULE,
    PORTAL_HELP_TOOL_LINE,
    render_orchestrator_instructions,
)
from src.routing.registry import get_registry
from src.routing.source_hints import resolve_source_mentions
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")

# W0 실측 문항(plans/142 §11.1)
Q_SYNONYM_HOWTO = "itam db에 유사어를 등록하려고 한다. 어떻게 해야 되냐?"
Q_RECEIVED_ALARM = "노이즈 캔슬링의 '수신 알람'은 무엇인지 설명하라."
Q_ALARM_COUNT = "오늘 수신 알람 몇 건이야"

pytestmark = pytest.mark.skipif(not help_index_status()[0], reason="매뉴얼 색인 없음(W1 산출물)")


def _config(*, enabled: bool = True) -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db"),
        portal_help=PortalHelpConfig(_env_file=None, enabled=enabled),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


def _state(query: str, **extra: Any) -> dict:
    state = create_initial_state(user_query=query, thread_id="th-142")
    state.update(extra)
    return state


def _decompose_llm(*tasks: dict) -> AsyncMock:
    """분해 LLM 목 — 주어진 task 목록을 JSON 으로 돌려준다."""
    llm = AsyncMock()
    content = json.dumps({"tasks": list(tasks)}, ensure_ascii=False)
    llm.ainvoke.return_value = MagicMock(content=content)
    return llm


def _task(agent: str, sub_query: str, task_id: str = "t1", **extra: Any) -> dict:
    return {"task_id": task_id, "agent": agent, "sub_query": sub_query, "depends_on": [],
            "input_from": [], "order": int(task_id[1:]), **extra}


class _NoCallLLM(FakeListChatModel):
    """부르면 실패하는 LLM — 결정적 단락이 분해 LLM 을 부르지 않음을 단언한다."""

    async def ainvoke(self, *args, **kwargs):  # noqa: D102
        raise AssertionError("포탈 도움말 명령은 분해 LLM 을 부르지 않는다")


# ── 1. 활성 판정 ────────────────────────────────────────────────────────────

def test_default_is_on() -> None:
    """G-7 (b) — 코드 기본값 on(사용자 확정 2026-10-13)."""
    assert PortalHelpConfig(_env_file=None).enabled is True


def test_active_requires_switch() -> None:
    assert ph.portal_active(_config()) is True
    assert ph.portal_active(_config(enabled=False)) is False
    # 목 설정(속성이 참으로 평가되는 MagicMock)은 켜진 것으로 보지 않는다
    assert ph.portal_active(MagicMock()) is False


def test_conditional_agents_and_systems() -> None:
    assert "portal_help" in active_conditional_agents(_config())
    assert "portal_help" not in active_conditional_agents(_config(enabled=False))
    # 데이터 소스 처리기가 아니다 — 명시 소스 고정·소스 슬롯 대상 시스템에 없다
    assert "portal" not in active_conditional_systems(_config())


def test_sanitize_cuts_edges_of_portal_task() -> None:
    """간선 없음 — portal_help task 로/에서 오는 의존을 끊는다."""
    result = {"tasks": [_task("portal_help", "수신 알람이 뭐야"),
                        _task("alarm_query", "오늘 수신 알람", "t2",
                              depends_on=["t1"], input_from=["t1"])]}
    assert sanitize_task_views(result, _config()) == ["t2"]
    assert result["tasks"][1]["depends_on"] == [] and result["tasks"][1]["input_from"] == []


# ── 2. 레지스트리 안내 항목(G-4) 부작용 차단 ────────────────────────────────

def test_registry_portal_is_help_system_not_data_source() -> None:
    reg = get_registry()
    [portal] = [s for s in reg.help_systems() if s.code == "portal"]
    assert portal.aliases == (
        "포탈 매뉴얼", "사용자 매뉴얼", "관리자 매뉴얼", "AIOps 포탈", "포탈 도움말",
    )
    assert "매뉴얼" not in portal.aliases  # 맨 「매뉴얼」 제외(G-4 (a) — 본부 매뉴얼 충돌)
    assert "portal" not in {s.code for s in reg.solutions()}
    assert "portal" not in {s.code for s in reg.non_db_systems()}
    for alias in portal.aliases:
        assert alias not in reg.source_alias_terms()
        assert alias not in reg.new_db_signal_terms()
    assert reg.system_label("portal") == "AIOps 포탈 매뉴얼"


def test_portal_alias_is_not_source_mention() -> None:
    """포탈 별칭은 소스 지목이 아니다 — 명시 소스 고정·소스 기억·소스 칩이 바뀌지 않는다."""
    assert resolve_source_mentions(["AIOps 포탈", "사용자 매뉴얼", "포탈 도움말"]) == []


# ── 3. 명시 명령 ③.9 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize(("query", "hint", "text"), [
    ("AIOps 포탈에서 수신 알람을 설명해줘", "AIOps 포탈", "수신 알람"),
    ("사용자 매뉴얼에서 유사어 등록 방법 찾아줘", "사용자 매뉴얼", "유사어 등록 방법"),
    ("포탈 도움말로 결과 엑셀 내려받기 검색", "포탈 도움말", "결과 엑셀 내려받기"),
    ("관리자 매뉴얼에서 설정 백업 방법 알려줘", "관리자 매뉴얼", "설정 백업 방법"),
    ("포탈 매뉴얼에서는 노이즈 캔슬링을 설명하시오", "포탈 매뉴얼", "노이즈 캔슬링"),
    ("수신 알람을 포탈 매뉴얼에서 찾아줘", "포탈 매뉴얼", "수신 알람"),
    ("AIOps 포탈에서 유사어 등록 방법 알려줘", "AIOps 포탈", "유사어 등록 방법"),
])
def test_command_matches(query: str, hint: str, text: str) -> None:
    cmd = ph.parse_portal_command(query, _config())
    assert cmd is not None and cmd.active
    assert (cmd.hint, cmd.query) == (hint, text)


@pytest.mark.parametrize("query", [
    "AIOps 포탈에서 CPU 사용률 상위 10건",                    # 명령 동사 없음 — 종전 data_query
    "AIOps 포탈에서 CPU 사용률 상위 10건 조회해줘",           # 「조회」는 포탈 명령 동사가 아니다
    "AIOps 포탈에서 오늘 수신 알람 몇 건인지 알려줘",         # 데이터 요청 표지 — 보류
    "AIOps 포탈에서 서버 목록 알려줘",                        # 데이터 요청 표지 — 보류
    "수신 알람을 AIOps 포탈에서 찾아줘",                      # 문서 지칭 없는 별칭(V-1)
    "AIOps 포탈에서 활성 알람을 알려줘",                      # 같은 이유(V-1) — 종전 분해
    "매뉴얼에서 수신 알람 설명해줘",                          # 맨 「매뉴얼」은 별칭이 아니다
    "AIOps 포탈에서 수신 알람 설명하고 김포에서 CPU 알려줘",  # 다른 소스 지목 동반 — 복합 질의
    "AIOps 포탈이 뭐야?",                                     # 지목 조사 없음
    "AIOps 포탈에서 설명해줘",                                # 지목·명령을 떼면 질의가 빈다
    "본부 매뉴얼에서 백업 담당자의 역할을 찾아줘",            # 문서 소스 지목(②.9) — 포탈 아님
    "",
])
def test_command_does_not_match(query: str) -> None:
    assert ph.parse_portal_command(query, _config()) is None


def test_inactive_command_still_recognised() -> None:
    cmd = ph.parse_portal_command("AIOps 포탈에서 수신 알람을 설명해줘", _config(enabled=False))
    assert cmd is not None and cmd.active is False


def test_doc_command_unchanged_by_shared_parser() -> None:
    """공용 지목 판정 추출 뒤에도 문서 명령(D-307)은 포탈 별칭에 반응하지 않는다."""
    assert parse_doc_command("AIOps 포탈에서 수신 알람을 설명해줘", _config()) is None
    assert parse_doc_command("사용자 매뉴얼에서 로그인 방법 찾아줘", _config()) is None


@pytest.mark.asyncio
async def test_plan_turn_pins_portal_help_without_decompose_llm() -> None:
    plan = await ip._plan_turn(_state("AIOps 포탈에서 수신 알람을 설명해줘"),
                               llm=_NoCallLLM(responses=[]), app_config=_config())
    assert plan[ip.PLAN_PATH_KEY] == ph.PORTAL_COMMAND_PATH
    [task] = plan["task_plan"]
    assert task["agent"] == "portal_help"
    assert task["sub_query"] == "수신 알람"


@pytest.mark.asyncio
async def test_plan_turn_inactive_command_notices_only() -> None:
    plan = await ip._plan_turn(_state("AIOps 포탈에서 수신 알람을 설명해줘"),
                               llm=_NoCallLLM(responses=[]), app_config=_config(enabled=False))
    assert plan[ip.PLAN_PATH_KEY] == ph.PORTAL_COMMAND_PATH
    [task] = plan["task_plan"]
    assert task["agent"] == "general_inference"
    assert "「AIOps 포탈」" in task["direct_response"]
    assert "/manual/user" in task["direct_response"]
    # 포탈은 데이터 소스가 아니다 — 소스 안내 키(턴 소스 기억)를 달지 않는다
    assert SOURCE_NOTICE_KEY not in task


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"template_structure": {"sheets": []}},
    {"uploaded_file": b"x"},
    {"selected_db_ids": ["polestar"]},
    {"selected_sources": ["apm"]},
])
async def test_plan_turn_skips_command_on_answer_turns(extra: dict) -> None:
    """양식·존·소스 선택 답변 턴은 그 턴의 결정적 단락이 우선한다."""
    llm = _decompose_llm(_task("general_inference", "수신 알람"))
    plan = await ip._plan_turn(_state("AIOps 포탈에서 수신 알람을 설명해줘", **extra),
                               llm=llm, app_config=_config())
    assert plan.get(ip.PLAN_PATH_KEY) != ph.PORTAL_COMMAND_PATH


# ── 4. 분해 프롬프트 절 ────────────────────────────────────────────────────

def test_planner_prompt_off_has_no_portal_bytes() -> None:
    off = ip._planner_system_prompt(_config(enabled=False))
    assert "portal_help" not in off
    assert INTENT_PLANNER_PORTAL_SECTION not in off


def test_planner_prompt_on_is_insertion_only() -> None:
    """활성 렌더 = 비활성 렌더 + 담당 줄 1 + 절 1(삽입만 — 나머지 바이트 불변)."""
    off = ip._planner_system_prompt(_config(enabled=False))
    on = ip._planner_system_prompt(_config())
    line = "\n" + ph.render_agent_line()
    assert on.count(line) == 1 and on.count(INTENT_PLANNER_PORTAL_SECTION) == 1
    assert on.replace(line, "", 1).replace(INTENT_PLANNER_PORTAL_SECTION, "", 1) == off
    # 대조 예 4쌍
    for pair in ("「오늘 수신 알람 몇 건」 → `alarm_query`",
                 "「hostname에 '장비이름' 유사어 등록해줘」 → `cache_management`",
                 "「CPU 상위 10건 엑셀로」 → `data_query`",
                 "「쿠버네티스 사용법」 → `general_inference`"):
        assert pair in on


@pytest.mark.asyncio
async def test_decompose_accepts_portal_help_agent() -> None:
    llm = _decompose_llm(_task("portal_help", "수신 알람이 뭐야"))
    plan = await ip._plan_turn(_state("수신 알람이 뭐야"), llm=llm, app_config=_config())
    [task] = plan["task_plan"]
    assert task["agent"] == "portal_help"


# ── 4b. 데이터 요청 출구 가드 — portal_help → data_query(W5 교정) ─────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "agent"), [
    (Q_ALARM_COUNT, "alarm_query"),        # W5 ng-01 — 알람 교정이 마저 바꾼다
    ("CPU 상위 10건 보여줘", "data_query"),
])
async def test_data_request_portal_task_is_coerced(query: str, agent: str) -> None:
    llm = _decompose_llm(_task("portal_help", query))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    [task] = plan["task_plan"]
    assert task["agent"] == agent
    assert task["sub_query"] == query


@pytest.mark.asyncio
async def test_data_request_cue_in_original_only_is_coerced() -> None:
    """분해가 sub_query 에서 표지를 지워도 원문에 표지가 있으면 되돌린다."""
    llm = _decompose_llm(_task("portal_help", "수신 알람 현황"))
    plan = await ip._plan_turn(_state(Q_ALARM_COUNT), llm=llm, app_config=_config())
    assert plan["task_plan"][0]["agent"] == "alarm_query"


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["수신 알람이 뭐야", "유사어 등록은 어떻게 해?"])
async def test_portal_question_keeps_portal_help(query: str) -> None:
    llm = _decompose_llm(_task("portal_help", query))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    [task] = plan["task_plan"]
    assert task["agent"] == "portal_help"


def test_data_request_guard_changes_only_matching_task() -> None:
    tasks = [_task("portal_help", "수신 알람이 뭐야"),
             _task("portal_help", Q_ALARM_COUNT, "t2"),
             _task("data_query", "CPU 상위 10건 보여줘", "t3")]
    ph.coerce_portal_data_request(tasks, "수신 알람이 뭐야, 그리고 그 수")
    assert [t["agent"] for t in tasks] == ["portal_help", "data_query", "data_query"]


@pytest.mark.asyncio
async def test_mixed_query_keeps_portal_task_in_multi_task_plan() -> None:
    """다중 task 계획은 sub_query 로만 판정한다 — 원문 표지가 포탈 질문 task 를 뒤집지 않는다."""
    query = "유사어 등록은 어떻게 해? 그리고 오늘 알람 몇 건이야"
    llm = _decompose_llm(_task("portal_help", "유사어 등록은 어떻게 해?"),
                         _task("alarm_query", "오늘 알람 몇 건이야", "t2"))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    assert [t["agent"] for t in plan["task_plan"]] == ["portal_help", "alarm_query"]


@pytest.mark.asyncio
async def test_data_request_guard_noop_when_disabled() -> None:
    """비활성이면 분해 어휘에 portal_help 가 없어 가드가 바꿀 task 가 없다(종전 계획 그대로)."""
    llm = _decompose_llm(_task("alarm_query", Q_ALARM_COUNT))
    plan = await ip._plan_turn(_state(Q_ALARM_COUNT), llm=llm, app_config=_config(enabled=False))
    assert plan[ip.PLAN_PATH_KEY] == "llm_decompose"
    assert [t["agent"] for t in plan["task_plan"]] == ["alarm_query"]


# ── 5. 되돌림 G-5 (b) — 분해 출구(2단) ──────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "agent"), [
    (Q_SYNONYM_HOWTO, "cache_management"),     # W0 2단 실측 담당
    (Q_RECEIVED_ALARM, "general_inference"),   # W0 2단 실측 담당
])
async def test_redirect_applies(query: str, agent: str) -> None:
    llm = _decompose_llm(_task(agent, "LLM 이 바꿔 쓴 질의"))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    assert plan[ip.PLAN_PATH_KEY] == ph.PORTAL_REDIRECT_PATH
    [task] = plan["task_plan"]
    assert task["agent"] == "portal_help"
    assert task["sub_query"] == query  # 원문 — 화면 용어 보존


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "tasks"), [
    # 데이터 요청(분해가 잘못 general_inference 로 보내도 판정이 불성립)
    (Q_ALARM_COUNT, [_task("general_inference", Q_ALARM_COUNT)]),
    # 외부 IT 개념 · 일반 개념
    ("쿠버네티스 사용법", [_task("general_inference", "쿠버네티스 사용법")]),
    ("CPU 사용률이 뭐야", [_task("general_inference", "CPU 사용률이 뭐야")]),
    # 실제 등록 요청
    ("hostname에 '장비이름' 유사어 등록해줘",
     [_task("cache_management", "hostname에 '장비이름' 유사어 등록")]),
    # 다중 task 계획
    (Q_RECEIVED_ALARM, [_task("general_inference", "수신 알람 설명"),
                        _task("alarm_query", "오늘 수신 알람 건수", "t2")]),
    # 조회 담당 단일 task
    (Q_SYNONYM_HOWTO, [_task("data_query", Q_SYNONYM_HOWTO)]),
    (Q_RECEIVED_ALARM, [_task("alarm_query", Q_RECEIVED_ALARM)]),
])
async def test_redirect_does_not_apply(query: str, tasks: list[dict]) -> None:
    llm = _decompose_llm(*tasks)
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    assert plan[ip.PLAN_PATH_KEY] != ph.PORTAL_REDIRECT_PATH
    assert all(t["agent"] != "portal_help" for t in plan["task_plan"])
    assert [t["agent"] for t in plan["task_plan"]] == [t["agent"] for t in tasks]


@pytest.mark.asyncio
async def test_redirect_off_when_disabled() -> None:
    llm = _decompose_llm(_task("general_inference", Q_RECEIVED_ALARM))
    plan = await ip._plan_turn(_state(Q_RECEIVED_ALARM), llm=llm, app_config=_config(enabled=False))
    assert plan[ip.PLAN_PATH_KEY] == "llm_decompose"
    assert plan["task_plan"][0]["agent"] == "general_inference"


def test_coerce_skips_direct_response_task() -> None:
    tasks = [_task("general_inference", Q_RECEIVED_ALARM, direct_response="고정 안내")]
    assert ph.coerce_portal_redirect(tasks, Q_RECEIVED_ALARM, _config()) is None
    assert tasks[0]["agent"] == "general_inference"


def test_usage_query_stays_with_usage_shortcut() -> None:
    """사용법 단락(③.8) 질의는 매뉴얼 판정이 성립해도 되돌리지 않는다(G-6 (a))."""
    query = "이 시스템에서 노이즈 캔슬링 사용법 알려줘"
    tasks = [_task("general_inference", query)]
    assert ph.coerce_portal_redirect(tasks, query, _config()) is None
    assert ph.portal_redirect(query, _config()) is None


# 관리자 매뉴얼 전용 절(A-32)로 되돌려지는 질문 — 되돌림은 호출자 역할로 매칭한다(V-2)
Q_ADMIN_ONLY = "사용자 비밀번호 초기화는 어떻게 해?"


@pytest.mark.parametrize(("user_role", "redirected"), [
    ("admin", True), ("user", False), (None, False), ("guest", False),
])
def test_redirect_matches_by_caller_role(user_role: str | None, redirected: bool) -> None:
    """관리자 = 두 권 · 그 밖(역할 없음 포함 — fail-closed) = 사용자 매뉴얼만."""
    match = ph.portal_redirect(Q_ADMIN_ONLY, _config(), user_role=user_role)
    assert (match is not None) is redirected
    if redirected:
        assert match.manual == "admin"


@pytest.mark.asyncio
@pytest.mark.parametrize(("user_role", "path"), [
    ("admin", ph.PORTAL_REDIRECT_PATH), ("user", "llm_decompose"), (None, "llm_decompose"),
])
async def test_decompose_exit_redirect_uses_state_role(user_role: str | None, path: str) -> None:
    llm = _decompose_llm(_task("general_inference", Q_ADMIN_ONLY))
    plan = await ip._plan_turn(_state(Q_ADMIN_ONLY, user_role=user_role), llm=llm,
                               app_config=_config())
    assert plan[ip.PLAN_PATH_KEY] == path


# ── 6. 되돌림 — handler 수렴점(1·2단 `run_general_inference`) ─────────────────

@pytest.fixture
def fake_service(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """답변 서비스 대역 — 호출 인자를 기록하고 고정 답을 돌려준다(서술 LLM 0)."""
    calls: list[dict] = []

    async def _fake(query: str, *, user: Any, llm: Any, index: Any = None) -> PortalHelpResult:
        calls.append({"query": query, "user": user})
        return PortalHelpResult("매뉴얼 답", section_ids=["U-44"])

    monkeypatch.setattr(ph, "answer_portal_help", _fake)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    return calls


@pytest.mark.asyncio
async def test_handler_redirects_portal_question(fake_service: list[dict]) -> None:
    from src.orchestration.subagents import run_general_inference

    out = await run_general_inference(
        {"agent": "general_inference", "sub_query": Q_RECEIVED_ALARM},
        {"user_query": Q_RECEIVED_ALARM, "user_role": "user", "user_id": "u1"},
        llm=FakeListChatModel(responses=["unused"]), app_config=_config(),
    )
    assert out["final_response"] == "매뉴얼 답"
    assert out[ph.META_KEY]["sections"] == ["U-44"]
    assert out["source_status"][0]["system"] == "portal"
    assert fake_service == [{"query": Q_RECEIVED_ALARM, "user": {"role": "user", "sub": "u1"}}]


@pytest.mark.asyncio
@pytest.mark.parametrize(("task", "enabled"), [
    ({"agent": "general_inference", "sub_query": "쿠버네티스 사용법"}, True),
    ({"agent": "general_inference", "sub_query": Q_RECEIVED_ALARM,
      "direct_response": "고정"}, True),
    ({"agent": "general_inference", "sub_query": Q_RECEIVED_ALARM}, False),
])
async def test_handler_does_not_redirect(
    task: dict, enabled: bool, fake_service: list[dict], monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.orchestration.subagents as sa

    async def _general(isolated: dict, *, llm: Any, app_config: Any) -> dict:
        return {"final_response": "일반 답"}

    monkeypatch.setattr(sa, "general_inference", _general)
    out = await sa.run_general_inference(task, {"user_query": task["sub_query"]},
                                         llm=FakeListChatModel(responses=["x"]),
                                         app_config=_config(enabled=enabled))
    assert fake_service == []
    assert out["final_response"] in ("일반 답", "고정")


@pytest.mark.asyncio
@pytest.mark.parametrize(("user_role", "redirected"), [("admin", True), ("user", False)])
async def test_handler_redirect_uses_isolated_role(
    user_role: str, redirected: bool, fake_service: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """handler 수렴점은 격리 입력의 `user_role`로 매칭한다(V-2)."""
    import src.orchestration.subagents as sa

    async def _general(isolated: dict, *, llm: Any, app_config: Any) -> dict:
        return {"final_response": "일반 답"}

    monkeypatch.setattr(sa, "general_inference", _general)
    out = await sa.run_general_inference(
        {"agent": "general_inference", "sub_query": Q_ADMIN_ONLY},
        {"user_query": Q_ADMIN_ONLY, "user_role": user_role, "user_id": "u1"},
        llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert out["final_response"] == ("매뉴얼 답" if redirected else "일반 답")


@pytest.mark.asyncio
async def test_run_portal_help_error_carries_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fail(query: str, *, user: Any, llm: Any, index: Any = None) -> PortalHelpResult:
        return PortalHelpResult("색인을 쓸 수 없습니다", status="error", reason="색인 없음")

    monkeypatch.setattr(ph, "answer_portal_help", _fail)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    out = await ph.run_portal_help({"sub_query": "수신 알람"}, {},
                                   llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert out["error"] == "색인 없음"
    assert out["degraded_reason"] == "portal_help_error"


# ── 7. 1단 도구·지침(G-8 (b)) ──────────────────────────────────────────────

def test_build_tools_adds_portal_tool_only_when_active() -> None:
    from src.orchestration.deepagents_tools import build_tools

    worker = FakeListChatModel(responses=["x"])
    off = [t.name for t in build_tools(worker, _config(enabled=False))]
    on = [t.name for t in build_tools(worker, _config())]
    assert ph.PORTAL_TOOL_NAME not in off
    assert on == [*off, ph.PORTAL_TOOL_NAME]  # 목록 끝에만 붙는다(나머지 순서 불변)


def test_orchestrator_instructions_follow_tool_exposure() -> None:
    assert render_orchestrator_instructions(False) == ORCHESTRATOR_INSTRUCTIONS
    on = render_orchestrator_instructions(True)
    assert PORTAL_HELP_RULE in on and on.endswith(PORTAL_HELP_TOOL_LINE)
    stripped = on.replace(PORTAL_HELP_RULE, "", 1).removesuffix(PORTAL_HELP_TOOL_LINE)
    assert stripped == ORCHESTRATOR_INSTRUCTIONS


@pytest.mark.asyncio
async def test_portal_tool_runs_portal_handler_with_user_role(fake_service: list[dict]) -> None:
    from src.orchestration.deepagents_tools import build_tools

    tools = build_tools(FakeListChatModel(responses=["x"]), _config(),
                        ambient_state={"user_role": "admin", "user_id": "a1"})
    [tool] = [t for t in tools if t.name == ph.PORTAL_TOOL_NAME]
    text = await tool.ainvoke({"sub_query": "수신 알람이 뭐야"})
    assert "매뉴얼 답" in str(text)
    assert fake_service == [{"query": "수신 알람이 뭐야", "user": {"role": "admin", "sub": "a1"}}]


# ── 8. 재계획 결정적 종료 · 의존 끊기 문구(W3 교정) ───────────────────────────

def _replan_state(task_plan: list[dict], task_results: dict) -> dict:
    state = create_initial_state(user_query="수신 알람이 뭐야")
    state["task_plan"] = task_plan
    state["task_results"] = task_results
    return state


def _no_eval_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.side_effect = AssertionError("포탈 전용 계획은 평가 LLM 을 부르지 않는다")
    return llm


@pytest.mark.asyncio
@pytest.mark.parametrize(("plan", "results"), [
    # 포탈 단일 task — 실패(사유 + 관련 절 링크)도 완결
    ([_task("portal_help", "수신 알람이 뭐야", status="completed")],
     {"t1": {"final_response": "관련 절을 찾지 못했습니다", "error": "근거 0건"}}),
    ([_task("portal_help", "수신 알람이 뭐야", status="completed")],
     {"t1": {"final_response": "매뉴얼 답"}}),
    # 포탈 + 결정적 안내뿐
    ([_task("portal_help", "수신 알람이 뭐야", status="completed"),
      _task("general_inference", "안내", "t2", direct_response="고정 안내", status="completed")],
     {"t1": {"final_response": "매뉴얼 답"}, "t2": {"final_response": "고정 안내"}}),
])
async def test_replanner_stops_portal_only_plan_without_llm(
    plan: list[dict], results: dict, caplog: pytest.LogCaptureFixture,
) -> None:
    from src.orchestration.replanner import replanner

    caplog.set_level("INFO", logger="src.orchestration.replanner")
    llm = _no_eval_llm()
    out = await replanner(_replan_state(plan, results), llm=llm, app_config=_config())
    assert out["needs_replan"] is False
    assert llm.ainvoke.await_count == 0
    assert "포탈 전용 계획 완료" in caplog.text  # 다른 결정적 종료가 아니라 이 규칙으로 끝났다


@pytest.mark.asyncio
async def test_replanner_evaluates_mixed_portal_plan() -> None:
    """포탈 + 조회 task 계획은 종전대로 평가 대상이다(포탈 전용 종료가 걸리지 않는다)."""
    from src.orchestration.replanner import replanner

    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(
        {"needs_replan": False, "reason": "충분", "additional_tasks": []}, ensure_ascii=False))
    plan = [_task("portal_help", "수신 알람이 뭐야", status="completed"),
            _task("alarm_query", "오늘 수신 알람 건수", "t2", status="completed")]
    results = {"t1": {"final_response": "매뉴얼 답"},
               "t2": {"query_results": [], "final_response": "0건"}}
    await replanner(_replan_state(plan, results), llm=llm, app_config=_config())
    assert llm.ainvoke.await_count == 1


def test_dependency_cut_note_for_portal_task() -> None:
    result = {"tasks": [
        _task("data_query", "서버"),
        _task("portal_help", "수신 알람이 뭐야", "t2", depends_on=["t1"], input_from=["t1"]),
    ]}
    ip._sanitize_task_views(result, _config())
    degraded = result.get("degraded") or []
    assert [d["reason"] for d in degraded] == ["portal_task_dependency_cut"]
    assert degraded[0]["detail"].startswith("매뉴얼 답은")
    assert "문서" not in degraded[0]["detail"]
