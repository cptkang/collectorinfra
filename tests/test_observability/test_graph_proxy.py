"""`_TracedGraph` 프록시 계약 테스트 (D-141).

프록시가 LangGraph 내부 동작을 깨면 그래프 전체가 불능이 되므로, 위임 계약을
구현보다 **먼저** 고정한다. 위임이 하나라도 어긋나면 여기서 잡힌다.
"""

from __future__ import annotations

import pytest
from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from src.observability import trace_collector as tc
from src.observability.graph_proxy import TracedGraph


class _State(TypedDict, total=False):
    request_id: str
    hits: list


@pytest.fixture(autouse=True)
def _clean():
    tc.reset_all()
    yield
    tc.reset_all()


def _build(traced: bool):
    """동일한 그래프를 프록시 유무만 바꿔 만든다."""
    raw = StateGraph(_State)
    g = TracedGraph(raw) if traced else raw

    async def a(state):
        return {"hits": state.get("hits", []) + ["a"]}

    async def b(state):
        return {"hits": state.get("hits", []) + ["b"]}

    g.add_node("a", a)
    g.add_node("b", b)
    g.add_edge(START, "a")
    g.add_conditional_edges("a", lambda s: "b", {"b": "b"})
    g.add_edge("b", END)
    return g.compile()


class TestDelegation:
    def test_compile_returns_runnable_graph(self):
        compiled = _build(traced=True)
        assert hasattr(compiled, "ainvoke")

    async def test_same_result_as_unwrapped(self):
        """프록시 유무가 실행 결과를 바꾸지 않는다."""
        plain = await _build(traced=False).ainvoke({"hits": []})
        wrapped = await _build(traced=True).ainvoke({"hits": []})

        assert plain["hits"] == wrapped["hits"] == ["a", "b"]

    def test_add_edge_and_conditional_edges_delegate(self):
        """엣지 API가 원본에 그대로 전달된다 (누락되면 compile이 실패한다)."""
        _build(traced=True)  # 예외 없이 컴파일되면 위임 성공

    def test_unknown_attribute_delegates(self):
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        assert g.state_schema is raw.state_schema
        assert g.nodes is raw.nodes
        assert g.edges is raw.edges

    def test_entry_and_finish_point_delegate(self):
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        async def n(state):
            return {}

        g.add_node("n", n)
        g.set_entry_point("n")
        g.set_finish_point("n")

        assert hasattr(g.compile(), "ainvoke")

    def test_underlying_graph_is_exposed(self):
        raw = StateGraph(_State)
        assert TracedGraph(raw).raw is raw


class TestIntrospectionTransparency:
    """래핑이 배선 검사를 가로막지 않아야 한다.

    회귀 고정: `traced`가 `functools.partial`을 감싸면서
    `test_deep_agent_wiring.py`의 배선 검증(partial.keywords 검사)이 **조용히 무력화**됐다
    (2026-08-19 기준선 대조에서 검출). 관측 계층 추가만으로 다른 안전망이 꺼지면 안 된다.
    """

    def test_wrapper_exposes_original_via_unwrap(self):
        import inspect
        from functools import partial

        def node(state, *, synthesize=False):
            return {}

        original = partial(node, synthesize=True)
        wrapped = tc.traced(original, name="n")

        assert inspect.unwrap(wrapped) is original

    def test_partial_keywords_survive_wrapping(self):
        """배선 인자(keywords)를 래핑 너머에서 읽을 수 있다."""
        import inspect
        from functools import partial

        async def node(state, *, synthesize=False):
            return {}

        wrapped = tc.traced(partial(node, synthesize=True), name="n")

        assert inspect.unwrap(wrapped).keywords.get("synthesize") is True

    def test_registered_node_is_introspectable(self):
        """그래프에 등록된 뒤에도 배선 인자를 확인할 수 있다."""
        import inspect
        from functools import partial

        raw = StateGraph(_State)
        g = TracedGraph(raw)

        async def agg(state, *, synthesize=False):
            return {}

        g.add_node("result_aggregator", partial(agg, synthesize=True))
        g.add_edge(START, "result_aggregator")
        g.add_edge("result_aggregator", END)
        compiled = g.compile()

        rc = compiled.nodes["result_aggregator"].bound
        target = rc.afunc if getattr(rc, "afunc", None) is not None else rc.func

        assert inspect.unwrap(target).keywords.get("synthesize") is True


class TestTracing:
    async def test_nodes_are_traced(self):
        compiled = _build(traced=True)
        tc.start_request("req1")

        await compiled.ainvoke({"request_id": "req1", "hits": []})

        nodes = [s.node for s in tc.steps_for("req1")]
        assert "a" in nodes and "b" in nodes

    async def test_conditional_node_is_traced(self):
        """조건부로 등록되는 노드도 자동 편입된다 (개별 배선 불필요)."""
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        async def only_when_enabled(state):
            return {"hits": ["cond"]}

        g.add_node("cond", only_when_enabled)
        g.add_edge(START, "cond")
        g.add_edge("cond", END)
        compiled = g.compile()

        tc.start_request("req1")
        await compiled.ainvoke({"request_id": "req1"})

        assert "cond" in [s.node for s in tc.steps_for("req1")]

    async def test_no_request_id_means_no_collection(self):
        compiled = _build(traced=True)

        result = await compiled.ainvoke({"hits": []})

        assert result["hits"] == ["a", "b"]
        assert tc.active_request_count() == 0


class TestDisabled:
    async def test_disabled_proxy_is_bit_identical(self):
        """`enabled=False`면 원본 함수를 그대로 등록한다 (비트동일)."""
        raw = StateGraph(_State)
        g = TracedGraph(raw, enabled=False)

        async def a(state):
            return {"hits": ["a"]}

        g.add_node("a", a)
        g.add_edge(START, "a")
        g.add_edge("a", END)
        compiled = g.compile()

        tc.start_request("req1")
        await compiled.ainvoke({"request_id": "req1"})

        assert tc.steps_for("req1") == []


class TestRealGraphWiring:
    """실제 `build_graph`가 프록시를 쓰는지 확인한다 (배선 누락 차단)."""

    def test_build_graph_uses_traced_graph(self):
        src = __import__("pathlib").Path("src/graph.py").read_text(encoding="utf-8")

        assert "TracedGraph" in src, "build_graph가 프록시를 쓰지 않음"
        assert src.count("StateGraph(AgentState)") == 1, (
            "StateGraph 생성 지점이 여러 곳이면 프록시 배선이 비대칭이 된다"
        )


class TestProxyEdgeCases:
    """프록시의 나머지 분기 — 미검증 상태로 두면 조용히 썩는다."""

    async def test_single_argument_add_node_uses_function_name(self):
        """LangGraph의 `add_node(action)` 형태에서도 노드명을 잃지 않는다."""
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        async def my_node(state):
            return {"hits": ["x"]}

        g.add_node(my_node)
        g.add_edge(START, "my_node")
        g.add_edge("my_node", END)
        compiled = g.compile()

        tc.start_request("req1")
        await compiled.ainvoke({"request_id": "req1"})

        assert "my_node" in [s.node for s in tc.steps_for("req1")]

    def test_wrapping_failure_falls_back_to_original(self, monkeypatch):
        """래핑이 실패해도 그래프 빌드는 계속된다 (관측만 포기)."""
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        def _boom(fn, *, name):
            raise RuntimeError("wrap broken")

        monkeypatch.setattr("src.observability.graph_proxy.traced", _boom)

        async def node(state):
            return {"hits": ["ok"]}

        g.add_node("n", node)  # 예외 전파 없음
        g.add_edge(START, "n")
        g.add_edge("n", END)

        assert hasattr(g.compile(), "ainvoke")

    def test_setattr_delegates_to_raw_graph(self):
        """속성 설정이 원본으로 전달된다 (프록시가 상태를 따로 갖지 않는다)."""
        raw = StateGraph(_State)
        g = TracedGraph(raw)

        g.some_marker = 42

        assert getattr(raw, "some_marker") == 42
        assert g.some_marker == 42
