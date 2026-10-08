"""plans/146 W3 — 앞 턴 0행 지시어 승계(F6 · G-3 (a)).

내부망 3회차 ITAM-106: t1 「통합인증 서비스 서버 목록」이 0행인데 t2 「그 서버들 지원 종료일」이
조건 없는 조인으로 3,748행(전체)을 답했다. 끊긴 지점은 2단 경로에서 앞 턴 조건이 다음 턴에 하나도
닿지 않은 것이다 — top-level `generated_sql`은 2단에서 비어 있고(`previous_sql` = ""), 0행이라
결과 요약·엔티티도 비어 계획 맥락이 「직전 대상 서버/장비: (없음)」뿐이었다.

체크포인터(AsyncSqliteSaver) 2턴으로 두 경로를 고정한다.
- 2단(기준): context_resolver → 계획 맥락 캡처 → 가짜 오케스트레이터 → 실제 result_aggregator
- 3·4단 그래프: context_resolver → 가짜 조회 파이프라인 → 실제 output_generator
"""

from __future__ import annotations

import importlib
import os
import tempfile
from functools import partial
from typing import Any
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, StateGraph

from src.config import load_config
from src.nodes.context_resolver import context_resolver
from src.nodes.query_generator import _build_user_prompt
from src.orchestration.intent_planner import _build_context_block
from src.orchestration.result_aggregator import result_aggregator
from src.state import AgentState, create_followup_input, create_initial_state
from src.utils.empty_antecedent import (
    EMPTY_ANTECEDENT_KEY,
    ZERO_ROW_TURN_KEY,
    empty_antecedent_note,
    resolve_empty_antecedent,
    widened_beyond_antecedent,
)

T1_QUERY = "자산관리에서 통합인증 서비스 서버 목록 보여줘"
T1_SQL = (
    "SELECT s.`서버호스트명` FROM `t_server` AS s "
    "WHERE s.`용도내용` LIKE '%통합인증 서비스%' LIMIT 10000"
)
T2_QUERY = "그 서버들 지원 종료일도 알려줘"
#: 앞 턴 조건을 잇지 않은 조인(3회차 실측 모양) — WHERE 없음
T2_WIDE_SQL = (
    "SELECT a.`서버호스트명`, a.`지원종료일` FROM `t_eos` AS a "
    "JOIN `t_server` AS b ON a.`서버호스트명` = b.`서버호스트명` LIMIT 10000"
)
#: 앞 턴 조건을 이은 조인
T2_CARRIED_SQL = (
    "SELECT a.`서버호스트명`, a.`지원종료일` FROM `t_eos` AS a "
    "JOIN `t_server` AS b ON a.`서버호스트명` = b.`서버호스트명` "
    "WHERE b.`용도내용` LIKE '%통합인증 서비스%' LIMIT 10000"
)
NOTE_HEAD = "[안내] 앞 질문("
#: 모듈 객체(`src.nodes` 패키지가 같은 이름의 함수를 재노출해 `from … import`는 함수를 준다)
og = importlib.import_module("src.nodes.output_generator")

_ROWS = [{"hostname": "web01", "eos": "20301231"}, {"hostname": "web02", "eos": "20291231"}]


def _task_result(sql: str, rows: list[dict[str, Any]], db_id: str = "itam") -> dict[str, Any]:
    return {
        "organized_data": {"summary": "", "rows": rows, "is_sufficient": True},
        "query_results": rows,
        "target_db_ids": [db_id],
        "db_origin": "classified",
        "generated_sql": sql,
        "executed_sqls": [{"db_id": db_id, "sql": sql}],
    }


TurnSpec = tuple[str, str, list[dict[str, Any]]] | tuple[str, dict[str, dict[str, Any]]]


async def _run_tier2(turns: list[TurnSpec]) -> tuple[list, list]:
    """2단 축약 그래프를 체크포인터 위에서 턴마다 돌린다 — (계획 맥락 캡처, 턴 출력).

    턴은 `(질의, SQL, 행)`(단일 task) 또는 `(질의, {task_id: 결과})`(복합 계획)이다.
    """
    seen: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    async def planner(state: AgentState) -> dict:
        ctx = state.get("conversation_context")
        seen.append({"ctx": ctx, "block": _build_context_block(ctx, state["user_query"])})
        return {"task_plan": [
            {"task_id": tid, "agent": "data_query", "sub_query": state["user_query"],
             "order": i, "depends_on": [], "input_from": []}
            for i, tid in enumerate(current["results"], 1)
        ]}

    async def orch(state: AgentState) -> dict:
        return {"task_results": current["results"]}

    async def agg(state: AgentState) -> dict:
        return await result_aggregator(
            state, llm=MagicMock(), app_config=load_config(),
            synthesize=True, composite_answer="steps",
        )

    g = StateGraph(AgentState)
    g.add_node("context_resolver", context_resolver)
    g.add_node("planner", planner)
    g.add_node("orch", orch)
    g.add_node("agg", agg)
    g.set_entry_point("context_resolver")
    g.add_edge("context_resolver", "planner")
    g.add_edge("planner", "orch")
    g.add_edge("orch", "agg")
    g.add_edge("agg", END)
    outs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as d:
        async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "c.db")) as saver:
            app = g.compile(checkpointer=saver)
            cfg = {"configurable": {"thread_id": "w3"}}
            for i, spec in enumerate(turns):
                query = spec[0]
                current["results"] = (
                    {"t1": _task_result(spec[1], spec[2])} if len(spec) == 3 else spec[1]
                )
                inp = (create_initial_state(query, thread_id="w3") if i == 0
                       else create_followup_input(query))
                outs.append(await app.ainvoke(inp, cfg))
    return seen, outs


async def _fake_summary(config, state, **_kw) -> str:
    rows = state["organized_data"]["rows"]
    if not rows:
        return "조건에 해당하는 데이터가 없습니다."
    return f"표 {len(rows)}행"


async def _run_graph_path(
    turns: list[tuple[str, str, list[dict[str, Any]], list[dict[str, Any]]]],
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """3·4단 그래프 축약(context_resolver → 가짜 조회 → 실제 output_generator) 턴 실행."""
    monkeypatch.setattr(og, "_generate_text_response", _fake_summary)
    current: dict[str, Any] = {}

    async def pipeline(state: AgentState) -> dict:
        rows = current["rows"]
        return {
            "parsed_requirements": {
                "original_query": state["user_query"], "output_format": "text",
                "filter_conditions": current["filters"], "query_targets": [],
            },
            "generated_sql": current["sql"],
            "query_attempts": [{"sql": current["sql"], "success": True}],
            "query_results": rows,
            "organized_data": {"summary": "", "rows": rows, "is_sufficient": True},
            "active_db_id": "polestar",
            "error_message": None,
        }

    g = StateGraph(AgentState)
    g.add_node("context_resolver", context_resolver)
    g.add_node("pipeline", pipeline)
    g.add_node(
        "output_generator",
        partial(og.output_generator, llm=MagicMock(), app_config=load_config()),
    )
    g.set_entry_point("context_resolver")
    g.add_edge("context_resolver", "pipeline")
    g.add_edge("pipeline", "output_generator")
    g.add_edge("output_generator", END)
    outs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as d:
        async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "c.db")) as saver:
            app = g.compile(checkpointer=saver)
            cfg = {"configurable": {"thread_id": "w3g"}}
            for i, (query, sql, rows, filters) in enumerate(turns):
                current.update(sql=sql, rows=rows, filters=filters)
                inp = (create_initial_state(query, thread_id="w3g") if i == 0
                       else create_followup_input(query))
                outs.append(await app.ainvoke(inp, cfg))
    return outs


# ── 단위: 판정 함수 ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_resolve_requires_demonstrative_and_record(self) -> None:
        prior = {ZERO_ROW_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL]}}
        assert resolve_empty_antecedent(T2_QUERY, prior, []) == {
            "query": T1_QUERY, "sqls": [T1_SQL],
        }
        assert resolve_empty_antecedent("지원 종료일 알려줘", prior, []) is None  # 지시어 없음
        assert resolve_empty_antecedent(T2_QUERY, {}, []) is None  # 앞 턴 기록 없음(행 있음)
        # 앞 턴이 식별자를 지목했으면 대상은 그 식별자다
        assert resolve_empty_antecedent(
            T2_QUERY, prior, [{"field": "hostname", "value": "web01"}]
        ) is None

    def test_widened_detection(self) -> None:
        ante = {"query": T1_QUERY, "sqls": [T1_SQL]}
        assert widened_beyond_antecedent(ante, [T2_WIDE_SQL]) is True
        assert widened_beyond_antecedent(ante, [T2_CARRIED_SQL]) is False
        # 재표현(핵심어만 남김)은 이은 것으로 본다
        assert widened_beyond_antecedent(
            ante, [T2_WIDE_SQL.replace("LIMIT", "WHERE b.`용도내용` LIKE '%통합인증%' LIMIT")]
        ) is False
        # 앞 턴 조건에 문자열 값이 없으면 판정하지 않는다
        assert widened_beyond_antecedent(
            {"query": "q", "sqls": ["SELECT a FROM t WHERE cpu > 90"]}, [T2_WIDE_SQL]
        ) is False


# ── 2단(기준 경로) ───────────────────────────────────────────────────────────


class TestTier2:
    async def test_zero_then_demonstrative_carries_condition_and_notifies(self) -> None:
        """(a) 앞 턴 0행 + 「그 서버들 …」 → 조건 승계 · 다시 0행이면 안내."""
        seen, outs = await _run_tier2([(T1_QUERY, T1_SQL, []), (T2_QUERY, T2_CARRIED_SQL, [])])
        # 1턴 끝 — 0행 조회 기록이 맥락에 남는다
        assert outs[0]["conversation_context"][ZERO_ROW_TURN_KEY] == {
            "query": T1_QUERY, "sqls": [T1_SQL],
        }
        ctx = seen[1]["ctx"]
        assert ctx[EMPTY_ANTECEDENT_KEY] == {"query": T1_QUERY, "sqls": [T1_SQL]}
        # 2단은 top-level SQL이 비어 있다 — 기록의 SQL로 채워 SQL 생성 프롬프트가 잇는다
        assert ctx["previous_sql"] == T1_SQL
        assert ctx["previous_results_summary"] == "0건 조회됨"
        assert ctx["previous_entities"] == []
        assert outs[1]["demonstrative_without_antecedent"] is False
        block = seen[1]["block"]
        assert f"직전 질의 「{T1_QUERY}」의 조회 결과가 0건" in block
        assert "조건 없이 전체 대상으로 넓히지 말 것" in block
        prompt = _build_user_prompt(
            {"original_query": T2_QUERY}, None, None, None, conversation_context=ctx,
        )
        assert "## 이전 대화의 SQL (참조용)" in prompt and "통합인증 서비스" in prompt
        # 다시 0행 → 결정적 안내
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1
        assert empty_antecedent_note(ctx[EMPTY_ANTECEDENT_KEY], widened=False) in (
            outs[1]["final_response"]
        )

    async def test_widened_rows_are_shown_with_notice_first(self) -> None:
        """(a) 조건 없이 전체로 넓힌 조회(3회차 3,748행 모양) — 행은 보이고 맨 앞에 안내(교정 1)."""
        _seen, outs = await _run_tier2([(T1_QUERY, T1_SQL, []), (T2_QUERY, T2_WIDE_SQL, _ROWS)])
        ante = {"query": T1_QUERY, "sqls": [T1_SQL]}
        note = empty_antecedent_note(ante, widened=True)
        assert outs[1]["final_response"].startswith(note + "\n\n")
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1
        assert "web01" in outs[1]["final_response"]
        assert len(outs[1]["query_results"]) == 2
        # 행이 보였으므로 다음 턴 지시어는 그 행을 가리킨다 — 0행 기록을 남기지 않는다
        assert ZERO_ROW_TURN_KEY not in (outs[1]["conversation_context"] or {})

    async def test_carried_rows_are_kept(self) -> None:
        """조건을 이어 행이 나오면 그대로 답한다(안내 없음)."""
        _seen, outs = await _run_tier2(
            [(T1_QUERY, T1_SQL, []), (T2_QUERY, T2_CARRIED_SQL, _ROWS)]
        )
        assert NOTE_HEAD not in outs[1]["final_response"]
        assert len(outs[1]["query_results"]) == 2
        assert ZERO_ROW_TURN_KEY not in (outs[1]["conversation_context"] or {})

    async def test_requery_then_gated_dependent_notifies(self) -> None:
        """MLX 실측 계획 모양 — 조건 재조회(t1 · 0행) → 지원 종료일(t2 · 선행 0건 게이트 미실행)."""
        gated = {"error": "선행 작업(t1)의 결과가 0건이라 이 단계를 실행하지 않았습니다.",
                 "skipped": True, "skip_reason": "prior_empty"}
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, []),
            (T2_QUERY, {"t1": _task_result(T1_SQL, []), "t2": gated}),
        ])
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1
        assert outs[1]["conversation_context"][ZERO_ROW_TURN_KEY]["query"] == T1_QUERY

    async def test_requery_then_widened_dependent_notifies_first(self) -> None:
        """재조회 task가 조건을 이어도, 행을 낸 후속 task가 조건 없이 넓혔으면 맨 앞에 안내한다."""
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, []),
            (T2_QUERY, {"t1": _task_result(T1_SQL, []), "t2": _task_result(T2_WIDE_SQL, _ROWS)}),
        ])
        assert outs[1]["final_response"].startswith(
            empty_antecedent_note({"query": T1_QUERY, "sqls": [T1_SQL]}, widened=True)
        )
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1
        assert outs[1]["query_results"]

    async def test_rows_then_demonstrative_is_unchanged(self) -> None:
        """(b) 앞 턴이 행을 돌려줬으면 종전 동작 — 기록·승계·안내 없음(폴스타 서버 행 모양)."""
        seen, outs = await _run_tier2([
            ("김포 운영 서버 목록", "SELECT hostname FROM cmm_resource WHERE x = 'y'", _ROWS),
            ("그 서버들 CPU 사용률", "SELECT hostname, cpu FROM m", _ROWS),
        ])
        assert ZERO_ROW_TURN_KEY not in (outs[0].get("conversation_context") or {})
        ctx = seen[1]["ctx"]
        assert EMPTY_ANTECEDENT_KEY not in ctx
        assert ctx["previous_sql"] == ""  # 2단 top-level SQL은 종전대로 비어 있다
        assert {"field": "hostname", "value": "web01"} in ctx["previous_entities"]
        assert seen[1]["block"].endswith(
            "- 사용자가 명시적으로 다른 위치/DB/대상을 지정하면 그 신호를 최우선으로 따르라"
            "(승계하지 말 것).\n"
        )
        assert "- 직전 대상 서버/장비: hostname=web01" in seen[1]["block"]
        assert NOTE_HEAD not in outs[1]["final_response"]

    async def test_zero_then_non_demonstrative_is_unchanged(self) -> None:
        """(c) 지시어 없는 후속 → 종전 동작(승계 맥락·안내 없음)."""
        seen, outs = await _run_tier2(
            [(T1_QUERY, T1_SQL, []), ("자산관리 서버 지원 종료일 알려줘", T2_WIDE_SQL, _ROWS)]
        )
        ctx = seen[1]["ctx"]
        assert EMPTY_ANTECEDENT_KEY not in ctx
        assert ctx["previous_sql"] == ""
        assert ctx["previous_results_summary"] == ""
        assert "직전 질의" not in seen[1]["block"]
        assert NOTE_HEAD not in outs[1]["final_response"]
        assert len(outs[1]["query_results"]) == 2


# ── 3·4단 그래프 경로(폴스타 단일 DB) 대칭 ─────────────────────────────────────


_PS_T1 = "김포 운영 서버 중 CPU 95% 넘는 서버"
_PS_T1_SQL = "SELECT hostname FROM r WHERE zone = 'gimpo' AND cpu > 95"
_PS_T2 = "그 서버들 메모리 사용률도"
_PS_T2_WIDE = "SELECT hostname, mem FROM r"
_PS_T2_CARRIED = "SELECT hostname, mem FROM r WHERE zone = 'gimpo' AND cpu > 95"


class TestGraphPathSymmetry:
    async def test_zero_then_demonstrative_notifies(self, monkeypatch) -> None:
        outs = await _run_graph_path(
            [(_PS_T1, _PS_T1_SQL, [], []), (_PS_T2, _PS_T2_CARRIED, [], [])], monkeypatch,
        )
        assert outs[0]["conversation_context"][ZERO_ROW_TURN_KEY]["sqls"] == [_PS_T1_SQL]
        assert outs[1]["conversation_context"][EMPTY_ANTECEDENT_KEY]["query"] == _PS_T1
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1
        assert outs[1]["demonstrative_without_antecedent"] is False

    async def test_widened_rows_are_shown_with_notice_first(self, monkeypatch) -> None:
        outs = await _run_graph_path(
            [(_PS_T1, _PS_T1_SQL, [], []), (_PS_T2, _PS_T2_WIDE, _ROWS, [])], monkeypatch,
        )
        ante = {"query": _PS_T1, "sqls": [_PS_T1_SQL]}
        assert outs[1]["final_response"] == (
            f"{empty_antecedent_note(ante, widened=True)}\n\n표 2행"
        )
        assert len(outs[1]["query_results"]) == 2
        assert ZERO_ROW_TURN_KEY not in outs[1]["conversation_context"]

    async def test_rows_then_demonstrative_is_unchanged(self, monkeypatch) -> None:
        """(b) 폴스타 앞 턴 행 있음 → 종전과 같은 응답 · 맥락 기록 없음."""
        outs = await _run_graph_path(
            [(_PS_T1, _PS_T1_SQL, _ROWS, []), (_PS_T2, _PS_T2_WIDE, _ROWS, [])], monkeypatch,
        )
        assert ZERO_ROW_TURN_KEY not in (outs[0].get("conversation_context") or {})
        assert EMPTY_ANTECEDENT_KEY not in outs[1]["conversation_context"]
        assert outs[1]["final_response"] == "표 2행"
        assert len(outs[1]["query_results"]) == 2

    async def test_named_server_zero_rows_keeps_identifier(self, monkeypatch) -> None:
        """앞 턴이 서버를 지목해 0행이면 대상은 그 서버다 — 빈 집합으로 보지 않는다(종전 동작)."""
        named = [{"field": "hostname", "op": "=", "value": "web09"}]
        outs = await _run_graph_path(
            [("web09 CPU 사용률", "SELECT cpu FROM m WHERE hostname = 'web09'", [], named),
             ("그 서버 메모리도", "SELECT mem FROM m", [], [])],
            monkeypatch,
        )
        ctx = outs[1]["conversation_context"]
        assert EMPTY_ANTECEDENT_KEY not in ctx
        assert ctx["previous_entities"] == [{"field": "hostname", "value": "web09"}]
        assert NOTE_HEAD not in outs[1]["final_response"]


# ── 독립 검증(verifier · plans/146 W3) ─────────────────────────────────────────
#
# 검증에서 찾은 결함(H-2 · M-1 · M-2 · M-4)은 교정 1에서 고쳐 xfail 표지를 걷었다. 넓힘은 행을
# 숨기지 않고 맨 앞에 안내하는 설계로 바뀌어(교정 1) 숨김 전제 단언(H-1)은 새 설계로 고쳤다.


async def _run_tier2_parsed(
    turns: list[tuple[str, dict[str, dict[str, Any]], list[dict[str, Any]]]],
    *,
    followup_kwargs: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """2단 축약 + 공통 전단 `input_parser` 대역(턴마다 `filter_conditions`를 싣는다).

    턴은 `(질의, {task_id: 결과}, filter_conditions)`이다.
    """
    seen: list[dict[str, Any]] = []
    current: dict[str, Any] = {}

    async def parser(state: AgentState) -> dict:
        return {"parsed_requirements": {
            "original_query": state["user_query"], "output_format": "text",
            "filter_conditions": current["filters"], "query_targets": [],
        }}

    async def planner(state: AgentState) -> dict:
        ctx = state.get("conversation_context")
        seen.append({"ctx": ctx, "block": _build_context_block(ctx, state["user_query"])})
        return {"task_plan": [
            {"task_id": tid, "agent": "data_query", "sub_query": state["user_query"],
             "order": i, "depends_on": [], "input_from": []}
            for i, tid in enumerate(current["results"], 1)
        ]}

    async def orch(state: AgentState) -> dict:
        return {"task_results": current["results"]}

    async def agg(state: AgentState) -> dict:
        return await result_aggregator(
            state, llm=MagicMock(), app_config=load_config(),
            synthesize=True, composite_answer="steps",
        )

    g = StateGraph(AgentState)
    for name, fn in (("context_resolver", context_resolver), ("parser", parser),
                     ("planner", planner), ("orch", orch), ("agg", agg)):
        g.add_node(name, fn)
    g.set_entry_point("context_resolver")
    g.add_edge("context_resolver", "parser")
    g.add_edge("parser", "planner")
    g.add_edge("planner", "orch")
    g.add_edge("orch", "agg")
    g.add_edge("agg", END)
    outs: list[dict[str, Any]] = []
    kw = followup_kwargs or [{} for _ in turns]
    with tempfile.TemporaryDirectory() as d:
        async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "c.db")) as saver:
            app = g.compile(checkpointer=saver)
            cfg = {"configurable": {"thread_id": "v3"}}
            for i, (query, results, filters) in enumerate(turns):
                current.update(results=results, filters=filters)
                inp = (create_initial_state(query, thread_id="v3") if i == 0
                       else create_followup_input(query, **kw[i]))
                outs.append(await app.ainvoke(inp, cfg))
    return seen, outs


_ZERO = {"t1": _task_result(T1_SQL, [])}
_WIDE = {"t1": _task_result(T2_WIDE_SQL, _ROWS)}
_CARRIED = {"t1": _task_result(T2_CARRIED_SQL, _ROWS)}
_NON_SQL = {"t1": {"organized_data": None, "final_answer": "APM 응답", "target_db_ids": []}}


class TestVerifierRequestScope:
    """요청 스코프 — 0행 기록은 바로 다음 턴만 본다(체크포인터 델타 병합 3턴 재현)."""

    async def test_three_turns_zero_demo_then_plain(self) -> None:
        seen, outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            (T2_QUERY, _CARRIED, []),
            ("자산관리 서버 지원 종료일 알려줘", _WIDE, []),
        ])
        assert EMPTY_ANTECEDENT_KEY in seen[1]["ctx"]
        # 2턴은 행이 나와 기록을 남기지 않는다 → 3턴 맥락에 승계·기록 모두 없다
        assert ZERO_ROW_TURN_KEY not in (outs[1]["conversation_context"] or {})
        assert EMPTY_ANTECEDENT_KEY not in seen[2]["ctx"]
        assert ZERO_ROW_TURN_KEY not in (outs[2]["conversation_context"] or {})
        assert NOTE_HEAD not in outs[2]["final_response"]
        assert len(outs[2]["query_results"]) == 2

    async def test_zero_plain_rows_then_demo_does_not_inherit(self) -> None:
        """0행 → 지시어 없는 질문(행 있음) → 지시어: 3턴 지시어는 2턴 결과를 가리킨다."""
        seen, outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            ("자산관리 서버 지원 종료일 알려줘", _WIDE, []),
            ("그 서버들 담당자도 알려줘", _WIDE, []),
        ])
        assert EMPTY_ANTECEDENT_KEY not in seen[2]["ctx"]
        assert {"field": "hostname", "value": "web01"} in seen[2]["ctx"]["previous_entities"]
        assert NOTE_HEAD not in outs[2]["final_response"]

    async def test_zero_then_non_sql_turn_drops_record(self) -> None:
        """0행 → 조회가 아닌 턴(실행 SQL 없음) → 지시어: 기록은 한 턴만 산다(종전 동작으로 복귀)."""
        seen, _outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            ("제니퍼 응답시간 알려줘", _NON_SQL, []),
            (T2_QUERY, _WIDE, []),
        ])
        assert EMPTY_ANTECEDENT_KEY not in seen[2]["ctx"]

    async def test_chain_keeps_original_antecedent(self) -> None:
        """0행 → 지시어 0행 → 지시어: 승계 기록은 최초 앞 턴 질의를 잇는다(설계대로)."""
        seen, outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            (T2_QUERY, {"t1": _task_result(T2_CARRIED_SQL, [])}, []),
            ("그 서버들 담당자도", {"t1": _task_result(T2_CARRIED_SQL, [])}, []),
        ])
        assert seen[2]["ctx"][EMPTY_ANTECEDENT_KEY]["query"] == T1_QUERY
        assert outs[2]["final_response"].count(NOTE_HEAD) == 1

    async def test_file_turn_on_same_thread_does_not_inherit(self) -> None:
        """파일 첨부 턴(라우트는 `create_initial_state`)은 맥락을 None으로 덮어 승계하지 않는다."""
        seen: list[dict[str, Any]] = []

        async def capture(state: AgentState) -> dict:
            seen.append(state.get("conversation_context") or {})
            return {}

        g = StateGraph(AgentState)
        g.add_node("context_resolver", context_resolver)
        g.add_node("capture", capture)
        g.set_entry_point("context_resolver")
        g.add_edge("context_resolver", "capture")
        g.add_edge("capture", END)
        with tempfile.TemporaryDirectory() as d:
            async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "c.db")) as saver:
                app = g.compile(checkpointer=saver)
                cfg = {"configurable": {"thread_id": "vf"}}
                await app.aupdate_state(cfg, {
                    **create_initial_state(T1_QUERY, thread_id="vf"),
                    "conversation_context": {ZERO_ROW_TURN_KEY: {"query": T1_QUERY,
                                                                 "sqls": [T1_SQL]}},
                })
                await app.ainvoke(
                    create_initial_state(T2_QUERY, uploaded_file=b"x", file_type="xlsx",
                                         thread_id="vf"), cfg,
                )
        assert EMPTY_ANTECEDENT_KEY not in seen[-1]

    async def test_db_scope_reset_still_inherits(self) -> None:
        """스코프 칩 해제 턴도 지시어 대상(빈 집합)은 잇는다 — 엔티티 sticky와 같은 규칙.

        (관찰 고정)
        """
        seen, _outs = await _run_tier2_parsed(
            [(T1_QUERY, _ZERO, []), (T2_QUERY, _CARRIED, [])],
            followup_kwargs=[{}, {"reset_db_scope": True}],
        )
        assert seen[1]["ctx"]["previous_db_ids"] == []
        assert seen[1]["ctx"][EMPTY_ANTECEDENT_KEY]["query"] == T1_QUERY

    async def test_zone_clarification_turn_keeps_antecedent_for_answer_turn(self) -> None:
        """0행 → 지시어(존 역질문) → 존 답변(같은 원문 재전송): 3턴도 앞 턴 조건을 이어야 한다."""
        zone_q = {"t1": {"zone_clarification": {"question": "어느 존?", "options": []},
                         "target_db_ids": []}}
        seen, outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            (T2_QUERY, zone_q, []),
            (T2_QUERY, _WIDE, []),
        ])
        assert outs[1]["final_response"] == "어느 존?"
        assert EMPTY_ANTECEDENT_KEY in seen[2]["ctx"]
        assert outs[2]["final_response"].startswith(
            empty_antecedent_note(_PRIOR, widened=True)
        )


class TestVerifierIdentifierTier2:
    async def test_named_server_zero_rows_keeps_identifier_tier2(self) -> None:
        """2단에서도 앞 턴이 서버를 지목한 0행이면 빈 집합 승계를 하지 않는다.

        (공통 전단 파싱 기준)
        """
        named = [{"field": "hostname", "op": "=", "value": "web09"}]
        seen, outs = await _run_tier2_parsed([
            ("web09 CPU 사용률", {"t1": _task_result("SELECT cpu FROM m WHERE hostname = 'web09'",
                                                     [])}, named),
            ("그 서버 메모리도", {"t1": _task_result("SELECT mem FROM m", _ROWS)}, []),
        ])
        ctx = seen[1]["ctx"]
        assert EMPTY_ANTECEDENT_KEY not in ctx
        assert ctx["previous_entities"] == [{"field": "hostname", "value": "web09"}]
        assert "hostname=web09" in seen[1]["block"]
        assert NOTE_HEAD not in outs[1]["final_response"]
        assert len(outs[1]["query_results"]) == 2


_PRIOR = {"query": T1_QUERY, "sqls": [T1_SQL]}


class TestVerifierWidenedJudgement:
    """넓힘 판정 — 표기 차이는 이은 것으로 본다(오탐 없음)."""

    @pytest.mark.parametrize("sql", [
        "SELECT * FROM t WHERE `용도내용` LIKE '%통합인증서비스%'",          # 공백 차이
        "SELECT * FROM t WHERE UPPER(`용도내용`) LIKE UPPER('%통합인증 서비스%')",
        "SELECT * FROM t WHERE `용도내용` IN ('통합인증 서비스', '기타')",    # IN 목록
        "SELECT * FROM t WHERE `용도내용` = '통합인증 서비스'",              # LIKE → =
        "SELECT * FROM t WHERE `용도내용` LIKE '통합인증%'",                 # 접두 패턴
    ])
    def test_spelling_variants_are_carried(self, sql: str) -> None:
        assert widened_beyond_antecedent(_PRIOR, [sql]) is False

    def test_case_variants_are_carried(self) -> None:
        prior = {"query": "q", "sqls": ["SELECT h FROM POLESTAR.R WHERE OS = 'AIX'"]}
        sql = "SELECT h FROM polestar.r WHERE os = 'aix'"
        assert widened_beyond_antecedent(prior, [sql]) is False

    def test_multi_db_prior_any_value_counts(self) -> None:
        prior = {"query": "q", "sqls": ["SELECT 1 FROM a WHERE z = 'gimpo'",
                                        "SELECT 1 FROM POLESTAR.A WHERE Z = 'BANK'"]}
        assert widened_beyond_antecedent(prior, ["SELECT 1 FROM a WHERE z = 'bank'"]) is False

    def test_numeric_condition_with_date_literal_carried_by_function(self) -> None:
        prior = {"query": "어제 CPU 95% 넘는 서버", "sqls": [
            "SELECT hostname FROM m WHERE cpu > 95 AND stat_date = '2026-10-07'"]}
        carried = ("SELECT hostname, mem FROM m WHERE cpu > 95 "
                   "AND stat_date = CURRENT_DATE - 1")
        assert widened_beyond_antecedent(prior, [carried]) is False

    def test_shared_incidental_literal_masks_widening(self) -> None:
        prior = {"query": T1_QUERY, "sqls": [
            "SELECT s.h FROM t_server s WHERE s.`용도내용` LIKE '%통합인증%' "
            "AND s.`운영상태` = '운영'"]}
        widened = ("SELECT a.h, a.eos FROM t_eos a JOIN t_server b ON a.h = b.h "
                   "WHERE b.`운영상태` = '운영'")
        assert widened_beyond_antecedent(prior, [widened]) is True

    def test_demonstrative_with_modu_is_inherited(self) -> None:
        prior = {ZERO_ROW_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL]}}
        query = "그 서버들의 지원 종료일을 모두 알려줘"
        assert resolve_empty_antecedent(query, prior, []) is not None


class TestVerifierGraphPathFalsePositive:
    async def test_carried_numeric_condition_rows_are_shown(self, monkeypatch) -> None:
        t1_sql = "SELECT hostname FROM r WHERE cpu > 95 AND stat_date = '2026-10-07'"
        t2_sql = "SELECT hostname, mem FROM r WHERE cpu > 95 AND stat_date = CURRENT_DATE - 1"
        outs = await _run_graph_path(
            [("어제 CPU 95% 넘는 서버", t1_sql, [], []),
             ("그 서버들 메모리 사용률도", t2_sql, _ROWS, [])], monkeypatch,
        )
        assert outs[1]["final_response"] == "표 2행"


# ── 라우트(SSE) — 숨긴 행이 메타·CSV로 새는가 ────────────────────────────────


@pytest.fixture
def _route_config(monkeypatch):
    from src.config import AppConfig, ServerConfig

    monkeypatch.setenv("SCHEMA_CACHE_ENABLED", "false")
    return AppConfig(
        db_backend="direct", db_connection_string="", log_level="WARNING",
        server=ServerConfig(query_timeout=60, file_query_timeout=120),
    )


class _RouteGraph:
    def __init__(self, compiled: Any) -> None:
        self._compiled = compiled

    def get_state(self, config: dict) -> None:
        return None

    async def ainvoke(self, input_state: dict, config: dict) -> dict:
        return await self._compiled.ainvoke(input_state, config)

    def astream_events(self, input_state: dict, config: dict, version: str = "v2"):
        return self._compiled.astream_events(input_state, config, version=version)


def test_widened_rows_reach_stream_meta_and_csv_with_notice_first(_route_config) -> None:
    """넓힘 행은 숨기지 않는다(교정 1) — 응답 맨 앞 안내 · row_count·CSV는 보인 행과 일치."""
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes

    ante = {"query": _PS_T1, "sqls": [_PS_T1_SQL]}

    async def query_executor(state: AgentState) -> dict:
        return {
            "query_results": _ROWS,
            "organized_data": {"summary": "", "rows": _ROWS, "is_sufficient": True},
            "query_attempts": [{"sql": _PS_T2_WIDE, "success": True}],
            "generated_sql": _PS_T2_WIDE,
            "conversation_context": {"turn_count": 2, EMPTY_ANTECEDENT_KEY: ante},
            "error_message": None,
        }

    g = StateGraph(AgentState)
    g.add_node("query_executor", query_executor)
    g.add_node("output_generator",
               partial(og.output_generator, llm=MagicMock(), app_config=load_config()))
    g.set_entry_point("query_executor")
    g.add_edge("query_executor", "output_generator")
    g.add_edge("output_generator", END)

    app = FastAPI()
    app.state.config = _route_config
    app.state.graph = _RouteGraph(g.compile())
    app.include_router(query_routes.router, prefix="/api/v1")
    app.dependency_overrides[require_user] = lambda: {"sub": "u1", "role": "user"}
    client = TestClient(app)
    r = client.post("/api/v1/query/stream", json={"query": _PS_T2})
    assert r.status_code == 200, r.text
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response"].startswith(empty_antecedent_note(ante, widened=True) + "\n\n")
    csv = client.get(f"/api/v1/query/{done['query_id']}/download-csv")
    assert done["row_count"] == 2
    assert csv.status_code == 200 and "web01" in csv.text


# ── 교정 1(plans/146 W3) — 조건 값 판정 · 「모두」 · 존 역질문 · 인용 상한 ─────────────────


class TestCorrection1:
    def test_prior_dates_and_formats_are_not_condition_values(self) -> None:
        """앞 턴의 날짜·형식 문자열만 있으면 판정하지 않는다(H-2)."""
        prior = {"query": "q", "sqls": [
            "SELECT h FROM m WHERE TO_CHAR(d, 'YYYY-MM-DD') = '2026-10-07' "
            "AND DATE_FORMAT(d, '%Y-%m') = '2026-10' AND ymd = '20261007'"]}
        assert widened_beyond_antecedent(prior, ["SELECT h, mem FROM m"]) is False

    def test_prior_equality_values_count_without_like(self) -> None:
        """LIKE가 없으면 같음·IN 비교 값으로 판정한다(날짜는 빼고)."""
        prior = {"query": "q", "sqls": [
            "SELECT h FROM m WHERE os IN ('AIX', 'HP-UX') AND d >= '2026-10-01'"]}
        assert widened_beyond_antecedent(prior, ["SELECT h FROM m WHERE d >= '2026-10-01'"])
        assert not widened_beyond_antecedent(prior, ["SELECT h FROM m WHERE os = 'hp-ux'"])

    def test_zone_turn_context_carries_record(self) -> None:
        """존 역질문 턴(2단 집계기 · 3단 semantic_router 공용) — 승계 중일 때만 넘긴다(M-2)."""
        from src.utils.empty_antecedent import carried_zone_turn_context

        ctx = {"turn_count": 2, EMPTY_ANTECEDENT_KEY: _PRIOR}
        assert carried_zone_turn_context(ctx) == {**ctx, ZERO_ROW_TURN_KEY: _PRIOR}
        assert carried_zone_turn_context({"turn_count": 2}) is None
        assert carried_zone_turn_context(None) is None

    def test_planner_block_quotes_capped_query_with_modu(self) -> None:
        """계획 맥락 — 「모두」 지시어 턴에도 승계 줄을 싣고(M-4) 앞 질의는 60자로 자른다(L-3)."""
        long_q = "자산관리에서 " + "통합인증 서비스 " * 10 + "서버 목록"
        ctx = {"turn_count": 2, EMPTY_ANTECEDENT_KEY: {"query": long_q, "sqls": [T1_SQL]}}
        block = _build_context_block(ctx, "그 서버들의 지원 종료일을 모두 알려줘")
        assert f"직전 질의 「{long_q[:60]}…」의 조회 결과가 0건" in block
        assert long_q not in block
        assert "조건 없이 전체 대상으로 넓히지 말 것" in block

    async def test_modu_demonstrative_inherits_end_to_end(self) -> None:
        """「그 서버들의 지원 종료일을 모두 알려줘」 — 승계해 다시 0행이면 안내(M-4 · 2단)."""
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, []),
            ("그 서버들의 지원 종료일을 모두 알려줘", T2_CARRIED_SQL, []),
        ])
        assert seen[1]["ctx"][EMPTY_ANTECEDENT_KEY]["query"] == T1_QUERY
        assert outs[1]["final_response"].count(NOTE_HEAD) == 1

    def test_global_query_without_demonstrative_is_unchanged(self) -> None:
        """지시어 명사구 없는 「모두」 질의는 종전대로 승계하지 않는다."""
        prior = {ZERO_ROW_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL]}}
        assert resolve_empty_antecedent("서버 지원 종료일을 모두 알려줘", prior, []) is None


# ── 재검증(verifier · 교정 1) ────────────────────────────────────────────────

# 재검증 xfail 3건(M-R1 「전체 로그서버」 · R-L2 CONCAT·`||` LIKE)은 교정 2에서 고쳐 표지를 걷었다.


class TestReverifyCorrection1:
    async def test_widened_turn_then_demonstrative_inherits_shown_rows(self) -> None:
        """넓힘 턴은 0행 기록을 남기지 않는다 — 다음 지시어는 보인 행의 엔티티를 잇는다(③)."""
        seen, outs = await _run_tier2_parsed([
            (T1_QUERY, _ZERO, []),
            (T2_QUERY, _WIDE, []),
            ("그 서버들 담당자도 알려줘", _WIDE, []),
        ])
        assert outs[1]["final_response"].startswith(empty_antecedent_note(_PRIOR, widened=True))
        assert EMPTY_ANTECEDENT_KEY not in seen[2]["ctx"]
        assert {"field": "hostname", "value": "web01"} in seen[2]["ctx"]["previous_entities"]
        assert NOTE_HEAD not in outs[2]["final_response"]

    def test_global_logserver_query_is_not_demonstrative(self) -> None:
        prior = {ZERO_ROW_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL]}}
        assert resolve_empty_antecedent("전체 로그서버 목록 보여줘", prior, []) is None

    @pytest.mark.parametrize("prior_sql", [
        "SELECT h FROM t WHERE `용도내용` LIKE CONCAT('%', '통합인증', '%')",
        "SELECT h FROM t WHERE 용도내용 LIKE '%' || '통합인증' || '%'",
    ])
    def test_concat_like_prior_values_are_extracted(self, prior_sql: str) -> None:
        prior = {"query": T1_QUERY, "sqls": [prior_sql]}
        assert widened_beyond_antecedent(prior, [T2_WIDE_SQL]) is True

    def test_like_value_wins_over_shared_equality(self) -> None:
        """LIKE 값이 있으면 상태값 `=` 공유로 이은 것으로 보지 않는다(M-1 해소 확인)."""
        prior = {"query": "q", "sqls": [
            "SELECT h FROM t WHERE nm LIKE '%통합인증%' AND st IN ('운영', '대기')"]}
        assert widened_beyond_antecedent(prior, ["SELECT h FROM t WHERE st = '운영'"]) is True
        assert not widened_beyond_antecedent(prior, ["SELECT h FROM t WHERE nm LIKE '%통합인증%'"])

    def test_comparison_operators_are_not_equality_values(self) -> None:
        """`>=`·`<=` 비교의 문자열 값은 조건 값으로 뽑지 않는다(날짜 아님이어도)."""
        prior = {"query": "q", "sqls": ["SELECT h FROM t WHERE grade >= 'B2' AND ver <= 'v9'"]}
        assert widened_beyond_antecedent(prior, ["SELECT h FROM t"]) is False


def _router_state(ctx: dict[str, Any] | None) -> dict[str, Any]:
    state = create_initial_state("그 서버들 cpu 사용률")
    state["parsed_requirements"] = {"original_query": "그 서버들 cpu 사용률",
                                    "query_targets": ["cpu"], "target_db_hints": []}
    state["conversation_context"] = ctx
    state["zone_clarification_allowed"] = True
    return state


async def _route_zone(state: dict[str, Any]) -> dict[str, Any]:
    from unittest.mock import AsyncMock, patch

    from src.routing.semantic_router import semantic_router

    rows = [{"db_id": "polestar_cm_gp", "relevance_score": 0.9, "sub_query_context": "q",
             "user_specified": False, "reason": "LLM"}]
    config = MagicMock()
    config.multi_db.get_active_db_ids.return_value = ["polestar_b0", "polestar_cm_gp"]
    config.multi_db.zone_group_exclusive = True
    config.enable_semantic_routing = True
    config.tier3_plan_loop_enabled = False
    zone_q = {"question": "어느 존?", "options": []}
    with patch("src.routing.semantic_router._llm_classify", AsyncMock(return_value=rows)), \
            patch("src.routing.semantic_router._ownership_enabled", return_value=False), \
            patch("src.routing.semantic_router._zone_clarification_or_none_router",
                  return_value=zone_q):
        return await semantic_router(state, llm=AsyncMock(), app_config=config)


class TestReverifySemanticRouterZone:
    """3단 존 역질문 출구(④) — 승계 없으면 반환 비트 동일, 승계 중이면 기록을 넘긴다."""

    async def test_without_antecedent_return_has_no_context_key(self) -> None:
        for ctx in (None, {"turn_count": 2, "previous_db_ids": []}):
            out = await _route_zone(_router_state(ctx))
            assert out == {
                "target_databases": [], "is_multi_db": False, "active_db_id": None,
                "user_specified_db": None, "routing_intent": "zone_clarification",
                "zone_clarification": {"question": "어느 존?", "options": []},
                "final_response": "어느 존?", "current_node": "semantic_router",
            }

    async def test_with_antecedent_carries_record(self) -> None:
        ctx = {"turn_count": 2, "previous_db_ids": [], EMPTY_ANTECEDENT_KEY: _PRIOR}
        out = await _route_zone(_router_state(ctx))
        assert out["routing_intent"] == "zone_clarification"
        assert out["conversation_context"] == {**ctx, ZERO_ROW_TURN_KEY: _PRIOR}


# ── 교정 2(plans/146 W3) — 지시어 명사구 경계 · 전역어 위치 ──────────────────────────


class TestCorrection2:
    @pytest.mark.parametrize(("query", "inherits"), [
        ("로그서버 목록 보여줘", False),            # 「로그서버」 안의 「그서버」는 지시어가 아니다
        ("전체 서버 중 그 서버들 지원 종료일", False),  # 전역어가 명사구 앞
        ("모든 그 서버들 담당자", False),
        ("그 서버들 지원 종료일도 알려줘", True),
        ("해당서버 메모리도", True),
        ("(그 서버들) 담당자", True),
        ("이전 서버들 지원 종료일", True),
    ])
    def test_demonstrative_phrase_boundary(self, query: str, inherits: bool) -> None:
        prior = {ZERO_ROW_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL]}}
        assert (resolve_empty_antecedent(query, prior, []) is not None) is inherits

    def test_concat_like_with_column_uses_where_like_value(self) -> None:
        """CONCAT 안이 컬럼뿐인 조인(오라클 P05 모양)은 건너뛰고 WHERE LIKE 값으로 판정한다."""
        prior = {"query": "q", "sqls": [
            "SELECT s.h FROM s JOIN a ON s.u LIKE CONCAT('%', a.nm, '%') "
            "WHERE a.nm LIKE '%통합인증%'"]}
        assert widened_beyond_antecedent(prior, [T2_WIDE_SQL]) is True
        assert not widened_beyond_antecedent(prior, [T2_CARRIED_SQL])
