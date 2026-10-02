"""plans/127 W1~W3 — 문서 RAG 조건부 처리기 `doc_query`(125 계약 위 · 2단 기준 경로).

고정하는 계약:
  1. **비활성 = 바이트 불변**: `RAG_ENABLED`·`RAG_CHAT_ROUTING_ENABLED` 둘 다 켜지고 보기로
     오를 문서군(정본 enabled ∧ 비민감)이 있어야 활성이다. 설정 대역(MagicMock)·스위치 off·
     민감 문서군뿐이면 처리기 미등록 · 분해 프롬프트는 기본 렌더 그대로 · 고정 처리기 목록 불변.
  2. 활성이면 분해 프롬프트는 **삽입만**(담당 한 줄 + 문서군 절) — APM 과 함께 켜져도 각자 삽입만.
  3. 레지스트리: `doc`은 비DB 시스템(존·실행 그룹 밖) · 보기는 레지스트리에 적지 않는다(YAML 파생).
  4. 처리기: 소스 축 인가(`allowed_sources`) → 문서군 축(민감) → 엔진 1회 ·
     보기 없으면 전체 + 고지 ·
     실패 status 는 사유 문구 그대로 + `error` · 일반 LLM 폴백 없음 · 감사 source=`chat`.
  5. 분해 출구: `views` 정제(처리기별 어휘) · 문서 task 의존 제거 + 경과 노트(간선 없음).
  6. 재계획: 문서 전용 계획은 LLM 평가 없이 종료(G-12) · 문서 재검색 후속은 중복 방지로 종료.
  7. 사용법 안내: 활성 ∧ 허용일 때만 문서 행(「없는데 있다」·「있는데 없다」 둘 다 막는다).
문서 엔진은 대역(`answer_from_documents` 교체)이다 — 실 LLM·외부 호출 0.
"""

from __future__ import annotations

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.doc_qa.service import DocAnswer
from src.orchestration import apm_query as aq
from src.orchestration import doc_query as dq
from src.orchestration.conditional_agents import active_conditional_agents, sanitize_task_views
from src.orchestration.db_access import is_access_denied_result
from src.orchestration.intent_planner import _coerce_alarm_intent
from src.orchestration.replanner import replanner
from src.orchestration.schemas import DecomposedPlan, views_plan_model
from src.orchestration.subagents import SUBAGENT_REGISTRY, resolve_subagent
from src.prompts.intent_planner import (
    INTENT_PLANNER_SYSTEM_TEMPLATE,
    render_intent_planner_environment_terms,
)
from src.routing.db_authz import SOURCE_ACCESS_DENIED_MESSAGE
from src.routing.registry import get_registry
from src.state import create_initial_state

# 패키지 `__init__`이 같은 이름의 함수를 다시 내보내 `from … import 모듈`이 함수를 가리킨다
ip = importlib.import_module("src.orchestration.intent_planner")
gi = importlib.import_module("src.nodes.general_inference")

BASE_AGENTS = {"data_query", "process_query", "alarm_query", "cache_management",
               "synonym_registration", "general_inference", "host_inspect"}


def _rag(enabled=True, routing=True, collections_file="", **extra) -> SimpleNamespace:
    return SimpleNamespace(enabled=enabled, chat_routing_enabled=routing,
                           collections_file=collections_file, max_collections_per_turn=2,
                           sensitive_allowed_users="", **extra)


def _cfg(rag=None, *, apm=False) -> SimpleNamespace:
    from src.config import DBHubConfig
    return SimpleNamespace(
        rag=rag,
        dbhub=DBHubConfig(source_endpoints={"apm": "http://127.0.0.1:9096/sse"} if apm else {}),
        composite=SimpleNamespace(task_frame_enabled=False, plan_dag_validation_enabled=False,
                                  max_targets=10, fanout_concurrency=2, audit_enabled=False),
        router=SimpleNamespace(capability_ownership_enabled=False),
        multi_db=SimpleNamespace(get_active_db_ids=lambda: ["polestar_cm_gp"]),
    )


def _yaml(tmp_path, items: list[dict]) -> str:
    import yaml
    path = tmp_path / "rag_collections.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "collections": items}, allow_unicode=True),
                    encoding="utf-8")
    return str(path)


def _base_prompt() -> str:
    # 기본 렌더 = 기본 템플릿 + 답변 영역 칸(plans/132 N-5 — 항상 켜짐) + 환경어 채움
    from src.prompts.intent_planner import render_intent_planner_areas_template

    return render_intent_planner_environment_terms(
        render_intent_planner_areas_template(INTENT_PLANNER_SYSTEM_TEMPLATE, ip._area_rows()),
        get_registry().environment_terms)


# ── 1. 비활성 = 바이트 불변 ──────────────────────────────────────────────────

def test_inactive_configs_register_nothing(tmp_path) -> None:
    sensitive_only = _yaml(tmp_path, [{"id": "secret_docs", "title": "민감", "sensitive": True}])
    inactive = [
        MagicMock(),                              # 설정 대역
        _cfg(None),                               # rag 그룹 없음
        _cfg(_rag(enabled=False)),                # 기능 off
        _cfg(_rag(routing=False)),                # 라우팅 스위치 off(기본)
        _cfg(_rag(collections_file=sensitive_only)),  # 보기로 오를 문서군 0(민감뿐)
    ]
    assert set(SUBAGENT_REGISTRY) == BASE_AGENTS, "고정 처리기 목록은 그대로다(D-283 ②)"
    base = _base_prompt()
    for cfg in inactive:
        assert dq.active_doc_subagents(cfg) == {}
        assert resolve_subagent("doc_query", cfg) is None
        if not isinstance(cfg, MagicMock):
            assert active_conditional_agents(cfg) == {}
            assert ip._planner_system_prompt(cfg) == base, "비활성은 기본 렌더 그대로"


def test_registry_doc_is_non_db_system_without_views() -> None:
    reg = get_registry()
    assert reg.is_non_db_system("doc") and reg.system_db_ids("doc") == ()
    assert not any(g.solution == "doc" for g in reg.zone_groups())
    assert reg.views_of("doc") == (), "보기는 레지스트리에 적지 않는다 — rag_collections.yaml 파생"
    for code in ("doc_regulation", "doc_design"):
        assert reg.capability_owners(code) == ("doc",)
    assert reg.system_label("doc") == "사내 문서(규정·설계 근거)"


# ── 2. 활성 프롬프트 = 삽입만 ─────────────────────────────────────────────────

def _strip_doc(prompt: str) -> str:
    start = prompt.index("## 사내 문서(규정·설계 근거) 조회")
    end = prompt.index("## 작업 분해 규칙\n")
    return (prompt[:start] + prompt[end:]).replace("\n" + dq.render_agent_line(), "", 1)


def test_active_prompt_is_insertion_only() -> None:
    cfg = _cfg(_rag())
    active = ip._planner_system_prompt(cfg)
    assert dq.render_agent_line() in active
    assert _strip_doc(active) == _base_prompt(), "기존 줄은 한 글자도 바뀌지 않는다"
    # 문서군 표·예시는 정본 YAML 파생
    views = [dq.view_id(c.id) for c in dq.doc_views(cfg)]
    assert views and all(f"`{v}`" in active for v in views)
    assert f'"views": ["{views[0]}"]' in active


def test_apm_and_doc_both_active_each_insertion_only() -> None:
    both = ip._planner_system_prompt(_cfg(_rag(), apm=True))
    apm_only = ip._planner_system_prompt(_cfg(None, apm=True))
    assert _strip_doc(both) == apm_only
    assert both.index(aq.render_agent_line()) < both.index(dq.render_agent_line())
    assert both.index("## WAS·미들웨어(APM) 조회") < both.index("## 사내 문서(규정·설계 근거) 조회")


def test_sensitive_collection_is_not_a_view(tmp_path) -> None:
    path = _yaml(tmp_path, [
        {"id": "open_docs", "title": "공개 문서", "description": "공개"},
        {"id": "secret_docs", "title": "민감 문서", "description": "민감", "sensitive": True},
        {"id": "off_docs", "title": "꺼진 문서", "description": "꺼짐", "enabled": False},
    ])
    cfg = _cfg(_rag(collections_file=path))
    assert [c.id for c in dq.doc_views(cfg)] == ["open_docs"]
    prompt = ip._planner_system_prompt(cfg)
    assert "secret_docs" not in prompt and "민감 문서" not in prompt and "off_docs" not in prompt


# ── 3. 분해 출구 — 보기 정제 · 의존 제거 ─────────────────────────────────────

def test_views_schema_and_sanitize() -> None:
    cfg = _cfg(_rag())
    model = views_plan_model(DecomposedPlan, ("doc_query",))
    plan = model.model_validate({"tasks": [
        {"task_id": "t1", "agent": "doc_query", "sub_query": "x", "views": ["doc.hq_manual"]}]})
    assert plan.tasks[0].views == ["doc.hq_manual"]
    assert dq.sanitize_views(["doc.nope", "doc.arch_docs", "doc.arch_docs", 3, "doc.hq_manual"],
                             cfg) == ["doc.arch_docs", "doc.hq_manual"]
    assert dq.sanitize_views("doc.hq_manual", cfg) == ["doc.hq_manual"]
    assert dq.sanitize_views(["doc.hq_manual"], _cfg(_rag(routing=False))) == []


def test_sanitize_task_views_per_agent_and_cuts_doc_dependencies() -> None:
    cfg = _cfg(_rag(), apm=True)
    result = {"tasks": [
        {"task_id": "t1", "agent": "doc_query", "sub_query": "규정",
         "views": ["doc.hq_manual", "x"],
         "depends_on": ["t2"], "input_from": ["t2"]},
        {"task_id": "t2", "agent": "data_query", "sub_query": "서버", "views": ["doc.hq_manual"]},
        {"task_id": "t3", "agent": "apm_query", "sub_query": "WAS",
         "views": ["apm.events", "doc.x"]},
        {"task_id": "t4", "agent": "data_query", "sub_query": "후속",
         "depends_on": ["t1", "t2"],
         "input_from": ["t1"]},
    ]}
    cut = sanitize_task_views(result, cfg)
    t1, t2, t3, t4 = result["tasks"]
    assert t1["views"] == ["doc.hq_manual"] and "views" not in t2 and t3["views"] == ["apm.events"]
    assert t1["depends_on"] == [] and t1["input_from"] == []
    assert t4["depends_on"] == ["t2"] and t4["input_from"] == []
    assert cut == ["t1", "t4"]


def test_planner_exit_records_dependency_cut_note() -> None:
    result = {"tasks": [
        {"task_id": "t1", "agent": "data_query", "sub_query": "서버"},
        {"task_id": "t2", "agent": "doc_query", "sub_query": "규정", "depends_on": ["t1"],
         "input_from": ["t1"]},
    ]}
    ip._sanitize_task_views(result, _cfg(_rag()))
    reasons = [d["reason"] for d in result.get("degraded") or []]
    assert reasons == ["doc_task_dependency_cut"]


def test_alarm_coercion_leaves_doc_task_alone() -> None:
    tasks = [{"task_id": "t1", "agent": "doc_query", "sub_query": "알람 이벤트 대응 절차는?"}]
    assert _coerce_alarm_intent(tasks)[0]["agent"] == "doc_query"


# ── 4. 처리기 ─────────────────────────────────────────────────────────────────

class _Engine:
    def __init__(self, answer: DocAnswer):
        self.answer = answer
        self.calls: list[dict] = []

    async def __call__(self, query, collection_ids, **kwargs):
        self.calls.append({"query": query, "collections": list(collection_ids), **kwargs})
        return self.answer


@pytest.fixture
def engine(monkeypatch):
    def install(answer: DocAnswer) -> _Engine:
        fake = _Engine(answer)
        monkeypatch.setattr(dq, "answer_from_documents", fake)
        return fake
    return install


def _isolated(**extra) -> dict:
    return {"user_role": "user", "user_id": "kim", "thread_id": "th-1", "allowed_sources": None,
            **extra}


@pytest.mark.asyncio
async def test_handler_denied_by_source_axis_never_calls_engine(engine) -> None:
    fake = engine(DocAnswer("x"))
    res = await dq.run_doc_query({"sub_query": "q", "views": []},
                                 _isolated(allowed_sources=["apm"]), llm=None,
                                 app_config=_cfg(_rag()))
    assert is_access_denied_result(res) and fake.calls == []
    assert SOURCE_ACCESS_DENIED_MESSAGE in json.dumps(res, ensure_ascii=False)
    assert "문서" not in res.get("final_response", ""), "거부 문구에 소스 이름을 싣지 않는다"


@pytest.mark.asyncio
async def test_handler_uses_chosen_views_and_chat_audit(engine) -> None:
    fake = engine(DocAnswer("답\n\n---\n**참고 문서**\n1. A", citations=[MagicMock()], status="ok"))
    res = await dq.run_doc_query({"sub_query": "계정 신청은 누가 승인하나요?",
                                  "views": ["doc.hq_manual"]},
                                 _isolated(allowed_sources=["doc"]), llm="LLM",
                                 app_config=_cfg(_rag()))
    call = fake.calls[0]
    assert call["query"] == "계정 신청은 누가 승인하나요?" and call["collections"] == ["hq_manual"]
    assert call["llm"] == "LLM"
    assert call["audit_context"] == {"user_id": "kim", "thread_id": "th-1", "source": "chat"}
    assert res["final_response"].startswith("답") and "error" not in res
    assert res["source_status"][0]["status"] == "ok"
    assert res["doc_query"]["views"] == ["doc.hq_manual"]


@pytest.mark.asyncio
async def test_handler_without_views_searches_all_and_discloses(engine) -> None:
    fake = engine(DocAnswer("답", status="ok"))
    res = await dq.run_doc_query({"sub_query": "q", "views": []}, _isolated(), llm=None,
                                 app_config=_cfg(_rag()))
    assert fake.calls[0]["collections"] == ["hq_manual", "arch_docs"]
    assert res["final_response"].startswith("_문서군을 지정하지 않아 등록 문서군 전체(")
    assert res["doc_query"]["all_collections"] is True


@pytest.mark.parametrize("status", ["stale_id", "timeout", "error", "blocked_pii", "disabled"])
@pytest.mark.asyncio
async def test_handler_failure_keeps_engine_message(engine, status) -> None:
    engine(DocAnswer("색인이 갱신되어 조회할 수 없습니다", status=status, reason="사유"))
    res = await dq.run_doc_query({"sub_query": "q", "views": ["doc.hq_manual"]}, _isolated(),
                                 llm=None, app_config=_cfg(_rag()))
    assert res["final_response"] == "색인이 갱신되어 조회할 수 없습니다"
    assert res["error"] == "사유" and res["degraded_reason"] == f"doc_{status}"


@pytest.mark.asyncio
async def test_handler_empty_is_a_normal_answer(engine) -> None:
    engine(DocAnswer("찾지 못했습니다", status="empty"))
    res = await dq.run_doc_query({"sub_query": "q", "views": ["doc.hq_manual"]}, _isolated(),
                                 llm=None, app_config=_cfg(_rag()))
    assert "error" not in res and res["source_status"][0]["status"] == "empty"


@pytest.mark.asyncio
async def test_handler_passes_identity_to_collection_axis(engine, monkeypatch) -> None:
    seen: dict = {}

    def fake_allowed(collections, *, user, rag_config):
        seen["user"] = user
        return [c.id for c in collections]

    monkeypatch.setattr(dq, "allowed_collection_ids", fake_allowed)
    engine(DocAnswer("답"))
    await dq.run_doc_query({"sub_query": "q", "views": ["doc.hq_manual"]},
                           _isolated(user_role="admin", user_id="root"), llm=None,
                           app_config=_cfg(_rag()))
    assert seen["user"] == {"role": "admin", "sub": "root"}


def test_resolve_subagent_returns_doc_spec_when_active() -> None:
    spec = resolve_subagent("doc_query", _cfg(_rag()))
    assert spec is dq.DOC_QUERY_SPEC and spec.output_type == "text" and spec.key_facets_out == ()


# ── 5. 재계획 ─────────────────────────────────────────────────────────────────

def _state(task_plan, task_results) -> dict:
    state = create_initial_state(user_query="규정 질문")
    state["task_plan"] = task_plan
    state["task_results"] = task_results
    return state


@pytest.mark.asyncio
async def test_replanner_stops_doc_only_plan_without_llm(mock_config) -> None:
    llm = AsyncMock()
    llm.ainvoke.side_effect = AssertionError("문서 전용 계획은 평가 LLM 을 부르지 않는다")
    state = _state(
        [{"task_id": "t1", "agent": "doc_query", "sub_query": "q", "status": "completed"}],
        {"t1": {"final_response": "찾지 못했습니다"}},
    )
    out = await replanner(state, llm=llm, app_config=mock_config)
    assert out["needs_replan"] is False and llm.ainvoke.await_count == 0


@pytest.mark.asyncio
async def test_replanner_blocks_doc_research_followup(mock_config, caplog) -> None:
    caplog.set_level("INFO", logger="src.orchestration.replanner")
    mock_config.rag.enabled = True
    mock_config.rag.chat_routing_enabled = True
    content = json.dumps({"needs_followup": True, "reason": "문서 재확인", "new_tasks": [
        {"agent": "doc_query", "sub_query": "같은 규정 다시", "depends_on": [], "input_from": []}]},
        ensure_ascii=False)
    llm = AsyncMock()
    llm.ainvoke.return_value = MagicMock(content=content)
    state = _state(
        [{"task_id": "t1", "agent": "data_query", "sub_query": "서버", "status": "completed"},
         {"task_id": "t2", "agent": "doc_query", "sub_query": "규정", "status": "completed"}],
        {"t1": {"error": "timeout"}, "t2": {"final_response": "답"}},
    )
    out = await replanner(state, llm=llm, app_config=mock_config)
    assert llm.ainvoke.await_count == 1
    assert out["needs_replan"] is False and "task_plan" not in out
    # 문서 처리기가 활성이라 어휘 안에 남았고(폴백 치환 아님) 문서 재검색 가드가 끊었다
    assert "후속이 모두 문서 재검색" in caplog.text


# ── 6. 사용법 안내 ─────────────────────────────────────────────────────────────

def _usage(cfg, **state) -> str:
    base = {"allowed_db_ids": None, "user_role": "user", "allowed_sources": None}
    return gi._build_usage_answer({**base, **state}, cfg)


def test_usage_lists_doc_only_when_active_and_allowed() -> None:
    active = _usage(_cfg(_rag()))
    assert "사내 문서(규정·설계 근거)" in active and "- 사내 문서: 등록된 문서(" in active
    assert "사내 문서" not in _usage(_cfg(_rag(routing=False)))
    assert "사내 문서" not in _usage(_cfg(_rag()), allowed_sources=[])
    assert "사내 문서" not in _usage(_cfg(_rag()), allowed_sources=["apm"])
    assert "사내 문서" in _usage(_cfg(_rag()), allowed_sources=["doc"])
    inactive = _usage(_cfg(_rag(routing=False)))
    assert inactive == _usage(_cfg(None)), "비활성은 종전 안내문 그대로"


# ── 7. 관리 화면 후보 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_source_candidates_mark_doc_active() -> None:
    from src.api.routes.admin import list_observation_sources

    for rag, expected in ((_rag(), True), (_rag(routing=False), False), (None, False)):
        req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=_cfg(rag))))
        data = await list_observation_sources(req, {"sub": "root"})
        codes = {s["code"]: s for s in data["sources"]}
        assert codes["doc"]["active"] is expected and codes["doc"]["label"]
