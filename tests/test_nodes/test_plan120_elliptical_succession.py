"""생략형 후속 턴 직전 서버 승계 (plans/120 PL-1 · G-4 (a)).

run 20260923-140539 G-01: 1턴 「cocm-hdkapp01 서버의 OS 확인」
→ 2턴 「해당 서버의 최근 1개월 CPU …」 → 3턴 「네 그럼 메모리도 보여줘」가 `prev_entities=1`인데
지시어가 없어 승계되지 않고 전 서버 4,782행을 냈다. 공통 전단(`context_resolver → input_parser`)
에서 좁은 조건으로 직전 서버를 잇고, 응답에 고지를 싣는지(침묵 승계 금지) 2단·3단 두 경로에서
본다. 실 LLM 0 · DB 0.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from src.nodes.context_resolver import context_resolver
from src.nodes.input_parser import input_parser
from src.nodes.output_generator import output_generator
from src.state import create_initial_state
from src.utils.query_gen_common import (
    ELLIPTICAL_SUCCESSION_KEY,
    elliptical_succession_filter,
    render_elliptical_succession_note,
)

_HOST = "cocm-hdkapp01"
_T3 = "네 그럼 메모리도 보여줘"
_NOTE_HEAD = "**[직전 서버 기준]**"


def _ctx(entities: list[dict] | None = None, *, complete: bool = True) -> dict[str, Any]:
    return {
        "turn_count": 3,
        "previous_result_count": 1,
        "previous_entities": (
            entities if entities is not None else [{"field": "server_name", "value": _HOST}]
        ),
        "previous_entities_complete": complete,
    }


def _parsed(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "query_targets": ["서버", "메모리"],
        "filter_conditions": [],
        "target_db_hints": [],
        "output_format": "text",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 판정 함수 — 양성
# ---------------------------------------------------------------------------


class TestPositive:
    def test_g01_t3_inherits_server_name_as_name_field(self):
        """서버명만 해소되면 name 필드(D-148 · D-046) — 지시어 경로와 같은 규칙."""
        assert elliptical_succession_filter(_T3, _parsed(), _ctx()) == {
            "field": "name", "op": "=", "value": _HOST,
        }

    def test_hostname_entity_uses_hostname_field(self):
        ctx = _ctx([{"field": "hostname", "value": "hdkapp01"}])
        assert elliptical_succession_filter(_T3, _parsed(), ctx)["field"] == "hostname"

    def test_hostname_and_name_pair_is_one_server(self):
        """hostname·서버명 한 쌍은 같은 서버의 두 표기 — hostname을 쓴다."""
        ctx = _ctx([
            {"field": "server_name", "value": _HOST},
            {"field": "hostname", "value": "hdkapp01"},
            {"field": "ip_address", "value": "10.0.0.1"},
        ])
        assert elliptical_succession_filter(_T3, _parsed(), ctx) == {
            "field": "hostname", "op": "=", "value": "hdkapp01",
        }

    @pytest.mark.parametrize("query", [
        "메모리도 보여줘",          # 첨가 조사만
        "그럼 메모리 보여줘",       # 담화 표지만
        "그러면 디스크 사용률은?",
        "CPU 온도도 보여줘",        # '온도' + 첨가 조사 '도'
        "그럼, 알람은?",
    ])
    def test_additive_markers(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is not None


# ---------------------------------------------------------------------------
# 판정 함수 — 음성(오탐 방지)
# ---------------------------------------------------------------------------


class TestNegative:
    def test_multiple_previous_entities(self):
        ctx = _ctx([{"field": "server_name", "value": "a"}, {"field": "server_name", "value": "b"}])
        assert elliptical_succession_filter(_T3, _parsed(), ctx) is None

    def test_sample_or_sticky_entities(self):
        """표본(행 상한 초과)·sticky 승계분이면 context_resolver가 complete=False로 둔다."""
        assert elliptical_succession_filter(_T3, _parsed(), _ctx(complete=False)) is None
        ctx = _ctx()
        ctx.pop("previous_entities_complete")   # 옛 체크포인트 — 키 없음
        assert elliptical_succession_filter(_T3, _parsed(), ctx) is None

    def test_no_previous_entities(self):
        assert elliptical_succession_filter(_T3, _parsed(), _ctx([])) is None
        assert elliptical_succession_filter(_T3, _parsed(), None) is None

    def test_demonstrative_previous_value_is_not_an_entity(self):
        ctx = _ctx([{"field": "hostname", "value": "해당 서버"}])
        assert elliptical_succession_filter(_T3, _parsed(), ctx) is None

    def test_new_host_filter(self):
        parsed = _parsed(filter_conditions=[{"field": "hostname", "op": "=", "value": "webdb01"}])
        assert elliptical_succession_filter("그럼 webdb01도 보여줘", parsed, _ctx()) is None

    def test_new_ip_filter(self):
        parsed = _parsed(filter_conditions=[{"field": "ip", "op": "=", "value": "10.1.2.3"}])
        assert elliptical_succession_filter("그럼 메모리도 보여줘", parsed, _ctx()) is None

    @pytest.mark.parametrize("query", [
        "그럼 cocm-abc02도 보여줘",   # 파서가 필터로 못 뽑은 새 호스트명
        "그럼 db01도 보여줘",
        "그럼 10.1.2.3도 보여줘",
    ])
    def test_new_identifier_surface_in_text(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    @pytest.mark.parametrize("query", ["그럼 김포 메모리도 보여줘", "그럼 은행존도 보여줘"])
    def test_location_word(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    def test_location_hint_from_parser(self):
        parsed = _parsed(target_db_hints=["여의도"])
        assert elliptical_succession_filter(_T3, parsed, _ctx()) is None

    @pytest.mark.parametrize("query", [
        "그럼 전체 서버 메모리도 보여줘",
        "그럼 모든 서버 메모리도",
        "메모리도 모두 보여줘",
    ])
    def test_global_scope_terms(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    @pytest.mark.parametrize("query", ["메모리 보여줘", "디스크 사용률 알려줘"])
    def test_no_additive_marker(self, query):
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    @pytest.mark.parametrize("query", [
        "온도 보여줘", "응답 속도 알려줘", "사용률이 어느 정도야", "알람 빈도 보여줘",
        "용도 알려줘", "한도 보여줘", "심각도 높은 알람", "30도 넘는 장비",
        "보여줘도 돼?", "그래도 메모리 보여줘", "아무도 안 쓰는 서버",
    ])
    def test_nouns_and_endings_ending_in_do(self, query):
        """'도'로 끝나지만 첨가 조사가 아닌 낱말은 표지가 아니다."""
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    def test_demonstrative_turn_left_to_existing_path(self):
        """지시어 턴은 종전 경로(2단 주입 · 노드 지시어 스코프)가 잇는다 — 중복 주입 금지."""
        query = "그럼 그 서버 메모리도 보여줘"
        assert elliptical_succession_filter(query, _parsed(), _ctx()) is None

    def test_no_query_targets(self):
        parsed = _parsed(query_targets=[])
        assert elliptical_succession_filter("네 그럼 고마워요", parsed, _ctx()) is None


# ---------------------------------------------------------------------------
# ⓕ·ⓖ — 서버 집합을 스스로 지목·선택하는 턴은 생략형이 아니다(코드 리뷰 탐침 14건)
# ---------------------------------------------------------------------------

_DISK_OVER = [{"field": "disk_usage", "op": ">", "value": 80}]
_CPU_OVER = [{"field": "cpu_usage", "op": ">", "value": 80}]
_WINDOWS = [{"field": "os", "op": "=", "value": "Windows"}]
_MEM_OVER = [{"field": "mem_usage", "op": ">", "value": 90}]


class TestSetSelectionNotElliptical:
    @pytest.mark.parametrize(("query", "over"), [
        ("디스크 사용률도 높은 서버 알려줘", {"filter_conditions": _DISK_OVER}),
        ("그럼 메모리 사용률 상위 5개 서버는?", {"limit": 5}),
        ("그러면 CPU 80% 넘는 서버도 알려줘", {"filter_conditions": _CPU_OVER}),
        ("그럼 다른 서버도 보여줘", {}),
        ("나머지 서버도 보여줘", {}),
        ("WAS 서버도 보여줘", {}),
        ("윈도우 서버도 보여줘", {"filter_conditions": _WINDOWS}),
        ("그럼 서버 목록도 보여줘", {}),
        ("그럼 서버 수도 알려줘", {"aggregation": "count"}),
        ("그럼 알람 발생한 서버도 알려줘", {}),
        ("오늘도 CPU 높은 서버 있어?", {}),
        ("그럼 서버별 메모리도 보여줘", {}),
        ("그럼 상위 10건도 보여줘", {"limit": 10}),
        ("그럼 이상 서버도 알려줘", {}),
    ])
    def test_review_probe_cases(self, query, over):
        assert elliptical_succession_filter(query, _parsed(**over), _ctx()) is None

    @pytest.mark.parametrize(("query", "over"), [
        # 서버 명사 없이 선택·집계 신호만 있는 턴(ⓖ 단독)
        ("그럼 상위 5개도 보여줘", {}),
        ("그럼 메모리 top5도", {}),
        ("그럼 메모리 목록도 보여줘", {}),
        ("나머지도 보여줘", {}),
        ("그럼 존별 메모리도", {}),
        ("각각 메모리도 보여줘", {}),
        ("그럼 메모리도 보여줘", {"limit": 5}),
        ("그럼 메모리도 보여줘", {"aggregation": "top_n"}),
        ("그럼 메모리도 보여줘", {"aggregation": "group_by"}),
        ("그럼 메모리도 보여줘", {"filter_conditions": _MEM_OVER}),
    ])
    def test_selection_signals_without_server_noun(self, query, over):
        assert elliptical_succession_filter(query, _parsed(**over), _ctx()) is None

    @pytest.mark.parametrize(("query", "over"), [
        ("그럼 알람도 보여줘", {}),
        ("그럼 디스크도", {}),
        ("그럼 메모리 추이도 보여줘", {"aggregation": "time_series"}),   # 한 서버의 흐름
        ("그럼 메모리 일별도 보여줘", {}),                                # 시간 구간 '~별'
    ])
    def test_target_noun_omitted_still_inherits(self, query, over):
        assert elliptical_succession_filter(query, _parsed(**over), _ctx()) == {
            "field": "name", "op": "=", "value": _HOST,
        }


@pytest.mark.parametrize("entities", [
    [{"field": "server_name", "value": "a"}],
    [{"field": "hostname", "value": "h"}],
    [{"field": "name", "value": "a"}, {"field": "hostname", "value": "h"}],
    [{"field": "name", "value": "a"}, {"field": "name", "value": "b"}],
    [{"field": "hostname", "value": "h1"}, {"field": "host_name", "value": "h2"}],
    [{"field": "서버명", "value": "a"}, {"field": "resource_id", "value": 7}],
    [{"field": "hostname", "value": "그 서버"}],
])
def test_entity_resolution_matches_demonstrative_path(entities):
    """ⓐ의 해소는 지시어 경로와 같다.

    `resolve_targets` ③ → `_inject_demonstrative_hostname`의 필드 선택(hostname 우선, 없으면 name).
    """
    from src.utils.prior_targets import resolve_targets

    res = resolve_targets(previous_entities=entities)
    got = elliptical_succession_filter(_T3, _parsed(), _ctx(entities))
    if len(res.targets) != 1:
        assert got is None
        return
    target = res.targets[0]
    expected = ("hostname", target.hostname) if target.hostname else ("name", target.server_name)
    assert got is not None and (got["field"], got["value"]) == expected


def test_note_text():
    note = render_elliptical_succession_note({"field": "name", "value": _HOST})
    assert note == (
        f"**[직전 서버 기준]** 직전 서버 `{_HOST}` 기준으로 조회했습니다 — "
        "전체 서버는 '전체 서버 …'로 다시 요청하세요."
    )
    assert render_elliptical_succession_note(None) == ""


# ---------------------------------------------------------------------------
# context_resolver — 엔티티 완결성(ⓐ)
# ---------------------------------------------------------------------------


def _prev_turn_state(
    *,
    rows: list[dict],
    parsed: dict | None = None,
    prior_ctx: dict | None = None,
    query: str = _T3,
) -> dict:
    """직전 턴이 끝난 뒤 이번 턴(3턴째) 입력이 병합된 상태 — context_resolver 입력."""
    state = create_initial_state(user_query=query, thread_id="th-g01")
    state["messages"] = [
        HumanMessage(content="t1"), AIMessage(content="a1"),
        HumanMessage(content="t2"), AIMessage(content="a2"),
        HumanMessage(content=query),
    ]
    state["query_results"] = rows
    state["parsed_requirements"] = parsed or {"filter_conditions": []}
    state["conversation_context"] = prior_ctx
    return state


class TestContextResolverCompleteness:
    @pytest.mark.asyncio
    async def test_single_row_turn_is_complete(self):
        rows = [{"server_name": _HOST, "cpu_avg": 3.1}]
        out = await context_resolver(_prev_turn_state(rows=rows))
        ctx = out["conversation_context"]
        assert ctx["previous_entities"] == [{"field": "server_name", "value": _HOST}]
        assert ctx["previous_entities_complete"] is True

    @pytest.mark.asyncio
    async def test_rows_over_cap_without_filter_is_sample(self):
        rows = [{"server_name": _HOST, "day": d} for d in range(25)]
        out = await context_resolver(_prev_turn_state(rows=rows))
        assert out["conversation_context"]["previous_entities_complete"] is False

    @pytest.mark.asyncio
    async def test_rows_over_cap_with_host_filter_is_complete(self):
        rows = [{"server_name": _HOST, "day": d} for d in range(25)]
        parsed = {"filter_conditions": [{"field": "name", "op": "=", "value": _HOST}]}
        out = await context_resolver(_prev_turn_state(rows=rows, parsed=parsed))
        assert out["conversation_context"]["previous_entities_complete"] is True

    @pytest.mark.asyncio
    async def test_sticky_entities_are_not_complete(self):
        """직전 턴이 식별자 없는 집계(전체 서버 수)면 엔티티는 그 전 턴의 sticky 승계분이다."""
        prior_ctx = {"previous_entities": [{"field": "server_name", "value": _HOST}]}
        out = await context_resolver(
            _prev_turn_state(rows=[{"server_count": 1690}], prior_ctx=prior_ctx)
        )
        ctx = out["conversation_context"]
        assert ctx["previous_entities"] == [{"field": "server_name", "value": _HOST}]
        assert ctx["previous_entities_complete"] is False


# ---------------------------------------------------------------------------
# 공통 전단 → 2단·3단 (G-01 3턴 모의)
# ---------------------------------------------------------------------------


def _parser_llm(content: str) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = content
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _parser_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.structured_output_backend = "none"
    return cfg


def _streaming_llm(text: str) -> AsyncMock:
    llm = AsyncMock()

    async def _astream(messages, config=None):
        yield AIMessageChunk(content=text)

    llm.astream = _astream
    return llm


_T3_PARSE = (
    '{"query_targets": ["서버", "메모리"], "filter_conditions": [], "output_format": "text"}'
)
# G-01 2턴 결과 — 2단 top-level query_results(서버명 1열 · 1행)
_T2_ROWS = [{"server_name": _HOST, "cpu_avg": 3.12, "cpu_max": 41.5}]


async def _front_end(rows: list[dict], *, query: str = _T3, prior_ctx: dict | None = None) -> dict:
    """context_resolver → input_parser 를 통과한 이번 턴 상태(두 단 공통 전단)."""
    state = _prev_turn_state(rows=rows, query=query, prior_ctx=prior_ctx)
    state.update(await context_resolver(state))
    state.update(await input_parser(state, llm=_parser_llm(_T3_PARSE), app_config=_parser_cfg()))
    return state


def _organized(state: dict) -> dict:
    rows = [{"server_name": _HOST, "mem_avg": 55.0}]
    state["organized_data"] = {
        "summary": "1건", "rows": rows, "column_mapping": None, "is_sufficient": True,
    }
    state["query_results"] = rows
    return state


class TestG01ThreeTurns:
    @pytest.mark.asyncio
    async def test_front_end_injects_filter_and_marker(self):
        state = await _front_end(_T2_ROWS)
        parsed = state["parsed_requirements"]
        assert parsed["filter_conditions"] == [{"field": "name", "op": "=", "value": _HOST}]
        assert parsed[ELLIPTICAL_SUCCESSION_KEY] == {"field": "name", "value": _HOST}

    @pytest.mark.asyncio
    async def test_tier3_node_path_notice(self):
        """3단 — 노드가 top-level parsed_requirements로 SQL을 만들고 output_generator가 고지한다."""
        from src.nodes.prompt_blocks import demonstrative_entity_scope
        from src.utils.query_gen_common import has_host_identifier_filter

        state = _organized(await _front_end(_T2_ROWS))
        assert has_host_identifier_filter(state["parsed_requirements"])
        assert demonstrative_entity_scope(state) is None   # 지시어 스코프와 중복 없음
        out = await output_generator(state, llm=_streaming_llm("요약"), app_config=MagicMock())
        assert out["final_response"].count(_NOTE_HEAD) == 1
        assert f"`{_HOST}`" in out["final_response"]

    @pytest.mark.asyncio
    async def test_tier2_subagent_path_notice(self):
        """2단 — 격리 입력 → 지시어 주입(중복 없음) → `_build_output_state` → 고지."""
        from src.orchestration.result_aggregator import _build_output_state
        from src.orchestration.subagents import _inject_demonstrative_hostname, _make_isolated_input

        state = await _front_end(_T2_ROWS)
        task = {
            "task_id": "t1", "agent": "data_query", "sub_query": "메모리 사용률 조회", "order": 1,
        }
        isolated = _make_isolated_input(task, state, {})
        parsed_for_gen = _inject_demonstrative_hostname(isolated)
        assert parsed_for_gen["filter_conditions"] == [{"field": "name", "op": "=", "value": _HOST}]

        rows = [{"server_name": _HOST, "mem_avg": 55.0}]
        res = {
            "organized_data": {
                "summary": "1건", "rows": rows, "column_mapping": None, "is_sufficient": True,
            },
            "query_results": rows,
            "target_db_ids": ["polestar_cm_gp"],
        }
        out_state = _build_output_state(state, task, res)
        out = await output_generator(out_state, llm=_streaming_llm("요약"), app_config=MagicMock())
        assert out["final_response"].count(_NOTE_HEAD) == 1

    @pytest.mark.asyncio
    async def test_bulk_previous_turn_does_not_inherit(self):
        """직전 턴이 대량 조회면(엔티티 여럿) 승계하지 않는다 — 2026-08-04 축소 오답 방지."""
        rows = [{"server_name": f"srv{i:03d}"} for i in range(30)]
        state = await _front_end(rows)
        parsed = state["parsed_requirements"]
        assert parsed["filter_conditions"] == []
        assert ELLIPTICAL_SUCCESSION_KEY not in parsed

    @pytest.mark.asyncio
    async def test_sticky_entity_after_global_turn_does_not_inherit(self):
        prior_ctx = {"previous_entities": [{"field": "server_name", "value": _HOST}]}
        state = await _front_end([{"server_count": 1690}], prior_ctx=prior_ctx)
        assert ELLIPTICAL_SUCCESSION_KEY not in state["parsed_requirements"]

    @pytest.mark.asyncio
    async def test_no_notice_without_succession(self):
        state = _organized(create_initial_state(user_query="메모리 보여줘"))
        state["parsed_requirements"] = _parsed()
        out = await output_generator(state, llm=_streaming_llm("요약"), app_config=MagicMock())
        assert _NOTE_HEAD not in out["final_response"]

    @pytest.mark.asyncio
    async def test_form_upload_turn_is_excluded(self):
        state = _prev_turn_state(rows=_T2_ROWS, query="그럼 메모리 양식도 채워줘")
        state.update(await context_resolver(state))
        state["uploaded_file"] = b"PK\x03\x04dummy"
        state["file_type"] = "xlsx"
        out = await input_parser(state, llm=_parser_llm(_T3_PARSE), app_config=_parser_cfg())
        assert ELLIPTICAL_SUCCESSION_KEY not in out["parsed_requirements"]


class TestZoneAnswerReuseIdempotent:
    """plans/119 Q-2 재사용 턴 — 같은 규칙으로 한 번만 적용된다."""

    @pytest.mark.asyncio
    async def test_reuse_turn_applies_rule_once(self):
        state = _prev_turn_state(rows=_T2_ROWS)
        state.update(await context_resolver(state))
        state["reuse_parsed_requirements"] = _parsed()
        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=AssertionError("LLM 호출 금지"))
        out = await input_parser(state, llm=llm, app_config=MagicMock())
        parsed = out["parsed_requirements"]
        assert parsed["filter_conditions"] == [{"field": "name", "op": "=", "value": _HOST}]
        assert parsed[ELLIPTICAL_SUCCESSION_KEY] == {"field": "name", "value": _HOST}

        # 이미 승계된 파싱본을 다시 쓰는 턴 — 필터가 있어 재적용되지 않는다(중복 0)
        state["reuse_parsed_requirements"] = parsed
        again = (await input_parser(state, llm=llm, app_config=MagicMock()))["parsed_requirements"]
        assert again["filter_conditions"] == parsed["filter_conditions"]
        assert again[ELLIPTICAL_SUCCESSION_KEY] == parsed[ELLIPTICAL_SUCCESSION_KEY]
