"""plans/142 독립 검증 · 보안 감사(verifier) — 포탈 기능 질의응답(D-315).

실 LLM 0 — 분해 LLM 은 목, 서술 LLM 은 `astream_text` 대역. 색인은 저장소 산출물
(`src/static/manual/help_index.json`)을 그대로 읽는다(되돌림·역할 필터 판정 재료가 실 색인이다).

`xfail(strict=True)` 테스트는 **지적한 결함의 재현**이다 — 결함이 고쳐지면 XPASS 로 실패하므로
그때 표지를 지운다.
"""

from __future__ import annotations

import importlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import src.doc_qa.portal_help as svc
from src.config import AppConfig, DBHubConfig, LLMConfig, PortalHelpConfig
from src.orchestration import portal_help as ph
from src.routing.registry import get_registry, parse_registry
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")

pytestmark = pytest.mark.skipif(not svc.help_index_status()[0], reason="매뉴얼 색인 없음")


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
    state = create_initial_state(user_query=query, thread_id="th-142-verify")
    state.update(extra)
    return state


def _task(agent: str, sub_query: str, task_id: str = "t1", **extra: Any) -> dict:
    return {"task_id": task_id, "agent": agent, "sub_query": sub_query, "depends_on": [],
            "input_from": [], "order": int(task_id[1:]), **extra}


def _decompose_llm(*tasks: dict) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(
        content=json.dumps({"tasks": list(tasks)}, ensure_ascii=False))
    return llm


class _NoCallLLM(FakeListChatModel):
    async def ainvoke(self, *args, **kwargs):  # noqa: D102
        raise AssertionError("결정적 단락은 분해 LLM 을 부르지 않는다")


class _Recorder:
    """서술 LLM 대역 — 받은 메시지를 기록한다."""

    def __init__(self) -> None:
        self.calls: list[list[Any]] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    rec = _Recorder()

    async def _fake(llm: Any, messages: list[Any], **kw: Any) -> str:
        rec.calls.append(messages)
        return "서술"

    monkeypatch.setattr(svc, "astream_text", _fake)
    return rec


# ══════════════════════════════════════════════════════════════════════════
# 1. 보안 · 인가 — 역할 필터 우회 없음
# ══════════════════════════════════════════════════════════════════════════

def _admin_probe_queries() -> list[str]:
    """관리자 매뉴얼 전용 절 제목·항목명(사용자 매뉴얼에 같은 이름이 없는 것)으로 만든 질의."""
    index = svc.load_help_index()
    assert index is not None
    user_titles = {svc._compact(c.title_key) for c in index.chunks if c.manual == "user"}
    out: list[str] = []
    for c in index.chunks:
        if c.manual != "admin" or svc._compact(c.title_key) in user_titles:
            continue
        out.append(f"{c.title_key}이 뭐야")
        out.append(f"'{c.title_key}'은 어떻게 해")
    return out


@pytest.mark.asyncio
async def test_user_role_never_receives_admin_manual_content(recorder: _Recorder) -> None:
    """일반 사용자 — 관리자 매뉴얼 전용 용어를 따옴표·평문으로 물어도 관리자 본문·입력 예·링크가
    서술 프롬프트와 답 어디에도 실리지 않는다(G-2). 관리자 전용 안내는 절 번호·제목만 낸다."""
    index = svc.load_help_index()
    queries = _admin_probe_queries()
    assert len(queries) > 100  # 실 색인 표본이 충분하다
    admin_texts = {c.text for c in index.chunks if c.manual == "admin"}
    user_texts = {c.text for c in index.chunks if c.manual == "user"}
    admin_only_texts = admin_texts - user_texts
    for q in queries:
        before = len(recorder.calls)
        res = await svc.answer_portal_help(q, user={"role": "user", "sub": "u1"}, llm=object())
        assert "/manual/admin" not in res.answer, q
        if res.status == svc.STATUS_ADMIN_ONLY:
            assert len(recorder.calls) == before  # LLM 0회
            assert all(s.startswith("admin:") for s in res.section_ids)
            assert all(t not in res.answer for t in admin_only_texts if len(t) > 40)
            continue
        assert all(s.startswith("user:") for s in res.section_ids), (q, res.section_ids)
        for messages in recorder.calls[before:]:
            prompt = "\n".join(str(m.content) for m in messages)
            assert "[관리자 매뉴얼 " not in prompt, q  # 근거 머리표(`_render_evidence`)


@pytest.mark.asyncio
async def test_missing_or_unknown_role_is_not_admin(recorder: _Recorder) -> None:
    """신원이 비거나(배치·워커 호출) 역할이 낯설면 일반 사용자로 본다(fail-closed)."""
    q = "'사용자 목록'은 어떻게 해"
    for user in (None, {}, {"role": None}, {"role": "guest"}, {"role": "USER"}):
        res = await svc.answer_portal_help(q, user=user, llm=object())
        assert res.diagnostics["is_admin"] is False
        assert all(not s.startswith("admin:") or res.status == svc.STATUS_ADMIN_ONLY
                   for s in res.section_ids)
    admin = await svc.answer_portal_help(q, user={"role": "admin"}, llm=object())
    assert admin.diagnostics["is_admin"] is True


@pytest.mark.asyncio
async def test_handler_identity_only_from_user_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """처리기는 격리 입력의 `user_role`(라우트가 검증 토큰·DB 로 채운 값)만 신원으로 옮긴다 —
    격리 입력의 다른 키(`is_admin`·`roles`)가 관리자 판정에 끼어들지 않는다."""
    seen: list[Any] = []

    async def _fake(query: str, *, user: Any, llm: Any, index: Any = None):
        seen.append(dict(user))
        return svc.PortalHelpResult("답")

    monkeypatch.setattr(ph, "answer_portal_help", _fake)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    isolated = {"user_role": "user", "user_id": "u1", "is_admin": True, "roles": ["admin"],
                "user_query": "수신 알람이 뭐야"}
    await ph.run_portal_help({"sub_query": "수신 알람이 뭐야"}, isolated,
                             llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert seen == [{"role": "user", "sub": "u1"}]
    from src.doc_qa.authz import _is_admin
    assert _is_admin(seen[0]) is False


@pytest.mark.asyncio
async def test_tier1_tool_without_ambient_role_is_not_admin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """1단 도구 — ambient 에 `user_role` 이 없으면 신원 None(일반 사용자)으로 간다(2단과 대칭)."""
    from src.orchestration.deepagents_tools import build_tools

    seen: list[Any] = []

    async def _fake(query: str, *, user: Any, llm: Any, index: Any = None):
        seen.append(dict(user))
        return svc.PortalHelpResult("답")

    monkeypatch.setattr(ph, "answer_portal_help", _fake)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    tools = build_tools(FakeListChatModel(responses=["x"]), _config(),
                        ambient_state={"user_id": "u9"})
    [tool] = [t for t in tools if t.name == ph.PORTAL_TOOL_NAME]
    await tool.ainvoke({"sub_query": "수신 알람이 뭐야"})
    assert seen == [{"role": None, "sub": "u9"}]


def test_route_identity_reinjection_covers_user_role() -> None:
    """후속 턴 델타에도 이번 요청 토큰의 role 이 다시 실린다.

    체크포인터는 델타만 병합한다 — 강등이 즉시 반영돼야 한다.
    """
    from src.api.routes.query import _with_current_identity

    delta = _with_current_identity({"user_role": "admin"}, {"role": "user"})
    assert delta["user_role"] == "user"


def test_portal_is_not_selectable_source() -> None:
    """요청 본문 `selected_sources=["portal"]`는 레지스트리 소스가 아니라 걸러진다."""
    reg = get_registry()
    known = {s.code for s in reg.solutions()} | {reg.system_of(d) or d for d in reg.db_ids()}
    assert "portal" not in known
    assert not reg.is_non_db_system("portal")


# ══════════════════════════════════════════════════════════════════════════
# 2. 레지스트리 부작용 — `data_source: false`
# ══════════════════════════════════════════════════════════════════════════

def test_portal_absent_from_every_source_view() -> None:
    reg = get_registry()
    aliases = set(ph.portal_aliases())
    assert aliases  # 별칭 5종
    assert "portal" not in {s.code for s in reg.solutions()}
    assert "portal" not in {s.code for s in reg.non_db_systems()}
    assert not aliases & set(reg.source_alias_terms())
    assert not aliases & set(reg.new_db_signal_terms())
    assert reg.solution_aliases("portal") == ()
    assert reg.views_of("portal") == () and reg.sources_of("portal") == ()
    for cap in {c.code for c in reg.capability_specs()}:
        assert "portal" not in reg.capability_owners(cap)
        assert "portal" not in reg.capability_providers(cap)
    assert reg.system_label("portal") == "AIOps 포탈 매뉴얼"


def test_portal_not_in_active_conditional_systems() -> None:
    """소스 슬롯·명시 소스 고정·소스 기억이 쓰는 시스템 → 처리기 표에 포탈이 없다."""
    from src.orchestration.conditional_agents import active_conditional_systems

    assert "portal" not in active_conditional_systems(_config())


def test_registry_quoted_false_is_not_data_source() -> None:
    reg = parse_registry({"solutions": [
        {"code": "portal", "label": "x", "data_source": "false", "aliases": ["포탈 도움말"]},
    ]})
    assert "portal" not in {s.code for s in reg.solutions()}


# ══════════════════════════════════════════════════════════════════════════
# 3. 명시 명령 ③.9 경계
# ══════════════════════════════════════════════════════════════════════════

_PORTAL_PREFIX_DATA_QUERIES = [
    "AIOps 포탈에서 김포 서버 CPU 사용률을 알려줘",
    "AIOps 포탈에서 활성 알람을 알려줘",
    "AIOps 포탈에서 gimpo-web01 서버 찾아줘",
    "AIOps 포탈로 알람 현황 알려줘",
]


@pytest.mark.parametrize("query", _PORTAL_PREFIX_DATA_QUERIES)
def test_portal_prefixed_data_request_is_not_portal_command(query: str) -> None:
    assert ph.parse_portal_command(query, _config()) is None


@pytest.mark.asyncio
async def test_portal_prefixed_data_request_off_reaches_decompose() -> None:
    query = _PORTAL_PREFIX_DATA_QUERIES[0]
    llm = _decompose_llm(_task("data_query", query))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config(enabled=False))
    assert plan[ip.PLAN_PATH_KEY] == "llm_decompose"


@pytest.mark.parametrize("query", [
    "관리자 매뉴얼에서 '사용자 목록'을 설명해줘",
    "사용자 매뉴얼에서 '상위 억제 알람 유형'을 설명해줘",
    "사용자 매뉴얼에서 '최근 결정'을 설명해줘",
])
def test_command_with_screen_term_containing_data_cue(query: str) -> None:
    assert ph.parse_portal_command(query, _config()) is not None


@pytest.mark.asyncio
async def test_screen_term_question_is_not_coerced_to_data() -> None:
    query = "노이즈 관제의 '상위 억제 알람 유형'은 무엇인지 설명해줘"
    llm = _decompose_llm(_task("portal_help", query))
    plan = await ip._plan_turn(_state(query), llm=llm, app_config=_config())
    assert [t["agent"] for t in plan["task_plan"]] == ["portal_help"]


@pytest.mark.asyncio
async def test_help_command_aliases_pin_without_llm() -> None:
    """정상 명령 — 별칭 5종 각각 분해 LLM 없이 portal_help 단일 task."""
    for alias in ph.portal_aliases():
        query = f"{alias}에서 수신 알람을 설명해줘"
        plan = await ip._plan_turn(_state(query), llm=_NoCallLLM(responses=["x"]),
                                   app_config=_config())
        assert plan[ip.PLAN_PATH_KEY] == ph.PORTAL_COMMAND_PATH, alias
        assert [t["agent"] for t in plan["task_plan"]] == ["portal_help"]
        assert plan["task_plan"][0]["sub_query"] == "수신 알람"


# ══════════════════════════════════════════════════════════════════════════
# 4. 되돌림 G-5 경계
# ══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("query", [
    "오늘 수신 알람 몇 건이야", "서버 CPU 상위 10", "현재 활성 알람이 뭐야",
    "web01 서버 상태가 어떻게 돼?", "김포 서버 CPU 사용률이 어떻게 돼", "알람이 뭐야",
    "어제 발생한 알람 뭐야", "서버별 알람 건수 어떻게 돼", "TCP 동작 원리가 뭐야",
    "리눅스 로그 검색 방법", "nginx 설정 리로드 방법", "spring 의존성 주입이 뭐야",
    "네트워크 문제 해결 방법", "오라클 DB 연결 테스트 방법", "쿠버네티스 사용법",
])
def test_redirect_rejects_data_and_external_questions(query: str) -> None:
    """확인된 음성 경계(현재 통과) — 데이터 요청·외부 IT 질문은 되돌리지 않는다."""
    assert ph.portal_redirect(query, _config()) is None


_EXTERNAL_FALSE_POSITIVES = [
    "장애 재현 절차가 뭐야",
    "redis 재연결 간격 설정 방법",
    "윈도우 비밀번호 초기화 방법",
    "데이터 마스킹이 뭐야",
]


@pytest.mark.parametrize("query", _EXTERNAL_FALSE_POSITIVES)
def test_redirect_rejects_external_questions_with_manual_terms(query: str) -> None:
    assert ph.portal_redirect(query, _config()) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["장애 재현 절차가 뭐야", "redis 재연결 간격 설정 방법"])
async def test_user_general_question_not_answered_admin_only(
    query: str, recorder: _Recorder, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.orchestration.subagents as sa

    async def _general(isolated: dict, *, llm: Any, app_config: Any) -> dict:
        return {"final_response": "일반 답"}

    monkeypatch.setattr(sa, "general_inference", _general)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    out = await sa.run_general_inference(
        {"agent": "general_inference", "sub_query": query},
        {"user_query": query, "user_role": "user", "user_id": "u1"},
        llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert out["final_response"] == "일반 답"


@pytest.mark.parametrize("query", [
    "노이즈 캔슬링 억제율이 어떻게 돼?",
    "이 서버 중요도가 어떻게 돼",
    "지금 열린 사건이 뭐야",
])
def test_redirect_rejects_value_questions(query: str) -> None:
    assert ph.portal_redirect(query, _config()) is None


@pytest.mark.asyncio
async def test_handler_redirect_respects_original_usage_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.orchestration.subagents as sa

    async def _general(isolated: dict, *, llm: Any, app_config: Any) -> dict:
        return {"final_response": "사용법 안내"}

    async def _fake(query: str, *, user: Any, llm: Any, index: Any = None):
        return svc.PortalHelpResult("매뉴얼 답")

    monkeypatch.setattr(sa, "general_inference", _general)
    monkeypatch.setattr(ph, "answer_portal_help", _fake)
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    original = "이 시스템에서 노이즈 캔슬링 사용법 알려줘"
    out = await sa.run_general_inference(
        {"agent": "general_inference", "sub_query": "노이즈 캔슬링이 뭐야"},
        {"user_query": "노이즈 캔슬링이 뭐야", "original_user_query": original,
         "user_role": "user"},
        llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert out["final_response"] == "사용법 안내"


def test_coerce_redirect_never_touches_multi_task_plans() -> None:
    """2단 분해 출구 — 다중 task 계획은 portal 판정이 성립하는 원문이어도 불변."""
    q = "노이즈 캔슬링이 뭐야"
    tasks = [_task("general_inference", q), _task("general_inference", q, "t2")]
    snapshot = json.loads(json.dumps(tasks))
    assert ph.coerce_portal_redirect(tasks, q, _config()) is None
    assert tasks == snapshot


def test_data_request_coerce_does_not_touch_other_tasks() -> None:
    tasks = [_task("portal_help", "수신 알람이 뭐야"),
             _task("general_inference", "오늘 알람 건수", "t2"),
             _task("portal_help", "오늘 수신 알람 몇 건", "t3")]
    ph.coerce_portal_data_request(tasks, "수신 알람이 뭐야, 그리고 오늘 수신 알람 몇 건")
    assert [t["agent"] for t in tasks] == ["portal_help", "general_inference", "data_query"]


# ══════════════════════════════════════════════════════════════════════════
# 5. 기본 on 회귀 — 사용법이 아닌 기존 경로는 on/off 동일
# ══════════════════════════════════════════════════════════════════════════

def _strip(plan: dict) -> dict:
    return json.loads(json.dumps(plan, ensure_ascii=False, default=str))


@pytest.mark.asyncio
@pytest.mark.parametrize(("query", "tasks"), [
    ("오늘 수신 알람 몇 건이야", [_task("alarm_query", "오늘 수신 알람 몇 건")]),
    ("김포 서버 CPU 사용률 상위 10건", [_task("data_query", "김포 서버 CPU 사용률 상위 10건")]),
    ("web01 프로세스 목록", [_task("data_query", "web01 프로세스 목록")]),
    ("최근 이벤트 보여줘", [_task("data_query", "최근 이벤트 보여줘")]),
    ("쿠버네티스 사용법", [_task("general_inference", "쿠버네티스 사용법")]),
    ("hostname에 '장비이름' 유사어 등록해줘",
     [_task("cache_management", "hostname에 '장비이름' 유사어 등록")]),
    ("AIOps 포탈에서 CPU 사용률 상위 10건", [_task("data_query", "CPU 사용률 상위 10건")]),
])
async def test_non_help_plans_identical_on_off(query: str, tasks: list[dict]) -> None:
    on = await ip._plan_turn(_state(query), llm=_decompose_llm(*tasks), app_config=_config())
    off = await ip._plan_turn(_state(query), llm=_decompose_llm(*tasks),
                              app_config=_config(enabled=False))
    assert _strip(on) == _strip(off)


@pytest.mark.asyncio
@pytest.mark.parametrize("query", [
    "사용법 알려줘",                         # U-18 ❓ 사용법 단락
    "이 시스템에서 노이즈 캔슬링 사용법 알려줘",
    "본부 매뉴얼에서 백업 절차 찾아줘",       # U-46 문서 명령 ②.9(문서 비활성 → 안내)
    "전산관리매뉴얼에서 서버 반입 절차를 검색해줘",
])
async def test_shortcut_paths_identical_on_off(query: str) -> None:
    on = await ip._plan_turn(_state(query), llm=_NoCallLLM(responses=["x"]), app_config=_config())
    off = await ip._plan_turn(_state(query), llm=_NoCallLLM(responses=["x"]),
                              app_config=_config(enabled=False))
    assert _strip(on) == _strip(off)
    assert on[ip.PLAN_PATH_KEY] != ph.PORTAL_COMMAND_PATH


# ══════════════════════════════════════════════════════════════════════════
# 6. 색인 부재 · 비활성
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def no_index(monkeypatch: pytest.MonkeyPatch, tmp_path):
    svc.reset_help_index_cache()
    monkeypatch.setattr(svc, "DEFAULT_INDEX_PATH", tmp_path / "missing.json")
    yield
    svc.reset_help_index_cache()


def test_missing_index_disables_every_entry(no_index, caplog: pytest.LogCaptureFixture) -> None:
    from src.orchestration.conditional_agents import active_conditional_agents
    from src.orchestration.deepagents_tools import build_tools

    cfg = _config()
    assert ph.portal_active(cfg) is False
    assert "portal_help" not in active_conditional_agents(cfg)
    assert ph.PORTAL_TOOL_NAME not in [t.name for t in build_tools(
        FakeListChatModel(responses=["x"]), cfg)]
    assert ph.portal_redirect("노이즈 캔슬링이 뭐야", cfg) is None
    assert "portal_help" not in ip._planner_system_prompt(cfg)
    caplog.set_level("WARNING", logger="src.orchestration.portal_help")
    ph.log_portal_help_startup(cfg)
    assert "색인을 쓸 수 없음" in caplog.text  # 침묵 금지


@pytest.mark.asyncio
async def test_missing_index_command_notices_only(no_index) -> None:
    plan = await ip._plan_turn(_state("AIOps 포탈에서 수신 알람을 설명해줘"),
                               llm=_NoCallLLM(responses=["x"]), app_config=_config())
    [task] = plan["task_plan"]
    assert task["agent"] == "general_inference"
    assert "도움말은 지금 쓸 수 없어" in task["direct_response"]


@pytest.mark.asyncio
async def test_missing_index_handler_returns_structured_error(no_index, monkeypatch) -> None:
    monkeypatch.setattr(ph, "create_llm", lambda *a, **k: FakeListChatModel(responses=["x"]))
    out = await ph.run_portal_help({"sub_query": "수신 알람"}, {"user_role": "user"},
                                   llm=FakeListChatModel(responses=["x"]), app_config=_config())
    assert out["degraded_reason"] == "portal_help_error"
    assert "색인" in out["error"]


@pytest.mark.asyncio
async def test_replanner_portal_rule_needs_all_results() -> None:
    """포탈 전용 결정적 종료는 모든 task 결과가 있을 때만이다.

    결과가 빠진 task 가 있으면 이 규칙으로 끝내지 않는다.
    """
    from src.orchestration.replanner import replanner

    state = create_initial_state(user_query="수신 알람이 뭐야")
    state["task_plan"] = [_task("portal_help", "수신 알람이 뭐야", status="completed"),
                          _task("portal_help", "침묵 규칙이 뭐야", "t2", status="completed")]
    state["task_results"] = {"t1": {"final_response": "매뉴얼 답"}}
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=json.dumps(
        {"needs_replan": False, "reason": "충분", "additional_tasks": []}, ensure_ascii=False))
    import logging

    records: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    h = _H()
    logging.getLogger("src.orchestration.replanner").addHandler(h)
    try:
        await replanner(state, llm=llm, app_config=_config())
    finally:
        logging.getLogger("src.orchestration.replanner").removeHandler(h)
    assert not any("포탈 전용 계획 완료" in r for r in records)
