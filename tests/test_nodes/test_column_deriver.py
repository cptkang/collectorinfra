"""단계적 컬럼 도출 루프 단위 테스트 (Plan 67 Phase S2 / D-128).

실 LLM을 부르지 않는다(D-127) — 고정 tool-call 시퀀스를 재생하는 결정적 목 LLM으로
[요구 분해 → 도구 탐색 → SMQ 누적] 전 구간과 가드(라운드·tool 호출 상한, 전체 타임아웃,
tool-calling 미지원, 미등록 도구, 파싱 실패)를 검증한다.

검증 축:
    1. 정상 완주 — 분해 1콜 + 도구 왕복 + 최종 SMQ 파싱, 계측(라운드·tool·llm) 일치.
    2. 미해결 필드 — 구조화 사유가 레코드에 남고 SMQ는 비운다(침묵 폴백 금지).
    3. 가드 — 라운드 상한·tool 호출 상한·전체 타임아웃에서 예외 없이 사유와 함께 종료.
    4. 강등 — bind_tools 미지원·미등록 도구 호출·최종 JSON 부재.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from src.nodes import column_deriver as cd
from src.nodes.semantic_compiler import load_semantic_model

_DB_ID = "polestar_cm_gp"
_QUERY = "서버별 OS종류와 벤더를 지난달 기준으로 조회해줘"

_DECOMPOSED = json.dumps({
    "fields": [
        {"field": "OS종류", "role_hint": "dimension"},
        {"field": "벤더", "role_hint": "dimension"},
        {"field": "지난달", "role_hint": "time"},
    ]
}, ensure_ascii=False)

_FINAL_OK = json.dumps({
    "smq": {"pattern": "A", "resource_types": ["server.Server"],
            "dimensions": ["OSType", "Vendor"]},
    "fields": [
        {"field": "OS종류", "role": "dimension", "selection": "OSType",
         "evidence": "search_catalog 정확 일치", "confidence": 0.95},
        {"field": "벤더", "role": "dimension", "selection": "Vendor",
         "evidence": "search_catalog 정확 일치", "confidence": 0.9},
    ],
    "unresolved": [],
}, ensure_ascii=False)


def _model() -> dict:
    """실 카탈로그(오프라인 로드)를 도구 주입 재료로 쓴다."""
    model = load_semantic_model(_DB_ID)
    assert model, "시맨틱 모델 로드 실패 — 오프라인 카탈로그 확인"
    return model


def _tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


class _ScriptedLLM:
    """고정 응답 시퀀스를 재생하는 결정적 목 LLM.

    ``bind_tools``는 자신을 돌려주므로 분해 호출과 루프 호출이 같은 큐를 소비한다 —
    스크립트 순서가 곧 [분해, 라운드1, 라운드2, …]다. 응답이 예외 인스턴스면 그것을 raise한다.
    """

    def __init__(self, responses, *, bind_error: Exception | None = None, delay: float = 0.0):
        self._responses = list(responses)
        self._bind_error = bind_error
        self._delay = delay
        self.bound_tools = None
        self.calls: list[list] = []

    def bind_tools(self, tools):
        if self._bind_error is not None:
            raise self._bind_error
        self.bound_tools = list(tools)
        return self

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        if self._delay:
            await asyncio.sleep(self._delay)
        if not self._responses:
            raise AssertionError("목 응답이 소진됐다(스크립트 부족)")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response() if callable(response) else response


def _tool_messages(messages: list) -> list[str]:
    """메시지 목록에서 ToolMessage 본문만 뽑는다."""
    return [m.content for m in messages if isinstance(m, ToolMessage)]


# ──────────────────────────────────────────────
# 1. 정상 완주
# ──────────────────────────────────────────────

async def test_full_loop_derives_smq_with_evidence():
    """분해 → 도구 탐색 → 최종 SMQ까지 완주하고 계측·근거를 남긴다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content="", tool_calls=[
            _tool_call("search_catalog", {"term": "OS종류"}, "t1"),
            _tool_call("resolve_time_range", {"query": "지난달"}, "t2"),
        ]),
        AIMessage(content=_FINAL_OK),
    ])

    record = await cd.derive_smq(
        llm, _QUERY, _DB_ID, _model(),
        deps=cd.StepwiseDeps(path="single"),
    )

    assert record["stopped_reason"] == cd.STOP_COMPLETED
    assert record["smq"]["dimensions"] == ["OSType", "Vendor"]
    assert record["unresolved"] == []
    assert record["rounds"] == 2
    assert record["tool_calls"] == 2
    assert record["llm_calls"] == 3          # 분해 1 + 루프 2
    assert record["path"] == "single" and record["db_id"] == _DB_ID
    assert record["elapsed_ms"] >= 0.0
    assert record["covered"] is None         # 커버리지 판정은 호출부(컴파일러) 몫

    # 도구가 실제로 실행돼 결과가 다음 라운드 입력에 들어갔다.
    tool_bodies = _tool_messages(llm.calls[-1])
    assert any("OSType" in body for body in tool_bodies)
    assert any("resolved" in body for body in tool_bodies)


async def test_catalog_is_not_inlined_in_loop_prompt():
    """루프 프롬프트는 카탈로그 전문을 싣지 않는다(도구로 확인하는 것이 이 경로의 취지)."""
    llm = _ScriptedLLM([AIMessage(content=_DECOMPOSED), AIMessage(content=_FINAL_OK)])
    await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    loop_system = llm.calls[-1][0].content
    assert "[카탈로그]" not in loop_system
    assert "search_catalog" in loop_system


async def test_decompose_failure_continues_with_raw_query():
    """요구 분해 실패는 루프를 막지 않고 원문으로 진행한다(사유는 로그)."""
    llm = _ScriptedLLM([RuntimeError("분해 실패"), AIMessage(content=_FINAL_OK)])
    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["stopped_reason"] == cd.STOP_COMPLETED
    assert record["llm_calls"] == 1          # 실패한 분해 호출은 계측하지 않는다
    assert "분해 결과 없음" in llm.calls[-1][-1].content


# ──────────────────────────────────────────────
# 2. 미해결 필드 — 구조화 사유
# ──────────────────────────────────────────────

async def test_unresolved_fields_are_reported_without_smq():
    """카탈로그로 해소 못한 필드는 사유와 함께 남고 SMQ는 비운다."""
    final = json.dumps({
        "smq": None,
        "fields": [],
        "unresolved": [{"field": "유사 사양", "reason": "카탈로그에 대응 항목 없음"}],
    }, ensure_ascii=False)
    llm = _ScriptedLLM([AIMessage(content=_DECOMPOSED), AIMessage(content=final)])

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["smq"] is None
    assert record["unresolved"] == [
        {"field": "유사 사양", "reason": "카탈로그에 대응 항목 없음"}
    ]
    assert record["stopped_reason"] == cd.STOP_COMPLETED


async def test_null_smq_without_reason_still_gets_structured_reason():
    """smq=null인데 사유가 없으면 사유를 만들어 넣는다(침묵 금지)."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content=json.dumps({"smq": None, "fields": [], "unresolved": []})),
    ])
    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["smq"] is None
    assert len(record["unresolved"]) == 1
    assert "SMQ" in record["unresolved"][0]["reason"]


# ──────────────────────────────────────────────
# 3. 가드 — 상한·타임아웃
# ──────────────────────────────────────────────

async def test_max_rounds_guard_stops_loop():
    """도구만 계속 부르면 라운드 상한에서 사유와 함께 종료한다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        *[
            AIMessage(content="", tool_calls=[_tool_call("resolve_limit", {"query": "상위 10"}, f"t{i}")])
            for i in range(5)
        ],
    ])

    record = await cd.derive_smq(
        llm, _QUERY, _DB_ID, _model(),
        limits=cd.StepwiseLimits(max_rounds=3, max_tool_calls=10, timeout_seconds=10.0),
    )

    assert record["stopped_reason"] == cd.STOP_MAX_ROUNDS
    assert record["rounds"] == 3
    assert record["smq"] is None
    assert any("상한 도달" in u["reason"] for u in record["unresolved"])


async def test_max_tool_calls_guard_sends_finalize_note():
    """tool 호출 상한에 닿으면 초과 호출을 실행하지 않고 마감을 지시한다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content="", tool_calls=[
            _tool_call("resolve_limit", {"query": "상위 10"}, "t1"),
            _tool_call("resolve_time_range", {"query": "지난달"}, "t2"),
        ]),
        AIMessage(content=_FINAL_OK),
    ])

    record = await cd.derive_smq(
        llm, _QUERY, _DB_ID, _model(),
        limits=cd.StepwiseLimits(max_rounds=4, max_tool_calls=1, timeout_seconds=10.0),
    )

    assert record["tool_calls"] == 1          # 상한 초과 호출은 실행되지 않았다
    bodies = _tool_messages(llm.calls[-1])
    assert any("한도 도달" in b for b in bodies)
    assert any("[마감]" in getattr(m, "content", "") for m in llm.calls[-1])
    # 마감 지시 후 최종 JSON이 오면 정상 완주로 되돌린다.
    assert record["stopped_reason"] == cd.STOP_COMPLETED
    assert record["smq"]["pattern"] == "A"


async def test_tool_budget_exhausted_without_conclusion_keeps_reason():
    """상한 도달 후에도 결론이 안 오면 tool 상한 사유가 유지된다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content="", tool_calls=[
            _tool_call("resolve_limit", {"query": "상위 10"}, "t1"),
            _tool_call("resolve_limit", {"query": "상위 20"}, "t2"),
        ]),
        AIMessage(content="", tool_calls=[_tool_call("resolve_limit", {"query": "전체"}, "t3")]),
    ])

    record = await cd.derive_smq(
        llm, _QUERY, _DB_ID, _model(),
        limits=cd.StepwiseLimits(max_rounds=2, max_tool_calls=1, timeout_seconds=10.0),
    )

    assert record["stopped_reason"] == cd.STOP_MAX_TOOL_CALLS
    assert record["tool_calls"] == 1
    assert record["smq"] is None


async def test_overall_timeout_guard_returns_partial_metrics():
    """per-call이 아니라 루프 전체 타임아웃으로 중단하고 부분 계측을 보고한다."""
    llm = _ScriptedLLM(
        [
            AIMessage(content=_DECOMPOSED),
            *[
                AIMessage(content="", tool_calls=[_tool_call("resolve_limit", {"query": "전체"}, f"t{i}")])
                for i in range(20)
            ],
        ],
        delay=0.02,
    )

    record = await cd.derive_smq(
        llm, _QUERY, _DB_ID, _model(),
        limits=cd.StepwiseLimits(max_rounds=100, max_tool_calls=100, timeout_seconds=0.08),
    )

    assert record["stopped_reason"] == cd.STOP_TIMEOUT
    assert record["smq"] is None
    assert any("타임아웃" in u["reason"] for u in record["unresolved"])
    assert record["rounds"] >= 1              # 중단 시점까지의 계측이 남는다
    assert record["elapsed_ms"] > 0


# ──────────────────────────────────────────────
# 4. 강등 경로
# ──────────────────────────────────────────────

async def test_bind_tools_unsupported_degrades_with_reason():
    """tool-calling 미지원 LLM은 사유와 함께 강등한다(예외 전파 없음)."""
    llm = _ScriptedLLM([], bind_error=NotImplementedError("tool calling 미지원"))

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["stopped_reason"] == cd.STOP_TOOL_BINDING_UNSUPPORTED
    assert record["llm_calls"] == 0
    assert "tool-calling 미지원" in record["unresolved"][0]["reason"]


async def test_unknown_tool_call_is_reported_to_llm():
    """미등록 도구 호출은 사유를 LLM에 돌려주고 루프는 계속한다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content="", tool_calls=[_tool_call("write_todos", {}, "t1")]),
        AIMessage(content=_FINAL_OK),
    ])

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["stopped_reason"] == cd.STOP_COMPLETED
    assert any("등록되지 않은 도구" in b for b in _tool_messages(llm.calls[-1]))


async def test_final_response_without_json_is_parse_error():
    """최종 응답에 JSON이 없으면 파싱 실패 사유로 종료한다."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        AIMessage(content="설명만 하고 JSON을 안 냈다"),
    ])

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["stopped_reason"] == cd.STOP_PARSE_ERROR
    assert record["smq"] is None
    assert record["unresolved"]


async def test_no_tools_available_degrades(monkeypatch):
    """도구가 하나도 없으면 루프를 시작하지 않는다(방어 가드)."""
    from src.tools import binding

    monkeypatch.setattr(binding, "build_query_tools", lambda ctx: [])
    llm = _ScriptedLLM([])

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    assert record["stopped_reason"] == cd.STOP_NO_TOOLS
    assert record["llm_calls"] == 0


async def test_tool_failure_is_surfaced_not_swallowed():
    """도구 실행 실패는 사유를 LLM에 돌려준다(예외 전파·침묵 금지)."""
    llm = _ScriptedLLM([
        AIMessage(content=_DECOMPOSED),
        # search_catalog는 term:str을 받는다 — dict를 주면 도구 검증이 실패한다.
        AIMessage(content="", tool_calls=[_tool_call("search_catalog", {"term": {"bad": 1}}, "t1")]),
        AIMessage(content=_FINAL_OK),
    ])

    record = await cd.derive_smq(llm, _QUERY, _DB_ID, _model())

    bodies = _tool_messages(llm.calls[-1])
    assert any("도구 실패" in b or "error" in b for b in bodies)
    assert record["stopped_reason"] == cd.STOP_COMPLETED


# ──────────────────────────────────────────────
# 5. 도구 목록 — 주입 재료에 따른 노출
# ──────────────────────────────────────────────

async def test_tool_list_reflects_injected_deps():
    """주입된 재료에 따라 도구 목록이 늘어난다(없는 재료의 도구는 노출하지 않는다)."""
    llm = _ScriptedLLM([AIMessage(content=_DECOMPOSED), AIMessage(content=_FINAL_OK)])
    await cd.derive_smq(llm, _QUERY, _DB_ID, _model(), deps=cd.StepwiseDeps(path="single"))
    minimal = {t.name for t in llm.bound_tools}
    assert {"search_catalog", "check_smq_coverage", "resolve_time_range",
            "resolve_limit", "classify_metric_field"} <= minimal
    assert "lookup_synonym" not in minimal
    assert "search_value_index" not in minimal

    llm2 = _ScriptedLLM([AIMessage(content=_DECOMPOSED), AIMessage(content=_FINAL_OK)])
    await cd.derive_smq(
        llm2, _QUERY, _DB_ID, _model(),
        deps=cd.StepwiseDeps(
            path="single",
            synonyms={"cmm_resource.name": ["서버명"]},
            value_index={"resource_type": ["server.Server"]},
            schema_info={"tables": {"cmm_resource": {"columns": []}}},
        ),
    )
    enriched = {t.name for t in llm2.bound_tools}
    assert {"lookup_synonym", "search_value_index", "validate_sql_draft"} <= enriched


def test_limits_from_config_clamps_minimums():
    """상한 설정은 최소 1로 클램프한다(0·음수로 루프가 무의미해지는 것 방지)."""
    from src.config import Text2SQLConfig

    limits = cd.StepwiseLimits.from_config(Text2SQLConfig(
        stepwise_max_rounds=0, stepwise_max_tool_calls=-3, stepwise_timeout_seconds=1.5,
    ))
    assert (limits.max_rounds, limits.max_tool_calls, limits.timeout_seconds) == (1, 1, 1.5)


@pytest.mark.parametrize("field_name", ["stepwise_derivation"])
def test_flag_defaults_off(field_name):
    """신규 플래그는 기본 OFF다(옵트인 증분)."""
    from src.config import Text2SQLConfig

    assert getattr(Text2SQLConfig(), field_name) is False
