"""그래프 노드 반환 키 ⊆ 그 그래프 상태 스키마 키 — 전 노드 일괄 정합 (plans/181 W1-B · 스멜 N6).

LangGraph는 노드 반환 dict에서 그래프 채널(상태·입력·출력 스키마 키의 합집합)에 없는 키를
**예외 없이 버린다**(`StateGraph.attach_node`의 `_get_updates` — `k in output_keys` 필터).
`docs/18` 2026-08-26: `form_memory_panel`을 result_aggregator 반환 dict에만 넣고 `AgentState`에
선언하지 않아 패널이 조용히 사라졌다. 기존 테스트는 키 하나씩 `in AgentState.__annotations__`로
본다(`test_plan67_phase0_core` 등) — 이 파일은 등록된 **모든 노드**를 한 번에 본다.

## 노드 집합 — 실제 배선에서 얻는다

`src.graph.StateGraph`를 기록용 하위 클래스로 바꿔 `build_graph`를 사다리 단·옵트인 조합별로
빌드하고, `add_node`로 등록된 (그래프 상태 스키마, 노드 이름, 등록 객체)를 모은다. 메인 그래프
(`AgentState`)와 3단 task 서브그래프(`TaskRunState` · 출력 `TaskOutcomeState`)가 모두 같은
경로로 잡힌다. 등록 객체는 `functools.partial`(`.func`)·트레이싱 프록시(`traced`의
`__wrapped__`)를 벗겨 실제 함수를 찾고, partial 키워드로 주입된 함수(`authorized_router`의
`inner=semantic_router`)도 그 노드의 함수로 본다.

빌드 조합(`_BUILDS`): 1·2·4단은 옵트인 5종 모두 on, 3단은 모두 on(계획 루프 등록 → 순차 러너
미등록)과 계획 루프만 off(순차 러너 등록) 두 가지 — 노드 등록을 바꾸는 분기 전수를 덮는다
(`test_plan166_w0_graph_topology.py` 옵션 축과 같은 5종). 트레이싱은 켜서 프록시 래핑 벗기기를
실제로 거친다. 1단은 백엔드 판정(네트워크)·deepagents 조립을 대역으로 고정한다 — 실 LLM·
네트워크·DB 접속 0(그래프 조립만).

## 반환 키 추출 규칙 (AST · 문자열 상수 키만)

노드 함수 소스를 AST로 읽고, 중첩 함수·람다·클래스 본문을 뺀 자기 본문의 `return <식>`에서:

- dict 리터럴 `{"k": v}`의 문자열 상수 키 · `{**x}` 펼침은 `x`를 같은 규칙으로 따라간다
- 반환되는 지역 이름 `n`: `n = {...}`/`n: T = {...}` 대입 · `n["k"] = …`(증분 대입 포함) ·
  `n.update({...})`/`n.update(k=v)` · `n.setdefault("k", …)`의 리터럴 키
- `await`·조건식(`a if c else b`)은 안쪽 식을 따라간다
- `dict(x, k=v)`는 `x`의 키와 키워드 이름
- **같은 모듈** 헬퍼 호출 `helper(...)`의 반환(직접 반환 · 지역 이름 대입 · `**helper(...)`)은
  `_MAX_HELPER_DEPTH`(3)단계까지 따라간다. 헬퍼 매개변수는 호출부 인자 식으로 치환한다 —
  `return result`·`{**result, …}`·`out = dict(result)`처럼 받은 dict를 감싸 돌려주는 통과
  헬퍼(결과 집계기의 단일 통과점 체인)를 지나 노드 본문 dict의 키까지 닿는다

한계(의도적으로 추적하지 않는다): 비리터럴 키(`n[var] = …`) · 다른 모듈 헬퍼의 반환 ·
4단계 이상 헬퍼 · 반환하지 않는 헬퍼가 인자 dict를 제자리 수정하는 경우(`_normalize(result)`) ·
루프 변수·매개변수로 받은 호출 대상(`await step(...)`·`await inner(state)`) ·
`Command(update=…)`(현재 노드 반환에 없음) · 반환 이름과 별칭(`a = n` 뒤 `a["k"] = …`).
이 범위의 미선언 키는 이 테스트가 못 잡는다 — 공허 통과는 `test_extractor_*` 양성 단언으로 막는다.

## 현재 위반

`KNOWN_UNDECLARED`에 (노드 함수 한정 이름, 키) → 근거로 고정한다. 제품 코드는 고치지 않는다
(행동 변경 후보 — plans/181 §5). 예외 집합 항목이 더 이상 위반이 아니면 테스트가 실패한다
(낡은 예외 제거 강제).
"""

from __future__ import annotations

import ast
import functools
import inspect
import textwrap
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph

import src.graph as graph_module
from src.config import AppConfig, LLMConfig, OrchestratorConfig

#: 사다리 단 → (deepagents, intent_orchestration, semantic_routing). 상위 단 플래그도 켜 둔다.
_TIERS: dict[str, tuple[bool, bool, bool]] = {
    "deep_agent": (True, True, True),
    "intent_orchestration": (False, True, True),
    "semantic_router": (False, False, True),
    "legacy": (False, False, False),
}

_OPTIONS: frozenset[str] = frozenset({"approval", "fault_dx", "plan_loop", "seq", "probe"})

#: (사다리 단, 켤 옵트인) — 노드 등록을 바꾸는 분기 전수를 덮는 최소 조합
_BUILDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("deep_agent", _OPTIONS),
    ("intent_orchestration", _OPTIONS),
    ("semantic_router", _OPTIONS),
    ("semantic_router", _OPTIONS - {"plan_loop"}),
    ("legacy", _OPTIONS),
)

#: (노드 함수 `모듈.한정이름`, 반환 리터럴 키) → 근거. 비면 위반 0. 셋 다 그래프 경로에서는
#: LangGraph가 버리는 키다(행동 변경 후보 — plans/181 §5).
KNOWN_UNDECLARED: dict[tuple[str, str], str] = {
    ("src.orchestration.agent_orchestrator.agent_orchestrator", "sufficiency_shortfalls"): (
        "2단 그래프 노드 — 버려진다. 병기 유지·2027-03-09 폐기(state.py dependency_notes 주석 · "
        "D-161 ①) · 실제 전달은 dependency_notes(NOTE_SUFFICIENCY). replanner가 "
        "state.get으로 읽지만 그래프 경로에서는 늘 비어 있다"
    ),
    ("src.nodes.multi_db_executor.multi_db_executor", "skipped_dbs"): (
        "2단 서브에이전트가 함수를 직접 불러 반환 dict에서 읽는다(subagents.py → task_results). "
        "3단 그래프·task 서브그래프에서는 버려진다 — 미조회 사유는 dependency_notes가 운반"
    ),
    ("src.nodes.multi_db_executor.multi_db_executor", "regen_stops"): (
        "의도된 폐기 — 2단 핸들러만 regen_stop으로 접고, 그래프 경로(3단)는 상태 스키마에 없어 "
        "버려진다고 반환부 주석에 명시(plans/119) · 사용자 사유는 db_errors가 운반"
    ),
}

#: 헬퍼 추적 깊이 — 노드 함수(0)에서 같은 모듈 헬퍼 3단계까지. 실측 최소값이다: 2단 결과 집계기는
#: 노드 본문 dict를 `_with_answer_history(_apply_incomplete_notice(out))`로 감싸 그 안이 다시
#: `_apply_dependency_notes(out)`(2단계)로 넘기고, 2단 의도 분해기는 `_plan_turn`(1) →
#: `_llm_plan`(2) → `_on_path`(3)로 반환한다. 현재 코드에서 3단계와 4단계의 추출 결과가
#: 같다(고정점).
_MAX_HELPER_DEPTH = 3


# ──────────────────────────────────────────────
# 노드 수집
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class _NodeRecord:
    """등록된 노드 1건 — 그래프 라벨·노드 이름·실제 함수·그 그래프의 상태 스키마 키."""

    graph: str
    node: str
    fn: Callable[..., Any]
    schema_keys: frozenset[str]


def _schema_keys(schema: Any) -> frozenset[str]:
    """TypedDict 스키마의 선언 키(상속 포함)."""
    return frozenset(schema.__required_keys__ | schema.__optional_keys__)


def _graph_keys(graph: StateGraph) -> frozenset[str]:
    """그래프 채널이 되는 키 — 상태·입력·출력 스키마 키의 합집합."""
    return (
        _schema_keys(graph.state_schema)
        | _schema_keys(graph.input_schema)
        | _schema_keys(graph.output_schema)
    )


def _unwrap_functions(action: Any) -> list[Callable[..., Any]]:
    """등록 객체에서 partial·`__wrapped__`를 벗겨 실제 함수(들)를 찾는다.

    partial 키워드로 주입된 함수·partial(예: `inner=semantic_router`)도 노드 함수로 본다.
    컴파일된 서브그래프 등 함수가 아닌 객체는 건너뛴다.
    """
    found: list[Callable[..., Any]] = []
    stack = [action]
    while stack:
        obj = stack.pop()
        if isinstance(obj, functools.partial):
            stack.append(obj.func)
            stack.extend(
                v for v in obj.keywords.values()
                if isinstance(v, functools.partial) or inspect.isfunction(v)
            )
        elif inspect.isfunction(obj) and hasattr(obj, "__wrapped__"):
            stack.append(obj.__wrapped__)
        elif inspect.isfunction(obj):
            found.append(obj)
    return found


def _config(tier: str, on: frozenset[str]) -> AppConfig:
    """사다리·옵트인 플래그와 LLM 설정을 전부 명시한 테스트 config(`.env` 누수 차단)."""
    deep, intent, semantic = _TIERS[tier]
    cfg = AppConfig(
        llm=LLMConfig(provider="ollama", model="llama3.1:8b"),
        orchestrator=OrchestratorConfig(
            provider="vllm", base_url="http://127.0.0.1:9/v1", model="Qwen3.5-9B",
        ),
    )
    cfg.worker_provider_override = None
    cfg.enable_deepagents_package = deep
    cfg.enable_intent_orchestration = intent
    cfg.enable_semantic_routing = semantic
    cfg.enable_sql_approval = "approval" in on
    cfg.noise_gate.fault_diagnosis_enabled = "fault_dx" in on
    cfg.tier3_plan_loop_enabled = "plan_loop" in on
    cfg.composite.sequential_fallback_tiers_enabled = "seq" in on
    cfg.cross_system_probe_enabled = "probe" in on
    cfg.observability.trace_enabled = True
    return cfg


def _collect_nodes(monkeypatch: pytest.MonkeyPatch) -> list[_NodeRecord]:
    """`_BUILDS` 조합마다 그래프를 빌드해 등록 노드를 모은다(그래프 라벨 = 상태 스키마 이름)."""
    seen: dict[tuple[str, str, Callable[..., Any]], _NodeRecord] = {}

    class _RecordingStateGraph(StateGraph):  # type: ignore[type-arg]
        """`add_node` 호출을 기록하는 `StateGraph`."""

        def add_node(self, node: Any, action: Any = None, **kwargs: Any) -> Any:
            label = self.state_schema.__name__
            keys = _graph_keys(self)
            for fn in _unwrap_functions(action):
                seen.setdefault((label, str(node), fn), _NodeRecord(label, str(node), fn, keys))
            return super().add_node(node, action, **kwargs)

    for tier, on in _BUILDS:
        with monkeypatch.context() as mp:
            mp.setattr(graph_module, "StateGraph", _RecordingStateGraph)
            mp.setattr(
                graph_module, "select_orchestration_backend",
                lambda c, _d=_TIERS[tier][0]: "deep_agent" if _d else "semantic_router",
            )
            mp.setattr(graph_module, "_deep_agent_buildable", lambda c, llm: True)
            ladder: list[str] = []
            mp.setattr(
                graph_module, "record_ladder_resolution",
                lambda t, r, _l=ladder, **_: _l.append(t.value),
            )
            graph_module.build_graph(_config(tier, on), checkpointer=InMemorySaver())
            assert ladder == [tier], (tier, ladder)
    return list(seen.values())


@pytest.fixture(scope="module")
def node_records() -> Iterator[list[_NodeRecord]]:
    """전 조합의 등록 노드(모듈 1회 빌드 — monkeypatch는 빌드 뒤 원복)."""
    mp = pytest.MonkeyPatch()
    try:
        yield _collect_nodes(mp)
    finally:
        mp.undo()


# ──────────────────────────────────────────────
# 반환 키 추출 (AST)
# ──────────────────────────────────────────────


@functools.cache
def _function_ast(fn: Callable[..., Any]) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """함수 소스의 정의 노드."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)), fn
    return node


@functools.cache
def _own_nodes(fn: Callable[..., Any]) -> tuple[ast.AST, ...]:
    """함수 자기 본문의 AST 노드 — 중첩 함수·람다·클래스 본문은 들어가지 않는다."""
    found: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(_function_ast(fn)))
    while stack:
        node = stack.pop()
        found.append(node)
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            stack.extend(ast.iter_child_nodes(node))
    return tuple(found)


def _str_const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


#: 이름 추적 중복 방지 — (프레임, 지역 이름). 호출 사슬을 따라 내려가며 쌓는다.
_Visiting = frozenset[tuple["_Frame", str]]


class _Frame:
    """함수 1개의 반환 리터럴 키 추출 프레임.

    `bindings`는 헬퍼 매개변수 → (호출부 프레임, 인자 식)이다. 헬퍼가 매개변수를 그대로·고쳐서
    돌려주면(`return result` · `{**result, …}` · `out = dict(result)`) 그 키를 호출부 인자 식에서
    찾는다 — 결과 집계기의 단일 통과점 체인이 노드 본문 dict를 감싸 돌려주는 형태다.
    """

    def __init__(
        self,
        fn: Callable[..., Any],
        depth: int = 0,
        bindings: dict[str, tuple[_Frame, ast.AST]] | None = None,
    ) -> None:
        self.fn = fn
        self.depth = depth
        self.bindings = bindings or {}

    def returned_keys(self, visiting: _Visiting = frozenset()) -> set[str]:
        keys: set[str] = set()
        for node in _own_nodes(self.fn):
            if isinstance(node, ast.Return) and node.value is not None:
                keys |= self.expr_keys(node.value, visiting)
        return keys

    def _helper(self, call: ast.Call) -> Callable[..., Any] | None:
        """같은 모듈에 정의된 이름 호출이면 그 함수(추적 깊이 안일 때만)."""
        if self.depth >= _MAX_HELPER_DEPTH or not isinstance(call.func, ast.Name):
            return None
        target = self.fn.__globals__.get(call.func.id)
        target = inspect.unwrap(target) if callable(target) else None
        if inspect.isfunction(target) and target.__module__ == self.fn.__module__:
            return target
        return None

    def _enter(self, helper: Callable[..., Any], call: ast.Call) -> _Frame:
        """헬퍼 프레임 — 위치·키워드 인자를 매개변수에 묶는다(`*args` 펼침은 건너뛴다)."""
        args = _function_ast(helper).args
        params = [a.arg for a in (*args.posonlyargs, *args.args)]
        bindings: dict[str, tuple[_Frame, ast.AST]] = {}
        for param, arg in zip(params, call.args, strict=False):
            if isinstance(arg, ast.Starred):
                break
            bindings[param] = (self, arg)
        for kw in call.keywords:
            if kw.arg is not None:
                bindings[kw.arg] = (self, kw.value)
        return _Frame(helper, self.depth + 1, bindings)

    def expr_keys(self, expr: ast.AST, visiting: _Visiting) -> set[str]:
        if isinstance(expr, ast.Await):
            return self.expr_keys(expr.value, visiting)
        if isinstance(expr, ast.IfExp):
            return self.expr_keys(expr.body, visiting) | self.expr_keys(expr.orelse, visiting)
        if isinstance(expr, ast.Dict):
            keys: set[str] = set()
            for k, v in zip(expr.keys, expr.values, strict=True):
                if k is None:
                    keys |= self.expr_keys(v, visiting)
                elif (s := _str_const(k)) is not None:
                    keys.add(s)
            return keys
        if isinstance(expr, ast.Name):
            return self.name_keys(expr.id, visiting)
        if isinstance(expr, ast.Call):
            if isinstance(expr.func, ast.Name) and expr.func.id == "dict":
                keys = {kw.arg for kw in expr.keywords if kw.arg is not None}
                for arg in expr.args:
                    keys |= self.expr_keys(arg, visiting)
                return keys
            if (helper := self._helper(expr)) is not None:
                return self._enter(helper, expr).returned_keys(visiting)
        return set()

    def name_keys(self, name: str, visiting: _Visiting) -> set[str]:
        """지역 이름 `name`에 쌓이는 리터럴 키(대입·첨자 대입·update·setdefault·매개변수 인자)."""
        if (self, name) in visiting:
            return set()
        visiting = visiting | {(self, name)}
        keys: set[str] = set()
        if name in self.bindings:
            caller, arg = self.bindings[name]
            keys |= caller.expr_keys(arg, visiting)
        for node in _own_nodes(self.fn):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    keys |= self._target_keys(target, node.value, name, visiting)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                keys |= self._target_keys(node.target, node.value, name, visiting)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == name
            ):
                if node.func.attr == "update":
                    for arg in node.args:
                        keys |= self.expr_keys(arg, visiting)
                    keys |= {kw.arg for kw in node.keywords if kw.arg is not None}
                elif node.func.attr == "setdefault" and node.args:
                    if (s := _str_const(node.args[0])) is not None:
                        keys.add(s)
        return keys

    def _target_keys(
        self, target: ast.AST, value: ast.AST, name: str, visiting: _Visiting,
    ) -> set[str]:
        if isinstance(target, ast.Name) and target.id == name:
            return self.expr_keys(value, visiting)
        if (
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == name
            and (s := _str_const(target.slice)) is not None
        ):
            return {s}
        return set()


def _qualname(fn: Callable[..., Any]) -> str:
    return f"{fn.__module__}.{fn.__qualname__}"


def _returned_keys(fn: Callable[..., Any]) -> set[str]:
    return _Frame(fn).returned_keys()


def _violations(records: list[_NodeRecord]) -> dict[tuple[str, str], set[str]]:
    """(노드 함수 한정 이름, 미선언 키) → 그 키를 버리는 (그래프:노드) 목록."""
    found: dict[tuple[str, str], set[str]] = {}
    for rec in records:
        for key in _returned_keys(rec.fn) - rec.schema_keys:
            found.setdefault((_qualname(rec.fn), key), set()).add(f"{rec.graph}:{rec.node}")
    return found


# ──────────────────────────────────────────────
# 추출기 자체 검증 — 공허 통과 차단
# ──────────────────────────────────────────────


def _keys_of(records: list[_NodeRecord], graph: str, node: str) -> set[str]:
    keys: set[str] = set()
    for rec in records:
        if rec.graph == graph and rec.node == node:
            keys |= _returned_keys(rec.fn)
    return keys


def test_extractor_collects_nodes_from_wiring(node_records: list[_NodeRecord]) -> None:
    """실제 배선에서 메인 그래프·task 서브그래프 노드가 하한 이상 잡힌다."""
    main = {r.node for r in node_records if r.graph == "AgentState"}
    sub = {r.node for r in node_records if r.graph == "TaskRunState"}
    # 실측(2026-10-16): 메인 32 · 서브 11 · (그래프, 노드, 함수) 44건
    assert len(main) >= 32, sorted(main)
    assert len(sub) >= 11, sorted(sub)
    assert {"output_generator", "approval_gate", "deep_agent", "plan", "finalize"} <= main
    assert {"task_prompt", "pack_outcome", "result_organizer"} <= sub
    # partial 키워드로 주입된 라우터 본체도 노드 함수로 잡힌다
    router_fns = {r.fn.__name__ for r in node_records if r.node == "semantic_router"}
    assert {"authorized_router", "semantic_router"} <= router_fns


def test_extractor_finds_known_return_keys(node_records: list[_NodeRecord]) -> None:
    """알려진 노드의 반환 키가 실제로 추출된다(추출기가 0개를 내는 공허 통과 차단)."""
    aggregator = _keys_of(node_records, "AgentState", "result_aggregator")
    # docs/18 2026-08-26 사고 키 — 노드 본문 `out[...] =`가 통과 헬퍼 체인을 지나 잡힌다
    assert {"final_response", "form_memory_panel", "messages", "dependency_notes"} <= aggregator
    assert "final_response" in _keys_of(node_records, "AgentState", "output_generator")
    assert {"task_plan", "is_composite"} <= _keys_of(node_records, "AgentState", "intent_planner")
    assert "parsed_requirements" in _keys_of(node_records, "AgentState", "input_parser")
    assert "active_db_id" in _keys_of(node_records, "AgentState", "semantic_router")
    assert "task_outcomes" in _keys_of(node_records, "TaskRunState", "pack_outcome")
    # 모든 등록 노드 함수에서 키가 1개 이상 잡힌다 — 새 노드가 추출기가 못 읽는 형태로 반환하면
    # 여기서 실패한다(추출 규칙을 넓히거나 반환 형태를 리터럴로 드러내라).
    opaque = sorted({f"{r.graph}:{r.node}" for r in node_records if not _returned_keys(r.fn)})
    assert not opaque, opaque


def test_extractor_rules_on_synthetic_function() -> None:
    """추출 규칙(리터럴·펼침·첨자 대입·update·setdefault·dict()·헬퍼 3단계·통과 헬퍼)을 고정한다."""
    keys = _returned_keys(_synthetic_node)
    assert keys == {"a", "b", "c", "d", "e", "f", "g", "h1", "h2", "h3", "cond_x", "cond_y"}, keys
    # 통과 헬퍼: 노드 본문 dict(`base`)가 헬퍼 2단계를 거쳐 돌아와도 잡히고, 헬퍼가 더한 키도 잡힌다
    assert _returned_keys(_synthetic_passthrough_node) == {"base", "p0", "p2"}


def _synthetic_helper() -> dict[str, Any]:
    out = {"h1": 1}
    return {**out, **_synthetic_helper2()}


def _synthetic_helper2() -> dict[str, Any]:
    return {"h2": 1, **_synthetic_helper3()}


def _synthetic_helper3() -> dict[str, Any]:
    return {"h3": 1, **_synthetic_helper4()}


def _synthetic_helper4() -> dict[str, Any]:
    return {"h4_beyond_depth": 1}


async def _synthetic_node(flag: bool, dyn: str) -> dict[str, Any]:
    def _nested() -> dict[str, Any]:
        return {"nested_ignored": 1}

    if flag:
        return {"cond_x": 1} if dyn else {"cond_y": 2}
    result: dict[str, Any] = {"a": 1, **{"b": 2}}
    result["c"] = 3
    result[dyn] = 4
    result.update({"d": 5})
    result.update(e=6)
    result.setdefault("f", 7)
    result.update(dict(g=8))
    result.update(_synthetic_helper())
    return result


def _synthetic_pass0(result: dict[str, Any]) -> dict[str, Any]:
    res = dict(result)
    res["p0"] = 1
    return res


def _synthetic_pass1(result: dict[str, Any]) -> dict[str, Any]:
    return _synthetic_pass2(result)


def _synthetic_pass2(result: dict[str, Any]) -> dict[str, Any]:
    return {**result, "p2": 1}


def _synthetic_passthrough_node() -> dict[str, Any]:
    out = {"base": 1}
    return _synthetic_pass1(_synthetic_pass0(out))


# ──────────────────────────────────────────────
# 정합
# ──────────────────────────────────────────────


def test_node_return_keys_declared_in_graph_state(node_records: list[_NodeRecord]) -> None:
    """등록 노드의 반환 리터럴 키 ⊆ 그 그래프 상태 스키마 키(알려진 예외 제외)."""
    violations = _violations(node_records)
    new = {k: sorted(v) for k, v in violations.items() if k not in KNOWN_UNDECLARED}
    assert not new, (
        "그래프 상태 스키마에 없는 키를 반환하는 노드 — LangGraph가 조용히 버린다"
        f"(docs/18 2026-08-26). 상태 스키마에 선언하라: {new}"
    )


def test_known_undeclared_are_still_violations(node_records: list[_NodeRecord]) -> None:
    """예외 집합 항목이 더 이상 위반이 아니면 실패한다(낡은 예외 제거 강제)."""
    violations = _violations(node_records)
    stale = sorted(k for k in KNOWN_UNDECLARED if k not in violations)
    assert not stale, f"해소된 예외를 KNOWN_UNDECLARED에서 지워라: {stale}"
