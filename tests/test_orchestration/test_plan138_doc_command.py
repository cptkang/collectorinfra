"""plans/138 — RAG 문서 검색 강제 라우팅(D-307) · 원문 검색 · answer 프로파일 · 미지정 고지.

실 LLM·외부 호출 0. 레지스트리·문서군 정본은 저장소 파일(`config/db_registry.yaml` ·
`config/rag_collections.yaml`)을 그대로 읽는다 — 별칭·표면어 정본이 판정 재료이기 때문이다.
"""

from __future__ import annotations

import importlib

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from src.config import AppConfig, DBHubConfig, LLMConfig, RagConfig
from src.doc_qa.service import DocAnswer
from src.orchestration import doc_query as dq
from src.orchestration.conditional_agents import SOURCE_NOTICE_KEY, apply_explicit_sources
from src.orchestration.doc_command import DOC_COMMAND_PATH, match_collections, parse_doc_command
from src.infrastructure.doc_sources import routing_collections
from src.state import create_initial_state

ip = importlib.import_module("src.orchestration.intent_planner")


def _config(*, routing: bool = True) -> AppConfig:
    return AppConfig(
        _env_file=None,
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db"),
        rag=RagConfig(_env_file=None, enabled=True, chat_routing_enabled=routing),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )


class _NoCallLLM(FakeListChatModel):
    """부르면 실패하는 LLM — 강제 단락이 분해 LLM을 부르지 않음을 단언한다."""

    async def ainvoke(self, *args, **kwargs):  # noqa: D102
        raise AssertionError("문서 검색 명령은 분해 LLM 을 부르지 않는다")


# ── 1. 판정표(plans/138 §3 W1) ──────────────────────────────────────────────

@pytest.mark.parametrize(("query", "views", "text"), [
    ("RAG에서 서버 관리자의 역할을 검색해줘", [], "서버 관리자의 역할"),
    ("본부 매뉴얼에서 백업 담당자의 역할을 찾아줘", ["doc.hq_manual"], "백업 담당자의 역할"),
    ("본부매뉴얼에서 백업 담당자의 역할을 찾아줘", ["doc.hq_manual"], "백업 담당자의 역할"),
    ("전산관리매뉴얼에서 계정 신청 승인자 알려줘", ["doc.hq_manual"], "계정 신청 승인자"),
    ("전산관리 매뉴얼에서는 계정 신청 승인자를 검색해 줘", ["doc.hq_manual"], "계정 신청 승인자"),
    ("사내 문서에서 운영·개발 분리 기준 검색하시오", [], "운영·개발 분리 기준"),
    ("아키텍처 문서에서 서버 구성 단위를 찾아줘", ["doc.arch_docs"], "서버 구성 단위"),
    ("아키텍처 설계문서에서 서버 구성 단위를 조회해줘", ["doc.arch_docs"], "서버 구성 단위"),
    ("서버 관리자의 역할을 RAG에서 찾아줘", [], "서버 관리자의 역할"),
    ("RAG로 백업 담당자의 역할 검색", [], "백업 담당자의 역할"),
    ("RAG의 본부 매뉴얼에서 백업 담당자 찾아봐", ["doc.hq_manual"], "백업 담당자"),
])
def test_command_matches(query, views, text) -> None:
    cmd = parse_doc_command(query, _config())
    assert cmd is not None and cmd.active
    assert cmd.views == views
    assert cmd.query == text


@pytest.mark.parametrize("query", [
    "RAG가 뭐야?",                                       # 지목 조사 없음 — 개념 질문
    "서버 관리자의 역할은?",                             # 지목 없음 — 종전 분해(W3)
    "RAG에서 서버 관리자의 역할",                        # 검색 동사 없음
    "RAG에서 백업 담당자 찾고 김포 폴스타에서 CPU 보여줘",  # 다른 소스 지목 동반(D-307 ⑤)
    "김포 서버 CPU 목록을 워드 문서로 만들어줘",        # 「문서」는 별칭이 아니다
    "사용 매뉴얼에서 로그인 방법 찾아줘",               # 「매뉴얼」은 별칭이 아니다
    "RAG에서 검색해줘",                                  # 지목·명령을 떼면 질의가 빈다
    "",
])
def test_command_does_not_match(query) -> None:
    assert parse_doc_command(query, _config()) is None


def test_content_words_never_route_to_documents() -> None:
    """내용어(역할·절차·규정)만으로는 문서행이 아니다(D-307 주의 ① · D-004)."""
    for query in ("백업 담당자의 역할을 찾아줘", "계정 신청 절차 검색해줘", "보안 규정 알려줘"):
        assert parse_doc_command(query, _config()) is None


def test_general_word_outside_designator_does_not_pin_collection() -> None:
    """문장 중간의 일반어(「지침」·「아키텍처」)는 문서군 고정에 쓰지 않는다(D-307 ③)."""
    cmd = parse_doc_command("RAG에서 아키텍처 지침의 담당자를 찾아줘", _config())
    assert cmd is not None and cmd.views == []
    assert cmd.query == "아키텍처 지침의 담당자"


def test_match_collections_is_space_insensitive() -> None:
    cols = routing_collections(_config().rag)
    assert match_collections("본부 매뉴얼", cols) == ["hq_manual"]
    assert match_collections("아키텍처 설계문서", cols) == ["arch_docs"]
    assert match_collections("RAG", cols) == []


def test_inactive_routing_still_recognised_for_notice() -> None:
    cmd = parse_doc_command("RAG에서 서버 관리자의 역할을 검색해줘", _config(routing=False))
    assert cmd is not None and cmd.active is False and cmd.views == []


# ── 2. 계층 A 단락(②.9) ─────────────────────────────────────────────────────

def _state(query: str, **extra) -> dict:
    state = create_initial_state(user_query=query, thread_id="th-1")
    state.update(extra)
    return state


@pytest.mark.asyncio
async def test_plan_turn_pins_doc_query_without_decompose_llm() -> None:
    query = "본부 매뉴얼에서 백업 담당자의 역할을 찾아줘"
    plan = await ip._plan_turn(_state(query), llm=_NoCallLLM(responses=[]), app_config=_config())
    assert plan[ip.PLAN_PATH_KEY] == DOC_COMMAND_PATH
    [task] = plan["task_plan"]
    assert task["agent"] == "doc_query"
    assert task["views"] == ["doc.hq_manual"]
    assert task["sub_query"] == "백업 담당자의 역할"


@pytest.mark.asyncio
async def test_plan_turn_inactive_routing_notices_only() -> None:
    query = "RAG에서 서버 관리자의 역할을 검색해줘"
    plan = await ip._plan_turn(_state(query), llm=_NoCallLLM(responses=[]),
                               app_config=_config(routing=False))
    [task] = plan["task_plan"]
    assert task["agent"] == "general_inference"
    assert task[SOURCE_NOTICE_KEY] == ["doc"]
    assert "「RAG」" in task["direct_response"] and "조회하지 않았습니다" in task["direct_response"]


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"template_structure": {"sheets": []}},
    {"uploaded_file": b"x"},
    {"selected_db_ids": ["polestar"]},
    {"selected_sources": ["doc"]},
])
async def test_answer_and_form_turns_are_not_command_turns(extra) -> None:
    state = _state("RAG에서 서버 관리자의 역할을 검색해줘", **extra)
    assert ip._doc_command_plan(state, _config(), state["user_query"]) is None


def test_plan_exit_keeps_doc_command_task() -> None:
    """출구 정규화·소스 선별이 강제 계획을 바꾸지 않는다(멱등)."""
    config = _config()
    query = "RAG에서 서버 관리자의 역할을 검색해줘"
    state = _state(query, parsed_requirements={"target_db_hints": ["RAG"]})
    plan = ip._doc_command_plan(state, config, query)
    before = [dict(t) for t in plan["task_plan"]]
    ip._normalize_plan_exit(plan, state)
    ip._apply_source_selection(plan, state, config)
    after = [{k: t[k] for k in before[0]} for t in plan["task_plan"]]
    assert after == before


def test_rag_alias_pins_data_query_task_to_documents() -> None:
    """별칭 추가는 기존 명시 소스 고정(D-293)에도 반영된다(D-307 주의 ②)."""
    tasks = [{"task_id": "t1", "agent": "data_query", "sub_query": "RAG에서 백업 담당자"}]
    changed = apply_explicit_sources(tasks, ["RAG"], _config())
    assert changed == ["t1"] and tasks[0]["agent"] == "doc_query"


# ── 3. 단일 문서 task 원문 검색(W4) ──────────────────────────────────────────

def _decomposed(*tasks: dict) -> dict:
    return {"tasks": [dict(t) for t in tasks]}


def test_single_doc_task_first_turn_uses_original_query() -> None:
    result = _decomposed({"task_id": "t1", "agent": "doc_query", "sub_query": "백업 담당자 역할 규정"})
    ip._restore_doc_query_text(result, "백업 담당자의 역할은?", None)
    assert result["tasks"][0]["sub_query"] == "백업 담당자의 역할은?"


def test_follow_up_turn_keeps_rewritten_query() -> None:
    result = _decomposed({"task_id": "t1", "agent": "doc_query", "sub_query": "백업 담당자의 역할"})
    ip._restore_doc_query_text(result, "그럼 백업 담당자는?", {"previous_query": "x"})
    assert result["tasks"][0]["sub_query"] == "백업 담당자의 역할"


def test_composite_plan_keeps_task_queries() -> None:
    result = _decomposed(
        {"task_id": "t1", "agent": "doc_query", "sub_query": "계정 신청 승인 규정"},
        {"task_id": "t2", "agent": "data_query", "sub_query": "web01 CPU"},
    )
    ip._restore_doc_query_text(result, "계정 신청 승인 규정과 web01 CPU 알려줘", None)
    assert result["tasks"][0]["sub_query"] == "계정 신청 승인 규정"


# ── 4. answer 프로파일(W5) · 미지정 고지(W6) ─────────────────────────────────

def test_answer_llm_uses_answer_profile(monkeypatch) -> None:
    seen: dict = {}

    def fake_create(config, **kwargs):
        seen.update(kwargs)
        return "ANSWER_LLM"

    monkeypatch.setattr(dq, "create_llm", fake_create)
    assert dq._answer_llm(_config(), fallback="INJECTED") == "ANSWER_LLM"
    assert seen == {"purpose": "answer"}


def test_answer_llm_falls_back_with_reason(monkeypatch, caplog) -> None:
    def broken(config, **kwargs):
        raise RuntimeError("no endpoint")

    monkeypatch.setattr(dq, "create_llm", broken)
    assert dq._answer_llm(_config(), fallback="INJECTED") == "INJECTED"
    assert "answer 프로파일 LLM 생성 실패" in caplog.text


@pytest.mark.asyncio
async def test_handler_passes_answer_llm_and_guides_unspecified(monkeypatch) -> None:
    calls: list[dict] = []

    async def fake_engine(query, collection_ids, **kwargs):
        calls.append({"query": query, "collections": list(collection_ids), **kwargs})
        return DocAnswer("답", status="ok")

    monkeypatch.setattr(dq, "answer_from_documents", fake_engine)
    monkeypatch.setattr(dq, "create_llm", lambda config, **kw: "ANSWER_LLM")
    isolated = {"user_role": "user", "user_id": "kim", "thread_id": "th-1", "allowed_sources": None}
    res = await dq.run_doc_query({"sub_query": "q", "views": []}, isolated, llm="INJECTED",
                                 app_config=_config())
    assert calls[0]["llm"] == "ANSWER_LLM"
    assert "문서군을 지정하면 더 정확합니다(예: 「본부 전산관리매뉴얼에서 … 찾아줘」)" in res["final_response"]

    res = await dq.run_doc_query({"sub_query": "q", "views": ["doc.hq_manual"]}, isolated,
                                 llm="INJECTED", app_config=_config())
    assert "문서군을 지정" not in res["final_response"]


# ── 5. 분해 프롬프트(W3) ────────────────────────────────────────────────────

def test_planner_prompt_carries_rnr_only_when_active() -> None:
    active = ip._planner_system_prompt(_config(routing=True))
    assert "역할·책임·담당자(R&R)" in active
    assert "부서·담당자별 역할과 책임(R&R)" in active  # 문서군 설명(정본 YAML 렌더)
    inactive = ip._planner_system_prompt(_config(routing=False))
    assert "R&R" not in inactive
