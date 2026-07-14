"""deepagents 트랙 B 그래프 배선(wiring) 테스트 (Plan 49 §4.6/§7 step 7).

이 모듈은 "실제 deepagents 패키지 경로가 런타임 진입점에서 실제로 실행되도록 배선되었는가"를
검증한다. deepagents/vLLM 인프라 없이(패키지 미설치 환경에서도) 검증 가능하도록,
백엔드 선택·노드 조립을 mock으로 대체한다.

검증 대상:
- select_orchestration_backend="deep_agent" 시 graph가 field_mapper -> deep_agent -> END로 배선
- deepagents 패키지 미설치(조립 RuntimeError) 시 semantic_router 경로로 안전 폴백(회귀 없음)
- run_deep_agent 노드: 도구 미호출 시 마지막 메시지 폴백
- run_deep_agent 노드(step6): 도구 결과 collector → FabriX result_aggregator 재정리 (성공기준 5)
- run_deep_agent 노드: 조립 RuntimeError 시 그래프를 죽이지 않고 안내 응답
- _extract_final_response / _extract_ambient_state 헬퍼
- (실제 패키지 설치 시) create_deep_agent 런타임으로 collector → FabriX step6 실증, system_prompt 조립
"""

import os

import pytest

from src.config import (
    AppConfig,
    DBHubConfig,
    LLMConfig,
    OrchestratorConfig,
    QueryConfig,
    SecurityConfig,
    ServerConfig,
)
from src.graph import build_graph
import src.graph as graph_module
import src.orchestration.deep_agent as deep_agent_module
from src.orchestration.deep_agent import (
    _extract_ambient_state,
    _extract_final_response,
    run_deep_agent,
)


def _build_config(*, package: bool, semantic: bool) -> AppConfig:
    """트랙 B/시멘틱 플래그를 명시 설정한 테스트 config를 만든다.

    model_post_init가 환경변수를 읽으므로 관련 env를 제거하고 생성 후 명시 설정한다.
    """
    os.environ.pop("ENABLE_DEEPAGENT_ORCHESTRATION", None)
    os.environ.pop("ENABLE_SEMANTIC_ROUTING", None)
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        dbhub=DBHubConfig(server_url="http://localhost:9099/sse", source_name="infra_db", mcp_call_timeout=60),
        query=QueryConfig(max_retry_count=3, default_limit=1000),
        security=SecurityConfig(sensitive_columns=["password"], mask_pattern="***"),
        server=ServerConfig(host="0.0.0.0", port=8000),
        orchestrator=OrchestratorConfig(provider="vllm", base_url="http://vllm:8000/v1"),
        checkpoint_backend="sqlite",
        checkpoint_db_url=":memory:",
    )
    cfg.enable_deepagent_orchestration = False
    cfg.enable_semantic_routing = semantic
    cfg.enable_deepagents_package = package
    return cfg


def _node_names(compiled) -> set[str]:
    return set(compiled.get_graph().nodes.keys())


def _bound_partial(compiled, node_name):
    """컴파일된 그래프 노드에 바인딩된 functools.partial을 추출한다(동기/비동기 모두)."""
    import functools

    rc = compiled.nodes[node_name].bound
    target = rc.afunc if getattr(rc, "afunc", None) is not None else rc.func
    return target if isinstance(target, functools.partial) else None


def test_orchestration_result_aggregator_wired_with_synthesize(monkeypatch):
    """다중 의도 오케스트레이션(replanner) 경로의 result_aggregator는 synthesize=True로 배선된다(D-062).

    회귀 방지: 폐쇄망(deepagents 미설치)에서 실제 활성 경로는 deep_agent가 아니라
    intent_planner→agent_orchestrator→replanner→result_aggregator이다. 이 경로의
    result_aggregator가 synthesize 없이(기본 False) 배선되면 복합 task가 deterministic
    이어붙이기로 모순 이중 답변이 한 말풍선에 노출된다. 단일 LLM 합성을 강제한다.
    """
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")

    cfg = _build_config(package=False, semantic=False)
    cfg.enable_deepagent_orchestration = True
    compiled = build_graph(cfg)

    assert "result_aggregator" in _node_names(compiled)
    partial = _bound_partial(compiled, "result_aggregator")
    assert partial is not None
    assert partial.keywords.get("synthesize") is True


# ──────────────────────────────────────────────
# 그래프 배선: deep_agent 노드 등록 + 진입 경로
# ──────────────────────────────────────────────

def test_deep_agent_node_wired_when_selected(monkeypatch):
    """백엔드 선택=deep_agent + 조립 가능 시 deep_agent 노드가 그래프에 배선된다."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)

    cfg = _build_config(package=True, semantic=True)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    # deep_agent 노드가 등록되고, 트랙 A/시멘틱 노드는 (상호 배타로) 미등록
    assert "deep_agent" in names
    assert "semantic_router" not in names
    assert {"intent_planner", "agent_orchestrator"}.isdisjoint(names)


def test_deep_agent_edge_field_mapper_to_deep_agent(monkeypatch):
    """field_mapper -> deep_agent -> END 엣지가 구성된다(실제 실행 경로)."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)

    cfg = _build_config(package=True, semantic=False)
    compiled = build_graph(cfg)
    edges = compiled.get_graph().edges
    pairs = {(e.source, e.target) for e in edges}

    assert ("field_mapper", "deep_agent") in pairs
    assert any(src == "deep_agent" for src, _ in pairs)


def test_fallback_to_semantic_router_when_package_missing(monkeypatch):
    """선택=deep_agent이나 패키지 미설치(조립 불가) → semantic_router 경로 폴백(회귀 없음)."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "deep_agent")
    # 조립 불가(패키지 미설치) 시뮬레이션
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: False)

    cfg = _build_config(package=True, semantic=True)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    # deep_agent 미등록, 기존 semantic_router 경로 유지
    assert "deep_agent" not in names
    assert "semantic_router" in names


def test_no_deep_agent_when_backend_is_semantic_router(monkeypatch):
    """백엔드 선택=semantic_router면 deep_agent 노드 미등록(플래그 off 기본 동작)."""
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: "semantic_router")

    cfg = _build_config(package=False, semantic=True)
    compiled = build_graph(cfg)
    names = _node_names(compiled)

    assert "deep_agent" not in names
    assert "semantic_router" in names


# ──────────────────────────────────────────────
# run_deep_agent 노드 동작
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_deep_agent_no_tool_calls_uses_last_message(monkeypatch):
    """도구 미호출(오케스트레이터 직접 응답) 시 마지막 메시지를 폴백으로 사용한다."""
    captured = {}

    class _FakeAgent:
        async def ainvoke(self, payload):
            captured["payload"] = payload
            return {"messages": [{"role": "user", "content": "질의"},
                                 {"role": "assistant", "content": "최종 응답입니다"}]}

    def _fake_build(config, *, worker_llm=None, ambient_state=None, collector=None):
        captured["ambient"] = ambient_state
        # collector를 채우지 않음 → 도구 미호출 케이스
        return _FakeAgent()

    monkeypatch.setattr(deep_agent_module, "build_deep_agent", _fake_build)

    cfg = _build_config(package=True, semantic=False)
    state = {"user_query": "서버 목록 조회", "thread_id": "t1", "user_id": "u1"}
    out = await run_deep_agent(state, app_config=cfg)

    assert out["final_response"] == "최종 응답입니다"
    assert out["current_node"] == "deep_agent"
    assert captured["payload"]["messages"][0]["content"] == "서버 목록 조회"
    assert captured["ambient"]["thread_id"] == "t1"
    assert captured["ambient"]["user_id"] == "u1"


@pytest.mark.asyncio
async def test_run_deep_agent_step6_aggregates_via_fabrix(monkeypatch):
    """step6: 도구가 collector에 결과를 남기면 FabriX result_aggregator로 최종 응답 생성.

    오케스트레이터의 마지막 자유 서술은 사용되지 않아야 한다(성공기준 5).
    """
    captured = {}

    class _FakeAgent:
        async def ainvoke(self, payload):
            # 도구 실행을 시뮬레이션: collector에 원본 결과 적재
            self._collector.append(
                ({"task_id": "tool_data_query_1", "agent": "data_query", "order": 1,
                  "status": "completed", "sub_query": "서버 목록"},
                 {"organized_data": {"summary": "3개 서버", "rows": [{"hostname": "web-01"}],
                                     "is_sufficient": True}})
            )
            return {"messages": [{"role": "assistant", "content": "[오케스트레이터 자유서술 — 사용 금지]"}]}

    collector_ref = {}

    def _fake_build(config, *, worker_llm=None, ambient_state=None, collector=None):
        agent = _FakeAgent()
        agent._collector = collector
        collector_ref["c"] = collector
        return agent

    async def _fake_aggregator(state, *, llm=None, app_config=None, synthesize=False):
        captured["task_plan"] = state["task_plan"]
        captured["task_results"] = state["task_results"]
        captured["synthesize"] = synthesize
        summary = state["task_results"]["tool_data_query_1"]["organized_data"]["summary"]
        return {"final_response": f"[FabriX] {summary}", "current_node": "result_aggregator"}

    monkeypatch.setattr(deep_agent_module, "build_deep_agent", _fake_build)
    import importlib
    ra_mod = importlib.import_module("src.orchestration.result_aggregator")
    monkeypatch.setattr(ra_mod, "result_aggregator", _fake_aggregator)

    cfg = _build_config(package=True, semantic=False)
    out = await run_deep_agent({"user_query": "서버 목록 조회"}, app_config=cfg)

    # FabriX 재정리 결과가 사용되고, 오케스트레이터 자유 서술은 노출되지 않음
    assert out["final_response"] == "[FabriX] 3개 서버"
    assert "자유서술" not in out["final_response"]
    assert out["current_node"] == "deep_agent"
    # collector 원본 결과가 task_plan/task_results로 전달됨
    assert captured["task_plan"][0]["agent"] == "data_query"
    assert "tool_data_query_1" in captured["task_results"]
    # 딥 에이전트 경로는 단일 합성 모드로 aggregator를 호출한다(D-062)
    assert captured["synthesize"] is True


@pytest.mark.asyncio
async def test_run_deep_agent_safe_on_build_runtime_error(monkeypatch):
    """deepagents 미설치(RuntimeError) 시 노드가 죽지 않고 안내 응답을 반환한다."""
    def _raise(config, *, worker_llm=None, ambient_state=None, collector=None):
        raise RuntimeError("deepagents 패키지가 설치되지 않았습니다.")

    monkeypatch.setattr(deep_agent_module, "build_deep_agent", _raise)

    cfg = _build_config(package=True, semantic=False)
    out = await run_deep_agent({"user_query": "x"}, app_config=cfg)

    assert out["current_node"] == "deep_agent"
    assert "deepagents" in out["final_response"] or "사용할 수 없습니다" in out["final_response"]


# ──────────────────────────────────────────────
# 헬퍼: 응답/ambient 추출
# ──────────────────────────────────────────────

def test_extract_final_response_from_message_objects():
    """메시지 객체(content 속성)에서 마지막 AI 응답을 추출한다."""
    class _Msg:
        def __init__(self, content):
            self.content = content

    result = {"messages": [_Msg("질의"), _Msg("정리된 응답")]}
    assert _extract_final_response(result) == "정리된 응답"


def test_extract_final_response_content_blocks():
    """content가 블록 리스트면 텍스트를 이어붙여 추출한다."""
    result = {"messages": [{"content": [{"type": "text", "text": "부분1 "},
                                        {"type": "text", "text": "부분2"}]}]}
    assert _extract_final_response(result) == "부분1 부분2"


def test_extract_final_response_empty():
    """메시지가 없으면 안전한 기본 문구를 반환한다."""
    assert _extract_final_response({"messages": []}) == "응답을 생성할 수 없습니다."


def test_extract_ambient_state_filters_none():
    """ambient 추출은 None 값을 제외하고 식별/권한/양식 컨텍스트만 포함한다."""
    state = {
        "user_query": "q",
        "thread_id": "t1",
        "user_id": None,
        "allowed_db_ids": ["polestar"],
        "unrelated": "x",
    }
    ambient = _extract_ambient_state(state)
    assert ambient["thread_id"] == "t1"
    assert ambient["allowed_db_ids"] == ["polestar"]
    assert "user_id" not in ambient   # None 제외
    assert "unrelated" not in ambient  # 화이트리스트 외 제외


# ──────────────────────────────────────────────
# 실제 deepagents 패키지 통합 (설치 시에만 실행 — 폐쇄망 미설치 시 skip)
# ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_real_deepagents_collector_and_fabrix_step6(monkeypatch):
    """실제 create_deep_agent로 도구 호출 → collector 원본 적재 → FabriX 재정리(step6).

    오케스트레이터(가짜 tool-calling LLM)가 query_infra_db를 호출하고, 도구 내부
    handler(가짜 FabriX)가 원본 결과를 반환하면, collector가 원본을 적재하고
    run_deep_agent가 result_aggregator(FabriX)로 최종 응답을 만든다. 오케스트레이터의
    자유 서술은 노출되지 않는다(성공기준 5). 실제 패키지 런타임 표면 실증.
    """
    pytest.importorskip("deepagents")
    from langchain_core.messages import AIMessage
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class _FakeToolLLM(GenericFakeChatModel):
        def bind_tools(self, tools, **kw):
            return self

    msgs = iter([
        AIMessage(content="", tool_calls=[
            {"name": "query_infra_db", "args": {"sub_query": "서버 목록"}, "id": "c1"}]),
        AIMessage(content="[오케스트레이터 자유서술 — 사용 금지]"),
    ])
    fake_orch = _FakeToolLLM(messages=msgs)

    # data_query handler(=FabriX 파이프라인) 스텁: 원본 organized_data 반환
    from src.orchestration import subagents
    async def _fake_handler(task, isolated, *, llm, app_config):
        return {"organized_data": {"summary": "3개 서버 조회됨",
                                   "rows": [{"hostname": "web-01"}], "is_sufficient": True},
                "query_results": [{"hostname": "web-01"}]}
    monkeypatch.setitem(
        subagents.SUBAGENT_REGISTRY, "data_query",
        subagents.SubAgentSpec("data_query", "인프라 DB 조회", _fake_handler),
    )

    # 오케스트레이터 LLM 팩토리를 가짜로 (vLLM 불필요)
    import src.llm as llm_mod
    monkeypatch.setattr(llm_mod, "create_orchestrator_llm", lambda c: fake_orch)

    # result_aggregator(FabriX) 스텁: collector 원본이 도달했는지 확인
    import importlib
    ra_mod = importlib.import_module("src.orchestration.result_aggregator")
    async def _fake_agg(state, *, llm=None, app_config=None, synthesize=False):
        s = state["task_results"][state["task_plan"][0]["task_id"]]["organized_data"]["summary"]
        return {"final_response": f"[FabriX] {s}", "current_node": "result_aggregator"}
    monkeypatch.setattr(ra_mod, "result_aggregator", _fake_agg)

    cfg = _build_config(package=True, semantic=False)
    out = await run_deep_agent(
        {"user_query": "서버 목록 조회", "thread_id": "t1"},
        app_config=cfg, worker_llm=fake_orch,
    )

    assert out["final_response"] == "[FabriX] 3개 서버 조회됨"
    assert "자유서술" not in out["final_response"]
    assert out["current_node"] == "deep_agent"


def test_real_build_deep_agent_uses_system_prompt(monkeypatch):
    """실제 create_deep_agent가 system_prompt 인자로 정상 조립된다(0.6.10 시그니처 실측 반영).

    과거 `instructions=` 인자는 0.6.10에 존재하지 않아 TypeError를 유발했다.
    """
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

    class _FakeToolLLM(GenericFakeChatModel):
        def bind_tools(self, tools, **kw):
            return self

    import src.llm as llm_mod
    monkeypatch.setattr(llm_mod, "create_orchestrator_llm",
                        lambda c: _FakeToolLLM(messages=iter([])))

    from src.orchestration.deep_agent import build_deep_agent
    cfg = _build_config(package=True, semantic=False)
    agent = build_deep_agent(cfg, worker_llm=_FakeToolLLM(messages=iter([])))
    # 컴파일된 LangGraph 에이전트(ainvoke 보유)가 반환된다
    assert hasattr(agent, "ainvoke")
