"""plans/150 W1 — 지시어 후속 턴 표본 승계 교정(F1 · G-1 (a)).

내부망 5회차 ITAM-106: t1 「통합인증 서비스 서버리스트」가 85행인데 t2 「그 서버들 …」이 IP 10개만
`IN`으로 조회했다(침묵 부분 답). 끊긴 지점은 셋이다.
- `context_resolver`는 엔티티를 결과 앞쪽 20행(`_MAX_ENTITY_ROWS`)에서만 모은다(표본).
- 2단 `_build_context_block`은 지시어 턴에 그 표본을 「직전 대상 서버/장비」로 실었다.
- 노드 경로 `demonstrative_entity_scope`는 표본이면 승계하지 않는다 — 2단과 비대칭(D-066).

체크포인터(AsyncSqliteSaver) 2턴으로 앞 턴 0행 · 20행 이하 · 21행 이상 × 2단·노드 경로를 고정한다.
- 2단(기준): context_resolver → 계획 맥락 캡처 → 가짜 오케스트레이터 → 실제 result_aggregator
- 3·4단 그래프: context_resolver → 가짜 조회 파이프라인 → 실제 output_generator
"""

from __future__ import annotations

import importlib
import json
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
from src.nodes.prompt_blocks import demonstrative_entity_scope
from src.nodes.query_generator import _build_user_prompt
from src.orchestration.intent_planner import _build_context_block
from src.orchestration.result_aggregator import result_aggregator
from src.state import AgentState, create_followup_input, create_initial_state
from src.utils.empty_antecedent import (
    EMPTY_ANTECEDENT_KEY,
    ENTITY_SAMPLE_ROWS,
    SAMPLED_ANTECEDENT_KEY,
    SAMPLED_TURN_KEY,
    ZERO_ROW_TURN_KEY,
    resolve_sampled_antecedent,
    sampled_antecedent_note,
    sampled_antecedent_outcome,
    sampled_turn,
    scoped_to_sample,
)

#: 모듈 객체(`src.nodes` 패키지가 같은 이름의 함수를 재노출해 `from … import`는 함수를 준다)
og = importlib.import_module("src.nodes.output_generator")

# ── ITAM 모양(한글 칸 · 식별 힌트는 IP만 걸린다) ──────────────────────────────────

T1_QUERY = "자산관리에서 통합인증 서비스 서버리스트 보여줘"
T1_SQL = (
    "SELECT `서버호스트명`, `IP주소내용` FROM `t_server` "
    "WHERE `그룹경로내용` LIKE '%통합인증%' LIMIT 10000"
)
T2_QUERY = "그 서버들 OS 버전도 알려줘"
#: 5회차 실측 모양 — 표본 IP만 `IN`
T2_SAMPLE_SQL = (
    "SELECT `서버호스트명`, `OS명` FROM `t_server` "
    "WHERE `IP주소내용` IN ('10.0.0.0', '10.0.0.1', '10.0.0.2') LIMIT 10000"
)
#: 앞 턴 조건을 이은 조회
T2_CARRIED_SQL = (
    "SELECT `서버호스트명`, `OS명` FROM `t_server` "
    "WHERE `그룹경로내용` LIKE '%통합인증%' LIMIT 10000"
)
#: 앞 턴 조건을 잇지 않은 조회(조건 없음)
T2_WIDE_SQL = "SELECT `서버호스트명`, `OS명` FROM `t_server` LIMIT 10000"

NOTE_HEAD = "[안내] 이번 조회는 앞 질문("
OLD_NOTE_HEAD = "[안내] 앞 질문("


def _itam_rows(n: int) -> list[dict[str, Any]]:
    return [{"서버호스트명": f"auth{i:02d}", "IP주소내용": f"10.0.0.{i}"} for i in range(n)]


def _os_rows(n: int) -> list[dict[str, Any]]:
    return [{"서버호스트명": f"auth{i:02d}", "OS명": "RHEL 8"} for i in range(n)]


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


async def _run_tier2(
    turns: list[TurnSpec],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
    for name, fn in (("context_resolver", context_resolver), ("planner", planner),
                     ("orch", orch), ("agg", agg)):
        g.add_node(name, fn)
    g.set_entry_point("context_resolver")
    g.add_edge("context_resolver", "planner")
    g.add_edge("planner", "orch")
    g.add_edge("orch", "agg")
    g.add_edge("agg", END)
    outs: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as d:
        async with AsyncSqliteSaver.from_conn_string(os.path.join(d, "c.db")) as saver:
            app = g.compile(checkpointer=saver)
            cfg = {"configurable": {"thread_id": "p150"}}
            for i, spec in enumerate(turns):
                query = spec[0]
                current["results"] = (
                    {"t1": _task_result(spec[1], spec[2])} if len(spec) == 3 else spec[1]
                )
                inp = (create_initial_state(query, thread_id="p150") if i == 0
                       else create_followup_input(query))
                outs.append(await app.ainvoke(inp, cfg))
    return seen, outs


async def _fake_summary(config, state, **_kw) -> str:
    rows = state["organized_data"]["rows"]
    return f"표 {len(rows)}행" if rows else "조건에 해당하는 데이터가 없습니다."


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
            cfg = {"configurable": {"thread_id": "p150g"}}
            for i, (query, sql, rows, filters) in enumerate(turns):
                current.update(sql=sql, rows=rows, filters=filters)
                inp = (create_initial_state(query, thread_id="p150g") if i == 0
                       else create_followup_input(query))
                outs.append(await app.ainvoke(inp, cfg))
    return outs


def _ante(row_count: int = 85, sample: list[str] | None = None) -> dict[str, Any]:
    return {
        "query": T1_QUERY, "sqls": [T1_SQL], "row_count": row_count,
        "sample_values": sample if sample is not None else [f"10.0.0.{i}" for i in range(20)],
    }


# ── 단위: 판정 함수 ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_sampled_turn_threshold(self) -> None:
        assert sampled_turn(T1_QUERY, [T1_SQL], ENTITY_SAMPLE_ROWS) is None
        assert sampled_turn(T1_QUERY, [], 85) is None
        assert sampled_turn(T1_QUERY, [T1_SQL], ENTITY_SAMPLE_ROWS + 1) == {
            "query": T1_QUERY, "sqls": [T1_SQL], "row_count": ENTITY_SAMPLE_ROWS + 1,
        }

    def test_resolve_requires_demonstrative_record_and_sample(self) -> None:
        prior = {SAMPLED_TURN_KEY: {"query": T1_QUERY, "sqls": [T1_SQL], "row_count": 85}}
        ents = [{"field": "IP주소내용", "value": "10.0.0.0"}]
        got = resolve_sampled_antecedent(T2_QUERY, prior, ents, entities_complete=False)
        assert got == {"query": T1_QUERY, "sqls": [T1_SQL], "row_count": 85,
                       "sample_values": ["10.0.0.0"], "sample_columns": ["IP주소내용"]}
        # 판정 전용 표본은 앞쪽 행의 한글 식별 칸까지 넓힌다(교정 M2) — 엔티티는 그대로
        got = resolve_sampled_antecedent(
            T2_QUERY, prior, ents, entities_complete=False, sample_rows=_itam_rows(25),
        )
        assert got is not None
        assert got["sample_columns"] == ["서버호스트명", "IP주소내용"]
        assert "auth19" in got["sample_values"] and "auth20" not in got["sample_values"]
        assert resolve_sampled_antecedent(
            "OS 버전 알려줘", prior, ents, entities_complete=False) is None  # 지시어 없음
        assert resolve_sampled_antecedent(
            T2_QUERY, {}, ents, entities_complete=False) is None  # 기록 없음(20행 이하)
        # 엔티티가 앞 결과 전체(서버 식별 필터로 좁힌 앞 턴) — 표본이 아니다
        assert resolve_sampled_antecedent(T2_QUERY, prior, ents, entities_complete=True) is None

    def test_outcome_partial_widened_carried(self) -> None:
        ante = _ante()
        assert scoped_to_sample(ante, [T2_SAMPLE_SQL]) is True
        assert scoped_to_sample(ante, [T2_CARRIED_SQL]) is False
        assert sampled_antecedent_outcome(ante, [T2_SAMPLE_SQL], 3) == (
            sampled_antecedent_note(ante, partial=True)
        )
        # 표본 `IN`은 0행이어도 알린다(일부만 본 결과다)
        assert sampled_antecedent_outcome(ante, [T2_SAMPLE_SQL], 0) == (
            sampled_antecedent_note(ante, partial=True)
        )
        # 앞 조건을 함께 이었어도 표본 `IN`이면 일부다
        both = T2_CARRIED_SQL.replace(
            "LIMIT", "AND `IP주소내용` IN ('10.0.0.3', '10.0.0.4') LIMIT"
        )
        assert sampled_antecedent_outcome(ante, [both], 2) == (
            sampled_antecedent_note(ante, partial=True)
        )
        # 같은 턴에 앞 조건을 다시 조회한 선행 결과 전체로 건 `IN`(표본 밖 값 포함)은 표본이 아니다
        # (MLX 실측 계획 모양 — t1 조건 재조회 → t2 「앞서 조회된 서버들」)
        full_in = (
            "SELECT `OS명` FROM `t_server` WHERE `IP주소내용` IN ("
            + ", ".join(f"'10.0.0.{i}'" for i in range(85)) + ")"
        )
        assert scoped_to_sample(ante, [T2_CARRIED_SQL, full_in]) is False
        assert sampled_antecedent_outcome(ante, [T2_CARRIED_SQL, full_in], 85) is None
        assert sampled_antecedent_outcome(ante, [T2_WIDE_SQL], 3000) == (
            sampled_antecedent_note(ante, partial=False)
        )
        assert sampled_antecedent_outcome(ante, [T2_WIDE_SQL], 0) is None
        assert sampled_antecedent_outcome(ante, [T2_CARRIED_SQL], 85) is None
        assert sampled_antecedent_outcome(None, [T2_SAMPLE_SQL], 3) is None

    def test_note_wording(self) -> None:
        ante = _ante()
        assert sampled_antecedent_note(ante, partial=True) == (
            f"[안내] 이번 조회는 앞 질문(「{T1_QUERY}」)의 결과 85건 중 일부 서버만 대상으로 "
            "했습니다 — 아래 결과는 앞 결과 전체가 아닙니다. 전체를 보려면 앞 질문의 조건을 "
            "함께 적어 다시 질문해 주세요."
        )
        assert sampled_antecedent_note(ante, partial=False) == (
            f"[안내] 이번 조회는 앞 질문(「{T1_QUERY}」)의 조건을 잇지 않아, 아래 결과는 앞에서 "
            "찾은 서버 85건이 아니라 전체 대상일 수 있습니다. 앞 조건으로 보려면 대상을 직접 "
            "적어 다시 질문해 주세요."
        )


# ── 2단(기준 경로) ───────────────────────────────────────────────────────────


class TestTier2:
    async def test_over_cap_does_not_present_sample_as_antecedent(self) -> None:
        """21행 이상 → 계획 맥락에 표본 값이 「직전 대상」으로 실리지 않고 조건 승계 지시를 싣는다.

        5회차 ITAM-106 t2 모양(85행 · 한글 식별 칸이라 IP만 엔티티)."""
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            (T2_QUERY, T2_SAMPLE_SQL, _os_rows(3)),
        ])
        assert outs[0]["conversation_context"][SAMPLED_TURN_KEY] == {
            "query": T1_QUERY, "sqls": [T1_SQL], "row_count": 85,
        }
        ctx = seen[1]["ctx"]
        ante = ctx[SAMPLED_ANTECEDENT_KEY]
        assert ante["row_count"] == 85 and ante["sqls"] == [T1_SQL]
        assert ante["sample_values"][:3] == ["auth00", "10.0.0.0", "auth01"]
        assert ante["sample_columns"] == ["서버호스트명", "IP주소내용"]
        # 엔티티 쪽은 종전대로 IP만(한글 식별 칸 힌트 미확장 — 단수 지시어 등 소비처 불변)
        assert {e["field"] for e in ctx["previous_entities"]} == {"IP주소내용"}
        # 2단은 top-level SQL이 비어 있다 — 기록의 SQL로 채워 SQL 생성 프롬프트가 잇는다
        assert ctx["previous_sql"] == T1_SQL
        # 엔티티 자체는 그대로 둔다(다른 소비처 종전) · 표본 표지는 False
        assert ctx["previous_entities"] and ctx["previous_entities_complete"] is False
        block = seen[1]["block"]
        assert "10.0.0." not in block
        assert "IP주소내용=" not in block
        assert (
            f"- 직전 대상 서버/장비: (직전 질의 「{T1_QUERY}」의 결과 85건 — 표본만 보존되어 "
            "식별값을 싣지 않음)" in block
        )
        assert "일부 서버 식별값으로 좁히거나 조건 없이 전체 대상으로 넓히지 말 것" in block
        prompt = _build_user_prompt(
            {"original_query": T2_QUERY}, None, None, None, conversation_context=ctx,
        )
        assert "## 이전 대화의 SQL (참조용)" in prompt and "통합인증" in prompt
        # 표본 `IN` → 맨 앞 안내 · 행은 그대로
        resp = outs[1]["final_response"]
        assert resp.startswith(sampled_antecedent_note(ante, partial=True) + "\n\n")
        assert resp.count(NOTE_HEAD) == 1
        assert len(outs[1]["query_results"]) == 3

    async def test_over_cap_carried_has_no_notice(self) -> None:
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            (T2_QUERY, T2_CARRIED_SQL, _os_rows(85)),
        ])
        assert NOTE_HEAD not in outs[1]["final_response"]
        assert len(outs[1]["query_results"]) == 85
        # 이번 턴도 상한을 넘었다 — 다음 턴이 이 턴의 조건을 잇는다
        assert outs[1]["conversation_context"][SAMPLED_TURN_KEY]["sqls"] == [T2_CARRIED_SQL]

    async def test_over_cap_widened_notifies_first(self) -> None:
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            (T2_QUERY, T2_WIDE_SQL, _os_rows(30)),
        ])
        ante = seen[1]["ctx"][SAMPLED_ANTECEDENT_KEY]
        resp = outs[1]["final_response"]
        assert resp.startswith(sampled_antecedent_note(ante, partial=False) + "\n\n")
        assert resp.count(NOTE_HEAD) == 1
        assert len(outs[1]["query_results"]) == 30

    async def test_requery_then_dependent_full_scope_has_no_notice(self) -> None:
        """MLX 실측 계획 모양 — 조건 재조회(t1 · 85행) → 「앞서 조회된 서버들」(t2 · 선행 결과 전체
        `IN`). 표본 값과 겹쳐도 표본 밖 값이 섞였으면 일부 조회가 아니다."""
        full_in = (
            "SELECT `서버호스트명`, `OS명` FROM `t_server` WHERE `IP주소내용` IN ("
            + ", ".join(f"'10.0.0.{i}'" for i in range(85)) + ") LIMIT 10000"
        )
        _seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            (T2_QUERY, {"t1": _task_result(T1_SQL, _itam_rows(85)),
                        "t2": _task_result(full_in, _os_rows(85))}),
        ])
        assert NOTE_HEAD not in outs[1]["final_response"]

    async def test_sample_hostnames_in_after_date_prior_is_partial(self) -> None:
        """교정 M2·M3 — 앞 조건이 날짜뿐(ITAM-212 모양)인데 표본 호스트명(한글 식별 칸)을 `IN`.

        판정 전용 표본이 `서버호스트명`까지 담아 「일부만 조회」로 잡는다.
        """
        date_sql = (
            "SELECT `서버호스트명`, `IP주소내용` FROM `t_server` "
            "WHERE `취득년월일` < DATE_FORMAT(DATE_SUB(CURDATE(), INTERVAL 6 YEAR), '%Y%m%d')"
        )
        host_in = (
            "SELECT `서버호스트명`, `OS명` FROM `t_server` "
            "WHERE `서버호스트명` IN ('auth00', 'auth01', 'auth02')"
        )
        seen, outs = await _run_tier2([
            ("도입한 지 6년 넘은 서버 목록 보여줘", date_sql, _itam_rows(85)),
            (T2_QUERY, host_in, _os_rows(3)),
        ])
        ante = seen[1]["ctx"][SAMPLED_ANTECEDENT_KEY]
        assert outs[1]["final_response"].startswith(
            sampled_antecedent_note(ante, partial=True) + "\n\n"
        )
        assert len(outs[1]["query_results"]) == 3

    async def test_up_to_cap_is_unchanged(self) -> None:
        """20행 이하 → 종전 동작(기록·승계·안내 없음 · 엔티티를 「직전 대상」으로 싣는다)."""
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(ENTITY_SAMPLE_ROWS)),
            (T2_QUERY, T2_SAMPLE_SQL, _os_rows(3)),
        ])
        assert SAMPLED_TURN_KEY not in (outs[0].get("conversation_context") or {})
        ctx = seen[1]["ctx"]
        assert SAMPLED_ANTECEDENT_KEY not in ctx
        assert ctx["previous_sql"] == ""
        assert ctx["previous_entities_complete"] is True
        block = seen[1]["block"]
        assert "- 직전 대상 서버/장비: IP주소내용=10.0.0.0, IP주소내용=10.0.0.1" in block
        assert block.endswith(
            "- 사용자가 명시적으로 다른 위치/DB/대상을 지정하면 그 신호를 최우선으로 따르라"
            "(승계하지 말 것).\n"
        )
        assert NOTE_HEAD not in outs[1]["final_response"]

    async def test_zero_rows_keeps_plan146_path(self) -> None:
        """앞 턴 0행 → 146 W3 경로 그대로(표본 승계와 섞이지 않는다)."""
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, []),
            (T2_QUERY, T2_CARRIED_SQL, []),
        ])
        assert ZERO_ROW_TURN_KEY in outs[0]["conversation_context"]
        assert SAMPLED_TURN_KEY not in outs[0]["conversation_context"]
        ctx = seen[1]["ctx"]
        assert EMPTY_ANTECEDENT_KEY in ctx and SAMPLED_ANTECEDENT_KEY not in ctx
        assert "조회 결과가 0건" in seen[1]["block"]
        assert NOTE_HEAD not in outs[1]["final_response"]
        assert outs[1]["final_response"].count(OLD_NOTE_HEAD) == 1

    async def test_over_cap_without_demonstrative_is_unchanged(self) -> None:
        seen, outs = await _run_tier2([
            (T1_QUERY, T1_SQL, _itam_rows(85)),
            ("자산관리 서버 OS 버전 알려줘", T2_WIDE_SQL, _os_rows(30)),
        ])
        ctx = seen[1]["ctx"]
        assert SAMPLED_ANTECEDENT_KEY not in ctx
        assert ctx["previous_sql"] == ""
        assert "직전 대상 서버/장비" not in seen[1]["block"]
        assert NOTE_HEAD not in outs[1]["final_response"]


# ── 3·4단 그래프 경로 ──────────────────────────────────────────────────────


class TestGraphPath:
    async def test_over_cap_partial_notice_and_existing_sql_channel(self, monkeypatch) -> None:
        """노드 경로 — 표본 가드(`demonstrative_entity_scope`)는 승계하지 않고, 앞 턴 SQL은 종전대로
        `previous_sql`(top-level)로 SQL 생성 프롬프트에 닿는다. 실행 뒤 안내는 2단과 같다."""
        outs = await _run_graph_path([
            (T1_QUERY, T1_SQL, _itam_rows(85), []),
            (T2_QUERY, T2_SAMPLE_SQL, _os_rows(3), []),
        ], monkeypatch)
        assert outs[0]["conversation_context"][SAMPLED_TURN_KEY]["row_count"] == 85
        ctx = outs[1]["conversation_context"]
        ante = ctx[SAMPLED_ANTECEDENT_KEY]
        assert ctx["previous_sql"] == T1_SQL
        assert demonstrative_entity_scope({
            "user_query": T2_QUERY, "parsed_requirements": {}, "conversation_context": ctx,
        }) is None
        prompt = _build_user_prompt(
            {"original_query": T2_QUERY}, None, None, None, conversation_context=ctx,
        )
        assert "이전 SQL을 기반으로 조건을 추가/수정하세요" in prompt and "통합인증" in prompt
        assert outs[1]["final_response"] == (
            f"{sampled_antecedent_note(ante, partial=True)}\n\n표 3행"
        )
        assert len(outs[1]["query_results"]) == 3

    async def test_over_cap_widened_and_carried(self, monkeypatch) -> None:
        outs = await _run_graph_path([
            (T1_QUERY, T1_SQL, _itam_rows(85), []),
            (T2_QUERY, T2_WIDE_SQL, _os_rows(30), []),
        ], monkeypatch)
        ante = outs[1]["conversation_context"][SAMPLED_ANTECEDENT_KEY]
        assert outs[1]["final_response"] == (
            f"{sampled_antecedent_note(ante, partial=False)}\n\n표 30행"
        )
        outs = await _run_graph_path([
            (T1_QUERY, T1_SQL, _itam_rows(85), []),
            (T2_QUERY, T2_CARRIED_SQL, _os_rows(85), []),
        ], monkeypatch)
        assert outs[1]["final_response"] == "표 85행"

    async def test_up_to_cap_is_unchanged(self, monkeypatch) -> None:
        outs = await _run_graph_path([
            (T1_QUERY, T1_SQL, _itam_rows(ENTITY_SAMPLE_ROWS), []),
            (T2_QUERY, T2_SAMPLE_SQL, _os_rows(3), []),
        ], monkeypatch)
        assert SAMPLED_TURN_KEY not in (outs[0].get("conversation_context") or {})
        assert SAMPLED_ANTECEDENT_KEY not in outs[1]["conversation_context"]
        assert outs[1]["final_response"] == "표 3행"

    async def test_zero_rows_keeps_plan146_path(self, monkeypatch) -> None:
        outs = await _run_graph_path([
            (T1_QUERY, T1_SQL, [], []),
            (T2_QUERY, T2_CARRIED_SQL, [], []),
        ], monkeypatch)
        ctx = outs[1]["conversation_context"]
        assert EMPTY_ANTECEDENT_KEY in ctx and SAMPLED_ANTECEDENT_KEY not in ctx
        assert outs[1]["final_response"].count(OLD_NOTE_HEAD) == 1
        assert NOTE_HEAD not in outs[1]["final_response"]

    async def test_named_server_many_rows_is_not_sample(self, monkeypatch) -> None:
        """앞 턴이 서버를 지목했으면(시계열 30행) 엔티티가 전체다 — 표본 승계 없음(종전)."""
        named = [{"field": "hostname", "op": "=", "value": "web09"}]
        rows = [{"hostname": "web09", "cpu": i} for i in range(30)]
        outs = await _run_graph_path([
            ("web09 CPU 사용률 추이", "SELECT hostname, cpu FROM m WHERE hostname = 'web09'",
             rows, named),
            ("그 서버 메모리도", "SELECT mem FROM m WHERE hostname IN ('web09', 'web08')",
             _os_rows(2), []),
        ], monkeypatch)
        ctx = outs[1]["conversation_context"]
        assert ctx["previous_entities_complete"] is True
        assert SAMPLED_ANTECEDENT_KEY not in ctx
        assert outs[1]["final_response"] == "표 2행"


# ── 폴스타 대칭(D-066) — 같은 모양이 2단·노드 경로에서 같은 안내 ───────────────────────


_PS_T1 = "김포 운영 서버 목록 보여줘"
_PS_T1_SQL = "SELECT hostname, ip FROM cmm_resource WHERE zone = 'gimpo' LIMIT 1000"
_PS_T2 = "그 서버들 메모리 사용률도"
_PS_T2_SAMPLE = "SELECT hostname, mem FROM m WHERE hostname IN ('web00', 'web01', 'web02')"
_PS_ROWS = [{"hostname": f"web{i:02d}", "ip": f"10.1.0.{i}"} for i in range(30)]


class TestPolestarSymmetry:
    async def test_same_notice_on_both_paths(self, monkeypatch) -> None:
        seen, t2_outs = await _run_tier2([
            (_PS_T1, _PS_T1_SQL, _PS_ROWS), (_PS_T2, _PS_T2_SAMPLE, _os_rows(3)),
        ])
        block = seen[1]["block"]
        assert "hostname=web00" not in block and "결과 30건 — 표본만 보존" in block
        g_outs = await _run_graph_path([
            (_PS_T1, _PS_T1_SQL, _PS_ROWS, []), (_PS_T2, _PS_T2_SAMPLE, _os_rows(3), []),
        ], monkeypatch)
        note = sampled_antecedent_note(
            {"query": _PS_T1, "row_count": 30}, partial=True,
        )
        assert t2_outs[1]["final_response"].startswith(note + "\n\n")
        assert g_outs[1]["final_response"] == f"{note}\n\n표 3행"


# ── 라우트(SSE) — 안내는 done.response 맨 앞 · 행은 메타·CSV에 그대로 ────────────────


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


def test_partial_rows_reach_stream_meta_and_csv_with_notice_first(_route_config) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.api.dependencies import require_user
    from src.api.routes import query as query_routes

    ante = _ante()
    rows = _os_rows(3)

    async def query_executor(state: AgentState) -> dict:
        return {
            "query_results": rows,
            "organized_data": {"summary": "", "rows": rows, "is_sufficient": True},
            "query_attempts": [{"sql": T2_SAMPLE_SQL, "success": True}],
            "generated_sql": T2_SAMPLE_SQL,
            "conversation_context": {"turn_count": 2, SAMPLED_ANTECEDENT_KEY: ante},
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
    r = client.post("/api/v1/query/stream", json={"query": T2_QUERY})
    assert r.status_code == 200, r.text
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    done = [e for e in events if e["type"] == "done"][0]
    assert done["response"].startswith(sampled_antecedent_note(ante, partial=True) + "\n\n")
    assert done["row_count"] == 3
    csv = client.get(f"/api/v1/query/{done['query_id']}/download-csv")
    assert csv.status_code == 200 and "auth02" in csv.text
