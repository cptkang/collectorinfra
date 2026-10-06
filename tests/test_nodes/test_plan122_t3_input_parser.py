"""plans/122 T-3 — input_parser 기간 슬롯 · 기준 시각 주입 · 해석 · 되묻기 (D-309).

가짜 LLM만 쓴다(실 LLM 호출 0). 기준 시각은 `input_parser._now`를 바꿔 고정한다.
설정은 검증 대상 필드(`query.time_resolution_enabled`)를 명시해 `.env`와 무관하게 한다.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END

import src.graph as graph_module
from src.domain.query_time import (
    SLOT_ABSENT,
    SLOT_ACCEPTED,
    SLOT_REJECTED,
    SLOT_UNUSED,
    QueryTime,
    clarify_message,
    resolve_query_time,
)
from src.domain.time_expr import SLOT_KEYS
from src.domain.time_spec import ANCHORS, COMPLETENESS, DISPLAY_GRAINS, KST, RELATIONS, UNITS
from src.prompts.input_parser import (
    INPUT_PARSER_CSV_CONTEXT_PROMPT,
    INPUT_PARSER_SYSTEM_PROMPT,
    INPUT_PARSER_TIME_SLOT_SECTION,
    time_anchor_line,
)
from src.state import create_initial_state

# `src.nodes` 패키지가 같은 이름의 함수를 재노출해 `import … as`는 함수를 잡는다 — 모듈을 직접 연다
ip = importlib.import_module("src.nodes.input_parser")

NOW = datetime(2026, 10, 6, 10, 0, tzinfo=KST)  # 화요일
SUFFIX = INPUT_PARSER_TIME_SLOT_SECTION + time_anchor_line(NOW.date())

SLOT_3_DAYS_AGO = {
    "relation": "ago", "n": 3, "unit": "day", "completeness": None, "anchor": None,
    "start": None, "end": None, "display_grain": "none", "span": "3일 전",
}


class _FakeLLM:
    """호출마다 준비된 JSON 응답을 차례로 내고, 받은 메시지를 기록한다."""

    def __init__(self, *payloads: dict[str, Any]) -> None:
        self._payloads = list(payloads)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any], config: Any = None) -> SimpleNamespace:
        self.calls.append(list(messages))
        payload = self._payloads[min(len(self.calls), len(self._payloads)) - 1]
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    def system_prompts(self) -> list[str]:
        return [m[0].content for m in self.calls]


def _cfg(on: bool) -> SimpleNamespace:
    return SimpleNamespace(query=SimpleNamespace(time_resolution_enabled=on))


def _payload(time_expr: Any = None, *, with_slot_key: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"query_targets": ["서버", "CPU"], "filter_conditions": []}
    if with_slot_key:
        out["time_expr"] = time_expr
    return out


@pytest.fixture(autouse=True)
def _fixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """기준 시각 고정 + 구조화 출력 경로 차단(`.env`의 백엔드 설정과 무관하게)."""
    monkeypatch.setattr(ip, "_now", lambda: NOW)

    async def _no_structured(llm: Any, messages: list[Any], *, config: Any = None) -> None:
        return None

    monkeypatch.setattr(ip, "_try_structured_requirements", _no_structured)


# ──────────────────────────────────────────────
# 프롬프트 계약
# ──────────────────────────────────────────────


class TestPromptContract:
    def test_slot_section_lists_every_key_and_enum(self) -> None:
        """프롬프트의 슬롯 키·enum이 검증기(`slot_to_spec`)와 정확히 같다(드리프트 차단)."""
        for key in SLOT_KEYS:
            assert f'"{key}"' in INPUT_PARSER_TIME_SLOT_SECTION, key
        for value in (*RELATIONS, *UNITS, *COMPLETENESS, *ANCHORS, *DISPLAY_GRAINS):
            assert re.search(rf"\b{value}\b", INPUT_PARSER_TIME_SLOT_SECTION), value
        for key in ("year", "year_offset", "month", "day", "hour"):
            assert f'"{key}"' in INPUT_PARSER_TIME_SLOT_SECTION, key

    def test_examples_pass_slot_validation(self) -> None:
        """프롬프트 예시 슬롯은 검증기를 통과한다 — 예시가 폐기 대상이면 LLM이 틀린 꼴을 배운다."""
        examples = re.findall(
            r'입력: "(.+?)"\ntime_expr: (null|\{.*?\})\n\n', INPUT_PARSER_TIME_SLOT_SECTION + "\n",
            re.S,
        )
        assert len(examples) == 5
        for text, slot_json in examples:
            slot = json.loads(slot_json)
            qt = resolve_query_time(text, NOW, slot=slot)
            assert qt.clarify is None, text
            assert qt.slot_status != SLOT_REJECTED, (text, qt.slot_reason)
            if slot is None:
                assert qt.slot_status == SLOT_ABSENT

    def test_anchor_line_is_date_only(self) -> None:
        line = time_anchor_line(NOW.date())
        assert line.startswith("\n## 기준 시각\n오늘은 2026-10-06(화) KST입니다.")
        assert "10:00" not in line
        assert "슬롯으로만" in line


# ──────────────────────────────────────────────
# ① off = 종전 바이트 · ② on = 맨 끝 접미
# ──────────────────────────────────────────────


class TestPromptOnOff:
    @pytest.mark.asyncio
    async def test_off_sends_legacy_messages_and_no_time_resolution(self) -> None:
        llm = _FakeLLM(_payload(with_slot_key=False))
        state = create_initial_state(user_query="지난달 CPU 사용률")
        out = await ip.input_parser(state, llm=llm, app_config=_cfg(False))

        (messages,) = llm.calls
        assert isinstance(messages[0], SystemMessage)
        assert messages[0].content == INPUT_PARSER_SYSTEM_PROMPT
        assert isinstance(messages[1], HumanMessage)
        assert messages[1].content == "지난달 CPU 사용률"
        assert len(messages) == 2
        assert "time_resolution" not in out
        assert "final_response" not in out
        assert "time_expr" not in out["parsed_requirements"]

    @pytest.mark.asyncio
    async def test_on_appends_slot_section_and_anchor_at_the_very_end(self) -> None:
        llm = _FakeLLM(_payload(None))
        state = create_initial_state(user_query="지난달 CPU 사용률")
        await ip.input_parser(state, llm=llm, app_config=_cfg(True))

        (system,) = llm.system_prompts()
        assert system == INPUT_PARSER_SYSTEM_PROMPT + SUFFIX
        assert system.endswith(time_anchor_line(NOW.date()))
        assert "오늘은 2026-10-06(화) KST입니다." in system

    @pytest.mark.asyncio
    async def test_multiturn_context_stays_before_suffix(self) -> None:
        """멀티턴 맥락 절 뒤에 붙는다 — on 프롬프트 = off 프롬프트 + 접미(접두 불변)."""
        ctx = {"turn_count": 2, "previous_sql": "SELECT 1", "previous_tables": ["t"]}
        off_llm, on_llm = _FakeLLM(_payload(with_slot_key=False)), _FakeLLM(_payload(None))
        await ip._parse_natural_language(off_llm, "그 중 상위 5대", conversation_context=ctx)
        await ip._parse_natural_language(
            on_llm, "그 중 상위 5대", conversation_context=ctx, anchor_at=NOW
        )
        (off_system,) = off_llm.system_prompts()
        (on_system,) = on_llm.system_prompts()
        assert off_system.startswith(INPUT_PARSER_SYSTEM_PROMPT + "\n\n## 이전 대화 맥락")
        assert "기간 슬롯" not in off_system
        assert on_system == off_system + SUFFIX


# ──────────────────────────────────────────────
# ③~⑤ 해석 · 슬롯 처리 · 로그
# ──────────────────────────────────────────────


def _qt(out: dict[str, Any]) -> QueryTime:
    qt = QueryTime.from_state(out["time_resolution"])
    assert qt is not None
    return qt


class TestResolution:
    @pytest.mark.asyncio
    async def test_rule_match_leaves_slot_unused(self) -> None:
        slot = {"relation": "last", "n": 1, "unit": "month", "span": "지난달"}
        llm = _FakeLLM(_payload(slot))
        out = await ip.input_parser(
            create_initial_state(user_query="지난달 CPU"), llm=llm, app_config=_cfg(True)
        )
        qt = _qt(out)
        assert qt.metric is not None and qt.metric.source == "rule"
        assert qt.slot_status == SLOT_UNUSED
        assert qt.anchor_at == NOW
        assert "final_response" not in out

    @pytest.mark.asyncio
    async def test_rule_miss_accepts_valid_slot(self) -> None:
        llm = _FakeLLM(_payload(SLOT_3_DAYS_AGO))
        out = await ip.input_parser(
            create_initial_state(user_query="3일 전 메모리 사용률"), llm=llm, app_config=_cfg(True)
        )
        qt = _qt(out)
        assert qt.slot_status == SLOT_ACCEPTED
        assert qt.clarify is None
        assert qt.metric is not None and qt.metric.source == "llm"
        assert qt.metric.start == datetime(2026, 10, 3, tzinfo=KST)
        assert qt.metric.end == datetime(2026, 10, 4, tzinfo=KST)
        assert out["parsed_requirements"]["time_expr"] == SLOT_3_DAYS_AGO
        assert "final_response" not in out

    @pytest.mark.asyncio
    async def test_hallucinated_span_is_rejected_with_logged_reason(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        slot = {"relation": "last", "n": 7, "unit": "day", "span": "최근 7일"}
        llm = _FakeLLM(_payload(slot))
        caplog.set_level(logging.INFO, logger=ip.logger.name)
        out = await ip.input_parser(
            create_initial_state(user_query="메모리 사용률 상위 5대"),
            llm=llm, app_config=_cfg(True),
        )
        qt = _qt(out)
        assert qt.slot_status == SLOT_REJECTED
        assert qt.slot_reason == "span_not_in_text"
        assert qt.metric is not None and qt.metric.source == "default"
        lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("[시간해석]")]
        assert len(lines) == 1
        assert "slot=rejected reason=span_not_in_text" in lines[0]
        assert "source=default" in lines[0]

    @pytest.mark.asyncio
    async def test_llm_failure_still_resolves_by_rule(self) -> None:
        class _Broken:
            async def ainvoke(self, messages: list[Any], config: Any = None) -> Any:
                raise RuntimeError("LLM 에러")

        out = await ip.input_parser(
            create_initial_state(user_query="어제 CPU 사용률"), llm=_Broken(), app_config=_cfg(True)
        )
        qt = _qt(out)
        assert qt.metric is not None and qt.metric.source == "rule"
        assert qt.slot_status == SLOT_ABSENT


# ──────────────────────────────────────────────
# ⑥ CSV 경로 대칭 · ⑦ 재사용 경로 대칭
# ──────────────────────────────────────────────


_SHEETS = {
    "서버": {"headers": ["호스트명", "CPU"], "example_rows": [], "csv_text": ""},
    "메모리": {"headers": ["호스트명", "메모리"], "example_rows": [], "csv_text": ""},
}


class TestPathSymmetry:
    @pytest.mark.asyncio
    async def test_csv_path_appends_suffix_per_sheet_and_merges_first_slot(self) -> None:
        llm = _FakeLLM(_payload(None), _payload(SLOT_3_DAYS_AGO))
        state = create_initial_state(user_query="3일 전 사용률 채워줘", csv_sheet_data=_SHEETS)
        out = await ip.input_parser(state, llm=llm, app_config=_cfg(True))

        systems = llm.system_prompts()
        assert len(systems) == 2
        for name, system in zip(_SHEETS, systems):
            csv_section = INPUT_PARSER_CSV_CONTEXT_PROMPT.format(
                sheet_name=name, csv_context=ip._format_single_sheet_csv(_SHEETS[name])
            )
            assert system == INPUT_PARSER_SYSTEM_PROMPT + csv_section + SUFFIX
        assert out["parsed_requirements"]["time_expr"] == SLOT_3_DAYS_AGO
        assert _qt(out).slot_status == SLOT_ACCEPTED

    @pytest.mark.asyncio
    async def test_csv_path_off_is_legacy(self) -> None:
        llm = _FakeLLM(_payload(with_slot_key=False))
        state = create_initial_state(user_query="3일 전 사용률 채워줘", csv_sheet_data=_SHEETS)
        out = await ip.input_parser(state, llm=llm, app_config=_cfg(False))
        for system in llm.system_prompts():
            assert "기간 슬롯" not in system and "기준 시각" not in system
        assert "time_expr" not in out["parsed_requirements"]
        assert "time_resolution" not in out

    def test_merge_without_slot_keys_keeps_legacy_shape(self) -> None:
        merged = ip._merge_sheet_parse_results([{"query_targets": ["a"]}, {"query_targets": ["b"]}])
        assert "time_expr" not in merged

    @pytest.mark.asyncio
    async def test_reuse_path_resolves_with_reused_slot(self) -> None:
        reused = {
            "query_targets": ["메모리"], "filter_conditions": [], "time_expr": SLOT_3_DAYS_AGO,
        }
        state = create_initial_state(user_query="3일 전 메모리 사용률")
        state["reuse_parsed_requirements"] = reused
        llm = _FakeLLM(_payload(None))
        out = await ip.input_parser(state, llm=llm, app_config=_cfg(True))
        assert llm.calls == []
        qt = _qt(out)
        assert qt.slot_status == SLOT_ACCEPTED and qt.anchor_at == NOW

    @pytest.mark.asyncio
    async def test_reuse_path_off_has_no_time_resolution(self) -> None:
        state = create_initial_state(user_query="3일 전 메모리 사용률")
        state["reuse_parsed_requirements"] = {"query_targets": ["메모리"], "filter_conditions": []}
        out = await ip.input_parser(state, llm=_FakeLLM(_payload(None)), app_config=_cfg(False))
        assert "time_resolution" not in out

    @pytest.mark.asyncio
    async def test_reuse_path_clarifies_too(self) -> None:
        state = create_initial_state(user_query="13월 CPU 사용률")
        state["reuse_parsed_requirements"] = {"query_targets": ["CPU"], "filter_conditions": []}
        out = await ip.input_parser(state, llm=_FakeLLM(_payload(None)), app_config=_cfg(True))
        assert out["final_response"] == clarify_message("invalid_date")


# ──────────────────────────────────────────────
# ⑧ 되묻기 · ⑨ off면 종전 간선
# ──────────────────────────────────────────────


class TestClarifyNode:
    @pytest.mark.asyncio
    async def test_invalid_month_asks_back_without_querying(self) -> None:
        out = await ip.input_parser(
            create_initial_state(user_query="13월 CPU 사용률"),
            llm=_FakeLLM(_payload(None)), app_config=_cfg(True),
        )
        assert _qt(out).clarify == "invalid_date"
        assert out["final_response"] == clarify_message("invalid_date")
        assert out["final_response"].startswith("[조회 기간] ")
        assert [m.content for m in out["messages"]] == [out["final_response"]]
        assert isinstance(out["messages"][0], AIMessage)

    @pytest.mark.asyncio
    async def test_unresolved_trace_without_slot_asks_back(self) -> None:
        """규칙이 기간 흔적만 보고 슬롯도 없으면 침묵 기본값 대신 되묻는다(§10.3 「해석 불가」)."""
        out = await ip.input_parser(
            create_initial_state(user_query="3일 전 메모리 사용률"),
            llm=_FakeLLM(_payload(None)), app_config=_cfg(True),
        )
        assert out["final_response"] == clarify_message("unresolved")

    @pytest.mark.parametrize(
        ("code", "phrase"),
        [
            ("invalid_date", "달력에 없는 날짜"),
            ("future_explicit", "아직 오지 않은 기간"),
            ("future_relative", "미래 기간(내년·다음 달 등)"),
            ("unresolved", "기간 표현을 해석하지 못해"),
            ("invalid_spec", "기간 표현을 해석하지 못해"),
        ],
    )
    def test_clarify_message_wording(self, code: str, phrase: str) -> None:
        msg = clarify_message(code)
        assert msg.startswith("[조회 기간] ")
        assert phrase in msg
        for example in ("「지난달」", "「2026년 9월」", "「최근 7일」", "「어제」"):
            assert example in msg


class TestRouteAfterInputParser:
    def test_clarify_ends(self) -> None:
        qt = resolve_query_time("13월 CPU", NOW)
        assert graph_module.route_after_input_parser({"time_resolution": qt.to_state()}) == END

    @pytest.mark.parametrize("value", [None, {}, {"version": 999, "clarify": "invalid_date"}])
    def test_none_or_unknown_goes_to_field_mapper(self, value: Any) -> None:
        assert graph_module.route_after_input_parser({"time_resolution": value}) == "field_mapper"

    def test_resolved_goes_to_field_mapper(self) -> None:
        qt = resolve_query_time("지난달 CPU", NOW)
        state = {"time_resolution": qt.to_state()}
        assert graph_module.route_after_input_parser(state) == "field_mapper"


_TIERS = ("deep_agent", "intent_orchestration", "semantic_router", "legacy")


class _ReachedFieldMapperError(Exception):
    pass


def _build(mock_config: Any, monkeypatch: pytest.MonkeyPatch, tier: str, llm: Any, *, on: bool):
    """사다리 단 하나로 컴파일한 그래프 — input_parser만 실제 노드(가짜 LLM).

    field_mapper는 도달하면 예외를 내는 표지다.
    """
    mock_config.query.time_resolution_enabled = on
    mock_config.enable_deepagents_package = tier == "deep_agent"
    mock_config.enable_intent_orchestration = tier == "intent_orchestration"
    mock_config.enable_semantic_routing = tier == "semantic_router"
    backend = "deep_agent" if tier == "deep_agent" else "semantic_router"
    monkeypatch.setattr(graph_module, "select_orchestration_backend", lambda c: backend)
    monkeypatch.setattr(graph_module, "_deep_agent_buildable", lambda c, w: True)
    monkeypatch.setattr(graph_module, "create_llm", lambda *a, **kw: llm)

    async def _context_resolver(state: Any, **_kw: Any) -> dict[str, Any]:
        return {}

    async def _field_mapper(state: Any, **_kw: Any) -> dict[str, Any]:
        raise _ReachedFieldMapperError

    monkeypatch.setattr(graph_module, "context_resolver", _context_resolver)
    monkeypatch.setattr(graph_module, "field_mapper", _field_mapper)
    return graph_module.build_graph(mock_config)


class TestClarifyGraph:
    @pytest.mark.parametrize("tier", _TIERS)
    def test_every_tier_has_the_same_input_parser_edges(
        self, mock_config: Any, monkeypatch: pytest.MonkeyPatch, tier: str
    ) -> None:
        compiled = _build(mock_config, monkeypatch, tier, _FakeLLM(_payload(None)), on=True)
        targets = {e.target for e in compiled.get_graph().edges if e.source == "input_parser"}
        assert targets == {"field_mapper", END}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tier", _TIERS)
    async def test_clarify_ends_graph_before_field_mapper(
        self, mock_config: Any, monkeypatch: pytest.MonkeyPatch, tier: str
    ) -> None:
        compiled = _build(mock_config, monkeypatch, tier, _FakeLLM(_payload(None)), on=True)
        result = await compiled.ainvoke(
            create_initial_state(user_query="13월 CPU 사용률", thread_id=f"t3-{tier}"),
            {"configurable": {"thread_id": f"t3-{tier}"}},
        )
        assert result["final_response"] == clarify_message("invalid_date")
        assert result["current_node"] == "input_parser"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tier", _TIERS)
    async def test_off_goes_to_field_mapper_even_for_invalid_month(
        self, mock_config: Any, monkeypatch: pytest.MonkeyPatch, tier: str
    ) -> None:
        compiled = _build(
            mock_config, monkeypatch, tier, _FakeLLM(_payload(with_slot_key=False)), on=False
        )
        with pytest.raises(_ReachedFieldMapperError):
            await compiled.ainvoke(
                create_initial_state(user_query="13월 CPU 사용률", thread_id=f"t3-off-{tier}"),
                {"configurable": {"thread_id": f"t3-off-{tier}"}},
            )

    @pytest.mark.asyncio
    async def test_on_resolved_query_goes_to_field_mapper(
        self, mock_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compiled = _build(mock_config, monkeypatch, "legacy", _FakeLLM(_payload(None)), on=True)
        with pytest.raises(_ReachedFieldMapperError):
            await compiled.ainvoke(
                create_initial_state(user_query="지난달 CPU 사용률", thread_id="t3-on-ok"),
                {"configurable": {"thread_id": "t3-on-ok"}},
            )

    @pytest.mark.asyncio
    async def test_stream_closes_on_root_input_parser_event(
        self, mock_config: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """스트림 라우트 계약 — `final_response`를 담은 루트 직속 노드 종료 이벤트에서 닫는다.

        라우트(`src/api/routes/query.py`)는 `parent_ids`가 1개 이하인 `on_chain_end` 출력에
        `final_response` 키가 있으면 그것을 응답으로 낸다. 되묻기 턴은 input_parser 이벤트가
        그것이고, 정상 턴의 input_parser 출력에는 키가 없어야 한다(있으면 빈 답으로 닫힌다).
        """
        async def _events(query: str, thread: str) -> list[dict[str, Any]]:
            compiled = _build(mock_config, monkeypatch, "legacy", _FakeLLM(_payload(None)), on=True)
            seen: list[dict[str, Any]] = []
            try:
                async for ev in compiled.astream_events(
                    create_initial_state(user_query=query, thread_id=thread),
                    {"configurable": {"thread_id": thread}}, version="v2",
                ):
                    if ev["event"] == "on_chain_end" and ev.get("name") == "input_parser":
                        seen.append(ev)
            except _ReachedFieldMapperError:
                pass
            return seen

        (clarify,) = await _events("13월 CPU 사용률", "t3-stream-clarify")
        assert len(clarify.get("parent_ids") or ()) == 1
        assert clarify["data"]["output"]["final_response"] == clarify_message("invalid_date")

        (normal,) = await _events("지난달 CPU 사용률", "t3-stream-ok")
        assert "final_response" not in normal["data"]["output"]
        assert "time_resolution" in normal["data"]["output"]
